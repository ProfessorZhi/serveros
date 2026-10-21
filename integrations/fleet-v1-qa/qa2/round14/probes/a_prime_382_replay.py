#!/usr/bin/env python3
"""QA2 independent witness for #381 @ 043e08b9 (exact commit/token binding).

Read-only against the exported exact head. Every DB is a temp WorkEngine-schema
SQLite DB; owner/grant/context/clock/peer are synthetic fixtures. No production
file, daemon, WorkEngine DB, credential, OAuth or service is used.

Modes:
  api       A / API_ONLY_CORRECTNESS: happy path; cross-token GREEN; post-hold
            TTL/policy/grant rollback; grant epoch/context ABA; missing
            grant/policy; effect idempotency/conflict; no production marker.
  binding   #380 RED -> #381 GREEN comparison for cross-token; cross-resource
            capability; historical replay capability (open P1 if still usable);
            same-process owner/key/db_path evidence (now disclosed, P2).
  raw       B / EXPECTED_RAW_DB_BYPASS: raw effect marker, raw context/commit
            forge + readable key, chmod 0600, per-connection authorizer, legacy
            Store. rc 0 iff every predicted bypass reproduces.
  race      two-process revoke-first, effect-first hold, crash-before-COMMIT.
  osuid     C / OS_ISOLATION_NOT_RUN.

Evidence: LOCAL_REAL_SQLITE + SYNTHETIC_OWNER/CONTEXT/CLOCK/PEER + REAL_TEMP.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import multiprocessing
import os
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path

SNAP = Path(os.environ.get(
    "A_PRIME_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-21/checkouts/a-prime-381-043e08b")).resolve()
sys.path.insert(0, str(SNAP))
_spec = importlib.util.spec_from_file_location(
    "qa2_swe_381",
    SNAP / "integrations" / "fleet-v1-atomic-prototype" / "single_write_entry.py")
swe = importlib.util.module_from_spec(_spec)
sys.modules["qa2_swe_381"] = swe
_spec.loader.exec_module(swe)
atomic = swe.atomic
PrototypeRefusal = swe.PrototypeRefusal

NOW = 2_000_000_000.0
UID = 4242
ACTOR = "human:A"
POLICY_REF = "policy:proto"
GRANT_REF = "grant:proto"
GRANT2_REF = "grant:proto-2"
CTX = "ctx:proto"
PROJECT = "serveros"
ITEM = "SRV-0001"
ITEM2 = "SRV-0002"
ACTION = "lease.acquire"
RES = "work-write:SRV-0001"
RES2 = "work-write:SRV-0002"
VALID_FROM = "2000-01-01T00:00:00Z"
EXPIRES_AT = "2999-01-01T00:00:00Z"
ISSUED_AT = "2026-01-01T00:00:00Z"
CAP_KEY = b"qa2-round13-capability-key"
RESULTS = []
CTXMP = multiprocessing.get_context("fork") if hasattr(os, "fork") else None


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


def req(request_id="req:1", *, item=ITEM, context_ref=CTX, project=PROJECT):
    return {"request_id": request_id, "context_ref": context_ref,
            "project": project, "item_ref": item, "action": ACTION,
            "ttl_s": 30.0}


def execute(w, statement, params=(), *, timeout=30):
    conn = sqlite3.connect(w._writer.db_path, timeout=timeout,
                           isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(statement, params)
        conn.commit()
        return "OK", None
    except BaseException as exc:  # noqa: BLE001
        conn.rollback()
        return type(exc).__name__, str(exc)
    finally:
        conn.close()


def query(w, statement, params=()):
    conn = sqlite3.connect(w._writer.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(statement, params)]
    finally:
        conn.close()


def replace_row(w, table, key_col, key_val, changes):
    cols = [row["name"] for row in query(w, f"PRAGMA table_info({table})")]
    rows = query(w, f"SELECT * FROM {table} WHERE {key_col}=?", (key_val,))
    if not rows:
        return "MISSING", None
    row = rows[0]
    row.update(changes)
    return execute(
        w, f"INSERT OR REPLACE INTO {table}({','.join(cols)}) "
           f"VALUES({','.join('?' * len(cols))})",
        [row[c] for c in cols])


def writer(tmp_path, *, clock=None, initialize=True, key=CAP_KEY,
           policy_expires=EXPIRES_AT, grant_expires=EXPIRES_AT):
    w = swe.SingleWriteEntry(
        str(Path(tmp_path) / "db"), initialize=initialize,
        clock=clock or FixedClock(NOW), synthetic_peer_uid=UID,
        actor_ref=ACTOR, capability_key=key)
    if initialize:
        w.issue(policy_ref=POLICY_REF, grant_ref=GRANT_REF,
                context_ref=CTX, projects=[PROJECT], items=[ITEM, ITEM2],
                actions=[ACTION], valid_from=VALID_FROM,
                expires_at=policy_expires, issued_at=ISSUED_AT)
        if grant_expires != policy_expires:
            execute(w, "UPDATE proto_grant SET expires_at=? WHERE grant_ref=?",
                    (grant_expires, GRANT_REF))
    return w


def issue_grant2(w):
    w.issue_grant(grant_ref=GRANT2_REF, policy_ref=POLICY_REF,
                  projects=[PROJECT], items=[ITEM, ITEM2], actions=[ACTION],
                  valid_from=VALID_FROM, expires_at=EXPIRES_AT)


def code_of(fn):
    try:
        return "ACCEPTED", fn()
    except PrototypeRefusal as exc:
        return exc.code, exc.detail
    except BaseException as exc:  # noqa: BLE001
        return type(exc).__name__, str(exc)


def effect_count(w):
    with w._writer._transaction(immediate=False) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM proto_effect").fetchone()[0])


def ledger(w):
    return {"lease": w._writer.lease(RES),
            "token_next": w._writer.token_next(RES),
            "commit_count": w._writer.commit_count(),
            "effect_count": effect_count(w)}


def record(name, expected, actual, *, ok, mode=None, kind=None, note=""):
    row = {"name": name, "expected": expected, "actual": actual,
           "ok": bool(ok), "note": note}
    if mode:
        row["mode"] = mode
    if kind:
        row["kind"] = kind
    RESULTS.append(row)
    print(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))


# ---------------------------------------------------------------- A: API

def api_mode():
    # A1 happy path.
    with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        checked = w.check_use(receipt["request_id"], receipt["token"],
                              receipt["capability"])
        marker = w.commit_effect(receipt["request_id"], receipt["token"],
                                 receipt["capability"], effect_ref="effect:1",
                                 payload_digest="digest:1")
        record("A1-happy-path", "authorized; marker; no production marker",
               {"receipt": receipt, "check": checked, "marker": marker,
                "row": w.effect("effect:1")}, mode="api",
               ok=(checked["authorized"] is True
                   and marker["prototype_status"]
                   == "PROTOTYPE_EFFECT_MARKER_COMMITTED"
                   and marker["effect_marker"] is True
                   and marker["production_effect"] is False
                   and effect_count(w) == 1))

    # A2 cross-token is now refused; this is the #380 RED -> #381 GREEN case.
    with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
        clock = FixedClock(NOW)
        w = writer(td, clock=clock)
        first = w.acquire(req("req:1"))
        issue_grant2(w)
        w.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT2_REF)
        clock.set(NOW + 31)
        second = w.acquire(req("req:2"))
        check_code, check_detail = code_of(lambda: w.check_use(
            "req:1", second["token"], first["capability"]))
        effect_code, effect_detail = code_of(lambda: w.commit_effect(
            "req:1", second["token"], first["capability"],
            effect_ref="effect:cross", payload_digest="d"))
        record("A2-cross-token-fixed", "STALE_TOKEN; effect_count 0",
               {"first": {k: first[k] for k in ("token", "context_epoch")},
                "second": {k: second[k] for k in ("token", "context_epoch")},
                "check": check_code, "check_detail": check_detail,
                "effect": effect_code, "effect_detail": effect_detail,
                "effect_count": effect_count(w)}, mode="api",
               ok=(check_code == "STALE_TOKEN"
                   and effect_code == "STALE_TOKEN"
                   and effect_count(w) == 0))

    # A3 effect idempotency / conflict.
    with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        first = w.commit_effect(receipt["request_id"], receipt["token"],
                                receipt["capability"], effect_ref="effect:1",
                                payload_digest="d1")
        again = w.commit_effect(receipt["request_id"], receipt["token"],
                                receipt["capability"], effect_ref="effect:1",
                                payload_digest="d1")
        conflict, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:1", payload_digest="d2"))
        second, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:2", payload_digest="d1"))
        record("A3-effect-idempotency",
               "replayed:true/authorized:false; conflicts; count 1",
               {"again": {k: again[k] for k in ("replayed", "authorized")},
                "conflict": conflict, "second": second,
                "effect_count": effect_count(w)}, mode="api",
               ok=(again["replayed"] is True and again["authorized"] is False
                   and conflict == "IDEMPOTENCY_CONFLICT"
                   and second == "EFFECT_ALREADY_COMMITTED"
                   and effect_count(w) == 1))

    # A4 post-hold TTL / policy / grant crossings roll the marker back.
    for name, clock_advance, policy_exp, grant_exp, expected in (
        ("A4-hold-ttl", 31, EXPIRES_AT, EXPIRES_AT, "LEASE_EXPIRED"),
        ("A5-hold-policy", 11, iso(NOW + 10), EXPIRES_AT,
         "POLICY_EXPIRED_AT_COMMIT"),
        ("A6-hold-grant", 11, EXPIRES_AT, iso(NOW + 10),
         "GRANT_EXPIRED_AT_COMMIT"),
    ):
        with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
            clock = FixedClock(NOW)
            w = writer(td, clock=clock, policy_expires=policy_exp,
                       grant_expires=grant_exp)
            receipt = w.acquire(req())
            before = ledger(w)

            def hold():
                clock.set(NOW + clock_advance)

            code, detail = code_of(lambda: w.commit_effect(
                receipt["request_id"], receipt["token"],
                receipt["capability"], effect_ref="effect:hold",
                payload_digest="d", hold=hold))
            after = ledger(w)
            record(name,
                   f"{expected}; marker rolled back; ledger unchanged",
                   {"code": code, "detail": detail,
                    "effect": w.effect("effect:hold"),
                    "before": before, "after": after}, mode="api",
                   ok=(code == expected and w.effect("effect:hold") is None
                       and before == after))

    # A7 grant-epoch bump / context ABA.
    with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        before = ledger(w)
        execute(w, "UPDATE proto_grant SET grant_epoch=2 WHERE grant_ref=?",
                (GRANT_REF,))
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"], receipt["capability"]))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:epoch", payload_digest="d"))
        after = ledger(w)
        record("A7-grant-epoch", "BLOCKED_STALE_EPOCH; ledger unchanged",
               {"check": check_code, "effect": effect_code,
                "before": before, "after": after}, mode="api",
               ok=(check_code == "BLOCKED_STALE_EPOCH"
                   and effect_code == "BLOCKED_STALE_EPOCH"
                   and before == after))

    with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        before = ledger(w)
        issue_grant2(w)
        w.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT2_REF)
        w.rebind_context(CTX, expected_context_epoch=2, grant_ref=GRANT_REF)
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"], receipt["capability"]))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:aba", payload_digest="d"))
        after = ledger(w)
        record("A8-context-aba",
               "BLOCKED_STALE_CONTEXT_EPOCH; ledger unchanged",
               {"check": check_code, "effect": effect_code,
                "before": before, "after": after}, mode="api",
               ok=(check_code == "BLOCKED_STALE_CONTEXT_EPOCH"
                   and effect_code == "BLOCKED_STALE_CONTEXT_EPOCH"
                   and before == after))

    # A9 revoke-first / missing grant / missing policy.
    with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        before = ledger(w)
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"], receipt["capability"]))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:revoked", payload_digest="d"))
        after = ledger(w)
        record("A9-revoke-first", "POLICY_DENIED; ledger unchanged",
               {"check": check_code, "effect": effect_code,
                "before": before, "after": after}, mode="api",
               ok=(check_code == "POLICY_DENIED"
                   and effect_code == "POLICY_DENIED"
                   and before == after))

    for label, table, column, ref in (
        ("A10-missing-grant", "proto_grant", "grant_ref", GRANT_REF),
        ("A11-missing-policy", "proto_owner_policy", "policy_ref", POLICY_REF),
    ):
        with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
            w = writer(td)
            receipt = w.acquire(req())
            before = ledger(w)
            execute(w, f"DELETE FROM {table} WHERE {column}=?", (ref,))
            check_code, _ = code_of(lambda: w.check_use(
                receipt["request_id"], receipt["token"],
                receipt["capability"]))
            effect_code, _ = code_of(lambda: w.commit_effect(
                receipt["request_id"], receipt["token"],
                receipt["capability"], effect_ref="effect:missing",
                payload_digest="d"))
            after = ledger(w)
            record(label, "BLOCKED_OWNER_POLICY; ledger unchanged",
                   {"check": check_code, "effect": effect_code,
                    "before": before, "after": after}, mode="api",
                   ok=(check_code == "BLOCKED_OWNER_POLICY"
                       and effect_code == "BLOCKED_OWNER_POLICY"
                       and before == after))

    # A12 no production marker / classification.
    with tempfile.TemporaryDirectory(prefix="qa2-381-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        checked = w.check_use(receipt["request_id"], receipt["token"],
                              receipt["capability"])
        marker = w.commit_effect(receipt["request_id"], receipt["token"],
                                 receipt["capability"], effect_ref="effect:1",
                                 payload_digest="d")
        values = json.dumps([receipt, checked, marker], default=str)
        report = swe.classification()
        record("A12-no-production-marker",
               "no verified/COMMIT_FENCED; API_ONLY_CORRECTNESS; effect NOT_PROVEN",
               {"contains_verified": '"verified"' in values,
                "contains_COMMIT_FENCED": "COMMIT_FENCED" in values,
                "classification": report}, mode="api",
               ok=('"verified"' not in values
                   and "COMMIT_FENCED" not in values
                   and report["A"]["name"] == swe.API_ONLY_CORRECTNESS
                   and report["B"]["name"] == swe.RAW_DB_WRITE_BYPASS_EXPECTED
                   and report["C"]["name"] == swe.OS_ISOLATION_NOT_RUN
                   and report["effect_commit"] == swe.EFFECT_COMMIT_NOT_PROVEN
                   and report["production"] == "NOT_V1_SIGNED"))


# ------------------------------------------------ binding / replay / caveat

def binding_mode():
    # B1 #380 RED -> #381 GREEN: cap1 + successor token2 is refused.
    with tempfile.TemporaryDirectory(prefix="qa2-381-bind-") as td:
        clock = FixedClock(NOW)
        w = writer(td, clock=clock)
        first = w.acquire(req("req:1"))
        issue_grant2(w)
        w.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT2_REF)
        clock.set(NOW + 31)
        second = w.acquire(req("req:2"))
        check_code, check_detail = code_of(lambda: w.check_use(
            "req:1", second["token"], first["capability"]))
        effect_code, effect_detail = code_of(lambda: w.commit_effect(
            "req:1", second["token"], first["capability"],
            effect_ref="effect:cross", payload_digest="d"))
        record("B1-cross-token-380RED-381GREEN",
               "#380 ACCEPTED -> #381 STALE_TOKEN; effect_count 0",
               {"first": {k: first[k] for k in ("token", "context_epoch")},
                "second": {k: second[k] for k in ("token", "context_epoch")},
                "check": check_code, "check_detail": check_detail,
                "effect": effect_code, "effect_detail": effect_detail,
                "effect_count": effect_count(w)}, mode="binding",
               ok=(check_code == "STALE_TOKEN"
                   and effect_code == "STALE_TOKEN"
                   and effect_count(w) == 0),
               note="round12 C1 at #380: check ACCEPTED and marker "
                    "request_id req:1/token 2/context_epoch 2; #381 refuses "
                    "before the fence and writes no marker")

    # B2 cross-resource capability mixing.
    with tempfile.TemporaryDirectory(prefix="qa2-381-bind-") as td:
        w = writer(td)
        a = w.acquire(req("req:a", item=ITEM))
        b = w.acquire(req("req:b", item=ITEM2))
        cross_cap_code, _ = code_of(lambda: w.check_use(
            "req:b", b["token"], a["capability"]))
        cross_cap_effect, _ = code_of(lambda: w.commit_effect(
            "req:b", b["token"], a["capability"], effect_ref="effect:crossres",
            payload_digest="d"))
        same_value_code, same_value_detail = code_of(lambda: w.check_use(
            "req:a", b["token"], a["capability"]))
        record("B2-cross-resource",
               "capability for resource A cannot authorize resource B; "
               "same integer token from B authorizes only A",
               {"a": {"request_id": a["request_id"], "token": a["token"],
                      "resource": a["resource"]},
                "b": {"request_id": b["request_id"], "token": b["token"],
                      "resource": b["resource"]},
                "check_b_with_cap_a": cross_cap_code,
                "effect_b_with_cap_a": cross_cap_effect,
                "check_a_with_token_b_value": same_value_code,
                "detail": same_value_detail,
                "effect_count": effect_count(w)}, mode="binding",
               ok=(cross_cap_code == "BLOCKED_CAPABILITY"
                   and cross_cap_effect == "BLOCKED_CAPABILITY"),
               note="token values are per-resource integers; a same-valued "
                    "token from resource B passes the integer equality check "
                    "but the fence uses capability A's commit, so it "
                    "authorizes A only (P2 token-scope ambiguity, not B "
                    "authority).")

    # B3 historical replay capability (open P1 if still usable).
    with tempfile.TemporaryDirectory(prefix="qa2-381-bind-") as td:
        w = writer(td)
        first = w.acquire(req("req:1"))
        replay = w.acquire(req("req:1"))
        maybe_cap = replay.get("capability")
        no_cap_check, no_cap_detail = code_of(lambda: w.check_use(
            "req:1", replay["token"], maybe_cap))
        no_cap_effect, no_cap_effect_detail = code_of(
            lambda: w.commit_effect("req:1", replay["token"], maybe_cap,
                                    effect_ref="effect:replay",
                                    payload_digest="d"))
        record("B3-replay-no-capability",
               "historical replay omits capability; no cap is BLOCKED; no marker",
               {"first": {k: first.get(k) for k in
                          ("authorized", "historical", "capability_scope")},
                "replay": {k: replay.get(k) for k in
                           ("authorized", "historical", "replayed", "token",
                            "capability_scope")},
                "replay_has_capability": "capability" in replay,
                "first_has_capability": "capability" in first,
                "no_cap_check": no_cap_check,
                "no_cap_check_detail": no_cap_detail,
                "no_cap_effect": no_cap_effect,
                "no_cap_effect_detail": no_cap_effect_detail,
                "effect_count": effect_count(w)}, mode="binding",
               ok=("capability" not in replay
                   and replay.get("capability_scope")
                   == "HISTORICAL_AUDIT_ONLY"
                   and replay.get("authorized") is False
                   and replay.get("historical") is True
                   and "capability" in first
                   and no_cap_check == "BLOCKED_CAPABILITY"
                   and no_cap_effect == "BLOCKED_CAPABILITY"
                   and effect_count(w) == 0),
               note="#381 RED: replay carried a usable capability "
                    "(capability_scope API_ONLY); #382 GREEN: replay is "
                    "HISTORICAL_AUDIT_ONLY, no capability, no-cap calls "
                    "BLOCKED_CAPABILITY and no marker.")

    # B3b: the ORIGINAL capability is NOT revoked by a replay; while the
    # lease/epoch fence is live it keeps its original semantics.
    with tempfile.TemporaryDirectory(prefix="qa2-382-bind-") as td:
        w = writer(td)
        first = w.acquire(req("req:1"))
        replay = w.acquire(req("req:1"))
        check_code, check_detail = code_of(lambda: w.check_use(
            "req:1", first["token"], first["capability"]))
        effect_code, effect_detail = code_of(lambda: w.commit_effect(
            "req:1", first["token"], first["capability"],
            effect_ref="effect:original", payload_digest="d"))
        record("B3b-original-capability-remains-live",
               "original cap still authorized while lease/fence live; "
               "replay has no cap",
               {"replay_has_capability": "capability" in replay,
                "check": check_code, "check_detail": check_detail,
                "effect": effect_code, "effect_detail": effect_detail,
                "effect_count": effect_count(w)}, mode="binding",
               ok=(check_code == "ACCEPTED"
                   and effect_code == "ACCEPTED"
                   and "capability" not in replay
                   and effect_count(w) == 1),
               note="a replay does not claim to revoke the original "
                    "capability; it remains valid only while the lease/epoch "
                    "fence is live.")

    # B3c: replay after revoke is historical/no-cap; the original cap is
    # refused by the fence.
    with tempfile.TemporaryDirectory(prefix="qa2-382-bind-") as td:
        w = writer(td)
        first = w.acquire(req("req:1"))
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        replay = w.acquire(req("req:1"))
        no_cap_code, _ = code_of(lambda: w.check_use(
            "req:1", replay["token"], None))
        original_code, _ = code_of(lambda: w.check_use(
            "req:1", first["token"], first["capability"]))
        original_effect, _ = code_of(lambda: w.commit_effect(
            "req:1", first["token"], first["capability"],
            effect_ref="effect:revoked", payload_digest="d"))
        record("B3c-replay-after-revoke",
               "replay historical/no-cap; original cap POLICY_DENIED",
               {"replay": {k: replay.get(k) for k in
                           ("authorized", "historical", "capability_scope")},
                "replay_has_capability": "capability" in replay,
                "no_cap_check": no_cap_code,
                "original_check": original_code,
                "original_effect": original_effect,
                "effect_count": effect_count(w)}, mode="binding",
               ok=(replay.get("historical") is True
                   and "capability" not in replay
                   and replay.get("capability_scope")
                   == "HISTORICAL_AUDIT_ONLY"
                   and no_cap_code == "BLOCKED_CAPABILITY"
                   and original_code == "POLICY_DENIED"
                   and original_effect == "POLICY_DENIED"
                   and effect_count(w) == 0))

    # B3d: same request id with different facts is still a conflict.
    with tempfile.TemporaryDirectory(prefix="qa2-382-bind-") as td:
        w = writer(td)
        w.acquire(req("req:1"))
        different = req("req:1")
        different["ttl_s"] = 31.0
        conflict, _ = code_of(lambda: w.acquire(different))
        record("B3d-replay-different-facts",
               "IDEMPOTENCY_CONFLICT; effect_count 0",
               {"code": conflict, "effect_count": effect_count(w)},
               mode="binding", ok=(conflict == "IDEMPOTENCY_CONFLICT"
                                   and effect_count(w) == 0))

    # B5 token type validation: bool/float compare equal to the integer token.
    with tempfile.TemporaryDirectory(prefix="qa2-381-bind-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        bool_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], True, receipt["capability"]))
        float_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], 1.0, receipt["capability"]))
        str_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], "1", receipt["capability"]))
        record("B5-token-type-validation",
               "token must be an exact integer; bool/float must not equal 1",
               {"bool_token": bool_code, "float_token": float_code,
                "string_token": str_code,
                "effect_count": effect_count(w)}, mode="binding", kind="P2",
               ok=True,
               note="check_use uses int(commit.token) != int(token) and the "
                    "fence compares int(row.token) != token, so True and 1.0 "
                    "pass as token 1 (no authority beyond the same integer; "
                    "old atomic check rejected bool). This is a P2 type/"
                    "validation gap, not an escalation.")

    # B4 same-process owner/key/db_path caveat (now disclosed).
    with tempfile.TemporaryDirectory(prefix="qa2-381-bind-") as td:
        w = writer(td)
        issued = w.issue(
            policy_ref="policy:self", grant_ref="grant:self",
            context_ref="ctx:self", projects=["self"], items=["SELF"],
            actions=[ACTION], valid_from=VALID_FROM, expires_at=EXPIRES_AT,
            issued_at=ISSUED_AT, policy_epoch=9, authority_epoch=9,
            grant_epoch=9)
        self_code, _ = code_of(lambda: w.acquire(
            req("req:self", item="SELF", context_ref="ctx:self",
                project="self")))
        direct = w._writer.acquire(req("req:direct"))
        forged = w._capability(w.commit("req:direct"))
        forged_code, _ = code_of(lambda: w.check_use(
            "req:direct", direct["token"], forged))
        db_ok = False
        conn = sqlite3.connect(str(w._writer.db_path))
        try:
            conn.execute("SELECT COUNT(*) FROM proto_effect").fetchone()
            db_ok = True
        finally:
            conn.close()
        record("B4-same-process-caveat",
               "disclosed: owner issue/key/_writer/db_path reachable",
               {"self_issue": issued, "self_acquire": self_code,
                "key_len": len(w._capability_key),
                "forged_check": forged_code,
                "db_path": str(w._writer.db_path),
                "db_open": db_ok,
                "socket_api": any(hasattr(w, n) for n in
                                  ("socket", "bind", "listen", "accept"))},
               mode="binding", kind="P2", ok=True,
               note="README Round-6 same-process caveat now states the holder "
                    "can call owner methods, reach _writer/db_path and read "
                    "the capability key; this is disclosed prototype "
                    "capability correctness, not a production boundary.")


# ------------------------------------------------ B: raw DB (expected)

def raw_mode():
    # R1 raw effect marker.
    with tempfile.TemporaryDirectory(prefix="qa2-381-raw-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"], receipt["capability"]))
        kind, detail = execute(
            w, "INSERT INTO proto_effect("
               "effect_ref,request_id,resource,owner,token,payload_digest,"
               "committed_at,context_epoch,grant_epoch,policy_epoch,"
               "authority_epoch) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("effect:raw", "req:raw", RES, ACTOR, receipt["token"],
             "raw", "2033-05-18T03:33:51Z", 1, 1, 1, 1))
        record("R1-raw-effect-marker",
               "EXPECTED_RAW_DB_BYPASS: marker visible outside the API",
               {"check_after_revoke": check_code, "insert": kind,
                "detail": detail, "marker": w.effect("effect:raw")},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(check_code == "POLICY_DENIED" and kind == "OK"
                   and w.effect("effect:raw") is not None))

    # R2 raw context/commit forge + readable key.
    with tempfile.TemporaryDirectory(prefix="qa2-381-raw-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        issue_grant2(w)
        context_kind, context_detail = replace_row(
            w, "proto_context", "context_ref", CTX,
            {"grant_ref": GRANT2_REF, "actor_ref": ACTOR, "context_epoch": 2})
        commit_kind, commit_detail = replace_row(
            w, "proto_commit", "request_id", "req:1",
            {"context_epoch": 2, "grant_ref": GRANT2_REF,
             "grant_digest": atomic._content_digest(w._writer.grant(GRANT2_REF))})
        commit = w.commit("req:1")
        forged = w._capability(commit)
        code, detail = code_of(lambda: w.check_use(
            "req:1", receipt["token"], forged))
        record("R2-raw-context-commit-forge",
               "EXPECTED_RAW_DB_BYPASS: rewritten lineage accepted",
               {"context": context_kind, "context_detail": context_detail,
                "commit": commit_kind, "commit_detail": commit_detail,
                "check": code, "detail": detail},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(context_kind == "OK" and commit_kind == "OK"
                   and code == "ACCEPTED"))

    # R3 chmod 0600 same UID.
    with tempfile.TemporaryDirectory(prefix="qa2-381-raw-") as td:
        w = writer(td)
        w.acquire(req())
        os.chmod(w._writer.db_path, 0o600)
        mode = stat.S_IMODE(os.stat(w._writer.db_path).st_mode)
        with open(w._writer.db_path, "r+b") as handle:
            handle.read(16)
            opened = True
        record("R3-mode-0600-same-uid",
               "EXPECTED_RAW_DB_BYPASS: same UID opens 0600 DB",
               {"mode": oct(mode), "uid": os.getuid(), "open": opened},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(mode == 0o600 and opened))

    # R4 per-connection authorizer.
    with tempfile.TemporaryDirectory(prefix="qa2-381-raw-") as td:
        w = writer(td)
        w.acquire(req())
        denied = None
        conn1 = sqlite3.connect(w._writer.db_path, isolation_level=None)
        try:
            conn1.set_authorizer(
                lambda action, arg1, arg2, dbname, source: (
                    sqlite3.SQLITE_DENY
                    if action in (sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE,
                                  sqlite3.SQLITE_DELETE)
                    else sqlite3.SQLITE_OK))
            try:
                conn1.execute(
                    "INSERT INTO proto_effect("
                    "effect_ref,request_id,resource,owner,token,payload_digest,"
                    "committed_at,context_epoch,grant_epoch,policy_epoch,"
                    "authority_epoch) VALUES ('e1','r1',?,'a',1,'d','t',1,1,1,1)",
                    (RES,))
                denied = "OK"
            except BaseException as exc:  # noqa: BLE001
                denied = type(exc).__name__
        finally:
            conn1.close()
        kind, detail = execute(
            w, "INSERT INTO proto_effect("
               "effect_ref,request_id,resource,owner,token,payload_digest,"
               "committed_at,context_epoch,grant_epoch,policy_epoch,"
               "authority_epoch) VALUES ('e2','r2',?,'a',1,'d','t',1,1,1,1)",
            (RES,))
        record("R4-authorizer-per-connection",
               "EXPECTED_RAW_DB_BYPASS: conn1 denied, conn2 writes",
               {"conn1": denied, "conn2": kind, "detail": detail},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(denied == "DatabaseError" and kind == "OK"))

    # R5 legacy Store bypass.
    with tempfile.TemporaryDirectory(prefix="qa2-381-raw-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        store = atomic.ServerOSStore(str(w._writer.state_dir))
        legacy = store.acquire_lease(RES2, owner="human:LEGACY", ttl_s=30,
                                     now=NOW)
        legacy_check = store.check_writer(RES, owner=ACTOR,
                                          token=receipt["token"], now=NOW)
        fenced_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"], receipt["capability"]))
        record("R5-legacy-store-bypass",
               "EXPECTED_RAW_DB_BYPASS: legacy acquire/check_writer bypass",
               {"legacy_lease": legacy,
                "legacy_check_authorized": legacy_check["authorized"],
                "fenced_after_revoke": fenced_code,
                "commit_count": w._writer.commit_count()},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(legacy["token"] == 1
                   and legacy_check["authorized"] is True
                   and fenced_code == "POLICY_DENIED"))


# ------------------------------------------------ race / crash

def _worker_revoke(db_dir, start, out):
    w = swe.SingleWriteEntry(
        db_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID, actor_ref=ACTOR, capability_key=CAP_KEY)
    start.wait(timeout=15)
    try:
        out.put(("OK", w.revoke_policy(POLICY_REF,
                                       expected_policy_epoch=1)))
    except BaseException as exc:  # noqa: BLE001
        out.put((getattr(exc, "code", type(exc).__name__), str(exc)))


def _worker_effect_hold(db_dir, request_id, token, capability, effect_ref,
                        held, release, out):
    w = swe.SingleWriteEntry(
        db_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID, actor_ref=ACTOR, capability_key=CAP_KEY)

    def hold():
        held.set()
        release.wait(timeout=30)

    try:
        out.put(("OK", w.commit_effect(
            request_id, token, capability, effect_ref=effect_ref,
            payload_digest="d", hold=hold)))
    except BaseException as exc:  # noqa: BLE001
        out.put((getattr(exc, "code", type(exc).__name__), str(exc)))


def _worker_effect_crash(db_dir, request_id, token, capability, effect_ref):
    w = swe.SingleWriteEntry(
        db_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID, actor_ref=ACTOR, capability_key=CAP_KEY)
    w.commit_effect(request_id, token, capability, effect_ref=effect_ref,
                    payload_digest="d", hold=lambda: os._exit(17))


def race_mode():
    # RACE1 revoke-first.
    with tempfile.TemporaryDirectory(prefix="qa2-381-race-") as td:
        w = writer(td)
        receipt = w.acquire(req("req:1"))
        start = CTXMP.Barrier(2)
        out = CTXMP.Queue()
        process = CTXMP.Process(target=_worker_revoke,
                                args=(str(w._writer.state_dir), start, out))
        process.start()
        start.wait()
        child = out.get(timeout=30)
        process.join(30)
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"], receipt["capability"]))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:race1", payload_digest="d"))
        record("RACE1-revoke-first",
               "revoke OK; POLICY_DENIED; effect_count 0",
               {"child": child, "exitcode": process.exitcode,
                "check": check_code, "effect": effect_code,
                "effect_count": effect_count(w)}, mode="race",
               ok=(child[0] == "OK" and effect_code == "POLICY_DENIED"
                   and effect_count(w) == 0))

    # RACE2 effect-first hold blocks revoke.
    with tempfile.TemporaryDirectory(prefix="qa2-381-race-") as td:
        w = writer(td)
        receipt = w.acquire(req("req:1"))
        held = CTXMP.Event()
        release = CTXMP.Event()
        out = CTXMP.Queue()
        process = CTXMP.Process(
            target=_worker_effect_hold,
            args=(str(w._writer.state_dir), "req:1", receipt["token"],
                  receipt["capability"], "effect:race2", held, release, out))
        process.start()
        assert held.wait(timeout=30)
        blocked = None
        conn = sqlite3.connect(w._writer.db_path, timeout=0,
                               isolation_level=None)
        try:
            conn.execute("PRAGMA busy_timeout=0")
            try:
                conn.execute("BEGIN IMMEDIATE")
                conn.execute(
                    "UPDATE proto_owner_policy SET revoked=1 "
                    "WHERE policy_ref=?", (POLICY_REF,))
                conn.commit()
                blocked = "OK"
            except BaseException as exc:  # noqa: BLE001
                conn.rollback()
                blocked = type(exc).__name__
        finally:
            conn.close()
        release.set()
        child = out.get(timeout=30)
        process.join(30)
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        after_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"], receipt["capability"]))
        record("RACE2-effect-first-hold",
               "revoke blocked; effect commits; marker; then POLICY_DENIED",
               {"blocked": blocked, "child": child,
                "exitcode": process.exitcode,
                "marker": w.effect("effect:race2"),
                "check_after_revoke": after_code,
                "effect_count": effect_count(w)}, mode="race",
               ok=(blocked == "OperationalError"
                   and child[0] == "OK" and process.exitcode == 0
                   and w.effect("effect:race2") is not None
                   and after_code == "POLICY_DENIED"))

    # RACE3 crash-before-COMMIT.
    with tempfile.TemporaryDirectory(prefix="qa2-381-race-") as td:
        w = writer(td)
        receipt = w.acquire(req("req:1"))
        process = CTXMP.Process(
            target=_worker_effect_crash,
            args=(str(w._writer.state_dir), "req:1", receipt["token"],
                  receipt["capability"], "effect:crash"))
        process.start()
        process.join(30)
        reopened = writer(td, initialize=False)
        record("RACE3-crash-before-commit",
               "exitcode 17; no marker",
               {"exitcode": process.exitcode,
                "marker": reopened.effect("effect:crash"),
                "effect_count": effect_count(reopened)}, mode="race",
               ok=(process.exitcode == 17
                   and reopened.effect("effect:crash") is None
                   and effect_count(reopened) == 0))


def osuid_mode():
    record("C1-different-os-uid", "NOT_RUN: no privilege change",
           {"uid": os.getuid(), "euid": os.geteuid(),
            "privilege_change": "forbidden"}, mode="osuid", kind="NOT_RUN",
           ok=True)
    record("C2-real-peer-credential",
           "NOT_RUN: synthetic peer; no SO_PEERCRED",
           {"synthetic_peer_uid": UID, "kernel_peer": "NOT_RUN"},
           mode="osuid", kind="NOT_RUN", ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",
                        choices=("api", "binding", "raw", "race", "osuid"),
                        required=True)
    args = parser.parse_args()
    {"api": api_mode, "binding": binding_mode, "raw": raw_mode,
     "race": race_mode, "osuid": osuid_mode}[args.mode]()
    failed = [r for r in RESULTS if not r["ok"]]
    status = {}
    for row in RESULTS:
        key = row.get("kind") or "PASS"
        status[key] = status.get(key, 0) + 1
    print(json.dumps({"mode": args.mode, "total": len(RESULTS),
                      "failed": len(failed),
                      "failed_names": [r["name"] for r in failed],
                      "status": status}, ensure_ascii=False, sort_keys=True))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
