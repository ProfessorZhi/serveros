"""A' atomic authority epoch + lease commit prototype (PROTOTYPE_ONLY).

This test runs against a real temp WorkEngine SQLite DB (`ServerOSStore`
schema) and two independent connections/processes. The owner policy, grant,
context and uid are synthetic fixtures; nothing here is a production owner.

The prototype must not be read as production authorization: its success word
is `PROTOTYPE_FENCED_COMMIT`, it carries `simulated_owner: True`, and it has
no `verified` key. Production #364 remains `BLOCKED_OWNER_COMMIT_SEAM`.

EVIDENCE: LOCAL / REAL_TEMP SQLite (temp DB, real schema/transaction/token
helpers); SYNTHETIC owner/uid/context; multi-process Linux only.
NOT_RUN: two real OS uids, a real owner key, production daemon/Fleet wiring,
finalize/effect same-transaction (see README).
"""
from __future__ import annotations

import importlib.util
import multiprocessing
import os
import sqlite3
import sys
import threading
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PROTOTYPE_PATH = REPO / "integrations" / "fleet-v1-atomic-prototype" / "atomic_fence.py"


def _load_prototype():
    name = "s1_atomic_fence_prototype"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PROTOTYPE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


proto = _load_prototype()

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

POLICY = dict(
    policy_ref=POLICY_REF, os_principal=f"uid:{UID}", actor_ref=ACTOR,
    projects=[PROJECT], items=[ITEM], actions=[ACTION],
    valid_from=VALID_FROM, expires_at=EXPIRES_AT, issued_at=ISSUED_AT)
GRANT = dict(
    grant_ref=GRANT_REF, policy_ref=POLICY_REF, os_principal=f"uid:{UID}",
    actor_ref=ACTOR, projects=[PROJECT], items=[ITEM], actions=[ACTION],
    valid_from=VALID_FROM, expires_at=EXPIRES_AT, owner_policy_epoch=1)
CONTEXT = dict(context_ref=CONTEXT_REF, actor_ref=ACTOR, grant_ref=GRANT_REF)


def _request(request_id="req:1", **overrides) -> dict:
    request = {
        "request_id": request_id, "context_ref": CONTEXT_REF,
        "project": PROJECT, "item_ref": ITEM, "action": ACTION, "ttl_s": 30.0,
    }
    request.update(overrides)
    return request


def _make(tmp_path, *, policy_over=None, grant_over=None,
          context_over=None, seed_policy=True, seed_grant=True,
          seed_context=True):
    p = proto.AtomicFencePrototype(str(tmp_path / "proto"))
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


def _refused(p, code, request=None, peer_uid=UID, now=NOW):
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(request or _request(), peer_uid=peer_uid, now=now)
    assert caught.value.code == code, str(caught.value)
    assert p.lease(RESOURCE) is None
    assert p.commit_count() == 0


# ---------------------------------------------------------------------------
# The fenced commit itself.
# ---------------------------------------------------------------------------

def test_a_fenced_commit_writes_policy_epoch_and_lease_in_one_transaction(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request(), peer_uid=UID, now=NOW)

    assert receipt["prototype_status"] == "PROTOTYPE_FENCED_COMMIT"
    assert receipt["simulated_owner"] is True
    assert receipt["owner_status"] == "SIMULATED_OWNER"
    assert "verified" not in receipt
    assert receipt["policy_epoch"] == 1
    assert receipt["authority_epoch"] == 1
    assert receipt["token"] == 1
    lease = p.lease(RESOURCE)
    assert lease == {
        "resource": RESOURCE, "owner": ACTOR, "token": "1",
        "expires_at": receipt["expires_at"],
    }
    commit = p.commit("req:1")
    assert commit["policy_epoch"] == 1
    assert commit["authority_epoch"] == 1
    assert commit["token"] == 1
    assert p.token_next(RESOURCE) == 2


def test_real_work_engine_reader_sees_the_fenced_lease(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request(), peer_uid=UID, now=NOW)
    store = proto.ServerOSStore(str(p.state_dir))
    live = store.active_lease(RESOURCE, now=NOW)
    assert live == {
        "resource": RESOURCE, "owner": ACTOR, "token": receipt["token"],
        "expires_at": receipt["expires_at"],
    }
    checked = store.check_writer(RESOURCE, owner=ACTOR,
                                 token=receipt["token"], now=NOW)
    assert checked["authorized"] is True
    assert checked["token"] == receipt["token"]


def test_revoke_commits_before_acquire_and_leaves_no_lease(tmp_path):
    p = _make(tmp_path)
    revoked = p.revoke_policy(POLICY_REF, expected_policy_epoch=1, now=NOW)
    assert revoked["prototype_status"] == "PROTOTYPE_POLICY_REVOKED"
    assert revoked["owner_status"] == "SIMULATED_OWNER"
    assert revoked["policy_epoch"] == 2
    assert revoked["authority_epoch"] == 2

    _refused(p, "POLICY_DENIED")
    assert p.policy(POLICY_REF)["revoked"] == 1


def test_acquire_before_revoke_is_auditable_and_blocks_a_new_acquire(tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request(), peer_uid=UID, now=NOW)
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1, now=NOW)

    lease = p.lease(RESOURCE)
    assert lease["token"] == str(first["token"])
    commit = p.commit("req:1")
    assert commit["policy_epoch"] == 1
    assert commit["authority_epoch"] == 1
    assert p.commit_count() == 1

    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request("req:2"), peer_uid=UID, now=NOW)
    assert caught.value.code == "POLICY_DENIED"
    assert p.lease(RESOURCE)["token"] == str(first["token"])
    assert p.commit_count() == 1


def test_legacy_cross_domain_read_then_revoke_then_write_is_reproduced(tmp_path):
    """RED baseline: the old verify-read / revoke / lease-write gap.

    This test asserts that the legacy pattern DOES write a lease after the
    revoke. It is a reproduction of the problem the prototype removes, not a
    desired behavior. The prototype test above is the GREEN side.
    """
    p = _make(tmp_path)
    read_done = threading.Event()
    revoke_done = threading.Event()
    outcome = {}

    def legacy_writer():
        conn = sqlite3.connect(p.db_path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT revoked, policy_epoch FROM proto_owner_policy "
            "WHERE policy_ref=?", (POLICY_REF,)).fetchone()
        outcome["snapshot_revoked"] = int(row["revoked"])
        read_done.set()
        assert revoke_done.wait(10)
        # Separate write transaction, using the stale snapshot above.
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO leases(resource,owner,token,expires_at) "
            "VALUES (?,?,?,?)",
            (RESOURCE, ACTOR, 99, proto._lease_stamp(NOW + 30)))
        conn.commit()
        conn.close()
        outcome["wrote"] = True

    thread = threading.Thread(target=legacy_writer)
    thread.start()
    assert read_done.wait(5)
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1, now=NOW)
    revoke_done.set()
    thread.join(10)
    assert not thread.is_alive()
    assert outcome == {"snapshot_revoked": 0, "wrote": True}
    lease = p.lease(RESOURCE)
    assert lease is not None and int(lease["token"]) == 99, (
        "the legacy cross-domain write should reproduce a lease after revoke")


# ---------------------------------------------------------------------------
# Fail-closed matrix.  Every refusal leaves no lease and no commit row.
# ---------------------------------------------------------------------------

def test_revoked_grant_is_refused(tmp_path):
    p = _make(tmp_path, grant_over={"revoked": 1})
    _refused(p, "POLICY_DENIED")


def test_expired_policy_is_refused(tmp_path):
    p = _make(tmp_path, policy_over={
        "issued_at": "1999-01-01T00:00:00Z",
        "expires_at": "2001-01-01T00:00:00Z"})
    _refused(p, "POLICY_DENIED")


def test_future_issued_policy_is_refused(tmp_path):
    p = _make(tmp_path, policy_over={"issued_at": "2999-01-01T00:00:00Z"})
    _refused(p, "BLOCKED_OWNER_POLICY")


def test_unreadable_policy_window_is_refused(tmp_path):
    p = _make(tmp_path, policy_over={"valid_from": "not-a-time"})
    _refused(p, "BLOCKED_OWNER_POLICY")


def test_stale_owner_policy_epoch_is_refused(tmp_path):
    p = _make(tmp_path, grant_over={"owner_policy_epoch": 2})
    _refused(p, "BLOCKED_STALE_EPOCH")


def test_stale_authority_epoch_is_refused(tmp_path):
    p = _make(tmp_path)
    _refused(p, "STALE_AUTHORITY_EPOCH",
             request=_request(expected_authority_epoch=99))


def test_stale_grant_epoch_is_refused(tmp_path):
    p = _make(tmp_path, grant_over={"grant_epoch": 2})
    _refused(p, "STALE_GRANT_EPOCH",
             request=_request(expected_grant_epoch=1))


def test_old_writer_epoch_is_refused_against_a_newer_policy(tmp_path):
    p = _make(tmp_path,
              policy_over={"policy_epoch": 2, "authority_epoch": 2},
              grant_over={"owner_policy_epoch": 2})
    _refused(p, "STALE_AUTHORITY_EPOCH",
             request=_request(expected_authority_epoch=1))


def test_the_real_work_engine_db_is_in_wal_mode(tmp_path):
    p = _make(tmp_path)
    conn = sqlite3.connect(p.db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"


def test_cross_uid_is_refused(tmp_path):
    p = _make(tmp_path)
    _refused(p, "UNTRUSTED_ACTOR", peer_uid=9999)


def test_cross_actor_is_refused(tmp_path):
    p = _make(tmp_path, context_over={"actor_ref": "human:B"})
    _refused(p, "UNTRUSTED_ACTOR")


@pytest.mark.parametrize("overrides", [
    {"project": "another"},
    {"item_ref": "SRV-9999"},
    {"action": "work.commit"},
])
def test_out_of_scope_is_refused(tmp_path, overrides):
    p = _make(tmp_path)
    _refused(p, "POLICY_DENIED", request=_request(**overrides))


def test_missing_owner_attestation_is_refused(tmp_path):
    p = _make(tmp_path, policy_over={"owner_attestation": ""})
    _refused(p, "BLOCKED_OWNER_POLICY")


def test_missing_policy_row_is_refused(tmp_path):
    p = _make(tmp_path, seed_policy=False)
    _refused(p, "BLOCKED_OWNER_POLICY")


def test_missing_grant_row_is_refused(tmp_path):
    p = _make(tmp_path, seed_grant=False)
    _refused(p, "BLOCKED_OWNER_POLICY")


def test_missing_context_row_is_refused(tmp_path):
    p = _make(tmp_path, seed_context=False)
    _refused(p, "UNTRUSTED_ACTOR")


def test_non_boolean_revocation_data_is_blocked(tmp_path):
    p = _make(tmp_path, policy_over={"revoked": 2})
    _refused(p, "BLOCKED_OWNER_POLICY")


def test_invalid_ttl_is_refused(tmp_path):
    p = _make(tmp_path)
    _refused(p, "LEASE_TTL_INVALID", request=_request(ttl_s=0))


def test_caller_self_asserted_authority_fields_are_rejected(tmp_path):
    p = _make(tmp_path)
    for field, value in (
            ("uid", 4242), ("actor_ref", ACTOR), ("owner", ACTOR),
            ("policy_ref", POLICY_REF), ("verified", True), ("epoch", 1)):
        _refused(p, "INVALID_REQUEST",
                 request=_request(**{field: value}))


# ---------------------------------------------------------------------------
# Idempotency / replay / old writer.
# ---------------------------------------------------------------------------

def test_same_request_id_replays_the_committed_token(tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request(), peer_uid=UID, now=NOW)
    again = p.acquire(_request(), peer_uid=UID, now=NOW)
    assert again["replayed"] is True
    assert again["token"] == first["token"]
    assert p.commit_count() == 1
    assert p.token_next(RESOURCE) == 2


def test_same_request_id_with_different_facts_conflicts(tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request(), peer_uid=UID, now=NOW)
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request(ttl_s=31.0), peer_uid=UID, now=NOW)
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert p.lease(RESOURCE)["token"] == str(first["token"])
    assert p.commit_count() == 1


def test_missing_request_id_is_refused(tmp_path):
    p = _make(tmp_path)
    request = _request()
    del request["request_id"]
    _refused(p, "INVALID_REQUEST", request=request)


def test_replay_after_revoke_returns_historical_receipt_but_new_request_refuses(
        tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request(), peer_uid=UID, now=NOW)
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1, now=NOW)
    replay = p.acquire(_request(), peer_uid=UID, now=NOW)
    assert replay["replayed"] is True
    assert replay["token"] == first["token"]
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request("req:2"), peer_uid=UID, now=NOW)
    assert caught.value.code == "POLICY_DENIED"
    assert p.commit_count() == 1


def test_token_is_monotonic_after_expiry_and_old_replay_keeps_its_token(
        tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request(), peer_uid=UID, now=NOW)
    second = p.acquire(_request("req:2"), peer_uid=UID, now=NOW + 3600)
    assert second["token"] == first["token"] + 1
    assert second["token"] > first["token"]
    replay = p.acquire(_request(), peer_uid=UID, now=NOW + 3600)
    assert replay["token"] == first["token"]
    assert p.lease(RESOURCE)["token"] == str(second["token"])
    assert p.commit_count() == 2


# ---------------------------------------------------------------------------
# Multi-process: two independent SQLite connections.
# ---------------------------------------------------------------------------

def _worker_acquire(db_dir, request, peer_uid, now, barrier, queue):
    p = proto.AtomicFencePrototype(db_dir, initialize=False)
    barrier.wait(timeout=15)
    try:
        receipt = p.acquire(request, peer_uid=peer_uid, now=now)
        queue.put(("ok", receipt["token"], receipt["request_id"]))
    except BaseException as exc:
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _worker_acquire_hold(db_dir, request, peer_uid, now, written, release,
                         queue):
    p = proto.AtomicFencePrototype(db_dir, initialize=False)

    def hold():
        written.set()
        assert release.wait(15)

    try:
        receipt = p.acquire(request, peer_uid=peer_uid, now=now, hold=hold)
        queue.put(("ok", receipt["token"]))
    except BaseException as exc:
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _worker_revoke(db_dir, policy_ref, expected, now, go, queue):
    p = proto.AtomicFencePrototype(db_dir, initialize=False)
    assert go.wait(15)
    try:
        result = p.revoke_policy(
            policy_ref, expected_policy_epoch=expected, now=now)
        queue.put(("ok", result["policy_epoch"]))
    except BaseException as exc:
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _worker_crash(db_dir, request, peer_uid, now):
    p = proto.AtomicFencePrototype(db_dir, initialize=False)
    p.acquire(request, peer_uid=peer_uid, now=now,
              hold=lambda: os._exit(17))


def _worker_commit(db_dir, request, peer_uid, now, queue):
    p = proto.AtomicFencePrototype(db_dir, initialize=False)
    try:
        receipt = p.acquire(request, peer_uid=peer_uid, now=now)
        queue.put(("ok", receipt["token"]))
    except BaseException as exc:
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _join(process, timeout=20):
    process.join(timeout)
    assert not process.is_alive(), "worker deadlocked"
    assert process.exitcode == 0, f"worker exit {process.exitcode}"


def test_two_processes_compete_for_one_fenced_lease(tmp_path):
    p = _make(tmp_path)
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_worker_acquire,
                    args=(str(p.state_dir), _request(request_id),
                          UID, NOW, barrier, queue))
        for request_id in ("req:p1", "req:p2")
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        _join(process)

    successes = [r for r in results if r[0] == "ok"]
    refusals = [r for r in results if r[0] == "refused"]
    assert len(successes) == 1, results
    assert len(refusals) == 1, results
    assert refusals[0][1] == "LEASE_HELD_BY_OTHER", results
    lease = p.lease(RESOURCE)
    assert lease is not None
    assert int(lease["token"]) == successes[0][1]
    assert p.commit_count() == 1


def test_acquire_lock_precedes_a_concurrent_revoke(tmp_path):
    p = _make(tmp_path)
    ctx = multiprocessing.get_context("fork")
    written = ctx.Event()
    release = ctx.Event()
    go = ctx.Event()
    acquire_queue = ctx.Queue()
    revoke_queue = ctx.Queue()
    acquirer = ctx.Process(
        target=_worker_acquire_hold,
        args=(str(p.state_dir), _request(), UID, NOW, written, release,
              acquire_queue))
    revoker = ctx.Process(
        target=_worker_revoke,
        args=(str(p.state_dir), POLICY_REF, 1, NOW, go, revoke_queue))
    acquirer.start()
    assert written.wait(15), "acquirer did not reach its held transaction"
    revoker.start()
    go.set()
    release.set()
    _join(acquirer)
    _join(revoker)
    assert acquire_queue.get(timeout=10)[0] == "ok"
    assert revoke_queue.get(timeout=10) == ("ok", 2)

    lease = p.lease(RESOURCE)
    assert lease is not None
    commit = p.commit("req:1")
    assert commit["policy_epoch"] == 1
    assert commit["authority_epoch"] == 1
    policy = p.policy(POLICY_REF)
    assert policy["revoked"] == 1
    assert policy["policy_epoch"] == 2
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request("req:2"), peer_uid=UID, now=NOW)
    assert caught.value.code == "POLICY_DENIED"
    assert p.commit_count() == 1


def test_revoke_first_then_acquire_in_another_process_is_refused(tmp_path):
    p = _make(tmp_path)
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1, now=NOW)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_worker_acquire,
        args=(str(p.state_dir), _request(), UID, NOW,
              ctx.Barrier(1), queue))
    process.start()
    assert queue.get(timeout=15) == ("refused", "POLICY_DENIED")
    _join(process)
    assert p.lease(RESOURCE) is None
    assert p.commit_count() == 0


# ---------------------------------------------------------------------------
# Crash / rollback / restart.
# ---------------------------------------------------------------------------

def test_crash_before_commit_leaves_no_lease_commit_or_token(tmp_path):
    p = _make(tmp_path)
    ctx = multiprocessing.get_context("fork")
    process = ctx.Process(
        target=_worker_crash,
        args=(str(p.state_dir), _request(), UID, NOW))
    process.start()
    process.join(20)
    assert not process.is_alive()
    assert process.exitcode == 17

    reopened = proto.AtomicFencePrototype(str(p.state_dir), initialize=False)
    assert reopened.lease(RESOURCE) is None
    assert reopened.commit("req:1") is None
    assert reopened.commit_count() == 0
    assert reopened.token_next(RESOURCE) is None
    assert reopened.policy(POLICY_REF)["policy_epoch"] == 1


def test_commit_survives_restart_and_replay_is_idempotent(tmp_path):
    p = _make(tmp_path)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_worker_commit,
        args=(str(p.state_dir), _request(), UID, NOW, queue))
    process.start()
    assert queue.get(timeout=15)[0] == "ok"
    _join(process)

    reopened = proto.AtomicFencePrototype(str(p.state_dir), initialize=False)
    lease = reopened.lease(RESOURCE)
    assert lease is not None and lease["token"] == "1"
    assert reopened.commit("req:1")["token"] == 1
    replay = reopened.acquire(_request(), peer_uid=UID, now=NOW)
    assert replay["replayed"] is True and replay["token"] == 1
    assert reopened.token_next(RESOURCE) == 2
    with pytest.raises(proto.PrototypeRefusal) as caught:
        reopened.acquire(_request("req:2"), peer_uid=UID, now=NOW)
    assert caught.value.code == "LEASE_HELD_BY_OTHER"
    assert reopened.commit_count() == 1
