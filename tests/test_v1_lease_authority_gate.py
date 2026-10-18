#!/usr/bin/env python3
"""S1 P0 (QA #256 Snapshot E, E5/E4): the send gate must read the Work Engine lease.

WHY THIS FILE EXISTS
--------------------
S1's send admission took its `current_writer` from the SERVEROS BINDING CLAIM.
`agent.binding.record` accepted a caller-supplied `writer={"owner": "agent:EVIL",
"token": 999}`, `agent.binding.resolve` reported it as the current writer, and
`admit_work` returned ADMITTED — while the Work Engine store held a live lease
`SRV-0001 = A/1` and refused a second writer. Two exclusive authorities
disagreed, and admission believed the one a caller can write.

The correction is not a second gate. It is that admission reads the SAME
authority the Work Engine writes: `context/resolver.py#live_lease` reads the
`leases` table (the table `workengine/store.py#acquire_lease` writes, in the same
`serveros.db`), and `admit_work` checks the thread's claim against it. No live
lease means no current writer; a mismatch is refused; the binding's writer is
minted FROM the lease rather than from its own counter.

Hermetic: temp SQLite, a temp store, a real `Daemon` over a temp root. No
provider, no network, no other repository.
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
store_mod = _load("serveros_runtime_agent_control_store", "agent_control_store.py")
router_mod = _load_at("serveros_fleet_agent_control_router",
                      REPO / "fleet" / "agent_control_router.py")

ITEM = "SRV-0001"


def stamp(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime(STAMP)


def now_epoch() -> float:
    return datetime.datetime.now(datetime.timezone.utc).timestamp()


def make_leases(root: Path, rows) -> Path:
    """The Work Engine lease table, at the place this daemon reads it."""
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


def roots(root: Path) -> "resolver.Roots":
    return resolver.Roots(root, root, root / "serveros.db")


# ---------------------------------------------------------------------------
# The reader: same table, same fixed-width stamp as `held_leases`.
# ---------------------------------------------------------------------------

def test_a_live_lease_is_returned_with_owner_and_token(tmp_path):
    make_leases(tmp_path, [(f"work-write:{ITEM}", "agent:A", "1", stamp(now_epoch() + 600))])
    lease = resolver.live_lease(roots(tmp_path), ITEM)
    assert lease["owner"] == "agent:A"
    assert lease["token"] == "1"


def test_an_expired_lease_is_not_a_live_lease(tmp_path):
    make_leases(tmp_path, [(f"work-write:{ITEM}", "agent:A", "1", stamp(now_epoch() - 60))])
    assert resolver.live_lease(roots(tmp_path), ITEM) is None


def test_an_absent_lease_is_not_a_live_lease(tmp_path):
    make_leases(tmp_path, [])
    assert resolver.live_lease(roots(tmp_path), ITEM) is None


# ---------------------------------------------------------------------------
# The kernel: the claim is checked against the LEASE, not against itself.
# ---------------------------------------------------------------------------

def _execution_binding(writer=None):
    return coordination.binding(
        session_role="execution", bound_item_ref=ITEM, writer=writer)


def _admit(record, lease):
    return coordination.admit_work(
        binding_record=record, work_item=ITEM, item_state="ACTIVE",
        authority={"source": "work-engine:leases", "lease": lease})


def test_a_spoofed_claim_is_refused_against_the_live_lease():
    """QA E5. The binding says EVIL/999; the Work Engine says A/1."""
    record = _execution_binding({"owner": "agent:EVIL", "epoch": 1, "token": "999"})
    with pytest.raises(coordination.ThreadRefused) as caught:
        _admit(record, {"owner": "agent:A", "token": "1"})
    assert caught.value.reason == "NOT_CURRENT_WRITER"


def test_no_live_lease_means_no_current_writer():
    record = _execution_binding({"owner": "agent:A", "epoch": 1, "token": "1"})
    with pytest.raises(coordination.ThreadRefused) as caught:
        _admit(record, None)
    assert caught.value.reason == "NOT_CURRENT_WRITER"


def test_a_thread_claiming_nothing_cannot_write_an_owned_scope():
    record = _execution_binding(None)
    with pytest.raises(coordination.ThreadRefused) as caught:
        _admit(record, {"owner": "agent:A", "token": "1"})
    assert caught.value.reason == "NOT_CURRENT_WRITER"


def test_the_current_lease_holder_is_admitted():
    record = _execution_binding({"owner": "agent:A", "epoch": 1, "token": "1"})
    out = _admit(record, {"owner": "agent:A", "token": "1"})
    assert out["state"] == "ADMITTED"


# ---------------------------------------------------------------------------
# The production seam: `agent.binding.record` / `resolve` / `_dispatch`.
# ---------------------------------------------------------------------------

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


def test_an_explicit_writer_is_refused(daemon):
    """QA E4. No trusted actor on this RPC means no caller-chosen writer."""
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM,
                     writer={"owner": "agent:EVIL", "epoch": 1, "token": "999"})
    assert answer["recorded"] is False
    assert answer["reason"] == "UNTRUSTED_ACTOR"


def test_the_claim_is_minted_from_the_live_lease(daemon, tmp_path):
    make_leases(tmp_path, [(f"work-write:{ITEM}", "agent:A", "1", stamp(now_epoch() + 600))])
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM)
    assert answer["recorded"] is True
    assert answer["binding"]["writer"]["owner"] == "agent:A"
    assert answer["binding"]["writer"]["token"] == "1"


def test_resolve_reports_the_lease_as_the_write_authority(daemon, tmp_path):
    make_leases(tmp_path, [(f"work-write:{ITEM}", "agent:A", "1", stamp(now_epoch() + 600))])
    _record(daemon, session_ref="session-1", session_role="execution",
            bound_item_ref=ITEM)
    read = daemon.handle({"method": "agent.binding.resolve",
                          "params": {"session_ref": "session-1"}})["result"]
    assert read["write_authority"]["source"] == "work-engine:leases"
    assert read["write_authority"]["lease"]["owner"] == "agent:A"
    assert read["write_authority"]["lease"]["token"] == "1"


def test_no_lease_means_no_claim_and_no_authority(daemon, tmp_path):
    make_leases(tmp_path, [])
    answer = _record(daemon, session_ref="session-1", session_role="execution",
                     bound_item_ref=ITEM)
    assert answer["recorded"] is True
    assert "writer" not in answer["binding"]
    read = daemon.handle({"method": "agent.binding.resolve",
                          "params": {"session_ref": "session-1"}})["result"]
    assert read["write_authority"]["lease"] is None


class _Handle:
    """The bindings handle the loopback reads, with a real authority."""

    def __init__(self, binding, lease):
        self._answer = {"binding": binding, "item": {"state": "ACTIVE"},
                        "write_authority": {"source": "work-engine:leases",
                                            "lease": lease}}

    def get(self, ref):
        return self._answer

    def put(self, record):
        return record

    def close(self, ref, **kwargs):
        return dict(self._answer["binding"], state="CLOSED")


class _Service:
    def __init__(self):
        self.calls = []

    def send(self, **kwargs):
        self.calls.append("send")
        return {"turn_state": "TURN_UNKNOWN"}

    def resume(self, **kwargs):
        self.calls.append("resume")
        return {"session_ref": kwargs.get("session_ref_value")}


def _send_request():
    return router_mod.AgentControlRequest(
        action="agent.session.send", device_ref="device:server-beijing",
        agent_runtime="agent-runtime:codex",
        params={"instruction": "more", "turn_idempotency_key": "t-lease"},
        session_ref="session:one")


def test_a_spoofed_claim_is_refused_at_the_dispatch_seam():
    """QA E5 at LocalLoopbackTransport._dispatch: the provider is not reached."""
    binding = coordination.binding(
        session_role="execution", bound_item_ref=ITEM,
        writer={"owner": "agent:EVIL", "epoch": 1, "token": "999"})
    service = _Service()
    loop = router_mod.LocalLoopbackTransport(
        device_ref="device:server-beijing", service=service,
        bindings=_Handle(binding, {"owner": "agent:A", "token": "1"}))
    with pytest.raises(router_mod.TransportFailure) as caught:
        loop._dispatch(_send_request())
    assert caught.value.reason == "NOT_CURRENT_WRITER"
    assert service.calls == []


def test_the_current_lease_holder_reaches_the_provider():
    binding = coordination.binding(
        session_role="execution", bound_item_ref=ITEM,
        writer={"owner": "agent:A", "epoch": 1, "token": "1"})
    service = _Service()
    loop = router_mod.LocalLoopbackTransport(
        device_ref="device:server-beijing", service=service,
        bindings=_Handle(binding, {"owner": "agent:A", "token": "1"}))
    loop._dispatch(_send_request())
    assert service.calls == ["send"]
