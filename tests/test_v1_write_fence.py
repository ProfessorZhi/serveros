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
coordination = _load("serveros_runtime_coordination", REPO / "runtime" / "coordination.py")

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
        # Both Sessions were created through `session.create`, so bindings for
        # both are recorded — which is the real shape, and it is what the
        # transfer requires: the target must be ESTABLISHED (recorded as this
        # Item's writer) before the source loses its authority (#237 B1).
        # The source is the scope's writer; the target is established (bound to
        # the same Item) but receives its claim only from the transfer. A
        # source with no claim has no write authority to move, which is the
        # distinction #310 F1/F2 found the old green tests were not making.
        self.bindings.bind(self.SOURCE, bound_item_ref=rt.ITEM, writer=writer())
        self.bindings.bind(self.TARGET, bound_item_ref=rt.ITEM)
        self.router = rt.router(self.service, bindings=self.bindings)

    def _send(self, instruction="more", key="k1"):
        return self.router.route(rt.router_mod.request_from_payload(rt.payload(
            "agent.session.send", rt.LOCAL, context_ref=rt.CONTEXT_REF,
            session_ref=self.SOURCE,
            params={"instruction": instruction, "turn_idempotency_key": key})))

    def _handoff(self, target=None, **params):
        return self.router.handoff(
            rt.router_mod.request_from_payload(rt.payload(
                "agent.session.handoff", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=self.SOURCE,
                params={"checkpoint": rt.CHECKPOINT, **params})),
            create_request=rt.router_mod.request_from_payload(rt.payload(
                "agent.session.resume", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=target or self.TARGET)))

    # -- the control: the source IS a writer before the handoff -------------

    def test_the_source_writes_before_the_handoff(self):
        response = self._send()
        self.assertTrue(response.ok, response.reason)

    # -- T1 ----------------------------------------------------------------

    def test_a_handoff_closes_the_source_binding(self):
        outcome = self._handoff()
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertIn("context_handed_off", outcome.completed)
        self.assertIn("write_authority_transferred", outcome.completed)
        record = self.bindings.get(self.SOURCE)["binding"]
        self.assertEqual(record["state"], "CLOSED",
                         "the source binding was left OPEN, so the stale source "
                         "is still an admitted writer")
        target = self.bindings.get(self.TARGET)
        self.assertIsInstance(target["binding"].get("writer"), dict,
                              "the target did not receive the write-authority claim")
        self.assertEqual(target["binding"]["writer"], target["current_writer"],
                         "the target's claim is not the current writer")
        self.assertNotEqual(target["current_writer"], record.get("writer"),
                            "the target did not take over the source's claim")

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


def writer(*, owner="agent:A", epoch=1, token="1", expires_at=None):
    w = {"owner": owner, "epoch": epoch, "token": token}
    if expires_at is not None:
        w["expires_at"] = expires_at
    return w


class TestHandoffWindow(unittest.TestCase):
    """#237 B1 — the source is revoked only once the TARGET is established.

    The window this closes: a handoff whose target ANSWERED with a session but
    was never RECORDED as the Item's writer. Closing the source on that alone
    leaves zero authorized writers, which a handoff cannot recover from.
    """

    SOURCE = "session:src:1"

    def setUp(self):
        self.service = rt.RecordingService()
        self.bindings = threads.MemoryBindings(unknown=threads.ASSUME_BOUND)
        self.bindings.bind(self.SOURCE, bound_item_ref=rt.ITEM, writer=writer())
        self.router = rt.router(self.service, bindings=self.bindings)

    def _send(self):
        return self.router.route(rt.router_mod.request_from_payload(rt.payload(
            "agent.session.send", rt.LOCAL, context_ref=rt.CONTEXT_REF,
            session_ref=self.SOURCE,
            params={"instruction": "more", "turn_idempotency_key": "k"})))

    def _handoff(self, target):
        return self.router.handoff(
            rt.router_mod.request_from_payload(rt.payload(
                "agent.session.handoff", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=self.SOURCE,
                params={"checkpoint": rt.CHECKPOINT})),
            create_request=rt.router_mod.request_from_payload(rt.payload(
                "agent.session.resume", rt.LOCAL, context_ref=rt.CONTEXT_REF,
                session_ref=target)))

    def test_a_target_with_a_session_but_no_binding_does_not_revoke_the_source(self):
        outcome = self._handoff("session:ghost:9")     # never bound
        self.assertFalse(outcome.ok,
                         "a handoff with no established target writer must not "
                         "report success")
        self.assertEqual(outcome.reason, "NOT_CURRENT_WRITER")
        self.assertIn("context_handed_off", outcome.completed)
        self.assertNotIn("write_authority_transferred", outcome.completed)
        self.assertEqual(self.bindings.get(self.SOURCE)["binding"]["state"], "OPEN")
        self.assertTrue(self._send().ok,
                        "the source was revoked on a session alone, leaving no "
                        "authorized writer")

    def test_a_target_bound_to_another_item_does_not_revoke_the_source(self):
        self.bindings.bind("session:elsewhere:7", bound_item_ref="LIT-0205")
        outcome = self._handoff("session:elsewhere:7")
        self.assertFalse(outcome.ok,
                         "a cross-Item target cannot receive write authority")
        self.assertEqual(outcome.reason, "NOT_CURRENT_WRITER")
        self.assertEqual(self.bindings.get(self.SOURCE)["binding"]["state"], "OPEN")

    def test_an_established_target_does_revoke_the_source(self):
        # The positive control: the same handoff, a target that IS recorded.
        self.bindings.bind("session:dst:2", bound_item_ref=rt.ITEM)
        outcome = self._handoff("session:dst:2")
        self.assertTrue(outcome.ok, outcome.reason)
        self.assertIn("write_authority_transferred", outcome.completed)
        self.assertEqual(self.bindings.get(self.SOURCE)["binding"]["state"], "CLOSED")
        target = self.bindings.get("session:dst:2")
        self.assertEqual(target["binding"]["writer"], target["current_writer"],
                         "the target's claim is not the current writer")


class TestWriterGateAtAdmission(unittest.TestCase):
    """#237 B1/T3 at the ROUTER seam — the writer gate wired into `_admit`.

    The kernel is tested in `TestWriterFenceKernel`. This is the wiring: the
    bindings handle supplies the current writer in the same read as the binding,
    and a `session.send` whose thread claims a superseded writer is refused with
    the transport's own word.
    """

    ITEM = "LIT-0101"
    SOURCE = "session:src:1"
    RIVAL = "session:rival:2"

    def setUp(self):
        self.service = rt.RecordingService()
        self.bindings = threads.MemoryBindings(unknown=threads.ASSUME_BOUND)

    def _router(self):
        return rt.router(self.service, bindings=self.bindings)

    def _send(self, ref):
        return self._router().route(rt.router_mod.request_from_payload(rt.payload(
            "agent.session.send", rt.LOCAL, context_ref=rt.CONTEXT_REF,
            session_ref=ref,
            params={"instruction": "more", "turn_idempotency_key": "k"})))

    def test_the_current_writer_is_admitted(self):
        self.bindings.bind(self.SOURCE, bound_item_ref=self.ITEM,
                           writer=writer(owner="agent:A", epoch=2, token="2"))
        response = self._send(self.SOURCE)
        self.assertTrue(response.ok, response.reason)

    def test_a_superseded_writer_is_refused(self):
        # The SOURCE claims epoch 1; a RIVAL holds epoch 2 for the same Item.
        self.bindings.bind(self.SOURCE, bound_item_ref=self.ITEM,
                           writer=writer(owner="agent:A", epoch=1, token="1"))
        self.bindings.bind(self.RIVAL, bound_item_ref=self.ITEM,
                           writer=writer(owner="agent:B", epoch=2, token="2"))
        response = self._send(self.SOURCE)
        self.assertFalse(response.ok)
        self.assertEqual(response.reason, "NOT_CURRENT_WRITER")

    def test_a_thread_that_claims_no_writer_is_not_gated(self):
        # Compatibility: a v1 binding claims nothing and every existing path
        # keeps working.
        self.bindings.bind(self.SOURCE, bound_item_ref=self.ITEM)
        response = self._send(self.SOURCE)
        self.assertTrue(response.ok, response.reason)

    def test_a_closed_rival_does_not_hold_the_scope(self):
        # The current writer is the highest OPEN claim; a closed binding's claim
        # is history, not authority.
        self.bindings.bind(self.SOURCE, bound_item_ref=self.ITEM,
                           writer=writer(owner="agent:A", epoch=3, token="3"))
        self.bindings.bind(self.RIVAL, bound_item_ref=self.ITEM, state="CLOSED",
                           writer=writer(owner="agent:B", epoch=4, token="4"))
        self.assertTrue(self._send(self.SOURCE).ok)


class TestWriterFenceKernel(unittest.TestCase):
    """The pure writer gate in `runtime/coordination.py` (#237 B1/T3).

    A thread that RECORDS a writer is claiming write authority, and the claim is
    checked against the current writer when it is supplied. A thread that
    records none never claimed it and is not gated — that is what keeps every
    pre-existing thread working.
    """

    ITEM = "LIT-0101"

    def _binding(self, **writer_kwargs):
        return coordination.binding(
            session_role="execution", bound_item_ref=self.ITEM,
            writer=writer(**writer_kwargs))

    # -- arming: a claim is gated, no claim is not -------------------------

    def test_a_binding_that_records_a_writer_is_v2(self):
        record = self._binding()
        self.assertEqual(record["schema"], coordination.SCHEMA_WRITER)
        self.assertEqual(record["writer"]["epoch"], 1)

    def test_a_binding_without_a_writer_stays_v1(self):
        record = coordination.binding(
            session_role="execution", bound_item_ref=self.ITEM)
        self.assertEqual(record["schema"], coordination.SCHEMA)
        self.assertNotIn("writer", record)

    def test_a_thread_that_records_no_writer_is_not_gated_while_the_scope_is_unowned(self):
        # Compatibility: a thread that claims nothing, on a scope nobody claims,
        # is every pre-existing thread. Gating it would break every normal path.
        record = coordination.binding(
            session_role="execution", bound_item_ref=self.ITEM)
        out = coordination.admit_work(
            binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
            current_writer=None)
        self.assertEqual(out["state"], "ADMITTED")
        self.assertEqual(out["writer_axis"], "UNKNOWN")

    def test_a_thread_that_records_no_writer_is_fenced_once_the_scope_is_owned(self):
        # The other half, and the one that stops a second writer: once a scope
        # has an owner, a thread claiming nothing is not the writer.
        record = coordination.binding(
            session_role="execution", bound_item_ref=self.ITEM)
        with self.assertRaises(coordination.ThreadRefused) as ctx:
            coordination.admit_work(
                binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
                current_writer=writer(owner="agent:B", epoch=9, token="9"))
        self.assertEqual(ctx.exception.reason, "NOT_CURRENT_WRITER")
        self.assertIn("second writer", ctx.exception.detail)

    # -- the gate ----------------------------------------------------------

    def test_the_current_writer_is_admitted(self):
        record = self._binding(owner="agent:A", epoch=3, token="7")
        out = coordination.admit_work(
            binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
            current_writer=writer(owner="agent:A", epoch=3, token="7"))
        self.assertEqual(out["state"], "ADMITTED")
        self.assertEqual(out["writer_axis"], "CURRENT")

    def test_a_stale_epoch_is_refused(self):
        record = self._binding(owner="agent:A", epoch=1, token="1")
        with self.assertRaises(coordination.ThreadRefused) as ctx:
            coordination.admit_work(
                binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
                current_writer=writer(owner="agent:A", epoch=2, token="2"))
        self.assertEqual(ctx.exception.reason, "NOT_CURRENT_WRITER")
        self.assertIn("superseded", ctx.exception.detail)

    def test_a_stale_token_is_refused(self):
        # Same owner, same epoch, a token that was promoted past — the case a
        # boolean "is this the owner" would wrongly admit (#237 T3).
        record = self._binding(owner="agent:A", epoch=3, token="1")
        with self.assertRaises(coordination.ThreadRefused) as ctx:
            coordination.admit_work(
                binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
                current_writer=writer(owner="agent:A", epoch=3, token="2"))
        self.assertEqual(ctx.exception.reason, "NOT_CURRENT_WRITER")

    def test_a_wrong_owner_is_refused(self):
        record = self._binding(owner="agent:A", epoch=3, token="7")
        with self.assertRaises(coordination.ThreadRefused) as ctx:
            coordination.admit_work(
                binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
                current_writer=writer(owner="agent:B", epoch=3, token="7"))
        self.assertEqual(ctx.exception.reason, "NOT_CURRENT_WRITER")

    def test_an_expired_claim_is_refused(self):
        record = self._binding(owner="agent:A", epoch=3, token="7",
                               expires_at=stamp(1000.0))
        with self.assertRaises(coordination.ThreadRefused) as ctx:
            coordination.admit_work(
                binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
                current_writer=writer(owner="agent:A", epoch=3, token="7"),
                now=stamp(1001.0))
        self.assertEqual(ctx.exception.reason, "NOT_CURRENT_WRITER")
        self.assertIn("expired", ctx.exception.detail)

    def test_a_claim_with_no_verifiable_source_is_refused(self):
        # #237: "when no verifiable source, explicit UNKNOWN/refuse".
        record = self._binding(owner="agent:A", epoch=3, token="7")
        with self.assertRaises(coordination.ThreadRefused) as ctx:
            coordination.admit_work(
                binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
                current_writer=None)
        self.assertEqual(ctx.exception.reason, "NOT_CURRENT_WRITER")
        self.assertIn("no current-writer record", ctx.exception.detail)

    # -- ordering: the four existing refusals still win --------------------

    def test_a_closed_thread_refusal_outranks_the_writer_refusal(self):
        record = self._binding(owner="agent:A", epoch=1, token="1")
        record["state"] = "CLOSED"
        with self.assertRaises(coordination.ThreadRefused) as ctx:
            coordination.admit_work(
                binding_record=record, work_item=self.ITEM, item_state="ACTIVE",
                current_writer=writer(owner="agent:B", epoch=9, token="9"))
        self.assertEqual(ctx.exception.reason, "THREAD_CLOSED_FOR_NEW_WORK")

    # -- shape validation --------------------------------------------------

    def test_a_coordinator_may_not_record_a_writer(self):
        with self.assertRaises(coordination.ThreadRefused) as ctx:
            coordination.binding(session_role="coordinator", writer=writer())
        self.assertEqual(ctx.exception.reason, "INVALID_CONTEXT")

    def test_a_writer_epoch_must_be_a_positive_integer_not_a_bool(self):
        for bad in (0, -1, True, "1", None):
            with self.assertRaises(coordination.ThreadRefused):
                coordination.normalize_writer({"owner": "a", "epoch": bad, "token": "1"})

    def test_a_writer_needs_an_owner_and_a_token(self):
        for bad in ({"epoch": 1, "token": "1"}, {"owner": "a", "epoch": 1}):
            with self.assertRaises(coordination.ThreadRefused):
                coordination.normalize_writer(bad)

    def test_writer_axis_reports_all_four_worlds(self):
        self.assertEqual(
            coordination.writer_axis(None), "UNKNOWN")
        self.assertEqual(
            coordination.writer_axis({"writer": None}), "UNKNOWN")
        self.assertEqual(
            coordination.writer_axis({"writer": writer(epoch=1, token="1")},
                                     current_writer=None), "UNKNOWN")
        self.assertEqual(
            coordination.writer_axis({"writer": writer(epoch=1, token="1")},
                                     current_writer=writer(epoch=2, token="2")),
            "STALE")
        self.assertEqual(
            coordination.writer_axis({"writer": writer(epoch=1, token="1")},
                                     current_writer=writer(epoch=1, token="1")),
            "CURRENT")


class TestWriterDerivation(unittest.TestCase):
    """The ONE derivation of who owns a write scope (#237 B1/T2).

    `current_writer` / `scope_has_writer` / `mint_writer` are pure, and the
    daemon, the gate and the test double all go through them — a second
    derivation would be a second answer to who may write.
    """

    ITEM = "LIT-0101"

    def test_no_claim_means_no_current_writer(self):
        b = {"s1": {"bound_item_ref": self.ITEM, "state": "OPEN"}}
        self.assertIsNone(coordination.current_writer(b, self.ITEM))
        self.assertFalse(coordination.scope_has_writer(b, self.ITEM))

    def test_the_newest_claim_wins(self):
        b = {
            "s1": {"bound_item_ref": self.ITEM, "state": "OPEN",
                   "writer": writer(owner="A", epoch=1, token="1")},
            "s2": {"bound_item_ref": self.ITEM, "state": "OPEN",
                   "writer": writer(owner="B", epoch=2, token="2")},
        }
        self.assertEqual(coordination.current_writer(b, self.ITEM)["owner"], "B")
        self.assertTrue(coordination.scope_has_writer(b, self.ITEM))

    def test_a_closed_binding_is_history_not_authority(self):
        b = {
            "s1": {"bound_item_ref": self.ITEM, "state": "OPEN",
                   "writer": writer(owner="A", epoch=1, token="1")},
            "s2": {"bound_item_ref": self.ITEM, "state": "CLOSED",
                   "writer": writer(owner="B", epoch=9, token="9")},
        }
        self.assertEqual(coordination.current_writer(b, self.ITEM)["owner"], "A")

    def test_a_closed_binding_alone_leaves_the_scope_unowned(self):
        # This is how a handoff frees the scope: the incumbent is closed and no
        # other thread claims it, so the successor may write.
        b = {"s1": {"bound_item_ref": self.ITEM, "state": "CLOSED",
                    "writer": writer(owner="A", epoch=1, token="1")}}
        self.assertIsNone(coordination.current_writer(b, self.ITEM))
        self.assertFalse(coordination.scope_has_writer(b, self.ITEM))

    def test_another_items_claim_is_not_this_scopes(self):
        b = {"s1": {"bound_item_ref": "LIT-0205", "state": "OPEN",
                    "writer": writer(owner="B", epoch=9, token="9")}}
        self.assertIsNone(coordination.current_writer(b, self.ITEM))

    def test_the_token_breaks_a_tie_within_an_epoch(self):
        b = {
            "s1": {"bound_item_ref": self.ITEM, "state": "OPEN",
                   "writer": writer(owner="A", epoch=1, token="1")},
            "s2": {"bound_item_ref": self.ITEM, "state": "OPEN",
                   "writer": writer(owner="B", epoch=1, token="2")},
        }
        self.assertEqual(coordination.current_writer(b, self.ITEM)["owner"], "B")

    def test_the_session_ref_is_the_final_tiebreak(self):
        # Two claims minted identically must still resolve to ONE winner rather
        # than to whichever dict was iterated last.
        b = {
            "s1": {"bound_item_ref": self.ITEM, "state": "OPEN",
                   "writer": writer(owner="A", epoch=1, token="1")},
            "s2": {"bound_item_ref": self.ITEM, "state": "OPEN",
                   "writer": writer(owner="B", epoch=1, token="1")},
        }
        self.assertEqual(coordination.current_writer(b, self.ITEM)["owner"], "B")
        self.assertEqual(coordination.current_writer(dict(reversed(list(b.items()))),
                                                      self.ITEM)["owner"], "B")

    def test_mint_starts_a_free_scope_at_epoch_1_token_1(self):
        claim = coordination.mint_writer({}, self.ITEM, owner="session-1")
        self.assertEqual(claim, {"owner": "session-1", "epoch": 1, "token": 1})

    def test_mint_advances_past_every_token_already_minted(self):
        b = {"s1": {"bound_item_ref": self.ITEM, "state": "OPEN",
                    "writer": writer(owner="A", epoch=1, token="4")}}
        claim = coordination.mint_writer(b, self.ITEM, owner="session-2")
        self.assertEqual(claim["epoch"], 1)
        self.assertEqual(claim["token"], 5)


class TestTheHandleCarriesTheWriter(unittest.TestCase):
    """The last hop: `DaemonBindings.get` must carry `current_writer` through.

    The daemon produces it, the router's `_admit` reads it, and this handle is
    the wire between them. A pass-through that dropped one field would leave the
    gate armed-but-blind in production and green in every other test — the exact
    shape `#237` is about. Loaded lazily because `mcp/agent_mcp.py` pulls the
    whole surface in.
    """

    def _agent_mcp(self):
        name = "serveros_test_agent_mcp"
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(
            name, REPO / "mcp" / "agent_mcp.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def test_the_daemons_writer_reaches_the_gate(self):
        agent_mcp = self._agent_mcp()
        claim = {"owner": "agent:A", "epoch": 1, "token": "1"}

        class Client:
            def call(self, method, params):
                self.method = method
                return {"found": True, "binding": {"session_role": "execution"},
                        "item": {"state": "ACTIVE"}, "current_writer": claim}

        handle = agent_mcp.DaemonBindings(Client())
        got = handle.get("session:1")
        self.assertEqual(got["current_writer"], claim)
        self.assertEqual(got["binding"]["session_role"], "execution")


if __name__ == "__main__":
    unittest.main()
