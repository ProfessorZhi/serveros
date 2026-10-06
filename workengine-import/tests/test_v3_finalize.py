import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from contextlib import redirect_stderr
from io import StringIO
import json

from workengine.model import Activity, ActivityState, ContextMode, RunState
from workengine.store import EvidenceConflict, ServerOSStore
from workengine.cli import build_parser, main


class FinalizeV3Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.s = ServerOSStore(Path(self.td.name) / "state")
        self.s.register_project("demo", "DEM"); self.item = self.s.create_item(__import__('workengine').WorkItem("DEM-0001", "demo", "x", "test", "/card"))
        self.s.add_activity(Activity(self.item.id, "test", "test")); self.s.attach_context(__import__('workengine').ExecutionContext(self.item.id, "test", "repo", ContextMode.TEST, "base"))
        self.run = self.s.begin_run(self.item.id, "test", "worker", "key", Path(self.td.name) / "artifacts")
        for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING): self.run = self.s.advance_run(self.item.id, self.run.id, state)

    def tearDown(self): self.td.cleanup()

    def _evidence_file(self, name="evidence.txt"):
        path = Path(self.td.name) / name
        path.write_text("evidence")
        return path

    def _record_evidence(self, locator, **overrides):
        metadata = {
            "kind": "report",
            "locator": str(locator),
            "command": "python -m unittest",
            "cases": ["test_case"],
            "exit_code": 0,
            "revision": "revision-1",
            "dirty": False,
            "verdict": "PASS",
        }
        metadata.update(overrides)
        return self.s.record_evidence(self.item.id, self.run.id, **metadata)

    def test_finalize_preserves_context_and_is_idempotent(self):
        result = self.s.finalize_run(self.item.id, self.run.id, verdict="PASS", exit_code=0, command="pytest", cases=["one"], revision="final", dirty=False)
        self.assertEqual(result["state"], "SUCCEEDED"); self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(self.s.execution_contexts(self.item.id, "test")[0].revision, "base")
        self.assertEqual(self.s.finalize_run(self.item.id, self.run.id, verdict="PASS", exit_code=0, command="pytest", cases=["one"], revision="final", dirty=False), result)

    def test_finalize_preserves_distinct_repo_contexts_and_records_aggregate_revision(self):
        self.s.attach_context(__import__('workengine').ExecutionContext(
            self.item.id, "test", "repo", ContextMode.TEST, "repo-sha", "/tmp/repo", True,
        ))
        self.s.attach_context(__import__('workengine').ExecutionContext(
            self.item.id, "test", "ops", ContextMode.INSPECT, "ops-sha", "/tmp/ops", False,
        ))

        result = self.s.finalize_run(
            self.item.id, self.run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["integration"],
            revision="repo=repo-sha,ops=ops-sha", dirty=True,
        )
        contexts = {
            context.repo: (context.revision, context.dirty)
            for context in self.s.execution_contexts(self.item.id, "test")
        }
        self.assertEqual(contexts, {"ops": ("ops-sha", False), "repo": ("repo-sha", True)})
        with self.s.connection() as conn:
            evidence = conn.execute(
                "SELECT revision,dirty FROM evidence WHERE item_id=? AND run_id=?",
                (self.item.id, self.run.id),
            ).fetchone()
        self.assertEqual((evidence["revision"], evidence["dirty"]), ("repo=repo-sha,ops=ops-sha", 1))
        self.assertEqual(
            self.s.finalize_run(
                self.item.id, self.run.id, verdict="PASS", exit_code=0,
                command="pytest", cases=["integration"],
                revision="repo=repo-sha,ops=ops-sha", dirty=True,
            ),
            result,
        )
        self.assertEqual(
            {
                context.repo: (context.revision, context.dirty)
                for context in self.s.execution_contexts(self.item.id, "test")
            },
            contexts,
        )

    def test_activity_transition_validates(self):
        self.s.transition_activity(self.item.id, "test", ActivityState.READY)
        self.assertEqual(self.s.transition_activity(self.item.id, "test", ActivityState.RUNNING).state, ActivityState.RUNNING)

    def test_finalize_repairs_manifest_after_publish_failure(self):
        original=Path.replace
        failed=[True]
        def flaky(self, target):
            if failed[0] and str(self).endswith('.tmp'):
                failed[0]=False; raise OSError('injected replace failure')
            return original(self,target)
        with patch.object(Path,'replace',flaky):
            with self.assertRaises(OSError): self.s.finalize_run(self.item.id,self.run.id,verdict='PASS',exit_code=0,command='pytest',cases=['one'],revision='final',dirty=False)
        result=self.s.finalize_run(self.item.id,self.run.id,verdict='PASS',exit_code=0,command='pytest',cases=['one'],revision='final',dirty=False)
        self.assertTrue(Path(self.run.artifact_dir,'finalize.json').exists()); self.assertEqual(result['state'],'SUCCEEDED')

    def test_evidence_requires_existing_hashable_file(self):
        with self.assertRaises(ValueError): self.s.record_evidence(self.item.id,self.run.id,kind='x',locator='/nope',command='x',cases=[],exit_code=0,revision='r',dirty=False,verdict='PASS')

    def test_evidence_replay_is_idempotent_but_changed_bytes_require_a_new_locator(self):
        path = self._evidence_file()
        first = self._record_evidence(path)
        self.assertEqual(self._record_evidence(path), first)
        path.write_text("corrected factual timestamp")
        with self.assertRaisesRegex(EvidenceConflict, "sha256.*new immutable"):
            self._record_evidence(path)
        with self.assertRaises(EvidenceConflict):
            self._record_evidence(path, kind="correction")
        with self.s.connection() as conn:
            rows = conn.execute("SELECT * FROM evidence").fetchall()
        self.assertEqual(len(rows), 1)
        self.assertEqual(dict(rows[0]), first)
        corrected = self._evidence_file("evidence-v2.txt")
        corrected.write_bytes(path.read_bytes())
        second = self._record_evidence(corrected, kind="correction")
        self.assertNotEqual(second["evidence_id"], first["evidence_id"])
        self.assertNotEqual(second["sha256"], first["sha256"])

    def test_evidence_replay_cannot_silently_change_a_verdict(self):
        path = self._evidence_file()
        self._record_evidence(path, verdict="FAIL")
        with self.assertRaisesRegex(EvidenceConflict, "verdict"):
            self._record_evidence(path, verdict="PASS")

    def test_cli_reports_a_changed_evidence_file_as_conflict(self):
        path = self._evidence_file()
        self._record_evidence(path)
        path.write_text("new report revision")
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main(["run", "evidence", self.item.id, self.run.id,
                         "--state-dir", str(self.s.state_dir), "--kind", "report",
                         "--locator", str(path), "--command", "python -m unittest",
                         "--case", "test_case", "--exit-code", "0",
                         "--revision", "revision-1", "--verdict", "PASS"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(stderr.getvalue())["error"]["code"], "EVIDENCE_CONFLICT")

    def test_evidence_rejects_secret_like_kind(self):
        with self.assertRaises(ValueError):
            self._record_evidence(self._evidence_file(), kind="api-key-report")

    def test_evidence_rejects_secret_like_locator_name(self):
        with self.assertRaises(ValueError):
            self._record_evidence(self._evidence_file("token-evidence.txt"))

    def test_evidence_rejects_secret_like_command(self):
        with self.assertRaises(ValueError):
            self._record_evidence(self._evidence_file(), command="pytest --token=value")

    def test_evidence_rejects_secret_like_cases(self):
        with self.assertRaises(ValueError):
            self._record_evidence(self._evidence_file(), cases=["password-case"])

    def test_evidence_rejects_secret_like_revision(self):
        with self.assertRaises(ValueError):
            self._record_evidence(self._evidence_file(), revision="secret-revision")

    def test_evidence_rejects_secret_like_verdict(self):
        with self.assertRaises(ValueError):
            self._record_evidence(self._evidence_file(), verdict="TOKEN_FOUND")

    def test_evidence_persists_numeric_exit_code_and_dirty_states(self):
        clean = self._record_evidence(
            self._evidence_file("clean.txt"), exit_code=7, dirty=False
        )
        dirty = self._record_evidence(
            self._evidence_file("dirty.txt"), exit_code=9, dirty=True
        )

        self.assertEqual((clean["exit_code"], clean["dirty"]), (7, 0))
        self.assertEqual((dirty["exit_code"], dirty["dirty"]), (9, 1))
        self.assertIs(type(clean["exit_code"]), int)
        self.assertIs(type(dirty["exit_code"]), int)
        with self.s.connection() as conn:
            persisted = conn.execute(
                "SELECT exit_code, typeof(exit_code), dirty FROM evidence ORDER BY evidence_id"
            ).fetchall()
        self.assertEqual([tuple(row) for row in persisted], [(7, "integer", 0), (9, "integer", 1)])

    def test_run_evidence_and_finalize_cli_shapes_are_available(self):
        a=build_parser().parse_args(['run','evidence','DEM-0001','RUN-0001','--kind','report','--locator','/tmp/report','--command','pytest','--exit-code','0','--revision','r','--verdict','PASS'])
        b=build_parser().parse_args(['run','finalize','DEM-0001','RUN-0001','--verdict','PASS','--exit-code','0','--command','pytest','--revision','r'])
        self.assertEqual((a.action,b.action),('evidence','finalize'))
