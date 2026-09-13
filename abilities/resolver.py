#!/usr/bin/env python3
"""Decide whether one device can do one thing, right now, and why.

    python3 resolver.py --request request.json
    python3 resolver.py --ability ability:test-management --device device:server-beijing \
        --runtime claude-code --inputs inputs.json

This is the ServerOS **Ability Resolver**. It is the third of the read-only
decision kernels, beside Context (what is my environment) and Space (where does
this belong). Ability answers "can this machine do this, and if not, why not".

    EffectiveAbility =  AbilityDefinition
                      ∩ DeviceCapabilities
                      ∩ DeviceProfile
                      ∩ DeviceBinding
                      ∩ AgentRuntimeCapabilities
                      ∩ RuntimeHealth
                      ∩ Policy
                      ∩ TaskContext

What this module is NOT
-----------------------
It does not read a file, run a probe, open a socket, look at a clock, or know
what a Skill or an MCP server is. Everything that touches the machine is a
PROBE, performed by the caller, and passed in as a record. That is not
fastidiousness — it is what makes the resolution of a three-device fleet
assertable in a unit test, and it is the discipline that makes the answer
reproducible.

The intersection is not a union, and the direction is the whole point. The
defect that produced this module was a capability reported as available because
one input mentioned it while seven others were never consulted. An intersection
makes "no" the default and requires every input to agree before the answer is
"yes".

UNKNOWN is a real answer
------------------------
`UNKNOWN` and `UNAVAILABLE` call for opposite actions — go and resolve the
missing input, versus find another provider — so they are never collapsed. But
a definite refusal outranks an indefinite yes: if an input cannot be read and a
LATER input refuses, the refusal is the answer. Otherwise a device with nothing
installed reports UNKNOWN for everything, which reads as "we have not looked"
and hides the finding that nothing is there.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCHEMA = "serveros/capability-resolution.v1"

STATUSES = ("AVAILABLE", "DEGRADED", "UNAVAILABLE", "INCOMPATIBLE", "UNKNOWN")

REASONS = (
    "AGENT_UNSUPPORTED",
    "DEVICE_DEPENDENCY_MISSING",
    "RUNTIME_UNHEALTHY",
    "POLICY_DENIED",
    "NETWORK_UNAVAILABLE",
    "PROVIDER_NOT_INSTALLED",
)

# The eight inputs, in evaluation order. Order is what the REASON depends on:
# any input may refuse, and the first to refuse names why. Cheap, local and
# unambiguous inputs are first, so the common refusal is also the clear one.
INPUT_ORDER = (
    "ability",
    "device_capability",
    "device_profile",
    "device_binding",
    "agent_runtime",
    "runtime_health",
    "policy",
    "task_context",
)


class Rejected(ValueError):
    """The request cannot be answered — as opposed to "the answer is no"."""


# ---------------------------------------------------------------------------
# Helpers. Small, pure, and named after the contract line each implements.
# ---------------------------------------------------------------------------

def _path_role_bound(profile: dict | None, role: str) -> bool:
    """Is `role` BOUND on this device?

    Three states, three meanings, and only one of them is "yes". UNBOUND means
    the role applies here and no path is known — a real gap. NOT_APPLICABLE
    means the role has no meaning on this device. Neither is a bound path, and
    collapsing them would turn "nobody has looked" into "there is nothing here".
    """
    if not profile:
        return False
    entry = (profile.get("paths") or {}).get(role)
    return bool(entry) and entry.get("state") == "BOUND" and entry.get("path")


def _refusal(status: str, reason: str, detail: str, **extra) -> dict:
    record = {"status": status, "reason": reason, "detail": detail}
    record.update(extra)
    return record


def _provider_materializable(provider: dict, profile: dict | None) -> tuple[bool, str]:
    """input 3 — DeviceProfile. Where would it be, and is that location known?

    A provider declaring no on-disk footprint (`path_role: none`: a builtin or
    a remote service) has nothing to bind and passes here. Everything else must
    name a role that resolves to a BOUND path on this device.
    """
    materialization = provider.get("materialization") or {}
    role = materialization.get("path_role")
    if role in (None, "none"):
        return True, "the provider declares no on-disk footprint"
    if _path_role_bound(profile, role):
        return True, f"{role} is BOUND on this device"
    return False, (f"path role {role!r} is not BOUND in this device's profile, "
                   "so where the provider would live is not known here")


def _instance_for(instances, provider_id: str, device_ref: str | None) -> dict | None:
    for instance in instances or []:
        if instance.get("provider") != provider_id:
            continue
        # A device-scoped resolution must find a device-scoped instance. An
        # instance with no device_ref is not a match for a device we asked
        # about: it is an observation from somewhere, and reading it as this
        # device's is how one machine's capability becomes another's claim.
        if device_ref is not None and instance.get("device_ref") != device_ref:
            continue
        return instance
    return None


# ---------------------------------------------------------------------------
# Per-input evaluation. Each returns either None (input agreed) or a refusal.
# ---------------------------------------------------------------------------

def _input_ability(request: dict) -> dict | None:
    ability = request["ability"]
    catalogue = request.get("abilities") or {}
    if ability not in catalogue:
        return _refusal(
            "UNAVAILABLE", "PROVIDER_NOT_INSTALLED",
            f"{ability} is not in the pinned catalogue; a documented ability is "
            "not an available one")
    return None


def _input_device_capability(request: dict, provider: dict) -> dict | None:
    required = provider.get("requirements") or {}
    device = request.get("device_capabilities") or {}
    if not required:
        return None
    if not device:
        return None  # unevaluable; the caller records it in inputs_missing
    missing = sorted(k for k, v in required.items() if device.get(k) != v)
    if missing:
        reason = "NETWORK_UNAVAILABLE" if "network" in missing else "DEVICE_DEPENDENCY_MISSING"
        return _refusal(
            "UNAVAILABLE", reason,
            f"this device does not satisfy {', '.join(missing)}",
            missing=missing)
    return None


def _input_agent_runtime(request: dict, provider: dict) -> dict | None:
    runtime = request.get("runtime_record")
    if runtime is None:
        return None  # unevaluable
    consumable = runtime.get("consumable_provider_kinds") or []
    if provider.get("kind") not in consumable:
        return _refusal(
            "INCOMPATIBLE", "AGENT_UNSUPPORTED",
            f"runtime {runtime.get('id')!r} cannot consume a {provider.get('kind')!r} provider")
    return None


def _input_runtime_health(request: dict, provider: dict) -> dict | None:
    health = (request.get("health") or {}).get(provider["id"])
    if health is None:
        return None  # unevaluable
    if health.get("functional") == "FAILED":
        return _refusal(
            "UNAVAILABLE", "RUNTIME_UNHEALTHY",
            "the provider is installed and fails its functional probe")
    return None


def _input_policy(request: dict, ability: str) -> dict | None:
    policy = request.get("policy")
    if policy is None:
        return None
    denied = set(policy.get("denied_abilities") or ())
    if ability in denied or "*" in denied:
        return _refusal(
            "UNAVAILABLE", "POLICY_DENIED",
            f"policy denies {ability} on this device or for this Item")
    return None


def _evaluate_one(request: dict, ability: str, provider: dict) -> dict:
    """One provider, through the eight inputs in order. First refusal wins.

    `ability` is not evaluated here — it is not a property of a provider, and
    `resolve()` settles it once before any provider is considered. The loop
    still names it, because the ORDER is the contract and skipping a name in
    the loop is how a future edit reorders the rest without noticing.
    """
    for name in INPUT_ORDER:
        if name == "ability":
            continue
        if name == "device_capability":
            if not request.get("device_capabilities"):
                continue
            refusal = _input_device_capability(request, provider)
        elif name == "device_profile":
            ok, detail = _provider_materializable(provider, request.get("profile"))
            refusal = None if ok else _refusal(
                "UNAVAILABLE", "PROVIDER_NOT_INSTALLED", detail)
        elif name == "device_binding":
            instance = _instance_for(request.get("instances"), provider["id"],
                                     request.get("device_ref"))
            if instance is None and request.get("instances") is not None:
                refusal = _refusal(
                    "UNAVAILABLE", "PROVIDER_NOT_INSTALLED",
                    "no instance of this provider is registered on this device")
            else:
                refusal = None
        elif name == "agent_runtime":
            if request.get("runtime_record") is None:
                continue
            refusal = _input_agent_runtime(request, provider)
        elif name == "runtime_health":
            refusal = _input_runtime_health(request, provider)
        elif name == "policy":
            refusal = _input_policy(request, ability)
        else:  # task_context
            refusal = None

        if refusal is not None:
            refusal["refused_by"] = name
            return refusal

    # -- no input refused. Green, or green-with-a-caveat. -------------------
    caveats = []
    instance = _instance_for(request.get("instances"), provider["id"],
                             request.get("device_ref"))
    if instance and instance.get("floating_dependency"):
        caveats.append(_refusal(
            "DEGRADED", "RUNTIME_UNHEALTHY",
            "the provider resolves through an unpinned dependency, so it is "
            "not the same provider tomorrow",
            finding="PERSISTENT_RUNTIME_FLOATING_DEPENDENCY"))

    preferred = provider.get("preference", 100)
    if caveats:
        return {**caveats[0], "preference": preferred}
    return {"status": "AVAILABLE", "reason": None, "detail": None,
            "preference": preferred}


# ---------------------------------------------------------------------------
# The public entry point.
# ---------------------------------------------------------------------------

def resolve(request: dict) -> dict:
    """Resolve one ability for one device, runtime and moment.

    Returns the record shape declared in `abilities/capability-contract.yaml`.
    Raises `Rejected` only for a malformed request — "no" is an answer, not an
    error, and it is returned rather than raised.
    """
    for field in ("ability", "device_ref", "runtime"):
        if not request.get(field):
            raise Rejected(f"request.{field} is required")

    ability = request["ability"]
    providers = [p for p in (request.get("providers") or [])
                 if p.get("ability") == ability]

    record = {
        "schema": SCHEMA,
        "ability": ability,
        "device_ref": request["device_ref"],
        "runtime": request["runtime"],
        "resolved_at": request.get("resolved_at"),
        "inputs_seen": [],
        "inputs_missing": [],
    }

    # Which of the eight were readable at all. Recorded whether or not they
    # decided anything, because a confident answer computed from the inputs
    # that happened to be readable is the failure this field exists to expose.
    for name, present in (
        ("ability", bool(request.get("abilities"))),
        ("device_capability", bool(request.get("device_capabilities"))),
        ("device_profile", request.get("profile") is not None),
        ("device_binding", request.get("instances") is not None),
        ("agent_runtime", request.get("runtime_record") is not None),
        ("runtime_health", bool(request.get("health"))),
        ("policy", request.get("policy") is not None),
        ("task_context", request.get("task_context") is not None),
    ):
        (record["inputs_seen"] if present else record["inputs_missing"]).append(name)

    # The ability itself is settled once, before any provider is considered.
    # It is not a property of a provider: a provider for an ability that is not
    # in the pinned catalogue is a provider for something that does not exist,
    # and the finding is about the catalogue, not about the provider.
    catalogue_refusal = _input_ability(request)
    if catalogue_refusal is not None:
        return {**record, **catalogue_refusal, "candidates": []}

    if not providers:
        return {**record, "status": "UNAVAILABLE",
                "reason": "PROVIDER_NOT_INSTALLED",
                "detail": f"no provider implements {ability}",
                "candidates": []}

    candidates = []
    for provider in sorted(providers, key=lambda p: (p.get("preference", 100), p["id"])):
        outcome = _evaluate_one(request, ability, provider)
        candidates.append({
            "provider": provider["id"],
            "status": outcome["status"],
            "reason": outcome.get("reason"),
            "detail": outcome.get("detail"),
            "refused_by": outcome.get("refused_by"),
            "finding": outcome.get("finding"),
            "preference": outcome.get("preference", provider.get("preference", 100)),
        })

    usable = [c for c in candidates if c["status"] in ("AVAILABLE", "DEGRADED")]
    if usable:
        chosen = usable[0]
        return {**record,
                "status": chosen["status"],
                "reason": chosen["reason"],
                "detail": chosen["detail"],
                "provider": chosen["provider"],
                "finding": chosen.get("finding"),
                "candidates": candidates}

    # Nothing was usable. Prefer a definite refusal over an indefinite one, in
    # the contract's order: a definite "no" outranks "we could not tell".
    definite = [c for c in candidates if c["status"] != "UNKNOWN"]
    if definite:
        chosen = definite[0]
        return {**record,
                "status": chosen["status"],
                "reason": chosen["reason"],
                "detail": chosen["detail"],
                "candidates": candidates}

    return {**record, "status": "UNKNOWN", "reason": None,
            "detail": ("no provider could be resolved and none refused: "
                       + ", ".join(record["inputs_missing"]) + " unavailable"),
            "candidates": candidates}


def summarize(record: dict) -> str:
    """One line, for a disclosure surface. Never a paragraph."""
    if record["status"] == "AVAILABLE":
        return f"{record['ability']}: available via {record.get('provider')}"
    if record["status"] == "UNKNOWN":
        return (f"{record['ability']}: unknown "
                f"({', '.join(record['inputs_missing']) or 'no inputs'})")
    return (f"{record['ability']}: {record['status'].lower()} "
            f"({record.get('reason')})")


def count_abilities(requests: list[dict]) -> dict:
    """The capability L0 disclosure: a number, and how many could not be decided.

    Runs no probe — every request carries records the caller already gathered —
    because a number that costs a probe is a number a session stops asking for.
    """
    statuses: dict[str, int] = {}
    for request in requests:
        record = resolve(request)
        statuses[record["status"]] = statuses.get(record["status"], 0) + 1
    return {
        "total": len(requests),
        "by_status": statuses,
        "usable": statuses.get("AVAILABLE", 0) + statuses.get("DEGRADED", 0),
        "undecided": statuses.get("UNKNOWN", 0),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--request", help="a JSON file holding one request")
    parser.add_argument("--ability")
    parser.add_argument("--device")
    parser.add_argument("--runtime", default="claude-code")
    parser.add_argument("--inputs", help="a JSON file holding the input records")
    args = parser.parse_args(argv)

    if args.request:
        request = json.loads(Path(args.request).read_text(encoding="utf-8"))
    else:
        if not (args.ability and args.device):
            parser.error("--ability and --device are required without --request")
        request = {"ability": args.ability, "device_ref": args.device,
                   "runtime": args.runtime}
        if args.inputs:
            request.update(json.loads(Path(args.inputs).read_text(encoding="utf-8")))

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
