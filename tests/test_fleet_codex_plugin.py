"""The isolated Codex client adapter: format, shared source and no global config.

These are package/build tests. The real CLI handshake is a separate no-model
runner (`run_codex_client_e2e.py`), because a unit test should not depend on the
installed Codex version or start an app-server.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = ROOT / "integrations" / "fleet-cc-dev-plugin"
SHARED = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"
URL = "https://fleet-next.example.ts.net/mcp"


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


codex_builder = _load("fleet_codex_builder", HERE / "build_codex_plugin.py")
codex_fixture = _load("fleet_codex_fixture", HERE / "build_codex_local_fixture.py")


class TheCodexDevelopmentAdapter(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-adapter-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "plugin"

    def test_the_codex_layout_is_the_one_the_cli_reads(self) -> None:
        record = codex_builder.generate(skills_source=SHARED, out=self.out,
                                        next_mcp_url=URL)
        manifest = json.loads(
            (self.out / ".codex-plugin" / "plugin.json").read_text())
        binding = json.loads((self.out / ".codex-mcp.json").read_text())
        self.assertEqual(manifest["name"], codex_builder.DEV_PLUGIN_NAME)
        self.assertEqual(manifest["version"], codex_builder.DEV_VERSION)
        self.assertEqual(manifest["skills"], "./skills/")
        self.assertEqual(manifest["mcpServers"], "./.codex-mcp.json")
        self.assertEqual(binding["mcpServers"]["serveros-fleet"]["url"], URL)
        self.assertEqual(record["distribution"],
                         "DEVELOPMENT_ONLY_NOT_A_RELEASE")
        self.assertFalse(record["installable"])

    def test_every_skill_is_byte_identical_to_the_shared_source(self) -> None:
        record = codex_builder.generate(skills_source=SHARED, out=self.out,
                                        next_mcp_url=URL)
        names = sorted(p.name for p in SHARED.iterdir() if p.is_dir())
        self.assertEqual(record["skills"], names)
        for name in names:
            self.assertEqual((self.out / "skills" / name / "SKILL.md").read_bytes(),
                             (SHARED / name / "SKILL.md").read_bytes())

    def test_no_archive_or_brand_asset_is_written(self) -> None:
        codex_builder.generate(skills_source=SHARED, out=self.out,
                               next_mcp_url=URL)
        self.assertEqual([p for p in self.out.rglob("*")
                          if p.suffix.lower() in (".zip", ".mcpb")], [])
        self.assertEqual([p for p in self.out.rglob("*")
                          if p.suffix.lower() in (".png", ".jpg", ".svg")], [])

    def test_a_second_generation_refuses_to_blend(self) -> None:
        codex_builder.generate(skills_source=SHARED, out=self.out,
                               next_mcp_url=URL)
        with self.assertRaises(codex_builder.Refused):
            codex_builder.generate(skills_source=SHARED, out=self.out,
                                   next_mcp_url=URL)

    def test_out_inside_the_shared_source_is_refused(self) -> None:
        inside = SHARED / "generated-codex"
        with self.assertRaises(codex_builder.Refused):
            codex_builder.generate(skills_source=SHARED, out=inside,
                                   next_mcp_url=URL)
        self.assertFalse(inside.exists())


class TheCodexOwnedStdioTransport(unittest.TestCase):
    """The local trusted owner runtime is an OAuth-free Codex binding."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-owner-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.entry = self.tmp / "serveros-fleet-owned"
        self.entry.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.entry.chmod(0o755)
        self.config = self.tmp / "owner-config.yaml"
        self.config.write_text("owner: synthetic\n", encoding="utf-8")
        self.out = self.tmp / "plugin"

    def test_the_stdio_binding_is_local_and_has_no_url(self) -> None:
        record = codex_builder.generate(
            skills_source=SHARED, out=self.out, transport="stdio-owner",
            owned_entry=str(self.entry), owned_config=str(self.config))
        server = json.loads((self.out / ".codex-mcp.json").read_text())[
            "mcpServers"]["serveros-fleet"]
        self.assertEqual(server["command"], str(self.entry))
        self.assertEqual(server["args"], ["--stdio", "--config", str(self.config)])
        self.assertNotIn("url", server)
        self.assertEqual(record["transport"], "stdio-owner")
        self.assertIsNone(record["mcp_url"])

    def test_relative_or_missing_entry_is_refused(self) -> None:
        for index, entry in enumerate(("bin/serveros-fleet-owned",
                                      str(self.tmp / "missing"))):
            with self.subTest(entry=entry):
                out = self.tmp / f"refused-{index}"
                with self.assertRaises(codex_builder.Refused):
                    codex_builder.generate(
                        skills_source=SHARED, out=out, transport="stdio-owner",
                        owned_entry=entry, owned_config=str(self.config))
                self.assertFalse(out.exists())

    def test_http_mode_may_not_also_carry_a_local_owner_binding(self) -> None:
        with self.assertRaises(codex_builder.Refused):
            codex_builder.generate(
                skills_source=SHARED, out=self.out, next_mcp_url=URL,
                transport="http", owned_entry=str(self.entry),
                owned_config=str(self.config))
        self.assertFalse(self.out.exists())


class TheCodexLocalFixture(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="codex-fixture-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "fixture"

    def test_the_marketplace_points_at_the_local_plugin(self) -> None:
        record = codex_fixture.generate(out=self.out)
        marketplace = json.loads(
            (self.out / ".agents" / "plugins" / "marketplace.json").read_text())
        plugin = marketplace["plugins"][0]
        self.assertEqual(plugin["source"]["source"], "local")
        self.assertEqual(plugin["source"]["path"], "./plugin")
        self.assertEqual(plugin["name"], codex_fixture.FIXTURE_NAME)
        self.assertEqual(record["marketplace"], str(self.out))
        self.assertEqual(record["marketplace_manifest"],
                         str(self.out / ".agents" / "plugins" / "marketplace.json"))
        self.assertFalse(record["installable"])
        self.assertEqual(record["uploads"], [])

    def test_the_fixture_uses_stdio_with_explicit_log_and_state(self) -> None:
        codex_fixture.generate(out=self.out)
        binding = json.loads(
            (self.out / "plugin" / ".codex-mcp.json").read_text())
        server = binding["mcpServers"][codex_fixture.SERVER_NAME]
        self.assertEqual(server["command"], sys.executable)
        self.assertIn("--log", server["args"])
        self.assertIn("--state-dir", server["args"])
        self.assertNotIn("url", server)

    def test_the_fixture_carries_the_same_eight_skills(self) -> None:
        record = codex_fixture.generate(out=self.out)
        names = sorted(p.name for p in SHARED.iterdir() if p.is_dir())
        self.assertEqual(record["skills"], names)
        for name in names:
            self.assertEqual(
                (self.out / "plugin" / "skills" / name / "SKILL.md").read_bytes(),
                (SHARED / name / "SKILL.md").read_bytes())

    def test_the_fixture_does_not_touch_a_global_codex_home(self) -> None:
        codex_fixture.generate(out=self.out)
        # The generated tree is entirely below --out; no `.codex` directory is
        # created as a side effect.
        self.assertEqual(list(self.tmp.rglob(".codex")), [])


if __name__ == "__main__":
    unittest.main()
