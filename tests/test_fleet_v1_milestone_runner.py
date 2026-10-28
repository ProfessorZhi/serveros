#!/usr/bin/env python3
"""#417/#424/#434 — the bounded runner entry, offline.

WHAT THIS PROVES
----------------
The #428 observer and #423 notifier are assembled into ONE process entry with:

* an authorised config (watches, one ledger, one state, one lock, bounds);
* one injected provider factory and one injected SourceRead;
* a bounded ``run_periodic`` lifecycle, a stop request (SIGTERM on POSIX), and a
  single-active lock that refuses a second runner before its factory is called;
* a send-rate limit that records DELIVERY_UNKNOWN, never a false DELIVERED;
* source outages recorded as UNKNOWN and retried, not reported as success;
* an unexpected runner-level exception recorded as RUNNER_ERROR, not DELIVERED.

The subprocess tests use the fixture module `tests/fixtures/fleet_v1_milestone_runner/fakes.py`
through PYTHONPATH. No network, CLI, credential, service or real Feishu send.

EVIDENCE LABELS
---------------
CODE + offline fake end-to-end are this file. HOSTED_CI is the Draft PR.
SERVICE_NOT_INSTALLED is the service state: systemd / Windows Task Scheduler is
a README proposal only. REAL_FEISHU_DELIVERY, REAL_DEVICE and OAuth are NOT_RUN.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
RUNNER_PATH = (REPO / "integrations" / "fleet-v1-milestone-runner"
               / "runner.py")
FAKES_PATH = (REPO / "tests" / "fixtures" / "fleet_v1_milestone_runner"
              / "fakes.py")
FAKES_DIR = FAKES_PATH.parent
OBSERVER_FIXTURES = (REPO / "integrations" / "fleet-v1-milestone-observer"
                     / "fixtures")
RUNNER_NAME = "serveros_fleet_v1_milestone_runner_under_test"
FAKES_NAME = "serveros_fleet_v1_milestone_runner_fakes"
DEVICE = "device:tokyo-shared-dev-01"
BOOT = "boot-A"
RUN = "run_demo_1"


def _load(name: str, path: Path):
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


runner = _load(RUNNER_NAME, RUNNER_PATH)
fakes = _load(FAKES_NAME, FAKES_PATH)
O = runner._OBSERVER


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-runner-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.log = self.tmp / "provider.jsonl"
        self.ledger = self.tmp / "ledger.sqlite"
        self.state = self.tmp / "state.json"
        self.lock = self.tmp / "runner.lock"

    # -- config -----------------------------------------------------------
    def config(self, *, sequence, watches=None, max_ticks=2, max_runtime=None,
               interval=0.0, max_sends=30, summarizer_raise=False,
               ledger=None, state=None, lock=None, options=None):
        if watches is None:
            watches = [{"source": "cloudcli", "ref": RUN, "device_ref": DEVICE,
                        "boot_id": BOOT}]
        return runner.RunnerConfig.from_dict({
            "schema": runner.SCHEMA,
            "ledger_path": str(ledger or self.ledger),
            "state_path": str(state or self.state),
            "lock_path": str(lock or self.lock),
            "conversation_ref": "conversation:ops",
            "watches": watches,
            "interval_seconds": interval,
            "max_ticks": max_ticks,
            "max_runtime_seconds": max_runtime,
            "max_sends_per_minute": max_sends,
            "options": options or {"base_backoff_seconds": 0,
                                   "max_backoff_seconds": 0},
            "provider_options": {"log": str(self.log)},
            "source_options": {"sequence": sequence,
                               "fixtures": str(OBSERVER_FIXTURES)},
            "summarizer_options": {"raise": True} if summarizer_raise else {},
        })

    def make(self, config, **kwargs):
        kwargs.setdefault("summarizer_factory",
                          fakes.summarizer if config.summarizer_options else None)
        return runner.MilestoneRunner(
            config, provider_factory=fakes.provider,
            source_factory=fakes.source, **kwargs)

    # -- readback ---------------------------------------------------------
    def lines(self):
        if not self.log.exists():
            return []
        return [json.loads(line) for line in
                self.log.read_text(encoding="utf-8").splitlines() if line.strip()]

    def ledger_row(self, key):
        if not self.ledger.exists():
            return None
        db = sqlite3.connect(self.ledger)
        try:
            db.row_factory = sqlite3.Row
            found = db.execute(
                "SELECT * FROM milestone_deliveries WHERE delivery_key=?",
                (key,)).fetchone()
            return dict(found) if found else None
        finally:
            db.close()

    def wait_for(self, predicate, timeout=5.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.02)
        return False


class GreenOutageAndRed(Base):
    def test_green_once_then_restart_does_not_resend(self):
        sequence = [{"payload": "running"}, {"payload": "completed"}]
        first = self.make(self.config(sequence=sequence))
        result = first.run()
        self.assertEqual(result["state"], runner.BOUNDED_STOP)
        self.assertEqual(result["service_state"], runner.SERVICE_STATE)
        lines = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("VERIFIED_GREEN", lines[0]["text"])
        self.assertRegex(lines[0]["idempotency_key"], r"^ms1:[0-9a-f]{46}$")
        self.assertEqual(len(lines[0]["idempotency_key"]), 50)

        full = first.last["rows"][0]["delivery_key"]
        self.assertTrue(full.startswith("milestone:cloudcli:"))
        self.assertGreater(len(full), 50)
        self.assertEqual(lines[0]["idempotency_key"],
                         runner._NOTIFY.transport_key_for(full))
        row = self.ledger_row(full)
        self.assertEqual(row["state"], "DELIVERED")
        self.assertEqual(row["delivery_key"], full)
        self.assertEqual(row["transport_key"], lines[0]["idempotency_key"])

        restarted = self.make(self.config(sequence=[{"payload": "completed"}],
                                          max_ticks=1))
        again = restarted.run()
        self.assertEqual(again["state"], runner.BOUNDED_STOP)
        self.assertEqual(len(self.lines()), 1, "a restart must not resend")

    def test_source_outage_is_unknown_then_one_green(self):
        sequence = [{"raise": "SOURCE_UNAVAILABLE"},
                    {"payload": "completed"}]
        instance = self.make(self.config(sequence=sequence, max_ticks=2))
        result = instance.run()
        self.assertEqual(result["state"], runner.BOUNDED_STOP)
        self.assertEqual(len(self.lines()), 1)
        first_row = instance.ticks[0]["rows"][0]
        self.assertFalse(first_row["delivered"])
        self.assertNotEqual(first_row["state"], "DELIVERED")
        self.assertEqual(first_row["outcome"], "UNKNOWN")
        self.assertTrue(instance.ticks[1]["rows"][0]["delivered"])

    def test_red_survives_a_failing_summarizer(self):
        instance = self.make(self.config(sequence=[{"payload": "failed"}],
                                         max_ticks=1, summarizer_raise=True))
        result = instance.run()
        self.assertEqual(result["state"], runner.BOUNDED_STOP)
        self.assertEqual(len(self.lines()), 1)
        text = self.lines()[0]["text"]
        self.assertIn("RUN_FAILED", text)
        self.assertIn("Action required", text)
        self.assertNotIn("fake summarizer unavailable", text)


class OneActiveAndSharedLedger(Base):
    def test_second_runner_same_lock_refuses_before_the_factory(self):
        calls = []

        def provider_factory(options):
            calls.append(1)
            return fakes.provider(options)

        config = self.config(sequence=[{"payload": "running"}],
                             max_ticks=100, max_runtime=30, interval=0.1)
        first = runner.MilestoneRunner(
            config, provider_factory=provider_factory,
            source_factory=fakes.source)
        first.start()
        second = runner.MilestoneRunner(
            config, provider_factory=provider_factory,
            source_factory=fakes.source)
        with self.assertRaises(runner.RunnerStartRefused) as caught:
            second.start()
        self.assertEqual(caught.exception.code, "ANOTHER_RUNNER_ACTIVE")
        self.assertEqual(len(calls), 1, "the refused runner never called its factory")
        first.stop()

    def test_two_runners_with_distinct_locks_share_one_ledger(self):
        first = self.make(self.config(
            sequence=[{"payload": "completed"}], max_ticks=1))
        self.assertEqual(first.run()["state"], runner.BOUNDED_STOP)
        self.assertEqual(len(self.lines()), 1)
        second = self.make(self.config(
            sequence=[{"payload": "completed"}], max_ticks=1,
            state=self.tmp / "state-2.json", lock=self.tmp / "runner-2.lock"))
        self.assertEqual(second.run()["state"], runner.BOUNDED_STOP)
        self.assertEqual(len(self.lines()), 1,
                         "the shared ledger answers DUPLICATE")

    def test_send_rate_limit_is_unknown_not_delivered(self):
        watches = [
            {"source": "cloudcli", "ref": "run_demo_1", "device_ref": DEVICE,
             "boot_id": BOOT},
            {"source": "cloudcli", "ref": "run_demo_2", "device_ref": DEVICE,
             "boot_id": BOOT},
        ]
        instance = self.make(self.config(
            sequence=[{"payload": "completed"}, {"payload": "completed"}],
            watches=watches, max_ticks=1, max_sends=1))
        result = instance.run()
        self.assertEqual(result["state"], runner.BOUNDED_STOP)
        self.assertEqual(len(self.lines()), 1, "the second send was rate-limited")
        rows = instance.last["rows"]
        self.assertEqual(sum(1 for row in rows if row["delivered"]), 1)
        self.assertEqual(
            sum(1 for row in rows if row["state"] == O.DELIVERY_UNKNOWN), 1,
            "a rate-limited send is UNKNOWN, never DELIVERED")

    def test_unexpected_runner_exception_is_error_not_delivered(self):
        instance = self.make(self.config(
            sequence=[{"payload": "completed"}], max_ticks=1))
        instance.start()

        class Boom:
            @staticmethod
            def run_periodic(**_kwargs):
                def generator():
                    raise RuntimeError("runner loop exploded")
                    yield  # pragma: no cover
                return generator()

        instance.observer = Boom()
        result = instance.run()
        self.assertEqual(result["state"], runner.ERROR)
        self.assertIn("RuntimeError", result["error"])
        self.assertEqual(self.lines(), [], "an error is never a delivery")


class ConfigRefusals(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-runner-config-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = self.tmp / "ledger.sqlite"
        self.state = self.tmp / "state.json"
        self.lock = self.tmp / "runner.lock"

    def valid(self, **changes):
        data = {
            "schema": runner.SCHEMA,
            "ledger_path": str(self.ledger),
            "state_path": str(self.state),
            "lock_path": str(self.lock),
            "conversation_ref": "conversation:ops",
            "watches": [{"source": "cloudcli", "ref": RUN,
                         "device_ref": DEVICE, "boot_id": BOOT}],
            "interval_seconds": 0,
            "max_ticks": 1,
            "max_runtime_seconds": None,
            "max_sends_per_minute": 30,
            "options": {},
            "provider_options": {},
            "source_options": {},
            "summarizer_options": {},
        }
        data.update(changes)
        return data

    def refused(self, **changes):
        with self.assertRaises(runner.RunnerConfigError):
            runner.RunnerConfig.from_dict(self.valid(**changes))

    def test_unbounded_lifecycle_is_refused(self):
        with self.assertRaises(runner.RunnerConfigError) as caught:
            runner.RunnerConfig.from_dict(
                self.valid(max_ticks=None, max_runtime_seconds=None))
        self.assertEqual(caught.exception.code, "UNBOUNDED_LIFECYCLE")

    def test_relative_or_symlinked_paths_are_refused(self):
        self.refused(ledger_path="ledger.sqlite")
        link = self.tmp / "link.sqlite"
        link.symlink_to(self.ledger)
        self.refused(ledger_path=str(link))

    def test_unknown_fields_and_bad_bounds_are_refused(self):
        self.refused(extra=1)
        self.refused(max_ticks=0)
        self.refused(max_ticks=runner.MAX_TICKS_HARD + 1)
        self.refused(max_sends_per_minute=-1)
        self.refused(options={"not_an_option": True})
        self.refused(watches=[
            {"source": "cloudcli", "ref": RUN, "device_ref": DEVICE,
             "boot_id": BOOT},
            {"source": "cloudcli", "ref": RUN, "device_ref": DEVICE,
             "boot_id": BOOT},
        ])


class ServiceAndImports(Base):
    def test_service_is_not_installed_and_no_service_client_is_imported(self):
        self.assertEqual(runner.SERVICE_STATE, "SERVICE_NOT_INSTALLED")
        source = RUNNER_PATH.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(
            imported & {"subprocess", "socket", "requests", "urllib", "httpx",
                        "aiohttp", "systemd"},
            set())
        for marker in ("systemctl", "schtasks", "os.environ", "getenv"):
            self.assertNotIn(marker, source)


class OfflineSubprocess(Base):
    def subprocess_config(self, data, name):
        path = self.tmp / f"config-{name}.json"
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    def run_subprocess(self, data, *, name, summarizer=False, timeout=20):
        config = self.subprocess_config(data, name)
        env = os.environ.copy()
        env["PYTHONPATH"] = str(FAKES_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        command = [sys.executable, str(RUNNER_PATH), "--config", str(config),
                   "--provider-factory", "fakes:provider",
                   "--source-factory", "fakes:source"]
        if summarizer:
            command += ["--summarizer-factory", "fakes:summarizer"]
        return subprocess.run(command, cwd=str(REPO), env=env, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              timeout=timeout)

    def green_config(self, name, *, summarizer_raise=False, sequence=None):
        return {
            "schema": runner.SCHEMA,
            "ledger_path": str(self.ledger),
            "state_path": str(self.state),
            "lock_path": str(self.lock),
            "conversation_ref": "conversation:ops",
            "watches": [{"source": "cloudcli", "ref": RUN,
                         "device_ref": DEVICE, "boot_id": BOOT}],
            "interval_seconds": 0,
            "max_ticks": 2,
            "max_runtime_seconds": None,
            "max_sends_per_minute": 30,
            "options": {"base_backoff_seconds": 0, "max_backoff_seconds": 0},
            "provider_options": {"log": str(self.log)},
            "source_options": {"sequence": sequence or [
                {"payload": "running"}, {"payload": "completed"}],
                "fixtures": str(OBSERVER_FIXTURES)},
            "summarizer_options": {"raise": True} if summarizer_raise else {},
        }

    def test_subprocess_green_then_restart(self):
        first = self.run_subprocess(self.green_config("first"), name="first")
        self.assertEqual(first.returncode, 0, first.stderr)
        parsed = json.loads(first.stdout)
        self.assertEqual(parsed["state"], runner.BOUNDED_STOP)
        self.assertEqual(parsed["service_state"], runner.SERVICE_STATE)
        lines = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("VERIFIED_GREEN", lines[0]["text"])
        self.assertRegex(lines[0]["idempotency_key"], r"^ms1:[0-9a-f]{46}$")
        key = lines[0]["idempotency_key"]
        db = sqlite3.connect(self.ledger)
        try:
            db.row_factory = sqlite3.Row
            rows = [dict(row) for row in
                    db.execute("SELECT * FROM milestone_deliveries")]
        finally:
            db.close()
        self.assertEqual(len(rows), 1)
        full = rows[0]["delivery_key"]
        self.assertTrue(full.startswith("milestone:cloudcli:"))
        self.assertGreater(len(full), 50)
        self.assertEqual(rows[0]["transport_key"], key)
        self.assertEqual(rows[0]["state"], "DELIVERED")

        again = self.run_subprocess(
            self.green_config("restart", sequence=[{"payload": "completed"}]),
            name="restart")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertEqual(len(self.lines()), 1, "a restart must not resend")

    def test_subprocess_red_summarizer_failure(self):
        config = self.green_config(
            "red", summarizer_raise=True, sequence=[{"payload": "failed"}])
        config["max_ticks"] = 1
        proc = self.run_subprocess(config, name="red", summarizer=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        parsed = json.loads(proc.stdout)
        self.assertEqual(parsed["state"], runner.BOUNDED_STOP)
        lines = self.lines()
        self.assertEqual(len(lines), 1)
        self.assertIn("RUN_FAILED", lines[0]["text"])
        self.assertIn("Action required", lines[0]["text"])

    def test_subprocess_sigterm_stops_and_second_lock_is_refused(self):
        config = self.green_config(
            "sigterm", sequence=[{"payload": "running"}])
        config["max_ticks"] = 1000
        config["interval_seconds"] = 0.2
        config["max_runtime_seconds"] = 30
        path = self.subprocess_config(config, "sigterm")
        env = os.environ.copy()
        env["PYTHONPATH"] = str(FAKES_DIR) + os.pathsep + env.get("PYTHONPATH", "")
        command = [sys.executable, str(RUNNER_PATH), "--config", str(path),
                   "--provider-factory", "fakes:provider",
                   "--source-factory", "fakes:source"]
        first = subprocess.Popen(command, cwd=str(REPO), env=env, text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.addCleanup(lambda: first.poll() is None and first.kill())
        self.assertTrue(self.wait_for(self.lock.exists),
                        "the first runner never took its lock")
        second = subprocess.run(command, cwd=str(REPO), env=env, text=True,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                timeout=15)
        self.assertEqual(second.returncode, 2, second.stderr)
        refused = json.loads(second.stdout)
        self.assertEqual(refused["state"], runner.START_REFUSED)
        self.assertEqual(refused["code"], "ANOTHER_RUNNER_ACTIVE")
        self.assertEqual(self.lines(), [])

        first.send_signal(signal.SIGTERM)
        out, err = first.communicate(timeout=15)
        self.assertEqual(first.returncode, 0, err)
        stopped = json.loads(out)
        self.assertEqual(stopped["state"], runner.STOPPED)
        self.assertEqual(stopped["service_state"], runner.SERVICE_STATE)
        self.assertEqual(self.lines(), [])


if __name__ == "__main__":
    unittest.main()
