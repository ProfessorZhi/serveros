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
    """
    import yaml
    data = yaml.safe_load(RUNTIME_CONTRACT.read_text(encoding="utf-8")) or {}
    if runtime_id not in tuple(data.get("ids") or ()):
        return None
    return {
        "id": runtime_id,
        "consumable_provider_kinds": list(
            (data.get("capabilities") or {}).get("consumable_provider_kinds") or ()),
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

    def __init__(self, *, device_ref: str, service) -> None:
        if not str(device_ref).startswith("device:"):
            raise TargetError(
                f"{device_ref!r} is not a device ref; the loopback answers for one "
                f"device and a hostname is not the identity Fleet uses")
        self.device_ref = str(device_ref)
        self.service = service

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
            return service.create(
                provider_id=p.get("provider_id") or _provider_hint(resolved),
                agent_id=p["agent_id"], instruction=p["instruction"],
                idempotency_key=p["idempotency_key"], workspace=request.workspace)
        if request.verb == "session.send":
            return service.send(session_ref_value=request.session_ref,
                                instruction=p["instruction"],
                                turn_idempotency_key=p["turn_idempotency_key"])
        if request.verb == "session.result":
            return service.result(session_ref_value=request.session_ref,
                                  trigger_id=p.get("trigger_id"))
        if request.verb == "session.inspect":
            return service.session_inspect(request.session_ref)
        if request.verb == "session.resume":
            return service.resume(session_ref_value=request.session_ref)
        if request.verb == "session.interrupt":
            return service.interrupt(session_ref_value=request.session_ref)
        if request.verb == "session.stop":
            return service.stop(session_ref_value=request.session_ref)
        if request.verb == "session.handoff":
            return service.handoff(session_ref_value=request.session_ref)
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
                 registry=None, clock=None, context_store: dict | None = None) -> None:
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
        self.context_store = dict(context_store or {})
        self.transports = list(transports or [])
        # Single-flight replay for retries, in memory and bounded. Named for
        # what it is: an in-process guard against a caller retrying a mutation
        # within one router lifetime, NOT a durable exactly-once guarantee
        # across a restart. The limitation is declared in the contract rather
        # than left for a reader to assume the stronger thing.
        self._seen_keys: dict[str, dict] = {}
        self._max_seen = 512

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
            **self._resolved_context(request),
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

        replayed = self._replay(request)
        if replayed is not None:
            return replayed

        try:
            result = node.send(request, resolved=record)
        except TransportFailure as exc:
            # The transport's own vocabulary, verbatim. Nothing here converts a
            # delivery failure into a statement about the session on the far
            # side, because the transport learned nothing about it.
            return self._refuse(request, exc.reason, exc.detail, record=record,
                                retryable=exc.retryable)

        self._remember(request, result)
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

    def _replay(self, request: AgentControlRequest) -> AgentControlResponse | None:
        """A retry of the same intent, answered from the first attempt.

        Scoped to one router instance and to mutations. A read replayed from
        memory would answer with a stale observation, which is worse than
        answering slowly; a mutation replayed from memory is the one case where
        the caller's intent is that the second attempt have no second effect.
        """
        if not request.idempotency_key or not _mutating(request.verb):
            return None
        seen = self._seen_keys.get(request.idempotency_key)
        if seen is None:
            return None
        if seen["action"] != request.action or seen["device_ref"] != request.device_ref:
            # The same key on a different intent is a caller bug, and silently
            # answering it with the other call's result would report an effect
            # that never happened for this request.
            raise Rejected(
                f"idempotency key {request.idempotency_key!r} was already used for "
                f"{seen['action']} on {seen['device_ref']}; a key identifies one "
                f"intent, not one caller")
        return AgentControlResponse(
            ok=True, action=request.action, verb=request.verb,
            device_ref=request.device_ref, correlation_id=request.correlation_id,
            route=seen.get("route"), target=seen.get("target"),
            result=dict(seen["result"], idempotent_replay=True),
            provenance=tuple(seen.get("provenance") or ()),
            dispatched=False)

    def _remember(self, request: AgentControlRequest, result: dict) -> None:
        if not request.idempotency_key or not _mutating(request.verb):
            return
        self._seen_keys[request.idempotency_key] = {
            "action": request.action, "device_ref": request.device_ref,
            "result": result,
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

    def show(self) -> dict:
        return {
            "ok": self.ok, "reason": self.reason,
            "completed": list(self.completed), "stopped_at": self.stopped_at,
            "source_preserved": self.source_preserved,
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
    if not checkpoint:
        return HandoffOutcome(False, "INVALID_CONTEXT", tuple(completed),
                              "checkpoint", True,
                              {"why": "a handoff with nothing to hand over moves a "
                                      "session and leaves its state behind"})
    completed.append("checkpoint")

    created = router.route(create_request or _resume_request(request))
    if not created.ok:
        # Nothing was stopped. The source session is exactly as it was, and the
        # reason is the target's own — not a handoff-specific rewording of it.
        return HandoffOutcome(False, created.reason, tuple(completed),
                              _stop_step(created.reason), True,
                              {"target": created.show()})
    completed.extend(["resolve_target", "target_available", "target_ready"])

    handed = created.result or {}
    completed.append("context_handed_off")

    release = (request.params or {}).get("release_source")
    if not release:
        return HandoffOutcome(True, None, tuple(completed), None, True,
                              {"target": handed,
                               "why": "the source was not stopped: release_source "
                                      "was not requested, and a handoff that stops "
                                      "the source by default is a handoff that "
                                      "loses it whenever the target turns out to "
                                      "be unsuitable an hour later"})

    stopped = router.route(_stop_request(request))
    if not stopped.ok:
        return HandoffOutcome(False, stopped.reason, tuple(completed), "source_released",
                              True, {"target": handed, "stop": stopped.show(),
                                     "why": "the target is running and the source is "
                                            "still running; both exist and neither "
                                            "was lost"})
    completed.append("source_released")
    return HandoffOutcome(True, None, tuple(completed), None, False,
                          {"target": handed, "stop": stopped.result})


def _stop_step(reason: str | None) -> str:
    """Which step a failed handoff stopped at, named by what was not done."""
    return "resolve_target" if reason in ("DEVICE_UNREACHABLE",
                                          "NODE_RUNTIME_UNAVAILABLE") else "target_ready"


def _resume_request(request: AgentControlRequest) -> AgentControlRequest:
    action = (request.params or {}).get("target_action") or "agent.session.resume"
    return AgentControlRequest(
        action=action, device_ref=str((request.params or {}).get("to_device_ref") or ""),
        agent_runtime=request.agent_runtime,
        params=dict((request.params or {}).get("target_params") or {}),
        context_ref=request.context_ref, workspace=request.workspace,
        work_item=request.work_item,
        session_ref=(request.params or {}).get("to_session_ref"),
        correlation_id=request.correlation_id)


def _stop_request(request: AgentControlRequest) -> AgentControlRequest:
    return AgentControlRequest(
        action="agent.session.stop", device_ref=request.device_ref,
        agent_runtime=request.agent_runtime,
        params={}, context_ref=request.context_ref,
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
    stray = sorted(set(payload) - envelope)
    if stray:
        # The envelope is closed for the same reason `params` is, and this is
        # where a credential posted as a top-level field lands. Silently
        # dropping it would leave the caller believing it took effect; a
        # credential that is quietly ignored is a credential the caller thinks
        # is being used.
        raise Rejected(
            f"an Agent Control request does not take {stray}; it takes "
            f"{sorted(envelope)}. Credentials belong to the provider, not to a "
            f"routed request")

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

    return AgentControlRequest(
        action=action, device_ref=str(payload["device_ref"]),
        agent_runtime=str(payload["agent_runtime"]), params=params,
        context_ref=payload.get("context_ref"), approvals=tuple(approvals),
        workspace=payload.get("workspace"), work_item=payload.get("work_item"),
        session_ref=payload.get("session_ref"),
        correlation_id=str(payload.get("correlation_id") or ""),
        idempotency_key=payload.get("idempotency_key"),
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
