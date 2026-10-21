"""The shipped Fleet-only tree must not require the ServerOS runtime package."""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


class StandaloneControl(unittest.TestCase):
    def test_fleet_only_artifact_can_start_and_read_without_runtime_sources(self):
        root = Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as tmp:
            bundle = Path(tmp)
            for name in ("fleet_mcp", "fleet"):
                shutil.copytree(root / name, bundle / name,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            self.assertFalse((bundle / "runtime").exists())
            script = (
                "import sys; sys.path.insert(0, " + repr(str(bundle)) + "); "
                "from fleet_mcp.config import FleetConfig; "
                "from fleet_mcp.fleet import FleetService, Principal; "
                "s=FleetService(FleetConfig(source=None,"
                "local_device_ref='device:tokyo-shared-dev-01'),providers={}); "
                "r=s.list_sessions(Principal(name='fixture',scopes=frozenset(['fleet:read']),"
                "credential_kind='pat')); "
                "assert r['total']==0 and r['devices_unreachable']; "
                "assert not any(k=='runtime' or k.startswith('runtime.') "
                "or k.startswith('serveros_runtime') for k in sys.modules)"
            )
            result = subprocess.run(
                [sys.executable, "-I", "-c", script], cwd=bundle,
                env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"),
                capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
