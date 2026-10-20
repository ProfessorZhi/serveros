#!/usr/bin/env python3
"""Q1 round-6 probe: Fleet Next unit-name P0 and the authority boundary.

Independent of the author tests. Loads the committed preflight and installer
from the candidate tree and checks:

  * the committed Next unit name/template exists and differs from V1;
  * the templates keep distinct unit names and SyslogIdentifier, and the Next
    template keeps the bounded canary limits;
  * a valid proposal is PRECHECK_READY; same-unit / overlapping path / same
    port / same public URL / missing owner attestation are BLOCKED;
  * canary mode excuses only the two client-registration facts, not owner or
    rollback;
  * `canary-start --no-start` is refused with no write/execute;
  * the installer source ever starts/stops/enables only the Next unit, and
    `--execute` requires `--owner-approved`;
  * the workflow path filters name files that exist and the job is offline.

No systemd, no network, no token, no production data.

Run: QA_TREE=<candidate> python3 qa_probe_r6_fleet_next_gate.py
"""
from __future__ import annotations

import copy
import importlib.util
import json
import os
import re
import sys
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(REPO))


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = load("q1r6_gate", REPO / "fleet_mcp" / "deploy"
            / "next_dual_stack_preflight.py")
installer = load("q1r6_install", REPO / "fleet_mcp" / "deploy"
                 / "next_install.py")

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def proposal():
    return {
        "schema": gate.SCHEMA,
        "v1": {
            "service_unit": "serveros-fleet-mcp.service",
            "runtime_root": "/tmp/q1-fleet-v1/ingress",
            "config_path": "/tmp/q1-fleet-v1/config/binding.yaml",
            "oauth_state_root": "/tmp/q1-fleet-v1/oauth",
            "local_listen": {"host": "127.0.0.1", "port": 18101},
            "public_mcp_url": "https://v1.q1.invalid/mcp",
            "plugin_id": "plugin_q1_v1",
        },
        "next": {
            "service_unit": "serveros-fleet-next-mcp.service",
            "runtime_root": "/tmp/q1-fleet-next/ingress-next",
            "config_path": "/tmp/q1-fleet-next/config/binding.yaml",
            "oauth_state_root": "/tmp/q1-fleet-next/oauth",
            "local_listen": {"host": "127.0.0.1", "port": 18102},
            "public_mcp_url": "https://next.q1.invalid/mcp",
            "plugin_id": "plugin_q1_next",
        },
        "limits": {
            "memory_max_mib": 1536, "memory_swap_max_mib": 0,
            "cpu_quota_pct": 40, "tasks_max": 96,
        },
        "attestations": {
            "v1_identity_observed": True,
            "next_owner_binding_reviewed": True,
            "next_oauth_client_authorized": True,
            "rollback_ready": True,
        },
    }


def evaluate(p, **kwargs):
    return gate.evaluate(p, **kwargs)


def blocked_with(p, needle, **kwargs):
    result = evaluate(p, **kwargs)
    return (result["decision"] == "BLOCKED"
            and any(needle in b for b in result["blockers"]))


# -- unit/template names ----------------------------------------------------
check("NEXT_UNIT differs from V1_UNIT",
      gate.NEXT_UNIT != gate.V1_UNIT,
      f"{gate.NEXT_UNIT} vs {gate.V1_UNIT}")
for unit in (gate.NEXT_UNIT, gate.V1_UNIT):
    path = REPO / "fleet_mcp" / "deploy" / unit
    check(f"committed template exists for {unit}", path.is_file(), str(path))

next_template = (REPO / "fleet_mcp" / "deploy" / gate.NEXT_UNIT).read_text(
    encoding="utf-8")
v1_template = (REPO / "fleet_mcp" / "deploy" / gate.V1_UNIT).read_text(
    encoding="utf-8")
next_syslog = re.search(r"^SyslogIdentifier=(\S+)$", next_template, re.M)
v1_syslog = re.search(r"^SyslogIdentifier=(\S+)$", v1_template, re.M)
check("Next SyslogIdentifier is the Next name",
      bool(next_syslog) and next_syslog.group(1) == "serveros-fleet-next-mcp",
      next_syslog.group(1) if next_syslog else "missing")
check("V1 SyslogIdentifier is the V1 name",
      bool(v1_syslog) and v1_syslog.group(1) == "serveros-fleet-mcp",
      v1_syslog.group(1) if v1_syslog else "missing")
check("journal identifiers are distinct",
      bool(next_syslog) and bool(v1_syslog)
      and next_syslog.group(1) != v1_syslog.group(1), "")
for required in ("MemoryMax=1536M", "MemorySwapMax=0", "CPUQuota=40%",
                 "TasksMax=96", "NoNewPrivileges=yes",
                 "@FLEET_MCP_RUNTIME@", "@FLEET_MCP_CONFIG@"):
    check(f"Next template keeps {required}", required in next_template, "")
check("V1 template does not carry the Next canary limits",
      "MemoryMax=1536M" not in v1_template, "")

# -- preflight boundary negatives ------------------------------------------
good = proposal()
ready = evaluate(good)
check("valid proposal is PRECHECK_READY_FOR_HUMAN_CANARY",
      ready["decision"] == "PRECHECK_READY_FOR_HUMAN_CANARY"
      and ready["blockers"] == [] and ready["writes"] == []
      and ready["executes"] == [] and ready["v1_changed"] is False,
      repr(ready)[:240])

same_unit = copy.deepcopy(good)
same_unit["next"]["service_unit"] = gate.V1_UNIT
check("same unit as V1 is BLOCKED",
      blocked_with(same_unit, "must use dedicated Next unit"),
      repr(evaluate(same_unit)["blockers"]))

same_path = copy.deepcopy(good)
same_path["next"]["runtime_root"] = good["v1"]["runtime_root"] + "/nested"
check("nested/overlapping runtime root is BLOCKED",
      blocked_with(same_path, "overlaps V1"),
      repr(evaluate(same_path)["blockers"]))

same_port = copy.deepcopy(good)
same_port["next"]["local_listen"]["port"] = good["v1"]["local_listen"]["port"]
check("same listening port is BLOCKED",
      blocked_with(same_port, "conflicts with V1 listening port"),
      repr(evaluate(same_port)["blockers"]))

same_url = copy.deepcopy(good)
same_url["next"]["public_mcp_url"] = good["v1"]["public_mcp_url"]
check("same public endpoint is BLOCKED",
      blocked_with(same_url, "same published endpoint as V1"),
      repr(evaluate(same_url)["blockers"]))

owner_denied = copy.deepcopy(good)
owner_denied["attestations"]["next_owner_binding_reviewed"] = False
check("missing owner attestation is BLOCKED (normal)",
      blocked_with(owner_denied, "owner"),
      repr(evaluate(owner_denied)["blockers"]))
check("missing owner attestation is BLOCKED (canary)",
      blocked_with(owner_denied, "owner", canary=True),
      repr(evaluate(owner_denied, canary=True)["blockers"]))

canary_bootstrap = copy.deepcopy(good)
canary_bootstrap["next"]["plugin_id"] = None
canary_bootstrap["attestations"]["next_oauth_client_authorized"] = False
canary_ready = evaluate(canary_bootstrap, canary=True)
check("canary mode excuses only the two client-registration facts",
      canary_ready["decision"] == "CANARY_START_READY"
      and canary_ready["blockers"] == [], repr(canary_ready)[:240])
normal_bootstrap = evaluate(canary_bootstrap)
check("normal install still refuses the unassigned client facts",
      normal_bootstrap["decision"] == "BLOCKED",
      repr(normal_bootstrap["blockers"]))

# -- installer gate --------------------------------------------------------
no_start = installer.plan(good, installer.CANARY_START, no_start=True)
check("canary-start --no-start is BLOCKED with no action",
      no_start["decision"] == "BLOCKED" and no_start["writes"] == []
      and no_start["executes"] == [], repr(no_start)[:220])
stage = installer.plan(good, "stage")
check("stage is a no-start plan (writes/executes empty)",
      stage["decision"] == "DRY_RUN_READY"
      and stage["writes"] == [] and stage["executes"] == []
      and stage["mode"] == "NEXT_ONLY_STAGE_NO_START", repr(stage)[:240])

src = (REPO / "fleet_mcp" / "deploy" / "next_install.py").read_text(
    encoding="utf-8")
check("installer starts/stops only UNIT, never V1_UNIT",
      'systemctl("start", UNIT' in src
      and not re.search(r'systemctl\(\s*"(start|stop|restart|enable|disable)"\s*,\s*V1_UNIT', src),
      "")
check("installer defines STAGING_COMMANDS as the two no-start verbs",
      'STAGING_COMMANDS = ("stage", "refresh-stage")' in src, "")
check("--execute requires --owner-approved",
      'if not args.owner_approved' in src
      and "requires explicit --owner-approved" in src, "")

# -- workflow --------------------------------------------------------------
workflow = (REPO / ".github" / "workflows"
            / "fleet-next-isolated-contracts.yml").read_text(encoding="utf-8")
paths = set(re.findall(r"'(fleet_mcp/deploy/[^']+\.service)'", workflow))
check("workflow names the committed Next template",
      paths == {"fleet_mcp/deploy/serveros-fleet-next-mcp.service"}, repr(paths))
check("workflow unit path exists",
      all((REPO / p).is_file() for p in paths), repr(paths))
check("workflow has contents: read and offline unittest commands",
      "permissions:\n  contents: read" in workflow
      and "python3 -m unittest discover" in workflow
      and "systemctl" not in workflow, "")
check("workflow does not install or enable a service",
      "systemctl" not in workflow
      and "deploy/install.py" not in workflow
      and not re.search(r"(?:^|\s)install\.py(?:\s|$)", workflow), "")

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed},
                 ensure_ascii=False))
sys.exit(1 if failed else 0)
