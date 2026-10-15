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
TEMPLATE = "fleet_mcp/deploy/serveros-fleet-example-mcp.service"
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
    if command == "uninstall":
        return {"decision": "BLOCKED",
                "blockers": ["Next uninstall requires separate reviewed retirement"],
                "writes": [], "executes": []}
    recovery = command in ("rollback", "status")
    preflight = gate.evaluate(proposal, recovery=recovery)
    ready = "RECOVERY_PLAN_READY" if recovery else "PRECHECK_READY_FOR_HUMAN_CANARY"
    if preflight["decision"] != ready:
        return {"decision": "BLOCKED", "blockers": preflight["blockers"],
                "writes": [], "executes": []}
    nxt = proposal["next"]
    root = nxt["runtime_root"]
    if Path(root).name != COMPOSITION:
        return {"decision": "BLOCKED",
                "blockers": ["next.runtime_root: must end in /ingress-next"],
                "writes": [], "executes": []}
    if not recovery and proposal["limits"] != BUDGET:
        return {"decision": "BLOCKED",
                "blockers": ["limits: must match the Next service template"],
                "writes": [], "executes": []}
    if not recovery:
        template = _HERE / "serveros-fleet-example-mcp.service"
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
        "mode": "NEXT_ONLY_RECOVERY" if recovery else "NEXT_CANARY_INSTALL",
        "command": command,
        "unit": UNIT,
        "composition": COMPOSITION,
        "same_as_v1": False,
        "limits": dict(BUDGET) if not recovery else None,
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
    mod.SERVICE_NAME = "serveros-fleet-example-mcp"
    mod.COMPOSITION = COMPOSITION
    return mod


def _effective_limit_errors(mod) -> list[str]:
    """Inspect live systemd values. A template alone cannot prove isolation."""
    info = mod.systemctl(
        "show", UNIT, "-p", "MemoryMax", "-p", "MemorySwapMax",
        "-p", "TasksMax", "-p", "CPUQuotaPerSecUSec", check=False)
    if info.returncode != 0:
        return ["systemd limits could not be read back"]
    props = dict(line.split("=", 1) for line in info.stdout.splitlines() if "=" in line)
    problems = []
    for key, wanted in (
        ("MemoryMax", BUDGET["memory_max_mib"] * 1024 * 1024),
        ("MemorySwapMax", 0),
        ("TasksMax", BUDGET["tasks_max"]),
    ):
        try:
            if int(props.get(key, "")) != wanted:
                problems.append(key + " not enforced")
        except ValueError:
            problems.append(key + " unavailable")
    # systemd normally reports CPUQuotaPerSecUSec as e.g. 400ms.
    quota = props.get("CPUQuotaPerSecUSec", "")
    matched = re.fullmatch(r"(\d+)(us|ms|s)", quota.strip())
    if not matched:
        problems.append("CPUQuotaPerSecUSec unavailable")
    else:
        num = int(matched.group(1))
        micros = num * {"us": 1, "ms": 1000, "s": 1000000}[matched.group(2)]
        if micros != BUDGET["cpu_quota_pct"] * 10000:
            problems.append("CPUQuotaPerSecUSec not enforced")
    return problems


def _real_path_isolation(proposal: dict) -> None:
    """Resolve symlinks at execution time so lexical separation cannot lie."""
    for next_key in gate.PATH_FIELDS:
        nxt = Path(proposal["next"][next_key]).resolve()
        for old_key in gate.PATH_FIELDS:
            v1 = Path(proposal["v1"][old_key]).resolve()
            if nxt == v1 or nxt in v1.parents or v1 in nxt.parents:
                raise Refused("Next path resolves inside V1; refusing effect")


def _restore_failed_install(mod, root: Path, before: tuple, reason: str) -> None:
    """Stop only Next, restore its previous pointers, never restart V1.

    Artifacts are intentionally retained for forensics. A recovered prior
    pointer is not an assurance the prior Next process is running.
    """
    # A failure before any revision switch (e.g. a source-validation error)
    # must not take down a previously healthy Next. V1 is never addressed.
    changed = mod.read_link(root / "current") != before[0]
    if not changed:
        raise Refused("Next install failed before revision switch; existing Next untouched: "
                      + reason)
    stop_error = None
    try:
        stopped = mod.systemctl("stop", UNIT, check=False)
        if stopped.returncode != 0:
            stop_error = " Next stop reported nonzero status."
    except (OSError, RuntimeError):
        stop_error = " Next stop could not be confirmed."
    try:
        if mod.read_link(root / "current") != before[0]:
            mod.point(root / "current", before[0])
            mod.point(root / "previous", before[1])
    except (OSError, ValueError) as exc:
        raise Refused("Next failed; could not restore Next revision links: "
                      + type(exc).__name__ + (";" + stop_error if stop_error else "")) from exc
    raise Refused("Next failed, Next-only stop/rollback attempted: " + reason
                  + (stop_error or ""))


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
    effect_started = False
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
        # Consent is per-operator intent. It must not be replaced with a
        # platform-wide permission switch or inherited from the V1 token.
        _real_path_isolation(proposal)
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

            # The Next and V1 binding files must be ACTUALLY distinct today,
            # not merely separate addresses typed into a JSON proposal.
            old = mod.binding(Path(proposal["v1"]["config_path"]))
            info = mod.binding(Path(nxt["config_path"]))
            if (old["port"] != proposal["v1"]["local_listen"]["port"]
                    or old["bind"] != proposal["v1"]["local_listen"]["host"]):
                raise Refused("live V1 binding differs from reviewed proposal")
            if (info["port"] != nxt["local_listen"]["port"]
                    or info["bind"] != nxt["local_listen"]["host"]):
                raise Refused("Next binding differs from reviewed proposal")
            if info["port"] == old["port"]:
                raise Refused("Next would reuse V1's local listening port")
            if not args.no_start and not mod.wait_for_health(old["probe"], 3.0):
                raise Refused("V1 health unavailable before Next start; do not risk cutover")

        # Every destructive operation is addressed to the Next unit ONLY.
        # The wrapper does not grant OAuth, register a plugin or change V1.
        root = Path(nxt["runtime_root"])
        prior = (mod.read_link(root / "current"), mod.read_link(root / "previous"))
        effect_started = True  # no longer a dry-run, even if the install fails
        try:
            result = mod.main(args_list)
        except SystemExit as exc:
            if args.command == "install":
                _restore_failed_install(mod, root, prior,
                                        "installer exited nonzero; status must be inspected")
            raise
        if args.command == "install" and result != 0:
            _restore_failed_install(mod, root, prior, "installer returned nonzero")
        if args.command == "install" and not args.no_start:
            faults = _effective_limit_errors(mod)
            if faults:
                _restore_failed_install(mod, root, prior,
                                        "cgroup limits not enforced: " + ", ".join(faults))
            if not mod.wait_for_health(old["probe"], 3.0):
                _restore_failed_install(mod, root, prior,
                                        "V1 /health unavailable after Next launch")
        return result
    except Refused as exc:
        # Never report writes=[] after attempting an install/restart and
        # perhaps a Next-only rollback. The outcome may be partial/unknown.
        print(json.dumps({
            "decision": "BLOCKED",
            "reason": str(exc),
            "effect_state": ("NEXT_ONLY_ACTION_ATTEMPTED" if effect_started
                             else "NO_SYSTEM_ACTION_STARTED"),
            "writes": ("POSSIBLE_OR_ROLLED_BACK" if effect_started else []),
            "executes": ([UNIT] if effect_started else []),
            "v1_action": False,
        }, sort_keys=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
