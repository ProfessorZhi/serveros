#!/usr/bin/env python3
"""Tests for the Remote Console — §L §M §N §O §P §AA §AB.

The console is the join of four planes and owns none of them, so most of what
there is to assert is about NOT doing things: that a resource which supports
close does not get reported as supporting interrupt, that selecting a session
cannot move a thread's Item, that L0 carries no session row, that an operation
the provider lacks is shown rather than dropped, and that nothing here claims
the person on the other end was verified.

Three of the assertions below are about the GATES rather than the code — the
`never_satisfied_by` check, the level ceiling, and the level-0 prohibition —
because a guard nobody can make fire is the same defect as a feature nobody
wired, and this repository has paid for that shape often enough to have a name
for it.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


console = _load("serveros_runtime_human_console", REPO / "runtime" / "human_console.py")
store_mod = _load("serveros_runtime_agent_control_store",
                  REPO / "runtime" / "agent_control_store.py")

NOW = "2026-09-15T00:00:00Z"


def row(ref: str = "session:dev:botmux:s1", *, runtime: str = "claude-code",
        device: str = "device:tokyo-shared-dev-01", status: str = "RUNNING",
        support: dict | None = None, **extra) -> dict:
    """A provider row, shaped the way `mcp/botmux_provider.py#list_sessions`
    really shapes one. Copied from that method rather than invented, because a
    fixture with a shape of its own would make the console pass here and fail
    against the provider."""
    item = {"session_ref": ref, "device_ref": device, "provider": "provider:botmux",
            "provider_session_id": "s1", "provider_agent_id": "a1",
            "agent_ref": "agent:claude-code", "agent_runtime": runtime,
            "native_runtime": runtime, "status": status,
            "status_provenance": "provider-reported", "provider_status": status,
            "status_reason": None, "provider_evidence": ["api/sessions"],
            "can_accept_input": True,
            "action_support": support if support is not None else {},
            "transport": "botmux-dashboard-api"}
    item.update(extra)
    return item


# ---------------------------------------------------------------------------
# The vocabulary (§M)
# ---------------------------------------------------------------------------

class TheVocabularyIsClientsFree(unittest.TestCase):
    def test_every_operation_names_an_action_the_contract_declares(self):
        """§M's list, checked against the closed verb list rather than trusted.

        The console maps a person's word onto the session contract's word, and
        a mapping onto a verb that contract does not have would be a console
        offering an operation nobody implements.
        """
        import yaml
        contract = yaml.safe_load(
            (REPO / "runtime" / "agent-session-contract.yaml").read_text())
        declared = set(contract["support"]["actions"])
        for op in console.OPERATIONS:
            with self.subTest(operation=op.name):
                if op.local:
                    self.assertIsNone(op.action)
                else:
                    self.assertIn(op.action, declared)

    def test_no_operation_carries_a_client_word(self):
        """§P. The unified thing is the operation, not the UI.

        A vocabulary that had grown `session.fork`, `session.retry` or
        `session.plan-mode` would be one client's buttons wearing ServerOS
        names. The check is a closed list rather than a deny-list, so a new
        operation is a decision somebody makes here.
        """
        self.assertEqual(console.OPERATION_NAMES, (
            "session.list", "session.inspect", "session.result", "session.select",
            "session.create", "session.send", "session.resume", "session.close",
            "session.interrupt", "session.handoff"))

    def test_the_two_spellings_of_one_operation_are_one_operation(self):
        """§M says `resume/continue` and `close` where the contract says
        `resume` and `stop`. An alias rather than a second entry — two entries
        would acquire two support states and could disagree."""
        self.assertIs(console.resolve("session.continue"), console.resolve("session.resume"))
        self.assertIs(console.resolve("session.stop"), console.resolve("session.close"))
        self.assertNotIn("session.continue", console.OPERATION_NAMES)
        self.assertNotIn("session.stop", console.OPERATION_NAMES)

    def test_an_operation_this_console_does_not_have_is_refused_by_name(self):
        with self.assertRaises(console.ConsoleRefused) as caught:
            console.resolve("session.kill")
        self.assertEqual(caught.exception.reason, "ACTION_UNSUPPORTED")
        self.assertIn("session.select", caught.exception.detail)

    def test_the_levels_are_the_existing_four_and_not_a_fifth_scale(self):
        """§12 forbids a fourth coordinate system. The console is another
        SUBJECT of L0–L3, so it must name the same four and no others."""
        self.assertEqual(console.LEVELS, ("L0", "L1", "L2", "L3"))
        for op in console.OPERATIONS:
            self.assertIn(op.level, console.LEVELS)

    def test_a_level_is_cumulative(self):
        for index, level in enumerate(console.LEVELS):
            with self.subTest(level=level):
                offered = {op.name for op in console.operations_at(level)}
                lower = {op.name for op in console.operations_at(console.LEVELS[index - 1])
                         } if index else set()
                self.assertLessEqual(lower, offered)
        self.assertEqual(len(console.operations_at("L3")), len(console.OPERATIONS))
        self.assertNotIn("session.create", {o.name for o in console.operations_at("L1")})

    def test_an_unknown_level_is_refused_rather_than_defaulted(self):
        with self.assertRaises(console.ConsoleRefused):
            console.operations_at("L4")
        with self.assertRaises(console.ConsoleRefused):
            console.levels("L9", [])


# ---------------------------------------------------------------------------
# Support, and the rule §M states twice
# ---------------------------------------------------------------------------

class CloseIsNeverReportedAsInterrupt(unittest.TestCase):
    """§M: *"不要把 close 冒充 interrupt"*, and the console must say
    `interrupt = UNSUPPORTED` when the transport has close and not interrupt."""

    # Exactly the shape BotMux v3.18 presents: a session that can be stopped
    # and has no turn-scoped verb at all.
    SUPPORT = {
        "session.stop": {"state": "SUPPORTED", "reason": "close supported"},
        "session.interrupt": {"state": "UNSUPPORTED",
                              "reason": "no turn-scoped verb exists"},
    }

    def test_close_and_interrupt_are_read_from_their_own_actions(self):
        states = console.operation_states(self.SUPPORT)
        self.assertEqual(states["session.close"]["state"], console.AVAILABLE)
        self.assertEqual(states["session.interrupt"]["state"], console.UNSUPPORTED)
        self.assertIn("no turn-scoped verb", states["session.interrupt"]["because"])

    def test_the_forbidden_reading_is_recorded_on_the_operation(self):
        close = console.resolve("session.close")
        self.assertEqual(close.never_satisfied_by, ("session.interrupt",))
        self.assertNotIn("session.interrupt",
                         console.resolve("session.interrupt").never_satisfied_by)

    def test_a_state_map_produced_from_another_operations_answer_is_refused(self):
        """The guard, made to fire. Without this the `never_satisfied_by` tuple
        is a comment: the loop above keeps the two apart by construction, and
        nothing else in the module would notice if it stopped."""
        states = console.operation_states(self.SUPPORT)
        # The exact "simplification": price interrupt on the session's ability
        # to be closed, which is the mistake §M names.
        states["session.interrupt"] = dict(states["session.interrupt"],
                                           action="session.stop",
                                           state=console.AVAILABLE)
        with self.assertRaises(console.ConsoleRefused) as caught:
            console._assert_no_borrowed_support(states)
        self.assertIn("session.interrupt", str(caught.exception))

    def test_the_relation_must_be_between_two_different_verbs(self):
        """If a future edit collapsed the two onto one provider action, the
        check above would pass on both and the forbidding would be decorative."""
        original = console.OPERATIONS
        broken = tuple(
            console.Operation(name=op.name, action="session.stop", level=op.level,
                              aliases=op.aliases,
                              never_satisfied_by=op.never_satisfied_by,
                              local=op.local, destructive=op.destructive,
                              requires_feature=op.requires_feature)
            if op.name == "session.interrupt" else op for op in original)
        console.OPERATIONS = broken
        try:
            with self.assertRaises(console.ConsoleRefused) as caught:
                console._assert_no_borrowed_support({
                    op.name: {"state": console.UNKNOWN, "action": op.action}
                    for op in broken})
            self.assertIn("priced on provider action", str(caught.exception))
        finally:
            console.OPERATIONS = original

    def test_a_local_operation_may_not_carry_a_providers_answer(self):
        """`session.select` is ServerOS's own decision. An action on its entry
        would be a claim that a provider was consulted."""
        with self.assertRaises(console.ConsoleRefused):
            console._assert_no_borrowed_support({
                "session.select": {"state": "AVAILABLE", "action": "session.send"}})

    def test_an_absent_action_is_unknown_and_not_unsupported(self):
        """The distinction §4 and §O both rest on: nobody asked is not the same
        as asked and declined, and they send an operator to different places."""
        states = console.operation_states({})
        self.assertEqual(states["session.send"]["state"], console.UNKNOWN)
        self.assertIn("not in this session's action_support map",
                      states["session.send"]["because"])
        self.assertEqual(states["session.select"]["state"], console.LOCAL)

    def test_select_is_local_and_not_available(self):
        """A caller reading AVAILABLE would conclude a provider was asked and
        agreed. For `session.select` no provider is asked at all."""
        self.assertEqual(console.operation_states({})["session.select"]["state"],
                         console.LOCAL)
        self.assertNotEqual(console.LOCAL, console.AVAILABLE)


class TheFeatureVocabularyNarrowsAndNeverWidens(unittest.TestCase):
    """§P: runtime-specific behaviour is decided by the feature vocabulary."""

    def test_a_missing_feature_withdraws_the_operation_it_needs(self):
        states = console.operation_states(
            {"session.resume": {"state": "SUPPORTED", "reason": "provider has it"}},
            features={"session.lifecycle": "UNAVAILABLE"})
        self.assertEqual(states["session.resume"]["state"], console.UNSUPPORTED)
        self.assertIn("session.lifecycle", states["session.resume"]["because"])

    def test_the_providers_own_answer_is_kept_beside_the_feature_verdict(self):
        """The withdrawal has a reason of its own, and the provider's answer is
        not deleted by it — a reader who sees UNSUPPORTED has to be able to
        find out whether the provider has the verb."""
        states = console.operation_states(
            {"session.resume": {"state": "SUPPORTED", "reason": "provider has it"}},
            features={"session.lifecycle": "INCOMPATIBLE"})
        self.assertEqual(states["session.resume"]["provider_state"], "SUPPORTED")
        self.assertIn("provider has it", states["session.resume"]["because"])

    def test_an_unknown_feature_narrows_nothing(self):
        """A feature nobody established may not withdraw an operation the
        provider supports. UNKNOWN means go and find out, not no."""
        states = console.operation_states(
            {"session.resume": {"state": "SUPPORTED", "reason": "provider has it"}},
            features={"session.lifecycle": "UNKNOWN"})
        self.assertEqual(states["session.resume"]["state"], console.AVAILABLE)

    def test_the_gate_cannot_widen_an_operation_the_provider_lacks(self):
        states = console.operation_states(
            {"session.resume": {"state": "UNSUPPORTED", "reason": "provider lacks it"}},
            features={"session.lifecycle": "AVAILABLE"})
        self.assertEqual(states["session.resume"]["state"], console.UNSUPPORTED)

    def test_the_feature_names_come_from_the_contract(self):
        """§P's vocabulary, not a list written in the console. A name that the
        runtime contract does not declare is a gate that can never fire."""
        import yaml
        contract = yaml.safe_load(
            (REPO / "abilities" / "agent-runtime-contract.yaml").read_text())
        declared = set((contract.get("runtime_features") or {}).get("order") or ())
        self.assertTrue(declared, "the runtime contract declares no features")
        for op in console.OPERATIONS:
            if op.requires_feature:
                with self.subTest(operation=op.name):
                    self.assertIn(op.requires_feature, declared)


# ---------------------------------------------------------------------------
# The levels (§N), and §AH's prohibition on L0
# ---------------------------------------------------------------------------

class TheLevelsSayWhatTheyMayAndNoMore(unittest.TestCase):
    ROWS = [
        row("session:dev:botmux:s1", runtime="claude-code"),
        row("session:dev:botmux:s2", runtime="codex"),
        row("session:dev:botmux:s3", runtime="codex",
            device="device:work-fj"),
    ]
    BINDINGS = {
        "session:dev:botmux:s1": {"session_ref": "session:dev:botmux:s1",
                                  "session_role": "execution",
                                  "bound_item_ref": "LIT-0101", "state": "OPEN"},
        "session:dev:botmux:s2": {"session_ref": "session:dev:botmux:s2",
                                  "session_role": "coordinator", "state": "OPEN"},
    }

    def test_l0_carries_counts_and_no_session_row(self):
        """§AH: L0 must load no full session history. The cheapest way to be
        sure is to return no session at all — so this asserts the ABSENCE,
        which is the property, rather than the presence of counts."""
        payload = console.level_l0(self.ROWS)
        self.assertEqual(payload["sessions_total"], 3)
        self.assertNotIn("sessions", payload)
        encoded = json.dumps(payload)
        for ref in ("session:dev:botmux:s1", "session:dev:botmux:s2"):
            self.assertNotIn(ref, encoded)
        by_device = {entry["device_ref"]: entry for entry in payload["by_device"]}
        self.assertEqual(by_device["device:tokyo-shared-dev-01"]["total"], 2)
        self.assertEqual(by_device["device:tokyo-shared-dev-01"]["by_runtime"],
                         {"claude-code": 1, "codex": 1})

    def test_l1_is_one_card_per_session_with_the_six_facts(self):
        """§N's list, asserted as an exact key set: a card that quietly grew a
        transcript field would be L0's prohibition arriving one level down.

        Exact, not a subset — `CARD_FIELDS` is the decision about what a card
        is, and this is what makes it one. It caught a real disagreement while
        this file was being written: the constant listed six facts and the
        projection emitted a seventh (`title`) that nothing had argued for.
        """
        payload = console.level_l1(self.ROWS, bindings=self.BINDINGS)
        self.assertEqual(payload["count"], 3)
        card = payload["sessions"][0]
        self.assertEqual(set(card), set(console.CARD_FIELDS))
        self.assertEqual(card["bound_item"]["item_ref"], "LIT-0101")
        self.assertEqual(card["bound_item"]["session_role"], "execution")

    def test_the_card_carries_a_name_and_not_a_transcript(self):
        """The one field past §N's enumeration, held to being a label: it is
        copied from the provider row and there is nowhere on the card for
        anything the session SAID."""
        session = row("session:dev:botmux:s9", title="fix the flaky probe")
        card = console.level_l1([session])["sessions"][0]
        self.assertEqual(card["title"], "fix the flaky probe")
        # A row whose provider ignored the field yields absence, not a
        # substitute read — the console never goes and fetches content to fill
        # a name it was not given.
        self.assertIsNone(console.level_l1([row()])["sessions"][0]["title"])

    def test_the_bound_item_comes_from_the_binding_and_not_the_provider(self):
        """No provider knows what a session is for. The join is the card."""
        for candidate in self.ROWS:
            self.assertNotIn("bound_item", candidate)
            self.assertNotIn("bound_item_ref", candidate)
        card = console.level_l1(self.ROWS, bindings=self.BINDINGS)["sessions"][0]
        self.assertEqual(card["bound_item"]["item_ref"], "LIT-0101")

    def test_a_coordinator_says_it_is_bound_to_none_rather_than_nothing(self):
        """An absent Item and an Item nobody looked up are different facts."""
        card = console.level_l1(self.ROWS, bindings=self.BINDINGS)["sessions"][1]
        self.assertIsNone(card["bound_item"]["item_ref"])
        self.assertEqual(card["bound_item"]["session_role"], "coordinator")
        self.assertIn("because", card["bound_item"])
        self.assertTrue(card["bound_item"]["because"],
                        "the reason is present and non-empty; a card that said "
                        "`because: ''` would carry the key and none of the fact")

    def test_a_session_with_no_binding_has_no_bound_item_and_not_a_guess(self):
        card = console.level_l1(self.ROWS, bindings=self.BINDINGS)["sessions"][2]
        self.assertIsNone(card["bound_item"])

    def test_l2_returns_unsupported_operations_marked_rather_than_omitted(self):
        """§M: the console must SHOW `interrupt = UNSUPPORTED`. Dropping the
        entry would leave a person unable to tell 'cannot be done' from 'nobody
        thought of it'."""
        payload = console.level_l2(
            self.ROWS, bindings=self.BINDINGS,
            action_support=CloseIsNeverReportedAsInterrupt.SUPPORT,
            selected={"state": "SELECTED", "session_ref": "session:dev:botmux:s1"})
        by_name = {entry["name"]: entry for entry in payload["operations"]}
        self.assertIn("session.interrupt", by_name)
        self.assertEqual(by_name["session.interrupt"]["state"], "UNSUPPORTED")
        self.assertEqual(by_name["session.close"]["state"], "AVAILABLE")
        self.assertTrue(by_name["session.close"]["destructive"])
        self.assertEqual(by_name["session.select"]["state"], "LOCAL")

    def test_l3_carries_the_providers_own_fields(self):
        payload = console.level_l3(self.ROWS, bindings=self.BINDINGS,
                                   action_support={})
        diagnostic = payload["diagnostics"][0]
        self.assertEqual(diagnostic["provider"], "provider:botmux")
        self.assertEqual(diagnostic["transport"], "botmux-dashboard-api")
        self.assertEqual(diagnostic["provider_session_id"], "s1")
        self.assertIn("session_ref", diagnostic)

    def test_l3_says_so_when_the_feature_map_was_never_read(self):
        payload = console.level_l3(self.ROWS, bindings=self.BINDINGS)
        self.assertEqual(payload["features"]["state"], "UNKNOWN")
        self.assertIn("no runtime binding was read", payload["features"]["reason"])

    def test_the_dispatcher_refuses_a_level_it_does_not_serve(self):
        with self.assertRaises(console.ConsoleRefused):
            console.levels("L4", self.ROWS)


# ---------------------------------------------------------------------------
# Selection (§M) — and the two things it may never be
# ---------------------------------------------------------------------------

class SelectingIsNotRebinding(unittest.TestCase):
    def test_a_selection_record_cannot_carry_a_threads_fields(self):
        """§M: selecting is NOT changing identity and NOT moving the execution
        thread to another Item. Held by shape: the two field sets are disjoint,
        so a selection has no field in which a `bound_item_ref` could travel."""
        for field in console.THREAD_BINDING_FIELDS:
            self.assertNotIn(field, console.SELECTION_FIELDS)

    def test_the_store_agrees_about_which_fields_those_are(self):
        """The store spells the two names itself — it is the lower layer and
        importing the console upward would invert that. This is the join that
        keeps the copy from drifting."""
        self.assertEqual(tuple(store_mod.BINDING_ONLY_FIELDS),
                         tuple(console.THREAD_BINDING_FIELDS))

    def test_a_selection_without_a_conversation_is_refused(self):
        with self.assertRaises(console.ConsoleRefused):
            console.selection_record(conversation_ref="", session_ref="s", at=NOW)

    def test_selecting_nothing_is_refused(self):
        with self.assertRaises(console.ConsoleRefused):
            console.selection_record(conversation_ref="c", session_ref="", at=NOW)

    def test_an_unverified_person_may_still_select(self):
        """§AA: a non-destructive console runs on the existing authenticated
        transport. `human.identity.verify` is UNKNOWN, so there is no actor and
        none is invented — but the record still says which absence it is, so
        that the day a verified identity exists the two are distinguishable.
        """
        record = console.selection_record(conversation_ref="c", session_ref="s",
                                          at=NOW)
        self.assertEqual(record["identity"], "UNVERIFIED")
        self.assertEqual(console.selection_record(
            conversation_ref="c", session_ref="s", at=NOW,
            identity="VERIFIED")["identity"], "VERIFIED")

    def test_no_operation_a_selection_permits_is_destructive(self):
        """§AA's line, computed rather than restated: the console can say which
        of its operations may not run on an unverified identity, and selection
        is not one of them."""
        permitted, refused = console.non_destructive(
            ["session.select", "session.send", "session.close", "session.interrupt"])
        self.assertEqual(permitted, ["session.select", "session.send"])
        self.assertEqual(refused, ["session.close", "session.interrupt"])

    def test_a_selection_that_was_read_back_is_still_checked(self):
        """Durability is not authority — the store's own rule, in its new shape.

        The record is well-formed and it came off disk; that is not what makes
        it current. What it names has to still be in the list the provider just
        gave, and the three states are distinct answers rather than a present
        and an absent one.
        """
        record = console.selection_record(conversation_ref="c", session_ref="s",
                                          at=NOW)
        self.assertEqual(console.selection_axis(None)["state"], "NONE")
        self.assertEqual(console.selection_axis(record)["state"], "SELECTED")
        self.assertEqual(
            console.selection_axis(record, live_refs=["other"])["state"], "STALE")

    def test_a_stale_selection_is_kept_and_explained(self):
        """Clearing it would leave the person finding nothing selected rather
        than being told what happened to what they chose."""
        record = console.selection_record(conversation_ref="c", session_ref="s",
                                          at=NOW)
        stale = console.selection_axis(record, live_refs=[])
        self.assertEqual(stale["session_ref"], "s")
        self.assertEqual(stale["selected_at"], NOW)
        self.assertTrue(stale["because"])

    def test_no_live_list_means_nobody_checked_rather_than_it_vanished(self):
        """`live_refs=None` is 'this call did not read the provider', and the
        answer must not be STALE — a console that reported a stale selection
        whenever it skipped a read would train its reader to ignore the word."""
        record = console.selection_record(conversation_ref="c", session_ref="s",
                                          at=NOW)
        self.assertEqual(console.selection_axis(record)["state"], "SELECTED")

    def test_an_identity_word_outside_the_vocabulary_is_refused(self):
        """§AA. A reader compares this field; free text would make that
        impossible, and `VERIFIED` is a claim the console may only carry if a
        transport made it."""
        with self.assertRaises(console.ConsoleRefused):
            console.selection_record(conversation_ref="c", session_ref="s",
                                     at=NOW, identity="probably ok")


# ---------------------------------------------------------------------------
# The store's half
# ---------------------------------------------------------------------------

class TheSelectionStoreMovesAttentionAndNotThreads(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.path = Path(self.dir.name) / "control.json"
        self.store = store_mod.AgentControlStore(self.path)

    def tearDown(self):
        self.dir.cleanup()

    def record(self, session="s1", conversation="c1"):
        return console.selection_record(conversation_ref=conversation,
                                        session_ref=session, at=NOW)

    def test_a_second_selection_supersedes_the_first(self):
        """§M's operation is moving a selection, so this one is NOT write-once
        — and that asymmetry with `put_binding` is the whole reason both can
        live in one file."""
        self.store.put_selection(self.record("s1"))
        self.store.put_selection(self.record("s2"))
        self.assertEqual(self.store.get_selection("c1")["session_ref"], "s2")

    def test_a_binding_is_still_write_once(self):
        """The other half of the asymmetry, asserted in the same file so that a
        future edit making selections write-once the way bindings are — or
        bindings movable the way selections are — cannot look consistent."""
        self.store.put_binding({"session_ref": "s1", "session_role": "execution",
                                "bound_item_ref": "LIT-0101"})
        with self.assertRaises(store_mod.StoreError):
            self.store.put_binding({"session_ref": "s1", "session_role": "execution",
                                    "bound_item_ref": "LIT-0205"})

    def test_the_store_refuses_a_selection_carrying_an_item(self):
        """The console's builder never produces one; the store refuses one
        anyway, so the caller who assembles a record by hand is stopped too."""
        with self.assertRaises(store_mod.StoreError) as caught:
            self.store.put_selection({"conversation_ref": "c1", "session_ref": "s1",
                                      "bound_item_ref": "LIT-0101"})
        self.assertIn("bound_item_ref", str(caught.exception))

    def test_the_store_refuses_a_selection_carrying_a_role(self):
        with self.assertRaises(store_mod.StoreError):
            self.store.put_selection({"conversation_ref": "c1", "session_ref": "s1",
                                      "session_role": "execution"})

    def test_a_selection_survives_a_restart(self):
        self.store.put_selection(self.record("s1"))
        reopened = store_mod.AgentControlStore(self.path)
        self.assertEqual(reopened.get_selection("c1")["session_ref"], "s1")

    def test_a_selection_is_never_swept(self):
        """A pointer is not a window. Sweeping one would make a conversation
        that once chose something indistinguishable from one that never did."""
        self.store.put_selection(self.record("s1"))
        self.store.sweep(before="2030-01-01T00:00:00Z")
        self.assertIsNotNone(self.store.get_selection("c1"))

    def test_the_summary_counts_selections_and_never_returns_them(self):
        """`s1` is the session's own name and it must not come back out.

        Asserted over the summary minus `path`. The full document includes the
        store's absolute path, and that path is a `tempfile.mkdtemp()` name — on
        2026-09-22 a run whose scratch directory happened to be `tmph0s1lsv7`
        failed this assertion with the selection nowhere in the payload. The
        check is about what the summary returns, and a random directory name is
        not something it returns.
        """
        self.store.put_selection(self.record("s1"))
        summary = self.store.summary()
        self.assertEqual(summary["selections"], 1)
        returned = {k: v for k, v in summary.items() if k != "path"}
        self.assertNotIn("s1", json.dumps(returned))

    def test_a_selection_without_a_conversation_is_refused_by_the_store(self):
        with self.assertRaises(store_mod.StoreError):
            self.store.put_selection({"session_ref": "s1"})


# ---------------------------------------------------------------------------
# The surface a person actually reaches (§L)
# ---------------------------------------------------------------------------

class TheConsoleIsReachable(unittest.TestCase):
    def test_the_agent_surface_declares_the_console_tool(self):
        import yaml
        contract = yaml.safe_load(
            (REPO / "fleet" / "mcp-contract.yaml").read_text())
        tools = contract["surfaces"]["serveros-agent-mcp"]["tools"]
        self.assertIn("agent.console", tools)

    def test_the_console_is_not_a_mutation_and_takes_no_context(self):
        """It reads a device and a level, and — unlike every control action —
        needs no `context_ref`, because it changes nothing. A console that
        required an actor would be claiming to know who was looking."""
        import test_agent_mcp as agent_tests
        spec = agent_tests.schema("agent.console")
        self.assertNotIn("context_ref", spec["properties"])
        self.assertEqual(spec["required"], ["device"])

    def test_the_daemon_serves_the_selection_and_never_a_providers_name(self):
        """§L: ServerOS core does not reach for a provider. The daemon's own
        source is asserted free of the transport's name by another file; this
        asserts the two methods that carry §M exist on its socket list."""
        source = (REPO / "runtime" / "daemon.py").read_text()
        self.assertIn('"agent.selection.record"', source)
        self.assertIn('"agent.selection.resolve"', source)

    def test_the_cli_offers_every_operation_and_names_the_forbidden_substitution(self):
        """§M readable by a person: `session.close` cannot report as
        `session.interrupt`, and the command that prints the vocabulary says so."""
        import subprocess
        result = subprocess.run(
            [sys.executable, str(REPO / "runtime" / "cli.py"),
             "console", "operations", "--level", "L2", "--json"],
            capture_output=True, text=True, cwd=str(REPO))
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        by_name = {op["name"]: op for op in payload["operations"]}
        self.assertEqual(by_name["session.close"]["never_satisfied_by"],
                         ["session.interrupt"])
        self.assertTrue(by_name["session.close"]["destructive"])
        self.assertFalse(by_name["session.select"]["destructive"])
        self.assertEqual(by_name["session.select"]["action"], None)

    def test_the_console_module_reaches_nothing(self):
        """The console is a join of planes it does not own, and that is held by
        what it can import. No subprocess, no socket, no provider, no store —
        it is handed rows and answers about levels."""
        ALLOWED = {"__future__", "dataclasses", "typing"}
        imported = set()
        for node in ast.walk(ast.parse((REPO / "runtime" / "human_console.py").read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertFalse(imported - ALLOWED, sorted(imported - ALLOWED))


# ---------------------------------------------------------------------------
# End to end, through the real router and the real view (§L)
# ---------------------------------------------------------------------------

CONVERSATION = "conversation:feishu:app1:dm:abc"


def _harness(*, bindings=True, selected=True, interrupts=True):
    """One `agent.console` call over the deployed shape.

    The router, the dispatcher and the view are the real ones — the fixture
    surface in `test_agent_mcp.py` is subclassed rather than rebuilt, so these
    two suites cannot come to different conclusions about what is deployed, and
    an edit to the deployed router moves both. Only the provider and the daemon
    are fixtures, which is the same line the agent suite draws.

    The refs are DERIVED from that file's constants instead of written out. A
    literal `"session:local:..."` would pass here and be refused by the real
    resolver, which is exactly what happened the first time this was written.
    """
    import test_agent_mcp as agent_tests

    device = agent_tests.LOCAL
    live = agent_tests.semantics.session_ref(device, agent_tests.PROVIDER, "s1")
    chosen = live if selected else agent_tests.semantics.session_ref(
        device, agent_tests.PROVIDER, "closed")

    support = {"session.send": {"state": "SUPPORTED"},
               "session.result": {"state": "SUPPORTED"},
               "session.stop": {"state": "SUPPORTED", "reason": "close supported"}}
    support["session.interrupt"] = (
        {"state": "SUPPORTED", "reason": "turn-scoped cancel exists"} if interrupts
        else {"state": "UNSUPPORTED",
              "reason": "the transport has no turn-scoped verb"})

    class Provider(agent_tests.FakeProvider):
        def list_sessions(self, *, device_ref):
            rows = super().list_sessions(device_ref=device_ref)
            for row in rows:
                row["action_support"] = dict(support)
                row["agent_runtime"] = "claude-code"
                row["title"] = "fix the flaky probe"
            return rows

    class Daemon(agent_tests.FakeDaemon):
        def call(self, method, params=None):
            # Recorded here too: the parent records only what it handled
            # itself, and a test that asserts on the wire would otherwise see
            # an empty log and pass for the wrong reason.
            self.calls.append((method, dict(params or {})))
            if method == "agent.binding.list":
                return {"records": {
                    live: {"session_ref": live, "session_role": "execution",
                           "bound_item_ref": "LIT-0101", "state": "OPEN"}}}
            if method == "agent.selection.resolve":
                if str((params or {}).get("conversation_ref")) != CONVERSATION:
                    return {"axis": {"state": "NONE", "because": "nothing selected"}}
                seen = (params or {}).get("live_session_refs")
                if seen is not None and chosen not in seen:
                    return {"axis": {"state": "STALE", "session_ref": chosen,
                                     "because": "what this conversation chose is "
                                                "not in the list just read"}}
                return {"axis": {"state": "SELECTED", "session_ref": chosen}}
            return super().call(method, params)

    daemon = Daemon()
    client, _service, _built = agent_tests.build(daemon=daemon, provider=Provider())
    if bindings:
        client._bindings = agent_tests.agent_mcp.DaemonBindings(daemon)
    return client, daemon, device, live


class TheConsoleAnswersEndToEnd(unittest.TestCase):
    def console(self, client, device, **arguments):
        error, answer = agent_call(client, "agent.console", device=device,
                                   **arguments)
        self.assertFalse(error, answer)
        return answer

    def test_l0_listing_shows_counts_and_no_session(self):
        client, _daemon, device, live = _harness()
        answer = self.console(client, device, level="L0")
        self.assertEqual(answer["sessions_total"], 1)
        self.assertNotIn(live, json.dumps(answer))

    def test_l1_joins_the_threads_item_onto_the_providers_row(self):
        """Neither plane can produce this card: the provider knows the session
        and not what it is for, the binding store the reverse."""
        client, _daemon, device, _live = _harness()
        card = self.console(client, device, level="L1")["sessions"][0]
        self.assertEqual(card["bound_item"]["item_ref"], "LIT-0101")
        self.assertEqual(card["bound_item"]["session_role"], "execution")
        self.assertEqual(card["runtime"], "claude-code")
        self.assertEqual(card["title"], "fix the flaky probe")

    def test_a_binding_store_that_did_not_answer_says_so(self):
        """The client with no daemon handle is the deployed shape of "the store
        is unreachable", and it must not read as "no thread has a purpose" —
        that reading would show every session as a coordinator."""
        client, _daemon, device, _live = _harness(bindings=False)
        answer = self.console(client, device, level="L1")
        self.assertEqual(answer["binding_state"], "UNKNOWN")
        self.assertIn("did not answer", answer["binding_because"])
        self.assertIsNone(answer["sessions"][0]["bound_item"])

    def test_l2_prices_the_operations_on_the_session_the_person_chose(self):
        """§M end to end. Here the transport has BOTH verbs, so this asserts
        the console reads the provider's per-session map at all — the half the
        unit tests above cannot show, since they hand it a map directly."""
        client, _daemon, device, _live = _harness(interrupts=True)
        answer = self.console(client, device, level="L2",
                              conversation_ref=CONVERSATION)
        by_name = {op["name"]: op for op in answer["operations"]}
        self.assertEqual(by_name["session.close"]["state"], "AVAILABLE")
        self.assertEqual(by_name["session.interrupt"]["state"], "AVAILABLE")
        self.assertEqual(by_name["session.select"]["state"], "LOCAL")
        self.assertEqual(answer["conversation_ref"], CONVERSATION)

    def test_l2_shows_interrupt_unsupported_when_the_transport_lacks_it(self):
        """§M's other half, on the same path: the entry is still THERE and it
        says UNSUPPORTED, so a person can tell "cannot be done" from "nobody
        built it"."""
        client, _daemon, device, _live = _harness(interrupts=False)
        answer = self.console(client, device, level="L2",
                              conversation_ref=CONVERSATION)
        by_name = {op["name"]: op for op in answer["operations"]}
        self.assertEqual(by_name["session.interrupt"]["state"], "UNSUPPORTED")
        self.assertIn("no turn-scoped verb", by_name["session.interrupt"]["because"])
        self.assertEqual(by_name["session.close"]["state"], "AVAILABLE")

    def test_with_nothing_selected_no_operation_is_priced_on_a_provider(self):
        """`action_support` is per-session, so with no session chosen there is
        no honest device-wide answer. Everything is UNKNOWN — not by default,
        but because nobody was asked."""
        client, _daemon, device, _live = _harness()
        answer = self.console(client, device, level="L2")
        by_name = {op["name"]: op for op in answer["operations"]}
        self.assertEqual(by_name["session.close"]["state"], "UNKNOWN")
        self.assertEqual(by_name["session.select"]["state"], "LOCAL")
        self.assertNotIn("conversation_ref", answer)

    def test_a_selection_whose_session_is_gone_is_reported_stale(self):
        client, _daemon, device, _live = _harness(selected=False)
        answer = self.console(client, device, level="L2",
                              conversation_ref=CONVERSATION)
        self.assertEqual(answer["selected"]["state"], "STALE")
        # Every operation L2 offers, and no operation is priced on a provider:
        # the selection points at a session this call cannot see, so nobody was
        # asked about its verbs.
        self.assertEqual(
            {op["name"]: op["state"] for op in answer["operations"]},
            {op.name: ("LOCAL" if op.local else "UNKNOWN")
             for op in console.operations_at("L2")})

    def test_the_live_list_is_passed_in_rather_than_fetched_by_the_store(self):
        """The daemon holds no provider, so the comparison against what is
        alive can only be made by the caller. Asserted on the wire rather than
        on the answer, because an answer of STALE proves nothing about which
        side computed it."""
        client, daemon, device, _live = _harness(selected=False)
        self.console(client, device, level="L2", conversation_ref=CONVERSATION)
        sent = [params for method, params in daemon.calls
                if method == "agent.selection.resolve"]
        self.assertTrue(sent, "no selection read reached the daemon")
        self.assertIn("live_session_refs", sent[0])
        self.assertTrue(sent[0]["live_session_refs"],
                        "the live list was passed in empty; a console that "
                        "cannot say what is alive would call every selection "
                        "stale")


def agent_call(client, name, **arguments):
    import test_agent_mcp as agent_tests
    return agent_tests.payload(agent_tests.call(client, name, **arguments))


if __name__ == "__main__":
    unittest.main()
