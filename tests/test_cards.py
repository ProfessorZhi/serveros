#!/usr/bin/env python3
"""The renderers: one model, two outputs, and the ways they could diverge.

§6 asks for a Feishu payload and a visual preview from the *same* renderer, and
the reason is not tidiness. A preview built by second code that reads the model
separately is a preview of a card that does not exist — and it would look
correct, which is worse than looking broken.

So the load-bearing tests here are the ones that check the two outputs against
each other, plus the two rules a card can break silently: an unknown state
rendered as green, and a sample rendered as a reading.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
import sys
import unittest
from datetime import datetime
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cards = _load("serveros_runtime_cards", "cards.py")
presentation = cards.presentation
scenarios = _load("serveros_runtime_card_scenarios", "card_scenarios.py")
source = _load("serveros_runtime_card_source", "card_source.py")
# Loaded for the fixture-vocabulary guard below, which derives the key sets a
# digest row may carry by running the producer rather than by listing them.
alerting = _load("serveros_runtime_alerting", "alerting.py")
alert_digest = _load("serveros_runtime_alert_digest", "alert_digest.py")
attribution = _load("serveros_runtime_attribution", "attribution.py")

DEVICE = "device:tokyo-shared-dev-01"


class _Register:
    """A stand-in for the alert store, holding exactly the rows it is given.

    §3.4's 当前问题 block is graded from the register by `alert_digest.current`,
    and a test that needs the block READ has to hand one over the way the live
    path does (`cli.cmd_card_status` → `card_source.load_problems`). Writing the
    reading by hand instead — `{"state": "READ", "counts": {...}}` — would be the
    test asserting what it believes the grader says, and the grader is the thing
    that decides whether this block keeps the card green. So the store is here
    and `current` is the only thing that reads it, exactly as in production.
    """

    def __init__(self, rows=()):
        self._rows = {str(i): dict(row) for i, row in enumerate(rows)}

    def alerts(self):
        return self._rows

#: §6: "按当前 card schema/SDK 校验，不从草图发明 JSON 属性". These seven are
#: the ones already in use in the cards this node sends, so the schema version in
#: production is known to accept them. An eighth would be a guess, and a guessed
#: tag renders as a blank rectangle in a conversation nobody is watching. The
#: count in this sentence was wrong by one until it was counted against the set
#: below; the set is the authority and the sentence is prose about it.
PROVEN_TAGS = {"markdown", "hr", "plain_text", "collapsible_panel",
               "column_set", "standard_icon", "column"}


def _tags(element: dict) -> set:
    found = {element.get("tag")}
    for child in (element.get("columns") or []):
        for inner in (child.get("elements") or []):
            found |= _tags(inner)
    for inner in (element.get("elements") or []):
        found |= _tags(inner)
    header = element.get("header") or {}
    for sub in ("title", "icon"):
        if isinstance(header.get(sub), dict):
            found |= _tags(header[sub])
    return found


def _all_tags(card: dict) -> set:
    found = set()
    for element in (card.get("body") or {}).get("elements") or []:
        found |= _tags(element)
    header = card.get("header") or {}
    for sub in ("title", "subtitle"):
        if isinstance(header.get(sub), dict):
            found |= _tags(header[sub])
    return found


def _markdown(card: dict) -> list[str]:
    """Every sentence the card says, wherever it sits in the element tree.

    It used to read the top level only, and by the time the face was made of
    cells that had stopped meaning «every sentence»: a cell's text lives inside
    a `column_set`, so the pair-check below would have gone on comparing the
    preview against the handful of lines that were still top-level and calling
    it «one model, two renderings». A check that stops covering the new part of
    the card is the failure it was written to catch, one level down.
    """
    found: list[str] = []

    def walk(element: dict) -> None:
        if element.get("tag") == "markdown":
            found.append(element["content"])
        for column in element.get("columns") or []:
            for inner in column.get("elements") or []:
                walk(inner)
        for inner in element.get("elements") or []:
            walk(inner)

    for element in (card.get("body") or {}).get("elements") or []:
        walk(element)
    return found


def _lines(card: dict) -> list[str]:
    """Every LINE the card carries, at any depth.

    Line-wise as well as deep, because §M4 moved the card's self-account into
    the 技术诊断 fold and that fold is one markdown element holding several
    sentences separated by newlines. A search over whole elements finds the fold
    and misses every sentence inside it, which is how the schedule sentence came
    to look absent from a card that was printing it.
    """
    return [line for text in _markdown(card) for line in text.split("\n")]


def _face(card: dict) -> list[str]:
    """What is on the FIRST SCREEN: every markdown that is not behind a fold.

    `_markdown` answers «what does this card say» and walks the whole tree, so
    it is the wrong instrument for the one rule that is about position rather
    than content — §1's «≤10–12 行» and §13's «旧账/恢复清单不塞日常首屏». A
    sentence inside a closed `collapsible_panel` is not on the first screen; a
    sentence inside the panel's own HEADER is, because the header is what a
    person taps. That distinction is the whole of this function.

    It exists because a rule with no instrument is a preference. The block that
    renders §3.4's 当前问题 was reviewed by eye against fifteen previews and its
    register sentence looked like context in all of them; the same sentence is
    «登记表里还有 1360 项…» against the live register, and the only thing that
    can tell those two apart is an extractor that knows where the fold starts.
    """
    found: list[str] = []

    def walk(element: dict, folded: bool) -> None:
        if element.get("tag") == "markdown" and not folded:
            found.append(element["content"])
        for column in element.get("columns") or []:
            for inner in column.get("elements") or []:
                walk(inner, folded)
        title = ((element.get("header") or {}).get("title") or {})
        if title.get("tag") == "markdown" and not folded:
            found.append(title["content"])
        for inner in element.get("elements") or []:
            walk(inner, folded or element.get("tag") == "collapsible_panel")

    for element in (card.get("body") or {}).get("elements") or []:
        walk(element, False)
    return found


def _column_sets(card: dict) -> list[dict]:
    """Every `column_set` in the card, in the order the card states them."""
    found: list[dict] = []

    def walk(element: dict) -> None:
        if element.get("tag") == "column_set":
            found.append(element)
        for column in element.get("columns") or []:
            for inner in column.get("elements") or []:
                walk(inner)
        for inner in element.get("elements") or []:
            walk(inner)

    for element in (card.get("body") or {}).get("elements") or []:
        walk(element)
    return found


class ScenarioRenderTests(unittest.TestCase):
    """Every situation, because a card that only renders on a good day is a
    card that is missing on the day it matters."""

    @classmethod
    def setUpClass(cls):
        cls.items = scenarios.rendered()

    def test_they_are_the_ones_the_design_and_the_criteria_name(self):
        """§6's six, then AC18–AC21's four, then the one on neither list, then
        the two the acceptance round of 2026-09-30 added.

        The order is asserted, not just the set, because the four groups come
        from four different places and a scenario that migrated between them
        would be a fixture whose provenance a reviewer can no longer read off
        the file. The list is written out rather than derived so that ADDING a
        scenario is a deliberate edit to this test — a fixture nobody had to
        justify is a fixture that quietly stops matching the criteria.

        The last group is the four states that round asks to be photographed.
        Two of them had no preview: ⚠️ ATTENTION, because no scenario before it
        was anything but green, red or unread; and a machine that is fine on a
        day with a hole in it, because the only gap anyone had drawn sat inside
        an unread machine. §6 and AC18–AC21 name the outcomes, not the states a
        screenshot set has to cover, which is why these are on neither list.

        The fifteenth is the newest and is on no list either. §3.4's fourth block
        is fixed on the status card's first screen, and with fourteen scenarios
        every one of them left it green or unread — so the state a reader
        actually has to act on («there ARE current problems») had no preview at
        all. The two ends of the block are covered by `observer_failure` (no
        register read) and by the document-level reading the other thirteen
        share (read, nothing on the face); this one is the middle.
        """
        self.assertEqual(
            [i["scenario"] for i in self.items],
            ["normal", "unbound_gateway", "one_critical", "observer_failure",
             "mixed", "recovery_only",
             # AC18–AC21.
             "pressure_boundary", "expected_service_down", "observability_loss",
             "historical_backlog",
             # Not named by any criterion: the only scenario that renders red.
             # See the scenario's own comment for why it has to exist.
             "critical_service_failed",
             # The states the 2026-09-30 acceptance round photographs.
             "host_attention", "day_gap",
             # The alert face's own fourth answer. The group above covers the
             # STATUS card's four states; this is the INCIDENT card's 未知, and
             # it is a different fact: the machine is not unread, one finding
             # stopped being observed. No live row reaches the face in this
             # state today — the 1,094 held rows are all consumption-level — so
             # without this fixture the branch §4's second question added would
             # be photographed nowhere.
             "unconfirmed_open",
             # §3.4's fourth block with something on it. See this class's
             # docstring for why it is on neither list.
             "problems_on_the_face"])

    def test_the_four_states_that_acceptance_photographs_are_all_reachable(self):
        """normal / attention / unknown / gap — each reached by a rule, not by
        an author writing the word down.

        Asserted by scenario name rather than by "some scenario has verdict X",
        which would pass on a fixture that reached the word by accident and tell
        a reviewer nothing about which file to open.
        """
        by_name = {i["scenario"]: i["model"] for i in self.items}
        ok = lambda name: by_name[name]["env_health"]["verdict"]  # noqa: E731
        self.assertEqual(ok("normal"), "OK")
        self.assertEqual(ok("host_attention"), "ATTENTION")
        self.assertEqual(ok("observer_failure"), "UNCONFIRMED")
        # The fourth is the one the other three cannot express: the machine is
        # fine and the DAY is not fully read. A gap is a property of the day
        # store, so it lives in the timeline and not in the verdict — and it is
        # the one thing that keeps「与上一时点相同」from reading a stretch nobody
        # observed as a stretch that was quiet.
        self.assertEqual(ok("day_gap"), "OK")
        # TWO stretches, and the second one is the reason the panel reads the
        # present instant at all. The store stops at 14:00; the scenario renders
        # at 16:20, so 15:00 went unread as surely as 11:00 and 12:00 did — the
        # one inside the stored rows and the one between the last stored row and
        # now. Counting only the first would report a day with 13 promised hours
        # as having a single hole, and the hole the reader is standing in is the
        # one they can still do something about.
        gaps = [e for e in by_name["day_gap"]["day"]["timeline"]
                if e["kind"] == "gap"]
        self.assertEqual(
            [(g["ticks"], g["from_hhmm"], g["to_hhmm"]) for g in gaps],
            [(2, "10:00", "13:00"), (1, "14:00", "16:20")],
            [e["kind"] for e in by_name["day_gap"]["day"]["timeline"]])
        self.assertTrue(all("未有观测" in g["says"] for g in gaps))

    def test_the_two_counts_of_unread_hours_are_not_two_of_the_same_word(self):
        """§6 and §2, on the card that shows both at once.

        `day_gap` puts two counts of unobserved time inside four lines of each
        other: the delta says «缺 1 个时点读数» — ticks skipped between the last
        reading and now — and the panel title says how many STRETCHES nobody
        observed, which is 2 here (`(2, "10:00", "13:00")` and
        `(1, "14:00", "16:20")` above). Both numbers are true of what they
        count, and the title's compressed form spelled the second one «缺口 2»:
        one word, two quantities, nothing on the face to tell them apart, on the
        one card whose whole point is that an unread hour is visible.

        The long form always had the right unit («⚠️ 2 段无观测»), so this pins
        that the compressed form is the same sentence rather than a shorter one
        with the unit traded away for two cells.
        """
        title = cards._timeline_title(
            {"updates": 5, "transitions": 0, "unobserved": 2, "day_ref": None},
            {"schedule": {}})
        self.assertIn("2 段无观测", title)
        self.assertNotIn("缺口", title)
        # The other side of the pair, on the same numbers: the delta counts
        # TICKS and says so. Two units, so the two numbers cannot be read as
        # answers to one question.
        delta = NoDeltaClauseEndsInABlankLine._delta(
            {"history": [NoDeltaClauseEndsInABlankLine._row(tick=13, hhmm="14:00")],
             "latest": None, "day_ref": "CN-Mainland/2031-06-10"}, tick=15)
        self.assertIn("缺 1 个时点读数", delta["detail"])
        self.assertNotIn("段", delta["detail"])

    def test_the_incident_faces_fourth_answer_reaches_a_rendered_face(self):
        """`UNCONFIRMED` on an alert card, asserted through the fixture and not
        through `_now_line` directly.

        The unit tests below pin the four spellings; this one is what stops the
        fixture drifting away from them. It reads the rendered face, so a
        fixture whose row lost its `hold_kind`, or gained a fresher `last_seen`,
        fails here rather than quietly rendering the STILL branch in a preview
        a reviewer is being asked to approve.

        It also asserts the pair is not merged, from the rendered side: the
        same card must NOT say 仍在发生, and `critical_service_failed` — a live
        OPEN row with no hold — must still say it. Both are `state: OPEN`.
        """
        by_name = {i["scenario"]: i["model"] for i in self.items}
        unconfirmed = by_name["unconfirmed_open"]["events"][0]["existence"]
        self.assertEqual(unconfirmed["state"], presentation.UNCONFIRMED)
        # 173043 and not 2 * 86400: the fixture renders at 06:20:00Z and the row
        # was last graded at 06:15:57Z, so the silence is two days and four
        # minutes. The four minutes are the point — an age asserted as a round
        # number would pass on a fixture whose timestamps had drifted apart.
        self.assertEqual(unconfirmed["silent_seconds"], 173043)
        self.assertEqual(presentation.human_duration(unconfirmed["silent_seconds"]),
                         "2 天 0 小时")
        still = by_name["critical_service_failed"]["events"][0]["existence"]
        self.assertEqual(still["state"], presentation.STILL)
        # The rendered payload and not the model: the model carries the state
        # and the seconds, but the sentence a reviewer reads is built by
        # `cards._now_line` out of them, and it is the sentence that has to say
        # 无法确认 rather than 仍在发生.
        items = {i["scenario"]: i for i in self.items}
        body = items["unconfirmed_open"]["json"]
        # The words are 最后读数 and not 最后一次读数 (§2's line budget: measured
        # at 390px, the longer form made this line 422.9px of the 360px body and
        # it broke, which left the time on a line of its own). What the pair test
        # above pins — 无法确认 against 仍在发生 — is unchanged, and so is the
        # time of the last reading, which is what an age is read against.
        self.assertIn("无法确认 · 最后读数 09-18 14:15", body)
        self.assertNotIn("仍在发生", body)

    def test_a_status_fixture_cannot_state_a_verdict_the_rules_do_not_reach(self):
        """The state word on a status preview is produced, not declared.

        This is the regression the derived fixtures exist for. A hand-written
        `host:` block is the author's belief about what the producer says, and
        the belief was wrong in the two places nobody looks: the block had lost
        the 交换区 line the producer always writes, and no block carried
        `metrics` at all — so every preview drew a card with no two-column face
        while the live card had three rows of cells.

        Checked as a positive control, because a single reading agreeing with a
        single verdict proves nothing: the SAME capture, with and without the
        scenario's own declared mutations, goes through the same
        `card_source._host_section` and comes out on two different verdicts —
        and the two previews carry exactly those two.
        """
        source = scenarios._load("serveros_runtime_card_source", "card_source.py")
        by_name = {i["scenario"]: i["model"] for i in self.items}
        for scenario in scenarios.scenarios():
            if scenario.get("card") != "status":
                continue
            name = scenario.get("name")
            if not isinstance(scenario.get("reads"), str):
                continue  # a scenario that declares gaps rather than reads
            with self.subTest(scenario=name):
                # A fixture may not carry the blocks at all. If it does, the
                # assertion below could still pass while a second author wrote
                # the card, which is the whole failure mode.
                for block in ("mine", "host", "sections", "snapshot"):
                    self.assertNotIn(block, scenario,
                                     f"{name} writes its own `{block}` block")
                payloads = scenarios.read_set(
                    scenario["reads"], mutations=scenario.get("mutations") or ())
                reached = source._host_section(payloads.get("summary"), [])["state"]
                self.assertEqual(by_name[name]["sections"]["host"]["state"], reached)
        # The control. One capture, two readings of it, two verdicts.
        name = "host_attention"
        mutations = next(s for s in scenarios.scenarios()
                         if s.get("name") == name)["mutations"]
        quiet = source._host_section(scenarios.read_set("measured-20260930")
                                     .get("summary"), [])["state"]
        hot = source._host_section(
            scenarios.read_set("measured-20260930", mutations=mutations)
            .get("summary"), [])["state"]
        self.assertNotEqual(quiet, hot, "the mutation changed no reading the rule reads")
        self.assertEqual(hot, by_name[name]["sections"]["host"]["state"])
        self.assertEqual(quiet, by_name["normal"]["sections"]["host"]["state"])

    def test_every_scenario_renders_both_outputs(self):
        """HTML and a card, both non-empty, and the card able to be read.

        THIS ASSERTED `> 1` ELEMENTS UNTIL THE SAMPLE LINE WAS REMOVED, and
        that count was never the invariant — it was being met by the body copy
        of the sample mark, so the assertion went on passing for two digest
        scenarios whose entire body is one closed fold. It said «not empty»
        and measured «has a second element», and the only thing supplying that
        second element was the line §2 deleted.

        What is actually required of a quiet digest is that the title carries
        the answer and the fold carries the numbers, so that a reader who taps
        nothing is still told both. That is the assertion now: a title, a body,
        and — when the body is a single fold — a fold header that says what is
        inside it. `mixed` is 4 low-priority rows and 113 backlogged ones with
        nothing on the face; its face is 「✅ 没有需要处理的变化」 over
        「背景与历史 · 4 项低优先级 · 113 项历史积压」, which is short and
        complete, and a test that demanded a third line would be asking the
        card to say something §5 already put in the fold.
        """
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                self.assertTrue(item["html"].startswith("<!DOCTYPE html>"))
                card = item["card"]
                self.assertTrue(card["header"]["title"]["content"].strip())
                elements = card["body"]["elements"]
                self.assertGreaterEqual(len(elements), 1)
                if len(elements) == 1 and elements[0].get("tag") == \
                        "collapsible_panel":
                    self.assertTrue(
                        elements[0]["header"]["title"]["content"].strip(),
                        "the only element on the face is a fold nobody can read "
                        "the header of")

    def test_every_scenario_uses_only_proven_element_tags(self):
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                self.assertEqual(_all_tags(item["card"]) - PROVEN_TAGS, set())

    def test_every_scenario_is_marked_as_a_sample_in_the_header(self):
        """A preview that cannot be told from a reading is worse than none.

        ONCE, AND ON THE TITLE. This asserted `any(...)` over the body until
        the body copy was removed — it had been added on the argument that a
        body line survives being quoted, and §2 answered that it cost a third
        copy of the same four words on a card whose first screen is capped at
        ten to twelve lines. `any` cannot tell one copy from three, so it went
        on passing while the defect it was written beside grew; an exact count
        can. The title is what a forwarded card carries, which is why the one
        remaining copy is there and nowhere else.
        """
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                title = item["card"]["header"]["title"]["content"]
                self.assertIn(presentation.SAMPLE_MARK, title)
                marks = sum(m.count(presentation.SAMPLE_MARK)
                            for m in [title] + _markdown(item["card"]))
                self.assertEqual(marks, 1, "the sample mark is on the title once")

    def test_no_scenario_renders_an_empty_panel(self):
        """A fold that opens onto nothing is a tap spent on a promise."""
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                panels = [e for e in item["card"]["body"]["elements"]
                          if e.get("tag") == "collapsible_panel"]
                for panel in panels:
                    body = "\n".join(str(e.get("content") or "")
                                     for e in panel.get("elements") or [])
                    self.assertTrue(body.strip(),
                                    f"{item['scenario']}: empty panel")

    def test_the_header_title_is_not_repeated_as_the_first_body_line(self):
        """It is the most valuable line on a 390px screen, and the first draft
        spent it saying what the line above it had just said.

        The sample mark is skipped. Every preview carries one, so a check
        against `[0]` compared the header against 「🧪 示例 / 尚未发送」 and
        passed on all thirteen scenarios without ever looking at a face — the
        kind of check that reports coverage it does not have.
        """
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                title = item["card"]["header"]["title"]["content"]
                bare = title.replace("**", "").split(" · ")[0].strip()
                face = [line for line in _markdown(item["card"])
                        if presentation.SAMPLE_MARK not in line]
                self.assertTrue(face, "a card with no face")
                self.assertNotIn(bare, face[0])

    def test_the_kinds_that_went_unverified_are_named_one_panel_down(self):
        """The card names which expectations went unverified, one entry per
        kind — and §4 puts that list in 技术详情, because it is a list of codes
        and `code` is the first of the nine the section folds.

        The move is measured rather than stylistic: on the face the list was
        446.9px of a 360px row, so it broke, and the break left a `·` at the
        start of the second line where it read as one more item. The property
        this case has always checked is unchanged; the panel it checks moved.
        """
        item = next(i for i in self.items if i["scenario"] == "observability_loss")
        model = scenarios.model_for(
            next(s for s in scenarios.scenarios()
                 if s["name"] == "observability_loss"), sample=False)
        elements = item["card"]["body"]["elements"]
        # The face is what is visible with every fold closed, so it is the
        # markdown elements that are NOT inside a collapsible panel. Reading
        # `_markdown` here would have found the labels wherever they were,
        # which is exactly the question this case is about.
        face = "\n".join(e["content"] for e in elements
                         if e.get("tag") == "markdown")
        fold = "\n".join(inner["content"]
                         for e in elements if e.get("tag") == "collapsible_panel"
                         for inner in e["elements"]
                         if inner.get("tag") == "markdown")
        self.assertEqual(face.split("\n")[0], "**监控完整性**")
        # THE LABEL AND NOT THE CODE, and now neither is on the face. This used
        # to assert each raw code was in the heading, which is §2's forbidden
        # spelling sitting on the first screen; the vocabulary became catalogue
        # labels, and the labels themselves are what §4 folds. The check reads
        # the catalogue the way the renderer does instead of hard-coding either
        # spelling, and it asserts the absence on the face in both.
        for code in (model["integrity"]["by_code"] or {}):
            with self.subTest(code=code):
                label = presentation.definition_for(code)["label"]
                self.assertTrue(label, code)
                self.assertIn(label, fold)
                self.assertNotIn(label, face)
                self.assertNotIn(code, face)
        self.assertNotIn("本次未被观测到", face.split("\n")[0])


class IntegrityFaceTests(unittest.TestCase):
    """§4's second and seventh questions, on the block that answers neither.

    The event blocks beside it carry `**现在**` and `**首次观测**`; this block
    carried 状态 / 影响 / 建议 and stopped, so on the one finding whose whole
    content is «nobody looked» a reader could not tell whether anyone had
    looked since, or whether it began an hour or a fortnight ago. Both lines
    are checked here against a finding built through `integrity_finding`, so
    the instants are the ones the digest would really hand the card.
    """

    AT = "2026-09-20T00:00:00+00:00"

    @staticmethod
    def _held(code: str, first_seen: str) -> dict:
        return {"alert_ref": f"alert:{code}:{first_seen}", "code": code,
                "subject_ref": "x", "severity": "medium", "hold_kind": "unmeasured",
                "hold_reason": "not reported this tick",
                "first_seen": first_seen, "occurrence_count": 1,
                "was_announced": True}

    def _face(self, rows: list[dict]) -> list[str]:
        finding = presentation.integrity_finding(rows, at=self.AT)
        self.assertIsNotNone(finding)
        return cards._integrity_face(finding).splitlines()

    def test_the_face_answers_now_and_since_when(self):
        face = self._face([self._held("DESIRED_ACTUAL_MISMATCH",
                                      "2026-09-19T02:00:00+00:00")])
        self.assertEqual([line.split("**")[1] for line in face],
                         ["监控完整性", "状态", "现在", "影响", "建议", "首次观测"])

    def test_now_is_unconfirmed_and_never_still_happening(self):
        """Every row behind this finding is OPEN and held because no grading
        pass reached it. 「仍在发生」 would say the machine is still in some
        condition — the one thing this finding exists to say we cannot know —
        and a block that printed it would be the monitoring system answering a
        question it has just said it cannot answer."""
        face = "\n".join(self._face([self._held("DESIRED_ACTUAL_MISMATCH",
                                               "2026-09-19T02:00:00+00:00")]))
        self.assertIn("**现在** 无法确认", face)
        self.assertNotIn("仍在发生", face)
        self.assertNotIn("已恢复", face)

    def test_the_start_is_the_earliest_row_not_the_newest(self):
        """«Since when» is the oldest row's `first_seen`. A maximum would date
        the newest subject, and a loss that began a fortnight ago would read as
        one that began this morning."""
        face = self._face([
            self._held("DESIRED_ACTUAL_MISMATCH", "2026-09-19T02:00:00+00:00"),
            self._held("FUNCTIONAL_PROBE_FAILED", "2026-09-11T02:00:00+00:00"),
            self._held("MCP_PROCESS_ALIVE_BUT_UNAVAILABLE", "2026-09-19T20:00:00+00:00")])
        # 09-11 10:00 and 09-19 10:00 in Asia/Shanghai, the node's zone; the
        # test card's `at` is 09-20 08:00 local.
        self.assertIn("**首次观测** 09-11 10:00 · 持续 8 天 22 小时", face[-1])

    def test_the_start_is_dropped_rather_than_placed_when_unrecorded(self):
        """A labelled line holding only 未知 is the field a reader learns to
        skip, and the 现在 line has already said the reading is not current.
        The row is still counted — only its instant is missing."""
        face = self._face([self._held("DESIRED_ACTUAL_MISMATCH", "")])
        self.assertEqual([line for line in face if line.startswith("**首次观测**")], [])
        self.assertIn("**现在** 无法确认", "\n".join(face))
        self.assertIn("1 个", "\n".join(face))


class IntegrityKindsTests(unittest.TestCase):
    """The cap is the aggregation rule, one line higher up the card.

    THE LIST WEARS THE CATALOGUE'S WORDS AND NEVER A CODE. Two cases below
    used to be handed invented codes — `ZZZ_BIG`, `MANY` — and assert they came
    back in the string. That pinned the old rule rather than the property the
    cases are about, and the old rule printed `DESIRED_ACTUAL_MISMATCH ×300` on
    the first screen, which is §2's `code` in the first screen's most expensive
    line. They now use codes the catalogue defines and assert on the label, so
    the property — biggest first, capped, the rest counted — is checked in the
    vocabulary a reader actually sees.

    The list is `_integrity_kinds`'s and it renders one panel down. What is
    checked here is the list; where it is printed is `_integrity_detail`'s, and
    that is checked against a rendered card.
    """

    @staticmethod
    def _finding(codes: dict) -> dict:
        return {"kind": "monitoring-integrity", "attention": {"level": "REVIEW"},
                "count": sum(codes.values()), "by_code": codes,
                "text": "t", "impact": "i", "action": "a",
                "not_a_finding": {"count": 0, "note": "n"}}

    def test_it_names_at_most_the_cap_and_counts_the_rest(self):
        kinds = cards._integrity_kinds(self._finding({
            "DESIRED_ACTUAL_MISMATCH": 300, "FUNCTIONAL_PROBE_FAILED": 200,
            "TUNNEL_UNHEALTHY": 40, "PROTECTION_LOST_ON_IDENTITY_SWITCH": 2,
            "DESIRED_ACTUAL_MISMATCH2": 1}))
        self.assertEqual(kinds.count("×"), cards.INTEGRITY_CODES_SHOWN)
        self.assertIn("等 2 类", kinds)

    def test_the_biggest_kinds_are_the_ones_kept(self):
        """Sorted by count, not by word: the 300-row expectation is the one a
        person needs to see, and a list that dropped it to make room for
        three single rows would hide the largest hole behind the smallest."""
        kinds = cards._integrity_kinds(self._finding({
            "DESIRED_ACTUAL_MISMATCH": 300, "FUNCTIONAL_PROBE_FAILED": 1,
            "TUNNEL_UNHEALTHY": 1, "PROTECTION_LOST_ON_IDENTITY_SWITCH": 1}))
        self.assertIn("期望与实测不一致 ×300", kinds)
        small = ("探针未通过", "隧道不健康", "保护已失效")
        self.assertEqual(1, sum(1 for word in small if word not in kinds))
        self.assertIn("等 1 类", kinds)
        # The code is still in the string's vocabulary nowhere, and the count it
        # was counted under is what says how big the hole is.
        self.assertNotIn("DESIRED_ACTUAL_MISMATCH", kinds)

    def test_two_codes_with_one_label_are_one_entry(self):
        """`未声明归属` is the declared label of TWO codes, and the list
        counts by the word a reader sees. Two entries reading «未声明归属 ×2»
        and «未声明归属 ×3» would be the catalogue's own shared label arriving
        on the card as a duplication — and the sum is the number that belongs
        there, because the reader's question is how much stands unowned."""
        kinds = cards._integrity_kinds(self._finding({
            "RUNTIME_UNOWNED": 2, "TUNNEL_OWNER_MISSING": 3}))
        self.assertEqual(kinds, "未声明归属 ×5")

    def test_a_code_the_catalogue_does_not_define_is_one_hole_with_a_size(self):
        """A code with no catalogue entry still has to be counted — it is the
        thing that fired — and `definition_for` answers ABSENT by carrying the
        code verbatim because the catalogue has a hole. A line has no room
        for that sentence, so the hole becomes the word and the count stays the
        size of it: the alternative is a list that either drops the rows or
        prints the code §2 sends to the back."""
        kinds = cards._integrity_kinds(self._finding({
            "NO_SUCH_CODE_A": 2, "NO_SUCH_CODE_B": 3}))
        self.assertEqual(kinds, "未登记的 code ×5")

    def test_a_finding_with_nothing_to_enumerate_says_nothing(self):
        """An aggregate with nothing to enumerate still gets its face — the
        section name is a constant there — but the fold prints no
        `不成立的声明` line, because a label with nothing after it is a field a
        reader has to interpret as a value."""
        finding = self._finding({})
        self.assertEqual(cards._integrity_kinds(finding), "")
        self.assertEqual(
            [b for b in cards._integrity_detail(finding)
             if b.startswith("**不成立的声明")], [])

    def test_a_count_of_one_costs_no_characters(self):
        """A line on a card is expensive and `×1` is three characters saying
        nothing. The number appears where it is the interesting part: a word
        standing behind more than one subject."""
        kinds = cards._integrity_kinds(self._finding({
            "FUNCTIONAL_PROBE_FAILED": 1, "TUNNEL_UNHEALTHY": 42}))
        self.assertEqual(kinds, "隧道不健康 ×42 · 探针未通过")

    def test_every_scenario_still_says_a_sample_without_the_mark(self):
        """The mark is a flag, and the flag has to be off for the card to be
        sendable — a card that says 示例 unconditionally would make the flag
        meaningless on the one card that is real."""
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                model = scenarios.model_for(scenario, sample=False)
                self.assertFalse(model["sample"])
                card = cards.card_for(model)
                self.assertNotIn(presentation.SAMPLE_MARK,
                                 card["header"]["title"]["content"])
                self.assertFalse(any(presentation.SAMPLE_MARK in m
                                     for m in _markdown(card)))


def _panels(card: dict) -> dict:
    """Every collapsible panel, keyed by the title a person taps."""
    out = {}
    for element in (card.get("body") or {}).get("elements") or []:
        if element.get("tag") != "collapsible_panel":
            continue
        title = ((element.get("header") or {}).get("title") or {}).get("content")
        body = "\n".join(str(e.get("content") or "")
                         for e in element.get("elements") or [])
        out[str(title)] = body
    return out


def _panel_elements(card: dict) -> list[dict]:
    """Every `collapsible_panel` element, whole — what `_panels` drops.

    `_panels` keeps the title and the body because that is what almost every
    test is about. The affordance is neither: it is the header's `icon`
    declaration, so the tests that are about the fold's shape need the element.
    """
    return [element
            for element in (card.get("body") or {}).get("elements") or []
            if element.get("tag") == "collapsible_panel"]


def _summaries(html: str) -> list[str]:
    """The text between `<summary>` and `</summary>`, in document order."""
    return re.findall(r"<summary>(.*?)</summary>", html, re.DOTALL)


def _detail_under(card: dict, fragment: str) -> str:
    found = [body for title, body in _panels(card).items() if fragment in title]
    assert found, f"no panel whose title contains {fragment!r}: {list(_panels(card))}"
    return "\n".join(found)


class DeclaredServiceDownTests(unittest.TestCase):
    """AC19. A service declared `running` that is not gets named in words.

    「有一个对象的实际状态与声明的期望状态不一致」 is true of every mismatch
    this code can raise, which is exactly why it is not enough: the reader is
    told that a comparison failed and not which service, nor in what way. The
    words for the two declared pairs live in `card-definitions.yaml`, and the
    cases below are the ones that could quietly stop being true — an undeclared
    pair acquiring an invented word, a resting resource being called 不可用, and
    a caveated comparison being resolved by one word that the evidence
    explicitly refuses to resolve.
    """

    @classmethod
    def setUpClass(cls):
        cls.scenario = next(s for s in scenarios.scenarios()
                            if s["name"] == "expected_service_down")
        cls.model = scenarios.model_for(cls.scenario, sample=False)
        cls.card = cards.card_for(cls.model)

    def test_the_first_screen_names_the_service_and_says_it_is_down(self):
        event = self.model["events"][0]
        self.assertEqual(event["what"]["text"], "container0-postgres 不可用")
        face = "\n".join(_markdown(self.card))
        self.assertIn("container0-postgres 不可用", face)
        # Not merely "not the generic sentence": the generic sentence is the
        # thing a reader was getting, so its absence is asserted too.
        self.assertNotIn("实际状态与声明的期望状态不一致", face)

    def test_the_declared_pair_is_what_selected_the_word(self):
        """The word is read off both sides, so both sides are on the model."""
        compared = self.model["events"][0]["what"]["compared"]
        self.assertEqual(compared["desired"], "running")
        self.assertEqual(compared["actual"], "NOT_RUNNING")
        self.assertIsNone(compared["caveat"])

    def test_the_technical_detail_keeps_both_sides_of_the_comparison(self):
        """The face says 不可用; the fold says against what, and what the
        provider itself reported. Without the second half a reader cannot tell
        a container Docker reported as `exited` from a process match."""
        detail = _detail_under(self.card, "技术详情")
        self.assertIn("declared running · measured NOT_RUNNING", detail)
        self.assertIn("provider says exited", detail)

    def test_a_caveated_comparison_keeps_the_generic_sentence(self):
        """The hidepid case, built by changing one field of the fixture above.

        `_process_match_caveat` exists because on this host a failed `pgrep`
        has two readings — the process is gone, or it is running behind a
        boundary this uid cannot cross. Saying 不可用 there would be the card
        settling, in two characters, the question the evidence block spends a
        paragraph refusing to settle.
        """
        digest = copy.deepcopy(self.scenario["digest"])
        digest["rows"][0]["caveat"] = CAVEAT
        model = presentation.alert_model(
            digest, at=self.scenario.get("at") or scenarios.document().get("at"),
            names=presentation.name_index(attribution=self.scenario.get("attribution"),
                                          registry=self.scenario.get("registry")))
        card = cards.card_for(model)
        event = model["events"][0]
        self.assertNotIn("不可用", event["what"]["text"])
        self.assertEqual(event["what"]["text"],
                         presentation.definition_for(
                             event["code"])["what"])
        self.assertEqual(event["what"]["compared"]["caveat"], CAVEAT)
        self.assertIn("caveat:", _detail_under(card, "技术详情"))

    def test_the_generic_sentence_is_still_what_an_undeclared_pair_gets(self):
        """The strict direction, and the reason `says` is a table rather than a
        translation: a pair nobody wrote down must not get a guessed word."""
        self.assertIsNone(presentation.comparison_word(
            "DESIRED_ACTUAL_MISMATCH", desired="stopped", actual="STOPPED"))
        self.assertIsNone(presentation.comparison_word(
            "DESIRED_ACTUAL_MISMATCH", desired="running", actual="FUNCTIONALLY_FAILED"))
        self.assertTrue(presentation.definition_for(
            "DESIRED_ACTUAL_MISMATCH")["what"])


class TheMeasurementIsAFieldOnTheFirstScreenTests(unittest.TestCase):
    """Incident Card V2. The number and the tier it crossed, on the face.

    §4 always required the measurement; it required it one tap down, inside a
    block titled 技术详情, which is where a reader who has not yet decided to
    care will not go. A card whose whole finding is «this reading passed this
    threshold» answered neither half of it on the first screen.

    What makes it a FIELD and not a nicer sentence is that the model carries it
    as one — `event["measurement"]` with the axis it came from — and that it is
    produced by `alerting.measurement_parts`, the same function the evidence
    block's sentence is joined from. These cases pin both: the face, the model,
    and the fact that the two registers cannot come to disagree.
    """

    @classmethod
    def setUpClass(cls):
        cls.by_name = {i["scenario"]: i for i in scenarios.rendered()
                       if i["model"].get("card") == "alert"}

    def test_a_row_that_measured_something_prints_it_with_its_threshold(self):
        item = self.by_name["pressure_boundary"]
        event = item["model"]["events"][0]
        self.assertEqual(event["code"], "MEMORY_PRESSURE")
        face = "\n".join(_markdown(item["card"]))
        self.assertIn("**测量** 97.3%（≥ 阈值 90.0%）", face)

    def test_the_field_carries_the_axis_and_not_only_the_words(self):
        """A string on the face is a sentence; a field says where it came from.
        The axis is what lets a reader go and look at the register row."""
        event = self.by_name["pressure_boundary"]["model"]["events"][0]
        self.assertEqual(event["measurement"]["axis"], "used_percent")
        self.assertTrue(event["measurement"]["measured"])
        self.assertEqual(event["measurement"]["face"], "97.3%（≥ 阈值 90.0%）")

    def test_the_two_first_screen_numbers_are_the_fixtures_own_readings(self):
        """Two codes, two registers, and neither figure written by the test.

        The numbers are the ones the fixtures carry, and the fixtures carry the
        register's own row: `card-scenarios.yaml` takes them from this machine's
        alert register rather than inventing a plausible percentage, which is
        the only reason a reader can check the face against anything.
        """
        cases = {"one_critical": ("DISK_CRITICAL", "92.9%（≥ 阈值 92.0%）"),
                 "pressure_boundary": ("MEMORY_PRESSURE", "97.3%（≥ 阈值 90.0%）")}
        for scenario, (code, face) in cases.items():
            with self.subTest(scenario=scenario):
                item = self.by_name[scenario]
                self.assertEqual(item["model"]["events"][0]["code"], code)
                self.assertIn(f"**测量** {face}",
                              "\n".join(_markdown(item["card"])))

    def test_the_line_is_printed_exactly_when_an_event_has_a_measurement(self):
        """Printed for every card that has one, and for no card that does not.

        The negative half is this host's own reading of its codes: two of the
        alert codes in the catalogue are declarations rather than measurements —
        `FUNCTIONAL_PROBE_FAILED` is a probe that did not answer — and the
        scenario that renders one is the control. A line that appeared for them
        would be the card printing a figure it does not have.
        """
        for name, item in self.by_name.items():
            with self.subTest(scenario=name):
                face = "\n".join(_markdown(item["card"]))
                carried = any(e["measurement"] for e in item["model"]["events"])
                self.assertEqual("**测量**" in face, carried)

    def test_a_code_that_measures_nothing_gets_no_measurement_line(self):
        """And the reason it does not is one tap down rather than nowhere."""
        item = self.by_name["critical_service_failed"]
        event = item["model"]["events"][0]
        self.assertEqual(event["code"], "FUNCTIONAL_PROBE_FAILED")
        self.assertIsNone(event["measurement"])
        self.assertNotIn("**测量**", "\n".join(_markdown(item["card"])))
        self.assertIn("no measurement", event["evidence"])

    def test_the_face_and_the_fold_are_joined_from_one_reading(self):
        """The drift guard, and the reason both registers live in one function.

        The face prints the leading reading in the card's language and the fold
        prints every reading in the contract's. They are different sentences
        about the same number, and a renderer that re-read `used_percent` off
        the row itself would be a second reader of the register's payload — the
        day the two disagree is a day the face and the fold under it print two
        different figures for one finding.

        Both halves are asserted on the MODEL, after the renderer: the `says`
        sentence the face was derived from has to still be in the evidence
        block, and the digits have to be the same digits. A face that had
        re-derived itself from the row would pass the second and could fail the
        first.
        """
        digits = lambda text: sorted(re.findall(r"[\d.]+", str(text)))  # noqa: E731
        checked = 0
        for name, item in self.by_name.items():
            for event in item["model"]["events"]:
                measurement = event["measurement"]
                if not measurement:
                    continue
                with self.subTest(scenario=name, code=event["code"]):
                    self.assertIn(measurement["says"], event["evidence"])
                    self.assertEqual(digits(measurement["face"]),
                                     digits(measurement["says"]))
                    checked += 1
        # Three codes in the fixtures carry a reading, and a guard that silently
        # stopped running would read as one that passed.
        self.assertEqual(checked, 3)

    def test_the_evidence_block_still_prints_the_numbers_it_always_did(self):
        """The lift did not move anything out of the fold: the face gained a
        line, and the block below it kept every clause it had."""
        detail = _detail_under(self.by_name["pressure_boundary"]["card"],
                               "技术详情")
        self.assertIn("97.3% (≥ threshold 90.0%)", detail)


class TheEventLineWearsTheCataloguesLabelTests(unittest.TestCase):
    """`Claude 会话 15413 · 遗留进程组` — the line that says what the subject IS.

    The header carries the attention word ("⚠️ 需确认 · 1 项"), so the event line
    owes the reader the catalogue's noun for the code rather than the word they
    read six lines ago. Both spellings were on the live card once: the noun only
    appeared when `presentation._event_of` copied it into the event's `what`
    block, and that block is built field by field — `label` was returned by
    `definition_for` and then dropped on the floor. Nothing failed, because
    `_event_title` falls back to the subject alone by design and a missing label
    is therefore indistinguishable from a code that has none.

    That is what this test is for: the fallback is correct, so the only way to
    tell "no label declared" from "label not threaded" is to read the catalogue
    and check it reached the face.
    """

    def test_every_alert_scenario_carries_the_declared_label_onto_the_face(self):
        checked = 0
        for scenario in scenarios.scenarios():
            if scenario.get("card") != "alert":
                continue
            model = scenarios.model_for(scenario, sample=False)
            face = "\n".join(_markdown(cards.card_for(model)))
            for event in model["events"]:
                declared = presentation.definition_for(event["code"]).get("label")
                if not declared:
                    continue
                checked += 1
                with self.subTest(scenario=scenario["name"], code=event["code"]):
                    self.assertEqual(event["what"]["label"], declared)
                    self.assertIn(f"{event['subject']['text']} · {declared}", face)
        self.assertGreater(checked, 0,
                           "no alert scenario exercises the label, so this test "
                           "would pass on a model that dropped it")


class TheCardSaysWhetherItIsStillTrueTests(unittest.TestCase):
    """§4's second question — 「现在还存在吗」 — and the two answers that look alike.

    `state: OPEN` in the register covers two situations that call for opposite
    things from a reader: the last pass that graded this still found it above
    its floor, and no pass has graded it since. The card used to answer the
    question with `resolved_at or at` — the instant of rendering — so a row held
    since 2026-09-21 read as a reading taken as the card was being written. On
    this node that is 1,091 rows, and the fixtures carried neither `hold_kind`
    nor `last_seen`, so all four of the alert scenarios were silently on the
    wrong side of it: they had no way to say anything but 「仍在发生」.

    The four spellings are asserted individually because any one of them alone
    passes on a renderer that ignores the other three.
    """

    AT = "2026-10-04T08:30:00+00:00"

    @staticmethod
    def _event(**row) -> dict:
        base = {"code": "DESIRED_ACTUAL_MISMATCH", "severity": "high",
                "subject_ref": "runtime:serveros-control-plane",
                "first_seen": "2026-09-18T06:15:57+00:00",
                "occurrence_count": 5}
        base.update(row)
        return presentation._event_of(base, at=TheCardSaysWhetherItIsStillTrueTests.AT)

    def test_a_row_nobody_has_graded_says_so_and_says_when_it_stopped(self):
        line = cards._now_line(self._event(
            state="OPEN", hold_kind="unmeasured",
            last_seen="2026-09-18T06:15:57.175939+00:00"))
        self.assertIn("**现在** 无法确认", line)
        self.assertIn("最后读数 09-18 14:15", line)
        # One unit, and the unit is chosen by `presentation.age_says`. The
        # seconds themselves are still on the event (`existence.silent_seconds`)
        # and still spelled exactly by `human_duration` in the fold; what
        # changed is the FACE, where 「距今 16 天 0 小时」 cost 422.9px of a
        # 360px body and wrapped. See `cards._now_line`.
        self.assertIn("（16 天前）", line)
        # The sentence that must never be printed about this row.
        self.assertNotIn("仍在发生", line)

    def test_a_row_still_being_graded_says_it_is_still_happening(self):
        line = cards._now_line(self._event(
            state="OPEN", last_seen="2026-10-04T08:20:43+00:00"))
        self.assertIn("**现在** 仍在发生", line)
        self.assertIn("最近观测 10-04 16:20", line)
        self.assertNotIn("无法确认", line)

    def test_a_row_the_register_closed_says_recovered(self):
        line = cards._now_line(self._event(
            state="RESOLVED", resolved_at="2026-09-26T09:58:41+00:00",
            last_seen="2026-09-26T09:57:41+00:00"))
        self.assertEqual(line, "**现在** 已恢复 · 09-26 17:58")

    def test_a_row_the_register_did_not_describe_is_unconfirmed_not_still(self):
        """`state` missing is the register not having said, and the card must
        not turn that into 「仍在发生」 — the answer a reader acts on. It is
        `UNCONFIRMED` with no reading, which is the same face a held row gets;
        what separates them is `basis`, in the fold."""
        event = self._event()
        line = cards._now_line(event)
        self.assertEqual(line, "**现在** 无法确认 · 未记录最后读数")
        self.assertEqual(event["existence"]["state"], presentation.UNCONFIRMED)
        self.assertEqual(event["existence"]["basis"],
                         "the register row carried no state")
        self.assertNotIn("仍在发生", line)

    def test_an_event_with_no_existence_block_says_unknown(self):
        """The fourth spelling, at its only reachable path. Every event the
        workspace builds carries `existence` (`presentation._event_of` always
        attaches it), so this is the defensive line for a caller that hands the
        renderer a bare event: it says it does not know rather than picking one
        of the three answers the register did not give."""
        self.assertEqual(cards._now_line({}), "**现在** 状态未知")
        self.assertEqual(cards._now_line({"existence": None}), "**现在** 状态未知")

    def test_a_row_with_no_recorded_reading_claims_no_time(self):
        line = cards._now_line(self._event(state="OPEN", hold_kind="unmeasured"))
        self.assertEqual(line, "**现在** 无法确认 · 未记录最后读数")

    def test_the_render_instant_is_never_offered_as_an_observation(self):
        """The defect itself, as an assertion. `_event_of` used to set
        `last_seen` to `resolved_at or at`; a row with neither now carries
        None, and the model must not have filled it in from the clock."""
        event = self._event(state="OPEN", hold_kind="unmeasured")
        self.assertIsNone(event["times"]["last_seen"])
        self.assertIsNone(event["existence"]["observed_at"])

    def test_the_two_open_answers_are_told_apart_by_hold_kind_alone(self):
        held = self._event(state="OPEN", hold_kind="above_floor",
                           last_seen="2026-10-04T08:00:00+00:00")
        graded = self._event(state="OPEN", last_seen="2026-10-04T08:00:00+00:00")
        self.assertEqual(held["existence"]["state"], presentation.UNCONFIRMED)
        self.assertEqual(graded["existence"]["state"], presentation.STILL)
        # Same row, same clock, same `state: OPEN` — and the two lines must not
        # be interchangeable, or the pair above is decoration.
        self.assertNotEqual(cards._now_line(held), cards._now_line(graded))


#: A caveat of the shape `desired.py` writes. Written out rather than imported
#: because the producer's own sentence names a file and a node, and a test that
#: asserted on it would be asserting that the sentence was unchanged.
CAVEAT = ("the measurement is `pgrep -f` matching 0 process(es), and on this "
          "host that negative result has two readings that this comparison "
          "cannot tell apart: the process is gone, or it is running behind a "
          "visibility boundary this uid cannot cross")


class TheFixturesSpeakTheProducersVocabularyTests(unittest.TestCase):
    """Every key a card fixture writes is a key the producer emits.

    AC14 and AC16 ask the tests to constrain the card rather than snapshot a
    pretty sample, and a fixture is where that is easiest to fail silently: a
    row is a dictionary, nothing checks its keys, and a key the producer never
    writes renders as a sentence about evidence nobody collected. This file's
    `owner` field was exactly that — `{type: serveros, ref: ...}` on four
    fixture rows, a shape `alert_digest._owner_of` cannot produce, which the
    card then printed as "owner: unattributed" beside an owner the fixture
    claimed to have. Nothing failed; the preview just showed an absence.

    The allowed sets are DERIVED, not listed: `deltas` is run against a real
    store holding one alert, so a change to the projection moves these sets and
    a fixture that did not follow fails here.
    """

    @classmethod
    def setUpClass(cls):
        import tempfile
        cls.tmp = Path(tempfile.mkdtemp(prefix="cards-fixture-vocab-"))
        cls.vocab = cls._measure()
        cls.document = yaml.safe_load(
            (RUNTIME / "card-scenarios.yaml").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        import shutil
        shutil.rmtree(cls.tmp, ignore_errors=True)

    @staticmethod
    def _measure() -> dict:
        """One delta row, one held row, and the owner shape, from the producer."""
        import datetime
        state = TheFixturesSpeakTheProducersVocabularyTests.tmp / "alerts" / "state.json"
        state.parent.mkdir(parents=True, exist_ok=True)
        store = alerting.AlertStore(state)
        cursor = alert_digest.DeliveryCursor(alert_digest.cursor_path(state))
        at = datetime.datetime.fromtimestamp(
            1789000000, datetime.timezone.utc).isoformat()
        alert_digest.ensure_baseline(cursor, store, at=at, device_ref=DEVICE)
        ownership = attribution._owner(
            attribution.ROLE_EXTERNAL_USER_SESSION, why="the cgroup names a login",
            slice_name="user.slice", uid="1002", session="134")
        ref = alerting.alert_ref(alerting.identity(
            code="ORPHAN_PROCESS_TREE", device_ref=DEVICE,
            subject_ref="runtime-group:process-tree:session-134"))
        alerts = store.alerts()
        alerts[ref] = {
            "alert_ref": ref, "code": "ORPHAN_PROCESS_TREE", "severity": "high",
            "device_ref": DEVICE, "subject_ref": "runtime-group:process-tree:session-134",
            "resource_ref": "runtime-group:process-tree:session-134",
            "state": "OPEN", "first_seen": at, "resolved_at": None,
            "occurrence_count": 1, "hold_kind": "unmeasured",
            "hold_reason": "the measurement was not reported this tick",
            "measured": {"ownership": ownership},
            "provenance": {"freshness": "FRESH", "source": "host-processes",
                           "observed_at": at},
        }
        store.commit(alerts)
        document = alert_digest.deltas(store, cursor, at=at)
        return {
            "row": set(document["rows"][0]) if document["rows"] else set(),
            "held": set(document["held"][0]) if document["held"] else set(),
            "owner": set(document["rows"][0]["owner"] or {})
                     if document["rows"] else set(),
            "ownership": ownership,
        }

    def _alert_fixtures(self):
        for scenario in self.document["scenarios"]:
            if scenario.get("card") == "alert":
                yield scenario, (scenario.get("digest") or {})

    def test_the_derivation_itself_produced_something(self):
        """The positive control. An empty allowed-set would pass every check
        below by rejecting nothing.

        `immediate` is asserted because only `deltas` writes it: a version of
        this that reached `_row_of` alone would silently reject every fixture
        row carrying the delta vocabulary.
        """
        self.assertTrue(self.vocab["row"] >= {
            "key", "code", "severity_rank", "owner", "provenance", "caveat",
            "desired_state", "actual_state", "actual_derived_state",
            "actual_raw", "kind", "was_announced", "backlog", "immediate"},
            sorted(self.vocab["row"]))
        self.assertTrue(self.vocab["held"] >= {"alert_ref", "code", "hold_kind",
                                               "hold_reason", "was_announced"},
                        sorted(self.vocab["held"]))
        self.assertEqual(len(self.vocab["owner"]), 7, sorted(self.vocab["owner"]))

    def test_every_fixture_alert_row_uses_the_producers_keys(self):
        checked = 0
        for scenario, digest in self._alert_fixtures():
            for row in digest.get("rows") or []:
                checked += 1
                with self.subTest(scenario=scenario["name"], key=row.get("key")):
                    self.assertEqual(set(row) - self.vocab["row"], set())
        self.assertGreater(checked, 0, "no fixture row was checked")

    def test_every_fixture_held_row_uses_the_producers_keys(self):
        checked = 0
        for scenario, digest in self._alert_fixtures():
            for row in digest.get("held") or []:
                checked += 1
                with self.subTest(scenario=scenario["name"]):
                    self.assertEqual(set(row) - self.vocab["held"], set())
        self.assertGreater(checked, 0, "no held row was checked")

    def test_a_fixture_owner_is_a_shape_somebody_measured(self):
        """`{type: serveros, ref: ...}` is what this caught."""
        checked = 0
        for scenario, digest in self._alert_fixtures():
            for row in digest.get("rows") or []:
                owner = row.get("owner")
                if owner is None:
                    continue
                checked += 1
                with self.subTest(scenario=scenario["name"], key=row.get("key")):
                    self.assertEqual(set(owner) - self.vocab["owner"], set())
                    self.assertIn(owner.get("class"),
                                  {attribution.EXTERNAL, attribution.MANAGED,
                                   attribution.UNKNOWN})
                    self.assertIn(owner.get("role"), {
                        None, attribution.ROLE_EXTERNAL_SYSTEM,
                        attribution.ROLE_EXTERNAL_UNSLICED,
                        attribution.ROLE_EXTERNAL_USER_MANAGER,
                        attribution.ROLE_EXTERNAL_USER_SESSION,
                        attribution.ROLE_MANAGED_UNIT})
        self.assertGreater(checked, 0, "no fixture carries an owner at all")

    def test_the_owner_rule_would_catch_the_shape_that_was_here(self):
        """The negative control, on the four rows this file used to carry."""
        self.assertEqual({"type", "ref"} - self.vocab["owner"], {"type", "ref"})

    def test_a_row_that_compares_two_states_carries_both_of_them(self):
        """AC19's input. One side alone renders the generic sentence — the word
        is chosen from the pair — so a half comparison is a fixture that looks
        like it demonstrates the criterion and does not."""
        seen = 0
        for scenario, digest in self._alert_fixtures():
            for row in digest.get("rows") or []:
                if row.get("desired_state") is None:
                    continue
                seen += 1
                with self.subTest(scenario=scenario["name"], key=row.get("key")):
                    self.assertTrue(row.get("actual_derived_state"))
        self.assertGreater(seen, 0, "no fixture demonstrates a comparison")
        self.assertIsNotNone(presentation.comparison_word(
            "DESIRED_ACTUAL_MISMATCH",
            desired="running",
            actual=next(r["actual_derived_state"]
                        for s, d in self._alert_fixtures()
                        for r in d.get("rows") or []
                        if r.get("code") == "DESIRED_ACTUAL_MISMATCH")))


def notice(*, due=True, verdict="OK", observation="新鲜", gaps=0, hhmm="14:00",
           tick=7, total=13, version="status@0123456789ab", **over) -> dict:
    """One `monitoring_policy.notice()` answer, as the renderer receives it."""
    document = {
        "due": due, "reason": "MODE_ALWAYS", "says": "每次更新都提醒",
        "verdict": verdict, "observation": observation, "gaps": gaps,
        "hhmm": hhmm, "tick": tick, "ticks_per_workday": total,
        "card_version": version, "policy_mode": "always", "bucket": "ROUTINE",
        "prompt": "none",
    }
    document.update(over)
    return document


class TheNoticeIsNotACard(unittest.TestCase):
    """It is a new message about the card, and it has to read as one.

    The whole reason the notice exists is that a PATCH raises no notification,
    so its success condition is that a person sees a NEW message. Anything card
    shaped fails that condition in the worst available way: it would be read as
    a second status card, and a reader who believes there are two dashboards
    stops trusting either.
    """

    def test_the_words_are_plain_lines_with_no_envelope(self):
        lines = cards.notice_lines(notice(), node_label="Tokyo")
        self.assertTrue(all(isinstance(line, str) for line in lines))
        text = cards.notice_text(notice(), node_label="Tokyo")
        for envelope in ('"tag"', '"schema"', "collapsible_panel", "column_set"):
            with self.subTest(envelope=envelope):
                self.assertNotIn(envelope, text)

    def test_it_names_the_card_version_it_is_about(self):
        # Without this line the notice is a second source of truth about
        # «现在怎么样», and there would be nothing to check it against.
        text = cards.notice_text(notice(version="status@fedcba987654"),
                                 node_label="Tokyo")
        self.assertIn("status@fedcba987654", text)

    def test_the_node_and_the_hour_lead_the_message(self):
        text = cards.notice_text(notice(hhmm="14:00"), node_label="Tokyo")
        self.assertTrue(text.startswith("✅ Tokyo · 状态更新 14:00"), text)
        self.assertIn("第 7/13 小时", text)

    def test_a_not_due_decision_has_no_words_and_refuses_to_borrow_any(self):
        with self.assertRaises(presentation.PresentationError) as caught:
            cards.notice_lines(notice(due=False, reason="UNCHANGED",
                                      says="这一小时的状态与上一小时相同"))
        self.assertIn("UNCHANGED", str(caught.exception))

    def test_the_node_label_falls_back_rather_than_printing_none(self):
        self.assertIn("本机", cards.notice_lines(notice())[0])


class TheNoticeQuotesAndDoesNotDecide(unittest.TestCase):
    """Every word above the version line is quoted from the row.

    A notice that computed a verdict of its own would be a second place the
    state is decided, and the two places would disagree on the hour it mattered.
    """

    def test_the_verdict_the_observation_and_the_gaps_are_the_row(self):
        text = cards.notice_text(notice(verdict="ATTENTION",
                                        observation="有项目需处理", gaps=2),
                                 node_label="Tokyo")
        self.assertIn("建议查看", text)
        self.assertIn("有项目需处理", text)
        self.assertIn("缺口 2", text)

    def test_the_words_come_from_the_one_map_and_not_from_a_call_site(self):
        for verdict, word in (("OK", "正常"), ("ATTENTION", "建议查看"),
                              ("CRITICAL", "需要处理"), ("UNCONFIRMED", "无法确认")):
            with self.subTest(verdict=verdict):
                self.assertEqual(cards.face_word(verdict), word)
                self.assertIn(word, cards.notice_text(notice(verdict=verdict)))

    def test_a_verdict_the_map_has_not_been_taught_is_never_green(self):
        # §6's rule, applied to the notice's one word: «unknown» is not «fine»,
        # and a word added later must not be able to make the face read good.
        self.assertEqual(cards.face_word("WEIRD"), cards.NEUTRAL_WORD)
        self.assertEqual(cards.face_word(None), cards.NEUTRAL_WORD)
        text = cards.notice_text(notice(verdict="WEIRD"), node_label="Tokyo")
        self.assertIn(cards.NEUTRAL_WORD, text)
        self.assertNotIn("正常", text)
        _, icon = cards._template("WEIRD")
        self.assertTrue(text.startswith(icon))

    def test_a_quiet_hour_does_not_print_a_zero(self):
        # «缺口 0» on every good hour is a line a reader learns to skip, and it
        # would sit exactly where the non-zero case needs to be noticed.
        self.assertNotIn("缺口", cards.notice_text(notice(gaps=0)))
        self.assertIn("缺口 1", cards.notice_text(notice(gaps=1)))

    def test_a_missing_hour_is_omitted_rather_than_printed_as_none(self):
        text = cards.notice_text(notice(hhmm=None, tick=None, total=None),
                                 node_label="Tokyo")
        self.assertNotIn("None", text)
        self.assertNotIn("状态更新", text)
        self.assertNotIn("小时", text)


class ColourTests(unittest.TestCase):
    """§6's "未知用中性而非正常绿", on the map that decides."""

    def test_the_verdict_words_all_have_a_template(self):
        for word in (presentation.OK, presentation.ATTENTION,
                     presentation.UNCONFIRMED, presentation.CRITICAL):
            with self.subTest(word=word):
                self.assertIn(word, cards.TEMPLATES)

    def test_unknown_and_unconfirmed_are_not_green(self):
        for word in (presentation.UNKNOWN, presentation.UNCONFIRMED):
            with self.subTest(word=word):
                self.assertNotEqual(cards._template(word)[0], "green")

    def test_a_verdict_nobody_taught_the_renderer_is_neutral_and_not_green(self):
        for word in ("", None, "PROBABLY_FINE", "ok"):
            with self.subTest(word=word):
                self.assertEqual(cards._template(word), cards.NEUTRAL_TEMPLATE)
                self.assertNotEqual(cards._template(word)[0], "green")

    def test_a_critical_event_alone_does_not_make_the_card_red(self):
        """The separation the whole axis exists for, on the measured case.

        `one_critical` carries a CRITICAL-severity event and renders ORANGE,
        because severity is what the detector said about the measurement and
        attention is what the declaration says about the subject. Nothing
        registers `/data` as a filesystem that should be in some state, so the
        honest level is "confirm this". A card keyed on severity would have
        painted the node red for it — which is the reading that made the old
        card alarming on a quiet hour.
        """
        by_name = {i["scenario"]: i["card"] for i in scenarios.rendered()}
        self.assertEqual(by_name["one_critical"]["header"]["template"], "orange")
        self.assertEqual(by_name["pressure_boundary"]["header"]["template"], "orange")

    def test_the_red_card_is_the_escalated_one_and_says_so(self):
        """Red has exactly one producer: a declared expectation whose subject
        resolves to a registration marked `criticality: critical`.

        Two scenarios are red now and the rule is unchanged — the second is the
        card this round added to photograph 未知 on an alert face, and it
        reaches red by the same escalation through the same registration. What
        is asserted is the producer, so both entries carry it: a red card whose
        event had no escalation would be a renderer choosing red for itself.
        """
        by_name = {i["scenario"]: i for i in scenarios.rendered()}
        red = [name for name, item in by_name.items()
               if item["card"]["header"]["template"] == "red"]
        self.assertEqual(sorted(red), ["critical_service_failed",
                                       "unconfirmed_open"])
        for name in red:
            event = by_name[name]["model"]["events"][0]
            self.assertEqual(event["attention"]["level"], "ACTION_REQUIRED")
            self.assertTrue(event["attention"]["escalated"])
            self.assertIn("critical", event["attention"]["because"])

    def test_the_measured_noise_batch_no_longer_interrupts(self):
        """`mixed` is the real batch that motivated the round: four live rows
        off this node's register — three `consumption` codes and one orphan
        process tree — none of them a violated expectation. It is green now,
        and the counts it used to print as events are still on the card as
        counts."""
        item = {i["scenario"]: i for i in scenarios.rendered()}["mixed"]
        self.assertEqual(item["card"]["header"]["template"], "green")
        self.assertEqual(item["model"]["events"], [])
        self.assertEqual(item["model"]["watch"]["count"], 4)

    def test_an_unread_environment_is_grey_and_not_green(self):
        by_name = {i["scenario"]: i["card"] for i in scenarios.rendered()}
        self.assertEqual(by_name["observer_failure"]["header"]["template"], "grey")
        self.assertEqual(by_name["normal"]["header"]["template"], "green")

    def test_an_unbound_gateway_does_not_grey_the_dashboard(self):
        """§6 is about readings nobody took, and this is not one of them.

        `unbound_gateway` (renamed from `partial_unknown`, which described the
        defect rather than the scenario) renders the shape this node really
        produces: the shared gateway has no service binding, the other two
        sections were read. It used to come out grey with "有项目尚未读到：
        gateway" over a body whose own text said 这一栏报为未绑定 — a heading and
        a sentence naming two different problems, one of which did not exist.

        Grey is still what an unread section gets; `observer_failure` above is
        the control for that, and without it this fix and "nothing is ever grey"
        would look the same from here.
        """
        item = {i["scenario"]: i for i in scenarios.rendered()}["unbound_gateway"]
        self.assertEqual(item["card"]["header"]["template"], "green")
        self.assertEqual(item["model"]["env_health"]["verdict"], "OK")
        # Green, and honest: the clause names the one section that has nothing
        # declared for it. "三项均已读到" would be false and §6 bans the false
        # green on the words as much as on the icon.
        self.assertIn("gateway", item["model"]["env_health"]["verdict_text"])
        # The payload, not the page. The page carries the scenario's own
        # provenance caption, which quotes the wording this scenario used to
        # produce in order to say what changed — so a scan of the page reports
        # a defect the card stopped making. `json` is the thing that would be
        # sent, and it is the only place the claim "the card does not say a
        # section went unread" can be checked without the caption answering
        # for it.
        self.assertNotIn("未读到", item["json"])

    def test_no_scenario_renders_red_or_orange_without_a_fault_in_it(self):
        """The colour has to come from the model, not from the card's mood."""
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                template = item["card"]["header"]["template"]
                if template == "grey":
                    continue
                model = item["model"]
                if model["card"] == "status":
                    self.assertNotEqual(
                        model["env_health"]["verdict"], presentation.UNCONFIRMED)
                if template == "red":
                    # Red is reserved for ACTION_REQUIRED, and the only card
                    # that can be red is the alert card.
                    self.assertEqual(model["card"], "alert")
                    self.assertTrue(any(e["attention"]["level"] == "ACTION_REQUIRED"
                                        for e in model["events"]))


class TheFaceLineIsOneBlockOneLineTests(unittest.TestCase):
    """每块一行摘要：折叠头自己就是那一行，读数一个不少地沉进折叠。

    The layout this replaced put the readings two abreast on the face — six
    readings for the host block, three rows of them, on a first screen §2 caps
    at ten to twelve lines. These tests pin the shape that replaced it, and
    every one of them is about the two ways that replacement can go wrong: a
    reading that quietly stops being shown anywhere, and a block whose state
    word is derived from something other than the verdict's own lists.
    """

    def _section(self, *cells, **extra) -> dict:
        section = {"metrics": [{"label": label, "value": value}
                               for label, value in cells]}
        section.update(extra)
        return section

    def test_the_line_is_the_declared_prefix_and_stops_at_the_budget(self):
        """Which readings, in which order, and where it stops.

        The producer emits `CPU / 负载 / 内存 / 交换区 / 磁盘 / / 磁盘 /data` and
        a face that simply took the first three would lead with a core count
        that never changes. The declaration decides the order and this checks
        the face follows it — and that it stops BEFORE exceeding the budget
        rather than after, because a summary that wraps costs two of the ten
        lines it was introduced to save.
        """
        section = self._section(
            ("CPU", "128 逻辑核"), ("负载", "12.44 / 20.1 / 27.32"),
            ("内存", "27.0%"), ("交换区", "63.6%"), ("磁盘 /", "70.2%"),
            ("磁盘 /data", "16.5%"))
        language = cards._section_language()
        budget = int(language["width"])
        used = cards._cells(cards._face_prefix("host"))
        says = cards._block_readings("host", section, used=used)

        self.assertLessEqual(used + cards._cells(says), budget)
        self.assertTrue(says.startswith("内存 27.0%"), says)
        self.assertNotIn("128 逻辑核", says)

        # It is a PREFIX of the declared readings, in the declared order — and
        # the piece after the last one shown is one that would NOT have fitted.
        by_label = {cell["label"]: cell["value"] for cell in section["metrics"]}
        pieces = [f"{label} {by_label[label]}"
                  for label in language["readings"]["host"] if label in by_label]
        shown = says.split(" · ")
        self.assertEqual(shown, pieces[:len(shown)])
        if len(shown) < len(pieces):
            self.assertGreater(
                used + cards._cells(" · ".join(pieces[:len(shown) + 1])), budget)

    def test_the_budget_is_width_and_the_heading_has_already_spent_some_of_it(self):
        """A budget on the readings alone is a budget on a line that never wraps.

        The heading shares the line, so it is passed in as spent room; and the
        unit is width, not `len()` — the same count of wide glyphs takes twice
        the room of digits, and a character count would cut the wrong one.
        """
        used = cards._cells(cards._face_prefix("host"))
        budget = int(cards._section_language()["width"])
        self.assertGreater(used, 0)
        self.assertEqual(cards._cells("27.0%"), 5)
        self.assertEqual(cards._cells("二七点零"), 8)

        section = self._section(("内存", "27.0%"))
        self.assertEqual(cards._block_readings("host", section, used=used),
                         "内存 27.0%")
        # 只剩一格时一个读数都放不下——前缀真的算进去了。
        self.assertEqual(cards._block_readings("host", section, used=budget - 1),
                         "")

    def test_a_declared_reading_the_producer_did_not_take_is_skipped(self):
        """The label is matched; a missing one is skipped, never invented.

        Printing «磁盘 /data 未读到» for a cell no producer emitted would be
        reporting a reading nobody attempted — a hole that is really an
        absence of a hole.
        """
        says = cards._block_readings("host", self._section(("内存", "27.0%"),
                                                           ("交换区", "63.6%")))
        self.assertEqual(says, "内存 27.0% · 交换区 63.6%")
        self.assertNotIn("未读到", says)

    def test_the_fold_loses_nothing_the_face_had_to_drop(self):
        """Everything the block measured is in the fold, one reading a line.

        This is what makes the shorter face honest: the reading that did not
        fit the budget is one tap away ON THE SAME LINE, not gone.
        """
        section = self._section(("负载", "12.44 / 20.1 / 27.32"),
                                ("内存", "27.0%"), ("磁盘 /", "70.2%"))
        self.assertEqual(cards._block_fold(section),
                         ["负载　12.44 / 20.1 / 27.32",
                          "内存　27.0%", "磁盘 /　70.2%"])

    def test_a_cell_keeps_its_note_and_a_missing_value_is_spelled(self):
        """A value that did not arrive must not read as a value of zero."""
        with_note = self._section(("内存", "27.0%"))
        with_note["metrics"][0]["note"] = "64.3 GiB / 247.0 GiB"
        self.assertEqual(cards._block_fold(with_note),
                         ["内存　27.0%　64.3 GiB / 247.0 GiB"])
        self.assertEqual(cards._block_fold(self._section(("配额", ""))),
                         ["配额　未读到"])

    def test_a_faulted_block_says_so_and_keeps_its_readings_below(self):
        """The state comes from the verdict's own list, not from a state test.

        A bound gateway's state is how the gateway IS and an unbound one's is
        the absence of a declaration, so `== "OK"` over both is the bug that
        once made every bound gateway render as the word BOUND.
        """
        section = self._section(("内存", "27.0%"))
        health = {"sections_read": [], "sections_unread": [],
                  "sections_faulted": ["host"]}
        self.assertEqual(cards._block_summary("host", section, health),
                         "⚠️ 建议查看")
        self.assertEqual(cards._block_fold(section), ["内存　27.0%"])

    def test_unbound_and_unreadable_are_two_states_and_not_one(self):
        """§2 names these as the pair that may not be merged.

        An established absence sends a reader nowhere — there is nothing to go
        and look at. A reading that did not happen sends them to the
        collection. One word over both sends them to the wrong one.
        """
        unbound = cards._block_summary(
            "gateway", {"state": presentation.GATEWAY_UNBOUND, "metrics": []},
            {"sections_unread": [], "sections_faulted": []})
        unread = cards._block_summary(
            "gateway", {"state": "OK", "metrics": []},
            {"sections_unread": ["gateway"], "sections_faulted": []})
        self.assertEqual(unbound, "未配置监控绑定")
        self.assertEqual(unread, "无法确认")
        self.assertNotEqual(unbound, unread)

    def test_a_bound_gateway_with_no_cells_gets_the_word_not_a_reading(self):
        says = cards._block_summary(
            "gateway", {"state": "OK", "metrics": []},
            {"sections_unread": [], "sections_faulted": []})
        self.assertEqual(says, "就绪")

    def test_no_scenario_reaches_a_block_with_no_summary_at_all(self):
        """The empty branch is a last resort and no real shape may reach it.

        Reaching it means a block was read, is not faulted, and yet matched
        none of its declared readings — the declaration and the producer have
        parted company. A bare fold header is the honest thing to print there,
        but it is a shape to be found, not one to get used to.
        """
        for item in scenarios.rendered():
            model = item["model"]
            if model.get("card") != "status":
                continue
            health = model["env_health"]
            for name in model.get("order") or []:
                with self.subTest(scenario=item["scenario"], block=name):
                    section = (model.get("sections") or {}).get(name) or {}
                    self.assertTrue(cards._block_summary(name, section, health),
                                    f"{item['scenario']} 的 {name} 落到了空摘要")


class OneModelTwoRenderingsTests(unittest.TestCase):
    """The pair has to be checkable against each other, or the preview is
    evidence about nothing."""

    def test_every_json_sentence_appears_in_the_html(self):
        # Compared against `_inline`, which is what the HTML renderer actually
        # puts on the page: the card's `**bold**` is drawn as a span there and
        # printed as asterisks in the JSON. Asking for the escaped text instead
        # would be asking the preview to expose the markup it exists to render.
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                for text in _markdown(item["card"]):
                    self.assertIn(cards._inline(text), item["html"])

    def test_the_html_shows_the_card_twice_at_the_two_widths(self):
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                self.assertEqual(item["html"].count('class="frame'), 2)
                self.assertIn(f"手机 {cards.MOBILE_WIDTH}px", item["html"])
                self.assertIn("桌面", item["html"])

    def test_the_narrow_frame_is_the_width_the_design_names(self):
        self.assertEqual(cards.MOBILE_WIDTH, 390)

    def test_the_preview_does_not_restack_the_columns_on_a_narrow_screen(self):
        """The layout may not depend on the viewport, and for one round it did.

        Feishu stacks `column_set` columns only under `flex_mode: "stretch"`;
        under the `"none"` every card here writes, a narrow screen 「按比例压缩列
        宽度» and the columns stay abreast. The preview used to carry an
        `@media (max-width: 390px)` rule that collapsed them, so the phone frame
        showed a layout the phone does not have — and it showed the flattering
        one, because stacked cells are longer and roomier than squeezed ones.
        That is the failure this whole file exists to prevent, written inside
        the preview itself.

        A `@media` rule is the shape that disagreement takes, so the test is
        that there is none. It is not «the rule was wrong»: a viewport-dependent
        layout in the preview would be wrong for any card, because the preview
        would then be describing a second rendering rather than the one the
        schema defines.
        """
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                self.assertNotIn("@media", item["html"])

    def test_every_fold_draws_the_affordance_the_card_declares(self):
        """§5/§6. The caret left the title and lives in the widget now, so the
        widget may not lose it.

        `panel` declares `down-small-ccm_outlined` and `icon_position:
        follow_text`. The titles carried a `▸` as well, and on 2026-10-04 both
        were drawn: the browser's own `<details>` marker beside the title's
        character, `▶▸` on the 390px screenshots, where the card has one
        chevron after the text. The character is gone, which makes this
        declaration the ONLY affordance a fold has — so it is asserted on the
        card as well as on the preview, and the preview is asserted to DRAW it
        rather than to reserve space for it. The document holds the card
        twice, hence the factor of two.
        """
        token = "down-small-ccm_outlined"
        glyph = cards._FOLD_ICONS[token]
        for item in scenarios.rendered():
            panels = _panel_elements(item["card"])
            for panel in panels:
                with self.subTest(scenario=item["scenario"], panel=panel):
                    header = panel["header"]
                    self.assertEqual((header.get("icon") or {}).get("token"), token)
                    self.assertEqual(header.get("icon_position"), "follow_text")
                    self.assertTrue(header.get("icon_expanded_angle"))
            with self.subTest(scenario=item["scenario"], what="drawn"):
                self.assertEqual(item["html"].count(f">{glyph}</span>"),
                                 len(panels) * 2,
                                 "预览画的折叠标记数与卡面上的折叠数对不上")

    def test_the_browsers_own_fold_marker_is_suppressed(self):
        """A `<details>` draws its own triangle, and the preview may not show a
        marker the card does not have.

        The preview translates a card; `<details>` is the closest HTML there
        is to `collapsible_panel`, and its default marker appears where
        Feishu's chevron does not — at the LEFT, before the title. Leaving it
        in place is why a card with one chevron photographed as two triangles.
        """
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                self.assertIn("::-webkit-details-marker", item["html"])
                self.assertIn("list-style: none", item["html"])
                for summary in _summaries(item["html"]):
                    # The drawn chevron IS inside the summary — it is the one
                    # the card declares — so what may carry no caret is the
                    # title, which is everything before it.
                    title = summary.split('<span class="caret')[0]
                    for caret in ("▸", "▶", "▼", "▾", "►"):
                        self.assertNotIn(caret, title)

    def test_each_column_gets_the_width_the_card_declares(self):
        """The width rule is translated, not re-decided.

        The card says `width` and `weight`; the preview repeats them as CSS
        because a preview is not Feishu and there is no one else to ask. The
        document draws the card twice, so the expected list appears twice —
        which also pins that the narrow frame and the desktop one are the same
        element list rather than two layouts.
        """
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                want = [cards._flex(column)
                        for element in _column_sets(item["card"])
                        for column in element["columns"]]
                self.assertEqual(re.findall(r'<div style="([^"]+)">', item["html"]),
                                 want + want)

    def test_the_json_output_is_the_card_and_not_a_wrapper(self):
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                card = json.loads(item["json"])
                self.assertEqual(card, item["card"])
                self.assertEqual(card["schema"], "2.0")
                self.assertIn("body", card)

    def test_rendering_the_same_model_twice_gives_the_same_card(self):
        """Determinism, so that a preview reviewed yesterday is still the card
        that would be sent today."""
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                first = cards.card_for(scenarios.model_for(scenario))
                second = cards.card_for(scenarios.model_for(scenario))
                self.assertEqual(first, second)

    def test_rendering_does_not_change_the_model_it_was_given(self):
        """A renderer that decides is a second place the decisions live."""
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                model = scenarios.model_for(scenario)
                before = copy.deepcopy(model)
                cards.card_for(model)
                self.assertEqual(model, before)


class EscapingTests(unittest.TestCase):
    """A preview is an HTML document some browser will render, and its content
    includes process names, ids and paths off a shared machine."""

    def _alert_html(self, subject_ref: str) -> str:
        model = presentation.alert_model(
            {"at": "2026-09-20T00:00:00+00:00", "device_ref": "device:stub",
             "rows": [{"key": "alert:x", "kind": "NEW", "code": "DISK_CRITICAL",
                       "severity": "high", "severity_word": "CRITICAL",
                       "subject_ref": subject_ref, "state": "OPEN",
                       "first_seen": "2026-09-20T00:00:00+00:00"}]},
            at="2026-09-20T00:00:00+00:00")
        self.assertEqual(len(model["events"]), 1)
        return cards.html(model)

    def test_a_subject_cannot_inject_markup_into_the_preview(self):
        html = self._alert_html("container:<script>alert(1)</script>")
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_a_hostile_node_label_cannot_either(self):
        model = presentation.status_model(
            {"node_label": '</title><script>alert(1)</script>',
             "mine": {"state": "OK", "lines": ["x"]},
             "host": {"state": "OK", "lines": ["y"]}},
            at="2026-09-20T00:00:00+00:00")
        html = cards.html(model)
        self.assertNotIn("<script>", html)
        self.assertIn("&lt;script&gt;", html)

    def test_an_ampersand_survives_as_text(self):
        self.assertIn("a&amp;b", self._alert_html("container:a&b"))


class TheRecoveryFaceTests(unittest.TestCase):
    """§10's five lines, and the four things the old face printed that it may not.

    `recovery_card` spent four lines per recovery:

        ✅ **<what>** / **对象** <subject> / **恢复依据** <the definition of a
        recovery, again>（state RESOLVED）/ **出现于** 2026-09-19T02:17:52+00:00
        · **恢复于** … · **持续** …

    Every clause was true. Three were defects §2 names: the raw ISO instants of
    the register put on a card read on a phone; `（state RESOLVED）` printing the
    register's own spelling of a state the sentence had already stated; and
    `恢复依据` repeating, word for word and on every item, a fact about how the
    card is built rather than about the thing that recovered. The fourth was
    that the subject — the one word a person matches against the ⚠️ they saw —
    was on the second line instead of the first.

    What is pinned here is the four properties, not the prose. The prose is in
    the renderer where a reader of the renderer can see it.
    """

    AT = "2026-09-20T00:00:00+00:00"
    ROW = {"key": "alert:1", "kind": "RECOVERED", "code": "STALE_CONTAINER",
           "severity": "medium", "severity_word": "WARNING",
           "subject_ref": "container:abc", "state": "RESOLVED",
           "first_seen": "2026-09-19T02:17:52+00:00",
           "resolved_at": "2026-09-19T02:18:52+00:00",
           "was_announced": True}

    def _face(self, rows=None) -> list[str]:
        model = presentation.recovery_model(
            {"rows": rows or [self.ROW]}, at=self.AT)
        card = cards.card_for(model)
        return [e["content"] for e in card["body"]["elements"]
                if e.get("tag") == "markdown"]

    def test_the_face_is_ten_lines_for_two_recoveries(self):
        """§10's five, and the subject leads every one of them."""
        rows = [self.ROW, {**self.ROW, "key": "alert:2",
                           "resolved_at": "2026-09-19T03:00:00+00:00"}]
        face = self._face(rows)
        first = face[0].splitlines()
        self.assertEqual(len(first), 5)
        self.assertTrue(first[0].startswith("✅ **"), first[0])
        self.assertIn("已恢复", first[0])
        # 现在 and not 出现于. §10's recovery shape closes on the current state
        # and leaves the start off, and the start is `恢复` minus `本次持续` —
        # the two lines above it — so nothing left the card. What the line
        # answers is the question a card that says ✅ is asked: is it still
        # clear.
        self.assertEqual([line.split("**")[1] for line in first[1:]],
                         ["恢复", "本次持续", "恢复前", "现在"])

    def test_the_recovery_time_is_a_clock_the_reader_is_in(self):
        """§10: 14:20, not `2026-09-19T02:18:52+00:00`.

        THE EXPECTED STRING IS COMPUTED IN ASIA/SHANGHAI AND THE RENDERER TAKES
        THE MACHINE'S ZONE, which on this node is the same one (`timedatectl`,
        2026-10-04: `Time zone: Asia/Shanghai`). They agree here and they are
        not the same guarantee: `runtime/business_calendar.py` states the rule
        for the day window — «the zone the window is read in is the calendar's,
        not the reader's», 「本机不拿自己的时区替它作答」 — and this face has no
        declaration behind it to read one from. So if this goes red on a host
        whose zone is not +08:00, the reading is not «the test is wrong»: it is
        that a card for a Beijing reader is printing times in a zone the reader
        is not in, and the fix is to give `_local` a declared zone rather than
        to change the number below.
        """
        from zoneinfo import ZoneInfo
        moment = datetime.fromisoformat(self.ROW["resolved_at"])
        expected = moment.astimezone(ZoneInfo("Asia/Shanghai")).strftime("%m-%d %H:%M")
        face = self._face()
        self.assertIn(f"**恢复** {expected}", face[0])
        for line in face:
            self.assertNotIn("+00:00", line, "a raw instant reached the face")
            self.assertNotIn("T02:", line, "a raw instant reached the face")

    def test_the_state_word_and_the_definition_are_not_on_the_face(self):
        face = self._face()
        joined = "\n".join(face)
        self.assertNotIn("RESOLVED", joined)
        self.assertNotIn("恢复依据", joined)
        self.assertNotIn("（state", joined)

    def test_the_closure_rule_is_stated_once_per_card_not_once_per_item(self):
        """It is a fact about the card, so three recoveries do not repeat it.

        Stated at all because a list of ✅ invites exactly one misreading —
        «we stopped hearing about it and called it recovered» — and a reader
        who has not read OBSERVABILITY-CARDS.md has no other way to know that
        this card's ✅ means positive evidence. It rides on the closing line,
        which already existed, rather than on a sixth line of its own: §2's
        「自我解释」 is a line spent explaining the card.
        """
        rows = [self.ROW, {**self.ROW, "key": "alert:2"},
                {**self.ROW, "key": "alert:3"}]
        face = self._face(rows)
        self.assertEqual("\n".join(face).count("肯定证据"), 1)
        # Three faces and two lines of chrome, and the chrome is named rather
        # than counted: `3 + 2` alone would go on passing if a tail line grew
        # back and something else shrank.
        #
        # `本批恢复 N 项 · 已告知 M 项` OPENED THIS LINE AND IS GONE. `已告知 M
        # 项` was the header's own «已恢复 · M 项», printed a second time eight
        # lines below itself — §2's 一个概念只出现一次 — and the batch total is
        # the header's M plus the count in the clause that replaced it. Both
        # figures are still in the payload and in `serverosctl alert digest`.
        # The split this test is about is unchanged — three faces, then this
        # line, then the footnote.
        self.assertEqual(face[3], "均为肯定证据关闭")
        self.assertEqual(face[4], cards.FOOTNOTE_READ_ONLY)

    def test_a_row_nobody_was_told_about_is_counted_on_that_line(self):
        """§5's second half, and it is the same question as the first.

        A resolution of an alert the register never delivered looks exactly
        like a recovery and is not one: nobody experienced it going wrong. The
        count rides on the closing line rather than taking a row beside it,
        because «what counts here» is one question and the two clauses are its
        two halves. Measured at 390px the pair is 318.4px of the 360px body.
        """
        rows = [self.ROW, {**self.ROW, "key": "alert:2", "was_announced": False}]
        face = self._face(rows)
        self.assertEqual(face[-2], "均为肯定证据关闭 · 1 项从未告知，不计入恢复")

    def test_a_recovery_that_did_not_fit_gets_its_own_line(self):
        """The capacity note is not the same kind of fact as the two above.

        «另有 N 项已告知的恢复未进卡面» can be true at the same time as either
        of them, and it is about the card rather than about what a ✅ means, so
        it keeps a row instead of joining a clause that is already at 318.4px
        with far less than a note's worth of room left in the row.
        """
        rows = [self.ROW] + [{**self.ROW, "key": f"alert:{n}"} for n in range(2, 5)]
        model = presentation.recovery_model({"rows": rows}, at=self.AT, limit=1)
        face = [e["content"] for e in cards.card_for(model)["body"]["elements"]
                if e.get("tag") == "markdown"]
        self.assertIn("另有 3 项已告知的恢复未进卡面", face)
        self.assertEqual(face[-2], "均为肯定证据关闭")


class RendererRefusesTests(unittest.TestCase):
    def test_an_unknown_card_kind_raises_instead_of_rendering_something(self):
        """Falling back would render it as whichever card came first, with that
        card's colour — a recovery with an alert's amber reads as bad news."""
        for model in ({"card": "not-a-card"}, {}, {"card": None},
                      {"card": "status "}):
            with self.subTest(model=model):
                with self.assertRaises(presentation.PresentationError):
                    cards.card_for(model)

    def test_the_three_card_kinds_are_each_registered(self):
        self.assertEqual(sorted(cards._RENDERERS),
                         ["alert", "recovery", "status"])

    def test_the_renderer_writes_nothing(self):
        """It is handed a model and returns two strings. No file, no socket, no
        subprocess — a renderer that wrote a file would be a producer."""
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                rendered = cards.preview(scenarios.model_for(scenario))
                self.assertEqual(sorted(rendered),
                                 ["card", "html", "json", "model"])


#: A synthetic year, for the reason `tests/test_business_calendar.py` gives at
#: length: an official calendar copied into this tree would be a copy nobody
#: re-verifies, and this fixture's own Saturday-as-workday is what makes it
#: unmistakable for one.
SYNTHETIC_CALENDAR = {
    "schema": "serveros/business-calendar.v1",
    "jurisdiction": "CN-Mainland",
    "timezone": "Asia/Shanghai",
    "source": {"document_id": "synthetic-test-notice-2031",
               "revision": "synthetic-rev-1"},
    "years": {"2031": {"workdays": ["2031-06-07"],
                       "non_working_days": ["2031-06-09"]}},
    "imported_at": "2030-12-01T00:00:00+08:00",
    "verified_at": "2030-12-01T00:00:00+08:00",
}


def calendar_reading(document=None, *, state="ABSENT", reason="未导入",
                     path="/synthetic/business-calendar.yaml"):
    """A reading in the shape `card_source.load_calendar` returns."""
    if document is None:
        return {"state": state, "declaration": None, "path": path,
                "reason": reason, "detail": "" if state == "ABSENT" else "理由"}
    return {"state": "READ",
            "declaration": source.business_calendar.declaration(document),
            "path": path, "reason": "", "detail": ""}


class StatusSourceTests(unittest.TestCase):
    """The producer, exercised against a stub so that the card's ability to be
    fed does not depend on the host being up."""

    @staticmethod
    def _reads(root_fs_percent: float = 48.0) -> dict:
        return {
            "resource summary": {
                "host": {"cpu": {"logical_cpus": 8, "model": "stub"},
                         "load": {"load1": 0.5, "load5": 0.4, "load15": 0.3,
                                  "runnable": "1/100"},
                         "memory": {"used_bytes": 1024 ** 3,
                                    "total_bytes": 8 * 1024 ** 3,
                                    "available_bytes": 7 * 1024 ** 3,
                                    "cached_bytes": 2 * 1024 ** 3,
                                    "swap": {"used_percent": 0.0,
                                             "used_bytes": 0,
                                             "total_bytes": 1024 ** 3}}},
                "disk": {"roles": [
                    {"state": "PRESENT", "mount_point": "/", "role": "ROOT_FS",
                     "source": "/dev/vda1", "used_percent": root_fs_percent,
                     "free_bytes": 1024 ** 3, "inodes": {"used_percent": 5.0}},
                    {"state": "PRESENT", "mount_point": "/data",
                     "role": "STATE_FS", "source": "/dev/vdb",
                     "used_percent": 10.0, "free_bytes": 1024 ** 3,
                     "inodes": {"used_percent": 1.0}},
                    {"state": "PRESENT", "mount_point": "/data",
                     "role": "ARTIFACTS_FS", "source": "/dev/vdb",
                     "used_percent": 10.0, "free_bytes": 1024 ** 3,
                     "inodes": {"used_percent": 1.0}}]},
                "docker": {"ServerVersion": "29.0", "ContainersRunning": 1,
                           "ContainersStopped": 0, "Containers": 1, "Images": 2},
                "user": {"summary": {"process_count": 3,
                                     "cpu_percent_of_host": 0.1,
                                     "cpu_cores": 0.01, "logical_cpus": 8,
                                     "memory_rss_bytes": 1024 ** 3,
                                     "memory_percent_of_host": 1.0,
                                     "provenance": "stub"},
                         "quota": {"mount": "/data", "filesystem": "/dev/vdb",
                                   "used_bytes": 10, "soft_limit_bytes": 100,
                                   "limit_bytes": 200, "used_percent": 10.0}},
                "advisory": {"thresholds": {"filesystem_percent_high": 85.0,
                                            "inode_percent_high": 85.0,
                                            "swap_percent_high": 90.0},
                             "notes": []}},
            "resource user-services": {"units": [], "linger": "yes",
                                       "supervisor": "systemd --user"},
            "runtime list": {"count": 1, "resources": [{"id": "x"}]},
        }

    def _gather(self, *, failing: set[tuple[str, ...]] = frozenset(),
                root_fs_percent: float = 48.0) -> dict:
        reads = self._reads(root_fs_percent)

        def run(argv):
            if argv in failing:
                return None, "exit 2"
            return reads.get(" ".join(argv)), None
        return source.gather(run=run)

    def _snapshot(self, problems=None, **kwargs) -> dict:
        return source.snapshot(self._gather(**kwargs),
                               at="2026-09-20 12:00:00 JST",
                               node_label="device:stub", problems=problems)

    def _snapshot_from(self, reads: dict) -> dict:
        """The same path `_snapshot` takes, over a stub the caller edited."""
        def run(argv):
            return reads.get(" ".join(argv)), None
        return source.snapshot(source.gather(run=run),
                               at="2026-09-29 10:00:00 JST",
                               node_label="device:stub")

    # -- a reading nobody took is never the word `None` -----------------------
    #
    # `None` is what a producer emits for a key it has and a figure it does not.
    # That is the shape this card was measured against on 2026-09-29: the live
    # mine block rendered "进程数 None" and "（None 核等效 / 共 None 核）" on a
    # face a person reads, where every other line on the same block already knew
    # how to say 未读到.

    def test_an_unread_mine_figure_is_not_the_word_none(self):
        reads = self._reads()
        reads["resource summary"]["user"]["summary"] = {
            "process_count": None, "cpu_percent_of_host": None,
            "cpu_cores": None, "logical_cpus": None,
            "memory_rss_bytes": None, "memory_percent_of_host": None}
        reads["resource user-services"]["linger"] = None
        reads["resource user-services"]["supervisor"] = None
        lines = "\n".join(self._snapshot_from(reads)["mine"]["lines"])
        self.assertNotIn("None", lines)
        self.assertIn("未读到", lines)

    def test_a_missing_core_count_is_one_absence_and_not_two(self):
        """The percentage above it has no denominator when either is missing.

        Printed as two independent fields this came out "未读到 核等效 / 共
        未读到 核" — a sentence to decode before a reader can tell that one
        measurement is gone rather than two, when the two halves are one.
        """
        reads = self._reads()
        reads["resource summary"]["user"]["summary"]["cpu_cores"] = None
        cpu_line = [line for line
                    in self._snapshot_from(reads)["mine"]["lines"]
                    if line.startswith("CPU 占整机")][0]
        self.assertNotIn("None", cpu_line)
        self.assertIn("分母", cpu_line)

    def test_a_zero_is_a_reading_and_survives_the_same_path(self):
        """The guard this replaced was a truthiness test waiting to happen.

        Zero processes and a user manager that does not linger are readings,
        and a "missing" rule written as `or '未读到'` would have eaten them.
        """
        reads = self._reads()
        reads["resource summary"]["user"]["summary"]["process_count"] = 0
        reads["resource user-services"]["linger"] = "no"
        mine = self._snapshot_from(reads)["mine"]
        # The process count is a CELL now, not a sentence. §M4/V01 read the
        # status card as 「默认直接显示CPU、RAM、容量和关键服务», so 进程 moved
        # from `lines` (the fold's prose) to `metrics` (the two-abreast face) —
        # and the reading it carries has to survive the move, which is the whole
        # point of this test: zero is a count, not the absence of one.
        cells = {m["label"]: m["value"] for m in mine["metrics"]}
        self.assertEqual(cells.get("进程"), "0 个")
        self.assertIn("linger no", "\n".join(mine["lines"]))

    def test_an_unread_host_figure_is_not_the_word_none(self):
        reads = self._reads()
        host = reads["resource summary"]["host"]
        host["load"] = {"load1": None, "load5": None, "load15": None,
                        "runnable": None}
        reads["resource summary"]["advisory"]["thresholds"] = {}
        reads["resource summary"]["docker"] = {"ServerVersion": None,
                                               "ContainersRunning": None,
                                               "ContainersStopped": None,
                                               "Containers": None,
                                               "Images": None}
        lines = "\n".join(self._snapshot_from(reads)["host"]["lines"])
        self.assertNotIn("None", lines)
        # The unit is dropped with the number. "阈值 未读到%" is half a unit, and
        # half a unit is how a reader learns to skim this line.
        self.assertNotIn("未读到%", lines)
        self.assertIn("未读到", lines)

    # -- the reads

    def test_all_four_reads_are_attempted_and_named(self):
        collected = self._gather()
        self.assertEqual(collected["read_names"],
                         ["summary", "top", "user_services", "runtime_list"])
        self.assertEqual(collected["gaps"], [])

    def test_a_healthy_stub_produces_a_clean_snapshot(self):
        snap = self._snapshot()
        self.assertEqual(snap["gaps"], [])
        self.assertEqual(snap["coverage"]["reads_missing"], [])
        self.assertEqual(snap["host"]["state"], source.OK)
        self.assertEqual(snap["mine"]["state"], source.OK)

    def test_a_failed_read_is_a_named_gap_and_never_an_empty_answer(self):
        snap = self._snapshot(failing={("resource", "summary")})
        self.assertEqual(len(snap["gaps"]), 1)
        self.assertIn("summary", snap["gaps"][0])
        self.assertIn("exit 2", snap["gaps"][0])
        self.assertEqual(snap["mine"]["state"], source.UNKNOWN)
        self.assertEqual(snap["host"]["state"], source.UNKNOWN)
        self.assertTrue(snap["mine"]["why"])
        self.assertTrue(snap["host"]["why"])

    def test_a_missing_read_does_not_render_the_block_as_fine(self):
        snap = self._snapshot(failing={("resource", "summary")})
        model = presentation.status_model(snap, at=snap["at"])
        self.assertNotEqual(model["env_health"]["verdict"], presentation.OK)
        self.assertEqual(model["sections"]["mine"]["lines"], [])

    def test_a_partly_read_account_block_is_not_green_either(self):
        """`mine` is fed by three reads and one arriving is not three."""
        snap = self._snapshot(failing={("resource", "user-services")})
        self.assertEqual(snap["mine"]["state"], source.UNKNOWN)
        self.assertTrue(snap["mine"]["lines"])  # it still shows what did arrive

    def test_every_read_the_producer_can_name_has_a_word_on_the_face(self):
        """`cards.GAP_READS` is one half of a two-sided contract, and this holds
        the other half.

        The face says WHICH reading did not arrive, in the words a person knows
        it by — §2 keeps the schema key off a first screen. That holds only
        while the renderer's table covers every name the producer can put into
        `coverage.reads_missing`, and the failure is silent in the direction
        that matters: an uncovered name is not dropped, it is printed raw by
        `GAP_READS.get(name, name)`, so the first screen quietly grows a
        `business_calendar` exactly where the old card carried one.

        The names come from the producer rather than from a list written here,
        so that a read added to `DEFAULT_READS` — or a third source of gaps in
        `snapshot` — turns this red instead of turning the card ugly. Equality
        runs both ways on purpose: a name in the table that nobody can emit is
        a promise about a reading that does not exist.
        """
        every_read_failed = self._gather(failing={("resource", "summary"),
                                                 ("resource", "top"),
                                                 ("resource", "user-services"),
                                                 ("runtime", "list")})
        snap = source.snapshot(
            every_read_failed, at="2026-09-20 12:00:00 JST",
            node_label="device:stub",
            calendar=calendar_reading(),                     # state=ABSENT
            day={"state": "INVALID", "path": "/synthetic/day.json",
                 "reason": "当天卡状态文件读不到", "detail": ""})
        named = snap["coverage"]["reads_missing"]
        self.assertEqual(set(named), {name for name, _ in source.DEFAULT_READS}
                         | {"business_calendar", "card_day_store"})
        self.assertEqual(set(cards.GAP_READS), set(named))

        # And the words reach the FACE, which is the half that was broken: the
        # gap is a finding and stays on the first screen, the key and the
        # command go one tap down with the rest of 采集口径.
        #
        # ON A GAP THAT FITS, and the second case is the other half of the
        # same contract. When the list outgrows the row the face stops naming
        # them: measured at 390px, four names is 543.4px of a 360px body and
        # wraps onto a second line — inventory sections arriving on the first
        # screen one human label at a time, which is the §2 shape this line was
        # rewritten to avoid. So the two cases are asserted together, and the
        # degradation is a decision with a measurement behind it rather than
        # something that happened to the line one day.
        def face_of(snapshot: dict) -> str:
            model = presentation.status_model(snapshot, at=snapshot["at"])
            return "\n".join(
                e.get("content", "")
                for e in cards.status_card(model)["body"]["elements"]
                if isinstance(e.get("content"), str))

        every_face = face_of(snap)
        self.assertIn("采集缺口", every_face)
        # How many, and where the names are. Both facts, no wrap, and the count
        # is the one the names would have carried.
        self.assertIn(f"{len(named)} 项读数未读到（名称见技术诊断）", every_face)

        # The named form, asserted on the line's own function. A rendered
        # snapshot with few enough gaps to fit also needs a calendar and a day
        # that were READ, and that would make this test about two other reads
        # rather than about the table. What the two halves have to hold is that
        # a name in `GAP_READS` is a name this line can say and a key is not.
        two = ["summary", "top"]
        named_line = cards._gap_line({"gap_reads": two}, [{}, {}])
        for raw in cards.GAP_READS:
            self.assertNotIn(raw, named_line, f"{raw} reached the first screen")
        for name in two:
            self.assertIn(cards.GAP_READS[name], named_line)

    # -- the shaping

    def test_the_same_filesystem_is_named_once_however_many_roles_claim_it(self):
        """`/data` carries three roles on this node, and a card that printed it
        three times reads as three disks."""
        snap = self._snapshot()
        mounts = [line for line in snap["host"]["lines"]
                  if line.startswith("文件系统")]
        self.assertEqual(len(mounts), 2)
        self.assertTrue(any("STATE_FS" in m and "ARTIFACTS_FS" in m
                            for m in mounts))

    def test_a_filesystem_over_the_published_threshold_raises_the_block(self):
        snap = self._snapshot(root_fs_percent=91.0)
        self.assertEqual(snap["host"]["state"], source.ATTENTION)
        self.assertIn("91.0%", snap["host"]["why"])

    def test_the_threshold_comes_from_the_payload_and_not_from_this_tree(self):
        """Raise the published limit and the same reading stops being a fault."""
        reads = self._reads(91.0)
        reads["resource summary"]["advisory"]["thresholds"][
            "filesystem_percent_high"] = 95.0

        def run(argv):
            return reads.get(" ".join(argv)), None
        snap = source.snapshot(source.gather(run=run), at="now")
        self.assertEqual(snap["host"]["state"], source.OK)

    def test_the_card_does_not_select_the_account_by_name(self):
        """§2 exists because the live monitor matched an owner prefix against a
        home directory. This block uses the uid the process already has."""
        snap = self._snapshot()
        self.assertEqual(snap["mine"]["detail"]["uid"], __import__("os").getuid())

    # -- the gateway

    def test_an_unbound_gateway_is_absent_rather_than_guessed(self):
        """Named, and not guessed at. §3's answer, and the card says which.

        The last two assertions used to be `state == UNKNOWN` and `verdict !=
        OK`, and they pinned a defect rather than a rule: UNBOUND is what this
        section reports when no binding names the gateway, which is a reading
        that succeeded. Rewritten to UNKNOWN on the way to the card it read as
        a reading that failed, and the whole dashboard went grey over it —
        while the block's own text said 这一栏报为未绑定.

        §6's rule is untouched and is asserted by the test below: a gateway
        that IS bound and cannot be probed is still UNKNOWN and still never OK.

        THE REGISTER IS SUPPLIED, and that is part of the scenario rather than a
        convenience. The live status path always reads it (`cmd_card_status`
        passes `source.load_problems`), so a snapshot without one is a caller
        shape production does not have — and §3.4 makes the block 无法确认 for
        it, correctly, since a card whose register nobody opened has not earned
        a green verdict. This assertion is about the GATEWAY axis, so the other
        three are handed their honest answers: an empty register, read.
        """
        snap = self._snapshot(
            problems=alert_digest.current(_Register(), at="2026-09-20 12:00:00 JST"))
        self.assertNotIn("gateway", snap,
                         "a snapshot with no binding must not carry a gateway "
                         "block for the card to fill in from somewhere")
        model = presentation.status_model(snap, at=snap["at"], binding=None)
        self.assertEqual(model["sections"]["gateway"]["state"],
                         presentation.GATEWAY_UNBOUND)
        self.assertTrue(model["sections"]["gateway"]["why"])
        self.assertEqual(model["env_health"]["verdict"], presentation.OK)
        self.assertEqual(model["env_health"]["sections_unbound"], ["gateway"])
        self.assertEqual(model["env_health"]["sections_unread"], [])

    def test_a_binding_puts_the_gateway_in_the_snapshot(self):
        binding = {"service_ref": "service:x",
                   "gateway": {"endpoint": "http://127.0.0.1:1"}}
        snap = source.snapshot(self._gather(), at="2026-09-20 12:00:00 JST",
                               binding=binding)
        self.assertIn("gateway", snap)
        model = presentation.status_model(snap, at=snap["at"], binding=binding)
        section = model["sections"]["gateway"]
        # §3's marker, asserted where it actually lives. This used to be written
        # as `assertNotEqual(section["state"], UNKNOWN)`, and it passed for the
        # wrong reason: the section's `state` was the word `BOUND`, so the check
        # was `"BOUND" != "UNKNOWN"` — true on every card ever rendered, and
        # silent about the gateway it claimed to be checking.
        self.assertEqual(section["detail"]["state"], presentation.GATEWAY_BOUND)
        self.assertEqual(section["detail"]["service_ref"], "service:x")
        # THE ADDRESS, CARRIED BESIDE THE LINES AND NOT AMONG THEM. §R15 puts
        # loopback on the list of things a reader scrolls past — UID, model
        # number, port — and `cards.status_card` prints `lines[0]` on the face
        # and folds the rest, so an endpoint at the head of `lines` was the
        # gateway block's first screen for any gateway whose only reading is
        # where it listens. It has its own field now, which is what the card
        # appends to the fold, and this asserts the two halves of that: the
        # address is still on the card, and it is not one of the lines.
        self.assertEqual(section["entry"], "本机入口 http://127.0.0.1:1")
        self.assertFalse([line for line in section["lines"]
                          if "127.0.0.1" in line],
                         "the loopback is a line again, and lines[0] is the face")

    def test_a_binding_without_a_measurement_is_not_reported_as_ok(self):
        """A binding names a gateway; it does not measure one.

        §2 gives the binding the job of saying *which* gateway this node's is,
        and nothing else. A card that read the presence of a binding as a
        reading about the service would put green over a service nobody has
        checked — and the repair for "we have not looked" and the repair for
        "it is broken" are different errands for different people.
        """
        binding = {"service_ref": "service:x",
                   "gateway": {"endpoint": "http://127.0.0.1:1"}}
        snap = source.snapshot(self._gather(), at="2026-09-20 12:00:00 JST",
                               binding=binding)
        model = presentation.status_model(snap, at=snap["at"], binding=binding)
        self.assertEqual(model["sections"]["gateway"]["state"],
                         presentation.UNKNOWN)
        self.assertNotEqual(model["env_health"]["verdict"], presentation.OK)

    def test_no_binding_is_not_a_failure_here(self):
        """The binding file does not exist on this node yet. `None` is the
        designed answer, and the module has no fallback to reach for."""
        self.assertIsNone(source.load_binding("/nonexistent/path/nope.yaml"))
        self.assertFalse(hasattr(source, "guess_gateway"))


    # -- the schedule

    def _scheduled(self, *, at="2031-06-10T09:30:00+08:00", reading=None, **kwargs):
        snap = source.snapshot(self._gather(**kwargs), at=at,
                               node_label="device:stub",
                               calendar=reading if reading is not None
                               else calendar_reading(SYNTHETIC_CALENDAR))
        return snap, presentation.status_model(snap, at=at)

    def test_a_reading_inside_the_window_reaches_the_rendered_card(self):
        snap, model = self._scheduled()
        self.assertEqual(snap["schedule"]["route"], "ROUTINE_UPDATE")
        self.assertEqual(snap["schedule"]["tick"], 2)
        line = [text for text in _lines(cards.status_card(model))
                if text.startswith("播报日程 ")]
        self.assertEqual(len(line), 1, "the schedule line is not on the card")
        self.assertIn("2031-06-10", line[0])
        self.assertIn("08:00–20:00", line[0])
        self.assertIn("2/13", line[0])

    def test_the_day_identity_is_the_one_a_dedupe_would_use(self):
        snap, _ = self._scheduled()
        self.assertEqual(snap["schedule"]["day_ref"],
                         f"CN-Mainland/2031-06-10")

    def test_a_second_tick_of_the_same_day_is_the_same_day_ref(self):
        first, _ = self._scheduled(at="2031-06-10T09:30:00+08:00")
        second, _ = self._scheduled(at="2031-06-10T11:00:00+08:00")
        self.assertEqual(first["schedule"]["day_ref"], second["schedule"]["day_ref"])
        self.assertNotEqual(first["schedule"]["tick"], second["schedule"]["tick"])

    def test_a_rest_day_claims_no_routine_broadcast(self):
        snap, model = self._scheduled(at="2031-06-09T12:00:00+08:00")
        self.assertEqual(snap["schedule"]["reason"], "NON_WORKING_DAY")
        self.assertFalse(snap["schedule"]["in_window"])
        text = " ".join(_markdown(cards.status_card(model)))
        self.assertIn("不创建例行状态卡", text)

    def test_an_unimported_calendar_is_a_gap_and_not_a_workday(self):
        """§9's third kind, and the reason the reading is carried rather than a
        declaration: «not imported» and «did not validate» repair differently."""
        for state, reason in (("ABSENT", "本年度官方日历尚未导入"),
                              ("INVALID", "文件存在但无法解析")):
            snap, model = self._scheduled(reading=calendar_reading(
                None, state=state, reason=reason))
            self.assertEqual(snap["schedule"]["kind"], "UNKNOWN")
            self.assertIsNone(snap["schedule"]["in_window"],
                              "an unknown calendar was answered as «outside the "
                              "window», which is a decision it did not make")
            self.assertEqual(snap["schedule"]["route"], "NO_ROUTINE_BROADCAST")
            self.assertEqual(len(snap["gaps"]), 1)
            self.assertIn("business_calendar", snap["gaps"][0])
            self.assertIn(reason, snap["gaps"][0])
            text = " ".join(_markdown(cards.status_card(model)))
            self.assertIn("calendar=UNKNOWN", text, state)
            self.assertIn("观察与记录照常", text, state)
            self.assertNotIn("在常规播报窗口", text, state)

    def test_a_caller_that_supplies_no_reading_gets_no_schedule_and_no_gap(self):
        """The shaper opens no file of its own: a snapshot made without a
        reading must not depend on the machine it was made on."""
        snap = source.snapshot(self._gather(), at="2031-06-10T09:30:00+08:00")
        self.assertNotIn("schedule", snap)
        self.assertEqual(snap["gaps"], [])
        model = presentation.status_model(snap, at="2031-06-10T09:30:00+08:00")
        text = " ".join(_markdown(cards.status_card(model)))
        self.assertIn("calendar=UNKNOWN", text)
        self.assertIn("没有读工作日历", text)

    def test_the_reading_chooses_which_file_and_the_default_is_the_instance_one(self):
        self.assertTrue(str(source.DEFAULT_CALENDAR_PATH).endswith(
            ".serveros/business-calendar.yaml"))
        self.assertIs(source.DEFAULT_CALENDAR_PATH,
                      source.business_calendar.DEFAULT_PATH,
                      "the path was written down a second time and can now drift")
        reading = source.load_calendar("/nonexistent/path/nope.yaml")
        self.assertEqual(reading["state"], "ABSENT")
        self.assertEqual(reading["path"], "/nonexistent/path/nope.yaml")

    def test_the_window_bounds_are_the_designs_and_not_a_local_choice(self):
        snap, _ = self._scheduled()
        self.assertEqual(snap["schedule"]["window"],
                         [source.business_calendar.WINDOW_FIRST_HOUR,
                          source.business_calendar.WINDOW_LAST_HOUR])
        self.assertEqual(snap["schedule"]["ticks_per_workday"], 13)


class NoDeltaClauseEndsInABlankLine(unittest.TestCase):
    """§1 and §6, on the line the status card leads with.

    The delta's text travels as ONE markdown element, and `presentation._delta`
    used to end its clause with a line feed — the gap clauses by construction,
    and the change list on the hours where only the roll-up moved, because
    `named_changes` excludes the derived axes and the join could be empty.
    Found in the 2026-10-04 render of `day_gap`: 4 `<p>` elements across 2
    scenarios ended in a break that no rule asked for.

    WHAT THAT BREAK DID AND DID NOT DO. It was not a visible defect: the box
    measures one line with it and without it, Chromium trims trailing
    whitespace at the end of a block, and a shot of `day_gap` with only the
    break removed is pixel-identical to the shot that still has it (see
    `_measure/isolate-newline`). The claim it made a blank line on the card was
    this round's earlier assumption and this round's own measurement refuted
    it. It is kept as a rule because the string is one markdown block handed to
    a transport whose renderer is not Chromium — a trailing break in a markdown
    block is a line break in some of them — so the break is a hazard that
    nothing here ever drew, not a line anybody saw.

    The separation between two clauses is the joiner's business, so the clauses
    carry no break of their own and the break is inserted once, only when there
    really are two lines to separate. These tests pin that rather than the
    wording: the same rule holds for the next clause somebody adds.
    """

    @staticmethod
    def _row(**over) -> dict:
        row = {"tick": 2, "hhmm": "11:00", "local": "11:00",
               "verdict": "OK", "observation": "OK",
               "sections": {"mine": "OK", "host": "OK", "gateway": "OK"},
               "gaps": 0}
        row.update(over)
        return row

    @staticmethod
    def _delta(day: dict, *, tick: int, verdict="OK", observation=None,
               sections=None, gaps=0, day_ref="CN-Mainland/2031-06-10") -> dict:
        """The delta for the instant `tick`, over a hand-built day store.

        Hand-built and not gathered: the comparison is over the card's own
        vocabulary, so the two documents it reads are a row and a schedule, and
        a test that reached the live store to make one would be answering about
        this machine.

        `verdict` and `observation` are separate parameters because they are
        separate derived axes: moving both makes `derived_changes` return two
        names, which is a different case from the one where only one moved.
        """
        health = {"verdict": verdict,
                  "observation": verdict if observation is None else observation,
                  "gaps": [f"gap-{i}" for i in range(gaps)]}
        return presentation._delta(
            day, health,
            sections or {"mine": {"state": "OK"}, "host": {"state": "OK"},
                         "gateway": {"state": "OK"}},
            {"tick": tick, "day_ref": day_ref})

    def test_a_comparison_that_skipped_hours_does_not_end_in_a_line_feed(self):
        """Two ticks apart, nothing moved: the gap clause is the whole line."""
        delta = self._delta({"history": [self._row()], "latest": None,
                             "day_ref": "CN-Mainland/2031-06-10"}, tick=4)
        self.assertEqual(delta["headline"], "与 11:00 相同")
        self.assertEqual(delta["unobserved"], 1)
        self.assertEqual(
            delta["detail"],
            "（11:00–现在缺 1 个时点读数，不是「无变化」）")

    def test_a_quiet_adjacent_hour_spends_no_line_at_all(self):
        """Nothing moved and nothing was skipped: there is no clause, and the
        consumer's `if delta.get("detail")` is what drops it."""
        delta = self._delta({"history": [self._row()], "latest": None,
                             "day_ref": "CN-Mainland/2031-06-10"}, tick=3)
        self.assertEqual(delta["unobserved"], 0)
        self.assertEqual(delta["detail"], "")
        self.assertFalse(delta["detail"])

    def test_the_roll_up_moving_alone_leaves_the_clause_alone(self):
        """`named_changes` excludes the derived axes, so the hour where only
        the verdict moved has an EMPTY list of names and the join that used to
        make the break has nothing to join."""
        delta = self._delta({"history": [self._row()], "latest": None,
                             "day_ref": "CN-Mainland/2031-06-10"},
                            tick=4, verdict="ATTENTION", observation="OK")
        self.assertEqual(delta["headline"], "较 11:00 有 1 处变化")
        self.assertEqual(delta["moved"], ["整体状态 正常→建议查看"])
        self.assertEqual(
            delta["detail"],
            "（11:00–现在缺 1 个时点读数，不是「无变化」）")

    def test_two_clauses_are_two_lines_and_the_second_is_the_last(self):
        """Both present: one break between them, none after."""
        delta = self._delta({"history": [self._row()], "latest": None,
                             "day_ref": "CN-Mainland/2031-06-10"},
                            tick=4, verdict="ATTENTION",
                            sections={"mine": {"state": "ATTENTION"},
                                      "host": {"state": "OK"},
                                      "gateway": {"state": "OK"}})
        self.assertEqual(delta["detail"].count("\n"), 1)
        gap, _, names = delta["detail"].partition("\n")
        self.assertTrue(gap.startswith("（11:00–现在缺"), delta["detail"])
        self.assertEqual(names, "我的资源 正常→建议查看")

    def test_a_comparison_across_days_does_not_end_in_a_line_feed(self):
        delta = self._delta({"history": [self._row()], "latest": None,
                             "day_ref": "CN-Mainland/2031-06-09"}, tick=4)
        self.assertEqual(delta["detail"], "（跨日比较：上一次读数是 2031-06-09 的）")

    def test_no_delta_ever_ends_its_text_in_a_break(self):
        """Every branch above, checked for the property itself rather than for
        the sentence: a `\\n` at the end, or two in a row, is a break the clause
        did not ask for and the joiner did not put there."""
        cases = [
            self._delta({"history": [self._row()], "latest": None,
                         "day_ref": "CN-Mainland/2031-06-10"}, tick=4),
            self._delta({"history": [self._row()], "latest": None,
                         "day_ref": "CN-Mainland/2031-06-10"}, tick=3),
            self._delta({"history": [self._row()], "latest": None,
                         "day_ref": "CN-Mainland/2031-06-10"},
                        tick=4, verdict="ATTENTION"),
            self._delta({"history": [self._row()], "latest": None,
                         "day_ref": "CN-Mainland/2031-06-10"},
                        tick=4, verdict="ATTENTION",
                        sections={"mine": {"state": "ATTENTION"},
                                  "host": {"state": "OK"},
                                  "gateway": {"state": "OK"}}),
            self._delta({"history": [self._row()], "latest": None,
                         "day_ref": "CN-Mainland/2031-06-09"}, tick=4),
            self._delta({"history": [], "latest": None}, tick=1),
            self._delta(None, tick=1),
            self._delta({"history": [], "latest": None},
                        tick=5, day_ref=None),
        ]
        for delta in cases:
            with self.subTest(headline=delta["headline"]):
                detail = str(delta["detail"])
                self.assertFalse(detail.endswith("\n"), repr(detail))
                self.assertNotIn("\n\n", detail)

    def test_no_rendered_line_ends_in_a_break_in_any_scenario(self):
        """The same rule at the other end of the pipe, over the real scenarios:
        a `<p>` whose content ends in a line feed carries a break nothing on
        the card asked for — the separation lives in the card's structure, not
        in the text. This is a source check, and it is the one that would have
        caught the 2026-10-04 defect: the rendered box for that paragraph
        measured one line both ways, so no measurement of the picture was ever
        going to see it.
        """
        checked = 0
        for scenario in scenarios.scenarios():
            with self.subTest(scenario=scenario["name"]):
                model = scenarios.model_for(scenario, sample=False)
                for para in re.findall(r"<p[^>]*>(.*?)</p>",
                                       cards.html(model), re.S):
                    checked += 1
                    self.assertFalse(para.endswith("\n"), repr(para[-40:]))
        self.assertGreater(checked, 20, "no paragraphs were actually rendered")


if __name__ == "__main__":
    unittest.main()


class ACatalogueHoleIsNotAFinding(unittest.TestCase):
    """§八-B, on the card. `integrity_finding` can return a document whose
    COUNT IS ZERO — held rows whose codes nothing has decided about. That
    document is a hole in the admission catalogue and not a finding, so it must
    never reach the face, the title or the level computation. It takes the same
    line as the event-row hole, in the same words: one fact, one vocabulary."""

    @staticmethod
    def _model(integrity, undecided=None):
        return {
            "schema": "x", "card": "alert", "sample": False,
            "sample_mark": None, "at": "2026-10-04T10:00:00+00:00",
            "device_ref": "device:tokyo-shared-dev-01",
            "events": [], "integrity": integrity,
            "watch": {"count": 0, "by_code": {}, "note": "n"},
            "undecided": undecided or {"count": 0, "codes": [], "note": "n"},
            "recovery": {}, "omitted": {"rows": 0, "beyond_card_cap": 0,
                                        "backlog": 0, "silent_resolutions": 0,
                                        "note": "n"},
            "severity_counts": {}, "limit": 3,
        }

    def _face(self, model):
        return "\n".join(_markdown(cards.alert_card(model)))

    def test_a_zero_count_finding_never_reaches_the_face_or_the_title(self):
        model = self._model({"kind": "monitoring-integrity", "count": 0,
                             "by_code": {}, "text": "t", "impact": "i",
                             "action": "a", "attention": {"level": None},
                             "not_a_finding": {"count": 0, "note": "n"},
                             "undecided": {"count": 2,
                                           "codes": ["GHOST_A", "GHOST_B"],
                                           "note": "n"}})
        card = cards.alert_card(model)
        title = card["header"]["title"]["content"]
        self.assertNotIn("0 个主体失去观测", title)
        face = " ".join(_markdown(card))
        self.assertNotIn("监控完整性", face)
        self.assertIn("GHOST_A", face)

    def test_the_hole_is_merged_into_the_same_line_events_use(self):
        model = self._model(
            {"kind": "monitoring-integrity", "count": 0, "by_code": {},
             "text": "t", "impact": "i", "action": "a",
             "attention": {"level": None},
             "not_a_finding": {"count": 0, "note": "n"},
             "undecided": {"count": 1, "codes": ["GHOST_B"], "note": "n"}},
            undecided={"count": 1, "codes": ["GHOST_A"], "note": "n"})
        face = " ".join(_markdown(cards.alert_card(model)))
        self.assertEqual(face.count("未在注意力表中定级"), 1, face)
        self.assertIn("GHOST_A", face)
        self.assertIn("GHOST_B", face)

    def test_a_real_finding_still_renders_as_one(self):
        model = self._model({"kind": "monitoring-integrity", "count": 3,
                             "by_code": {"DESIRED_ACTUAL_MISMATCH": 3},
                             "text": "3 declared subjects", "impact": "i",
                             "action": "a", "attention": {"level": "REVIEW",
                                                          "because": "b"},
                             "not_a_finding": {"count": 0, "note": "n"},
                             "undecided": {"count": 0, "codes": [], "note": "n"}})
        card = cards.alert_card(model)
        self.assertIn("3 个主体失去观测",
                      card["header"]["title"]["content"])


class WhatIsWrongNowIsNotTheRegister(unittest.TestCase):
    """§3.4's 当前问题 answers «现在有问题吗», and the register is bigger than
    that question. This class pins the line between the two.

    It exists because nothing did. The block was built, rendered into fifteen
    previews and reviewed by eye, and every one of those previews showed a
    two-row block — a headline and a sentence about the whole register. On this
    machine that sentence reads «登记表里还有 1360 项…» (measured 2026-10-04
    against `/data/home/vince/state/serveros/runtime/alerts/state.json`), which
    is the shape §13 forbids in the one place it forbids it: «旧账/恢复清单不塞
    日常首屏». The suite was green throughout, because a rule with no test is a
    preference.

    THE FIXTURES WERE THE REASON IT WAS INVISIBLE. Every scenario's register
    holds at most a handful of rows, so the sentence read «还有 4 项» and looked
    like context. It is not the size that is wrong — the same sentence with one
    row behind it is the same sentence — which is why the first test below uses
    a count no fixture would ever have.
    """

    AT = "2026-10-04T18:20:00+08:00"

    @staticmethod
    def _row(code, subject, **extra):
        row = {"code": code, "subject_ref": subject, "state": "OPEN",
               "severity": "medium", "first_seen": "2026-09-16T08:58:24+00:00",
               "last_seen": "2026-10-04T10:20:00+00:00"}
        row.update(extra)
        return row

    def _card(self, rows):
        """A real status card, over the real read set, with this register."""
        payloads = scenarios.read_set("measured-20260930")
        snap = source.snapshot(
            {"schema": source.SCHEMA, "reads": payloads, "gaps": [],
             "read_names": [n for n, _ in source.DEFAULT_READS]},
            at=self.AT, node_label=DEVICE,
            problems=alert_digest.current(_Register(rows), at=self.AT))
        return cards.status_card(presentation.status_model(snap, at=self.AT))

    # -- the register's below-the-face population is not the card's business --

    def test_a_large_register_does_not_put_its_size_on_the_face(self):
        """The number that broke it. 1,360 is what this node holds.

        `HIGH_MEMORY_SUSTAINED` is in the notification contract and under the
        face, so `alert_digest.current` counts it in `watch` and not in `shown`
        — the contract has already decided it is not a current problem. A card
        that prints the count under «没有需要处理的问题» re-opens that decision
        on the one screen §13 reserves for what changes a judgement, and does it
        with a four-digit number that drifts every tick while nothing acts on
        it.
        """
        rows = [self._row("HIGH_MEMORY_SUSTAINED", f"runtime-group:process-tree:s{i}")
                for i in range(1360)]
        card = self._card(rows)
        face = "\n".join(_face(card))
        self.assertIn("没有需要处理的问题", face)
        for fragment in ("1360", "登记表里还有"):
            self.assertNotIn(fragment, face,
                             "the register's below-face population reached the "
                             "face; §13 keeps 旧账 off 日常首屏")
        # And it is not dropped, which would trade one failure for the other:
        # a reader who wants the register can still find how much of it the
        # face did not carry.
        self.assertIn("1360", _detail_under(card, "技术诊断"))

    def test_the_sentence_moves_rather_than_disappearing(self):
        """«folded» and «deleted» are different answers, and §5 is why.

        A short answer is only honest with its omissions beside it — so the
        count has to be reachable. It is reachable one tap down, in the panel
        that already carries the block's own note pointing at `alert list`.
        """
        card = self._card([self._row("HIGH_MEMORY_SUSTAINED", "host:memory")])
        fold = _detail_under(card, "技术诊断")
        self.assertIn("登记表里还有 1 项", fold)
        self.assertIn("alert list", fold)

    # -- what IS about the problems stays where a person reads it ------------

    def test_the_omission_of_unlisted_problems_stays_on_the_face(self):
        """The other half of the split, and the half that must not move.

        «另有 N 项未逐条列出» is about problems — it says the list above it is
        not all of them — and a reader given «5 类问题» with three lines under
        it and no fourth would take the three for the five. Five on-face codes
        with two rows each: three codes shown, six rows covered, ten rows on the
        face in all.
        """
        codes = ["DISK_CRITICAL", "SWAP_PRESSURE", "MEMORY_PRESSURE",
                 "ROOT_FS_PRESSURE", "DATA_FS_PRESSURE"]
        rows = [self._row(code, f"filesystem:/mnt/{code.lower()}-{i}")
                for code in codes for i in range(2)]
        face = "\n".join(_face(self._card(rows)))
        self.assertIn("另有 4 项未逐条列出（共 5 类问题）", face)

    def test_an_acknowledged_problem_is_still_a_problem_on_the_face(self):
        """ACK and SNOOZE change what is said, not whether it is happening.

        §11: 已确认 is not a resolution. These rows are on the face, a person
        has answered them, and the card owes the reader both facts — which is
        why this one is a face sentence and not a fold sentence, unlike the
        register's size.
        """
        rows = [self._row("DISK_CRITICAL", "filesystem:/"),
                self._row("SWAP_PRESSURE", "host:swap", state="ACKNOWLEDGED")]
        face = "\n".join(_face(self._card(rows)))
        self.assertIn("已确认 1 项", face)
        self.assertIn("不再列在这里", face)

    def test_an_ungraded_code_is_a_face_sentence_and_not_a_fold_one(self):
        """The one below-the-fold-looking sentence that is not.

        «N 项的 code 未在注意力表中定级» is a hole in the catalogue, and a hole
        is the one thing that makes the headline itself unreliable: «没有需要
        处理的问题» is only true if the codes somebody is holding have all been
        graded. It belongs on the face for the reason the register's size does
        not — this one can change the answer above it, and that one cannot.
        """
        rows = [self._row("NOT_A_DECIDED_CODE", "host:something")]
        face = "\n".join(_face(self._card(rows)))
        self.assertIn("未在注意力表中定级", face)

    # -- an unread register reports itself the way every other read does -----

    def test_the_tool_words_are_not_on_the_face_of_an_unread_register(self):
        """§R15, applied to the fourth block.

        `card_source.load_problems` hands over whatever the subprocess last
        said — the final stderr line, or `exit N`. That is 采集口径, and this
        card already keeps 采集口径 one tap down for every other read: the face
        says which readings did not arrive, and 技术诊断 carries
        `summary（resource summary）未读到：exit 2`. The register was the one
        read reporting itself in tool vocabulary, on the first screen, in a
        sentence a reader cannot act on.
        """
        reading = alert_digest.unreadable("exit 2", at=self.AT)
        snap = source.snapshot(
            {"schema": source.SCHEMA,
             "reads": scenarios.read_set("measured-20260930"), "gaps": [],
             "read_names": [n for n, _ in source.DEFAULT_READS]},
            at=self.AT, node_label=DEVICE, problems=reading)
        card = cards.status_card(presentation.status_model(snap, at=self.AT))
        face = "\n".join(_face(card))
        self.assertIn("无法确认", face)
        self.assertIn("空白不是「没有问题」", face)
        self.assertNotIn("exit 2", face)
        self.assertNotIn("serverosctl", face)
        self.assertIn("serverosctl alert current 未读到：exit 2",
                      _detail_under(card, "技术诊断"))

    def test_a_register_nobody_handed_is_not_blamed_on_a_command(self):
        """«nothing was handed» and «the read failed» are two different facts.

        `status_model` always builds this block, so the first shape is what a
        unit caller and an older fixture produce. It has no command to name —
        nobody ran one — and a fold sentence about `serverosctl` there would be
        an account of a read that never happened, which is the same class of
        mistake as rendering «no problems» for a register nobody opened.
        """
        snap = source.snapshot(
            {"schema": source.SCHEMA, "reads": {}, "gaps": [],
             "read_names": [n for n, _ in source.DEFAULT_READS]},
            at=self.AT, node_label=DEVICE)   # no `problems=` at all
        block = presentation.problems_block(snap.get("problems"), at=self.AT)
        self.assertEqual(block["state"], "UNAVAILABLE")
        self.assertEqual(block["below"], [])
        self.assertTrue(block["why"])


class TheFoldAffordanceIsDeclaredOnce(unittest.TestCase):
    """`cards._fold_icon`: the declaration translated, not a shape of its own.

    §11 says one renderer, and the preview is the half of it that a person can
    look at. `<details>` draws a marker of its own wherever it likes, so a
    preview that translated the title and left the marker alone drew a fold
    nobody sends — two triangles at the left where the card has one chevron
    after the text, which is what the 2026-10-04 390px screenshots showed.
    """

    def header(self, **over) -> dict:
        base = {"title": {"tag": "markdown", "content": "**标题**"},
                "icon": {"tag": "standard_icon", "token": "down-small-ccm_outlined",
                         "size": "16px 16px"},
                "icon_position": "follow_text",
                "icon_expanded_angle": -180}
        base.update(over)
        return base

    def test_the_declared_token_is_what_gets_drawn(self):
        self.assertIn(cards._FOLD_ICONS["down-small-ccm_outlined"],
                      cards._fold_icon(self.header()))

    def test_follow_text_does_not_float_and_right_does(self):
        """The position decides WHERE the chevron is, so it may not be fixed.

        Every fold this product sends is `follow_text`, which puts the icon
        after the title. A preview that always drew it at the far edge would
        be wrong about every card; one that always drew it after the text
        would be wrong about any card that says `right`.
        """
        self.assertNotIn("far", cards._fold_icon(self.header()))
        self.assertIn("far", cards._fold_icon(self.header(icon_position="right")))

    def test_no_declared_position_is_feishus_default_and_not_the_title(self):
        header = self.header()
        header.pop("icon_position")
        self.assertIn("far", cards._fold_icon(header))

    def test_the_declared_angle_is_carried_into_the_css_variable(self):
        self.assertIn("--open-angle:-180deg", cards._fold_icon(self.header()))
        self.assertIn("--open-angle:0deg",
                      cards._fold_icon(self.header(icon_expanded_angle=None)))

    def test_the_declared_size_is_reserved_even_for_an_unknown_token(self):
        """A token nobody listed is drawn as `?` and NOT as a chevron — the
        rule `face_word` follows for a verdict nobody taught it. The size is
        still the declared one, because the width the header spends is a fact
        about the card whatever the glyph is.
        """
        icon = cards._fold_icon(self.header(
            icon={"token": "nobody-taught-this", "size": "20px 20px"}))
        self.assertIn(cards.UNKNOWN_FOLD_ICON, icon)
        self.assertIn("width:20px;height:20px", icon)
        self.assertNotIn(cards._FOLD_ICONS["down-small-ccm_outlined"], icon)

    def test_a_panel_that_declares_no_icon_still_draws_the_default_caret(self):
        """Feishu draws its own caret when a panel declares no icon, so a
        preview that drew nothing would show an affordance-less fold — a
        heading a reader cannot tell from a heading.
        """
        header = self.header()
        header.pop("icon")
        icon = cards._fold_icon(header)
        self.assertIn(cards.DEFAULT_FOLD_ICON, icon)
        self.assertIn("far", icon)

    def test_a_panel_that_declares_no_header_at_all_does_not_raise(self):
        for header in ({}, None):
            with self.subTest(header=header):
                self.assertIn(cards.DEFAULT_FOLD_ICON, cards._fold_icon(header))
