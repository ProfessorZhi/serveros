import hashlib
import errno
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

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

    def test_apply_copies_then_removes_source_across_filesystems(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            target = root / "elsewhere" / "target"
            source.write_text("cross-device", encoding="utf-8")
            manager = MigrationManager(ServerOSStore(root / "state"))
            manager.plan({"project": "demo", "operations": [{"type": "move", "source": str(source), "target": str(target)}]})

            real_rename = os.rename

            def rename_with_cross_device_error(src, dst):
                if Path(src) == source and Path(dst) == target:
                    raise OSError(errno.EXDEV, "Invalid cross-device link")
                return real_rename(src, dst)

            with mock.patch("workengine.migration.os.rename", side_effect=rename_with_cross_device_error):
                result = manager.apply("demo")

            self.assertEqual(result["status"], "COMPLETED")
            self.assertFalse(source.exists())
            self.assertEqual(target.read_text(encoding="utf-8"), "cross-device")

    def test_apply_recovers_when_verified_source_and_target_both_exist(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            target = root / "target"
            source.write_text("same", encoding="utf-8")
            manager = MigrationManager(ServerOSStore(root / "state"))
            manager.plan({"project": "demo", "operations": [{"type": "move", "source": str(source), "target": str(target)}]})
            target.write_text("same", encoding="utf-8")

            result = manager.apply("demo")

            self.assertEqual(result["status"], "COMPLETED")
            self.assertFalse(source.exists())
            self.assertTrue(target.exists())

    def test_plan_rejects_symlinks_in_source_tree(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "source"
            source.mkdir()
            (source / "data").write_text("content", encoding="utf-8")
            (source / "link").symlink_to(source / "data")
            manager = MigrationManager(ServerOSStore(root / "state"))

            with self.assertRaisesRegex(MigrationError, "symlink"):
                manager.plan({"project": "demo", "operations": [{"type": "move", "source": str(source), "target": str(root / "target")}]})

    def test_verify_allows_later_operation_to_add_a_new_nested_target(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tree = root / "old-tree"
            tree.mkdir()
            (tree / "original").write_text("one", encoding="utf-8")
            extra = root / "extra"
            extra.write_text("two", encoding="utf-8")
            target = root / "new-tree"
            manager = MigrationManager(ServerOSStore(root / "state"))
            manager.plan({
                "project": "demo",
                "operations": [
                    {"type": "move", "source": str(tree), "target": str(target)},
                    {"type": "move", "source": str(extra), "target": str(target / "added" / "extra")},
                ],
            })

            manager.apply("demo")

            self.assertEqual(manager.verify("demo")["status"], "PASS")

    def test_plan_rejects_nested_target_before_its_parent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            tree = root / "old-tree"
            tree.mkdir()
            extra = root / "extra"
            extra.write_text("two", encoding="utf-8")
            target = root / "new-tree"
            manager = MigrationManager(ServerOSStore(root / "state"))

            with self.assertRaisesRegex(MigrationError, "ancestor target must be earlier"):
                manager.plan({
                    "project": "demo",
                    "operations": [
                        {"type": "move", "source": str(extra), "target": str(target / "added")},
                        {"type": "move", "source": str(tree), "target": str(target)},
                    ],
                })


if __name__ == "__main__":
    unittest.main()
