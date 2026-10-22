"""Local-only Fleet MCP binding.

Authority split, following `fleet/README.md`
--------------------------------------------
`fleet/registry.yaml` is authored and public: it says which devices exist.
*This* file is observed and local-only: it says how this runtime reaches them.
It is never committed, never copied between machines, and never read by
anything that publishes.

It records credential **anchors** — paths — and never credential values. That
is `fleet/transport.py`'s rule (`FORBIDDEN_BINDING_KEYS`: a binding says where
a transport is; the transport holds its own credentials), and it is the reason
this file can sit in a home directory next to the other ServerOS bindings
without becoming a secret store.

Default location: `fleet_mcp/local_roots.py#binding_root()`. Deliberately NOT
`~/.serveros/`: that root's name list is closed and enforced by
`runtime/paths.py#admit`, and this surface's own contract says the remedy is
"move it rather than widen this list". `FLEET_MCP_CONFIG` overrides the default.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .errors import BAD_REQUEST, refuse
from .feishu_sso import FeishuBinding, FeishuWhitelistEntry
from .identity import SessionKey
from .local_roots import binding_candidates, refusal_if_closed_root
from . import runtime_identity
from .runtime_identity import BASE_RUNTIME
from .runtime_create_policy import (RuntimeProjectDefaults, PolicyError,
                                    parse_runtime_create_defaults)
from .scopes import ALLOWED_SCOPES, DEFAULT_GRANTABLE_SCOPES
from .owner_access import OwnerAccessProfile, parse_owner_access

SCHEMA = "serveros/fleet-mcp.local/v1"

# Kinds this surface knows how to drive. The set is closed on purpose: a typo in
# a binding must not silently produce a device that reports "no sessions".
PROVIDER_KINDS = ("cloudcli-mcp-http", "fleet-node")

# The verbs `policy.operator_grants` may name, and the ones it may not yet.
#
# Same two-list shape as `scopes.py`'s `IMPLEMENTED_SCOPES` / `PLANNED_SCOPES`,
# and for the same reason: the plan has to be visible for the next verb to be
# additive rather than a redesign, but a name that reads as live while being
# inert is worse than no name at all. A binding naming a planned verb is refused
# at load, pointing at the scope that does not exist yet.
#
# `control` and `create` graduated here so the CloudCLI mirror can be governed at
# the principal x device x verb axis the SAME way `send` is. Graduating the verb
# does not grant anything: a principal with no entry still holds nothing, and the
# per-device flag and the per-session / per-project floor are checked on top
# (`fleet._may_control` / `_may_create`). `approve` and `navigate` stay planned —
# their Fleet scopes do not exist, so no grant can express them, and the two
# native verbs that need them are DENIED rather than inherited.
OPERATOR_GRANT_VERBS = ("send", "control", "create", "approve", "navigate")
PLANNED_OPERATOR_GRANT_VERBS = ()

# The ownership value that makes a device eligible to appear in an owner grant.
#
# A single allowed value rather than "anything except `personal`", because the
# field is `str | None` and the two polarities differ exactly on the inputs
# nobody thought about: `None` (authored without an owner), `"Company"`, and
# `"personal "` all pass a `!= "personal"` test and all mean "this device's
# owner is not established". Unknown ownership is DEFER here as it is everywhere
# else on this host, and DEFER is not a grant.
GRANT_ELIGIBLE_OWNERSHIP = "company"



def _expand(value: str) -> str:
    return os.path.expanduser(os.path.expandvars(value))


def _anchor(value: Any, what: str) -> Path:
    if not value or not isinstance(value, str):
        raise refuse(BAD_REQUEST, f"{what} must be a path anchor")
    return Path(_expand(value))


DEFAULT_CONFIG_PATHS = binding_candidates("fleet-mcp.local.yaml")


def provider_key(device_ref: str, runtime: str = BASE_RUNTIME) -> str:
    """The ledger key one runtime on one device is filed under.

    The device's own runtime keeps the BARE `device:<id>` key, so a binding
    written before a second runtime existed is unchanged on disk and in memory.
    Any other runtime appends `|<tag>` — the separator a session reference
    already uses — so the two spellings agree about where the runtime sits.
    """
    return device_ref if runtime == BASE_RUNTIME else f"{device_ref}|{runtime}"


@dataclass(frozen=True)
class ProviderBinding:
    device_ref: str
    kind: str
    endpoint: str
    credential_anchor: Path
    #: Which runtime ON that device this binding reaches. One physical machine
    #: can host several — different OS accounts, each with its own CloudCLI and
    #: its own credential — and they are reached by separate bindings with
    #: separate endpoints and separate anchors. It defaults to the device's own
    #: runtime, so a binding written before a second one existed means exactly
    #: what it meant then.
    runtime: str = BASE_RUNTIME
    connect_timeout: float = 5.0
    read_timeout: float = 45.0
    enabled: bool = True
    note: str | None = None
    # A device whose provider may be described but not driven. Set for a device
    # the operator wants visible in `fleet_list_devices` while write control is
    # withheld — the read/write split is per device, not per credential.
    allow_send: bool = False
    # A device whose sessions may be *created* from this runtime. A separate
    # grant from `allow_send` on purpose: messaging a session that exists and
    # minting one that does not are different questions, and an operator who
    # answered the first has not thereby answered the second. Default false,
    # like every other grant here, so a binding that says nothing about creation
    # does not authorize it.
    allow_create: bool = False
    # A device whose runs may be *stopped* from this runtime. The fourth grant,
    # and the first one that takes something away: creation and messaging both
    # add to the world, and an interrupt ends work that is in progress. Off by
    # default, like every other grant, so a binding that never considered the
    # question answers it with silence rather than with yes.
    allow_control: bool = False
    #: Which credential profile inside the account this binding reaches. One OS
    #: account can hold several independent CC identities — a general route, an
    #: official subscription, a work identity — and those are separate runtimes
    #: with separate tags, separate anchors and separate grants. This is the
    #: NAME of one. It grants nothing: a binding that sets it is still bound by
    #: every flag above, and a profile named here is not thereby authorized for
    #: anything the profile beside it was not.
    profile: str | None = None
    #: Which OS account owns this runtime. Naming only, for the same reason.
    account: str | None = None
    #: Which KIND of agent this runtime serves. A second naming axis, and it is
    #: declared rather than read off the tag: inferring it from the spelling is
    #: how a Claude binding ends up claiming a Codex runtime, and the loader
    #: checks that the declared axes actually derive the tag, so a family that
    #: disagrees with the name is refused instead of believed.
    family: str = BASE_RUNTIME
    #: An owner-authored incarnation label: which provisioning of this profile
    #: the operator meant. Its purpose is to let a declaration say "the second
    #: time this account was set up", so two declarations can be told apart.
    #:
    #: It is a DECLARATION ON A LOCAL FILE. It is not a live-process identity,
    #: nothing signs it, and this surface cannot read a remote incarnation back
    #: today — so it must not be described as fencing. A deployment that needs
    #: proof it is speaking to the same running process needs that proof from
    #: the device's own protocol; until then the honest answer is UNVERIFIED,
    #: never a quiet fallback to a different profile.
    instance_id: str | None = None
    # Explicitly serve unqualified Claude/Codex session namespaces through
    # this one base account gateway. This adds no runtime or grant.
    multiplex_native_families: bool = False
    owner_access: OwnerAccessProfile | None = None

    def __post_init__(self) -> None:
        """Shape only. Whether a declared runtime EXISTS is not a question a
        single binding can answer, and set-level questions are asked where the
        whole set is visible."""
        if self.owner_access is not None and not isinstance(self.owner_access, OwnerAccessProfile):
            raise ValueError("owner_access must be a validated owner profile")
        if not isinstance(self.multiplex_native_families, bool):
            raise ValueError("multiplex_native_families must be true or false")
        if self.multiplex_native_families and (
                self.runtime != BASE_RUNTIME or self.family != BASE_RUNTIME
                or self.profile is not None):
            raise ValueError("multiplex_native_families requires an unprofiled base Claude gateway")
        if self.family not in runtime_identity.FAMILIES:
            raise ValueError(
                f"family {self.family!r} is not one this surface names; it is "
                f"one of {list(runtime_identity.FAMILIES)}. A family is the "
                f"KIND of agent, not a model and not an account."
            )
        for name, value in (("account", self.account), ("profile", self.profile),
                            ("instance_id", self.instance_id)):
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be a non-empty string when set")
            if value.strip() != value:
                raise ValueError(f"{name} must not be padded with whitespace")
            if runtime_identity.is_a_selector(value):
                raise ValueError(
                    f"{name} {value!r} is the query vocabulary, which names a set "
                    f"of runtimes rather than one"
                )

    def credential(self) -> str | None:
        """Read the credential at the anchor. Never logged, never returned."""
        try:
            text = self.credential_anchor.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            return None
        except OSError:
            return None
        return text or None


@dataclass(frozen=True)
class AuthBinding:
    pat_anchor: Path | None = None
    owner_grant_anchor: Path | None = None
    # The human login proof for the consent page: a `0600` file holding
    # `<username>:$scrypt$<n>$<r>$<p>$<salt>$<hash>`. When present it REPLACES
    # the PAT as the way a person approves an authorization — the operator types
    # a username and password instead of pasting a token. The PAT is still what
    # an API client authenticates with, unless `pat_enabled` is false.
    password_anchor: Path | None = None
    # Whether a PAT may authenticate an MCP request at all. True preserves the
    # behaviour of every deployment that predates password login; a deployment
    # that wants the browser login to be the only door sets it false. The code
    # path is never removed, so turning it back on is one line of YAML.
    pat_enabled: bool = True
    #: How long a browser login lasts. Long enough that authorizing three MCP
    #: clients in one sitting asks for the password once; short enough that a
    #: borrowed laptop does not stay authorized overnight.
    session_ttl: int = 8 * 3600
    #: The TOTP secret anchor. Absent means password-only login; present means
    #: the consent form also asks for the six-digit code.
    totp_anchor: Path | None = None
    oauth_enabled: bool = True
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 24 * 3600
    #: Whether a refresh replaces the token it was presented with. Rotation is
    #: what makes reuse detection possible — a rotated token turning up a second
    #: time is the signature of a stolen chain — and it is also what a client
    #: with two refreshes in flight trips over. True keeps that protection, and
    #: every deployment that never writes this key keeps it. False makes the
    #: refresh token a standing credential the client reuses: the price is that
    #: a replayed token can no longer be told apart from an honest one, the
    #: return is a session that cannot die of its own race.
    refresh_rotate: bool = True
    #: How long a rotated token is still honoured after it was rotated: inside
    #: this window a replay answers with the token that replaced it rather than
    #: revoking the chain. 0 means no grace — every replay revokes, which is the
    #: earlier behaviour. A client race is seconds wide; the window does not
    #: have to be large to cover the case it exists for.
    refresh_rotation_grace_seconds: int = 0
    # Scopes this surface issues. A ChatGPT connector is granted the read set
    # plus `fleet:send` / `fleet:create` only if the operator adds them here —
    # this tuple is the ceiling the consent form may present, and the operator
    # still ticks each box. `fleet:create` is listed beside `fleet:send` rather
    # than folded into it: the two are granted separately, so clearing one box
    # leaves the other exactly as it was.
    #
    # The default is `DEFAULT_GRANTABLE_SCOPES` — read and send, and **not**
    # create. A deployment that never wrote the word "create" has not authorized
    # creation, so the omit-the-key case must be the narrow one; an operator who
    # wants a create-capable connector says so per client, in this tuple. (The
    # earlier revision defaulted to all three, which made "create" the thing you
    # got by not thinking about it.)
    grantable_scopes: tuple[str, ...] = DEFAULT_GRANTABLE_SCOPES
    # The second consent proof: a whitelisted Feishu identity instead of the
    # owner's PAT. Absent — the default — means this deployment has no Feishu
    # route at all and the OAuth surface is exactly what it was. It is *not*
    # enabled by default and is not enabled by writing a half-filled block: an
    # enabled block with no identity in it is refused at load, because a
    # deployment that believes SSO works and has nobody who can use it is a
    # support ticket rather than a configuration.
    feishu: FeishuBinding | None = None


@dataclass(frozen=True)
class ServerBinding:
    bind: str = "127.0.0.1"
    port: int = 3401
    public_base_url: str | None = None
    # The MCP path. Kept configurable because a reverse proxy in front of this
    # may strip a prefix, and a service that hard-codes its own path cannot be
    # put behind one without editing code.
    mcp_path: str = "/mcp"
    max_body_bytes: int = 1_000_000


@dataclass(frozen=True)
class CreateGrant:
    """What may be asked for *on one project*, once creation is on for a device.

    Two independent ceilings, both of them narrow by default:

    *   **Which projects.** The grant exists only for the projects named as keys
        of `PolicyBinding.create_allow`; a project that is not a key is refused
        before any provider is asked. There is no "any project" default, because
        a project id is what decides which working tree, which credentials and
        which reachable services a minted session inherits — so an allowlist
        that is empty by omission must mean "none", not "all".
    *   **Which permission mode and which model the caller may name.** Empty
        means the caller may name neither, and the provider's own default
        applies. This is the ceiling that stops `create` from being a way to
        widen one's own authority: without it, a caller holding the create scope
        could mint a session that runs with `bypassPermissions`, which is a
        larger grant than messaging a session an operator already authorized.
        A model is here for the same shape of reason — a model name selects a
        backend, and a backend not named in the binding is not one the operator
        considered.

    The ceilings are *per project* rather than per policy because that is the
    scope at which the answer is actually known: a project whose sessions are
    read-only research notes and a project whose sessions touch a live service
    do not deserve the same latitude, and one flat allowlist would have to
    answer for both with a single word.
    """

    permission_modes: tuple[str, ...] = ()
    models: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {"permission_modes": list(self.permission_modes),
                "models": list(self.models)}


@dataclass(frozen=True)
class PolicyBinding:
    # Explicit owners of exact native namespaces, configured once. Other
    # principals and independently bound account profiles retain their floors.
    owner_max: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # Sessions this runtime is additionally authorized to drive. Control into a
    # session is granted per session by an operator, never by the caller naming
    # an id — see `APPROVAL_REQUIRED`.
    send_allow: tuple[str, ...] = ()
    # `send_allow: ["*"]` would make the per-session grant meaningless. It is
    # refused at load rather than at the first send, so a configuration mistake
    # is an outage on start rather than a silent widening at 3am.
    allow_wildcard_send: bool = False
    # Who may drive which device, by verb: `{principal name: {device_ref:
    # [verbs]}}`. This is the third axis the other two gates do not have.
    #
    # `send_allow` answers "which sessions", and a device's `allow_send` answers
    # "may this runtime reach it at all" — neither answers *who*. `send_allow:
    # ["*"]` therefore reads as "every session" to every caller holding
    # `fleet:send`, which is a broader statement than any operator means to make
    # and is why the wildcard is refused outright below.
    #
    # Three exact-match dimensions, no wildcard in any of them:
    #
    # *   **Principal.** `Principal.name` — the identity the credential produced,
    #     never a parameter. For OAuth that is `oauth:<client_id>`; see
    #     `load_operator_grants` for why that is the stable half of the token and
    #     `sub` is not.
    # *   **Device.** The `device:<id>` ref, and additionally the device must be
    #     eligible by ownership (see `GRANT_ELIGIBLE_OWNERSHIP`).
    # *   **Verb.** Drawn from `OPERATOR_GRANT_VERBS`. `send` here is a verb, not a
    #     mode: it admits `fleet_send_message` and nothing else, and the three
    #     planned verbs are refused rather than inherited.
    #
    # Empty — the default — is no grant to anyone on any device. A principal
    # not named here keeps exactly the reach it had before this field existed.
    #
    # Named `operator_grants` and not `owner_grants` because `AuthBinding` in
    # this same file already has `owner_grant_anchor`, which is a **credential**
    # — the anchor that lets an operator become the owner. Two keys one word
    # apart, one a path to a secret and one a table of authority, is the kind of
    # pair that gets edited by mistake. The axis here is the operator, not the
    # credential.
    operator_grants: dict[str, dict[str, tuple[str, ...]]] = field(default_factory=dict)
    # Projects this runtime may create sessions on, mapped to that project's
    # ceiling on the caller-supplied `permissionMode` and `model`. A device that
    # sets `allow_create: true` is authorized to be minted on; *which* projects
    # is this mapping, and an empty mapping means no project at all. Everything
    # the device gate refuses, this gate refuses one level in — see `CreateGrant`
    # for why the inner ceiling is the narrow one.
    create_allow: dict[str, CreateGrant] = field(default_factory=dict)
    # Explicit runtime-and-project defaults are independent of the project's
    # upper ceilings. Absent entries preserve existing provider defaults. No
    # new runtime inherits model credentials or bypass mode from its neighbor.
    runtime_create_defaults: dict[str, dict[str, RuntimeProjectDefaults]] = field(default_factory=dict)
    # `create_allow: {"*": ...}` would make the project allowlist meaningless,
    # exactly as a wildcard send does. Refused at load for the same reason: a
    # configuration mistake should be an outage on start, not a silent widening
    # at 3am.
    allow_wildcard_create: bool = False
    # Sessions this runtime is additionally authorized to *interrupt*. A
    # separate list from `send_allow` on purpose, and not a superset of it: an
    # operator who authorized messaging a session has not thereby authorized
    # ending its run, and keeping one list would make the second grant
    # unachievable for anyone who needs the first but not the second.
    #
    # There is no inner ceiling to write down here, unlike `create_allow` — the
    # caller names a session and nothing else. `session_interrupt`'s own
    # argument list is one string, so there is no caller-supplied mode, model or
    # path that could widen the grant; the whole of "how much" is the device
    # flag and this list.
    control_allow: tuple[str, ...] = ()
    # `control_allow: ["*"]` would make the per-session grant meaningless,
    # exactly as a wildcard send does. Refused at load, same reason.
    allow_wildcard_control: bool = False
    max_read_messages: int = 40
    max_message_chars: int = 8000
    read_redact: bool = True
    sessions_cache_seconds: float = 0.0
    discovery_timeout_seconds: float = 5.0
    discovery_workers: int = 4

    def __post_init__(self):
        from .discovery import discovery_settings
        discovery_settings(self.discovery_timeout_seconds, self.discovery_workers)
        object.__setattr__(self, "owner_max", load_owner_max(self.owner_max))


@dataclass(frozen=True)
class AuditBinding:
    path: Path
    max_bytes: int = 8 * 1024 * 1024
    rate_limit_per_minute: int = 240


@dataclass(frozen=True)
class FleetConfig:
    source: Path | None
    local_device_ref: str
    providers: dict[str, ProviderBinding] = field(default_factory=dict)
    auth: AuthBinding = field(default_factory=AuthBinding)
    server: ServerBinding = field(default_factory=ServerBinding)
    policy: PolicyBinding = field(default_factory=PolicyBinding)
    audit: AuditBinding | None = None
    registry_path: Path | None = None
    #: Which OS account on this device is the device's OWN — the one the bare
    #: runtime tag belongs to. It is authored by the operator, never inferred:
    #: a hostname, a login name or a path all happen to be right until the day
    #: one of them is not, and a guessed owner silently re-labels every runtime
    #: that was derived from it.
    #:
    #: Absent means the device's own account is not established, and a binding
    #: that DECLARES an account or a profile is then refused rather than checked
    #: against a guess. A binding that declares neither is unaffected, which is
    #: what keeps a binding written before these axes existed loading as it did.
    device_default_account: str | None = None

    def __post_init__(self):
        for key, binding in self.providers.items():
            if not binding.multiplex_native_families:
                continue
            if key != provider_key(binding.device_ref, binding.runtime):
                raise ValueError("multiplexed binding key disagrees with its device/runtime")
            if (binding.account is not None
                    and (self.device_default_account is None
                         or binding.account != self.device_default_account)):
                raise ValueError("multiplexed account must be the declared device owner")
            if any(other.device_ref == binding.device_ref and other.runtime == "codex"
                   for other in self.providers.values()):
                raise ValueError("multiplexed base conflicts with an explicit Codex binding")
        for namespaces in self.policy.owner_max.values():
            for namespace in namespaces:
                device, _, runtime = namespace.partition("|")
                binding = self.namespace_binding(device, runtime or BASE_RUNTIME)
                if binding is None or not binding.enabled or binding.owner_access is None:
                    raise ValueError("owner_max requires an enabled binding with an owner_access profile")

    def session_binding(self, key: SessionKey) -> ProviderBinding | None:
        return self.namespace_binding(key.device_ref, key.provider)

    def namespace_binding(self, device_ref: str, namespace: str) -> ProviderBinding | None:
        exact = self.provider_for(device_ref, namespace)
        if exact is not None:
            return exact
        base = self.provider_for(device_ref)
        if (namespace == "codex" and base is not None and base.enabled
                and base.multiplex_native_families):
            return base
        return None

    def provider_for(self, device_ref: str,
                     runtime: str = BASE_RUNTIME) -> ProviderBinding | None:
        """The binding for one runtime on one device, or `None`.

        `runtime` defaults to the device's own, so every call site written
        before a device could serve two means the same thing it meant then.
        """
        return self.providers.get(provider_key(device_ref, runtime))

    def describe(self) -> dict[str, Any]:
        """A summary safe to emit. Anchors are shown as paths, never values."""
        return {
            "local_device_ref": self.local_device_ref,
            "registry_path": str(self.registry_path) if self.registry_path else None,
            "providers": {
                ref: {
                    "device_ref": b.device_ref,
                    "runtime": b.runtime,
                    "multiplex_native_families": b.multiplex_native_families,
                    "owner_access": ({"owner_approved": True,
                                      "project_roots": dict(b.owner_access.project_roots),
                                      "permission_mode": b.owner_access.permission_mode}
                                     if b.owner_access else None),
                    "kind": b.kind,
                    "endpoint": b.endpoint,
                    "enabled": b.enabled,
                    "allow_send": b.allow_send,
                    "allow_create": b.allow_create,
                    "allow_control": b.allow_control,
                    "credential_anchor_present": b.credential_anchor.exists(),
                }
                for ref, b in sorted(self.providers.items())
            },
            "server": {"bind": self.server.bind, "port": self.server.port,
                       "mcp_path": self.server.mcp_path},
            "policy": {
                "owner_max": {p: list(v) for p, v in self.policy.owner_max.items()},
                "send_allow": list(self.policy.send_allow),
                "control_allow": list(self.policy.control_allow),
                "create_allow": {
                    project: grant.as_dict()
                    for project, grant in sorted(self.policy.create_allow.items())
                },
                # Grants are reported as their *shape* — how many principals,
                # which devices, which verbs — and not as the principal names.
                # A principal name is a credential identifier (`oauth:<client_id>`,
                # see `fleet.py#Principal`), and this summary is the one policy
                # view that is safe to print at start-up. The operator-facing
                # question "who holds what" is answered per call instead, by
                # `fleet_list_devices`, which reports the caller's own grant and
                # so cannot leak a different principal's.
                "operator_grants": {
                    "principals": len(self.policy.operator_grants),
                    "devices": sorted(
                        {ref for per_device in self.policy.operator_grants.values()
                         for ref in per_device}
                    ),
                    "verbs": sorted(
                        {verb for per_device in self.policy.operator_grants.values()
                         for verbs in per_device.values() for verb in verbs}
                    ),
                },
                "read_redact": self.policy.read_redact,
            },
            "grantable_scopes": list(self.auth.grantable_scopes),
            # The second consent proof, reported as its shape — on or off, which
            # application, how many identities — and never as the anchor path.
            # Presence of the anchor is the same fact `credential_anchor_present`
            # reports for a provider: whether a secret is configured, not where
            # it is or what it holds.
            "feishu": (
                {
                    "enabled": self.auth.feishu.enabled,
                    "app_scope": self.auth.feishu.app_scope,
                    "identities": len(self.auth.feishu.whitelist),
                    "secret_anchor_present": bool(
                        self.auth.feishu.secret_anchor and self.auth.feishu.secret_anchor.exists()
                    ),
                }
                if self.auth.feishu is not None else None
            ),
        }


def default_config_path() -> Path | None:
    override = os.environ.get("FLEET_MCP_CONFIG")
    if override:
        return Path(_expand(override))
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return None


def _name_list(value: Any, what: str) -> tuple[str, ...]:
    """A list of strings, de-duplicated, order preserved, refusals not silent."""
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise refuse(BAD_REQUEST, f"{what} must be a list of strings")
    seen: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise refuse(BAD_REQUEST, f"{what} must contain non-empty strings")
        name = item.strip()
        if name not in seen:
            seen.append(name)
    return tuple(seen)


def _grantable_scopes(auth_raw: dict[str, Any]) -> tuple[str, ...]:
    """The scopes this deployment will issue, checked against the one vocabulary.

    Absent means `DEFAULT_GRANTABLE_SCOPES` — read and send. Present means
    exactly what was written, and every name in it must be a scope this build
    implements: a binding that says `fleet:control` is a binding that expects a
    verb this code does not have, and minting a token for it would hand a client
    authority whose every exercise silently does nothing. Refused at load, so
    the mistake is an outage on start rather than a token that half-works.
    """
    if "grantable_scopes" not in auth_raw or auth_raw.get("grantable_scopes") is None:
        return DEFAULT_GRANTABLE_SCOPES
    declared = _name_list(auth_raw.get("grantable_scopes"), "auth.grantable_scopes")
    unknown = [s for s in declared if s not in ALLOWED_SCOPES]
    if unknown:
        raise refuse(
            BAD_REQUEST,
            f"auth.grantable_scopes names {sorted(unknown)}, which this build "
            f"does not implement. Known scopes: {list(ALLOWED_SCOPES)}.",
        )
    return declared


def _feishu_whitelist(auth_raw: dict[str, Any], app_scope: str) -> tuple[FeishuWhitelistEntry, ...]:
    """Parse `auth.feishu.whitelist` into entries, or refuse.

    An entry names an identity the operator has *verified* — the tenant it
    belongs to and its stable user id. `app_scope` may be written per entry to
    support a second application later; omitted, it is the binding's own.

    What this will not do is invent an identity. There is no default entry, no
    "the first login becomes the operator" bootstrap, and `user_id: "*"` is not
    a wildcard — it is a string that will never match a real open_id, which is
    the honest outcome for an operator who has not yet decided who is allowed in.
    """
    raw = (auth_raw.get("feishu") or {}).get("whitelist") or []
    if isinstance(raw, dict) or not isinstance(raw, (list, tuple)):
        raise refuse(BAD_REQUEST, "auth.feishu.whitelist must be a list of identity entries")
    entries: list[FeishuWhitelistEntry] = []
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise refuse(BAD_REQUEST, f"auth.feishu.whitelist[{index}] must be a mapping")
        tenant = str(item.get("tenant_key") or "").strip()
        user = str(item.get("user_id") or "").strip()
        if not tenant or not user:
            raise refuse(
                BAD_REQUEST,
                f"auth.feishu.whitelist[{index}] must name both `tenant_key` and "
                f"`user_id`; an identity is matched on its tenant as well as its "
                f"user id, because an open_id is unique within a tenant and not "
                f"across tenants.",
            )
        entry_scope = str(item.get("app_scope") or app_scope).strip()
        entries.append(FeishuWhitelistEntry(
            tenant_key=tenant,
            user_id=user,
            app_scope=entry_scope,
            label=str(item.get("label") or "").strip(),
        ))
    return tuple(entries)


def _feishu_binding(auth_raw: dict[str, Any]) -> FeishuBinding | None:
    """The Feishu block, or `None` when the deployment did not write one.

    Absent is absent: a deployment with no `auth.feishu` key gets `None`, which
    every caller reads as "no such route". Present-but-disabled is parsed for
    completeness but grants nothing. Present-and-enabled must be whole — app id,
    secret anchor, redirect and at least one identity — or the load is refused.
    """
    raw = auth_raw.get("feishu")
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise refuse(BAD_REQUEST, "auth.feishu must be a mapping")
    app_scope = str(raw.get("app_scope") or "").strip()
    enabled = bool(raw.get("enabled", False))
    whitelist = _feishu_whitelist(auth_raw, app_scope)
    secret_anchor = raw.get("secret_anchor")
    binding = FeishuBinding(
        enabled=enabled,
        app_scope=app_scope,
        app_id=(str(raw["app_id"]).strip() if raw.get("app_id") else None),
        secret_anchor=(Path(_expand(str(secret_anchor))) if secret_anchor else None),
        redirect_uri=(str(raw["redirect_uri"]).strip() if raw.get("redirect_uri") else None),
        base_url=str(raw.get("base_url") or FeishuBinding.base_url),
        authorize_path=str(raw.get("authorize_path") or FeishuBinding.authorize_path),
        token_path=str(raw.get("token_path") or FeishuBinding.token_path),
        userinfo_path=str(raw.get("userinfo_path") or FeishuBinding.userinfo_path),
        scope=(str(raw["scope"]) if raw.get("scope") else None),
        whitelist=whitelist,
    )
    if not enabled:
        return binding
    # Enabled: name every missing piece at once, so the operator fixes the block
    # in one edit rather than one restart per field.
    missing = [
        key for key, value in (("app_scope", binding.app_scope), ("app_id", binding.app_id),
                               ("secret_anchor", binding.secret_anchor),
                               ("redirect_uri", binding.redirect_uri))
        if not value
    ]
    if missing:
        raise refuse(
            BAD_REQUEST,
            f"auth.feishu.enabled is true but {sorted(missing)} is unset. A "
            f"half-configured sign-in is refused rather than attempted: it would "
            f"fail at the moment a person tried to use it, which is the worst "
            f"time to find out.",
        )
    if not whitelist:
        raise refuse(
            BAD_REQUEST,
            "auth.feishu.enabled is true but `auth.feishu.whitelist` is empty, so "
            "no identity could ever sign in. Write the identities the operator "
            "has verified — each one's `tenant_key` and `user_id` — or leave "
            "`enabled` false. This surface will not admit an identity nobody "
            "wrote down, and it will not guess which ones are yours.",
        )
    return binding


def _create_allow(policy_raw: dict[str, Any]) -> dict[str, CreateGrant]:
    """Parse `policy.create_allow` into project grants.

    Two shapes are accepted for each entry, because the common case is a grant
    with no inner ceiling and writing `project: {}` for it is noise:

        create_allow:
          p-notes:                 # no caller-supplied mode or model at all
          p-research:
            permission_modes: [plan]
            models: [claude-sonnet-5-5]

    Both are the same object; the bare form is `CreateGrant()`.
    """
    raw = policy_raw.get("create_allow")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise refuse(
            BAD_REQUEST,
            "policy.create_allow must be a mapping of project id to its grant "
            "(or to nothing, for a grant with no caller-supplied mode or model)",
        )
    grants: dict[str, CreateGrant] = {}
    for project, entry in raw.items():
        if not isinstance(project, str) or not project.strip():
            raise refuse(BAD_REQUEST, "policy.create_allow project ids must be non-empty strings")
        name = project.strip()
        # `==` and not `in`: a project id is one name, and a substring test here
        # would refuse a project that merely contains an asterisk — a false
        # positive on the one gate whose whole job is to be exact.
        if name == "*" and not bool(policy_raw.get("allow_wildcard_create", False)):
            raise refuse(
                BAD_REQUEST,
                "policy.create_allow contains '*'. A wildcard makes the project "
                "allowlist meaningless; set `allow_wildcard_create: true` if "
                "that is genuinely intended.",
            )
        if entry is None:
            grants[name] = CreateGrant()
            continue
        if not isinstance(entry, dict):
            raise refuse(
                BAD_REQUEST,
                f"policy.create_allow[{name!r}] must be a mapping or empty",
            )
        unknown = set(entry) - {"permission_modes", "models"}
        if unknown:
            raise refuse(
                BAD_REQUEST,
                f"policy.create_allow[{name!r}] has unknown key(s) {sorted(unknown)}; "
                f"known keys: ['models', 'permission_modes']",
            )
        grants[name] = CreateGrant(
            permission_modes=_name_list(
                entry.get("permission_modes"),
                f"policy.create_allow[{name!r}].permission_modes",
            ),
            models=_name_list(entry.get("models"), f"policy.create_allow[{name!r}].models"),
        )
    return grants


def load_operator_grants(policy_raw: dict[str, Any]) -> dict[str, dict[str, tuple[str, ...]]]:
    """Parse `policy.operator_grants` into `{principal: {device_ref: (verbs,)}}`.

        operator_grants:
          "oauth:fleet-XXXXXXXX":
            device:tokyo-shared-dev-01: [send]
            device:work-fj: [send]

    What this parser will not do is the part worth reading.

    **It cannot validate a principal name.** OAuth clients are registered at
    runtime (`register_client`), so at load time the set of names that will
    exist is not knowable, and a check against "the clients I can see" would
    refuse the very grant an operator is trying to write. So a name that does
    not match any credential is accepted here and fails at the first send, with
    a message that names what was presented — see `fleet.py#_owner_grant_verbs`.
    The failure mode is a refusal that says which name it saw, never a grant that
    quietly applies to nobody.

    **It refuses a planned verb.** `control`, `approve` and `navigate` are
    named in `PLANNED_OPERATOR_GRANT_VERBS` and refused at load, because a binding
    that lists one is a binding whose author believes a control verb is live.
    Accepting it would produce a file that reads as authority and a runtime in
    which the verb does nothing — and the next person to debug that has to
    discover, from a silent no-op, which of the two halves is missing.

    **It refuses a wildcard in either dimension.** `"*"` as a principal or as a
    device would make the dimension meaningless in exactly the way
    `allow_wildcard_send` makes `send_allow` meaningless, and an operator who
    wants "all of them" is really asking for the three devices to be written
    out, which is the sentence that can be reviewed.
    """
    raw = policy_raw.get("operator_grants")
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise refuse(
            BAD_REQUEST,
            "policy.operator_grants must be a mapping of principal name to a "
            "mapping of device ref to a list of verbs",
        )
    grants: dict[str, dict[str, tuple[str, ...]]] = {}
    for principal, devices in raw.items():
        if not isinstance(principal, str) or not principal.strip():
            raise refuse(BAD_REQUEST, "policy.operator_grants principal names must be non-empty strings")
        name = principal.strip()
        # `"*"` is the vocabulary a deployment uses to say "every principal that
        # can reach this runtime". It is not a default and it is not implied:
        # a binding that does not write it grants nothing it did not name. The
        # behaviour this field produces is therefore still entirely a property
        # of the file, which is what lets one binding ship open and another
        # ship closed with the same code.
        if not isinstance(devices, dict):
            raise refuse(
                BAD_REQUEST,
                f"policy.operator_grants[{name!r}] must be a mapping of device ref "
                f"to a list of verbs",
            )
        per_device: dict[str, tuple[str, ...]] = {}
        for device_ref, verbs in devices.items():
            if not isinstance(device_ref, str) or not device_ref.strip():
                raise refuse(
                    BAD_REQUEST,
                    f"policy.operator_grants[{name!r}] device refs must be non-empty strings",
                )
            ref = device_ref.strip()
            # `"*"` on the device axis is the same word as on the principal
            # axis: "every enrolled device". It is not a fallback for a typo —
            # an exact ref that names nothing still grants nothing — and the
            # grant eligibility check (`ownership == company`) is applied after
            # this lookup, so a wildcard cannot reach a device a named ref
            # could not.
            if ref != "*" and not ref.startswith("device:"):
                raise refuse(
                    BAD_REQUEST,
                    f"policy.operator_grants[{name!r}] device ref {ref!r} is not a "
                    f"typed ref; device refs are `device:<id>`",
                )
            named = _name_list(verbs, f"policy.operator_grants[{name!r}][{ref!r}]")
            planned = [v for v in named if v in PLANNED_OPERATOR_GRANT_VERBS]
            if planned:
                raise refuse(
                    BAD_REQUEST,
                    f"policy.operator_grants[{name!r}][{ref!r}] names {sorted(planned)}, "
                    f"which this build does not implement. Known verbs: "
                    f"{list(OPERATOR_GRANT_VERBS)}; the planned verbs "
                    f"{list(PLANNED_OPERATOR_GRANT_VERBS)} are recorded in "
                    f"`scopes.py` as scopes and are not live here either.",
                )
            unknown = [v for v in named if v not in OPERATOR_GRANT_VERBS]
            if unknown:
                raise refuse(
                    BAD_REQUEST,
                    f"policy.operator_grants[{name!r}][{ref!r}] names {sorted(unknown)}, "
                    f"which is not a verb. Known verbs: {list(OPERATOR_GRANT_VERBS)}.",
                )
            if named:
                per_device[ref] = named
        if per_device:
            grants[name] = per_device
    return grants


def load_owner_max(raw) -> dict[str, tuple[str, ...]]:
    """Compile `policy.owner_max` — exact principals, or one `"*"` fallback.

    The namespace list is always exact: a device/runtime pair, never a wildcard.
    What `"*"` widens is only the PRINCIPAL axis, and only for principals this
    runtime admits at all — the same shape `policy.operator_grants` already has,
    and for the same reason: an OAuth principal is `oauth:<client_id>`, an opaque
    id that a re-registration can change. Pinning it into a binding makes the
    owner's own project creation depend on an id nobody authored.

    `"*"` is consulted only when the presented principal has no entry of its own.
    An exact entry says what that principal holds, including when the device it
    needs is not in it.
    """
    if not isinstance(raw, dict):
        raise ValueError("policy.owner_max must map exact principals to namespace lists")
    result = {}
    for principal, namespaces in raw.items():
        if (not isinstance(principal, str) or not principal.strip()
                or principal != principal.strip()
                or ("*" in principal and principal != "*")):
            raise ValueError("owner_max requires exact nonempty principal names, or \"*\"")
        if not isinstance(namespaces, (list, tuple)) or not namespaces:
            raise ValueError("owner_max requires a nonempty namespace list")
        selected = []
        for namespace in namespaces:
            if (not isinstance(namespace, str) or namespace != namespace.strip()
                    or "*" in namespace or namespace.count("|") > 1):
                raise ValueError("owner_max requires exact device/runtime keys")
            device, _, runtime = namespace.partition("|")
            if runtime == BASE_RUNTIME or ("|" in namespace and not runtime):
                raise ValueError("the base owner namespace uses its bare device key")
            SessionKey(device, runtime or BASE_RUNTIME, "owner-config")
            if runtime_identity.is_a_selector(runtime):
                raise ValueError("owner_max cannot name a runtime query selector")
            if namespace in selected:
                raise ValueError("duplicate owner_max namespace")
            selected.append(namespace)
        result[principal] = tuple(selected)
    return result


def load_config(path: str | Path | None = None) -> FleetConfig:
    explicit = path if path is not None else default_config_path()
    if explicit is None:
        raise refuse(
            BAD_REQUEST,
            "no Fleet MCP binding found. Looked for "
            + ", ".join(str(p) for p in DEFAULT_CONFIG_PATHS)
            + " (override with FLEET_MCP_CONFIG). This surface has no route to "
            "any device until one is present, and refuses rather than "
            "reporting an empty fleet.",
        )
    source = Path(_expand(str(explicit)))
    closed = refusal_if_closed_root(source)
    if closed:
        raise refuse(BAD_REQUEST, closed)
    try:
        raw = yaml.safe_load(source.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raise refuse(BAD_REQUEST, f"Fleet MCP binding not found at {source}") from None
    except yaml.YAMLError as exc:
        raise refuse(BAD_REQUEST, f"Fleet MCP binding at {source} is not valid YAML: {exc}") from None

    if not isinstance(raw, dict):
        raise refuse(BAD_REQUEST, "Fleet MCP binding must be a mapping")
    schema = raw.get("schema")
    if schema != SCHEMA:
        raise refuse(
            BAD_REQUEST,
            f"Fleet MCP binding declares schema {schema!r}; this surface reads {SCHEMA!r}",
        )

    local_device = raw.get("device_ref")
    if not local_device or not str(local_device).startswith("device:"):
        raise refuse(
            BAD_REQUEST,
            "`device_ref` must name this host with a typed ref (`device:<id>`)",
        )

    # Read before the providers, because a binding's declared identity is
    # checked against it. Never defaulted from a hostname, a login name or a
    # path: those are right until the day they are not, and being wrong here
    # re-labels every runtime derived from the wrong owner.
    device_default_account = raw.get("device_default_account")
    if device_default_account is not None:
        if (not isinstance(device_default_account, str)
                or not device_default_account.strip()
                or runtime_identity.is_a_selector(device_default_account)):
            raise refuse(
                BAD_REQUEST,
                "`device_default_account` must name the OS account this device's "
                "OWN runtime belongs to — a non-empty name that is not the query "
                "vocabulary. It is authored, not discovered.",
            )

    providers: dict[str, ProviderBinding] = {}
    for ref, entry in (raw.get("providers") or {}).items():
        if not isinstance(entry, dict):
            raise refuse(BAD_REQUEST, f"provider {ref} must be a mapping")
        kind = entry.get("kind")
        if kind not in PROVIDER_KINDS:
            raise refuse(
                BAD_REQUEST,
                f"provider {ref} declares unknown kind {kind!r}; known kinds: {list(PROVIDER_KINDS)}",
            )
        # A second runtime on one machine is a second provider entry. The
        # runtime is written either in the key (`device:<id>|<tag>`) or in a
        # `runtime:` field, and never in both without agreeing — a binding whose
        # callers reach it under a different name from the one it declares is a
        # binding nobody can find.
        device_ref, _, keyed_runtime = str(ref).partition("|")
        field_runtime = entry.get("runtime")
        if keyed_runtime and field_runtime and str(field_runtime) != keyed_runtime:
            raise refuse(
                BAD_REQUEST,
                f"provider {ref} names runtime {keyed_runtime!r} in its key and "
                f"{field_runtime!r} in its `runtime` field",
            )
        runtime = str(field_runtime or keyed_runtime or BASE_RUNTIME)
        if not device_ref.startswith("device:"):
            raise refuse(BAD_REQUEST, f"provider key {ref!r} is not a typed device ref")
        # A runtime may not be spelled as a query selector. `all` would pass the
        # provider grammar, so a binding could be filed under a name that every
        # `runtime: all` listing would then have to guess at — was it the
        # selector, or the runtime? Refusing it here keeps the two sets disjoint
        # at the only point where the ambiguity could be introduced, rather than
        # at the point where it would be consulted.
        if runtime_identity.is_a_selector(runtime):
            raise refuse(
                BAD_REQUEST,
                f"provider {ref} names runtime {runtime!r}, which is a query "
                f"selector ({list(runtime_identity.SELECTORS)}) and not a tag. "
                f"A runtime is named `{BASE_RUNTIME}` or `{BASE_RUNTIME}-<account>`.",
            )
        # The runtime must be a tag this surface can address in a session ref;
        # the authority for that grammar is `SessionKey`, so it is asked rather
        # than restated here.
        SessionKey(device_ref, runtime, "binding-validation")
        try:
            providers[provider_key(device_ref, runtime)] = ProviderBinding(
                device_ref=device_ref,
                runtime=runtime,
                kind=kind,
                endpoint=str(entry.get("endpoint") or ""),
                credential_anchor=_anchor(entry.get("credential_anchor"), f"provider {ref} credential_anchor"),
                connect_timeout=float(entry.get("connect_timeout", 5.0)),
                read_timeout=float(entry.get("read_timeout", 45.0)),
                enabled=bool(entry.get("enabled", True)),
                note=entry.get("note"),
                allow_send=bool(entry.get("allow_send", False)),
                allow_create=bool(entry.get("allow_create", False)),
                allow_control=bool(entry.get("allow_control", False)),
                account=entry.get("account"),
                profile=entry.get("profile"),
                family=str(entry.get("family", BASE_RUNTIME)),
                instance_id=entry.get("instance_id"),
                multiplex_native_families=entry.get("multiplex_native_families", False),
                owner_access=parse_owner_access(entry.get("owner_access")),
            )
        except ValueError as exc:
            raise refuse(BAD_REQUEST, f"provider {ref}: {exc}") from None

    # Two set-level questions a single binding cannot answer, asked once the
    # whole set is visible. Both refuse rather than pick a winner: a duplicate
    # here is a declaration that says two things about one runtime, and the
    # silent resolutions — first wins, last wins — differ only in which of the
    # two the operator will be surprised by.
    _instances_seen: dict[str, str] = {}
    for _key, _binding in providers.items():
        # The tag is not taken on trust. A binding may be filed under any valid
        # provider name, so a declaration that disagrees with its own key would
        # leave the operator reading one identity while the router uses another —
        # and across accounts that is a mis-route, not a typo. `runtime_tag` is
        # the one naming authority, so the check is its output against the key
        # rather than a second restatement of the rule.
        _declares_identity = (_binding.account is not None
                              or _binding.profile is not None
                              or _binding.family != BASE_RUNTIME)
        if _declares_identity:
            if device_default_account is None:
                raise refuse(
                    BAD_REQUEST,
                    f"provider {_key} declares an account, profile or family, but "
                    f"this binding does not say which OS account is the device's "
                    f"OWN (`device_default_account`). Without it the tag cannot be "
                    f"checked against the declaration, and guessing an owner from "
                    f"the host would re-label every runtime derived from it.",
                )
            if _binding.account is None:
                raise refuse(
                    BAD_REQUEST,
                    f"provider {_key} declares a profile but no `account`. Which "
                    f"OS account a runtime belongs to is half of what its tag "
                    f"encodes, so it is stated rather than left to the tag.",
                )
            _expected = runtime_identity.runtime_tag(
                _binding.account, device_owner=device_default_account,
                profile=_binding.profile, family=_binding.family)
            if _binding.runtime != _expected:
                raise refuse(
                    BAD_REQUEST,
                    f"provider {_key} is filed under runtime "
                    f"{_binding.runtime!r}, but its own declaration "
                    f"(account={_binding.account!r}, profile={_binding.profile!r}, "
                    f"family={_binding.family!r}) derives {_expected!r}. One tag "
                    f"addresses one runtime, so the declaration and the key must "
                    f"agree; this is refused rather than routed by the tag while "
                    f"the operator reads the declaration.",
                )
        if _binding.instance_id is not None:
            prior = _instances_seen.get(_binding.instance_id)
            if prior is not None and prior != _key:
                raise refuse(
                    BAD_REQUEST,
                    f"two bindings declare the same instance_id "
                    f"{_binding.instance_id!r} ({prior} and {_key}). One "
                    f"incarnation label names one provisioning, so this is "
                    f"refused rather than resolved by declaration order.",
                )
            _instances_seen[_binding.instance_id] = _key
        # A duplicate account/profile pair is deliberately NOT checked for,
        # because the check above makes it unreachable: the tag must equal what
        # the declaration derives, and a tag is one dictionary key, so two
        # entries deriving one identity are two spellings of one key that YAML
        # has already collapsed. A check that cannot fire is not a guard, it is
        # a comment with a traceback attached — and it would read as coverage
        # nobody has.

    auth_raw = raw.get("auth") or {}
    auth = AuthBinding(
        pat_anchor=Path(_expand(auth_raw["pat_anchor"])) if auth_raw.get("pat_anchor") else None,
        owner_grant_anchor=(
            Path(_expand(auth_raw["owner_grant_anchor"])) if auth_raw.get("owner_grant_anchor") else None
        ),
        password_anchor=(
            Path(_expand(auth_raw["password_anchor"])) if auth_raw.get("password_anchor") else None
        ),
        pat_enabled=bool(auth_raw.get("pat_enabled", True)),
        session_ttl=int(auth_raw.get("session_ttl", 8 * 3600)),
        totp_anchor=(Path(_expand(auth_raw["totp_anchor"])) if auth_raw.get("totp_anchor") else None),
        oauth_enabled=bool(auth_raw.get("oauth_enabled", True)),
        access_token_ttl=int(auth_raw.get("access_token_ttl", 3600)),
        refresh_token_ttl=int(auth_raw.get("refresh_token_ttl", 30 * 24 * 3600)),
        refresh_rotate=bool(auth_raw.get("refresh_rotate", True)),
        refresh_rotation_grace_seconds=int(auth_raw.get("refresh_rotation_grace_seconds", 0)),
        grantable_scopes=_grantable_scopes(auth_raw),
        feishu=_feishu_binding(auth_raw),
    )

    server_raw = raw.get("server") or {}
    server = ServerBinding(
        bind=str(server_raw.get("bind", "127.0.0.1")),
        port=int(server_raw.get("port", 3401)),
        public_base_url=server_raw.get("public_base_url"),
        mcp_path=str(server_raw.get("mcp_path", "/mcp")),
        max_body_bytes=int(server_raw.get("max_body_bytes", 1_000_000)),
    )

    policy_raw = raw.get("policy") or {}
    send_allow = tuple(policy_raw.get("send_allow") or ())
    wildcard_send = bool(policy_raw.get("allow_wildcard_send", False))
    # `allow_wildcard_send` used to be the switch that made `send_allow: ["*"]`
    # legal. It is refused outright now, and the refusal is a breaking change to
    # any deployment that set it — which is said here rather than discovered.
    #
    # Why refuse rather than merely deprioritize: deprioritizing leaves the old
    # grant in place. A deployment with `send_allow: ["*"]` and the wildcard on
    # still hands every credential holding `fleet:send` the whole fleet, so an
    # operator who registers a *second* OAuth client gets the broad authority
    # without ever editing `operator_grants` — the grant nobody wrote, arriving
    # through the door this change exists to close. A named-principal table is
    # only the answer if it is the only way to say "all of them", and that means
    # the wildcard has to stop working, not merely stop being consulted.
    #
    # The message is the migration: it names the three devices, so an operator
    # can act on it without reading this code.
    if wildcard_send or "*" in send_allow:
        # Name the trigger that actually fired: an operator who wrote
        # `send_allow: ["*"]` and one who wrote `allow_wildcard_send: true` are
        # editing two different lines, and a message that says "or" makes the
        # first of them re-read a key they never set.
        if wildcard_send and "*" in send_allow:
            trigger = "`send_allow` contains '*' and `allow_wildcard_send` is set"
        elif wildcard_send:
            trigger = "`allow_wildcard_send` is set"
        else:
            trigger = "`send_allow` contains '*'"
        raise refuse(
            BAD_REQUEST,
            f"policy.{trigger}. This grant is global, not per principal: every "
            f"credential holding `fleet:send` receives it. It is refused rather "
            f"than honoured, because the replacement is a named table and a "
            f"working wildcard is a second way to say the same thing. Replace it "
            f"with `policy.operator_grants: {{<principal>: {{<device_ref>: [send]}}}}` "
            f"naming the devices individually — for example "
            f"`device:tokyo-shared-dev-01`, `device:work-fj`, `device:llm-lab-dell` "
            f"— and drop `allow_wildcard_send`. Per-session `send_allow` entries "
            f"that name a session ref and not '*' are unaffected.",
        )
    control_allow = tuple(policy_raw.get("control_allow") or ())
    if "*" in control_allow and not bool(policy_raw.get("allow_wildcard_control", False)):
        raise refuse(
            BAD_REQUEST,
            "policy.control_allow contains '*'. A wildcard makes the per-session "
            "grant meaningless; set `allow_wildcard_control: true` if that is "
            "genuinely intended.",
        )
    create_grants = _create_allow(policy_raw)
    try:
        runtime_defaults = parse_runtime_create_defaults(
            policy_raw.get("runtime_create_defaults"),
            known_runtimes=set(providers), project_ceilings=create_grants)
    except PolicyError as exc:
        raise refuse(BAD_REQUEST, f"policy.runtime_create_defaults: {exc}") from None
    # A route name alone does not isolate credentials. A newly configured
    # account-level default must not be wired to the same gateway credential
    # anchor or endpoint as another runtime on the SAME physical device.
    # Compare canonical paths as well as spelling to reject symlink aliases.
    for key in runtime_defaults:
        binding = providers[key]
        for other_key, other in providers.items():
            if other_key == key or binding.device_ref != other.device_ref:
                continue
            if (binding.credential_anchor.resolve() == other.credential_anchor.resolve()
                    or binding.endpoint == other.endpoint):
                raise refuse(
                    BAD_REQUEST,
                    "policy.runtime_create_defaults requires distinct per-account "
                    "provider credential anchors and endpoints; owner must "
                    "repair the device binding before creating sessions",
                )
    from .discovery import discovery_settings
    try:
        discovery_timeout, discovery_workers = discovery_settings(
            policy_raw.get("discovery_timeout_seconds", 5.0),
            policy_raw.get("discovery_workers", 4))
    except ValueError as exc:
        raise refuse(BAD_REQUEST, str(exc)) from None
    try:
        owner_max = load_owner_max(policy_raw.get("owner_max", {}))
    except ValueError as exc:
        raise refuse(BAD_REQUEST, str(exc)) from None
    policy = PolicyBinding(
        owner_max=owner_max,
        send_allow=send_allow,
        allow_wildcard_send=wildcard_send,
        operator_grants=load_operator_grants(policy_raw),
        create_allow=create_grants,
        runtime_create_defaults=runtime_defaults,
        allow_wildcard_create=bool(policy_raw.get("allow_wildcard_create", False)),
        control_allow=control_allow,
        allow_wildcard_control=bool(policy_raw.get("allow_wildcard_control", False)),
        max_read_messages=int(policy_raw.get("max_read_messages", 40)),
        max_message_chars=int(policy_raw.get("max_message_chars", 8000)),
        read_redact=bool(policy_raw.get("read_redact", True)),
        sessions_cache_seconds=float(policy_raw.get("sessions_cache_seconds", 0.0)),
        discovery_timeout_seconds=discovery_timeout,
        discovery_workers=discovery_workers,
    )

    audit_raw = raw.get("audit") or {}
    audit = None
    if audit_raw.get("path"):
        audit = AuditBinding(
            path=Path(_expand(audit_raw["path"])),
            max_bytes=int(audit_raw.get("max_bytes", 8 * 1024 * 1024)),
            rate_limit_per_minute=int(audit_raw.get("rate_limit_per_minute", 240)),
        )

    registry_raw = raw.get("registry_path")
    registry_path = Path(_expand(registry_raw)) if registry_raw else None

    try:
        return FleetConfig(
            source=source,
            local_device_ref=str(local_device),
            providers=providers,
            auth=auth,
            server=server,
            policy=policy,
            audit=audit,
            registry_path=registry_path,
            device_default_account=device_default_account,
        )
    except ValueError as exc:
        raise refuse(BAD_REQUEST, str(exc)) from None
