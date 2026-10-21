"""A' prototype: one writer object as the API-only authority/effect entry.

STATUS: PROTOTYPE_ONLY / NOT_V1_SIGNED / V1_NOT_RELEASE.

This is an experiment in a **single restricted mutator contract**, not OS
isolation and not a production authorization.

  A / API_ONLY_CORRECTNESS
      A caller holding only the `SingleWriteEntry` API can issue synthetic
      owner facts, acquire a fenced lease, obtain an in-process capability,
      run a use-check, and write an effect marker. All authority/lease/commit/
      effect-marker writes happen in the same temp-SQLite `BEGIN IMMEDIATE`
      transaction. The caller cannot supply uid/actor/clock/policy/epoch or
      rewrite the commit through the API; extra keyword arguments are rejected
      and a tampered capability is refused.

  B / RAW_DB_WRITE_BYPASS_EXPECTED
      Anyone who can write the same SQLite file can bypass the API entirely:
      insert an effect marker, drop the experimental triggers, or rewrite
      authority rows. That is EXPECTED and is why the honest classification is
      BLOCKED_DB_WRITER_ISOLATION. This module does not claim otherwise.

  C / OS_ISOLATION_NOT_RUN
      There is no real OS uid/ACL/socket/key separation here. The capability
      key is an in-process test secret, not an owner key. No chmod/chown,
      production socket, real daemon or real OS user is used.

  EFFECT_COMMIT_NOT_PROVEN
      `proto_effect` is a prototype marker, not Work Engine
      `finalize_run_verified` or a production effect. Real effect/finalize in
      the same transaction would require an owner-approved WorkEngine change.

SAME-PROCESS CAVEAT
-------------------
This is still an in-process object. A holder can call the synthetic owner
issue/revoke/rebind methods, reach `_writer`/`db_path`, and read the in-process
capability key. The HMAC capability binds the commit row and catches a caller
who mixes request/token/capability, but it does not establish OS isolation or
a real owner key.

Flags:
  NOT_V1_SIGNED              no production authorization
  BLOCKED_DB_WRITER_ISOLATION same-file raw SQL writer can bypass
  RAW_DB_WRITE_BYPASS_EXPECTED classified, not hidden
  OS_ISOLATION_NOT_RUN       no UID/ACL/socket/key isolation
  EFFECT_COMMIT_NOT_PROVEN   marker only, no production finalize
"""
from __future__ import annotations

import hashlib
import hmac
import importlib.util
import json
import secrets
import sqlite3
import sys
import time
from pathlib import Path

# Load the sibling prototype under the same module name the tests use, so
# PrototypeRefusal/Prototype classes are identical across both modules.
_ATOMIC_NAME = "s1_atomic_fence_prototype"
if _ATOMIC_NAME in sys.modules:
    atomic = sys.modules[_ATOMIC_NAME]
else:
    _path = Path(__file__).with_name("atomic_fence.py")
    _spec = importlib.util.spec_from_file_location(_ATOMIC_NAME, _path)
    atomic = importlib.util.module_from_spec(_spec)
    sys.modules[_ATOMIC_NAME] = atomic
    _spec.loader.exec_module(atomic)

PrototypeRefusal = atomic.PrototypeRefusal

PROTOTYPE_OWNER_ISSUED = "PROTOTYPE_OWNER_ISSUED"
PROTOTYPE_USE_CHECK_OK = "PROTOTYPE_USE_CHECK_OK"
PROTOTYPE_EFFECT_MARKER_COMMITTED = "PROTOTYPE_EFFECT_MARKER_COMMITTED"
BLOCKED_CAPABILITY = "BLOCKED_CAPABILITY"
BLOCKED_DB_WRITER_ISOLATION = "BLOCKED_DB_WRITER_ISOLATION"
API_ONLY_CORRECTNESS = "API_ONLY_CORRECTNESS"
RAW_DB_WRITE_BYPASS_EXPECTED = "RAW_DB_WRITE_BYPASS_EXPECTED"
OS_ISOLATION_NOT_RUN = "OS_ISOLATION_NOT_RUN"
EFFECT_COMMIT_NOT_PROVEN = "EFFECT_COMMIT_NOT_PROVEN"

_CAP_FIELDS = (
    "request_id", "resource", "owner", "token", "grant_ref", "policy_ref",
    "context_ref", "context_actor", "context_epoch", "grant_epoch",
    "policy_epoch", "authority_epoch", "expires_at",
)


def classification() -> dict:
    """The honest A/B/C classification this experiment reports."""
    return {
        "A": {"name": API_ONLY_CORRECTNESS,
              "scope": "capability-mediated API; temp SQLite; synthetic owner"},
        "B": {"name": RAW_DB_WRITE_BYPASS_EXPECTED,
              "scope": "same-file raw SQL writer bypasses the API",
              "blocked": BLOCKED_DB_WRITER_ISOLATION},
        "C": {"name": OS_ISOLATION_NOT_RUN,
              "scope": "no real UID/ACL/socket/owner-key isolation"},
        "effect_commit": EFFECT_COMMIT_NOT_PROVEN,
        "production": "NOT_V1_SIGNED",
    }


def _capability_material(mapping) -> bytes:
    binding = {field: mapping[field] for field in _CAP_FIELDS}
    return json.dumps(binding, sort_keys=True, separators=(",", ":"),
                      default=str).encode()


class SingleWriteEntry:
    """The one restricted writer object for the prototype.

    Owner-side methods (`issue`, `issue_grant`, `revoke_policy`,
    `rebind_context`) are synthetic fixture operations. Caller-side methods
    (`acquire`, `check_use`, `commit_effect`) never accept attributes, policy,
    grant, epoch, owner, uid, actor, clock or SQL from the caller.
    """

    def __init__(self, state_dir, *, initialize: bool = True, clock=None,
                 synthetic_peer_uid=None, actor_ref=None,
                 capability_key=None) -> None:
        self._writer = atomic.AtomicFencePrototype(
            state_dir, initialize=initialize, clock=clock,
            synthetic_peer_uid=synthetic_peer_uid)
        self._actor = str(actor_ref).strip() if actor_ref else ""
        if capability_key is None:
            capability_key = secrets.token_bytes(32)
        if isinstance(capability_key, str):
            capability_key = capability_key.encode()
        self._capability_key = bytes(capability_key)
        if initialize:
            self._ensure_effect_table()

    def _ensure_effect_table(self) -> None:
        with self._writer._transaction() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS proto_effect (
                    effect_ref TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL UNIQUE,
                    resource TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    token INTEGER NOT NULL,
                    payload_digest TEXT NOT NULL,
                    committed_at TEXT NOT NULL,
                    context_epoch INTEGER NOT NULL,
                    grant_epoch INTEGER NOT NULL,
                    policy_epoch INTEGER NOT NULL,
                    authority_epoch INTEGER NOT NULL
                )
            """)

    # -- synthetic owner side ---------------------------------------------

    def issue(self, *, policy_ref, grant_ref, context_ref, projects, items,
              actions, valid_from, expires_at, issued_at,
              policy_epoch=1, authority_epoch=1, grant_epoch=1) -> dict:
        """Synthetic owner issuance: policy + grant + context in ONE transaction."""
        if not self._actor:
            raise PrototypeRefusal(
                "UNTRUSTED_ACTOR", "the writer has no bound synthetic actor")
        peer_uid = self._writer._synthetic_peer_uid
        if isinstance(peer_uid, bool) or not isinstance(peer_uid, int):
            raise PrototypeRefusal(
                "UNTRUSTED_ACTOR", "the writer has no bound synthetic peer uid")
        principal = f"uid:{peer_uid}"
        pref = atomic._text(policy_ref, "policy_ref")
        gref = atomic._text(grant_ref, "grant_ref")
        cref = atomic._text(context_ref, "context_ref")
        projects = atomic._string_list(projects, "projects")
        items = atomic._string_list(items, "items")
        actions = atomic._string_list(actions, "actions")
        atomic._int(policy_epoch, "policy_epoch")
        atomic._int(authority_epoch, "authority_epoch")
        atomic._int(grant_epoch, "grant_epoch")
        try:
            with self._writer._transaction() as conn:
                conn.execute(
                    "INSERT INTO proto_owner_policy("
                    "policy_ref,policy_epoch,authority_epoch,os_principal,"
                    "actor_ref,projects_json,items_json,actions_json,"
                    "valid_from,expires_at,revoked,issued_by,issued_at,"
                    "owner_attestation) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (pref, policy_epoch, authority_epoch, principal,
                     self._actor, json.dumps(projects), json.dumps(items),
                     json.dumps(actions), str(valid_from), str(expires_at),
                     0, "owner:simulated", str(issued_at),
                     "simulated-owner-attestation"))
                conn.execute(
                    "INSERT INTO proto_grant("
                    "grant_ref,policy_ref,grant_epoch,owner_policy_epoch,"
                    "os_principal,actor_ref,projects_json,items_json,"
                    "actions_json,valid_from,expires_at,revoked) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (gref, pref, grant_epoch, policy_epoch, principal,
                     self._actor, json.dumps(projects), json.dumps(items),
                     json.dumps(actions), str(valid_from), str(expires_at), 0))
                conn.execute(
                    "INSERT INTO proto_context(context_ref,actor_ref,grant_ref,"
                    "context_epoch) VALUES (?,?,?,1)",
                    (cref, self._actor, gref))
        except sqlite3.IntegrityError as exc:
            raise PrototypeRefusal(
                "ALREADY_ISSUED", f"the synthetic owner facts already exist: {exc}"
            ) from exc
        return {
            "prototype_status": PROTOTYPE_OWNER_ISSUED,
            "simulated_owner": True,
            "policy_ref": pref, "grant_ref": gref, "context_ref": cref,
            "context_epoch": 1, "production_authorization": False,
        }

    def issue_grant(self, *, grant_ref, policy_ref, projects, items, actions,
                    valid_from, expires_at, grant_epoch=1,
                    owner_policy_epoch=1) -> dict:
        """Synthetic owner grant issuance in one transaction (test rebind target)."""
        if not self._actor:
            raise PrototypeRefusal(
                "UNTRUSTED_ACTOR", "the writer has no bound synthetic actor")
        peer_uid = self._writer._synthetic_peer_uid
        principal = f"uid:{peer_uid}"
        gref = atomic._text(grant_ref, "grant_ref")
        pref = atomic._text(policy_ref, "policy_ref")
        projects = atomic._string_list(projects, "projects")
        items = atomic._string_list(items, "items")
        actions = atomic._string_list(actions, "actions")
        atomic._int(grant_epoch, "grant_epoch")
        atomic._int(owner_policy_epoch, "owner_policy_epoch")
        try:
            with self._writer._transaction() as conn:
                conn.execute(
                    "INSERT INTO proto_grant("
                    "grant_ref,policy_ref,grant_epoch,owner_policy_epoch,"
                    "os_principal,actor_ref,projects_json,items_json,"
                    "actions_json,valid_from,expires_at,revoked) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (gref, pref, grant_epoch, owner_policy_epoch, principal,
                     self._actor, json.dumps(projects), json.dumps(items),
                     json.dumps(actions), str(valid_from), str(expires_at), 0))
        except sqlite3.IntegrityError as exc:
            raise PrototypeRefusal(
                "ALREADY_ISSUED", f"the synthetic grant already exists: {exc}"
            ) from exc
        return {"prototype_status": PROTOTYPE_OWNER_ISSUED,
                "simulated_owner": True, "grant_ref": gref,
                "production_authorization": False}

    def revoke_policy(self, policy_ref, *, expected_policy_epoch) -> dict:
        return self._writer.revoke_policy(
            policy_ref, expected_policy_epoch=expected_policy_epoch)

    def rebind_context(self, context_ref, *, expected_context_epoch,
                       grant_ref=None, actor_ref=None) -> dict:
        return self._writer.rebind_context(
            context_ref, expected_context_epoch=expected_context_epoch,
            grant_ref=grant_ref, actor_ref=actor_ref)

    # -- API-only caller side ---------------------------------------------

    def _capability(self, mapping) -> str:
        return hmac.new(self._capability_key, _capability_material(mapping),
                        hashlib.sha256).hexdigest()

    def acquire(self, request) -> dict:
        """Acquire through the prototype.

        Only a fresh `PROTOTYPE_FENCED_COMMIT` receipt gets an in-process
        capability. A historical replay (`authorized:false`, `historical:true`,
        `replayed:true`) is audit-only: the capability field is omitted and
        `capability_scope` is `HISTORICAL_AUDIT_ONLY`. The ORIGINAL capability
        from the first acquire is not revoked by a replay; it stays valid only
        while the lease/epoch fence remains live.
        """
        receipt = dict(self._writer.acquire(request))
        receipt["production_effect_eligible"] = False
        fresh = (
            receipt.get("prototype_status") == atomic.PROTOTYPE_FENCED_COMMIT
            and receipt.get("authorized") is True
            and receipt.get("historical") is False
            and receipt.get("replayed") is False
        )
        if fresh:
            receipt["capability"] = self._capability(receipt)
            receipt["capability_scope"] = "API_ONLY"
        else:
            # Fail closed: an unknown/historical status never gets a capability.
            receipt.pop("capability", None)
            receipt["capability_scope"] = "HISTORICAL_AUDIT_ONLY"
        return receipt

    def check_use(self, request_id, token, capability) -> dict:
        """Use-check in one transaction; no effect marker is written.

        The caller token must equal the committed token for this request_id;
        the fencer receives that same commit row (never a re-query by token).
        """
        if not isinstance(capability, str) or not capability:
            raise PrototypeRefusal(BLOCKED_CAPABILITY, "no capability presented")
        try:
            now_e = atomic._lease_now(self._writer._clock())
        except atomic.LeaseError as exc:
            raise PrototypeRefusal(exc.reason_code, exc.detail) from exc
        with self._writer._transaction() as conn:
            commit = conn.execute(
                "SELECT * FROM proto_commit WHERE request_id=?",
                (str(request_id),)).fetchone()
            if commit is None:
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY", "no fenced commit for this request")
            if int(commit["token"]) != int(token):
                raise PrototypeRefusal(
                    "STALE_TOKEN",
                    "the caller token is not this request's committed token")
            if not hmac.compare_digest(
                    self._capability(commit), str(capability)):
                raise PrototypeRefusal(
                    BLOCKED_CAPABILITY,
                    "the capability does not bind this committed authority")
            checked = self._writer._check_writer_fenced(
                conn, str(commit["resource"]), owner=str(commit["owner"]),
                token=token, now_e=now_e, commit=commit,
                expected_request_id=str(request_id))
        return {**checked,
                "request_id": str(request_id),
                "prototype_status": PROTOTYPE_USE_CHECK_OK,
                "capability_verified": True,
                "production_effect": False,
                "effect_eligible": False}

    def commit_effect(self, request_id, token, capability, *, effect_ref,
                      payload_digest, hold=None) -> dict:
        """Check the fence and write a prototype effect marker in ONE transaction.

        The exact committed request_id/resource/owner/token are bound before
        the fence. After the TEST-ONLY hold, the injected clock is read again
        in the SAME transaction and the exact commit fence is re-run; a TTL or
        policy/grant expiry rolls the marker back. A concurrent revoke cannot
        slip in because this transaction holds the SQLite write lock.
        """
        if not isinstance(capability, str) or not capability:
            raise PrototypeRefusal(BLOCKED_CAPABILITY, "no capability presented")
        effect_ref = atomic._text(effect_ref, "effect_ref")
        payload_digest = atomic._text(payload_digest, "payload_digest")
        try:
            now_e = atomic._lease_now(self._writer._clock())
        except atomic.LeaseError as exc:
            raise PrototypeRefusal(exc.reason_code, exc.detail) from exc
        with self._writer._transaction() as conn:
            commit = conn.execute(
                "SELECT * FROM proto_commit WHERE request_id=?",
                (str(request_id),)).fetchone()
            if commit is None:
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY", "no fenced commit for this request")
            if int(commit["token"]) != int(token):
                raise PrototypeRefusal(
                    "STALE_TOKEN",
                    "the caller token is not this request's committed token")
            if not hmac.compare_digest(
                    self._capability(commit), str(capability)):
                raise PrototypeRefusal(
                    BLOCKED_CAPABILITY,
                    "the capability does not bind this committed authority")
            checked = self._writer._check_writer_fenced(
                conn, str(commit["resource"]), owner=str(commit["owner"]),
                token=token, now_e=now_e, commit=commit,
                expected_request_id=str(request_id))
            existing = conn.execute(
                "SELECT * FROM proto_effect WHERE effect_ref=?",
                (effect_ref,)).fetchone()
            if existing is not None:
                if (str(existing["request_id"]) == str(request_id)
                        and int(existing["token"]) == int(token)
                        and str(existing["payload_digest"]) == payload_digest):
                    return self._effect_receipt(existing, replayed=True)
                raise PrototypeRefusal(
                    "IDEMPOTENCY_CONFLICT",
                    "this effect_ref was committed with other facts")
            existing_request = conn.execute(
                "SELECT * FROM proto_effect WHERE request_id=?",
                (str(request_id),)).fetchone()
            if existing_request is not None:
                raise PrototypeRefusal(
                    "EFFECT_ALREADY_COMMITTED",
                    "this lease commit already has an effect marker")
            committed_at = atomic._lease_stamp(now_e)
            conn.execute(
                "INSERT INTO proto_effect("
                "effect_ref,request_id,resource,owner,token,payload_digest,"
                "committed_at,context_epoch,grant_epoch,policy_epoch,"
                "authority_epoch) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (effect_ref, str(request_id), str(commit["resource"]),
                 str(commit["owner"]), int(token), payload_digest,
                 committed_at, int(checked["context_epoch"]),
                 int(checked["grant_epoch"]), int(checked["policy_epoch"]),
                 int(checked["authority_epoch"])))
            if hold is not None:
                hold()
            # COMMIT-PHASE RE-CHECK. Read the trusted clock again in the SAME
            # BEGIN IMMEDIATE after the marker insert/hold and re-run the exact
            # commit fence (lease/commit expiry, context/policy/grant windows).
            # A crossing rolls the proto_effect marker back; a concurrent
            # revoke cannot slip in because this transaction holds the write lock.
            final_now = atomic._lease_now(self._writer._clock())
            final_checked = self._writer._check_writer_fenced(
                conn, str(commit["resource"]), owner=str(commit["owner"]),
                token=token, now_e=final_now, commit=commit,
                expected_request_id=str(request_id))
            return {
                "prototype_status": PROTOTYPE_EFFECT_MARKER_COMMITTED,
                "simulated_owner": True,
                "authorized": True,
                "effect_marker": True,
                "production_effect": False,
                "effect_eligible": False,
                "capability_verified": True,
                "effect_ref": effect_ref,
                "request_id": str(request_id),
                "resource": str(commit["resource"]),
                "owner": str(commit["owner"]),
                "token": int(token),
                "payload_digest": payload_digest,
                "committed_at": committed_at,
                "context_epoch": int(final_checked["context_epoch"]),
                "grant_epoch": int(final_checked["grant_epoch"]),
                "policy_epoch": int(final_checked["policy_epoch"]),
                "authority_epoch": int(final_checked["authority_epoch"]),
                "replayed": False,
            }

    @staticmethod
    def _effect_receipt(row, *, replayed: bool) -> dict:
        return {
            "prototype_status": PROTOTYPE_EFFECT_MARKER_COMMITTED,
            "simulated_owner": True,
            "authorized": False if replayed else True,
            "effect_marker": True,
            "production_effect": False,
            "effect_eligible": False,
            "capability_verified": True,
            "effect_ref": str(row["effect_ref"]),
            "request_id": str(row["request_id"]),
            "resource": str(row["resource"]),
            "owner": str(row["owner"]),
            "token": int(row["token"]),
            "payload_digest": str(row["payload_digest"]),
            "committed_at": str(row["committed_at"]),
            "context_epoch": int(row["context_epoch"]),
            "grant_epoch": int(row["grant_epoch"]),
            "policy_epoch": int(row["policy_epoch"]),
            "authority_epoch": int(row["authority_epoch"]),
            "replayed": replayed,
        }

    # -- read helpers ------------------------------------------------------

    def effect(self, effect_ref) -> dict | None:
        with self._writer._transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT * FROM proto_effect WHERE effect_ref=?",
                (str(effect_ref),)).fetchone()
        return dict(row) if row is not None else None

    def context(self, context_ref) -> dict | None:
        return self._writer.context(context_ref)

    def commit(self, request_id) -> dict | None:
        return self._writer.commit(request_id)
