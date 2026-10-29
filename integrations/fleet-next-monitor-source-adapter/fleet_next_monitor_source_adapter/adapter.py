"""The read-only Legacy source adapter, driven entirely by injection.

There is no network client here, and no import of one. The caller injects an
object with three READ methods; this module never looks for anything else. That
is what makes the adapter testable with synthetic JSON and impossible to point
at a live service by accident.

What it will not do
-------------------
No create, send, delete, stop, restart, interrupt or cancel. There is no code
path that calls such a verb, and the protocol it accepts does not declare one.
A read is charged against a bounded budget, so a monitor tick cannot spend an
unbounded number of provider reads.

What "terminal" means
---------------------
A native `completed` is not success. It becomes `TERMINAL_VERIFIED` only when
the same answer carries native output in the Agent Monitor's own vocabulary: a
non-empty `lastAssistantMessage.content`, or a verified artifact bound to the
expected `sourceSHA`. A `transcript` / `messages` list is *normalised* into that
message by `evidence.normalise_run_fact` — it is not accepted as a second,
looser form, and a transcript that cannot be shown to be this run's own
assistant turn yields nothing (PR #508 measured the divergence). Otherwise the
run stays `UNKNOWN` with `NO_NATIVE_ACTIVITY`. `RUN_NOT_FOUND`, an offline route
and a boot mismatch are never `DONE` — the status vocabulary has no such word.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol

from .contract import (
    NATIVE_IDENTITY_KEYS,
    REASON_BAD_PAYLOAD,
    REASON_BOOT_MISMATCH,
    REASON_DRAFT_CI,
    REASON_EXPIRED,
    REASON_IDENTITY_MISMATCH,
    REASON_INSTANCE_MISMATCH,
    REASON_NEVER_ISSUED,
    REASON_NO_NATIVE_ACTIVITY,
    REASON_READINGS_CAPPED,
    REASON_REMOTE_REFUSED,
    REASON_ROUTE_OFFLINE,
    REASON_RUN_NOT_FOUND,
    REASON_UNKNOWN_STATUS,
    REASON_UNVERIFIED_CI,
    STATUS_ACCEPTED,
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
    VIEW_SESSION,
    CallerGrant,
    Identity,
    Observation,
    SourceRefused,
)
from .evidence import (
    adopt_native_run_id,
    native_evidence,
    normalise_run_fact,
)

#: The client must expose these three names and nothing is assumed about the
#: rest. There is deliberately no write verb in the protocol.
class LegacyReadOnlyClient(Protocol):  # pragma: no cover - structural typing
    fleet_instance_id: str
    route_id: str
    boot_id: str | None

    def read_session(self, *, device_ref: str, session_ref: str) -> Mapping[str, Any]:
        ...

    def read_run(self, *, device_ref: str, session_ref: str,
                 run_id: str | None = None,
                 delivery_id: str | None = None) -> Mapping[str, Any]:
        ...

    def read_ci(self, *, source_sha: str) -> Mapping[str, Any]:
        ...


class RunNotFound(Exception):
    """The device answered and has no such run.

    `reason` separates a delivery that was never issued from one that expired;
    `retryable` is the native field that exists to stop a blind retry. Both are
    carried through verbatim — never invented.
    """

    def __init__(self, reason: str = REASON_NEVER_ISSUED,
                 retryable: bool | None = False) -> None:
        self.reason = reason
        self.retryable = retryable
        super().__init__(reason)


class RouteOffline(Exception):
    """The route to the device is down. Not a failure of the work."""


class RemoteRefused(Exception):
    """The far end refused. Its message is discarded on construction.

    Deliberately accepts no message: a remote refusal is where a gateway quotes
    back the credential it was configured with, and there is no reason for this
    adapter to hold that string even briefly.
    """

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


class LegacyReadOnlySourceAdapter:
    """Read one Session, Run or CI fact, subject to an explicit grant."""

    #: Kept for the negative test that inspects the public surface. This adapter
    #: never calls any of them; there is no method on it that could.
    WRITE_VERBS = frozenset({
        "create", "send", "delete", "stop", "restart", "interrupt", "cancel",
        "create_session", "send_to_session", "interrupt_session",
    })

    def __init__(self, client: LegacyReadOnlyClient, grant: CallerGrant, *,
                 max_readings: int = 500, now=None) -> None:
        if not isinstance(grant, CallerGrant):
            raise SourceRefused("BAD_GRANT", "a CallerGrant is required")
        if isinstance(max_readings, bool) or not isinstance(max_readings, int) or max_readings <= 0:
            raise SourceRefused("BAD_BUDGET", "max_readings must be a positive integer")
        self._client = client
        self._grant = grant
        self._max = max_readings
        self._used = 0
        self._now = now or _utc_now

    @property
    def readings_used(self) -> int:
        return self._used

    # -- authorization ------------------------------------------------------

    def _authorize(self, *, view: str, device_ref: str | None = None,
                   session_ref: str | None = None,
                   project: str | None = None) -> None:
        """Refuse before any provider call. Order is deliberate: the cheapest
        check first, and nothing that reaches the client can bypass this."""
        if view not in self._grant.views:
            raise SourceRefused("DENIED", f"{self._grant.principal!r} may not read the {view} view")
        if device_ref is not None and device_ref not in self._grant.devices:
            raise SourceRefused("DENIED", f"{device_ref!r} is not granted to {self._grant.principal!r}")
        if session_ref is not None and session_ref not in self._grant.sessions:
            raise SourceRefused(
                "DENIED",
                f"session {session_ref!r} is not explicitly granted; an empty or "
                f"unlisted session is not 'all sessions'")
        if project is not None and project not in self._grant.projects:
            raise SourceRefused("DENIED", f"project {project!r} is not granted")

    def _charge(self) -> None:
        if self._used >= self._max:
            raise _BudgetExhausted
        self._used += 1

    # -- identity -----------------------------------------------------------

    def _identity(self, *, device_ref: str | None, session_ref: str | None = None,
                  run_id: str | None = None, delivery_id: str | None = None,
                  source_sha: str | None = None,
                  boot_id: str | None = None) -> Identity:
        instance = getattr(self._client, "fleet_instance_id", None)
        route = getattr(self._client, "route_id", None)
        if not isinstance(instance, str) or not instance:
            raise SourceRefused(
                "BLOCKED_UPSTREAM",
                "the injected client declares no fleet_instance_id; an observation "
                "that cannot name which Legacy instance it came from is not emitted")
        if not isinstance(route, str) or not route:
            raise SourceRefused("BLOCKED_UPSTREAM", "the injected client declares no route_id")
        return Identity(
            fleet_instance_id=instance,
            route_id=route,
            device_ref=device_ref,
            session_ref=session_ref,
            run_id=run_id,
            delivery_id=delivery_id,
            # A caller-supplied expectation wins over the client's own boot: the
            # watch names the boot the RUN belongs to, and the client's boot is
            # only a default for a caller that names none.
            boot_id=(boot_id if boot_id is not None
                     else getattr(self._client, "boot_id", None)),
            provider=getattr(self._client, "provider", None),
            source_sha=source_sha,
            observed_at=self._now(),
        )

    @staticmethod
    def _identity_mismatch(payload: Mapping[str, Any], identity: Identity) -> str | None:
        """Return a reason when the answer describes something else.

        This is the cross-instance and cross-session fence: the same `runId`
        under two instances is two different runs, and an answer that names the
        wrong instance, device, session, run, delivery or boot is never merged
        into the requested one.
        """
        instance = _first(payload, "instanceId", "instance_id", "fleet_instance_id")
        if instance is not None and instance != identity.fleet_instance_id:
            return REASON_INSTANCE_MISMATCH
        device = _first(payload, "deviceRef", "device_ref")
        if device is not None and identity.device_ref is not None and device != identity.device_ref:
            return REASON_IDENTITY_MISMATCH
        session = _first(payload, "sessionRef", "session_ref")
        if session is not None and session != identity.session_ref:
            return REASON_IDENTITY_MISMATCH
        for key, expected in (("runId", identity.run_id), ("run_id", identity.run_id),
                              ("deliveryId", identity.delivery_id),
                              ("delivery_id", identity.delivery_id)):
            value = payload.get(key)
            if value is not None and expected is not None and value != expected:
                return REASON_IDENTITY_MISMATCH
        boot = _first(payload, "bootId", "boot_id")
        if boot is not None and identity.boot_id is not None and boot != identity.boot_id:
            return REASON_BOOT_MISMATCH
        return None

    # -- observations -------------------------------------------------------

    def observe_session(self, *, device_ref: str, session_ref: str,
                        project: str | None = None) -> Observation:
        self._authorize(view=VIEW_SESSION, device_ref=device_ref,
                        session_ref=session_ref, project=project)
        identity = self._identity(device_ref=device_ref, session_ref=session_ref)
        try:
            self._charge()
        except _BudgetExhausted:
            return self._capped(VIEW_SESSION, identity)
        try:
            payload = self._client.read_session(device_ref=device_ref,
                                                session_ref=session_ref)
        except RunNotFound as exc:  # a session that is gone
            return self._unknown(VIEW_SESSION, identity, exc.reason, exc.retryable)
        except RouteOffline:
            return self._offline(VIEW_SESSION, identity)
        except RemoteRefused:
            return self._unknown(VIEW_SESSION, identity, REASON_REMOTE_REFUSED, False)
        except SourceRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - the class name is local, the message is not used
            return self._unknown(VIEW_SESSION, identity, f"REMOTE_{type(exc).__name__}", None)
        if not isinstance(payload, Mapping):
            return self._unknown(VIEW_SESSION, identity, REASON_BAD_PAYLOAD, None)
        mismatch = self._identity_mismatch(payload, identity)
        if mismatch is not None:
            # Not this session's answer; its identifiers are not forwarded.
            return self._unknown(VIEW_SESSION, identity, mismatch, None)
        native = self._native_fields(payload)
        running = payload.get("running")
        if running is True or str(payload.get("status") or "").lower() == "running":
            return self._observation(VIEW_SESSION, identity, STATUS_RUNNING, native=native)
        # A session that exists is not a task that succeeded.
        return self._unknown(VIEW_SESSION, identity, REASON_UNKNOWN_STATUS, None,
                             native=native)

    def observe_run(self, *, device_ref: str, session_ref: str,
                    run_id: str | None = None, delivery_id: str | None = None,
                    project: str | None = None,
                    source_sha: str | None = None,
                    expected_boot_id: str | None = None,
                    expected_source_sha: str | None = None) -> Observation:
        """Read one run.

        `expected_boot_id` and `expected_source_sha` are the watch's
        expectations, as #493's `observe_watch` publishes them
        (`integrations/fleet-next-agent-monitor/.../sources.py:494-504`). Both
        are honoured here and neither is dropped:

        *   `expected_boot_id` is the boot the run must belong to; it overrides
            the client's own boot as the comparison target, so a watch that
            outlives a reboot still refuses the previous boot's answer;
        *   `expected_source_sha` is the same expectation `source_sha` already
            carried, under the name the monitor calls it. Naming both with
            different values is a mistake in the caller, not a preference, and
            it is refused before any read rather than resolved by precedence.
        """
        if (source_sha is not None and expected_source_sha is not None
                and source_sha != expected_source_sha):
            raise SourceRefused(
                "BAD_REQUEST",
                f"`source_sha` and `expected_source_sha` name different commits "
                f"({source_sha!r} and {expected_source_sha!r}); this adapter will "
                f"not choose one for the caller")
        expectation_sha = expected_source_sha if expected_source_sha is not None else source_sha
        self._authorize(view=VIEW_RUN, device_ref=device_ref,
                        session_ref=session_ref, project=project)
        identity = self._identity(device_ref=device_ref, session_ref=session_ref,
                                  run_id=run_id, delivery_id=delivery_id,
                                  source_sha=expectation_sha,
                                  boot_id=expected_boot_id)
        if run_id is None and delivery_id is None:
            return self._unknown(VIEW_RUN, identity, "NO_IDENTIFIER", None)
        try:
            self._charge()
        except _BudgetExhausted:
            return self._capped(VIEW_RUN, identity)
        try:
            payload = self._client.read_run(device_ref=device_ref, session_ref=session_ref,
                                            run_id=run_id, delivery_id=delivery_id)
        except RunNotFound as exc:
            return self._unknown(VIEW_RUN, identity, exc.reason, exc.retryable)
        except RouteOffline:
            return self._offline(VIEW_RUN, identity)
        except RemoteRefused:
            return self._unknown(VIEW_RUN, identity, REASON_REMOTE_REFUSED, False)
        except SourceRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - never relay the message
            return self._unknown(VIEW_RUN, identity, f"REMOTE_{type(exc).__name__}", None)
        return self._classify_run(payload, identity)

    def observe_ci(self, *, source_sha: str, project: str) -> Observation:
        self._authorize(view=VIEW_CI, project=project)
        # A pipeline is repo-scoped, not device-scoped: no device ref is invented
        # for it. The route and instance still come from the injected client.
        identity = self._identity(device_ref=None, source_sha=source_sha)
        try:
            self._charge()
        except _BudgetExhausted:
            return self._capped(VIEW_CI, identity)
        try:
            payload = self._client.read_ci(source_sha=source_sha)
        except RouteOffline:
            return self._offline(VIEW_CI, identity)
        except RemoteRefused:
            return self._unknown(VIEW_CI, identity, REASON_REMOTE_REFUSED, False)
        except SourceRefused:
            raise
        except Exception as exc:  # noqa: BLE001 - never relay the message
            return self._unknown(VIEW_CI, identity, f"REMOTE_{type(exc).__name__}", None)
        if not isinstance(payload, Mapping):
            return self._unknown(VIEW_CI, identity, REASON_BAD_PAYLOAD, None)
        # A pipeline answer names its commit under more than one key: #493's
        # offline facts say `head_sha`, a GitHub-shaped answer says `headSHA`,
        # and this adapter's own fixtures said `sourceSHA`. All three name the
        # same fact, so all three are read — and an answer that names NONE of
        # them is still a mismatch, not a benefit of the doubt.
        answered_sha = _first(payload, "sourceSHA", "source_sha",
                              "head_sha", "headSHA", "sha")
        if answered_sha != source_sha:
            # A result for another SHA; its identifiers are not forwarded.
            return self._unknown(VIEW_CI, identity, REASON_IDENTITY_MISMATCH, None)
        native = self._native_fields(payload)
        conclusion = str(payload.get("conclusion") or "").lower()
        if conclusion in {"failure", "failed", "timed_out", "cancelled", "startup_failure"}:
            return self._observation(VIEW_CI, identity, STATUS_FAILED, native=native)
        if payload.get("isDraft") is True or payload.get("draft") is True:
            return self._unknown(VIEW_CI, identity, REASON_DRAFT_CI, None, native=native)
        if conclusion == "success":
            # `trusted` is the negative half of the same claim: an answer that
            # says it was verified and separately says it is not trusted is not
            # a verified pipeline, and the doubt wins.
            if payload.get("verified") is True and payload.get("trusted") is not False:
                return self._observation(VIEW_CI, identity, STATUS_TERMINAL_VERIFIED,
                                         native=native)
            return self._unknown(VIEW_CI, identity, REASON_UNVERIFIED_CI, None,
                                 native=native)
        return self._unknown(VIEW_CI, identity, REASON_UNKNOWN_STATUS, None,
                             native=native)

    # -- classification -----------------------------------------------------

    def _classify_run(self, payload: Any, identity: Identity) -> Observation:
        if not isinstance(payload, Mapping):
            return self._unknown(VIEW_RUN, identity, REASON_BAD_PAYLOAD, None)
        mismatch = self._identity_mismatch(payload, identity)
        if mismatch is not None:
            # The answer names another instance/device/session/run/boot. Its
            # native identifiers are deliberately NOT forwarded: this is exactly
            # the cross-instance case, and copying them would be the leak.
            return self._unknown(VIEW_RUN, identity, mismatch, None)
        # From here on the fact is the monitor's fact: closed vocabulary, and a
        # carrier already folded into `lastAssistantMessage`. The identity
        # check above runs on the raw answer so a snake-case spelling is still
        # fenced; nothing below reads a key the monitor would refuse.
        fact = normalise_run_fact(payload)
        # A `deliveryId`-only watch has no run id of its own; the answer saying
        # which run this delivery is, is adoption, not invention (#493
        # `sources._classify_run`). Done after the mismatch check, so an answer
        # that disagreed has already been refused.
        identity = adopt_native_run_id(identity, fact)
        native = self._native_fields(payload)
        status = str(payload.get("status") or "").lower()
        if payload.get("failed") is True or status in {"failed", "failure", "error"}:
            return self._observation(VIEW_RUN, identity, STATUS_FAILED, native=native)
        if status in {"completed", "settled"}:
            if self._has_native_evidence(fact, identity):
                return self._terminal(VIEW_RUN, identity, native=native)
            return self._unknown(VIEW_RUN, identity, REASON_NO_NATIVE_ACTIVITY, None,
                                 native=native)
        if status == "queued":
            return self._observation(VIEW_RUN, identity, STATUS_QUEUED, native=native)
        if status == "accepted":
            return self._observation(VIEW_RUN, identity, STATUS_ACCEPTED, native=native)
        if status == "running":
            return self._observation(VIEW_RUN, identity, STATUS_RUNNING, native=native)
        if status == "started":
            return self._observation(VIEW_RUN, identity, STATUS_STARTED, native=native)
        return self._unknown(VIEW_RUN, identity, REASON_UNKNOWN_STATUS, None,
                             native=native)

    @classmethod
    def _has_native_evidence(cls, fact: Mapping[str, Any],
                             identity: Identity) -> bool:
        """A completed run is verified only by something it produced.

        `fact` is a **normalised** fact (`evidence.normalise_run_fact`): the
        monitor's closed vocabulary, with a `transcript` / `messages` carrier
        already folded into `lastAssistantMessage`. The predicate is #493's
        `_has_native_activity`, branch for branch — a non-empty assistant
        message, or an artifact the answer marks verified *and* binds to the
        expected commit. A device note saying it had no readable message vetoes
        both message forms before anything else is read.

        The single-spelling aliases this adapter used to accept
        (`verifiedArtifact`, `verified_artifact`, `verified_native_artifact`)
        are gone: they are not in the monitor's vocabulary, so accepting them
        would be a second standard of evidence on one path and three on the
        other. An answer that only has them is `UNKNOWN`, which is the
        fail-closed direction. Likewise an artifact with no expected sha to be
        bound to is not evidence — the previous version returned True for it
        (#508 D2).
        """
        return native_evidence(fact, identity.source_sha)

    # -- constructors -------------------------------------------------------

    @staticmethod
    def _native_fields(payload: Any) -> dict[str, Any] | None:
        """The native identity fields the answer carried, verbatim.

        Scalars only. A container here would be arbitrary remote content (a
        model list, a transcript, an error object), and this adapter forwards
        none of that.
        """
        if not isinstance(payload, Mapping):
            return None
        aliases = (
            ("instanceId", "instanceId"), ("instance_id", "instanceId"),
            ("deviceRef", "deviceRef"), ("device_ref", "deviceRef"),
            ("sessionRef", "sessionRef"), ("session_ref", "sessionRef"),
            ("runId", "runId"), ("run_id", "runId"),
            ("deliveryId", "deliveryId"), ("delivery_id", "deliveryId"),
            ("bootId", "bootId"), ("boot_id", "bootId"),
            ("provider", "provider"),
            ("sourceSHA", "sourceSHA"), ("source_sha", "sourceSHA"),
            ("observedAt", "observedAt"), ("observed_at", "observedAt"),
        )
        out: dict[str, Any] = {}
        for key, canonical in aliases:
            if canonical in out:
                continue
            value = payload.get(key)
            if isinstance(value, (str, int, float, bool)) or value is None:
                if value is not None:
                    out[canonical] = value
        return {k: out[k] for k in NATIVE_IDENTITY_KEYS if k in out}

    def _observation(self, view: str, identity: Identity, status: str, *,
                     reason: str | None = None,
                     retryable: bool | None = None,
                     native: dict[str, Any] | None = None) -> Observation:
        return Observation(view=view, status=status, identity=identity,
                           reason=reason, retryable=retryable,
                           readings_used=self._used, native=native)

    def _unknown(self, view: str, identity: Identity, reason: str,
                 retryable: bool | None,
                 native: dict[str, Any] | None = None) -> Observation:
        return self._observation(view, identity, STATUS_UNKNOWN,
                                 reason=reason, retryable=retryable, native=native)

    def _offline(self, view: str, identity: Identity) -> Observation:
        return self._observation(view, identity, STATUS_OFFLINE,
                                 reason=REASON_ROUTE_OFFLINE, retryable=True)

    def _capped(self, view: str, identity: Identity) -> Observation:
        # The budget is spent; this is a monitor-side fact, not a device one.
        return self._observation(view, identity, STATUS_STALE,
                                 reason=REASON_READINGS_CAPPED, retryable=True)

    def _terminal(self, view: str, identity: Identity, *,
                  native: dict[str, Any] | None = None) -> Observation:
        """The only place a successful terminal state is produced."""
        return self._observation(view, identity, STATUS_TERMINAL_VERIFIED, native=native)


class FixtureLegacyClient:
    """Synthetic Legacy answers, for the CLI and for tests.

    This is not a fallback data source: it is never used unless a caller injects
    it, and every value it returns comes from a JSON document the caller wrote.
    An `errors` map can make a read raise one of the typed failures instead.
    """

    def __init__(self, document: Mapping[str, Any], *, fleet_instance_id: str = "fixture-instance",
                 route_id: str = "fixture-route", boot_id: str | None = "fixture-boot",
                 provider: str | None = None) -> None:
        if not isinstance(document, Mapping):
            raise SourceRefused("BAD_FIXTURE", "a fixture document is a JSON object")
        self._document = copy.deepcopy(dict(document))
        self.fleet_instance_id = fleet_instance_id
        self.route_id = route_id
        self.boot_id = boot_id
        self.provider = provider
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def _error(self, view: str) -> None:
        errors = self._document.get("errors") or {}
        kind = errors.get(view) if isinstance(errors, Mapping) else None
        if kind == "RUN_NOT_FOUND":
            details = self._document.get("notFound") or {}
            raise RunNotFound(reason=str(details.get("reason") or REASON_NEVER_ISSUED),
                              retryable=details.get("retryable", False))
        if kind == "OFFLINE":
            raise RouteOffline("fixture route offline")
        if kind == "REMOTE_REFUSED":
            raise RemoteRefused("fixture-remote-refused")

    def read_session(self, *, device_ref, session_ref):
        self.calls.append(("read_session", {"device_ref": device_ref, "session_ref": session_ref}))
        self._error(VIEW_SESSION)
        return copy.deepcopy(self._document.get("session") or {})

    def read_run(self, *, device_ref, session_ref, run_id=None, delivery_id=None):
        self.calls.append(("read_run", {"device_ref": device_ref, "session_ref": session_ref,
                                        "run_id": run_id, "delivery_id": delivery_id}))
        self._error(VIEW_RUN)
        return copy.deepcopy(self._document.get("run") or {})

    def read_ci(self, *, source_sha):
        self.calls.append(("read_ci", {"source_sha": source_sha}))
        self._error(VIEW_CI)
        return copy.deepcopy(self._document.get("ci") or {})
