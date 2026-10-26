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

import datetime as dt
import json
import os
import threading
import time
from dataclasses import dataclass
from functools import partial
from typing import Any

from .audit import AuditLog, RateLimiter
from .dispatch import DispatchGuard, delivery_receipt
from .discovery import DiscoveryPool
from . import cloudcli_native, core_lite_source, runtime_identity
from .config import (CreateGrant, FleetConfig, OPERATOR_GRANT_VERBS,
                     provider_key)
from .runtime_identity import BASE_RUNTIME
from .runtime_create_policy import PolicyError
from .errors import (
    APPROVAL_REQUIRED,
    BAD_REQUEST,
    BAD_SESSION_REF,
    FleetRefusal,
    IDEMPOTENT_REPLAY,
    DISPATCH_OUTCOME_UNKNOWN,
    INTERNAL,
    PROJECT_NOT_FOUND,
    RATE_LIMITED,
    SCOPE_DENIED,
    UNKNOWN_DEVICE,
    WRITE_NOT_GRANTED,
    OUTPUT_TOO_LARGE,
    refuse,
)
from .identity import SessionKey
from .owner_access import build_project_path, OwnerAccessError
from .version import SERVER_VERSION
from .session_detail import normalize_detail
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


@dataclass(frozen=True)
class _AuthoredVerb:
    """The label `_require_operator_verb` reports for a tool with no native row.

    `fleet_delete_project` reaches CloudCLI's `project_delete`, which
    `cloudcli_native` records but deliberately does not MIRROR (see
    `EXTENSION_CLAIMED_NATIVE_TOOLS`), so there is no `NativeTool` object to hand
    to the grant check. All that check needs from it is the namespaced name it
    puts in its refusal — the caller has to be told WHICH verb was not granted.
    """

    namespaced: str


class FleetService:
    def __init__(self, config: FleetConfig, *, registry=None, providers=None,
                 core_lite_source=None, attempts=None, events=None) -> None:
        self.config = config
        self.registry = registry if registry is not None else load_registry(config.registry_path)
        self.providers: dict[str, DeviceProvider] = (
            providers if providers is not None else build_providers(config)
        )
        #: A provider-side Core-Lite reader, injected. `None` means no device
        #: here has one, which is the truthful default: no live Node exposes a
        #: trusted Core API yet, so every reading this surface could otherwise
        #: show would be one it made up. See `core_lite_status()`.
        self.core_lite_source = core_lite_source
        self.audit = AuditLog(config.audit)
        self.limiter = RateLimiter(config.audit.rate_limit_per_minute) if config.audit else None
        self.dispatch = DispatchGuard(attempts)
        self.events = events
        self.run_watcher = None
        self.discovery = DiscoveryPool(config.policy.discovery_workers)

    # -- helpers -----------------------------------------------------------

    def _owner_namespaces(self, principal: Principal) -> tuple[str, ...]:
        """The namespaces `policy.owner_max` gives THIS caller.

        `"*"` is the principal-axis fallback, consulted only when the caller has
        no entry of its own — an exact entry says what it holds, including when
        that is a namespace list this device is not in. The namespace side is
        never a wildcard. One reader, so `fleet_status` and the create gate
        cannot answer "is this an owner" differently.
        """
        by_principal = self.config.policy.owner_max
        return (by_principal[principal.name] if principal.name in by_principal
                else by_principal.get("*", ()))

    def _owner_profile(self, principal: Principal, device_ref: str, namespace: str):
        selected = self._owner_namespaces(principal)
        if provider_key(device_ref, namespace) not in selected:
            return None
        binding = self.config.namespace_binding(device_ref, namespace)
        if (binding is None or not binding.enabled
                or not self._device_is_grant_eligible(device_ref)):
            return None
        return binding.owner_access

    def _project_root_profile(self, principal: Principal, device_ref: str,
                              namespace: str):
        """The roots this caller may MINT a project below, and why.

        Two admissions, deliberately different in what they carry:

        *   `("owner-max", profile)` — the configured owner. Minting and the
            `bypassPermissions` default for sessions in the new project are the
            same authority, and that authority is why owner-max requires a
            durable dispatch ledger the standalone HTTP surface does not have.
        *   `("operator-grant", profile)` — a caller holding the `create` verb on
            a device the operator gave roots for. It carries the ROOTS only. It
            is not owner-max: `_owner_project_grant` still asks owner-max, so a
            session minted under this admission takes the normal create policy
            and may not name `bypassPermissions` or a model. Creation is not
            smuggled in as a way to widen one's own session authority.

        Everything else — no binding, no profile, a personal device, or no
        `create` verb — is `None`, which the caller answers with
        APPROVAL_REQUIRED. An empty `owner_max` and an absent `owner_access`
        block therefore both mean refusal, which is the default this deployment
        ships.
        """
        owner = self._owner_profile(principal, device_ref, namespace)
        if owner is not None:
            return owner, "owner-max"
        binding = self.config.namespace_binding(device_ref, namespace)
        if (binding is None or not binding.enabled or binding.owner_access is None
                or not self._device_is_grant_eligible(device_ref)):
            return None
        if "create" not in self._operator_grant_verbs(principal, device_ref, namespace):
            return None
        return binding.owner_access, "operator-grant"

    def owner_access_report(self, principal: Principal) -> list[dict[str, Any]]:
        """What `fleet_status.owner_access` shows: where THIS caller may mint.

        `permission_mode` is reported only where it is actually in force. An
        `operator-grant` admission carries the roots and nothing else, so it
        reports `None` rather than repeating the profile's `bypassPermissions`
        — a status that named a mode the gate would refuse is worse than no
        status at all.
        """
        report = []
        for key in sorted(self.config.providers):
            binding = self.config.providers[key]
            admitted = self._project_root_profile(principal, binding.device_ref,
                                                  binding.runtime)
            if admitted is None:
                continue
            profile, how = admitted
            report.append({
                "device_ref": binding.device_ref,
                "runtime": binding.runtime,
                "root_refs": list(profile.project_roots),
                "permission_mode": profile.permission_mode if how == "owner-max" else None,
                "admitted_by": how,
            })
        return report

    @staticmethod
    def _create_namespace(binding, family):
        return family if binding.multiplex_native_families else binding.runtime

    def _owner_project_grant(self, principal, device_ref, project, runtime, namespace):
        profile = self._owner_profile(principal, device_ref, namespace)
        if profile is None:
            return None
        existing = self.config.policy.create_allow.get(project)
        rule = self._project_runtime_rule(principal, device_ref, project, runtime, namespace)
        binding = self.config.provider_for(device_ref, runtime)
        alias = binding is not None and binding.multiplex_native_families and namespace != runtime
        models = rule.allowed_models if rule is not None else (
            existing.models if existing is not None and not alias else ())
        return CreateGrant(permission_modes=(profile.permission_mode,), models=tuple(models))

    def _project_runtime_rule(self, principal, device_ref, project, runtime, namespace):
        binding = self.config.provider_for(device_ref, runtime)
        policy_runtime = runtime
        if (self._owner_profile(principal, device_ref, namespace) is not None
                and binding is not None and binding.multiplex_native_families):
            policy_runtime = namespace
        return self.config.policy.runtime_create_defaults.get(
            provider_key(device_ref, policy_runtime), {}).get(project)

    def _resolve_create_policy(self, principal, device_ref, project, runtime,
                               namespace, family, model, permission_mode):
        grant = self._may_create(device_ref, project, runtime, principal=principal,
                                 namespace=namespace)
        profile = self._owner_profile(principal, device_ref, namespace)
        binding = self.config.provider_for(device_ref, runtime)
        if family not in runtime_identity.FAMILIES:
            raise refuse(BAD_REQUEST, "provider must name the Claude or Codex native family")
        if profile is not None and not binding.multiplex_native_families and family != binding.family:
            raise refuse(WRITE_NOT_GRANTED, "native family differs from the selected account profile")
        rule = self._project_runtime_rule(principal, device_ref, project, runtime, namespace)
        by = None
        if rule is not None:
            if "create" not in self._operator_grant_verbs(principal, device_ref, namespace):
                raise refuse(APPROVAL_REQUIRED, "selected runtime/project requires a create operator grant")
            try:
                model, resolved_mode = rule.resolve(
                    family=family, model=model,
                    permission_mode=None if profile is not None else permission_mode)
                if profile is None:
                    permission_mode = resolved_mode
                by = "runtime/project policy"
            except PolicyError as exc:
                raise refuse(WRITE_NOT_GRANTED, f"selected runtime/project denies session defaults: {exc}") from None
        if profile is not None:
            permission_mode = profile.permission_mode if permission_mode is None else permission_mode
            by = "owner-max profile"
        self._may_name_mode_and_model(device_ref, project, grant,
                                      model=model, permission_mode=permission_mode)
        return model, permission_mode, by

    @staticmethod
    def _project_row(rows, project_id: str) -> dict[str, Any] | None:
        """The catalogue row whose id is exactly `project_id`, or `None`.

        A row is matched only when every id field it carries agrees with the
        others AND with the id asked for: a row that reports two different ids
        is ambiguous, and picking one of them would be this surface guessing
        which project to act on.
        """
        for row in rows:
            if not isinstance(row, dict):
                continue
            ids = [row[k] for k in ("projectId", "project_id", "id")
                   if isinstance(row.get(k), str) and row[k]]
            if ids and len(set(ids)) == 1 and ids[0] == project_id:
                return row
        return None

    @staticmethod
    def _project_catalogue_row(provider, project_id: str, *,
                               include_archived: bool) -> dict[str, Any] | None:
        """One project row, read from the DEVICE's own catalogue.

        `include_archived` is explicit at every call site because the two
        questions differ: a create verifies against the ACTIVE catalogue (a
        session cannot be minted in an archived project), while a delete's
        pre-read must see archived rows too, or the row a permanent delete most
        often targets would be invisible. An unreadable catalogue is a refusal
        rather than an empty one — acting on "it is not there" when the question
        was never answered is exactly the blind delete this read exists to
        prevent.
        """
        caller = getattr(provider, "native_call", None)
        if caller is None:
            raise refuse(WRITE_NOT_GRANTED, "provider cannot verify its current project catalogue")
        listing = caller("projects_list", {"includeArchived": bool(include_archived)})
        rows = listing.get("projects") if isinstance(listing, dict) else listing
        if rows is None and isinstance(listing, dict) and isinstance(listing.get("data"), dict):
            rows = listing["data"].get("projects")
        if not isinstance(rows, list):
            raise refuse(WRITE_NOT_GRANTED, "provider did not return a current project catalogue")
        return FleetService._project_row(rows, project_id)

    @staticmethod
    def _verify_owner_project(provider, project):
        row = FleetService._project_catalogue_row(provider, project,
                                                  include_archived=False)
        if row is not None and not (row.get("isArchived") is True
                                    or row.get("is_archived") is True):
            return
        raise refuse(WRITE_NOT_GRANTED, "project does not exist in the selected runtime's active catalogue")

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

    def _session_provider(self, key: SessionKey) -> DeviceProvider:
        binding = self.config.session_binding(key)
        if binding is None:
            raise refuse(UNKNOWN_DEVICE, "the session namespace has no configured binding")
        if not binding.enabled:
            raise refuse(UNKNOWN_DEVICE, "the addressed session binding is disabled")
        return self._require_provider(key.device_ref, binding.runtime)

    def _verified_family_detail(self, key: SessionKey, provider, detail=None):
        binding = self.config.session_binding(key)
        if binding is None or not binding.multiplex_native_families:
            return None
        if detail is None:
            detail = provider.get_session(key)
        detail = normalize_detail(key, detail, binding)
        native = detail.get("session")
        if (not isinstance(native, dict) or "session_ref" in native
                or native.get("provider") != key.provider
                or not any(name in native for name in
                           ("id", "sessionId", "session_id", "native_session_id"))):
            raise refuse(BAD_SESSION_REF, "native session family or identity is unverified")
        return detail

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

    def _declared_grant_verbs(self, principal_name: str, device_ref: str,
                              runtime: str = BASE_RUNTIME) -> tuple[str, ...]:
        """What the binding *declares* for this principal on this device.

        Eligibility is deliberately not applied here. This answers "did the
        operator write a grant that covers this", which is the question both
        `_may_send` and `_may_control` need in order to tell a missing grant
        apart from a grant written against a device it cannot reach. Applying
        eligibility here would erase exactly the distinction the refusal exists
        to draw, and the caller applies it where it belongs.
        """
        grants = self.config.policy.operator_grants
        # An entry for this principal is authoritative even when it is empty:
        # `{}` means "this principal holds nothing", and falling through to the
        # wildcard would turn an explicit deny into a grant. Only an ABSENT
        # principal entry consults `"*"`.
        per_device = grants[principal_name] if principal_name in grants else grants.get("*", {})
        if not per_device:
            return ()
        # Looked up under the SAME key a provider binding uses: the base runtime
        # keeps the bare `device:<id>` key, so a grant written before a device
        # could serve two means exactly what it meant then, and a SECOND runtime
        # needs its own entry. That is the "independent owner consent" a new
        # runtime requires — it is not inherited from the runtime beside it.
        #
        # `"*"` is consulted only when no exact device entry exists, for the
        # same reason: an exact entry says what this device holds, including
        # when what it holds is an empty list.
        key = provider_key(device_ref, runtime)
        verbs = per_device[key] if key in per_device else per_device.get("*")
        if not verbs:
            return ()
        return tuple(verbs)

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

        On the ownership test: a grant naming a device whose owner class the
        binding does not admit is not *narrowed* here, it is inert, and the
        refusal says so. The eligible classes come from
        `policy.grant_eligible_ownership`, whose default is every ownership the
        registry can author; a binding narrows it by naming fewer. What stays in
        code is the distinction between "not in the list" and "no owner at
        all": `ownership` is `str | None`, and an unset owner is an unknown
        owner, which is DEFER on this host rather than an authorization.
        """
        if self._owner_profile(principal, device_ref, runtime) is not None:
            return tuple(OPERATOR_GRANT_VERBS)
        verbs = self._declared_grant_verbs(principal.name, device_ref, runtime)
        if not verbs:
            return ()
        if not self._device_is_grant_eligible(device_ref):
            return ()
        return tuple(v for v in verbs if v in OPERATOR_GRANT_VERBS)

    def _device_is_grant_eligible(self, device_ref: str) -> bool:
        """Whether a device may appear in an operator grant at all.

        Read from `policy.grant_eligible_ownership`, never from a constant in
        this file: which ownership classes an operator drives is their decision
        about their own fleet, and the binding is where they express it. The
        default there is every ownership the registry can author.

        An ownership the registry does not state is still not eligible, because
        it is not a value the list can hold — see `KNOWN_DEVICE_OWNERSHIPS`.
        """
        ownership = self._device_ownership(device_ref)
        return ownership is not None and ownership in self._eligible_ownerships()

    def _eligible_ownerships(self) -> tuple[str, ...]:
        return self.config.policy.grant_eligible_ownership

    def _may_send(self, principal: Principal, key: SessionKey) -> None:
        binding = self.config.session_binding(key)
        if binding is None:
            # No binding is not "not authorized", it is "not reachable" — the
            # distinction `_require_provider` draws in its own words, and the one
            # `_may_create` already defers to. Asking it here also keeps the two
            # answers apart: "there is a route here and you were not given it"
            # tells the caller the profile is real, which a caller probing a tag
            # is not necessarily entitled to learn, while `UNKNOWN_DEVICE` says
            # nothing about whether this tag was ever bound.
            self._session_provider(key)
        if not binding.enabled:
            raise refuse(UNKNOWN_DEVICE, "the addressed session binding is disabled")
        if not binding.allow_send and self._owner_profile(principal, key.device_ref, key.provider) is None:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{key.device_ref} is not authorized to send on this runtime. "
                f"Sending is granted per device in the local binding "
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
            granted_here = self._declared_grant_verbs(
                principal.name, key.device_ref, key.provider)
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
                scope = self.config.policy.operator_grants
                who = principal.name if principal.name in scope else "*"
                per_device = scope.get(who, {})
                named = provider_key(key.device_ref, key.provider)
                what = named if named in per_device else "*"
                detail = (
                    f"`policy.operator_grants[{who!r}][{what!r}]` "
                    f"does name {list(granted_here)}, but {key.device_ref} is "
                    f"{ownership!r} and `policy.grant_eligible_ownership` is "
                    f"{list(self._eligible_ownerships())}. The grant is inert here rather than "
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
        binding = self.config.session_binding(key)
        if binding is None:
            # The same line `_may_send` draws: a runtime with no binding has no
            # route, which is unreachable rather than unauthorized, and an
            # answer that named a withheld grant would confirm the profile for a
            # caller who may not be entitled to know it is there.
            self._session_provider(key)
        if not binding.enabled:
            raise refuse(UNKNOWN_DEVICE, "the addressed session binding is disabled")
        if not binding.allow_control and self._owner_profile(principal, key.device_ref, key.provider) is None:
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
            granted_here = self._declared_grant_verbs(
                principal.name, key.device_ref, key.provider)
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

    def _may_control_device(self, principal: Principal, device_ref: str, binding,
                            native, *, runtime: str) -> None:
        """The DEVICE half of control, for a target that is not a session.

        `_may_control` is stated over a `SessionKey` because stopping a run is
        about one session. Deleting a project is about a project ROW: there is no
        session to name, so `policy.control_allow` — a per-session list — has no
        referent here, while the other two gates apply unchanged:

        *   the device's `allow_control` (or a selected owner-max profile — the
            same substitution `_may_control` makes), and
        *   the caller's `control` operator grant on that device.

        A caller who may not stop a run on a machine may not remove project rows
        on it either, and this is deliberately NOT routed through `allow_create`:
        removal is not minting, and a device trusted to create is not thereby
        trusted to destroy.
        """
        if not binding.allow_control and self._owner_profile(principal, device_ref, runtime) is None:
            raise refuse(
                APPROVAL_REQUIRED,
                f"{device_ref} is not authorized for control from this runtime. "
                f"Removing a project row is granted per device in the local "
                f"binding (`allow_control: true`) and per caller by the "
                f"`control` operator grant — never by naming a project id.",
                device_ref=device_ref,
                required_verb="control",
            )
        self._require_operator_verb(principal, device_ref, "control", native,
                                    runtime=runtime)

    def _may_create(self, device_ref: str, project: str,
                    runtime: str = BASE_RUNTIME, *, principal=None,
                    namespace=None) -> CreateGrant:
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
        owner_grant = (self._owner_project_grant(principal, device_ref, project, runtime,
                                                namespace or runtime)
                       if principal is not None else None)
        if binding is not None and not binding.enabled:
            raise refuse(UNKNOWN_DEVICE, "the selected creation binding is disabled")
        if owner_grant is not None:
            return owner_grant
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
            wildcard = self._configured_create_grant("*")
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

    def _configured_create_grant(self, project: str):
        """The binding's create grant for one project, wildcard included.

        ONE reading of `policy.create_allow`, used by every ceiling that asks
        what a project admits. It exists because there were two: session
        creation honoured `create_allow["*"]` when `allow_wildcard_create` was
        on, while the reconfigure ceiling read `create_allow.get(project)` and
        so saw nothing for a project covered only by the wildcard — a session
        could be MINTED under a grant it was then unable to be RECONFIGURED
        under, with the sweep reading as a refusal that no configuration could
        lift. `"*"` passes through unharmed: it is a real key, and the caller
        asking for it is asking for the wildcard entry itself.
        """
        grants = self.config.policy.create_allow
        if project != "*" and grants.get(project) is not None:
            return grants[project]
        if self.config.policy.allow_wildcard_create:
            return grants.get("*")
        return None

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

    def core_lite_status(self, principal: Principal, device_ref: str,
                         *, received_at=None) -> dict[str, Any]:
        """This device's Core-Lite reading, or why there is not one to show.

        **Not an MCP tool.** Nothing in `tools.py` names it, `list_devices` and
        `status` do not call it, and no route is declared for it — so the served
        surface is unchanged by this seam: ten hand-written tools, the 22
        inherited verbs and the two capability reads, six scopes,
        `TOOL_SURFACE_REVISION = "fleet-mcp-7"`.
        Adding a field to `fleet_list_devices` would be a change to a tool's
        contract shape, and that decision belongs in the change that makes it,
        openly, rather than inside a wiring commit.

        What it does add is the seam: a place a provider adapter can be plugged
        into, and one place that decides whether what came back may be
        attributed to this device at all.

        The device reference is an ARGUMENT and the account comes from the
        binding — neither is read from the document. The route evidence comes
        from the source. `ProviderHealth` contributes nothing here, on purpose:
        a provider whose transport answers is not thereby a device whose Core
        answered.
        """
        principal.require(SCOPE_READ)
        # ONE implementation, shared with the two tools that embed this. Two
        # would be two answers to "whose reading is this", and the one that is
        # asked least often is the one that drifts.
        summary = self._core_lite_summary(device_ref, received_at=received_at)
        # `device_ref` and `decision` are the names the disclosure table already
        # carries a kind for (REF and CODE). A fresh pair of names here would
        # have been silently WITHHELD from the audit record — which is what
        # `test_fleet_mcp_disclosure` caught — and adding them to the table
        # would be a disclosure-policy change smuggled in under a wiring commit.
        self.audit.record("fleet.core_lite_status", principal=principal.name,
                          device_ref=device_ref,
                          decision=summary.get("route", {}).get("state"))
        return summary

    def _core_lite_summary(self, device_ref: str, runtime: str = BASE_RUNTIME,
                           *, received_at=None) -> dict[str, Any]:
        """`core_lite_status`'s body, without the scope check or the audit row.

        Split out because `list_devices` and `status` embed one of these PER
        DEVICE: the scope is already checked once by the tool that called them,
        and an audit row per device per read would turn one authorized read into
        a burst of records that say the same thing.

        Which source answers is decided HERE, per device, and that is the part
        that matters: a device reached over the node channel has an
        authenticated route and its provider hands back a source; a device
        reached any other way has none, and gets `NOT_CONFIGURED` rather than
        borrowing another device's route. `self.core_lite_source`, when injected,
        is the fallback a test uses — never a licence for one device's answer to
        be published under another's name, because `admit_core_lite` still
        compares the route's device with the one asked about.
        """
        binding = self.config.provider_for(device_ref, runtime)
        account = binding.account if binding is not None else None
        if received_at is None:
            received_at = dt.datetime.now(dt.timezone.utc)
        source = None
        provider = self._provider(device_ref, runtime)
        getter = getattr(provider, "core_lite_source", None) if provider is not None else None
        if callable(getter):
            source = getter()
        if source is None:
            source = self.core_lite_source
        summary = core_lite_source.admit_core_lite(
            source,
            device_ref=device_ref,
            binding_account=account,
            received_at=received_at,
        )
        # WHICH account's Core this is, carried beside the reading rather than
        # assumed from the device. One machine can run several OS accounts and
        # each has its own Core; a summary that named only the device would let a
        # second account's readings be read as the machine's one answer, which is
        # the cross-account substitution the runtime axis exists to prevent.
        # `runtime` is what was ASKED FOR, `account` what the binding DECLARES —
        # the same requested/declared split the document already carries for
        # identity, and neither is inferred from the other.
        summary["runtime"] = runtime
        summary["account"] = account
        summary["source"] = ("node-core-route" if provider is not None
                             and callable(getattr(provider, "core_lite_source", None))
                             else "injected" if source is not None else None)
        return summary

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
                    # The Core reading, next to — and NOT derived from — the
                    # provider's health. A connector whose transport answers is
                    # not thereby a machine whose Core reported, and the two are
                    # kept as separate keys so no reader can plug one into the
                    # other's meaning. A device with no trusted Core route says
                    # `NOT_CONFIGURED` here while its `provider` block is
                    # perfectly reachable, which is the whole point of the split.
                    # A LIST, one entry per BOUND runtime — the same shape
                    # `fleet_status` carries, because it is the same fact and
                    # two shapes for one fact is how a reader picks the wrong
                    # one. This was a single summary over the base runtime while
                    # `status` already reported per runtime, so a machine
                    # serving two accounts showed one Core here and two there.
                    "core_lite": [self._core_lite_summary(device.device_ref.ref, runtime)
                                  for runtime in (self._bound_runtimes(device.device_ref.ref)
                                                  or (BASE_RUNTIME,))],
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
        # A value the caller DID send, which names no runtime, is a bad request
        # and never the device's own. The distinction is the point: `None` is
        # the absence of a name and means the device's own runtime, while `""`
        # and `"   "` are names that match nothing — and resolving either of
        # them with `runtime or BASE_RUNTIME` silently routed the call to the
        # base account, which is the one outcome this axis exists to prevent.
        # Whitespace is refused here rather than as `UNKNOWN_DEVICE`, because
        # that code tells an operator a binding is missing when what is missing
        # is a name.
        if not isinstance(runtime, str) or not runtime.strip():
            raise refuse(
                BAD_REQUEST,
                f"{runtime!r} is not a runtime name: a runtime is "
                f"`{BASE_RUNTIME}` or `{BASE_RUNTIME}-<account>`, and a blank or "
                f"non-string value names none. Omit the key — or pass null — to "
                f"use the device's own runtime.",
            )
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
        selections = {entry.device_ref.ref: self._runtimes_for(entry.device_ref.ref, runtime)
                      for entry in targets}
        jobs = {}
        for entry in targets:
            ref = entry.device_ref.ref
            for one in selections[ref]:
                provider = self._provider(ref, one)
                if provider is not None:
                    where = provider_key(ref, one)
                    token = (principal.credential_kind, principal.name, where, limit, project, state)
                    jobs[where] = (token, partial(provider.list_sessions,
                                                 limit=limit, project=project, state=state))
        outcomes = self.discovery.collect(
            jobs, timeout=self.config.policy.discovery_timeout_seconds)
        for entry in targets:
            ref = entry.device_ref.ref
            selected = selections[ref]
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
                    found = outcomes[where]
                    if isinstance(found, Exception):
                        raise found
                    from .session_detail import validate_binding_target
                    selected_binding = self.config.provider_for(ref, one)
                    for summary in found:
                        if summary.key.device_ref != ref:
                            raise refuse(BAD_SESSION_REF, "listing names another device")
                        validate_binding_target(summary.key, selected_binding, observation=True)
                        if (one == BASE_RUNTIME and summary.key.provider == "codex"
                                and self.config.provider_for(ref, "codex") is not None):
                            raise refuse(BAD_SESSION_REF, "base listing impersonates an explicit Codex route")
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
                    row["binding_runtime"] = one
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
        binding_counts: dict[str, int] = {}
        for row in sessions:
            key = provider_key(row["device_ref"], row["runtime"])
            counts[key] = counts.get(key, 0) + 1
            actual = provider_key(row["device_ref"], row["binding_runtime"])
            binding_counts[actual] = binding_counts.get(actual, 0) + 1
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
            "sessions_per_binding": binding_counts,
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
        """What `principal` may do with `key` — the same upper bound `send_to_session` enforces.

        `send_to_session` asks two questions before it sends: does this
        credential carry `fleet:send`, and does `_may_send` admit this
        device/session/principal? This function must answer both. It used to
        answer only the second, so a read-only credential — device send-enabled,
        session allowlisted, no operator grant — was reported `send_allowed:
        true` and then refused `SCOPE_DENIED`. The scope is the first gate on the
        execution path, so it is the first term here; a read surface that omits
        it reports a capability the machine will not exercise.

        Takes the principal because both gates do. A control state computed
        without one would answer "may anyone", and reporting that under the key
        `send_allowed` would tell a caller it may send to a session that
        `send_to_session` will refuse — a read surface disagreeing with the
        machine about the one question it was asked, which is worse than not
        reporting it.
        """
        binding = self.config.session_binding(key)
        device_ok = bool(binding and binding.enabled and (binding.allow_send
                         or self._owner_profile(principal, key.device_ref, key.provider) is not None))
        session_ok = key.ref in self.config.policy.send_allow
        grant_verbs = self._operator_grant_verbs(principal, key.device_ref, key.provider)
        scope_ok = SCOPE_SEND in principal.scopes
        return {
            "send_allowed": bool(scope_ok and device_ok
                                 and (session_ok or "send" in grant_verbs)),
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
        if self._owner_profile(principal, key.device_ref, key.provider) is not None:
            return {**self._session_control_state(principal, key),
                    "note": "Owner-max is configured for this runtime. The current credential's "
                            "scopes apply; no new per-session or per-project grant is needed."}
        return {
            **self._session_control_state(principal, key),
            "note": "control is a separate grant from creation. A session this "
                    "caller may not send to is reported, not hidden: sending "
                    "requires the credential to carry `fleet:send`, `allow_send` "
                    "on the device, and either the session in `policy.send_allow` "
                    "or a `send` grant for this principal on this device in "
                    "`policy.operator_grants`.",
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
        provider = self._session_provider(key)
        detail = provider.get_session(key)
        detail = normalize_detail(
            key, detail, self.config.session_binding(key))
        self._verified_family_detail(key, provider, detail)
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
        provider = self._session_provider(key)
        self._verified_family_detail(key, provider)
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

    def _record_delivery_ack(self, claim, record, *, expect_execution=False):
        # An omitted idempotency key removes replay protection, not ACK checks.
        # In particular a busy/error envelope cannot become accepted:true merely
        # because no claim was requested.
        try:
            receipt = delivery_receipt(record)
            if expect_execution and not (receipt["run_id"] or receipt["queued_message_uuid"] or receipt.get("delivery_id")):
                raise ValueError("execution acknowledgement has no original reference")
        except (ValueError, TypeError, AttributeError) as exc:
            raise refuse(DISPATCH_OUTCOME_UNKNOWN,
                         "the provider did not return a valid delivery acknowledgement; "
                         "reconcile the original session before retrying",
                         retry_safe=False,
                         idempotency_durability=(self.dispatch.durability if claim
                                                else "NOT_REQUESTED")) from exc
        if claim is not None:
            self.dispatch.complete(claim, record)

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

        claim = None
        if idempotency_key is not None:
            claim = self.dispatch.begin(
                principal, _VERB_SEND, key.ref, idempotency_key,
                device_ref=key.device_ref, runtime=key.provider,
                session_ref=key.ref, params={"message": message})
            replayed = claim.replay
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
                    **({"delivery_id": replayed["delivery_id"]} if "delivery_id" in replayed else {}),
                    "queued_message_uuid": replayed.get("queued_message_uuid"),
                    "idempotency_durability": self.dispatch.durability,
                }

        provider = self._session_provider(key)
        self._verified_family_detail(key, provider)
        payload = provider.send(key, message, idempotency_key=idempotency_key)
        run_id = payload.get("runId") or payload.get("run_id") or (payload.get("run") or {}).get("id")
        delivery = ({"delivery_id": payload.get("deliveryId", payload.get("delivery_id"))}
                    if "deliveryId" in payload or "delivery_id" in payload else {})
        record = {
            **delivery,
            "display_name": key.as_dict(),
            "run_id": run_id,
            "queued_message_uuid": payload.get("queuedMessageUuid") or payload.get("queued_message_uuid"),
            "accepted": payload.get("accepted", True),
            "raw": payload,
        }
        self._record_delivery_ack(claim, record, expect_execution=True)
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
            **delivery,
            "run": record,
            "idempotency_durability": self.dispatch.durability if claim else "NOT_REQUESTED",
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

        provider = self._session_provider(key)
        self._verified_family_detail(key, provider)
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

    def create_project(self, principal: Principal, *, device: str, root: str,
                       name: str, display_name: str | None = None,
                       runtime: str = BASE_RUNTIME, idempotency_key: str | None = None):
        """Create one new project through an exact owner's named local root."""
        principal.require(SCOPE_CREATE)
        self.admit(principal)
        self._validate_runtime_selector(runtime)
        if not isinstance(device, str) or not device.strip():
            raise refuse(BAD_REQUEST, "device is required")
        device_ref = resolve_or_refuse(self.registry, device).device_ref.ref
        binding = self.config.namespace_binding(device_ref, runtime)
        admitted = self._project_root_profile(principal, device_ref, runtime)
        if binding is None or not binding.enabled:
            raise refuse(UNKNOWN_DEVICE, "the project namespace has no enabled binding")
        if admitted is None:
            raise refuse(
                APPROVAL_REQUIRED,
                "project creation requires the selected owner-max profile, or the "
                "`create` operator grant on a device whose operator authored "
                "project roots",
            )
        profile, admitted_by = admitted
        if display_name is not None and (not isinstance(display_name, str)
                or not display_name.strip() or len(display_name) > 256
                or any(ord(c) < 32 for c in display_name)):
            raise refuse(BAD_REQUEST, "display_name must be a nonempty label of at most 256 characters")
        try:
            target = build_project_path(profile, root, name, local_check=False)
        except OwnerAccessError as exc:
            raise refuse(BAD_REQUEST, str(exc)) from None
        adapter = self._require_provider(device_ref, binding.runtime)
        claim = None
        if idempotency_key is not None:
            claim = self.dispatch.begin(
                principal, "project.create", provider_key(device_ref, binding.runtime),
                idempotency_key, device_ref=device_ref, runtime=binding.runtime,
                params={"namespace": runtime, "root": root, "name": name,
                        "display_name": display_name}, expected_project_path=target)
            if claim.replay is not None:
                return {**claim.replay, "device_ref": device_ref, "runtime": runtime,
                        "binding_runtime": binding.runtime, "idempotent_replay": True,
                        "idempotency_durability": self.dispatch.durability}
        creator = getattr(adapter, "create_project", None)
        if creator is None:
            raise refuse("CAPABILITY_UNSUPPORTED", "provider has no project creation operation")
        payload = creator(root=root, name=name, project_path=target, display_name=display_name)
        if (not isinstance(payload, dict) or payload.get("freshDirectoryCreated") is not True
                or payload.get("accepted", True) is not True
                or not isinstance(payload.get("projectId"), str) or not payload["projectId"].strip()
                or payload["projectId"] != payload["projectId"].strip() or len(payload["projectId"]) > 512
                or payload.get("path") != target
                or payload.get("isArchived") is not None and payload.get("isArchived") is not False
                or payload.get("outcome", "created") != "created"):
            raise refuse(INTERNAL, "project creation may have happened but its fresh ACK is malformed",
                         retry_safe=False)
        receipt = {"project_id": payload["projectId"], "project_path": target, "accepted": True}
        if claim is not None:
            self.dispatch.complete(claim, receipt)
        self.audit.record("fleet.project.create", principal=principal.name,
                          device_ref=device_ref, runtime=runtime, project_id=receipt["project_id"])
        return {**receipt, "device_ref": device_ref, "runtime": runtime,
                "binding_runtime": binding.runtime, "idempotent_replay": False,
                "fresh_directory_created": True, "admitted_by": admitted_by,
                "idempotency_durability": self.dispatch.durability if claim else "NOT_REQUESTED"}

    def delete_project(self, principal: Principal, *, device: str, project_id: str,
                       confirm_path: str, force: bool = False,
                       runtime: str = BASE_RUNTIME) -> dict[str, Any]:
        """Archive (default) or permanently remove ONE project row.

        Three questions, in this order, and each is a different authority:

        *   **may this caller destroy stored state on this device** — the
            device's `allow_control` (or a selected owner-max profile) plus the
            `control` operator grant, through `_may_control_device`. A project
            row is stored state on that machine, and removing it is not a lighter
            act than stopping a run;
        *   **is this the project the caller means** — the catalogue is read
            FIRST, archived rows included, and the row's own `path` must equal
            `confirm_path`. A project id is opaque and easy to paste wrong; the
            path is what a person can recognise, so the caller has to repeat the
            one it saw. An id with no row is `PROJECT_NOT_FOUND`, which is also
            what a second force delete answers — an honest reading, not a
            replayed success;
        *   **did it actually happen** — the device's ACK is not taken as proof.
            The catalogue is read AGAIN: a force delete must leave no row even
            with archived included, and an archive must leave the row marked
            archived. A device that answers "deleted" and still lists the project
            is reported as a contradiction, never as success.

        `force: false` is reversible (the row is archived; the directory and the
        transcripts are untouched) and `force: true` removes the session rows and
        their transcript files. The project DIRECTORY on disk is never removed —
        the device's own service does not do it either, and this call restates
        that in its answer rather than leaving the silence to be read as an
        omission.

        No idempotency key, and this call does not need one: the second call
        re-reads the catalogue and answers from it, so a retried force delete
        reports `PROJECT_NOT_FOUND` and a retried archive reports
        `was_archived: true`. That is a statement about the present; a cached
        answer would be a statement about the past wearing its clothes.
        """
        principal.require(SCOPE_CONTROL)
        self.admit(principal)
        self._validate_runtime_selector(runtime)
        if not isinstance(device, str) or not device.strip():
            raise refuse(BAD_REQUEST, "device is required")
        if (not isinstance(project_id, str) or not project_id.strip()
                or project_id != project_id.strip() or len(project_id) > 512):
            raise refuse(
                BAD_REQUEST,
                "project_id must be a non-empty id, exactly as `projects_list` "
                "reports it — leading or trailing whitespace is a different id.",
            )
        if not isinstance(confirm_path, str) or not confirm_path.strip():
            raise refuse(
                BAD_REQUEST,
                "confirm_path is required: repeat the project's own `path` as "
                "`projects_list` reports it. A delete is confirmed against the "
                "device's catalogue, never against the id alone.",
            )
        device_ref = resolve_or_refuse(self.registry, device).device_ref.ref
        binding = self.config.namespace_binding(device_ref, runtime)
        if binding is None or not binding.enabled:
            raise refuse(UNKNOWN_DEVICE, "the project namespace has no enabled binding")
        self._may_control_device(
            principal, device_ref, binding,
            _AuthoredVerb(cloudcli_native.namespaced("project_delete")),
            runtime=binding.runtime,
        )
        provider = self._require_provider(device_ref, binding.runtime)
        deleter = getattr(provider, "delete_project", None)
        if deleter is None:
            raise refuse(
                "CAPABILITY_UNSUPPORTED",
                f"{device_ref}'s provider ({provider.kind}) has no project "
                f"deletion operation.",
                device_ref=device_ref,
            )
        before = self._project_catalogue_row(provider, project_id,
                                            include_archived=True)
        if before is None:
            raise refuse(
                PROJECT_NOT_FOUND,
                f"{device_ref} has no project with id {project_id!r} — archived "
                f"projects included. Read `projects_list` again rather than "
                f"assuming an earlier call failed.",
                device_ref=device_ref,
                project_id=project_id,
            )
        before_path = before.get("path")
        if not isinstance(before_path, str) or not before_path:
            raise refuse(
                INTERNAL,
                "the device's catalogue named the project without a path, so a "
                "delete could not be confirmed against it",
                retry_safe=False,
            )
        if before_path != confirm_path:
            # The path is NOT echoed: the caller may hold `control` without
            # `read`, and the path of a project it cannot list is not this
            # refusal's to disclose. It is told which read answers the question.
            raise refuse(
                BAD_REQUEST,
                "confirm_path does not match the path the device's catalogue "
                "holds for this project id. Read `projects_list` and repeat the "
                "row's own `path` exactly.",
                device_ref=device_ref,
                project_id=project_id,
            )
        payload = deleter(project_id=project_id, force=bool(force))
        if (not isinstance(payload, dict)
                or payload.get("projectId") != project_id
                or payload.get("path") != before_path
                or not isinstance(payload.get("deleted"), bool)
                or not isinstance(payload.get("archived"), bool)
                or not isinstance(payload.get("wasArchived"), bool)
                or payload.get("deleted") != bool(force)
                or payload.get("archived") == payload.get("deleted")
                or payload.get("directoryRemoved") is not False):
            raise refuse(
                INTERNAL,
                "project deletion may have happened but its ACK is malformed: it "
                "must name the same project id and path, say which of archived/"
                "deleted it did, and say that no directory was removed",
                retry_safe=False,
            )
        after = self._project_catalogue_row(provider, project_id,
                                            include_archived=True)
        if force:
            verified = after is None
            verification = "absent from the catalogue, archived included"
        else:
            verified = after is not None and bool(
                after.get("isArchived") is True or after.get("is_archived") is True
                or after.get("isArchived") == 1 or after.get("is_archived") == 1)
            verification = "catalogued as archived"
        if not verified:
            raise refuse(
                INTERNAL,
                f"{device_ref} acknowledged the {'delete' if force else 'archive'} "
                f"of {project_id!r} and its catalogue still disagrees. The effect "
                f"is unconfirmed: reconcile by reading `projects_list` before "
                f"retrying.",
                retry_safe=False,
                device_ref=device_ref,
                project_id=project_id,
            )
        self.audit.record("fleet.project.delete", principal=principal.name,
                          device_ref=device_ref, runtime=runtime,
                          project_id=project_id,
                          mode="force" if force else "archive")
        return {
            "device_ref": device_ref,
            "runtime": runtime,
            "binding_runtime": binding.runtime,
            "project_id": project_id,
            "project_path": before_path,
            "was_archived": payload["wasArchived"],
            "archived": payload["archived"],
            "deleted": payload["deleted"],
            # A literal, so a caller reading the shape cannot mistake the silence
            # for an omission: this call never removes a directory.
            "directory_removed": False,
            "verified": True,
            "verification": verification,
        }

    # Modes that never wait for a person, and modes that do. These classify a
    # value the DEVICE reported; they are not a list of what a caller may ask
    # for (that is `create_allow.<project>.permission_modes`) and they never
    # stand in for a readback. A mode this build does not know is `UNKNOWN`
    # rather than guessed safe.
    _UNATTENDED_SAFE_MODES = frozenset({"dontAsk", "plan", "bypassPermissions"})
    _INTERACTIVE_MODES = frozenset({"default", "manual", "auto", "acceptEdits"})

    def _read_effective_permission_mode(self, provider, key, resolved_mode):
        """Ask the device what it actually applied; never infer it from the ask.

        One `session_get`, after the session exists. A failure here is a finding
        about the READBACK, not about the create: the session may well exist and
        be fine, and turning a successful mint into an error because the second
        read failed would report a failure this surface did not observe. The
        caller gets `UNVERIFIED` and the reason, which is the honest answer.
        """
        try:
            detail = provider.get_session(key)
        except Exception as exc:  # noqa: BLE001 - any readback failure is a finding
            return None, {
                "state": "UNVERIFIED",
                "prompt_risk": "UNKNOWN",
                "requested": resolved_mode,
                "effective": None,
                "reason": f"session_get failed: {type(exc).__name__}",
            }
        native = detail.get("session") if isinstance(detail.get("session"), dict) else {}
        mode = native.get("permissionMode")
        if mode is None:
            mode = detail.get("permissionMode")
        if not isinstance(mode, str) or not mode.strip():
            return None, {
                "state": "UNVERIFIED",
                "prompt_risk": "UNKNOWN",
                "requested": resolved_mode,
                "effective": None,
                "reason": "the device did not report a permission mode for this "
                          "session; a requested value is not proof of the effective "
                          "one",
            }
        mode = mode.strip()
        if resolved_mode is None:
            state = "REPORTED"
            reason = ("the device reported its own default; nothing was requested "
                      "for it to be compared against")
        elif mode == resolved_mode:
            state = "CONFIRMED"
            reason = "the device reported the mode that was resolved for this create"
        else:
            state = "MISMATCH"
            reason = ("the device reported a mode other than the one resolved for "
                      "this create")
        risk = ("NONE" if mode in self._UNATTENDED_SAFE_MODES
                else "HIGH" if mode in self._INTERACTIVE_MODES else "UNKNOWN")
        return mode, {
            "state": state,
            "prompt_risk": risk,
            "requested": resolved_mode,
            "effective": mode,
            "reason": reason,
        }

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
        # A runtime the caller named that is not a name is refused here, with
        # the device, and for the same reason: `""` is an argument that was
        # supplied and names nothing, while an omitted one means the device's
        # own runtime. Resolving the first as the second would mint the session
        # in the base account under a caller who asked for another one.
        self._validate_runtime_selector(runtime)
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
        selected_binding = self.config.provider_for(device_ref, runtime)
        receipt_namespace = self._create_namespace(selected_binding, provider)
        requested_defaults = {"model": model, "permission_mode": permission_mode}
        model, permission_mode, by = self._resolve_create_policy(
            principal, device_ref, project, runtime, receipt_namespace,
            provider, model, permission_mode)
        resolved_defaults = {"model": model, "permission_mode": permission_mode, "by": by}

        if provider not in runtime_identity.FAMILIES:
            raise refuse(BAD_REQUEST, "provider must name the Claude or Codex native family")
        selected_binding = self.config.provider_for(device_ref, runtime)
        receipt_namespace = (provider if selected_binding is not None
                             and selected_binding.multiplex_native_families else runtime)
        claim = None
        if idempotency_key is not None:
            claim = self.dispatch.begin(
                principal, _VERB_CREATE, _create_scope(device_ref, project, runtime),
                idempotency_key, device_ref=device_ref, runtime=receipt_namespace,
                params={"project": project, "message": message, "provider": provider,
                        "model": requested_defaults["model"],
                        "permission_mode": requested_defaults["permission_mode"],
                        "lifecycle_mode": lifecycle_mode})
            replayed = claim.replay
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
                    **({"delivery_id": replayed["delivery_id"]} if "delivery_id" in replayed else {}),
                    "queued_message_uuid": replayed.get("queued_message_uuid"),
                    "created": replayed,
                    "idempotency_durability": self.dispatch.durability,
                    # Recomputed rather than replayed: the session is the thing
                    # the key remembers, and what may be done with it is a
                    # question about policy *now*. A cached answer here would be
                    # exactly the stale grant this surface refuses everywhere
                    # else.
                    "control": self._control_block(principal, key) if key else None,
                    "permission_readiness": {
                        "state": "UNVERIFIED",
                        "prompt_risk": "UNKNOWN",
                        "requested": None,
                        "effective": None,
                        "reason": "replayed from the idempotency record; no device "
                                  "call was made, so the effective mode of the "
                                  "session that already exists is unknown here",
                    },
                    "note": "replayed from the idempotency record; no second "
                            "session was created and nothing on the device was "
                            "called.",
                }

        if self._owner_profile(principal, device_ref, receipt_namespace) is not None:
            self._verify_owner_project(adapter, project)
        payload = adapter.create(
            project=project,
            message=message,
            provider=provider,
            model=model,
            lifecycle_mode=lifecycle_mode,
            permission_mode=permission_mode,
        )
        session_id = payload.get("sessionId") or payload.get("session_id")
        if not isinstance(session_id, str) or not session_id.strip():
            # A create that reports success must name what it created. There is
            # nothing sensible to return without it, and returning the bare
            # payload would hand the caller a shape this surface never promised.
            raise refuse(
                INTERNAL,
                f"{device_ref} answered session_create without a session id; "
                f"the session may exist and this surface cannot name it. Keys "
                f"answered: {sorted(payload)}",
            )
        # Profiles keep their exact runtime namespace. Only an explicitly
        # multiplexed base uses the native family. This expected namespace was
        # fixed before the claim/IO; it never relabels a stored receipt.
        key = SessionKey.compose(device_ref, receipt_namespace, str(session_id))
        run_id = payload.get("runId") or payload.get("run_id")
        queued_message_uuid = payload.get("queuedMessageUuid", payload.get("queued_message_uuid"))
        delivery = ({"delivery_id": payload.get("deliveryId", payload.get("delivery_id"))}
                    if "deliveryId" in payload or "delivery_id" in payload else {})
        record = {
            **delivery,
            "session": key.as_dict(),
            "run_id": run_id,
            "queued_message_uuid": queued_message_uuid,
            "accepted": payload.get("accepted", True),
            "raw": payload,
        }
        self._record_delivery_ack(claim, record,
                                  expect_execution=bool(message and message.strip()))
        # The one fact the create could not know: what the DEVICE applied. Read
        # back here, in the same answer, because a second call is a second
        # round trip a caller can forget — and "created" being mistaken for
        # "permission-ready" is the exact inference this block exists to stop.
        effective_mode, readiness = self._read_effective_permission_mode(
            adapter, key, permission_mode)
        readiness["run_started"] = bool(message and message.strip())
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
            "queued_message_uuid": queued_message_uuid,
            **delivery,
            "created": record,
            "idempotency_durability": self.dispatch.durability if claim else "NOT_REQUESTED",
            # The caller is told, in the same answer, whether the session it just
            # minted is one it may drive. Sent separately it would be a second
            # round trip the caller could forget, and the natural mistake — treat
            # "created" as "mine to control" — is exactly the inference this
            # block exists to prevent.
            "control": self._control_block(principal, key),
            # requested / resolved / effective, kept apart because they are
            # three facts and only the first two are known HERE.
            #
            # `effective` is null on purpose and never inferred from `resolved`:
            # what the device actually ran with is a property of the device, and
            # no amount of configuration declares it. A surface that reported
            # `resolved` as effective would be asserting a readback it never
            # took — and the one case that matters, a device that quietly
            # applies its own default, is exactly the case where the two differ.
            "defaults": {
                "requested": requested_defaults,
                "resolved": resolved_defaults,
                "effective": {"model": None, "permission_mode": effective_mode},
                "effective_reason": (
                    "permission_mode is what the device reported for this session, "
                    "or null when it reported nothing; a null is UNKNOWN, never "
                    "'the resolved value was used'. model is not read back: the "
                    "provider reports no effective model on this surface."),
            },
            # Four states, not a boolean: CONFIRMED and MISMATCH are comparisons
            # against what was resolved, REPORTED is a device default with
            # nothing to compare, and UNVERIFIED is the absence of an answer.
            # `prompt_risk` is a statement about the mode the DEVICE reported:
            # NONE means nothing in that mode waits for a person, HIGH means it
            # does, UNKNOWN means this build does not know the mode.
            "permission_readiness": readiness,
            "note": "session_create mints a session and, when a first message was "
                    "given, starts its run. It grants no authority over anything "
                    "that session may then operate on: see `control` for whether "
                    "sending to it is authorized here, which is a separate grant.",
        }

    def get_run(
        self,
        principal: Principal,
        key: SessionKey,
        run_id: str | None = None,
        *,
        delivery_id: str | None = None,
        wait: bool = False,
        wait_seconds: float = 0.0,
    ) -> dict[str, Any]:
        principal.require(SCOPE_READ)
        self.admit(principal)
        identifiers = {name: value for name, value in (("runId", run_id), ("deliveryId", delivery_id))
                       if value is not None}
        problems = cloudcli_native.validate_arguments(identifiers, cloudcli_native.input_schema("run_get"))
        if problems:
            raise refuse(BAD_REQUEST, "; ".join(problems))
        provider = self._session_provider(key)
        self._verified_family_detail(key, provider)
        if delivery_id is not None:
            caller = getattr(provider, "get_delivery", None)
            if not callable(caller):
                raise refuse("CAPABILITY_UNSUPPORTED", "provider lacks delivery lookup")
            binding = self.config.session_binding(key)
            payload = cloudcli_native.validate_delivery(caller(key, delivery_id), key, delivery_id, family=binding.family)
            if payload.get("provider") is not None and payload["provider"] not in {key.provider, binding.family}:
                raise refuse(BAD_SESSION_REF, "delivery metadata names another session family")
            self.audit.record("fleet.get_run", principal=principal.name, session_ref=key.ref,
                              delivery_id=delivery_id)
            return {**payload, **key.as_dict(), "delivery_id": delivery_id, "run_id": payload.get("runId")}
        payload = provider.get_run(key, run_id, wait=wait, wait_seconds=wait_seconds)
        expected = {**key.as_dict(), "run_id": run_id, "runId": run_id, "sessionId": key.native_session_id}
        if payload.get("idKind") is not None and payload["idKind"] != "run":
            raise refuse(BAD_SESSION_REF, "run lookup returned another identifier namespace")
        for field in ("device_ref", "native_session_id", "session_ref", "run_id", "runId", "sessionId"):
            if payload.get(field) is not None and payload[field] != expected[field]:
                raise refuse(BAD_SESSION_REF, "run metadata contradicts its requested target")
        binding = self.config.session_binding(key)
        native_family = key.provider if key.provider in runtime_identity.FAMILIES else binding.family
        providers = (key.provider,) if payload.get("session_ref") is not None else (
            key.provider, native_family)
        if payload.get("provider") is not None and payload["provider"] not in providers:
            raise refuse(BAD_SESSION_REF, "run metadata names another session family")
        self.audit.record(
            "fleet.get_run", principal=principal.name, session_ref=key.ref, run_id=run_id
        )
        return {**payload, **key.as_dict(), "run_id": run_id}

    def _event_visible(self, principal: Principal, event: dict) -> bool:
        """Current Fleet read boundary, also the seam for authored read restrictions.

        fleet:read currently covers enabled configured bindings globally. Create
        grants/defaults do not restrict reads. No project is inferred from them.
        """
        try:
            key = SessionKey.parse(event["session_ref"])
            resolve_or_refuse(self.registry, key.device_ref)
            self._session_provider(key)
            return (event["device_ref"] == key.device_ref
                    and event["runtime"] == key.provider)
        except (FleetRefusal, KeyError, ValueError):
            return False

    def inbox(self, principal: Principal, *, since=None, kinds=None, level=None,
              limit=100, ack=False) -> dict[str, Any]:
        principal.require(SCOPE_READ)
        self.admit(principal)
        if not isinstance(ack, bool):
            raise ValueError("ack must be boolean")
        if self.events is None:
            return {"status": "UNAVAILABLE", "events": [],
                    "durability": "UNAVAILABLE"}
        subscriber = json.dumps([principal.credential_kind, principal.name], separators=(",", ":"))
        page = self.events.read(subscriber, since=since, limit=limit,
                                visible=partial(self._event_visible, principal),
                                kinds=kinds, level=level)
        if ack:
            page["acknowledged_cursor"] = self.events.ack(subscriber, page["next_cursor"])
        return {**page, "status": "AVAILABLE", "durability": "SQLITE"}

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
        # The same authority the read path already uses, asked BEFORE the
        # default is applied — otherwise `""` is not "unnamed", it is the base
        # account, chosen by a caller who named something else.
        self._validate_runtime_selector(runtime)
        if not session_ref:
            return runtime if runtime is not None else BASE_RUNTIME
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
        proof_key = self._native_session_key(device_ref, session_ref, native) if session_ref else None
        session_key = proof_key if cloudcli_native.is_session_addressed(native) else None
        binding = (self.config.session_binding(session_key) if session_key is not None
                   else self.config.provider_for(device_ref, runtime))
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
        if proof_key is not None and "session" in args:
            self._same_identity(args, "session", proof_key.native_session_id, native)
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
        # (recorded from the CloudCLI build each entry names in `recorded_from`;
        # a live per-device schema may override).
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
        provider = (self._session_provider(session_key) if session_key is not None
                    else self._require_provider(device_ref, runtime))
        if governance == cloudcli_native.GOV_CREATE:
            namespace = self._create_namespace(binding, args.get("provider") or "claude")
            if self._owner_profile(principal, device_ref, namespace) is not None:
                self._verify_owner_project(provider, args.get("project"))
        caller = getattr(provider, "native_call", None)
        if caller is None:
            raise refuse(
                BAD_REQUEST,
                f"{device_ref}'s provider ({provider.kind}) does not inherit the "
                f"CloudCLI native surface.",
            )
        family_detail = (self._verified_family_detail(session_key, provider)
                         if session_key is not None else None)
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
                                             project, args, native, detail=family_detail,
                                             principal=principal)
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
        # Asked before the binding lookup, so a blank runtime is a bad request
        # rather than "no such binding" — the same rule, and the same authority,
        # as every other entry that takes a runtime.
        self._validate_runtime_selector(runtime)
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
            namespace = self._create_namespace(binding, arguments.get("provider") or "claude")
            self._require_operator_verb(principal, device_ref, "create", native,
                                        project=project_name, runtime=namespace)
            model, mode, _ = self._resolve_create_policy(
                principal, device_ref, project_name, binding.runtime, namespace,
                arguments.get("provider") or "claude", arguments.get("model"),
                arguments.get("permissionMode"))
            if model is not None:
                arguments["model"] = model
            if mode is not None:
                arguments["permissionMode"] = mode
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
                                    arguments: dict[str, Any], native, *, detail=None,
                                    principal=None) -> None:
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
        if detail is None:
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
        try:
            detail = normalize_detail(key, detail, self.config.session_binding(key))
        except FleetRefusal:
            raise refuse(APPROVAL_REQUIRED, "session project metadata contradicts its target") from None
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
        binding = self.config.session_binding(key)
        actual_runtime = binding.runtime if binding is not None else key.provider
        owner = (self._owner_profile(principal, device_ref, key.provider)
                 if principal is not None else None)
        grant = (self._owner_project_grant(principal, device_ref, actual, actual_runtime,
                                          key.provider) if owner is not None
                 else self._configured_create_grant(actual))
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
        binding = self.config.session_binding(key)
        actual_runtime = binding.runtime if binding is not None else key.provider
        family = key.provider if key.provider in runtime_identity.FAMILIES else (
            binding.family if binding is not None else None)
        runtime_rule = (self._project_runtime_rule(principal, device_ref, actual,
                                                  actual_runtime, key.provider)
                        if principal is not None else self.config.policy.runtime_create_defaults.get(
                            provider_key(device_ref, actual_runtime), {}).get(actual))
        if runtime_rule is not None:
            if family != runtime_rule.family:
                raise refuse(WRITE_NOT_GRANTED, "session family is not authorized on this account/project")
            if model is not None and model not in runtime_rule.allowed_models:
                raise refuse(
                    WRITE_NOT_GRANTED,
                    "session_reconfigure model is not authorized for this account/project",
                    device_ref=device_ref, runtime=key.provider, project=actual,
                )
            if mode is not None and owner is None and mode not in runtime_rule.allowed_permission_modes:
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
            # Two refusals, because they send an operator to two edits. A grant
            # the binding DOES declare but the device cannot hold is a registry
            # fact, not a missing line; reporting it as "held are none" would
            # send someone to add a grant that is already there.
            declared = self._declared_grant_verbs(
                principal.name, device_ref, runtime)
            if verb in declared:
                ownership = self._device_ownership(device_ref)
                detail = (
                    f"the binding does name `{verb}` for {principal.name!r} on "
                    f"{device_ref}, but that device is {ownership!r} and "
                    f"`policy.grant_eligible_ownership` is "
                    f"{list(self._eligible_ownerships())}. The "
                    f"grant is inert here rather than narrowed: the owner of a "
                    f"device is a registry fact, and an unset owner is an "
                    f"unknown one, which this host defers on rather than "
                    f"authorizes."
                )
            else:
                detail = (
                    f"the operator grant verbs held are "
                    f"{list(verbs) or 'none'}."
                )
            raise refuse(
                WRITE_NOT_GRANTED,
                f"`{native.namespaced}` needs a `{verb}` operator grant for "
                f"{principal.name!r} on {device_ref}; {detail} The device floor "
                f"passed and the principal is not granted, so the call is "
                f"refused.",
                device_ref=device_ref, required_verb=verb,
                **({"project": project} if project is not None else {}),
            )

    def status(self, principal: Principal) -> dict[str, Any]:
        """Health of this surface and of every dependency it composes.

        This is a read, and it is gated as one. Before this line it called only
        `admit()` — the rate limiter — so a credential with no scope, or with
        only `fleet:send`, could read the enrolled-device inventory, every
        route's health, `policy.send_allow` and the audit tail. That is more
        than the diagnostic the tool advertises, and it contradicted
        `fleet/owner-grant-3devices.md`, which names `fleet:read` for
        `fleet_status`. A narrow write credential that needs to diagnose a
        failed write still learns why from the refusal it already received
        (`UNKNOWN_DEVICE`, `PROVIDER_UNREACHABLE`, `WRITE_NOT_GRANTED`); it
        does not need the fleet's read surface to do it.
        """
        principal.require(SCOPE_READ)
        self.admit(principal)
        providers: list[dict[str, Any]] = []
        for ref, provider in sorted(self.providers.items()):
            health = provider.health()
            providers.append(health.as_dict())
        # The Core readings, gathered beside the provider health and never folded
        # into it. Keyed by DEVICE rather than by provider key, because a Core
        # reading is a fact about a machine and not about one runtime's route to
        # it; a device with two runtimes still has one Core.
        core_lite: list[dict[str, Any]] = []
        for device in self.registry.all():
            ref = device.device_ref.ref
            # One row per BOUND runtime. A device that serves two accounts has
            # two Cores, and collapsing them to one row would publish whichever
            # answered first as the machine's whole answer. The base runtime is
            # always included when it is bound, so the pre-runtime shape is still
            # exactly one row for a single-account device.
            for runtime in self._bound_runtimes(ref) or (BASE_RUNTIME,):
                core_lite.append(self._core_lite_summary(ref, runtime))
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
                "version": SERVER_VERSION,
                "local_device_ref": self.config.local_device_ref,
                "config_source": str(self.config.source) if self.config.source else None,
                "audit_enabled": bool(self.config.audit),
                "idempotency_durability": self.dispatch.durability,
            },
            "event_projection": {
                "status": "AVAILABLE" if self.events is not None else "UNAVAILABLE",
                "durability": "SQLITE" if self.events is not None else "UNAVAILABLE",
                "observer": self.run_watcher.last_result if self.run_watcher is not None else None,
            },
            "identity_layer": {
                "authority": "fleet/registry.yaml",
                "enrolled_devices": enrolled,
                "devices_with_route": bound,
            },
            "providers": providers,
            # A separate top-level list, not a field on each provider: the
            # reading is about the MACHINE. Merging it into `providers` would
            # say that a device's Core is a property of one runtime's route,
            # which is exactly the substitution this key exists to prevent.
            "core_lite": core_lite,
            "policy": {
                "send_allow": list(self.config.policy.send_allow),
                "read_redact": self.config.policy.read_redact,
                "max_read_messages": self.config.policy.max_read_messages,
                # Reported because it is now the binding's answer rather than a
                # constant in this file: an operator reading `fleet_status`
                # should be able to see which ownership classes a grant can
                # reach without opening the YAML.
                "grant_eligible_ownership": list(self._eligible_ownerships()),
            },
            "principal": principal.as_dict(),
            "owner_access": self.owner_access_report(principal),
            "recent_audit": self.audit.tail(20),
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
