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


TRI_NODE_SCHEMA = "serveros/fleet-v1-runtime-parity-tri-node.v1"
TRI_NODE_OBSERVATION_SCHEMA = (
    "serveros/fleet-v1-runtime-parity-tri-node-observation.v1")

CONNECTOR_STATES = ("ENROLLED_REACHABLE", "ENROLLED_UNREACHABLE",
                    "DEVICE_NOT_ENROLLED", "UNKNOWN")
POLICY_STATES = ("GRANT_OBSERVED", "GRANT_UNKNOWN", "READ_AUTH_REQUIRED",
                 "READ_UNAVAILABLE")
VERB_STATES = ("OBSERVED", "DECLARED_ONLY", "UNSUPPORTED", "UNKNOWN")
LIFECYCLE_MATRIX_VERBS = ("create", "send", "get_run", "resume",
                          "interrupt", "end")
STATE_ORDER = (
    "DEVICE_NOT_ENROLLED", "ENROLLED_UNREACHABLE", "ENROLLED_REACHABLE",
    "DECLARED_BINDING_ONLY", "INSTALLED_ONLY", "READ_AUTHORIZED",
    "NEEDS_LOGIN", "OBSERVED_UNBOUND", "GRANT_UNKNOWN", "UNKNOWN",
)


def _connector_state(device: dict) -> str:
    enrollment = device.get("enrollment")
    reachability = device.get("reachability")
    reachability = reachability if isinstance(reachability, dict) else {}
    state = reachability.get("state")
    if enrollment == "NOT_ENROLLED" or state == "DEVICE_NOT_ENROLLED":
        return "DEVICE_NOT_ENROLLED"
    if state == "KNOWN_ONLINE":
        return "ENROLLED_REACHABLE"
    if enrollment == "enrolled" and state == "UNKNOWN":
        return "ENROLLED_UNREACHABLE"
    return "UNKNOWN"


def _verb_states(provider: dict) -> dict:
    """Declared versus observed, per verb, without promoting one to the other."""
    declared = provider.get("declared_verbs")
    declared = declared if isinstance(declared, dict) else {}
    observed = set(provider.get("observed_verbs") or [])
    out = {}
    for verb in LIFECYCLE_MATRIX_VERBS:
        if verb in observed:
            out[verb] = "OBSERVED"
        elif declared.get(verb) == "UNSUPPORTED":
            out[verb] = "UNSUPPORTED"
        elif declared.get(verb) == "SUPPORTED":
            out[verb] = "DECLARED_ONLY"
        else:
            out[verb] = "UNKNOWN"
    return out


def _provider_effective(provider: dict, field: str) -> dict:
    block = provider.get(
        "effective_model" if field == "model" else "effective_permission_mode")
    block = block if isinstance(block, dict) else {}
    value = block.get("value")
    source = block.get("source")
    if _text(value) and block.get("provenance") == "FACT" and _text(source):
        return {"value": value, "provenance": "FACT", "source": source}
    return {
        "value": None,
        "provenance": "UNKNOWN",
        "reason": block.get("reason") or (
            f"no provider-native {field} field with FACT provenance was observed"),
    }


def _bound(family, bindings) -> bool:
    return f"agent-runtime:{family}" in (bindings or [])


def _policy_word(block, policy_state: str) -> dict:
    """A grant is only GRANT_OBSERVED when a policy read actually established it."""
    block = block if isinstance(block, dict) else {}
    state = block.get("state")
    if policy_state == "GRANT_OBSERVED" and state in POLICY_STATES:
        return {"state": state, "reason": block.get("reason")}
    return {
        "state": "GRANT_UNKNOWN",
        "reason": block.get("reason") or "no authorized policy status read",
    }


def project_tri_node(observation: dict) -> dict:
    """One read-only cross-device capability/entitlement matrix. Pure."""
    if not isinstance(observation, dict):
        raise ValueError("observation must be a mapping")
    if observation.get("schema") != TRI_NODE_OBSERVATION_SCHEMA:
        raise ValueError(
            f"observation schema must be {TRI_NODE_OBSERVATION_SCHEMA!r}; "
            f"got {observation.get('schema')!r}")

    policy = observation.get("fleet_policy_read")
    policy = policy if isinstance(policy, dict) else {}
    policy_state = policy.get("state")
    if policy_state not in POLICY_STATES:
        policy_state = "GRANT_UNKNOWN"

    devices_out = {}
    for device in observation.get("devices") or []:
        if not isinstance(device, dict):
            continue
        ref = device.get("device_ref")
        if not _text(ref):
            continue

        connector = _connector_state(device)
        bindings = list(device.get("declared_bindings") or [])
        providers = []
        for provider in device.get("providers") or []:
            if not isinstance(provider, dict):
                continue
            providers.append({
                "provider": provider.get("provider"),
                "family": provider.get("family"),
                "bound": _bound(provider.get("family"), bindings),
                "control_plane": provider.get("control_plane"),
                "execution_readiness": provider.get("execution_readiness"),
                "reads": {
                    "agent_list": provider.get("agent_list"),
                    "session_list": provider.get("session_list"),
                },
                "sessions_observed": provider.get("sessions_observed", 0),
                "verbs": _verb_states(provider),
                "effective": {
                    "model": _provider_effective(provider, "model"),
                    "permissionMode": _provider_effective(provider, "permissionMode"),
                },
            })

        states = [connector]
        if (device.get("enrollment") == "enrolled" and not providers
                and bindings):
            states.append("DECLARED_BINDING_ONLY")
        if providers:
            reads = ([p["reads"]["agent_list"] for p in providers]
                     + [p["reads"]["session_list"] for p in providers])
            if "READ_AUTHORIZED" in reads:
                states.append("READ_AUTHORIZED")
            if "NEEDS_LOGIN" in reads:
                states.append("NEEDS_LOGIN")
            cli = device.get("cli")
            if isinstance(cli, dict) and cli.get("provenance") == "FACT":
                states.append("INSTALLED_ONLY")
            if any(not p["bound"] for p in providers):
                states.append("OBSERVED_UNBOUND")
        states.append("GRANT_UNKNOWN")
        selected = set(states)
        states = [state for state in STATE_ORDER if state in selected]

        devices_out[ref] = {
            "display_name": device.get("display_name"),
            "platform": device.get("platform"),
            "environment_profile": device.get("environment_profile"),
            "enrollment": device.get("enrollment"),
            "connector": connector,
            "reachability": device.get("reachability"),
            "node_runtime": device.get("node_runtime"),
            "os_principal": device.get("os_principal"),
            "declared_bindings": bindings,
            "declared_agents": device.get("declared_agents"),
            "provider_inventory": device.get("provider_inventory"),
            "providers": providers,
            "sessions_observed": len(device.get("sessions") or []),
            "project": device.get("project") or {"state": "UNKNOWN"},
            "create_allow": _policy_word(device.get("create_allow"), policy_state),
            "session_create": _policy_word(device.get("session_create"), policy_state),
            "states": states,
        }

    return {
        "schema": TRI_NODE_SCHEMA,
        "evidence_level": EVIDENCE_LEVEL,
        "recorded_at": observation.get("recorded_at"),
        "scope": observation.get("scope"),
        "fleet_policy_read": {
            "state": policy_state,
            "endpoint": policy.get("endpoint"),
            "server": policy.get("server"),
            "http_status": policy.get("http_status"),
            "error": policy.get("error"),
            "error_description": policy.get("error_description"),
            "credential_read": policy.get("credential_read", False),
            "credential_used": policy.get("credential_used", False),
            "observed_at": policy.get("observed_at"),
            "consequence": policy.get("consequence"),
        },
        "devices": devices_out,
        "behavioral": observation.get("behavioral") or {
            "state": "UNKNOWN", "live": False,
            "reason": "no behavioral observation was supplied",
        },
        "no_fallback": observation.get("no_fallback") is True,
        "fallback": None,
        "approval_required": observation.get("approval_required") or [],
        "redactions": observation.get("redactions") or [],
    }


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
