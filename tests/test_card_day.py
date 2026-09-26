#!/usr/bin/env python3
"""§11 — one card per workday, patched at the hour, and the hours it folded.

§11 is four sentences, and each of them is a thing that can be got wrong in a
way nobody notices until the chat history is already wrong:

    每个 WORKDAY 在首个允许的播报时点创建一张 Daily Status Card
    08:00 错过可在后续工作窗口创建，之后整点只 PATCH，不追发已错过的所有小时
    每次更新先把旧 latest 压缩成 compact history row，再写新 latest
    20:00 完成最后一次 routine patch 后当日卡不再因普通采样变化，次一 WORKDAY 新建

So the tests below are grouped by which sentence they hold. V28 is the
acceptance criterion for the second and the fourth — 「同一工作日 08:00–20:00
**整点**只 patch 一张 Status Card，下一工作日新建」 — and it is written as ONE
workday walked from 08:00 to 20:00 in `TheWorkdayIsThirteenPatchesOfOneCard`,
because a criterion about a cadence is only tested by running the cadence.

WHAT IS NOT IN THIS FILE
------------------------
No clock and no transport, because the module has neither: every instant here is
written down, and nothing in `runtime/card_day.py` reads a clock or sends
anything. The years are SYNTHETIC for the reason `tests/test_business_calendar.py`
gives at length, and one test below asserts a weekday against the stdlib first,
because the fixture's point is that the declaration — not the calendar — decides.
"""
from __future__ import annotations

import contextlib
import datetime
import importlib.util
import io
import json
import shutil
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


#: Loaded under the names `runtime/cli.py::_card_modules` resolves, so the CLI
#: tests below drive the same module objects the command does — including
#: `card_source.gather`, which is the seam they stub.
business_calendar = _load("serveros_runtime_business_calendar",
                          ROOT / "runtime" / "business_calendar.py")
card_day = _load("serveros_runtime_card_day", ROOT / "runtime" / "card_day.py")
card_delivery = _load("serveros_runtime_card_delivery",
                      ROOT / "runtime" / "card_delivery.py")
presentation = _load("serveros_runtime_presentation",
                     ROOT / "runtime" / "presentation.py")
source = _load("serveros_runtime_card_source", ROOT / "runtime" / "card_source.py")
cli = _load("serveros_runtime_cli_for_cards", ROOT / "runtime" / "cli.py")

YEAR = 2031
OFFICIAL_WORK_SATURDAY = "2031-06-07"   # a workday although it is a Saturday
OFFICIAL_REST_MONDAY = "2031-06-09"     # a rest day although it is a Monday
PLAIN_WORKDAY = "2031-06-10"            # covered, in neither list
NEXT_WORKDAY = "2031-06-11"

CALENDAR = """\
schema: serveros/business-calendar.v1
jurisdiction: CN-Mainland
timezone: Asia/Shanghai
source:
  document_id: synthetic-test-notice-2031
  revision: synthetic-rev-1
years:
  "2031":
    workdays: ["2031-06-07"]
    non_working_days: ["2031-06-09"]
imported_at: "2030-12-01T00:00:00+08:00"
verified_at: "2030-12-01T00:00:00+08:00"
"""

_READING = None
_CALENDAR_DIR = None


def reading() -> dict:
    """`read_declaration`'s own answer, from one synthetic file written once.

    The real reader rather than a hand-built mapping, so every test below is
    about the schedule the node would actually compute — and `setUpModule`
    removes the file when the module is done.
    """
    global _READING, _CALENDAR_DIR
    if _READING is None:
        _CALENDAR_DIR = Path(tempfile.mkdtemp(prefix="card-day-calendar-"))
        path = _CALENDAR_DIR / "business-calendar.yaml"
        path.write_text(CALENDAR, encoding="utf-8")
        _READING = business_calendar.read_declaration(path)
    return _READING


def setUpModule():
    reading()


def tearDownModule():
    global _READING, _CALENDAR_DIR
    if _CALENDAR_DIR is not None:
        shutil.rmtree(_CALENDAR_DIR, ignore_errors=True)
    _READING, _CALENDAR_DIR = None, None


def at(day, hour, minute=0, second=0, offset="+08:00") -> str:
    return f"{day}T{hour:02d}:{minute:02d}:{second:02d}{offset}"


def schedule(instant, *, reading_=None) -> dict:
    """The window this instant falls in, computed the way the node computes it."""
    return business_calendar.routine_window(instant,
                                            reading=reading_ or reading())


def no_calendar(instant) -> dict:
    """The same call on a node that has imported no calendar."""
    return business_calendar.routine_window(
        instant, reading=business_calendar.read_declaration(
            Path("/nonexistent/business-calendar.yaml")))


def model(*, verdict="OK", observation="新鲜", gaps=(), states=None, **over) -> dict:
    """One status model, small enough to read, carrying nothing about anybody."""
    states = dict({"mine": "OK", "host": "OK", "gateway": "UNBOUND"},
                  **(states or {}))
    document = {
        "env_health": {"verdict": verdict, "observation": observation,
                       "gaps": list(gaps)},
        "sections": {name: {"state": state} for name, state in states.items()},
    }
    document.update(over)
    return document


# ---------------------------------------------------------------------------
# the row is a closed set of fields
# ---------------------------------------------------------------------------

class TheRowKeepsWhatTheCardSaidAndNothingElse(unittest.TestCase):
    """§11: a history row keeps 关键人读数字和状态 and copies no PID/code/raw probe.

    A rule that TRIMS would have to know every machine-detail field the model
    will ever grow; a row that can only be built from an allowlist cannot carry
    one at all. The second is what these tests hold, because the first is a
    promise and the second is a property of the document.
    """

    def test_the_row_sections_are_the_cards_own_three_blocks(self):
        """Checked against the renderer rather than asserted in a comment: a
        fourth block on the card would otherwise go missing from every row."""
        self.assertEqual(tuple(presentation.SECTIONS), card_day.ROW_SECTIONS)

    def test_a_row_cannot_carry_a_field_outside_the_allowlist(self):
        candidate = row_of(schedule(at(PLAIN_WORKDAY, 9)))
        candidate["pid"] = 4242
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.check_row(candidate)
        self.assertIn("pid", str(caught.exception))

    def test_a_row_missing_a_field_is_refused(self):
        """`hhmm` rather than `gaps`, and the choice is the point: some fields
        have a second check that catches their absence whatever this one does,
        and the ones that do not are the ones this rule is load-bearing for."""
        for field in ("hhmm", "local", "observation"):
            candidate = row_of(schedule(at(PLAIN_WORKDAY, 9)))
            del candidate[field]
            with self.assertRaises(card_day.CardDayError, msg=field) as caught:
                card_day.check_row(candidate)
            self.assertIn(field, str(caught.exception))

    def test_a_row_naming_a_section_that_is_not_one_of_the_three_is_refused(self):
        candidate = row_of(schedule(at(PLAIN_WORKDAY, 9)))
        candidate["sections"]["docker"] = "OK"
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.check_row(candidate)
        self.assertIn("docker", str(caught.exception))

    def test_a_row_with_no_verdict_is_refused(self):
        """A row that leads with nothing is a row the folded view cannot be
        read down its left edge."""
        candidate = row_of(schedule(at(PLAIN_WORKDAY, 9)))
        candidate["verdict"] = ""
        with self.assertRaises(card_day.CardDayError):
            card_day.check_row(candidate)

    def test_the_gap_count_is_a_count_and_not_the_gap_text(self):
        candidate = row_of(schedule(at(PLAIN_WORKDAY, 9)))
        candidate["gaps"] = ["summary（…）未读到：x"]
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.check_row(candidate)
        self.assertIn("observation", str(caught.exception))

    def test_the_tick_is_an_ordinal_and_not_a_clock_reading(self):
        candidate = row_of(schedule(at(PLAIN_WORKDAY, 9)))
        candidate["tick"] = "09:00"
        with self.assertRaises(card_day.CardDayError):
            card_day.check_row(candidate)

    def test_extracting_a_row_copies_no_machine_detail(self):
        """The model is given a detail block and a probe payload, and neither
        reaches the row — asserted over the serialised row, so a nested copy
        would fail it too."""
        full = model(verdict="ATTENTION", states={"gateway": "DEGRADED"},
                     detail={"pid": 4242, "raw_probe": "cat /proc/4242/cmdline"},
                     probes=[{"code": "SRV-0009", "value": 4242}])
        row_ = card_day.row(full, schedule=schedule(at(PLAIN_WORKDAY, 9)))
        text = json.dumps(row_, ensure_ascii=False, sort_keys=True)
        self.assertNotIn("4242", text)
        self.assertNotIn("raw_probe", text)
        self.assertNotIn("SRV-0009", text)
        self.assertEqual(sorted(row_), sorted(card_day.ROW_FIELDS))

    def test_the_row_takes_its_hour_from_the_calendar_and_not_the_machine(self):
        """§9 puts the timezone in the declaration. A row stamped in the
        machine's zone would disagree with the window it claims to be inside."""
        sched = schedule(at(PLAIN_WORKDAY, 9))
        row_ = card_day.row(model(), schedule=sched)
        self.assertEqual(row_["hhmm"], "09:00")
        self.assertEqual(row_["tick"], 2)
        self.assertEqual(row_["local"], sched["local"])
        self.assertIn("+08:00", row_["local"])

    def test_a_row_without_an_hour_is_refused_rather_than_stamped(self):
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.row(model(), schedule={"tick": None, "hour": None,
                                            "minute": None})
        self.assertIn("tick", str(caught.exception))

    def test_one_history_line_reads_left_to_right(self):
        row_ = card_day.row(model(verdict="UNCONFIRMED", gaps=["x"],
                                  states={"gateway": None}),
                            schedule=schedule(at(PLAIN_WORKDAY, 10)))
        says = card_day.row_says(row_)
        self.assertTrue(says.startswith("10:00 UNCONFIRMED"), says)
        self.assertIn("mine=OK", says)
        self.assertIn("gateway=UNKNOWN", says)
        self.assertIn("采集缺口 1", says)


def row_of(sched: dict, **over) -> dict:
    """One valid row for a test to bend, built the way the module builds it."""
    return card_day.row(model(**over), schedule=sched)


# ---------------------------------------------------------------------------
# §11 sentence 1 and 2: when the day's card is created, and what "not backfilled"
# means
# ---------------------------------------------------------------------------

class TheDaysCardIsCreatedAtTheFirstAllowedTick(unittest.TestCase):

    def test_the_first_allowed_tick_creates_the_card(self):
        answer = card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 8)),
                                 at=at(PLAIN_WORKDAY, 8))
        self.assertEqual(answer["action"], card_day.CREATE)
        self.assertEqual(answer["reason"], card_day.NEW_WORKDAY)
        self.assertEqual(answer["tick"], 1)
        self.assertEqual(answer["missed_ticks"], 0)
        self.assertFalse(answer["rollover"])
        self.assertIn("今天还没有卡", answer["says"])

    def test_a_late_first_tick_creates_and_does_not_backfill_the_missed_hours(self):
        """§11: 「错过 08:00 可在后续工作窗口创建」 and 「不追发已错过的所有小时」.
        Both halves matter: creating late is allowed, and creating late does not
        conjure the hours that were skipped."""
        answer = card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 11)),
                                 at=at(PLAIN_WORKDAY, 11))
        self.assertEqual(answer["action"], card_day.CREATE)
        self.assertEqual(answer["missed_ticks"], 3)
        self.assertIn("早先的 3 个时点不追发", answer["says"])

    def test_the_missed_count_is_recorded_on_the_card_it_creates(self):
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 11)),
                      at=at(PLAIN_WORKDAY, 11), model=model())
        day = store.summary()
        self.assertEqual(day["missed_ticks"], 3)
        self.assertEqual(day["updates"], 1)
        self.assertIn("未追发", day["says"])

    def test_a_tick_behind_the_cards_own_is_refused_rather_than_written(self):
        """A clock that stepped backwards, or a caller replaying an earlier
        hour. Letting it through moves the latest backwards, so the NEXT real
        tick folds the wrong hour into the history and the panel a person opens
        lists the day out of order — the hour would be recorded wrongly once and
        read wrongly after that."""
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 11)),
                      at=at(PLAIN_WORKDAY, 11), model=model())
        settled = json.dumps(store.read(), sort_keys=True)
        result = store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                               at=at(PLAIN_WORKDAY, 9), model=model())
        self.assertFalse(result["wrote"])
        self.assertEqual(result["decision"]["reason"], card_day.OUT_OF_ORDER)
        self.assertIn("11", result["decision"]["says"])
        self.assertEqual(json.dumps(store.read(), sort_keys=True), settled)
        # …and the fold that follows is still the hour after the last one.
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 12)),
                      at=at(PLAIN_WORKDAY, 12), model=model())
        self.assertEqual([r["hhmm"] for r in store.read()["open"]["history"]],
                         ["11:00"])

    def test_the_hour_the_card_is_on_is_still_patchable(self):
        """The refusal is about going BACKWARDS. The tick the card is already
        on is the same tick, and a second look inside that hour replaces it."""
        store = temp_store(self)
        for _ in range(2):
            store.advance(schedule=schedule(at(PLAIN_WORKDAY, 11)),
                          at=at(PLAIN_WORKDAY, 11), model=model())
        self.assertEqual(store.summary()["updates"], 1)
        self.assertEqual(store.read()["open"]["latest"]["hhmm"], "11:00")

    def test_the_backwards_refusal_is_decided_and_not_improvised(self):
        sched = schedule(at(PLAIN_WORKDAY, 9))
        document = card_day.apply(
            {}, action=card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 11)),
                                       at=at(PLAIN_WORKDAY, 11)),
            row_=row_of(schedule(at(PLAIN_WORKDAY, 11))),
            at=at(PLAIN_WORKDAY, 11))
        answer = card_day.decide(document, schedule=sched, at=at(PLAIN_WORKDAY, 9))
        self.assertEqual(answer["action"], card_day.SKIP)
        self.assertEqual(answer["reason"], card_day.OUT_OF_ORDER)
        self.assertIn(card_day.OUT_OF_ORDER, card_day.REASONS)

    def test_a_rest_day_creates_nothing_and_backfills_nothing(self):
        answer = card_day.decide({}, schedule=schedule(at(OFFICIAL_REST_MONDAY, 9)),
                                 at=at(OFFICIAL_REST_MONDAY, 9))
        self.assertEqual(answer["action"], card_day.SKIP)
        self.assertEqual(answer["reason"], card_day.NOT_A_WORKDAY)
        self.assertEqual(answer["day_ref"], f"CN-Mainland/{OFFICIAL_REST_MONDAY}")
        self.assertIn("不创建空白状态卡", answer["says"])

    def test_a_declared_working_saturday_does_get_a_card(self):
        """The premise first: the stdlib agrees this is a Saturday, so what the
        test measures is the declaration overriding the weekday derivation."""
        self.assertEqual(datetime.date(2031, 6, 7).weekday(), 5)
        answer = card_day.decide({}, schedule=schedule(at(OFFICIAL_WORK_SATURDAY, 8)),
                                 at=at(OFFICIAL_WORK_SATURDAY, 8))
        self.assertEqual(answer["action"], card_day.CREATE)

    def test_without_a_calendar_there_is_no_day_to_file_a_card_under(self):
        """§15's default, carried to the card: no calendar, no routine card.
        The identity is also the dedupe key, so `None` would collide across
        every node and every day at once."""
        unreadable = no_calendar(at(PLAIN_WORKDAY, 9))
        answer = card_day.decide({}, schedule=unreadable, at=at(PLAIN_WORKDAY, 9))
        self.assertEqual(answer["action"], card_day.SKIP)
        self.assertEqual(answer["reason"], card_day.CALENDAR_UNKNOWN)
        self.assertIsNone(answer["day_ref"])
        # V42's word, carried from the schedule this answer is built on: the
        # refusal does not get to paraphrase the diagnosis it was handed.
        self.assertIn("calendar=UNKNOWN", unreadable["says"])
        self.assertIn("事故卡另按 P1/P2 策略", answer["says"])

    def test_before_the_window_the_card_waits_for_the_first_allowed_tick(self):
        answer = card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 7)),
                                 at=at(PLAIN_WORKDAY, 7))
        self.assertEqual(answer["action"], card_day.SKIP)
        self.assertEqual(answer["reason"], card_day.BEFORE_WINDOW)
        self.assertIn("进入窗口后的第一个整点建卡", answer["says"])

    def test_after_the_window_the_day_is_closed_to_routine_updates(self):
        answer = card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 21)),
                                 at=at(PLAIN_WORKDAY, 21))
        self.assertEqual(answer["action"], card_day.SKIP)
        self.assertEqual(answer["reason"], card_day.AFTER_WINDOW)
        self.assertIn("当日卡不再因普通采样变化", answer["says"])

    def test_the_window_ends_after_the_last_routine_patch_not_at_it(self):
        """§10's window is inclusive at both ends, so 20:00 is the thirteenth
        patch and 20:00:01 is after the window — one second apart, one update
        apart, and the same day."""
        last = schedule(at(PLAIN_WORKDAY, 20))
        self.assertTrue(last["in_window"])
        self.assertEqual(last["tick"], 13)
        self.assertEqual(card_day.decide({}, schedule=last,
                                         at=at(PLAIN_WORKDAY, 20))["reason"],
                         card_day.NEW_WORKDAY)
        after = schedule(at(PLAIN_WORKDAY, 20, 0, 1))
        self.assertFalse(after["in_window"])
        self.assertEqual(card_day.decide({}, schedule=after,
                                         at=at(PLAIN_WORKDAY, 20, 0, 1))["reason"],
                         card_day.AFTER_WINDOW)

    def test_a_sample_inside_the_hour_is_that_hours_own_tick(self):
        """V28's 「整点只 patch 一张」 is about the ROW and not the second.

        §11 sentence 4 has the card stop changing after 20:00 「不再因普通采样
        变化」, which says ordinary sampling does change it inside the window;
        and `routine_window` says the hour is 推荐 — a tick a minute late is
        still the same tick. Both 09:00 and 09:30 are tick 2, so the second look
        replaces the hour rather than adding a row to it, and enforcing the hour
        on the clock instead would freeze the card of a timer that drifts off
        the second.
        """
        first = card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 9)),
                                at=at(PLAIN_WORKDAY, 9))
        later = card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 9, 30)),
                                at=at(PLAIN_WORKDAY, 9, 30))
        self.assertEqual(first["tick"], 2)
        self.assertEqual(later["tick"], 2)
        self.assertEqual(later["action"], first["action"])
        self.assertEqual(later["reason"], first["reason"])
        self.assertEqual(later["says"], first["says"])
        self.assertNotIn("NOT_A_TICK", card_day.REASONS)

    def test_a_drifting_timer_still_advances_the_card(self):
        """The failure this prevents is silent: a timer firing at :20 past the
        hour would leave the day's card reading 08:00 all day while the node is
        fine — a stale surface that looks like a healthy one."""
        store = temp_store(self)
        for hour, minute in ((8, 20), (9, 20), (10, 20)):
            store.advance(schedule=schedule(at(PLAIN_WORKDAY, hour, minute)),
                          at=at(PLAIN_WORKDAY, hour, minute), model=model())
        day = store.read()["open"]
        self.assertEqual(day["latest"]["hhmm"], "10:20")
        self.assertEqual([r["hhmm"] for r in day["history"]],
                         ["08:20", "09:20"])
        self.assertEqual(store.summary()["updates"], 3)

    def test_a_ticks_ordinal_is_not_believed_beyond_the_thirteen(self):
        sched = dict(schedule(at(PLAIN_WORKDAY, 9)), tick=14)
        answer = card_day.decide({}, schedule=sched, at=at(PLAIN_WORKDAY, 9))
        self.assertEqual(answer["action"], card_day.SKIP)
        self.assertEqual(answer["reason"], card_day.SCHEDULE_UNREADABLE)
        self.assertIn("14", answer["says"])

    def test_a_schedule_reason_nobody_explained_is_not_read_as_an_answer(self):
        """An out-of-window schedule this module does not recognise might mean
        anything, and acting on a reason it cannot name would be acting on a
        sentence it did not read."""
        sched = dict(schedule(at(PLAIN_WORKDAY, 9)), in_window=False,
                     reason="SOMETHING_NEW")
        answer = card_day.decide({}, schedule=sched, at=at(PLAIN_WORKDAY, 9))
        self.assertEqual(answer["action"], card_day.SKIP)
        self.assertEqual(answer["reason"], card_day.SCHEDULE_UNREADABLE)
        self.assertNotIn("SOMETHING_NEW", answer["reason"])

    def test_the_ceiling_is_the_windows_own_thirteen(self):
        """A literal here would be the second place the number lives."""
        self.assertEqual(card_day.ticks_per_workday(),
                         business_calendar.ROUTINE_UPDATES_PER_WORKDAY)
        self.assertEqual(card_day.ticks_per_workday(), 13)
        self.assertEqual(card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 9)),
                                         at=at(PLAIN_WORKDAY, 9))["ticks_per_workday"],
                         13)

    def test_deciding_writes_nothing_and_changes_no_document(self):
        document = {"schema": card_day.SCHEMA, "open": None, "previous": None}
        before = json.dumps(document, sort_keys=True)
        card_day.decide(document, schedule=schedule(at(PLAIN_WORKDAY, 9)),
                        at=at(PLAIN_WORKDAY, 9))
        self.assertEqual(json.dumps(document, sort_keys=True), before)

    def test_the_reason_is_a_word_from_the_modules_own_vocabulary(self):
        """A reason nobody can name is a reason nobody can act on."""
        for instant in (at(PLAIN_WORKDAY, 7), at(PLAIN_WORKDAY, 9),
                        at(PLAIN_WORKDAY, 9, 30), at(PLAIN_WORKDAY, 21),
                        at(OFFICIAL_REST_MONDAY, 9)):
            answer = card_day.decide({}, schedule=schedule(instant), at=instant)
            self.assertIn(answer["reason"], card_day.REASONS)
            self.assertIn(answer["action"], card_day.ACTIONS)


# ---------------------------------------------------------------------------
# §11 sentence 3: the previous latest becomes a row before the new one is written
# ---------------------------------------------------------------------------

class EveryUpdateFoldsTheHourItReplaces(unittest.TestCase):

    def test_a_second_hour_folds_the_first_into_the_history(self):
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model(verdict="OK"))
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 10)),
                      at=at(PLAIN_WORKDAY, 10),
                      model=model(verdict="ATTENTION"))
        day = store.read()["open"]
        self.assertEqual([r["hhmm"] for r in day["history"]], ["09:00"])
        self.assertEqual(day["history"][0]["verdict"], "OK")
        self.assertEqual(day["latest"]["hhmm"], "10:00")
        self.assertEqual(day["latest"]["verdict"], "ATTENTION")
        self.assertEqual(store.summary()["updates"], 2)

    def test_the_history_row_is_what_the_card_said_and_not_a_later_reading(self):
        """The row comes from the model the same invocation rendered, so a store
        advanced at a quiet hour keeps saying the hour was quiet even after the
        machine has changed."""
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model(verdict="OK"))
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 10)),
                      at=at(PLAIN_WORKDAY, 10),
                      model=model(verdict="CRITICAL", states={"host": "CRITICAL"}))
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 11)),
                      at=at(PLAIN_WORKDAY, 11), model=model())
        history = {r["hhmm"]: r for r in store.read()["open"]["history"]}
        self.assertEqual(history["09:00"]["verdict"], "OK")
        self.assertEqual(history["09:00"]["sections"]["host"], "OK")

    def test_folding_never_grows_a_day_past_its_own_hours(self):
        """The ceiling is enforced on the way in as well as on the way out: a
        document already holding a workday's thirteen hours folds to thirteen,
        not fourteen — so a fold can never write a file the next read refuses.

        The input here is deliberately one the cadence cannot produce — thirteen
        hours AND a latest is fourteen looks at a thirteen-hour day — because a
        cadence that behaved would never ask this question. What is being held
        is that the fold's answer stays readable whatever it is handed.
        """
        rows = [card_day.row(model(), schedule=schedule(at(PLAIN_WORKDAY, hour)))
                for hour in range(8, 21)]
        open_day = {"day_ref": f"CN-Mainland/{PLAIN_WORKDAY}", "kind": "WORKDAY",
                    "opened_at": at(PLAIN_WORKDAY, 8),
                    "updated_at": at(PLAIN_WORKDAY, 20), "missed_ticks": 0,
                    "latest": dict(rows[-1], tick=5, hhmm="12:00"),
                    "history": rows}
        folded = card_day.fold(open_day,
                               card_day.row(model(), schedule=schedule(at(PLAIN_WORKDAY, 14))),
                               at=at(PLAIN_WORKDAY, 14))
        self.assertEqual(len(folded["history"]), card_day.ticks_per_workday())
        self.assertEqual(folded["history"][0]["tick"], 2,
                         "the fold keeps the newest thirteen and drops the oldest")
        # …and the document the fold produced is one the reader accepts.
        directory = Path(tempfile.mkdtemp(prefix="card-day-ceiling-"))
        self.addCleanup(shutil.rmtree, directory, ignore_errors=True)
        path = directory / "day.json"
        path.write_text(json.dumps({"schema": card_day.SCHEMA, "open": folded,
                                    "previous": None}), encoding="utf-8")
        self.assertEqual(len(card_day.DayStore(path).read()["open"]["history"]),
                         card_day.ticks_per_workday())

    def test_a_second_update_inside_the_same_hour_replaces_that_hour(self):
        """§11 keeps the history 「按小时」: two rows for one hour would be the
        card disagreeing with its own cadence."""
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model(verdict="OK"))
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9, 0, 40)),
                      at=at(PLAIN_WORKDAY, 9, 0, 40),
                      model=model(verdict="ATTENTION"))
        day = store.read()["open"]
        self.assertEqual(day["history"], [])
        self.assertEqual(day["latest"]["verdict"], "ATTENTION")
        self.assertEqual(store.summary()["updates"], 1)

    def test_a_repeat_of_the_same_tick_leaves_the_document_as_it_was(self):
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model())
        first = json.dumps(store.read(), sort_keys=True)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model())
        self.assertEqual(json.dumps(store.read(), sort_keys=True), first)

    def test_a_skip_writes_nothing_and_records_no_row(self):
        """A SKIP that carried a row would grow the day a history of hours it
        deliberately did not report."""
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model())
        before = store.path.read_bytes()
        result = store.advance(schedule=schedule(at(PLAIN_WORKDAY, 21)),
                               at=at(PLAIN_WORKDAY, 21), model=model())
        self.assertFalse(result["wrote"])
        self.assertIsNone(result.get("row"))
        self.assertEqual(result["decision"]["reason"], card_day.AFTER_WINDOW)
        self.assertEqual(store.path.read_bytes(), before)

    def test_a_skip_that_arrived_with_a_row_is_refused(self):
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.apply({}, action={"action": card_day.SKIP, "reason": "x"},
                           row_=row_of(schedule(at(PLAIN_WORKDAY, 9))))
        self.assertIn("SKIP", str(caught.exception))

    def test_an_action_nobody_can_name_is_refused(self):
        """The document it is handed is one every OTHER guard would accept — an
        open card for the same day and a row to write — so what this test
        measures is the vocabulary check and not the guard behind it. Without
        it, «MAYBE» would arrive as a patch and be read as «no change»."""
        document = card_day.apply(
            {}, action=card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 9)),
                                       at=at(PLAIN_WORKDAY, 9)),
            row_=row_of(schedule(at(PLAIN_WORKDAY, 9))), at=at(PLAIN_WORKDAY, 9))
        for action in ({"action": "MAYBE", "day_ref": f"CN-Mainland/{PLAIN_WORKDAY}",
                        "day_kind": "WORKDAY"},
                       {"action": None, "day_ref": f"CN-Mainland/{PLAIN_WORKDAY}"}):
            with self.assertRaises(card_day.CardDayError) as caught:
                card_day.apply(document, action=action,
                               row_=row_of(schedule(at(PLAIN_WORKDAY, 10))),
                               at=at(PLAIN_WORKDAY, 10))
            self.assertIn(repr(action["action"]), str(caught.exception))
        # …and the same is refused when there is nothing to patch either, so the
        # refusal does not depend on the document being well formed.
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.apply({}, action={"action": "MAYBE"})
        self.assertIn("MAYBE", str(caught.exception))

    def test_a_patch_with_no_card_to_patch_is_refused(self):
        """A patch that invented its card would invent every field of it — when
        it opened, how many hours it missed. `decide` never asks for this, so
        the guard is exercised where it lives: on a document handed to `apply`."""
        open_day = card_day.apply(
            {}, action=card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 9)),
                                       at=at(PLAIN_WORKDAY, 9)),
            row_=row_of(schedule(at(PLAIN_WORKDAY, 9))), at=at(PLAIN_WORKDAY, 9))
        action = card_day.decide(open_day, schedule=schedule(at(PLAIN_WORKDAY, 10)),
                                 at=at(PLAIN_WORKDAY, 10))
        self.assertEqual(action["action"], card_day.PATCH)
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.apply({"open": None}, action=action,
                           row_=row_of(schedule(at(PLAIN_WORKDAY, 10))),
                           at=at(PLAIN_WORKDAY, 10))
        self.assertIn("no card for today", str(caught.exception))

    def test_a_patch_against_another_days_card_is_refused(self):
        """Two days becoming one card is the failure the day identity is for.
        `decide` never produces this — a different day is a CREATE — so the
        guard is exercised where it lives, on a document handed to `apply`."""
        document = card_day.apply(
            {}, action=card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 9)),
                                       at=at(PLAIN_WORKDAY, 9)),
            row_=row_of(schedule(at(PLAIN_WORKDAY, 9))), at=at(PLAIN_WORKDAY, 9))
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.apply(
                document,
                action={"action": card_day.PATCH, "day_ref": f"CN-Mainland/{NEXT_WORKDAY}",
                        "day_kind": "WORKDAY"},
                row_=row_of(schedule(at(NEXT_WORKDAY, 9))), at=at(NEXT_WORKDAY, 9))
        text = str(caught.exception)
        self.assertIn(PLAIN_WORKDAY, text)
        self.assertIn(NEXT_WORKDAY, text)

    def test_a_create_over_a_day_that_already_has_a_card_is_refused(self):
        """V28's one card per workday, enforced on the document as well as the
        decision: two creations in one day is the second chat message."""
        document = card_day.apply(
            {}, action=card_day.decide({}, schedule=schedule(at(PLAIN_WORKDAY, 9)),
                                       at=at(PLAIN_WORKDAY, 9)),
            row_=row_of(schedule(at(PLAIN_WORKDAY, 9))), at=at(PLAIN_WORKDAY, 9))
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.apply(
                document,
                action={"action": card_day.CREATE, "day_ref": f"CN-Mainland/{PLAIN_WORKDAY}",
                        "day_kind": "WORKDAY"},
                row_=row_of(schedule(at(PLAIN_WORKDAY, 10))), at=at(PLAIN_WORKDAY, 10))
        self.assertIn("already has a card", str(caught.exception))

    def test_advancing_needs_the_model_whose_hour_it_is(self):
        store = temp_store(self)
        before = store.path.exists()
        with self.assertRaises(card_day.CardDayError) as caught:
            store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                          at=at(PLAIN_WORKDAY, 9), model=None)
        self.assertIn("model", str(caught.exception))
        self.assertEqual(store.path.exists(), before)

    def test_the_history_never_grows_past_the_workdays_hours(self):
        store = temp_store(self)
        for hour in range(8, 21):
            store.advance(schedule=schedule(at(PLAIN_WORKDAY, hour)),
                          at=at(PLAIN_WORKDAY, hour), model=model())
        day = store.read()["open"]
        self.assertEqual(len(day["history"]), 12)
        self.assertLessEqual(len(day["history"]), card_day.ticks_per_workday())
        self.assertEqual(store.summary()["updates"], 13)


# ---------------------------------------------------------------------------
# V28, walked: one workday, thirteen hours, one card
# ---------------------------------------------------------------------------

class TheWorkdayIsThirteenPatchesOfOneCard(unittest.TestCase):
    """V28: 「同一工作日 08:00–20:00 整点只 patch 一张，下一工作日新建」.

    Written as one walk rather than thirteen assertions, because a criterion
    about a cadence is only tested by running the cadence: every tick is the
    same tick, and what has to hold is that the thirteenth leaves ONE open card
    with thirteen hours accounted for.
    """

    def test_thirteen_hours_of_sampling_produce_one_card_and_no_second_one(self):
        store = temp_store(self)
        actions = []
        for hour in range(8, 21):
            for minute in (0, 30):        # the hour, and a sample inside it
                instant = at(PLAIN_WORKDAY, hour, minute)
                result = store.advance(schedule=schedule(instant), at=instant,
                                       model=model())
                actions.append((f"{hour:02d}:{minute:02d}",
                                result["decision"]["action"],
                                result["decision"]["reason"]))
        day = store.read()["open"]
        self.assertEqual([t for t, a, _ in actions if a == card_day.CREATE],
                         ["08:00"],
                         f"more than one card was created: {actions}")
        # Every hour of the window, both looks, is a PATCH of the same day: the
        # sample inside the hour replaces that hour's row and never opens
        # another card.
        self.assertEqual({r for _, _, r in actions},
                         {card_day.NEW_WORKDAY, card_day.SAME_WORKDAY,
                          card_day.AFTER_WINDOW})
        # 20:30 is the one look the window does not cover — the last routine
        # patch is 20:00 itself (§10 含两端), and after it the day is closed.
        self.assertEqual([t for t, _, r in actions if r == card_day.AFTER_WINDOW],
                         ["20:30"])
        self.assertEqual(day["day_ref"], f"CN-Mainland/{PLAIN_WORKDAY}")
        self.assertEqual(len(day["history"]), 12)
        self.assertEqual(day["latest"]["tick"], card_day.ticks_per_workday())
        self.assertEqual(day["latest"]["hhmm"], "20:00")
        self.assertEqual(store.summary()["updates"], 13)
        self.assertEqual(day["missed_ticks"], 0)

    def test_one_row_per_hour_no_matter_how_often_the_hour_was_looked_at(self):
        """The count that matters is the ROW count: thirteen hours of samples at
        four looks an hour still fold to twelve rows and a latest."""
        store = temp_store(self)
        for hour in range(8, 21):
            for minute in (0, 15, 30, 45):
                instant = at(PLAIN_WORKDAY, hour, minute)
                store.advance(schedule=schedule(instant), at=instant, model=model())
        day = store.read()["open"]
        self.assertEqual(len(day["history"]), 12)
        self.assertEqual(store.summary()["updates"], 13)
        hours = [r["tick"] for r in day["history"]]
        self.assertEqual(hours, sorted(set(hours)), f"an hour appears twice: {hours}")

    def test_sampling_outside_the_window_changes_nothing_on_the_card(self):
        """§11 sentence 4: after the last routine patch the day's card does not
        change on ordinary sampling — and the same holds before the window."""
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model())
        settled = json.dumps(store.read(), sort_keys=True)
        for hour in (0, 6, 7, 21, 23):
            instant = at(PLAIN_WORKDAY, hour)
            result = store.advance(schedule=schedule(instant), at=instant,
                                   model=model(verdict="CRITICAL"))
            self.assertFalse(result["wrote"], f"{instant} wrote the day's card")
        self.assertEqual(json.dumps(store.read(), sort_keys=True), settled)

    def test_the_next_workday_opens_a_new_card_and_closes_the_old_one(self):
        store = temp_store(self)
        for hour in range(8, 21):
            store.advance(schedule=schedule(at(PLAIN_WORKDAY, hour)),
                          at=at(PLAIN_WORKDAY, hour), model=model())
        result = store.advance(schedule=schedule(at(NEXT_WORKDAY, 8)),
                               at=at(NEXT_WORKDAY, 8), model=model())
        self.assertTrue(result["decision"]["rollover"])
        self.assertEqual(result["decision"]["reason"], card_day.NEW_WORKDAY)
        document = store.read()
        self.assertEqual(document["open"]["day_ref"], f"CN-Mainland/{NEXT_WORKDAY}")
        self.assertEqual(document["open"]["history"], [])
        self.assertEqual(document["open"]["latest"]["tick"], 1)
        self.assertEqual(document["previous"]["day_ref"],
                         f"CN-Mainland/{PLAIN_WORKDAY}")
        self.assertEqual(document["previous"]["closed_at"], at(NEXT_WORKDAY, 8))
        self.assertEqual(document["previous"]["updates"], 13)

    def test_a_rest_day_between_two_workdays_does_not_touch_either_card(self):
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model())
        settled = json.dumps(store.read(), sort_keys=True)
        for hour in range(8, 21):
            instant = at(OFFICIAL_REST_MONDAY, hour)
            result = store.advance(schedule=schedule(instant), at=instant,
                                   model=model())
            self.assertFalse(result["wrote"])
        self.assertEqual(json.dumps(store.read(), sort_keys=True), settled)


# ---------------------------------------------------------------------------
# the document on disk
# ---------------------------------------------------------------------------

class TheDaysMemoryIsOneDocumentBesideTheDeliveryLedger(unittest.TestCase):

    def test_the_day_store_sits_beside_the_delivery_ledger(self):
        """Two documents, one shape: `<state>/cards/`. Checked against
        `card_delivery.ledger_path` rather than asserted in a comment, because
        the point is that a second card document does not open a second place
        for card documents to live."""
        state = "/state/alerts/state.json"
        self.assertEqual(card_day.day_path(state),
                         card_delivery.ledger_path(state).parent / "day.json")

    def test_the_default_path_is_where_an_instances_own_state_resolves_to(self):
        """`~/.serveros/business-calendar.yaml` is the sibling default, and the
        same parent: one instance, one directory of card documents."""
        instance_state = Path.home() / ".serveros" / "state" / "alerts.json"
        self.assertEqual(card_day.day_path(instance_state), card_day.DEFAULT_PATH)

    def test_the_renderers_default_and_the_writers_default_are_one_path(self):
        """`card_source.DEFAULT_DAY_STORE_PATH` is an ALIAS and not a second
        literal: two defaults that drift would have `card status` rendering one
        file while `card day-advance` writes another, and both would look right.
        """
        self.assertEqual(source.DEFAULT_DAY_STORE_PATH, card_day.DEFAULT_PATH)
        reading = source.load_day_store()
        self.assertEqual(reading["path"], str(card_day.DEFAULT_PATH))

    def test_an_absent_store_is_an_empty_day_and_not_an_error(self):
        store = card_day.DayStore(self.path / "nothing-here.json")
        self.assertEqual(store.read(), card_day.empty())
        self.assertIsNone(store.summary()["day_ref"])
        self.assertIn("还没有", store.summary()["says"])

    def test_an_unreadable_store_is_refused_rather_than_replaced(self):
        """Starting over would give the same workday a second NEW card, which is
        the one thing V28 forbids by name. Losing the memory is not the harm;
        re-creating the card is."""
        path = self.path / "day.json"
        path.write_text("not json at all", encoding="utf-8")
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.DayStore(path)
        text = str(caught.exception)
        self.assertIn(str(path), text)
        self.assertIn("V28", text)
        self.assertEqual(path.read_text(encoding="utf-8"), "not json at all")

    def test_a_store_whose_open_day_is_not_an_object_is_refused(self):
        path = self.path / "day.json"
        path.write_text(json.dumps({"open": ["2031-06-10"]}), encoding="utf-8")
        with self.assertRaises(card_day.CardDayError):
            card_day.DayStore(path)

    def test_a_store_with_an_unnamed_open_day_is_refused(self):
        """The identity is the dedupe key, and a card without one would match
        against every other day."""
        path = self.path / "day.json"
        path.write_text(json.dumps({"open": {"latest": None, "history": []}}),
                        encoding="utf-8")
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.DayStore(path)
        self.assertIn("day_ref", str(caught.exception))

    def test_a_store_that_folded_by_something_other_than_the_hour_is_refused(self):
        path = self.path / "day.json"
        rows = [row_of(schedule(at(PLAIN_WORKDAY, 9))) for _ in range(14)]
        path.write_text(json.dumps({"open": {"day_ref": f"CN-Mainland/{PLAIN_WORKDAY}",
                                             "latest": None, "history": rows}}),
                        encoding="utf-8")
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.DayStore(path)
        self.assertIn("13", str(caught.exception))

    def test_a_store_whose_latest_was_hand_written_is_refused(self):
        """The allowlist is enforced on documents too, so a row somebody edited
        in is refused by the reader rather than trusted by it."""
        path = self.path / "day.json"
        row_ = row_of(schedule(at(PLAIN_WORKDAY, 9)))
        row_["pid"] = 4242
        path.write_text(json.dumps({"open": {"day_ref": f"CN-Mainland/{PLAIN_WORKDAY}",
                                             "latest": row_, "history": []}}),
                        encoding="utf-8")
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.DayStore(path)
        self.assertIn("pid", str(caught.exception))

    def test_a_store_whose_folded_history_was_hand_written_is_refused(self):
        """The same rule for the rows already folded, which is a second place a
        row can enter the document — and the one a reader would otherwise
        believe, because it is the history rather than the latest."""
        path = self.path / "day.json"
        row_ = row_of(schedule(at(PLAIN_WORKDAY, 9)))
        row_["raw_probe"] = "cat /proc/4242/cmdline"
        path.write_text(json.dumps({"open": {"day_ref": f"CN-Mainland/{PLAIN_WORKDAY}",
                                             "latest": row_of(schedule(at(PLAIN_WORKDAY, 10))),
                                             "history": [row_]}}),
                        encoding="utf-8")
        with self.assertRaises(card_day.CardDayError) as caught:
            card_day.DayStore(path)
        self.assertIn("raw_probe", str(caught.exception))

    def test_the_store_round_trips_through_a_second_reader(self):
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model())
        again = card_day.DayStore(store.path)
        self.assertEqual(again.read()["open"], store.read()["open"])
        self.assertEqual(again.summary()["updates"], 1)

    def test_an_empty_day_keeps_no_previous_and_a_rolled_one_does(self):
        store = temp_store(self)
        self.assertIsNone(store.summary()["previous"])
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model())
        self.assertIsNone(store.summary()["previous"])
        store.advance(schedule=schedule(at(NEXT_WORKDAY, 8)),
                      at=at(NEXT_WORKDAY, 8), model=model())
        self.assertEqual(store.summary()["previous"]["day_ref"],
                         f"CN-Mainland/{PLAIN_WORKDAY}")

    def test_the_document_says_which_schema_wrote_it(self):
        store = temp_store(self)
        store.advance(schedule=schedule(at(PLAIN_WORKDAY, 9)),
                      at=at(PLAIN_WORKDAY, 9), model=model())
        on_disk = json.loads(store.path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk["schema"], card_day.SCHEMA)

    def test_the_summary_carries_the_folded_lines_a_panel_renders(self):
        store = temp_store(self)
        for hour in (9, 10):
            store.advance(schedule=schedule(at(PLAIN_WORKDAY, hour)),
                          at=at(PLAIN_WORKDAY, hour), model=model())
        day = store.summary()
        self.assertEqual(len(day["lines"]), 1)
        self.assertEqual(card_day.row_says(day["history"][0]), day["lines"][0])
        self.assertIn(f"CN-Mainland/{PLAIN_WORKDAY}", day["says"])
        self.assertIn("2/13", day["says"])
        # The identity travels as the summary's own field too — it is what a
        # consumer files the card under, and a sentence that still named the day
        # while the field said None would render right and dedupe wrong.
        self.assertEqual(day["day_ref"], f"CN-Mainland/{PLAIN_WORKDAY}")
        self.assertEqual(day["latest"]["tick"], 3)

    def setUp(self):
        self.path = Path(tempfile.mkdtemp(prefix="card-day-store-"))

    def tearDown(self):
        shutil.rmtree(self.path, ignore_errors=True)


# ---------------------------------------------------------------------------
# the two commands
# ---------------------------------------------------------------------------

#: One gather, small enough to read. Every value here is a fact about nobody.
COLLECTED = {
    "reads": {
        "summary": {
            "host": {"cpu": {"logical_cpus": 8, "model": "stub"},
                     "load": {"load1": 0.1, "load5": 0.1, "load15": 0.1,
                              "runnable": 1},
                     "memory": {"used_bytes": 1024, "total_bytes": 2048,
                                "available_bytes": 1024, "cached_bytes": 0,
                                "swap": {"used_bytes": 0, "total_bytes": 0,
                                         "used_percent": 0.0}}},
            "user": {"summary": {"process_count": 1,
                                 "cpu_percent_of_host": 0.0, "cpu_cores": 0.0,
                                 "logical_cpus": 8, "memory_rss_bytes": 1024,
                                 "memory_percent_of_host": 0.0,
                                 "provenance": "stub"},
                     "quota": {}},
            "disk": {"roles": []},
            "docker": {},
            "advisory": {"thresholds": {}, "notes": []},
        },
        "user_services": {"units": [], "linger": "yes",
                          "supervisor": "systemd --user"},
        "runtime_list": {"resources": []},
    },
    "gaps": [],
    "read_names": ["summary", "top", "user_services", "runtime_list"],
}


class TheTwoCommandsReadAndWriteDifferentThings(unittest.TestCase):
    """`card day` answers and `card day-advance` writes, and nothing else does.

    Driven through the command's own parser, because the failure this catches is
    an argument the parser never declares or a keyword the caller forgets — both
    of which are invisible until somebody runs it on the node.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="card-day-cli-"))
        self.calendar = self.dir / "business-calendar.yaml"
        self.calendar.write_text(CALENDAR, encoding="utf-8")
        self.store = self.dir / "day.json"
        real_gather = source.gather
        source.gather = lambda *a, **k: json.loads(json.dumps(COLLECTED))
        self.addCleanup(setattr, source, "gather", real_gather)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_cli(self, *argv) -> tuple[int, dict | str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = cli.main(list(argv))
            except SystemExit as exit_:   # `--help`, which argparse exits on
                code = int(exit_.code or 0)
        text = out.getvalue()
        try:
            return code, json.loads(text)
        except ValueError:
            return code, text

    @staticmethod
    def texts(card: dict) -> list[str]:
        """Every string the card carries, at any depth.

        Depth because the panels are nested: the day's folded hours are inside a
        `collapsible_panel`'s title and body, so a walk over the top-level
        elements would find the day line and miss the history under it.
        """
        found: list[str] = []

        def walk(node) -> None:
            if isinstance(node, dict):
                for value in node.values():
                    walk(value)
            elif isinstance(node, list):
                for value in node:
                    walk(value)
            elif isinstance(node, str):
                found.append(node)

        walk(card)
        return found

    def day(self, at_, *, json_=True, store=None):
        argv = ["card", "day", "--business-calendar", str(self.calendar),
                "--day-store", str(store or self.store), "--at", at_]
        if json_:
            argv.append("--json")
        return self.run_cli(*argv)

    def advance(self, at_, *, json_=True):
        argv = ["card", "day-advance", "--business-calendar", str(self.calendar),
                "--day-store", str(self.store), "--at", at_]
        if json_:
            argv.append("--json")
        return self.run_cli(*argv)

    def test_the_reader_answers_the_action_and_writes_nothing(self):
        code, payload = self.day(at(PLAIN_WORKDAY, 9))
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["action"], card_day.CREATE)
        self.assertEqual(payload["decision"]["reason"], card_day.NEW_WORKDAY)
        self.assertEqual(payload["schedule"]["tick"], 2)
        self.assertFalse(self.store.exists(),
                         "the reading command created the day store")

    def test_the_writer_creates_the_card_the_reader_promised(self):
        code, payload = self.advance(at(PLAIN_WORKDAY, 9))
        self.assertEqual(code, 0)
        self.assertTrue(payload["wrote"])
        self.assertIsNotNone(payload["row"])
        self.assertEqual(payload["day"]["updates"], 1)
        self.assertEqual(payload["row"]["hhmm"], "09:00")
        # …and the reader, asked afterwards, agrees about what exists.
        _code, read_back = self.day(at(PLAIN_WORKDAY, 9))
        self.assertEqual(read_back["decision"]["action"], card_day.PATCH)
        self.assertEqual(read_back["day"]["updates"], 1)

    def test_the_writer_leaves_no_file_when_the_instant_decides_nothing(self):
        """07:30 on a workday: before the window, so there is nothing to create
        and nothing to patch — and no file left to be mistaken for a card."""
        code, payload = self.advance(at(PLAIN_WORKDAY, 7, 30))
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["reason"], card_day.BEFORE_WINDOW)
        self.assertFalse(payload["wrote"])
        self.assertFalse(self.store.exists())

    def test_the_writer_records_the_hour_in_the_folded_history(self):
        for hour in (9, 10):
            self.advance(at(PLAIN_WORKDAY, hour))
        document = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertEqual([r["hhmm"] for r in document["open"]["history"]],
                         ["09:00"])
        self.assertEqual(document["open"]["latest"]["hhmm"], "10:00")

    def test_a_store_that_cannot_be_read_is_a_fault_and_not_a_fresh_day(self):
        self.store.write_text("{ this is not json", encoding="utf-8")
        for verb in ("day", "day-advance"):
            argv = ["card", verb, "--business-calendar", str(self.calendar),
                    "--day-store", str(self.store), "--at", at(PLAIN_WORKDAY, 9)]
            code, text = self.run_cli(*argv)
            self.assertNotEqual(code, 0, verb)
            self.assertIn(str(self.store), str(text), verb)
        self.assertEqual(self.store.read_text(encoding="utf-8"), "{ this is not json")

    def test_no_calendar_is_answered_without_writing_anything(self):
        code, payload = self.run_cli(
            "card", "day", "--business-calendar", str(self.dir / "absent.yaml"),
            "--day-store", str(self.store), "--at", at(PLAIN_WORKDAY, 9), "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload["decision"]["reason"], card_day.CALENDAR_UNKNOWN)
        self.assertFalse(self.store.exists())

    def test_the_day_line_reaches_the_status_card(self):
        """The consumers `cards.status_card` renders: a line for today, and a
        panel of the hours already folded."""
        for hour in (9, 10):
            self.advance(at(PLAIN_WORKDAY, hour))
        code, card = self.run_cli(
            "card", "status", "--json", "--sample", "--at", at(PLAIN_WORKDAY, 10),
            "--business-calendar", str(self.calendar),
            "--day-store", str(self.store))
        self.assertEqual(code, 0)
        texts = self.texts(card)
        self.assertTrue(any(t.startswith(f"**当天卡** CN-Mainland/{PLAIN_WORKDAY}")
                            for t in texts), texts)
        self.assertTrue(any(t.startswith("**今日历史（1 小时）**") for t in texts), texts)
        self.assertTrue(any(t.startswith("09:00 ") and "mine=OK" in t
                            for t in texts), texts)
        # …and the line does not read 「当天卡 当天卡 …» in front of a person.
        self.assertFalse(any("**当天卡** 当天卡" in t for t in texts), texts)

    def test_an_absent_store_tells_the_card_today_has_no_card_yet(self):
        """A reading that says ABSENT is an answer, and this is what it says.

        It is the normal state of a workday before its first tick — and after
        the first tick it is the visible sign that nothing is advancing the day,
        which is a different statement from a card that says nothing at all.
        """
        code, card = self.run_cli(
            "card", "status", "--json", "--sample", "--at", at(PLAIN_WORKDAY, 10),
            "--business-calendar", str(self.calendar),
            "--day-store", str(self.dir / "nothing-here.json"))
        self.assertEqual(code, 0)
        texts = self.texts(card)
        self.assertTrue(any(t == "**当天卡** 今天还没有当天的卡" for t in texts), texts)
        self.assertFalse(any("今日历史" in t for t in texts), texts)

    def test_an_unreadable_store_tells_the_card_and_does_not_erase_it(self):
        """The fault becomes a collection gap on the card, and the file is left
        exactly as it was: rendering must not be the thing that starts over."""
        self.store.write_text("{ not json", encoding="utf-8")
        code, card = self.run_cli(
            "card", "status", "--json", "--sample", "--at", at(PLAIN_WORKDAY, 10),
            "--business-calendar", str(self.calendar),
            "--day-store", str(self.store))
        self.assertEqual(code, 0)
        texts = self.texts(card)
        self.assertTrue(any(t.startswith("**采集缺口** card_day_store")
                            for t in texts), texts)
        # No day BLOCK, because there is no reading to render one from — and the
        # gap that replaces it does say 「当天卡状态文件读不到», which is the
        # whole point of naming the read rather than the number.
        self.assertFalse(any(t.startswith("**当天卡**") for t in texts), texts)
        self.assertEqual(self.store.read_text(encoding="utf-8"), "{ not json")

    def test_a_snapshot_handed_no_reading_claims_nothing_about_the_day(self):
        """`«handed a reading that says absent»` and `«handed nothing»` are the
        two branches, and this is the second: a fixture that never mentions a
        day store renders no day block rather than an invented one."""
        bare = source.snapshot(COLLECTED, at=at(PLAIN_WORKDAY, 10),
                               calendar=source.load_calendar(str(self.calendar)))
        self.assertNotIn("day", bare)
        handed = source.snapshot(
            COLLECTED, at=at(PLAIN_WORKDAY, 10),
            calendar=source.load_calendar(str(self.calendar)),
            day=source.load_day_store(str(self.dir / "nothing-here.json")))
        self.assertEqual(handed["day"]["day_ref"], None)
        self.assertIn("还没有", handed["day"]["says"])

    def test_the_writer_and_the_reader_decide_the_same_thing_the_same_way(self):
        """The writer decides before it renders and the reader decides without
        rendering, and the two are asked about the same instant. They agree
        because the decision comes from the schedule and the stored day in both
        — not because one of them asked the other."""
        for instant in (at(PLAIN_WORKDAY, 7), at(PLAIN_WORKDAY, 9),
                        at(PLAIN_WORKDAY, 9, 30), at(PLAIN_WORKDAY, 12),
                        at(PLAIN_WORKDAY, 21), at(OFFICIAL_REST_MONDAY, 9)):
            # The reader first: it is the one that must not write, and the
            # writer then agrees with the answer a person would have been shown.
            _code, read_back = self.day(instant)
            _code, written = self.advance(instant)
            self.assertEqual(written["decision"]["action"],
                             read_back["decision"]["action"], instant)
            self.assertEqual(written["decision"]["reason"],
                             read_back["decision"]["reason"], instant)
            self.assertIn(read_back["decision"]["reason"], card_day.REASONS)

    def test_the_writer_needs_no_calendar_to_refuse_a_new_card(self):
        code, payload = self.run_cli(
            "card", "day-advance", "--business-calendar",
            str(self.dir / "absent.yaml"), "--day-store", str(self.store),
            "--at", at(PLAIN_WORKDAY, 9), "--json")
        self.assertEqual(code, 0)
        self.assertFalse(payload["wrote"])
        self.assertEqual(payload["decision"]["reason"], card_day.CALENDAR_UNKNOWN)
        self.assertFalse(self.store.exists())

    def test_the_verbs_are_declared_on_the_card_command(self):
        """A parser that never declared these would fail on the first run, and a
        hand run is evidence about one afternoon rather than about the command."""
        for verb in ("day", "day-advance"):
            code, text = self.run_cli("card", verb, "--help")
            self.assertEqual(code, 0, text)
            self.assertIn("--day-store", str(text), verb)
            self.assertIn("--business-calendar", str(text), verb)


def temp_store(case: unittest.TestCase) -> "card_day.DayStore":
    """A day store under a directory the case takes down with it."""
    directory = Path(tempfile.mkdtemp(prefix="card-day-"))
    case.addCleanup(shutil.rmtree, directory, ignore_errors=True)
    return card_day.DayStore(directory / "day.json")


if __name__ == "__main__":
    unittest.main()
