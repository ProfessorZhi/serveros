"""The plugin package against the Agent Plugins 1.0.0 specification.

The parts of the specification that matter here are the ones that decide whether
a live Fleet MCP *could* be attached to this package, and the answer has a shape
worth testing because it is easy to get backwards.

A portable binding is expressed by a **separate file at the package root**,
`mcp.json` — not by a manifest key. The manifest's property list is closed
(`additionalProperties: false`), so `mcpServers` inside `plugin.json` is not a
narrower way to do it; it is invalid. And an OAuth-carrying or app-backed
binding is not expressible in v1 at all: the specification states that v1
defines no OAuth configuration and no portable credential-reference fields.

So this package is instruction-only in a stronger sense than "we chose not to
wire it up". It is instruction-only, it conforms as it stands, and the one file
that would change that is refused by the builder — which is what the test below
pins: the deliberate absence and the enforced absence must not drift apart.

Read from https://agent-plugins.org/specification on 2026-10-14. Nothing here
contacts a client, and no claim is made that a client loads any of it.
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

PACKAGE = ROOT / "integrations" / "chatgpt-fleet-next"
CONTRACT_DOC = PACKAGE / "MCP-INTEGRATION.md"

PLUGIN_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/plugin.schema.json"
MCP_SCHEMA_URL = "https://agent-plugins.org/schemas/1.0.0/mcp.schema.json"

#: The closed top-level property set of the 1.0.0 manifest schema.
MANIFEST_KEYS = frozenset({
    "$schema", "name", "version", "description", "author", "homepage",
    "repository", "license", "keywords", "extensions",
})


def _load_builder():
    spec = importlib.util.spec_from_file_location("fleet_next_build_zip", PACKAGE / "build_zip.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bz = _load_builder()


def _manifest() -> dict:
    return json.loads((PACKAGE / "plugin.json").read_text(encoding="utf-8"))


class ThePackageConformsToTheFixedLayout(unittest.TestCase):

    def test_the_manifest_is_at_the_plugin_root_and_targets_1_0_0(self):
        self.assertTrue((PACKAGE / "plugin.json").is_file())
        self.assertEqual(_manifest()["$schema"], PLUGIN_SCHEMA_URL)

    def test_the_manifest_declares_no_key_outside_the_closed_set(self):
        self.assertEqual(set(_manifest()) - MANIFEST_KEYS, set())

    def test_skills_are_immediate_children_each_holding_a_SKILL_md(self):
        # "clients MUST NOT recursively search deeper descendants" — a skill one
        # level deeper is not a skill, so nesting it is a silent loss.
        skills = PACKAGE / "skills"
        self.assertTrue(skills.is_dir())
        children = [entry for entry in sorted(skills.iterdir())]
        self.assertTrue(children)
        for entry in children:
            with self.subTest(skill=entry.name):
                self.assertTrue(entry.is_dir(), f"{entry.name} is not a directory")
                self.assertTrue((entry / "SKILL.md").is_file())
        deeper = sorted(p.relative_to(skills).as_posix()
                        for p in skills.rglob("SKILL.md"))
        self.assertEqual(deeper, [f"{entry.name}/SKILL.md" for entry in children])

    def test_no_component_directory_is_shipped_that_the_spec_does_not_name(self):
        # The specified fixed locations are `skills/` and `mcp.json`. Anything
        # else at the root is either a source-side file (this package keeps its
        # notes and its builder here) or a client extension directory, and the
        # payload allowlist in build_zip.py already keeps the former out.
        for entry in sorted(PACKAGE.iterdir()):
            if entry.is_dir() and entry.name not in ("skills", "dist", "__pycache__"):
                with self.subTest(entry=entry.name):
                    self.assertTrue(
                        entry.name.startswith("com."),
                        f"{entry.name} is neither a fixed location nor a "
                        f"reverse-domain extension directory")


class ALiveMcpIsBoundByAFileAndNotByTheManifest(unittest.TestCase):

    def test_the_manifest_declares_no_mcp_servers_because_the_schema_forbids_it(self):
        manifest = _manifest()
        self.assertNotIn("mcpServers", manifest)
        self.assertNotIn("mcpServers", MANIFEST_KEYS)

    def test_the_package_ships_no_mcp_json_today(self):
        self.assertFalse((PACKAGE / "mcp.json").exists())
        self.assertFalse((PACKAGE / ".mcp.json").exists())

    def test_the_builder_refuses_the_file_that_would_bind_one(self):
        """The half that keeps the previous test from being merely an accident.

        A conforming `mcp.json` — the specification's own shape for a remote
        Streamable HTTP server — is refused. So the absence asserted above is
        enforced, and adding a binding has to be a reviewed change to the
        builder rather than something a rebuild could introduce.
        """
        tmp = Path(tempfile.mkdtemp(prefix="fleet-next-mcp-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        package = tmp / "pkg"
        shutil.copytree(PACKAGE, package,
                        ignore=shutil.ignore_patterns("dist", "__pycache__"))
        (package / "mcp.json").write_text(json.dumps({
            "$schema": MCP_SCHEMA_URL,
            "mcpServers": {
                "serveros-fleet": {
                    "type": "streamable-http",
                    "url": "https://example.invalid/mcp",
                }
            },
        }), encoding="utf-8")
        with self.assertRaises(bz.Refused) as caught:
            bz.collect(package)
        self.assertIn("mcp.json", str(caught.exception))

    def test_the_instruction_only_claim_agrees_with_the_tree(self):
        extension = _manifest()["extensions"]["com.serveros.fleet-next"]
        self.assertIs(extension["declaresMcpServer"], False)
        self.assertEqual(extension["kind"], "instruction-only")
        # If a binding were ever added, both statements would have to move
        # together — and the builder would stop the build until they did.
        for name in bz.LIVE_SURFACE_NAMES:
            self.assertFalse((PACKAGE / name).exists(), name)

    def test_the_schema_that_would_govern_a_binding_is_the_1_0_0_pair(self):
        # Same VERSION, not the same filename: the specification requires the
        # mcp.json `$schema` to match the version plugin.json declares, and the
        # two filenames are different on purpose (plugin.schema.json vs
        # mcp.schema.json).
        def version_of(url: str) -> str:
            return url.rsplit("/", 2)[-2]

        self.assertEqual(version_of(_manifest()["$schema"]),
                         version_of(MCP_SCHEMA_URL))
        self.assertEqual(version_of(MCP_SCHEMA_URL), "1.0.0")


class TheContractNamesBothPathsAndItsUnknowns(unittest.TestCase):

    def test_the_contract_document_exists_and_is_reviewable(self):
        self.assertTrue(CONTRACT_DOC.is_file())
        text = CONTRACT_DOC.read_text(encoding="utf-8")
        for named in ("mcp.json", "streamable-http", "UNKNOWN"):
            with self.subTest(named=named):
                self.assertIn(named, text)

    def test_the_contract_records_that_v1_has_no_oauth_or_credential_reference(self):
        # Whitespace is normalised first: the sentence is quoted across a line
        # break in the document, and a substring search that spans the wrap
        # would fail on the line that states the rule.
        text = " ".join(CONTRACT_DOC.read_text(encoding="utf-8").split())
        self.assertIn("no OAuth configuration or portable credential-reference", text)

    def test_the_integration_document_is_not_a_payload_member(self):
        # It is a note for reviewers. Shipping it to a client would put a
        # document about an absent feature inside the artifact that lacks it.
        members = [p.relative_to(PACKAGE).as_posix()
                   for p in bz.collect(PACKAGE)]
        self.assertNotIn("MCP-INTEGRATION.md", members)


if __name__ == "__main__":
    unittest.main()
