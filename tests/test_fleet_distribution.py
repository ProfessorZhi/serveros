"""One Fleet Core, one shared source, three client adapters, one version."""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SHARED = ROOT / "integrations/fleet-v1-first-install/plugin"


def build_module():
    name = "fleet_distribution_build_test"
    if name not in sys.modules:
        spec = importlib.util.spec_from_file_location(name, ROOT / "distribution/build.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return sys.modules[name]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Distribution(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = build_module()
        cls.doc = cls.b.channels()
        cls.trees = {c: ROOT / cls.doc["clients"][c]["tree"] for c in cls.b.CLIENTS}
        plugin = cls.doc["identity"]["plugin"]
        cls.plugin_dirs = {"claude-code": cls.trees["claude-code"] / "plugins" / plugin,
                           "codex": cls.trees["codex"] / "plugins" / plugin,
                           "openai": cls.trees["openai"]}

    def test_every_committed_tree_matches_the_shared_source(self):
        self.assertEqual(self.b.stale(), [])

    def test_one_version_everywhere(self):
        shared = json.loads((SHARED / "plugin.json").read_text())["version"]
        manifests = {
            "claude-code": self.plugin_dirs["claude-code"] / ".claude-plugin/plugin.json",
            "codex": self.plugin_dirs["codex"] / ".codex-plugin/plugin.json",
            "openai": self.plugin_dirs["openai"] / "plugin.json",
        }
        for client, path in manifests.items():
            with self.subTest(client=client):
                self.assertEqual(json.loads(path.read_text())["version"], shared)

    def test_one_server_everywhere(self):
        url = self.doc["server"]["mcp_url"]
        server = self.doc["identity"]["mcp_server"]
        bindings = {
            "claude-code": self.plugin_dirs["claude-code"] / ".mcp.json",
            "codex": self.plugin_dirs["codex"] / ".codex-mcp.json",
            "openai": self.plugin_dirs["openai"] / "mcp.json",
        }
        for client, path in bindings.items():
            with self.subTest(client=client):
                servers = json.loads(path.read_text())["mcpServers"]
                self.assertEqual(list(servers), [server])
                self.assertEqual(servers[server]["url"], url)

    def test_the_same_skills_byte_for_byte(self):
        shared = {p.parent.name: p.read_bytes() for p in (SHARED / "skills").glob("*/SKILL.md")}
        self.assertGreaterEqual(len(shared), 13)
        for client, plugin in self.plugin_dirs.items():
            with self.subTest(client=client):
                got = {p.parent.name: p.read_bytes() for p in (plugin / "skills").glob("*/SKILL.md")}
                self.assertEqual(got, shared)

    def test_the_brand_assets_are_the_locked_ones(self):
        lock = json.loads((ROOT / self.doc["shared"]["brand_lock"]).read_text())
        for slot in lock["slots"]:
            for client, plugin in self.plugin_dirs.items():
                with self.subTest(client=client, slot=slot["slot"]):
                    self.assertEqual(sha(plugin / slot["shipped_path"]), slot["expected_sha256"])

    def test_the_v1_identity_never_reuses_the_legacy_one(self):
        legacy = json.loads((ROOT / "plugins/serveros-fleet-ingress/.claude-plugin/plugin.json").read_text())
        self.assertNotEqual(self.doc["identity"]["plugin"], legacy["name"])
        openai = json.loads((self.plugin_dirs["openai"] / "plugin.json").read_text())
        self.assertEqual(openai["name"], "serveros-fleet-v1")
        self.assertEqual(openai["extensions"]["com.openai"]["interface"]["displayName"],
                         "ServerOS Fleet V1")

    def test_no_secret_or_client_id_in_any_binding(self):
        for client, plugin in self.plugin_dirs.items():
            for path in plugin.rglob("*.json"):
                text = path.read_text().lower()
                with self.subTest(client=client, file=path.name):
                    for word in ("client_secret", "clientsecret", "\"clientid\"", "bearer ", "token\":"):
                        self.assertNotIn(word, text)

    def test_the_openai_zip_is_reproducible(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            first = self.b.zip_client("openai", Path(a))
            second = self.b.zip_client("openai", Path(b))
            self.assertEqual(sha(first), sha(second))
            self.assertTrue(first.name.endswith(f"-v{self.doc['version']}.zip"))

    def test_install_is_claude_code_only(self):
        with self.assertRaises(self.b.DistributionError):
            self.b.install("codex")


if __name__ == "__main__":
    unittest.main()
