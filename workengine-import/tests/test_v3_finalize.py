import tempfile
import unittest
from pathlib import Path

from workengine.model import Activity, ActivityState, ContextMode, RunState
from workengine.store import ServerOSStore


class FinalizeV3Tests(unittest.TestCase):
    def setUp(self):
        self.td = tempfile.TemporaryDirectory(); self.s = ServerOSStore(Path(self.td.name) / "state")
        self.s.register_project("demo", "DEM"); self.item = self.s.create_item(__import__('workengine').WorkItem("DEM-0001", "demo", "x", "test", "/card"))
        self.s.add_activity(Activity(self.item.id, "test", "test")); self.s.attach_context(__import__('workengine').ExecutionContext(self.item.id, "test", "repo", ContextMode.TEST, "base"))
        self.run = self.s.begin_run(self.item.id, "test", "worker", "key", Path(self.td.name) / "artifacts")
        for state in (RunState.PREPARING, RunState.RUNNING, RunState.FINALIZING): self.run = self.s.advance_run(self.item.id, self.run.id, state)

    def tearDown(self): self.td.cleanup()

    def test_finalize_replaces_context_and_is_idempotent(self):
        result = self.s.finalize_run(self.item.id, self.run.id, verdict="PASS", exit_code=0, command="pytest", cases=["one"], revision="final", dirty=False)
        self.assertEqual(result["state"], "SUCCEEDED"); self.assertEqual(result["verdict"], "PASS")
        self.assertEqual(self.s.execution_contexts(self.item.id, "test")[0].revision, "final")
        self.assertEqual(self.s.finalize_run(self.item.id, self.run.id, verdict="PASS", exit_code=0, command="pytest", cases=["one"], revision="final", dirty=False), result)

    def test_activity_transition_validates(self):
        self.s.transition_activity(self.item.id, "test", ActivityState.READY)
        self.assertEqual(self.s.transition_activity(self.item.id, "test", ActivityState.RUNNING).state, ActivityState.RUNNING)
