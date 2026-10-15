#!/usr/bin/env python3
"""Isolated Fleet Next installer wrapper. DRY-RUN by default; never targets V1.

The V1 install.py hardcodes serveros-fleet-mcp.service and must not be invoked
directly to install the Next canary. This wrapper uses a separate unit template,
configuration, release root and distinct unit name, and requires the machine-
readable dual-stack preflight to pass before any Next-side write or restart.

Owner flags are intent acknowledgments, NOT authentication or a substitute for
the human release approval required by the deployment policy.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from fleet_mcp.deploy import next_dual_stack_preflight as gate

UNIT = gate.NEXT_UNIT
TEMPLATE = "fleet_mcp/deploy/serveros-fleet-next-mcp.service"
COMPOSITION = "ingress-next"
BUDGET = {
    "memory_max_mib": 1536,
    "memory_swap_max_mib": 0,
    "cpu_quota_pct": 40,
    "tasks_max": 96,
}


class Refused(Exception):
    pass


def _load_json(path: str) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as f:
            item = json.load(f)
    except (OSError, ValueError) as exc:
        raise Refused(f"cannot load non-secret proposal: {type(exc).__name__}") from exc
    if not isinstance(item, dict):
        raise Refused("proposal must be a JSON object")
    return item


def plan(proposal: dict, command: str) -> dict:
    preflight = gate.evaluate(proposal)
    if preflight["decision"] != "PRECHECK_READY_FOR_HUMAN_CANARY":
        return {"decision": "BLOCKED", "blockers": preflight["blockers"],
                "writes": [], "executes": []}
    nxt = proposal["next"]
    root = nxt["runtime_root"]
    if Path(root).name != COMPOSITION:
        return {"decision": "BLOCKED",
                "blockers": ["next.runtime_root: must end in /ingress-next"],
                "writes": [], "executes": []}
    if proposal["limits"] != BUDGET:
        return {"decision": "BLOCKED",
                "blockers": ["limits: must match the Next service template"],
                "writes": [], "executes": []}
    template = _HERE / "serveros-fleet-next-mcp.service"
    if not template.is_file():
        return {"decision": "BLOCKED", "blockers": ["Next unit template missing"],
                "writes": [], "executes": []}
    body = template.read_text(encoding="utf-8")
    for required in (
        "MemoryMax=1536M", "MemorySwapMax=0", "CPUQuota=40%",
        "TasksMax=96", "NoNewPrivileges=yes", "@FLEET_MCP_RUNTIME@",
        "@FLEET_MCP_CONFIG@",
    ):
        if required not in body:
            return {"decision": "BLOCKED",
                    "blockers": ["Next unit template does not match bounded canary contract"],
                    "writes": [], "executes": []}
    return {
        "decision": "DRY_RUN_READY",
        "command": command,
        "unit": UNIT,
        "composition": COMPOSITION,
        "same_as_v1": False,
        "limits": dict(BUDGET),
        "writes": [], "executes": [],
        "requires": [
            "Next owner authorization and distinct real App/Plugin registration",
            "Short live memory-PSI/swap-IO probe before Next start",
            "systemctl --user show confirms effective cgroup limits after start",
            "V1 /health remains healthy throughout canary and Next-only rollback",
        ],
    }


def _current_pressure() -> str | None:
    """One-second, tiny procfs-only sample; never uses SwapUsed occupancy."""
    try:
        def psi() -> float:
            for line in Path("/proc/pressure/memory").read_text().splitlines():
                if line.startswith("full "):
                    return float(re.search(r"\bavg10=([0-9.]+)", line).group(1))
            raise ValueError("missing full PSI")

        def swapout() -> int:
            for line in Path("/proc/vmstat").read_text().splitlines():
                if line.startswith("pswpout "):
                    return int(line.split()[1])
            raise ValueError("missing pswpout")

        first_psi = psi()
        before = swapout()
        time.sleep(1)
        second_psi = psi()
        pages = max(0, swapout() - before)
        mbps = pages * os.sysconf("SC_PAGE_SIZE") / (1024 * 1024)
    except (OSError, ValueError, AttributeError, TypeError):
        return "cannot verify immediate host memory pressure"
    if max(first_psi, second_psi) >= 30 or (
            max(first_psi, second_psi) >= 10 and mbps >= 2):
        return "host actively stalled or writing swap; defer this launch attempt"
    return None


def _load_legacy_installer():
    """The constants are changed inside THIS subprocess only, not V1's unit."""
    spec = importlib.util.spec_from_file_location("fleet_next_shared_installer",
                                                  _HERE / "install.py")
    if spec is None or spec.loader is None:
        raise Refused("shared installer module cannot be loaded")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.UNIT_NAME = UNIT
    mod.UNIT_TEMPLATE = TEMPLATE
    mod.SERVICE_NAME = "serveros-fleet-next-mcp"
    mod.COMPOSITION = COMPOSITION
    return mod


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("command", choices=("install", "status", "rollback", "uninstall"))
    p.add_argument("--proposal", required=True, help="non-secret, freshly reviewed JSON")
    p.add_argument("--source", default=str(_ROOT))
    p.add_argument("--revision")
    p.add_argument("--label")
    p.add_argument("--no-start", action="store_true")
    p.add_argument("--execute", action="store_true")
    p.add_argument("--owner-approved", action="store_true")
    p.add_argument("--health-timeout", type=float, default=20.0)
    args = p.parse_args(argv)
    if args.health_timeout <= 0 or args.health_timeout > 60:
        p.error("--health-timeout must be within (0, 60]")
    try:
        proposal = _load_json(args.proposal)
        preview = plan(proposal, args.command)
        if preview["decision"] == "BLOCKED":
            print(json.dumps(preview, indent=2, sort_keys=True))
            return 2

        if not args.execute:
            print(json.dumps(preview, indent=2, sort_keys=True))
            return 0
        if not args.owner_approved:
            raise Refused("--execute requires explicit --owner-approved confirmation")
        if args.command == "uninstall":
            raise Refused("Next removal is a separate reviewed operation; no automatic uninstall")
        if args.command == "install" and not args.no_start:
            reason = _current_pressure()
            if reason:
                raise Refused(reason)

        mod = _load_legacy_installer()
        nxt = proposal["next"]
        runtime_root = str(Path(nxt["runtime_root"]).parent)
        args_list = [args.command, "--runtime-root", runtime_root,
                     "--composition", COMPOSITION, "--config", nxt["config_path"],
                     "--health-timeout", str(args.health_timeout)]
        if args.command == "install":
            args_list.extend(["--source", args.source])
            if args.revision:
                args_list.extend(["--revision", args.revision])
            if args.label:
                args_list.extend(["--label", args.label])
            if args.no_start:
                args_list.append("--no-start")

            # The binding must agree with the reviewed proposal's local port.
            info = mod.binding(Path(nxt["config_path"]))
            observed_port = info["port"]
            if observed_port != nxt["local_listen"]["port"]:
                raise Refused("Next binding port differs from reviewed proposal")
            if info["bind"] != nxt["local_listen"]["host"]:
                raise Refused("Next binding address differs from reviewed proposal")

        # Every destructive operation is addressed to the Next unit ONLY.
        # The wrapper does not grant OAuth, register a plugin or change V1.
        return mod.main(args_list)
    except Refused as exc:
        print(json.dumps({"decision": "BLOCKED", "reason": str(exc),
                          "writes": [], "executes": []}, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
