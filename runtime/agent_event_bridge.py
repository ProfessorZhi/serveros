#!/usr/bin/env python3
"""Pure Agent-hook adapter for ServerOS's EXISTING notification vocabulary.

This adapter neither sends notifications nor writes state. A trusted local
observer must supply device identity, collection time and a stable occurrence
reference. Provider payloads are untrusted; no raw prompt, error, path or tool
arguments are copied into a notification. This is not an approval decision.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from typing import Any
from runtime import agent_sessions, notifications

SCHEMA = "serveros/agent-event-observation.v1"
MAX_REF_LENGTH = 512


class EventNotEstablished(ValueError):
    pass


def _ref(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise EventNotEstablished(f"{name} missing")
    value = value.strip()
    if len(value) > MAX_REF_LENGTH or any(ord(ch) < 32 for ch in value):
        raise EventNotEstablished(f"{name} invalid")
    return value


def _observed_at(value: str) -> str:
    value = _ref(value, "observed_at")
    try:
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise EventNotEstablished("observed_at must be ISO8601") from None
    if date.utcoffset() is None:
        raise EventNotEstablished("observed_at must contain timezone")
    return value


def _meaning(provider: str, signal: dict) -> tuple[str, str | None]:
    if provider == "claude":
        name = _ref(signal.get("hook_event_name"), "hook_event_name")
        if name == "Stop" and signal.get("stop_hook_active") is not True:
            return "turn.completed", signal.get("session_id")
        if name == "PermissionRequest":
            return "approval.required", signal.get("session_id")
        raise EventNotEstablished("Claude hook does not establish this event")
    if provider == "codex":
        if signal.get("method") != "turn/completed":
            raise EventNotEstablished("Codex event is not terminal")
        params = signal.get("params")
        turn = params.get("turn") if isinstance(params, dict) else None
        if not isinstance(turn, dict):
            raise EventNotEstablished("Codex turn evidence absent")
        event = {"completed": "turn.completed",
                 "failed": "turn.failed"}.get(turn.get("status"))
        if not event:
            raise EventNotEstablished("Codex turn not terminal")
        return event, params.get("threadId")
    raise EventNotEstablished("unknown provider")


def normalize(*, provider: str, device_ref: str, session_id: str,
              occurrence_ref: str, observed_at: str, signal: dict) -> dict:
    """Return a sanitized notification candidate, never a confirmed delivery.

    The occurrence_ref must be the stable provider/CloudCLI run/turn/request id,
    not a fresh UUID generated at each retry. Caller authorization, durable
    storage and delivery stay with ServerOS Work/Policy/Notifications.
    """
    if provider not in ("claude", "codex") or not isinstance(signal, dict):
        raise EventNotEstablished("invalid provider event")
    device = _ref(device_ref, "device_ref")
    session = _ref(session_id, "session_id")
    occurrence = _ref(occurrence_ref, "occurrence_ref")
    at = _observed_at(observed_at)
    meaning, claimed_session = _meaning(provider, signal)
    if claimed_session is not None and claimed_session != session:
        raise EventNotEstablished("provider session differs from authenticated subject")
    if meaning not in notifications.events():
        raise EventNotEstablished("undeclared ServerOS event")
    canonical = agent_sessions.session_ref(device, provider, session)
    fingerprint = json.dumps([provider, device, session, occurrence, meaning],
                             separators=(",", ":"), ensure_ascii=False)
    correlation = hashlib.sha256(fingerprint.encode("utf-8")).hexdigest()[:32]
    item = notifications.Event(event=meaning, device_ref=device, at=at,
                               correlation_id=correlation, session_ref=canonical)
    return {
        "schema": SCHEMA,
        "notification": item.show(),
        "idempotency_key": item.idempotency_key(),
        "origin": {"provider": provider, "occurrence_ref": occurrence,
                   "trust": "provider-reported"},
        "requires_local_authorization": True,
    }
