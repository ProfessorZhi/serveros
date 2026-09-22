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
import sys
import unittest
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
    return [e["content"] for e in (card.get("body") or {}).get("elements") or []
            if e.get("tag") == "markdown"]


class ScenarioRenderTests(unittest.TestCase):
    """Every situation, because a card that only renders on a good day is a
    card that is missing on the day it matters."""

    @classmethod
    def setUpClass(cls):
        cls.items = scenarios.rendered()

    def test_they_are_the_ones_the_design_and_the_criteria_name(self):
        """§6's six, then AC18–AC21's four, then the eleventh on neither list.

        The order is asserted, not just the set, because the three groups come
        from three different places and a scenario that migrated between them
        would be a fixture whose provenance a reviewer can no longer read off
        the file. The list is written out rather than derived so that ADDING a
        scenario is a deliberate edit to this test — a fixture nobody had to
        justify is a fixture that quietly stops matching the criteria.
        """
        self.assertEqual(
            [i["scenario"] for i in self.items],
            ["normal", "partial_unknown", "one_critical", "observer_failure",
             "mixed", "recovery_only",
             # AC18–AC21.
             "pressure_boundary", "expected_service_down", "observability_loss",
             "historical_backlog",
             # Not named by any criterion: the only scenario that renders red.
             # See the scenario's own comment for why it has to exist.
             "critical_service_failed"])

    def test_every_scenario_renders_both_outputs(self):
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                self.assertTrue(item["html"].startswith("<!DOCTYPE html>"))
                self.assertGreater(len(item["card"]["body"]["elements"]), 1)

    def test_every_scenario_uses_only_proven_element_tags(self):
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                self.assertEqual(_all_tags(item["card"]) - PROVEN_TAGS, set())

    def test_every_scenario_is_marked_as_a_sample_in_the_header(self):
        """A preview that cannot be told from a reading is worse than none."""
        for item in self.items:
            with self.subTest(scenario=item["scenario"]):
                self.assertIn(presentation.SAMPLE_MARK,
                              item["card"]["header"]["title"]["content"])
                self.assertTrue(any(presentation.SAMPLE_MARK in m
                                    for m in _markdown(item["card"])))

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
        passed on all eleven scenarios without ever looking at a face — the
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

    def test_the_integrity_heading_says_which_expectations_went_unverified(self):
        """The header carries the level and the count, so the heading's job is
        the thing the header cannot say. Its first version restated both — the
        same number three times in four lines, which is what the 390px render
        showed."""
        item = next(i for i in self.items if i["scenario"] == "observability_loss")
        model = scenarios.model_for(
            next(s for s in scenarios.scenarios()
                 if s["name"] == "observability_loss"), sample=False)
        # One markdown element holds the whole face, so the lines are split out
        # of it. Checking the element instead would have found the heading
        # wherever it was in the block.
        face = [line
                for element in _markdown(item["card"]) for line in element.split("\n")]
        heading = [line for line in face if line.startswith("**监控完整性")]
        self.assertEqual(len(heading), 1, face)
        for code in (model["integrity"]["by_code"] or {}):
            with self.subTest(code=code):
                self.assertIn(code, heading[0])
        self.assertNotIn("本次未被观测到", heading[0])


class IntegrityHeadingCapTests(unittest.TestCase):
    """The cap is the aggregation rule, one line higher up the card."""

    @staticmethod
    def _finding(codes: dict) -> dict:
        return {"kind": "monitoring-integrity", "attention": {"level": "REVIEW"},
                "count": sum(codes.values()), "by_code": codes,
                "text": "t", "impact": "i", "action": "a",
                "not_a_finding": {"count": 0, "note": "n"}}

    def test_it_names_at_most_the_cap_and_counts_the_rest(self):
        heading = cards._integrity_heading(self._finding({
            "DESIRED_ACTUAL_MISMATCH": 300, "FUNCTIONAL_PROBE_FAILED": 200,
            "TUNNEL_UNHEALTHY": 40, "PROTECTION_LOST_ON_IDENTITY_SWITCH": 2,
            "DESIRED_ACTUAL_MISMATCH2": 1}))
        self.assertEqual(heading.count("×"), cards.INTEGRITY_CODES_SHOWN)
        self.assertIn("等 2 类", heading)

    def test_the_biggest_kinds_are_the_ones_kept(self):
        """Sorted by count, not by code: the 300-row expectation is the one a
        person needs to see, and a heading that dropped it for alphabetical
        order would be hiding the largest hole behind the smallest."""
        heading = cards._integrity_heading(self._finding({
            "ZZZ_BIG": 300, "AAA_SMALL": 1, "BBB_SMALL": 1, "CCC_SMALL": 1}))
        self.assertIn("ZZZ_BIG", heading)
        self.assertNotIn("CCC_SMALL", heading)

    def test_a_finding_with_no_codes_still_gets_a_heading(self):
        """An aggregate with nothing to enumerate is still a block, and a block
        with no heading merges into the event above it."""
        self.assertEqual(cards._integrity_heading(self._finding({})),
                         "**监控完整性**")

    def test_a_count_of_one_costs_no_characters(self):
        """A heading is the most expensive line on the card and `×1` is three
        characters saying nothing. The number appears where it is the
        interesting part: a code standing behind more than one subject."""
        heading = cards._integrity_heading(self._finding({
            "ONE": 1, "MANY": 42}))
        self.assertEqual(heading, "**监控完整性 · MANY ×42 · ONE**")

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
        resolves to a registration marked `criticality: critical`."""
        by_name = {i["scenario"]: i for i in scenarios.rendered()}
        red = [name for name, item in by_name.items()
               if item["card"]["header"]["template"] == "red"]
        self.assertEqual(red, ["critical_service_failed"])
        event = by_name["critical_service_failed"]["model"]["events"][0]
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
        self.assertEqual(by_name["partial_unknown"]["header"]["template"], "grey")
        self.assertEqual(by_name["normal"]["header"]["template"], "green")

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


class OneModelTwoRenderingsTests(unittest.TestCase):
    """The pair has to be checkable against each other, or the preview is
    evidence about nothing."""

    def test_every_json_sentence_appears_in_the_html(self):
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                for text in _markdown(item["card"]):
                    self.assertIn(cards._esc(text), item["html"])

    def test_the_html_shows_the_card_twice_at_the_two_widths(self):
        for item in scenarios.rendered():
            with self.subTest(scenario=item["scenario"]):
                self.assertEqual(item["html"].count('class="frame'), 2)
                self.assertIn(f"手机 {cards.MOBILE_WIDTH}px", item["html"])
                self.assertIn("桌面", item["html"])

    def test_the_narrow_frame_is_the_width_the_design_names(self):
        self.assertEqual(cards.MOBILE_WIDTH, 390)

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

    def _snapshot(self, **kwargs) -> dict:
        return source.snapshot(self._gather(**kwargs),
                               at="2026-09-20 12:00:00 JST",
                               node_label="device:stub")

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
        snap = self._snapshot()
        self.assertNotIn("gateway", snap)
        model = presentation.status_model(snap, at=snap["at"], binding=None)
        self.assertEqual(model["sections"]["gateway"]["state"],
                         presentation.UNKNOWN)
        self.assertTrue(model["sections"]["gateway"]["why"])
        self.assertNotEqual(model["env_health"]["verdict"], presentation.OK)

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
        self.assertIn("http://127.0.0.1:1", " ".join(section["lines"]))

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


if __name__ == "__main__":
    unittest.main()
