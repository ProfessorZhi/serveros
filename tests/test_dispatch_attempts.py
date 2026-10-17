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


def recorded_unknown(*, principal="agent:A", owner="agent:A", work_item="SRV-0001",
                     **over):
    rec = attempts.attempt_record(
        key=KEY, action=SEND["action"], device_ref=SEND["device_ref"],
        verb=SEND["verb"], intent_digest=digest(), phase=attempts.EFFECT_UNKNOWN,
        work_item=work_item, principal=principal)
    rec.update(over)
    return rec


def observation(**over):
    obs = {"state": attempts.OBSERVED_APPLIED, "intent_digest": digest(),
           "source": "provider:turn-journal"}
    obs.update(over)
    return obs


def reconciler(**over):
    rec = {"principal": "agent:A", "owner": "agent:A", "epoch": 1, "token": "1"}
    rec.update(over)
    return rec


CURRENT_WRITER = {"owner": "agent:A", "epoch": 1, "token": "1"}


class TestIntentIdentity(unittest.TestCase):
    """#237 T5 / QA P0-1. The KEY identifies a TARGET, not just a payload.

    `intent_digest` compared action/device/verb/params and omitted the session,
    the runtime and the principal — so two sends to DIFFERENT sessions under one
    key digested identically and the second was answered as a replay of the
    first. The typed envelope that identifies an intent now includes them.
    """

    def _d(self, **over):
        base = dict(action="agent.session.send", device_ref="device:tokyo",
                    verb="session.send", params={"instruction": "x"})
        base.update(over)
        return attempts.intent_digest(**base)

    def test_a_different_session_target_is_a_different_intent(self):
        self.assertNotEqual(self._d(session_ref="session:a"),
                            self._d(session_ref="session:b"))

    def test_a_different_runtime_is_a_different_intent(self):
        self.assertNotEqual(self._d(agent_runtime="agent-runtime:codex"),
                            self._d(agent_runtime="agent-runtime:claude-code"))

    def test_a_different_principal_is_a_different_intent(self):
        self.assertNotEqual(self._d(principal="human:a"),
                            self._d(principal="human:b"))

    def test_the_same_target_and_principal_is_the_same_intent(self):
        self.assertEqual(
            self._d(session_ref="s", agent_runtime="r", principal="h"),
            self._d(session_ref="s", agent_runtime="r", principal="h"))

    def test_a_missing_target_is_not_the_same_as_an_empty_one(self):
        # `None` and "" are different claims; the canonical form must not
        # collapse them into one intent.
        self.assertNotEqual(self._d(session_ref=None),
                            self._d(session_ref=""))


class TestReconcileObservation(unittest.TestCase):
    """#245. EFFECT_UNKNOWN -> SETTLED only when an owner proves it.

    Fail closed: anything that is not an independent, same-intent, current-writer
    observation leaves the attempt at EFFECT_UNKNOWN.
    """

    def test_only_an_unknown_outcome_is_reconcilable(self):
        for phase in (attempts.CALL_NOT_SENT, attempts.ACCEPTED,
                      attempts.SETTLED, attempts.SUPERSEDED):
            rec = recorded_unknown()
            rec["phase"] = phase
            v = attempts.reconcile_observation(
                rec, observation=observation(), reconciler=reconciler(),
                current_writer=CURRENT_WRITER)
            self.assertEqual(v["verdict"], attempts.REFUSE_NOT_UNKNOWN, phase)

    def test_an_owner_observation_that_proves_applied_settles(self):
        v = attempts.reconcile_observation(
            recorded_unknown(), observation=observation(result={"turn": "t1"}),
            reconciler=reconciler(), current_writer=CURRENT_WRITER)
        self.assertEqual(v["verdict"], attempts.SETTLE_APPLIED)
        self.assertEqual(v["phase"], attempts.SETTLED)
        self.assertEqual(v["result"], {"turn": "t1"})

    def test_an_observation_that_proves_not_applied_frees_the_scope(self):
        v = attempts.reconcile_observation(
            recorded_unknown(), observation=observation(state=attempts.OBSERVED_NOT_APPLIED),
            reconciler=reconciler(), current_writer=CURRENT_WRITER)
        self.assertEqual(v["verdict"], attempts.SETTLE_NOT_APPLIED)
        self.assertEqual(v["phase"], attempts.CALL_NOT_SENT)

    def test_a_non_owner_may_not_reconcile(self):
        v = attempts.reconcile_observation(
            recorded_unknown(principal="agent:A"),
            observation=observation(), reconciler=reconciler(principal="agent:B"),
            current_writer=CURRENT_WRITER)
        self.assertEqual(v["verdict"], attempts.REFUSE_PRINCIPAL)

    def test_a_stale_writer_may_not_reconcile(self):
        v = attempts.reconcile_observation(
            recorded_unknown(), observation=observation(),
            reconciler=reconciler(epoch=1, token="1"),
            current_writer={"owner": "agent:A", "epoch": 2, "token": "2"})
        self.assertEqual(v["verdict"], attempts.REFUSE_STALE_WRITER)

    def test_no_current_writer_source_fails_closed(self):
        v = attempts.reconcile_observation(
            recorded_unknown(), observation=observation(), reconciler=reconciler(),
            current_writer=None)
        self.assertEqual(v["verdict"], attempts.REFUSE_UNKNOWN)

    def test_an_observation_about_another_intent_proves_nothing(self):
        v = attempts.reconcile_observation(
            recorded_unknown(),
            observation=observation(intent_digest=digest(params={"instruction": "x"})),
            reconciler=reconciler(), current_writer=CURRENT_WRITER)
        self.assertEqual(v["verdict"], attempts.REFUSE_UNKNOWN)

    def test_a_matching_provider_locator_identifies_the_intent(self):
        rec = recorded_unknown(provider_locator="provider-session:xyz")
        obs = {"state": attempts.OBSERVED_APPLIED,
               "provider_locator": "provider-session:xyz",
               "source": "provider:turn-journal"}
        v = attempts.reconcile_observation(
            rec, observation=obs, reconciler=reconciler(),
            current_writer=CURRENT_WRITER)
        self.assertEqual(v["verdict"], attempts.SETTLE_APPLIED)

    def test_an_undecidable_observation_stays_unknown(self):
        for state in ("UNKNOWN", "", None, "MAYBE"):
            v = attempts.reconcile_observation(
                recorded_unknown(), observation=observation(state=state),
                reconciler=reconciler(), current_writer=CURRENT_WRITER)
            self.assertEqual(v["verdict"], attempts.REFUSE_UNKNOWN, state)

    def test_a_late_reply_after_supersede_is_refused(self):
        rec = recorded_unknown()
        rec["phase"] = attempts.SUPERSEDED
        v = attempts.reconcile_observation(
            rec, observation=observation(), reconciler=reconciler(),
            current_writer=CURRENT_WRITER)
        self.assertEqual(v["verdict"], attempts.REFUSE_NOT_UNKNOWN)

    def test_nothing_recorded_cannot_be_reconciled(self):
        v = attempts.reconcile_observation(
            None, observation=observation(), reconciler=reconciler(),
            current_writer=CURRENT_WRITER)
        self.assertEqual(v["verdict"], attempts.REFUSE_UNKNOWN)

    def test_a_scope_with_no_work_item_skips_the_writer_check(self):
        # A coordinator send has no write scope; the principal check still holds.
        v = attempts.reconcile_observation(
            recorded_unknown(work_item=None), observation=observation(),
            reconciler=reconciler(), current_writer=None)
        self.assertEqual(v["verdict"], attempts.SETTLE_APPLIED)


class TestObservationProvenance(unittest.TestCase):
    """#245. An observation must say WHERE it came from, and a lookup MISS is not
    proof that the effect did not happen.

    These are the two rules that stop an adapter turning "I could not find it"
    into "it did not happen" — which would free the scope and let the next retry
    send the same effect a second time.
    """

    def test_an_observation_without_a_source_proves_nothing(self):
        for state in (attempts.OBSERVED_APPLIED, attempts.OBSERVED_NOT_APPLIED):
            obs = {"state": state, "intent_digest": digest()}   # no `source`
            v = attempts.reconcile_observation(
                recorded_unknown(), observation=obs, reconciler=reconciler(),
                current_writer=CURRENT_WRITER)
            self.assertEqual(v["verdict"], attempts.REFUSE_UNKNOWN, state)

    def test_a_lookup_miss_is_not_proof_of_absence(self):
        # The provider's own absence words describe a MISS, not a fact about the
        # effect. None of them may free the scope.
        for state in ("ABSENT", "NOT_FOUND", "MISSING", "TURN_NOT_FOUND"):
            obs = {"state": state, "intent_digest": digest(),
                   "source": "provider:trigger-result"}
            v = attempts.reconcile_observation(
                recorded_unknown(), observation=obs, reconciler=reconciler(),
                current_writer=CURRENT_WRITER)
            self.assertEqual(v["verdict"], attempts.REFUSE_UNKNOWN, state)

    def test_only_an_affirmative_not_applied_settles(self):
        v = attempts.reconcile_observation(
            recorded_unknown(),
            observation={"state": attempts.OBSERVED_NOT_APPLIED,
                         "intent_digest": digest(),
                         "source": "provider:durable-journal"},
            reconciler=reconciler(), current_writer=CURRENT_WRITER)
        self.assertEqual(v["verdict"], attempts.SETTLE_NOT_APPLIED)


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

    def test_a_reconciled_attempt_is_settled_on_disk_across_a_restart(self):
        with tempfile.TemporaryDirectory() as td:
            self._store(td).put_dispatch_attempt(recorded_unknown())
            verdict = self._store(td).reconcile_dispatch_attempt(
                KEY, observation=observation(result={"turn": "t1"}),
                reconciler=reconciler(), current_writer=CURRENT_WRITER)
            self.assertEqual(verdict["verdict"], attempts.SETTLE_APPLIED)
            # A different instance over the same file — a restart.
            self.assertEqual(self._store(td).get_dispatch_attempt(KEY)["phase"],
                             attempts.SETTLED)

    def test_a_refused_reconciliation_writes_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            self._store(td).put_dispatch_attempt(recorded_unknown())
            verdict = self._store(td).reconcile_dispatch_attempt(
                KEY, observation=observation(),
                reconciler=reconciler(principal="agent:B"),
                current_writer=CURRENT_WRITER)
            self.assertEqual(verdict["verdict"], attempts.REFUSE_PRINCIPAL)
            self.assertEqual(self._store(td).get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)

    def test_the_store_defaults_the_section_without_migrating(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._store(td)
            store.put_approval({"approval_ref": "a1", "action": "x"})
            self.assertEqual(store.dispatch_attempts(), {})


if __name__ == "__main__":
    unittest.main()
