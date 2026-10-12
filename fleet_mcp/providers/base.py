"""The provider boundary: what a device must be able to answer.

A provider is a transport adapter, in `fleet/transport.py`'s sense: replacing
CloudCLI-over-HTTP with something else must change nothing above this line.
It answers "how do I reach it"; it does not decide who may do what.

Deliberately absent from this interface, mirroring `DeviceTransport`'s own
note: a generic `shell(device, command)` or `call(device, tool_name)`. A
provider that could forward any provider tool would make the Fleet surface a
transparent proxy to a device — the caller would be choosing the operation, and
there would be nothing left in the call to govern. Each verb below is named,
bounded, and answers one question.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from ..identity import SessionKey

# --------------------------------------------------------------------------
# Session classification
# --------------------------------------------------------------------------
#
# The distinction this surface exists to keep: a session that a provider knows
# about is not a session that is running. A transcript file on disk, a row in a
# database, and a live agent process are three different pieces of evidence,
# and only the last one is "an agent is working right now".
#
# CloudCLI reports two independent things — `running` (a run is executing) and
# `hostState` (a per-session host process is alive: `busy`, `lingering`, or
# absent). This surface carries both through unchanged rather than deriving one
# from the other, because a resident host that is idle is a real and useful
# state ("its working directory is warm, but nothing is happening") and it is
# not the same as a running agent.

RUNNING_AGENT = "RUNNING_AGENT"
"""A run is executing in this session right now."""

RESIDENT_HOST = "RESIDENT_HOST"
"""A per-session host process is alive, and no run is executing.

`hostState` is present and `running` is false. The session is warm and
addressable; it is not working.
"""

MANAGED_IDLE = "MANAGED_IDLE"
"""The provider manages this session and nothing is running for it."""

ARCHIVED = "ARCHIVED"
"""The provider reports the session as archived."""


@dataclass(frozen=True)
class SessionSummary:
    key: SessionKey
    title: str
    classification: str
    running: bool
    resident_host: bool
    host_state: str | None
    lifecycle_mode: str | None
    project_id: str | None
    last_activity: dict[str, Any] | None
    current_run: dict[str, Any] | None = None
    # The provider fields the classification was read from. Carried so that a
    # consumer can audit the reading rather than trust the word — the whole
    # point of the classification is that it is a *derivation*, and a
    # derivation without its inputs cannot be checked.
    evidence: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            **self.key.as_dict(),
            "title": self.title,
            "classification": self.classification,
            "running": self.running,
            "resident_host": self.resident_host,
            "host_state": self.host_state,
            "lifecycle_mode": self.lifecycle_mode,
            "project_id": self.project_id,
            "last_activity": self.last_activity,
            "current_run": self.current_run,
            "evidence": self.evidence,
        }


def classify(raw: dict[str, Any]) -> tuple[str, bool, bool, dict[str, Any]]:
    """Derive (classification, running, resident_host, evidence) from one row.

    Pure, so it can be tested against recorded provider rows rather than only
    against a live device — the classification is the part most likely to be
    wrong in a way nobody notices, and a rule that can only be exercised by
    talking to production is a rule that gets changed without being tested.
    """
    running = bool(raw.get("running") is True)
    host_state = raw.get("hostState") or raw.get("host_state")
    resident = bool(host_state) and not running
    archived = bool(raw.get("isArchived") or raw.get("is_archived"))

    if archived and not running:
        verdict = ARCHIVED
    elif running:
        verdict = RUNNING_AGENT
    elif resident:
        verdict = RESIDENT_HOST
    else:
        verdict = MANAGED_IDLE

    evidence = {
        "provider.running": raw.get("running"),
        "provider.hostState": host_state,
        "provider.isArchived": raw.get("isArchived"),
        "rule": "running -> RUNNING_AGENT; hostState set -> RESIDENT_HOST; "
                "archived -> ARCHIVED; else MANAGED_IDLE",
    }
    return verdict, running, resident, evidence


def summary_from_dict(row: dict[str, Any]) -> SessionSummary:
    """Rebuild a summary from the wire form `SessionSummary.as_dict` emits.

    Used by the node-connector path, where the summary was built on the far
    side. Every field the classification rested on travels with it, so this is
    a re-hydration rather than a second reading — a consumer inspecting
    `evidence` sees the same provider fields the connector saw.
    """
    return SessionSummary(
        key=SessionKey.parse(row["session_ref"]),
        title=str(row.get("title") or ""),
        classification=str(row.get("classification") or MANAGED_IDLE),
        running=bool(row.get("running")),
        resident_host=bool(row.get("resident_host")),
        host_state=row.get("host_state"),
        lifecycle_mode=row.get("lifecycle_mode"),
        project_id=row.get("project_id"),
        last_activity=row.get("last_activity"),
        current_run=row.get("current_run"),
        evidence=row.get("evidence") or {},
    )


@dataclass(frozen=True)
class ProviderHealth:
    device_ref: str
    kind: str
    reachable: bool
    authorized: bool
    detail: str
    server_name: str | None = None
    server_version: str | None = None
    tool_count: int | None = None
    error_code: str | None = None
    # `latency_ms` is what this surface measured with its own clock, not what
    # the device promised. When two callers' probes are coalesced it describes
    # the one probe they shared, which is why a payload must not present it as
    # a per-caller figure. Nothing here is served from a completed earlier call.
    latency_ms: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "device_ref": self.device_ref,
            "kind": self.kind,
            "reachable": self.reachable,
            "authorized": self.authorized,
            "detail": self.detail,
            "server_name": self.server_name,
            "server_version": self.server_version,
            "tool_count": self.tool_count,
            "error_code": self.error_code,
            "latency_ms": self.latency_ms,
        }


@runtime_checkable
class DeviceProvider(Protocol):
    """How this runtime reaches one device's agent sessions."""

    device_ref: str
    kind: str

    def health(self) -> ProviderHealth:
        """Measure now, or join a probe already in flight for the same device
        and the same caller. Never an answer from a completed earlier call."""

    def list_sessions(
        self, *, limit: int, project: str | None = None, state: str | None = None
    ) -> list[SessionSummary]:
        ...

    def get_session(self, key: SessionKey) -> dict[str, Any]:
        ...

    def read_session(
        self,
        key: SessionKey,
        *,
        mode: str = "latest",
        limit: int = 20,
        around_id: str | None = None,
        before: int | None = None,
        after: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        ...

    def send(
        self, key: SessionKey, message: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        ...

    def get_run(
        self, key: SessionKey, run_id: str, *, wait: bool = False, wait_seconds: float = 0.0
    ) -> dict[str, Any]:
        ...
