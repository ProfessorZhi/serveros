#!/usr/bin/env python3
"""Independent QA Snapshot O (P0-B) — #274 strict T10 finalization consumer.

QA_TREE=<#274 f8246ce>; WE_TREE=<WE15 4bd9e8a>. A REAL offline
`bounded_invocation.invoke_once` receipt is wrapped into WE15's
`VerifiedRunReceipt` by a clearly labelled TEST DOUBLE authority; WE15's REAL
`finalize_run` writes the durable finalize.json/evidence. #274's REAL
`finalization_result` reads it back and decides.

No provider/network/credential/login. A T10_VERIFIED positive is ELIGIBLE for
QA only; production parent/call site remains BLOCKED_TRUST_SOURCE.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

TREE = Path(os.environ["QA_TREE"]).resolve()
WE = Path(os.environ["WE_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
RUNTIME = TREE / "runtime"
failures: list[str] = []
sys.path.insert(0, str(WE))

from workengine import WorkItem  # noqa: E402
from workengine.authority import (  # noqa: E402
    ReceiptError, T10Binding, VerifiedRunReceipt)
from workengine.model import (Activity, ContextMode, ExecutionContext,  # noqa: E402
                              RunState)
from workengine.store import ServerOSStore  # noqa: E402


def load(name, path):
    if name in sys.modules:
        return sys.modules[name]
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


GATE = load("qa_finalization_result",
            TREE / "integrations/fleet-v1-verdict-evidence/finalization_result.py")
continuation = load("serveros_runtime_continuation", RUNTIME / "continuation.py")
wake = load("serveros_runtime_wake", RUNTIME / "wake.py")
executor = load("serveros_bounded_executor", RUNTIME / "bounded_executor.py")
bounded = load("serveros_runtime_bounded_invocation", RUNTIME / "bounded_invocation.py")

ITEM = "SRV-0001"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
RUNNER = "claude-code"
ACTIVITY = "implement"
EPOCH = 1
BUDGET = {"wall_seconds": 45, "max_attempts": 2, "max_output_bytes": 200000}
RUNNER_SOURCE = '''#!/usr/bin/env python3
import sys
sys.stdin.read()
print("ALL TESTS PASSED - child says SUCCESS")
print("synthetic failing assertion detail", file=sys.stderr)
sys.exit(int(sys.argv[1]))
'''


def check(label, got, expected, *, security=False):
    ok = got == expected
    tag = "PASS" if ok else ("SECURITY_FAIL" if security else "FAIL")
    print(f"{tag} {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(label)


def now():
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def minutes(n):
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=n)).isoformat()


def git_tree(root):
    tree = root / "workspace" / "worktrees" / "demo" / "item"
    tree.mkdir(parents=True)
    for a in (("git", "init", "-q"),
              ("git", "config", "user.email", "t10@example.invalid"),
              ("git", "config", "user.name", "T10")):
        subprocess.run(a, cwd=tree, check=True, capture_output=True)
    (tree / "README").write_text("signed finalization fixture\n", encoding="utf-8")
    subprocess.run(("git", "add", "README"), cwd=tree, check=True, capture_output=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=tree, check=True,
                   capture_output=True)
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=tree, check=True,
                          capture_output=True, text=True).stdout.strip()
    return tree, head


def grant(tree):
    return {"delegated_by": CONTEXT, "items": [ITEM],
            "actions": ["engineering.execute"], "budget": dict(BUDGET),
            "valid_from": minutes(-1), "expires_at": minutes(60),
            "revoked": False,
            "engineering": {"schema": executor.SCHEMA, "worktree": str(tree),
                            "image": "sha256:" + "1" * 64,
                            "memory_bytes": 64 * 1024 ** 2, "cpus": 1,
                            "pids_limit": 16}}


def checkpoint(tree):
    return continuation.build_checkpoint(
        work_item=ITEM, epoch=EPOCH, parent_owner="parent-a",
        next_safe_action="run the offline engineering check",
        waiting_on={"kind": "window", "until": minutes(10)}, now=now(),
        requirement_revision="req-1", write_scope=[str(tree)])


def envelope(record, g):
    return wake.capability_envelope(
        record, g, GRANT, attempt=1, key="continuation-wake-SRV-0001-e1-a1",
        spend={"wall_seconds": BUDGET["wall_seconds"],
               "attempts_allowed": BUDGET["max_attempts"]}, now=now())


def make_store(root, head, tree):
    store = ServerOSStore(root / "we-state")
    store.register_project("demo", "SRV")
    item = store.create_item(WorkItem(ITEM, "demo", "x", "test", "/card"))
    store.add_activity(Activity(item.id, ACTIVITY, "test"))
    store.attach_context(ExecutionContext(
        item.id, ACTIVITY, "repo", ContextMode.TEST, head,
        worktree=str(tree), dirty=False))
    run = store.begin_run(item.id, ACTIVITY, RUNNER, "signed-finalize-1",
                          root / "artifacts")
    for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
        run = store.advance_run(item.id, run.id, state)
    return store, item, run


def real_receipt(root, store, tree, exit_code):
    g = grant(tree)
    cp = checkpoint(tree)
    env = envelope(cp, g)
    resolved = executor.binding(envelope=env, grant=g, checkpoint=cp,
                                worktrees_root=tree.parents[1])
    receipts = root / "receipts"
    receipts.mkdir(mode=0o700)
    os.chmod(receipts, 0o700)
    runner = root / "synthetic_runner.py"
    runner.write_text(RUNNER_SOURCE, encoding="utf-8")
    real = bounded.invoke_once

    def fake_invoke(*, key, argv, cwd, receipts, input_text, timeout,
                    readonly, max_output_bytes, env=None):
        return real(key=key, argv=[sys.executable, str(runner), str(exit_code)],
                    cwd=cwd, receipts=receipts, input_text=input_text,
                    timeout=timeout, readonly=readonly,
                    max_output_bytes=max_output_bytes, env=env)

    old_invoke = executor.bounded.invoke_once
    old_settle = executor._settle
    executor.bounded.invoke_once = fake_invoke
    executor._settle = lambda config: {"worker_stopped": True,
                                       "effect": "EXECUTED_UNVERIFIED"}
    try:
        return executor.execute(
            envelope=env, grant=g, resolved=resolved, key="verify",
            argv=["python3", "-c", "ignored"], receipts=receipts,
            state_db=store.db_path)
    finally:
        executor.bounded.invoke_once = old_invoke
        executor._settle = old_settle


class TestReceiptAuthority:
    """TEST DOUBLE around a real invoke_once receipt. NOT a production parent."""

    def __init__(self, receipt, ref="opaque:receipt:1"):
        self.receipt = receipt
        self.ref = ref
        self.calls = []

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        self.calls.append(receipt_ref)
        if receipt_ref != self.ref:
            raise ReceiptError("UNTRUSTED_RECEIPT", "not this authority's ref")
        return self.receipt


def we15_receipt(fx, item, run, head):
    receipt = fx["receipt"]
    return VerifiedRunReceipt(
        item_ref=item.id, run_ref=run.id, action_key=receipt["action_key"],
        input_digest=receipt["input_digest"],
        receipt_sha256=fx["receipt_sha256"],
        process_exit_code=receipt["process_exit_code"],
        verdict="PASS" if receipt["process_exit_code"] == 0 else "FAIL",
        runner_identity=RUNNER, commit_sha=head, actor_ref=CONTEXT)


def expected_for(item, run, head, receipt):
    return GATE.ExpectedFinalization(
        item_ref=item.id, run_ref=run.id, commit_sha=head, actor_ref=CONTEXT,
        action_key=receipt.action_key, input_digest=receipt.input_digest,
        receipt_sha256=receipt.receipt_sha256,
        process_exit_code=receipt.process_exit_code, runner_identity=RUNNER)


def rewrite_finalize(store, item, run, mutate, *, update_sha):
    loc = Path(store.get_run(item.id, run.id).artifact_dir) / "finalize.json"
    payload = json.loads(loc.read_text(encoding="utf-8"))
    mutate(payload)
    body = json.dumps(payload) + "\n"
    loc.write_text(body, encoding="utf-8")
    if update_sha:
        digest = hashlib.sha256(body.encode()).hexdigest()
        with store.connection() as c:
            c.execute("UPDATE evidence SET sha256=? WHERE item_id=? AND run_id=? "
                      "AND kind='finalize'", (digest, item.id, run.id))


SCRATCH.mkdir(parents=True, exist_ok=True)
base = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="o-274-"))
tree, head = git_tree(base / "tree")

# Legacy SUCCEEDED / UNSIGNED.
legacy_root = base / "legacy"
legacy_store, legacy_item, legacy_run = make_store(legacy_root, head, tree)
legacy_store.finalize_run(legacy_item.id, legacy_run.id, verdict="PASS",
                          exit_code=0, command="pytest", cases=["one"],
                          revision=head, dirty=False)
legacy_read = GATE.read_work_engine_finalization(
    legacy_store.db_path, legacy_item.id, legacy_run.id)
legacy_expected = GATE.ExpectedFinalization(
    item_ref=legacy_item.id, run_ref=legacy_run.id, commit_sha=head,
    actor_ref=CONTEXT, action_key="action:any", input_digest="a" * 64,
    receipt_sha256="b" * 64, process_exit_code=0, runner_identity=RUNNER)
legacy_decision = GATE.evaluate_signed_finalization(legacy_expected, legacy_read)
check("O1 legacy UNSIGNED SUCCEEDED is DENY", legacy_decision.decision,
      GATE.DECISION_DENY, security=True)
check("O1 reason names UNSIGNED", "UNSIGNED_FINALIZATION" in legacy_decision.reasons,
      True)
check("O1 legacy readback is UNSIGNED", legacy_read.authorization, "UNSIGNED")

# T10_VERIFIED from a real receipt + WE15 finalize + test-double authority.
t10_root = base / "t10"
t10_store, t10_item, t10_run = make_store(t10_root, head, tree)
fx = real_receipt(t10_root, t10_store, tree, 0)
receipt = we15_receipt(fx, t10_item, t10_run, head)
authority = TestReceiptAuthority(receipt)
t10_store.finalize_run(
    t10_item.id, t10_run.id, verdict="PASS", exit_code=0, command="pytest",
    cases=["one"], revision=head, dirty=False, t10_required=True,
    t10_binding=T10Binding(item_ref=t10_item.id, run_ref=t10_run.id,
                           commit_sha=head, actor_ref=CONTEXT),
    receipt_ref=authority.ref, receipt_authority=authority)
t10_read = GATE.read_work_engine_finalization(
    t10_store.db_path, t10_item.id, t10_run.id)
t10_expected = expected_for(t10_item, t10_run, head, receipt)
t10_decision = GATE.evaluate_signed_finalization(t10_expected, t10_read)
check("O2 T10_VERIFIED exit0 is ELIGIBLE only", t10_decision.decision,
      GATE.DECISION_ELIGIBLE)
check("O2 no parent acceptance / no QA signature",
      (t10_decision.parent_accepted, t10_decision.qa_signature), (False, False))
check("O2 production remains BLOCKED_TRUST_SOURCE",
      t10_decision.production_status, "BLOCKED_TRUST_SOURCE")

# Caller JSON is not authority; absent readback is blocked.
json_decision = GATE.evaluate_signed_finalization(
    t10_expected, {"authorization": "T10_VERIFIED", "t10_acceptable": True})
check("O3 caller JSON label is DENY", json_decision.decision,
      GATE.DECISION_DENY, security=True)
check("O3 reason CALLER_JSON_NOT_AUTHORITY",
      "CALLER_JSON_NOT_AUTHORITY" in json_decision.reasons, True)
check("O4 absent readback is BLOCKED_TRUST_SOURCE",
      GATE.evaluate_signed_finalization(t10_expected, None).decision,
      GATE.DECISION_BLOCKED)

# Missing store / missing evidence.
try:
    GATE.read_work_engine_finalization(base / "no-such.db", t10_item.id, t10_run.id)
    missing = "READ"
except GATE.FinalizationReadbackError as exc:
    missing = exc.reason_code
check("O5 missing store is BLOCKED_TRUST_SOURCE", missing, "BLOCKED_TRUST_SOURCE")

# Tampered evidence bytes (sha not updated) -> DENY.
tamper_root = base / "tamper"
tamper_store, tamper_item, tamper_run = make_store(tamper_root, head, tree)
fx_t = real_receipt(tamper_root, tamper_store, tree, 0)
tamper_receipt = we15_receipt(fx_t, tamper_item, tamper_run, head)
tamper_store.finalize_run(
    tamper_item.id, tamper_run.id, verdict="PASS", exit_code=0,
    command="pytest", cases=["one"], revision=head, dirty=False,
    t10_required=True,
    t10_binding=T10Binding(item_ref=tamper_item.id, run_ref=tamper_run.id,
                           commit_sha=head, actor_ref=CONTEXT),
    receipt_ref="opaque:t", receipt_authority=TestReceiptAuthority(
        tamper_receipt, ref="opaque:t"))
rewrite_finalize(tamper_store, tamper_item, tamper_run,
                 lambda p: p["cases"].append("injected-after-anchor"), update_sha=False)
tamper_read = GATE.read_work_engine_finalization(
    tamper_store.db_path, tamper_item.id, tamper_run.id)
tamper_decision = GATE.evaluate_signed_finalization(
    expected_for(tamper_item, tamper_run, head, tamper_receipt), tamper_read)
check("O6 tampered evidence bytes are DENY", tamper_decision.decision,
      GATE.DECISION_DENY, security=True)
check("O6 reason EVIDENCE_TAMPERED",
      "EVIDENCE_TAMPERED" in tamper_decision.reasons, True)

# Missing marker, with the evidence sha updated (a writer who rewrote both).
marker_root = base / "marker"
marker_store, marker_item, marker_run = make_store(marker_root, head, tree)
fx_m = real_receipt(marker_root, marker_store, tree, 0)
marker_receipt = we15_receipt(fx_m, marker_item, marker_run, head)
marker_store.finalize_run(
    marker_item.id, marker_run.id, verdict="PASS", exit_code=0,
    command="pytest", cases=["one"], revision=head, dirty=False,
    t10_required=True,
    t10_binding=T10Binding(item_ref=marker_item.id, run_ref=marker_run.id,
                           commit_sha=head, actor_ref=CONTEXT),
    receipt_ref="opaque:m", receipt_authority=TestReceiptAuthority(
        marker_receipt, ref="opaque:m"))
rewrite_finalize(marker_store, marker_item, marker_run,
                 lambda p: (p.pop("authorization", None),
                            p.pop("runner_receipt", None)), update_sha=True)
marker_read = GATE.read_work_engine_finalization(
    marker_store.db_path, marker_item.id, marker_run.id)
marker_decision = GATE.evaluate_signed_finalization(
    expected_for(marker_item, marker_run, head, marker_receipt), marker_read)
check("O7 missing authorization marker is DENY", marker_decision.decision,
      GATE.DECISION_DENY, security=True)
check("O7 reason AUTHORIZATION_NOT_T10_VERIFIED",
      "AUTHORIZATION_NOT_T10_VERIFIED" in marker_decision.reasons, True)

# Nonzero actual exit in the runner receipt, evidence re-anchored.
exit_root = base / "exit"
exit_store, exit_item, exit_run = make_store(exit_root, head, tree)
fx_x = real_receipt(exit_root, exit_store, tree, 0)
exit_receipt = we15_receipt(fx_x, exit_item, exit_run, head)
exit_store.finalize_run(
    exit_item.id, exit_run.id, verdict="PASS", exit_code=0, command="pytest",
    cases=["one"], revision=head, dirty=False, t10_required=True,
    t10_binding=T10Binding(item_ref=exit_item.id, run_ref=exit_run.id,
                           commit_sha=head, actor_ref=CONTEXT),
    receipt_ref="opaque:x", receipt_authority=TestReceiptAuthority(
        exit_receipt, ref="opaque:x"))
def nonzero(p):
    p["runner_receipt"]["process_exit_code"] = 1
rewrite_finalize(exit_store, exit_item, exit_run, nonzero, update_sha=True)
exit_read = GATE.read_work_engine_finalization(
    exit_store.db_path, exit_item.id, exit_run.id)
exit_decision = GATE.evaluate_signed_finalization(
    expected_for(exit_item, exit_run, head, exit_receipt), exit_read)
check("O8 nonzero actual exit is DENY", exit_decision.decision,
      GATE.DECISION_DENY, security=True)
check("O8 reason NO_VERIFIED_ZERO_EXIT",
      "NO_VERIFIED_ZERO_EXIT" in exit_decision.reasons, True)

# Cross-run / actor / commit / action / digest / anchor / runner.
for label, expected, code in (
        ("run", GATE.ExpectedFinalization(
            **{**t10_expected.__dict__, "run_ref": "RUN-9999"}),
         "RUN_MISMATCH"),
        ("actor", GATE.ExpectedFinalization(
            **{**t10_expected.__dict__, "actor_ref": "human:B"}),
         "ACTOR_MISMATCH"),
        ("commit", GATE.ExpectedFinalization(
            **{**t10_expected.__dict__, "commit_sha": "f" * 40}),
         "COMMIT_MISMATCH"),
        ("action", GATE.ExpectedFinalization(
            **{**t10_expected.__dict__, "action_key": "action:other"}),
         "ACTION_MISMATCH"),
        ("digest", GATE.ExpectedFinalization(
            **{**t10_expected.__dict__, "input_digest": "0" * 64}),
         "INPUT_DIGEST_MISMATCH"),
        ("anchor", GATE.ExpectedFinalization(
            **{**t10_expected.__dict__, "receipt_sha256": "0" * 64}),
         "RECEIPT_ANCHOR_MISMATCH"),
        ("runner", GATE.ExpectedFinalization(
            **{**t10_expected.__dict__, "runner_identity": "other"}),
         "RUNNER_MISMATCH")):
    decision = GATE.evaluate_signed_finalization(expected, t10_read)
    check(f"O9 cross-{label} is DENY", decision.decision, GATE.DECISION_DENY,
          security=True)
    check(f"O9 reason {code}", code in decision.reasons, True)

# Stale grant: this gate carries no grant/epoch input.
gate_fields = {f.name for f in __import__("dataclasses").fields(
    GATE.ExpectedFinalization)}
check("O10 the strict gate cannot see a stale grant (coverage gap)",
      [f for f in ("grant_ref", "grant_epoch", "epoch") if f in gate_fields], [])

# Historic unlabeled compatibility is only the LEGACY consumer; the strict
# gate denies it (O7). Source the legacy branch so the wiring risk is explicit.
verdict_src = (TREE / "integrations/fleet-v1-verdict-evidence/verdict_evidence.py").read_text(encoding="utf-8")
check("S1 legacy consumer has an explicit unlabeled compatibility branch",
      "authorization is None" in verdict_src, True)
check("S1 strict gate denies missing authorization",
      "AUTHORIZATION_NOT_T10_VERIFIED" in
      (TREE / "integrations/fleet-v1-verdict-evidence/finalization_result.py")
      .read_text(encoding="utf-8"), True)
gate_src = (TREE / "integrations/fleet-v1-verdict-evidence/finalization_result.py").read_text(encoding="utf-8")
serveros_py = [p for p in TREE.rglob("*.py") if "/tests/" not in p.as_posix()]
check("S2 no production module imports finalization_result",
      any("finalization_result" in p.read_text(encoding="utf-8")
          and "fleet-v1-verdict-evidence" not in p.as_posix()
          for p in serveros_py), False)
check("S2 the strict gate has no production finalize call site",
      "finalize_run(" in gate_src, False)

print()
print("scratch:", base)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: #274 strict T10 consumer checks held")
