#!/usr/bin/env python3
"""#237 T10 P0 — a downstream consumer must read the signed finalization.

THE DEFECT THIS FILE PINS
-------------------------
A careless consumer can read Work Engine `runs.state == SUCCEEDED` and accept a
Run. Work Engine #15 now labels a legacy finalize `authorization: "UNSIGNED"` /
`t10_acceptable: false`; a T10-verified finalize is `T10_VERIFIED` / `true`.
Only the latter may pass a V1/T10 consumer gate. The label must come from the
durable finalize evidence and the runner receipt, not from a caller's JSON.

WHAT IS REAL HERE
-----------------
* The exact Work Engine #15 source (`4bd9e8a`) is extracted read-only with
  `git archive`; its real `ServerOSStore` writes the evidence and the
  `finalize.json`.
* The real `runtime/bounded_invocation.invoke_once` produces the receipt through
  the #269 executor (the Docker worker argv is replaced by a real offline
  synthetic runner in this CPU-only test).
* The #269 PREVIEW bridge verifies that receipt; a clearly labelled #15
  test-double wraps its result into the #15 `VerifiedRunReceipt` (which adds
  `actor_ref`).
* The new `finalization_result.py` reads `serveros.db` / the evidence file
  read-only and the gate refuses a legacy `SUCCEEDED`/`UNSIGNED` result, a
  caller JSON label, tampering, missing `t10_acceptable`, inconsistent
  Item/Run/commit/actor, weaker `authorization`, and a non-zero process exit.

WHAT IS NOT CLAIMED
-------------------
The production `RunnerReceiptAuthority` / OS-attested parent is still unbound.
The positive case is `ELIGIBLE` for independent QA only, with
`production_status=BLOCKED_TRUST_SOURCE`; it is LOCAL/HERMETIC, not production.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RUNTIME = REPO / "runtime"
WE_REPO = Path("/data/home/vince/workspace/src/work-engine")
WE_HEAD = "4bd9e8a1c6a6a1b24c1717ec65862c8900d5f7be"
SIGNED_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "finalization_result.py")
BRIDGE_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "authority_bridge.py")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    parent = str(path.parent)
    if parent not in sys.path:
        sys.path.insert(0, parent)
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


executor = _load("serveros_bounded_executor", RUNTIME / "bounded_executor.py")
bounded = _load("serveros_runtime_bounded_invocation",
                RUNTIME / "bounded_invocation.py")
continuation = _load("serveros_runtime_continuation",
                     RUNTIME / "continuation.py")
wake = _load("serveros_runtime_wake", RUNTIME / "wake.py")
BOUNDED = _load(
    "serveros_fleet_v1_bounded_provenance",
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "bounded_provenance.py")
VERDICT = _load(
    "serveros_fleet_v1_verdict_evidence",
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "verdict_evidence.py")
BRIDGE = _load("serveros_fleet_v1_authority_bridge", BRIDGE_PATH)

ITEM = "SRV-0001"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
RUNNER = "claude-code"
ACTIVITY = "implement"
EPOCH = 1
BUDGET = {"wall_seconds": 45, "max_attempts": 2, "max_output_bytes": 200000}

RUNNER_SOURCE = '''#!/usr/bin/env python3
"""Real offline runner: prints the child's PASS text, exits as told."""
import sys
sys.stdin.read()
print("ALL TESTS PASSED - child says SUCCESS")
print("synthetic failing assertion detail", file=sys.stderr)
sys.exit(int(sys.argv[1]))
'''


@pytest.fixture(scope="session")
def signed():
    return _load("serveros_fleet_v1_signed_finalization", SIGNED_PATH)


@pytest.fixture(scope="session")
def we15(tmp_path_factory):
    dest = tmp_path_factory.mktemp("we15")
    src = dest / "src"
    src.mkdir()
    archive = dest / "we15.tar"
    with archive.open("wb") as fh:
        subprocess.run(
            ["git", "-C", str(WE_REPO), "archive", WE_HEAD],
            stdout=fh, check=True, stderr=subprocess.PIPE)
    with tarfile.open(archive) as tar:
        tar.extractall(src, filter="data")
    sys.path.insert(0, str(src))
    for name in [name for name in sys.modules
                 if name == "workengine" or name.startswith("workengine.")]:
        del sys.modules[name]
    import workengine  # noqa: F401  (the archive on sys.path is the pin)
    from workengine import WorkItem
    from workengine.authority import ReceiptError, T10Binding, VerifiedRunReceipt
    from workengine.model import Activity, ContextMode, ExecutionContext, RunState
    from workengine.store import ServerOSStore
    return SimpleNamespace(
        WorkItem=WorkItem,
        Activity=Activity,
        ContextMode=ContextMode,
        ExecutionContext=ExecutionContext,
        RunState=RunState,
        ServerOSStore=ServerOSStore,
        VerifiedRunReceipt=VerifiedRunReceipt,
        ReceiptError=ReceiptError,
        T10Binding=T10Binding,
    )


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _minutes(n: float) -> str:
    return (datetime.datetime.now(datetime.timezone.utc)
            + datetime.timedelta(minutes=n)).isoformat()


def _git_tree(tmp_path: Path):
    tree = tmp_path / "workspace" / "worktrees" / "demo" / "item"
    tree.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=tree, check=True,
                   capture_output=True)
    subprocess.run(["git", "config", "user.email", "t10@example.invalid"],
                   cwd=tree, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "T10"], cwd=tree,
                   check=True, capture_output=True)
    (tree / "README").write_text("signed finalization fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=tree, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tree, check=True,
                   capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree, check=True,
                          capture_output=True, text=True).stdout.strip()
    return tree, head


def _grant(tree: Path) -> dict:
    return {
        "delegated_by": CONTEXT,
        "items": [ITEM],
        "actions": ["engineering.execute"],
        "budget": dict(BUDGET),
        "valid_from": _minutes(-1),
        "expires_at": _minutes(60),
        "revoked": False,
        "engineering": {
            "schema": executor.SCHEMA,
            "worktree": str(tree),
            "image": "sha256:" + "1" * 64,
            "memory_bytes": 64 * 1024 ** 2,
            "cpus": 1,
            "pids_limit": 16,
        },
    }


def _checkpoint(tree: Path) -> dict:
    return continuation.build_checkpoint(
        work_item=ITEM, epoch=EPOCH, parent_owner="parent-a",
        next_safe_action="run the offline engineering check",
        waiting_on={"kind": "window", "until": _minutes(10)},
        now=_now(), requirement_revision="req-1", write_scope=[str(tree)])


def _envelope(record: dict, grant: dict) -> dict:
    return wake.capability_envelope(
        record, grant, GRANT, attempt=1,
        key="continuation-wake-SRV-0001-e1-a1",
        spend={"wall_seconds": BUDGET["wall_seconds"],
               "attempts_allowed": BUDGET["max_attempts"]},
        now=_now())


def _runner(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_runner.py"
    path.write_text(RUNNER_SOURCE, encoding="utf-8")
    return path


def _we_store(we15, tmp_path: Path, tree: Path, head: str):
    store = we15.ServerOSStore(tmp_path / "we-state")
    store.register_project("demo", "SRV")
    item = store.create_item(
        we15.WorkItem(ITEM, "demo", "x", "test", "/card"))
    store.add_activity(we15.Activity(item.id, ACTIVITY, "test"))
    store.attach_context(we15.ExecutionContext(
        item.id, ACTIVITY, "repo", we15.ContextMode.TEST, head,
        str(tree), False))
    run = store.begin_run(
        item.id, ACTIVITY, RUNNER, "signed-finalization-1",
        tmp_path / "artifacts")
    for state in (we15.RunState.PREPARING, we15.RunState.RUNNING,
                  we15.RunState.FINALIZING):
        run = store.advance_run(item.id, run.id, state)
    return store, item, run


def _execute(monkeypatch, tmp_path, store, tree, *, exit_code):
    grant = _grant(tree)
    checkpoint = _checkpoint(tree)
    envelope = _envelope(checkpoint, grant)
    resolved = executor.binding(
        envelope=envelope, grant=grant, checkpoint=checkpoint,
        worktrees_root=tree.parents[1])
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    runner = _runner(tmp_path)
    real = bounded.invoke_once

    def fake_invoke(*, key, argv, cwd, receipts, input_text, timeout,
                    readonly, max_output_bytes, env=None):
        return real(
            key=key,
            argv=[sys.executable, str(runner), str(exit_code)],
            cwd=cwd, receipts=receipts, input_text=input_text,
            timeout=timeout, readonly=readonly,
            max_output_bytes=max_output_bytes, env=env)

    monkeypatch.setattr(executor.bounded, "invoke_once", fake_invoke)
    monkeypatch.setattr(
        executor, "_settle",
        lambda config: {"worker_stopped": True, "effect": "EXECUTED_UNVERIFIED"})
    result = executor.execute(
        envelope=envelope, grant=grant, resolved=resolved,
        key="verify", argv=["python3", "-c", "ignored"],
        receipts=receipts, state_db=store.db_path)
    return SimpleNamespace(
        store=store, tree=tree, grant=grant, checkpoint=checkpoint,
        envelope=envelope, result=result, runner=runner, receipts=receipts)


def _expected_continuation(fx, run_id, head, *, exit_code=0):
    receipt = fx.result["receipt"]
    private = Path(fx.result["receipt_path"]).parent
    action = receipt["action_key"]
    input_text = (private / (action + ".stdin")).read_text(encoding="utf-8")
    return BOUNDED.ExpectedContinuation(
        checkpoint_ref=fx.envelope["checkpoint_ref"],
        action_key=action,
        work_item_id=ITEM,
        work_run_id=run_id,
        commit_sha=head,
        principal=CONTEXT,
        grant_ref=GRANT,
        runner_identity=RUNNER,
        argv=(sys.executable, str(fx.runner), str(exit_code)),
        cwd=str(private),
        input_text=input_text,
        readonly=False,
        timeout=BUDGET["wall_seconds"] + 30,
        max_output_bytes=BUDGET["max_output_bytes"],
        env={},
        worktree=str(fx.tree),
    )


def _authority(fx, run_id, head):
    return BOUNDED.ContinuationAuthority(
        checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_action_key=fx.envelope["action_key"],
        envelope_work_item=fx.envelope["work_item_ref"],
        envelope_grant_ref=fx.envelope["grant_ref"],
        envelope_delegation_ref=fx.envelope["delegation_ref"],
        envelope_checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_expires_at=fx.envelope["expires_at"],
        run_id=run_id,
        run_state="FINALIZING",
        run_worker=RUNNER,
        execution_repo="repo",
        execution_revision=head,
        execution_worktree=str(fx.tree),
        execution_dirty=0,
        grant_ref=GRANT,
        grant_delegated_by=CONTEXT,
        grant_valid_from=fx.grant["valid_from"],
        grant_expires_at=fx.grant["expires_at"],
        grant_revoked=False,
        receipt_sha256=fx.result["receipt_sha256"],
    )


class _V15ReceiptAuthority:
    """Clearly labelled #15 test double around the #269 PREVIEW bridge.

    The #269 bridge returns the #14-shaped receipt; Work Engine #15 adds
    `actor_ref`. This wrapper is not a production verifier; it exists only in
    this hermetic LOCAL test to exercise the #15 store with the real offline
    receipt.
    """

    def __init__(self, bridge, receipt_factory, actor_ref: str) -> None:
        self.bridge = bridge
        self.receipt_factory = receipt_factory
        self.actor_ref = actor_ref

    def verify(self, receipt_ref, *, item_ref, run_ref, now):
        receipt = self.bridge.verify(
            receipt_ref, item_ref=item_ref, run_ref=run_ref, now=now)
        return self.receipt_factory(
            item_ref=receipt.item_ref,
            run_ref=receipt.run_ref,
            action_key=receipt.action_key,
            input_digest=receipt.input_digest,
            receipt_sha256=receipt.receipt_sha256,
            process_exit_code=receipt.process_exit_code,
            verdict=receipt.verdict,
            runner_identity=receipt.runner_identity,
            commit_sha=receipt.commit_sha,
            actor_ref=self.actor_ref,
        )


def _scenario(monkeypatch, signed, we15, tmp_path, *, t10: bool):
    tree, head = _git_tree(tmp_path)
    store, item, run = _we_store(we15, tmp_path, tree, head)
    fx = _execute(monkeypatch, tmp_path, store, tree, exit_code=0)
    expected_cont = _expected_continuation(fx, run.id, head)
    authority = _authority(fx, run.id, head)
    bridge = BRIDGE.LocalRunnerReceiptAuthority(
        expected=expected_cont, authority=authority,
        receipt_ref="serveros-runner-receipt:" + expected_cont.action_key,
        expected_anchor=fx.result["receipt_sha256"],
        receipt_factory=lambda **kw: SimpleNamespace(**kw),
        error_factory=we15.ReceiptError)
    receipt = bridge.verify(
        bridge.receipt_ref, item_ref=item.id, run_ref=run.id, now=time.time())

    if t10:
        result = store.finalize_run(
            item.id, run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision=head, dirty=False,
            t10_required=True,
            t10_binding=we15.T10Binding(
                item_ref=item.id, run_ref=run.id, commit_sha=head,
                actor_ref=CONTEXT),
            receipt_ref=bridge.receipt_ref,
            receipt_authority=_V15ReceiptAuthority(
                bridge, we15.VerifiedRunReceipt, CONTEXT))
    else:
        result = store.finalize_run(
            item.id, run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision=head, dirty=False)

    readback = signed.read_work_engine_finalization(
        store.db_path, item.id, run.id)
    expected = signed.ExpectedFinalization(
        item_ref=item.id,
        run_ref=run.id,
        commit_sha=head,
        actor_ref=CONTEXT,
        action_key=receipt.action_key,
        input_digest=receipt.input_digest,
        receipt_sha256=receipt.receipt_sha256,
        process_exit_code=0,
        runner_identity=RUNNER,
    )
    return SimpleNamespace(
        tree=tree, head=head, store=store, item=item, run=run, fx=fx,
        result=result, readback=readback, expected=expected, bridge=bridge)


def test_legacy_unsigned_succeeded_is_denied(monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=False)
    assert sc.result["state"] == "SUCCEEDED"
    assert sc.result["authorization"] == "UNSIGNED"
    assert sc.result["t10_acceptable"] is False
    assert sc.readback.run_state == "SUCCEEDED"

    decision = signed.evaluate_signed_finalization(sc.expected, sc.readback)
    assert decision.decision == signed.DECISION_DENY, decision.as_dict()
    assert "UNSIGNED_FINALIZATION" in decision.reasons or \
           "AUTHORIZATION_NOT_T10_VERIFIED" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.parent_accepted is False
    assert decision.qa_signature is False


def test_t10_verified_exact_result_is_eligible_for_qa_only(
        monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    assert sc.result["authorization"] == "T10_VERIFIED"
    assert sc.result["t10_acceptable"] is True
    assert sc.readback.authorization == "T10_VERIFIED"
    assert sc.readback.t10_acceptable is True
    assert sc.readback.evidence_verified is True

    decision = signed.evaluate_signed_finalization(sc.expected, sc.readback)
    assert decision.decision == signed.DECISION_ELIGIBLE, decision.as_dict()
    assert decision.eligible_for_qa_review is True
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.requires_independent_qa is True
    assert decision.t10_status == "T10_VERIFIED"
    assert decision.production_status == "BLOCKED_TRUST_SOURCE"
    assert decision.evidence_level == "LOCAL_HERMETIC"


def test_missing_t10_acceptable_is_denied(monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    bad = dataclasses.replace(sc.readback, t10_acceptable=None)
    decision = signed.evaluate_signed_finalization(sc.expected, bad)
    assert decision.decision == signed.DECISION_DENY, decision.as_dict()
    assert "T10_NOT_ACCEPTABLE" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_caller_json_label_is_not_authority(monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    fabricated = {
        "state": "SUCCEEDED",
        "authorization": "T10_VERIFIED",
        "t10_acceptable": True,
        "runner_receipt": {"process_exit_code": 0},
    }
    decision = signed.evaluate_signed_finalization(sc.expected, fabricated)
    assert decision.decision == signed.DECISION_DENY, decision.as_dict()
    assert "CALLER_JSON_NOT_AUTHORITY" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_tampered_finalize_evidence_is_denied(
        monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    path = Path(sc.readback.evidence_locator)
    path.write_bytes(path.read_bytes() + b" ")
    tampered = signed.read_work_engine_finalization(
        sc.store.db_path, sc.item.id, sc.run.id)
    assert tampered.evidence_verified is False
    decision = signed.evaluate_signed_finalization(sc.expected, tampered)
    assert decision.decision == signed.DECISION_DENY, decision.as_dict()
    assert "EVIDENCE_TAMPERED" in decision.reasons


def test_inconsistent_commit_actor_or_run_is_denied(
        monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    for changed, code in (
        ({"commit_sha": "f" * 40}, "COMMIT_MISMATCH"),
        ({"actor_ref": "ctx-other"}, "ACTOR_MISMATCH"),
        ({"run_ref": "RUN-9999"}, "RUN_MISMATCH"),
    ):
        bad = dataclasses.replace(sc.expected, **changed)
        decision = signed.evaluate_signed_finalization(bad, sc.readback)
        assert decision.decision == signed.DECISION_DENY, decision.as_dict()
        assert code in decision.reasons, decision.as_dict()


def test_no_verified_zero_process_exit_is_denied(
        monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    boundary = dict(sc.readback.runner_receipt)
    boundary["process_exit_code"] = 1
    bad = dataclasses.replace(sc.readback, runner_receipt=boundary)
    decision = signed.evaluate_signed_finalization(sc.expected, bad)
    assert decision.decision == signed.DECISION_DENY, decision.as_dict()
    assert "NO_VERIFIED_ZERO_EXIT" in decision.reasons
    assert decision.eligible_for_qa_review is False

    expected_one = dataclasses.replace(sc.expected, process_exit_code=1)
    decision = signed.evaluate_signed_finalization(expected_one, sc.readback)
    assert decision.decision == signed.DECISION_DENY, decision.as_dict()
    assert "EXPECTED_EXIT_NOT_ZERO" in decision.reasons


def test_weaker_authorization_is_denied(monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    for weaker in ("SIGNED", "VERIFIED", "T10_VERIFIED ", "t10_verified", ""):
        bad = dataclasses.replace(sc.readback, authorization=weaker)
        decision = signed.evaluate_signed_finalization(sc.expected, bad)
        assert decision.decision == signed.DECISION_DENY, decision.as_dict()
        assert "AUTHORIZATION_NOT_T10_VERIFIED" in decision.reasons


def test_missing_receipt_anchor_or_source_is_denied(
        monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    missing = dataclasses.replace(sc.readback, runner_receipt=None)
    decision = signed.evaluate_signed_finalization(sc.expected, missing)
    assert decision.decision == signed.DECISION_DENY, decision.as_dict()
    assert "MISSING_SOURCE_PROOF" in decision.reasons

    boundary = dict(sc.readback.runner_receipt)
    boundary["receipt_sha256"] = "0" * 64
    bad = dataclasses.replace(sc.readback, runner_receipt=boundary)
    decision = signed.evaluate_signed_finalization(sc.expected, bad)
    assert decision.decision == signed.DECISION_DENY, decision.as_dict()
    assert "RECEIPT_ANCHOR_MISMATCH" in decision.reasons


def test_missing_or_unreadable_source_is_blocked(
        monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    with pytest.raises(signed.FinalizationReadbackError) as ctx:
        signed.read_work_engine_finalization(
            tmp_path / "absent.db", sc.item.id, sc.run.id)
    assert ctx.value.reason_code == "BLOCKED_TRUST_SOURCE"
    decision = signed.evaluate_signed_finalization(sc.expected, None)
    assert decision.decision == signed.DECISION_BLOCKED, decision.as_dict()
    assert "BLOCKED_TRUST_SOURCE" in decision.reasons


def test_succeeded_state_without_finalize_evidence_is_blocked(
        monkeypatch, signed, we15, tmp_path):
    tree, head = _git_tree(tmp_path)
    store, item, run = _we_store(we15, tmp_path, tree, head)
    with store.connection() as conn:
        conn.execute("UPDATE runs SET state='SUCCEEDED' WHERE item_id=? AND run_id=?",
                     (item.id, run.id))
    with pytest.raises(signed.FinalizationReadbackError) as ctx:
        signed.read_work_engine_finalization(store.db_path, item.id, run.id)
    assert ctx.value.reason_code == "BLOCKED_TRUST_SOURCE"


def _verdict_snapshot(store, item_ref: str, run_ref: str):
    conn = sqlite3.connect(store.db_path)
    try:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT item_id, run_id, worker, state, artifact_dir FROM runs "
            "WHERE item_id=? AND run_id=?", (item_ref, run_ref)).fetchone()
        evidence = conn.execute(
            "SELECT item_id, run_id, kind, locator, sha256, command, cases_json, "
            "exit_code, revision, dirty, verdict FROM evidence "
            "WHERE item_id=? AND run_id=? AND kind='finalize' "
            "ORDER BY evidence_id DESC LIMIT 1",
            (item_ref, run_ref)).fetchone()
    finally:
        conn.close()
    assert run is not None and evidence is not None
    locator = str(evidence["locator"])
    data = Path(locator).read_bytes()
    return VERDICT.AuthoritySnapshot(
        run=dict(run), finalize_evidence=(dict(evidence),),
        artifacts={locator: data})


def _verdict_expected(run_ref: str, head: str):
    return VERDICT.ExpectedAcceptance(
        item_id=ITEM,
        run_id=run_ref,
        revision=head,
        principal=CONTEXT,
        command="pytest",
        recognized_runners={CONTEXT: (RUNNER,)},
    )


def test_existing_verdict_consumer_denies_unsigned_state_succeeded(
        monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=False)
    assert sc.result["state"] == "SUCCEEDED"
    assert sc.result["authorization"] == "UNSIGNED"
    decision = VERDICT.evaluate_acceptance(
        _verdict_expected(sc.run.id, sc.head),
        _verdict_snapshot(sc.store, sc.item.id, sc.run.id))
    assert decision.decision == VERDICT.DECISION_DENY, decision.as_dict()
    assert "UNSIGNED_FINALIZATION" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_existing_verdict_consumer_accepts_only_t10_verified(
        monkeypatch, signed, we15, tmp_path):
    sc = _scenario(monkeypatch, signed, we15, tmp_path, t10=True)
    assert sc.result["authorization"] == "T10_VERIFIED"
    decision = VERDICT.evaluate_acceptance(
        _verdict_expected(sc.run.id, sc.head),
        _verdict_snapshot(sc.store, sc.item.id, sc.run.id))
    assert decision.decision == VERDICT.DECISION_ELIGIBLE, decision.as_dict()
    assert decision.eligible_for_qa_review is True
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
