"""Tests for the Context Capsule resolver.

Hermetic: every world is built in a temp directory. Nothing here reads this
machine's real records, worktrees, artifacts or state.
"""
import importlib.util
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

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
        """Standalone use keeps working: unnamed roots come from the environment."""
        world = make_world(self.base, [("RUN-0001", "SUCCEEDED")])
        proc = subprocess.run(
            [sys.executable, str(RESOLVER), ITEM, "--json",
             "--workspace-root", str(world["workspace"]),
             "--artifacts-root", str(world["artifacts"])],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            env={"PATH": "/usr/bin:/bin", "SERVEROS_STATE_DIR": str(world["state_dir"])},
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["runs"]["latest"]["id"], "RUN-0001")


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
