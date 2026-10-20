#!/usr/bin/env python3
"""QA2 independent witnesses for #379 @ 7245fc6b (context generation + ABA).

Read-only against the exported exact head. Every DB is a temp WorkEngine-schema
SQLite DB; owner policy/grant/context/clock/peer are synthetic fixtures. No
production file, daemon, WorkEngine DB, credential, OAuth or service is used.

What is tested:

  controls  the claimed #379 properties:
            - raw context UPDATE without an epoch increase / DELETE and
              commit UPDATE / DELETE raise sqlite3.IntegrityError;
            - rebind_context CAS refuses a stale expected epoch;
            - API A->B->A advances context_epoch 1->2->3 and the old token is
              BLOCKED_STALE_CONTEXT_EPOCH;
            - grant_epoch bump / missing binding / historical replay;
            - canonical content digest: equality at commit and sensitivity to
              every content column;
            - same-epoch single content mutation -> BLOCKED_STALE_CONTENT
              (or a stronger refusal).

  findings  bypasses / revivals:
            - SQLite INSERT OR REPLACE does not fire the BEFORE DELETE
              trigger (recursive_triggers defaults to 0), so a context row can
              be replaced at epoch 1 after a 1->2->3 ABA and the old token
              becomes authorized:true again;
            - the same REPLACE rewrites a proto_commit row (context_epoch,
              digests, token), bypassing the claimed commit immutability;
            - grant/policy content A->B->A re-matches the committed digest and
              revives the old token (digest is point-in-time equality, not a
              generation);
            - the digest is an unsigned hash over a mutable row; a writer who
              can rewrite the commit row can make changed content authorized
              (NO_OWNER_TRUST);
            - raw SQL can jump the context epoch to a future value (trigger
              enforces only strictly-increasing, not +1/CAS), poisoning the
              synthetic owner API.

Severity is PROTOTYPE_LOCAL. P1 = an old token becomes authorized again or a
commit row is rewritten without dropping a trigger. P2 = hardening /
NO_OWNER_TRUST. Real owner key, SO_PEERCRED, two uids, production wiring,
Windows and Provider/OAuth are NOT_RUN.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

SNAP = Path(os.environ.get(
    "A_PRIME_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-21/checkouts/a-prime-379-7245fc6")).resolve()
sys.path.insert(0, str(SNAP))
spec = importlib.util.spec_from_file_location(
    "qa2_atomic_fence_379_gen",
    SNAP / "integrations" / "fleet-v1-atomic-prototype" / "atomic_fence.py")
proto = importlib.util.module_from_spec(spec)
sys.modules["qa2_atomic_fence_379_gen"] = proto
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
CTXMP = multiprocessing.get_context("fork") if hasattr(os, "fork") else None


def rebind_worker(state_dir, start, out):
    p = proto.AtomicFencePrototype(
        state_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID)
    start.wait()
    try:
        result = p.rebind_context(CTX, expected_context_epoch=1,
                                  grant_ref=GRANT_REF_2)
        out.put(("OK", result["context_epoch"]))
    except proto.PrototypeRefusal as exc:
        out.put((exc.code, None))
    except BaseException as exc:  # noqa: BLE001
        out.put((type(exc).__name__, str(exc)))


class FixedClock:
    def __init__(self, now=NOW):
        self.now = float(now)

    def __call__(self):
        return self.now


def req(request_id="req:1"):
    return {"request_id": request_id, "context_ref": CTX, "project": PROJECT,
            "item_ref": ITEM, "action": "lease.acquire", "ttl_s": 30.0}


def world(root):
    p = proto.AtomicFencePrototype(
        str(Path(root) / "proto"), clock=FixedClock(NOW),
        synthetic_peer_uid=UID)
    p.seed_policy(policy_ref=POLICY_REF, os_principal=f"uid:{UID}",
                  actor_ref=ACTOR, projects=[PROJECT], items=[ITEM],
                  actions=["lease.acquire"], valid_from=VALID_FROM,
                  expires_at=EXPIRES_AT, issued_at=ISSUED_AT)
    p.seed_grant(grant_ref=GRANT_REF, policy_ref=POLICY_REF,
                 os_principal=f"uid:{UID}", actor_ref=ACTOR,
                 projects=[PROJECT], items=[ITEM], actions=["lease.acquire"],
                 valid_from=VALID_FROM, expires_at=EXPIRES_AT,
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


def execute(p, statement, params=()):
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
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
    """INSERT OR REPLACE an existing row. Returns (kind, detail, row)."""
    cols = table_columns(p, table)
    rows = query(p, f"SELECT * FROM {table} WHERE {key_col}=?", (key_val,))
    if not rows:
        return "MISSING", None, None
    row = rows[0]
    row.update(changes)
    values = [row[c] for c in cols]
    kind, detail = execute(
        p, f"INSERT OR REPLACE INTO {table}({','.join(cols)}) "
           f"VALUES({','.join('?' * len(cols))})", values)
    return kind, detail, row


def replace_row_with_recursive(p, table, key_col, key_val, changes):
    cols = table_columns(p, table)
    rows = query(p, f"SELECT * FROM {table} WHERE {key_col}=?", (key_val,))
    if not rows:
        return "MISSING", None
    row = rows[0]
    row.update(changes)
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
        conn.execute("PRAGMA recursive_triggers=ON")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            f"INSERT OR REPLACE INTO {table}({','.join(cols)}) "
            f"VALUES({','.join('?' * len(cols))})",
            [row[c] for c in cols])
        conn.commit()
        return "OK", None
    except BaseException as exc:  # noqa: BLE001
        conn.rollback()
        return type(exc).__name__, str(exc)
    finally:
        conn.close()


def state(p):
    commit = p.commit("req:1")
    return {
        "lease": p.lease(RES),
        "token_next": p.token_next(RES),
        "commit_count": p.commit_count(),
        "commit": None if commit is None else {
            "context_ref": commit["context_ref"],
            "context_actor": commit["context_actor"],
            "context_epoch": commit["context_epoch"],
            "grant_ref": commit["grant_ref"],
            "grant_epoch": commit["grant_epoch"],
            "policy_epoch": commit["policy_epoch"],
            "authority_epoch": commit["authority_epoch"],
            "token": commit["token"],
            "policy_digest": commit["policy_digest"][:12],
            "grant_digest": commit["grant_digest"][:12],
        },
        "context": p.context(CTX),
        "grant": p.grant(GRANT_REF),
        "policy": p.policy(POLICY_REF),
    }


def record(name, expected, actual, *, ok, severity=None, note="", kind=None):
    row = {"name": name, "expected": expected, "actual": actual,
           "ok": bool(ok), "severity": severity, "note": note}
    if kind:
        row["kind"] = kind
    RESULTS.append(row)
    print(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))


def is_integrity(kind):
    return kind == "IntegrityError"


# ---------------------------------------------------------------- controls

CONTENT_MUTATIONS = [
    ("grant", GRANT_REF, "projects_json", json.dumps(["other-project"]),
     "BLOCKED_STALE_CONTENT"),
    ("grant", GRANT_REF, "items_json", json.dumps(["OTHER-ITEM"]),
     "BLOCKED_STALE_CONTENT"),
    ("grant", GRANT_REF, "actions_json", json.dumps(["finalize"]),
     "BLOCKED_STALE_CONTENT"),
    ("grant", GRANT_REF, "os_principal", "uid:9999", "BLOCKED_STALE_CONTENT"),
    ("grant", GRANT_REF, "valid_from", "2001-01-01T00:00:00Z",
     "BLOCKED_STALE_CONTENT"),
    ("grant", GRANT_REF, "expires_at", "2998-01-01T00:00:00Z",
     "BLOCKED_STALE_CONTENT"),
    ("grant", GRANT_REF, "actor_ref", ACTOR_B, "BLOCKED_STALE_CONTEXT"),
    ("grant", GRANT_REF, "revoked", 1, "POLICY_DENIED"),
    ("policy", POLICY_REF, "projects_json", json.dumps(["other-project"]),
     "BLOCKED_STALE_CONTENT"),
    ("policy", POLICY_REF, "items_json", json.dumps(["OTHER-ITEM"]),
     "BLOCKED_STALE_CONTENT"),
    ("policy", POLICY_REF, "actions_json", json.dumps(["finalize"]),
     "BLOCKED_STALE_CONTENT"),
    ("policy", POLICY_REF, "os_principal", "uid:9999", "BLOCKED_STALE_CONTENT"),
    ("policy", POLICY_REF, "issued_by", "owner:other", "BLOCKED_STALE_CONTENT"),
    ("policy", POLICY_REF, "issued_at", "2020-01-01T00:00:00Z",
     "BLOCKED_STALE_CONTENT"),
    ("policy", POLICY_REF, "valid_from", "2001-01-01T00:00:00Z",
     "BLOCKED_STALE_CONTENT"),
    ("policy", POLICY_REF, "expires_at", "2998-01-01T00:00:00Z",
     "BLOCKED_STALE_CONTENT"),
    ("policy", POLICY_REF, "actor_ref", ACTOR_B, "BLOCKED_STALE_CONTEXT"),
    ("policy", POLICY_REF, "revoked", 1, "POLICY_DENIED"),
]


def controls():
    table = {"grant": ("proto_grant", "grant_ref"),
             "policy": ("proto_owner_policy", "policy_ref")}

    # Raw context UPDATE without an epoch increase, DELETE, and commit
    # UPDATE/DELETE.
    for name, stmt, params in (
        ("C1-raw-context-grant-no-epoch",
         "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
         ("grant:proto-2", CTX)),
        ("C2-raw-context-actor-no-epoch",
         "UPDATE proto_context SET actor_ref=? WHERE context_ref=?",
         (ACTOR_B, CTX)),
        ("C3-raw-context-same-epoch",
         "UPDATE proto_context SET context_epoch=1 WHERE context_ref=?",
         (CTX,)),
        ("C4-raw-context-epoch-zero",
         "UPDATE proto_context SET context_epoch=0 WHERE context_ref=?",
         (CTX,)),
        ("C6-raw-context-delete",
         "DELETE FROM proto_context WHERE context_ref=?", (CTX,)),
        ("C7-raw-commit-update",
         "UPDATE proto_commit SET token=999 WHERE request_id='req:1'", ()),
        ("C8-raw-commit-delete",
         "DELETE FROM proto_commit WHERE request_id='req:1'", ()),
    ):
        with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
            p = world(td)
            receipt = p.acquire(req())
            kind, detail = execute(p, stmt, params)
            record(name, "sqlite3.IntegrityError", {"kind": kind,
                   "detail": detail}, ok=is_integrity(kind),
                   note="trigger/CHECK must refuse this statement")

    # Epoch decrease after a legal increment.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT_REF_2)
        kind, detail = execute(
            p, "UPDATE proto_context SET context_epoch=1 WHERE context_ref=?",
            (CTX,))
        record("C5-raw-context-epoch-decrease", "sqlite3.IntegrityError",
               {"kind": kind, "detail": detail,
                "context": p.context(CTX)}, ok=is_integrity(kind),
               note="2->1 must not be accepted (no rollback of the generation)")

    # CAS stale expected epoch.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT_REF_2)
        code, detail = code_of(lambda: p.rebind_context(
            CTX, expected_context_epoch=1, grant_ref=GRANT_REF))
        record("C9-rebind-cas-stale-expected",
               "BLOCKED_STALE_CONTEXT_EPOCH",
               {"code": code, "detail": detail, "context": p.context(CTX)},
               ok=code == "BLOCKED_STALE_CONTEXT_EPOCH")

    # No-op rebind is idempotent and does not invalidate the token.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        receipt = p.acquire(req())
        result = p.rebind_context(CTX, expected_context_epoch=1)
        code, detail = check(p, receipt["token"])
        record("C10-rebind-noop-idempotent",
               "replayed:true, epoch 1, token still authorized",
               {"rebind": result, "fenced": code, "detail": detail},
               ok=(result["replayed"] is True
                   and result["context_epoch"] == 1
                   and code == "ACCEPTED"))

    # API ABA on the grant and on the actor: 1->2->3, old token stale.
    for name, kwargs in (
        ("C11-api-aba-grant", {"grant_ref": GRANT_REF_2}),
        ("C12-api-aba-actor", {"actor_ref": ACTOR_B}),
    ):
        with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
            p = world(td)
            add_second_grant(p)
            receipt = p.acquire(req())
            first = kwargs.get("grant_ref", GRANT_REF)
            second = GRANT_REF if first == GRANT_REF_2 else GRANT_REF
            p.rebind_context(CTX, expected_context_epoch=1, **kwargs)
            back = ("actor_ref" if "actor_ref" in kwargs else "grant_ref")
            p.rebind_context(CTX, expected_context_epoch=2,
                             **{back: (ACTOR if back == "actor_ref"
                                       else GRANT_REF)})
            code, detail = check(p, receipt["token"])
            record(name, "BLOCKED_STALE_CONTEXT_EPOCH",
                   {"fenced": code, "detail": detail,
                    "context": p.context(CTX), "state": state(p)},
                   ok=code == "BLOCKED_STALE_CONTEXT_EPOCH",
                   note="API A->B->A advances the epoch; the old token must "
                        "not recover")

    # Raw SQL WITH an epoch increment is allowed by the trigger, but the old
    # token must still be stale.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        kind, detail = execute(
            p, "UPDATE proto_context SET grant_ref=?, context_epoch=2 "
               "WHERE context_ref=?", (GRANT_REF_2, CTX))
        code, fdetail = check(p, receipt["token"])
        record("C13-raw-context-epoch-increment",
               "OK update, BLOCKED_STALE_CONTEXT_EPOCH",
               {"kind": kind, "detail": detail, "fenced": code,
                "fdetail": fdetail, "context": p.context(CTX)},
               ok=(kind == "OK" and code == "BLOCKED_STALE_CONTEXT_EPOCH"),
               note="the trigger enforces increase, not the CAS API; the "
                    "old token must still be stale")

    # Grant epoch bump, unchanged positive, historical replay.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        receipt = p.acquire(req())
        execute(p, "UPDATE proto_grant SET grant_epoch=2 WHERE grant_ref=?",
                (GRANT_REF,))
        code, detail = check(p, receipt["token"])
        record("C14-grant-epoch-bump", "BLOCKED_STALE_EPOCH",
               {"fenced": code, "detail": detail},
               ok=code == "BLOCKED_STALE_EPOCH")

    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        receipt = p.acquire(req())
        code, detail = check(p, receipt["token"])
        record("C15-unchanged-point-in-time",
               "authorized:true, effect_eligible:false",
               {"fenced": code, "receipt": detail},
               ok=(code == "ACCEPTED" and detail.get("authorized") is True
                   and detail.get("effect_eligible") is False
                   and "verified" not in detail))

    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        request = req()
        receipt = p.acquire(request)
        replay = p.acquire(request)
        record("C16-historical-replay",
               "authorized:false, historical:true, effect_eligible:false",
               {"replay": replay}, ok=(replay.get("authorized") is False
                                       and replay.get("historical") is True
                                       and replay.get("effect_eligible") is False))

    # Canonical digest: equals a recomputation; changes for every content
    # column; returns to the committed value when the row is restored.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        p.acquire(req())
        commit = p.commit("req:1")
        grant = p.grant(GRANT_REF)
        policy = p.policy(POLICY_REF)
        base_grant = proto._content_digest(grant)
        base_policy = proto._content_digest(policy)
        sensitivities = {}
        for table, ref, col, val, _ in CONTENT_MUTATIONS:
            if table == "grant":
                row = dict(grant)
            else:
                row = dict(policy)
            row[col] = val
            sensitivities[f"{table}.{col}"] = (
                proto._content_digest(row) !=
                (base_grant if table == "grant" else base_policy))
        record("C17-canonical-digest",
               "commit digest == recompute; every content column changes it",
               {"commit_policy_digest_ok": commit["policy_digest"] == base_policy,
                "commit_grant_digest_ok": commit["grant_digest"] == base_grant,
                "sensitivity": sensitivities},
               ok=(commit["policy_digest"] == base_policy
                   and commit["grant_digest"] == base_grant
                   and all(sensitivities.values())))

    # Same-epoch single content mutations must be refused.
    for table, ref, col, val, expected in CONTENT_MUTATIONS:
        with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
            p = world(td)
            receipt = p.acquire(req())
            tname = ("proto_grant" if table == "grant"
                     else "proto_owner_policy")
            key = "grant_ref" if table == "grant" else "policy_ref"
            before = {"lease": p.lease(RES), "token_next": p.token_next(RES),
                      "commit_count": p.commit_count()}
            kind, detail = execute(
                p, f"UPDATE {tname} SET {col}=? WHERE {key}=?", (val, ref))
            code, fdetail = check(p, receipt["token"])
            after = {"lease": p.lease(RES), "token_next": p.token_next(RES),
                     "commit_count": p.commit_count()}
            record(f"C18-content-{table}-{col}", expected,
                   {"sql": kind, "detail": detail, "fenced": code,
                    "fdetail": fdetail, "side_effect": before != after,
                    "before": before, "after": after},
                   ok=(kind == "OK" and code == expected and before == after),
                   note="permanent same-generation content mutation; the "
                        "refusal must not change the lease/commit/token")

    # Diagnostic: recursive_triggers=ON makes REPLACE hit the DELETE trigger.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        p.acquire(req())
        kind, detail = replace_row_with_recursive(
            p, "proto_context", "context_ref", CTX,
            {"grant_ref": GRANT_REF, "actor_ref": ACTOR, "context_epoch": 1})
        record("C19-recursive-triggers-on-blocks-replace",
               "sqlite3.IntegrityError", {"kind": kind, "detail": detail},
               ok=is_integrity(kind),
               note="diagnostic only: the prototype does not set "
                    "PRAGMA recursive_triggers=ON, so this is not its default")

    # Persistent single rebind through the CAS API: the epoch advances and
    # the old token must be stale (not the old BLOCKED_STALE_CONTEXT).
    for name, kwargs in (
        ("C20-api-single-rebind-grant", {"grant_ref": GRANT_REF_2}),
        ("C21-api-single-rebind-actor", {"actor_ref": ACTOR_B}),
    ):
        with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
            p = world(td)
            add_second_grant(p)
            receipt = p.acquire(req())
            result = p.rebind_context(CTX, expected_context_epoch=1, **kwargs)
            code, detail = check(p, receipt["token"])
            record(name, "BLOCKED_STALE_CONTEXT_EPOCH",
                   {"rebind": result, "fenced": code, "detail": detail},
                   ok=(result["context_epoch"] == 2
                       and code == "BLOCKED_STALE_CONTEXT_EPOCH"))

    # A lease row without a fenced commit row: the fenced check must refuse.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        store = proto.ServerOSStore(str(p.state_dir))
        legacy = store.acquire_lease(RES, owner="human:LEGACY", ttl_s=30,
                                     now=NOW)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner="human:LEGACY", token=legacy["token"]))
        record("C22-missing-fence-row", "BLOCKED_OWNER_POLICY",
               {"legacy_lease": legacy, "fenced": code, "detail": detail,
                "commit_count": p.commit_count()},
               ok=code == "BLOCKED_OWNER_POLICY",
               note="legacy acquire writes a lease with no proto_commit; the "
                    "fenced check refuses, but the legacy write itself remains "
                    "a P0 production-wiring bypass")

    # Invalid raw SQL is refused by SQLite, not silently accepted.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        p.acquire(req())
        kind, detail = execute(p, "THIS IS NOT SQL")
        record("C23-invalid-raw-sql", "OperationalError",
               {"kind": kind, "detail": detail},
               ok=kind == "OperationalError")

    # A forged commit that names a missing context must still be refused.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        receipt = p.acquire(req())
        commit = query(p, "SELECT * FROM proto_commit WHERE request_id='req:1'")[0]
        forged = dict(commit)
        forged.update({"request_id": "req:missing",
                       "context_ref": "ctx:missing",
                       "committed_at": "2033-05-18T03:33:21Z"})
        cols = table_columns(p, "proto_commit")
        kind, detail = execute(
            p, f"INSERT INTO proto_commit({','.join(cols)}) "
               f"VALUES({','.join('?' * len(cols))})",
            [forged[c] for c in cols])
        code, fdetail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("C26-missing-context-refused", "BLOCKED_STALE_CONTEXT",
               {"insert": {"kind": kind, "detail": detail},
                "fenced": code, "detail": fdetail,
                "commit_count": p.commit_count()},
               ok=code == "BLOCKED_STALE_CONTEXT",
               note="the forged row is selectable by rowid DESC (F8), but a "
                    "commit whose context_ref does not exist is refused")

    # Two-process CAS contention: exactly one writer advances 1->2, the other
    # sees the committed epoch and refuses; no lost update.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        add_second_grant(p)
        if CTXMP is None:
            record("C24-rebind-cas-contention", "SKIP",
                   {"skip": "no fork"}, ok=True, kind="skip")
        else:
            start = CTXMP.Barrier(3)
            out = CTXMP.Queue()
            procs = [CTXMP.Process(target=rebind_worker,
                                   args=(str(p.state_dir), start, out))
                     for _ in range(2)]
            for proc in procs:
                proc.start()
            start.wait()
            results = [out.get(timeout=30) for _ in procs]
            for proc in procs:
                proc.join(timeout=30)
            codes = sorted(code for code, _ in results)
            record("C24-rebind-cas-contention",
                   "one OK, one BLOCKED_STALE_CONTEXT_EPOCH, final epoch 2",
                   {"results": results, "context": p.context(CTX),
                    "exitcodes": [proc.exitcode for proc in procs]},
                   ok=(codes == ["BLOCKED_STALE_CONTEXT_EPOCH", "OK"]
                       and p.context(CTX)["context_epoch"] == 2))

    # WAL / integrity survive the generation schema.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        p.acquire(req())
        mode = query(p, "PRAGMA journal_mode")[0]["journal_mode"]
        integrity = query(p, "PRAGMA integrity_check")[0]["integrity_check"]
        record("C25-wal-integrity", "wal + ok",
               {"journal_mode": mode, "integrity_check": integrity},
               ok=(str(mode).lower() == "wal" and integrity == "ok"))


# ---------------------------------------------------------------- findings

def findings():
    # F1. API ABA to epoch 3, then a raw INSERT OR REPLACE back to epoch 1.
    # recursive_triggers defaults to 0, so the BEFORE DELETE trigger does not
    # fire and the old token is authorized again.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT_REF_2)
        p.rebind_context(CTX, expected_context_epoch=2, grant_ref=GRANT_REF)
        aba_code, aba_detail = check(p, receipt["token"])
        before = state(p)
        kind, detail, row = replace_row(
            p, "proto_context", "context_ref", CTX,
            {"grant_ref": GRANT_REF, "actor_ref": ACTOR, "context_epoch": 1})
        after = state(p)
        code, fdetail = check(p, receipt["token"])
        record("F1-replace-context-revives-old-token",
               "old token stays BLOCKED_STALE_CONTEXT_EPOCH",
               {"api_aba": {"fenced": aba_code, "detail": aba_detail},
                "replace": {"kind": kind, "detail": detail, "row": row},
                "before": before, "after": after,
                "fenced_after": code, "fenced_detail": fdetail,
                "recursive_triggers": query(
                    p, "PRAGMA recursive_triggers")},
               ok=(aba_code != "ACCEPTED" and code != "ACCEPTED"),
               severity="P1",
               note="INSERT OR REPLACE is not an UPDATE: SQLite does not fire "
                    "the BEFORE DELETE trigger when recursive_triggers=0, so "
                    "the context generation is rolled back to 1 and the old "
                    "token becomes authorized:true again.")

    # F2. Same bypass for the actor ABA.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        receipt = p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, actor_ref=ACTOR_B)
        p.rebind_context(CTX, expected_context_epoch=2, actor_ref=ACTOR)
        aba_code, aba_detail = check(p, receipt["token"])
        kind, detail, row = replace_row(
            p, "proto_context", "context_ref", CTX,
            {"grant_ref": GRANT_REF, "actor_ref": ACTOR, "context_epoch": 1})
        code, fdetail = check(p, receipt["token"])
        record("F2-replace-context-actor-revives-old-token",
               "old token stays BLOCKED_STALE_CONTEXT_EPOCH",
               {"api_aba": {"fenced": aba_code, "detail": aba_detail},
                "replace": {"kind": kind, "detail": detail, "row": row},
                "fenced_after": code, "fenced_detail": fdetail},
               ok=(aba_code != "ACCEPTED" and code != "ACCEPTED"),
               severity="P1",
               note="the actor rollback revives the token through the same "
                    "REPLACE path.")

    # F3. REPLACE a proto_commit row after a legal epoch advance: the commit
    # immutability trigger is bypassed and the old token is accepted.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT_REF_2)
        stale_code, stale_detail = check(p, receipt["token"])
        kind, detail, row = replace_row(
            p, "proto_commit", "request_id", "req:1",
            {"context_epoch": 2, "grant_ref": GRANT_REF_2,
             "grant_digest": proto._content_digest(p.grant(GRANT_REF_2))})
        code, fdetail = check(p, receipt["token"])
        record("F3-replace-commit-bypasses-immutability",
               "UPDATE/DELETE/REPLACE all leave the commit immutable",
               {"stale_before": {"fenced": stale_code, "detail": stale_detail},
                "replace": {"kind": kind, "detail": detail,
                            "row": {k: row[k] for k in
                                    ("context_ref", "context_epoch",
                                     "token", "grant_ref")}},
                "fenced_after": code, "fenced_detail": fdetail},
               ok=(stale_code != "ACCEPTED" and code != "ACCEPTED"),
               severity="P1",
               note="the UPDATE/DELETE triggers do not see INSERT OR REPLACE; "
                    "a writer can align commit.context_epoch with the current "
                    "context and re-authorize the old token.")

    # F4/F5. Content A->B->A: the digest matches again and the old token
    # revives. The digest is point-in-time equality, not a generation.
    for name, table, ref, col, changed in (
        ("F4-content-aba-grant-items", "proto_grant", GRANT_REF, "items_json",
         json.dumps(["OTHER-ITEM"])),
        ("F5-content-aba-policy-actions", "proto_owner_policy", POLICY_REF,
         "actions_json", json.dumps(["finalize"])),
        ("F9-content-aba-grant-actor", "proto_grant", GRANT_REF, "actor_ref",
         ACTOR_B),
        ("F10-content-aba-policy-actor", "proto_owner_policy", POLICY_REF,
         "actor_ref", ACTOR_B),
    ):
        with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
            p = world(td)
            receipt = p.acquire(req())
            key = "grant_ref" if table == "proto_grant" else "policy_ref"
            original = query(p, f"SELECT {col} FROM {table} WHERE {key}=?",
                             (ref,))[0][col]
            execute(p, f"UPDATE {table} SET {col}=? WHERE {key}=?",
                    (changed, ref))
            mid_code, mid_detail = check(p, receipt["token"])
            execute(p, f"UPDATE {table} SET {col}=? WHERE {key}=?",
                    (original, ref))
            code, fdetail = check(p, receipt["token"])
            record(name, "old token stays refused after A->B->A",
                   {"middle": {"fenced": mid_code, "detail": mid_detail},
                    "restored": {"fenced": code, "detail": fdetail},
                    "original": original},
                   ok=(mid_code != "ACCEPTED" and code != "ACCEPTED"),
                   severity="P1",
                   note="A->B is refused, then A->B->A re-matches the stored "
                        "digest and the old token is authorized again. A "
                        "content generation (or an immutable policy/grant "
                        "table) is still required.")

    # F6. Unsigned digest: change content, recompute the digest, REPLACE the
    # commit row, and the changed content becomes authorized.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        receipt = p.acquire(req())
        execute(p, "UPDATE proto_grant SET items_json=? WHERE grant_ref=?",
                (json.dumps(["OTHER-ITEM"]), GRANT_REF))
        stale_code, stale_detail = check(p, receipt["token"])
        changed = p.grant(GRANT_REF)
        forged = proto._content_digest(changed)
        kind, detail, _ = replace_row(
            p, "proto_commit", "request_id", "req:1",
            {"grant_digest": forged})
        code, fdetail = check(p, receipt["token"])
        record("F6-unsigned-digest-reforged-commit",
               "changed content stays refused without an owner signature",
               {"stale_before": {"fenced": stale_code, "detail": stale_detail},
                "forged_digest": forged[:16],
                "replace": {"kind": kind, "detail": detail},
                "fenced_after": code, "fenced_detail": fdetail},
               ok=(stale_code != "ACCEPTED" and code != "ACCEPTED"),
               severity="P2",
               note="the digest is an unkeyed hash of a mutable row; a writer "
                    "who can rewrite proto_commit can also recompute it. This "
                    "is NO_OWNER_TRUST / PROTOTYPE_ONLY, not an owner-signed "
                    "content proof.")

    # F8. A plain INSERT of a second proto_commit row for the same
    # resource/token is allowed (there is no BEFORE INSERT trigger). The
    # fenced check selects ORDER BY rowid DESC, so the forged row wins and the
    # old token is authorized again.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        add_second_grant(p)
        receipt = p.acquire(req())
        p.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT_REF_2)
        stale_code, stale_detail = check(p, receipt["token"])
        commit = query(p, "SELECT * FROM proto_commit WHERE request_id='req:1'")[0]
        forged = dict(commit)
        forged.update({
            "request_id": "req:forged",
            "context_epoch": 2,
            "grant_ref": GRANT_REF_2,
            "grant_digest": proto._content_digest(p.grant(GRANT_REF_2)),
            "committed_at": "2033-05-18T03:33:21Z",
        })
        cols = table_columns(p, "proto_commit")
        kind, detail = execute(
            p, f"INSERT INTO proto_commit({','.join(cols)}) "
               f"VALUES({','.join('?' * len(cols))})",
            [forged[c] for c in cols])
        code, fdetail = check(p, receipt["token"])
        latest = query(
            p, "SELECT request_id,context_epoch,grant_ref FROM proto_commit "
               "WHERE resource=? AND token=? ORDER BY rowid DESC LIMIT 1",
            (RES, receipt["token"]))
        record("F8-plain-insert-forged-commit",
               "a second commit row for the same lease cannot re-authorize it",
               {"stale_before": {"fenced": stale_code, "detail": stale_detail},
                "insert": {"kind": kind, "detail": detail},
                "commit_count": p.commit_count(), "latest": latest,
                "fenced_after": code, "fenced_detail": fdetail},
               ok=(stale_code != "ACCEPTED" and code != "ACCEPTED"),
               severity="P1",
               note="proto_commit has UPDATE/DELETE triggers but no BEFORE "
                    "INSERT guard and no uniqueness on (resource,token); a "
                    "writer can append a forged commit with the current "
                    "context epoch/digests and check_writer_fenced selects it "
                    "by rowid DESC.")

    # F7. Raw SQL can jump the context epoch to a future value. The old token
    # is refused (safe), but the CAS API is poisoned; the trigger enforces
    # only strictly-increasing, not +1 or an owner-authenticated producer.
    with tempfile.TemporaryDirectory(prefix="qa2-379-") as td:
        p = world(td)
        receipt = p.acquire(req())
        kind, detail = execute(
            p, "UPDATE proto_context SET context_epoch=999 WHERE context_ref=?",
            (CTX,))
        code, fdetail = check(p, receipt["token"])
        cas, cdetail = code_of(lambda: p.rebind_context(
            CTX, expected_context_epoch=2, grant_ref=GRANT_REF))
        record("F7-future-epoch-poisons-cas",
               "raw SQL cannot jump the generation past the CAS API",
               {"raw_update": {"kind": kind, "detail": detail},
                "context": p.context(CTX),
                "old_token": {"fenced": code, "detail": fdetail},
                "cas_expected_2": {"code": cas, "detail": cdetail}},
               ok=(kind != "OK" or cas == "ACCEPTED"),
               severity="P2",
               note="the trigger accepts any strictly larger epoch (999) and "
                    "there is no upper bound or owner-authenticated +1; the "
                    "old token is safely refused but a later legitimate CAS "
                    "with the true next epoch is blocked.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=("controls", "findings"), required=True)
    args = parser.parse_args()
    if args.mode == "controls":
        controls()
    else:
        findings()
    failed = [r for r in RESULTS if not r["ok"]]
    print(json.dumps({"mode": args.mode, "total": len(RESULTS),
                      "failed": len(failed),
                      "failed_names": [r["name"] for r in failed],
                      "severities": sorted({r["severity"] for r in RESULTS
                                            if r["severity"]})},
                     ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
