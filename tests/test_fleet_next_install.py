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
        self.assertEqual(preview["unit"], "serveros-fleet-example-mcp.service")
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

    def test_bootstrap_stages_with_no_new_plugin_id_and_no_oauth(self):
        p = candidate()
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        with mock.patch.object(next_install, "_load_legacy_installer",
                               side_effect=AssertionError("no system actions in dry-run")):
            rc, summary = cli(["stage"], p)
        self.assertEqual(rc, 0)
        self.assertEqual(summary["mode"], "NEXT_ONLY_STAGE_NO_START")
        self.assertEqual(summary["unit"], gate.NEXT_UNIT)
        self.assertEqual(summary["executes"], [])

    def test_execute_stage_always_passes_no_start_into_next_installer(self):
        p = candidate()
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        class FakeMod:
            calls = []
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402,
                        "bind": "127.0.0.1", "probe": "http://127.0.0.1:8401" if old else "http://127.0.0.1:8402"}
            def read_link(self, path):
                return None
            def main(self, args):
                self.calls.append(args)
                return 0
        module = FakeMod()
        with mock.patch.object(next_install, "_load_legacy_installer", return_value=module):
            rc, output = cli(["stage", "--execute", "--owner-approved"], p)
        self.assertEqual(rc, 0)
        self.assertEqual(len(module.calls), 1)
        self.assertIn("--no-start", module.calls[0])
        self.assertEqual(module.calls[0][0], "install")
        self.assertIn("--composition", module.calls[0])
        self.assertIn("ingress-next", module.calls[0])

    def test_stage_does_not_repoint_a_running_existing_next(self):
        p = candidate()
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        class FakeMod:
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402,
                        "bind": "127.0.0.1", "probe": "http://127.0.0.1:8401"}
            def read_link(self, path):
                return Path("/preexisting-next-revision") if path.name == "current" else None
            def main(self, args):
                raise AssertionError("must not stage over an existing Next")
        with mock.patch.object(next_install, "_load_legacy_installer", return_value=FakeMod()):
            rc, output = cli(["stage", "--execute", "--owner-approved"], p)
        self.assertEqual(rc, 2)
        self.assertIn("empty Next revision root", output["reason"])

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
        content = (ROOT / "fleet_mcp/deploy/serveros-fleet-example-mcp.service").read_text()
        for line in ("MemoryMax=1536M", "MemorySwapMax=0", "CPUQuota=40%",
                     "TasksMax=96", "NoNewPrivileges=yes",
                     "SyslogIdentifier=serveros-fleet-example-mcp"):
            self.assertIn(line, content)

    def test_no_v1_installer_constants_are_edited_in_source(self):
        shared = (ROOT / "fleet_mcp/deploy/install.py").read_text()
        self.assertIn('UNIT_NAME = "serveros-fleet-mcp.service"', shared)
        self.assertIn('UNIT_TEMPLATE = "fleet_mcp/deploy/serveros-fleet-mcp.service"', shared)

    def test_effective_cgroup_limits_are_checked_on_actual_service(self):
        class FakeMod:
            def systemctl(self, *args, **kw):
                return type("Result", (), {"returncode": 0, "stdout": (
                    "MemoryMax=1610612736\nMemorySwapMax=0\nTasksMax=96\n"
                    "CPUQuotaPerSecUSec=400ms\n")})()
        self.assertEqual(next_install._effective_limit_errors(FakeMod()), [])

    def test_missing_live_swap_cap_is_not_reported_as_healthy(self):
        class FakeMod:
            def systemctl(self, *args, **kw):
                return type("Result", (), {"returncode": 0, "stdout": (
                    "MemoryMax=1610612736\nMemorySwapMax=infinity\nTasksMax=96\n"
                    "CPUQuotaPerSecUSec=infinity\n")})()
        failures = next_install._effective_limit_errors(FakeMod())
        self.assertTrue(any("MemorySwapMax" in f for f in failures))
        self.assertTrue(any("CPUQuotaPerSecUSec" in f for f in failures))


    def test_emergency_rollback_survives_revoked_oauth_and_missing_plugin(self):
        p = candidate()
        p["next"]["plugin_id"] = None
        p["next"]["public_mcp_url"] = None
        p["limits"] = None
        p["attestations"] = None
        with mock.patch.object(next_install, "_load_legacy_installer",
                               side_effect=AssertionError("dry-run cannot import")):
            rc, report = cli(["rollback"], p)
        self.assertEqual(rc, 0)
        self.assertEqual(report["decision"], "DRY_RUN_READY")
        self.assertEqual(report["mode"], "NEXT_ONLY_RECOVERY")
        self.assertIsNone(report["limits"])

    def test_recovery_does_not_depend_on_source_unit_template(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(next_install, "_HERE", Path(tmp)):
                self.assertEqual(next_install.plan(candidate(), "rollback")["decision"],
                                 "DRY_RUN_READY")
                self.assertEqual(next_install.plan(candidate(), "status")["decision"],
                                 "DRY_RUN_READY")
                self.assertEqual(next_install.plan(candidate(), "install")["decision"],
                                 "BLOCKED")

    def test_symlink_alias_to_v1_is_refused_before_any_effect(self):
        with tempfile.TemporaryDirectory() as tmp:
            old = Path(tmp) / "old"
            old.mkdir()
            alias = Path(tmp) / "alias"
            alias.symlink_to(old, target_is_directory=True)
            p = candidate()
            p["v1"]["runtime_root"] = str(old)
            p["next"]["runtime_root"] = str(alias / "ingress-next")
            self.assertEqual(gate.evaluate(p)["decision"],
                             "PRECHECK_READY_FOR_HUMAN_CANARY")
            with self.assertRaises(next_install.Refused):
                next_install._real_path_isolation(p)

    def test_failed_new_revision_stops_next_only_and_restores_pointers(self):
        root = Path("/example/new/ingress-next")
        old_rev = Path("/example/previous-revision")
        new_rev = Path("/example/failed-revision")
        class Fake:
            def __init__(self):
                self.links = {"current": new_rev, "previous": old_rev}
                self.calls = []
            def read_link(self, path):
                return self.links.get(path.name)
            def point(self, path, target):
                self.links[path.name] = target
            def systemctl(self, *args, **kw):
                self.calls.append(args)
                return type("Result", (), {"returncode": 0})()
        m = Fake()
        with self.assertRaisesRegex(next_install.Refused, "Next-only stop/rollback"):
            next_install._restore_failed_install(m, root, (old_rev, None),
                                                  "simulated canary failure")
        self.assertEqual(m.calls, [("stop", gate.NEXT_UNIT)])
        self.assertEqual(m.links["current"], old_rev)
        self.assertIsNone(m.links["previous"])

    def test_error_before_revision_switch_does_not_stop_healthy_old_next(self):
        root = Path("/example/new/ingress-next")
        prior = Path("/example/previous")
        class Fake:
            def read_link(self, path):
                return prior if path.name == "current" else None
            def systemctl(self, *args, **kw):
                raise AssertionError("must not stop old Next")
            def point(self, *args):
                raise AssertionError("must not move old Next")
        with self.assertRaisesRegex(next_install.Refused, "existing Next untouched"):
            next_install._restore_failed_install(Fake(), root, (prior, None),
                                                  "before-change failure")

    def test_simulated_install_crash_recovers_next_only(self):
        p = candidate()
        root = Path(p["next"]["runtime_root"])
        old_rev, new_rev = root / "rev-old", root / "rev-bad"
        class MockModule:
            def __init__(self):
                self.links = {"current": old_rev, "previous": None}
                self.calls = []
            def binding(self, path):
                if str(path) == p["v1"]["config_path"]:
                    return {"port": 8401, "bind": "127.0.0.1", "probe": "http://127.0.0.1:8401"}
                return {"port": 8402, "bind": "127.0.0.1", "probe": "http://127.0.0.1:8402"}
            def wait_for_health(self, *args):
                return {"ok": True}
            def read_link(self, path):
                return self.links[path.name]
            def point(self, path, target):
                self.links[path.name] = target
            def systemctl(self, *args, **kwargs):
                self.calls.append(args)
                return type("Result", (), {"returncode": 0})()
            def main(self, args):
                self.links["previous"] = old_rev
                self.links["current"] = new_rev
                raise SystemExit(3)
        instance = MockModule()
        with mock.patch.object(next_install, "_load_legacy_installer",
                               return_value=instance):
            with mock.patch.object(next_install, "_current_pressure", return_value=None):
                rc, report = cli(["install", "--execute", "--owner-approved"], p)
        self.assertEqual(rc, 2)
        self.assertIn("Next-only stop/rollback", report["reason"])
        self.assertEqual(report["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertEqual(report["executes"], [gate.NEXT_UNIT])
        self.assertNotEqual(report["writes"], [])
        self.assertFalse(report["v1_action"])
        self.assertEqual(instance.calls, [("stop", gate.NEXT_UNIT)])
        self.assertEqual(instance.links["current"], old_rev)
        self.assertIsNone(instance.links["previous"])



if __name__ == "__main__":
    unittest.main()
