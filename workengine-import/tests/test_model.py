"""Tests for the Work Item data model and serialization."""

from __future__ import annotations

import unittest

from workengine.model import State, WorkItem, now_iso


class WorkItemModelTest(unittest.TestCase):
    def test_defaults(self):
        item = WorkItem(id="WI-AAAA", title="T", requirement="R")
        self.assertEqual(item.status, State.NEW)
        self.assertEqual(item.plan, [])
        self.assertEqual(item.evidence, [])
        self.assertEqual(item.history, [])

    def test_serialization_round_trip(self):
        item = WorkItem(
            id="WI-AAAA",
            title="Ship C1",
            requirement="Deliver a resumable work engine.",
            repo="work-engine",
            worktree="/abs/path/to/worktree",
            plan=["step1", "step2"],
            status=State.IMPLEMENTING,
        )
        item.record("advance", State.NEW, State.TRIAGED, "triaged")
        item.record("advance", State.TRIAGED, State.READY, "ready")
        item.record("advance", State.READY, State.IMPLEMENTING, "go")
        item.add_evidence("/abs/path/to/evidence.json", kind="json", note="test run")

        data = item.to_dict()
        # Status must serialize as a plain string, not an enum.
        self.assertIsInstance(data["status"], str)
        self.assertEqual(data["status"], "IMPLEMENTING")

        restored = WorkItem.from_dict(data)
        self.assertEqual(restored, item)
        self.assertEqual(restored.status, State.IMPLEMENTING)
        self.assertEqual(len(restored.history), 3)
        self.assertEqual(restored.evidence[0]["path"], "/abs/path/to/evidence.json")

    def test_now_iso_is_timezone_aware(self):
        ts = now_iso()
        self.assertIn("+", ts)  # contains UTC offset per isoformat

    def test_new_id_unique_and_prefixed(self):
        ids = {WorkItem(id="WI-AAAA", title="", requirement="").id for _ in range(1)}
        self.assertTrue(ids)
        # id prefix check via generator
        from workengine.model import new_id

        generated = [new_id() for _ in range(50)]
        self.assertEqual(len(set(generated)), len(generated))
        self.assertTrue(all(g.startswith("WI-") for g in generated))


if __name__ == "__main__":
    unittest.main()
