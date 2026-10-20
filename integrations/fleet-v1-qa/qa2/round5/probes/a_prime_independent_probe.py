#!/usr/bin/env python3
"""QA2 independent witnesses for A' prototype #375 @ 0c081f7.

Read-only against an exported exact head. It uses the prototype API and the
real WorkEngine SQLite schema; owner policy/grant/context/uid are synthetic
fixtures. It does NOT modify the prototype, author PR, WorkEngine, daemon or
any production file.

Modes:
  controls  fail-closed prototype properties; rc 0 iff all controls hold.
  findings  production-wiring / contract witnesses; rc 1 iff a gap is
            confirmed. A P0 finding is a production-wiring barrier, not a
            claim that the prototype itself is production.

Evidence: LOCAL + REAL_TEMP SQLite; REAL_WINDOWS and two real OS uids NOT_RUN.
No owner key, OS grant, provider, OAuth, credential or production service.
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
import time
from pathlib import Path

SNAP = Path(os.environ.get(
    "A_PRIME_SNAPSHOT",
    "/data/home/vince/artifacts/skills/qa2-independent-security/"
    "2026-10-21/checkouts/a-prime-375-0c081f7")).resolve()
sys.path.insert(0, str(SNAP))


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


proto = _load("qa2_atomic_fence", SNAP / "integrations" /
              "fleet-v1-atomic-prototype" / "atomic_fence.py")

NOW = 2_000_000_000.0
UID = 4242
ACTOR = "human:A"
POLICY_REF = "policy:proto"
GRANT_REF = "grant:proto"
CONTEXT_REF = "ctx:proto"
PROJECT = "serveros"
ITEM = "SRV-0001"
ACTION = "lease.acquire"
RESOURCE = "work-write:SRV-0001"
VALID_FROM = "2000-01-01T00:00:00Z"
EXPIRES_AT = "2999-01-01T00:00:00Z"
ISSUED_AT = "2026-01-01T00:00:00Z"

POLICY = dict(policy_ref=POLICY_REF, os_principal=f"uid:{UID}",
              actor_ref=ACTOR, projects=[PROJECT], items=[ITEM],
              actions=[ACTION], valid_from=VALID_FROM, expires_at=EXPIRES_AT,
              issued_at=ISSUED_AT)
GRANT = dict(grant_ref=GRANT_REF, policy_ref=POLICY_REF,
             os_principal=f"uid:{UID}", actor_ref=ACTOR,
             projects=[PROJECT], items=[ITEM], actions=[ACTION],
             valid_from=VALID_FROM, expires_at=EXPIRES_AT,
             owner_policy_epoch=1)
CONTEXT = dict(context_ref=CONTEXT_REF, actor_ref=ACTOR, grant_ref=GRANT_REF)

CTX = multiprocessing.get_context("fork") if hasattr(os, "fork") else None
RESULTS = []


def record(name, level, expected, actual, *, ok, severity=None, note=""):
    RESULTS.append({"name": name, "level": level, "expected": expected,
                    "actual": actual, "ok": bool(ok), "severity": severity,
                    "note": note})
    print(json.dumps(RESULTS[-1], ensure_ascii=False, sort_keys=True))


def _world(root, *, policy_over=None, grant_over=None, context_over=None,
           seed_policy=True, seed_grant=True, seed_context=True):
    p = proto.AtomicFencePrototype(str(Path(root) / "proto"))
    if seed_policy:
        values = dict(POLICY)
        values.update(policy_over or {})
        p.seed_policy(**values)
    if seed_grant:
        values = dict(GRANT)
        values.update(grant_over or {})
        p.seed_grant(**values)
    if seed_context:
        values = dict(CONTEXT)
        values.update(context_over or {})
        p.seed_context(**values)
    return p


def _req(request_id="req:1", **overrides):
    request = {"request_id": request_id, "context_ref": CONTEXT_REF,
               "project": PROJECT, "item_ref": ITEM, "action": ACTION,
               "ttl_s": 30.0}
    request.update(overrides)
    return request


def _integrity(db_path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return str(conn.execute("PRAGMA integrity_check").fetchone()[0])
    finally:
        conn.close()


def _journal_mode(db_path) -> str:
    conn = sqlite3.connect(db_path)
    try:
        return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    finally:
        conn.close()


def _refused(p, expected, request=None, peer_uid=UID, now=NOW):
    try:
        p.acquire(request or _req(), peer_uid=peer_uid, now=now)
        actual = "ACCEPTED"
    except proto.PrototypeRefusal as exc:
        actual = exc.code
    except BaseException as exc:  # noqa: BLE001 - a crash is not a refusal
        actual = f"{type(exc).__name__}: {exc}"
    lease = p.lease(RESOURCE)
    commits = p.commit_count()
    ok = actual == expected and lease is None and commits == 0
    return ok, {"reason": actual, "lease": lease, "commits": commits}


# ---------------------------------------------------------------------------
# multiprocessing workers (fork; each opens its own SQLite connection)
# ---------------------------------------------------------------------------

def _acquire_worker(db_dir, request, peer_uid, now, barrier, queue):
    p = proto.AtomicFencePrototype(db_dir, initialize=False)
    try:
        barrier.wait(timeout=15)
        receipt = p.acquire(request, peer_uid=peer_uid, now=now)
        queue.put(("ok", int(receipt["token"]), str(receipt["request_id"])))
    except BaseException as exc:  # noqa: BLE001
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _revoke_worker(db_dir, policy_ref, expected, now, barrier, queue):
    p = proto.AtomicFencePrototype(db_dir, initialize=False)
    try:
        barrier.wait(timeout=15)
        receipt = p.revoke_policy(policy_ref, expected_policy_epoch=expected,
                                  now=now)
        queue.put(("ok", int(receipt["policy_epoch"]),
                   int(receipt["authority_epoch"])))
    except BaseException as exc:  # noqa: BLE001
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _crash_worker(db_dir, request, peer_uid, now):
    p = proto.AtomicFencePrototype(db_dir, initialize=False)
    p.acquire(request, peer_uid=peer_uid, now=now,
              hold=lambda: os._exit(17))


def _spawn(target, args, queue, barrier_count=1):
    barrier = CTX.Barrier(barrier_count)
    proc = CTX.Process(target=target, args=(*args, barrier, queue))
    proc.start()
    return proc


def _finish(proc, timeout=20):
    proc.join(timeout)
    if proc.is_alive():
        proc.terminate()
        proc.join(5)
        return False
    return True


# ---------------------------------------------------------------------------
# Controls
# ---------------------------------------------------------------------------

def controls():
    if CTX is None:
        record("multiprocess-available", "NOT_RUN", {"fork": True},
               {"fork": False}, ok=True,
               note="MULTIPROCESS_FORK_NOT_AVAILABLE: Linux-only evidence")
        return

    # 1. revoke commits first, then a separate process acquires: no lease.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1, now=NOW)
        q = CTX.Queue()
        proc = _spawn(_acquire_worker, (str(p.state_dir), _req(), UID, NOW), q)
        msg = q.get(timeout=20)
        alive = _finish(proc)
        ok = (alive and msg == ("refused", "POLICY_DENIED")
              and p.lease(RESOURCE) is None and p.commit_count() == 0)
        record("revoke-first-then-acquire-no-lease", "REAL_TEMP",
               {"acquire": "refused POLICY_DENIED", "lease": None,
                "commits": 0},
               {"msg": msg, "lease": p.lease(RESOURCE),
                "commits": p.commit_count()}, ok=ok)

    # 2. acquire commits first, then revoke; the historical token remains and a
    #    new request is refused.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        first = p.acquire(_req(), peer_uid=UID, now=NOW)
        q = CTX.Queue()
        proc = _spawn(_revoke_worker, (str(p.state_dir), POLICY_REF, 1, NOW), q)
        msg = q.get(timeout=20)
        alive = _finish(proc)
        try:
            p.acquire(_req("req:2"), peer_uid=UID, now=NOW)
            second = "ACCEPTED"
        except proto.PrototypeRefusal as exc:
            second = exc.code
        lease = p.lease(RESOURCE)
        ok = (alive and msg == ("ok", 2, 2)
              and first["token"] == 1 and lease is not None
              and int(lease["token"]) == 1 and p.commit_count() == 1
              and second == "POLICY_DENIED")
        record("acquire-first-revoke-keeps-history-new-request-refused",
               "REAL_TEMP",
               {"revoke": [2, 2], "token": 1, "new_request": "POLICY_DENIED",
                "commits": 1},
               {"revoke": msg, "lease": lease, "new_request": second,
                "commits": p.commit_count()}, ok=ok)

    # 3. Barrier race: acquire vs revoke, no sleep.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        qa, qr = CTX.Queue(), CTX.Queue()
        barrier = CTX.Barrier(2)
        pa = CTX.Process(target=_acquire_worker,
                         args=(str(p.state_dir), _req("req:race"), UID, NOW,
                               barrier, qa))
        pr = CTX.Process(target=_revoke_worker,
                         args=(str(p.state_dir), POLICY_REF, 1, NOW,
                               barrier, qr))
        pa.start(); pr.start()
        ma = qa.get(timeout=20); mr = qr.get(timeout=20)
        alive = _finish(pa) and _finish(pr)
        lease = p.lease(RESOURCE)
        commits = p.commit_count()
        if ma[0] == "ok":
            try:
                p.acquire(_req("req:after"), peer_uid=UID, now=NOW)
                after = "ACCEPTED"
            except proto.PrototypeRefusal as exc:
                after = exc.code
            ok = (alive and ma[1] == 1 and mr == ("ok", 2, 2)
                  and lease is not None and int(lease["token"]) == 1
                  and commits == 1 and after == "POLICY_DENIED")
        else:
            ok = (alive and ma == ("refused", "POLICY_DENIED")
                  and mr == ("ok", 2, 2) and lease is None and commits == 0)
        record("barrier-race-acquire-vs-revoke", "REAL_TEMP",
               {"serialized": True, "no_new_lease_after_revoke": True},
               {"acquire": ma, "revoke": mr, "lease": lease,
                "commits": commits}, ok=ok)

    # 4. Two processes contend for one resource: one token, one commit.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        q1, q2 = CTX.Queue(), CTX.Queue()
        barrier = CTX.Barrier(2)
        p1 = CTX.Process(target=_acquire_worker,
                         args=(str(p.state_dir), _req("req:a"), UID, NOW,
                               barrier, q1))
        p2 = CTX.Process(target=_acquire_worker,
                         args=(str(p.state_dir), _req("req:b"), UID, NOW,
                               barrier, q2))
        p1.start(); p2.start()
        m1 = q1.get(timeout=20); m2 = q2.get(timeout=20)
        alive = _finish(p1) and _finish(p2)
        ok_msgs = [m for m in (m1, m2) if m[0] == "ok"]
        ref_msgs = [m for m in (m1, m2) if m[0] == "refused"]
        lease = p.lease(RESOURCE)
        conn = p._connect()
        try:
            busy = int(conn.execute("PRAGMA busy_timeout").fetchone()[0])
        finally:
            conn.close()
        integrity = _integrity(p.db_path)
        mode = _journal_mode(p.db_path)
        try:
            p.acquire(_req("req:c"), peer_uid=UID, now=NOW)
            third = "ACCEPTED"
        except proto.PrototypeRefusal as exc:
            third = exc.code
        ok = (alive and len(ok_msgs) == 1 and len(ref_msgs) == 1
              and ref_msgs[0][1] == "LEASE_HELD_BY_OTHER"
              and lease is not None and int(lease["token"]) == ok_msgs[0][1]
              and p.commit_count() == 1 and p.token_next(RESOURCE) == 2
              and mode == "wal" and busy >= 30000 and integrity == "ok"
              and third == "LEASE_HELD_BY_OTHER")
        record("two-process-contention-one-token", "REAL_TEMP",
               {"one_ok": 1, "one_refused": 1, "token": 1, "commits": 1,
                "wal": True, "busy_timeout": 30000, "integrity": "ok",
                "third": "LEASE_HELD_BY_OTHER"},
               {"messages": [m1, m2], "lease": lease,
                "commits": p.commit_count(),
                "token_next": p.token_next(RESOURCE), "wal": mode,
                "busy_timeout": busy, "integrity": integrity,
                "third": third}, ok=ok)

    # 5. Crash before COMMIT: rollback; committed state survives restart.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        proc = CTX.Process(target=_crash_worker,
                           args=(str(p.state_dir), _req("req:crash"), UID, NOW))
        proc.start(); proc.join(20)
        exitcode = proc.exitcode
        p2 = proto.AtomicFencePrototype(str(p.state_dir), initialize=False)
        integrity = _integrity(p.db_path)
        ok = (exitcode == 17 and p2.lease(RESOURCE) is None
              and p2.commit_count() == 0 and p2.token_next(RESOURCE) is None
              and integrity == "ok")
        record("crash-before-commit-rolls-back", "REAL_TEMP",
               {"exit": 17, "lease": None, "commits": 0, "token": None,
                "integrity": "ok"},
               {"exit": exitcode, "lease": p2.lease(RESOURCE),
                "commits": p2.commit_count(),
                "token": p2.token_next(RESOURCE), "integrity": integrity},
               ok=ok)

    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        q = CTX.Queue()
        proc = _spawn(_acquire_worker,
                      (str(p.state_dir), _req("req:ok"), UID, NOW), q)
        msg = q.get(timeout=20)
        alive = _finish(proc)
        p2 = proto.AtomicFencePrototype(str(p.state_dir), initialize=False)
        lease = p2.lease(RESOURCE)
        replay = p2.acquire(_req("req:ok"), peer_uid=UID, now=NOW)
        integrity = _integrity(p.db_path)
        ok = (alive and msg[0] == "ok" and lease is not None
              and int(lease["token"]) == 1 and p2.commit_count() == 1
              and p2.token_next(RESOURCE) == 2 and replay["replayed"] is True
              and replay["token"] == 1 and integrity == "ok")
        record("commit-survives-restart-replay-idempotent", "REAL_TEMP",
               {"message": "ok", "token": 1, "commits": 1, "replayed": True,
                "integrity": "ok"},
               {"message": msg, "lease": lease,
                "commits": p2.commit_count(),
                "token_next": p2.token_next(RESOURCE),
                "replay": {"replayed": replay.get("replayed"),
                           "token": replay.get("token")},
                "integrity": integrity}, ok=ok)

    # 6. Invalid/self-asserted request matrix: every refusal leaves no lease.
    cases = [
        ("revoked-policy", "POLICY_DENIED",
         dict(policy_over={"revoked": 1}), {}),
        ("expired-policy", "POLICY_DENIED",
         dict(policy_over={"issued_at": "1999-01-01T00:00:00Z",
                           "expires_at": "2001-01-01T00:00:00Z"}), {}),
        ("future-issued-policy", "BLOCKED_OWNER_POLICY",
         dict(policy_over={"issued_at": "2999-01-01T00:00:00Z"}), {}),
        ("unreadable-window", "BLOCKED_OWNER_POLICY",
         dict(policy_over={"valid_from": "not-a-time"}), {}),
        ("revoked-grant", "POLICY_DENIED", dict(grant_over={"revoked": 1}), {}),
        ("expired-grant", "POLICY_DENIED",
         dict(grant_over={"expires_at": "2001-01-01T00:00:00Z"}), {}),
        ("stale-owner-policy-epoch", "BLOCKED_STALE_EPOCH",
         dict(grant_over={"owner_policy_epoch": 2}), {}),
        ("stale-authority-epoch", "STALE_AUTHORITY_EPOCH", {},
         {"expected_authority_epoch": 99}),
        ("stale-grant-epoch", "STALE_GRANT_EPOCH",
         dict(grant_over={"grant_epoch": 2}), {"expected_grant_epoch": 1}),
        ("cross-actor", "UNTRUSTED_ACTOR",
         dict(context_over={"actor_ref": "human:B"}), {}),
        ("cross-project", "POLICY_DENIED", {}, {"project": "another"}),
        ("cross-item", "POLICY_DENIED", {}, {"item_ref": "SRV-9999"}),
        ("cross-action", "POLICY_DENIED", {}, {"action": "work.commit"}),
        ("missing-attestation", "BLOCKED_OWNER_POLICY",
         dict(policy_over={"owner_attestation": ""}), {}),
        ("missing-policy", "BLOCKED_OWNER_POLICY", dict(seed_policy=False), {}),
        ("missing-grant", "BLOCKED_OWNER_POLICY", dict(seed_grant=False), {}),
        ("missing-context", "UNTRUSTED_ACTOR", dict(seed_context=False), {}),
        ("nonbool-policy-revoked", "BLOCKED_OWNER_POLICY",
         dict(policy_over={"revoked": 2}), {}),
        ("nonbool-grant-revoked", "BLOCKED_OWNER_POLICY",
         dict(grant_over={"revoked": 2}), {}),
        ("invalid-ttl-zero", "LEASE_TTL_INVALID", {}, {"ttl_s": 0}),
        ("invalid-ttl-negative", "LEASE_TTL_INVALID", {}, {"ttl_s": -1}),
        ("caller-verified", "INVALID_REQUEST", {}, {"verified": True}),
        ("caller-commit-fenced", "INVALID_REQUEST", {},
         {"COMMIT_FENCED": True}),
        ("caller-owner", "INVALID_REQUEST", {}, {"owner": ACTOR}),
        ("caller-uid", "INVALID_REQUEST", {}, {"uid": UID}),
        ("caller-actor", "INVALID_REQUEST", {}, {"actor_ref": ACTOR}),
        ("caller-policy-ref", "INVALID_REQUEST", {}, {"policy_ref": POLICY_REF}),
        ("caller-epoch", "INVALID_REQUEST", {}, {"epoch": 1}),
    ]
    for name, expected, world_kwargs, req_kwargs in cases:
        with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
            p = _world(td, **world_kwargs)
            ok, actual = _refused(p, expected, _req(**req_kwargs))
            record(f"invalid-{name}", "REAL_TEMP",
                   {"reason": expected, "lease": None, "commits": 0},
                   actual, ok=ok)

    # Cross uid (peer is a separate argument, not a request field).
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        ok, actual = _refused(p, "UNTRUSTED_ACTOR", peer_uid=9999)
        record("invalid-cross-uid", "REAL_TEMP",
               {"reason": "UNTRUSTED_ACTOR", "lease": None, "commits": 0},
               actual, ok=ok)

    # Missing request id.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        request = _req()
        del request["request_id"]
        ok, actual = _refused(p, "INVALID_REQUEST", request)
        record("invalid-missing-request-id", "REAL_TEMP",
               {"reason": "INVALID_REQUEST", "lease": None, "commits": 0},
               actual, ok=ok)

    # Same request id + different core facts -> conflict, no new lease/commit.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        first = p.acquire(_req(), peer_uid=UID, now=NOW)
        try:
            p.acquire(_req(ttl_s=31.0), peer_uid=UID, now=NOW)
            actual = "ACCEPTED"
        except proto.PrototypeRefusal as exc:
            actual = exc.code
        ok = (actual == "IDEMPOTENCY_CONFLICT"
              and p.lease(RESOURCE) is not None
              and int(p.lease(RESOURCE)["token"]) == first["token"]
              and p.commit_count() == 1)
        record("same-request-id-different-facts-conflict", "REAL_TEMP",
               {"reason": "IDEMPOTENCY_CONFLICT", "commits": 1},
               {"reason": actual, "lease": p.lease(RESOURCE),
                "commits": p.commit_count()}, ok=ok)

    # The real WorkEngine reader sees the fenced lease; the receipt has no
    # production positive field.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        receipt = p.acquire(_req(), peer_uid=UID, now=NOW)
        store = proto.ServerOSStore(str(p.state_dir))
        live = store.active_lease(RESOURCE, now=NOW)
        checked = store.check_writer(RESOURCE, owner=ACTOR,
                                     token=receipt["token"], now=NOW)
        ok = ("verified" not in receipt
              and receipt.get("prototype_status") == "PROTOTYPE_FENCED_COMMIT"
              and receipt.get("simulated_owner") is True
              and live is not None and live["token"] == receipt["token"]
              and checked.get("authorized") is True)
        record("no-production-positive-and-real-reader", "REAL_TEMP",
               {"verified_key": False,
                "prototype_status": "PROTOTYPE_FENCED_COMMIT",
                "real_reader": True},
               {"verified_key": "verified" in receipt,
                "prototype_status": receipt.get("prototype_status"),
                "simulated_owner": receipt.get("simulated_owner"),
                "live": live, "checked": checked}, ok=ok)

    # No production module imports the prototype.
    needles = ("atomic_fence", "AtomicFencePrototype",
               "fleet-v1-atomic-prototype")
    hits = []
    for sub in ("runtime", "workengine", "mcp", "fleet_mcp", "intake"):
        root = SNAP / sub
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            text = path.read_text(encoding="utf-8", errors="replace")
            if any(n in text for n in needles):
                hits.append(str(path.relative_to(SNAP)))
    record("no-production-import", "CODE", {"hits": []}, {"hits": hits},
           ok=not hits)


# ---------------------------------------------------------------------------
# Findings: production-wiring / contract witnesses
# ---------------------------------------------------------------------------

def findings():
    # F1. Legacy ServerOSStore.acquire_lease writes outside the fence after a
    #     policy revoke. This is a production-wiring P0 barrier, not a claim
    #     that the prototype itself is production.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1, now=NOW)
        store = proto.ServerOSStore(str(p.state_dir))
        legacy = store.acquire_lease(RESOURCE, owner="human:LEGACY",
                                     ttl_s=30, now=NOW)
        lease = p.lease(RESOURCE)
        live = store.active_lease(RESOURCE, now=NOW)
        readme = (SNAP / "integrations" / "fleet-v1-atomic-prototype" /
                  "README.md").read_text(encoding="utf-8")
        normalized = " ".join(readme.split())
        disclosed = ("ServerOSStore.acquire_lease" in normalized
                     and "production default stays blocked" in normalized)
        ok = lease is None  # the safe expectation; actual writes
        record("f1-legacy-acquire-lease-bypasses-fence", "REAL_TEMP",
               {"post_revoke_lease": None, "commits": 0,
                "disclosed": True},
               {"legacy_write": legacy, "post_revoke_lease": lease,
                "live": live, "proto_commits": p.commit_count(),
                "policy_revoked": p.policy(POLICY_REF)["revoked"],
                "disclosed": disclosed},
               ok=ok, severity="P0",
               note="legacy WorkEngine acquire_lease can write a new lease "
                    "after revoke outside the fence; README discloses the "
                    "legacy bypass; production must route every mutation "
                    "through one fenced entrypoint")

    # F2. An acquire that committed before the revoke leaves a lease token
    #     that the real WorkEngine active_lease/check_writer still accepts
    #     after the policy revoke. The token is historical, but a consumer
    #     using check_writer alone is not fenced by the authority epoch.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        first = p.acquire(_req(), peer_uid=UID, now=NOW)
        p.revoke_policy(POLICY_REF, expected_policy_epoch=1, now=NOW)
        store = proto.ServerOSStore(str(p.state_dir))
        live = store.active_lease(RESOURCE, now=NOW + 1)
        checked = store.check_writer(RESOURCE, owner=ACTOR,
                                     token=first["token"], now=NOW + 1)
        ok = live is None and not checked.get("authorized")
        record("f2-pre-revoke-token-still-accepted-by-check-writer",
               "REAL_TEMP",
               {"active_lease_after_revoke": None,
                "check_writer_authorized": False},
               {"active_lease_after_revoke": live,
                "check_writer_authorized": checked.get("authorized"),
                "policy_revoked": p.policy(POLICY_REF)["revoked"],
                "commit_epoch": p.commit(_req()["request_id"])["policy_epoch"]},
               ok=ok, severity="P1",
               note="revoke policy does not invalidate the existing lease "
                    "row; ServerOSStore.active_lease/check_writer still accept "
                    "the pre-revoke token. The commit row records epoch 1, but "
                    "the existing WorkEngine writer guard does not read it. A "
                    "production consumer must check the authority epoch or "
                    "revoke existing leases at use time.")

    # F3. The optional expected_* epoch pins are not part of the idempotency
    #     digest; a replay with different pins returns the old receipt rather
    #     than IDEMPOTENCY_CONFLICT.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td)
        first = p.acquire(_req(expected_authority_epoch=1),
                          peer_uid=UID, now=NOW)
        try:
            replay = p.acquire(_req(expected_authority_epoch=2),
                               peer_uid=UID, now=NOW)
            actual = "ACCEPTED"
        except proto.PrototypeRefusal as exc:
            actual = exc.code
            replay = None
        ok = actual == "IDEMPOTENCY_CONFLICT"
        record("f3-expected-epoch-pin-not-in-idempotency-digest", "REAL_TEMP",
               {"reason": "IDEMPOTENCY_CONFLICT"},
               {"reason": actual,
                "replayed": (replay or {}).get("replayed"),
                "token": (replay or {}).get("token"),
                "commits": p.commit_count()},
               ok=ok, severity="P2",
               note="the request digest includes request_id/context/project/"
                    "item/action/ttl/peer_uid, but not expected_authority_epoch"
                    " or expected_grant_epoch; a same-id replay with a "
                    "different pin is accepted as a replay")

    # F4. The verification instant is a caller argument; a caller can pass a
    #     past now and acquire while the policy is expired at the real clock.
    with tempfile.TemporaryDirectory(prefix="qa2-a-prime-") as td:
        p = _world(td, policy_over={
            "issued_at": "1999-01-01T00:00:00Z",
            "valid_from": "2000-01-01T00:00:00Z",
            "expires_at": "2001-01-01T00:00:00Z"},
            grant_over={
                "valid_from": "2000-01-01T00:00:00Z",
                "expires_at": "2001-01-01T00:00:00Z"})
        try:
            receipt = p.acquire(_req("req:past"), peer_uid=UID,
                                now=960_000_000.0)
            actual = "ACCEPTED"
        except proto.PrototypeRefusal as exc:
            actual = exc.code
            receipt = None
        ok = actual != "ACCEPTED"
        record("f4-caller-chosen-now-bypasses-real-expiry", "REAL_TEMP",
               {"at_real_now": "refused"},
               {"at_caller_now": actual,
                "receipt_token": (receipt or {}).get("token"),
                "real_now": time.time()},
               ok=ok, severity="P2",
               note="`now` and `peer_uid` are out-of-band prototype arguments; "
                    "the production entrypoint must supply them from a trusted "
                    "clock and SO_PEERCRED. The prototype does not label a "
                    "trusted-clock/trusted-peer dependency in its flags.")

    # F5. The README's NOT PROVEN list does not mention a trusted clock or
    #     trusted peer, although both are caller-supplied to acquire().
    readme = (SNAP / "integrations" / "fleet-v1-atomic-prototype" /
              "README.md").read_text(encoding="utf-8")
    ok = ("trusted clock" in readme.lower()
          and "SO_PEERCRED" in readme)
    record("f5-readme-trusted-clock-peer-not-explicit", "CODE",
           {"trusted_clock": True, "so_peercred": True},
           {"trusted_clock": "trusted clock" in readme.lower(),
            "so_peercred": "SO_PEERCRED" in readme},
           ok=ok, severity="P2",
           note="README flags NO_OWNER_TRUST/NO_DAEMON_WIRING/two-real-uids, "
                "but the acquire() caller-supplied now/peer_uid are not "
                "explicitly listed as NOT_PROVEN trusted inputs")


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
