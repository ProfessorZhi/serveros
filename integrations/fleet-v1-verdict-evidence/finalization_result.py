#!/usr/bin/env python3
"""#237 T10 P0 — downstream gate over a Work Engine signed finalization.

THE DEFECT
----------
A consumer that reads only `runs.state == SUCCEEDED` accepts a legacy finalize
whose own durable record says `authorization: "UNSIGNED"` /
`t10_acceptable: false`. Work Engine #15 (`4bd9e8a`) labels the two states, but
a label is useful only if the consumer reads it. This module is that consumer.

WHAT IT READS
-------------
`read_work_engine_finalization(state_db, item_ref, run_ref)` opens the Work
Engine `serveros.db` read-only and reads:

* the exact `runs` row (state, worker, artifact_dir);
* the last `kind='finalize'` `evidence` row;
* the `finalize.json` at the row's locator, which must be
  `<artifact_dir>/finalize.json`, must not be a symlink, and whose bytes must
  hash to the evidence row's `sha256`.

The durable file carries `authorization` (`UNSIGNED` or `T10_VERIFIED`) and,
when signed, the `runner_receipt` block (`action_key`, `input_digest`,
`receipt_sha256`, `process_exit_code`, `runner_identity`, `commit_sha`,
`actor_ref`). `t10_acceptable` is derived exactly as Work Engine derives it:
`authorization == "T10_VERIFIED"` and a `runner_receipt` is present. A caller
JSON object, a path, or a child's text is never this readback.

THE GATE
--------
`evaluate_signed_finalization(expected, readback)` refuses T10 success unless
**both** `T10_VERIFIED` and `t10_acceptable=true` are present in the durable
readback, the evidence bytes still hash to the evidence row, the run state is
`SUCCEEDED`, and the exact Item/Run/commit/actor/action/input digest/receipt
anchor/runner identity match the parent's expected binding. A non-zero or
missing `process_exit_code` is never accepted. Any mismatch is `DENY`; an
unreadable or absent authoritative readback is `BLOCKED_TRUST_SOURCE`.

WHAT THIS DOES NOT CLAIM
------------------------
The production `RunnerReceiptAuthority` / OS-attested parent is still unbound.
An `ELIGIBLE` decision is for independent QA only, with
`production_status="BLOCKED_TRUST_SOURCE"`. This module does not create a
signature, a second ledger, or a production acceptance.
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DECISION_DENY = "DENY"
DECISION_BLOCKED = "BLOCKED_TRUST_SOURCE"
DECISION_ELIGIBLE = "ELIGIBLE"

# reason codes
CALLER_JSON_NOT_AUTHORITY = "CALLER_JSON_NOT_AUTHORITY"
EVIDENCE_TAMPERED = "EVIDENCE_TAMPERED"
AUTHORIZATION_NOT_T10_VERIFIED = "AUTHORIZATION_NOT_T10_VERIFIED"
UNSIGNED_FINALIZATION = "UNSIGNED_FINALIZATION"
T10_NOT_ACCEPTABLE = "T10_NOT_ACCEPTABLE"
RUN_NOT_SUCCEEDED = "RUN_NOT_SUCCEEDED"
EXPECTED_EXIT_NOT_ZERO = "EXPECTED_EXIT_NOT_ZERO"
NO_VERIFIED_ZERO_EXIT = "NO_VERIFIED_ZERO_EXIT"
MISSING_SOURCE_PROOF = "MISSING_SOURCE_PROOF"
ITEM_MISMATCH = "ITEM_MISMATCH"
RUN_MISMATCH = "RUN_MISMATCH"
COMMIT_MISMATCH = "COMMIT_MISMATCH"
ACTOR_MISMATCH = "ACTOR_MISMATCH"
RUNNER_MISMATCH = "RUNNER_MISMATCH"
ACTION_MISMATCH = "ACTION_MISMATCH"
INPUT_DIGEST_MISMATCH = "INPUT_DIGEST_MISMATCH"
RECEIPT_ANCHOR_MISMATCH = "RECEIPT_ANCHOR_MISMATCH"
VERDICT_MISMATCH = "VERDICT_MISMATCH"
EXIT_CODE_MISMATCH = "EXIT_CODE_MISMATCH"
REVISION_MISMATCH = "REVISION_MISMATCH"
DIRTY_RECORD = "DIRTY_RECORD"
LOCATOR_MISMATCH = "LOCATOR_MISMATCH"
READBACK_CONTRADICTION = "READBACK_CONTRADICTION"
INVALID_READBACK = "INVALID_READBACK"
BLOCKED_TRUST_SOURCE = "BLOCKED_TRUST_SOURCE"

_TERMINAL_RUN_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "INVALID"})
_HEX40 = re.compile(r"[0-9a-fA-F]{40}")
_HEX64 = re.compile(r"[0-9a-fA-F]{64}")

_BECAUSE = {
    CALLER_JSON_NOT_AUTHORITY: "a caller JSON label is not the authoritative Work Engine record",
    EVIDENCE_TAMPERED: "the finalize evidence bytes do not match the evidence row's sha256",
    AUTHORIZATION_NOT_T10_VERIFIED: "the durable finalization is not labelled T10_VERIFIED",
    UNSIGNED_FINALIZATION: "the durable finalization is labelled UNSIGNED",
    T10_NOT_ACCEPTABLE: "the finalization is not t10_acceptable",
    RUN_NOT_SUCCEEDED: "the Work Engine Run is not SUCCEEDED",
    EXPECTED_EXIT_NOT_ZERO: "the parent expected a nonzero process exit, which cannot be T10 success",
    NO_VERIFIED_ZERO_EXIT: "the finalization has no verified process_exit_code == 0",
    MISSING_SOURCE_PROOF: "the finalization carries no runner_receipt proof",
    ITEM_MISMATCH: "the finalization is for another Item",
    RUN_MISMATCH: "the finalization is for another Run",
    COMMIT_MISMATCH: "the finalization is for another commit SHA",
    ACTOR_MISMATCH: "the finalization was verified for another actor",
    RUNNER_MISMATCH: "the Run worker is not the expected runner identity",
    ACTION_MISMATCH: "the runner receipt names another action",
    INPUT_DIGEST_MISMATCH: "the runner receipt input_digest is not the expected one",
    RECEIPT_ANCHOR_MISMATCH: "the runner receipt anchor is not the expected one",
    VERDICT_MISMATCH: "the finalization verdict is not PASS",
    EXIT_CODE_MISMATCH: "the finalization exit_code is not 0",
    REVISION_MISMATCH: "the finalization revision is not the expected SHA",
    DIRTY_RECORD: "the finalization record is dirty",
    LOCATOR_MISMATCH: "the evidence locator is not the Run's finalize.json",
    READBACK_CONTRADICTION: "the readback fields disagree with the durable evidence file",
    INVALID_READBACK: "the readback is not a usable Work Engine record",
    BLOCKED_TRUST_SOURCE: "no authenticated Work Engine finalization readback is available",
}
_ELIGIBLE_BECAUSE = (
    "the durable Work Engine finalization is T10_VERIFIED and t10_acceptable, "
    "the evidence bytes still match the evidence row, and the runner receipt "
    "matches the exact Item/Run/commit/actor with a verified exit 0; this is "
    "eligibility for independent QA review, not a production acceptance")


class FinalizationReadbackError(ValueError):
    """The authoritative store/evidence cannot be read; fail closed."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


@dataclass(frozen=True)
class ExpectedFinalization:
    item_ref: str
    run_ref: str
    commit_sha: str
    actor_ref: str
    action_key: str
    input_digest: str
    receipt_sha256: str
    process_exit_code: int
    runner_identity: str


@dataclass(frozen=True)
class WorkEngineFinalizeReadback:
    item_ref: str
    run_ref: str
    run_state: str
    run_worker: str
    artifact_dir: str
    evidence_locator: str
    evidence_sha256: str
    evidence_bytes: bytes
    evidence_verified: bool
    authorization: str | None
    t10_acceptable: bool | None
    verdict: str | None
    exit_code: int | None
    revision: str | None
    dirty: bool | int | None
    runner_receipt: Mapping[str, Any] | None
    source: str = "work-engine:runs+evidence"
    evidence_level: str = "LOCAL_HERMETIC"


@dataclass(frozen=True)
class FinalizationDecision:
    decision: str
    reasons: tuple[str, ...]
    because: str
    expected: Mapping[str, Any]
    readback: Mapping[str, Any] | None
    eligible_for_qa_review: bool
    parent_accepted: bool
    qa_signature: bool
    requires_independent_qa: bool
    t10_status: str
    production_status: str
    evidence_level: str
    authority: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "because": self.because,
            "expected": dict(self.expected),
            "readback": dict(self.readback) if self.readback is not None else None,
            "eligible_for_qa_review": self.eligible_for_qa_review,
            "parent_accepted": self.parent_accepted,
            "qa_signature": self.qa_signature,
            "requires_independent_qa": self.requires_independent_qa,
            "t10_status": self.t10_status,
            "production_status": self.production_status,
            "evidence_level": self.evidence_level,
            "authority": self.authority,
        }


def read_work_engine_finalization(state_db, item_ref: str,
                                  run_ref: str) -> WorkEngineFinalizeReadback:
    """Read the authoritative Run + finalize evidence, read-only.

    The locator comes from the evidence row, never from the caller. It must be
    the Run's own `finalize.json`, not a symlink, and its bytes must be
    available. A missing store/row/file raises `FinalizationReadbackError`
    (`BLOCKED_TRUST_SOURCE`); a file whose bytes do not match the evidence row
    is returned with `evidence_verified=False` so the gate can DENY tampering
    explicitly.
    """
    path = Path(str(state_db))
    if not path.is_absolute() or not path.is_file():
        raise FinalizationReadbackError(
            BLOCKED_TRUST_SOURCE,
            f"Work Engine store {str(state_db)!r} is not a readable absolute file")
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    except sqlite3.Error as exc:
        raise FinalizationReadbackError(
            BLOCKED_TRUST_SOURCE,
            f"Work Engine store {path} cannot be opened read-only ({exc})") from exc
    try:
        conn.row_factory = sqlite3.Row
        run = conn.execute(
            "SELECT item_id, run_id, state, worker, artifact_dir FROM runs "
            "WHERE item_id=? AND run_id=?", (item_ref, run_ref)).fetchone()
        if run is None:
            raise FinalizationReadbackError(
                BLOCKED_TRUST_SOURCE,
                f"no Work Engine Run {item_ref}/{run_ref}")
        evidence = conn.execute(
            "SELECT item_id, run_id, kind, locator, sha256, verdict, exit_code, "
            "revision, dirty FROM evidence WHERE item_id=? AND run_id=? "
            "AND kind='finalize' ORDER BY evidence_id DESC LIMIT 1",
            (item_ref, run_ref)).fetchone()
        if evidence is None:
            raise FinalizationReadbackError(
                BLOCKED_TRUST_SOURCE,
                f"no kind=finalize evidence for {item_ref}/{run_ref}; a "
                f"SUCCEEDED state alone is not a signed finalization")
    except sqlite3.Error as exc:
        raise FinalizationReadbackError(
            BLOCKED_TRUST_SOURCE,
            f"Work Engine store {path} has no readable Run/finalize evidence "
            f"({exc})") from exc
    finally:
        conn.close()

    artifact_dir = Path(str(run["artifact_dir"]))
    locator = Path(str(evidence["locator"]))
    if (not artifact_dir.is_absolute() or not locator.is_absolute()
            or locator.is_symlink() or not locator.is_file()):
        raise FinalizationReadbackError(
            BLOCKED_TRUST_SOURCE,
            f"the finalize evidence locator {locator} is not a readable "
            f"absolute file")
    resolved_dir = artifact_dir.resolve()
    resolved_locator = locator.resolve()
    if (resolved_locator.parent != resolved_dir
            or resolved_locator.name != "finalize.json"):
        raise FinalizationReadbackError(
            BLOCKED_TRUST_SOURCE,
            f"the finalize evidence locator {resolved_locator} is not the "
            f"Run's own finalize.json")
    data = resolved_locator.read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    verified = digest == str(evidence["sha256"]).lower()
    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    authorization = payload.get("authorization")
    runner_receipt = payload.get("runner_receipt")
    if not isinstance(runner_receipt, dict):
        runner_receipt = None
    t10_acceptable = (
        authorization == "T10_VERIFIED" and runner_receipt is not None)
    return WorkEngineFinalizeReadback(
        item_ref=str(run["item_id"]),
        run_ref=str(run["run_id"]),
        run_state=str(run["state"]),
        run_worker=str(run["worker"]),
        artifact_dir=str(resolved_dir),
        evidence_locator=str(resolved_locator),
        evidence_sha256=str(evidence["sha256"]).lower(),
        evidence_bytes=data,
        evidence_verified=verified,
        authorization=str(authorization) if isinstance(authorization, str) else None,
        t10_acceptable=t10_acceptable,
        verdict=str(payload.get("verdict")) if isinstance(payload.get("verdict"), str) else None,
        exit_code=payload.get("exit_code") if type(payload.get("exit_code")) is int else None,
        revision=str(payload.get("revision")) if isinstance(payload.get("revision"), str) else None,
        dirty=payload.get("dirty") if isinstance(payload.get("dirty"), (bool, int)) else None,
        runner_receipt=runner_receipt,
    )


def evaluate_signed_finalization(
        expected: ExpectedFinalization,
        readback: WorkEngineFinalizeReadback | Any | None) -> FinalizationDecision:
    """Refuse T10 success unless the durable signed finalization is exact.

    A caller dict/JSON label is `DENY`; a missing/unreadable readback is
    `BLOCKED_TRUST_SOURCE`; an authoritative contradiction is `DENY`; only a
    matching `T10_VERIFIED` + `t10_acceptable=true` + verified zero-exit receipt
    is `ELIGIBLE` for independent QA review.
    """
    _validate_expected(expected)
    reasons = _Reasons()

    if readback is None:
        reasons.add(BLOCKED_TRUST_SOURCE, _BLOCKED)
        return _decision(expected, reasons, None)
    if not isinstance(readback, WorkEngineFinalizeReadback):
        reasons.add(CALLER_JSON_NOT_AUTHORITY, _DENY)
        return _decision(expected, reasons, None)

    if not readback.evidence_verified:
        reasons.add(EVIDENCE_TAMPERED, _DENY)
    if (not isinstance(readback.evidence_bytes, (bytes, bytearray))
            or hashlib.sha256(bytes(readback.evidence_bytes)).hexdigest()
            != str(readback.evidence_sha256).lower()):
        reasons.add(EVIDENCE_TAMPERED, _DENY)

    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(bytes(readback.evidence_bytes))
        if isinstance(parsed, dict):
            payload = parsed
    except (TypeError, ValueError):
        pass
    if not payload:
        reasons.add(INVALID_READBACK, _DENY)

    file_authorization = payload.get("authorization")
    if readback.authorization == "UNSIGNED" or file_authorization == "UNSIGNED":
        reasons.add(UNSIGNED_FINALIZATION, _DENY)
    if (readback.authorization != "T10_VERIFIED"
            or file_authorization != "T10_VERIFIED"):
        reasons.add(AUTHORIZATION_NOT_T10_VERIFIED, _DENY)

    file_receipt = payload.get("runner_receipt")
    if not isinstance(file_receipt, dict):
        file_receipt = None
    derived_acceptable = (
        file_authorization == "T10_VERIFIED" and file_receipt is not None)
    if readback.t10_acceptable is not True or derived_acceptable is not True:
        reasons.add(T10_NOT_ACCEPTABLE, _DENY)

    if str(readback.run_state).strip().upper() != "SUCCEEDED":
        reasons.add(RUN_NOT_SUCCEEDED, _DENY)
    if readback.item_ref != expected.item_ref:
        reasons.add(ITEM_MISMATCH, _DENY)
    if readback.run_ref != expected.run_ref:
        reasons.add(RUN_MISMATCH, _DENY)
    if readback.run_worker != expected.runner_identity:
        reasons.add(RUNNER_MISMATCH, _DENY)
    if expected.process_exit_code != 0:
        reasons.add(EXPECTED_EXIT_NOT_ZERO, _DENY)
    if readback.verdict != "PASS":
        reasons.add(VERDICT_MISMATCH, _DENY)
    if readback.exit_code != 0:
        reasons.add(EXIT_CODE_MISMATCH, _DENY)
    if readback.revision != expected.commit_sha:
        reasons.add(REVISION_MISMATCH, _DENY)
    if bool(readback.dirty):
        reasons.add(DIRTY_RECORD, _DENY)
    try:
        if (Path(readback.evidence_locator).resolve().parent
                != Path(readback.artifact_dir).resolve()
                or Path(readback.evidence_locator).name != "finalize.json"):
            reasons.add(LOCATOR_MISMATCH, _DENY)
    except OSError:
        reasons.add(LOCATOR_MISMATCH, _DENY)

    receipt = readback.runner_receipt
    if not isinstance(receipt, Mapping):
        reasons.add(MISSING_SOURCE_PROOF, _DENY)
    else:
        if file_receipt is not None and dict(receipt) != dict(file_receipt):
            reasons.add(READBACK_CONTRADICTION, _DENY)
        if receipt.get("action_key") != expected.action_key:
            reasons.add(ACTION_MISMATCH, _DENY)
        if receipt.get("input_digest") != expected.input_digest:
            reasons.add(INPUT_DIGEST_MISMATCH, _DENY)
        if receipt.get("receipt_sha256") != expected.receipt_sha256:
            reasons.add(RECEIPT_ANCHOR_MISMATCH, _DENY)
        if type(receipt.get("process_exit_code")) is not int \
                or receipt.get("process_exit_code") != 0:
            reasons.add(NO_VERIFIED_ZERO_EXIT, _DENY)
        elif expected.process_exit_code != 0:
            reasons.add(EXPECTED_EXIT_NOT_ZERO, _DENY)
        if receipt.get("runner_identity") != expected.runner_identity:
            reasons.add(RUNNER_MISMATCH, _DENY)
        if receipt.get("commit_sha") != expected.commit_sha:
            reasons.add(COMMIT_MISMATCH, _DENY)
        if receipt.get("actor_ref") != expected.actor_ref:
            reasons.add(ACTOR_MISMATCH, _DENY)

    return _decision(expected, reasons, readback)


class _DENY:
    pass


class _BLOCKED:
    pass


class _Reasons:
    def __init__(self) -> None:
        self.codes: list[str] = []
        self.has_deny = False
        self.has_blocked = False

    def add(self, code: str, severity) -> None:
        if code not in self.codes:
            self.codes.append(code)
        if severity is _DENY:
            self.has_deny = True
        elif severity is _BLOCKED:
            self.has_blocked = True

    def decision(self) -> str:
        if self.has_deny:
            return DECISION_DENY
        if self.has_blocked:
            return DECISION_BLOCKED
        return DECISION_ELIGIBLE


def _validate_expected(expected: ExpectedFinalization) -> None:
    for name in ("item_ref", "run_ref", "actor_ref", "action_key",
                 "runner_identity"):
        value = getattr(expected, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"expected finalization {name} must be nonempty")
    if not isinstance(expected.commit_sha, str) or not _HEX40.fullmatch(
            expected.commit_sha):
        raise ValueError("expected finalization commit_sha must be a full 40-hex SHA")
    for name in ("input_digest", "receipt_sha256"):
        value = getattr(expected, name)
        if not isinstance(value, str) or not _HEX64.fullmatch(value):
            raise ValueError(f"expected finalization {name} must be a 64-hex digest")
    if type(expected.process_exit_code) is not int:
        raise ValueError("expected finalization process_exit_code must be an int")


def _decision(expected: ExpectedFinalization, reasons: _Reasons,
              readback: WorkEngineFinalizeReadback | None) -> FinalizationDecision:
    decision = reasons.decision()
    eligible = decision == DECISION_ELIGIBLE
    if eligible:
        because = _ELIGIBLE_BECAUSE
    else:
        because = "; ".join(_BECAUSE.get(code, code) for code in reasons.codes)
    authorization = readback.authorization if readback is not None else None
    if eligible:
        t10_status = "T10_VERIFIED"
    elif authorization == "UNSIGNED":
        t10_status = "UNSIGNED"
    elif decision == DECISION_BLOCKED:
        t10_status = "UNKNOWN"
    else:
        t10_status = authorization or "NOT_VERIFIED"
    readback_summary = None
    if readback is not None:
        readback_summary = {
            "item_ref": readback.item_ref,
            "run_ref": readback.run_ref,
            "run_state": readback.run_state,
            "run_worker": readback.run_worker,
            "evidence_locator": readback.evidence_locator,
            "evidence_sha256": readback.evidence_sha256,
            "evidence_verified": readback.evidence_verified,
            "authorization": readback.authorization,
            "t10_acceptable": readback.t10_acceptable,
            "verdict": readback.verdict,
            "exit_code": readback.exit_code,
            "revision": readback.revision,
            "dirty": readback.dirty,
            "runner_receipt": (
                dict(readback.runner_receipt)
                if isinstance(readback.runner_receipt, Mapping) else None),
            "source": readback.source,
            "evidence_level": readback.evidence_level,
        }
    return FinalizationDecision(
        decision=decision,
        reasons=tuple(reasons.codes),
        because=because,
        expected={
            "item_ref": expected.item_ref,
            "run_ref": expected.run_ref,
            "commit_sha": expected.commit_sha,
            "actor_ref": expected.actor_ref,
            "action_key": expected.action_key,
            "input_digest": expected.input_digest,
            "receipt_sha256": expected.receipt_sha256,
            "process_exit_code": expected.process_exit_code,
            "runner_identity": expected.runner_identity,
        },
        readback=readback_summary,
        eligible_for_qa_review=eligible,
        parent_accepted=False,
        qa_signature=False,
        requires_independent_qa=True,
        t10_status=t10_status,
        production_status=BLOCKED_TRUST_SOURCE,
        evidence_level=(readback.evidence_level if readback is not None
                        else "UNKNOWN"),
        authority="work-engine:runs+evidence (durable finalize evidence)",
    )
