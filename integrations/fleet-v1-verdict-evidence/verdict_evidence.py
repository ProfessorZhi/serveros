#!/usr/bin/env python3
"""#237 T10 — a pure acceptance seam over Work Engine's Run/evidence records.

WHY THIS MODULE EXISTS
----------------------
Issue #237's T10 acceptance is one sentence:

    Child self-reports success while tests fail -> Parent not ACCEPTED.

A child's report is prose.  Prose has no exit code, no revision and no digest,
so a Parent that reads it is not evaluating evidence; it is reading a claim.
The existing trusted authority for an execution attempt is Work Engine:

* `runs` (item_id, run_id, worker, state, artifact_dir, ...) is the durable
  Run record.  `runs.worker` is the runner identity that executed it.
* `evidence` (item_id, run_id, kind, locator, sha256, command, cases_json,
  exit_code, revision, dirty, verdict, created_at) is the execution receipt.
* `workengine.store.finalize_run` is the only writer that concludes a Run.
  It writes the `kind='finalize'` evidence row at
  `<artifact_dir>/finalize.json` and sets `runs.state` to `SUCCEEDED` exactly
  when `verdict == 'PASS' and exit_code == 0`; every other combination is
  `FAILED`.

This module reads that boundary and nothing else.  It is deliberately pure:

* no sqlite connection, no filesystem write, no Board, no second ledger;
* the caller supplies a read-only `AuthoritySnapshot` (the rows an adapter
  read from work-engine) and the gate decides;
* the child's text is accepted only as `untrusted_child_report` and is never
  inspected.  It cannot change the decision, and the decision says so.

WHAT "ELIGIBLE" MEANS, AND WHAT IT DOES NOT MEAN
------------------------------------------------
`ELIGIBLE` means: a structured finalize receipt exists, belongs to this exact
Item/Run, was produced by a recognised runner for the expected principal, its
command matches the expected tests command, it exited zero with `PASS`, it is
bound to the exact expected SHA with no dirty worktree, its locator is inside
this Run's artifact directory, the artifact bytes still hash to the recorded
SHA-256, and the artifact payload agrees with the row.

It is a precondition for a Parent review or a QA gate.  It is NOT a Parent
acceptance and NOT a V1 QA signature.  This module has no authority to grant
either, so every decision it returns carries `parent_accepted=False`,
`qa_signature=False`, `requires_independent_qa=True`, and both
`run_state_advanced=False` and `qa_gate_advanced=False`.

THE RESIDUAL GAP THIS SEAM DOES NOT CLOSE
-----------------------------------------
Work Engine's `finalize_run` receives `exit_code` and `verdict` as *arguments
from the finalizing caller*.  The host-side durable process fact is
`runtime/bounded_invocation.invoke_once`, whose receipt records
`process_exit_code` from `proc.returncode` and digests stdout/stderr.  Until
Work Engine (or its finalizing caller) links that runner-produced receipt into
the evidence row, a hostile child with write access to the Work Engine store
could still assert a false exit code.  Therefore this module is a
text-versus-receipt gate; it does not make T10 adversarially closed.  The
README names the owner integration required, and this module must not be used
as a V1 QA signature.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

DECISION_DENY = "DENY"
DECISION_UNKNOWN = "UNKNOWN"
DECISION_ELIGIBLE = "ELIGIBLE"

#: The authority this seam reads.  Named in every decision so a reader can
#: tell which store the answer is about.
AUTHORITY = "work-engine:runs+evidence"
FINALIZE_KIND = "finalize"
PASS_WORD = "PASS"

#: Work Engine folds verdict spellings before comparing them
#: (`workengine.store._verdict_word`), so `pass` and `PASS` are one judgement.
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}$")
_DIGEST_RE = re.compile(r"^[0-9a-fA-F]{64}$")
_RUN_STATES = frozenset({
    "ALLOCATED", "PREPARING", "RUNNING", "FINALIZING",
    "SUCCEEDED", "FAILED", "CANCELLED", "INVALID",
})
_FAILED_RUN_STATES = frozenset({"FAILED", "CANCELLED", "INVALID"})

# Reason codes.  Each is a fact about one comparison, not a severity: the
# decision is derived from which set the facts belong to.
MISSING_RUN = "MISSING_RUN"
INVALID_RUN_RECORD = "INVALID_RUN_RECORD"
RUN_NOT_TERMINAL = "RUN_NOT_TERMINAL"
RUN_FAILED = "RUN_FAILED"
UNRECOGNIZED_RUNNER = "UNRECOGNIZED_RUNNER"
CROSS_PRINCIPAL = "CROSS_PRINCIPAL"
MISSING_RECEIPT = "MISSING_RECEIPT"
AMBIGUOUS_RECEIPT = "AMBIGUOUS_RECEIPT"
INVALID_RECEIPT = "INVALID_RECEIPT"
NOT_FINALIZE_RECEIPT = "NOT_FINALIZE_RECEIPT"
CROSS_RUN_RECEIPT = "CROSS_RUN_RECEIPT"
COMMAND_MISMATCH = "COMMAND_MISMATCH"
TESTS_FAILED = "TESTS_FAILED"
VERDICT_NOT_PASS = "VERDICT_NOT_PASS"
INVALID_REVISION = "INVALID_REVISION"
STALE_REVISION = "STALE_REVISION"
DIRTY_REVISION = "DIRTY_REVISION"
EVIDENCE_OUTSIDE_RUN_ARTIFACTS = "EVIDENCE_OUTSIDE_RUN_ARTIFACTS"
MISSING_ARTIFACT = "MISSING_ARTIFACT"
DIGEST_MISMATCH = "DIGEST_MISMATCH"
ARTIFACT_CONTRADICTION = "ARTIFACT_CONTRADICTION"
UNSIGNED_FINALIZATION = "UNSIGNED_FINALIZATION"
AUTHORIZATION_NOT_T10_VERIFIED = "AUTHORIZATION_NOT_T10_VERIFIED"
T10_NOT_ACCEPTABLE = "T10_NOT_ACCEPTABLE"
MISSING_SOURCE_PROOF = "MISSING_SOURCE_PROOF"
NO_VERIFIED_ZERO_EXIT = "NO_VERIFIED_ZERO_EXIT"
COMMIT_MISMATCH = "COMMIT_MISMATCH"
ACTOR_MISMATCH = "ACTOR_MISMATCH"
RUNNER_MISMATCH = "RUNNER_MISMATCH"
INVALID_SIGNED_RECEIPT = "INVALID_SIGNED_RECEIPT"

_DENY_REASONS = frozenset({
    INVALID_RUN_RECORD, RUN_FAILED, UNRECOGNIZED_RUNNER, CROSS_PRINCIPAL,
    AMBIGUOUS_RECEIPT, INVALID_RECEIPT, NOT_FINALIZE_RECEIPT,
    CROSS_RUN_RECEIPT, COMMAND_MISMATCH, TESTS_FAILED, VERDICT_NOT_PASS,
    INVALID_REVISION, STALE_REVISION, DIRTY_REVISION,
    EVIDENCE_OUTSIDE_RUN_ARTIFACTS, DIGEST_MISMATCH, ARTIFACT_CONTRADICTION,
    UNSIGNED_FINALIZATION, AUTHORIZATION_NOT_T10_VERIFIED, T10_NOT_ACCEPTABLE,
    MISSING_SOURCE_PROOF, NO_VERIFIED_ZERO_EXIT, COMMIT_MISMATCH,
    ACTOR_MISMATCH, RUNNER_MISMATCH, INVALID_SIGNED_RECEIPT,
})
_UNKNOWN_REASONS = frozenset({
    MISSING_RUN, RUN_NOT_TERMINAL, MISSING_RECEIPT, MISSING_ARTIFACT,
})

_BECAUSE = {
    MISSING_RUN: "the authority has no Run row for this Item/Run",
    INVALID_RUN_RECORD: "the Run row is not a usable Work Engine record",
    RUN_NOT_TERMINAL: "the Run has not reached a terminal verdict",
    RUN_FAILED: "the Run's terminal state is not SUCCEEDED",
    UNRECOGNIZED_RUNNER: "the Run's worker is not a recognised runner identity",
    CROSS_PRINCIPAL: "the Run's worker belongs to a different principal",
    MISSING_RECEIPT: "no Work Engine kind=finalize evidence row exists for this Run",
    AMBIGUOUS_RECEIPT: "more than one finalize receipt claims this Run",
    INVALID_RECEIPT: "the evidence row is not a usable Work Engine receipt",
    NOT_FINALIZE_RECEIPT: "the row is not the finalize receipt that concluded the Run",
    CROSS_RUN_RECEIPT: "the receipt belongs to a different Item/Run",
    COMMAND_MISMATCH: "the receipt's command is not the expected tests command",
    TESTS_FAILED: "the receipt's exit_code is not 0",
    VERDICT_NOT_PASS: "the receipt's verdict is not PASS",
    INVALID_REVISION: "the receipt's revision is not an exact 40-hex SHA",
    STALE_REVISION: "the receipt is bound to a different revision than the expected exact SHA",
    DIRTY_REVISION: "the receipt records a dirty worktree, so the SHA does not describe what was tested",
    EVIDENCE_OUTSIDE_RUN_ARTIFACTS: "the receipt locator is outside this Run's artifact directory",
    MISSING_ARTIFACT: "the receipt's artifact is not readable, so its digest cannot be checked",
    DIGEST_MISMATCH: "the artifact bytes do not hash to the receipt's recorded SHA-256",
    ARTIFACT_CONTRADICTION: "the artifact payload disagrees with the evidence row",
    UNSIGNED_FINALIZATION: "the durable finalize.json is labelled UNSIGNED",
    AUTHORIZATION_NOT_T10_VERIFIED: "the durable finalize.json is not T10_VERIFIED",
    T10_NOT_ACCEPTABLE: "the finalization is not t10_acceptable",
    MISSING_SOURCE_PROOF: "the T10_VERIFIED label carries no runner_receipt proof",
    NO_VERIFIED_ZERO_EXIT: "the runner_receipt has no verified process_exit_code == 0",
    COMMIT_MISMATCH: "the runner_receipt commit is not the expected exact SHA",
    ACTOR_MISMATCH: "the runner_receipt actor is not the expected principal",
    RUNNER_MISMATCH: "the runner_receipt runner is not the Run's expected worker",
    INVALID_SIGNED_RECEIPT: "the runner_receipt fields are not usable evidence",
}

_ELIGIBLE_BECAUSE = (
    "the Work Engine finalize receipt agrees with the expected Run, exact "
    "revision, command, runner identity and artifact digest; this is "
    "eligibility for review, not acceptance or a QA signature"
)


@dataclass(frozen=True)
class ExpectedAcceptance:
    """What the Parent is asking the authority to prove.

    `recognized_runners` is the caller's derivation from the live grant /
    principal binding: principal -> runner identities that principal may use.
    It is an expectation, not a fact.  The gate only checks the Run's recorded
    `worker` against it; it does not mint or widen a grant.
    """

    item_id: str
    run_id: str
    revision: str
    principal: str
    command: str
    recognized_runners: Mapping[str, Iterable[str]]


@dataclass(frozen=True)
class AuthoritySnapshot:
    """A read-only readback from the authority.

    `run` is the `runs` row for the expected Item/Run (or None when absent).
    `finalize_evidence` is every `kind='finalize'` evidence row the authority
    returned for that Run; exactly one is required for eligibility, because two
    receipts for one Run are an ambiguity the gate will not resolve in the
    caller's favour.
    `artifacts` maps a receipt's absolute locator to the bytes read back from
    that locator.  The gate re-hashes those bytes; it never trusts the row's
    `sha256` on its own.
    """

    run: Mapping[str, Any] | None
    finalize_evidence: tuple[Mapping[str, Any], ...]
    artifacts: Mapping[str, bytes]


@dataclass(frozen=True)
class AcceptanceDecision:
    decision: str
    reasons: tuple[str, ...]
    because: str
    target: Mapping[str, Any]
    receipt: Mapping[str, Any] | None
    child_report_ignored: bool
    eligible_for_qa_review: bool
    parent_accepted: bool
    qa_signature: bool
    requires_independent_qa: bool
    run_state_advanced: bool
    qa_gate_advanced: bool
    authority: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "because": self.because,
            "target": dict(self.target),
            "receipt": dict(self.receipt) if self.receipt is not None else None,
            "child_report_ignored": self.child_report_ignored,
            "eligible_for_qa_review": self.eligible_for_qa_review,
            "parent_accepted": self.parent_accepted,
            "qa_signature": self.qa_signature,
            "requires_independent_qa": self.requires_independent_qa,
            "run_state_advanced": self.run_state_advanced,
            "qa_gate_advanced": self.qa_gate_advanced,
            "authority": self.authority,
        }


def evaluate_acceptance(
    expected: ExpectedAcceptance,
    authority: AuthoritySnapshot,
    *,
    untrusted_child_report: str | None = None,
) -> AcceptanceDecision:
    """Decide whether a structured receipt may move a Parent/QA gate.

    The child's report is deliberately not parsed.  It is present in the
    signature only so tests and callers can demonstrate that it changes
    nothing; the returned decision records `child_report_ignored=True` whenever
    one was supplied.
    """
    _validate_expected(expected)

    reasons: list[str] = []

    def add(code: str) -> None:
        if code not in reasons:
            reasons.append(code)

    run = authority.run
    artifact_dir: Path | None = None

    if run is None:
        add(MISSING_RUN)
    elif not _run_is_well_formed(run):
        add(INVALID_RUN_RECORD)
    else:
        if (run["item_id"] != expected.item_id
                or run["run_id"] != expected.run_id):
            add(CROSS_RUN_RECEIPT)
        state = str(run["state"]).strip().upper()
        if state not in _RUN_STATES:
            add(INVALID_RUN_RECORD)
        elif state in _FAILED_RUN_STATES:
            add(RUN_FAILED)
        elif state != "SUCCEEDED":
            add(RUN_NOT_TERMINAL)
        artifact_dir = Path(str(run["artifact_dir"])).resolve()
        _check_worker(expected, run["worker"], add)

    rows = tuple(authority.finalize_evidence)
    receipt: Mapping[str, Any] | None = None
    if not rows:
        add(MISSING_RECEIPT)
    elif len(rows) > 1:
        add(AMBIGUOUS_RECEIPT)
    else:
        receipt = rows[0]
        _check_receipt(
            expected, receipt, run, artifact_dir, authority.artifacts, add)

    if any(code in _DENY_REASONS for code in reasons):
        decision = DECISION_DENY
    elif any(code in _UNKNOWN_REASONS for code in reasons):
        decision = DECISION_UNKNOWN
    else:
        decision = DECISION_ELIGIBLE

    because = "; ".join(_BECAUSE.get(code, code) for code in reasons)
    if not because:
        because = _ELIGIBLE_BECAUSE
    if untrusted_child_report is not None:
        because += (
            ". The child's report was recorded as untrusted prose and was not "
            "read for this decision")

    return AcceptanceDecision(
        decision=decision,
        reasons=tuple(reasons),
        because=because,
        target={
            "item_id": expected.item_id,
            "run_id": expected.run_id,
            "revision": expected.revision,
            "principal": expected.principal,
            "command": expected.command,
        },
        receipt=dict(receipt) if receipt is not None else None,
        child_report_ignored=untrusted_child_report is not None,
        eligible_for_qa_review=(decision == DECISION_ELIGIBLE),
        # This seam holds no acceptance or signing authority.  These four are
        # constants rather than derived booleans so a later edit cannot turn a
        # green receipt into a Parent acceptance by accident.
        parent_accepted=False,
        qa_signature=False,
        requires_independent_qa=True,
        run_state_advanced=False,
        qa_gate_advanced=False,
        authority=AUTHORITY,
    )


def _validate_expected(expected: ExpectedAcceptance) -> None:
    for name, value in (
        ("item_id", expected.item_id),
        ("run_id", expected.run_id),
        ("principal", expected.principal),
        ("command", expected.command),
    ):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"expected {name} must be a nonempty string")
    if not isinstance(expected.revision, str) or not _SHA_RE.fullmatch(
            expected.revision):
        raise ValueError(
            "expected revision must be an exact full 40-hex git SHA; a branch "
            "name, short SHA or moving ref cannot identify what was tested")
    runners = expected.recognized_runners
    if not isinstance(runners, Mapping) or expected.principal not in runners:
        raise ValueError(
            "recognized_runners must map the expected principal to its runner "
            "identities; an unknown principal has no authority to check")
    for principal, identities in runners.items():
        if not isinstance(principal, str) or not principal.strip():
            raise ValueError("recognized_runners keys must be nonempty principals")
        for identity in identities:
            if not isinstance(identity, str) or not identity.strip():
                raise ValueError(
                    "recognized_runners values must be nonempty runner identities")


def _run_is_well_formed(run: Mapping[str, Any]) -> bool:
    if not isinstance(run, Mapping):
        return False
    for key in ("item_id", "run_id", "worker", "state", "artifact_dir"):
        value = run.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    artifact_dir = Path(str(run["artifact_dir"]))
    return artifact_dir.is_absolute()


def _check_worker(expected: ExpectedAcceptance, worker: Any, add) -> None:
    if not isinstance(worker, str) or not worker.strip():
        add(INVALID_RUN_RECORD)
        return
    mine = {str(item) for item in expected.recognized_runners[expected.principal]}
    everybody = {
        str(item)
        for identities in expected.recognized_runners.values()
        for item in identities
    }
    if worker not in everybody:
        add(UNRECOGNIZED_RUNNER)
    elif worker not in mine:
        add(CROSS_PRINCIPAL)


def _receipt_is_well_formed(row: Mapping[str, Any]) -> bool:
    if not isinstance(row, Mapping):
        return False
    text_fields = (
        "item_id", "run_id", "kind", "locator", "command", "cases_json",
        "revision", "verdict",
    )
    for key in text_fields:
        value = row.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    digest = row.get("sha256")
    if not isinstance(digest, str) or not _DIGEST_RE.fullmatch(digest):
        return False
    if type(row.get("exit_code")) is not int:
        return False
    dirty = row.get("dirty")
    if type(dirty) is not int and not isinstance(dirty, bool):
        return False
    try:
        cases = json.loads(row["cases_json"])
    except (TypeError, ValueError):
        return False
    if not isinstance(cases, list):
        return False
    if not Path(row["locator"]).is_absolute():
        return False
    return True


def _check_receipt(
    expected: ExpectedAcceptance,
    row: Mapping[str, Any],
    run: Mapping[str, Any] | None,
    artifact_dir: Path | None,
    artifacts: Mapping[str, bytes],
    add,
) -> None:
    if not _receipt_is_well_formed(row):
        add(INVALID_RECEIPT)
        return

    if row["kind"] != FINALIZE_KIND:
        add(NOT_FINALIZE_RECEIPT)
    if row["item_id"] != expected.item_id or row["run_id"] != expected.run_id:
        add(CROSS_RUN_RECEIPT)
    if row["command"] != expected.command:
        add(COMMAND_MISMATCH)
    if row["exit_code"] != 0:
        add(TESTS_FAILED)
    if str(row["verdict"]).strip().upper() != PASS_WORD:
        add(VERDICT_NOT_PASS)

    revision = str(row["revision"]).strip()
    if not _SHA_RE.fullmatch(revision):
        add(INVALID_REVISION)
    elif revision != expected.revision:
        add(STALE_REVISION)

    if bool(row["dirty"]):
        add(DIRTY_REVISION)

    locator = Path(str(row["locator"]))
    if artifact_dir is None or not _is_within(locator, artifact_dir):
        add(EVIDENCE_OUTSIDE_RUN_ARTIFACTS)

    data = _artifact_bytes(locator, artifacts)
    if data is None:
        add(MISSING_ARTIFACT)
        return
    if hashlib.sha256(data).hexdigest().lower() != str(row["sha256"]).lower():
        add(DIGEST_MISMATCH)
        return
    if not _artifact_matches_row(data, row):
        add(ARTIFACT_CONTRADICTION)
    _check_signed_finalization_artifact(data, expected, run, add)


def _check_signed_finalization_artifact(
        data: bytes, expected: ExpectedAcceptance,
        run: Mapping[str, Any] | None, add) -> None:
    """Refuse a #15 finalize.json whose label is not T10_VERIFIED.

    Work Engine #15 writes `authorization: "UNSIGNED" | "T10_VERIFIED"` into
    the durable finalize.json. A consumer that reads only `runs.state` would
    accept an UNSIGNED record. This check is the low-level half; the strict
    `finalization_result.evaluate_signed_finalization` is the current
    full-result gate. A pre-#15 fixture with no `authorization` key is left to
    that strict gate; every labelled record is checked here.
    """
    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        return
    if not isinstance(payload, dict):
        return
    authorization = payload.get("authorization")
    if authorization is None:
        return
    if authorization == "UNSIGNED":
        add(UNSIGNED_FINALIZATION)
        return
    if authorization != "T10_VERIFIED":
        add(AUTHORIZATION_NOT_T10_VERIFIED)
        return
    if "t10_acceptable" in payload and payload.get("t10_acceptable") is not True:
        add(T10_NOT_ACCEPTABLE)
    receipt = payload.get("runner_receipt")
    if not isinstance(receipt, Mapping):
        add(MISSING_SOURCE_PROOF)
        return
    if (type(receipt.get("process_exit_code")) is not int
            or receipt.get("process_exit_code") != 0):
        add(NO_VERIFIED_ZERO_EXIT)
    if (not _SHA_RE.fullmatch(str(receipt.get("commit_sha") or ""))
            or str(receipt.get("commit_sha")) != expected.revision):
        add(COMMIT_MISMATCH)
    if (not str(receipt.get("actor_ref") or "").strip()
            or str(receipt.get("actor_ref")) != expected.principal):
        add(ACTOR_MISMATCH)
    runner = str(receipt.get("runner_identity") or "").strip()
    workers = {str(item) for item in
               expected.recognized_runners.get(expected.principal, ())}
    run_worker = str(run.get("worker") or "") if isinstance(run, Mapping) else ""
    if not runner or runner not in workers or (run_worker and runner != run_worker):
        add(RUNNER_MISMATCH)
    if not str(receipt.get("action_key") or "").strip():
        add(INVALID_SIGNED_RECEIPT)
    for field in ("input_digest", "receipt_sha256"):
        if not re.fullmatch(r"[0-9a-fA-F]{64}", str(receipt.get(field) or "")):
            add(INVALID_SIGNED_RECEIPT)


def _is_within(path: Path, directory: Path) -> bool:
    try:
        resolved = path.resolve()
        root = directory.resolve()
    except OSError:
        return False
    return resolved == root or root in resolved.parents


def _artifact_bytes(locator: Path, artifacts: Mapping[str, bytes]) -> bytes | None:
    candidates = [str(locator)]
    try:
        candidates.append(str(locator.resolve()))
    except OSError:
        pass
    for key in candidates:
        data = artifacts.get(key)
        if data is None:
            continue
        if not isinstance(data, (bytes, bytearray)):
            return None
        return bytes(data)
    return None


def _artifact_matches_row(data: bytes, row: Mapping[str, Any]) -> bool:
    try:
        payload = json.loads(data)
    except (TypeError, ValueError):
        return False
    if not isinstance(payload, dict):
        return False

    expected_fields = {
        "command": row["command"],
        "cases": json.loads(row["cases_json"]),
        "exit_code": row["exit_code"],
        "revision": row["revision"],
        "dirty": bool(row["dirty"]),
        "verdict": str(row["verdict"]).strip().upper(),
    }
    actual_fields = {
        "command": payload.get("command"),
        "cases": payload.get("cases"),
        "exit_code": payload.get("exit_code"),
        "revision": payload.get("revision"),
        "dirty": payload.get("dirty"),
        "verdict": (
            str(payload.get("verdict")).strip().upper()
            if isinstance(payload.get("verdict"), str) else payload.get("verdict")
        ),
    }
    if actual_fields["dirty"] is not None:
        actual_fields["dirty"] = bool(actual_fields["dirty"])
    return actual_fields == expected_fields
