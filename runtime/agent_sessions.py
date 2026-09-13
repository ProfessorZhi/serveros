#!/usr/bin/env python3
"""Provider-neutral Agent Session semantics for the Node Runtime Plane.

No provider I/O lives here. This module owns stable refs, the closed support
vocabulary and the admission decision that serverosd serves to control adapters.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import quote, unquote

import yaml

_HERE = Path(__file__).resolve().parent
CONTRACT = _HERE / "agent-session-contract.yaml"


class AgentSessionError(RuntimeError):
    pass


class UnknownSession(AgentSessionError):
    pass


def contract() -> dict:
    data = yaml.safe_load(CONTRACT.read_text()) or {}
    if not isinstance(data, dict):
        raise AgentSessionError(f"{CONTRACT} must contain a mapping")
    return data


def _segment(value: str) -> str:
    return quote(str(value), safe="")


def session_ref(device_ref: str, provider_id: str, provider_session_id: str) -> str:
    return "agent-session:" + "/".join(
        _segment(v) for v in (device_ref, provider_id, provider_session_id)
    )


def parse_session_ref(ref: str) -> tuple[str, str, str]:
    prefix = "agent-session:"
    if not str(ref).startswith(prefix):
        raise UnknownSession(f"{ref!r} is not an Agent Session ref")
    parts = str(ref)[len(prefix):].split("/")
    if len(parts) != 3 or any(not part for part in parts):
        raise UnknownSession(f"malformed Agent Session ref: {ref!r}")
    return tuple(unquote(part) for part in parts)  # type: ignore[return-value]


def agent_ref(device_ref: str, provider_id: str, provider_agent_id: str) -> str:
    return "agent:" + "/".join(
        _segment(v) for v in (device_ref, provider_id, provider_agent_id)
    )


def support(state: str, reason: str | None = None) -> dict:
    allowed = set(contract()["support"]["values"])
    if state not in allowed:
        raise AgentSessionError(f"unknown support state {state!r}; expected {sorted(allowed)}")
    out = {"state": state}
    if reason:
        out["reason"] = reason
    return out


def admission(action: str, context: dict | None = None) -> dict:
    """Pure intent admission. Provider capability is checked separately.

    `context` does not steer the decision today — every declared class is decided
    by the action alone. It is required anyway, and echoed back, for two reasons:
    a decision that is returned without its subject cannot be audited after the
    fact, and an admission function whose callers were never built to supply a
    subject acquires no way to acquire one later.
    """
    rules = contract()["admission"]
    if action in set(rules.get("read_only") or []):
        decision, klass = "ALLOW", "read_only"
        reason = "read-only Agent Session observation"
    elif action in set(rules.get("delegation") or []):
        decision, klass = "ALLOW", "delegation"
        reason = "delegation grants no new host privilege"
    elif action in set(rules.get("human_gated") or []):
        decision, klass = "REQUIRE_HUMAN", "human_gated"
        reason = "explicit current human approval is required"
    else:
        decision, klass = "DENY", "undeclared"
        reason = "action is not declared by the Agent Session contract"
    return {"action": action, "decision": decision, "class": klass,
            "reason": reason, "context": dict(context or {})}
