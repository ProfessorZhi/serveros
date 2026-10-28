#!/usr/bin/env python3
"""#417 — out-of-band milestone delivery after the GPT turn is gone.

WHAT THIS IS
------------
A thin, offline-testable seam between three things that already exist:

    device facts        CloudCLI ``run_get`` / GitHub Actions / a signed-off phase
    the severity axis   ``runtime/notification-contract.yaml`` (INFO / ATTENTION / ACTION)
    the outbound outlet ``runtime/notifications.py#capability_of`` + an injected
                        ``HumanControlProvider`` (at Tokyo:
                        ``runtime/direct_lark_notify.py`` over
                        ``runtime/message_transport.py``)

It does NOT claim a §10 event name: the contract declares the closest INFO and
ACTION events DECLARED_ABSENT, and a milestone is not an alert. It uses the
contract's severity words and the same capability gate, then hands its own
deterministic sentence to the provider.

It classifies ONE bounded observation at a time, composes a safe sentence
(optionally with one event-activated summarizer call), and records whether the
outbound provider actually returned a message id. The same milestone observed
twice, or observed again after a process restart, is not sent twice.

WHAT IT REFUSES
---------------
* A Run that reads ``completed`` but whose native assistant output is missing,
  or whose boot id differs from the acknowledged boot, is UNKNOWN — never
  success. CloudCLI 1.41 answers ``completed``/``settled`` with
  ``lastAssistantMessage: null`` when the model call failed (#411/#416); that
  answer means "the process ended", not "there is a result".
* A CI matrix green is CI_PASSED. It is not RELEASE_ACCEPTED and not
  V1_PHASE_ACCEPTED; only a signed-off phase input can say those.
* YELLOW/recoverable and ordinary green updates stay on the card. They do not
  interrupt a person.
* A provider answer without a remote message id, or a provider exception, is
  UNKNOWN delivery. It is never written down as DELIVERED.
* Raw prompts, transcripts, provider payloads and credentials are not accepted
  as fields and are not copied into the message or the ledger. Free text is
  redacted for token-shaped strings before it leaves this module.
* This module never reads an environment recipient, never invokes the CLI and
  never opens a socket. The provider is injected by the Tokyo caller.

THE ONE MISSING PRODUCTION SEAM
-------------------------------
``SourceRead`` is the read-only seam: ``read(source, ref) -> payload``. This
module ships ``FixtureSource`` (offline JSON) and the three payload
normalizers. It does NOT ship an authenticated CloudCLI ``run_get`` client or a
GitHub Actions client; that is the single next wiring point. The tests prove an
offline end-to-end, not a live one.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import sqlite3
import sys
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol, runtime_checkable

SCHEMA = "serveros/fleet-milestone-notify.v1"

#: `runtime/message_transport.py` passes `idempotency_key[:50]` to the CLI and
#: to the service's own dedupe. The full ledger identity may be longer, so the
#: transport receives a scheme-tagged digest that fits that ceiling.
TRANSPORT_KEY_PREFIX = "ms1:"
TRANSPORT_KEY_LENGTH = 50

#: The four answers a milestone can have. ORDINARY_GREEN and SILENT are
#: deliberately separate from VERIFIED_GREEN: a successful daily tick is not a
#: milestone, and a still-running source has not answered yet.
VERIFIED_GREEN = "VERIFIED_GREEN"
ORDINARY_GREEN = "ORDINARY_GREEN"
YELLOW = "YELLOW"
RED = "RED"
UNKNOWN = "UNKNOWN"
SILENT = "SILENT"

RUN_COMPLETED = "RUN_COMPLETED"
CI_PASSED = "CI_PASSED"
PHASE_ACCEPTED = "PHASE_ACCEPTED"
PR_ACCEPTED = "PR_ACCEPTED"
TASK_COMPLETED = "TASK_COMPLETED"

#: Delivery-ledger states. Only DELIVERED means a provider named a message.
#: The value words are distinct from the milestone outcomes above so a caller
#: can tell "the milestone outcome is unknown" from "the send is unknown".
DELIVERED = "DELIVERED"
UNKNOWN_DELIVERY = "DELIVERY_UNKNOWN"
IN_FLIGHT = "IN_FLIGHT"
CARD_ONLY = "CARD_ONLY"
SILENT_STATE = "NO_EVENT"
DUPLICATE = "DUPLICATE"
UNKNOWN_REPLAY = "UNKNOWN_REPLAY"

_CLOUDCLI_STATUS = {
    "queued": "RUNNING",
    "running": "RUNNING",
    "completed": "COMPLETED",
    "succeeded": "COMPLETED",
    "failed": "FAILED",
    "cancelled": "CANCELLED",
    "canceled": "CANCELLED",
    "aborted": "CANCELLED",
}
_GITHUB_TERMINAL = frozenset({
    "success", "failure", "cancelled", "timed_out", "action_required",
    "neutral", "skipped", "stale", "startup_failure",
})
_PHASE_KINDS = frozenset({PHASE_ACCEPTED, PR_ACCEPTED, TASK_COMPLETED})

#: The positive milestone kinds #423 offers to the #417/#422 vocabulary freeze.
#: This is the evidence axis; it is not a delivery lifecycle. The set is
#: explicit so a new kind cannot be added without changing this declaration
#: and the contract tests that pin it.
MILESTONE_KINDS = frozenset({
    RUN_COMPLETED, CI_PASSED, PHASE_ACCEPTED, PR_ACCEPTED, TASK_COMPLETED,
})

#: Outcomes `send_text` may record, and the reason it is a closed set rather
#: than free text: these two words are what a batch aggregator sends, and the
#: seam's job is to make "a batch must not pretend to be a milestone" mechanical
#: instead of a convention. The set is disjoint from :data:`MILESTONE_KINDS`, so
#: no argument to the seam can produce a row a reader would take for a milestone.
#: A milestone is chosen by the classifier from evidence — never by a caller.
BATCH_SUMMARY = "BATCH_SUMMARY"
BATCH_ALERT = "BATCH_ALERT"
NON_MILESTONE_OUTCOMES = frozenset({BATCH_SUMMARY, BATCH_ALERT})
_SOURCES = frozenset({"cloudcli", "github_actions", "phase"})

_CLOUDCLI_ENVELOPE = frozenset({
    "device_ref", "session_ref", "observed_at", "bound_boot_id",
    "source_trusted", "milestone", "gates_required", "gates_passed",
    "summary", "refs", "run",
})
_GITHUB_ENVELOPE = frozenset({
    "device_ref", "observed_at", "source_trusted", "milestone",
    "gates_required", "gates_passed", "summary", "refs", "workflow_run",
})
_PHASE_ENVELOPE = frozenset({
    "milestone_kind", "key", "device_ref", "observed_at", "accepted",
    "accepted_by", "evidence", "source_trusted", "summary", "refs",
})

#: Token-shaped strings are replaced, not truncated: a receipt that carries half
#: a credential is still a credential leak. The patterns are deliberately broad
#: and only run over free text a caller wrote for a person.
_SECRET_PATTERNS = (
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"(?i)\baws_secret_access_key\s*[:=]\s*\S+"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"sk-[A-Za-z0-9]{20,}"),
    re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
               re.S),
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+"),
    re.compile(r"(?i)\b(?:password|passwd|secret|token|api[_-]?key|access_token)"
               r"\s*[:=]\s*\S+"),
)
_SECRET_WORDS = re.compile(
    r"(?i)\b(?:tenant_access_token|app_secret|client_secret|private_key|"
    r"authorization|credential)\b")


class MilestoneRefused(ValueError):
    """The observation is not a milestone this boundary can establish."""


# --------------------------------------------------------------------------- #
# sanitisation and closed-shape validation
# --------------------------------------------------------------------------- #
def _scrub(value: str) -> str:
    """Redact credentials and control characters from free text."""
    text = str(value)
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[redacted]", text)
    text = _SECRET_WORDS.sub("[redacted]", text)
    return "".join(ch for ch in text if ch == "\n" or ord(ch) >= 32)


def _text(value: Any, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MilestoneRefused(f"{name} is required")
    text = value.strip()
    if len(text) > maximum or any(ord(ch) < 32 for ch in text):
        raise MilestoneRefused(f"{name} is not a bounded single-line value")
    return text


def _optional_text(value: Any, name: str, maximum: int = 280) -> str | None:
    """Free text a caller wrote for a person, redacted before it is kept.

    Unlike ``_text`` this permits newlines: an alert detail is a short block of
    labelled lines. Control characters are still stripped by ``_scrub``.
    """
    if value in (None, ""):
        return None
    if not isinstance(value, str) or not value.strip():
        raise MilestoneRefused(f"{name} must be text")
    text = value.strip()
    if len(text) > maximum:
        raise MilestoneRefused(f"{name} exceeds {maximum} characters")
    return _scrub(text) or None


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise MilestoneRefused(f"{name} must be a boolean")
    return value


def _strings(value: Any, name: str, maximum: int = 50) -> tuple[str, ...]:
    if value in (None, ""):
        return ()
    if not isinstance(value, (list, tuple)) or len(value) > maximum:
        raise MilestoneRefused(f"{name} must be a bounded list")
    return tuple(_scrub(_text(item, f"{name}[{index}]", 128))
                 for index, item in enumerate(value))


def _device(value: Any) -> str:
    text = _text(value, "device_ref", 128)
    if not text.startswith("device:") or len(text) == len("device:"):
        raise MilestoneRefused(
            "device_ref must be a typed device:<id> reference; a bare id is not "
            "a stable subject")
    return text


def _at(value: Any) -> str:
    text = _text(value, "observed_at", 64)
    try:
        moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise MilestoneRefused("observed_at must be ISO8601") from None
    if moment.tzinfo is None:
        raise MilestoneRefused("observed_at must carry a timezone")
    return text


def _closed(payload: Any, allowed: frozenset[str], source: str) -> dict:
    """A source payload has a closed envelope.

    Raw provider payloads are not a place this boundary reads from. A field
    named ``prompt``, ``transcript`` or ``token`` is refused rather than
    ignored: silently dropping it would make a useful-looking payload look
    safe while a future field name walks through the same door.
    """
    if not isinstance(payload, dict):
        raise MilestoneRefused(f"{source} payload must be an object")
    extra = sorted(set(payload) - allowed)
    if extra:
        raise MilestoneRefused(
            f"{source} payload carries fields this boundary does not accept: "
            f"{extra}. Raw prompts, transcripts and credentials must not cross it")
    return payload


def _merge_refs(*groups: Any) -> tuple[str, ...]:
    ordered: dict[str, None] = {}
    for group in groups:
        if group is None:
            continue
        if isinstance(group, str):
            group = (group,) if group else ()
        for item in group:
            if isinstance(item, str) and item.strip():
                ordered[_scrub(item.strip())[:128]] = None
    return tuple(ordered)[:50]


# --------------------------------------------------------------------------- #
# device-side facts
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Evidence:
    """One bounded observation, already stripped of the provider's raw payload."""

    source: str
    key: str
    device_ref: str
    observed_at: str
    milestone_kind: str
    status: str
    major: bool
    source_trusted: bool
    native_output_trusted: bool
    boot_identity_trusted: bool
    gates_required: tuple[str, ...]
    gates_passed: tuple[str, ...]
    accepted: bool
    action_required: bool
    summary: str | None
    refs: tuple[str, ...]


def normalize_cloudcli_run(payload: dict) -> Evidence:
    """`run_get` plus the ACK binding, as the device can state it.

    The nested ``run`` is the source's own verbatim result. It is read for
    ``status``, ``runId``, ``bootId``, ``note`` and *whether* an assistant
    message exists — never for its content, which is where a prompt or a
    transcript would live.
    """
    payload = _closed(payload, _CLOUDCLI_ENVELOPE, "cloudcli")
    run = payload.get("run")
    if not isinstance(run, dict):
        raise MilestoneRefused("cloudcli payload needs the verbatim run_get result under 'run'")
    run_id = _scrub(_text(run.get("runId"), "run.runId", 128))
    raw_status = _text(run.get("status"), "run.status", 32).lower()
    status = _CLOUDCLI_STATUS.get(raw_status)
    if status is None:
        raise MilestoneRefused(f"run.status {raw_status!r} is not a CloudCLI status")

    session_ref = _text(payload.get("session_ref"), "session_ref", 256)
    run_session = run.get("sessionId")
    if isinstance(run_session, str) and run_session.strip() and run_session not in session_ref:
        raise MilestoneRefused(
            "session_ref does not name the session the Run answered for")

    bound_boot = str(payload.get("bound_boot_id") or "").strip()
    if bound_boot:
        _text(bound_boot, "bound_boot_id", 128)
    run_boot = run.get("bootId")
    boot_identity_trusted = (bool(bound_boot) and isinstance(run_boot, str)
                             and run_boot == bound_boot)

    message = run.get("lastAssistantMessage")
    content = message.get("content") if isinstance(message, dict) else None
    has_output = isinstance(content, str) and bool(content.strip())
    note = run.get("note")
    no_readable = isinstance(note, str) and "no readable assistant message" in note.lower()
    native_output_trusted = has_output and not no_readable

    return Evidence(
        source="cloudcli",
        key=run_id,
        device_ref=_device(payload.get("device_ref")),
        observed_at=_at(payload.get("observed_at")),
        milestone_kind=RUN_COMPLETED,
        status=status,
        major=_bool(payload.get("milestone"), "milestone"),
        source_trusted=_bool(payload.get("source_trusted"), "source_trusted"),
        native_output_trusted=native_output_trusted,
        boot_identity_trusted=boot_identity_trusted,
        gates_required=_strings(payload.get("gates_required"), "gates_required"),
        gates_passed=_strings(payload.get("gates_passed"), "gates_passed"),
        accepted=False,
        action_required=False,
        summary=_optional_text(payload.get("summary"), "summary"),
        refs=_merge_refs(payload.get("refs"), f"run:{run_id}", session_ref),
    )


def normalize_github_actions(payload: dict) -> Evidence:
    """One workflow run, as GitHub states it. No webhook body is needed."""
    payload = _closed(payload, _GITHUB_ENVELOPE, "github_actions")
    run = payload.get("workflow_run")
    if not isinstance(run, dict):
        raise MilestoneRefused("github_actions payload needs a 'workflow_run' object")
    run_id = run.get("id")
    if isinstance(run_id, bool) or not isinstance(run_id, (int, str)):
        raise MilestoneRefused("workflow_run.id is required")
    run_id = _text(str(run_id), "workflow_run.id", 64)
    name = _scrub(_text(run.get("name"), "workflow_run.name", 128))
    run_id = _scrub(run_id)
    phase = _text(run.get("status"), "workflow_run.status", 32).lower()
    conclusion: str | None = None
    if run.get("conclusion") is not None:
        conclusion = _text(run.get("conclusion"), "workflow_run.conclusion", 32).lower()
    if phase == "completed":
        status = conclusion or "unknown"
    else:
        status = phase
    head_sha = run.get("head_sha")
    sha_ref = (f"sha:{_text(head_sha, 'workflow_run.head_sha', 64)}"
               if isinstance(head_sha, str) and head_sha.strip() else None)
    return Evidence(
        source="github_actions",
        key=f"{name}:{run_id}",
        device_ref=_device(payload.get("device_ref")),
        observed_at=_at(payload.get("observed_at")),
        milestone_kind=CI_PASSED,
        status=status,
        major=_bool(payload.get("milestone"), "milestone"),
        source_trusted=_bool(payload.get("source_trusted"), "source_trusted"),
        # Not applicable to CI; the equivalent checks are source_trusted and
        # the required-gate set, which the classifier reads for this source.
        native_output_trusted=True,
        boot_identity_trusted=True,
        gates_required=_strings(payload.get("gates_required"), "gates_required"),
        gates_passed=_strings(payload.get("gates_passed"), "gates_passed"),
        accepted=False,
        action_required=(conclusion == "action_required"),
        summary=_optional_text(payload.get("summary"), "summary"),
        refs=_merge_refs(payload.get("refs"), f"gha:{run_id}", sha_ref),
    )


def normalize_phase_milestone(payload: dict) -> Evidence:
    """A milestone a person signed off, with the evidence that was signed."""
    payload = _closed(payload, _PHASE_ENVELOPE, "phase")
    kind = _text(payload.get("milestone_kind"), "milestone_kind", 64).upper()
    if kind not in _PHASE_KINDS:
        raise MilestoneRefused(f"milestone_kind {kind!r} is not a signed-off phase kind")
    key = _scrub(_text(payload.get("key"), "key", 128))
    accepted = _bool(payload.get("accepted"), "accepted")
    evidence = _strings(payload.get("evidence"), "evidence")
    accepted_by: str | None = None
    if accepted:
        accepted_by = _text(payload.get("accepted_by"), "accepted_by", 128)
        if not evidence:
            raise MilestoneRefused("an accepted milestone needs at least one evidence ref")
    elif payload.get("accepted_by") not in (None, ""):
        accepted_by = _text(payload.get("accepted_by"), "accepted_by", 128)
    return Evidence(
        source="phase",
        key=key,
        device_ref=_device(payload.get("device_ref")),
        observed_at=_at(payload.get("observed_at")),
        milestone_kind=kind,
        status="ACCEPTED" if accepted else "AWAITING_ACCEPTANCE",
        major=True,
        source_trusted=_bool(payload.get("source_trusted"), "source_trusted"),
        native_output_trusted=True,
        boot_identity_trusted=True,
        gates_required=(),
        gates_passed=(),
        accepted=accepted,
        action_required=False,
        summary=_optional_text(payload.get("summary"), "summary"),
        refs=_merge_refs(evidence, payload.get("refs"), accepted_by),
    )


def normalize(source: str, payload: dict) -> Evidence:
    """The one dispatcher the local observer calls."""
    if source == "cloudcli":
        return normalize_cloudcli_run(payload)
    if source == "github_actions":
        return normalize_github_actions(payload)
    if source == "phase":
        return normalize_phase_milestone(payload)
    raise MilestoneRefused(f"{source!r} is not a known source; known={sorted(_SOURCES)}")


# --------------------------------------------------------------------------- #
# milestone decision
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Decision:
    outcome: str
    deliver: bool
    severity: str | None
    why: str
    milestone_kind: str
    key: str
    source: str
    device_ref: str
    observed_at: str
    subject: str
    headline: str
    detail: str
    action_required: bool


def _subject(evidence: Evidence) -> str:
    if evidence.source == "cloudcli":
        return f"run:{evidence.key}"
    if evidence.source == "github_actions":
        return f"ci:{evidence.key}"
    return f"phase:{evidence.key}"


def _missing_gates(evidence: Evidence) -> tuple[str, ...]:
    return tuple(gate for gate in evidence.gates_required
                 if gate not in evidence.gates_passed)


def _gate_state(evidence: Evidence, gate: str) -> str:
    """The classifier's trust answer wins over a caller's claimed gate list.

    A payload can say ``gates_passed=["native-output"]`` while the run it
    carries has no assistant message. Printing PASS next to an UNKNOWN outcome
    would be the receipt contradicting the finding; these two gates are derived
    from the evidence itself.
    """
    if evidence.source == "cloudcli":
        if gate == "native-output":
            return "PASS" if evidence.native_output_trusted else "MISSING"
        if gate == "boot-identity":
            return "PASS" if evidence.boot_identity_trusted else "MISSING"
    return "PASS" if gate in evidence.gates_passed else "MISSING"


def _severity(word: str | None) -> str | None:
    """Validate against the contract's INFO/ATTENTION/ACTION axis.

    The milestone trigger axis is deliberately separate from the §10 event
    list. The contract declares the closest INFO event DECLARED_ABSENT (INFO is
    not worth a message on its own), and a milestone is not an alert, so this
    module uses the contract's severity vocabulary WITHOUT claiming an event
    name the contract says nobody raises.
    """
    if word is None:
        return None
    values = set((_notifications().contract().get("events", {})
                  .get("severity") or {}).values())
    if word not in values:
        raise MilestoneRefused(
            f"{word!r} is not a contract severity; known={sorted(values)}")
    return word


def _detail(evidence: Evidence, kind: str, next_action: str,
            severity: str | None) -> str:
    lines = [f"Kind: {kind}", f"Status: {evidence.status}"]
    if severity:
        lines.append(f"Severity: {severity}")
    if evidence.gates_required:
        lines.append("Gates: " + ", ".join(
            f"{gate}={_gate_state(evidence, gate)}"
            for gate in evidence.gates_required))
    if evidence.refs:
        lines.append("Evidence: " + ", ".join(evidence.refs))
    if evidence.summary:
        summary_lines = evidence.summary.splitlines()
        lines.append("Summary: " + summary_lines[0])
        # Indent continuation lines so a summary cannot introduce what looks
        # like another labelled block in the message.
        lines.extend("  " + line for line in summary_lines[1:])
    lines.append("Next: " + next_action)
    return "\n".join(lines)


def _make(evidence: Evidence, *, outcome: str, deliver: bool,
          severity: str | None, why: str, next_action: str,
          kind: str | None = None,
          action_required: bool = False) -> Decision:
    kind = kind or evidence.milestone_kind
    subject = _subject(evidence)
    severity = _severity(severity)
    return Decision(
        outcome=outcome, deliver=deliver, severity=severity, why=why,
        milestone_kind=kind, key=evidence.key,
        source=evidence.source, device_ref=evidence.device_ref,
        observed_at=evidence.observed_at, subject=subject,
        headline=f"{outcome} · {kind} · {subject} · {evidence.device_ref}",
        detail=_detail(evidence, kind, next_action, severity),
        action_required=action_required)


def _silent(evidence: Evidence, why: str) -> Decision:
    return _make(evidence, outcome=SILENT, deliver=False, severity=None,
                 why=why,
                 next_action="No message: the source has not reached a terminal state.")


def _ordinary_green(evidence: Evidence) -> Decision:
    return _make(
        evidence, outcome=ORDINARY_GREEN, deliver=False, severity="INFO",
        why="ORDINARY_GREEN",
        next_action="No out-of-band message; ordinary green is a card update.")


def _yellow(evidence: Evidence, why: str, next_action: str | None = None) -> Decision:
    if evidence.source == "github_actions":
        kind = "CI_NOT_PASSED"
    elif evidence.source == "phase":
        kind = "PHASE_AWAITING_ACCEPTANCE"
    else:
        kind = None
    return _make(
        evidence, outcome=YELLOW, deliver=False, severity="ATTENTION",
        why=why, kind=kind,
        next_action=next_action or
        "No out-of-band message; a recoverable warning updates the card/digest.")


def _red(evidence: Evidence, why: str, *, action_required: bool) -> Decision:
    if evidence.source == "cloudcli":
        kind = "RUN_CANCELLED" if evidence.status == "CANCELLED" else "RUN_FAILED"
    elif evidence.source == "github_actions":
        kind = "CI_ACTION_REQUIRED" if action_required else "CI_FAILED"
    else:
        kind = "PHASE_REJECTED"
    return _make(
        evidence, outcome=RED, deliver=True,
        severity="ACTION" if action_required else "ATTENTION",
        why=why, kind=kind, action_required=action_required,
        next_action="Action required: inspect the failure and decide whether to retry or escalate.")


def _unknown(evidence: Evidence, why: str) -> Decision:
    if evidence.source == "cloudcli":
        kind = "RUN_OUTCOME_UNKNOWN"
    elif evidence.source == "github_actions":
        kind = "CI_OUTCOME_UNKNOWN"
    else:
        kind = "PHASE_UNVERIFIED"
    return _make(
        evidence, outcome=UNKNOWN, deliver=True, severity="ACTION",
        why=why, kind=kind,
        next_action="Do not treat this as success. Inspect the native result and boot "
                    "identity; a person must decide.")


def _verified_green(evidence: Evidence, why: str) -> Decision:
    return _make(
        evidence, outcome=VERIFIED_GREEN, deliver=True, severity="INFO",
        why=why,
        next_action="No action; this milestone is recorded once. Ordinary green stays "
                    "on the card.")


def decide(evidence: Evidence) -> Decision:
    """Classify one observation, deterministically.

    The rule order is the acceptance order: trust before success, terminal
    before green, and a claimed milestone without its required gates is UNKNOWN
    rather than a partial green.
    """
    if not isinstance(evidence, Evidence):
        raise MilestoneRefused("decide needs Evidence")
    if not evidence.source_trusted:
        # An unsigned/spoofed observation must not be able to raise an outbound
        # alert. Trust is the first gate for every source and every outcome.
        return _silent(evidence, "SOURCE_NOT_TRUSTED")

    if evidence.source == "cloudcli":
        if evidence.status == "RUNNING":
            return _silent(evidence, "RUN_NOT_TERMINAL")
        if evidence.status in {"FAILED", "CANCELLED"}:
            why = "RUN_FAILED" if evidence.status == "FAILED" else "RUN_CANCELLED"
            return _red(evidence, why, action_required=False)
        if not evidence.native_output_trusted:
            return _unknown(evidence, "RUN_COMPLETED_WITHOUT_NATIVE_OUTPUT")
        if not evidence.boot_identity_trusted:
            return _unknown(evidence, "RUN_COMPLETED_WITHOUT_TRUSTED_BOOT")
        if _missing_gates(evidence):
            return _unknown(evidence, "REQUIRED_GATES_NOT_EVIDENCED")
        if not evidence.major:
            return _ordinary_green(evidence)
        return _verified_green(evidence, RUN_COMPLETED)

    if evidence.source == "github_actions":
        status = evidence.status
        if status in {"queued", "in_progress", "requested", "waiting", "pending"}:
            return _silent(evidence, "CI_NOT_TERMINAL")
        if status not in _GITHUB_TERMINAL:
            # A completed workflow whose conclusion we cannot read is not a
            # green tick and not a running job: it is an unestablished outcome.
            return _unknown(evidence, "CI_CONCLUSION_UNKNOWN")
        if status == "action_required":
            return _red(evidence, "ACTION_REQUIRED", action_required=True)
        if status in {"failure", "timed_out", "startup_failure", "cancelled"}:
            return _red(evidence, f"CI_{status.upper()}", action_required=False)
        if status in {"neutral", "skipped", "stale"}:
            return _yellow(evidence, "CI_DID_NOT_PRODUCE_A_PASS")
        # success
        if _missing_gates(evidence):
            return _unknown(evidence, "REQUIRED_GATES_NOT_EVIDENCED")
        if not evidence.major:
            return _ordinary_green(evidence)
        # CI_PASSED, never RELEASE_ACCEPTED: a matrix is evidence for a gate,
        # not a person's acceptance of the release.
        return _verified_green(evidence, CI_PASSED)

    # phase
    if not evidence.accepted:
        return _yellow(
            evidence, "PHASE_NOT_ACCEPTED",
            "No out-of-band message; the phase stays on the card until it is signed off.")
    return _verified_green(evidence, evidence.milestone_kind)


# --------------------------------------------------------------------------- #
# composition: one optional summarizer call over safe facts only
# --------------------------------------------------------------------------- #
def public_facts(decision: Decision) -> dict:
    """The only thing a summarizer is allowed to see."""
    return {"outcome": decision.outcome, "milestone_kind": decision.milestone_kind,
            "key": decision.key, "source": decision.source,
            "device_ref": decision.device_ref, "why": decision.why,
            "detail": decision.detail}


def compose(decision: Decision, *, summarizer: Callable[[dict], str] | None = None) -> str:
    """Deterministic detail first; a summarizer can add one sanitized sentence.

    The deterministic block is never replaced. That is the property a RED or
    UNKNOWN notification needs when the model is down or answers nonsense.
    """
    detail = decision.detail
    if summarizer is not None:
        try:
            extra = summarizer(public_facts(decision))
        except Exception:
            extra = None
        if isinstance(extra, str) and extra.strip():
            extra = _scrub(extra.strip())[:400].strip()
            if extra:
                detail = f"{detail}\nSummary from caretaker: {extra}"
    return detail


def _canonical_identity(value: str) -> str:
    """The one byte form both keys are derived from: NFC, UTF-8.

    Case is NOT folded. `RunA` and `runa` are different milestones; the
    contract's kind/key words are case-sensitive identifiers.
    """
    if not isinstance(value, str):
        raise MilestoneRefused("a delivery identity must be text")
    return unicodedata.normalize("NFC", value)


def _identity_text(decision: Decision) -> str:
    return (f"milestone:{decision.source}:{decision.device_ref}:"
            f"{decision.milestone_kind}:{decision.key}:{decision.outcome}")


def raw_delivery_key_for(decision: Decision) -> str:
    """The pre-NFC identity, used only to find a row written before NFC.

    A ledger written by the previous revision may hold a decomposed identity.
    The observer checks this value before claiming the canonical key, so such
    a row is not resent once.
    """
    return _text(_identity_text(decision), "delivery_key", 512)


def delivery_key_for(decision: Decision) -> str:
    """The full ledger identity for one milestone outcome.

    It is derived from the fact, not from the sentence, so `observed_at`, the
    summary and a caretaker sentence do not change it. This full value is what
    the ledger stores and what `reconcile` accepts.
    """
    return _text(_canonical_identity(_identity_text(decision)),
                 "delivery_key", 512)


def transport_key_for(full_identity: str) -> str:
    """A <=50-character key for `runtime/message_transport.py`.

    The transport passes `idempotency_key[:50]` to the CLI, so handing it a
    full identity longer than 50 makes two different milestones share a key on
    their common prefix. This returns `ms1:` plus 46 hex characters of
    SHA-256 (50 total) over the NFC/UTF-8 canonical identity.

    184 bits make an accidental collision vanishingly unlikely, but this is a
    digest, not a zero-collision proof. The full identity remains the ledger
    key and is stored next to the short key.
    """
    if not isinstance(full_identity, str):
        raise MilestoneRefused("a delivery identity must be text")
    full = _text(_canonical_identity(full_identity), "delivery_key", 512)
    digest = hashlib.sha256(full.encode("utf-8")).hexdigest()
    key = TRANSPORT_KEY_PREFIX + digest[
        :TRANSPORT_KEY_LENGTH - len(TRANSPORT_KEY_PREFIX)]
    if len(key) > TRANSPORT_KEY_LENGTH:  # pragma: no cover - fixed arithmetic
        raise MilestoneRefused("the transport key exceeds the 50-character ceiling")
    return key


def legacy_transport_key(full_identity: str) -> str:
    """The pre-digest transport key: the first 50 characters.

    Used ONLY to reconcile a row written before the digest existed, so a retry
    uses the same key its original attempt used. New rows never take this path,
    and a legacy DELIVERED row is never replayed.

    A legacy reconcile cannot retroactively distinguish two pre-fix rows that
    shared this 50-character prefix; it trusts the provider's dedupe echo. That
    trade-off is confined to rows written before the digest.
    """
    if not isinstance(full_identity, str):
        raise MilestoneRefused("a delivery identity must be text")
    return full_identity[:TRANSPORT_KEY_LENGTH]


def message(decision: Decision,
            *, summarizer: Callable[[dict], str] | None = None) -> str:
    """The sentence handed to the EXISTING provider.

    ServerOS authors it here and the provider decides how it is carried. The
    severity word is the contract's; no §10 event name is claimed, because the
    contract declares the closest INFO event DECLARED_ABSENT and a milestone is
    not an alert.
    """
    return f"{decision.headline}\n{compose(decision, summarizer=summarizer)}"


# --------------------------------------------------------------------------- #
# the outbound hop: one provider, one durable receipt ledger
# --------------------------------------------------------------------------- #
def _runtime_module(name: str, filename: str):
    module = sys.modules.get(name)
    if module is None:
        path = Path(__file__).resolve().parents[2] / "runtime" / filename
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


def _notifications():
    # The tree's canonical name for this file (runtime/alerting.py and the
    # notification tests use it). A second name would execute the module twice
    # in one process and give its callers a second set of classes.
    return _runtime_module("serveros_runtime_notifications", "notifications.py")


def _idempotency_window() -> int:
    transport = _runtime_module("serveros_runtime_message_transport",
                                "message_transport.py")
    return int(transport.IDEMPOTENCY_WINDOW_SECONDS)


def _parse_iso(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo is not None else None


class MilestoneLedger:
    """The receipt ledger. It stores milestone keys and delivery states, not events.

    This is not a second event bus: it has no subscribers, no queue and no
    provider payload. Its only job is making replay safe beyond the transport's
    own one-hour dedupe window.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS milestone_deliveries (
                    delivery_key TEXT PRIMARY KEY,
                    milestone_key TEXT NOT NULL,
                    outcome TEXT NOT NULL,
                    state TEXT NOT NULL,
                    remote_id TEXT,
                    attempted_at TEXT NOT NULL,
                    why TEXT NOT NULL,
                    intent TEXT NOT NULL,
                    transport_key TEXT);
            """)
            # executescript committed; reacquire the write lock before the
            # additive migration, which keeps every pre-digest row.
            db.execute("BEGIN IMMEDIATE")
            columns = {row["name"] for row in
                       db.execute("PRAGMA table_info(milestone_deliveries)")}
            if "transport_key" not in columns:
                db.execute("ALTER TABLE milestone_deliveries "
                           "ADD COLUMN transport_key TEXT")
        self.path.chmod(0o600)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def get(self, delivery_key: str) -> dict | None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM milestone_deliveries WHERE delivery_key=?",
                (delivery_key,)).fetchone()
            return dict(row) if row else None

    def claim(self, *, delivery_key: str, milestone_key: str, outcome: str,
              attempted_at: str, intent: str,
              transport_key: str) -> dict | None:
        """Atomically claim one send, or return the row that already owns it.

        The claim is the INSERT itself: two concurrent observers cannot both
        win, because the delivery key is the primary key. A claimed row is
        ``IN_FLIGHT`` until ``finish``; a crash there is visible as UNKNOWN, not
        as permission to send again.

        ``delivery_key`` is the full identity; ``transport_key`` is the
        <=50-character digest the provider receives. Both live on the row so
        the mapping can be audited.
        """
        try:
            with self._transaction() as db:
                db.execute(
                    "INSERT INTO milestone_deliveries "
                    "(delivery_key, milestone_key, outcome, state, remote_id, "
                    " attempted_at, why, intent, transport_key) "
                    "VALUES (?,?,?,?,NULL,?,?,?,?)",
                    (delivery_key, milestone_key, outcome, IN_FLIGHT,
                     attempted_at, "claimed for one send", intent,
                     transport_key))
            return None
        except sqlite3.IntegrityError:
            return self.get(delivery_key)

    def finish(self, *, delivery_key: str, state: str,
               remote_id: str | None, attempted_at: str, why: str,
               intent: str | None = None) -> bool:
        """Finish a claim. Only an IN_FLIGHT row may move."""
        with self._transaction() as db:
            if intent is None:
                cursor = db.execute(
                    "UPDATE milestone_deliveries SET state=?, remote_id=?, "
                    "attempted_at=?, why=? WHERE delivery_key=? AND state=?",
                    (state, remote_id, attempted_at, why, delivery_key,
                     IN_FLIGHT))
            else:
                cursor = db.execute(
                    "UPDATE milestone_deliveries SET state=?, remote_id=?, "
                    "attempted_at=?, why=?, intent=? "
                    "WHERE delivery_key=? AND state=?",
                    (state, remote_id, attempted_at, why, intent,
                     delivery_key, IN_FLIGHT))
            return cursor.rowcount > 0

    def resolve(self, *, delivery_key: str, state: str,
                remote_id: str | None, attempted_at: str, why: str,
                transport_key: str | None = None) -> bool:
        """Move a non-DELIVERED row. DELIVERED is final and cannot be undone."""
        with self._transaction() as db:
            if transport_key is None:
                cursor = db.execute(
                    "UPDATE milestone_deliveries SET state=?, remote_id=?, "
                    "attempted_at=?, why=? WHERE delivery_key=? AND state<>?",
                    (state, remote_id, attempted_at, why, delivery_key, DELIVERED))
            else:
                cursor = db.execute(
                    "UPDATE milestone_deliveries SET state=?, remote_id=?, "
                    "attempted_at=?, why=?, transport_key=? "
                    "WHERE delivery_key=? AND state<>?",
                    (state, remote_id, attempted_at, why, transport_key,
                     delivery_key, DELIVERED))
            return cursor.rowcount > 0


@dataclass(frozen=True)
class Observation:
    outcome: str
    state: str
    delivery_key: str | None
    remote_id: str | None
    message: str | None
    card_summary: str | None
    why: str
    delivered: bool
    transport_key: str | None = None


def _prior_observation(prior: dict, delivery_key: str) -> Observation:
    """What a replay may say about a delivery another observation owns."""
    outcome = prior["outcome"]
    transport = prior.get("transport_key") or legacy_transport_key(
        prior["delivery_key"])
    if prior["state"] == DELIVERED:
        return Observation(outcome, DUPLICATE, delivery_key, prior["remote_id"],
                           None, None,
                           "already delivered; a replay does not resend", True,
                           transport)
    if prior["state"] == IN_FLIGHT:
        return Observation(
            outcome, UNKNOWN_REPLAY, delivery_key, None, None, None,
            "another observation claimed this delivery and did not finish; its "
            "send is UNKNOWN, not a reason to resend", False, transport)
    return Observation(outcome, UNKNOWN_REPLAY, delivery_key, None, None, None,
                       "previous delivery is UNKNOWN; a replay does not resend",
                       False, transport)


class MilestoneNotifier:
    """Tokyo-side adapter. It owns no credential; the provider is injected."""

    def __init__(self, provider, ledger_path, *, conversation_ref: str,
                 summarizer: Callable[[dict], str] | None = None,
                 clock: Callable[[], Any] | None = None):
        self.provider = provider
        self.ledger = MilestoneLedger(ledger_path)
        self.conversation_ref = _text(conversation_ref, "conversation_ref", 256)
        self.summarizer = summarizer
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    def _now(self) -> datetime:
        value = self.clock()
        if isinstance(value, bool):
            raise MilestoneRefused("clock must return datetime or epoch seconds")
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value, tz=timezone.utc)
        if isinstance(value, datetime):
            return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        raise MilestoneRefused("clock must return datetime or epoch seconds")

    def _send(self, text: str,
              transport_key: str) -> tuple[str, str | None, str]:
        """Hand the sentence to the injected provider after its capability probe.

        `transport_key` is the <=50-character digest, not the full ledger
        identity: `runtime/message_transport.py` truncates at 50 and the
        service dedupes on what it receives. The length check here refuses a
        regression back to the full key instead of letting the transport
        silently cut it.
        """
        key = _text(transport_key, "transport_key", TRANSPORT_KEY_LENGTH)
        notifications = _notifications()
        state, reason = notifications.capability_of(self.provider)
        if state != "SUPPORTED":
            return UNKNOWN_DELIVERY, None, _scrub(
                f"{state}: {reason or 'human.notification.transport is not SUPPORTED'}"
            )[:240]
        try:
            answer = self.provider.notify(
                conversation_ref=self.conversation_ref, text=text,
                idempotency_key=key)
        except Exception as exc:
            return UNKNOWN_DELIVERY, None, _scrub(
                f"{type(exc).__name__}: {exc}")[:240]
        remote = answer.get("remote_id") if isinstance(answer, dict) else None
        # Require an explicit True. A provider that returns only a remote id,
        # or `delivered: 0`, has not made the delivery claim this ledger needs.
        if (isinstance(answer, dict) and answer.get("delivered") is True
                and isinstance(remote, str) and remote.strip()):
            return DELIVERED, _scrub(remote.strip())[:256], "provider returned a message id"
        why = (answer.get("why") if isinstance(answer, dict) else None) or \
            "provider returned no message id"
        return UNKNOWN_DELIVERY, None, _scrub(str(why))[:240]

    def observe(self, evidence: Evidence) -> Observation:
        decision = decide(evidence)
        if decision.outcome == SILENT:
            return Observation(decision.outcome, SILENT_STATE, None, None, None,
                               None, decision.why, False)
        if not decision.deliver:
            # YELLOW and ordinary green exist so that "successful" does not
            # become an interruption, and "recoverable" does not become an alert.
            return Observation(decision.outcome, CARD_ONLY, None, None, None,
                               decision.headline + "\n" + decision.detail,
                               decision.why, False)

        # The key is a property of the fact, not of the sentence: compute it
        # before the summarizer so a replay does not spend a model call.
        key = delivery_key_for(decision)
        raw_key = raw_delivery_key_for(decision)
        if raw_key != key:
            # A row written before NFC canonicalisation must not be resent
            # under a new primary key.
            legacy_prior = self.ledger.get(raw_key)
            if legacy_prior is not None:
                return _prior_observation(legacy_prior, raw_key)
        transport = transport_key_for(key)
        claim_text = message(decision)
        claim_intent = json.dumps(
            {"text": claim_text, "transport_key": transport},
            ensure_ascii=False, sort_keys=True)
        prior = self.ledger.claim(
            delivery_key=key, milestone_key=evidence.key,
            outcome=decision.outcome, attempted_at=self._now().isoformat(),
            intent=claim_intent, transport_key=transport)
        if prior is not None:
            return _prior_observation(prior, key)

        try:
            text = message(decision, summarizer=self.summarizer)
            state, remote_id, why = self._send(text, transport)
        except Exception as exc:  # a claim must never be left IN_FLIGHT by a bug
            text = claim_text
            state, remote_id = UNKNOWN_DELIVERY, None
            why = _scrub(f"{type(exc).__name__}: {exc}")[:240]

        self.ledger.finish(
            delivery_key=key, state=state, remote_id=remote_id,
            attempted_at=self._now().isoformat(), why=why,
            intent=json.dumps({"text": text, "transport_key": transport},
                              ensure_ascii=False, sort_keys=True))
        row = self.ledger.get(key) or {"state": state, "remote_id": remote_id}
        if row["state"] == DELIVERED and state != DELIVERED:
            why = "another observation recorded the delivered receipt"
        return Observation(decision.outcome, row["state"], key,
                           row["remote_id"], text, None, why,
                           row["state"] == DELIVERED, transport)

    def observe_payload(self, source: str, payload: dict) -> Observation:
        return self.observe(normalize(source, payload))

    def send_text(self, *, delivery_key: str, milestone_key: str,
                  outcome: str, text: str, why: str) -> Observation:
        """Send one caller-authored sentence under a caller-chosen identity.

        This is `observe()`'s outbound half with the sentence supplied instead of
        derived from a payload: the same claim, the same <=50-character transport
        digest, the same UNKNOWN, the same receipt and the same reconcile. It
        exists because a batch summary is a statement about *several* milestones,
        so it cannot be expressed as one payload without inventing a `batch`
        source and a batch kind, after which a reader could no longer tell a
        milestone from a summary.

        It deliberately accepts no provider, channel or credential: the provider
        is the notifier's, injected once at construction. A seam that let a
        caller pass one would be a second write path to the one outbound
        provider, and two replicas could both send.

        `outcome` must be one of :data:`NON_MILESTONE_OUTCOMES`. That is the
        mechanical half of "a batch must not pretend to be a milestone": the
        closed set and the closed `MILESTONE_KINDS` set are disjoint, so no
        argument to this method can produce a row that reads as a milestone.
        """
        delivery_key = _text(delivery_key, "delivery_key", 512)
        milestone_key = _text(milestone_key, "milestone_key", 512)
        outcome = _text(outcome, "outcome", 32)
        if outcome not in NON_MILESTONE_OUTCOMES:
            raise MilestoneRefused(
                f"outcome {outcome!r} is not a non-milestone outcome; this seam "
                f"sends batch summaries and alerts, and a milestone is chosen by "
                f"the classifier from evidence, not by a caller "
                f"({sorted(NON_MILESTONE_OUTCOMES)})")
        text = _text(text, "text", 2000)
        why = _scrub(_text(why, "why", 240))

        transport = transport_key_for(delivery_key)
        intent = json.dumps({"text": text, "transport_key": transport},
                            ensure_ascii=False, sort_keys=True)
        prior = self.ledger.claim(
            delivery_key=delivery_key, milestone_key=milestone_key,
            outcome=outcome, attempted_at=self._now().isoformat(),
            intent=intent, transport_key=transport)
        if prior is not None:
            return _prior_observation(prior, delivery_key)

        try:
            state, remote_id, sent_why = self._send(text, transport)
        except Exception as exc:  # a claim must never be left IN_FLIGHT
            state, remote_id = UNKNOWN_DELIVERY, None
            sent_why = _scrub(f"{type(exc).__name__}: {exc}")[:240]

        self.ledger.finish(delivery_key=delivery_key, state=state,
                           remote_id=remote_id,
                           attempted_at=self._now().isoformat(),
                           why=sent_why, intent=intent)
        row = self.ledger.get(delivery_key) or {"state": state,
                                                "remote_id": remote_id}
        if row["state"] == DELIVERED and state != DELIVERED:
            sent_why = "another observation recorded the delivered receipt"
        return Observation(outcome, row["state"], delivery_key, row["remote_id"],
                           text, None, sent_why, row["state"] == DELIVERED,
                           transport)

    def reconcile(self, delivery_key: str) -> Observation:
        """Re-ask the SAME call under the SAME transport key, inside the window.

        Outside the window a repeat would be a second message, so the answer is
        UNKNOWN and no call is made. A DELIVERED entry is never re-sent. A row
        written before the digest existed keeps its original `full[:50]` key so
        a retry does not create a second message under a new key.
        """
        delivery_key = _text(delivery_key, "delivery_key", 512)
        prior = self.ledger.get(delivery_key)
        if prior is None:
            return Observation(UNKNOWN, "MISSING", delivery_key, None, None, None,
                               "no such delivery", False)
        transport = prior.get("transport_key") or legacy_transport_key(
            prior["delivery_key"])
        transport = str(transport)[:TRANSPORT_KEY_LENGTH]
        if prior["state"] == DELIVERED:
            return Observation(prior["outcome"], DUPLICATE, delivery_key,
                               prior["remote_id"], None, None,
                               "already delivered", True, transport)
        if prior["state"] not in (UNKNOWN_DELIVERY, IN_FLIGHT):
            return Observation(prior["outcome"], prior["state"], delivery_key,
                               prior["remote_id"], None, None,
                               "this delivery is not reconcile-able", False,
                               transport)
        attempted = _parse_iso(prior["attempted_at"])
        now = self._now()
        age = None if attempted is None else (now - attempted).total_seconds()
        if age is None or age > _idempotency_window():
            return Observation(
                prior["outcome"], UNKNOWN_DELIVERY, delivery_key, None, None, None,
                "outside the transport's dedupe window; a repeat could be a second message",
                False, transport)
        try:
            spec = json.loads(prior["intent"])
        except (TypeError, ValueError):
            return Observation(prior["outcome"], UNKNOWN_DELIVERY, delivery_key,
                               None, None, None, "stored intent is unreadable",
                               False, transport)
        text = spec.get("text")
        if not isinstance(text, str) or not text.strip():
            return Observation(prior["outcome"], UNKNOWN_DELIVERY, delivery_key,
                               None, None, None, "stored intent has no text",
                               False, transport)
        state, remote_id, why = self._send(text, transport)
        if not prior.get("transport_key"):
            why = _scrub(
                f"{why}; legacy full[:50] key: a dedupe echo cannot prove this "
                f"row's own message existed")[:240]
        self.ledger.resolve(
            delivery_key=delivery_key, state=state, remote_id=remote_id,
            attempted_at=now.isoformat(), why=why, transport_key=transport)
        row = self.ledger.get(delivery_key) or {"state": state,
                                                "remote_id": remote_id}
        if row["state"] == DELIVERED and state != DELIVERED:
            why = "another observation recorded the delivered receipt"
        return Observation(prior["outcome"], row["state"], delivery_key,
                           row["remote_id"], None, None, why,
                           row["state"] == DELIVERED, transport)


# --------------------------------------------------------------------------- #
# the one read-only source seam (production implementation is the next wiring)
# --------------------------------------------------------------------------- #
@runtime_checkable
class SourceRead(Protocol):
    """`read(source, ref) -> payload`. The next production wiring point."""

    def read(self, source: str, ref: str) -> Mapping[str, Any]: ...


class FixtureSource:
    """Offline JSON fixtures. Not a production client; no network, no credentials."""

    def __init__(self, root):
        self.root = Path(root).resolve()

    def read(self, source: str, ref: str) -> dict:
        source = _text(source, "source", 32)
        ref = _text(ref, "ref", 128)
        if source not in _SOURCES:
            raise MilestoneRefused(f"{source!r} is not a known source")
        if any(part in ref for part in ("/", "\\", "..")) or ref.startswith("."):
            raise MilestoneRefused("ref must be a fixture name, not a path")
        path = (self.root / source / f"{ref}.json").resolve()
        if self.root not in path.parents:
            raise MilestoneRefused("ref escapes the fixture root")
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError:
            raise MilestoneRefused(f"fixture not found: {source}/{ref}") from None
        if len(raw.encode("utf-8")) > 64 * 1024:
            raise MilestoneRefused("fixture exceeds the 64 KiB bound")
        try:
            payload = json.loads(raw)
        except ValueError:
            raise MilestoneRefused(f"fixture is not JSON: {source}/{ref}") from None
        if not isinstance(payload, dict):
            raise MilestoneRefused("fixture must contain an object")
        return payload
