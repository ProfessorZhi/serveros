#!/usr/bin/env python3
"""S1 P0 follow-up (QA Snapshot G): create must say whether the thread can write.

THE GAP THIS PINS
-----------------
Since #261 the send admission reads the Work Engine lease. QA Snapshot G then
measured the other half: nothing in the product ACQUIRES that lease at session
creation, so a create on an Item with no lease records a binding that can never
send — and the caller only finds out on the first `session.send`.

The acquisition itself is BLOCKED_PRODUCER_BOUNDARY (see
`fleet/agent-control-contract.yaml#write_scope.producer_boundary_blocked`):
work-engine #13 exposes `acquire_lease` only as a Python method, with no CLI or
MCP boundary, and serveros has no call path to it. What this file verifies is the
fail-closed half that DOES exist and can be made honest now: `agent.binding.record`
reports `write_authority` (the same shape `agent.binding.resolve` returns), so a
caller learns at CREATE time that the thread cannot send.

Hermetic: temp SQLite, a real `Daemon` over a temp root.
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


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
registry_mod = _load("serveros_runtime_registry", "registry.py")


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


def stamp(offset_s: float) -> str:
    return datetime.datetime.fromtimestamp(
        datetime.datetime.now(datetime.timezone.utc).timestamp() + offset_s,
        datetime.timezone.utc).strftime(STAMP)


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


def _record(daemon, **params):
    return daemon.handle({"method": "agent.binding.record",
                          "params": params})["result"]


def test_create_reports_no_write_authority_without_a_lease(daemon, tmp_path):
    """The trap QA measured: a create that can never send, discovered later."""
    make_leases(tmp_path, [])
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM)
    assert answer["recorded"] is True
    assert answer["write_authority"]["source"] == "work-engine:leases"
    assert answer["write_authority"]["lease"] is None


def test_create_reports_the_live_lease_when_one_exists(daemon, tmp_path):
    make_leases(tmp_path, [(f"work-write:{ITEM}", "agent:A", "1", stamp(600))])
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM)
    assert answer["recorded"] is True
    assert answer["write_authority"]["lease"]["owner"] == "agent:A"
    assert answer["write_authority"]["lease"]["token"] == "1"


def test_an_expired_lease_is_reported_as_no_authority(daemon, tmp_path):
    make_leases(tmp_path, [(f"work-write:{ITEM}", "agent:A", "1", stamp(-60))])
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM)
    assert answer["recorded"] is True
    assert answer["write_authority"]["lease"] is None


def test_a_coordinator_reports_no_scope_authority(daemon, tmp_path):
    make_leases(tmp_path, [])
    answer = _record(daemon, session_ref="session-c", session_role="coordinator")
    assert answer["recorded"] is True
    assert answer["write_authority"]["lease"] is None
