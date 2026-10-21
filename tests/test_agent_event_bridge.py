"""Tests: conservative Agent event observation, no new notification bus."""
from __future__ import annotations
import unittest
from runtime.agent_event_bridge import EventNotEstablished, normalize

DEVICE = "device:tokyo-shared-dev-01"
NOW = "2026-10-12T10:00:00+08:00"


def claude(signal, occurrence="run-1"):
    return normalize(provider="claude", device_ref=DEVICE, session_id="s1",
                     occurrence_ref=occurrence, observed_at=NOW,
                     signal={"session_id": "s1", **signal})


class AgentEventBridgeTests(unittest.TestCase):
    def test_stop_means_turn_not_task_success(self):
        self.assertEqual(claude({"hook_event_name": "Stop"})["notification"]["event"],
                         "turn.completed")

    def test_approval_is_only_a_candidate(self):
        event = claude({"hook_event_name": "PermissionRequest"})
        self.assertEqual(event["notification"]["event"], "approval.required")
        self.assertTrue(event["requires_local_authorization"])

    def test_idempotent_retry_and_distinct_turn(self):
        signal = {"hook_event_name": "Stop"}
        self.assertEqual(claude(signal)["idempotency_key"],
                         claude(signal)["idempotency_key"])
        self.assertNotEqual(claude(signal, "run-2")["idempotency_key"],
                            claude(signal)["idempotency_key"])

    def test_secret_provider_text_not_forwarded(self):
        marker = "NEVER-EXPORT-ME"
        result = claude({"hook_event_name": "PermissionRequest",
                         "tool_input": {"password": marker}})
        self.assertNotIn(marker, str(result))

    def test_nonterminal_hook_refused(self):
        for name in ("PostToolUse", "PostToolUseFailure", "SessionStart"):
            with self.subTest(name=name):
                with self.assertRaises(EventNotEstablished):
                    claude({"hook_event_name": name})

    def test_recursive_stop_refused(self):
        with self.assertRaises(EventNotEstablished):
            claude({"hook_event_name": "Stop", "stop_hook_active": True})

    def test_spoofed_session_refused(self):
        with self.assertRaises(EventNotEstablished):
            claude({"hook_event_name": "Stop", "session_id": "other"})

    def test_event_identity_required(self):
        with self.assertRaises(EventNotEstablished):
            claude({"hook_event_name": "Stop"}, occurrence="")

    def test_naive_timestamp_refused(self):
        with self.assertRaises(EventNotEstablished):
            normalize(provider="claude", device_ref=DEVICE, session_id="s1",
                      occurrence_ref="run", observed_at="2026-10-12T10:00:00",
                      signal={"hook_event_name": "Stop", "session_id": "s1"})

    def test_codex_completed_failed(self):
        for status, expected in (("completed", "turn.completed"),
                                 ("failed", "turn.failed")):
            data = normalize(provider="codex", device_ref=DEVICE,
                session_id="thread-1", occurrence_ref="turn-1",
                observed_at=NOW, signal={"method": "turn/completed", "params": {
                    "threadId": "thread-1", "turn": {
                        "status": status, "error": {"token": "secret"}}}})
            self.assertEqual(data["notification"]["event"], expected)
            self.assertNotIn("secret", str(data))

    def test_codex_in_progress_refused(self):
        with self.assertRaises(EventNotEstablished):
            normalize(provider="codex", device_ref=DEVICE,
                session_id="thread-1", occurrence_ref="turn-1", observed_at=NOW,
                signal={"method": "turn/completed", "params": {
                    "threadId": "thread-1", "turn": {"status": "inProgress"}}})

    def test_invalid_device_ref(self):
        with self.assertRaises(Exception):
            normalize(provider="claude", device_ref="just-a-hostname",
                session_id="s1", occurrence_ref="run", observed_at=NOW,
                signal={"hook_event_name": "Stop", "session_id": "s1"})


if __name__ == "__main__":
    unittest.main()
