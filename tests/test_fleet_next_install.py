"""No production effects from the Fleet Next wrapper unless explicitly executed.

These are mock/offline tests only.  They never call systemd, use an SSH tunnel,
register OAuth or install a ChatGPT plugin.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from fleet_mcp.deploy import install  # noqa: E402
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
        "unit_path": "/home/operator/.config/systemd/user/serveros-fleet-example-mcp.service",
        "started": False,
        "next": "systemctl --user start serveros-fleet-example-mcp.service",
        "tree_sha256": "0" * 64,
    }
    payload.update(over)
    return json.dumps(payload, indent=2)


def legacy_full_receipt(**over) -> str:
    """What `install.py::install()` prints on the start path."""
    payload = {
        "unit_path": "/home/operator/.config/systemd/user/serveros-fleet-example-mcp.service",
        "started": True,
        "health": {"ok": True},
        "files": 42,
        "tree_sha256": "0" * 64,
    }
    payload.update(over)
    return json.dumps(payload, indent=2)


class _FakeInstallModule:
    """The no-op form of the bits of `install.py` the wrapper may consult.

    A fake modelling a CLEAN git source: `tree_state` answers None and
    `git_revision` has no HEAD. Tests that need a dirty tree or a real HEAD
    override these.

    `begin_effect` models the shared installer's EFFECT BOUNDARY. A fake whose
    `main` dies the way a MID-INSTALL death behaves has to leave the same
    evidence the real installer leaves, or the wrapper cannot tell that death
    from a refusal that never wrote anything — which is the whole distinction
    under test.
    """

    EFFECT_MARKER = install.EFFECT_MARKER

    def git_revision(self, source):
        return None

    def tree_state(self, source):
        return None

    def begin_effect(self, args) -> Path:
        """Leave the marker where the wrapper looks for it, as install.py does."""
        runtime_root = Path(args[args.index("--runtime-root") + 1])
        composition = args[args.index("--composition") + 1]
        root = runtime_root / composition
        root.mkdir(parents=True, exist_ok=True)
        marker = root / self.EFFECT_MARKER
        marker.write_text("0\n", encoding="utf-8")
        return marker


def writable_root(p: dict, tmp: str) -> Path:
    """A Next composition root a fake can leave effect evidence in."""
    root = Path(tmp) / next_install.COMPOSITION
    root.mkdir(parents=True, exist_ok=True)
    p["next"]["runtime_root"] = str(root)
    return root


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
        p["next"]["public_mcp_url"] = None
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        with mock.patch.object(next_install, "_load_legacy_installer",
                               side_effect=AssertionError("no system actions in dry-run")):
            rc, summary = cli(["stage"], p)
        self.assertEqual(rc, 0)
        self.assertEqual(summary["mode"], "NEXT_ONLY_STAGE_NO_START")
        self.assertEqual(summary["unit"], gate.NEXT_UNIT)
        self.assertEqual(summary["executes"], [])

    def test_full_install_still_refuses_unassigned_next_public_url(self):
        p = candidate()
        p["next"]["public_mcp_url"] = None
        verdict = next_install.plan(p, "install")
        self.assertEqual(verdict["decision"], "BLOCKED")
        self.assertTrue(any("next.public_mcp_url" in reason
                            for reason in verdict["blockers"]))

    def test_execute_stage_always_passes_no_start_into_next_installer(self):
        p = candidate()
        p["next"]["public_mcp_url"] = None
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        class FakeMod(_FakeInstallModule):
            calls = []
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402,
                        "bind": "127.0.0.1", "probe": "http://127.0.0.1:8401" if old else "http://127.0.0.1:8402"}
            def read_link(self, path):
                return None
            def systemctl(self, *args, check=True):
                # Mirrors the real module: `stage` reads enablement before any
                # write, and only the exact string `disabled` is publishable.
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": ("disabled\n" if args[:1] == ("is-enabled",)
                               else "inactive\n")})()
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
        self.assertEqual(output["unit_path"],
                         "/home/operator/.config/systemd/user/serveros-fleet-example-mcp.service")
        self.assertIs(output["started"], False)
        self.assertFalse(output["v1_action"])
        self.assertEqual(output["executes"], [gate.NEXT_UNIT])
        self.assertIn("systemctl --user start", output["receipt"]["next"])

    def test_stage_does_not_repoint_a_running_existing_next(self):
        p = candidate()
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        class FakeMod(_FakeInstallModule):
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
        class FakeMod(_FakeInstallModule):
            def systemctl(self, *args, **kw):
                return type("Result", (), {"returncode": 0, "stdout": (
                    "MemoryMax=1610612736\nMemorySwapMax=0\nTasksMax=96\n"
                    "CPUQuotaPerSecUSec=400ms\n")})()
        self.assertEqual(next_install._effective_limit_errors(FakeMod()), [])

    def test_missing_live_swap_cap_is_not_reported_as_healthy(self):
        class FakeMod(_FakeInstallModule):
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
        with tempfile.TemporaryDirectory() as tmp:
            p = candidate()
            root = writable_root(p, tmp)
            old_rev, new_rev = root / "rev-old", root / "rev-bad"

            class MockModule(_FakeInstallModule):
                def __init__(self):
                    self.links = {"current": old_rev, "previous": None}
                    self.calls = []

                def binding(self, path):
                    if str(path) == p["v1"]["config_path"]:
                        return {"port": 8401, "bind": "127.0.0.1",
                                "probe": "http://127.0.0.1:8401"}
                    return {"port": 8402, "bind": "127.0.0.1",
                            "probe": "http://127.0.0.1:8402"}

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
                    # A crash MID-install: the shared installer has crossed its
                    # effect boundary, and left the evidence that says so.
                    self.begin_effect(args)
                    self.links["previous"] = old_rev
                    self.links["current"] = new_rev
                    raise SystemExit(3)

            instance = MockModule()
            with mock.patch.object(next_install, "_load_legacy_installer",
                                   return_value=instance):
                with mock.patch.object(next_install, "_current_pressure",
                                       return_value=None):
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
        class FakeMod(_FakeInstallModule):
            calls = []
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                        "probe": "http://127.0.0.1:8401" if old else "http://127.0.0.1:8402"}
            def read_link(self, path):
                return None
            def systemctl(self, *args, check=True):
                return type("Result", (), {
                    "returncode": 0,
                    "stdout": ("disabled\n" if args[:1] == ("is-enabled",)
                               else "inactive\n")})()
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
        with tempfile.TemporaryDirectory() as tmp:
            p = candidate()
            writable_root(p, tmp)

            class FakeMod(_FakeInstallModule):
                def binding(self, path):
                    old = str(path) == p["v1"]["config_path"]
                    return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                            "probe": "http://127.0.0.1:8401" if old
                            else "http://127.0.0.1:8402"}

                def read_link(self, path):
                    return None

                def systemctl(self, *args, **kw):
                    # Enablement must be a state a publish accepts; `is-active`
                    # is what the same guard reads first.
                    return type("Result", (), {
                        "returncode": 0,
                        "stdout": ("disabled\n" if args[:1] == ("is-enabled",)
                                   else "inactive\n")})()

                def main(self, args):
                    # The unit did not answer AFTER the install: past the effect
                    # boundary, so the marker has to be there or the wrapper
                    # would report a refusal instead of a failed start.
                    self.begin_effect(args)
                    print(legacy_full_receipt(started=False, health=None))
                    raise SystemExit(3)

            with mock.patch.object(next_install, "_load_legacy_installer",
                                   return_value=FakeMod()):
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
        class FakeMod(_FakeInstallModule):
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


def _git_repo() -> tempfile.TemporaryDirectory:
    """A real repository, because `install.py::git_revision` reads real state."""
    temp = tempfile.TemporaryDirectory(prefix="fleet-install-rev-")
    root = Path(temp.name)
    def run(*a):
        return subprocess.run(["git", "-C", str(root), *a],
                              capture_output=True, text=True, check=True)
    run("init", "-q")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    (root / "fleet_mcp").mkdir()
    (root / "fleet_mcp" / "marker.py").write_text("x = 1\n", encoding="utf-8")
    run("add", "-A")
    run("commit", "-q", "-m", "base")
    return temp


def _head(repo: str) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()


def _publish(command, p, *, active="inactive", enabled="disabled",
             enabled_error=False, extra=(), accept=False, head=None, record=None,
             tree_problem=None):
    """Run a non-starting publish against a unit whose state we choose.

    `accept=False` makes the installer unreachable, so a test asserting a
    refusal also proves nothing reached the installer.
    """
    class FakeMod(_FakeInstallModule):
        def binding(self, path):
            old = str(path) == p["v1"]["config_path"]
            return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                    "probe": "http://127.0.0.1:8401" if old
                    else "http://127.0.0.1:8402"}
        def read_link(self, path):
            return None
        def wait_for_health(self, *a, **k):
            return {"ok": True}
        def git_revision(self, source):
            return head
        def tree_state(self, source):
            return tree_problem
        def systemctl(self, *args, check=True):
            if record is not None:
                record.append(args)
            if args[:1] == ("is-active",):
                return type("Result", (), {
                    "returncode": 0, "stdout": active + "\n"})()
            if args[:1] == ("is-enabled",):
                if enabled_error:
                    raise RuntimeError("systemctl is-enabled could not be read")
                return type("Result", (), {
                    "returncode": 0, "stdout": enabled + "\n"})()
            return type("Result", (), {"returncode": 0, "stdout": ""})()
        def main(self, args):
            if not accept:
                raise AssertionError("nothing may reach the installer")
            print(legacy_stage_receipt())
            return 0
    with mock.patch.object(next_install, "_load_legacy_installer",
                           return_value=FakeMod()):
        return cli([command, "--execute", "--owner-approved", *extra], p)


class ANonStartingPublishNeedsAUnitThatCannotStartItselfTest(unittest.TestCase):
    """`Linger` is on: a unit that can start itself runs the new bytes later.

    The empty revision root proves only that nothing is SERVING from this tree.
    It proves nothing about the unit, and `stage` and `install --no-start` used
    to read no unit state at all. Two distinct hazards follow, and both are
    refused:

    * a unit left ENABLED by an earlier `install` is started by the user manager
      at boot/login, so the published revision runs with no activation approval;
    * a unit in `activating (auto-restart)` re-evaluates `ExecStart` on every
      attempt (the template sets `Restart=on-failure`), so it can pick the
      revision up within `RestartSec` while `is-enabled` still reads `disabled`.

    A unit that does NOT EXIST YET is the first-install state and is accepted:
    `systemctl --user is-enabled` answers `not-found` for it (measured on this
    host, rc=4), which is strictly safer than `disabled` — a unit that does not
    exist cannot start at all, and refusing it would refuse the very first
    `stage`, the command that creates the unit file.
    """

    def _bootstrap(self) -> dict:
        p = candidate()
        p["next"]["public_mcp_url"] = None
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        return p

    def test_a_unit_that_does_not_exist_yet_is_still_publishable(self):
        """The first install: `is-enabled` answers `not-found` — and that is safe.

        Refusing it would refuse the very command that creates the unit file, so
        this is the regression guard for that over-reach.
        """
        rc, out = _publish("stage", self._bootstrap(),
                           enabled="not-found", accept=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "EXECUTED")

    def test_an_enabled_next_is_refused_even_with_an_empty_root(self):
        rc, out = _publish("stage", self._bootstrap(), enabled="enabled")
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertIn("disabled", out["reason"])
        self.assertIn("Linger", out["reason"])
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")

    def test_any_other_enablement_is_refused(self):
        """Fail closed: only `disabled` and `not-found` may be published to."""
        for estate in ("enabled", "static", "masked", "indirect", "generated", ""):
            with self.subTest(enablement=estate):
                rc, out = _publish("stage", self._bootstrap(), enabled=estate)
                self.assertEqual(rc, 2, out)
                self.assertIn("disabled", out["reason"])

    def test_a_unit_that_could_restart_is_refused(self):
        """`Restart=on-failure`: an auto-restarting unit picks the bytes up."""
        for estate in ("activating", "active", "failed", "deactivating"):
            with self.subTest(active=estate):
                rc, out = _publish("stage", self._bootstrap(),
                                   active=estate, enabled="disabled")
                self.assertEqual(rc, 2, out)
                self.assertIn("inactive", out["reason"])

    def test_an_unreadable_enablement_is_refused(self):
        rc, out = _publish("stage", self._bootstrap(), enabled_error=True)
        self.assertEqual(rc, 2, out)
        self.assertIn("enablement", out["reason"])

    def test_the_stage_preview_states_the_whole_precondition(self):
        """The preview is what an operator approves: it must not be looser."""
        preview = next_install.plan(self._bootstrap(), "stage")
        self.assertEqual(preview["decision"], "DRY_RUN_READY")
        requires = " ".join(preview["requires"])
        self.assertIn("INACTIVE", requires)
        self.assertIn("disabled", requires)
        self.assertIn("not installed yet", requires)
        self.assertNotIn("memory-PSI", requires)


class InstallNoStartIsAlsoANonStartingPublishTest(unittest.TestCase):
    """`install --no-start` publishes and starts nothing — the same hazard class.

    It writes the revision AND the unit file and starts nothing, so it needs the
    same precondition as `stage`. Reading no unit state here left the stated
    invariant true for two of the three verbs that publish without starting.
    """

    def test_install_no_start_refuses_an_enabled_unit(self):
        rc, out = _publish("install", candidate(), enabled="enabled",
                           extra=("--no-start",))
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertIn("disabled", out["reason"])

    def test_install_no_start_refuses_a_restarting_unit(self):
        rc, out = _publish("install", candidate(), active="activating",
                           extra=("--no-start",))
        self.assertEqual(rc, 2, out)
        self.assertIn("inactive", out["reason"])

    def test_install_no_start_is_allowed_where_no_unit_exists_yet(self):
        rc, out = _publish("install", candidate(), enabled="not-found",
                           extra=("--no-start",), accept=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "EXECUTED")

    def test_the_install_no_start_preview_states_the_precondition(self):
        preview = next_install.plan(candidate(), "install", no_start=True)
        self.assertEqual(preview["decision"], "DRY_RUN_READY")
        self.assertIn("--no-start", " ".join(preview["requires"]))

    def test_a_starting_install_is_not_gated_on_enablement(self):
        """`install` without `--no-start` STARTS, so enablement is not its gate."""
        preview = next_install.plan(candidate(), "install")
        self.assertEqual(preview["decision"], "DRY_RUN_READY")
        self.assertNotIn("DISABLED", " ".join(preview["requires"]).upper())

    def test_a_starting_install_reads_no_unit_state_at_all(self):
        """Behavioural, not just preview text: the start verb takes no unit guard.

        Asserting the preview string alone would pass for an empty `requires`.
        This runs the real path and requires that no `is-active` / `is-enabled`
        read happened — the guard belongs to the three publishing verbs only.
        """
        calls = []
        with mock.patch.object(next_install, "_current_pressure",
                               return_value=None), \
             mock.patch.object(next_install, "_effective_limit_errors",
                               return_value=[]):
            rc, out = _publish("install", candidate(), record=calls, accept=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "EXECUTED")
        reads = [c for c in calls
                 if c[:1] in (("is-active",), ("is-enabled",))]
        self.assertEqual(reads, [], "the starting verb must not read unit state")


class AMistypedRevisionTakesNoSystemActionTest(unittest.TestCase):
    """A validation refusal must be free of system action.

    The shared installer refuses a `--revision` the source's HEAD disagrees
    with, but it refuses by DYING — and this wrapper treats a dying installer as
    an attempted effect (it may have died after writing) and runs a Next-only
    stop/rollback. That is the right default for a death of unknown timing and
    the wrong answer for a mistyped SHA: nothing was written, so a healthy Next
    must not be stopped for it. The wrapper pre-checks before any effect, which
    is what keeps the refusal off that path.
    """

    def _mistyped(self, command, p, *, extra=()):
        calls = []
        with mock.patch.object(next_install, "_current_pressure",
                               return_value=None):
            rc, out = _publish(command, p, extra=("--revision", "d" * 40, *extra),
                               head="c" * 40, record=calls)
        return rc, out, calls

    def test_a_mistyped_revision_on_a_starting_install_stops_nothing(self):
        """The exact shape a typo would take: `install --execute --revision ...`."""
        rc, out, calls = self._mistyped("install", candidate())
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        self.assertEqual(out["executes"], [])
        self.assertEqual(out["writes"], [])
        self.assertIn("--revision", out["reason"])
        self.assertEqual(calls, [], "no systemctl call of any kind")

    def test_a_mistyped_revision_on_a_stage_stops_nothing(self):
        p = candidate()
        p["next"]["public_mcp_url"] = None
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        rc, out, calls = self._mistyped("stage", p)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        self.assertEqual(calls, [])

    def test_a_revision_equal_to_head_is_not_refused_by_the_pre_check(self):
        """It is a pre-check, not a second and stricter gate."""
        with mock.patch.object(next_install, "_current_pressure",
                               return_value=None):
            rc, out = _publish("install", candidate(),
                               extra=("--revision", "c" * 40, "--no-start"),
                               head="c" * 40, accept=True)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "EXECUTED")


class ExplicitRevisionMustRestateAGitHeadTest(unittest.TestCase):
    """`--revision` is the route for a NON-git source, never an override.

    The revision names the revision directory and is recorded in ARTIFACT.json,
    so on a git source it may only restate the HEAD: a caller-chosen SHA ties
    those bytes to a commit that never described them. The refusal happens
    before the binding is read, which is what the absent config below uses to
    tell the two gates apart.
    """

    ABSENT_CONFIG = "/nonexistent/fleet-binding.yaml"

    def _run(self, source: str, revision: str | None) -> str:
        argv = ["install", "--source", str(source), "--config", self.ABSENT_CONFIG,
                "--runtime-root", "/nonexistent/runtime-assets"]
        if revision is not None:
            argv += ["--revision", revision]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                install.main(argv)
        return err.getvalue()

    def test_a_differing_revision_on_a_git_source_is_refused_before_any_read(self):
        with _git_repo() as repo:
            err = self._run(repo, "d" * 40)
        self.assertIn("--revision", err)
        self.assertIn("is not the git HEAD", err)
        # Refused BEFORE the binding: a later gate would have named the file.
        self.assertNotIn("no binding at", err)

    def test_the_git_head_itself_is_accepted_and_the_next_gate_speaks(self):
        with _git_repo() as repo:
            err = self._run(repo, _head(repo))
        self.assertIn("no binding at", err)          # reached the NEXT gate
        self.assertNotIn("is not the git HEAD", err)

    def test_a_non_git_source_keeps_the_explicit_revision_route(self):
        """The escape hatch a non-git artifact needs must not be closed."""
        with tempfile.TemporaryDirectory(prefix="fleet-install-nongit-") as plain:
            err = self._run(plain, "c" * 40)
        self.assertIn("no binding at", err)
        self.assertNotIn("is not the git HEAD", err)

    def test_omitting_revision_on_a_non_git_source_still_demands_one(self):
        with tempfile.TemporaryDirectory(prefix="fleet-install-nongit-") as plain:
            err = self._run(plain, None)
        self.assertIn("pass --revision", err)


class TheLiveInstallGatesWereNotLoosenedTest(unittest.TestCase):
    """Hardening a NON-starting publish must not soften the live `install` gate."""

    def test_the_canary_install_still_demands_the_oauth_attestation(self):
        p = candidate()
        p["attestations"]["next_oauth_client_authorized"] = False
        self.assertEqual(next_install.plan(p, "install")["decision"], "BLOCKED")

    def test_the_publish_guard_did_not_leak_onto_the_install_preview(self):
        """`install` STARTS, so enablement is not its precondition."""
        preview = next_install.plan(candidate(), "install")
        self.assertEqual(preview["decision"], "DRY_RUN_READY")
        self.assertNotIn("DISABLED", " ".join(preview["requires"]))


class AGitSourceMustBeTheCommitItNamesTest(unittest.TestCase):
    """The revision NAME must equal the BYTES, not merely the HEAD string.

    `install.py` copies the working tree from the FILESYSTEM. Matching
    `--revision` to `git rev-parse HEAD` therefore proves nothing about the bytes
    on disk — including in the `--revision == HEAD` and `--revision`-omitted
    cases the revision guard cannot distinguish. A dirty tree publishes an
    artifact whose ARTIFACT.json names a revision it is not. All of these are
    refused BEFORE the binding is read, before anything is copied, and before any
    `systemctl`: the absent `--config` below is what tells the two gates apart.
    """

    ABSENT_CONFIG = "/nonexistent/fleet-binding.yaml"
    ABSENT_ROOT = "/nonexistent/runtime-assets"

    def _run(self, source, *extra):
        argv = ["install", "--source", str(source), "--config", self.ABSENT_CONFIG,
                "--runtime-root", self.ABSENT_ROOT,
                "--composition", "ingress-next", *extra]
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            with self.assertRaises(SystemExit):
                install.main(argv)
        return err.getvalue()

    # -- refused: the tree is not the commit --------------------------------

    def test_a_dirty_tracked_file_is_refused_before_any_read(self):
        with _git_repo() as repo:
            (Path(repo) / "fleet_mcp" / "marker.py").write_text(
                "x = 2\n", encoding="utf-8")
            err = self._run(repo)
        self.assertIn("1 uncommitted path", err)
        self.assertNotIn("no binding at", err)   # refused before the config read

    def test_an_untracked_file_is_refused(self):
        with _git_repo() as repo:
            (Path(repo) / "scratch.txt").write_text("wip\n", encoding="utf-8")
            err = self._run(repo)
        self.assertIn("uncommitted", err)
        self.assertNotIn("no binding at", err)

    def test_the_count_is_per_file_not_per_directory(self):
        """`--untracked-files=all`: a new directory must not collapse to one row."""
        with _git_repo() as repo:
            d = Path(repo) / "newdir"
            d.mkdir()
            (d / "a.txt").write_text("1\n", encoding="utf-8")
            (d / "b.txt").write_text("2\n", encoding="utf-8")
            err = self._run(repo)
        self.assertIn("2 uncommitted path", err)

    def test_a_held_index_lock_is_refused(self):
        with _git_repo() as repo:
            gitdir = subprocess.run(
                ["git", "-C", repo, "rev-parse", "--absolute-git-dir"],
                capture_output=True, text=True, check=True).stdout.strip()
            lock = Path(gitdir) / "index.lock"
            lock.write_text("", encoding="utf-8")
            try:
                err = self._run(repo)
            finally:
                lock.unlink()
        self.assertIn("index.lock", err)

    def test_the_lock_is_found_through_a_linked_worktree_pointer(self):
        """`.git` is a FILE in a linked worktree; a path guess would miss it."""
        with _git_repo() as repo:
            wt = tempfile.mkdtemp(prefix="fleet-install-wt-")
            subprocess.run(["git", "-C", repo, "worktree", "add", "-q",
                            wt, "-b", "side"], check=True, capture_output=True)
            try:
                self.assertTrue((Path(wt) / ".git").is_file(),
                                "a linked worktree's .git must be a pointer file")
                gitdir = subprocess.run(
                    ["git", "-C", wt, "rev-parse", "--absolute-git-dir"],
                    capture_output=True, text=True, check=True).stdout.strip()
                self.assertNotEqual(str(Path(gitdir)), str(Path(wt) / ".git"))
                lock = Path(gitdir) / "index.lock"
                lock.write_text("", encoding="utf-8")
                try:
                    err = self._run(wt)
                finally:
                    lock.unlink()
            finally:
                subprocess.run(["git", "-C", repo, "worktree", "remove",
                                "--force", wt], check=False, capture_output=True)
        self.assertIn("index.lock", err)

    # -- accepted: nothing was claimed that could be broken -----------------

    def test_ignored_build_residue_is_not_a_dirty_tree(self):
        """`.gitignore` is where build output belongs; it must not refuse a tree."""
        with _git_repo() as repo:
            (Path(repo) / ".gitignore").write_text("build/\n", encoding="utf-8")
            for a in (["add", ".gitignore"], ["commit", "-qm", "ignore"]):
                subprocess.run(["git", "-C", repo, *a], check=True,
                               capture_output=True)
            (Path(repo) / "build").mkdir()
            (Path(repo) / "build" / "out.bin").write_bytes(b"\x00" * 16)
            err = self._run(repo)
        self.assertIn("no binding at", err)          # reached the NEXT gate
        self.assertNotIn("uncommitted", err)

    def test_a_clean_git_source_passes_the_tree_check(self):
        with _git_repo() as repo:
            err = self._run(repo, "--revision", _head(repo))
        self.assertIn("no binding at", err)
        self.assertNotIn("uncommitted", err)

    def test_a_non_git_source_keeps_the_explicit_revision_route(self):
        """No HEAD means no claim to break; `--revision` stays its only route."""
        with tempfile.TemporaryDirectory(prefix="fleet-install-nongit-") as plain:
            err = self._run(plain, "--revision", "c" * 40)
        self.assertIn("no binding at", err)
        self.assertNotIn("uncommitted", err)

    # -- none after effect -------------------------------------------------

    def test_the_refusal_creates_no_runtime_tree(self):
        with _git_repo() as repo, tempfile.TemporaryDirectory() as root:
            (Path(repo) / "scratch.txt").write_text("wip\n", encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit):
                    install.main(["install", "--source", repo,
                                  "--config", self.ABSENT_CONFIG,
                                  "--runtime-root", root,
                                  "--composition", "ingress-next"])
            self.assertEqual(sorted(p.name for p in Path(root).iterdir()), [])
            self.assertIn("uncommitted", err.getvalue())


class ADirtyTreeTakesNoSystemActionTest(unittest.TestCase):
    """The wrapper pre-checks the tree for the same reason it pre-checks the SHA.

    The shared installer refuses a dirty tree by DYING, and a dying installer is
    read as an attempted effect — so without this the refusal would run a
    Next-only stop/rollback. A validation refusal must stop nothing.
    """

    PROBLEM = ("/src has 1 uncommitted path(s) (tracked edits or untracked "
               "files); the installer copies the working tree")

    def test_a_dirty_source_on_a_stage_stops_nothing(self):
        calls = []
        p = candidate()
        p["next"]["public_mcp_url"] = None
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        rc, out = _publish("stage", p, tree_problem=self.PROBLEM,
                           head="c" * 40, record=calls)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        self.assertEqual(out["executes"], [])
        self.assertIn("uncommitted", out["reason"])
        self.assertEqual(calls, [], "no systemctl call of any kind")

    def test_a_dirty_source_on_a_starting_install_stops_nothing(self):
        calls = []
        with mock.patch.object(next_install, "_current_pressure",
                               return_value=None):
            rc, out = _publish("install", candidate(), tree_problem=self.PROBLEM,
                               head="c" * 40, record=calls)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        # The reason must be the REFUSAL's, not just any blocked outcome: with
        # `accept=False` the fake raises if the installer is reached, and a
        # blocked shape is not evidence that the pre-check fired.
        self.assertIn("uncommitted", out["reason"])
        self.assertEqual(calls, [])

    def _bootstrap(self) -> dict:
        p = candidate()
        p["next"]["public_mcp_url"] = None
        p["next"]["plugin_id"] = None
        p["attestations"]["next_oauth_client_authorized"] = False
        return p

    def test_a_clean_tree_is_not_refused_by_the_pre_check(self):
        rc, out = _publish("install", candidate(),
                           extra=("--no-start",), accept=True, tree_problem=None)
        self.assertEqual(rc, 0, out)
        self.assertEqual(out["decision"], "EXECUTED")


class APreEffectRefusalDoesNotStopALiveNextTest(unittest.TestCase):
    """A validation refusal is not an outage.

    `install.py` refuses several things BEFORE it crosses its effect boundary —
    a `revision_dir` that already exists without `--force`, a payload entry that
    is missing or not executable, a git source it disagrees with. Each is a
    `die`, and the wrapper treats a dying installer as an attempted effect and
    runs a Next-only stop/rollback. That default is right for a death of unknown
    timing and wrong for a refusal that wrote nothing: it turns a mistyped retry
    into an outage of a healthy Next.

    The marker the shared installer writes at its boundary is what tells them
    apart. Neither the exit code (a `daemon-reload` can fail *after* `current`
    moved) nor the symlinks (a same-revision restart leaves them identical) can.
    """

    def _dies_at(self, p, calls, *, mark: bool):
        class FakeMod(_FakeInstallModule):
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                        "probe": "http://127.0.0.1:8401" if old
                        else "http://127.0.0.1:8402"}

            def read_link(self, path):
                if path.name == "current":
                    return Path("/rev/fleet-mcp-20261015@aa17512")
                return None

            def wait_for_health(self, *a, **k):
                return {"ok": True}

            def point(self, *a):
                raise AssertionError("a refusal must not move a pointer")

            def systemctl(self, *args, **kw):
                calls.append(args)
                return type("Result", (), {"returncode": 0,
                                           "stdout": "inactive\n"})()

            def main(self, args):
                if mark:
                    self.begin_effect(args)   # a death PAST the boundary
                raise SystemExit(2)           # ...and one BEFORE it, otherwise

        return FakeMod()

    def _run(self, *, mark: bool):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            p = candidate()
            writable_root(p, tmp)
            with mock.patch.object(next_install, "_load_legacy_installer",
                                   return_value=self._dies_at(p, calls, mark=mark)), \
                 mock.patch.object(next_install, "_current_pressure",
                                   return_value=None):
                rc, out = cli(["install", "--execute", "--owner-approved"], p)
        stops = [c for c in calls if c[:1] == ("stop",)]
        return rc, out, stops

    def test_a_refusal_before_the_boundary_leaves_a_live_next_alone(self):
        rc, out, stops = self._run(mark=False)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        self.assertEqual(out["writes"], [])
        self.assertEqual(out["executes"], [])
        self.assertIn("before any effect", out["reason"])
        self.assertEqual(stops, [], "a pre-effect refusal must not stop Next")

    def test_the_marker_is_the_only_thing_separating_the_two(self):
        """The same fake, the same SystemExit, one marker apart."""
        _, before, before_stops = self._run(mark=False)
        _, after, after_stops = self._run(mark=True)
        self.assertEqual(before["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        self.assertEqual(before_stops, [])
        self.assertEqual(after["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertEqual(after["executes"], [gate.NEXT_UNIT])
        self.assertIn("Next-only stop/rollback", after["reason"])
        self.assertEqual(after_stops, [("stop", gate.NEXT_UNIT)])


class APreEffectSystemExitStillPrintsOneReceiptTest(unittest.TestCase):
    """`install.py::binding()` refuses by dying, before the installer is driven.

    At rung 0-1 an operator runs `stage --execute` before the Next binding file
    exists. `binding()` dies with `SystemExit`, which the wrapper's
    `except Refused` does not catch — so the execute path printed NO document at
    all, and a caller parsing one document cannot tell that from a crash. The
    dry run is unaffected (it returns before the binding is read), which made the
    approved preview work while the execute died silently.

    Exercised with the REAL `install.py`, so the three refusals are its own.
    """

    def _run_stage_with(self, next_binding: str | None):
        with tempfile.TemporaryDirectory() as tmp:
            p = candidate()
            writable_root(p, tmp)
            v1_cfg = Path(tmp) / "v1-binding.yaml"
            v1_cfg.write_text("server:\n  bind: 127.0.0.1\n  port: 8401\n",
                              encoding="utf-8")
            p["v1"]["config_path"] = str(v1_cfg)
            if next_binding is not None:
                cfg = Path(tmp) / "next-binding.yaml"
                cfg.write_text(next_binding, encoding="utf-8")
                p["next"]["config_path"] = str(cfg)
            with _git_repo() as repo:
                return cli(["stage", "--execute", "--owner-approved",
                            "--source", repo], p)

    def test_every_pre_effect_binding_refusal_prints_one_receipt(self):
        cases = {
            "absent file": None,
            "invalid YAML": "server: [unclosed\n",
            "no server.port": "server:\n  bind: 127.0.0.1\n",
            "server is not a mapping": "server: hello\n",
            "server is a sequence": "server:\n  - 1\n",
        }
        for label, body in cases.items():
            with self.subTest(refusal=label):
                rc, out = self._run_stage_with(body)
                self.assertEqual(rc, 2, out)
                self.assertEqual(out["decision"], "BLOCKED")
                self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
                self.assertEqual(out["writes"], [])
                self.assertEqual(out["executes"], [])
                self.assertFalse(out["v1_action"])
                # Pins the PRE-effect branch specifically, on two axes.
                # `refusal_exit` is structural: without it the post-effect
                # branch emits the same four fields, so the branch point would
                # be unobservable — and for `rollback`/`status` the two branches
                # genuinely differ.
                self.assertEqual(out["refusal_exit"], 2)
                # ...and this is the BEHAVIOURAL half, which is the one that
                # matters: the two branches share every other field, so a swap
                # of the reason sentences would otherwise pass the whole suite
                # and tell an operator "no system action was taken" for a case
                # where the installer HAD been driven — the exact overstatement
                # this branch exists to prevent.
                self.assertIn("pre-effect", out["reason"])
                self.assertNotIn("after driving the installer", out["reason"])


class TheRunbookDoesNotSellAStopTheToolWillNotTakeTest(unittest.TestCase):
    """The remedy for a LIVE Next has to name the operator's own stop step.

    `refresh-stage` requires `is-active == inactive` and a unit that cannot start
    itself, so recommending it for a running unit recommends a command that will
    be refused — and implies the tool takes the service down on the way, which
    none of the publishing verbs ever does. An earlier revision of the note said
    exactly that, and this is the assertion that keeps it from coming back.
    """

    RUNBOOK = ROOT / "docs" / "fleet-v1-next-ingress-runbook.md"

    def test_the_live_next_remedy_names_the_operators_own_stop(self):
        text = self.RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("*What to do.*", text)
        remedy = text.split("*What to do.*", 1)[1].split("`--revision` also gains", 1)[0]
        self.assertIn("systemctl --user stop", remedy)
        self.assertIn("systemctl --user disable", remedy)
        self.assertIn("maintenance window", remedy)
        # It must not present one publish verb as the whole remedy, and it must
        # name the right verb for an EMPTY root, where `refresh-stage` refuses.
        self.assertIn("neither publish verb is a", remedy)
        self.assertIn("`stage` when the root is empty", remedy)


class TheSharedInstallerMarksItsEffectBoundaryTest(unittest.TestCase):
    """The marker is written by the REAL `install.py`, at the real boundary.

    The wrapper tests drive fakes, so they prove the wrapper reads the marker —
    not that the shared installer writes it, and not that it writes it in the
    right PLACE. This drives `install.py::install()` itself: once with a payload
    that dies at the pre-boundary validation, once with a death past it.
    """

    def _args(self, tmp: str, *, executable: bool = True):
        src = Path(tmp) / "src"
        (src / "fleet_mcp" / "bin").mkdir(parents=True)
        entry = src / "fleet_mcp" / "bin" / "serveros-fleet-mcp"
        entry.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        entry.chmod(0o755 if executable else 0o644)
        (src / "fleet_mcp" / "deploy").mkdir(parents=True)
        (src / "fleet_mcp" / "deploy" / "serveros-fleet-mcp.service").write_text(
            "[Service]\nExecStart=@FLEET_MCP_RUNTIME@ @FLEET_MCP_CONFIG@\n",
            encoding="utf-8")
        (src / "fleet").mkdir()
        (src / "fleet" / "marker.py").write_text("x = 1\n", encoding="utf-8")
        cfg = Path(tmp) / "binding.yaml"
        cfg.write_text("server:\n  bind: 127.0.0.1\n  port: 8401\n", encoding="utf-8")
        root = Path(tmp) / "runtime"
        args = install.parser().parse_args([
            "install", "--source", str(src), "--revision", "c" * 40,
            "--config", str(cfg), "--runtime-root", str(root),
            "--composition", "ingress", "--unit-dir", str(Path(tmp) / "units"),
            "--no-start", "--label", "test-label"])
        return args, root / "ingress"

    def test_a_refusal_before_the_boundary_leaves_no_marker(self):
        """A payload entry that is not executable refuses BEFORE any publish."""
        with tempfile.TemporaryDirectory() as tmp:
            args, composition_root = self._args(tmp, executable=False)
            with self.assertRaises(SystemExit):
                install.install(args)
            self.assertFalse((composition_root / install.EFFECT_MARKER).exists())
            self.assertFalse((composition_root / "current").exists())

    def test_a_death_past_the_boundary_leaves_the_marker_behind(self):
        """`daemon-reload` fails AFTER `current` moved: recovery is required."""
        with tempfile.TemporaryDirectory() as tmp:
            args, composition_root = self._args(tmp)
            def boom(*a, check=True):
                install.die("simulated daemon-reload failure")
            with mock.patch.object(install, "systemctl", boom):
                with self.assertRaises(SystemExit):
                    install.install(args)
            self.assertTrue((composition_root / install.EFFECT_MARKER).exists(),
                            "a death past the boundary must leave the evidence")
            self.assertTrue((composition_root / "current").exists())

    def test_a_clean_no_start_run_clears_the_marker(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, composition_root = self._args(tmp)
            with mock.patch.object(install, "systemctl",
                                   lambda *a, **k: type("R", (), {"returncode": 0,
                                                                  "stdout": ""})()):
                self.assertEqual(install.install(args), 0)
            self.assertFalse((composition_root / install.EFFECT_MARKER).exists(),
                             "a clean finish must not leave a recovery marker")


class TheDirtyTreeGuardReachesV1TooAndIsRecordedTest(unittest.TestCase):
    """`install.py` is SHARED, so this refusal reaches V1 installs as well.

    That is a real change to what V1 will accept, and it is the deliberate one:
    the alternative is publishing a V1 artifact whose `ARTIFACT.json` names a
    commit its bytes do not match. It REFUSES — it does not stop, move, rewrite
    or replace anything, so a V1 that is already running is untouched, and the
    operator remedy (make the tree clean, or deploy from a clean revision tree)
    is in the refusal itself. The runbook carries the migration note, and this
    test is what keeps the behaviour from changing silently.
    """

    def test_the_shared_installer_refuses_a_dirty_git_source(self):
        with _git_repo() as repo:
            (Path(repo) / "fleet_mcp" / "marker.py").write_text("x = 2\n",
                                                               encoding="utf-8")
            args = install.parser().parse_args([
                "install", "--source", repo, "--no-start",
                "--config", "/nonexistent/binding.yaml",
                "--runtime-root", "/nonexistent/runtime"])
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit):
                    install.install(args)
        self.assertIn("uncommitted", err.getvalue())
        self.assertNotIn("no binding at", err.getvalue(),
                         "refused before the binding is read, i.e. before any "
                         "copy, pointer move or systemctl")

    def test_the_runbook_records_the_v1_side_of_this(self):
        text = (ROOT / "docs" / "fleet-v1-next-ingress-runbook.md").read_text(
            encoding="utf-8")
        self.assertIn("#### And the tree must BE that revision", text)
        note = text.split("#### And the tree must BE that revision", 1)[1]
        self.assertIn("V1", note[:6000],
                      "the note must say this reaches V1 as well")


class APlainExceptionFromTheInstallerStillReportsTest(unittest.TestCase):
    """The installer can fail with an ordinary exception, not only `die`.

    A `systemctl` missing from the inherited PATH, an `OSError` writing the
    unit, an `ENOSPC` on `os.replace` — all of those land AFTER the effect
    boundary, and all of them used to escape the wrapper as a traceback: no
    receipt for the caller, and no Next-only recovery for a run that had already
    moved `current`. The exit code does not exist to consult; the marker does.
    """

    def _raises(self, p, calls, *, mark: bool):
        class FakeMod(_FakeInstallModule):
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                        "probe": "http://127.0.0.1:8401" if old
                        else "http://127.0.0.1:8402"}

            def read_link(self, path):
                if path.name == "current":
                    return Path("/rev/fleet-mcp-20261015@aa17512")
                return None

            def wait_for_health(self, *a, **k):
                return {"ok": True}

            def point(self, *a):
                raise AssertionError("a constant pointer needs no restore")

            def systemctl(self, *args, **kw):
                calls.append(args)
                return type("Result", (), {"returncode": 0,
                                           "stdout": "inactive\n"})()

            def main(self, args):
                if mark:
                    self.begin_effect(args)
                raise OSError("systemctl: not found")

        return FakeMod()

    def _run(self, *, mark: bool):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            p = candidate()
            writable_root(p, tmp)
            with mock.patch.object(next_install, "_load_legacy_installer",
                                   return_value=self._raises(p, calls, mark=mark)), \
                 mock.patch.object(next_install, "_current_pressure",
                                   return_value=None):
                rc, out = cli(["install", "--execute", "--owner-approved"], p)
        return rc, out, [c for c in calls if c[:1] == ("stop",)]

    def test_an_oserror_before_the_boundary_stops_nothing_and_reports(self):
        rc, out, stops = self._run(mark=False)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        self.assertEqual(out["writes"], [])
        self.assertEqual(stops, [])

    def test_an_oserror_past_the_boundary_recovers_and_still_reports(self):
        rc, out, stops = self._run(mark=True)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertTrue(out["writes"])
        self.assertIn("OSError", out["reason"])
        self.assertEqual(stops, [("stop", gate.NEXT_UNIT)])


class TheBindingRefusesShapesItCannotReadTest(unittest.TestCase):
    """`install.binding()` refuses; it does not raise.

    Tested DIRECTLY, not through the wrapper: the wrapper now catches ordinary
    exceptions, so a test that drives it cannot tell a clean `die` from an
    escaping `AttributeError` — and the V1 path has no such catch. Every shape
    below used to reach `.get` on something that is not a mapping.
    """

    SHAPES = {
        "invalid YAML": "server: [unclosed\n",
        "a scalar document": "hello\n",
        "server is a scalar": "server: hello\n",
        "server is a sequence": "server:\n  - 1\n",
        "no server.port": "server:\n  bind: 127.0.0.1\n",
    }

    def _refusal(self, body: str) -> str:
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp) / "binding.yaml"
            cfg.write_text(body, encoding="utf-8")
            err = io.StringIO()
            with contextlib.redirect_stderr(err):
                with self.assertRaises(SystemExit):
                    install.binding(cfg)
        return err.getvalue()

    def test_every_unreadable_shape_is_refused_by_die(self):
        for label, body in self.SHAPES.items():
            with self.subTest(shape=label):
                self.assertIn("install.py:", self._refusal(body))


class TheTreeRuleAsksWhetherItIsARepositoryAtAllTest(unittest.TestCase):
    """A caller that gates the tree rule on a readable HEAD exempts an UNBORN one.

    `git init` with nothing committed — or `git checkout --orphan` — is a work
    tree whose bytes no commit describes: exactly the misnaming this rule exists
    to catch, reachable through the shape a HEAD-based gate skips. "Is this a
    work tree?" is what separates "makes no claim" from "makes a claim I cannot
    verify", so `tree_state` asks it first and answers it itself.
    """

    def test_a_plain_directory_makes_no_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(install.tree_state(Path(tmp)))

    def test_a_repository_with_an_unborn_head_is_still_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "-C", str(root), "init", "-q"], check=True)
            (root / "fleet_mcp").mkdir()
            (root / "fleet_mcp" / "marker.py").write_text("x = 1\n",
                                                          encoding="utf-8")
            self.assertIsNone(install.git_revision(root),
                              "an unborn HEAD reads as no revision at all")
            problem = install.tree_state(root)
            self.assertIsNotNone(problem, "an unborn HEAD must not be exempt")
            self.assertIn("uncommitted", problem)

    def test_a_clean_repository_passes(self):
        with _git_repo() as repo:
            self.assertIsNone(install.tree_state(Path(repo)))

    def test_a_dirty_repository_is_refused(self):
        with _git_repo() as repo:
            (Path(repo) / "fleet_mcp" / "marker.py").write_text("x = 2\n",
                                                                encoding="utf-8")
            self.assertIn("uncommitted", install.tree_state(Path(repo)))

    def test_a_git_entry_git_cannot_read_is_refused_not_exempted(self):
        """A `.git` FILE whose `gitdir:` target is gone reports "not a repository".

        That answer is indistinguishable from a plain directory by exit code
        alone (rc 128 either way), and the bytes such a tree holds certainly
        match no commit — so the `.git` ENTRY is what decides, and it decides
        refuse rather than exempt.
        """
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").write_text("gitdir: /nonexistent/gone\n",
                                       encoding="utf-8")
            (root / "fleet_mcp").mkdir()
            problem = install.tree_state(root)
            self.assertIsNotNone(problem, "a broken .git must not be exempt")
            self.assertIn(".git entry", problem)


class AFailureAfterACompletedInstallStillPrintsOneDocumentTest(unittest.TestCase):
    """The wrapper's own post-start verification runs AFTER a successful start.

    `_effective_limit_errors` and the V1 health readback sit outside the inner
    try, just past `effect_started`. An `OSError` there — a `systemctl` missing
    from the wrapper's PATH, say — used to be re-raised, so an install that had
    actually SUCCEEDED left the caller with no document at all.
    """

    def _started(self, p, calls):
        class FakeMod(_FakeInstallModule):
            def binding(self, path):
                old = str(path) == p["v1"]["config_path"]
                return {"port": 8401 if old else 8402, "bind": "127.0.0.1",
                        "probe": "http://127.0.0.1:8401" if old
                        else "http://127.0.0.1:8402"}

            def read_link(self, path):
                return None

            def wait_for_health(self, *a, **k):
                return {"ok": True}

            def systemctl(self, *args, **kw):
                calls.append(args)
                return type("Result", (), {"returncode": 0,
                                           "stdout": "inactive\n"})()

            def main(self, args):
                print(legacy_full_receipt())
                return 0

        return FakeMod()

    def test_a_raising_limit_readback_still_reports(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp:
            p = candidate()
            writable_root(p, tmp)
            with mock.patch.object(next_install, "_load_legacy_installer",
                                   return_value=self._started(p, calls)), \
                 mock.patch.object(next_install, "_current_pressure",
                                   return_value=None), \
                 mock.patch.object(next_install, "_effective_limit_errors",
                                   side_effect=OSError("systemctl: not found")):
                rc, out = cli(["install", "--execute", "--owner-approved"], p)
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NEXT_ONLY_ACTION_ATTEMPTED")
        self.assertTrue(out["writes"])
        self.assertIn("OSError", out["reason"])
        # The post-effect sentence, not the pre-effect one: this failure
        # followed a COMPLETED install, so "no system action was taken" would be
        # false in the direction that matters.
        self.assertIn("after driving the installer", out["reason"])
        self.assertNotIn("pre-effect", out["reason"])


class ABadHealthTimeoutStillPrintsOneDocumentTest(unittest.TestCase):
    """Our own bound check belongs inside the one-document contract.

    `p.error` prints usage and exits with no JSON at all, so a mistyped
    `--health-timeout` left a caller with nothing to parse. `argparse`'s own
    errors (an unknown flag, a missing `--proposal`) stay usage-level: they are
    about the shape of the invocation, not about the host or the run.
    """

    def test_the_bound_check_reports_instead_of_printing_usage(self):
        rc, out = cli(["stage", "--health-timeout", "0"], candidate())
        self.assertEqual(rc, 2, out)
        self.assertEqual(out["decision"], "BLOCKED")
        self.assertEqual(out["effect_state"], "NO_SYSTEM_ACTION_STARTED")
        self.assertEqual(out["writes"], [])
        self.assertIn("health-timeout", out["reason"])


class ThePayloadAuditRefusesIgnoredMembersTest(unittest.TestCase):
    """A clean `git status` does not mean every COPIED byte is tracked.

    `git status` never reports ignored files, and `copytree` copies them — so a
    `.gitignore`d secret under `fleet_mcp/` shipped inside the artifact while
    `tree_state()` called the tree clean, and `ARTIFACT.json` named a revision
    that never described it. The tree that matters is the one that was COPIED,
    so the audit asks about `staging`; that is also what keeps ordinary build
    residue out of the rule, because `IGNORE` drops it before it gets there.
    """

    SERVICE = "[Service]\nExecStart=@FLEET_MCP_RUNTIME@ @FLEET_MCP_CONFIG@\n"

    def _git_source(self, tmp: str, *, ignore: str, fixture: str | None):
        src = Path(tmp) / "src"
        (src / "fleet_mcp" / "bin").mkdir(parents=True)
        entry = src / "fleet_mcp" / "bin" / "serveros-fleet-mcp"
        entry.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        entry.chmod(0o755)
        (src / "fleet_mcp" / "deploy").mkdir(parents=True)
        (src / "fleet_mcp" / "deploy" / "serveros-fleet-mcp.service").write_text(
            self.SERVICE, encoding="utf-8")
        (src / "fleet").mkdir()
        (src / "fleet" / "marker.py").write_text("x = 1\n", encoding="utf-8")
        (src / ".gitignore").write_text(ignore, encoding="utf-8")

        def run(*a):
            return subprocess.run(["git", "-C", str(src), *a],
                                  capture_output=True, text=True, check=True)
        run("init", "-q")
        run("config", "user.email", "t@example.invalid")
        run("config", "user.name", "t")
        run("add", "-A")
        run("commit", "-q", "-m", "base")
        if fixture is not None:
            path = src / fixture
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("HARmlessFixtureOnly\n", encoding="utf-8")

        cfg = Path(tmp) / "binding.yaml"
        cfg.write_text("server:\n  bind: 127.0.0.1\n  port: 8401\n", encoding="utf-8")
        root = Path(tmp) / "runtime"
        args = install.parser().parse_args([
            "install", "--source", str(src), "--config", str(cfg),
            "--runtime-root", str(root), "--composition", "ingress",
            "--unit-dir", str(Path(tmp) / "units"), "--no-start", "--label", "l"])
        return args, root / "ingress", src

    @staticmethod
    def _quiet_systemctl(*a, **k):
        return type("R", (), {"returncode": 0, "stdout": ""})()

    def test_an_ignored_member_under_the_payload_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, comp, _ = self._git_source(
                tmp, ignore="fleet_mcp/*.secret\n",
                fixture="fleet_mcp/owner-pat.secret")
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit):
                install.install(args)
            self.assertIn("not tracked by the repository", err.getvalue())
            # Nothing was published, and the ignored member is not left behind
            # in a staging directory either.
            self.assertFalse((comp / "current").exists())
            self.assertEqual(list(comp.glob(".staging-*")), [])
            self.assertFalse((comp / install.EFFECT_MARKER).exists())

    def test_ordinary_ignored_build_residue_is_not_refused(self):
        """`IGNORE` already drops `__pycache__`, so it must not be a refusal."""
        with tempfile.TemporaryDirectory() as tmp:
            args, comp, _ = self._git_source(
                tmp, ignore="__pycache__/\n*.pyc\n",
                fixture="fleet_mcp/__pycache__/marker.cpython-312.pyc")
            with mock.patch.object(install, "systemctl", self._quiet_systemctl):
                self.assertEqual(install.install(args), 0)
            self.assertTrue((comp / "current").exists())

    def test_a_clean_git_source_is_not_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            args, comp, _ = self._git_source(tmp, ignore="*.log\n", fixture=None)
            with mock.patch.object(install, "systemctl", self._quiet_systemctl):
                self.assertEqual(install.install(args), 0)
            self.assertTrue((comp / "current").exists())

    def test_a_non_git_source_makes_no_claim(self):
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain"
            plain.mkdir()
            staging = Path(tmp) / "staging"
            staging.mkdir()
            self.assertIsNone(install.payload_audit(plain, staging))

    def test_an_unreadable_payload_listing_is_refused(self):
        """Fail closed: a `git ls-files` that cannot answer is not a pass."""
        with tempfile.TemporaryDirectory() as tmp:
            args, _, src = self._git_source(tmp, ignore="*.log\n", fixture=None)
            staging = Path(tmp) / "staging"
            staging.mkdir()
            (staging / "fleet_mcp").mkdir()
            (staging / "fleet_mcp" / "x.py").write_text("x\n", encoding="utf-8")
            with mock.patch.object(install.subprocess, "run",
                                   side_effect=OSError("git: not found")):
                self.assertIn("refusing to publish blind",
                              install.payload_audit(src, staging))


if __name__ == "__main__":
    unittest.main()
