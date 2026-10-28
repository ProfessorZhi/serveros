"""One named monitor case, run against whichever tree is on ``PYTHONPATH``.

Used for the #500 RED baseline and for mutation controls. It never decides
whether a behaviour is good; it reports what the real monitor did so the test
can compare a before/after pair.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

DEV = "device:tokyo-qa-1"
SESS = "device:tokyo-qa-1|claude|sess-qa"
VICTIM = "device:victim"
RUN = "run-qa-1"
DEL = "del-qa-1"
BOOT = "boot-qa-1"
SHA = "a" * 40

CASE = os.environ.get("QA_CASE", "ci-forged")
MODE = os.environ.get("QA_MODE", "deliver")
ROOT = Path(tempfile.mkdtemp(prefix="wave18-case-"))
STATE_DIR = Path(os.environ.get("QA_STATE_DIR", ROOT / "state"))
STATE_DIR.mkdir(parents=True, exist_ok=True)
JOURNAL = Path(os.environ.get("QA_JOURNAL", ROOT / "journal.jsonl"))
LEDGER = Path(os.environ.get("QA_LEDGER", ROOT / "ledger.sqlite"))


def run_fact(*, run_id: str | None = RUN, message: str | None = "native",
             status: str = "completed") -> dict:
    fact = {"instanceId": "inst-qa", "deviceRef": DEV, "sessionRef": SESS,
            "deliveryId": DEL, "bootId": BOOT, "status": status}
    if run_id is not None:
        fact["runId"] = run_id
    if message is not None:
        fact["lastAssistantMessage"] = {"content": message}
    return fact


def ci_fact() -> dict:
    return {"head_sha": SHA, "status": "completed", "conclusion": "success",
            "draft": False, "verified": True, "trusted": True,
            "name": "ci", "id": "99"}


def config(watch: dict, *, views: list[str], devices: list[str],
           projects: list[str] | None = None) -> dict:
    return {"schema": "serveros/fleet-next-agent-monitor-config.v1",
            "grant": {"principal": "qa", "devices": devices, "projects": projects or [],
                      "sessions": [SESS], "views": views},
            "watches": [watch],
            "bounds": {"debounce_ticks": 1}}


def watch_run(**overrides) -> dict:
    watch = {"kind": "run", "device_ref": DEV, "session_ref": SESS,
             "run_id": RUN, "delivery_id": DEL, "expected_boot_id": BOOT,
             "major": True}
    watch.update(overrides)
    return watch


def watch_ci(**overrides) -> dict:
    watch = {"kind": "ci", "expected_source_sha": SHA,
             "project": "serveros", "major": True}
    watch.update(overrides)
    return watch


def build(case: str):
    if case in {"ci-forged", "ci-valid"}:
        watch = watch_ci(device_ref=VICTIM) if case == "ci-forged" else watch_ci()
        return (config(watch, views=["ci"], devices=[DEV],
                       projects=["serveros"]),
                {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
                 "boot_id": BOOT, "ci": {SHA: ci_fact()}})
    if case == "expired":
        return (config(watch_run(), views=["run"], devices=[DEV]),
                {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
                 "boot_id": BOOT, "runs": {RUN: {"raise": "EXPIRED"}}})
    if case == "never-issued":
        return (config(watch_run(), views=["run"], devices=[DEV]),
                {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
                 "boot_id": BOOT, "runs": {}})
    if case == "running":
        return (config(watch_run(), views=["run"], devices=[DEV]),
                {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
                 "boot_id": BOOT, "runs": {RUN: run_fact(status="running")}})
    if case == "delivery-only-native":
        return (config(watch_run(run_id=None, delivery_id=DEL), views=["run"],
                       devices=[DEV]),
                {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
                 "boot_id": BOOT,
                 "runs": {"delivery:" + DEL: run_fact()}})
    if case == "delivery-only-no-run":
        return (config(watch_run(run_id=None, delivery_id=DEL), views=["run"],
                       devices=[DEV]),
                {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
                 "boot_id": BOOT,
                 "runs": {"delivery:" + DEL: run_fact(run_id=None)}})
    if case == "unknown":
        return (config(watch_run(), views=["run"], devices=[DEV]),
                {"fleet_instance_id": "inst-qa", "route_id": "route-qa",
                 "boot_id": BOOT, "runs": {RUN: run_fact()}})
    raise SystemExit(f"unknown QA_CASE {case!r}")


def main() -> int:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from fleet_next_agent_monitor.core import MonitorCore
    from fleet_next_agent_monitor.state import DurableState
    import fleet_next_agent_monitor.runtime as runtime
    import milestone_notify
    import qa_fakes

    document, facts = build(CASE)
    facts_path = ROOT / "facts.json"
    facts_path.write_text(json.dumps(facts), encoding="utf-8")
    config = runtime.load_config(document)
    source = runtime.build_source(config, offline_facts=facts_path)
    provider = qa_fakes.JournalProvider(JOURNAL, mode=MODE)
    notifier = milestone_notify.MilestoneNotifier(
        provider, LEDGER, conversation_ref="chat:qa")
    core = MonitorCore(source=source, notifier=notifier,
                       state=DurableState(STATE_DIR / "state.json"),
                       watches=config["watches"], debounce_ticks=1)
    tick = core.run_once()
    journal = []
    if JOURNAL.exists():
        journal = [json.loads(line) for line in
                   JOURNAL.read_text(encoding="utf-8").splitlines() if line.strip()]
    rows = []
    if LEDGER.exists():
        import sqlite3
        with sqlite3.connect(LEDGER) as db:
            db.row_factory = sqlite3.Row
            try:
                rows = [dict(row) for row in db.execute(
                    "SELECT delivery_key, state FROM milestone_deliveries")]
            except sqlite3.Error:
                rows = []
    print(json.dumps({
        "case": CASE,
        "state": tick.get("state"),
        "exit": tick.get("exit"),
        "dispatched": tick.get("dispatched"),
        "delivered": tick.get("delivered"),
        "duplicate": tick.get("duplicate"),
        "unknown_receipts": tick.get("unknown_receipts"),
        "backlog": tick.get("backlog"),
        "reads": tick.get("reads"),
        "counts": tick.get("counts"),
        "watch": (tick.get("watches") or [{}])[0],
        "journal": journal,
        "ledger": rows,
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
