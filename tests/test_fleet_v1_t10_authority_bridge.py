#!/usr/bin/env python3
"""#237 T10 — a real local runner receipt served to the public Work Engine.

WHAT THIS PROVES
----------------
The public in-tree `workengine` ships `ServerOSStore.finalize_run_verified` and
the `RunnerReceiptAuthority` contract, but leaves the producer unbound. This
file builds the T10 side of that seam as an **opt-in PREVIEW-only** adapter and
proves it with the public in-tree package, a real temporary git worktree, the
real `runtime/bounded_invocation.invoke_once`, and a real offline runner:

* the parent mints an opaque `receipt_ref` for the admitted action; a child
  text path, a caller JSON hash, or an arbitrary filesystem path is not one;
* the authority reads the private `invoke_once` receipt under that action,
  recomputes `input_digest` and `receipt_sha256` over receipt + stdin/stdout/
  stderr, and refuses when the parent-recalled anchor does not match;
* it requires the exact Work Engine Run / Item / commit SHA / runner identity /
  grant/principal binding from the continuation authority;
* the real child prints `ALL TESTS PASSED` and exits 1. The authority returns
  `process_exit_code=1`, `verdict=FAIL` (child text is hashed, never parsed),
  and `finalize_run_verified(verdict=PASS, exit_code=0)` MUST refuse success;
* the exact trusted exit 0 returns a `VerifiedRunReceipt` that
  `finalize_run_verified` accepts, writes into the existing `finalize.json`
  evidence, and concludes `SUCCEEDED` for **independent QA only**.

WHAT THIS DOES NOT CLAIM
------------------------
The bridge is not wired into production. The Work Engine seam remains
`BLOCKED_ORIGINAL_OWNER` and ServerOS remains `BLOCKED_WIRING` until a real
finalize call site consumes the proof and an independent QA owner signs.
Evidence level: LOCAL / PREVIEW_ONLY, not REAL_DEVICE or PRODUCTION.

HISTORICAL_PRIVATE_SOURCE_UNVERIFIED: this suite used to extract Work Engine
#14 (`e4c4a2b…`) from an owner-private checkout. It no longer reads that path
and does not compare against it. Exact old-source equality is NOT_RUN, never
PASS; the public in-tree package is the source under test.
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
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
RUNTIME = REPO / "runtime"
#: The old #14 archive pin, kept only as a historical label. This suite does
#: not read that checkout, so exact old-source equality is
#: HISTORICAL_PRIVATE_SOURCE_UNVERIFIED / NOT_RUN — never a pass.
HISTORICAL_WE14_HEAD = "e4c4a2b2b3fb6fb7a0af564fa30d8bc5eea3043a"
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
def bridge_module():
    return _load("serveros_fleet_v1_authority_bridge", BRIDGE_PATH)


@pytest.fixture(scope="session")
def we14():
    """The public in-tree Work Engine package, imported normally.

    The former fixture extracted an owner-private #14 checkout and deleted
    every `workengine*` entry from `sys.modules`. That read a path Dell does not
    have and changed module identity for every later consumer. This fixture
    imports the package the tree ships, so it neither reads a private path nor
    swaps `sys.modules`; the public `workengine.authority` dataclasses are the
    contract under test.
    """
    from workengine import WorkItem
    from workengine.authority import ReceiptError, VerifiedRunReceipt
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
    )


def test_the_public_fixture_keeps_the_in_tree_module_identity(we14):
    """No second `workengine` object is left in `sys.modules`.

    The old fixture deleted and re-imported the package from a private archive,
    so a later consumer could hold one `ReceiptError` while the store raised
    another (#332). This fixture imports the in-tree package and must not
    replace it; the object identity is the assertion.
    """
    import workengine.authority

    assert sys.modules["workengine.authority"] is workengine.authority
    assert we14.ReceiptError is workengine.authority.ReceiptError
    assert Path(workengine.authority.__file__).resolve().is_relative_to(REPO)


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
    (tree / "README").write_text("authority bridge fixture\n", encoding="utf-8")
    subprocess.run(["git", "add", "README"], cwd=tree, check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-qm", "initial"], cwd=tree, check=True,
                   capture_output=True)
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree, check=True,
                          capture_output=True, text=True).stdout.strip()
    return tree, head


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


def _checkpoint(tree: Path) -> dict:
    return continuation.build_checkpoint(
        work_item=ITEM,
        epoch=EPOCH,
        parent_owner="parent-a",
        next_safe_action="run the offline engineering check",
        waiting_on={"kind": "window", "until": _minutes(10)},
        now=_now(),
        requirement_revision="req-1",
        write_scope=[str(tree)],
    )


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


def _we_store(we14, tmp_path: Path, tree: Path, head: str):
    store = we14.ServerOSStore(tmp_path / "we-state")
    store.register_project("demo", "SRV")
    item = store.create_item(
        we14.WorkItem(ITEM, "demo", "x", "test", "/card"))
    store.add_activity(we14.Activity(item.id, ACTIVITY, "test"))
    store.attach_context(we14.ExecutionContext(
        item.id, ACTIVITY, "repo", we14.ContextMode.TEST, head,
        str(tree), False))
    run = store.begin_run(
        item.id, ACTIVITY, RUNNER, "authority-bridge-1",
        tmp_path / "artifacts")
    for state in (we14.RunState.PREPARING, we14.RunState.RUNNING,
                  we14.RunState.FINALIZING):
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


def _expected(fx, tree, head, *, exit_code):
    receipt = fx.result["receipt"]
    private = Path(fx.result["receipt_path"]).parent
    action = receipt["action_key"]
    input_text = (private / (action + ".stdin")).read_text(encoding="utf-8")
    return BOUNDED.ExpectedContinuation(
        checkpoint_ref=fx.envelope["checkpoint_ref"],
        action_key=action,
        work_item_id=ITEM,
        work_run_id=fx.run_id,
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
        worktree=str(tree),
    )


def _authority(fx, head, tree, *, run_state="FINALIZING", anchor=True):
    return BOUNDED.ContinuationAuthority(
        checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_action_key=fx.envelope["action_key"],
        envelope_work_item=fx.envelope["work_item_ref"],
        envelope_grant_ref=fx.envelope["grant_ref"],
        envelope_delegation_ref=fx.envelope["delegation_ref"],
        envelope_checkpoint_ref=fx.envelope["checkpoint_ref"],
        envelope_expires_at=fx.envelope["expires_at"],
        run_id=fx.run_id,
        run_state=run_state,
        run_worker=RUNNER,
        execution_repo="repo",
        execution_revision=head,
        execution_worktree=str(tree),
        execution_dirty=0,
        grant_ref=GRANT,
        grant_delegated_by=CONTEXT,
        grant_valid_from=fx.grant["valid_from"],
        grant_expires_at=fx.grant["expires_at"],
        grant_revoked=False,
        receipt_sha256=fx.result["receipt_sha256"] if anchor else None,
    )


def _bridge(bridge_module, we14, expected, authority, *, receipt_ref=None,
            anchor=None):
    def receipt(**fields):
        # The public `VerifiedRunReceipt` adds `actor_ref`; the PREVIEW bridge
        # predates that field and supplies the other nine. The actor is the
        # same grant principal the continuation authority already names — this
        # is a test-local adapter, not a second authority or a caller claim.
        return we14.VerifiedRunReceipt(**fields, actor_ref=CONTEXT)

    return bridge_module.LocalRunnerReceiptAuthority(
        expected=expected,
        authority=authority,
        receipt_ref=receipt_ref or ("serveros-runner-receipt:"
                                    + expected.action_key),
        expected_anchor=(authority.receipt_sha256 if anchor is None else anchor),
        receipt_factory=receipt,
        error_factory=we14.ReceiptError,
    )


def _scenario(monkeypatch, bridge_module, we14, tmp_path, *, exit_code=0):
    tree, head = _git_tree(tmp_path)
    store, item, run = _we_store(we14, tmp_path, tree, head)
    fx = _execute(monkeypatch, tmp_path, store, tree, exit_code=exit_code)
    fx.run_id = run.id
    expected = _expected(fx, tree, head, exit_code=exit_code)
    authority = _authority(fx, head, tree)
    bridge = _bridge(bridge_module, we14, expected, authority)
    return SimpleNamespace(
        tree=tree, head=head, store=store, item=item, run=run,
        fx=fx, expected=expected, authority=authority, bridge=bridge)


def test_real_exit_one_with_pass_text_cannot_conclude_success(
        monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=1)
    receipt = sc.bridge.verify(
        sc.bridge.receipt_ref, item_ref=sc.item.id, run_ref=sc.run.id,
        now=time.time())
    assert isinstance(receipt, we14.VerifiedRunReceipt)
    assert receipt.actor_ref == CONTEXT
    assert receipt.process_exit_code == 1
    assert receipt.verdict == "FAIL"
    assert receipt.receipt_sha256 == sc.fx.result["receipt_sha256"]
    assert receipt.commit_sha == sc.head
    assert receipt.runner_identity == RUNNER
    assert receipt.input_digest == sc.fx.result["receipt"]["input_digest"]

    with pytest.raises(we14.ReceiptError) as ctx:
        sc.store.finalize_run_verified(
            sc.item.id, sc.run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision=sc.head, dirty=False,
            receipt_ref=sc.bridge.receipt_ref, receipt_authority=sc.bridge)
    assert ctx.value.reason_code in {
        "EXIT_CODE_DISAGREEMENT", "RECEIPT_VERDICT_DISAGREEMENT"}
    assert sc.store.get_run(sc.item.id, sc.run.id).state == we14.RunState.FINALIZING
    with sc.store.connection() as conn:
        assert conn.execute("SELECT COUNT(*) FROM evidence").fetchone()[0] == 0


def test_real_exit_zero_verified_receipt_concludes_succeeded(
        monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=0)
    result = sc.store.finalize_run_verified(
        sc.item.id, sc.run.id, verdict="PASS", exit_code=0,
        command="pytest", cases=["one"], revision=sc.head, dirty=False,
        receipt_ref=sc.bridge.receipt_ref, receipt_authority=sc.bridge)
    assert result == {"state": "SUCCEEDED", "verdict": "PASS"}
    assert sc.store.get_run(sc.item.id, sc.run.id).state == we14.RunState.SUCCEEDED

    locator = Path(sc.store.get_run(sc.item.id, sc.run.id).artifact_dir) / "finalize.json"
    payload = json.loads(locator.read_text(encoding="utf-8"))
    boundary = payload["runner_receipt"]
    assert boundary["action_key"] == sc.fx.result["receipt"]["action_key"]
    assert boundary["input_digest"] == sc.fx.result["receipt"]["input_digest"]
    assert boundary["receipt_sha256"] == sc.fx.result["receipt_sha256"]
    assert boundary["process_exit_code"] == 0
    assert boundary["runner_identity"] == RUNNER
    assert boundary["commit_sha"] == sc.head
    with sc.store.connection() as conn:
        row = conn.execute(
            "SELECT sha256 FROM evidence WHERE item_id=? AND run_id=?",
            (sc.item.id, sc.run.id)).fetchone()
    assert row["sha256"] == hashlib.sha256(locator.read_bytes()).hexdigest()

    # The bridge is preview-only; a real finalize call site and independent QA
    # are still absent.
    assert bridge_module.STATUS == "PREVIEW_ONLY"
    assert bridge_module.PRODUCTION_STATUS == "BLOCKED_ORIGINAL_OWNER"


def test_a_child_text_path_is_not_a_receipt(
        monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=0)
    child = tmp_path / "child.txt"
    child.write_text("ALL TESTS PASSED\n", encoding="utf-8")
    with pytest.raises(we14.ReceiptError) as ctx:
        sc.bridge.verify(str(child), item_ref=sc.item.id, run_ref=sc.run.id,
                         now=time.time())
    assert ctx.value.reason_code in {"UNTRUSTED_RECEIPT", "RUNNER_RECEIPT_REQUIRED"}
    with pytest.raises(we14.ReceiptError):
        sc.store.finalize_run_verified(
            sc.item.id, sc.run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision=sc.head, dirty=False,
            receipt_ref=str(child), receipt_authority=sc.bridge)
    assert sc.store.get_run(sc.item.id, sc.run.id).state == we14.RunState.FINALIZING


def test_missing_trusted_context_is_blocked_trust_source(
        monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=0)
    with pytest.raises(we14.ReceiptError) as ctx:
        bridge_module.LocalRunnerReceiptAuthority(
            expected=sc.expected, authority=None,
            receipt_ref="serveros-runner-receipt:" + sc.expected.action_key,
            expected_anchor=sc.fx.result["receipt_sha256"],
            receipt_factory=we14.VerifiedRunReceipt,
            error_factory=we14.ReceiptError)
    assert ctx.value.reason_code == "BLOCKED_TRUST_SOURCE"
    with pytest.raises(we14.ReceiptError) as ctx:
        sc.store.finalize_run_verified(
            sc.item.id, sc.run.id, verdict="PASS", exit_code=0,
            command="pytest", cases=["one"], revision=sc.head, dirty=False,
            receipt_ref=sc.bridge.receipt_ref, receipt_authority=None)
    assert ctx.value.reason_code == "BLOCKED_TRUST_SOURCE"


def test_missing_parent_anchor_is_blocked(monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=0)
    with pytest.raises(we14.ReceiptError) as ctx:
        _bridge(bridge_module, we14, sc.expected, sc.authority, anchor="")
    assert ctx.value.reason_code == "BLOCKED_TRUST_SOURCE"


def test_stale_sha_is_refused(monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=0)
    stale = dataclasses.replace(sc.expected, commit_sha="f" * 40)
    stale_bridge = _bridge(bridge_module, we14, stale, sc.authority)
    with pytest.raises(we14.ReceiptError) as ctx:
        stale_bridge.verify(
            stale_bridge.receipt_ref, item_ref=sc.item.id,
            run_ref=sc.run.id, now=time.time())
    assert ctx.value.reason_code in {"STALE_SHA", "UNTRUSTED_RECEIPT"}


def test_revoked_grant_is_refused(monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=0)
    revoked = dataclasses.replace(sc.authority, grant_revoked=True)
    bridge = _bridge(bridge_module, we14, sc.expected, revoked)
    with pytest.raises(we14.ReceiptError) as ctx:
        bridge.verify(bridge.receipt_ref, item_ref=sc.item.id,
                      run_ref=sc.run.id, now=time.time())
    assert ctx.value.reason_code == "GRANT_REVOKED"


def test_cross_run_is_refused(monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=0)
    with pytest.raises(we14.ReceiptError) as ctx:
        sc.bridge.verify(sc.bridge.receipt_ref, item_ref=sc.item.id,
                         run_ref="RUN-9999", now=time.time())
    assert ctx.value.reason_code == "RECEIPT_RUN_MISMATCH"


def test_cross_principal_is_refused(monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=0)
    other = dataclasses.replace(sc.authority, grant_delegated_by="ctx-other")
    bridge = _bridge(bridge_module, we14, sc.expected, other)
    with pytest.raises(we14.ReceiptError) as ctx:
        bridge.verify(bridge.receipt_ref, item_ref=sc.item.id,
                      run_ref=sc.run.id, now=time.time())
    assert ctx.value.reason_code in {"CROSS_PRINCIPAL", "UNTRUSTED_RECEIPT"}


def test_checksum_tampering_is_refused(monkeypatch, bridge_module, we14, tmp_path):
    sc = _scenario(monkeypatch, bridge_module, we14, tmp_path, exit_code=1)
    path = Path(sc.fx.result["receipt_path"])
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["process_exit_code"] = 0
    path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    with pytest.raises(we14.ReceiptError) as ctx:
        sc.bridge.verify(sc.bridge.receipt_ref, item_ref=sc.item.id,
                         run_ref=sc.run.id, now=time.time())
    assert ctx.value.reason_code in {"UNTRUSTED_RECEIPT", "INVALID_RECEIPT"}
