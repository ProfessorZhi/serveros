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
from . import cloudcli_native
from .config import CreateGrant, FleetConfig, GRANT_ELIGIBLE_OWNERSHIP, OPERATOR_GRANT_VERBS
from .errors import (
    APPROVAL_REQUIRED,
    BAD_REQUEST,
    BAD_SESSION_REF,
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
# The scope names are defined once, in `scopes.py`, and re-exported here because
# this module is where callers have always imported them from.
from .scopes import SCOPE_CONTROL, SCOPE_CREATE, SCOPE_READ, SCOPE_SEND  # noqa: F401

# How long a recorded idempotency key is honoured. Long enough to cover a
# caller's retry after a timeout, short enough that the table is bounded by the
# session it belongs to rather than by uptime.
IDEMPOTENCY_TTL_SECONDS = 3600

# The two verbs that carry an idempotency key, named once so the identity they
# compose cannot drift from the identity they look up. `send` and `create` share
# one table, so the verb is part of a key's identity: without it a key spent on
# one verb answers for the other, and the answer has the wrong shape.
_VERB_SEND = "send"
_VERB_CREATE = "create"

# There is deliberately no `_VERB_CONTROL`. `fleet_interrupt_session` takes no
# idempotency key and stores nothing, so it has no verb to file under — and the
# reason is worth stating where the other two are defined, because it looks like
# an omission. An interrupt is a statement about the present: "the run in flight
# is not in flight any more". A cached answer to that is a statement about the
# past wearing the present's clothes. Record `aborted: true` now, let a later run
# start, and a retry of the same key would be told the caller's run is stopped
# while it is still going. Replaying the *action* instead is the safe reading,
# because aborting an idle session is not an error — CloudCLI answers
# `aborted: false` and says there was no run to abort — so a duplicate delivers
# the truth rather than a fabrication. See `interrupt_session`.


def _create_scope(device_ref: str, project: str) -> str:
    """What a create key is scoped to: the device *and* the project.

    Both, because either alone is wrong in a way the caller cannot see. Scoped
    to the device only, the same key against two projects returns the first
    project's session — a session in the wrong working tree, wearing a plausible
    session id. Scoped to the project only, the same key against two devices
    returns a session on the wrong machine, which is the worse of the two: the
    caller asked Tokyo to make something and would be told it is on the laptop.
    """
    return f"{device_ref}\x00{project}"


def _key_from_record(record: dict[str, Any], device_ref: str) -> SessionKey | None:
    """Recover the session key from a stored create record, or None if it is not
    a shape this surface wrote. Never raises: a malformed record must not turn a
    replay into an error whose message is about this surface's own bookkeeping.

    The device is cross-checked rather than assumed. The identity a key is
    looked up under already includes the device, so a mismatch here would mean
    the lookup and the record disagree — and the honest answer to that is "I
    cannot say what this session is", which is a missing `control` block, not a
    session ref borrowed from the record.
    """
    session = record.get("session")
    if not isinstance(session, dict):
        return None
    ref = session.get("session_ref")
    if not isinstance(ref, str) or not ref:
        return None
    try:
        key = SessionKey.parse(ref)
    except Exception:  # noqa: BLE001 - a bad record is a missing control block, not a refusal
        return None
    return key if key.device_ref == device_ref else None


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

    def _device_ownership(self, device_ref: str) -> str | None:
        """The authored owner of a device, or None if the registry has no say."""
        for device in self.registry.all():
            if device.device_ref.ref == device_ref:
                return device.ownership
        return None

    def _operator_grant_verbs(self, principal: Principal, device_ref: str) -> tuple[str, ...]:
        """The verbs `principal` holds on `device_ref` — `()`, usually.

        Three exact matches and no fourth, each one a refusal with its own
        reason rather than a single "no":

        *   **Principal.** `principal.name`, which is what the presented
            credential produced. The caller cannot influence it: it is not a
            tool parameter, and `Principal` is constructed in the transport from
            the token, never from the request body.
        *   **Device.** The `device:<id>` ref, and only after the device passes
            the ownership test below.
        *   **Verb.** `send` is asked for by name. A caller holding a grant that
            lists only some other verb gets nothing here — verbs do not inherit
            from one another, which is what keeps the three planned ones from
            arriving for free on the day they are implemented.

        On the ownership test: a grant naming `device:personal-dell` is not
        *narrowed* here, it is inert, and the refusal says so. The rule is
        `ownership == "company"` — the registered devices of the organization
        this runtime serves — rather than "not personal", because `ownership` is
        `str | None` and an unset owner is an unknown owner, and an unknown owner
        is DEFER on this host rather than an authorization. Writing the eligible
        set as one allowed value also means a registry edit that changes a
        device's owner changes the answer here, with no second list to keep in
        step.
        """
        per_device = self.config.policy.operator_grants.get(principal.name)
        if not per_device:
            return ()
        verbs = per_device.get(device_ref)
        if not verbs:
            return ()
        if not self._device_is_grant_eligible(device_ref):
            return ()
        return tuple(v for v in verbs if v in OPERATOR_GRANT_VERBS)

    def _device_is_grant_eligible(self, device_ref: str) -> bool:
        """Whether a device may appear in an operator grant at all."""
        return self._device_ownership(device_ref) == GRANT_ELIGIBLE_OWNERSHIP

    def _may_send(self, principal: Principal, key: SessionKey) -> None:
        binding = self.config.provider_for(key.device_ref)
        if binding is None or not binding.allow_send:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.device_ref} is not authorized for control from this "
                f"runtime. Control is granted per device in the local binding "
                f"(`allow_send: true`), and per session in `policy.send_allow` "
                f"— never by the caller naming a session id.",
            )
        # The operator grant, checked before the per-session list and on a
        # different axis. Reaching here means the device is reachable at all;
        # what the grant adds is *who*, and a session that does not exist yet
        # cannot be named in `send_allow`, which is the whole reason the axis
        # exists. It does not widen `send_allow` in either direction: a
        # principal named in the grant still cannot send to a device its owner
        # is not the organization's, and a principal not named still has exactly
        # the per-session reach it had before.
        if "send" in self._operator_grant_verbs(principal, key.device_ref):
            # The one admission that leaves no trace in the binding: a send
            # allowed per device, to a session `send_allow` never named. The
            # `fleet.send` record that follows says a message went out but not
            # why it was permitted, and "why" is the question an audit of a
            # grant exists to answer.
            self.audit.record_grant(
                event="fleet.grant",
                principal=principal.name,
                device_ref=key.device_ref,
                verb="send",
                decision="allowed",
                grant_reason="operator-grant",
                session_ref=key.ref,
            )
            return
        allow = self.config.policy.send_allow
        if key.ref not in allow:
            # The refusal distinguishes three situations that a single "denied"
            # would flatten, because they send an operator to three different
            # edits: a principal with no grant anywhere, a principal whose grant
            # does name this device, and a grant that names a device its owner
            # does not make eligible. Only the third is a policy question rather
            # than a typo, and it is the one worth saying out loud — a grant
            # written against a personal device reads as authority and is inert.
            granted_here = (
                self.config.policy.operator_grants.get(principal.name, {}).get(key.device_ref)
            )
            if granted_here:
                ownership = self._device_ownership(key.device_ref)
                # Recorded as well as reported, because the operator who wrote
                # the grant and the principal refused by it are usually not the
                # same person: a grant that reads as authority and does nothing
                # is invisible to everyone except whoever hits it.
                self.audit.record_grant(
                    event="fleet.grant",
                    principal=principal.name,
                    device_ref=key.device_ref,
                    verb="send",
                    decision="refused",
                    grant_reason="device-not-eligible",
                    session_ref=key.ref,
                )
                detail = (
                    f"`policy.operator_grants[{principal.name!r}][{key.device_ref!r}]` "
                    f"does name {list(granted_here)}, but {key.device_ref} is "
                    f"{ownership!r} and only {GRANT_ELIGIBLE_OWNERSHIP!r} devices "
                    f"may be granted. The grant is inert here rather than "
                    f"narrowed: the owner of a device is a registry fact, and "
                    f"an unset owner is an unknown one, which this host defers "
                    f"on rather than authorizes."
                )
            else:
                detail = (
                    f"credential {principal.name!r} holds no `send` grant for "
                    f"{key.device_ref} in `policy.operator_grants`. If this name "
                    f"is not the one your credential presents, the grant is "
                    f"addressed to a principal that does not exist — the name is "
                    f"`oauth:<client_id>` for OAuth and `owner-pat` for the PAT."
                )
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.ref} is not in `policy.send_allow`, and {detail} Nothing "
                f"was inferred from the session id: a session is reachable by "
                f"being named, either per session in `send_allow` or per device "
                f"in an operator grant.",
                session_ref=key.ref,
                principal=principal.name,
                device_ref=key.device_ref,
                allowed=sorted(allow),
            )

    def _may_control(self, key: SessionKey) -> None:
        """The single place this runtime decides whether a run may be stopped.

        Two gates, both on this side, and both closed by default:

        *   **the device**, `allow_control` on its binding — may this runtime
            stop runs on that machine at all;
        *   **the session**, `policy.control_allow` — and, given the first, this
            one run in particular.

        They are separate from the send grants rather than shared with them.
        Delivering a message and ending the work it started are different
        questions, and an operator who answered the first has not answered the
        second; folding them would make "may write, may not stop" unexpressible.

        **Integration point.** This method is the one seam a device-wide owner
        grant for control is meant to plug into — a floor that answers "this
        operator may stop *any* run on this device" would widen the device arm
        here and leave the session arm alone. It is deliberately one method with
        one caller so that lands as an edit in a single place rather than as
        conditions scattered through the verb. Nothing in this revision
        implements that floor; the node connector has its own, separately.
        """
        binding = self.config.provider_for(key.device_ref)
        if binding is None or not binding.allow_control:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.device_ref} is not authorized for control from this "
                f"runtime. Stopping a run is granted per device in the local "
                f"binding (`allow_control: true`) and per session in "
                f"`policy.control_allow` — never by the caller naming a session "
                f"id, and never as a side effect of the send grants.",
            )
        allow = self.config.policy.control_allow
        if self.config.policy.allow_wildcard_control and "*" in allow:
            return
        if key.ref not in allow:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.ref} is not in `policy.control_allow`. The device is "
                f"authorized for control and this session is not.",
                session_ref=key.ref,
                allowed=sorted(allow),
            )

    def _may_create(self, device_ref: str, project: str) -> CreateGrant:
        """Creation is granted per device *and* per project, by the binding.

        Two gates, and both are needed. The device gate (`allow_create`) is a
        different flag from `allow_send` on purpose: a device authorized for
        control is not thereby a device authorized to be minted on, so an
        operator who wants one and not the other can say so. The project gate
        (`policy.create_allow`) is the second half, and it is the one that answers
        the question the device gate cannot: *which* project. A project id is
        what decides the working tree, the credentials and the reachable services
        a minted session inherits, so "authorized to create here" is not an
        answer to "authorized to create this".

        There is no per-session half — the session does not exist yet, and naming
        one is not a thing this call can do. That absence is why the two gates
        above carry the whole weight, and why the project allowlist is closed by
        default: an empty `create_allow` means no project, never every project.

        Returns the project's grant, so the caller checks the mode/model ceiling
        against the same lookup the authorization came from rather than resolving
        the project a second time and hoping the two agree.
        """
        binding = self.config.provider_for(device_ref)
        if binding is None:
            # No binding is not "not authorized", it is "not reachable", and the
            # two send an operator to different configuration. `_require_provider`
            # is where that distinction is already drawn; this defers to it
            # rather than folding unreachability into an authorization refusal.
            self._require_provider(device_ref)
        if binding is None or not binding.allow_create:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{device_ref} is not authorized for session creation from this "
                f"runtime. Creation is granted per device in the local binding "
                f"(`allow_create: true`) and is refused by default — never by "
                f"the caller naming a device id.",
            )
        grants = self.config.policy.create_allow
        if grants.get(project) is None:
            wildcard = grants.get("*") if self.config.policy.allow_wildcard_create else None
            if wildcard is None:
                raise refuse(
                    APPROVAL_REQUIRED,
                    f"project {project!r} is not in `policy.create_allow` for "
                    f"{device_ref}. The device is authorized for creation and "
                    f"this project is not: a project id decides which working "
                    f"tree and which credentials a minted session inherits, so "
                    f"the allowlist is closed and an unlisted project is refused "
                    f"rather than defaulted.",
                    device_ref=device_ref,
                    project=project,
                    allowed=sorted(grants),
                )
            return wildcard
        return grants[project]

    def _may_name_mode_and_model(
        self, device_ref: str, project: str, grant: CreateGrant, *,
        model: str | None, permission_mode: str | None,
    ) -> None:
        """Constrain what the caller may *name*, not the provider's own default.

        A caller that supplies neither gets the provider's default, which the
        operator who configured that provider has already accepted — so the
        ceilings only apply to values the caller chose. That asymmetry is the
        point: the failure this closes is a caller widening its own authority by
        asking for a session that runs with, say, `bypassPermissions`, which is a
        strictly larger grant than messaging a session the operator authorized.
        Without this check the create scope would be a route around `allow_send`.

        Empty means "you may not name one": the default posture is that a create
        grants no discretion over privilege or backend.
        """
        for what, value, allowed in (
            ("permission_mode", permission_mode, grant.permission_modes),
            ("model", model, grant.models),
        ):
            if value is None:
                continue
            if value not in allowed:
                raise refuse(
                    WRITE_NOT_GRANTED,
                    f"`{what}` {value!r} is not authorized for project {project!r} "
                    f"on {device_ref}. A caller-supplied {what} is constrained to "
                    f"the project's own development policy in the local binding "
                    f"(`policy.create_allow.<project>.{what}s`), and the default "
                    f"is that the caller may not name one at all — the provider's "
                    f"own default applies instead.",
                    device_ref=device_ref,
                    project=project,
                    requested=value,
                    allowed=sorted(allowed),
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
                        # The caller's *own* grant, reported to the caller.
                        # This is the discoverability surface for the grant, and
                        # it is safe to be one because it is scoped to the
                        # credential making the call: it can only ever show a
                        # caller what it already holds, never what someone else
                        # does. That matters more than usual here, because a
                        # grant addressed to a mis-spelled principal name cannot
                        # be caught at load — OAuth clients register at runtime —
                        # so "what do I actually hold" has to be answerable
                        # without reading the binding file.
                        "operator_grant_verbs": sorted(
                            self._operator_grant_verbs(principal, device.device_ref.ref)
                        ),
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
                row["control_allowed"] = self._session_control_state(principal, summary.key)
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

    def _session_control_state(self, principal: Principal, key: SessionKey) -> dict[str, Any]:
        """What `principal` may do with `key` — the same check `_may_send` makes.

        Takes the principal because the gate it reports on does. A control state
        computed without one would answer "may anyone", and reporting that under
        the key `send_allowed` would tell a caller it may send to a session that
        `send_to_session` will refuse — a read surface disagreeing with the
        machine about the one question it was asked, which is worse than not
        reporting it.
        """
        binding = self.config.provider_for(key.device_ref)
        device_ok = bool(binding and binding.allow_send)
        session_ok = key.ref in self.config.policy.send_allow
        grant_verbs = self._operator_grant_verbs(principal, key.device_ref)
        return {
            "send_allowed": bool(device_ok and (session_ok or "send" in grant_verbs)),
            "device_authorized": device_ok,
            "session_allowlisted": bool(session_ok),
            # Reported as the verbs the grant actually supplied, and empty for
            # the ordinary case, so a caller can tell "I am here because an
            # operator named this session" from "I am here because an operator
            # named me". `()` is also the answer for a grant that names this
            # principal and device but lists some other verb: a verb is not
            # satisfied by a sibling.
            "operator_grant_verbs": sorted(grant_verbs),
        }

    def _control_block(self, principal: Principal, key: SessionKey) -> dict[str, Any]:
        """The control state, computed fresh, with the not-a-grant said out loud.

        Attached to `create_session`'s answer because that is where the wrong
        inference is most available: a caller that has just minted a session and
        sees `session_ref` come back will read it as a handle it may now write
        through. It is not. `create` minted the session; `send` is a separate
        grant, checked per principal, per device and per session, and this block
        is the answer to that check rather than a promise about it.
        """
        return {
            **self._session_control_state(principal, key),
            "note": "control is a separate grant from creation. A session this "
                    "caller may not send to is reported, not hidden: sending "
                    "requires `allow_send` on the device and either the session "
                    "in `policy.send_allow` or a `send` grant for this principal "
                    "on this device in `policy.operator_grants`.",
        }

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
        detail["control"] = self._session_control_state(principal, key)
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
        # Above the replay lookup, for the same reason as in `create_session`:
        # `_may_send` is the revocation recheck. A session dropped from
        # `policy.send_allow` must stop answering from the idempotency cache too.
        self._may_send(principal, key)

        if idempotency_key:
            replayed = self._lookup_idempotent(principal, _VERB_SEND, key.ref, idempotency_key)
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
            self._remember_idempotent(principal, _VERB_SEND, key.ref, idempotency_key, record)
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

    def interrupt_session(self, principal: Principal, key: SessionKey) -> dict[str, Any]:
        """Stop the run this session currently has. Nothing else.

        What the verb is
        ----------------
        `session_interrupt` on the provider side stops the run a session has in
        flight and leaves its host alone — CloudCLI's own adapter holds no
        host-manager seam, so a resident session's process survives an interrupt
        with its pid. That shape is what makes this verb safe to expose at all:
        it is recoverable (the session row, the transcript and the process are
        all still there), and it is *not* delete, force-close or `session_close`,
        which the contract still lists as unbuilt for the good reason that they
        take something away that this surface cannot put back.

        Three answers, and the middle one is not a failure
        --------------------------------------------------
        An interrupt resolves to one of three things, and collapsing them would
        be the whole bug: the run was stopped; there was no run in flight; the
        call did not succeed. CloudCLI distinguishes the first two itself,
        answering `aborted: false` with a sentence saying there was no run to
        abort, and this method forwards that distinction rather than flattening
        it into a boolean. A caller told "aborted" when nothing was running
        would think it had stopped work that was never started.

        No idempotency key
        ------------------
        Accepted from nobody and stored for nobody — see the note beside
        `_VERB_SEND`. The caller's answer is the provider's answer, every time.

        No message content anywhere
        ---------------------------
        Nothing here is passed to the audit log but the session reference and the
        provider's own verdict. The verb has no free-text argument to leak, by
        construction rather than by redaction: an `interrupt` that also accepted
        a reason string would be a transcript-adjacent write in a log that
        promises not to hold one.
        """
        principal.require(SCOPE_CONTROL)
        self.admit(principal)
        self._may_control(key)

        provider = self._require_provider(key.device_ref)
        payload = provider.interrupt(key)
        # An answer with no `aborted` field is not an answer. This is not
        # defensive padding: `structured()` returns `{}` for a provider result it
        # could not parse, and `bool({}.get("aborted"))` is `False` — so without
        # this line a garbled reply would be reported to the caller as the
        # specific, confident claim "there was no run in flight". That is the
        # worst possible reading, because it tells the caller to stop worrying
        # about a run that may well still be going.
        if "aborted" not in payload:
            raise refuse(
                INTERNAL,
                f"{key.device_ref} answered `session_interrupt` without an "
                f"`aborted` field, so this surface cannot tell whether a run "
                f"was stopped. Reported rather than read as `false`.",
                session_ref=key.ref,
            )
        aborted = bool(payload["aborted"])
        self.audit.record(
            "fleet.interrupt",
            principal=principal.name,
            session_ref=key.ref,
            aborted=aborted,
        )
        answer: dict[str, Any] = {
            **key.as_dict(),
            "aborted": aborted,
            "note": "`aborted: false` means there was no run in flight, not that "
                    "the call failed. Interrupt stops a run; it does not close "
                    "the session, end its host, or delete anything.",
        }
        # Forwarded verbatim when the provider explained itself, so the caller
        # reads CloudCLI's own sentence rather than this surface's paraphrase of
        # it. `aborted: true` carries no message because there is nothing to
        # explain.
        message = payload.get("message")
        if message:
            answer["message"] = message
        return answer

    def create_session(
        self,
        principal: Principal,
        *,
        device: str,
        project: str,
        message: str | None = None,
        provider: str = "claude",
        model: str | None = None,
        lifecycle_mode: str | None = None,
        permission_mode: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Mint a session on one device, and start its first run only if asked.

        The verb is single-device by construction: unlike a listing, there is no
        useful "create on every device", and a fan-out here would be a way to
        make three machines work with one call. So the device is required and
        named, and there is no partial-completion state to report.

        Everything the caller supplies travels to the provider under the
        provider's own names (`project`, `message`, `provider`, `model`,
        `lifecycleMode`, `permissionMode`) — the surface translates once, in the
        adapter, and invents no second spelling. Two things it deliberately does
        not do: it does not forward the idempotency key (CloudCLI has no such
        concept, and the guard below is this surface's own), and it does not
        fabricate a run id for a create that started no run — "created" and
        "started" stay different words.
        """
        principal.require(SCOPE_CREATE)
        self.admit(principal)
        if not isinstance(project, str) or not project.strip():
            raise refuse(BAD_REQUEST, "`project` is required and must name a provider project id")
        if message is not None:
            if not isinstance(message, str):
                raise refuse(BAD_REQUEST, "`message` must be a string when supplied")
            if len(message) > self.config.policy.max_message_chars:
                raise refuse(
                    WRITE_NOT_GRANTED,
                    f"the first message is {len(message)} characters; the policy "
                    f"maximum is {self.config.policy.max_message_chars}. Refused "
                    f"rather than truncated: a silently shortened instruction is "
                    f"executed as if it were the whole one.",
                )
        if not isinstance(device, str) or not device.strip():
            # Refused as BAD_REQUEST and not UNKNOWN_DEVICE, and the difference
            # is the whole reason this check is here at all: an *absent* device
            # is an argument the caller did not supply, and "no such device"
            # would send them to `fleet_list_devices` to look for a device they
            # never named.
            raise refuse(
                BAD_REQUEST,
                "`device` is required and must be an enrolled `device:<id>` ref "
                "as reported by `fleet_list_devices`",
            )
        resolved = resolve_or_refuse(self.registry, device)
        device_ref = resolved.device_ref.ref
        # Reachability first, then policy. A caller with no route to the device
        # is told that, not told about an allowlist — the two answers point at
        # different configuration, and answering the second when the first is
        # true sends an operator to edit the wrong file.
        adapter = self._require_provider(device_ref)
        # Authorization is re-established on *every* call, including a call that
        # turns out to be an idempotent replay — which is why this line is above
        # the replay lookup and not below it. A replay is a plain "may you do
        # this now?" answered from a cached record, and the record was written
        # when the answer was yes. Moving the check after the lookup would make
        # the cache the authority: an operator who removes a project from
        # `create_allow` would find that the key minted under the old grant still
        # returns its session, so a revoked grant would keep answering for as
        # long as the key was retried.
        grant = self._may_create(device_ref, project)
        self._may_name_mode_and_model(
            device_ref, project, grant, model=model, permission_mode=permission_mode
        )

        if idempotency_key:
            replayed = self._lookup_idempotent(
                principal, _VERB_CREATE, _create_scope(device_ref, project), idempotency_key
            )
            if replayed is not None:
                self.audit.record(
                    "fleet.create_session.replay",
                    principal=principal.name,
                    device_ref=device_ref,
                    idempotency_key=idempotency_key,
                )
                key = _key_from_record(replayed, device_ref)
                return {
                    **replayed.get("session", {}),
                    "idempotent_replay": True,
                    "code": IDEMPOTENT_REPLAY,
                    "detail": "this idempotency_key already created this session; "
                              "no second session was created",
                    "run_id": replayed.get("run_id"),
                    "created": replayed,
                    # Recomputed rather than replayed: the session is the thing
                    # the key remembers, and what may be done with it is a
                    # question about policy *now*. A cached answer here would be
                    # exactly the stale grant this surface refuses everywhere
                    # else.
                    "control": self._control_block(principal, key) if key else None,
                    "note": "replayed from the idempotency record; no second "
                            "session was created and nothing on the device was "
                            "called.",
                }

        payload = adapter.create(
            project=project,
            message=message,
            provider=provider,
            model=model,
            lifecycle_mode=lifecycle_mode,
            permission_mode=permission_mode,
        )
        session_id = payload.get("sessionId") or payload.get("session_id")
        if not session_id:
            # A create that reports success must name what it created. There is
            # nothing sensible to return without it, and returning the bare
            # payload would hand the caller a shape this surface never promised.
            raise refuse(
                INTERNAL,
                f"{device_ref} answered session_create without a session id; "
                f"the session may exist and this surface cannot name it. Keys "
                f"answered: {sorted(payload)}",
            )
        key = SessionKey.compose(device_ref, provider, str(session_id))
        run_id = payload.get("runId") or payload.get("run_id")
        record = {
            "session": key.as_dict(),
            "run_id": run_id,
            "raw": payload,
        }
        if idempotency_key:
            self._remember_idempotent(
                principal, _VERB_CREATE, _create_scope(device_ref, project), idempotency_key, record
            )
        # The first message is not logged: the audit is a control-plane record,
        # and a record that accumulates what was said is a second copy of it.
        self.audit.record(
            "fleet.create_session",
            principal=principal.name,
            session_ref=key.ref,
            run_id=run_id,
            project=project,
            idempotency_key=idempotency_key,
        )
        return {
            **key.as_dict(),
            "idempotent_replay": False,
            "run_id": run_id,
            "created": record,
            # The caller is told, in the same answer, whether the session it just
            # minted is one it may drive. Sent separately it would be a second
            # round trip the caller could forget, and the natural mistake — treat
            # "created" as "mine to control" — is exactly the inference this
            # block exists to prevent.
            "control": self._control_block(principal, key),
            "note": "session_create mints a session and, when a first message was "
                    "given, starts its run. It grants no authority over anything "
                    "that session may then operate on: see `control` for whether "
                    "sending to it is authorized here, which is a separate grant.",
        }

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

    def _idem_identity(self, principal: Principal, verb: str, scope: str, key: str) -> str:
        """What an idempotency key is a name *for*.

        Four components, and every one of them is here because leaving it out
        was a bug with the same shape:

        *   **the credential** — otherwise one caller's key answers another's
            retry, which is a cross-credential answer to a question nobody asked;
        *   **the verb** — `send` and `create` had one shared table, so a key
            used to message a session would, replayed with `create`, return a
            *create* result that had never been created. Two verbs are two
            namespaces; a key is a name for one attempt, and the verb is part of
            which attempt it was;
        *   **the target** — the device and the project for `create`, the session
            ref for `send`. Without it, the same key against a *different*
            device or a different project returned the first device's session:
            the caller would be handed a session id minted somewhere else, on the
            strength of a key they only meant to retry once, and the second
            device would never be asked. This is the one that matters most,
            because the returned record *looks* wholly valid — it names a real
            session, just not the one that was asked for;
        *   **the key itself**.

        `\\x00` is the separator because it cannot occur in any of the four: a
        principal name, a verb, a project id and a session ref all come from
        text a user or a registry authored, and a separator that could occur in
        one component would let two distinct identities compose to the same
        string.
        """
        return f"{principal.name}\x00{verb}\x00{scope}\x00{key}"

    def _lookup_idempotent(
        self, principal: Principal, verb: str, scope: str, key: str
    ) -> dict[str, Any] | None:
        with self._idem_lock:
            self._sweep_idempotent()
            entry = self._idempotency.get(self._idem_identity(principal, verb, scope, key))
            return entry.run if entry else None

    def _remember_idempotent(
        self, principal: Principal, verb: str, scope: str, key: str, run: dict[str, Any]
    ) -> None:
        with self._idem_lock:
            self._idempotency[self._idem_identity(principal, verb, scope, key)] = (
                _IdempotencyEntry(run=run)
            )

    def _sweep_idempotent(self) -> None:
        cutoff = time.time() - IDEMPOTENCY_TTL_SECONDS
        for key in [k for k, entry in self._idempotency.items() if entry.at < cutoff]:
            self._idempotency.pop(key, None)

    # -- status ------------------------------------------------------------

    def native_call(
        self,
        principal: Principal,
        *,
        device_ref: str,
        native_tool: str,
        arguments: dict[str, Any] | None = None,
        session_ref: str | None = None,
        project: str | None = None,
    ) -> dict[str, Any]:
        """Inherit ONE CloudCLI native tool, under Fleet's own gates.

        The tool name is checked against the recorded closed set FIRST, because
        a name that is not in it is not a capability this build offers and must
        never reach a device. After that the call passes three gates a read
        could not satisfy:

          1. the native verb's OWN scopes, mapped onto Fleet scopes, through
             `Principal.require`. A native scope Fleet plans but does not
             implement (`approve`, `navigate`) maps to a planned Fleet scope and
             is refused — inheriting a device's verb cannot widen a credential;
          2. a write verb is routed through the SAME floor its Fleet verb uses
             (`_gate_native_write`): `_may_send` for send, `_may_control` for
             control, `_may_create` for create — never a weaker
             class-only check, and never by reading a session id out of the
             native payload;
          3. the provider must actually implement the native surface.

        What it deliberately does NOT do: name a device by a mutable default,
        forward a tool the caller named, guess a session or project identity from
        the native `arguments`, or translate the provider's answer. The
        provider's output travels out unchanged, minus the redaction the MCP
        surface applies to every payload.
        """
        native = cloudcli_native.NATIVE_BY_NAME.get(native_tool)
        if native is None:
            raise refuse(
                BAD_REQUEST,
                f"{native_tool!r} is not a CloudCLI tool this build mirrors. The "
                f"mirrored set is the {cloudcli_native.NATIVE_TOOL_COUNT} tools in "
                f"`fleet_cloudcli_native_manifest`; a name that is not among them "
                f"is refused rather than forwarded to a device.",
            )
        binding = self.config.provider_for(device_ref)
        if binding is None or not binding.enabled:
            raise refuse(
                UNKNOWN_DEVICE,
                f"{device_ref} is not an enabled provider on this runtime.",
            )
        for fleet_scope in native.fleet_scopes():
            principal.require(fleet_scope)
        self._gate_native_write(principal, device_ref, binding, native,
                                arguments or {}, session_ref=session_ref,
                                project=project)
        provider = self._require_provider(device_ref)
        caller = getattr(provider, "native_call", None)
        if caller is None:
            raise refuse(
                BAD_REQUEST,
                f"{device_ref}'s provider ({provider.kind}) does not inherit the "
                f"CloudCLI native surface.",
            )
        result = caller(native_tool, dict(arguments or {}))
        audit = getattr(self, "audit", None)
        if audit is not None:
            # Only allowlisted fields: the egress table (`disclosure.py`) admits
            # `principal` and `device_ref`. The native tool NAME is a fact worth
            # recording and is not on that table yet — naming it belongs to
            # `disclosure.py`'s owner, not to this caller.
            audit.record("mcp.native_call", principal=principal.name,
                         device_ref=device_ref)
        return result

    def _gate_native_write(self, principal: Principal, device_ref: str, binding,
                           native, arguments: dict[str, Any], *,
                           session_ref: str | None, project: str | None) -> None:
        """Route a native write through the SAME floor its Fleet verb uses.

        A read returns here untouched. Every other class is refused unless the
        identity its floor is stated over is present AND consistent:

          * send / control -> a `session_ref`, whose device must equal
            `device_ref`; the floor is `_may_send` / `_may_control`, reused
            verbatim, so `policy.send_allow` and `policy.control_allow` bind;
          * create -> a `project`; the floor is `_may_create`, so the project
            allowlist binds.

        A class whose identity cannot be established is REFUSED, never guessed
        from the native payload: a native `session` id inside `arguments` is not
        a Fleet address, and inferring a device, a provider or a project from it
        is exactly how a caller would reach a session or project the floor never
        allowed. `arguments` is carried to the device verbatim but is NEVER a
        source of authority.
        """
        governance = native.governance
        if governance == cloudcli_native.GOV_READ:
            return
        if governance in (cloudcli_native.GOV_SEND, cloudcli_native.GOV_CONTROL):
            key = self._native_session_key(device_ref, session_ref, native)
            if governance == cloudcli_native.GOV_SEND:
                self._may_send(principal, key)
            else:
                self._may_control(key)
                self._require_operator_verb(principal, device_ref, "control", native)
            return
        if governance == cloudcli_native.GOV_CREATE:
            project_name = self._native_project(project, native)
            self._may_create(device_ref, project_name)
            self._require_operator_verb(principal, device_ref, "create", native,
                                        project=project_name)
            return
        # approve / navigate: their Fleet scope is planned and `principal.require`
        # already refused them above. Reaching here is a defect in the mapping.
        raise refuse(
            WRITE_NOT_GRANTED,
            f"`{native.namespaced}` is a {governance} verb with no implemented "
            f"Fleet floor; it must be refused at the scope gate, and reaching "
            f"this branch is a defect in `cloudcli_native`'s scope map.",
        )

    def _native_session_key(self, device_ref: str, session_ref: str | None,
                            native) -> SessionKey:
        """The session a native write addresses, or a refusal.

        Required rather than inferred. A native `session` id inside `arguments`
        is a device-local id, not a Fleet address: it carries no device and no
        provider, and composing one here would be a guess the floor would then
        be stated over. The caller names the session the way the rest of Fleet
        does, and the device half must agree with `device_ref`.
        """
        if not session_ref or not str(session_ref).strip():
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}` addresses one session and this call "
                f"named none. Pass `session_ref` (`device:<id>|<provider>|"
                f"<native session id>`), the same address `fleet_list_sessions` "
                f"returns — a native `session` id inside `arguments` is not an "
                f"address this runtime can check.",
            )
        try:
            key = SessionKey.parse(str(session_ref))
        except Exception:  # noqa: BLE001 - any parse failure is one refusal
            raise refuse(
                BAD_SESSION_REF,
                f"`session_ref` names no session this runtime can parse. Use the "
                f"full `device:<id>|<provider>|<native session id>` form.",
            ) from None
        if key.device_ref != device_ref:
            raise refuse(
                BAD_SESSION_REF,
                f"`session_ref` names {key.device_ref} and `device_ref` names "
                f"{device_ref}; a session is addressed on the device that holds "
                f"it, and the two halves must agree.",
                session_device=key.device_ref, device_ref=device_ref,
            )
        return key

    def _native_project(self, project: str | None, native) -> str:
        """The project a native create is for, or a refusal. Never inferred."""
        if not isinstance(project, str) or not project.strip():
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}` mints a session and this call named no "
                f"`project`. A project id decides which working tree and which "
                f"credentials the new session inherits, so it is named here "
                f"rather than read out of the native payload.",
            )
        return project.strip()

    def _require_operator_verb(self, principal: Principal, device_ref: str,
                               verb: str, native, *, project: str | None = None) -> None:
        """The principal x device x verb grant, on top of the class floor.

        The floor (`_may_control` / `_may_create`) answers «may this device be
        driven, on this session or project»; this answers «may THIS principal».
        Both are required, so a device that is control-enabled with no grant to
        this credential is still refused, and the refusal says which half is
        missing.
        """
        if verb not in OPERATOR_GRANT_VERBS:
            raise refuse(
                WRITE_NOT_GRANTED,
                f"`{native.namespaced}` needs the `{verb}` operator verb, which "
                f"this build does not implement (OPERATOR_GRANT_VERBS="
                f"{list(OPERATOR_GRANT_VERBS)}); no grant can express it, so it "
                f"is refused rather than inherited.",
                device_ref=device_ref, required_verb=verb,
            )
        verbs = self._operator_grant_verbs(principal, device_ref)
        if verb not in verbs:
            raise refuse(
                WRITE_NOT_GRANTED,
                f"`{native.namespaced}` needs a `{verb}` operator grant for "
                f"{principal.name!r} on {device_ref}; the operator grant verbs "
                f"held are {list(verbs) or 'none'}. The device floor passed and "
                f"the principal is not granted, so the call is refused.",
                device_ref=device_ref, required_verb=verb,
                **({"project": project} if project is not None else {}),
            )

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
                "read_redact": self.config.policy.read_redact,
                "max_read_messages": self.config.policy.max_read_messages,
            },
            "principal": principal.as_dict(),
            "recent_audit": self.audit.tail(20),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
