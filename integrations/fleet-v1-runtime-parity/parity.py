#!/usr/bin/env python3
"""R1 #248 — one source-level Claude Code / Codex runtime-parity projection.

WHAT THIS IS
------------
A pure mapping from an *observation* to one R1 record. It joins the axes the
issue names — device, OS principal, credential-instance reference, family,
transport, runtime tag, project, requested/resolved/effective model and
permission mode, and the supported Session lifecycle — and it refuses, or says
UNKNOWN, wherever the source cannot support a claim.

WHAT THIS IS NOT
----------------
* not a Session owner: it creates no session, sends nothing, interrupts
  nothing, and imports no provider module;
* not an authorizer: a ``PRECONDITIONS_MET`` dispatch is not a grant. Admission
  remains with Agent Control / the Agent Session service;
* not a live reader: it opens no socket, starts no process, reads no clock and
  reads no credential. ``evaluated_at`` is supplied by the caller, so the same
  observation always projects to the same record;
* not a second registry: the capability table below is a *reading* of the
  pinned source, with the source named beside each cell.

TWO INVARIANTS
--------------
1. ``effective`` is accepted only when the observation carries a
   provider-response field name and ``FACT`` provenance. ``requested``,
   ``resolved``, a policy override or a default label never become effective.
   Model and permission mode are resolved independently, because a provider can
   report one without the other.
2. The middle segment of a session ref is the *runtime tag* (``claude``,
   ``claude-fjd-primary``, ``codex-primary``), not the CloudCLI family
   (``claude``/``codex``) and not the agent-runtime id
   (``agent-runtime:claude-code``). The tag is derived from family, OS account
   and credential profile; a declared tag that disagrees is refused rather than
   routed.

SOURCE NOTES AT THE PIN
-----------------------
``cloudcli_native.py`` records 22 native verbs; there is no ``session_resume``
among them, so resume is UNSUPPORTED over CloudCLI. ``session_start`` starts a
host, it does not resume a session.
``codex_app_server_provider.py`` declares create/inspect/send/result/resume/
interrupt SUPPORTED and stop UNSUPPORTED; resume is ``thread/resume`` and
interrupt is ``turn/interrupt`` (current turn only).
At the pinned source there is no Claude Code *provider* adapter. The local CC
plugin is a client plugin, so its lifecycle is UNKNOWN with an explicit
``PROVIDER_ADAPTER_ABSENT`` blocker. It is not filled in by inference.
"""
from __future__ import annotations

import datetime
import re

SCHEMA = "serveros/fleet-v1-runtime-parity.v1"
EVIDENCE_LEVEL = "SOURCE_FIXTURE"
SESSION_OWNER = "Agent Control / Agent Session service (not this fixture)"
ACTION = "OBSERVE_ONLY"
AUTHORIZATION = "NOT_DECIDED: admission remains with Agent Control"

FAMILIES = ("claude", "codex")
AGENT_RUNTIME_BY_FAMILY = {
    "claude": "agent-runtime:claude-code",
    "codex": "agent-runtime:codex",
}
TRANSPORTS = ("cloudcli-mcp-http", "codex-app-server", "claude-code-native")
LIFECYCLE_VERBS = ("read", "create", "send", "get_run", "resume", "interrupt")
SUPPORT_STATES = ("SUPPORTED", "UNSUPPORTED", "UNKNOWN")
DISPATCH_STATES = ("PRECONDITIONS_MET", "REFUSED", "UNKNOWN")
READINESS_STATES = ("AVAILABLE", "DEGRADED", "UNAVAILABLE", "INCOMPATIBLE", "UNKNOWN")

SELECTORS = ("all", "*", "base")
SESSION_TTL_SECONDS = 900
CREDENTIAL_PREFIX = "credential-instance:"
TAG_GRAMMAR = re.compile(r"^[a-z][a-z0-9._-]{0,31}$")
REF_SEGMENT = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

REFUSAL_CODES = (
    "CREDENTIAL_DEVICE_MISMATCH",
    "CREDENTIAL_PROFILE_MISMATCH",
    "CREDENTIAL_REF_INVALID",
    "DEVICE_OWNER_INVALID",
    "DEVICE_REF_INVALID",
    "EVALUATED_AT_INVALID",
    "FAMILY_INVALID",
    "GRANT_MISSING",
    "NEEDS_LOGIN",
    "PRINCIPAL_MISMATCH",
    "PROJECT_UNBOUND",
    "PROVIDER_NOT_INSTALLED",
    "PROVIDER_NOT_READY",
    "PROVIDER_UNBOUND",
    "RUNTIME_MISMATCH",
    "RUNTIME_TAG_MISMATCH",
    "SESSION_FRESHNESS_UNKNOWN",
    "SESSION_REF_MISMATCH",
    "SESSION_STALE",
)
BLOCKER_CODES = (
    "PROVIDER_ADAPTER_ABSENT",
    "TOOL_LIST_NOT_READ",
)

# The grant each verb needs. The scope is the Fleet scope; the flag is the
# per-device grant F1 keeps separate from it. Neither is inferred from another.
REQUIRED_GRANT = {
    "read": ("fleet:read", None),
    "create": ("fleet:create", "allow_create"),
    "send": ("fleet:send", "allow_send"),
    "get_run": ("fleet:read", None),
    "resume": ("fleet:control", "allow_control"),
    "interrupt": ("fleet:control", "allow_control"),
}

# Which CloudCLI native verbs a lifecycle verb needs on the device's own
# tools/list. A recorded verb the device does not declare is UNSUPPORTED by
# device, exactly as `cloudcli_native.capability_snapshot` says.
CLOUDCLI_REQUIRED_TOOLS = {
    "read": ("sessions_list", "session_get", "session_read"),
    "create": ("session_create",),
    "send": ("session_send",),
    "get_run": ("run_get",),
    "resume": (),
    "interrupt": ("session_interrupt",),
}

_SOURCE_CLOUDCLI = {
    "read": ("SUPPORTED",
             "fleet_mcp/cloudcli_native.py#NATIVE_TOOLS[sessions_list,session_get,session_read]"),
    "create": ("SUPPORTED", "fleet_mcp/cloudcli_native.py#NATIVE_TOOLS[session_create]"),
    "send": ("SUPPORTED", "fleet_mcp/cloudcli_native.py#NATIVE_TOOLS[session_send]"),
    "get_run": ("SUPPORTED", "fleet_mcp/cloudcli_native.py#NATIVE_TOOLS[run_get]"),
    "resume": ("UNSUPPORTED",
               "fleet_mcp/cloudcli_native.py#NATIVE_TOOLS: no session_resume; "
               "session_start starts a host, it does not resume"),
    "interrupt": ("SUPPORTED",
                  "fleet_mcp/cloudcli_native.py#NATIVE_TOOLS[session_interrupt]"),
}

_SOURCE_CODEX_APP_SERVER = {
    "read": ("SUPPORTED",
             "mcp/codex_app_server_provider.py#actions[session.inspect]; thread/list + thread/read"),
    "create": ("SUPPORTED",
               "mcp/codex_app_server_provider.py#actions[session.create]; thread/start + first turn"),
    "send": ("SUPPORTED",
             "mcp/codex_app_server_provider.py#actions[session.send]; turn/start"),
    "get_run": ("SUPPORTED",
                "mcp/codex_app_server_provider.py#actions[session.result]; "
                "turn completion + thread/read"),
    "resume": ("SUPPORTED",
               "mcp/codex_app_server_provider.py#actions[session.resume]; thread/resume"),
    "interrupt": ("SUPPORTED",
                  "mcp/codex_app_server_provider.py#actions[session.interrupt]; "
                  "turn/interrupt (current turn only)"),
}


def _all(state: str, source: str) -> dict:
    return {verb: (state, source) for verb in LIFECYCLE_VERBS}


CAPABILITY_MATRIX = {
    ("claude", "cloudcli-mcp-http"): dict(_SOURCE_CLOUDCLI),
    ("codex", "cloudcli-mcp-http"): dict(_SOURCE_CLOUDCLI),
    ("codex", "codex-app-server"): dict(_SOURCE_CODEX_APP_SERVER),
    ("claude", "codex-app-server"): _all(
        "UNSUPPORTED",
        "mcp/codex_app_server_provider.py#RUNTIME_REF=agent-runtime:codex; "
        "the app-server transport serves the codex family only",
    ),
    ("claude", "claude-code-native"): _all(
        "UNKNOWN",
        "mcp/# no Claude Code provider adapter at the pinned source; "
        "integrations/fleet-cc-dev-plugin is a client plugin, not a session provider",
    ),
    ("codex", "claude-code-native"): _all(
        "UNSUPPORTED",
        "the claude-code-native transport does not serve agent-runtime:codex",
    ),
}


# ---------------------------------------------------------------------------
# Small pure helpers. No IO, no clock.
# ---------------------------------------------------------------------------

def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _iso(value):
    if not _text(value):
        return None
    try:
        moment = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=datetime.timezone.utc)
    return moment.astimezone(datetime.timezone.utc)


def _refusal(code: str, axis: str, detail: str) -> dict:
    return {"code": code, "axis": axis, "detail": detail}


def _blocker(code: str, detail: str) -> dict:
    return {"code": code, "detail": detail}


def _secret_like(value: str) -> bool:
    """A reference is a NAME. These shapes are values and are refused.

    The check is deliberately conservative and runs before any equality check,
    so a token placed where a reference belongs is reported as a malformed
    reference rather than echoed back in a detail string.
    """
    lowered = value.lower()
    for token in ("bearer ", "sk-", "xoxb-", "xoxp-", "akia", "-----begin",
                  "private key", "token=", "password="):
        if token in lowered:
            return True
    for part in re.split(r"[/:\s]+", value):
        if len(part) >= 32 and re.fullmatch(r"[A-Za-z0-9+/=_-]+", part):
            return True
    return False


def _slug(value: str) -> str:
    kept = "".join(
        ch if (ch.isascii() and (ch.isalnum() or ch in "._-")) else "-"
        for ch in value.strip().lower()
    ).strip("-.")
    if not kept:
        raise ValueError(f"{value!r} reduces to no tag characters")
    return kept


def derive_runtime_tag(family: str, os_principal: str, device_owner: str,
                       profile: str | None = None) -> str:
    """The session-ref middle segment, from the three naming axes.

    Mirrors the source rule: the device's own account with no profile keeps the
    bare family (``claude``/``codex``); any other account or a named profile is
    suffixed. A tag is a NAME, never a grant and never evidence of isolation.
    """
    if family not in FAMILIES:
        raise ValueError(f"{family!r} is not an agent family; expected {list(FAMILIES)}")
    if not _text(os_principal):
        raise ValueError("a runtime belongs to a named OS account")
    if not _text(device_owner):
        raise ValueError("the device's own OS account must be named, not assumed")
    own = os_principal.strip() == device_owner.strip()
    if own and profile is None:
        tag = family
    else:
        parts = [family]
        if not own:
            parts.append(_slug(os_principal))
        if profile is not None:
            parts.append(_slug(profile))
        tag = "-".join(parts)
    if len(tag) > 32:
        raise ValueError(f"the tag {tag!r} exceeds the 32-character grammar")
    if tag in SELECTORS or not TAG_GRAMMAR.fullmatch(tag):
        raise ValueError(f"{tag!r} is not a provider tag this surface can address")
    return tag


def _quote_segment(value: str) -> str:
    out = []
    for byte in str(value).encode("utf-8"):
        ch = chr(byte)
        if ch.isalnum() or ch in "-_.~":
            out.append(ch)
        else:
            out.append("%%%02X" % byte)
    return "".join(out)


def _parse_session_ref(ref) -> tuple | None:
    prefix = "agent-session:"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        return None
    parts = ref[len(prefix):].split("/")
    if len(parts) != 3 or any(not part for part in parts):
        return None
    return parts[0], parts[1], parts[2]


def _defaults_view(defaults, key: str) -> dict:
    block = defaults.get(key) if isinstance(defaults, dict) else None
    block = block if isinstance(block, dict) else {}
    out = {"model": block.get("model"), "permissionMode": block.get("permissionMode")}
    if key == "resolved":
        out["by"] = block.get("by")
    return out


def _effective_view(raw) -> dict:
    """Accept effective only from a provider-response field with FACT provenance."""
    raw = raw if isinstance(raw, dict) else {}
    out = {
        "model": None,
        "model_provenance": "UNKNOWN",
        "model_source": None,
        "permissionMode": None,
        "permissionMode_provenance": "UNKNOWN",
        "permissionMode_source": None,
        "rejected": [],
        "reason": ("effective is accepted only from a provider-response field with "
                   "FACT provenance; requested/resolved/default never become effective"),
    }
    for field in ("model", "permissionMode"):
        value = raw.get(field)
        provenance = raw.get(f"{field}_provenance")
        source = raw.get(f"{field}_source")
        if value is None:
            continue
        if _text(value) and provenance == "FACT" and _text(source):
            out[field] = value.strip()
            out[f"{field}_provenance"] = "FACT"
            out[f"{field}_source"] = source.strip()
        else:
            out["rejected"].append(field)
    return out


def _support(family, transport, verb: str, declared_tools) -> tuple:
    matrix = CAPABILITY_MATRIX.get((family, transport))
    if matrix is None:
        return ("UNKNOWN", f"no source mapping for family={family!r} transport={transport!r}")
    state, source = matrix[verb]
    if transport == "cloudcli-mcp-http" and state == "SUPPORTED":
        if declared_tools is None:
            return ("UNKNOWN", "the device's own tools/list was not read")
        missing = [name for name in CLOUDCLI_REQUIRED_TOOLS[verb]
                   if name not in declared_tools]
        if missing:
            return ("UNSUPPORTED", f"the device does not declare {missing}")
    return (state, source)


# ---------------------------------------------------------------------------
# The projection.
# ---------------------------------------------------------------------------

def project(observation: dict) -> dict:
    """One R1 record from one observation. Pure; deterministic; no IO."""
    if not isinstance(observation, dict):
        raise ValueError("observation must be a mapping")

    refusals: list[dict] = []
    blockers: list[dict] = []

    evaluated_at = _iso(observation.get("evaluated_at"))
    if evaluated_at is None:
        refusals.append(_refusal(
            "EVALUATED_AT_INVALID", "timing",
            "evaluated_at is required and must be an ISO-8601 instant"))

    device_ref = observation.get("device_ref")
    device_id = None
    if _text(device_ref) and str(device_ref).startswith("device:"):
        device_id = str(device_ref)[len("device:"):]
        if not device_id:
            refusals.append(_refusal("DEVICE_REF_INVALID", "device",
                                     "device_ref names no device id"))
    else:
        refusals.append(_refusal("DEVICE_REF_INVALID", "device",
                                 "device_ref must be a typed device:<id> ref"))

    device_owner = observation.get("device_owner")
    if not _text(device_owner):
        refusals.append(_refusal(
            "DEVICE_OWNER_INVALID", "os-principal",
            "device_owner must name the device's own OS account; it is not derived from a hostname or login"))
        device_owner = None
    elif _secret_like(device_owner):
        refusals.append(_refusal(
            "DEVICE_OWNER_INVALID", "os-principal",
            "device_owner looks like a credential value; it is refused and not echoed"))
        device_owner = None
    else:
        device_owner = device_owner.strip()

    provider = observation.get("provider")
    provider = provider if isinstance(provider, dict) else {}

    family = provider.get("family")
    family_out = family if family in FAMILIES else None
    if family not in FAMILIES:
        refusals.append(_refusal(
            "FAMILY_INVALID", "family",
            f"family must be one of {list(FAMILIES)}; an agent-runtime id is not a family"))

    agent_runtime = provider.get("agent_runtime")
    expected_runtime = AGENT_RUNTIME_BY_FAMILY.get(family)
    if expected_runtime is not None and agent_runtime != expected_runtime:
        refusals.append(_refusal(
            "RUNTIME_MISMATCH", "agent-runtime",
            f"family {family!r} requires {expected_runtime!r}"))

    transport = provider.get("transport")
    transport_out = transport if _text(transport) else None

    os_principal = provider.get("os_principal")
    if not _text(os_principal):
        refusals.append(_refusal(
            "PRINCIPAL_MISMATCH", "os-principal",
            "os_principal must name the OS account the provider actually runs as"))
        os_principal = None
    elif _secret_like(os_principal):
        refusals.append(_refusal(
            "PRINCIPAL_MISMATCH", "os-principal",
            "os_principal looks like a credential value; it is refused and not echoed"))
        os_principal = None
    else:
        os_principal = os_principal.strip()
    os_principal_out = os_principal

    profile = provider.get("profile")
    if profile is not None and not _text(profile):
        refusals.append(_refusal(
            "CREDENTIAL_REF_INVALID", "credential-instance",
            "profile must be a non-empty string when set"))
        profile = None
    elif profile is not None:
        profile = profile.strip()
        if _secret_like(profile):
            refusals.append(_refusal(
                "CREDENTIAL_REF_INVALID", "credential-instance",
                "profile looks like a credential value; it is refused and not echoed"))
            profile = None

    declared_tag = provider.get("runtime_tag")
    derived_tag = None
    if family in FAMILIES and _text(os_principal) and device_owner is not None:
        try:
            derived_tag = derive_runtime_tag(family, os_principal, device_owner, profile)
        except ValueError as exc:
            refusals.append(_refusal("RUNTIME_TAG_MISMATCH", "runtime-tag", str(exc)))
    if not _text(declared_tag):
        refusals.append(_refusal(
            "RUNTIME_TAG_MISMATCH", "runtime-tag",
            "runtime_tag is required; it is the session-ref middle segment"))
    elif _secret_like(declared_tag):
        refusals.append(_refusal(
            "RUNTIME_TAG_MISMATCH", "runtime-tag",
            "runtime_tag looks like a credential value; it is refused and not echoed"))
        declared_tag = None
    elif declared_tag in SELECTORS or not TAG_GRAMMAR.fullmatch(declared_tag):
        refusals.append(_refusal(
            "RUNTIME_TAG_MISMATCH", "runtime-tag",
            "runtime_tag must be a provider tag, not a query selector or an agent-runtime id"))
    elif derived_tag is not None and declared_tag != derived_tag:
        refusals.append(_refusal(
            "RUNTIME_TAG_MISMATCH", "runtime-tag",
            f"declared runtime_tag {declared_tag!r} disagrees with the tag derived "
            f"from family/OS-account/profile ({derived_tag!r})"))

    credential_ref = provider.get("credential_instance_ref")
    credential_out = None
    if not _text(credential_ref):
        refusals.append(_refusal(
            "CREDENTIAL_REF_INVALID", "credential-instance",
            "credential_instance_ref is required and must be a reference, never a value"))
    elif _secret_like(credential_ref):
        refusals.append(_refusal(
            "CREDENTIAL_REF_INVALID", "credential-instance",
            "credential_instance_ref looks like a credential value; it is refused and not echoed"))
    elif not str(credential_ref).startswith(CREDENTIAL_PREFIX):
        refusals.append(_refusal(
            "CREDENTIAL_REF_INVALID", "credential-instance",
            f"credential_instance_ref must start with {CREDENTIAL_PREFIX!r}"))
    else:
        parts = str(credential_ref)[len(CREDENTIAL_PREFIX):].split("/")
        if len(parts) != 3 or any(not REF_SEGMENT.fullmatch(part or "") for part in parts):
            refusals.append(_refusal(
                "CREDENTIAL_REF_INVALID", "credential-instance",
                "credential_instance_ref must be credential-instance:<device>/<os-principal>/<profile>"))
        else:
            cred_device, cred_account, cred_profile = parts
            if device_id is not None and cred_device != device_id:
                refusals.append(_refusal(
                    "CREDENTIAL_DEVICE_MISMATCH", "credential-instance",
                    "the credential-instance reference names a different device"))
            elif os_principal is not None and cred_account != os_principal:
                refusals.append(_refusal(
                    "PRINCIPAL_MISMATCH", "credential-instance",
                    "the credential-instance reference names a different OS principal"))
            elif profile is not None and cred_profile != profile:
                refusals.append(_refusal(
                    "CREDENTIAL_PROFILE_MISMATCH", "credential-instance",
                    "the credential-instance reference names a different profile"))
            else:
                credential_out = str(credential_ref)

    bindings = observation.get("device_bindings")
    bound = False
    if family in FAMILIES and _text(declared_tag) and TAG_GRAMMAR.fullmatch(declared_tag):
        if isinstance(bindings, list):
            bound = any(
                isinstance(row, dict)
                and row.get("family") == family
                and row.get("runtime_tag") == declared_tag
                and row.get("agent_runtime") == agent_runtime
                for row in bindings
            )
        if not bound:
            refusals.append(_refusal(
                "PROVIDER_UNBOUND", "device-binding",
                f"no runtime binding names family={family!r} runtime_tag={declared_tag!r}; "
                "no other family or runtime is tried"))

    project_block = observation.get("project")
    project_out = None
    if (isinstance(project_block, dict) and _text(project_block.get("id"))
            and project_block.get("bound") is True):
        project_out = {"id": project_block["id"], "bound": True}
    else:
        refusals.append(_refusal(
            "PROJECT_UNBOUND", "project",
            "project.id and project.bound=true are required; a project id decides the "
            "working tree, credential and reachable services a session inherits"))

    installed = provider.get("installed")
    if installed is not True and installed is not False:
        installed = None
    readiness_detail = provider.get("readiness_detail")
    if not _text(readiness_detail):
        readiness_detail = None
    raw_readiness = provider.get("readiness")
    readiness = "UNKNOWN"
    if installed is False:
        readiness = "UNAVAILABLE"
        readiness_detail = "NOT_INSTALLED"
        refusals.append(_refusal(
            "PROVIDER_NOT_INSTALLED", "readiness",
            "the provider's own installation probe reports no installed CLI"))
    elif raw_readiness == "NEEDS_LOGIN":
        readiness = "UNAVAILABLE"
        readiness_detail = "NEEDS_LOGIN"
        refusals.append(_refusal(
            "NEEDS_LOGIN", "readiness",
            "the provider reports that a human login is required; no fallback account is used"))
    elif raw_readiness in READINESS_STATES:
        readiness = raw_readiness
        if readiness in ("UNAVAILABLE", "INCOMPATIBLE"):
            refusals.append(_refusal(
                "PROVIDER_NOT_READY", "readiness",
                f"the provider's readiness is {readiness}"))
    elif raw_readiness is None:
        readiness = "UNKNOWN"
    else:
        # An unrecognised token is not translated into a known state.
        readiness = "UNKNOWN"

    session = observation.get("session")
    session_state = "NOT_OBSERVED"
    session_ref_ok = True
    session_ref_out = None
    if isinstance(session, dict):
        parsed = _parse_session_ref(session.get("ref"))
        if parsed is None:
            session_ref_ok = False
            refusals.append(_refusal(
                "SESSION_REF_MISMATCH", "session",
                "session.ref is not an agent-session:<device>/<runtime-tag>/<native-id> ref"))
        else:
            s_device, s_tag, _native = parsed
            if device_id is not None and s_device != _quote_segment(str(device_ref)):
                session_ref_ok = False
                refusals.append(_refusal(
                    "SESSION_REF_MISMATCH", "session",
                    "the session ref names a different device"))
            elif _text(declared_tag) and s_tag != declared_tag:
                session_ref_ok = False
                refusals.append(_refusal(
                    "SESSION_REF_MISMATCH", "session",
                    "the session ref's middle segment is not this runtime's tag"))
            else:
                session_ref_out = session.get("ref")
        observed_at = _iso(session.get("observed_at"))
        if observed_at is None:
            session_state = "UNKNOWN"
            refusals.append(_refusal(
                "SESSION_FRESHNESS_UNKNOWN", "session",
                "session.observed_at is absent or unreadable; freshness is UNKNOWN, not fresh"))
        elif evaluated_at is not None and (evaluated_at - observed_at).total_seconds() > SESSION_TTL_SECONDS:
            session_state = "STALE"
            refusals.append(_refusal(
                "SESSION_STALE", "session",
                f"the session observation is older than {SESSION_TTL_SECONDS}s"))
        else:
            session_state = "FRESH"

    grant = observation.get("grant")
    grant_present = isinstance(grant, dict)
    grant_scopes: set[str] = set()
    grant_flags = {"allow_create": False, "allow_send": False, "allow_control": False}
    if not grant_present:
        refusals.append(_refusal(
            "GRANT_MISSING", "grant",
            "no grant accompanies the observation; capability and readiness do not authorize"))
    else:
        scopes = grant.get("scopes")
        if isinstance(scopes, list):
            grant_scopes = {scope for scope in scopes if isinstance(scope, str)}
        for flag in grant_flags:
            grant_flags[flag] = grant.get(flag) is True

    declared_tools = provider.get("declared_native_tools")
    if isinstance(declared_tools, list):
        declared_tools = {name for name in declared_tools if isinstance(name, str)}
    else:
        declared_tools = None

    if family == "claude" and transport == "claude-code-native":
        blockers.append(_blocker(
            "PROVIDER_ADAPTER_ABSENT",
            "no Claude Code provider adapter exists at the pinned source; the local "
            "fleet-cc-dev-plugin is a client, not a Session provider"))
    if transport == "cloudcli-mcp-http" and declared_tools is None:
        blockers.append(_blocker(
            "TOOL_LIST_NOT_READ",
            "the device's own tools/list was not read, so per-device support is UNKNOWN"))

    structural_codes = {
        "CREDENTIAL_DEVICE_MISMATCH", "CREDENTIAL_PROFILE_MISMATCH",
        "CREDENTIAL_REF_INVALID", "DEVICE_OWNER_INVALID", "DEVICE_REF_INVALID",
        "EVALUATED_AT_INVALID", "FAMILY_INVALID", "PRINCIPAL_MISMATCH",
        "PROJECT_UNBOUND", "PROVIDER_UNBOUND", "RUNTIME_MISMATCH",
        "RUNTIME_TAG_MISMATCH",
    }
    structural = [row for row in refusals if row["code"] in structural_codes]

    lifecycle: dict[str, dict] = {}
    for verb in LIFECYCLE_VERBS:
        support, source = _support(family, transport, verb, declared_tools)
        reason = None
        if structural:
            dispatch = "REFUSED"
            reason = structural[0]["code"]
        elif support == "UNSUPPORTED":
            dispatch = "REFUSED"
            reason = "ACTION_UNSUPPORTED"
        elif support != "SUPPORTED":
            dispatch = "UNKNOWN"
            reason = "ACTION_UNKNOWN"
        elif installed is False:
            dispatch = "REFUSED"
            reason = "PROVIDER_NOT_INSTALLED"
        elif readiness in ("UNAVAILABLE", "INCOMPATIBLE"):
            dispatch = "REFUSED"
            reason = ("NEEDS_LOGIN" if readiness_detail == "NEEDS_LOGIN"
                      else "PROVIDER_NOT_READY")
        elif readiness == "UNKNOWN":
            dispatch = "UNKNOWN"
            reason = "PROVIDER_READINESS_UNKNOWN"
        elif verb != "create" and not session_ref_ok:
            dispatch = "REFUSED"
            reason = "SESSION_REF_MISMATCH"
        elif verb != "create" and session_state == "NOT_OBSERVED":
            dispatch = "UNKNOWN"
            reason = "NO_SESSION_OBSERVED"
        elif verb != "create" and session_state == "STALE":
            dispatch = "REFUSED"
            reason = "SESSION_STALE"
        elif verb != "create" and session_state == "UNKNOWN":
            dispatch = "REFUSED"
            reason = "SESSION_FRESHNESS_UNKNOWN"
        else:
            scope, flag = REQUIRED_GRANT[verb]
            if not grant_present:
                dispatch = "REFUSED"
                reason = "GRANT_MISSING"
            elif scope not in grant_scopes:
                dispatch = "REFUSED"
                reason = f"GRANT_MISSING:{scope}"
            elif flag is not None and not grant_flags.get(flag, False):
                dispatch = "REFUSED"
                reason = f"GRANT_MISSING:{flag}"
            else:
                dispatch = "PRECONDITIONS_MET"
        row = {"support": support, "support_source": source, "dispatch": dispatch}
        if reason is not None:
            row["dispatch_reason"] = reason
        lifecycle[verb] = row

    return {
        "schema": SCHEMA,
        "evidence_level": EVIDENCE_LEVEL,
        "evaluated_at": observation.get("evaluated_at") if _text(observation.get("evaluated_at")) else None,
        "identity": {
            "device_ref": device_ref if _text(device_ref) else None,
            "device_owner": device_owner,
            "os_principal": os_principal_out,
            "credential_instance_ref": credential_out,
            "profile": profile,
            "family": family_out,
            "agent_runtime": agent_runtime if _text(agent_runtime) else None,
            "runtime_tag": declared_tag if _text(declared_tag) else None,
            "transport": transport_out,
            "project": project_out,
        },
        "provider": {
            "installed": installed,
            "readiness": readiness,
            "readiness_detail": readiness_detail,
            "binding": "BOUND" if bound else "UNBOUND",
            "declared_native_tools": sorted(declared_tools) if declared_tools is not None else None,
        },
        "session": {"ref": session_ref_out, "state": session_state},
        "defaults": {
            "requested": _defaults_view(observation.get("defaults"), "requested"),
            "resolved": _defaults_view(observation.get("defaults"), "resolved"),
            "effective": _effective_view(
                (observation.get("defaults") or {}).get("effective")
                if isinstance(observation.get("defaults"), dict) else None),
        },
        "lifecycle": lifecycle,
        "refusals": refusals,
        "blockers": blockers,
        # No fallback is attempted by construction. The field exists so a reader
        # can see that absence is an answer rather than an omitted attempt.
        "fallback": None,
        "session_owner": SESSION_OWNER,
        "action": ACTION,
        "authorization": AUTHORIZATION,
    }
