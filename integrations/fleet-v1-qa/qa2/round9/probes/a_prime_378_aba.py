#!/usr/bin/env python3
"""QA2 ABA / same-generation content-mutation witnesses for #378 @ 069f10cc.

Read-only against the exported exact head. Every DB is a temp WorkEngine-schema
SQLite DB; owner policy/grant/context/clock/peer are synthetic fixtures. No
production file, daemon, WorkEngine DB, credential, OAuth or service is used.

The question this probe answers:

  #378 persists `context_ref`/`context_actor` and re-reads `proto_context` in
  the same BEGIN IMMEDIATE. That refuses a *persistent* rebind. But the
  context has no generation and the commit stores no scope/window digest, so:

    C. ABA: context grant A->B->A (or actor A->B->A) with no context_epoch.
       Does the OLD token recover authorized:true after the state is restored?
    D. Same grant_ref, in-place grant.scope / grant.actions / policy.scope
       rewritten without a grant_epoch bump: is the old token still accepted?

Modes:
  controls  permanent actor/ref/epoch/window changes and the *middle* state of
            each ABA cycle must be refused; rc 0 iff all refuse.
  findings  ABA revival and same-generation content mutation; rc 1 iff the old
            token is still authorized (a confirmed gap).

Severity: ABA revival is P1 at the prototype fence layer (an old token becomes
authorized again after a lineage change that a generation fence should make
monotonic); same-generation content mutation is P2 and is disclosed in the
README as an owner-protocol requirement. Both are PROTOTYPE_LOCAL, not
production: the real owner reissue/epoch producer does not exist here.
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
    "2026-10-21/checkouts/a-prime-378-069f10c")).resolve()
sys.path.insert(0, str(SNAP))
spec = importlib.util.spec_from_file_location(
    "qa2_atomic_fence_378_aba",
    SNAP / "integrations" / "fleet-v1-atomic-prototype" / "atomic_fence.py")
proto = importlib.util.module_from_spec(spec)
sys.modules["qa2_atomic_fence_378_aba"] = proto
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


def add_second_grant(p):
    sql(p, "INSERT INTO proto_grant("
           "grant_ref,policy_ref,grant_epoch,owner_policy_epoch,os_principal,"
           "actor_ref,projects_json,items_json,actions_json,valid_from,"
           "expires_at,revoked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (GRANT_REF_2, POLICY_REF, 1, 1, f"uid:{UID}", ACTOR,
         json.dumps([PROJECT]), json.dumps([ITEM]),
         json.dumps(["lease.acquire"]), VALID_FROM, EXPIRES_AT, 0))


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


def sql(p, statement, params=()):
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def columns(p, table):
    conn = sqlite3.connect(p.db_path)
    try:
        return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    finally:
        conn.close()


def state(p):
    commit = p.commit("req:1")
    return {
        "lease": p.lease(RES),
        "token_next": p.token_next(RES),
        "commit_count": p.commit_count(),
        "commit": None if commit is None else {
            "request_id": commit["request_id"],
            "context_ref": commit.get("context_ref"),
            "context_actor": commit.get("context_actor"),
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
        "grant2": p.grant(GRANT_REF_2),
        "policy": p.policy(POLICY_REF),
    }


def record(name, expected, actual, *, ok, severity=None, note="", kind=None):
    row = {"name": name, "expected": expected, "actual": actual,
           "ok": bool(ok), "severity": severity, "note": note}
    if kind:
        row["kind"] = kind
    RESULTS.append(row)
    print(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))


def is_refusal(code):
    return code != "ACCEPTED"


# ---------------------------------------------------------------- controls

def controls():
    # Permanent (not reverted) mutations must be refused by #378.
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        sql(p, "UPDATE proto_grant SET actor_ref=? WHERE grant_ref=?",
            (ACTOR_B, GRANT_REF))
        code, detail = check(p, receipt["token"])
        record("C1-grant-actor-change", "BLOCKED_STALE_CONTEXT",
               {"fenced": code, "detail": detail, "state": state(p)},
               ok=code == "BLOCKED_STALE_CONTEXT", note="grant.actor_ref A->B, "
               "same grant_ref/grant_epoch; the current check compares "
               "context_actor/grant.actor_ref/policy.actor_ref")

    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        sql(p, "UPDATE proto_owner_policy SET actor_ref=? WHERE policy_ref=?",
            (ACTOR_B, POLICY_REF))
        code, detail = check(p, receipt["token"])
        record("C2-policy-actor-change", "BLOCKED_STALE_CONTEXT",
               {"fenced": code, "detail": detail, "state": state(p)},
               ok=code == "BLOCKED_STALE_CONTEXT", note="policy.actor_ref A->B, "
               "same policy_ref/epoch")

    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        sql(p, "UPDATE proto_grant SET owner_policy_epoch=2 WHERE grant_ref=?",
            (GRANT_REF,))
        code, detail = check(p, receipt["token"])
        record("C3-grant-owner-policy-epoch-bump", "BLOCKED_STALE_EPOCH",
               {"fenced": code, "detail": detail},
               ok=code == "BLOCKED_STALE_EPOCH",
               note="grant.owner_policy_epoch 1->2 is caught by "
                    "_check_commit_windows inside check_writer_fenced")

    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        sql(p, "UPDATE proto_grant SET expires_at=? WHERE grant_ref=?",
            ("2001-01-01T00:00:00Z", GRANT_REF))
        code, detail = check(p, receipt["token"])
        record("C4-grant-window-narrowed", "GRANT_EXPIRED_AT_COMMIT",
               {"fenced": code, "detail": detail},
               ok=code == "GRANT_EXPIRED_AT_COMMIT",
               note="the current grant window is re-read at check time")

    # Middle states of the ABA cycles must be refused; only the restored
    # final state is the question in findings mode.
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        add_second_grant(p)
        sql(p, "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
            (GRANT_REF_2, CTX))
        code, detail = check(p, receipt["token"])
        record("C5-ABA-grant-middle-state", "BLOCKED_STALE_CONTEXT",
               {"fenced": code, "detail": detail, "state": state(p)},
               ok=code == "BLOCKED_STALE_CONTEXT",
               note="during the A->B leg the old token is refused; findings "
                    "mode then restores B->A and re-checks")

    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        sql(p, "UPDATE proto_context SET actor_ref=? WHERE context_ref=?",
            (ACTOR_B, CTX))
        code, detail = check(p, receipt["token"])
        record("C6-ABA-actor-middle-state", "BLOCKED_STALE_CONTEXT",
               {"fenced": code, "detail": detail, "state": state(p)},
               ok=code == "BLOCKED_STALE_CONTEXT",
               note="context.actor_ref A->B is refused; findings restores A")


# ---------------------------------------------------------------- findings

def findings():
    # C1. ABA on the context grant: A -> B -> A, no context_epoch, no grant
    # epoch bump. The old token is refused in the middle leg and, after the
    # exact original state is restored, becomes authorized again.
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        add_second_grant(p)
        before = state(p)
        sql(p, "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
            (GRANT_REF_2, CTX))
        middle_code, middle_detail = check(p, receipt["token"])
        mid = state(p)
        sql(p, "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
            (GRANT_REF, CTX))
        after_code, after_detail = check(p, receipt["token"])
        after = state(p)
        record("F1-ABA-context-grant-revives-old-token",
               "old token stays permanently historical after a rebind cycle",
               {"before": before, "middle": {
                   "fenced": middle_code, "detail": middle_detail,
                   "state": mid},
                "after": {"fenced": after_code, "detail": after_detail,
                          "state": after},
                "context_columns": columns(p, "proto_context"),
                "commit_columns": columns(p, "proto_commit")},
               ok=(middle_code != "ACCEPTED" and after_code != "ACCEPTED"),
               severity="P1",
               note="context grant A->B->A: the middle leg is refused "
                    "BLOCKED_STALE_CONTEXT, then the restored state makes the "
                    "same token authorized:true again. proto_context has no "
                    "epoch/digest, so the fence cannot tell 'never rebound' "
                    "from 'rebound and reverted'.")

    # C2. ABA on the context actor: A -> B -> A. Same revival.
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        sql(p, "UPDATE proto_context SET actor_ref=? WHERE context_ref=?",
            (ACTOR_B, CTX))
        middle_code, middle_detail = check(p, receipt["token"])
        mid = state(p)
        sql(p, "UPDATE proto_context SET actor_ref=? WHERE context_ref=?",
            (ACTOR, CTX))
        after_code, after_detail = check(p, receipt["token"])
        after = state(p)
        record("F2-ABA-context-actor-revives-old-token",
               "old token stays permanently historical after an actor remap cycle",
               {"before": before, "middle": {
                   "fenced": middle_code, "detail": middle_detail,
                   "state": mid},
                "after": {"fenced": after_code, "detail": after_detail,
                          "state": after},
                "context_columns": columns(p, "proto_context")},
               ok=(middle_code != "ACCEPTED" and after_code != "ACCEPTED"),
               severity="P1",
               note="context actor A->B->A: the middle leg is refused "
                    "BLOCKED_STALE_CONTEXT, then the restored actor makes the "
                    "old token authorized:true again. The lease/commit owner "
                    "stays human:A; no epoch recorded the remap.")

    # C3. ABA on the grant actor (same grant_ref/grant_epoch).
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        sql(p, "UPDATE proto_grant SET actor_ref=? WHERE grant_ref=?",
            (ACTOR_B, GRANT_REF))
        middle_code, middle_detail = check(p, receipt["token"])
        mid = state(p)
        sql(p, "UPDATE proto_grant SET actor_ref=? WHERE grant_ref=?",
            (ACTOR, GRANT_REF))
        after_code, after_detail = check(p, receipt["token"])
        after = state(p)
        record("F3-ABA-grant-actor-revives-old-token",
               "old token stays permanently historical after a grant-actor cycle",
               {"before": before, "middle": {
                   "fenced": middle_code, "detail": middle_detail,
                   "state": mid},
                "after": {"fenced": after_code, "detail": after_detail,
                          "state": after}},
               ok=(middle_code != "ACCEPTED" and after_code != "ACCEPTED"),
               severity="P1",
               note="proto_grant.actor_ref A->B->A with grant_epoch untouched: "
                    "the check samples current content and has no digest/"
                    "generation, so reverting the content revives the token.")

    # D1. In-place grant scope narrowing: remove the committed item and the
    # granted action, keep grant_ref/grant_epoch. The fenced check never reads
    # scope, so the old token remains authorized.
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        sql(p, "UPDATE proto_grant SET items_json=?, actions_json=? "
               "WHERE grant_ref=?",
            (json.dumps(["OTHER-ITEM"]), json.dumps(["finalize"]), GRANT_REF))
        after = state(p)
        code, detail = check(p, receipt["token"])
        record("F4-grant-scope-narrowed-in-place",
               "scope rewrite without a grant_epoch bump invalidates the token",
               {"fenced": code, "detail": detail, "before": before,
                "after": after, "grant_scope_read_by_check": False},
               ok=(code != "ACCEPTED"), severity="P2",
               note="grant.items_json/actions_json rewritten in place; "
                    "check_writer_fenced reads grant revocation/epoch/window/"
                    "actor but not projects/items/actions, so it still returns "
                    "authorized:true. README discloses same-epoch content "
                    "mutation as an owner-protocol requirement, but the "
                    "prototype also stores no grant scope digest to detect it.")

    # D2. In-place policy scope narrowing (projects/items/actions).
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        sql(p, "UPDATE proto_owner_policy SET projects_json=?, items_json=?, "
               "actions_json=? WHERE policy_ref=?",
            (json.dumps(["other-project"]), json.dumps(["OTHER-ITEM"]),
             json.dumps(["finalize"]), POLICY_REF))
        after = state(p)
        code, detail = check(p, receipt["token"])
        record("F5-policy-scope-narrowed-in-place",
               "policy scope rewrite without an epoch bump invalidates the token",
               {"fenced": code, "detail": detail, "before": before,
                "after": after, "policy_scope_read_by_check": False},
               ok=(code != "ACCEPTED"), severity="P2",
               note="policy projects/items/actions rewritten in place at the "
                    "same policy_epoch/authority_epoch; the fenced check does "
                    "not compare policy scope and the commit stores no scope "
                    "digest.")

    # D3. In-place grant window extension (same ref/epoch).
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        sql(p, "UPDATE proto_grant SET expires_at=? WHERE grant_ref=?",
            ("3999-01-01T00:00:00Z", GRANT_REF))
        after = state(p)
        code, detail = check(p, receipt["token"])
        record("F6-grant-window-extended-in-place",
               "window rewrite without a grant_epoch bump is detected",
               {"fenced": code, "detail": detail, "before": before,
                "after": after},
               ok=(code != "ACCEPTED"), severity="P2",
               note="grant.expires_at extended in place; the check reads the "
                    "current window, so an extension is accepted. Narrowing "
                    "into the past is refused (control C4); content changes "
                    "that stay valid are not generation-bound.")

    # DDL witness: what the commit does not persist.
    with tempfile.TemporaryDirectory(prefix="qa2-378-aba-") as td:
        p = world(td)
        p.acquire(req())
        commit_cols = columns(p, "proto_commit")
        context_cols = columns(p, "proto_context")
        wanted = {"grant_scope_digest", "policy_scope_digest",
                  "context_epoch", "context_digest"}
        record("F7-no-generation-or-scope-digest",
               "commit persists a context generation and scope digests",
               {"commit_columns": commit_cols,
                "context_columns": context_cols,
                "missing": sorted(wanted - set(commit_cols) - set(context_cols))},
               ok=bool(wanted & (set(commit_cols) | set(context_cols))),
               severity="P2", kind="evidence",
               note="proto_commit has context_ref/context_actor but no "
                    "context_epoch/digest and no grant/policy scope or window "
                    "digest; proto_context has only context_ref/actor_ref/"
                    "grant_ref. This is the mechanism behind F1-F6.")


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
