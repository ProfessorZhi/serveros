#!/usr/bin/env python3
"""QA2 independent witnesses for A' prototype #376 @ 02688cec.

Read-only against the exported exact head. Uses the prototype API and the real
WorkEngine SQLite schema. Owner policy/grant/context/clock/peer are synthetic
fixtures. No production file, daemon, WorkEngine, database, credential or
service is modified.

Modes:
  controls  A/B/C/D/E/F fail-closed prototype controls; rc 0 iff all hold.
  findings  independent P0 production-wiring and P1/P2 counterexamples; rc 1
            iff a gap is confirmed. P0 findings are production barriers, not
            claims that the prototype itself is production.

Evidence: LOCAL_REAL_SQLITE + SYNTHETIC_OWNER/CLOCK/PEER; REAL_TEMP for the
temp SQLite DB; KERNEL_PEER_NOT_RUN and REAL_WINDOWS_NOT_RUN.
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
    "2026-10-21/checkouts/a-prime-376-02688ce")).resolve()
sys.path.insert(0, str(SNAP))

spec = importlib.util.spec_from_file_location(
    "qa2_atomic_fence_376",
    SNAP / "integrations" / "fleet-v1-atomic-prototype" / "atomic_fence.py")
proto = importlib.util.module_from_spec(spec)
sys.modules["qa2_atomic_fence_376"] = proto
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
CTXMP = multiprocessing.get_context("fork") if hasattr(os, "fork") else None
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


def world(root, *, clock=None, peer=UID, policy_over=None, grant_over=None,
          context_over=None, seed_policy=True, seed_grant=True,
          seed_context=True):
    p = proto.AtomicFencePrototype(
        str(Path(root) / "proto"), clock=clock or FixedClock(NOW),
        synthetic_peer_uid=peer)
    if seed_policy:
        values = dict(policy_ref=POLICY_REF, os_principal=f"uid:{UID}",
                      actor_ref=ACTOR, projects=[PROJECT], items=[ITEM],
                      actions=["lease.acquire"], valid_from=VALID_FROM,
                      expires_at=EXPIRES_AT, issued_at=ISSUED_AT)
        values.update(policy_over or {})
        p.seed_policy(**values)
    if seed_grant:
        values = dict(grant_ref=GRANT_REF, policy_ref=POLICY_REF,
                      os_principal=f"uid:{UID}", actor_ref=ACTOR,
                      projects=[PROJECT], items=[ITEM],
                      actions=["lease.acquire"], valid_from=VALID_FROM,
                      expires_at=EXPIRES_AT, grant_epoch=1,
                      owner_policy_epoch=1)
        values.update(grant_over or {})
        p.seed_grant(**values)
    if seed_context:
        values = dict(context_ref=CTX, actor_ref=ACTOR, grant_ref=GRANT_REF)
        values.update(context_over or {})
        p.seed_context(**values)
    return p


def code_of(fn):
    try:
        return "ACCEPTED", fn()
    except proto.PrototypeRefusal as exc:
        return exc.code, exc.detail
    except BaseException as exc:  # noqa: BLE001
        return type(exc).__name__, str(exc)


def record(name, level, expected, actual, *, ok, severity=None, note=""):
    RESULTS.append({"name": name, "level": level, "expected": expected,
                    "actual": actual, "ok": bool(ok), "severity": severity,
                    "note": note})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True,
                     default=str))


# ---------------------------------------------------------------------------
# Process workers
# ---------------------------------------------------------------------------

def _acquire_worker(db_dir, request, barrier, queue):
    p = proto.AtomicFencePrototype(
        db_dir, initialize=False, clock=FixedClock(NOW), synthetic_peer_uid=UID)
    try:
        barrier.wait(timeout=15)
        r = p.acquire(request)
        queue.put(("ok", r["token"], r["request_id"]))
    except BaseException as exc:  # noqa: BLE001
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _hold_worker(db_dir, request, written, release, queue):
    p = proto.AtomicFencePrototype(
        db_dir, initialize=False, clock=FixedClock(NOW), synthetic_peer_uid=UID)

    def hold():
        written.set()
        release.wait(15)

    try:
        r = p.acquire(request, hold=hold)
        queue.put(("ok", r["token"]))
    except BaseException as exc:  # noqa: BLE001
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _revoke_probe(db_dir, attempted, blocked, release, queue):
    conn = sqlite3.connect(str(Path(db_dir) / "serveros.db"), timeout=0,
                           isolation_level=None)
    attempted.set()
    locked = None
    try:
        conn.execute("BEGIN IMMEDIATE")
    except sqlite3.OperationalError as exc:
        locked = str(exc)
        blocked.set()
    if locked is None:
        conn.rollback()
        blocked.set()
    queue.put(("blocked", locked))
    release.wait(15)
    conn.close()
    p = proto.AtomicFencePrototype(
        db_dir, initialize=False, clock=FixedClock(NOW), synthetic_peer_uid=UID)
    try:
        r = p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        queue.put(("ok", r["policy_epoch"], r["authority_epoch"]))
    except BaseException as exc:  # noqa: BLE001
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _crash_worker(db_dir, request):
    p = proto.AtomicFencePrototype(
        db_dir, initialize=False, clock=FixedClock(NOW), synthetic_peer_uid=UID)
    p.acquire(request, hold=lambda: os._exit(17))


def _commit_exit_worker(db_dir, request, queue):
    p = proto.AtomicFencePrototype(
        db_dir, initialize=False, clock=FixedClock(NOW), synthetic_peer_uid=UID)
    r = p.acquire(request)
    queue.put(("ok", r["token"]))
    queue.close()
    queue.join_thread()
    os._exit(23)


# ---------------------------------------------------------------------------
# Controls A/B/C/D/E/F
# ---------------------------------------------------------------------------

def controls():
    # A. Commit-phase re-check with a FixedClock jump after the held write.
    cases = [
        ("A-ttl-expiry", dict(), dict(), NOW + 31,
         "LEASE_EXPIRED_AT_COMMIT"),
        ("A-policy-expiry",
         dict(policy_over={"expires_at": iso(NOW + 10)}),
         dict(grant_over={"expires_at": iso(NOW + 1000)}), NOW + 11,
         "POLICY_EXPIRED_AT_COMMIT"),
        ("A-grant-expiry",
         dict(policy_over={"expires_at": iso(NOW + 1000)}),
         dict(grant_over={"expires_at": iso(NOW + 10)}), NOW + 11,
         "GRANT_EXPIRED_AT_COMMIT"),
        ("A-backward-before-window",
         dict(policy_over={"valid_from": iso(NOW - 100)}),
         dict(grant_over={"valid_from": iso(NOW - 100)}), NOW - 200,
         "POLICY_DENIED"),
    ]
    for name, pkw, gkw, jump, expected in cases:
        with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
            clock = FixedClock(NOW)
            p = world(td, clock=clock, **pkw, **gkw)
            code, detail = code_of(
                lambda: p.acquire(req(), hold=lambda: clock.set(jump)))
            actual = {"reason": code, "lease": p.lease(RES),
                      "commits": p.commit_count(),
                      "token_next": p.token_next(RES)}
            ok = (code == expected and actual["lease"] is None
                  and actual["commits"] == 0
                  and actual["token_next"] is None)
            record(f"control-{name}", "LOCAL_REAL_SQLITE",
                   {"reason": expected, "lease": None, "commits": 0,
                    "token_next": None}, actual, ok=ok)

    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        clock = FixedClock(NOW)
        p = world(td, clock=clock)
        code, detail = code_of(
            lambda: p.acquire(req(), hold=lambda: clock.set(NOW + 5)))
        record("control-A-within-window", "LOCAL_REAL_SQLITE",
               {"reason": "ACCEPTED", "token": 1, "commits": 1},
               {"reason": code, "lease": p.lease(RES),
                "commits": p.commit_count()},
               ok=(code == "ACCEPTED" and p.lease(RES) is not None
                   and p.commit_count() == 1))

    # B. Historical replay and epoch pins.
    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        first = p.acquire(req())
        replay = p.acquire(req())
        ok = (first["prototype_status"] == "PROTOTYPE_FENCED_COMMIT"
              and first["authorized"] is True
              and first["historical"] is False
              and first["effect_eligible"] is False
              and "verified" not in first
              and replay["prototype_status"] == "PROTOTYPE_HISTORICAL_REPLAY"
              and replay["authorized"] is False
              and replay["historical"] is True
              and replay["effect_eligible"] is False
              and replay["token"] == first["token"]
              and p.commit_count() == 1 and p.token_next(RES) == 2)
        record("control-B-historical-replay", "LOCAL_REAL_SQLITE",
               {"first": "FENCED", "replay": "HISTORICAL",
                "authorized": False, "historical": True,
                "effect_eligible": False, "token_same": True},
               {"first": first["prototype_status"],
                "replay": replay["prototype_status"],
                "authorized": replay["authorized"],
                "historical": replay["historical"],
                "effect_eligible": replay["effect_eligible"],
                "token_same": replay["token"] == first["token"],
                "commits": p.commit_count(),
                "token_next": p.token_next(RES)}, ok=ok)
        for label, overrides in (
                ("authority", {"expected_authority_epoch": 1}),
                ("grant", {"expected_grant_epoch": 1})):
            code, detail = code_of(lambda o=overrides: p.acquire(req(**o)))
            record(f"control-B-different-{label}-pin", "LOCAL_REAL_SQLITE",
                   {"reason": "IDEMPOTENCY_CONFLICT", "commits": 1},
                   {"reason": code, "commits": p.commit_count()},
                   ok=(code == "IDEMPOTENCY_CONFLICT"
                       and p.commit_count() == 1))

    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        first = p.acquire(req())
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        replay = p.acquire(req())
        code, _ = code_of(lambda: p.acquire(req("req:2")))
        record("control-B-replay-after-revoke", "LOCAL_REAL_SQLITE",
               {"replay": "HISTORICAL", "authorized": False,
                "new_request": "POLICY_DENIED", "commits": 1},
               {"replay": replay["prototype_status"],
                "authorized": replay["authorized"],
                "new_request": code, "commits": p.commit_count()},
               ok=(replay["prototype_status"] == "PROTOTYPE_HISTORICAL_REPLAY"
                   and replay["authorized"] is False
                   and code == "POLICY_DENIED"
                   and p.commit_count() == 1))

    # C. Processes: blocked revoker, contention, crash, commit-exit.
    if CTXMP is None:
        record("control-C-multiprocess", "NOT_RUN",
               {"fork": True}, {"fork": False}, ok=True,
               note="MULTIPROCESS_FORK_NOT_AVAILABLE")
    else:
        with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
            p = world(td)
            written, release = CTXMP.Event(), CTXMP.Event()
            attempted, blocked = CTXMP.Event(), CTXMP.Event()
            aq, rq = CTXMP.Queue(), CTXMP.Queue()
            a = CTXMP.Process(target=_hold_worker,
                              args=(str(p.state_dir), req(), written, release, aq))
            r = CTXMP.Process(target=_revoke_probe,
                              args=(str(p.state_dir), attempted, blocked, release, rq))
            a.start()
            written.wait(15)
            r.start()
            attempted.wait(5)
            blocked.wait(5)
            blocked_msg = rq.get(timeout=10)
            release.set()
            a.join(20); r.join(20)
            am = aq.get(timeout=10)
            rm = rq.get(timeout=10)
            lease = p.lease(RES)
            commit = p.commit("req:1")
            policy = p.policy(POLICY_REF)
            new_code, _ = code_of(lambda: p.acquire(req("req:2")))
            ok = (blocked_msg[0] == "blocked"
                  and blocked_msg[1] and "database is locked" in blocked_msg[1]
                  and am[0] == "ok" and rm == ("ok", 2, 2)
                  and lease is not None and int(lease["token"]) == 1
                  and commit["policy_epoch"] == 1
                  and policy["policy_epoch"] == 2 and policy["revoked"] == 1
                  and new_code == "POLICY_DENIED" and p.commit_count() == 1)
            record("control-C-blocked-revoker", "LOCAL_REAL_SQLITE",
                   {"blocked": "database is locked", "acquire": 1,
                    "revoke": [2, 2], "new_request": "POLICY_DENIED",
                    "commits": 1},
                   {"blocked": blocked_msg[1], "acquire": am, "revoke": rm,
                    "lease": lease, "new_request": new_code,
                    "commits": p.commit_count()}, ok=ok)

        with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
            p = world(td)
            barrier = CTXMP.Barrier(2)
            q1, q2 = CTXMP.Queue(), CTXMP.Queue()
            p1 = CTXMP.Process(target=_acquire_worker,
                               args=(str(p.state_dir), req("req:a"), barrier, q1))
            p2 = CTXMP.Process(target=_acquire_worker,
                               args=(str(p.state_dir), req("req:b"), barrier, q2))
            p1.start(); p2.start()
            m1 = q1.get(timeout=20); m2 = q2.get(timeout=20)
            p1.join(20); p2.join(20)
            oks = [m for m in (m1, m2) if m[0] == "ok"]
            refs = [m for m in (m1, m2) if m[0] == "refused"]
            conn = sqlite3.connect(p.db_path)
            wal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            integ = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            conn.close()
            lease = p.lease(RES)
            ok = (len(oks) == 1 and len(refs) == 1
                  and refs[0][1] == "LEASE_HELD_BY_OTHER"
                  and lease is not None and int(lease["token"]) == oks[0][1]
                  and p.commit_count() == 1 and p.token_next(RES) == 2
                  and wal == "wal" and integ == "ok")
            record("control-C-contention-one-token", "LOCAL_REAL_SQLITE",
                   {"one_ok": 1, "one_refused": 1, "token": 1, "commits": 1,
                    "wal": "wal", "integrity": "ok"},
                   {"messages": [m1, m2], "lease": lease,
                    "commits": p.commit_count(),
                    "token_next": p.token_next(RES), "wal": wal,
                    "integrity": integ}, ok=ok)

        with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
            p = world(td)
            proc = CTXMP.Process(target=_crash_worker,
                                 args=(str(p.state_dir), req("req:crash")))
            proc.start(); proc.join(20)
            y = proto.AtomicFencePrototype(
                str(p.state_dir), initialize=False, clock=FixedClock(NOW),
                synthetic_peer_uid=UID)
            conn = sqlite3.connect(p.db_path)
            integ = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            conn.close()
            record("control-C-crash-before-commit", "LOCAL_REAL_SQLITE",
                   {"exit": 17, "lease": None, "commits": 0, "token": None,
                    "integrity": "ok"},
                   {"exit": proc.exitcode, "lease": y.lease(RES),
                    "commits": y.commit_count(),
                    "token": y.token_next(RES), "integrity": integ},
                   ok=(proc.exitcode == 17 and y.lease(RES) is None
                       and y.commit_count() == 0
                       and y.token_next(RES) is None and integ == "ok"))

        with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
            p = world(td)
            q = CTXMP.Queue()
            proc = CTXMP.Process(target=_commit_exit_worker,
                                 args=(str(p.state_dir), req("req:ok"), q))
            proc.start(); proc.join(20)
            y = proto.AtomicFencePrototype(
                str(p.state_dir), initialize=False, clock=FixedClock(NOW),
                synthetic_peer_uid=UID)
            lease = y.lease(RES)
            replay = y.acquire(req("req:ok"))
            conn = sqlite3.connect(p.db_path)
            wal = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            integ = str(conn.execute("PRAGMA integrity_check").fetchone()[0])
            conn.close()
            record("control-C-commit-exit-reopen", "LOCAL_REAL_SQLITE",
                   {"exit": 23, "token": 1, "commits": 1,
                    "replay": "HISTORICAL", "wal": "wal", "integrity": "ok"},
                   {"exit": proc.exitcode, "lease": lease,
                    "commits": y.commit_count(), "token_next": y.token_next(RES),
                    "replay": replay["prototype_status"],
                    "wal": wal, "integrity": integ},
                   ok=(proc.exitcode == 23 and lease is not None
                       and int(lease["token"]) == 1 and y.commit_count() == 1
                       and y.token_next(RES) == 2
                       and replay["prototype_status"] == "PROTOTYPE_HISTORICAL_REPLAY"
                       and wal == "wal" and integ == "ok"))

    # D. Fenced writer check: valid, revoke, expired grant.
    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        receipt = p.acquire(req())
        checked = p.check_writer_fenced(RES, owner=ACTOR,
                                        token=receipt["token"])
        record("control-D-fenced-valid", "LOCAL_REAL_SQLITE",
               {"status": "PROTOTYPE_FENCED_CHECK", "authorized": True,
                "effect_eligible": False},
               checked, ok=(checked["prototype_status"] == "PROTOTYPE_FENCED_CHECK"
                            and checked["authorized"] is True
                            and checked["effect_eligible"] is False))
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        fenced, _ = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        store = proto.ServerOSStore(str(p.state_dir))
        legacy = store.check_writer(RES, owner=ACTOR,
                                    token=receipt["token"], now=NOW)
        record("control-D-after-revoke", "LOCAL_REAL_SQLITE",
               {"fenced": "POLICY_DENIED", "legacy_authorized": True,
                "known_production_gap": True},
               {"fenced": fenced,
                "legacy_authorized": legacy["authorized"]},
               ok=(fenced == "POLICY_DENIED"
                   and legacy["authorized"] is True))

    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        clock = FixedClock(NOW)
        p = world(td, clock=clock,
                  policy_over={"expires_at": iso(NOW + 1000)},
                  grant_over={"expires_at": iso(NOW + 10)})
        receipt = p.acquire(req(ttl_s=600.0))
        clock.set(NOW + 11)
        fenced, _ = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        store = proto.ServerOSStore(str(p.state_dir))
        legacy = store.check_writer(RES, owner=ACTOR,
                                    token=receipt["token"], now=NOW + 11)
        record("control-D-after-grant-expiry", "LOCAL_REAL_SQLITE",
               {"fenced": "GRANT_EXPIRED_AT_COMMIT",
                "legacy_authorized": True},
               {"fenced": fenced,
                "legacy_authorized": legacy["authorized"]},
               ok=(fenced == "GRANT_EXPIRED_AT_COMMIT"
                   and legacy["authorized"] is True))

    # E. Constructor-bound clock/peer and request injection.
    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        results = []
        for label, kwargs in (("now", {"now": NOW}), ("peer_uid", {"peer_uid": UID})):
            code, _ = code_of(lambda k=kwargs: p.acquire(req(), **k))
            results.append((label, code))
        record("control-E-per-call-now-peer", "LOCAL_REAL_SQLITE",
               {"now": "TypeError", "peer_uid": "TypeError", "lease": None},
               {"results": results, "lease": p.lease(RES),
                "commits": p.commit_count()},
               ok=(all(code == "TypeError" for _, code in results)
                   and p.lease(RES) is None and p.commit_count() == 0))

    for label, peer in (("missing", None), ("bool", True)):
        with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
            p = world(td, peer=peer)
            code, _ = code_of(lambda: p.acquire(req()))
            record(f"control-E-{label}-peer", "LOCAL_REAL_SQLITE",
                   {"reason": "UNTRUSTED_ACTOR", "lease": None, "commits": 0},
                   {"reason": code, "lease": p.lease(RES),
                    "commits": p.commit_count()},
                   ok=(code == "UNTRUSTED_ACTOR" and p.lease(RES) is None
                       and p.commit_count() == 0))

    for field, value in (("uid", UID), ("actor_ref", ACTOR),
                         ("verified", True), ("authority", "owner"),
                         ("COMMIT_FENCED", True), ("now", NOW)):
        with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
            p = world(td)
            code, _ = code_of(lambda f=field, v=value: p.acquire(req(**{f: v})))
            record(f"control-E-request-{field}", "LOCAL_REAL_SQLITE",
                   {"reason": "INVALID_REQUEST", "lease": None, "commits": 0},
                   {"reason": code, "lease": p.lease(RES),
                    "commits": p.commit_count()},
                   ok=(code == "INVALID_REQUEST" and p.lease(RES) is None
                       and p.commit_count() == 0))

    # F. README production flags, no production import, no verified.
    readme = (SNAP / "integrations" / "fleet-v1-atomic-prototype" /
              "README.md").read_text(encoding="utf-8")
    normalized = " ".join(readme.split())
    flags = ("NO_TRUSTED_CLOCK", "NO_TRUSTED_PEER", "SO_PEERCRED_NOT_BOUND",
             "PRODUCTION_WIRING_REQUIRED", "REAL_FINALIZE_SAME_TXN",
             "POWER_LOSS")
    missing = [flag for flag in flags if flag not in normalized]
    record("control-F-readme-flags", "CODE", {"missing": []},
           {"missing": missing}, ok=not missing)
    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        receipt = p.acquire(req())
        record("control-F-no-production-positive", "LOCAL_REAL_SQLITE",
               {"verified_key": False, "effect_eligible": False},
               {"verified_key": "verified" in receipt,
                "effect_eligible": receipt["effect_eligible"]},
               ok=("verified" not in receipt
                   and receipt["effect_eligible"] is False))
    hits = []
    for sub in ("runtime", "workengine", "mcp", "fleet_mcp", "intake"):
        root = SNAP / sub
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if ("atomic_fence" in text or "AtomicFencePrototype" in text):
                hits.append(str(path.relative_to(SNAP)))
    record("control-F-no-production-import", "CODE", {"hits": []},
           {"hits": hits}, ok=not hits)
    source = (SNAP / "integrations" / "fleet-v1-atomic-prototype" /
              "atomic_fence.py").read_text(encoding="utf-8")
    composed = "def finalize" in source or "finalize_run_verified(" in source
    record("control-F-finalize-not-proven", "NOT_RUN",
           {"REAL_FINALIZE_SAME_TXN": "NOT_PROVEN"},
           {"README": "REAL_FINALIZE_SAME_TXN" in normalized,
            "finalize_composed": composed},
           ok=True, note="finalize/effect opens its own transaction; this "
                         "prototype does not compose it")


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

def findings():
    # P1: check_writer_fenced compares commit policy/authority epochs but not
    # commit.grant_epoch against the current grant.
    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        receipt = p.acquire(req())
        conn = sqlite3.connect(p.db_path)
        conn.execute("UPDATE proto_grant SET grant_epoch=2 WHERE grant_ref=?",
                     (GRANT_REF,))
        conn.commit(); conn.close()
        code, detail = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        commit = p.commit("req:1")
        grant = p.grant(GRANT_REF)
        status = detail.get("prototype_status") if isinstance(detail, dict) else None
        record("f1-fenced-check-misses-grant-epoch", "LOCAL_REAL_SQLITE",
               {"fenced": "BLOCKED_STALE_EPOCH",
                "commit_grant_epoch": 1, "current_grant_epoch": 2},
               {"fenced": code, "fenced_status": status,
                "commit_grant_epoch": commit["grant_epoch"],
                "current_grant_epoch": grant["grant_epoch"]},
               ok=(code == "BLOCKED_STALE_EPOCH"),
               severity="P1",
               note="check_writer_fenced compares commit.policy_epoch and "
                    "commit.authority_epoch with the current policy, but not "
                    "commit.grant_epoch with the current grant.grant_epoch; a "
                    "pre-existing token remains authorized after the grant "
                    "generation moves. Add the grant-epoch comparison and a "
                    "regression test.")

    # P0 production-wiring barrier: legacy acquire_lease writes after revoke.
    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        store = proto.ServerOSStore(str(p.state_dir))
        lease = store.acquire_lease(RES, owner="human:LEGACY", ttl_s=30,
                                    now=NOW)
        record("f2-legacy-acquire-bypasses-fence", "LOCAL_REAL_SQLITE",
               {"post_revoke_lease": None, "proto_commits": 0,
                "disclosed": True},
               {"legacy_lease": lease, "post_revoke_lease": p.lease(RES),
                "proto_commits": p.commit_count(),
                "policy_revoked": p.policy(POLICY_REF)["revoked"],
                "disclosed": "PRODUCTION_WIRING_REQUIRED" in
                " ".join((SNAP / "integrations" /
                          "fleet-v1-atomic-prototype" / "README.md")
                         .read_text(encoding="utf-8").split())},
               ok=(p.lease(RES) is None), severity="P0",
               note="legacy ServerOSStore.acquire_lease still writes a new "
                    "lease after revoke outside the fence; README marks it "
                    "PRODUCTION_WIRING_REQUIRED; the PR does not claim it fixed")

    # P0/P1 production-wiring barrier: legacy check_writer accepts old token;
    # the fenced check refuses it.
    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        receipt = p.acquire(req())
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
        store = proto.ServerOSStore(str(p.state_dir))
        legacy = store.check_writer(RES, owner=ACTOR,
                                    token=receipt["token"], now=NOW)
        fenced, _ = code_of(lambda: p.check_writer_fenced(
            RES, owner=ACTOR, token=receipt["token"]))
        record("f3-legacy-check-writer-accepts-old-token",
               "LOCAL_REAL_SQLITE",
               {"legacy_authorized": False, "fenced": "POLICY_DENIED"},
               {"legacy_authorized": legacy["authorized"],
                "fenced": fenced}, ok=(not legacy["authorized"]),
               severity="P0",
               note="legacy ServerOSStore.check_writer reads only the lease "
                    "row and accepts the pre-revoke token; the new fenced check "
                    "refuses it. README marks the legacy path "
                    "PRODUCTION_WIRING_REQUIRED; production must use the fenced "
                    "check across the effect.")

    # P2: fenced check has no peer/context binding; owner/token are caller
    # inputs. It is a token/authority check, not a caller authentication.
    with tempfile.TemporaryDirectory(prefix="qa2-376-") as td:
        p = world(td)
        receipt = p.acquire(req())
        checked = p.check_writer_fenced(RES, owner=ACTOR,
                                        token=receipt["token"])
        record("f4-fenced-check-not-peer-context-bound", "LOCAL_REAL_SQLITE",
               {"peer_bound": True, "context_read": True},
               {"peer_bound": False, "context_read": False,
                "authorized": checked["authorized"]},
               ok=False, severity="P2",
               note="check_writer_fenced takes owner/token as caller strings; "
                    "it does not read the constructor synthetic peer or the "
                    "current context. README flags NO_TRUSTED_PEER / "
                    "SO_PEERCRED_NOT_BOUND; production must bind the peer and "
                    "hold the fence across the effect.")


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
