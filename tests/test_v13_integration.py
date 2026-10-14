#!/usr/bin/env python3
"""The integration candidate: cross-process locking, the real-sender seam, the
sample journal, and the managed CLI's three modes.

EVERY test here is offline. The two lock tests spawn REAL operating-system
processes, because «two writers cannot both hold it» is a claim about the kernel,
not about a flag in one interpreter. Nothing contacts Feishu.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

MODULE_DIR = ROOT / "runtime"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


surface_lock = _load("serveros_runtime_surface_lock", MODULE_DIR / "surface_lock.py")
live_sender = _load("serveros_runtime_live_sender", MODULE_DIR / "live_sender.py")
journal_mod = _load("serveros_runtime_snapshot_journal",
                    MODULE_DIR / "snapshot_journal.py")
surface_cli = _load("serveros_runtime_surface_cli", MODULE_DIR / "surface_cli.py")
surface = _load("serveros_runtime_card_surface", MODULE_DIR / "card_surface.py")
runner_mod = _load("serveros_runtime_card_surface_runner",
                   MODULE_DIR / "card_surface_runner.py")

AT = "2026-10-14T17:30:00+08:00"


def _test_dir(prefix):
    """A test-owned directory under this user's `.cache`.

    `dir=` makes the stdlib create it; the context manager removes EXACTLY what
    it created. Nothing here composes a path it then deletes by hand, so there
    is no expression a later edit can loosen into «remove the shared root».
    """
    cache = Path(os.path.expanduser("~")) / ".cache"
    cache.mkdir(parents=True, exist_ok=True)
    return tempfile.TemporaryDirectory(prefix=prefix, dir=cache)

_HOLD = (
    "import importlib.util,sys,time\n"
    "spec=importlib.util.spec_from_file_location('l', sys.argv[1])\n"
    "m=importlib.util.module_from_spec(spec); sys.modules['l']=m; spec.loader.exec_module(m)\n"
    "lock=m.SurfaceLock(sys.argv[2])\n"
    "assert lock.acquire(), 'holder could not acquire'\n"
    "print('HELD', flush=True)\n"
    "time.sleep(float(sys.argv[3]))\n"
    "lock.release()\n"
)
_CRASH = (
    "import importlib.util,sys,os\n"
    "spec=importlib.util.spec_from_file_location('l', sys.argv[1])\n"
    "m=importlib.util.module_from_spec(spec); sys.modules['l']=m; spec.loader.exec_module(m)\n"
    "lock=m.SurfaceLock(sys.argv[2])\n"
    "assert lock.acquire(), 'crasher could not acquire'\n"
    "print('HELD', flush=True)\n"
    "os._exit(7)\n"          # die WITHOUT releasing
)


class TheCrossProcessLock(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.lockfile = Path(self.tmp.name) / "cards" / "locks" / "live.lock"
        self.module = str(MODULE_DIR / "surface_lock.py")

    def test_a_second_os_process_is_told_busy(self):
        holder = subprocess.Popen(
            [sys.executable, "-c", _HOLD, self.module, str(self.lockfile), "3"],
            stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.wait)
        self.assertEqual(holder.stdout.readline().strip(), "HELD")
        lock = surface_lock.SurfaceLock(self.lockfile)
        self.assertFalse(lock.acquire())              # BUSY, not an exception
        verdict = lock.busy_verdict(surface="LIVE", at=AT)
        self.assertEqual(verdict["reason"], "BUSY")
        self.assertEqual(verdict["action"], "SKIP")
        holder.wait(timeout=20)
        self.assertTrue(surface_lock.SurfaceLock(self.lockfile).acquire())

    def test_a_crashed_holder_releases_the_lock(self):
        crashed = subprocess.run(
            [sys.executable, "-c", _CRASH, self.module, str(self.lockfile)],
            capture_output=True, text=True, timeout=60, check=False)
        self.assertEqual(crashed.returncode, 7)       # it really died holding it
        # the KERNEL released it: no stale lock to reap, no pid guessing
        lock = surface_lock.SurfaceLock(self.lockfile)
        self.assertTrue(lock.acquire())
        lock.release()

    def test_the_lock_file_is_owner_private(self):
        lock = surface_lock.SurfaceLock(self.lockfile)
        self.assertTrue(lock.acquire())
        mode = os.stat(self.lockfile).st_mode & 0o777
        self.assertEqual(mode, 0o600)
        lock.release()


class TheRealSenderSeam(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "alignment.env"
        self.config.write_text(
            "# 收件人是本机 ops 通知早就在用的同一个绑定\n"
            "cli_aa08005233f91bcd ou_8fe4ed0b562dc89a883d43f0778061e5\n",
            encoding="utf-8")
        self.credentials = Path(self.tmp.name) / "lark-cli"
        self.credentials.mkdir()
        (self.credentials / "cli_aa08005233f91bcd_ou_8fe4ed0b562dc89a883d43f0778061e5.enc"
         ).write_text("x", encoding="utf-8")

    def test_it_is_off_until_explicitly_enabled(self):
        with self.assertRaises(live_sender.LiveSenderDisabled):
            live_sender.LiveSender()

    def test_the_recipient_comes_from_the_cross_checked_local_binding(self):
        recipient = live_sender.owner_recipient(config_path=self.config,
                                                credential_dir=self.credentials)
        self.assertEqual(recipient[:5], "user:")
        self.assertIn("ou_", recipient)

    def test_a_declared_app_that_disagrees_with_the_credential_is_refused(self):
        self.config.write_text("cli_aa0fffffffffff ou_8fe4ed0b562dc89a883d43f0778061e5\n",
                               encoding="utf-8")
        with self.assertRaises(live_sender.LiveSenderError):
            live_sender.owner_recipient(config_path=self.config,
                                        credential_dir=self.credentials)

    def test_a_bare_or_chat_recipient_is_refused(self):
        for bad in ("ou_bare", "chat:oc_x", ""):
            with self.subTest(bad=bad):
                with self.assertRaises(live_sender.LiveSenderError):
                    live_sender.LiveSender(enabled=True, recipient=bad,
                                           binding={"recipient": "user:ou_owner"})

    def test_a_delivered_without_a_message_id_is_not_a_confirmed_identity(self):
        answer = live_sender.LiveSender._classify(
            {"state": "DELIVERED", "provider": {"remote_id": None}})
        self.assertEqual(answer["state"], live_sender.DELIVERED_UNVERIFIED)
        self.assertEqual(answer["evidence"], "remote")

    def test_a_readback_of_a_non_message_is_refused(self):
        sender = live_sender.LiveSender(enabled=True, recipient="user:ou_x",
                                        binding={"recipient": "user:ou_x"})
        with self.assertRaises(live_sender.LiveSenderError):
            sender.readback("oc_conversation")

    def test_an_unreadable_or_legacy_readback_is_unknown_not_ok(self):
        sender = live_sender.LiveSender(enabled=True, recipient="user:ou_x",
                                        binding={"recipient": "user:ou_x"})
        sender.cli = "/bin/echo"                     # emits nothing usable

        class _Done:
            stdout = ""
        real_run = subprocess.run
        try:
            subprocess.run = lambda *a, **k: subprocess.CompletedProcess(a, 0, "", "")
            result = sender.readback("om_x")
        finally:
            subprocess.run = real_run
        self.assertFalse(result["available"])
        self.assertIsNone(result["card"])


class TheSampleJournal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.journal = journal_mod.Journal(Path(self.tmp.name) / "cards" / "samples.json")

    def _reading(self, label, value, state="KNOWN"):
        return {"label": label, "value": value, "state": state}

    def test_a_sample_carries_typed_axes_and_provenance(self):
        sample = journal_mod.sample_from_readings(
            [self._reading("内存", "32.6%（80.4 GiB / 247.0 GiB）"),
             self._reading("负载", "17.87 / 28.05 / 43.4"),
             self._reading("WiFi 型号", "AX201", state="UNKNOWN")],
            at=AT, provenance="read-only local sample")
        self.assertEqual(sample["readings"]["mem_percent"], 32.6)
        self.assertEqual(sample["readings"]["cpu_load1"], 17.87)
        self.assertIsNone(sample["readings"]["swap_percent"])   # absent, not zero
        self.assertEqual(sample["provenance"], "read-only local sample")

    def test_a_replayed_slot_replaces_itself_rather_than_inflating_coverage(self):
        for minute in ("17:30", "17:31"):
            sample = journal_mod.sample_from_readings(
                [self._reading("内存", "30%")], at=f"2026-10-14T{minute}:00+08:00",
                provenance="p")
            self.journal.append(sample)
        self.assertEqual(len(self.journal.read()["samples"]), 1)

    def test_the_journal_is_bounded(self):
        for index in range(journal_mod.CAP + 20):
            minute = index % 60
            hour = 10 + (index // 60)
            sample = {"at": f"2026-10-14T{hour:02d}:{minute:02d}:00+08:00",
                      "slot": hour * 100 + minute, "provenance": "p",
                      "readings": {"mem_percent": 30.0}}
            self.journal.append(sample)
        self.assertLessEqual(len(self.journal.read()["samples"]), journal_mod.CAP)

    def test_a_single_point_is_an_unknown_trend_and_carries_its_coverage(self):
        self.journal.append({"at": "2026-10-14T11:00:00+08:00",
                             "slot": journal_mod._slot("2026-10-14T11:00:00+08:00"),
                             "provenance": "p", "readings": {"mem_percent": 30.0}})
        summary = self.journal.summary(start="2026-10-14T11:00:00+08:00",
                                       end="2026-10-14T12:00:00+08:00")
        self.assertEqual(summary["samples"], 1)
        self.assertEqual(summary["trend"], "UNKNOWN")           # one point, no curve
        self.assertLess(summary["coverage"], 1.0)               # and it says so
        self.assertGreater(summary["gaps"], 0)

    def test_mean_peak_and_peak_time_come_from_real_points(self):
        for hour in (11, 12):
            self.journal.append({"at": f"2026-10-14T{hour}:00:00+08:00",
                                 "slot": journal_mod._slot(f"2026-10-14T{hour}:00:00+08:00"),
                                 "provenance": "p", "readings": {"mem_percent": hour}})
        summary = self.journal.summary(start="2026-10-14T11:00:00+08:00",
                                       end="2026-10-14T13:00:00+08:00")
        axis = summary["axes"]["mem_percent"]
        self.assertEqual(axis["peak"], 12.0)
        self.assertEqual(axis["mean"], 11.5)
        self.assertTrue(axis["peak_at"])


class TheLoadGate(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def _files(self, *, load, swap_total, swap_free):
        loadavg = Path(self.tmp.name) / "loadavg"
        loadavg.write_text(f"{load} 1.0 1.0 1/100 999\n", encoding="utf-8")
        meminfo = Path(self.tmp.name) / "meminfo"
        meminfo.write_text(f"MemTotal: 1 kB\nSwapTotal: {swap_total} kB\n"
                           f"SwapFree: {swap_free} kB\n", encoding="utf-8")
        return loadavg, meminfo

    def test_a_loaded_host_is_refused_with_its_numbers(self):
        loadavg, meminfo = self._files(load=400.0, swap_total=1000, swap_free=900)
        gate = surface_cli.load_gate(loadavg_path=loadavg, meminfo_path=meminfo)
        self.assertFalse(gate["ok"])
        self.assertIn("load", gate["why"])

    def test_a_swapping_host_is_refused(self):
        loadavg, meminfo = self._files(load=1.0, swap_total=1000, swap_free=1)
        gate = surface_cli.load_gate(loadavg_path=loadavg, meminfo_path=meminfo)
        self.assertFalse(gate["ok"])
        self.assertIn("swap", gate["why"])

    def test_a_calm_host_passes(self):
        loadavg, meminfo = self._files(load=1.0, swap_total=1000, swap_free=900)
        self.assertTrue(surface_cli.load_gate(loadavg_path=loadavg,
                                              meminfo_path=meminfo)["ok"])

    def test_an_unreadable_host_is_treated_as_unsafe(self):
        gate = surface_cli.load_gate(loadavg_path="/nonexistent",
                                     meminfo_path="/nonexistent")
        self.assertFalse(gate["ok"])


class TheManagedCli(unittest.TestCase):
    def test_preview_touches_nothing(self):
        trace = surface_cli.mode_preview(type("A", (), {"at": AT})())
        self.assertFalse(trace["store_touched"])
        self.assertEqual(trace["transport_calls"], 0)
        self.assertEqual(trace["components"]["live"]["schema"], "2.0")
        self.assertFalse(trace["components"]["live"]["has_chart"])

    def test_dry_run_uses_a_sandbox_and_reports_what_it_would_send(self):
        trace = surface_cli.mode_dry_run(type("A", (), {"at": AT, "events": ""})())
        self.assertTrue(trace["sandbox"])
        self.assertEqual(trace["evidence"], "synthetic")
        self.assertGreaterEqual(trace["would_send"], 1)

    def test_canary_needs_the_explicit_flag(self):
        args = type("A", (), {"i_understand_this_sends": False})()
        with self.assertRaises(surface_cli.PreflightRefused):
            surface_cli.mode_canary(args)

    def test_canary_is_refused_by_a_dangerous_host_before_anything_is_sent(self):
        args = type("A", (), {"i_understand_this_sends": True})()
        original = surface_cli.load_gate
        surface_cli.load_gate = lambda **k: {"ok": False, "why": "injected",
                                             "load_per_cpu": 9.0, "swap_used": 0.99}
        try:
            result = surface_cli.mode_canary(args)
        finally:
            surface_cli.load_gate = original
        self.assertFalse(result["sent"])
        self.assertEqual(result["blocked"], "PREFLIGHT")

    def test_the_shipped_units_are_disabled_templates(self):
        directory = ROOT / "deploy" / "v13-surfaces"
        names = sorted(p.name for p in directory.iterdir())
        self.assertIn("v13-live-heartbeat.timer", names)
        self.assertIn("v13-report-period.timer", names)
        for name in names:
            if name.endswith((".timer", ".service")):
                text = (directory / name).read_text(encoding="utf-8")
                self.assertIn("DISABLED TEMPLATE", text)


if __name__ == "__main__":
    unittest.main()


class TheCanaryEntry(unittest.TestCase):
    """A real mode must not be reachable by accident. Each guard is asserted."""

    def _args(self, **over):
        base = {"i_understand_this_sends": True, "state": "", "at": AT,
                "events": "", "closing": ""}
        base.update(over)
        return type("A", (), base)()

    def _clean_gate(self):
        original = surface_cli.load_gate
        surface_cli.load_gate = lambda **k: {"ok": True, "why": "",
                                             "load_per_cpu": 0.1, "swap_used": 0.1}
        self.addCleanup(lambda: setattr(surface_cli, "load_gate", original))

    def test_a_real_mode_without_an_explicit_state_is_refused(self):
        with self.assertRaises(surface_cli.PreflightRefused):
            surface_cli.validated_state("")

    def test_a_relative_state_is_refused(self):
        with self.assertRaises(surface_cli.PreflightRefused):
            surface_cli.validated_state("cards/surfaces.json")

    def test_a_state_outside_this_users_home_is_refused(self):
        with self.assertRaises(surface_cli.PreflightRefused):
            surface_cli.validated_state("/tmp/somewhere-else/surfaces.json")

    def test_an_absolute_home_state_is_accepted(self):
        # `validated_state` chmods the state's PARENT, so the parent must be a
        # test-owned directory and never a shared root like `~/.cache`.
        with _test_dir("v13-state-") as work:
            candidate = Path(work) / "cards" / "surfaces.json"
            accepted = surface_cli.validated_state(str(candidate))
            self.assertTrue(accepted.is_absolute())
            self.assertEqual(accepted.parent.resolve(),
                             (Path(work) / "cards").resolve())

    def test_canary_without_state_is_refused_even_on_a_clean_host(self):
        self._clean_gate()
        with self.assertRaises(surface_cli.PreflightRefused):
            surface_cli.mode_canary(self._args(state=""))

    def test_two_real_runs_on_one_state_cannot_both_hold_it(self):
        # The directory is created BY THE STDLIB and removed BY THE STDLIB. The
        # assertions below pin what it is, so a later edit cannot make the
        # cleanup point at `.cache` itself.
        with _test_dir("v13-lock-") as work:
            parent = Path(work)
            self.assertTrue(parent.name.startswith("v13-lock-"))
            self.assertEqual(parent.parent.resolve(),
                             (Path(os.path.expanduser("~")) / ".cache").resolve())
            self.assertNotEqual(parent.resolve(), Path(os.path.expanduser("~")).resolve())
            state = str(parent / "cards" / "surfaces.json")
            held = surface_lock.SurfaceLock(
                surface_lock.lock_path(state, surface="surfaces"))
            self.assertTrue(held.acquire())
            self._clean_gate()
            try:
                result = surface_cli.mode_canary(self._args(state=state))
            finally:
                held.release()
            self.assertFalse(result["sent"])
            self.assertEqual(result["blocked"], "BUSY")

    def test_canary_still_refuses_on_a_dangerous_host(self):
        original = surface_cli.load_gate
        surface_cli.load_gate = lambda **k: {"ok": False, "why": "injected",
                                             "load_per_cpu": 9.0, "swap_used": 0.99}
        try:
            result = surface_cli.mode_canary(self._args(state="/home/x/s.json"))
        finally:
            surface_cli.load_gate = original
        self.assertEqual(result["blocked"], "PREFLIGHT")


class TheOwnerBindingIsMandatory(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.config = Path(self.tmp.name) / "alignment.env"
        self.config.write_text(
            "cli_aa08005233f91bcd ou_8fe4ed0b562dc89a883d43f0778061e5\n",
            encoding="utf-8")
        self.credentials = Path(self.tmp.name) / "lark-cli"
        self.credentials.mkdir()
        self._credential("cli_aa08005233f91bcd",
                         "ou_8fe4ed0b562dc89a883d43f0778061e5")

    def _credential(self, app, user):
        for stale in self.credentials.glob("*.enc"):
            stale.unlink()
        (self.credentials / f"{app}_{user}.enc").write_text("x", encoding="utf-8")

    def _binding(self):
        return live_sender.owner_binding(config_path=self.config,
                                         credential_dir=self.credentials)

    def test_all_three_facts_must_agree(self):
        binding = self._binding()
        self.assertEqual(binding["app_id"], "cli_aa08005233f91bcd")
        self.assertEqual(binding["recipient"],
                         "user:ou_8fe4ed0b562dc89a883d43f0778061e5")

    def test_a_config_with_no_app_is_refused(self):
        self.config.write_text("ou_8fe4ed0b562dc89a883d43f0778061e5\n", encoding="utf-8")
        with self.assertRaises(live_sender.LiveSenderError):
            self._binding()

    def test_a_config_with_no_owner_is_refused(self):
        self.config.write_text("cli_aa08005233f91bcd\n", encoding="utf-8")
        with self.assertRaises(live_sender.LiveSenderError):
            self._binding()

    def test_a_missing_credential_is_refused(self):
        for stale in self.credentials.glob("*.enc"):
            stale.unlink()
        with self.assertRaises(live_sender.LiveSenderError):
            self._binding()

    def test_a_credential_for_another_user_is_refused(self):
        self._credential("cli_aa08005233f91bcd",
                         "ou_00000000000000000000000000000000")
        with self.assertRaises(live_sender.LiveSenderError):
            self._binding()

    def test_a_credential_for_another_app_is_refused(self):
        self._credential("cli_aa0fffffffffff",
                         "ou_8fe4ed0b562dc89a883d43f0778061e5")
        with self.assertRaises(live_sender.LiveSenderError):
            self._binding()

    def test_a_caller_cannot_name_its_own_recipient(self):
        binding = self._binding()
        with self.assertRaises(live_sender.LiveSenderError):
            live_sender.LiveSender(enabled=True, recipient="user:ou_someone_else",
                                   binding=binding)
        self.assertEqual(
            live_sender.LiveSender(enabled=True, recipient=binding["recipient"],
                                   binding=binding).recipient,
            binding["recipient"])


class TheReadbackVerifiedSender(unittest.TestCase):
    CARD = {"schema": "2.0",
            "header": {"template": "blue",
                       "title": {"tag": "plain_text", "content": "ServerOS · 节点监控"}},
            "body": {"elements": [
                {"tag": "column_set", "columns": [{"tag": "column"}, {"tag": "column"}]},
                {"tag": "collapsible_panel"},
            ]}}

    class _Inner:
        def __init__(self, *, state="DELIVERED", remote_id="om_1"):
            self.state, self.remote_id = state, remote_id
            self.calls = []

        def send_card(self, card, *, idempotency_key, **_):
            self.calls.append(("send_card", idempotency_key))
            return {"state": self.state, "evidence": "remote",
                    "provider": {"remote_id": self.remote_id,
                                 "transport": "im +messages-send (interactive)"}}

        def update(self, request):
            self.calls.append(("update", request.get("message_id")))
            return {"state": self.state, "evidence": "remote",
                    "provider": {"remote_id": self.remote_id,
                                 "transport": "im messages patch"}}

    def test_a_matching_readback_is_the_only_path_to_remote_verified(self):
        verifier = live_sender.ReadbackVerifiedSender(
            self._Inner(), readback=lambda mid: {"available": True, "card": self.CARD})
        answer = verifier.send_card(self.CARD, idempotency_key="k")
        self.assertEqual(answer["state"], "DELIVERED")
        self.assertEqual(answer["evidence"], "remote_verified")

    def test_a_self_report_with_no_readback_is_unknown_pending(self):
        verifier = live_sender.ReadbackVerifiedSender(
            self._Inner(), readback=lambda mid: {"available": False,
                                                 "reason": "legacy projection"})
        answer = verifier.send_card(self.CARD, idempotency_key="k")
        self.assertEqual(answer["state"], live_sender.UNKNOWN_PENDING)
        self.assertNotEqual(answer["evidence"], "remote_verified")

    def test_a_remote_card_that_differs_is_unknown_pending(self):
        other = dict(self.CARD, header={"template": "red",
                                        "title": {"tag": "plain_text", "content": "别的卡"}})
        verifier = live_sender.ReadbackVerifiedSender(
            self._Inner(), readback=lambda mid: {"available": True, "card": other})
        answer = verifier.send_card(self.CARD, idempotency_key="k")
        self.assertEqual(answer["state"], live_sender.UNKNOWN_PENDING)
        self.assertIn("结构不一致", answer["why"])

    def test_a_receipt_naming_another_message_is_unknown_pending(self):
        verifier = live_sender.ReadbackVerifiedSender(
            self._Inner(remote_id="om_other"),
            readback=lambda mid: {"available": True, "card": self.CARD})
        answer = verifier.update({"message_id": "om_target", "card": self.CARD})
        self.assertEqual(answer["state"], live_sender.UNKNOWN_PENDING)

    def test_a_delivered_with_no_message_id_is_unverified(self):
        verifier = live_sender.ReadbackVerifiedSender(
            self._Inner(remote_id=None),
            readback=lambda mid: {"available": True, "card": self.CARD})
        answer = verifier.send_card(self.CARD, idempotency_key="k")
        self.assertEqual(answer["state"], live_sender.DELIVERED_UNVERIFIED)

    def test_a_readback_that_raises_is_unknown_pending(self):
        def explode(mid):
            raise RuntimeError("network down")
        verifier = live_sender.ReadbackVerifiedSender(self._Inner(), readback=explode)
        answer = verifier.send_card(self.CARD, idempotency_key="k")
        self.assertEqual(answer["state"], live_sender.UNKNOWN_PENDING)

    def test_a_failed_send_is_not_upgraded_by_the_wrapper(self):
        verifier = live_sender.ReadbackVerifiedSender(
            self._Inner(state="FAILED"),
            readback=lambda mid: {"available": True, "card": self.CARD})
        answer = verifier.send_card(self.CARD, idempotency_key="k")
        self.assertEqual(answer["state"], "FAILED")


class TheDeployedTemplatesAreStaticContract(unittest.TestCase):
    def setUp(self):
        self.directory = ROOT / "deploy" / "v13-surfaces"

    def _text(self, name):
        return (self.directory / name).read_text(encoding="utf-8")

    def test_every_unit_is_a_disabled_template(self):
        for name in ("v13-live-heartbeat.service", "v13-live-heartbeat.timer",
                     "v13-report-period.service", "v13-report-period.timer"):
            self.assertIn("DISABLED TEMPLATE", self._text(name))

    def test_no_exec_start_points_into_a_worktree(self):
        for name in ("v13-live-heartbeat.service", "v13-report-period.service"):
            text = self._text(name)
            self.assertNotIn("/worktrees/", text)
            self.assertIn(".local/lib/serveros-v13-surfaces", text)

    def test_the_two_services_call_distinct_modes(self):
        live, report = (self._text("v13-live-heartbeat.service"),
                        self._text("v13-report-period.service"))
        self.assertIn("--live-heartbeat", live)
        self.assertNotIn("--report-period", live)
        self.assertIn("--report-period", report)
        self.assertNotIn("--live-heartbeat", report)

    def test_every_exec_start_is_explicit_about_consent_and_state(self):
        for name in ("v13-live-heartbeat.service", "v13-report-period.service"):
            text = self._text(name)
            self.assertIn("--i-understand-this-sends", text)
            self.assertIn("--state ", text)

    def test_no_v13_unit_is_loaded_on_this_host(self):
        listing = subprocess.run(["systemctl", "--user", "list-units", "--all"],
                                 capture_output=True, text=True, timeout=60,
                                 check=False).stdout
        self.assertNotIn("v13-live-heartbeat", listing)
        self.assertNotIn("v13-report-period", listing)


class TheReportPeriodWindow(unittest.TestCase):
    def setUp(self):
        self._work = _test_dir("v13-report-")
        self.addCleanup(self._work.cleanup)
        self.state = str(Path(self._work.name) / "cards" / "surfaces.json")

    def test_a_window_that_cannot_be_read_is_unknown(self):
        args = type("A", (), {"state": self.state})()
        original = surface_cli._current_period
        surface_cli._current_period = lambda at: {"period_ref": None, "in_period": False}
        try:
            history = surface_cli._report_history(args, at=AT)
        finally:
            surface_cli._current_period = original
        self.assertFalse(history["available"])
        self.assertEqual(history["trend"], "UNKNOWN")

    def test_a_period_without_a_window_is_unknown(self):
        args = type("A", (), {"state": self.state})()
        original = surface_cli._current_period
        surface_cli._current_period = lambda at: {"period_ref": "P#x", "in_period": True}
        try:
            history = surface_cli._report_history(args, at=AT)
        finally:
            surface_cli._current_period = original
        self.assertFalse(history["available"])
