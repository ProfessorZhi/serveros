#!/usr/bin/env python3
"""The two surfaces — ONE long-lived LIVE card, per-period REPORT cards.

The 2026-10-14 product ruling these tests hold:

    LIVE 有且只有一张，同 message_id 每 30 分钟心跳 PATCH，
        新告警/升级/恢复/处理状态变化即时 PATCH；
        ALERT 是这张卡里的一个区域，不是第三张卡。
    REPORT 按时段独立成卡，自己的 message_id，绝不与 LIVE 共用；
        报告冻结不阻塞 LIVE，报告失败不冻结 LIVE。

NO CLOCK AND NO TRANSPORT. Every instant is written down; `runtime/card_surface.py`
reads no clock and sends nothing. The store is driven on a temp file. Nothing
here contacts Feishu.
"""
from __future__ import annotations

import datetime
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


surface = _load("serveros_runtime_card_surface", ROOT / "runtime" / "card_surface.py")
delivery = _load("serveros_runtime_card_delivery", ROOT / "runtime" / "card_delivery.py")
card_period = _load("serveros_runtime_card_period", ROOT / "runtime" / "card_period.py")
transport = _load("serveros_runtime_message_transport",
                  ROOT / "runtime" / "message_transport.py")

T0 = "2026-10-14T10:00:00+08:00"          # a bound LIVE message was sent here
D = datetime.date


def _bound(sent_at=T0):
    return {"remote_id": "om_live_1", "at": sent_at}


class TheLiveCadence(unittest.TestCase):
    def test_a_half_hour_heartbeat_patches_the_same_message(self):
        decision = surface.live_decision(
            now="2026-10-14T10:30:00+08:00", binding=_bound(), last_patch_at=T0)
        self.assertEqual(decision["action"], surface.PATCH)
        self.assertEqual(decision["reasons"], [surface.HEARTBEAT])
        self.assertEqual(decision["remote_id"], "om_live_1")
        self.assertFalse(decision["coalesced"])

    def test_under_the_slot_nothing_happens(self):
        decision = surface.live_decision(
            now="2026-10-14T10:29:00+08:00", binding=_bound(), last_patch_at=T0)
        self.assertEqual(decision["action"], surface.SKIP)
        self.assertEqual(decision["reason"], surface.NOT_DUE)

    def test_an_event_patches_immediately_without_waiting_for_the_slot(self):
        decision = surface.live_decision(
            now="2026-10-14T10:05:00+08:00", binding=_bound(), last_patch_at=T0,
            events=[{"kind": surface.NEW}])
        self.assertEqual(decision["action"], surface.PATCH)
        self.assertEqual(decision["reasons"], [surface.EVENT])
        self.assertEqual(decision["remote_id"], "om_live_1")

    def test_an_event_and_the_heartbeat_coalesce_into_one_patch(self):
        decision = surface.live_decision(
            now="2026-10-14T10:30:00+08:00", binding=_bound(), last_patch_at=T0,
            events=[{"kind": surface.ESCALATED}])
        self.assertEqual(decision["action"], surface.PATCH)
        self.assertEqual(decision["reasons"], [surface.EVENT, surface.HEARTBEAT])
        self.assertTrue(decision["coalesced"])

    def test_duplicate_events_collapse_and_unknown_words_are_dropped(self):
        self.assertEqual(
            surface.event_kinds([{"kind": "NEW"}, {"kind": "NEW"},
                                 {"kind": "ESCALATED"}, "RECOVERED", "BANANA"]),
            [surface.NEW, surface.ESCALATED, surface.RECOVERED])

    def test_a_recovery_patches_the_same_live_card(self):
        decision = surface.live_decision(
            now="2026-10-14T10:05:00+08:00", binding=_bound(), last_patch_at=T0,
            events=[{"kind": surface.RECOVERED}])
        self.assertEqual(decision["action"], surface.PATCH)
        self.assertEqual(decision["remote_id"], "om_live_1")

    def test_an_undated_binding_is_not_patched(self):
        decision = surface.live_decision(
            now="2026-10-14T10:30:00+08:00",
            binding={"remote_id": "om_live_1", "at": "not-a-time"}, last_patch_at=T0)
        self.assertEqual(decision["action"], surface.SKIP)
        self.assertEqual(decision["reason"], delivery.IDENTITY_UNDATED)

    def test_a_bad_slot_is_refused_not_read_as_always_due(self):
        with self.assertRaises(surface.CardSurfaceError):
            surface.heartbeat_due(now=T0, last_patch_at=T0, slot_seconds=0)


class TheLiveIsNotAPeriodCard(unittest.TestCase):
    def test_no_binding_creates_exactly_one_message(self):
        decision = surface.live_decision(
            now=T0, binding=None, last_patch_at=None)
        self.assertEqual(decision["action"], surface.CREATE)
        self.assertEqual(decision["message_action"], delivery.NEW_MESSAGE)

    def test_rotation_happens_only_when_the_window_is_verified_expired(self):
        decision = surface.live_decision(
            now="2026-10-14T10:00:00+08:00",
            binding={"remote_id": "om_live_1", "at": "2026-09-01T10:00:00+08:00"},
            last_patch_at="2026-09-01T10:00:00+08:00")
        self.assertEqual(decision["action"], surface.SUCCESSOR)
        self.assertIn(surface.ROTATION, decision["reasons"])
        self.assertEqual(decision["message_action"], delivery.SUCCESSOR_MESSAGE)

    def test_the_successor_binding_carries_the_old_id_forward(self):
        binding = surface.successor_binding(
            previous={"remote_id": "om_live_1", "rotations": 0},
            remote_id="om_live_2", at="2026-10-14T10:00:00+08:00")
        self.assertEqual(binding["remote_id"], "om_live_2")
        self.assertEqual(binding["previous_remote_id"], "om_live_1")
        self.assertEqual(binding["rotations"], 1)


class TheTwoSurfacesAreIndependent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = surface.SurfaceStore(
            Path(self.tmp.name) / "cards" / "surfaces.json")
        self.period = {"period_ref": "CN-Test/2026-10-14#morning",
                       "label": "上午", "day_ref": "CN-Test/2026-10-14",
                       "in_period": True}

    def _bind_live(self):
        self.store.apply_live(
            decision={"action": surface.CREATE}, remote_id="om_live", at=T0)

    def test_a_report_has_its_own_message_id_and_never_the_live_one(self):
        self._bind_live()
        decision = self.store.decide_report(period=self.period)
        self.assertEqual(decision["action"], surface.CREATE)
        self.store.apply_report(period=self.period, decision=decision,
                                at="2026-10-14T11:00:00+08:00", remote_id="om_report")
        summary = self.store.summary()
        self.assertEqual(summary["live"]["remote_id"], "om_live")
        self.assertEqual(summary["reports"][0]["period_ref"], self.period["period_ref"])
        # two DISTINCT remote ids, and the live binding is untouched
        self.assertNotEqual(summary["live"]["remote_id"], "om_report")

    def test_a_report_final_does_not_freeze_the_live_card(self):
        self._bind_live()
        created = self.store.decide_report(period=self.period)
        self.store.apply_report(period=self.period, decision=created,
                                at="2026-10-14T11:00:00+08:00", remote_id="om_report")
        closed = self.store.decide_report(period=self.period, closing=self.period["period_ref"])
        self.assertEqual(closed["action"], surface.FINAL)
        self.store.apply_report(period=self.period, decision=closed,
                                at="2026-10-14T12:00:00+08:00",
                                archive={"overview": "x"})
        # the report is pending; the LIVE card is still a live binding
        self.assertEqual(self.store.summary()["reports"][0]["phase"],
                         surface.FINAL_PENDING)
        live = self.store.summary()["live"]
        self.assertEqual(live["remote_id"], "om_live")
        # the LIVE card still heartbeats beside the pending report
        heartbeat = self.store.decide_live(at="2026-10-14T13:00:00+08:00")
        self.assertEqual(heartbeat["action"], surface.PATCH)

    def test_a_failed_report_receipt_does_not_freeze_and_does_not_block_live(self):
        self._bind_live()
        created = self.store.decide_report(period=self.period)
        self.store.apply_report(period=self.period, decision=created,
                                at="2026-10-14T11:00:00+08:00", remote_id="om_report")
        closed = self.store.decide_report(period=self.period, closing=self.period["period_ref"])
        self.store.apply_report(period=self.period, decision=closed,
                                at="2026-10-14T12:00:00+08:00")
        for bad in ("FAILED", "UNKNOWN", "UNSUPPORTED"):
            with self.subTest(state=bad):
                with self.assertRaises(surface.CardSurfaceError):
                    self.store.confirm_report(period_ref=self.period["period_ref"],
                                              at="2026-10-14T12:05:00+08:00",
                                              receipt={"state": bad})
                self.assertEqual(self.store.summary()["reports"][0]["phase"],
                                 surface.FINAL_PENDING)

    def test_a_pending_report_retries_the_same_message(self):
        self._bind_live()
        created = self.store.decide_report(period=self.period)
        self.store.apply_report(period=self.period, decision=created,
                                at="2026-10-14T11:00:00+08:00", remote_id="om_report")
        self.store.apply_report(period=self.period,
                                decision=self.store.decide_report(
                                    period=self.period,
                                    closing=self.period["period_ref"]),
                                at="2026-10-14T12:00:00+08:00")
        retry = self.store.decide_report(period=self.period)
        self.assertEqual(retry["action"], surface.FINAL)
        self.assertTrue(retry.get("retry"))
        # the report keeps the SAME message; live is still live
        self.assertEqual(self.store.summary()["reports"][0]["period_ref"],
                         self.period["period_ref"])
        self.assertEqual(self.store.summary()["live"]["remote_id"], "om_live")

    def test_a_confirmed_report_freezes_only_itself(self):
        self._bind_live()
        created = self.store.decide_report(period=self.period)
        self.store.apply_report(period=self.period, decision=created,
                                at="2026-10-14T11:00:00+08:00", remote_id="om_report")
        self.store.apply_report(period=self.period,
                                decision=self.store.decide_report(
                                    period=self.period, closing=self.period["period_ref"]),
                                at="2026-10-14T12:00:00+08:00")
        self.store.confirm_report(period_ref=self.period["period_ref"],
                                  at="2026-10-14T12:01:00+08:00",
                                  receipt={"state": delivery.DELIVERED,
                                           "remote_id": "om_report", "version": "v"})
        self.assertEqual(self.store.summary()["reports"][0]["phase"], surface.FROZEN)
        self.assertEqual(self.store.summary()["live"]["remote_id"], "om_live")
        again = self.store.decide_report(period=self.period)
        self.assertEqual(again["action"], surface.SKIP)
        self.assertEqual(again["reason"], surface.ALREADY_FROZEN)

    def test_a_second_report_period_is_a_second_message(self):
        self._bind_live()
        first = self.store.decide_report(period=self.period)
        self.store.apply_report(period=self.period, decision=first,
                                at="2026-10-14T11:00:00+08:00", remote_id="om_report_a")
        other = {"period_ref": "CN-Test/2026-10-14#afternoon", "label": "下午",
                 "day_ref": "CN-Test/2026-10-14", "in_period": True}
        second = self.store.decide_report(period=other)
        self.assertEqual(second["action"], surface.CREATE)
        self.store.apply_report(period=other, decision=second,
                                at="2026-10-14T13:00:00+08:00", remote_id="om_report_b")
        self.assertEqual(len(self.store.summary()["reports"]), 2)

    def test_an_unreadable_store_is_refused_rather_than_replaced(self):
        path = Path(self.tmp.name) / "cards" / "surfaces.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(surface.CardSurfaceError):
            surface.SurfaceStore(path)


class TheStoreIsIdempotent(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = surface.SurfaceStore(
            Path(self.tmp.name) / "cards" / "surfaces.json")

    def test_apply_is_idempotent_on_the_same_instant_and_never_rewinds(self):
        self.store.apply_live(decision={"action": surface.CREATE},
                              remote_id="om_live", at=T0)
        self.store.apply_live(decision={"action": surface.PATCH},
                              at="2026-10-14T10:30:00+08:00")
        # a replayed EARLIER tick must not move the heartbeat clock backwards
        self.store.apply_live(decision={"action": surface.PATCH}, at=T0)
        self.assertEqual(self.store.summary()["live"]["last_patch_at"],
                         "2026-10-14T10:30:00+08:00")

    def test_a_patch_without_a_binding_is_refused(self):
        with self.assertRaises(surface.CardSurfaceError):
            self.store.apply_live(decision={"action": surface.PATCH}, at=T0)


class ItDoesNotBuildAThirdCard(unittest.TestCase):
    def test_the_surface_module_mints_no_alert_kind(self):
        # ALERT lives inside the LIVE card. The delivery ledger's per-episode
        # alert/recovery kinds keep their OWN production path and are not used by
        # this module to mint a separate V13 card.
        source = (ROOT / "runtime" / "card_surface.py").read_text(encoding="utf-8")
        for forbidden in ("CardLedger(", "record(delivery.ALERT", "record(\"alert\""):
            self.assertNotIn(forbidden, source)

    def test_there_are_exactly_two_surfaces(self):
        self.assertEqual(surface.SURFACES, ("LIVE", "REPORT"))

    def test_the_new_policy_is_not_one_card_per_workday(self):
        # The old «one card per workday» cadence is production's; this module
        # does not implement it, and a LIVE with a healthy binding never
        # rotates on a day or period boundary.
        decision = surface.live_decision(
            now="2026-10-15T09:00:00+08:00",      # next workday, period boundary
            binding=_bound(), last_patch_at="2026-10-15T08:30:00+08:00")
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
        # a report for an unnamed day is refused, not invented
        self.assertEqual(surface.report_decision(period=unknown, report=None)["action"],
                         surface.SKIP)


if __name__ == "__main__":
    unittest.main()
