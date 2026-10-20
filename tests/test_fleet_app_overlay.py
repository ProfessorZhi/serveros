#!/usr/bin/env python3
"""The private App-backed overlay builder: what it preserves, and what it refuses.

Two builders now live in this repository and they produce opposite things. The
one in `integrations/chatgpt-fleet-next/` builds an instruction-only plugin and
refuses `.app.json`, `mcp.json` and a `.codex-plugin/` layout. The one here
builds an overlay for an ALREADY-INSTALLED private plugin, so it must carry the
app reference and the compatibility manifest — the same three things the other
one refuses. The official documentation read on 2026-10-15 is what makes that
not a stylistic choice: a public submission ZIP "cannot currently be submitted"
if it contains app references, so a ZIP that is right for one path is wrong for
the other by rule.

The first thing tested is therefore that the two do not overlap, and the rest is
about the one property an overlay exists for: it changes skills, assets and the
version, and **nothing else**. The plugin id, the name, the `displayName` and the
`apps` reference are pinned, and a source that moves any of them is refused —
because that is a different plugin wearing this one's release slot.

Nothing here uploads, creates, updates, rolls back or contacts anything. The
icon checks are this repository's own rules, not the platform's; the platform
documents no format, size or dimension limit at all, and every report carries
`renders_as: None` so that verifying a file is never read as a claim about how a
client draws it.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

OVERLAY_DIR = ROOT / "integrations" / "fleet-app-overlay"
FIXTURE = ROOT / "tests" / "data" / "fleet-overlay-fixture"
INSTRUCTION_ONLY = ROOT / "integrations" / "chatgpt-fleet-next"

BASELINE = json.loads((OVERLAY_DIR / "baseline.json").read_text(encoding="utf-8"))


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bo = _load("fleet_build_overlay", OVERLAY_DIR / "build_overlay.py")
bz = _load("fleet_build_zip_instruction_only", INSTRUCTION_ONLY / "build_zip.py")


def png(width: int, height: int, rgb=(62, 111, 176)) -> bytes:
    raw = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        body = tag + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr)
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


class _Source:
    """A writable copy of the fixture, plus a matching baseline."""

    def __init__(self, test: unittest.TestCase, *, baseline: dict | None = None):
        self._tmp = Path(tempfile.mkdtemp(prefix="fleet-overlay-"))
        test.addCleanup(shutil.rmtree, self._tmp, True)
        self.root = self._tmp / "src"
        shutil.copytree(FIXTURE, self.root)
        self.out = self._tmp / "dist"
        self.baseline = copy.deepcopy(baseline or BASELINE)

    def manifest(self) -> dict:
        return json.loads((self.root / "plugin.json").read_text(encoding="utf-8"))

    def set_manifest(self, **changes) -> None:
        manifest = self.manifest()
        manifest.update(changes)
        (self.root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    def set_openai(self, **changes) -> None:
        manifest = self.manifest()
        manifest["extensions"]["com.openai"].update(changes)
        (self.root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")

    def build(self, baseline: dict | None = None) -> dict:
        return bo.build(self.root, self.out, baseline or self.baseline)


def _refused(test: unittest.TestCase, source: _Source,
             baseline: dict | None = None) -> str:
    """The refusal message, or a test failure naming what built instead."""
    try:
        source.build(baseline)
    except bo.Refused as exc:
        return str(exc)
    test.fail("expected a refusal, but the build succeeded")
    return ""


class TheTwoBuildersCannotBeConfused(unittest.TestCase):
    """One refuses exactly what the other requires. That is the safety property."""

    def test_the_instruction_only_builder_refuses_an_overlay_tree(self):
        with self.assertRaises(bz.Refused) as caught:
            bz.collect(FIXTURE)
        message = str(caught.exception)
        self.assertTrue("generated plugin layout" in message or "live MCP/App" in message,
                        message)

    def test_the_overlay_builder_refuses_a_tree_with_no_openai_extension(self):
        src = _Source(self)
        manifest = src.manifest()
        del manifest["extensions"]
        (src.root / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
        with self.assertRaises(bo.Refused) as caught:
            src.build()
        self.assertIn("extensions.com.openai", str(caught.exception))

    def test_the_overlay_archive_says_which_path_it_is_for(self):
        record = _Source(self).build()
        self.assertEqual(record["distribution"], "PRIVATE_APP_BACKED_OVERLAY_ONLY")
        self.assertIn("PRIVATE", record["warning"])
        self.assertFalse(record["creates_plugin"])
        self.assertEqual(record["writes"], [])


class TheIdentityDoesNotMove(unittest.TestCase):

    def test_the_fixture_builds_and_preserves_the_pinned_identity(self):
        record = _Source(self).build()
        self.assertEqual(record["plugin_id"],
                         "plugin_asdk_app_6ace0c9c0dfc81918ed5f0fac18182b6")
        self.assertIn("app binding preserved", " ".join(record["identity_notes"]))
        self.assertTrue(any("displayName unchanged" in n for n in record["identity_notes"]))

    def test_a_changed_display_name_is_refused(self):
        src = _Source(self)
        src.set_openai(displayName="Fleet Next")
        self.assertIn("displayName", _refused(self, src))

    def test_a_changed_apps_reference_is_refused(self):
        src = _Source(self)
        src.set_openai(apps="./other.json")
        self.assertIn("apps", _refused(self, src))

    def test_an_apps_reference_to_a_missing_file_is_refused(self):
        # The baseline is moved too, so the identity check does not fire first:
        # this is the second check on its own — `apps` pointing at something
        # that is not in the source, which is a binding about to be dropped.
        src = _Source(self)
        src.set_openai(apps="./.app-absent.json")
        src.baseline["apps"] = "./.app-absent.json"
        self.assertIn("not in the source", _refused(self, src))

    def test_an_apps_reference_that_escapes_the_root_is_refused(self):
        src = _Source(self)
        src.set_openai(apps="./../outside.json")
        src.baseline["apps"] = "./../outside.json"
        self.assertIn("escapes", _refused(self, src))

    def test_a_changed_compatibility_display_name_is_refused(self):
        src = _Source(self)
        compat = json.loads((src.root / ".codex-plugin" / "plugin.json").read_text(encoding="utf-8"))
        compat["interface"]["displayName"] = "Fleet Next"
        (src.root / ".codex-plugin" / "plugin.json").write_text(json.dumps(compat), encoding="utf-8")
        with self.assertRaises(bo.Refused):
            src.build()

    def test_a_version_that_does_not_advance_is_refused(self):
        src = _Source(self)
        src.baseline["version"] = "0.1.1"
        self.assertIn("does not advance", _refused(self, src))

    def test_a_non_semver_version_is_refused(self):
        src = _Source(self)
        src.set_manifest(version="0.2")
        self.assertIn("MAJOR.MINOR.PATCH", _refused(self, src))

    def test_an_unpinned_field_is_skipped_rather_than_guessed(self):
        # The baseline pins `name` to null because we do not know it. A build
        # that changed it must still succeed: a check against a guessed value
        # would pass for the wrong reason.
        src = _Source(self)
        src.set_manifest(name="something-else")
        self.assertEqual(src.build()["name"], "something-else")


class TheIconSlotsAreCheckedButNothingIsClaimed(unittest.TestCase):

    def test_a_compliant_icon_reports_its_dimensions_and_no_rendering_claim(self):
        record = _Source(self).build()
        for slot in ("composerIcon", "logo"):
            with self.subTest(slot=slot):
                report = record["icons"][slot]
                self.assertEqual(report["status"], "OK")
                self.assertIsNone(report["renders_as"])

    def test_a_declared_slot_with_no_file_is_needs_asset_not_a_failure(self):
        src = _Source(self)
        (src.root / "assets" / "logo.png").unlink()
        self.assertEqual(src.build()["icons"]["logo"]["status"], "NEEDS_ASSET")

    def test_an_undeclared_slot_is_needs_asset(self):
        src = _Source(self)
        src.set_openai(logo=None)
        self.assertEqual(src.build()["icons"]["logo"]["status"], "NEEDS_ASSET")

    def test_a_composer_icon_that_is_not_square_is_refused(self):
        # Both sides are above the minimum on purpose: at 64x32 the size rule
        # fires first and the squareness rule never runs, which would make this
        # test pass for a reason that has nothing to do with what it asserts.
        src = _Source(self)
        (src.root / "assets" / "composerIcon.png").write_bytes(png(64, 96))
        self.assertIn("not square", _refused(self, src))

    def test_a_logo_may_be_a_wordmark(self):
        # The asymmetry is deliberate: a logo is often not square, and refusing
        # one would refuse the shape most wordmarks have.
        src = _Source(self)
        (src.root / "assets" / "logo.png").write_bytes(png(160, 48))
        report = src.build()["icons"]["logo"]
        self.assertEqual(report["status"], "OK")
        self.assertFalse(report["square"])

    def test_an_icon_below_the_minimum_is_refused(self):
        src = _Source(self)
        (src.root / "assets" / "composerIcon.png").write_bytes(png(32, 32))
        self.assertIn("below", _refused(self, src))

    def test_an_icon_above_the_size_cap_is_refused(self):
        # Bytes, not pixels. A 2048x2048 solid-colour PNG compresses to about
        # ten kilobytes, so a test written that way would pass while the cap it
        # names was never exercised.
        src = _Source(self)
        (src.root / "assets" / "logo.png").write_bytes(
            png(64, 64) + b"\x00" * (bo.ICON_MAX_BYTES + 1))
        self.assertIn("exceeds", _refused(self, src))

    def test_a_format_outside_the_allowed_set_is_refused(self):
        src = _Source(self)
        (src.root / "assets" / "logo.png").unlink()
        (src.root / "assets" / "logo.bmp").write_bytes(b"BM" + b"\x00" * 64)
        src.set_openai(logo="./assets/logo.bmp")
        self.assertIn("not one of", _refused(self, src))

    def test_each_reader_gets_real_dimensions_out(self):
        # A reader that returned None would be refused above; these are the
        # positive controls that the parsers actually parse.
        self.assertEqual(bo.inspect_icon.__module__, "fleet_build_overlay")
        self.assertEqual(bo._png_size(png(48, 48)), (48, 48))


class SecretsAndByProductsDoNotLeave(unittest.TestCase):

    def test_a_dotenv_is_refused(self):
        src = _Source(self)
        (src.root / ".env").write_text("TOKEN=canary\n", encoding="utf-8")
        self.assertIn(".env", _refused(self, src))

    def test_a_private_key_is_refused(self):
        src = _Source(self)
        (src.root / "assets" / "signing.key").write_text("canary\n", encoding="utf-8")
        self.assertIn("signing.key", _refused(self, src))

    def test_a_token_shaped_filename_is_refused(self):
        src = _Source(self)
        (src.root / "token.json").write_text("{}\n", encoding="utf-8")
        self.assertIn("token.json", _refused(self, src))

    def test_a_bytecode_cache_is_refused(self):
        src = _Source(self)
        (src.root / "__pycache__").mkdir()
        (src.root / "__pycache__" / "x.pyc").write_bytes(b"\x00")
        self.assertIn("__pycache__", _refused(self, src))

    def test_a_previous_dist_directory_is_refused(self):
        src = _Source(self)
        (src.root / "dist").mkdir()
        (src.root / "dist" / "old.zip").write_bytes(b"PK")
        self.assertIn("dist", _refused(self, src))

    def test_a_symlink_is_refused(self):
        src = _Source(self)
        (src.root / "assets" / "logo.png").unlink()
        (src.root / "assets" / "logo.png").symlink_to("/etc/hostname")
        self.assertIn("symlink", _refused(self, src))


class TheArchiveIsReproducibleAndComplete(unittest.TestCase):

    def test_two_builds_of_one_source_agree(self):
        src = _Source(self)
        first = src.build()
        second = src.build()
        self.assertEqual(first["archive_sha256"], second["archive_sha256"])
        self.assertEqual(first["content_sha256"], second["content_sha256"])

    def test_check_mode_returns_a_stable_verdict(self):
        src = _Source(self)
        self.assertTrue(bo.check(src.root, src.baseline)["stable"])

    def test_the_previous_archive_is_never_a_member_of_the_next_one(self):
        src = _Source(self)
        first = src.build()
        second = src.build()
        self.assertEqual(first["members"], second["members"])
        self.assertFalse([m for m in second["members"] if m.startswith("dist/")])

    def test_the_members_are_the_skills_and_assets_and_the_two_manifests(self):
        record = _Source(self).build()
        members = set(record["members"])
        self.assertIn("plugin.json", members)
        self.assertIn(".app.json", members)
        self.assertIn(".codex-plugin/plugin.json", members)
        self.assertIn("skills/fleet-overlay-smoke/SKILL.md", members)
        self.assertIn("assets/composerIcon.png", members)

    def test_the_written_archive_holds_exactly_those_members(self):
        src = _Source(self)
        record = src.build()
        with zipfile.ZipFile(src.out / record["archive"]) as zf:
            self.assertEqual(sorted(zf.namelist()), sorted(record["members"]))
            self.assertEqual(json.loads(zf.read("plugin.json"))["version"], "0.1.1")

    def test_a_tree_with_no_skill_is_refused(self):
        src = _Source(self)
        shutil.rmtree(src.root / "skills")
        self.assertIn("SKILL.md", _refused(self, src))

    def test_the_release_record_is_written_beside_the_archive(self):
        src = _Source(self)
        record = src.build()
        written = json.loads((src.out / f"{record['name']}-overlay-v{record['version']}.release.json")
                             .read_text(encoding="utf-8"))
        self.assertEqual(written["archive_sha256"], record["archive_sha256"])
        self.assertEqual(written["distribution"], bo.DISTRIBUTION)


if __name__ == "__main__":
    unittest.main()
