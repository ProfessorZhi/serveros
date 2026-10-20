#!/usr/bin/env python3
"""The Core's recovery receipt becomes typed LIVE/REPORT events — and a
successful verb is still not a recovery.

WHAT THIS FILE KEEPS FALSE
--------------------------
    "the verb exited 0, so the service recovered"   — exit status is a fact
                                                      about the ATTEMPT
    "the receipt says the budget is 3, so it is 3"  — the receipt does not
                                                      carry it; UNKNOWN is said
    "any node's receipt is this device's"           — node is checked
    "an owner we could not establish is an owner"   — it refuses
    "a stamp in the future is fresh"                — it refuses
    "a code nobody defined is a code"               — it is UNVERIFIED

Nothing here runs a Core, reads a manifest or reaches a socket: a receipt is a
document this adapter is handed, exactly as `admits` it.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / "runtime"

NOW = "2026-10-16T06:00:00Z"
DEVICE = "device:tokyo-shared-dev-01"
OWNER = "vince"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


recovery = _load("serveros_fleet_recovery_events", RUNTIME / "fleet_recovery_events.py")
surface = _load("serveros_runtime_card_surface", RUNTIME / "card_surface.py")


def receipt(**over):
    doc = {
        "schema": recovery.RECEIPT_SCHEMA,
        "mode": "active",
        "node": DEVICE,
        "principal": OWNER,
        "trigger": "tick",
        "started_at": "2026-10-16T05:59:40Z",
        "finished_at": "2026-10-16T05:59:55Z",
        "manifest": {"schema": "serveros/native-core-manifest.v1", "generation": 7,
                     "sha256": "0" * 64},
        "plan": {
            "ok": True,
            "order": ["gateway0"],
            "actions": [{"service_id": "gateway0", "unit": "gateway0.service",
                         "verb": "start", "reason": "not active"}],
            "refusals": [],
        },
        "executed": [
            {"service_id": "gateway0", "unit": "gateway0.service", "verb": "start",
             "ok": True, "code": "EXITED", "reason": "", "exit_code": 0},
        ],
        "summary": {"actions": 1, "refusals": 0, "executed": 1, "failed": 0},
    }
    doc.update(over)
    return doc


class AdmissionTest(unittest.TestCase):
    def _events(self, doc, **kw):
        kw.setdefault("max_age_s", 3600)
        return recovery.recovery_events(doc, device_ref=DEVICE, now=NOW, **kw)

    def test_a_good_receipt_becomes_events(self):
        events, unverified = self._events(receipt())
        self.assertEqual(unverified, 0)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], surface.HANDLING)

    def test_the_event_shape_is_the_one_the_runner_already_carries(self):
        events, _ = self._events(receipt())
        for event in events:
            for key in ("kind", "episode", "version", "source_row_key"):
                self.assertIn(key, event)
            self.assertIn(event["kind"], surface.EVENT_KINDS)

    def test_a_wrong_node_is_refused(self):
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(node="device:somewhere-else"))

    def test_an_unknown_owner_is_refused(self):
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(principal=""))

    def test_a_wrong_owner_is_refused_when_one_is_required(self):
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(principal="someone-else"), expected_owner=OWNER)
        # ...and the right one is accepted.
        events, _ = self._events(receipt(), expected_owner=OWNER)
        self.assertTrue(events)

    def test_a_future_stamp_is_refused(self):
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(finished_at="2026-10-16T06:30:00Z"))

    def test_unparseable_stamps_are_refused(self):
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(started_at="yesterday"))
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(finished_at=None))

    def test_a_stale_receipt_is_refused(self):
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(finished_at="2026-10-16T04:00:00Z"), max_age_s=1800)

    def test_a_mode_outside_the_closed_set_is_refused(self):
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(mode="maybe"))

    def test_a_wrong_schema_is_refused(self):
        with self.assertRaises(recovery.RecoveryRefused):
            self._events(receipt(schema="serveros/something-else.v1"))


class LoopEventsTest(unittest.TestCase):
    def _events(self, doc):
        return recovery.recovery_events(doc, device_ref=DEVICE, now=NOW,
                                        max_age_s=3600)

    def test_a_successful_verb_is_handling_and_never_recovered(self):
        # The sentence this pins: exit 0 is a fact about the ATTEMPT.
        events, _ = self._events(receipt())
        self.assertEqual([e["kind"] for e in events], [surface.HANDLING])
        self.assertNotIn(surface.RECOVERED, [e["kind"] for e in events])
        self.assertEqual(events[0]["run"], "executed")
        self.assertEqual(events[0]["service"], "gateway0")

    def test_a_refusal_becomes_an_event_and_names_its_code(self):
        doc = receipt(plan={"ok": False, "order": [], "actions": [],
                            "refusals": [{"scope": "litellm", "code": "NOT_INSTALLED",
                                          "reason": "no unit by that name"}]},
                      executed=[], summary={"actions": 0, "refusals": 1,
                                            "executed": 0, "failed": 0})
        events, unverified = self._events(doc)
        self.assertEqual(unverified, 0)
        self.assertEqual(events[0]["kind"], surface.ESCALATED)
        self.assertEqual(events[0]["code"], "NOT_INSTALLED")

    def test_a_transient_refusal_is_handling_not_escalated(self):
        for code in ("NO_LEASE", "AGENT_BUSY"):
            doc = receipt(plan={"ok": False, "order": [], "actions": [],
                                "refusals": [{"scope": "svc", "code": code,
                                              "reason": "waiting"}]},
                          executed=[])
            events, _ = self._events(doc)
            self.assertEqual(events[0]["kind"], surface.HANDLING, code)

    def test_a_code_nobody_defined_is_unverified(self):
        doc = receipt(plan={"ok": False, "order": [], "actions": [],
                            "refusals": [{"scope": "svc", "code": "MADE_UP",
                                          "reason": "?"}]}, executed=[])
        events, unverified = self._events(doc)
        self.assertEqual(events, [])
        self.assertEqual(unverified, 1)

    def test_the_budget_is_unknown_unless_a_producer_supplies_it(self):
        # The v1 receipt carries no budget; inventing one would be publishing a
        # number nobody measured.
        events, _ = self._events(receipt())
        self.assertEqual(events[0]["budget"], "UNKNOWN")
        self.assertEqual(events[0]["backoff_seconds"], "UNKNOWN")
        with_block = receipt(recovery={"budget": 3, "backoff_seconds": 60})
        events2, _ = self._events(with_block)
        self.assertEqual(events2[0]["budget"], 3)
        self.assertEqual(events2[0]["backoff_seconds"], 60)

    def test_the_event_carries_owner_and_times(self):
        events, _ = self._events(receipt())
        self.assertEqual(events[0]["owner"], OWNER)
        self.assertEqual(events[0]["started_at"], "2026-10-16T05:59:40Z")
        self.assertEqual(events[0]["finished_at"], "2026-10-16T05:59:55Z")
        self.assertEqual(events[0]["trigger"], "tick")


class VersionTest(unittest.TestCase):
    """Duplicate polls dedupe; a changed outcome patches."""

    def _events(self, doc):
        return recovery.recovery_events(doc, device_ref=DEVICE, now=NOW,
                                        max_age_s=3600)[0]

    def test_the_same_receipt_polled_twice_yields_the_same_version(self):
        a = self._events(receipt())
        b = self._events(receipt())
        self.assertEqual([e["version"] for e in a], [e["version"] for e in b])

    def test_a_changed_outcome_yields_a_new_version(self):
        a = self._events(receipt())
        changed = receipt(executed=[{"service_id": "gateway0",
                                     "unit": "gateway0.service", "verb": "start",
                                     "ok": False, "code": "TIMED_OUT",
                                     "reason": "did not answer", "exit_code": -1}])
        b = self._events(changed)
        self.assertNotEqual(a[0]["version"], b[0]["version"])

    def test_a_rejection_whose_scope_is_missing_is_unverified(self):
        doc = receipt(plan={"ok": False, "order": [], "actions": [],
                            "refusals": [{"scope": "", "code": "LINK_DOWN",
                                          "reason": "no link"}]}, executed=[])
        events, unverified = recovery.recovery_events(doc, device_ref=DEVICE,
                                                       now=NOW, max_age_s=3600)
        self.assertEqual(events, [])
        self.assertEqual(unverified, 1)


class ReadinessTransitionTest(unittest.TestCase):
    """RECOVERED comes from readiness, and from nowhere else."""

    def test_not_ready_to_ready_is_recovered(self):
        events, _ = recovery.recovery_transitions(
            {"gateway0": "NOT_READY"}, {"gateway0": "READY"}, device_ref=DEVICE)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["kind"], surface.RECOVERED)
        self.assertEqual(events[0]["was"], "NOT_READY")
        self.assertEqual(events[0]["readiness"], "READY")

    def test_ready_to_not_ready_is_escalated(self):
        events, _ = recovery.recovery_transitions(
            {"gateway0": "READY"}, {"gateway0": "NOT_READY"}, device_ref=DEVICE)
        self.assertEqual(events[0]["kind"], surface.ESCALATED)

    def test_a_service_seen_for_the_first_time_has_not_recovered(self):
        events, _ = recovery.recovery_transitions({}, {"gateway0": "READY"},
                                                  device_ref=DEVICE)
        self.assertEqual(events, [],
                         "RECOVERED claims a crossing; nobody saw one here")

    def test_an_unchanged_word_is_not_an_event(self):
        events, _ = recovery.recovery_transitions({"gateway0": "READY"},
                                                  {"gateway0": "READY"},
                                                  device_ref=DEVICE)
        self.assertEqual(events, [])

    def test_a_word_outside_the_closed_set_is_unverified(self):
        events, unverified = recovery.recovery_transitions(
            {"gateway0": "NOT_READY"}, {"gateway0": "PROBABLY"}, device_ref=DEVICE)
        self.assertEqual(events, [])
        self.assertEqual(unverified, 1)

    def test_a_transition_is_never_an_executed_row(self):
        # The two producers are separate functions so neither can borrow the
        # other's evidence: a receipt alone can NEVER say RECOVERED.
        events, _ = recovery.recovery_events(receipt(), device_ref=DEVICE,
                                             now=NOW, max_age_s=3600)
        self.assertNotIn(surface.RECOVERED, [e["kind"] for e in events])


if __name__ == "__main__":
    unittest.main(verbosity=2)
