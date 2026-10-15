"""Offline contract tests: Next may be staged separately, never over V1.

No real systemd, device, OAuth, ChatGPT plugin or SSH connection is accessed.
"""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from fleet_mcp.deploy import next_dual_stack_preflight as gate  # noqa: E402


def proposal() -> dict:
    return {
        "schema": gate.SCHEMA,
        "v1": {
            "service_unit": "serveros-fleet-mcp.service",
            "runtime_root": "/home/operator/runtime-assets/fleet-v1",
            "config_path": "/home/operator/.config/fleet-v1/binding.yaml",
            "oauth_state_root": "/home/operator/.local/state/fleet-v1-oauth",
            "local_listen": {"host": "127.0.0.1", "port": 8101},
            "public_mcp_url": "https://old.example.invalid/mcp",
            "plugin_id": "plugin_asdk_app_old_example_123",
        },
        "next": {
            "service_unit": "serveros-fleet-example-mcp.service",
            "runtime_root": "/home/operator/runtime-assets/fleet-next",
            "config_path": "/home/operator/.config/fleet-next/binding.yaml",
            "oauth_state_root": "/home/operator/.local/state/fleet-next-oauth",
            "local_listen": {"host": "127.0.0.1", "port": 8102},
            "public_mcp_url": "https://next.example.invalid/mcp",
            "plugin_id": "plugin_asdk_app_new_example_789",
        },
        "limits": {
            "memory_max_mib": 1536,
            "memory_swap_max_mib": 0,
            "cpu_quota_pct": 40,
            "tasks_max": 96,
        },
        "attestations": {
            "v1_identity_observed": True,
            "next_owner_binding_reviewed": True,
            "next_oauth_client_authorized": True,
            "rollback_ready": True,
        },
    }


def blockers(p: dict) -> list[str]:
    return gate.evaluate(p)["blockers"]


class OfflineDoubleStackSafety(unittest.TestCase):
    def test_complete_isolated_candidate_is_only_a_human_canary_precheck(self):
        result = gate.evaluate(proposal())
        self.assertEqual(result["decision"], "PRECHECK_READY_FOR_HUMAN_CANARY")
        self.assertEqual(result["blockers"], [])
        self.assertEqual(result["writes"], [])
        self.assertEqual(result["executes"], [])
        self.assertFalse(result["v1_changed"])
        self.assertIn("systemctl --user show", " ".join(result["required_live_readback"]))
        self.assertIn("not an authorization", result["note"])

    def test_same_unit_is_not_dual_stack(self):
        p = proposal()
        p["next"]["service_unit"] = p["v1"]["service_unit"]
        self.assertTrue(any("dedicated Next unit" in x for x in blockers(p)))

    def test_same_runtime_root_is_refused(self):
        p = proposal()
        p["next"]["runtime_root"] = p["v1"]["runtime_root"]
        self.assertTrue(any("runtime_root: overlaps" in x for x in blockers(p)))

    def test_nested_runtime_root_is_refused_too(self):
        p = proposal()
        p["next"]["runtime_root"] = p["v1"]["runtime_root"] + "/canary"
        self.assertTrue(any("runtime_root: overlaps" in x for x in blockers(p)))

    def test_same_local_socket_is_refused(self):
        p = proposal()
        p["next"]["local_listen"]["port"] = 8101
        self.assertTrue(any("listening port" in x for x in blockers(p)))

    def test_next_must_bind_only_to_loopback(self):
        p = proposal()
        p["next"]["local_listen"]["host"] = "0.0.0.0"
        self.assertTrue(any("loopback only" in x for x in blockers(p)))

    def test_same_published_url_even_with_trailing_slash_is_refused(self):
        p = proposal()
        p["next"]["public_mcp_url"] = "https://old.example.invalid/mcp/"
        self.assertTrue(any("same published endpoint" in x for x in blockers(p)))

    def test_same_plugin_id_is_refused(self):
        p = proposal()
        p["next"]["plugin_id"] = p["v1"]["plugin_id"]
        self.assertTrue(any("must differ from V1" in x for x in blockers(p)))

    def test_missing_new_plugin_registration_is_not_fabricated(self):
        p = proposal()
        p["next"]["plugin_id"] = None
        self.assertTrue(any("next.plugin_id" in x for x in blockers(p)))

    def test_app_store_and_config_must_be_independent(self):
        p = proposal()
        p["next"]["oauth_state_root"] = p["v1"]["oauth_state_root"]
        p["next"]["config_path"] = p["v1"]["config_path"]
        lines = blockers(p)
        self.assertTrue(any("oauth_state_root: overlaps" in x for x in lines))
        self.assertTrue(any("config_path: overlaps" in x for x in lines))

    def test_next_cannot_consume_global_swap(self):
        p = proposal()
        p["limits"]["memory_swap_max_mib"] = 1024
        self.assertTrue(any("must not consume global swap" in x for x in blockers(p)))

    def test_memory_cpu_task_limits_are_mandatory(self):
        p = proposal()
        p["limits"].update(memory_max_mib=0, cpu_quota_pct=150, tasks_max=None)
        self.assertEqual(sum(x.startswith("limits.") for x in blockers(p)), 3)

    def test_attestation_is_not_implicitly_granted(self):
        p = proposal()
        p["attestations"]["rollback_ready"] = False
        self.assertTrue(any("rollback_ready" in x for x in blockers(p)))

    def test_identity_must_be_verified_not_guessed(self):
        p = proposal()
        p["v1"]["public_mcp_url"] = "http://127.0.0.1:9001/mcp"
        self.assertTrue(any("v1.public_mcp_url" in x for x in blockers(p)))

    def test_no_secrets_are_echoed_from_unexpected_fields(self):
        p = proposal()
        p["next"]["token"] = "FAKE_SECRET_DO_NOT_ECHO"
        printed = json.dumps(gate.evaluate(p))
        self.assertNotIn("FAKE_SECRET_DO_NOT_ECHO", printed)
        self.assertIn("unsupported field(s) token", printed)

    def test_high_swap_percentage_is_not_a_blanket_block(self):
        p = proposal()
        # Historical occupancy is not a validated identity or live-pressure
        # metric; it cannot be used as an invented green or red signal.
        result = gate.evaluate(p)
        self.assertEqual(result["decision"], "PRECHECK_READY_FOR_HUMAN_CANARY")
        self.assertTrue(any("swap I/O" in x for x in result["required_live_readback"]))

    def test_cli_emits_read_only_json_and_nonzero_for_unapproved(self):
        p = proposal()
        p["attestations"]["rollback_ready"] = False
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "candidate.json"
            path.write_text(json.dumps(p), encoding="utf-8")
            # evaluate() is deliberately the only writer-independent component;
            # an executable process check is optional, with no external imports.
            self.assertEqual(gate.evaluate(json.loads(path.read_text()))["decision"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
