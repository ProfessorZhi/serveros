#!/usr/bin/env python3
"""Offline contract tests for the ChatGPT plugin update preflight + matrix."""
from __future__ import annotations

import base64
import importlib.util
import json
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-app-overlay"
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location(
    "chatgpt_update_preflight", HERE / "chatgpt_update_preflight.py")
preflight = importlib.util.module_from_spec(spec)
sys.modules["chatgpt_update_preflight"] = preflight
spec.loader.exec_module(preflight)

FIXTURE = ROOT / "tests" / "data" / "fleet-overlay-fixture"
ICON = (FIXTURE / "assets" / "composerIcon.png").read_bytes()
LOGO = (FIXTURE / "assets" / "logo.png").read_bytes()
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8"
    "/x8AAusB9Wl2ZQAAAABJRU5ErkJggg==")
PLUGIN_ID = "plugin_asdk_app_TEST_PLUGIN"
RELEASE_ID = "PluginRelease_TEST_1"


def manifest(version: str, icon: str = "./assets/composerIcon.png",
             logo: str = "./assets/logo.png") -> dict:
    return {
        "name": "serveros-fleet",
        "version": version,
        "description": "TEST FIXTURE — not a real plugin",
        "extensions": {"com.openai": {
            "displayName": "ServerOS Fleet",
            "composerIcon": icon,
            "logo": logo,
        }},
    }


def make_zip(path: Path, files: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name in sorted(files):
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, files[name])


def files(version: str, *, app: bool = False, icon: bytes = ICON,
          omit_skill: bool = False) -> dict[str, bytes]:
    out = {
        "plugin.json": json.dumps(manifest(version)).encode(),
        "assets/composerIcon.png": icon,
        "assets/logo.png": LOGO,
    }
    if not omit_skill:
        out["skills/fleet-batch-commander/SKILL.md"] = b"---\nname: x\n---\n"
    if app:
        out[".app.json"] = json.dumps({"apps": {"x": {
            "id": "asdk_app_TEST_APP", "mcp": "https://example.invalid/mcp"}}}
        ).encode()
    return out


def previous() -> dict:
    return {
        "schema": "serveros/chatgpt-plugin-release.v1",
        "plugin_id": PLUGIN_ID,
        "release_id": RELEASE_ID,
        "name": "serveros-fleet",
        "version": "0.1.0",
        "members": [
            "plugin.json", "assets/composerIcon.png", "assets/logo.png",
            "skills/fleet-batch-commander/SKILL.md",
        ],
    }


class ThePreflightChecksTheUpdateGuard(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="chatgpt-update-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(self.tmp, True))
        self.prev = self.tmp / "previous.json"
        self.prev.write_text(json.dumps(previous()) + "\n", encoding="utf-8")

    def archive(self, name: str, payload: dict[str, bytes]) -> Path:
        path = self.tmp / name
        make_zip(path, payload)
        return path

    def test_valid_same_identity_complete_zip_passes(self) -> None:
        out = preflight.preflight(self.prev, self.archive("next.zip", files("0.2.0")),
                                  PLUGIN_ID, RELEASE_ID)
        self.assertEqual(out["verdict"], "PASS")
        self.assertFalse(out["uploaded"])
        self.assertFalse(out["creator_called"])
        self.assertFalse(out["production_verified"])
        self.assertFalse(out["app_backed"])
        self.assertEqual(out["icons"][0]["status"], "OK")

    def test_app_backed_package_is_marked_presentation_unknown(self) -> None:
        out = preflight.preflight(
            self.prev, self.archive("app.zip", files("0.2.0", app=True)),
            PLUGIN_ID, RELEASE_ID)
        self.assertTrue(out["app_backed"])
        self.assertEqual(out["presentation_status"],
                         "APP_BACKED_PRESENTATION_UNKNOWN")

    def test_exact_plugin_id_is_required(self) -> None:
        with self.assertRaises(preflight.Refused) as caught:
            preflight.preflight(self.prev, self.archive("x.zip", files("0.2.0")),
                                "plugin_asdk_app_OTHER", RELEASE_ID)
        self.assertEqual(caught.exception.code, "PLUGIN_ID_MISMATCH")

    def test_app_id_is_not_a_plugin_id(self) -> None:
        with self.assertRaises(preflight.Refused) as caught:
            preflight.preflight(self.prev, self.archive("x.zip", files("0.2.0")),
                                "asdk_app_TEST_APP", RELEASE_ID)
        self.assertEqual(caught.exception.code, "APP_ID_NOT_PLUGIN_ID")

    def test_stale_expected_release_id_is_refused(self) -> None:
        with self.assertRaises(preflight.Refused) as caught:
            preflight.preflight(self.prev, self.archive("x.zip", files("0.2.0")),
                                PLUGIN_ID, "PluginRelease_OLD")
        self.assertEqual(caught.exception.code, "STALE_EXPECTED_RELEASE_ID")

    def test_tar_gz_is_not_evidenced(self) -> None:
        path = self.tmp / "next.tar.gz"
        path.write_bytes(b"not a zip")
        with self.assertRaises(preflight.Refused) as caught:
            preflight.preflight(self.prev, path, PLUGIN_ID, RELEASE_ID)
        self.assertEqual(caught.exception.code, "UNSUPPORTED_ARCHIVE_FORMAT")

    def test_version_must_advance(self) -> None:
        for version in ("0.1.0", "0.0.9"):
            with self.subTest(version=version):
                with self.assertRaises(preflight.Refused) as caught:
                    preflight.preflight(
                        self.prev, self.archive(f"{version}.zip", files(version)),
                        PLUGIN_ID, RELEASE_ID)
                self.assertEqual(caught.exception.code, "VERSION_NOT_ADVANCING")

    def test_complete_zip_may_not_omit_a_previous_member(self) -> None:
        with self.assertRaises(preflight.Refused) as caught:
            preflight.preflight(
                self.prev,
                self.archive("omit.zip", files("0.2.0", omit_skill=True)),
                PLUGIN_ID, RELEASE_ID)
        self.assertEqual(caught.exception.code, "OMITTED_MEMBER")

    def test_one_pixel_icon_is_refused(self) -> None:
        with self.assertRaises(preflight.Refused) as caught:
            preflight.preflight(
                self.prev,
                self.archive("tiny.zip", files("0.2.0", icon=PNG_1X1)),
                PLUGIN_ID, RELEASE_ID)
        self.assertEqual(caught.exception.code, "ICON_INVALID")


class ThePresentationMatrixIsHonest(unittest.TestCase):
    def setUp(self) -> None:
        self.matrix = json.loads(
            (HERE / "chatgpt_presentation_matrix.json").read_text(encoding="utf-8"))

    def test_app_backed_avatar_is_never_a_promise(self) -> None:
        rows = {row["case"]: row for row in self.matrix["cases"]}
        for name in ("app_backed_skills_only", "app_backed_avatar_via_zip",
                     "app_id_used_as_plugin_id"):
            with self.subTest(case=name):
                row = rows[name]
                self.assertIn(row["status"], {"UNKNOWN", "BLOCKED"})
                self.assertFalse(row["verified"])
                self.assertNotEqual(row["status"], "SUPPORTED")

    def test_portable_metadata_is_doc_supported_but_not_verified(self) -> None:
        row = next(row for row in self.matrix["cases"]
                   if row["case"] == "portable_plugin_manifest_assets")
        self.assertEqual(row["status"], "SUPPORTED_BY_PUBLIC_DOC")
        self.assertFalse(row["verified"])

    def test_claude_code_is_not_a_chatgpt_update_path(self) -> None:
        row = next(row for row in self.matrix["cases"]
                   if row["case"] == "claude_code_plugin_marketplace")
        self.assertEqual(row["status"], "NOT_APPLICABLE")


class TheSourcePackageIsVersioned(unittest.TestCase):
    def test_new_skill_is_in_the_source_package(self) -> None:
        root = ROOT / "integrations" / "chatgpt-fleet-next"
        manifest = json.loads((root / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.1.1")
        self.assertTrue((root / "skills" / "fleet-batch-commander" / "SKILL.md").is_file())

    def test_package_stays_instruction_only(self) -> None:
        root = ROOT / "integrations" / "chatgpt-fleet-next"
        for name in (".app.json", ".mcp.json", "mcp.json"):
            with self.subTest(name=name):
                self.assertFalse((root / name).exists())


if __name__ == "__main__":
    unittest.main()
