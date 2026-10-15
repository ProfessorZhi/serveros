#!/usr/bin/env python3
"""Read-only, offline isolation gate for the *new* Fleet ingress.

This is NOT a deployment command.  It never opens sockets, reads a running
binding, installs a service, updates OAuth, or touches a V1 unit.  Operators
supply observed, non-secret identity values for the existing V1 and the
proposed Next service; the gate checks that the plan cannot silently replace V1.

A full Swap does not, by itself, prove active memory pressure.  Conversely,
a green result here does not make launching safe during swap thrashing: the
caller must measure live PSI/swap I/O and confirm actual systemd cgroup limits
before starting a separate, one-instance canary.
"""
from __future__ import annotations

import argparse
import json
import posixpath
import re
import sys
from collections.abc import Mapping
from urllib.parse import urlsplit

SCHEMA = "serveros/fleet-next-dual-stack-preflight.v1"
V1_UNIT = "serveros-fleet-mcp.service"
NEXT_UNIT = "serveros-fleet-next-mcp.service"

IDENTITY_FIELDS = frozenset({
    "service_unit", "runtime_root", "config_path", "oauth_state_root",
    "local_listen", "public_mcp_url", "plugin_id",
})
LIMIT_FIELDS = frozenset({
    "memory_max_mib", "memory_swap_max_mib", "cpu_quota_pct", "tasks_max",
})
ATTEST_FIELDS = frozenset({
    "v1_identity_observed", "next_owner_binding_reviewed",
    "next_oauth_client_authorized", "rollback_ready",
})
TOP_FIELDS = frozenset({"schema", "v1", "next", "limits", "attestations"})
LIMITS = {
    "memory_max_mib": (256, 4096),
    "cpu_quota_pct": (5, 100),
    "tasks_max": (16, 256),
}
PATH_FIELDS = ("runtime_root", "config_path", "oauth_state_root")


def _shape(mapping: object, allowed: frozenset[str], label: str, problems: list[str]) -> Mapping:
    if not isinstance(mapping, dict):
        problems.append(f"{label}: expected object")
        return {}
    unknown = set(mapping) - allowed
    if unknown:
        # Never repeat values: a manifest accidentally containing a password
        # must not leak it into an issue or deploy log.
        problems.append(f"{label}: unsupported field(s) {', '.join(sorted(unknown))}")
    return mapping


def _path(value: object) -> str | None:
    if not isinstance(value, str) or not value.startswith("/"):
        return None
    clean = posixpath.normpath(value)
    return clean if clean == value and clean != "/" else None


def _url(value: object) -> tuple[str, str, int | None, str] | None:
    if not isinstance(value, str):
        return None
    try:
        u = urlsplit(value)
        port = u.port
    except ValueError:
        return None
    if (u.scheme != "https" or not u.hostname or u.username or u.password
            or u.query or u.fragment or not u.path.startswith("/")):
        return None
    return (u.scheme, u.hostname.lower(), port or 443, u.path.rstrip("/") or "/")


def _listen(value: object, label: str, problems: list[str]) -> tuple[str, int] | None:
    b = _shape(value, frozenset({"host", "port"}), label, problems)
    host, port = b.get("host"), b.get("port")
    if not isinstance(host, str) or not host:
        problems.append(f"{label}.host: missing/invalid")
        return None
    if isinstance(port, bool) or not isinstance(port, int) or not 1 <= port <= 65535:
        problems.append(f"{label}.port: missing/invalid")
        return None
    return (host, port)


def _overlap(first: str, second: str) -> bool:
    return (first == second or first.startswith(second + "/")
            or second.startswith(first + "/"))


def evaluate(proposal: object, *, recovery: bool = False) -> dict:
    """Validate install or an emergency Next-only inspection/rollback.

    Recovery may remain possible when the Next OAuth client has been revoked,
    the plugin was uninstalled, or a limit changed. The caller still has to
    name the fixed Next unit and prove filesystem isolation from V1. This is
    not authority to act; the separate installer requires explicit approval.
    """
    problems: list[str] = []
    root = _shape(proposal, TOP_FIELDS, "proposal", problems)
    if root.get("schema") != SCHEMA:
        problems.append("schema: unsupported or missing")

    v1 = _shape(root.get("v1"), IDENTITY_FIELDS, "v1", problems)
    nxt = _shape(root.get("next"), IDENTITY_FIELDS, "next", problems)
    # A failing / revoked OAuth client and a damaged release plan must not
    # prevent an operator from inspecting or restoring the *Next-only* service.
    # Their install attestation is deliberately irrelevant during recovery.
    limits = (_shape(root.get("limits"), LIMIT_FIELDS, "limits", problems)
              if not recovery else {})
    attest = (_shape(root.get("attestations"), ATTEST_FIELDS, "attestations", problems)
              if not recovery else {})

    if v1.get("service_unit") != V1_UNIT:
        problems.append("v1.service_unit: expected observed V1 unit")
    if nxt.get("service_unit") != NEXT_UNIT:
        problems.append("next.service_unit: must use dedicated Next unit")
    for key in PATH_FIELDS:
        a, b = _path(v1.get(key)), _path(nxt.get(key))
        if a is None:
            problems.append(f"v1.{key}: absolute normalized path required")
        if b is None:
            problems.append(f"next.{key}: absolute normalized path required")
        if a and b and _overlap(a, b):
            problems.append(f"next.{key}: overlaps V1")

    # A different field name does not mean a different isolation boundary:
    # e.g. Next's runtime tree must not be nested inside V1's OAuth state.
    for next_key in PATH_FIELDS:
        next_path = _path(nxt.get(next_key))
        if next_path is None:
            continue
        for old_key in PATH_FIELDS:
            if old_key == next_key:
                continue  # already checked above
            old_path = _path(v1.get(old_key))
            if old_path and _overlap(next_path, old_path):
                problems.append(f"next.{next_key}: overlaps V1.{old_key}")

    if not recovery:
        a_bind = _listen(v1.get("local_listen"), "v1.local_listen", problems)
        b_bind = _listen(nxt.get("local_listen"), "next.local_listen", problems)
        if b_bind and b_bind[0] not in {"127.0.0.1", "::1"}:
            problems.append("next.local_listen: must bind loopback only")
        if a_bind and b_bind and a_bind[1] == b_bind[1] and (
            a_bind[0] == b_bind[0] or a_bind[0] in {"0.0.0.0", "::"}
            or b_bind[0] in {"0.0.0.0", "::"}
        ):
            problems.append("next.local_listen: conflicts with V1 listening port")

        a_url, b_url = _url(v1.get("public_mcp_url")), _url(nxt.get("public_mcp_url"))
        if a_url is None:
            problems.append("v1.public_mcp_url: verified HTTPS /mcp URL required")
        if b_url is None:
            problems.append("next.public_mcp_url: dedicated HTTPS /mcp URL required")
        if a_url and b_url and a_url == b_url:
            problems.append("next.public_mcp_url: same published endpoint as V1")
        for name, url in (("v1", a_url), ("next", b_url)):
            if url and not url[3].endswith("/mcp"):
                problems.append(f"{name}.public_mcp_url: endpoint path must end in /mcp")

        for label, item in (("v1", v1), ("next", nxt)):
            pid = item.get("plugin_id")
            if not isinstance(pid, str) or not pid.strip():
                problems.append(f"{label}.plugin_id: actual installed/registered ID required")
            elif not re.fullmatch(r"[A-Za-z0-9_-]{8,128}", pid):
                problems.append(f"{label}.plugin_id: malformed")
        if nxt.get("plugin_id") and nxt.get("plugin_id") == v1.get("plugin_id"):
            problems.append("next.plugin_id: must differ from V1 during dual-stack period")

        for key, (minimum, maximum) in LIMITS.items():
            val = limits.get(key)
            if isinstance(val, bool) or not isinstance(val, int) or not minimum <= val <= maximum:
                problems.append(f"limits.{key}: must be integer {minimum}..{maximum}")
        if limits.get("memory_swap_max_mib") != 0:
            problems.append("limits.memory_swap_max_mib: canary must not consume global swap")
        for key in sorted(ATTEST_FIELDS):
            if attest.get(key) is not True:
                problems.append(f"attestations.{key}: owner/readback confirmation missing")

    # Deliberately no threshold on SwapUsed percentage: it is an inventory
    # counter, not a measurement of live thrashing.  Live safety checks are
    # mandatory on the actual host, and this offline gate does not invent them.
    return {
        "schema": SCHEMA,
        "decision": ("RECOVERY_PLAN_READY" if recovery else "PRECHECK_READY_FOR_HUMAN_CANARY") if not problems else "BLOCKED",
        "blockers": sorted(set(problems)),
        "writes": [],
        "executes": [],
        "v1_changed": False,
        "next_service_unit": NEXT_UNIT,
        "required_live_readback": [
            "V1 /health and OAuth still answer before/after the canary",
            "Next binds its own loopback port and public route",
            "systemctl --user show Next confirms MemoryMax, MemorySwapMax=0, CPUQuota, TasksMax",
            "Live PSI full / swap I/O / blocked tasks watched during launch; abort if worsening",
            "Exactly one next service instance; no concurrent model/CC stress tests",
            "Next tool discovery, OAuth, one read-only device request, and rollback checked",
        ],
        "mode": "RECOVERY_ONLY" if recovery else "INSTALL_PRECHECK",
        "note": "This is dry-run metadata, never an authorization or a launch.",
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--proposal", required=True, help="non-secret JSON dual-stack candidate")
    args = p.parse_args(argv)
    try:
        with open(args.proposal, "r", encoding="utf-8") as file:
            proposal = json.load(file)
    except (OSError, ValueError) as exc:
        # Do not print potentially private file content.
        print(json.dumps({"decision": "BLOCKED", "blockers": [type(exc).__name__],
                          "writes": [], "executes": []}, sort_keys=True))
        return 2
    result = evaluate(proposal)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["decision"] == "PRECHECK_READY_FOR_HUMAN_CANARY" else 2


if __name__ == "__main__":
    raise SystemExit(main())
