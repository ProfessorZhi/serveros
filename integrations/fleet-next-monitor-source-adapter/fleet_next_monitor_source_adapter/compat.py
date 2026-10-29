"""The versioned view that lets #493's monitor read this adapter unchanged.

The seam
--------
#493's monitor samples a source through two calls
(`integrations/fleet-next-agent-monitor/.../sources.py:494-504`):

```
observe_run(device_ref=, session_ref=, run_id=, delivery_id=, project=,
            expected_boot_id=, expected_source_sha=) -> mapping
observe_ci(source_sha=, project=)                          -> mapping
```

Independent QA (#495) put the two modules side by side and found three
differences, each pinned by a test rather than a sentence:

1.  the keyword names — #490 had `source_sha` and no watch-level boot, so
    `observe_run(..., expected_boot_id=...)` raised `TypeError`;
2.  a refusal — #490 raises `SourceRefused` *before* any provider call, #493's
    core reads an observation and carries on with the other watches;
3.  two words — #490 says `IDENTITY_MISMATCH` where #493 says `SHA_MISMATCH`,
    and `NO_IDENTIFIER` where #493 says `BAD_PAYLOAD`.

(1) is fixed in the adapter itself (`observe_run` now declares both keywords and
honours them), so the adapter is directly callable by `observe_watch`. This
module is the answer to (2) and (3), and it is the reason the fix is a *view*
with a version rather than a rewrite of the native contract:

*   the native refusal stays an exception, because refusing before the read is
    the stronger fail-closed form and the contract tests pin it;
*   the words stay native for the same reason, and are translated here, once,
    with the condition preserved (an UNKNOWN stays UNKNOWN — translation never
    upgrades a status);
*   the mapping this module returns is what #493's core can `.get()` from,
    which the typed `Observation` also supports, so a caller may use either.

What this module will not do
----------------------------
It does not swallow `expected_boot_id` or `expected_source_sha`: both are passed
through to the adapter, which compares them. A watch that names a different boot
gets `BOOT_MISMATCH`, and an artifact bound to another commit is not evidence.
It does not turn a configuration refusal (`BAD_GRANT`, `BAD_IDENTITY`,
`BLOCKED_UPSTREAM`) into an observation either: those are mistakes in the
caller or the wiring, not facts about the far end, and they stay exceptions.
"""
from __future__ import annotations

from typing import Any, Mapping

from .adapter import LegacyReadOnlySourceAdapter
from .contract import (
    REASON_BAD_PAYLOAD,
    REASON_IDENTITY_MISMATCH,
    REASON_NOT_GRANTED,
    REASON_SHA_MISMATCH,
    STATUS_DENIED,
    VIEW_CI,
    VIEW_RUN,
    Observation,
    SourceRefused,
)

#: The contract this module publishes. The version is in the string because a
#: second consumer with a different seam is the reason a version exists.
COMPAT_SCHEMA = "serveros/fleet-next-monitor-source-adapter.compat.v2"

#: The two translated words, keyed by view. Translation only renames a fact; it
#: never changes a status, so no reason here can move an observation into
#: `DISPATCHABLE`.
_RUN_REASONS = {"NO_IDENTIFIER": REASON_BAD_PAYLOAD}
_CI_REASONS = {REASON_IDENTITY_MISMATCH: REASON_SHA_MISMATCH}

#: Refusal codes that are a fact about the caller's grant. These are the ones
#: #493 renders as a `DENIED` observation; everything else stays an exception.
_DENIAL_CODES = frozenset({"DENIED", REASON_NOT_GRANTED})


class CompatMonitorSource:
    """#493's source seam, backed by `LegacyReadOnlySourceAdapter`."""

    def __init__(self, adapter: LegacyReadOnlySourceAdapter) -> None:
        if not isinstance(adapter, LegacyReadOnlySourceAdapter):
            raise SourceRefused(
                "BAD_ADAPTER",
                "this view wraps a LegacyReadOnlySourceAdapter; a different object "
                "would be a second source of truth about the same reads")
        self._adapter = adapter

    @property
    def readings_used(self) -> int:
        """The budget spent so far, counted by the adapter, not by this view."""
        return self._adapter.readings_used

    # -- the seam -----------------------------------------------------------

    def observe_run(self, *, device_ref: str, session_ref: str,
                    run_id: str | None = None, delivery_id: str | None = None,
                    project: str | None = None,
                    expected_boot_id: str | None = None,
                    expected_source_sha: str | None = None) -> dict[str, Any]:
        try:
            observation = self._adapter.observe_run(
                device_ref=device_ref, session_ref=session_ref,
                run_id=run_id, delivery_id=delivery_id, project=project,
                expected_boot_id=expected_boot_id,
                expected_source_sha=expected_source_sha)
        except SourceRefused as exc:
            if exc.code not in _DENIAL_CODES:
                raise
            return self._denied(VIEW_RUN, device_ref=device_ref,
                                session_ref=session_ref, run_id=run_id,
                                delivery_id=delivery_id,
                                expected_boot_id=expected_boot_id,
                                expected_source_sha=expected_source_sha)
        return self._rendered(observation, VIEW_RUN, _RUN_REASONS)

    def observe_ci(self, *, source_sha: str,
                   project: str | None = None) -> dict[str, Any]:
        try:
            observation = self._adapter.observe_ci(source_sha=source_sha,
                                                   project=project)
        except SourceRefused as exc:
            if exc.code not in _DENIAL_CODES:
                raise
            return self._denied(VIEW_CI, expected_source_sha=source_sha)
        return self._rendered(observation, VIEW_CI, _CI_REASONS)

    # -- rendering ----------------------------------------------------------

    @staticmethod
    def _rendered(observation: Observation, view: str,
                  reasons: Mapping[str, str]) -> dict[str, Any]:
        document = observation.as_dict()
        document["schema"] = COMPAT_SCHEMA
        document["view"] = view
        reason = document.get("reason")
        if isinstance(reason, str):
            document["reason"] = reasons.get(reason, reason)
        return document

    def _denied(self, view: str, *, device_ref: str | None = None,
                session_ref: str | None = None, run_id: str | None = None,
                delivery_id: str | None = None,
                expected_boot_id: str | None = None,
                expected_source_sha: str | None = None) -> dict[str, Any]:
        return {
            "schema": COMPAT_SCHEMA,
            "view": view,
            "status": STATUS_DENIED,
            "reason": REASON_NOT_GRANTED,
            "retryable": False,
            "identity": self._identity_stub(
                device_ref=device_ref, session_ref=session_ref, run_id=run_id,
                delivery_id=delivery_id, expected_boot_id=expected_boot_id,
                expected_source_sha=expected_source_sha),
            "native": None,
            "readings": [],
            "readingsUsed": self._adapter.readings_used,
        }

    def _identity_stub(self, *, device_ref: str | None, session_ref: str | None,
                       run_id: str | None, delivery_id: str | None,
                       expected_boot_id: str | None,
                       expected_source_sha: str | None) -> dict[str, Any]:
        """The addressing facts the CALLER named, plus what the client declares.

        Nothing here comes from the far end: a denial happens before the read,
        which is the point of a denial. The identity fields are therefore the
        requested ones — the same block a successful read would carry, minus
        anything that would have had to be read to fill it.
        """
        client = getattr(self._adapter, "_client", None)
        now = getattr(self._adapter, "_now", None)
        return {
            "instanceId": _text(getattr(client, "fleet_instance_id", None)),
            "routeId": _text(getattr(client, "route_id", None)),
            "deviceRef": device_ref,
            "sessionRef": session_ref,
            "runId": run_id,
            "deliveryId": delivery_id,
            "bootId": (expected_boot_id if expected_boot_id is not None
                       else _text(getattr(client, "boot_id", None))),
            "provider": _text(getattr(client, "provider", None)),
            "sourceSHA": expected_source_sha,
            "observedAt": now() if callable(now) else None,
        }


def _text(value: Any) -> str | None:
    """A declared identifier, or None — never a coerced copy of something else."""
    return value if isinstance(value, str) and value else None
