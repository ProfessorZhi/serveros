"""The V1 candidate beside the surface in service: a second plugin identity and
a second OAuth store, so neither copy touches the other."""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fleet_mcp.config import load_config  # noqa: E402
from fleet_mcp.runtime import default_store_root  # noqa: E402

URL = "https://serveros-fleet-v1.example.ts.net/mcp"


def plugin_script():
    spec = importlib.util.spec_from_file_location("fleet_plugin_side_test",
                                                  ROOT / "scripts/fleet_plugin.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class SidePlugin(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.out = Path(self._td.name) / "side"
        self.fp = plugin_script()
        self.plugin = self.fp.generate_side(self.out, URL)

    def tearDown(self):
        self._td.cleanup()

    def test_every_identity_differs_from_the_plugin_in_service(self):
        manifest = json.loads((self.plugin / ".claude-plugin/plugin.json").read_text())
        servers = json.loads((self.plugin / ".mcp.json").read_text())["mcpServers"]
        market = json.loads((self.out / ".claude-plugin/marketplace.json").read_text())
        committed = json.loads((ROOT / "plugins/serveros-fleet-ingress/.mcp.json").read_text())["mcpServers"]
        self.assertNotEqual(manifest["name"], "serveros-fleet-ingress")
        self.assertNotEqual(market["name"], "serveros")
        self.assertEqual(list(servers), ["serveros-fleet-v1"])
        self.assertNotIn("serveros-fleet-v1", committed)
        (side,) = servers.values()
        (prod,) = committed.values()
        self.assertEqual(side["url"], URL)
        self.assertNotEqual(side["oauth"]["callbackPort"], prod["oauth"]["callbackPort"])

    def test_same_skills_and_images_as_the_committed_plugin(self):
        committed = ROOT / "plugins/serveros-fleet-ingress"
        for sub in ("skills", "assets"):
            ours = sorted(p.relative_to(self.plugin / sub).as_posix()
                          for p in (self.plugin / sub).rglob("*") if p.is_file())
            theirs = sorted(p.relative_to(committed / sub).as_posix()
                            for p in (committed / sub).rglob("*") if p.is_file())
            self.assertEqual(ours, theirs, sub)

    def test_the_committed_plugin_is_untouched(self):
        self.assertEqual(self.fp.check(self.fp.DEFAULT_NEXT_MCP_URL), 0)


class SeparateOAuthStore(unittest.TestCase):
    def test_oauth_store_root_overrides_the_default(self):
        with tempfile.TemporaryDirectory() as td:
            binding = Path(td) / "fleet-v1.yaml"
            binding.write_text("schema: serveros/fleet-mcp.local/v1\n"
                               "device_ref: device:tokyo-shared-dev-01\n"
                               f"server:\n  port: 3411\n  oauth_store_root: {td}/oauth-v1\n",
                               encoding="utf-8")
            config = load_config(binding)
            self.assertEqual(config.server.port, 3411)
            self.assertEqual(default_store_root(config), Path(td) / "oauth-v1")


if __name__ == "__main__":
    unittest.main()
