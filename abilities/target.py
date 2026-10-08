#!/usr/bin/env python3
"""Resolve a semantic Agent Control request to one addressable target, or say why not.

    python3 target.py --request request.json
    python3 target.py --device device:server-beijing --runtime agent-runtime:claude-code \\
        --action session.create --evidence evidence.json

The eight steps are declared in `abilities/target-contract.yaml` and evaluated
here in that order. This module owns no capability rule of its own: it calls
`abilities/resolver.py` for the capability intersection, `provider_instance.py`
for readiness, `runtime/agent_sessions.py` for support and admission,
`runtime/approval.py` for the human gate, `context/actor.py` for identity, and
`fleet/transport.py` for device identity. A copy of any of those vocabularies
here would be a second answer to a question that already has one.

What this module is NOT
-----------------------
It reads one file: its own contract, to assert that the vocabulary below is the
declared one. It opens no socket, runs no probe, reads no clock it was not
handed, and knows nothing about a provider's native protocol. Everything that
touches a machine arrives as a record the caller gathered — which is what makes
a three-device fleet resolvable inside a unit test.

The order is the contract
-------------------------
Each step runs only after every earlier step agreed, and the FIRST step that
refuses names the reason. That is not a convenience. It is the difference
between four failures that are repaired four different ways and one word that
means nothing: a provider that cannot do a verb is fixed by choosing another
provider, a provider that is down is fixed by repairing a machine, an unapproved
request is fixed by asking a person, and a device with no node is fixed by
deploying one. A record that reports all four as "not available" has told its
reader to start with the wrong one.

An input nobody supplied is not an input that agreed
-----------------------------------------------------
Two steps — `adapter` and `route` — depend on evidence a caller may not have
gathered. Where such a step could not be evaluated AND nothing refused, the
target is UNKNOWN rather than AVAILABLE. That rule is copied from
`abilities/resolver.py`, which reintroduced its own founding defect one level
down by letting absent inputs read as assenting ones; the same shape is
available here and is closed the same way. UNKNOWN and UNAVAILABLE are never
collapsed: one means go and supply the missing record, the other means stop.
"""
from __future__ import annotations

import argparse
import datetime
import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote, unquote

import yaml

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parent
_RUNTIME = _REPO / "runtime"
_CONTEXT = _REPO / "context"
_FLEET = _REPO / "fleet"

SCHEMA = "serveros/agent-control-target.v1"
CONTRACT = _HERE / "target-contract.yaml"
RUNTIME_CONTRACT = _HERE / "agent-runtime-contract.yaml"
SESSION_CONTRACT = _RUNTIME / "agent-session-contract.yaml"
PROVIDER_CONTRACT = _HERE / "provider-contract.yaml"
TRANSPORT_CONTRACT = _FLEET / "transport-contract.yaml"


class TargetError(RuntimeError):
    """The kernel cannot run. Never a resolution verdict."""


class Rejected(ValueError):
    """The request cannot be answered — as opposed to "the answer is no"."""


# ---------------------------------------------------------------------------
# Sibling kernels, loaded by path.
#
# The `sys.modules` guard is not an optimisation. Loading one file twice under
# two names produces two distinct classes, and an `except` clause written
# against one of them silently misses the other — which is how the daemon once
# failed to catch its own `Rejected`.
# ---------------------------------------------------------------------------

def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise TargetError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resolver = _load("serveros_ability_resolver", _HERE / "resolver.py")
provider_instance = _load("serveros_abilities_provider_instance",
                          _HERE / "provider_instance.py")
sessions = _load("serveros_runtime_agent_sessions", _RUNTIME / "agent_sessions.py")
approval = _load("serveros_runtime_approval", _RUNTIME / "approval.py")
transport = _load("serveros_fleet_transport", _FLEET / "transport.py")
actor = _load("_serveros_context_actor", _CONTEXT / "actor.py")


# ---------------------------------------------------------------------------
# Vocabulary, read from the contracts rather than restated.
# ---------------------------------------------------------------------------

def contract() -> dict:
    data = yaml.safe_load(CONTRACT.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise TargetError(f"{CONTRACT} must contain a mapping")
    return data


def steps_in_order() -> tuple[str, ...]:
    return tuple(contract()["steps"]["order"])


def reason_vocabulary() -> tuple[str, ...]:
    return tuple(contract()["reasons"]["values"])


def statuses() -> tuple[str, ...]:
    return tuple(contract()["record"]["status"]["values"])


def runtime_ids() -> tuple[str, ...]:
    data = yaml.safe_load(RUNTIME_CONTRACT.read_text(encoding="utf-8")) or {}
    return tuple(data["ids"])


# The provider contract's own reason tokens, translated onto the refusal
# vocabulary above. A translation, not a collapse: the provider's word is kept
# verbatim in `readiness.reason` and this mapping only answers the narrower
# question "which of the router's words is it", so a caller that has one list
# to switch on does not have to learn two.
PROVIDER_REASON_TRANSLATION = {
    "AGENT_UNSUPPORTED": "AGENT_RUNTIME_UNAVAILABLE",
    "DEVICE_DEPENDENCY_MISSING": "PROVIDER_UNAVAILABLE",
    "RUNTIME_UNHEALTHY": "PROVIDER_UNAVAILABLE",
    "POLICY_DENIED": "POLICY_DENIED",
    "NETWORK_UNAVAILABLE": "DEVICE_UNREACHABLE",
    "PROVIDER_NOT_INSTALLED": "PROVIDER_UNAVAILABLE",
    "EXECUTION_UNVERIFIED": "READINESS_UNKNOWN",
    "EXECUTION_FAILED": "PROVIDER_UNAVAILABLE",
}

# Which transport capability level a route needs, by admission class. Read off
# `runtime/agent-session-contract.yaml#admission`, which decides the class, so
# there is one place where "is this a delegation" is answered.
ROUTE_LEVEL = {
    "read_only": "inspect",
    "delegation": "execute-safe",
    "human_gated": "execute-safe",
}

# The status a target takes when a step refuses, by the step that refused. A
# refusal is not always UNAVAILABLE: an unsupported verb is INCOMPATIBLE —
# waiting does not repair it and another machine will not either — and that
# distinction is the one thing a consumer needs to decide what to do next.
STEP_STATUS = {
    "device": "UNAVAILABLE",
    "agent_runtime": "UNAVAILABLE",
    "provider_instance": "UNAVAILABLE",
    "adapter": "INCOMPATIBLE",
    "support": "INCOMPATIBLE",
    "readiness": "UNAVAILABLE",
    "admission": "UNAVAILABLE",
    "route": "UNAVAILABLE",
}


# ---------------------------------------------------------------------------
# Refs
# ---------------------------------------------------------------------------

def runtime_ref(runtime_id: str) -> str:
    return "agent-runtime:" + str(runtime_id)


def parse_runtime_ref(ref: str) -> str:
    """The id inside an `agent-runtime:<id>` ref, or a refusal.

    Refuses anything that is not that shape. A cliId, a container name, a tmux
    pane, a pid, a binary path and a provider's bot id are all things ServerOS
    observes ABOUT a runtime; none of them is its identity, and a resolver that
    accepted one by resemblance would produce a record that cannot be audited.
    """
    prefix = "agent-runtime:"
    value = str(ref or "")
    if not value.startswith(prefix):
        raise Rejected(
            f"{value!r} is not an Agent Runtime ref. A runtime is named "
            f"`{prefix}<id>` where id is declared in "
            f"{RUNTIME_CONTRACT.name}#ids ({', '.join(runtime_ids())}). A cliId, "
            f"a container name, a tmux pane, a pid and a bot id are observations "
            f"about a runtime, not its identity."
        )
    runtime_id = value[len(prefix):]
    if runtime_id not in runtime_ids():
        raise Rejected(
            f"{value!r} names runtime {runtime_id!r}, which "
            f"{RUNTIME_CONTRACT.name} does not declare. Known: "
            f"{', '.join(runtime_ids())}.")
    return runtime_id


def provider_instance_ref(device_ref: str, provider_id: str) -> str:
    return "provider-instance:" + "/".join(
        quote(str(value), safe="") for value in (device_ref, provider_id))


def parse_provider_instance_ref(ref: str) -> tuple[str, str]:
    prefix = "provider-instance:"
    if not str(ref or "").startswith(prefix):
        raise Rejected(f"{ref!r} is not a Provider Instance ref")
    parts = str(ref)[len(prefix):].split("/")
    if len(parts) != 2 or not all(parts):
        raise Rejected(f"malformed Provider Instance ref: {ref!r}")
    return tuple(unquote(part) for part in parts)  # type: ignore[return-value]


def adapter_ref(runtime_id: str, provider_id: str) -> str:
    """`adapter:<runtime>/<provider>`, the pattern `provider-contract.yaml` declares."""
    return f"adapter:{runtime_id}/{provider_id}"


def now_utc(clock=None) -> str:
    moment = clock() if clock else datetime.datetime.now(datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc).isoformat().replace("+00:00", "Z")


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _refusal(status: str, reason: str, detail: str, **extra) -> dict:
    record = {"status": status, "reason": reason, "detail": detail}
    record.update(extra)
    return record


def _agreed(detail: str, **extra) -> dict:
    record = {"status": None, "reason": None, "detail": detail}
    record.update(extra)
    return record


def _unevaluable(detail: str, **extra) -> dict:
    record = {"status": "UNKNOWN", "reason": None, "detail": detail}
    record.update(extra)
    return record


def _actions() -> tuple[str, ...]:
    data = yaml.safe_load(SESSION_CONTRACT.read_text(encoding="utf-8")) or {}
    return tuple((data.get("support") or {}).get("actions") or ())


def _instance_for(instances, provider_id: str, device_ref: str) -> dict | None:
    """A device-scoped resolution finds a device-scoped instance or nothing.

    An instance with no `device_ref` is not a match for a device we asked
    about: it is an observation from somewhere, and reading it as this
    machine's is how one device's capability becomes another's claim. Same rule
    as `abilities/resolver.py`, applied to the same record.
    """
    for instance in instances or []:
        if instance.get("provider") != provider_id:
            continue
        if instance.get("device_ref") != device_ref:
            continue
        return instance
    return None


def _support_state(instance: dict, action: str) -> tuple[str, str | None]:
    """(SUPPORTED | UNSUPPORTED | UNKNOWN, provider's own reason)."""
    table = instance.get("action_support")
    if not isinstance(table, dict):
        return "UNKNOWN", "the provider instance reports no action_support table"
    entry = table.get(action)
    if entry is None:
        # Absent from a table the provider DID publish is not the same as a
        # provider that published nothing. A closed vocabulary with a hole in
        # it is a "no" for the hole; a missing table is "nobody asked".
        return "UNSUPPORTED", f"{action} is not in the provider's action_support table"
    if isinstance(entry, str):
        return entry, None
    if isinstance(entry, dict):
        return str(entry.get("state") or "UNKNOWN"), entry.get("reason")
    return "UNKNOWN", f"unreadable action_support entry for {action}"


def _staleness(instance: dict, reference: str | None) -> bool:
    """Is this instance's observation older than the readiness window?

    `provider_instance`'s ageing helpers take a record with an `observed_at`
    attribute — they are written against `runtime/health.py`'s Observation. An
    instance record is the same fact in its declared dict form, so it is handed
    over in that shape rather than the check being written a second time here.
    """
    shim = SimpleNamespace(observed_at=instance.get("observed_at"))
    return provider_instance.is_stale(shim, reference)


# ---------------------------------------------------------------------------
# The steps. Each takes the request and a mutable context and returns a verdict
# record. None of them raises for "no" — a resolution that cannot be reached is
# an answer, and only a malformed request is an error.
# ---------------------------------------------------------------------------

def _step_device(request: dict, ctx: dict) -> dict:
    device_ref = ctx["device_ref"]
    registry = request.get("registry") or transport.DeviceRegistry()
    try:
        device = registry.resolve(device_ref)
    except transport.UnknownDevice as exc:
        # An unenrolled ref is a caller mistake, not a runtime fact. The fleet
        # registry is authored; nothing derives an enrolment from an endpoint,
        # so there is nothing here to go and observe — only something to fix.
        raise Rejected(
            f"{device_ref!r} is not an enrolled device. Enrolment is a human "
            f"decision recorded in fleet/registry.yaml; a request cannot "
            f"perform one.") from exc

    ctx["device"] = device
    ctx["device_capabilities"] = dict(request.get("device_capabilities") or {})
    ctx["profile"] = request.get("profile")

    local = request.get("local_device_ref")
    presence = (request.get("presence") or {}).get(device_ref) or {}
    node_runtime = (request.get("node_runtime") or {}).get(device_ref) or {}
    ctx["presence"] = presence
    ctx["node_runtime"] = node_runtime

    if local and str(local) == device_ref:
        ctx["route_kind"] = "local"
        ctx["route_why"] = "this request was resolved on the device it names"
        return _agreed(
            f"{device_ref} is the local device",
            observed_at=None, source="request.local_device_ref")

    if not local:
        # Nothing in this kernel derives which machine it is running on. A
        # resolver that read a hostname would answer differently in a
        # container, and the difference would be invisible in the record.
        ctx["route_kind"] = "UNKNOWN"
        ctx["route_why"] = "the caller did not state which device this resolution runs on"
        return _unevaluable(
            "local_device_ref was not supplied, so a remote target cannot be "
            "distinguished from a local one",
            unevaluable_because="local_device_ref")

    # A deployed node is the definite fact §13 asks for: absence of a ServerOS
    # runtime on the target is a deployment state, not an unknown device.
    if node_runtime.get("state") == "ABSENT":
        ctx["route_kind"] = "none"
        ctx["route_why"] = "no ServerOS node runtime is deployed on that device"
        return _refusal(
            "UNAVAILABLE", "NODE_RUNTIME_UNAVAILABLE",
            f"no ServerOS node runtime is deployed on {device_ref}, so there is "
            f"nothing there to route an Agent Session request to",
            node_runtime=node_runtime.get("provenance") or "unstated")

    if presence.get("state") == "KNOWN_OFFLINE":
        ctx["route_kind"] = "none"
        ctx["route_why"] = "the fleet plane reports the device offline"
        return _refusal(
            "UNAVAILABLE", "DEVICE_UNREACHABLE",
            f"the fleet plane reports {device_ref} offline",
            presence=presence.get("provenance") or "unstated")

    if node_runtime.get("state") == "RUNNING" or presence.get("state") == "KNOWN_ONLINE":
        ctx["route_kind"] = "fleet"
        ctx["route_why"] = "a node runtime is deployed and the fleet plane sees the device"
        return _agreed(
            f"{device_ref} is reachable through the fleet plane",
            observed_at=node_runtime.get("observed_at") or presence.get("observed_at"),
            source=node_runtime.get("provenance") or presence.get("provenance")
            or "unstated")

    ctx["route_kind"] = "UNKNOWN"
    ctx["route_why"] = ("nothing has reported whether that device is reachable; "
                        "absence of evidence is not absence of a device")
    return _unevaluable(
        f"presence and node deployment are both unobserved for {device_ref}",
        unevaluable_because="presence")


def _step_agent_runtime(request: dict, ctx: dict) -> dict:
    runtime_id = ctx["runtime_id"]
    device = ctx["device"]
    if runtime_id not in tuple(device.agents):
        return _refusal(
            "UNAVAILABLE", "AGENT_RUNTIME_UNAVAILABLE",
            f"{ctx['device_ref']} does not declare the {runtime_id} runtime; "
            f"it declares {', '.join(device.agents) or 'none'}",
            declared=list(device.agents))
    ctx["runtime_id"] = runtime_id
    return _agreed(
        f"{ctx['device_ref']} declares {runtime_id}",
        observed_at=None, source="fleet/registry.yaml#agents")


def _instance_prerequisite(ctx: dict, what: str) -> dict | None:
    """The verdict for a step whose provider instance was never resolved.

    An unevaluable step does not stop the chain — a definite refusal further
    down outranks an indefinite answer above it, which is why the device step
    can be unevaluable and the provider step still be able to say no. The price
    of that rule is that a later step may run without the output of an earlier
    one, and each of them has to say so rather than reach for a key that was
    never set. This is that sentence, in one place, so the four steps that
    depend on the same two fields cannot disagree about how to say it.
    """
    if ctx.get("instance") is not None and ctx.get("provider_id") is not None:
        return None
    return _unevaluable(
        f"no provider instance was resolved, so {what} cannot be established",
        unevaluable_because="provider_instance")


def _step_provider_instance(request: dict, ctx: dict) -> dict:
    providers = request.get("providers")
    instances = request.get("instances")
    wanted = request.get("provider_id")
    if providers is None:
        return _unevaluable(
            "no provider rows were supplied, so no instance can be chosen",
            unevaluable_because="providers")

    rows = [p for p in providers if p.get("id")]
    if wanted:
        rows = [p for p in rows if p["id"] == wanted]
    rows.sort(key=lambda p: (p.get("preference", 100), p["id"]))

    candidates = []
    for row in rows:
        if instances is None:
            candidates.append({
                "provider": row["id"], "status": "UNKNOWN", "reason": None,
                "detail": "no provider instance records were supplied",
                "instance": None})
            continue
        instance = _instance_for(instances, row["id"], ctx["device_ref"])
        if instance is None:
            candidates.append({
                "provider": row["id"], "status": "UNAVAILABLE",
                "reason": "PROVIDER_UNAVAILABLE",
                "detail": f"no instance of {row['id']} is registered on "
                          f"{ctx['device_ref']}",
                "instance": None})
            continue
        candidates.append({
            "provider": row["id"], "status": "AVAILABLE", "reason": None,
            "detail": f"an instance of {row['id']} is registered on "
                      f"{ctx['device_ref']}",
            "instance": instance, "provider_row": row})

    # Compacted once, here, rather than at each return. The full records under
    # a candidate are evidence about one provider, and a resolution is echoed
    # to an agent and into a disclosure surface; shedding them in the happy
    # path only would have been a coincidence of which line ran.
    ctx["candidates"] = _compact(candidates)
    usable = [c for c in candidates if c["status"] == "AVAILABLE"]
    if not usable:
        if not candidates:
            return _refusal(
                "UNAVAILABLE", "PROVIDER_UNAVAILABLE",
                f"no provider implements {ctx['action']}"
                + (f" and {wanted!r} was pinned" if wanted else ""))
        definite = [c for c in candidates if c["status"] != "UNKNOWN"]
        if definite:
            first = definite[0]
            return _refusal("UNAVAILABLE", "PROVIDER_UNAVAILABLE", first["detail"],
                            candidates=ctx["candidates"])
        return _unevaluable(candidates[0]["detail"], candidates=ctx["candidates"])

    chosen = usable[0]
    ctx["provider_row"] = chosen.get("provider_row") or next(
        (p for p in rows if p["id"] == chosen["provider"]), {})
    ctx["instance"] = chosen["instance"]
    ctx["provider_id"] = chosen["provider"]
    ctx["provider_instance_ref"] = provider_instance_ref(ctx["device_ref"],
                                                         chosen["provider"])
    return _agreed(
        f"{chosen['provider']} is instantiated on {ctx['device_ref']}",
        observed_at=(ctx["instance"] or {}).get("observed_at"),
        source="provider instance record",
        candidates=ctx["candidates"])


def _compact(candidates: list[dict]) -> list[dict]:
    """The candidate list without the full records under it.

    The resolution record is read by an agent and echoed into a disclosure
    surface; carrying a complete provider instance under every candidate would
    make one resolution as large as the whole readiness plane. What a reader
    needs is which providers were considered and what each one's answer was.
    """
    return [{k: c.get(k) for k in ("provider", "status", "reason", "detail")}
            for c in candidates]


def _step_adapter(request: dict, ctx: dict) -> dict:
    blocked = _instance_prerequisite(ctx, "whether that runtime can consume it")
    if blocked:
        return blocked
    row = ctx.get("provider_row") or {}
    runtime_id = ctx["runtime_id"]
    declared = row.get("adapters")
    ctx["adapter_ref"] = adapter_ref(runtime_id, ctx["provider_id"])

    if declared:
        if runtime_id not in tuple(declared):
            return _refusal(
                "INCOMPATIBLE", "AGENT_RUNTIME_UNAVAILABLE",
                f"{ctx['provider_id']} declares adapters for "
                f"{', '.join(declared)} and not for {runtime_id}; the runtime "
                f"cannot consume this provider, which is AGENT_UNSUPPORTED in "
                f"the provider contract's own word",
                provider_reason="AGENT_UNSUPPORTED")
        return _agreed(
            f"{ctx['provider_id']} declares an adapter for {runtime_id}",
            observed_at=None, source="provider row#adapters")

    runtime_record = request.get("runtime_record")
    if runtime_record is None:
        return _unevaluable(
            "the provider declares no adapter list and no Agent Runtime record "
            "was supplied, so consumability cannot be established",
            unevaluable_because="runtime_record")

    consumable = tuple(runtime_record.get("consumable_provider_kinds") or ())
    kind = row.get("kind")
    if kind and kind not in consumable:
        return _refusal(
            "INCOMPATIBLE", "AGENT_RUNTIME_UNAVAILABLE",
            f"runtime {runtime_id!r} cannot consume a {kind!r} provider",
            provider_reason="AGENT_UNSUPPORTED")
    return _agreed(
        f"runtime {runtime_id!r} can consume a {kind!r} provider",
        observed_at=None, source="agent-runtime-contract.yaml#capabilities")


def _step_support(request: dict, ctx: dict) -> dict:
    blocked = _instance_prerequisite(ctx, "whether the provider declares it")
    if blocked:
        return blocked
    state, reason = _support_state(ctx["instance"], ctx["action"])
    ctx["support"] = {"state": state}
    if reason:
        ctx["support"]["reason"] = reason
    ctx["support"]["provider"] = ctx["provider_id"]

    if state == "SUPPORTED":
        return _agreed(f"{ctx['provider_id']} declares {ctx['action']} SUPPORTED",
                       observed_at=ctx["instance"].get("observed_at"),
                       source="provider instance#action_support")
    if state == "UNSUPPORTED":
        # INCOMPATIBLE, not UNAVAILABLE. Waiting does not repair it and another
        # machine will not either: the verb does not exist for this provider,
        # and the repair is a different provider or a different verb. A
        # stronger provider verb never satisfies a narrower ServerOS one, so
        # nothing here may substitute one.
        return _refusal(
            "INCOMPATIBLE", "ACTION_UNSUPPORTED",
            f"{ctx['provider_id']} does not support {ctx['action']}"
            + (f": {reason}" if reason else ""),
            support=state)
    # UNKNOWN shares the reason word because the closed vocabulary has no word
    # for "nobody asked the provider", and the distinction is not lost: it is
    # carried in `support.state` and in the target status below.
    return _refusal(
        "UNKNOWN", "ACTION_UNSUPPORTED",
        f"whether {ctx['provider_id']} supports {ctx['action']} is unestablished"
        + (f": {reason}" if reason else ""),
        support=state)


def _step_readiness(request: dict, ctx: dict) -> dict:
    blocked = _instance_prerequisite(ctx, "whether it would work if asked now")
    if blocked:
        return blocked
    instance = ctx["instance"]
    reference = ctx.get("reference")
    status = str(instance.get("status") or "UNKNOWN")
    provider_reason = instance.get("reason")

    if status in ("UNAVAILABLE", "INCOMPATIBLE") and not _staleness(instance, reference):
        ctx["readiness"] = {
            "status": status, "reported_status": status, "reason": provider_reason,
            "reason_text": instance.get("reason_text"),
            "observed_at": instance.get("observed_at"),
            "fresh": True, "provider": ctx["provider_id"],
            "instance_ref": ctx["provider_instance_ref"],
        }
        return _refusal(
            "UNAVAILABLE",
            PROVIDER_REASON_TRANSLATION.get(provider_reason or "", "PROVIDER_UNAVAILABLE"),
            f"{ctx['provider_id']} on {ctx['device_ref']} is {status}"
            + (f" ({provider_reason})" if provider_reason else ""),
            readiness_state=status)

    # A stale observation is not a verdict. `provider_instance`'s own rule: an
    # old failure falls back to UNKNOWN and never to AVAILABLE, because ageing
    # a negative into a positive is the one way this mechanism could start
    # lying — and a provider that has since been repaired would otherwise stay
    # dead forever.
    stale = _staleness(instance, reference)
    fresh_status = "UNKNOWN" if stale else status
    ctx["readiness"] = {
        # `status` answers "would it work now"; `reported_status` is what the
        # provider last said, kept verbatim even when the two differ. Without
        # it, ageing a stale AVAILABLE to UNKNOWN would discard the only fact
        # anyone has about that provider — and a reader could not tell a
        # provider that was last seen working from one that was never observed.
        "status": fresh_status, "reported_status": status,
        "reason": provider_reason,
        "reason_text": instance.get("reason_text"),
        "observed_at": instance.get("observed_at"),
        "fresh": not stale, "provider": ctx["provider_id"],
        "instance_ref": ctx["provider_instance_ref"],
    }

    if stale:
        return _unevaluable(
            f"the last observation of {ctx['provider_id']} on {ctx['device_ref']} "
            f"is older than the readiness window; it establishes nothing about now",
            unevaluable_because="readiness")
    if fresh_status == "UNKNOWN":
        return _unevaluable(
            f"{ctx['provider_id']} on {ctx['device_ref']} reports no readiness"
            + (f" ({provider_reason})" if provider_reason else ""),
            unevaluable_because="readiness")
    if fresh_status == "DEGRADED":
        return _agreed(
            f"{ctx['provider_id']} is DEGRADED and usable",
            observed_at=instance.get("observed_at"),
            source="provider instance#status", degraded=True)
    return _agreed(
        f"{ctx['provider_id']} is AVAILABLE on {ctx['device_ref']}",
        observed_at=instance.get("observed_at"),
        source="provider instance#status")


def _step_admission(request: dict, ctx: dict) -> dict:
    action = ctx["action"]
    decision = sessions.admission(action, {
        "device_ref": ctx["device_ref"],
        "agent_runtime": ctx["agent_runtime"],
        "workspace": request.get("workspace"),
        "work_item": request.get("work_item"),
        "session_ref": request.get("session_ref"),
    })
    ctx["admission"] = decision
    ctx["admission_required"] = decision["decision"] == "REQUIRE_HUMAN"

    if decision["decision"] == "DENY":
        return _refusal(
            "UNAVAILABLE", "POLICY_DENIED",
            f"{action} is not declared by the Agent Session contract",
            admission=decision["decision"])

    if decision["decision"] == "ALLOW":
        return _agreed(
            f"{action} is admitted as {decision['class']}",
            observed_at=None, source="agent-session-contract.yaml#admission")

    # -- REQUIRE_HUMAN. The gate is a policy decision and the approval is a
    # -- separate fact about whether a person already made it.
    context = request.get("context")
    if context is None:
        if request.get("context_ref"):
            return _refusal(
                "UNAVAILABLE", "INVALID_CONTEXT",
                "a context_ref was presented and no context was resolved from "
                "it. Identity is resolved against what ServerOS minted; a ref "
                "on its own is a claim, not evidence.",
                admission=decision["decision"])
        return _refusal(
            "UNAVAILABLE", "APPROVAL_REQUIRED",
            f"{action} is human-gated and no trusted actor context was presented",
            admission=decision["decision"])

    try:
        # The REQUEST may not carry identity, and the CONTEXT may not be a
        # request. `refuse_if_forged` is applied to the request only: a minted
        # context record legitimately HAS an `actor_ref` field, and running the
        # forbidden-key sweep over it would refuse every valid context — which
        # is how a guard that is correct in one place becomes a guard that
        # silently forbids the thing it was written to permit.
        actor.refuse_if_forged(request)
        record = actor.validate(context)
    except actor.Rejected as exc:
        return _refusal("UNAVAILABLE", "INVALID_CONTEXT", str(exc),
                        admission=decision["decision"])

    verdict = actor.authorizes(record, ctx["reference"])
    if not verdict["authorizes"]:
        return _refusal(
            "UNAVAILABLE", "UNTRUSTED_ACTOR",
            f"the presented context does not authorize a mutation: {verdict['because']}",
            admission=decision["decision"], context_ref=record["context_ref"])

    ctx["context_ref"] = record["context_ref"]
    ctx["actor_ref"] = record["actor_ref"]

    # An approval binds a provider, so a request whose provider was never
    # resolved has nothing for a person to have approved. Asking
    # `approval.decide` anyway would compare the presented approvals against a
    # target with a null provider — an approval for some other provider's
    # request could match it, which is the binding failing open.
    blocked = _instance_prerequisite(ctx, "what an approval would be bound to")
    if blocked:
        return blocked

    try:
        decided = approval.decide(
            approvals=list(request.get("approvals") or []),
            request={
                "action": action,
                "actor_ref": record["actor_ref"],
                "target": {
                    "device_ref": ctx["device_ref"],
                    "session_ref": request.get("session_ref"),
                    "agent_runtime": ctx["agent_runtime"],
                    "provider": ctx["provider_id"],
                },
                "params": dict(request.get("params") or {}),
            },
            context_ref=record["context_ref"],
            now=ctx["reference"])
    except approval.Rejected as exc:
        return _refusal("UNAVAILABLE", "INVALID_CONTEXT", str(exc),
                        admission=decision["decision"])

    ctx["approval"] = decided
    if decided.get("approval_ref"):
        # Surfaced through the context rather than as an extra on this step's
        # verdict, because an ALLOW verdict does not refuse and `resolve` keeps
        # extras only from the step that stopped the chain. An approval that
        # allowed a mutation and left no ref in the record would be the one
        # audit link §22 asks for, dropped on the success path.
        ctx["approval_ref"] = decided["approval_ref"]
    if decided["decision"] == "ALLOW":
        return _agreed(
            f"an identified human approved exactly this request "
            f"({decided['approval_ref']})",
            observed_at=None, source="approval record",
            approval_ref=decided["approval_ref"])
    return _refusal(
        "UNAVAILABLE", decided["decision"],
        decided.get("reason") or "the approval does not authorize this request",
        admission=decision["decision"],
        approval_ref=decided.get("approval_ref"))


def _step_route(request: dict, ctx: dict) -> dict:
    kind = ctx.get("route_kind") or "UNKNOWN"
    admission_class = (ctx.get("admission") or {}).get("class") or "read_only"
    level = ROUTE_LEVEL.get(admission_class, "inspect")

    if kind == "local":
        ctx["route"] = {"kind": "local", "device_ref": ctx["device_ref"],
                        "level": level, "why": ctx["route_why"]}
        return _agreed(f"route: local ({level})", observed_at=None,
                       source="request.local_device_ref")

    if kind == "fleet":
        ctx["route"] = {"kind": "fleet", "device_ref": ctx["device_ref"],
                        "level": level, "why": ctx["route_why"]}
        return _agreed(f"route: fleet ({level})", observed_at=None,
                       source="fleet plane presence")

    ctx["route"] = {"kind": "none", "device_ref": ctx["device_ref"],
                    "level": level, "why": ctx["route_why"]}
    # `route_kind` is "none" when a step already refused and "UNKNOWN" when
    # nothing did. Only the second is this step's own finding, and it is not a
    # refusal: this runtime has not observed the device, which is different
    # from having observed it to be out of reach.
    if kind == "UNKNOWN":
        return _unevaluable(
            f"no route can be named: {ctx['route_why']}",
            unevaluable_because="route")
    return _refusal("UNAVAILABLE", "DEVICE_UNREACHABLE", ctx["route_why"])


STEP_FUNCTIONS = {
    "device": _step_device,
    "agent_runtime": _step_agent_runtime,
    "provider_instance": _step_provider_instance,
    "adapter": _step_adapter,
    "support": _step_support,
    "readiness": _step_readiness,
    "admission": _step_admission,
    "route": _step_route,
}


# ---------------------------------------------------------------------------
# The public entry point
# ---------------------------------------------------------------------------

def _validate_request(request: dict) -> None:
    if not isinstance(request, dict):
        raise Rejected("a target resolution request must be a mapping")
    for field in ("device_ref", "agent_runtime", "action"):
        if not request.get(field):
            raise Rejected(f"request.{field} is required")
    if not str(request["device_ref"]).startswith("device:"):
        raise Rejected(
            f"{request['device_ref']!r} is not a device ref. Fleet identity is "
            f"authored as `device:<id>`; a hostname, a relay uuid, an address "
            f"and an SSH alias are routes, and a record keyed on one answers "
            f"differently on the next machine.")


def resolve(request: dict) -> dict:
    """Resolve one Agent Control request to one target, or say why not.

    Returns the record shape declared in `abilities/target-contract.yaml`.
    Raises `Rejected` only for a malformed request — "no" is an answer, and it
    is returned rather than raised.
    """
    _validate_request(request)
    ctx = {
        "device_ref": str(request["device_ref"]),
        "agent_runtime": str(request["agent_runtime"]),
        "runtime_id": parse_runtime_ref(request["agent_runtime"]),
        "action": str(request["action"]),
        "reference": request.get("resolved_at") or now_utc(request.get("clock")),
        "admission_required": False,
    }

    record = {
        "schema": SCHEMA,
        "request": {
            "device_ref": ctx["device_ref"],
            "agent_runtime": ctx["agent_runtime"],
            "action": ctx["action"],
            "provider_id": request.get("provider_id"),
            "session_ref": request.get("session_ref"),
            "workspace": request.get("workspace"),
            "work_item": request.get("work_item"),
            "context_ref": request.get("context_ref"),
        },
        "target": {
            "device_ref": ctx["device_ref"],
            "agent_runtime": ctx["agent_runtime"],
            "provider_ref": None,
            "provider_instance_ref": None,
            "adapter_ref": None,
            "route": None,
        },
        "support": {"state": "UNKNOWN"},
        "readiness": None,
        "admission": None,
        "admission_required": False,
        "status": None,
        "reason": None,
        "detail": None,
        "resolved_at": ctx["reference"],
        "provenance": [],
        "steps": [],
    }

    refusal = None
    unevaluated = []

    for name in steps_in_order():
        verdict = STEP_FUNCTIONS[name](request, ctx)
        entry = {"step": name, "outcome": _outcome(verdict),
                 "detail": verdict["detail"]}
        if verdict.get("reason"):
            entry["reason"] = verdict["reason"]
        if verdict.get("unevaluable_because"):
            entry["unevaluable_because"] = verdict["unevaluable_because"]
        record["steps"].append(entry)

        if verdict.get("observed_at") is not None or verdict.get("source"):
            record["provenance"].append({
                "step": name,
                "field": _field_for(name),
                "source": verdict.get("source") or "unstated",
                "observed_at": verdict.get("observed_at"),
                "strength": "provider-response" if verdict.get("observed_at")
                else "none",
            })

        if entry["outcome"] == "REFUSED":
            # The first refusal wins and the chain stops. Later steps are not
            # run and are not reported as having agreed — a step that did not
            # run has no opinion, and recording one would be the record telling
            # its reader something nobody established.
            refusal = {"step": name, **verdict}
            break
        if entry["outcome"] == "UNEVALUABLE":
            unevaluated.append(name)

    record["support"] = ctx.get("support") or {"state": "UNKNOWN"}
    record["readiness"] = ctx.get("readiness")
    record["admission"] = ctx.get("admission")
    record["admission_required"] = bool(ctx.get("admission_required"))
    if ctx.get("provider_id"):
        record["target"]["provider_ref"] = f"provider:{_kebab(ctx['provider_id'])}" \
            if not str(ctx["provider_id"]).startswith("provider:") else ctx["provider_id"]
    record["target"]["provider_instance_ref"] = ctx.get("provider_instance_ref")
    record["target"]["adapter_ref"] = ctx.get("adapter_ref")
    record["target"]["route"] = ctx.get("route")
    record["candidates"] = ctx.get("candidates", [])
    if ctx.get("context_ref"):
        record["context_ref"] = ctx["context_ref"]
    if ctx.get("actor_ref"):
        record["actor_ref"] = ctx["actor_ref"]
    if ctx.get("approval_ref"):
        record["approval_ref"] = ctx["approval_ref"]

    if refusal is not None:
        # The status the STEP gives, not a single word for every refusal:
        # an unsupported verb is INCOMPATIBLE and waiting will not repair it,
        # a down provider is UNAVAILABLE and waiting might.
        status = refusal.get("status") or STEP_STATUS.get(refusal["step"], "UNAVAILABLE")
        record["status"] = status
        record["reason"] = refusal["reason"]
        record["detail"] = refusal["detail"]
        record["refused_by"] = refusal["step"]
        # Extras a refusing step attached are surfaced beside the refusal, so
        # the evidence for the refusal travels with it. `support`, `readiness`,
        # `admission` and `candidates` are NOT copied: those fields already
        # hold the structured records from the context, and overwriting one
        # with a one-word summary from the refusal is how a reader ends up
        # comparing a status against a status word.
        for key in ("node_runtime", "presence", "approval_ref",
                    "readiness_state", "support_state"):
            if key in refusal:
                record[key] = refusal[key]
        return record

    if unevaluated:
        record["status"] = "UNKNOWN"
        record["reason"] = None
        record["detail"] = ("no step refused, and these could not be evaluated: "
                            + ", ".join(unevaluated))
        record["unevaluated"] = unevaluated
        return record

    # DEGRADED is a positive answer with a caveat, never a refusal: the target
    # is addressable, and the provider said so in the only vocabulary that has
    # a word for "works, not as intended".
    record["status"] = ("DEGRADED"
                        if (ctx.get("readiness") or {}).get("status") == "DEGRADED"
                        else "AVAILABLE")
    record["reason"] = None
    record["detail"] = f"{record['target']['provider_ref']} would carry {ctx['action']}"
    return record


def _outcome(verdict: dict) -> str:
    if verdict.get("status") in (None, "AVAILABLE", "DEGRADED"):
        return "AGREED"
    if verdict.get("status") == "UNKNOWN":
        return "UNEVALUABLE"
    return "REFUSED"


def _field_for(step: str) -> str:
    return {
        "device": "target.device_ref",
        "agent_runtime": "target.agent_runtime",
        "provider_instance": "target.provider_instance_ref",
        "adapter": "target.adapter_ref",
        "support": "support",
        "readiness": "readiness",
        "admission": "admission",
        "route": "target.route",
    }.get(step, step)


def _kebab(value: str) -> str:
    return str(value).split(":", 1)[-1]


def summarize(record: dict) -> str:
    """One line, for a disclosure surface. Never a paragraph."""
    target = record.get("target") or {}
    if record["status"] in ("AVAILABLE", "DEGRADED"):
        route = (target.get("route") or {}).get("kind")
        return (f"{target.get('provider_ref')} on {target.get('device_ref')} "
                f"via {route} ({record['status'].lower()})")
    if record["status"] == "UNKNOWN":
        return (f"no target resolved for {target.get('device_ref')}: "
                f"{', '.join(record.get('unevaluated') or []) or 'nothing established'}")
    return (f"no target for {target.get('device_ref')}: "
            f"{record['reason']} ({record.get('refused_by')})")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", help="a JSON file holding one request")
    parser.add_argument("--device")
    parser.add_argument("--runtime", default="agent-runtime:claude-code")
    parser.add_argument("--action")
    parser.add_argument("--evidence", help="a JSON file holding the evidence records")
    args = parser.parse_args(argv)

    if args.request:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    else:
        if not (args.device and args.action):
            parser.error("--device and --action are required without --request")
        request = {"device_ref": args.device, "agent_runtime": args.runtime,
                   "action": args.action}
        if args.evidence:
            request.update(json.loads(Path(args.evidence).read_text(encoding="utf-8")))

    try:
        record = resolve(request)
    except Rejected as exc:
        print(json.dumps({"error": str(exc)}, indent=2), file=sys.stderr)
        return 2

    json.dump(record, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
