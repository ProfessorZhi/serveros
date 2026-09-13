import tempfile
import unittest
from pathlib import Path

from workengine.activity import ActivityGraph, GraphError
from workengine.artifacts import ArtifactBoundaryError, artifact_dir
from workengine.engine import ServerOS
from workengine.model import ContextMode, ExecutionContext, RunState
from workengine.store import ServerOSStore


class ServerOSExecutionTest(unittest.TestCase):
    def test_activity_graph_rejects_cycles_and_reports_ready_nodes(self):
        with self.assertRaises(GraphError):
            ActivityGraph({"a": ["b"], "b": ["a"]})
        graph = ActivityGraph({"resolve": [], "test": ["resolve"], "report": ["test"]})
        self.assertEqual(graph.ready(set()), ["resolve"])
        self.assertEqual(graph.ready({"resolve"}), ["test"])

    def test_artifact_path_is_exact_and_rejects_escape(self):
        root = Path("/opt/applications/data/home-hwz/artifacts")
        self.assertEqual(
            artifact_dir(root, "litellm", "LIT-1042", "RUN-0001"),
            root / "work-items/litellm/LIT-1042/RUN-0001",
        )
        with self.assertRaises(ArtifactBoundaryError):
            artifact_dir(root, "../escape", "LIT-1042", "RUN-0001")
        with self.assertRaises(ValueError):
            artifact_dir(Path("relative"), "litellm", "LIT-1042", "RUN-0001")

    def test_one_item_can_have_two_repo_contexts_and_idempotent_run(self):
        with tempfile.TemporaryDirectory() as td:
            store = ServerOSStore(td)
            osctl = ServerOS(store=store, artifacts_root=Path(td) / "artifacts")
            osctl.register_project("serveros", "SRV")
            item = osctl.create_item("serveros", "multi-repo change", "ops")
            osctl.add_activity(item.id, "implement", "develop")
            osctl.attach_context(item.id, "implement", "hwz-ops", ContextMode.DEVELOP, revision="abc123")
            osctl.attach_context(item.id, "implement", "work-engine", ContextMode.DEVELOP, revision="def456")
            first = osctl.begin_run(item.id, "implement", "worker-a", "same-key")
            second = osctl.begin_run(item.id, "implement", "worker-a", "same-key")
            self.assertEqual(first.id, second.id)
            self.assertEqual(first.state, RunState.ALLOCATED)
            self.assertEqual(len(store.execution_contexts(item.id, "implement")), 2)
            self.assertTrue(Path(first.artifact_dir).is_absolute())

    def test_attaching_same_repo_is_idempotent_and_updates_its_context(self):
        with tempfile.TemporaryDirectory() as td:
            store = ServerOSStore(td)
            osctl = ServerOS(store=store, artifacts_root=Path(td) / "artifacts")
            osctl.register_project("serveros", "SRV")
            item = osctl.create_item("serveros", "refresh context", "ops")
            osctl.add_activity(item.id, "implement", "develop")
            first_worktree = str(Path(td) / "first")
            final_worktree = str(Path(td) / "final")

            store.attach_context(ExecutionContext(
                item.id, "implement", "work-engine", ContextMode.DEVELOP,
                "old", first_worktree,
            ))
            store.attach_context(ExecutionContext(
                item.id, "implement", "work-engine", ContextMode.DEVELOP,
                "old", first_worktree,
            ))
            store.attach_context(ExecutionContext(
                item.id, "implement", "work-engine", ContextMode.TEST,
                "new", final_worktree, True,
            ))
            store.attach_context(ExecutionContext(
                item.id, "implement", "hwz-ops", ContextMode.INSPECT,
                "ops", str(Path(td) / "ops"),
            ))

            contexts = store.execution_contexts(item.id, "implement")
            self.assertEqual(len(contexts), 2)
            by_repo = {context.repo: context for context in contexts}
            self.assertEqual(by_repo["work-engine"].mode, ContextMode.TEST)
            self.assertEqual(by_repo["work-engine"].revision, "new")
            self.assertEqual(by_repo["work-engine"].worktree, final_worktree)
            self.assertTrue(by_repo["work-engine"].dirty)
            self.assertEqual(by_repo["hwz-ops"].revision, "ops")

    def test_run_lifecycle_is_linear_and_resumable(self):
        with tempfile.TemporaryDirectory() as td:
            osctl = ServerOS(store=ServerOSStore(td), artifacts_root=Path(td) / "artifacts")
            osctl.register_project("serveros", "SRV")
            item = osctl.create_item("serveros", "run test", "test")
            osctl.add_activity(item.id, "test", "test")
            run = osctl.begin_run(item.id, "test", "worker-a", "key")
            for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
                run = osctl.advance_run(item.id, run.id, state)
            # SUCCEEDED is a verdict rather than a step, so it comes from
            # finalize_run — which is also what makes the state resumable from
            # an evidence record rather than from a bare transition.
            osctl.store.finalize_run(
                item.id, run.id, verdict="PASS", exit_code=0, command="pytest",
                cases=["lifecycle"], revision="abc", dirty=False,
            )
            resumed = ServerOS(store=ServerOSStore(td), artifacts_root=Path(td) / "artifacts").resume_run(item.id, run.id)
            self.assertEqual(resumed.state, RunState.SUCCEEDED)
            with self.assertRaises(ValueError):
                osctl.advance_run(item.id, run.id, RunState.RUNNING)


if __name__ == "__main__":
    unittest.main()
