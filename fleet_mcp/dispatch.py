"""Delivery claims over an optional existing owner; no file, Work or grant store."""
from __future__ import annotations

import hashlib
import json
import threading
import uuid
from dataclasses import dataclass
from typing import Any

from .errors import (BAD_REQUEST, DISPATCH_OUTCOME_UNKNOWN, DISPATCH_STATE_UNAVAILABLE,
                     IDEMPOTENCY_CONFLICT, RATE_LIMITED, refuse)
from .identity import SessionKey

RECEIPT_FIELDS = frozenset(("session_ref", "run_id", "delivery_id", "queued_message_uuid", "accepted"))
PROJECT_RECEIPT_FIELDS = frozenset(("project_id", "project_path", "accepted"))
MAX_RECEIPT_BYTES = 65536


def delivery_receipt(record: dict[str, Any], *, project: bool = False) -> dict[str, Any]:
    """Keep address/ACK facts; never persist raw provider data or instructions."""
    if project:
        if any(k in record for k in ("session", "display_name", "session_ref", "run_id", "delivery_id", "queued_message_uuid")):
            raise ValueError("a project receipt cannot contain session identifiers")
        receipt = {k: record.get(k) for k in PROJECT_RECEIPT_FIELDS}
        if (receipt["accepted"] is not True or any(
                not isinstance(receipt[k], str) or not receipt[k].strip()
                for k in ("project_id", "project_path"))):
            raise ValueError("provider did not acknowledge a real project")
        if len(json.dumps(receipt, allow_nan=False).encode("utf-8")) > MAX_RECEIPT_BYTES:
            raise ValueError("project receipt exceeds its byte bound")
        return receipt
    address = record.get("session") or record.get("display_name") or {}
    ref = address.get("session_ref")
    if not isinstance(ref, str) or not ref:
        raise ValueError("provider receipt has no session address")
    SessionKey.parse(ref)
    receipt = {"session_ref": ref, "run_id": record.get("run_id"),
               "queued_message_uuid": record.get("queued_message_uuid"),
               "accepted": record.get("accepted", True)}
    if "delivery_id" in record:
        receipt["delivery_id"] = record["delivery_id"]
    if receipt["accepted"] is not True:
        raise ValueError("provider did not acknowledge delivery")
    if ("display_name" in record and not receipt["run_id"]
            and not receipt["queued_message_uuid"] and not receipt.get("delivery_id")):
        raise ValueError("send acknowledgement has no execution reference")
    for field in ("run_id", "delivery_id", "queued_message_uuid"):
        value = receipt.get(field)
        if value is not None and (not isinstance(value, str) or not value.strip()
                                  or len(value) > 512 or any(ord(c) < 32 for c in value)):
            raise ValueError("provider returned a malformed identifier")
    if len(json.dumps(receipt, allow_nan=False).encode("utf-8")) > MAX_RECEIPT_BYTES:
        raise ValueError("provider receipt exceeds its byte bound")
    return receipt


@dataclass(frozen=True)
class Claim:
    key: str
    digest: str
    actor: str
    claim_id: str | None
    replay: dict[str, Any] | None = None
    verb: str | None = None
    expected_project_path: str | None = None


class DispatchGuard:
    def __init__(self, owner=None, *, capacity: int = 1024):
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity < 1:
            raise ValueError("dispatch capacity must be a positive integer")
        if owner is not None and not all(callable(getattr(owner, name, None)) for name in
                ("claim_dispatch_attempt", "complete_dispatch_claim")):
            raise ValueError("attempt owner lacks atomic claim/completion")
        self.owner = owner
        self.capacity = capacity
        self._records: dict[str, dict] = {}
        self._lock = threading.Lock()

    @property
    def durability(self):
        return "AGENT_CONTROL_STORE" if self.owner is not None else "PROCESS_LOCAL"

    def begin(self, principal, verb: str, target: str, key: str, *,
              device_ref: str, runtime: str, params: dict, session_ref=None,
              expected_project_path=None) -> Claim:
        if not isinstance(key, str) or not key.strip():
            raise refuse(BAD_REQUEST, "idempotency_key must be a non-empty string")
        actor = json.dumps([principal.credential_kind, principal.name], separators=(",", ":"))
        identity = json.dumps([actor, verb, target, key], separators=(",", ":"))
        token = "fleet-mcp:" + hashlib.sha256(identity.encode("utf-8")).hexdigest()
        project = verb == "project.create"
        if ((project and (not isinstance(expected_project_path, str) or not expected_project_path.strip()))
                or (not project and expected_project_path is not None)):
            raise refuse(BAD_REQUEST, "project.create requires its expected target path")
        intent_payload = {"principal": actor, "verb": verb, "target": target, "params": params}
        if project:
            intent_payload["expected_project_path"] = expected_project_path
        payload = json.dumps(intent_payload, sort_keys=True, separators=(",", ":"),
                             ensure_ascii=False, allow_nan=False)
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        intent = {"key": token, "action": "fleet." + verb, "device_ref": device_ref,
                  "verb": verb if project else "session." + verb, "intent_digest": digest,
                  "principal": actor, "runtime": runtime}
        if session_ref:
            intent["session_ref"] = session_ref
        if project:
            if session_ref is not None:
                raise refuse(BAD_REQUEST, "a project claim cannot name a session")
            intent["expected_project_path"] = expected_project_path
        try:
            if self.owner is not None:
                answer = self.owner.claim_dispatch_attempt(intent, actor=actor)
            else:
                with self._lock:
                    existing = self._records.get(token)
                    if existing is None:
                        if len(self._records) >= self.capacity:
                            raise refuse(RATE_LIMITED, "retry guard is full; old attempts were retained")
                        marker = uuid.uuid4().hex
                        existing = {**intent, "phase": "EFFECT_UNKNOWN", "_dispatch_claim": marker}
                        self._records[token] = existing
                        answer = {"claimed": True, "claim_id": marker, "record": dict(existing)}
                    else:
                        answer = {"claimed": False, "record": dict(existing)}
        except Exception as exc:
            if getattr(exc, "code", None) == RATE_LIMITED:
                raise
            raise refuse(DISPATCH_STATE_UNAVAILABLE, "attempt owner could not claim this delivery",
                         retry_safe=False, idempotency_durability=self.durability) from exc
        record = answer.get("record") or {}
        if record.get("intent_digest") != digest:
            raise refuse(IDEMPOTENCY_CONFLICT, "this key already names a different request; nothing sent")
        if answer.get("claimed") is True and answer.get("claim_id"):
            return Claim(token, digest, actor, answer["claim_id"], verb=verb,
                         expected_project_path=expected_project_path)
        if record.get("phase") == "ACCEPTED" and isinstance(record.get("result"), dict):
            receipt = record["result"]
            try:
                if project:
                    if (record.get("verb") != "project.create"
                            or set(receipt) != PROJECT_RECEIPT_FIELDS
                            or record.get("expected_project_path") != expected_project_path):
                        raise ValueError("misbound project receipt")
                    delivery_receipt(receipt, project=True)
                    if receipt["project_path"] != expected_project_path:
                        raise ValueError("project receipt path differs from intent")
                    return Claim(token, digest, actor, None, dict(receipt), verb=verb,
                                 expected_project_path=expected_project_path)
                if record.get("verb") == "project.create":
                    raise ValueError("a session replay cannot contain a project receipt")
                if set(receipt) - RECEIPT_FIELDS or receipt.get("accepted") is not True:
                    raise ValueError("unexpected receipt field")
                ref = SessionKey.parse(receipt["session_ref"])
                replay_record = {"session": ref.as_dict(), **receipt}
                if verb == "send":
                    replay_record["display_name"] = ref.as_dict()
                delivery_receipt(replay_record)
                if (ref.device_ref != device_ref or ref.provider != runtime
                        or (session_ref and ref.ref != session_ref)):
                    raise ValueError("receipt target differs from original intent")
            except Exception as exc:
                raise refuse(DISPATCH_STATE_UNAVAILABLE, "stored receipt is malformed or misbound",
                             retry_safe=False) from exc
            replay = {"session": ref.as_dict(), "display_name": ref.as_dict(),
                      "run_id": receipt.get("run_id"),
                      "queued_message_uuid": receipt.get("queued_message_uuid"), "accepted": True}
            if "delivery_id" in receipt:
                replay["delivery_id"] = receipt["delivery_id"]
            return Claim(token, digest, actor, None, replay)
        raise refuse(DISPATCH_OUTCOME_UNKNOWN,
                     "earlier delivery may have happened; reconcile its session/run before retrying",
                     retry_safe=False, idempotency_durability=self.durability)

    def complete(self, claim: Claim, record: dict[str, Any]) -> None:
        try:
            project = claim.verb == "project.create"
            receipt = delivery_receipt(record, project=project)
            if project and receipt["project_path"] != claim.expected_project_path:
                raise ValueError("project acknowledgement differs from the claimed path")
            if self.owner is not None:
                self.owner.complete_dispatch_claim(
                    claim.key, digest=claim.digest, claim_id=claim.claim_id,
                    actor=claim.actor, receipt=receipt)
            else:
                with self._lock:
                    entry = self._records[claim.key]
                    if (entry.get("_dispatch_claim") != claim.claim_id
                            or entry["phase"] != "EFFECT_UNKNOWN"):
                        raise ValueError("delivery claim changed")
                    entry.update(phase="ACCEPTED", result=receipt)
                    entry.pop("_dispatch_claim", None)
        except Exception as exc:
            raise refuse(DISPATCH_OUTCOME_UNKNOWN,
                         "delivery may have happened but its ACK could not be recorded",
                         retry_safe=False, idempotency_durability=self.durability) from exc
