#!/usr/bin/env python3
"""Observed-vs-declared projection for R1 #248.

WHAT THIS IS
------------
A pure projection over a *recorded* read-only observation. It keeps five states
distinct instead of collapsing them into "works / does not work":

    ABSENT            no such provider instance was in the inventory
    INSTALLED_ONLY    a CLI/version was proven; no provider/session read followed
    READ_AUTHORIZED   a documented read-only provider/session read answered with
                      authoritative provider-response evidence
    NEEDS_LOGIN       the provider itself said authentication is required
    UNKNOWN           nobody established it

The distinction is the point. An installed CLI is not a provider. A readable
session list is not a new account entitlement. A provider that is present but
not in the device's `runtime_bindings` is observed but unbound.

WHAT IT IS NOT
--------------
* not a live probe: it reads no file, socket, process, clock or credential;
* not a Session owner or authorizer: `new_work` is a refusal/projection, never a
  grant;
* not a fallback engine: an unbound provider is refused; another family is not
  tried;
* not behavioral evidence: a structural `CONFORMANT` verdict is carried with its
  structural-only scope, and `live` stays false.

EFFECTIVE MODEL / PERMISSION MODE
---------------------------------
`effective` is accepted per field only from a provider-native field with `FACT`
provenance and a named source. A declared model, a provider family, a requested
value or a config default never becomes effective. Model and permission mode are
independent: the Codex readback can prove one and not the other.
"""
from __future__ import annotations

SCHEMA = "serveros/fleet-v1-runtime-parity-observed.v1"
OBSERVATION_SCHEMA = "serveros/fleet-v1-runtime-parity-observation.v1"
EVIDENCE_LEVEL = "REAL_READ_ONLY"

OBSERVED_STATES = ("ABSENT", "INSTALLED_ONLY", "READ_AUTHORIZED",
                   "NEEDS_LOGIN", "UNKNOWN")
BINDING_STATES = ("BOUND", "UNBOUND", "UNKNOWN")
NEW_WORK_VERBS = ("create", "send", "resume", "interrupt")
ENTITLEMENT = "NOT_INFERRED_FROM_SESSION_READ"
RUNTIME_KEYS = ("claude-code", "codex")


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _cli_state(cli) -> str:
    if not isinstance(cli, dict):
        return "UNKNOWN"
    if cli.get("installed") is False:
        return "ABSENT"
    if (cli.get("installed") is True and _text(cli.get("version"))
            and cli.get("provenance") == "FACT"):
        return "INSTALLED_ONLY"
    return "UNKNOWN"


def _aggregate_reads(states) -> str:
    """One read surface across providers, without merging its neighbours.

    A succeeded read wins over a login requirement on a *different* surface,
    because they are different questions; the caller still gets both fields.
    """
    known = [state for state in states if state in OBSERVED_STATES]
    if "READ_AUTHORIZED" in known:
        return "READ_AUTHORIZED"
    if "NEEDS_LOGIN" in known:
        return "NEEDS_LOGIN"
    if known and all(state == "ABSENT" for state in known):
        return "ABSENT"
    return "UNKNOWN"


def _direct_state(runtime: dict, providers: list[dict]) -> str:
    direct = runtime.get("direct_provider")
    direct = direct if isinstance(direct, dict) else {}
    state = direct.get("state")
    if state == "ABSENT":
        return "ABSENT"
    if state == "PRESENT":
        reads = [provider.get("session_list") for provider in providers]
        return "READ_AUTHORIZED" if "READ_AUTHORIZED" in reads else "UNKNOWN"
    return "UNKNOWN"


def _effective(runtime: dict, field: str) -> dict:
    """One effective field, per field, from provider-response evidence only."""
    providers = [p for p in runtime.get("providers") or [] if isinstance(p, dict)]
    sessions = [s for s in runtime.get("session_metadata") or [] if isinstance(s, dict)]
    candidates = []
    block_name = "effective_model" if field == "model" else "effective_permission_mode"
    for provider in providers:
        block = provider.get(block_name)
        if isinstance(block, dict):
            candidates.append({
                "value": block.get("value"),
                "provenance": block.get("provenance"),
                "source": block.get("source"),
            })
    for session in sessions:
        candidates.append({
            "value": session.get(field),
            "provenance": session.get(f"{field}_provenance"),
            "source": session.get(f"{field}_source"),
        })
    for candidate in candidates:
        if (_text(candidate["value"]) and candidate["provenance"] == "FACT"
                and _text(candidate["source"])):
            return {"value": candidate["value"], "provenance": "FACT",
                    "source": candidate["source"]}
    return {
        "value": None,
        "provenance": "UNKNOWN",
        "reason": (f"no provider-native {field} field with FACT provenance and a "
                   "named source was observed"),
    }


def _new_work_reasons(states: dict) -> list[str]:
    reasons = []
    if states["direct_provider"] == "ABSENT":
        reasons.append("PROVIDER_ADAPTER_ABSENT")
    if states["agent_list"] == "NEEDS_LOGIN":
        reasons.append("NEEDS_LOGIN")
    if not reasons:
        reasons.append("ENTITLEMENT_NOT_OBSERVED")
    return reasons


def _new_work(states: dict, binding_state: str) -> dict:
    """What a *new* create/send/resume/interrupt would project to.

    It never returns PRECONDITIONS_MET. A read-only observation cannot establish
    a grant, an execution entitlement, or a bound runtime, and the source
    contract requires all three before a mutation could proceed.
    """
    if binding_state == "UNBOUND":
        return {verb: {"dispatch": "REFUSED", "reasons": ["PROVIDER_UNBOUND"]}
                for verb in NEW_WORK_VERBS}
    reasons = _new_work_reasons(states)
    return {verb: {"dispatch": "UNKNOWN", "reasons": list(reasons)}
            for verb in NEW_WORK_VERBS}


def project_observed(observation: dict) -> dict:
    """One observed-vs-declared record from one recorded snapshot. Pure."""
    if not isinstance(observation, dict):
        raise ValueError("observation must be a mapping")
    if observation.get("schema") != OBSERVATION_SCHEMA:
        raise ValueError(
            f"observation schema must be {OBSERVATION_SCHEMA!r}; "
            f"got {observation.get('schema')!r}")

    runtimes = observation.get("runtimes")
    runtimes = runtimes if isinstance(runtimes, dict) else {}

    projected = {}
    for key in RUNTIME_KEYS:
        runtime = runtimes.get(key)
        runtime = runtime if isinstance(runtime, dict) else {}
        providers = [p for p in runtime.get("providers") or [] if isinstance(p, dict)]

        cli_state = _cli_state(runtime.get("cli"))
        binding = runtime.get("binding")
        binding = binding if isinstance(binding, dict) else {}
        binding_state = binding.get("state")
        if binding_state not in BINDING_STATES:
            binding_state = "UNKNOWN"

        states = {
            "cli": cli_state,
            "direct_provider": _direct_state(runtime, providers),
            "agent_list": _aggregate_reads([p.get("agent_list") for p in providers]),
            "session_list": _aggregate_reads([p.get("session_list") for p in providers]),
        }

        projected[key] = {
            "states": states,
            "binding": binding_state,
            "binding_declared": binding.get("declared"),
            "providers": [p.get("provider") for p in providers],
            "sessions_observed": len(runtime.get("session_metadata") or []),
            "reads_observed": {
                "agent_list": states["agent_list"],
                "session_list": states["session_list"],
                "transcript": "NOT_READ",
            },
            "effective": {
                "model": _effective(runtime, "model"),
                "permissionMode": _effective(runtime, "permissionMode"),
            },
            "new_work": _new_work(states, binding_state),
            "entitlement": ENTITLEMENT,
        }

    device = observation.get("device")
    device = device if isinstance(device, dict) else {}
    return {
        "schema": SCHEMA,
        "evidence_level": EVIDENCE_LEVEL,
        "device_ref": observation.get("device_ref"),
        "device_owner": observation.get("device_owner"),
        "recorded_at": observation.get("recorded_at"),
        "runtimes": projected,
        "structural_parity": device.get("structural_parity") or {},
        "behavioral": observation.get("behavioral") or {
            "state": "UNKNOWN", "live": False,
            "reason": "no behavioral observation was supplied",
        },
        "entitlement": ENTITLEMENT,
        "fallback": None,
        "redactions": observation.get("redactions") or [],
    }
