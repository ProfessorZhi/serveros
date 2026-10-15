#!/usr/bin/env python3
"""Read-only, offline gate for the Next ingress's OWN Tailscale node.

`next_dual_stack_preflight` decides that the Next service cannot silently
replace V1: separate unit, roots, port, published URL, plugin id. It says
nothing about the *network identity* the Next ingress publishes through, and
that is a second, independent way to collide with V1 — by reusing the existing
`tailscaled` state, socket, service or Funnel, or by pointing a new Funnel at
V1's loopback port.

This module is that second gate. It is NOT a deployment command: it never opens
a socket, never reads a running binding, never talks to `tailscaled`, never
writes a service and never starts a daemon. Operators supply observed,
non-secret identity values; the gate decides whether the plan is isolated and
at which stage of the ladder it honestly stands.

Three properties it exists to keep:

  * **No secret can enter.** A Tailscale auth key must never be issued, stored,
    copied or recorded by this tooling — registration is an interactive owner
    login in the owner's own browser. The plan schema has no field for a key,
    and any credential-shaped field name is refused *by name, with no value
    echoed*.

  * **Provisioning a route is not serving it.** Publishing a Funnel at a
    restricted loopback backend that is not running yet is a real, checkable
    step — and it is exactly the step at which HTTP `/health` CANNOT answer.
    The ladder therefore separates "the route and its certificate exist" from
    "the process behind it is live" from "the public HTTPS surface actually
    answered". A run sheet that asked for a 200 before the unit starts would
    block its own next stage.

  * **A stage is not evidence of the one above it.** A self-reported boolean is
    not public liveness. `PUBLIC_MCP_VERIFIED` is reached only from a recorded
    readback of the public origin, and there is deliberately no `public_verified`
    flag for a plan to set.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections.abc import Mapping

SCHEMA = "serveros/fleet-next-tailscale-ingress.v1"

#: The stages, in the only order they may be reached. `flag` names the state
#: field whose observed value advances the ladder, or `None` for the entry
#: stage. `public_verified` is the exception: it is DERIVED from recorded
#: readback evidence, never from a flag, so it cannot be self-reported.
STAGES: tuple[tuple[str, str | None], ...] = (
    ("SOURCE", None),
    ("STAGED_NO_START", "staged"),
    ("NEW_NODE_REGISTERED", "node_registered"),
    ("NEXT_ROUTE_PROVISIONED", "route_provisioned"),
    ("TLS_CERT_READY", "tls_cert_ready"),
    ("NEXT_UNIT_LIVE", "unit_live"),
    ("PUBLIC_MCP_VERIFIED", "public_verified"),
    ("FEISHU_AUTHENTICATED", "feishu_authenticated"),
    ("CHATGPT_INSTALLED", "chatgpt_installed"),
)

EXISTING_FIELDS = frozenset({
    "node_name", "state_dir", "socket", "service_unit", "funnel_target_port",
})
NEXT_FIELDS = frozenset({
    "node_name", "state_dir", "socket", "service_unit", "tailnet_host",
    "funnel_target_port", "local_listen", "issuer",
})
ROUTE_FIELDS = frozenset({
    "health", "mcp", "oauth_protected_resource",
    "oauth_authorization_server", "feishu_callback",
})
STATE_FIELDS = frozenset({
    "staged", "node_registered", "owner_login_attested", "route_provisioned",
    "tls_cert_ready", "unit_live", "feishu_authenticated", "chatgpt_installed",
})
#: What a public verification must have actually observed. Every field is a
#: measurement taken against the public origin, so the stage cannot be reached
#: by asserting it.
PUBLIC_READBACK_FIELDS = frozenset({
    "health_status", "oauth_protected_resource_status",
    "oauth_authorization_server_status", "mcp_tools_seen", "issuer", "audience",
})
TOP_FIELDS = frozenset({"schema", "existing", "next", "routes", "state",
                        "next_state", "public_readback"})

#: The paths the Next host must serve, and their only acceptable values. The
#: issuer is a host, not a path, so the four paths plus /health are the whole
#: HTTP surface this gate knows about. `feishu_callback` is exact because a
#: callback registered in a console is a URL an operator cannot silently fix.
ROUTES = {
    "health": "/health",
    "mcp": "/mcp",
    "oauth_protected_resource": "/.well-known/oauth-protected-resource",
    "oauth_authorization_server": "/.well-known/oauth-authorization-server",
    "feishu_callback": "/oauth/feishu/callback",
}

#: `tailscaled`'s well-known socket. A Next node sharing it IS the old node.
DEFAULT_TAILSCALED_SOCKET = "/run/tailscale/tailscaled.sock"

#: Substrings that mean "this field holds a credential". Checked on FIELD NAMES
#: only. The value is never read, compared, logged or echoed — a refusal that
#: quoted the value would be the leak it is refusing.
SECRET_HINTS = ("authkey", "auth_key", "token", "secret", "password", "passwd",
                "credential", "private_key", "apikey", "api_key", "bearer")

_HOST = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?(\.[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?)+$")

#: The stages at which the backend behind the route is deliberately NOT running,
#: and at which no HTTP status may be demanded or claimed.
ROUTE_ONLY_STAGES = ("NEXT_ROUTE_PROVISIONED", "TLS_CERT_READY")


def _path(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    clean = posixpath.normpath(value)
    return clean if clean == value and clean != "/" else None


def _overlap(first: str, second: str) -> bool:
    return (first == second or first.startswith(second + "/")
            or second.startswith(first + "/"))


def _name(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    held = value.strip()
    return held or None


def _key(value: object) -> str:
    """Normalise a field name for the secret check, without reading values."""
    return str(value).strip().lower().replace("-", "_")


def _audit_names(mapping: object, label: str, problems: list[str],
                 path: str = "") -> None:
    """Walk every field NAME for credential shape. Never touches a value."""
    if isinstance(mapping, Mapping):
        for key, sub in mapping.items():
            if any(hint in _key(key) for hint in SECRET_HINTS):
                where = f"{path}.{key}" if path else str(key)
                # Name only. The value is deliberately not read, not compared
                # and not echoed, so a plan that pasted a key cannot leak it
                # through this refusal.
                problems.append(
                    f"{label}: field {where!r} looks like a credential; this "
                    f"tooling never accepts, stores, copies or logs a Tailscale "
                    f"auth key or token — registration is an interactive owner "
                    f"login in the owner's own browser"
                )
            _audit_names(sub, label, problems,
                         f"{path}.{key}" if path else str(key))


def _shape(mapping: object, allowed: frozenset[str], label: str,
           problems: list[str]) -> Mapping:
    if not isinstance(mapping, dict):
        problems.append(f"{label}: expected object")
        return {}
    unknown = set(mapping) - allowed
    if unknown:
        # Unknown NAMES only; never the values behind them.
        problems.append(f"{label}: unsupported field(s) {', '.join(sorted(unknown))}")
    return mapping


def public_verified(readback: object, *, issuer: object,
                    problems: list[str]) -> bool:
    """Whether the PUBLIC origin was actually observed serving, and how well.

    Every field is a measurement. A caller that has not fetched anything has
    nothing to put here, which is the point: there is no boolean to set.
    """
    if readback is None:
        return False
    if not isinstance(readback, dict):
        problems.append("public_readback: expected object")
        return False
    unknown = set(readback) - PUBLIC_READBACK_FIELDS
    if unknown:
        problems.append(
            f"public_readback: unsupported field(s) {', '.join(sorted(unknown))}")
    missing = PUBLIC_READBACK_FIELDS - set(readback)
    if missing:
        problems.append(
            "public_readback: incomplete — every field is an observation made "
            f"against the public origin; missing {', '.join(sorted(missing))}"
        )
        return False

    ok = True
    for field in ("health_status", "oauth_protected_resource_status",
                  "oauth_authorization_server_status"):
        value = readback.get(field)
        if value != 200:
            problems.append(
                f"public_readback.{field}: observed {value!r}; reaching "
                f"PUBLIC_MCP_VERIFIED requires the public origin to have "
                f"answered 200 on this path"
            )
            ok = False
    tools_seen = readback.get("mcp_tools_seen")
    if isinstance(tools_seen, bool) or not isinstance(tools_seen, int) or tools_seen < 1:
        problems.append(
            "public_readback.mcp_tools_seen: must be the number of tools the "
            "public /mcp actually listed; an open port is not a tool face"
        )
        ok = False
    if readback.get("issuer") != issuer:
        problems.append(
            "public_readback.issuer: must be the issuer the public origin "
            "reported, and must equal next.issuer — a mismatch means the "
            "discovery documents point somewhere other than the route just "
            "published"
        )
        ok = False
    audience = readback.get("audience")
    if not isinstance(audience, str) or not audience.strip():
        problems.append("public_readback.audience: the observed audience is required")
        ok = False
    return ok


def reached_stage(state: Mapping, *, public_ok: bool) -> int:
    """How far up the ladder the evidence actually reaches.

    Monotone by construction: a stage is reached only if every stage below it
    is too. `node_registered` additionally requires `owner_login_attested`,
    because a node that was not interactively logged in by the owner was
    registered by something else.
    """
    index = 0
    for position, (_, flag) in enumerate(STAGES):
        if flag is None:
            index = position
            continue
        if flag == "node_registered":
            ok = bool(state.get("node_registered")) and bool(
                state.get("owner_login_attested"))
        elif flag == "public_verified":
            ok = public_ok
        else:
            ok = bool(state.get(flag))
        if not ok:
            break
        index = position
    return index


def evaluate(plan: object) -> dict:
    """Decide whether the Next ingress is network-isolated, and where it stands.

    Pure. Returns a decision, the blockers, the stage reached, and what the next
    stage requires. Always `writes: []` and `executes: []` — this module has no
    side effects to declare, and saying so lets a caller assert it.
    """
    problems: list[str] = []
    _audit_names(plan, "plan", problems)

    root = _shape(plan, TOP_FIELDS, "plan", problems)
    if root.get("schema") != SCHEMA:
        problems.append("schema: unsupported or missing")

    existing = _shape(root.get("existing"), EXISTING_FIELDS, "existing", problems)
    nxt = _shape(root.get("next"), NEXT_FIELDS, "next", problems)
    routes = _shape(root.get("routes"), ROUTE_FIELDS, "routes", problems)
    state = _shape(root.get("state"), STATE_FIELDS, "state", problems)

    # --- the node identity must be its own -------------------------------
    old_name, new_name = _name(existing.get("node_name")), _name(nxt.get("node_name"))
    if not old_name:
        problems.append("existing.node_name: observed name of the node in use required")
    if not new_name:
        problems.append("next.node_name: the new node must be named")
    elif old_name and new_name == old_name:
        problems.append("next.node_name: must differ from the node already in use")

    for key in ("state_dir", "socket"):
        old, new = _path(existing.get(key)), _path(nxt.get(key))
        if old is None:
            problems.append(f"existing.{key}: absolute normalized path required")
        if new is None:
            problems.append(f"next.{key}: absolute normalized path required")
        if old and new and _overlap(old, new):
            problems.append(
                f"next.{key}: overlaps the node already in use — a new node "
                f"needs its own state and its own socket"
            )
    # A different field name is not a different isolation boundary: the Next
    # socket must not live inside the old node's state dir, nor its state dir
    # inside the old socket's directory.
    for new_key in ("state_dir", "socket"):
        new_path = _path(nxt.get(new_key))
        if new_path is None:
            continue
        for old_key in ("state_dir", "socket"):
            old_path = _path(existing.get(old_key))
            if old_path and _overlap(new_path, old_path):
                problems.append(
                    f"next.{new_key}: overlaps the node already in use "
                    f"(existing.{old_key})"
                )

    if _path(nxt.get("socket")) == DEFAULT_TAILSCALED_SOCKET:
        problems.append(
            "next.socket: the system tailscaled socket belongs to the existing "
            "node; a Next node must not share it"
        )
    old_unit, new_unit = _name(existing.get("service_unit")), _name(nxt.get("service_unit"))
    if not old_unit:
        problems.append("existing.service_unit: observed unit name required")
    if not new_unit:
        problems.append("next.service_unit: a dedicated unit name is required")
    elif new_unit == old_unit:
        problems.append("next.service_unit: must be a unit of its own")

    # --- the published host must be a NEW tailnet name --------------------
    host = nxt.get("tailnet_host")
    if not isinstance(host, str) or not _HOST.fullmatch(host) or not host.endswith(".ts.net"):
        problems.append("next.tailnet_host: a dedicated <name>.ts.net host is required")

    # --- Funnel must point at THIS ingress's own loopback port ------------
    listen = _shape(nxt.get("local_listen"), frozenset({"host", "port"}),
                    "next.local_listen", problems)
    bind_host, bind_port = listen.get("host"), listen.get("port")
    if bind_host not in {"127.0.0.1", "::1"}:
        problems.append("next.local_listen: must bind loopback only")
    if isinstance(bind_port, bool) or not isinstance(bind_port, int) \
            or not 1 <= bind_port <= 65535:
        problems.append("next.local_listen.port: missing/invalid")
        bind_port = None
    old_target = existing.get("funnel_target_port")
    if old_target is not None and bind_port is not None and old_target == bind_port:
        problems.append("next.local_listen.port: the existing node already publishes this port")
    target = nxt.get("funnel_target_port")
    if isinstance(target, bool) or not isinstance(target, int) or not 1 <= target <= 65535:
        problems.append("next.funnel_target_port: missing/invalid")
    elif bind_port is not None and target != bind_port:
        problems.append(
            "next.funnel_target_port: must be this ingress's own loopback port; "
            "a Funnel aimed at anything else publishes V1's port or a dead one"
        )

    # --- one issuer, consistent paths -------------------------------------
    issuer = nxt.get("issuer")
    if not isinstance(host, str) or not host.endswith(".ts.net"):
        issuer_ok = False
    else:
        issuer_ok = issuer == f"https://{host}"
    if not issuer_ok:
        problems.append(
            "next.issuer: must be exactly https://<next.tailnet_host> — the "
            "issuer, both discovery documents and the MCP route share one origin"
        )
    for name, expected in ROUTES.items():
        held = routes.get(name)
        if held != expected:
            problems.append(f"routes.{name}: must be {expected!r}")

    # --- the public stage is reached from evidence, never from a flag -----
    public_ok = public_verified(root.get("public_readback"), issuer=issuer,
                                problems=problems)

    # --- the stage ladder, fail-closed and monotone -----------------------
    index = reached_stage(state, public_ok=public_ok)
    stage = STAGES[index][0]
    if state.get("chatgpt_installed") and stage != STAGES[-1][0]:
        problems.append(
            f"state.chatgpt_installed: claimed while the ladder stands at "
            f"{stage}; an open port is not a usable MCP route"
        )
    if state.get("owner_login_attested") is not True and state.get("node_registered"):
        problems.append(
            "state.node_registered: claimed without state.owner_login_attested — "
            "registration is the owner's interactive browser login, and nothing "
            "else may stand in for it"
        )

    pending = [flag for _, flag in STAGES[index + 1:] if flag is not None]
    return {
        "schema": SCHEMA,
        "decision": "BLOCKED" if problems else "PLAN_READY",
        "blockers": sorted(set(problems)),
        "stage": stage if not problems else "BLOCKED",
        "stage_ordinal": index,
        "next_stage": STAGES[index + 1][0] if index + 1 < len(STAGES) else None,
        "next_stage_requires": pending[:1],
        "backend_running": stage not in ROUTE_ONLY_STAGES and index > 0,
        "public_verified": public_ok,
        "writes": [],
        "executes": [],
        "old_node_changed": False,
        "authkey_required": False,
        "owner_interaction_required": (
            None if index >= 2 else
            "new Tailscale node login: owner's own browser, in the owner's own "
            "session; MFA if the tailnet requires it, never an auth key"
        ),
    }


def run_sheet(plan: object) -> dict:
    """The ordered OWNER commands for the next stage — a sheet, not an action.

    Tokyo's host is operated by its own owner under the resource gate. This
    function therefore emits instructions and executes nothing; `--manual-run`
    prints this and stops.

    A step never asks for something the stage cannot produce. In particular the
    two route stages run against a backend that is deliberately NOT started, so
    they read configuration, the published route and the TLS handshake — never
    an HTTP status.
    """
    decision = evaluate(plan)
    if decision["decision"] == "BLOCKED":
        return decision
    stage = decision["next_stage"]
    sheets = {
        "STAGED_NO_START": [
            "stage the Next bytes with the existing wrapper: next_install.py stage "
            "--proposal <plan> --no-start  (dry-run unless --execute --owner-approved)"
        ],
        "NEW_NODE_REGISTERED": [
            "start the NEW node's own daemon, with its own --statedir and --socket "
            "under next.state_dir (never the system tailscaled socket)",
            "complete the login interactively in the owner's own browser",
            "read back: the new node appears as its own machine in the tailnet, "
            "and the existing node is unchanged",
        ],
        "NEXT_ROUTE_PROVISIONED": [
            "enable Funnel on the NEW node only, pointed at "
            "127.0.0.1:<next.local_listen.port>",
            "publish the route and read it back from the tailnet side: which node "
            "serves it, and which loopback port it targets",
            "the backend is NOT started at this stage and MUST NOT be started to "
            "satisfy this step: no HTTP status is expected, asked for or recorded "
            "here — a connection refused or 502 behind a working route is the "
            "correct observation",
        ],
        "TLS_CERT_READY": [
            "complete the certificate for the new <next.tailnet_host> and read it "
            "back as a TLS fact: the handshake and the served certificate chain "
            "for the issuer host",
            "still no backend: a successful handshake with no HTTP response is the "
            "expected result, and no 200 may be claimed here",
        ],
        "NEXT_UNIT_LIVE": [
            "start the Next user unit with the existing Next unit file",
            "read back: systemctl --user show confirms MemoryMax, MemorySwapMax=0, "
            "CPUQuota, TasksMax, and exactly one Next instance",
        ],
        "PUBLIC_MCP_VERIFIED": [
            "now that the unit is live, fetch the public origin for real: "
            "GET /health, both OAuth discovery documents, and the /mcp tool face",
            "record the observations in public_readback, including the issuer and "
            "audience the public origin itself reported",
            "this stage is reached from that recording, not from any flag",
        ],
        "FEISHU_AUTHENTICATED": [
            "one real Feishu login against the new callback path, in the owner's browser"
        ],
        "CHATGPT_INSTALLED": [
            "install the Next plugin once, under its own PluginID, and confirm one "
            "tool discovery over the new HTTPS origin"
        ],
    }
    return {
        **decision,
        "stage": stage,
        "owner_commands": sheets.get(stage, []),
        "note": "A run sheet, not an execution. Nothing here opens a socket, "
                "starts a daemon or writes a unit.",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--plan", required=True,
                   help="non-secret JSON: the two node identities, the route set, "
                        "the observed state flags, and any public readback")
    p.add_argument("--manual-run", action="store_true",
                   help="print the owner run sheet for the next stage (still no "
                        "writes and no execution)")
    p.add_argument("--owner-approved", action="store_true",
                   help="intent acknowledgment for --manual-run; not authentication")
    args = p.parse_args(argv)
    try:
        with open(args.plan, "r", encoding="utf-8") as handle:
            plan = json.load(handle)
    except (OSError, ValueError) as exc:
        print(json.dumps({"decision": "BLOCKED", "blockers": [type(exc).__name__],
                          "writes": [], "executes": []}, sort_keys=True))
        return 2
    if args.manual_run and not args.owner_approved:
        print(json.dumps({
            "decision": "BLOCKED",
            "blockers": ["--manual-run requires explicit --owner-approved"],
            "writes": [], "executes": [],
        }, indent=2, sort_keys=True))
        return 2
    result = run_sheet(plan) if args.manual_run else evaluate(plan)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["decision"] == "PLAN_READY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
