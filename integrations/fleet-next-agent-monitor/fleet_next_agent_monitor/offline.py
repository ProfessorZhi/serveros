"""An offline facts client: synthetic JSON, no network, no credential.

Why this exists: the monitor core must be *executable* on a host that cannot
reach the Legacy Fleet, and PR #490 already established the pattern of proving
the read path against synthetic answers ("纯离线、假 client"). This client is
that pattern, and it is named so that no reader can mistake it for a live one:

*   it reads one JSON document from disk and answers from it;
*   it has no HTTP library, no token, no host and no port;
*   it can simulate the three refusals a real client raises — `RunNotFound`,
    `RouteOffline`, `RemoteRefused` — plus a dropped network, because those are
    exactly the fail-closed paths the monitor must prove.

It is a *client*, not a substitute for the monitor: the adapter, the core, the
durable state and the cross-process tests are all the shipping code.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .sources import RemoteRefused, RouteOffline, RunNotFound

#: Facts a synthetic answer may carry. Closed on purpose: a key this client does
#: not understand would be a behaviour the tests silently do not cover.
#:
#: `transcript` and `messages` are carriers of the same fact the monitor already
#: reads — the run's last assistant turn — and the native client answers with
#: them. Refusing the key here made the two read paths disagree on one run fact
#: (#508 P1): the adapter reached `TERMINAL_VERIFIED`, this reader raised
#: `ValueError` before any classifier saw it. The key is accepted for its *shape*
#: here and bounded where it is read (`sources._normalise_native`); accepting a
#: carrier is not accepting it as evidence.
RUN_KEYS = frozenset({
    "instanceId", "deviceRef", "sessionRef", "sessionId", "runId", "deliveryId",
    "bootId", "status", "lastAssistantMessage", "artifacts", "note",
    "transcript", "messages",
})
CI_KEYS = frozenset({
    "head_sha", "status", "conclusion", "draft", "verified", "trusted", "name", "id",
})


class OfflineFactsClient:
    """Answers `read_run` / `read_ci` from one JSON document."""

    def __init__(self, document: Mapping[str, Any]) -> None:
        if not isinstance(document, Mapping):
            raise ValueError("an offline facts document is an object")
        self.fleet_instance_id = str(document.get("fleet_instance_id") or "")
        self.route_id = str(document.get("route_id") or "")
        self.boot_id = document.get("boot_id")
        self.provider = document.get("provider")
        self._runs = dict(document.get("runs") or {})
        self._ci = dict(document.get("ci") or {})
        #: Counts every provider call, so a test can prove "zero reads".
        self.reads = 0

    @classmethod
    def from_path(cls, path: str | Path) -> "OfflineFactsClient":
        raw = Path(path).read_text(encoding="utf-8")
        return cls(json.loads(raw))

    def read_run(self, *, device_ref: str, session_ref: str,
                 run_id: str | None = None,
                 delivery_id: str | None = None) -> Mapping[str, Any]:
        self.reads += 1
        key = run_id or f"delivery:{delivery_id}"
        fact = self._runs.get(key)
        if fact is None:
            raise RunNotFound("NEVER_ISSUED", False)
        return _response(fact, RUN_KEYS)

    def read_ci(self, *, source_sha: str, project: str | None = None) -> Mapping[str, Any]:
        self.reads += 1
        fact = self._ci.get(source_sha)
        if fact is None:
            raise RunNotFound("NEVER_ISSUED", False)
        return _response(fact, CI_KEYS)


def _response(fact: Any, allowed: frozenset[str]) -> Mapping[str, Any]:
    """Interpret the fixture's sentinel keys as the refusals a live client raises."""
    if not isinstance(fact, Mapping):
        raise ValueError("a fixture fact is an object")
    surprises = set(fact) - allowed - {"raise"}
    if surprises:
        raise ValueError(f"unknown fixture key(s): {sorted(surprises)}")
    behaviour = fact.get("raise")
    if behaviour == "RUN_NOT_FOUND":
        raise RunNotFound(str(fact.get("reason") or "NEVER_ISSUED"),
                          bool(fact.get("retryable", False)))
    if behaviour == "EXPIRED":
        raise RunNotFound("EXPIRED", False)
    if behaviour == "OFFLINE":
        raise RouteOffline()
    if behaviour == "REFUSED":
        raise RemoteRefused()
    if behaviour == "NETWORK":
        raise ConnectionResetError("simulated drop")
    if behaviour is not None:
        raise ValueError(f"unknown fixture behaviour {behaviour!r}")
    return {key: value for key, value in fact.items() if key != "raise"}
