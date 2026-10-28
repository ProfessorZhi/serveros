"""Reproduce the four pre-fix defects on the Wave 14 RC #494 tree.

This is the RED baseline for the Wave 16 QA. It is deliberately a separate
program: the combined suite imports the fixed tree, so running the same file
against #494 would be a second kind of test. Here the old tree is named by
``QA_BASELINE_ROOT`` and the four observations are reported as a JSON object.

Exit 0 means **all four old defects were reproduced**. Exit 1 means the
baseline did not behave as recorded (the probe is stale, or the tree is not
#494). The test treats this as evidence of the before picture, never as a
product pass.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

QA_DIR = Path(__file__).resolve().parent
ROOT = Path(os.environ["QA_BASELINE_ROOT"]).resolve()
MONITOR = ROOT / "integrations" / "fleet-next-agent-monitor"
ACTIVATION = ROOT / "integrations" / "fleet-next-agent-monitor-activation"
NOTIFIER = ROOT / "integrations" / "fleet-v1-milestone-notify"
SOURCE = ROOT / "integrations" / "fleet-next-monitor-source-adapter"
PATHS = [str(MONITOR), str(ACTIVATION), str(NOTIFIER), str(SOURCE)]

DEV = "device:tokyo-qa-1"
SESS = "device:tokyo-qa-1|claude|sess-qa"
RUN = "run-qa-1"
DEL = "del-qa-1"
BOOT = "boot-qa-1"
SHA = "a" * 40


def _env(extra: dict | None = None) -> dict[str, str]:
    env = dict(os.environ)
    for key in list(env):
        if key.startswith("FLEET_NEXT_AGENT_MONITOR_"):
            env.pop(key)
    env["PYTHONPATH"] = os.pathsep.join([str(QA_DIR), *PATHS])
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def _monitor(directory: Path, facts: dict, config: dict,
             extra: dict) -> tuple[int, dict]:
    directory.mkdir(parents=True, exist_ok=True)
    facts_path = directory / "facts.json"
    config_path = directory / "config.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    config_path.write_text(json.dumps(config), encoding="utf-8")
    args = [sys.executable, "-m", "fleet_next_agent_monitor",
            "--config", str(config_path),
            "--state", str(directory / "state.json"),
            "--offline-facts", str(facts_path),
            "--notifier-entry", "qa_fakes:notifier_factory"]
    done = subprocess.run(args, cwd=str(directory), env=_env(extra),
                          capture_output=True, text=True, timeout=120)
    return done.returncode, json.loads(done.stdout) if done.stdout.strip() else {}


def _activation(directory: Path) -> tuple[int, dict]:
    legacy = directory / "legacy"
    nxt = directory / "next"
    legacy.mkdir(parents=True, exist_ok=True)
    nxt.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, str(QA_DIR / "run_activation.py"),
            "--legacy-root", str(legacy), "--next-root", str(nxt),
            "--state", str(nxt / "state.json"), "--lock", str(nxt / "lock"),
            "--port", "19201", "--legacy-port", "19200",
            "--service-name", "serveros-next-agent-monitor",
            "--legacy-service-name", "serveros-legacy",
            "--plugin-id", "serveros-next-monitor",
            "--legacy-plugin-id", "serveros-legacy", "--ticks", "1",
            "--monitor-entry", "fleet_next_agent_monitor.runtime:tick",
            "--legacy-read", "activation:probe_true",
            "--next-write", "activation:probe_false"]
    env = _env({"QA_ROOTS": json.dumps([str(QA_DIR), *PATHS])})
    done = subprocess.run(args, cwd=str(directory), env=env,
                          capture_output=True, text=True, timeout=120)
    return done.returncode, json.loads(done.stdout) if done.stdout.strip() else {}


def _run_fact(status: str = "completed", message: str | None = "native") -> dict:
    fact = {"instanceId": "inst-qa", "deviceRef": DEV, "sessionRef": SESS,
            "runId": RUN, "deliveryId": DEL, "bootId": BOOT, "status": status}
    if message is not None:
        fact["lastAssistantMessage"] = {"content": message}
    return fact


def _run_config() -> dict:
    return {
        "schema": "serveros/fleet-next-agent-monitor-config.v1",
        "grant": {"principal": "qa", "devices": [DEV], "sessions": [SESS],
                  "projects": [], "views": ["run"]},
        "watches": [{"kind": "run", "device_ref": DEV, "session_ref": SESS,
                     "run_id": RUN, "delivery_id": DEL,
                     "expected_boot_id": BOOT, "major": True}],
        "bounds": {"debounce_ticks": 1},
    }


def main() -> int:
    directory = Path(tempfile.mkdtemp(prefix="wave16-baseline-"))
    result: dict[str, object] = {}

    # 1. old core: DELIVERY_UNKNOWN becomes SETTLED on the next tick
    facts = {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
             "boot_id": BOOT, "runs": {RUN: _run_fact()}}
    common = {"QA_JOURNAL": str(directory / "journal.jsonl"),
              "QA_LEDGER": str(directory / "ledger.sqlite"),
              "QA_PROVIDER_MODE": "unknown"}
    # The SAME state directory: the false SETTLED is a fact about the second
    # tick reading the first tick's cursor, not about a fresh state file.
    first_code, first = _monitor(directory / "unknown", facts,
                                 _run_config(), common)
    second_code, second = _monitor(directory / "unknown", facts,
                                   _run_config(), common)
    result["unknown_delivery"] = {
        "tick1_state": first.get("state"), "tick1_exit": first_code,
        "tick2_state": second.get("state"), "tick2_exit": second_code,
        "tick2_receipt": (second.get("watches") or [{}])[0].get("receipt"),
    }

    # 2. old activation: a blocked tick exits 0
    code, activation = _activation(directory / "activation")
    result["activation_blocked"] = {
        "state": activation.get("state"), "exit": code,
        "health": activation.get("health"),
    }

    # 3. old adapter: #493's observe_watch passes expected_boot_id and the
    #    adapter does not accept it
    sys.path[:0] = PATHS
    from fleet_next_agent_monitor.sources import observe_watch
    from fleet_next_monitor_source_adapter import (
        CallerGrant, FixtureLegacyClient, LegacyReadOnlySourceAdapter)

    grant = CallerGrant.from_mapping({
        "principal": "qa", "devices": [DEV], "projects": [],
        "sessions": [SESS], "views": ["run"]})
    adapter = LegacyReadOnlySourceAdapter(
        FixtureLegacyClient({"run": _run_fact()}, fleet_instance_id="inst-qa",
                            route_id="route-qa", boot_id=BOOT), grant)
    try:
        observe_watch(adapter, {"kind": "run", "device_ref": DEV,
                                "session_ref": SESS, "run_id": RUN,
                                "expected_boot_id": BOOT})
        result["adapter_direct"] = "NO_ERROR"
    except Exception as exc:  # noqa: BLE001 - the exception type is the evidence
        result["adapter_direct"] = f"{type(exc).__name__}: {exc}"

    # 4. old CI payload: the monitor sends device_ref=""
    ci_facts = {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
                "boot_id": BOOT, "ci": {SHA: {
                    "head_sha": SHA, "status": "completed",
                    "conclusion": "success", "draft": False,
                    "verified": True, "trusted": True, "name": "ci", "id": "9"}}}
    ci_config = {
        "schema": "serveros/fleet-next-agent-monitor-config.v1",
        "grant": {"principal": "qa", "devices": [], "projects": ["serveros"],
                  "sessions": [], "views": ["ci"]},
        "watches": [{"kind": "ci", "expected_source_sha": SHA,
                     "project": "serveros", "major": True}],
        "bounds": {"debounce_ticks": 1},
    }
    _, ci_tick = _monitor(directory / "ci", ci_facts, ci_config,
                          {"QA_JOURNAL": str(directory / "ci-journal.jsonl"),
                           "QA_LEDGER": str(directory / "ci-ledger.sqlite"),
                           "QA_PROVIDER_MODE": "deliver"})
    result["ci_payload"] = {
        "state": ci_tick.get("state"),
        "delivered": ci_tick.get("delivered"),
        "receipt": (ci_tick.get("watches") or [{}])[0].get("receipt"),
        "why": (ci_tick.get("watches") or [{}])[0].get("receipt_why"),
    }

    print(json.dumps(result, indent=2, sort_keys=True))
    old_unknown = (result["unknown_delivery"]["tick1_state"] == "UNCONFIRMED"
                   and result["unknown_delivery"]["tick2_state"] == "SETTLED")
    old_activation = (result["activation_blocked"]["state"] == "BOUNDED_STOP"
                      and result["activation_blocked"]["exit"] == 0)
    old_adapter = str(result["adapter_direct"]).startswith("TypeError:")
    old_ci = (result["ci_payload"]["delivered"] == 0
              and "device_ref" in str(result["ci_payload"]["why"]))
    return 0 if all((old_unknown, old_activation, old_adapter, old_ci)) else 1


if __name__ == "__main__":
    raise SystemExit(main())
