import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from workengine.cli import build_parser
from workengine.engine import ServerOS, default_state_dir
from workengine.model import RunCancelReason, RunState, now_iso
from workengine.store import ServerOSStore


class RunCancelTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.s = ServerOSStore(self.root / "state")
        self.s.register_project("demo", "DEM")
        import workengine

        self.item = self.s.create_item(
            workengine.WorkItem("DEM-0001", "demo", "x", "test", "/card")
        )
        self.s.add_activity(workengine.Activity(self.item.id, "test", "test"))

    def tearDown(self):
        self.td.cleanup()

    def _run_at(self, state, key="key"):
        run = self.s.begin_run(self.item.id, "test", "worker", key, self.root / "artifacts")
        if state == RunState.ALLOCATED:
            return run
        for step in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
            run = self.s.advance_run(self.item.id, run.id, step)
            if run.state == state:
                return run
        raise AssertionError(f"fixture cannot reach {state.value} by advancing")

    def _events(self, event_type="run_terminated"):
        return [e for e in self.s.item_events(self.item.id) if e["event_type"] == event_type]

    # ── guards shared by the mutation tests ────────────────────────────────

    def _assert_run_termination_is_audited(self, run_id, *, to="CANCELLED", reason="r", actor="a",
                                           reason_code=None, evidence=()):
        """The audit guard: every termination must leave exactly one matching record."""
        events = self._events()
        self.assertEqual(len(events), 1, f"expected exactly one audit record, got {len(events)}")
        payload = events[0]["payload"]
        run = self.s.get_run(self.item.id, run_id)
        self.assertEqual(payload["run"], run_id)
        self.assertEqual(payload["to"], to)
        self.assertEqual(payload["reason"], reason)
        self.assertEqual(payload["actor"], actor)
        self.assertEqual(payload["evidence"], list(evidence))
        if reason_code is not None:
            self.assertEqual(payload["reason_code"], reason_code)
        self.assertEqual(payload["to"], run.state.value)
        self.assertEqual(events[0]["at"], run.ended_at)

    def _assert_state_machine_still_decides(self, run_id):
        """The legality guard: a terminal Run can never be restated."""
        with self.assertRaises(ValueError):
            self.s.cancel_run(self.item.id, run_id, reason="again", actor="a")

    # ── positive ───────────────────────────────────────────────────────────

    def test_cancels_from_every_non_terminal_state(self):
        for index, state in enumerate(
            (RunState.ALLOCATED, RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING)
        ):
            run = self._run_at(state, key=f"k{index}")
            result = self.s.cancel_run(
                self.item.id, run.id, reason=f"stop {state.value}", actor="operator",
                acknowledge_active_worker=True,
            )
            self.assertEqual(result["from"], state.value)
            self.assertEqual(result["to"], "CANCELLED")
            self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.CANCELLED)
            self.assertTrue(self.s.get_run(self.item.id, run.id).ended_at)

    def test_abandon_maps_to_cancelled_with_its_own_reason_code(self):
        run = self._run_at(RunState.ALLOCATED)
        result = self.s.cancel_run(
            self.item.id, run.id, reason="walked away", actor="operator", operation="abandon"
        )
        self.assertEqual(result["to"], "CANCELLED")
        self.assertEqual(result["reason_code"], RunCancelReason.ABANDONED.value)
        self.assertEqual(result["operation"], "abandon")

    def test_cancel_can_state_that_a_result_must_not_count(self):
        run = self._run_at(RunState.RUNNING, key="invalid")
        result = self.s.cancel_run(
            self.item.id, run.id, reason="measurement invalid", actor="operator",
            target=RunState.INVALID, acknowledge_active_worker=True,
        )
        self.assertEqual(result["to"], "INVALID")
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.INVALID)

    # ── audit ──────────────────────────────────────────────────────────────

    def test_termination_preserves_reason_actor_previous_state_and_references(self):
        reference = self.root / "governance-note.md"
        reference.write_text("why")
        run = self._run_at(RunState.PREPARING, key="audit")
        self.s.cancel_run(
            self.item.id, run.id, reason="superseded", actor="governance",
            reason_code=RunCancelReason.SUPERSEDED, evidence=(str(reference),),
        )
        self._assert_run_termination_is_audited(
            run.id, reason="superseded", actor="governance",
            reason_code="SUPERSEDED", evidence=(str(reference),),
        )

    def test_history_is_append_only(self):
        first = self._run_at(RunState.ALLOCATED, key="a1")
        second = self._run_at(RunState.PREPARING, key="a2")
        self.s.cancel_run(self.item.id, first.id, reason="one", actor="op")
        before = list(self.s.item_events(self.item.id))
        self.s.cancel_run(self.item.id, second.id, reason="two", actor="op")
        after = self.s.item_events(self.item.id)
        self.assertEqual(after[: len(before)], before)
        self.assertEqual(len(after), len(before) + 1)

    # ── negative ───────────────────────────────────────────────────────────

    def test_unknown_run_fails(self):
        with self.assertRaises(KeyError):
            self.s.cancel_run(self.item.id, "RUN-9999", reason="r", actor="a")

    def test_missing_reason_or_actor_fails(self):
        run = self._run_at(RunState.ALLOCATED)
        with self.assertRaises(ValueError):
            self.s.cancel_run(self.item.id, run.id, reason="   ", actor="a")
        with self.assertRaises(ValueError):
            self.s.cancel_run(self.item.id, run.id, reason="r", actor="")
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.ALLOCATED)

    def test_invalid_target_and_unknown_reason_code_fail(self):
        run = self._run_at(RunState.ALLOCATED)
        for target in (RunState.RUNNING, RunState.SUCCEEDED, RunState.FAILED):
            with self.assertRaises(ValueError):
                self.s.cancel_run(self.item.id, run.id, reason="r", actor="a", target=target)
        with self.assertRaises(ValueError):
            self.s.cancel_run(self.item.id, run.id, reason="r", actor="a", reason_code="NOT_A_TABLE_ENTRY")
        with self.assertRaises(ValueError):
            self.s.cancel_run(self.item.id, run.id, reason="r", actor="a", evidence=("relative/path",))
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.ALLOCATED)

    def test_terminal_run_is_rejected_and_identically_restated_is_a_no_op(self):
        run = self._run_at(RunState.FINALIZING, key="term")
        self.s.finalize_run(
            self.item.id, run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision="r", dirty=False,
        )
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.SUCCEEDED)
        with self.assertRaises(ValueError):
            self.s.cancel_run(self.item.id, run.id, reason="r", actor="a")

        second = self._run_at(RunState.ALLOCATED, key="noop")
        self.s.cancel_run(self.item.id, second.id, reason="first", actor="op")
        before = self.s.item_events(self.item.id)
        again = self.s.cancel_run(self.item.id, second.id, reason="second", actor="op")
        self.assertTrue(again["already_terminal"])
        self.assertEqual(again["ended_at"], self.s.get_run(self.item.id, second.id).ended_at)
        self.assertEqual(self.s.item_events(self.item.id), before)
        # a no-op must report the recorded termination, not the caller's request
        self.assertEqual(again["reason"], "first")
        self.assertEqual(again["actor"], "op")

    def test_a_run_ended_by_another_path_is_refused_rather_than_echoed(self):
        run = self._run_at(RunState.FINALIZING, key="other")
        self.s.finalize_run(
            self.item.id, run.id, verdict="FAIL", exit_code=1,
            command="pytest", cases=["one"], revision="r", dirty=False,
        )
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.FAILED)
        with self.assertRaises(ValueError):
            self.s.cancel_run(self.item.id, run.id, reason="r", actor="a")
        self.assertEqual(self._events(), [])

    def test_a_no_op_on_a_cancellation_recorded_outside_this_api_reports_no_reason(self):
        # A Run terminated before this API existed has the terminal state but no
        # audit record. Reporting the caller's reason here would invent history.
        run = self._run_at(RunState.RUNNING, key="legacy")
        with self.s.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE runs SET state='CANCELLED',ended_at=? WHERE item_id=? AND run_id=?",
                (now_iso(), self.item.id, run.id),
            )
        again = self.s.cancel_run(self.item.id, run.id, reason="invented", actor="ghost")
        self.assertTrue(again["already_terminal"])
        self.assertEqual((again["reason"], again["actor"], again["evidence"]), ("", "", []))
        self.assertEqual(self._events(), [])

    def test_advance_run_can_no_longer_terminate_a_run(self):
        run = self._run_at(RunState.RUNNING, key="adv")
        for target in (RunState.CANCELLED, RunState.INVALID):
            with self.assertRaises(ValueError):
                self.s.advance_run(self.item.id, run.id, target)
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.RUNNING)
        self.assertEqual(self._events(), [])
        # the forward chain still advances
        self.assertEqual(
            self.s.advance_run(self.item.id, run.id, RunState.FINALIZING).state, RunState.FINALIZING
        )

    def test_advance_run_can_no_longer_pronounce_a_verdict(self):
        """A Run must not reach SUCCEEDED with nothing recorded behind it.

        `advance --to SUCCEEDED` used to be a legal way to end a FINALIZING Run:
        the ledger said "succeeded" and no evidence row, verdict, exit code or
        revision said on what basis. The state machine permits the transition —
        FINALIZING is the only state that can reach SUCCEEDED — so the guard has
        to live where the claim would be made, which is here.
        """
        run = self._run_at(RunState.FINALIZING, key="verdict")
        with self.assertRaises(ValueError) as caught:
            self.s.advance_run(self.item.id, run.id, RunState.SUCCEEDED)
        self.assertIn("finalize_run", str(caught.exception))
        # Nothing moved: a refused verdict is not a half-written one.
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.FINALIZING)
        self.assertEqual(self._events(), [])

    def test_a_finalizing_run_is_judged_by_finalize_not_by_advance(self):
        run = self._run_at(RunState.FINALIZING, key="judge")
        with self.assertRaises(ValueError):
            self.s.advance_run(self.item.id, run.id, RunState.FAILED)
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.FINALIZING)
        # finalize_run still reaches both verdicts, with the evidence attached.
        self.assertEqual(
            self.s.finalize_run(
                self.item.id, run.id, verdict="FAIL", exit_code=1, command="pytest",
                cases=["x"], revision="abc", dirty=False,
            )["state"],
            "FAILED",
        )

    def test_a_run_that_never_finalized_can_still_fail(self):
        """The abort path keeps working; only the verdict was taken away.

        A Run that dies in PREPARING or RUNNING is aborted, not judged.
        `finalize_run` requires FINALIZING, so it has no route to this case —
        refusing FAILED outright would have removed a capability rather than
        redirecting one.
        """
        run = self._run_at(RunState.RUNNING, key="abort")
        self.assertEqual(
            self.s.advance_run(self.item.id, run.id, RunState.FAILED).state, RunState.FAILED
        )

    def test_running_run_requires_explicit_acknowledgement(self):
        run = self._run_at(RunState.RUNNING, key="live")
        with self.assertRaises(ValueError) as caught:
            self.s.cancel_run(self.item.id, run.id, reason="r", actor="a")
        self.assertIn("acknowledge-active-worker", str(caught.exception))
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.RUNNING)
        self.assertEqual(self._events(), [])

        result = self.s.cancel_run(
            self.item.id, run.id, reason="r", actor="a", acknowledge_active_worker=True
        )
        self.assertTrue(result["runtime_may_still_be_active"])
        self.assertTrue(self._events()[0]["payload"]["runtime_may_still_be_active"])

    def test_dry_run_reports_the_plan_without_writing(self):
        run = self._run_at(RunState.PREPARING, key="dry")
        before = self.s.item_events(self.item.id)
        result = self.s.cancel_run(self.item.id, run.id, reason="r", actor="a",
                                   reason_code=RunCancelReason.SCOPE_CHANGED, dry_run=True)
        self.assertTrue(result["dry_run"])
        self.assertEqual((result["from"], result["to"]), ("PREPARING", "CANCELLED"))
        self.assertEqual(result["reason_code"], "SCOPE_CHANGED")
        self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.PREPARING)
        self.assertEqual(self.s.item_events(self.item.id), before)

    # ── no cascade ─────────────────────────────────────────────────────────

    def test_cancelling_a_run_leaves_the_item_and_artifacts_alone(self):
        run = self._run_at(RunState.RUNNING, key="nocascade")
        for state in ("AVAILABLE", "READY", "ACTIVE"):
            self.s.transition_item(self.item.id, state, note="in flight")
        artifact = Path(run.artifact_dir) / "keep.txt"
        artifact.write_text("keep me")
        listing = sorted(p.name for p in Path(run.artifact_dir).iterdir())

        self.s.cancel_run(self.item.id, run.id, reason="r", actor="a",
                          acknowledge_active_worker=True)

        self.assertEqual(self.s.get_item(self.item.id).state.value, "ACTIVE")
        self.assertTrue(artifact.exists())
        self.assertEqual(sorted(p.name for p in Path(run.artifact_dir).iterdir()), listing)
        self.assertEqual(self.s.list_deliverables(self.item.id), [])

    # ── mutation: prove the guards above actually discriminate ─────────────

    def test_audit_guard_catches_an_implementation_that_skips_the_audit_record(self):
        run = self._run_at(RunState.PREPARING, key="mut-audit")

        def bypassing_cancel(self, item_id, run_id, *, reason, actor, **options):
            with self.transaction(immediate=True) as c:
                c.execute(
                    "UPDATE runs SET state='CANCELLED',ended_at=? WHERE item_id=? AND run_id=?",
                    (now_iso(), item_id, run_id),
                )
            return {"to": "CANCELLED"}

        with patch.object(ServerOSStore, "cancel_run", bypassing_cancel):
            self.s.cancel_run(self.item.id, run.id, reason="r", actor="a")
            self.assertEqual(self.s.get_run(self.item.id, run.id).state, RunState.CANCELLED)
            with self.assertRaises(AssertionError):
                self._assert_run_termination_is_audited(run.id)

    def test_state_machine_guard_catches_an_implementation_that_ignores_it(self):
        run = self._run_at(RunState.FINALIZING, key="mut-sm")
        self.s.finalize_run(
            self.item.id, run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision="r", dirty=False,
        )

        def lawless_cancel(self, item_id, run_id, *, reason, actor, target="CANCELLED", **options):
            with self.transaction(immediate=True) as c:
                at = now_iso()
                c.execute(
                    "UPDATE runs SET state=?,ended_at=? WHERE item_id=? AND run_id=?",
                    (str(target), at, item_id, run_id),
                )
                self._event(c, item_id, "run_terminated",
                            {"run": run_id, "to": str(target), "reason": reason, "actor": actor}, at=at)
            return {"to": str(target)}

        with patch.object(ServerOSStore, "cancel_run", lawless_cancel):
            with self.assertRaises(AssertionError):
                self._assert_state_machine_still_decides(run.id)


class RunCancelCliTests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory()
        self.root = Path(self.td.name)
        self.state = self.root / "state"
        self.artifacts = self.root / "artifacts"
        self.repo = Path(__file__).resolve().parents[1]
        self.env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(self.repo)}

    def tearDown(self):
        self.td.cleanup()

    def _cli(self, *args):
        return subprocess.run(
            [sys.executable, "-m", "workengine", "--state-dir", str(self.state),
             "--artifacts-root", str(self.artifacts), *args],
            cwd=self.repo, env=self.env, capture_output=True, text=True,
        )

    def test_cli_round_trip_and_second_cancellation(self):
        production = Path(default_state_dir()) / "serveros.db"
        production_before = production.stat().st_mtime_ns if production.exists() else None

        self.assertEqual(self._cli("item", "create", "--project", "demo", "--prefix", "DEM",
                                   "--title", "fixture", "--kind", "test").returncode, 0)
        self.assertEqual(self._cli("activity", "add", "DEM-0001", "test", "--kind", "test").returncode, 0)
        begun = self._cli("run", "begin", "DEM-0001", "test", "--worker", "fixture",
                          "--idempotency-key", "fixture-key")
        self.assertEqual(begun.returncode, 0, begun.stderr)
        self.assertEqual(json.loads(begun.stdout)["state"], "ALLOCATED")

        planned = self._cli("run", "cancel", "DEM-0001", "RUN-0001", "--reason", "fixture teardown",
                            "--actor", "test-runner", "--dry-run")
        self.assertEqual(planned.returncode, 0, planned.stderr)
        planned_payload = json.loads(planned.stdout)
        self.assertTrue(planned_payload["termination"]["dry_run"])
        self.assertEqual(planned_payload["state"], "ALLOCATED")

        cancelled = self._cli("run", "cancel", "DEM-0001", "RUN-0001", "--reason", "fixture teardown",
                              "--actor", "test-runner", "--reason-code", "SCOPE_CHANGED")
        self.assertEqual(cancelled.returncode, 0, cancelled.stderr)
        payload = json.loads(cancelled.stdout)
        self.assertEqual(payload["state"], "CANCELLED")
        self.assertEqual(payload["termination"]["reason_code"], "SCOPE_CHANGED")
        self.assertEqual(payload["termination"]["actor"], "test-runner")

        shown = json.loads(self._cli("run", "show", "DEM-0001", "RUN-0001").stdout)
        self.assertEqual(shown["state"], "CANCELLED")
        events = json.loads(self._cli("item", "show", "DEM-0001").stdout)["events"]
        terminated = [e for e in events if e["event_type"] == "run_terminated"]
        self.assertEqual(len(terminated), 1)
        self.assertEqual(terminated[0]["payload"]["reason"], "fixture teardown")

        again = self._cli("run", "cancel", "DEM-0001", "RUN-0001", "--reason", "twice", "--actor", "test-runner")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue(json.loads(again.stdout)["termination"]["already_terminal"])
        self.assertEqual(
            len([e for e in json.loads(self._cli("item", "show", "DEM-0001").stdout)["events"]
                 if e["event_type"] == "run_terminated"]),
            1,
        )

        # The fixture must never have reached the production ledger.
        self.assertTrue((self.state / "serveros.db").exists())
        if production_before is not None:
            self.assertEqual(production.stat().st_mtime_ns, production_before)

    def test_cli_exposes_cancel_and_abandon_and_refuses_bulk_or_reasonless_forms(self):
        parser = build_parser()
        cancel = parser.parse_args(["run", "cancel", "DEM-0001", "RUN-0001", "--reason", "r", "--actor", "a"])
        abandon = parser.parse_args(["run", "abandon", "DEM-0001", "RUN-0001", "--reason", "r", "--actor", "a"])
        self.assertEqual((cancel.action, abandon.action), ("cancel", "abandon"))
        self.assertEqual(cancel.to, "CANCELLED")
        self.assertFalse(hasattr(abandon, "to"))
        for argv in (
            ["run", "cancel", "DEM-0001", "RUN-0001", "--actor", "a"],
            ["run", "cancel", "DEM-0001", "RUN-0001", "--reason", "r"],
        ):
            with self.assertRaises(SystemExit):
                parser.parse_args(argv)
        rejected = self._cli("run", "abandon", "DEM-0001", "RUN-0001", "--reason", "r", "--actor", "a")
        self.assertEqual(rejected.returncode, 2)


if __name__ == "__main__":
    unittest.main()
