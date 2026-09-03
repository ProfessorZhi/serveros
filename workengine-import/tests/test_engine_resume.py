"""End-to-end cross-session resume test.

This is the C1 acceptance path: create a Work Item, advance it through several
states, attach evidence, then **simulate a session death** by discarding the
in-memory engine and reading back from the same persisted store with a brand
new engine (what a fresh session/process would do). The new engine must be able
to find the worktree, read the evidence pointers, and know the legal next
transition.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from workengine.engine import WorkEngine
from workengine.model import State
from workengine.state_machine import IllegalTransition, allowed_transitions


class ResumeIntegrationTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.state_dir = Path(self._tmp.name) / "state"
        self.worktree_dir = Path(self._tmp.name) / "worktrees" / "muc-e2e"

    def test_full_resume_after_session_death(self):
        # --- session A builds state ---
        engine_a = WorkEngine(state_dir=self.state_dir)
        item = engine_a.create(
            title="Resumable C1",
            requirement="C1 Work Engine must survive session death.",
            repo="work-engine",
            worktree=str(self.worktree_dir),
            plan=["build engine", "advance states", "resume", "verify"],
        )
        item_id = item.id

        for state in ("TRIAGED", "READY", "IMPLEMENTING"):
            engine_a.advance(item_id, state, note=f"advanced to {state}")

        engine_a.attach_evidence(item_id, str(self.worktree_dir / "report.md"), kind="markdown", note="state report")
        engine_a.attach_evidence(item_id, str(self.worktree_dir / "evidence.json"), kind="json", note="test run")

        # --- session A is now gone (engine_a dropped); session B resumes ---
        engine_b = WorkEngine(state_dir=self.state_dir)

        info = engine_b.resume(item_id)
        resumed = info["item"]

        # The fresh session recovers identity + requirement.
        self.assertEqual(resumed.id, item_id)
        self.assertEqual(resumed.title, "Resumable C1")
        self.assertEqual(resumed.repo, "work-engine")

        # It recovers the absolute worktree path.
        self.assertEqual(resumed.worktree, str(self.worktree_dir.resolve()))

        # It recovers the evidence pointers.
        self.assertEqual(len(resumed.evidence), 2)
        self.assertEqual(resumed.evidence[0]["path"], str((self.worktree_dir / "report.md").resolve()))

        # It recovers the current state and the legal next transition.
        self.assertEqual(resumed.status, State.IMPLEMENTING)
        expected_next = {s.value for s in allowed_transitions(State.IMPLEMENTING)}
        self.assertEqual({s.value for s in info["next"]}, expected_next)
        self.assertIn("VERIFYING", expected_next)

        # The new session can legally continue the chain where it left off.
        advanced = engine_b.advance(item_id, "VERIFYING", note="continued after session death")
        self.assertEqual(advanced.status, State.VERIFYING)
        self.assertEqual(len(advanced.history), 5)  # 1 create + 3 prior advances + this one

    def test_illegal_advance_still_rejected_after_resume(self):
        engine_a = WorkEngine(state_dir=self.state_dir)
        item = engine_a.create("t", "r")
        engine_a.advance(item.id, "TRIAGED")

        engine_b = WorkEngine(state_dir=self.state_dir)
        # Skipping READY -> IMPLEMENTING directly from TRIAGED is illegal.
        with self.assertRaises(IllegalTransition):
            engine_b.advance(item.id, "IMPLEMENTING")

    def test_resume_unknown_id_raises(self):
        engine = WorkEngine(state_dir=self.state_dir)
        with self.assertRaises(KeyError):
            engine.resume("WI-NOPE")


if __name__ == "__main__":
    unittest.main()
