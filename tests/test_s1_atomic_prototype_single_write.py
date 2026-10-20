"""A' single-writer capability API: API_ONLY_GREEN vs raw-DB bypass.

The writer object is the only API-only entry for synthetic issue/revoke/rebind/
acquire/use-check/effect-marker. The same-file raw SQL adversary is expected
to bypass it; that is classified RAW_DB_WRITE_BYPASS_EXPECTED /
BLOCKED_DB_WRITER_ISOLATION, never hidden as a fix.

EVIDENCE: LOCAL / REAL_TEMP SQLite; synthetic owner/uid/actor/clock; Linux
fork only. No production owner/OS uid/socket/key/daemon/provider.
NOT_RUN: OS isolation, real owner key, production finalize/effect.
"""
from __future__ import annotations

import importlib.util
import multiprocessing
import os
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
PATH = REPO / "integrations" / "fleet-v1-atomic-prototype" / "single_write_entry.py"


def _load():
    name = "s1_single_write_entry"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


swe = _load()
atomic = swe.atomic

FORK_ONLY = pytest.mark.skipif(
    not hasattr(os, "fork"),
    reason="MULTIPROCESS_FORK_NOT_AVAILABLE: Linux-only prototype evidence")

NOW = 2_000_000_000.0
UID = 4242
ACTOR = "human:A"
POLICY_REF = "policy:proto"
GRANT_REF = "grant:proto"
GRANT2_REF = "grant:proto-2"
CONTEXT_REF = "ctx:proto"
PROJECT = "serveros"
ITEM = "SRV-0001"
ACTION = "lease.acquire"
RESOURCE = "work-write:SRV-0001"
VALID_FROM = "2000-01-01T00:00:00Z"
EXPIRES_AT = "2999-01-01T00:00:00Z"
ISSUED_AT = "2026-01-01T00:00:00Z"


class FixedClock:
    def __init__(self, now: float = NOW):
        self.now = float(now)

    def __call__(self) -> float:
        return self.now


def _writer(tmp_path, *, clock=None, initialize=True):
    w = swe.SingleWriteEntry(
        str(tmp_path / "db"), initialize=initialize,
        clock=clock or FixedClock(NOW), synthetic_peer_uid=UID,
        actor_ref=ACTOR, capability_key=b"test-capability-key")
    if initialize:
        w.issue(
            policy_ref=POLICY_REF, grant_ref=GRANT_REF,
            context_ref=CONTEXT_REF, projects=[PROJECT], items=[ITEM],
            actions=[ACTION], valid_from=VALID_FROM, expires_at=EXPIRES_AT,
            issued_at=ISSUED_AT)
    return w


def _request(request_id="req:1", **overrides):
    request = {
        "request_id": request_id, "context_ref": CONTEXT_REF,
        "project": PROJECT, "item_ref": ITEM, "action": ACTION, "ttl_s": 30.0,
    }
    request.update(overrides)
    return request


def _effect_count(w):
    with w._writer._transaction(immediate=False) as conn:
        return int(conn.execute("SELECT COUNT(*) FROM proto_effect").fetchone()[0])


# ---------------------------------------------------------------------------
# A / API_ONLY_GREEN
# ---------------------------------------------------------------------------

def test_api_only_issue_acquire_use_check_and_effect_marker(tmp_path):
    w = _writer(tmp_path)
    receipt = w.acquire(_request())
    assert receipt["prototype_status"] == "PROTOTYPE_FENCED_COMMIT"
    assert receipt["capability_scope"] == "API_ONLY"
    assert receipt["production_effect_eligible"] is False
    assert "verified" not in receipt

    checked = w.check_use(receipt["request_id"], receipt["token"],
                          receipt["capability"])
    assert checked["prototype_status"] == "PROTOTYPE_USE_CHECK_OK"
    assert checked["authorized"] is True
    assert checked["effect_eligible"] is False
    assert checked["production_effect"] is False

    marker = w.commit_effect(
        receipt["request_id"], receipt["token"], receipt["capability"],
        effect_ref="effect:1", payload_digest="digest:1")
    assert marker["prototype_status"] == "PROTOTYPE_EFFECT_MARKER_COMMITTED"
    assert marker["effect_marker"] is True
    assert marker["production_effect"] is False
    assert marker["effect_eligible"] is False
    assert _effect_count(w) == 1
    row = w.effect("effect:1")
    assert row["token"] == receipt["token"]
    assert row["payload_digest"] == "digest:1"


def test_effect_replay_is_idempotent_and_conflicting_payload_is_refused(tmp_path):
    w = _writer(tmp_path)
    receipt = w.acquire(_request())
    first = w.commit_effect(receipt["request_id"], receipt["token"],
                            receipt["capability"], effect_ref="effect:1",
                            payload_digest="digest:1")
    again = w.commit_effect(receipt["request_id"], receipt["token"],
                            receipt["capability"], effect_ref="effect:1",
                            payload_digest="digest:1")
    assert again["replayed"] is True
    assert again["authorized"] is False
    assert _effect_count(w) == 1
    with pytest.raises(swe.PrototypeRefusal) as caught:
        w.commit_effect(receipt["request_id"], receipt["token"],
                        receipt["capability"], effect_ref="effect:1",
                        payload_digest="digest:2")
    assert caught.value.code == "IDEMPOTENCY_CONFLICT"
    assert first["effect_ref"] == "effect:1"


def test_api_only_cannot_supply_authority_facts(tmp_path):
    w = _writer(tmp_path)
    with pytest.raises(TypeError):
        w.acquire(_request(), uid=UID)
    with pytest.raises(TypeError):
        w.acquire(_request(), actor_ref=ACTOR)
    with pytest.raises(TypeError):
        w.acquire(_request(), now=NOW)
    receipt = w.acquire(_request())
    with pytest.raises(TypeError):
        w.check_use(receipt["request_id"], receipt["token"],
                    receipt["capability"], actor_ref=ACTOR)
    with pytest.raises(TypeError):
        w.commit_effect(receipt["request_id"], receipt["token"],
                        receipt["capability"], effect_ref="e",
                        payload_digest="d", now=NOW)
    with pytest.raises(swe.PrototypeRefusal) as caught:
        w.acquire(_request(uid=UID))
    assert caught.value.code == "INVALID_REQUEST"


def test_tampered_capability_is_refused_without_an_effect(tmp_path):
    w = _writer(tmp_path)
    receipt = w.acquire(_request())
    bad = receipt["capability"][:-1] + (
        "0" if receipt["capability"][-1] != "0" else "1")
    for call in (
            lambda: w.check_use(receipt["request_id"],
                                receipt["token"], bad),
            lambda: w.commit_effect(receipt["request_id"], receipt["token"],
                                    bad, effect_ref="effect:bad",
                                    payload_digest="d"),
    ):
        with pytest.raises(swe.PrototypeRefusal) as caught:
            call()
        assert caught.value.code == "BLOCKED_CAPABILITY"
    assert _effect_count(w) == 0


def test_revoke_after_acquire_blocks_use_and_effect(tmp_path):
    w = _writer(tmp_path)
    receipt = w.acquire(_request())
    w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    with pytest.raises(swe.PrototypeRefusal) as caught:
        w.check_use(receipt["request_id"], receipt["token"],
                    receipt["capability"])
    assert caught.value.code == "POLICY_DENIED"
    with pytest.raises(swe.PrototypeRefusal) as caught:
        w.commit_effect(receipt["request_id"], receipt["token"],
                        receipt["capability"], effect_ref="effect:revoked",
                        payload_digest="d")
    assert caught.value.code == "POLICY_DENIED"
    assert _effect_count(w) == 0


def test_context_aba_after_acquire_blocks_use_and_effect(tmp_path):
    w = _writer(tmp_path)
    receipt = w.acquire(_request())
    w.issue_grant(grant_ref=GRANT2_REF, policy_ref=POLICY_REF,
                  projects=[PROJECT], items=[ITEM], actions=[ACTION],
                  valid_from=VALID_FROM, expires_at=EXPIRES_AT)
    assert w.rebind_context(CONTEXT_REF, expected_context_epoch=1,
                            grant_ref=GRANT2_REF)["context_epoch"] == 2
    assert w.rebind_context(CONTEXT_REF, expected_context_epoch=2,
                            grant_ref=GRANT_REF)["context_epoch"] == 3
    for call in (
            lambda: w.check_use(receipt["request_id"],
                                receipt["token"], receipt["capability"]),
            lambda: w.commit_effect(receipt["request_id"],
                                    receipt["token"], receipt["capability"],
                                    effect_ref="effect:aba",
                                    payload_digest="d"),
    ):
        with pytest.raises(swe.PrototypeRefusal) as caught:
            call()
        assert caught.value.code == "BLOCKED_STALE_CONTEXT_EPOCH"
    assert _effect_count(w) == 0


def test_expired_or_superseded_token_blocks_use_check(tmp_path):
    clock = FixedClock(NOW)
    w = _writer(tmp_path, clock=clock)
    first = w.acquire(_request("req:1"))
    clock.now = NOW + 31
    with pytest.raises(swe.PrototypeRefusal) as caught:
        w.check_use(first["request_id"], first["token"], first["capability"])
    assert caught.value.code == "LEASE_EXPIRED"
    second = w.acquire(_request("req:2"))
    with pytest.raises(swe.PrototypeRefusal) as caught:
        w.check_use(first["request_id"], first["token"], first["capability"])
    assert caught.value.code == "STALE_TOKEN"
    ok = w.check_use(second["request_id"], second["token"],
                     second["capability"])
    assert ok["authorized"] is True


def test_effect_marker_rolls_back_on_failure_before_commit(tmp_path):
    w = _writer(tmp_path)
    receipt = w.acquire(_request())

    def explode():
        raise RuntimeError("test fault after marker insert")

    with pytest.raises(RuntimeError):
        w.commit_effect(receipt["request_id"], receipt["token"],
                        receipt["capability"], effect_ref="effect:fault",
                        payload_digest="d", hold=explode)
    assert w.effect("effect:fault") is None
    assert _effect_count(w) == 0
    # The lease/commit fence rows are unchanged by the rolled-back marker.
    assert w.commit(receipt["request_id"])["token"] == receipt["token"]


@FORK_ONLY
def test_effect_marker_rolls_back_on_process_crash(tmp_path):
    w = _writer(tmp_path)
    ctx = multiprocessing.get_context("fork")
    process = ctx.Process(
        target=_worker_crash_effect,
        args=(str(w._writer.state_dir), _request("req:crash")))
    process.start()
    process.join(20)
    assert not process.is_alive()
    assert process.exitcode == 17
    reopened = _writer(tmp_path, initialize=False)
    assert reopened.effect("effect:crash") is None
    assert _effect_count(reopened) == 0


# ---------------------------------------------------------------------------
# B / RAW_DB_WRITE_BYPASS_EXPECTED
# ---------------------------------------------------------------------------

def test_raw_db_writer_can_commit_an_effect_marker_bypassing_the_api(tmp_path):
    """EXPECTED_RAW_DB_BYPASS: same-file SQL writer is outside the capability API."""
    w = _writer(tmp_path)
    receipt = w.acquire(_request())
    w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    with pytest.raises(swe.PrototypeRefusal):
        w.check_use(receipt["request_id"], receipt["token"],
                    receipt["capability"])

    conn = sqlite3.connect(w._writer.db_path, isolation_level=None)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO proto_effect("
            "effect_ref,request_id,resource,owner,token,payload_digest,"
            "committed_at,context_epoch,grant_epoch,policy_epoch,"
            "authority_epoch) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            ("effect:raw", receipt["request_id"], receipt["resource"],
             receipt["owner"], receipt["token"], "raw-digest",
             atomic._lease_stamp(NOW), receipt["context_epoch"],
             receipt["grant_epoch"], receipt["policy_epoch"],
             receipt["authority_epoch"]))
        conn.commit()
    finally:
        conn.close()
    assert w.effect("effect:raw") is not None
    assert swe.classification()["B"]["name"] == \
        swe.RAW_DB_WRITE_BYPASS_EXPECTED
    assert swe.classification()["B"]["blocked"] == \
        swe.BLOCKED_DB_WRITER_ISOLATION


def test_legacy_store_still_bypasses_the_writer_api(tmp_path):
    """PRODUCTION_WIRING_REQUIRED: the real store writes outside the API."""
    w = _writer(tmp_path)
    receipt = w.acquire(_request())
    w.revoke_policy(POLICY_REF, expected_policy_epoch=1)
    resource = "work-write:SRV-9999"
    store = atomic.ServerOSStore(str(w._writer.state_dir))
    lease = store.acquire_lease(resource, owner="human:LEGACY",
                                ttl_s=30, now=NOW)
    assert lease["token"] == 1
    assert w._writer.lease(resource)["owner"] == "human:LEGACY"
    assert w._writer.commit("req:legacy") is None
    with pytest.raises(swe.PrototypeRefusal):
        w.commit_effect(receipt["request_id"], receipt["token"],
                        receipt["capability"], effect_ref="effect:legacy",
                        payload_digest="d")
    assert _effect_count(w) == 0


def test_os_isolation_is_not_claimed():
    report = swe.classification()
    assert report["C"]["name"] == swe.OS_ISOLATION_NOT_RUN
    assert report["A"]["name"] == swe.API_ONLY_GREEN
    assert report["effect_commit"] == swe.EFFECT_COMMIT_NOT_PROVEN
    assert report["production"] == "NOT_V1_SIGNED"


# ---------------------------------------------------------------------------
# Multi-process acquire: one winner, one fence.
# ---------------------------------------------------------------------------

def _worker_acquire(db_dir, request, barrier, queue):
    w = swe.SingleWriteEntry(
        db_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID, actor_ref=ACTOR,
        capability_key=b"test-capability-key")
    barrier.wait(timeout=15)
    try:
        receipt = w.acquire(request)
        queue.put(("ok", receipt["token"], receipt["request_id"]))
    except BaseException as exc:
        queue.put(("refused", getattr(exc, "code", type(exc).__name__)))


def _worker_crash_effect(db_dir, request):
    w = swe.SingleWriteEntry(
        db_dir, initialize=False, clock=FixedClock(NOW),
        synthetic_peer_uid=UID, actor_ref=ACTOR,
        capability_key=b"test-capability-key")
    receipt = w.acquire(request)
    w.commit_effect(receipt["request_id"], receipt["token"],
                    receipt["capability"], effect_ref="effect:crash",
                    payload_digest="d", hold=lambda: os._exit(17))


@FORK_ONLY
def test_two_processes_compete_for_one_writer_lease(tmp_path):
    w = _writer(tmp_path)
    ctx = multiprocessing.get_context("fork")
    barrier = ctx.Barrier(2)
    queue = ctx.Queue()
    processes = [
        ctx.Process(target=_worker_acquire,
                    args=(str(w._writer.state_dir), _request(request_id),
                          barrier, queue))
        for request_id in ("req:p1", "req:p2")
    ]
    for process in processes:
        process.start()
    results = [queue.get(timeout=20) for _ in processes]
    for process in processes:
        process.join(20)
        assert not process.is_alive()
        assert process.exitcode == 0
    assert len([r for r in results if r[0] == "ok"]) == 1, results
    assert len([r for r in results if r[0] == "refused"]) == 1, results
    assert w._writer.commit_count() == 1
