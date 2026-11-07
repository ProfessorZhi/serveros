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
                if client == "openai":
                    continue  # the registered App owns ChatGPT's icon; the package carries none
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
        app_fields = json.loads((ROOT / "distribution/openai-app.json").read_text())
        root = json.loads((tree / "plugin.json").read_text())
        codex = json.loads((tree / ".codex-plugin/plugin.json").read_text())
        for manifest in (root["extensions"]["com.openai"], codex):
            self.assertEqual(manifest["apps"], "./.app.json")
            # The App owns these; an upload that changes them is refused.
            self.assertEqual(manifest["interface"], app_fields["interface"])
        for manifest in (root, codex):
            self.assertEqual(manifest["author"], app_fields["author"])
            self.assertEqual(manifest["description"], app_fields["description"])
        self.assertEqual(list(root), ["$schema", "author", "description", "extensions", "name", "version"])
        self.assertNotIn("mcpServers", (tree / "plugin.json").read_text())
        self.assertFalse((tree / "assets").exists())

    def test_the_repository_root_is_the_marketplace_for_both_clis(self):
        """`marketplace add ProfessorZhi/serveros` resolves to the committed plugins."""
        for client, rel in self.b.ROOT_MARKETPLACES.items():
            with self.subTest(client=client):
                market = json.loads((ROOT / rel).read_text())
                self.assertEqual(market["name"], self.doc["identity"]["marketplace"])
                (entry,) = market["plugins"]
                self.assertEqual(entry["name"], self.doc["identity"]["plugin"])
                source = entry["source"] if isinstance(entry["source"], str) else entry["source"]["path"]
                self.assertEqual((ROOT / source).resolve(), self.plugin_dirs[client].resolve())
                # A sparse checkout of these paths is enough to install.
                self.assertTrue(all(s.split("/")[0] in (rel.parts[0], "distribution")
                                    for s in self.b.SPARSE[client]))
                self.assertTrue(source.removeprefix("./").startswith(self.b.SPARSE[client][1]))

    def test_the_root_codex_marketplace_file_is_committed_not_ignored(self):
        import subprocess
        out = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q",
                              ".agents/plugins/marketplace.json"])
        self.assertEqual(out.returncode, 1, "the root Codex marketplace file is gitignored")
        stray = subprocess.run(["git", "-C", str(ROOT), "check-ignore", "-q", ".agents/skills/x"])
        self.assertEqual(stray.returncode, 0, "anything else under the root .agents/ must stay ignored")

    def test_install_removes_the_identity_it_replaces(self):
        replaced = [r["install_id"] for r in self.doc["identity"]["replaces"]]
        self.assertIn("serveros-fleet-v1@serveros-v1", replaced)
        self.assertIn("serveros-fleet-ingress@serveros", replaced)

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
    def _dry_run(self, *args, clis=("claude", "codex")):
        import subprocess
        bindir = Path(tempfile.mkdtemp())
        for cli in clis:  # stand-ins that must never run on a dry run
            exe = bindir / cli
            exe.write_text("#!/bin/sh\nexit 99\n")
            exe.chmod(0o755)
        script = ROOT / "distribution/install/install.sh"
        self.assertEqual(subprocess.run(["bash", "-n", str(script)]).returncode, 0)
        out = subprocess.run(["bash", str(script), "--dry-run", *args], text=True, capture_output=True,
                             env={"PATH": f"{bindir}:/usr/bin:/bin", "HOME": "/tmp"})
        self.assertEqual(out.returncode, 0, out.stderr)
        return out.stdout

    def test_install_sh_installs_both_clis_from_github_main(self):
        out = self._dry_run()
        self.assertIn("+ claude plugin marketplace add https://github.com/ProfessorZhi/serveros.git#main "
                      "--sparse .claude-plugin distribution/claude-code", out)
        self.assertIn("+ codex plugin marketplace add ProfessorZhi/serveros --ref main "
                      "--sparse .agents --sparse distribution/codex", out)
        for retired in ("serveros-fleet-v1@serveros-v1", "serveros-fleet-ingress@serveros"):
            self.assertIn(f"+ claude plugin uninstall {retired}", out)
        self.assertIn("fleet-latest/serveros-fleet-openai.zip", out)
        self.assertIn("plugin_asdk_app_6aed2ec2e0e48191b438082e444c3210", out)

    def test_install_sh_pins_a_release_tag_or_uses_this_checkout(self):
        self.assertIn("serveros.git#fleet-v1.3.2", self._dry_run("--version", "1.3.2"))
        local = self._dry_run("--local")
        self.assertIn(f"+ claude plugin marketplace add {ROOT}\n", local)
        self.assertIn(f"+ codex plugin marketplace add {ROOT}\n", local)

    def test_no_installer_handles_a_credential(self):
        for name in ("install.sh", "install.ps1"):
            text = (ROOT / "distribution/install" / name).read_text().lower()
            for word in ("password=", "token=", "totp=", "client_secret"):
                self.assertNotIn(word, text, name)
