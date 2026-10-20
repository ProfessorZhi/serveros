#!/usr/bin/env python3
"""#237 T10 P1 — legacy compatibility must never become V1 acceptance.

THE DEFECT
----------
`verdict_evidence._check_signed_finalization_artifact` deliberately returns on
`authorization is None` so pre-#15 fixtures keep passing. That is a LEGACY
mode. If a future V1 path is wired through the same function, an unsigned
record with no marker would be accepted as V1 evidence.

WHAT THIS FILE PINS
-------------------
* `evaluate_acceptance` remains a clearly isolated LEGACY/UNSIGNED
  compatibility path: its decision says `v1_acceptable=False` /
  `v1_mode="LEGACY_COMPATIBILITY"`, and a V1 call with `v1_required=True`
  refuses (`V1_REQUIRES_STRICT_PATH`).
* The only V1 positive path is `evaluate_v1_acceptance`, which requires:
  - the strict signed finalization gate (`finalization_result`) over the
    authoritative Work Engine exact Run and durable `finalize.json`;
  - a grant/epoch freshness answer from a trusted parent. No parent means
    `BLOCKED_WIRING`; a stale/unknown/revoked grant means no acceptance; the
    consumer never fabricates a grant verdict.
* A clearly labelled **test-double** trusted parent around the real offline
  exit-0 receipt is `ELIGIBLE_FOR_QA` only: `v1_acceptable=False`,
  `production_status="BLOCKED_TRUST_SOURCE"`, `parent_accepted=False`,
  `qa_signature=False`.
* Absent authorization marker, legacy `SUCCEEDED`/`UNSIGNED`, caller JSON fake
  `T10_VERIFIED`, missing durable evidence, tampered bytes, stale grant unknown,
  child text PASS with real exit 1, cross-run/actor/SHA → no acceptance.

The real Work Engine #15 source is extracted read-only; the #274 signed
finalization test helpers are reused for the real `finalize_run` and the real
offline `invoke_once` receipt. Evidence level: LOCAL/HERMETIC, never production.
"""
from __future__ import annotations

import dataclasses
import datetime
import hashlib
import importlib.util
import json
import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
WE_REPO = Path("/data/home/vince/workspace/src/work-engine")
WE_HEAD = "4bd9e8a1c6a6a1b24c1717ec65862c8900d5f7be"
FINAL_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "finalization_result.py")
V1_PATH = (
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "v1_acceptance.py")
PRIOR_PATH = HERE / "test_fleet_v1_t10_signed_finalization.py"

ITEM = "SRV-0001"
CONTEXT = "ctx-delegated-1"
GRANT = "grant-1"
RUNNER = "claude-code"
RUN = "RUN-0001"
HEAD = "0123456789abcdef0123456789abcdef01234567"


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


VERDICT = _load(
    "serveros_fleet_v1_verdict_evidence",
    REPO / "integrations" / "fleet-v1-verdict-evidence" / "verdict_evidence.py")
FINAL = _load("serveros_fleet_v1_signed_finalization", FINAL_PATH)
V1 = _load("serveros_fleet_v1_v1_acceptance", V1_PATH)
PRIOR = _load("test_fleet_v1_t10_signed_finalization", PRIOR_PATH)


@pytest.fixture(scope="session")
def we15(tmp_path_factory):
    dest = tmp_path_factory.mktemp("we15_legacy")
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
    import workengine  # noqa: F401
    from workengine import WorkItem
    from workengine.authority import ReceiptError, T10Binding, VerifiedRunReceipt
    from workengine.model import Activity, ContextMode, ExecutionContext, RunState
    from workengine.store import ServerOSStore
    return SimpleNamespace(
        WorkItem=WorkItem, Activity=Activity, ContextMode=ContextMode,
        ExecutionContext=ExecutionContext, RunState=RunState,
        ServerOSStore=ServerOSStore, VerifiedRunReceipt=VerifiedRunReceipt,
        ReceiptError=ReceiptError, T10Binding=T10Binding)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _grant_snapshot(**over):
    values = dict(
        grant_ref=GRANT,
        principal=CONTEXT,
        verified=True,
        fresh=True,
        revoked=False,
        epoch=1,
        verified_by="test-double:t10-local-parent",
        checked_at=_now(),
    )
    values.update(over)
    return V1.GrantFreshnessSnapshot(**values)


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


def test_legacy_pre15_compat_cannot_affect_v1(tmp_path):
    snapshot = _pre15_snapshot(tmp_path)
    expected = _pre15_expected()

    legacy = VERDICT.evaluate_acceptance(expected, snapshot)
    # The historical pre-#15 fixture still passes its own receipt-vs-prose
    # tests, but the decision says it is not V1 evidence.
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


def test_v1_missing_trusted_parent_is_blocked_wiring(
        monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=None, expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "NO_TRUSTED_PARENT_FOR_GRANT_EPOCH" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_v1_test_double_grant_is_eligible_for_qa_only(
        monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=_grant_snapshot(),
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


def test_v1_absent_authorization_marker_is_denied(
        monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    absent = dataclasses.replace(sc.readback, authorization=None)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, absent, grant=_grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert ("AUTHORIZATION_NOT_T10_VERIFIED" in decision.reasons
            or "UNSIGNED_FINALIZATION" in decision.reasons)
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_v1_legacy_unsigned_succeeded_is_denied(
        monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=False)
    assert sc.result["state"] == "SUCCEEDED"
    assert sc.result["authorization"] == "UNSIGNED"
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=_grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "UNSIGNED_FINALIZATION" in decision.reasons
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_v1_caller_json_fake_label_is_denied(
        monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    fabricated = {
        "state": "SUCCEEDED",
        "authorization": "T10_VERIFIED",
        "t10_acceptable": True,
        "runner_receipt": {"process_exit_code": 0},
    }
    decision = V1.evaluate_v1_acceptance(
        sc.expected, fabricated, grant=_grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "CALLER_JSON_NOT_AUTHORITY" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_v1_caller_json_grant_is_denied(monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    fabricated = {
        "grant_ref": GRANT, "principal": CONTEXT, "verified": True,
        "fresh": True, "revoked": False, "epoch": 1,
        "verified_by": "test-double:t10-local-parent", "checked_at": _now(),
    }
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=fabricated,
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "CALLER_JSON_NOT_AUTHORITY" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_v1_missing_durable_evidence_is_blocked(
        monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, None, grant=_grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_v1_tampered_bytes_is_denied(monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    path = Path(sc.readback.evidence_locator)
    path.write_bytes(path.read_bytes() + b" ")
    tampered = FINAL.read_work_engine_finalization(
        sc.store.db_path, sc.item.id, sc.run.id)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, tampered, grant=_grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert "EVIDENCE_TAMPERED" in decision.reasons
    assert decision.eligible_for_qa_review is False


def test_v1_stale_or_unknown_grant_is_not_accepted(
        monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    for changed, code in (
        ({"fresh": False}, "STALE_GRANT_UNKNOWN"),
        ({"verified": False}, "GRANT_FRESHNESS_UNKNOWN"),
        ({"epoch": None}, "GRANT_EPOCH_UNKNOWN"),
        ({"revoked": True}, "GRANT_REVOKED"),
    ):
        grant = _grant_snapshot(**changed)
        decision = V1.evaluate_v1_acceptance(
            sc.expected, sc.readback, grant=grant,
            expected_grant_ref=GRANT)
        assert decision.decision != V1.DECISION_ELIGIBLE_FOR_QA, decision.as_dict()
        assert code in decision.reasons, decision.as_dict()
        assert decision.eligible_for_qa_review is False
        assert decision.v1_acceptable is False


def test_v1_cross_grant_actor_or_run_is_denied(
        monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    grant = _grant_snapshot()
    for changed_grant, code in (
        ({"principal": "ctx-other"}, "ACTOR_MISMATCH"),
        ({"grant_ref": "grant-2"}, "GRANT_MISMATCH"),
    ):
        decision = V1.evaluate_v1_acceptance(
            sc.expected, sc.readback,
            grant=_grant_snapshot(**changed_grant),
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


def test_v1_child_text_pass_real_exit_one_is_denied(monkeypatch, we15, tmp_path):
    tree, head = PRIOR._git_tree(tmp_path)
    store, item, run = PRIOR._we_store(we15, tmp_path, tree, head)
    fx = PRIOR._execute(monkeypatch, tmp_path, store, tree, exit_code=1)
    assert fx.result["receipt"]["process_exit_code"] == 1
    assert b"ALL TESTS PASSED" in (
        Path(fx.result["receipt_path"]).parent
        / (fx.result["receipt"]["action_key"] + ".stdout")).read_bytes()

    # A legacy finalize writes the caller's PASS/0 as SUCCEEDED/UNSIGNED while
    # the real runner receipt says exit 1. The V1 gate must not accept it.
    result = store.finalize_run(
        item.id, run.id, verdict="PASS", exit_code=0,
        command="pytest", cases=["one"], revision=head, dirty=False)
    assert result["state"] == "SUCCEEDED"
    assert result["authorization"] == "UNSIGNED"
    readback = FINAL.read_work_engine_finalization(
        store.db_path, item.id, run.id)
    expected_cont = PRIOR._expected_continuation(
        fx, run.id, head, exit_code=1)
    expected = FINAL.ExpectedFinalization(
        item_ref=item.id, run_ref=run.id, commit_sha=head,
        actor_ref=CONTEXT, action_key=expected_cont.action_key,
        input_digest=fx.result["receipt"]["input_digest"],
        receipt_sha256=fx.result["receipt_sha256"],
        process_exit_code=1, runner_identity=RUNNER)
    decision = V1.evaluate_v1_acceptance(
        expected, readback, grant=_grant_snapshot(),
        expected_grant_ref=GRANT)
    assert decision.decision == V1.DECISION_DENY, decision.as_dict()
    assert decision.eligible_for_qa_review is False
    assert decision.v1_acceptable is False


def test_v1_requires_explicit_context(monkeypatch, we15, tmp_path):
    sc = PRIOR._scenario(monkeypatch, FINAL, we15, tmp_path, t10=True)
    decision = V1.evaluate_v1_acceptance(
        sc.expected, sc.readback, grant=_grant_snapshot(),
        expected_grant_ref=GRANT, v1_required=False)
    assert decision.decision == V1.DECISION_BLOCKED_WIRING, decision.as_dict()
    assert "V1_CONTEXT_REQUIRED" in decision.reasons
    assert decision.eligible_for_qa_review is False
