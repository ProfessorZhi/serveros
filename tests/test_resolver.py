"""Tests for the Context Capsule resolver.

Hermetic: every world is built in a temp directory. Nothing here reads this
machine's real records, worktrees, artifacts or state.
"""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
RESOLVER = ROOT / "context" / "resolver.py"

_spec = importlib.util.spec_from_file_location("serveros_resolver", RESOLVER)
resolver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolver)

ITEM = "DEMO-0001"


def make_world(base: Path, runs: list[tuple[str, str]], project="demo",
               source_refs: list[str] | None = None) -> dict[str, Path]:
    """A minimal but real ServerOS world: workspace, artifacts and state."""
    workspace = base / "workspace"
    artifacts = base / "artifacts"
    state_dir = base / "state/serveros"

    (workspace / "ops/workspace/serveros").mkdir(parents=True)
    (workspace / "ops/workspace/serveros/projects.yaml").write_text(
        "projects:\n"
        f"  {project}:\n"
        "    worktrees_under: demo-repo\n"
        "    enterprise_source: demo-delivery\n"
        "    delivery_branch: update/100-dev\n"
        "    test_envs: test-envs\n"
    )

    item_dir = workspace / "records/projects" / project / "items" / ITEM
    item_dir.mkdir(parents=True)
    card = "title: demo item\nkind: feature\nacceptance:\n  - it works\n"
    if source_refs is not None:
        card += "source_refs:\n" + "".join(f"  - {ref}\n" for ref in source_refs)
    (item_dir / "card.yaml").write_text(card)
    (item_dir / "brief.md").write_text("# Demo\n\nThe objective sentence.\n")

    run_root = artifacts / "work-items" / project / ITEM
    state_dir.mkdir(parents=True)
    conn = sqlite3.connect(state_dir / "serveros.db")
    conn.executescript(
        "CREATE TABLE items_index (item_id TEXT PRIMARY KEY, project TEXT, title TEXT, kind TEXT,"
        " card_locator TEXT, state TEXT, outcome TEXT, created_at TEXT, updated_at TEXT);"
        "CREATE TABLE runs (item_id TEXT, run_id TEXT, activity_id TEXT, worker TEXT,"
        " idempotency_key TEXT, state TEXT, artifact_dir TEXT, attempt INTEGER,"
        " started_at TEXT, ended_at TEXT, PRIMARY KEY (item_id, run_id));"
        "CREATE TABLE deliverables (deliverable_id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " item_id TEXT, kind TEXT, locator TEXT, created_at TEXT);"
        "CREATE TABLE leases (resource TEXT PRIMARY KEY, owner TEXT, token TEXT, expires_at TEXT);"
    )
    conn.execute(
        "INSERT INTO items_index VALUES (?,?,?,?,?,?,?,?,?)",
        (ITEM, project, "demo item", "feature", "", "ACTIVE", None, "", ""),
    )
    for run_id, state in runs:
        (run_root / run_id).mkdir(parents=True, exist_ok=True)
        conn.execute(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)",
            (ITEM, run_id, "A1", "w", run_id, state, str(run_root / run_id), 1, "", ""),
        )
    conn.commit()
    conn.close()
    return {"workspace": workspace, "artifacts": artifacts, "state_dir": state_dir}


def run_resolver(world: dict[str, Path], *extra: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(RESOLVER), ITEM, "--json",
         "--workspace-root", str(world["workspace"]),
         "--artifacts-root", str(world["artifacts"]),
         "--state-dir", str(world["state_dir"]),
         *extra],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )


def capsule(world: dict[str, Path], *extra: str) -> dict:
    proc = run_resolver(world, *extra)
    if proc.returncode != 0:
        raise AssertionError(proc.stderr)
    return json.loads(proc.stdout)


class RunSafetyTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_latest_run_state_is_reported(self):
        """The gap this closes: a newest Run that is still RUNNING must say so."""
        world = make_world(self.base, [("RUN-0001", "SUCCEEDED"), ("RUN-0002", "RUNNING")])
        runs = capsule(world)["runs"]
        self.assertEqual(runs["latest"]["id"], "RUN-0002")
        self.assertEqual(runs["latest"]["state"], "RUNNING")
        self.assertTrue(runs["latest"]["locator"].endswith("RUN-0002"))

    def test_every_in_flight_run_is_active(self):
        world = make_world(self.base, [
            ("RUN-0001", "SUCCEEDED"), ("RUN-0002", "ALLOCATED"),
            ("RUN-0003", "RUNNING"), ("RUN-0004", "FAILED"),
            ("RUN-0005", "FINALIZING"),
        ])
        active = {r["id"]: r["state"] for r in capsule(world)["runs"]["active"]}
        self.assertEqual(active, {"RUN-0002": "ALLOCATED", "RUN-0003": "RUNNING", "RUN-0005": "FINALIZING"})

    def test_terminal_runs_are_never_active(self):
        world = make_world(self.base, [
            ("RUN-0001", "SUCCEEDED"), ("RUN-0002", "FAILED"),
            ("RUN-0003", "CANCELLED"), ("RUN-0004", "INVALID"),
        ])
        self.assertEqual(capsule(world)["runs"]["active"], [])

    def test_capsule_never_grants_a_writable_run(self):
        """A read-only resolve cannot know the caller, so it must grant nothing."""
        world = make_world(self.base, [("RUN-0001", "RUNNING")])
        runs = capsule(world)["runs"]
        self.assertIs(runs["writable"], False)
        self.assertNotIn("writable", runs["latest"])

    def test_latest_is_ordering_not_ownership(self):
        """`latest` may itself be an in-flight Run — that is exactly the hazard."""
        world = make_world(self.base, [("RUN-0001", "SUCCEEDED"), ("RUN-0002", "RUNNING")])
        runs = capsule(world)["runs"]
        self.assertEqual(runs["latest"]["id"], runs["active"][-1]["id"])

    def test_runs_without_state_records_never_invent_a_state(self):
        world = make_world(self.base, [])
        runs = capsule(world)["runs"]
        self.assertIsNone(runs["latest"])
        self.assertEqual(runs["active"], [])
        self.assertIs(runs["writable"], False)


class HeldLeaseTests(unittest.TestCase):
    """T6: a row in `leases` is not a lease being held."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)
        self.world = make_world(self.base, [])
        self.db = self.world["state_dir"] / "serveros.db"

    def tearDown(self):
        self._tmp.cleanup()

    def _roots(self):
        return resolver.Roots(
            workspace_root=self.world["workspace"],
            artifacts_root=self.world["artifacts"],
            state_db=self.db,
        )

    def _lease(self, resource, expires_at):
        conn = sqlite3.connect(self.db)
        try:
            conn.execute(
                "INSERT INTO leases(resource,owner,token,expires_at) VALUES (?,?,?,?)",
                (resource, "agent:test", "1", expires_at),
            )
            conn.commit()
        finally:
            conn.close()

    def test_only_unexpired_leases_are_reported_as_held(self):
        self._lease("work-write:EXPIRED", "2000-01-01T00:00:00Z")
        self._lease("work-write:LIVE", "2999-01-01T00:00:00Z")
        self.assertEqual(resolver.held_leases(self._roots()), ["work-write:LIVE"])

    def test_the_expired_row_is_not_deleted_by_the_reader(self):
        """A read-only resolve must not turn 'expired' into 'never existed'."""
        self._lease("work-write:EXPIRED", "2000-01-01T00:00:00Z")
        self.assertEqual(resolver.held_leases(self._roots()), [])
        conn = sqlite3.connect(self.db)
        try:
            count = conn.execute("SELECT count(*) FROM leases").fetchone()[0]
        finally:
            conn.close()
        self.assertEqual(count, 1)


class RootContractTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_explicit_roots_win_over_the_environment(self):
        """The caller's resolved roots must be the ones actually read."""
        world = make_world(self.base, [("RUN-0001", "SUCCEEDED")])
        other = self.base / "decoy"
        other.mkdir()
        proc = subprocess.run(
            [sys.executable, str(RESOLVER), ITEM, "--json",
             "--workspace-root", str(world["workspace"]),
             "--artifacts-root", str(world["artifacts"]),
             "--state-dir", str(world["state_dir"])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={"PATH": "/usr/bin:/bin", "DATA_ROOT": str(other),
                 "WORKSPACE_ROOT": str(other), "ARTIFACTS_ROOT": str(other)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["runs"]["latest"]["id"], "RUN-0001")

    def test_state_dir_selects_the_database(self):
        """State is read from the explicit state dir, not a DATA_ROOT-derived one."""
        world = make_world(self.base, [("RUN-0001", "RUNNING")])
        other = self.base / "decoy-data"
        (other / "state/serveros").mkdir(parents=True)
        proc = subprocess.run(
            [sys.executable, str(RESOLVER), ITEM, "--json",
             "--workspace-root", str(world["workspace"]),
             "--artifacts-root", str(world["artifacts"]),
             "--state-dir", str(world["state_dir"])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={"PATH": "/usr/bin:/bin", "DATA_ROOT": str(other)},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["runs"]["latest"]["state"], "RUNNING")

    def test_missing_roots_still_fall_back_to_the_environment(self):
        """Standalone use keeps working: unnamed roots come from the environment.

        `DATA_ROOT` is named explicitly here. It did not need to be while the
        file carried a literal default, and that was the problem: the test was
        reading this machine. Which host this is, is a fact a test has to
        state, not inherit.
        """
        world = make_world(self.base, [("RUN-0001", "SUCCEEDED")])
        proc = subprocess.run(
            [sys.executable, str(RESOLVER), ITEM, "--json",
             "--workspace-root", str(world["workspace"]),
             "--artifacts-root", str(world["artifacts"])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={"PATH": "/usr/bin:/bin", "DATA_ROOT": str(self.base),
                 "SERVEROS_STATE_DIR": str(world["state_dir"])},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["runs"]["latest"]["id"], "RUN-0001")


class RootAuthorityTests(unittest.TestCase):
    """§Q6 — where the roots come from when nobody names them.

    The answer used to be `/opt/applications/data/home-hwz`: one machine's
    layout, compiled into a resolver every other machine also runs. On a host
    without that directory `infer_item` compared the caller's location against
    a root that was not there, found nothing, and reported "no item inferred" —
    the same sentence a correct empty answer produces.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _without_root_environment(self):
        """Everything that could name a root, removed — this machine's included."""
        return mock.patch.dict(
            os.environ,
            {key: os.environ[key] for key in os.environ
             if key not in ("DATA_ROOT", "WORKSPACE_ROOT", "SERVEROS_ARTIFACTS_ROOT",
                            "ARTIFACTS_ROOT")},
            clear=True,
        )

    def test_this_hosts_profile_answers_when_the_environment_does_not(self):
        """The profile is the same document `serverosd` reads, read the same way.

        Nothing in the resolver knows a machine name, a user name or a layout:
        the deployment states where its roots are, and the roles `data_root`
        and `artifacts` are the two it may state that the environment did not.
        """
        profile = self.base / "host-profile.yaml"
        profile.write_text(
            "schema: serveros/host-profile.v1\n"
            "node:\n"
            "  roots:\n"
            f"    identity_home: {self.base}\n"
            f"    artifacts: {self.base}/built\n",
            encoding="utf-8")
        with self._without_root_environment(), \
                mock.patch.dict(os.environ, {resolver.HOST_PROFILE_ENV: str(profile)}):
            roots = resolver.Roots.from_env()
        self.assertEqual(roots.data_root, self.base)
        self.assertEqual(roots.workspace_root, self.base / "workspace")
        self.assertEqual(roots.artifacts_root, self.base / "built")

    def test_a_host_that_says_nothing_is_refused_not_guessed(self):
        """No authority, no answer — and the refusal says which knob to turn."""
        with self._without_root_environment(), mock.patch.dict(
                os.environ,
                {resolver.HOST_PROFILE_ENV: str(self.base / "absent.yaml")}):
            with self.assertRaises(resolver.RootsUnresolved) as caught:
                resolver.Roots.from_env()
        message = str(caught.exception)
        self.assertIn("DATA_ROOT", message)
        self.assertIn(resolver.HOST_PROFILE_ENV, message)

    def test_the_environment_wins_over_the_profile(self):
        """A caller that has already resolved its own roots can hand them over."""
        profile = self.base / "host-profile.yaml"
        profile.write_text(
            "schema: serveros/host-profile.v1\nnode:\n  roots:\n"
            f"    identity_home: {self.base}/from-profile\n",
            encoding="utf-8")
        with self._without_root_environment(), mock.patch.dict(
                os.environ, {resolver.HOST_PROFILE_ENV: str(profile),
                             "DATA_ROOT": str(self.base / "from-env")}):
            self.assertEqual(resolver.Roots.from_env().data_root, self.base / "from-env")

    def test_an_item_is_inferred_from_every_layout_the_table_names(self):
        """All three patterns are the same shape, so all three are depth 2.

        The `artifacts/work-items` entry said 3 — one more than its own layout
        has — so it matched nothing on any node, ever. It was invisible beside
        the Beijing root above it: that root broke the other two patterns as
        well, so the one visible cause absorbed the blame for both.
        """
        roots = resolver.Roots(
            workspace_root=self.base / "workspace",
            artifacts_root=self.base / "artifacts",
            state_db=self.base / "state/serveros/serveros.db",
        )
        for cwd, expected in (
            (self.base / "workspace/worktrees/demo/SRV-0001", "SRV-0001"),
            (self.base / "workspace/test-envs/demo/MUC-0002", "MUC-0002"),
            (self.base / "artifacts/work-items/demo/LIT-0003", "LIT-0003"),
            # A Run directory under the Item is still that Item: the check is
            # on position, not on the path ending there.
            (self.base / "artifacts/work-items/demo/SRV-0001/RUN-0001", "SRV-0001"),
        ):
            with self.subTest(cwd=str(cwd)):
                self.assertEqual(resolver.infer_item(roots, cwd), expected)

    def test_a_directory_that_is_not_an_item_is_still_refused(self):
        """Widening the depth must not have widened what counts as an Item."""
        roots = resolver.Roots(
            workspace_root=self.base / "workspace",
            artifacts_root=self.base / "artifacts",
            state_db=self.base / "state/serveros/serveros.db",
        )
        for cwd in (self.base / "artifacts/work-items/demo",
                    self.base / "workspace/worktrees/demo",
                    self.base / "artifacts/work-items/other/not-an-item"):
            with self.subTest(cwd=str(cwd)):
                with self.assertRaises(ValueError):
                    resolver.infer_item(roots, cwd)


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_baseline_never_substitutes_for_head(self):
        """A recorded baseline must not be reported as the current revision."""
        world = make_world(self.base, [("RUN-0001", "SUCCEEDED")])
        worktree = world["workspace"] / "worktrees/demo-repo" / ITEM
        worktree.mkdir(parents=True)

        def git(*args):
            subprocess.run(["git", "-C", str(worktree), *args], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        git("init", "--quiet", "-b", "main")
        git("config", "user.email", "t@example.invalid")
        git("config", "user.name", "t")
        (worktree / "f").write_text("baseline\n")
        git("add", "-A")
        git("commit", "--quiet", "-m", "baseline")
        baseline = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                                  stdout=subprocess.PIPE, text=True).stdout.strip()
        (worktree / "f").write_text("moved on\n")
        git("commit", "--quiet", "-am", "second")
        head = subprocess.run(["git", "-C", str(worktree), "rev-parse", "HEAD"],
                              stdout=subprocess.PIPE, text=True).stdout.strip()

        self.assertNotEqual(baseline, head)
        development = capsule(world)["development"]
        self.assertEqual(development["revision"], head)
        self.assertNotEqual(development["revision"], baseline)


class SourceRefTests(unittest.TestCase):
    """`source_refs` is a mixed namespace, so each form has a different anchor.

    A wrong anchor hands an Agent a locator that does not exist — worse than
    handing it none, because it looks authoritative.
    """

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.base = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def world(self, refs: list[str]) -> dict[str, Path]:
        world = make_world(self.base, [("RUN-0001", "SUCCEEDED")], source_refs=refs)
        (world["workspace"] / "records/projects/demo/reports").mkdir(parents=True, exist_ok=True)
        (world["workspace"] / "records/information/topics").mkdir(parents=True, exist_ok=True)
        (world["workspace"] / "records/information/topics/note.md").write_text("n\n")
        return world

    def test_records_relative_ref_anchors_at_the_records_checkout(self):
        """`projects/...` is written from the records root, not from the Item."""
        world = self.world(["projects/demo/items/DEMO-0001/design.md"])
        self.assertEqual(
            capsule(world)["relevant_sources"],
            ["records/projects/demo/items/DEMO-0001/design.md"],
        )

    def test_records_relative_information_ref_anchors_at_the_records_checkout(self):
        world = self.world(["information/topics/note.md"])
        self.assertEqual(capsule(world)["relevant_sources"], ["records/information/topics/note.md"])

    def test_item_relative_ref_still_anchors_at_the_item_directory(self):
        """A plain relative path keeps the old meaning; only the prefixes changed."""
        world = self.world(["../../reports/topic.md"])
        self.assertEqual(
            capsule(world)["relevant_sources"], ["records/projects/demo/reports/topic.md"]
        )

    def test_fragment_survives_resolution(self):
        world = self.world(["../../reports/topic.md#L70", "information/topics/note.md#p=1-9"])
        self.assertEqual(
            capsule(world)["relevant_sources"],
            ["records/projects/demo/reports/topic.md#L70",
             "records/information/topics/note.md#p=1-9"],
        )

    def test_typed_locators_are_carried_verbatim(self):
        """A URI names a thing, not a file: never rewritten, never path-joined."""
        refs = [
            "conversation:SRV-0003-live-multi-workstream-2026-09-09",
            "feishu://wiki/node/abc",
            "github://ProfessorZhi/serveros",
            "https://example.invalid/report",
        ]
        self.assertEqual(capsule(self.world(refs))["relevant_sources"], refs)

    def test_absolute_ref_is_normalized_and_shortened(self):
        """An absolute path needs no anchor, but is still shortened to a root."""
        target = self.base / "workspace/records/information/topics/note.md"
        self.assertEqual(
            capsule(self.world([str(target)]))["relevant_sources"],
            ["records/information/topics/note.md"],
        )

    def test_no_source_refs_yields_an_empty_list(self):
        self.assertEqual(capsule(self.world([]))["relevant_sources"], [])


if __name__ == "__main__":
    unittest.main()
