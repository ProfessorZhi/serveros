#!/usr/bin/env python3
"""#237 T10 — PREVIEW-only `RunnerReceiptAuthority` over a real local receipt.

WHAT THIS IS
------------
Work Engine #14 (`e4c4a2b`) added `ServerOSStore.finalize_run_verified` and the
`RunnerReceiptAuthority.verify(receipt_ref, *, item_ref, run_ref, now)`
contract. It deliberately left the producer unbound (`BLOCKED_ORIGINAL_OWNER`).
This module is the T10 side of that seam, as an **opt-in PREVIEW-only**
adapter. It is not imported by any production path.

The trusted parent is the process that:

* admitted the continuation wake (the real envelope/grant/checkpoint);
* ran `runtime/bounded_invocation.invoke_once` and holds its receipt path,
  `receipt_sha256`, and the exact dispatch spec used for `input_digest`;
* read the Work Engine `runs`/`execution_contexts` row (unique open Run, exact
  revision, worktree, worker) — the same facts the #263 executor binds;
* mints an **opaque** `receipt_ref`. A path, a hash, a caller's JSON object or
  the child's text is never a `receipt_ref`.

`verify` resolves that opaque ref under the parent's admitted action only
(`expected.cwd` is the private receipts directory and `expected.action_key` is
the admitted action). It recomputes:

* `input_digest` from the exact argv/cwd/stdin/readonly/timeout/max-output/env;
* `receipt_sha256` over the receipt and its stdin/stdout/stderr bytes, and
  compares it to the anchor the parent recalled;
* `output_sha256` for both streams;
* every binding field against the envelope/grant/checkpoint/Work Engine facts.

It then returns the real `VerifiedRunReceipt` object through the factory the
caller supplies (the Work Engine class in the hermetic test). The verdict is
derived from `process_exit_code`; the child's `ALL TESTS PASSED` text is hashed
and never parsed.

WHY IT IS STILL BLOCKED
-----------------------
There is no production Work Engine finalize call site that consumes this
authority, and no independent QA signature. `PRODUCTION_STATUS` is
`BLOCKED_ORIGINAL_OWNER`; the bridge is a provenance proof, not a production
acceptance. If the parent cannot supply the receipt anchor, the Work Engine
Run/execution_context binding, the opaque ref or the receipt factory, the
constructor refuses `BLOCKED_TRUST_SOURCE` and names what is missing.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

STATUS = "PREVIEW_ONLY"
PRODUCTION_STATUS = "BLOCKED_ORIGINAL_OWNER"

_TERMINAL_RUN_STATES = frozenset({"SUCCEEDED", "FAILED", "CANCELLED", "INVALID"})
_BINDING_CODES = {
    "cycle_ref": "UNTRUSTED_RECEIPT",
    "action_key": "UNTRUSTED_RECEIPT",
    "work_item_id": "RECEIPT_ITEM_MISMATCH",
    "work_run_id": "RECEIPT_RUN_MISMATCH",
    "commit_sha": "STALE_SHA",
    "principal": "CROSS_PRINCIPAL",
    "grant_ref": "UNTRUSTED_RECEIPT",
    "runner_identity": "UNTRUSTED_RECEIPT",
}


class RunnerReceiptRefused(ValueError):
    """A refusal with the reason code the Work Engine refusal matrix names."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code
        self.detail = detail


def _bounded_module():
    name = "serveros_fleet_v1_bounded_provenance"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name("bounded_provenance.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


BOUNDED = _bounded_module()


@dataclass(frozen=True)
class LocalRunnerReceiptAuthority:
    """A parent-held verifier for one admitted continuation action.

    `expected` is the `ExpectedContinuation` (the exact dispatch plus the eight
    binding fields). `authority` is the `ContinuationAuthority` readback of the
    wake envelope, grant/checkpoint and Work Engine Run/execution_context.
    `expected_anchor` is the `receipt_sha256` the parent recalled from the
    `invoke_once` result. `receipt_factory` is the Work Engine
    `VerifiedRunReceipt` class (the caller supplies it, so this module does not
    import the other repository). `error_factory` lets the same refusal codes
    reach `finalize_run_verified` as its own `ReceiptError`.
    """

    expected: Any
    authority: Any | None
    receipt_ref: str
    expected_anchor: str | None
    receipt_factory: Callable[..., Any]
    error_factory: Callable[[str, str], Exception] | None = None

    def __post_init__(self) -> None:
        missing = []
        if self.expected is None:
            missing.append("ExpectedContinuation")
        if self.authority is None:
            missing.append("ContinuationAuthority (Work Engine Run/execution_context)")
        if not self.receipt_ref:
            missing.append("opaque receipt_ref")
        if not self.expected_anchor:
            missing.append("receipt_sha256 anchor")
        if self.receipt_factory is None:
            missing.append("VerifiedRunReceipt factory")
        if missing:
            self._refuse(
                "BLOCKED_TRUST_SOURCE",
                "the preview bridge needs the real invoke_once receipt anchor, "
                "the Work Engine Run/execution_context authority snapshot, the "
                "opaque receipt_ref and the VerifiedRunReceipt factory; missing: "
                + ", ".join(missing)
                + ". A caller path, child text or caller JSON cannot substitute")

    def _refuse(self, reason_code: str, detail: str) -> None:
        if self.error_factory is not None:
            raise self.error_factory(reason_code, detail)
        raise RunnerReceiptRefused(reason_code, detail)

    def verify(self, receipt_ref: str, *, item_ref: str, run_ref: str,
               now: float) -> Any:
        expected = self.expected
        authority = self.authority

        if str(receipt_ref) != self.receipt_ref:
            self._refuse(
                "UNTRUSTED_RECEIPT",
                "receipt_ref is not the opaque token this parent minted; a "
                "filesystem path, a hash or another action's key is not a "
                "receipt")
        if str(item_ref) != expected.work_item_id:
            self._refuse(
                "RECEIPT_ITEM_MISMATCH",
                f"this authority is for Item {expected.work_item_id!r}, not "
                f"{item_ref!r}")
        if str(run_ref) != expected.work_run_id:
            self._refuse(
                "RECEIPT_RUN_MISMATCH",
                f"this authority is for Run {expected.work_run_id!r}, not "
                f"{run_ref!r}")

        try:
            now_dt = _epoch_instant(now)
        except RunnerReceiptRefused as exc:
            self._refuse(exc.reason_code, exc.detail)
        valid_from = _instant(getattr(authority, "grant_valid_from", None))
        expires_at = _instant(getattr(authority, "grant_expires_at", None))
        envelope_expiry = _instant(getattr(authority, "envelope_expires_at", None))
        if valid_from is None or expires_at is None:
            self._refuse(
                "BLOCKED_TRUST_SOURCE",
                "the grant's valid_from/expires_at is not readable; a receipt "
                "cannot be bound to an unbounded grant")
        if envelope_expiry is None:
            self._refuse(
                "BLOCKED_TRUST_SOURCE",
                "the wake envelope's expires_at is not readable; a receipt "
                "that outlives its capability is not evidence")
        if authority.grant_revoked:
            self._refuse(
                "GRANT_REVOKED",
                f"grant {authority.grant_ref!r} is revoked; no receipt may be "
                f"accepted under it")
        if now_dt < valid_from:
            self._refuse(
                "GRANT_NOT_YET_VALID",
                f"grant {authority.grant_ref!r} starts at "
                f"{authority.grant_valid_from}, after {now_dt.isoformat()}")
        if now_dt >= expires_at:
            self._refuse(
                "GRANT_EXPIRED",
                f"grant {authority.grant_ref!r} closed at "
                f"{authority.grant_expires_at}, before {now_dt.isoformat()}")
        if now_dt > envelope_expiry:
            self._refuse(
                "ENVELOPE_EXPIRED",
                f"the wake envelope closed at {authority.envelope_expires_at}")

        if authority.checkpoint_ref != expected.checkpoint_ref:
            self._refuse("UNTRUSTED_RECEIPT", "the authority names another checkpoint")
        if authority.envelope_work_item != expected.work_item_id:
            self._refuse("RECEIPT_ITEM_MISMATCH",
                         "the wake envelope names another Item")
        if authority.envelope_grant_ref != expected.grant_ref:
            self._refuse("UNTRUSTED_RECEIPT",
                         "the wake envelope names another grant")
        if authority.envelope_delegation_ref != expected.principal:
            self._refuse("CROSS_PRINCIPAL",
                         "the wake envelope's delegation is not the expected principal")
        if authority.run_id != expected.work_run_id:
            self._refuse("RECEIPT_RUN_MISMATCH",
                         "the Work Engine Run is not the expected Run")
        if authority.run_worker != expected.runner_identity:
            self._refuse("UNTRUSTED_RECEIPT",
                         "the Work Engine Run worker is not the expected runner identity")
        if str(authority.run_state).strip().upper() in _TERMINAL_RUN_STATES:
            self._refuse("UNTRUSTED_RECEIPT",
                         "the Work Engine Run is already terminal")
        if str(authority.execution_revision).lower() != expected.commit_sha.lower():
            self._refuse("STALE_SHA",
                         "the execution_context revision is not the expected exact SHA")
        try:
            if Path(authority.execution_worktree).resolve() != Path(
                    expected.worktree).resolve():
                self._refuse("UNTRUSTED_RECEIPT",
                             "the execution_context is for another worktree")
        except OSError:
            self._refuse("BLOCKED_TRUST_SOURCE",
                         "the execution_context worktree is not readable")
        if bool(authority.execution_dirty):
            self._refuse("UNTRUSTED_RECEIPT",
                         "the execution_context records a dirty worktree")
        if not str(authority.execution_repo or "").strip():
            self._refuse("BLOCKED_TRUST_SOURCE",
                         "the execution_context names no repo")
        if authority.grant_ref != expected.grant_ref:
            self._refuse("UNTRUSTED_RECEIPT",
                         "the authority names another grant")
        if authority.grant_delegated_by != expected.principal:
            self._refuse("CROSS_PRINCIPAL",
                         "the grant's delegated_by is not the expected principal")

        receipt = BOUNDED.read_bounded_receipt(
            expected.cwd, expected.action_key)
        if receipt is None:
            self._refuse(
                "UNTRUSTED_RECEIPT",
                "no invoke_once receipt exists under the admitted action; a "
                "caller path or child text is not a receipt")
        anchor = BOUNDED.receipt_anchor(receipt)
        if (anchor is None
                or not isinstance(self.expected_anchor, str)
                or not re.fullmatch(r"[0-9a-fA-F]{64}", self.expected_anchor)
                or anchor.lower() != self.expected_anchor.lower()):
            self._refuse(
                "UNTRUSTED_RECEIPT",
                "the receipt/artifact bytes do not match the anchor the parent "
                "recalled; a rewritten receipt is not the one that ran")

        payload = receipt.receipt
        if str(payload.get("action_key")) != expected.action_key:
            self._refuse("UNTRUSTED_RECEIPT",
                         "the receipt names another action key")
        if str(payload.get("status")) != "EXITED":
            self._refuse("UNTRUSTED_RECEIPT",
                         "the receipt did not reach EXITED")
        exit_code = payload.get("process_exit_code")
        if type(exit_code) is not int:
            self._refuse("INVALID_RECEIPT",
                         "the receipt has no integer process_exit_code")
        if str(payload.get("input_digest")).lower() != _input_digest(expected).lower():
            self._refuse(
                "UNTRUSTED_RECEIPT",
                "the receipt's input_digest does not describe the parent's "
                "exact dispatch spec")
        if (receipt.stdin_bytes is None
                or receipt.stdin_bytes != expected.input_text.encode("utf-8")):
            self._refuse("UNTRUSTED_RECEIPT",
                         "the receipt's stdin artifact is not the parent's input")
        recorded = payload.get("output_sha256")
        if (not isinstance(recorded, dict)
                or set(recorded) != {"stdout", "stderr"}
                or any(not isinstance(recorded.get(name), str)
                       or not re.fullmatch(r"[0-9a-fA-F]{64}", recorded[name])
                       for name in ("stdout", "stderr"))):
            self._refuse("INVALID_RECEIPT",
                         "the receipt has no usable output_sha256 map")
        if (receipt.stdout_bytes is None or receipt.stderr_bytes is None
                or hashlib.sha256(receipt.stdout_bytes).hexdigest()
                != recorded["stdout"].lower()
                or hashlib.sha256(receipt.stderr_bytes).hexdigest()
                != recorded["stderr"].lower()):
            self._refuse("UNTRUSTED_RECEIPT",
                         "the receipt's output bytes do not match its digests")

        binding = _parse_binding(receipt.stdin_bytes)
        if binding is None:
            self._refuse("UNTRUSTED_RECEIPT",
                         "the receipt's stdin carries no T10 dispatch binding")
        wanted = BOUNDED.binding_payload(expected)
        for field, reason in _BINDING_CODES.items():
            if binding.get(field) != wanted[field]:
                self._refuse(
                    reason,
                    f"the receipt binding field {field!r} is "
                    f"{binding.get(field)!r}, the parent expected "
                    f"{wanted[field]!r}")

        started = _instant(payload.get("started_at"))
        finished = _instant(payload.get("finished_at"))
        if started is None or finished is None:
            self._refuse("UNTRUSTED_RECEIPT",
                         "the receipt has no readable execution window")
        if started < valid_from:
            self._refuse("GRANT_NOT_YET_VALID",
                         "the receipt started before the grant")
        if finished >= expires_at:
            self._refuse("GRANT_EXPIRED",
                         "the receipt finished at or after the grant expiry")
        if finished > envelope_expiry:
            self._refuse("ENVELOPE_EXPIRED",
                         "the receipt finished after the wake envelope expired")

        return self.receipt_factory(
            item_ref=expected.work_item_id,
            run_ref=expected.work_run_id,
            action_key=expected.action_key,
            input_digest=str(payload["input_digest"]),
            receipt_sha256=anchor,
            process_exit_code=exit_code,
            verdict="PASS" if exit_code == 0 else "FAIL",
            runner_identity=expected.runner_identity,
            commit_sha=expected.commit_sha,
        )


def _input_digest(expected) -> str:
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
    fields = BOUNDED.BINDING_FIELDS
    if not all(isinstance(value.get(field), str) and value[field]
               for field in fields):
        return None
    return {field: value[field] for field in fields}


def _instant(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else None


def _epoch_instant(value) -> datetime:
    try:
        return datetime.fromtimestamp(float(value), timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError) as exc:
        raise RunnerReceiptRefused(
            "BLOCKED_TRUST_SOURCE",
            f"the verifier instant {value!r} is not a readable epoch: {exc}") from exc
