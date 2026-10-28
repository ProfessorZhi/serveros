#!/usr/bin/env python3
"""Wave 13 compose QA: #487 + #477 + #481 + #480.

The suite calls the public producers; it does not edit them. It also records
the one owner conflict that makes this candidate BLOCKED_OWNER rather than a
green RC: #481 changes the batch README whose exact bytes #487's Wave 12
ledger pins.
"""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-next-rc-wave13-qa"
sys.path.insert(0, str(HERE))

qa = sys.modules.get("wave13_qa")
if qa is None:
    spec = importlib.util.spec_from_file_location(
        "wave13_qa", HERE / "wave13_qa.py")
    qa = importlib.util.module_from_spec(spec)
    sys.modules["wave13_qa"] = qa
    spec.loader.exec_module(qa)


class TheCompositionIsPinned(unittest.TestCase):
    def test_exact_heads_and_parents(self) -> None:
        self.assertEqual(qa.SOURCE_HEADS, {
            "rc_487": "d8dc1f92b4252338e52271ca3f74b4d05ee39fbc",
            "model_477": "be2c1cccd6a506485c9465967b2531facffe459e",
            "cli_481": "9b7710a26180e1e6d00ce597827d447d0d2348b7",
            "parallel_480": "3777d134be02d896c9e36bc2ffd3a25baf9aaa04",
        })
        self.assertEqual(qa.PARENTS["parallel_480"],
                         qa.SOURCE_HEADS["model_477"])
        self.assertEqual(qa.MERGE_BASES["parallel_480"],
                         qa.PARENTS["model_477"])

    def test_the_merge_order_is_forced_by_ancestry(self) -> None:
        # #480's parent is #477; #481's merge-base is already in #487.
        self.assertEqual(qa.PARENTS["parallel_480"],
                         qa.SOURCE_HEADS["model_477"])
        self.assertEqual(qa.MERGE_BASES["cli_481"],
                         qa.PARENTS["cli_481"])


class ModelAndProjectGrantAreChecked(unittest.TestCase):
    def test_grant_denies_before_evidence_and_no_provider_is_called(self) -> None:
        facts = qa.model_grant_facts()
        self.assertEqual(facts["denied_verdict"], "BLOCKED")
        self.assertIn("MODEL_POLICY_DENIED", facts["denied_codes"])
        self.assertEqual(facts["allowed_verdict"], "PASS")
        self.assertEqual(facts["allowed_decision"], "EXPLICIT")
        self.assertEqual(facts["missing_verdict"], "BLOCKED")
        self.assertIn("MODEL_EVIDENCE_MISSING", facts["missing_codes"])
        self.assertEqual(facts["provider_calls"], 0)


class ParallelPluginRefusesLegacyReuseAndShadow(unittest.TestCase):
    def test_legacy_id_url_and_shadow_are_refused(self) -> None:
        facts = qa.parallel_gate_facts()
        self.assertEqual(facts["same_id"], "LEGACY_ID_UPDATE_FORBIDDEN")
        self.assertEqual(facts["same_url"], "NEXT_TAKEOVER_URL")
        self.assertEqual(facts["shadow"], "NEXT_SHADOW_WRITE_FORBIDDEN")
        self.assertEqual(facts["shadow_creator_calls"], 0)
        self.assertFalse(facts["shadow_network"])


class SourceArchivesAreDistinct(unittest.TestCase):
    def test_9skill_15member_and_7member_4skill_are_not_substituted(self) -> None:
        facts = qa.archive_facts()
        self.assertEqual(facts["nine"]["archive_sha256"],
                         "7320129eb33d57d9e988331e46c470738fc847efcc28a063f70a69bf171e12db")
        self.assertEqual(facts["nine"]["content_sha256"],
                         "6e8c6e984d3c6414d72de47c423a162a5c1775a3c98bbd84c0f4c869b8b6e93b")
        self.assertEqual((facts["nine"]["members"], facts["nine"]["skills"]),
                         (15, 9))
        self.assertEqual(facts["seven"]["archive_sha256"],
                         "14b3b4ee84a7be5428d29939e6a7297ec0aed34395f0267e74c9f18ef0bd65e2")
        self.assertEqual(facts["seven"]["content_sha256"],
                         "df15ee3779678ac80c54ab8891b6f6597f4c902f1fe3fc9e09566b99a1b3a7e1")
        self.assertEqual((facts["seven"]["members"], facts["seven"]["skills"]),
                         (7, 4))
        self.assertTrue(facts["distinct"])


class LegacyControlSurfaceIsOutsideTheDelta(unittest.TestCase):
    def test_no_fleet_mcp_runtime_service_or_plugin_path_moves(self) -> None:
        paths = qa.legacy_surface_touched()
        if paths is None:
            self.skipTest("shallow checkout has no #487 base to diff; local evidence covers it")
        self.assertEqual(paths, [])


class TheOwnerConflictIsNamedNotPatched(unittest.TestCase):
    def test_wave12_ledger_and_481_readme_digest_conflict(self) -> None:
        facts = qa.wave12_conflict()
        self.assertEqual(facts["code"], "BLOCKED_OWNER")
        self.assertEqual(facts["file"],
                         "integrations/fleet-v1-milestone-batch/README.md")
        self.assertEqual(facts["expected_sha256"],
                         "d3b5c1e59daca7da6daf545569f88bfc8a2ec6abe3ffda43be5c8e10a3e0e6cf")
        self.assertEqual(facts["actual_sha256"],
                         "3f479eec5a7643ab51e0a3e67a87cb841e537c1b37edaeb69d5961cb61104558")
        self.assertIn("#487", facts["owner"])
        self.assertIn("#481", facts["owner"])


if __name__ == "__main__":
    unittest.main()
