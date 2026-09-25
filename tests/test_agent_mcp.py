#!/usr/bin/env python3
"""Tests for `serveros-agent-mcp` — the Agent Control surface (§11).

§11 is a list of names an agent must be able to reach and a rule about every one
of the mutating ones: the actor is resolved from a `context_ref` and is never
taken from the caller. Both halves are asserted here, plus the two structural
properties the surface exists to have — that it cannot express a shell, and that
no tool reaches a provider without going through the router.

The surface used to be narrower and the tests used to assert the narrowness.
Three of the four control verbs it declined are served now, because §22 landed
and the reason for declining them was the thing that landed.
"""
from __future__ import annotations

import ast
import io
import importlib.util
import json
import re
import sys
import unittest
from pathlib import Path

import threads
import yaml

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
MCP = REPO / "mcp"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


agent_mcp = _load("serveros_test_agent_mcp", MCP / "agent_mcp.py")
service_mod = _load("serveros_test_service_for_contract", MCP / "agent_session_service.py")
node_mcp = _load("serveros_test_node_mcp", MCP / "node_mcp.py")
# Loaded under the names the surface itself uses, so this file and the surface
# under test share one module object per file rather than two that disagree.
semantics = sys.modules["serveros_runtime_agent_sessions"]
router_mod = sys.modules["serveros_fleet_agent_control_router"]
target_mod = sys.modules["serveros_abilities_target"]
provider_instance = _load("serveros_abilities_provider_instance",
                          REPO / "abilities" / "provider_instance.py")
actor = sys.modules["_serveros_context_actor"]

LOCAL = "device:server-beijing"
FJ = "device:work-fj"
DELL = "device:personal-dell"
# Enrolled 2026-09-17. Named here rather than derived from the registry so that
# the expected ORDER stays asserted: `device.list` returns registry order, and
# a set would let a reordering pass unnoticed.
TOKYO = "device:tokyo-shared-dev-01"
RUNTIME = "agent-runtime:codex"
PROVIDER = "provider:test-agent-session"
# §17. A create states what its thread is for, and this is the Item the fixture
# threads are for. A real ref, and not a placeholder: `coordination.binding`
# only requires the field to be non-empty, and a fixture that passed "x" would
# not exercise the same path a person's Item does.
ITEM = "LIT-0101"


def minted_context(*, actor_id: str = "human:hwz") -> dict:
    """A context ServerOS really minted, from a report a provider really could
    have produced. Built through `actor.build` rather than hand-written: a
    literal here would be this file's idea of a trusted context, and the
    resolver's idea is the one that decides whether a mutation is authorized.
    """
    now = provider_instance.now_utc()
    report = {
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_agent_mcp_test",
        "message_ref": "om_agent_mcp_test",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": actor_id,
        "provider_pid": 4242,
    }
    return actor.build(report, now=now)


CONTEXT = minted_context()
CONTEXT_REF = CONTEXT["context_ref"]


class FakeDaemon:
    """serverosd, for the three methods this surface is allowed to call."""

    def __init__(self, *, decision: str = "ALLOW",
                 contexts: dict | None = None) -> None:
        self.decision = decision
        self.contexts = {CONTEXT_REF: CONTEXT} if contexts is None else contexts
        self.calls: list[tuple[str, dict]] = []

    def call(self, method, params=None):
        self.calls.append((method, dict(params or {})))
        if method == "device.show":
            return {"device_ref": LOCAL}
        if method == "agent.action.admit":
            return {"decision": self.decision, "reason": "fixture"}
        if method == "agent.context.resolve":
            ref = str((params or {}).get("context_ref"))
            if ref in self.contexts:
                return {"found": True, "context_ref": ref, "context": self.contexts[ref]}
            return {"found": False, "context_ref": ref,
                    "because": "no context with this ref was reported"}
        raise AssertionError(method)

    def methods(self) -> list[str]:
        return [name for name, _ in self.calls]


class FakeProvider:
    """A provider that declares what a real one declares, and no more.

    `provider_kind` is deliberately absent. Both deployed providers omit it, and
    the resolver treats an absent kind as no incompatibility — inventing one
    here would make this fixture assert against an adapter step that the real
    deployment never takes.
    """

    provider_id = PROVIDER

    def __init__(self, support: str = "SUPPORTED") -> None:
        self._support = support
        self.calls: list[str] = []

    def actions(self) -> dict:
        return {action: semantics.support(self._support)
                for action in semantics.contract()["support"]["actions"]}

    def observe(self, *, device_ref: str) -> dict:
        """One readiness record, with an observation on each of the two axes.

        `RUNNING` and `OK` are the two vocabularies rather than one, and that is
        the point of the axes: whether a provider answers and whether it can do
        the thing it accepted are different questions, and a fixture that gave
        both the same word would not exercise the distinction.
        """
        now = provider_instance.now_utc()
        return provider_instance.instance(
            provider=self.provider_id, device_ref=device_ref,
            observations=[
                provider_instance.observation(
                    axis=provider_instance.AXIS_CONTROL_PLANE, state="RUNNING",
                    observer_type="provider-native", observer_id="fixture",
                    observed_at=now, evidence_type="provider-response"),
                provider_instance.observation(
                    axis=provider_instance.AXIS_EXECUTION, state="OK",
                    observer_type="provider-native", observer_id="fixture",
                    observed_at=now, evidence_type="provider-response"),
            ],
            reference=now,
        )

    def list_agents(self, *, device_ref):
        return [{"agent_id": "a1", "agent_ref": "agent:a1", "device_ref": device_ref}]

    def list_sessions(self, *, device_ref):
        return [{"session_ref": semantics.session_ref(device_ref, self.provider_id, "s1"),
                 "provider_session_id": "s1",
                 "last_activity": "2026-01-01T00:00:00Z",
                 "action_support": {
                     "session.send": semantics.support("SUPPORTED"),
                     "session.result": semantics.support("SUPPORTED"),
                 }}]

    def _record(self, name, **kwargs):
        self.calls.append(name)
        return {"action": name,
                "session_ref": kwargs.get("session_ref_value")
                or semantics.session_ref(LOCAL, self.provider_id, "s1")}

    def create(self, **kwargs):
        return self._record("create", **kwargs)

    def send(self, **kwargs):
        return self._record("send", **kwargs)

    def result(self, **kwargs):
        return self._record("result", **kwargs)

    def stop(self, **kwargs):
        return self._record("stop", **kwargs)

    def resume(self, **kwargs):
        return self._record("resume", **kwargs)

    def interrupt(self, **kwargs):
        return self._record("interrupt", **kwargs)

    def handoff(self, **kwargs):
        return self._record("handoff", **kwargs)


def build(*, daemon=None, provider=None, providers=None, decision="ALLOW"):
    """The surface, over the real router and the real view.

    Nothing between the tool call and the provider is stubbed except the
    provider and the daemon. A fixture that replaced the router would be
    asserting this file's idea of §11 rather than the deployed one.
    """
    daemon = daemon or FakeDaemon(decision=decision)
    if providers is None:
        providers = [provider or FakeProvider()]
    device_ref = daemon.call("device.show", {})["device_ref"]
    service = service_mod.SessionService(
        device_ref=device_ref, providers=list(providers),
        admission=lambda action, context: daemon.call("agent.action.admit", {
            "action": action, "context": context}))
    router = router_mod.FleetAgentControlRouter(
        local_device_ref=device_ref,
        transports=[router_mod.LocalLoopbackTransport(
                        device_ref=device_ref, service=service,
                        bindings=threads.MemoryBindings(
                            unknown=threads.ASSUME_BOUND)),
                    router_mod.NodeControlTransport(connector=None,
                                                    device_refs=(FJ, DELL))],
        context_store=agent_mcp.DaemonContexts(daemon))
    view = agent_mcp.view_mod.FleetView(routing=router, sessions=lambda ref: {
        "state": "KNOWN", "sessions": service.session_list().get("sessions") or []},
        # The agent source is the REAL one, unlike the session lambda above: it
        # is the closure under test for `agent.list`, and a fixture that
        # answered from a lambda would be asserting this file's idea of what
        # one device's agents are rather than the deployment's.
        agents=agent_mcp._agents_for(service, device_ref))
    return agent_mcp.AgentControlClient(router=router, view=view), service, daemon


def call(client, name, **arguments):
    return agent_mcp.AgentMcpServer(client).handle({
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": name, "arguments": arguments},
    })


def payload(response):
    """(is_error, decoded answer)."""
    result = response["result"]
    text = result["content"][0]["text"]
    if result["isError"]:
        return True, text
    return False, json.loads(text)


def schema(name: str) -> dict:
    return next(t for t in agent_mcp.TOOLS if t["name"] == name)["inputSchema"]


# ---------------------------------------------------------------------------
# The surface is the one the contract declares, and the contract has a surface.
# ---------------------------------------------------------------------------

class TestTheSurfaceIsTheDeclaredOne(unittest.TestCase):
    def contract(self) -> dict:
        return yaml.safe_load((REPO / "fleet/mcp-contract.yaml").read_text())

    def test_every_declared_surface_has_an_implementation(self):
        """The gate that `serveros-fleet-mcp` would have failed.

        It was declared for two revisions with no `implementation:` key and no
        file behind it, and nothing noticed because nothing was looking. The
        rule is now structural: a surface in this file either names the code
        that serves it, in this repository or in a named other one, or it is
        not a surface.
        """
        surfaces = self.contract()["surfaces"]
        for name, block in surfaces.items():
            with self.subTest(surface=name):
                self.assertIn("implementation", block,
                              f"{name} is declared with nothing behind it")
                implementation = str(block["implementation"])
                if ":" in implementation:
                    repo, _, path = implementation.partition(":")
                    self.assertTrue(repo and path, implementation)
                    self.assertIn("tools_source", block,
                                  f"{name} lives in {repo} and does not name where "
                                  f"its tool list is read from")
                    continue
                self.assertTrue((REPO / implementation).exists(), implementation)

    def test_the_surface_serves_exactly_what_the_contract_lists(self):
        surface = self.contract()["surfaces"]["serveros-agent-mcp"]
        self.assertEqual(tuple(surface["tools"]), agent_mcp.tool_names())
        self.assertEqual(surface["resources"], [])
        self.assertEqual(surface["implementation"], "mcp/agent_mcp.py")

    def test_every_declared_control_action_is_served_or_named_absent(self):
        """The gap between "the contract declares it" and "a surface serves it"
        is where this project keeps finding declared-but-absent behaviour, so it
        gets a gate rather than a convention. `agent.session.interrupt` is the
        one action the router declares and this surface declines, and the
        contract says so in `explicitly_absent` rather than leaving a reader to
        infer it from a name that is simply not there.
        """
        surface = self.contract()["surfaces"]["serveros-agent-mcp"]
        declared = agent_mcp.declared_actions()
        served = set(agent_mcp.tool_names())
        absent = set(surface["explicitly_absent"])

        session_verbs = {verb for verb in declared if verb.startswith("agent.session.")}
        self.assertEqual(session_verbs - served, absent & session_verbs)
        self.assertFalse(served & absent, "a tool cannot be both served and absent")
        # Every mutating tool is an action the router declares. The reverse is
        # not required — the surface serves three reads the contract also knows
        # as session actions — so the assertion runs one way only.
        self.assertTrue(set(agent_mcp.CONTROL_ACTIONS) <= declared,
                        sorted(set(agent_mcp.CONTROL_ACTIONS) - declared))

    def test_the_release_read_and_write_sets_all_have_a_server(self):
        """§11's list, turned into a table with a check behind it.

        Every name in `section_11` must be served by the surface it is mapped
        to. Where that surface is implemented in this repository the tool list
        is read off the module; where it is not, the mapping is a handshake and
        the surface has already had to name its `tools_source` in the test
        above. What this rules out is the state §11's list was in before it was
        a table: names that read as coverage and resolve to nothing.
        """
        contract = self.contract()
        surfaces = contract["surfaces"]
        modules = {"serveros-agent-mcp": agent_mcp, "serveros-node-mcp": node_mcp}
        for section in ("read", "write"):
            for name, target in contract["section_11"][section].items():
                with self.subTest(section=section, name=name):
                    if isinstance(target, dict):
                        surface, spelled = target["surface"], target["as"]
                    else:
                        surface, spelled = target, name
                    self.assertIn(surface, surfaces, f"{name} maps to a surface that "
                                                     f"is not declared")
                    module = modules.get(surface)
                    if module is None:
                        self.assertIn("tools_source", surfaces[surface],
                                      f"{name} maps to {surface}, which does not say "
                                      f"where its tool list lives")
                        continue
                    self.assertIn(spelled, module.tool_names(),
                                  f"§11 maps {name} to {surface} as {spelled}, and "
                                  f"{surface} does not serve it")

    def test_no_name_is_served_by_two_surfaces(self):
        contract = self.contract()
        owned: dict[str, str] = {}
        for section in ("read", "write"):
            for name, target in contract["section_11"][section].items():
                surface = target["surface"] if isinstance(target, dict) else target
                self.assertNotIn(name, owned,
                                 f"{name} is claimed by {owned.get(name)} and {surface}")
                owned[name] = surface
        self.assertNotIn("shared_names", contract,
                         "an exemption block is back; §11's set partitions or it does not")

    def test_every_verb_the_session_contract_declares_has_a_name_here(self):
        """The session contract's verb lists, turned into a checkable table.

        `runtime/agent-session-contract.yaml` declares eleven provider verbs,
        partitions them into `read_only`, `delegation` and `human_gated`, and
        §11's set is elsewhere asserted to partition. None of that said whether
        any surface SERVES them, and two of them — `agent.list` and
        `agent.inspect` — were served by nothing: declared read-only in the
        contract, named as a surface by two docstrings in `mcp/`, and backed by
        a `SessionService.agent_list` that only its own tests ever called. The
        gap was invisible because every existing gate iterated a list that the
        missing names were not on.

        The mapping is derived rather than tabulated: the surface's name for a
        provider verb is `agent.<verb>`, and `agent.list` is already spelled
        that way. A table here would be a second copy of a naming convention,
        and the copy would be the one that fell behind.
        """
        contract = yaml.safe_load(
            (REPO / "runtime/agent-session-contract.yaml").read_text())
        admission = contract["admission"]
        declared = list(contract["support"]["actions"])
        served = set(agent_mcp.tool_names())
        absent = set(self.contract()["surfaces"]["serveros-agent-mcp"]["explicitly_absent"])

        # The partition, before the mapping: a verb in two lists is a verb with
        # two admission rules, and the one that took effect would be whichever
        # the reader looked at first.
        groups = {name: set(admission[name])
                  for name in ("read_only", "delegation", "human_gated")}
        pairs = [("read_only", "delegation"), ("read_only", "human_gated"),
                 ("delegation", "human_gated")]
        for left, right in pairs:
            with self.subTest(left=left, right=right):
                self.assertEqual(set(), groups[left] & groups[right],
                                 f"{left} and {right} overlap")
        self.assertEqual(set(declared), set().union(*groups.values()),
                         "a verb is declared outside the three admission lists")

        for verb in declared:
            with self.subTest(verb=verb):
                spelled = verb if verb.startswith("agent.") else f"agent.{verb}"
                if spelled in absent:
                    self.assertNotIn(spelled, served,
                                     "a verb cannot be both served and absent")
                    continue
                self.assertIn(spelled, served,
                              f"{verb} is declared in the session contract and no "
                              f"tool serves it; the surface's name for it is "
                              f"{spelled}")

    def test_every_tool_this_surface_declares_can_be_dispatched(self):
        """A tool in `TOOLS` that `call()` cannot route is a name in a menu.

        The reverse of the check above, and needed for the same reason: the two
        lists are written in different places, and the failure is one of them
        gaining a name the other did not.
        """
        client = build()[0]
        # Only the dispatch is under test, so the arguments are the shortest
        # ones the tools' own schemas allow, and the ref is a well-formed one so
        # that a routed action gets as far as the router rather than stopping
        # at the ref parser. What an action then does with them is every other
        # test in this file.
        arguments = {"device": LOCAL, "runtime": "agent-runtime:claude-code",
                     "agent_id": "a1", "context_ref": "human-context:x/y",
                     "session_role": "coordinator",
                     "session_ref": router_mod.semantics.session_ref(
                         LOCAL, "provider:fixture", "s1")}
        for spec in agent_mcp.TOOLS:
            with self.subTest(tool=spec["name"]):
                try:
                    client.call(spec["name"], arguments)
                except Exception as exc:  # noqa: BLE001 - the message is the evidence
                    self.assertNotIn("unimplemented Agent MCP operation", str(exc),
                                     f"{spec['name']} is declared and not dispatched")

    def test_every_tool_schema_is_closed(self):
        for spec in agent_mcp.TOOLS:
            with self.subTest(tool=spec["name"]):
                self.assertFalse(spec["inputSchema"]["additionalProperties"])
                self.assertTrue(spec["description"])

    def test_the_mutation_schemas_require_a_context_ref(self):
        for name in agent_mcp.CONTROL_ACTIONS:
            with self.subTest(tool=name):
                self.assertIn("context_ref", schema(name).get("required") or [])

    def test_a_mutation_takes_no_more_parameters_than_the_router_declares(self):
        """One list of what an action may carry, checked rather than restated.

        The surface accepting a parameter the router would refuse is the
        surface advertising a field it cannot carry — and an accepted
        free-form field is where an argv eventually arrives.
        """
        declared = router_mod.action_params()
        optional = router_mod.action_optional()
        for name in agent_mcp.CONTROL_ACTIONS:
            with self.subTest(tool=name):
                allowed = set(declared[name]) | set(optional[name])
                address = set(schema(name)["properties"]) - allowed
                # The address keys are the envelope: which machine, which
                # runtime, which session, whose context. They are not `params`
                # and the router validates them from the envelope, not from
                # `params`, so they are not in the contract's per-action lists.
                address -= {"device", "runtime", "session_ref", "context_ref",
                            "workspace", "work_item", "timeout_s",
                            "idempotency_key", "turn_idempotency_key"}
                self.assertFalse(address & allowed, sorted(address & allowed))
                for key in allowed:
                    self.assertIn(key, schema(name)["properties"],
                                  f"{name} drops {key!r}, which the router accepts")


# ---------------------------------------------------------------------------
# The actor is resolved, never accepted.
# ---------------------------------------------------------------------------

class TestTheActorIsNeverSelfReported(unittest.TestCase):
    def setUp(self):
        self.client, self.service, self.daemon = build()
        self.provider = self.service.providers[PROVIDER]
        self.ref = semantics.session_ref(LOCAL, PROVIDER, "s1")

    def test_an_actor_argument_is_refused_rather_than_ignored(self):
        is_error, text = payload(call(
            self.client, "agent.session.send", session_ref=self.ref, runtime=RUNTIME,
            instruction="go", turn_idempotency_key="t1",
            context_ref=CONTEXT_REF, actor="human:somebody"))
        self.assertTrue(is_error)
        self.assertIn("unexpected=['actor']", text)
        # And the refusal is total: the provider was never reached.
        self.assertEqual(self.provider.calls, [])

    def test_a_mutation_without_a_context_is_refused_at_the_boundary(self):
        """Two gates, and the outer one is the schema.

        `context_ref` is a required parameter of every mutating tool, so a call
        that omits it never reaches the router. Both gates stay: the schema is a
        statement about the surface, and the router's refusal is the one that
        holds when a second caller assembles a request some other way.
        """
        is_error, text = payload(call(
            self.client, "agent.session.send", session_ref=self.ref, runtime=RUNTIME,
            instruction="go", turn_idempotency_key="t1"))
        self.assertTrue(is_error)
        self.assertIn("missing=['context_ref']", text)
        self.assertEqual(self.provider.calls, [])

    def test_a_mutation_with_an_empty_context_is_refused_below(self):
        """The gate the schema cannot express: a ref that is present and empty.

        `required` accepts `""`, so this reaches the router, and the router is
        where "no actor" is decided. A surface that treated an empty string as
        absent would be deciding identity itself.
        """
        is_error, answer = payload(call(
            self.client, "agent.session.send", session_ref=self.ref, runtime=RUNTIME,
            instruction="go", turn_idempotency_key="t1", context_ref=""))
        self.assertFalse(is_error, answer)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "INVALID_CONTEXT")
        self.assertIn("no actor", answer["detail"])
        self.assertEqual(self.provider.calls, [])

    def test_a_context_that_does_not_resolve_is_refused(self):
        is_error, answer = payload(call(
            self.client, "agent.session.send", session_ref=self.ref, runtime=RUNTIME,
            instruction="go", turn_idempotency_key="t1",
            context_ref="human-context:botmux/0000000000000000"))
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "INVALID_CONTEXT")
        self.assertIn("no context was resolved", answer["detail"])
        self.assertEqual(self.provider.calls, [])

    def test_the_context_is_resolved_through_the_daemon_not_from_a_local_copy(self):
        call(self.client, "agent.session.send", session_ref=self.ref, runtime=RUNTIME,
             instruction="go", turn_idempotency_key="t1", context_ref=CONTEXT_REF)
        self.assertIn("agent.context.resolve", self.daemon.methods())

    def test_the_store_behind_the_surface_refuses_to_be_enumerated(self):
        """An empty iterator would say the store is empty, which is a claim."""
        store = agent_mcp.DaemonContexts(self.daemon)
        with self.assertRaises(TypeError):
            list(store)
        with self.assertRaises(TypeError):
            len(store)


# ---------------------------------------------------------------------------
# Every §11 mutation, and what each one needs.
# ---------------------------------------------------------------------------

class TestEveryMutationIsAddressedAndGated(unittest.TestCase):
    def setUp(self):
        self.client, self.service, self.daemon = build()
        self.provider = self.service.providers[PROVIDER]
        self.ref = semantics.session_ref(LOCAL, PROVIDER, "s1")

    def arguments_for(self, name: str, context_ref: str | None = CONTEXT_REF) -> dict:
        """The smallest legal call of each control action, addressed to the
        fixture's session. `context_ref=None` leaves the key out entirely, which
        the schema refuses and `""` does not.

        A create carries the two §17 fields, because "legal" now includes saying
        what the thread is for — an `execution` thread with no Item is refused
        before the provider is asked, and a fixture that omitted them would be a
        fixture of an illegal call.
        """
        if name == "agent.session.create":
            arguments = {"device": LOCAL, "runtime": RUNTIME, "agent_id": "a1",
                         "instruction": "go", "idempotency_key": "k1",
                         "session_role": "execution", "bound_item_ref": ITEM}
        else:
            arguments = {"session_ref": self.ref, "runtime": RUNTIME}
            if name == "agent.session.send":
                arguments.update(instruction="go", turn_idempotency_key="k1")
        if context_ref is not None:
            arguments["context_ref"] = context_ref
        return arguments

    def test_every_control_action_declares_a_context_ref(self):
        """§11: every mutation requires one, and the requirement is in the
        schema rather than in a sentence a caller could skip."""
        for name in agent_mcp.CONTROL_ACTIONS:
            with self.subTest(tool=name):
                self.assertIn("context_ref", schema(name)["required"])

    def test_every_control_action_refuses_an_empty_context(self):
        for name in agent_mcp.CONTROL_ACTIONS:
            with self.subTest(tool=name):
                arguments = self.arguments_for(name)
                arguments["context_ref"] = ""
                is_error, answer = payload(call(self.client, name, **arguments))
                self.assertFalse(is_error, answer)
                self.assertFalse(answer["ok"])
                self.assertEqual(answer["reason"], "INVALID_CONTEXT")
        self.assertEqual(self.provider.calls, [])

    def test_the_device_is_taken_from_the_session_ref(self):
        """No `device` argument, and the call still lands on the right machine.

        A ref names its device. Requiring a caller to repeat it would give it
        two places to be inconsistent with itself, and the second one would
        win depending on which the transport happened to read.
        """
        is_error, answer = payload(call(
            self.client, "agent.session.send", session_ref=self.ref, runtime=RUNTIME,
            instruction="go", turn_idempotency_key="t1", context_ref=CONTEXT_REF))
        self.assertFalse(is_error, answer)
        self.assertTrue(answer["ok"], answer)
        self.assertEqual(answer["device_ref"], LOCAL)
        self.assertIn("send", self.provider.calls)

    def test_a_device_that_disagrees_with_the_ref_is_refused(self):
        """A refusal, not a tool error, and the difference matters here.

        The call is addressable — both devices exist — and the router is the
        thing that says two of them were named. It answers with §8's word for
        it rather than with a Python exception dressed as a result.
        """
        is_error, answer = payload(call(
            self.client, "agent.session.send", device=FJ, session_ref=self.ref,
            runtime=RUNTIME, instruction="go", turn_idempotency_key="t1",
            context_ref=CONTEXT_REF))
        self.assertFalse(is_error, answer)
        self.assertEqual(answer["state"], "REFUSED")
        self.assertEqual(answer["reason_code"], "INVALID_CONTEXT")
        self.assertIn(FJ, answer["detail"])
        self.assertEqual(self.provider.calls, [])

    def test_a_session_ref_that_is_not_a_session_ref_is_refused(self):
        is_error, answer = payload(call(
            self.client, "agent.session.send", session_ref="oc_some_chat_id",
            runtime=RUNTIME, instruction="go", turn_idempotency_key="t1",
            context_ref=CONTEXT_REF))
        self.assertFalse(is_error, answer)
        self.assertEqual(answer["state"], "REFUSED")
        self.assertEqual(answer["reason_code"], "INVALID_CONTEXT")
        self.assertEqual(self.provider.calls, [])

    def test_a_runtime_that_is_not_a_runtime_ref_is_refused(self):
        """§4. A cliId, a container name or a binary path is not a runtime.

        None of these is resolved to anything: the ref is rejected before the
        router sees it, and the provider is never asked.
        """
        for bad in ("codex", "codex-cli", "/usr/bin/codex", "agent-runtime:"):
            with self.subTest(runtime=bad):
                arguments = self.arguments_for("agent.session.send")
                arguments["runtime"] = bad
                is_error, answer = payload(call(
                    self.client, "agent.session.send", **arguments))
                self.assertFalse(is_error, answer)
                self.assertEqual(answer["state"], "REFUSED", answer)
                self.assertEqual(answer["reason_code"], "INVALID_CONTEXT")
        self.assertEqual(self.provider.calls, [])

    def test_create_does_not_accept_a_provider(self):
        """The provider comes from the resolved target, not from the caller.

        A create that named its own provider would use a resolution it did not
        ask for and cannot see, and the caller would have chosen the machine
        the request lands on without choosing the device.
        """
        self.assertNotIn("provider", schema("agent.session.create")["properties"])
        is_error, text = payload(call(
            self.client, "agent.session.create", device=LOCAL, runtime=RUNTIME,
            agent_id="a1", instruction="go", idempotency_key="k1",
            context_ref=CONTEXT_REF, provider=PROVIDER))
        self.assertTrue(is_error)
        self.assertIn("unexpected=['provider']", text)

    def test_create_reaches_the_provider_that_was_resolved(self):
        is_error, answer = payload(call(
            self.client, "agent.session.create", device=LOCAL, runtime=RUNTIME,
            agent_id="a1", instruction="go", idempotency_key="k1",
            context_ref=CONTEXT_REF, session_role="execution",
            bound_item_ref=ITEM))
        self.assertFalse(is_error, answer)
        self.assertTrue(answer["ok"], answer)
        self.assertIn("create", self.provider.calls)
        # The provider the call actually landed on is reported back, as the
        # target the router resolved. A surface that dropped this would leave
        # the caller unable to tell which provider answered a create it never
        # named one for.
        self.assertEqual(answer["target"]["provider_ref"], PROVIDER)
        self.assertEqual(answer["target"]["agent_runtime"], RUNTIME)
        self.assertEqual(answer["target"]["device_ref"], LOCAL)

    def test_a_create_with_no_role_cannot_be_expressed_through_this_surface(self):
        """§17, enforced at the schema rather than at the router.

        `session_role` is in the tool's `required`, so an unbound create is not
        a refused call here — it is not a call this surface can be asked to
        make. That is the stronger form of the claim: a caller cannot reach the
        provider with no purpose recorded even by accident. The router's own
        refusal for the same payload is
        `tests/test_agent_control_router.py::TestRequestsAreClosed`, which is
        where the non-MCP paths land.

        A tool error, not a refusal: nothing about the fleet was established, so
        there is no reason word from the fleet vocabulary to report.
        """
        is_error, answer = payload(call(
            self.client, "agent.session.create", device=LOCAL, runtime=RUNTIME,
            agent_id="a1", instruction="go", idempotency_key="k1",
            context_ref=CONTEXT_REF))
        self.assertTrue(is_error)
        self.assertIn("session_role", answer)
        self.assertEqual(self.provider.calls, [])

    def test_a_coordinator_create_is_expressible_and_may_not_name_an_item(self):
        """`coordinator` is a legal role, so the schema must not forbid it.

        The role that exists to cross Items — and the migration path for the
        threads that predate §17 — would be unreachable if the surface only
        accepted `execution`. The item rule is the kernel's, not the schema's.
        """
        is_error, answer = payload(call(
            self.client, "agent.session.create", device=LOCAL, runtime=RUNTIME,
            agent_id="a1", instruction="go", idempotency_key="k1",
            context_ref=CONTEXT_REF, session_role="coordinator"))
        self.assertFalse(is_error, answer)
        self.assertTrue(answer["ok"], answer)
        self.assertIsNone(answer["result"]["binding"]["bound_item_ref"])

    def test_a_human_gated_action_with_a_context_and_no_approval_is_not_admitted(self):
        """§22. An identified actor is not an approval, and the two are not
        allowed to collapse into one word."""
        is_error, answer = payload(call(
            self.client, "agent.session.stop", session_ref=self.ref, runtime=RUNTIME,
            context_ref=CONTEXT_REF))
        self.assertFalse(is_error, answer)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "APPROVAL_REQUIRED")
        self.assertEqual(self.provider.calls, [])

    def test_a_remote_device_answers_a_typed_unavailability(self):
        """§7/§13. Never a locally-served request, and never an empty list."""
        remote = semantics.session_ref(FJ, PROVIDER, "s1")
        is_error, answer = payload(call(
            self.client, "agent.session.send", session_ref=remote, runtime=RUNTIME,
            instruction="go", turn_idempotency_key="t1", context_ref=CONTEXT_REF))
        self.assertFalse(is_error, answer)
        self.assertFalse(answer["ok"])
        self.assertEqual(answer["reason"], "NODE_RUNTIME_UNAVAILABLE")
        self.assertEqual(self.provider.calls, [])


# ---------------------------------------------------------------------------
# Reads. Cost is disclosed, and absence is typed.
# ---------------------------------------------------------------------------

class TestTheReadPath(unittest.TestCase):
    def setUp(self):
        self.client, self.service, self.daemon = build()

    def test_device_list_comes_from_the_view_and_states_its_level(self):
        is_error, answer = payload(call(self.client, "device.list", level="L1"))
        self.assertFalse(is_error, answer)
        self.assertEqual(answer["level"], "L1")
        self.assertEqual([row["device_ref"] for row in answer["devices"]],
                         [LOCAL, FJ, DELL, TOKYO])

    def test_two_levels_do_not_return_the_same_fields(self):
        """§12 asks for this by name, so it is asserted rather than described."""
        _e, shallow = payload(call(self.client, "device.list", level="L0"))
        _e, deep = payload(call(self.client, "device.list", level="L1"))
        self.assertNotEqual(shallow["fields"], deep["fields"])
        self.assertLess(len(shallow["fields"]), len(deep["fields"]))
        self.assertTrue(set(shallow["fields"]) < set(deep["fields"]))

    def test_the_cheap_level_touches_no_clock(self):
        _e, answer = payload(call(self.client, "device.list", level="L0"))
        for row in answer["devices"]:
            with self.subTest(device=row["device_ref"]):
                self.assertNotIn("last_seen", row)
                self.assertNotIn("reachability", row)

    def test_a_device_with_no_node_runtime_is_not_rendered_as_a_working_one(self):
        _e, answer = payload(call(self.client, "device.list", level="L1"))
        row = next(r for r in answer["devices"] if r["device_ref"] == FJ)
        self.assertEqual(row["node_runtime"]["state"], "ABSENT")
        # §13: the registry declaring an agent on a device must not read as a
        # claim that the device can run it.
        self.assertEqual(row["declared_agents"], ["claude-code", "codex"])

    def test_an_unknown_device_is_a_bad_reference_not_an_unreachable_machine(self):
        is_error, answer = payload(call(self.client, "device.inspect",
                                        device="device:not-enrolled"))
        self.assertFalse(is_error, answer)
        self.assertEqual(answer["state"], "REFUSED")
        self.assertEqual(answer["reason_code"], "INVALID_CONTEXT")

    def test_provider_readiness_is_an_observation_with_a_time_on_it(self):
        is_error, answer = payload(call(self.client, "agent.provider.list", device=LOCAL))
        self.assertFalse(is_error, answer)
        self.assertEqual(answer["providers"]["state"], "KNOWN")
        readiness = answer["provider_readiness"]["instances"][0]
        self.assertEqual(readiness["status"], "AVAILABLE")
        self.assertTrue(readiness["observed_at"])

    def test_an_absent_provider_says_which_absence_it_is(self):
        is_error, answer = payload(call(self.client, "agent.provider.inspect",
                                        device=LOCAL, provider="provider:nope"))
        self.assertFalse(is_error, answer)
        self.assertEqual(answer["state"], "NOT_FOUND")
        self.assertEqual(answer["inventory_state"], "KNOWN")
        self.assertIn("did not list this provider", answer["because"])

    def test_a_remote_provider_listing_is_typed_rather_than_empty(self):
        _e, answer = payload(call(self.client, "agent.provider.list", device=FJ))
        self.assertNotEqual(answer.get("providers"), {"state": "KNOWN", "providers": []})
        self.assertNotEqual(answer["state"], "KNOWN")

    def test_the_runtime_list_does_not_claim_any_device_can_run_one(self):
        is_error, answer = payload(call(self.client, "agent.runtime.list"))
        self.assertFalse(is_error, answer)
        self.assertEqual([row["runtime_ref"] for row in answer["runtimes"]],
                         [f"agent-runtime:{rid}" for rid in target_mod.runtime_ids()])
        self.assertIn("does not", answer["note"])

    def test_an_undeclared_runtime_is_absent_rather_than_invented(self):
        is_error, answer = payload(call(self.client, "agent.runtime.inspect",
                                        runtime="agent-runtime:gemini-cli"))
        self.assertFalse(is_error, answer)
        self.assertEqual(answer["state"], "NOT_FOUND")
        self.assertIn("does not declare", answer["because"])
        self.assertEqual(answer["declared"], ["agent-runtime:claude-code",
                                              "agent-runtime:codex"])

    def test_a_read_does_not_ask_for_admission(self):
        call(self.client, "agent.provider.list", device=LOCAL)
        self.assertNotIn("agent.action.admit", self.daemon.methods())


# ---------------------------------------------------------------------------
# The surface cannot become something else.
# ---------------------------------------------------------------------------

class TestTheSurfaceCannotBecomeAShell(unittest.TestCase):
    FORBIDDEN = ("shell", "exec", "command", "spawn", "rm", "kill", "reboot", "prune")

    def test_no_tool_name_carries_a_lifecycle_or_shell_word(self):
        for name in agent_mcp.tool_names():
            with self.subTest(tool=name):
                for word in self.FORBIDDEN:
                    self.assertNotIn(word, name.split(".")[-1])

    def test_the_node_surface_has_no_mutating_verb(self):
        """§11's read-only invariant, checked as a property rather than by
        reading the file. Every node tool must be a name this surface's own
        mutation vocabulary does not contain, and none may take a context."""
        for spec in node_mcp.TOOLS:
            name = spec["name"]
            with self.subTest(tool=name):
                for word in self.FORBIDDEN:
                    self.assertNotIn(word, name.split(".")[-1])
                self.assertNotIn("context_ref", spec["inputSchema"]["properties"],
                                 f"{name} asks for an actor, which only a mutation "
                                 f"would need")

    def test_the_surface_only_reaches_the_router_the_view_and_the_daemon(self):
        """§11's prohibitions, asserted against the module's IMPORTS.

        Read from the AST rather than by searching the text, because the text
        contains sentences saying what the module must not do — a substring
        search over prose fails on its own documentation, and a check that
        fails on the docstring is a check somebody deletes.

        The import list is the claim: this module can reach the standard
        library, the three adapters it is part of, and nothing else. There is no
        `subprocess`, no socket, no database driver, no docker client — and
        since a module cannot use what it never imported, that is the whole
        assertion, stated once.
        """
        ALLOWED = {
            "__future__", "importlib.util", "json", "sys", "collections.abc",
            "pathlib", "argparse",
        }
        imported = set()
        for node in ast.walk(ast.parse((MCP / "agent_mcp.py").read_text())):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                imported.add(node.module or "")
        self.assertFalse(imported - ALLOWED, sorted(imported - ALLOWED))

        # And the OTHER half of the reach: the module loads its parts by path,
        # so the path list is the rest of the surface's vocabulary.
        #
        # `serveros_runtime_coordination` is §17's kernel, and it is here rather
        # than typed out in the schema for the reason this whole list exists: the
        # `session_role` enum in `agent.session.create` is read from
        # `coordination.ROLES`, so a role added to the kernel reaches the tool
        # schema without a second edit. A typed-out list would be the surface
        # offering a role the kernel refuses, or refusing one it accepts.
        loaded = set(re.findall(r'_load\("([^"]+)"', (MCP / "agent_mcp.py").read_text()))
        self.assertEqual(loaded, {
            "serveros_mcp_node_shared", "serveros_agent_session_service",
            "serveros_botmux_provider", "serveros_codex_provider",
            "serveros_fleet_agent_control_router", "serveros_fleet_transport",
            "serveros_fleet_view", "serveros_abilities_target",
            "serveros_runtime_coordination",
            # §M/§N. The console's rules, and §P's feature vocabulary. Both are
            # loaded rather than restated here for the same reason as the
            # coordination kernel above: the console owns what a selection may
            # carry and the feature contract owns which operations a runtime is
            # offered, and a second copy in this module would be the second
            # spelling that drifts. Named explicitly so that adding a third
            # module to this surface is a decision rather than a commit.
            "serveros_runtime_human_console",
            "serveros_abilities_runtime_binding",
        })

    def test_daemon_never_imports_or_calls_botmux_provider(self):
        source = (REPO / "runtime/daemon.py").read_text().lower()
        self.assertNotIn("botmux", source)
        self.assertNotIn("urllib", source)
        self.assertNotIn("http://", source)
        self.assertNotIn("https://", source)

    def test_a_parameter_the_router_does_not_declare_cannot_be_passed(self):
        client, service, _daemon = build()
        is_error, text = payload(call(
            client, "agent.session.create", device=LOCAL, runtime=RUNTIME,
            agent_id="a1", instruction="go", idempotency_key="k1",
            context_ref=CONTEXT_REF, argv=["sh", "-c", "id"]))
        self.assertTrue(is_error)
        self.assertIn("unexpected=", text)
        self.assertEqual(service.providers[PROVIDER].calls, [])


# ---------------------------------------------------------------------------
# The protocol surface.
# ---------------------------------------------------------------------------

class TestTheProtocolSurface(unittest.TestCase):
    def test_the_served_version_is_the_one_source(self):
        """§2/§26. A second literal here is a second answer to which version is
        running, and the second one is what a client is told."""
        self.assertEqual(agent_mcp.SERVER_VERSION, node_mcp.SERVER_VERSION)

    def test_initialize_states_the_authority_boundary(self):
        server = agent_mcp.AgentMcpServer(client=object())
        result = server.handle({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": "2025-06-18"}})["result"]
        self.assertEqual(result["serverInfo"]["name"], "serveros-agent-mcp")
        instructions = result["instructions"]
        self.assertIn("serverosd admission", instructions)
        self.assertIn("provider-backed", instructions)
        self.assertIn("context_ref", instructions)
        self.assertIn("NODE_RUNTIME_UNAVAILABLE", instructions)

    def test_an_unknown_tool_names_the_ones_that_exist(self):
        server = agent_mcp.AgentMcpServer(client=object())
        response = server.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                  "params": {"name": "agent.session.kill",
                                             "arguments": {}}})
        self.assertTrue(response["result"]["isError"])
        self.assertIn("agent.session.stop", response["result"]["content"][0]["text"])

    def test_declared_absent_verbs_have_no_reachable_path(self):
        server = agent_mcp.AgentMcpServer(client=object())
        for verb in ("agent.session.interrupt", "remote.shell", "session.stop"):
            with self.subTest(verb=verb):
                response = server.handle({"jsonrpc": "2.0", "id": 1,
                                          "method": "tools/call",
                                          "params": {"name": verb, "arguments": {}}})
                self.assertTrue(response["result"]["isError"])
                self.assertIn("Unknown tool",
                              response["result"]["content"][0]["text"])

    def test_the_stdio_loop_answers_every_request(self):
        client, _service, _daemon = build()
        out = io.StringIO()
        agent_mcp.serve(
            instream=io.StringIO("\n".join([
                json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                            "params": {}}),
                json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list",
                            "params": {}}),
                "not json",
            ]) + "\n"),
            outstream=out,
            server=agent_mcp.AgentMcpServer(client),
        )
        lines = [json.loads(line) for line in out.getvalue().splitlines()]
        self.assertEqual([line.get("id") for line in lines], [1, 2, None])
        self.assertEqual(len(lines[1]["result"]["tools"]), len(agent_mcp.TOOLS))
        self.assertEqual(lines[2]["error"]["code"], -32700)

    def test_list_tools_prints_every_tool(self):
        import contextlib
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(agent_mcp.main(["--list-tools"]), 0)
        for name in agent_mcp.tool_names():
            self.assertIn(name, out.getvalue())


class TheSessionSourceAnswersForOneDevice(unittest.TestCase):
    """`_sessions_for` answers about the device it was built for, and no other.

    Two files promised this in prose and neither was tested, which is how the
    behaviour drifted: the callable accepted a `device_ref`, ignored it, and
    called `service.session_list()` for whatever the view asked about. A live
    call for a REMOTE device came back `{"state": "KNOWN", "sessions": []}` —
    this node's empty list wearing another machine's name.

    `state: KNOWN` is the damaging half. An empty list says "that device has no
    active sessions", which is a claim about a machine nobody asked, and no
    downstream reader can tell it apart from a device that really was asked and
    really was empty.

    The pair below is the point: the local ref answers, the remote ref does
    not, and the two readings differ in KIND rather than in value. A test that
    only asserted the remote case would pass against an implementation that
    refused the local one too.
    """

    def source(self, listing=None):
        service = fake_service(listing)
        return agent_mcp._sessions_for(service, LOCAL)

    def test_the_local_device_is_answered_by_the_local_service(self):
        answer = self.source({"sessions": [{"session_id": "s-1"}]})(LOCAL)
        self.assertEqual(answer["state"], "KNOWN")
        self.assertEqual(answer["sessions"], [{"session_id": "s-1"}])

    def test_a_remote_device_is_unknown_and_not_an_empty_list(self):
        for remote in (FJ, DELL):
            with self.subTest(device=remote):
                answer = self.source({"sessions": [{"session_id": "s-1"}]})(remote)
                self.assertEqual(answer["state"], "UNKNOWN")
                # NOT `KNOWN` with `sessions: []`. The distinction is the whole
                # test: the fixture's service HAS a session, so an
                # implementation that leaked through would report it here and
                # be caught rather than accidentally looking right.
                self.assertNotIn("sessions", answer)
                self.assertIn(remote, answer["detail"])
                self.assertIn(LOCAL, answer["detail"])

    def test_a_device_that_is_not_a_ref_at_all_is_refused_by_the_source(self):
        """Not merely "not local": a name that is not a device ref is not a
        device. The source's answer must not depend on whether the string
        happens to look like something the registry would accept."""
        answer = self.source({"sessions": []})("server-beijing")
        self.assertEqual(answer["state"], "UNKNOWN")
        self.assertNotIn("sessions", answer)

    def test_an_unreadable_listing_passes_its_own_state_through(self):
        """The local device's own failure is still reported as the service
        reported it, not flattened into UNKNOWN by this wrapper."""
        answer = self.source({"state": "NOT_RUNNING",
                              "reason": "no provider is live"})(LOCAL)
        self.assertEqual(answer["state"], "NOT_RUNNING")
        self.assertEqual(answer["reason"], "no provider is live")


class TheAgentInventoryAndTheSourceItReads(unittest.TestCase):
    """`agent.list` / `agent.inspect`, and the one-device rule behind them.

    Two different claims are under test and they need different fixtures. The
    first is what the verb ANSWERS about a device: the provider rows, with each
    provider's own readiness record beside them, because the finding this
    exists for — agents listed, provider unable to execute — is only readable
    when both halves are on one record. The second is WHICH device it is
    allowed to answer about, which is the rule `_sessions_for` already had to
    learn the hard way and which this closure copies rather than re-derives.
    """

    def test_the_verb_serves_the_providers_agents_with_their_readiness(self):
        client, service, _ = build()
        answer = client.agent_list(LOCAL)
        self.assertEqual("KNOWN", answer["state"])
        self.assertEqual(["a1"], [row["agent_id"] for row in answer["agents"]])
        self.assertEqual(LOCAL, answer["device_ref"])
        # The load-bearing half. Without readiness on the same record an
        # operator reads a listed agent as a runnable one, which is the exact
        # overstatement §64 added a second axis to prevent.
        self.assertTrue(answer["providers"], "readiness did not travel with the inventory")
        self.assertTrue(answer["complete"])

    def test_a_provider_that_cannot_be_asked_is_not_an_empty_fleet(self):
        """A broken provider is evidence, and it is not an empty list.

        The distinction is the module's own rule, restated where it is cheapest
        to get wrong: an agent inventory that comes back `agents: []` with no
        error reads as "this device has no agents". The service records the
        failure instead, and `complete` is what tells a reader the list is
        partial — a field that would be decorative if the failure were dropped.
        """
        class Broken(FakeProvider):
            def list_agents(self, *, device_ref):
                raise RuntimeError("the provider's transport is down")

        client, _, _ = build(provider=Broken())
        answer = client.agent_list(LOCAL)
        self.assertEqual([], answer["agents"])
        self.assertEqual(1, len(answer["provider_errors"]))
        self.assertIn("transport is down", answer["provider_errors"][0]["error"])
        self.assertFalse(answer["complete"],
                         "a partial fleet was reported as the whole one")

    def test_a_remote_device_is_unknown_and_names_no_agents(self):
        """The failure this rule was written for, one provider method over.

        The fixture's service HAS an agent, so an implementation that answered
        for whatever device it was asked about would return that agent here and
        be caught. `agents: []` would be the subtler version of the same lie:
        "that device has no agents" about a machine nobody asked.
        """
        client, _, _ = build()
        for remote in (FJ, DELL):
            with self.subTest(device=remote):
                answer = client.agent_list(remote)
                self.assertEqual("UNKNOWN", answer["state"])
                self.assertNotIn("agents", answer)
                self.assertIn(remote, answer["detail"])
                self.assertIn(LOCAL, answer["detail"])

    def test_inspect_resolves_against_the_list_the_verb_serves(self):
        client, _, _ = build()
        answer = client.agent_inspect(LOCAL, "a1")
        self.assertEqual("FOUND", answer["state"])
        self.assertEqual("a1", answer["agent"]["agent_id"])
        self.assertEqual(client.agent_list(LOCAL)["agents"][0], answer["agent"])

    def test_inspect_says_what_was_listed_when_nothing_matches(self):
        """Not `NOT_FOUND` with nothing beside it.

        A misspelling and an unenrolled agent have different repairs, and the
        list is what tells them apart — the same reason the closure harness
        carries the provider's own list into its refusal.
        """
        client, _, _ = build()
        answer = client.agent_inspect(LOCAL, "a2")
        self.assertEqual("NOT_FOUND", answer["state"])
        self.assertEqual(["a1"], answer["agents_listed"])
        self.assertTrue(answer["inventory_complete"])

    def test_inspect_refuses_to_choose_between_two_agents(self):
        """Two matches is its own answer, not the first one winning.

        The agent_ref is the unambiguous spelling, so the refusal can say which
        two were meant rather than leaving a caller to guess which agent it had
        just addressed.
        """
        class TwoAgents(FakeProvider):
            def list_agents(self, *, device_ref):
                return [{"agent_id": "a1", "agent_ref": "agent:p1/a1"},
                        {"agent_id": "a1", "agent_ref": "agent:p2/a1"}]

        client, _, _ = build(provider=TwoAgents())
        answer = client.agent_inspect(LOCAL, "a1")
        self.assertEqual("AMBIGUOUS", answer["state"])
        self.assertEqual(["agent:p1/a1", "agent:p2/a1"], answer["matched"])

    def test_an_unreadable_inventory_is_not_a_missing_agent(self):
        """`NOT_FOUND` is a claim about a fleet. This is the case where nobody
        established one, and answering with a miss would send a caller to enrol
        an agent that may already be there."""
        client, _, _ = build()
        answer = client.agent_inspect(FJ, "a1")
        self.assertEqual("UNKNOWN", answer["state"])
        self.assertNotIn("agents_listed", answer)
        self.assertEqual("a1", answer["agent_id"])

    def test_the_deployment_actually_wires_an_agent_source(self):
        """`build()` — the wiring the MCP server runs, not this file's fixture.

        Found by a mutation that survived: dropping `agents=` from
        `agent_mcp.build()` left the whole class above green, because every test
        in it assembles its own graph through this file's own `build()`. Two
        builders, one of them tested — the same two-copies shape the ledger
        keeps recording, so the deployment's builder gets its own assertion.

        It runs with no network on purpose. `build()` against the real providers
        is the honest measure: the codex provider answers from its own local
        app-server, the botmux one lands in `provider_errors` because this test
        has no dashboard token, and the answer is `KNOWN` with
        `complete: False`. A wiring that dropped the agent source would answer
        UNKNOWN with a reason instead, which is what makes this falsifiable.
        """
        client = agent_mcp.build(daemon=FakeDaemon())
        answer = client.agent_list(LOCAL)
        self.assertEqual("KNOWN", answer["state"],
                         "the deployment's view has no agent source bound")
        self.assertTrue(answer["agents"], "the deployment listed no agent at all")
        self.assertFalse(answer["complete"])
        self.assertTrue(answer["provider_errors"])
        for row in answer["agents"]:
            with self.subTest(agent=row.get("agent_id")):
                self.assertEqual(LOCAL, row.get("device_ref"))
                self.assertTrue(row.get("provider"), "an agent with no owning provider")

    def test_the_source_refuses_a_remote_device_rather_than_answering_for_it(self):
        service = fake_agent_service()
        source = agent_mcp._agents_for(service, LOCAL)
        answer = source(FJ)
        self.assertEqual("UNKNOWN", answer["state"])
        self.assertIn(FJ, answer["detail"])
        self.assertIn(LOCAL, answer["detail"])


def fake_agent_service():
    class _Service:
        def agent_list(self):
            return {"device_ref": LOCAL, "agents": [{"agent_id": "a1"}],
                    "providers": [], "provider_errors": [], "complete": True}
    return _Service()


def fake_service(listing):
    class _Service:
        def session_list(self):
            return listing
    return _Service()


if __name__ == "__main__":
    unittest.main()
