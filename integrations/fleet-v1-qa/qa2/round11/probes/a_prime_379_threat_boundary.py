#!/usr/bin/env python3
"""QA2 threat-boundary matrix for #379 @ 7245fc6b.

A = API-only caller (no direct SQL)
B = same-UID raw SQLite writer (new connection; INSERT/REPLACE/DROP TRIGGER)
C = different OS UID / real file permission (NOT_RUN: no sudo/setuid/chown)

Read-only against the exported exact head. Every DB is a temp WorkEngine-schema
SQLite DB; owner policy/grant/context/clock/peer are synthetic fixtures. No
production file, daemon, WorkEngine DB, credential, OAuth or service is used.

Modes:
  api    A: CAS/epoch/rollback/commit-time refusal/missing-fence fail-closed;
         rc 0 iff every API-only property holds.
  db     B: each case is EXPECTED_FAIL_UNTRUSTED_DB_WRITE. The code deliberately
         performs a same-UID raw SQLite tamper and records `ok:true` when the
         tamper SUCCEEDS (the boundary failed as predicted), rc 0 iff all
         predicted tampering is reproduced. This is not a product PASS.
  osuid  C: records NOT_RUN with the reason (no privilege change is allowed).

Evidence levels: LOCAL_REAL_SQLITE, SYNTHETIC_OWNER/CONTEXT/CLOCK/PEER,
REAL_TEMP; KERNEL_PEER_NOT_RUN, REAL_WINDOWS_NOT_RUN, PRODUCTION_DEPENDENCY.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import socket
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

SNAP = Path(os.environ.get(
    "A_PRIME_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-21/checkouts/a-prime-379-7245fc6")).resolve()
sys.path.insert(0, str(SNAP))
spec = importlib.util.spec_from_file_location(
    "qa2_atomic_fence_379_boundary",
    SNAP / "integrations" / "fleet-v1-atomic-prototype" / "atomic_fence.py")
proto = importlib.util.module_from_spec(spec)
sys.modules["qa2_atomic_fence_379_boundary"] = proto
spec.loader.exec_module(proto)

NOW = 2_000_000_000.0
UID = 4242
ACTOR = "human:A"
ACTOR_B = "human:B"
POLICY_REF = "policy:proto"
GRANT_REF = "grant:proto"
GRANT_REF_2 = "grant:proto-2"
CTX = "ctx:proto"
PROJECT = "serveros"
ITEM = "SRV-0001"
RES = "work-write:SRV-0001"
VALID_FROM = "2000-01-01T00:00:00Z"
EXPIRES_AT = "2999-01-01T00:00:00Z"
ISSUED_AT = "2026-01-01T00:00:00Z"
RESULTS = []


class FixedClock:
    def __init__(self, now=NOW):
        self.now = float(now)

    def set(self, now):
        self.now = float(now)

    def __call__(self):
        return self.now


def iso(epoch):
    import datetime
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def req(request_id="req:1", **overrides):
    request = {"request_id": request_id, "context_ref": CTX,
               "project": PROJECT, "item_ref": ITEM, "action": "lease.acquire",
               "ttl_s": 30.0}
    request.update(overrides)
    return request


def world(root, policy_expires=EXPIRES_AT, grant_expires=EXPIRES_AT):
    p = proto.AtomicFencePrototype(
        str(Path(root) / "proto"), clock=FixedClock(NOW),
        synthetic_peer_uid=UID)
    p.seed_policy(policy_ref=POLICY_REF, os_principal=f"uid:{UID}",
                  actor_ref=ACTOR, projects=[PROJECT], items=[ITEM],
                  actions=["lease.acquire"], valid_from=VALID_FROM,
                  expires_at=policy_expires, issued_at=ISSUED_AT)
    p.seed_grant(grant_ref=GRANT_REF, policy_ref=POLICY_REF,
                 os_principal=f"uid:{UID}", actor_ref=ACTOR,
                 projects=[PROJECT], items=[ITEM], actions=["lease.acquire"],
                 valid_from=VALID_FROM, expires_at=grant_expires,
                 grant_epoch=1, owner_policy_epoch=1)
    p.seed_context(context_ref=CTX, actor_ref=ACTOR, grant_ref=GRANT_REF,
                   context_epoch=1)
    return p


def add_second_grant(p):
    p.seed_grant(grant_ref=GRANT_REF_2, policy_ref=POLICY_REF,
                 os_principal=f"uid:{UID}", actor_ref=ACTOR,
                 projects=[PROJECT], items=[ITEM], actions=["lease.acquire"],
                 valid_from=VALID_FROM, expires_at=EXPIRES_AT,
                 grant_epoch=1, owner_policy_epoch=1)


def code_of(fn):
    try:
        return "ACCEPTED", fn()
    except proto.PrototypeRefusal as exc:
        return exc.code, exc.detail
    except BaseException as exc:  # noqa: BLE001
        return type(exc).__name__, str(exc)


def check(p, token):
    return code_of(lambda: p.check_writer_fenced(RES, owner=ACTOR,
                                                 token=token))


def execute(p, statement, params=(), *, pragma=None):
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
        if pragma:
            conn.execute(pragma)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(statement, params)
        conn.commit()
        return "OK", None
    except BaseException as exc:  # noqa: BLE001
        conn.rollback()
        return type(exc).__name__, str(exc)
    finally:
        conn.close()


def query(p, statement, params=()):
    conn = sqlite3.connect(p.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(statement, params)]
    finally:
        conn.close()


def table_columns(p, table):
    return [row["name"] for row in query(p, f"PRAGMA table_info({table})")]


def replace_row(p, table, key_col, key_val, changes):
    cols = table_columns(p, table)
    rows = query(p, f"SELECT * FROM {table} WHERE {key_col}=?", (key_val,))
    if not rows:
        return "MISSING", None
    row = rows[0]
    row.update(changes)
    return execute(
        p, f"INSERT OR REPLACE INTO {table}({','.join(cols)}) "
           f"VALUES({','.join('?' * len(cols))})",
        [row[c] for c in cols])


def state(p, token=None):
    commit = p.commit("req:1")
    out = {
        "lease": p.lease(RES),
        "token_next": p.token_next(RES),
        "commit_count": p.commit_count(),
        "context": p.context(CTX),
        "commit": None if commit is None else {
            "request_id": commit["request_id"],
            "context_epoch": commit["context_epoch"],
            "grant_ref": commit["grant_ref"],
            "token": commit["token"],
            "grant_digest": commit["grant_digest"][:12],
        },
    }
    if token is not None:
        code, detail = check(p, token)
        out["fenced"] = code
        out["fenced_detail"] = detail
    return out


def record(name, expected, actual, *, ok, mode=None, note="", kind=None):
    row = {"name": name, "expected": expected, "actual": actual,
           "ok": bool(ok), "note": note}
    if mode:
        row["mode"] = mode
    if kind:
        row["kind"] = kind
    RESULTS.append(row)
    print(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))


# ---------------------------------------------------------------- A: API-only

def api_mode():
    # A1 point-in-time positive.
    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td)
        receipt = p.acquire(req())
        code, detail = check(p, receipt["token"])
        record("A1-point-in-time", "authorized:true, effect_eligible:false",
               {"fenced": code, "detail": detail}, mode="api",
               ok=(code == "ACCEPTED" and detail["authorized"] is True
                   and detail["effect_eligible"] is False
                   and "verified" not in detail))

    # A2 stale CAS and no side effect; the old token is untouched by a
    # refused CAS.
    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td)
        receipt = p.acquire(req())
        code, detail = code_of(lambda: p.rebind_context(
            CTX, expected_context_epoch=2, grant_ref=GRANT_REF_2))
        after_code, after_detail = check(p, receipt["token"])
        record("A2-cas-stale-no-side-effect",
               "BLOCKED_STALE_CONTEXT_EPOCH; context epoch 1; token valid",
               {"cas": code, "cas_detail": detail,
                "context": p.context(CTX),
                "fenced_after": after_code,
                "fenced_detail": after_detail}, mode="api",
               ok=(code == "BLOCKED_STALE_CONTEXT_EPOCH"
                   and p.context(CTX)["context_epoch"] == 1
                   and after_code == "ACCEPTED"))

    # A3 zero/negative expected epoch is an invalid request, not a write.
    for label, expected in (("zero", 0), ("negative", -1)):
        with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
            p = world(td)
            p.acquire(req())
            code, detail = code_of(lambda: p.rebind_context(
                CTX, expected_context_epoch=expected, grant_ref=GRANT_REF_2))
            record(f"A3-cas-{label}", "INVALID_REQUEST",
                   {"code": code, "detail": detail,
                    "context": p.context(CTX)}, mode="api",
                   ok=(code == "INVALID_REQUEST"
                       and p.context(CTX)["context_epoch"] == 1))

    # A4 legal rebind advances the generation and the old token is stale.
    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        result = p.rebind_context(CTX, expected_context_epoch=1,
                                  grant_ref=GRANT_REF_2)
        code, detail = check(p, receipt["token"])
        record("A4-rebind-old-token-stale",
               "epoch 2; BLOCKED_STALE_CONTEXT_EPOCH",
               {"rebind": result, "fenced": code, "detail": detail},
               mode="api",
               ok=(result["context_epoch"] == 2
                   and code == "BLOCKED_STALE_CONTEXT_EPOCH"))

    # A5 no-op rebind is idempotent and does not invalidate the token.
    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td)
        receipt = p.acquire(req())
        result = p.rebind_context(CTX, expected_context_epoch=1)
        code, detail = check(p, receipt["token"])
        record("A5-noop-rebind", "replayed:true; token still valid",
               {"rebind": result, "fenced": code}, mode="api",
               ok=(result["replayed"] is True and code == "ACCEPTED"))

    # A6 commit-phase TTL refusal rolls back lease, token and commit.
    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td)
        clock = p._clock
        code, detail = code_of(lambda: p.acquire(
            req(), hold=lambda: clock.set(NOW + 31)))
        record("A6-commit-ttl-rollback", "LEASE_EXPIRED_AT_COMMIT; no rows",
               {"code": code, "detail": detail,
                "state": state(p)}, mode="api",
               ok=(code == "LEASE_EXPIRED_AT_COMMIT"
                   and p.lease(RES) is None and p.commit_count() == 0
                   and p.token_next(RES) is None))

    # A7 commit-phase policy-expiry refusal rolls back.
    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td, policy_expires=iso(NOW + 10))
        clock = p._clock
        code, detail = code_of(lambda: p.acquire(
            req(), hold=lambda: clock.set(NOW + 11)))
        record("A7-commit-policy-expiry-rollback",
               "POLICY_EXPIRED_AT_COMMIT; no rows",
               {"code": code, "detail": detail,
                "state": state(p)}, mode="api",
               ok=(code == "POLICY_EXPIRED_AT_COMMIT"
                   and p.lease(RES) is None and p.commit_count() == 0
                   and p.token_next(RES) is None))

    # A8 a lease with no fenced commit row is refused by the API check.
    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td)
        store = proto.ServerOSStore(str(p.state_dir))
        legacy = store.acquire_lease(RES, owner="human:LEGACY", ttl_s=30,
                                     now=NOW)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner="human:LEGACY", token=legacy["token"]))
        record("A8-missing-fence-fail-closed", "BLOCKED_OWNER_POLICY",
               {"lease": legacy, "fenced": code, "detail": detail,
                "commit_count": p.commit_count()}, mode="api",
               ok=(code == "BLOCKED_OWNER_POLICY"
                   and p.commit_count() == 0))

    # A9 the request cannot inject authority/commit fields.
    for field, value in (("context_epoch", 2), ("policy_digest", "x"),
                         ("grant_digest", "x"), ("verified", True),
                         ("now", NOW), ("peer_uid", 0),
                         ("COMMIT_FENCED", True)):
        with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
            p = world(td)
            code, detail = code_of(lambda: p.acquire(req(**{field: value})))
            record(f"A9-reject-{field}", "INVALID_REQUEST",
                   {"code": code, "detail": detail,
                    "state": state(p)}, mode="api",
                   ok=(code == "INVALID_REQUEST"
                       and p.lease(RES) is None and p.commit_count() == 0))

    # A10 missing context and historical replay.
    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td)
        code, detail = code_of(lambda: p.acquire(req(context_ref="ctx:none")))
        record("A10-missing-context", "UNTRUSTED_ACTOR",
               {"code": code, "detail": detail}, mode="api",
               ok=code == "UNTRUSTED_ACTOR")

    with tempfile.TemporaryDirectory(prefix="qa2-379-api-") as td:
        p = world(td)
        request = req()
        p.acquire(request)
        replay = p.acquire(request)
        record("A11-historical-replay",
               "authorized:false, historical:true, effect_eligible:false",
               {"replay": replay}, mode="api",
               ok=(replay["authorized"] is False
                   and replay["historical"] is True
                   and replay["effect_eligible"] is False))


# ------------------------------------------------ B: same-UID raw SQLite write

def db_mode():
    # B1 REPLACE context after a legal API A->B->A rolls the epoch back.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT_REF_2)
        p.rebind_context(CTX, expected_context_epoch=2, grant_ref=GRANT_REF)
        stale, _ = check(p, receipt["token"])
        kind, detail = replace_row(
            p, "proto_context", "context_ref", CTX,
            {"grant_ref": GRANT_REF, "actor_ref": ACTOR, "context_epoch": 1})
        code, fdetail = check(p, receipt["token"])
        record("B1-replace-context-aba-revive",
               "EXPECTED_FAIL_UNTRUSTED_DB_WRITE: old token authorized",
               {"api_aba_stale": stale, "replace": kind, "detail": detail,
                "context": p.context(CTX), "fenced": code,
                "fenced_detail": fdetail}, mode="db", kind="EXPECTED_FAIL",
               ok=(stale != "ACCEPTED" and kind == "OK"
                   and code == "ACCEPTED"))

    # B2 plain INSERT appends a forged commit selected by rowid DESC.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT_REF_2)
        stale, _ = check(p, receipt["token"])
        commit = query(p, "SELECT * FROM proto_commit WHERE request_id='req:1'")[0]
        forged = dict(commit)
        forged.update({"request_id": "req:forged", "context_epoch": 2,
                       "grant_ref": GRANT_REF_2,
                       "grant_digest": proto._content_digest(p.grant(GRANT_REF_2)),
                       "committed_at": "2033-05-18T03:33:21Z"})
        cols = table_columns(p, "proto_commit")
        kind, detail = execute(
            p, f"INSERT INTO proto_commit({','.join(cols)}) "
               f"VALUES({','.join('?' * len(cols))})",
            [forged[c] for c in cols])
        code, fdetail = check(p, receipt["token"])
        record("B2-plain-insert-forged-commit",
               "EXPECTED_FAIL_UNTRUSTED_DB_WRITE: old token authorized",
               {"stale_before": stale, "insert": kind, "detail": detail,
                "commit_count": p.commit_count(), "fenced": code,
                "fenced_detail": fdetail}, mode="db", kind="EXPECTED_FAIL",
               ok=(stale != "ACCEPTED" and kind == "OK"
                   and code == "ACCEPTED"))

    # B3 DROP the epoch trigger, then same-epoch UPDATE A->B->A.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        drop, drop_detail = execute(
            p, "DROP TRIGGER proto_context_epoch_must_increase")
        to_b, to_b_detail = execute(
            p, "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
            (GRANT_REF_2, CTX))
        mid, _ = check(p, receipt["token"])
        back, back_detail = execute(
            p, "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
            (GRANT_REF, CTX))
        code, fdetail = check(p, receipt["token"])
        record("B3-drop-epoch-trigger-aba",
               "EXPECTED_FAIL_UNTRUSTED_DB_WRITE: trigger gone, old token authorized",
               {"drop": drop, "drop_detail": drop_detail,
                "to_b": to_b, "to_b_detail": to_b_detail, "middle": mid,
                "back": back, "back_detail": back_detail,
                "context": p.context(CTX), "fenced": code,
                "fenced_detail": fdetail}, mode="db", kind="EXPECTED_FAIL",
               ok=(drop == "OK" and to_b == "OK" and back == "OK"
                   and code == "ACCEPTED"))

    # B4 DROP the delete + epoch triggers, DELETE and reinsert epoch 1.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        receipt = p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, actor_ref=ACTOR_B)
        stale, _ = check(p, receipt["token"])
        dropped = []
        for trigger in ("proto_context_delete_forbidden",
                        "proto_context_epoch_must_increase"):
            kind, detail = execute(p, f"DROP TRIGGER {trigger}")
            dropped.append({"trigger": trigger, "kind": kind,
                            "detail": detail})
        delete, delete_detail = execute(
            p, "DELETE FROM proto_context WHERE context_ref=?", (CTX,))
        insert, insert_detail = execute(
            p, "INSERT INTO proto_context(context_ref,actor_ref,grant_ref,"
               "context_epoch) VALUES(?,?,?,?)",
            (CTX, ACTOR, GRANT_REF, 1))
        code, fdetail = check(p, receipt["token"])
        record("B4-drop-delete-trigger-reinsert",
               "EXPECTED_FAIL_UNTRUSTED_DB_WRITE: delete/reinsert revives",
               {"stale_before": stale, "dropped": dropped,
                "delete": delete, "delete_detail": delete_detail,
                "insert": insert, "insert_detail": insert_detail,
                "context": p.context(CTX), "fenced": code,
                "fenced_detail": fdetail}, mode="db", kind="EXPECTED_FAIL",
               ok=(stale != "ACCEPTED" and all(d["kind"] == "OK"
                                               for d in dropped)
                   and delete == "OK" and insert == "OK"
                   and code == "ACCEPTED"))

    # B5 content change + reforge the unsigned commit digest.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        receipt = p.acquire(req())
        change, change_detail = execute(
            p, "UPDATE proto_grant SET items_json=? WHERE grant_ref=?",
            (json.dumps(["OTHER-ITEM"]), GRANT_REF))
        stale, _ = check(p, receipt["token"])
        forged = proto._content_digest(p.grant(GRANT_REF))
        kind, detail = replace_row(p, "proto_commit", "request_id", "req:1",
                                   {"grant_digest": forged})
        code, fdetail = check(p, receipt["token"])
        record("B5-reforge-unsigned-digest",
               "EXPECTED_FAIL_UNTRUSTED_DB_WRITE: changed content authorized",
               {"change": change, "change_detail": change_detail,
                "stale_before": stale, "forged_digest": forged[:16],
                "replace": kind, "detail": detail, "fenced": code,
                "fenced_detail": fdetail}, mode="db", kind="EXPECTED_FAIL",
               ok=(stale != "ACCEPTED" and kind == "OK"
                   and code == "ACCEPTED"))

    # B6 the same plain INSERT with recursive_triggers=ON still wins.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT_REF_2)
        commit = query(p, "SELECT * FROM proto_commit WHERE request_id='req:1'")[0]
        forged = dict(commit)
        forged.update({"request_id": "req:forged-recursive",
                       "context_epoch": 2, "grant_ref": GRANT_REF_2,
                       "grant_digest": proto._content_digest(p.grant(GRANT_REF_2)),
                       "committed_at": "2033-05-18T03:33:22Z"})
        cols = table_columns(p, "proto_commit")
        kind, detail = execute(
            p, f"INSERT INTO proto_commit({','.join(cols)}) "
               f"VALUES({','.join('?' * len(cols))})",
            [forged[c] for c in cols],
            pragma="PRAGMA recursive_triggers=ON")
        code, fdetail = check(p, receipt["token"])
        record("B6-recursive-triggers-do-not-help",
               "EXPECTED_FAIL_UNTRUSTED_DB_WRITE: plain INSERT still accepted",
               {"insert": kind, "detail": detail,
                "commit_count": p.commit_count(), "fenced": code,
                "fenced_detail": fdetail}, mode="db", kind="EXPECTED_FAIL",
               ok=(kind == "OK" and code == "ACCEPTED"))

    # B7 an authorizer is per-connection: conn1 denies writes, conn2 does not.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        p.acquire(req())
        denied = None
        conn1 = sqlite3.connect(p.db_path, isolation_level=None)
        try:
            conn1.set_authorizer(
                lambda action, arg1, arg2, dbname, source: (
                    sqlite3.SQLITE_DENY
                    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                                  sqlite3.SQLITE_DELETE)
                    else sqlite3.SQLITE_OK))
            try:
                conn1.execute(
                    "UPDATE proto_grant SET items_json=? WHERE grant_ref=?",
                    (json.dumps(["DENIED-ITEM"]), GRANT_REF))
                denied = "OK"
            except BaseException as exc:  # noqa: BLE001
                denied = type(exc).__name__
        finally:
            conn1.close()
        allowed, allowed_detail = execute(
            p, "UPDATE proto_grant SET items_json=? WHERE grant_ref=?",
            (json.dumps(["OTHER-ITEM"]), GRANT_REF))
        record("B7-authorizer-per-connection",
               "EXPECTED_FAIL_UNTRUSTED_DB_WRITE: conn1 denied, conn2 writes",
               {"conn1_authorized_write": denied,
                "conn2_write": allowed, "detail": allowed_detail,
                "grant_items": p.grant(GRANT_REF)["items_json"]},
               mode="db", kind="EXPECTED_FAIL",
               ok=(denied == "DatabaseError" and allowed == "OK"))

    # B8 chmod 0600 is not a same-UID boundary.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        p.acquire(req())
        os.chmod(p.db_path, 0o600)
        mode = stat.S_IMODE(os.stat(p.db_path).st_mode)
        open_ok = False
        with open(p.db_path, "r+b") as handle:
            handle.read(16)
            open_ok = True
        record("B8-mode-0600-same-uid",
               "EXPECTED_FAIL: 0600 file is still writable by the same UID",
               {"mode": oct(mode), "uid": os.getuid(),
                "db_owner_uid": os.stat(p.db_path).st_uid,
                "same_uid_rw_open": open_ok}, mode="db", kind="EXPECTED_FAIL",
               ok=(mode == 0o600 and open_ok))

    # B9 chmod 0600 on a Unix socket does not stop the same UID.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        path = str(Path(td) / "fence.sock")
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            server.bind(path)
            os.chmod(path, 0o600)
            server.listen(1)
            client.connect(path)
            connected = True
            mode = stat.S_IMODE(os.stat(path).st_mode)
        except BaseException as exc:  # noqa: BLE001
            connected = False
            mode = None
            detail = f"{type(exc).__name__}: {exc}"
        finally:
            client.close()
            server.close()
        record("B9-socket-0600-same-uid",
               "EXPECTED_FAIL: a 0600 socket accepts the same UID",
               {"socket": True, "mode": None if mode is None else oct(mode),
                "same_uid_connected": connected,
                "detail": "" if connected else detail},
               mode="db", kind="EXPECTED_FAIL",
               ok=(connected and mode == 0o600))

    # B10 the digest is unkeyed: recompute it from the row.
    with tempfile.TemporaryDirectory(prefix="qa2-379-db-") as td:
        p = world(td)
        p.acquire(req())
        commit = p.commit("req:1")
        grant = p.grant(GRANT_REF)
        recomputed = proto._content_digest(grant)
        record("B10-unkeyed-digest",
               "EXPECTED_FAIL: SHA-256 recomputable without a key",
               {"commit_grant_digest": commit["grant_digest"][:16],
                "recomputed": recomputed[:16],
                "equal": commit["grant_digest"] == recomputed,
                "key_material_in_prototype": False},
               mode="db", kind="EXPECTED_FAIL",
               ok=(commit["grant_digest"] == recomputed))


# ------------------------------------------------ C: different OS UID (NOT_RUN)

def osuid_mode():
    record("C1-different-os-uid-write",
           "NOT_RUN: no sudo/setuid/chown; second real UID cannot be attested",
           {"uid": os.getuid(), "euid": os.geteuid(),
            "privilege_change": "forbidden",
            "reason": "no sudo/setuid/chown permitted; KERNEL_PEER_NOT_RUN"},
           mode="osuid", kind="NOT_RUN", ok=True,
           note="different-UID file-permission boundary is NOT_RUN, not PASS")
    record("C2-real-peer-credential",
           "NOT_RUN: no SO_PEERCRED / two real OS uids",
           {"synthetic_peer_uid": UID,
            "kernel_peer": "NOT_RUN"},
           mode="osuid", kind="NOT_RUN", ok=True,
           note="the prototype peer uid is a constructor fixture")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("api", "db", "osuid"), required=True)
    args = parser.parse_args()
    {"api": api_mode, "db": db_mode, "osuid": osuid_mode}[args.mode]()
    failed = [r for r in RESULTS if not r["ok"]]
    status = {}
    for row in RESULTS:
        key = row.get("kind") or "PASS"
        status[key] = status.get(key, 0) + 1
    print(json.dumps({"mode": args.mode, "total": len(RESULTS),
                      "failed": len(failed),
                      "failed_names": [r["name"] for r in failed],
                      "status": status},
                     ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
