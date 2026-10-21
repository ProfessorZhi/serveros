"""The Claude Code development adapter: what it generates, and what it never does.

Three properties matter, and two of them are absences.

The first is that **the skills have one source**. The adapter is a second
distribution of the same shared Skills, not a second copy of them: the generated
`SKILL.md` files are byte-identical to the ones the ChatGPT package ships, and a
test that only counted directories would pass on a generator that had
re-typed them.

The second absence is the **brand asset**. The two approved PNGs are a release
input; an unbranded development adapter must generate without them, and a
generator that quietly produced a placeholder would be inventing a brand asset
it has no authority over.

The third absence is the **archive**. The output is a directory Claude Code
loads in place. A ZIP is the artefact that gets installed, and this is not an
installable release -- so nothing here may write one.

The manifest and `.mcp.json` shapes follow the Claude Code documentation for the
version installed on this host. `claude plugin validate` is the authority; these
tests cover what validation on that version does *not* check (it does not read
`.mcp.json` at all before v2.1.281), which is exactly the gap a test is for.

No network, no secret, no model call.
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
SHARED_SKILLS = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"

_spec = importlib.util.spec_from_file_location("fleet_cc_dev_plugin", HERE / "build_cc_plugin.py")
cc = importlib.util.module_from_spec(_spec)
sys.modules["fleet_cc_dev_plugin"] = cc
_spec.loader.exec_module(cc)

URL = "https://fleet-next.example.ts.net/mcp"

#: Documented top-level keys of `.claude-plugin/plugin.json`. An unknown key is
#: *stripped with a warning* rather than rejected, so a field this module
#: invented would vanish silently -- which is why the list is pinned here.
DOCUMENTED_MANIFEST_KEYS = {
    "$schema", "name", "displayName", "version", "description", "author",
    "homepage", "repository", "license", "keywords", "metadata", "icon",
    "documentationUrl", "supportUrl", "privacyPolicyUrl", "termsOfServiceUrl",
    "defaultEnabled", "dependencies", "settings", "userConfig", "types",
    "channels", "skills", "commands", "agents", "hooks", "mcpServers",
    "lspServers", "outputStyles", "workflows", "experimental",
}


class Generated(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-dev-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "plugin"

    def generate(self, **overrides) -> dict:
        kwargs = dict(skills_source=SHARED_SKILLS, out=self.out, next_mcp_url=URL)
        kwargs.update(overrides)
        return cc.generate(**kwargs)

    def manifest(self) -> dict:
        return json.loads((self.out / ".claude-plugin" / "plugin.json").read_text())

    def mcp(self) -> dict:
        return json.loads((self.out / ".mcp.json").read_text())

    # -- the layout Claude Code documents -------------------------------------

    def test_the_layout_is_the_documented_one(self) -> None:
        self.generate()
        self.assertTrue((self.out / ".claude-plugin" / "plugin.json").is_file())
        self.assertTrue((self.out / ".mcp.json").is_file())
        self.assertTrue((self.out / "skills").is_dir())
        self.assertTrue((self.out / cc.DEV_MARKER).is_file())
        # Everything else is at the plugin root, not inside `.claude-plugin/`.
        self.assertEqual([p.name for p in (self.out / ".claude-plugin").iterdir()],
                         ["plugin.json"])

    def test_the_manifest_declares_only_documented_keys(self) -> None:
        self.generate()
        stray = sorted(set(self.manifest()) - DOCUMENTED_MANIFEST_KEYS)
        self.assertEqual(stray, [], "an unknown key is stripped silently at load")

    def test_the_manifest_name_is_kebab_case_and_not_reserved(self) -> None:
        self.generate()
        name = self.manifest()["name"]
        self.assertRegex(name, cc._builder.NAME_RE if hasattr(cc._builder, "NAME_RE")
                         else r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
        for reserved in ("claude", "anthropic", "anthropics", "claude-code"):
            self.assertNotEqual(name, reserved)
        self.assertFalse(name.startswith(("claude-", "anthropic-", "cc-plugin-")))

    # -- one source for the skills -------------------------------------------

    def test_every_skill_is_byte_identical_to_the_shared_source(self) -> None:
        record = self.generate()
        source_names = sorted(p.name for p in SHARED_SKILLS.iterdir())
        self.assertEqual(record["skills"], source_names)
        for name in source_names:
            generated = (self.out / "skills" / name / "SKILL.md").read_bytes()
            self.assertEqual(generated, (SHARED_SKILLS / name / "SKILL.md").read_bytes(),
                             f"{name} was re-typed rather than read from the source")
        self.assertIn("fleet-v1-first-install", record["skills_source"])

    def test_a_front_matter_name_that_disagrees_with_the_folder_is_refused(self) -> None:
        source = self.tmp / "bad-skills"
        (source / "renamed").mkdir(parents=True)
        (source / "renamed" / "SKILL.md").write_text(
            "---\nname: something-else\ndescription: x\n---\nbody\n", encoding="utf-8")
        with self.assertRaises(cc.Refused) as caught:
            self.generate(skills_source=source)
        self.assertIn("namespaces a skill under its directory", caught.exception.detail)
        self.assertFalse(self.out.exists())

    # -- the two absences -----------------------------------------------------

    def test_nothing_brand_shaped_and_nothing_installable_is_written(self) -> None:
        self.generate()
        images = [p for p in self.out.rglob("*") if p.suffix.lower() in
                  (".png", ".jpg", ".jpeg", ".webp", ".svg", ".ico")]
        archives = [p for p in self.out.rglob("*") if p.suffix.lower() in (".zip", ".mcpb")]
        self.assertEqual(images, [], "the adapter is deliberately unbranded")
        self.assertEqual(archives, [], "a ZIP is the artefact that gets installed")
        self.assertNotIn("icon", self.manifest())

    def test_the_marker_says_it_is_not_the_release(self) -> None:
        self.generate()
        body = (self.out / cc.DEV_MARKER).read_text()
        self.assertIn("DEVELOPMENT ONLY", body)
        self.assertIn("not the release", body)

    # -- the binding ----------------------------------------------------------

    def test_the_binding_is_typed_http_with_the_scopes_pinned(self) -> None:
        self.generate(scopes="fleet:read")
        server = self.mcp()["mcpServers"]["serveros-fleet"]
        # A `url` with no `type` is read as a stdio server -- a config error,
        # not a fallback.
        self.assertEqual(server["type"], "http")
        self.assertEqual(server["url"], URL)
        self.assertEqual(server["oauth"]["scopes"], "fleet:read")

    def test_a_scope_outside_the_six_is_refused(self) -> None:
        with self.assertRaises(cc.Refused) as caught:
            self.generate(scopes="fleet:read fleet:root")
        self.assertIn("not minted by this surface", caught.exception.detail)
        self.assertFalse(self.out.exists())

    def test_the_default_requested_scope_is_the_read_only_one(self) -> None:
        self.generate()
        self.assertEqual(self.mcp()["mcpServers"]["serveros-fleet"]["oauth"]["scopes"],
                         "fleet:read")

    def test_an_empty_or_non_https_endpoint_is_refused(self) -> None:
        for url in ("", None, "http://fleet.example.ts.net/mcp",
                    "https://fleet.example.ts.net/not-mcp",
                    "https://user:pw@fleet.example.ts.net/mcp"):
            with self.subTest(url=url):
                with self.assertRaises(cc.Refused):
                    self.generate(next_mcp_url=url)

    # -- one-shot, and not inside its own source -----------------------------

    def test_a_second_generation_refuses_to_blend_into_the_first(self) -> None:
        self.generate()
        before = (self.out / ".mcp.json").read_bytes()
        with self.assertRaises(cc.Refused) as caught:
            self.generate()
        self.assertIn("already exists", caught.exception.detail)
        self.assertEqual((self.out / ".mcp.json").read_bytes(), before)

    def test_an_out_inside_the_skills_source_is_refused(self) -> None:
        inside = SHARED_SKILLS / "generated"
        with self.assertRaises(cc.Refused) as caught:
            cc.generate(skills_source=SHARED_SKILLS, out=inside, next_mcp_url=URL)
        self.assertIn("or is inside it", caught.exception.detail)
        self.assertFalse(inside.exists())


class TheLocalOwnerStdioTransport(unittest.TestCase):
    """The local trusted owner runtime is a separate, OAuth-free binding."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-owner-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.entry = self.tmp / "serveros-fleet-owned"
        self.entry.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        self.entry.chmod(0o755)
        self.config = self.tmp / "owner-config.yaml"
        self.config.write_text("owner: synthetic\n", encoding="utf-8")
        self.out = self.tmp / "plugin"

    def test_the_stdio_binding_has_no_remote_url_or_oauth(self) -> None:
        record = cc.generate(skills_source=SHARED_SKILLS, out=self.out,
                             transport="stdio-owner",
                             owned_entry=str(self.entry),
                             owned_config=str(self.config))
        server = json.loads((self.out / ".mcp.json").read_text())["mcpServers"]["serveros-fleet"]
        self.assertEqual(server["type"], "stdio")
        self.assertEqual(server["command"], str(self.entry))
        self.assertEqual(server["args"], ["--stdio", "--config", str(self.config)])
        self.assertNotIn("url", server)
        self.assertNotIn("oauth", server)
        self.assertEqual(record["transport"], "stdio-owner")
        self.assertIsNone(record["oauth_scopes"])

    def test_relative_missing_or_non_executable_owned_entry_is_refused(self) -> None:
        cases = [
            dict(owned_entry="bin/serveros-fleet-owned", owned_config=str(self.config)),
            dict(owned_entry=str(self.tmp / "missing"), owned_config=str(self.config)),
            dict(owned_entry=str(self.config), owned_config=str(self.config)),
            dict(owned_entry=str(self.entry), owned_config=str(self.tmp / "missing")),
        ]
        for index, case in enumerate(cases):
            with self.subTest(case=index):
                out = self.tmp / f"refused-{index}"
                with self.assertRaises(cc.Refused):
                    cc.generate(skills_source=SHARED_SKILLS, out=out,
                                transport="stdio-owner", **case)
                self.assertFalse(out.exists())

    def test_http_mode_may_not_also_carry_a_local_owner_binding(self) -> None:
        with self.assertRaises(cc.Refused):
            cc.generate(skills_source=SHARED_SKILLS, out=self.out,
                        next_mcp_url="https://fleet-next.example.ts.net/mcp",
                        transport="http", owned_entry=str(self.entry),
                        owned_config=str(self.config))
        self.assertFalse(self.out.exists())


class TheV1SelfHealingBoundary(unittest.TestCase):
    """V1 has bounded self-healing; it is not a V2 capability.

    An earlier revision of `fleet-safety-boundaries` said self-healing was V2,
    which is wrong on the product: V1 closes a recovery loop over the resource
    monitoring plane for the **approved services** on a device, with the owner's
    authority and a list of bounds. "Production recovery needs approval" is a
    gate on acting, not a statement that the capability is absent — and a Skill
    that answers otherwise teaches the assistant to refuse work V1 supports.

    These are offline assertions over the source text: they pin the *writing*,
    not a model's behaviour. Behaviour was measured once, wrongly, and the
    corrected text has not been re-run against a model.
    """

    SAFETY = SHARED_SKILLS / "fleet-safety-boundaries" / "SKILL.md"
    EVIDENCE = SHARED_SKILLS / "fleet-evidence-review" / "SKILL.md"
    README = HERE / "README.md"

    def safety_text(self) -> str:
        return self.SAFETY.read_text(encoding="utf-8")

    def test_the_bounds_of_v1_self_healing_are_all_named(self) -> None:
        body = self.safety_text().lower()
        for bound in ("scope", "owner", "allowlist", "budget", "backoff",
                      "busy agent", "manually disabled", "needslogin",
                      "same-unit single writer"):
            with self.subTest(bound=bound):
                self.assertIn(bound, body, f"the {bound!r} bound is not written down")

    def test_the_named_services_are_the_approved_ones(self) -> None:
        body = self.safety_text().lower()
        for service in ("cloudcli", "tailscale", "connector"):
            with self.subTest(service=service):
                self.assertIn(service, body)

    def test_the_text_never_calls_self_healing_v2(self) -> None:
        """The exact regression: the capability moved into V2 in prose."""
        body = self.safety_text().lower()
        self.assertNotIn("automatic self-healing is v2", body)
        self.assertNotIn("self-healing is v2", body)
        self.assertIn("bounded self-healing is v1", body)

    def test_testing_the_loop_is_separated_from_production_recovery(self) -> None:
        body = self.safety_text().lower()
        self.assertIn("toy service", body)
        self.assertIn("production recovery", body)

    def test_the_monitoring_skill_points_at_the_real_boundary(self) -> None:
        body = self.EVIDENCE.read_text(encoding="utf-8").lower()
        self.assertIn("owner-authorised loop", body)
        self.assertNotIn("must *not* perform", body)

    def test_the_readme_records_self_heal_as_not_run_not_forbidden(self) -> None:
        body = self.README.read_text(encoding="utf-8")
        self.assertIn("`SELF_HEAL_VERIFIED` | **NOT_RUN**", body)
        self.assertNotIn("automatic self-healing is V2", body)

    def test_the_readme_withdraws_the_earlier_probes_self_heal_answer(self) -> None:
        body = self.README.read_text(encoding="utf-8")
        self.assertIn("FAILED_REQUIREMENT", body)
        self.assertIn("pending re-test", body)

    def test_the_readme_separates_load_evidence_from_mcp_enumeration(self) -> None:
        body = self.README.read_text(encoding="utf-8")
        self.assertIn("package-load evidence", body)
        self.assertIn("not an MCP\nenumeration", body)
        self.assertIn("read-only smoke profile", body)

    def test_the_readme_keeps_the_over_limit_model_call_on_the_record(self) -> None:
        body = self.README.read_text(encoding="utf-8")
        self.assertIn("Two** restricted model calls were made, not one", body)


if __name__ == "__main__":
    unittest.main()
