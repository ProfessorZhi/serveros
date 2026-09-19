#!/usr/bin/env python3
"""Registration candidacy: the question between "well-formed" and "still running".

`test_registration_admission.py` covers what `registry.validate` refuses at the
moment of registration. This file covers the pipeline that decides whether a
thing should reach that moment at all, and the four negative cases the round is
fixed on:

  unauthorized   something was noticed and nobody said it was theirs
  duplicate      the same candidate, asked about again on the next launch
  lifecycle      two lifetimes that look like one answer and are not
  source lost    a source stopped reporting and nothing ended

Each has a counterpart that must NOT fire. A pipeline that refuses everything
is as broken as one that refuses nothing, and only the first is obvious — so
every refusal below is paired with the case it must let through.

The last class is the one worth reading if you only read one: it asserts that
the session-owns-a-durable-resource rule is NOT implemented twice. That rule
already lives in the contract's `ownership` block and runs inside
`registry.validate`; a second copy in `registration.py` would agree with it
today and drift later, which is precisely the defect `test_composition_authority`
was written to catch.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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


registration = _load("serveros_runtime_registration", "registration.py")
registry = _load("serveros_runtime_registry", "registry.py")
graph_module = registration.graph

AT = "2026-09-20T10:00:00Z"
ACTOR = "user:vince"
SESSION_OWNER = {"type": "serveros", "ref": "session:abc123"}
PROJECT_OWNER = {"type": "project", "ref": "project:multi-user-claw"}


def a_launch_record(kind="mcp-server", owner=PROJECT_OWNER, key="launch:proj:bridge", **detail):
    return registration.from_launch_record(
        device_ref="device:tokyo-shared-dev-01", kind=kind, owner=owner,
        launched_by=(owner or {}).get("ref") or "unknown", evidence_key=key,
        detail=detail or None)


def a_registered_resource(resource_id, **overrides):
    record = {"id": resource_id, "kind": "mcp-server", "owner": PROJECT_OWNER,
              "scope": "user", "lifecycle": {"class": "persistent"},
              "desired": {"state": "running"}, "protection": {"level": "normal"}}
    record.update(overrides)
    return registry.validate(record)


class NoticingIsNotRegisteringTests(unittest.TestCase):
    """Case 1, unauthorized. An observation has no owner in it, and for a kind
    whose contract demands a decisive owner that is the end of the line."""

    def test_an_observation_of_a_decisive_owner_kind_is_a_discovery_only(self):
        found = registration.observe(device_ref="device:tokyo-shared-dev-01",
                                     kind="agent-runtime", evidence_key="proc:40700")
        self.assertEqual(found.owner_state, registration.OWNER_UNKNOWN)
        self.assertTrue(found.deferrable)
        self.assertEqual(found.proposed, {},
                         "a discovery proposes nothing; a proposal is a claim "
                         "that something is known about the thing")
        self.assertIn("discovery only", found.discovery_note)
        self.assertIn("decisive owner", found.why)

    def test_an_unowned_discovery_cannot_be_authorized_into_resources(self):
        found = registration.observe(device_ref="device:d", kind="agent-runtime",
                                     evidence_key="proc:40700")
        with self.assertRaises(registration.RegistrationError) as caught:
            registration.authorize(found, registration.SCOPE_NODE, actor=ACTOR, at=AT)
        message = str(caught.exception)
        self.assertIn("no confirmable owner", message)
        self.assertIn("defer_refs", message)

    def test_a_discovery_is_deferred_with_a_reason_rather_than_dropped(self):
        """A recorded decision is not the same as an omission, and the reason
        is carried by the person who chose it."""
        found = registration.observe(device_ref="device:d", kind="agent-runtime",
                                     evidence_key="proc:40700")
        decision = registration.Decision(
            candidate_id=found.id, scope=registration.SCOPE_EXTERNAL,
            actor=ACTOR, at=AT)
        planned = registration.plan(found, decision)
        self.assertEqual(len(planned.records), 0)
        self.assertEqual(len(planned.defer), 1)
        self.assertTrue(planned.defer[0]["because"])
        self.assertIn("because", planned.defer[0])

    def test_an_observation_may_not_assert_what_the_thing_is_for(self):
        with self.assertRaises(registration.RegistrationError) as caught:
            registration.observe(device_ref="device:d", kind="process",
                                 evidence_key="p:1",
                                 asserted_purpose="MCP server for Playwright")
        self.assertIn("may not assert a purpose", str(caught.exception))

    def test_an_observation_of_an_unconstrained_kind_with_an_owner_proceeds(self):
        """The counterpart. Without it the rule above would be 'observations
        never produce candidates', which is a different and useless rule.

        `process` is the right kind for this: the contract declares no
        requirements for it at all, so the only thing standing between the
        observation and a candidate would be a rule invented here."""
        found = registration.observe(device_ref="device:d", kind="process",
                                     evidence_key="p:1", owner=PROJECT_OWNER)
        self.assertEqual(found.owner_state, registration.OWNER_CONFIRMED)
        self.assertFalse(found.deferrable)


class TheSessionEndsItTests(unittest.TestCase):
    """Case 4, lifecycle. A stdio MCP is started and ended by its client; a
    node service is not. Same kind, same scope vocabulary, two lifetimes."""

    def test_a_session_scoped_launch_gets_a_lifetime_its_kind_accepts(self):
        """`mcp-server` admits only persistent and session-scoped, so the
        session answer resolves to session-scoped and NOT to disposable. The
        module reads that from the contract; picking it locally would produce a
        plan the registry refuses for a reason unrelated to the person's answer.
        """
        candidate = a_launch_record(owner=SESSION_OWNER, key="launch:s:mcp",
                                    session_scoped=True)
        decision = registration.authorize(candidate, registration.SCOPE_SESSION,
                                          actor=ACTOR, at=AT)
        planned = registration.plan(candidate, decision, resource_id="scratch-mcp")
        self.assertTrue(planned.admissible, planned.because)
        self.assertEqual(planned.records[0].lifecycle, "session-scoped")
        self.assertEqual(planned.records[0].record["recovery"]["model"], "none")

    def test_a_session_scope_may_not_produce_a_durable_resource(self):
        """Driven through the registry's own rule rather than a copy of it.

        The module does not restate the rule — it lets the plan carry the
        illegal pair to `registry.validate` and reports what came back — so
        the test asks the registry the same question directly, and asserts the
        answer is a refusal rather than a plan nobody can apply.
        """
        with self.assertRaises(registry.RegistryInvalid) as caught:
            registry.validate({"id": "session-owned-thing", "kind": "mcp-server",
                               "owner": SESSION_OWNER, "scope": "user",
                               "lifecycle": {"class": "persistent"},
                               "desired": {"state": "running"},
                               "protection": {"level": "normal"}})
        self.assertIn("owner of record", str(caught.exception))

    def test_the_contract_names_this_case_and_the_module_does_not_repeat_it(self):
        """The rule has an author and a story attached, and the story is what
        tells a reader that the rule is narrow rather than arbitrary.

        The story stays in the contract. A module that copied the explanation
        would be maintaining a second copy of a rule it does not implement, and
        the copy is the one nobody updates.
        """
        ownership = registry.contract()["ownership"]
        self.assertIn("Case A", ownership["permitted"]["forbidden"]["persistent"])
        self.assertEqual(ownership["on_violation"]["disposition"], "REFUSE")

        source = (RUNTIME / "registration.py").read_text(encoding="utf-8")
        for pattern in ownership["session_owner_ids"]:
            self.assertNotIn(
                pattern, source,
                f"the contract already says {pattern!r} is a session owner; the "
                "list belongs there and a copy here would be a second "
                "implementation of `registry.validate`'s ownership rule")

    def test_a_project_scoped_and_a_session_scoped_candidate_are_different_candidates(self):
        """Case 4, the other half: two sightings that share a kind and an
        evidence key but differ in lifetime are two questions, not one. Merging
        them would answer the durable one with the disposable one's answer."""
        session_candidate = a_launch_record(owner=SESSION_OWNER, key="launch:x",
                                            session_scoped=True)
        node_candidate = a_launch_record(owner=PROJECT_OWNER, key="launch:x")
        self.assertNotEqual(session_candidate.id, node_candidate.id)

    def test_a_session_scoped_launch_proposes_a_lifetime_its_kind_admits(self):
        """The proposal is what a person is shown, so it may not name a class
        the registry would refuse. `disposable` is the obvious word and it is
        not one of the two `mcp-server` admits."""
        candidate = a_launch_record(owner=SESSION_OWNER, key="launch:s",
                                    session_scoped=True)
        self.assertIn("session-scoped", candidate.discovery_note)
        proposed = candidate.proposed["lifecycle"]["class"]
        self.assertEqual(proposed, "session-scoped")
        self.assertIn(proposed, registration._kind_lifecycles("mcp-server"))


class RegistrationGrantsRecognitionNotRepairTests(unittest.TestCase):
    """The rule that is easiest to lose: a registered resource and a resource
    that may be restarted unattended are different states."""

    def test_node_registration_does_not_turn_on_automatic_repair(self):
        candidate = a_launch_record(key="launch:node:svc")
        decision = registration.authorize(candidate, registration.SCOPE_NODE,
                                          actor=ACTOR, at=AT)
        planned = registration.plan(candidate, decision, resource_id="node-svc")
        self.assertTrue(planned.admissible, planned.because)
        record = planned.records[0].record
        self.assertEqual(record["recovery"]["model"], "redeclare",
                         "registering a node resource must not be the same act "
                         "as authorizing its restart")
        self.assertEqual(record["reconcile"]["mode"], "observe")
        self.assertTrue(record["recovery"]["because"],
                        "a 'cannot be repaired automatically' model has to say why")

    def test_restart_has_to_be_asked_for_by_name(self):
        candidate = a_launch_record(key="launch:node:svc")
        decision = registration.authorize(candidate, registration.SCOPE_NODE,
                                          actor=ACTOR, at=AT, recovery="restart")
        planned = registration.plan(candidate, decision, resource_id="node-svc")
        self.assertEqual(planned.records[0].record["recovery"]["model"], "restart")

    def test_a_protected_resource_may_not_be_registered_for_restart(self):
        candidate = a_launch_record(key="launch:node:prot")
        candidate.proposed["protection"] = {"level": "protected"}
        decision = registration.authorize(candidate, registration.SCOPE_NODE,
                                          actor=ACTOR, at=AT, recovery="restart")
        planned = registration.plan(candidate, decision, resource_id="protected-svc")
        self.assertFalse(planned.admissible)
        self.assertEqual(planned.refusal, "PROTECTED_MAY_NOT_RESTART")


class NamingTests(unittest.TestCase):
    """A resource id is read by a person at three in the morning, which is why
    the registry refuses ids that encode a host, a port or a run."""

    def test_a_writing_scope_without_a_name_refuses_rather_than_guessing(self):
        candidate = a_launch_record(key="launch:nameless")
        decision = registration.authorize(candidate, registration.SCOPE_NODE,
                                          actor=ACTOR, at=AT)
        planned = registration.plan(candidate, decision)
        self.assertEqual(planned.refusal, "RESOURCE_ID_REQUIRED")
        self.assertIn("a name a person will read", planned.because)

    def test_a_candidate_derived_id_would_not_have_been_legal(self):
        """The reason the refusal above is not merely conservative: the obvious
        shortcut fails the registry's own pattern, and it fails it by encoding
        the identity this module hashed."""
        candidate = a_launch_record(key="launch:nameless")
        with self.assertRaises(registry.RegistryInvalid):
            registry.validate({"id": f"{candidate.kind}:{candidate.id[:12]}",
                               "kind": "mcp-server", "owner": PROJECT_OWNER,
                               "scope": "user",
                               "lifecycle": {"class": "persistent"},
                               "desired": {"state": "running"},
                               "protection": {"level": "normal"}})


class DeduplicationTests(unittest.TestCase):
    """Case 2, duplicate. A question that reappears on every launch is a
    question people learn to dismiss, and a dismissed prompt authorizes
    nothing."""

    def test_the_same_candidate_is_not_asked_twice(self):
        first = a_launch_record(key="launch:same")
        decision = registration.authorize(first, registration.SCOPE_PROJECT,
                                          actor=ACTOR, at=AT)
        second_sighting = a_launch_record(key="launch:same")
        self.assertEqual(first.id, second_sighting.id)
        self.assertIsNotNone(registration.already_asked(second_sighting, [decision]))

    def test_a_different_candidate_is_still_asked(self):
        first = a_launch_record(key="launch:a")
        decision = registration.authorize(first, registration.SCOPE_PROJECT,
                                          actor=ACTOR, at=AT)
        other = a_launch_record(key="launch:b")
        self.assertIsNone(registration.already_asked(other, [decision]))

    def test_a_candidate_needs_an_evidence_key_to_be_identifiable(self):
        with self.assertRaises(registration.RegistrationError) as caught:
            registration.candidate_identity(device_ref="d", kind="process",
                                            owner_ref=None, evidence_key="")
        self.assertIn("dedup", str(caught.exception))

    def test_an_ignored_candidate_is_remembered_rather_than_forgotten(self):
        candidate = a_launch_record(key="launch:noise")
        decision = registration.authorize(candidate, registration.SCOPE_IGNORE,
                                          actor=ACTOR, at=AT)
        planned = registration.plan(candidate, decision)
        self.assertEqual(len(planned.records), 0)
        self.assertEqual(planned.defer[0]["because"], registration.DEFER_IGNORED)
        self.assertIsNotNone(registration.already_asked(candidate, [decision]))


class ScopeVocabularyTests(unittest.TestCase):
    """Two of the five answers write nothing, and a boolean would have collapsed
    them into 'no'."""

    def test_the_five_scopes_are_kept_distinct(self):
        self.assertEqual(len(registration.SCOPES), 5)
        self.assertEqual(len(set(registration.SCOPES)), 5)

    def test_external_records_a_dependency_and_registers_nothing(self):
        candidate = a_launch_record(key="launch:someone-else")
        decision = registration.authorize(candidate, registration.SCOPE_EXTERNAL,
                                          actor=ACTOR, at=AT)
        planned = registration.plan(candidate, decision)
        self.assertEqual(len(planned.records), 0)
        self.assertEqual(planned.defer[0]["because"], registration.DEFER_EXTERNAL)
        self.assertIn("not ours to manage", planned.because)

    def test_only_three_scopes_write_a_resource(self):
        writing = [s for s in registration.SCOPES
                   if registration.Decision(candidate_id="c", scope=s,
                                            actor=ACTOR, at=AT).registers]
        self.assertEqual(sorted(writing), ["node", "project", "session"])

    def test_a_scope_the_module_does_not_know_is_refused(self):
        with self.assertRaises(registration.RegistrationError):
            registration.Decision(candidate_id="c", scope="maybe", actor=ACTOR, at=AT)

    def test_a_writing_scope_without_an_authorizing_subject_is_refused(self):
        with self.assertRaises(registration.RegistrationError) as caught:
            registration.Decision(candidate_id="c", scope="node", actor="", at=AT)
        self.assertIn("unattributable", str(caught.exception))

    def test_a_deferred_ref_does_not_look_like_a_resource_id(self):
        """A deferred thing is exactly the thing with no registration; giving it
        a registration-shaped ref would put it back in the namespace the
        decision kept it out of."""
        candidate = a_launch_record(key="launch:deferred")
        decision = registration.Decision(candidate_id=candidate.id,
                                         scope=registration.SCOPE_EXTERNAL,
                                         actor=ACTOR, at=AT)
        ref = registration.plan(candidate, decision).defer[0]["ref"]
        self.assertTrue(ref.startswith("deferred:"))
        self.assertNotEqual(ref, candidate.id)


class SourceLostTests(unittest.TestCase):
    """Case 3, source lost. Losing sight of a resource is not evidence that it
    ended, and a desired state a person set is not a recoverer's to overrule."""

    def test_a_resource_no_observation_covers_is_unreported_not_absent(self):
        out = registration.resolve([a_registered_resource("delta-mcp")], {},
                                   now="2026-09-20T12:00:00Z")
        reading = out["readings"][0]
        self.assertEqual(reading["state"], "UNREPORTED")
        self.assertNotEqual(reading["state"], "ABSENT")
        self.assertEqual(reading["freshness"]["state"], "UNKNOWN")
        self.assertEqual(reading["desired"], "running",
                         "the last approved desired state stands")

    def test_nothing_is_ever_retired_by_this_module(self):
        out = registration.resolve([a_registered_resource("delta-mcp")], {},
                                   now="2026-09-20T12:00:00Z")
        self.assertEqual(out["retired"], [])
        self.assertIn("never retires", out["note"])

    def test_no_reading_is_actionable(self):
        out = registration.resolve([a_registered_resource("delta-mcp")], {},
                                   now="2026-09-20T12:00:00Z")
        self.assertTrue(all(not r["actionable"] for r in out["readings"]))

    def test_an_unparseable_timestamp_is_unknown_and_not_fresh(self):
        out = registration.resolve(
            [a_registered_resource("gamma-mcp")],
            {"gamma-mcp": {"running": True, "observed_at": "not-a-time"}},
            now="2026-09-20T12:00:00Z")
        reading = out["readings"][0]
        self.assertEqual(reading["state"], "UNREPORTED")
        self.assertEqual(reading["freshness"]["state"], "UNKNOWN")

    def test_a_fresh_observation_of_a_running_resource_is_observed(self):
        """The counterpart, so the class above cannot pass by reporting
        UNREPORTED for everything."""
        out = registration.resolve(
            [a_registered_resource("alpha-mcp")],
            {"alpha-mcp": {"running": True, "observed_at": "2026-09-20T11:59:30Z"}},
            now="2026-09-20T12:00:00Z")
        self.assertEqual(out["readings"][0]["state"], "OBSERVED")

    def test_an_observation_past_its_window_is_stale(self):
        """The window is the source's, not a guess made here. `registry`
        declares 86400 seconds, so an observation from six hours ago is FRESH —
        correctly — and going stale takes more than a day. A test that assumed
        "not just now" meant "stale" would pass against a wrong module and fail
        against this one."""
        window = graph_module.FRESH_WITHIN_SECONDS[registration.RESOLVE_SOURCE]
        self.assertEqual(window, 86400)

        recent = registration.resolve(
            [a_registered_resource("beta-mcp")],
            {"beta-mcp": {"running": True, "observed_at": "2026-09-20T06:00:00Z"}},
            now="2026-09-20T12:00:00Z")
        self.assertEqual(recent["readings"][0]["state"], "OBSERVED")

        aged = registration.resolve(
            [a_registered_resource("beta-mcp")],
            {"beta-mcp": {"running": True, "observed_at": "2026-09-17T06:00:00Z"}},
            now="2026-09-20T12:00:00Z")
        self.assertEqual(aged["readings"][0]["state"], "STALE")


class OneRuleOneImplementationTests(unittest.TestCase):
    """The rule that decides a session may not own a persistent resource lives
    in the contract's `ownership` block and is executed by
    `registry.validate`. If this module grew its own copy the two would agree
    today and be free to disagree later — the defect that put
    `test_composition_authority` in this suite."""

    def test_the_ownership_rule_is_read_from_the_contract(self):
        permitted = (registry.contract()["ownership"]["permitted"]
                     ["session_owner_lifecycle"])
        self.assertEqual(sorted(permitted), ["disposable", "session-scoped"])

    def test_registration_does_not_restate_the_session_ownership_rule(self):
        source = (RUNTIME / "registration.py").read_text(encoding="utf-8")
        self.assertNotIn("session_owner_lifecycle", source,
                         "the permitted-lifetime list belongs to the contract; a "
                         "copy here is a second implementation of one rule")
        self.assertNotIn("agent-session:*", source,
                         "the session owner id patterns belong to the contract")

    def test_the_lifecycle_a_scope_asks_for_is_checked_against_the_contract(self):
        """`mcp-server` admits only persistent and session-scoped. The module
        must reach that from `kind_requirements`, not from its own table."""
        allowed = registration._kind_lifecycles("mcp-server")
        self.assertEqual(sorted(allowed), ["persistent", "session-scoped"])
        self.assertIsNone(registration._kind_lifecycles("tunnel"),
                          "a kind that declares no constraint is unconstrained "
                          "by its kind, not free of the ownership rule")

    def test_an_empty_intersection_refuses_and_names_both_sides(self):
        """A refusal a caller cannot act on is a refusal that gets worked
        around, so it names what the scope offered and what the kind admits."""
        with mock.patch.dict(registration._SCOPE_LIFECYCLE,
                             {registration.SCOPE_SESSION: ("task-scoped",)}):
            lifecycle, because = registration._lifecycle_for("mcp-server",
                                                             registration.SCOPE_SESSION)
        self.assertIsNone(lifecycle)
        self.assertIn("task-scoped", because)
        self.assertIn("persistent", because)


class PlanIsNotAWriteTests(unittest.TestCase):
    def test_the_module_exposes_no_mutating_verb(self):
        for verb in ("write", "apply", "register", "save", "commit", "mutate"):
            self.assertFalse(hasattr(registration, verb),
                             f"registration.{verb}() would make this module able "
                             "to act, and reviewing 'what should happen' "
                             "separately from 'do it' is the whole split")

    def test_a_plan_that_the_registry_refuses_is_refused_as_a_plan(self):
        candidate = a_launch_record(key="launch:odd")
        decision = registration.authorize(candidate, registration.SCOPE_NODE,
                                          actor=ACTOR, at=AT)
        planned = registration.plan(candidate, decision, resource_id="has a space")
        self.assertFalse(planned.admissible)
        self.assertEqual(planned.refusal, "REGISTRY_REFUSED")
        self.assertTrue(planned.because)
        self.assertEqual(planned.records, [])

    def test_a_decision_about_one_candidate_cannot_plan_another(self):
        one = a_launch_record(key="launch:one")
        two = a_launch_record(key="launch:two")
        decision = registration.authorize(one, registration.SCOPE_NODE,
                                          actor=ACTOR, at=AT)
        with self.assertRaises(registration.RegistrationError) as caught:
            registration.plan(two, decision, resource_id="thing")
        self.assertIn(one.id, str(caught.exception))

    def test_every_refusal_carries_a_reason(self):
        """A refusal without a reason is a dead end with no repair in it."""
        candidate = a_launch_record(key="launch:r")
        refusals = []
        for scope in (registration.SCOPE_NODE,):
            decision = registration.authorize(candidate, scope, actor=ACTOR, at=AT)
            refusals.append(registration.plan(candidate, decision))          # no name
            refusals.append(registration.plan(candidate, decision,
                                              resource_id="has a space"))   # bad name
        discovered = registration.observe(device_ref="d", kind="agent-runtime",
                                          evidence_key="proc:1")
        refusals.append(registration.plan(
            discovered, registration.Decision(
                candidate_id=discovered.id, scope=registration.SCOPE_NODE,
                actor=ACTOR, at=AT)))
        for planned in refusals:
            self.assertFalse(planned.admissible)
            self.assertTrue(planned.refusal)
            self.assertTrue(planned.because.strip(),
                            f"{planned.refusal} was refused with no explanation")


# ---------------------------------------------------------------------------
# The loop, from a person's side
# ---------------------------------------------------------------------------

class TheLoopIsRunnableTests(unittest.TestCase):
    """The module was complete and nothing called it.

    A pipeline with no entry point is a schema — every rule above can hold and
    the loop still never closes, because closing it is not something the module
    can do for itself. What this class asserts is that all five stages are
    reachable by somebody at a terminal, and that none of them writes.
    """

    @classmethod
    def setUpClass(cls):
        cls.cli = _load("serveros_cli", "cli.py")

    def _args(self, **overrides):
        base = {"registry": None, "device": "", "launch_records": "", "out": "",
                "at": AT, "json": True, "candidates": "", "evidence_key": "",
                "scope": "ignore", "actor": ACTOR, "id": "", "note": "",
                "recovery": None}
        base.update(overrides)
        return argparse.Namespace(**base)

    def _observed(self, containers=(), ports=(), units=(), counts=None):
        """A stand-in for the live observation, in the shape the layer returns."""
        return {
            "unregistered": [*containers, *ports],
            "counts": {"containers_observed": len(containers),
                       "containers_unregistered": len(containers),
                       "endpoints_observed": 64,
                       "endpoints_unregistered": len(ports),
                       "endpoints_with_no_visible_holder": 50,
                       "units_observed": len(units),
                       "units_unregistered": None,
                       "registered": 4, **(counts or {})},
            "observed_at": "2026-09-20T09:59:00Z",
            "gap": "gapped",
        }

    def _container(self, ref, **detail):
        return {"kind": "container", "evidence_key": ref, "label": ref,
                "detail": {"disposition": "DEFER", "registry_anchors_tried":
                           {"tried": [ref], "matched": False}, **detail}}

    def _discover(self, observed, **overrides):
        """Run the command with the observation and the registry stubbed."""
        emitted = {}

        def capture(payload, *, as_json, lines):
            emitted["payload"] = payload
            emitted["lines"] = lines
            return None
        with mock.patch.object(self.cli, "load_registry", return_value=self._reg()), \
             mock.patch.object(self.cli, "_observed_without_a_registration",
                               return_value=observed), \
             mock.patch.object(self.cli, "emit", side_effect=capture):
            code = self.cli.cmd_registration_discover(self._args(**overrides))
        return code, emitted

    @staticmethod
    def _reg():
        class _Registry:
            device_ref = "device:stub"
            defer_refs: dict = {}

            @staticmethod
            def all():
                return []
        return _Registry()

    def _plan(self, document, **overrides):
        emitted = {}

        def capture(payload, *, as_json, lines=None):
            emitted["payload"] = payload
            emitted["lines"] = lines or []
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump(document, handle)
            path = handle.name
        try:
            with mock.patch.object(self.cli, "emit", side_effect=capture):
                code = self.cli.cmd_registration_plan(
                    self._args(candidates=path, **overrides))
        finally:
            Path(path).unlink()
        return code, emitted

    # -- the commands exist, and nothing may reach past them

    def test_all_three_commands_are_reachable_from_the_parser(self):
        """A verb a person cannot type is not an entry point. This is the test
        that fails if the group is ever left loaded but unreachable."""
        parser = self.cli.build_parser()
        required = {
            "discover": [],
            "resolve": [],
            "plan": ["--candidates", "/dev/null", "--evidence-key", "k",
                     "--scope", "ignore", "--actor", "user:test"],
        }
        for name, flags in required.items():
            with self.subTest(command=name):
                args = parser.parse_args(["registration", name, *flags])
                self.assertEqual(args.func.__name__, f"cmd_registration_{name}")

    def test_the_plan_scope_vocabulary_is_the_module_s(self):
        """`external` and `ignore` are the two that write nothing, and a CLI
        that had forgotten them would leave a person no way to answer 'this is
        not mine' or 'stop asking'."""
        parser = self.cli.build_parser()
        action = next(a for a in parser._subparsers._group_actions[0].choices[
            "registration"]._subparsers._group_actions[0].choices["plan"
            ]._actions if a.dest == "scope")
        self.assertEqual(sorted(action.choices), sorted(registration.SCOPES))

    def test_no_new_command_takes_a_flag_that_names_something_to_act_on(self):
        """The round's own rule, on the surface this round added: a command
        whose flags name a thing to stop is a command that stops it."""
        banned = ("--stop", "--restart", "--remove", "--prune", "--kill",
                  "--reclaim", "--write", "--apply", "--register")
        source = (RUNTIME / "cli.py").read_text(encoding="utf-8")
        start = source.index('registration = sub.add_parser(')
        end = source.index('alert = sub.add_parser(', start)
        block = source[start:end]
        self.assertEqual([flag for flag in banned if f'"{flag}"' in block], [])

    # -- discover

    def test_an_ownerless_observation_is_a_discovery_note_and_not_a_candidate(self):
        code, emitted = self._discover(
            self._observed([self._container("runtime:container:abc")]))
        self.assertEqual(code, self.cli.EXIT_OK)
        payload = emitted["payload"]
        self.assertEqual(payload["counts"]["candidates"], 1)
        self.assertEqual(payload["counts"]["candidates_with_an_owner"], 0)
        self.assertEqual(payload["counts"]["discovery_notes"], 1)
        self.assertTrue(payload["candidates"][0]["deferrable"])
        self.assertIn("发现记录", "\n".join(emitted["lines"]))

    def test_a_launch_record_is_what_makes_a_candidate(self):
        observed = self._observed([self._container("runtime:container:abc")])
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump({"schema": self.cli.LAUNCH_RECORD_SCHEMA,
                       "records": [{"kind": "container",
                                    "evidence_key": "runtime:container:abc",
                                    "launched_by": "project:x",
                                    "owner": PROJECT_OWNER}]}, handle)
            path = handle.name
        try:
            code, emitted = self._discover(observed, launch_records=path)
        finally:
            Path(path).unlink()
        self.assertEqual(code, self.cli.EXIT_OK)
        self.assertEqual(emitted["payload"]["counts"]["candidates_with_an_owner"], 1)
        self.assertFalse(emitted["payload"]["candidates"][0]["deferrable"])

    def test_a_launch_record_in_an_unknown_dialect_is_not_trusted(self):
        observed = self._observed([self._container("runtime:container:abc")])
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as handle:
            json.dump({"schema": "something/else.v1",
                       "records": [{"kind": "container",
                                    "evidence_key": "runtime:container:abc",
                                    "owner": PROJECT_OWNER}]}, handle)
            path = handle.name
        try:
            with self.assertRaises(ValueError):
                self._discover(observed, launch_records=path)
        finally:
            Path(path).unlink()

    def test_discover_reports_what_it_declined_to_ask_about(self):
        """The count is the whole reason a short list is not read as a quiet
        machine. 62 unattributed listeners must not become 62 questions, and
        they must not become zero either."""
        code, emitted = self._discover(self._observed())
        self.assertEqual(code, self.cli.EXIT_OK)
        counts = emitted["payload"]["counts"]
        self.assertEqual(counts["endpoints_observed"], 64)
        self.assertEqual(counts["endpoints_with_no_visible_holder"], 50)
        self.assertIn("未变成问题", "\n".join(emitted["lines"]))

    def test_discover_writes_nothing_without_an_out_path(self):
        with mock.patch.object(self.cli.Path, "write_text") as written:
            self._discover(self._observed([self._container("runtime:container:abc")]))
        self.assertFalse(written.called)

    # -- plan

    def test_a_writing_scope_on_an_ownerless_candidate_is_refused(self):
        discovered = registration.observe(
            device_ref="device:stub", kind="container",
            evidence_key="runtime:container:abc")
        code, emitted = self._plan({"device_ref": "device:stub",
                                    "candidates": [discovered.as_dict()]},
                                   evidence_key="runtime:container:abc",
                                   scope="node", actor=ACTOR, id="a-name")
        self.assertEqual(code, self.cli.EXIT_FAULT)
        self.assertEqual(emitted["payload"]["code"], "DECISION_REFUSED")
        self.assertIn("UNKNOWN", emitted["payload"]["detail"])

    def test_recording_a_dependency_is_admissible_and_writes_no_resource(self):
        discovered = registration.observe(
            device_ref="device:stub", kind="container",
            evidence_key="runtime:container:abc")
        code, emitted = self._plan({"device_ref": "device:stub",
                                    "candidates": [discovered.as_dict()]},
                                   evidence_key="runtime:container:abc",
                                   scope="external", actor=ACTOR)
        self.assertEqual(code, self.cli.EXIT_OK)
        plan = emitted["payload"]["plan"]
        self.assertTrue(plan["admissible"])
        self.assertEqual(plan["records"], [])
        self.assertEqual(plan["defer"][0]["because"], "EXTERNAL_DEPENDENCY")

    def test_a_writing_scope_produces_a_record_the_registry_validated(self):
        candidate = a_launch_record(kind="mcp-server", key="launch:proj:bridge")
        code, emitted = self._plan({"device_ref": "device:stub",
                                    "candidates": [candidate.as_dict()]},
                                   evidence_key="launch:proj:bridge",
                                   scope="node", actor=ACTOR, id="the-bridge")
        self.assertEqual(code, self.cli.EXIT_OK)
        plan = emitted["payload"]["plan"]
        self.assertTrue(plan["admissible"])
        self.assertEqual([r["id"] for r in plan["records"]], ["the-bridge"])
        # Validated, not written. The command says so on the way out.
        self.assertIn("不写任何登记", "\n".join(emitted["lines"]))

    def test_a_writing_scope_without_a_name_refuses_rather_than_deriving_one(self):
        candidate = a_launch_record(kind="mcp-server", key="launch:proj:bridge")
        code, emitted = self._plan({"device_ref": "device:stub",
                                    "candidates": [candidate.as_dict()]},
                                   evidence_key="launch:proj:bridge",
                                   scope="node", actor=ACTOR)
        self.assertEqual(code, self.cli.EXIT_OK)
        plan = emitted["payload"]["plan"]
        self.assertFalse(plan["admissible"])
        self.assertEqual(plan["records"], [])
        self.assertTrue(plan["refusal"])

    def test_an_evidence_key_that_is_not_in_the_document_is_refused(self):
        candidate = a_launch_record(kind="mcp-server", key="launch:proj:bridge")
        code, emitted = self._plan({"device_ref": "device:stub",
                                    "candidates": [candidate.as_dict()]},
                                   evidence_key="launch:somewhere-else",
                                   scope="ignore", actor=ACTOR)
        self.assertEqual(code, self.cli.EXIT_FAULT)
        self.assertEqual(emitted["payload"]["code"], "CANDIDATE_NOT_IN_DOCUMENT")

    def test_a_document_whose_id_contradicts_its_evidence_is_refused(self):
        """The id is re-derived, never trusted: a decision is matched to a
        candidate by it, so a stored id the evidence does not produce is how an
        answer lands on a thing nobody was shown."""
        forged = a_launch_record(kind="mcp-server", key="launch:proj:bridge").as_dict()
        forged["id"] = "0" * 64
        with self.assertRaises(registration.RegistrationError):
            registration.Candidate.from_dict(forged)

    def test_a_candidate_survives_a_round_trip_through_the_document(self):
        before = a_launch_record(kind="mcp-server", key="launch:proj:bridge")
        after = registration.Candidate.from_dict(before.as_dict())
        self.assertEqual(after.id, before.id)
        self.assertEqual(after.as_dict(), before.as_dict())

    # -- resolve

    def test_resolve_reads_the_measured_words_from_the_contract(self):
        """RUNNING and STOPPED are the contract's words, not this command's.
        A hand-written pair would agree today and diverge the day one grows a
        third state — and the third state's resources would be reported as
        observed and running, which is the answer that hides a stopped thing."""
        run = self._resolve([("a", "RUNNING"), ("b", "STOPPED"), ("c", "UNKNOWN")])
        self.assertIs(run["joined"]["a"]["running"], True)
        self.assertIs(run["joined"]["b"]["running"], False)
        self.assertNotIn("c", run["joined"])
        self.assertEqual([r["resource_id"] for r in run["not_decisive"]], ["c"])

    def test_a_reading_neither_side_established_is_not_called_absent(self):
        """`running: False` is ABSENT, and ABSENT is a claim. A measurement
        that says UNKNOWN has not made it, so it is left out of the join and
        named beside the readings instead."""
        run = self._resolve([("c", "UNKNOWN")])
        self.assertEqual(run["joined"], {})
        self.assertEqual(run["not_decisive"][0]["actual_state"], "UNKNOWN")

    def test_resolve_hands_the_module_a_clock_it_can_parse(self):
        """The clock is not the display string. Handed a formatted one, the
        module cannot age anything and answers UNREPORTED for every
        registration — four "nobody looked" where the truth is one bad
        argument, and the four are the ones a reader believes."""
        run = self._resolve([("a", "RUNNING")], at="")
        self.assertIsNotNone(graph_module._seconds_between(run["now"], run["now"]),
                             "the default clock must be one the module can parse")
        self.assertIs(run["joined"]["a"]["running"], True)

    def _resolve(self, rows, **overrides):
        """Run `resolve` with the measurement stubbed.

        Returns what the command passed to the module (`now`, `joined`), what
        the module said (`readings`), and what the command reported separately
        (`not_decisive`) — because the whole question here is whether those
        last two agree about the same four resources.
        """
        seen: dict = {}
        comparison = [{"resource_id": rid, "actual_state": state,
                       "measured_at": "2026-09-20T09:59:00Z",
                       "verdict": "IN_DESIRED_STATE", "why": "because"}
                      for rid, state in rows]
        real_resolve = registration.resolve

        def spy(resources, observations, *, now, source=None):
            seen["now"] = now
            seen["joined"] = dict(observations)
            document = real_resolve(resources, observations, now=now)
            seen["readings"] = document["readings"]
            return document

        def capture(payload, *, as_json, lines):
            seen["reported"] = payload

        with mock.patch.object(self.cli, "load_registry", return_value=self._reg()), \
             mock.patch.object(self.cli.view_mod, "desired_actual",
                               return_value={"comparisons": comparison,
                                             "measured_at": "2026-09-20T09:59:00Z",
                                             "unchecked": []}), \
             mock.patch.object(self.cli.registry_mod, "contract",
                               return_value={"desired_actual": {
                                   "satisfies": {"running": ["RUNNING"],
                                                 "stopped": ["STOPPED"]}}}), \
             mock.patch.object(registration, "resolve", side_effect=spy), \
             mock.patch.object(self.cli, "emit", side_effect=capture):
            code = self.cli.cmd_registration_resolve(self._args(**overrides))
        self.assertEqual(code, self.cli.EXIT_OK)
        return {"now": seen["now"], "joined": seen["joined"],
                "readings": seen["readings"],
                "not_decisive": seen["reported"]["not_decisive"]}


if __name__ == "__main__":
    unittest.main()
