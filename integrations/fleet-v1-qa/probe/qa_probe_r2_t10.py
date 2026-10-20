#!/usr/bin/env python3
"""Q1 independent T10 / finalize-evidence probe for the S1 acceptance round.

MODE=red   (origin/main 771c81c): prove the old behaviour is unlabelled and
           that `t10_required` does not exist.
MODE=green (a candidate with #297/#304): prove the T10 route refuses without a
           trusted parent, an unsigned terminal Run cannot be relabelled, a
           tampered/forged finalize.json is refused by its evidence anchor, and
           a failed final rename does not conclude the Run.

Run: QA_TREE=<tree> QA_MODE=red|green python3 qa_probe_r2_t10.py
"""
from __future__ import annotations

import inspect
import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
MODE = os.environ.get("QA_MODE", "green").strip().lower()
sys.path.insert(0, str(REPO))

from workengine import WorkItem  # noqa: E402
from workengine.model import (  # noqa: E402
    Activity, ContextMode, ExecutionContext, RunState)
from workengine.store import ServerOSStore  # noqa: E402

ITEM = "DEM-0001"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


def fresh_run(store, root, key):
    run = store.begin_run(ITEM, "test", "worker", key, root / "artifacts")
    for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
        run = store.advance_run(ITEM, run.id, state)
    return run


def evidence_count(store, run):
    with store.connection() as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE item_id=? AND run_id=?",
            (ITEM, run.id)).fetchone()[0]


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    store = ServerOSStore(root / "state")
    store.register_project("demo", "DEM")
    store.create_item(WorkItem(ITEM, "demo", "x", "test", "/card"))
    store.add_activity(Activity(ITEM, "test", "test"))
    store.attach_context(ExecutionContext(
        ITEM, "test", "repo", ContextMode.TEST, "base"))

    signature = inspect.signature(store.finalize_run)
    has_t10 = "t10_required" in signature.parameters

    if MODE == "red":
        check("RED finalize_run has no t10_required route", not has_t10,
              f"params={sorted(signature.parameters)}")
        run = fresh_run(store, root, "red")
        answer = store.finalize_run(
            ITEM, run.id, verdict="PASS", exit_code=0, command="pytest",
            cases=["one"], revision="r", dirty=False)
        check("RED caller PASS/0 concludes SUCCEEDED with no authorization label",
              answer.get("state") == "SUCCEEDED"
              and "authorization" not in answer
              and "t10_acceptable" not in answer,
              repr(answer))
        failed = [n for n, ok, _ in results if not ok]
        print(json.dumps({"mode": MODE, "checks": len(results),
                          "failed": failed}, ensure_ascii=False))
        sys.exit(1 if failed else 0)

    # ---- GREEN (candidate with the T10 route and evidence anchors) ----
    from workengine.authority import (
        ReceiptError, T10Binding, VerifiedRunReceipt)

    DIGEST = "a" * 64
    ANCHOR = "b" * 64
    COMMIT = "c" * 40
    ACTOR = "human:q1"
    ACTION_KEY = "action:q1:1"
    RECEIPT_REF = "trusted:receipt:q1"

    class StubAuthority:
        def __init__(self, receipt):
            self.receipt = receipt

        def verify(self, receipt_ref, *, item_ref, run_ref, now):
            if receipt_ref != RECEIPT_REF:
                raise ValueError("unknown receipt")
            return self.receipt

    def receipt_for(run_id):
        return VerifiedRunReceipt(
            item_ref=ITEM, run_ref=run_id, action_key=ACTION_KEY,
            input_digest=DIGEST, receipt_sha256=ANCHOR, process_exit_code=0,
            verdict="PASS", runner_identity="runner:q1", commit_sha=COMMIT,
            actor_ref=ACTOR)

    check("GREEN finalize_run exposes t10_required", has_t10,
          f"params={sorted(signature.parameters)}")

    # 1. No trusted parent.
    run1 = fresh_run(store, root, "g1")
    try:
        store.finalize_run(
            ITEM, run1.id, verdict="PASS", exit_code=0, command="pytest",
            cases=["one"], revision="r", dirty=False, t10_required=True)
        blocked = False
        reason = ""
    except ReceiptError as exc:
        blocked = True
        reason = exc.reason_code
    check("GREEN t10_required without authority is BLOCKED_TRUST_SOURCE",
          blocked and reason == "BLOCKED_TRUST_SOURCE",
          f"reason={reason}")
    check("GREEN refusal leaves the Run FINALIZING",
          store.get_run(ITEM, run1.id).state == RunState.FINALIZING,
          str(store.get_run(ITEM, run1.id).state))
    check("GREEN refusal writes no evidence", evidence_count(store, run1) == 0,
          f"evidence={evidence_count(store, run1)}")

    # 2. Legacy is explicitly UNSIGNED.
    legacy = store.finalize_run(
        ITEM, run1.id, verdict="PASS", exit_code=0, command="pytest",
        cases=["one"], revision="r", dirty=False)
    check("GREEN legacy finalize is labelled UNSIGNED",
          legacy.get("authorization") == "UNSIGNED"
          and legacy.get("t10_acceptable") is False, repr(legacy))

    # 3. A forged finalize.json is refused by the evidence anchor.
    path1 = Path(store.get_run(ITEM, run1.id).artifact_dir) / "finalize.json"
    forged = json.loads(path1.read_text(encoding="utf-8"))
    forged["authorization"] = "T10_VERIFIED"
    forged["runner_receipt"] = {
        "action_key": ACTION_KEY, "input_digest": DIGEST,
        "receipt_sha256": ANCHOR, "process_exit_code": 0,
        "runner_identity": "runner:forged", "commit_sha": COMMIT,
        "actor_ref": ACTOR,
    }
    path1.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    try:
        reread = store.finalize_run(
            ITEM, run1.id, verdict="PASS", exit_code=0, command="pytest",
            cases=["one"], revision="r", dirty=False)
        forged_label = reread.get("authorization")
        forged_raised = None
    except ReceiptError as exc:
        forged_label = None
        forged_raised = exc.reason_code
    check("GREEN a forged finalize.json is not read as T10_VERIFIED",
          forged_label != "T10_VERIFIED",
          f"label={forged_label!r} raised={forged_raised!r}")
    check("GREEN the forged file is refused by its anchor",
          forged_raised in ("EVIDENCE_HASH_MISMATCH", "EVIDENCE_TORN",
                            "EVIDENCE_MISSING"),
          repr(forged_raised))

    # 4. A genuine T10_VERIFIED record is still readable; tampering it is not.
    run2 = fresh_run(store, root, "g2")
    verified = store.finalize_run(
        ITEM, run2.id, verdict="PASS", exit_code=0, command="pytest",
        cases=["one"], revision="r", dirty=False, t10_required=True,
        t10_binding=T10Binding(ITEM, run2.id, COMMIT, ACTOR),
        receipt_ref=RECEIPT_REF,
        receipt_authority=StubAuthority(receipt_for(run2.id)))
    check("GREEN a test-double verified receipt is T10_VERIFIED",
          verified.get("authorization") == "T10_VERIFIED"
          and verified.get("t10_acceptable") is True, repr(verified))
    path2 = Path(store.get_run(ITEM, run2.id).artifact_dir) / "finalize.json"
    body2 = json.loads(path2.read_text(encoding="utf-8"))
    body2["runner_receipt"]["actor_ref"] = "human:FORGED"
    path2.write_text(json.dumps(body2) + "\n", encoding="utf-8")
    try:
        store.finalize_run(
            ITEM, run2.id, verdict="PASS", exit_code=0, command="pytest",
            cases=["one"], revision="r", dirty=False)
        tampered_label = "READ"
    except ReceiptError as exc:
        tampered_label = exc.reason_code
    check("GREEN a tampered T10 file is refused", tampered_label != "READ",
          repr(tampered_label))

    # 5. A failed final rename must not conclude the Run.
    run3 = fresh_run(store, root, "g3")
    path3 = Path(store.get_run(ITEM, run3.id).artifact_dir) / "finalize.json"
    path3.mkdir()
    try:
        store.finalize_run(
            ITEM, run3.id, verdict="PASS", exit_code=0, command="pytest",
            cases=["one"], revision="r", dirty=False)
        injected = "NO-ERROR"
    except Exception as exc:  # noqa: BLE001
        injected = type(exc).__name__
    check("GREEN a failed final rename raises", injected != "NO-ERROR", injected)
    check("GREEN a failed final rename leaves the Run FINALIZING",
          store.get_run(ITEM, run3.id).state == RunState.FINALIZING,
          str(store.get_run(ITEM, run3.id).state))
    check("GREEN a failed final rename writes no evidence",
          evidence_count(store, run3) == 0,
          f"evidence={evidence_count(store, run3)}")

failed = [n for n, ok, _ in results if not ok]
print(json.dumps({"mode": MODE, "checks": len(results),
                  "failed": failed}, ensure_ascii=False))
sys.exit(1 if failed else 0)
