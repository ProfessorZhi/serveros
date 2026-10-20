#!/usr/bin/env python3
"""R1 #248 / S2 #245 — the minimal production observation interface.

WHAT THIS IS
------------
A pure conformance validator for the three signed objects a production
reconcile source would need:

1. **Core source binding** — a Core/owner-signed statement that names the
   source, its exact `(adapter, verb, thread_known)`, the provider read path,
   the capability, and the device/project/owner scope. A caller cannot
   self-declare provenance: `provenance` is accepted only from this signed
   object, never from the observation or a request field.
2. **Owner-signed observation** — the owner's signed statement about one
   `EFFECT_UNKNOWN` attempt. It carries the stable pre-send `client_turn_id`,
   the known `thread_id`, the writer fence (`writer_owner/epoch/token`), a
   non-empty `observation_id`, a fresh `observed_at`, the source binding id,
   the intent digest and the provider locator. Its signature subject is the
   owner; the key authority is `owner`, never `execution`.
3. **Provider client-id echo** — the provider's own readback that the pre-send
   client id was persisted and can be re-read. In production it must be
   `live=true, synthetic=false` and `echoed_value == client_turn_id`. A
   synthetic echo can exercise the contract but never unblocks production.

WHAT IT REFUSES
---------------
The validator is fail-closed at every step. It refuses self-declared
provenance, forged source/observation signatures, non-owner key authority,
source scope/audience mismatch, expired source bindings, capability inflation
(CLI adapter or unknown thread claiming `CLIENT_TURN_ID`), missing/empty
observation id or client turn id, unknown thread, writer epoch mismatch,
invalid/future/stale observed_at, replay, NOT_APPLIED from a positive-only
readback, changed payload, forged locator, missing parent, cross-device,
cross-project, and a non-`EFFECT_UNKNOWN` attempt.

PRODUCTION IS STILL BLOCKED
---------------------------
This PR does **not** implement a production-positive path. Every output has
`production_decision=BLOCKED_PROVIDER_SOURCE`, `production_eligible=false`,
`send_allowed=false`, `retry_allowed=false`, `resend_allowed=false`,
`settle_allowed=false`. The remaining external dependencies are listed on every
record:

* `BLOCKED_OWNER_SIGNED_SOURCE` — the runtime has no Core/owner Ed25519 trust
  binding or signer for observations; the test verifiers are synthetic.
* `BLOCKED_PROVIDER_ECHO` — the Codex `clientUserMessageId → clientId` echo is
  schema-level only; no live echo has been observed.
* `BLOCKED_S2_OBSERVATION_FIELD` — the S2 kernel/store/daemon accept a
  provenance-tagged observation, not a signed envelope.
* `BLOCKED_COMMIT_SEAM` — S1's verify→commit race is still fail-closed
  (`BLOCKED_OWNER_COMMIT_SEAM`); a reconcile settle needs an atomic commit.
* `BLOCKED_REPLAY_STORE` — there is no observation-id replay set; the current
  store keys dispatch attempts, not owner observations.

The module is pure: no socket, file, process, host clock, environment,
credential or transcript value. `now` is an input. The test-only HMAC helper
(`sign_payload`) exists so the fixture can run without a key store; it declares
itself synthetic and cannot satisfy the production verifier checks.
"""
from __future__ import annotations

import base64
import datetime
import hashlib
import hmac
import json

SCHEMA = "serveros/fleet-v1-runtime-observation-contract.v1"
EVIDENCE_LEVEL = "SYNTHETIC_FIXTURE"

BLOCKED_PROVIDER_SOURCE = "BLOCKED_PROVIDER_SOURCE"
BLOCKED_OWNER_SIGNED_SOURCE = "BLOCKED_OWNER_SIGNED_SOURCE"
BLOCKED_PROVIDER_ECHO = "BLOCKED_PROVIDER_ECHO"
BLOCKED_S2_OBSERVATION_FIELD = "BLOCKED_S2_OBSERVATION_FIELD"
BLOCKED_COMMIT_SEAM = "BLOCKED_COMMIT_SEAM"
BLOCKED_REPLAY_STORE = "BLOCKED_REPLAY_STORE"
BLOCKED_TIME_UNTRUSTED = "BLOCKED_TIME_UNTRUSTED"

CONTRACT_SATISFIED_SYNTHETIC = "CONTRACT_SATISFIED_SYNTHETIC"
REFUSED_SELF_DECLARED_PROVENANCE = "REFUSED_SELF_DECLARED_PROVENANCE"
REFUSED_SOURCE_BINDING_SIGNATURE = "REFUSED_SOURCE_BINDING_SIGNATURE"
REFUSED_SOURCE_BINDING_SCHEMA = "REFUSED_SOURCE_BINDING_SCHEMA"
REFUSED_SOURCE_BINDING_EXPIRED = "REFUSED_SOURCE_BINDING_EXPIRED"
REFUSED_SOURCE_SCOPE = "REFUSED_SOURCE_SCOPE"
REFUSED_OBSERVATION_SIGNATURE = "REFUSED_OBSERVATION_SIGNATURE"
REFUSED_OBSERVATION_SCHEMA = "REFUSED_OBSERVATION_SCHEMA"
REFUSED_KEY_AUTHORITY = "REFUSED_KEY_AUTHORITY"
REFUSED_CAPABILITY_MISMATCH = "REFUSED_CAPABILITY_MISMATCH"
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
REFUSED_MISSING_CLIENT_TURN_ID = "REFUSED_MISSING_CLIENT_TURN_ID"
REFUSED_THREAD_UNKNOWN = "REFUSED_THREAD_UNKNOWN"
REFUSED_OBSERVED_AT_INVALID = "REFUSED_OBSERVED_AT_INVALID"
REFUSED_OBSERVED_AT_FUTURE = "REFUSED_OBSERVED_AT_FUTURE"
REFUSED_OBSERVED_AT_STALE = "REFUSED_OBSERVED_AT_STALE"
REFUSED_OBSERVATION_TIME_ORDER = "REFUSED_OBSERVATION_TIME_ORDER"
REFUSED_ECHO_MISMATCH = "REFUSED_ECHO_MISMATCH"
REFUSED_ECHO_SCHEMA = "REFUSED_ECHO_SCHEMA"
REFUSED_NOT_APPLIED_UNAVAILABLE = "REFUSED_NOT_APPLIED_UNAVAILABLE"
REFUSED_NOT_UNKNOWN = "REFUSED_NOT_UNKNOWN"
UNKNOWN = "UNKNOWN"

CLIENT_TURN_ID = "CLIENT_TURN_ID"
RESPONSE_ID_ONLY = "RESPONSE_ID_ONLY"
NO_SOURCE = "NO_SOURCE"
CLI_ONLY_NOT_WIRED = "CLI_ONLY_NOT_WIRED"
READ_PATHS = ("codex:thread/turns/list", "codex:thread/read")
MAX_AGE_LIMIT_SECONDS = 3600
AUTHORITY = "ADMISSION_REMAINS_WITH_AGENT_CONTROL"

_SOURCE_FIELDS = frozenset({
    "schema", "source_id", "provenance", "adapter", "verb", "thread_known",
    "read_path", "capability", "device_ref", "project_ref", "owner_ref",
    "audience", "key_id", "issued_at", "expires_at", "nonce", "signature",
})
_OBSERVATION_FIELDS = frozenset({
    "schema", "observation_id", "client_turn_id", "thread_id", "thread_known",
    "source_binding_id", "owner_ref", "device_ref", "project_ref",
    "intent_digest", "provider_locator", "parent_ref", "state", "source",
    "observed_at", "writer_owner", "writer_epoch", "writer_token",
    "audience", "key_id", "issued_at", "nonce", "signature",
})
_ECHO_FIELDS = frozenset({
    "schema", "capability", "adapter", "verb", "thread_known", "read_path",
    "client_turn_id", "echoed_value", "matched_on", "observed_at", "live",
    "synthetic", "source",
})
_PROVENANCE_CLAIM_FIELDS = frozenset({
    "provenance", "minted_by", "owner_signed", "source_binding", "signed_by",
    "core_signed", "authority",
})
_SOURCE_SCHEMA = "serveros/core-observation-source-binding.v1"
_OBSERVATION_SCHEMA = "serveros/owner-signed-observation.v1"
_ECHO_SCHEMA = "serveros/provider-client-id-echo.v1"


class VerifierUnavailable(RuntimeError):
    """The production verifier cannot run in this interpreter."""


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def canonical_bytes(payload: dict) -> bytes:
    """The one canonical form, matching `context/actor.py#signed_bytes`.

    The signature field is excluded: a signature is not part of what it signs.
    """
    body = {key: value for key, value in payload.items() if key != "signature"}
    return json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sign_payload(payload: dict, test_key: str) -> str:
    """SYNTHETIC TEST ONLY. This HMAC is not an owner authority."""
    return "sha256:" + hmac.new(
        str(test_key).encode("utf-8"), canonical_bytes(payload),
        hashlib.sha256).hexdigest()


def _parse_instant(value):
    if not _text(value):
        return None
    try:
        parsed = datetime.datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(datetime.timezone.utc)


def _expected_capability(adapter, verb, thread_known):
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


class Ed25519Verifier:
    """The production signature interface.

    The Core owns the key map and constructs this object; the observation never
    supplies its own public key. `cryptography` is imported lazily, the same
    way `context/actor.py#verify_delegation` does, so a host without Ed25519
    support fails closed instead of silently accepting a weaker scheme.
    """

    authority = "owner"
    production = True

    def __init__(self, *, keys: dict[str, bytes], authority: str = "owner") -> None:
        self.keys = {str(key): bytes(value) for key, value in (keys or {}).items()}
        self.authority = str(authority)

    def verify(self, *, key_id: str, payload: dict, signature: str) -> bool:
        try:
            from cryptography.exceptions import InvalidSignature
            from cryptography.hazmat.primitives.asymmetric.ed25519 import (
                Ed25519PublicKey,
            )
        except ImportError as exc:
            raise VerifierUnavailable(
                "Ed25519 support is required for an owner-signed observation") from exc
        public = self.keys.get(str(key_id))
        if public is None:
            return False
        try:
            raw = base64.b64decode(str(signature), validate=True)
            Ed25519PublicKey.from_public_bytes(public).verify(
                raw, canonical_bytes(payload))
        except (TypeError, ValueError, InvalidSignature):
            return False
        return True


def _base_blockers(core_verifier, owner_verifier, echo) -> list[str]:
    blockers: list[str] = []
    core_ok = (getattr(core_verifier, "authority", None) == "core"
               and getattr(core_verifier, "production", False) is True)
    owner_ok = (getattr(owner_verifier, "authority", None) == "owner"
                and getattr(owner_verifier, "production", False) is True)
    if not (core_ok and owner_ok):
        blockers.append(BLOCKED_OWNER_SIGNED_SOURCE)
    if not (isinstance(echo, dict) and echo.get("live") is True
            and echo.get("synthetic") is False):
        blockers.append(BLOCKED_PROVIDER_ECHO)
    blockers.extend([BLOCKED_S2_OBSERVATION_FIELD, BLOCKED_COMMIT_SEAM,
                     BLOCKED_REPLAY_STORE])
    return blockers


def _result(*, synthetic_decision, detail, production_blockers,
            synthetic_signature_valid=False, source_binding=None,
            observation=None, echo=None) -> dict:
    source = source_binding if isinstance(source_binding, dict) else {}
    obs = observation if isinstance(observation, dict) else {}
    echo = echo if isinstance(echo, dict) else {}
    return {
        "schema": SCHEMA,
        "evidence_level": EVIDENCE_LEVEL,
        "production_decision": BLOCKED_PROVIDER_SOURCE,
        "production_eligible": False,
        "production_blockers": list(production_blockers),
        "send_allowed": False,
        "retry_allowed": False,
        "resend_allowed": False,
        "settle_allowed": False,
        "synthetic_decision": synthetic_decision,
        "synthetic_settle_allowed": False,
        "synthetic_signature_valid": bool(synthetic_signature_valid),
        "source_binding": {
            "source_id": source.get("source_id"),
            "provenance": source.get("provenance"),
            "capability": source.get("capability"),
            "read_path": source.get("read_path"),
        },
        "provider_echo": {
            "live": echo.get("live"),
            "synthetic": echo.get("synthetic"),
            "matched": (echo.get("echoed_value") == echo.get("client_turn_id")),
        },
        "observation_id": obs.get("observation_id"),
        "client_turn_id": obs.get("client_turn_id"),
        "authority": AUTHORITY,
        "detail": detail,
        "not_claimed": (
            "no production signer, no live provider echo, no S2 field, no "
            "atomic commit seam, no replay store; no send/retry/resend/settle",
        ),
    }


def _closed(payload, allowed) -> bool:
    return isinstance(payload, dict) and set(payload) <= set(allowed)


def validate(*, attempt: dict, observation, source_binding, provider_echo,
             core_verifier, owner_verifier, trust: dict, now: str,
             max_age_seconds: int = 300, applied_observation_ids=()) -> dict:
    """Validate the production observation interface. Pure; always blocked."""
    if not isinstance(attempt, dict):
        raise ValueError("attempt must be a mapping")
    if not isinstance(max_age_seconds, int) or not (0 < max_age_seconds <= MAX_AGE_LIMIT_SECONDS):
        raise ValueError(
            f"max_age_seconds must be an int in 1..{MAX_AGE_LIMIT_SECONDS}")
    now_instant = _parse_instant(now)
    blockers = _base_blockers(core_verifier, owner_verifier, provider_echo)
    if now_instant is None:
        return _result(
            synthetic_decision=BLOCKED_TIME_UNTRUSTED,
            detail="now must be a trusted zoned ISO-8601 instant",
            production_blockers=blockers + [BLOCKED_TIME_UNTRUSTED],
            observation=observation, source_binding=source_binding,
            echo=provider_echo)

    # 1. Closed schemas. A caller field cannot become provenance.
    if not _closed(observation, _OBSERVATION_FIELDS):
        unexpected = set(observation) - _OBSERVATION_FIELDS if isinstance(observation, dict) else set()
        if unexpected & _PROVENANCE_CLAIM_FIELDS:
            return _result(
                synthetic_decision=REFUSED_SELF_DECLARED_PROVENANCE,
                detail=("the observation carries provenance/authority fields. "
                        "Provenance is accepted only from the signed Core "
                        "source binding, never from the observation"),
                production_blockers=blockers, source_binding=source_binding,
                observation=observation, echo=provider_echo)
        return _result(
            synthetic_decision=REFUSED_OBSERVATION_SCHEMA,
            detail=f"unexpected observation fields: {sorted(unexpected)}",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if not _closed(source_binding, _SOURCE_FIELDS):
        return _result(
            synthetic_decision=REFUSED_SOURCE_BINDING_SCHEMA,
            detail="the source binding has unexpected fields",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if provider_echo is not None and not _closed(provider_echo, _ECHO_FIELDS):
        return _result(
            synthetic_decision=REFUSED_ECHO_SCHEMA,
            detail="the provider echo has unexpected fields",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 2. Source binding: signed by the Core, in scope, unexpired.
    if source_binding.get("schema") != _SOURCE_SCHEMA:
        return _result(
            synthetic_decision=REFUSED_SOURCE_BINDING_SCHEMA,
            detail="unknown source binding schema",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if getattr(core_verifier, "authority", None) != "core":
        return _result(
            synthetic_decision=REFUSED_KEY_AUTHORITY,
            detail="the source binding verifier is not a Core authority",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if source_binding.get("key_id") != trust.get("core_key_id") or \
            not core_verifier.verify(key_id=source_binding.get("key_id"),
                                     payload=source_binding,
                                     signature=source_binding.get("signature")):
        return _result(
            synthetic_decision=REFUSED_SOURCE_BINDING_SIGNATURE,
            detail="the Core source binding signature did not verify",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if source_binding.get("provenance") != "core:agent-control":
        return _result(
            synthetic_decision=REFUSED_SELF_DECLARED_PROVENANCE,
            detail="the signed source binding does not name core:agent-control",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if source_binding.get("audience") != trust.get("audience") or \
            source_binding.get("owner_ref") != attempt.get("principal") or \
            source_binding.get("device_ref") != attempt.get("device_ref") or \
            source_binding.get("project_ref") != attempt.get("project_ref"):
        return _result(
            synthetic_decision=REFUSED_SOURCE_SCOPE,
            detail="the source binding is not scoped to this owner/device/project",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    issued = _parse_instant(source_binding.get("issued_at"))
    expires = _parse_instant(source_binding.get("expires_at"))
    if issued is None or expires is None or issued > now_instant or expires < now_instant:
        return _result(
            synthetic_decision=REFUSED_SOURCE_BINDING_EXPIRED,
            detail="the source binding is not valid at now",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    expected_capability = _expected_capability(
        source_binding.get("adapter"), source_binding.get("verb"),
        source_binding.get("thread_known"))
    if expected_capability != source_binding.get("capability") or \
            source_binding.get("capability") != CLIENT_TURN_ID or \
            source_binding.get("read_path") not in READ_PATHS:
        return _result(
            synthetic_decision=REFUSED_CAPABILITY_MISMATCH,
            detail=("the source binding claims a capability its adapter/verb/"
                    "thread_known cannot provide, or names no Codex read path"),
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 3. Observation: signed by the owner, about this attempt.
    if observation.get("schema") != _OBSERVATION_SCHEMA:
        return _result(
            synthetic_decision=REFUSED_OBSERVATION_SCHEMA,
            detail="unknown observation schema",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if getattr(owner_verifier, "authority", None) != "owner":
        return _result(
            synthetic_decision=REFUSED_KEY_AUTHORITY,
            detail="the observation verifier is not an owner authority",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if observation.get("key_id") != trust.get("owner_key_id"):
        return _result(
            synthetic_decision=REFUSED_KEY_AUTHORITY,
            detail="the observation key is not the trusted owner key",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if not owner_verifier.verify(key_id=observation.get("key_id"),
                                 payload=observation,
                                 signature=observation.get("signature")):
        return _result(
            synthetic_decision=REFUSED_OBSERVATION_SIGNATURE,
            detail="the owner observation signature did not verify",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if observation.get("audience") != trust.get("audience") or \
            observation.get("source_binding_id") != source_binding.get("source_id"):
        return _result(
            synthetic_decision=REFUSED_SOURCE_SCOPE,
            detail="the observation audience or source binding id is not this binding",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if observation.get("owner_ref") != attempt.get("principal") or \
            observation.get("owner_ref") != trust.get("owner_ref"):
        return _result(
            synthetic_decision=REFUSED_PRINCIPAL,
            detail="the observation owner is not the attempt principal",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 4. Attempt phase.
    if str(attempt.get("phase") or "").strip().upper() != "EFFECT_UNKNOWN":
        return _result(
            synthetic_decision=REFUSED_NOT_UNKNOWN,
            detail="the attempt is not EFFECT_UNKNOWN",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 5. Stable ids and thread knowledge.
    if not _text(observation.get("observation_id")):
        return _result(
            synthetic_decision=REFUSED_MISSING_OBSERVATION_ID,
            detail="an unidentifiable observation cannot be proven fresh",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if not _text(observation.get("client_turn_id")):
        return _result(
            synthetic_decision=REFUSED_MISSING_CLIENT_TURN_ID,
            detail="the stable pre-send client turn id is required",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if observation.get("thread_known") is not True or \
            not _text(observation.get("thread_id")):
        return _result(
            synthetic_decision=REFUSED_THREAD_UNKNOWN,
            detail="the provider thread id must be known before the read",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 6. Scope, lineage, intent.
    if observation.get("device_ref") != attempt.get("device_ref"):
        return _result(
            synthetic_decision=REFUSED_CROSS_DEVICE,
            detail="the observation is about another device",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if observation.get("project_ref") != attempt.get("project_ref"):
        return _result(
            synthetic_decision=REFUSED_CROSS_PROJECT,
            detail="the observation is about another project",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    parent = str(attempt.get("parent_ref") or "").strip()
    if not parent or observation.get("parent_ref") != parent:
        return _result(
            synthetic_decision=REFUSED_MISSING_PARENT,
            detail="the attempt has no parent, or the observation names another",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    recorded_digest = str(attempt.get("intent_digest") or "").strip()
    observed_digest = str(observation.get("intent_digest") or "").strip()
    if recorded_digest and observed_digest and recorded_digest != observed_digest:
        return _result(
            synthetic_decision=REFUSED_INTENT_MISMATCH,
            detail="the intent digest differs",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    recorded_locator = str(attempt.get("provider_locator") or "").strip()
    observed_locator = str(observation.get("provider_locator") or "").strip()
    if recorded_locator and observed_locator and recorded_locator != observed_locator:
        return _result(
            synthetic_decision=REFUSED_LOCATOR_MISMATCH,
            detail="the provider locator differs",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if not ((observed_digest and observed_digest == recorded_digest) or
            (observed_locator and observed_locator == recorded_locator)):
        return _result(
            synthetic_decision=REFUSED_INTENT_OR_LOCATOR_MISMATCH,
            detail="neither digest nor locator ties the observation to this attempt",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 7. Writer fence: attempt writer vs current writer, and signed fence.
    current = attempt.get("current_writer")
    if not isinstance(current, dict):
        return _result(
            synthetic_decision=REFUSED_STALE_WRITER,
            detail="no current-writer record is available",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    mine = attempt.get("writer") or {}
    for field in ("owner", "epoch", "token"):
        if not _text(str(mine.get(field) or "")) or \
                not _text(str(current.get(field) or "")) or \
                str(mine.get(field)) != str(current.get(field)):
            return _result(
                synthetic_decision=REFUSED_STALE_WRITER,
                detail=f"the attempt writer {field} is not the current writer's",
                production_blockers=blockers, source_binding=source_binding,
                observation=observation, echo=provider_echo)
    for signed_field, current_field in (("writer_owner", "owner"),
                                        ("writer_epoch", "epoch"),
                                        ("writer_token", "token")):
        if not _text(str(current.get(current_field) or "")) or \
                str(observation.get(signed_field)) != str(current.get(current_field)):
            return _result(
                synthetic_decision=REFUSED_STALE_WRITER,
                detail=(f"the signed {signed_field} is not the current writer's "
                        f"{current_field}; a superseded writer cannot settle"),
                production_blockers=blockers, source_binding=source_binding,
                observation=observation, echo=provider_echo)

    # 8. Time: observed_at is fresh and not future; issuance is ordered.
    observed_instant = _parse_instant(observation.get("observed_at"))
    if observed_instant is None:
        return _result(
            synthetic_decision=REFUSED_OBSERVED_AT_INVALID,
            detail="observed_at is not a zoned ISO-8601 instant",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if observed_instant > now_instant:
        return _result(
            synthetic_decision=REFUSED_OBSERVED_AT_FUTURE,
            detail="observed_at is after now",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if (now_instant - observed_instant).total_seconds() > max_age_seconds:
        return _result(
            synthetic_decision=REFUSED_OBSERVED_AT_STALE,
            detail=f"observed_at is older than {max_age_seconds}s",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    observation_issued = _parse_instant(observation.get("issued_at"))
    if observation_issued is None or not (observed_instant <= observation_issued <= now_instant):
        return _result(
            synthetic_decision=REFUSED_OBSERVATION_TIME_ORDER,
            detail="observed_at, issued_at and now are not ordered",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 9. Replay.
    observation_id = str(observation.get("observation_id"))
    if observation_id in set(applied_observation_ids or ()):
        return _result(
            synthetic_decision=REFUSED_REPLAY,
            detail="this observation id was already applied",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 10. State. This readback is positive-only.
    state = str(observation.get("state") or "").strip().upper()
    if state == "NOT_APPLIED":
        return _result(
            synthetic_decision=REFUSED_NOT_APPLIED_UNAVAILABLE,
            detail="this readback has no authoritative NOT_APPLIED record",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if state != "APPLIED":
        return _result(
            synthetic_decision=UNKNOWN,
            detail=f"the observation state {state or 'empty'} settles nothing",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    # 11. Provider echo: the provider must have persisted the client id.
    if provider_echo is None:
        return _result(
            synthetic_decision=BLOCKED_PROVIDER_ECHO,
            detail="no provider client-id echo is available",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if provider_echo.get("schema") != _ECHO_SCHEMA or \
            provider_echo.get("capability") != CLIENT_TURN_ID or \
            provider_echo.get("adapter") != source_binding.get("adapter") or \
            provider_echo.get("verb") != source_binding.get("verb") or \
            provider_echo.get("thread_known") is not True or \
            provider_echo.get("read_path") != source_binding.get("read_path") or \
            provider_echo.get("source") != source_binding.get("read_path") or \
            provider_echo.get("client_turn_id") != observation.get("client_turn_id") or \
            provider_echo.get("matched_on") != "userMessage.clientId":
        return _result(
            synthetic_decision=REFUSED_ECHO_MISMATCH,
            detail="the provider echo does not match this source and client id",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)
    if provider_echo.get("echoed_value") != observation.get("client_turn_id"):
        return _result(
            synthetic_decision=REFUSED_ECHO_MISMATCH,
            detail="the provider echoed a different client id",
            production_blockers=blockers, source_binding=source_binding,
            observation=observation, echo=provider_echo)

    return _result(
        synthetic_decision=CONTRACT_SATISFIED_SYNTHETIC,
        detail=("the signed source, observation and client-id echo satisfy the "
                "local contract; production remains blocked on the listed "
                "external dependencies"),
        production_blockers=blockers, synthetic_signature_valid=True,
        source_binding=source_binding, observation=observation,
        echo=provider_echo)
