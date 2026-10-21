"""#237 T1 / #310 F1/F2 — the handoff through the REAL daemon bindings.

WHY THIS FILE EXISTS
--------------------
#310 found that the green handoff tests used `tests/threads.py::MemoryBindings`,
which has a `close`, while the production MCP loopback is built with
`mcp/agent_mcp.py::DaemonBindings`, which had none. `_revoke_source_write_authority`
therefore skipped revocation on the deployed path and the stale source stayed
the current writer. The target also never received an owner/epoch/token, so
closing the source would have left the scope merely unowned.

This file uses the REAL graph:

  * a real `runtime/daemon.py::Daemon` over a temp state dir and a real
    `serveros.db` holding a live Work lease;
  * the REAL `mcp/agent_mcp.py::DaemonBindings` over that daemon;
  * the REAL `fleet/agent_control_router.py` loopback and handoff;
  * a recording `SessionService` for the provider call — no real provider, no
    session, no model request.

It proves A -> B transfers the writer claim from the live lease, A is fenced,
B is admitted only with the live lease, a rival cannot compete, and an
unreachable daemon/stale source/restart/expired claim all fail closed or
recover as the records say.

LEVEL: LOCAL / HERMETIC. Temp daemon state and a temp SQLite lease table; the
socket path is never bound. No provider, network, credential or paid send.
"""
from __future__ import annotations

import datetime
import importlib.util
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


import test_agent_control_router as rt  # noqa: E402

daemon_mod = _load("serveros_runtime_daemon", REPO / "runtime" / "daemon.py")
agent_mcp = _load("serveros_mcp_agent_mcp_s1_handoff",
                  REPO / "mcp" / "agent_mcp.py")
router_mod = rt.router_mod
coordination = _load("serveros_runtime_coordination",
                     REPO / "runtime" / "coordination.py")

SOURCE = "session:src:1"
TARGET = "session:dst:2"
RIVAL = "session:rival:3"
LEASE_RESOURCE = f"work-write:{rt.ITEM}"


class LocalDaemonHandle:
    """The `DaemonBindings` handle over an in-process real daemon."""

    def __init__(self, daemon) -> None:
        self._daemon = daemon

    def call(self, method: str, params: dict) -> dict:
        answer = self._daemon.handle(
            {"method": method, "params": params},
            peer={"pid": os.getpid(), "uid": os.getuid(), "gid": os.getgid()})
        if not answer.get("ok"):
            error = answer.get("error") or {}
            raise RuntimeError(f"{error.get('code')}: {error.get('message')}")
        return answer.get("result") or {}


class UnreachableHandle:
    def call(self, method: str, params: dict) -> dict:
        raise OSError("daemon is not reachable")


class CloseFailingBindings:
    """The real bindings, except that the close transport fails.

    The claim half still reaches the daemon, so the handoff can reach the
    pre-claimed state; the close failure is the atomicity case under test.
    """

    def __init__(self, inner) -> None:
        self._inner = inner

    def get(self, ref):
        return self._inner.get(ref)

    def claim(self, ref, *, from_ref, context_ref=None):
        return self._inner.claim(ref, from_ref=from_ref, context_ref=context_ref)

    def close(self, *args, **kwargs):
        raise router_mod.TransportFailure(
            "NODE_RUNTIME_UNAVAILABLE", "the source close transport failed")


class CloseRaisingBindings(CloseFailingBindings):
    """The real bindings, except that close raises a plain provider error."""

    def close(self, *args, **kwargs):
        raise RuntimeError("the source close exploded")


class HandoffDaemonBindingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.root = Path(self._td.name)
        (self.root / "run").mkdir()
        self.daemon = self._daemon()
        self.service = rt.RecordingService()
        self.handle = LocalDaemonHandle(self.daemon)
        self.bindings = agent_mcp.DaemonBindings(self.handle)
        self.router = self._router(self.bindings, self.service)
        self.store = self.daemon.control_store()
        self.store.put_context(rt.CONTEXT)
        self._lease(owner="agent:A", token="1")
        self._record(SOURCE)
        self._record(TARGET)

    # -- fixture -----------------------------------------------------------

    def _daemon(self, *, state_name="state", socket_name="s.sock"):
        registry = daemon_mod.registry_mod.Registry(document={
            "schema": "serveros/managed-runtime-registry.v1",
            "device_ref": rt.LOCAL,
            "resources": [],
        })
        return daemon_mod.Daemon(
            registry, state_dir=self.root / state_name,
            socket_path=self.root / "run" / socket_name, log=lambda message: None)

    def _router(self, bindings, service):
        loopback = router_mod.LocalLoopbackTransport(
            device_ref=rt.LOCAL, service=service, bindings=bindings)
        return router_mod.FleetAgentControlRouter(
            local_device_ref=rt.LOCAL, transports=[loopback],
            context_store={rt.CONTEXT_REF: rt.CONTEXT}, attempts=None)

    def _lease(self, *, owner: str, token: str, ttl: int = 600) -> None:
        db = self.root / "serveros.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
            "owner TEXT NOT NULL, token TEXT NOT NULL, expires_at TEXT NOT NULL)")
        expires = datetime.datetime.fromtimestamp(
            time.time() + ttl, datetime.timezone.utc).strftime(
                "%Y-%m-%dT%H:%M:%SZ")
        conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                     (LEASE_RESOURCE, owner, token, expires))
        conn.commit()
        conn.close()

    def _lease_raw(self, *, owner: str, token: str,
                   expires_at: str) -> None:
        db = self.root / "serveros.db"
        conn = sqlite3.connect(db)
        conn.execute(
            "CREATE TABLE IF NOT EXISTS leases (resource TEXT PRIMARY KEY, "
            "owner TEXT NOT NULL, token TEXT NOT NULL, expires_at TEXT NOT NULL)")
        conn.execute("INSERT OR REPLACE INTO leases VALUES (?,?,?,?)",
                     (LEASE_RESOURCE, owner, token, expires_at))
        conn.commit()
        conn.close()

    def _drop_lease(self) -> None:
        conn = sqlite3.connect(self.root / "serveros.db")
        conn.execute("DELETE FROM leases WHERE resource=?", (LEASE_RESOURCE,))
        conn.commit()
        conn.close()

    def _record(self, ref: str, *, context_ref: str = rt.CONTEXT_REF,
                store=None, handle=None) -> dict:
        handle = handle or self.handle
        return handle.call("agent.binding.record", {
            "context_ref": context_ref,
            "session_ref": ref,
            "session_role": "execution",
            "bound_item_ref": rt.ITEM,
            "device_ref": rt.LOCAL,
        })

    def _context(self, *, status="VERIFIED", provenance="FACT",
                 actor_ref="human:A"):
        now = daemon_mod._iso(self.daemon._now())
        return daemon_mod.actor_mod.build({
            "human_control_provider": "provider:test",
            "transport": "feishu",
            "transport_identity": "test",
            "conversation_ref": "oc_actor_gate",
            "message_ref": "om_actor_gate",
            "observed_at": now,
            "provider_status": status,
            "provenance": provenance,
            "actor_ref": actor_ref,
            "provider_pid": os.getpid(),
        }, now=now)

    def _send(self, ref: str, *, key="k1", router=None):
        router = router or self.router
        return router.route(router_mod.request_from_payload(rt.payload(
            "agent.session.send", rt.LOCAL, context_ref=rt.CONTEXT_REF,
            session_ref=ref,
            params={"instruction": "more", "turn_idempotency_key": key})))

    def _handoff(self, *, router=None, target=TARGET):
        router = router or self.router
        return router.handoff(
            router_mod.request_from_payload(rt.payload(
                "agent.session.handoff", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=SOURCE,
                params={"checkpoint": rt.CHECKPOINT})),
            create_request=router_mod.request_from_payload(rt.payload(
                "agent.session.resume", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=target)))

    def _current(self, store=None):
        store = store or self.store
        return coordination.current_writer(store.bindings(), rt.ITEM)

    # -- the control -------------------------------------------------------

    def test_the_source_writes_and_the_target_without_a_claim_is_fenced(self):
        self.assertTrue(self._send(SOURCE).ok)
        target = self._send(TARGET, key="k2")
        self.assertFalse(target.ok)
        self.assertEqual(target.reason, "NOT_CURRENT_WRITER")
        self.assertEqual(self.store.get_binding(TARGET).get("writer"), None)

    # -- the transfer ------------------------------------------------------

    def test_the_handoff_moves_the_writer_claim_to_the_target(self):
        # The owner-approved Work lease moves to the target's principal before
        # the handoff. The daemon mints the target's claim from THIS lease; it
        # is not a second counter and not a caller string.
        self._lease(owner="agent:B", token="2")
        outcome = self._handoff()
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertIn("write_authority_transferred", outcome.completed)

        source = self.store.get_binding(SOURCE)
        target = self.store.get_binding(TARGET)
        self.assertEqual(source["state"], "CLOSED")
        self.assertEqual(target["writer"], self._current())
        self.assertEqual(target["writer"]["owner"], "agent:B")
        self.assertEqual(str(target["writer"]["token"]), "2")

        stale = self._send(SOURCE, key="k3")
        self.assertFalse(stale.ok)
        self.assertEqual(stale.reason, "THREAD_CLOSED_FOR_NEW_WORK")
        self.assertTrue(self._send(TARGET, key="k4").ok)

    def test_the_target_is_admitted_only_with_a_live_work_lease(self):
        self._lease(owner="agent:B", token="2")
        self.assertTrue(self._handoff().ok)
        self._drop_lease()
        denied = self._send(TARGET, key="k5")
        self.assertFalse(denied.ok)
        self.assertEqual(denied.reason, "NOT_CURRENT_WRITER")

    def test_no_lease_transfer_is_fail_closed_and_keeps_the_source(self):
        # The lease still names A (owner agent:A token 1). The handoff cannot
        # mint a target claim from the same lease; it is not a transfer.
        outcome = self._handoff()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "NOT_CURRENT_WRITER")
        self.assertNotIn("write_authority_transferred", outcome.completed)
        self.assertEqual(self.store.get_binding(SOURCE)["state"], "OPEN")
        self.assertTrue(self._send(SOURCE, key="k6").ok)

    def test_a_rival_cannot_compete_for_the_scope(self):
        self._lease(owner="agent:B", token="2")
        self.assertTrue(self._handoff().ok)
        self._record(RIVAL)
        rival = self.store.get_binding(RIVAL)
        self.assertIsNone(rival.get("writer"))
        denied = self._send(RIVAL, key="k7")
        self.assertFalse(denied.ok)
        self.assertEqual(denied.reason, "NOT_CURRENT_WRITER")

    # -- failure modes -----------------------------------------------------

    def test_an_unreachable_daemon_is_fail_closed(self):
        bad = agent_mcp.DaemonBindings(UnreachableHandle())
        outcome = self._handoff(router=self._router(bad, rt.RecordingService()))
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "NODE_RUNTIME_UNAVAILABLE")
        self.assertEqual(self.store.get_binding(SOURCE)["state"], "OPEN")

    def test_a_stale_source_cannot_transfer_authority(self):
        # A rival claim outranks the source before the handoff. The source is
        # no longer the current writer, so it cannot hand the scope to anyone.
        self.store.put_binding({
            "schema": "serveros/thread-binding.v2",
            "session_ref": RIVAL, "session_role": "execution",
            "bound_item_ref": rt.ITEM, "bound_item_ref_frozen": True,
            "state": "OPEN",
            "writer": {"owner": "agent:C", "epoch": 1, "token": "99"},
        })
        outcome = self._handoff()
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "NOT_CURRENT_WRITER")
        self.assertEqual(self.store.get_binding(SOURCE)["state"], "OPEN")

    def test_a_restart_keeps_the_transferred_claim(self):
        self._lease(owner="agent:B", token="2")
        self.assertTrue(self._handoff().ok)

        restarted = self._daemon(state_name="state", socket_name="s2.sock")
        handle = LocalDaemonHandle(restarted)
        bindings = agent_mcp.DaemonBindings(handle)
        router = self._router(bindings, rt.RecordingService())
        store = restarted.control_store()

        self.assertEqual(store.get_binding(SOURCE)["state"], "CLOSED")
        self.assertEqual(store.get_binding(TARGET)["writer"],
                         coordination.current_writer(store.bindings(), rt.ITEM))
        stale = self._send(SOURCE, key="k8", router=router)
        self.assertFalse(stale.ok)
        self.assertEqual(stale.reason, "THREAD_CLOSED_FOR_NEW_WORK")
        self.assertTrue(self._send(TARGET, key="k9", router=router).ok)

    # -- the strong actor verdict at the mutation boundary ----------------

    def test_an_unverified_context_cannot_claim(self):
        self._lease(owner="agent:B", token="2")
        context = self._context(status="BLOCKED", provenance="UNKNOWN",
                                actor_ref="human:unverified")
        self.store.put_context(context)
        answer = self.handle.call("agent.binding.claim", {
            "session_ref": TARGET, "from_ref": SOURCE,
            "context_ref": context["context_ref"]})
        self.assertFalse(answer.get("claimed"), answer)
        self.assertEqual(answer.get("reason"), "UNTRUSTED_ACTOR")
        self.assertIsNone(self.store.get_binding(TARGET).get("writer"),
                          "an unverified actor minted a writer claim")

    def test_an_unverified_context_cannot_close_with_successor(self):
        self._lease(owner="agent:B", token="2")
        context = self._context(status="BLOCKED", provenance="UNKNOWN",
                                actor_ref="human:unverified")
        self.store.put_context(context)
        answer = self.handle.call("agent.binding.close", {
            "session_ref": SOURCE, "successor_ref": TARGET,
            "context_ref": context["context_ref"]})
        self.assertFalse(answer.get("closed"), answer)
        self.assertEqual(answer.get("reason"), "UNTRUSTED_ACTOR")
        self.assertEqual(self.store.get_binding(SOURCE)["state"], "OPEN")
        self.assertIsNone(self.store.get_binding(TARGET).get("writer"),
                          "an unverified actor transferred write authority")

    # -- the pre-claim is not free: every failure must say what it left --

    def test_a_target_provider_failure_after_preclaim_is_blocked_atomicity(self):
        self._lease(owner="agent:B", token="2")
        # The provider does not carry the target's resume. The support refusal
        # happens at the resolver's `support` step, AFTER the handoff has
        # already minted the target's writer claim from the live lease.
        router = self._router(self.bindings, rt.ok_service(support={
            "session.create": {"state": "SUPPORTED"},
            "session.inspect": {"state": "SUPPORTED"},
            "session.result": {"state": "SUPPORTED"},
            "session.send": {"state": "SUPPORTED"},
            "session.resume": {"state": "UNSUPPORTED",
                               "reason": "this provider does not resume"},
        }))
        outcome = self._handoff(router=router)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "BLOCKED_ATOMICITY")
        self.assertFalse(outcome.source_preserved,
                         "the target's preclaim was already current, so the "
                         "source's write authority is not preserved")
        self.assertEqual(self.store.get_binding(TARGET)["writer"],
                         self._current())
        self.assertEqual(self.store.get_binding(SOURCE)["state"], "OPEN")
        denied = self._send(SOURCE, key="k10", router=router)
        self.assertFalse(denied.ok)
        self.assertEqual(denied.reason, "NOT_CURRENT_WRITER")

    def test_a_close_transport_failure_after_preclaim_is_blocked_atomicity(self):
        self._lease(owner="agent:B", token="2")
        router = self._router(CloseFailingBindings(self.bindings), self.service)
        outcome = self._handoff(router=router)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "BLOCKED_ATOMICITY")
        self.assertFalse(outcome.source_preserved,
                         "the target's preclaim was already current, so the "
                         "source's write authority is not preserved")
        self.assertEqual(self.store.get_binding(TARGET)["writer"],
                         self._current())
        self.assertEqual(self.store.get_binding(SOURCE)["state"], "OPEN")
        denied = self._send(SOURCE, key="k11", router=router)
        self.assertFalse(denied.ok)
        self.assertEqual(denied.reason, "NOT_CURRENT_WRITER")

    # -- the ADMISSION clock, not only the report flag --------------------

    def test_an_expired_offset_lease_is_refused_at_real_admission(self):
        """The lease instant is two hours past; its +08:00 text sorts later.

        The temp `leases` row is filtered by SQL text and then checked by
        `coordination.admit_work`; both used to see the local text as "later
        than now" and admit the stale worker. The same instant as a zoned
        `datetime` is expired, so the send must be refused before the provider.
        """
        expired = (datetime.datetime.now(datetime.timezone.utc)
                   - datetime.timedelta(hours=2)).astimezone(
                       datetime.timezone(datetime.timedelta(hours=8))
                   ).isoformat()
        self._lease_raw(owner="agent:A", token="1", expires_at=expired)
        response = self._send(SOURCE, key="k-offset")
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "NOT_CURRENT_WRITER")
        self.assertNotIn("send", self.service.names(),
                         "an expired lease reached the provider")

    # -- provider exceptions after the preclaim ---------------------------

    def test_a_provider_resume_exception_after_preclaim_is_blocked_atomicity(self):
        self._lease(owner="agent:B", token="2")
        service = rt.RecordingService(
            refuse={"on": "resume", "message": "provider exploded"})
        router = self._router(self.bindings, service)
        try:
            outcome = self._handoff(router=router)
        except Exception as exc:  # noqa: BLE001 - the escape is the defect
            self.fail(f"handoff raised {type(exc).__name__}: {exc}; a "
                      f"pre-claimed scope must return BLOCKED_ATOMICITY")
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "BLOCKED_ATOMICITY")
        self.assertFalse(outcome.source_preserved)
        self.assertIs(outcome.detail.get("scope_stalled"), True)
        self.assertEqual(self.store.get_binding(TARGET)["writer"],
                         self._current())
        self.assertEqual(self.store.get_binding(SOURCE)["state"], "OPEN")
        self._send(SOURCE, key="k12", router=router)
        self.assertNotIn("send", service.names(),
                         "a denied source reached the provider")

    def test_a_source_close_exception_after_preclaim_is_blocked_atomicity(self):
        self._lease(owner="agent:B", token="2")
        router = self._router(CloseRaisingBindings(self.bindings), self.service)
        outcome = self._handoff(router=router)
        self.assertFalse(outcome.ok)
        self.assertEqual(outcome.reason, "BLOCKED_ATOMICITY")
        self.assertFalse(outcome.source_preserved)
        self.assertIs(outcome.detail.get("scope_stalled"), True)
        self.assertEqual(self.store.get_binding(TARGET)["writer"],
                         self._current())
        self.assertEqual(self.store.get_binding(SOURCE)["state"], "OPEN")
        self._send(SOURCE, key="k13", router=router)
        self.assertNotIn("send", self.service.names())

    def test_an_expired_source_claim_is_recovered_by_the_transfer(self):
        """The clock moves, the source claim expires, the handoff recovers."""
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "run").mkdir()
            self.root = root
            daemon = self._daemon()
            handle = LocalDaemonHandle(daemon)
            bindings = agent_mcp.DaemonBindings(handle)
            router = self._router(bindings, rt.RecordingService())
            store = daemon.control_store()
            store.put_context(rt.CONTEXT)
            # Two hours in the past, expressed in +08:00. The UTC instant is
            # expired; the LOCAL text sorts AFTER a UTC now at the same date,
            # which is exactly the lexicographic trap #307 names.
            expired = (datetime.datetime.now(datetime.timezone.utc)
                       - datetime.timedelta(hours=2)).astimezone(
                           datetime.timezone(datetime.timedelta(hours=8))
                       ).isoformat()
            store.put_binding({
                "schema": "serveros/thread-binding.v2",
                "session_ref": SOURCE, "session_role": "execution",
                "bound_item_ref": rt.ITEM, "bound_item_ref_frozen": True,
                "state": "OPEN",
                "writer": {"owner": "agent:A", "epoch": 1, "token": "1",
                           "expires_at": expired},
            })
            store.put_binding({
                "schema": "serveros/thread-binding.v1",
                "session_ref": TARGET, "session_role": "execution",
                "bound_item_ref": rt.ITEM, "bound_item_ref_frozen": True,
                "state": "OPEN",
            })
            self._lease(owner="agent:B", token="2")

            outcome = router.handoff(
                router_mod.request_from_payload(rt.payload(
                    "agent.session.handoff", rt.LOCAL,
                    context_ref=rt.CONTEXT_REF, session_ref=SOURCE,
                    params={"checkpoint": rt.CHECKPOINT})),
                create_request=router_mod.request_from_payload(rt.payload(
                    "agent.session.resume", rt.LOCAL,
                    context_ref=rt.CONTEXT_REF, session_ref=TARGET)))
            self.assertTrue(outcome.ok, outcome.reason)
            self.assertEqual(store.get_binding(SOURCE)["state"], "CLOSED")
            self.assertEqual(store.get_binding(TARGET)["writer"],
                             coordination.current_writer(store.bindings(), rt.ITEM))
            self.assertIs(
                outcome.detail["revocation"]["revoked"]["source_claim_expired"],
                True, "the expired source claim was not reported as recovered")


if __name__ == "__main__":
    unittest.main()
