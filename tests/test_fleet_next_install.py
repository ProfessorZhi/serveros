"""No production effects from the Fleet Next wrapper unless explicitly executed.

These are mock/offline tests only.  They never call systemd, use an SSH tunnel,
register OAuth or install a ChatGPT plugin.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from fleet_mcp.deploy import next_install, next_dual_stack_preflight as gate  # noqa: E402


def candidate() -> dict:
    return {
        "schema": gate.SCHEMA,
        "v1": {
            "service_unit": gate.V1_UNIT,
            "runtime_root": "/home/operator/runtime-assets/old-ingress/ingress",
            "config_path": "/home/operator/.config/old-fleet/config.yaml",
            "oauth_state_root": "/home/operator/.local/state/old-fleet-oauth",
            "local_listen": {"host": "127.0.0.1", "port": 8401},
            "public_mcp_url": "https://v1.example.invalid/mcp",
            "plugin_id": "plugin_asdk_app_existing_v1",
        },
        "next": {
            "service_unit": gate.NEXT_UNIT,
            "runtime_root": "/home/operator/runtime-assets/new-ingress/ingress-next",
            "config_path": "/home/operator/.config/new-fleet/config.yaml",
            "oauth_state_root": "/home/operator/.local/state/new-fleet-oauth",
            "local_listen": {"host": "127.0.0.1", "port": 8402},
            "public_mcp_url": "https://next.example.invalid/mcp",
            "plugin_id": "plugin_asdk_app_fresh_next",
        },
        "limits": dict(next_install.BUDGET),
        "attestations": {
            "v1_identity_observed": True,
            "next_owner_binding_reviewed": True,
            "next_oauth_client_authorized": True,
            "rollback_ready": True,
        },
    }


def cli(argv: list[str], proposal: dict) -> tuple[int, dict]:
    with tempfile.TemporaryDirectory() as temp:
        p = Path(temp) / "proposal.json"
        p.write_text(json.dumps(proposal), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = next_install.main([argv[0], "--proposal", str(p)] + argv[1:])
    return rc, json.loads(stdout.getvalue())


class NextOnlyInstaller(unittest.TestCase):
    def test_safe_preview_names_next_unit_and_has_no_effect(self):
        preview = next_install.plan(candidate(), "install")
        self.assertEqual(preview["decision"], "DRY_RUN_READY")
        self.assertEqual(preview["unit"], "serveros-fleet-next-mcp.service")
        self.assertFalse(preview["same_as_v1"])
        self.assertEqual(preview["writes"], [])
        self.assertEqual(preview["executes"], [])
        self.assertEqual(preview["limits"], next_install.BUDGET)

    def test_regular_cli_is_dry_run_and_cannot_import_installer(self):
        with mock.patch.object(next_install, "_load_legacy_installer",
                               side_effect=AssertionError("must not run")):
            rc, doc = cli(["install"], candidate())
        self.assertEqual(rc, 0)
        self.assertEqual(doc["decision"], "DRY_RUN_READY")
        self.assertEqual(doc["writes"], [])

    def test_next_root_needs_own_composition(self):
        p = candidate()
        p["next"]["runtime_root"] = "/home/operator/runtime-assets/new-ingress/ingress"
        self.assertEqual(next_install.plan(p, "install")["decision"], "BLOCKED")

    def test_unit_budget_cannot_be_changed_in_plan_only(self):
        p = candidate()
        p["limits"]["cpu_quota_pct"] = 90
        self.assertIn("limits: must match", " ".join(next_install.plan(p, "install")["blockers"]))

    def test_missing_plugin_id_refuses_even_without_sudo(self):
        p = candidate()
        p["next"]["plugin_id"] = None
        result = next_install.plan(p, "install")
        self.assertEqual(result["decision"], "BLOCKED")

    def test_v1_service_name_cannot_be_selected(self):
        p = candidate()
        p["next"]["service_unit"] = gate.V1_UNIT
        self.assertEqual(next_install.plan(p, "install")["decision"], "BLOCKED")

    def test_actual_execute_without_owner_ack_is_refused_before_legacy_module(self):
        with mock.patch.object(next_install, "_load_legacy_installer",
                               side_effect=AssertionError("must not run")):
            rc, doc = cli(["install", "--execute"], candidate())
        self.assertEqual(rc, 2)
        self.assertEqual(doc["decision"], "BLOCKED")
        self.assertIn("--owner-approved", doc["reason"])

    def test_legacy_uninstall_is_never_forwarded_by_next_wrapper(self):
        with mock.patch.object(next_install, "_load_legacy_installer",
                               side_effect=AssertionError("must not run")):
            rc, doc = cli(["uninstall", "--execute", "--owner-approved"], candidate())
        self.assertEqual(rc, 2)
        self.assertEqual(doc["decision"], "BLOCKED")

    def test_template_uses_separate_journal_and_real_limits(self):
        content = (ROOT / "fleet_mcp/deploy/serveros-fleet-next-mcp.service").read_text()
        for line in ("MemoryMax=1536M", "MemorySwapMax=0", "CPUQuota=40%",
                     "TasksMax=96", "NoNewPrivileges=yes",
                     "SyslogIdentifier=serveros-fleet-next-mcp"):
            self.assertIn(line, content)

    def test_no_v1_installer_constants_are_edited_in_source(self):
        shared = (ROOT / "fleet_mcp/deploy/install.py").read_text()
        self.assertIn('UNIT_NAME = "serveros-fleet-mcp.service"', shared)
        self.assertIn('UNIT_TEMPLATE = "fleet_mcp/deploy/serveros-fleet-mcp.service"', shared)


if __name__ == "__main__":
    unittest.main()
