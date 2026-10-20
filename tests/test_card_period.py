#!/usr/bin/env python3
"""§11½ — the period card: one message per period, patched quietly, closed once.

The ruling these tests hold (2026-10-14), in the sentences that shape the code:

    全天候保留完整 V13 LIVE 监控卡；旧 §10 的 08–20 只约束常规主动播报，
    不约束资源采样及已存在卡片的静默 PATCH
    工作日示例 09–12 上午 / 12–18 下午 / 18–次日09 夜间；节假日全天值守
    每时段仅新建一条消息；时段内每 10 分钟静默 PATCH 同一 message_id
    结束时最终 PATCH 为总览概括 + 完整时段趋势 + 三设备摘要 + 交接，随后冻结
    新时段卡初建可能有一次可见消息，不可声称静默创建

WHAT IS NOT IN THIS FILE
------------------------
No clock and no transport: every instant is written down, and `runtime/card_period.py`
reads no clock and sends nothing. The calendar is SYNTHETIC for the reason
`tests/test_business_calendar.py` gives at length — the declaration, not the
weekday, decides — and one test checks a synthetic workday against the stdlib
first so the fixture's own claim is verified before it is relied on. The fleet
model is the three-device FIXTURE, never a live read.
"""
from __future__ import annotations

import datetime
import importlib.util
import json
import re
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


card_period = _load("serveros_runtime_card_period", ROOT / "runtime" / "card_period.py")
card_delivery = _load("serveros_runtime_card_delivery",
                      ROOT / "runtime" / "card_delivery.py")
card_day = _load("serveros_runtime_card_day", ROOT / "runtime" / "card_day.py")
monitor = _load("serveros_runtime_fleet_monitor_card",
                ROOT / "runtime" / "fleet_monitor_card.py")
source = _load("serveros_runtime_fleet_monitor_source",
               ROOT / "runtime" / "fleet_monitor_source.py")

D = datetime.date
#: A synthetic declaration covering one year. 2026-10-14 is a plain Wednesday
#: (asserted below); 2026-10-17 is DECLARED a workday though it is a Saturday
#: (a 调休), and 2026-10-01 is DECLARED a rest day though it is a Thursday.
DECL = {
    "jurisdiction": "CN-Test",
    "timezone": "Asia/Shanghai",
    "source": {"url": "test://calendar", "revision": "test-1",
               "published": "2026-01-01"},
    "years": {2026: {"workdays": {D(2026, 10, 17)},
                     "non_working_days": {D(2026, 10, 1)}}},
    "fallback": None,
}

#: A tick's reading model. Small on purpose: the row asserts it keeps only the
#: closed set below, and a bigger model would only be more ways to pass by luck.
MODEL = {
    "env_health": {"verdict": "OK", "observation": "已观测", "gaps": []},
    "sections": {"mine": {"state": "OK"}, "host": {"state": "OK"},
                 "gateway": {"state": "OK"}},
}


def _periods(store, at, row_for):
    return store.advance(period=card_period.period_at(at, declaration=DECL),
                         at=at, row_for=row_for)


class TheFixtureIsWhatItClaims(unittest.TestCase):
    def test_the_synthetic_workday_is_a_weekday(self):
        # The declaration decides, but the fixture's own sentence — «a plain
        # Wednesday» — is checked here so a wrong date fails loudly rather than
        # quietly changing which branch every test below exercises.
        self.assertEqual(D(2026, 10, 14).weekday(), 2)
        self.assertEqual(D(2026, 10, 17).weekday(), 5)
        self.assertEqual(D(2026, 10, 1).weekday(), 3)


class ThePeriodsTileTheDay(unittest.TestCase):
    def test_a_workday_is_morning_afternoon_night(self):
        cases = {
            "2026-10-14T09:30:00+08:00": "morning",
            "2026-10-14T13:00:00+08:00": "afternoon",
            "2026-10-14T20:00:00+08:00": "night",
        }
        for at, name in cases.items():
            with self.subTest(at=at):
                fact = card_period.period_at(at, declaration=DECL)
                self.assertTrue(fact["in_period"])
                self.assertEqual(fact["name"], name)
                self.assertEqual(fact["period_ref"], f"CN-Test/2026-10-14#{name}")

    def test_the_tick_is_a_ten_minute_slot(self):
        ticks = {at: card_period.period_at(at, declaration=DECL)["tick"]
                 for at in ("2026-10-14T09:00:00+08:00",
                            "2026-10-14T09:10:00+08:00",
                            "2026-10-14T09:20:00+08:00",
                            "2026-10-14T09:30:00+08:00")}
        # 09:00 is the first slot, and each ten minutes advances it once. A
        # minutes-over-seconds bug would pin the first six minutes to slot 1 and
        # this is the assertion that catches it.
        self.assertEqual(list(ticks.values()), [1, 2, 3, 4])

    def test_the_night_period_wraps_to_the_previous_day(self):
        # 02:00 has no period of its own on the 15th; it is the tail of the
        # 14th's night shift, and the period_ref says so — which is what makes
        # the identity a dedupe key rather than a second card at midnight.
        fact = card_period.period_at("2026-10-15T02:00:00+08:00", declaration=DECL)
        self.assertTrue(fact["in_period"])
        self.assertEqual(fact["period_ref"], "CN-Test/2026-10-14#night")

    def test_a_rest_day_is_one_all_day_period(self):
        fact = card_period.period_at("2026-10-01T14:00:00+08:00", declaration=DECL)
        self.assertTrue(fact["in_period"])
        self.assertEqual(fact["name"], "all_day")
        self.assertEqual(fact["period_ref"], "CN-Test/2026-10-01#all_day")

    def test_a_declared_workday_overrides_the_weekend(self):
        fact = card_period.period_at("2026-10-17T10:00:00+08:00", declaration=DECL)
        self.assertTrue(fact["in_period"])
        self.assertEqual(fact["name"], "morning")

    def test_an_unknown_year_gets_no_period(self):
        fact = card_period.period_at("2027-06-01T10:00:00+08:00", declaration=DECL)
        self.assertFalse(fact["in_period"])
        self.assertEqual(fact["reason"], card_period.CALENDAR_UNKNOWN)
        self.assertIsNone(fact["period_ref"])


class TheLifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = card_period.PeriodStore(
            Path(self.tmp.name) / "cards" / "period.json")
        self.model = source.model_from_fixture()

    def _row_for(self, step):
        return card_period.row(MODEL, period=step["period"])

    def test_the_first_tick_creates_and_later_ticks_patch_one_message(self):
        at = "2026-10-14T09:30:00+08:00"
        first = _periods(self.store, at, self._row_for)
        self.assertEqual(first["decision"]["action"], card_period.CREATE)
        # The CREATE is a NEW_MESSAGE and the module does not claim it is quiet.
        self.assertEqual(first["decision"]["message"]["action"],
                         card_delivery.NEW_MESSAGE)

        self.store.announce(remote_id="om_alpha", at=at)

        at2 = "2026-10-14T09:40:00+08:00"
        second = self.store.advance(
            period=card_period.period_at(at2, declaration=DECL), at=at2,
            row_for=self._row_for)
        self.assertEqual(second["decision"]["action"], card_period.PATCH)
        self.assertEqual(second["decision"]["message"]["action"],
                         card_delivery.PATCH_MESSAGE)
        self.assertEqual(second["decision"]["message"]["remote_id"], "om_alpha")
        # ONE message the whole period: the binding never moved.
        self.assertEqual(second["period"]["message"]["remote_id"], "om_alpha")
        self.assertEqual(second["period"]["ticks"], 2)

    def _finalize_at(self, at):
        """The PREPARED archive and version a FINAL step carries."""
        def finalize(step):
            shown = self.store.summary()
            rows = shown["rows"] + ([shown["latest"]] if shown["latest"] else [])
            view = card_period.archive(self.model, period=step["period"], rows=rows)
            card = card_period.render_archive(view, model=self.model)
            return {"archive": view,
                    "frozen": card_period.frozen_block(
                        card=card, at=at, period_ref=step["period_ref"])}
        return finalize

    def test_the_boundary_prepares_the_final_and_waits_for_the_ack(self):
        at = "2026-10-14T11:50:00+08:00"
        _periods(self.store, at, self._row_for)
        self.store.announce(remote_id="om_morning", at=at)

        boundary = "2026-10-14T12:00:00+08:00"
        first = self.store.advance(
            period=card_period.period_at(boundary, declaration=DECL), at=boundary,
            row_for=self._row_for, finalize=self._finalize_at(boundary))
        # ONE step: the final is PREPARED. No successor opens, because the
        # archive has not been confirmed at the far end.
        self.assertEqual([s["action"] for s in first["decision"]["steps"]],
                         [card_period.FINAL])
        doc = self.store.read()
        self.assertEqual(doc["open"]["period_ref"], "CN-Test/2026-10-14#morning")
        self.assertEqual(card_period._phase(doc["open"]), card_period.FINAL_PENDING)
        self.assertIsNone(doc["open"]["frozen"])
        self.assertIsNone(doc.get("previous"))

        # A tick in the NEXT period only RETRIES the same message.
        later = "2026-10-14T12:10:00+08:00"
        second = self.store.advance(
            period=card_period.period_at(later, declaration=DECL), at=later,
            row_for=self._row_for, finalize=self._finalize_at(later))
        self.assertEqual(second["decision"]["action"], card_period.FINAL)
        self.assertEqual(second["decision"]["reason"],
                         card_period.FINAL_UNCONFIRMED)
        self.assertEqual(second["decision"]["message"]["remote_id"], "om_morning")
        self.assertEqual(self.store.summary()["pending"]["attempts"], 2)

        # The acknowledgement arrives → the freeze is committed.
        version = self.store.summary()["pending"]["frozen"]["version"]
        self.store.confirm(at=later, receipt={"kind": card_delivery.PERIOD,
                                              "state": card_delivery.DELIVERED,
                                              "version": version,
                                              "remote_id": "om_morning"})
        self.assertEqual(card_period._phase(self.store.read()["open"]),
                         card_period.FROZEN)

        # Only NOW does the successor open.
        opened = "2026-10-14T12:20:00+08:00"
        third = self.store.advance(
            period=card_period.period_at(opened, declaration=DECL), at=opened,
            row_for=self._row_for)
        self.assertEqual(third["decision"]["action"], card_period.CREATE)
        self.assertEqual(third["period"]["period_ref"],
                         "CN-Test/2026-10-14#afternoon")
        self.assertEqual(self.store.read()["previous"]["period_ref"],
                         "CN-Test/2026-10-14#morning")
        self.assertIsNotNone(self.store.read()["previous"]["frozen"])

    def test_a_frozen_period_is_not_patched_again(self):
        at = "2026-10-14T09:30:00+08:00"
        ref = card_period.period_at(at, declaration=DECL)["period_ref"]
        _periods(self.store, at, self._row_for)
        self.store.announce(remote_id="om_a", at=at)
        # 收班: an explicit close of THIS period, rather than waiting for the
        # next period's first tick to detect the boundary.
        closed = self.store.advance(
            period=card_period.period_at(at, declaration=DECL), at=at,
            row_for=self._row_for, closing=ref, finalize=self._finalize_at(at))
        self.assertEqual(closed["decision"]["action"], card_period.FINAL)
        version = self.store.summary()["pending"]["frozen"]["version"]
        self.store.confirm(at=at, receipt={"kind": card_delivery.PERIOD,
                                           "state": card_delivery.DELIVERED,
                                           "version": version,
                                           "remote_id": "om_a"})
        replay = self.store.advance(
            period=card_period.period_at(at, declaration=DECL), at=at,
            row_for=self._row_for)
        self.assertEqual(replay["decision"]["action"], card_period.SKIP)
        self.assertEqual(replay["decision"]["reason"], card_period.ALREADY_FROZEN)

    def test_replaying_an_instant_never_opens_a_second_message(self):
        at = "2026-10-14T09:30:00+08:00"
        first = _periods(self.store, at, self._row_for)
        self.assertEqual(first["decision"]["action"], card_period.CREATE)
        self.store.announce(remote_id="om_once", at=at)
        # The same instant replayed: a PATCH of the same message, not a second.
        replay = self.store.advance(
            period=card_period.period_at(at, declaration=DECL), at=at,
            row_for=self._row_for)
        self.assertEqual(replay["decision"]["action"], card_period.PATCH)
        self.assertEqual(replay["period"]["message"]["remote_id"], "om_once")
        self.assertEqual(len(replay["decision"]["steps"]), 1)

    def test_an_unreadable_store_is_refused_rather_than_replaced(self):
        path = Path(self.tmp.name) / "cards" / "period.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(card_period.CardPeriodError):
            card_period.PeriodStore(path)

    def test_announce_reports_a_rebinding(self):
        at = "2026-10-14T09:30:00+08:00"
        _periods(self.store, at, self._row_for)
        first = self.store.announce(remote_id="om_1", at=at)
        self.assertEqual(first["change"], card_day.BOUND)
        again = self.store.announce(remote_id="om_1", at=at)
        self.assertEqual(again["change"], card_day.UNCHANGED)
        moved = self.store.announce(remote_id="om_2", at=at)
        self.assertEqual(moved["change"], card_day.REBOUND)

    def test_announce_refuses_a_chat_id(self):
        at = "2026-10-14T09:30:00+08:00"
        _periods(self.store, at, self._row_for)
        with self.assertRaises(card_period.CardPeriodError):
            self.store.announce(remote_id="oc_conversation", at=at)


class TheRemoteAckGate(unittest.TestCase):
    """The 2026-10-14 review's gate: NO freeze before a verified remote ACK.

    The failure this class exists against is a local `apply(FINAL)` that writes
    `frozen` on the strength of having ATTEMPTED the final patch: a 429 / timeout
    at the far end then meets a card that says it was archived, the next tick
    reads ALREADY_FROZEN and refuses to retry, and the boundary opens a successor
    over an archive that never landed. Every test here drives that path with a
    fake transport (a receipt handed in, or a `card_delivery` ledger row) — no
    real Feishu message is sent, and no clock is read.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = card_period.PeriodStore(
            Path(self.tmp.name) / "cards" / "period.json")
        self.ledger = card_delivery.CardLedger(
            Path(self.tmp.name) / "cards" / "delivery.json")
        self.model = source.model_from_fixture()

    def _row_for(self, step):
        return card_period.row(MODEL, period=step["period"])

    def _finalize_at(self, at):
        def finalize(step):
            shown = self.store.summary()
            rows = shown["rows"] + ([shown["latest"]] if shown["latest"] else [])
            view = card_period.archive(self.model, period=step["period"], rows=rows)
            card = card_period.render_archive(view, model=self.model)
            return {"archive": view,
                    "frozen": card_period.frozen_block(
                        card=card, at=at, period_ref=step["period_ref"])}
        return finalize

    def _prepare_final(self, at="2026-10-14T11:50:00+08:00"):
        _periods(self.store, at, self._row_for)
        self.store.announce(remote_id="om_shift", at=at)
        ref = card_period.period_at(at, declaration=DECL)["period_ref"]
        return self.store.advance(
            period=card_period.period_at(at, declaration=DECL), at=at,
            row_for=self._row_for, closing=ref, finalize=self._finalize_at(at))

    def test_a_failed_or_unknown_receipt_never_freezes(self):
        self._prepare_final()
        version = self.store.summary()["pending"]["frozen"]["version"]
        for bad in card_delivery.OUTCOMES[1:]:  # FAILED / UNSUPPORTED / UNKNOWN
            with self.subTest(state=bad):
                with self.assertRaises(card_period.CardPeriodError):
                    self.store.confirm(
                        at="2026-10-14T11:55:00+08:00",
                        receipt={"state": bad, "version": version,
                                 "remote_id": "om_shift"})
                self.assertEqual(card_period._phase(self.store.read()["open"]),
                                 card_period.FINAL_PENDING)
                self.assertIsNone(self.store.read()["open"]["frozen"])

    def test_a_receipt_for_another_version_is_refused(self):
        self._prepare_final()
        with self.assertRaises(card_period.CardPeriodError):
            self.store.confirm(at="2026-10-14T11:55:00+08:00",
                               receipt={"state": card_delivery.DELIVERED,
                                        "version": "period@someone-else",
                                        "remote_id": "om_shift"})

    def test_confirm_without_a_prepared_final_is_refused(self):
        at = "2026-10-14T09:30:00+08:00"
        _periods(self.store, at, self._row_for)
        with self.assertRaises(card_period.CardPeriodError):
            self.store.confirm(at=at, receipt={"state": card_delivery.DELIVERED,
                                               "version": "x", "remote_id": "om"})

    def test_a_retry_reuses_the_same_message_id(self):
        self._prepare_final()
        later = "2026-10-14T11:58:00+08:00"
        r = self.store.advance(
            period=card_period.period_at(later, declaration=DECL), at=later,
            row_for=self._row_for, finalize=self._finalize_at(later))
        self.assertEqual(r["decision"]["message"]["action"],
                         card_delivery.PATCH_MESSAGE)
        self.assertEqual(r["decision"]["message"]["remote_id"], "om_shift")
        self.assertEqual(self.store.summary()["pending"]["remote_id"], "om_shift")
        # ONE message the whole period, CREATE through the retry.
        self.assertEqual(self.store.summary()["message"]["remote_id"], "om_shift")

    def test_restart_reconciles_against_the_delivery_ledger(self):
        self._prepare_final()
        version = self.store.summary()["pending"]["frozen"]["version"]
        # 1. No receipt at all → not confirmed; retry.
        first = self.store.reconcile_pending(ledger=self.ledger,
                                             at="2026-10-14T12:01:00+08:00")
        self.assertEqual(first["outcome"], "RETRY")
        self.assertEqual(card_period._phase(self.store.read()["open"]),
                         card_period.FINAL_PENDING)
        # 2. A FAILED receipt is not a confirmation either.
        self.ledger.record("period", at="2026-10-14T11:55:00+08:00", state="FAILED",
                           version=version, remote_id="om_shift", reason="429")
        second = self.store.reconcile_pending(ledger=self.ledger,
                                              at="2026-10-14T12:02:00+08:00")
        self.assertEqual(second["outcome"], "RETRY")
        self.assertIsNone(self.store.read()["open"]["frozen"])
        # 3. A DELIVERED receipt for THIS version confirms the freeze.
        self.ledger.record("period", at="2026-10-14T12:03:00+08:00",
                           state="DELIVERED", version=version,
                           remote_id="om_shift")
        third = self.store.reconcile_pending(ledger=self.ledger,
                                             at="2026-10-14T12:03:00+08:00")
        self.assertEqual(third["outcome"], "CONFIRMED")
        self.assertEqual(card_period._phase(self.store.read()["open"]),
                         card_period.FROZEN)
        # The restart path kept ONE ledger: nothing was written by this module.
        self.assertEqual(len(self.ledger.entries("period")), 2)

    def test_reconcile_when_not_pending_is_a_no_op(self):
        at = "2026-10-14T09:30:00+08:00"
        _periods(self.store, at, self._row_for)
        r = self.store.reconcile_pending(ledger=self.ledger, at=at)
        self.assertEqual(r["outcome"], "NOT_PENDING")

    def test_a_successor_is_refused_at_the_write_layer_over_a_pending_final(self):
        self._prepare_final()
        nxt = card_period.period_at("2026-10-14T12:00:00+08:00", declaration=DECL)
        with self.assertRaises(card_period.CardPeriodError):
            card_period.apply(self.store.read(),
                              step={"action": card_period.CREATE,
                                    "period_ref": nxt["period_ref"], "period": nxt},
                              row_=card_period.row(MODEL, period=nxt),
                              at="2026-10-14T12:00:00+08:00")

    def test_a_pending_final_outranks_a_later_period_tick(self):
        # HANDOVER ORDER: a tick well into the NEXT period still only retries —
        # it never opens the next card while the archive is unconfirmed.
        self._prepare_final()
        much_later = "2026-10-14T15:00:00+08:00"
        r = self.store.advance(
            period=card_period.period_at(much_later, declaration=DECL),
            at=much_later, row_for=self._row_for,
            finalize=self._finalize_at(much_later))
        self.assertEqual(r["decision"]["reason"], card_period.FINAL_UNCONFIRMED)
        self.assertEqual(self.store.read()["open"]["period_ref"],
                         "CN-Test/2026-10-14#morning")

    def test_an_unconfirmed_store_is_refused_on_reload(self):
        # A pending final with no prepared version cannot be reconciled after a
        # restart, so the store refuses it rather than opening a second message.
        self._prepare_final()
        doc = self.store.read()
        doc["open"]["pending"]["frozen"] = {"kind": card_delivery.PERIOD,
                                            "at": "x", "version": None,
                                            "payload_hash": None}
        Path(self.store.path).write_text(json.dumps(doc), encoding="utf-8")
        with self.assertRaises(card_period.CardPeriodError):
            card_period.PeriodStore(self.store.path)

    def test_deploy_readiness_is_not_ready(self):
        ready = card_period.deploy_readiness()
        self.assertFalse(ready["ready"])
        self.assertTrue(ready["unverified"])


class ThePresentationLayerUsesOnlyWords(unittest.TestCase):
    """The 2026-10-14 screenshot regressions, each pinned by an assertion.

    Every case here failed on the card the user actually saw. They are written
    against the RENDERED card body — the markdown a person reads — because that
    is where the defects were visible, and a test that only looks at the view
    model would not have caught a `{'state': ...}` leaking into the markdown.
    """

    NOW = "2026-10-14T09:40:00+08:00"

    def _reading(self, label, value, *, unit="", note="", severity="OK"):
        return monitor.reading(label, value, unit=unit, note=note,
                               observed_at=self.NOW, severity=severity)

    def _unknown(self, ref="device:work-fj", name="FJPCOS 工作站"):
        return {"device_ref": ref, "display_name": name, "platform": "windows",
                "reachability": {"state": monitor.STATE_UNKNOWN,
                                 "reason": "该设备没有可用的采集结果"},
                "readings": [], "accounts": []}

    def _model(self, *, tokyo=(), extras=(), services=()):
        devices = [{
            "device_ref": "device:tokyo-shared-dev-01",
            "display_name": "东京共享开发机", "platform": "linux",
            "reachability": {"state": monitor.STATE_KNOWN, "observed_at": self.NOW},
            "readings": list(tokyo), "accounts": [],
        }] + list(extras)
        return monitor.monitor_model(devices=devices, services=list(services),
                                     now=self.NOW)

    def _body_text(self, card):
        found = []

        def walk(node):
            if isinstance(node, dict):
                if isinstance(node.get("content"), str):
                    found.append(node["content"])
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(card["body"])
        return "\n".join(found)

    def _top_elements(self, card):
        return card["body"]["elements"]

    def _kpi_row_columns(self, card):
        return [len(e.get("columns") or [])
                for e in self._top_elements(card)
                if e.get("tag") == "column_set"]

    def _period(self):
        return card_period.period_at(self.NOW, declaration=DECL)

    def _row(self, period):
        return card_period.row(MODEL, period=period)

    def test_nothing_leaks_a_mapping_none_or_a_key_into_the_body(self):
        model = self._model(tokyo=[self._reading("CPU", "128 逻辑核")],
                            extras=[self._unknown()])
        text = self._body_text(card_period.render_period_card(model))
        for bad in ("{'", "}", "None", "observed_at", "'state'", "'reason'"):
            self.assertNotIn(bad, text, f"card body leaked {bad!r}")

    def test_an_unmeasured_device_is_not_called_lost(self):
        model = self._model(tokyo=[self._reading("CPU", "128 逻辑核")],
                            extras=[self._unknown(), self._unknown("device:llm-lab-dell", "A6000 推理机")])
        text = self._body_text(card_period.render_period_card(model))
        self.assertIn("未获取采样", text)
        self.assertNotIn("失联", text)

    def test_a_single_sample_is_not_called_a_trend(self):
        period = self._period()
        view = card_period.archive(self._model(tokyo=[self._reading("CPU", "128")]),
                                   period=period, rows=[self._row(period)])
        text = card_period.render_archive_text(view)
        self.assertIn("仅 1 次采样，不足以评价趋势", text)
        self.assertNotIn("完整时段趋势一览", text)

    def test_no_service_data_is_not_zero_services(self):
        model = self._model(tokyo=[self._reading("CPU", "128")])
        card = card_period.render_period_card(model)
        # A standalone first-screen 服务 line was one of the things that read
        # badly; the counts now live inside the collected device's fold.
        self.assertFalse(any(e.get("tag") == "markdown" and "服务" in str(e.get("content") or "")
                             for e in self._top_elements(card)))
        text = self._body_text(card)
        self.assertIn("服务：未采集", text)
        self.assertNotIn("共 0", text)

    def test_unknown_sources_do_not_paint_the_card_red(self):
        # Two unmeasured devices and a near-threshold swap — and still no red,
        # because none of it is a CONFIRMED critical.
        model = self._model(
            tokyo=[self._reading("交换区", "89.4%", note="阈值 90.0%",
                                 severity="ATTENTION")],
            extras=[self._unknown(), self._unknown("device:llm-lab-dell", "A6000 推理机")])
        card = card_period.render_period_card(model)
        self.assertEqual(card["header"]["template"], "blue")

    def test_a_confirmed_critical_is_red(self):
        model = self._model(tokyo=[self._reading("磁盘", "100%", severity="CRITICAL")])
        self.assertEqual(card_period.render_period_card(model)["header"]["template"], "red")

    def test_the_title_names_the_product_and_keeps_the_test_mark_small(self):
        card = card_period.render_period_card(
            self._model(tokyo=[self._reading("CPU", "128")]), test_mark="[灰度测试]")
        self.assertEqual(card["header"]["title"]["content"], "ServerOS · 节点监控")
        self.assertIn("[灰度测试]", card["header"]["subtitle"]["content"])
        self.assertIn("时段进行中", card["header"]["subtitle"]["content"])
        # The developer identifier must not be anywhere on the card.
        blob = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("gray/", blob)
        self.assertNotIn("#demo", blob)

    def test_the_header_shows_local_time_not_a_long_iso(self):
        card = card_period.render_period_card(self._model(tokyo=[self._reading("CPU", "128")]))
        subtitle = card["header"]["subtitle"]["content"]
        self.assertRegex(subtitle, r"\d{2}:\d{2}")
        self.assertNotIn("T09:40", subtitle)

    def test_the_first_screen_leads_with_the_native_kpi_grid(self):
        period = self._period()
        model = self._model(
            tokyo=[self._reading("CPU", "128 逻辑核"), self._reading("内存", "33.7%")],
            extras=[self._unknown()])
        view = card_period.archive(model, period=period, rows=[self._row(period)])
        card = card_period.render_archive(view, model=model)
        elements = self._top_elements(card)
        self.assertEqual(elements[0]["tag"], "column_set")      # KPI first
        self.assertTrue(any(e.get("tag") == "div" for e in elements))
        panels = [i for i, e in enumerate(elements) if e.get("tag") == "collapsible_panel"]
        self.assertTrue(panels)
        summary = next(i for i, e in enumerate(elements)
                       if "时段小结" in str(e.get("columns") or ""))
        self.assertLess(summary, min(panels))                   # closure above the folds

    def test_no_recorded_events_says_recorded_not_safe(self):
        period = self._period()
        view = card_period.archive(self._model(tokyo=[self._reading("CPU", "128")]),
                                   period=period, rows=[self._row(period)])
        text = card_period.render_archive_text(view)
        self.assertIn("本时段未记录事故", text)
        self.assertNotIn("无事故", text)

    def test_swap_near_threshold_gets_one_tight_line(self):
        model = self._model(tokyo=[self._reading("交换区", "89.4%", note="阈值 90.0%")])
        text = self._body_text(card_period.render_period_card(model))
        self.assertIn("接近阈值", text)

    def test_the_card_uses_native_containers_and_no_unverified_element(self):
        model = self._model(tokyo=[self._reading("CPU", "128")],
                            extras=[self._unknown()])
        card = card_period.render_period_card(model)
        tags = []
        monitor._walk_tags(card["body"], tags)
        self.assertIn("column_set", tags)
        self.assertIn("collapsible_panel", tags)
        self.assertIn("div", tags)
        # Gated off / unverified — never used as decoration.
        self.assertNotIn("chart", tags)
        self.assertNotIn("table", tags)
        self.assertNotIn("interactive_container", tags)
        self.assertEqual(card["config"], {"update_multi": True, "width_mode": "default"})

    def test_the_kpi_block_is_a_native_two_column_grid(self):
        model = self._model(tokyo=[
            self._reading("CPU", "128 逻辑核"), self._reading("内存", "30.4%"),
            self._reading("交换区", "89.1%", note="阈值 90.0%"),
            self._reading("磁盘 /", "9.5%")])
        card = card_period.render_period_card(model)
        rows = [e for e in self._top_elements(card) if e.get("tag") == "column_set"]
        self.assertGreaterEqual(len(rows), 2)
        self.assertEqual([len(r["columns"]) for r in rows[:2]], [2, 2])
        tile = rows[0]["columns"][0]
        self.assertEqual(tile["tag"], "column")
        self.assertEqual(tile["background_style"], "grey-50")
        self.assertEqual(tile["padding"], "12px")
        # A core COUNT is never rendered as a percentage.
        self.assertNotIn("128%", json.dumps(card, ensure_ascii=False))

    def test_no_column_carries_the_erroneous_corner_radius(self):
        # larksuite/cli #2371: `column.corner_radius` is not a field of `column`
        # and makes Card 2.0 answer `200621 unknown property`.
        card = card_period.render_period_card(
            self._model(tokyo=[self._reading("CPU", "128")]))

        def walk(node):
            if isinstance(node, dict):
                if node.get("tag") == "column":
                    self.assertNotIn("corner_radius", node)
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(card["body"])

    def test_the_header_wears_an_icon_and_status_tags(self):
        model = self._model(tokyo=[self._reading("CPU", "128")],
                            extras=[self._unknown()])
        header = card_period.render_period_card(model)["header"]
        self.assertEqual(header["icon"]["tag"], "standard_icon")
        self.assertTrue(header["icon"]["token"])
        tags = header["text_tag_list"]
        self.assertTrue(1 <= len(tags) <= 3)
        self.assertEqual(tags[0]["tag"], "text_tag")
        self.assertIn("已采集", tags[0]["text"]["content"])

    def test_the_card_is_a_valid_native_document(self):
        model = self._model(tokyo=[self._reading("CPU", "128")],
                            extras=[self._unknown()])
        card = card_period.render_period_card(model)
        self.assertEqual(card["schema"], "2.0")
        ids = []
        monitor._walk_element_ids(card, ids)
        self.assertEqual(len(ids), len(set(ids)))
        for eid in ids:
            self.assertLessEqual(len(eid), 20)
            self.assertTrue(eid[:1].isalpha())
        self.assertLessEqual(card_period._element_count(card["body"]) + 1,
                             card_period._NATIVE_MAX_ELEMENTS)


class TheKpiIsTypedAndNarrowSafe(unittest.TestCase):
    """The 18:32 desktop screenshot's follow-ups, each pinned by an assertion.

    The defect that started this: the tile read «CPU 128», which a reader takes
    for a utilization, when 128 is the LOGICAL CORE COUNT. The captions now come
    from a typed axis table, not from the reading's label, and a reading that
    contradicts its axis is dropped rather than mislabelled.
    """

    NOW = "2026-10-14T18:32:00+08:00"

    def _reading(self, label, value, *, unit="", note="", severity="OK"):
        return monitor.reading(label, value, unit=unit, note=note,
                               observed_at=self.NOW, severity=severity)

    def _device(self, ref, name, readings=None, *, state=monitor.STATE_KNOWN,
                platform="linux", reason=None):
        reach = {"state": state}
        if state == monitor.STATE_KNOWN:
            reach["observed_at"] = self.NOW
        if reason:
            reach["reason"] = reason
        return {"device_ref": ref, "display_name": name, "platform": platform,
                "reachability": reach, "readings": list(readings or []), "accounts": []}

    def _model(self, *, tokyo=(), extras=()):
        devices = [self._device("device:tokyo-shared-dev-01", "东京共享开发机", tokyo)] \
            + list(extras)
        return monitor.monitor_model(devices=devices, services=(), now=self.NOW)

    def _tiles(self, card):
        rows = [e for e in card["body"]["elements"] if e.get("tag") == "column_set"]
        return [c for r in rows for c in (r.get("columns") or [])]

    def _caption(self, tile):
        return re.sub(r"</?font[^>]*>", "", tile["elements"][1]["content"])

    def _value(self, tile):
        return tile["elements"][0]["content"]

    def test_a_core_count_is_captioned_as_cores_and_never_a_percentage(self):
        model = self._model(tokyo=[self._reading("CPU", "128 逻辑核")])
        card = card_period.render_period_card(model)
        tiles = self._tiles(card)
        self.assertEqual(len(tiles), 1)
        self.assertIn("逻辑核", self._caption(tiles[0]))
        self.assertIn("128", self._value(tiles[0]))
        blob = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("128%", blob)          # a count is not a percentage
        self.assertNotIn(">CPU<", blob)         # nor is it a bare "CPU"

    def test_a_real_utilization_earns_the_utilization_caption(self):
        model = self._model(tokyo=[self._reading("CPU", "12.5%")])
        tiles = self._tiles(card_period.render_period_card(model))
        self.assertIn("使用率", self._caption(tiles[0]))

    def test_the_percent_axes_keep_their_units_and_labels(self):
        model = self._model(tokyo=[self._reading("内存", "32.6%"),
                                   self._reading("交换区", "89.2%", note="阈值 90.0%"),
                                   self._reading("磁盘 /", "9.5%")])
        tiles = self._tiles(card_period.render_period_card(model))
        self.assertEqual([self._caption(t) for t in tiles], ["内存", "交换区", "磁盘"])
        for tile in tiles:
            self.assertIn("%", self._value(tile))

    def test_an_axis_with_no_reading_is_not_invented(self):
        card = card_period.render_period_card(
            self._model(tokyo=[self._reading("内存", "32.6%")]))
        self.assertEqual(len(self._tiles(card)), 1)
        self.assertNotIn("0%", json.dumps(card, ensure_ascii=False))
        self.assertNotIn("未获取</font>", json.dumps(card, ensure_ascii=False))

    def test_an_axis_whose_reading_contradicts_its_unit_is_dropped(self):
        # A byte count under the memory axis (declared `percent`) would put a
        # wrong caption under the number — the exact class of the CPU defect.
        card = card_period.render_period_card(
            self._model(tokyo=[self._reading("内存", "2048 MiB")]))
        self.assertEqual(self._tiles(card), [])

    def test_the_narrow_layout_uses_no_fixed_pixel_width(self):
        card = card_period.render_period_card(
            self._model(tokyo=[self._reading("内存", "32.6%")]))

        def walk(node):
            if isinstance(node, dict):
                if node.get("tag") == "column":
                    self.assertEqual(node.get("width"), "weighted")
                    self.assertNotIn("px", str(node.get("width")))
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)

        walk(card["body"])

    def test_colours_are_theme_tokens_not_raw_rgba(self):
        # Theme tokens (grey-50 / yellow-50 / blue) carry their own light/dark;
        # a raw colour would pin one theme and be unreadable in the other.
        blob = json.dumps(card_period.render_period_card(
            self._model(tokyo=[self._reading("内存", "32.6%")])), ensure_ascii=False)
        self.assertNotIn("rgba(", blob)
        self.assertNotIn("light_mode", blob)
        self.assertNotIn("dark_mode", blob)

    def test_the_swap_threshold_earns_the_yellow_tile_and_not_below_it(self):
        hot = self._model(tokyo=[self._reading("交换区", "89.2%", note="阈值 90.0%")])
        cool = self._model(tokyo=[self._reading("交换区", "40.0%")])
        self.assertEqual(self._tiles(card_period.render_period_card(hot))[0]["background_style"],
                         "yellow-50")
        self.assertEqual(self._tiles(card_period.render_period_card(cool))[0]["background_style"],
                         "grey-50")

    def test_the_server_verified_structure_does_not_regress(self):
        # schema 2.0 / two 2-column column_set / three folds — what the remote
        # `user_card_content` read-back confirmed. None of it may regress.
        model = self._model(
            tokyo=[self._reading("CPU", "128 逻辑核"), self._reading("内存", "32.6%"),
                   self._reading("交换区", "89.2%"), self._reading("磁盘 /", "9.5%")],
            extras=[self._device("device:work-fj", "FJPCOS 工作站", state=monitor.STATE_UNKNOWN,
                                 platform="windows", reason="无采集"),
                    self._device("device:llm-lab-dell", "A6000 推理机",
                                 state=monitor.STATE_UNKNOWN, reason="无采集")])
        card = card_period.render_period_card(model)
        self.assertEqual(card["schema"], "2.0")
        rows = [e for e in card["body"]["elements"] if e.get("tag") == "column_set"]
        self.assertEqual([len(r["columns"]) for r in rows], [2, 2])
        tags = []
        monitor._walk_tags(card["body"], tags)
        self.assertEqual(tags.count("collapsible_panel"), 3)
        self.assertNotIn("chart", tags)
        self.assertNotIn("table", tags)
        self.assertNotIn("corner_radius",
                         json.dumps([c for r in rows for c in r["columns"]]))
        self.assertEqual(card["header"]["icon"]["token"], card_period._HEADER_ICON)
        self.assertEqual(len(card["header"]["text_tag_list"]), 2)


class TheRowKeepsOnlyWhatItNames(unittest.TestCase):
    def test_a_row_carries_the_closed_set(self):
        period = card_period.period_at("2026-10-14T09:30:00+08:00", declaration=DECL)
        row = card_period.row(MODEL, period=period)
        self.assertEqual(set(row), set(card_period.ROW_FIELDS))
        self.assertEqual(row["tick"], 4)
        self.assertEqual(row["hhmm"], "09:30")
        self.assertEqual(set(row["sections"]), set(card_day.ROW_SECTIONS))

    def test_a_row_refuses_machine_detail(self):
        bad = {k: None for k in card_period.ROW_FIELDS}
        bad.update({"tick": 1, "hhmm": "09:00", "verdict": "OK",
                    "sections": {n: "OK" for n in card_day.ROW_SECTIONS},
                    "gaps": 0, "local": None, "observation": "OK",
                    "pid": 1})
        with self.assertRaises(card_period.CardPeriodError):
            card_period.check_row(bad)


class TheArchiveIsTheFourBlocks(unittest.TestCase):
    def setUp(self):
        self.model = source.model_from_fixture()
        self.period = card_period.period_at("2026-10-14T09:30:00+08:00",
                                            declaration=DECL)
        self.rows = [
            card_period.row(MODEL, period={**self.period, "tick": 1}),
            card_period.row(MODEL, period={**self.period, "tick": 2}),
        ]

    def test_the_archive_names_overview_trend_devices_and_handover(self):
        view = card_period.archive(
            self.model, period=self.period, rows=self.rows,
            incidents=({"episode": "e1", "says": "内存压力"},),
            recoveries=({"episode": "e1", "says": "已恢复"},),
            unresolved=({"key": "k1", "says": "磁盘待清理"},))
        self.assertEqual(set(view),
                         {"schema", "period_ref", "label", "day_ref", "overview",
                          "trend", "devices", "history", "handover"})
        self.assertGreaterEqual(len(view["devices"]), 1)
        text = card_period.render_archive_text(view)
        for block in ("时段小结", "完整时段趋势一览", "三设备摘要",
                      "交接 · 事故 / 自愈 / 未解决"):
            self.assertIn(block, text)
        self.assertIn("内存压力", text)
        self.assertIn("磁盘待清理", text)

    def test_the_final_card_keeps_the_fleet_ia_and_opens_no_chart(self):
        view = card_period.archive(self.model, period=self.period, rows=self.rows)
        card = card_period.render_archive(view, model=self.model)
        self.assertEqual(card["config"], {"update_multi": True,
                                          "width_mode": "default"})
        # The V13 three-device folds are the fleet renderer's own elements,
        # lifted — the archive is written above them, not instead of them.
        tags = []
        card_period.monitor._walk_tags(card["body"], tags)
        self.assertIn("collapsible_panel", tags)
        # ...and no chart, because none has been verified on a real client.
        self.assertNotIn("chart", tags)

    def test_the_renderer_refuses_to_claim_a_verified_chart(self):
        view = card_period.archive(self.model, period=self.period, rows=self.rows)
        with self.assertRaises(card_period.CardPeriodError):
            card_period.render_archive(view, model=self.model, charts_verified=True)


class TheTableIsDeclared(unittest.TestCase):
    def test_a_caller_may_replace_the_table_whole(self):
        custom = {
            "tick_seconds": 300,
            "workday": [
                {"name": "day", "label": "白班", "start": "08:00", "end": "20:00"},
                {"name": "night", "label": "夜班", "start": "20:00", "end": "08:00"},
            ],
        }
        tbl = card_period.table(custom)
        self.assertEqual(tbl["tick_seconds"], 300)
        fact = card_period.period_at("2026-10-14T09:00:00+08:00", declaration=DECL,
                                     table_=tbl)
        self.assertEqual(fact["name"], "day")
        self.assertEqual(fact["label"], "白班")
        # Five-minute ticks: 09:00 is slot 12 of a window that opened at 08:00.
        self.assertEqual(fact["tick"], 13)

    def test_a_malformed_table_is_refused(self):
        with self.assertRaises(card_period.CardPeriodError):
            card_period.table({"tick_seconds": 0})
        with self.assertRaises(card_period.CardPeriodError):
            card_period.table({"workday": [{"label": "无名"}]})
        with self.assertRaises(card_period.CardPeriodError):
            card_period.table({"workday": [{"name": "x", "start": "9:0",
                                            "end": "10:00"}]})


if __name__ == "__main__":
    unittest.main()
