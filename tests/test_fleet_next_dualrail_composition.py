#!/usr/bin/env python3
"""Cross-PR QA: #480 parallel-plugin preflight vs #483 Legacy safety.

The assertions call both producer modules through their public functions. They
do not grep the producer sources and they do not modify either one.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-next-dualrail-composition-qa"
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location(
    "dualrail_qa", HERE / "dualrail_qa.py")
qa = importlib.util.module_from_spec(spec)
sys.modules["dualrail_qa"] = qa
spec.loader.exec_module(qa)

LEGACY_SKILLS = frozenset(qa.LEGACY_SKILLS)
NEXT_SKILLS = frozenset(qa.NEXT_SKILLS)


def legacy() -> dict:
    return qa.legacy_record()


def next_plugin() -> dict:
    return qa.next_record()


def changed(record: dict, **fields) -> dict:
    out = copy.deepcopy(record)
    out.update(fields)
    return out


class OnePlacementBothPreflightsAgree(unittest.TestCase):
    def test_a_separate_placement_is_green_in_both(self) -> None:
        out = qa.evaluate(legacy(), next_plugin())
        self.assertTrue(out["both_green"])
        self.assertEqual(out["shared"], "GREEN")
        self.assertEqual(out["classification"], "GREEN")
        self.assertEqual(out["plan_483"]["decision"], "OK")
        self.assertEqual(out["preflight_480"]["verdict"], "PASS")
        self.assertEqual(out["creator_calls"], 0)
        self.assertFalse(out["network"])

    def test_shared_plugin_id_blocks_in_both(self) -> None:
        old = legacy()
        new = changed(next_plugin(), backend_plugin_id=old["backend_plugin_id"])
        out = qa.evaluate(old, new)
        self.assertTrue(out["both_blocked"])
        self.assertEqual(out["classification"], "BLOCKED")
        self.assertIn("plugin_id", out["plan_483"]["collisions"])
        self.assertEqual(out["preflight_480"]["code"],
                         "LEGACY_ID_UPDATE_FORBIDDEN")

    def test_shared_mcp_url_blocks_in_both(self) -> None:
        old = legacy()
        new = changed(next_plugin(), mcp_url=old["mcp_url"])
        out = qa.evaluate(old, new)
        self.assertTrue(out["both_blocked"])
        self.assertEqual(out["classification"], "BLOCKED")
        self.assertIn("mcp_endpoint", out["plan_483"]["collisions"])
        self.assertEqual(out["preflight_480"]["code"], "NEXT_TAKEOVER_URL")

    def test_shared_port_blocks_in_both(self) -> None:
        old = legacy()
        new = changed(next_plugin(), port=old["port"])
        out = qa.evaluate(old, new)
        self.assertTrue(out["both_blocked"])
        self.assertEqual(out["classification"], "BLOCKED")
        self.assertIn("port", out["plan_483"]["collisions"])
        self.assertEqual(out["preflight_480"]["code"], "NEXT_SHARED_PORT")

    def test_shared_state_root_blocks_in_both(self) -> None:
        old = legacy()
        new = changed(next_plugin(), datastore=old["datastore"])
        out = qa.evaluate(old, new)
        self.assertTrue(out["both_blocked"])
        self.assertEqual(out["classification"], "BLOCKED")
        self.assertIn("state_root", out["plan_483"]["collisions"])
        self.assertEqual(out["preflight_480"]["code"], "NEXT_SHARED_DATASTORE")

    def test_shared_resources_are_exactly_the_common_ones(self) -> None:
        self.assertEqual(
            set(qa.SHARED_RESOURCES),
            {"backend_plugin_id", "mcp_url", "port", "datastore/state_root"})


class CoverageDifferencesAreNamedNotHidden(unittest.TestCase):
    """A resource only one producer models is reported, not pretended shared."""

    def test_483_only_layout_resources_block_only_483(self) -> None:
        old = legacy()
        for field in ("socket_path", "install_root", "lock_root", "unit"):
            with self.subTest(field=field):
                new = changed(next_plugin(), **{field: old[field]})
                out = qa.evaluate(old, new)
                self.assertTrue(out["blocked_483"])
                self.assertFalse(out["blocked_480"])
                self.assertEqual(out["classification"], "ONLY_483")
                self.assertEqual(out["shared"], "COVERAGE_DIFFERENCE")
                self.assertEqual(out["plan_483"]["code"],
                                 "BLOCKED_LEGACY_COLLISION")
                self.assertEqual(out["preflight_480"]["verdict"], "PASS")
                self.assertEqual(out["combined_gate"], "BLOCKED")

    def test_480_only_identity_resources_block_only_480(self) -> None:
        old = legacy()
        expectations = {
            "oauth_audience": "NEXT_SHARED_AUDIENCE",
            "credential_anchor": "NEXT_SHARED_CREDENTIAL",
            "tool_namespace": "NEXT_TAKEOVER_NAMESPACE",
        }
        for field, code in expectations.items():
            with self.subTest(field=field):
                new = changed(next_plugin(), **{field: old[field]})
                out = qa.evaluate(old, new)
                self.assertTrue(out["blocked_480"])
                self.assertFalse(out["blocked_483"])
                self.assertEqual(out["classification"], "ONLY_480")
                self.assertEqual(out["shared"], "COVERAGE_DIFFERENCE")
                self.assertEqual(out["preflight_480"]["code"], code)
                self.assertEqual(out["plan_483"]["decision"], "OK")
                self.assertEqual(out["combined_gate"], "BLOCKED")

    def test_the_matrix_names_every_gap(self) -> None:
        matrix = qa.coverage_matrix()
        self.assertEqual(set(matrix["only_480"]), {
            "oauth_audience", "credential_anchor", "tool_namespace", "skills",
            "source_pin", "archive_digest",
        })
        self.assertEqual(set(matrix["only_483"]), {
            "socket_path", "install_root", "lock_root", "unit",
        })
        self.assertIn("coverage difference", matrix["note"])


class MissingNextIdentityIsNeverInvented(unittest.TestCase):
    def test_blank_backend_plugin_id_blocks_with_the_named_code(self) -> None:
        out = qa.evaluate(legacy(), changed(next_plugin(), backend_plugin_id=""))
        self.assertTrue(out["both_blocked"])
        self.assertEqual(out["preflight_480"]["code"],
                         "BLOCKED_NEXT_PLUGIN_NOT_REGISTERED")
        self.assertEqual(out["plan_483"]["code"],
                         "BLOCKED_PLACEMENT_INCOMPLETE")
        self.assertIn("backend_plugin_id", out["plan_483"]["incomplete"])
        # Both refuse; the codes differ. That difference is a known owner gap,
        # not a substitute ID and not a green result.
        self.assertFalse(out["codes_agree"])
        self.assertNotIn("plugin_next_fleet",
                         str(out["preflight_480"]))
        self.assertNotIn("plugin_legacy_fleet",
                         str(out["preflight_480"]))


class SkillsAndZipsStaySeparate(unittest.TestCase):
    def test_legacy_has_8_and_next_has_9(self) -> None:
        old = legacy()
        new = next_plugin()
        self.assertEqual(len(old["skills"]), 8)
        self.assertEqual(set(old["skills"]), LEGACY_SKILLS)
        self.assertEqual(len(new["skills"]), 9)
        self.assertEqual(set(new["skills"]), NEXT_SKILLS)
        self.assertEqual(NEXT_SKILLS - LEGACY_SKILLS, {"fleet-batch-commander"})
        self.assertNotEqual(old["archive_sha256"], new["archive_sha256"])
        self.assertEqual(new["source_pin"], "fleet_v1_9skill")
        pins = qa.parallel.TRUSTED_SOURCE_PINS
        self.assertEqual(
            pins["fleet_v1_9skill"]["archive_sha256"],
            "7320129eb33d57d9e988331e46c470738fc847efcc28a063f70a69bf171e12db")
        self.assertEqual(
            pins["chatgpt_fleet_next_7member"]["archive_sha256"],
            "14b3b4ee84a7be5428d29939e6a7297ec0aed34395f0267e74c9f18ef0bd65e2")
        self.assertNotEqual(pins["fleet_v1_9skill"]["archive_sha256"],
                            pins["chatgpt_fleet_next_7member"]["archive_sha256"])

    def test_the_public_preflight_reports_both_skill_sets(self) -> None:
        pre = qa.preflight_480(legacy(), next_plugin())
        self.assertEqual(pre["verdict"], "PASS")
        self.assertEqual(set(pre["legacy_skills"]), LEGACY_SKILLS)
        self.assertEqual(set(pre["next_skills"]), NEXT_SKILLS)
        self.assertEqual(pre["legacy_skills"], sorted(LEGACY_SKILLS))
        self.assertEqual(pre["next_skills"], sorted(NEXT_SKILLS))

    def test_a_7_member_package_cannot_be_passed_as_next(self) -> None:
        old = legacy()
        pin = qa.parallel.TRUSTED_SOURCE_PINS["chatgpt_fleet_next_7member"]
        new = changed(
            next_plugin(),
            source_pin="chatgpt_fleet_next_7member",
            archive_sha256=pin["archive_sha256"],
            content_sha256=pin["content_sha256"],
            members=pin["members"],
        )
        out = qa.evaluate(old, new)
        self.assertEqual(out["preflight_480"]["code"], "MIXED_SOURCE_PIN")
        self.assertFalse(out["blocked_483"])
        self.assertEqual(out["classification"], "ONLY_480")
        self.assertEqual(out["combined_gate"], "BLOCKED")

    def test_next_8_skills_without_batch_commander_is_refused(self) -> None:
        out = qa.evaluate(legacy(), changed(next_plugin(),
                                           skills=sorted(LEGACY_SKILLS)))
        self.assertEqual(out["preflight_480"]["code"],
                         "NEXT_CONTROLLER_INCOMPLETE")
        self.assertEqual(out["classification"], "ONLY_480")


class NextUpdateStaysInsideNext(unittest.TestCase):
    def test_same_id_update_needs_consent_release_and_advancing_version(self) -> None:
        old = next_plugin()
        previous = dict(old)
        newer = changed(old, version="1.1.0")
        pre = qa.preflight_480(
            legacy(), newer, previous_next=previous,
            explicit_next_update_consent=True,
            expected_release_id=previous["release_id"])
        self.assertEqual(pre["verdict"], "PASS")
        self.assertTrue(pre["same_id_next_update"])

        blocked = qa.preflight_480(legacy(), newer, previous_next=previous)
        self.assertEqual(blocked["code"], "NEXT_UPDATE_CONSENT_REQUIRED")
        stale = qa.preflight_480(
            legacy(), newer, previous_next=previous,
            explicit_next_update_consent=True, expected_release_id="stale")
        self.assertEqual(stale["code"], "STALE_EXPECTED_RELEASE_ID")
        not_new = qa.preflight_480(
            legacy(), previous, previous_next=previous,
            explicit_next_update_consent=True,
            expected_release_id=previous["release_id"])
        self.assertEqual(not_new["code"], "NEXT_VERSION_NOT_ADVANCING")

    def test_legacy_cannot_be_upgraded_by_the_next_operation(self) -> None:
        plan = qa.plan_483(legacy(), next_plugin())
        out = qa.legacy_safety.upgrade(
            plan.legacy, version="9.9.9", artifact_digest="sha256:" + "9" * 64)
        self.assertEqual(out.decision, "BLOCKED")
        self.assertEqual(out.code, "ROLLBACK_CROSSES_TRACKS")
        self.assertEqual(out.placement, plan.legacy)


class NextLifecycleLeavesLegacyUntouched(unittest.TestCase):
    def test_install_upgrade_failure_rollback_keep_fake_legacy_answers(self) -> None:
        out = qa.legacy_survival()
        self.assertTrue(out["all_same"], out)
        self.assertEqual(out["drifted"], {})
        self.assertEqual(out["legacy_before"], out["legacy_after"])
        expected_send = {
            "accepted": True, "deliveryId": "legacy-delivery-1",
            "source": "LEGACY_FAKE",
        }
        self.assertEqual(out["fake_send_before"], expected_send)
        self.assertEqual(out["fake_send_after"], expected_send)
        before = {entry[0]: entry for entry in out["legacy_before"]}
        after = {entry[0]: entry for entry in out["legacy_after"]}
        for verb in ("get_run", "get_session", "sessions_list"):
            with self.subTest(verb=verb):
                self.assertEqual(before[verb], after[verb])
        self.assertEqual(out["creator_calls"], 0)
        self.assertFalse(out["network"])

    def test_upgrade_and_rollback_are_per_track(self) -> None:
        next_p = qa.plan_483(legacy(), next_plugin()).next
        up = qa.legacy_safety.upgrade(
            next_p, version="1.0.1",
            artifact_digest="sha256:" + "3" * 64)
        self.assertEqual(up.decision, "OK")
        self.assertEqual(up.version, "1.0.1")
        self.assertEqual(up.rollback_version, "1.0.0")
        rb = qa.legacy_safety.rollback(up)
        self.assertEqual(rb.decision, "OK")
        self.assertEqual(rb.placement, next_p)
        repeat = qa.legacy_safety.upgrade(
            up, version="1.0.1",
            artifact_digest="sha256:" + "4" * 64)
        self.assertEqual(repeat.code, "VERSION_NOT_ADVANCING")
        self.assertEqual(repeat.placement, up.placement)


class IdsAreScopedToTrackInstanceAndBoot(unittest.TestCase):
    def test_same_run_id_in_another_scope_is_not_a_hit(self) -> None:
        out = qa.cross_scope_matrix()
        self.assertEqual(out["hit"], "HIT")
        self.assertEqual(out["cross_track"], "CROSS_TRACK")
        self.assertEqual(out["foreign_instance"], "FOREIGN_INSTANCE")
        self.assertEqual(out["stale_boot"], "STALE_BOOT")
        self.assertEqual(out["legacy_state"], "completed")
        self.assertEqual(out["next_state"], "completed")
        self.assertFalse(out["same_object_across_tracks"])


class ShadowAndPermissionRefuseBeforeProvider(unittest.TestCase):
    def test_both_producers_refuse_shadow_write(self) -> None:
        out = qa.shadow_consensus()
        self.assertEqual(out["preflight_480_code"],
                         "NEXT_SHADOW_WRITE_FORBIDDEN")
        self.assertEqual(out["plan_483_shadow_code"],
                         "NEXT_SHADOW_WRITE_FORBIDDEN")
        self.assertFalse(out["shadow_write_gateway_called"])
        self.assertEqual(out["denied_code"], "SHADOW_NOT_GRANTED")
        self.assertFalse(out["denied_gateway_called"])
        self.assertEqual(out["read_state"], "OBSERVED")
        self.assertTrue(out["read_gateway_called"])
        self.assertEqual(out["gateway_calls_from_refusals"], 0)
        self.assertEqual(out["gateway_calls"], ["get_run"])
        self.assertNotIn("send", out["gateway_calls"])
        self.assertFalse(out["network"])


if __name__ == "__main__":
    unittest.main()
