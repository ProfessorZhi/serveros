"""Tests for WorkItemStore persistence."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workengine.engine import WorkEngine
from workengine.store import WorkItemStore


class WorkItemStoreTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name) / "state"

    def test_save_creates_directory_and_file(self):
        store = WorkItemStore(self.state_dir)
        engine = WorkEngine(store=store)
        item = engine.create("t", "r")
        self.assertTrue((self.state_dir / f"{item.id}.json").exists())

    def test_load_returns_none_for_unknown(self):
        store = WorkItemStore(self.state_dir)
        self.assertIsNone(store.load("WI-NOPE"))

    def test_round_trip_across_fresh_store_instance(self):
        # A brand-new store (like a fresh session/process) reads what a prior
        # instance wrote.
        engine_a = WorkEngine(state_dir=self.state_dir)
        item = engine_a.create("t", "r", worktree="worktree-a")
        engine_a.advance(item.id, "TRIAGED")

        engine_b = WorkEngine(state_dir=self.state_dir)
        restored = engine_b.get(item.id)
        self.assertIsNotNone(restored)
        self.assertEqual(restored.id, item.id)
        self.assertEqual(restored.status.value, "TRIAGED")

    def test_list_returns_all(self):
        engine = WorkEngine(state_dir=self.state_dir)
        engine.create("t1", "r1")
        engine.create("t2", "r2")
        self.assertEqual(len(engine.list()), 2)

    def test_state_dir_is_absolute_after_resolution(self):
        store = WorkItemStore(self.state_dir)
        self.assertTrue(store.state_dir.is_absolute())

    def test_corrupt_file_skipped_in_list(self):
        store = WorkItemStore(self.state_dir)
        # Write a corrupted JSON file.
        (self.state_dir / "WI-BROKEN.json").write_text("{ not json", encoding="utf-8")
        self.assertIsNone(store.load("WI-BROKEN"))
        self.assertEqual(store.list(), [])


if __name__ == "__main__":
    unittest.main()
