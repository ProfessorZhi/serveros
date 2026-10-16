#!/usr/bin/env python3
"""V1 write fencing — the two V1 blockers this branch closes (#237 T1, T6).

WHY THIS FILE EXISTS
--------------------
Issue #237 is an implementation-and-acceptance tracker, not a design note. Two
of its items are reachable here with nothing but existing authority, and this
file is their red-then-green receipt.

  T1  A Session that was handed off is still a writer.
      `fleet/agent_control_router.py::handoff` keeps the source Session alive
      (deliberately — a handoff must not lose the session), but it also left the
      source's *binding* OPEN. `THREAD_CLOSED_FOR_NEW_WORK` is exactly the word
      for this state — "the thread was closed while its provider session is
      still alive and accepting turns" — and the binding was never closed, so a
      stale `session.send` on the source was ADMITTED. The fix transfers write
      authority at `context_handed_off`: authority moves, the process does not
      have to.

  T6  A lease that expired is still read as held.
      `context/resolver.py::held_leases` is `SELECT resource FROM leases` with
      no `expires_at` filter, so an expired row — a crashed holder — was
      reported as a live claim and counted into `protected`.

WHAT IS AND IS NOT CLAIMED
--------------------------
The fence is enforced at the CONTROL-PLANE admission and at the Work-lease
record. It is NOT claimed to stop a shell side-effect inside a provider that
offers no native fence; a control plane that said so would be claiming a
guarantee the executor cannot give. Read-only verbs (`inspect`, `result`,
`interrupt`, `stop`) are deliberately NOT gated — closing a thread must not make
its closure unreadable.

Hermetic: temp databases and an in-memory bindings handle. No socket, no
network, no real ServerOS state.
"""
from __future__ import annotations

import datetime
import importlib.util
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

import threads

REPO = Path(__file__).resolve().parent.parent

# The router's own harness (its fake provider/service, its request builders).
# Imported rather than re-written: a second copy of "what a loopback needs" is a
# second answer to it, and `tests/threads.py` already made that argument for the
# bindings handle.
import test_agent_control_router as rt


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


resolver = _load("serveros_context_resolver", REPO / "context" / "resolver.py")

# The ONE lease stamp format, pinned in `work-engine/workengine/store.py` and
# consumed here by SQL string comparison. Second precision, fixed width, Z.
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def stamp(epoch: float) -> str:
    return datetime.datetime.fromtimestamp(
        epoch, datetime.timezone.utc).strftime(STAMP)


def state_db_with_leases(tmp: Path, rows: list[tuple[str, str, str, str]]) -> Path:
    """A real `serveros.db` holding the `leases` table and nothing else."""
    db = tmp / "serveros.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE leases (resource TEXT PRIMARY KEY, owner TEXT NOT NULL, "
        "token TEXT NOT NULL, expires_at TEXT NOT NULL)")
    conn.executemany("INSERT INTO leases VALUES (?,?,?,?)", rows)
    conn.commit()
    conn.close()
    return db


class TestHeldLeasesRespectsExpiry(unittest.TestCase):
    """T6 — an expired lease row is not a held lease."""

    def _roots(self, tmp: Path, rows):
        db = state_db_with_leases(tmp, rows)
        return resolver.Roots(tmp / "ws", tmp / "art", db)

    def test_an_expired_lease_is_not_reported_as_held(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._roots(tmp, [
                ("work-write:SRV-0001", "agent:A", "1", stamp(2000.0)),  # live
                ("work-write:SRV-0002", "agent:B", "2", stamp(1000.0)),  # expired
            ])
            self.assertEqual(
                resolver.held_leases(roots, now=1500.0), ["work-write:SRV-0001"])

    def test_a_live_lease_is_reported(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._roots(tmp, [
                ("browser:chromium", "agent:A", "1", stamp(2000.0))])
            self.assertEqual(resolver.held_leases(roots, now=1000.0),
                             ["browser:chromium"])

    def test_a_lease_expiring_exactly_now_is_not_held(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            roots = self._roots(tmp, [
                ("work-write:SRV-0001", "agent:A", "1", stamp(1500.0))])
            self.assertEqual(resolver.held_leases(roots, now=1500.0), [])


class TestHandoffRevokesSourceWriteAuthority(unittest.TestCase):
    """T1 — after a handoff, the source Session may read but not write."""

    SOURCE = "session:src:1"
    TARGET = "session:dst:2"

    def setUp(self):
        self.service = rt.RecordingService()
        self.bindings = threads.MemoryBindings(unknown=threads.ASSUME_BOUND)
        # The source Session was created through `session.create`, so a binding
        # for it is recorded — which is the real shape, and the one a close can
        # act on.
        self.bindings.bind(self.SOURCE, bound_item_ref=rt.ITEM)
        self.router = rt.router(self.service, bindings=self.bindings)

    def _send(self, instruction="more", key="k1"):
        return self.router.route(rt.router_mod.request_from_payload(rt.payload(
            "agent.session.send", rt.LOCAL, context_ref=rt.CONTEXT_REF,
            session_ref=self.SOURCE,
            params={"instruction": instruction, "turn_idempotency_key": key})))

    def _handoff(self, **params):
        return self.router.handoff(
            rt.router_mod.request_from_payload(rt.payload(
                "agent.session.handoff", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=self.SOURCE,
                params={"checkpoint": rt.CHECKPOINT, **params})),
            create_request=rt.router_mod.request_from_payload(rt.payload(
                "agent.session.resume", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=self.TARGET)))

    # -- the control: the source IS a writer before the handoff -------------

    def test_the_source_writes_before_the_handoff(self):
        response = self._send()
        self.assertTrue(response.ok, response.reason)

    # -- T1 ----------------------------------------------------------------

    def test_a_handoff_closes_the_source_binding(self):
        outcome = self._handoff()
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertIn("context_handed_off", outcome.completed)
        record = self.bindings.get(self.SOURCE)["binding"]
        self.assertEqual(record["state"], "CLOSED",
                         "the source binding was left OPEN, so the stale source "
                         "is still an admitted writer")

    def test_a_stale_send_after_handoff_is_refused(self):
        self._handoff()
        response = self._send(instruction="do more", key="k2")
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "THREAD_CLOSED_FOR_NEW_WORK")

    def test_a_stale_resume_after_handoff_is_refused(self):
        self._handoff()
        response = self.router.route(rt.router_mod.request_from_payload(rt.payload(
            "agent.session.resume", rt.LOCAL, context_ref=rt.CONTEXT_REF,
            session_ref=self.SOURCE)))
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "THREAD_CLOSED_FOR_NEW_WORK")

    def test_reading_the_source_after_handoff_is_not_gated(self):
        """The closure must not make the closed thread unreadable."""
        self._handoff()
        response = self.router.route(rt.router_mod.request_from_payload(rt.payload(
            "agent.session.inspect", rt.LOCAL, context_ref=rt.CONTEXT_REF,
            session_ref=self.SOURCE)))
        self.assertTrue(response.ok, response.reason)

    def test_authority_moves_even_when_the_source_process_stays_up(self):
        """Revoking write authority is not stopping the process, and the two
        are separate operations (#237 B1)."""
        outcome = self._handoff()   # release_source defaults to False
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertNotIn("stop", self.service.names(),
                         "the source process was stopped by a revocation")
        self.assertEqual(self.bindings.get(self.SOURCE)["binding"]["state"],
                         "CLOSED")

    def test_a_failed_handoff_does_not_revoke_the_source(self):
        """Nothing was handed over, so nothing is revoked."""
        outcome = self.router.handoff(
            rt.router_mod.request_from_payload(rt.payload(
                "agent.session.handoff", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=self.SOURCE,
                params={"checkpoint": rt.CHECKPOINT, "to_device_ref": rt.FJ,
                        "target_action": "agent.session.resume"})))
        self.assertFalse(outcome.ok)
        self.assertNotIn("context_handed_off", outcome.completed)
        self.assertEqual(self.bindings.get(self.SOURCE)["binding"]["state"], "OPEN")
        self.assertTrue(self._send().ok, "a failed handoff fenced the source")


if __name__ == "__main__":
    unittest.main()
