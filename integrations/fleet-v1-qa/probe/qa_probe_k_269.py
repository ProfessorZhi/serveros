#!/usr/bin/env python3
"""Independent QA Snapshot K — T10 RunnerReceiptAuthority bridge #269.

Extracts #269 into QA_TREE and WE #14 into WE_TREE (read-only). Drives the REAL
`runtime/bounded_invocation.invoke_once` with a synthetic offline runner, the
REAL `LocalRunnerReceiptAuthority.verify`, and the REAL WE #14
`ServerOSStore.finalize_run_verified`. No pytest, no author test import.

LOCAL only: synthetic run/item/context, temporary state DB and git worktree.
No provider/network/credential/login. The bridge is PREVIEW_ONLY; a positive
exit 0 is LOCAL eligibility, not production T10 acceptance.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import importlib.util
import json
import os
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
from workengine.authority import ReceiptError, VerifiedRunReceipt  # noqa: E402
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


BOUNDED = load("serveros_fleet_v1_bounded_provenance",
               TREE / "integrations/fleet-v1-verdict-evidence/bounded_provenance.py")
BRIDGE = load("qa_authority_bridge",
              TREE / "integrations/fleet-v1-verdict-evidence/authority_bridge.py")
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
    (tree / "README").write_text("authority bridge fixture\n", encoding="utf-8")
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


def runner_source(root):
    path = root / "synthetic_runner.py"
    path.write_text(RUNNER_SOURCE, encoding="utf-8")
    return path


def we_store(root, tree, head):
    store = ServerOSStore(root / "we-state")
    store.register_project("demo", "SRV")
    item = store.create_item(WorkItem(ITEM, "demo", "x", "test", "/card"))
    store.add_activity(Activity(item.id, ACTIVITY, "test"))
    store.attach_context(ExecutionContext(
        item.id, ACTIVITY, "repo", ContextMode.TEST, head, str(tree), False))
    run = store.begin_run(item.id, ACTIVITY, RUNNER, "authority-bridge-1",
                          root / "artifacts")
    for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
        run = store.advance_run(item.id, run.id, state)
    return store, item, run


def execute(root, store, tree, exit_code):
    g = grant(tree)
    cp = checkpoint(tree)
    env = envelope(cp, g)
    resolved = executor.binding(envelope=env, grant=g, checkpoint=cp,
                                worktrees_root=tree.parents[1])
    receipts = root / "receipts"
    receipts.mkdir(mode=0o700)
    os.chmod(receipts, 0o700)
    runner = runner_source(root)
    real = bounded.invoke_once

    def fake_invoke(*, key, argv, cwd, receipts, input_text, timeout,
                    readonly, max_output_bytes, env=None):
        return real(key=key,
                    argv=[sys.executable, str(runner), str(exit_code)],
                    cwd=cwd, receipts=receipts, input_text=input_text,
                    timeout=timeout, readonly=readonly,
                    max_output_bytes=max_output_bytes, env=env)

    old_invoke = executor.bounded.invoke_once
    old_settle = executor._settle
    executor.bounded.invoke_once = fake_invoke
    executor._settle = lambda config: {"worker_stopped": True,
                                       "effect": "EXECUTED_UNVERIFIED"}
    try:
        result = executor.execute(
            envelope=env, grant=g, resolved=resolved, key="verify",
            argv=["python3", "-c", "ignored"], receipts=receipts,
            state_db=store.db_path)
    finally:
        executor.bounded.invoke_once = old_invoke
        executor._settle = old_settle
    return SimpleNamespace(store=store, tree=tree, grant=g, checkpoint=cp,
                           envelope=env, result=result, runner=runner,
                           receipts=receipts, run_id=None)


def expected(fx, tree, head, exit_code):
    receipt = fx.result["receipt"]
    private = Path(fx.result["receipt_path"]).parent
    action = receipt["action_key"]
    input_text = (private / (action + ".stdin")).read_text(encoding="utf-8")
    return BOUNDED.ExpectedContinuation(
        checkpoint_ref=fx.envelope["checkpoint_ref"], action_key=action,
        work_item_id=ITEM, work_run_id=fx.run_id, commit_sha=head,
        principal=CONTEXT, grant_ref=GRANT, runner_identity=RUNNER,
        argv=(sys.executable, str(fx.runner), str(exit_code)),
        cwd=str(private), input_text=input_text, readonly=False,
        timeout=BUDGET["wall_seconds"] + 30,
        max_output_bytes=BUDGET["max_output_bytes"], env={},
        worktree=str(tree))


def authority(fx, head, tree, *, run_state="FINALIZING", anchor=True,
              **over):
    values = dict(
        checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_action_key=fx.envelope["action_key"],
        envelope_work_item=fx.envelope["work_item_ref"],
        envelope_grant_ref=fx.envelope["grant_ref"],
        envelope_delegation_ref=fx.envelope["delegation_ref"],
        envelope_checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_expires_at=fx.envelope["expires_at"],
        run_id=fx.run_id, run_state=run_state, run_worker=RUNNER,
        execution_repo="repo", execution_revision=head,
        execution_worktree=str(tree), execution_dirty=0,
        grant_ref=GRANT, grant_delegated_by=CONTEXT,
        grant_valid_from=fx.grant["valid_from"],
        grant_expires_at=fx.grant["expires_at"], grant_revoked=False,
        receipt_sha256=fx.result["receipt_sha256"] if anchor else None)
    values.update(over)
    return BOUNDED.ContinuationAuthority(**values)


def bridge(exp, auth, *, receipt_ref=None, anchor=None):
    return BRIDGE.LocalRunnerReceiptAuthority(
        expected=exp, authority=auth,
        receipt_ref=receipt_ref or ("serveros-runner-receipt:" + exp.action_key),
        expected_anchor=(auth.receipt_sha256 if anchor is None else anchor),
        receipt_factory=VerifiedRunReceipt, error_factory=ReceiptError)


def scenario(root, *, exit_code=0):
    tree, head = git_tree(root)
    store, item, run = we_store(root, tree, head)
    fx = execute(root, store, tree, exit_code)
    fx.run_id = run.id
    exp = expected(fx, tree, head, exit_code)
    auth = authority(fx, head, tree)
    return SimpleNamespace(tree=tree, head=head, store=store, item=item,
                           run=run, fx=fx, expected=exp, authority=auth,
                           bridge=bridge(exp, auth))


def refused(call, codes):
    try:
        call()
        return "ACCEPTED"
    except ReceiptError as exc:
        return exc.reason_code if exc.reason_code in codes else f"other:{exc.reason_code}"


SCRATCH.mkdir(parents=True, exist_ok=True)
base = Path(tempfile.mkdtemp(dir=SCRATCH, prefix="k-t10-"))

# K1 — exit 1 with PASS text cannot conclude success.
s1 = scenario(base / "k1", exit_code=1)
r1 = s1.bridge.verify(s1.bridge.receipt_ref, item_ref=s1.item.id,
                      run_ref=s1.run.id, now=__import__("time").time())
check("K1 receipt exit is 1", r1.process_exit_code, 1)
check("K1 verdict is FAIL", r1.verdict, "FAIL")
check("K1 PASS/exit0 finalize refused",
      refused(lambda: s1.store.finalize_run_verified(
          s1.item.id, s1.run.id, verdict="PASS", exit_code=0,
          command="pytest", cases=["one"], revision=s1.head, dirty=False,
          receipt_ref=s1.bridge.receipt_ref, receipt_authority=s1.bridge),
          {"EXIT_CODE_DISAGREEMENT", "RECEIPT_VERDICT_DISAGREEMENT"}),
      "EXIT_CODE_DISAGREEMENT")
check("K1 run stays FINALIZING",
      s1.store.get_run(s1.item.id, s1.run.id).state, RunState.FINALIZING)
with s1.store.connection() as c:
    check("K1 no evidence row",
          c.execute("SELECT COUNT(*) FROM evidence").fetchone()[0], 0)

# K2 — exit 0 is a LOCAL preview success, not production T10.
s2 = scenario(base / "k2", exit_code=0)
out = s2.store.finalize_run_verified(
    s2.item.id, s2.run.id, verdict="PASS", exit_code=0, command="pytest",
    cases=["one"], revision=s2.head, dirty=False,
    receipt_ref=s2.bridge.receipt_ref, receipt_authority=s2.bridge)
check("K2 state SUCCEEDED", out["state"], "SUCCEEDED")
check("K2 run state", s2.store.get_run(s2.item.id, s2.run.id).state,
      RunState.SUCCEEDED)
locator = Path(s2.store.get_run(s2.item.id, s2.run.id).artifact_dir) / "finalize.json"
payload = json.loads(locator.read_text(encoding="utf-8"))
check("K2 runner_receipt block written", "runner_receipt" in payload, True)
with s2.store.connection() as c:
    row = c.execute("SELECT sha256 FROM evidence WHERE item_id=? AND run_id=?",
                    (s2.item.id, s2.run.id)).fetchone()
check("K2 evidence anchors finalize.json",
      row["sha256"], hashlib.sha256(locator.read_bytes()).hexdigest())
check("K2 bridge is PREVIEW_ONLY", BRIDGE.STATUS, "PREVIEW_ONLY")
check("K2 production status BLOCKED_ORIGINAL_OWNER",
      BRIDGE.PRODUCTION_STATUS, "BLOCKED_ORIGINAL_OWNER")

# K3 — a caller path/child text is not an opaque receipt_ref.
s3 = scenario(base / "k3", exit_code=0)
child = base / "k3" / "child.txt"
child.write_text("ALL TESTS PASSED\n", encoding="utf-8")
check("K3 child path refused",
      refused(lambda: s3.bridge.verify(str(child), item_ref=s3.item.id,
                                       run_ref=s3.run.id,
                                       now=__import__("time").time()),
              {"UNTRUSTED_RECEIPT", "RUNNER_RECEIPT_REQUIRED"}),
      "UNTRUSTED_RECEIPT")

# K4 — absent parent facts are BLOCKED_TRUST_SOURCE.
check("K4 absent authority parent refused",
      refused(lambda: BRIDGE.LocalRunnerReceiptAuthority(
          expected=None, authority=None, receipt_ref="", expected_anchor=None,
          receipt_factory=None, error_factory=ReceiptError),
          {"BLOCKED_TRUST_SOURCE"}), "BLOCKED_TRUST_SOURCE")

# K5 — changed input/argv/env digest is refused.
s5 = scenario(base / "k5", exit_code=0)
changed = dataclasses.replace(s5.expected,
                              input_text=s5.expected.input_text + "x")
s5b = bridge(changed, s5.authority)
check("K5 changed stdin digest refused",
      refused(lambda: s5b.verify(s5b.receipt_ref, item_ref=s5.item.id,
                                 run_ref=s5.run.id,
                                 now=__import__("time").time()),
              {"UNTRUSTED_RECEIPT", "INPUT_DIGEST_MISMATCH"}),
      "UNTRUSTED_RECEIPT")

# K6/K7/K8/K11 — stale SHA, mismatches, tamper, revoked grant.
s6 = scenario(base / "k6", exit_code=0)
a6 = authority(s6.fx, "f" * 40, s6.tree)
b6 = bridge(s6.expected, a6)
check("K6 stale commit SHA refused",
      refused(lambda: b6.verify(b6.receipt_ref, item_ref=s6.item.id,
                                run_ref=s6.run.id,
                                now=__import__("time").time()),
              {"STALE_SHA"}), "STALE_SHA")

s7 = scenario(base / "k7", exit_code=0)
a7 = authority(s7.fx, s7.head, s7.tree, run_id="RUN-9999")
b7 = bridge(dataclasses.replace(s7.expected, work_run_id="RUN-9999"), a7)
check("K7 cross-run refused",
      refused(lambda: b7.verify(b7.receipt_ref, item_ref=s7.item.id,
                                run_ref="RUN-9999",
                                now=__import__("time").time()),
              {"RECEIPT_RUN_MISMATCH"}), "RECEIPT_RUN_MISMATCH")

s7b = scenario(base / "k7b", exit_code=0)
a7b = authority(s7b.fx, s7b.head, s7b.tree, grant_delegated_by="agent:EVIL")
b7b = bridge(s7b.expected, a7b)
check("K7 cross-principal refused",
      refused(lambda: b7b.verify(b7b.receipt_ref, item_ref=s7b.item.id,
                                 run_ref=s7b.run.id,
                                 now=__import__("time").time()),
              {"CROSS_PRINCIPAL"}), "CROSS_PRINCIPAL")

s8 = scenario(base / "k8", exit_code=0)
b8 = bridge(s8.expected, s8.authority, anchor="0" * 64)
check("K8 tampered anchor refused",
      refused(lambda: b8.verify(b8.receipt_ref, item_ref=s8.item.id,
                                run_ref=s8.run.id,
                                now=__import__("time").time()),
              {"UNTRUSTED_RECEIPT"}), "UNTRUSTED_RECEIPT")

s11 = scenario(base / "k11", exit_code=0)
a11 = authority(s11.fx, s11.head, s11.tree, grant_revoked=True)
b11 = bridge(s11.expected, a11)
check("K11 revoked grant refused",
      refused(lambda: b11.verify(b11.receipt_ref, item_ref=s11.item.id,
                                 run_ref=s11.run.id,
                                 now=__import__("time").time()),
              {"GRANT_REVOKED"}), "GRANT_REVOKED")

# K9/K13 — terminal/duplicate: no second evidence row.
s9 = scenario(base / "k9", exit_code=0)
s9.store.finalize_run_verified(
    s9.item.id, s9.run.id, verdict="PASS", exit_code=0, command="pytest",
    cases=["one"], revision=s9.head, dirty=False,
    receipt_ref=s9.bridge.receipt_ref, receipt_authority=s9.bridge)
# A second finalize on the terminal run must not add evidence.
try:
    s9.store.finalize_run_verified(
        s9.item.id, s9.run.id, verdict="PASS", exit_code=0, command="pytest",
        cases=["one"], revision=s9.head, dirty=False,
        receipt_ref=s9.bridge.receipt_ref, receipt_authority=s9.bridge)
    second = "RETURNED"
except ReceiptError as exc:
    second = exc.reason_code
with s9.store.connection() as c:
    count = c.execute("SELECT COUNT(*) FROM evidence WHERE item_id=? AND run_id=?",
                      (s9.item.id, s9.run.id)).fetchone()[0]
check("K9 duplicate finalize is fail-closed or idempotent", second in
      {"RETURNED", "UNTRUSTED_RECEIPT"}, True)
check("K9 no second evidence row", count, 1)

# K10 — a missing Work Engine Run cannot be finalized.
s10 = scenario(base / "k10", exit_code=0)
check("K10 missing run refused",
      refused(lambda: s10.store.finalize_run_verified(
          s10.item.id, "RUN-MISSING", verdict="PASS", exit_code=0,
          command="pytest", cases=["one"], revision=s10.head, dirty=False,
          receipt_ref=s10.bridge.receipt_ref, receipt_authority=s10.bridge),
          {"RECEIPT_RUN_MISMATCH"}), "RECEIPT_RUN_MISMATCH")

# ---------------------------------------------------------------------------
# SOURCE — no production upgrade, no production caller, no OS verifier.
# ---------------------------------------------------------------------------
serveros_py = [p for p in TREE.rglob("*.py") if "/tests/" not in p.as_posix()]
bridge_src = (TREE / "integrations/fleet-v1-verdict-evidence/authority_bridge.py").read_text(encoding="utf-8")
check("S1 bridge declares PREVIEW_ONLY", 'STATUS = "PREVIEW_ONLY"' in bridge_src, True)
check("S1 bridge declares BLOCKED_ORIGINAL_OWNER",
      'PRODUCTION_STATUS = "BLOCKED_ORIGINAL_OWNER"' in bridge_src, True)
check("S1 no production module imports authority_bridge",
      any("authority_bridge" in p.read_text(encoding="utf-8")
          and "fleet-v1-verdict-evidence" not in p.as_posix()
          for p in serveros_py if "tests" not in p.as_posix()), False)
check("S1 finalize_run_verified has no production caller",
      any("finalize_run_verified(" in p.read_text(encoding="utf-8")
          for p in serveros_py
          if "integrations/fleet-v1-verdict-evidence" not in p.as_posix()),
      False)
store_src = (WE / "workengine/store.py").read_text(encoding="utf-8")
check("S1 legacy finalize_run does not call finalize_run_verified",
      "finalize_run_verified" not in store_src.split("def finalize_run(")[1]
      .split("def finalize_run_verified(")[0], True)
check("S1 no SO_PEERCRED-based receipt verifier in the bridge",
      "SO_PEERCRED" in bridge_src, False)

print()
print("scratch:", base)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print("PROBE OK: every T10 bridge check held; PREVIEW_ONLY/BLOCKED_ORIGINAL_OWNER")
