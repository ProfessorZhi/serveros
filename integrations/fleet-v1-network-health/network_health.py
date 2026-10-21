#!/usr/bin/env python3
"""Fleet V1 N3 — service dependency health and bounded recovery *planning*.

This module is a pure, offline model. It has no network client, no process
launcher and no service manager: it consumes observations that some other,
separately owned component measured, and it returns an assessment plus a
decision. Nothing here can restart, reset, re-authenticate or otherwise change
a host.

Why the axes are separate
-------------------------
The 2026-09-12 incident in ``runtime/health.py`` is the same shape as the one
this module exists to prevent, one plane over. A service can be:

* ``liveness``      — the OS manager says its process/unit is running;
* ``listener``      — a declared local listener accepts a connection;
* ``transport``     — the transport carries a real request/response;
* ``identity``      — the device/OS-principal/account identity is established;
* ``functional``    — the application answers an *authenticated* readiness call.

Each is a different fact. ``listener=OK`` plus ``transport=OK`` is compatible
with a service that is in a login wait loop, or pointed at the wrong remote.
So ``HEALTHY`` requires every axis the profile declares, and it requires the
``functional`` axis. A profile that only claims local layers reports
``LOCAL_ONLY`` and never ``HEALTHY``.

The Windows 16095 case is the concrete example: ``127.0.0.1:16095`` is a
user-stated browser endpoint, not a service name, not an SSH endpoint, not a
remote address. The profile may report that the local listener and the local
forward are up, but until an authenticated application answer and the remote
owner identity are measured, the honest verdict is ``LOCAL_ONLY``.

Nothing is assumed
------------------
A profile must *declare* its service name, listener, tunnel id and remote
device ref. There are no defaults. A missing declaration is
``IDENTITY_UNSPECIFIED``, not a guessed ``tailscaled``/``sshd``/``16095``
inference. A measured PID is accepted as diagnostic data only; it never
participates in identity or in a verdict.

Fail-closed conditions
----------------------
``UNKNOWN``, ``NEEDS_LOGIN``, a manual stop, a wrong OS principal, a foreign
device's service, a missing grant, stale/ambiguous readings, and "remote
healthy but local tunnel down" all produce a refusal or a human-required
decision. There is no fallback rule: an uncovered combination raises
``HealthUndetermined`` rather than returning a reassuring word.

Recovery is a plan, never an action
-----------------------------------
``plan_recovery`` returns symbolic actions and gate results. ``executable`` is
always ``False`` and ``execution_denied_reason`` is ``SOURCE_ONLY_MILESTONE``.
There is no argv, no environment, no credential and no provider login path in
the returned object. A later, separately authorized adapter may consume a
plan; this module cannot execute one.
"""
from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

# ---------------------------------------------------------------------------
# Closed vocabularies
# ---------------------------------------------------------------------------

AXES = ("liveness", "listener", "transport", "identity", "functional")

AXIS_STATES = frozenset(
    {"OK", "FAIL", "UNKNOWN", "NEEDS_LOGIN", "NOT_APPLICABLE"}
)

SERVICE_KINDS = frozenset(
    {
        "tailscale",
        "cloudcli_fleet_node",
        "linux_ssh_tunnel",
        "windows_loopback_selkies",
        "selkies_stream",
        "rdc",
    }
)

PLATFORMS = frozenset({"linux", "windows", "any"})

RECOVERY_DOMAINS = frozenset(
    {"local_device", "remote_device", "external_permission", "human_only"}
)

RECOVERY_VERBS = frozenset(
    {
        "restart_local_service",
        "reestablish_local_tunnel",
        "request_remote_owner_recovery",
        "request_external_action",
    }
)

PROVENANCE_TYPES = frozenset(
    {
        "host-measurement",
        "provider-response",
        "declared-healthcheck",
        "journal-diagnostic",
        "declared-config",
        "user-reported",
        "none",
    }
)

# Strength of the evidence, not confidence in the author. A journal line may
# corroborate a reason; it may never carry a non-UNKNOWN state.
EVIDENCE_STRENGTH = {
    "provider-response": True,
    "host-measurement": True,
    "declared-healthcheck": True,
    "journal-diagnostic": False,
    "declared-config": False,
    "user-reported": False,
    "none": False,
}

# Raw process material and credentials never cross this boundary. A key is
# rejected recursively; a URL is rejected because a query string is where a
# token usually hides.
FORBIDDEN_KEYS = frozenset(
    {
        "argv",
        "args",
        "cmdline",
        "command",
        "commands",
        "env",
        "environ",
        "environment",
        "token",
        "access_token",
        "refresh_token",
        "password",
        "secret",
        "private_key",
        "credential",
        "credentials",
        "cookie",
        "cookies",
        "authorization",
        "url",
        "uri",
        "query",
        "headers",
    }
)

_SECRET_MARKERS = ("://", "?", "token=", "password=", "secret=", "bearer ")

_DEVICE_REF = re.compile(r"^device:[a-z0-9][a-z0-9-]{1,62}$")
_IDENTITY_REF = re.compile(r"^[a-z][a-z0-9._:-]{1,127}$")
_TUNNEL_ID = re.compile(r"^[a-z][a-z0-9._:-]{1,127}$")
_DETAIL_CODE = re.compile(r"^[A-Z0-9_]{1,64}$")
_PRINCIPAL_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@\\-]{0,127}$")

CLOCK_SKEW_S = 60.0


class NetworkHealthError(Exception):
    """Base class for model failures."""


class InvalidProfile(NetworkHealthError):
    """A profile is incomplete, inconsistent or invents identity."""


class InvalidObservation(NetworkHealthError):
    """An observation does not satisfy the evidence contract."""


class ForbiddenObservationField(InvalidObservation):
    """Raw process material, credentials or a URL query crossed the boundary."""


class InvalidRecoveryRequest(NetworkHealthError):
    """A recovery request is malformed or asks for an impossible domain."""


class HealthUndetermined(NetworkHealthError):
    """The combination matches no rule. Deliberately fatal (no fallback)."""


# ---------------------------------------------------------------------------
# Time
# ---------------------------------------------------------------------------


def parse_timestamp(value: str) -> datetime.datetime:
    """Parse an ISO-8601 timestamp, requiring an explicit timezone.

    A timestamp without a timezone is not evidence of freshness; it is a local
    convention that happens to parse. Refuse it at the boundary.
    """
    if not isinstance(value, str) or not value:
        raise InvalidObservation("observed_at must be a non-empty ISO-8601 string")
    text = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError as exc:
        raise InvalidObservation(f"observed_at is not ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None:
        raise InvalidObservation(
            f"observed_at must carry a timezone: {value!r}"
        )
    return parsed.astimezone(datetime.timezone.utc)


# ---------------------------------------------------------------------------
# Boundary sanitisation
# ---------------------------------------------------------------------------


def _assert_no_forbidden(value: Any, path: str) -> None:
    """Reject raw arguments, environment, credentials and URL queries.

    This is intentionally recursive and key-name based. A caller cannot smuggle
    ``/proc/<pid>/cmdline`` into a ``detail`` string through a nested mapping.
    """
    if isinstance(value, Mapping):
        for key, item in value.items():
            name = str(key).lower()
            if name in FORBIDDEN_KEYS:
                raise ForbiddenObservationField(
                    f"{path}.{key}: raw process/credential/URL material is not "
                    f"accepted by this contract"
                )
            _assert_no_forbidden(item, f"{path}.{key}")
        return
    if isinstance(value, (list, tuple, set)):
        for index, item in enumerate(value):
            _assert_no_forbidden(item, f"{path}[{index}]")
        return
    if isinstance(value, str):
        lowered = value.lower()
        for marker in _SECRET_MARKERS:
            if marker in lowered:
                raise ForbiddenObservationField(
                    f"{path}: value contains {marker!r}; URLs/queries/credentials "
                    f"are not observations"
                )


def _require_ref(name: str, value: Any, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.match(value):
        raise InvalidProfile(f"{name} is not a declared, typed reference: {value!r}")
    return value


# ---------------------------------------------------------------------------
# Declared service identity
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DeclaredIdentity:
    """What the owner *declared* about a service, never what was guessed.

    ``service_name`` and ``pid`` are diagnostic. They are not identity and do
    not participate in matching. ``remote_device_ref`` is a typed ServerOS
    device ref; there is deliberately no remote address/host field, because a
    host/IP is a route, not a service identity.
    """

    declared_by: str
    service_name: str | None = None
    identity_ref: str | None = None
    listener_address: str | None = None
    listener_port: int | None = None
    remote_device_ref: str | None = None
    tunnel_id: str | None = None
    pid: int | None = None
    fixture_only: bool = False

    def __post_init__(self) -> None:
        if self.declared_by not in {
            "owner_declared",
            "user_stated",
            "provider_response",
        }:
            raise InvalidProfile(
                f"declared_by={self.declared_by!r} is not an admissible declaration "
                f"source"
            )
        if (self.listener_address is None) != (self.listener_port is None):
            raise InvalidProfile(
                "listener_address and listener_port must be declared together"
            )
        if self.listener_address is not None:
            # Only loopback literals. A name is resolved by whatever resolver
            # is configured; a model that can point anywhere is a scanner.
            if self.listener_address not in {"127.0.0.1", "::1"}:
                raise InvalidProfile(
                    f"listener_address must be a loopback literal, got "
                    f"{self.listener_address!r}"
                )
            if (
                not isinstance(self.listener_port, int)
                or isinstance(self.listener_port, bool)
                or not 1 <= self.listener_port <= 65535
            ):
                raise InvalidProfile(
                    f"listener_port is not a TCP port: {self.listener_port!r}"
                )
        if self.identity_ref is not None:
            _require_ref("identity_ref", self.identity_ref, _IDENTITY_REF)
        if self.remote_device_ref is not None:
            _require_ref("remote_device_ref", self.remote_device_ref, _DEVICE_REF)
        if self.tunnel_id is not None:
            _require_ref("tunnel_id", self.tunnel_id, _TUNNEL_ID)
        if self.pid is not None and (
            not isinstance(self.pid, int) or isinstance(self.pid, bool) or self.pid <= 0
        ):
            raise InvalidProfile(f"pid must be a positive integer or None: {self.pid!r}")
        if self.service_name is not None and not isinstance(self.service_name, str):
            raise InvalidProfile("service_name must be a string or None")
        _assert_no_forbidden(
            {
                "service_name": self.service_name,
                "identity_ref": self.identity_ref,
                "listener_address": self.listener_address,
                "remote_device_ref": self.remote_device_ref,
                "tunnel_id": self.tunnel_id,
            },
            "identity",
        )

    def show(self) -> dict:
        # `pid` is intentionally absent: it is diagnostic input, not identity.
        return {
            "declared_by": self.declared_by,
            "service_name": self.service_name,
            "identity_ref": self.identity_ref,
            "listener_address": self.listener_address,
            "listener_port": self.listener_port,
            "remote_device_ref": self.remote_device_ref,
            "tunnel_id": self.tunnel_id,
            "fixture_only": self.fixture_only,
        }


# ---------------------------------------------------------------------------
# Retry / backoff budget
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RetryBudget:
    """A pure budget. It computes a delay; it never sleeps and never acts."""

    max_attempts: int = 3
    base_delay_s: float = 5.0
    factor: float = 2.0
    max_delay_s: float = 60.0
    window_s: float = 3600.0

    def __post_init__(self) -> None:
        if not isinstance(self.max_attempts, int) or self.max_attempts < 1:
            raise InvalidProfile("max_attempts must be >= 1")
        if self.base_delay_s <= 0:
            raise InvalidProfile("base_delay_s must be > 0")
        if self.factor < 1:
            raise InvalidProfile("factor must be >= 1")
        if self.max_delay_s < self.base_delay_s:
            raise InvalidProfile("max_delay_s must be >= base_delay_s")
        if self.window_s <= 0:
            raise InvalidProfile("window_s must be > 0")

    def delay_for(self, attempt: int) -> float:
        """Delay before a 1-based attempt, exponentially capped."""
        if not isinstance(attempt, int) or attempt < 1:
            raise InvalidRecoveryRequest("attempt is 1-based and must be >= 1")
        return min(
            self.max_delay_s,
            self.base_delay_s * (self.factor ** (attempt - 1)),
        )

    def remaining(self, attempts_used: int) -> int:
        return max(0, self.max_attempts - attempts_used)

    def exhausted(self, attempts_used: int) -> bool:
        return attempts_used >= self.max_attempts

    def show(self) -> dict:
        return {
            "max_attempts": self.max_attempts,
            "base_delay_s": self.base_delay_s,
            "factor": self.factor,
            "max_delay_s": self.max_delay_s,
            "window_s": self.window_s,
        }


# ---------------------------------------------------------------------------
# Profile
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ServiceProfile:
    """One separately owned service dependency profile.

    The profile declares *what must be measured* and *who may recover it*. It
    does not declare a live state. Every identity field is authored; none is
    defaulted.
    """

    profile_id: str
    service_kind: str
    platform: str
    owner_device_ref: str
    owner_principal_ref: str
    required_axes: tuple[str, ...]
    identity: DeclaredIdentity
    required_grants: tuple[str, ...]
    recovery_domain: str
    allowed_verbs: tuple[str, ...]
    retry_budget: RetryBudget
    requires_listener: bool = False
    requires_remote_identity: bool = False
    requires_tunnel_identity: bool = False
    requires_service_identity: bool = True
    claims_authenticated_health: bool = True
    offline_recoverable: bool = False
    link_dependent: bool = True
    local_tunnel: bool = False
    human_only: bool = False
    recovery_grant: str | None = None
    stream_scope: str | None = None
    fixture_only: bool = False

    def __post_init__(self) -> None:
        if not self.profile_id or not isinstance(self.profile_id, str):
            raise InvalidProfile("profile_id is required")
        if self.service_kind not in SERVICE_KINDS:
            raise InvalidProfile(f"unknown service_kind {self.service_kind!r}")
        if self.platform not in PLATFORMS:
            raise InvalidProfile(f"unknown platform {self.platform!r}")
        _require_ref("owner_device_ref", self.owner_device_ref, _DEVICE_REF)
        _require_ref("owner_principal_ref", self.owner_principal_ref, _PRINCIPAL_REF)
        axes = tuple(self.required_axes)
        if not axes:
            raise InvalidProfile("required_axes cannot be empty")
        if len(set(axes)) != len(axes):
            raise InvalidProfile("required_axes contains duplicates")
        unknown = set(axes) - set(AXES)
        if unknown:
            raise InvalidProfile(f"unknown required axes: {sorted(unknown)}")
        if self.recovery_domain not in RECOVERY_DOMAINS:
            raise InvalidProfile(f"unknown recovery_domain {self.recovery_domain!r}")
        if not self.required_grants:
            raise InvalidProfile(
                "at least one read grant must be declared; an empty grant list is "
                "not a licence to observe"
            )
        unknown_verbs = set(self.allowed_verbs) - RECOVERY_VERBS
        if unknown_verbs:
            raise InvalidProfile(f"unknown recovery verbs: {sorted(unknown_verbs)}")
        if self.human_only:
            if self.recovery_domain != "human_only":
                raise InvalidProfile("human_only profiles must use recovery_domain=human_only")
            if self.allowed_verbs:
                raise InvalidProfile("human_only profiles may not declare recovery verbs")
        if self.recovery_domain == "human_only" and not self.human_only:
            raise InvalidProfile("recovery_domain=human_only requires human_only=True")
        if self.recovery_domain == "local_device" and not self.human_only:
            if not self.recovery_grant:
                raise InvalidProfile(
                    "a local recovery profile must declare recovery_grant; a missing "
                    "grant is a refusal, not an implied permission"
                )
        if self.claims_authenticated_health and "functional" not in axes:
            raise InvalidProfile(
                "claims_authenticated_health=True requires the functional axis"
            )
        if self.requires_listener != ("listener" in axes):
            raise InvalidProfile(
                "requires_listener must agree with the presence of the listener axis"
            )
        if (
            self.requires_service_identity
            or self.requires_remote_identity
            or self.requires_tunnel_identity
        ) and "identity" not in axes:
            raise InvalidProfile(
                "identity declarations require the identity axis"
            )
        if self.service_kind == "selkies_stream":
            if self.stream_scope not in {"local", "remote"}:
                raise InvalidProfile(
                    "selkies_stream requires stream_scope=local|remote"
                )
        elif self.stream_scope is not None:
            raise InvalidProfile("stream_scope is only valid for selkies_stream")
        if self.service_kind == "windows_loopback_selkies":
            if self.platform != "windows":
                raise InvalidProfile(
                    "windows_loopback_selkies is a Windows profile"
                )
            if (
                self.identity.listener_address != "127.0.0.1"
                or self.identity.listener_port != 16095
            ):
                raise InvalidProfile(
                    "windows_loopback_selkies is the user-stated "
                    "127.0.0.1:16095 endpoint; any other value is a different "
                    "claim and must be declared as such"
                )
            if self.claims_authenticated_health:
                raise InvalidProfile(
                    "the 16095 endpoint cannot claim authenticated health until a "
                    "remote application identity is measured"
                )
        if self.fixture_only and not self.identity.fixture_only:
            raise InvalidProfile(
                "fixture_only profiles require fixture_only identity"
            )

    def missing_identity_fields(self) -> tuple[str, ...]:
        missing: list[str] = []
        if self.requires_service_identity and not self.identity.identity_ref:
            missing.append("identity_ref")
        if self.requires_listener:
            if not self.identity.listener_address:
                missing.append("listener_address")
            if not self.identity.listener_port:
                missing.append("listener_port")
        if self.requires_remote_identity and not self.identity.remote_device_ref:
            missing.append("remote_device_ref")
        if self.requires_tunnel_identity and not self.identity.tunnel_id:
            missing.append("tunnel_id")
        return tuple(missing)

    def show(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "service_kind": self.service_kind,
            "platform": self.platform,
            "owner_device_ref": self.owner_device_ref,
            "owner_principal_ref": self.owner_principal_ref,
            "required_axes": list(self.required_axes),
            "identity": self.identity.show(),
            "required_grants": list(self.required_grants),
            "recovery_domain": self.recovery_domain,
            "allowed_verbs": list(self.allowed_verbs),
            "retry_budget": self.retry_budget.show(),
            "claims_authenticated_health": self.claims_authenticated_health,
            "offline_recoverable": self.offline_recoverable,
            "link_dependent": self.link_dependent,
            "local_tunnel": self.local_tunnel,
            "human_only": self.human_only,
            "stream_scope": self.stream_scope,
            "fixture_only": self.fixture_only,
        }


# ---------------------------------------------------------------------------
# Observations
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Observation:
    """One axis, one moment, one evidence class, one owning device/principal."""

    axis: str
    state: str
    observed_by: Mapping[str, str]
    observed_at: str | None
    evidence: Mapping[str, Any]
    device_ref: str
    principal_ref: str
    max_age_s: int = 120
    detail_code: str | None = None

    def __post_init__(self) -> None:
        if self.axis not in AXES:
            raise InvalidObservation(f"unknown axis {self.axis!r}")
        if self.state not in AXIS_STATES:
            raise InvalidObservation(
                f"{self.axis}: {self.state!r} is not one of {sorted(AXIS_STATES)}"
            )
        if not isinstance(self.max_age_s, int) or isinstance(self.max_age_s, bool) or self.max_age_s <= 0:
            raise InvalidObservation("max_age_s must be a positive integer")
        if not isinstance(self.observed_by, Mapping):
            raise InvalidObservation("observed_by must be a mapping")
        observer_type = str(self.observed_by.get("type", ""))
        observer_id = str(self.observed_by.get("id", ""))
        if not observer_type or not observer_id:
            raise InvalidObservation(
                f"{self.axis}={self.state}: an observation must name its observer"
            )
        if observer_type not in PROVENANCE_TYPES:
            raise InvalidObservation(f"unknown observer type {observer_type!r}")
        if not isinstance(self.evidence, Mapping):
            raise InvalidObservation("evidence must be a mapping")
        evidence_type = str(self.evidence.get("type", "none"))
        if evidence_type not in EVIDENCE_STRENGTH:
            raise InvalidObservation(f"unknown evidence type {evidence_type!r}")
        if self.state != "UNKNOWN":
            if not EVIDENCE_STRENGTH[evidence_type]:
                raise InvalidObservation(
                    f"{self.axis}={self.state}: evidence {evidence_type!r} cannot "
                    f"carry a non-UNKNOWN state; report UNKNOWN or find a probe"
                )
            if not self.observed_at:
                raise InvalidObservation(
                    f"{self.axis}={self.state}: a non-UNKNOWN state needs observed_at"
                )
        if self.observed_at is not None:
            parse_timestamp(self.observed_at)
        _require_ref("device_ref", self.device_ref, _DEVICE_REF)
        if not isinstance(self.principal_ref, str) or not _PRINCIPAL_REF.match(
            self.principal_ref
        ):
            raise InvalidObservation(
                f"principal_ref is not a declared OS principal: {self.principal_ref!r}"
            )
        if self.detail_code is not None and not _DETAIL_CODE.match(self.detail_code):
            raise InvalidObservation(
                f"detail_code must be a machine code, got {self.detail_code!r}"
            )
        _assert_no_forbidden(
            {
                "observed_by": dict(self.observed_by),
                "evidence": dict(self.evidence),
                "detail_code": self.detail_code,
            },
            f"observation.{self.axis}",
        )

    @property
    def authoritative(self) -> bool:
        return bool(EVIDENCE_STRENGTH.get(str(self.evidence.get("type", "none")), False))

    @property
    def observed_at_dt(self) -> datetime.datetime:
        if self.observed_at is None:
            raise InvalidObservation("this observation has no timestamp")
        return parse_timestamp(self.observed_at)

    def show(self) -> dict:
        return {
            "axis": self.axis,
            "state": self.state,
            "observed_by": dict(self.observed_by),
            "observed_at": self.observed_at,
            "evidence": dict(self.evidence),
            "authoritative": self.authoritative,
            "device_ref": self.device_ref,
            "principal_ref": self.principal_ref,
            "max_age_s": self.max_age_s,
            "detail_code": self.detail_code,
        }


def observation_from_mapping(mapping: Mapping[str, Any]) -> Observation:
    """Build an observation, refusing raw process/credential/URL material."""
    if not isinstance(mapping, Mapping):
        raise InvalidObservation("an observation must be a mapping")
    _assert_no_forbidden(mapping, "observation")
    required = (
        "axis",
        "state",
        "observed_by",
        "device_ref",
        "principal_ref",
    )
    missing = [key for key in required if key not in mapping]
    if missing:
        raise InvalidObservation(f"observation is missing {missing}")
    return Observation(
        axis=mapping["axis"],
        state=mapping["state"],
        observed_by=mapping["observed_by"],
        observed_at=mapping.get("observed_at"),
        evidence=mapping.get("evidence", {"type": "none"}),
        device_ref=mapping["device_ref"],
        principal_ref=mapping["principal_ref"],
        max_age_s=mapping.get("max_age_s", 120),
        detail_code=mapping.get("detail_code"),
    )


# ---------------------------------------------------------------------------
# Profile loading
# ---------------------------------------------------------------------------


def profile_from_mapping(mapping: Mapping[str, Any]) -> ServiceProfile:
    """Build a profile from an authored fixture/contract mapping."""
    if not isinstance(mapping, Mapping):
        raise InvalidProfile("a profile must be a mapping")
    _assert_no_forbidden(mapping, "profile")
    required = (
        "profile_id",
        "service_kind",
        "platform",
        "owner_device_ref",
        "owner_principal_ref",
        "required_axes",
        "identity",
        "required_grants",
        "recovery_domain",
        "retry",
    )
    missing = [key for key in required if key not in mapping]
    if missing:
        raise InvalidProfile(f"profile is missing {missing}")
    identity_payload = mapping["identity"]
    if not isinstance(identity_payload, Mapping):
        raise InvalidProfile("identity must be a mapping")
    retry_payload = mapping["retry"]
    if not isinstance(retry_payload, Mapping):
        raise InvalidProfile("retry must be a mapping")
    identity = DeclaredIdentity(
        declared_by=identity_payload.get("declared_by", "owner_declared"),
        service_name=identity_payload.get("service_name"),
        identity_ref=identity_payload.get("identity_ref"),
        listener_address=identity_payload.get("listener_address"),
        listener_port=identity_payload.get("listener_port"),
        remote_device_ref=identity_payload.get("remote_device_ref"),
        tunnel_id=identity_payload.get("tunnel_id"),
        pid=identity_payload.get("pid"),
        fixture_only=bool(identity_payload.get("fixture_only", False)),
    )
    retry = RetryBudget(
        max_attempts=retry_payload.get("max_attempts", 3),
        base_delay_s=retry_payload.get("base_delay_s", 5.0),
        factor=retry_payload.get("factor", 2.0),
        max_delay_s=retry_payload.get("max_delay_s", 60.0),
        window_s=retry_payload.get("window_s", 3600.0),
    )
    return ServiceProfile(
        profile_id=mapping["profile_id"],
        service_kind=mapping["service_kind"],
        platform=mapping["platform"],
        owner_device_ref=mapping["owner_device_ref"],
        owner_principal_ref=mapping["owner_principal_ref"],
        required_axes=tuple(mapping["required_axes"]),
        identity=identity,
        required_grants=tuple(mapping["required_grants"]),
        recovery_domain=mapping["recovery_domain"],
        allowed_verbs=tuple(mapping.get("allowed_verbs", ())),
        retry_budget=retry,
        requires_listener=bool(mapping.get("requires_listener", False)),
        requires_remote_identity=bool(mapping.get("requires_remote_identity", False)),
        requires_tunnel_identity=bool(mapping.get("requires_tunnel_identity", False)),
        requires_service_identity=bool(mapping.get("requires_service_identity", True)),
        claims_authenticated_health=bool(
            mapping.get("claims_authenticated_health", True)
        ),
        offline_recoverable=bool(mapping.get("offline_recoverable", False)),
        link_dependent=bool(mapping.get("link_dependent", True)),
        local_tunnel=bool(mapping.get("local_tunnel", False)),
        human_only=bool(mapping.get("human_only", False)),
        recovery_grant=mapping.get("recovery_grant"),
        stream_scope=mapping.get("stream_scope"),
        fixture_only=bool(mapping.get("fixture_only", False)),
    )


# ---------------------------------------------------------------------------
# Evaluation context
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EvaluationContext:
    """Caller facts. Every gate defaults to its refusing value."""

    now: datetime.datetime
    requester_device_ref: str
    requester_principal_ref: str
    grant_refs: frozenset[str]
    manual_stopped: bool = False
    lease_available: bool = False
    masked: bool = False
    install_state: str = "unknown"
    link_state: str = "unknown"
    agent_state: str = "unknown"
    attempts_used: int = 0
    external_grant_ref: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.now, datetime.datetime) or self.now.tzinfo is None:
            raise InvalidRecoveryRequest("now must be a timezone-aware datetime")
        _require_ref("requester_device_ref", self.requester_device_ref, _DEVICE_REF)
        if not isinstance(self.requester_principal_ref, str) or not _PRINCIPAL_REF.match(
            self.requester_principal_ref
        ):
            raise InvalidRecoveryRequest(
                f"requester_principal_ref is not a declared OS principal: "
                f"{self.requester_principal_ref!r}"
            )
        if not isinstance(self.grant_refs, (frozenset, set, tuple, list)):
            raise InvalidRecoveryRequest("grant_refs must be a collection")
        object.__setattr__(self, "grant_refs", frozenset(self.grant_refs))
        if self.install_state not in {"installed", "not_installed", "unknown"}:
            raise InvalidRecoveryRequest(
                f"unknown install_state {self.install_state!r}"
            )
        if self.link_state not in {"up", "down", "unknown"}:
            raise InvalidRecoveryRequest(f"unknown link_state {self.link_state!r}")
        if self.agent_state not in {"idle", "busy", "unknown"}:
            raise InvalidRecoveryRequest(f"unknown agent_state {self.agent_state!r}")
        if (
            not isinstance(self.attempts_used, int)
            or isinstance(self.attempts_used, bool)
            or self.attempts_used < 0
        ):
            raise InvalidRecoveryRequest("attempts_used must be >= 0")
        if self.external_grant_ref is not None and not isinstance(
            self.external_grant_ref, str
        ):
            raise InvalidRecoveryRequest("external_grant_ref must be a string or None")


# ---------------------------------------------------------------------------
# Assessments
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AxisAssessment:
    axis: str
    state: str
    authoritative: bool
    observed_at: str | None
    age_s: float | None
    detail_code: str | None

    def show(self) -> dict:
        return {
            "state": self.state,
            "authoritative": self.authoritative,
            "observed_at": self.observed_at,
            "age_s": self.age_s,
            "detail_code": self.detail_code,
        }


@dataclass(frozen=True)
class HealthAssessment:
    profile: ServiceProfile
    verdict: str
    verdict_reason: str
    axes: Mapping[str, AxisAssessment]
    trusted_evidence: tuple[str, ...] = ()
    untrusted_evidence: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    def show(self) -> dict:
        return {
            "profile": self.profile.show(),
            "verdict": self.verdict,
            "verdict_reason": self.verdict_reason,
            "axes": {axis: self.axes[axis].show() for axis in self.axes},
            "trusted_evidence": list(self.trusted_evidence),
            "untrusted_evidence": list(self.untrusted_evidence),
            "notes": list(self.notes),
        }

    to_dict = show


def _assessment(
    profile: ServiceProfile,
    verdict: str,
    reason: str,
    *,
    axes: Mapping[str, AxisAssessment] | None = None,
    trusted: tuple[str, ...] = (),
    untrusted: tuple[str, ...] = (),
    notes: tuple[str, ...] = (),
) -> HealthAssessment:
    return HealthAssessment(
        profile=profile,
        verdict=verdict,
        verdict_reason=reason,
        axes=dict(axes or {}),
        trusted_evidence=trusted,
        untrusted_evidence=untrusted,
        notes=notes,
    )


def _evidence_row(observation: Observation) -> str:
    return (
        f"{observation.axis}:{observation.evidence.get('type', 'none')}:"
        f"{observation.detail_code or 'NO_DETAIL'}"
    )


def assess(
    profile: ServiceProfile,
    observations: Iterable[Observation],
    context: EvaluationContext,
) -> HealthAssessment:
    """Evaluate one service profile against synthetic or real observations.

    Precedence is deliberate and total. A `MANUAL_STOPPED` declaration, a wrong
    principal/device, a missing identity declaration, a missing grant, a
    foreign device's reading, staleness, ambiguity, `NEEDS_LOGIN` and a
    local-tunnel/remote-app split each produce a different verdict because each
    calls for a different repair.
    """
    snapshot = tuple(observations)

    if context.manual_stopped:
        return _assessment(profile, "MANUAL_STOPPED", "MANUAL_STOPPED_DECLARED")
    if context.requester_device_ref != profile.owner_device_ref:
        return _assessment(profile, "WRONG_DEVICE", "REQUESTER_DEVICE_MISMATCH")
    if context.requester_principal_ref != profile.owner_principal_ref:
        return _assessment(profile, "WRONG_OS_USER", "REQUESTER_PRINCIPAL_MISMATCH")

    missing_identity = profile.missing_identity_fields()
    if missing_identity:
        return _assessment(
            profile,
            "IDENTITY_UNSPECIFIED",
            "IDENTITY_FIELDS_UNSPECIFIED:" + ",".join(missing_identity),
        )

    if not set(profile.required_grants).issubset(context.grant_refs):
        return _assessment(profile, "MISSING_GRANT", "REQUIRED_GRANT_MISSING")

    for observation in snapshot:
        if observation.device_ref != profile.owner_device_ref:
            return _assessment(
                profile,
                "FOREIGN_DEVICE_SERVICE",
                f"OBSERVATION_DEVICE_MISMATCH:{observation.axis}",
            )
        if observation.principal_ref != profile.owner_principal_ref:
            return _assessment(
                profile,
                "WRONG_OS_USER",
                f"OBSERVATION_PRINCIPAL_MISMATCH:{observation.axis}",
            )
        # A remote application answer may name the remote device in its
        # evidence. It must be the device this profile declared, not a
        # convenient host that happened to answer.
        remote_claim = observation.evidence.get("remote_device_ref")
        if remote_claim is not None and remote_claim != profile.identity.remote_device_ref:
            return _assessment(
                profile,
                "FOREIGN_DEVICE_SERVICE",
                f"EVIDENCE_REMOTE_DEVICE_MISMATCH:{observation.axis}",
            )

    # Stale evidence is not evidence. A future timestamp is a clock fault and
    # is treated the same way: refusing is the only non-inventing answer.
    for observation in snapshot:
        if observation.observed_at is None:
            continue
        age = (context.now - observation.observed_at_dt).total_seconds()
        if age < -CLOCK_SKEW_S:
            return _assessment(
                profile,
                "STALE",
                f"FUTURE_TIMESTAMP:{observation.axis}",
            )
        if age > observation.max_age_s:
            return _assessment(
                profile,
                "STALE",
                f"OBSERVATION_STALE:{observation.axis}",
            )

    axes: dict[str, AxisAssessment] = {}
    trusted: list[str] = []
    untrusted: list[str] = []

    for axis in AXES:
        axis_observations = [o for o in snapshot if o.axis == axis]
        authoritative = [o for o in axis_observations if o.authoritative]
        if authoritative:
            states = {o.state for o in authoritative}
            if len(states) > 1:
                return _assessment(
                    profile,
                    "AMBIGUOUS",
                    f"CONFLICTING_AUTHORITATIVE_READINGS:{axis}",
                )
            chosen = max(authoritative, key=lambda o: o.observed_at_dt)
            state = chosen.state
            observed_at = chosen.observed_at
            age = (
                (context.now - chosen.observed_at_dt).total_seconds()
                if chosen.observed_at
                else None
            )
            detail_code = chosen.detail_code
            is_authoritative = True
        else:
            chosen = axis_observations[0] if axis_observations else None
            state = "UNKNOWN"
            observed_at = chosen.observed_at if chosen else None
            age = (
                (context.now - chosen.observed_at_dt).total_seconds()
                if chosen and chosen.observed_at
                else None
            )
            detail_code = "NO_AUTHORITATIVE_READING" if chosen else "NOT_OBSERVED"
            is_authoritative = False

        if axis in profile.required_axes and state == "NOT_APPLICABLE":
            state = "UNKNOWN"
            detail_code = "REQUIRED_AXIS_NOT_APPLICABLE"

        axes[axis] = AxisAssessment(
            axis=axis,
            state=state,
            authoritative=is_authoritative,
            observed_at=observed_at,
            age_s=age,
            detail_code=detail_code,
        )
        for observation in axis_observations:
            (trusted if observation.authoritative else untrusted).append(
                _evidence_row(observation)
            )

    # NEEDS_LOGIN is a person, not a restart. It outranks generic FAIL/UNKNOWN
    # because the repair is authentication, and a restart loop cannot supply it.
    if any(axes[axis].state == "NEEDS_LOGIN" for axis in profile.required_axes):
        return _assessment(
            profile,
            "NEEDS_LOGIN",
            "SERVICE_REQUIRES_HUMAN_LOGIN",
            axes=axes,
            trusted=tuple(trusted),
            untrusted=tuple(untrusted),
        )

    # Remote application healthy while a local tunnel/listener is down. The
    # remote owner is not the repair authority for the local half.
    if profile.local_tunnel and axes["functional"].state == "OK":
        local_failed = any(
            axes[axis].state == "FAIL"
            for axis in ("listener", "transport")
            if axis in profile.required_axes
        )
        if local_failed:
            return _assessment(
                profile,
                "REMOTE_OK_LOCAL_DOWN",
                "REMOTE_OK_LOCAL_TUNNEL_DOWN",
                axes=axes,
                trusted=tuple(trusted),
                untrusted=tuple(untrusted),
                notes=(
                    "remote application answered; local listener/transport did not",
                ),
            )

    required_states = {axis: axes[axis].state for axis in profile.required_axes}
    if any(state == "UNKNOWN" for state in required_states.values()):
        return _assessment(
            profile,
            "UNKNOWN",
            "REQUIRED_AXIS_UNKNOWN",
            axes=axes,
            trusted=tuple(trusted),
            untrusted=tuple(untrusted),
        )
    if any(state == "FAIL" for state in required_states.values()):
        return _assessment(
            profile,
            "UNHEALTHY",
            "REQUIRED_AXIS_FAILED",
            axes=axes,
            trusted=tuple(trusted),
            untrusted=tuple(untrusted),
        )
    if any(state != "OK" for state in required_states.values()):
        raise HealthUndetermined(
            f"no rule classifies required axis states {required_states}"
        )

    if profile.claims_authenticated_health:
        return _assessment(
            profile,
            "HEALTHY",
            "ALL_REQUIRED_AXES_OK",
            axes=axes,
            trusted=tuple(trusted),
            untrusted=tuple(untrusted),
        )
    return _assessment(
        profile,
        "LOCAL_ONLY",
        "LOCAL_LAYERS_OK_AUTHENTICATED_APP_HEALTH_NOT_CLAIMED",
        axes=axes,
        trusted=tuple(trusted),
        untrusted=tuple(untrusted),
        notes=(
            "listener/transport/local identity only; authenticated application "
            "health is UNKNOWN",
        ),
    )


# ---------------------------------------------------------------------------
# Recovery planning — decisions, never effects
# ---------------------------------------------------------------------------

PLAN_DECISIONS = frozenset(
    {"NO_ACTION", "PLAN_ONLY", "NEEDS_HUMAN", "REFUSED"}
)

REFUSAL_CODES = frozenset(
    {
        "NO_LEASE",
        "MASKED",
        "INSTALL_STATE_UNKNOWN",
        "NOT_INSTALLED",
        "NEEDS_LOGIN",
        "LINK_DOWN",
        "LINK_STATE_UNKNOWN",
        "AGENT_STATE_UNKNOWN",
        "AGENT_BUSY",
        "MANUAL_STOPPED",
        "WRONG_DEVICE",
        "WRONG_OS_USER",
        "FOREIGN_DEVICE_SERVICE",
        "MISSING_GRANT",
        "IDENTITY_UNSPECIFIED",
        "UNREAD_OBSERVATION",
        "EXTERNAL_PERMISSION_REQUIRED",
        "RETRY_BUDGET_EXHAUSTED",
        "NO_ALLOWED_VERB",
        "HUMAN_ONLY",
        "REMOTE_APP_UNHEALTHY_UNOWNED",
    }
)


@dataclass(frozen=True)
class RecoveryPlan:
    """A symbolic plan. There is no command here and no way to run one."""

    profile_id: str
    decision: str
    action: str | None
    action_domain: str | None
    refusal_code: str | None
    refusal_detail: str | None
    requires_external_grant: bool
    external_grant_ref: str | None
    attempt: int
    delay_s: float
    budget_remaining: int
    executable: bool
    execution_denied_reason: str
    rationale: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.decision not in PLAN_DECISIONS:
            raise InvalidRecoveryRequest(f"unknown decision {self.decision!r}")
        if self.refusal_code is not None and self.refusal_code not in REFUSAL_CODES:
            raise InvalidRecoveryRequest(
                f"unknown refusal code {self.refusal_code!r}"
            )
        if self.action_domain not in {
            None,
            "local_device",
            "remote_device",
            "external",
        }:
            raise InvalidRecoveryRequest(
                f"unknown action_domain {self.action_domain!r}"
            )
        if self.executable:
            raise InvalidRecoveryRequest(
                "this module cannot produce an executable plan; refusing to "
                "pretend otherwise"
            )
        if self.execution_denied_reason != "SOURCE_ONLY_MILESTONE":
            raise InvalidRecoveryRequest(
                "every plan must name SOURCE_ONLY_MILESTONE as the reason it "
                "cannot execute"
            )

    def show(self) -> dict:
        return {
            "profile_id": self.profile_id,
            "decision": self.decision,
            "action": self.action,
            "action_domain": self.action_domain,
            "refusal_code": self.refusal_code,
            "refusal_detail": self.refusal_detail,
            "requires_external_grant": self.requires_external_grant,
            "external_grant_ref": self.external_grant_ref,
            "attempt": self.attempt,
            "delay_s": self.delay_s,
            "budget_remaining": self.budget_remaining,
            "executable": self.executable,
            "execution_denied_reason": self.execution_denied_reason,
            "rationale": list(self.rationale),
        }

    to_dict = show


def _plan(
    profile: ServiceProfile,
    decision: str,
    *,
    action: str | None = None,
    action_domain: str | None = None,
    refusal_code: str | None = None,
    refusal_detail: str | None = None,
    requires_external_grant: bool = False,
    external_grant_ref: str | None = None,
    attempt: int = 0,
    delay_s: float = 0.0,
    budget_remaining: int = 0,
    rationale: tuple[str, ...] = (),
) -> RecoveryPlan:
    return RecoveryPlan(
        profile_id=profile.profile_id,
        decision=decision,
        action=action,
        action_domain=action_domain,
        refusal_code=refusal_code,
        refusal_detail=refusal_detail,
        requires_external_grant=requires_external_grant,
        external_grant_ref=external_grant_ref,
        attempt=attempt,
        delay_s=delay_s,
        budget_remaining=budget_remaining,
        executable=False,
        execution_denied_reason="SOURCE_ONLY_MILESTONE",
        rationale=rationale,
    )


def _refuse(
    profile: ServiceProfile,
    code: str,
    detail: str | None = None,
    rationale: tuple[str, ...] = (),
) -> RecoveryPlan:
    return _plan(
        profile,
        "REFUSED",
        refusal_code=code,
        refusal_detail=detail,
        rationale=rationale,
    )


def plan_recovery(
    assessment: HealthAssessment,
    context: EvaluationContext,
) -> RecoveryPlan:
    """Turn an assessment into a pure decision/plan.

    Gate order is explicit because *which* refusal is returned decides what a
    reader is told to repair. Nothing in this function reads the host, sends a
    request, starts a process or logs in.
    """
    profile = assessment.profile
    verdict = assessment.verdict

    if verdict == "MANUAL_STOPPED" or context.manual_stopped:
        return _refuse(
            profile,
            "MANUAL_STOPPED",
            "a person stopped this service; starting it is not this model's decision",
        )
    if verdict == "WRONG_DEVICE":
        return _refuse(profile, "WRONG_DEVICE", "requester is not the owning device")
    if verdict == "WRONG_OS_USER":
        return _refuse(
            profile, "WRONG_OS_USER", "requester is not the owning OS principal"
        )
    if verdict == "FOREIGN_DEVICE_SERVICE":
        return _refuse(
            profile,
            "FOREIGN_DEVICE_SERVICE",
            "an observation belongs to another device; it is not this service's fact",
        )
    if verdict == "MISSING_GRANT":
        return _refuse(
            profile, "MISSING_GRANT", "the required read/recovery grant is absent"
        )
    if verdict == "IDENTITY_UNSPECIFIED":
        return _refuse(
            profile,
            "IDENTITY_UNSPECIFIED",
            assessment.verdict_reason,
        )
    if verdict == "NEEDS_LOGIN":
        return _plan(
            profile,
            "NEEDS_HUMAN",
            action="REQUEST_LOGIN",
            refusal_code="NEEDS_LOGIN",
            refusal_detail="only a person can renew this authorization",
            rationale=("a restart cannot renew a consumed or expired credential",),
        )
    if verdict == "HEALTHY":
        return _plan(
            profile,
            "NO_ACTION",
            rationale=("assessment=HEALTHY; no recovery is warranted",),
        )
    if verdict == "LOCAL_ONLY":
        functional = assessment.axes.get("functional")
        if functional is not None and functional.state == "FAIL":
            # The local layers are up, but the authenticated application is
            # known to be failing. Without a declared remote owner this node
            # has no authority to repair it and must not restart its local
            # half as a substitute for the real repair.
            return _plan(
                profile,
                "NEEDS_HUMAN",
                refusal_code="REMOTE_APP_UNHEALTHY_UNOWNED",
                refusal_detail=(
                    "authenticated application failed but no remote owner is "
                    "declared; a local listener/forward restart cannot repair it"
                ),
                rationale=("local scope does not own the remote application",),
            )
        return _plan(
            profile,
            "NO_ACTION",
            rationale=(
                "assessment=LOCAL_ONLY; authenticated app health is not claimed "
                "and no local fault is actionable",
            ),
        )
    if profile.human_only:
        return _plan(
            profile,
            "NEEDS_HUMAN",
            refusal_code="HUMAN_ONLY",
            refusal_detail="this surface is human-operated by policy",
            rationale=("tool presence is not desktop-control authority",),
        )
    if verdict in {"UNKNOWN", "STALE", "AMBIGUOUS"}:
        return _refuse(
            profile,
            "UNREAD_OBSERVATION",
            assessment.verdict_reason,
            rationale=("an unread or conflicting observation is not an effect request",),
        )
    if verdict not in {"UNHEALTHY", "REMOTE_OK_LOCAL_DOWN"}:
        raise HealthUndetermined(
            f"no recovery rule for assessment verdict {verdict!r}"
        )

    # Decide the action and its authority domain before the admission gates:
    # the link gate is per-service and depends on whether the action is local.
    functional = assessment.axes.get("functional")
    functional_failed = functional is not None and functional.state == "FAIL"
    remote_owned_failure = (
        verdict == "UNHEALTHY"
        and functional_failed
        and profile.requires_remote_identity
        and profile.identity.remote_device_ref is not None
        and profile.recovery_domain in {"local_device", "remote_device"}
    )

    if verdict == "REMOTE_OK_LOCAL_DOWN":
        action = "REESTABLISH_LOCAL_TUNNEL"
        action_domain = "local_device"
    elif remote_owned_failure:
        # The local tunnel/transport may be fine; the failed half is the
        # remote application. A local restart is not the repair, and this node
        # may only request the remote owner's action under an external grant.
        action = "REQUEST_REMOTE_OWNER_RECOVERY"
        action_domain = "remote_device"
    elif profile.recovery_domain == "local_device":
        if (
            profile.local_tunnel
            and "reestablish_local_tunnel" in profile.allowed_verbs
        ):
            action = "REESTABLISH_LOCAL_TUNNEL"
        elif "restart_local_service" in profile.allowed_verbs:
            action = "RESTART_LOCAL_SERVICE"
        else:
            return _refuse(
                profile,
                "NO_ALLOWED_VERB",
                "the profile declares no local recovery verb",
            )
        action_domain = "local_device"
    elif profile.recovery_domain == "remote_device":
        action = "REQUEST_REMOTE_OWNER_RECOVERY"
        action_domain = "remote_device"
    elif profile.recovery_domain == "external_permission":
        action = "REQUEST_EXTERNAL_ACTION"
        action_domain = "external"
    else:
        return _plan(
            profile,
            "NEEDS_HUMAN",
            refusal_code="HUMAN_ONLY",
            refusal_detail="no automated authority domain",
        )

    # N1 admission gates, in the documented order.
    if not context.lease_available:
        return _refuse(profile, "NO_LEASE", "another writer holds the pen")
    if context.masked:
        return _refuse(profile, "MASKED", "the unit/account is masked by a person")
    if context.install_state == "unknown":
        return _refuse(
            profile, "INSTALL_STATE_UNKNOWN", "install state was not read"
        )
    if context.install_state == "not_installed":
        return _refuse(profile, "NOT_INSTALLED", "there is no installed service")

    if profile.link_dependent:
        if context.link_state == "unknown":
            return _refuse(
                profile, "LINK_STATE_UNKNOWN", "link state was not established"
            )
        if context.link_state == "down":
            # Per-service opt-in only. A crashed LOCAL tunnel may be started
            # while the link is down; a network-dependent connector may not.
            if not (profile.offline_recoverable and action_domain == "local_device"):
                return _refuse(
                    profile,
                    "LINK_DOWN",
                    "the link is down and this service is link-dependent",
                )

    if context.agent_state == "unknown":
        return _refuse(
            profile,
            "AGENT_STATE_UNKNOWN",
            "it is not established whether an agent is using this service",
        )
    if context.agent_state == "busy":
        return _refuse(
            profile,
            "AGENT_BUSY",
            "a busy thing is not a broken thing",
        )

    if action_domain == "local_device":
        if context.requester_device_ref != profile.owner_device_ref:
            return _refuse(profile, "WRONG_DEVICE", "not the owning device")
        if context.requester_principal_ref != profile.owner_principal_ref:
            return _refuse(profile, "WRONG_OS_USER", "not the owning principal")
        if not profile.recovery_grant or profile.recovery_grant not in context.grant_refs:
            return _refuse(
                profile,
                "MISSING_GRANT",
                "the local recovery grant is absent",
            )
        requires_external = False
    else:
        if context.requester_device_ref != profile.owner_device_ref:
            return _refuse(profile, "WRONG_DEVICE", "not the requesting device")
        if context.requester_principal_ref != profile.owner_principal_ref:
            return _refuse(profile, "WRONG_OS_USER", "not the requesting principal")
        if not context.external_grant_ref:
            return _refuse(
                profile,
                "EXTERNAL_PERMISSION_REQUIRED",
                "the remote/external owner grant is absent; this node cannot "
                "recover another host by itself",
            )
        requires_external = True

    if profile.retry_budget.exhausted(context.attempts_used):
        return _refuse(
            profile,
            "RETRY_BUDGET_EXHAUSTED",
            f"attempts_used={context.attempts_used} "
            f"max_attempts={profile.retry_budget.max_attempts}",
        )

    attempt = context.attempts_used + 1
    return _plan(
        profile,
        "PLAN_ONLY",
        action=action,
        action_domain=action_domain,
        requires_external_grant=requires_external,
        external_grant_ref=context.external_grant_ref,
        attempt=attempt,
        delay_s=profile.retry_budget.delay_for(attempt),
        budget_remaining=profile.retry_budget.remaining(attempt),
        rationale=(
            f"assessment={verdict}",
            f"authority_domain={action_domain}",
            "symbolic plan only; no effect is performed by this module",
        ),
    )
