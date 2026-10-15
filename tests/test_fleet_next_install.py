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
    """Run the CLI and return its exit code plus the ONE receipt it printed.

    Every path prints exactly one document: a blocked preview, a dry-run
    preview, a `Refused` refusal, and — for `--execute` — a single Next-only
    receipt republished from the legacy installer's own JSON. So this helper
    requires exactly one, and an empty or doubled stream is a failure of the
    contract under test rather than a result to be tolerated.
    """
    with tempfile.TemporaryDirectory() as temp:
        p = Path(temp) / "proposal.json"
        p.write_text(json.dumps(proposal), encoding="utf-8")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc = next_install.main([argv[0], "--proposal", str(p)] + argv[1:])
    text = stdout.getvalue()
    payload, problem = next_install.one_receipt(text)
    if problem is not None:
        raise AssertionError(f"cli output contract: {problem}; raw={text[:200]!r}")
    return rc, payload


def legacy_stage_receipt(**over) -> str:
    """What `install.py::install()` actually prints on its `--no-start` path.

    Transcribed from the real function, not invented: it is the one-document
    contract the wrapper consumes and republishes.
    """
    payload = {
        "unit_path": "/home/operator/.config/systemd/user/serveros-fleet-next-mcp.service",
        "started": False,
        "next": "systemctl --user start serveros-fleet-next-mcp.service",
        "tree_sha256": "0" * 64,
    }
    payload.update(over)
    return json.dumps(payload, indent=2)


def legacy_full_receipt(**over) -> str:
    """What `install.py::install()` prints on the start path."""
    payload = {
        "unit_path": "/home/operator/.config/systemd/user/serveros-fleet-next-mcp.service",
        "started": True,
        "health": {"ok": True},
        "files": 42,
        "tree_sha256": "0" * 64,
    }
    payload.update(over)
    return json.dumps(payload, indent=2)


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
                # The real installer prints one JSON receipt on this path.
                print(legacy_stage_receipt())
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
        # ...and the wrapper republishes it as one Next-only receipt.
        self.assertEqual(output["decision"], "EXECUTED")
        self.assertEqual(output["unit"], gate.NEXT_UNIT)
        self.assertIs(output["started"], False)
        self.assertFalse(output["v1_action"])
        self.assertEqual(output["executes"], [gate.NEXT_UNIT])
        self.assertIn("systemctl --user start", output["receipt"]["next"])

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
        content = (ROOT / "fleet_mcp/deploy/serveros-fleet-next-mcp.service").read_text()
        for line in ("MemoryMax=1536M", "MemorySwapMax=0", "CPUQuota=40%",
                     "TasksMax=96", "NoNewPrivileges=yes",
                     "SyslogIdentifier=serveros-fleet-next-mcp"):
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
                                                 "simulated canary failure",
                                                 effect_attempted=True)
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
                                                 "before-change failure",
                                                 effect_attempted=False)

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



class TheExecutePathMustProduceExactlyOneReceiptTest(unittest.TestCase):
    """Success is a receipt, not an exit code.

    The legacy `install.py` prints one JSON object on each of its success
    paths. The wrapper captures it and republishes exactly one Next-only
    receipt. A command that returned 0 without leaving one has not said what it
    did, and "it probably worked" is not a state this wrapper reports.
    """

    def test_the_real_receipt_shapes_are_accepted(self):
        for text in (legacy_stage_receipt(), legacy_full_receipt()):
            with self.subTest(text=text[:40]):
                payload, problem = next_install.one_receipt(text)
                self.assertIsNone(problem)
                self.assertIn("unit_path", payload)

    def test_surrounding_whitespace_is_not_a_second_document(self):
        payload, problem = next_install.one_receipt("\n" + legacy_stage_receipt() + "\n\n")
        self.assertIsNone(problem)
        self.assertEqual(payload["started"], False)

    def test_an_empty_stream_is_refused(self):
        for text in ("", "   ", "\n"):
            with self.subTest(text=repr(text)):
                payload, problem = next_install.one_receipt(text)
                self.assertIsNone(payload)
                self.assertIn("no receipt", problem)

    def test_two_documents_are_refused(self):
        payload, problem = next_install.one_receipt(
            legacy_stage_receipt() + "\n" + legacy_full_receipt())
        self.assertIsNone(payload)
        self.assertIn("2 JSON documents", problem)

    def test_a_corrupt_stream_is_refused(self):
        payload, problem = next_install.one_receipt('{"started": false,,}')
        self.assertIsNone(payload)
        self.assertIn("not valid JSON", problem)

    def test_a_non_object_document_is_refused(self):
        payload, problem = next_install.one_receipt("[1, 2, 3]")
        self.assertIsNone(payload)
        self.assertIn("not a JSON object", problem)

    # -- through the CLI ---------------------------------------------------

    def _execute(self, emit, p: dict):
        """Run `stage --execute` with an installer whose stdout is `emit()`."""
        class FakeMod:
            calls = []
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                        "probe": "http://127.0.0.1:8401" if old else "http://127.0.0.1:8402"}
            def read_link(self, path):
                return None
            def main(self, args):
                self.calls.append(args)
                emit()
                return 0
        module = FakeMod()
        with mock.patch.object(next_install, "_load_legacy_installer", return_value=module):
            return cli(["stage", "--execute", "--owner-approved"], p)

    def _bootstrap_stage(self) -> dict:
        p = candidate()
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        return p

    def test_the_happy_path_republishes_exactly_one_document(self):
        rc, doc = self._execute(lambda: print(legacy_stage_receipt()),
                                self._bootstrap_stage())
        self.assertEqual(rc, 0)
        self.assertEqual(doc["decision"], "EXECUTED")
        self.assertEqual(doc["receipt"]["started"], False)

    def test_no_receipt_is_unknown_not_success(self):
        """The behaviour this replaces tolerated an empty stream and called it
        success. It is not success, and it is not a clean failure either: the
        command may or may not have taken effect."""
        rc, doc = self._execute(lambda: None, self._bootstrap_stage())
        self.assertNotEqual(rc, 0)
        self.assertEqual(doc["decision"], "UNKNOWN")
        self.assertIn("no receipt", doc["reason"])
        self.assertNotEqual(doc["writes"], [], "an attempted effect is not writes=[]")
        self.assertEqual(doc["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertFalse(doc["v1_action"])

    def test_corrupt_output_is_unknown_not_success(self):
        rc, doc = self._execute(lambda: print('{"started": false,,}'),
                                self._bootstrap_stage())
        self.assertNotEqual(rc, 0)
        self.assertEqual(doc["decision"], "UNKNOWN")
        self.assertIn("not valid JSON", doc["reason"])

    def test_two_documents_are_unknown_not_a_pick_of_one(self):
        rc, doc = self._execute(
            lambda: (print(legacy_stage_receipt()),
                     print(legacy_stage_receipt(started=True))),
            self._bootstrap_stage())
        self.assertNotEqual(rc, 0)
        self.assertEqual(doc["decision"], "UNKNOWN")
        self.assertIn("2 JSON documents", doc["reason"])

    def test_a_dying_installer_that_printed_a_receipt_is_quoted_back(self):
        """`install.py` prints its receipt and THEN dies when the service did
        not answer. The wrapper must not lose that fact in the refusal."""
        p = candidate()
        class FakeMod:
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                        "probe": "http://127.0.0.1:8401" if old else "http://127.0.0.1:8402"}
            def read_link(self, path):
                return None
            def systemctl(self, *a, **k):
                return type("Result", (), {"returncode": 0, "stdout": ""})()
            def main(self, args):
                print(legacy_full_receipt(started=False, health=None))
                raise SystemExit(3)
        with mock.patch.object(next_install, "_load_legacy_installer", return_value=FakeMod()):
            rc, report = cli(["stage", "--execute", "--owner-approved"], p)
        self.assertEqual(rc, 2)
        self.assertIn("started=False", report["reason"])
        self.assertEqual(report["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertFalse(report["v1_action"])


class ASameRevisionRestartIsNotUntouchedTest(unittest.TestCase):
    """The `current` symlink cannot distinguish "nothing was attempted" from
    "the unit was restarted and did not come back".

    An install over an already-live unit takes the restart path. A restart of
    the SAME revision leaves `current` byte-identical while replacing the
    process serving it, so judging by symlink equality alone reported
    "existing Next untouched" for exactly the case that needs a rollback.
    """

    REV = Path("/example/new/ingress-next/rev-same")
    CAPS = ("MemoryMax=1610612736\nMemorySwapMax=0\nTasksMax=96\n"
            "CPUQuotaPerSecUSec=400ms\n")

    def _restart_that_never_comes_back(self, p: dict):
        class FakeMod:
            def __init__(self):
                self.calls = []
                self.health_calls = 0
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                        "probe": "http://127.0.0.1:8401" if old else "http://127.0.0.1:8402"}
            def read_link(self, path):
                # The same revision before and after: nothing to restore.
                return ASameRevisionRestartIsNotUntouchedTest.REV if path.name == "current" else None
            def point(self, *args, **kwargs):
                raise AssertionError("a same-revision restart has no pointer to restore")
            def systemctl(self, *args, **kwargs):
                self.calls.append(args)
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": ASameRevisionRestartIsNotUntouchedTest.CAPS})()
            def wait_for_health(self, *args):
                self.health_calls += 1
                # V1 answers before the launch; the post-launch check fails.
                return {"ok": True} if self.health_calls == 1 else False
            def main(self, args):
                print(legacy_full_receipt(revision=str(ASameRevisionRestartIsNotUntouchedTest.REV)))
                return 0
        return FakeMod()

    def _run(self, p: dict):
        instance = self._restart_that_never_comes_back(p)
        with mock.patch.object(next_install, "_load_legacy_installer", return_value=instance):
            with mock.patch.object(next_install, "_current_pressure", return_value=None):
                rc, report = cli(["install", "--execute", "--owner-approved"], p)
        return instance, rc, report

    def test_it_is_not_reported_as_untouched(self):
        instance, rc, report = self._run(candidate())
        self.assertEqual(rc, 2)
        self.assertNotIn("untouched", report["reason"])

    def test_it_stops_next_and_says_the_state_is_unknown(self):
        instance, rc, report = self._run(candidate())
        self.assertIn(("stop", gate.NEXT_UNIT), instance.calls)
        self.assertIn("Next-only stop/rollback", report["reason"])
        self.assertIn("UNKNOWN", report["reason"])

    def test_the_receipt_never_claims_a_clean_or_safe_outcome(self):
        instance, rc, report = self._run(candidate())
        self.assertEqual(report["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertNotEqual(report["writes"], [], "an attempted effect is not writes=[]")
        self.assertEqual(report["executes"], [gate.NEXT_UNIT])
        self.assertFalse(report["v1_action"])

    def test_a_pointer_restore_must_not_be_invented(self):
        """The fake raises if `point()` is called, which is how this asserts the
        wrapper does not fabricate a revision move it never observed."""
        instance, rc, report = self._run(candidate())
        self.assertEqual(rc, 2)

    def test_effect_not_attempted_still_leaves_a_healthy_next_alone(self):
        """The complement, and the reason the flag exists: same unchanged
        symlink, no effect attempted — and Next must not be stopped."""
        root = Path("/example/new/ingress-next")
        rev = self.REV
        class Fake:
            def read_link(self, path):
                return rev if path.name == "current" else None
            def systemctl(self, *args, **kwargs):
                raise AssertionError("must not stop a healthy Next")
            def point(self, *args):
                raise AssertionError("must not move anything")
        with self.assertRaisesRegex(next_install.Refused, "existing Next untouched"):
            next_install._restore_failed_install(Fake(), root, (rev, None),
                                                 "pre-effect failure",
                                                 effect_attempted=False)

    def test_the_two_branches_differ_only_by_the_flag(self):
        """Same unchanged symlink, opposite outcomes: the discriminator is the
        caller's knowledge, not the filesystem's appearance."""
        rev = self.REV
        root = Path("/example/new/ingress-next")
        stopped = []

        class Attempted:
            def read_link(self, path):
                return rev if path.name == "current" else None
            def systemctl(self, *args, **kwargs):
                stopped.append(args)
                return type("Result", (), {"returncode": 0})()

        with self.assertRaisesRegex(next_install.Refused, "UNKNOWN"):
            next_install._restore_failed_install(Attempted(), root, (rev, None),
                                                 "attempted", effect_attempted=True)
        self.assertEqual(stopped, [("stop", gate.NEXT_UNIT)])


if __name__ == "__main__":
    unittest.main()
