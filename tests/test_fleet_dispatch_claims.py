"""Delivery retries: do not turn a lost acknowledgement into a second task."""
from __future__ import annotations

import importlib.util
import json
import os
import queue
import sys
import tempfile
import threading
import unittest
from pathlib import Path

from fleet_mcp.errors import FleetRefusal
from fleet_mcp.fleet import FleetService
from fleet_mcp.identity import SessionKey
from test_fleet_mcp_surface import Harness, TOKYO
from test_fleet_create_session import Harness as CreateHarness


def owner_store(path):
    name = "serveros_runtime_agent_control_store"
    if name not in sys.modules:
        source = Path(__file__).resolve().parents[1] / "runtime/agent_control_store.py"
        spec = importlib.util.spec_from_file_location(name, source)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name].AgentControlStore(path)


class DispatchClaims(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.key = SessionKey.compose(TOKYO, "claude", "owned-session")
        self.h = Harness(self.root, send_allow=(self.key.ref,), allow_tokyo_send=True)
        self.principal = self.h.principal()

    def send(self, message="complete the whole task", **kwargs):
        return self.h.service.send_to_session(
            self.principal, self.key, message=message, idempotency_key="attempt-1", **kwargs)

    def test_changed_instruction_is_a_conflict_and_does_not_resend(self):
        self.send()
        with self.assertRaises(FleetRefusal) as caught:
            self.send("a different task")
        self.assertEqual(caught.exception.code, "IDEMPOTENCY_CONFLICT")
        self.assertEqual(len(self.h.tokyo.sent), 1)

    def test_one_claimant_sends_while_a_simultaneous_retry_returns_promptly(self):
        entered, release = threading.Event(), threading.Event()
        outcomes = queue.Queue()
        calls = []

        def blocked(key, message, **kwargs):
            calls.append(message)
            if len(calls) > 1:
                outcomes.put("SECOND_PROVIDER_CALL")
                return {"runId": "duplicate"}
            entered.set()
            release.wait(5)
            return {"runId": "first-run", "accepted": True}

        self.h.tokyo.send = blocked

        def invoke():
            try:
                self.send()
            except FleetRefusal as exc:
                outcomes.put(exc.code)

        first = threading.Thread(target=invoke)
        second = threading.Thread(target=invoke)
        first.start()
        try:
            self.assertTrue(entered.wait(2))
            second.start()
            self.assertEqual(outcomes.get(timeout=2), "DISPATCH_OUTCOME_UNKNOWN")
        finally:
            release.set()
            first.join(3)
            if second.ident is not None:
                second.join(3)
        self.assertEqual(len(calls), 1)

    def test_lost_ack_is_not_blindly_retried(self):
        calls = []

        def lost(*args, **kwargs):
            calls.append(True)
            raise FleetRefusal("PROVIDER_TIMEOUT", "response lost after send")

        self.h.tokyo.send = lost
        with self.assertRaises(FleetRefusal):
            self.send()
        with self.assertRaises(FleetRefusal) as caught:
            self.send()
        self.assertEqual(caught.exception.code, "DISPATCH_OUTCOME_UNKNOWN")
        self.assertEqual(len(calls), 1)

    def test_process_local_scope_is_reported_and_no_key_remains_non_idempotent(self):
        first = self.send()
        self.assertEqual(first["idempotency_durability"], "PROCESS_LOCAL")
        replay = self.send()
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(first["run_id"], replay["run_id"])
        self.h.service.send_to_session(self.principal, self.key, message="unkeyed")
        self.h.service.send_to_session(self.principal, self.key, message="unkeyed")
        self.assertEqual(len(self.h.tokyo.sent), 3)

    def test_invalid_stored_receipts_fail_closed_before_io(self):
        bad = [
            {"session_ref": "device:work-fj|claude|other", "run_id": "r", "accepted": True},
            {"session_ref": self.key.ref, "run_id": {"private": "bad"}, "accepted": True},
            {"session_ref": self.key.ref, "run_id": "x" * 66000, "accepted": True},
            {"accepted": True},
        ]
        for receipt in bad:
            with self.subTest(receipt_shape=list(receipt)):
                class Owner:
                    def claim_dispatch_attempt(self, intent, *, actor):
                        return {"claimed": False, "record": {
                            **intent, "phase": "ACCEPTED", "result": receipt}}

                    def complete_dispatch_claim(self, *args, **kwargs):
                        raise AssertionError("replay cannot complete")

                self.h.service = FleetService(
                    self.h.config, providers=self.h.service.providers, attempts=Owner())
                with self.assertRaises(FleetRefusal) as caught:
                    self.send()
                self.assertEqual(caught.exception.code, "DISPATCH_STATE_UNAVAILABLE")
        self.assertEqual(self.h.tokyo.sent, [])

    def test_memory_capacity_keeps_old_attempts_and_refuses_new_ones(self):
        from fleet_mcp.dispatch import DispatchGuard
        self.h.service.dispatch = DispatchGuard(capacity=1)
        self.send()
        with self.assertRaises(FleetRefusal) as caught:
            self.h.service.send_to_session(
                self.principal, self.key, message="second", idempotency_key="new-key")
        self.assertEqual(caught.exception.code, "RATE_LIMITED")
        self.assertTrue(self.send()["idempotent_replay"])
        self.assertEqual(len(self.h.tokyo.sent), 1)

    def test_rejected_or_malformed_create_is_not_an_accepted_receipt(self):
        for response in ({"sessionId": {"instruction": "PRIVATE_CANARY"}},
                         {"sessionId": "would-exist", "accepted": False}):
            with self.subTest(response=response):
                h = CreateHarness(self.root)
                calls = []

                def create(**kwargs):
                    calls.append(True)
                    return response

                h.providers[TOKYO].create = create
                for _ in range(2):
                    with self.assertRaises(FleetRefusal):
                        h.service.create_session(
                            h.principal(), device=TOKYO, project="p-1", idempotency_key="bad-create")
                self.assertEqual(len(calls), 1)

    @unittest.skipIf(os.name == "nt", "existing owner uses a POSIX file lock")
    def test_same_owner_read_during_claim_flush_cannot_erase_the_claim(self):
        store = owner_store(self.root / "read-race.json")
        intent = {"key": "fleet-mcp:read-race", "action": "fleet.send",
                  "device_ref": TOKYO, "verb": "session.send", "intent_digest": "0" * 64,
                  "principal": "actor", "runtime": "claude", "session_ref": self.key.ref}
        entered, release = threading.Event(), threading.Event()
        result = queue.Queue()
        original = store._flush

        def pause():
            entered.set()
            release.wait(5)
            original()

        store._flush = pause
        thread = threading.Thread(target=lambda: result.put(
            store.claim_dispatch_attempt(intent, actor="actor")))
        thread.start()
        try:
            self.assertTrue(entered.wait(2))
            self.assertIsNone(store.get_dispatch_attempt(intent["key"]))
        finally:
            release.set()
            thread.join(3)
        self.assertTrue(result.get(timeout=1)["claimed"])
        self.assertEqual(store.get_dispatch_attempt(intent["key"])["phase"], "EFFECT_UNKNOWN")

    @unittest.skipIf(os.name == "nt", "existing owner uses a POSIX file lock")
    def test_owner_public_reads_hide_marker_and_other_invocations_cannot_ack(self):
        store = owner_store(self.root / "markers.json")
        intent = {"key": "fleet-mcp:marker", "action": "fleet.send",
                  "device_ref": TOKYO, "verb": "session.send", "intent_digest": "0" * 64,
                  "principal": "actor", "runtime": "claude", "session_ref": self.key.ref}
        claim = store.claim_dispatch_attempt(intent, actor="actor")
        self.assertNotIn("_dispatch_claim", store.get_dispatch_attempt(intent["key"]))
        self.assertNotIn("_dispatch_claim", store.dispatch_attempts()[intent["key"]])
        retry = store.claim_dispatch_attempt(intent, actor="actor")
        self.assertFalse(retry["claimed"])
        self.assertNotIn("claim_id", retry)
        receipt = {"session_ref": self.key.ref, "run_id": "run", "accepted": True}
        error = sys.modules["serveros_runtime_agent_control_store"].AttemptRefused
        for changes in ({"claim_id": "wrong"}, {"digest": "1" * 64}, {"actor": "other"}):
            args = dict(digest=intent["intent_digest"], claim_id=claim["claim_id"],
                        actor="actor", receipt=receipt)
            args.update(changes)
            with self.assertRaises(error):
                store.complete_dispatch_claim(intent["key"], **args)
        current = store.get_dispatch_attempt(intent["key"])
        with self.assertRaises(error):
            store.put_dispatch_attempt({**current, "phase": "ACCEPTED"}, actor="actor")
        store.complete_dispatch_claim(
            intent["key"], digest=intent["intent_digest"], claim_id=claim["claim_id"],
            actor="actor", receipt=receipt)
        with self.assertRaises(error):
            store.complete_dispatch_claim(
                intent["key"], digest=intent["intent_digest"], claim_id=claim["claim_id"],
                actor="actor", receipt=receipt)

    @unittest.skipIf(os.name == "nt", "existing owner uses a POSIX file lock")
    def test_receipt_survives_two_independently_reconstructed_owners(self):
        path = self.root / "owner.json"
        self.h.service = FleetService(
            self.h.config, providers=self.h.service.providers, attempts=owner_store(path))
        first = self.send("PRIVATE_INSTRUCTION_DO_NOT_PERSIST")
        self.h.service = FleetService(
            self.h.config, providers=self.h.service.providers, attempts=owner_store(path))
        replay = self.send("PRIVATE_INSTRUCTION_DO_NOT_PERSIST")
        self.assertTrue(replay["idempotent_replay"])
        self.assertEqual(replay["run_id"], first["run_id"])
        self.assertEqual(len(self.h.tokyo.sent), 1)
        stored = path.read_text()
        self.assertNotIn("PRIVATE_INSTRUCTION_DO_NOT_PERSIST", stored)
        self.assertNotIn('"raw"', stored)
        self.assertEqual(first["idempotency_durability"], "AGENT_CONTROL_STORE")
        self.assertNotIn("claim_id", json.dumps(first))
        self.assertNotIn("claim_id", json.dumps(replay))

    @unittest.skipIf(os.name == "nt", "existing owner uses a POSIX file lock")
    def test_unknown_survives_owner_reconstruction(self):
        path = self.root / "owner.json"
        calls = []

        def lost(*args, **kwargs):
            calls.append(True)
            raise FleetRefusal("PROVIDER_TIMEOUT", "lost")

        self.h.tokyo.send = lost
        self.h.service = FleetService(
            self.h.config, providers=self.h.service.providers, attempts=owner_store(path))
        with self.assertRaises(FleetRefusal):
            self.send()
        self.h.service = FleetService(
            self.h.config, providers=self.h.service.providers, attempts=owner_store(path))
        with self.assertRaises(FleetRefusal) as caught:
            self.send()
        self.assertEqual(caught.exception.code, "DISPATCH_OUTCOME_UNKNOWN")
        self.assertEqual(len(calls), 1)

    @unittest.skipIf(os.name == "nt", "existing owner uses a POSIX file lock")
    def test_create_without_message_replays_after_reconstruction(self):
        h = CreateHarness(self.root)
        path = self.root / "create-owner.json"
        h.service = FleetService(h.config, providers=h.providers, attempts=owner_store(path))
        first = h.service.create_session(
            h.principal(), device=TOKYO, project="p-1", idempotency_key="create-1")
        h.service = FleetService(h.config, providers=h.providers, attempts=owner_store(path))
        second = h.service.create_session(
            h.principal(), device=TOKYO, project="p-1", idempotency_key="create-1")
        self.assertIsNone(first["run_id"])
        self.assertEqual(first["session_ref"], second["session_ref"])
        self.assertTrue(second["idempotent_replay"])
        self.assertEqual(len(h.providers[TOKYO].created), 1)

    @unittest.skipIf(os.name == "nt", "existing owner uses a POSIX file lock")
    def test_failure_to_save_ack_blocks_retries_even_after_reconstruction(self):
        path = self.root / "owner.json"
        store = owner_store(path)
        original = store.complete_dispatch_claim

        def fail(*args, **kwargs):
            raise OSError("simulated disk failure")

        store.complete_dispatch_claim = fail
        self.h.service = FleetService(self.h.config, providers=self.h.service.providers,
                                      attempts=store)
        with self.assertRaises(FleetRefusal) as caught:
            self.send()
        self.assertEqual(caught.exception.code, "DISPATCH_OUTCOME_UNKNOWN")
        store.complete_dispatch_claim = original
        self.h.service = FleetService(
            self.h.config, providers=self.h.service.providers, attempts=owner_store(path))
        with self.assertRaises(FleetRefusal):
            self.send()
        self.assertEqual(len(self.h.tokyo.sent), 1)


if __name__ == "__main__":
    unittest.main()
