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

import json
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from .audit import AuditLog, RateLimiter
from . import cloudcli_native, runtime_identity
from .config import (CreateGrant, FleetConfig, GRANT_ELIGIBLE_OWNERSHIP,
                     OPERATOR_GRANT_VERBS, provider_key)
from .runtime_identity import BASE_RUNTIME
from .runtime_create_policy import PolicyError
from .errors import (
    APPROVAL_REQUIRED,
    BAD_REQUEST,
    BAD_SESSION_REF,
    FleetRefusal,
    IDEMPOTENT_REPLAY,
    INTERNAL,
    RATE_LIMITED,
    SCOPE_DENIED,
    UNKNOWN_DEVICE,
    WRITE_NOT_GRANTED,
    OUTPUT_TOO_LARGE,
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
from .scopes import (SCOPE_APPROVE, SCOPE_CONTROL, SCOPE_CREATE, SCOPE_NAVIGATE,  # noqa: F401
                     SCOPE_READ, SCOPE_SEND)

# How long a recorded idempotency key is honoured. Long enough to cover a
# caller's retry after a timeout, short enough that the table is bounded by the
# session it belongs to rather than by uptime.
IDEMPOTENCY_TTL_SECONDS = 3600

#: The largest mirrored CloudCLI answer this surface will relay, in BYTES. A
#: paginated read (`limit`, `cursor`) stays far under it; a runaway single
#: transcript does not. It bounds the MIRROR only — the hand-written nine keep
#: their own semantics and their own read bounds, so this changes no old tool.
MAX_NATIVE_RESULT_BYTES = 1_048_576

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


def _create_scope(device_ref: str, project: str,
                  runtime: str = BASE_RUNTIME) -> str:
    """What a create key is scoped to: the device, the *runtime*, the project.

    All three, because any one alone is wrong in a way the caller cannot see.
    Scoped to the device only, the same key against two projects returns the
    first project's session — a session in the wrong working tree, wearing a
    plausible session id. Scoped to the project only, the same key against two
    devices returns a session on the wrong machine, which is the worse of the
    two: the caller asked Tokyo to make something and would be told it is on
    the laptop. And scoped without the runtime, the same key against two
    accounts on ONE machine returns whichever account answered first — which is
    the cross-account case this all exists to prevent, and the one nobody can
    see from the returned ref, because both sessions are on the right device and
    differ only in whose credential they were minted under.

    The runtime is carried as its own component rather than folded into the
    device, so the three axes stay separately readable in the key.
    """
    return f"{device_ref}\x00{runtime}\x00{project}"


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


def _approval_ids(listing) -> set[str]:
    """The request ids a device's `approvals_list` answer carries.

    Tolerant of the ENVELOPE (`approvals` / `items` / `data` / `requests`, at
    any depth) and strict about what counts as an id: a top-level list of
    objects, each naming its request under `requestId` / `request_id` / `id`.
    Anything else yields nothing — and nothing means «cannot locate», which the
    caller refuses on, so an unrecognised shape fails closed rather than open.
    """
    return cloudcli_native.approval_ids(listing)


def _session_project(detail) -> str | None:
    """The project id a device's `session_get` answer reports, or `None`.

    Looks at the answer and one nested level (`session` / `data`), under the
    names CloudCLI uses (`projectId` / `project_id` / `project`). `None` means
    «the device did not state a project», which a caller refuses on — so an
    unrecognised shape fails closed rather than defaulting to the caller's claim.
    """
    if not isinstance(detail, dict):
        return None
    for node in (detail, detail.get("session"), detail.get("data")):
        if isinstance(node, dict):
            for key in ("projectId", "project_id", "project"):
                value = node.get(key)
                if isinstance(value, str) and value:
                    return value
    return None


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

    def _provider(self, device_ref: str,
                  runtime: str = BASE_RUNTIME) -> DeviceProvider | None:
        return self.providers.get(provider_key(device_ref, runtime))

    def _require_provider(self, device_ref: str,
                          runtime: str = BASE_RUNTIME) -> DeviceProvider:
        """The provider for one RUNTIME on one device.

        One physical machine can reach this surface through more than one
        binding — a second OS account with its own CloudCLI, its own endpoint
        and its own credential — and the runtime is what picks between them.
        `runtime` defaults to the device's own, so a caller written before a
        device could serve two reaches exactly the binding it reached then.
        """
        provider = self.providers.get(provider_key(device_ref, runtime))
        if provider is None:
            raise refuse(
                UNKNOWN_DEVICE,
                f"{device_ref} has no route for runtime {runtime!r} on this "
                f"runtime. One device may hold several; this one holds "
                f"{sorted(k for k in self.providers if k.split('|')[0] == device_ref) or 'none'}. "
                f"`fleet_list_devices` reports the same fact as "
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

    def _operator_grant_verbs(self, principal: Principal, device_ref: str,
                              runtime: str = BASE_RUNTIME) -> tuple[str, ...]:
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
        # Looked up under the SAME key a provider binding uses: the base runtime
        # keeps the bare `device:<id>` key, so a grant written before a device
        # could serve two means exactly what it meant then, and a SECOND runtime
        # needs its own entry. That is the "independent owner consent" a new
        # runtime requires — it is not inherited from the runtime beside it.
        verbs = per_device.get(provider_key(device_ref, runtime))
        if not verbs:
            return ()
        if not self._device_is_grant_eligible(device_ref):
            return ()
        return tuple(v for v in verbs if v in OPERATOR_GRANT_VERBS)

    def _device_is_grant_eligible(self, device_ref: str) -> bool:
        """Whether a device may appear in an operator grant at all."""
        return self._device_ownership(device_ref) == GRANT_ELIGIBLE_OWNERSHIP

    def _may_send(self, principal: Principal, key: SessionKey) -> None:
        binding = self.config.provider_for(key.device_ref, key.provider)
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
        if "send" in self._operator_grant_verbs(principal, key.device_ref, key.provider):
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
                    f"`policy.operator_grants[{principal.name!r}]"
                    f"[{provider_key(key.device_ref, key.provider)!r}]` "
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

    def _may_control(self, principal: Principal, key: SessionKey) -> None:
        """The single place this runtime decides whether a run may be stopped.

        Three gates, all on this side, and all closed by default:

        *   **the device**, `allow_control` on its binding — may this runtime
            stop runs on that machine at all;
        *   **the principal**, the `control` operator grant for this credential
            on this device — may *this* caller stop anything there;
        *   **the session**, `policy.control_allow` — and, given the first, this
            one run in particular.

        The middle gate is what makes a session a caller just created usable
        without a second, manual edit. Before it, control was granted only by
        naming each session in `policy.control_allow`, so every newly minted
        session had to be added there by hand before it could be stopped — which
        is a floor nobody keeps up, and a floor that is not maintained is one
        that gets widened to `*` instead. `_operator_grant_verbs` matches the
        principal, the device and the verb, and returns `()` unless the device is
        `ownership: company`, so this arm cannot reach a personal machine and a
        grant written for one is inert. It grants nothing by default: a principal
        with no entry still holds nothing, and the device flag and the per-session
        list are both still checked.

        The three are separate from the send grants rather than shared with them.
        Delivering a message and ending the work it started are different
        questions, and an operator who answered the first has not answered the
        second; folding them would make "may write, may not stop" unexpressible.

        Every control verb on this surface — `fleet_interrupt_session` and the
        mirrored `session_interrupt` / `session_close` / `session_reconfigure` /
        `session_background`'s stop branch — reaches this one method, so they
        cannot disagree about who may stop what.
        """
        binding = self.config.provider_for(key.device_ref, key.provider)
        if binding is None or not binding.allow_control:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.device_ref} is not authorized for control from this "
                f"runtime. Stopping a run is granted per device in the local "
                f"binding (`allow_control: true`) and per session in "
                f"`policy.control_allow` — never by the caller naming a session "
                f"id, and never as a side effect of the send grants.",
            )
        if "control" in self._operator_grant_verbs(principal, key.device_ref, key.provider):
            self.audit.record_grant(
                event="fleet.grant",
                principal=principal.name,
                device_ref=key.device_ref,
                verb="control",
                decision="allowed",
                grant_reason="operator-grant",
                session_ref=key.ref,
            )
            return
        allow = self.config.policy.control_allow
        if self.config.policy.allow_wildcard_control and "*" in allow:
            return
        if key.ref not in allow:
            granted_here = (
                self.config.policy.operator_grants.get(principal.name, {}).get(key.device_ref)
            )
            if granted_here:
                self.audit.record_grant(
                    event="fleet.grant",
                    principal=principal.name,
                    device_ref=key.device_ref,
                    verb="control",
                    decision="refused",
                    grant_reason="device-not-eligible",
                    session_ref=key.ref,
                )
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.ref} is not in `policy.control_allow`, and "
                f"{principal.name!r} holds no `control` grant for "
                f"{key.device_ref}"
                + (" — the grant the operator wrote for this device does name "
                   "verbs, but this device is not one the organization owns, so "
                   "the grant is inert here rather than narrowed."
                   if granted_here else "")
                + ". The device is authorized for control and this session is not.",
                session_ref=key.ref,
                allowed=sorted(allow),
            )

    def _may_create(self, device_ref: str, project: str,
                    runtime: str = BASE_RUNTIME) -> CreateGrant:
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

        Both gates are read for the caller's RUNTIME, not for the device alone.
        `allow_create` lives on a binding, and a binding is one account's
        reach — so a machine that authorized creation in one account has not
        authorized it in the account beside it, and reading the device's first
        binding would answer for the wrong one. The runtime is a parameter here
        rather than a lookup this function performs for itself, so the caller
        resolves it once and every gate below reads the same account.

        Returns the project's grant, so the caller checks the mode/model ceiling
        against the same lookup the authorization came from rather than resolving
        the project a second time and hoping the two agree.
        """
        binding = self.config.provider_for(device_ref, runtime)
        if binding is None:
            # No binding is not "not authorized", it is "not reachable", and the
            # two send an operator to different configuration. `_require_provider`
            # is where that distinction is already drawn; this defers to it
            # rather than folding unreachability into an authorization refusal.
            # It is asked for the SAME runtime, so the two cannot disagree about
            # which account was unreachable.
            self._require_provider(device_ref, runtime)
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

    def _bound_runtimes(self, device_ref: str) -> tuple[str, ...]:
        """Every runtime this host holds a BINDING for on one device, base first.

        A binding is not a route. It may be disabled, or its provider may have
        failed to build, and neither is knowable from the config alone — which is
        exactly why this reports the binding and `_provider` reports the route.
        A machine that declares `fjd`'s runtime and whose `fjd` connector is down
        is a machine with a gap an operator can close, and saying so is more use
        than a listing that is silently one runtime short.

        Read off the bindings' own `device_ref`/`runtime` fields rather than by
        splitting keys: `provider_key` is the surface's spelling of the pair, and
        parsing a spelling back into its parts is how two spellings drift.
        """
        owned = [b for b in self.config.providers.values() if b.device_ref == device_ref]
        others = sorted({b.runtime for b in owned if b.runtime != BASE_RUNTIME})
        if any(b.runtime == BASE_RUNTIME for b in owned):
            return (BASE_RUNTIME, *others)
        return tuple(others)

    def _runtimes_for(self, device_ref: str, runtime: str | None) -> tuple[str, ...]:
        """Which runtimes one device is queried for, given a selector.

        `None` — the default — is the device's OWN runtime, and only it. That is
        what every caller written before a device could serve two already meant,
        so the default is not a new behaviour with a compatibility story; it is
        the old behaviour with a name.

        The selector cannot be a tag: `config.py` refuses a binding whose runtime
        is one, so `all` here is never a machine's second runtime that happens to
        be called `all`.
        """
        if runtime is None or runtime == runtime_identity.SELECTOR_BASE:
            return (BASE_RUNTIME,)
        if runtime in (runtime_identity.SELECTOR_ALL, runtime_identity.SELECTOR_ANY):
            return self._bound_runtimes(device_ref)
        return (runtime,)

    def _validate_runtime_selector(self, runtime: str | None) -> None:
        """Refuse a malformed selector once, before any device is asked.

        Malformed and unknown are different answers and get different handling. A
        selector that is not a runtime NAME at all (`FJD`, `claude fjd`) cannot
        be answered by any device, so it is a BAD_REQUEST raised here. A legal
        name with nothing behind it (`claude-nobody`) is a per-device gap and is
        reported as one, because on a fleet of several machines the second is
        true of some devices and not others.

        The grammar authority is `SessionKey`, so it is asked rather than
        restated — a second regex is a second answer to the same question.
        """
        if runtime is None or runtime_identity.is_a_selector(runtime):
            return
        try:
            SessionKey(self.config.local_device_ref, runtime, "runtime-selector")
        except ValueError as exc:
            raise refuse(
                BAD_REQUEST,
                f"{runtime!r} is not a runtime name: {exc}. Name one runtime as "
                f"`{BASE_RUNTIME}` or `{BASE_RUNTIME}-<account>`, or select every "
                f"runtime on the device with "
                f"`{runtime_identity.SELECTOR_ALL}`.",
            ) from None

    def list_sessions(
        self,
        principal: Principal,
        *,
        device: str | None = None,
        project: str | None = None,
        state: str | None = None,
        limit: int = 20,
        classification: str | None = None,
        runtime: str | None = None,
    ) -> dict[str, Any]:
        """Sessions across the fleet, one row per (device, runtime) pair.

        One physical machine can serve more than one runtime — a second OS
        account with its own CloudCLI — so `device × runtime` is the unit this
        walks, not `device`. `limit` is per RUNTIME, the same way it was per
        device when a device was one runtime; a machine with two runtimes returns
        up to twice as many rows, and `runtimes_queried` and
        `sessions_per_runtime` say how many were asked and what each answered, so
        a caller can tell "there are no more" from "the page is full".

        A runtime that did not answer is an entry in `devices_unreachable`, named
        with its device AND its runtime, and every OTHER runtime's rows are still
        returned. That is deliberate and it is the whole of the partial-result
        rule: one runtime being down never hides another's sessions, and never
        turns into a refusal that would hide the ones that did answer.
        """
        principal.require(SCOPE_READ)
        self.admit(principal)
        self._validate_runtime_selector(runtime)

        if device:
            resolved = resolve_or_refuse(self.registry, device)
            targets = [resolved]
        else:
            targets = list(self.registry.all())

        sessions: list[dict[str, Any]] = []
        unreachable: list[dict[str, Any]] = []
        queried: list[str] = []
        for entry in targets:
            ref = entry.device_ref.ref
            selected = self._runtimes_for(ref, runtime)
            if not selected:
                # `all` resolved to no runtimes, which happens on a device this
                # host holds no binding for. Skipped, that device would
                # contribute neither a row nor an entry, and a caller could not
                # tell it from one that answered with nothing — the same
                # silence the per-runtime gap below exists to remove, one level
                # up. The default selector never lands here: it asks for the
                # device's own runtime whether or not a binding exists, and
                # reports the absence as a gap.
                unreachable.append(
                    {
                        "device_ref": ref,
                        "runtime": None,
                        "provider_key": ref,
                        "code": UNKNOWN_DEVICE,
                        "detail": "this host holds no binding for any runtime "
                                  "on this device, so `all` had nothing to ask",
                    }
                )
                continue
            for one in selected:
                # The ledger key, which is also what the audit records: the bare
                # device ref for the device's own runtime (unchanged), and
                # `device:<id>|<tag>` for a second one. One string cannot be two
                # runtimes' worth of rows.
                where = provider_key(ref, one)
                queried.append(where)
                provider = self._provider(ref, one)
                if provider is None:
                    unreachable.append(
                        {
                            "device_ref": ref,
                            "runtime": one,
                            "provider_key": where,
                            "code": UNKNOWN_DEVICE,
                            "detail": "enrolled, and bound, but this runtime holds no route to it",
                        }
                    )
                    continue
                try:
                    found = provider.list_sessions(limit=limit, project=project, state=state)
                except Exception as exc:  # noqa: BLE001 - one runtime's failure is one runtime's failure
                    code = getattr(exc, "code", "INTERNAL")
                    detail = getattr(exc, "detail", str(exc))
                    unreachable.append(
                        {
                            "device_ref": ref,
                            "runtime": one,
                            "provider_key": where,
                            "code": code,
                            "detail": detail,
                        }
                    )
                    continue
                for summary in found:
                    row = summary.as_dict()
                    row["device_display_name"] = entry.display_name
                    # Said explicitly rather than left implicit in `provider`.
                    # A session ref's `provider` field IS the runtime tag, but
                    # `provider` elsewhere on this surface means the CloudCLI
                    # AGENT FAMILY (`create_session`), and one connector serves
                    # several. A caller reading `provider` on a row and taking it
                    # for the agent family would be reading the wrong axis, so the
                    # runtime is named as itself.
                    row["runtime"] = summary.key.provider
                    row["control_allowed"] = self._session_control_state(principal, summary.key)
                    sessions.append(row)

        if classification:
            sessions = [s for s in sessions if s.get("classification") == classification]
        sessions.sort(key=lambda r: ((r.get("last_activity") or {}).get("iso") or ""), reverse=True)

        self.audit.record(
            "fleet.list_sessions",
            principal=principal.name,
            devices=len(targets),
            runtimes=len(queried),
            sessions=len(sessions),
            unreachable=[u["provider_key"] for u in unreachable],
        )
        counts: dict[str, int] = {}
        for row in sessions:
            key = provider_key(row["device_ref"], row["runtime"])
            counts[key] = counts.get(key, 0) + 1
        return {
            "sessions": sessions,
            "total": len(sessions),
            "devices_queried": len(targets),
            # Per RUNTIME, and named by the same ledger key the config files
            # bindings under, so a caller can tell which machine-and-runtime
            # answered and which one it is looking at. A device bound for two
            # runtimes appears twice, once per runtime: the pair is the unit, and
            # a count of one for a two-runtime device would be the arithmetic
            # saying the opposite of what happened.
            "runtimes_queried": queried,
            "sessions_per_runtime": counts,
            # Named with their runtime, not their device alone. On a machine
            # serving two runtimes, "the device is unreachable" is not a thing
            # that can be true: one runtime answered and the other did not, and
            # an entry that named only the device would make those two facts
            # indistinguishable.
            "devices_unreachable": unreachable,
            "classification_legend": {
                "RUNNING_AGENT": "a run is executing right now",
                "RESIDENT_HOST": "a per-session host process is alive; no run executing",
                "MANAGED_IDLE": "the provider manages it; nothing is running",
                "ARCHIVED": "the provider reports it archived",
            },
            "identity_note": "a session is addressed by device_ref + provider + "
                             "native_session_id; the native id alone is not an "
                             "address and the same id can exist on two devices. "
                             "A row's `runtime` is that `provider`, said plainly: "
                             "one physical machine can serve several runtimes, "
                             "and the same native id under two of them is two "
                             "different sessions, not a duplicate.",
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
        binding = self.config.provider_for(key.device_ref, key.provider)
        device_ok = bool(binding and binding.allow_send)
        session_ok = key.ref in self.config.policy.send_allow
        grant_verbs = self._operator_grant_verbs(principal, key.device_ref, key.provider)
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
        provider = self._require_provider(key.device_ref, key.provider)
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
        provider = self._require_provider(key.device_ref, key.provider)
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

        provider = self._require_provider(key.device_ref, key.provider)
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
        self._may_control(principal, key)

        provider = self._require_provider(key.device_ref, key.provider)
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
        runtime: str = BASE_RUNTIME,
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
        adapter = self._require_provider(device_ref, runtime)
        # Authorization is re-established on *every* call, including a call that
        # turns out to be an idempotent replay — which is why this line is above
        # the replay lookup and not below it. A replay is a plain "may you do
        # this now?" answered from a cached record, and the record was written
        # when the answer was yes. Moving the check after the lookup would make
        # the cache the authority: an operator who removes a project from
        # `create_allow` would find that the key minted under the old grant still
        # returns its session, so a revoked grant would keep answering for as
        # long as the key was retried.
        grant = self._may_create(device_ref, project, runtime)
        # Owner-approved defaults are addressed by the EXACT bound runtime and
        # project. No global default, fallback account, or operator-provided
        # endpoint is permitted. Existing installations with no policy keep the
        # provider's own default behavior byte-for-byte.
        runtime_rule = self.config.policy.runtime_create_defaults.get(
            provider_key(device_ref, runtime), {}).get(project)
        if runtime_rule is not None:
            # A broad OAuth create scope is insufficient to request the
            # elevated/default mode of an independently managed account.
            if "create" not in self._operator_grant_verbs(principal, device_ref, runtime):
                raise refuse(
                    APPROVAL_REQUIRED,
                    "this caller has no create operator grant for the selected "
                    "runtime/project; project and OAuth scope alone do not "
                    "authorize another account",
                    device_ref=device_ref, runtime=runtime, project=project,
                )
            try:
                model, permission_mode = runtime_rule.resolve(
                    family=provider, model=model, permission_mode=permission_mode)
            except PolicyError as exc:
                raise refuse(
                    WRITE_NOT_GRANTED,
                    f"selected runtime/project denies the requested session defaults: {exc}",
                    device_ref=device_ref, runtime=runtime, project=project,
                ) from None
        self._may_name_mode_and_model(
            device_ref, project, grant, model=model, permission_mode=permission_mode
        )

        if idempotency_key:
            replayed = self._lookup_idempotent(
                principal, _VERB_CREATE, _create_scope(device_ref, project, runtime), idempotency_key
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
        # Composed with the RUNTIME, not with `provider`. The middle component of
        # a session ref is the runtime tag — `list_sessions` says so in as many
        # words (`row["runtime"] = summary.key.provider`) and every read, send
        # and control path resolves it that way
        # (`_require_provider(key.device_ref, key.provider)`). `provider` here is
        # the CloudCLI AGENT FAMILY, a different axis: one connector serves both.
        # Composing with the family gave a session minted in a second account the
        # base account's address, so the very next `read_session` on that ref
        # resolved to the account beside the one that created it — the same
        # device, a different credential, and nothing in the returned ref to say
        # so. For the default call the two spellings agree (`provider` defaults
        # to `claude`, which is `BASE_RUNTIME`), so this is byte-identical for
        # every caller that existed before a device could serve two.
        key = SessionKey.compose(device_ref, runtime, str(session_id))
        run_id = payload.get("runId") or payload.get("run_id")
        record = {
            "session": key.as_dict(),
            "run_id": run_id,
            "raw": payload,
        }
        if idempotency_key:
            self._remember_idempotent(
                principal, _VERB_CREATE, _create_scope(device_ref, project, runtime), idempotency_key, record
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
        provider = self._require_provider(key.device_ref, key.provider)
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

    def _runtime_for_call(self, device_ref: str, session_ref: str | None,
                          runtime: str | None) -> str:
        """Which runtime a call is addressed to, or a refusal.

        A `session_ref` already names one — `device:<id>|<runtime>|<native id>`
        — so a call that names one runtime in its reference and another on the
        call is refused rather than resolved in favour of either. It is the same
        mistake as a reference whose device disagrees with `device_ref`, and it
        is the one a caller makes when a session's UUID is the same under both
        runtimes: on this surface it never is, because the runtime is part of
        the address.
        """
        if not session_ref:
            return runtime or BASE_RUNTIME
        key = SessionKey.parse(str(session_ref))
        if runtime is not None and runtime != key.provider:
            raise refuse(
                BAD_SESSION_REF,
                f"`session_ref` names runtime {key.provider!r} and this call "
                f"names {runtime!r}. A session is addressed on the runtime that "
                f"holds it, and the two halves must agree — the same id under "
                f"two runtimes is two different sessions.",
            )
        return key.provider

    def native_call(
        self,
        principal: Principal,
        *,
        device_ref: str,
        native_tool: str,
        arguments: dict[str, Any] | None = None,
        session_ref: str | None = None,
        project: str | None = None,
        runtime: str | None = None,
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
        runtime = self._runtime_for_call(device_ref, session_ref, runtime)
        binding = self.config.provider_for(device_ref, runtime)
        if binding is None or not binding.enabled:
            raise refuse(
                UNKNOWN_DEVICE,
                f"{device_ref} is not an enabled provider for runtime "
                f"{runtime!r} on this runtime.",
            )
        # HIGH-6: the rate limiter every hand-written verb passes through. A
        # mirrored call is a call like any other and must not be a way around it.
        self.admit(principal)
        # LOW-14: `arguments` is passed to the device. A value that is not an
        # object is refused here rather than silently coerced (`dict(["ab"])`
        # turns a list into a mapping) or raising a Python-level error later.
        if arguments is not None and not isinstance(arguments, dict):
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}`'s arguments must be a JSON object; a "
                f"{type(arguments).__name__} was given.",
            )
        args = dict(arguments or {})
        # The class is decided ONCE, here, and BEFORE the scope gate, so a
        # `session_background` whose `stopTaskId` is present-but-empty is
        # refused as a bad request rather than screened as a listing — no read
        # scope can ever reach the stop path by way of a typo'd stop value.
        try:
            governance = cloudcli_native.governance_for(native, args)
        except cloudcli_native.NativeArgumentError as exc:
            raise refuse(BAD_REQUEST, str(exc)) from None
        # The scope the call needs follows the CLASS: a `session_background`
        # LIST is `fleet:read`, and only its STOP is `fleet:control`.
        for fleet_scope in cloudcli_native.fleet_scopes_for(native, governance):
            principal.require(fleet_scope)
        self._gate_native_write(principal, device_ref, binding, native, args,
                                session_ref=session_ref, project=project,
                                governance=governance, runtime=runtime)
        # HIGH-7: validate the caller's arguments against the tool's OWN schema
        # (recorded from CloudCLI 1.39.0; a live per-device schema may override).
        # A required parameter that is missing, or one the schema does not
        # declare, is refused rather than forwarded and silently ignored.
        problems = cloudcli_native.validate_arguments(
            args, cloudcli_native.input_schema(native_tool))
        if problems:
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}` arguments do not match the CloudCLI "
                f"schema: {'; '.join(problems)}.",
            )
        # HIGH-5: the read limit is the SAME bound the hand-written reader uses;
        # a mirrored read cannot ask for more than `fleet_read_session` may.
        if governance == cloudcli_native.GOV_READ:
            # Minimal disclosure for the approval register: a device-wide list
            # is other principals' pending decisions, so the list is SCOPED to
            # one named session and an unscoped read is refused.
            if native_tool == "approvals_list":
                key = self._native_session_key(device_ref, session_ref, native)
                args["session"] = key.native_session_id
            self._clamp_native_read(args)
        provider = self._require_provider(device_ref, runtime)
        caller = getattr(provider, "native_call", None)
        if caller is None:
            raise refuse(
                BAD_REQUEST,
                f"{device_ref}'s provider ({provider.kind}) does not inherit the "
                f"CloudCLI native surface.",
            )
        # OWNERSHIP before the answer: the requestId must be one THIS device
        # enumerates for the session named. This runs on the host, over a READ
        # (`approvals_list`), so an arbitrary string cannot be used to clear a
        # request that belongs to another session, another user or another
        # device. It fails closed: a device that will not enumerate refuses the
        # answer rather than accepting a string it cannot check.
        if native_tool == "approval_answer":
            self._verify_approval_ownership(provider, args, session_ref, device_ref)
        # HIGH-4 (stronger): a reconfigure that names a model or a permission
        # mode is checked against the project the SESSION actually belongs to,
        # read from the device — not the project the caller asserted.
        if governance == cloudcli_native.GOV_RECONFIGURE and (
                args.get("model") is not None or args.get("permissionMode") is not None):
            self._native_reconfigure_ceiling(provider, device_ref, session_ref,
                                             project, args, native)
        result = caller(native_tool, args, session_ref=session_ref)
        # HIGH-5: a mirrored READ carries the same "this is data, not
        # instruction" marking the hand-written reader attaches, so a consumer
        # cannot mistake a remote transcript for a local fact.
        if governance == cloudcli_native.GOV_READ and isinstance(result, dict):
            result.setdefault("untrusted_content", True)
            result.setdefault("untrusted_notice", UNTRUSTED_NOTICE)
        # The mirrored answer is MEASURED before it leaves. A runaway transcript
        # is refused with a typed error rather than relayed whole into the
        # ingress (and on to ChatGPT); the size is the one the wire would carry,
        # so a multi-byte transcript counts as its bytes, not its characters.
        self._refuse_oversized_result(result, native, device_ref)
        audit = getattr(self, "audit", None)
        if audit is not None:
            # The tool NAME is on the audit allowlist so the record says WHICH
            # verb ran; the arguments are not, so nothing a user typed or a
            # session held is written to the log.
            audit.record("mcp.native_call", principal=principal.name,
                         device_ref=device_ref, tool=native.name)
        return result

    def _refuse_oversized_result(self, result, native, device_ref: str) -> None:
        """Refuse a mirrored answer past the byte limit this surface relays.

        Measured on the exact payload that would be returned, encoded the way
        the wire carries it (UTF-8), so a page of multi-byte text is counted as
        its bytes. The refusal names the device, the verb and the measured size
        and carries NO remote text — the oversized body is measured and dropped,
        never copied into the error or the audit. It is a REFUSAL, never a
        truncation: a shortened transcript reads as a complete one.
        """
        try:
            blob = json.dumps(result, ensure_ascii=False, default=str).encode("utf-8")
        except (TypeError, ValueError):
            # A result that will not serialise cannot be measured, and an
            # unmeasurable answer is refused rather than sent on unmeasured.
            raise refuse(
                OUTPUT_TOO_LARGE,
                f"`{native.namespaced}` on {device_ref} returned a value this "
                f"surface cannot measure; an unmeasurable answer is refused "
                f"rather than relayed.",
                device_ref=device_ref, tool=native.name,
            ) from None
        if len(blob) > MAX_NATIVE_RESULT_BYTES:
            raise refuse(
                OUTPUT_TOO_LARGE,
                f"`{native.namespaced}` on {device_ref} returned {len(blob)} bytes, "
                f"over the {MAX_NATIVE_RESULT_BYTES}-byte limit this surface "
                f"relays. The answer is refused rather than truncated — a "
                f"shortened transcript reads as a complete one. Narrow the read "
                f"(a smaller `limit`, a `cursor`) to take it in pages.",
                device_ref=device_ref, tool=native.name,
                bytes=len(blob), limit=MAX_NATIVE_RESULT_BYTES,
            )

    def native_capability(self, principal: Principal, *, device_ref: str,
                          runtime: str = BASE_RUNTIME) -> dict[str, Any]:
        """A read-only snapshot of what ONE device exposes. Read-scoped.

        Reuses `health()` / `tools_list()` / `coverage` through the provider's
        `capability_snapshot`. It reads nothing a read did not already read,
        holds no credential of its own, and reports UNKNOWN where a route cannot
        enumerate — never a verb count without evidence, and never by reaching
        past a device's authentication.
        """
        principal.require(SCOPE_READ)
        # The rate limiter every hand-written read passes through, in the order
        # the rest of this class uses it. A capability snapshot reads the
        # device's `tools/list`, so on a `cloudcli-mcp-http` device it is an
        # outbound call like any other; exempting it would make this the one
        # read that can be hammered.
        self.admit(principal)
        binding = self.config.provider_for(device_ref, runtime)
        if binding is None or not binding.enabled:
            raise refuse(UNKNOWN_DEVICE,
                         f"{device_ref} is not an enabled provider for "
                         f"runtime {runtime!r} on this runtime.")
        provider = self._require_provider(device_ref, runtime)
        snapshot = getattr(provider, "capability_snapshot", None)
        if snapshot is None:
            return cloudcli_native.capability_snapshot(
                device_ref=device_ref, source=getattr(provider, "kind", None),
                reason="this provider kind exposes no capability snapshot")
        return snapshot()

    def _clamp_native_read(self, args: dict[str, Any]) -> None:
        """Cap a mirrored read's page size to the surface's own bound.

        `fleet_read_session` clamps `limit` to `policy.max_read_messages`; a
        mirrored `session_read`/`session_search` must not be a way to ask for
        everything at once. The cap is applied to the arguments SENT to the
        device, so the device is never handed an unbounded page.
        """
        if not isinstance(args.get("limit"), int):
            return
        bound = self.config.policy.max_read_messages
        if isinstance(bound, int) and args["limit"] > bound:
            args["limit"] = bound

    def _gate_native_write(self, principal: Principal, device_ref: str, binding,
                           native, arguments: dict[str, Any], *,
                           session_ref: str | None, project: str | None,
                           governance: str, runtime: str = BASE_RUNTIME) -> None:
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
        if governance == cloudcli_native.GOV_READ:
            return
        if governance in (cloudcli_native.GOV_SEND, cloudcli_native.GOV_CONTROL,
                          cloudcli_native.GOV_RECONFIGURE,
                          cloudcli_native.GOV_NAVIGATE):
            key = self._native_session_key(device_ref, session_ref, native)
            # BLOCKER-1: the session the floor passed on MUST be the session the
            # device is asked about. Without this, a `session_ref` for an
            # allowed session alongside `arguments.session` for another one
            # would be authorized as the first and delivered to the second.
            self._same_identity(arguments, "session", key.native_session_id, native)
            if governance in (cloudcli_native.GOV_SEND, cloudcli_native.GOV_NAVIGATE):
                # `session_send` and `ui_open_session` are both "may this caller
                # address THIS session on THIS device", so they share the one
                # per-session floor that already answers it. Navigate used to be
                # gated on the device alone — scope plus the `navigate` operator
                # grant — which on a shared company machine let a caller put ANY
                # session on that machine in front of whoever is sitting at it,
                # without being able to name that session in any per-session
                # list. It now takes the same outer `session_ref` every
                # session-addressed verb takes, must agree with the native
                # `arguments.session`, and is bounded by `send_allow` / the
                # `send` grant before the `navigate` grant is even consulted.
                self._may_send(principal, key)
            else:
                self._may_control(principal, key)
                self._require_operator_verb(principal, device_ref, "control", native,
                                            runtime=runtime)
                # The model/mode ceiling for a reconfigure is applied in
                # `native_call`, AFTER the provider is resolved, because it must
                # read the session's REAL project from the device.
            if governance == cloudcli_native.GOV_NAVIGATE:
                self._require_operator_verb(principal, device_ref, "navigate", native,
                                               runtime=runtime)
            return
        if governance == cloudcli_native.GOV_CREATE:
            project_name = self._native_project(project, native)
            # BLOCKER-1 for the project axis: the project the floor passed on is
            # the project that must be minted in.
            self._same_identity(arguments, "project", project_name, native)
            grant = self._may_create(device_ref, project_name, runtime)
            self._require_operator_verb(principal, device_ref, "create", native,
                                        project=project_name, runtime=runtime)
            # The CloudCLI *native mirror* must apply the SAME owner-approved
            # per-runtime defaults as fleet_create_session. Without this, the
            # caller could bypass the configured default by choosing the
            # mirrored session_create tool with omitted model/permissionMode.
            runtime_rule = self.config.policy.runtime_create_defaults.get(
                provider_key(device_ref, runtime), {}).get(project_name)
            if runtime_rule is not None:
                try:
                    resolved_model, resolved_mode = runtime_rule.resolve(
                        family=arguments.get("provider") or "claude",
                        model=arguments.get("model"),
                        permission_mode=arguments.get("permissionMode"))
                except PolicyError as exc:
                    raise refuse(
                        WRITE_NOT_GRANTED,
                        f"native session_create denied by selected runtime/project: {exc}",
                        device_ref=device_ref, runtime=runtime, project=project_name,
                    ) from None
                if resolved_model is not None:
                    arguments["model"] = resolved_model
                if resolved_mode is not None:
                    arguments["permissionMode"] = resolved_mode
            # HIGH-3: a caller may not name a model or permission mode the
            # project's grant does not allow — the same ceiling the hand-written
            # `fleet_create_session` applies, reused rather than restated.
            self._may_name_mode_and_model(
                device_ref, project_name, grant,
                model=arguments.get("model"),
                permission_mode=arguments.get("permissionMode"))
            return
        if governance == cloudcli_native.GOV_APPROVE:
            # An approval is answered for ONE request, on ONE device, in ONE
            # session. The session is named at the outer layer so the request's
            # ownership can be ENUMERATED on that device (see
            # `_verify_approval_ownership`); a bare `requestId` string is not an
            # address and is never enough to answer one.
            key = self._native_session_key(device_ref, session_ref, native)
            got = arguments.get("session")
            if got is not None:
                self._same_identity(arguments, "session", key.native_session_id, native)
            # The SESSION floor, before the device-level `approve` grant is even
            # read. Without it the device arm was the whole check: a principal
            # holding `approve` for a machine could answer an approval raised in
            # ANY session on it, which on a shared company workstation means
            # answering someone else's request with a `allow`/`deny` the owner of
            # that session never gave. It reuses `_may_send` because the question
            # is the same one — "may this caller address this session on this
            # device" — and answering a request and sending a message are both
            # addressed to a session rather than to a device.
            self._may_send(principal, key)
            self._native_approval(arguments, native)
            self._require_operator_verb(principal, device_ref, "approve", native,
                                            runtime=runtime)
            return
        if governance == cloudcli_native.GOV_NAVIGATE:
            self._require_operator_verb(principal, device_ref, "navigate", native)
            return
        raise refuse(
            WRITE_NOT_GRANTED,
            f"`{native.namespaced}` is a {governance} verb with no implemented "
            f"Fleet floor; it must be refused at the scope gate, and reaching "
            f"this branch is a defect in `cloudcli_native`'s governance map.",
        )

    def _same_identity(self, arguments: dict[str, Any], field: str,
                       expected: str, native) -> None:
        """The address the floor passed on is the address forwarded.

        The floor (`_may_send` / `_may_control` / `_may_create`) is stated over
        a Fleet address given at the OUTER layer; the native tool names its own
        target inside `arguments`. If the two disagree, the call is authorized
        as one and delivered to another, which is the whole of BLOCKER-1. Both
        are therefore required, and they must match exactly.
        """
        got = arguments.get(field)
        if not isinstance(got, str) or not got.strip():
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}` must name its own `{field}` inside "
                f"`arguments` — the same one the floor was checked on — and this "
                f"call named none. The surface will not fill it in from the "
                f"outer address, because a call that addresses one thing and is "
                f"checked against another is how the floor is bypassed.",
            )
        if got.strip() != expected:
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}`: the `{field}` the floor was checked on "
                f"and the `{field}` being forwarded differ. A call authorized "
                f"against one target and delivered to another is refused.",
                field=field,
            )

    def _native_reconfigure_ceiling(self, provider, device_ref: str,
                                    session_ref: str | None, project: str | None,
                                    arguments: dict[str, Any], native) -> None:
        """A reconfigure's model/mode is bounded by the SESSION's REAL project.

        The caller's `project` is a claim. The project that decides the ceiling
        is the one the target session actually belongs to, read from the device
        (`session_get` — a metadata read, never a transcript). The two must
        agree, and the ceiling is applied against the DEVICE-stated project.
        A caller cannot widen its own authority by declaring a laxer project,
        and a session whose project cannot be confirmed is refused rather than
        guessed. `session_get` is itself a read the current authorization
        already permits, so no new reach is added to establish this.
        """
        mode = arguments.get("permissionMode")
        model = arguments.get("model")
        if not isinstance(project, str) or not project.strip():
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}` names a `permissionMode` or `model`, so it "
                f"must also name `project` — the ceiling is the project's own "
                f"grant, and with no project to check it against it is refused.",
            )
        declared = project.strip()
        try:
            key = SessionKey.parse(str(session_ref))
        except Exception:  # noqa: BLE001 - any parse failure is one refusal
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}` must name `session_ref` so the session's "
                f"real project can be read; a bare session id is not an address.",
            ) from None
        caller = getattr(provider, "native_call", None)
        if caller is None:
            raise refuse(
                APPROVAL_REQUIRED,
                f"`{native.namespaced}` cannot confirm which project the session "
                f"belongs to (this provider does not report it), so a permission "
                f"change is refused rather than trusted to the caller's claim.",
            )
        try:
            detail = caller("session_get", {"session": key.native_session_id})
        except FleetRefusal as exc:
            raise refuse(
                APPROVAL_REQUIRED,
                f"`{native.namespaced}` could not confirm the session's project: "
                f"the device would not report it ({exc.code}). A permission "
                f"change with no confirmed project is refused.",
                device_ref=device_ref,
            ) from None
        actual = _session_project(detail)
        if actual is None:
            raise refuse(
                APPROVAL_REQUIRED,
                f"`{native.namespaced}` could not confirm the session's project: "
                f"the device reported no project id, so the ceiling cannot be "
                f"checked and the change is refused.",
                device_ref=device_ref,
            )
        if actual != declared:
            raise refuse(
                WRITE_NOT_GRANTED,
                f"`{native.namespaced}`: the session belongs to project {actual!r} "
                f"on the device, not the {declared!r} the caller named. A "
                f"permission change is checked against the session's OWN project, "
                f"never against a project the caller chose.",
                device_ref=device_ref, device_project=actual, declared=declared,
            )
        grant = self.config.policy.create_allow.get(actual)
        if grant is None:
            raise refuse(
                WRITE_NOT_GRANTED,
                f"`{native.namespaced}` cannot change {actual!r}'s model or "
                f"permission mode: the project is not in `policy.create_allow`, so "
                f"no ceiling exists to allow `{mode or model}`.",
                device_ref=device_ref, project=actual,
            )
        self._may_name_mode_and_model(device_ref, actual, grant,
                                      model=model, permission_mode=mode)
        # Reconfigure must not bypass the narrower per-credential-instance
        # ceiling after a legitimate create. Do NOT fill omitted fields here:
        # a reconfigure that names only model must not reset permissionMode.
        runtime_rule = self.config.policy.runtime_create_defaults.get(
            provider_key(device_ref, key.provider), {}).get(actual)
        if runtime_rule is not None:
            if model is not None and model not in runtime_rule.allowed_models:
                raise refuse(
                    WRITE_NOT_GRANTED,
                    "session_reconfigure model is not authorized for this account/project",
                    device_ref=device_ref, runtime=key.provider, project=actual,
                )
            if mode is not None and mode not in runtime_rule.allowed_permission_modes:
                raise refuse(
                    WRITE_NOT_GRANTED,
                    "session_reconfigure permissionMode is not authorized for this account/project",
                    device_ref=device_ref, runtime=key.provider, project=actual,
                )

    def _native_approval(self, arguments: dict[str, Any], native) -> None:
        """An approval answer must name the request AND the user's choice.

        `approval_answer` is the one mirrored verb whose whole meaning is a
        person's decision, so its Fleet floor refuses two absences: no
        `requestId` (an answer to nothing is not an answer) and no boolean
        `allow` (this surface never chooses for the user — it carries a choice
        the user made). This is the FLEET half only; the DEVICE's native control
        plane re-confirms the requestId belongs to it, and this surface never
        answers on the user's behalf.
        """
        request_id = arguments.get("requestId")
        if not isinstance(request_id, str) or not request_id.strip():
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}` answers ONE approval request and this "
                f"call named no `requestId`. An answer to nothing is not an "
                f"answer, and this surface will not pick a request to answer.",
            )
        if not isinstance(arguments.get("allow"), bool):
            raise refuse(
                BAD_REQUEST,
                f"`{native.namespaced}` requires `allow` (a boolean) — the "
                f"user's decision. This surface never chooses for the user; it "
                f"carries a choice they made.",
            )

    def _verify_approval_ownership(self, provider, args: dict[str, Any],
                                   session_ref: str | None, device_ref: str) -> None:
        """The `requestId` must be one THIS device enumerates, for a named session.

        CloudCLI's `approvals_list` is the device's own register. Asking it,
        scoped to the session, is what turns «this request belongs here» from a
        string the caller asserted into a fact the device stated. It fails
        CLOSED at every step: no session named, a device that will not
        enumerate, or an answer whose ids cannot be read all refuse the answer
        rather than accepting an id this surface cannot locate. An arbitrary
        string can therefore never clear another session's, user's or device's
        approval.
        """
        request_id = args["requestId"]
        try:
            key = SessionKey.parse(str(session_ref))
        except Exception:  # noqa: BLE001 - any parse failure is one refusal
            raise refuse(
                BAD_REQUEST,
                "`approval_answer` must name `session_ref` so the request can be "
                "located on the device; a bare `requestId` is not an address and "
                "is never enough to answer one.",
            ) from None
        caller = getattr(provider, "native_call", None)
        if caller is None:
            raise refuse(
                APPROVAL_REQUIRED,
                f"`approval_answer` cannot confirm that request {request_id!r} "
                f"belongs to {device_ref}: this provider does not enumerate "
                f"approvals, so ownership cannot be established and the answer "
                f"is refused.",
            )
        try:
            listing = caller("approvals_list", {"session": key.native_session_id})
        except FleetRefusal as exc:
            raise refuse(
                APPROVAL_REQUIRED,
                f"`approval_answer` could not confirm that request {request_id!r} "
                f"belongs to {device_ref}: the device would not enumerate its "
                f"approvals ({exc.code}). A request this surface cannot locate is "
                f"not answered.",
                device_ref=device_ref,
            ) from None
        enumerated = _approval_ids(listing)
        if request_id not in enumerated:
            raise refuse(
                APPROVAL_REQUIRED,
                f"request {request_id!r} is not one this device enumerates for the "
                f"named session. An approval is answered only for a request that "
                f"belongs to it, never by naming an id that appears nowhere.",
                device_ref=device_ref, enumerated=len(enumerated),
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
                               verb: str, native, *, project: str | None = None,
                               runtime: str = BASE_RUNTIME) -> None:
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
        verbs = self._operator_grant_verbs(principal, device_ref, runtime)
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
