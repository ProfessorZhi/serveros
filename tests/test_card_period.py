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

    def test_the_boundary_closes_the_old_shift_and_opens_the_new_one(self):
        at = "2026-10-14T11:50:00+08:00"
        _periods(self.store, at, self._row_for)
        self.store.announce(remote_id="om_morning", at=at)

        def finalize(step):
            rows = self.store.summary()["rows"]
            rows = rows + [self.store.summary()["latest"]]
            view = card_period.archive(self.model, period=step["period"],
                                       rows=rows)
            card = card_period.render_archive(view, model=self.model)
            return {"archive": view,
                    "frozen": card_period.frozen_block(
                        card=card, at="2026-10-14T12:00:00+08:00",
                        period_ref=step["period_ref"])}

        at2 = "2026-10-14T12:00:00+08:00"
        answer = self.store.advance(
            period=card_period.period_at(at2, declaration=DECL), at=at2,
            row_for=self._row_for, finalize=finalize)
        # Two steps, and in this order: the morning shift's archive must be
        # written while its rows are still correct, before the afternoon opens.
        self.assertEqual([s["action"] for s in answer["decision"]["steps"]],
                         [card_period.FINAL, card_period.CREATE])
        doc = self.store.read()
        self.assertEqual(doc["previous"]["period_ref"], "CN-Test/2026-10-14#morning")
        self.assertIsNotNone(doc["previous"]["frozen"])
        self.assertEqual(doc["previous"]["frozen"]["kind"], card_delivery.PERIOD)
        self.assertEqual(doc["open"]["period_ref"], "CN-Test/2026-10-14#afternoon")

    def test_a_frozen_period_is_not_patched_again(self):
        at = "2026-10-14T09:30:00+08:00"
        ref = card_period.period_at(at, declaration=DECL)["period_ref"]
        _periods(self.store, at, self._row_for)
        self.store.announce(remote_id="om_a", at=at)
        # 收班: an explicit close of THIS period, rather than waiting for the
        # next period's first tick to detect the boundary.
        closed = self.store.advance(
            period=card_period.period_at(at, declaration=DECL), at=at,
            row_for=self._row_for, closing=ref,
            finalize=lambda step: {"frozen": {"kind": card_delivery.PERIOD,
                                              "at": at, "version": "v",
                                              "payload_hash": "h"}},
        )
        self.assertEqual(closed["decision"]["action"], card_period.FINAL)
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
                          "trend", "devices", "handover"})
        self.assertGreaterEqual(len(view["devices"]), 1)
        text = card_period.render_archive_text(view)
        for block in ("总览概括", "完整时段趋势一览", "三设备摘要",
                      "交接：事故 / 自愈 / 未解决"):
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
