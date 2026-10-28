"""The Fleet Next ChatGPT plugin package: what it ships, and what it refuses.

Two properties are load-bearing and neither is visible by reading the builder
once.

The first is that the archive is a function of the SOURCE alone. The obvious
implementation — walk the folder and zip what you find — fails on the second
run: the first run wrote `dist/`, the second run finds it and packages the
previous archive inside the new one, and every build produces a different
release identity. The failure is quiet, and `--check` does not catch it, because
a check that builds into a scratch directory never sees the growth. So the
regression test below builds TWICE INTO THE PACKAGE'S OWN DIRECTORY, which is
the arrangement that used to break, and asserts the two digests agree.

The second is that this package is not the Native ServerOS plugin. Issue #186
draws that line explicitly, and the line is easy to cross by accident because
both are "the plugin" in conversation. The guard here is the live-surface rule:
a member that would declare an MCP server is refused, so a package that grew a
connector cannot ship under a manifest that says instruction-only.

The refusals are tested rather than described. A refusal that only exists in a
docstring is a refusal nobody has seen fire, and the whole point of this build is
to be the one place that says no.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PACKAGE = ROOT / "integrations" / "chatgpt-fleet-next"
BUILDER = PACKAGE / "build_zip.py"


def _load_builder():
    """The package directory has hyphens, so it is not an importable name."""
    spec = importlib.util.spec_from_file_location("fleet_next_build_zip", BUILDER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bz = _load_builder()


class _TempPackage:
    """A copy of the package in a scratch directory, for mutations."""

    def __init__(self, test: unittest.TestCase):
        self._tmp = Path(tempfile.mkdtemp(prefix="fleet-next-pkg-"))
        test.addCleanup(shutil.rmtree, self._tmp, True)
        self.root = self._tmp / "pkg"
        shutil.copytree(PACKAGE, self.root,
                        ignore=shutil.ignore_patterns("dist", "__pycache__"))

    def manifest(self) -> dict:
        return json.loads((self.root / "plugin.json").read_text(encoding="utf-8"))

    def set_manifest(self, **changes) -> None:
        (self.root / "plugin.json").write_text(
            json.dumps({**self.manifest(), **changes}), encoding="utf-8")


class TheManifestIsWithinTheSchema(unittest.TestCase):
    """A stray key is refused by a conforming client, so it is refused here first."""

    def test_the_builder_knows_exactly_the_published_1_0_0_properties(self):
        # Pinned against
        # https://agent-plugins.org/schemas/1.0.0/plugin.schema.json, whose root
        # has `additionalProperties: false`. If the schema grows a key, this test
        # is where the builder is told, rather than a build failing on a client.
        self.assertEqual(
            bz.SCHEMA_PROPERTIES,
            frozenset({"$schema", "name", "version", "description", "author",
                       "homepage", "repository", "license", "keywords",
                       "extensions"}),
        )

    def test_the_shipped_manifest_carries_no_key_the_schema_forbids(self):
        manifest = json.loads((PACKAGE / "plugin.json").read_text(encoding="utf-8"))
        self.assertEqual(set(manifest) - bz.SCHEMA_PROPERTIES, set())
        for required in bz.SCHEMA_PROPERTIES & {"$schema", "name"}:
            self.assertIn(required, manifest)

    def test_an_icon_key_is_refused_because_the_schema_has_no_such_property(self):
        # The icon is a shipped FILE. Issue #186 asks for an icon, and the 1.0.0
        # manifest schema defines no property to declare one — so the tempting
        # fix, an `icon` key, is the one thing a conforming client rejects.
        package = _TempPackage(self)
        with self.assertRaises(bz.Refused) as caught:
            package.set_manifest(icon="icon.svg")
            bz.load_manifest(package.root)
        self.assertIn("icon", str(caught.exception))

    def test_a_name_outside_the_schema_pattern_is_refused(self):
        package = _TempPackage(self)
        package.set_manifest(name="Fleet Next")
        with self.assertRaises(bz.Refused):
            bz.load_manifest(package.root)

    def test_a_version_that_is_not_semver_is_refused(self):
        package = _TempPackage(self)
        package.set_manifest(version="0.1")
        with self.assertRaises(bz.Refused):
            bz.load_manifest(package.root)


class TheArchiveIsAFunctionOfTheSourceAlone(unittest.TestCase):

    def test_two_builds_into_the_packages_own_directory_agree(self):
        """The arrangement that used to break: output lands inside the source.

        Building into `dist/` and then building again is the ordinary way an
        operator uses this tool. It must not be the way the digests diverge.
        """
        package = _TempPackage(self)
        first = bz.build(package.root, package.root / "dist")
        second = bz.build(package.root, package.root / "dist")
        self.assertEqual(first["archive_sha256"], second["archive_sha256"])
        self.assertEqual(first["content_sha256"], second["content_sha256"])

    def test_the_previous_archive_is_never_a_member_of_the_next_one(self):
        package = _TempPackage(self)
        first = bz.build(package.root, package.root / "dist")
        second = bz.build(package.root, package.root / "dist")
        self.assertEqual(first["members"], second["members"])
        for member in second["members"]:
            self.assertFalse(member.startswith("dist/"),
                             f"{member} is build output, not payload")

    def test_the_payload_is_exactly_the_declared_entries(self):
        package = _TempPackage(self)
        result = bz.build(package.root, package.root / "dist")
        self.assertIn("plugin.json", result["members"])
        self.assertIn("icon.svg", result["members"])
        for member in result["members"]:
            top = member.split("/", 1)[0]
            self.assertIn(top, bz.PAYLOAD_ENTRIES + ("skills",))

    def test_no_bytecode_cache_reaches_the_payload(self):
        # The repository already asserts this for the Native artifact
        # (`test_bytecode_caches_never_reach_the_artifact`); a second artifact
        # does not get a second standard.
        package = _TempPackage(self)
        (package.root / "__pycache__").mkdir()
        (package.root / "__pycache__" / "build_zip.cpython-312.pyc").write_bytes(b"\x00")
        package.set_manifest()
        result = bz.build(package.root, package.root / "dist")
        self.assertEqual([m for m in result["members"] if m.endswith(".pyc")], [])

    def test_check_reports_stability_and_exits_zero(self):
        package = _TempPackage(self)
        result = bz.check(package.root)
        self.assertTrue(result["stable"])
        self.assertEqual(
            bz.main([str(package.root), "--check", "--out", str(package.root / "dist")]), 0)


class ThisIsNotTheNativeServerOSPlugin(unittest.TestCase):
    """Issue #186: the GPT release must not be confused with `plugin/bundle.yaml`."""

    def test_the_two_compositions_do_not_share_a_name(self):
        import yaml  # noqa: PLC0415 - the repo already depends on PyYAML

        bundle = yaml.safe_load((ROOT / "plugin" / "bundle.yaml").read_text(encoding="utf-8"))
        manifest = json.loads((PACKAGE / "plugin.json").read_text(encoding="utf-8"))
        self.assertNotEqual(manifest["name"], bundle["name"])
        self.assertEqual(bundle["name"], "hwz-server-os")

    def test_the_package_declares_no_live_mcp_surface(self):
        manifest = json.loads((PACKAGE / "plugin.json").read_text(encoding="utf-8"))
        for key in ("mcpServers", "mcp_servers", "app"):
            self.assertNotIn(key, manifest)
        self.assertFalse(
            manifest["extensions"]["com.serveros.fleet-next"]["declaresMcpServer"])

    def test_a_live_surface_in_the_tree_is_refused_wherever_it_sits(self):
        for at in ("mcp.json", ".app.json", ".mcp.json", "skills/.app.json"):
            with self.subTest(member=at):
                package = _TempPackage(self)
                target = package.root / at
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text("{}", encoding="utf-8")
                # The refusal must fire even though the allowlist would never
                # have selected this file. A refusal that only covers what ships
                # cannot tell "refused" from "quietly dropped".
                with self.assertRaises(bz.Refused) as caught:
                    bz.collect(package.root)
                self.assertIn("live MCP/App surface", str(caught.exception))

    def test_a_manifest_that_declares_a_server_is_refused(self):
        package = _TempPackage(self)
        package.set_manifest(mcpServers="./.mcp.json")
        with self.assertRaises(bz.Refused) as caught:
            bz.collect(package.root)
        self.assertIn("mcpServers", str(caught.exception))

    def test_the_native_generated_layout_is_refused_by_directory(self):
        """The filename rule cannot see these, so the directory has to be one.

        A member of `.claude-plugin/` is named `plugin.json` — not in
        `LIVE_SURFACE_NAMES`, and not in the payload allowlist — so before this
        rule a tree that grew a second copy of the Native artifact would have
        built clean and shipped as though nothing had happened. Tested at the
        root and nested, because the audit runs over the whole tree.
        """
        for layout in bz.GENERATED_LAYOUT_DIRS:
            for at in (layout, f"skills/fleet-ability-routing/{layout}"):
                with self.subTest(layout=at):
                    package = _TempPackage(self)
                    (package.root / at).mkdir(parents=True, exist_ok=True)
                    (package.root / at / "plugin.json").write_text("{}", encoding="utf-8")
                    with self.assertRaises(bz.Refused) as caught:
                        bz.collect(package.root)
                    self.assertIn("generated plugin layout", str(caught.exception))

    def test_the_layout_rule_names_what_the_native_build_actually_writes(self):
        # Pinned to the two directories `scripts/build.py` emits and
        # `scripts/release.py` names in `REQUIRED_MEMBERS[PLUGIN]`, so widening
        # this tuple is a deliberate diff rather than a drift away from the
        # artifact the rule exists to keep out of this package.
        self.assertEqual(set(bz.GENERATED_LAYOUT_DIRS),
                         {".claude-plugin", ".codex-plugin"})

    def test_the_clean_package_is_not_caught_by_the_layout_rule(self):
        # Positive control. A gate that refuses everything would pass the three
        # tests above and tell you nothing.
        package = _TempPackage(self)
        members = [p.relative_to(package.root).as_posix()
                   for p in bz.collect(package.root)]
        self.assertIn("plugin.json", members)
        self.assertEqual(set(members), set(bz.PAYLOAD_MEMBERS))
        self.assertEqual(len(members), len(bz.PAYLOAD_MEMBERS))

    def test_an_unlisted_skill_member_is_refused_not_silently_packed(self):
        package = _TempPackage(self)
        (package.root / "skills" / "fleet-ability-routing" / "EXTRA.md").write_text(
            "not part of the release", encoding="utf-8")
        with self.assertRaises(bz.Refused) as caught:
            bz.collect(package.root)
        self.assertIn("unexpected payload member", str(caught.exception))
        self.assertIn("EXTRA.md", str(caught.exception))

    def test_a_rogue_skill_directory_is_refused(self):
        package = _TempPackage(self)
        (package.root / "skills" / "rogue").mkdir()
        (package.root / "skills" / "rogue" / "SKILL.md").write_text(
            "---\nname: rogue\ndescription: no\n---\n", encoding="utf-8")
        with self.assertRaises(bz.Refused) as caught:
            bz.collect(package.root)
        self.assertIn("skills/rogue/SKILL.md", str(caught.exception))

    def test_a_missing_declared_skill_is_refused(self):
        package = _TempPackage(self)
        shutil.rmtree(package.root / "skills" / "fleet-batch-commander")
        with self.assertRaises(bz.Refused) as caught:
            bz.collect(package.root)
        self.assertIn("missing payload member", str(caught.exception))

    def test_an_extra_top_level_file_is_not_packed(self):
        package = _TempPackage(self)
        (package.root / "EXTRA.md").write_text("not payload", encoding="utf-8")
        result = bz.build(package.root, package.root / "dist")
        self.assertNotIn("EXTRA.md", result["members"])
        self.assertEqual(set(result["members"]), set(bz.PAYLOAD_MEMBERS))


class TheSkillSourceIsComplete(unittest.TestCase):

    def test_the_declared_first_release_skills_are_present(self):
        # Issue #186 names these three for the minimum first version.
        for name in ("fleet-ability-routing", "fleet-agent-collaboration",
                     "fleet-multiagent-review"):
            with self.subTest(skill=name):
                self.assertTrue((PACKAGE / "skills" / name / "SKILL.md").is_file())

    def test_every_skill_has_the_two_frontmatter_keys_the_repo_uses(self):
        for skill in sorted((PACKAGE / "skills").glob("*/SKILL.md")):
            with self.subTest(skill=skill.parent.name):
                text = skill.read_text(encoding="utf-8")
                self.assertTrue(text.startswith("---\n"), "frontmatter must open the file")
                frontmatter = text.split("---\n", 2)[1]
                keys = {line.split(":", 1)[0] for line in frontmatter.splitlines() if line}
                self.assertEqual(keys, {"name", "description"})

    def test_a_tree_with_no_skill_is_refused(self):
        package = _TempPackage(self)
        shutil.rmtree(package.root / "skills")
        with self.assertRaises(bz.Refused) as caught:
            bz.collect(package.root)
        self.assertIn("SKILL.md", str(caught.exception))

    def test_a_symlinked_member_is_refused(self):
        package = _TempPackage(self)
        (package.root / "icon.svg").unlink()
        (package.root / "icon.svg").symlink_to("/etc/hostname")
        with self.assertRaises(bz.Refused) as caught:
            bz.collect(package.root)
        self.assertIn("symlink", str(caught.exception))


class TheShippedIconIsUsable(unittest.TestCase):

    def test_the_icon_is_a_member_and_parses_as_svg(self):
        import xml.etree.ElementTree as ET  # noqa: PLC0415

        package = _TempPackage(self)
        result = bz.build(package.root, package.root / "dist")
        self.assertIn("icon.svg", result["members"])

        archive = Path(result["archive"])
        with zipfile.ZipFile(archive) as zf:
            root = ET.fromstring(zf.read("icon.svg"))
        self.assertTrue(root.tag.endswith("svg"))
        # Sized absolutely: a client that renders it outside a layout needs its
        # own width and height, not a viewBox alone.
        self.assertEqual(root.get("width"), "128")
        self.assertEqual(root.get("height"), "128")


if __name__ == "__main__":
    unittest.main()
