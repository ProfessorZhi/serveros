#!/usr/bin/env python3
"""Q1 RED baseline: the gates do not exist on the declared pre-change tree.

Runs against a tree WITHOUT the candidate changes (665067e for S1, 771c81c for
WE/T10). It asserts the ABSENCE that the green probes turn into behaviour:

  R1  `admit_work` has no writer gate: a binding that carries a superseded
      writer is still ADMITTED.
  R2  `held_leases` has no expiry filter: an expired row is reported as held.
  R3  the Work store has no exclusive lease API at all (T2/T3).
  R4  `finalize_run` has no T10-required route and concludes SUCCEEDED from the
      caller's own verdict/exit_code (legacy, unlabelled).
  R5  serverosd has no `agent.lease.authority.verify` read (B3).

Run: QA_TREE=<pre-change worktree> python3 qa_probe_q1_red.py
"""
from __future__ import annotations

import importlib.util
import inspect
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


coordination = load("red_coordination", REPO / "runtime" / "coordination.py")
resolver = load("red_resolver", REPO / "context" / "resolver.py")

# R1: a superseded writer claim is ignored by the old admission kernel.
old_binding = {
    "schema": "serveros/thread-binding.v1",
    "session_role": "execution",
    "bound_item_ref": "LIT-0101",
    "bound_item_ref_frozen": True,
    "state": "OPEN",
    "writer": {"owner": "session:A", "epoch": 1, "token": "1"},
}
try:
    verdict = coordination.admit_work(
        binding_record=old_binding, work_item="LIT-0101", item_state="ACTIVE")
    r1 = verdict.get("state") == "ADMITTED"
    r1_detail = repr(verdict)
except TypeError as exc:
    r1 = False
    r1_detail = f"TypeError:{exc}"
check("R1 old admission ignores a superseded writer claim", r1, r1_detail)

# R2: old held_leases has no expiry filter (and no injectable now).
with tempfile.TemporaryDirectory() as td:
    tmp = Path(td)
    db = tmp / "serveros.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE leases (resource TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "token TEXT NOT NULL, expires_at TEXT NOT NULL)")
    conn.execute("INSERT INTO leases VALUES ('work-write:EXPIRED','a','1',"
                 "'2000-01-01T00:00:00Z')")
    conn.commit()
    conn.close()
    roots = resolver.Roots(tmp / "ws", tmp / "art", db)
    try:
        held = resolver.held_leases(roots)
        r2 = "work-write:EXPIRED" in held
        r2_detail = f"held={held}"
    except TypeError as exc:
        r2 = False
        r2_detail = f"TypeError:{exc}"
check("R2 old held_leases reports an expired row as held", r2, r2_detail)

# R3/R4: the Work store's lease and T10 APIs.
sys.path.insert(0, str(REPO))
from workengine.store import ServerOSStore  # noqa: E402

has_lease = hasattr(ServerOSStore, "acquire_lease")
check("R3 Work store has no exclusive lease API", not has_lease,
      f"acquire_lease={has_lease}")

signature = inspect.signature(ServerOSStore.finalize_run)
has_t10 = "t10_required" in signature.parameters
check("R4 finalize_run has no T10-required route", not has_t10,
      f"params={sorted(signature.parameters)}")

# R4 behaviour: legacy finalize concludes SUCCEEDED from the caller's words.
from workengine import WorkItem  # noqa: E402
from workengine.model import (  # noqa: E402
    Activity, ContextMode, ExecutionContext, RunState)

with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    store = ServerOSStore(root / "state")
    store.register_project("demo", "DEM")
    store.create_item(WorkItem("DEM-0001", "demo", "x", "test", "/card"))
    store.add_activity(Activity("DEM-0001", "test", "test"))
    store.attach_context(ExecutionContext(
        "DEM-0001", "test", "repo", ContextMode.TEST, "base"))
    run = store.begin_run("DEM-0001", "test", "worker", "k", root / "artifacts")
    for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
        run = store.advance_run("DEM-0001", run.id, state)
    answer = store.finalize_run(
        "DEM-0001", run.id, verdict="PASS", exit_code=0, command="pytest",
        cases=["one"], revision="r", dirty=False)
    r4 = (answer.get("state") == "SUCCEEDED"
          and "authorization" not in answer
          and "t10_acceptable" not in answer)
    check("R4 caller PASS/0 concludes SUCCEEDED with no T10 label", r4,
          repr(answer))

# R5: no verified-read method on the daemon.
daemon_text = (REPO / "runtime" / "daemon.py").read_text(encoding="utf-8")
check("R5 serverosd has no agent.lease.authority.verify read",
      "agent.lease.authority.verify" not in daemon_text,
      "method present" if "agent.lease.authority.verify" in daemon_text else "")

failed = [name for name, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed}, ensure_ascii=False))
sys.exit(1 if failed else 0)
