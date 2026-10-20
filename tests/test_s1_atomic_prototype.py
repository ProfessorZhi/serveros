"""A' atomic authority epoch + lease commit prototype (PROTOTYPE_HARDENING).

This test runs against a real temp WorkEngine SQLite DB (`ServerOSStore`
schema) and independent connections/processes. The owner policy, grant,
context, uid and clock are SYNTHETIC HARNESS INPUTS; nothing here is a
production owner, SO_PEERCRED, store clock or effect commit.

The prototype must not be read as production authorization: its success word
is `PROTOTYPE_FENCED_COMMIT`, it carries `simulated_owner: True` /
`owner_status: SIMULATED_OWNER`, and it has no `verified` key. Production #364
remains `BLOCKED_OWNER_COMMIT_SEAM`.

EVIDENCE: LOCAL / REAL_TEMP SQLite (temp DB, real schema/transaction/token
helpers); SYNTHETIC owner/uid/context/clock; multi-process Linux only.
NOT_RUN: two real OS uids, kernel SO_PEERCRED, a real owner key, production
daemon/Fleet wiring, DB migration, power loss, real Windows.
"""
from __future__ import annotations

import datetime
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

FORK_ONLY = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="MULTIPROCESS_FORK_NOT_AVAILABLE: Linux-only prototype evidence")

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


class FixedClock:
    """A synthetic trusted-clock fixture. No sleep and no wall-clock drift."""

    def __init__(self, now: float = NOW):
        self.now = float(now)

    def __call__(self) -> float:
        return self.now

    def set(self, now: float) -> None:
        self.now = float(now)


def _iso(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _request(request_id="req:1", **overrides) -> dict:
    request = {
        "request_id": request_id, "context_ref": CONTEXT_REF,
        "project": PROJECT, "item_ref": ITEM, "action": ACTION, "ttl_s": 30.0,
    }
    request.update(overrides)
    return request


def _make(tmp_path, *, clock=None, synthetic_peer_uid=UID,
          policy_over=None, grant_over=None, context_over=None,
          seed_policy=True, seed_grant=True, seed_context=True):
    p = proto.AtomicFencePrototype(
        str(tmp_path / "proto"), clock=clock or FixedClock(NOW),
        synthetic_peer_uid=synthetic_peer_uid)
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


def _refused(p, code, request=None):
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(request or _request())
    assert caught.value.code == code, str(caught.value)
    assert p.lease(RESOURCE) is None
    assert p.commit_count() == 0


def _seed_second_grant(p, ref="grant:proto-2", **overrides):
    values = dict(GRANT)
    values["grant_ref"] = ref
    values.update(overrides)
    p.seed_grant(**values)


def _raw_sql(p, sql, params=()):
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(sql, params)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _insert_orphan_commit(p, *, resource, token, context_ref="ctx:missing"):
    """Test-only legacy row with a context ref that does not exist."""
    expires = proto._lease_stamp(NOW + 30)
    committed = proto._lease_stamp(NOW)
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO leases(resource,owner,token,expires_at) "
            "VALUES (?,?,?,?)", (resource, ACTOR, token, expires))
        conn.execute(
            "INSERT INTO proto_commit("
            "request_id,request_digest,grant_ref,policy_ref,context_ref,"
            "context_actor,context_epoch,policy_digest,grant_digest,"
            "grant_epoch,policy_epoch,authority_epoch,resource,owner,token,"
            "expires_at,committed_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("req:orphan", "x", GRANT_REF, POLICY_REF, context_ref,
             ACTOR, 1, "x", "x", 1, 1, 1, resource, ACTOR, token,
             expires, committed))
        conn.commit()
    finally:
        conn.close()


def _bump_grant_epoch(p, epoch=2):
    """Test-only owner grant generation bump, in the same temp SQLite DB."""
    conn = sqlite3.connect(p.db_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE proto_grant SET grant_epoch=? WHERE grant_ref=?",
            (epoch, GRANT_REF))
        conn.commit()
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# The fenced commit itself.
# ---------------------------------------------------------------------------

def test_a_fenced_commit_writes_policy_epoch_and_lease_in_one_transaction(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())

    assert receipt["prototype_status"] == "PROTOTYPE_FENCED_COMMIT"
    assert receipt["simulated_owner"] is True
    assert receipt["owner_status"] == "SIMULATED_OWNER"
    assert receipt["authorized"] is True
    assert receipt["historical"] is False
    assert receipt["effect_eligible"] is False
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
    receipt = p.acquire(_request())
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
    revoked = p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    assert revoked["prototype_status"] == "PROTOTYPE_POLICY_REVOKED"
    assert revoked["owner_status"] == "SIMULATED_OWNER"
    assert revoked["policy_epoch"] == 2
    assert revoked["authority_epoch"] == 2

    _refused(p, "POLICY_DENIED")
    assert p.policy(POLICY_REF)["revoked"] == 1


def test_acquire_before_revoke_is_auditable_and_blocks_a_new_acquire(tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request())
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1)

    lease = p.lease(RESOURCE)
    assert lease["token"] == str(first["token"])
    commit = p.commit("req:1")
    assert commit["policy_epoch"] == 1
    assert commit["authority_epoch"] == 1
    assert p.commit_count() == 1

    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request("req:2"))
    assert caught.value.code == "POLICY_DENIED"
    assert p.lease(RESOURCE)["token"] == str(first["token"])
    assert p.commit_count() == 1


def test_legacy_cross_domain_read_then_revoke_then_write_is_reproduced(tmp_path):
    """RED baseline: the old verify-read / revoke / lease-write gap.

    This test asserts that the legacy pattern DOES write a lease after the
    revoke. It is a reproduction of the problem the prototype removes, not a
    desired behavior. The fenced path above is the GREEN side.
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
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    revoke_done.set()
    thread.join(10)
    assert not thread.is_alive()
    assert outcome == {"snapshot_revoked": 0, "wrote": True}
    lease = p.lease(RESOURCE)
    assert lease is not None and int(lease["token"]) == 99, (
        "the legacy cross-domain write should reproduce a lease after revoke")


# ---------------------------------------------------------------------------
# Commit-phase clock re-check.  Controlled fake-clock jumps, no sleeps.
# ---------------------------------------------------------------------------

def test_ttl_expiry_during_hold_rolls_back_lease_token_and_commit(tmp_path):
    clock = FixedClock(NOW)
    p = _make(tmp_path, clock=clock)
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request(), hold=lambda: clock.set(NOW + 31))
    assert caught.value.code == "LEASE_EXPIRED_AT_COMMIT", str(caught.value)
    assert p.lease(RESOURCE) is None
    assert p.commit_count() == 0
    assert p.token_next(RESOURCE) is None
    assert p.policy(POLICY_REF)["policy_epoch"] == 1


def test_policy_expiry_during_hold_rolls_back_all_rows(tmp_path):
    clock = FixedClock(NOW)
    p = _make(tmp_path, clock=clock, policy_over={
        "expires_at": _iso(NOW + 10)}, grant_over={
        "expires_at": _iso(NOW + 1000)})
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request(), hold=lambda: clock.set(NOW + 11))
    assert caught.value.code == "POLICY_EXPIRED_AT_COMMIT", str(caught.value)
    assert p.lease(RESOURCE) is None
    assert p.commit_count() == 0
    assert p.token_next(RESOURCE) is None


def test_grant_expiry_during_hold_rolls_back_all_rows(tmp_path):
    clock = FixedClock(NOW)
    p = _make(tmp_path, clock=clock, policy_over={
        "expires_at": _iso(NOW + 1000)}, grant_over={
        "expires_at": _iso(NOW + 10)})
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request(), hold=lambda: clock.set(NOW + 11))
    assert caught.value.code == "GRANT_EXPIRED_AT_COMMIT", str(caught.value)
    assert p.lease(RESOURCE) is None
    assert p.commit_count() == 0
    assert p.token_next(RESOURCE) is None


def test_a_backward_clock_jump_before_the_window_rolls_back(tmp_path):
    clock = FixedClock(NOW)
    p = _make(tmp_path, clock=clock, policy_over={
        "valid_from": _iso(NOW - 100)}, grant_over={
        "valid_from": _iso(NOW - 100)})
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request(), hold=lambda: clock.set(NOW - 200))
    assert caught.value.code == "POLICY_DENIED", str(caught.value)
    assert p.lease(RESOURCE) is None
    assert p.commit_count() == 0
    assert p.token_next(RESOURCE) is None


def test_a_caller_cannot_pass_now_or_peer_uid_to_acquire(tmp_path):
    p = _make(tmp_path)
    with pytest.raises(TypeError):
        p.acquire(_request(), now=NOW)
    with pytest.raises(TypeError):
        p.acquire(_request(), peer_uid=UID)


def test_a_missing_synthetic_peer_uid_is_refused(tmp_path):
    p = _make(tmp_path, synthetic_peer_uid=None)
    _refused(p, "UNTRUSTED_ACTOR")


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


def test_cross_uid_is_refused(tmp_path):
    p = _make(tmp_path, synthetic_peer_uid=9999)
    _refused(p, "UNTRUSTED_ACTOR")


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


def test_empty_issued_by_is_refused(tmp_path):
    p = _make(tmp_path, policy_over={"issued_by": ""})
    _refused(p, "BLOCKED_OWNER_POLICY")


def test_authority_epoch_behind_policy_epoch_is_refused(tmp_path):
    p = _make(tmp_path, policy_over={"policy_epoch": 2, "authority_epoch": 1},
              grant_over={"owner_policy_epoch": 2})
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


def test_the_real_work_engine_db_is_in_wal_mode(tmp_path):
    p = _make(tmp_path)
    conn = sqlite3.connect(p.db_path)
    try:
        mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    finally:
        conn.close()
    assert str(mode).lower() == "wal"


# ---------------------------------------------------------------------------
# Idempotency / historical replay / old writer.
# ---------------------------------------------------------------------------

def test_same_request_id_is_a_historical_replay_not_current_authority(tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request())
    again = p.acquire(_request())
    assert again["prototype_status"] == "PROTOTYPE_HISTORICAL_REPLAY"
    assert again["authorized"] is False
    assert again["historical"] is True
    assert again["effect_eligible"] is False
    assert again["token"] == first["token"]
    assert p.commit_count() == 1
    assert p.token_next(RESOURCE) == 2


def test_same_request_id_with_different_facts_conflicts(tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request())
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request(ttl_s=31.0))
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert p.lease(RESOURCE)["token"] == str(first["token"])
    assert p.commit_count() == 1


def test_same_request_id_with_a_different_epoch_pin_conflicts(tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request())
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request(expected_authority_epoch=1))
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert p.lease(RESOURCE)["token"] == str(first["token"])
    assert p.commit_count() == 1


def test_missing_request_id_is_refused(tmp_path):
    p = _make(tmp_path)
    request = _request()
    del request["request_id"]
    _refused(p, "INVALID_REQUEST", request=request)


def test_replay_after_revoke_is_historical_and_new_request_refuses(tmp_path):
    p = _make(tmp_path)
    first = p.acquire(_request())
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    replay = p.acquire(_request())
    assert replay["prototype_status"] == "PROTOTYPE_HISTORICAL_REPLAY"
    assert replay["authorized"] is False
    assert replay["historical"] is True
    assert replay["effect_eligible"] is False
    assert replay["token"] == first["token"]
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.acquire(_request("req:2"))
    assert caught.value.code == "POLICY_DENIED"
    assert p.commit_count() == 1


def test_token_is_monotonic_after_expiry_and_old_replay_keeps_its_token(
        tmp_path):
    clock = FixedClock(NOW)
    p = _make(tmp_path, clock=clock)
    first = p.acquire(_request())
    clock.set(NOW + 3600)
    second = p.acquire(_request("req:2"))
    assert second["token"] == first["token"] + 1
    replay = p.acquire(_request())
    assert replay["prototype_status"] == "PROTOTYPE_HISTORICAL_REPLAY"
    assert replay["authorized"] is False
    assert replay["token"] == first["token"]
    assert p.lease(RESOURCE)["token"] == str(second["token"])
    assert p.commit_count() == 2


# ---------------------------------------------------------------------------
# Production-wiring gaps: legacy writes outside the fence.
# ---------------------------------------------------------------------------

def test_legacy_serveros_store_acquire_bypasses_the_fence(tmp_path):
    """PRODUCTION_WIRING_REQUIRED: the real store can write outside the fence.

    This is a negative test proving the prototype is not a production
    boundary. It must keep passing while the production entrypoint is
    unfenced; it is not a desired authorization.
    """
    p = _make(tmp_path)
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    store = proto.ServerOSStore(str(p.state_dir))
    lease = store.acquire_lease(RESOURCE, owner="human:LEGACY",
                                ttl_s=30, now=NOW)
    assert lease["token"] == 1
    assert p.lease(RESOURCE)["owner"] == "human:LEGACY"
    assert p.commit_count() == 0
    assert p.policy(POLICY_REF)["revoked"] == 1


def test_legacy_check_writer_accepts_a_pre_revoke_token_after_revoke(tmp_path):
    """PRODUCTION_WIRING_REQUIRED: check_writer reads only the lease row.

    The prototype's `check_writer_fenced` refuses the same token after the
    revoke; a production writer must use the fenced check (and hold it across
    its effect), not the legacy one.
    """
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    store = proto.ServerOSStore(str(p.state_dir))
    legacy = store.check_writer(RESOURCE, owner=ACTOR,
                                token=receipt["token"], now=NOW)
    assert legacy["authorized"] is True
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced(RESOURCE, owner=ACTOR, token=receipt["token"])
    assert caught.value.code == "POLICY_DENIED"


def test_fenced_check_writer_refuses_an_expired_grant_after_acquire(tmp_path):
    clock = FixedClock(NOW)
    p = _make(tmp_path, clock=clock, policy_over={
        "expires_at": _iso(NOW + 1000)}, grant_over={
        "expires_at": _iso(NOW + 10)})
    receipt = p.acquire(_request(ttl_s=600.0))
    clock.set(NOW + 11)
    store = proto.ServerOSStore(str(p.state_dir))
    legacy = store.check_writer(RESOURCE, owner=ACTOR,
                                token=receipt["token"], now=NOW + 11)
    assert legacy["authorized"] is True
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced(RESOURCE, owner=ACTOR,
                              token=receipt["token"])
    assert caught.value.code == "GRANT_EXPIRED_AT_COMMIT"


def test_fenced_check_refuses_after_grant_epoch_bump(tmp_path):
    """QA2 witness: a lease committed at grant_epoch=1 must not survive epoch=2.

    The legacy reader still accepts the token (PRODUCTION_WIRING_REQUIRED);
    the fenced check must refuse BLOCKED_STALE_EPOCH.
    """
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    assert p.commit("req:1")["grant_epoch"] == 1
    _bump_grant_epoch(p, 2)
    store = proto.ServerOSStore(str(p.state_dir))
    legacy = store.check_writer(RESOURCE, owner=ACTOR,
                                token=receipt["token"], now=NOW)
    assert legacy["authorized"] is True
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced(RESOURCE, owner=ACTOR,
                              token=receipt["token"])
    assert caught.value.code == "BLOCKED_STALE_EPOCH", str(caught.value)


def test_fenced_check_refuses_context_rebind_to_a_same_epoch_grant(tmp_path):
    """A rebind must bump context_epoch; old grant_epoch equality is not enough."""
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    _seed_second_grant(p, "grant:proto-2")
    rebind = p.rebind_context(CONTEXT_REF, expected_context_epoch=1,
                              grant_ref="grant:proto-2")
    assert rebind["context_epoch"] == 2
    assert p.context(CONTEXT_REF)["context_epoch"] == 2
    store = proto.ServerOSStore(str(p.state_dir))
    legacy = store.check_writer(RESOURCE, owner=ACTOR,
                                token=receipt["token"], now=NOW)
    assert legacy["authorized"] is True
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced(RESOURCE, owner=ACTOR,
                              token=receipt["token"])
    assert caught.value.code == "BLOCKED_STALE_CONTEXT_EPOCH", str(caught.value)


def test_context_aba_rebind_cannot_resurrect_the_old_token(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    _seed_second_grant(p, "grant:proto-2")
    assert p.rebind_context(CONTEXT_REF, expected_context_epoch=1,
                            grant_ref="grant:proto-2")["context_epoch"] == 2
    assert p.rebind_context(CONTEXT_REF, expected_context_epoch=2,
                            grant_ref=GRANT_REF)["context_epoch"] == 3
    assert p.context(CONTEXT_REF)["context_epoch"] == 3
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced(RESOURCE, owner=ACTOR,
                              token=receipt["token"])
    assert caught.value.code == "BLOCKED_STALE_CONTEXT_EPOCH", str(caught.value)


def test_context_actor_aba_cannot_resurrect_the_old_token(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    assert p.rebind_context(CONTEXT_REF, expected_context_epoch=1,
                            actor_ref="human:B")["context_epoch"] == 2
    assert p.rebind_context(CONTEXT_REF, expected_context_epoch=2,
                            actor_ref=ACTOR)["context_epoch"] == 3
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced(RESOURCE, owner=ACTOR,
                              token=receipt["token"])
    assert caught.value.code == "BLOCKED_STALE_CONTEXT_EPOCH", str(caught.value)


def test_raw_sql_context_rebind_without_epoch_is_refused(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    _seed_second_grant(p, "grant:proto-2")
    with pytest.raises(sqlite3.IntegrityError):
        _raw_sql(p, "UPDATE proto_context SET grant_ref=? WHERE context_ref=?",
                 ("grant:proto-2", CONTEXT_REF))
    assert p.context(CONTEXT_REF)["context_epoch"] == 1
    checked = p.check_writer_fenced(RESOURCE, owner=ACTOR,
                                    token=receipt["token"])
    assert checked["authorized"] is True


def test_context_delete_is_refused_by_generation_trigger(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    with pytest.raises(sqlite3.IntegrityError):
        _raw_sql(p, "DELETE FROM proto_context WHERE context_ref=?",
                 (CONTEXT_REF,))
    checked = p.check_writer_fenced(RESOURCE, owner=ACTOR,
                                    token=receipt["token"])
    assert checked["authorized"] is True


def test_fenced_check_refuses_a_missing_context_row(tmp_path):
    p = _make(tmp_path)
    p.acquire(_request())
    _insert_orphan_commit(p, resource="work-write:SRV-9999", token=77)
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced("work-write:SRV-9999", owner=ACTOR, token=77)
    assert caught.value.code == "BLOCKED_STALE_CONTEXT", str(caught.value)


def test_context_rebind_priority_over_policy_revoke_and_epoch_bump(tmp_path):
    for label, mutate in (
            ("revoke", lambda p: p.revoke_policy(
                POLICY_REF, expected_policy_epoch=1)),
            ("epoch", lambda p: _bump_grant_epoch(p, 2)),
    ):
        case = tmp_path / label
        case.mkdir()
        p = _make(case)
        receipt = p.acquire(_request())
        _seed_second_grant(p, "grant:proto-2")
        p.rebind_context(CONTEXT_REF, expected_context_epoch=1,
                         grant_ref="grant:proto-2")
        mutate(p)
        with pytest.raises(proto.PrototypeRefusal) as caught:
            p.check_writer_fenced(RESOURCE, owner=ACTOR,
                                  token=receipt["token"])
        assert caught.value.code == "BLOCKED_STALE_CONTEXT_EPOCH", (
            label, str(caught.value))


def test_same_epoch_grant_content_mutation_is_refused(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    _raw_sql(p, "UPDATE proto_grant SET projects_json=? WHERE grant_ref=?",
             ('["other"]', GRANT_REF))
    store = proto.ServerOSStore(str(p.state_dir))
    legacy = store.check_writer(RESOURCE, owner=ACTOR,
                                token=receipt["token"], now=NOW)
    assert legacy["authorized"] is True
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced(RESOURCE, owner=ACTOR,
                              token=receipt["token"])
    assert caught.value.code == "BLOCKED_STALE_CONTENT", str(caught.value)


def test_same_epoch_policy_content_mutation_is_refused(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    _raw_sql(p, "UPDATE proto_owner_policy SET projects_json=? "
                "WHERE policy_ref=?", ('["other"]', POLICY_REF))
    store = proto.ServerOSStore(str(p.state_dir))
    legacy = store.check_writer(RESOURCE, owner=ACTOR,
                                token=receipt["token"], now=NOW)
    assert legacy["authorized"] is True
    with pytest.raises(proto.PrototypeRefusal) as caught:
        p.check_writer_fenced(RESOURCE, owner=ACTOR,
                              token=receipt["token"])
    assert caught.value.code == "BLOCKED_STALE_CONTENT", str(caught.value)


def test_cross_context_rebind_does_not_affect_the_old_token(tmp_path):
    p = _make(tmp_path)
    p.seed_context(context_ref="ctx:other", actor_ref=ACTOR,
                   grant_ref=GRANT_REF)
    receipt = p.acquire(_request())
    _seed_second_grant(p, "grant:proto-2")
    p.rebind_context("ctx:other", expected_context_epoch=1,
                     grant_ref="grant:proto-2")
    checked = p.check_writer_fenced(RESOURCE, owner=ACTOR,
                                    token=receipt["token"])
    assert checked["authorized"] is True
    assert checked["context_ref"] == CONTEXT_REF


def test_commit_rows_are_immutable(tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    with pytest.raises(sqlite3.IntegrityError):
        _raw_sql(p, "UPDATE proto_commit SET context_ref=? WHERE request_id=?",
                 ("ctx:other", "req:1"))
    with pytest.raises(sqlite3.IntegrityError):
        _raw_sql(p, "DELETE FROM proto_commit WHERE request_id=?", ("req:1",))
    checked = p.check_writer_fenced(RESOURCE, owner=ACTOR,
                                    token=receipt["token"])
    assert checked["authorized"] is True


def test_fenced_check_writer_accepts_before_revoke_and_never_effect_eligible(
        tmp_path):
    p = _make(tmp_path)
    receipt = p.acquire(_request())
    checked = p.check_writer_fenced(RESOURCE, owner=ACTOR,
                                    token=receipt["token"])
    assert checked["prototype_status"] == "PROTOTYPE_FENCED_CHECK"
    assert checked["authorized"] is True
    assert checked["historical"] is False
    assert checked["effect_eligible"] is False
    assert checked["context_ref"] == CONTEXT_REF
    assert checked["context_actor"] == ACTOR
    assert checked["context_epoch"] == 1
    assert checked["grant_ref"] == GRANT_REF


# ---------------------------------------------------------------------------
# Multi-process: two independent SQLite connections.
# ---------------------------------------------------------------------------

def _worker_proto(db_dir):
    return proto.AtomicFencePrototype(
        db_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID)


def _worker_acquire(db_dir, request, barrier, queue):
    p = _worker_proto(db_dir)
    barrier.wait(timeout=15)
    try:
        receipt = p.acquire(request)
        queue.put(("ok", receipt["token"], receipt["request_id"]))
    except BaseException as exc:
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _worker_acquire_hold(db_dir, request, written, release, queue):
    p = _worker_proto(db_dir)

    def hold():
        written.set()
        assert release.wait(15)

    try:
        receipt = p.acquire(request, hold=hold)
        queue.put(("ok", receipt["token"]))
    except BaseException as exc:
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _worker_revoke_probe(db_dir, policy_ref, expected, attempted, blocked,
                         release, queue):
    """Attempt BEGIN IMMEDIATE in a separate connection, then really revoke.

    `blocked` is set only when that independent BEGIN IMMEDIATE raises
    'database is locked' because the acquirer's transaction holds the write
    lock. No sleep and no simulated concurrency.
    """
    p = _worker_proto(db_dir)
    conn = sqlite3.connect(p.db_path, timeout=0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 0")
    attempted.set()
    try:
        try:
            conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower():
                queue.put(("probe_error", str(exc)))
                return
            blocked.set()
        else:
            conn.rollback()
            queue.put(("probe_not_blocked", None))
            return
    finally:
        conn.close()
    assert release.wait(15)
    try:
        result = p.revoke_policy(policy_ref, expected_policy_epoch=expected)
        queue.put(("ok", result["policy_epoch"]))
    except BaseException as exc:
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _worker_crash_before_commit(db_dir, request):
    p = _worker_proto(db_dir)
    p.acquire(request, hold=lambda: os._exit(17))


def _worker_commit_then_exit(db_dir, request, queue):
    p = _worker_proto(db_dir)
    receipt = p.acquire(request)
    queue.put(("ok", receipt["token"]))
    # Flush the feeder thread before the hard process exit; os._exit does not
    # run multiprocessing's normal shutdown.
    queue.close()
    queue.join_thread()
    os._exit(23)


def _worker_commit(db_dir, request, queue):
    p = _worker_proto(db_dir)
    try:
        receipt = p.acquire(request)
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
                          barrier, queue))
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
    assert p.token_next(RESOURCE) == successes[0][1] + 1


@FORK_ONLY
def test_acquire_lock_precedes_a_concurrent_revoke_with_blocked_barrier(
        tmp_path):
    p = _make(tmp_path)
    ctx = multiprocessing.get_context("fork")
    written = ctx.Event()
    release = ctx.Event()
    attempted = ctx.Event()
    blocked = ctx.Event()
    acquire_queue = ctx.Queue()
    revoke_queue = ctx.Queue()
    acquirer = ctx.Process(
        target=_worker_acquire_hold,
        args=(str(p.state_dir), _request(), written, release, acquire_queue))
    revoker = ctx.Process(
        target=_worker_revoke_probe,
        args=(str(p.state_dir), POLICY_REF, 1, attempted, blocked, release,
              revoke_queue))
    acquirer.start()
    assert written.wait(15), "acquirer did not reach its held transaction"
    revoker.start()
    assert attempted.wait(5), "revoker did not attempt BEGIN IMMEDIATE"
    assert blocked.wait(5), (
        "revoker's independent BEGIN IMMEDIATE was not blocked by the "
        "acquirer's write lock")
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
        p.acquire(_request("req:2"))
    assert caught.value.code == "POLICY_DENIED"
    assert p.commit_count() == 1


@FORK_ONLY
def test_revoke_first_then_acquire_in_another_process_is_refused(tmp_path):
    p = _make(tmp_path)
    p.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_worker_acquire,
        args=(str(p.state_dir), _request(), ctx.Barrier(1), queue))
    process.start()
    assert queue.get(timeout=15) == ("refused", "POLICY_DENIED")
    _join(process)
    assert p.lease(RESOURCE) is None
    assert p.commit_count() == 0


# ---------------------------------------------------------------------------
# Crash / rollback / restart.
# ---------------------------------------------------------------------------

@FORK_ONLY
def test_crash_before_commit_leaves_no_lease_commit_or_token(tmp_path):
    p = _make(tmp_path)
    ctx = multiprocessing.get_context("fork")
    process = ctx.Process(
        target=_worker_crash_before_commit,
        args=(str(p.state_dir), _request()))
    process.start()
    process.join(20)
    assert not process.is_alive()
    assert process.exitcode == 17

    reopened = proto.AtomicFencePrototype(str(p.state_dir), initialize=False,
                                          clock=FixedClock(NOW),
                                          synthetic_peer_uid=UID)
    assert reopened.lease(RESOURCE) is None
    assert reopened.commit("req:1") is None
    assert reopened.commit_count() == 0
    assert reopened.token_next(RESOURCE) is None
    assert reopened.policy(POLICY_REF)["policy_epoch"] == 1


@FORK_ONLY
def test_commit_then_process_exit_survives_and_integrity_is_ok(tmp_path):
    p = _make(tmp_path)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_worker_commit_then_exit,
        args=(str(p.state_dir), _request(), queue))
    process.start()
    assert queue.get(timeout=15)[0] == "ok"
    process.join(20)
    assert not process.is_alive()
    assert process.exitcode == 23

    reopened = proto.AtomicFencePrototype(str(p.state_dir), initialize=False,
                                          clock=FixedClock(NOW),
                                          synthetic_peer_uid=UID)
    lease = reopened.lease(RESOURCE)
    assert lease is not None and lease["token"] == "1"
    assert reopened.commit("req:1")["token"] == 1
    conn = sqlite3.connect(reopened.db_path)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower() == "wal"
        # Recorded for the report: os._exit after COMMIT is a process kill,
        # not a power-loss/fsync proof.
        synchronous = conn.execute("PRAGMA synchronous").fetchone()[0]
    finally:
        conn.close()
    assert synchronous in (1, 2, 3)  # NORMAL/FULL/EXTRA
    replay = reopened.acquire(_request())
    assert replay["prototype_status"] == "PROTOTYPE_HISTORICAL_REPLAY"
    assert replay["authorized"] is False
    assert replay["token"] == 1
    assert reopened.token_next(RESOURCE) == 2


@FORK_ONLY
def test_commit_survives_restart_and_replay_is_idempotent(tmp_path):
    p = _make(tmp_path)
    ctx = multiprocessing.get_context("fork")
    queue = ctx.Queue()
    process = ctx.Process(
        target=_worker_commit,
        args=(str(p.state_dir), _request(), queue))
    process.start()
    assert queue.get(timeout=15)[0] == "ok"
    _join(process)

    reopened = proto.AtomicFencePrototype(str(p.state_dir), initialize=False,
                                          clock=FixedClock(NOW),
                                          synthetic_peer_uid=UID)
    lease = reopened.lease(RESOURCE)
    assert lease is not None and lease["token"] == "1"
    assert reopened.commit("req:1")["token"] == 1
    replay = reopened.acquire(_request())
    assert replay["prototype_status"] == "PROTOTYPE_HISTORICAL_REPLAY"
    assert replay["authorized"] is False
    assert replay["token"] == 1
    assert reopened.token_next(RESOURCE) == 2
    with pytest.raises(proto.PrototypeRefusal) as caught:
        reopened.acquire(_request("req:2"))
    assert caught.value.code == "LEASE_HELD_BY_OTHER"
    assert reopened.commit_count() == 1
