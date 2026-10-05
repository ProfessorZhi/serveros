#!/usr/bin/env python3
"""The attention axis: which findings may interrupt a person, and why that is
not the same question as how severe they are.

The rule this file exists to keep is one sentence, and it is the round's
authority: **告警 = 实际状态违反了一个明确的预期状态，而且这个偏差值得人知道。**
Two halves, and a finding that fails either is not an alert — it may still be a
true and useful measurement, and the card still counts it, but it does not
start a sentence with a person's attention.

Almost everything below is negative, and deliberately. The convenient
implementation of this axis is a table that defaults to something; the whole
point of the axis is that an undecided code is a hole in a declaration and must
be reported as one rather than silently given a level. So the load-bearing
tests are the ones that check the table has no hole, that nothing is guessed
from a string or a colour, and that a subject which simply stopped being
observed does not become an incident.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

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


presentation = _load("serveros_runtime_presentation", "presentation.py")
# Loaded here and not in `runtime/`: `tests/` may read the detectors, so the
# check that the axis covers them lives on this side of the boundary while
# `presentation.py` stays out of the admission gate's loader set.
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")
resource_anomaly = _load("serveros_runtime_resource_anomaly", "resource_anomaly.py")
alerting = _load("serveros_runtime_alerting", "alerting.py")
alert_digest = _load("serveros_runtime_alert_digest", "alert_digest.py")
cards = _load("serveros_runtime_cards", "cards.py")


def _every_detector_code() -> set:
    """Every code any detector on this node can open, from the detectors.

    Not from the contract, and not from the catalogue: a check that asked the
    attention table to agree with itself would pass on a table that had dropped
    a code. These two sets are the producers' own declarations.
    """
    codes = set(getattr(anomaly, "SEVERITY", {}) or {})
    codes |= set(getattr(resource_anomaly, "CODES", {}) or {})
    codes |= set(getattr(resource_anomaly, "ADMISSION_LEVELS", {}) or {})
    return {str(c) for c in codes if c}


class TheTableHasNoHoleTests(unittest.TestCase):
    """An undecided code is a declared hole, not a default."""

    def test_every_detector_code_is_decided(self):
        decided = presentation.code_expectations()
        undecided = sorted(c for c in _every_detector_code() if c not in decided)
        self.assertEqual(undecided, [],
                         f"these codes have no declared expectation: {undecided}")

    def test_every_entry_names_a_kind_this_module_knows(self):
        known = {"declared", "boundary", "consumption", "history"}
        for code, entry in presentation.code_expectations().items():
            with self.subTest(code=code):
                self.assertIn(entry["kind"], known)
                self.assertIn(entry["level"], presentation.AXIS)

    def test_nothing_is_decided_by_a_substring_or_a_colour(self):
        """The decision is a table lookup. A code the table has never seen has
        no expectation, and that is reported rather than guessed — the failure
        this prevents is a new detector's code being classified by the word
        "MEMORY" appearing in it."""
        for invented in ("MEMORY_PRESSURE_V2", "NOT_A_CODE", "", "DISK_CRITICAL_X"):
            with self.subTest(code=invented):
                self.assertEqual(presentation.expectation_of(invented)["kind"], None)
                self.assertIsNone(presentation.attention_of(invented)["level"])

    def test_the_axis_order_is_the_contracts_and_the_renderers(self):
        self.assertEqual(presentation.AXIS,
                         ("ACTION_REQUIRED", "REVIEW", "WATCH",
                          "INFORMATIONAL", "NONE"))
        self.assertEqual(list(cards.presentation.AXIS), list(presentation.AXIS))

    def test_every_level_has_a_word_a_person_reads(self):
        for level in presentation.AXIS:
            with self.subTest(level=level):
                word = presentation.attention_word(level)
                self.assertTrue(word)
                self.assertNotIn(level, word)


class OnlyExpectationsInterruptTests(unittest.TestCase):
    """The two halves of the definition, tested separately."""

    def test_a_measurement_over_a_threshold_is_not_an_interruption(self):
        """A process eating memory violates nothing. It is worth knowing and it
        is not worth being interrupted for."""
        for code in ("HIGH_MEMORY_SUSTAINED", "HIGH_CPU_SUSTAINED",
                     "BUILD_CACHE_HIGH", "CONTAINER_LOG_LARGE"):
            with self.subTest(code=code):
                attention = presentation.attention_of(code)
                self.assertEqual(attention["level"], "WATCH")
                self.assertFalse(presentation.on_the_face(attention["level"]))

    def test_a_violated_declaration_is_an_interruption(self):
        for code in ("DESIRED_ACTUAL_MISMATCH", "FUNCTIONAL_PROBE_FAILED",
                     "TUNNEL_UNHEALTHY", "PROTECTION_LOST_ON_IDENTITY_SWITCH"):
            with self.subTest(code=code):
                attention = presentation.attention_of(code)
                self.assertEqual(attention["level"], "REVIEW")
                self.assertTrue(presentation.on_the_face(attention["level"]))

    def test_a_long_running_thing_that_nothing_declares_is_neither(self):
        """Age is not a violation. These are the rows that made the old card
        unreadable — 500 of them, all true, none an expectation."""
        for code in ("AGE_WITHOUT_ACTIVITY", "STALE_CONTAINER", "RUNTIME_UNOWNED",
                     "ORPHAN_PROCESS_TREE", "EXTERNAL_SYSTEM_PROCESS_TREE"):
            with self.subTest(code=code):
                self.assertEqual(presentation.attention_of(code)["level"],
                                 "INFORMATIONAL")
                self.assertFalse(presentation.on_the_face("INFORMATIONAL"))

    def test_a_lifecycle_mismatch_is_not_a_violated_declaration(self):
        """`RUNTIME_OUTLIVED_OWNER` sat in `declared` until 2026-09-24, and it
        was the only hygiene code there — which is why it was the only one of
        them on the ⚠️需确认 face, and why the face on this host was made of it.

        The bucket's membership test is stated in the contract and this code
        fails it: a `declared` code needs something on this node saying what
        the subject's state SHOULD be, and nothing declares that a process
        group must not outlive its login session. What the code reports is a
        lifecycle mismatch it measured itself.

        The live evidence, because a code moved off a face for looking untidy
        and a code moved off a face for being misfiled look identical in the
        diff: 323 alerts in one week, 306 `process-tree` and 17
        `claude-session`, and the only three that never closed were sessions
        held in `closing` — a state they never leave, so the finding was
        reported forever, in two other accounts' names and in this operator's
        own. Nothing about that row was a question a person could answer, and
        the level it carried was the only thing that made it a question.
        """
        self.assertEqual(presentation.expectation_of("RUNTIME_OUTLIVED_OWNER")["kind"],
                         "history")
        level = presentation.attention_of("RUNTIME_OUTLIVED_OWNER")["level"]
        self.assertEqual(level, "INFORMATIONAL")
        self.assertFalse(presentation.on_the_face(level))
        # Still alert truth: off the face is not out of the register.
        self.assertIn("RUNTIME_OUTLIVED_OWNER", anomaly.SEVERITY)

    def test_moving_it_out_of_the_declaration_bucket_removed_its_escalation(self):
        """The consequence, asserted rather than left to be discovered.

        `escalation` is `declared` PLUS a critical registration. A code with no
        declaration has nothing for a criticality to weigh, so this one can no
        longer reach ACTION_REQUIRED however critical the registered resource
        is. That is the move working, not a regression: the escalation question
        is "how important is the declaration being violated", and the honest
        answer here is that there is no declaration.
        """
        self.assertEqual(presentation.attention_of("RUNTIME_OUTLIVED_OWNER",
                                                   criticality="critical")["level"],
                         "INFORMATIONAL")
        # And the mechanism is still there for the codes that do declare one,
        # so the assertion above is about this code and not about escalation
        # having been switched off.
        self.assertEqual(
            presentation.attention_of("DESIRED_ACTUAL_MISMATCH",
                                      criticality="critical")["level"],
            "ACTION_REQUIRED")


class TheBoundaryBucketAgreesWithTheThresholdsTests(unittest.TestCase):
    """`boundary` is the bucket fed by declared thresholds, so it has to be the
    set of codes the threshold table actually grades."""

    def test_every_code_the_gate_would_refuse_on_is_boundary(self):
        """The admission gate and the attention axis are two axes, and they
        meet at exactly one place: a code the gate would REFUSE a creation for
        has to be a declared threshold, because refusing is the strongest thing
        this system can do to a caller and a default is not a declaration.

        `advisory` is allowed to differ, and it does: `BUILD_CACHE_HIGH` is a
        consumption measurement the gate only advises about, and the card says
        WATCH for it. Asserting all of `ADMISSION_LEVELS` would be asserting
        that two axes are one axis.
        """
        levels = dict(getattr(resource_anomaly, "ADMISSION_LEVELS", {}) or {})
        refusing = sorted(c for c, lvl in levels.items() if lvl == "refuse")
        self.assertTrue(refusing, "no code refuses; this check would pass "
                                  "vacuously")
        for code in refusing:
            with self.subTest(code=code):
                self.assertEqual(presentation.expectation_of(code)["kind"],
                                 "boundary")

    def test_the_two_filesystem_spellings_of_one_measurement_agree(self):
        """DISK_WARNING is WATCH and ROOT_FS_PRESSURE / DATA_FS_PRESSURE are the
        same measurement read by the other surface. A filesystem one code calls
        a warning and another calls a pressure is not a reason to interrupt
        LESS, so the two critical tiers agree and the alias is stated."""
        self.assertEqual(presentation.attention_of("DISK_WARNING")["level"], "WATCH")
        for code in ("ROOT_FS_PRESSURE", "DATA_FS_PRESSURE", "DISK_CRITICAL"):
            with self.subTest(code=code):
                self.assertEqual(presentation.attention_of(code)["level"], "REVIEW")

    def test_a_threshold_measurement_is_never_action_required_on_its_own(self):
        for code in sorted(set(getattr(resource_anomaly, "ADMISSION_LEVELS", {}))):
            with self.subTest(code=code):
                self.assertNotEqual(
                    presentation.attention_of(code)["level"], "ACTION_REQUIRED")


class EscalationNeedsADeclarationTests(unittest.TestCase):
    """Red has exactly one producer, and it is a registration."""

    def test_without_a_registration_the_level_stays_review(self):
        attention = presentation.attention_of("DESIRED_ACTUAL_MISMATCH",
                                              criticality=None)
        self.assertEqual(attention["level"], "REVIEW")
        self.assertFalse(attention["escalated"])

    def test_an_important_registration_does_not_escalate(self):
        """`important` is the node's most common criticality, and it is not a
        licence to interrupt harder. Only `critical` is."""
        attention = presentation.attention_of("DESIRED_ACTUAL_MISMATCH",
                                              criticality="important")
        self.assertEqual(attention["level"], "REVIEW")
        self.assertFalse(attention["escalated"])

    def test_a_critical_registration_escalates_and_says_why(self):
        attention = presentation.attention_of("FUNCTIONAL_PROBE_FAILED",
                                              criticality="critical")
        self.assertEqual(attention["level"], "ACTION_REQUIRED")
        self.assertTrue(attention["escalated"])
        self.assertIn("critical", attention["because"])

    def test_escalation_is_never_invented_for_a_code_with_no_expectation(self):
        """A history code about a critical resource is still not an incident:
        escalation moves `declared` findings, and these are not that."""
        for code in ("STALE_CONTAINER", "AGE_WITHOUT_ACTIVITY"):
            with self.subTest(code=code):
                attention = presentation.attention_of(code, criticality="critical")
                self.assertEqual(attention["level"], "INFORMATIONAL")
                self.assertFalse(attention["escalated"])

    def test_escalation_cannot_change_whether_a_row_may_be_printed(self):
        """The delivery gate does not pass `criticality`, and does not need to.

        `alert_digest.face_table` is built from `attention_of(code)` alone, with
        no criticality, and every row is then answered from that one table. That
        is only sound while BOTH ends of `escalation` are on the face, so the
        contract is checked against itself rather than trusted: a `to:` naming a
        level off the face would make the gate quietly wrong in a direction
        nothing else here would see.
        """
        for code in presentation.code_expectations():
            with self.subTest(code=code):
                self.assertEqual(
                    presentation.on_the_face(
                        presentation.attention_of(code)["level"]),
                    presentation.on_the_face(
                        presentation.attention_of(code,
                                                  criticality="critical")["level"]))

    def test_no_level_is_reachable_only_through_a_string(self):
        """Every level in the axis is produced by at least one code, so an
        unreadable card cannot be blamed on a level nobody could reach."""
        produced = {e["level"] for e in presentation.code_expectations().values()}
        for level in ("ACTION_REQUIRED", "REVIEW", "WATCH", "INFORMATIONAL"):
            with self.subTest(level=level):
                self.assertTrue(
                    level in produced
                    or level == "ACTION_REQUIRED",  # escalation only, by design
                    "a level nothing produces is a level nobody reviewed")


class TheGateAndTheFaceAreOneRuleTests(unittest.TestCase):
    """`alert_digest.decision` may be caused only by what `alert_model` prints.

    Two modules read one axis, and the failure they drift into is the one this
    node actually had: the gate counted 460 findings at high/medium as worth a
    card while the face classified every one of them `watch` and printed none,
    so a card went out once an hour saying `✅ 没有需要处理的变化`. Agreeing
    per code across the whole closed mapping is the only form of this check that
    cannot come back a third time through a code nobody thought about.
    """

    AT = "2026-09-24T01:00:00+00:00"

    @classmethod
    def setUpClass(cls):
        cls.codes = sorted(presentation.code_expectations())
        cls.rows = {
            code: {"key": f"alert:{code}", "code": code, "kind": "NEW",
                   "severity": "high", "state": "OPEN",
                   "subject_ref": f"subject:{code}",
                   "first_seen": "2026-09-24T00:00:00+00:00", "owner": None}
            for code in cls.codes
        }
        cls.model = presentation.alert_model(
            {"rows": list(cls.rows.values()), "held": [],
             "device_ref": "device:test"},
            at=cls.AT, limit=len(cls.codes))

    def test_the_join_saw_every_decided_code(self):
        """A join over three codes agrees trivially. If the table ever stopped
        being enumerable this has to fail rather than pass quietly."""
        self.assertTrue(self.codes)
        self.assertEqual(sorted(self.rows), self.codes)

    def test_the_gate_and_the_face_give_the_same_answer_for_every_code(self):
        printed = {event["ref"] for event in self.model["events"]}
        for code, row in self.rows.items():
            with self.subTest(code=code):
                self.assertEqual(
                    alert_digest.may_interrupt(row), f"alert:{code}" in printed,
                    f"the delivery gate and the card face disagree about {code}")

    def test_between_them_the_three_buckets_account_for_every_code(self):
        """A code that landed in no bucket would make the test above pass by
        being absent from `events`, which is the shape of a silent hole."""
        self.assertEqual(
            len(self.model["events"]) + self.model["watch"]["count"]
            + self.model["undecided"]["count"], len(self.codes))

    def test_a_code_the_face_counts_may_not_cause_a_card(self):
        """`watch` and `undecided` are both off the face and both refuse. The
        difference between them matters to a READER of the card — one is a
        declared level below the threshold, the other is a hole in the
        declaration — and not to the gate."""
        printed = {event["ref"] for event in self.model["events"]}
        for code, row in self.rows.items():
            if f"alert:{code}" in printed:
                continue
            with self.subTest(code=code):
                self.assertFalse(alert_digest.may_interrupt(row))


class MonitoringIntegrityTests(unittest.TestCase):
    """AC6 and AC20 are one rule read from two sides."""

    @staticmethod
    def _held(code: str, kind: str | None) -> dict:
        return {"alert_ref": f"alert:{code}", "code": code, "subject_ref": "x",
                "severity": "medium", "hold_kind": kind,
                "hold_reason": "the measurement was not reported this tick",
                "first_seen": "2026-09-18T06:00:00+00:00",
                "occurrence_count": 2, "was_announced": False}

    def test_a_declared_subject_losing_observation_is_one_finding(self):
        """542 rows and one cause is one line. The count is the only number."""
        rows = [self._held("DESIRED_ACTUAL_MISMATCH", "unmeasured") for _ in range(542)]
        finding = presentation.integrity_finding(rows)
        self.assertIsNotNone(finding)
        self.assertEqual(finding["count"], 542)
        self.assertIn("542", finding["text"])

    def test_a_history_subject_losing_observation_is_not_a_finding(self):
        """AC20. Nothing declares these should still exist, so "we cannot see
        it" is a fact about our looking and not about the world. This is the
        historical backlog the criteria name, and it must not alert."""
        rows = [self._held("HIGH_MEMORY_SUSTAINED", "unmeasured"),
                self._held("ORPHAN_PROCESS_TREE", "unmeasured"),
                self._held("STALE_CONTAINER", "unmeasured")]
        self.assertIsNone(presentation.integrity_finding(rows))

    def test_a_held_row_that_was_measured_is_not_a_finding_either(self):
        """`above_floor` means somebody looked and the value is still up. The
        card is being told the same thing it was told last hour, and nothing
        became unobservable."""
        rows = [self._held("DESIRED_ACTUAL_MISMATCH", "above_floor")]
        self.assertIsNone(presentation.integrity_finding(rows))

    def test_the_two_halves_are_counted_separately_and_never_summed(self):
        finding = presentation.integrity_finding(
            [self._held("DESIRED_ACTUAL_MISMATCH", "unmeasured"),
             self._held("STALE_CONTAINER", "unmeasured")])
        self.assertEqual(finding["count"], 1)
        self.assertEqual(finding["not_a_finding"]["count"], 1)

    def test_an_undecided_code_is_a_hole_and_not_filed_as_history(self):
        """§八-B. `history` says "the contract decided this code carries no
        expectation, so not seeing it is a fact about our looking". An
        UNDECIDED code is "nobody decided", which is the distinction
        `attention_of` refuses to collapse — and filing it under the history
        note made the node assert an expectation-free status for a code it had
        never looked at. That is the incomplete admission catalogue."""
        rows = [self._held("NOT_A_DECIDED_CODE_AT_ALL", "unmeasured")]
        finding = presentation.integrity_finding(rows)
        self.assertIsNotNone(finding, "a catalogue hole must not vanish")
        self.assertEqual(finding["count"], 0)
        self.assertEqual(finding["undecided"]["count"], 1)
        self.assertEqual(finding["undecided"]["codes"],
                         ["NOT_A_DECIDED_CODE_AT_ALL"])
        self.assertEqual(finding["not_a_finding"]["count"], 0)
        self.assertIn("未决定", finding["undecided"]["note"])
        self.assertNotIn("没有任何东西声明", finding["not_a_finding"]["note"])

    def test_a_decided_code_still_goes_to_history_and_not_to_the_hole(self):
        """The other half: the change separates two facts, it does not move
        every held row into the alarming bucket."""
        finding = presentation.integrity_finding(
            [self._held("HIGH_MEMORY_SUSTAINED", "unmeasured")])
        self.assertIsNone(finding, "a consumption code held is not a finding "
                                   "and is not a catalogue hole either")

    def test_the_wording_is_declared_and_not_built_here(self):
        """The sentence lives in `notification-contract.yaml`. A renderer that
        wrote its own would be the second presentation authority this round
        forbids."""
        finding = presentation.integrity_finding(
            [self._held("DESIRED_ACTUAL_MISMATCH", "unmeasured")])
        block = presentation.attention_policy()["monitoring_integrity"]
        self.assertEqual(finding["text"], block["what"].format(count=1))
        self.assertEqual(finding["impact"], block["impact"])
        self.assertEqual(finding["action"], block["action"])

    def test_a_contract_without_the_wording_is_refused(self):
        """No fallback. A fallback is how the wording migrates from the
        declaration into the renderer without anybody deciding to move it."""
        good = presentation.attention_policy()
        for missing in ("what", "impact", "action"):
            broken = {k: v for k, v in good.items() if k != "monitoring_integrity"}
            block = dict(good["monitoring_integrity"])
            block.pop(missing)
            broken["monitoring_integrity"] = block
            with self.subTest(missing=missing):
                original = presentation._contract
                presentation._contract = lambda: broken
                try:
                    with self.assertRaises(presentation.PresentationError):
                        presentation._integrity_words()
                finally:
                    presentation._contract = original

    def test_the_finding_is_never_escalated_to_action_required(self):
        """Losing sight of something is not the same as something being broken.
        The honest level is REVIEW at most."""
        rows = [self._held("DESIRED_ACTUAL_MISMATCH", "unmeasured")]
        self.assertEqual(presentation.integrity_finding(rows)["attention"]["level"],
                         "REVIEW")


class _StubStore:
    """Only what `deltas()` asks of a store: the registers it reads.

    A hand-built register rather than the live one. The live alert register is
    a mutable file this host's daemon writes to while the suite runs, so a test
    that read it would assert about whatever happened to be true at that
    moment — and would be the third reader of a file whose readers are counted.
    """

    def __init__(self, alerts: list[dict]) -> None:
        self._alerts = {str(a["alert_ref"]): a for a in alerts}

    def alerts(self) -> dict:
        return self._alerts


class TheDigestCarriesTheDiscriminatorTests(unittest.TestCase):
    """AC20 is implemented one layer down, and this is that layer.

    `hold_reason` is English prose written by `alerting`. A card that decided
    "did this subject stop being measured" by reading it would be a card whose
    behaviour changes when somebody fixes a typo in a sentence. `hold_kind` is
    the machine-readable half, and it has to survive the projection.
    """

    def test_alerting_classifies_a_hold_rather_than_only_describing_it(self):
        source = (RUNTIME / "alerting.py").read_text(encoding="utf-8")
        self.assertIn("hold_kind", source)
        for kind in ("unmeasured", "not_evaluated", "not_a_measurement",
                     "margin_undeclared", "unusable_measurement",
                     "below_floor", "above_floor"):
            with self.subTest(kind=kind):
                self.assertIn(kind, source)

    def test_the_digest_publishes_held_rows_separately_from_deltas(self):
        source = (RUNTIME / "alert_digest.py").read_text(encoding="utf-8")
        self.assertIn('"held"', source)

    def test_the_held_block_is_not_a_delta(self):
        """Whatever `deltas()` puts in `held` is not a change. A caller that
        merged it into the change list would announce every held row every
        hour, and the rows it would announce are the ones nobody looked at."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            cursor = alert_digest.DeliveryCursor(Path(tmp) / "cursor.json")
            # A delta without a baseline is refused by design, and the refusal
            # is the point of the baseline: everything already in the register
            # at that instant is known-and-never-announced, so the backlog is
            # countable instead of retroactive. The baseline has to be taken
            # BEFORE the rows exist for the row below to stay a delta.
            alert_digest.ensure_baseline(cursor, _StubStore([]),
                                         at="2026-09-17T00:00:00+00:00",
                                         device_ref="device:stub")
            store = _StubStore([
                {"alert_ref": "alert:held", "state": "OPEN", "code": "STALE_CONTAINER",
                 "subject_ref": "container:abc", "severity": "medium",
                 "hold_kind": "unmeasured", "hold_reason": "not reported this tick",
                 "first_seen": "2026-09-18T06:00:00+00:00",
                 "occurrence_count": 3, "resolved_at": None},
                {"alert_ref": "alert:resolved", "state": "RESOLVED",
                 "code": "HIGH_MEMORY_SUSTAINED", "subject_ref": "container:def",
                 "severity": "high", "hold_kind": None, "hold_reason": None,
                 "first_seen": "2026-09-18T06:00:00+00:00",
                 "occurrence_count": 1,
                 "resolved_at": "2026-09-18T07:00:00+00:00"},
            ])
            found = alert_digest.deltas(store, cursor, at="2026-09-20T00:00:00+00:00",
                                        policy_block={"include_severities": ["high"]})
        self.assertIn("held", found)
        self.assertEqual([r["alert_ref"] for r in found["held"]], ["alert:held"])
        # The held row is NOT in the delta list. The register below also
        # contains a real delta — an unannounced resolution — and it is here on
        # purpose: asserting `rows == []` would pass on a version of this
        # module that had stopped classifying anything at all.
        self.assertNotIn("alert:held", [r.get("key") for r in found["rows"]])
        self.assertIn("alert:resolved", [r.get("key") for r in found["rows"]])

    def test_a_held_row_without_a_kind_is_not_carried_at_all(self):
        """The discriminator is what puts a row in the block. Carrying rows
        whose hold nobody classified would put unclassified prose in front of
        the integrity rule, which decides by kind and would then have to guess."""
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = _StubStore([
                {"alert_ref": "alert:legacy", "state": "OPEN", "code": "STALE_CONTAINER",
                 "subject_ref": "container:abc", "severity": "medium",
                 "hold_kind": None, "hold_reason": "still above the floor",
                 "first_seen": "2026-09-18T06:00:00+00:00",
                 "occurrence_count": 3, "resolved_at": None},
            ])
            cursor = alert_digest.DeliveryCursor(Path(tmp) / "cursor.json")
            alert_digest.ensure_baseline(cursor, _StubStore([]),
                                         at="2026-09-17T00:00:00+00:00",
                                         device_ref="device:stub")
            found = alert_digest.deltas(store, cursor, at="2026-09-20T00:00:00+00:00",
                                        policy_block={"include_severities": ["high"]})
        self.assertEqual(found["held"], [])

    def test_only_the_lost_observation_kinds_are_integrity_candidates(self):
        self.assertEqual(presentation.LOST_OBSERVATION,
                         frozenset({"unmeasured", "not_evaluated"}))


class TheCardCannotActTests(unittest.TestCase):
    """§15's `alert != authorization`, at this layer."""

    def test_no_attention_word_reaches_a_machine(self):
        """The words are display text. The axis is read by the renderer and by
        nothing else, and the renderer writes no card that names an operation.
        """
        source = (RUNTIME / "cards.py").read_text(encoding="utf-8")
        for verb in ("subprocess", "os.system", "docker ", "systemctl ",
                     "requests.", "urllib"):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, source)


if __name__ == "__main__":
    unittest.main()
