#!/usr/bin/env python3
"""The release plan for the CloudCLI full-inheritance surface — precheck,
per-device rollout and rollback, in DRY-RUN by default.

What this is for
----------------
The 22-tool mirror changes three things a live ingress depends on: the served
`tools/list`, the advertised OAuth scopes (adding `fleet:approve` and
`fleet:navigate`), and the node route table (one route added). The served set is
the hand-written nine, the 22 mirrored CloudCLI verbs, and the two read-only
tools that describe the mirror (`fleet_cloudcli_native_manifest`,
`fleet_cloudcli_native_capability`) — 33 today. This module READS that number
from `tools.SERVED_TOOLS` and never restates it, because a count written down in
two places is a count that disagrees the first time one of them moves: this
docstring said "9 → 31" while the tree served 33.

This module reads the RUNNING revision, compares it with this source tree, and
prints what an operator would do — device by device, with a rollback step —
WITHOUT doing any of it. `--execute` exists so the plan is not a dead end, but
this module never calls it from tests and the plan's default is to change
nothing.

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

#: The devices the mirror is granted on, DERIVED from the authored registry's own
#: `ownership` rather than listed here.
#:
#: A hand-written list is exactly how a personal machine enters a company
#: rollout. This constant used to name `device:personal-dell` — which the
#: registry declares `ownership: personal` — while omitting `device:work-fj`,
#: the company Windows workstation (`ownership: company`, role
#: `corporate-workstation`) the rollout is actually for. The registry already
#: answers "which devices are the company's", so the plan asks it instead of
#: keeping a second answer that can disagree.
def company_devices() -> list[str]:
    """Every `ownership: company` device, as its canonical `device:<id>` ref.

    Sorted, so the plan reads the same whatever order a registry declares its
    devices in. Refuses an empty set rather than returning one: a rollout step
    that connects a connector on no devices would read as a completed step, and
    silence is the wrong answer to "which machines is this for".
    """
    # Imported by absolute path, like `source_surface` below, because this file
    # is also run AS A SCRIPT (it has a `__main__`): a relative import here
    # raises `attempted relative import with no known parent package` the moment
    # anyone runs `python3 fleet_mcp/deploy/release_plan.py`, which is the one
    # way an operator invokes it.
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from fleet_mcp.registry import load_registry

    refs = sorted(str(device.device_ref) for device in load_registry().all()
                  if getattr(device, "ownership", None) == "company")
    if not refs:
        raise ValueError(
            "the registry declares no `ownership: company` device, so the "
            "rollout has no targets. A plan whose device list is empty is not a "
            "plan, and this one refuses to print as though it were.")
    return refs


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


#: Markers of a production fix this branch must NOT clobber. The running
#: revision was built from `fleet-oauth-rfc9207-review-20261013`; the source tree
#: here descends from an older integration branch and does NOT carry them. If a
#: deploy went out as-is it would REGRESS them, so the precheck refuses to call
#: the tree deployable until they are reconciled.
PRODUCTION_FIX_MARKERS = {
    "fleet_mcp/http_transport.py": "_redirect_origin",
    "fleet_mcp/oauth.py": "authorization_response_iss_parameter_supported",
}


def source_delta() -> dict:
    """Files where the SOURCE tree differs from the RUNNING revision, and
    whether the source is missing a production fix.

    Read-only. The source is the loser in a plain copy-deploy: a file the
    deployed tree has and this one does not is a production change a deploy
    would delete. This names them instead of letting `install.py` overwrite
    silently.
    """
    missing_markers: list[str] = []
    for rel, marker in PRODUCTION_FIX_MARKERS.items():
        src = _ROOT / rel
        if src.is_file() and marker not in src.read_text(encoding="utf-8"):
            missing_markers.append(rel)
    differing: list[str] = []
    if CURRENT.exists():
        for rel in sorted(PRODUCTION_FIX_MARKERS):
            deployed = CURRENT / rel
            src = _ROOT / rel
            if (deployed.is_file() and src.is_file()
                    and deployed.read_bytes() != src.read_bytes()):
                differing.append(rel)
    return {
        "differing_from_running": differing,
        "source_missing_production_fix": missing_markers,
        "reconcile_before_deploy": bool(missing_markers),
        # The note follows the MEASUREMENT, not a fixed paragraph. It used to be
        # one static sentence that asserted this branch lacks the CSP/RFC 9207
        # fix — which stopped being true when `b3c0640` ported them, and a note
        # that keeps saying "REBASE OR MERGE" after the rebase is a note an
        # operator learns to skip, which is the one moment it has to be read.
        "note": (
            "This tree is missing a production fix the running revision carries. "
            "REBASE OR MERGE BEFORE ANY DEPLOY: a copy-deploy from this tree "
            "would remove the CSP `form-action` fix and RFC 9207 `iss`, which "
            "are production behaviour, not cosmetics."
            if missing_markers else
            "This tree carries the production CSP `form-action` / RFC 9207 `iss` "
            "fixes the running revision has, so a copy-deploy does not regress "
            "them. Re-read `differing_from_running` above if either of those two "
            "files changes again."),
    }


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
    devices = company_devices()
    same_surface = (installed.get("tools") == source["served_tools"])
    installed_tools = installed.get("tools")
    installed_phrase = (f"the {installed_tools}-tool surface"
                        if isinstance(installed_tools, int) else "an unreadable surface")
    return {
        "schema": "serveros/fleet-cloudcli-release-plan.v1",
        "mode": "DRY_RUN",
        "precheck": {
            "installed": installed,
            "source": source,
            "surface_would_change": not same_surface,
            "note": (f"The running ingress is {installed_phrase}; this tree serves "
                     f"{source['served_tools']} — the hand-written "
                     f"{source['hand_written']}, the {source['mirrored']} inherited "
                     f"CloudCLI verbs, and the two read-only tools that describe "
                     f"the mirror."),
            "production_delta": source_delta(),
        },
        "rollout": [
            {
                "step": 1,
                "target": "ingress",
                "action": "publish a new revision under ingress/revisions and repoint "
                          "`current`",
                # Derived, not written down. These two numbers are the whole
                # point of the gate, and the copy of them that lived here said
                # "31" for a tree that serves 33.
                "gates": ["contract sha matches",
                          f"served_tools == {source['served_tools']}",
                          f"scopes_supported == {len(source['scopes'])}"],
                "reversible_by": "rollback step 1",
            },
            {
                "step": 2,
                "target": "devices",
                "action": "connect the updated node connector on each fleet-node device",
                "devices": devices,
                "device_source": "fleet/registry.yaml, `ownership: company`",
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
        # The parts are named as a sum that ADDS UP: the hand-written nine, the
        # mirrored 22, and the two tools that describe the mirror. An earlier
        # line here printed two of the three parts as though they were the whole.
        _source = document["precheck"]["source"]
        print(f"source serves: {_source['served_tools']} tools "
              f"({_source['hand_written']} hand-written + {_source['mirrored']} "
              f"mirrored + {_source['served_tools'] - _source['hand_written'] - _source['mirrored']} "
              f"describing the mirror)")
        print(f"rollout targets: {', '.join(document['rollout'][1]['devices'])}")
        for step in document["rollout"]:
            print(f"  rollout {step['step']}: {step['target']} — {step['action']}")
        for step in document["rollback"]:
            print(f"  rollback {step['step']}: {step['action']}")
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
