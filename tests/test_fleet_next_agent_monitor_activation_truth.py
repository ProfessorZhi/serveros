#!/usr/bin/env python3
"""What the activation wrapper is allowed to say about the batch it ran.

WHY THIS FILE EXISTS
--------------------
#488 shipped a wrapper that runs N monitor ticks and then reports. #495 measured
three ways that report lost the monitor's answer:

1. the tick's `state`, `exit`, `why` and `health` were dropped on the floor
   (`activation.py` kept only `backlog` and `unknown_receipts`);
2. every activation ended in `BOUNDED_STOP` / exit 0, so `BLOCKED_INPUT` (exit 3)
   and `UNCONFIRMED` (exit 4) reached a supervisor as success — "the process
   finished" was written where "the batch settled" belonged;
3. the CLI passed neither a clock nor a cursor age, so `health.last_tick` was
   always `0.0` and `health.stale` was always `false`, whatever the monitor said.

The rule this file pins is #478's, applied to the wrapper: **a stop is not a
completion**, and nothing unmeasured may be reported as healthy. So:

* a tick's control word is carried through, never merged with the wrapper's own;
* `BOUNDED_STOP` is emitted only for a tick that is *green*, and then it is the
  only word available with `completed: true`;
* an unreadable or absent answer is `UNRECOGNISED`, which is not a status;
* `health.stale` is `null` when nothing measured staleness — never `false`.

Red→Green: every test below fails against #488 at `009662f3`. The two tests that
had to be *rewritten* (`..._activation.py`, `..._activation_batch.py`) encoded
the old flattening and are changed with it; the reason is in the PR body.
"""
from __future__ import annotations

import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import time
import types
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = (REPO / "integrations" / "fleet-next-agent-monitor-activation"
               / "activation.py")
MODULE_NAME = "serveros_next_monitor_activation_truth"


def _load():
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="next-monitor-truth-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.m = _load()

    def activation(self, **overrides):
        """Absolute paths on *any* host: a POSIX literal is not absolute on win32."""
        fields = dict(
            legacy_root=str(self.tmp / "legacy"),
            next_root=str(self.tmp / "next"),
            state=str(self.tmp / "next" / "state"),
            lock=str(self.tmp / "next" / "monitor.lock"),
            port=3241, legacy_port=3231,
            service_name="serveros-next-agent-monitor",
            legacy_service_name="serveros-fleet-node-connector",
            plugin_id="serveros-fleet-next-ingress",
            legacy_plugin_id="serveros-fleet-ingress",
            ticks=1)
        fields.update(overrides)
        return self.m.Activation(**fields)

    def run_one(self, row, **overrides):
        return self.m.run_bounded(
            self.activation(**overrides), monitor_entry=lambda: row,
            legacy_read=lambda: True, next_write=lambda: False,
            clock=lambda: 0.0)


class TheTickVerdictIsCarriedNotSummarised(Base):
    def test_a_settled_tick_is_the_only_bounded_stop(self):
        result = self.run_one({"state": "SETTLED", "why": "every verdict terminal"})
        self.assertEqual(result["state"], self.m.BOUNDED_STOP)
        self.assertEqual(result["exit"], self.m.EXIT_OK)
        self.assertIs(result["completed"], True)
        self.assertEqual(result["ticks"], 1)

    def test_a_blocked_tick_is_reported_as_blocked_input_with_exit_three(self):
        """#495's P0: a dispatch no run ever reached is not "bounded stop"."""
        result = self.run_one({
            "state": "BLOCKED_INPUT", "exit": 3, "backlog": 0,
            "why": "BLOCKED_RUNTIME_ENTRY: no config"})
        self.assertEqual(result["state"], "BLOCKED_INPUT")
        self.assertEqual(result["exit"], 3)
        self.assertIs(result["completed"], False)
        self.assertIn("BLOCKED_RUNTIME_ENTRY", result["why"])
        self.assertNotEqual(result["state"], self.m.BOUNDED_STOP)

    def test_an_unconfirmed_tick_is_reported_as_unconfirmed_with_exit_four(self):
        result = self.run_one({
            "state": "UNCONFIRMED", "exit": 4, "unknown_receipts": 1,
            "why": "the summary receipt is UNKNOWN"})
        self.assertEqual(result["state"], "UNCONFIRMED")
        self.assertEqual(result["exit"], 4)
        self.assertIs(result["completed"], False)

    def test_a_ticked_tick_is_progress_and_not_a_bounded_stop(self):
        result = self.run_one({"state": "TICKED", "exit": 0, "backlog": 3})
        self.assertEqual(result["state"], "TICKED")
        self.assertIs(result["completed"], False)
        self.assertNotEqual(result["state"], self.m.BOUNDED_STOP,
                            "a batch still in flight finished the *process*, "
                            "not the batch")

    def test_a_row_with_no_state_is_unrecognised_and_never_a_success(self):
        """A row that names no verdict cannot be read as a finished one."""
        result = self.run_one({"row": "ok"})
        self.assertEqual(result["state"], self.m.UNRECOGNISED)
        self.assertIs(result["completed"], False)
        self.assertNotEqual(result["exit"], 0)
        self.assertIn("no state", result["why"])

    def test_an_unknown_state_word_is_unrecognised_not_probably_fine(self):
        result = self.run_one({"state": "DONE", "why": "looks finished"})
        self.assertEqual(result["state"], self.m.UNRECOGNISED)
        self.assertIn("DONE", result["why"])
        self.assertNotEqual(result["exit"], 0)

    def test_a_state_that_is_not_even_a_string_is_unrecognised_not_a_crash(self):
        """A producer bug must not take the supervisor's process down with it."""
        for bad in (["SETTLED"], {"SETTLED": True}, 1):
            with self.subTest(state=bad):
                result = self.run_one({"state": bad})
                self.assertEqual(result["state"], self.m.UNRECOGNISED)
                self.assertNotEqual(result["exit"], 0)

    def test_a_blocked_word_with_a_zero_exit_cannot_become_a_zero(self):
        """A producer's 0 is not allowed to launder a blocked verdict."""
        result = self.run_one({"state": "BLOCKED_INPUT", "exit": 0})
        self.assertEqual(result["state"], "BLOCKED_INPUT")
        self.assertEqual(result["exit"], 3)

    def test_a_settled_row_the_producer_calls_not_green_is_not_a_completion(self):
        result = self.run_one({"state": "SETTLED", "green": False,
                               "why": "the summary never reached the provider"})
        self.assertIs(result["completed"], False)
        self.assertEqual(result["state"], "UNCONFIRMED",
                         "terminal facts with no delivered summary are unconfirmed")
        self.assertNotEqual(result["exit"], 0)
        self.assertEqual(result["ticks_report"][0]["state"], "SETTLED",
                         "the producer's own word is still carried verbatim")


class TheTickHealthIsCarriedVerbatim(Base):
    def test_the_producers_health_is_reported_unchanged(self):
        reported = {"last_tick": "2026-10-28T09:00:00+0000",
                    "last_ok": "2026-10-28T09:00:00+0000",
                    "cursor_age": 12.5, "consecutive_failures": 0,
                    "stale": False}
        result = self.run_one({"state": "SETTLED", "health": reported})
        self.assertEqual(result["health"]["reported"], reported)
        self.assertEqual(result["health"]["cursor_age"], 12.5)
        self.assertIs(result["health"]["stale"], False)

    def test_a_producer_stale_flag_survives_a_fresh_wrapper_clock(self):
        """#493 computes staleness from its own state; the wrapper may not erase it."""
        result = self.run_one({"state": "UNCONFIRMED", "health": {
            "last_tick": "2026-10-28T07:00:00+0000", "last_ok": None,
            "cursor_age": 7200.0, "consecutive_failures": 1, "stale": True}})
        self.assertIs(result["health"]["stale"], True)
        self.assertEqual([w["code"] for w in result["warnings"]], ["STALE_MONITOR"])

    def test_an_unmeasured_staleness_is_unknown_not_false(self):
        """The bug #495 measured: no age in, `stale: false` out."""
        result = self.run_one({"state": "SETTLED"})
        self.assertIsNone(result["health"]["stale"])
        self.assertIsNone(result["health"]["cursor_age"])
        self.assertNotIn("STALE_MONITOR", [w["code"] for w in result["warnings"]])

    def test_backlog_and_unknown_receipts_reach_health_and_warn(self):
        result = self.run_one({"state": "UNCONFIRMED", "backlog": 7,
                               "unknown_receipts": 2})
        self.assertEqual(result["health"]["backlog"], 7)
        self.assertEqual(result["health"]["unknown_receipts"], 2)
        codes = [w["code"] for w in result["warnings"]]
        self.assertIn("BACKLOG", codes)
        self.assertIn("UNKNOWN_RECEIPTS", codes)

    def test_a_stale_cursor_the_wrapper_measured_is_not_a_completion(self):
        result = self.m.run_bounded(
            self.activation(), monitor_entry=lambda: {"state": "SETTLED"},
            legacy_read=lambda: True, next_write=lambda: False,
            clock=lambda: 0.0, cursor_age_seconds=7200.0)
        self.assertIs(result["health"]["stale"], True)
        self.assertIs(result["completed"], False)
        self.assertEqual(result["state"], "UNCONFIRMED")


class TheSchemaIsClosed(Base):
    SCENARIOS = (
        {"state": "SETTLED"},
        {"state": "BLOCKED_INPUT", "exit": 3, "health": {"stale": True,
                                                        "cursor_age": 9000.0}},
        {"row": "ok"},
        {"state": "DONE"},
        {"state": "UNCONFIRMED", "backlog": 1, "unknown_receipts": 1,
         "health": {"stale": False, "cursor_age": 1.0, "last_ok": None,
                    "last_tick": "t", "consecutive_failures": 0}},
    )

    def test_every_result_is_sealed_to_the_closed_schema(self):
        for row in self.SCENARIOS:
            with self.subTest(row=row):
                result = self.run_one(row, ticks=2)
                self.assertEqual(set(result), set(self.m.RESULT_KEYS))
                self.assertIn(result["state"], self.m.RESULT_STATES)
                self.assertEqual(set(result["health"]), set(self.m.HEALTH_KEYS))
                for record in result["ticks_report"]:
                    self.assertEqual(set(record), set(self.m.TICK_RECORD_KEYS))
                    self.assertIn(record["state"], self.m.TICK_STATES)

    def test_ticks_report_keeps_one_closable_record_per_tick(self):
        result = self.run_one({"state": "TICKED"}, ticks=3)
        self.assertEqual(result["ticks"], 3)
        self.assertEqual([r["index"] for r in result["ticks_report"]], [0, 1, 2])
        self.assertEqual(result["state"], "TICKED")

    def test_a_refusal_before_any_tick_reports_no_health_at_all(self):
        """`{}` would be "nothing wrong found"; the truth is "nothing measured"."""
        result = self.m.run_bounded(
            self.activation(), monitor_entry="does.not.exist:monitor",
            legacy_read=lambda: True, next_write=lambda: False)
        self.assertEqual(result["state"], self.m.BLOCKED_UPSTREAM_ENTRY)
        self.assertIsNone(result["health"])
        self.assertIs(result["completed"], False)
        self.assertEqual(result["ticks_report"], [])


class TheRealProducersDocumentShapes(Base):
    """#493's two answers, copied key for key, in the shape `runtime.tick` returns.

    The multi-process regression against the real `runtime:tick` lives in
    `..._runtime_tick.py`; it can only run where #493 is composed and the host is
    POSIX. These literals are the same documents, so the wrapper's handling of
    them is pinned on every host — and a drift in #493's shape shows up here as a
    missing key rather than as a silent `None`.
    """

    #: `runtime._blocked("BLOCKED_RUNTIME_ENTRY", …, exit_code=3, state=…)`.
    BLOCKED = {
        "schema": "serveros/fleet-next-agent-monitor-tick.v1",
        "state": "BLOCKED_INPUT", "exit": 3,
        "why": "BLOCKED_RUNTIME_ENTRY",
        "detail": "FLEET_NEXT_AGENT_MONITOR_CONFIG and FLEET_NEXT_AGENT_MONITOR_STATE "
                  "must both name absolute paths; a zero-argument tick has nowhere "
                  "else to read its configuration",
        "watches": [], "counts": {}, "dispatched": 0, "delivered": 0, "duplicate": 0,
        "unknown_receipts": 0, "backlog": 0, "health": {}, "warnings": [],
    }

    #: `core.MonitorCore._tick` for one watch that is still running.
    TICKED = {
        "schema": "serveros/fleet-next-agent-monitor-tick.v1",
        "state": "TICKED", "exit": 0, "boot_epoch": "epoch-1",
        "recovered_from": None, "observed_at": "2026-10-28T09:00:00+0000",
        "reads": 1, "counts": {"RUNNING": 1}, "dispatched": 0, "delivered": 0,
        "duplicate": 0, "unknown_receipts": 0, "backlog": 1,
        "watches": [{"key": "run|dev-1|sess-1|run-1", "view": "run",
                     "status": "RUNNING", "reason": None, "dispatched": False,
                     "receipt": None}],
        "dispatched_any": False,
        "health": {"last_tick": "2026-10-28T09:00:00+0000",
                   "last_ok": "2026-10-28T09:00:00+0000",
                   "cursor_age": 0.4, "consecutive_failures": 0, "stale": False},
        "warnings": [{"code": "BACKLOG", "status": None, "reason": None,
                      "why": "1 watch(es) have no terminal verdict"}],
    }

    def test_the_blocked_document_is_forwarded_with_its_own_words(self):
        result = self.run_one(self.BLOCKED)
        self.assertEqual(result["state"], "BLOCKED_INPUT")
        self.assertEqual(result["exit"], 3)
        self.assertIs(result["completed"], False)
        self.assertEqual(result["why"], "BLOCKED_RUNTIME_ENTRY")
        self.assertEqual(result["health"]["reported"], {},
                         "#493 said `health: {}`; the wrapper may not rewrite it")
        self.assertIsNone(result["health"]["stale"],
                          "an empty health block is not a clean bill of health")

    def test_the_running_document_keeps_the_monitor_s_own_vitals(self):
        result = self.run_one(self.TICKED)
        self.assertEqual(result["state"], "TICKED")
        self.assertIs(result["completed"], False)
        self.assertEqual(result["health"]["reported"], self.TICKED["health"])
        self.assertEqual(result["health"]["cursor_age"], 0.4)
        self.assertIs(result["health"]["stale"], False)
        self.assertEqual(result["health"]["backlog"], 1)
        self.assertEqual(result["warnings"], self.TICKED["warnings"],
                         "the monitor's own warning is not the wrapper's to rewrite")


class TheCliPassesWhatItMeasured(Base):
    """`activation.py:310-312` dropped both `clock` and the cursor age."""

    def _argv(self, **overrides):
        values = dict(
            legacy_root=str(self.tmp / "legacy"),
            next_root=str(self.tmp / "next"),
            state=str(self.tmp / "next" / "state"),
            lock=str(self.tmp / "next" / "monitor.lock"),
            port="3241", legacy_port="3231",
            service_name="serveros-next-agent-monitor",
            legacy_service_name="serveros-fleet-node-connector",
            plugin_id="serveros-fleet-next-ingress",
            legacy_plugin_id="serveros-fleet-ingress", ticks="1",
            monitor_entry=MODULE_NAME + ":probe_true",
            legacy_read=MODULE_NAME + ":probe_true",
            next_write=MODULE_NAME + ":probe_false")
        values.update(overrides)
        argv = []
        for key, value in values.items():
            argv.extend(["--" + key.replace("_", "-"), str(value)])
        return argv

    def _cli(self, tick, **overrides) -> tuple[int, dict]:
        module = types.ModuleType(MODULE_NAME + "_cli_tick")
        module.tick = tick
        module.probe_true = lambda: True
        module.probe_false = lambda: False
        sys.modules[module.__name__] = module
        buffer = io.StringIO()
        previous = sys.stdout
        sys.stdout = buffer
        try:
            code = self.m.main(self._argv(
                monitor_entry=module.__name__ + ":tick",
                legacy_read=module.__name__ + ":probe_true",
                next_write=module.__name__ + ":probe_false", **overrides))
        finally:
            sys.stdout = previous
            sys.modules.pop(module.__name__, None)
        return code, json.loads(buffer.getvalue())

    def test_the_cli_timestamps_its_own_tick_with_a_real_clock(self):
        before = time.time()
        _code, document = self._cli(lambda: {"state": "SETTLED"})
        self.assertGreater(document["health"]["last_tick"], 1e9)
        self.assertGreaterEqual(document["health"]["last_tick"], before,
                                "the CLI passed no clock, so last_tick was 0.0")

    def test_the_cli_can_pass_a_cursor_age_so_staleness_is_measured(self):
        _code, document = self._cli(lambda: {"state": "UNCONFIRMED"},
                                    cursor_age_seconds="7200")
        self.assertIs(document["health"]["stale"], True)
        self.assertEqual([w["code"] for w in document["warnings"]],
                         ["STALE_MONITOR"])

    def test_the_cli_exits_three_when_the_monitor_is_blocked(self):
        code, document = self._cli(lambda: {"state": "BLOCKED_INPUT", "exit": 3,
                                            "why": "BLOCKED_RUNTIME_ENTRY"})
        self.assertEqual(code, 3)
        self.assertEqual(document["state"], "BLOCKED_INPUT")
        self.assertEqual(document["exit"], 3)

    def test_the_cli_exits_zero_only_for_a_bounded_stop(self):
        code, document = self._cli(lambda: {"state": "SETTLED"})
        self.assertEqual(code, 0)
        self.assertEqual(document["state"], self.m.BOUNDED_STOP)
        self.assertIs(document["completed"], True)


if __name__ == "__main__":
    unittest.main()
