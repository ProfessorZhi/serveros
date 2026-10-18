#!/usr/bin/env python3
"""#237 T10 — continuation engineering provenance, fail-closed.

WHAT THIS FILE PROVES
---------------------
`continuation.execute` used to call `runtime/bounded_executor.execute`, which
spawned a real bounded invocation without ever saying WHICH Work run, commit SHA
or runner identity the command belonged to. This file drives the new binding:

* the trusted executor reads the real Work Engine `runs` and
  `execution_contexts` rows (read-only, through a temporary `serveros.db`);
* it requires exactly one open Run for the Item and exactly one execution
  context for that Run's activity at the granted worktree;
* it requires the execution context's `revision` to be an exact 40-hex SHA,
  equal to the worktree's real `HEAD`, with a clean tracked tree;
* it derives `principal` from the grant, not from the envelope or the child;
* it puts the canonical binding into the config that becomes the bounded
  invocation's stdin, so it is inside `input_digest`;
* it records `receipt_sha256` over the receipt and its stdin/stdout/stderr in
  the existing private `<action>.container` report.

The real `runtime/bounded_invocation.invoke_once` is used unchanged. The Docker
worker argv is replaced by a real offline synthetic runner in this test so the
CPU-only host does not need Docker or a pinned image; the binding/config/anchor
logic under test is the product code, not a fake. The runner prints
`ALL TESTS PASSED` and exits 1 in the denial case, and exits 0 in the happy
case. A child's stdout is hashed, never parsed.

The envelope, grant and checkpoint are built by the real producers
(`wake.capability_envelope`, the grant shape and
`continuation.build_checkpoint`); the existing
`tests/test_continuation_mcp.py` suite covers the authorization decision
(`continuation_control.verify`) that precedes the executor. This file tests the
binding that follows an ALLOW.

WHAT IS STILL OPEN
------------------
Work Engine `finalize_run` does not yet consume this proof. The happy path is
`ELIGIBLE` for independent QA review only, with
`t10_status=BLOCKED_WIRING` and `work_engine_consumption=NOT_WIRED`. The
continuation wake/checkpoint does not create the Work run or execution context;
the owner contract is in the README.
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
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RUNTIME = REPO / "runtime"
MCP = REPO / "mcp"


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


STORE = _load("serveros_runtime_agent_control_store", RUNTIME / "agent_control_store.py")
continuation = _load("serveros_runtime_continuation", RUNTIME / "continuation.py")
wake = _load("serveros_runtime_wake", RUNTIME / "wake.py")
executor = _load("serveros_bounded_executor", RUNTIME / "bounded_executor.py")
bounded = _load("serveros_runtime_bounded_invocation", RUNTIME / "bounded_invocation.py")
VERDICT = _load(
    "serveros_fleet_v1_verdict_evidence",
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "verdict_evidence.py")
BOUNDED = _load(
    "serveros_fleet_v1_bounded_provenance",
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "bounded_provenance.py")

ITEM = "SRV-0001"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
RUN = "RUN-0001"
RUNNER = "claude-code"
ACTIVITY = "implement"
EPOCH = 1
BUDGET = {"wall_seconds": 45, "max_attempts": 2, "max_output_bytes": 200000}
HEAD_RE = "0123456789abcdef0123456789abcdef01234567"

RUNNER_SOURCE = '''#!/usr/bin/env python3
"""Real offline runner: prints the child's PASS text, exits as told."""
import sys
sys.stdin.read()
print("ALL TESTS PASSED - child says SUCCESS")
print("synthetic failing assertion detail", file=sys.stderr)
sys.exit(int(sys.argv[1]))
'''


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
    (tree / "README").write_text("continuation fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=tree, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tree, check=True,
                   capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree, check=True,
                          capture_output=True, text=True).stdout.strip()
    return tree, head


def _state_db(path: Path, *, runs, contexts) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE runs (item_id TEXT, run_id TEXT, activity_id TEXT, "
            "worker TEXT, idempotency_key TEXT, state TEXT, artifact_dir TEXT, "
            "attempt INTEGER, started_at TEXT, ended_at TEXT)")
        conn.execute(
            "CREATE TABLE execution_contexts (context_id INTEGER PRIMARY KEY "
            "AUTOINCREMENT, item_id TEXT, activity_id TEXT, repo TEXT, "
            "mode TEXT, revision TEXT, worktree TEXT, dirty INTEGER)")
        conn.executemany(
            "INSERT INTO runs VALUES (?,?,?,?,?,?,?,?,?,?)", runs)
        conn.executemany(
            "INSERT INTO execution_contexts "
            "(item_id, activity_id, repo, mode, revision, worktree, dirty) "
            "VALUES (?,?,?,?,?,?,?)", contexts)
        conn.commit()
    finally:
        conn.close()


def _run_row(*, run_id=RUN, worker=RUNNER, state="RUNNING", activity=ACTIVITY,
             item=ITEM):
    return (item, run_id, activity, worker, "idem-" + run_id, state,
            "/tmp/artifacts/" + run_id, 1, _now(), "")


def _context_row(*, item=ITEM, activity=ACTIVITY, revision=HEAD_RE,
                 worktree="/tmp/worktree", dirty=0, repo="repo"):
    return (item, activity, repo, "develop", revision, worktree, dirty)


def _grant(tree: Path, **over) -> dict:
    grant = {
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
    grant.update(over)
    return grant


def _checkpoint(tree: Path, **over) -> dict:
    record = continuation.build_checkpoint(
        work_item=ITEM,
        epoch=EPOCH,
        parent_owner="parent-a",
        next_safe_action="run the offline engineering check",
        waiting_on={"kind": "window", "until": _minutes(10)},
        now=_now(),
        requirement_revision="req-1",
        write_scope=[str(tree)],
    )
    record.update(over)
    return record


def _envelope(record: dict, grant: dict, *, key="continuation-wake-SRV-0001-e1-a1",
              **over) -> dict:
    envelope = wake.capability_envelope(
        record, grant, GRANT, attempt=1, key=key,
        spend={"wall_seconds": BUDGET["wall_seconds"],
               "attempts_allowed": BUDGET["max_attempts"]},
        now=_now())
    envelope.update(over)
    return envelope


def _runner(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_runner.py"
    path.write_text(RUNNER_SOURCE, encoding="utf-8")
    return path


def _fixture(tmp_path: Path, *, runs=None, contexts=None, **grant_over):
    tree, head = _git_tree(tmp_path)
    state_db = tmp_path / "state" / "serveros.db"
    _state_db(
        state_db,
        runs=runs if runs is not None else [_run_row()],
        contexts=contexts if contexts is not None else [
            _context_row(revision=head, worktree=str(tree))],
    )
    grant = _grant(tree, **grant_over)
    checkpoint = _checkpoint(tree)
    envelope = _envelope(checkpoint, grant)
    resolved = executor.binding(
        envelope=envelope, grant=grant, checkpoint=checkpoint,
        worktrees_root=tree.parents[1])
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    return SimpleNamespace(
        tree=tree, head=head, state_db=state_db, grant=grant,
        checkpoint=checkpoint, envelope=envelope, resolved=resolved,
        receipts=receipts)


def _execute(monkeypatch, fx, tmp_path, *, exit_code,
             runner=None, called=None):
    runner = runner or _runner(tmp_path)
    calls = called if called is not None else {"invoke": 0}
    real = bounded.invoke_once

    def fake_invoke(*, key, argv, cwd, receipts, input_text, timeout,
                    readonly, max_output_bytes, env=None):
        calls["invoke"] += 1
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
        envelope=fx.envelope, grant=fx.grant, resolved=fx.resolved,
        key="verify", argv=["python3", "-c", "ignored"],
        receipts=fx.receipts, state_db=fx.state_db)
    return result, calls


def _receipt_from(result):
    return result["receipt"]


def _private(result):
    return Path(result["receipt_path"]).parent


def _expected(fx, result, runner, exit_code):
    receipt = _receipt_from(result)
    private = _private(result)
    action = receipt["action_key"]
    input_text = (private / (action + ".stdin")).read_text(encoding="utf-8")
    return BOUNDED.ExpectedContinuation(
        checkpoint_ref=fx.envelope["checkpoint_ref"],
        action_key=action,
        work_item_id=ITEM,
        work_run_id=RUN,
        commit_sha=fx.head,
        principal=CONTEXT,
        grant_ref=GRANT,
        runner_identity=RUNNER,
        argv=(sys.executable, str(runner), str(exit_code)),
        cwd=str(private),
        input_text=input_text,
        readonly=False,
        timeout=BUDGET["wall_seconds"] + 30,
        max_output_bytes=BUDGET["max_output_bytes"],
        env={},
        worktree=str(fx.tree),
    )


def _authority(fx, result, *, run_id=RUN, run_state="RUNNING",
               run_worker=RUNNER, revision=None, worktree=None, dirty=0,
               principal=CONTEXT, revoked=False, expires=None,
               anchor=True):
    return BOUNDED.ContinuationAuthority(
        checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_action_key=fx.envelope["action_key"],
        envelope_work_item=fx.envelope["work_item_ref"],
        envelope_grant_ref=fx.envelope["grant_ref"],
        envelope_delegation_ref=fx.envelope["delegation_ref"],
        envelope_checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_expires_at=fx.envelope["expires_at"],
        run_id=run_id,
        run_state=run_state,
        run_worker=run_worker,
        execution_repo="repo",
        execution_revision=revision or fx.head,
        execution_worktree=worktree or str(fx.tree),
        execution_dirty=dirty,
        grant_ref=GRANT,
        grant_delegated_by=principal,
        grant_valid_from=fx.grant["valid_from"],
        grant_expires_at=expires or fx.grant["expires_at"],
        grant_revoked=revoked,
        receipt_sha256=result.get("receipt_sha256") if anchor else None,
    )


def _evaluate(expected, authority, result, child_report="ALL TESTS PASSED"):
    receipt = BOUNDED.read_bounded_receipt(
        _private(result), expected.action_key)
    assert receipt is not None
    return BOUNDED.evaluate_continuation(
        expected, authority, receipt,
        untrusted_child_report=child_report)


def test_real_execute_exit_one_with_pass_text_is_denied(
        monkeypatch, tmp_path):
    fx = _fixture(tmp_path)
    result, _ = _execute(monkeypatch, fx, tmp_path, exit_code=1)
    receipt = _receipt_from(result)
    assert receipt["process_exit_code"] == 1
    assert b"ALL TESTS PASSED" in (
        _private(result) / (receipt["action_key"] + ".stdout")).read_bytes()
    assert result["receipt_sha256"] == BOUNDED.receipt_anchor(
        BOUNDED.read_bounded_receipt(_private(result), receipt["action_key"]))

    decision = _evaluate(
        _expected(fx, result, tmp_path / "synthetic_runner.py", 1),
        _authority(fx, result), result)
    assert decision.decision == BOUNDED.DECISION_DENY, decision.as_dict()
    assert "PROCESS_EXIT_NONZERO" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.parent_accepted is False
    assert decision.qa_signature is False


def test_real_execute_exit_zero_is_eligible_for_qa_only(
        monkeypatch, tmp_path):
    fx = _fixture(tmp_path)
    result, _ = _execute(monkeypatch, fx, tmp_path, exit_code=0)
    decision = _evaluate(
        _expected(fx, result, tmp_path / "synthetic_runner.py", 0),
        _authority(fx, result), result)
    assert decision.decision == BOUNDED.DECISION_ELIGIBLE, decision.as_dict()
    assert decision.eligible_for_qa_review is True
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.requires_independent_qa is True
    assert decision.t10_status == "BLOCKED_WIRING"
    assert decision.work_engine_consumption == "NOT_WIRED"


def test_missing_open_run_refuses_before_spawn(monkeypatch, tmp_path):
    fx = _fixture(tmp_path, runs=[])
    called = {"invoke": 0}
    with pytest.raises(ValueError, match="open Work Engine Run"):
        _execute(monkeypatch, fx, tmp_path, exit_code=0, called=called)
    assert called["invoke"] == 0
    assert list(fx.receipts.glob("engineering-*")) == []


def test_ambiguous_open_runs_refuse_before_spawn(monkeypatch, tmp_path):
    fx = _fixture(
        tmp_path,
        runs=[_run_row(run_id="RUN-0001"), _run_row(run_id="RUN-0002")])
    called = {"invoke": 0}
    with pytest.raises(ValueError, match="more than one open Work Engine Run"):
        _execute(monkeypatch, fx, tmp_path, exit_code=0, called=called)
    assert called["invoke"] == 0


def test_stale_execution_revision_refuses_before_spawn(monkeypatch, tmp_path):
    tree, head = _git_tree(tmp_path)
    state_db = tmp_path / "state" / "serveros.db"
    _state_db(
        state_db,
        runs=[_run_row()],
        contexts=[_context_row(revision="f" * 40, worktree=str(tree))])
    grant = _grant(tree)
    checkpoint = _checkpoint(tree)
    envelope = _envelope(checkpoint, grant)
    resolved = executor.binding(
        envelope=envelope, grant=grant, checkpoint=checkpoint,
        worktrees_root=tree.parents[1])
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    fx = SimpleNamespace(
        tree=tree, head=head, state_db=state_db, grant=grant,
        checkpoint=checkpoint, envelope=envelope, resolved=resolved,
        receipts=receipts)
    called = {"invoke": 0}
    with pytest.raises(ValueError, match="not the worktree HEAD"):
        _execute(monkeypatch, fx, tmp_path, exit_code=0, called=called)
    assert called["invoke"] == 0


def test_cross_principal_refuses_before_spawn(monkeypatch, tmp_path):
    fx = _fixture(tmp_path)
    fx.envelope["delegation_ref"] = "ctx-other"
    called = {"invoke": 0}
    with pytest.raises(ValueError, match="delegation"):
        _execute(monkeypatch, fx, tmp_path, exit_code=0, called=called)
    assert called["invoke"] == 0


def test_revoked_grant_refuses_before_spawn(monkeypatch, tmp_path):
    fx = _fixture(tmp_path)
    fx.grant["revoked"] = True
    called = {"invoke": 0}
    with pytest.raises(ValueError, match="revoked"):
        _execute(monkeypatch, fx, tmp_path, exit_code=0, called=called)
    assert called["invoke"] == 0


def test_another_work_run_binding_is_denied_by_the_consumer(
        monkeypatch, tmp_path):
    # The store holds RUN-0002; the Parent expected RUN-0001.
    tree, head = _git_tree(tmp_path)
    state_db = tmp_path / "state" / "serveros.db"
    _state_db(
        state_db,
        runs=[_run_row(run_id="RUN-0002")],
        contexts=[_context_row(revision=head, worktree=str(tree))])
    grant = _grant(tree)
    checkpoint = _checkpoint(tree)
    envelope = _envelope(checkpoint, grant)
    resolved = executor.binding(
        envelope=envelope, grant=grant, checkpoint=checkpoint,
        worktrees_root=tree.parents[1])
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    fx = SimpleNamespace(
        tree=tree, head=head, state_db=state_db, grant=grant,
        checkpoint=checkpoint, envelope=envelope, resolved=resolved,
        receipts=receipts)
    result, _ = _execute(monkeypatch, fx, tmp_path, exit_code=0)
    # The producer bound the run the store actually held; the Parent expected
    # RUN-0001. That disagreement must deny, not silently take the store's word.
    decision = _evaluate(
        _expected(fx, result, tmp_path / "synthetic_runner.py", 0),
        _authority(fx, result, run_id=RUN), result)
    assert decision.decision == BOUNDED.DECISION_DENY, decision.as_dict()
    assert "CROSS_RUN" in decision.reasons


def test_missing_receipt_anchor_is_blocked_wiring(monkeypatch, tmp_path):
    fx = _fixture(tmp_path)
    result, _ = _execute(monkeypatch, fx, tmp_path, exit_code=0)
    decision = _evaluate(
        _expected(fx, result, tmp_path / "synthetic_runner.py", 0),
        _authority(fx, result, anchor=False), result)
    assert decision.decision == BOUNDED.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "MISSING_RECEIPT_ANCHOR" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_checksum_tampering_is_denied(monkeypatch, tmp_path):
    fx = _fixture(tmp_path)
    result, _ = _execute(monkeypatch, fx, tmp_path, exit_code=1)
    authority = _authority(fx, result)
    action = _receipt_from(result)["action_key"]
    path = _private(result) / (action + ".json")
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["process_exit_code"] = 0
    path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    decision = _evaluate(
        _expected(fx, result, tmp_path / "synthetic_runner.py", 1),
        authority, result)
    assert decision.decision == BOUNDED.DECISION_DENY, decision.as_dict()
    assert "RECEIPT_ANCHOR_MISMATCH" in decision.reasons
