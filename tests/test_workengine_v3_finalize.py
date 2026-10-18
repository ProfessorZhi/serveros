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

    def _record_evidence(self, locator, item=None, run=None, **overrides):
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
        return self.s.record_evidence(item or self.item.id, run or self.run.id, **metadata)

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

    def test_evidence_refuses_a_run_that_does_not_exist_and_records_nothing(self):
        """Measured 2026-10-08 against the production store, before the fix.

        `workengine run evidence SRV-0011 19 --locator …` wrote `evidence_id 224`
        with `run_id '19'` and exited 0, and no `runs` row anywhere carried that
        id. The refusal is not enough on its own: the point of the check is that
        the bad row must not land, so this asserts the row count too.
        """
        with self.assertRaisesRegex(KeyError, "unknown Run"):
            self._record_evidence(self._evidence_file(), run="RUN-9999")
        with self.s.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)

    def test_evidence_refuses_an_unknown_item_by_name(self):
        with self.assertRaisesRegex(KeyError, "unknown Item: DEM-9999"):
            self._record_evidence(self._evidence_file(), item="DEM-9999")
        with self.s.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)

    def test_evidence_names_the_item_that_owns_the_run(self):
        """A real Run id under the wrong Item is a third answer, not "unknown Run".

        Saying "unknown Run" there sends the caller hunting a typo in an id they
        copied correctly; the row they named exists and belongs to someone else.

        Run ids are per-Item sequences, so this case needs the other Item to be
        *ahead* of this one: DEM-0001 has only RUN-0001, and asking it for
        DEM-0002's RUN-0002 is the reachable form of the mistake. Handing it
        DEM-0002's RUN-0001 is not a mistake at all — this Item has a RUN-0001
        too, and the write lands on the caller's own Run, which is why the test
        advances DEM-0002 twice rather than once.
        """
        other = self.s.create_item(__import__('workengine').WorkItem("DEM-0002", "demo", "y", "test", "/card"))
        self.s.add_activity(Activity(other.id, "test", "test"))
        self.s.attach_context(__import__('workengine').ExecutionContext(other.id, "test", "repo", ContextMode.TEST, "base"))
        self.s.begin_run(other.id, "test", "worker", "key-2", Path(self.td.name) / "artifacts-2")
        theirs = self.s.begin_run(other.id, "test", "worker", "key-3", Path(self.td.name) / "artifacts-2")
        self.assertNotEqual(theirs.id, self.run.id)
        with self.assertRaisesRegex(KeyError, f"Run {theirs.id} belongs to Item DEM-0002, not DEM-0001"):
            self._record_evidence(self._evidence_file(), run=theirs.id)

    def test_evidence_still_accepts_the_run_the_caller_owns(self):
        """Positive control: the guard must refuse the wrong pair, not every pair."""
        recorded = self._record_evidence(self._evidence_file("owned.txt"))
        self.assertEqual((recorded["run_id"], recorded["item_id"]), (self.run.id, self.item.id))

    def test_cli_refuses_an_unknown_run_and_records_nothing(self):
        stderr = StringIO()
        with redirect_stderr(stderr):
            code = main(["run", "evidence", self.item.id, "RUN-9999",
                         "--state-dir", str(self.s.state_dir), "--kind", "report",
                         "--locator", str(self._evidence_file()), "--command", "python -m unittest",
                         "--case", "test_case", "--exit-code", "0",
                         "--revision", "revision-1", "--verdict", "PASS"])
        self.assertEqual(code, 2)
        error = json.loads(stderr.getvalue())["error"]
        self.assertEqual(error["code"], "INVALID_INPUT")
        self.assertIn("unknown Run", error["message"])
        with self.s.connection() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)

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


class TheVerdictIsJudgedByItsWordNotItsSpelling(unittest.TestCase):
    """A judgement has one word; the case it arrives in is not a second fact.

    Measured 2026-10-08: `run finalize … --verdict pass` (lower case, exit 0)
    recorded `{"state": "FAILED", "verdict": "pass"}` and reported no error, on a
    Run whose work had passed. `advance --to` next door declares its vocabulary
    with `choices`; `--verdict` declared nothing, so the spelling that counted
    was knowable only by reading the comparison in the store.
    """

    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.s = ServerOSStore(Path(self.td.name) / "state")
        self.s.register_project("demo", "DEM"); self.item = self.s.create_item(__import__('workengine').WorkItem("DEM-0001", "demo", "x", "test", "/card"))
        self.s.add_activity(Activity(self.item.id, "test", "test")); self.s.attach_context(__import__('workengine').ExecutionContext(self.item.id, "test", "repo", ContextMode.TEST, "base"))
        self.run = self.s.begin_run(self.item.id, "test", "worker", "key", Path(self.td.name) / "artifacts")
        for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING): self.run = self.s.advance_run(self.item.id, self.run.id, state)

    def tearDown(self): self.td.cleanup()

    def _finalize(self, verdict):
        return self.s.finalize_run(self.item.id, self.run.id, verdict=verdict, exit_code=0,
                                  command="pytest", cases=["one"], revision="final", dirty=False)

    def test_a_lower_case_pass_concludes_the_run_succeeded(self):
        result = self._finalize("pass")
        self.assertEqual(result["state"], "SUCCEEDED")
        self.assertEqual(result["verdict"], "PASS", "the recorded word is the engine's, not the caller's")
        self.assertEqual(self.s.get_run(self.item.id, self.run.id).state, RunState.SUCCEEDED)

    def test_the_word_still_decides_and_folding_does_not_conclude_everything_succeeded(self):
        # The negative control: folding the case must not turn every verdict into
        # a pass. A different word is still a different judgement.
        self.assertEqual(self._finalize("fail")["state"], "FAILED")

    def test_surrounding_whitespace_is_not_a_second_judgement_either(self):
        self.assertEqual(self._finalize("  PASS \n")["state"], "SUCCEEDED")

    def test_a_verdict_the_corpus_carries_is_recorded_without_being_refused(self):
        # `INCONCLUSIVE`, `PARTIAL`, `SUCCESS` and `BUDGET_EXHAUSTED` are all on
        # this node's Runs. The spelling is fixed here; the membership is not
        # closed, because closing it would refuse history rather than spell it.
        result = self._finalize("inconclusive")
        self.assertEqual(result["verdict"], "INCONCLUSIVE")
        self.assertEqual(result["state"], "FAILED")

    def _plant_a_row_from_before_the_change(self, path, verdict):
        """Write a row the way the store would have written it before today.

        The fold on the STORED side only matters for a row that is already on
        disk in the old spelling — a row written through `record_evidence` today
        arrives folded, so replaying it proves nothing about this. Reaching into
        the table is the point: this is the row a node carries forward.
        """
        import hashlib
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        with self.s.connection() as conn:
            conn.execute(
                "INSERT INTO evidence(item_id,run_id,kind,locator,sha256,command,cases_json,"
                "exit_code,revision,dirty,verdict,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (self.item.id, self.run.id, "report", str(path), digest, "pytest",
                 json.dumps(["one"]), 0, "r", 0, verdict, "2026-01-01T00:00:00Z"))
            return dict(conn.execute("SELECT * FROM evidence ORDER BY evidence_id DESC LIMIT 1").fetchone())

    def test_a_replay_that_only_changes_the_spelling_is_not_a_conflict(self):
        path = Path(self.td.name) / "evidence.txt"; path.write_text("evidence")
        planted = self._plant_a_row_from_before_the_change(path, "pass")
        self.assertEqual(planted["verdict"], "pass", "the planted row is the old spelling, or this proves nothing")
        replayed = self.s.record_evidence(self.item.id, self.run.id, locator=str(path), verdict="PASS",
                                          kind="report", command="pytest", cases=["one"],
                                          exit_code=0, revision="r", dirty=False)
        self.assertEqual(replayed["evidence_id"], planted["evidence_id"],
                         "the same judgement in a different case is the same judgement, not a new row")

    def test_a_row_written_in_the_old_spelling_is_still_a_different_word_from_another(self):
        # The control for the test above: folding must not make every word equal
        # to every other, or the conflict this module exists to raise is gone.
        path = Path(self.td.name) / "evidence.txt"; path.write_text("evidence")
        self._plant_a_row_from_before_the_change(path, "fail")
        with self.assertRaisesRegex(EvidenceConflict, "verdict"):
            self.s.record_evidence(self.item.id, self.run.id, locator=str(path), verdict="PASS",
                                   kind="report", command="pytest", cases=["one"],
                                   exit_code=0, revision="r", dirty=False)

    def test_a_genuinely_different_verdict_is_still_a_conflict(self):
        path = Path(self.td.name) / "evidence.txt"; path.write_text("evidence")
        metadata = dict(kind="report", command="pytest", cases=["one"], exit_code=0,
                        revision="r", dirty=False)
        self.s.record_evidence(self.item.id, self.run.id, locator=str(path), verdict="fail", **metadata)
        with self.assertRaisesRegex(EvidenceConflict, "verdict"):
            self.s.record_evidence(self.item.id, self.run.id, locator=str(path), verdict="PASS", **metadata)

    def test_the_cli_states_the_rule_a_caller_could_not_read_before(self):
        # Read the help a caller actually sees, not the parser's internals: the
        # defect was that nothing addressable said which word counted, so the
        # thing to pin is the rendered sentence.
        def _run_actions(parser):
            return parser._subparsers._group_actions[0].choices["run"]._subparsers._group_actions[0].choices
        actions = _run_actions(build_parser())
        for verb in ("evidence", "finalize"):
            help_text = actions[verb].format_help()
            self.assertIn("PASS", help_text, f"`run {verb}` does not name the word that concludes a Run")
            self.assertIn("SUCCEEDED", help_text, f"`run {verb}` does not say what the word concludes")
            self.assertIn("FAILED", help_text, f"`run {verb}` does not say what any other word concludes")
