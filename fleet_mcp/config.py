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
from .local_roots import binding_candidates, refusal_if_closed_root
from .scopes import ALLOWED_SCOPES, DEFAULT_GRANTABLE_SCOPES

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
OPERATOR_GRANT_VERBS = ("send", "control", "create")
PLANNED_OPERATOR_GRANT_VERBS = ("approve", "navigate")

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


@dataclass(frozen=True)
class ProviderBinding:
    device_ref: str
    kind: str
    endpoint: str
    credential_anchor: Path
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
    oauth_enabled: bool = True
    access_token_ttl: int = 3600
    refresh_token_ttl: int = 30 * 24 * 3600
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

    def provider_for(self, device_ref: str) -> ProviderBinding | None:
        return self.providers.get(device_ref)

    def describe(self) -> dict[str, Any]:
        """A summary safe to emit. Anchors are shown as paths, never values."""
        return {
            "local_device_ref": self.local_device_ref,
            "registry_path": str(self.registry_path) if self.registry_path else None,
            "providers": {
                ref: {
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
        if name == "*":
            raise refuse(
                BAD_REQUEST,
                "policy.operator_grants names '*' as a principal. A wildcard "
                "principal would give every credential that can reach this "
                "runtime the same authority — the global grant this field "
                "exists to replace. Name the principals.",
            )
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
            if ref == "*":
                raise refuse(
                    BAD_REQUEST,
                    f"policy.operator_grants[{name!r}] names '*' as a device. A "
                    f"wildcard device would make the device axis meaningless; "
                    f"write the device refs out.",
                )
            if not ref.startswith("device:"):
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
        if not str(ref).startswith("device:"):
            raise refuse(BAD_REQUEST, f"provider key {ref!r} is not a typed device ref")
        providers[ref] = ProviderBinding(
            device_ref=ref,
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
        )

    auth_raw = raw.get("auth") or {}
    auth = AuthBinding(
        pat_anchor=Path(_expand(auth_raw["pat_anchor"])) if auth_raw.get("pat_anchor") else None,
        owner_grant_anchor=(
            Path(_expand(auth_raw["owner_grant_anchor"])) if auth_raw.get("owner_grant_anchor") else None
        ),
        oauth_enabled=bool(auth_raw.get("oauth_enabled", True)),
        access_token_ttl=int(auth_raw.get("access_token_ttl", 3600)),
        refresh_token_ttl=int(auth_raw.get("refresh_token_ttl", 30 * 24 * 3600)),
        grantable_scopes=_grantable_scopes(auth_raw),
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
    policy = PolicyBinding(
        send_allow=send_allow,
        allow_wildcard_send=wildcard_send,
        operator_grants=load_operator_grants(policy_raw),
        create_allow=_create_allow(policy_raw),
        allow_wildcard_create=bool(policy_raw.get("allow_wildcard_create", False)),
        control_allow=control_allow,
        allow_wildcard_control=bool(policy_raw.get("allow_wildcard_control", False)),
        max_read_messages=int(policy_raw.get("max_read_messages", 40)),
        max_message_chars=int(policy_raw.get("max_message_chars", 8000)),
        read_redact=bool(policy_raw.get("read_redact", True)),
        sessions_cache_seconds=float(policy_raw.get("sessions_cache_seconds", 0.0)),
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

    return FleetConfig(
        source=source,
        local_device_ref=str(local_device),
        providers=providers,
        auth=auth,
        server=server,
        policy=policy,
        audit=audit,
        registry_path=registry_path,
    )
