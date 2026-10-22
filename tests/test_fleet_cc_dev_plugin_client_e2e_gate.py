#!/usr/bin/env python3
"""What must be true before a local CC client E2E may be attempted, and what may
never be claimed without one.

The F line's next step is a real Claude Code client talking to the live Fleet
Next surface. That step needs an **owner-completed OAuth consent** and a token,
so this module does not perform it. What it does instead is make the
*preconditions* executable and make the *overclaims* impossible:

- the Skills the adapter ships are pinned by content hash, so "one canonical
  copy" is a checked fact rather than a convention;
- the binding invents no credential and is never a local command or an
  environment block -- the two ways a plugin smuggles something that is not a
  URL;
- the adapter cannot request more authority than it declares, and what it
  declares is inside the default grant;
- nothing the plugin *registers* (manifest, binding) claims a tool count or an
  installation, because a registered name is not an enumeration and a directory
  is not an install.

Everything here is offline and deterministic: the generator, the canonical
Skills, and `fleet_mcp.scopes` as the single source of the scope vocabulary. No
network, no secret, no model call, and the `claude` CLI is never invoked -- the
package-load receipt is a separate, human-run observation recorded in the PR.
"""
from __future__ import annotations

import hashlib
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

from fleet_mcp import scopes as fleet_scopes  # noqa: E402

HERE = ROOT / "integrations" / "fleet-cc-dev-plugin"
SHARED_SKILLS = ROOT / "integrations" / "fleet-v1-first-install" / "plugin" / "skills"
URL = "https://fleet-next.example.ts.net/mcp"

_spec = importlib.util.spec_from_file_location("fleet_cc_dev_gate", HERE / "build_cc_plugin.py")
cc = importlib.util.module_from_spec(_spec)
sys.modules["fleet_cc_dev_gate"] = cc
_spec.loader.exec_module(cc)

#: sha256 of the canonical Skill sources, as of the revision this file was
#: updated for the Operator Skills set. The adapter carries no second copy --
#: it reads these bytes -- so a change here is a change to everything that ships
#: them. Re-pinning is a
#: deliberate act: change the digest in the same commit as the text, and say in
#: the message which source the new text was judged against.
PINNED_SKILLS = {
    "fleet-async-dispatch": "ad0e4d3e3a3e6073b6ca57c324ce80d07fe3863ba791c2ec4f3f765394b17a4b",
    "fleet-commander-orchestration": "216aa82564e7c0becc39705b6772240bb661965fe9f9ad399e3ca6dcaf47c88c",
    "fleet-create-with-grants": "67a89e3910173d5887a5253309ae54ffd83f0aa2d18cf2e017698ff57ee70b9f",
    "fleet-device-and-session-discovery": "5c7131e2325d3742b7c6b14bf258e5dd468148f12490de5c825956e880c116d3",
    "fleet-evidence-review": "b4ab1d93513f7a10894e253fc74422f158508e9cb5bc61fbd71f85eada2ed67d",
    "fleet-plugin-update": "f10d852c0bc286eb8b7343a8a9a6839d54e14a384efd00e226b2d894767d8205",
    "fleet-reuse-idle-session": "7e0cf5e428bde2f4f0cd626a8881344742c564a1284a9961d03c354a1ffe6eba",
    "fleet-safety-boundaries": "64401b469bf71f9d43a16e7d8a1e5a76db2c0be0badd80da41f3d5f38172c877",
}


class TheSkillsArePinnedToTheirCanonicalBytes(unittest.TestCase):
    """One canonical copy, and it is pinned by what it *is*."""

    def test_each_canonical_skill_matches_its_pinned_hash(self) -> None:
        present = sorted(p.parent.name for p in SHARED_SKILLS.glob("*/SKILL.md"))
        self.assertEqual(present, sorted(PINNED_SKILLS),
                         "the skills set changed; re-pin deliberately")
        for name, digest in sorted(PINNED_SKILLS.items()):
            with self.subTest(skill=name):
                actual = hashlib.sha256(
                    (SHARED_SKILLS / name / "SKILL.md").read_bytes()).hexdigest()
                self.assertEqual(
                    actual, digest,
                    f"{name}/SKILL.md changed. Re-pin it in the same commit and "
                    f"name the source the new text was judged against.")

    def test_the_adapter_writes_exactly_the_pinned_bytes(self) -> None:
        """The generated tree is the canonical bytes, not a re-typing of them."""
        tmp = Path(tempfile.mkdtemp(prefix="cc-gate-"))
        self.addCleanup(shutil.rmtree, tmp, True)
        out = tmp / "plugin"
        cc.generate(skills_source=SHARED_SKILLS, out=out, next_mcp_url=URL)
        for name, digest in sorted(PINNED_SKILLS.items()):
            with self.subTest(skill=name):
                written = (out / "skills" / name / "SKILL.md").read_bytes()
                self.assertEqual(hashlib.sha256(written).hexdigest(), digest)


class TheBindingInventsNothing(unittest.TestCase):
    """A URL is a URL. A command, an env block or a client secret is not."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "plugin"
        self.record = cc.generate(skills_source=SHARED_SKILLS, out=self.out,
                                  next_mcp_url=URL)

    def binding(self) -> dict:
        return json.loads((self.out / ".mcp.json").read_text(encoding="utf-8"))

    def server(self) -> dict:
        return self.binding()["mcpServers"]["serveros-fleet"]

    def test_the_binding_carries_no_invented_credential(self) -> None:
        """Nothing here has been registered, so there is no id or secret to name."""
        text = (self.out / ".mcp.json").read_text(encoding="utf-8")
        for invented in ("clientId", "client_id", "clientSecret", "client_secret",
                         "registrationUrl", "bearer", "token", "password"):
            with self.subTest(field=invented):
                self.assertNotIn(invented, text)

    def test_the_binding_is_never_a_local_command_or_an_env_block(self) -> None:
        """`command`/`env` would make this a stdio server or a secret carrier."""
        for forbidden in ("command", "args", "env", "cwd"):
            with self.subTest(key=forbidden):
                self.assertNotIn(forbidden, self.server())

    def test_the_adapter_requests_only_a_read_only_subset(self) -> None:
        """It may not ask for more than it declares, nor outside the six."""
        requested = set(self.server()["oauth"]["scopes"].split())
        self.assertEqual(requested, {"fleet:read"})
        self.assertTrue(requested < set(fleet_scopes.ALLOWED_SCOPES),
                        "the smoke profile must be a strict subset, not the whole surface")
        self.assertTrue(requested <= set(fleet_scopes.DEFAULT_GRANTABLE_SCOPES),
                        "the request must sit inside the grant an undeclared deployment gives")

    def test_a_scope_the_surface_does_not_mint_is_refused(self) -> None:
        with self.assertRaises(cc.Refused):
            cc.generate(skills_source=SHARED_SKILLS, out=self.tmp / "second",
                        next_mcp_url=URL, scopes="fleet:read fleet:root")


class NothingRegisteredClaimsAnEnumerationOrAnInstall(unittest.TestCase):
    """A load registers a name. It does not enumerate tools or install anything."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="cc-gate-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.out = self.tmp / "plugin"
        self.record = cc.generate(skills_source=SHARED_SKILLS, out=self.out,
                                  next_mcp_url=URL)

    def test_the_record_is_not_installable_and_did_nothing_outward(self) -> None:
        self.assertFalse(self.record["installable"])
        self.assertFalse(self.record["creates_plugin"])
        self.assertEqual(self.record["uploads"], [])
        self.assertEqual(self.record["installs"], [])
        self.assertEqual(self.record["distribution"], "DEVELOPMENT_ONLY_NOT_A_RELEASE")

    def test_no_registered_artifact_claims_a_tool_count(self) -> None:
        """The manifest and the binding are what a client *registers*.

        A count there would be a claim the plugin cannot support: the schemas
        are resolved at runtime, from a server the plugin has never spoken to
        with a credential.
        """
        import re
        for structural in (".claude-plugin/plugin.json", ".mcp.json"):
            text = (self.out / structural).read_text(encoding="utf-8")
            self.assertIsNone(re.search(r"\b\d+\s+tools?\b", text),
                              f"{structural} claims a tool count")
            self.assertNotIn("inputSchema", text)

    def test_the_marker_forbids_the_reading_it_exists_to_prevent(self) -> None:
        body = (self.out / cc.DEV_MARKER).read_text(encoding="utf-8")
        self.assertIn("DEVELOPMENT ONLY", body)
        self.assertIn("not the release", body)
        self.assertIn("Do not install this", body)


if __name__ == "__main__":
    unittest.main()
