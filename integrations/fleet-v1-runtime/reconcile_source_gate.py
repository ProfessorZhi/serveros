#!/usr/bin/env python3
"""R1 #248 / S2 #245 — the reconcile-source gate, and why production stays blocked.

THE QUESTION
------------
S2's durable `EFFECT_UNKNOWN` kernel is usable locally, but the production
observation source is `None`. After a lost send response, can a real Codex /
CloudCLI / Claude Code provider be asked by a **pre-send client id** that the
node still holds, and is that answer an **owner-signed observation**?

THE ANSWER ON THIS NODE (2026-10-20)
------------------------------------
* **Codex app-server `send` to a known thread** has a schema-level pre-send id
  (`TurnStartParams.clientUserMessageId`) and a provider readback
  (`ThreadItem.userMessage.clientId`) through `thread/turns/list` +
  `itemsView: "full"` or `thread/read` + `includeTurns: true`. Public client
  documentation also describes the field as echoed back as the userMessage
  item's `clientId`.
* **No owner-signed observation exists.** The S2 contract has no signature
  field. The daemon route forbids a caller-supplied `observation`, `source`,
  `capability` or `principal`; the production source is `None`; the kernel
  checks principal, writer, intent/locator and `source` provenance, but a
  provenance string is not a signature. The `SyntheticCodexReadbackSource` in
  this directory is `SCHEMA_ONLY` test code, not an owner authority.
* Therefore the gate returns `BLOCKED_PROVIDER_SOURCE` in production. It does
  **not** send, retry or auto-settle. The synthetic mode exists only to prove
  the negative checks; a synthetic HMAC key is never a production authority.

WHAT THE GATE REFUSES
---------------------
    no source / RESPONSE_ID_ONLY / NO_SOURCE   BLOCKED_PROVIDER_SOURCE
    production mode, any observation           BLOCKED_OWNER_SIGNATURE
    missing/bad synthetic signature            BLOCKED_OWNER_SIGNATURE
    phase not EFFECT_UNKNOWN                   REFUSED_NOT_UNKNOWN
    different principal                        REFUSED_PRINCIPAL
    different device                           REFUSED_CROSS_DEVICE
    different project                          REFUSED_CROSS_PROJECT
    missing parent attempt                     REFUSED_MISSING_PARENT
    changed payload digest                     REFUSED_INTENT_MISMATCH
    forged provider locator                    REFUSED_LOCATOR_MISMATCH
    neither digest nor locator matches         REFUSED_INTENT_OR_LOCATOR_MISMATCH
    missing/mismatched current writer          REFUSED_STALE_WRITER
    replayed observation id                    REFUSED_REPLAY
    NOT_APPLIED from this adapter              REFUSED_NOT_APPLIED_UNAVAILABLE
    UNKNOWN observation                        UNKNOWN (no settle)

Every result has `send_allowed=False`, `retry_allowed=False`,
`production_eligible=False` and `production_decision=BLOCKED_PROVIDER_SOURCE`.
`settle_allowed=True` is reachable only from a fully valid **synthetic** signed
observation, and even then `production_eligible=False`. This is the fail-closed
default: uncertainty never sends, never retries, never settles.

This module is pure: no socket, file, process, clock, environment, credential
or transcript value. It takes the attempt, the observation and the test key as
data.
"""
from __future__ import annotations

import hashlib
import hmac
import json

SCHEMA = "serveros/fleet-v1-runtime-reconcile-source-gate.v1"
EVIDENCE_LEVEL = "SYNTHETIC_FIXTURE"

CLIENT_TURN_ID = "CLIENT_TURN_ID"
RESPONSE_ID_ONLY = "RESPONSE_ID_ONLY"
NO_SOURCE = "NO_SOURCE"
BLOCKED_PROVIDER_SOURCE = "BLOCKED_PROVIDER_SOURCE"
BLOCKED_OWNER_SIGNATURE = "BLOCKED_OWNER_SIGNATURE"

REFUSED_NOT_UNKNOWN = "REFUSED_NOT_UNKNOWN"
REFUSED_PRINCIPAL = "REFUSED_PRINCIPAL"
REFUSED_CROSS_DEVICE = "REFUSED_CROSS_DEVICE"
REFUSED_CROSS_PROJECT = "REFUSED_CROSS_PROJECT"
REFUSED_MISSING_PARENT = "REFUSED_MISSING_PARENT"
REFUSED_INTENT_MISMATCH = "REFUSED_INTENT_MISMATCH"
REFUSED_LOCATOR_MISMATCH = "REFUSED_LOCATOR_MISMATCH"
REFUSED_INTENT_OR_LOCATOR_MISMATCH = "REFUSED_INTENT_OR_LOCATOR_MISMATCH"
REFUSED_STALE_WRITER = "REFUSED_STALE_WRITER"
REFUSED_REPLAY = "REFUSED_REPLAY"
REFUSED_NOT_APPLIED_UNAVAILABLE = "REFUSED_NOT_APPLIED_UNAVAILABLE"
UNKNOWN = "UNKNOWN"
SYNTHETIC_ACCEPTED_APPLIED = "SYNTHETIC_ACCEPTED_APPLIED"

MODES = ("production", "synthetic")
TEST_SIGNATURE_ALGORITHM = "TEST_HMAC_SHA256"
SIGNATURE_AUTHORITY = "SYNTHETIC_TEST_ONLY"

# The S2 kernel's verdicts. This gate is a pre-kernel check; it names the
# kernel verdict its reason maps to, so the two cannot silently disagree.
S2_SETTLE_APPLIED = "SETTLE_APPLIED"
S2_REFUSE_NOT_UNKNOWN = "REFUSE_NOT_UNKNOWN"
S2_REFUSE_PRINCIPAL = "REFUSE_PRINCIPAL"
S2_REFUSE_STALE_WRITER = "REFUSE_STALE_WRITER"
S2_REFUSE_UNKNOWN = "REFUSE_UNKNOWN"

_SIGNED_FIELDS = (
    "observation_id",
    "owner_ref",
    "device_ref",
    "project_ref",
    "intent_digest",
    "provider_locator",
    "parent_ref",
    "state",
    "source",
    "observed_at",
)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def signature_payload(observation: dict) -> bytes:
    """The canonical bytes an owner signs. Only the declared fields."""
    body = {key: observation.get(key) for key in _SIGNED_FIELDS}
    return json.dumps(body, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def sign(observation: dict, test_key: str) -> str:
    """Test-only HMAC. A synthetic key is never a production authority."""
    return "sha256:" + hmac.new(
        str(test_key).encode("utf-8"), signature_payload(observation),
        hashlib.sha256).hexdigest()


def signature_valid(observation: dict, test_key: str) -> bool:
    signature = observation.get("signature")
    if not _text(signature) or not _text(test_key):
        return False
    if observation.get("signature_algorithm") != TEST_SIGNATURE_ALGORITHM:
        return False
    return hmac.compare_digest(str(signature), sign(observation, test_key))


def _result(*, decision, s2_verdict, detail, mode, capability, attempt,
            observation, settle_allowed=False, owner_signed=False,
            signature_authority=None) -> dict:
    return {
        "schema": SCHEMA,
        "evidence_level": EVIDENCE_LEVEL,
        "mode": mode,
        "decision": decision,
        "detail": detail,
        "s2_verdict": s2_verdict,
        "s2_status": (BLOCKED_PROVIDER_SOURCE
                      if s2_verdict is None else s2_verdict),
        "source_capability": capability,
        "attempt_key": (attempt or {}).get("key"),
        "observation_id": (observation or {}).get("observation_id"),
        "send_allowed": False,
        "retry_allowed": False,
        "settle_allowed": bool(settle_allowed),
        "production_eligible": False,
        "production_decision": BLOCKED_PROVIDER_SOURCE,
        "owner_signed": bool(owner_signed),
        "signature_authority": signature_authority,
        "not_claimed": (
            "a synthetic HMAC is not an owner authority; a production source "
            "is absent; no send, retry or automatic settle is performed",
        ),
    }


def evaluate(attempt: dict, observation: dict | None, source: dict | None, *,
             mode: str = "production", test_key: str | None = None,
             applied_observation_ids=()) -> dict:
    """Decide whether a provider observation may settle an EFFECT_UNKNOWN.

    The source capability is checked first, exactly as the S2 driver does.
    Production mode then refuses for a missing owner-signed observation even
    if the synthetic HMAC would validate. Synthetic mode is a test harness.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}; got {mode!r}")
    if not isinstance(attempt, dict):
        raise ValueError("attempt must be a mapping")

    source = source if isinstance(source, dict) else {}
    capability = str(source.get("capability") or NO_SOURCE).strip().upper()
    if not source.get("present") or capability != CLIENT_TURN_ID:
        return _result(
            decision=BLOCKED_PROVIDER_SOURCE, s2_verdict=None,
            detail=(f"the observation source is {capability}; only "
                    f"{CLIENT_TURN_ID} can be asked by the id the attempt still "
                    f"holds, so the effect stays EFFECT_UNKNOWN"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)

    if mode == "production":
        return _result(
            decision=BLOCKED_OWNER_SIGNATURE, s2_verdict=None,
            detail=("no owner-signed observation exists in the production "
                    "contract. The daemon-held source and the kernel's principal/"
                    "writer/provenance checks are not a signature, so production "
                    "stays BLOCKED_PROVIDER_SOURCE"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)

    if not isinstance(observation, dict):
        return _result(
            decision=BLOCKED_OWNER_SIGNATURE, s2_verdict=None,
            detail="no observation envelope was supplied",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)
    if not _text(test_key) or not signature_valid(observation, test_key):
        return _result(
            decision=BLOCKED_OWNER_SIGNATURE, s2_verdict=None,
            detail=("the observation is not signed by the test key, or its "
                    "signature algorithm is not TEST_HMAC_SHA256; a synthetic "
                    "signature is never production authority"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)

    owner_signed = True
    authority = SIGNATURE_AUTHORITY

    phase = str(attempt.get("phase") or "").strip().upper()
    if phase != "EFFECT_UNKNOWN":
        return _result(
            decision=REFUSED_NOT_UNKNOWN, s2_verdict=S2_REFUSE_NOT_UNKNOWN,
            detail=f"the attempt is {phase or 'unphased'}, not EFFECT_UNKNOWN",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    principal = str(attempt.get("principal") or "").strip()
    if not _text(principal) or observation.get("owner_ref") != principal:
        return _result(
            decision=REFUSED_PRINCIPAL, s2_verdict=S2_REFUSE_PRINCIPAL,
            detail=(f"the observation owner {observation.get('owner_ref')!r} is "
                    f"not the recorded principal {principal!r}"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    if not _text(attempt.get("device_ref")) or \
            observation.get("device_ref") != attempt.get("device_ref"):
        return _result(
            decision=REFUSED_CROSS_DEVICE, s2_verdict=S2_REFUSE_UNKNOWN,
            detail="the observation is about another device",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    if not _text(attempt.get("project_ref")) or \
            observation.get("project_ref") != attempt.get("project_ref"):
        return _result(
            decision=REFUSED_CROSS_PROJECT, s2_verdict=S2_REFUSE_UNKNOWN,
            detail="the observation is about another project",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    parent = str(attempt.get("parent_ref") or "").strip()
    if not parent or observation.get("parent_ref") != parent:
        return _result(
            decision=REFUSED_MISSING_PARENT, s2_verdict=S2_REFUSE_UNKNOWN,
            detail=("the attempt has no parent ref, or the observation names a "
                    "different parent; a settle with no lineage is refused"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    recorded_digest = str(attempt.get("intent_digest") or "").strip()
    observed_digest = str(observation.get("intent_digest") or "").strip()
    if recorded_digest and observed_digest and recorded_digest != observed_digest:
        return _result(
            decision=REFUSED_INTENT_MISMATCH, s2_verdict=S2_REFUSE_UNKNOWN,
            detail=("the client id matches another payload: the intent digest "
                    "differs. A locator match cannot override a present digest "
                    "mismatch in this gate"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    recorded_locator = str(attempt.get("provider_locator") or "").strip()
    observed_locator = str(observation.get("provider_locator") or "").strip()
    if recorded_locator and observed_locator and recorded_locator != observed_locator:
        return _result(
            decision=REFUSED_LOCATOR_MISMATCH, s2_verdict=S2_REFUSE_UNKNOWN,
            detail="the provider locator is not the one recorded for this attempt",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    same_intent = bool(observed_digest and observed_digest == recorded_digest)
    same_locator = bool(observed_locator and observed_locator == recorded_locator)
    if not (same_intent or same_locator):
        return _result(
            decision=REFUSED_INTENT_OR_LOCATOR_MISMATCH,
            s2_verdict=S2_REFUSE_UNKNOWN,
            detail=("neither the intent digest nor the provider locator ties "
                    "the observation to this attempt"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    work_item = str(attempt.get("work_item") or "").strip()
    if work_item:
        current = attempt.get("current_writer")
        if not isinstance(current, dict):
            return _result(
                decision=REFUSED_STALE_WRITER, s2_verdict=S2_REFUSE_UNKNOWN,
                detail=("the attempt is Work-scoped and no current-writer record "
                        "was available; a writer nobody could authorize is refused"),
                mode=mode, capability=capability, attempt=attempt,
                observation=observation, owner_signed=owner_signed,
                signature_authority=authority)
        mine = attempt.get("writer") or {}
        for field in ("owner", "epoch", "token"):
            if not _text(str(mine.get(field) or "")) or \
                    not _text(str(current.get(field) or "")) or \
                    str(mine.get(field)) != str(current.get(field)):
                return _result(
                    decision=REFUSED_STALE_WRITER,
                    s2_verdict=S2_REFUSE_STALE_WRITER,
                    detail=(f"the writer {field} does not match the current "
                            f"writer; a superseded writer cannot settle the scope"),
                    mode=mode, capability=capability, attempt=attempt,
                    observation=observation, owner_signed=owner_signed,
                    signature_authority=authority)

    observation_id = str(observation.get("observation_id") or "").strip()
    if observation_id and observation_id in set(applied_observation_ids or ()):
        return _result(
            decision=REFUSED_REPLAY, s2_verdict=S2_REFUSE_NOT_UNKNOWN,
            detail=("this observation id was already applied; a replay is not a "
                    "second settle"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)

    state = str(observation.get("state") or "").strip().upper()
    if state == "APPLIED":
        return _result(
            decision=SYNTHETIC_ACCEPTED_APPLIED,
            s2_verdict=S2_SETTLE_APPLIED,
            detail=("a fully matching SYNTHETIC signed observation proves APPLIED "
                    "for the test harness only; production remains blocked"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, settle_allowed=True,
            owner_signed=owner_signed, signature_authority=authority)
    if state == "NOT_APPLIED":
        return _result(
            decision=REFUSED_NOT_APPLIED_UNAVAILABLE,
            s2_verdict=S2_REFUSE_UNKNOWN,
            detail=("this provider readback can prove APPLIED only. It has no "
                    "authoritative NOT_APPLIED record, so absence remains "
                    "UNKNOWN and the retry stays fenced"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, owner_signed=owner_signed,
            signature_authority=authority)
    return _result(
        decision=UNKNOWN, s2_verdict=S2_REFUSE_UNKNOWN,
        detail=f"the observation says {state or 'nothing'}, which settles nothing",
        mode=mode, capability=capability, attempt=attempt,
        observation=observation, owner_signed=owner_signed,
        signature_authority=authority)


def capability_matrix() -> list[dict]:
    """The provider/CLI capability matrix for the reconciliation key.

    `owner_signed_observation` is the second condition the task requires. It
    is `ABSENT` everywhere on this node, which is why production is blocked.
    """
    return [
        {
            "adapter": "codex-app-server",
            "verb": "send",
            "precondition": "thread id already known",
            "pre_send_id": "TurnStartParams.clientUserMessageId",
            "re_query": "thread/turns/list + itemsView=full; match userMessage.clientId",
            "provider_explicit": True,
            "owner_signed_observation": "ABSENT",
            "capability": CLIENT_TURN_ID,
            "production_decision": BLOCKED_PROVIDER_SOURCE,
            "evidence": ("stable v2 schema; public client docs describe the "
                         "echo; no live echo and no owner signature"),
        },
        {
            "adapter": "codex-app-server",
            "verb": "create",
            "precondition": "thread id is in the lost response",
            "pre_send_id": "TurnStartParams.clientUserMessageId",
            "re_query": "no known thread to page",
            "provider_explicit": True,
            "owner_signed_observation": "ABSENT",
            "capability": RESPONSE_ID_ONLY,
            "production_decision": BLOCKED_PROVIDER_SOURCE,
            "evidence": "a client id cannot locate an unknown thread",
        },
        {
            "adapter": "codex-cli",
            "verb": "any",
            "precondition": "CLI resume/queue by provider session id",
            "pre_send_id": None,
            "re_query": "codex resume <session-id>; codex queue --thread",
            "provider_explicit": False,
            "owner_signed_observation": "ABSENT",
            "capability": NO_SOURCE,
            "production_decision": BLOCKED_PROVIDER_SOURCE,
            "evidence": "help surfaces expose no pre-send client id",
        },
        {
            "adapter": "claude-code-cli",
            "verb": "any",
            "precondition": "CLI only, not the BotMux provider path",
            "pre_send_id": "--session-id <uuid>",
            "re_query": "--resume <session-id>",
            "provider_explicit": True,
            "owner_signed_observation": "ABSENT",
            "capability": "CLI_ONLY_NOT_WIRED",
            "production_decision": BLOCKED_PROVIDER_SOURCE,
            "evidence": ("the CLI can choose a session id, but the BotMux "
                         "adapter does not pass or read it; no provider readback"),
        },
        {
            "adapter": "botmux-agent-session",
            "verb": "send",
            "precondition": "triggerId is in the lost response",
            "pre_send_id": "turnIdempotencyKey",
            "re_query": "GET /trigger-result?triggerId=...; no read by client key",
            "provider_explicit": False,
            "owner_signed_observation": "ABSENT",
            "capability": RESPONSE_ID_ONLY,
            "production_decision": BLOCKED_PROVIDER_SOURCE,
            "evidence": "the Dashboard API read needs the response id",
        },
        {
            "adapter": "cloudcli-mcp",
            "verb": "send",
            "precondition": "no client id parameter on session_send",
            "pre_send_id": None,
            "re_query": "run_get requires runId",
            "provider_explicit": False,
            "owner_signed_observation": "ABSENT",
            "capability": NO_SOURCE,
            "production_decision": BLOCKED_PROVIDER_SOURCE,
            "evidence": "the MCP surface exposes no pre-send client turn id",
        },
        {
            "adapter": "human-control",
            "verb": "send",
            "precondition": "triggerId is in the lost response",
            "pre_send_id": "idempotencyKey",
            "re_query": "/api/trigger returns triggerId; no read by client key",
            "provider_explicit": False,
            "owner_signed_observation": "ABSENT",
            "capability": RESPONSE_ID_ONLY,
            "production_decision": BLOCKED_PROVIDER_SOURCE,
            "evidence": "the response is the only id reader",
        },
    ]
