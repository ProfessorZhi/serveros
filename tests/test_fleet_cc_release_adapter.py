"""The Claude Code adapter for the V1 release package.

What is load-bearing here is that the adapter is the **same artifact** as the
portable package, not a second one:

- the name, version and description come from the package manifest, so a
  release bump does not need a second edit;
- the skills are byte-identical to the shared source, not re-typed;
- the two brand assets are the bytes the lock pins, copied rather than
  regenerated;
- the binding is the observed ingress, all six scopes, and the pinned loopback
  port;
- the installer-facing README and the privacy notice ship unchanged, and the
  manifest points at the notice a directory review can read.

`claude plugin validate` is the authority for the manifest shape; these tests
cover what it does not: that the content matches the release package, and that
a generation refuses to write over a previous one.

No network, no secret, no model call.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HERE = ROOT / "integrations" / "fleet-cc-dev-plugin"
RELEASE = ROOT / "integrations" / "fleet-v1-first-install"
SHARED_SKILLS = RELEASE / "plugin" / "skills"

_spec = importlib.util.spec_from_file_location(
    "fleet_cc_release_adapter", HERE / "build_release_adapter.py")
ra = importlib.util.module_from_spec(_spec)
sys.modules["fleet_cc_release_adapter"] = ra
_spec.loader.exec_module(ra)

URL = "https://fleet-next.example.ts.net/mcp"


def generate(out: Path, **kwargs) -> dict:
    return ra.generate(out=out, next_mcp_url=kwargs.pop("next_mcp_url", URL), **kwargs)


class GeneratedFromTheReleasePackage(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="fleet-cc-release-")
        self.addCleanup(self._tmp.cleanup)
        self.out = Path(self._tmp.name) / "plugin"
        self.record = generate(self.out)

    def test_the_manifest_carries_the_release_identity(self) -> None:
        release = json.loads((RELEASE / "plugin" / "plugin.json").read_text(encoding="utf-8"))
        manifest = json.loads(
            (self.out / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["name"], release["name"])
        self.assertEqual(manifest["version"], release["version"])
        self.assertEqual(
            manifest["displayName"],
            release["extensions"]["com.openai"]["interface"]["displayName"])

    def test_the_icon_is_the_composer_slot_and_exists(self) -> None:
        manifest = json.loads(
            (self.out / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["icon"], "./assets/composerIcon.png")
        self.assertTrue((self.out / manifest["icon"]).is_file())

    def test_the_binding_is_the_observed_ingress_with_every_scope(self) -> None:
        config = json.loads((self.out / ".mcp.json").read_text(encoding="utf-8"))
        server = config["mcpServers"]["serveros-fleet"]
        self.assertEqual(server["type"], "http")
        self.assertEqual(server["url"], URL)
        self.assertEqual(set(server["oauth"]["scopes"].split()),
                         set(ra._build.FLEET_SCOPES))
        self.assertEqual(server["oauth"]["callbackPort"], ra.CALLBACK_PORT)

    def test_the_assets_are_the_pinned_bytes(self) -> None:
        lock = json.loads((RELEASE / "brand-assets.lock.json").read_text(encoding="utf-8"))
        for slot in lock["slots"]:
            path = self.out / slot["shipped_path"]
            with self.subTest(slot=slot["slot"]):
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                                 slot["expected_sha256"])

    def test_the_privacy_notice_ships_and_the_manifest_names_it(self) -> None:
        manifest = json.loads(
            (self.out / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["privacyPolicyUrl"], ra.PRIVACY_POLICY_URL)
        self.assertEqual((self.out / "PRIVACY.md").read_bytes(),
                         (RELEASE / "plugin" / "PRIVACY.md").read_bytes())

    def test_the_readme_is_the_packages_own_and_is_long_enough_to_review(self) -> None:
        shipped = (self.out / "README.md").read_bytes()
        self.assertEqual(shipped, (RELEASE / "plugin" / "README.md").read_bytes())
        self.assertGreaterEqual(len(shipped.decode("utf-8").split()), 40)

    def test_the_skills_are_byte_identical_to_the_shared_source(self) -> None:
        names = sorted(p.name for p in SHARED_SKILLS.iterdir() if p.is_dir())
        self.assertTrue(names)
        for name in names:
            with self.subTest(skill=name):
                expected = (SHARED_SKILLS / name / "SKILL.md").read_bytes()
                self.assertEqual(
                    (self.out / "skills" / name / "SKILL.md").read_bytes(), expected)

    def test_nothing_is_written_besides_the_reported_members(self) -> None:
        on_disk = {p.relative_to(self.out).as_posix()
                   for p in self.out.rglob("*") if p.is_file()}
        self.assertEqual(on_disk, set(self.record["members"]))


class Refusals(unittest.TestCase):
    def test_a_url_that_is_not_the_mcp_path_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ra.Refused) as caught:
                generate(Path(tmp) / "plugin", next_mcp_url="https://example.ts.net/sse")
            self.assertEqual(caught.exception.status, ra.SOURCE_REFUSED)

    def test_a_scope_outside_the_six_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ra.Refused) as caught:
                generate(Path(tmp) / "plugin", scopes=("fleet:read", "fleet:admin"))
            self.assertIn("fleet:admin", caught.exception.detail)

    def test_an_existing_output_directory_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "plugin"
            out.mkdir()
            with self.assertRaises(ra.Refused) as caught:
                generate(out)
            self.assertIn("already exists", caught.exception.detail)


if __name__ == "__main__":
    unittest.main()
