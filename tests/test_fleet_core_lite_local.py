"""The hub's own device: Core-Lite read from the file its Core publishes."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fleet_mcp import core_lite_local as src  # noqa: E402
from fleet_mcp.config import load_config  # noqa: E402
from test_fleet_core_lite_source import ACCOUNT, DEVICE, NOW, OTHER, admit, document  # noqa: E402

PUBLISH = ROOT / "native/node-core/core-lite/deploy/publish-status.sh"


class LocalFileSource(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.dir = Path(self._td.name)
        self.path = self.dir / "status.json"

    def tearDown(self):
        self._td.cleanup()

    def write(self, doc, mode=0o600):
        self.path.write_text(json.dumps(doc), encoding="utf-8")
        os.chmod(self.path, mode)

    def test_a_published_file_is_admitted_for_this_device(self):
        self.write(document())
        summary = admit(src.LocalFileCoreLiteSource(self.path, DEVICE))
        self.assertEqual(summary["connected"], "CONNECTED", summary)
        self.assertIn("cpu", json.dumps(summary["observations"]))

    def test_another_device_is_refused_not_answered(self):
        self.write(document())
        summary = admit(src.LocalFileCoreLiteSource(self.path, DEVICE), device=OTHER)
        self.assertNotEqual(summary["connected"], "CONNECTED")
        self.assertNotIn("cpu", json.dumps(summary.get("observations", {})))

    def test_no_file_is_no_reading(self):
        summary = admit(src.LocalFileCoreLiteSource(self.path, DEVICE))
        self.assertNotEqual(summary["connected"], "CONNECTED")

    def test_a_widened_file_is_not_trusted(self):
        self.write(document(), mode=0o644)
        summary = admit(src.LocalFileCoreLiteSource(self.path, DEVICE))
        self.assertNotEqual(summary["connected"], "CONNECTED")

    def test_a_symlink_is_not_followed(self):
        real = self.dir / "real.json"
        real.write_text(json.dumps(document()), encoding="utf-8")
        os.chmod(real, 0o600)
        self.path.symlink_to(real)
        summary = admit(src.LocalFileCoreLiteSource(self.path, DEVICE))
        self.assertNotEqual(summary["connected"], "CONNECTED")

    def test_garbage_is_not_trusted(self):
        self.path.write_text("not json", encoding="utf-8")
        os.chmod(self.path, 0o600)
        summary = admit(src.LocalFileCoreLiteSource(self.path, DEVICE))
        self.assertNotEqual(summary["connected"], "CONNECTED")


class TheBindingNamesTheFile(unittest.TestCase):
    def test_core_lite_snapshot_path(self):
        with tempfile.TemporaryDirectory() as td:
            binding = Path(td) / "fleet.yaml"
            binding.write_text("schema: serveros/fleet-mcp.local/v1\n"
                               f"device_ref: {DEVICE}\n"
                               f"core_lite:\n  snapshot_path: {td}/status.json\n",
                               encoding="utf-8")
            self.assertEqual(load_config(binding).core_lite.snapshot_path,
                             Path(td) / "status.json")

    def test_a_relative_path_is_refused(self):
        from fleet_mcp.errors import FleetRefusal
        with tempfile.TemporaryDirectory() as td:
            binding = Path(td) / "fleet.yaml"
            binding.write_text("schema: serveros/fleet-mcp.local/v1\n"
                               f"device_ref: {DEVICE}\n"
                               "core_lite:\n  snapshot_path: status.json\n", encoding="utf-8")
            with self.assertRaises(FleetRefusal):
                load_config(binding)


class PublishScript(unittest.TestCase):
    def test_it_writes_one_owner_only_file_atomically(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir()
            (bin_dir / "serveros-node-resource-snapshot").write_text(
                "#!/bin/sh\necho '{\"schema\":\"serveros/node-resource-snapshot.v1\"}'\n")
            (bin_dir / "core-lite-status").write_text(
                "#!/bin/sh\necho '{\"schema\":\"serveros/core-lite-status.v1\",\"node\":\"x\"}'\n")
            for f in bin_dir.iterdir():
                f.chmod(0o755)
            out = Path(td) / "state" / "status.json"
            result = subprocess.run(["bash", str(PUBLISH), "--bin", str(bin_dir), "--profile",
                                     "/dev/null", "--out", str(out)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
            self.assertEqual(json.loads(out.read_text())["node"], "x")
            self.assertEqual([p.name for p in out.parent.iterdir()], ["status.json"])

    def test_a_failing_builder_keeps_the_previous_file(self):
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            bin_dir = Path(td) / "bin"
            bin_dir.mkdir()
            (bin_dir / "serveros-node-resource-snapshot").write_text("#!/bin/sh\necho '{}'\n")
            (bin_dir / "core-lite-status").write_text("#!/bin/sh\nexit 3\n")
            for f in bin_dir.iterdir():
                f.chmod(0o755)
            out = Path(td) / "status.json"
            out.write_text('{"previous": true}')
            result = subprocess.run(["bash", str(PUBLISH), "--bin", str(bin_dir), "--profile",
                                     "/dev/null", "--out", str(out)], capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(json.loads(out.read_text()), {"previous": True})


if __name__ == "__main__":
    unittest.main()
