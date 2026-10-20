#!/usr/bin/env python3
"""QA2 context -> grant rebinding adversarial witness for #377.

Pinned snapshot: #377 `8b42de479e5c0167f51bdb4cb8d7fba43be7479f`.
Read-only against the prototype; the SQLite DB is a temp WorkEngine-schema DB.
Owner policy/grant/context/clock/peer are synthetic fixtures; no production
file, daemon, WorkEngine, credential, OAuth or service is touched.

The witness acquires a fenced lease, then mutates the trusted context / grant /
policy rows and records exactly what `check_writer_fenced` does, plus the
request, receipt, lease, token, commit row and DDL columns.

Modes:
  controls  cases that the #377 fix already refuses (grant removed,
            policy_ref mismatch, grant_epoch bump); rc 0 iff all refuse.
  findings  context rebind / actor change / context deletion / same-grant_ref
            context mutation; rc 1 iff the old token is still authorized.

Severity: the context cases are P1 (the fenced check does not read the trusted
context and the commit row does not persist context_ref/actor_ref); the group
is PROTOTYPE_LOCAL, not production.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

SNAP = Path(os.environ.get(
    "A_PRIME_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-21/checkouts/a-prime-377-8b42de4")).resolve()
sys.path.insert(0, str(SNAP))
spec = importlib.util.spec_from_file_location(
    "qa2_atomic_fence_377_ctx",
    SNAP / "integrations" / "fleet-v1-atomic-prototype" / "atomic_fence.py")
proto = importlib.util.module_from_spec(spec)
sys.modules["qa2_atomic_fence_377_ctx"] = proto
spec.loader.exec_module(proto)

NOW = 2_000_000_000.0
UID = 4242
ACTOR = "human:A"
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
    p.seed_context(context_ref=CTX, actor_ref=ACTOR, grant_ref=GRANT_REF)
    return p


def code_of(fn):
    try:
        return "ACCEPTED", fn()
    except proto.PrototypeRefusal as exc:
        return exc.code, exc.detail
    except BaseException as exc:  # noqa: BLE001
        return type(exc).__name__, str(exc)


def sql(p, statement, params=()):
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def ddl(p):
    conn = sqlite3.connect(p.db_path)
    conn.row_factory = sqlite3.Row
    try:
        commit_cols = [row[1] for row in conn.execute(
            "PRAGMA table_info(proto_commit)")]
        context_cols = [row[1] for row in conn.execute(
            "PRAGMA table_info(proto_context)")]
        commit = conn.execute(
            "SELECT * FROM proto_commit WHERE request_id='req:1'").fetchone()
        context = conn.execute(
            "SELECT * FROM proto_context WHERE context_ref=?", (CTX,)).fetchone()
        commit_row = dict(commit) if commit is not None else None
        context_row = dict(context) if context is not None else None
    finally:
        conn.close()
    return {"commit_columns": commit_cols,
            "context_columns": context_cols,
            "commit_row": commit_row,
            "context_row": context_row}


def snapshot(p):
    lease = p.lease(RES)
    commit = p.commit("req:1")
    return {
        "lease": lease,
        "token_next": p.token_next(RES),
        "commit_count": p.commit_count(),
        "commit": None if commit is None else {
            "request_id": commit["request_id"],
            "grant_ref": commit["grant_ref"],
            "policy_ref": commit["policy_ref"],
            "grant_epoch": commit["grant_epoch"],
            "policy_epoch": commit["policy_epoch"],
            "authority_epoch": commit["authority_epoch"],
            "owner": commit["owner"],
            "token": commit["token"],
        },
        "context": p.context(CTX),
        "grant": p.grant(GRANT_REF),
        "policy": p.policy(POLICY_REF),
    }


def record(name, expected, actual, *, ok, severity=None, note=""):
    RESULTS.append({"name": name, "expected": expected, "actual": actual,
                    "ok": bool(ok), "severity": severity, "note": note})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True,
                     default=str))


def _insert_second_grant(p):
    sql(p, "INSERT INTO proto_grant("
           "grant_ref,policy_ref,grant_epoch,owner_policy_epoch,os_principal,"
           "actor_ref,projects_json,items_json,actions_json,valid_from,"
           "expires_at,revoked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (GRANT_REF_2, POLICY_REF, 1, 1, f"uid:{UID}", ACTOR,
         json.dumps([PROJECT]), json.dumps([ITEM]),
         json.dumps(["lease.acquire"]), VALID_FROM, EXPIRES_AT, 0))


def findings():
    # 1. Rebind the trusted context to another legal grant with the same
    #    policy/epoch. The fenced check selects the grant by the commit's
    #    grant_ref, not by the context, so the old token stays authorized.
    with tempfile.TemporaryDirectory(prefix="qa2-377-ctx-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = snapshot(p)
        _insert_second_grant(p)
        sql(p, "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
            (GRANT_REF_2, CTX))
        after = snapshot(p)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("context-grant-rebind", "BLOCKED_STALE_EPOCH/OWNER_POLICY",
               {"fenced": code, "detail": detail,
                "before": before, "after": after,
                "ddl": ddl(p)}, ok=(code != "ACCEPTED"), severity="P1",
               note="context.grant_ref moved to another legal grant with the "
                    "same grant_epoch; the fenced check still authorizes the "
                    "old token because it never reads proto_context")

    # 2. Mutate the context actor only (same grant_ref). The context has no
    #    epoch; the fenced check does not read it.
    with tempfile.TemporaryDirectory(prefix="qa2-377-ctx-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = snapshot(p)
        sql(p, "UPDATE proto_context SET actor_ref=? WHERE context_ref=?",
            ("human:B", CTX))
        after = snapshot(p)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("context-actor-change", "UNTRUSTED_ACTOR/BLOCKED",
               {"fenced": code, "detail": detail,
                "before": before, "after": after}, ok=(code != "ACCEPTED"),
               severity="P1",
               note="context actor changed to human:B; the lease owner/commit "
                    "owner remain human:A and the fenced check still "
                    "authorizes. The context has no epoch and is not read.")

    # 3. Delete the context row. The commit has no context_ref, so the fenced
    #    check cannot notice it.
    with tempfile.TemporaryDirectory(prefix="qa2-377-ctx-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = snapshot(p)
        sql(p, "DELETE FROM proto_context WHERE context_ref=?", (CTX,))
        after = snapshot(p)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("context-deleted", "UNTRUSTED_ACTOR/BLOCKED",
               {"fenced": code, "detail": detail,
                "before": before, "after": after,
                "ddl": ddl(p)}, ok=(code != "ACCEPTED"), severity="P1",
               note="the trusted context row is gone; the fenced check still "
                    "authorizes because proto_commit has no context_ref and "
                    "the check does not read proto_context")

    # 4. Context mutation with the same grant_ref is not epoch-bound: the
    #    context table has no epoch column and no link in the commit row.
    with tempfile.TemporaryDirectory(prefix="qa2-377-ctx-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = snapshot(p)
        sql(p, "UPDATE proto_context SET actor_ref=? WHERE context_ref=?",
            ("human:C", CTX))
        after = snapshot(p)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("same-grant-ref-context-mutation", "epoch/lineage refusal",
               {"fenced": code, "detail": detail,
                "before": before, "after": after,
                "context_columns": ddl(p)["context_columns"]},
               ok=(code != "ACCEPTED"), severity="P1",
               note="proto_context has no epoch; a same-grant_ref context "
                    "mutation is not bound by any generation and is invisible "
                    "to check_writer_fenced")

    # DDL/commit persistence witness, recorded once.
    with tempfile.TemporaryDirectory(prefix="qa2-377-ctx-") as td:
        p = world(td)
        request = req()
        receipt = p.acquire(request)
        info = ddl(p)
        record("commit-persists-context", "context_ref+actor_ref persisted",
               {"request": request, "receipt": receipt,
                "lease": p.lease(RES), "token": receipt["token"],
                "commit_row": info["commit_row"],
                "commit_columns": info["commit_columns"],
                "context_columns": info["context_columns"],
                "context_row": info["context_row"]},
               ok=("context_ref" in info["commit_columns"]
                   and ("context_actor" in info["commit_columns"]
                        or "actor_ref" in info["commit_columns"])),
               severity="P1",
               note="proto_commit persists grant/policy/epoch/owner/token but "
                    "no context_ref and no actor_ref; the request digest is a "
                    "hash and is not used by the fenced check. The minimal "
                    "fencing invariant must persist the context_ref/actor (or "
                    "a context digest) and re-read proto_context in the same "
                    "BEGIN IMMEDIATE.")


def controls():
    # Cases the #377 fix already refuses: grant removed, policy_ref mismatch,
    # grant_epoch bump. These are controls for the new witness.
    with tempfile.TemporaryDirectory(prefix="qa2-377-ctx-") as td:
        p = world(td)
        receipt = p.acquire(req())
        sql(p, "DELETE FROM proto_grant WHERE grant_ref=?", (GRANT_REF,))
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("grant-removed", "BLOCKED_OWNER_POLICY",
               {"fenced": code, "detail": detail}, ok=code == "BLOCKED_OWNER_POLICY")

    with tempfile.TemporaryDirectory(prefix="qa2-377-ctx-") as td:
        p = world(td)
        receipt = p.acquire(req())
        sql(p, "UPDATE proto_grant SET policy_ref=? WHERE grant_ref=?",
            ("policy:other", GRANT_REF))
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("policy-ref-mismatch", "BLOCKED_OWNER_POLICY",
               {"fenced": code, "detail": detail},
               ok=code == "BLOCKED_OWNER_POLICY")

    with tempfile.TemporaryDirectory(prefix="qa2-377-ctx-") as td:
        p = world(td)
        receipt = p.acquire(req())
        sql(p, "UPDATE proto_grant SET grant_epoch=2 WHERE grant_ref=?",
            (GRANT_REF,))
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("grant-epoch-bump", "BLOCKED_STALE_EPOCH",
               {"fenced": code, "detail": detail},
               ok=code == "BLOCKED_STALE_EPOCH")

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
