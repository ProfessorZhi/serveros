"""A' PROTOTYPE: owner policy/epoch and lease commit in one SQLite transaction.

STATUS: PROTOTYPE_HARDENING / NOT_V1_SIGNED / V1_NOT_RELEASE.

Flags:
  PROD_DEFAULT_BLOCKED       production #364 still returns BLOCKED_OWNER_COMMIT_SEAM
  NO_OWNER_TRUST             the only owner is a synthetic fixture; no owner key
  NO_DAEMON_WIRING           nothing here is imported by serverosd or WorkEngine
  NO_MIGRATION               the prototype adds tables to a TEMP DB only
  NOT_V1_SIGNED              this is not a production authorization
  NO_TRUSTED_CLOCK           the clock is a synthetic constructor fixture
  NO_TRUSTED_PEER            synthetic_peer_uid is a fixture; no SO_PEERCRED
  PRODUCTION_WIRING_REQUIRED legacy acquire/check_writer/work revoke/finalize
                             still bypass this fence

WHAT IS REAL
------------
The lease table, its schema, the SQLite connection settings, the `BEGIN
IMMEDIATE` transaction, the token allocation (`id_sequences` kind='lease') and
the timestamp/TTL rules are the real `workengine.store.ServerOSStore` schema /
helpers.  `AtomicFencePrototype` creates a temp `ServerOSStore` to materialize
that schema, then adds `proto_*` fence tables to the same DB.  It does NOT
modify `workengine/store.py` or `workengine/authority.py`, and it does not
call `ServerOSStore.acquire_lease` (that method opens its own transaction, so
it cannot include the fence row).

WHAT IS SIMULATED
-----------------
The owner policy and grant rows are synthetic fixtures.  The owner
"attestation" is a non-empty fixture string, NOT a signature and NOT a key.
The clock and `synthetic_peer_uid` are constructor-bound harness fixtures;
`acquire` accepts no per-call `now`/`peer_uid`, so a request field cannot
choose them.  They are still NOT the production store clock or kernel
`SO_PEERCRED`.  This prototype proves a transaction shape, not that a real
owner exists.

NOT PROVEN
----------
* Real owner identity/policy issuance and revocation.
* Production daemon/Fleet/WorkEngine wiring or migration.
* Real finalize/effect exactly-once: `ServerOSStore.finalize_run_verified`
  opens its own transaction, so lease + finalize are not composed here.
* Real legacy `ServerOSStore.acquire_lease`/`check_writer`, JSON `work revoke`,
  `finalize` or an effect being routed through this fence.
* Power-loss/fsync durability (`os._exit` is a process kill), two real OS uids,
  a production owner key, a live provider, OAuth or a send.

OUTPUT
------
A successful commit is named `PROTOTYPE_FENCED_COMMIT` and carries
`simulated_owner: True`, `authorized: True`, `historical: False`,
`effect_eligible: False`.  A replay is `PROTOTYPE_HISTORICAL_REPLAY` with
`authorized: False`, `historical: True`, `effect_eligible: False`.  Neither
has a `verified` key; neither is a production positive or an effect commit.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from workengine.store import (  # real WorkEngine semantics, imported read-only
    LeaseError,
    ServerOSStore,
    _lease_epoch,
    _lease_expiry,
    _lease_now,
    _lease_stamp,
    _lease_ttl,
)

PROTOTYPE_FENCED_COMMIT = "PROTOTYPE_FENCED_COMMIT"
PROTOTYPE_POLICY_REVOKED = "PROTOTYPE_POLICY_REVOKED"
SIMULATED_OWNER = "SIMULATED_OWNER"
PROTOTYPE_HISTORICAL_REPLAY = "PROTOTYPE_HISTORICAL_REPLAY"
PROTOTYPE_FENCED_CHECK = "PROTOTYPE_FENCED_CHECK"
POLICY_EXPIRED_AT_COMMIT = "POLICY_EXPIRED_AT_COMMIT"
GRANT_EXPIRED_AT_COMMIT = "GRANT_EXPIRED_AT_COMMIT"
LEASE_EXPIRED_AT_COMMIT = "LEASE_EXPIRED_AT_COMMIT"
RESOURCE_PREFIX = "work-write:"

_ALLOWED_REQUEST_KEYS = {
    "request_id", "context_ref", "project", "item_ref", "action", "ttl_s",
    "expected_authority_epoch", "expected_grant_epoch",
}


class PrototypeRefusal(Exception):
    """A prototype refusal.  Not a production `verified:false` envelope."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}")


def _text(value, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise PrototypeRefusal("INVALID_REQUEST", f"{field} must be a non-empty string")
    return value.strip()


def _int(value, field: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise PrototypeRefusal(
            "INVALID_REQUEST", f"{field} must be an integer >= {minimum}, not {value!r}")
    return value


def _string_list(value, field: str) -> list[str]:
    if (not isinstance(value, (list, tuple))
            or not all(isinstance(item, str) and item.strip() for item in value)):
        raise PrototypeRefusal(
            "INVALID_REQUEST", f"{field} must be a non-empty list of strings")
    return [item.strip() for item in value]


def _instant(value, field: str) -> float:
    try:
        parsed = datetime.datetime.fromisoformat(
            str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError) as exc:
        raise PrototypeRefusal(
            "BLOCKED_OWNER_POLICY",
            f"{field} {value!r} is not a readable zoned instant") from exc
    if parsed.tzinfo is None:
        raise PrototypeRefusal(
            "BLOCKED_OWNER_POLICY", f"{field} {value!r} has no timezone")
    return parsed.timestamp()


def _strict_flag(value, field: str) -> int:
    # bool is an int subclass; it is a data error here, not a revocation.
    if isinstance(value, bool) or not isinstance(value, int) or value not in (0, 1):
        raise PrototypeRefusal(
            "BLOCKED_OWNER_POLICY",
            f"{field} {value!r} is not the integer 0 or 1")
    return value


def _json_list(value, field: str) -> list[str]:
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise PrototypeRefusal(
            "BLOCKED_OWNER_POLICY", f"{field} is not readable JSON") from exc
    if (not isinstance(parsed, list) or not parsed
            or not all(isinstance(item, str) and item for item in parsed)):
        raise PrototypeRefusal(
            "BLOCKED_OWNER_POLICY", f"{field} is not a non-empty string list")
    if "*" in parsed:
        raise PrototypeRefusal(
            "BLOCKED_OWNER_POLICY", f"{field} contains a wildcard; scope is exact")
    return parsed


def _digest(request: dict, peer_uid: int) -> str:
    material = {
        "request_id": request["request_id"],
        "context_ref": request["context_ref"],
        "project": request["project"],
        "item_ref": request["item_ref"],
        "action": request["action"],
        "ttl_s": float(request["ttl_s"]),
        "expected_authority_epoch": request.get("expected_authority_epoch"),
        "expected_grant_epoch": request.get("expected_grant_epoch"),
        "peer_uid": peer_uid,
    }
    return hashlib.sha256(
        json.dumps(material, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class AtomicFencePrototype:
    """A temp SQLite authority fence + Work Engine lease commit.

    `initialize=True` materializes the real WorkEngine schema via
    `ServerOSStore` and then creates the `proto_*` fence tables.  A
    multi-process worker uses `initialize=False` and only opens the existing
    temp DB, so concurrent schema DDL never races.
    """

    def __init__(self, state_dir, *, initialize: bool = True, clock=None,
                 synthetic_peer_uid=None) -> None:
        path = Path(state_dir)
        if not path.is_absolute():
            raise ValueError("the prototype state directory must be absolute")
        path.mkdir(parents=True, exist_ok=True)
        self.state_dir = path
        self.db_path = path / "serveros.db"
        if initialize:
            # Real schema: projects/items/leases/id_sequences, WAL, and the
            # same connection settings the Work Engine uses.
            ServerOSStore(path)
            self._ensure_fence_tables()
        elif not self.db_path.is_file():
            raise PrototypeRefusal(
                "BLOCKED_STORAGE",
                f"{self.db_path} does not exist; initialize the prototype first")
        self._clock = clock or time.time
        # SYNTHETIC HARNESS INPUT, never a kernel credential. A production
        # entrypoint must read SO_PEERCRED instead.
        self._synthetic_peer_uid = synthetic_peer_uid

    # -- storage -----------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def _transaction(self, *, immediate: bool = True):
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _ensure_fence_tables(self) -> None:
        with self._transaction() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS proto_owner_policy (
                    policy_ref TEXT PRIMARY KEY,
                    policy_epoch INTEGER NOT NULL,
                    authority_epoch INTEGER NOT NULL,
                    os_principal TEXT NOT NULL,
                    actor_ref TEXT NOT NULL,
                    projects_json TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked INTEGER NOT NULL,
                    issued_by TEXT NOT NULL,
                    issued_at TEXT NOT NULL,
                    owner_attestation TEXT NOT NULL,
                    revoked_at TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS proto_grant (
                    grant_ref TEXT PRIMARY KEY,
                    policy_ref TEXT NOT NULL,
                    grant_epoch INTEGER NOT NULL,
                    owner_policy_epoch INTEGER NOT NULL,
                    os_principal TEXT NOT NULL,
                    actor_ref TEXT NOT NULL,
                    projects_json TEXT NOT NULL,
                    items_json TEXT NOT NULL,
                    actions_json TEXT NOT NULL,
                    valid_from TEXT NOT NULL,
                    expires_at TEXT NOT NULL,
                    revoked INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proto_context (
                    context_ref TEXT PRIMARY KEY,
                    actor_ref TEXT NOT NULL,
                    grant_ref TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proto_commit (
                    request_id TEXT PRIMARY KEY,
                    request_digest TEXT NOT NULL,
                    grant_ref TEXT NOT NULL,
                    policy_ref TEXT NOT NULL,
                    grant_epoch INTEGER NOT NULL,
                    policy_epoch INTEGER NOT NULL,
                    authority_epoch INTEGER NOT NULL,
                    resource TEXT NOT NULL,
                    owner TEXT NOT NULL,
                    token INTEGER NOT NULL,
                    expires_at TEXT NOT NULL,
                    committed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS proto_policy_event (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    policy_ref TEXT NOT NULL,
                    event TEXT NOT NULL,
                    at TEXT NOT NULL,
                    old_epoch INTEGER NOT NULL,
                    new_epoch INTEGER NOT NULL,
                    authority_epoch INTEGER NOT NULL
                );
            """)

    # -- synthetic owner fixtures -----------------------------------------

    def seed_policy(self, *, policy_ref, os_principal, actor_ref, projects,
                    items, actions, valid_from, expires_at, issued_at,
                    issued_by="owner:simulated",
                    owner_attestation="simulated-owner-attestation",
                    policy_epoch=1, authority_epoch=1, revoked=0) -> dict:
        ref = _text(policy_ref, "policy_ref")
        principal = _text(os_principal, "os_principal")
        actor = _text(actor_ref, "actor_ref")
        projects = _string_list(projects, "projects")
        items = _string_list(items, "items")
        actions = _string_list(actions, "actions")
        _int(policy_epoch, "policy_epoch")
        _int(authority_epoch, "authority_epoch")
        if isinstance(revoked, bool) or not isinstance(revoked, int):
            raise PrototypeRefusal("INVALID_REQUEST", "revoked must be an integer")
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO proto_owner_policy("
                "policy_ref,policy_epoch,authority_epoch,os_principal,actor_ref,"
                "projects_json,items_json,actions_json,valid_from,expires_at,"
                "revoked,issued_by,issued_at,owner_attestation) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (ref, policy_epoch, authority_epoch, principal, actor,
                 json.dumps(projects), json.dumps(items), json.dumps(actions),
                 str(valid_from), str(expires_at), revoked,
                 str(issued_by), str(issued_at), str(owner_attestation)))
        return self.policy(ref)

    def seed_grant(self, *, grant_ref, policy_ref, os_principal, actor_ref,
                   projects, items, actions, valid_from, expires_at,
                   grant_epoch=1, owner_policy_epoch=1, revoked=0) -> dict:
        ref = _text(grant_ref, "grant_ref")
        policy = _text(policy_ref, "policy_ref")
        principal = _text(os_principal, "os_principal")
        actor = _text(actor_ref, "actor_ref")
        projects = _string_list(projects, "projects")
        items = _string_list(items, "items")
        actions = _string_list(actions, "actions")
        _int(grant_epoch, "grant_epoch")
        _int(owner_policy_epoch, "owner_policy_epoch")
        if isinstance(revoked, bool) or not isinstance(revoked, int):
            raise PrototypeRefusal("INVALID_REQUEST", "revoked must be an integer")
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO proto_grant("
                "grant_ref,policy_ref,grant_epoch,owner_policy_epoch,"
                "os_principal,actor_ref,projects_json,items_json,actions_json,"
                "valid_from,expires_at,revoked) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (ref, policy, grant_epoch, owner_policy_epoch, principal,
                 actor, json.dumps(projects), json.dumps(items),
                 json.dumps(actions), str(valid_from), str(expires_at),
                 revoked))
        return self.grant(ref)

    def seed_context(self, *, context_ref, actor_ref, grant_ref) -> dict:
        ref = _text(context_ref, "context_ref")
        actor = _text(actor_ref, "actor_ref")
        grant = _text(grant_ref, "grant_ref")
        with self._transaction() as conn:
            conn.execute(
                "INSERT INTO proto_context(context_ref,actor_ref,grant_ref) "
                "VALUES (?,?,?)", (ref, actor, grant))
        return self.context(ref)

    # -- read helpers (test/audit only) -----------------------------------

    def policy(self, policy_ref) -> dict | None:
        with self._transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT * FROM proto_owner_policy WHERE policy_ref=?",
                (str(policy_ref),)).fetchone()
        return dict(row) if row is not None else None

    def grant(self, grant_ref) -> dict | None:
        with self._transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT * FROM proto_grant WHERE grant_ref=?",
                (str(grant_ref),)).fetchone()
        return dict(row) if row is not None else None

    def context(self, context_ref) -> dict | None:
        with self._transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT * FROM proto_context WHERE context_ref=?",
                (str(context_ref),)).fetchone()
        return dict(row) if row is not None else None

    def lease(self, resource: str) -> dict | None:
        with self._transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT resource,owner,token,expires_at FROM leases WHERE resource=?",
                (str(resource),)).fetchone()
        return dict(row) if row is not None else None

    def commit(self, request_id: str) -> dict | None:
        with self._transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT * FROM proto_commit WHERE request_id=?",
                (str(request_id),)).fetchone()
        return dict(row) if row is not None else None

    def commit_count(self) -> int:
        with self._transaction(immediate=False) as conn:
            return int(conn.execute("SELECT COUNT(*) FROM proto_commit").fetchone()[0])

    def token_next(self, resource: str) -> int | None:
        with self._transaction(immediate=False) as conn:
            row = conn.execute(
                "SELECT next_value FROM id_sequences WHERE scope=? AND kind='lease'",
                (str(resource),)).fetchone()
        return int(row["next_value"]) if row is not None else None

    # -- checks ------------------------------------------------------------

    @staticmethod
    def _check_policy(policy, now: float) -> None:
        if not str(policy["owner_attestation"] or "").strip():
            raise PrototypeRefusal(
                "BLOCKED_OWNER_POLICY",
                "the owner policy has no owner attestation (synthetic fixture)")
        if _strict_flag(policy["revoked"], "policy.revoked"):
            raise PrototypeRefusal(
                "POLICY_DENIED", f"{policy['policy_ref']} was revoked")
        _int(policy["policy_epoch"], "policy.policy_epoch")
        _int(policy["authority_epoch"], "policy.authority_epoch")
        if not str(policy["issued_by"] or "").strip():
            raise PrototypeRefusal(
                "BLOCKED_OWNER_POLICY", "the owner policy has no issued_by")
        if int(policy["authority_epoch"]) < int(policy["policy_epoch"]):
            raise PrototypeRefusal(
                "BLOCKED_OWNER_POLICY",
                "the authority epoch is behind the policy epoch")
        issued = _instant(policy["issued_at"], "policy.issued_at")
        valid_from = _instant(policy["valid_from"], "policy.valid_from")
        expires = _instant(policy["expires_at"], "policy.expires_at")
        if issued > now:
            raise PrototypeRefusal(
                "BLOCKED_OWNER_POLICY",
                "the owner policy was issued after the verification instant")
        if issued > expires:
            raise PrototypeRefusal(
                "BLOCKED_OWNER_POLICY",
                "the owner policy was issued after its own expiry")
        if now < valid_from:
            raise PrototypeRefusal(
                "POLICY_DENIED", "the owner policy is not yet valid")
        if now >= expires:
            raise PrototypeRefusal("POLICY_DENIED", "the owner policy expired")

    @staticmethod
    def _check_grant(grant, policy, now: float) -> None:
        if _strict_flag(grant["revoked"], "grant.revoked"):
            raise PrototypeRefusal(
                "POLICY_DENIED", f"{grant['grant_ref']} was revoked")
        _int(grant["grant_epoch"], "grant.grant_epoch")
        _int(grant["owner_policy_epoch"], "grant.owner_policy_epoch")
        if int(grant["owner_policy_epoch"]) != int(policy["policy_epoch"]):
            raise PrototypeRefusal(
                "BLOCKED_STALE_EPOCH",
                "the grant was issued under an older owner-policy epoch")
        valid_from = _instant(grant["valid_from"], "grant.valid_from")
        expires = _instant(grant["expires_at"], "grant.expires_at")
        if now < valid_from:
            raise PrototypeRefusal("POLICY_DENIED", "the grant is not yet valid")
        if now >= expires:
            raise PrototypeRefusal("POLICY_DENIED", "the grant expired")

    @staticmethod
    def _check_identity(policy, grant, context, peer_uid: int) -> None:
        peer_principal = f"uid:{peer_uid}"
        if not (str(policy["os_principal"]) == str(grant["os_principal"])
                == peer_principal):
            raise PrototypeRefusal(
                "UNTRUSTED_ACTOR",
                "the policy, grant and kernel peer principal do not agree")
        if not (str(policy["actor_ref"]) == str(grant["actor_ref"])
                == str(context["actor_ref"])):
            raise PrototypeRefusal(
                "UNTRUSTED_ACTOR",
                "the policy, grant and trusted context actor do not agree")

    @staticmethod
    def _check_scope(policy, grant, project: str, item_ref: str,
                     action: str) -> None:
        policy_scope = {
            "projects": _json_list(policy["projects_json"], "policy.projects"),
            "items": _json_list(policy["items_json"], "policy.items"),
            "actions": _json_list(policy["actions_json"], "policy.actions"),
        }
        grant_scope = {
            "projects": _json_list(grant["projects_json"], "grant.projects"),
            "items": _json_list(grant["items_json"], "grant.items"),
            "actions": _json_list(grant["actions_json"], "grant.actions"),
        }
        requested = {"projects": project, "items": item_ref, "actions": action}
        for field in ("projects", "items", "actions"):
            if not set(grant_scope[field]) <= set(policy_scope[field]):
                raise PrototypeRefusal(
                    "POLICY_DENIED",
                    f"the grant {field} are not inside the owner policy")
            if requested[field] not in grant_scope[field]:
                raise PrototypeRefusal(
                    "POLICY_DENIED",
                    f"{requested[field]!r} is not in the grant {field}")

    # -- the one fenced mutation ------------------------------------------

    def acquire(self, request, *, hold=None) -> dict:
        """Read the fence and write the lease in ONE `BEGIN IMMEDIATE`.

        The trusted inputs are constructor-bound SYNTHETIC harness facts:
        `clock` and `synthetic_peer_uid`. The request cannot name them, and
        `acquire` accepts no per-call `now` or `peer_uid`. A production
        entrypoint must supply the store clock and kernel `SO_PEERCRED`
        instead; this prototype cannot do that.

        `hold` is a TEST-ONLY hook invoked after the lease/commit rows are
        written and before the commit-phase window re-check. The clock is read
        again after the hold, inside the same `BEGIN IMMEDIATE`; a TTL or
        policy/grant expiry is rolled back atomically.
        """
        if not isinstance(request, dict):
            raise PrototypeRefusal("INVALID_REQUEST", "request must be an object")
        extra = set(request) - _ALLOWED_REQUEST_KEYS
        if extra:
            raise PrototypeRefusal(
                "INVALID_REQUEST",
                f"caller-supplied authority fields are not accepted: {sorted(extra)}")
        request_id = _text(request.get("request_id"), "request_id")
        context_ref = _text(request.get("context_ref"), "context_ref")
        project = _text(request.get("project"), "project")
        item_ref = _text(request.get("item_ref"), "item_ref")
        action = _text(request.get("action"), "action")
        try:
            ttl = _lease_ttl(request.get("ttl_s"))
        except LeaseError as exc:
            raise PrototypeRefusal(exc.reason_code, exc.detail) from exc
        try:
            now_e = _lease_now(self._clock())
        except LeaseError as exc:
            raise PrototypeRefusal(exc.reason_code, exc.detail) from exc
        peer_uid = self._synthetic_peer_uid
        if isinstance(peer_uid, bool) or not isinstance(peer_uid, int) or peer_uid < 0:
            raise PrototypeRefusal(
                "UNTRUSTED_ACTOR",
                "the harness bound no synthetic peer uid; production must use "
                "SO_PEERCRED, not a caller argument")
        expected = request.get("expected_authority_epoch")
        if expected is not None:
            _int(expected, "expected_authority_epoch")
        expected_grant = request.get("expected_grant_epoch")
        if expected_grant is not None:
            _int(expected_grant, "expected_grant_epoch")
        digest = _digest(request, peer_uid)

        with self._transaction() as conn:
            replay = conn.execute(
                "SELECT * FROM proto_commit WHERE request_id=?",
                (request_id,)).fetchone()
            if replay is not None:
                if str(replay["request_digest"]) != digest:
                    raise PrototypeRefusal(
                        "IDEMPOTENCY_CONFLICT",
                        "the request id was already committed with other facts")
                # Historical audit only: never current authority and never
                # eligible for an effect commit.
                return self._receipt(replay)

            context = conn.execute(
                "SELECT actor_ref,grant_ref FROM proto_context WHERE context_ref=?",
                (context_ref,)).fetchone()
            if context is None:
                raise PrototypeRefusal(
                    "UNTRUSTED_ACTOR", "the context_ref is not a trusted context")
            grant = conn.execute(
                "SELECT * FROM proto_grant WHERE grant_ref=?",
                (str(context["grant_ref"]),)).fetchone()
            if grant is None:
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY", "the context has no grant")
            policy = conn.execute(
                "SELECT * FROM proto_owner_policy WHERE policy_ref=?",
                (str(grant["policy_ref"]),)).fetchone()
            if policy is None:
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY", "the grant has no owner policy")

            self._check_policy(policy, now_e)
            self._check_grant(grant, policy, now_e)
            self._check_identity(policy, grant, context, peer_uid)
            self._check_scope(policy, grant, project, item_ref, action)
            if action != "lease.acquire":
                raise PrototypeRefusal(
                    "ACTION_NOT_GRANTED", "this prototype commits acquire only")
            if expected is not None and int(expected) != int(policy["authority_epoch"]):
                raise PrototypeRefusal(
                    "STALE_AUTHORITY_EPOCH",
                    f"request pins authority epoch {expected}, current is "
                    f"{policy['authority_epoch']}")
            if (expected_grant is not None
                    and int(expected_grant) != int(grant["grant_epoch"])):
                raise PrototypeRefusal(
                    "STALE_GRANT_EPOCH",
                    f"request pins grant epoch {expected_grant}, current is "
                    f"{grant['grant_epoch']}")

            resource = f"{RESOURCE_PREFIX}{item_ref}"
            existing = conn.execute(
                "SELECT resource,owner,token,expires_at FROM leases WHERE resource=?",
                (resource,)).fetchone()
            if existing is not None and _lease_epoch(existing["expires_at"]) > now_e:
                raise PrototypeRefusal(
                    "LEASE_HELD_BY_OTHER",
                    f"{resource!r} is held by {existing['owner']!r} "
                    f"(token {existing['token']})")
            token = self._next_token(conn, resource)
            expires_at = _lease_expiry(now_e, ttl)
            if existing is None:
                conn.execute(
                    "INSERT INTO leases(resource,owner,token,expires_at) "
                    "VALUES (?,?,?,?)",
                    (resource, str(policy["actor_ref"]), token, expires_at))
            else:
                conn.execute(
                    "UPDATE leases SET owner=?,token=?,expires_at=? WHERE resource=?",
                    (str(policy["actor_ref"]), token, expires_at, resource))
            committed_at = _lease_stamp(now_e)
            conn.execute(
                "INSERT INTO proto_commit("
                "request_id,request_digest,grant_ref,policy_ref,grant_epoch,"
                "policy_epoch,authority_epoch,resource,owner,token,expires_at,"
                "committed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (request_id, digest, str(grant["grant_ref"]),
                 str(policy["policy_ref"]), int(grant["grant_epoch"]),
                 int(policy["policy_epoch"]), int(policy["authority_epoch"]),
                 resource,
                 str(policy["actor_ref"]), token, expires_at, committed_at))
            if hold is not None:
                hold()
            # COMMIT-PHASE RE-CHECK. Read the injected trusted clock AGAIN
            # inside this same BEGIN IMMEDIATE, after the effect/hold, and
            # re-check every window before COMMIT. A clock that crossed TTL or
            # a policy/grant expiry raises here and the transaction rolls back
            # the lease, the id_sequences token and proto_commit together.
            final_now = _lease_now(self._clock())
            self._check_commit_windows(policy, grant, expires_at, final_now)
            return {
                "prototype_status": PROTOTYPE_FENCED_COMMIT,
                "simulated_owner": True,
                "owner_status": SIMULATED_OWNER,
                "authorized": True,
                "historical": False,
                "effect_eligible": False,
                "request_id": request_id,
                "grant_ref": str(grant["grant_ref"]),
                "policy_ref": str(policy["policy_ref"]),
                "grant_epoch": int(grant["grant_epoch"]),
                "policy_epoch": int(policy["policy_epoch"]),
                "authority_epoch": int(policy["authority_epoch"]),
                "resource": resource,
                "owner": str(policy["actor_ref"]),
                "token": int(token),
                "expires_at": expires_at,
                "committed_at": committed_at,
                "replayed": False,
            }

    @staticmethod
    def _check_commit_windows(policy, grant, lease_expires_at, now) -> None:
        """The commit-phase window re-check; raises to roll the transaction back."""
        if not str(policy["issued_by"] or "").strip():
            raise PrototypeRefusal(
                "BLOCKED_OWNER_POLICY", "the owner policy has no issued_by")
        if int(policy["authority_epoch"]) < int(policy["policy_epoch"]):
            raise PrototypeRefusal(
                "BLOCKED_OWNER_POLICY",
                "the authority epoch is behind the policy epoch")
        issued = _instant(policy["issued_at"], "policy.issued_at")
        if issued > now:
            raise PrototypeRefusal(
                "BLOCKED_OWNER_POLICY",
                "the owner policy is issued after the commit clock")
        policy_from = _instant(policy["valid_from"], "policy.valid_from")
        policy_to = _instant(policy["expires_at"], "policy.expires_at")
        grant_from = _instant(grant["valid_from"], "grant.valid_from")
        grant_to = _instant(grant["expires_at"], "grant.expires_at")
        if now < policy_from or now < grant_from:
            raise PrototypeRefusal(
                "POLICY_DENIED",
                "the clock moved before the policy/grant window at commit")
        if now >= policy_to:
            raise PrototypeRefusal(
                POLICY_EXPIRED_AT_COMMIT,
                "the owner policy expired before COMMIT")
        if now >= grant_to:
            raise PrototypeRefusal(
                GRANT_EXPIRED_AT_COMMIT, "the grant expired before COMMIT")
        if _lease_epoch(lease_expires_at) <= now:
            raise PrototypeRefusal(
                LEASE_EXPIRED_AT_COMMIT, "the lease TTL lapsed before COMMIT")
        if _strict_flag(policy["revoked"], "policy.revoked"):
            raise PrototypeRefusal(
                "POLICY_DENIED", "the owner policy was revoked before COMMIT")
        if _strict_flag(grant["revoked"], "grant.revoked"):
            raise PrototypeRefusal(
                "POLICY_DENIED", "the grant was revoked before COMMIT")
        if int(grant["owner_policy_epoch"]) != int(policy["policy_epoch"]):
            raise PrototypeRefusal(
                "BLOCKED_STALE_EPOCH",
                "the policy epoch changed before COMMIT")

    def check_writer_fenced(self, resource, *, owner, token) -> dict:
        """Prototype-only writer check that re-reads the authority epoch NOW.

        `ServerOSStore.check_writer` reads only the lease row, so a token
        acquired before a revoke still looks authorized. This method reads the
        `proto_commit` row, the current policy and the current grant in one
        `BEGIN IMMEDIATE`, checks the commit/grant/policy lineage and refuses
        after a revoke or a policy/authority/grant epoch change. It is still
        NOT an effect transaction: a caller must hold the fence across its
        effect.
        """
        if not isinstance(resource, str) or not resource.strip():
            raise PrototypeRefusal("NOT_HOLDER", "a lease names a resource")
        if not isinstance(owner, str) or not owner.strip():
            raise PrototypeRefusal("NOT_HOLDER", "an owner must be a string")
        if isinstance(token, bool) or not isinstance(token, int):
            raise PrototypeRefusal("STALE_TOKEN", "token must be an integer")
        try:
            now_e = _lease_now(self._clock())
        except LeaseError as exc:
            raise PrototypeRefusal(exc.reason_code, exc.detail) from exc
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT resource,owner,token,expires_at FROM leases WHERE resource=?",
                (str(resource),)).fetchone()
            if row is None:
                raise PrototypeRefusal("NOT_HOLDER", "no lease row")
            if str(row["owner"]) != str(owner) or int(row["token"]) != token:
                raise PrototypeRefusal(
                    "STALE_TOKEN", "the caller is not the current holder")
            if _lease_epoch(row["expires_at"]) <= now_e:
                raise PrototypeRefusal("LEASE_EXPIRED", "the lease expired")
            commit = conn.execute(
                "SELECT * FROM proto_commit WHERE resource=? AND token=? "
                "ORDER BY rowid DESC LIMIT 1",
                (str(resource), token)).fetchone()
            if commit is None:
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY",
                    "the lease has no fenced commit row")
            policy = conn.execute(
                "SELECT * FROM proto_owner_policy WHERE policy_ref=?",
                (str(commit["policy_ref"]),)).fetchone()
            if policy is None:
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY", "the fenced commit has no policy")
            grant = conn.execute(
                "SELECT * FROM proto_grant WHERE grant_ref=?",
                (str(commit["grant_ref"]),)).fetchone()
            if grant is None:
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY", "the fenced commit has no grant")
            # The commit row, the grant row and the policy row must be the
            # same authority lineage, and the grant generation must still be
            # the one the lease was committed under. A bump of grant_epoch
            # invalidates the historical token even when policy/authority
            # epochs are unchanged.
            if str(commit["grant_ref"]) != str(grant["grant_ref"]):
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY",
                    "the fenced commit's grant_ref does not match the grant")
            if str(grant["policy_ref"]) != str(commit["policy_ref"]):
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY",
                    "the grant's policy_ref does not match the commit")
            if int(commit["grant_epoch"]) != int(grant["grant_epoch"]):
                raise PrototypeRefusal(
                    "BLOCKED_STALE_EPOCH",
                    f"the grant epoch moved from {commit['grant_epoch']} to "
                    f"{grant['grant_epoch']}; the token is historical")
            self._check_commit_windows(policy, grant, row["expires_at"], now_e)
            if _strict_flag(policy["revoked"], "policy.revoked"):
                raise PrototypeRefusal(
                    "POLICY_DENIED", "the owner policy is revoked")
            if int(commit["policy_epoch"]) != int(policy["policy_epoch"]):
                raise PrototypeRefusal(
                    "BLOCKED_STALE_EPOCH", "the policy epoch moved after commit")
            if int(commit["authority_epoch"]) != int(policy["authority_epoch"]):
                raise PrototypeRefusal(
                    "BLOCKED_STALE_EPOCH", "the authority epoch moved after commit")
            return {
                "prototype_status": PROTOTYPE_FENCED_CHECK,
                "simulated_owner": True,
                "owner_status": SIMULATED_OWNER,
                "authorized": True,
                "historical": False,
                "effect_eligible": False,
                "resource": str(resource),
                "owner": str(owner),
                "token": token,
                "grant_epoch": int(grant["grant_epoch"]),
                "policy_epoch": int(policy["policy_epoch"]),
                "authority_epoch": int(policy["authority_epoch"]),
            }

    def revoke_policy(self, policy_ref, *, expected_policy_epoch) -> dict:
        """Commit a synthetic owner revoke in the SAME SQLite transaction domain.

        The policy epoch and authority epoch advance together; an acquire that
        commits before this revoke is auditable, and one that starts after it
        sees the new epoch and refuses.
        """
        ref = _text(policy_ref, "policy_ref")
        expected = _int(expected_policy_epoch, "expected_policy_epoch")
        try:
            now_e = _lease_now(self._clock())
        except LeaseError as exc:
            raise PrototypeRefusal(exc.reason_code, exc.detail) from exc
        with self._transaction() as conn:
            row = conn.execute(
                "SELECT * FROM proto_owner_policy WHERE policy_ref=?",
                (ref,)).fetchone()
            if row is None:
                raise PrototypeRefusal(
                    "BLOCKED_OWNER_POLICY", "no owner policy to revoke")
            if _strict_flag(row["revoked"], "policy.revoked") == 1:
                return {
                    "prototype_status": PROTOTYPE_POLICY_REVOKED,
                    "simulated_owner": True,
                    "owner_status": SIMULATED_OWNER,
                    "policy_ref": ref,
                    "policy_epoch": int(row["policy_epoch"]),
                    "authority_epoch": int(row["authority_epoch"]),
                    "replayed": True,
                }
            if int(row["policy_epoch"]) != expected:
                raise PrototypeRefusal(
                    "STALE_POLICY_EPOCH",
                    f"expected policy epoch {expected}, current is "
                    f"{row['policy_epoch']}")
            new_policy_epoch = int(row["policy_epoch"]) + 1
            new_authority_epoch = int(row["authority_epoch"]) + 1
            revoked_at = _lease_stamp(now_e)
            conn.execute(
                "UPDATE proto_owner_policy SET revoked=1, policy_epoch=?, "
                "authority_epoch=?, revoked_at=? WHERE policy_ref=?",
                (new_policy_epoch, new_authority_epoch, revoked_at, ref))
            conn.execute(
                "INSERT INTO proto_policy_event("
                "policy_ref,event,at,old_epoch,new_epoch,authority_epoch) "
                "VALUES (?,?,?,?,?,?)",
                (ref, "revoked", revoked_at, expected, new_policy_epoch,
                 new_authority_epoch))
            return {
                "prototype_status": PROTOTYPE_POLICY_REVOKED,
                "simulated_owner": True,
                "owner_status": SIMULATED_OWNER,
                "policy_ref": ref,
                "policy_epoch": new_policy_epoch,
                "authority_epoch": new_authority_epoch,
                "replayed": False,
            }

    # -- internals ---------------------------------------------------------

    @staticmethod
    def _next_token(conn, resource: str) -> int:
        """The real `id_sequences` lease-token rule, inside our transaction."""
        row = conn.execute(
            "SELECT next_value FROM id_sequences WHERE scope=? AND kind='lease'",
            (resource,)).fetchone()
        if row is None:
            conn.execute(
                "INSERT INTO id_sequences(scope,kind,next_value) "
                "VALUES (?, 'lease', 2)", (resource,))
            return 1
        token = int(row["next_value"])
        conn.execute(
            "UPDATE id_sequences SET next_value=? WHERE scope=? AND kind='lease'",
            (token + 1, resource))
        return token

    @staticmethod
    def _receipt(row) -> dict:
        # A replay is a HISTORICAL audit record. It is not current authority,
        # and it cannot be handed to an effect commit.
        return {
            "prototype_status": PROTOTYPE_HISTORICAL_REPLAY,
            "simulated_owner": True,
            "owner_status": SIMULATED_OWNER,
            "authorized": False,
            "historical": True,
            "effect_eligible": False,
            "request_id": str(row["request_id"]),
            "grant_ref": str(row["grant_ref"]),
            "policy_ref": str(row["policy_ref"]),
            "grant_epoch": int(row["grant_epoch"]),
            "policy_epoch": int(row["policy_epoch"]),
            "authority_epoch": int(row["authority_epoch"]),
            "resource": str(row["resource"]),
            "owner": str(row["owner"]),
            "token": int(row["token"]),
            "expires_at": str(row["expires_at"]),
            "committed_at": str(row["committed_at"]),
            "replayed": True,
        }
