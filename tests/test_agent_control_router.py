#!/usr/bin/env python3
"""Tests for the Fleet Agent Control Router (§7–§9).

The tests that matter here are the ones about a sentence somebody would
otherwise write. Each class below is one of those sentences, kept false.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import threads

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


router_mod = _load("serveros_fleet_agent_control_router",
                   ROOT / "fleet" / "agent_control_router.py")
transport = _load("serveros_fleet_transport", ROOT / "fleet" / "transport.py")
target = _load("serveros_abilities_target", ROOT / "abilities" / "target.py")
semantics = _load("serveros_runtime_agent_sessions", ROOT / "runtime" / "agent_sessions.py")
provider_instance = _load("serveros_abilities_provider_instance",
                          ROOT / "abilities" / "provider_instance.py")
actor = _load("_serveros_context_actor", ROOT / "context" / "actor.py")


# A context ServerOS really minted, from a report a HumanControlProvider really
# could have produced. Built through `actor.build` rather than hand-written,
# because a literal would be this test's idea of a trusted context and the
# resolver's idea is the one that decides whether a mutation is authorized.
def minted_context(*, actor_id: str = "human:hwz") -> dict:
    now = provider_instance.now_utc()
    report = {
        "human_control_provider": "provider:botmux-human-control",
        "transport": "feishu",
        "transport_identity": "botmux-verified-enterprise-user",
        "conversation_ref": "oc_router_test",
        "message_ref": "om_router_test",
        "observed_at": now,
        "provider_status": "VERIFIED",
        "provenance": "FACT",
        "actor_ref": actor_id,
        "provider_pid": 4242,
    }
    return actor.build(report, now=now)


CONTEXT = minted_context()
CONTEXT_REF = CONTEXT["context_ref"]
CONTEXT_STORE = {CONTEXT_REF: CONTEXT}

LOCAL = "device:server-beijing"
FJ = "device:work-fj"
DELL = "device:personal-dell"
RUNTIME = "agent-runtime:codex"
KIND_PROVIDER = "provider:test-agent-session"
FAKE_NODE = HERE / "fake_node_control.py"
ANCHOR = "2026-09-14T00:00:00Z"
# §17. Two real Item refs, because a mismatch test is only a mismatch test if
# the two sides differ — and because these are the two refs the incident that
# motivated §17 actually named.
ITEM = "LIT-0101"
OTHER_ITEM = "LIT-0205"


# ---------------------------------------------------------------------------
# A loopback service that records what it was asked to do.
# ---------------------------------------------------------------------------

class FakeProvider:
    """The two things the router reads off a provider: id and capability table."""

    def __init__(self, provider_id: str, support: dict | None = None) -> None:
        self.provider_id = provider_id
        self._support = support if support is not None else {
            action: {"state": "SUPPORTED"}
            for action in semantics.contract()["support"]["actions"]}

    def actions(self) -> dict:
        return dict(self._support)


class RecordingService:
    """Stands in for `SessionService`. Records calls; returns shaped answers."""

    def __init__(self, refuse: dict | None = None, *, provider_id: str = KIND_PROVIDER,
                 support: dict | None = None, ready: bool = True) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.refuse = refuse
        self.providers = {provider_id: FakeProvider(provider_id, support)}
        self._ready = ready
        self._provider_id = provider_id

    def readiness(self, provider_id: str) -> dict:
        if not self._ready:
            raise RuntimeError("provider would not answer a readiness probe")
        return {
            "schema": "serveros/provider-instance.v1",
            "provider": provider_id,
            "device_ref": LOCAL,
            # Observed now, because a readiness record is a fact about now. A
            # pinned timestamp would age out of the freshness window and the
            # suite would start failing on a date rather than on a change.
            "observed_at": provider_instance.now_utc(),
            "status": "AVAILABLE",
            "status_provenance": "INFERRED",
            "reason": None,
            "reason_text": None,
        }

    def _record(self, name: str, **kwargs) -> dict:
        self.calls.append((name, kwargs))
        if self.refuse and self.refuse.get("on") == name:
            raise RuntimeError(self.refuse["message"])
        return {"session_ref": kwargs.get("session_ref_value") or "session:local:1",
                "action": name, **{k: v for k, v in kwargs.items() if v is not None}}

    def create(self, **kwargs):
        return self._record("create", **kwargs)

    def send(self, **kwargs):
        return self._record("send", **kwargs)

    def result(self, **kwargs):
        return self._record("result", **kwargs)

    def session_inspect(self, ref):
        return self._record("session_inspect", session_ref_value=ref)

    def resume(self, **kwargs):
        return self._record("resume", **kwargs)

    def interrupt(self, **kwargs):
        return self._record("interrupt", **kwargs)

    def stop(self, **kwargs):
        return self._record("stop", **kwargs)

    def handoff(self, **kwargs):
        return self._record("handoff", **kwargs)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]


def fake_node_transport(*, device_refs=(FJ, DELL), mode="ok", reason=None,
                        presence=None, node_runtime=None, timeout_s=5.0):
    """A `NodeControlTransport` whose connector drives a real child process."""
    env = dict(os.environ, SERVEROS_FAKE_NODE_MODE=mode)
    if reason:
        env["SERVEROS_FAKE_NODE_REASON"] = reason
    if presence:
        env["SERVEROS_FAKE_NODE_PRESENCE"] = presence
    if node_runtime:
        env["SERVEROS_FAKE_NODE_RUNTIME"] = node_runtime
    proc = subprocess.Popen(
        [sys.executable, str(FAKE_NODE)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, env=env)

    sent: list[dict] = []

    def connector(payload: dict) -> dict:
        sent.append(payload)
        proc.stdin.write(json.dumps(payload) + "\n")
        proc.stdin.flush()
        line = proc.stdout.readline() if mode != "hang" else _read_with_timeout(proc)
        if not line:
            raise transport.TransportUnavailable(
                "the forged node closed the connection without answering")
        if mode == "badjson":
            raise transport.TransportUnavailable("the node answered with non-JSON")
        return json.loads(line)

    node = router_mod.NodeControlTransport(
        connector=connector, device_refs=tuple(device_refs), timeout_s=timeout_s)
    node.sent = sent
    node.proc = proc
    return node


def _read_with_timeout(proc, seconds: float = 1.0) -> str:
    """Wait briefly, then give up. Used only by the `hang` fixture."""
    import select
    ready, _, _ = select.select([proc.stdout], [], [], seconds)
    return proc.stdout.readline() if ready else ""


def router(service, node=None, *, local=LOCAL, context_store=CONTEXT_STORE,
           bindings=None):
    # §17. `assume-bound` because these are tests about ROUTING, not about the
    # thread boundary — `tests/test_coordination.py` is where the boundary is
    # tested, and it uses the refusing mode. The mode is named here rather than
    # defaulted so that a reader can tell which test is guarding what.
    nodes = [router_mod.LocalLoopbackTransport(
        device_ref=local, service=service,
        bindings=bindings or threads.MemoryBindings(unknown=threads.ASSUME_BOUND))]
    if node is not None:
        nodes.append(node)
    return router_mod.FleetAgentControlRouter(
        local_device_ref=local, transports=nodes, context_store=context_store)


def deployed_router(service, **kwargs):
    """The configuration this release actually deploys.

    One local loopback plus a node transport that covers both Windows devices
    and has no connector, because neither runs a ServerOS node. Constructing it
    here means the tests assert the answer the deployment gives, not the answer
    a router with no transports happens to give — those are different, and the
    second one is UNKNOWN rather than a typed unavailability.
    """
    return router(service, router_mod.NodeControlTransport(
        connector=None, device_refs=(FJ, DELL)), **kwargs)


def ok_service(**kwargs) -> RecordingService:
    """A local service whose single provider supports every declared verb.

    Built from the session contract's own action list so that a verb added
    there is supported here without an edit, and a verb this release cannot
    serve fails loudly rather than silently reporting SUPPORTED.
    """
    return RecordingService(**kwargs)


def payload(action: str, device: str = LOCAL, runtime: str = RUNTIME, **extra) -> dict:
    base = {"action": action, "device_ref": device, "agent_runtime": runtime}
    base.update(extra)
    return base


READ_ONLY = "agent.session.inspect"
MUTATING = "agent.session.send"


# ---------------------------------------------------------------------------
# The vocabulary is borrowed, not restated.
# ---------------------------------------------------------------------------

class TestReasonVocabularyIsBorrowed(unittest.TestCase):

    def test_router_uses_the_owner_list(self):
        self.assertEqual(router_mod.reason_vocabulary(), target.reason_vocabulary())

    def test_router_declares_no_reasons_of_its_own(self):
        # The failure mode: the router grows a local list, the owner's list
        # changes, and the two disagree while both look authoritative.
        vocabulary = set(router_mod.reason_vocabulary())
        for name, value in vars(router_mod).items():
            if name.startswith("__") or not isinstance(value, (list, tuple, set, frozenset)):
                continue
            self.assertNotEqual(set(value), vocabulary,
                                f"{name} in the router is a copy of the reason "
                                f"vocabulary; reach it through reason_vocabulary()")

    def test_the_router_contract_points_at_the_owner_and_does_not_recut_it(self):
        # Two modules each holding the list would pass the test above and still
        # drift, so the file is asked where the list lives rather than what it
        # contains.
        failures = router_mod.contract()["failures"]
        self.assertEqual(failures["vocabulary_owner"],
                         "abilities/target.py#reason_vocabulary")
        source = (ROOT / "fleet" / "agent-control-contract.yaml").read_text(encoding="utf-8")
        self.assertNotIn("reasons:\n  values:", source)
        self.assertNotIn("APPROVAL_EXPIRED", source)

    def test_transport_failure_refuses_outside_the_list(self):
        with self.assertRaises(target.TargetError):
            router_mod.TransportFailure("MADE_UP_REASON", "no")

    def test_every_reason_the_router_can_produce_is_in_the_list(self):
        # Every refusal path the router reaches is checked at construction, so
        # this is really a test that no path bypasses the check. It walks the
        # paths that exist and asserts each word is one the owner declared.
        svc = RecordingService()
        r = router(svc)
        produced = []
        for action in ("agent.session.inspect", "agent.session.send"):
            for device in (LOCAL, FJ):
                response = r.route(router_mod.request_from_payload(
                    payload(action, device, context_ref="ctx:test:1",
                            params=_minimal_params(action))))
                if response.reason:
                    produced.append(response.reason)
        for reason in produced:
            self.assertIn(reason, router_mod.reason_vocabulary())

    def test_the_control_vocabulary_and_the_session_contract_agree(self):
        # A control name whose verb the session contract does not declare would
        # route to nothing, and nothing is what it would return.
        declared = set(semantics.contract()["support"]["actions"])
        for control, verb in router_mod.control_actions().items():
            self.assertIn(verb, declared,
                          f"{control} maps to {verb}, which the session contract "
                          f"does not declare")

    def test_every_session_action_that_can_be_controlled_is_reachable(self):
        controllable = {a for a in semantics.contract()["support"]["actions"]
                        if a.startswith("session.") and a != "session.list"}
        self.assertEqual(controllable, set(router_mod.control_actions().values()))

    def test_nothing_may_be_required_and_optional_at_once(self):
        required = router_mod.action_params()
        optional = router_mod.action_optional()
        for control in required:
            overlap = set(required[control]) & set(optional[control])
            self.assertEqual(overlap, set(),
                             f"{control} declares {overlap} as both required and "
                             f"optional; one of those is wrong")

    def test_forbidden_actions_are_named_and_absent(self):
        forbidden = router_mod.contract()["actions"]["forbidden"]
        self.assertIn("remote.shell", forbidden)
        for action in forbidden:
            self.assertNotIn(action, router_mod.control_actions())


def _minimal_params(action: str) -> dict:
    return {
        "agent.session.create": {"agent_id": "codex-app-server",
                                 "instruction": "hello",
                                 "idempotency_key": "k-1"},
        "agent.session.send": {"instruction": "more", "turn_idempotency_key": "t-1"},
        "agent.session.result": {"trigger_id": "tr-1"},
    }.get(action, {})


# ---------------------------------------------------------------------------
# A request carries a closed action and typed parameters.
# ---------------------------------------------------------------------------

class TestRequestsAreClosed(unittest.TestCase):

    def test_unknown_action_is_malformed_not_a_refusal(self):
        with self.assertRaises(target.Rejected):
            router_mod.request_from_payload(payload("agent.session.explode"))

    def test_remote_shell_is_not_an_action(self):
        for name in ("remote.shell", "remote.exec", "device.reboot"):
            with self.assertRaises(target.Rejected):
                router_mod.request_from_payload(payload(name))

    def test_an_undeclared_parameter_is_refused(self):
        with self.assertRaises(target.Rejected) as caught:
            router_mod.request_from_payload(payload(
                "agent.session.send", context_ref=CONTEXT_REF,
                params={"instruction": "x", "turn_idempotency_key": "t",
                        "command": "rm -rf /"}))
        self.assertIn("command", str(caught.exception))
        self.assertIn("never a command", str(caught.exception))

    def test_a_credential_in_a_request_is_refused(self):
        with self.assertRaises(target.Rejected) as caught:
            router_mod.request_from_payload(payload(
                "agent.session.send", context_ref=CONTEXT_REF,
                params={"instruction": "x", "turn_idempotency_key": "t"},
                access_token="not-a-real-token"))
        # The token is in an undeclared envelope field, so it is refused as an
        # unexpected key rather than as a credential — either way it does not
        # reach a transport, which is the property under test.
        self.assertIn("does not take", str(caught.exception))

    def test_a_credential_nested_in_a_declared_parameter_is_refused(self):
        with self.assertRaises(target.Rejected) as caught:
            router_mod.request_from_payload(payload(
                "agent.session.handoff", context_ref=CONTEXT_REF,
                params={"checkpoint": {"summary": "s", "token": "not-a-real-token"},
                        "to_device_ref": FJ}))
        self.assertIn("token", str(caught.exception))

    def test_missing_required_parameters_are_refused(self):
        with self.assertRaises(target.Rejected) as caught:
            router_mod.request_from_payload(payload("agent.session.send"))
        self.assertIn("instruction", str(caught.exception))

    def test_the_wire_payload_has_no_free_form_field(self):
        request = router_mod.request_from_payload(payload(
            "agent.session.send", context_ref=CONTEXT_REF,
            params={"instruction": "x", "turn_idempotency_key": "t"},
            session_ref="session:a:b:c"))
        wire = request.wire()
        # §17's two fields are on the wire for every request and are None on
        # every action but create. They are carried rather than omitted because
        # a node-side router rebuilds the request from this payload, and a
        # `session.create` whose role did not survive the hop would be refused
        # on the far side for a fact the caller did supply.
        self.assertEqual(set(wire), {"schema", "action", "device_ref", "agent_runtime",
                                     "params", "context_ref", "workspace", "work_item",
                                     "session_ref", "session_role", "bound_item_ref",
                                     "correlation_id",
                                     "idempotency_key", "timeout_s"})
        self.assertEqual(wire["schema"], router_mod.SCHEMA)
        self.assertIsNone(wire["session_role"])
        self.assertIsNone(wire["bound_item_ref"])

    def test_a_correlation_id_is_minted_when_absent(self):
        one = router_mod.request_from_payload(payload("agent.session.inspect"))
        two = router_mod.request_from_payload(payload("agent.session.inspect"))
        self.assertTrue(one.correlation_id)
        self.assertNotEqual(one.correlation_id, two.correlation_id)

    def test_a_mutation_without_a_trusted_context_is_refused(self):
        svc = RecordingService()
        response = router(svc).route(router_mod.request_from_payload(payload(
            MUTATING, LOCAL, params=_minimal_params(MUTATING))))
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "INVALID_CONTEXT")
        self.assertEqual(svc.calls, [], "a refused mutation reached the service")

    def test_a_read_without_a_context_is_allowed(self):
        svc = RecordingService()
        response = router(svc).route(router_mod.request_from_payload(
            payload(READ_ONLY, LOCAL, session_ref="session:a:b:c")))
        self.assertTrue(response.ok, response.detail)
        self.assertEqual(svc.names(), ["session_inspect"])

    def test_a_create_must_say_what_its_thread_is_for(self):
        """§17. No role, no default, and the refusal is about the request.

        A default here would be a guess about somebody's intent recorded as a
        fact, and the fact would then be read by every later turn as though a
        person had stated it.
        """
        with self.assertRaises(target.Rejected) as caught:
            router_mod.request_from_payload(payload(
                "agent.session.create", context_ref=CONTEXT_REF,
                params=_minimal_params("agent.session.create")))
        self.assertIn("session_role", str(caught.exception))

    def test_the_role_fields_belong_to_the_one_action_that_creates_a_thread(self):
        """`session.send` may not restate what the thread is for.

        The thread's Item is immutable, so there is nowhere for a `bound_item_ref`
        on a send to go — and a field that is accepted and ignored is the
        declaration-with-no-reader shape this repository keeps finding.
        """
        for action, extra in (("agent.session.send", {"session_role": "execution"}),
                              ("agent.session.resume", {"bound_item_ref": ITEM})):
            with self.subTest(action=action):
                with self.assertRaises(target.Rejected) as caught:
                    router_mod.request_from_payload(payload(
                        action, context_ref=CONTEXT_REF,
                        params=_minimal_params(action), **extra))
                self.assertIn("does not take", str(caught.exception))


# ---------------------------------------------------------------------------
# §17: what a thread is for, and which verbs may put work into one.
#
# The kernel's rules are `tests/test_coordination.py`'s. This class tests the
# two seams that make the kernel reachable — `session.create` records a binding,
# `session.send` and `session.resume` consult it — because a rule nothing calls
# is the "complete, tested and unreachable" shape this repository keeps finding.
# ---------------------------------------------------------------------------

class TestTheTurnIntoAThreadIsGated(unittest.TestCase):

    def setUp(self):
        self.service = ok_service()
        self.bindings = threads.MemoryBindings(unknown=threads.REFUSE)
        self.router = router(self.service, bindings=self.bindings)
        self.ref = semantics.session_ref(LOCAL, KIND_PROVIDER, "s1")

    def created(self, **extra):
        """A create whose binding is in place, returning the provider's ref."""
        response = self.router.route(router_mod.request_from_payload(payload(
            "agent.session.create", context_ref=CONTEXT_REF,
            params=_minimal_params("agent.session.create"),
            session_role="execution", bound_item_ref=ITEM, **extra)))
        self.assertTrue(response.ok, response.detail)
        return response.result["session_ref"]

    def send(self, work_item=None):
        base = {"context_ref": CONTEXT_REF, "session_ref": self.ref,
                "params": _minimal_params("agent.session.send")}
        if work_item is not None:
            base["work_item"] = work_item
        return self.router.route(router_mod.request_from_payload(
            payload("agent.session.send", **base)))

    def test_a_create_records_what_its_thread_is_for(self):
        """Stored against the ref the PROVIDER minted, not one we invented.

        The count of writes is asserted too: a create that recorded two
        bindings for one session would make `put_binding`'s write-once rule look
        enforced while it was being written twice under two refs.
        """
        response = self.router.route(router_mod.request_from_payload(payload(
            "agent.session.create", context_ref=CONTEXT_REF,
            params=_minimal_params("agent.session.create"),
            session_role="execution", bound_item_ref=ITEM)))
        self.assertTrue(response.ok, response.detail)
        self.assertEqual(len(self.bindings.puts), 1)
        record = self.bindings.puts[0]
        # The provider's ref, taken from its own answer — the whole reason the
        # store step is separate from the build step.
        self.assertEqual(record["session_ref"], response.result["session_ref"])
        self.assertEqual(record["session_role"], "execution")
        self.assertEqual(record["bound_item_ref"], ITEM)
        self.assertTrue(record["bound_item_ref_frozen"])

    def test_a_create_names_the_item_in_its_answer_so_the_caller_can_see_it(self):
        response = self.router.route(router_mod.request_from_payload(payload(
            "agent.session.create", context_ref=CONTEXT_REF,
            params=_minimal_params("agent.session.create"),
            session_role="execution", bound_item_ref=ITEM)))
        self.assertEqual(response.result["binding"],
                         {"session_role": "execution", "bound_item_ref": ITEM,
                          "state": "OPEN"})

    def test_a_coordinator_may_not_be_bound_to_an_item(self):
        """The refusal is typed, and nothing was started.

        A coordinator with one Item is an execution that has not said so, and
        the two behave differently under every rule that reads the field.
        """
        response = self.router.route(router_mod.request_from_payload(payload(
            "agent.session.create", context_ref=CONTEXT_REF,
            params=_minimal_params("agent.session.create"),
            session_role="coordinator", bound_item_ref=ITEM)))
        self.assertFalse(response.ok, response.result)
        self.assertEqual(response.reason, "INVALID_CONTEXT")
        self.assertEqual(self.service.calls, [], "a provider was asked to start a "
                                                 "session that could not be bound")

    def test_work_that_names_another_item_is_refused_in_the_mismatch_word(self):
        self.created()
        self.bindings.bind(self.ref, bound_item_ref=ITEM)
        response = self.send(work_item=OTHER_ITEM)
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "THREAD_ITEM_MISMATCH")
        self.assertEqual(self.service.names(), ["create"], response.detail)

    def test_the_same_thread_admits_its_own_item(self):
        """The positive control for the mismatch: same call, other Item.

        Without this, a gate that refused every send would pass the test above
        and would have closed the feature rather than the boundary.
        """
        self.created()
        self.bindings.bind(self.ref, bound_item_ref=ITEM)
        response = self.send(work_item=ITEM)
        self.assertTrue(response.ok, response.detail)
        self.assertIn("send", self.service.names())

    def test_a_thread_nobody_recorded_admits_nothing(self):
        """`refuse` mode: unrecorded is not unconstrained.

        An absent binding read as "no restriction" is exactly how LIT-0205's
        work entered LIT-0101's conversation: nothing had ever recorded where
        that conversation belonged, and nothing said so.
        """
        response = self.send()
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "INVALID_CONTEXT")
        self.assertEqual(self.service.calls, [])

    def test_a_closed_thread_refuses_new_work_in_its_own_word(self):
        self.created()
        self.bindings.bind(self.ref, bound_item_ref=ITEM, state="CLOSED")
        response = self.send()
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "THREAD_CLOSED_FOR_NEW_WORK")

    def test_a_thread_whose_item_was_delivered_refuses_new_work(self):
        self.created()
        self.bindings.items[ITEM] = ("DELIVERED", "SUCCESS")
        self.bindings.bind(self.ref, bound_item_ref=ITEM)
        response = self.send()
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "READ_ONLY_CLOSURE")

    def test_an_item_parked_on_a_person_does_not_close_its_thread(self):
        """NEEDS_HUMAN is a live Item, and the boundary must not read it as done.

        The distinction is the whole reason `READ_ONLY_ITEM_STATES` cites two
        states rather than treating "not ACTIVE" as finished.
        """
        self.created()
        self.bindings.items[ITEM] = ("NEEDS_HUMAN", None)
        self.bindings.bind(self.ref, bound_item_ref=ITEM)
        self.assertTrue(self.send(work_item=ITEM).ok)

    def test_a_resume_is_gated_with_a_send(self):
        """A resume starts a turn in the thread, so it is the same question."""
        self.created()
        self.bindings.bind(self.ref, bound_item_ref=ITEM, state="CLOSED")
        response = self.router.route(router_mod.request_from_payload(payload(
            "agent.session.resume", context_ref=CONTEXT_REF, session_ref=self.ref)))
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "THREAD_CLOSED_FOR_NEW_WORK")

    def test_reading_a_closed_thread_is_not_gated(self):
        """Deliberately ungated, and the reason is worth stating.

        Refusing `inspect` on a closed thread would make the closure record
        itself unreadable — the opposite of what recording it is for.
        """
        self.created()
        self.bindings.bind(self.ref, bound_item_ref=ITEM, state="CLOSED")
        response = self.router.route(router_mod.request_from_payload(payload(
            "agent.session.inspect", session_ref=self.ref)))
        self.assertTrue(response.ok, response.detail)
        self.assertIn("session_inspect", self.service.names())


# ---------------------------------------------------------------------------
# §8: three failures, three words.
# ---------------------------------------------------------------------------

class TestFailuresStayDistinct(unittest.TestCase):

    def test_an_undeployed_device_is_typed_unavailable_not_unknown(self):
        # §7: with no ServerOS node on the Windows machines the answer is a
        # typed unavailability, not "unknown device". The distinction matters
        # because one names a deployment task and the other names a mistake.
        response = deployed_router(RecordingService()).route(
            router_mod.request_from_payload(payload(
                MUTATING, FJ, context_ref=CONTEXT_REF,
                params=_minimal_params(MUTATING))))
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "NODE_RUNTIME_UNAVAILABLE")
        self.assertIn("no ServerOS node runtime is deployed", response.detail)
        for device in (FJ, DELL):
            self.assertIn(device, json.dumps(
                deployed_router(RecordingService()).evidence((device,))))

    def test_the_undeployed_answer_names_a_deployment_and_not_a_mistake(self):
        # The two must not share a word: one is repairable by enrolling or
        # deploying, the other by fixing the caller.
        response = deployed_router(RecordingService()).route(
            router_mod.request_from_payload(payload(READ_ONLY, DELL)))
        self.assertEqual(response.reason, "NODE_RUNTIME_UNAVAILABLE")
        with self.assertRaises(target.Rejected):
            deployed_router(RecordingService()).route(
                router_mod.request_from_payload(payload(READ_ONLY, "device:ghost")))

    def test_a_transport_failure_is_not_reported_as_a_session_state(self):
        node = fake_node_transport(mode="die")
        response = router(RecordingService(), node).route(
            router_mod.request_from_payload(payload(
                MUTATING, FJ, context_ref=CONTEXT_REF,
                params=_minimal_params(MUTATING), session_ref="session:x:y:z")))
        self.assertFalse(response.ok)
        self.assertIsNone(response.result)
        blob = json.dumps(response.show())
        for word in ("CLOSED", "closed", "STOPPED", "FAILED"):
            self.assertNotIn(word, blob,
                             "a delivery failure was reported as a session state")

    def test_the_transport_refusal_word_is_the_transport_refusal_word(self):
        node = fake_node_transport(mode="refuse", reason="TIMEOUT")
        response = router(RecordingService(), node).route(
            router_mod.request_from_payload(payload(
                MUTATING, FJ, context_ref=CONTEXT_REF,
                params=_minimal_params(MUTATING))))
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "TIMEOUT")
        self.assertEqual(response.detail, "refused by fixture")

    def test_a_provider_refusal_is_not_reworded_as_policy(self):
        # The Codex app-server provider declares session.stop UNSUPPORTED, for a
        # measured reason. The router must carry that word through, because
        # POLICY_DENIED would tell a person to ask for a permission that cannot
        # grant a capability.
        service = ok_service(support={
            "session.create": {"state": "SUPPORTED"},
            "session.inspect": {"state": "SUPPORTED"},
            "session.stop": {"state": "UNSUPPORTED",
                             "reason": "thread/archive is a reversible hide and "
                                       "thread/delete destroys history"},
        })
        response = router(service).route(router_mod.request_from_payload(
            payload("agent.session.stop", LOCAL, context_ref=CONTEXT_REF,
                    session_ref="session:a:b:c")))
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "ACTION_UNSUPPORTED")
        self.assertNotEqual(response.reason, "POLICY_DENIED")
        self.assertIn("thread/archive", response.detail)
        self.assertEqual([name for name, _ in service.calls], [],
                         "an unsupported verb reached the provider")

    def test_an_offline_device_is_unreachable_not_unknown(self):
        node = fake_node_transport(presence="KNOWN_OFFLINE")
        response = router(RecordingService(), node).route(
            router_mod.request_from_payload(payload(
                READ_ONLY, FJ, session_ref="session:a:b:c")))
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "DEVICE_UNREACHABLE")
        self.assertEqual(node.sent[-1]["op"], "node.probe")

    def test_an_unobserved_device_is_unknown_and_not_a_claim(self):
        # An enrolled device no transport covers. Nothing has established that
        # it is down, so DEVICE_UNREACHABLE would be a claim wider than the
        # evidence; the resolver says UNKNOWN and the router does not upgrade it.
        response = router(RecordingService()).route(router_mod.request_from_payload(
            payload(READ_ONLY, DELL, session_ref="session:a:b:c")))
        self.assertFalse(response.ok)
        self.assertIsNone(response.reason)
        self.assertEqual(response.resolved["status"], "UNKNOWN")
        self.assertIn("route", response.resolved["unevaluated"])
        self.assertFalse(response.dispatched)

    def test_an_unenrolled_device_is_malformed_not_unavailable(self):
        with self.assertRaises(target.Rejected):
            router(RecordingService()).route(router_mod.request_from_payload(
                payload(READ_ONLY, "device:nobody-registered-this")))

    def test_a_transport_that_cannot_serve_raises_rather_than_returning_false(self):
        node = router_mod.NodeControlTransport(device_refs=(FJ,))
        with self.assertRaises(router_mod.TransportFailure) as caught:
            node.send(router_mod.request_from_payload(payload(
                READ_ONLY, DELL)))
        self.assertEqual(caught.exception.reason, "DEVICE_UNREACHABLE")


# ---------------------------------------------------------------------------
# §7: forged remote integration, over a real wire.
# ---------------------------------------------------------------------------

class TestRemoteIntegration(unittest.TestCase):

    def tearDown(self):
        for attr in ("_node",):
            node = getattr(self, attr, None)
            if node is not None and node.proc.poll() is None:
                node.proc.kill()

    def _router(self, **kwargs):
        self._node = fake_node_transport(**kwargs)
        return router(RecordingService(), self._node)

    def test_a_remote_read_round_trips(self):
        r = self._router()
        response = r.route(router_mod.request_from_payload(payload(
            READ_ONLY, FJ, session_ref="session:a:b:c")))
        self.assertTrue(response.ok, response.detail)
        self.assertTrue(response.dispatched)
        self.assertEqual(response.route, "fleet")

    def test_the_node_sees_the_correlation_id_and_not_a_command(self):
        r = self._router()
        request = router_mod.request_from_payload(payload(
            READ_ONLY, FJ, session_ref="session:a:b:c"))
        r.route(request)
        seen = self._node.sent[-1]
        self.assertEqual(seen["correlation_id"], request.correlation_id)
        self.assertEqual(seen["device_ref"], FJ)
        self.assertNotIn("command", seen)
        self.assertNotIn("argv", seen)

    def test_the_node_sees_the_types_that_were_sent(self):
        # Serialization is the half an in-process fake skips.
        r = self._router()
        request = router_mod.request_from_payload(payload(
            MUTATING, FJ, context_ref=CONTEXT_REF, session_ref="session:a:b:c",
            params={"instruction": "carry me", "turn_idempotency_key": "t-9"},
            idempotency_key="k-9", timeout_s=30))
        r.route(request)
        seen = self._node.sent[-1]
        self.assertEqual(seen["params"], {"instruction": "carry me",
                                          "turn_idempotency_key": "t-9"})
        self.assertEqual(seen["idempotency_key"], "k-9")
        self.assertEqual(seen["timeout_s"], 30)
        self.assertEqual(seen["context_ref"], CONTEXT_REF)

    def test_the_resolution_travels_beside_the_request_and_not_inside_it(self):
        r = self._router()
        r.route(router_mod.request_from_payload(payload(
            READ_ONLY, FJ, session_ref="session:a:b:c")))
        seen = self._node.sent[-1]
        self.assertEqual(seen["resolved"]["status"], "AVAILABLE")
        self.assertNotIn("resolved", seen["params"])
        self.assertNotIn("_target", json.dumps(seen["params"]))

    def test_a_node_cannot_answer_a_probe_with_a_new_request(self):
        # Found while writing this suite: the transport's inputs were spread
        # last into the resolver request, so a forged node could return
        # `{"action": null}` from a `resolve.inputs` probe and choose what it
        # was being asked to do. The whitelist is the fix.
        def hostile(payload: dict) -> dict:
            if payload.get("op") == "resolve.inputs":
                return {"ok": True, "result": {"action": None, "device_ref": DELL,
                                               "providers": [], "instances": []}}
            return {"ok": True, "result": {"session_ref": "session:hostile:1"}}

        node = router_mod.NodeControlTransport(connector=hostile, device_refs=(FJ, DELL))
        node.reachable = lambda device_ref: {  # the node claims to be up
            "presence": {"state": "KNOWN_ONLINE"},
            "node_runtime": {"state": "RUNNING"},
            "source": "hostile"}
        response = router(RecordingService(), node).route(
            router_mod.request_from_payload(payload(
                READ_ONLY, FJ, session_ref="session:a:b:c")))
        # The refusal is the resolver's, about providers, for the device the
        # caller named — not one about a null action on the Dell.
        self.assertFalse(response.ok)
        self.assertEqual(response.action, READ_ONLY)
        self.assertEqual(response.device_ref, FJ)
        self.assertEqual(response.verb, "session.inspect")
        self.assertNotIn(DELL, json.dumps(response.show()))

    def test_a_node_that_never_answers_is_a_typed_failure(self):
        node = fake_node_transport(mode="hang")
        self._node = node
        r = router(RecordingService(), node)
        with self.assertRaises(transport.TransportUnavailable):
            node.send(router_mod.request_from_payload(payload(
                READ_ONLY, FJ, session_ref="session:a:b:c")))

    def test_a_node_that_answers_with_garbage_is_a_transport_failure(self):
        r = self._router(mode="badjson")
        response = r.route(router_mod.request_from_payload(payload(
            READ_ONLY, FJ, session_ref="session:a:b:c")))
        self.assertFalse(response.ok)
        # The connector raised TransportUnavailable, which is not a
        # TransportFailure — the router must not dress it as a reason from the
        # closed vocabulary that nobody produced.
        self.assertIn(response.reason, router_mod.reason_vocabulary())

    def test_the_loopback_refuses_to_serve_a_remote_device(self):
        node = router_mod.LocalLoopbackTransport(
            device_ref=LOCAL, service=RecordingService(),
            bindings=threads.MemoryBindings(unknown=threads.ASSUME_BOUND))
        with self.assertRaises(router_mod.TransportFailure) as caught:
            node.send(router_mod.request_from_payload(payload(READ_ONLY, FJ)))
        self.assertEqual(caught.exception.reason, "DEVICE_UNREACHABLE")

    def test_a_node_transport_does_not_substitute_a_local_answer(self):
        # The substitution this release forbids: remote request, no connector,
        # answered from here. It must refuse instead.
        node = router_mod.NodeControlTransport(connector=None, device_refs=(FJ,))
        with self.assertRaises(router_mod.TransportFailure) as caught:
            node.send(router_mod.request_from_payload(payload(
                READ_ONLY, FJ, session_ref="session:a:b:c")))
        self.assertEqual(caught.exception.reason, "NODE_RUNTIME_UNAVAILABLE")


# ---------------------------------------------------------------------------
# Idempotency, scoped honestly.
# ---------------------------------------------------------------------------

class TestIdempotency(unittest.TestCase):

    def test_a_retried_mutation_is_not_a_second_effect(self):
        svc = RecordingService()
        r = router(svc)
        request = payload(MUTATING, LOCAL, context_ref=CONTEXT_REF,
                          params=_minimal_params(MUTATING),
                          idempotency_key="same-intent")
        first = r.route(router_mod.request_from_payload(request))
        second = r.route(router_mod.request_from_payload(request))
        self.assertTrue(first.ok)
        self.assertTrue(second.ok)
        self.assertEqual(svc.names(), ["send"])
        self.assertTrue(second.result.get("idempotent_replay"))
        self.assertFalse(second.dispatched)

    def test_a_read_is_never_replayed_from_memory(self):
        # A replayed read answers with an observation that has aged. Two reads
        # must both reach the service.
        svc = RecordingService()
        r = router(svc)
        request = payload(READ_ONLY, LOCAL, session_ref="session:a:b:c",
                          idempotency_key="same-read")
        r.route(router_mod.request_from_payload(request))
        r.route(router_mod.request_from_payload(request))
        self.assertEqual(svc.names(), ["session_inspect", "session_inspect"])

    def test_the_same_key_on_a_different_intent_is_a_caller_bug(self):
        r = router(RecordingService())
        r.route(router_mod.request_from_payload(payload(
            MUTATING, LOCAL, context_ref=CONTEXT_REF,
            params=_minimal_params(MUTATING), idempotency_key="k")))
        with self.assertRaises(target.Rejected) as caught:
            r.route(router_mod.request_from_payload(payload(
                "agent.session.resume", LOCAL, context_ref=CONTEXT_REF,
                idempotency_key="k")))
        self.assertIn("one intent", str(caught.exception))

    def test_durability_is_declared_and_is_not_claimed_to_be_more(self):
        declared = router_mod.contract()["idempotency"]
        self.assertEqual(declared["scope"], "in-process, per router instance")
        self.assertIn("NOT durable", declared["durability"])


# ---------------------------------------------------------------------------
# §9: handoff. The order is the safety property.
# ---------------------------------------------------------------------------

class TestHandoff(unittest.TestCase):

    def _source(self):
        return RecordingService()

    def test_route_refuses_to_dispatch_a_server_side_procedure(self):
        # Dispatching it would ask a provider for a verb the session contract
        # reserves, and the ACTION_UNSUPPORTED it returned would be true for a
        # reason that is not the real one.
        service = RecordingService()
        response = router(service).route(router_mod.request_from_payload(payload(
            "agent.session.handoff", LOCAL, context_ref=CONTEXT_REF,
            session_ref="session:a:b:c")))
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "ACTION_UNSUPPORTED")
        self.assertIn("handoff()", response.detail)
        self.assertEqual(service.names(), [])

    def test_a_handoff_to_an_unavailable_target_keeps_the_source(self):
        source = self._source()
        r = deployed_router(source)
        outcome = r.handoff(router_mod.request_from_payload(payload(
            "agent.session.handoff", LOCAL, context_ref=CONTEXT_REF,
            session_ref="session:a:b:c",
            params={"checkpoint": {"summary": "state"},
                    "to_device_ref": FJ, "target_action": "agent.session.resume",
                    "release_source": True})))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "NODE_RUNTIME_UNAVAILABLE")
        self.assertTrue(outcome.source_preserved)
        self.assertNotIn("stop", source.names(),
                         "the source was stopped although the target never came up")
        self.assertEqual(outcome.completed, ("checkpoint",))

    def test_the_source_is_not_released_by_default(self):
        # The target here is the local device, so the "target came up" path is
        # genuinely exercised rather than stubbed past.
        source = self._source()
        r = router(source)
        outcome = r.handoff(
            router_mod.request_from_payload(payload(
                "agent.session.handoff", LOCAL, context_ref=CONTEXT_REF,
                session_ref="session:a:b:c",
                params={"checkpoint": {"summary": "state"}})),
            create_request=router_mod.request_from_payload(payload(
                "agent.session.result", LOCAL, session_ref="session:a:b:c")))
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertNotIn("stop", source.names())
        self.assertTrue(outcome.source_preserved)
        self.assertIn("release_source", outcome.detail["why"])

    def test_a_handoff_with_nothing_to_hand_over_is_refused(self):
        source = self._source()
        r = deployed_router(source)
        outcome = r.handoff(router_mod.request_from_payload(payload(
            "agent.session.handoff", LOCAL, context_ref=CONTEXT_REF,
            session_ref="session:a:b:c",
            params={"to_device_ref": FJ})))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "INVALID_CONTEXT")
        self.assertEqual(outcome.completed, ())
        self.assertEqual(source.names(), [])
        self.assertIn("nothing to hand over", outcome.detail["why"])

    def test_the_declared_step_order_is_the_implemented_order(self):
        declared = router_mod.contract()["handoff"]["steps"]
        self.assertEqual(tuple(declared), router_mod.HANDOFF_STEPS)

    def test_a_release_the_policy_gate_refuses_leaves_the_source_running(self):
        # `session.stop` is human-gated, and the handoff carries a context but
        # no approval. The target is up and the source must stay up: a handoff
        # that treated its own authority as sufficient to stop a session would
        # be the §22 gate bypassed by a compound action.
        source = self._source()
        r = router(source)
        outcome = r.handoff(
            router_mod.request_from_payload(payload(
                "agent.session.handoff", LOCAL, context_ref=CONTEXT_REF,
                session_ref="session:a:b:c",
                params={"checkpoint": {"summary": "state"},
                        "release_source": True,
                        "target_action": "agent.session.result"})),
            create_request=router_mod.request_from_payload(payload(
                "agent.session.result", LOCAL, session_ref="session:a:b:c")))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "APPROVAL_REQUIRED")
        self.assertEqual(outcome.stopped_at, "source_released")
        self.assertTrue(outcome.source_preserved)
        self.assertNotIn("stop", source.names())
        self.assertIn("both exist", outcome.detail["why"])

    def test_a_release_that_the_provider_refuses_leaves_the_source_running(self):
        # The same shape one layer down: the provider itself declines. A refused
        # stop is reported as the provider's refusal and the source is untouched
        # — "the stop did not happen" and "the session ended anyway" are
        # different sentences and only one of them is true here.
        source = ok_service(support={
            "session.create": {"state": "SUPPORTED"},
            "session.inspect": {"state": "SUPPORTED"},
            "session.result": {"state": "SUPPORTED"},
            "session.stop": {"state": "UNSUPPORTED",
                             "reason": "thread/archive is not a stop"},
        })
        r = router(source)
        outcome = r.handoff(
            router_mod.request_from_payload(payload(
                "agent.session.handoff", LOCAL, context_ref=CONTEXT_REF,
                session_ref="session:a:b:c",
                params={"checkpoint": {"summary": "state"},
                        "release_source": True,
                        "target_action": "agent.session.result"})),
            create_request=router_mod.request_from_payload(payload(
                "agent.session.result", LOCAL, session_ref="session:a:b:c")))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "ACTION_UNSUPPORTED")
        self.assertTrue(outcome.source_preserved)
        self.assertNotIn("stop", source.names())


# ---------------------------------------------------------------------------
# Signatures and identities.
# ---------------------------------------------------------------------------

class TestRouterIdentity(unittest.TestCase):

    def test_the_router_will_not_derive_its_own_device(self):
        with self.assertRaises(TypeError):
            router_mod.FleetAgentControlRouter(transports=[])

    def test_a_hostname_is_not_a_device_ref(self):
        with self.assertRaises(target.TargetError):
            router_mod.FleetAgentControlRouter(local_device_ref="beijing-host")

    def test_the_loopback_serves_one_named_device(self):
        with self.assertRaises(target.TargetError):
            router_mod.LocalLoopbackTransport(
                device_ref="localhost", service=RecordingService(),
                bindings=threads.MemoryBindings(unknown=threads.ASSUME_BOUND))

    def test_the_router_holds_no_session_state(self):
        # §24: transport failure must not become a session state, and the way to
        # keep that true is for the router to have nowhere to put one.
        r = router(RecordingService())
        self.assertEqual([name for name in vars(r) if "session" in name.lower()], [])

    def test_the_evidence_names_only_the_device_asked_about(self):
        r = router(RecordingService())
        evidence = r.evidence((LOCAL,))
        self.assertEqual(set(evidence["presence"]), {LOCAL})
        self.assertEqual(set(evidence["node_runtime"]), {LOCAL})
        self.assertNotIn(FJ, json.dumps(evidence))

    def test_an_unobserved_device_produces_no_evidence(self):
        node = router_mod.NodeControlTransport(device_refs=())
        reported = node.reachable(DELL)
        self.assertNotIn("presence", reported)
        self.assertNotIn("node_runtime", reported)

    def test_route_rejects_a_raw_payload(self):
        with self.assertRaises(target.Rejected):
            router(RecordingService()).route(payload(READ_ONLY))


if __name__ == "__main__":
    unittest.main(verbosity=2)
