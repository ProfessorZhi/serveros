#!/usr/bin/env python3
"""QA2 independent lineage/no-side-effect witnesses for #377.

Read-only against an exported exact head. The prototype's owner policy/grant/
context/clock/peer are synthetic fixtures; the SQLite DB is a temp DB with the
real WorkEngine schema. No production file, daemon, WorkEngine, database,
credential or service is touched.

`--mode controls` exercises the fenced-check expectations that #377 claims to
fix (grant-epoch bump, policy_ref lineage) plus valid/revoke/expired and
no-side-effect controls. On #376 the two fix controls are RED (ACCEPTED); on
#377 they are GREEN (BLOCKED_*).

`--mode findings` records the residual production-wiring and context-lineage
gaps. P0 findings are production barriers, not prototype claims.
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
    "qa2_atomic_fence_377",
    SNAP / "integrations" / "fleet-v1-atomic-prototype" / "atomic_fence.py")
proto = importlib.util.module_from_spec(spec)
sys.modules["qa2_atomic_fence_377"] = proto
spec.loader.exec_module(proto)

NOW = 2_000_000_000.0
UID = 4242
ACTOR = "human:A"
POLICY_REF = "policy:proto"
GRANT_REF = "grant:proto"
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

    def set(self, now):
        self.now = float(now)


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


def world(root, *, clock=None, policy_over=None, grant_over=None):
    p = proto.AtomicFencePrototype(
        str(Path(root) / "proto"), clock=clock or FixedClock(NOW),
        synthetic_peer_uid=UID)
    values = dict(policy_ref=POLICY_REF, os_principal=f"uid:{UID}",
                  actor_ref=ACTOR, projects=[PROJECT], items=[ITEM],
                  actions=["lease.acquire"], valid_from=VALID_FROM,
                  expires_at=EXPIRES_AT, issued_at=ISSUED_AT)
    values.update(policy_over or {})
    p.seed_policy(**values)
    values = dict(grant_ref=GRANT_REF, policy_ref=POLICY_REF,
                  os_principal=f"uid:{UID}", actor_ref=ACTOR,
                  projects=[PROJECT], items=[ITEM], actions=["lease.acquire"],
                  valid_from=VALID_FROM, expires_at=EXPIRES_AT,
                  grant_epoch=1, owner_policy_epoch=1)
    values.update(grant_over or {})
    p.seed_grant(**values)
    p.seed_context(context_ref=CTX, actor_ref=ACTOR, grant_ref=GRANT_REF)
    return p


def code_of(fn):
    try:
        return "ACCEPTED", fn()
    except proto.PrototypeRefusal as exc:
        return exc.code, exc.detail
    except BaseException as exc:  # noqa: BLE001
        return type(exc).__name__, str(exc)


def state(p):
    lease = p.lease(RES)
    commit = p.commit("req:1")
    return {"lease": lease,
            "commit": None if commit is None else {
                "token": commit["token"],
                "grant_epoch": commit["grant_epoch"],
                "policy_epoch": commit["policy_epoch"],
                "authority_epoch": commit["authority_epoch"]},
            "commits": p.commit_count(),
            "token_next": p.token_next(RES)}


def record(name, expected, actual, *, ok, severity=None, note=""):
    RESULTS.append({"name": name, "expected": expected, "actual": actual,
                    "ok": bool(ok), "severity": severity, "note": note})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True,
                     default=str))


def _sql(p, statement, params=()):
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(statement, params)
        conn.commit()
    finally:
        conn.close()


def controls():
    # A valid token is accepted point-in-time, never effect-eligible, and the
    # read-only fenced check does not change lease/commit/token.
    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        after = state(p)
        ok = (code == "ACCEPTED" and isinstance(detail, dict)
              and detail.get("prototype_status") == "PROTOTYPE_FENCED_CHECK"
              and detail.get("authorized") is True
              and detail.get("historical") is False
              and detail.get("effect_eligible") is False
              and detail.get("grant_epoch") == 1
              and before == after)
        record("valid-token-point-in-time", {"fenced": "FENCED_CHECK",
                                             "authorized": True,
                                             "effect_eligible": False,
                                             "grant_epoch": 1,
                                             "no_side_effect": True},
               {"code": code, "detail": detail, "before": before,
                "after": after}, ok=ok)

    # The P1 fix: grant_epoch bump is refused, with no lease/commit side effect.
    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        _sql(p, "UPDATE proto_grant SET grant_epoch=2 WHERE grant_ref=?",
             (GRANT_REF,))
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        after = state(p)
        ok = (code == "BLOCKED_STALE_EPOCH" and before == after)
        record("grant-epoch-bump-refused", {"reason": "BLOCKED_STALE_EPOCH",
                                            "no_side_effect": True},
               {"reason": code, "detail": detail, "before": before,
                "after": after}, ok=ok)

    # policy_ref lineage mismatch is refused; the grant is read by
    # commit.grant_ref but its policy_ref must equal commit.policy_ref.
    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        _sql(p, "UPDATE proto_grant SET policy_ref=? WHERE grant_ref=?",
             ("policy:other", GRANT_REF))
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        after = state(p)
        ok = (code == "BLOCKED_OWNER_POLICY" and before == after)
        record("policy-ref-lineage-mismatch-refused",
               {"reason": "BLOCKED_OWNER_POLICY", "no_side_effect": True},
               {"reason": code, "detail": detail, "before": before,
                "after": after}, ok=ok)

    # Policy revoke and expired grant still refuse, with no side effects.
    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        p = world(td)
        receipt = p.acquire(req())
        before = state(p)
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        after = state(p)
        record("policy-revoke-refused", {"reason": "POLICY_DENIED",
                                         "no_side_effect": True},
               {"reason": code, "detail": detail, "before": before,
                "after": after},
               ok=(code == "POLICY_DENIED" and before == after))

    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        clock = FixedClock(NOW)
        p = world(td, clock=clock,
                  policy_over={"expires_at": iso(NOW + 1000)},
                  grant_over={"expires_at": iso(NOW + 10)})
        receipt = p.acquire(req(ttl_s=600.0))
        before = state(p)
        clock.set(NOW + 11)
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        after = state(p)
        record("expired-grant-refused", {"reason": "GRANT_EXPIRED_AT_COMMIT",
                                         "no_side_effect": True},
               {"reason": code, "detail": detail, "before": before,
                "after": after},
               ok=(code == "GRANT_EXPIRED_AT_COMMIT" and before == after))


def findings():
    # Context/grant rebind: the fenced check reads the commit's grant_ref and
    # that grant row, not the current context. The commit.grant_ref ==
    # grant.grant_ref comparison is tautological (the grant is selected by
    # commit.grant_ref), so a context rebound to a different grant with the
    # same grant_epoch is not detected. P2: owner protocol must bump
    # grant_epoch on reissue; production effect path must read the context.
    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        p = world(td)
        receipt = p.acquire(req())
        _sql(p, "INSERT INTO proto_grant("
                "grant_ref,policy_ref,grant_epoch,owner_policy_epoch,"
                "os_principal,actor_ref,projects_json,items_json,actions_json,"
                "valid_from,expires_at,revoked) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
             ("grant:proto-2", POLICY_REF, 1, 1, f"uid:{UID}", ACTOR,
              json.dumps([PROJECT]), json.dumps([ITEM]),
              json.dumps(["lease.acquire"]), VALID_FROM, EXPIRES_AT, 0))
        _sql(p, "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
             ("grant:proto-2", CTX))
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("context-grant-rebind-not-detected", {
            "fenced": "BLOCKED_STALE_EPOCH/BLOCKED_OWNER_POLICY",
            "authorized": False},
               {"fenced": code, "detail": detail,
                "context_grant_ref": p.context(CTX)["grant_ref"],
                "commit_grant_ref": p.commit("req:1")["grant_ref"]},
               ok=(code != "ACCEPTED"), severity="P2",
               note="check_writer_fenced selects the grant by commit.grant_ref; "
                    "commit.grant_ref == grant.grant_ref is tautological. It "
                    "does not read the current context, so a context rebound to "
                    "a different grant with the same grant_epoch is not "
                    "detected. A correct owner reissue must bump grant_epoch; "
                    "the production effect path should read the context.")

    # Legacy production-wiring barriers remain (P0, correctly disclosed).
    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        p = world(td)
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        store = proto.ServerOSStore(str(p.state_dir))
        lease = store.acquire_lease(RES, owner="human:LEGACY", ttl_s=30,
                                    now=NOW)
        record("legacy-acquire-bypasses-fence", {"post_revoke_lease": None},
               {"legacy_lease": lease, "post_revoke_lease": p.lease(RES),
                "proto_commits": p.commit_count(),
                "policy_revoked": p.policy(POLICY_REF)["revoked"]},
               ok=(p.lease(RES) is None), severity="P0",
               note="legacy ServerOSStore.acquire_lease writes a new lease "
                    "after revoke outside the fence; README marks "
                    "PRODUCTION_WIRING_REQUIRED; not claimed fixed")

    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        p = world(td)
        receipt = p.acquire(req())
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        store = proto.ServerOSStore(str(p.state_dir))
        legacy = store.check_writer(RES, owner=ACTOR,
                                    token=receipt["token"], now=NOW)
        fenced, _ = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("legacy-check-writer-accepts-old-token",
               {"legacy_authorized": False, "fenced": "POLICY_DENIED"},
               {"legacy_authorized": legacy["authorized"],
                "fenced": fenced}, ok=(not legacy["authorized"]),
               severity="P0",
               note="legacy ServerOSStore.check_writer still accepts the "
                    "pre-revoke token; fenced check refuses. README marks "
                    "PRODUCTION_WIRING_REQUIRED.")

    # Fenced check still has no peer/context authentication (P2).
    with tempfile.TemporaryDirectory(prefix="qa2-377-") as td:
        p = world(td)
        receipt = p.acquire(req())
        checked = p.check_writer_fenced(RES, owner=ACTOR,
                                        token=receipt["token"])
        record("fenced-check-not-peer-context-bound",
               {"peer_bound": True, "context_read": True},
               {"peer_bound": False, "context_read": False,
                "authorized": checked["authorized"]},
               ok=False, severity="P2",
               note="check_writer_fenced takes caller owner/token strings; "
                    "README flags NO_TRUSTED_PEER/SO_PEERCRED_NOT_BOUND.")


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
