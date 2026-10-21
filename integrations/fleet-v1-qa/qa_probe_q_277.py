#!/usr/bin/env python3
"""Independent QA Snapshot Q — #276 old vs #277 new, legacy vs V1 acceptance.

QA_MODE=old QA_TREE=<#276 1bf20fe>  -> reproduce the unlabeled legacy fail-open
QA_MODE=new QA_TREE=<#277 0ba1ef0>  -> prove legacy is isolated and V1 is strict

Real WE15 `finalize_run` durable records + a real offline `invoke_once`
receipt; the parent authority is a clearly labelled TEST DOUBLE. LOCAL only:
no provider/network/login/credential, no production daemon.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from datetime import timezone
from pathlib import Path
from types import SimpleNamespace

TREE = Path(os.environ["QA_TREE"]).resolve()
WE = Path(os.environ["WE_TREE"]).resolve()
SCRATCH = Path(os.environ["QA_SCRATCH"]).resolve()
MODE = os.environ["QA_MODE"]
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


GATE = load(f"q_gate_{MODE}",
            TREE / "integrations/fleet-v1-verdict-evidence/finalization_result.py")
LEGACY = load(f"q_legacy_{MODE}",
              TREE / "integrations/fleet-v1-verdict-evidence/verdict_evidence.py")
BP = load(f"q_bp_{MODE}",
          TREE / "integrations/fleet-v1-verdict-evidence/bounded_provenance.py")
V1 = None
if MODE == "new":
    V1 = load("q_v1_acceptance",
              TREE / "integrations/fleet-v1-verdict-evidence/v1_acceptance.py")
    # Use the strict module instance the V1 entry itself loads, so its
    # isinstance checks see objects of its own class (a real caller's shape).
    VF = V1.FINAL
executor = load(f"q_executor_{MODE}", RUNTIME / "bounded_executor.py")
bounded = load(f"q_bounded_{MODE}", RUNTIME / "bounded_invocation.py")
continuation = load(f"q_continuation_{MODE}", RUNTIME / "continuation.py")
wake = load(f"q_wake_{MODE}", RUNTIME / "wake.py")

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
    print(f"{tag} [{MODE}] {label}: got={got!r} expected={expected!r}")
    if not ok:
        failures.append(f"[{MODE}] {label}")


def now_iso(offset=0.0):
    return (datetime.datetime.now(timezone.utc)
            + datetime.timedelta(seconds=offset)).isoformat()


def minutes(n):
    return (datetime.datetime.now(timezone.utc)
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


def control_grant(tree):
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
        waiting_on={"kind": "window", "until": minutes(10)}, now=now_iso(),
        requirement_revision="req-1", write_scope=[str(tree)])


def envelope(record, g):
    return wake.capability_envelope(
        record, g, GRANT, attempt=1, key="continuation-wake-SRV-0001-e1-a1",
        spend={"wall_seconds": BUDGET["wall_seconds"],
               "attempts_allowed": BUDGET["max_attempts"]}, now=now_iso())


def make_store(root, head, tree):
    st = ServerOSStore(root / "we-state")
    st.register_project("demo", "SRV")
    item = st.create_item(WorkItem(ITEM, "demo", "x", "test", "/card"))
    st.add_activity(Activity(item.id, ACTIVITY, "test"))
    st.attach_context(ExecutionContext(
        item.id, ACTIVITY, "repo", ContextMode.TEST, head,
        worktree=str(tree), dirty=False))
    run = st.begin_run(item.id, ACTIVITY, RUNNER, "signed-finalize-1",
                       root / "artifacts")
    for s in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING):
        run = st.advance_run(item.id, run.id, s)
    return st, item, run


def real_receipt(root, st, tree, exit_code):
    g = control_grant(tree)
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
            state_db=st.db_path)
    finally:
        executor.bounded.invoke_once = old_invoke
        executor._settle = old_settle


class TestReceiptAuthority:
    """TEST DOUBLE around a real invoke_once receipt. NOT a production parent."""

    def __init__(self, receipt, ref="opaque:receipt:1"):
        self.receipt = receipt
        self.ref = ref

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        if receipt_ref != self.ref:
            raise ReceiptError("UNTRUSTED_RECEIPT", "not this authority's ref")
        return self.receipt


def we15_receipt(fx, item, run, head):
    r = fx["receipt"]
    return VerifiedRunReceipt(
        item_ref=item.id, run_ref=run.id, action_key=r["action_key"],
        input_digest=r["input_digest"], receipt_sha256=fx["receipt_sha256"],
        process_exit_code=r["process_exit_code"],
        verdict="PASS" if r["process_exit_code"] == 0 else "FAIL",
        runner_identity=RUNNER, commit_sha=head, actor_ref=CONTEXT)


def expected_for(item, run, head, receipt):
    return GATE.ExpectedFinalization(
        item_ref=item.id, run_ref=run.id, commit_sha=head, actor_ref=CONTEXT,
        action_key=receipt.action_key, input_digest=receipt.input_digest,
        receipt_sha256=receipt.receipt_sha256,
        process_exit_code=receipt.process_exit_code, runner_identity=RUNNER)


def rewrite_finalize(st, item, run, mutate, *, update_sha):
    loc = Path(st.get_run(item.id, run.id).artifact_dir) / "finalize.json"
    payload = json.loads(loc.read_text(encoding="utf-8"))
    mutate(payload)
    body = json.dumps(payload) + "\n"
    loc.write_text(body, encoding="utf-8")
    if update_sha:
        digest = hashlib.sha256(body.encode()).hexdigest()
        with st.connection() as c:
            c.execute("UPDATE evidence SET sha256=? WHERE item_id=? AND run_id=? "
                      "AND kind='finalize'", (digest, item.id, run.id))


def finalize_t10(st, item, run, head, receipt, ref):
    st.finalize_run(
        item.id, run.id, verdict="PASS", exit_code=0, command="pytest",
        cases=["one"], revision=head, dirty=False, t10_required=True,
        t10_binding=T10Binding(item_ref=item.id, run_ref=run.id,
                               commit_sha=head, actor_ref=CONTEXT),
        receipt_ref=ref, receipt_authority=TestReceiptAuthority(receipt, ref=ref))


def legacy_snapshot(st, item, run):
    row = dict(st.connection().execute(
        "SELECT * FROM runs WHERE item_id=? AND run_id=?",
        (item.id, run.id)).fetchone())
    erow = dict(st.connection().execute(
        "SELECT * FROM evidence WHERE item_id=? AND run_id=? AND kind='finalize'",
        (item.id, run.id)).fetchone())
    loc = Path(erow["locator"])
    return BP_or_legacy_snapshot(row, erow, loc)


def BP_or_legacy_snapshot(row, erow, loc):
    return LEGACY.AuthoritySnapshot(
        run=row, finalize_evidence=(erow,),
        artifacts={erow["locator"]: loc.read_bytes()})


def legacy_expected(item, run, head):
    return LEGACY.ExpectedAcceptance(
        item_id=item.id, run_id=run.id, revision=head, principal=CONTEXT,
        command="pytest", recognized_runners={CONTEXT: (RUNNER,)})


SCRATCH.mkdir(parents=True, exist_ok=True)
base = Path(tempfile.mkdtemp(dir=SCRATCH, prefix=f"q-{MODE}-"))
tree, head = git_tree(base / "tree")

# A durable legacy SUCCEEDED/UNSIGNED record turned into a pre-#15 unlabeled
# record by deleting the marker and re-anchoring the evidence digest.
legacy_root = base / "legacy"
legacy_store, legacy_item, legacy_run = make_store(legacy_root, head, tree)
legacy_store.finalize_run(legacy_item.id, legacy_run.id, verdict="PASS",
                          exit_code=0, command="pytest", cases=["one"],
                          revision=head, dirty=False)
snap_unsigned = legacy_snapshot(legacy_store, legacy_item, legacy_run)

rewrite_finalize(legacy_store, legacy_item, legacy_run,
                 lambda p: p.pop("authorization", None), update_sha=True)
snap_unlabeled = legacy_snapshot(legacy_store, legacy_item, legacy_run)
unlabeled_read = GATE.read_work_engine_finalization(
    legacy_store.db_path, legacy_item.id, legacy_run.id)
unlabeled_expected = GATE.ExpectedFinalization(
    item_ref=legacy_item.id, run_ref=legacy_run.id, commit_sha=head,
    actor_ref=CONTEXT, action_key="action:any", input_digest="a" * 64,
    receipt_sha256="b" * 64, process_exit_code=0, runner_identity=RUNNER)

# A second store keeps a labelled UNSIGNED record untouched.
unsigned_root = base / "unsigned"
unsigned_store, unsigned_item, unsigned_run = make_store(unsigned_root, head, tree)
unsigned_store.finalize_run(unsigned_item.id, unsigned_run.id, verdict="PASS",
                            exit_code=0, command="pytest", cases=["one"],
                            revision=head, dirty=False)
unsigned_read = GATE.read_work_engine_finalization(
    unsigned_store.db_path, unsigned_item.id, unsigned_run.id)
unsigned_expected = GATE.ExpectedFinalization(
    item_ref=unsigned_item.id, run_ref=unsigned_run.id, commit_sha=head,
    actor_ref=CONTEXT, action_key="action:any", input_digest="a" * 64,
    receipt_sha256="b" * 64, process_exit_code=0, runner_identity=RUNNER)

# ---------------------------------------------------------------------------
# R — legacy on the unlabeled record: red in #276, isolated in #277.
# ---------------------------------------------------------------------------
accept = LEGACY.evaluate_acceptance(
    legacy_expected(legacy_item, legacy_run, head), snap_unlabeled)
check("R legacy decision on pre-#15 unlabeled", accept.decision,
      LEGACY.DECISION_ELIGIBLE)
check("R legacy carries no V1 label reason",
      [r for r in accept.reasons if r.startswith(("UNSIGNED", "AUTHORIZATION",
                                                  "T10_"))], [])

if MODE == "old":
    check("R old legacy has no v1_acceptable field",
          hasattr(accept, "v1_acceptable"), False)
    try:
        LEGACY.evaluate_acceptance(
            legacy_expected(legacy_item, legacy_run, head), snap_unlabeled,
            v1_required=True)
        refused = "ACCEPTED"
    except TypeError as exc:
        refused = "UNSUPPORTED" if "v1_required" in str(exc) else f"OTHER:{exc}"
    check("R old legacy cannot refuse a v1_required call", refused,
          "UNSUPPORTED")
    check("R old tree has no v1_acceptance module",
          (TREE / "integrations/fleet-v1-verdict-evidence/v1_acceptance.py").exists(),
          False)
    check("R old DispatchDecision has no v1_acceptable field",
          "v1_acceptable" in BP.DispatchDecision.__dataclass_fields__, False)
    reasons = BP._Reasons()
    BP._check_work(SimpleNamespace(
        work_item_id=legacy_item.id, work_run_id=legacy_run.id,
        commit_sha=head, principal=CONTEXT, finalize_command="pytest",
        runner_identity=RUNNER), snap_unlabeled, {}, reasons)
    check("R old bounded_provenance leaves the unlabeled record eligible",
          [c for c in reasons.codes if c.startswith("WORK_")], [])
    print("OLD_RED_CONFIRMED: a durable unlabeled record is legacy-ELIGIBLE "
          "and no v1_acceptable field exists")
else:
    check("R new legacy decision is still ELIGIBLE", accept.decision,
          LEGACY.DECISION_ELIGIBLE)
    check("R new legacy marks v1_acceptable false", accept.v1_acceptable, False,
          security=True)
    check("R new legacy mode is LEGACY_COMPATIBILITY", accept.v1_mode,
          "LEGACY_COMPATIBILITY")
    refused = LEGACY.evaluate_acceptance(
        legacy_expected(legacy_item, legacy_run, head), snap_unlabeled,
        v1_required=True)
    check("R new v1_required is DENY", refused.decision, LEGACY.DECISION_DENY,
          security=True)
    check("R new v1_required reason V1_REQUIRES_STRICT_PATH",
          "V1_REQUIRES_STRICT_PATH" in refused.reasons, True)
    check("R new v1_required v1_acceptable false", refused.v1_acceptable, False)
    check("R new labelled UNSIGNED is DENY",
          LEGACY.evaluate_acceptance(
              legacy_expected(legacy_item, legacy_run, head),
              snap_unsigned).decision, LEGACY.DECISION_DENY)
    reasons = BP._Reasons()
    BP._check_work(SimpleNamespace(
        work_item_id=legacy_item.id, work_run_id=legacy_run.id,
        commit_sha=head, principal=CONTEXT, finalize_command="pytest",
        runner_identity=RUNNER), snap_unlabeled, {}, reasons)
    check("R new bounded_provenance still calls the legacy seam (no WORK_ denial)",
          [c for c in reasons.codes if c.startswith("WORK_")], [])
    check("R new DispatchDecision defaults v1_acceptable false",
          BP.DispatchDecision.__dataclass_fields__["v1_acceptable"].default,
          False, security=True)
    check("R new DispatchDecision defaults v1_mode LEGACY_COMPATIBILITY",
          BP.DispatchDecision.__dataclass_fields__["v1_mode"].default,
          "LEGACY_COMPATIBILITY")
    check("R new tree has the V1 acceptance module",
          (TREE / "integrations/fleet-v1-verdict-evidence/v1_acceptance.py").exists(),
          True)

# ---------------------------------------------------------------------------
# T — real WE15 T10 durable records for the V1 matrix.
# ---------------------------------------------------------------------------
t10_root = base / "t10"
t10_store, t10_item, t10_run = make_store(t10_root, head, tree)
fx = real_receipt(t10_root, t10_store, tree, 0)
receipt = we15_receipt(fx, t10_item, t10_run, head)
finalize_t10(t10_store, t10_item, t10_run, head, receipt, "opaque:t10")
t10_read = GATE.read_work_engine_finalization(
    t10_store.db_path, t10_item.id, t10_run.id)
t10_expected = expected_for(t10_item, t10_run, head, receipt)
check("T real T10_VERIFIED record reads back", t10_read.authorization,
      "T10_VERIFIED")
check("T strict gate alone is ELIGIBLE", GATE.evaluate_signed_finalization(
      t10_expected, t10_read).decision, GATE.DECISION_ELIGIBLE)

# Fake child: real receipt exit 1, caller demands PASS/0.
fake_root = base / "fake"
fake_store, fake_item, fake_run = make_store(fake_root, head, tree)
fx1 = real_receipt(fake_root, fake_store, tree, 1)
fake_receipt = we15_receipt(fx1, fake_item, fake_run, head)
try:
    finalize_t10(fake_store, fake_item, fake_run, head, fake_receipt,
                 "opaque:fake")
    fake = "ACCEPTED"
except ReceiptError as exc:
    fake = exc.reason_code
check("T fake child PASS/actual exit1 is refused", fake in {
      "INVALID_RECEIPT", "RECEIPT_INCONSISTENT", "EXIT_CODE_DISAGREEMENT"}, True,
      security=True)
check("T fake child Run stays FINALIZING",
      fake_store.get_run(fake_item.id, fake_run.id).state, RunState.FINALIZING)
check("T fake child writes no evidence", fake_store.connection().execute(
      "SELECT COUNT(*) c FROM evidence WHERE item_id=? AND run_id=?",
      (fake_item.id, fake_run.id)).fetchone()["c"], 0)

# Tampered evidence and a durable receipt whose actual exit is 1.
tamper_root = base / "tamper"
tamper_store, tamper_item, tamper_run = make_store(tamper_root, head, tree)
fx_t = real_receipt(tamper_root, tamper_store, tree, 0)
tamper_receipt = we15_receipt(fx_t, tamper_item, tamper_run, head)
finalize_t10(tamper_store, tamper_item, tamper_run, head, tamper_receipt,
             "opaque:tamper")
rewrite_finalize(tamper_store, tamper_item, tamper_run,
                 lambda p: p["cases"].append("injected"), update_sha=False)
tamper_read = GATE.read_work_engine_finalization(
    tamper_store.db_path, tamper_item.id, tamper_run.id)

exit_root = base / "exit"
exit_store, exit_item, exit_run = make_store(exit_root, head, tree)
fx_e = real_receipt(exit_root, exit_store, tree, 0)
exit_receipt = we15_receipt(fx_e, exit_item, exit_run, head)
finalize_t10(exit_store, exit_item, exit_run, head, exit_receipt, "opaque:exit")
rewrite_finalize(exit_store, exit_item, exit_run,
                 lambda p: p["runner_receipt"].__setitem__(
                     "process_exit_code", 1), update_sha=True)
exit_read = GATE.read_work_engine_finalization(
    exit_store.db_path, exit_item.id, exit_run.id)

if MODE == "old":
    check("T old strict gate refuses the unlabeled record",
          GATE.evaluate_signed_finalization(
              unlabeled_expected, unlabeled_read).decision, GATE.DECISION_DENY)
    print("OLD_RED_CONFIRMED: strict exists but legacy is the only V1-shaped path")
else:
    # -----------------------------------------------------------------------
    # V — new V1 acceptance matrix.
    # -----------------------------------------------------------------------
    def vexp(item, run, hd, rcpt):
        return VF.ExpectedFinalization(
            item_ref=item.id, run_ref=run.id, commit_sha=hd,
            actor_ref=CONTEXT, action_key=rcpt.action_key,
            input_digest=rcpt.input_digest,
            receipt_sha256=rcpt.receipt_sha256,
            process_exit_code=rcpt.process_exit_code,
            runner_identity=RUNNER)

    t10_read_v1 = VF.read_work_engine_finalization(
        t10_store.db_path, t10_item.id, t10_run.id)
    t10_expected_v1 = vexp(t10_item, t10_run, head, receipt)
    unlabeled_read_v1 = VF.read_work_engine_finalization(
        legacy_store.db_path, legacy_item.id, legacy_run.id)
    unlabeled_expected_v1 = VF.ExpectedFinalization(
        item_ref=legacy_item.id, run_ref=legacy_run.id, commit_sha=head,
        actor_ref=CONTEXT, action_key="action:any", input_digest="a" * 64,
        receipt_sha256="b" * 64, process_exit_code=0, runner_identity=RUNNER)
    unsigned_read_v1 = VF.read_work_engine_finalization(
        unsigned_store.db_path, unsigned_item.id, unsigned_run.id)
    tamper_read_v1 = VF.read_work_engine_finalization(
        tamper_store.db_path, tamper_item.id, tamper_run.id)
    exit_read_v1 = VF.read_work_engine_finalization(
        exit_store.db_path, exit_item.id, exit_run.id)

    def gsnap(**over):
        values = dict(
            grant_ref="grant-1", principal=CONTEXT, verified=True, fresh=True,
            revoked=False, epoch=1, verified_by="test-double:qa",
            checked_at=now_iso())
        values.update(over)
        return V1.GrantFreshnessSnapshot(**values)

    def v1(expected=t10_expected_v1, readback=t10_read_v1, grant=None, **kwargs):
        params = dict(grant=grant, expected_grant_ref="grant-1")
        params.update(kwargs)
        return V1.evaluate_v1_acceptance(expected, readback, **params)

    def no_accept(decision):
        return (decision.v1_acceptable is False
                and decision.parent_accepted is False
                and decision.qa_signature is False)

    d = v1(grant=gsnap(), v1_required=False)
    check("V v1_required=False is BLOCKED_WIRING", d.decision,
          V1.DECISION_BLOCKED_WIRING, security=True)
    check("V v1_required=False reason V1_CONTEXT_REQUIRED",
          V1.V1_CONTEXT_REQUIRED in d.reasons, True)
    check("V v1_required=False no acceptance", no_accept(d), True)

    d = v1(grant=gsnap(), expected_grant_ref="")
    check("V empty expected_grant_ref is BLOCKED_WIRING", d.decision,
          V1.DECISION_BLOCKED_WIRING)
    check("V empty grant ref reason", V1.GRANT_REF_REQUIRED in d.reasons, True)

    d = v1(grant=None)
    check("V missing grant snapshot is BLOCKED_WIRING", d.decision,
          V1.DECISION_BLOCKED_WIRING, security=True)
    check("V missing parent reason",
          V1.NO_TRUSTED_PARENT_FOR_GRANT_EPOCH in d.reasons, True)
    check("V missing parent no acceptance", no_accept(d), True)

    d = v1(grant={"verified": True, "fresh": True, "revoked": False,
                  "epoch": 1, "verified_by": "trusted-parent:forged"})
    check("V caller JSON grant is DENY", d.decision, V1.DECISION_DENY,
          security=True)
    check("V caller JSON reason CALLER_JSON_NOT_AUTHORITY",
          V1.CALLER_JSON_NOT_AUTHORITY in d.reasons, True)

    for label, over, decision, reason in (
            ("stale", dict(fresh=False), V1.DECISION_BLOCKED_WIRING,
             V1.STALE_GRANT_UNKNOWN),
            ("revoked", dict(revoked=True), V1.DECISION_DENY,
             V1.GRANT_REVOKED),
            ("epoch-unknown", dict(epoch=None), V1.DECISION_BLOCKED_WIRING,
             V1.GRANT_EPOCH_UNKNOWN),
            ("not-verified", dict(verified=False), V1.DECISION_BLOCKED_WIRING,
             V1.GRANT_FRESHNESS_UNKNOWN),
            ("wrong-grant", dict(grant_ref="grant-2"), V1.DECISION_DENY,
             V1.GRANT_MISMATCH),
            ("wrong-principal", dict(principal="human:B"), V1.DECISION_DENY,
             V1.ACTOR_MISMATCH),
            ("trusted-parent-marker",
             dict(verified_by="trusted-parent:owner-session"),
             V1.DECISION_BLOCKED_WIRING, V1.V1_ACCEPTANCE_NOT_WIRED),
            ("unrecognized-source", dict(verified_by="root:forged"),
             V1.DECISION_BLOCKED_WIRING, V1.UNRECOGNIZED_TRUST_SOURCE)):
        d = v1(grant=gsnap(**over))
        check(f"V {label} is {decision}", d.decision, decision, security=True)
        check(f"V {label} reason {reason}", reason in d.reasons, True)
        check(f"V {label} no acceptance", no_accept(d), True)

    d = v1(grant=gsnap())
    check("V test-double exit0 is ELIGIBLE_FOR_QA", d.decision,
          V1.DECISION_ELIGIBLE_FOR_QA)
    check("V test-double v1_acceptable false", d.v1_acceptable, False,
          security=True)
    check("V test-double eligible_for_qa_review true",
          d.eligible_for_qa_review, True)
    check("V test-double parent/QA false",
          (d.parent_accepted, d.qa_signature), (False, False))
    check("V test-double production BLOCKED_TRUST_SOURCE",
          d.production_status, "BLOCKED_TRUST_SOURCE")
    check("V test-double trusted_parent is TEST_DOUBLE", d.trusted_parent,
          "TEST_DOUBLE")

    # Strict-gate negative readbacks must not become positive V1 acceptance.
    for label, readback, expected, reason in (
            ("legacy-unsigned", unsigned_read_v1,
             vexp(unsigned_item, unsigned_run, head, receipt),
             "UNSIGNED_FINALIZATION"),
            ("pre-15-unlabeled", unlabeled_read_v1, unlabeled_expected_v1,
             "AUTHORIZATION_NOT_T10_VERIFIED"),
            ("tampered", tamper_read_v1,
             vexp(tamper_item, tamper_run, head, tamper_receipt),
             "EVIDENCE_TAMPERED"),
            ("actual-exit-1", exit_read_v1,
             vexp(exit_item, exit_run, head, exit_receipt),
             "NO_VERIFIED_ZERO_EXIT")):
        d = V1.evaluate_v1_acceptance(
            expected, readback, grant=gsnap(), expected_grant_ref="grant-1")
        check(f"V {label} is DENY", d.decision, V1.DECISION_DENY,
              security=True)
        check(f"V {label} reason {reason}", reason in d.reasons, True)
        check(f"V {label} no acceptance", no_accept(d), True)

    for label, over, reason in (
            ("item", dict(item_ref="SRV-9999"), "ITEM_MISMATCH"),
            ("run", dict(run_ref="RUN-9999"), "RUN_MISMATCH"),
            ("commit", dict(commit_sha="f" * 40), "COMMIT_MISMATCH"),
            ("actor", dict(actor_ref="human:B"), "ACTOR_MISMATCH")):
        expected = VF.ExpectedFinalization(
            **{**t10_expected_v1.__dict__, **over})
        d = V1.evaluate_v1_acceptance(
            expected, t10_read_v1, grant=gsnap(), expected_grant_ref="grant-1")
        check(f"V wrong {label} is DENY", d.decision, V1.DECISION_DENY,
              security=True)
        check(f"V wrong {label} reason {reason}", reason in d.reasons, True)

    d = V1.evaluate_v1_acceptance(
        t10_expected_v1, {"authorization": "T10_VERIFIED", "t10_acceptable": True},
        grant=gsnap(), expected_grant_ref="grant-1")
    check("V caller JSON readback is DENY", d.decision, V1.DECISION_DENY,
          security=True)
    check("V caller JSON readback reason",
          "CALLER_JSON_NOT_AUTHORITY" in d.reasons, True)

    vsrc = (TREE / "integrations/fleet-v1-verdict-evidence/v1_acceptance.py").read_text(encoding="utf-8")
    check("S v1_acceptable is never set true in the V1 module",
          "v1_acceptable=True" in vsrc, False, security=True)
    check("S V1 decision hard-codes parent_accepted=False",
          "parent_accepted=False" in vsrc, True)
    check("S V1 decision hard-codes qa_signature=False",
          "qa_signature=False" in vsrc, True)
    check("S legacy V1_REQUIRES_STRICT_PATH is a deny reason",
          "V1_REQUIRES_STRICT_PATH" in
          (TREE / "integrations/fleet-v1-verdict-evidence/verdict_evidence.py")
          .read_text(encoding="utf-8").split("_DENY_REASONS")[1].split("})")[0],
          True)
    prod_py = [p for p in TREE.rglob("*.py")
               if "/tests/" not in p.as_posix()
               and "fleet-v1-verdict-evidence" not in p.as_posix()]
    for module in ("v1_acceptance", "verdict_evidence", "finalization_result",
                   "bounded_provenance"):
        check(f"S no production module imports {module}",
              any(module in p.read_text(encoding="utf-8") for p in prod_py),
              False)
    print("NEW_GREEN_CONFIRMED: legacy isolated; V1 strict; no production import")

print()
print("scratch:", base)
if failures:
    print(f"PROBE FAILED: {len(failures)} check(s): {failures}")
    sys.exit(1)
print(f"PROBE OK [{MODE}]")
