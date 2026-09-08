import hashlib
import tempfile
import unittest
from pathlib import Path

from workengine.migration import MigrationError, MigrationManager
from workengine.store import ServerOSStore


class MigrationManagerTest(unittest.TestCase):
    def test_apply_resumes_after_crash_without_duplicate_move(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            first = root / "old-one"
            second = root / "old-two"
            first.write_text("one", encoding="utf-8")
            second.write_text("two", encoding="utf-8")
            manifest = {
                "project": "demo",
                "operations": [
                    {"type": "move", "source": str(first), "target": str(root / "new-one"), "sha256": hashlib.sha256(b"one").hexdigest()},
                    {"type": "move", "source": str(second), "target": str(root / "new-two"), "sha256": hashlib.sha256(b"two").hexdigest()},
                ],
            }
            manager = MigrationManager(ServerOSStore(root / "state"))
            manager.plan(manifest)
            with self.assertRaises(RuntimeError):
                manager.apply("demo", crash_after=1)
            result = MigrationManager(ServerOSStore(root / "state")).apply("demo")
            self.assertEqual(result["status"], "COMPLETED")
            self.assertEqual((root / "new-one").read_text(), "one")
            self.assertEqual((root / "new-two").read_text(), "two")

    def test_plan_rejects_relative_or_conflicting_targets(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            target = root / "target"
            source.write_text("x")
            target.write_text("occupied")
            manager = MigrationManager(ServerOSStore(root / "state"))
            with self.assertRaises(MigrationError):
                manager.plan({"project": "demo", "operations": [{"type": "move", "source": "relative", "target": str(root / "x")}]})
            with self.assertRaises(MigrationError):
                manager.plan({"project": "demo", "operations": [{"type": "move", "source": str(source), "target": str(target)}]})

    def test_compensate_restores_completed_moves(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            target = root / "target"
            source.write_text("content")
            manager = MigrationManager(ServerOSStore(root / "state"))
            manager.plan({"project": "demo", "operations": [{"type": "move", "source": str(source), "target": str(target)}]})
            manager.apply("demo")
            result = manager.compensate("demo")
            self.assertEqual(result["status"], "COMPENSATED")
            self.assertTrue(source.exists())
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
