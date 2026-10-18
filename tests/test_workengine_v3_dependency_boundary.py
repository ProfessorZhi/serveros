"""A dependency the store does not have is named, not traced.

Measured 2026-10-08 on the installed CLI (`~/.local/bin/workengine`,
tree `c3b56f3b`, the merge base this branch starts from): eleven calls that name
an Item, an activity or a Run the store does not have answered with a Python
traceback and exit code 1. The CLI's contract with a caller is a JSON error and
exit 2 — `main` catches `(ValueError, KeyError, OSError)` — so a traceback is not
a stricter answer, it is the absence of one. Two of the eleven were the database
speaking (`sqlite3.IntegrityError: FOREIGN KEY constraint failed`), one was a
`None` dereference (`AttributeError: 'NoneType' object has no attribute
'project'`), and one was the primary key (`UNIQUE constraint failed`).

A second family was answering, but about the wrong thing: `run show`, `run
advance`, `run cancel` and `run abandon` called a Run that exists under another
Item "unknown Run", and `activity transition DEM-9999 act-1` named the activity
for an Item that does not exist — while `run evidence` and `run finalize`, given
the same arguments, already named the Item that owns the Run. One boundary
existed (`_require_run`) and three call sites did not use it.

Every test here is one of those measurements, turned around: the refusal must
name the half that is missing, and a dependency that *is* there must still be
accepted — a guard that refuses everything would satisfy the first half alone.
"""
import json
import tempfile
import unittest
from contextlib import redirect_stderr
from io import StringIO
from pathlib import Path

from workengine import ExecutionContext, WorkItem
from workengine.model import Activity, ActivityState, ContextMode, RunState
from workengine.store import ServerOSStore
from workengine.cli import main


class ADependencyIsNamed(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.state = Path(self.td.name) / "state"
        self.artifacts = Path(self.td.name) / "artifacts"
        self.s = ServerOSStore(self.state)
        self.s.register_project("demo", "DEM")
        self.item = self.s.create_item(WorkItem("DEM-0001", "demo", "x", "test", "/card"))
        self.s.add_activity(Activity(self.item.id, "act-1", "develop"))
        self.s.register_source("docs", "doc-1", revision="r1", locator="file:///tmp/doc-1")
        self.s.record_observation("docs:doc-1:r1", "obs-1", semantic_type="text",
                                  content_hash="a" * 64)
        self.s.record_claim("clm-1", "file:///tmp/clm-1", ["obs-1"])
        self.s.create_candidate("cnd-1", "demo", "t", "test", dedupe_key="d1",
                                claim_ids=["clm-1"])
        # DEM-0002 is *ahead* of DEM-0001 on purpose: DEM-0001 has no Runs at
        # all, so `DEM-0001/RUN-0001` can only ever mean DEM-0002's row. Run ids
        # are per-Item sequences, so an Item with a RUN-0001 of its own would be
        # naming its own Run and the cross-Item case would not exist.
        self.other = self.s.create_item(WorkItem("DEM-0002", "demo", "y", "test", "/card"))
        self.s.add_activity(Activity(self.other.id, "act-2", "develop"))
        self.theirs = self.s.begin_run(self.other.id, "act-2", "worker", "key-2", self.artifacts)

    def tearDown(self):
        self.td.cleanup()

    def _cli(self, *args):
        """One CLI call, with `--state-dir` right after the group name.

        The action subparsers of `candidate` and `federation` are built without
        `parents=[_common()]`, so `--state-dir` is only accepted directly after
        the group; putting it there is the form that works for every group.
        """
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main([args[0], "--state-dir", str(self.state), *args[1:]])
        body = stderr.getvalue().strip()
        return code, (json.loads(body)["error"] if body.startswith("{") else None)

    def _count(self, table):
        with self.s.connection() as conn:
            return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]

    # ---- the measurement table, turned around ----------------------------------

    def test_every_measured_path_answers_exit_two_and_a_sentence(self):
        """The eleven tracebacks, each named. Exit 2 plus `error.message`, no traceback.

        This is the whole defect in one place: on the merge base every case below
        exited 1 after printing `Traceback (most recent call last)`. The
        arguments are the ones the probe used, so a failure here is the same
        measurement that was red before the fix.
        """
        cases = {
            "activity add unknown Item":
                ("activity", "add", "DEM-9999", "act-x", "--kind", "develop"),
            "activity add with an unknown --needs":
                ("activity", "add", "DEM-0001", "act-3", "--kind", "develop", "--needs", "act-none"),
            "activity add its own id twice":
                ("activity", "add", "DEM-0001", "act-1", "--kind", "develop"),
            "activity add its own id as another kind":
                ("activity", "add", "DEM-0001", "act-1", "--kind", "test"),
            "activity transition unknown activity":
                ("activity", "transition", "DEM-0001", "act-none", "--to", "CLOSED"),
            "activity transition unknown Item":
                ("activity", "transition", "DEM-9999", "act-1", "--to", "CLOSED"),
            "context attach unknown activity":
                ("context", "attach", "DEM-0001", "act-none", "--repo", "r",
                 "--mode", "develop", "--revision", "r1"),
            "context attach unknown Item":
                ("context", "attach", "DEM-9999", "act-1", "--repo", "r",
                 "--mode", "develop", "--revision", "r1"),
            "run begin unknown Item":
                ("run", "begin", "DEM-9999", "act-1", "--worker", "w",
                 "--idempotency-key", "k-x"),
            "run begin unknown activity":
                ("run", "begin", "DEM-0001", "act-none", "--worker", "w",
                 "--idempotency-key", "k-x"),
        }
        for label, argv in cases.items():
            with self.subTest(label):
                code, error = self._cli(*argv)
                self.assertEqual(code, 2, label)
                self.assertIsNotNone(error, label)
                self.assertEqual(error["code"], "INVALID_INPUT", label)
                self.assertTrue(error["message"].strip(), label)

    def test_the_cross_item_family_names_the_owner_on_every_verb(self):
        """Six verbs, one sentence — three of them did not use the boundary that wrote it.

        `run evidence` and `run finalize` already answered this way; `run show`,
        `run advance`, `run cancel` and `run abandon` answered "unknown Run" for
        the same arguments, which sends a caller looking for a typo in an id they
        copied correctly.
        """
        expected = f"Run {self.theirs.id} belongs to Item DEM-0002, not DEM-0001"
        calls = {
            "run show": ("run", "show", "DEM-0001", self.theirs.id),
            "run advance": ("run", "advance", "DEM-0001", self.theirs.id, "--to", "RUNNING"),
            "run cancel": ("run", "cancel", "DEM-0001", self.theirs.id, "--reason", "r",
                           "--actor", "a", "--reason-code", "OPERATOR_CANCELLED"),
            "run abandon": ("run", "abandon", "DEM-0001", self.theirs.id, "--reason", "r",
                            "--actor", "a"),
        }
        for label, argv in calls.items():
            with self.subTest(label):
                code, error = self._cli(*argv)
                self.assertEqual(code, 2, label)
                # `str(KeyError("x"))` is the repr of its argument, quotes
                # included, so the sentence is asserted as contained rather than
                # as equal — the quoting is `KeyError`'s, not the store's.
                self.assertIn(expected, error["message"], label)
                self.assertNotIn("unknown Run", error["message"], label)
        with self.assertRaisesRegex(KeyError, expected):
            self.s.require_run("DEM-0001", self.theirs.id)
        with self.assertRaisesRegex(KeyError, expected):
            self.s.run_events_summary("DEM-0001", self.theirs.id)

    def test_an_unknown_item_is_not_reported_as_an_unknown_run(self):
        """The Item half is checked first, and says so.

        Before the fix, `run show DEM-9999 RUN-0001` answered "unknown Run:
        DEM-9999/RUN-0001" — blaming the half that may well have been copied
        correctly.
        """
        for verb, tail in (("show", ()), ("advance", ("--to", "RUNNING"))):
            with self.subTest(verb):
                code, error = self._cli("run", verb, "DEM-9999", "RUN-0001", *tail)
                self.assertEqual(code, 2)
                self.assertIn("unknown Item: DEM-9999", error["message"])
                self.assertNotIn("unknown Run", error["message"])

    # ---- an activity is named by the half that is missing ----------------------

    def test_activity_add_refuses_an_unknown_item_by_name(self):
        with self.assertRaisesRegex(KeyError, "unknown Item: DEM-9999"):
            self.s.add_activity(Activity("DEM-9999", "act-x", "develop"))
        with self.s.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0], 2)

    def test_activity_add_refuses_an_unknown_dependency_by_name(self):
        with self.assertRaisesRegex(KeyError, "unknown activity: DEM-0001/act-none"):
            self.s.add_activity(Activity(self.item.id, "act-3", "develop"), ["act-none"])
        with self.s.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM activities").fetchone()[0], 2)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM activity_dependencies").fetchone()[0], 0)

    def test_activity_add_refuses_a_dependency_of_another_item_by_name(self):
        with self.assertRaisesRegex(KeyError, "activity act-2 belongs to Item DEM-0002, not DEM-0001"):
            self.s.add_activity(Activity(self.item.id, "act-3", "develop"), ["act-2"])

    def test_activity_add_refuses_to_declare_the_same_activity_twice(self):
        """A second `add` cannot add anything; it can only disagree.

        Measured before this refusal: `activity add` twice for one id answered
        `sqlite3.IntegrityError: UNIQUE constraint failed`. The row it would
        write is the row that is there, so nothing is refused that was working —
        and the kind it already has is not rewritten.
        """
        self.s.add_activity(Activity(self.item.id, "act-3", "develop"))
        with self.assertRaisesRegex(ValueError, "already declared as develop"):
            self.s.add_activity(Activity(self.item.id, "act-3", "develop"))
        with self.assertRaisesRegex(ValueError, "already declared as develop"):
            self.s.add_activity(Activity(self.item.id, "act-3", "test"))
        with self.s.connection() as conn:
            kind = conn.execute(
                "SELECT kind FROM activities WHERE item_id=? AND activity_id=?",
                (self.item.id, "act-3"),
            ).fetchone()["kind"]
        self.assertEqual(kind, "develop")
        self.assertEqual(self._count("activities"), 3)

    def test_activity_transition_names_the_item_not_the_activity(self):
        """`activity transition DEM-9999 act-1` used to answer `'act-1'` — the half that exists."""
        with self.assertRaisesRegex(KeyError, "unknown Item: DEM-9999"):
            self.s.transition_activity("DEM-9999", "act-1", ActivityState.READY)
        with self.assertRaisesRegex(KeyError, "unknown activity: DEM-0001/act-none"):
            self.s.transition_activity(self.item.id, "act-none", ActivityState.READY)

    # ---- a context is attached to an activity that exists ----------------------

    def test_attaching_a_context_names_the_missing_half(self):
        with self.assertRaisesRegex(KeyError, "unknown Item: DEM-9999"):
            self.s.attach_context(ExecutionContext("DEM-9999", "act-1", "repo",
                                                   ContextMode.DEVELOP, "r1"))
        with self.assertRaisesRegex(KeyError, "unknown activity: DEM-0001/act-none"):
            self.s.attach_context(ExecutionContext(self.item.id, "act-none", "repo",
                                                   ContextMode.DEVELOP, "r1"))
        with self.assertRaisesRegex(KeyError, "activity act-2 belongs to Item DEM-0002, not DEM-0001"):
            self.s.attach_context(ExecutionContext(self.item.id, "act-2", "repo",
                                                   ContextMode.DEVELOP, "r1"))
        self.assertEqual(self._count("execution_contexts"), 0)

    # ---- a run is begun against things that exist ------------------------------

    def test_run_begin_names_an_unknown_item_instead_of_dereferencing_none(self):
        """`begin_run` used to reach `self.get_item(item_id).project` and raise AttributeError.

        `AttributeError` is not a `KeyError`, so `main` did not catch it and the
        caller got a traceback; and the attribute it named was never the
        caller's problem.
        """
        with self.assertRaisesRegex(KeyError, "unknown Item: DEM-9999"):
            self.s.begin_run("DEM-9999", "act-1", "worker", "k-a", self.artifacts)
        with self.assertRaisesRegex(KeyError, "unknown activity: DEM-0001/act-none"):
            self.s.begin_run(self.item.id, "act-none", "worker", "k-b", self.artifacts)
        with self.assertRaisesRegex(KeyError, "activity act-2 belongs to Item DEM-0002, not DEM-0001"):
            self.s.begin_run(self.item.id, "act-2", "worker", "k-c", self.artifacts)
        self.assertEqual(self._count("runs"), 1)

    # ---- the other dependency planes -------------------------------------------

    def test_candidate_match_and_promote_name_the_missing_item(self):
        with self.assertRaisesRegex(KeyError, "unknown Item: DEM-9999"):
            self.s.match_candidate("cnd-1", "DEM-9999")
        with self.assertRaisesRegex(KeyError, "unknown Item: DEM-9999"):
            self.s.promote_candidate("cnd-1", "DEM-9999")
        with self.s.connection() as conn:
            state = conn.execute(
                "SELECT state FROM item_candidates WHERE candidate_id='cnd-1'").fetchone()["state"]
        self.assertEqual(state, "NEW")

    def test_the_information_verbs_name_what_is_missing(self):
        with self.assertRaisesRegex(KeyError, "unknown source: nosuch:src:r1"):
            self.s.record_observation("nosuch:src:r1", "obs-x", semantic_type="text",
                                      content_hash="b" * 64)
        with self.assertRaisesRegex(KeyError, "unknown observation: obs-none"):
            self.s.record_claim("clm-x", "file:///tmp/c", ["obs-none"])
        with self.assertRaisesRegex(KeyError, "unknown claim: clm-none"):
            self.s.create_candidate("cnd-x", "demo", "t", "test", dedupe_key="dx",
                                    claim_ids=["clm-none"])
        with self.assertRaisesRegex(KeyError, "unknown Item: DEM-9999"):
            self.s.record_feedback("DEM-9999", "file:///tmp/f", "k", "obs-1")

    # ---- controls: a dependency that is there is still accepted ----------------

    def test_a_dependency_that_exists_is_still_accepted(self):
        """Without this the refusals above are satisfied by refusing everything."""
        self.s.add_activity(Activity(self.item.id, "act-3", "develop"), ["act-1"])
        self.s.attach_context(ExecutionContext(self.item.id, "act-3", "repo",
                                               ContextMode.DEVELOP, "r1"))
        self.s.transition_activity(self.item.id, "act-3", ActivityState.READY)
        run = self.s.begin_run(self.item.id, "act-3", "worker", "k-ok", self.artifacts)
        self.assertEqual((run.item_id, run.activity_id, run.state),
                         (self.item.id, "act-3", RunState.ALLOCATED))
        self.s.advance_run(self.item.id, run.id, RunState.PREPARING)
        self.s.match_candidate("cnd-1", self.item.id)
        self.s.record_feedback(self.item.id, "file:///tmp/f", "k", "obs-1")
        with self.s.connection() as conn:
            needs = [r["needs_activity_id"] for r in conn.execute(
                "SELECT needs_activity_id FROM activity_dependencies")]
        self.assertEqual(needs, ["act-1"])

    def test_replaying_what_already_holds_is_still_a_replay(self):
        """The two keyed adds that *are* replays by design are untouched by this change."""
        self.s.attach_context(ExecutionContext(self.item.id, "act-1", "repo",
                                               ContextMode.DEVELOP, "r1"))
        self.s.attach_context(ExecutionContext(self.item.id, "act-1", "repo",
                                               ContextMode.TEST, "r2"))
        contexts = self.s.execution_contexts(self.item.id, "act-1")
        self.assertEqual([c.revision for c in contexts], ["r2"])
        first = self.s.begin_run(self.item.id, "act-1", "worker", "same-key", self.artifacts)
        second = self.s.begin_run(self.item.id, "act-1", "worker", "same-key", self.artifacts)
        self.assertEqual(first.id, second.id)
        self.assertEqual(self._count("runs"), 2)


if __name__ == "__main__":
    unittest.main()
