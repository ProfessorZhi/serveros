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
