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

SCHEMA = "serveros/fleet-mcp.local/v1"

# Kinds this surface knows how to drive. The set is closed on purpose: a typo in
# a binding must not silently produce a device that reports "no sessions".
PROVIDER_KINDS = ("cloudcli-mcp-http", "fleet-node")


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
    grantable_scopes: tuple[str, ...] = ("fleet:read", "fleet:send", "fleet:create")


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
class PolicyBinding:
    # Sessions this runtime is additionally authorized to drive. Control into a
    # session is granted per session by an operator, never by the caller naming
    # an id — see `APPROVAL_REQUIRED`.
    send_allow: tuple[str, ...] = ()
    # `send_allow: ["*"]` would make the per-session grant meaningless. It is
    # refused at load rather than at the first send, so a configuration mistake
    # is an outage on start rather than a silent widening at 3am.
    allow_wildcard_send: bool = False
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
                    "credential_anchor_present": b.credential_anchor.exists(),
                }
                for ref, b in sorted(self.providers.items())
            },
            "server": {"bind": self.server.bind, "port": self.server.port,
                       "mcp_path": self.server.mcp_path},
            "policy": {
                "send_allow": list(self.policy.send_allow),
                "read_redact": self.policy.read_redact,
            },
        }


def default_config_path() -> Path | None:
    override = os.environ.get("FLEET_MCP_CONFIG")
    if override:
        return Path(_expand(override))
    for candidate in DEFAULT_CONFIG_PATHS:
        if candidate.exists():
            return candidate
    return None


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
        grantable_scopes=tuple(
            auth_raw.get("grantable_scopes") or ("fleet:read", "fleet:send", "fleet:create")
        ),
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
    if "*" in send_allow and not bool(policy_raw.get("allow_wildcard_send", False)):
        raise refuse(
            BAD_REQUEST,
            "policy.send_allow contains '*'. A wildcard makes the per-session "
            "grant meaningless; set `allow_wildcard_send: true` if that is "
            "genuinely intended.",
        )
    policy = PolicyBinding(
        send_allow=send_allow,
        allow_wildcard_send=bool(policy_raw.get("allow_wildcard_send", False)),
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
