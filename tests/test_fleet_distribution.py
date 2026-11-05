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
                           "openai": cls.trees["openai"],
                           "mcp": cls.trees["mcp"]}

    def test_every_committed_tree_matches_the_shared_source(self):
        self.assertEqual(self.b.stale(), [])

    def test_one_version_everywhere(self):
        shared = json.loads((SHARED / "plugin.json").read_text())["version"]
        manifests = {
            "claude-code": self.plugin_dirs["claude-code"] / ".claude-plugin/plugin.json",
            "codex": self.plugin_dirs["codex"] / ".codex-plugin/plugin.json",
            "openai": self.plugin_dirs["openai"] / "plugin.json",
            "openai-codex": self.plugin_dirs["openai"] / ".codex-plugin/plugin.json",
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
            "mcp": self.plugin_dirs["mcp"] / "mcp.json",
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

    def test_the_identity_carries_no_version_and_never_reuses_the_legacy_plugin(self):
        legacy = json.loads((ROOT / "plugins/serveros-fleet-ingress/.claude-plugin/plugin.json").read_text())
        self.assertNotEqual(self.doc["identity"]["plugin"], legacy["name"])
        openai = json.loads((self.plugin_dirs["openai"] / "plugin.json").read_text())
        self.assertEqual(openai["name"], self.doc["clients"]["openai"]["plugin_name"])
        self.assertEqual(openai["extensions"]["com.openai"]["interface"]["displayName"],
                         "ServerOS Fleet")
        self.assertEqual(openai["version"], self.doc["version"])
        for key in ("plugin", "marketplace", "mcp_server", "display_name"):
            self.assertNotRegex(self.doc["identity"][key].lower(), r"v\d", key)

    def test_the_chatgpt_package_is_bound_to_the_registered_app(self):
        """ChatGPT runs a plugin on the web only when its tools come from a
        registered App; a package declaring its own MCP server is desktop-only."""
        target = self.doc["clients"]["openai"]
        tree = self.plugin_dirs["openai"]
        self.assertFalse((tree / "mcp.json").exists())
        app = json.loads((tree / ".app.json").read_text())
        self.assertEqual(app, {"apps": {target["plugin_name"]: {"id": target["app_id"]}}})
        for manifest in (json.loads((tree / "plugin.json").read_text())["extensions"]["com.openai"],
                         json.loads((tree / ".codex-plugin/plugin.json").read_text())):
            self.assertEqual(manifest["apps"], "./.app.json")
            interface = manifest["interface"]
            self.assertEqual(interface["displayName"], "ServerOS Fleet")
            for slot in ("composerIcon", "logo"):
                self.assertTrue((tree / interface[slot]).is_file(), slot)
        self.assertNotIn("mcpServers", (tree / "plugin.json").read_text())

    def test_install_removes_the_identity_it_replaces(self):
        replaced = [r["install_id"] for r in self.doc["identity"]["replaces"]]
        self.assertIn("serveros-fleet-v1@serveros-v1", replaced)

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

    def test_install_launches_the_resolved_cli_so_a_windows_cmd_shim_works(self):
        from unittest import mock
        calls = []

        def fake_run(argv, **_kw):
            calls.append(argv)
            return mock.Mock(returncode=0, stdout="")

        shim = r"C:\Users\someone\AppData\Roaming\npm\claude.cmd"
        with mock.patch.object(self.b.shutil, "which", return_value=shim), \
                mock.patch.object(self.b.subprocess, "run", side_effect=fake_run):
            self.assertEqual(self.b.install("claude-code"), 0)
        self.assertTrue(calls)
        self.assertTrue(all(argv[0] == shim for argv in calls), calls)
        self.assertIn([shim, "plugin", "uninstall", "serveros-fleet-v1@serveros-v1"], calls)


if __name__ == "__main__":
    unittest.main()


class TheInstallersAreOneCommand(unittest.TestCase):
    def test_install_sh_parses_and_plans_from_a_checkout(self):
        import subprocess
        script = ROOT / "distribution/install/install.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        out = subprocess.run(["bash", str(script), "--dry-run"], text=True,
                             capture_output=True, env={"PATH": "/usr/bin:/bin", "HOME": "/tmp"})
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("from checkout", out.stdout)
        self.assertIn("plugin_asdk_app_6aec2811173881918325a3dc82742d8d", out.stdout)

    def test_no_installer_handles_a_credential(self):
        for name in ("install.sh", "install.ps1"):
            text = (ROOT / "distribution/install" / name).read_text().lower()
            for word in ("password=", "token=", "totp=", "client_secret"):
                self.assertNotIn(word, text, name)
