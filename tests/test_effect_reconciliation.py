#!/usr/bin/env python3
"""#245 / S2 — the reconciliation DRIVER, and the answers it refuses to invent.

WHAT THIS FILE IS FOR
---------------------
`runtime/dispatch_attempts.py` decides, from a recorded attempt, what an
arriving intent may do. `runtime/agent_control_store.py#dispatch_attempts`
makes that record durable. Neither one ASKS anybody what happened to an
EFFECT_UNKNOWN attempt: the decision exists, the record exists, and the caller
that would connect them after a timeout or a reconnect did not.

This file tests that missing glue, `integrations/v1-effect-reconciliation/
effect_reconciliation.py`, against the four failures #245 / #237 names:

  T4  accepted, reply lost, controller restart, same key retried
  T5  same idempotency key, modified payload
  T9  provider unreachable / partial dispatch (unknown is not completion)
  T12 control plane gone, reconnect (no doubled effects on replay)

THE RULE THE DRIVER MUST NOT BREAK
----------------------------------
A provider that can only be asked by an id that arrived in the LOST response
cannot prove anything about that turn. That is `BLOCKED_PROVIDER_SOURCE`, and
the effect stays EFFECT_UNKNOWN. A lookup miss is NOT an absence, so it may
never free the scope for a second send. `NOT_APPLIED` is accepted only with
provenance and only from an authoritative source.

Hermetic: a temp JSON store, no clock, no machine, no provider request. The
driver holds no state of its own — every durable read and write goes through
the EXISTING Agent Control store, and the test proves no second ledger appears.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"
INTEGRATIONS = REPO / "integrations" / "v1-effect-reconciliation"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


attempts = _load("serveros_runtime_dispatch_attempts",
                 RUNTIME / "dispatch_attempts.py")
store_mod = _load("serveros_runtime_agent_control_store",
                  RUNTIME / "agent_control_store.py")

# RED, deliberately visible: before this slice the driver did not exist, and a
# missing feature should fail with a sentence rather than an import traceback.
try:
    driver_mod = _load("serveros_integrations_effect_reconciliation",
                       INTEGRATIONS / "effect_reconciliation.py")
except FileNotFoundError:
    driver_mod = None

PRINCIPAL = "human:hwz"
OTHER_PRINCIPAL = "human:someone-else"
ITEM = "SRV-0001"
#: The writer that holds the Item's write scope in these tests. A Work-scoped
#: reconciliation is a protected write, so the kernel refuses to settle one
#: without a current writer; supplying it is part of the scenario, not a bypass.
WRITER = {"owner": "agent:hwz", "epoch": 3, "token": "tok-3"}
KEY = "effect-key-1"
SEND = {"action": "agent.session.send", "device_ref": "device:tokyo",
        "verb": "session.send", "params": {"instruction": "do it"}}


def digest(**over) -> str:
    payload = dict(SEND)
    payload.update(over)
    return attempts.intent_digest(**payload)


def attempt(*, key=KEY, phase=attempts.EFFECT_UNKNOWN, principal=PRINCIPAL,
            work_item=ITEM, provider_locator=None, **over) -> dict:
    record = attempts.attempt_record(
        key=key, action=SEND["action"], device_ref=SEND["device_ref"],
        verb=SEND["verb"], intent_digest=digest(), phase=phase,
        work_item=work_item, principal=principal, provider_locator=provider_locator)
    record.update(over)
    return record


def observation(state="APPLIED", *, source="test:authoritative-owner-journal",
                intent_digest=None, provider_locator=None, result=None) -> dict:
    out = {"state": state, "source": source,
           "intent_digest": digest() if intent_digest is None else intent_digest}
    if provider_locator is not None:
        out["provider_locator"] = provider_locator
    if result is not None:
        out["result"] = result
    return out


class RecordingSource:
    """An observation source with a declared capability and a call count.

    The capability is the whole point: `CLIENT_TURN_ID` means the source can be
    asked by the id we still hold after the response was lost;
    `RESPONSE_ID_ONLY` means it needs an id that only the lost response had.
    """

    def __init__(self, capability, *, observation=None, exc=None, by_key=None):
        self.capability = capability
        self._observation = observation
        self._exc = exc
        self._by_key = by_key
        self.calls: list[str] = []

    def observe(self, recorded, *, key):
        self.calls.append(key)
        if self._exc is not None:
            raise self._exc
        if self._by_key is not None:
            return self._by_key(recorded, key=key)
        return self._observation


def store_at(td) -> "store_mod.AgentControlStore":
    return store_mod.AgentControlStore(Path(td) / "control.json")


def seeded(store, record=None):
    store.put_dispatch_attempt(record or attempt(), actor=PRINCIPAL)
    return record or attempt()


class DriverTest(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(
            driver_mod,
            "integrations/v1-effect-reconciliation/effect_reconciliation.py "
            "does not exist — there is no caller that asks the owner what "
            "happened to an EFFECT_UNKNOWN attempt")


class TestProviderCapabilityIsHonest(DriverTest):
    """A source that cannot be asked by the id we hold proves nothing."""

    def test_a_response_id_only_source_is_blocked_and_not_called(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.RESPONSE_ID_ONLY, observation=observation())
            driver = driver_mod.EffectReconciler(
                attempts=store, observation_source=source)
            result = driver.reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"],
                             driver_mod.BLOCKED_PROVIDER_SOURCE)
            self.assertEqual(source.calls, [],
                             "a source that needs the lost response id was asked "
                             "anyway, so its answer would be a guess")
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)

    def test_no_source_is_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            driver = driver_mod.EffectReconciler(
                attempts=store, observation_source=None)
            result = driver.reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"],
                             driver_mod.BLOCKED_PROVIDER_SOURCE)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)

    def test_a_client_turn_id_source_is_asked(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID,
                observation=observation(result={"ok": True}))
            driver = driver_mod.EffectReconciler(
                attempts=store, observation_source=source)
            result = driver.reconcile(KEY, principal=PRINCIPAL,
                                      writer=WRITER, current_writer=WRITER)
            self.assertEqual(source.calls, [KEY])
            self.assertEqual(result["status"], attempts.SETTLE_APPLIED)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.SETTLED)


class TestLookupMissIsNotAbsence(DriverTest):
    """A miss names something nobody found, not something nobody did."""

    def test_turn_not_found_stays_effect_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID,
                observation=observation("TURN_NOT_FOUND"))
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"], attempts.REFUSE_UNKNOWN)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN,
                             "a lookup miss freed the scope for a second send")

    def test_absent_and_missing_are_normalised_to_unknown(self):
        for word in ("ABSENT", "NOT_FOUND", "MISSING", "LOOKUP_MISS"):
            with self.subTest(word=word), tempfile.TemporaryDirectory() as td:
                store = store_at(td)
                seeded(store)
                source = RecordingSource(
                    driver_mod.CLIENT_TURN_ID,
                    observation=observation(word))
                result = driver_mod.EffectReconciler(
                    attempts=store, observation_source=source
                ).reconcile(KEY, principal=PRINCIPAL)
                self.assertEqual(result["status"], attempts.REFUSE_UNKNOWN, word)
                self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                                 attempts.EFFECT_UNKNOWN, word)

    def test_not_applied_without_provenance_settles_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID,
                observation={"state": "NOT_APPLIED", "intent_digest": digest()})
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"], attempts.REFUSE_UNKNOWN)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)


class TestAcceptedThenReplyLostAcrossRestart(DriverTest):
    """#237 T4: the record survives the controller, and the retry is refused."""

    def test_a_restart_cannot_turn_unknown_into_a_retry(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "control.json"
            first = store_mod.AgentControlStore(path)
            seeded(first)

            # A restart: a NEW store instance over the SAME file, and a source
            # that only knows the id from the response that was lost.
            restarted = store_mod.AgentControlStore(path)
            source = RecordingSource(
                driver_mod.RESPONSE_ID_ONLY, observation=observation())
            result = driver_mod.EffectReconciler(
                attempts=restarted, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)

            self.assertEqual(result["status"],
                             driver_mod.BLOCKED_PROVIDER_SOURCE)
            self.assertEqual(result["source_called"], False)
            self.assertEqual(source.calls, [])
            self.assertEqual(
                driver_mod.EffectReconciler(
                    attempts=restarted, observation_source=source
                ).retry_verdict(KEY, digest=digest())["verdict"],
                attempts.REFUSE_UNKNOWN)

    def test_a_late_observation_cannot_overwrite_a_settled_attempt(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store, attempt(phase=attempts.SETTLED, result={"ok": True}))
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID,
                observation=observation("NOT_APPLIED"))
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"], "NOT_UNKNOWN")
            self.assertEqual(source.calls, [],
                             "a late reply overwrote an attempt that had already "
                             "answered")
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.SETTLED)


class TestSameKeyDifferentPayload(DriverTest):
    """#237 T5: a key identifies one intent, not one caller."""

    def test_a_modified_payload_is_a_conflict_through_the_driver(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store, attempt(phase=attempts.SETTLED, result={"ok": True}))
            other = digest(params={"instruction": "something else"})
            verdict = driver_mod.EffectReconciler(
                attempts=store, observation_source=None
            ).retry_verdict(KEY, digest=other)
            self.assertEqual(verdict["verdict"], attempts.CONFLICT)

    def test_the_store_refuses_a_second_digest_under_one_key(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store, attempt(phase=attempts.CALL_NOT_SENT))
            changed = attempt(phase=attempts.CALL_NOT_SENT)
            changed["intent_digest"] = digest(
                params={"instruction": "something else"})
            with self.assertRaises(store_mod.AttemptRefused) as caught:
                store.put_dispatch_attempt(changed, actor=PRINCIPAL)
            self.assertEqual(caught.exception.reason_code, "INVALID_CONTEXT")


class TestPartialDispatchIsNotCompletion(DriverTest):
    """#237 T9: unreachable is safe to retry; ambiguous is not."""

    def test_a_source_failure_leaves_the_effect_unknown(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(driver_mod.CLIENT_TURN_ID,
                                     exc=TimeoutError("provider did not answer"))
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"], "SOURCE_UNAVAILABLE")
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)
            self.assertEqual(
                driver_mod.EffectReconciler(
                    attempts=store, observation_source=source
                ).retry_verdict(KEY, digest=digest())["verdict"],
                attempts.REFUSE_UNKNOWN)

    def test_an_unknown_observation_decides_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID, observation=observation("UNKNOWN"))
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"], attempts.REFUSE_UNKNOWN)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)

    def test_an_authoritative_applied_settles_and_a_retry_replays(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "control.json"
            store = store_mod.AgentControlStore(path)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID,
                observation=observation(result={"ok": True, "turn": "t-1"}))
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL,
                        writer=WRITER, current_writer=WRITER)
            self.assertEqual(result["status"], attempts.SETTLE_APPLIED)

            # A restart reads the SETTLED phase from the same file, and the
            # retry is answered from the record rather than dispatched again.
            restarted = store_mod.AgentControlStore(path)
            replay = driver_mod.EffectReconciler(
                attempts=restarted, observation_source=source
            ).retry_verdict(KEY, digest=digest())
            self.assertEqual(replay["verdict"], attempts.REPLAY)
            self.assertEqual(replay["result"]["turn"], "t-1")

    def test_an_authoritative_not_applied_frees_the_scope(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID, observation=observation("NOT_APPLIED"))
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL,
                        writer=WRITER, current_writer=WRITER)
            self.assertEqual(result["status"], attempts.SETTLE_NOT_APPLIED)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.CALL_NOT_SENT)
            self.assertEqual(
                driver_mod.EffectReconciler(
                    attempts=store, observation_source=source
                ).retry_verdict(KEY, digest=digest())["verdict"],
                attempts.PROCEED)

    def test_an_observation_about_another_intent_proves_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID,
                observation=observation(intent_digest="0" * 64))
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"], attempts.REFUSE_UNKNOWN)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)

    def test_a_matching_provider_locator_is_enough_to_settle(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store, attempt(provider_locator="provider-turn:t-9"))
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID,
                observation=observation(intent_digest="0" * 64,
                                        provider_locator="provider-turn:t-9"))
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL,
                        writer=WRITER, current_writer=WRITER)
            self.assertEqual(result["status"], attempts.SETTLE_APPLIED)


class TestReconnectSweep(DriverTest):
    """#237 T12: on reconnect reconcile unknown effects, never replay work."""

    def _three(self, store):
        seeded(store, attempt(key="key-a", phase=attempts.EFFECT_UNKNOWN))
        seeded(store, attempt(key="key-b", phase=attempts.EFFECT_UNKNOWN))
        seeded(store, attempt(key="key-c", phase=attempts.SETTLED,
                              result={"ok": True}))
        return store

    def test_only_unknown_attempts_are_offered_to_the_source(self):
        with tempfile.TemporaryDirectory() as td:
            store = self._three(store_at(td))

            def by_key(recorded, *, key):
                if key == "key-a":
                    return observation(result={"ok": True})
                return observation("UNKNOWN")

            source = RecordingSource(driver_mod.CLIENT_TURN_ID, by_key=by_key)
            sweep = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile_unknown(principal=PRINCIPAL,
                                writer=WRITER, current_writer=WRITER)

            self.assertEqual(sorted(source.calls), ["key-a", "key-b"])
            self.assertEqual([r["key"] for r in sweep["results"]],
                             ["key-a", "key-b"])
            phases = {r["key"]: r["phase"] for r in sweep["results"]}
            self.assertEqual(phases["key-a"], attempts.SETTLED)
            self.assertEqual(phases["key-b"], attempts.EFFECT_UNKNOWN)
            self.assertEqual(store.get_dispatch_attempt("key-c")["phase"],
                             attempts.SETTLED,
                             "a settled attempt was reconciled again")

    def test_a_different_principal_settles_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store, attempt(principal=PRINCIPAL))
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID, observation=observation())
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=OTHER_PRINCIPAL)
            self.assertEqual(result["status"], attempts.REFUSE_PRINCIPAL)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)

    def test_a_stale_writer_settles_nothing(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID, observation=observation())
            driver = driver_mod.EffectReconciler(
                attempts=store, observation_source=source)
            result = driver.reconcile(
                KEY, principal=PRINCIPAL,
                writer={"owner": "agent:old", "epoch": 1, "token": "old"},
                current_writer={"owner": "agent:new", "epoch": 2, "token": "new"})
            self.assertEqual(result["status"], attempts.REFUSE_STALE_WRITER)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)

    def test_no_current_writer_cannot_settle_a_work_scope(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID, observation=observation())
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"], attempts.REFUSE_UNKNOWN)
            self.assertEqual(store.get_dispatch_attempt(KEY)["phase"],
                             attempts.EFFECT_UNKNOWN)

    def test_a_coordinator_scope_needs_no_writer(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store, attempt(work_item=None))
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID, observation=observation())
            result = driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)
            self.assertEqual(result["status"], attempts.SETTLE_APPLIED)


class TestTheDriverOwnsNoSecondLedger(DriverTest):
    """#245: the record stays in the existing Agent Control store."""

    def test_the_only_state_artifact_is_the_agent_control_store(self):
        with tempfile.TemporaryDirectory() as td:
            store = store_at(td)
            seeded(store)
            source = RecordingSource(
                driver_mod.CLIENT_TURN_ID, observation=observation())
            driver_mod.EffectReconciler(
                attempts=store, observation_source=source
            ).reconcile(KEY, principal=PRINCIPAL)

            files = sorted(p.name for p in Path(td).iterdir())
            self.assertTrue(set(files) <= {"control.json",
                                           store.lock_path.name},
                            f"the driver created state of its own: {files}")

    def test_the_driver_has_no_send_path(self):
        with tempfile.TemporaryDirectory() as td:
            driver = driver_mod.EffectReconciler(
                attempts=store_at(td), observation_source=None)
            self.assertFalse(hasattr(driver, "send"),
                             "a reconciler with a send path can blind-retry")


if __name__ == "__main__":
    unittest.main()
