"""Typed sampling of Legacy Fleet facts. Read-only, and authorized before it reads.

This is the monitor's *input side*. It exists so that the core (`core.py`) never
touches a provider directly, and so that the two rules that matter are enforced
in one place:

*   **Authorization precedes the read.** `_authorize` runs before the budget is
    charged and before any client method is called. A caller who was not granted
    a device, a session, a project or a view gets `DENIED` with **zero** provider
    calls — a monitor that read first and checked later has already leaked.
*   **A native `completed` is not success.** `TERMINAL_VERIFIED` requires
    positive native activity: a non-empty assistant message, or a verified
    artifact bound to the *expected* `sourceSHA`. Everything else — no activity,
    a lost run, an offline route, a boot mismatch, a draft pipeline, a pipeline
    for a different commit — stays `UNKNOWN`/`OFFLINE`/`FAILED` with a reason.
    The vocabulary has no word for "done", so it cannot be said by accident.

The shape of an observation is byte-compatible with PR #490's adapter
(`integrations/fleet-next-monitor-source-adapter`), so once that lands its
`Observation.as_dict()` can be fed to this core unchanged; the handoff is
recorded in `README.md`. This module deliberately re-implements the *thin*
adapter rather than importing an unmerged tree, and it imports no HTTP client:
the client is injected, and the write verbs are absent from the protocol.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .contract import (
    BLOCKED_CI_SUBJECT,
    REASON_BAD_PAYLOAD,
    REASON_BOOT_MISMATCH,
    REASON_DRAFT_CI,
    REASON_EXPIRED,
    REASON_IDENTITY_MISMATCH,
    REASON_INSTANCE_MISMATCH,
    REASON_NEVER_ISSUED,
    REASON_NOT_GRANTED,
    REASON_NO_NATIVE_ACTIVITY,
    REASON_READINGS_CAPPED,
    REASON_REMOTE_REFUSED,
    REASON_ROUTE_OFFLINE,
    REASON_RUN_NOT_FOUND,
    REASON_SHA_MISMATCH,
    REASON_UNKNOWN_STATUS,
    REASON_UNVERIFIED_CI,
    STATUS_ACCEPTED,
    STATUS_DENIED,
    STATUS_FAILED,
    STATUS_OFFLINE,
    STATUS_QUEUED,
    STATUS_RUNNING,
    STATUS_STALE,
    STATUS_STARTED,
    STATUS_TERMINAL_VERIFIED,
    STATUS_UNKNOWN,
    VIEW_CI,
    VIEW_RUN,
    VIEWS,
    MonitorRefused,
    watch_key,
)


class LegacyReadOnlyClient(Protocol):  # pragma: no cover - structural typing
    """The three READ methods and the identity the client already owns.

    There is no create, send, delete, stop, restart, interrupt or cancel here,
    and no module-level function to reach one. That is the read-only boundary:
    it is a type, not a discipline.
    """

    fleet_instance_id: str
    route_id: str
    boot_id: str | None
    provider: str | None

    def read_run(self, *, device_ref: str, session_ref: str,
                 run_id: str | None = None,
                 delivery_id: str | None = None) -> Mapping[str, Any]: ...

    def read_ci(self, *, source_sha: str, project: str | None = None) -> Mapping[str, Any]: ...


class RunNotFound(Exception):
    """The device answered and has no such run. Carries the native reason."""

    def __init__(self, reason: str = REASON_NEVER_ISSUED,
                 retryable: bool | None = False) -> None:
        self.reason = reason if reason in (REASON_NEVER_ISSUED, REASON_EXPIRED) else REASON_NEVER_ISSUED
        self.retryable = retryable
        super().__init__(self.reason)


class RouteOffline(Exception):
    """The route to the device is down. Not a failure of the work."""


class RemoteRefused(Exception):
    """The far end refused. Deliberately accepts no message to forward."""

    def __init__(self, code: str = REASON_REMOTE_REFUSED) -> None:
        self.code = code
        super().__init__(code)


class _BudgetExhausted(Exception):
    pass


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _first(payload: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        if name in payload:
            return payload[name]
    return None


#: A native answer may carry the run's assistant turns as `transcript` or
#: `messages` rather than the single `lastAssistantMessage` field. That is a
#: carrier of the same fact, not a second standard of evidence, so it is folded
#: into the one shape the classifier reads.
_CARRIER_KEYS = ("transcript", "messages")
_MESSAGE_KEYS = ("lastAssistantMessage", "last_assistant_message")

#: Only an assistant turn is something the run produced. A user prompt, a system
#: preamble or a tool result is input, and calling it output would let a run that
#: merely received a message look finished.
_ASSISTANT_ROLES = frozenset({"assistant"})

#: A turn may name an identity of its own. If it names one, it must be *this*
#: run's: a foreign turn in a shared transcript is not this run's output.
_TURN_IDENTITY = (
    ("instanceId", "instance_id", "fleet_instance_id"),
    ("deviceRef", "device_ref"),
    ("sessionRef", "session_ref"),
    ("runId", "run_id"),
    ("deliveryId", "delivery_id"),
    ("bootId", "boot_id"),
)

#: The carrier is bounded before it is read: 64 turns, 64 KiB per turn, 256 KiB
#: in total. A larger carrier is a payload the monitor *refuses*, never one it
#: truncates — dropping the tail could drop the very turn that was evidence, and
#: a short window keeps an untrusted answer from making the tick's cost unbounded.
MAX_CARRIER_TURNS = 64
MAX_TURN_CHARS = 64 * 1024
MAX_CARRIER_CHARS = 256 * 1024


class _CarrierRefused(Exception):
    """A carrier outside the bounded shape. The read becomes `BAD_PAYLOAD`."""


def _bounded_carrier(payload: Mapping[str, Any]) -> Any:
    """The carrier, when its shape is bounded; else `_CarrierRefused`."""
    carrier = _first(payload, *_CARRIER_KEYS)
    if carrier is None:
        return None
    if not isinstance(carrier, (list, tuple)):
        raise _CarrierRefused("a carrier is a list of turns")
    if len(carrier) > MAX_CARRIER_TURNS:
        raise _CarrierRefused("too many turns")
    total = 0
    for turn in carrier:
        if isinstance(turn, Mapping):
            content = turn.get("content")
            text = content if isinstance(content, str) else ""
        elif isinstance(turn, str):
            text = turn
        else:
            raise _CarrierRefused("a turn is an object or a bare string")
        if len(text) > MAX_TURN_CHARS:
            raise _CarrierRefused("a turn is over the byte bound")
        total += len(text)
    if total > MAX_CARRIER_CHARS:
        raise _CarrierRefused("the carrier is over the byte bound")
    return carrier


def _is_own_turn(turn: Mapping[str, Any], fact: Mapping[str, Any]) -> bool:
    """False when the turn names an identity that contradicts the fact's own."""
    for names in _TURN_IDENTITY:
        claimed = _first(turn, *names)
        if claimed is None:
            continue
        ours = _first(fact, *names)
        if ours is not None and claimed != ours:
            return False
    return True


def _message_from_carrier(carrier: Any, payload: Mapping[str, Any]) -> dict[str, Any] | None:
    """The run's last assistant turn, or nothing at all.

    Only the *last* assistant turn is a candidate: an earlier one cannot be
    promoted over a later one. If that turn is foreign, empty, or carries no
    text, the answer has no usable output — a later user prompt does not make an
    earlier assistant turn count, and a bare string names no role at all.
    """
    for turn in reversed(carrier):
        if not isinstance(turn, Mapping):
            # A bare string names no role, so it cannot be shown to be output.
            continue
        role = str(turn.get("role") or "").strip().lower()
        if role not in _ASSISTANT_ROLES:
            continue
        if not _is_own_turn(turn, payload):
            return None
        content = turn.get("content")
        if isinstance(content, str) and content.strip():
            # Only the text is forwarded; a turn's own identifiers are not
            # merged into the observation's identity.
            return {"content": content}
        return None
    return None


def _normalise_native(payload: Mapping[str, Any]) -> tuple[dict[str, Any], bool]:
    """The fact with its native evidence in one shape: `(fact, refused)`.

    A declared `lastAssistantMessage` — even a blank one — keeps its text and is
    never upgraded by a carrier the same answer also shipped: the device's own
    word about whether it produced anything outranks a wrapper's convenience
    field. A carrier is consulted only when no message was declared.

    `refused` is True when a carrier was outside the bounded shape. The caller
    must then report `BAD_PAYLOAD`, never classify a truncated carrier.
    """
    try:
        carrier = _bounded_carrier(payload)
    except _CarrierRefused:
        return dict(payload), True
    fact = dict(payload)
    for name in _MESSAGE_KEYS:
        if name in payload:
            declared = payload[name]
            fact["lastAssistantMessage"] = (
                {"content": declared["content"]}
                if isinstance(declared, Mapping) and "content" in declared else {})
            return fact, False
    if carrier is not None:
        message = _message_from_carrier(carrier, payload)
        if message is not None:
            fact["lastAssistantMessage"] = message
        else:
            # No usable turn: the absence is explicit, so a stray snake-spelling
            # key cannot later be read as evidence.
            fact["lastAssistantMessage"] = {}
    return fact, False


def _grant_token(value: Any, name: str) -> frozenset[str]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        raise MonitorRefused("BAD_GRANT", f"`{name}` must be a list of strings")
    box = frozenset(str(item) for item in value)
    if "*" in box:
        raise MonitorRefused(
            "BAD_GRANT",
            f"`{name}` contains `*`. A wildcard is not a grant: it would turn one "
            f"explicit authorization into everything that exists later.")
    for item in box:
        if not item.strip() or any(ord(ch) < 32 for ch in item):
            raise MonitorRefused("BAD_GRANT", f"`{name}` entries must be printable")
    return box


class Grant:
    """What one caller may read. Explicit; there is no default of yes.

    `sessions` is an allowlist: empty means *no session*, never "all sessions".
    The failure mode of the other reading is a monitor reporting somebody else's
    work as the owner's.
    """

    __slots__ = ("principal", "devices", "projects", "sessions", "views")

    def __init__(self, *, principal: str, devices: Any = (), projects: Any = (),
                 sessions: Any = (), views: Any = ()) -> None:
        if not isinstance(principal, str) or not principal.strip():
            raise MonitorRefused("BAD_GRANT", "a grant must name its principal")
        self.principal = principal.strip()
        self.devices = _grant_token(devices, "devices")
        self.projects = _grant_token(projects, "projects")
        self.sessions = _grant_token(sessions, "sessions")
        self.views = _grant_token(views, "views")
        unknown = self.views - VIEWS
        if unknown:
            raise MonitorRefused("BAD_GRANT", f"unknown view(s): {sorted(unknown)}")

    @classmethod
    def from_mapping(cls, document: Any) -> "Grant":
        if not isinstance(document, Mapping):
            raise MonitorRefused("BAD_GRANT", "a caller grant is a JSON object")
        try:
            principal = document["principal"]
        except KeyError:
            raise MonitorRefused("BAD_GRANT", "a grant must name its principal") from None
        return cls(principal=principal,
                   devices=document.get("devices") or (),
                   projects=document.get("projects") or (),
                   sessions=document.get("sessions") or (),
                   views=document.get("views") or ())

    def permits(self, *, view: str, device_ref: str | None = None,
                session_ref: str | None = None, project: str | None = None) -> str | None:
        """The sentence explaining a refusal, or None. Written here, not quoted."""
        if view not in self.views:
            return f"{self.principal!r} may not read the {view} view"
        if device_ref is not None and device_ref not in self.devices:
            return f"{device_ref!r} is not granted to {self.principal!r}"
        if session_ref is not None and session_ref not in self.sessions:
            return (f"session {session_ref!r} is not explicitly granted; an empty or "
                    f"unlisted session is not 'all sessions'")
        if project is not None and project not in self.projects:
            return f"{project!r} is not granted to {self.principal!r}"
        return None


class LegacyFactsSource:
    """Read one run or one pipeline, subject to an explicit grant and a budget."""

    def __init__(self, client: LegacyReadOnlyClient, grant: Grant, *,
                 max_readings: int = 500,
                 now=_utc_now) -> None:
        if not isinstance(grant, Grant):
            raise MonitorRefused("BAD_GRANT", "a Grant is required")
        if isinstance(max_readings, bool) or not isinstance(max_readings, int) or max_readings <= 0:
            raise MonitorRefused("BAD_BUDGET", "max_readings must be a positive integer")
        instance = getattr(client, "fleet_instance_id", None)
        route = getattr(client, "route_id", None)
        if not isinstance(instance, str) or not instance.strip():
            raise MonitorRefused(
                "BLOCKED_UPSTREAM",
                "the injected client declares no fleet_instance_id; an observation "
                "that cannot name which Legacy instance it came from is not emitted")
        if not isinstance(route, str) or not route.strip():
            raise MonitorRefused("BLOCKED_UPSTREAM", "the injected client declares no route_id")
        self._client = client
        self._grant = grant
        self._max = max_readings
        self._used = 0
        self._now = now

    @property
    def readings_used(self) -> int:
        return self._used

    # -- internals ----------------------------------------------------------

    def _charge(self) -> None:
        if self._used >= self._max:
            raise _BudgetExhausted
        self._used += 1

    def _identity(self, *, view: str, device_ref: str | None,
                  session_ref: str | None = None, run_id: str | None = None,
                  delivery_id: str | None = None,
                  source_sha: str | None = None) -> dict[str, Any]:
        return {
            "instanceId": self._client.fleet_instance_id,
            "routeId": self._client.route_id,
            "deviceRef": device_ref,
            "sessionRef": session_ref,
            "runId": run_id,
            "deliveryId": delivery_id,
            "bootId": getattr(self._client, "boot_id", None),
            "provider": getattr(self._client, "provider", None),
            "sourceSHA": source_sha,
            "observedAt": self._now(),
        }

    def _observation(self, *, view: str, status: str, identity: dict[str, Any],
                     reason: str | None = None, retryable: bool | None = None,
                     native: dict[str, Any] | None = None,
                     readings: tuple = ()) -> dict[str, Any]:
        return {
            "view": view,
            "status": status,
            "reason": reason,
            "retryable": retryable,
            "identity": dict(identity),
            "native": native,
            "readings": list(readings),
            "readingsUsed": self._used,
        }

    def _denied(self, *, view: str, why: str, identity: dict[str, Any],
                blocked: str | None = None) -> dict[str, Any]:
        """A refusal before the read. `blocked` names the word the *delivery*
        layer would use, so the tick can report the same refusal instead of a
        bare `DENIED` with no hint about which dimension failed."""
        return self._observation(view=view, status=STATUS_DENIED, identity=identity,
                                 reason=REASON_NOT_GRANTED, retryable=False,
                                 native={"why": why, "blocked": blocked})

    def _mismatch(self, payload: Mapping[str, Any], identity: dict[str, Any]) -> str | None:
        """A reason when the answer describes something other than the question."""
        instance = _first(payload, "instanceId", "instance_id", "fleet_instance_id")
        if instance is not None and instance != identity["instanceId"]:
            return REASON_INSTANCE_MISMATCH
        device = _first(payload, "deviceRef", "device_ref")
        if device is not None and identity["deviceRef"] is not None and device != identity["deviceRef"]:
            return REASON_IDENTITY_MISMATCH
        session = _first(payload, "sessionRef", "session_ref", "sessionId")
        if session is not None and identity["sessionRef"] is not None and session != identity["sessionRef"]:
            return REASON_IDENTITY_MISMATCH
        for key in ("runId", "run_id"):
            value = payload.get(key)
            if value is not None and identity["runId"] is not None and value != identity["runId"]:
                return REASON_IDENTITY_MISMATCH
        for key in ("deliveryId", "delivery_id"):
            value = payload.get(key)
            if value is not None and identity["deliveryId"] is not None and value != identity["deliveryId"]:
                return REASON_IDENTITY_MISMATCH
        return None

    # -- run ----------------------------------------------------------------

    def observe_run(self, *, device_ref: str, session_ref: str,
                    run_id: str | None = None, delivery_id: str | None = None,
                    project: str | None = None,
                    expected_boot_id: str | None = None,
                    expected_source_sha: str | None = None) -> dict[str, Any]:
        identity = self._identity(view=VIEW_RUN, device_ref=device_ref,
                                  session_ref=session_ref, run_id=run_id,
                                  delivery_id=delivery_id,
                                  source_sha=expected_source_sha)
        why = self._grant.permits(view=VIEW_RUN, device_ref=device_ref,
                                  session_ref=session_ref, project=project)
        if why is not None:
            # Denied BEFORE the budget and BEFORE the client: zero provider calls.
            return self._denied(view=VIEW_RUN, why=why, identity=identity)
        if run_id is None and delivery_id is None:
            return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_BAD_PAYLOAD,
                                     retryable=False)
        try:
            self._charge()
        except _BudgetExhausted:
            return self._observation(view=VIEW_RUN, status=STATUS_STALE,
                                     identity=identity, reason=REASON_READINGS_CAPPED,
                                     retryable=True)
        try:
            payload = self._client.read_run(device_ref=device_ref,
                                            session_ref=session_ref,
                                            run_id=run_id, delivery_id=delivery_id)
        except RunNotFound as exc:
            return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                     identity=identity, reason=exc.reason,
                                     retryable=exc.retryable)
        except RouteOffline:
            return self._observation(view=VIEW_RUN, status=STATUS_OFFLINE,
                                     identity=identity, reason=REASON_ROUTE_OFFLINE,
                                     retryable=True)
        except RemoteRefused:
            return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_REMOTE_REFUSED,
                                     retryable=False)
        except (TimeoutError, ConnectionError, OSError):
            # A dropped network is not a failed run and not a success.
            return self._observation(view=VIEW_RUN, status=STATUS_OFFLINE,
                                     identity=identity, reason=REASON_ROUTE_OFFLINE,
                                     retryable=True)
        except MonitorRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - the class name is local, the text is not kept
            return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                     identity=identity,
                                     reason=f"REMOTE_{type(exc).__name__}",
                                     retryable=None)
        if not isinstance(payload, Mapping):
            return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_BAD_PAYLOAD,
                                     retryable=False)
        mismatch = self._mismatch(payload, identity)
        if mismatch is not None:
            return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                     identity=identity, reason=mismatch, retryable=False)
        return self._classify_run(payload, identity, expected_boot_id=expected_boot_id,
                                  expected_source_sha=expected_source_sha)

    def _classify_run(self, payload: Mapping[str, Any], identity: dict[str, Any],
                      *, expected_boot_id: str | None,
                      expected_source_sha: str | None) -> dict[str, Any]:
        status = str(payload.get("status") or "").strip().lower()
        native_boot = _first(payload, "bootId", "boot_id")
        expected_boot = expected_boot_id or identity.get("bootId")
        if native_boot is not None and expected_boot is not None and native_boot != expected_boot:
            # The run belongs to another boot; its identifiers are not merged in.
            return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_BOOT_MISMATCH,
                                     retryable=False)
        # A `delivery_id`-only watch has no `run_id` of its own (#493 contract
        # §155 allows it). The far end may answer with the run's own `runId`;
        # adopting *that* is not inventing one — it is the answer saying which
        # run this delivery is. A watch that already named a run keeps its own
        # id, and `_mismatch` has already refused an answer that disagreed.
        if identity.get("runId") is None:
            native_run = _first(payload, "runId", "run_id")
            if isinstance(native_run, str) and native_run.strip():
                identity = dict(identity, runId=native_run.strip())
        if status in ("accepted",):
            return self._observation(view=VIEW_RUN, status=STATUS_ACCEPTED, identity=identity)
        if status in ("queued", "pending"):
            return self._observation(view=VIEW_RUN, status=STATUS_QUEUED, identity=identity)
        if status in ("started", "dispatching"):
            return self._observation(view=VIEW_RUN, status=STATUS_STARTED, identity=identity)
        if status in ("running", "in_progress"):
            return self._observation(view=VIEW_RUN, status=STATUS_RUNNING, identity=identity)
        if status in ("failed", "cancelled", "canceled", "error", "timed_out"):
            return self._observation(view=VIEW_RUN, status=STATUS_FAILED, identity=identity,
                                     reason=f"RUN_{status.upper()}", retryable=False)
        if status in ("completed", "succeeded", "success", "done"):
            # THE gate. A completed run without native activity is UNKNOWN.
            native, refused = _normalise_native(payload)
            if refused:
                # An unbounded carrier is a payload the monitor will not read,
                # not a shorter one it will. Truncating could drop the turn that
                # would have been the evidence, which is the same as inventing a
                # result from what happened to fit.
                return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                         identity=identity,
                                         reason=REASON_BAD_PAYLOAD,
                                         retryable=False)
            if not self._has_native_activity(native, expected_source_sha):
                return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                         identity=identity,
                                         reason=REASON_NO_NATIVE_ACTIVITY,
                                         retryable=False)
            return self._observation(view=VIEW_RUN, status=STATUS_TERMINAL_VERIFIED,
                                     identity=identity, retryable=False)
        return self._observation(view=VIEW_RUN, status=STATUS_UNKNOWN,
                                 identity=identity, reason=REASON_UNKNOWN_STATUS,
                                 retryable=None)

    @staticmethod
    def _has_native_activity(payload: Mapping[str, Any],
                             expected_source_sha: str | None) -> bool:
        """Positive evidence that the run produced something readable.

        Two admissible forms, both from the answer itself: a non-empty assistant
        message, or an artifact the answer marks verified *and* binds to the
        expected commit. A `note` saying there was no readable message vetoes the
        first — a device that tells us it had nothing is not evidence of output.
        """
        note = payload.get("note")
        if isinstance(note, str) and "no readable assistant message" in note.lower():
            return False
        message = _first(payload, "lastAssistantMessage", "last_assistant_message")
        content = message.get("content") if isinstance(message, Mapping) else None
        if isinstance(content, str) and content.strip():
            return True
        artifacts = payload.get("artifacts")
        if isinstance(artifacts, (list, tuple)):
            for artifact in artifacts:
                if not isinstance(artifact, Mapping):
                    continue
                if artifact.get("verified") is not True:
                    continue
                sha = _first(artifact, "sourceSHA", "source_sha", "sha")
                if isinstance(sha, str) and sha.strip() and expected_source_sha:
                    if sha.strip() == expected_source_sha:
                        return True
        return False

    # -- ci -----------------------------------------------------------------

    def observe_ci(self, *, source_sha: str, project: str | None = None,
                   device_ref: str | None = None) -> dict[str, Any]:
        """One pipeline fact, authorized on **both** dimensions before the read.

        A CI watch may carry its own typed `device_ref` as the delivery subject
        (#497). Declaring one is not the same as being granted one: the subject
        is an authorization claim, and the grant is the only thing that can
        answer it. So `device_ref`, when declared, is checked against
        `grant.devices` here — before the budget is charged and before any
        provider call — and a device outside the grant is `DENIED` with
        `blocked=BLOCKED_CI_SUBJECT`. #495's Wave 16 §4.1 measured the defect
        this closes: a granted project plus an ungranted declared device used to
        produce a `DELIVERED` message about somebody else's device.

        The project dimension is checked exactly as before; a watch needs both
        dimensions to be authorized, never one of them.
        """
        identity = self._identity(view=VIEW_CI, device_ref=None, source_sha=source_sha)
        if not isinstance(source_sha, str) or not source_sha.strip():
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_BAD_PAYLOAD,
                                     retryable=False)
        why = self._grant.permits(view=VIEW_CI, project=project)
        if why is not None:
            return self._denied(view=VIEW_CI, why=why, identity=identity)
        if device_ref is not None:
            why = self._grant.permits(view=VIEW_CI, device_ref=device_ref)
            if why is not None:
                # Zero reads: authorization precedes the budget and the client.
                return self._denied(view=VIEW_CI, why=why, identity=identity,
                                    blocked=BLOCKED_CI_SUBJECT)
            # Only an *authorized* device is carried into the identity, because
            # that identity is what the notifier checks before it sends.
            identity = dict(identity, deviceRef=device_ref)
        try:
            self._charge()
        except _BudgetExhausted:
            return self._observation(view=VIEW_CI, status=STATUS_STALE,
                                     identity=identity, reason=REASON_READINGS_CAPPED,
                                     retryable=True)
        try:
            payload = self._client.read_ci(source_sha=source_sha, project=project)
        except RouteOffline:
            return self._observation(view=VIEW_CI, status=STATUS_OFFLINE,
                                     identity=identity, reason=REASON_ROUTE_OFFLINE,
                                     retryable=True)
        except (TimeoutError, ConnectionError, OSError):
            return self._observation(view=VIEW_CI, status=STATUS_OFFLINE,
                                     identity=identity, reason=REASON_ROUTE_OFFLINE,
                                     retryable=True)
        except RemoteRefused:
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_REMOTE_REFUSED,
                                     retryable=False)
        except MonitorRefused:
            raise
        except Exception as exc:  # noqa: BLE001
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity,
                                     reason=f"REMOTE_{type(exc).__name__}",
                                     retryable=None)
        if not isinstance(payload, Mapping):
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_BAD_PAYLOAD,
                                     retryable=False)
        return self._classify_ci(payload, identity, expected_source_sha=source_sha)

    def _classify_ci(self, payload: Mapping[str, Any], identity: dict[str, Any],
                     *, expected_source_sha: str) -> dict[str, Any]:
        head_sha = _first(payload, "head_sha", "headSHA", "sourceSHA", "sha")
        if isinstance(head_sha, str) and head_sha.strip() and head_sha.strip() != expected_source_sha:
            # Green on another commit is not green on this one.
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_SHA_MISMATCH,
                                     retryable=False)
        if payload.get("draft") is True:
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_DRAFT_CI,
                                     retryable=True)
        phase = str(payload.get("status") or "").strip().lower()
        conclusion = payload.get("conclusion")
        conclusion = str(conclusion).strip().lower() if conclusion is not None else None
        if phase in ("queued", "in_progress", "requested", "waiting", "pending", ""):
            return self._observation(view=VIEW_CI, status=STATUS_RUNNING, identity=identity)
        if phase != "completed":
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_UNKNOWN_STATUS,
                                     retryable=None)
        if conclusion in ("failure", "timed_out", "startup_failure", "cancelled", "stale"):
            return self._observation(view=VIEW_CI, status=STATUS_FAILED, identity=identity,
                                     reason=f"CI_{conclusion.upper()}", retryable=False)
        if conclusion != "success":
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_UNVERIFIED_CI,
                                     retryable=None)
        if payload.get("verified") is False or payload.get("trusted") is False:
            return self._observation(view=VIEW_CI, status=STATUS_UNKNOWN,
                                     identity=identity, reason=REASON_UNVERIFIED_CI,
                                     retryable=False)
        return self._observation(view=VIEW_CI, status=STATUS_TERMINAL_VERIFIED,
                                 identity=identity, retryable=False)


def observe_watch(source: LegacyFactsSource, watch: Mapping[str, Any]) -> dict[str, Any]:
    """Sample one validated watch. The only place a watch becomes a read."""
    if watch["kind"] == VIEW_CI:
        return source.observe_ci(source_sha=watch["expected_source_sha"],
                                 project=watch.get("project"),
                                 device_ref=watch.get("device_ref"))
    return source.observe_run(
        device_ref=watch["device_ref"], session_ref=watch["session_ref"],
        run_id=watch.get("run_id"), delivery_id=watch.get("delivery_id"),
        project=watch.get("project"),
        expected_boot_id=watch.get("expected_boot_id"),
        expected_source_sha=watch.get("expected_source_sha"))


def key_of(watch: Mapping[str, Any]) -> str:
    return watch_key(dict(watch))
