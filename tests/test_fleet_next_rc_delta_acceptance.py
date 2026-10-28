#!/usr/bin/env python3
"""Acceptance for #479 + #471 + #472 and the separate #477/#480 candidate."""
from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-next-rc-delta-acceptance"
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location(
    "fleet_rc_delta_acceptance", HERE / "rc_delta_acceptance.py")
acceptance = importlib.util.module_from_spec(spec)
sys.modules["fleet_rc_delta_acceptance"] = acceptance
spec.loader.exec_module(acceptance)

LEGACY = {
    "backend_plugin_id": "plugin_legacy_fleet",
    "release_id": "PluginRelease_LEGACY_7",
    "mcp_url": "https://legacy-fleet.example/mcp",
    "port": 3411,
    "datastore": "/var/lib/serveros/legacy.db",
    "tool_namespace": "serveros-fleet",
    "skills": sorted(acceptance.LEGACY_SKILLS),
}


class TheStackIsExact(unittest.TestCase):
    def test_main_rc_is_479_plus_the_two_fix_commits(self) -> None:
        self.assertEqual(
            [row["commit"] for row in acceptance.MAIN_RC_STACK],
            ["d037588723172816b8af12cefb2fffe6afe09f79",
             "7b4b9d66d6dad9af90e9632a26e2f33b541d73e6",
             "0c588896a7b4b6b122a593c83683342429020cd6"])

    def test_477_and_480_are_a_separate_candidate(self) -> None:
        main = [row["commit"] for row in acceptance.MAIN_RC_STACK]
        candidate = [row["commit"] for row in acceptance.CANDIDATE_STACK]
        self.assertEqual(candidate[:3], main)
        self.assertIn("be2c1cccd6a506485c9465967b2531facffe459e", candidate)
        self.assertIn("3777d134be02d896c9e36bc2ffd3a25baf9aaa04", candidate)
        self.assertNotIn("be2c1cccd6a506485c9465967b2531facffe459e", main)
        self.assertNotIn("3777d134be02d896c9e36bc2ffd3a25baf9aaa04", main)

    def test_no_v2_407_draft_is_merged(self) -> None:
        graph = acceptance.dependency_graph()
        self.assertFalse(graph["v2_407_draft_merged"])
        self.assertNotIn("407", " ".join(
            row["label"] for row in graph["main_rc"]))

    def test_conditional_sources_are_not_deployed(self) -> None:
        for row in acceptance.CONDITIONAL_SOURCES:
            self.assertEqual(row["status"], "NOT_RUN_NOT_DEPLOYED")
            self.assertNotIn("IM_SENT", row["status"])
            self.assertNotIn("DEPLOYED", row["status"].replace(
                "NOT_RUN_NOT_DEPLOYED", ""))


class TheTwoOriginalRedsAreGreen(unittest.TestCase):
    def test_original_ids_pass_on_the_combined_tree(self) -> None:
        out = acceptance.run_original_reds(str(ROOT))
        self.assertEqual(out["exit_code"], 0, out["output_tail"])
        self.assertTrue(out["passed"])
        self.assertEqual(len(out["tests"]), 2)


class TheLegacyGateIsStillRequired(unittest.TestCase):
    def test_legacy_identity_and_8_skills_pass_read_only_shadow(self) -> None:
        out = acceptance.legacy_gate(
            legacy_baseline=LEGACY, legacy_current=dict(LEGACY))
        self.assertEqual(out["verdict"], "PASS")
        self.assertTrue(out["legacy_untouched"])
        self.assertTrue(out["next_shadow_read_only"])

    def test_legacy_rename_or_resource_change_is_refused(self) -> None:
        for field in ("backend_plugin_id", "mcp_url", "port", "datastore",
                      "tool_namespace"):
            with self.subTest(field=field):
                changed = dict(LEGACY)
                changed[field] = "changed"
                out = acceptance.legacy_gate(
                    legacy_baseline=LEGACY, legacy_current=changed)
                self.assertEqual(out["code"], "LEGACY_CONTROLLER_CHANGED")

    def test_legacy_skill_removal_is_refused(self) -> None:
        changed = dict(LEGACY)
        changed["skills"] = sorted(list(acceptance.LEGACY_SKILLS)[:7])
        out = acceptance.legacy_gate(
            legacy_baseline=LEGACY, legacy_current=changed)
        self.assertEqual(out["code"], "LEGACY_CONTROLLER_MUTATED")

    def test_shadow_write_is_refused(self) -> None:
        out = acceptance.legacy_gate(
            legacy_baseline=LEGACY, legacy_current=dict(LEGACY),
            shadow_writes=["create"])
        self.assertEqual(out["code"], "NEXT_SHADOW_WRITE_FORBIDDEN")


class TheTwoFixesArePresentInSource(unittest.TestCase):
    def test_payload_allowlist_fix_is_present(self) -> None:
        builder = (ROOT / "integrations" / "chatgpt-fleet-next" / "build_zip.py"
                   ).read_text(encoding="utf-8")
        self.assertIn("PAYLOAD_MEMBERS", builder)
        test = (ROOT / "tests" / "test_fleet_next_plugin_package.py"
                ).read_text(encoding="utf-8")
        self.assertIn("set(bz.PAYLOAD_MEMBERS)", test)
        self.assertNotIn("assertEqual(len(members), 6)", test)

    def test_live_commander_session_fixture_is_present(self) -> None:
        test = (ROOT / "tests" / "test_fleet_v1_milestone_batch_registration.py"
                ).read_text(encoding="utf-8")
        for marker in ("CONTRACT_PATH", "live_sessions", "live_snapshot",
                       "SESSION_A", "SESSION_B", "SESSION_C"):
            with self.subTest(marker=marker):
                self.assertIn(marker, test)


if __name__ == "__main__":
    unittest.main()
