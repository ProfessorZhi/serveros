"""Exercise the shared installer through the real Next-only --no-start path.

No actual systemd call, login, network request, or real user file is touched.
The unit is created only inside a temporary directory, using a deliberately
permissive umask to ensure the file itself is not group-writable.
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.deploy import next_install  # noqa: E402


class RealNoStartReceipt(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="fleet-next-unit-receipt-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source"
        launcher = self.source / "fleet_mcp" / "bin" / "serveros-fleet-mcp"
        launcher.parent.mkdir(parents=True)
        launcher.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        launcher.chmod(0o755)
        (self.source / "fleet").mkdir()
        template = self.source / "fleet_mcp" / "deploy" / "serveros-fleet-next-mcp.service"
        template.parent.mkdir(parents=True)
        template.write_text(
            "[Unit]\nDescription=Isolated test unit\n"
            "[Service]\nExecStart=@FLEET_MCP_RUNTIME@/fleet_mcp/bin/serveros-fleet-mcp "
            "--config @FLEET_MCP_CONFIG@\n", encoding="utf-8")
        self.config = self.root / "next-config.yaml"
        self.config.write_text(
            "device_ref: device:tokyo-shared-dev-01\n"
            "server:\n  bind: 127.0.0.1\n  port: 3411\n",
            encoding="utf-8")
        self.unit_dir = self.root / "units"

    def test_no_start_receipt_names_real_unit_and_hardens_permissions(self):
        mod = next_install._load_legacy_installer()
        args = SimpleNamespace(
            source=str(self.source), revision="a" * 40, label="test",
            config=str(self.config), runtime_root=str(self.root / "runtime"),
            composition="ingress-next", unit_dir=str(self.unit_dir),
            no_start=True, force=False, dry_run=False, health_timeout=1.0,
        )
        output = io.StringIO()
        with mock.patch.object(mod, "systemctl") as systemctl, \
             contextlib.redirect_stdout(output):
            old_umask = os.umask(0o002)
            try:
                result = mod.install(args)
            finally:
                os.umask(old_umask)

        self.assertEqual(result, 0)
        receipt = json.loads(output.getvalue())
        unit = self.unit_dir / "serveros-fleet-next-mcp.service"
        self.assertEqual(receipt["unit_path"], str(unit))
        self.assertFalse(receipt["started"])
        self.assertTrue(unit.is_file())
        self.assertEqual(stat.S_IMODE(unit.stat().st_mode), 0o600)
        self.assertIn("ingress-next/current", unit.read_text(encoding="utf-8"))
        self.assertNotIn("@FLEET_MCP_", unit.read_text(encoding="utf-8"))
        self.assertEqual(systemctl.call_args_list, [mock.call("daemon-reload")])
        current = self.root / "runtime" / "ingress-next" / "current"
        self.assertTrue(current.is_symlink())


if __name__ == "__main__":
    unittest.main()
