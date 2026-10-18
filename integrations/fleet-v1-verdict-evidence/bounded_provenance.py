#!/usr/bin/env python3
"""#237 T10 — reconcile Work Engine with bounded-invocation execution provenance.

WHAT THIS ADDS TO `verdict_evidence.py`
---------------------------------------
`verdict_evidence.py` answers "is this Work Engine finalize receipt a receipt,
rather than the child's prose?".  That is necessary and not sufficient for T10:
Work Engine's `finalize_run` takes `exit_code` and `verdict` as arguments from
the finalizing caller, so the stored exit code is a caller assertion.

The host-side process fact already exists in
`runtime/bounded_invocation.invoke_once`:

* it writes a durable receipt under a private 0700 directory before spawning;
* it records `process_exit_code` from `proc.returncode`, never from the child;
* it records `output_sha256` for the `.stdout` and `.stderr` files;
* it records `input_digest = sha256(json({argv,cwd,input,readonly,timeout,
  max_output_bytes,env}, sort_keys=True))`;
* it writes the exact stdin it received to `<key>.stdin`;
* it replays a receipt under the same key and input instead of re-executing.

This module reads that provenance with `read_bounded_receipt`, recomputes the
input and output digests, and requires the dispatch to name the exact
`cycle_ref / action_key / work_item / work_run / commit_SHA / principal /
grant_ref / runner_identity`.  A child's stdout text is hashed, never parsed.

WHAT IS STILL MISSING IN CURRENT PRODUCTION (BLOCKED_WIRING)
------------------------------------------------------------
The existing Agent Control DISPATCHED evidence is three fields:
`action_key`, `status`, `input_digest` (`runtime/cli.py::_dispatch_answer`).
It does not anchor the receipt file or its artifacts, and the existing
dispatchers do not put the T10 binding into the receipt's stdin/env.
Consequently a real exit 0 receipt produced by current production answers
`BLOCKED_WIRING`, not `ELIGIBLE`.  The producer patch is stated in the README:
record `receipt_sha256` in the DISPATCHED evidence and put the canonical
binding in the dispatch's stdin/env.  This module does not edit Work Engine,
`runtime/bounded_invocation.py`, `runtime/cli.py`, `runtime/agent_control_store.py`
or any other owner's file.

The receipt anchor is:

    sha256("receipt\\0" + receipt_bytes + "\\0"
           "stdin\\0"   + stdin_bytes   + "\\0"
           "stdout\\0"  + stdout_bytes  + "\\0"
           "stderr\\0"  + stderr_bytes  + "\\0")

The producer must compute the same value from the files it just read and store
it in `admission["confirmations"]["DISPATCHED"]["receipt_sha256"]`.  A receipt
edited after the confirmation then fails `RECEIPT_ANCHOR_MISMATCH`, even if the
forger also edits the Work Engine row.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable, Mapping

DECISION_DENY = "DENY"
DECISION_UNKNOWN = "UNKNOWN"
DECISION_BLOCKED_WIRING = "BLOCKED_WIRING"
DECISION_ELIGIBLE = "ELIGIBLE"

#: T10 is a release gate, not a local green light. Even when the producer and
#: consumer reconcile every local fact, a real Work Engine finalization does
#: not yet consume the producer proof (its `finalize_run` does not refuse a
#: finalize whose linked runner receipt is absent or contradictory). The
#: product status therefore stays BLOCKED_WIRING until that owner change lands.
T10_STATUS_BLOCKED_WIRING = "BLOCKED_WIRING"
WORK_ENGINE_CONSUMPTION_NOT_WIRED = "NOT_WIRED"

_DENY = "DENY"
_UNKNOWN = "UNKNOWN"
_BLOCKED = "BLOCKED_WIRING"

RECEIPT_STATUSES = (
    "STARTED", "EXITED", "REFUSED", "TIMED_OUT", "OUTPUT_LIMIT",
    "START_FAILED", "IO_FAILED",
)

BINDING_FIELDS = (
    "cycle_ref",
    "action_key",
    "work_item_id",
    "work_run_id",
    "commit_sha",
    "principal",
    "grant_ref",
    "runner_identity",
)

# Reason codes.  Every one is a fact about one comparison.
MISSING_RECEIPT = "MISSING_RECEIPT"
INVALID_RECEIPT = "INVALID_RECEIPT"
RECEIPT_NOT_EXITED = "RECEIPT_NOT_EXITED"
PROCESS_EXIT_NONZERO = "PROCESS_EXIT_NONZERO"
INPUT_DIGEST_MISMATCH = "INPUT_DIGEST_MISMATCH"
STDIN_ARTIFACT_MISMATCH = "STDIN_ARTIFACT_MISMATCH"
OUTPUT_DIGEST_MISMATCH = "OUTPUT_DIGEST_MISMATCH"
MISSING_ARTIFACT = "MISSING_ARTIFACT"
MISSING_DISPATCH_BINDING = "MISSING_DISPATCH_BINDING"
ACTION_KEY_MISMATCH = "ACTION_KEY_MISMATCH"
CYCLE_MISMATCH = "CYCLE_MISMATCH"
CROSS_RUN = "CROSS_RUN"
CROSS_PRINCIPAL = "CROSS_PRINCIPAL"
CROSS_GRANT = "CROSS_GRANT"
STALE_SHA = "STALE_SHA"
RUNNER_IDENTITY_MISMATCH = "RUNNER_IDENTITY_MISMATCH"
MISSING_ADMISSION = "MISSING_ADMISSION"
INVALID_ADMISSION = "INVALID_ADMISSION"
ADMISSION_RETIRED = "ADMISSION_RETIRED"
ADMISSION_NOT_DISPATCHED = "ADMISSION_NOT_DISPATCHED"
RESERVATION_MISMATCH = "RESERVATION_MISMATCH"
SELECTION_DOES_NOT_NAME_ITEM = "SELECTION_DOES_NOT_NAME_ITEM"
SELECTION_DIGEST_MISMATCH = "SELECTION_DIGEST_MISMATCH"
STALE_RECEIPT = "STALE_RECEIPT"
CLOCK_UNREADABLE = "CLOCK_UNREADABLE"
MISSING_GRANT = "MISSING_GRANT"
GRANT_REVOKED = "GRANT_REVOKED"
GRANT_NOT_YET_VALID = "GRANT_NOT_YET_VALID"
EXPIRED_GRANT = "EXPIRED_GRANT"
GRANT_WINDOW_UNREADABLE = "GRANT_WINDOW_UNREADABLE"
DISPATCH_CONFIRMATION_MISMATCH = "DISPATCH_CONFIRMATION_MISMATCH"
MISSING_RECEIPT_ANCHOR = "MISSING_RECEIPT_ANCHOR"
RECEIPT_ANCHOR_MISMATCH = "RECEIPT_ANCHOR_MISMATCH"
EXIT_CODE_DISAGREEMENT = "EXIT_CODE_DISAGREEMENT"
MISSING_WORK_AUTHORITY = "MISSING_WORK_AUTHORITY"

# Continuation engineering authority (wake envelope + Agent Control grant/
# checkpoint + Work Engine Run/execution_context).
MISSING_AUTHORITY = "MISSING_AUTHORITY"
INVALID_AUTHORITY = "INVALID_AUTHORITY"
CHECKPOINT_MISMATCH = "CHECKPOINT_MISMATCH"
ENVELOPE_MISMATCH = "ENVELOPE_MISMATCH"
ENVELOPE_EXPIRED = "ENVELOPE_EXPIRED"
WORK_RUN_MISMATCH = "WORK_RUN_MISMATCH"
WORK_RUN_TERMINAL = "WORK_RUN_TERMINAL"
WORK_RUN_INVALID = "WORK_RUN_INVALID"
WORK_RUN_WORKER_MISMATCH = "WORK_RUN_WORKER_MISMATCH"
EXECUTION_REVISION_MISMATCH = "EXECUTION_REVISION_MISMATCH"
EXECUTION_WORKTREE_MISMATCH = "EXECUTION_WORKTREE_MISMATCH"
EXECUTION_DIRTY = "EXECUTION_DIRTY"

_BECAUSE = {
    MISSING_RECEIPT: "no bounded-invocation receipt exists for this action key",
    INVALID_RECEIPT: "the receipt is not a usable invoke_once record",
    RECEIPT_NOT_EXITED: "the receipt did not reach the EXITED status",
    PROCESS_EXIT_NONZERO: "the runner's recorded process_exit_code is not 0",
    INPUT_DIGEST_MISMATCH: "the receipt's input_digest does not describe the expected dispatch",
    STDIN_ARTIFACT_MISMATCH: "the receipt's stdin artifact is not the expected input",
    OUTPUT_DIGEST_MISMATCH: "the receipt's stdout/stderr bytes do not match its recorded digests",
    MISSING_ARTIFACT: "a receipt artifact is missing, so its digest cannot be checked",
    MISSING_DISPATCH_BINDING: "the receipt's stdin does not carry the T10 dispatch binding",
    ACTION_KEY_MISMATCH: "the receipt/confirmation names a different action_key",
    CYCLE_MISMATCH: "the receipt was dispatched for a different admission cycle",
    CROSS_RUN: "the dispatch binding names a different Item or Run",
    CROSS_PRINCIPAL: "the dispatch binding names a different principal",
    CROSS_GRANT: "the dispatch binding names a different grant",
    STALE_SHA: "the dispatch binding names a different commit SHA",
    RUNNER_IDENTITY_MISMATCH: "the dispatch binding names a different runner identity",
    MISSING_ADMISSION: "the Agent Control admission is not available",
    INVALID_ADMISSION: "the Agent Control admission is not a usable record",
    ADMISSION_RETIRED: "the admission was retired",
    ADMISSION_NOT_DISPATCHED: "the admission did not record a DISPATCHED phase",
    RESERVATION_MISMATCH: "the dispatch bounds differ from the admission's reservation",
    SELECTION_DOES_NOT_NAME_ITEM: "the admission's selection does not name this Item",
    SELECTION_DIGEST_MISMATCH: "the admission's selection digest is not the expected one",
    STALE_RECEIPT: "the receipt predates the admission that claims it",
    CLOCK_UNREADABLE: "a receipt/admission instant is not a readable UTC instant",
    MISSING_GRANT: "the grant record is not available",
    GRANT_REVOKED: "the grant is revoked",
    GRANT_NOT_YET_VALID: "the receipt started before the grant's valid_from",
    EXPIRED_GRANT: "the receipt finished at or after the grant's expires_at",
    GRANT_WINDOW_UNREADABLE: "the grant's valid_from/expires_at is not readable",
    DISPATCH_CONFIRMATION_MISMATCH: "the recorded DISPATCHED evidence disagrees with the receipt",
    MISSING_RECEIPT_ANCHOR: "the admission did not record a receipt_sha256 anchor",
    RECEIPT_ANCHOR_MISMATCH: "the receipt/artifact bytes do not match the recorded receipt_sha256",
    EXIT_CODE_DISAGREEMENT: "the runner receipt and the Work Engine evidence disagree on exit_code",
    MISSING_WORK_AUTHORITY: "the Work Engine Run/evidence snapshot is not available",
    MISSING_AUTHORITY: "the continuation authority snapshot is not available",
    INVALID_AUTHORITY: "the continuation authority snapshot is not a usable record",
    CHECKPOINT_MISMATCH: "the receipt/authority names a different checkpoint",
    ENVELOPE_MISMATCH: "the wake envelope disagrees with the expected Item/grant/checkpoint",
    ENVELOPE_EXPIRED: "the receipt finished after the wake envelope expired",
    WORK_RUN_MISMATCH: "the dispatch binding names a different Work Engine Run",
    WORK_RUN_TERMINAL: "the Work Engine Run is already terminal",
    WORK_RUN_INVALID: "the Work Engine Run state is not one this gate knows",
    WORK_RUN_WORKER_MISMATCH: "the Work Engine Run worker is not the expected runner identity",
    EXECUTION_REVISION_MISMATCH: "the Work Engine execution_context revision is not the expected SHA",
    EXECUTION_WORKTREE_MISMATCH: "the Work Engine execution_context is for a different worktree",
    EXECUTION_DIRTY: "the Work Engine execution_context records a dirty worktree",
}

_ELIGIBLE_BECAUSE = (
    "the runner receipt anchors exit 0, the exact dispatch binding, the exact "
    "commit SHA and the artifact digests, and Work Engine records the same Run "
    "at the same SHA; this is eligibility for independent QA review, not a "
    "Parent acceptance and not a QA signature"
)
_BLOCKED_BECAUSE = (
    "the receipt is authentic, but the existing producer/authority pair does "
    "not record the T10 dispatch binding and receipt_sha256 anchor; a real exit "
    "0 cannot be tied to work_run/commit_SHA/principal/grant/runner until the "
    "owner patch in the README lands"
)


@dataclass(frozen=True)
class ExpectedDispatch:
    cycle_ref: str
    action_key: str
    work_item_id: str
    work_run_id: str
    commit_sha: str
    principal: str
    grant_ref: str
    runner_identity: str
    argv: tuple[str, ...]
    cwd: str
    input_text: str
    readonly: bool
    timeout: int
    max_output_bytes: int
    env: Mapping[str, str]
    finalize_command: str
    selection_digest: str | None = None


@dataclass(frozen=True)
class AdmissionSnapshot:
    cycle_ref: str
    action_key: str
    grant_ref: str
    phase: str
    admitted_at: str
    retired_at: str | None
    reservation: Mapping[str, int]
    lease: Mapping[str, str]
    selection: Mapping[str, Any]
    confirmations: Mapping[str, Any]


@dataclass(frozen=True)
class GrantSnapshot:
    grant_ref: str
    delegated_by: str
    valid_from: str
    expires_at: str
    revoked: bool


@dataclass(frozen=True)
class BoundedReceipt:
    action_key: str
    raw_bytes: bytes
    receipt: Mapping[str, Any]
    stdin_bytes: bytes | None
    stdout_bytes: bytes | None
    stderr_bytes: bytes | None
    path: str


@dataclass(frozen=True)
class ExpectedContinuation:
    """What the Parent expects a continuation engineering execution to be.

    `cwd` is the bounded invocation's actual working directory (the private
    receipts directory); `worktree` is the granted host worktree whose HEAD and
    execution_context the binding must describe.
    """

    checkpoint_ref: str
    action_key: str
    work_item_id: str
    work_run_id: str
    commit_sha: str
    principal: str
    grant_ref: str
    runner_identity: str
    argv: tuple[str, ...]
    cwd: str
    input_text: str
    readonly: bool
    timeout: int
    max_output_bytes: int
    env: Mapping[str, str]
    worktree: str

    @property
    def cycle_ref(self) -> str:
        """The binding's `cycle_ref` is the checkpoint the wake carries."""
        return self.checkpoint_ref


@dataclass(frozen=True)
class ContinuationAuthority:
    """A read-only readback of the wake envelope, grant and Work Engine Run.

    Each field is a fact from an existing owner: the envelope (written by
    `runtime/wake.py`), the Agent Control grant/checkpoint, or Work Engine
    `runs`/`execution_contexts`. The producer builds this while it still holds
    the trusted stores; the consumer compares it to the receipt binding.
    """

    checkpoint_ref: str
    envelope_action_key: str
    envelope_work_item: str
    envelope_grant_ref: str
    envelope_delegation_ref: str
    envelope_checkpoint_ref: str
    envelope_expires_at: str
    run_id: str
    run_state: str
    run_worker: str
    execution_repo: str
    execution_revision: str
    execution_worktree: str
    execution_dirty: int
    grant_ref: str
    grant_delegated_by: str
    grant_valid_from: str
    grant_expires_at: str
    grant_revoked: bool
    receipt_sha256: str | None


@dataclass(frozen=True)
class DispatchDecision:
    decision: str
    reasons: tuple[str, ...]
    because: str
    target: Mapping[str, Any]
    receipt: Mapping[str, Any] | None
    admission: Mapping[str, Any] | None
    work_decision: Mapping[str, Any] | None
    child_report_ignored: bool
    eligible_for_qa_review: bool
    parent_accepted: bool
    qa_signature: bool
    requires_independent_qa: bool
    run_state_advanced: bool
    qa_gate_advanced: bool
    wiring: str
    t10_status: str
    work_engine_consumption: str
    evidence_level: str
    authority: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "because": self.because,
            "target": dict(self.target),
            "receipt": dict(self.receipt) if self.receipt is not None else None,
            "admission": dict(self.admission) if self.admission is not None else None,
            "work_decision": (
                dict(self.work_decision) if self.work_decision is not None else None),
            "child_report_ignored": self.child_report_ignored,
            "eligible_for_qa_review": self.eligible_for_qa_review,
            "parent_accepted": self.parent_accepted,
            "qa_signature": self.qa_signature,
            "requires_independent_qa": self.requires_independent_qa,
            "run_state_advanced": self.run_state_advanced,
            "qa_gate_advanced": self.qa_gate_advanced,
            "wiring": self.wiring,
            "t10_status": self.t10_status,
            "work_engine_consumption": self.work_engine_consumption,
            "evidence_level": self.evidence_level,
            "authority": self.authority,
        }


def binding_payload(expected: ExpectedDispatch) -> dict[str, str]:
    return {field: str(getattr(expected, field)) for field in BINDING_FIELDS}


def canonical_binding(expected: ExpectedDispatch) -> str:
    """The producer writes this exact string as stdin and in the dispatch env."""
    return json.dumps(
        binding_payload(expected), sort_keys=True, separators=(",", ":"))


def read_bounded_receipt(receipts_dir, action_key: str) -> BoundedReceipt | None:
    """Read one receipt and its stdin/stdout/stderr, refusing unsafe paths.

    Mirrors `runtime/bounded_invocation.invoke_once`: the directory must be
    private, the receipt must not be a symlink, and missing artifact files are
    returned as None rather than invented.  A missing receipt is None; an
    unsafe path is ValueError.
    """
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", str(action_key)):
        raise ValueError("safe action key required")
    root = Path(receipts_dir)
    if not root.is_absolute() or root.is_symlink() or not root.is_dir():
        raise ValueError("absolute, non-symlink receipts directory required")
    if root.stat().st_mode & 0o077:
        raise ValueError("receipt directory must be private (0700)")

    path = root / (str(action_key) + ".json")
    if path.is_symlink():
        raise ValueError("symlink receipt refused")
    if not path.is_file():
        return None
    raw = path.read_bytes()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"receipt is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("receipt is not a JSON object")

    def artifact(extension: str) -> bytes | None:
        item = root / (str(action_key) + "." + extension)
        if item.is_symlink():
            raise ValueError(f"symlink {extension} artifact refused")
        if not item.is_file():
            return None
        return item.read_bytes()

    return BoundedReceipt(
        action_key=str(action_key),
        raw_bytes=raw,
        receipt=payload,
        stdin_bytes=artifact("stdin"),
        stdout_bytes=artifact("stdout"),
        stderr_bytes=artifact("stderr"),
        path=str(path),
    )


def receipt_anchor(receipt: BoundedReceipt) -> str | None:
    """The bytes-anchor the producer must record in the DISPATCHED evidence.

    Missing artifacts answer None: an anchor over invented empty bytes would
    say a receipt was complete when it was not.
    """
    parts = (
        ("receipt", receipt.raw_bytes),
        ("stdin", receipt.stdin_bytes),
        ("stdout", receipt.stdout_bytes),
        ("stderr", receipt.stderr_bytes),
    )
    if any(data is None for _, data in parts):
        return None
    digest = hashlib.sha256()
    for label, data in parts:
        digest.update(label.encode("utf-8"))
        digest.update(b"\x00")
        digest.update(data)
        digest.update(b"\x00")
    return digest.hexdigest()


def evaluate_dispatch(
    expected: ExpectedDispatch,
    admission: AdmissionSnapshot | None,
    grant: GrantSnapshot | None,
    receipt: BoundedReceipt | None,
    work_authority,
    *,
    untrusted_child_report: str | None = None,
) -> DispatchDecision:
    """Decide whether this runner receipt may move a Parent/QA gate.

    A child's stdout/stderr is digest material only.  The decision comes from
    the receipt's recorded process_exit_code, its input/output digests, the
    admission/grant records, and Work Engine's Run/evidence — never from text.
    """
    _validate_expected(expected)
    if receipt is None:
        reasons = _Reasons()
        reasons.add(MISSING_RECEIPT, _UNKNOWN)
        return _decision(expected, reasons, None, admission, None, None,
                         untrusted_child_report)

    reasons = _Reasons()
    payload = receipt.receipt
    anchor = receipt_anchor(receipt)

    if not _receipt_is_well_formed(payload):
        reasons.add(INVALID_RECEIPT, _DENY)
        return _decision(expected, reasons, receipt, admission, None, None,
                         untrusted_child_report)

    if str(payload.get("action_key")) != expected.action_key:
        reasons.add(ACTION_KEY_MISMATCH, _DENY)

    status = str(payload.get("status"))
    if status != "EXITED":
        reasons.add(RECEIPT_NOT_EXITED, _UNKNOWN)
    else:
        exit_code = payload.get("process_exit_code")
        if type(exit_code) is not int:
            reasons.add(INVALID_RECEIPT, _DENY)
        elif exit_code != 0:
            reasons.add(PROCESS_EXIT_NONZERO, _DENY)

    expected_digest = _input_digest(expected)
    if str(payload.get("input_digest")).lower() != expected_digest.lower():
        reasons.add(INPUT_DIGEST_MISMATCH, _DENY)

    if receipt.stdin_bytes is None or receipt.stdout_bytes is None or receipt.stderr_bytes is None:
        reasons.add(MISSING_ARTIFACT, _UNKNOWN)
    else:
        if receipt.stdin_bytes != expected.input_text.encode("utf-8"):
            reasons.add(STDIN_ARTIFACT_MISMATCH, _DENY)
        recorded = payload.get("output_sha256")
        if (not isinstance(recorded, Mapping)
                or set(recorded) != {"stdout", "stderr"}
                or any(not _is_digest(recorded.get(name)) for name in ("stdout", "stderr"))):
            reasons.add(INVALID_RECEIPT, _DENY)
        else:
            actual = {
                "stdout": hashlib.sha256(receipt.stdout_bytes).hexdigest(),
                "stderr": hashlib.sha256(receipt.stderr_bytes).hexdigest(),
            }
            if any(str(recorded[name]).lower() != actual[name] for name in actual):
                reasons.add(OUTPUT_DIGEST_MISMATCH, _DENY)

    parsed_binding = _parse_binding(receipt.stdin_bytes)
    if parsed_binding is None:
        reasons.add(MISSING_DISPATCH_BINDING, _BLOCKED)
    else:
        _check_binding(expected, parsed_binding, reasons)

    _check_admission(expected, admission, receipt, payload, anchor, reasons)
    _check_grant(expected, grant, payload, reasons)
    work = _check_work(expected, work_authority, payload, reasons)

    return _decision(expected, reasons, receipt, admission, work, anchor,
                     untrusted_child_report)


class _Reasons:
    def __init__(self) -> None:
        self.codes: list[str] = []
        self.has_deny = False
        self.has_unknown = False
        self.has_blocked = False

    def add(self, code: str, severity: str) -> None:
        if code not in self.codes:
            self.codes.append(code)
        if severity == _DENY:
            self.has_deny = True
        elif severity == _UNKNOWN:
            self.has_unknown = True
        elif severity == _BLOCKED:
            self.has_blocked = True

    def decision(self) -> str:
        if self.has_deny:
            return DECISION_DENY
        if self.has_unknown:
            return DECISION_UNKNOWN
        if self.has_blocked:
            return DECISION_BLOCKED_WIRING
        return DECISION_ELIGIBLE


def _decision(expected, reasons, receipt, admission, work, anchor,
              untrusted_child_report) -> DispatchDecision:
    decision = reasons.decision()
    eligible = decision == DECISION_ELIGIBLE
    if decision == DECISION_ELIGIBLE:
        because = _ELIGIBLE_BECAUSE
    elif decision == DECISION_BLOCKED_WIRING:
        because = _BLOCKED_BECAUSE
    else:
        because = "; ".join(_BECAUSE.get(code, code) for code in reasons.codes)
    if untrusted_child_report is not None:
        because += (
            ". The child's report was recorded as untrusted prose and was not "
            "read for this decision")

    wiring = (
        "BOUND" if eligible
        else "MISSING" if reasons.has_blocked
        else "NOT_ESTABLISHED"
    )
    receipt_summary = None
    if receipt is not None:
        payload = receipt.receipt
        receipt_summary = {
            "action_key": payload.get("action_key"),
            "status": payload.get("status"),
            "process_exit_code": payload.get("process_exit_code"),
            "input_digest": payload.get("input_digest"),
            "output_sha256": dict(payload.get("output_sha256") or {}),
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "path": receipt.path,
            "anchor": anchor,
        }
    admission_summary = None
    if admission is not None:
        admission_summary = {
            "cycle_ref": admission.cycle_ref,
            "action_key": admission.action_key,
            "grant_ref": admission.grant_ref,
            "phase": admission.phase,
            "admitted_at": admission.admitted_at,
        }
    return DispatchDecision(
        decision=decision,
        reasons=tuple(reasons.codes),
        because=because,
        target={
            "cycle_ref": expected.cycle_ref,
            "action_key": expected.action_key,
            "work_item_id": expected.work_item_id,
            "work_run_id": expected.work_run_id,
            "commit_sha": expected.commit_sha,
            "principal": expected.principal,
            "grant_ref": expected.grant_ref,
            "runner_identity": expected.runner_identity,
        },
        receipt=receipt_summary,
        admission=admission_summary,
        work_decision=work.as_dict() if work is not None else None,
        child_report_ignored=untrusted_child_report is not None,
        eligible_for_qa_review=eligible,
        parent_accepted=False,
        qa_signature=False,
        requires_independent_qa=True,
        run_state_advanced=False,
        qa_gate_advanced=False,
        wiring=wiring,
        t10_status=T10_STATUS_BLOCKED_WIRING,
        work_engine_consumption=WORK_ENGINE_CONSUMPTION_NOT_WIRED,
        evidence_level="LOCAL_BOUNDED_INVOCATION",
        authority=(
            "runtime/bounded_invocation.invoke_once + "
            "agent-control admission + work-engine:runs+evidence"),
    )


_RUN_STATES = frozenset({
    "ALLOCATED", "PREPARING", "RUNNING", "FINALIZING",
    "SUCCEEDED", "FAILED", "CANCELLED", "INVALID",
})
_TERMINAL_RUN_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "INVALID"})


def evaluate_continuation(
    expected: ExpectedContinuation,
    authority: ContinuationAuthority | None,
    receipt: BoundedReceipt | None,
    *,
    untrusted_child_report: str | None = None,
) -> DispatchDecision:
    """Decide a continuation engineering receipt against the real authority.

    The receipt must be the real `runtime/bounded_invocation.invoke_once` output
    for this exact action key. Its binding must name the envelope's checkpoint,
    Item and grant, the grant's principal, the unique open Work Engine Run, that
    Run's `worker`, and the execution_context revision equal to the granted
    worktree's exact HEAD. The anchor must match the bytes the producer recorded
    in the existing private report.

    The happy path is `ELIGIBLE` for independent QA only. Work Engine
    finalization still does not consume this proof, so `t10_status` stays
    `BLOCKED_WIRING`.
    """
    _validate_continuation(expected)
    reasons = _Reasons()
    if receipt is None:
        reasons.add(MISSING_RECEIPT, _UNKNOWN)
        return _continuation_decision(
            expected, reasons, None, authority, None, untrusted_child_report)

    payload, anchor, _parsed = _check_receipt_core(expected, receipt, reasons)
    _check_continuation_authority(expected, authority, payload, anchor, reasons)
    return _continuation_decision(
        expected, reasons, receipt, authority, anchor, untrusted_child_report)


def _check_receipt_core(expected, receipt: BoundedReceipt, reasons: _Reasons):
    """The receipt checks shared by both surfaces; no authority is consulted."""
    payload = receipt.receipt
    anchor = receipt_anchor(receipt)
    if not _receipt_is_well_formed(payload):
        reasons.add(INVALID_RECEIPT, _DENY)
        return payload, anchor, None

    if str(payload.get("action_key")) != expected.action_key:
        reasons.add(ACTION_KEY_MISMATCH, _DENY)

    status = str(payload.get("status"))
    if status != "EXITED":
        reasons.add(RECEIPT_NOT_EXITED, _UNKNOWN)
    else:
        exit_code = payload.get("process_exit_code")
        if type(exit_code) is not int:
            reasons.add(INVALID_RECEIPT, _DENY)
        elif exit_code != 0:
            reasons.add(PROCESS_EXIT_NONZERO, _DENY)

    expected_digest = _input_digest(expected)
    if str(payload.get("input_digest")).lower() != expected_digest.lower():
        reasons.add(INPUT_DIGEST_MISMATCH, _DENY)

    if (receipt.stdin_bytes is None or receipt.stdout_bytes is None
            or receipt.stderr_bytes is None):
        reasons.add(MISSING_ARTIFACT, _UNKNOWN)
    else:
        if receipt.stdin_bytes != expected.input_text.encode("utf-8"):
            reasons.add(STDIN_ARTIFACT_MISMATCH, _DENY)
        recorded = payload.get("output_sha256")
        if (not isinstance(recorded, Mapping)
                or set(recorded) != {"stdout", "stderr"}
                or any(not _is_digest(recorded.get(name))
                       for name in ("stdout", "stderr"))):
            reasons.add(INVALID_RECEIPT, _DENY)
        else:
            actual = {
                "stdout": hashlib.sha256(receipt.stdout_bytes).hexdigest(),
                "stderr": hashlib.sha256(receipt.stderr_bytes).hexdigest(),
            }
            if any(str(recorded[name]).lower() != actual[name] for name in actual):
                reasons.add(OUTPUT_DIGEST_MISMATCH, _DENY)

    parsed = _parse_binding(receipt.stdin_bytes)
    if parsed is None:
        reasons.add(MISSING_DISPATCH_BINDING, _BLOCKED)
    else:
        _check_binding(expected, parsed, reasons)
    return payload, anchor, parsed


def _check_continuation_authority(expected, authority, payload, anchor,
                                  reasons: _Reasons) -> None:
    if authority is None:
        reasons.add(MISSING_AUTHORITY, _UNKNOWN)
        return
    try:
        required = (
            authority.checkpoint_ref, authority.envelope_action_key,
            authority.envelope_work_item, authority.envelope_grant_ref,
            authority.envelope_delegation_ref, authority.envelope_checkpoint_ref,
            authority.envelope_expires_at, authority.run_id, authority.run_state,
            authority.run_worker, authority.execution_repo,
            authority.execution_revision, authority.execution_worktree,
            authority.grant_ref, authority.grant_delegated_by,
            authority.grant_valid_from, authority.grant_expires_at,
        )
        if any(not isinstance(value, str) or not value.strip()
               for value in required):
            reasons.add(INVALID_AUTHORITY, _DENY)
            return

        if authority.checkpoint_ref != expected.checkpoint_ref:
            reasons.add(CHECKPOINT_MISMATCH, _DENY)
        if authority.envelope_checkpoint_ref != expected.checkpoint_ref:
            reasons.add(ENVELOPE_MISMATCH, _DENY)
        if authority.envelope_work_item != expected.work_item_id:
            reasons.add(ENVELOPE_MISMATCH, _DENY)
        if authority.envelope_grant_ref != expected.grant_ref:
            reasons.add(ENVELOPE_MISMATCH, _DENY)
        if authority.envelope_delegation_ref != expected.principal:
            reasons.add(CROSS_PRINCIPAL, _DENY)

        if authority.run_id != expected.work_run_id:
            reasons.add(WORK_RUN_MISMATCH, _DENY)
        if authority.run_worker != expected.runner_identity:
            reasons.add(WORK_RUN_WORKER_MISMATCH, _DENY)
        state = authority.run_state.strip().upper()
        if state in _TERMINAL_RUN_STATES:
            reasons.add(WORK_RUN_TERMINAL, _DENY)
        elif state not in _RUN_STATES:
            reasons.add(WORK_RUN_INVALID, _DENY)

        if authority.execution_revision.lower() != expected.commit_sha.lower():
            reasons.add(EXECUTION_REVISION_MISMATCH, _DENY)
        try:
            if Path(authority.execution_worktree).resolve() != Path(
                    expected.worktree).resolve():
                reasons.add(EXECUTION_WORKTREE_MISMATCH, _DENY)
        except OSError:
            reasons.add(INVALID_AUTHORITY, _DENY)
        if bool(authority.execution_dirty):
            reasons.add(EXECUTION_DIRTY, _DENY)
        if not str(authority.execution_repo).strip():
            reasons.add(INVALID_AUTHORITY, _DENY)

        if authority.grant_ref != expected.grant_ref:
            reasons.add(CROSS_GRANT, _DENY)
        if authority.grant_delegated_by != expected.principal:
            reasons.add(CROSS_PRINCIPAL, _DENY)
        if authority.grant_revoked:
            reasons.add(GRANT_REVOKED, _DENY)

        valid_from = _instant(authority.grant_valid_from)
        expires_at = _instant(authority.grant_expires_at)
        started = _instant(payload.get("started_at"))
        finished = _instant(payload.get("finished_at"))
        if valid_from is None or expires_at is None:
            reasons.add(GRANT_WINDOW_UNREADABLE, _UNKNOWN)
        elif started is None or finished is None:
            reasons.add(CLOCK_UNREADABLE, _UNKNOWN)
        else:
            if started < valid_from:
                reasons.add(GRANT_NOT_YET_VALID, _DENY)
            if finished >= expires_at:
                reasons.add(EXPIRED_GRANT, _DENY)

        envelope_expiry = _instant(authority.envelope_expires_at)
        if envelope_expiry is None:
            reasons.add(CLOCK_UNREADABLE, _UNKNOWN)
        elif finished is not None and finished > envelope_expiry:
            reasons.add(ENVELOPE_EXPIRED, _DENY)

        recorded_anchor = authority.receipt_sha256
        if not recorded_anchor:
            reasons.add(MISSING_RECEIPT_ANCHOR, _BLOCKED)
        elif anchor is None:
            reasons.add(MISSING_ARTIFACT, _UNKNOWN)
        elif str(recorded_anchor).lower() != anchor.lower():
            reasons.add(RECEIPT_ANCHOR_MISMATCH, _DENY)
    except (AttributeError, TypeError, ValueError):
        reasons.add(INVALID_AUTHORITY, _DENY)


def _validate_continuation(expected: ExpectedContinuation) -> None:
    for name in (
        "checkpoint_ref", "work_item_id", "work_run_id", "principal",
        "grant_ref", "runner_identity", "worktree",
    ):
        value = getattr(expected, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"expected continuation {name} must be a nonempty string")
    if (not isinstance(expected.action_key, str)
            or not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", expected.action_key)):
        raise ValueError("expected continuation action_key is not a bounded key")
    if not isinstance(expected.commit_sha, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40}", expected.commit_sha):
        raise ValueError(
            "expected continuation commit_sha must be an exact full 40-hex SHA")
    if (not isinstance(expected.argv, (tuple, list)) or not expected.argv
            or any(not isinstance(item, str) or not item or "\x00" in item
                   for item in expected.argv)):
        raise ValueError("expected continuation argv must be nonempty strings")
    if not isinstance(expected.cwd, str) or not Path(expected.cwd).is_absolute():
        raise ValueError("expected continuation cwd must be absolute")
    if not Path(expected.worktree).is_absolute():
        raise ValueError("expected continuation worktree must be absolute")
    if type(expected.timeout) is not int or expected.timeout <= 0:
        raise ValueError("expected continuation timeout must be positive")
    if type(expected.max_output_bytes) is not int or expected.max_output_bytes <= 0:
        raise ValueError("expected continuation max_output_bytes must be positive")
    if not isinstance(expected.readonly, bool):
        raise ValueError("expected continuation readonly must be a bool")
    if not isinstance(expected.input_text, str):
        raise ValueError("expected continuation input_text must be a string")
    if not isinstance(expected.env, Mapping) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in expected.env.items()):
        raise ValueError("expected continuation env must map strings to strings")


def _continuation_decision(expected, reasons, receipt, authority, anchor,
                           untrusted_child_report) -> DispatchDecision:
    decision = reasons.decision()
    eligible = decision == DECISION_ELIGIBLE
    if eligible:
        because = (
            "the runner receipt anchors exit 0, the canonical continuation "
            "binding, the unique open Work Engine Run, the exact "
            "execution_context revision and the artifact digests; this is "
            "eligibility for independent QA review, not a Parent acceptance "
            "and not a QA signature")
    elif decision == DECISION_BLOCKED_WIRING:
        because = _BLOCKED_BECAUSE
    else:
        because = "; ".join(_BECAUSE.get(code, code) for code in reasons.codes)
    if untrusted_child_report is not None:
        because += (
            ". The child's report was recorded as untrusted prose and was not "
            "read for this decision")

    wiring = (
        "BOUND" if eligible
        else "MISSING" if reasons.has_blocked
        else "NOT_ESTABLISHED"
    )
    receipt_summary = None
    if receipt is not None:
        payload = receipt.receipt
        receipt_summary = {
            "action_key": payload.get("action_key"),
            "status": payload.get("status"),
            "process_exit_code": payload.get("process_exit_code"),
            "input_digest": payload.get("input_digest"),
            "output_sha256": dict(payload.get("output_sha256") or {}),
            "started_at": payload.get("started_at"),
            "finished_at": payload.get("finished_at"),
            "path": receipt.path,
            "anchor": anchor,
        }
    authority_summary = None
    if authority is not None:
        authority_summary = {
            "checkpoint_ref": authority.checkpoint_ref,
            "work_run_id": authority.run_id,
            "run_state": authority.run_state,
            "run_worker": authority.run_worker,
            "execution_revision": authority.execution_revision,
            "execution_worktree": authority.execution_worktree,
            "grant_ref": authority.grant_ref,
            "principal": authority.grant_delegated_by,
        }
    return DispatchDecision(
        decision=decision,
        reasons=tuple(reasons.codes),
        because=because,
        target={
            "checkpoint_ref": expected.checkpoint_ref,
            "action_key": expected.action_key,
            "work_item_id": expected.work_item_id,
            "work_run_id": expected.work_run_id,
            "commit_sha": expected.commit_sha,
            "principal": expected.principal,
            "grant_ref": expected.grant_ref,
            "runner_identity": expected.runner_identity,
            "worktree": expected.worktree,
        },
        receipt=receipt_summary,
        admission=authority_summary,
        work_decision=None,
        child_report_ignored=untrusted_child_report is not None,
        eligible_for_qa_review=eligible,
        parent_accepted=False,
        qa_signature=False,
        requires_independent_qa=True,
        run_state_advanced=False,
        qa_gate_advanced=False,
        wiring=wiring,
        t10_status=T10_STATUS_BLOCKED_WIRING,
        work_engine_consumption=WORK_ENGINE_CONSUMPTION_NOT_WIRED,
        evidence_level="LOCAL_OFFLINE_SYNTHETIC",
        authority=(
            "runtime/wake envelope + agent-control grant/checkpoint + "
            "work-engine:runs+execution_contexts + "
            "runtime/bounded_invocation.invoke_once"),
    )


def _validate_expected(expected: ExpectedDispatch) -> None:
    for name in (
        "cycle_ref", "action_key", "work_item_id", "work_run_id",
        "principal", "grant_ref", "runner_identity", "finalize_command",
    ):
        value = getattr(expected, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"expected {name} must be a nonempty string")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", expected.action_key):
        raise ValueError("expected action_key must match invoke_once's key rule")
    if not isinstance(expected.commit_sha, str) or not re.fullmatch(
            r"[0-9a-fA-F]{40}", expected.commit_sha):
        raise ValueError("expected commit_sha must be an exact full 40-hex git SHA")
    if not isinstance(expected.argv, (tuple, list)) or not expected.argv or any(
            not isinstance(item, str) or not item or "\x00" in item
            for item in expected.argv):
        raise ValueError("expected argv must be a nonempty list of nonempty strings")
    if not isinstance(expected.cwd, str) or not Path(expected.cwd).is_absolute():
        raise ValueError("expected cwd must be absolute")
    if type(expected.timeout) is not int or expected.timeout <= 0:
        raise ValueError("expected timeout must be a positive integer")
    if type(expected.max_output_bytes) is not int or expected.max_output_bytes <= 0:
        raise ValueError("expected max_output_bytes must be a positive integer")
    if not isinstance(expected.readonly, bool):
        raise ValueError("expected readonly must be a bool")
    if not isinstance(expected.input_text, str):
        raise ValueError("expected input_text must be a string")
    if not isinstance(expected.env, Mapping) or any(
            not isinstance(key, str) or not re.fullmatch(
                r"[A-Za-z_][A-Za-z0-9_]*", key)
            or not isinstance(value, str) or "\x00" in value
            for key, value in expected.env.items()):
        raise ValueError("expected env must map portable names to strings")
    if expected.selection_digest is not None and (
            not isinstance(expected.selection_digest, str)
            or not expected.selection_digest):
        raise ValueError("expected selection_digest must be a nonempty string")


def _receipt_is_well_formed(payload: Mapping[str, Any]) -> bool:
    for key in ("action_key", "status", "started_at", "finished_at", "input_digest"):
        if not isinstance(payload.get(key), str) or not payload[key]:
            return False
    if not _is_digest(payload.get("input_digest")):
        return False
    if str(payload.get("status")) not in RECEIPT_STATUSES:
        return False
    if payload.get("process_exit_code") is not None and (
            type(payload.get("process_exit_code")) is not int):
        return False
    return True


def _is_digest(value) -> bool:
    return isinstance(value, str) and bool(re.fullmatch(r"[0-9a-fA-F]{64}", value))


def _input_digest(expected: ExpectedDispatch) -> str:
    payload = {
        "argv": list(expected.argv),
        "cwd": str(Path(expected.cwd).resolve()),
        "input": expected.input_text,
        "readonly": expected.readonly,
        "timeout": expected.timeout,
        "max_output_bytes": expected.max_output_bytes,
        "env": dict(expected.env),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _parse_binding(stdin_bytes: bytes | None) -> dict[str, str] | None:
    if stdin_bytes is None:
        return None
    try:
        value = json.loads(stdin_bytes.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(value, dict):
        return None
    if not all(isinstance(value.get(field), str) and value[field]
               for field in BINDING_FIELDS):
        return None
    return {field: value[field] for field in BINDING_FIELDS}


def _check_binding(expected, parsed: Mapping[str, str], reasons: _Reasons) -> None:
    mapping = {
        "cycle_ref": CYCLE_MISMATCH,
        "action_key": ACTION_KEY_MISMATCH,
        "work_item_id": CROSS_RUN,
        "work_run_id": CROSS_RUN,
        "commit_sha": STALE_SHA,
        "principal": CROSS_PRINCIPAL,
        "grant_ref": CROSS_GRANT,
        "runner_identity": RUNNER_IDENTITY_MISMATCH,
    }
    wanted = binding_payload(expected)
    for field, code in mapping.items():
        if parsed[field] != wanted[field]:
            reasons.add(code, _DENY)


def _check_admission(expected, admission, receipt, payload, anchor,
                     reasons: _Reasons) -> None:
    if admission is None:
        reasons.add(MISSING_ADMISSION, _UNKNOWN)
        return
    try:
        if admission.cycle_ref != expected.cycle_ref:
            reasons.add(CYCLE_MISMATCH, _DENY)
        if admission.action_key != expected.action_key:
            reasons.add(ACTION_KEY_MISMATCH, _DENY)
        if admission.grant_ref != expected.grant_ref:
            reasons.add(CROSS_GRANT, _DENY)
        if admission.retired_at:
            reasons.add(ADMISSION_RETIRED, _DENY)
        if str(admission.phase).upper() != "DISPATCHED":
            reasons.add(ADMISSION_NOT_DISPATCHED, _UNKNOWN)

        reservation = admission.reservation
        if (reservation.get("wall_seconds") != expected.timeout
                or reservation.get("max_output_bytes") != expected.max_output_bytes):
            reasons.add(RESERVATION_MISMATCH, _DENY)

        selected = _selected_ids(admission.selection)
        if expected.work_item_id not in selected:
            reasons.add(SELECTION_DOES_NOT_NAME_ITEM, _DENY)
        if expected.selection_digest is not None:
            if admission.selection.get("selection_digest") != expected.selection_digest:
                reasons.add(SELECTION_DIGEST_MISMATCH, _DENY)

        confirmation = admission.confirmations.get("DISPATCHED")
        if not isinstance(confirmation, Mapping):
            reasons.add(ADMISSION_NOT_DISPATCHED, _UNKNOWN)
            return
        if str(confirmation.get("action_key")) != expected.action_key:
            reasons.add(DISPATCH_CONFIRMATION_MISMATCH, _DENY)
        if str(confirmation.get("status")) != str(payload.get("status")):
            reasons.add(DISPATCH_CONFIRMATION_MISMATCH, _DENY)
        if str(confirmation.get("input_digest")).lower() != str(
                payload.get("input_digest")).lower():
            reasons.add(DISPATCH_CONFIRMATION_MISMATCH, _DENY)

        recorded_anchor = confirmation.get("receipt_sha256")
        if not recorded_anchor:
            reasons.add(MISSING_RECEIPT_ANCHOR, _BLOCKED)
        elif anchor is None:
            reasons.add(MISSING_ARTIFACT, _UNKNOWN)
        elif str(recorded_anchor).lower() != anchor.lower():
            reasons.add(RECEIPT_ANCHOR_MISMATCH, _DENY)

        admitted = _instant(admission.admitted_at)
        started = _instant(payload.get("started_at"))
        finished = _instant(payload.get("finished_at"))
        if admitted is None or started is None or finished is None:
            reasons.add(CLOCK_UNREADABLE, _UNKNOWN)
        elif started < admitted:
            reasons.add(STALE_RECEIPT, _DENY)
        elif finished < started:
            reasons.add(CLOCK_UNREADABLE, _UNKNOWN)
    except (AttributeError, TypeError):
        reasons.add(INVALID_ADMISSION, _DENY)


def _selected_ids(selection: Mapping[str, Any]) -> set[str]:
    out: set[str] = set()
    for entry in selection.get("selected") or []:
        if isinstance(entry, str):
            out.add(entry)
        elif isinstance(entry, Mapping):
            for key in ("id", "item", "item_id", "ref"):
                if isinstance(entry.get(key), str):
                    out.add(entry[key])
                    break
    return out


def _check_grant(expected, grant, payload, reasons: _Reasons) -> None:
    if grant is None:
        reasons.add(MISSING_GRANT, _UNKNOWN)
        return
    if grant.grant_ref != expected.grant_ref:
        reasons.add(CROSS_GRANT, _DENY)
    if grant.delegated_by != expected.principal:
        reasons.add(CROSS_PRINCIPAL, _DENY)
    if grant.revoked:
        reasons.add(GRANT_REVOKED, _DENY)
    valid_from = _instant(grant.valid_from)
    expires_at = _instant(grant.expires_at)
    if valid_from is None or expires_at is None:
        reasons.add(GRANT_WINDOW_UNREADABLE, _UNKNOWN)
        return
    started = _instant(payload.get("started_at"))
    finished = _instant(payload.get("finished_at"))
    if started is not None and started < valid_from:
        reasons.add(GRANT_NOT_YET_VALID, _DENY)
    if finished is not None and finished >= expires_at:
        reasons.add(EXPIRED_GRANT, _DENY)


def _check_work(expected, work_authority, payload, reasons: _Reasons):
    verdict = _verdict_module()
    if work_authority is None:
        reasons.add(MISSING_WORK_AUTHORITY, _UNKNOWN)
        return None
    work_expected = verdict.ExpectedAcceptance(
        item_id=expected.work_item_id,
        run_id=expected.work_run_id,
        revision=expected.commit_sha,
        principal=expected.principal,
        command=expected.finalize_command,
        recognized_runners={expected.principal: (expected.runner_identity,)},
    )
    work = verdict.evaluate_acceptance(work_expected, work_authority)
    if work.decision == verdict.DECISION_DENY:
        for reason in work.reasons:
            reasons.add("WORK_" + reason, _DENY)
    elif work.decision == verdict.DECISION_UNKNOWN:
        for reason in work.reasons:
            reasons.add("WORK_" + reason, _UNKNOWN)

    rows = tuple(work_authority.finalize_evidence)
    receipt_exit = payload.get("process_exit_code")
    if len(rows) == 1 and type(receipt_exit) is int:
        if rows[0].get("exit_code") != receipt_exit:
            reasons.add(EXIT_CODE_DISAGREEMENT, _DENY)
    return work


def _instant(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _verdict_module():
    name = "serveros_fleet_v1_verdict_evidence"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name("verdict_evidence.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
