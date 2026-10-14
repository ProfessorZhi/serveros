#!/usr/bin/env python3
"""The two surfaces — ONE long-lived LIVE card, per-period REPORT cards.

The 2026-10-14 review's gate these tests hold:

    the heartbeat is a SLOT (epoch // 1800), not «time since the last patch»;
    event identity is (episode/event_id @ version) and is remembered ACROSS
    rounds; cursors move ONLY on a DELIVERED receipt; a report is frozen only on
    an acknowledgement of THAT report; and a report whose period has ended is
    closed even when no period is current.

NO CLOCK AND NO TRANSPORT. Every instant is written down; nothing here contacts
Feishu. All timestamps are `+08:00`, whose offset is a whole number of half-hour
slots, so the slot boundary lands on the local `:00` / `:30`.
"""
from __future__ import annotations

import datetime
import importlib.util
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


surface = _load("serveros_runtime_card_surface", ROOT / "runtime" / "card_surface.py")
delivery = _load("serveros_runtime_card_delivery", ROOT / "runtime" / "card_delivery.py")
card_period = _load("serveros_runtime_card_period", ROOT / "runtime" / "card_period.py")
transport = _load("serveros_runtime_message_transport",
                  ROOT / "runtime" / "message_transport.py")


def _at(hhmm, day="2026-10-14"):
    return f"{day}T{hhmm}:00+08:00"


D = datetime.date
BOUND_AT = _at("17:00")
LIVE_ID = "om_live_1"


def _bound():
    return {"remote_id": LIVE_ID, "at": BOUND_AT}


def _live_state(slot=surface.regular_slot_id(BOUND_AT), consumed=None):
    return {"last_delivered_regular_slot": slot, "consumed_events": consumed or {}}


class TheHeartbeatIsASlot(unittest.TestCase):
    def test_the_slot_boundary_is_the_local_half_hour(self):
        self.assertEqual(surface.regular_slot_id(_at("17:00")),
                         surface.regular_slot_id(_at("17:29")))
        self.assertNotEqual(surface.regular_slot_id(_at("17:29")),
                            surface.regular_slot_id(_at("17:30")))

    def test_a_naive_instant_has_no_slot(self):
        self.assertIsNone(surface.regular_slot_id("2026-10-14T17:00:00"))

    def test_the_heartbeat_fires_when_the_slot_moves(self):
        decision = surface.live_decision(now=_at("17:30"), binding=_bound(),
                                         live_state=_live_state())
        self.assertEqual(decision["action"], surface.PATCH)
        self.assertEqual(decision["reasons"], [surface.HEARTBEAT])

    def test_an_alert_at_17_29_does_not_swallow_the_17_30_heartbeat(self):
        # THE BUG THE REVIEW NAMES: an event patch must not push the regular
        # cadence forward. The slot cursor is separate from the event cursor.
        live = _live_state()
        early = surface.live_decision(now=_at("17:29"), binding=_bound(), live_state=live,
                                      events=[{"kind": surface.NEW, "episode": "e1",
                                               "version": 1}])
        self.assertEqual(early["action"], surface.PATCH)
        self.assertEqual(early["reasons"], [surface.EVENT])
        # the event patch ACKNOWLEDGES the event but NOT the 17:30 slot
        live = {"last_delivered_regular_slot": live["last_delivered_regular_slot"],
                "consumed_events": {"e1@1": {"at": _at("17:29")}}}
        on_time = surface.live_decision(now=_at("17:30"), binding=_bound(), live_state=live)
        self.assertEqual(on_time["action"], surface.PATCH)
        self.assertEqual(on_time["reasons"], [surface.HEARTBEAT])

    def test_an_alarm_and_the_heartbeat_in_one_slot_patch_once(self):
        decision = surface.live_decision(
            now=_at("17:30"), binding=_bound(), live_state=_live_state(),
            events=[{"kind": surface.ESCALATED, "episode": "e9", "version": 2}])
        self.assertEqual(decision["action"], surface.PATCH)
        self.assertEqual(decision["reasons"], [surface.EVENT, surface.HEARTBEAT])
        self.assertTrue(decision["coalesced"])

    def test_a_bad_slot_is_refused_not_read_as_always_due(self):
        with self.assertRaises(surface.CardSurfaceError):
            surface.regular_slot_id(_at("17:00"), slot_seconds=0)


class TheEventIdentity(unittest.TestCase):
    def test_two_episodes_of_the_same_kind_are_two_events(self):
        decision = surface.live_decision(
            now=_at("17:05"), binding=_bound(), live_state=_live_state(),
            events=[{"kind": surface.NEW, "episode": "a", "version": 1},
                    {"kind": surface.NEW, "episode": "b", "version": 1}])
        self.assertEqual(decision["events"], ["a@1", "b@1"])

    def test_a_replayed_event_is_not_fresh(self):
        live = _live_state(consumed={"a@1": {"at": _at("17:01")}})
        decision = surface.live_decision(
            now=_at("17:05"), binding=_bound(), live_state=live,
            events=[{"kind": surface.NEW, "episode": "a", "version": 1}])
        self.assertEqual(decision["events"], [])
        self.assertEqual(decision["action"], surface.SKIP)

    def test_a_new_version_of_the_same_episode_is_fresh(self):
        live = _live_state(consumed={"a@1": {"at": _at("17:01")}})
        decision = surface.live_decision(
            now=_at("17:05"), binding=_bound(), live_state=live,
            events=[{"kind": surface.ESCALATED, "episode": "a", "version": 2}])
        self.assertEqual(decision["events"], ["a@2"])
        self.assertEqual(decision["action"], surface.PATCH)

    def test_an_event_without_identity_is_unverified_and_does_not_patch(self):
        decision = surface.live_decision(
            now=_at("17:05"), binding=_bound(), live_state=_live_state(),
            events=[{"kind": surface.NEW}])
        self.assertEqual(decision["events"], [])
        self.assertEqual(decision["unverified_events"], 1)
        self.assertEqual(decision["action"], surface.SKIP)


class TheCursorsMoveOnlyOnAck(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = surface.SurfaceStore(Path(self.tmp.name) / "cards" / "surfaces.json")

    def _create_live(self):
        decision = self.store.decide_live(at=BOUND_AT)
        self.assertEqual(decision["action"], surface.CREATE)
        self.store.stage_live(decision=decision, at=BOUND_AT)
        self.store.confirm_live(at=BOUND_AT,
                                receipt={"state": delivery.DELIVERED, "remote_id": LIVE_ID})

    def test_create_binds_the_slot_and_the_message(self):
        self._create_live()
        live = self.store.summary()["live"]
        self.assertEqual(live["remote_id"], LIVE_ID)
        self.assertEqual(live["last_delivered_regular_slot"],
                         surface.regular_slot_id(BOUND_AT))

    def test_a_failed_receipt_does_not_advance_the_slot(self):
        self._create_live()
        decision = self.store.decide_live(at=_at("17:30"))
        self.assertEqual(decision["action"], surface.PATCH)
        self.store.stage_live(decision=decision, at=_at("17:30"))
        with self.assertRaises(surface.CardSurfaceError):
            self.store.confirm_live(at=_at("17:30"),
                                    receipt={"state": "FAILED", "remote_id": LIVE_ID})
        self.assertEqual(self.store.summary()["live"]["last_delivered_regular_slot"],
                         surface.regular_slot_id(BOUND_AT))
        # ...and the next tick still wants the SAME heartbeat
        again = self.store.decide_live(at=_at("17:31"))
        self.assertEqual(again["action"], surface.PATCH)
        self.assertIn(surface.HEARTBEAT, again["reasons"])

    def test_an_unknown_receipt_does_not_consume_the_event(self):
        self._create_live()
        event = {"kind": surface.NEW, "episode": "z", "version": 1}
        decision = self.store.decide_live(at=_at("17:05"), events=[event])
        self.store.stage_live(decision=decision, at=_at("17:05"))
        with self.assertRaises(surface.CardSurfaceError):
            self.store.confirm_live(at=_at("17:05"),
                                    receipt={"state": "UNKNOWN", "remote_id": LIVE_ID})
        retry = self.store.decide_live(at=_at("17:06"), events=[event])
        self.assertEqual(retry["events"], ["z@1"])
        self.assertEqual(retry["action"], surface.PATCH)

    def test_a_receipt_for_another_message_is_refused(self):
        self._create_live()
        decision = self.store.decide_live(at=_at("17:30"))
        self.store.stage_live(decision=decision, at=_at("17:30"))
        with self.assertRaises(surface.CardSurfaceError):
            self.store.confirm_live(at=_at("17:30"),
                                    receipt={"state": delivery.DELIVERED,
                                             "remote_id": "om_someone_else"})

    def test_a_delivered_receipt_advances_both_cursors(self):
        self._create_live()
        decision = self.store.decide_live(
            at=_at("17:30"), events=[{"kind": surface.NEW, "episode": "q", "version": 3}])
        self.store.stage_live(decision=decision, at=_at("17:30"))
        self.store.confirm_live(at=_at("17:30"),
                                receipt={"state": delivery.DELIVERED, "remote_id": LIVE_ID})
        live = self.store.summary()["live"]
        self.assertEqual(live["last_delivered_regular_slot"],
                         surface.regular_slot_id(_at("17:30")))
        self.assertEqual(live["consumed_events"], 1)

    def test_timeout_replay_targets_the_same_message_and_never_creates_a_second(self):
        self._create_live()
        decision = self.store.decide_live(at=_at("17:30"))
        self.store.stage_live(decision=decision, at=_at("17:30"))
        self.store.fail_live(reason="timeout")
        replay = self.store.decide_live(at=_at("17:31"))
        self.assertEqual(replay["action"], surface.PATCH)
        self.assertEqual(replay["remote_id"], LIVE_ID)

    def test_rotation_only_when_the_window_is_verified_expired(self):
        decision = surface.live_decision(
            now="2026-10-14T10:00:00+08:00",
            binding={"remote_id": LIVE_ID, "at": "2026-09-01T10:00:00+08:00"})
        self.assertEqual(decision["action"], surface.SUCCESSOR)
        self.assertIn(surface.ROTATION, decision["reasons"])

    def test_a_naive_binding_is_not_rotated_or_patched(self):
        decision = surface.live_decision(
            now=_at("17:30"), binding={"remote_id": LIVE_ID, "at": "nope"},
            live_state=_live_state())
        self.assertEqual(decision["action"], surface.SKIP)
        self.assertEqual(decision["reason"], delivery.IDENTITY_UNDATED)

    def test_the_successor_keeps_the_old_id_and_the_cursors(self):
        binding = surface.successor_binding(
            previous={"remote_id": LIVE_ID, "rotations": 0, "consumed_events": {"a@1": {}},
                      "last_delivered_regular_slot": 42},
            remote_id="om_live_2", at=_at("17:30"))
        self.assertEqual(binding["remote_id"], "om_live_2")
        self.assertEqual(binding["previous_remote_id"], LIVE_ID)
        self.assertEqual(binding["rotations"], 1)
        self.assertEqual(binding["last_delivered_regular_slot"], 42)

    def test_an_unreadable_store_is_refused_rather_than_replaced(self):
        path = Path(self.tmp.name) / "cards" / "surfaces.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(surface.CardSurfaceError):
            surface.SurfaceStore(path)


class TheReportSurface(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = surface.SurfaceStore(Path(self.tmp.name) / "cards" / "surfaces.json")
        self.morning = {"period_ref": "CN-Test/2026-10-14#morning", "label": "上午",
                        "day_ref": "CN-Test/2026-10-14", "in_period": True}
        self.afternoon = {"period_ref": "CN-Test/2026-10-14#afternoon", "label": "下午",
                          "day_ref": "CN-Test/2026-10-14", "in_period": True}
        self.none = {"period_ref": None, "in_period": False}
        self._create_live()

    def _create_live(self):
        decision = self.store.decide_live(at=BOUND_AT)
        self.store.stage_live(decision=decision, at=BOUND_AT)
        self.store.confirm_live(at=BOUND_AT,
                                receipt={"state": delivery.DELIVERED, "remote_id": LIVE_ID})

    def _open_report(self, period, remote_id="om_report", at=_at("11:00")):
        decision = self.store.decide_report(period=period)
        self.assertEqual(decision["action"], surface.CREATE)
        self.store.apply_report(period=period, decision=decision, at=at, remote_id=remote_id)

    def test_a_report_has_its_own_message_and_never_the_live_one(self):
        self._open_report(self.morning)
        summary = self.store.summary()
        self.assertEqual(summary["live"]["remote_id"], LIVE_ID)
        self.assertEqual(summary["reports"][0]["period_ref"], self.morning["period_ref"])

    def test_a_report_final_does_not_freeze_live_and_live_keeps_heartbeating(self):
        self._open_report(self.morning)
        self.store.apply_report(period=self.morning,
                                decision=self.store.decide_report(period=self.morning,
                                                                  closing=self.morning["period_ref"]),
                                at=_at("12:00"))
        self.assertEqual(self.store.summary()["reports"][0]["phase"], surface.FINAL_PENDING)
        self.assertEqual(self.store.summary()["live"]["remote_id"], LIVE_ID)
        self.assertEqual(self.store.decide_live(at=_at("17:30"))["action"], surface.PATCH)

    def test_a_pending_report_retries_the_same_message(self):
        self._open_report(self.morning)
        self.store.apply_report(period=self.morning,
                                decision=self.store.decide_report(period=self.morning,
                                                                  closing=self.morning["period_ref"]),
                                at=_at("12:00"))
        retry = self.store.decide_report(period=self.morning)
        self.assertEqual(retry["action"], surface.FINAL)
        self.assertTrue(retry.get("retry"))

    def test_a_stale_report_is_closed_across_the_period_boundary(self):
        # The morning report was never closed; the afternoon has begun.
        self._open_report(self.morning)
        decision = self.store.decide_report(period=self.afternoon)
        self.assertEqual(decision["action"], surface.FINAL)
        self.assertEqual(decision["period_ref"], self.morning["period_ref"])

    def test_a_report_is_closed_even_when_no_period_is_current(self):
        # e.g. just after midnight, when `in_period` is false — the cross-shift
        # report must still be closable.
        self._open_report(self.morning)
        decision = self.store.decide_report(period=self.none)
        self.assertEqual(decision["action"], surface.FINAL)
        self.assertEqual(decision["period_ref"], self.morning["period_ref"])

    def test_confirm_requires_the_prepared_version(self):
        self._open_report(self.morning)
        self.store.apply_report(period=self.morning,
                                decision=self.store.decide_report(period=self.morning,
                                                                  closing=self.morning["period_ref"]),
                                at=_at("12:00"), prepared_version="report@abc")
        with self.assertRaises(surface.CardSurfaceError):
            self.store.confirm_report(period_ref=self.morning["period_ref"], at=_at("12:01"),
                                      receipt={"state": delivery.DELIVERED,
                                               "remote_id": "om_report",
                                               "version": "report@OTHER"})
        self.assertEqual(self.store.summary()["reports"][0]["phase"], surface.FINAL_PENDING)

    def test_confirm_rejects_another_cards_delivered_receipt(self):
        self._open_report(self.morning)
        self.store.apply_report(period=self.morning,
                                decision=self.store.decide_report(period=self.morning,
                                                                  closing=self.morning["period_ref"]),
                                at=_at("12:00"), prepared_version="report@abc")
        with self.assertRaises(surface.CardSurfaceError):
            self.store.confirm_report(period_ref=self.morning["period_ref"], at=_at("12:01"),
                                      receipt={"state": delivery.DELIVERED,
                                               "remote_id": "om_another_card",
                                               "version": "report@abc"})

    def test_a_verified_acknowledgement_freezes_only_this_report(self):
        self._open_report(self.morning)
        self.store.apply_report(period=self.morning,
                                decision=self.store.decide_report(period=self.morning,
                                                                  closing=self.morning["period_ref"]),
                                at=_at("12:00"), prepared_version="report@abc")
        self.store.confirm_report(period_ref=self.morning["period_ref"], at=_at("12:01"),
                                  receipt={"state": delivery.DELIVERED,
                                           "remote_id": "om_report", "version": "report@abc"})
        self.assertEqual(self.store.summary()["reports"][0]["phase"], surface.FROZEN)
        self.assertEqual(self.store.summary()["live"]["remote_id"], LIVE_ID)
        self.assertEqual(self.store.decide_report(period=self.morning)["action"], surface.SKIP)

    def test_a_failed_report_receipt_never_freezes(self):
        self._open_report(self.morning)
        self.store.apply_report(period=self.morning,
                                decision=self.store.decide_report(period=self.morning,
                                                                  closing=self.morning["period_ref"]),
                                at=_at("12:00"), prepared_version="report@abc")
        for bad in ("FAILED", "UNKNOWN", "UNSUPPORTED"):
            with self.subTest(state=bad):
                with self.assertRaises(surface.CardSurfaceError):
                    self.store.confirm_report(
                        period_ref=self.morning["period_ref"], at=_at("12:01"),
                        receipt={"state": bad, "remote_id": "om_report",
                                 "version": "report@abc"})
                self.assertEqual(self.store.summary()["reports"][0]["phase"],
                                 surface.FINAL_PENDING)

    def test_the_hot_store_is_bounded_and_frozen_reports_are_retired(self):
        for index in range(surface.REPORT_HOT_CAP + 6):
            period = {"period_ref": f"CN-Test/2026-10-{index:02d}#morning",
                      "day_ref": f"CN-Test/2026-10-{index:02d}", "in_period": True}
            decision = self.store.decide_report(period=period)
            self.store.apply_report(period=period, decision=decision,
                                    at=_at("11:00"), remote_id=f"om_r{index}")
            self.store.apply_report(period=period,
                                    decision=self.store.decide_report(period=period,
                                                                      closing=period["period_ref"]),
                                    at=_at("12:00"), prepared_version=f"v{index}")
            self.store.confirm_report(period_ref=period["period_ref"], at=_at("12:01"),
                                      receipt={"state": delivery.DELIVERED,
                                               "remote_id": f"om_r{index}",
                                               "version": f"v{index}"})
        summary = self.store.summary()
        self.assertLessEqual(len(summary["reports"]), surface.REPORT_HOT_CAP)
        self.assertGreater(summary["retired"], 0)
        # nothing was lost: hot + retired account for every report made
        self.assertEqual(len(summary["reports"]) + summary["retired"],
                         surface.REPORT_HOT_CAP + 6)


class ItDoesNotBuildAThirdCard(unittest.TestCase):
    def test_the_surface_module_mints_no_alert_kind(self):
        source = (ROOT / "runtime" / "card_surface.py").read_text(encoding="utf-8")
        for forbidden in ("CardLedger(", 'record("alert"', "record(delivery.ALERT"):
            self.assertNotIn(forbidden, source)

    def test_there_are_exactly_two_surfaces(self):
        self.assertEqual(surface.SURFACES, ("LIVE", "REPORT"))

    def test_the_new_policy_is_not_one_card_per_workday(self):
        decision = surface.live_decision(
            now="2026-10-15T09:00:00+08:00", binding=_bound(),
            live_state={"last_delivered_regular_slot":
                        surface.regular_slot_id(_at("08:30", day="2026-10-15"))})
        self.assertEqual(decision["action"], surface.PATCH)
        self.assertNotIn(surface.ROTATION, decision["reasons"])


class TheRecipientAndCalendar(unittest.TestCase):
    def test_the_recipient_is_a_user_or_a_chat_never_a_bare_id(self):
        self.assertEqual(transport.split_recipient("user:ou_x"), ("--user-id", "ou_x"))
        self.assertEqual(transport.split_recipient("chat:oc_x"), ("--chat-id", "oc_x"))
        with self.assertRaises(transport.TransportFault):
            transport.split_recipient("ou_x")

    def test_the_report_period_handles_overnight_and_holiday(self):
        DECL = {"jurisdiction": "CN-Test", "timezone": "Asia/Shanghai",
                "years": {2026: {"workdays": {D(2026, 10, 17)},
                                 "non_working_days": {D(2026, 10, 1)}}},
                "fallback": None}
        night = card_period.period_at("2026-10-15T02:00:00+08:00", declaration=DECL)
        self.assertEqual(night["period_ref"], "CN-Test/2026-10-14#night")
        holiday = card_period.period_at("2026-10-01T14:00:00+08:00", declaration=DECL)
        self.assertEqual(holiday["name"], "all_day")
        unknown = card_period.period_at("2027-06-01T10:00:00+08:00", declaration=DECL)
        self.assertFalse(unknown["in_period"])
        self.assertEqual(surface.report_decision(period=unknown, reports=[])["action"],
                         surface.SKIP)


if __name__ == "__main__":
    unittest.main()
