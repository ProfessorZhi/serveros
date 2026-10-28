#!/usr/bin/env python3
"""Offline source→archive check for the 9-Skill commander package.

The existing first-install builder is the only publisher. This test assembles a
variant source by copying the canonical 8 Skills and adding the #456
fleet-batch-commander Skill, then asks that builder for a ZIP. No network,
Creator, upload, install, OAuth or credential.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-v1-first-install"
sys.path.insert(0, str(ROOT))

_spec = importlib.util.spec_from_file_location(
    "fleet_chatgpt_package_builder", HERE / "build_first_install.py")
builder = importlib.util.module_from_spec(_spec)
sys.modules["fleet_chatgpt_package_builder"] = builder
_spec.loader.exec_module(builder)

BATCH_SKILL = (ROOT / "integrations" / "chatgpt-fleet-next" / "skills" /
               "fleet-batch-commander" / "SKILL.md")
CANONICAL = HERE / "plugin" / "skills"
_spec = importlib.util.spec_from_file_location(
    "fleet_commander_batch_assembler", HERE / "assemble_commander_batch_source.py")
assembler = importlib.util.module_from_spec(_spec)
sys.modules["fleet_commander_batch_assembler"] = assembler
_spec.loader.exec_module(assembler)
OBSERVED_URL = "https://serveros-fleet-next.tail3d8b76.ts.net/mcp"


def observation() -> dict:
    base = f"{urlsplit(OBSERVED_URL).scheme}://{urlsplit(OBSERVED_URL).hostname}"
    return {
        "schema": builder.ENDPOINT_OBSERVATION_SCHEMA,
        "url": OBSERVED_URL,
        "source": "LIVE",
        "observed_at": "2026-10-22T14:03:06+08:00",
        "reachable": True,
        "tls": {"scheme": "https", "verified": True},
        "discovery": {
            "protected_resource": {
                "url": base + "/.well-known/oauth-protected-resource",
                "status": 200, "receipt_ref": "evidence/obs/prm",
            },
            "authorization_server": {
                "url": base + "/.well-known/oauth-authorization-server",
                "status": 200, "receipt_ref": "evidence/obs/as",
                "issuer": base,
                "scopes_supported": list(builder.FLEET_SCOPES),
            },
        },
        "receipt_ref": "evidence/obs/endpoint",
    }


class Variant:
    def __init__(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fleet-commander-9-")
        self.root = Path(self._tmp.name)
        self.source = self.root / "plugin"
        self.source_map = assembler.assemble(self.source)
        self.observation = self.root / "observation.json"
        self.observation.write_text(json.dumps(observation()), encoding="utf-8")
        self.lock = json.loads((HERE / "brand-assets.lock.json").read_text(encoding="utf-8"))
        self.out = self.root / "dist"

    def build(self, out: Path | None = None) -> dict:
        return builder.build(
            self.source, out or self.out, self.lock,
            next_mcp_url=OBSERVED_URL, observation=self.observation,
            app_backed=False, plugin_id=None, registration=None, v1_mcp_url=None)

    def close(self) -> None:
        self._tmp.cleanup()


class TheNineSkillArchive(unittest.TestCase):
    def setUp(self) -> None:
        self.v = Variant()
        self.addCleanup(self.v.close)
        self.record = self.v.build()

    def test_source_map_names_every_skill_and_origin(self) -> None:
        self.assertEqual(self.v.source_map["skill_count"], 9)
        names = {row["name"] for row in self.v.source_map["skills"]}
        self.assertIn("fleet-batch-commander", names)
        self.assertEqual(len(names), 9)
        for row in self.v.source_map["skills"]:
            self.assertIn(row["origin"], {
                "integrations/fleet-v1-first-install/plugin/skills/"
                + row["name"] + "/SKILL.md",
                "integrations/chatgpt-fleet-next/skills/fleet-batch-commander/SKILL.md",
            })

    def test_archive_contains_the_eight_plus_the_new_skill(self) -> None:
        self.assertEqual(len(self.record["skills"]), 9)
        self.assertIn("fleet-batch-commander", self.record["skills"])
        with zipfile.ZipFile(self.v.out / self.record["archive"]) as zf:
            names = set(zf.namelist())
        for skill in self.record["skills"]:
            self.assertIn(f"skills/{skill}/SKILL.md", names)

    def test_eight_stay_byte_identical_and_the_new_one_is_the_456_skill(self) -> None:
        with zipfile.ZipFile(self.v.out / self.record["archive"]) as zf:
            for path in sorted(CANONICAL.glob("*/SKILL.md")):
                name = path.parent.name
                self.assertEqual(zf.read(f"skills/{name}/SKILL.md"), path.read_bytes())
            self.assertEqual(zf.read("skills/fleet-batch-commander/SKILL.md"),
                             BATCH_SKILL.read_bytes())

    def test_identity_and_binding_are_preserved(self) -> None:
        with zipfile.ZipFile(self.v.out / self.record["archive"]) as zf:
            manifest = json.loads(zf.read("plugin.json"))
            binding = json.loads(zf.read("mcp.json"))
        self.assertEqual(manifest["name"], "serveros-fleet-ingress")
        self.assertEqual(manifest["version"], "1.0.3")
        self.assertEqual(manifest["extensions"]["com.openai"]["interface"]["displayName"],
                         "ServerOS Fleet")
        server = binding["mcpServers"]["serveros-fleet"]
        self.assertEqual(server["type"], "streamable-http")
        self.assertEqual(server["url"], OBSERVED_URL)
        self.assertEqual(server["extensions"]["com.openai"]["auth"]["type"], "oauth")
        self.assertEqual(server["extensions"]["com.openai"]["auth"]["client"]["mode"], "dcr")

    def test_archive_is_deterministic(self) -> None:
        second = self.v.root / "dist2"
        other = self.v.build(out=second)
        self.assertEqual(other["archive_sha256"], self.record["archive_sha256"])
        self.assertEqual(other["content_sha256"], self.record["content_sha256"])

    def test_archive_carries_no_credential_shaped_member(self) -> None:
        with zipfile.ZipFile(self.v.out / self.record["archive"]) as zf:
            for name in zf.namelist():
                data = zf.read(name)
                self.assertNotIn(b"ANTHROPIC_AUTH_TOKEN", data)
                self.assertNotIn(b"OPENAI_API_KEY", data)
                self.assertNotIn(b"BEGIN PRIVATE KEY", data)
                self.assertNotIn(b"sk-", data)


if __name__ == "__main__":
    unittest.main()
