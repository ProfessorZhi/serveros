#!/usr/bin/env python3
"""#237 T10 — a child's success text is not a runner receipt.

THE ACCEPTANCE THIS FILE PINS
-----------------------------
Issue #237's T10 row says exactly one thing:

    T10 | Child self-reports success while tests fail | Parent not ACCEPTED

The gate under test therefore has to answer a narrower question than "did the
work succeed": it has to decide whether the PARENT may treat a claimed success
as evidence at all.  Prose is not evidence.  The only thing that may move a Run
or a QA gate is a structured record read back from the existing authority —
Work Engine's `runs` row and its `kind=finalize` evidence row — whose exit code,
verdict, exact revision, artifact digest and runner identity all agree.

The negative fixtures in `integrations/fleet-v1-verdict-evidence/fixtures/
negative_cases.json` are the positive control for that sentence.  Each one is a
case that a plausible "the child said it worked" gate would accept, and each
one states the reason code that must reject or leave it UNKNOWN.

WHAT THIS FILE DOES NOT PROVE
-----------------------------
It does not sign a V1 QA verdict, and it does not close T10.  The gate's receipt
is only as strong as Work Engine's producer: `finalize_run` takes `exit_code`
and `verdict` as arguments from the finalizing caller.  The runner-produced
process fact (`runtime/bounded_invocation.invoke_once`'s `process_exit_code`)
is not yet linked into that row.  The module's README states the owner
integration that would be required; until it lands, this is a
text-versus-receipt seam, not proof that a hostile child cannot forge the DB.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "verdict_evidence.py")
FIXTURES_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "fixtures"
    / "negative_cases.json")
MODULE_NAME = "serveros_fleet_v1_verdict_evidence"


def _load_gate():
    """Load the seam by path, the way the runtime loads its own modules.

    `integrations/fleet-v1-verdict-evidence` cannot be an importable package
    name (the directory has hyphens), and adding a second package alias would
    create the two-names-for-one-file problem the runtime comments warn about.
    Loading by path keeps one module object and one name.
    """
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gate():
    return _load_gate()


FIXTURES = json.loads(FIXTURES_PATH.read_text(encoding="utf-8"))
BASE = FIXTURES["base"]
CASES = FIXTURES["cases"]
CASE_BY_NAME = {case["name"]: case for case in CASES}


def _merge(base, override):
    """Recursive dict merge; lists and scalars replace."""
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def _build(case, tmp_path, gate):
    """Turn one fixture case into the two inputs the pure gate accepts.

    The artifact bytes are written into a real temporary run directory, because
    the gate checks the locator's containment by resolving it.  Nothing here
    writes to a Work Engine store: the fixture stands in for rows a read-only
    authority adapter would have returned.
    """
    expected = gate.ExpectedAcceptance(
        **_merge(BASE["expected"], case.get("expected", {})))
    run_raw = _merge(BASE["run"], case.get("run", {}))
    artifact_dir = (tmp_path / run_raw.pop("artifact_dir")).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    run = {
        "item_id": expected.item_id,
        "run_id": expected.run_id,
        "worker": run_raw["worker"],
        "state": run_raw["state"],
        "artifact_dir": str(artifact_dir),
    }

    artifacts = {}
    rows = []
    for raw in case.get("evidence", [BASE["evidence"]]):
        row = _merge(BASE["evidence"], raw)
        artifact_spec = row.pop("fixture_artifact", {}) or {}
        locator = (artifact_dir / row["locator"]).resolve()
        row.setdefault("item_id", expected.item_id)
        row.setdefault("run_id", expected.run_id)
        row.setdefault("command", expected.command)
        row.setdefault("exit_code", 0)
        row.setdefault("revision", expected.revision)
        row.setdefault("dirty", 0)
        row.setdefault("verdict", "PASS")
        row.setdefault("kind", "finalize")
        row.setdefault("cases_json", json.dumps(["case"]))
        row["locator"] = str(locator)

        payload = {
            "command": row["command"],
            "cases": json.loads(row["cases_json"]),
            "exit_code": row["exit_code"],
            "revision": row["revision"],
            "dirty": bool(row["dirty"]),
            "verdict": str(row["verdict"]).upper(),
        }
        if "content" in artifact_spec:
            payload = artifact_spec["content"]
        if artifact_spec.get("omit"):
            row.setdefault("sha256", hashlib.sha256(b"absent").hexdigest())
        else:
            data = (json.dumps(payload, sort_keys=True) + "\n").encode()
            locator.parent.mkdir(parents=True, exist_ok=True)
            locator.write_bytes(data)
            artifacts[str(locator)] = data
            row.setdefault("sha256", hashlib.sha256(data).hexdigest())
        rows.append(row)

    authority = gate.AuthoritySnapshot(
        run=run, finalize_evidence=tuple(rows), artifacts=artifacts)
    return expected, authority


def _valid_case(tmp_path, gate):
    return _build(
        CASE_BY_NAME["valid_receipt_is_eligible_but_not_accepted"],
        tmp_path, gate)


@pytest.mark.parametrize("case", CASES, ids=[case["name"] for case in CASES])
def test_negative_fixture_never_accepts_on_claim_text(gate, case, tmp_path):
    expected, authority = _build(case, tmp_path, gate)
    decision = gate.evaluate_acceptance(
        expected, authority,
        untrusted_child_report=case.get("claim"))

    want = case["want"]
    assert decision.decision == want["decision"], decision.as_dict()
    for reason in want.get("includes", []):
        assert reason in decision.reasons, decision.as_dict()

    # One rule for every case, including the eligible one: this gate never
    # accepts for a Parent and never signs for QA.
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.requires_independent_qa is True
    assert decision.run_state_advanced is False
    assert decision.qa_gate_advanced is False
    assert decision.eligible_for_qa_review is (
        decision.decision == gate.DECISION_ELIGIBLE)
    assert decision.child_report_ignored is True


def test_valid_receipt_is_eligible_but_not_a_signature(gate, tmp_path):
    expected, authority = _valid_case(tmp_path, gate)
    decision = gate.evaluate_acceptance(
        expected, authority, untrusted_child_report="SUCCESS")

    assert decision.decision == gate.DECISION_ELIGIBLE
    assert decision.reasons == ()
    assert decision.eligible_for_qa_review is True
    # Eligibility is not acceptance, and a PASS receipt is not a QA signature.
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.requires_independent_qa is True
    assert decision.run_state_advanced is False
    assert decision.qa_gate_advanced is False


def test_child_text_is_never_the_source_of_truth(gate, tmp_path):
    expected, authority = _valid_case(tmp_path, gate)
    passed = gate.evaluate_acceptance(
        expected, authority, untrusted_child_report="SUCCESS exit 0 PASS")
    failed = gate.evaluate_acceptance(
        expected, authority, untrusted_child_report="FAILED exit 1")

    assert passed.decision == failed.decision == gate.DECISION_ELIGIBLE
    assert passed.reasons == failed.reasons == ()
    assert passed.child_report_ignored is True
    assert failed.child_report_ignored is True


def test_missing_receipt_cannot_advance_on_success_text(gate, tmp_path):
    case = CASE_BY_NAME["text_success_without_receipt"]
    expected, authority = _build(case, tmp_path, gate)
    decision = gate.evaluate_acceptance(
        expected, authority,
        untrusted_child_report="All tests passed with exit code 0. SUCCESS.")

    assert decision.decision == gate.DECISION_UNKNOWN
    assert "MISSING_RECEIPT" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.parent_accepted is False


def test_nonzero_exit_rejects_even_when_verdict_says_pass(gate, tmp_path):
    case = CASE_BY_NAME["forged_pass_with_nonzero_exit"]
    expected, authority = _build(case, tmp_path, gate)
    decision = gate.evaluate_acceptance(
        expected, authority, untrusted_child_report="PASS")

    assert decision.decision == gate.DECISION_DENY
    assert "TESTS_FAILED" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_tampered_artifact_bytes_are_denied(gate, tmp_path):
    expected, authority = _valid_case(tmp_path, gate)
    row = authority.finalize_evidence[0]
    artifacts = dict(authority.artifacts)
    artifacts[row["locator"]] = artifacts[row["locator"]] + b" "
    tampered = gate.AuthoritySnapshot(
        run=authority.run, finalize_evidence=authority.finalize_evidence,
        artifacts=artifacts)

    decision = gate.evaluate_acceptance(expected, tampered)
    assert decision.decision == gate.DECISION_DENY
    assert "DIGEST_MISMATCH" in decision.reasons


def test_verdict_word_is_folded_case_insensitively(gate, tmp_path):
    expected, authority = _valid_case(tmp_path, gate)
    row = dict(authority.finalize_evidence[0])
    row["verdict"] = "pass"
    folded = gate.AuthoritySnapshot(
        run=authority.run, finalize_evidence=(row,),
        artifacts=authority.artifacts)

    decision = gate.evaluate_acceptance(expected, folded)
    assert decision.decision == gate.DECISION_ELIGIBLE
    assert decision.reasons == ()


def test_expected_revision_must_be_an_exact_full_sha(gate):
    expected = gate.ExpectedAcceptance(
        item_id="SRV-0100",
        run_id="RUN-0001",
        revision="abc123",
        principal="agent:alice",
        command="python -m pytest",
        recognized_runners={"agent:alice": ("claude-code",)},
    )
    with pytest.raises(ValueError):
        gate.evaluate_acceptance(
            expected,
            gate.AuthoritySnapshot(run=None, finalize_evidence=(), artifacts={}))


def test_an_unknown_principal_expectation_is_refused(gate, tmp_path):
    expected, authority = _valid_case(tmp_path, gate)
    unknown = gate.ExpectedAcceptance(
        item_id=expected.item_id,
        run_id=expected.run_id,
        revision=expected.revision,
        principal="agent:nobody",
        command=expected.command,
        recognized_runners={"agent:alice": ("claude-code",)},
    )
    with pytest.raises(ValueError):
        gate.evaluate_acceptance(unknown, authority)


def test_the_gate_does_not_mutate_the_authority_snapshot(gate, tmp_path):
    expected, authority = _valid_case(tmp_path, gate)
    before = copy.deepcopy(authority)
    gate.evaluate_acceptance(expected, authority, untrusted_child_report="PASS")
    assert authority == before


# ---------------------------------------------------------------------------
# T10 product seam: reconcile Work Engine with bounded-invocation provenance
# ---------------------------------------------------------------------------
#
# The tests below do NOT hand the adapter a JSON exit code. They call the real
# `runtime/bounded_invocation.invoke_once` (unmodified, loaded by path), which
# spawns a real offline synthetic runner, writes a real receipt under a private
# 0700 directory, and records stdout/stderr and their digests. The adapter then
# reads those files and has to decide from them.
#
# The producer contract this exercises and the current wiring gap it exposes
# are stated in `integrations/fleet-v1-verdict-evidence/README.md`.

BOUNDED_MODULE_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "bounded_provenance.py")
BOUNDED_MODULE_NAME = "serveros_fleet_v1_bounded_provenance"
BOUNDED_EXECUTOR_PATH = REPO / "runtime" / "bounded_invocation.py"
BOUNDED_EXECUTOR_NAME = "serveros_runtime_bounded_invocation_t10"

REV_A = "0123456789abcdef0123456789abcdef01234567"
REV_B = "fedcba9876543210fedcba9876543210fedcba98"
FINALIZE_COMMAND = "python -m pytest tests/test_fleet_v1_verdict_evidence.py -q"

SYNTHETIC_RUNNER = '''#!/usr/bin/env python3
"""Offline runner: prints the child's PASS text, then exits as told."""
import sys
sys.stdin.read()
print("ALL TESTS PASSED - child says SUCCESS")
print("synthetic failing assertion detail", file=sys.stderr)
sys.exit(int(sys.argv[1]))
'''


def _load_bounded():
    if BOUNDED_MODULE_NAME in sys.modules:
        return sys.modules[BOUNDED_MODULE_NAME]
    spec = importlib.util.spec_from_file_location(
        BOUNDED_MODULE_NAME, BOUNDED_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BOUNDED_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[BOUNDED_MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


def _load_bounded_executor():
    if BOUNDED_EXECUTOR_NAME in sys.modules:
        return sys.modules[BOUNDED_EXECUTOR_NAME]
    spec = importlib.util.spec_from_file_location(
        BOUNDED_EXECUTOR_NAME, BOUNDED_EXECUTOR_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {BOUNDED_EXECUTOR_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[BOUNDED_EXECUTOR_NAME] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def bounded():
    return _load_bounded()


def _runner_files(tmp_path):
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    runner = tmp_path / "synthetic_runner.py"
    runner.write_text(SYNTHETIC_RUNNER, encoding="utf-8")
    return runner, cwd


def _receipt_dir(tmp_path):
    receipts = tmp_path / "receipts"
    receipts.mkdir(mode=0o700)
    return receipts


def _make_expected(bounded, runner, cwd, *, exit_code=0, principal="agent:alice",
                   work_run_id="RUN-0001", commit_sha=REV_A, grant_ref="grant-1",
                   runner_identity="claude-code", action_key="act-1",
                   cycle_ref="cyc-1", input_text=None, env=None,
                   timeout=10, max_output_bytes=100000):
    fields = dict(
        cycle_ref=cycle_ref,
        action_key=action_key,
        work_item_id="SRV-0100",
        work_run_id=work_run_id,
        commit_sha=commit_sha,
        principal=principal,
        grant_ref=grant_ref,
        runner_identity=runner_identity,
        argv=(sys.executable, str(runner), str(exit_code)),
        cwd=str(cwd),
        input_text="",
        readonly=True,
        timeout=timeout,
        max_output_bytes=max_output_bytes,
        env={},
        finalize_command=FINALIZE_COMMAND,
    )
    draft = bounded.ExpectedDispatch(**fields)
    canonical = bounded.canonical_binding(draft)
    if input_text is None:
        input_text = canonical
    if env is None:
        env = {"SERVEROS_DISPATCH_BINDING": canonical}
    fields.update(input_text=input_text, env=env)
    return bounded.ExpectedDispatch(**fields)


def _invoke(expected, receipts):
    executor = _load_bounded_executor()
    return executor.invoke_once(
        key=expected.action_key,
        argv=list(expected.argv),
        cwd=Path(expected.cwd),
        receipts=receipts,
        input_text=expected.input_text,
        timeout=expected.timeout,
        readonly=expected.readonly,
        max_output_bytes=expected.max_output_bytes,
        env=dict(expected.env),
    )


def _work_authority(gate, tmp_path, expected, *, exit_code=0, verdict="PASS",
                    worker=None, revision=None, command=None, run_state=None,
                    item_id=None, run_id=None, tag="work-run"):
    artifact_dir = (tmp_path / tag).resolve()
    artifact_dir.mkdir(parents=True, exist_ok=True)
    locator = artifact_dir / "finalize.json"
    payload = {
        "command": command or expected.finalize_command,
        "cases": ["test_fleet_v1_verdict_evidence"],
        "exit_code": exit_code,
        "revision": revision or expected.commit_sha,
        "dirty": False,
        "verdict": verdict,
    }
    data = (json.dumps(payload, sort_keys=True) + "\n").encode()
    locator.write_bytes(data)
    item = item_id or expected.work_item_id
    run = run_id or expected.work_run_id
    run_row = {
        "item_id": item,
        "run_id": run,
        "worker": worker or expected.runner_identity,
        "state": run_state or (
            "SUCCEEDED" if verdict == "PASS" and exit_code == 0 else "FAILED"),
        "artifact_dir": str(artifact_dir),
    }
    evidence = {
        "item_id": item,
        "run_id": run,
        "kind": "finalize",
        "locator": str(locator),
        "sha256": hashlib.sha256(data).hexdigest(),
        "command": payload["command"],
        "cases_json": json.dumps(payload["cases"]),
        "exit_code": exit_code,
        "revision": payload["revision"],
        "dirty": 0,
        "verdict": verdict,
    }
    return gate.AuthoritySnapshot(
        run=run_row, finalize_evidence=(evidence,),
        artifacts={str(locator): data})


def _admission(bounded, expected, receipts, *, admitted_at="2026-01-01T00:00:00+00:00",
               anchor=True, phase="DISPATCHED", grant_ref=None,
               selected_item=None, wall_seconds=None, max_output_bytes=None):
    receipt = bounded.read_bounded_receipt(receipts, expected.action_key)
    assert receipt is not None
    confirmation = {
        "action_key": receipt.receipt["action_key"],
        "status": receipt.receipt["status"],
        "input_digest": receipt.receipt["input_digest"],
    }
    if anchor:
        value = bounded.receipt_anchor(receipt)
        if value is not None:
            confirmation["receipt_sha256"] = value
    return bounded.AdmissionSnapshot(
        cycle_ref=expected.cycle_ref,
        action_key=expected.action_key,
        grant_ref=grant_ref or expected.grant_ref,
        phase=phase,
        admitted_at=admitted_at,
        retired_at=None,
        reservation={
            "wall_seconds": wall_seconds if wall_seconds is not None else expected.timeout,
            "max_attempts": 1,
            "max_output_bytes": (
                max_output_bytes if max_output_bytes is not None
                else expected.max_output_bytes),
        },
        lease={
            "resource": "work:" + expected.work_item_id,
            "scope": "t10-synthetic",
            "holder": expected.runner_identity,
        },
        selection={
            "selected": [selected_item or expected.work_item_id],
            "selection_digest": "sel-1",
        },
        confirmations={"DISPATCHED": confirmation},
    )


def _grant(bounded, expected, *, revoked=False, delegated_by=None,
           grant_ref=None, valid_from="2020-01-01T00:00:00+00:00",
           expires_at="2099-01-01T00:00:00+00:00"):
    return bounded.GrantSnapshot(
        grant_ref=grant_ref or expected.grant_ref,
        delegated_by=delegated_by or expected.principal,
        valid_from=valid_from,
        expires_at=expires_at,
        revoked=revoked,
    )


def _produce(bounded, tmp_path, expected):
    receipts = _receipt_dir(tmp_path)
    _invoke(expected, receipts)
    receipt = bounded.read_bounded_receipt(receipts, expected.action_key)
    assert receipt is not None
    return receipts, receipt


def test_real_runner_exit_one_with_pass_text_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=1)
    receipts, receipt = _produce(bounded, tmp_path, expected)
    assert b"ALL TESTS PASSED" in receipt.stdout_bytes

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts),
        _grant(bounded, expected),
        receipt,
        _work_authority(gate, tmp_path, expected, exit_code=1, verdict="FAIL"),
        untrusted_child_report="ALL TESTS PASSED - SUCCESS",
    )

    assert decision.decision == bounded.DECISION_DENY
    assert "PROCESS_EXIT_NONZERO" in decision.reasons, decision.as_dict()
    assert decision.eligible_for_qa_review is False
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.child_report_ignored is True


def test_real_runner_exit_zero_with_exact_binding_is_eligible(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts, receipt = _produce(bounded, tmp_path, expected)

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts),
        _grant(bounded, expected),
        receipt,
        _work_authority(gate, tmp_path, expected, exit_code=0, verdict="PASS"),
        untrusted_child_report="ALL TESTS PASSED - SUCCESS",
    )

    assert decision.decision == bounded.DECISION_ELIGIBLE, decision.as_dict()
    assert decision.reasons == ()
    assert decision.eligible_for_qa_review is True
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.requires_independent_qa is True
    assert decision.run_state_advanced is False
    assert decision.qa_gate_advanced is False


def test_current_production_receipt_is_blocked_wiring(bounded, gate, tmp_path):
    """The real receipt is authentic, but the store never recorded what binds it.

    `mcp/continuation_mcp.py` writes the work-verb arguments as stdin and
    `runtime/cli.py::_dispatch_answer` records only action_key/status/
    input_digest. Neither the T10 binding nor a receipt-file anchor exists, so
    a real exit 0 is still not eligible.
    """
    runner, cwd = _runner_files(tmp_path)
    production_input = json.dumps(
        {"key": "act-1", "argv": [sys.executable, str(runner), "0"]},
        sort_keys=True)
    expected = _make_expected(
        bounded, runner, cwd, exit_code=0,
        input_text=production_input, env={})
    receipts, receipt = _produce(bounded, tmp_path, expected)

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts, anchor=False),
        _grant(bounded, expected),
        receipt,
        _work_authority(gate, tmp_path, expected, exit_code=0, verdict="PASS"),
        untrusted_child_report="SUCCESS",
    )

    assert decision.decision == bounded.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "MISSING_DISPATCH_BINDING" in decision.reasons
    assert "MISSING_RECEIPT_ANCHOR" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.parent_accepted is False


def test_missing_receipt_anchor_is_blocked_wiring(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts, receipt = _produce(bounded, tmp_path, expected)

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts, anchor=False),
        _grant(bounded, expected),
        receipt,
        _work_authority(gate, tmp_path, expected),
        untrusted_child_report="SUCCESS",
    )

    assert decision.decision == bounded.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "MISSING_RECEIPT_ANCHOR" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_forged_exit_zero_receipt_is_denied_by_the_receipt_anchor(
        bounded, gate, tmp_path):
    """Edit the real exit-1 receipt to say 0: the recorded anchor still says 1."""
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=1)
    receipts, receipt = _produce(bounded, tmp_path, expected)
    admission = _admission(bounded, expected, receipts)

    receipt_path = receipts / (expected.action_key + ".json")
    tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
    tampered["process_exit_code"] = 0
    receipt_path.write_text(
        json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    forged = bounded.read_bounded_receipt(receipts, expected.action_key)
    assert forged is not None

    # Work Engine is forged to agree with the tampered receipt. The anchor is
    # the only remaining disagreement, and it is enough.
    decision = bounded.evaluate_dispatch(
        expected,
        admission,
        _grant(bounded, expected),
        forged,
        _work_authority(gate, tmp_path, expected, exit_code=0, verdict="PASS"),
        untrusted_child_report="PASS",
    )

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "RECEIPT_ANCHOR_MISMATCH" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_nonzero_runner_exit_wins_over_forged_work_engine_zero(
        bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=1)
    receipts, receipt = _produce(bounded, tmp_path, expected)

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts),
        _grant(bounded, expected),
        receipt,
        _work_authority(gate, tmp_path, expected, exit_code=0, verdict="PASS"),
        untrusted_child_report="PASS exit 0",
    )

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "PROCESS_EXIT_NONZERO" in decision.reasons
    assert "EXIT_CODE_DISAGREEMENT" in decision.reasons


def test_output_digest_mismatch_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts, receipt = _produce(bounded, tmp_path, expected)
    admission = _admission(bounded, expected, receipts)

    stdout = receipts / (expected.action_key + ".stdout")
    stdout.write_bytes(stdout.read_bytes() + b"tampered\n")
    changed = bounded.read_bounded_receipt(receipts, expected.action_key)
    assert changed is not None

    decision = bounded.evaluate_dispatch(
        expected, admission, _grant(bounded, expected), changed,
        _work_authority(gate, tmp_path, expected),
        untrusted_child_report="PASS")

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "OUTPUT_DIGEST_MISMATCH" in decision.reasons


def test_receipt_not_exited_is_unknown(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts, receipt = _produce(bounded, tmp_path, expected)

    receipt_path = receipts / (expected.action_key + ".json")
    changed = json.loads(receipt_path.read_text(encoding="utf-8"))
    changed["status"] = "TIMED_OUT"
    receipt_path.write_text(
        json.dumps(changed, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reread = bounded.read_bounded_receipt(receipts, expected.action_key)
    assert reread is not None
    # The producer recorded the receipt as it now stands, so the anchor is not
    # the question here: the status is.
    admission = _admission(bounded, expected, receipts, anchor=True)

    decision = bounded.evaluate_dispatch(
        expected, admission, _grant(bounded, expected), reread,
        _work_authority(gate, tmp_path, expected),
        untrusted_child_report="SUCCESS")

    assert decision.decision == bounded.DECISION_UNKNOWN, decision.as_dict()
    assert "RECEIPT_NOT_EXITED" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_missing_receipt_is_unknown(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts = _receipt_dir(tmp_path)
    assert bounded.read_bounded_receipt(receipts, expected.action_key) is None

    decision = bounded.evaluate_dispatch(
        expected, None, _grant(bounded, expected), None,
        _work_authority(gate, tmp_path, expected),
        untrusted_child_report="SUCCESS")

    assert decision.decision == bounded.DECISION_UNKNOWN, decision.as_dict()
    assert "MISSING_RECEIPT" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_stale_receipt_predating_the_admission_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts, receipt = _produce(bounded, tmp_path, expected)

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts,
                   admitted_at="2099-01-01T00:00:00+00:00"),
        _grant(bounded, expected),
        receipt,
        _work_authority(gate, tmp_path, expected),
        untrusted_child_report="SUCCESS")

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "STALE_RECEIPT" in decision.reasons


def test_expired_grant_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts, receipt = _produce(bounded, tmp_path, expected)

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts),
        _grant(bounded, expected, expires_at="2020-01-01T00:00:00+00:00"),
        receipt,
        _work_authority(gate, tmp_path, expected),
        untrusted_child_report="SUCCESS")

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "EXPIRED_GRANT" in decision.reasons


def test_revoked_grant_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts, receipt = _produce(bounded, tmp_path, expected)

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts),
        _grant(bounded, expected, revoked=True),
        receipt,
        _work_authority(gate, tmp_path, expected),
        untrusted_child_report="SUCCESS")

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "GRANT_REVOKED" in decision.reasons


def _foreign_receipt_decision(bounded, gate, tmp_path, receipt_expected, expected):
    receipts = _receipt_dir(tmp_path)
    _invoke(receipt_expected, receipts)
    receipt = bounded.read_bounded_receipt(receipts, expected.action_key)
    assert receipt is not None
    return bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts),
        _grant(bounded, expected),
        receipt,
        _work_authority(gate, tmp_path, expected),
        untrusted_child_report="PASS")


def test_cross_principal_binding_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    receipt_expected = _make_expected(
        bounded, runner, cwd, exit_code=0, principal="agent:bob")
    expected = _make_expected(
        bounded, runner, cwd, exit_code=0, principal="agent:alice",
        input_text=receipt_expected.input_text, env=receipt_expected.env)

    decision = _foreign_receipt_decision(
        bounded, gate, tmp_path, receipt_expected, expected)

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "CROSS_PRINCIPAL" in decision.reasons


def test_cross_run_binding_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    receipt_expected = _make_expected(
        bounded, runner, cwd, exit_code=0, work_run_id="RUN-9999")
    expected = _make_expected(
        bounded, runner, cwd, exit_code=0, work_run_id="RUN-0001",
        input_text=receipt_expected.input_text, env=receipt_expected.env)

    decision = _foreign_receipt_decision(
        bounded, gate, tmp_path, receipt_expected, expected)

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "CROSS_RUN" in decision.reasons


def test_stale_sha_binding_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    receipt_expected = _make_expected(
        bounded, runner, cwd, exit_code=0, commit_sha=REV_B)
    expected = _make_expected(
        bounded, runner, cwd, exit_code=0, commit_sha=REV_A,
        input_text=receipt_expected.input_text, env=receipt_expected.env)

    decision = _foreign_receipt_decision(
        bounded, gate, tmp_path, receipt_expected, expected)

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "STALE_SHA" in decision.reasons


def test_cross_grant_binding_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    receipt_expected = _make_expected(
        bounded, runner, cwd, exit_code=0, grant_ref="grant-2")
    expected = _make_expected(
        bounded, runner, cwd, exit_code=0, grant_ref="grant-1",
        input_text=receipt_expected.input_text, env=receipt_expected.env)

    decision = _foreign_receipt_decision(
        bounded, gate, tmp_path, receipt_expected, expected)

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "CROSS_GRANT" in decision.reasons


def test_runner_identity_binding_mismatch_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    receipt_expected = _make_expected(
        bounded, runner, cwd, exit_code=0, runner_identity="codex")
    expected = _make_expected(
        bounded, runner, cwd, exit_code=0, runner_identity="claude-code",
        input_text=receipt_expected.input_text, env=receipt_expected.env)

    decision = _foreign_receipt_decision(
        bounded, gate, tmp_path, receipt_expected, expected)

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "RUNNER_IDENTITY_MISMATCH" in decision.reasons


def test_unrecognized_work_engine_runner_is_denied(bounded, gate, tmp_path):
    runner, cwd = _runner_files(tmp_path)
    expected = _make_expected(bounded, runner, cwd, exit_code=0)
    receipts, receipt = _produce(bounded, tmp_path, expected)

    decision = bounded.evaluate_dispatch(
        expected,
        _admission(bounded, expected, receipts),
        _grant(bounded, expected),
        receipt,
        _work_authority(gate, tmp_path, expected, worker="mystery-runner"),
        untrusted_child_report="SUCCESS")

    assert decision.decision == bounded.DECISION_DENY, decision.as_dict()
    assert "WORK_UNRECOGNIZED_RUNNER" in decision.reasons


def test_symlink_receipt_is_refused(bounded, tmp_path):
    receipts = _receipt_dir(tmp_path)
    real = receipts / "real.json"
    real.write_text("{}\n", encoding="utf-8")
    (receipts / "act-1.json").symlink_to(real)

    with pytest.raises(ValueError):
        bounded.read_bounded_receipt(receipts, "act-1")
