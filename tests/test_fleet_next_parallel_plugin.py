#!/usr/bin/env python3
"""Offline dual-track preflight tests: Legacy Fleet vs parallel Fleet Next.

No Creator call, network, device, service, OAuth, credential or model. The
Creator stand-in is local and never invents a registration.
"""
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HERE = ROOT / "integrations" / "fleet-next-parallel-plugin"
sys.path.insert(0, str(HERE))

spec = importlib.util.spec_from_file_location("fleet_parallel_plugin", HERE / "parallel_plugin.py")
parallel = importlib.util.module_from_spec(spec)
sys.modules["fleet_parallel_plugin"] = parallel
spec.loader.exec_module(parallel)

LEGACY = {
    "backend_plugin_id": "plugin_legacy_fleet",
    "release_id": "PluginRelease_LEGACY_7",
    "name": "serveros-fleet-ingress",
    "version": "0.1.0",
    "mcp_url": "https://legacy-fleet.example/mcp",
    "oauth_audience": "legacy-fleet-audience",
    "credential_anchor": "vault:legacy-fleet",
    "port": 3411,
    "datastore": "/var/lib/serveros/legacy.db",
    "tool_namespace": "serveros-fleet",
    "skills": sorted(parallel.LEGACY_SKILLS),
    "archive_sha256": "1" * 64,
}

NEXT = {
    "backend_plugin_id": "plugin_next_fleet",
    "release_id": "PluginRelease_NEXT_1",
    "name": "serveros-fleet-next",
    "version": "1.0.0",
    "mcp_url": "https://next-fleet.example/mcp",
    "oauth_audience": "next-fleet-audience",
    "credential_anchor": "vault:next-fleet",
    "port": 3412,
    "datastore": "/var/lib/serveros/next.db",
    "tool_namespace": "serveros-fleet-next",
    "skills": sorted(parallel.NEXT_SKILLS),
    "source_pin": "fleet_v1_9skill",
    "archive_sha256": parallel.TRUSTED_SOURCE_PINS["fleet_v1_9skill"]["archive_sha256"],
    "content_sha256": parallel.TRUSTED_SOURCE_PINS["fleet_v1_9skill"]["content_sha256"],
    "members": 15,
}


def preflight(**overrides):
    legacy_current = overrides.pop("legacy_current", copy.deepcopy(LEGACY))
    next_plugin = overrides.pop("next_plugin", copy.deepcopy(NEXT))
    return parallel.preflight(
        legacy_baseline=copy.deepcopy(LEGACY),
        legacy_current=legacy_current,
        next_plugin=next_plugin,
        **overrides)


class SeparateTracks(unittest.TestCase):
    def test_two_tracks_are_distinct_and_legacy_stays_usable(self) -> None:
        out = preflight()
        self.assertEqual(out["verdict"], "PASS", out)
        self.assertNotEqual(out["legacy_backend_plugin_id"],
                            out["next_backend_plugin_id"])
        self.assertNotEqual(out["legacy_mcp_url"], out["next_mcp_url"])
        self.assertTrue(out["legacy_usable_on_failure"])
        self.assertTrue(out["next_install_isolated"])
        self.assertEqual(out["creator_calls"], 0)
        self.assertFalse(out["network"])
        self.assertFalse(out["production_verified"])
        self.assertEqual(len(out["legacy_skills"]), 8)
        self.assertEqual(len(out["next_skills"]), 9)

    def test_missing_next_id_is_blocked_not_synthesized(self) -> None:
        next_plugin = copy.deepcopy(NEXT)
        del next_plugin["backend_plugin_id"]
        out = preflight(next_plugin=next_plugin)
        self.assertEqual(out["code"], "BLOCKED_NEXT_PLUGIN_NOT_REGISTERED")

    def test_next_may_not_reuse_legacy_backend_id(self) -> None:
        next_plugin = copy.deepcopy(NEXT)
        next_plugin["backend_plugin_id"] = LEGACY["backend_plugin_id"]
        out = preflight(next_plugin=next_plugin)
        self.assertEqual(out["code"], "LEGACY_ID_UPDATE_FORBIDDEN")

    def test_next_may_not_share_legacy_url_namespace_or_resources(self) -> None:
        cases = {
            "mcp_url": "NEXT_TAKEOVER_URL",
            "oauth_audience": "NEXT_SHARED_AUDIENCE",
            "credential_anchor": "NEXT_SHARED_CREDENTIAL",
            "port": "NEXT_SHARED_PORT",
            "datastore": "NEXT_SHARED_DATASTORE",
            "tool_namespace": "NEXT_TAKEOVER_NAMESPACE",
        }
        for field, code in cases.items():
            with self.subTest(field=field):
                next_plugin = copy.deepcopy(NEXT)
                next_plugin[field] = LEGACY[field]
                out = preflight(next_plugin=next_plugin)
                self.assertEqual(out["code"], code)

    def test_legacy_rename_or_skill_removal_is_refused(self) -> None:
        renamed = copy.deepcopy(LEGACY)
        renamed["backend_plugin_id"] = "plugin_legacy_renamed"
        self.assertEqual(preflight(legacy_current=renamed)["code"],
                         "LEGACY_RENAMED")
        shrunk = copy.deepcopy(LEGACY)
        shrunk["skills"] = sorted(list(parallel.LEGACY_SKILLS)[:7])
        self.assertEqual(preflight(legacy_current=shrunk)["code"],
                         "LEGACY_CONTROLLER_MUTATED")

    def test_next_must_carry_the_nine_skill_controller(self) -> None:
        next_plugin = copy.deepcopy(NEXT)
        next_plugin["skills"] = sorted(parallel.LEGACY_SKILLS)
        self.assertEqual(preflight(next_plugin=next_plugin)["code"],
                         "NEXT_CONTROLLER_INCOMPLETE")


class SourcePinsAreNotMixed(unittest.TestCase):
    def test_standalone_seven_member_pin_cannot_masquerade_as_next(self) -> None:
        pin = parallel.TRUSTED_SOURCE_PINS["chatgpt_fleet_next_7member"]
        next_plugin = copy.deepcopy(NEXT)
        next_plugin.update({
            "source_pin": "chatgpt_fleet_next_7member",
            "archive_sha256": pin["archive_sha256"],
            "content_sha256": pin["content_sha256"],
            "members": pin["members"],
            "skills": ["fleet-ability-routing", "fleet-agent-collaboration",
                       "fleet-batch-commander", "fleet-multiagent-review"],
        })
        self.assertEqual(preflight(next_plugin=next_plugin)["code"],
                         "MIXED_SOURCE_PIN")

    def test_a_different_digest_under_the_nine_skill_pin_is_refused(self) -> None:
        next_plugin = copy.deepcopy(NEXT)
        next_plugin["archive_sha256"] = "f" * 64
        self.assertEqual(preflight(next_plugin=next_plugin)["code"],
                         "SOURCE_PIN_MISMATCH")

    def test_legacy_and_next_may_not_share_an_archive(self) -> None:
        legacy = copy.deepcopy(LEGACY)
        legacy["archive_sha256"] = NEXT["archive_sha256"]
        self.assertEqual(preflight(legacy_current=legacy)["code"],
                         "MIXED_SOURCE_ARCHIVE")


class NextUpdatesAreScoped(unittest.TestCase):
    def test_same_next_id_requires_consent_and_exact_release(self) -> None:
        previous = copy.deepcopy(NEXT)
        updated = copy.deepcopy(NEXT)
        updated["version"] = "1.0.1"
        updated["release_id"] = "PluginRelease_NEXT_2"
        self.assertEqual(
            preflight(next_plugin=updated, previous_next=previous)["code"],
            "NEXT_UPDATE_CONSENT_REQUIRED")
        self.assertEqual(
            preflight(next_plugin=updated, previous_next=previous,
                     explicit_next_update_consent=True,
                     expected_release_id="PluginRelease_NEXT_OLD")["code"],
            "STALE_EXPECTED_RELEASE_ID")
        out = preflight(next_plugin=updated, previous_next=previous,
                        explicit_next_update_consent=True,
                        expected_release_id="PluginRelease_NEXT_1")
        self.assertEqual(out["verdict"], "PASS", out)
        self.assertTrue(out["same_id_next_update"])

    def test_same_next_id_version_must_advance(self) -> None:
        previous = copy.deepcopy(NEXT)
        updated = copy.deepcopy(NEXT)
        updated["release_id"] = "PluginRelease_NEXT_2"
        out = preflight(next_plugin=updated, previous_next=previous,
                        explicit_next_update_consent=True,
                        expected_release_id="PluginRelease_NEXT_1")
        self.assertEqual(out["code"], "NEXT_VERSION_NOT_ADVANCING")

    def test_never_use_legacy_id_even_with_consent(self) -> None:
        updated = copy.deepcopy(NEXT)
        updated["backend_plugin_id"] = LEGACY["backend_plugin_id"]
        out = preflight(next_plugin=updated, previous_next=copy.deepcopy(LEGACY),
                        explicit_next_update_consent=True,
                        expected_release_id=LEGACY["release_id"])
        self.assertEqual(out["code"], "LEGACY_ID_UPDATE_FORBIDDEN")


class ShadowIsReadOnly(unittest.TestCase):
    def test_shadow_write_is_refused(self) -> None:
        for write in ("create", "send", "stop", "restart", "manager_wake"):
            with self.subTest(write=write):
                out = preflight(shadow={"requested_writes": [write]})
                self.assertEqual(out["code"], "NEXT_SHADOW_WRITE_FORBIDDEN")

    def test_shadow_reads_pass(self) -> None:
        out = preflight(shadow={"requested_writes": []})
        self.assertEqual(out["verdict"], "PASS")
        self.assertTrue(out["shadow_read_only"])


class MockCreatorNeverInvents(unittest.TestCase):
    def test_mock_requires_exact_registered_next_id(self) -> None:
        creator = parallel.MockCreator(registered_plugin_id=None,
                                       legacy_plugin_id=LEGACY["backend_plugin_id"])
        out = creator.update_plugin(plugin_id=NEXT["backend_plugin_id"],
                                    expected_release_id=NEXT["release_id"],
                                    archive_sha256=NEXT["archive_sha256"])
        self.assertEqual(out["status"], "BLOCKED_NEXT_PLUGIN_NOT_REGISTERED")
        self.assertFalse(out["registered"])

    def test_mock_refuses_legacy_id_and_never_sends(self) -> None:
        creator = parallel.MockCreator(
            registered_plugin_id=NEXT["backend_plugin_id"],
            legacy_plugin_id=LEGACY["backend_plugin_id"])
        out = creator.update_plugin(plugin_id=LEGACY["backend_plugin_id"],
                                    expected_release_id=LEGACY["release_id"],
                                    archive_sha256=NEXT["archive_sha256"])
        self.assertEqual(out["status"], "LEGACY_ID_UPDATE_FORBIDDEN")
        out = creator.update_plugin(plugin_id=NEXT["backend_plugin_id"],
                                    expected_release_id=NEXT["release_id"],
                                    archive_sha256=NEXT["archive_sha256"])
        self.assertEqual(out["status"], "SIMULATED_NOT_SENT")
        self.assertFalse(out["network"])
        self.assertEqual(len(creator.calls), 2)


if __name__ == "__main__":
    unittest.main()
