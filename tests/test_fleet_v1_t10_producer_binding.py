#!/usr/bin/env python3
"""#237 T10 producer wiring — real `work dispatch` receipts, real exit codes.

WHAT THIS FILE IS
-----------------
`bounded_provenance.py` proved the consumer can reject a child's `PASS` text
when it is given a bounded-invocation receipt. That was still `BLOCKED_WIRING`:
the product never wrote the binding, and `_dispatch_answer` never recorded a
receipt anchor.

This file drives the REAL product caller:

    serverosctl work dispatch <cycle> --cwd <worktree> --receipts <private dir>
        --t10-work-item ... --t10-work-run ... --t10-commit-sha ...
        --t10-runner-identity ... -- <real offline runner> 1

The runner is a real process spawned by the unmodified
`runtime/bounded_invocation.invoke_once`. It prints `ALL TESTS PASSED` and exits
1. The product must record `process_exit_code=1`, the canonical T10 binding in
stdin, and a `receipt_sha256` anchor in the DISPATCHED evidence. The consumer
then returns `DENY / PROCESS_EXIT_NONZERO`; the child's text changes nothing.

The exit-0 case is `ELIGIBLE` for independent QA review only. It is still not a
Parent acceptance and not a QA signature, and the product status remains
`BLOCKED_WIRING` until a real Work Engine finalization consumes the producer
proof (`work_engine_consumption == "NOT_WIRED"`).

Boundary: this file uses only a temporary DATA_ROOT and a temporary git
worktree. It never touches the live store, the live receipts directory, a
provider, a model, OAuth, credentials or a protected branch.
"""
from __future__ import annotations

import datetime
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
CLI = REPO / "bin" / "serverosctl"
BOUNDED_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "bounded_provenance.py")
VERDICT_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "verdict_evidence.py")
STORE_PATH = REPO / "runtime" / "agent_control_store.py"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VERDICT = _load("serveros_fleet_v1_verdict_evidence", VERDICT_PATH)
BOUNDED = _load("serveros_fleet_v1_bounded_provenance", BOUNDED_PATH)
STORE = _load("serveros_runtime_agent_control_store_t10_producer", STORE_PATH)
AgentControlStore = STORE.AgentControlStore

CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
ITEM = "SRV-1"
RUNNER = "claude-code"
DIGEST = "a" * 64
BUDGET = {"wall_seconds": 60, "max_attempts": 2, "max_output_bytes": 200000}
FINALIZE_COMMAND = "python -m pytest synthetic"

RUNNER_SOURCE = '''#!/usr/bin/env python3
"""Real offline runner: prints the child's PASS text, exits as told."""
import os
import sys
sys.stdin.read()
print("ENV:" + os.environ.get("SERVEROS_DISPATCH_BINDING", ""))
print("ALL TESTS PASSED - child says SUCCESS")
print("synthetic failing assertion detail", file=sys.stderr)
sys.exit(int(sys.argv[1]))
'''


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _window() -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    return {
        "valid_from": (now - datetime.timedelta(hours=1)).isoformat(),
        "expires_at": (now + datetime.timedelta(days=1)).isoformat(),
    }


def _grant(**over) -> dict:
    grant = {
        "grant_ref": GRANT,
        "delegated_by": CONTEXT,
        "items": [ITEM],
        "budget": dict(BUDGET),
        **_window(),
    }
    grant.update(over)
    return grant


def _record(*, cycle="cyc-1", action="act-1", item=ITEM, holder="cyc-1",
            resource="browser:chromium", **over) -> dict:
    record = {
        "cycle_ref": cycle,
        "grant_ref": GRANT,
        "selection": {
            "selection_digest": DIGEST,
            "observed_at": _now(),
            "selected": [item],
            "carryover": [],
        },
        "reservation": dict(BUDGET),
        "lease": {"resource": resource, "scope": "session", "holder": holder},
        "action_key": action,
        "admitted_at": _now(),
    }
    record.update(over)
    return record


def _data_root(tmp_path: Path) -> dict:
    data = tmp_path / "data"
    for area in ("state/serveros/runtime/agent-control", "state/boards",
                 "workspace/records", "artifacts/work-items"):
        (data / area).mkdir(parents=True, exist_ok=True)
    return {
        "DATA_ROOT": str(data),
        "HOME": str(data),
        "WORKSPACE_ROOT": str(data / "workspace"),
        "ARTIFACTS_ROOT": str(data / "artifacts"),
        "SERVEROS_STATE_DIR": str(data / "state" / "serveros"),
    }


def _store_path(env: dict) -> Path:
    return Path(env["DATA_ROOT"]) / "state/serveros/runtime/agent-control/store.json"


def _cli(*args, env_extra: dict):
    env = dict(os.environ)
    env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True, text=True, cwd=str(REPO), env=env)


def _seed(env: dict) -> AgentControlStore:
    store = AgentControlStore(_store_path(env))
    store.put_context({"context_ref": CONTEXT})
    store.put_grant(_grant())
    return store


def _hold(env: dict, *, cycle: str, resource: str, holder: str) -> None:
    declared = _cli("lease", "declare", resource, "--scope", "session",
                    "--holder", holder, "--sharing", "shared",
                    env_extra=env)
    assert declared.returncode == 0, declared.stderr
    confirmed = _cli("work", "confirm", cycle, "--phase", "LEASE_HELD",
                     env_extra=env)
    assert confirmed.returncode == 0, confirmed.stderr


def _git_worktree(tmp_path: Path):
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    for args in (
        ("git", "init", "-q"),
        ("git", "config", "user.email", "t10@example.invalid"),
        ("git", "config", "user.name", "T10 Producer"),
    ):
        subprocess.run(args, cwd=worktree, check=True, capture_output=True)
    (worktree / "README").write_text("t10 producer fixture\n", encoding="utf-8")
    subprocess.run(("git", "add", "README"), cwd=worktree, check=True,
                   capture_output=True)
    subprocess.run(("git", "commit", "-qm", "initial"), cwd=worktree,
                   check=True, capture_output=True)
    head = subprocess.run(("git", "rev-parse", "HEAD"), cwd=worktree,
                          check=True, capture_output=True, text=True).stdout.strip()
    return worktree, head


def _runner(tmp_path: Path) -> Path:
    path = tmp_path / "synthetic_runner.py"
    path.write_text(RUNNER_SOURCE, encoding="utf-8")
    return path


def _receipts(tmp_path: Path) -> Path:
    path = tmp_path / "receipts"
    path.mkdir(mode=0o700)
    return path


def _dispatch(env, worktree, receipts, runner, *, cycle, action, run, sha,
              exit_code, t10=True, extra=(), command=None):
    args = ["work", "dispatch", cycle, "--cwd", str(worktree),
            "--receipts", str(receipts)]
    if t10:
        args += ["--t10-work-item", ITEM, "--t10-work-run", run,
                 "--t10-commit-sha", sha, "--t10-runner-identity", RUNNER]
    args += list(extra)
    command = list(command) if command is not None else [
        sys.executable, str(runner), str(exit_code)]
    args += ["--", *command]
    return _cli(*args, env_extra=env)


def _binding(*, cycle, action, run, sha, item=ITEM, principal=CONTEXT,
             grant=GRANT, runner=RUNNER) -> dict:
    return {
        "cycle_ref": cycle,
        "action_key": action,
        "work_item_id": item,
        "work_run_id": run,
        "commit_sha": sha,
        "principal": principal,
        "grant_ref": grant,
        "runner_identity": runner,
    }


def _canonical(binding: dict) -> str:
    return json.dumps(binding, sort_keys=True, separators=(",", ":"))


def _expected(binding, *, runner, worktree, exit_code, readonly=False,
              finalize_command=FINALIZE_COMMAND):
    canonical = _canonical(binding)
    return BOUNDED.ExpectedDispatch(
        cycle_ref=binding["cycle_ref"],
        action_key=binding["action_key"],
        work_item_id=binding["work_item_id"],
        work_run_id=binding["work_run_id"],
        commit_sha=binding["commit_sha"],
        principal=binding["principal"],
        grant_ref=binding["grant_ref"],
        runner_identity=binding["runner_identity"],
        argv=(sys.executable, str(runner), str(exit_code)),
        cwd=str(worktree),
        input_text=canonical,
        readonly=readonly,
        timeout=BUDGET["wall_seconds"],
        max_output_bytes=BUDGET["max_output_bytes"],
        env={"SERVEROS_DISPATCH_BINDING": canonical},
        finalize_command=finalize_command,
        selection_digest=DIGEST,
    )


def _admission(store, cycle):
    record = store.get_admission(cycle)
    assert record is not None
    return BOUNDED.AdmissionSnapshot(
        cycle_ref=record["cycle_ref"],
        action_key=record["action_key"],
        grant_ref=record["grant_ref"],
        phase=record["phase"],
        admitted_at=record["admitted_at"],
        retired_at=record.get("retired_at"),
        reservation=record["reservation"],
        lease=record["lease"],
        selection=record["selection"],
        confirmations=record.get("confirmations") or {},
    )


def _grant_snapshot(store, *, ref=GRANT, revoked=None, expires_at=None):
    grant = store.grants()[ref]
    return BOUNDED.GrantSnapshot(
        grant_ref=ref,
        delegated_by=grant["delegated_by"],
        valid_from=grant["valid_from"],
        expires_at=expires_at or grant["expires_at"],
        revoked=bool(grant.get("revoked")) if revoked is None else revoked,
    )


def _work_authority(tmp_path, *, run, sha, exit_code, verdict, worker=RUNNER,
                    tag="work-run"):
    artifact_dir = (tmp_path / tag).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    locator = artifact_dir / "finalize.json"
    payload = {
        "command": FINALIZE_COMMAND,
        "cases": ["synthetic"],
        "exit_code": exit_code,
        "revision": sha,
        "dirty": False,
        "verdict": verdict,
    }
    data = (json.dumps(payload, sort_keys=True) + "\n").encode()
    locator.write_bytes(data)
    run_row = {
        "item_id": ITEM,
        "run_id": run,
        "worker": worker,
        "state": "SUCCEEDED" if verdict == "PASS" and exit_code == 0 else "FAILED",
        "artifact_dir": str(artifact_dir),
    }
    evidence = {
        "item_id": ITEM,
        "run_id": run,
        "kind": "finalize",
        "locator": str(locator),
        "sha256": hashlib.sha256(data).hexdigest(),
        "command": FINALIZE_COMMAND,
        "cases_json": json.dumps(payload["cases"]),
        "exit_code": exit_code,
        "revision": sha,
        "dirty": 0,
        "verdict": verdict,
    }
    return VERDICT.AuthoritySnapshot(
        run=run_row, finalize_evidence=(evidence,),
        artifacts={str(locator): data})


def _evaluate(expected, admission, grant, receipts, work, child_report):
    receipt = BOUNDED.read_bounded_receipt(receipts, expected.action_key)
    assert receipt is not None
    return BOUNDED.evaluate_dispatch(
        expected, admission, grant, receipt, work,
        untrusted_child_report=child_report), receipt


def test_real_producer_exit_one_with_pass_text_is_denied(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record())
    _hold(env, cycle="cyc-1", resource="browser:chromium", holder="cyc-1")
    worktree, head = _git_worktree(tmp_path)
    runner = _runner(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(env, worktree, receipts, runner, cycle="cyc-1",
                     action="act-1", run="RUN-0001", sha=head, exit_code=1)
    assert done.returncode == 0, done.stderr

    receipt = BOUNDED.read_bounded_receipt(receipts, "act-1")
    assert receipt is not None
    assert receipt.receipt["process_exit_code"] == 1
    assert receipt.receipt["status"] == "EXITED"
    assert b"ALL TESTS PASSED" in receipt.stdout_bytes
    binding = _binding(cycle="cyc-1", action="act-1", run="RUN-0001", sha=head)
    assert receipt.stdin_bytes == _canonical(binding).encode()
    # The producer also puts the same canonical binding in the dispatch env;
    # the runner reports it on stdout, which the gate hashes rather than reads.
    assert _canonical(binding).encode() in receipt.stdout_bytes

    confirmation = store.get_admission("cyc-1")["confirmations"]["DISPATCHED"]
    assert confirmation["action_key"] == "act-1"
    assert confirmation["status"] == "EXITED"
    assert confirmation["input_digest"] == receipt.receipt["input_digest"]
    assert confirmation["receipt_sha256"] == BOUNDED.receipt_anchor(receipt)

    decision, _ = _evaluate(
        _expected(binding, runner=runner, worktree=worktree, exit_code=1),
        _admission(store, "cyc-1"), _grant_snapshot(store), receipts,
        _work_authority(tmp_path, run="RUN-0001", sha=head, exit_code=1,
                        verdict="FAIL"),
        "ALL TESTS PASSED - SUCCESS")

    assert decision.decision == BOUNDED.DECISION_DENY, decision.as_dict()
    assert "PROCESS_EXIT_NONZERO" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.t10_status == "BLOCKED_WIRING"
    assert decision.work_engine_consumption == "NOT_WIRED"


def test_real_producer_exit_zero_is_eligible_for_qa_only(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record(cycle="cyc-2", action="act-2", holder="cyc-2",
                        resource="browser:firefox"))
    _hold(env, cycle="cyc-2", resource="browser:firefox", holder="cyc-2")
    worktree, head = _git_worktree(tmp_path)
    runner = _runner(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(env, worktree, receipts, runner, cycle="cyc-2",
                     action="act-2", run="RUN-0002", sha=head, exit_code=0)
    assert done.returncode == 0, done.stderr

    binding = _binding(cycle="cyc-2", action="act-2", run="RUN-0002", sha=head)
    receipt = BOUNDED.read_bounded_receipt(receipts, "act-2")
    assert receipt is not None
    assert receipt.receipt["process_exit_code"] == 0
    assert store.get_admission("cyc-2")["confirmations"]["DISPATCHED"][
        "receipt_sha256"] == BOUNDED.receipt_anchor(receipt)

    decision, _ = _evaluate(
        _expected(binding, runner=runner, worktree=worktree, exit_code=0),
        _admission(store, "cyc-2"), _grant_snapshot(store), receipts,
        _work_authority(tmp_path, run="RUN-0002", sha=head, exit_code=0,
                        verdict="PASS"),
        "ALL TESTS PASSED - SUCCESS")

    assert decision.decision == BOUNDED.DECISION_ELIGIBLE, decision.as_dict()
    assert decision.eligible_for_qa_review is True
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.requires_independent_qa is True
    # The local producer/consumer pair is bound, but a real Work Engine
    # finalization does not yet consume the proof. T10 stays open.
    assert decision.t10_status == "BLOCKED_WIRING"
    assert decision.work_engine_consumption == "NOT_WIRED"


def test_incomplete_t10_binding_is_refused_before_spawn(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record(cycle="cyc-3", action="act-3", holder="cyc-3",
                        resource="browser:webkit"))
    _hold(env, cycle="cyc-3", resource="browser:webkit", holder="cyc-3")
    worktree, head = _git_worktree(tmp_path)
    runner = _runner(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(env, worktree, receipts, runner, cycle="cyc-3",
                     action="act-3", run="RUN-0003", sha=head, exit_code=0,
                     t10=False, extra=("--t10-work-item", ITEM))

    assert done.returncode != 0
    assert "T10" in done.stderr and "Nothing was run" in done.stderr
    assert not (receipts / "act-3.json").exists()
    assert "DISPATCHED" not in store.get_admission("cyc-3")["confirmations"]


def test_changed_sha_is_refused_before_spawn(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record(cycle="cyc-4", action="act-4", holder="cyc-4",
                        resource="browser:edge"))
    _hold(env, cycle="cyc-4", resource="browser:edge", holder="cyc-4")
    worktree, head = _git_worktree(tmp_path)
    runner = _runner(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(env, worktree, receipts, runner, cycle="cyc-4",
                     action="act-4", run="RUN-0004", sha="f" * 40,
                     exit_code=0)

    assert done.returncode != 0
    assert "not the worktree HEAD" in done.stderr
    assert "Nothing was run" in done.stderr
    assert not (receipts / "act-4.json").exists()


def test_revoked_grant_is_refused_before_spawn(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record(cycle="cyc-5", action="act-5", holder="cyc-5",
                        resource="browser:opera"))
    _hold(env, cycle="cyc-5", resource="browser:opera", holder="cyc-5")
    store.revoke_grant(GRANT, at=_now(), reason="t10 producer refusal")
    worktree, head = _git_worktree(tmp_path)
    runner = _runner(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(env, worktree, receipts, runner, cycle="cyc-5",
                     action="act-5", run="RUN-0005", sha=head, exit_code=0)

    assert done.returncode != 0
    assert "Nothing was run" in done.stderr
    assert not (receipts / "act-5.json").exists()


def test_non_t10_dispatch_is_blocked_wiring_not_eligible(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record(cycle="cyc-6", action="act-6", holder="cyc-6",
                        resource="browser:safari"))
    _hold(env, cycle="cyc-6", resource="browser:safari", holder="cyc-6")
    worktree, head = _git_worktree(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(
        env, worktree, receipts, None, cycle="cyc-6", action="act-6",
        run="RUN-0006", sha=head, exit_code=0, t10=False,
        command=["/bin/sh", "-c", "printf 'ALL TESTS PASSED\\n'; exit 0"])
    assert done.returncode == 0, done.stderr

    receipt = BOUNDED.read_bounded_receipt(receipts, "act-6")
    assert receipt is not None
    assert receipt.stdin_bytes == b""
    assert store.get_admission("cyc-6")["confirmations"]["DISPATCHED"][
        "receipt_sha256"] == BOUNDED.receipt_anchor(receipt)
    expected = BOUNDED.ExpectedDispatch(
        cycle_ref="cyc-6", action_key="act-6", work_item_id=ITEM,
        work_run_id="RUN-0006", commit_sha=head, principal=CONTEXT,
        grant_ref=GRANT, runner_identity=RUNNER,
        argv=("/bin/sh", "-c", "printf 'ALL TESTS PASSED\\n'; exit 0"),
        cwd=str(worktree), input_text="", readonly=False,
        timeout=BUDGET["wall_seconds"],
        max_output_bytes=BUDGET["max_output_bytes"], env={},
        finalize_command=FINALIZE_COMMAND, selection_digest=DIGEST,
    )
    decision, _ = _evaluate(
        expected, _admission(store, "cyc-6"), _grant_snapshot(store), receipts,
        _work_authority(tmp_path, run="RUN-0006", sha=head, exit_code=0,
                        verdict="PASS"),
        "ALL TESTS PASSED")
    assert decision.decision == BOUNDED.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "MISSING_DISPATCH_BINDING" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_cross_run_binding_is_denied_with_real_receipt(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record(cycle="cyc-7", action="act-7", holder="cyc-7",
                        resource="browser:chromium"))
    _hold(env, cycle="cyc-7", resource="browser:chromium", holder="cyc-7")
    worktree, head = _git_worktree(tmp_path)
    runner = _runner(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(env, worktree, receipts, runner, cycle="cyc-7",
                     action="act-7", run="RUN-9999", sha=head, exit_code=0)
    assert done.returncode == 0, done.stderr

    # The receipt's own binding names RUN-9999; the Parent expected RUN-0007.
    binding = _binding(cycle="cyc-7", action="act-7", run="RUN-9999", sha=head)
    expected = _expected(binding, runner=runner, worktree=worktree, exit_code=0)
    expected = BOUNDED.ExpectedDispatch(
        **{**expected.__dict__, "work_run_id": "RUN-0007"})
    decision, _ = _evaluate(
        expected, _admission(store, "cyc-7"), _grant_snapshot(store), receipts,
        _work_authority(tmp_path, run="RUN-0007", sha=head, exit_code=0,
                        verdict="PASS"),
        "ALL TESTS PASSED")
    assert decision.decision == BOUNDED.DECISION_DENY, decision.as_dict()
    assert "CROSS_RUN" in decision.reasons


def test_cross_principal_grant_is_denied_with_real_receipt(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record(cycle="cyc-8", action="act-8", holder="cyc-8",
                        resource="browser:firefox"))
    _hold(env, cycle="cyc-8", resource="browser:firefox", holder="cyc-8")
    worktree, head = _git_worktree(tmp_path)
    runner = _runner(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(env, worktree, receipts, runner, cycle="cyc-8",
                     action="act-8", run="RUN-0008", sha=head, exit_code=0)
    assert done.returncode == 0, done.stderr

    binding = _binding(cycle="cyc-8", action="act-8", run="RUN-0008", sha=head)
    grant = BOUNDED.GrantSnapshot(
        grant_ref=GRANT, delegated_by="ctx-other", valid_from="2020-01-01T00:00:00+00:00",
        expires_at="2099-01-01T00:00:00+00:00", revoked=False)
    decision, _ = _evaluate(
        _expected(binding, runner=runner, worktree=worktree, exit_code=0),
        _admission(store, "cyc-8"), grant, receipts,
        _work_authority(tmp_path, run="RUN-0008", sha=head, exit_code=0,
                        verdict="PASS"),
        "ALL TESTS PASSED")
    assert decision.decision == BOUNDED.DECISION_DENY, decision.as_dict()
    assert "CROSS_PRINCIPAL" in decision.reasons


def test_forged_exit_zero_receipt_is_denied_by_the_producer_anchor(tmp_path):
    env = _data_root(tmp_path)
    store = _seed(env)
    store.admit(_record(cycle="cyc-9", action="act-9", holder="cyc-9",
                        resource="browser:edge"))
    _hold(env, cycle="cyc-9", resource="browser:edge", holder="cyc-9")
    worktree, head = _git_worktree(tmp_path)
    runner = _runner(tmp_path)
    receipts = _receipts(tmp_path)

    done = _dispatch(env, worktree, receipts, runner, cycle="cyc-9",
                     action="act-9", run="RUN-0009", sha=head, exit_code=1)
    assert done.returncode == 0, done.stderr
    admission = _admission(store, "cyc-9")

    path = receipts / "act-9.json"
    tampered = json.loads(path.read_text(encoding="utf-8"))
    tampered["process_exit_code"] = 0
    path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")

    binding = _binding(cycle="cyc-9", action="act-9", run="RUN-0009", sha=head)
    decision, _ = _evaluate(
        _expected(binding, runner=runner, worktree=worktree, exit_code=1),
        admission, _grant_snapshot(store), receipts,
        _work_authority(tmp_path, run="RUN-0009", sha=head, exit_code=0,
                        verdict="PASS"),
        "PASS")
    assert decision.decision == BOUNDED.DECISION_DENY, decision.as_dict()
    assert "RECEIPT_ANCHOR_MISMATCH" in decision.reasons
    assert decision.eligible_for_qa_review is False
