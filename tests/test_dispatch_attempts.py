#!/usr/bin/env python3
"""#245 / S2 — the durable dispatch attempt, and the outcome it refuses to guess.

THE FAILURE THIS IS FOR
-----------------------
The router's idempotency guard is `fleet/agent_control_router.py#_replay`, and it
is honest about itself: `agent-control-contract.yaml#idempotency.durability` says
in-process, per router instance. Two consequences followed, and #245 names both:

  * the same key on the same action AND device but a DIFFERENT payload was
    answered with the first call's result — the guard compared `action` and
    `device_ref` and never the payload, so a retry that changed the instruction
    was silently swallowed as a replay of a different intent (T5);
  * restart the router and every recorded attempt is gone, so a retry after a
    lost response is a SECOND effect — and where the first effect's outcome is
    not known, dispatching again is a blind retry (T4/T9).

WHAT THIS FILE TESTS
--------------------
The narrow module `runtime/dispatch_attempts.py` decides, from a RECORDED
attempt, what an arriving intent may do. The record is durable: it lives in the
existing Agent Control store (`runtime/agent_control_store.py#dispatch_attempts`),
not in a new database — the store is the owner #245 names for it.

Hermetic: temp SQLite-free JSON store, no clock, no machine, no provider.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


attempts = _load("serveros_runtime_dispatch_attempts", "dispatch_attempts.py")
store_mod = _load("serveros_runtime_agent_control_store", "agent_control_store.py")

KEY = "t-1"
SEND = {"action": "agent.session.send", "device_ref": "device:tokyo",
        "verb": "session.send", "params": {"instruction": "do it"}}


def digest(**over):
    payload = dict(SEND)
    payload.update(over)
    return attempts.intent_digest(**payload)


class TestIntentDigest(unittest.TestCase):
    def test_the_same_intent_digests_the_same(self):
        self.assertEqual(digest(), digest())

    def test_a_changed_parameter_changes_the_digest(self):
        self.assertNotEqual(digest(), digest(params={"instruction": "do it twice"}))

    def test_parameter_key_order_does_not_changes_the_digest(self):
        a = attempts.intent_digest(action="a", device_ref="d", verb="v",
                                   params={"x": 1, "y": 2})
        b = attempts.intent_digest(action="a", device_ref="d", verb="v",
                                   params={"y": 2, "x": 1})
        self.assertEqual(a, b)

    def test_the_digest_is_stable_across_processes(self):
        # Literal, so a change to the canonical form is a deliberate edit here.
        self.assertRegex(digest(), r"^[0-9a-f]{64}$")


class TestReconcile(unittest.TestCase):
    """The kernel: what an arriving intent may do, from what was recorded."""

    def _recorded(self, phase, **over):
        rec = attempts.attempt_record(
            key=KEY, action=SEND["action"], device_ref=SEND["device_ref"],
            verb=SEND["verb"], intent_digest=digest(), phase=phase,
            work_item="SRV-0001", principal="human:hwz", runtime="agent-runtime:claude-code")
        rec.update(over)
        return rec

    def test_no_record_means_proceed(self):
        self.assertEqual(attempts.reconcile(None, digest=digest())["verdict"], "PROCEED")

    def test_a_settled_attempt_replays(self):
        v = attempts.reconcile(self._recorded(attempts.SETTLED, result={"ok": True}),
                               digest=digest())
        self.assertEqual(v["verdict"], "REPLAY")
        self.assertEqual(v["result"], {"ok": True})

    def test_the_same_key_with_a_different_payload_is_a_conflict(self):
        # #245 T5. Same key, same action, same device — a DIFFERENT payload.
        v = attempts.reconcile(self._recorded(attempts.SETTLED),
                               digest=digest(params={"instruction": "something else"}))
        self.assertEqual(v["verdict"], "CONFLICT")

    def test_an_unknown_outcome_is_never_retried(self):
        # #245 T4/T9. The worst retry: dispatching again when the first effect's
        # outcome is not known.
        v = attempts.reconcile(self._recorded(attempts.EFFECT_UNKNOWN), digest=digest())
        self.assertEqual(v["verdict"], "REFUSE_UNKNOWN")

    def test_an_in_flight_attempt_is_not_dispatched_twice(self):
        v = attempts.reconcile(self._recorded(attempts.ACCEPTED), digest=digest())
        self.assertEqual(v["verdict"], "IN_FLIGHT")

    def test_a_superseded_attempt_is_refused(self):
        v = attempts.reconcile(self._recorded(attempts.SUPERSEDED), digest=digest())
        self.assertEqual(v["verdict"], "REFUSE_SUPERSEDED")

    def test_a_call_that_was_never_sent_may_proceed(self):
        v = attempts.reconcile(self._recorded(attempts.CALL_NOT_SENT), digest=digest())
        self.assertEqual(v["verdict"], "PROCEED")


class TestPhaseAfterFailure(unittest.TestCase):
    """Which phase a failed dispatch leaves the attempt in (#245)."""

    def test_a_transport_that_could_not_deliver_stays_call_not_sent(self):
        for reason in ("DEVICE_UNREACHABLE", "NODE_RUNTIME_UNAVAILABLE"):
            self.assertEqual(attempts.phase_after_transport_failure(reason),
                             attempts.CALL_NOT_SENT, reason)

    def test_an_ambiguous_failure_becomes_effect_unknown(self):
        for reason in ("TIMEOUT", "PROVIDER_ERROR", "TURN_FAILED", "", None):
            self.assertEqual(attempts.phase_after_transport_failure(reason),
                             attempts.EFFECT_UNKNOWN, reason)


class TestTheRecordShape(unittest.TestCase):
    def test_the_record_carries_what_245_asks_for(self):
        rec = attempts.attempt_record(
            key=KEY, action=SEND["action"], device_ref=SEND["device_ref"],
            verb=SEND["verb"], intent_digest=digest(), phase=attempts.ACCEPTED,
            work_item="SRV-0001", principal="human:hwz",
            runtime="agent-runtime:claude-code", grant_ref="grant:abc",
            provider_locator="provider-session:xyz", at="2026-10-17T00:00:00Z")
        for field in ("key", "action", "device_ref", "verb", "intent_digest",
                      "phase", "work_item", "principal", "runtime", "grant_ref",
                      "provider_locator", "schema"):
            self.assertIn(field, rec, field)

    def test_an_unknown_phase_is_refused(self):
        with self.assertRaises(attempts.AttemptRefused):
            attempts.attempt_record(key=KEY, action="a", device_ref="d", verb="v",
                                    intent_digest="x", phase="MAYBE")


class TestTheAttemptIsDurable(unittest.TestCase):
    """The record survives a restart because the STORE holds it (#245)."""

    def _store(self, root):
        return store_mod.AgentControlStore(Path(root) / "control.json")

    def test_an_attempt_written_by_one_process_is_read_by_the_next(self):
        with tempfile.TemporaryDirectory() as td:
            rec = attempts.attempt_record(
                key=KEY, action=SEND["action"], device_ref=SEND["device_ref"],
                verb=SEND["verb"], intent_digest=digest(),
                phase=attempts.EFFECT_UNKNOWN, at="2026-10-17T00:00:00Z")
            self._store(td).put_dispatch_attempt(rec)

            # A DIFFERENT store instance over the same file — a restart.
            restarted = self._store(td)
            recorded = restarted.get_dispatch_attempt(KEY)
            self.assertEqual(recorded["phase"], attempts.EFFECT_UNKNOWN)
            self.assertEqual(
                attempts.reconcile(recorded, digest=digest())["verdict"],
                "REFUSE_UNKNOWN")

    def test_the_store_defaults_the_section_without_migrating(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store(td)
            store.put_approval({"approval_ref": "a1", "action": "x"})
            self.assertEqual(store.dispatch_attempts(), {})


if __name__ == "__main__":
    unittest.main()
