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
from urllib.parse import urlsplit

_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parents[1]

#: Where `fleet_mcp/deploy/install.py` publishes revisions, and the symlink a
#: unit's ExecStart resolves through.
#:
#: Written the way the installer already writes it — `~/runtime-assets/...`,
#: expanded at use — rather than as an absolute path naming this account's home.
#: The absolute form was a literal `/data/home/vince/...`, and a product-data
#: module that names a node is the thing `test_no_node_paths_in_product_data`
#: exists to catch: the same file installed for a different operator would carry
#: this one's home in its text. The declaration table in that test is empty on
#: purpose — "a place to put a file until the scanner is quiet" — so this is
#: fixed here rather than declared there.
#:
#: `~` is the operator running the plan, which is who `install.py --runtime-root`
#: means by its own default (`~/runtime-assets/serveros-fleet-mcp`), and a caller
#: that wants another root passes `--runtime-root` as it always could.
INGRESS_ROOT = Path("~/runtime-assets/serveros-fleet-mcp/ingress").expanduser()
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


RUNTIME_ROLLOUT_SCHEMA = "serveros/fleet-runtime-rollout-plan.v1"


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


def _endpoint_shape(endpoint: str) -> str:
    """`http://user:pass@host:7301/path` -> `host:7301`.

    A binding's endpoint is a URL, and a URL may carry userinfo. The plan is a
    document an operator reads and may paste into a ticket, so the one thing it
    must never do is round-trip a credential embedded in an endpoint. Only the
    host and port survive, and a URL that does not parse is reported as its
    first token with anything before an `@` removed — the shape, never the
    secret.
    """
    without_userinfo = endpoint.rsplit("@", 1)[-1] if "@" in endpoint else endpoint
    parsed = urlsplit(without_userinfo)
    return parsed.netloc or parsed.path.split("/", 1)[0]


def _grant_shape(grants: dict) -> dict:
    """Every `provider_key` an operator grant names, with NO principal names.

    A principal name is a credential identifier (`oauth:<client_id>`), so it is
    counted and never printed — the same rule `FleetConfig.describe` follows, and
    for the same reason. What an operator needs from this half is which
    machine-and-runtime a grant was written for and which verbs it carries,
    which is all the key and the verbs are.
    """
    shape: dict[str, dict] = {}
    for per_device in grants.values():
        if not isinstance(per_device, dict):
            continue
        for provider_key, verbs in per_device.items():
            row = shape.setdefault(str(provider_key), {"principals": 0, "verbs": set()})
            row["principals"] += 1
            row["verbs"].update(str(v) for v in (verbs or ()))
    return {key: {"principals": row["principals"], "verbs": sorted(row["verbs"])}
            for key, row in sorted(shape.items())}


def runtime_bindings(registry_path=None, config_path=None) -> dict:
    """The multi-runtime half of the rollout, as data. Reads; changes nothing.

    Why this half reads LOCAL bindings and not only the registry: a device record
    may carry `runtime_bindings` as `agent-runtime:<family>` refs and nothing
    else. Two OS accounts on one machine running the same family are therefore
    ONE declared family and TWO runtimes, and the registry is structurally unable
    to tell them apart — so `agent-runtime:claude-code` on the Dell is already
    correct and needs no edit, while `claude-fjd` can only ever be a local
    binding. A plan that waited for the registry to declare the second runtime
    would wait for a declaration the registry cannot make.

    Every gap below is a different remedy, which is why each is its own kind
    rather than one "incomplete" flag: a missing binding is an operator's edit, a
    missing credential anchor is `fjd`'s work on the device, a disabled binding is
    a decision someone already made, and a shared anchor is an isolation fact
    that is WRONG rather than absent.
    """
    if str(_ROOT) not in sys.path:
        sys.path.insert(0, str(_ROOT))
    from fleet_mcp.config import load_config, provider_key
    from fleet_mcp.errors import FleetRefusal
    from fleet_mcp.registry import load_registry

    registry = load_registry(registry_path)
    try:
        config = load_config(config_path)
    except (FleetRefusal, ValueError) as exc:
        # No local binding is a legitimate state for this plan to be run in — it
        # is the state the fleet is in before the host half ships. Reported as
        # UNAVAILABLE with the reason, never as an empty survey: "no runtimes
        # anywhere" and "this host cannot see the bindings" are different facts
        # and the second is the one that would be read as the first.
        return {
            "schema": RUNTIME_ROLLOUT_SCHEMA,
            "mode": "DRY_RUN",
            "state": "UNAVAILABLE",
            "reason": getattr(exc, "detail", str(exc)),
            "writes": [],
        }

    devices = []
    gaps: list[dict] = []
    for device in registry.all():
        ref = str(device.device_ref)
        ownership = getattr(device, "ownership", None)
        owned = [b for b in config.providers.values() if b.device_ref == ref]
        anchors: dict[str, list[str]] = {}
        rows = []
        for binding in sorted(owned, key=lambda b: (b.runtime != "claude", b.runtime)):
            anchors.setdefault(str(binding.credential_anchor), []).append(binding.runtime)
            row = {
                "runtime": binding.runtime,
                "provider_key": provider_key(ref, binding.runtime),
                "enabled": binding.enabled,
                "endpoint": _endpoint_shape(binding.endpoint),
                "credential_anchor_present": binding.credential_anchor.exists(),
                "allow_send": binding.allow_send,
                "allow_create": binding.allow_create,
                "allow_control": binding.allow_control,
            }
            rows.append(row)
            if ownership != "company":
                gaps.append({
                    "kind": "PERSONAL_DEVICE_IN_SCOPE", "device_ref": ref,
                    "runtime": binding.runtime,
                    "detail": f"this binding reaches a device the registry declares "
                              f"ownership: {ownership!r}; the fleet rollout is "
                              f"`ownership: company` only",
                })
            if not binding.enabled:
                gaps.append({
                    "kind": "BINDING_DISABLED", "device_ref": ref,
                    "runtime": binding.runtime,
                    "detail": "bound but disabled: this runtime has a name and no route",
                })
            if not binding.credential_anchor.exists():
                gaps.append({
                    "kind": "CREDENTIAL_ANCHOR_MISSING", "device_ref": ref,
                    "runtime": binding.runtime,
                    "detail": "the credential anchor names nothing on disk; the "
                              "device-side owner creates it, not this host",
                })
        for anchor, runtimes in anchors.items():
            if len(runtimes) > 1:
                gaps.append({
                    "kind": "SHARED_CREDENTIAL_ANCHOR", "device_ref": ref,
                    "runtime": None,
                    "detail": f"{sorted(runtimes)} resolve to one anchor, so one "
                              f"node secret signs for both. Each runtime signs with "
                              f"its own or the fourth isolation fact is not held; "
                              f"this host can see the sharing and cannot fix it",
                })
        declared = list(getattr(device, "runtime_bindings", ()) or ())
        if ownership == "company" and declared and not owned:
            gaps.append({
                "kind": "NO_BINDING", "device_ref": ref, "runtime": None,
                "detail": f"declares {declared} and this host holds no binding, so "
                          f"the runtimes are real and unreachable from here",
            })
        if ownership == "company" and owned and not declared:
            gaps.append({
                "kind": "RUNTIME_NOT_DECLARED", "device_ref": ref, "runtime": None,
                "detail": "this host holds a binding for a device the registry "
                          "declares no runtime for; one of the two is wrong",
            })
        devices.append({
            "device_ref": ref,
            # Read with `getattr`, like `ownership` above and for the reason
            # `company_devices` does: a survey over a device record it does not
            # fully understand should report the fields it does have rather than
            # refuse the whole document over one it does not.
            "display_name": getattr(device, "display_name", None),
            "ownership": ownership,
            "declared_runtimes": declared,
            "runtimes": rows,
            "runtime_count": len(rows),
        })

    bound_keys = {row["provider_key"] for d in devices for row in d["runtimes"]}
    grants = _grant_shape(config.policy.operator_grants)
    for key in sorted(set(grants) - bound_keys):
        gaps.append({
            "kind": "GRANT_FOR_UNBOUND_RUNTIME", "device_ref": key.split("|")[0],
            "runtime": key.split("|")[1] if "|" in key else None,
            "detail": f"a grant is written for {key!r}, which no binding declares. "
                      f"Nothing is granted by it and nothing is widened by it — it "
                      f"is authority recorded against a runtime that is not here",
        })

    company = [d for d in devices if d["ownership"] == "company"]
    excluded = [d for d in devices if d["ownership"] != "company"]
    return {
        "schema": RUNTIME_ROLLOUT_SCHEMA,
        "mode": "DRY_RUN",
        "state": "REPORTED",
        "devices": devices,
        # Sorted, like `company_devices()` above and for the same reason: this
        # is the same fact the module-level function reports, and a list whose
        # order follows however the registry happens to be authored is a plan
        # diff that flickers. `devices` above keeps the authored order, which is
        # what a detailed list should read in.
        "company_devices": sorted(d["device_ref"] for d in company),
        # The headline the whole request is about: how many PHYSICAL machines,
        # and how many runtimes. A device serving two runtimes is one machine and
        # two of these, so the two numbers are different facts — which is the one
        # thing a count of either alone cannot say.
        #
        # Counted over the devices IN SCOPE, with the rest named beside them.
        # Counting every device in the registry here said "5 physical devices"
        # for a rollout that targets three, and the two personal machines were
        # the difference: a headline that adds a machine the plan will never
        # touch to a number an operator reads as "what this will deploy" is the
        # same defect the hand-written device list had, moved into the total.
        "physical_devices": len(company),
        "runtimes": sum(d["runtime_count"] for d in company),
        # ...and the ones that are NOT, so the arithmetic closes: in-scope plus
        # excluded is every device the registry declares, and a reader can check
        # that rather than take it.
        "devices_in_scope": len(company),
        "devices_excluded": [
            {"device_ref": d["device_ref"], "ownership": d["ownership"]}
            for d in excluded
        ],
        "devices_seen_total": len(devices),
        "grants": grants,
        "gaps": gaps,
        "unresolved": len(gaps),
        "rollout": [
            {
                "stage": "A",
                "target": "host",
                "action": "ship the Fleet ingress carrying `provider_for(device, "
                          "runtime)` and the v2 signer, add the second binding, and "
                          "grant the operator the verbs it should hold ON THAT "
                          "RUNTIME — as separate entries, because the base runtime's "
                          "grants do not carry over",
                "gates": ["a v1-only fleet keeps answering exactly as before",
                          "no runtimes route anywhere until a binding exists"],
                "reversible_by": "repoint `ingress/current`; a v1-only fleet is "
                                 "unaffected because the base runtime still signs v1",
                "owner": "operator, with the maintenance approval a restart needs",
            },
            {
                "stage": "B",
                "target": "device (per user)",
                "action": "each OS account runs its own connector under its own "
                          "account, bound to `runtime: <its tag>`, with its own "
                          "CloudCLI credential, state root and node secret",
                "per_runtime": [
                    {"device_ref": d["device_ref"], "runtime": row["runtime"],
                     "provider_key": row["provider_key"]}
                    for d in company for row in d["runtimes"]
                    if row["runtime"] != "claude"
                ],
                "gates": ["the device's own runtime keeps signing v1 and is untouched",
                          "the second connector is a separate process, startable and "
                          "stoppable without touching the first"],
                "reversible_by": "stop that connector; the base runtime is untouched",
                # Said out loud because this plan is run BY the host and the work
                # is NOT the host's: the account, the credential, the state root
                # and the secret are all created as that other user, and none of
                # them can be produced from here.
                "owner": "the second account's user, on the device. This host does "
                         "not create their account, read their credential, or copy "
                         "a token into place, and a step that needed it would be a "
                         "step this plan cannot perform.",
            },
            {
                "stage": "C",
                "target": "oauth client (ChatGPT)",
                "action": "re-connect the connector so the tool list is re-read",
                "gates": ["the tool surface is unchanged by this rollout: the runtime "
                          "is a session-level fact, not a new tool",
                          "no scope is added by a second runtime"],
                "reversible_by": "re-consent; nothing on the device or the ingress "
                                 "is touched by a refresh",
                "owner": "the user",
            },
        ],
        # An empty list is the point: this function reads and returns. Named
        # rather than implied, so a reader can assert it rather than trust it.
        "writes": [],
        "note": "A second runtime on a company device changes the HOST and the "
                "DEVICE. It changes no OAuth scope, adds no tool, and never widens "
                "the eligible device set, which stays `ownership: company`.",
    }


def plan(registry_path=None, config_path=None) -> dict:
    """The precheck + per-device rollout + rollback, as data. Nothing is run.

    The two optional paths are for the multi-runtime survey, which must be
    pointable at a candidate registry and binding pair before either is
    installed. Left unset, both halves read what this host is actually
    configured with — which is what an operator running the script wants.
    """
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
        # The second half of the rollout: which PHYSICAL machines, which
        # runtimes on each, and every gap between what is declared and what is
        # bound. Kept in the same document as the ingress plan because they ship
        # in a fixed order (A host, then B device per user, then C reconnect) and
        # an operator reading one of them without the other would sequence them
        # wrong. It reports `writes: []`, and the ordering is stated inside it
        # rather than here.
        "runtime_bindings": runtime_bindings(registry_path, config_path),
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
        _rb = document["runtime_bindings"]
        if _rb.get("state") == "REPORTED":
            # Two numbers that are not each other: a machine serving two
            # runtimes is ONE device and TWO runtimes, and a line printing only
            # one of them would be read as the other.
            # An equation that adds up: in-scope + excluded = every device the
            # registry declares. A line naming only the in-scope count would be
            # the same omission the hand-written device list made.
            print(f"runtimes: {_rb['runtimes']} across {_rb['physical_devices']} "
                  f"physical device(s) in scope; {len(_rb['devices_excluded'])} "
                  f"excluded (ownership != company, "
                  f"{_rb['devices_seen_total']} seen); {_rb['unresolved']} gap(s)")
            for gap in _rb["gaps"]:
                where = gap["device_ref"]
                if gap["runtime"]:
                    where = f"{where}|{gap['runtime']}"
                print(f"  gap {gap['kind']}: {where} — {gap['detail']}")
        else:
            # Not an empty survey: a different answer, said as one.
            print(f"runtimes: UNAVAILABLE — {_rb.get('reason')}")
        for step in document["rollout"]:
            print(f"  rollout {step['step']}: {step['target']} — {step['action']}")
        for step in document["rollback"]:
            print(f"  rollback {step['step']}: {step['action']}")
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
