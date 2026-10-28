"""The typed contract between Legacy Fleet facts and the Fleet Next monitor.

Two rules shape every type here:

1.  **Legacy is authoritative.** This adapter reads facts Legacy already owns; it
    never becomes a second writer, a second store or a second event bus.
2.  **Identity is never folded.** `instanceId`, `deviceRef`, `sessionRef`,
    `runId`, `deliveryId`, `bootId`, `provider` and `sourceSHA` travel as
    separate fields. "The same string appeared in two places" is not evidence
    that they name the same thing, and a monitor that joined them would report
    one device's work as another's.

`STATUS_*` deliberately has no `DONE`: a native `completed` with no readable
activity, a `RUN_NOT_FOUND`, an offline route and a boot mismatch are all not
success, and a vocabulary without that word cannot say it by accident.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

SCHEMA = "serveros/fleet-next-monitor-source-adapter.v1"
OBSERVATION_SCHEMA = "serveros/fleet-next-monitor-observation.v1"

# -- views ------------------------------------------------------------------
VIEW_SESSION = "session"
VIEW_RUN = "run"
VIEW_CI = "ci"
VIEWS = frozenset({VIEW_SESSION, VIEW_RUN, VIEW_CI})

# -- statuses (no DONE, by design) -----------------------------------------
STATUS_ACCEPTED = "ACCEPTED"
STATUS_QUEUED = "QUEUED"
STATUS_STARTED = "STARTED"
STATUS_RUNNING = "RUNNING"
STATUS_TERMINAL_VERIFIED = "TERMINAL_VERIFIED"
STATUS_FAILED = "FAILED"
STATUS_STALE = "STALE"
STATUS_UNKNOWN = "UNKNOWN"
STATUS_OFFLINE = "OFFLINE"
STATUS_DENIED = "DENIED"
STATUSES = frozenset({
    STATUS_ACCEPTED, STATUS_QUEUED, STATUS_STARTED, STATUS_RUNNING,
    STATUS_TERMINAL_VERIFIED, STATUS_FAILED, STATUS_STALE, STATUS_UNKNOWN,
    STATUS_OFFLINE, STATUS_DENIED,
})

# -- reasons ----------------------------------------------------------------
REASON_NEVER_ISSUED = "NEVER_ISSUED"
REASON_EXPIRED = "EXPIRED"
REASON_RUN_NOT_FOUND = "RUN_NOT_FOUND"
REASON_NO_NATIVE_ACTIVITY = "NO_NATIVE_ACTIVITY"
REASON_BOOT_MISMATCH = "BOOT_MISMATCH"
REASON_INSTANCE_MISMATCH = "INSTANCE_MISMATCH"
REASON_IDENTITY_MISMATCH = "IDENTITY_MISMATCH"
REASON_ROUTE_OFFLINE = "ROUTE_OFFLINE"
REASON_READINGS_CAPPED = "READINGS_CAPPED"
REASON_REMOTE_REFUSED = "REMOTE_REFUSED"
REASON_UNKNOWN_STATUS = "UNKNOWN_STATUS"
REASON_UNVERIFIED_CI = "UNVERIFIED_CI"
REASON_DRAFT_CI = "DRAFT_CI"

#: The only values a native `retryable` may take when it is relayed onward.
#: Mirrored from the refusal convention: a missing `retryable: false` is what
#: makes a caller retry a run that will never appear.
BOOLS = (True, False)


class SourceRefused(Exception):
    """This adapter refused before calling a provider.

    Carries a code a caller can branch on and a sentence written HERE. It never
    carries remote text: a refusal that quoted the far end would be the leak
    this module exists to prevent.
    """

    def __init__(self, code: str, why: str) -> None:
        self.code = code
        self.why = why
        super().__init__(f"{code}: {why}")


#: A grant that names `*` is not a grant. Refused at construction so a wildcard
#: cannot be smuggled in as a list element and silently widen every device.
_WILDCARD = "*"


@dataclass(frozen=True)
class CallerGrant:
    """What one caller may read. Written explicitly; there is no default of yes.

    `sessions` is an explicit allowlist. An empty set means "no session", never
    "every session" — the failure mode of an empty-means-all reading is a
    monitor that reports somebody else's work as the owner's.
    """

    principal: str
    devices: frozenset[str]
    projects: frozenset[str]
    sessions: frozenset[str]
    views: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.principal, str) or not self.principal.strip():
            raise SourceRefused("BAD_GRANT", "a caller grant must name its principal")
        for label, values in (("devices", self.devices), ("projects", self.projects),
                              ("sessions", self.sessions), ("views", self.views)):
            if not isinstance(values, frozenset):
                raise SourceRefused("BAD_GRANT", f"`{label}` must be a frozenset")
            if _WILDCARD in values:
                raise SourceRefused(
                    "BAD_GRANT",
                    f"`{label}` contains `*`. A wildcard is not a grant: it would "
                    f"turn one explicit authorization into every device, session "
                    f"or view that exists later.")
            if any(not isinstance(v, str) or not v.strip() for v in values):
                raise SourceRefused("BAD_GRANT", f"`{label}` entries must be non-empty strings")
        unknown = self.views - VIEWS
        if unknown:
            raise SourceRefused("BAD_GRANT", f"unknown view(s): {sorted(unknown)}")

    @classmethod
    def from_mapping(cls, document: Mapping[str, Any]) -> "CallerGrant":
        if not isinstance(document, Mapping):
            raise SourceRefused("BAD_GRANT", "a caller grant is a JSON object")
        try:
            return cls(
                principal=str(document["principal"]),
                devices=frozenset(document.get("devices") or ()),
                projects=frozenset(document.get("projects") or ()),
                sessions=frozenset(document.get("sessions") or ()),
                views=frozenset(document.get("views") or ()),
            )
        except KeyError:
            raise SourceRefused("BAD_GRANT", "a caller grant must name its principal") from None


_INSTANCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}\Z")
_REF_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:|-]{0,255}\Z")


def _token(value: Any, *, name: str, pattern: re.Pattern[str]) -> str | None:
    """A declared identifier, or None. Never coerced, never truncated."""
    if value is None:
        return None
    if not isinstance(value, str) or not pattern.match(value):
        raise SourceRefused(
            "BAD_IDENTITY",
            f"`{name}` is not a bounded identifier; a value that cannot be named "
            f"is refused rather than carried into an observation")
    return value


@dataclass(frozen=True)
class Identity:
    """One observation's addressing facts, each in its own field."""

    fleet_instance_id: str
    route_id: str
    #: None for a CI observation: a pipeline is not device-scoped, and inventing
    #: a device ref for it would make a repo fact look like a machine fact.
    device_ref: str | None
    session_ref: str | None
    run_id: str | None
    delivery_id: str | None
    boot_id: str | None
    provider: str | None
    source_sha: str | None
    observed_at: str

    def __post_init__(self) -> None:
        _token(self.fleet_instance_id, name="instanceId", pattern=_INSTANCE_RE)
        _token(self.route_id, name="routeId", pattern=_INSTANCE_RE)
        _token(self.device_ref, name="deviceRef", pattern=_REF_RE)
        for name, value in (("sessionRef", self.session_ref), ("runId", self.run_id),
                            ("deliveryId", self.delivery_id), ("bootId", self.boot_id),
                            ("provider", self.provider), ("sourceSHA", self.source_sha)):
            _token(value, name=name, pattern=_REF_RE)
        if not isinstance(self.observed_at, str) or not self.observed_at:
            raise SourceRefused("BAD_IDENTITY", "an observation carries its time")

    def as_dict(self) -> dict[str, Any]:
        """Camel-case at the wire edge; the fields stay separate."""
        return {
            "instanceId": self.fleet_instance_id,
            "routeId": self.route_id,
            "deviceRef": self.device_ref,
            "sessionRef": self.session_ref,
            "runId": self.run_id,
            "deliveryId": self.delivery_id,
            "bootId": self.boot_id,
            "provider": self.provider,
            "sourceSHA": self.source_sha,
            "observedAt": self.observed_at,
        }


#: The native identity fields an observation may repeat verbatim. Kept separate
#: from `identity` (what the caller ASKED for): a native answer can name a
#: delivery id the request did not, and keeping the two blocks apart is what
#: lets a monitor compare them instead of assuming they agree.
NATIVE_IDENTITY_KEYS = (
    "instanceId", "deviceRef", "sessionRef", "runId", "deliveryId",
    "bootId", "provider", "sourceSHA", "observedAt",
)


@dataclass(frozen=True)
class Observation:
    """One bounded read, and what it establishes. Never more than that."""

    view: str
    status: str
    identity: Identity
    reason: str | None = None
    retryable: bool | None = None
    readings: tuple[dict[str, Any], ...] = ()
    readings_used: int = 0
    #: The native fields the answer carried, unchanged and unjoined. None when
    #: the read failed before an answer existed.
    native: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.view not in VIEWS:
            raise SourceRefused("BAD_OBSERVATION", f"unknown view {self.view!r}")
        if self.status not in STATUSES:
            raise SourceRefused("BAD_OBSERVATION", f"unknown status {self.status!r}")
        if self.native is not None:
            if not isinstance(self.native, dict):
                raise SourceRefused("BAD_OBSERVATION", "`native` must be an object or None")
            unexpected = set(self.native) - set(NATIVE_IDENTITY_KEYS)
            if unexpected:
                raise SourceRefused(
                    "BAD_OBSERVATION",
                    f"`native` carries non-identity fields: {sorted(unexpected)}")
            for name, value in self.native.items():
                if not isinstance(value, (str, int, float, bool)) and value is not None:
                    raise SourceRefused(
                        "BAD_OBSERVATION",
                        f"native field {name!r} is not a scalar; a container here "
                        f"would be remote text this adapter does not forward")

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": OBSERVATION_SCHEMA,
            "view": self.view,
            "status": self.status,
            "reason": self.reason,
            "retryable": self.retryable,
            "identity": self.identity.as_dict(),
            "native": self.native,
            "readings": list(self.readings),
            "readingsUsed": self.readings_used,
        }
