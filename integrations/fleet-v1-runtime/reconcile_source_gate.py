#!/usr/bin/env python3
"""R1 #248 / S2 #245 — the reconcile-source gate, hardened after QA2 #360.

WHY THIS IS v2
--------------
v1 (`b4020b73`) was fail-closed in production, but QA2 found that a synthetic
HMAC produced the *production* field names `owner_signed=true`,
`s2_verdict=SETTLE_APPLIED` and `settle_allowed=true`. A future consumer that
read those three fields would settle on a test key. v2 separates the two
worlds:

* **shared production fields are always a refusal**
  `decision = production_decision = BLOCKED_PROVIDER_SOURCE`,
  `production_eligible = false`, `send_allowed = false`,
  `retry_allowed = false`, `settle_allowed = false`, `owner_signed = false`,
  `s2_verdict = null`, `s2_status = BLOCKED_PROVIDER_SOURCE`.
  A consumer that reads only these fields can never settle.

* **synthetic evidence lives under `synthetic_*`**
  `synthetic_decision`, `synthetic_signature_valid`,
  `synthetic_settle_allowed`. These are test-harness fields; no production
  consumer may read them, and the synthetic HMAC is not an owner authority.

The other QA2 repairs are in the same gate:

* observation_id must be non-empty; an unidentifiable APPLIED is refused
  (`REFUSED_MISSING_OBSERVATION_ID`) instead of skipping replay;
* the signed `source` must equal the declared capability's read path
  (`REFUSED_SOURCE_MISMATCH`);
* signed `observed_at` is parsed and checked against a caller-supplied,
  trusted `now` with a bounded max age; future/stale/unparseable is refused;
* the source declaration is cross-checked against `(adapter, verb,
  thread_known)`; a CLI adapter or an unknown thread cannot declare
  `CLIENT_TURN_ID`;
* the signed payload includes the writer fence (`writer_owner`,
  `writer_epoch`, `writer_token`); an epoch 7 observation cannot settle an
  epoch 8 writer;
* the source object must name a complete adapter/verb/thread_known tuple and a
  read path consistent with `CLIENT_TURN_ID`.

PRODUCTION REMAINS BLOCKED
--------------------------
The two conditions for a production source are still not met: the Codex
app-server has a schema-level pre-send id and readback, but the runtime has no
**owner-signed observation** (the S2 kernel/store/daemon check principal,
writer, intent/locator and `source` provenance, not a signature), and
`dispatch_observation_source` is `None`. Production mode therefore returns
`BLOCKED_PROVIDER_SOURCE` with `production_reason=BLOCKED_OWNER_SIGNATURE`.
This module never sends, retries or settles in production.

This module is pure: no socket, file, process, clock, environment, credential
or transcript value. `now` is an input; it is never read from the host.
"""
from __future__ import annotations

import datetime
import hashlib
import hmac
import json

SCHEMA = "serveros/fleet-v1-runtime-reconcile-source-gate.v2"
EVIDENCE_LEVEL = "SYNTHETIC_FIXTURE"

CLIENT_TURN_ID = "CLIENT_TURN_ID"
RESPONSE_ID_ONLY = "RESPONSE_ID_ONLY"
NO_SOURCE = "NO_SOURCE"
CLI_ONLY_NOT_WIRED = "CLI_ONLY_NOT_WIRED"

BLOCKED_PROVIDER_SOURCE = "BLOCKED_PROVIDER_SOURCE"
BLOCKED_OWNER_SIGNATURE = "BLOCKED_OWNER_SIGNATURE"
BLOCKED_TIME_UNTRUSTED = "BLOCKED_TIME_UNTRUSTED"

REFUSED_NOT_UNKNOWN = "REFUSED_NOT_UNKNOWN"
REFUSED_PRINCIPAL = "REFUSED_PRINCIPAL"
REFUSED_CROSS_DEVICE = "REFUSED_CROSS_DEVICE"
REFUSED_CROSS_PROJECT = "REFUSED_CROSS_PROJECT"
REFUSED_MISSING_PARENT = "REFUSED_MISSING_PARENT"
REFUSED_INTENT_MISMATCH = "REFUSED_INTENT_MISMATCH"
REFUSED_LOCATOR_MISMATCH = "REFUSED_LOCATOR_MISMATCH"
REFUSED_INTENT_OR_LOCATOR_MISMATCH = "REFUSED_INTENT_OR_LOCATOR_MISMATCH"
REFUSED_STALE_WRITER = "REFUSED_STALE_WRITER"
REFUSED_MISSING_OBSERVATION_ID = "REFUSED_MISSING_OBSERVATION_ID"
REFUSED_REPLAY = "REFUSED_REPLAY"
REFUSED_SOURCE_MISMATCH = "REFUSED_SOURCE_MISMATCH"
REFUSED_OBSERVED_AT_INVALID = "REFUSED_OBSERVED_AT_INVALID"
REFUSED_OBSERVED_AT_FUTURE = "REFUSED_OBSERVED_AT_FUTURE"
REFUSED_OBSERVED_AT_STALE = "REFUSED_OBSERVED_AT_STALE"
REFUSED_NOT_APPLIED_UNAVAILABLE = "REFUSED_NOT_APPLIED_UNAVAILABLE"
UNKNOWN = "UNKNOWN"
SYNTHETIC_ACCEPTED_APPLIED = "SYNTHETIC_ACCEPTED_APPLIED"

MODES = ("production", "synthetic")
TEST_SIGNATURE_ALGORITHM = "TEST_HMAC_SHA256"
SIGNATURE_AUTHORITY = "SYNTHETIC_TEST_ONLY"
READ_PATHS = ("codex:thread/turns/list", "codex:thread/read")
MAX_AGE_LIMIT_SECONDS = 3600

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
    "writer_owner",
    "writer_epoch",
    "writer_token",
)


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _parse_instant(value):
    """A zoned ISO-8601 instant, or None. Never reads the host clock."""
    if not _text(value):
        return None
    raw = value.strip()
    try:
        parsed = datetime.datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


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


def _expected_capability(adapter, verb, thread_known):
    """The capability the declaration is allowed to claim.

    This is the cross-check QA2 asked for. A source object is caller data, so
    its `capability` is a claim; this table turns the claim into something the
    gate can falsify from the adapter/verb/thread_known tuple.
    """
    adapter = str(adapter or "").strip().lower()
    verb = str(verb or "").strip().lower()
    if adapter == "codex-app-server":
        if verb == "send":
            if thread_known is True:
                return CLIENT_TURN_ID
            if thread_known is False:
                return RESPONSE_ID_ONLY
            return None
        if verb == "create":
            return RESPONSE_ID_ONLY
        return NO_SOURCE
    if adapter == "codex-cli":
        return NO_SOURCE
    if adapter == "claude-code-cli":
        return CLI_ONLY_NOT_WIRED
    if adapter == "botmux-agent-session":
        return RESPONSE_ID_ONLY if verb == "send" else NO_SOURCE
    if adapter == "cloudcli-mcp":
        return NO_SOURCE
    if adapter == "human-control":
        return RESPONSE_ID_ONLY if verb == "send" else NO_SOURCE
    return None


def _result(*, synthetic_decision, production_reason, detail, mode, capability,
            attempt, observation, synthetic_signature_valid=False,
            synthetic_settle_allowed=False) -> dict:
    synthetic = mode == "synthetic"
    return {
        "schema": SCHEMA,
        "evidence_level": EVIDENCE_LEVEL,
        "mode": mode,
        "synthetic_only": synthetic,
        # ---- shared production fields: ALWAYS a refusal ----------------
        "decision": BLOCKED_PROVIDER_SOURCE,
        "production_decision": BLOCKED_PROVIDER_SOURCE,
        "production_reason": production_reason,
        "production_eligible": False,
        "send_allowed": False,
        "retry_allowed": False,
        "settle_allowed": False,
        "owner_signed": False,
        "s2_verdict": None,
        "s2_status": BLOCKED_PROVIDER_SOURCE,
        # ---- synthetic-only fields -------------------------------------
        "synthetic_decision": synthetic_decision if synthetic else None,
        "synthetic_signature_valid": bool(synthetic_signature_valid) if synthetic else False,
        "synthetic_settle_allowed": bool(synthetic_settle_allowed) if synthetic else False,
        "synthetic_observation_id": ((observation or {}).get("observation_id")
                                     if synthetic else None),
        "signature_authority": (SIGNATURE_AUTHORITY
                                if synthetic and synthetic_signature_valid
                                else None),
        # ---- context ----------------------------------------------------
        "detail": detail,
        "source_capability": capability,
        "attempt_key": (attempt or {}).get("key"),
        "not_claimed": (
            "production fields are always a refusal; the synthetic HMAC is not "
            "an owner authority; no send, retry or automatic settle",
        ),
    }


def evaluate(attempt: dict, observation: dict | None, source: dict | None, *,
             mode: str = "production", test_key: str | None = None,
             applied_observation_ids=(), now: str | None = None,
             max_age_seconds: int = 300) -> dict:
    """Decide whether a provider observation may settle an EFFECT_UNKNOWN.

    Production fields are always a refusal. The synthetic branch is a test
    harness and still refuses every QA2 adversarial witness.
    """
    if mode not in MODES:
        raise ValueError(f"mode must be one of {MODES}; got {mode!r}")
    if not isinstance(attempt, dict):
        raise ValueError("attempt must be a mapping")
    if not isinstance(max_age_seconds, int) or not (0 < max_age_seconds <= MAX_AGE_LIMIT_SECONDS):
        raise ValueError(
            f"max_age_seconds must be an int in 1..{MAX_AGE_LIMIT_SECONDS}")

    # 1. The source declaration is caller data; cross-check it before use.
    source = source if isinstance(source, dict) else {}
    adapter = source.get("adapter")
    verb = source.get("verb")
    thread_known = source.get("thread_known")
    declared = str(source.get("capability") or "").strip().upper()
    capability = declared or NO_SOURCE
    expected = _expected_capability(adapter, verb, thread_known)
    read_path = source.get("read_path")
    if (source.get("present") is not True or expected is None
            or declared != expected or expected != CLIENT_TURN_ID):
        return _result(
            synthetic_decision=BLOCKED_PROVIDER_SOURCE,
            production_reason=BLOCKED_PROVIDER_SOURCE,
            detail=(f"the source declaration {adapter!r}/{verb!r}/"
                    f"thread_known={thread_known!r} claims {capability}; the "
                    f"gate requires {expected!r} and only {CLIENT_TURN_ID} can "
                    f"be asked by the held client id"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)
    if read_path is not None and read_path not in READ_PATHS:
        return _result(
            synthetic_decision=BLOCKED_PROVIDER_SOURCE,
            production_reason=BLOCKED_PROVIDER_SOURCE,
            detail=f"the declared read_path {read_path!r} is not a Codex read path",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)

    # 2. Production: no owner-signed observation exists.
    if mode == "production":
        return _result(
            synthetic_decision=None,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("no owner-signed observation exists in the production "
                    "contract. The daemon-held source and the kernel's "
                    "principal/writer/provenance checks are not a signature, "
                    "so production stays BLOCKED_PROVIDER_SOURCE"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)

    # 3. Synthetic: the caller supplies a trusted simulated now.
    now_instant = _parse_instant(now)
    if now_instant is None:
        return _result(
            synthetic_decision=BLOCKED_TIME_UNTRUSTED,
            production_reason=BLOCKED_TIME_UNTRUSTED,
            detail=("now must be a trusted, zoned ISO-8601 instant supplied by "
                    "the caller; the gate never reads the host clock"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)

    # 4. Observation and test signature.
    if not isinstance(observation, dict):
        return _result(
            synthetic_decision=BLOCKED_OWNER_SIGNATURE,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail="no observation envelope was supplied",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)
    if not _text(test_key) or not signature_valid(observation, test_key):
        return _result(
            synthetic_decision=BLOCKED_OWNER_SIGNATURE,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("the observation is not signed by the test key, or its "
                    "signature algorithm is not TEST_HMAC_SHA256; a synthetic "
                    "signature is never production authority"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation)

    signature_ok = True

    # 5. Identity / intent / lineage.
    phase = str(attempt.get("phase") or "").strip().upper()
    if phase != "EFFECT_UNKNOWN":
        return _result(
            synthetic_decision=REFUSED_NOT_UNKNOWN,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=f"the attempt is {phase or 'unphased'}, not EFFECT_UNKNOWN",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    principal = str(attempt.get("principal") or "").strip()
    if not _text(principal) or observation.get("owner_ref") != principal:
        return _result(
            synthetic_decision=REFUSED_PRINCIPAL,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=(f"the observation owner {observation.get('owner_ref')!r} is "
                    f"not the recorded principal {principal!r}"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    if not _text(attempt.get("device_ref")) or \
            observation.get("device_ref") != attempt.get("device_ref"):
        return _result(
            synthetic_decision=REFUSED_CROSS_DEVICE,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail="the observation is about another device",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    if not _text(attempt.get("project_ref")) or \
            observation.get("project_ref") != attempt.get("project_ref"):
        return _result(
            synthetic_decision=REFUSED_CROSS_PROJECT,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail="the observation is about another project",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    parent = str(attempt.get("parent_ref") or "").strip()
    if not parent or observation.get("parent_ref") != parent:
        return _result(
            synthetic_decision=REFUSED_MISSING_PARENT,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("the attempt has no parent ref, or the observation names a "
                    "different parent; a settle with no lineage is refused"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    recorded_digest = str(attempt.get("intent_digest") or "").strip()
    observed_digest = str(observation.get("intent_digest") or "").strip()
    if recorded_digest and observed_digest and recorded_digest != observed_digest:
        return _result(
            synthetic_decision=REFUSED_INTENT_MISMATCH,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("the client id matches another payload: the intent digest "
                    "differs. A locator match cannot override a present digest "
                    "mismatch in this gate"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    recorded_locator = str(attempt.get("provider_locator") or "").strip()
    observed_locator = str(observation.get("provider_locator") or "").strip()
    if recorded_locator and observed_locator and recorded_locator != observed_locator:
        return _result(
            synthetic_decision=REFUSED_LOCATOR_MISMATCH,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail="the provider locator is not the one recorded for this attempt",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    same_intent = bool(observed_digest and observed_digest == recorded_digest)
    same_locator = bool(observed_locator and observed_locator == recorded_locator)
    if not (same_intent or same_locator):
        return _result(
            synthetic_decision=REFUSED_INTENT_OR_LOCATOR_MISMATCH,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("neither the intent digest nor the provider locator ties "
                    "the observation to this attempt"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    # 6. The signed source must be the declared capability's read path.
    signed_source = str(observation.get("source") or "").strip()
    if signed_source not in READ_PATHS:
        return _result(
            synthetic_decision=REFUSED_SOURCE_MISMATCH,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=(f"the signed source {signed_source!r} is not a Codex read "
                    f"path for {capability}"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)
    if read_path is not None and signed_source != read_path:
        return _result(
            synthetic_decision=REFUSED_SOURCE_MISMATCH,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=(f"the signed source {signed_source!r} is not the declared "
                    f"read_path {read_path!r}"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    # 7. Signed observed_at: parse, future and freshness.
    observed_instant = _parse_instant(observation.get("observed_at"))
    if observed_instant is None:
        return _result(
            synthetic_decision=REFUSED_OBSERVED_AT_INVALID,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail="observed_at is not a zoned ISO-8601 instant",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)
    if observed_instant > now_instant:
        return _result(
            synthetic_decision=REFUSED_OBSERVED_AT_FUTURE,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=f"observed_at {observed_instant.isoformat()} is after now",
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)
    age = (now_instant - observed_instant).total_seconds()
    if age > max_age_seconds:
        return _result(
            synthetic_decision=REFUSED_OBSERVED_AT_STALE,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=(f"observed_at is {int(age)}s old; max_age_seconds is "
                    f"{max_age_seconds}"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    # 8. Writer fence: both the attempt's writer and the signed observation
    #    must match the current writer (owner/epoch/token), the same triple
    #    the S2 kernel's `_same_writer` compares.
    current = attempt.get("current_writer")
    if not isinstance(current, dict):
        return _result(
            synthetic_decision=REFUSED_STALE_WRITER,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("the attempt is Work-scoped and no current-writer record "
                    "was available; a writer nobody could authorize is refused"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)
    mine = attempt.get("writer") or {}
    for field in ("owner", "epoch", "token"):
        mine_value = mine.get(field)
        current_value = current.get(field)
        if not _text(str(mine_value or "")) or \
                not _text(str(current_value or "")) or \
                str(mine_value) != str(current_value):
            return _result(
                synthetic_decision=REFUSED_STALE_WRITER,
                production_reason=BLOCKED_OWNER_SIGNATURE,
                detail=(f"the attempt writer {field} {mine_value!r} is not the "
                        f"current writer's {current_value!r}; a superseded "
                        f"writer cannot settle the scope"),
                mode=mode, capability=capability, attempt=attempt,
                observation=observation, synthetic_signature_valid=signature_ok)
    fence = {
        "writer_owner": current.get("owner"),
        "writer_epoch": current.get("epoch"),
        "writer_token": current.get("token"),
    }
    for field, expected_value in fence.items():
        observed_value = observation.get(field)
        if not _text(str(expected_value or "")) or \
                str(observed_value) != str(expected_value):
            return _result(
                synthetic_decision=REFUSED_STALE_WRITER,
                production_reason=BLOCKED_OWNER_SIGNATURE,
                detail=(f"the signed {field} {observed_value!r} is not the "
                        f"current writer's {expected_value!r}; a superseded "
                        f"writer cannot settle the scope"),
                mode=mode, capability=capability, attempt=attempt,
                observation=observation, synthetic_signature_valid=signature_ok)

    # 9. A non-empty observation id is required before replay can be checked.
    observation_id = str(observation.get("observation_id") or "").strip()
    if not observation_id:
        return _result(
            synthetic_decision=REFUSED_MISSING_OBSERVATION_ID,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("the observation has no id; an unidentifiable APPLIED "
                    "cannot be proven fresh and is refused"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    # 10. Replay.
    if observation_id in set(applied_observation_ids or ()):
        return _result(
            synthetic_decision=REFUSED_REPLAY,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("this observation id was already applied; a replay is not a "
                    "second settle"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)

    # 11. State.
    state = str(observation.get("state") or "").strip().upper()
    if state == "APPLIED":
        return _result(
            synthetic_decision=SYNTHETIC_ACCEPTED_APPLIED,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("a fully matching SYNTHETIC signed observation proves "
                    "APPLIED for the test harness only; production remains "
                    "blocked"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=True,
            synthetic_settle_allowed=True)
    if state == "NOT_APPLIED":
        return _result(
            synthetic_decision=REFUSED_NOT_APPLIED_UNAVAILABLE,
            production_reason=BLOCKED_OWNER_SIGNATURE,
            detail=("this provider readback can prove APPLIED only. It has no "
                    "authoritative NOT_APPLIED record, so absence remains "
                    "UNKNOWN and the retry stays fenced"),
            mode=mode, capability=capability, attempt=attempt,
            observation=observation, synthetic_signature_valid=signature_ok)
    return _result(
        synthetic_decision=UNKNOWN,
        production_reason=BLOCKED_OWNER_SIGNATURE,
        detail=f"the observation says {state or 'nothing'}, which settles nothing",
        mode=mode, capability=capability, attempt=attempt,
        observation=observation, synthetic_signature_valid=signature_ok)


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
            "capability": CLI_ONLY_NOT_WIRED,
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
