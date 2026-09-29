#!/usr/bin/env python3
"""The presentation model: what it refuses to say, asserted rather than intended.

Most of this file is negative. That is not an accident of style. The model's
whole job is to take a row of measurements and decline to turn it into more
than it is — to say UNKNOWN where the producer recorded nothing, to measure a
duration to the end that actually exists, to keep a recovery nobody was told
about off a recovery card. Each of those is a place where the convenient
answer is wrong, and the convenient answer is what a later edit will reach for.

A test that only checked the happy path would pass on a version of this module
that reported every unread section as green.
"""
from __future__ import annotations

import ast
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

# The detectors are loaded HERE and nowhere in `runtime/`. That is the point of
# this side of the boundary: `tests/` may read both, so the check that the
# catalogue covers them can live here, while `presentation.py` stays outside
# the admission gate's loader set (see its own note, and
# `test_resource_anomaly.py::test_nothing_in_the_tree_yet_creates_through_the_admission_gate`).
anomaly = _load("serveros_runtime_anomaly", "anomaly.py")
resource_anomaly = _load("serveros_runtime_resource_anomaly", "resource_anomaly.py")


def _catalogue() -> dict:
    import yaml
    return yaml.safe_load(
        (RUNTIME / "card-definitions.yaml").read_text(encoding="utf-8")) or {}


# ---------------------------------------------------------------------------
# §4 · the definition catalogue
# ---------------------------------------------------------------------------

#: A definition may not connect two facts. §4 names the exact failure it is
#: preventing — reading `restart failed` as `network fault` — and every word
#: below is a way to connect them.
FORBIDDEN_CAUSE = ("因为", "导致", "由于", "引起", "造成", "所以", "根因",
                   "because", "due to", "caused by", "therefore")

#: Words that name an operation on a machine. Kept to the unambiguous ones:
#: `停止` is deliberately absent because "已停止的对象" is a state a detector
#: reports, and a substring ban that cannot tell a state from an instruction
#: would force the catalogue to stop describing what it measured.
MACHINE_OPS = ("重启", "重新启动", "删除", "清理", "回收", "杀掉", "杀死",
               "重建", "卸载", "kill", "restart", "delete", "remove",
               "prune", "terminate")

#: The forms a refusal takes in this codebase. The card's one refusal line has
#: to contain one of them, which is what stops the ban on the line above from
#: being satisfied by a catalogue that simply stopped refusing anything.
NEGATIONS = ("不", "未", "无需", "非", "别")


class DefinitionCatalogueTests(unittest.TestCase):
    """The catalogue is a declaration; these are the ways it could lie."""

    def test_the_catalogue_covers_exactly_what_the_detectors_can_raise(self):
        """Both directions, because both are failures.

        A definition for a code no detector produces is an invented code that
        will sit in the file looking authoritative. A detector code with no
        definition is worse: it reaches a card as a bare identifier with no
        sentence, at the moment somebody needs one.
        """
        declared = set(_catalogue().get("codes") or {})
        raised = set(getattr(anomaly, "SEVERITY", {}) or {}) | set(
            getattr(resource_anomaly, "CODES", ()) or ())
        self.assertTrue(raised, "neither detector declared any code")
        self.assertEqual(declared - raised, set(), "catalogue invents codes")
        self.assertEqual(raised - declared, set(), "detector codes undefined")

    def test_the_two_detectors_both_reach_the_catalogue(self):
        """The positive control for the union above.

        `anomaly.SEVERITY` and `resource_anomaly.CODES` are different shapes
        from different modules, and a version of the test above that read only
        one of them would pass while half the codes were undefined. So each
        side is asserted to be non-empty on its own.
        """
        self.assertTrue(set(getattr(anomaly, "SEVERITY", {}) or {}))
        self.assertTrue(set(getattr(resource_anomaly, "CODES", ()) or ()))

    def test_every_definition_says_something(self):
        for code, entry in (_catalogue().get("codes") or {}).items():
            with self.subTest(code=code):
                self.assertIsInstance(entry, dict)
                self.assertTrue(str(entry.get("what") or "").strip(),
                                f"{code} has no `what`")

    def test_no_definition_connects_two_facts(self):
        """§4's ban on a generated root cause, enforced here and not by review."""
        for code, entry in (_catalogue().get("codes") or {}).items():
            for field in ("what", "impact"):
                text = str(entry.get(field) or "")
                for word in FORBIDDEN_CAUSE:
                    with self.subTest(code=code, field=field, word=word):
                        self.assertNotIn(word, text)

    def test_no_definition_names_an_operation(self):
        """A `what` line describes what was measured, never what to do.

        `impact` is held to the same rule: an impact that names an operation is
        an instruction wearing the word "impact".
        """
        for code, entry in (_catalogue().get("codes") or {}).items():
            for field in ("what", "impact"):
                text = str(entry.get(field) or "")
                for op in MACHINE_OPS:
                    with self.subTest(code=code, field=field, op=op):
                        self.assertNotIn(op, text)

    def test_no_action_names_an_operation_even_to_refuse_one(self):
        """§15's `alert != authorization`, in the one place words could break it.

        The catalogue is display text and no word in it reaches a machine — but
        a line reading "重启该服务" on a card is read by a person as an
        instruction, and this node's alerts go to somebody who could carry it
        out.

        This rule used to have an exception: an operation was allowed inside a
        clause that refused it ("本卡不会重建它"). The refusal is now made once
        for the whole card, in `cards.FOOTNOTE_READ_ONLY`, so a catalogue line
        has no business naming an operation at all — and a ban with no
        exceptions is one a reviewer can check by eye, which the per-clause
        version was not. The card's own line is asserted below, because a ban
        with nothing on the other side of it would pass on a card that had
        stopped refusing anything.
        """
        for code, text in (_catalogue().get("actions") or {}).items():
            for op in MACHINE_OPS:
                with self.subTest(code=code, op=op):
                    self.assertNotIn(op, str(text))

    def test_the_card_states_the_refusal_once_and_in_the_refusing_form(self):
        """The positive control for the ban above.

        With every operation word out of the catalogue, one line on the card is
        the only place §15 is stated. If that line stopped naming an operation —
        or named one without refusing it — every test above would still pass
        and the card would have quietly stopped saying that a suggestion is not
        an authorization.

        The check is on the whole line and not clause by clause. The footnote is
        one sentence whose 「未执行」 governs a list, and splitting it on 、 cuts
        「重启」 away from the word that refuses it — a per-clause reading would
        fail a line that is correct.
        """
        cards = _load("serveros_runtime_cards", "cards.py")
        line = cards.FOOTNOTE_READ_ONLY
        self.assertTrue(any(op in line for op in MACHINE_OPS),
                        f"{line!r} names no operation, so the catalogue's ban "
                        "is no longer paired with a refusal anywhere")
        self.assertTrue(any(n in line for n in NEGATIONS),
                        f"{line!r} names an operation without refusing it")

    def test_a_code_with_no_definition_is_reported_as_a_hole(self):
        """ABSENT is a first-class answer, not a fallback to something else."""
        answer = presentation.definition_for("NOT_A_REAL_CODE_AT_ALL")
        self.assertEqual(answer["state"], presentation.ABSENT)
        self.assertIsNone(answer["what"])
        self.assertIn("NOT_A_REAL_CODE_AT_ALL", answer["why"])
        self.assertEqual(presentation.definition_for("DISK_CRITICAL")["state"],
                         presentation.DECLARED)

    def test_the_model_loads_no_detector_itself(self):
        """The gate's second half, asserted from the side that may read both.

        `presentation.py` is not in `tests/test_resource_anomaly.py`'s loader
        set and must stay out of it. It is also not allowed to quietly acquire
        a detector through some other name — the two spellings are the only two
        ways to reach a module loaded by filename.
        """
        source = (RUNTIME / "presentation.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        strings = {n.value for n in ast.walk(tree)
                   if isinstance(n, ast.Constant) and isinstance(n.value, str)}
        self.assertNotIn("resource_anomaly.py", strings)
        self.assertNotIn("serveros_runtime_resource_anomaly", strings)


# ---------------------------------------------------------------------------
# §5 · certainty and duration
# ---------------------------------------------------------------------------

    def test_a_says_word_is_held_to_the_same_bans_as_a_what_line(self):
        """§4's ban is about sentences, not about which field they live in.

        `says` was added for AC19 and would otherwise have been the one place a
        cause word could be written without anything noticing — the same hole
        the gateway block above had, in the same shape.
        """
        words = 0
        for code, entry in (_catalogue().get("codes") or {}).items():
            for rule in entry.get("says") or []:
                words += 1
                for word in (str(rule.get("word") or ""),):
                    for banned in FORBIDDEN_CAUSE + MACHINE_OPS:
                        with self.subTest(code=code, word=word, banned=banned):
                            self.assertNotIn(banned, word)
                with self.subTest(code=code, rule=rule):
                    self.assertTrue(str(rule.get("word") or "").strip())
                    self.assertTrue(rule.get("desired"))
                    self.assertTrue(rule.get("actual"))
        self.assertGreater(words, 0, "no `says` rule exercises this test")

    def test_every_pair_the_comparison_can_emit_has_a_word(self):
        """The positive half of AC19, checked against the producer's mapping.

        Which pairs a mismatch can carry is decided by
        `managed-runtime-contract.yaml`'s `desired_actual.mismatch_states` and
        nowhere else — `desired.py` refuses to call anything a mismatch unless
        that map names it. So this test reads the map rather than a list, and a
        contract change that added a mismatch state without a word for it would
        fail here instead of reaching a card as the generic sentence.

        The other direction is asserted below, and it is why the second
        `NOT_READY` rule is not dead code pretending to be live.
        """
        import yaml
        contract = yaml.safe_load(
            (RUNTIME / "managed-runtime-contract.yaml").read_text(encoding="utf-8"))
        mismatches = (contract.get("desired_actual") or {}).get("mismatch_states") or {}
        self.assertTrue(mismatches, "the contract names no mismatch state")
        emit_able = {(declared, derived) for derived, declared in mismatches.items()}
        declared = set()
        for entry in (_catalogue().get("codes") or {}).values():
            for rule in entry.get("says") or []:
                for desired in rule.get("desired") or ():
                    for actual in rule.get("actual") or ():
                        declared.add((str(desired), str(actual)))
        self.assertEqual(emit_able - declared, set(),
                         "a pair the producer emits has no declared word")

    def test_the_one_declared_pair_the_comparison_cannot_emit_is_named(self):
        """AC19 says 「不可用/未就绪」, and only the first half is reachable.

        `mismatch_states` names `NOT_RUNNING` alone, so a registered resource
        that is alive and not answering never reaches this comparison — the
        probe codes report it instead (`FUNCTIONAL_PROBE_FAILED`, whose live
        row carries `liveness: RUNNING, functional: FAILED`). The `NOT_READY`
        word is kept because a widened contract should not also need a card
        change, and this test is where "declared but not reachable today" is
        written down rather than discovered. Widening the contract is an
        alerting-semantics decision with an alert-volume cost, so it is not
        made here.
        """
        self.assertEqual(
            presentation.comparison_word("DESIRED_ACTUAL_MISMATCH",
                                         desired="running", actual="NOT_READY"),
            "未就绪")
        import yaml
        contract = yaml.safe_load(
            (RUNTIME / "managed-runtime-contract.yaml").read_text(encoding="utf-8"))
        mismatches = (contract.get("desired_actual") or {}).get("mismatch_states") or {}
        self.assertNotIn("NOT_READY", mismatches,
                         "the contract now names NOT_READY: this test and the "
                         "card's reachability note are both out of date")

    def test_the_gateway_wording_is_declared_and_connects_nothing(self):
        """A gateway line is a sentence a person reads, so the ban applies to it.

        This block is NOT under `codes:` — a provider line is not a finding a
        detector opened, and filing it there would make every routed model look
        like an alert. But the rule §4 states is about sentences, not about
        which table they live in. Leaving the new table unscanned would make
        `gateway:` the one place a cause word could be added without anything
        going red, which is the shape of the defect this class exists to catch.
        """
        gateway = _catalogue().get("gateway") or {}
        providers = gateway.get("providers") or {}
        self.assertTrue(
            str(providers.get("line") or "").strip(),
            "gateway providers have no declared `line`; the wording would have "
            "to come from whatever measured the number, which is the second "
            "presentation authority this table exists to remove")
        for field, template in providers.items():
            for word in FORBIDDEN_CAUSE:
                with self.subTest(field=field, word=word):
                    self.assertNotIn(word, str(template))
        words = gateway.get("state_words") or {}
        self.assertTrue(words, "no gateway state words are declared")
        for state, word in words.items():
            with self.subTest(state=state):
                self.assertTrue(str(word).strip(), f"{state} has an empty word")
                for banned in FORBIDDEN_CAUSE:
                    self.assertNotIn(banned, str(word))
        probes = gateway.get("probes") or {}
        self.assertTrue(probes, "no probe wording is declared")
        for state, line in probes.items():
            with self.subTest(probe=state):
                self.assertTrue(str(line).strip(), f"probe {state} has no line")
                for banned in FORBIDDEN_CAUSE:
                    self.assertNotIn(banned, str(line))


class CertaintyTests(unittest.TestCase):
    def test_a_row_with_no_recorded_freshness_is_unknown(self):
        """The live register is full of these, so this is the common case."""
        answer = presentation.certainty_of({"provenance": {"source": "registry",
                                                           "observed_at": None}})
        self.assertEqual(answer["state"], presentation.UNKNOWN)
        self.assertTrue(answer["why"])

    def test_no_provenance_at_all_is_unknown_and_not_fresh(self):
        """`graph.freshness_of`'s rule, one layer up: a window nobody declared
        cannot make something fresh."""
        answer = presentation.certainty_of({})
        self.assertEqual(answer["state"], presentation.UNKNOWN)
        self.assertNotEqual(answer["state"], "FRESH")

    def test_a_stated_freshness_is_passed_through_with_its_provenance(self):
        answer = presentation.certainty_of({"provenance": {
            "freshness": "STALE", "source": "docker-inspect",
            "observed_at": "2026-09-20T00:00:00Z"}})
        self.assertEqual(answer["state"], "STALE")
        self.assertEqual(answer["source"], "docker-inspect")
        self.assertEqual(answer["observed_at"], "2026-09-20T00:00:00Z")

    def test_an_unknown_freshness_word_is_not_taken_as_fresh(self):
        answer = presentation.certainty_of({"provenance": {"freshness": "MAYBE"}})
        self.assertEqual(answer["state"], presentation.UNKNOWN)


class DurationTests(unittest.TestCase):
    """`alert_digest.duration_seconds` is `at - first_seen`; this is not that.

    The live register made the difference concrete: a RECOVERED row that was
    open for about a minute carries `duration_seconds: 53358` — 14.8 hours —
    because it is measured to now rather than to when it ended. A card printing
    that number tells a person a flapping process ran for most of a day.
    """

    def test_a_resolved_row_is_measured_to_its_resolution(self):
        answer = presentation.open_duration(
            {"state": "RESOLVED", "first_seen": "2026-09-19T02:17:52+00:00",
             "resolved_at": "2026-09-19T02:18:52+00:00"},
            at="2026-09-20T14:00:00+00:00")
        self.assertAlmostEqual(answer["seconds"], 60.0, places=3)
        self.assertEqual(answer["basis"], "resolved_at - first_seen")

    def test_a_resolved_row_open_for_a_minute_never_reads_as_hours(self):
        """The regression, stated as the number that was wrong."""
        answer = presentation.open_duration(
            {"state": "RESOLVED", "first_seen": "2026-09-19T02:17:52+00:00",
             "resolved_at": "2026-09-19T02:18:52+00:00"},
            at="2026-09-20T16:46:10+00:00")
        self.assertLess(answer["seconds"], 120)

    def test_an_open_row_is_measured_to_now(self):
        answer = presentation.open_duration(
            {"state": "OPEN", "first_seen": "2026-09-20T13:00:00+00:00"},
            at="2026-09-20T14:30:00+00:00")
        self.assertAlmostEqual(answer["seconds"], 5400.0, places=3)
        self.assertEqual(answer["basis"], "now - first_seen")

    def test_an_unparseable_end_is_unmeasured_and_says_why(self):
        answer = presentation.open_duration({"state": "OPEN", "first_seen": "—"},
                                            at="2026-09-20T14:30:00+00:00")
        self.assertIsNone(answer["seconds"])
        self.assertIn("first_seen", answer["why"])

    def test_two_clocks_disagreeing_is_unmeasured_and_not_a_negative(self):
        answer = presentation.open_duration(
            {"state": "RESOLVED", "first_seen": "2026-09-20T10:00:00+00:00",
             "resolved_at": "2026-09-20T09:00:00+00:00"},
            at="2026-09-20T14:00:00+00:00")
        self.assertIsNone(answer["seconds"])
        self.assertIn("两个时钟", answer["why"])

    def test_an_unmeasured_duration_renders_as_unknown_not_as_zero(self):
        self.assertEqual(presentation.human_duration(None), "未知")
        self.assertEqual(presentation.human_duration(0), "0 秒")


# ---------------------------------------------------------------------------
# §17 · the subject, in words
# ---------------------------------------------------------------------------

class SubjectDisplayTests(unittest.TestCase):
    """A ref is machine identity; a card needs a name. The conversion has one
    rule — say what the producer said, in the order it said it — and these are
    the shapes the live register actually carries."""

    def test_a_session_process_tree_names_the_session_and_puts_the_pid_below(self):
        """AC3: the face carries a name, the pid rides in the detail.

        `session-11127:pid1533977` is the ref, and it was on the card's first
        line — a raw identifier where a person reads. The face now says which
        conversation the finding is about; `precise` keeps the pid, because it
        is the only handle somebody has when they go to look, and a handle
        removed is a finding nobody can act on. Both halves are asserted here:
        a version that dropped the pid and a version that put it back on the
        face would each fail one of them.
        """
        answer = presentation.subject_display(
            "runtime-group:process-tree:session-11127:pid1533977")
        self.assertIn("会话 11127", answer["text"])
        self.assertNotIn("1533977", answer["text"])
        self.assertNotIn("session-11127:pid", answer["text"])
        self.assertIn("1533977", answer["precise"])
        self.assertIn("会话 11127", answer["precise"])
        self.assertEqual(answer["rule"], "runtime-group:process-tree:session-")

    def test_a_docker_process_tree_is_not_read_as_a_session(self):
        answer = presentation.subject_display(
            "runtime-group:process-tree:system.slice:docker:491d41804a0e")
        self.assertIn("Docker", answer["text"])
        self.assertEqual(answer["rule"],
                         "runtime-group:process-tree:system.slice:docker:")

    def test_a_long_opaque_id_is_abbreviated_and_a_named_one_is_not(self):
        """`litellm-shared-postgres` is a name a person recognises; a bare
        container hash is not.

        Abbreviating both would throw away the only readable part of the
        second; abbreviating neither puts a 64-character string on a 390px
        screen and pushes the actual finding off it.
        """
        named = presentation.subject_display("container:litellm-shared-postgres")
        self.assertEqual(named["text"], "容器 litellm-shared-postgres")
        hashed = presentation.subject_display("container:edyl7bsgsw8qk3m9p0r2")
        self.assertIn("…", hashed["text"])
        self.assertLess(len(hashed["text"]), 30)

    def test_no_display_name_is_derived_from_an_agent(self):
        """CARDS-STYLE R16, asserted against the table that decides it.

        The kind in a session-tree ref is `f"{agent}-session"` and `agent` comes
        from `graph.identify_agent` — a REGEX OVER PROCESS NAMES carrying
        `status: INFERRED`. R16: a cgroup's `session-15413.scope` may only be
        called an OS login session, and only an `AgentSessionProvider`-proven
        identity may be displayed as 「Claude 会话」/「Codex 会话」.

        This is not hypothetical on this node. The live `session-15413` group
        renders as `claude-session` because one process of its 45 matched
        `/claude(-code)?$/`, and the tree is a VS Code Server — the face said
        "Claude 会话 15413" about a thing that is not one, and the operator
        reading the card asked which Claude session it meant.

        The enumeration is the load-bearing half: a third agent added to
        `graph.AGENT_RULES` gets a `…-session` kind the moment it is added, and
        without this loop it would silently fall through to the opaque
        `runtime-group:` rule while the two rules above kept working.
        """
        graph = _load("serveros_runtime_graph", "graph.py")
        self.assertTrue(graph.AGENT_RULES, "no agent rules; this would be vacuous")
        for agent, _pattern in graph.AGENT_RULES:
            with self.subTest(agent=agent):
                answer = presentation.subject_display(
                    f"runtime-group:{agent}-session:session-15413:pid1315723")
                self.assertEqual(answer["rule"], f"runtime-group:{agent}-session:")
                self.assertIn("登录会话", answer["text"])
                self.assertNotIn(agent, answer["text"])
                self.assertNotIn(agent, answer["precise"])
                self.assertIn("登录会话", answer["precise"])

    def test_an_unknown_prefix_is_printed_whole_and_named_as_unexpanded(self):
        """`verbatim` is a rule and not the absence of one.

        A card whose subject came out as the raw ref has to be distinguish-
        able from one that was understood, and the rule name is what makes
        that checkable without diffing strings. Guessing at an unrecognised
        shape is how `...:session-150:pid28541` becomes "会话 150" with nothing
        holding the pid that the alert is actually about — a declared rule may
        shorten a face, but only one that also hands over the precise form.
        """
        answer = presentation.subject_display("some-new-kind:abc123")
        self.assertEqual(answer["rule"], "verbatim")
        self.assertEqual(answer["text"], "some-new-kind:abc123")
        self.assertEqual(answer["ref"], "some-new-kind:abc123")

    def test_an_empty_subject_is_named_rather_than_left_blank(self):
        answer = presentation.subject_display(None)
        self.assertTrue(answer["text"])
        self.assertIsNone(answer["ref"])

    def test_no_subject_ever_loses_its_own_ref(self):
        for ref in ("runtime-group:process-tree:session-1:pid2",
                    "container:litellm-shared-postgres",
                    "filesystem:mount:/data", "tunnel:abc"):
            with self.subTest(ref=ref):
                self.assertTrue(presentation.subject_display(ref)["text"])


# ---------------------------------------------------------------------------
# §2/§3 · the status card's blocks
# ---------------------------------------------------------------------------

class EnvHealthTests(unittest.TestCase):
    """§6's "未知用中性而非正常绿", applied to the summary above the blocks."""

    def _sections(self, **states):
        out = {}
        for name in presentation.SECTIONS:
            state = states.get(name, presentation.OK)
            out[name] = {"name": name, "state": state, "lines": [], "why": ""}
        return out

    def test_a_section_nobody_read_never_yields_ok(self):
        health = presentation.env_health(
            self._sections(gateway=presentation.UNKNOWN), at="now")
        self.assertNotEqual(health["verdict"], "OK")
        self.assertEqual(health["verdict"], "UNCONFIRMED")

    def test_the_unread_section_is_named_and_not_counted(self):
        """"一项未读到" sends the reader hunting through three blocks."""
        health = presentation.env_health(
            self._sections(host=presentation.UNKNOWN), at="now")
        self.assertIn("host", health["verdict_text"])
        self.assertIn("host", health["sections_unread"])

    def test_usability_and_observation_quality_are_two_answers(self):
        """§2 draws them as two, and they have different repairs."""
        health = presentation.env_health(self._sections(), at="now",
                                         gaps=["summary 未读到：exit 2"])
        self.assertEqual(health["verdict"], "OK")
        self.assertEqual(health["observation"], "部分新鲜")
        self.assertIn("1", health["observation_text"])

    def test_all_three_read_and_nothing_faulted_is_the_only_ok(self):
        health = presentation.env_health(self._sections(), at="now")
        self.assertEqual(health["verdict"], "OK")
        self.assertEqual(health["observation"], "新鲜")

    def test_a_fault_and_an_unread_block_are_both_reported(self):
        health = presentation.env_health(
            self._sections(host="CRITICAL", gateway=presentation.UNKNOWN),
            at="now")
        self.assertEqual(health["verdict"], "UNCONFIRMED")
        self.assertIn("需处理", health["verdict_text"])
        self.assertIn("未读到", health["verdict_text"])

    # -- UNBOUND is neither faulted nor unread --------------------------------
    #
    # The card that shipped this defect, rendered 2026-09-29: title "❔ 本机 ·
    # 资源播报" in grey over a body whose gateway block said "这一栏报为未绑定".
    # The heading and the sentence under it named two different problems and
    # only one of them existed.

    def test_an_unbound_gateway_is_an_answer_and_the_verdict_is_ok(self):
        """UNBOUND was established by looking. UNKNOWN is a reading nobody took."""
        health = presentation.env_health(
            self._sections(gateway=presentation.GATEWAY_UNBOUND), at="now")
        self.assertEqual(health["verdict"], "OK")
        self.assertEqual(health["sections_unread"], [])
        self.assertEqual(health["sections_unbound"], ["gateway"])

    def test_the_ok_above_an_unbound_gateway_names_it(self):
        """Green is right; "三项均已读到" would be false, and §6 bans the
        false green on the words as much as on the icon."""
        health = presentation.env_health(
            self._sections(gateway=presentation.GATEWAY_UNBOUND), at="now")
        self.assertIn("gateway", health["verdict_text"])

    def test_an_unbound_gateway_is_not_counted_as_read_either(self):
        """"Read" and "unbound" are two buckets, and the footer prints both.

        `cards.status_card` renders `观测范围 <sections_read>`; letting the
        unbound section in there would print "已读到 gateway" about the one
        block the card says nothing was read for.
        """
        health = presentation.env_health(
            self._sections(gateway=presentation.GATEWAY_UNBOUND), at="now")
        self.assertNotIn("gateway", health["sections_read"])
        self.assertNotIn("gateway", health["sections_unread"])

    def test_a_gateway_with_no_reading_is_still_never_green(self):
        """The positive control for the three tests above.

        §6's rule has to survive the fix: this is the case that must NOT have
        been made green, and without it "the card went grey for no reason" and
        "the card goes green for anything" are indistinguishable from here.
        """
        health = presentation.env_health(
            self._sections(gateway=presentation.UNKNOWN), at="now")
        self.assertEqual(health["verdict"], "UNCONFIRMED")
        self.assertIn("gateway", health["sections_unread"])
        self.assertEqual(health["sections_unbound"], [])

    def test_unbound_is_not_faulted_either(self):
        """It was in the `faulted` tuple, in a branch nothing could reach.

        `_gateway_state` rewrote the value to UNKNOWN before it arrived, so the
        entry never matched. Letting UNBOUND through unchanged without removing
        it would have turned a declaration gap in this monitor into "有项目需
        处理" — a fault in the environment, which is a different repair and
        somebody else's to make.
        """
        health = presentation.env_health(
            self._sections(gateway=presentation.GATEWAY_UNBOUND), at="now")
        self.assertNotEqual(health["verdict"], presentation.ATTENTION)
        self.assertNotIn("需处理", health["verdict_text"])


class GatewayBindingTests(unittest.TestCase):
    """§2: the shared gateway is chosen by binding, and by nothing else."""

    def test_no_binding_reports_unbound_with_a_reason(self):
        answer = presentation.gateway_section({}, binding=None)
        self.assertEqual(answer["state"], presentation.GATEWAY_UNBOUND)
        self.assertTrue(answer["why"])
        self.assertIsNone(answer["endpoint"])

    def test_an_unbound_gateway_is_not_filled_from_a_lookalike(self):
        """The snapshot may be full of things that look like a gateway. None of
        them is one unless a binding says so."""
        answer = presentation.gateway_section(
            {"gateway": {"endpoint": "http://127.0.0.1:4000",
                         "providers": [{"text": "looks like one"}]}},
            binding=None)
        self.assertEqual(answer["state"], presentation.GATEWAY_UNBOUND)
        self.assertIsNone(answer["endpoint"])
        self.assertEqual(answer["providers"], [])

    def test_a_binding_selects_the_section(self):
        answer = presentation.gateway_section(
            {"gateway": {"endpoint": "http://127.0.0.1:4000",
                         "providers": [{"text": "p"}]}},
            binding={"service_ref": "service:x", "source": "declared"})
        self.assertEqual(answer["state"], presentation.GATEWAY_BOUND)
        self.assertEqual(answer["service_ref"], "service:x")

    def test_the_unbound_word_survives_into_the_card_row(self):
        """`_gateway_state` rewrote UNBOUND to UNKNOWN on the way to the card.

        Pinned at this level rather than only through `env_health`, because the
        rewrite happened here: a future change that reintroduces it would still
        leave `env_health` correct and the card grey, and the grey card is the
        thing a person sees.
        """
        row = presentation._gateway_state(
            presentation.gateway_section({"gateway": {}}, binding=None))
        self.assertEqual(row["state"], presentation.GATEWAY_UNBOUND)
        self.assertEqual(row["lines"], [])
        self.assertIn("未绑定", row["why"])

    def test_the_maintainer_is_a_display_field_and_not_an_authority(self):
        answer = presentation.gateway_section(
            {"gateway": {"maintainer": "vince"}},
            binding={"service_ref": "service:x"})
        self.assertEqual(answer["maintainer"], "vince")
        self.assertNotIn("restart", str(answer).lower())

    def test_no_owner_or_name_is_used_to_find_a_gateway(self):
        """The rule as code, because the failure it prevents is a selector
        somebody adds later that looks harmless.

        Docstrings are stripped first. This module *documents* the rule by
        quoting it — "不因名称或 owner=ubuntu 排除" — and a scanner that counted
        the quotation as a violation would force the code to stop naming the
        rule it exists to keep, which is the same trap `_code_references` in
        `test_resource_anomaly.py` was written to avoid.
        """
        tree = ast.parse((RUNTIME / "presentation.py").read_text(encoding="utf-8"))
        docstrings = set()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                                     ast.AsyncFunctionDef)):
                continue
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
        literals = [n.value for n in ast.walk(tree)
                    if isinstance(n, ast.Constant) and isinstance(n.value, str)
                    and id(n) not in docstrings]
        for needle in ("ubuntu", "litellm-shared", "SHARED_GATEWAY_OWNER"):
            with self.subTest(needle=needle):
                self.assertEqual([s for s in literals if needle in s], [])

    def test_the_provider_line_comes_from_the_declared_table(self):
        """The proof is a mutation, not a reading of the output.

        Asserting that the rendered line *looks* right would pass just as well
        if the sentence were assembled inside this module — which is precisely
        the arrangement being removed. So the declared template is swapped for
        a recognisable one, and the rendered line is required to follow it.
        """
        provider = {"family": "DeepSeek", "state": "RECENT_SUCCESS",
                    "requests": 412, "ok": 410, "failed": 2, "p95_ms": 900}
        before = presentation._gateway_provider_line(provider)
        self.assertIn("412", before, "the declared line did not render the count")
        self.assertIn("900", before, "the p95 suffix was not rendered")

        original = presentation._catalogue

        def swapped() -> dict:
            return {"gateway": {
                "providers": {"line": "SWAPPED {family} {requests}"},
                "state_words": {"RECENT_SUCCESS": "SWAPPED-STATE"}}}

        try:
            presentation._catalogue = swapped
            after = presentation._gateway_provider_line(provider)
        finally:
            presentation._catalogue = original
        self.assertEqual(
            after, "SWAPPED DeepSeek 412",
            "the rendered line did not follow the declared template — the "
            "wording is being decided somewhere other than card-definitions.yaml")

    def test_a_provider_with_no_traffic_is_not_rendered_as_a_success(self):
        """§6, applied to a rate: silence is not good news.

        A model with no traffic in the window is UNKNOWN and gets its own
        sentence. Rendering it through the ordinary line would put a "成功 0"
        in front of somebody and invite them to read a dead route as a quiet one.
        """
        line = presentation._gateway_provider_line(
            {"family": "GPT", "state": "UNKNOWN", "requests": 0, "ok": 0,
             "failed": 0})
        self.assertIn("没有流量", line)
        self.assertNotIn("成功", line)

    def test_a_provider_that_only_has_text_still_reaches_the_card(self):
        """The fallback is narrow on purpose: hiding a real reading would be a
        worse answer than an inconsistently-worded one."""
        line = presentation._gateway_provider_line({"text": "旧形状的一行"})
        self.assertEqual(line, "旧形状的一行")
        self.assertIsNone(presentation._gateway_provider_line({}))
        self.assertIsNone(presentation._gateway_provider_line("not a row"))

    def test_the_probe_line_is_declared_and_a_failed_probe_is_not_a_broken_service(self):
        """The probe is run by whoever can reach the gateway, so its wording is
        declared here like everything else in this block.

        The distinction being pinned is the one §18 names: a probe that could
        not connect has established something about the probe, not about the
        service. A line that read `UNKNOWN` as a fault would turn one
        observation error into an accusation.
        """
        self.assertIn("连不上", presentation._gateway_probe_line({"state": "UNKNOWN"}))
        self.assertNotIn(
            "未就绪", presentation._gateway_probe_line({"state": "UNKNOWN"}),
            "UNKNOWN is being rendered with the DEGRADED line — a probe that "
            "could not connect is not a probe that found the service not ready")
        self.assertIn("未就绪", presentation._gateway_probe_line({"state": "DEGRADED"}))
        # An undeclared state word is rendered verbatim rather than blanked:
        # the probe said something, and the card does not get to decide it said
        # nothing.
        self.assertEqual(presentation._gateway_probe_line({"state": "NEW_WORD"}),
                         "NEW_WORD")
        self.assertEqual(presentation._gateway_probe_line({"text": "旧形状"}),
                         "旧形状")
        self.assertIsNone(presentation._gateway_probe_line({}))

    # -- what the section hands the card ------------------------------------
    #
    # The two tests below were both written against defects that reading the
    # source did not reveal and running it did. Neither was caught by a test
    # that asserted the block "renders"; both are about a value that was
    # computed, carried, and then dropped on the way to the card.

    def _bound(self, **gateway):
        """A snapshot whose gateway block carries what a binding composed."""
        return {"mine": {"state": "OK", "lines": []},
                "host": {"state": "OK", "lines": []},
                "gateway": {"endpoint": "http://127.0.0.1:26510", **gateway}}

    def test_a_bound_gateway_reports_what_was_measured_not_the_word_bound(self):
        """`state` says whether a binding exists; it is not a reading.

        `gateway_section` marks a bound section `BOUND`, and `_gateway_state`
        used to read the card's state out of that same key. `env_health` has no
        opinion about the word `BOUND` — it is not in the faulted list and not
        in the unread list — so a bound gateway fell between the two and the
        verdict above it said "三项均已读到且无异常". A gateway measured
        ATTENTION reported as fine, in green, which is the exact false-green §6
        exists to forbid.
        """
        model = presentation.status_model(
            self._bound(state="ATTENTION"), at="now",
            binding={"service_ref": "service:x"})
        self.assertEqual(model["sections"]["gateway"]["state"], "ATTENTION",
                         "the card is reporting the binding marker instead of "
                         "the measured state")
        self.assertEqual(model["env_health"]["verdict"], presentation.ATTENTION,
                         "a gateway in ATTENTION was not reported as needing "
                         "attention")

    def test_a_binding_with_no_reading_is_unknown_and_never_ok(self):
        """The other half of the same key. A binding names a gateway; it does
        not measure one. With no `state` measured, the section is UNKNOWN —
        never OK, because "we know which gateway it is" is not evidence that it
        works."""
        model = presentation.status_model(
            {"mine": {"state": "OK", "lines": []},
             "host": {"state": "OK", "lines": []},
             "gateway": {"endpoint": "http://127.0.0.1:26510"}},
            at="now", binding={"service_ref": "service:x"})
        self.assertEqual(model["sections"]["gateway"]["state"], presentation.UNKNOWN)
        self.assertEqual(model["env_health"]["verdict"], presentation.UNCONFIRMED)

    def test_the_window_line_actually_reaches_the_card(self):
        """`窗口 N 分钟` was a line that could never appear on any card.

        `_gateway_state` reads `window_minutes` off the section it is handed and
        renders it through the declared template; `gateway_section` builds that
        section and did not put the key in it. So the line was unreachable — the
        worst kind of dead code, because the card it produces is merely shorter
        than intended and nothing anywhere says a line is missing.
        """
        model = presentation.status_model(
            self._bound(state="OK", window_minutes=15,
                        providers=[{"family": "DeepSeek", "state": "RECENT_SUCCESS",
                                    "requests": 412, "ok": 410, "failed": 2}]),
            at="now", binding={"service_ref": "service:x"})
        lines = model["sections"]["gateway"]["lines"]
        self.assertIn("窗口 15 分钟", lines,
                      "the window line did not render — the section dropped "
                      "window_minutes on the way to the card")

    def test_a_route_that_was_asked_about_and_had_no_traffic_still_gets_a_line(self):
        """The row shape `gateway_binding.py` in the ops repository emits for a
        family on the declared watch list with no traffic in the window.

        Pinned here as a literal because the producer is in another repository
        and the two halves are joined only by this shape: the composer hands
        over `requests: 0` and a state word, and this file decides the words.
        Drop the row and the block goes silent — "asked and answered zero"
        becomes indistinguishable from "nobody built this".
        """
        model = presentation.status_model(
            self._bound(state="OK", window_minutes=15, providers=[
                {"family": "DeepSeek", "requests": 0, "ok": 0, "failed": 0,
                 "p95_ms": None, "state": "UNKNOWN", "last_ok": None,
                 "models": [], "measured": False}]),
            at="now", binding={"service_ref": "service:x"})
        lines = model["sections"]["gateway"]["lines"]
        self.assertTrue(any("DeepSeek" in line for line in lines),
                        f"the watched family vanished from the card: {lines}")
        self.assertFalse(any("成功" in line for line in lines),
                         "a family with no traffic was reported with a success "
                         "count")


# ---------------------------------------------------------------------------
# §18 · the measurement, from the register through the row to the face
# ---------------------------------------------------------------------------

class TheNumbersReachTheCardThroughTheDigestRow(unittest.TestCase):
    """The register carried the measurement all along. Two projections dropped
    it, and both are asserted here — fixing one and not the other still leaves
    the card printing prose in the position the contract asks for a number.

    Measured on this node 2026-09-29: `state.json` held 10,844 rows and every
    one carried a `measured` block — `SWAP_PRESSURE` at `used_percent 92.0`
    against `threshold_percent 90.0`, `ROOT_FS_PRESSURE` at 87.6 against 85.0
    — while `serverosctl alert digest --json` returned 9,273 rows and **not one
    of them carried a numeric measurement key**. So `alerting._measurement_line`
    fell through to its own fallback, and `notification-contract.yaml`'s
    `text.detail_shape` — "the measurement, with the threshold it was compared
    against" — was answered with something else.

    This is the inverse of the rule §18 states in the other direction. That
    rule forbids claiming a threshold was crossed without a measurement; this
    was a measurement that the pipeline lost before anybody could claim
    anything with it.
    """

    MEASURED = {
        "used_percent": 92.0, "threshold_percent": 90.0,
        "used_bytes": 15813074944, "total_bytes": 17179865088,
        "memory_pressure": {
            "state": "PRESENT",
            "some": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0},
            "full": {"avg10": 0.0, "avg60": 0.0, "avg300": 0.0}},
        "psi_memory_some_avg60_percent_high": 1.0,
        "psi_memory_full_avg60_percent_high": 0.5,
    }

    AT = "2026-09-29T02:05:00+08:00"

    def _row(self) -> dict:
        """A register record through `alert_digest._row_of`, with the real
        severity table and word list rather than stubs — the projection this
        asserts is the one the digest actually runs."""
        entry = {
            "alert_ref": "alert:stub", "code": "SWAP_PRESSURE",
            "severity": "high", "device_ref": "device:stub",
            "subject_ref": "host:swap", "resource_ref": "host:swap",
            "state": "OPEN", "first_seen": "2026-09-29T01:00:00+08:00",
            "occurrence_count": 3, "detector": "measurement-anomaly",
            "window": "point in time",
            "why": "swap in use is at or above the declared threshold.",
            "measured": dict(self.MEASURED),
            "provenance": {"observed_at": "2026-09-29T02:00:00+08:00",
                           "freshness": "FRESH", "source": "host-memory"},
        }
        return presentation.alert_digest._row_of(
            entry, at=self.AT,
            ranks=presentation.alert_digest.severity_ranks(),
            words=presentation.alerting.severity_words())

    def test_the_row_carries_the_reading_and_the_threshold(self):
        row = self._row()
        self.assertEqual(row["used_percent"], 92.0)
        self.assertEqual(row["threshold_percent"], 90.0)
        self.assertEqual(row["used_bytes"], 15813074944)
        self.assertEqual(row["memory_pressure"]["some"]["avg60"], 0.0)
        self.assertEqual(row["psi_memory_some_avg60_percent_high"], 1.0)

    def test_the_face_prints_the_number_and_not_a_sentence_about_it(self):
        row = self._row()
        block = presentation._for_detail(row, at=self.AT)
        line = presentation.alerting._measurement_line(block, block["measured"])
        self.assertIn("92.0% (threshold 90.0%)", line)
        self.assertIn("some avg60=0.0% (threshold 1.0%)", line)
        self.assertNotIn("swap in use is at or above", line)

    def test_every_field_the_renderer_reads_survives_the_projection(self):
        """`_DETAIL_MEASURED` is *derived* from `MEASUREMENT_FIELDS`, not a
        second copy of the same names. Two lists drift, and the direction they
        drift in is the harmful one: the renderer keeps a branch for a key the
        projection stopped forwarding and the card silently loses a line — the
        exact shape of the defect this class is about.

        Every name is planted on the row rather than read off one code's real
        payload, because the claim is about the two lists and not about
        SWAP_PRESSURE: a code whose `measured` legitimately lacks a key is not
        this test's subject, and asserting over one fixture would let a name
        that no detector emits sit in both lists unnoticed.
        """
        row = self._row()
        for field in presentation.alert_digest.MEASUREMENT_FIELDS:
            row[field] = f"planted:{field}"
        block = presentation._for_detail(row, at=self.AT)
        dropped = [f for f in presentation.alert_digest.MEASUREMENT_FIELDS
                   if block["measured"].get(f) != f"planted:{f}"]
        self.assertEqual(
            dropped, [],
            "the projection dropped a key the renderer still has a branch for")

    def test_a_row_with_no_number_says_so_rather_than_printing_the_why(self):
        """What the fallback actually is on this path, and it is worth pinning.

        `_for_detail` builds the block and carries no `why`, so a digest row
        with no measurement renders "no measurement — this code is a
        declaration" rather than the detector's prose. That makes the loss
        harder to notice than it sounds: what a reader got instead of the
        number was not even the useful half of the fallback.
        """
        row = self._row()
        for field in presentation.alert_digest.MEASUREMENT_FIELDS:
            row[field] = None
        block = presentation._for_detail(row, at=self.AT)
        line = presentation.alerting._measurement_line(block, block["measured"])
        self.assertIn("no measurement", line)


# ---------------------------------------------------------------------------
# §5 · the recovery card
# ---------------------------------------------------------------------------

class RecoveryCardTests(unittest.TestCase):
    """Only what was announced, and the live register is why.

    Of 1124 RECOVERED rows in the register when this was written, 13 were
    announced. A recovery card built from every resolved row would open with a
    wall of ✅ about incidents nobody ever saw.
    """

    ANNOUNCED = {"key": "alert:1", "kind": "RECOVERED", "code": "STALE_CONTAINER",
                 "severity": "medium", "severity_word": "WARNING",
                 "subject_ref": "container:abc", "state": "RESOLVED",
                 "first_seen": "2026-09-19T02:17:52+00:00",
                 "resolved_at": "2026-09-19T02:18:52+00:00",
                 "was_announced": True}
    SILENT = {**ANNOUNCED, "key": "alert:2", "was_announced": False}

    def test_only_announced_recoveries_are_shown(self):
        model = presentation.recovery_model(
            {"rows": [self.ANNOUNCED, self.SILENT]}, at="2026-09-20T00:00:00+00:00")
        self.assertEqual([i["ref"] for i in model["items"]], ["alert:1"])

    def test_the_silent_ones_are_counted_and_not_dropped(self):
        """Dropping them silently would be a card that looks complete."""
        model = presentation.recovery_model(
            {"rows": [self.ANNOUNCED, self.SILENT, self.SILENT]},
            at="2026-09-20T00:00:00+00:00")
        self.assertEqual(model["counts"]["recovered"], 3)
        self.assertEqual(model["counts"]["announced"], 1)
        self.assertEqual(model["counts"]["never_announced"], 2)
        self.assertIn("从未告知", model["omitted"]["note"])

    def test_a_recovery_says_what_makes_it_a_recovery(self):
        """§5: 观测失联或权限丢失不是恢复. `state` is the field that says which."""
        model = presentation.recovery_model(
            {"rows": [self.ANNOUNCED]}, at="2026-09-20T00:00:00+00:00")
        basis = model["items"][0]["basis"]
        self.assertEqual(basis["state"], "RESOLVED")
        self.assertIn("肯定证据", basis["note"])

    def test_an_alert_kind_row_is_not_a_recovery(self):
        model = presentation.recovery_model(
            {"rows": [{**self.ANNOUNCED, "kind": "NEW"}]},
            at="2026-09-20T00:00:00+00:00")
        self.assertEqual(model["items"], [])


# ---------------------------------------------------------------------------
# §15 · the model has no verbs
# ---------------------------------------------------------------------------

class TheModelCannotActTests(unittest.TestCase):
    """`alert != authorization`, asserted on the module rather than trusted.

    §15's rule is that an alert is a finding and not a permission. The way that
    rule breaks is not a decision to break it — it is somebody adding a
    convenience call to the module that builds the card, at which point the
    card surface is a control surface.
    """

    #: Names of functions that stop, start, remove or otherwise change
    #: something. None of them may be reached from this module, and this list
    #: is deliberately one name longer than the code needs — the point is to
    #: catch the next one.
    FORBIDDEN_CALLS = ("stop(", "restart(", "kill(", "remove(", "prune(",
                       "delete(", "reclaim(", "reconcile(", "ack(",
                       "snooze(", "apply(")

    def test_the_module_never_calls_a_mutating_function(self):
        tree = ast.parse((RUNTIME / "presentation.py").read_text(encoding="utf-8"))
        called = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                called.append(ast.unparse(node.func).lower() + "(")
        joined = "\n".join(called)
        for forbidden in self.FORBIDDEN_CALLS:
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, joined)

    def test_the_module_loads_only_the_two_modules_that_describe(self):
        """The only siblings this model reaches for are the two that describe
        what was found.

        A third — `resource_anomaly`, say — would be a detector, and a detector
        on the card path is the wiring §25 has not approved. The set is
        asserted as an equality rather than a subset: a subset would keep
        passing while a new sibling was added, which is the direction this
        check exists to notice.
        """
        allowed = {"serveros_runtime_alerting", "serveros_runtime_alert_digest"}
        # Only the siblings `_load` reaches, which are exactly the modules
        # named `serveros_runtime_*`. The stdlib and `yaml` are not reachable
        # the same way and are not what this check is about.
        loaded = {value.__name__ for value in vars(presentation).values()
                  if isinstance(value, type(sys))
                  and value.__name__.startswith("serveros_runtime_")}
        self.assertEqual(loaded, allowed)

    def test_every_action_carries_the_word_that_makes_it_a_suggestion(self):
        action = presentation.action_for("HIGH_MEMORY_SUSTAINED", "medium")
        self.assertIn("不是授权", action["authority"])
        self.assertTrue(action["word"])

    def test_a_code_with_no_named_action_still_gets_a_sentence(self):
        """An empty action on a critical alert reads as "nothing to do"."""
        action = presentation.action_for("MEMORY_PRESSURE", "high")
        self.assertTrue(action["text"])
        self.assertFalse(action["named_by_catalogue"])


if __name__ == "__main__":
    unittest.main()
