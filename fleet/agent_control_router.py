#!/usr/bin/env python3
"""Route one Agent Control request to the machine that should serve it.

WHY THIS FILE EXISTS AT ITS DECLARED PATH
-----------------------------------------
`abilities/target-contract.yaml` has named `fleet/agent_control_router.py` since
§218, and until this file was written nothing was there. That contract also
declared what this module must NOT do — "this file resolves an address; sending
something to it is a different module" — and what it must reuse: the reason
vocabulary, so that a caller refusing outside the list is refusing in a language
nobody else speaks.

So this module owns exactly one question: given a resolved target, which
transport carries the request, and what does it answer. It resolves nothing
itself. `abilities/target.py` decides the target, `runtime/agent_sessions.py`
decides whether the verb is supported and whether this actor may perform it, and
`fleet/transport.py` owns what a device is. Anything this file re-decided would
be a second answer to a question that already has one.

THE THREE WAYS A ROUTED CALL GOES WRONG, KEPT APART
---------------------------------------------------
§8 names the confusion, and each sentence below is one somebody would otherwise
write:

    the transport failed        -> DEVICE_UNREACHABLE or NODE_RUNTIME_UNAVAILABLE
    NOT "the session closed"    nothing was learned about the session

    the provider refuses        -> ACTION_UNSUPPORTED or READINESS_UNKNOWN
    NOT "policy denied"         nobody made a policy decision

    the request was refused     -> POLICY_DENIED / APPROVAL_REQUIRED / ...
    NOT "the provider is broken"

A transport that cannot deliver a request has learned nothing about the session
on the other side. Reporting such a session as CLOSED is the failure this whole
release is about one plane over: a claim wider than its evidence. The route
result therefore never carries a session state it did not receive.

WHAT A REQUEST MAY NOT CONTAIN
------------------------------
No `remote.shell`, no command string, no argv, and no credential. The request is
a closed action name plus typed parameters, and its parameters are checked with
`fleet/transport.py#validate_binding` — the same recursive rejection of credential
keys that already guards a device binding. Reusing it rather than writing a
second list is deliberate: two lists of credential field names is one list and
one place for the second to fall behind.
"""
from __future__ import annotations

import abc
import importlib.util
import json
import os
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
_ABILITIES = _ROOT / "abilities"
_RUNTIME = _ROOT / "runtime"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


transport = _load("serveros_fleet_transport", _HERE / "transport.py")
target = _load("serveros_abilities_target", _ABILITIES / "target.py")
semantics = _load("serveros_runtime_agent_sessions", _RUNTIME / "agent_sessions.py")
actor = _load("_serveros_context_actor", _ROOT / "context" / "actor.py")
# §17. The same module object the daemon loads, under the same synthetic name,
# and that is load-bearing rather than tidy: `ThreadRefused` is caught across
# this boundary, and two module instances would be two exception classes with
# one name — a `except` clause that silently stops matching.
coordination = _load("serveros_runtime_coordination", _RUNTIME / "coordination.py")
# #245 / S2. The dispatch-attempt kernel: intent identity, phases and the
# reconcile decision. Loaded under the same synthetic name everywhere, so the
# `AttemptRefused` class caught here is the one the store raises.
dispatch_attempts = _load("serveros_runtime_dispatch_attempts",
                          _RUNTIME / "dispatch_attempts.py")

SCHEMA = "serveros/agent-control-router.v1"
CONTRACT = _HERE / "agent-control-contract.yaml"

Rejected = target.Rejected
TargetError = target.TargetError


def contract() -> dict:
    import yaml
    data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TargetError(f"{CONTRACT} must contain a mapping")
    return data


def control_actions() -> dict:
    """`agent.session.create` -> `session.create`, read from the contract.

    The control vocabulary and the semantic one are two names for the same
    eight verbs, and the mapping is declared in one place rather than spelled at
    each call site. A control name with no semantic verb behind it is a name
    that would route to nothing.
    """
    return {row["control"]: row["verb"] for row in contract()["actions"]["values"]}


def action_params() -> dict:
    """The required parameter set per control action."""
    return {row["control"]: tuple(row["params"])
            for row in contract()["actions"]["values"]}


def action_optional() -> dict:
    """The rest of what each action accepts. Together with `action_params`,
    this is the closed set — an action may carry nothing else."""
    return {row["control"]: tuple(row.get("optional") or ())
            for row in contract()["actions"]["values"]}


def action_coordination() -> dict:
    """Which §17 envelope fields each control action may carry.

    Closed per action for the same reason `params` is, and the reason is not
    tidiness. `bound_item_ref` on a `session.send` would be a caller restating
    what a thread is for — and since the thread's Item is immutable, a router
    that accepted the field and ignored it would be accepting a declaration it
    has no reader for, which is this repository's most expensive recurring
    shape. A caller that wants a different Item wants a different thread, and
    the only action that creates one is `session.create`.
    """
    return {row["control"]: tuple(row.get("coordination") or ())
            for row in contract()["actions"]["values"]}


def server_side_actions() -> frozenset:
    """Actions that name a server-side procedure rather than a provider verb.

    `route()` refuses these. Dispatching one would ask a provider for a verb the
    session contract reserves and no provider implements, and the refusal it
    returned would be true for a reason that is not the real one.
    """
    return frozenset(row["control"] for row in contract()["actions"]["values"]
                     if row.get("server_side"))


def reason_vocabulary() -> tuple[str, ...]:
    """The one list. Owned by `abilities/target-contract.yaml`, not repeated here."""
    return target.reason_vocabulary()


RUNTIME_CONTRACT = _ABILITIES / "agent-runtime-contract.yaml"


def runtime_record(runtime_id: str) -> dict | None:
    """The device-independent runtime record the resolver's adapter step reads.

    Which runtimes exist on a machine is a device fact and lives in
    `fleet/registry.yaml`. What a runtime can consume is a runtime fact and
    lives here. The resolver needs the second to answer `AGENT_UNSUPPORTED`, and
    returning `None` for an unknown id is deliberate: the step then reports
    UNEVALUABLE rather than agreeing on a runtime nobody declared.

    This is only the FAMILY half. Whether this device runs that family, on which
    surface, at which version, with which features — the half that decides
    whether a call can be made HERE — is a binding, and it is resolved by
    `abilities/runtime_binding.py` and nowhere else. Keeping the two apart is
    the point of the split: "Codex can consume an MCP server" and "this machine
    has a Codex that can" were the same sentence for as long as the registry
    carried `agents:` and nothing more.

    `consumable_provider_kinds` is looked up per runtime. It was one list under
    one key, which made `parity.compared_fields` a comparison between a value
    and itself; the lookup below is what makes the remaining compared field name
    two values that can differ.
    """
    import yaml
    data = yaml.safe_load(RUNTIME_CONTRACT.read_text(encoding="utf-8")) or {}
    if runtime_id not in tuple(data.get("ids") or ()):
        return None
    kinds = (data.get("capabilities") or {}).get("consumable_provider_kinds") or {}
    return {
        "id": runtime_id,
        "consumable_provider_kinds": list(kinds.get(runtime_id) or ()),
    }


def provider_rows(providers) -> list[dict]:
    """Provider rows for the resolver, from live provider objects.

    `kind` is carried only when a provider declares one. A default would be an
    invented capability fact: the resolver's adapter step treats an absent kind
    as no incompatibility, and a default would have every provider claiming a
    kind it may not have.
    """
    rows: list[dict] = []
    for provider in providers:
        provider_id = getattr(provider, "provider_id", None)
        if not provider_id:
            continue
        row: dict = {"id": str(provider_id)}
        kind = getattr(provider, "provider_kind", None)
        if kind:
            row["kind"] = str(kind)
        preference = getattr(provider, "preference", None)
        if preference is not None:
            row["preference"] = int(preference)
        adapters = getattr(provider, "runtime_adapters", None)
        if adapters:
            row["adapters"] = [str(a) for a in adapters]
        rows.append(row)
    return rows


def provider_action_support(provider) -> dict:
    """A provider's own capability table, in the shape the resolver reads.

    Returns `{}` for a provider that declares none, which the resolver reports
    as UNKNOWN — "nobody asked" — and refuses with ACTION_UNSUPPORTED while
    keeping the distinction in `support.state`. Assembling a table here for a
    provider that published none would be this module answering a capability
    question on the provider's behalf.
    """
    declared = getattr(provider, "actions", None)
    if declared is None:
        return {}
    try:
        table = declared()
    except TypeError:
        # A table that needs inputs the caller has not supplied. Refusing to
        # guess which inputs is the honest answer; a defaulted call would
        # produce a capability verdict for a configuration nobody chose.
        return {}
    return {str(k): v for k, v in dict(table or {}).items()}


def _mutating(verb: str) -> bool:
    """Does this verb need a trusted context? Read from the session contract.

    `read_only` is the class that does not; `delegation` and `human_gated` both
    do. Deriving it rather than listing it means a verb that changes class in
    `runtime/agent-session-contract.yaml` changes here without an edit.
    """
    return semantics.admission(verb, {}).get("class") != "read_only"


# The only keys a transport may contribute to a resolution. Everything the
# resolver takes beyond these describes the REQUEST, and a transport that could
# supply those would be answering on the caller's behalf.
TRANSPORT_INPUT_KEYS = ("providers", "instances", "runtime_record")


# ---------------------------------------------------------------------------
# The typed request and response. No free-form field crosses either boundary.
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AgentControlRequest:
    """One control request, addressed to one device.

    `correlation_id` and `idempotency_key` are different things and neither is
    the other: the first identifies the call so that a log line on each machine
    can be joined, the second identifies the intended EFFECT so that a retry of
    the same intent is not a second effect. A correlation id reused as an
    idempotency key would make every retry a distinct intent; an idempotency key
    reused as a correlation id would make two different calls indistinguishable
    in a trace.
    """

    action: str
    device_ref: str
    agent_runtime: str
    params: Mapping = field(default_factory=dict)
    context_ref: str | None = None
    approvals: tuple = ()
    workspace: str | None = None
    work_item: str | None = None
    session_ref: str | None = None
    # §17. What the thread this request creates is FOR. Empty on every action
    # but `session.create`, and `action_coordination` is what says so.
    session_role: str | None = None
    bound_item_ref: str | None = None
    correlation_id: str = ""
    idempotency_key: str | None = None
    timeout_s: float = 60.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "params", dict(self.params or {}))
        # Approval records are presented, never authored here. §22 binds an
        # approval to one actor, action, device, target and request fingerprint;
        # a router that filled in a field would be completing a binding on
        # somebody's behalf, which is the one thing the binding exists to stop.
        object.__setattr__(self, "approvals", tuple(self.approvals or ()))
        if not self.correlation_id:
            object.__setattr__(self, "correlation_id", new_correlation_id())

    @property
    def verb(self) -> str:
        return control_actions()[self.action]

    @property
    def runtime_id(self) -> str:
        """`agent-runtime:codex` -> `codex`. The name a runtime record is keyed by."""
        return target.parse_runtime_ref(self.agent_runtime)

    def resolver_request(self) -> dict:
        """This request, in the shape `abilities/target.py#resolve` takes.

        The action handed to the resolver is the SEMANTIC verb: the resolver
        asks a provider what it supports, and providers declare `session.create`
        — not the control plane's spelling of it.
        """
        return {
            "device_ref": self.device_ref,
            "agent_runtime": self.agent_runtime,
            "action": self.verb,
            "session_ref": self.session_ref,
            "workspace": self.workspace,
            "work_item": self.work_item,
            "context_ref": self.context_ref,
        }

    def wire(self) -> dict:
        """The payload a node receives. Closed, and free of credentials."""
        return {
            "schema": SCHEMA,
            "action": self.action,
            "device_ref": self.device_ref,
            "agent_runtime": self.agent_runtime,
            "params": dict(self.params),
            "context_ref": self.context_ref,
            "workspace": self.workspace,
            "work_item": self.work_item,
            "session_ref": self.session_ref,
            "session_role": self.session_role,
            "bound_item_ref": self.bound_item_ref,
            "correlation_id": self.correlation_id,
            "idempotency_key": self.idempotency_key,
            "timeout_s": self.timeout_s,
        }


def new_correlation_id() -> str:
    return "acr-" + uuid.uuid4().hex[:24]


@dataclass(frozen=True)
class AgentControlResponse:
    """The answer, whether it is a result or a refusal. Both are answers."""

    ok: bool
    action: str
    verb: str
    device_ref: str
    correlation_id: str
    reason: str | None = None
    detail: str | None = None
    route: str | None = None
    target: dict | None = None
    resolved: dict | None = None
    result: dict | None = None
    provenance: tuple = ()
    retryable: bool = False
    dispatched: bool = False

    def show(self) -> dict:
        return {
            "schema": SCHEMA,
            "ok": self.ok,
            "action": self.action,
            "verb": self.verb,
            "device_ref": self.device_ref,
            "correlation_id": self.correlation_id,
            "reason": self.reason,
            "detail": self.detail,
            "route": self.route,
            "target": self.target,
            "result": self.result,
            "provenance": list(self.provenance),
            "retryable": self.retryable,
            "dispatched": self.dispatched,
        }


class TransportFailure(Exception):
    """A transport could not deliver. Carries a reason from the closed list.

    Kept as an exception rather than a return value because a transport that
    returns `{"ok": false}` for "I could not reach the machine" and for "the
    machine answered that the verb is unsupported" makes those two the same
    thing at every call site that forgets to look.
    """

    def __init__(self, reason: str, detail: str, *, retryable: bool = False) -> None:
        if reason not in reason_vocabulary():
            raise TargetError(
                f"{reason!r} is not in the reason vocabulary; a transport refusing "
                f"in a word of its own is refusing outside the one list")
        self.reason = reason
        self.detail = detail
        self.retryable = retryable
        super().__init__(f"{reason}: {detail}")


# ---------------------------------------------------------------------------
# Transports
# ---------------------------------------------------------------------------

class AgentControlTransport(abc.ABC):
    """Carries a typed request to a device and returns a typed answer.

    Deliberately narrow. There is no `execute`, no `run`, no command parameter
    and no way for a caller to reach one: §7 forbids `remote.shell` and an
    arbitrary command string, and the way to forbid those is to leave nowhere
    for them to be passed. A transport that grew a general-purpose `call(method,
    payload)` would be a shell with a schema on top.
    """

    name = "abstract"

    @abc.abstractmethod
    def reachable(self, device_ref: str) -> dict:
        """What this transport has observed about reaching `device_ref`.

        Returns `{"presence": {...}, "node_runtime": {...}, "source": ...}`
        shaped for `abilities/target.py`'s evidence fields. Absent keys mean
        unobserved, never "fine" — the resolver turns an unobserved device into
        UNKNOWN, and a transport that reported KNOWN_ONLINE for a device it has
        never contacted would defeat the whole resolver.
        """

    def resolve_inputs(self, device_ref: str, runtime_id: str) -> dict:
        """What this transport can establish about a target, in resolver shape.

        Returns the `providers` / `instances` / `runtime_record` inputs the
        resolver's provider, adapter, support and readiness steps read. The
        default is empty, and empty does not mean "fine": each of those steps
        reports UNEVALUABLE when its input is missing, so an unasked provider
        becomes an UNKNOWN target rather than an assumed one.

        This is deliberately NOT abstract. A transport that cannot answer it has
        said something true — nobody asked — and forcing every transport to
        implement it would produce implementations that return `{}` with a
        longer docstring.
        """
        return {}

    @abc.abstractmethod
    def send(self, request: AgentControlRequest, *, resolved: dict | None = None) -> dict:
        """Deliver a request. Raises `TransportFailure`, never returns one.

        `resolved` is the resolution record the router produced, passed beside
        the request rather than inside its `params`. The loopback needs it to
        know which provider was chosen; a node needs it to serve the request
        under the same resolution the caller paid for. Keeping it out of
        `params` is what lets `params` remain exactly what the caller sent, so
        neither the wire nor a test has to subtract the router's own bookkeeping
        to see the caller's request.
        """


class Unbound:
    """A bindings handle for a call site that has no store. Permits nothing.

    It exists so that the loopback's requirement can be satisfied without a
    filesystem, and it is named for what it means rather than for what it is:
    a site holding one of these will refuse every turn into every thread, and
    that is the correct behaviour for a process that cannot state what a thread
    is for. The alternative — a handle that answers "no binding, carry on" —
    is the permissive default this whole section removes.
    """

    def get(self, ref):  # noqa: D102 - the protocol, in four lines
        return {"binding": None, "item": None, "because": "no bindings handle"}

    def put(self, record):  # noqa: D102
        raise TargetError(
            "this call site has no bindings store, so a thread it creates "
            "cannot be recorded. Construct the loopback with a real handle")


class LocalLoopbackTransport(AgentControlTransport):
    """The device this process runs on, served without leaving the process.

    This is the only transport this release can actually deploy, and it is a
    real one rather than a mock: it drives the same `SessionService` the node's
    own MCP surface calls, so a request routed locally and a request made
    through the MCP take the identical path through support, readiness and
    admission. A loopback that shortcut those would make the local case the one
    case §22's guarantees were never exercised against.
    """

    name = "local-loopback"

    def __init__(self, *, device_ref: str, service, bindings=None) -> None:
        if not str(device_ref).startswith("device:"):
            raise TargetError(
                f"{device_ref!r} is not a device ref; the loopback answers for one "
                f"device and a hostname is not the identity Fleet uses")
        self.device_ref = str(device_ref)
        self.service = service
        # §17. Required, not defaulted, and the absence of a default is the
        # whole design. A loopback that could be built without a bindings
        # handle would dispatch every create and every send with no thread
        # check at all, and it would do so silently — which is how the failure
        # this section was written for happened in the first place. A caller
        # that genuinely has no store must pass one that refuses.
        if bindings is None:
            raise TargetError(
                "the loopback requires a bindings handle: without one it would "
                "dispatch work into threads whose purpose ServerOS never "
                "recorded, which is the case §17 exists for. Pass "
                "`bindings=Unbound()` if the call site truly has no store — it "
                "refuses every thread rather than permitting every one")
        self.bindings = bindings

    # -- §17: the thread this work is going into ---------------------------

    def _admit(self, request: AgentControlRequest) -> dict:
        """Whether this call may put work into the thread it is addressed to.

        Consulted for the actions that START a turn — `session.send` and
        `session.resume`. It is deliberately NOT consulted for `result`,
        `inspect`, `interrupt` or `stop`: those read or terminate work that is
        already in the thread, and refusing to let somebody read a closed
        thread would make the closure itself unreadable — which is the opposite
        of what a closure record is for.

        One read, not two. The binding and the bound Item's state come back
        from a single call, because a caller that fetched them separately would
        be joining two records taken at two moments and the refusal it makes
        would be about neither of them. The current writer for the thread's
        write scope travels in the SAME read (#237 B1): the claim on the binding
        and the authoritative current writer must be joined at one moment, or
        the gate decides on two records that were never both true.
        """
        read = self.bindings.get(request.session_ref) or {}
        try:
            return coordination.admit_work(
                binding_record=read.get("binding"),
                work_item=request.work_item,
                item_state=(read.get("item") or {}).get("state"),
                outcome=(read.get("item") or {}).get("outcome"),
                # A handle that cannot supply a current writer leaves this None,
                # and `admit_work` then refuses any thread that CLAIMS write
                # authority rather than admitting it unchecked: "no verifiable
                # source" is a refusal, not a pass. A thread that claims nothing
                # is unaffected, which is what keeps every pre-existing thread
                # working.
                current_writer=read.get("current_writer"),
                # The WORK ENGINE lease, when the handle can supply it. Passed
                # through rather than re-derived: the router does not hold the
                # lease store, and a second derivation of "who owns this scope"
                # is the second answer #237 is about. When it is supplied it is
                # the ONLY authority the kernel checks the claim against.
                authority=read.get("write_authority"),
                now=_writer_now())
        except coordination.ThreadRefused as exc:
            # Translated rather than propagated, and translated here rather
            # than caught in `route()`: `TransportFailure` validates the reason
            # against the closed vocabulary at construction, so this line is
            # also the assertion that the four words §17 refuses in are words a
            # caller has.
            raise TransportFailure(exc.reason, exc.detail) from exc

    def _bind(self, request: AgentControlRequest) -> dict:
        """The record of what the thread being created is for.

        Built and validated BEFORE the provider is asked, so that a create
        which cannot be bound never starts a provider session. The order
        matters: validating afterwards would leave a live session with no
        recorded purpose, and every later turn into it would refuse with
        INVALID_CONTEXT — a working session that reports itself as unbound.
        """
        try:
            return coordination.binding(
                session_role=request.session_role,
                bound_item_ref=request.bound_item_ref,
                work_item=request.work_item,
                device_ref=request.device_ref)
        except coordination.ThreadRefused as exc:
            raise TransportFailure(exc.reason, exc.detail) from exc

    def _record(self, request: AgentControlRequest, answer: dict, *,
                binding: dict) -> dict:
        """Store the binding against the ref the PROVIDER minted.

        The ref is not known until create has answered, which is why this is a
        second step rather than part of `_bind`. The binding itself is a
        parameter rather than built here, so that the call site shows the order:
        built first, provider asked second, stored third.

        If storing fails, the thread is unbound and every later turn into it
        refuses — the failure is loud and on the safe side, and it is worth
        saying which side that is: an unrecorded binding refuses work, it does
        not permit it.
        """
        ref = (answer or {}).get("session_ref")
        if not ref:
            raise TransportFailure(
                "INVALID_CONTEXT",
                "the provider answered a create with no session_ref, so the "
                "binding that says what this thread is for cannot be stored. "
                "Refusing rather than returning a session whose purpose "
                "ServerOS would not be able to state")
        record = dict(binding)
        record["session_ref"] = str(ref)
        stored = self.bindings.put(record)
        answer = dict(answer)
        answer["binding"] = {"session_role": stored.get("session_role"),
                             "bound_item_ref": stored.get("bound_item_ref"),
                             "state": stored.get("state")}
        return answer

    def reachable(self, device_ref: str) -> dict:
        if device_ref != self.device_ref:
            # Not "offline": unobserved. This transport cannot see other
            # machines and says so by omission rather than by guessing.
            return {"source": self.name}
        now = _now()
        return {
            "presence": {"state": "KNOWN_ONLINE", "observed_at": now,
                         "provenance": self.name},
            "node_runtime": {"state": "RUNNING", "observed_at": now,
                             "provenance": self.name},
            "source": self.name,
        }

    def send(self, request: AgentControlRequest, *, resolved: dict | None = None) -> dict:
        if request.device_ref != self.device_ref:
            raise TransportFailure(
                "DEVICE_UNREACHABLE",
                f"the local loopback serves {self.device_ref} only; "
                f"{request.device_ref} is not this machine")
        return self._dispatch(request, resolved)

    def resolve_inputs(self, device_ref: str, runtime_id: str) -> dict:
        """Provider rows and readiness records, from the live local service.

        The instance is the service's readiness record with the provider's own
        capability table attached. Both halves come from the provider: the table
        from `actions()`, the runtime facts from `observe()`. Assembling either
        here would make this module the author of a capability claim.

        A provider whose readiness call raises contributes no instance rather
        than a failed one. The resolver reports a missing instance as
        PROVIDER_UNAVAILABLE with the reason "no instance is registered", which
        is a weaker claim than it could make — and a weaker claim is the correct
        direction to be wrong in when the alternative is inventing a status.
        """
        if device_ref != self.device_ref:
            return {}
        providers = list((getattr(self.service, "providers", None) or {}).values())
        instances = []
        for provider in providers:
            try:
                record = dict(self.service.readiness(provider.provider_id))
            except Exception:
                continue
            record["action_support"] = provider_action_support(provider)
            instances.append(record)
        return {
            "providers": provider_rows(providers),
            "instances": instances,
            "runtime_record": runtime_record(runtime_id),
        }

    def _dispatch(self, request: AgentControlRequest, resolved: dict | None = None) -> dict:
        service = self.service
        p = dict(request.params)
        if request.verb == "session.create":
            # §17. Bound BEFORE the provider is asked, and the order is the
            # point rather than a detail: `service.create` starts a session, so
            # a create that validated afterwards would leave a live provider
            # session with no recorded purpose — and every later turn into it
            # would refuse with INVALID_CONTEXT, which is a working session
            # reporting itself as unbound. `_bind` raises before this line is
            # reached, so a create that cannot be bound starts nothing.
            record = self._bind(request)
            answer = service.create(
                provider_id=p.get("provider_id") or self._provider_hint(resolved),
                agent_id=p["agent_id"], instruction=p["instruction"],
                idempotency_key=p["idempotency_key"], workspace=request.workspace)
            return self._record(request, answer, binding=record)
        if request.verb == "session.send":
            self._admit(request)
            return service.send(session_ref_value=request.session_ref,
                                instruction=p["instruction"],
                                turn_idempotency_key=p["turn_idempotency_key"],
                                context_ref=request.context_ref)
        if request.verb == "session.result":
            return service.result(session_ref_value=request.session_ref,
                                  trigger_id=p.get("trigger_id"))
        if request.verb == "session.inspect":
            return service.session_inspect(request.session_ref)
        if request.verb == "session.resume":
            self._admit(request)
            return service.resume(session_ref_value=request.session_ref,
                                  context_ref=request.context_ref)
        # The three below forward `context_ref` for the same reason `send` and
        # `resume` do, and the omission is worth naming because it was invisible
        # rather than harmless. ServerOS asks TWO separate authorities about a
        # human-gated verb: this router's resolver, whose approval input arrives
        # on the request, and the daemon's admission, whose approval input is
        # its own store keyed by the actor it resolves. Only the second one is
        # reached from here, and it can resolve nobody unless the identity
        # travels with the call. Forwarding it is not what makes these verbs
        # succeed — the resolver above refuses them first while no approval can
        # exist — but without it the daemon's answer is about no one in
        # particular, which is a refusal that cannot be acted on.
        if request.verb == "session.interrupt":
            return service.interrupt(session_ref_value=request.session_ref,
                                     context_ref=request.context_ref)
        if request.verb == "session.stop":
            return service.stop(session_ref_value=request.session_ref,
                                context_ref=request.context_ref)
        if request.verb == "session.handoff":
            return service.handoff(session_ref_value=request.session_ref,
                                   context_ref=request.context_ref)
        raise TransportFailure(
            "ACTION_UNSUPPORTED",
            f"the loopback has no implementation for {request.verb!r}; this is a "
            f"gap in the router, not a statement about any provider")

    @staticmethod
    def _provider_hint(resolved: dict | None) -> str:
        """The provider to create on when the caller did not name one.

        Derived from the resolved target rather than defaulted, and only from
        the record the resolver produced — never from a device name, a runtime
        name or a binary on PATH. A create that picked a provider by itself
        would be a create that ignored the resolution the caller just paid for.
        """
        provider_ref = ((resolved or {}).get("target") or {}).get("provider_ref")
        if not provider_ref:
            raise TransportFailure(
                "PROVIDER_UNAVAILABLE",
                "no provider was resolved for this create and none was named; "
                "the router will not choose one on the caller's behalf")
        return str(provider_ref)


NodeWireCall = Callable[[dict], dict]


class NodeControlTransport(AgentControlTransport):
    """A remote device, reached through an injected wire call.

    `connector` is where the deployed wiring goes, and it is injected rather
    than constructed so that this module contains no client for a protocol that
    does not exist yet. With no connector the transport does not pretend: it
    refuses with NODE_RUNTIME_UNAVAILABLE, which is the true statement about
    this deployment — neither FJ nor Dell runs a ServerOS node — and is exactly
    the answer §7 requires in place of "unknown device".

    `transport-contract.yaml` forbids silent substitution, and that is the rule
    this class follows at the one point where substituting would be tempting: a
    remote request that cannot be delivered is refused, never served locally.
    """

    name = "node-control"

    def __init__(self, *, connector: NodeWireCall | None = None,
                 device_refs: tuple[str, ...] = (),
                 timeout_s: float = 60.0) -> None:
        self.connector = connector
        self.device_refs = tuple(str(ref) for ref in device_refs)
        self.timeout_s = float(timeout_s)

    def reachable(self, device_ref: str) -> dict:
        if device_ref not in self.device_refs:
            return {"source": self.name}
        if self.connector is None:
            return {
                "node_runtime": {"state": "ABSENT",
                                 "provenance": "no connector declared for this node"},
                "source": self.name,
            }
        try:
            probe = self.connector({"schema": SCHEMA, "op": "node.probe",
                                    "correlation_id": new_correlation_id(),
                                    "device_ref": device_ref,
                                    "timeout_s": self.timeout_s})
        except Exception as exc:
            return {
                "node_runtime": {"state": "ABSENT",
                                 "provenance": f"probe failed: {type(exc).__name__}"},
                "source": self.name,
            }
        probe = probe or {}
        # A wire answer is `{ok, result}` or `{ok, reason, detail}`; the
        # observation is inside `result`. Reading the envelope as though it were
        # the observation would report every device as UNKNOWN while looking
        # like it had asked.
        if not probe.get("ok"):
            refused = str(probe.get("reason") or "PROVIDER_ERROR")
            return {
                "node_runtime": {
                    "state": "ABSENT" if refused == "NODE_RUNTIME_UNAVAILABLE" else "UNKNOWN",
                    "provenance": f"probe refused: {refused}"},
                "source": self.name,
            }
        result = dict(probe.get("result") or {})
        return {
            "presence": {"state": result.get("presence") or "UNKNOWN",
                         "observed_at": result.get("observed_at"),
                         "provenance": self.name},
            "node_runtime": {"state": result.get("node_runtime") or "UNKNOWN",
                             "observed_at": result.get("observed_at"),
                             "provenance": self.name},
            "source": self.name,
        }

    def resolve_inputs(self, device_ref: str, runtime_id: str) -> dict:
        """Ask the node what it can establish about itself.

        With no connector the answer is empty, and empty is the correct answer:
        this control plane has not asked the remote machine anything, so it has
        no provider rows for it. The resolver turns that into UNEVALUABLE
        rather than into a target that happens to look fine.
        """
        if self.connector is None or device_ref not in self.device_refs:
            return {}
        try:
            answer = self.connector({
                "schema": SCHEMA, "op": "resolve.inputs",
                "correlation_id": new_correlation_id(), "device_ref": device_ref,
                "runtime_id": runtime_id, "timeout_s": self.timeout_s}) or {}
        except Exception:
            return {}
        if not answer.get("ok"):
            return {}
        return dict(answer.get("result") or {})

    def send(self, request: AgentControlRequest, *, resolved: dict | None = None) -> dict:
        if request.device_ref not in self.device_refs:
            raise TransportFailure(
                "DEVICE_UNREACHABLE",
                f"no node transport is configured for {request.device_ref}; "
                f"known={list(self.device_refs)}")
        if self.connector is None:
            raise TransportFailure(
                "NODE_RUNTIME_UNAVAILABLE",
                f"no ServerOS node runtime is reachable on {request.device_ref} "
                f"from this control plane; the request was not delivered and "
                f"nothing about any session there was observed")
        envelope = {"op": "agent.control", **request.wire()}
        if resolved:
            # Beside the request, never inside it: the node must serve the
            # resolution the caller paid for, and the caller's own parameters
            # must stay exactly what the caller wrote.
            envelope["resolved"] = resolved
        answer = self.connector(envelope) or {}
        if answer.get("ok"):
            return dict(answer.get("result") or {})
        raise TransportFailure(
            str(answer.get("reason") or "PROVIDER_ERROR"),
            str(answer.get("detail") or "the node refused without a detail"),
            retryable=bool(answer.get("retryable")))


def _now() -> str:
    import datetime
    return datetime.datetime.now(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


def _writer_now() -> str:
    """The stamp a writer TTL is compared against (#237 T3).

    NOT `_now()`. That one is an `isoformat()`, which drops the microsecond
    field when it is zero, so `...:00Z` and `...:00.123456Z` sort wrongly at the
    same second under a string comparison — and the writer's `expires_at` IS
    compared as a string, in `runtime/coordination.py#writer_axis`. Fixed width,
    second precision, Z: the same contract the exclusive Work lease pins.
    """
    import datetime
    return datetime.datetime.now(
        datetime.timezone.utc).strftime(coordination.WRITER_STAMP)


# ---------------------------------------------------------------------------
# The router
# ---------------------------------------------------------------------------

class FleetAgentControlRouter:
    """Routes a control request to the transport for the device it names.

    The router's own device is stated at construction and never derived. The
    resolver refuses to guess which machine it is running on — a resolver that
    read a hostname answers differently inside a container — and a router that
    guessed would put that answer back.

    Everything this class knows about a device comes from a transport's
    `reachable()`. There is no second reachability store, no cache, and nothing
    persisted: a router that remembered yesterday's ONLINE would answer a
    question about today with it.
    """

    def __init__(self, *, local_device_ref: str,
                 transports: list[AgentControlTransport] | None = None,
                 registry=None, clock=None, context_store: dict | None = None,
                 attempts=None) -> None:
        if not str(local_device_ref).startswith("device:"):
            raise TargetError(
                f"{local_device_ref!r} is not a device ref. This router is the "
                f"control plane of one named device, not of a hostname")
        self.local_device_ref = str(local_device_ref)
        self.registry = registry
        self._clock = clock
        # What ServerOS actually minted, keyed by ref. A `context_ref` on a
        # request is a claim; this is the store the claim is resolved against,
        # and it is deliberately empty by default. A router that minted a
        # context to satisfy a ref would verify a claim with itself.
        #
        # Held by reference rather than copied into a dict, because the store
        # is not always local: a caller whose contexts live behind the daemon
        # passes a mapping whose `get` asks for one record. Copying would
        # materialise it, and the alternative to a mapping here is a second
        # resolution path — the one thing `actor.resolve` must not have.
        # Nothing in this class writes to it.
        self.context_store = {} if context_store is None else context_store
        self.transports = list(transports or [])
        # Single-flight replay for retries, in memory and bounded. Named for
        # what it is: an in-process guard against a caller retrying a mutation
        # within one router lifetime, NOT a durable exactly-once guarantee
        # across a restart. The limitation is declared in the contract rather
        # than left for a reader to assume the stronger thing.
        self._seen_keys: dict[str, dict] = {}
        self._max_seen = 512
        # #245 / S2. The DURABLE half, when one is given: an object with
        # `get_dispatch_attempt(key)` / `put_dispatch_attempt(record)` — the
        # existing Agent Control store satisfies both. It is optional so that a
        # router built without a store keeps the in-process guard rather than
        # silently gaining a durable claim it cannot honour; and when it IS
        # given, the guard reads the record that survives a restart, so a retry
        # after a lost response is refused instead of dispatched a second time.
        self._attempts = attempts

    # -- evidence ----------------------------------------------------------

    def evidence(self, device_refs: tuple[str, ...]) -> dict:
        """What every transport can say about these devices, in resolver shape.

        Both maps are keyed by device ref and both omit what nobody observed.
        An omitted entry makes the resolver answer UNKNOWN, which is the point:
        a transport that filled in a default would be inventing the observation.
        """
        presence: dict = {}
        node_runtime: dict = {}
        for device_ref in device_refs:
            for node in self.transports:
                reported = node.reachable(device_ref)
                if "presence" in reported:
                    presence[device_ref] = dict(reported["presence"],
                                                source=reported.get("source"))
                if "node_runtime" in reported:
                    node_runtime[device_ref] = dict(reported["node_runtime"],
                                                    source=reported.get("source"))
        return {"presence": presence, "node_runtime": node_runtime}

    def _resolved_context(self, request: AgentControlRequest) -> dict:
        """Resolve the presented `context_ref` against what ServerOS minted.

        A ref that resolves to nothing contributes no `context` key, and the
        resolver then refuses with INVALID_CONTEXT — which §22 requires and
        which is the whole reason the ref is not passed through as though it
        were the record. Reusing `actor.resolve` rather than looking the ref up
        here is deliberate: it is the module that decides what a well-formed ref
        is, and a second reading of the same string is a second answer.
        """
        if not request.context_ref:
            return {}
        found = actor.resolve(request.context_ref, self.context_store)
        return {"context": found["context"]} if found.get("found") else {}

    def transport_for(self, device_ref: str) -> AgentControlTransport | None:
        for node in self.transports:
            if device_ref == getattr(node, "device_ref", None):
                return node
            if device_ref in getattr(node, "device_refs", ()):
                return node
        return None

    # -- the one entry point ----------------------------------------------

    def route(self, request: AgentControlRequest) -> AgentControlResponse:
        """Resolve, then dispatch. Never raises for "no"."""
        if not isinstance(request, AgentControlRequest):
            raise Rejected("route() takes an AgentControlRequest, not a payload")

        known = set(control_actions())
        if request.action not in known:
            raise Rejected(
                f"{request.action!r} is not an Agent Control action; "
                f"known={sorted(known)}")

        verb = request.verb
        if request.action in server_side_actions():
            return self._refuse(
                request, "ACTION_UNSUPPORTED",
                f"{request.action} names a server-side procedure, not a provider "
                f"verb; call handoff() on this router. Forwarding it would ask a "
                f"provider for a verb no provider implements, and the refusal it "
                f"returned would be true for the wrong reason")
        if _mutating(verb) and not request.context_ref:
            return self._refuse(request, "INVALID_CONTEXT",
                                f"{request.action} mutates, and a mutation without a "
                                f"trusted context_ref has no actor to bind an "
                                f"approval to or to attribute the act to")

        # A ref that was PRESENTED and did not resolve is refused here, before
        # admission is even reached, and regardless of what admission would
        # have said.
        #
        # The resolver already refuses an unresolvable ref on a human-gated
        # verb, which is where the actor is load-bearing. That leaves the
        # delegation-class verbs — `session.send` among them — where a forged
        # ref used to be dropped on the floor and the call proceeded with no
        # actor at all. Two things were wrong with that. The act was
        # unattributed in a ledger whose whole purpose is attribution, and a
        # forged ref was indistinguishable from no ref: §22 says a forged
        # context_ref is refused, and a caller that keeps presenting a stale
        # handle was never told it had stopped working. An ALLOW does not make
        # the claim true; it only means nobody needed to approve it.
        context = self._resolved_context(request)
        if request.context_ref and not context.get("context"):
            return self._refuse(
                request, "INVALID_CONTEXT",
                f"{request.action} presented a context_ref and no context was "
                f"resolved from it. A ref is a claim and the minted record is the "
                f"evidence; proceeding without one would record this act with no "
                f"actor and would tell a forger its ref had been accepted")

        # The actor behind the presented context, resolved by ServerOS from the
        # minted record. It is part of the intent identity (#237 T5) and it is
        # what the attempt record carries (#245 P0-2) — never a string from the
        # payload, and `None` when the request presented no context.
        principal = (context.get("context") or {}).get("actor_ref")

        node = self.transport_for(request.device_ref)
        observed = (node.resolve_inputs(request.device_ref, request.runtime_id)
                    if node is not None else {})
        resolver_request = {
            # Transport-supplied inputs go FIRST and are whitelisted, because a
            # transport supplies observations and never the request. Spreading
            # them last let a forged node answer a `resolve.inputs` probe with
            # `{"action": null}` and choose what it was being asked to do — the
            # caller's verb silently replaced by the machine being asked to
            # perform it. The whitelist is the fix; the ordering is the belt.
            **{key: observed[key] for key in TRANSPORT_INPUT_KEYS if key in observed},
            **{key: self.evidence((request.device_ref,))[key]
               for key in ("presence", "node_runtime")},
            **request.resolver_request(),
            "local_device_ref": self.local_device_ref,
            "registry": self.registry,
            "approvals": list(request.approvals),
            **context,
        }
        record = target.resolve(resolver_request)

        if record["status"] not in ("AVAILABLE", "DEGRADED"):
            return self._from_resolution(request, record)

        route = (record["target"].get("route") or {})
        kind = route.get("kind")
        if kind == "local":
            node = self.transport_for(request.device_ref)
            if node is None:
                return self._refuse(
                    request, "DEVICE_UNREACHABLE",
                    f"the target resolved to the local device but no transport is "
                    f"configured for {request.device_ref}", record=record)
        elif kind == "fleet":
            node = self.transport_for(request.device_ref)
            if node is None:
                return self._refuse(
                    request, "NODE_RUNTIME_UNAVAILABLE",
                    f"{request.device_ref} resolved to the fleet plane and no "
                    f"transport carries requests to it from this control plane",
                    record=record)
        else:
            return self._refuse(
                request, "DEVICE_UNREACHABLE",
                f"no route carries this request to {request.device_ref}",
                record=record)

        replayed = self._replay(request, principal=principal)
        if replayed is not None:
            return replayed

        # #245. The attempt is persisted BEFORE the mutation leaves this
        # process, and that order is the whole point: a record written here can
        # say "we had decided to send and had not yet" if the process dies next,
        # which a record written AFTER the send cannot — by then it has already
        # happened and the two crash windows are indistinguishable.
        self._begin_attempt(request, principal=principal)

        try:
            result = node.send(request, resolved=record)
        except TransportFailure as exc:
            # #245. Leave the attempt in the phase its failure actually
            # supports: nothing-sent stays CALL_NOT_SENT; anything ambiguous
            # becomes EFFECT_UNKNOWN, so a retry does not launch a second paid
            # turn. Then refuse in the transport's own vocabulary, verbatim —
            # nothing here converts a delivery failure into a statement about
            # the session on the far side, because the transport learned nothing
            # about it.
            self._mark_attempt_after_failure(request, exc.reason,
                                             principal=principal)
            return self._refuse(request, exc.reason, exc.detail, record=record,
                                retryable=exc.retryable)

        self._remember(request, result, principal=principal)
        return AgentControlResponse(
            ok=True, action=request.action, verb=verb,
            device_ref=request.device_ref, correlation_id=request.correlation_id,
            route=kind, target=record["target"],
            resolved=_resolution_summary(record),
            result=result, provenance=tuple(record.get("provenance") or ()),
            dispatched=True)

    # -- refusal construction ---------------------------------------------

    def _from_resolution(self, request, record) -> AgentControlResponse:
        """A resolved refusal, in the resolver's own words.

        The reason is copied, never re-derived and never re-worded. The router
        adding its own interpretation here is how "the provider does not support
        this" becomes "policy denied" one layer later.
        """
        unevaluated = list(record.get("unevaluated") or ())
        detail = record.get("detail")
        if not detail and unevaluated:
            # UNKNOWN is the resolver saying nobody established these inputs. The
            # router does not manufacture a reason word for it: every word in the
            # closed list claims something was established, and picking one would
            # be the router asserting a fact the resolver just declined to.
            detail = (f"nothing refused this request; {unevaluated} were never "
                      f"established, so the target is UNKNOWN rather than refused")
        return AgentControlResponse(
            ok=False, action=request.action, verb=request.verb,
            device_ref=request.device_ref, correlation_id=request.correlation_id,
            reason=record.get("reason"),
            detail=detail,
            route=((record.get("target") or {}).get("route") or {}).get("kind"),
            target=record.get("target"),
            resolved=_resolution_summary(record),
            provenance=tuple(record.get("provenance") or ()),
            retryable=record.get("reason") in ("TIMEOUT",),
            dispatched=False)

    def _refuse(self, request, reason, detail, *, record=None,
                retryable: bool = False) -> AgentControlResponse:
        if reason not in reason_vocabulary():
            raise TargetError(
                f"{reason!r} is not in the reason vocabulary; the router refuses "
                f"in the one list or it does not refuse")
        return AgentControlResponse(
            ok=False, action=request.action, verb=request.verb,
            device_ref=request.device_ref, correlation_id=request.correlation_id,
            reason=reason, detail=detail,
            route=(((record or {}).get("target") or {}).get("route") or {}).get("kind"),
            target=(record or {}).get("target"),
            resolved=_resolution_summary(record) if record else None,
            provenance=tuple((record or {}).get("provenance") or ()),
            retryable=retryable, dispatched=False)

    # -- idempotency -------------------------------------------------------

    def _intent_digest(self, request: AgentControlRequest, *, principal=None) -> str:
        """The identity of this request's INTENT — the TARGET, not just the key.

        #237 T5 / independent-QA P0-1. The guard compared the action, the device
        and the payload and omitted the session, the runtime and the principal —
        so two sends to different sessions under one key digested identically and
        the second was answered as a replay of the first. The digest is what
        makes that a CONFLICT.
        """
        return dispatch_attempts.intent_digest(
            action=request.action, device_ref=request.device_ref,
            verb=request.verb, params=request.params,
            workspace=request.workspace, work_item=request.work_item,
            session_ref=request.session_ref,
            agent_runtime=request.agent_runtime, principal=principal)

    def _attempt_record(self, request, phase, *, principal=None, result=None) -> dict:
        """One durable attempt record for this request, at this phase.

        It carries the RESOLVED principal and the target. The daemon re-derives
        the principal from the trusted context and refuses a record that claims a
        different one, so a principal from the payload is never trusted
        (#245 P0-2).
        """
        return dispatch_attempts.attempt_record(
            key=request.idempotency_key, action=request.action,
            device_ref=request.device_ref, verb=request.verb,
            intent_digest=self._intent_digest(request, principal=principal),
            phase=phase, work_item=request.work_item, principal=principal,
            runtime=request.agent_runtime, session_ref=request.session_ref,
            result=result, at=_now())

    def _durable_attempt(self, request) -> bool:
        """Whether this request has a durable attempt to write. Mutations only."""
        return bool(self._attempts is not None and request.idempotency_key
                    and _mutating(request.verb))

    def _begin_attempt(self, request, *, principal=None) -> None:
        """Persist `CALL_NOT_SENT` BEFORE the mutation leaves this process. #245.

        The order is the entire value. A record written BEFORE `node.send` can
        say "we had decided to send and had not yet" when the process dies next;
        a record written after the send cannot, because by then the effect has
        happened and "crashed before" and "crashed after" are the same bytes
        with opposite repairs.

        The attempt is recorded against the request's own trusted `context_ref`;
        the daemon resolves the actor from it and refuses a record whose claimed
        principal is anybody else. A store that refuses this write is NOT
        swallowed — it raises, as the binding write at `_record` does — because
        dispatching without the record is exactly the blind retry this exists to
        prevent.
        """
        if not self._durable_attempt(request):
            return
        self._attempts.put_dispatch_attempt(
            self._attempt_record(request, dispatch_attempts.CALL_NOT_SENT,
                                 principal=principal),
            context_ref=request.context_ref)

    def _mark_attempt_after_failure(self, request, reason, *, principal=None) -> None:
        """Leave the attempt in the phase the failure actually supports.

        Nothing-sent stays `CALL_NOT_SENT` (a retry is safe); anything ambiguous
        becomes `EFFECT_UNKNOWN`, so a retry refuses instead of launching a
        second paid turn.
        """
        if not self._durable_attempt(request):
            return
        self._attempts.put_dispatch_attempt(
            self._attempt_record(
                request, dispatch_attempts.phase_after_transport_failure(reason),
                principal=principal),
            context_ref=request.context_ref)

    def _replay(self, request: AgentControlRequest, *,
                principal=None) -> AgentControlResponse | None:
        """A retry of the same intent, answered from the record of the first.

        Two records, one decision. When a durable store was given (`attempts`),
        the record it holds — which survives a restart — decides, and a recorded
        UNKNOWN outcome refuses the retry instead of dispatching it again (#237
        T4/T9). Without one, the in-process dict decides, and the limitation is
        the one the contract already declares.

        A read is never replayed: answering one from memory would answer with an
        observation that has aged, which is worse than answering slowly.
        """
        if not request.idempotency_key or not _mutating(request.verb):
            return None
        digest = self._intent_digest(request, principal=principal)

        if self._attempts is not None:
            recorded = self._attempts.get_dispatch_attempt(request.idempotency_key)
            verdict = dispatch_attempts.reconcile(recorded, digest=digest)
            if verdict["verdict"] == dispatch_attempts.PROCEED:
                return None
            if verdict["verdict"] == dispatch_attempts.REPLAY:
                return AgentControlResponse(
                    ok=True, action=request.action, verb=request.verb,
                    device_ref=request.device_ref,
                    correlation_id=request.correlation_id,
                    result=dict(recorded.get("result") or {},
                                idempotent_replay=True),
                    dispatched=False)
            # CONFLICT / IN_FLIGHT / REFUSE_UNKNOWN / REFUSE_SUPERSEDED all
            # refuse, each saying why in the kernel's own sentence. A caller
            # told only "no" could not tell a settled replay from an unknown
            # outcome, and the repair differs.
            raise Rejected(
                f"idempotency key {request.idempotency_key!r}: "
                f"{verdict['because']}")

        seen = self._seen_keys.get(request.idempotency_key)
        if seen is None:
            return None
        if (seen["action"] != request.action
                or seen["device_ref"] != request.device_ref
                or seen.get("intent_digest") != digest):
            # The same key on a different intent is a caller bug, and silently
            # answering it with the other call's result would report an effect
            # that never happened for this request.
            raise Rejected(
                f"idempotency key {request.idempotency_key!r} was already used for "
                f"{seen['action']} on {seen['device_ref']} with a different "
                f"payload; a key identifies one intent, not one caller")
        return AgentControlResponse(
            ok=True, action=request.action, verb=request.verb,
            device_ref=request.device_ref, correlation_id=request.correlation_id,
            route=seen.get("route"), target=seen.get("target"),
            result=dict(seen["result"], idempotent_replay=True),
            provenance=tuple(seen.get("provenance") or ()),
            dispatched=False)

    def _remember(self, request: AgentControlRequest, result: dict, *,
                  principal=None) -> None:
        if not request.idempotency_key or not _mutating(request.verb):
            return
        digest = self._intent_digest(request, principal=principal)
        if self._attempts is not None:
            # The outcome. `_begin_attempt` already wrote CALL_NOT_SENT before
            # the send, so this MOVES the same attempt to SETTLED — same key,
            # same digest, a different phase, which is what a phase is for. The
            # daemon accepts a SETTLED only with the provider's answer attached
            # and only over an existing attempt, so a forged settle is refused
            # there (#245 P0-2). A store that refuses the write is NOT swallowed:
            # the caller would otherwise believe a retry will be recognised when
            # nothing recorded it.
            self._attempts.put_dispatch_attempt(
                self._attempt_record(request, dispatch_attempts.SETTLED,
                                      principal=principal, result=result),
                context_ref=request.context_ref)
        self._seen_keys[request.idempotency_key] = {
            "action": request.action, "device_ref": request.device_ref,
            "intent_digest": digest, "result": result,
        }
        if len(self._seen_keys) > self._max_seen:
            for key in list(self._seen_keys)[: len(self._seen_keys) - self._max_seen]:
                del self._seen_keys[key]

    def handoff(self, request: AgentControlRequest,
                create_request: AgentControlRequest | None = None) -> "HandoffOutcome":
        """§9's server-side procedure, on this router. See `handoff()` below."""
        return handoff(router=self, request=request, create_request=create_request)


def _resolution_summary(record: dict) -> dict:
    """The resolved record, minus the parts that are a call log.

    `steps` and the full provenance are what a Level 3 disclosure exists to
    show; a route result carries the verdict and the names, so that an ordinary
    caller is not handed a diagnostic record to answer a question about which
    machine got the request.
    """
    return {
        "status": record.get("status"),
        "reason": record.get("reason"),
        "provider_instance_ref": (record.get("target") or {}).get("provider_instance_ref"),
        "adapter_ref": (record.get("target") or {}).get("adapter_ref"),
        "admission_required": record.get("admission_required"),
        "refused_by": record.get("refused_by"),
        "unevaluated": list(record.get("unevaluated") or ()),
        "summary": target.summarize(record),
    }


# ---------------------------------------------------------------------------
# Handoff (§9). The order is the point.
# ---------------------------------------------------------------------------

HANDOFF_STEPS = ("checkpoint", "resolve_target", "target_available",
                 "target_ready", "context_handed_off", "source_released")


@dataclass(frozen=True)
class HandoffOutcome:
    ok: bool
    reason: str | None
    completed: tuple
    stopped_at: str | None
    source_preserved: bool
    detail: dict = field(default_factory=dict)
    #: The ref of the record this handoff is anchored to, recorded verbatim and
    #: carried back on every answer — including the refusals, so that a reader
    #: can tell "no checkpoint was named" from "the field was not filled in".
    #: §9's `checkpoint` is a ref and not a flag: what makes a lost session
    #: recoverable is knowing WHICH record holds the state.
    checkpoint: str | None = None

    def show(self) -> dict:
        return {
            "ok": self.ok, "reason": self.reason,
            "completed": list(self.completed), "stopped_at": self.stopped_at,
            "source_preserved": self.source_preserved,
            "checkpoint": self.checkpoint,
            "detail": self.detail,
            "order": list(HANDOFF_STEPS),
        }


def handoff(*, router: FleetAgentControlRouter, request: AgentControlRequest,
            create_request: AgentControlRequest | None = None) -> HandoffOutcome:
    """Move a session to another device, in the only order that is safe.

    CHECKPOINT FIRST. The source session is not touched until the target has
    accepted, because the two failure modes are not symmetric: a handoff that
    stops the source and then cannot start the target has lost the session, and
    a handoff that starts the target and then fails to stop the source has two
    sessions — which is visible, recoverable, and far cheaper.

    So this function never calls stop on the source unless the target answered.
    §9 states it as a rule and it is stated here as control flow, because a rule
    a reader has to remember is a rule that survives right up until the first
    refactor.
    """
    completed: list[str] = []

    checkpoint = (request.params or {}).get("checkpoint")
    if not isinstance(checkpoint, str) or not checkpoint.strip():
        # A ref, not a flag, and this is where the two differ: the parameter
        # used to accept any truthy value and was then dropped, so a handoff
        # could not be told from a handoff whose checkpoint was never written.
        # `fleet/agent-control-contract.yaml#handoff.checkpoint` says where the
        # ref's reader actually is and why it does not travel to the provider.
        return HandoffOutcome(
            False, "INVALID_CONTEXT", tuple(completed), "checkpoint", True,
            {"why": "a handoff is anchored to a checkpoint, and the checkpoint is "
                    "the ref of one — `<work_item>#<epoch>`, what "
                    "`work checkpoint` prints. What arrived was "
                    + ("nothing" if checkpoint is None else type(checkpoint).__name__)
                    + ", so nothing says the state was preserved before the session "
                      "was moved, and a handoff with nothing to hand over moves a "
                      "session and leaves its state behind"})
    completed.append("checkpoint")

    # §9's second step, and the reason it is checked before the resolver runs: a
    # handoff that names no destination is not a request the resolver can
    # refuse. Without `to_device_ref` this built a target request addressed to
    # the empty device, and the resolver rejected it — an exception out of a
    # procedure whose contract is that "no" is an answer. An unnamed destination
    # is a resolution failure, so it is reported as one; `to_device_ref` being
    # optional in the contract is not permission for it to go unread.
    if create_request is None and not str(
            (request.params or {}).get("to_device_ref") or ""):
        return HandoffOutcome(
            False, "INVALID_CONTEXT", tuple(completed), "resolve_target", True,
            {"why": "a handoff names no destination, so there is nothing to "
                    "resolve. `to_device_ref` is how the procedure is told where "
                    "the session goes, and a request that does not say is "
                    "malformed rather than refused"},
            checkpoint=checkpoint)

    target_request = create_request or _resume_request(request)
    created = router.route(target_request)
    if not created.ok:
        # Nothing was stopped. The source session is exactly as it was, and the
        # reason is the target's own — not a handoff-specific rewording of it.
        return HandoffOutcome(False, created.reason, tuple(completed),
                              _stop_step(created), True,
                              {"target": created.show()},
                              checkpoint=checkpoint)
    completed.extend(["resolve_target", "target_available", "target_ready"])

    # §9, one layer below the rule the order states. `created.ok` says the
    # target ANSWERED; it does not say the target is holding a session. The gap
    # is the whole of the release decision, because an ok answer that names no
    # session leaves nothing for the context to have been handed TO — and a
    # procedure that released the source on one would have ended the only
    # session anybody has evidence for.
    #
    # This is the rule `_record` already applies to a create, in the same word
    # and for the same reason: a ref the control plane did not receive is a
    # session ServerOS cannot name, and identity is the one thing a control verb
    # has no business establishing on the provider's behalf.
    handed = created.result or {}
    if not handed.get("session_ref"):
        return HandoffOutcome(
            False, "INVALID_CONTEXT", tuple(completed), "context_handed_off", True,
            {"target": created.show(),
             "why": "the target answered and named no session, so there is no "
                    "session the context was handed to. The source is untouched: "
                    "releasing it here would end the only session that is known "
                    "to exist"},
            checkpoint=checkpoint)
    completed.append("context_handed_off")

    # #237 B1 / architecture doc §5. The source's future WRITE authority is
    # revoked here — independently of `release_source` below — but ONLY once the
    # TARGET is ESTABLISHED, which `context_handed_off` by itself does NOT prove.
    #
    # `context_handed_off` says the target ANSWERED with a session. It does NOT
    # say the target has been recorded as the writer of this Item, and revoking
    # the source on a session alone leaves a window with NO authorized writer —
    # the one failure a handoff cannot recover from. `_revoke...` therefore
    # checks the target's OWN binding first and refuses to revoke until the
    # target is established; the source then keeps its authority, which is the
    # recoverable direction.
    #
    # Closing the source's BINDING — not the session — is what makes the existing
    # word `THREAD_CLOSED_FOR_NEW_WORK` answerable for the stale source: it means
    # exactly "the thread was closed while its provider session is still alive
    # and accepting turns". The source stays READABLE (`inspect`, `result`,
    # `interrupt`, `stop` are not gated). The binding is write-once on
    # `session_ref`, so the source can never be silently retargeted.
    _revoke_source_write_authority(
        router, request, target_ref=handed.get("session_ref"),
        target_device=target_request.device_ref)

    release = (request.params or {}).get("release_source")
    if not release:
        return HandoffOutcome(True, None, tuple(completed), None, True,
                              {"target": handed,
                               "why": "the source was not stopped: release_source "
                                      "was not requested, and a handoff that stops "
                                      "the source by default is a handoff that "
                                      "loses it whenever the target turns out to "
                                      "be unsuitable an hour later"},
                              checkpoint=checkpoint)

    stopped = router.route(_stop_request(request))
    if not stopped.ok:
        return HandoffOutcome(False, stopped.reason, tuple(completed), "source_released",
                              True, {"target": handed, "stop": stopped.show(),
                                     "why": "the target is running and the source is "
                                            "still running; both exist and neither "
                                            "was lost"},
                              checkpoint=checkpoint)
    completed.append("source_released")
    return HandoffOutcome(True, None, tuple(completed), None, False,
                          {"target": handed, "stop": stopped.result},
                          checkpoint=checkpoint)


# The handoff step each resolver step corresponds to. The two orders ask the
# same question in different words: the first four steps establish WHICH target
# could carry the request, and the last three whether it would do it now. Kept
# as one table rather than re-derived at each call site, so that a step added to
# one order and not the other is visible here.
HANDOFF_STEP_FOR_RESOLVER_STEP = {
    "device": "resolve_target",
    "agent_runtime": "resolve_target",
    "provider_instance": "resolve_target",
    "adapter": "resolve_target",
    "support": "target_ready",
    "readiness": "target_ready",
    "admission": "target_ready",
}

#: The route kinds that mean a route was NAMED. `_step_route` is the last step of
#: the resolver's chain, so a record carrying one of these is a record where
#: every step before it agreed and the request was addressed somewhere.
NAMED_ROUTES = ("local", "fleet")


def _stop_step(answer: AgentControlResponse) -> str:
    """Which step a failed handoff stopped at, read off the answer.

    Read, and not re-derived from the reason word, because the word does not
    answer this question. `DEVICE_UNREACHABLE` and `NODE_RUNTIME_UNAVAILABLE`
    are each produced on two planes — by the resolver, where no route was ever
    named, and by a transport, after a route WAS named and nothing could be
    delivered to it. Mapping the word alone therefore had to be wrong for one of
    the two, and it was wrong in the other direction: a device that resolved and
    whose runtime could not carry the request was reported as `target_ready`, a
    step whose name tells the reader to wait for something that will never be
    ready.

    Two facts on the answer decide it, both copied from the layer that
    established them and neither derived here: `refused_by` names the resolver
    step that refused, and `route` is the resolver's own `route_kind`, which is
    set only after every step before it agreed.
    """
    refused_by = (answer.resolved or {}).get("refused_by")
    if refused_by:
        step = HANDOFF_STEP_FOR_RESOLVER_STEP.get(refused_by)
        if step is None:
            raise TargetError(
                f"the resolver refused at {refused_by!r}, which corresponds to no "
                f"handoff step; §9's order and the resolver's order have to be "
                f"readable against each other or the stop point is a guess")
        return step
    if answer.route not in NAMED_ROUTES:
        # No route was named, so there was nothing to be available or ready.
        return "resolve_target"
    if answer.reason in ("DEVICE_UNREACHABLE", "NODE_RUNTIME_UNAVAILABLE"):
        # A route was named and the request never reached the other end of it.
        return "target_available"
    # The target was reached, and what came back is the target's own answer.
    return "target_ready"


def _revoke_source_write_authority(router, request: AgentControlRequest, *,
                                   target_ref, target_device):
    """Close the SOURCE thread's binding, once the TARGET is ESTABLISHED.

    §9 / #237 B1. Called once `context_handed_off` has been reached. The ORDER
    is the safety property, and it is not the order the design's steps were
    written in:

      * `context_handed_off` proves the target ANSWERED with a session;
      * it does NOT prove the target has been RECORDED as the writer of this
        Item — a session with no binding is not an established writer;
      * revoking the source on a session alone leaves a window with NO
        authorized writer, and a handoff cannot recover from that state.

    So the target's OWN binding is read first, and the source is closed only
    when that binding exists and is bound to the SAME Item. Every other outcome
    leaves the source's write authority intact, which is the recoverable
    direction to be wrong in: two writers is visible and correctable, zero
    writers is a stall.

    Reached through each device's OWN transport, so the bindings read and closed
    are the ones each side's admission will later read — never a second store.
    Returns {"revoked": <record|None>, "because": <str>}. The handoff does not
    turn a non-revocation into a failure: the target is up and the source is
    intact, which is exactly the state `HandoffOutcome.source_preserved` already
    describes.
    """
    source_transport = router.transport_for(request.device_ref)
    source_bindings = getattr(source_transport, "bindings", None)
    if source_bindings is None or not hasattr(source_bindings, "close"):
        return {"revoked": None,
                "because": "the source device has no bindings handle here"}
    source = None
    if hasattr(source_bindings, "get"):
        source = (source_bindings.get(request.session_ref) or {}).get("binding")
    if not source:
        return {"revoked": None,
                "because": "the source thread has no recorded binding to revoke"}
    if str(source.get("state") or "").upper() == "CLOSED":
        return {"revoked": source, "because": "the source thread was already closed"}
    item = str(source.get("bound_item_ref") or "")
    if not item:
        return {"revoked": None,
                "because": "the source thread has no bound Item, so there is no "
                           "write scope to transfer"}

    target_transport = router.transport_for(target_device) if target_device else None
    target_bindings = (getattr(target_transport, "bindings", None)
                       if target_transport is not None else None)
    target = None
    if target_ref and target_bindings is not None and hasattr(target_bindings, "get"):
        target = (target_bindings.get(target_ref) or {}).get("binding")
    if not target or str(target.get("bound_item_ref") or "") != item:
        return {"revoked": None,
                "because": (f"the target is not established as the writer of "
                            f"{item!r}: it answered with a session, but no binding "
                            f"records it as this Item's writer, and revoking the "
                            f"source now would leave no authorized writer. The "
                            f"source keeps its write authority")}

    closed = source_bindings.close(
        request.session_ref, at=_now(),
        reason="handoff moved write authority to the established target session")
    return {"revoked": closed,
            "because": "the target was established, so the source thread was closed"}


def _resume_request(request: AgentControlRequest) -> AgentControlRequest:
    action = (request.params or {}).get("target_action") or "agent.session.resume"
    return AgentControlRequest(
        action=action, device_ref=str((request.params or {}).get("to_device_ref") or ""),
        agent_runtime=request.agent_runtime,
        params=dict((request.params or {}).get("target_params") or {}),
        context_ref=request.context_ref, workspace=request.workspace,
        work_item=request.work_item,
        session_role=request.session_role, bound_item_ref=request.bound_item_ref,
        session_ref=(request.params or {}).get("to_session_ref"),
        correlation_id=request.correlation_id)


def _stop_request(request: AgentControlRequest) -> AgentControlRequest:
    """The stop this handoff would perform, carrying the caller's approvals.

    `session.stop` is human-gated, so the stop needs an approval bound to the
    STOP — its own action, its own target, its own parameters. Forwarding what
    the caller presented is not the compound action granting itself authority:
    `approval.decide` still decides it against this request, and an approval
    minted for the handoff or for another session is reported as
    APPROVAL_REQUIRED, APPROVAL_BINDING_MISMATCH or APPROVAL_CONTEXT_MISMATCH
    and leaves the source running. NOT forwarding it is what left §9's last step
    unreachable: with `approvals` always empty, `source_released` was a step
    name no caller could arrive at, and `release_source` was a parameter with no
    possible effect.
    """
    return AgentControlRequest(
        action="agent.session.stop", device_ref=request.device_ref,
        agent_runtime=request.agent_runtime,
        params={}, context_ref=request.context_ref,
        approvals=tuple(request.approvals or ()),
        session_ref=request.session_ref, correlation_id=request.correlation_id)


# ---------------------------------------------------------------------------
# Request construction, with the closed parameter set enforced here too.
# ---------------------------------------------------------------------------

def request_from_payload(payload: Mapping) -> AgentControlRequest:
    """Build a request from a caller's payload, or refuse it as malformed.

    Malformed is an exception and not a refusal, matching the resolver: a wrong
    action name is a caller mistake with nothing to observe, whereas "the device
    is unreachable" is a finding. Raising for the first and returning for the
    second keeps a caller from parsing a mistake as a status.
    """
    if not isinstance(payload, Mapping):
        raise Rejected("an Agent Control request must be a mapping")
    action = str(payload.get("action") or "")
    actions = control_actions()
    if action not in actions:
        raise Rejected(f"{action!r} is not an Agent Control action; "
                       f"known={sorted(actions)}")
    for field_name in ("device_ref", "agent_runtime"):
        if not payload.get(field_name):
            raise Rejected(f"{field_name} is required")

    envelope = {"action", "device_ref", "agent_runtime", "params", "context_ref",
                "approvals", "workspace", "work_item", "session_ref",
                "correlation_id", "idempotency_key", "timeout_s"}
    # §17's two fields are closed PER ACTION, not globally. On `session.send`
    # a `bound_item_ref` would be a caller restating what a thread is for, and
    # the router would have nowhere to put it — a field accepted and ignored is
    # the declaration-with-no-reader shape this repository keeps finding. So a
    # caller that names one where the contract does not allow it is refused by
    # name rather than quietly obeyed.
    allowed_envelope = envelope | set(action_coordination()[action])
    stray = sorted(set(payload) - allowed_envelope)
    if stray:
        # The envelope is closed for the same reason `params` is, and this is
        # where a credential posted as a top-level field lands. Silently
        # dropping it would leave the caller believing it took effect; a
        # credential that is quietly ignored is a credential the caller thinks
        # is being used.
        raise Rejected(
            f"{action} does not take {stray}; it takes "
            f"{sorted(allowed_envelope)}. Credentials belong to the provider, "
            f"not to a routed request, and §17's role fields belong to the one "
            f"action that creates the thread")
    # `session_role` is required on `session.create` and has no default, and
    # the check is here rather than in the session layer because it is a
    # property of the REQUEST. A create that did not say what the thread is for
    # is refused before a provider is asked to start one — the alternative is a
    # live provider session whose purpose ServerOS would have to guess at.
    for field_name in action_coordination()[action]:
        if not payload.get(field_name) and field_name == "session_role":
            raise Rejected(
                f"{action} requires {field_name}: a session is created with a "
                f"stated purpose, and there is no default because a default "
                f"would be a guess about somebody's intent recorded as a fact")

    params = dict(payload.get("params") or {})
    if not isinstance(params, Mapping):
        raise Rejected("params must be a mapping")
    allowed = set(action_params()[action]) | set(action_optional()[action])
    unknown = sorted(set(params) - allowed)
    if unknown:
        # The check that keeps a command string out. `params` is closed per
        # action, so a caller cannot smuggle an argv into a field the transport
        # would carry without looking at it.
        raise Rejected(
            f"{action} does not take {unknown}; it takes {sorted(allowed)}. "
            f"An Agent Control request carries a closed action and typed "
            f"parameters, never a command")
    missing = sorted(set(action_params()[action]) - set(params) - _derived_params(action))
    if missing:
        raise Rejected(f"{action} requires {missing}")

    try:
        # Reuses the device-binding check rather than repeating its list: one
        # definition of what a credential field is called.
        transport.validate_binding({**params, "device_ref": payload["device_ref"],
                                    "kind": "agent-control-request"})
    except ValueError as exc:
        raise Rejected(str(exc)) from exc

    approvals = payload.get("approvals") or []
    if not isinstance(approvals, (list, tuple)) or any(
            not isinstance(item, (dict, str)) for item in approvals):
        raise Rejected("approvals must be a list of approval records or refs")

    # `idempotency_key` on the envelope is the ROUTER's handle on a retry — the
    # key `_replay` answers from — and the action's own parameter is the
    # PROVIDER's: `session.create` takes `idempotency_key` and `session.send`
    # takes `turn_idempotency_key`, and those travel in `params` to the provider
    # that deduplicates on them.
    #
    # The envelope key is derived from the parameter when a caller supplied only
    # the parameter, and that is a fix, not a convenience. Both mean "this
    # intent, once", and before this the router's guard fired only when a caller
    # happened to set the envelope too: `{"action": "agent.session.send",
    # "params": {"turn_idempotency_key": "t-1"}}` — a request that names the key
    # exactly as the contract names it — dispatched with NO replay guard at all,
    # so a retry was a second turn. The MCP surface never met this only because
    # `agent_mcp._payload` copies the parameter onto the envelope itself, which
    # made one request mean two things depending on which surface it arrived at.
    #
    # An envelope key that is present is left alone rather than cross-checked
    # against the parameter. They are two different handles for two different
    # layers and a caller may legitimately set both; the parameter still reaches
    # the provider, so neither is dropped and neither answers for the other.
    idempotency_key = payload.get("idempotency_key")
    if not idempotency_key and _mutating(control_actions()[action]):
        idempotency_key = params.get("idempotency_key") or params.get(
            "turn_idempotency_key")

    return AgentControlRequest(
        action=action, device_ref=str(payload["device_ref"]),
        agent_runtime=str(payload["agent_runtime"]), params=params,
        context_ref=payload.get("context_ref"), approvals=tuple(approvals),
        workspace=payload.get("workspace"), work_item=payload.get("work_item"),
        session_ref=payload.get("session_ref"),
        session_role=payload.get("session_role"),
        bound_item_ref=payload.get("bound_item_ref"),
        correlation_id=str(payload.get("correlation_id") or ""),
        idempotency_key=idempotency_key,
        timeout_s=float(payload.get("timeout_s") or 60.0),
    )


def _derived_params(action: str) -> set:
    """Parameters an action accepts from the request envelope instead of `params`.

    `provider_id` is the resolved target's, and `session_ref` is the envelope's.
    Requiring them twice would give a caller two places to be inconsistent with
    itself, and the second one wins depending on which the transport read.
    """
    return {"provider_id"} if action == "agent.session.create" else set()


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", help="a JSON file holding one request")
    parser.add_argument("--local-device", default=os.environ.get("SERVEROS_DEVICE_REF"))
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    if not args.request:
        parser.error("--request is required: this tool will not invent a request")
    if not args.local_device:
        parser.error("--local-device (or SERVEROS_DEVICE_REF) is required: the "
                     "router is the control plane of a named device, and it will "
                     "not derive that from the hostname")
    payload = json.loads(Path(args.request).read_text(encoding="utf-8"))
    router = FleetAgentControlRouter(local_device_ref=args.local_device)
    response = router.route(request_from_payload(payload))
    json.dump(response.show(), sys.stdout, indent=2, sort_keys=True, default=str)
    sys.stdout.write("\n")
    return 0 if response.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
