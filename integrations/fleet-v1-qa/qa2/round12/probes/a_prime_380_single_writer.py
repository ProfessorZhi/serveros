#!/usr/bin/env python3
"""QA2 independent witness for #380 @ cbbfb63a single-write-entry prototype.

Read-only against the exported exact head. Every DB is a temp WorkEngine-schema
SQLite DB; owner policy/grant/context/clock/peer are synthetic fixtures. No
production file, daemon, WorkEngine DB, credential, OAuth or service is used.

Modes:
  api         A / API_ONLY_GREEN: issue/acquire/use-check/effect, replay,
              tamper/revoke/ABA/stale/missing-grant, rollback, no production
              verified/COMMIT_FENCED. rc 0 iff all expected properties hold.
  capability  independent P1 capability-correctness attacks: a capability from
              request A is accepted with token B of another generation; the
              caller can self-issue policy/grant/context and choose epochs;
              the HMAC key and the state_dir/db_path are reachable on the same
              object. rc 1 iff any attack succeeds (expected).
  raw         B / EXPECTED_RAW_DB_BYPASS: same-UID raw SQLite inserts an
              effect marker / rewrites context+commit / chmod 0600 / bypasses a
              per-connection authorizer; legacy ServerOSStore bypasses.
              rc 0 iff all expected bypasses reproduce.
  race        two-process revoke-first, effect-first with a held transaction,
              and crash-before-COMMIT. rc 0 iff the serialization/rollback
              properties hold.
  osuid       C / OS_ISOLATION_NOT_RUN: no privilege change; NOT_RUN.

Evidence: LOCAL_REAL_SQLITE + SYNTHETIC_OWNER/CONTEXT/CLOCK/PEER + REAL_TEMP;
KERNEL_PEER_NOT_RUN, REAL_WINDOWS_NOT_RUN, EFFECT_COMMIT_NOT_PROVEN.
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
    "2026-10-21/checkouts/a-prime-380-cbbfb63")).resolve()
sys.path.insert(0, str(SNAP))
_spec = importlib.util.spec_from_file_location(
    "qa2_swe_380",
    SNAP / "integrations" / "fleet-v1-atomic-prototype" / "single_write_entry.py")
swe = importlib.util.module_from_spec(_spec)
sys.modules["qa2_swe_380"] = swe
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
CAP_KEY = b"qa2-test-capability-key"
RESULTS = []
CTXMP = multiprocessing.get_context("fork") if hasattr(os, "fork") else None


class FixedClock:
    def __init__(self, now=NOW):
        self.now = float(now)

    def set(self, now):
        self.now = float(now)

    def __call__(self):
        return self.now


def req(request_id="req:1", *, item=ITEM, context_ref=CTX, project=PROJECT):
    return {"request_id": request_id, "context_ref": context_ref,
            "project": project, "item_ref": item, "action": ACTION,
            "ttl_s": 30.0}


def writer(tmp_path, *, clock=None, initialize=True, key=CAP_KEY):
    w = swe.SingleWriteEntry(
        str(Path(tmp_path) / "db"), initialize=initialize,
        clock=clock or FixedClock(NOW), synthetic_peer_uid=UID,
        actor_ref=ACTOR, capability_key=key)
    if initialize:
        w.issue(policy_ref=POLICY_REF, grant_ref=GRANT_REF,
                context_ref=CTX, projects=[PROJECT], items=[ITEM, ITEM2],
                actions=[ACTION], valid_from=VALID_FROM,
                expires_at=EXPIRES_AT, issued_at=ISSUED_AT)
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


def query(w, statement, params=()):
    conn = sqlite3.connect(w._writer.db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(statement, params)]
    finally:
        conn.close()


def execute(w, statement, params=(), *, timeout=30, pragma=None):
    conn = sqlite3.connect(w._writer.db_path, timeout=timeout,
                           isolation_level=None)
    try:
        conn.execute(f"PRAGMA busy_timeout={int(timeout * 1000)}")
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


def record(name, expected, actual, *, ok, mode=None, kind=None, note=""):
    row = {"name": name, "expected": expected, "actual": actual,
           "ok": bool(ok), "note": note}
    if mode:
        row["mode"] = mode
    if kind:
        row["kind"] = kind
    RESULTS.append(row)
    print(json.dumps(row, ensure_ascii=False, sort_keys=True, default=str))


# ---------------------------------------------------------------- A: API only

def api_mode():
    # A1 happy path: issue -> acquire -> use-check -> effect marker.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        checked = w.check_use(receipt["request_id"], receipt["token"],
                              receipt["capability"])
        marker = w.commit_effect(receipt["request_id"], receipt["token"],
                                 receipt["capability"], effect_ref="effect:1",
                                 payload_digest="digest:1")
        row = w.effect("effect:1")
        record("A1-happy-path", "authorized; one marker; no production marker",
               {"receipt": receipt, "check": checked, "marker": marker,
                "row": row, "effect_count": effect_count(w)}, mode="api",
               ok=(receipt["prototype_status"] == "PROTOTYPE_FENCED_COMMIT"
                   and checked["prototype_status"] == "PROTOTYPE_USE_CHECK_OK"
                   and checked["authorized"] is True
                   and marker["prototype_status"]
                   == "PROTOTYPE_EFFECT_MARKER_COMMITTED"
                   and marker["effect_marker"] is True
                   and marker["production_effect"] is False
                   and row["token"] == receipt["token"]
                   and effect_count(w) == 1))

    # A2 check_use must not write a marker.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.check_use(receipt["request_id"], receipt["token"],
                    receipt["capability"])
        record("A2-check-does-not-write", "effect_count 0",
               {"effect_count": effect_count(w)}, mode="api",
               ok=effect_count(w) == 0)

    # A3 replay is idempotent and does not re-authorize.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        first = w.commit_effect(receipt["request_id"], receipt["token"],
                                receipt["capability"], effect_ref="effect:1",
                                payload_digest="digest:1")
        again = w.commit_effect(receipt["request_id"], receipt["token"],
                                receipt["capability"], effect_ref="effect:1",
                                payload_digest="digest:1")
        record("A3-replay-idempotent",
               "replayed:true, authorized:false, count 1",
               {"first": first, "again": again,
                "effect_count": effect_count(w)}, mode="api",
               ok=(again["replayed"] is True
                   and again["authorized"] is False
                   and effect_count(w) == 1))

    # A4 conflicting payload / same request different effect_ref.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.commit_effect(receipt["request_id"], receipt["token"],
                        receipt["capability"], effect_ref="effect:1",
                        payload_digest="digest:1")
        conflict, conflict_detail = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:1", payload_digest="digest:2"))
        second, second_detail = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:2", payload_digest="digest:1"))
        record("A4-effect-conflicts",
               "IDEMPOTENCY_CONFLICT; EFFECT_ALREADY_COMMITTED; count 1",
               {"conflict": conflict, "conflict_detail": conflict_detail,
                "second": second, "second_detail": second_detail,
                "effect_count": effect_count(w)}, mode="api",
               ok=(conflict == "IDEMPOTENCY_CONFLICT"
                   and second == "EFFECT_ALREADY_COMMITTED"
                   and effect_count(w) == 1))

    # A5 tampered capability.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        bad = receipt["capability"][:-1] + (
            "0" if receipt["capability"][-1] != "0" else "1")
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"], bad))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], bad,
            effect_ref="effect:bad", payload_digest="d"))
        record("A5-tampered-capability",
               "BLOCKED_CAPABILITY; effect_count 0",
               {"check": check_code, "effect": effect_code,
                "effect_count": effect_count(w)}, mode="api",
               ok=(check_code == "BLOCKED_CAPABILITY"
                   and effect_code == "BLOCKED_CAPABILITY"
                   and effect_count(w) == 0))

    # A6 revoke-first: use and effect refuse; no marker.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        check_code, check_detail = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"],
            receipt["capability"]))
        effect_code, effect_detail = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:revoked", payload_digest="d"))
        record("A6-revoke-first", "POLICY_DENIED; effect_count 0",
               {"check": check_code, "check_detail": check_detail,
                "effect": effect_code, "effect_detail": effect_detail,
                "effect_count": effect_count(w)}, mode="api",
               ok=(check_code == "POLICY_DENIED"
                   and effect_code == "POLICY_DENIED"
                   and effect_count(w) == 0))

    # A7 context ABA.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        issue_grant2(w)
        w.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT2_REF)
        w.rebind_context(CTX, expected_context_epoch=2, grant_ref=GRANT_REF)
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"],
            receipt["capability"]))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:aba", payload_digest="d"))
        record("A7-context-aba",
               "BLOCKED_STALE_CONTEXT_EPOCH; effect_count 0",
               {"check": check_code, "effect": effect_code,
                "effect_count": effect_count(w)}, mode="api",
               ok=(check_code == "BLOCKED_STALE_CONTEXT_EPOCH"
                   and effect_code == "BLOCKED_STALE_CONTEXT_EPOCH"
                   and effect_count(w) == 0))

    # A8 expired / superseded token.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        clock = FixedClock(NOW)
        w = writer(td, clock=clock)
        first = w.acquire(req("req:1"))
        clock.set(NOW + 31)
        expired_code, _ = code_of(lambda: w.check_use(
            first["request_id"], first["token"], first["capability"]))
        expired_effect, _ = code_of(lambda: w.commit_effect(
            first["request_id"], first["token"], first["capability"],
            effect_ref="effect:ttl", payload_digest="d"))
        second = w.acquire(req("req:2"))
        stale_code, _ = code_of(lambda: w.check_use(
            first["request_id"], first["token"], first["capability"]))
        stale_effect, _ = code_of(lambda: w.commit_effect(
            first["request_id"], first["token"], first["capability"],
            effect_ref="effect:stale", payload_digest="d"))
        record("A8-expired-superseded",
               "LEASE_EXPIRED then STALE_TOKEN; effect_count 0",
               {"expired": expired_code, "expired_effect": expired_effect,
                "stale": stale_code, "stale_effect": stale_effect,
                "second_token": second["token"],
                "effect_count": effect_count(w)}, mode="api",
               ok=(expired_code == "LEASE_EXPIRED"
                   and expired_effect == "LEASE_EXPIRED"
                   and stale_code == "STALE_TOKEN"
                   and stale_effect == "STALE_TOKEN"
                   and effect_count(w) == 0))

    # A9 missing grant / policy: the fence refuses; no marker.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        execute(w, "DELETE FROM proto_grant WHERE grant_ref=?", (GRANT_REF,))
        code, detail = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"],
            receipt["capability"]))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:nogrant", payload_digest="d"))
        record("A9-missing-grant", "BLOCKED_OWNER_POLICY; effect_count 0",
               {"check": code, "detail": detail, "effect": effect_code,
                "effect_count": effect_count(w)}, mode="api",
               ok=(code == "BLOCKED_OWNER_POLICY"
                   and effect_code == "BLOCKED_OWNER_POLICY"
                   and effect_count(w) == 0))

    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        execute(w, "DELETE FROM proto_owner_policy WHERE policy_ref=?",
                (POLICY_REF,))
        code, detail = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"],
            receipt["capability"]))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:nopolicy", payload_digest="d"))
        record("A9b-missing-policy", "BLOCKED_OWNER_POLICY; effect_count 0",
               {"check": code, "detail": detail, "effect": effect_code,
                "effect_count": effect_count(w)}, mode="api",
               ok=(code == "BLOCKED_OWNER_POLICY"
                   and effect_code == "BLOCKED_OWNER_POLICY"
                   and effect_count(w) == 0))

    # A10 caller-side authority injection.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        kwargs = {}
        for call in ("uid", "actor_ref", "now", "policy_ref", "grant_ref",
                     "context_epoch", "authority_epoch"):
            try:
                w.acquire(req(), **{call: 1})
                kwargs[call] = "ACCEPTED"
            except TypeError:
                kwargs[call] = "TypeError"
        receipt = w.acquire(req())
        bad_request = req()
        bad_request["uid"] = UID
        injected, detail = code_of(lambda: w.acquire(bad_request))
        record("A10-injection", "TypeError; INVALID_REQUEST",
               {"kwarg_calls": kwargs, "request_injected": injected,
                "detail": detail}, mode="api",
               ok=(all(v == "TypeError" for v in kwargs.values())
                   and injected == "INVALID_REQUEST"))

    # A11 marker rollback on a fault in the same transaction.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())

        def explode():
            raise RuntimeError("test fault after marker insert")

        try:
            w.commit_effect(receipt["request_id"], receipt["token"],
                            receipt["capability"], effect_ref="effect:fault",
                            payload_digest="d", hold=explode)
            code = "NO_EXCEPTION"
        except RuntimeError:
            code = "RuntimeError"
        record("A11-marker-rollback", "RuntimeError; no effect row",
               {"code": code, "effect": w.effect("effect:fault"),
                "effect_count": effect_count(w)}, mode="api",
               ok=(code == "RuntimeError" and w.effect("effect:fault") is None
                   and effect_count(w) == 0))

    # A13 replay after revoke / after context ABA: the fence runs before the
    # idempotency lookup, so a replay does not re-authorize; no new marker.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.commit_effect(receipt["request_id"], receipt["token"],
                        receipt["capability"], effect_ref="effect:1",
                        payload_digest="d")
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        code, detail = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:1", payload_digest="d"))
        record("A13-replay-after-revoke",
               "POLICY_DENIED; no new authorization",
               {"code": code, "detail": detail,
                "effect_count": effect_count(w)}, mode="api",
               ok=(code == "POLICY_DENIED" and effect_count(w) == 1),
               note="replay is fail-closed after revoke; an idempotent retry "
                    "cannot obtain a historical replay receipt once the fence "
                    "no longer authorizes")

    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.commit_effect(receipt["request_id"], receipt["token"],
                        receipt["capability"], effect_ref="effect:1",
                        payload_digest="d")
        issue_grant2(w)
        w.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT2_REF)
        w.rebind_context(CTX, expected_context_epoch=2, grant_ref=GRANT_REF)
        code, detail = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:1", payload_digest="d"))
        record("A14-replay-after-aba",
               "BLOCKED_STALE_CONTEXT_EPOCH; no new authorization",
               {"code": code, "detail": detail,
                "effect_count": effect_count(w)}, mode="api",
               ok=(code == "BLOCKED_STALE_CONTEXT_EPOCH"
                   and effect_count(w) == 1))

    # A12 no production verified / COMMIT_FENCED; classification labels.
    with tempfile.TemporaryDirectory(prefix="qa2-380-api-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        checked = w.check_use(receipt["request_id"], receipt["token"],
                              receipt["capability"])
        marker = w.commit_effect(receipt["request_id"], receipt["token"],
                                 receipt["capability"], effect_ref="effect:1",
                                 payload_digest="d")
        keys = set(receipt) | set(checked) | set(marker)
        values = json.dumps([receipt, checked, marker], default=str)
        report = swe.classification()
        record("A12-no-production-marker",
               "no verified key / COMMIT_FENCED; A/B/C labels",
               {"keys": sorted(keys), "contains_verified": "verified" in keys,
                "contains_COMMIT_FENCED": "COMMIT_FENCED" in values,
                "classification": report}, mode="api",
               ok=("verified" not in keys
                   and "COMMIT_FENCED" not in values
                   and report["A"]["name"] == swe.API_ONLY_GREEN
                   and report["B"]["name"] == swe.RAW_DB_WRITE_BYPASS_EXPECTED
                   and report["C"]["name"] == swe.OS_ISOLATION_NOT_RUN
                   and report["effect_commit"] == swe.EFFECT_COMMIT_NOT_PROVEN
                   and report["production"] == "NOT_V1_SIGNED"))


# ------------------------------------------------ capability-correctness RED

def capability_mode():
    # C1: a capability for request A is accepted with token B of another
    # generation; the effect marker mixes A's request_id with B's epochs.
    with tempfile.TemporaryDirectory(prefix="qa2-380-cap-") as td:
        clock = FixedClock(NOW)
        w = writer(td, clock=clock)
        first = w.acquire(req("req:1"))
        issue_grant2(w)
        w.rebind_context(CTX, expected_context_epoch=1, grant_ref=GRANT2_REF)
        clock.set(NOW + 31)
        second = w.acquire(req("req:2"))
        cross_code, cross_detail = code_of(lambda: w.check_use(
            "req:1", second["token"], first["capability"]))
        effect_code, effect_detail = code_of(lambda: w.commit_effect(
            "req:1", second["token"], first["capability"],
            effect_ref="effect:cross", payload_digest="d"))
        marker = w.effect("effect:cross")
        record("C1-cross-token-capability",
               "capability for req:1 must not authorize token 2",
               {"first": {k: first[k] for k in ("request_id", "token",
                                                "context_epoch")},
                "second": {k: second[k] for k in ("request_id", "token",
                                                  "context_epoch")},
                "cross_check": cross_code, "cross_detail": cross_detail,
                "cross_effect": effect_code, "effect_detail": effect_detail,
                "marker": marker}, mode="capability", kind="P1",
               ok=(cross_code != "ACCEPTED" and effect_code != "ACCEPTED"),
               note="check_use fetches commit by request_id but passes the "
                    "caller's token to the fence without requiring "
                    "commit.token == token; commit_effect then writes a marker "
                    "with request_id req:1, token 2 and token 2's epochs.")

    # C2: the same object exposes owner-side issue; the caller can mint
    # arbitrary policy/grant/context and choose epochs.
    with tempfile.TemporaryDirectory(prefix="qa2-380-cap-") as td:
        w = writer(td)
        issued = w.issue(
            policy_ref="policy:self", grant_ref="grant:self",
            context_ref="ctx:self", projects=["self-project"],
            items=["SRV-SELF"], actions=[ACTION], valid_from=VALID_FROM,
            expires_at=EXPIRES_AT, issued_at=ISSUED_AT, policy_epoch=9,
            authority_epoch=9, grant_epoch=9)
        receipt_code, receipt_detail = code_of(lambda: w.acquire(
            req("req:self", item="SRV-SELF", context_ref="ctx:self",
                project="self-project")))
        record("C2-caller-self-issue",
               "a restricted caller cannot issue policy/grant/context",
               {"issued": issued, "acquire": receipt_code,
                "detail": receipt_detail,
                "context": w.context("ctx:self")}, mode="capability",
               kind="P1",
               ok=receipt_code != "ACCEPTED",
               note="issue/issue_grant/revoke/rebind are methods on the same "
                    "object the caller uses; issue accepts policy/grant/epoch "
                    "and creates authority. There is no owner/caller split.")

    # C3: the HMAC key is readable; a forged capability is accepted.
    with tempfile.TemporaryDirectory(prefix="qa2-380-cap-") as td:
        w = writer(td)
        receipt = w._writer.acquire(req("req:direct"))
        commit = w.commit("req:direct")
        forged = w._capability(commit)
        code, detail = code_of(lambda: w.check_use(
            "req:direct", receipt["token"], forged))
        record("C3-readable-hmac-key",
               "the capability key is not reachable from the caller object",
               {"key_type": type(w._capability_key).__name__,
                "key_len": len(w._capability_key),
                "forged_capability": forged[:16],
                "check": code, "detail": detail},
               mode="capability", kind="P1",
               ok=code != "ACCEPTED",
               note="entry._capability_key is a public attribute; any holder "
                    "of the object can mint a capability for any commit.")

    # C4: the state_dir / db_path of the internal writer are reachable.
    with tempfile.TemporaryDirectory(prefix="qa2-380-cap-") as td:
        w = writer(td)
        w.acquire(req())
        db_path = str(w._writer.db_path)
        conn = sqlite3.connect(db_path)
        try:
            count = int(conn.execute(
                "SELECT COUNT(*) FROM proto_effect").fetchone()[0])
        finally:
            conn.close()
        record("C4-public-state-dir-db-path",
               "the caller cannot reach the authoritative DB path",
               {"state_dir": str(w._writer.state_dir),
                "db_path": db_path,
                "writable_connection": True,
                "effect_count": count}, mode="capability", kind="P1",
               ok=False,
               note="SingleWriteEntry is a same-process Python class; its "
                    "_writer (AtomicFencePrototype) exposes state_dir/db_path "
                    "and a writable SQLite connection. It is not a single "
                    "writer process or IPC capability.")

    # C6: a historical replay receipt carries a usable capability; check_use
    # and commit_effect turn the replay's authorized:false into current
    # authorization and an effect marker.
    with tempfile.TemporaryDirectory(prefix="qa2-380-cap-") as td:
        w = writer(td)
        first = w.acquire(req("req:1"))
        replay = w.acquire(req("req:1"))
        replay_check = w.check_use("req:1", replay["token"],
                                   replay["capability"])
        replay_effect = w.commit_effect(
            "req:1", replay["token"], replay["capability"],
            effect_ref="effect:replay", payload_digest="d")
        record("C6-replay-capability-reauthorizes",
               "a historical replay must not yield current authorization",
               {"first": {k: first[k] for k in ("authorized", "historical",
                                                "token")},
                "replay_receipt": {k: replay.get(k) for k in
                                   ("authorized", "historical", "replayed",
                                    "token")},
                "replay_has_capability": bool(replay.get("capability")),
                "check_use": replay_check["authorized"],
                "effect": replay_effect["authorized"],
                "effect_marker": replay_effect["effect_marker"],
                "effect_count": effect_count(w)},
               mode="capability", kind="P1",
               ok=(replay_check["authorized"] is False
                   and replay_effect["authorized"] is False),
               note="SingleWriteEntry.acquire unconditionally attaches a "
                    "capability, including to a PROTOTYPE_HISTORICAL_REPLAY "
                    "receipt with authorized:false/historical:true. check_use "
                    "and commit_effect re-run the fence and accept it as "
                    "current authority, writing an effect marker.")

    # C5: constructor identity is caller-selected; no socket/uid/IPC.
    with tempfile.TemporaryDirectory(prefix="qa2-380-cap-") as td:
        w = swe.SingleWriteEntry(
            str(Path(td) / "db"), clock=FixedClock(NOW),
            synthetic_peer_uid=7777, actor_ref="human:SELF",
            capability_key=b"self-key")
        has_socket = any(hasattr(w, name) for name in
                         ("socket", "bind", "listen", "accept"))
        record("C5-constructor-self-assert",
               "actor/uid/clock/key are bound by a trusted entrypoint",
               {"actor": w._actor, "uid": w._writer._synthetic_peer_uid,
                "key": w._capability_key,
                "socket_api": has_socket,
                "is_python_object": isinstance(w, swe.SingleWriteEntry)},
               mode="capability", kind="P1",
               ok=False,
               note="the caller constructing the object chooses actor_ref, "
                    "synthetic_peer_uid and capability_key; no SO_PEERCRED, "
                    "socket, subprocess or UID check exists.")


# ------------------------------------------------ B: raw DB writer (expected)

def raw_mode():
    # R1: insert an effect marker after revoke; the API refuses the use but
    # the marker is visible to effect().
    with tempfile.TemporaryDirectory(prefix="qa2-380-raw-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"],
            receipt["capability"]))
        kind, detail = execute(
            w, "INSERT INTO proto_effect("
               "effect_ref,request_id,resource,owner,token,payload_digest,"
               "committed_at,context_epoch,grant_epoch,policy_epoch,"
               "authority_epoch) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("effect:raw", "req:raw", RES, ACTOR, receipt["token"],
             "raw-digest", "2033-05-18T03:33:51Z", 1, 1, 1, 1))
        record("R1-raw-effect-marker",
               "EXPECTED_RAW_DB_BYPASS: marker inserted outside the API",
               {"check_after_revoke": check_code, "insert": kind,
                "detail": detail, "marker": w.effect("effect:raw"),
                "classification": swe.classification()["B"]},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(check_code == "POLICY_DENIED" and kind == "OK"
                   and w.effect("effect:raw") is not None))

    # R2: raw REPLACE context+commit plus the readable HMAC key makes an old
    # token current and a forged capability accepted.
    with tempfile.TemporaryDirectory(prefix="qa2-380-raw-") as td:
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
               "EXPECTED_RAW_DB_BYPASS: old token current after raw rewrite",
               {"context_replace": context_kind, "context_detail": context_detail,
                "commit_replace": commit_kind, "commit_detail": commit_detail,
                "context": w.context(CTX), "commit": {
                    "context_epoch": commit["context_epoch"],
                    "grant_ref": commit["grant_ref"]},
                "check": code, "detail": detail},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(context_kind == "OK" and commit_kind == "OK"
                   and code == "ACCEPTED"))

    # R3: chmod 0600 is not a same-UID boundary.
    with tempfile.TemporaryDirectory(prefix="qa2-380-raw-") as td:
        w = writer(td)
        w.acquire(req())
        os.chmod(w._writer.db_path, 0o600)
        mode = stat.S_IMODE(os.stat(w._writer.db_path).st_mode)
        with open(w._writer.db_path, "r+b") as handle:
            handle.read(16)
            opened = True
        record("R3-mode-0600-same-uid",
               "EXPECTED_RAW_DB_BYPASS: 0600 still writable by same UID",
               {"mode": oct(mode), "uid": os.getuid(),
                "db_owner_uid": os.stat(w._writer.db_path).st_uid,
                "same_uid_rw_open": opened},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(mode == 0o600 and opened))

    # R4: authorizer is per-connection.
    with tempfile.TemporaryDirectory(prefix="qa2-380-raw-") as td:
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
               {"conn1": denied, "conn2": kind, "detail": detail,
                "effect_count": effect_count(w)},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(denied == "DatabaseError" and kind == "OK"))

    # R5: legacy ServerOSStore still bypasses the writer API.
    with tempfile.TemporaryDirectory(prefix="qa2-380-raw-") as td:
        w = writer(td)
        receipt = w.acquire(req())
        w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        store = atomic.ServerOSStore(str(w._writer.state_dir))
        legacy = store.acquire_lease(RES2, owner="human:LEGACY", ttl_s=30,
                                     now=NOW)
        legacy_check = store.check_writer(RES, owner=ACTOR,
                                          token=receipt["token"], now=NOW)
        fenced_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"],
            receipt["capability"]))
        record("R5-legacy-store-bypass",
               "EXPECTED_RAW_DB_BYPASS: legacy acquire/check_writer bypass",
               {"legacy_lease": legacy,
                "legacy_check_authorized": legacy_check["authorized"],
                "fenced_after_revoke": fenced_code,
                "commit_count": w._writer.commit_count()},
               mode="raw", kind="EXPECTED_FAIL",
               ok=(legacy["token"] == 1
                   and legacy_check["authorized"] is True
                   and fenced_code == "POLICY_DENIED"
                   and w._writer.commit_count() == 1))

    # R6: classification is honest, not a product PASS.
    record("R6-classification",
           "RAW_DB_WRITE_BYPASS_EXPECTED / BLOCKED_DB_WRITER_ISOLATION",
           {"classification": swe.classification()}, mode="raw",
           kind="EXPECTED_FAIL",
           ok=(swe.classification()["B"]["name"]
               == swe.RAW_DB_WRITE_BYPASS_EXPECTED
               and swe.classification()["B"]["blocked"]
               == swe.BLOCKED_DB_WRITER_ISOLATION))


# ------------------------------------------------ two-process race / crash

def _worker_revoke(db_dir, policy_ref, start, out):
    w = swe.SingleWriteEntry(
        db_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID, actor_ref=ACTOR, capability_key=CAP_KEY)
    start.wait(timeout=15)
    try:
        result = w.revoke_policy(policy_ref, expected_policy_epoch=1)
        out.put(("OK", result))
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
        result = w.commit_effect(request_id, token, capability,
                                 effect_ref=effect_ref, payload_digest="d",
                                 hold=hold)
        out.put(("OK", result))
    except BaseException as exc:  # noqa: BLE001
        out.put((getattr(exc, "code", type(exc).__name__), str(exc)))


def _worker_effect_crash(db_dir, request_id, token, capability, effect_ref):
    w = swe.SingleWriteEntry(
        db_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID, actor_ref=ACTOR, capability_key=CAP_KEY)
    w.commit_effect(request_id, token, capability, effect_ref=effect_ref,
                    payload_digest="d", hold=lambda: os._exit(17))


def race_mode():
    # RACE1 revoke-first: the revoke commits first; use/effect refuse.
    with tempfile.TemporaryDirectory(prefix="qa2-380-race-") as td:
        w = writer(td)
        receipt = w.acquire(req("req:1"))
        start = CTXMP.Barrier(2)
        out = CTXMP.Queue()
        process = CTXMP.Process(target=_worker_revoke,
                                args=(str(w._writer.state_dir), POLICY_REF,
                                      start, out))
        process.start()
        start.wait()
        child = out.get(timeout=30)
        process.join(30)
        check_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"],
            receipt["capability"]))
        effect_code, _ = code_of(lambda: w.commit_effect(
            receipt["request_id"], receipt["token"], receipt["capability"],
            effect_ref="effect:race1", payload_digest="d"))
        record("RACE1-revoke-first",
               "child revoke OK; POLICY_DENIED; effect_count 0",
               {"child": child, "exitcode": process.exitcode,
                "check": check_code, "effect": effect_code,
                "effect_count": effect_count(w)}, mode="race",
               ok=(child[0] == "OK" and effect_code == "POLICY_DENIED"
                   and effect_count(w) == 0))

    # RACE2 effect-first: the effect transaction holds the write lock; a
    # revoke from another connection with busy_timeout=0 must be blocked;
    # after release the effect commits and only then can the revoke commit.
    with tempfile.TemporaryDirectory(prefix="qa2-380-race-") as td:
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
        revoke = w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        after_code, _ = code_of(lambda: w.check_use(
            receipt["request_id"], receipt["token"],
            receipt["capability"]))
        record("RACE2-effect-first-hold",
               "revoke blocked while effect holds; effect commits; then revoke",
               {"blocked": blocked, "child": child,
                "exitcode": process.exitcode, "revoke": revoke,
                "marker": w.effect("effect:race2"),
                "check_after_revoke": after_code,
                "effect_count": effect_count(w)}, mode="race",
               ok=(blocked == "OperationalError"
                   and child[0] == "OK" and process.exitcode == 0
                   and w.effect("effect:race2") is not None
                   and after_code == "POLICY_DENIED"))

    # RACE3 crash-before-COMMIT: marker insert rolls back with the process.
    with tempfile.TemporaryDirectory(prefix="qa2-380-race-") as td:
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
               "exitcode 17; no effect marker",
               {"exitcode": process.exitcode,
                "marker": reopened.effect("effect:crash"),
                "effect_count": effect_count(reopened)}, mode="race",
               ok=(process.exitcode == 17
                   and reopened.effect("effect:crash") is None
                   and effect_count(reopened) == 0))


# ------------------------------------------------ C: OS isolation (NOT_RUN)

def osuid_mode():
    record("C1-different-os-uid", "NOT_RUN: no privilege change",
           {"uid": os.getuid(), "euid": os.geteuid(),
            "privilege_change": "forbidden",
            "reason": "no sudo/setuid/chown; second real UID cannot be attested"},
           mode="osuid", kind="NOT_RUN", ok=True)
    record("C2-real-peer-credential",
           "NOT_RUN: synthetic peer uid; no SO_PEERCRED",
           {"synthetic_peer_uid": UID, "kernel_peer": "NOT_RUN"},
           mode="osuid", kind="NOT_RUN", ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode",
                        choices=("api", "capability", "raw", "race", "osuid"),
                        required=True)
    args = parser.parse_args()
    {"api": api_mode, "capability": capability_mode, "raw": raw_mode,
     "race": race_mode, "osuid": osuid_mode}[args.mode]()
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
