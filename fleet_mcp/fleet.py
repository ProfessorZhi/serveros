"""The Fleet read model: devices, sessions, runs — composed, never re-derived.

What this module is
-------------------
A pure composition layer. It holds a device registry (authored elsewhere), a
set of providers (adapters to devices), and the policy that decides which of
the calls it receives are allowed. It computes no device identity, owns no
session truth, and stores no transcript.

What it is careful about
------------------------
*   **One device's failure is one device's failure.** Every fan-out is
    per-device and captures its own error; a dead Tokyo provider must not
    change the answer about Windows. This is the property that makes
    `fleet_list_sessions` usable while a laptop is asleep.

*   **A device with no route is reported, not omitted.** A device silently
    dropped from a list is indistinguishable from a device that has no
    sessions — and the second reading is the one an operator acts on.

*   **`send` is idempotent by key, not by hope.** A retry after an unknown
    outcome returns the recorded run instead of sending a second message. The
    provider gets no such guarantee (CloudCLI has no idempotency key), so the
    guard lives here, above the provider, where the surface can honour it.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .audit import AuditLog, RateLimiter
from .config import FleetConfig
from .errors import (
    APPROVAL_REQUIRED,
    BAD_REQUEST,
    IDEMPOTENT_REPLAY,
    INTERNAL,
    RATE_LIMITED,
    SCOPE_DENIED,
    UNKNOWN_DEVICE,
    WRITE_NOT_GRANTED,
    refuse,
)
from .identity import SessionKey
from .privacy import clamp_limit, redact_payload, UNTRUSTED_NOTICE
from .providers import DeviceProvider, ProviderHealth, build_providers
from .registry import (
    authored_facts,
    load_registry,
    measured_availability,
    resolve_or_refuse,
)
from .scopes import SCOPE_CREATE, SCOPE_READ, SCOPE_SEND

# How long a recorded idempotency key is honoured. Long enough to cover a
# caller's retry after a timeout, short enough that the table is bounded by the
# session it belongs to rather than by uptime.
IDEMPOTENCY_TTL_SECONDS = 3600

# The lifecycle modes a caller may ask a new session to start in. Bounded here
# rather than passed through, and the bound is the point: `resident` is the one
# value that leaves a process alive after the first turn ends, so "whatever
# string CloudCLI happens to accept today" is not a safe thing for this surface
# to forward on a caller's behalf. A value outside this set is refused, not
# relayed — the alternative is a gate that only holds for the values someone
# remembered to enumerate.
LIFECYCLE_MODES = ("per-run", "resident")


@dataclass(frozen=True)
class Principal:
    """Who is calling, and what they may do.

    Never derived from a client-supplied parameter. It is produced by the
    credential the request presented — a personal access token or an OAuth
    access token — and carried into every tool call by the server.
    """

    name: str
    scopes: frozenset[str]
    credential_kind: str  # "pat" | "oauth"

    def require(self, scope: str) -> None:
        if scope not in self.scopes:
            raise refuse(
                SCOPE_DENIED,
                f"credential {self.name!r} ({self.credential_kind}) does not "
                f"carry {scope!r}; it carries {sorted(self.scopes)}",
            )

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "credential_kind": self.credential_kind,
                "scopes": sorted(self.scopes)}


@dataclass
class _IdempotencyEntry:
    run: dict[str, Any]
    at: float = field(default_factory=time.time)


class FleetService:
    def __init__(self, config: FleetConfig, *, registry=None, providers=None) -> None:
        self.config = config
        self.registry = registry if registry is not None else load_registry(config.registry_path)
        self.providers: dict[str, DeviceProvider] = (
            providers if providers is not None else build_providers(config)
        )
        self.audit = AuditLog(config.audit)
        self.limiter = RateLimiter(config.audit.rate_limit_per_minute) if config.audit else None
        self._idempotency: dict[str, _IdempotencyEntry] = {}
        self._idem_lock = threading.Lock()

    # -- helpers -----------------------------------------------------------

    def _provider(self, device_ref: str) -> DeviceProvider | None:
        return self.providers.get(device_ref)

    def _require_provider(self, device_ref: str) -> DeviceProvider:
        provider = self.providers.get(device_ref)
        if provider is None:
            raise refuse(
                UNKNOWN_DEVICE,
                f"{device_ref} is enrolled but this runtime holds no route to "
                f"it. `fleet_list_devices` reports the same fact as "
                f"`local_agent_controller: UNKNOWN`.",
            )
        return provider

    def admit(self, principal: Principal) -> None:
        if self.limiter is not None:
            allowed, retry_after = self.limiter.check(principal.name)
            if not allowed:
                raise refuse(
                    RATE_LIMITED,
                    f"{principal.name!r} exceeded "
                    f"{self.config.audit.rate_limit_per_minute} requests/minute; "
                    f"retry in {retry_after:.0f}s",
                    retry_after_seconds=round(retry_after, 1),
                )

    def _may_send(self, key: SessionKey) -> None:
        binding = self.config.provider_for(key.device_ref)
        if binding is None or not binding.allow_send:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.device_ref} is not authorized for control from this "
                f"runtime. Control is granted per device in the local binding "
                f"(`allow_send: true`), and per session in `policy.send_allow` "
                f"— never by the caller naming a session id.",
            )
        allow = self.config.policy.send_allow
        if self.config.policy.allow_wildcard_send and "*" in allow:
            return
        if key.ref not in allow:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.ref} is not in `policy.send_allow`. The device is "
                f"authorized for control and this session is not.",
                session_ref=key.ref,
                allowed=sorted(allow),
            )

    def _may_create(self, device_ref: str, project: str) -> None:
        """May this runtime create a session in `project` on `device_ref`.

        Two keys again, and they are deliberately not the send keys: a device
        the operator authorized for control is not thereby authorized for
        creation, and the second half is a *project* rather than a session —
        because the thing being authorized is a place a new session will run
        in, not an object that already exists.

        The device half is checked first so the two refusals stay about
        different things: "this machine is not authorized" sends an operator to
        the device block, "this project is not authorized" sends them to the
        policy block. Collapsing them would tell a caller to edit the wrong
        stanza.
        """
        binding = self.config.provider_for(device_ref)
        if binding is None or not binding.allow_create:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{device_ref} is not authorized for session creation from this "
                f"runtime. Creation is granted per device in the local binding "
                f"(`allow_create: true`), separately from `allow_send` — and "
                f"per project in `policy.create_allow`. Neither is conferred by "
                f"naming a device or a project in a call.",
                device_ref=device_ref,
            )
        allow = self.config.policy.create_allow
        if self.config.policy.allow_wildcard_create and "*" in allow:
            return
        if project not in allow:
            raise refuse(
                APPROVAL_REQUIRED,
                f"project {project!r} is not in `policy.create_allow`. The "
                f"device is authorized for creation and this project is not.",
                device_ref=device_ref,
                project=project,
                allowed=sorted(allow),
            )

    # -- devices -----------------------------------------------------------

    def _probe(self, device_ref: str) -> tuple[bool, bool, bool, ProviderHealth | None]:
        provider = self._provider(device_ref)
        if provider is None:
            return False, False, False, None
        health = provider.health()
        return True, health.reachable, health.authorized, health

    def list_devices(self, principal: Principal, *, include_inactive: bool = True) -> dict[str, Any]:
        principal.require(SCOPE_READ)
        self.admit(principal)
        rows: list[dict[str, Any]] = []
        for device in self.registry.all():
            facts = authored_facts(device)
            present, reachable, authorized, health = self._probe(device.device_ref.ref)
            is_local = device.device_ref.ref == self.config.local_device_ref
            availability, capability, probe = measured_availability(
                device,
                provider_present=present,
                reachable=reachable,
                authorized=authorized,
                is_local=is_local,
            )
            facts.update(
                {
                    "availability": {
                        "fleet_presence": availability.fleet_presence,
                        "local_agent_controller": availability.local_agent_controller,
                        "remote_inventory_from_this_runtime": availability.remote_inventory_from_this_runtime,
                        "state": availability.state,
                    },
                    "capability": {
                        "transport_kind": capability.transport_kind,
                        "endpoint": capability.endpoint,
                        "available_here": list(capability.available_here()),
                        "external_only": list(capability.external_only()),
                    },
                    "provider": health.as_dict() if health else None,
                    "probe": probe,
                    "control": {
                        "send_allowed_device": bool(
                            self.config.provider_for(device.device_ref.ref)
                            and self.config.provider_for(device.device_ref.ref).allow_send
                        ),
                        "send_allowed_sessions": [
                            ref for ref in self.config.policy.send_allow if ref.startswith(device.device_ref.ref + "|")
                        ],
                        # Creation is reported separately from control, because
                        # it is granted separately. A caller that needs to make a
                        # session can read here whether it may — and, when it may
                        # not, which of the two halves is missing: the device, or
                        # every project (`create_allowed_projects` empty).
                        "create_allowed_device": bool(
                            self.config.provider_for(device.device_ref.ref)
                            and self.config.provider_for(device.device_ref.ref).allow_create
                        ),
                        "create_allowed_projects": list(self.config.policy.create_allow),
                    },
                }
            )
            if not include_inactive and availability.local_agent_controller != "AVAILABLE":
                continue
            rows.append(facts)
        self.audit.record("fleet.list_devices", principal=principal.name, devices=len(rows))
        return {
            "devices": rows,
            "total": len(rows),
            "local_device_ref": self.config.local_device_ref,
            "authority": {
                "identity": "fleet/registry.yaml (authored)",
                "reachability": "measured per call; never cached",
                "note": "a device listed here without a route is still an "
                        "enrolled device, and its absence from a session list "
                        "is not evidence that it has none",
            },
        }

    # -- sessions ----------------------------------------------------------

    def list_sessions(
        self,
        principal: Principal,
        *,
        device: str | None = None,
        project: str | None = None,
        state: str | None = None,
        limit: int = 20,
        classification: str | None = None,
    ) -> dict[str, Any]:
        principal.require(SCOPE_READ)
        self.admit(principal)

        if device:
            resolved = resolve_or_refuse(self.registry, device)
            targets = [resolved]
        else:
            targets = list(self.registry.all())

        sessions: list[dict[str, Any]] = []
        unreachable: list[dict[str, Any]] = []
        for entry in targets:
            ref = entry.device_ref.ref
            provider = self._provider(ref)
            if provider is None:
                unreachable.append(
                    {
                        "device_ref": ref,
                        "code": UNKNOWN_DEVICE,
                        "detail": "enrolled, but this runtime holds no route to it",
                    }
                )
                continue
            try:
                found = provider.list_sessions(limit=limit, project=project, state=state)
            except Exception as exc:  # noqa: BLE001 - one device's failure is one device's failure
                code = getattr(exc, "code", "INTERNAL")
                detail = getattr(exc, "detail", str(exc))
                unreachable.append({"device_ref": ref, "code": code, "detail": detail})
                continue
            for summary in found:
                row = summary.as_dict()
                row["device_display_name"] = entry.display_name
                row["control_allowed"] = self._session_control_state(summary.key)
                sessions.append(row)

        if classification:
            sessions = [s for s in sessions if s.get("classification") == classification]
        sessions.sort(key=lambda r: ((r.get("last_activity") or {}).get("iso") or ""), reverse=True)

        self.audit.record(
            "fleet.list_sessions",
            principal=principal.name,
            devices=len(targets),
            sessions=len(sessions),
            unreachable=[u["device_ref"] for u in unreachable],
        )
        return {
            "sessions": sessions,
            "total": len(sessions),
            "devices_queried": len(targets),
            "devices_unreachable": unreachable,
            "classification_legend": {
                "RUNNING_AGENT": "a run is executing right now",
                "RESIDENT_HOST": "a per-session host process is alive; no run executing",
                "MANAGED_IDLE": "the provider manages it; nothing is running",
                "ARCHIVED": "the provider reports it archived",
            },
            "identity_note": "a session is addressed by device_ref + provider + "
                             "native_session_id; the native id alone is not an "
                             "address and the same id can exist on two devices",
        }

    def _session_control_state(self, key: SessionKey) -> dict[str, Any]:
        binding = self.config.provider_for(key.device_ref)
        device_ok = bool(binding and binding.allow_send)
        session_ok = key.ref in self.config.policy.send_allow or (
            self.config.policy.allow_wildcard_send and "*" in self.config.policy.send_allow
        )
        return {
            "send_allowed": bool(device_ok and session_ok),
            "device_authorized": device_ok,
            "session_allowlisted": bool(session_ok),
        }

    @staticmethod
    def _create_note(control: dict[str, Any]) -> str:
        """What to tell a caller about the session creation just produced.

        Written from the `control` block rather than as a fixed sentence, so the
        note and the block cannot say different things — `create_session` and
        its replay both compute `control` afresh, and a note asserting "NOT
        authorized" beside a block reading `send_allowed: true` would be the
        surface contradicting itself in one result.
        """
        if control.get("send_allowed"):
            return (
                "This session was created and is ALREADY authorized for control: "
                "its session_ref is in `policy.send_allow` and the device allows "
                "sends. That grant came from the operator's binding, not from "
                "this call."
            )
        return (
            "This session was created and is NOT authorized for control. "
            "Creation conferred no right to drive what it created: "
            "`fleet_send_to_session` will refuse this session_ref until an "
            "operator adds it to `policy.send_allow`. The `control` block above "
            "reports that, and `send_allowed: false` is its expected reading "
            "here, not a fault."
        )

    def resolve_session(self, principal: Principal, *, session_ref: str | None,
                        device: str | None, session: str | None,
                        provider_name: str | None) -> SessionKey:
        if session_ref:
            key = SessionKey.parse(session_ref)
        elif device and session:
            key = SessionKey.compose(device, provider_name or "claude", session)
        else:
            raise refuse(
                BAD_REQUEST,
                "name the session either as `session_ref` "
                "(`device:<id>|<provider>|<native id>`) or as `device` + "
                "`session` (+ optional `provider`)",
            )
        resolve_or_refuse(self.registry, key.device_ref)
        return key

    def get_session(self, principal: Principal, key: SessionKey) -> dict[str, Any]:
        principal.require(SCOPE_READ)
        self.admit(principal)
        provider = self._require_provider(key.device_ref)
        detail = provider.get_session(key)
        detail["control"] = self._session_control_state(key)
        self.audit.record("fleet.get_session", principal=principal.name, session_ref=key.ref)
        return detail

    def read_session(
        self,
        principal: Principal,
        key: SessionKey,
        *,
        mode: str = "latest",
        limit: int | None = None,
        around_id: str | None = None,
        before: int | None = None,
        after: int | None = None,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        principal.require(SCOPE_READ)
        self.admit(principal)
        provider = self._require_provider(key.device_ref)
        applied, clamped = clamp_limit(
            limit, self.config.policy.max_read_messages, default=min(20, self.config.policy.max_read_messages)
        )
        payload = provider.read_session(
            key,
            mode=mode,
            limit=applied,
            around_id=around_id,
            before=before,
            after=after,
            cursor=cursor,
        )
        redactions = 0
        if self.config.policy.read_redact:
            payload, redactions = redact_payload(payload)
        result = {
            **key.as_dict(),
            "mode": mode,
            "limit_applied": applied,
            "limit_clamped": clamped,
            "redactions": redactions,
            "redaction_note": (
                "best-effort credential masking, not a guarantee of sanitization"
                if self.config.policy.read_redact
                else "disabled by policy"
            ),
            "untrusted_content": True,
            "untrusted_notice": UNTRUSTED_NOTICE,
            "transcript": payload,
        }
        self.audit.record(
            "fleet.read_session",
            principal=principal.name,
            session_ref=key.ref,
            mode=mode,
            limit=applied,
            redactions=redactions,
        )
        return result

    def send_to_session(
        self,
        principal: Principal,
        key: SessionKey,
        *,
        message: str,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        principal.require(SCOPE_SEND)
        self.admit(principal)
        if not message or not message.strip():
            raise refuse(BAD_REQUEST, "message must be a non-empty string")
        capped = message[: self.config.policy.max_message_chars]
        if len(capped) < len(message):
            raise refuse(
                WRITE_NOT_GRANTED,
                f"message is {len(message)} characters; the policy maximum is "
                f"{self.config.policy.max_message_chars}. Refused rather than "
                f"truncated: a silently shortened instruction is executed as if "
                f"it were the whole one.",
            )
        self._may_send(key)

        if idempotency_key:
            replayed = self._lookup_idempotent(principal, idempotency_key)
            if replayed is not None:
                self.audit.record(
                    "fleet.send.replay",
                    principal=principal.name,
                    session_ref=key.ref,
                    idempotency_key=idempotency_key,
                )
                return {
                    **key.as_dict(),
                    "idempotent_replay": True,
                    "code": IDEMPOTENT_REPLAY,
                    "detail": "this idempotency_key already produced this run; "
                              "no second message was sent",
                    "run": replayed,
                    "run_id": replayed.get("run_id"),
                }

        provider = self._require_provider(key.device_ref)
        payload = provider.send(key, message, idempotency_key=idempotency_key)
        run_id = payload.get("runId") or payload.get("run_id") or (payload.get("run") or {}).get("id")
        record = {
            "display_name": key.as_dict(),
            "run_id": run_id,
            "queued_message_uuid": payload.get("queuedMessageUuid") or payload.get("queued_message_uuid"),
            "accepted": payload.get("accepted", True),
            "raw": payload,
        }
        if idempotency_key and run_id:
            self._remember_idempotent(principal, idempotency_key, record)
        self.audit.record(
            "fleet.send",
            principal=principal.name,
            session_ref=key.ref,
            run_id=run_id,
            idempotency_key=idempotency_key,
        )
        return {
            **key.as_dict(),
            "idempotent_replay": False,
            "run_id": run_id,
            "queued_message_uuid": record["queued_message_uuid"],
            "run": record,
            "note": "session_send is message delivery. It grants no authority "
                    "over anything the receiving session may then operate on.",
        }

    def create_session(
        self,
        principal: Principal,
        *,
        device: str,
        project: str,
        message: str | None = None,
        lifecycle_mode: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a session in an authorized project, and hand back only its address.

        The shape of this call is the security property, so it is worth stating
        plainly. Creating a session is the one verb here whose *result* is a new
        object — and the object it produces is, by construction, one the caller
        cannot drive. `create_session` does not add anything to
        `policy.send_allow` and has no mechanism to: the allow-list is authored
        by an operator in the local binding, and this method has read access to
        it and no write access to anything. What it returns is a `session_ref`
        alongside a `control` block that is expected to read `send_allowed:
        false`. So "create a session and then instruct it" is two grants, and
        the caller learns the second one is missing from the answer to the
        first.

        That is deliberate and it is the reason this verb was worth adding to a
        surface whose whole design is that a caller never chooses what it is
        authorized to do. Without it, an agent able to create sessions would
        have a way to manufacture its own control targets.

        `message` is optional and means what it means on CloudCLI: with it, the
        first run is opened through the same control service a UI send uses and
        its run id comes back; without it, the session exists and nothing has
        run. The two are reported separately (`run_started`) rather than being
        collapsed, because "I made a session" and "I started an agent" are
        different things to have done.

        Idempotency is honoured here for the same reason it is honoured on
        `send`, and the stakes are higher: a resend duplicates a message, a
        re-create duplicates a *session*. A caller that repeats a key gets the
        recorded session back and no second one is made.
        """
        principal.require(SCOPE_CREATE)
        self.admit(principal)

        if not isinstance(project, str) or not project.strip():
            raise refuse(BAD_REQUEST, "`project` is required and must be a non-empty string")
        if lifecycle_mode is not None and lifecycle_mode not in LIFECYCLE_MODES:
            raise refuse(
                BAD_REQUEST,
                f"`lifecycle_mode` must be one of {list(LIFECYCLE_MODES)}; got "
                f"{lifecycle_mode!r}. Refused rather than relayed: `resident` is "
                f"the value that leaves a process running, so the accepted set "
                f"is this surface's to name, not whatever the far side accepts.",
            )
        if message is not None and len(message) > self.config.policy.max_message_chars:
            raise refuse(
                WRITE_NOT_GRANTED,
                f"message is {len(message)} characters; the policy maximum is "
                f"{self.config.policy.max_message_chars}. Refused rather than "
                f"truncated: a silently shortened instruction is executed as if "
                f"it were the whole one.",
            )

        device_ref = resolve_or_refuse(self.registry, device).device_ref.ref
        # Before the replay lookup, not after — the same order `send_to_session`
        # uses, and for the same reason: a grant the operator has since
        # withdrawn must not be re-exercised by replaying a key that was issued
        # under it.
        self._may_create(device_ref, project)

        if idempotency_key:
            replayed = self._lookup_idempotent(principal, idempotency_key)
            if replayed is not None:
                self.audit.record(
                    "fleet.create_session.replay",
                    principal=principal.name,
                    device_ref=device_ref,
                    project=project,
                    idempotency_key=idempotency_key,
                    session_ref=replayed.get("session_ref"),
                )
                # The stored record comes first so that the four fields below
                # overrule it. Spreading it last would return the creation's own
                # `created: True` and report a replay as a creation — the
                # reverse of what was asked, and the one answer a caller acting
                # on this result must not be given.
                replay: dict[str, Any] = {
                    **replayed,
                    "created": False,
                    "idempotent_replay": True,
                    "code": IDEMPOTENT_REPLAY,
                    "detail": "this idempotency_key already created this "
                              "session; no second session was created",
                }
                # `control` is recomputed rather than echoed. The session's
                # address and the run it produced are historical facts and are
                # returned as recorded; whether it may be driven is a
                # present-tense answer, and a grant added or withdrawn between
                # the two calls would otherwise be reported as it was then. A
                # caller that read a stale `send_allowed: false` would not try
                # the send it is now permitted to make, and one that read a
                # stale `true` would be told it may drive a session the operator
                # has since closed off.
                replay["control"] = self._session_control_state(
                    SessionKey.parse(replay["session_ref"])
                )
                replay["note"] = self._create_note(replay["control"])
                return replay

        provider = self._require_provider(device_ref)
        payload = provider.create_session(
            project=project,
            message=message or None,
            lifecycle_mode=lifecycle_mode,
        )
        native = payload.get("sessionId") or payload.get("session_id")
        if not native:
            raise refuse(
                INTERNAL,
                "the provider answered without naming the session it created. A "
                "session may exist on that device and this surface cannot "
                "address it — do not retry blindly; look at the device.",
                device_ref=device_ref,
                project=project,
            )
        # CloudCLI's own create defaults the provider family to `claude` when it
        # is not told otherwise, and this surface does not tell it otherwise —
        # the family is a property of the device's provider binding, never a
        # caller's choice, so there is no `provider` argument to get wrong.
        key = SessionKey.compose(device_ref, "claude", str(native))
        run_id = payload.get("runId") or payload.get("run_id")

        control = self._session_control_state(key)
        record = {
            **key.as_dict(),
            "created": True,
            "idempotent_replay": False,
            "run_id": run_id,
            "run_started": run_id is not None,
            "lifecycle_mode": lifecycle_mode,
            "control": control,
            "note": self._create_note(control),
        }
        if idempotency_key:
            self._remember_idempotent(principal, idempotency_key, record)
        self.audit.record(
            "fleet.create_session",
            principal=principal.name,
            device_ref=device_ref,
            project=project,
            session_ref=key.ref,
            run_id=run_id,
            lifecycle_mode=lifecycle_mode,
            idempotency_key=idempotency_key,
        )
        return record

    def get_run(
        self,
        principal: Principal,
        key: SessionKey,
        run_id: str,
        *,
        wait: bool = False,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any]:
        principal.require(SCOPE_READ)
        self.admit(principal)
        provider = self._require_provider(key.device_ref)
        payload = provider.get_run(key, run_id, wait=wait, wait_seconds=wait_seconds)
        self.audit.record(
            "fleet.get_run", principal=principal.name, session_ref=key.ref, run_id=run_id
        )
        return {**key.as_dict(), "run_id": run_id, **payload}

    # -- idempotency -------------------------------------------------------

    def _idem_identity(self, principal: Principal, key: str) -> str:
        return f"{principal.name}\x00{key}"

    def _lookup_idempotent(self, principal: Principal, key: str) -> dict[str, Any] | None:
        with self._idem_lock:
            self._sweep_idempotent()
            entry = self._idempotency.get(self._idem_identity(principal, key))
            return entry.run if entry else None

    def _remember_idempotent(self, principal: Principal, key: str, run: dict[str, Any]) -> None:
        with self._idem_lock:
            self._idempotency[self._idem_identity(principal, key)] = _IdempotencyEntry(run=run)

    def _sweep_idempotent(self) -> None:
        cutoff = time.time() - IDEMPOTENCY_TTL_SECONDS
        for key in [k for k, entry in self._idempotency.items() if entry.at < cutoff]:
            self._idempotency.pop(key, None)

    # -- status ------------------------------------------------------------

    def status(self, principal: Principal) -> dict[str, Any]:
        """Health of this surface and of every dependency it composes."""
        self.admit(principal)
        providers: list[dict[str, Any]] = []
        for ref, provider in sorted(self.providers.items()):
            health = provider.health()
            providers.append(health.as_dict())
        enrolled = [d.device_ref.ref for d in self.registry.all()]
        bound = sorted(self.providers)
        for ref in sorted(set(enrolled) - set(bound)):
            providers.append(
                {
                    "device_ref": ref,
                    "kind": None,
                    "reachable": False,
                    "authorized": False,
                    "detail": "enrolled with no provider binding on this runtime",
                    "error_code": UNKNOWN_DEVICE,
                }
            )
        return {
            "surface": {
                "server_name": "serveros-fleet-mcp",
                "version": "0.1.0",
                "local_device_ref": self.config.local_device_ref,
                "config_source": str(self.config.source) if self.config.source else None,
                "audit_enabled": bool(self.config.audit),
            },
            "identity_layer": {
                "authority": "fleet/registry.yaml",
                "enrolled_devices": enrolled,
                "devices_with_route": bound,
            },
            "providers": providers,
            "policy": {
                "send_allow": list(self.config.policy.send_allow),
                "create_allow": list(self.config.policy.create_allow),
                "read_redact": self.config.policy.read_redact,
                "max_read_messages": self.config.policy.max_read_messages,
            },
            "principal": principal.as_dict(),
            "recent_audit": self.audit.tail(20),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
