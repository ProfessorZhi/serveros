#!/usr/bin/env python3
"""The release plan for the CloudCLI full-inheritance surface — precheck,
per-device rollout and rollback, in DRY-RUN by default.

What this is for
----------------
The 22-tool mirror changes three things a live ingress depends on: the served
`tools/list` (9 → 31), the advertised OAuth scopes (adding `fleet:approve` and
`fleet:navigate`), and the node route table (one route added). This module reads
the RUNNING revision, compares it with this source tree, and prints what an
operator would do — device by device, with a rollback step — WITHOUT doing any
of it. `--execute` exists so the plan is not a dead end, but this module never
calls it from tests and the plan's default is to change nothing.

It opens the running ingress directory and the source tree READ-ONLY. It does
not install, restart, write a unit, touch OAuth, or reach a device.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]

#: Where `fleet_mcp/deploy/install.py` publishes revisions, and the symlink a
#: unit's ExecStart resolves through.
INGRESS_ROOT = Path("/data/home/vince/runtime-assets/serveros-fleet-mcp/ingress")
CURRENT = INGRESS_ROOT / "current"
REVISIONS = INGRESS_ROOT / "revisions"

#: The three company-owned devices the mirror is granted on. A personal device
#: is deliberately absent: nothing here widens a grant to one.
COMPANY_DEVICES = ("device:tokyo-shared-dev-01", "device:personal-dell",
                   "device:llm-lab-dell")


def installed_revision() -> dict:
    """What the running ingress is, read-only. Names and counts, never secrets."""
    info = {"current": None, "target": None, "tools": None,
            "tool_surface_revision": None, "exists": CURRENT.exists()}
    if CURRENT.exists():
        info["current"] = str(CURRENT.resolve())
        version = CURRENT / "fleet_mcp" / "version.py"
        if version.is_file():
            for line in version.read_text(encoding="utf-8").splitlines():
                if line.startswith("TOOL_SURFACE_REVISION"):
                    info["tool_surface_revision"] = line.split("=", 1)[1].strip().strip('"')
        tools_py = CURRENT / "fleet_mcp" / "tools.py"
        if tools_py.is_file():
            text = tools_py.read_text(encoding="utf-8")
            info["tools"] = text.count('name="fleet_') + text.count("name='fleet_")
    return info


def source_surface() -> dict:
    """What THIS source tree serves, and the contract's digest."""
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from fleet_mcp import cloudcli_native, scopes, tools
    contract = (_ROOT / "fleet" / "fleet-mcp-contract.yaml")
    digest = hashlib.sha256(contract.read_bytes()).hexdigest()[:12] if contract.is_file() else None
    return {
        "served_tools": len(tools.SERVED_TOOLS),
        "hand_written": len(tools.TOOLS),
        "mirrored": len(cloudcli_native.NATIVE_TOOLS),
        "scopes": list(scopes.IMPLEMENTED_SCOPES),
        "grantable_default": list(scopes.DEFAULT_GRANTABLE_SCOPES),
        "contract_sha12": digest,
    }


def plan() -> dict:
    """The precheck + per-device rollout + rollback, as data. Nothing is run."""
    installed = installed_revision()
    source = source_surface()
    same_surface = (installed.get("tools") == source["served_tools"])
    return {
        "schema": "serveros/fleet-cloudcli-release-plan.v1",
        "mode": "DRY_RUN",
        "precheck": {
            "installed": installed,
            "source": source,
            "surface_would_change": not same_surface,
            "note": ("The running ingress is the 9-tool surface; this tree serves "
                     "the hand-written nine plus the 22 inherited CloudCLI verbs."),
        },
        "rollout": [
            {
                "step": 1,
                "target": "ingress",
                "action": "publish a new revision under ingress/revisions and repoint "
                          "`current`",
                "gates": ["contract sha matches", "served_tools == 31",
                          "scopes_supported == 6"],
                "reversible_by": "rollback step 1",
            },
            {
                "step": 2,
                "target": "devices",
                "action": "connect the updated node connector on each fleet-node device",
                "devices": list(COMPANY_DEVICES),
                "gates": ["POST /v1/native/call is in the connector's closed route "
                          "table", "device owner floor unchanged"],
                "reversible_by": "rollback step 2 (previous connector)",
            },
            {
                "step": 3,
                "target": "oauth",
                "action": "grant fleet:approve / fleet:navigate to the operator "
                          "credential, then the user re-connects ChatGPT",
                "gate": "never default-granted: the scopes are expressible, not issued",
                "reversible_by": "rollback step 3 (drop the scopes, revoke the token)",
            },
        ],
        "rollback": [
            {"step": 1, "action": "repoint ingress/current to the previous revision",
             "effect": "the 9-tool surface is served again"},
            {"step": 2, "action": "leave the connector — it is backward-compatible "
                                  "(the new route is additive)"},
            {"step": 3, "action": "revoke the granted scopes / tokens"},
        ],
        "user_reconnect": [
            "OAuth clients are NOT re-issued automatically: a client granted the "
            "old read/send/create/control must be re-consented for fleet:approve "
            "and fleet:navigate, which means the user re-connects the ChatGPT "
            "connector by hand.",
        ],
        "maintenance_window": (
            "The ingress repoint (rollout step 1) is a restarted service and needs "
            "a short maintenance approval from the user BEFORE it runs; this plan "
            "does not schedule it."),
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--execute", action="store_true",
                        help="NOT used by tests; the plan performs nothing even "
                             "with it, and a real install is a separate, approved act")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    document = plan()
    if args.execute:
        # Deliberately inert: executing a release is an operator act with an
        # approval, and this module only ever prints the plan.
        document["execute"] = ("refused here: run `fleet_mcp/deploy/install.py` "
                               "after the maintenance approval; this planner "
                               "performs no install")
    if args.json:
        json.dump(document, sys.stdout, ensure_ascii=False, indent=2, sort_keys=True)
    else:
        print(f"mode: {document['mode']}")
        print(f"installed: {document['precheck']['installed']['current']}")
        print(f"source serves: {document['precheck']['source']['served_tools']} tools "
              f"({document['precheck']['source']['hand_written']} hand-written + "
              f"{document['precheck']['source']['mirrored']} mirrored)")
        for step in document["rollout"]:
            print(f"  rollout {step['step']}: {step['target']} — {step['action']}")
        for step in document["rollback"]:
            print(f"  rollback {step['step']}: {step['action']}")
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
