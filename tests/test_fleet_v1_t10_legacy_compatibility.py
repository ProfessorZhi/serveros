#!/usr/bin/env python3
"""#237 T10 P1 — legacy compatibility must never become V1 acceptance.

PUBLIC-SAFE X2 REWRITE (base rc/i1-v2-review-20261020 @ ec43f714)
----------------------------------------------------------------
The RC-v1 file extracted the owner-private Work Engine #15 archive with
`git archive` and therefore setup-errored on Dell/fjd, where that checkout does
not exist.  RC-v2 now ships PUBLIC `workengine/authority.py` (#304), so this
suite keeps the same negative assertions and proves the positive producer side
using only public in-tree code:

* the REAL in-tree `workengine.ServerOSStore` for the legacy `UNSIGNED`
  finalize and for `finalize_run(t10_required=True)`;
* the REAL in-tree `workengine.authority` dataclasses (`VerifiedRunReceipt`,
  `T10Binding`);
* the REAL in-tree `runtime.bounded_invocation.invoke_once` for a runner
  receipt;
* a LOCALLY CONTROLLED `RunnerReceiptAuthority`, labelled
  `PUBLIC_REIMPLEMENTATION_LOCAL`, which is not a production trusted parent.

The old owner-private #15 producer is labelled
`HISTORICAL_PRIVATE_SOURCE_UNVERIFIED`; it is never read, extracted or copied.
Pinned public fixtures remain for consumer-gate tests where the producer is not
the point.  See `integrations/fleet-v1-portability/README.md`.

WHAT THIS FILE STILL PINS
-------------------------
* `evaluate_acceptance` remains a clearly isolated LEGACY/UNSIGNED
  compatibility path: `v1_acceptable=False` / `v1_mode="LEGACY_COMPATIBILITY"`,
  and a V1 call with `v1_required=True` refuses (`V1_REQUIRES_STRICT_PATH`).
* The only V1 positive path is `evaluate_v1_acceptance`, which requires the
  strict signed finalization over the authoritative Work Engine exact Run and
  durable `finalize.json`, plus a grant/epoch freshness answer from a trusted
  parent.  No parent is `BLOCKED_WIRING`; stale/unknown/revoked/epoch-less is
  no acceptance.
* A clearly labelled **test-double** trusted parent around the pinned public
  readback is `ELIGIBLE_FOR_QA` only: `v1_acceptable=False`,
  `production_status="BLOCKED_TRUST_SOURCE"`, `parent_accepted=False`,
  `qa_signature=False`.
* Absent authorization marker, explicit legacy `UNSIGNED`, caller JSON fake
  `T10_VERIFIED`, missing durable evidence, tampered bytes, stale grant unknown,
  child text PASS with real exit 1, cross-run/actor/SHA, and an explicit
  `v1_required=False` all yield no acceptance.

Evidence level: LOCAL/HERMETIC.  Linux (Tokyo/Dell fjd) only; no product source
is changed and no production acceptance is claimed.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "v1_portability"))
import v1_portability_harness as H  # noqa: E402

VERDICT = H.VERDICT
FINAL = H.FINAL
V1 = H.V1
ITEM = H.ITEM
CONTEXT = H.CONTEXT
GRANT = H.GRANT
RUNNER = H.RUNNER
RUN = H.RUN
HEAD = H.HEAD

pytestmark = pytest.mark.skipif(
    sys.platform != "linux",
    reason="X2 evidence scope: Linux (Tokyo / Dell fjd) only")


def _pre15_snapshot(tmp_path: Path):
    artifact_dir = (tmp_path / "legacy-run").resolve()
    artifact_dir.mkdir(parents=True)
    locator = artifact_dir / "finalize.json"
    payload = {
        "command": "pytest",
        "cases": ["one"],
        "exit_code": 0,
        "revision": HEAD,
        "dirty": False,
        "verdict": "PASS",
        # Deliberately NO "authorization": this is the pre-#15 compatibility
        # fixture whose old behaviour must not become V1 acceptance.
    }
    data = (json.dumps(payload) + "\n").encode()
    locator.write_bytes(data)
    row = {
        "item_id": ITEM, "run_id": RUN, "kind": "finalize",
        "locator": str(locator),
        "sha256": hashlib.sha256(data).hexdigest(),
        "command": "pytest", "cases_json": json.dumps(["one"]),
        "exit_code": 0, "revision": HEAD, "dirty": 0, "verdict": "PASS",
    }
    run = {
        "item_id": ITEM, "run_id": RUN, "worker": RUNNER,
        "state": "SUCCEEDED", "artifact_dir": str(artifact_dir),
    }
    return VERDICT.AuthoritySnapshot(
        run=run, finalize_evidence=(row,), artifacts={str(locator): data})


def _pre15_expected():
    return VERDICT.ExpectedAcceptance(
        item_id=ITEM, run_id=RUN, revision=HEAD, principal=CONTEXT,
        command="pytest", recognized_runners={CONTEXT: (RUNNER,)})


# ---------------------------------------------------------------------------
# The legacy gate itself: compatible, and explicitly not V1.
# ---------------------------------------------------------------------------

def test_legacy_pre15_compat_cannot_affect_v1(tmp_path):
    snapshot = _pre15_snapshot(tmp_path)
    expected = _pre15_expected()

    legacy = VERDICT.evaluate_acceptance(expected, snapshot)
    assert legacy.decision == VERDICT.DECISION_ELIGIBLE
    assert legacy.v1_acceptable is False
    assert legacy.v1_mode == "LEGACY_COMPATIBILITY"

    refused = VERDICT.evaluate_acceptance(expected, snapshot, v1_required=True)
    assert refused.decision == VERDICT.DECISION_DENY, refused.as_dict()
    assert "V1_REQUIRES_STRICT_PATH" in refused.reasons
    assert refused.v1_required is True
    assert refused.v1_acceptable is False
    assert refused.v1_mode == "V1_REFUSED_LEGACY_PATH"
    assert refused.parent_accepted is False
    assert refused.qa_signature is False


def test_in_tree_legacy_finalize_is_unsigned_and_not_v1(tmp_path):
    """The REAL in-tree workengine writes a durable legacy `UNSIGNED` record."""
    sc = H.legacy_scenario(tmp_path)
    assert sc.result["state"] == "SUCCEEDED"
    assert sc.result["authorization"] == "UNSIGNED"
    assert sc.result["t10_acceptable"] is False
    payload = json.loads(sc.readback.evidence_bytes)
    assert payload["authorization"] == "UNSIGNED"
    assert "runner_receipt" not in payload
    assert sc.readback.run_state == "SUCCEEDED"
    assert sc.readback.authorization == "UNSIGNED"
    assert sc.readback.t10_acceptable is False
    assert sc.readback.evidence_verified is True

    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "UNSIGNED_FINALIZATION" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_pinned_unsigned_label_is_denied_by_v1(tmp_path):
    """The explicit legacy label is denied even with a test-double grant."""
    sc = H.public_readback(tmp_path, fixture_name=H.UNSIGNED_FIXTURE)
    assert sc.readback.authorization == "UNSIGNED"
    assert sc.readback.t10_acceptable is False

    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "UNSIGNED_FINALIZATION" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


# ---------------------------------------------------------------------------
# The trusted-parent boundary.
# ---------------------------------------------------------------------------

def test_v1_missing_trusted_parent_is_blocked_wiring(tmp_path):
    sc = H.public_readback(tmp_path)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=None, expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "NO_TRUSTED_PARENT_FOR_GRANT_EPOCH" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_v1_test_double_grant_is_eligible_for_qa_only(tmp_path):
    sc = H.public_readback(tmp_path)
    assert sc.readback.source == H.FIXTURE_SOURCE
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_ELIGIBLE_FOR_QA, decision.as_dict()
    assert decision.eligible_for_qa_review is True
    assert decision.v1_acceptable is False
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.requires_independent_qa is True
    assert decision.t10_status == "T10_VERIFIED"
    assert decision.production_status == "BLOCKED_TRUST_SOURCE"
    assert decision.trusted_parent == "TEST_DOUBLE"


def test_v1_stale_or_unknown_grant_is_not_accepted(tmp_path):
    sc = H.public_readback(tmp_path)
    for changed, code in (
        ({"fresh": False}, "STALE_GRANT_UNKNOWN"),
        ({"verified": False}, "GRANT_FRESHNESS_UNKNOWN"),
        ({"epoch": None}, "GRANT_EPOCH_UNKNOWN"),
        ({"revoked": True}, "GRANT_REVOKED"),
    ):
        grant = H.grant_snapshot(**changed)
        decision = V1.evaluate_v1_acceptance(
            sc.expected, sc.readback, grant=grant,
            expected_grant_ref=GRANT)
        assert decision.decision != V1.DECISION_ELIGIBLE_FOR_QA, decision.as_dict()
        assert code in decision.reasons, decision.as_dict()
        assert decision.eligible_for_qa_review is False
        assert decision.v1_acceptable is False


def test_v1_cross_grant_actor_or_run_is_denied(tmp_path):
    sc = H.public_readback(tmp_path)
    grant = H.grant_snapshot()
    for changed_grant, code in (
        ({"principal": "ctx-other"}, "ACTOR_MISMATCH"),
        ({"grant_ref": "grant-2"}, "GRANT_MISMATCH"),
    ):
        decision = V1.evaluate_v1_acceptance(
            sc.expected, sc.readback,
            grant=H.grant_snapshot(**changed_grant),
            expected_grant_ref=GRANT)
        assert decision.decision == V1.DECISION_DENY, decision.as_dict()
        assert code in decision.reasons, decision.as_dict()

    for changed, code in (
        ({"run_ref": "RUN-9999"}, "RUN_MISMATCH"),
        ({"actor_ref": "ctx-other"}, "ACTOR_MISMATCH"),
        ({"commit_sha": "f" * 40}, "COMMIT_MISMATCH"),
    ):
        expected = dataclasses.replace(sc.expected, **changed)
        decision = V1.evaluate_v1_acceptance(
            expected, sc.readback, grant=grant,
            expected_grant_ref=GRANT)
        assert decision.decision == V1.DECISION_DENY, decision.as_dict()
        assert code in decision.reasons, decision.as_dict()


def test_v1_requires_explicit_context(tmp_path):
    sc = H.public_readback(tmp_path)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT, v1_required=False)
    assert decision.decision == V1.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "V1_CONTEXT_REQUIRED" in decision.reasons
    assert decision.eligible_for_qa_review is False


# ---------------------------------------------------------------------------
# The durable readback: caller JSON, absence, tampering, real exit 1.
# ---------------------------------------------------------------------------

def test_v1_absent_authorization_marker_is_denied(tmp_path):
    sc = H.public_readback(tmp_path)
    absent = dataclasses.replace(sc.readback, authorization=None)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, absent, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert ("AUTHORIZATION_NOT_T10_VERIFIED" in decision.reasons
            or "UNSIGNED_FINALIZATION" in decision.reasons)
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_v1_caller_json_fake_label_is_denied(tmp_path):
    sc = H.public_readback(tmp_path)
    fabricated = {
        "state": "SUCCEEDED",
        "authorization": "T10_VERIFIED",
        "t10_acceptable": True,
        "runner_receipt": {"process_exit_code": 0},
    }
    decision = V1.evaluate_v1_acceptance(
        sc.expected, fabricated, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "CALLER_JSON_NOT_AUTHORITY" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_v1_caller_json_grant_is_denied(tmp_path):
    sc = H.public_readback(tmp_path)
    fabricated = {
        "grant_ref": GRANT, "principal": CONTEXT, "verified": True,
        "fresh": True, "revoked": False, "epoch": 1,
        "verified_by": "test-double:t10-local-parent",
        "checked_at": H.now_iso(),
    }
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=fabricated,
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "CALLER_JSON_NOT_AUTHORITY" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_v1_missing_durable_evidence_is_blocked(tmp_path):
    sc = H.public_readback(tmp_path)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, None, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_v1_tampered_bytes_is_denied(tmp_path):
    sc = H.legacy_scenario(tmp_path)
    sc.finalize_path.write_bytes(sc.finalize_path.read_bytes() + b" ")
    tampered = FINAL.read_work_engine_finalization(
        sc.store.db_path, sc.item.id, sc.run.id)
    assert tampered.evidence_verified is False

    decision = V1.evaluate_v1_acceptance(
        sc.expected, tampered, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "EVIDENCE_TAMPERED" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_v1_child_text_pass_real_exit_one_is_denied(tmp_path):
    """A legacy finalize claims PASS/0; the real child stdout says PASS but
    exits 1, and the independent receipt is what the consumer must believe."""
    sc = H.legacy_scenario(tmp_path)
    receipt = H.bounded_receipt(tmp_path, exit_code=1)
    assert receipt.record["process_exit_code"] == 1
    assert receipt.anchor is not None
    assert b"ALL TESTS PASSED" in receipt.stdout
    assert sc.readback.verdict == "PASS"
    assert sc.readback.exit_code == 0

    expected = dataclasses.replace(
        sc.expected,
        action_key=receipt.key,
        input_digest=receipt.input_digest,
        receipt_sha256=receipt.anchor,
        process_exit_code=1,
    )
    decision = V1.evaluate_v1_acceptance(
        expected, sc.readback, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "EXPECTED_EXIT_NOT_ZERO" in decision.reasons, decision.as_dict()
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


# ---------------------------------------------------------------------------
# Public in-tree T10 producer conformance (PUBLIC_REIMPLEMENTATION_LOCAL).
#
# The dataclasses and the store path are the real public ones. The only local
# piece is the `RunnerReceiptAuthority` implementation; its label says so, and
# the consumer gate still refuses to turn it into production acceptance.
# ---------------------------------------------------------------------------

def test_public_t10_producer_writes_an_exact_t10_verified_finalize(tmp_path):
    sc = H.t10_scenario(tmp_path)
    assert sc.producer.EVIDENCE_LABEL == H.PUBLIC_REIMPLEMENTATION_LOCAL
    assert sc.result["state"] == "SUCCEEDED"
    assert sc.result["authorization"] == "T10_VERIFIED"
    assert sc.result["t10_acceptable"] is True
    assert sc.readback.run_state == "SUCCEEDED"
    assert sc.readback.authorization == "T10_VERIFIED"
    assert sc.readback.t10_acceptable is True
    assert sc.readback.evidence_verified is True
    receipt = sc.readback.runner_receipt
    assert receipt["action_key"] == sc.receipt.key
    assert receipt["input_digest"] == sc.receipt.input_digest
    assert receipt["receipt_sha256"] == sc.receipt.anchor
    assert receipt["process_exit_code"] == 0
    assert receipt["runner_identity"] == H.RUNNER
    assert receipt["commit_sha"] == H.HEAD
    assert receipt["actor_ref"] == H.CONTEXT

    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=H.grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_ELIGIBLE_FOR_QA, decision.as_dict()
    assert decision.eligible_for_qa_review is True
    assert decision.v1_acceptable is False
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
    assert decision.requires_independent_qa is True
    assert decision.t10_status == "T10_VERIFIED"
    assert decision.production_status == "BLOCKED_TRUST_SOURCE"
    assert decision.trusted_parent == "TEST_DOUBLE"

    # The same REAL readback still refuses a stale or revoked grant.
    for changed, code in (
        ({"fresh": False}, "STALE_GRANT_UNKNOWN"),
        ({"revoked": True}, "GRANT_REVOKED"),
    ):
        bad = V1.evaluate_v1_acceptance(
            sc.expected, sc.readback, grant=H.grant_snapshot(**changed),
            expected_grant_ref=GRANT)
        assert bad.decision != V1.DECISION_ELIGIBLE_FOR_QA, bad.as_dict()
        assert code in bad.reasons, bad.as_dict()
        assert bad.v1_acceptable is False


def test_public_t10_producer_without_a_parent_is_blocked(tmp_path):
    inputs = H.t10_inputs(tmp_path)
    with pytest.raises(inputs.authority.ReceiptError) as exc:
        inputs.store.finalize_run(
            inputs.item.id, inputs.run.id,
            verdict="PASS", exit_code=0, command="pytest", cases=["one"],
            revision=H.HEAD, dirty=False, t10_required=True,
            t10_binding=inputs.binding, receipt_ref=inputs.receipt_ref,
            receipt_authority=None)
    assert exc.value.reason_code == "BLOCKED_TRUST_SOURCE"
    run = inputs.store.get_run(inputs.item.id, inputs.run.id)
    assert run.state.value == "FINALIZING"


def test_public_t10_binding_mismatch_is_refused(tmp_path):
    for index, (changed, code) in enumerate((
        ({"commit_sha": "f" * 40}, "T10_COMMIT_MISMATCH"),
        ({"actor_ref": "ctx-other"}, "T10_ACTOR_MISMATCH"),
        ({"run_ref": "RUN-9999"}, "T10_RUN_MISMATCH"),
    )):
        inputs = H.t10_inputs(
            tmp_path, binding_overrides=changed,
            root_name=f"t10-binding-{index}")
        producer = H.local_receipt_authority(inputs)
        with pytest.raises(inputs.authority.ReceiptError) as exc:
            inputs.store.finalize_run(
                inputs.item.id, inputs.run.id,
                verdict="PASS", exit_code=0, command="pytest", cases=["one"],
                revision=H.HEAD, dirty=False, t10_required=True,
                t10_binding=inputs.binding, receipt_ref=inputs.receipt_ref,
                receipt_authority=producer)
        assert exc.value.reason_code == code, exc.value


def test_public_t10_receipt_exit_disagreement_is_refused(tmp_path):
    inputs = H.t10_inputs(tmp_path, exit_code=1)
    assert inputs.verified.process_exit_code == 1
    producer = H.local_receipt_authority(inputs)
    with pytest.raises(inputs.authority.ReceiptError) as exc:
        inputs.store.finalize_run(
            inputs.item.id, inputs.run.id,
            verdict="PASS", exit_code=0, command="pytest", cases=["one"],
            revision=H.HEAD, dirty=False, t10_required=True,
            t10_binding=inputs.binding, receipt_ref=inputs.receipt_ref,
            receipt_authority=producer)
    assert exc.value.reason_code == "EXIT_CODE_DISAGREEMENT"


def test_public_t10_trusted_parent_label_is_not_acceptance(tmp_path):
    """A locally forged `trusted-parent:` string still cannot accept."""
    sc = H.t10_scenario(tmp_path)
    forged = H.grant_snapshot(verified_by="trusted-parent:local-forgery")
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=forged, expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "V1_ACCEPTANCE_NOT_WIRED" in decision.reasons
    assert decision.trusted_parent == "UNVERIFIED"
    assert decision.v1_acceptable is False
    assert decision.parent_accepted is False
    assert decision.qa_signature is False
