#!/usr/bin/env python3
"""Q1 independent probe for #237 T2/T3/T4/T6/T10 on the WE #295-#298 tip.

No product edits. Temp SQLite state only; no daemon, no network, no provider.

  1. T2/T3: two holders cannot hold one scope; a successor outranks the old
     token; an expired holder is refused at the commit boundary.
  2. T6: an expired row is not reported as held, and is not deleted.
  3. T4: EFFECT_UNKNOWN is NOT produced by this tree (confirm absence).
  4. T10: t10_required=True without a trusted parent refuses and leaves the
     Run FINALIZING with no evidence; legacy finalize is labelled UNSIGNED.
  5. Integrity: a T10_VERIFIED label read back from an EDITED finalize.json
     without checking the evidence row sha256 (label forgery surface).
  6. Failure injection: an unwritable finalize path must not conclude the Run.

Run: QA_TREE=<candidate worktree> python3 qa_probe_q1_we.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO = Path(os.environ["QA_TREE"]).resolve()
sys.path.insert(0, str(REPO))

from workengine import WorkItem  # noqa: E402
from workengine.authority import (  # noqa: E402
    ReceiptError, T10Binding, VerifiedRunReceipt)
from workengine.model import (  # noqa: E402
    Activity, ContextMode, ExecutionContext, RunState)
from workengine.store import LeaseError, ServerOSStore  # noqa: E402

ITEM = "DEM-0001"
RESOURCE = f"work-write:{ITEM}"
DIGEST = "a" * 64
ANCHOR = "b" * 64
COMMIT = "c" * 40
ACTION_KEY = "action:q1:1"
ACTOR = "human:q1"
RECEIPT_REF = "trusted:receipt:q1"
results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition), detail))
    print(f"{'PASS' if condition else 'FAIL'} {name}"
          + (f" :: {detail}" if detail else ""))


class StubReceiptAuthority:
    """A test double. It is NOT a production trust source."""

    def __init__(self, receipt):
        self.receipt = receipt

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        if receipt_ref != RECEIPT_REF:
            raise ValueError("unknown receipt")
        return self.receipt


def receipt_for(item, run):
    return VerifiedRunReceipt(
        item_ref=item, run_ref=run, action_key=ACTION_KEY,
        input_digest=DIGEST, receipt_sha256=ANCHOR, process_exit_code=0,
        verdict="PASS", runner_identity="runner:q1", commit_sha=COMMIT,
        actor_ref=ACTOR)


def fresh_run(store, artifact_root, key):
    run = store.begin_run(ITEM, "test", "worker", key, artifact_root)
    for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
        run = store.advance_run(ITEM, run.id, state)
    return run


with tempfile.TemporaryDirectory() as td:
    root = Path(td)
    store = ServerOSStore(root / "state")
    store.register_project("demo", "DEM")
    store.create_item(WorkItem(ITEM, "demo", "x", "test", "/card"))
    store.add_activity(Activity(ITEM, "test", "test"))
    store.attach_context(ExecutionContext(
        ITEM, "test", "repo", ContextMode.TEST, "base"))
    artifacts = root / "artifacts"

    # ---- T2/T3/T6: the exclusive lease --------------------------------
    first = store.acquire_lease(RESOURCE, owner="agent:A", ttl_s=30, now=1000.0)
    try:
        store.acquire_lease(RESOURCE, owner="agent:B", ttl_s=30, now=1001.0)
        held_refused = False
        held_reason = ""
    except LeaseError as exc:
        held_refused = True
        held_reason = exc.reason_code
    check("T2 second live holder is refused", held_refused,
          f"refused={held_refused} reason={held_reason}")

    second = store.acquire_lease(RESOURCE, owner="agent:B", ttl_s=30, now=1031.0)
    check("T2 successor gets a higher token", second["token"] > first["token"],
          f"{first['token']} -> {second['token']}")
    try:
        store.check_writer(RESOURCE, owner="agent:A", token=first["token"],
                           now=1032.0)
        stale_refused = False
        stale_reason = ""
    except LeaseError as exc:
        stale_refused = True
        stale_reason = exc.reason_code
    check("T3 old token after promotion is refused", stale_refused,
          f"refused={stale_refused} reason={stale_reason}")

    third = store.acquire_lease(RESOURCE, owner="agent:C", ttl_s=10, now=2000.0)
    try:
        store.check_writer(RESOURCE, owner="agent:C", token=third["token"],
                           now=2011.0)
        expired_refused = False
        expired_reason = ""
    except LeaseError as exc:
        expired_refused = True
        expired_reason = exc.reason_code
    check("T3 expired holder is refused at commit", expired_refused,
          f"refused={expired_refused} reason={expired_reason}")
    check("T6 expired row is not held", RESOURCE not in store.held_leases(now=2011.0),
          repr(store.held_leases(now=2011.0)))
    with store.transaction() as conn:
        rows = conn.execute("SELECT resource FROM leases").fetchall()
    check("T6 expired row is evidence, not deleted",
          [row["resource"] for row in rows] == [RESOURCE], repr(rows))

    # ---- T4: EFFECT_UNKNOWN -------------------------------------------
    # The B2 effect path lives in fleet/, runtime/ and workengine/. An
    # occurrence elsewhere (intake/) is not a producer for this gate.
    relevant = []
    for sub in ("fleet", "runtime", "workengine", "mcp"):
        for path in (REPO / sub).rglob("*.py"):
            try:
                text = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if "EFFECT_UNKNOWN" in text:
                relevant.append(str(path.relative_to(REPO)))
    elsewhere = []
    for path in REPO.rglob("*.py"):
        if any(part in (".git", "tests") for part in path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if "EFFECT_UNKNOWN" in text and not any(
                part in ("fleet", "runtime", "workengine", "mcp")
                for part in path.relative_to(REPO).parts):
            elsewhere.append(str(path.relative_to(REPO)))
    check("T4 EFFECT_UNKNOWN has no producer on the B2 effect path",
          not relevant, f"relevant={relevant} elsewhere={elsewhere}")

    # ---- T10: unsigned terminal run -----------------------------------
    run1 = fresh_run(store, artifacts, "k1")
    try:
        store.finalize_run(ITEM, run1.id, verdict="PASS", exit_code=0,
                           command="pytest", cases=["one"], revision="r",
                           dirty=False, t10_required=True)
        blocked = False
        blocked_reason = ""
    except ReceiptError as exc:
        blocked = True
        blocked_reason = exc.reason_code
    check("T10 no trusted parent refuses", blocked and blocked_reason == "BLOCKED_TRUST_SOURCE",
          f"refused={blocked} reason={blocked_reason}")
    check("T10 refusal leaves the Run FINALIZING",
          store.get_run(ITEM, run1.id).state == RunState.FINALIZING,
          str(store.get_run(ITEM, run1.id).state))

    legacy = store.finalize_run(ITEM, run1.id, verdict="PASS", exit_code=0,
                                command="pytest", cases=["one"], revision="r",
                                dirty=False)
    check("legacy finalize is labelled UNSIGNED",
          legacy.get("authorization") == "UNSIGNED"
          and legacy.get("t10_acceptable") is False, repr(legacy))
    stored = json.loads(
        (Path(store.get_run(ITEM, run1.id).artifact_dir) / "finalize.json")
        .read_text(encoding="utf-8"))
    check("unsigned conclusion is labelled in the durable record",
          stored.get("authorization") == "UNSIGNED"
          and "runner_receipt" not in stored, repr(stored.get("authorization")))

    try:
        store.finalize_run(ITEM, run1.id, verdict="PASS", exit_code=0,
                           command="pytest", cases=["one"], revision="r",
                           dirty=False, t10_required=True,
                           t10_binding=T10Binding(ITEM, run1.id, COMMIT, ACTOR),
                           receipt_ref=RECEIPT_REF,
                           receipt_authority=StubReceiptAuthority(
                               receipt_for(ITEM, run1.id)))
        launder = False
        launder_reason = ""
    except ReceiptError as exc:
        launder = True
        launder_reason = exc.reason_code
    check("T10 cannot relabel an unsigned terminal Run",
          launder and launder_reason == "T10_UNSIGNED_RUN",
          f"refused={launder} reason={launder_reason}")

    # ---- T10: verified positive path (test double) --------------------
    run2 = fresh_run(store, artifacts, "k2")
    verified = store.finalize_run(
        ITEM, run2.id, verdict="PASS", exit_code=0, command="pytest",
        cases=["one"], revision="r", dirty=False, t10_required=True,
        t10_binding=T10Binding(ITEM, run2.id, COMMIT, ACTOR),
        receipt_ref=RECEIPT_REF,
        receipt_authority=StubReceiptAuthority(receipt_for(ITEM, run2.id)))
    check("T10 verified path (test double) is labelled T10_VERIFIED",
          verified.get("authorization") == "T10_VERIFIED"
          and verified.get("t10_acceptable") is True, repr(verified))

    # ---- Integrity: edit finalize.json, then ask again ----------------
    path2 = Path(store.get_run(ITEM, run2.id).artifact_dir) / "finalize.json"
    body = json.loads(path2.read_text(encoding="utf-8"))
    body["runner_receipt"]["receipt_sha256"] = "f" * 64
    body["runner_receipt"]["actor_ref"] = "human:FORGED"
    path2.write_text(json.dumps(body) + "\n", encoding="utf-8")
    reread = store.finalize_run(ITEM, run2.id, verdict="PASS", exit_code=0,
                                command="pytest", cases=["one"], revision="r",
                                dirty=False)
    check("edited finalize.json is NOT trusted as T10_VERIFIED on re-read",
          reread.get("authorization") != "T10_VERIFIED", repr(reread))

    # Stronger: a run that was NEVER signed. Legacy-finalize it, then add a
    # fabricated runner_receipt to its durable file and ask again. If the label
    # becomes T10_VERIFIED, the file is trusted without the evidence sha256.
    run4 = fresh_run(store, artifacts, "k4")
    store.finalize_run(ITEM, run4.id, verdict="PASS", exit_code=0,
                       command="pytest", cases=["one"], revision="r",
                       dirty=False)
    path4 = Path(store.get_run(ITEM, run4.id).artifact_dir) / "finalize.json"
    forged = json.loads(path4.read_text(encoding="utf-8"))
    forged["authorization"] = "T10_VERIFIED"
    forged["runner_receipt"] = {
        "action_key": ACTION_KEY, "input_digest": DIGEST,
        "receipt_sha256": ANCHOR, "process_exit_code": 0,
        "runner_identity": "runner:forged", "commit_sha": COMMIT,
        "actor_ref": ACTOR,
    }
    path4.write_text(json.dumps(forged) + "\n", encoding="utf-8")
    forgery = store.finalize_run(ITEM, run4.id, verdict="PASS", exit_code=0,
                                 command="pytest", cases=["one"], revision="r",
                                 dirty=False)
    check("a forged finalize.json cannot become T10_VERIFIED",
          forgery.get("authorization") != "T10_VERIFIED", repr(forgery))

    # ---- Failure injection A: the temp write itself fails ------------
    run3 = fresh_run(store, artifacts, "k3")
    art3 = Path(store.get_run(ITEM, run3.id).artifact_dir)
    (art3 / "finalize.json.tmp").mkdir()
    try:
        store.finalize_run(ITEM, run3.id, verdict="PASS", exit_code=0,
                           command="pytest", cases=["one"], revision="r",
                           dirty=False)
        injected = "NO-ERROR"
    except Exception as exc:  # noqa: BLE001 - the probe records the type
        injected = type(exc).__name__
    check("unwritable temporary finalize path raises", injected != "NO-ERROR",
          injected)
    check("failed temp write does not conclude the Run",
          store.get_run(ITEM, run3.id).state == RunState.FINALIZING,
          str(store.get_run(ITEM, run3.id).state))
    with store.connection() as conn:
        count = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE item_id=? AND run_id=?",
            (ITEM, run3.id)).fetchone()[0]
    check("failed temp write writes no evidence", count == 0, f"evidence={count}")

    # ---- Failure injection B: the final rename fails AFTER commit -----
    run5 = fresh_run(store, artifacts, "k5")
    art5 = Path(store.get_run(ITEM, run5.id).artifact_dir)
    (art5 / "finalize.json").mkdir()
    try:
        store.finalize_run(ITEM, run5.id, verdict="PASS", exit_code=0,
                           command="pytest", cases=["one"], revision="r",
                           dirty=False)
        rename_injected = "NO-ERROR"
    except Exception as exc:  # noqa: BLE001
        rename_injected = type(exc).__name__
    check("failed final rename raises", rename_injected != "NO-ERROR",
          rename_injected)
    # The failure is AFTER the transaction commits. Record what the durable
    # state says, without asserting the safe direction we might wish for.
    run5_state = str(store.get_run(ITEM, run5.id).state)
    with store.connection() as conn:
        run5_evidence = conn.execute(
            "SELECT COUNT(*) FROM evidence WHERE item_id=? AND run_id=?",
            (ITEM, run5.id)).fetchone()[0]
    check("final-rename failure does not conclude the Run (observed)",
          run5_state == "FINALIZING",
          f"state={run5_state} evidence={run5_evidence} "
          f"(post-commit failure; see report)")

    # ---- DB failure injection ----------------------------------------
    broken = root / "broken"
    broken.mkdir()
    broken_store = ServerOSStore(broken)
    db_files = sorted(broken.glob("serveros.db*"))
    for path in db_files:
        os.chmod(path, 0o400)
    try:
        broken_store.acquire_lease("work-write:BROKEN", owner="agent:A",
                                   ttl_s=30, now=1000.0)
        db_fail = "NO-ERROR"
    except Exception as exc:  # noqa: BLE001
        db_fail = type(exc).__name__
    finally:
        for path in db_files:
            os.chmod(path, 0o600)
    check("unwritable DB does not silently grant a lease", db_fail != "NO-ERROR",
          db_fail)

failed = [name for name, ok, _ in results if not ok]
print(json.dumps({"checks": len(results), "failed": failed}, ensure_ascii=False))
sys.exit(1 if failed else 0)
