#!/usr/bin/env python3
"""R1 #248 — authorization projection for the 2026-10-20 Runtime Session matrix.

WHAT THIS IS
------------
A pure projection from one *observation* plus one *requested verb* to one
authorization record. It answers the question the R1 lane actually needs:

    Given this device, OS principal, family, transport, runtime binding,
    provider support, readiness, grant and context, may this verb be
    dispatched — and if not, which authority refused it?

It is deliberately narrower than ``fleet-v1-runtime-parity/parity.py``. That
module projects a SOURCE_FIXTURE capability matrix. This module projects the
*authorization chain* observed on 2026-10-20, and it keeps three different
"no" answers apart:

    REFUSED   the source refused, in the source's own reason vocabulary
    UNKNOWN   nothing refused, and a fact needed for a decision is unevaluated
    BLOCKED   an owner/enrollment/consent boundary stops the lane before the
              source is asked

A ``PRECONDITIONS_MET`` result is not a grant. Admission remains with Agent
Control; this module only says the source-level gates did not refuse.

WHAT THIS IS NOT
----------------
* not a Session owner: it creates, sends, resumes and interrupts nothing;
* not an authorizer: it does not mint a context_ref, a grant or an approval;
* not a live reader: no socket, file, process, clock, environment or
  credential read. ``evaluated_at`` is supplied by the caller;
* not a second registry: every fact comes from the observation record, and
  every reason comes from the source's closed vocabulary.

THE ORDER THAT MATTERS
----------------------
For the ServerOS Agent MCP surface the source checks, in this order:

    runtime declared -> provider support -> execution readiness -> admission
    -> thread binding (create/send/resume)

The first refusal wins. The order is visible in the fixture on purpose: Codex
on Tokyo is refused at the *runtime* step even though its provider declares
``session.interrupt`` SUPPORTED, and Claude Code is refused at the *support*
step because BotMux publishes no instance-level ``actions()`` table even
though its per-session row says ``session.inspect`` SUPPORTED.

SOURCE NOTES (authoritative main 771c81c)
-----------------------------------------
* ``runtime/agent-session-contract.yaml#admission``:
  read_only = list/inspect/result; delegation = create/resume/send;
  human_gated = interrupt/stop/handoff.
* ``fleet/agent_control_router.py#provider_action_support`` reads
  ``provider.actions()``. BotMux has no public ``actions()``; its
  per-session ``_actions()`` table is not consulted by the resolver.
* ``abilities/target.py#_step_agent_runtime`` reads the registry's
  ``agents`` projection, not ``runtime_bindings``.
* ``mcp/agent_mcp.py`` deliberately exposes no ``agent.session.interrupt``.
  ``agent.session.stop`` is the end verb; ``agent.session.result`` is the
  get_run analogue.
* ``fleet_mcp/cloudcli_native.py`` does not exist at 771c81c. The live
  CloudCLI MCP surface is a separate control plane with its own bearer token;
  it is not a ServerOS provider and its token is not a ServerOS grant.
"""
from __future__ import annotations

import datetime
import re

SCHEMA = "serveros/fleet-v1-runtime-authorization.v1"
VERSION = 1
EVIDENCE_LEVEL = "SOURCE_READ"
SESSION_OWNER = "Agent Control / Agent Session service (not this fixture)"
AUTHORITY = "ADMISSION_REMAINS_WITH_AGENT_CONTROL"

VERBS = ("read", "create", "send", "get_run", "resume", "interrupt", "end")
SURFACES = (
    "serveros-agent-mcp",
    "cloudcli-mcp",
    "codex-app-server",
    "botmux-agent-session",
    "claude-code-native",
)
FAMILIES = ("claude", "codex")
AGENT_RUNTIME = {
    "claude": "agent-runtime:claude-code",
    "codex": "agent-runtime:codex",
}

ADMISSION_CLASS = {
    "read": "read_only",
    "get_run": "read_only",
    "create": "delegation",
    "send": "delegation",
    "resume": "delegation",
    "interrupt": "human_gated",
    "end": "human_gated",
}
MUTATING = {
    "read": False,
    "get_run": False,
    "create": True,
    "send": True,
    "resume": True,
    "interrupt": True,
    "end": True,
}

# The ServerOS Agent MCP tool for each verb. `None` is an answer, not an
# omission: the surface deliberately does not expose interrupt.
SERVEROS_MCP_TOOL = {
    "read": "agent.session.inspect | agent.session.result | agent.session.list",
    "create": "agent.session.create",
    "send": "agent.session.send",
    "get_run": "agent.session.result",
    "resume": "agent.session.resume",
    "interrupt": None,
    "end": "agent.session.stop",
}

# The CloudCLI MCP tool for each verb, as visible to this session.
CLOUDCLI_TOOL = {
    "read": "sessions_list | session_get | session_read",
    "create": "session_create",
    "send": "session_send",
    "get_run": "run_get",
    "resume": None,
    "interrupt": "session_interrupt",
    "end": "session_close",
}

# The source's closed reason vocabulary. This list is a copy because the
# fixture has no YAML loader; a test compares it with the contract file, so it
# cannot drift silently.
REASONS = (
    "DEVICE_UNREACHABLE",
    "NODE_RUNTIME_UNAVAILABLE",
    "AGENT_RUNTIME_UNAVAILABLE",
    "PROVIDER_UNAVAILABLE",
    "ACTION_UNSUPPORTED",
    "READINESS_UNKNOWN",
    "POLICY_DENIED",
    "APPROVAL_REQUIRED",
    "APPROVAL_BINDING_MISMATCH",
    "APPROVAL_CONTEXT_MISMATCH",
    "APPROVAL_EXPIRED",
    "APPROVAL_ALREADY_CONSUMED",
    "SESSION_NOT_FOUND",
    "TURN_FAILED",
    "PROVIDER_ERROR",
    "TIMEOUT",
    "INVALID_CONTEXT",
    "UNTRUSTED_ACTOR",
    "THREAD_ITEM_MISMATCH",
    "READ_ONLY_CLOSURE",
    "THREAD_CLOSED_FOR_NEW_WORK",
)

# R1-level boundaries. These are NOT source reason words; they say which
# authority stopped the lane before the source could answer.
BLOCKED_CODES = (
    "BLOCKED_OWNER_LOGIN",
    "BLOCKED_OWNER_CONSENT",
    "BLOCKED_GRANT_UNKNOWN",
    "BLOCKED_RUNTIME_NOT_DECLARED",
    "BLOCKED_PROVIDER_SUPPORT_UNPUBLISHED",
    "BLOCKED_PROVIDER_ADAPTER_ABSENT",
    "BLOCKED_EXECUTION_UNVERIFIED",
    "BLOCKED_MCP_TOOL_ABSENT",
    "BLOCKED_PROTOCOL_FIELD_MISMATCH",
    "BLOCKED_NODE_RUNTIME_UNAVAILABLE",
    "BLOCKED_DEVICE_NOT_ENROLLED",
    "BLOCKED_AUTHORED_NOT_LIVE",
)

DECISIONS = ("PRECONDITIONS_MET", "REFUSED", "UNKNOWN", "BLOCKED")

_TAG = re.compile(r"^[a-z][a-z0-9._-]{0,31}$")
_ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z$")
_SECRET_SHAPED = re.compile(
    r"(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9]{8,}|"
    r"xox[baprs]-[A-Za-z0-9-]{8,}|Bearer\s+\S{8,})",
    re.IGNORECASE,
)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _iso(value):
    if not _text(value):
        return None
    value = value.strip()
    if not _ISO.fullmatch(value):
        return None
    try:
        datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return value


def _secret_like(value: str) -> bool:
    return bool(_SECRET_SHAPED.search(value))


def _record(decision, *, reason=None, detail=None, blocked=(), status=None,
            support=None, readiness=None):
    return {
        "decision": decision,
        "reason": reason,
        "detail": detail,
        "blocked_codes": list(blocked),
        "resolution_status": status or decision,
        "provider_support": support,
        "readiness": readiness,
    }


def _device(observation: dict, device_ref: str) -> dict | None:
    devices = observation.get("devices") or {}
    if isinstance(devices, dict):
        found = devices.get(device_ref)
        return found if isinstance(found, dict) else None
    if isinstance(devices, list):
        for row in devices:
            if isinstance(row, dict) and row.get("device_ref") == device_ref:
                return row
    return None


def _provider(observation: dict, surface: str, family: str) -> dict:
    providers = observation.get("providers") or {}
    if surface == "cloudcli-mcp":
        return providers.get("cloudcli") or {}
    if surface == "codex-app-server":
        return providers.get("codex") or {}
    if surface == "botmux-agent-session":
        return providers.get("botmux") or {}
    # ServerOS routes a family through the provider that implements it.
    return providers.get("codex" if family == "codex" else "botmux") or {}


def _effective(provider: dict) -> dict:
    """Accept an effective value only from a named FACT provider field.

    `requested`, `resolved`, a configuration value and a default label are
    never promoted. Model and permissionMode are resolved independently.
    """
    out = {}
    for field, key in (("model", "effective_model"),
                       ("permissionMode", "effective_permission_mode")):
        raw = provider.get(key) or {}
        value = raw.get("value")
        provenance = raw.get("provenance")
        source = raw.get("source")
        if value is None or provenance != "FACT" or not _text(source):
            out[field] = {
                "value": None,
                "provenance": "UNKNOWN",
                "source": None,
                "reason": (raw.get("reason")
                           or "no provider-response FACT field with a named source"),
            }
        else:
            out[field] = {
                "value": value,
                "provenance": "FACT",
                "source": source,
                "reason": None,
            }
    return out


def _grant_allows(grant, verb: str) -> str:
    """'ALLOW', 'DENY' or 'UNKNOWN'. Never inferred from another verb."""
    if not isinstance(grant, dict):
        return "UNKNOWN"
    scopes = set(grant.get("scopes") or ())
    flag = {
        "read": None,
        "get_run": None,
        "create": "allow_create",
        "send": "allow_send",
        "resume": "allow_control",
        "interrupt": "allow_control",
        "end": "allow_control",
    }[verb]
    scope = "fleet:read" if verb in ("read", "get_run") else (
        "fleet:create" if verb == "create" else
        "fleet:send" if verb == "send" else "fleet:control")
    if scope not in scopes:
        return "DENY"
    if flag is not None:
        if grant.get(flag) is True:
            return "ALLOW"
        if grant.get(flag) is False:
            return "DENY"
        return "UNKNOWN"
    return "ALLOW"


def _serveros(observation, request, *, device, provider, family, verb) -> dict:
    # 1. Surface tool availability. `interrupt` is deliberately absent from the
    #    MCP surface even though the router and the Codex provider have the
    #    verb. `stop` is the end verb.
    if verb == "interrupt":
        return _record(
            "REFUSED", reason="ACTION_UNSUPPORTED",
            detail=("agent.session.interrupt is not exposed by the Agent MCP "
                    "surface; the semantic end verb is agent.session.stop"),
            blocked=("BLOCKED_MCP_TOOL_ABSENT",))
    if verb == "get_run" and not SERVEROS_MCP_TOOL.get(verb):
        return _record("REFUSED", reason="ACTION_UNSUPPORTED",
                       detail="no get_run tool on this surface")

    # 2. Read-only direct reads (agent.session.list) do not pass the resolver.
    #    The two read scopes are not interchangeable: BotMux authorizes
    #    session metadata while its agent inventory requires dashboard login.
    route = request.get("route") or "routed"
    if verb == "read" and route == "direct":
        scope = request.get("read_scope") or "session_metadata"
        if scope == "agent_inventory" and (
                (provider.get("agent_inventory") or {}).get("state") == "NEEDS_LOGIN"):
            return _record(
                "BLOCKED",
                detail=("BotMux agent inventory requires dashboard authentication; "
                        "session metadata remains readable"),
                blocked=("BLOCKED_OWNER_LOGIN",))
        return _record("PRECONDITIONS_MET", status="AVAILABLE",
                       detail=f"direct service read ({scope}); no resolver step")

    # 3. Device identity and enrollment. An authored-only row is not a live
    #    identity: the live control plane still refuses it.
    if device is None:
        return _record(
            "BLOCKED", detail=f"{request.get('device_ref')} is not enrolled in "
                              f"the live control plane",
            blocked=("BLOCKED_DEVICE_NOT_ENROLLED",))
    if device.get("authored_only"):
        return _record(
            "BLOCKED",
            detail=("the device exists only in an uncommitted registry working "
                    "tree; the live control plane has not observed it"),
            blocked=("BLOCKED_AUTHORED_NOT_LIVE",))

    # 4. Runtime declaration. This is the step the source runs before support.
    declared = tuple(device.get("declared_runtimes") or ())
    runtime = AGENT_RUNTIME[family]
    if runtime not in declared:
        return _record(
            "REFUSED", reason="AGENT_RUNTIME_UNAVAILABLE",
            detail=(f"{request.get('device_ref')} does not declare {runtime}; "
                    f"it declares {', '.join(declared) or 'none'}"),
            blocked=("BLOCKED_RUNTIME_NOT_DECLARED",))

    # 5. Remote node runtime. The local loopback is served without a connector;
    #    every other device needs one.
    if not device.get("local") and device.get("node_runtime") == "ABSENT":
        return _record(
            "REFUSED", reason="NODE_RUNTIME_UNAVAILABLE",
            detail=(f"no ServerOS node runtime is deployed on "
                    f"{request.get('device_ref')}"),
            blocked=("BLOCKED_NODE_RUNTIME_UNAVAILABLE",))

    # 6. Provider support, from the provider's own actions() table.
    support_table = provider.get("instance_action_support") or {}
    support = support_table.get(f"session.{_verb_map(verb)}")
    if not support_table:
        return _record(
            "REFUSED", reason="ACTION_UNSUPPORTED",
            detail=("the provider publishes no instance-level action_support "
                    "table; per-session support is not consulted by the resolver"),
            blocked=("BLOCKED_PROVIDER_SUPPORT_UNPUBLISHED",),
            support={"state": "UNKNOWN"})
    if support == "UNSUPPORTED":
        return _record("REFUSED", reason="ACTION_UNSUPPORTED",
                       detail=f"{provider.get('provider')} does not support "
                              f"session.{_verb_map(verb)}",
                       support={"state": "UNSUPPORTED"})
    if support != "SUPPORTED":
        return _record(
            "REFUSED", reason="ACTION_UNSUPPORTED",
            detail=(f"whether {provider.get('provider')} supports "
                    f"session.{_verb_map(verb)} is unestablished"),
            blocked=("BLOCKED_PROVIDER_SUPPORT_UNPUBLISHED",),
            support={"state": support or "UNKNOWN"})

    # 7. Readiness. A known negative refuses; UNKNOWN is unevaluated, not a
    #    refusal, and the router reports the overall resolution as UNKNOWN.
    readiness_state = provider.get("execution_readiness") or "UNKNOWN"
    readiness = {"state": readiness_state,
                 "reason": provider.get("execution_readiness_reason")}
    if readiness_state in ("UNAVAILABLE", "INCOMPATIBLE"):
        return _record("REFUSED", reason="PROVIDER_UNAVAILABLE",
                       detail=f"provider readiness is {readiness_state}",
                       readiness=readiness, support={"state": "SUPPORTED"})
    unevaluated = readiness_state == "UNKNOWN"

    # 8. Admission. Reads pass; delegation needs a grant; human-gated needs an
    #    identified approval.
    klass = ADMISSION_CLASS[verb]
    if klass == "delegation":
        if not _text(request.get("context_ref")):
            return _record("REFUSED", reason="INVALID_CONTEXT",
                           detail="a delegation mutation requires context_ref",
                           readiness=readiness, support={"state": "SUPPORTED"})
        allowed = _grant_allows(request.get("grant"), verb)
        if allowed == "DENY":
            return _record("REFUSED", reason="POLICY_DENIED",
                           detail="the presented grant does not name this verb",
                           readiness=readiness, support={"state": "SUPPORTED"})
        if allowed == "UNKNOWN":
            return {
                **_record("UNKNOWN", reason=None,
                          detail="no grant is readable for this verb; UNKNOWN is "
                                 "not a deny and not an allow",
                          blocked=("BLOCKED_GRANT_UNKNOWN",),
                          readiness=readiness, support={"state": "SUPPORTED"}),
                "resolution_status": "UNKNOWN",
            }
    if klass == "human_gated":
        if not _text(request.get("context_ref")):
            return _record("REFUSED", reason="INVALID_CONTEXT",
                           detail="a human-gated mutation requires context_ref",
                           readiness=readiness, support={"state": "SUPPORTED"})
        if not _text(request.get("approval_ref")):
            return _record("REFUSED", reason="APPROVAL_REQUIRED",
                           detail="human-gated verb without an identified approval",
                           readiness=readiness, support={"state": "SUPPORTED"})
        if request.get("approval_ref") != request.get("expected_approval_ref"):
            return _record("REFUSED", reason="APPROVAL_BINDING_MISMATCH",
                           detail="the presented approval is not bound to this request",
                           readiness=readiness, support={"state": "SUPPORTED"})

    # 9. §17 thread binding. create binds; send/resume admit against a binding.
    if verb == "create":
        role = request.get("session_role")
        bound = request.get("bound_item_ref")
        if role not in ("coordinator", "execution"):
            return _record("REFUSED", reason="INVALID_CONTEXT",
                           detail="session_role is required and has no default",
                           readiness=readiness, support={"state": "SUPPORTED"})
        if role == "execution" and not _text(bound):
            return _record("REFUSED", reason="INVALID_CONTEXT",
                           detail="execution requires bound_item_ref",
                           readiness=readiness, support={"state": "SUPPORTED"})
        if role == "coordinator" and _text(bound):
            return _record("REFUSED", reason="INVALID_CONTEXT",
                           detail="coordinator may not be bound to an Item",
                           readiness=readiness, support={"state": "SUPPORTED"})
    if verb in ("send", "resume") and request.get("binding") in (None, "missing"):
        return _record("REFUSED", reason="INVALID_CONTEXT",
                       detail="no thread binding was recorded for this session",
                       readiness=readiness, support={"state": "SUPPORTED"})

    if unevaluated:
        return _record(
            "UNKNOWN", reason=None,
            detail=("no step refused; execution readiness was never established, "
                    "so the resolver reports the target as UNKNOWN and the router "
                    "does not dispatch"),
            blocked=("BLOCKED_EXECUTION_UNVERIFIED",),
            status="UNKNOWN", readiness=readiness,
            support={"state": "SUPPORTED"})

    return _record("PRECONDITIONS_MET", status="AVAILABLE",
                   detail="source-level preconditions pass; admission remains "
                          "with Agent Control",
                   readiness=readiness, support={"state": "SUPPORTED"})


def _verb_map(verb: str) -> str:
    # ServerOS spells the read verb `session.inspect`; `get_run` is
    # `session.result`; `end` is `session.stop`.
    return {"read": "inspect", "get_run": "result", "end": "stop"}.get(verb, verb)


def _cloudcli(observation, request, *, provider, family, verb) -> dict:
    tool = CLOUDCLI_TOOL.get(verb)
    if verb == "resume":
        return _record(
            "REFUSED", reason="ACTION_UNSUPPORTED",
            detail=("CloudCLI exposes no session_resume; session_start starts a "
                    "resident host, it is not a resume"),
            blocked=("BLOCKED_MCP_TOOL_ABSENT",))
    if verb == "get_run":
        return _record(
            "UNKNOWN", reason=None,
            detail=("run_get is declared with parameter `run`, while the server "
                    "requires `runId`; the run id exposed by session_get for an "
                    "active host lease was not resolvable (`This runId was never "
                    "issued.`)"),
            blocked=("BLOCKED_PROTOCOL_FIELD_MISMATCH",))
    if verb in ("create", "send", "interrupt", "end"):
        return _record(
            "BLOCKED",
            detail=("CloudCLI mutations are authorized by the ambient bearer "
                    "token held by the MCP client, not by a ServerOS context_ref "
                    "or grant. This lane did not create, send, interrupt or close "
                    "a real session."),
            blocked=("BLOCKED_OWNER_CONSENT",))
    if tool is None:
        return _record("REFUSED", reason="ACTION_UNSUPPORTED",
                       detail=f"no CloudCLI tool for {verb}")
    return _record("PRECONDITIONS_MET", status="AVAILABLE",
                   detail=f"CloudCLI read tool present: {tool}; the token is "
                          f"ambient and is not a ServerOS grant")


def _direct_provider(observation, request, *, provider, family, verb) -> dict:
    support_table = provider.get("instance_action_support") or {}
    if not support_table:
        return _record(
            "REFUSED", reason="ACTION_UNSUPPORTED",
            detail="the provider publishes no instance-level action_support",
            blocked=("BLOCKED_PROVIDER_SUPPORT_UNPUBLISHED",))
    support = support_table.get(f"session.{_verb_map(verb)}")
    if support == "UNSUPPORTED":
        return _record("REFUSED", reason="ACTION_UNSUPPORTED",
                       detail=f"provider declares session.{_verb_map(verb)} UNSUPPORTED",
                       support={"state": support})
    if support != "SUPPORTED":
        return _record("REFUSED", reason="ACTION_UNSUPPORTED",
                       detail=f"provider support for session.{_verb_map(verb)} is "
                              f"not SUPPORTED",
                       support={"state": support or "UNKNOWN"})
    if verb in ("read", "get_run"):
        return _record("PRECONDITIONS_MET", status="AVAILABLE",
                       detail="direct provider read; this bypasses Agent Control "
                              "admission and is used only for observation",
                       support={"state": support})
    return _record(
        "BLOCKED",
        detail=("a direct provider mutation bypasses the ServerOS admission "
                "chain; this lane did not execute it"),
        blocked=("BLOCKED_OWNER_CONSENT",), support={"state": support})


def project(observation: dict, request: dict) -> dict:
    """One authorization record. Pure, deterministic, no IO."""
    if not isinstance(observation, dict):
        raise ValueError("observation must be a mapping")
    if not isinstance(request, dict):
        raise ValueError("request must be a mapping")

    evaluated_at = _iso(request.get("evaluated_at") or observation.get("recorded_at"))
    device_ref = request.get("device_ref")
    surface = request.get("surface")
    family = request.get("family")
    verb = request.get("verb")
    runtime_tag = request.get("runtime_tag")

    errors = []
    if evaluated_at is None:
        errors.append(("INVALID_CONTEXT", "evaluated_at must be an ISO-8601 Z instant"))
    if not _text(device_ref) or not str(device_ref).startswith("device:"):
        errors.append(("INVALID_CONTEXT", "device_ref must be a typed device:<id> ref"))
    if surface not in SURFACES:
        errors.append(("INVALID_CONTEXT", f"surface must be one of {list(SURFACES)}"))
    if family not in FAMILIES:
        errors.append(("INVALID_CONTEXT", f"family must be one of {list(FAMILIES)}"))
    if verb not in VERBS:
        errors.append(("INVALID_CONTEXT", f"verb must be one of {list(VERBS)}"))
    if _text(runtime_tag) and _secret_like(runtime_tag):
        errors.append(("INVALID_CONTEXT",
                       "runtime_tag is credential-shaped and is refused without echo"))
    elif _text(runtime_tag) and not _TAG.fullmatch(runtime_tag):
        errors.append(("RUNTIME_TAG_MISMATCH",
                       "runtime_tag must be a provider tag, not an agent-runtime id"))
    if errors:
        reason, detail = errors[0]
        return _envelope(observation, request, evaluated_at, {
            **_record("REFUSED", reason=reason, detail=detail),
            "effective": {"model": {"value": None, "provenance": "UNKNOWN"},
                          "permissionMode": {"value": None, "provenance": "UNKNOWN"}},
        })

    device = _device(observation, device_ref)
    provider = _provider(observation, surface, family)

    if surface == "claude-code-native":
        result = _record(
            "BLOCKED",
            detail=("no Claude Code provider adapter exists at the authoritative "
                    "main 771c81c; integrations/fleet-cc-dev-plugin is a client "
                    "plugin, not a Session provider"),
            blocked=("BLOCKED_PROVIDER_ADAPTER_ABSENT",))
    elif surface == "serveros-agent-mcp":
        result = _serveros(observation, request, device=device,
                           provider=provider, family=family, verb=verb)
    elif surface == "cloudcli-mcp":
        result = _cloudcli(observation, request, provider=provider,
                           family=family, verb=verb)
    else:
        result = _direct_provider(observation, request, provider=provider,
                                  family=family, verb=verb)

    out = _envelope(observation, request, evaluated_at, result)
    out["effective"] = _effective(provider)
    return out


def _envelope(observation, request, evaluated_at, result) -> dict:
    family = request.get("family")
    verb = request.get("verb")
    surface = request.get("surface")
    klass = ADMISSION_CLASS.get(verb, "unknown")
    runtime_tag = request.get("runtime_tag")
    if _text(runtime_tag) and _secret_like(runtime_tag):
        # A credential-shaped value is refused by the caller before this
        # function runs; it must not be echoed back inside the refusal record.
        runtime_tag = "[REDACTED]"
    return {
        "schema": SCHEMA,
        "version": VERSION,
        "evidence_level": EVIDENCE_LEVEL,
        "evaluated_at": evaluated_at,
        "device_ref": request.get("device_ref"),
        "surface": surface,
        "family": family,
        "agent_runtime": AGENT_RUNTIME.get(family),
        "runtime_tag": runtime_tag,
        "verb": verb,
        "admission_class": klass,
        "mutating": MUTATING.get(verb, True),
        "requires_context_ref": klass in ("delegation", "human_gated"),
        "requires_approval": klass == "human_gated",
        "tool": (SERVEROS_MCP_TOOL.get(verb) if surface == "serveros-agent-mcp"
                 else CLOUDCLI_TOOL.get(verb) if surface == "cloudcli-mcp"
                 else None),
        "route": request.get("route") or "routed",
        "read_scope": request.get("read_scope"),
        **result,
        "effective": result.get("effective"),
        "grant": request.get("grant"),
        "fallback": None,
        "tier": "CC_FIRST" if family == "claude" else "FULL_V1_CODEX",
        "tier_state": "PARTIAL" if family == "claude" else "BLOCKED",
        "full_v1_codex": False,
        "session_owner": SESSION_OWNER,
        "authority": AUTHORITY,
        "not_run": [
            "the authorization projection executes no verb",
            "no model call, credential read, transcript read or system change",
        ],
    }


def matrix(observation: dict, requests: list[dict]) -> list[dict]:
    """Project many requests against one observation, in input order."""
    return [project(observation, request) for request in requests]
