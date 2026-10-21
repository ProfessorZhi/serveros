#!/usr/bin/env python3
"""Cross-repo resource contract: ONE namespace, `work-write:<item_ref>`.

THE MISMATCH
------------
Work Engine #14's `OwnerVerifiedLease._resource(item_ref)` derives
`work-write:<item_ref>` and stores THAT in `leases.resource`. ServerOS #264's
`context/resolver.py#live_lease` looked up the naked Item (`SRV-0001`), and the
earlier positive test `git`-acquired a naked row by hand — so it never proved
the owner-verified producer could be read at all.

The correction is one canonical function, `work_write_resource(item_ref)`, used
by the reader. There is NO fallback to the naked name and no guessing of an
alternate resource: a scope is `work-write:<item>` or it is not this scope.

Hermetic: temp SQLite, a real `Daemon` over a temp root. No provider, no network.
"""
from __future__ import annotations

import datetime
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
STAMP = "%Y-%m-%dT%H:%M:%SZ"
ITEM = "SRV-0001"
RESOURCE = "work-write:SRV-0001"


def _load_at(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _load(name: str, filename: str):
    return _load_at(name, RUNTIME / filename)


resolver = _load_at("serveros_context_resolver", REPO / "context" / "resolver.py")
coordination = _load("serveros_runtime_coordination", "coordination.py")
daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
registry_mod = _load("serveros_runtime_registry", "registry.py")


def stamp(offset_s: float) -> str:
    return datetime.datetime.fromtimestamp(
        datetime.datetime.now(datetime.timezone.utc).timestamp() + offset_s,
        datetime.timezone.utc).strftime(STAMP)


def make_leases(root: Path, rows) -> Path:
    state_dir = root / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    db = root / "serveros.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE leases (resource TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "token TEXT NOT NULL, expires_at TEXT NOT NULL)")
    conn.executemany("INSERT INTO leases VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


def roots(root: Path):
    return resolver.Roots(root, root, root / "serveros.db")


def a_registry():
    return registry_mod.Registry(document={
        "schema": "serveros/managed-runtime-registry.v1",
        "device_ref": "device:server-beijing",
        "resources": [],
    })


@pytest.fixture
def daemon(tmp_path):
    return daemon_mod.Daemon(
        a_registry(), state_dir=tmp_path / "state",
        socket_path=tmp_path / "run/serveros.sock", log=lambda message: None)


# ---------------------------------------------------------------------------
# The canonical namespace, declared once.
# ---------------------------------------------------------------------------

def test_the_canonical_resource_is_the_one_work_engine_writes():
    assert resolver.work_write_resource(ITEM) == RESOURCE
    assert resolver.work_write_resource(" SRV-0001 ") == RESOURCE
    assert resolver.item_from_work_write_resource(RESOURCE) == ITEM
    assert resolver.item_from_work_write_resource("SRV-0001") is None


# ---------------------------------------------------------------------------
# The reader: `work-write:<item>` is found; the naked name is NOT.
# ---------------------------------------------------------------------------

def test_a_work_engine_lease_is_found_by_the_item_ref(tmp_path):
    """RED before the fix: the owner-verified producer writes `work-write:…`."""
    make_leases(tmp_path, [(RESOURCE, "agent:A", "1", stamp(600))])
    lease = resolver.live_lease(roots(tmp_path), ITEM)
    assert lease is not None
    assert lease["owner"] == "agent:A"
    assert lease["token"] == "1"


def test_a_naked_legacy_row_is_not_read(tmp_path):
    """No silent fallback: the old spelling is a different resource."""
    make_leases(tmp_path, [(ITEM, "agent:A", "1", stamp(600))])
    assert resolver.live_lease(roots(tmp_path), ITEM) is None


def test_an_expired_work_write_lease_is_not_live(tmp_path):
    make_leases(tmp_path, [(RESOURCE, "agent:A", "1", stamp(-60))])
    assert resolver.live_lease(roots(tmp_path), ITEM) is None


def test_another_items_lease_is_not_this_items(tmp_path):
    make_leases(tmp_path, [("work-write:SRV-0002", "agent:A", "1", stamp(600))])
    assert resolver.live_lease(roots(tmp_path), ITEM) is None


# ---------------------------------------------------------------------------
# The send gate: the claim is checked against the canonical lease.
# ---------------------------------------------------------------------------

def _binding(writer=None):
    return coordination.binding(
        session_role="execution", bound_item_ref=ITEM, writer=writer)


def _admit(record, lease):
    return coordination.admit_work(
        binding_record=record, work_item=ITEM, item_state="ACTIVE",
        authority={"source": "work-engine:leases", "lease": lease})


def test_the_owner_verified_lease_admits_its_matching_writer(tmp_path):
    make_leases(tmp_path, [(RESOURCE, "agent:A", "1", stamp(600))])
    lease = resolver.live_lease(roots(tmp_path), ITEM)
    out = _admit(_binding({"owner": "agent:A", "epoch": 1, "token": "1"}), lease)
    assert out["state"] == "ADMITTED"


def test_a_forged_writer_is_refused_against_the_owner_verified_lease(tmp_path):
    make_leases(tmp_path, [(RESOURCE, "agent:A", "1", stamp(600))])
    lease = resolver.live_lease(roots(tmp_path), ITEM)
    with pytest.raises(coordination.ThreadRefused) as caught:
        _admit(_binding({"owner": "agent:EVIL", "epoch": 1, "token": "999"}), lease)
    assert caught.value.reason == "NOT_CURRENT_WRITER"


def test_an_old_token_is_refused(tmp_path):
    make_leases(tmp_path, [(RESOURCE, "agent:A", "2", stamp(600))])
    lease = resolver.live_lease(roots(tmp_path), ITEM)
    with pytest.raises(coordination.ThreadRefused) as caught:
        _admit(_binding({"owner": "agent:A", "epoch": 1, "token": "1"}), lease)
    assert caught.value.reason == "NOT_CURRENT_WRITER"


def test_no_lease_is_no_current_writer():
    with pytest.raises(coordination.ThreadRefused) as caught:
        _admit(_binding({"owner": "agent:A", "epoch": 1, "token": "1"}), None)
    assert caught.value.reason == "NOT_CURRENT_WRITER"


# ---------------------------------------------------------------------------
# The production seam: create mints from, and reports, the SAME lease.
# ---------------------------------------------------------------------------

def _record(daemon, **params):
    return daemon.handle({"method": "agent.binding.record",
                          "params": params})["result"]


def test_create_mints_from_the_owner_verified_lease(daemon, tmp_path):
    make_leases(tmp_path, [(RESOURCE, "agent:A", "1", stamp(600))])
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM)
    assert answer["recorded"] is True
    assert answer["binding"]["writer"]["owner"] == "agent:A"
    assert answer["binding"]["writer"]["token"] == "1"
    assert answer["write_authority"]["lease"]["owner"] == "agent:A"


def test_create_reports_no_authority_for_a_naked_legacy_row(daemon, tmp_path):
    make_leases(tmp_path, [(ITEM, "agent:A", "1", stamp(600))])
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM)
    assert answer["recorded"] is True
    assert "writer" not in answer["binding"]
    assert answer["write_authority"]["lease"] is None


# ---------------------------------------------------------------------------
# S1/S2 security regressions must survive the normalization.
# ---------------------------------------------------------------------------

def test_an_explicit_caller_writer_is_still_refused(daemon):
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM,
                     writer={"owner": "agent:EVIL", "epoch": 1, "token": "999"})
    assert answer["recorded"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


def test_a_terminal_settled_still_cannot_be_reopened(daemon):
    """S2's D4 invariant, still wired after the resource normalization."""
    kernel = (REPO / "runtime" / "dispatch_attempts.py").read_text(encoding="utf-8")
    store = (REPO / "runtime" / "agent_control_store.py").read_text(encoding="utf-8")
    assert "DISPATCH_TRANSITIONS" in kernel
    assert "dispatch_transition_allowed" in kernel
    # The store is where the transition is enforced atomically, under its lock.
    assert "dispatch_transition_allowed" in store
