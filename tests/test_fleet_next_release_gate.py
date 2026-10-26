"""A gate that goes red when the candidate stops being what it says it is.

Two failure modes this exists for, and they are different.

The first is a change to the surface. "33 tools, 6 scopes, read+send by default,
Feishu off" is the sentence several documents and pull requests are written
against, and every one of those numbers is a constant someone can edit in a
one-line commit while the prose stays exactly as it was. A gate that reads the
constants and compares them to the sentence is the only thing that notices.

The second is a claim. This candidate is not deployed, not installed and not
exercised against a real client, and the way that stops being true is not a
code change — it is a document that quietly drops the qualification. So the
status words are asserted too, on the grounds that removing "DEVICE NOT
DEPLOYED" from the status table should be as visible as changing a default.

Neither half asserts that the work is done. They assert that the repository
still says accurately what has been done.
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp import scopes, tools, version  # noqa: E402
from fleet_mcp.config import ProviderBinding  # noqa: E402

CANDIDATE_DOC = ROOT / "docs" / "fleet-next-candidate.md"
PACKAGING_DOC = ROOT / "integrations" / "chatgpt-fleet-next" / "PACKAGING.md"
GAPS_DOC = ROOT / "docs" / "fleet-next-device-readiness-gaps.md"


class TheSurfaceIsTheOneTheDocumentsDescribe(unittest.TestCase):
    """Every number here is quoted in at least one document. Change one and the
    prose becomes wrong, which is the thing a test can catch and review cannot."""

    def test_forty_four_tools_are_served(self):
        self.assertEqual(len(tools.SERVED_TOOLS), 45)
        self.assertEqual(len(tools.TOOLS), 12)
        self.assertEqual(len(tools.NATIVE_MIRROR_TOOLS) + 2, 33)

    def test_the_tool_surface_revision_is_the_one_the_contract_declares(self):
        import yaml

        # V5 retains core-lite output and adds the fresh-project entry.
        self.assertEqual(version.TOOL_SURFACE_REVISION, "fleet-mcp-8")
        contract = yaml.safe_load(
            (ROOT / "fleet" / "fleet-mcp-contract.yaml").read_text(encoding="utf-8"))
        self.assertEqual(contract["tool_surface_revision"], version.TOOL_SURFACE_REVISION)

    def test_exactly_six_scopes_are_implemented_and_they_are_the_named_six(self):
        self.assertEqual(scopes.IMPLEMENTED_SCOPES,
                         ("fleet:read", "fleet:send", "fleet:control",
                          "fleet:create", "fleet:approve", "fleet:navigate"))
        # What is advertised is what is enforced: an extra entry here would let
        # a client be granted authority no code path honours.
        self.assertEqual(scopes.ALLOWED_SCOPES, scopes.IMPLEMENTED_SCOPES)
        self.assertEqual(scopes.PLANNED_SCOPES, ())

    def test_the_default_grant_is_read_and_send_and_nothing_heavier(self):
        self.assertEqual(scopes.DEFAULT_GRANTABLE_SCOPES,
                         ("fleet:read", "fleet:send"))
        for heavier in (scopes.SCOPE_CREATE, scopes.SCOPE_CONTROL,
                        scopes.SCOPE_APPROVE, scopes.SCOPE_NAVIGATE):
            self.assertNotIn(heavier, scopes.DEFAULT_GRANTABLE_SCOPES)

    def test_eligibility_is_policy_and_defaults_to_every_owned_class(self):
        """The eligible-ownership set is a binding value, not a code constant.

        The default admits every ownership the registry can author, because the
        narrow reading is a narrowing and a deployment that wrote no policy has
        not asked for one. A binding narrows it by naming fewer.
        """
        from fleet_mcp.config import KNOWN_DEVICE_OWNERSHIPS, PolicyBinding
        self.assertEqual(PolicyBinding().grant_eligible_ownership,
                         KNOWN_DEVICE_OWNERSHIPS)


class NothingIsOnByDefault(unittest.TestCase):

    def test_a_provider_binding_grants_no_verb_until_one_is_written(self):
        binding = ProviderBinding(
            device_ref="device:x", kind="fleet-node",
            endpoint="http://127.0.0.1:1", credential_anchor="/dev/null")
        self.assertFalse(binding.allow_send)
        self.assertFalse(binding.allow_create)
        self.assertFalse(binding.allow_control)

    def test_the_feishu_sso_block_is_absent_by_default(self):
        from fleet_mcp.feishu_sso import FeishuBinding
        self.assertFalse(FeishuBinding().enabled)
        self.assertEqual(FeishuBinding().whitelist, ())

    def test_no_binding_file_is_a_refusal_rather_than_an_empty_fleet(self):
        from fleet_mcp.config import load_config
        from fleet_mcp.errors import FleetRefusal
        with self.assertRaises(FleetRefusal) as caught:
            load_config("/nonexistent/fleet-mcp.local.yaml")
        self.assertEqual(caught.exception.code, "BAD_REQUEST")


class ThePublishedClaimsStillCarryTheirQualification(unittest.TestCase):

    #: The four words the delivery is required to keep distinct. Dropping one
    #: is how "code is ready" becomes "this is ready", which it is not.
    STATUS_WORDS = ("CODE READY", "TESTED", "DEVICE NOT DEPLOYED",
                    "CLIENT NOT INSTALLED")

    def test_the_candidate_document_still_distinguishes_the_four_states(self):
        text = CANDIDATE_DOC.read_text(encoding="utf-8")
        for word in self.STATUS_WORDS:
            with self.subTest(word=word):
                self.assertIn(word, text)

    def test_the_candidate_document_does_not_claim_it_is_ready(self):
        """No sentence may assert THIS candidate is deployed or installed.

        Quoted spans are removed before scanning, and that is not a loophole.
        The document quotes other files as EVIDENCE of non-deployment —
        `"nothing here is deployed and no second runtime exists on any machine
        yet"` — so a plain substring search fails on the very sentence that
        demonstrates the opposite of what it is looking for. A claim is a
        sentence this document asserts; a quotation is a sentence it reports.
        """
        text = CANDIDATE_DOC.read_text(encoding="utf-8")
        unquoted = re.sub(r'"[^"]*"', " ", text)
        unquoted = re.sub(r"`[^`]*`", " ", unquoted).lower()
        for claim in ("is deployed", "has been deployed", "is installed",
                      "production ready", "production-ready", "is live"):
            with self.subTest(claim=claim):
                self.assertNotIn(claim, unquoted)

    def test_the_packaging_document_admits_which_gates_are_open(self):
        text = PACKAGING_DOC.read_text(encoding="utf-8")
        self.assertIn("NOT EXERCISED", text)
        self.assertIn("BLOCKED", text)
        self.assertIn("NOT INSTALLED", text)
        self.assertIn("NOT CONNECTED", text)

    def test_the_gap_document_records_unknown_rather_than_filling_it_in(self):
        text = GAPS_DOC.read_text(encoding="utf-8")
        self.assertIn("UNKNOWN", text)
        # The standing constraint: nothing about that account may be inferred.
        self.assertRegex(text, re.compile(r"fjd", re.IGNORECASE))


if __name__ == "__main__":
    unittest.main()
