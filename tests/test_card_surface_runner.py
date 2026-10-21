#!/usr/bin/env python3
"""The shadow round: policy → native renderer → INJECTED fake transport.

EVERY test here is offline. The sender is a `RecordingSender` that opens no
socket; the model is built in-memory; the clock is a string. What is proven is
the WIRING — that a decision becomes exactly one rendered card handed to the
transport, that the cursors move only on a DELIVERED receipt, and that a failure
leaves the same live message to retry rather than minting a second one.

WHAT IS FAKE AND WHAT IS REAL
    fake   the sender, the model, the instant
    real   `card_period`'s native CardKit renderer, `card_surface`'s policy,
           and the store's fail-closed commit rules
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


runner_mod = _load("serveros_runtime_card_surface_runner",
                   ROOT / "runtime" / "card_surface_runner.py")
surface = _load("serveros_runtime_card_surface", ROOT / "runtime" / "card_surface.py")
card_period = _load("serveros_runtime_card_period", ROOT / "runtime" / "card_period.py")
monitor = _load("serveros_runtime_fleet_monitor_card",
                ROOT / "runtime" / "fleet_monitor_card.py")
alert_digest = _load("serveros_runtime_alert_digest", ROOT / "runtime" / "alert_digest.py")


def _at(hhmm, day="2026-10-14"):
    return f"{day}T{hhmm}:00+08:00"


NOW = _at("17:00")
LIVE_ID = "om_live-1"   # what RecordingSender derives from remote_id="om_live"


def _reading(label, value, *, severity="OK"):
    return monitor.reading(label, value, observed_at=NOW, severity=severity)


def _model(*, tokyo=(), extras=()):
    devices = [{"device_ref": "device:tokyo-shared-dev-01",
                "display_name": "东京共享开发机", "platform": "linux",
                "reachability": {"state": monitor.STATE_KNOWN, "observed_at": NOW},
                "readings": list(tokyo), "accounts": []}] + list(extras)
    return monitor.monitor_model(devices=devices, services=(), now=NOW)


def _unknown(ref, name):
    return {"device_ref": ref, "display_name": name, "platform": "windows",
            "reachability": {"state": monitor.STATE_UNKNOWN, "reason": "无采集"},
            "readings": [], "accounts": []}


def _source(model, rows=()):
    return lambda *, at: (model, list(rows))


def _period(ref="CN-Test/2026-10-14#morning", *, in_period=True, label="上午"):
    return {"period_ref": ref if in_period else None, "label": label,
            "day_ref": "CN-Test/2026-10-14", "in_period": in_period}


class TheEventAdapter(unittest.TestCase):
    ROW = {"key": "alert:host:mem", "alert_ref": "alert:abc", "kind": "NEW",
           "severity": "CRITICAL", "state": "open",
           "first_seen": "2026-10-14T09:00:00+08:00", "resolved_at": None,
           "hold_kind": "above_floor"}

    def test_a_digest_row_becomes_a_surface_event_with_a_real_identity(self):
        event = runner_mod.event_from_digest_row(self.ROW)
        self.assertEqual(event["kind"], "NEW")
        self.assertEqual(event["episode"], alert_digest.episode_of(self.ROW))
        self.assertTrue(event["version"])

    def test_a_row_without_an_identity_is_unverified(self):
        self.assertIsNone(runner_mod.event_from_digest_row({"kind": "NEW"}))

    def test_an_unknown_kind_is_unverified(self):
        self.assertIsNone(runner_mod.event_from_digest_row(
            {**self.ROW, "kind": "SOMETHING_ELSE"}))

    def test_the_same_row_dedupes_and_a_change_does_not(self):
        self.assertEqual(runner_mod.digest_version(self.ROW),
                         runner_mod.digest_version(dict(self.ROW)))
        self.assertNotEqual(runner_mod.digest_version(self.ROW),
                            runner_mod.digest_version({**self.ROW, "severity": "ATTENTION"}))

    def test_reopened_and_updated_map_into_the_surface_vocabulary(self):
        self.assertEqual(runner_mod.event_from_digest_row(
            {**self.ROW, "kind": "REOPENED"})["kind"], "ESCALATED")
        self.assertEqual(runner_mod.event_from_digest_row(
            {**self.ROW, "kind": "UPDATED"})["kind"], "HANDLING")

    def test_a_batch_separates_events_from_unverified_rows(self):
        events, unverified = runner_mod.events_from_digest([self.ROW, {"kind": "NEW"}])
        self.assertEqual(len(events), 1)
        self.assertEqual(unverified, 1)


class TheShadowRound(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = surface.SurfaceStore(Path(self.tmp.name) / "cards" / "surfaces.json")
        self.model = _model(tokyo=[_reading("CPU", "128 逻辑核"),
                                   _reading("内存", "32.6%")],
                            extras=[_unknown("device:work-fj", "FJPCOS 工作站")])
        self.sender = runner_mod.RecordingSender(remote_id="om_live")

    def _runner(self, sender=None):
        return runner_mod.ShadowRunner(
            store=self.store, sender=sender or self.sender,
            model_source=_source(self.model),
            period_source=lambda *, at: _period())

    def _live_calls(self):
        """Transport calls that touched the LIVE message (the report has its own)."""
        return [c for c in self.sender.calls if c.get("remote_id") == LIVE_ID]

    def test_a_create_round_renders_one_card_and_binds_the_message(self):
        trace = self._runner().tick(at=NOW)
        self.assertEqual(trace["live"]["action"], surface.CREATE)
        self.assertTrue(trace["live"]["committed"])
        self.assertEqual(self.store.summary()["live"]["remote_id"], LIVE_ID)
        self.assertEqual(self._live_calls()[0]["op"], "send_card")
        # the same round also opens the period's REPORT on its OWN message
        report_id = self.store.read()["reports"][0]["remote_id"]
        self.assertNotEqual(report_id, LIVE_ID)

    def test_a_failed_create_binds_nothing_and_retries_under_the_same_key(self):
        # the LIVE create is this sender's FIRST call
        self.sender = runner_mod.RecordingSender(remote_id="om_live",
                                                 outcome="FAILED", fail_first=1)
        runner = self._runner(sender=self.sender)
        first = runner.tick(at=NOW)
        self.assertFalse(first["live"]["committed"])
        # nothing was BOUND: no remote_id, only the retained intent
        self.assertIsNone((self.store.read().get("live") or {}).get("remote_id"))
        second = runner.tick(at=_at("17:01"))
        self.assertTrue(second["live"]["committed"])
        keys = [c["idempotency_key"] for c in self.sender.calls
                if c.get("idempotency_key", "").startswith("card-surface-live-create")]
        # BOTH creates carry the SAME key: the service dedupes, so a retry after
        # an unacknowledged send cannot become a second live card.
        self.assertEqual(len(keys), 2)
        self.assertEqual(keys[0], keys[1])

    def test_an_event_at_17_29_does_not_swallow_the_17_30_heartbeat(self):
        runner = self._runner()
        runner.tick(at=NOW)                                   # CREATE @17:00
        runner.tick(at=_at("17:29"),
                    events=[{"kind": surface.NEW, "episode": "e1", "version": 1}])
        runner.tick(at=_at("17:30"))                          # the regular slot
        patches = [c for c in self._live_calls() if c["op"] == "update"]
        self.assertEqual(len(patches), 2)                     # 17:29 event + 17:30 slot
        # exactly ONE live message the whole time
        self.assertEqual(len([c for c in self._live_calls() if c["op"] == "send_card"]), 1)

    def test_an_event_and_the_heartbeat_in_one_round_send_once(self):
        runner = self._runner()
        runner.tick(at=NOW)
        before = len(self._live_calls())
        trace = runner.tick(at=_at("17:30"),
                            events=[{"kind": surface.ESCALATED, "episode": "e9", "version": 2}])
        self.assertEqual(trace["live"]["reasons"], [surface.EVENT, surface.HEARTBEAT])
        self.assertEqual(len(self._live_calls()) - before, 1)   # ONE patch, coalesced

    def test_a_repeated_event_sends_nothing(self):
        runner = self._runner()
        runner.tick(at=NOW)
        event = {"kind": surface.NEW, "episode": "e1", "version": 1}
        runner.tick(at=_at("17:05"), events=[event])
        after_first = len(self._live_calls())
        runner.tick(at=_at("17:06"), events=[dict(event)])
        self.assertEqual(len(self._live_calls()), after_first)

    def test_two_episodes_of_the_same_kind_are_independent(self):
        runner = self._runner()
        runner.tick(at=NOW)
        runner.tick(at=_at("17:05"),
                    events=[{"kind": surface.NEW, "episode": "a", "version": 1}])
        count_after_a = len(self._live_calls())
        runner.tick(at=_at("17:06"),
                    events=[{"kind": surface.NEW, "episode": "b", "version": 1}])
        self.assertEqual(len(self._live_calls()), count_after_a + 1)

    def test_a_timeout_keeps_the_intent_and_retries_the_same_message(self):
        runner = self._runner()
        runner.tick(at=NOW)                                  # CREATE @17:00
        # a sender whose FIRST call (the 17:30 heartbeat) answers UNKNOWN
        runner.sender = runner_mod.RecordingSender(remote_id="om_live",
                                                   outcome="UNKNOWN", fail_first=1)
        first = runner.tick(at=_at("17:30"))
        self.assertFalse(first["live"]["committed"])
        self.assertTrue(first["live"]["pending_retained"])
        runner.sender = runner_mod.RecordingSender(remote_id="om_live")   # a clean retry
        second = runner.tick(at=_at("17:31"))
        self.assertEqual(second["live"]["action"], surface.PATCH)
        self.assertEqual(second["live"]["receipt"]["state"], "DELIVERED")
        # still exactly ONE live message
        self.assertEqual(len([c for c in self.sender.calls
                              if c.get("remote_id") == LIVE_ID and c["op"] == "send_card"]), 1)


class TheReportRound(unittest.TestCase):
    MORNING = "CN-Test/2026-10-14#morning"

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = surface.SurfaceStore(Path(self.tmp.name) / "cards" / "surfaces.json")
        self.model = _model(tokyo=[_reading("内存", "32.6%")])
        self.sender = runner_mod.RecordingSender(remote_id="om_fake")

    def _runner(self, sender=None, period=None):
        return runner_mod.ShadowRunner(
            store=self.store, sender=sender or self.sender,
            model_source=_source(self.model),
            period_source=(period or (lambda *, at: _period())))

    def test_a_report_is_a_second_message_and_freezes_only_itself(self):
        runner = self._runner()
        runner.tick(at=NOW)                                   # LIVE create + REPORT create
        live_id = self.store.summary()["live"]["remote_id"]
        report_id = self.store.read()["reports"][0]["remote_id"]
        self.assertNotEqual(report_id, live_id)               # two distinct messages
        trace = runner.tick(at=_at("12:00"), closing=self.MORNING)   # FINAL -> acknowledged
        self.assertTrue(trace["report"]["committed"])
        self.assertEqual(self.store.summary()["reports"][0]["phase"], surface.FROZEN)
        self.assertEqual(self.store.summary()["live"]["remote_id"], live_id)
        # the LIVE card still heartbeats after the report froze
        before = len([c for c in self.sender.calls if c.get("remote_id") == live_id])
        runner.tick(at=_at("17:30"))
        self.assertGreater(len([c for c in self.sender.calls
                                if c.get("remote_id") == live_id]), before)

    def test_a_wrong_version_acknowledgement_leaves_the_report_pending(self):
        # The sender echoes the WRONG version on the final patch — the freeze is
        # refused and the report stays FINAL_PENDING (retry the same message).
        sender = runner_mod.RecordingSender(remote_id="om_live", version="report@WRONG")
        runner = self._runner(sender=sender)
        runner.tick(at=NOW)
        trace = runner.tick(at=_at("12:00"), closing=self.MORNING)
        self.assertFalse(trace["report"]["committed"])
        self.assertIn("refused", trace["report"])
        self.assertEqual(self.store.summary()["reports"][0]["phase"], surface.FINAL_PENDING)
        self.assertEqual(self.store.summary()["live"]["remote_id"], LIVE_ID)

    def test_a_failed_report_does_not_block_live(self):
        # call 0 = LIVE create (succeeds), call 1 = the REPORT create (fails)
        sender = runner_mod.RecordingSender(remote_id="om_live",
                                            outcome="FAILED", fail_calls=(1,))
        runner = self._runner(sender=sender)
        runner.tick(at=NOW)
        self.assertEqual(self.store.summary()["live"]["remote_id"], LIVE_ID)
        # the report's CREATE was STAGED before the send and never bound: the
        # durable intent survives, which is what lets a restart resume it
        rows = self.store.summary()["reports"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phase"], surface.CREATE_PENDING)
        self.assertIsNone((self.store.read()["reports"][0]).get("remote_id"))
        before = len([c for c in sender.calls if c.get("remote_id") == LIVE_ID])
        runner.tick(at=_at("17:30"))                            # LIVE still patches
        self.assertGreater(len([c for c in sender.calls
                                if c.get("remote_id") == LIVE_ID]), before)

    def test_a_cross_period_report_is_closed(self):
        runner = self._runner()
        runner.tick(at=NOW)                                   # morning report open
        afternoon = _period("CN-Test/2026-10-14#afternoon", label="下午")
        runner = self._runner(period=lambda *, at: afternoon)
        trace = runner.tick(at=_at("13:00"))
        self.assertEqual(trace["report"]["action"], surface.FINAL)
        self.assertEqual(trace["report"]["period_ref"], self.MORNING)


class TheCrashBoundaries(unittest.TestCase):
    """Crash-at-boundary and stale-window behaviour (the 2026-10-14 P0 review).

    The through-line: a CREATE that has been STAGED but not acknowledged is a
    durable state, not an absence. A restart resumes its own intent under the
    SAME idempotency key; past the send service's window it stops and says so
    rather than minting a second card.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "cards" / "surfaces.json"
        self.store = surface.SurfaceStore(self.path)
        self.model = _model(tokyo=[_reading("内存", "32.6%")])

    def _runner(self, store=None, sender=None):
        return runner_mod.ShadowRunner(
            store=store or self.store,
            sender=sender or runner_mod.RecordingSender(remote_id="om_live"),
            model_source=_source(self.model),
            period_source=lambda *, at: _period())

    def test_a_staged_create_reloads_and_resumes_the_same_key(self):
        bad = runner_mod.RecordingSender(remote_id="om_live", outcome="FAILED",
                                         fail_first=2)
        self._runner(sender=bad).tick(at=NOW)          # both stages fail their sends
        # A RESTART: a brand-new store over the same file must LOAD, not refuse.
        reopened = surface.SurfaceStore(self.path)
        self.assertEqual(reopened.read()["live"]["phase"], surface.CREATE_PENDING)
        decision = reopened.decide_live(at=_at("17:01"))
        self.assertTrue(decision.get("resume"))
        self.assertEqual(decision["idempotency_key"], "card-surface-live-create-0")
        # the resumed send carries the SAME key, so the service dedupes
        good = runner_mod.RecordingSender(remote_id="om_live")
        self._runner(store=reopened, sender=good).tick(at=_at("17:01"))
        self.assertEqual(good.calls[0]["idempotency_key"], "card-surface-live-create-0")
        self.assertEqual(reopened.summary()["live"]["remote_id"], LIVE_ID)

    def test_a_create_pending_past_the_window_is_not_resent(self):
        bad = runner_mod.RecordingSender(outcome="FAILED", fail_first=2)
        self._runner(sender=bad).tick(at=NOW)          # staged at 17:00
        reopened = surface.SurfaceStore(self.path)
        decision = reopened.decide_live(at=_at("18:30"))     # 90 min later
        self.assertEqual(decision["action"], surface.SKIP)
        self.assertEqual(decision["reason"], surface.NEEDS_RECONCILE)
        self.assertEqual(reopened.read()["live"]["phase"], surface.NEEDS_RECONCILE)
        # and a tick sends NOTHING — the same key is no longer deduplicated
        good = runner_mod.RecordingSender(remote_id="om_live")
        trace = self._runner(store=reopened, sender=good).tick(at=_at("18:31"))
        self.assertEqual(trace["transport_calls"], 0)
        self.assertEqual(good.calls, [])

    def test_a_report_create_staged_before_the_send_resumes_after_a_crash(self):
        bad = runner_mod.RecordingSender(outcome="FAILED", fail_first=2)
        self._runner(sender=bad).tick(at=NOW)
        row = self.store.read()["reports"][0]
        self.assertEqual(row["phase"], surface.CREATE_PENDING)   # intent on disk
        self.assertIsNone(row["remote_id"])                      # no id yet
        # restart: the same intent resumes under the same key and binds
        reopened = surface.SurfaceStore(self.path)
        good = runner_mod.RecordingSender(remote_id="om_rep")
        trace = self._runner(store=reopened, sender=good).tick(at=_at("17:05"))
        self.assertTrue(trace["report"]["committed"])
        keys = [c["idempotency_key"] for c in good.calls if c["op"] == "send_card"]
        self.assertIn("card-surface-report-CN-Test/2026-10-14#morning", keys)
        self.assertEqual(reopened.summary()["reports"][0]["phase"], surface.LIVE)
        self.assertTrue(reopened.read()["reports"][0]["remote_id"])

    def test_a_failed_report_patch_does_not_advance_the_confirmed_cursor(self):
        runner = self._runner()
        runner.tick(at=NOW)                                   # LIVE + report bound
        before = self.store.read()["reports"][0]["last_patch_at"]
        # call 0 = the LIVE heartbeat (succeeds), call 1 = the report patch (fails)
        runner.sender = runner_mod.RecordingSender(remote_id="om_live-1",
                                                   outcome="FAILED", fail_calls=(1,))
        trace = runner.tick(at=_at("17:30"))
        self.assertTrue(trace["live"]["committed"])
        self.assertFalse(trace["report"]["committed"])
        row = self.store.read()["reports"][0]
        self.assertEqual(row["last_patch_at"], before)        # cursor did NOT move
        self.assertIsNotNone(row.get("pending_patch"))        # the observation stayed

    def test_an_empty_prepared_version_cannot_freeze_a_report(self):
        runner = self._runner()
        runner.tick(at=NOW)
        ref = "CN-Test/2026-10-14#morning"
        decision = self.store.decide_report(period=_period(), closing=ref, at=_at("12:00"))
        self.store.apply_report(period=_period(), decision=decision, at=_at("12:00"))
        with self.assertRaises(surface.CardSurfaceError):
            self.store.confirm_report(period_ref=ref, at=_at("12:01"),
                                      receipt={"state": "DELIVERED",
                                               "remote_id": self.store.read()["reports"][0]["remote_id"]})

    def test_an_unresolved_pending_is_not_overwritten(self):
        first = {"action": surface.CREATE, "slot": 1, "events": []}
        self.store.stage_live(decision=first, at=NOW, idempotency_key="k1",
                              recipient="user:ou_x")
        with self.assertRaises(surface.CardSurfaceError):
            self.store.stage_live(decision=first, at=_at("17:01"), idempotency_key="k2",
                                  recipient="user:ou_x")
        # the SAME key is a legitimate retry of the same intent
        self.store.stage_live(decision=first, at=_at("17:01"), idempotency_key="k1",
                              recipient="user:ou_x")

    def test_a_reentrant_tick_is_refused(self):
        runner = self._runner()

        class Reentrant(runner_mod.RecordingSender):
            def send_card(self, card, **kwargs):
                runner.tick(at=NOW)                      # re-enter mid-round
                return super().send_card(card, **kwargs)

        runner.sender = Reentrant(remote_id="om_live")
        with self.assertRaises(runner_mod.RunnerError):
            runner.tick(at=NOW)
        self.assertFalse(runner._busy)                   # the guard released

    def test_a_synthetic_receipt_is_labelled_and_never_remote(self):
        runner = self._runner()
        runner.tick(at=NOW)
        self.assertEqual(self.store.read()["live"]["evidence"],
                         surface.EVIDENCE_SYNTHETIC)
        self.assertEqual(runner.evidence, surface.EVIDENCE_SYNTHETIC)

    def test_the_heartbeat_still_behaves_across_the_slot(self):
        # a regression guard: none of the fail-closed work moved the cadence
        runner = self._runner()
        runner.tick(at=NOW)
        runner.tick(at=_at("17:29"),
                    events=[{"kind": surface.NEW, "episode": "e1", "version": 1}])
        before = len([c for c in runner.sender.calls if c.get("remote_id") == LIVE_ID])
        runner.tick(at=_at("17:30"))
        after = len([c for c in runner.sender.calls if c.get("remote_id") == LIVE_ID])
        self.assertEqual(after - before, 1)              # the 17:30 slot still fires

    def test_the_native_card_is_unchanged(self):
        card = card_period.render_period_card(
            _model(tokyo=[_reading("CPU", "128 逻辑核"), _reading("内存", "32.6%"),
                          _reading("交换区", "89.2%"), _reading("磁盘 /", "9.5%")]),
            test_mark="[灰度测试]")
        rows = [e for e in card["body"]["elements"] if e.get("tag") == "column_set"]
        self.assertEqual([len(r["columns"]) for r in rows], [2, 2])
        tags = []
        monitor._walk_tags(card["body"], tags)
        self.assertNotIn("chart", tags)
        self.assertNotIn("table", tags)
        self.assertEqual(surface.SURFACES, ("LIVE", "REPORT"))


class ItTouchesNothingReal(unittest.TestCase):
    def test_the_runner_never_imports_the_live_sender(self):
        source = (ROOT / "runtime" / "card_surface_runner.py").read_text(encoding="utf-8")
        for forbidden in ("LarkMessageSender", "lark-cli", "import subprocess",
                          "message_transport.sender("):
            self.assertNotIn(forbidden, source)

    def test_the_preview_sender_refuses_to_carry_anything(self):
        sender = runner_mod.PreviewSender()
        with self.assertRaises(runner_mod.RunnerError):
            sender.send_card({"schema": "2.0"}, idempotency_key="x")
        with self.assertRaises(runner_mod.RunnerError):
            sender.update({"message_id": "om_x"})

    def test_preview_decides_and_renders_without_touching_state_or_transport(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        store = surface.SurfaceStore(Path(tmp.name) / "surfaces.json")
        runner = runner_mod.ShadowRunner(
            store=store, sender=runner_mod.PreviewSender(),
            model_source=_source(_model(tokyo=[_reading("内存", "32.6%")])),
            period_source=lambda *, at: _period())
        trace = runner.preview(at=NOW)
        self.assertFalse(trace["store_touched"])
        self.assertEqual(trace["transport_calls"], 0)
        self.assertIsNone(store.read()["live"])              # nothing was written
        self.assertEqual(trace["components"]["live"]["schema"], "2.0")
        self.assertEqual(trace["components"]["live"]["kpi_columns_per_row"], [1])
        self.assertFalse(trace["components"]["live"]["has_chart"])

    def test_a_bare_recipient_is_refused(self):
        with self.assertRaises(runner_mod.RunnerError):
            runner_mod.verified_recipient("ou_bare_id")
        self.assertEqual(runner_mod.verified_recipient("user:ou_x"), "user:ou_x")

    def test_the_rendered_cards_use_the_native_kpi_and_no_chart_or_table(self):
        card = card_period.render_period_card(
            _model(tokyo=[_reading("CPU", "128 逻辑核"), _reading("内存", "32.6%"),
                          _reading("交换区", "89.2%"), _reading("磁盘 /", "9.5%")]),
            test_mark="[灰度测试]")
        rows = [e for e in card["body"]["elements"] if e.get("tag") == "column_set"]
        self.assertEqual([len(r["columns"]) for r in rows], [2, 2])
        tags = []
        monitor._walk_tags(card["body"], tags)
        self.assertNotIn("chart", tags)
        self.assertNotIn("table", tags)
        self.assertNotIn("corner_radius", json.dumps(
            [c for r in rows for c in r["columns"]]))

    def test_there_are_two_surfaces_and_no_alert_card(self):
        self.assertEqual(surface.SURFACES, ("LIVE", "REPORT"))
        source = (ROOT / "runtime" / "card_surface_runner.py").read_text(encoding="utf-8")
        self.assertNotIn("CardLedger(", source)


if __name__ == "__main__":
    unittest.main()
