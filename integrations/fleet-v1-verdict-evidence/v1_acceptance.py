#!/usr/bin/env python3
"""#237 T10 P1 — the single strict V1 acceptance path.

WHY THIS EXISTS
---------------
`verdict_evidence.evaluate_acceptance` is deliberately a LEGACY/UNSIGNED
compatibility gate: pre-#15 fixtures have no `authorization` marker and still
exercise the receipt-vs-prose behaviour. That compatibility must never become
V1 acceptance. This module is the only V1 positive path.

It composes:

* `finalization_result.evaluate_signed_finalization` over the authoritative Work
  Engine exact Run and durable `finalize.json` (T10_VERIFIED + t10_acceptable +
  a matching `runner_receipt` with a verified exit 0);
* a grant/epoch freshness **answer from a trusted parent**. The consumer does
  not compute grant freshness itself and never fabricates it. No parent means
  `BLOCKED_WIRING`; stale/unknown/revoked/epoch-unknown means no acceptance.

A clearly labelled `"test-double:"` parent answer is `ELIGIBLE_FOR_QA` only:
`v1_acceptable=False`, `production_status="BLOCKED_TRUST_SOURCE"`,
`parent_accepted=False`, `qa_signature=False`. A `"trusted-parent:"` marker is
not authenticated in this milestone, so it remains `BLOCKED_WIRING`
(`V1_ACCEPTANCE_NOT_WIRED`); no caller string can create a production
acceptance.

The explicit `v1_required=True` context is required. A call with it false is
`BLOCKED_WIRING`, never a permissive legacy override.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

DECISION_DENY = "DENY"
DECISION_BLOCKED_WIRING = "BLOCKED_WIRING"
DECISION_ELIGIBLE_FOR_QA = "ELIGIBLE_FOR_QA"

V1_CONTEXT_REQUIRED = "V1_CONTEXT_REQUIRED"
GRANT_REF_REQUIRED = "GRANT_REF_REQUIRED"
NO_TRUSTED_PARENT_FOR_GRANT_EPOCH = "NO_TRUSTED_PARENT_FOR_GRANT_EPOCH"
GRANT_FRESHNESS_UNKNOWN = "GRANT_FRESHNESS_UNKNOWN"
STALE_GRANT_UNKNOWN = "STALE_GRANT_UNKNOWN"
GRANT_EPOCH_UNKNOWN = "GRANT_EPOCH_UNKNOWN"
GRANT_REVOKED = "GRANT_REVOKED"
GRANT_MISMATCH = "GRANT_MISMATCH"
ACTOR_MISMATCH = "ACTOR_MISMATCH"
INVALID_GRANT_FRESHNESS = "INVALID_GRANT_FRESHNESS"
CALLER_JSON_NOT_AUTHORITY = "CALLER_JSON_NOT_AUTHORITY"
UNRECOGNIZED_TRUST_SOURCE = "UNRECOGNIZED_TRUST_SOURCE"
V1_ACCEPTANCE_NOT_WIRED = "V1_ACCEPTANCE_NOT_WIRED"
TEST_DOUBLE_TRUST_SOURCE = "TEST_DOUBLE_TRUST_SOURCE"

_BECAUSE = {
    V1_CONTEXT_REQUIRED: "V1 acceptance requires an explicit v1_required context; there is no permissive legacy override",
    GRANT_REF_REQUIRED: "the V1 path needs the expected grant_ref",
    NO_TRUSTED_PARENT_FOR_GRANT_EPOCH: "no trusted parent supplied a grant/epoch freshness answer; this consumer cannot invent one",
    GRANT_FRESHNESS_UNKNOWN: "the trusted parent did not verify grant freshness",
    STALE_GRANT_UNKNOWN: "the grant is stale or its freshness is unknown",
    GRANT_EPOCH_UNKNOWN: "the trusted parent supplied no positive grant epoch",
    GRANT_REVOKED: "the grant is revoked",
    GRANT_MISMATCH: "the grant answer names another grant",
    ACTOR_MISMATCH: "the grant answer names another principal",
    INVALID_GRANT_FRESHNESS: "the grant freshness answer is not a usable record",
    CALLER_JSON_NOT_AUTHORITY: "a caller JSON object is not a trusted-parent grant answer",
    UNRECOGNIZED_TRUST_SOURCE: "the grant answer's trust source is not recognized",
    V1_ACCEPTANCE_NOT_WIRED: "no authenticated production trusted parent is wired; a test double cannot become V1 acceptance",
}
_ELIGIBLE_FOR_QA = (
    "the strict signed finalization and a labelled test-double grant/epoch "
    "answer agree; this is eligibility for independent QA review only, not V1 "
    "acceptance or a QA signature (the production trusted parent is absent)")


def _final_module():
    name = "serveros_fleet_v1_signed_finalization"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).with_name("finalization_result.py")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


FINAL = _final_module()


@dataclass(frozen=True)
class GrantFreshnessSnapshot:
    """A trusted parent's answer about the grant/epoch at execution time."""

    grant_ref: str
    principal: str
    verified: bool
    fresh: bool | None
    revoked: bool | None
    epoch: int | None
    verified_by: str
    checked_at: str


@dataclass(frozen=True)
class V1Decision:
    decision: str
    reasons: tuple[str, ...]
    because: str
    expected: Mapping[str, Any]
    readback: Mapping[str, Any] | None
    grant: Mapping[str, Any] | None
    eligible_for_qa_review: bool
    v1_acceptable: bool
    parent_accepted: bool
    qa_signature: bool
    requires_independent_qa: bool
    t10_status: str
    production_status: str
    evidence_level: str
    trusted_parent: str
    authority: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "decision": self.decision,
            "reasons": list(self.reasons),
            "because": self.because,
            "expected": dict(self.expected),
            "readback": dict(self.readback) if self.readback is not None else None,
            "grant": dict(self.grant) if self.grant is not None else None,
            "eligible_for_qa_review": self.eligible_for_qa_review,
            "v1_acceptable": self.v1_acceptable,
            "parent_accepted": self.parent_accepted,
            "qa_signature": self.qa_signature,
            "requires_independent_qa": self.requires_independent_qa,
            "t10_status": self.t10_status,
            "production_status": self.production_status,
            "evidence_level": self.evidence_level,
            "trusted_parent": self.trusted_parent,
            "authority": self.authority,
        }


def evaluate_v1_acceptance(
    expected,
    readback,
    *,
    grant: GrantFreshnessSnapshot | Any | None,
    expected_grant_ref: str,
    v1_required: bool = True,
) -> V1Decision:
    """The single strict V1 acceptance path.

    `expected`/`readback` are `finalization_result.ExpectedFinalization`/
    `WorkEngineFinalizeReadback`. `grant` is the trusted parent's grant/epoch
    answer; missing/stale/unknown/revoked/epoch-less answers are no acceptance.
    """
    if v1_required is not True:
        return _decision(
            DECISION_BLOCKED_WIRING, [V1_CONTEXT_REQUIRED],
            expected=None, readback=None, grant=None,
            eligible=False, v1_acceptable=False, t10_status="UNKNOWN",
            evidence_level="UNKNOWN", trusted_parent="NONE",
            because=_BECAUSE[V1_CONTEXT_REQUIRED])

    if not isinstance(expected_grant_ref, str) or not expected_grant_ref.strip():
        return _decision(
            DECISION_BLOCKED_WIRING, [GRANT_REF_REQUIRED],
            expected=None, readback=None, grant=None,
            eligible=False, v1_acceptable=False, t10_status="UNKNOWN",
            evidence_level="UNKNOWN", trusted_parent="NONE",
            because=_BECAUSE[GRANT_REF_REQUIRED])

    final = FINAL.evaluate_signed_finalization(expected, readback)
    base_expected = dict(final.expected)
    base_readback = dict(final.readback) if final.readback is not None else None

    if final.decision != FINAL.DECISION_ELIGIBLE:
        decision = (DECISION_BLOCKED_WIRING
                    if final.decision == FINAL.DECISION_BLOCKED
                    else DECISION_DENY)
        return _decision(
            decision, list(final.reasons), expected=base_expected,
            readback=base_readback, grant=None, eligible=False,
            v1_acceptable=False, t10_status=final.t10_status,
            evidence_level=final.evidence_level, trusted_parent="NONE",
            because=final.because)

    if grant is None:
        return _decision(
            DECISION_BLOCKED_WIRING, [NO_TRUSTED_PARENT_FOR_GRANT_EPOCH],
            expected=base_expected, readback=base_readback, grant=None,
            eligible=False, v1_acceptable=False, t10_status=final.t10_status,
            evidence_level=final.evidence_level, trusted_parent="NONE",
            because=_BECAUSE[NO_TRUSTED_PARENT_FOR_GRANT_EPOCH])

    if not isinstance(grant, GrantFreshnessSnapshot):
        return _decision(
            DECISION_DENY, [CALLER_JSON_NOT_AUTHORITY],
            expected=base_expected, readback=base_readback, grant=None,
            eligible=False, v1_acceptable=False, t10_status=final.t10_status,
            evidence_level=final.evidence_level, trusted_parent="NONE",
            because=_BECAUSE[CALLER_JSON_NOT_AUTHORITY])

    def stop(code: str, decision: str) -> V1Decision:
        return _decision(
            decision, [code], expected=base_expected, readback=base_readback,
            grant=_grant_summary(grant), eligible=False, v1_acceptable=False,
            t10_status=final.t10_status, evidence_level=final.evidence_level,
            trusted_parent="UNVERIFIED", because=_BECAUSE[code])

    for name in ("grant_ref", "principal", "verified_by", "checked_at"):
        value = getattr(grant, name)
        if not isinstance(value, str) or not value.strip():
            return stop(INVALID_GRANT_FRESHNESS, DECISION_BLOCKED_WIRING)
    if grant.verified is not True:
        return stop(GRANT_FRESHNESS_UNKNOWN, DECISION_BLOCKED_WIRING)
    if grant.fresh is not True:
        return stop(STALE_GRANT_UNKNOWN, DECISION_BLOCKED_WIRING)
    if grant.revoked is not False:
        return stop(GRANT_REVOKED, DECISION_DENY)
    if type(grant.epoch) is not int or grant.epoch < 1:
        return stop(GRANT_EPOCH_UNKNOWN, DECISION_BLOCKED_WIRING)
    if grant.grant_ref != expected_grant_ref:
        return stop(GRANT_MISMATCH, DECISION_DENY)
    if grant.principal != expected.actor_ref:
        return stop(ACTOR_MISMATCH, DECISION_DENY)

    if grant.verified_by.startswith("test-double:"):
        return _decision(
            DECISION_ELIGIBLE_FOR_QA, [TEST_DOUBLE_TRUST_SOURCE],
            expected=base_expected, readback=base_readback,
            grant=_grant_summary(grant), eligible=True, v1_acceptable=False,
            t10_status=final.t10_status, evidence_level=final.evidence_level,
            trusted_parent="TEST_DOUBLE", because=_ELIGIBLE_FOR_QA)
    if grant.verified_by.startswith("trusted-parent:"):
        return _decision(
            DECISION_BLOCKED_WIRING, [V1_ACCEPTANCE_NOT_WIRED],
            expected=base_expected, readback=base_readback,
            grant=_grant_summary(grant), eligible=False, v1_acceptable=False,
            t10_status=final.t10_status, evidence_level=final.evidence_level,
            trusted_parent="UNVERIFIED",
            because=_BECAUSE[V1_ACCEPTANCE_NOT_WIRED])
    return stop(UNRECOGNIZED_TRUST_SOURCE, DECISION_BLOCKED_WIRING)


def _grant_summary(grant: GrantFreshnessSnapshot) -> dict[str, Any]:
    return {
        "grant_ref": grant.grant_ref,
        "principal": grant.principal,
        "verified": grant.verified,
        "fresh": grant.fresh,
        "revoked": grant.revoked,
        "epoch": grant.epoch,
        "verified_by": grant.verified_by,
        "checked_at": grant.checked_at,
    }


def _decision(decision: str, reasons, *, expected, readback, grant,
              eligible: bool, v1_acceptable: bool, t10_status: str,
              evidence_level: str, trusted_parent: str,
              because: str) -> V1Decision:
    return V1Decision(
        decision=decision,
        reasons=tuple(reasons),
        because=because,
        expected=dict(expected) if expected is not None else {},
        readback=dict(readback) if readback is not None else None,
        grant=dict(grant) if grant is not None else None,
        eligible_for_qa_review=bool(eligible),
        v1_acceptable=bool(v1_acceptable),
        parent_accepted=False,
        qa_signature=False,
        requires_independent_qa=True,
        t10_status=t10_status,
        production_status="BLOCKED_TRUST_SOURCE",
        evidence_level=evidence_level,
        trusted_parent=trusted_parent,
        authority=(
            "work-engine:runs+evidence (strict signed finalization) + "
            "trusted-parent grant/epoch answer"),
    )
