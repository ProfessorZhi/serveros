#!/usr/bin/env python3
"""The published days and hours a routine broadcast is allowed in.

Every year in this file is SYNTHETIC and says so. A calendar declaration is a
projection of a published notice; using a real notice's dates as test data would
put a copy of an official calendar into the repository, where the next reader
would have no way to tell the fixture from an import — and `docs/MONITORING-
SCOPE-RECOVERY-CARDS.md` §9's whole reason for keeping the days out of the code
is that a calendar in the tree is a calendar nobody re-verifies. So the fixture
declares workdays on a Saturday and rest days on a Monday, and every test that
depends on a weekday asserts its own premise against the stdlib first.
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


business_calendar = _load("serveros_runtime_business_calendar",
                          ROOT / "runtime" / "business_calendar.py")

SCHEMA = "serveros/business-calendar.v1"

#: The synthetic year, and the three days the fixture declares.
YEAR = 2031
OFFICIAL_WORK_SATURDAY = "2031-06-07"   # declared a workday although it is a Saturday
OFFICIAL_REST_MONDAY = "2031-06-09"     # declared a rest day although it is a Monday
PLAIN_WEEKDAY = "2031-06-10"            # covered, in neither list
PLAIN_SUNDAY = "2031-06-15"             # covered, in neither list
UNCOVERED_MONDAY = "2032-06-07"         # a year the declaration does not cover


def declaration(**over):
    """One valid declaration, with every field a test can bend."""
    document = {
        "schema": SCHEMA,
        "jurisdiction": "CN-Mainland",
        "timezone": "Asia/Shanghai",
        "source": {"document_id": "synthetic-test-notice-2031",
                   "revision": "synthetic-rev-1"},
        "years": {str(YEAR): {"workdays": [OFFICIAL_WORK_SATURDAY],
                              "non_working_days": [OFFICIAL_REST_MONDAY]}},
        "imported_at": "2030-12-01T00:00:00+08:00",
        "verified_at": "2030-12-01T00:00:00+08:00",
    }
    document.update(over)
    return document


def at(day, hour, minute=0, second=0, offset="+08:00"):
    return (f"{day}T{hour:02d}:{minute:02d}:{second:02d}{offset}")


class TheDeclarationMustSayWhereItCameFrom(unittest.TestCase):
    """§9: a year's declaration carries the notice's locator and revision.

    Each refusal is tested for the sentence it names, because the failure these
    prevent is silent: a projection missing its source reads at every later step
    exactly like one that has it.
    """

    def refused(self, document, names):
        with self.assertRaises(business_calendar.CalendarError) as caught:
            business_calendar.declaration(document)
        text = str(caught.exception)
        for name in names:
            self.assertIn(name, text, f"the refusal does not name {name}: {text}")
        return text

    def test_a_declaration_with_no_source_is_refused(self):
        # An empty mapping is a MISSING source, and the refusal says so in those
        # words: the locator check below is the one that names url/document_id.
        self.refused(declaration(source={}), ["source"])

    def test_a_source_with_no_locator_is_refused(self):
        self.refused(declaration(source={"revision": "synthetic-rev-1"}),
                     ["url", "document_id"])

    def test_a_source_with_no_revision_or_date_cannot_be_re_verified(self):
        self.refused(declaration(source={"url": "https://example.invalid/x"}),
                     ["published_at", "revision"])

    def test_a_missing_top_level_key_is_named_one_by_one(self):
        for key in business_calendar.REQUIRED_KEYS:
            document = declaration()
            document.pop(key)
            self.refused(document, [key])

    def test_another_schema_is_not_read_leniently(self):
        self.refused(declaration(schema="serveros/business-calendar.v2"),
                     ["v2", SCHEMA])

    def test_a_timezone_this_node_cannot_resolve_is_refused(self):
        self.refused(declaration(timezone="Shanghai/Standard"),
                     ["Shanghai/Standard", "timezone"])

    def test_a_date_declared_as_both_a_workday_and_a_rest_day_is_refused(self):
        text = self.refused(
            declaration(years={str(YEAR): {"workdays": [PLAIN_WEEKDAY],
                                           "non_working_days": [PLAIN_WEEKDAY]}}),
            [PLAIN_WEEKDAY])
        self.assertIn("BOTH", text)

    def test_a_date_nested_under_the_wrong_year_is_refused(self):
        # A date from another year under this year's key: the two halves of the
        # declaration were written at different times and only one can be right.
        self.refused(
            declaration(years={str(YEAR): {"workdays": [OFFICIAL_WORK_SATURDAY],
                                           "non_working_days": [UNCOVERED_MONDAY]}}),
            [UNCOVERED_MONDAY, str(YEAR)])

    def test_a_day_that_is_not_a_date_is_refused_rather_than_skipped(self):
        self.refused(
            declaration(years={str(YEAR): {"workdays": ["the seventh of June"],
                                           "non_working_days": []}}),
            ["the seventh of June"])

    def test_a_fallback_outside_the_published_list_is_refused(self):
        self.refused(declaration(fallback={"rule": "always-work",
                                           "declared_by": "an-instance"}),
                     ["always-work", "weekday"])

    def test_a_fallback_with_no_author_is_refused(self):
        self.refused(declaration(fallback={"rule": "weekday"}), ["declared_by"])

    def test_a_declaration_that_is_not_a_mapping_is_refused(self):
        self.refused(["2031"], ["mapping"])

    def test_an_empty_years_block_can_never_be_wrong_so_it_is_refused(self):
        self.refused(declaration(years={}), ["years"])


class ReadingAMissingDeclarationIsAnAnswerAndNotAnException(unittest.TestCase):
    """§9's UNKNOWN has two causes, and they are repaired differently."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="business-calendar-test-"))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_a_file_that_is_not_there_reads_as_absent(self):
        answer = business_calendar.read_declaration(self.dir / "nothing.yaml")
        self.assertEqual(answer["state"], "ABSENT")
        self.assertIsNone(answer["declaration"])
        self.assertIn("尚未导入", answer["reason"])

    def test_a_file_that_will_not_parse_reads_as_invalid_and_keeps_the_error(self):
        broken = self.dir / "broken.yaml"
        broken.write_text("schema: [unclosed\n", encoding="utf-8")
        answer = business_calendar.read_declaration(broken)
        self.assertEqual(answer["state"], "INVALID")
        self.assertIsNone(answer["declaration"])
        self.assertTrue(answer["detail"], "the parse error was dropped")

    def test_a_file_that_parses_but_does_not_validate_reads_as_invalid(self):
        thin = self.dir / "thin.yaml"
        thin.write_text("schema: serveros/business-calendar.v1\n", encoding="utf-8")
        answer = business_calendar.read_declaration(thin)
        self.assertEqual(answer["state"], "INVALID")
        self.assertIn("jurisdiction", answer["detail"])

    def test_a_valid_file_reads_as_read(self):
        good = self.dir / "calendar.yaml"
        import yaml
        good.write_text(yaml.safe_dump(declaration(), allow_unicode=True),
                        encoding="utf-8")
        answer = business_calendar.read_declaration(good)
        self.assertEqual(answer["state"], "READ")
        self.assertEqual(answer["declaration"]["jurisdiction"], "CN-Mainland")

    def test_nothing_here_reads_the_instances_own_declaration_by_accident(self):
        """The default path is named, and it is not this test's business."""
        self.assertTrue(str(business_calendar.DEFAULT_PATH).endswith(
            ".serveros/business-calendar.yaml"))


class OfficialDaysOverrideTheWeekdayDerivation(unittest.TestCase):
    """§9: «官方调休日优先覆盖 weekday/weekend 推导»."""

    def setUp(self):
        self.decl = business_calendar.declaration(declaration())
        self.assertEqual(datetime.date.fromisoformat(
            OFFICIAL_WORK_SATURDAY).weekday(), 5, "the fixture premises a Saturday")
        self.assertEqual(datetime.date.fromisoformat(
            OFFICIAL_REST_MONDAY).weekday(), 0, "the fixture premises a Monday")

    def kind(self, day, hour=10):
        return business_calendar.day(at(day, hour), declaration=self.decl)

    def test_a_declared_workday_on_a_saturday_is_a_workday(self):
        facts = self.kind(OFFICIAL_WORK_SATURDAY)
        self.assertEqual(facts["kind"], "WORKDAY")
        self.assertEqual(facts["authority"], "declared-workday")
        self.assertIn("调休上班日", facts["says"])

    def test_a_declared_rest_day_on_a_monday_is_not_a_workday(self):
        facts = self.kind(OFFICIAL_REST_MONDAY)
        self.assertEqual(facts["kind"], "NON_WORKING_DAY")
        self.assertEqual(facts["authority"], "declared-rest")

    def test_an_undeclared_monday_in_a_covered_year_is_a_workday(self):
        facts = self.kind(PLAIN_WEEKDAY)
        self.assertEqual(facts["kind"], "WORKDAY")
        self.assertEqual(facts["authority"], "weekday")

    def test_an_undeclared_sunday_in_a_covered_year_is_not(self):
        facts = self.kind(PLAIN_SUNDAY)
        self.assertEqual(facts["kind"], "NON_WORKING_DAY")
        self.assertEqual(facts["authority"], "weekday")

    def test_a_year_the_declaration_does_not_cover_is_unknown_and_not_monday(self):
        facts = self.kind(UNCOVERED_MONDAY)
        self.assertEqual(facts["kind"], "UNKNOWN",
                         "an uncovered Monday was answered as a workday")
        self.assertIsNone(facts["authority"])
        self.assertIn("2032", facts["says"])
        self.assertIn("不按周一至周五推算", facts["says"])

    def test_an_instance_fallback_is_applied_only_when_it_was_declared(self):
        with_fallback = business_calendar.declaration(declaration(
            fallback={"rule": "weekday", "declared_by": "SRV-0001 instance config"}))
        facts = business_calendar.day(at(UNCOVERED_MONDAY, 10),
                                      declaration=with_fallback)
        self.assertEqual(facts["kind"], "WORKDAY")
        self.assertEqual(facts["authority"], "weekday-fallback")
        self.assertIn("这不是官方工作日", facts["says"])
        self.assertIn("SRV-0001 instance config", facts["says"])

    def test_with_no_declaration_the_day_is_unknown_and_nothing_is_derived(self):
        facts = business_calendar.day(at(PLAIN_WEEKDAY, 10),
                                      timezone="Asia/Shanghai")
        self.assertEqual(facts["kind"], "UNKNOWN")
        self.assertIn("calendar=UNKNOWN", facts["says"])
        self.assertIn("不按周一至周五推算", facts["says"])
        self.assertIsNone(facts["authority"])

    def test_the_day_is_named_in_the_calendars_zone_and_not_the_readers(self):
        """The same instant, spelled in two zones, is the same day."""
        shanghai = business_calendar.day(at(PLAIN_WEEKDAY, 0, 30),
                                         declaration=self.decl)
        utc = business_calendar.day(f"{PLAIN_WEEKDAY}T00:30:00+00:00",
                                    declaration=self.decl)
        self.assertEqual(shanghai["date"], PLAIN_WEEKDAY)
        self.assertEqual({k: shanghai[k] for k in ("date", "kind", "authority")},
                         {k: utc[k] for k in ("date", "kind", "authority")})
        # 00:30 +08:00 is 16:30 the PREVIOUS day in UTC; the calendar's zone
        # decides, and the two spellings agree because of it.
        self.assertEqual(utc["date"], PLAIN_WEEKDAY)

    def test_every_kind_and_authority_this_module_can_return_is_published(self):
        answers = [self.kind(PLAIN_WEEKDAY), self.kind(OFFICIAL_REST_MONDAY),
                   self.kind(UNCOVERED_MONDAY),
                   business_calendar.day(at(PLAIN_WEEKDAY, 10))]
        for facts in answers:
            self.assertIn(facts["kind"], business_calendar.DAY_KINDS)
            if facts["authority"] is not None:
                self.assertIn(facts["authority"],
                              business_calendar.DAY_AUTHORITIES)

    def test_an_unreadable_instant_answers_unknown_rather_than_raising(self):
        facts = business_calendar.day("yesterday afternoon",
                                      declaration=self.decl)
        self.assertEqual(facts["kind"], "UNKNOWN")
        self.assertIsNone(facts["date"])


class TheWindowIsEightToTwentyInclusive(unittest.TestCase):
    """§10: «只在 WORKDAY 的 08:00–20:00（含两端）…最多 13 次 routine update»."""

    def setUp(self):
        self.decl = business_calendar.declaration(declaration())
        self.reading = {"state": "READ", "declaration": self.decl,
                        "path": "/synthetic", "reason": "", "detail": ""}

    def window(self, day, hour, minute=0, second=0, reading=None):
        return business_calendar.routine_window(
            at(day, hour, minute, second),
            reading=self.reading if reading is None else reading)

    def test_the_ceiling_is_thirteen_because_the_window_is_thirteen_hours(self):
        self.assertEqual(business_calendar.ROUTINE_UPDATES_PER_WORKDAY, 13)
        self.assertEqual(business_calendar.WINDOW_FIRST_HOUR, 8)
        self.assertEqual(business_calendar.WINDOW_LAST_HOUR, 20)

    def test_the_thirteen_ticks_of_one_workday_are_exactly_one_through_thirteen(self):
        ticks = [self.window(PLAIN_WEEKDAY, hour)["tick"]
                 for hour in range(8, 21)]
        self.assertEqual(ticks, list(range(1, 14)))
        for hour in range(8, 21):
            answer = self.window(PLAIN_WEEKDAY, hour)
            self.assertTrue(answer["in_window"])
            self.assertEqual(answer["route"], "ROUTINE_UPDATE")
            self.assertEqual(answer["reason"], "IN_WINDOW")

    def test_a_minute_before_eight_is_before_the_window(self):
        answer = self.window(PLAIN_WEEKDAY, 7, 59, 59)
        self.assertFalse(answer["in_window"])
        self.assertEqual(answer["reason"], "BEFORE_WINDOW")
        self.assertEqual(answer["route"], "NO_ROUTINE_BROADCAST")
        self.assertIsNone(answer["tick"])
        self.assertIn("不追发", answer["says"])

    def test_eight_oclock_exactly_is_inside(self):
        answer = self.window(PLAIN_WEEKDAY, 8, 0, 0)
        self.assertTrue(answer["in_window"])
        self.assertEqual(answer["tick"], 1)

    def test_twenty_oclock_exactly_is_still_inside(self):
        answer = self.window(PLAIN_WEEKDAY, 20, 0, 0)
        self.assertTrue(answer["in_window"])
        self.assertEqual(answer["tick"], 13)

    def test_last_tick_accepts_bounded_scheduler_and_collection_delay(self):
        for minute, second in ((0, 1), (0, 32), (4, 59)):
            with self.subTest(minute=minute, second=second):
                answer = self.window(PLAIN_WEEKDAY, 20, minute, second)
                self.assertTrue(answer["in_window"])
                self.assertEqual(answer["tick"], 13)
                self.assertEqual(answer["route"], "ROUTINE_UPDATE")

    def test_last_tick_grace_ends_at_twenty_oh_five(self):
        answer = self.window(PLAIN_WEEKDAY, 20, 5, 0)
        self.assertFalse(answer["in_window"])
        self.assertEqual(answer["reason"], "AFTER_WINDOW")
        self.assertNotEqual(answer["route"], "ROUTINE_UPDATE")

    def test_a_rest_day_has_no_window_even_at_midday(self):
        answer = self.window(OFFICIAL_REST_MONDAY, 15)
        self.assertFalse(answer["in_window"])
        self.assertEqual(answer["reason"], "NON_WORKING_DAY")
        self.assertEqual(answer["route"], "NO_ROUTINE_BROADCAST")
        self.assertIn("不创建例行状态卡", answer["says"])

    def test_an_unknown_calendar_answers_none_and_not_false(self):
        """The difference between «outside the window» and «cannot say» is the
        whole point of §9's third kind: a node that answered False here would be
        reporting a decision it did not make."""
        for state in ("ABSENT", "INVALID"):
            answer = self.window(PLAIN_WEEKDAY, 15, reading={
                "state": state, "declaration": None, "path": "/nonexistent",
                "reason": "本年度官方日历尚未导入", "detail": ""})
            self.assertIsNone(answer["in_window"], state)
            self.assertEqual(answer["kind"], "UNKNOWN")
            self.assertEqual(answer["reason"], "CALENDAR_UNKNOWN")
            self.assertEqual(answer["route"], "NO_ROUTINE_BROADCAST")
            self.assertIn("观察与记录照常", answer["says"])

    def test_a_declared_fallback_still_says_it_is_not_an_official_workday(self):
        reading = {"state": "READ", "declaration": business_calendar.declaration(
            declaration(fallback={"rule": "weekday", "declared_by": "an-instance"})),
            "path": "/synthetic", "reason": "", "detail": ""}
        answer = self.window(UNCOVERED_MONDAY, 10, reading=reading)
        self.assertTrue(answer["in_window"])
        self.assertEqual(answer["route"], "ROUTINE_UPDATE")
        self.assertIn("不是官方工作日", answer["says"])

    def test_the_window_is_read_in_the_calendars_zone_not_the_instants_spelling(self):
        spelled = business_calendar.routine_window(
            f"{PLAIN_WEEKDAY}T00:00:00+00:00", reading=self.reading)
        self.assertEqual(spelled["date"], PLAIN_WEEKDAY)
        self.assertEqual(spelled["hour"], 8)
        self.assertEqual(spelled["tick"], 1)

    def test_every_reason_it_can_return_is_published(self):
        answers = [self.window(PLAIN_WEEKDAY, h) for h in (7, 12, 21)]
        answers.append(self.window(OFFICIAL_REST_MONDAY, 12))
        answers.append(self.window(PLAIN_WEEKDAY, 12, reading={
            "state": "ABSENT", "declaration": None, "reason": "", "detail": ""}))
        for answer in answers:
            self.assertIn(answer["reason"], business_calendar.WINDOW_REASONS)

    def test_the_same_instant_always_answers_the_same_way(self):
        """No clock lives in this module: the instant is the only time input."""
        once = self.window(PLAIN_WEEKDAY, 9, 30)
        twice = self.window(PLAIN_WEEKDAY, 9, 30)
        self.assertEqual(once, twice)

    def test_every_unknown_answer_says_calendar_unknown_on_its_face(self):
        """The acceptance criterion reads the Diagnostic for `calendar=UNKNOWN`,
        and the sentence comes from whichever branch produced it — one of which
        is about zones and would otherwise never name the calendar."""
        unknowns = [
            # no reading at all, and no zone named either
            business_calendar.routine_window(at(PLAIN_WEEKDAY, 15)),
            # a reading that is absent
            self.window(PLAIN_WEEKDAY, 15, reading={
                "state": "ABSENT", "declaration": None, "reason": "未导入",
                "detail": "", "path": "/nonexistent"}),
            # a reading that does not validate
            self.window(PLAIN_WEEKDAY, 15, reading={
                "state": "INVALID", "declaration": None, "reason": "无法解析",
                "detail": "exc", "path": "/broken"}),
            # a covered declaration, an uncovered year, no fallback
            business_calendar.routine_window(at(UNCOVERED_MONDAY, 15),
                                             reading=self.reading),
        ]
        for answer in unknowns:
            self.assertEqual(answer["kind"], "UNKNOWN", answer)
            self.assertIn("calendar=UNKNOWN", answer["says"], answer)
            self.assertEqual(answer["reason"], "CALENDAR_UNKNOWN")
            self.assertIsNone(answer["in_window"])
        # and it is said once, not stacked by the wrapper and the branch both
        for answer in unknowns:
            self.assertEqual(answer["says"].count("calendar=UNKNOWN"), 1, answer)

    def test_the_says_line_is_a_whole_sentence_and_not_a_fragment(self):
        answer = self.window(PLAIN_WEEKDAY, 9, 30)
        self.assertIn(PLAIN_WEEKDAY, answer["says"])
        self.assertIn("08:00", answer["says"])
        self.assertIn("Asia/Shanghai", answer["says"])


class TheDayRefIsTheIdentityADedupeWouldUse(unittest.TestCase):
    """§11's create-vs-patch turns on «has this day's card been sent»; the day
    needs a name whose stability across the day's thirteen ticks is the point."""

    def setUp(self):
        self.reading = {"state": "READ",
                        "declaration": business_calendar.declaration(declaration()),
                        "path": "/synthetic", "reason": "", "detail": ""}

    def test_all_thirteen_ticks_of_a_workday_share_one_day_ref(self):
        refs = {business_calendar.routine_window(
            at(PLAIN_WEEKDAY, hour), reading=self.reading)["day_ref"]
            for hour in range(8, 21)}
        self.assertEqual(len(refs), 1, refs)
        self.assertEqual(refs.pop(), f"CN-Mainland/{PLAIN_WEEKDAY}")

    def test_the_next_day_has_a_different_ref(self):
        today = business_calendar.routine_window(at(PLAIN_WEEKDAY, 12),
                                                 reading=self.reading)
        tomorrow = business_calendar.routine_window(at("2031-06-11", 12),
                                                    reading=self.reading)
        self.assertNotEqual(today["day_ref"], tomorrow["day_ref"])

    def test_a_calendar_that_could_not_be_read_names_no_day(self):
        answer = business_calendar.routine_window(
            at(PLAIN_WEEKDAY, 12),
            reading={"state": "ABSENT", "declaration": None, "reason": "",
                     "detail": ""})
        self.assertIsNone(answer["day_ref"])


class ThePublishedSentencesComeFromTheDesign(unittest.TestCase):
    """The three docstrings that made this module exist are quoted, so a later
    reader can check the words rather than remember them."""

    def test_the_module_names_the_sections_it_implements(self):
        text = business_calendar.__doc__
        for section in ("§9", "§10", "§11"):
            self.assertIn(section, text)

    def test_the_thirteen_is_derived_from_the_window_and_not_repeated(self):
        # Read where the module now LIVES, not where it used to. D3 moved the
        # implementation to `runtime/reports/` and left a facade at the old
        # path; the facade forwards attributes, so the derivation is not in its
        # source. Asserting it there would have gone red for the wrong reason
        # and would have frozen the stub instead of the rule.
        source = (ROOT / "runtime" / "reports" / "business_calendar.py").read_text(
            encoding="utf-8")
        self.assertIn(
            "ROUTINE_UPDATES_PER_WORKDAY = WINDOW_LAST_HOUR - WINDOW_FIRST_HOUR + 1",
            source, "the count is no longer the window's consequence")


if __name__ == "__main__":
    unittest.main()
