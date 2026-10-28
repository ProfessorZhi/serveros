#!/usr/bin/env python3
"""Cross-process tests for the Fleet Next Agent Monitor body.

Every negative case below starts a **new Python interpreter** running the real
one-shot CLI (`python -m fleet_next_agent_monitor`) against a real durable state
file and a real, file-backed notifier binding. Nothing is patched in-process: a
monitor that only behaves when its internals are mocked has not been tested.

One thing is deliberately *not* real, and it is named rather than hidden: the
notifier factory handed to `--notifier-entry` is a journal writer, because
#423's `MilestoneNotifier` is not on `main` yet. What these tests therefore prove
is the monitor's own decision — *whether* and *how often* a fact reaches the
hand-over boundary. They do not prove Feishu dedupe; that is #423's ledger and
its own PR's claim. `test_no_notifier_binding_blocks_delivery` pins the honest
alternative: with no binding there is a named `BLOCKED_LEDGER`, never a
substitute sender.

Run:  python3 -m unittest discover -s tests \\
          -p 'test_fleet_next_agent_monitor_core.py' -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = REPO / "integrations" / "fleet-next-agent-monitor"
TESTS_DIR = Path(__file__).resolve().parent

SHA_GREEN = "a" * 40
SHA_OTHER = "b" * 40


# --------------------------------------------------------------------------- #
# notifier bindings, resolved by the SUBPROCESS through `--notifier-entry`
# --------------------------------------------------------------------------- #
class _JournalNotifier:
    """Appends one line per hand-over. A real file, so two processes share it."""

    def __init__(self, path: str) -> None:
        self.path = path

    def observe_payload(self, source, payload):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"source": source, "payload": payload},
                                    sort_keys=True) + "\n")
        return {"state": "DELIVERED", "delivered": True, "remote_id": f"om_{source}"}


class _RaisingNotifier:
    """Writes the message, then fails: a crash *inside* the send."""

    def __init__(self, path: str) -> None:
        self.path = path

    def observe_payload(self, source, payload):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"source": source, "payload": payload},
                                    sort_keys=True) + "\n")
        raise ConnectionResetError("the send died mid-flight")


class _UnknownNotifier:
    """A provider that answers without a receipt: not delivered, not refused."""

    def observe_payload(self, source, payload):  # noqa: ARG002
        return {"state": "DELIVERY_UNKNOWN", "delivered": False, "remote_id": None}


def journal_factory(config=None):  # noqa: ARG001 - the binding takes a mapping
    return _JournalNotifier(os.environ["FNAM_TEST_JOURNAL"])


def raising_factory(config=None):  # noqa: ARG001
    return _RaisingNotifier(os.environ["FNAM_TEST_JOURNAL"])


def unknown_factory(config=None):  # noqa: ARG001
    return _UnknownNotifier()


def no_entry(*_args, **_kwargs):  # never called; the module has no such factory
    raise AssertionError("unused")


# --------------------------------------------------------------------------- #
# harness
# --------------------------------------------------------------------------- #
class MonitorProcessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.state = self.dir / "state.json"
        self.journal = self.dir / "journal.jsonl"
        self.config = self.dir / "config.json"
        self.facts = self.dir / "facts.json"

    # -- fixtures ----------------------------------------------------------

    def write(self, path: Path, document) -> Path:
        path.write_text(json.dumps(document, indent=2), encoding="utf-8")
        return path

    def facts_document(self, *, runs=None, ci=None, boot_id="boot-1"):
        return {
            "fleet_instance_id": "legacy-01",
            "route_id": "route-a",
            "boot_id": boot_id,
            "provider": "cloudcli",
            "runs": runs or {},
            "ci": ci or {},
        }

    def config_document(self, watches, *, views=("run", "ci"), sessions=("ses-1",),
                        devices=("dev-1",), projects=("proj",)):
        return {
            "schema": "serveros/fleet-next-agent-monitor-config.v1",
            "grant": {"principal": "owner", "devices": list(devices),
                      "projects": list(projects), "sessions": list(sessions),
                      "views": list(views)},
            "watches": watches,
        }

    # -- the one door ------------------------------------------------------

    def run_cli(self, *, notifier="journal_factory", extra_args=(),
                config=None, facts=None):
        if config is not None:
            self.write(self.config, config)
        if facts is not None:
            self.write(self.facts, facts)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([
            str(PACKAGE_PARENT), str(TESTS_DIR)])
        env["FNAM_TEST_JOURNAL"] = str(self.journal)
        entry = (f"{__name__}:{notifier}" if notifier else None)
        argv = [sys.executable, "-m", "fleet_next_agent_monitor",
                "--config", str(self.config), "--state", str(self.state),
                "--offline-facts", str(self.facts), *extra_args]
        if entry:
            argv += ["--notifier-entry", entry]
        completed = subprocess.run(argv, cwd=str(self.dir), env=env,
                                   capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode,
                         self._exit_from(completed),
                         f"the CLI's exit code and its document disagree: "
                         f"{completed.stderr}")
        return completed

    @staticmethod
    def _exit_from(completed: subprocess.CompletedProcess) -> int:
        try:
            document = json.loads(completed.stdout)
        except ValueError:
            return -1
        return int(document.get("exit", -1))

    def document(self, completed) -> dict:
        self.assertNotEqual(completed.stdout.strip(), "",
                            f"the CLI printed nothing; stderr={completed.stderr}")
        return json.loads(completed.stdout)

    def journal_rows(self) -> list[dict]:
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in
                self.journal.read_text(encoding="utf-8").splitlines() if line.strip()]

    def run_watch(self, **overrides):
        watch = {"kind": "run", "device_ref": "dev-1", "session_ref": "ses-1",
                 "run_id": "run-1", "expected_boot_id": "boot-1", "major": True}
        watch.update(overrides)
        return watch


# --------------------------------------------------------------------------- #
# the negative matrix — each case is its own interpreter
# --------------------------------------------------------------------------- #
class NoFalseSuccessTest(MonitorProcessTest):
    """`completed` is not success, and the monitor must not be able to say it is."""

    def test_no_native_activity_is_never_verified(self):
        """A completed run with no assistant message and no bound artifact."""
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs={"run-1": {
                "instanceId": "legacy-01", "deviceRef": "dev-1",
                "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                "status": "completed"}}))
        tick = self.document(completed)
        self.assertEqual(tick["counts"], {"UNKNOWN": 1})
        self.assertNotIn("TERMINAL_VERIFIED", json.dumps(tick["counts"]))
        self.assertEqual(tick["watches"][0]["reason"], "NO_NATIVE_ACTIVITY")
        self.assertEqual(self.journal_rows(), [],
                         "an UNKNOWN must never reach the notifier")
        self.assertEqual(completed.returncode, 0)

    def test_device_note_vetoes_a_message(self):
        """The device itself says there was no readable message: not evidence."""
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs={"run-1": {
                "instanceId": "legacy-01", "deviceRef": "dev-1",
                "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                "status": "completed",
                "lastAssistantMessage": {"content": "ignored"},
                "note": "completed with no readable assistant message"}}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["reason"], "NO_NATIVE_ACTIVITY")
        self.assertEqual(self.journal_rows(), [])

    def test_native_activity_is_verified_and_delivered_once(self):
        """The positive control: without it the negatives above prove nothing."""
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs={"run-1": {
                "instanceId": "legacy-01", "deviceRef": "dev-1",
                "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                "status": "completed",
                "lastAssistantMessage": {"content": "real output"}}}))
        tick = self.document(completed)
        self.assertEqual(tick["counts"], {"TERMINAL_VERIFIED": 1})
        self.assertEqual(tick["delivered"], 1)
        rows = self.journal_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "cloudcli")
        self.assertEqual(rows[0]["payload"]["run"]["status"], "completed")


class FencingTest(MonitorProcessTest):
    def test_boot_mismatch_fails_closed(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch(expected_boot_id="boot-2")]),
            facts=self.facts_document(runs={"run-1": {
                "instanceId": "legacy-01", "deviceRef": "dev-1",
                "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                "status": "completed",
                "lastAssistantMessage": {"content": "output from another boot"}}}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["reason"], "BOOT_MISMATCH")
        self.assertEqual(self.journal_rows(), [])

    def test_lost_run_fails_closed(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs={}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["status"], "UNKNOWN")
        self.assertEqual(tick["watches"][0]["reason"], "NEVER_ISSUED")
        self.assertEqual(self.journal_rows(), [])

    def test_expired_run_is_distinguished_from_never_issued(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs={"run-1": {"raise": "EXPIRED"}}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["reason"], "EXPIRED")
        self.assertEqual(self.journal_rows(), [])

    def test_network_interruption_fails_closed(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs={"run-1": {"raise": "NETWORK"}}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["status"], "OFFLINE")
        self.assertEqual(tick["watches"][0]["reason"], "ROUTE_OFFLINE")
        self.assertEqual(self.journal_rows(), [])


class CiTest(MonitorProcessTest):
    def ci_watch(self, sha=SHA_GREEN):
        return {"kind": "ci", "project": "proj", "expected_source_sha": sha,
                "major": True}

    def test_ci_draft_fails_closed(self):
        completed = self.run_cli(
            config=self.config_document([self.ci_watch()]),
            facts=self.facts_document(ci={SHA_GREEN: {
                "status": "completed", "conclusion": "success", "draft": True,
                "head_sha": SHA_GREEN, "id": 11, "name": "ci"}}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["reason"], "DRAFT_CI")
        self.assertEqual(self.journal_rows(), [])

    def test_ci_wrong_sha_fails_closed(self):
        completed = self.run_cli(
            config=self.config_document([self.ci_watch()]),
            facts=self.facts_document(ci={SHA_GREEN: {
                "status": "completed", "conclusion": "success",
                "head_sha": SHA_OTHER, "id": 12, "name": "ci"}}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["reason"], "SHA_MISMATCH")
        self.assertEqual(self.journal_rows(), [])

    def test_ci_green_on_the_exact_sha_is_delivered(self):
        completed = self.run_cli(
            config=self.config_document([self.ci_watch()]),
            facts=self.facts_document(ci={SHA_GREEN: {
                "status": "completed", "conclusion": "success",
                "head_sha": SHA_GREEN, "id": 13, "name": "ci"}}))
        tick = self.document(completed)
        self.assertEqual(tick["counts"], {"TERMINAL_VERIFIED": 1})
        rows = self.journal_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "github_actions")
        self.assertEqual(rows[0]["payload"]["workflow_run"]["head_sha"], SHA_GREEN)


class ProcessBoundaryTest(MonitorProcessTest):
    def green_run(self):
        return self.facts_document(runs={"run-1": {
            "instanceId": "legacy-01", "deviceRef": "dev-1", "sessionRef": "ses-1",
            "runId": "run-1", "bootId": "boot-1", "status": "completed",
            "lastAssistantMessage": {"content": "output"}}})

    def test_duplicate_run_across_processes_delivers_once(self):
        config = self.config_document([self.run_watch()])
        first = self.run_cli(config=config, facts=self.green_run())
        self.assertEqual(self.document(first)["delivered"], 1)
        second = self.run_cli()
        tick = self.document(second)
        self.assertEqual(tick["dispatched"], 0, "a second process re-dispatched")
        self.assertEqual(tick["duplicate"], 1)
        self.assertEqual(len(self.journal_rows()), 1,
                         "the same fact produced two messages")

    def test_crash_after_send_is_not_resent(self):
        """A crash inside the send leaves UNKNOWN, never permission to resend."""
        config = self.config_document([self.run_watch()])
        first = self.run_cli(notifier="raising_factory", config=config,
                            facts=self.green_run())
        tick = self.document(first)
        self.assertEqual(len(self.journal_rows()), 1)
        self.assertEqual(tick["unknown_receipts"], 1)
        self.assertEqual(first.returncode, 4, "an UNKNOWN receipt is UNCONFIRMED")
        second = self.run_cli()
        again = self.document(second)
        self.assertEqual(again["dispatched"], 0)
        self.assertEqual(len(self.journal_rows()), 1,
                         "a crashed send was retried as a blind second message")

    def test_provider_without_a_receipt_is_unconfirmed(self):
        config = self.config_document([self.run_watch()])
        completed = self.run_cli(notifier="unknown_factory", config=config,
                                 facts=self.green_run())
        tick = self.document(completed)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["unknown_receipts"], 1)
        self.assertEqual(completed.returncode, 4)

    def test_second_writer_refuses_while_the_lock_is_held(self):
        config = self.config_document([self.run_watch()])
        self.write(self.config, config)
        self.write(self.facts, self.green_run())
        lock_holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl,os,sys,time\n"
             f"fd=os.open({str(self.state) + '.lock'!r}, os.O_RDWR|os.O_CREAT, 0o600)\n"
             "fcntl.flock(fd, fcntl.LOCK_EX)\n"
             "sys.stdout.write('locked\\n'); sys.stdout.flush()\n"
             "time.sleep(30)\n"],
            cwd=str(self.dir), stdout=subprocess.PIPE, text=True)
        # LIFO: kill, then reap, then close the pipe — an unreaped child would
        # outlive the test and a closing pipe would hide it.
        self.addCleanup(lock_holder.stdout.close)
        self.addCleanup(lock_holder.wait)
        self.addCleanup(lock_holder.kill)
        self.assertEqual(lock_holder.stdout.readline().strip(), "locked")
        # The holder must still be alive: `readline` on a newline-less write
        # returns at EOF, i.e. only after the holder exited and dropped the lock.
        self.assertIsNone(lock_holder.poll(),
                          "the lock holder exited before the second writer ran")
        completed = self.run_cli()
        tick = self.document(completed)
        self.assertEqual(tick["state"], "REFUSED")
        self.assertEqual(tick["why"], "SECOND_WRITER")
        self.assertEqual(completed.returncode, 2)
        self.assertEqual(self.journal_rows(), [],
                         "a refused writer still delivered")

    def test_restart_records_the_epoch_it_recovered_from(self):
        config = self.config_document([self.run_watch()])
        self.run_cli(config=config, facts=self.facts_document(runs={}))
        second = self.run_cli()
        tick = self.document(second)
        self.assertIsNotNone(tick["recovered_from"],
                             "a restart did not record the epoch it took over")
        self.assertNotEqual(tick["recovered_from"], tick["boot_epoch"])

    def test_no_notifier_binding_blocks_delivery(self):
        """With no ledger there is a named blocker, not a substitute sender."""
        completed = self.run_cli(notifier=None,
                                 config=self.config_document([self.run_watch()]),
                                 facts=self.green_run())
        tick = self.document(completed)
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_LEDGER")
        self.assertTrue(any(w["code"] == "BLOCKED_LEDGER" for w in tick["warnings"]))
        self.assertEqual(self.journal_rows(), [])


# --------------------------------------------------------------------------- #
# the #488 contract: a ZERO-ARGUMENT tick, in a separate interpreter
# --------------------------------------------------------------------------- #
class ActivationEntryTest(MonitorProcessTest):
    def test_zero_argument_tick_returns_the_wrapper_shape(self):
        config = self.config_document([self.run_watch()])
        self.write(self.config, config)
        self.write(self.facts, self.facts_document(runs={"run-1": {
            "instanceId": "legacy-01", "deviceRef": "dev-1", "sessionRef": "ses-1",
            "runId": "run-1", "bootId": "boot-1", "status": "completed",
            "lastAssistantMessage": {"content": "output"}}}))
        env = dict(os.environ)
        env.update({
            "PYTHONPATH": os.pathsep.join([str(PACKAGE_PARENT), str(TESTS_DIR)]),
            "FNAM_TEST_JOURNAL": str(self.journal),
            "FLEET_NEXT_AGENT_MONITOR_CONFIG": str(self.config),
            "FLEET_NEXT_AGENT_MONITOR_STATE": str(self.state),
            "FLEET_NEXT_AGENT_MONITOR_OFFLINE_FACTS": str(self.facts),
            "FLEET_NEXT_AGENT_MONITOR_NOTIFIER": f"{__name__}:journal_factory",
        })
        completed = subprocess.run(
            [sys.executable, "-c",
             "import json; from fleet_next_agent_monitor.runtime import tick; "
             "row = tick(); "
             "json.dump({k: row[k] for k in ('state','backlog','unknown_receipts',"
             "'delivered')}, __import__('sys').stdout)"],
            cwd=str(self.dir), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        row = json.loads(completed.stdout)
        # #488 reads exactly these two names off a tick's return value.
        self.assertIn("backlog", row)
        self.assertIn("unknown_receipts", row)
        self.assertEqual(row["delivered"], 1)

    def test_zero_argument_tick_without_configuration_is_a_named_block(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([str(PACKAGE_PARENT), str(TESTS_DIR)])
        for name in ("FLEET_NEXT_AGENT_MONITOR_CONFIG", "FLEET_NEXT_AGENT_MONITOR_STATE"):
            env.pop(name, None)
        completed = subprocess.run(
            [sys.executable, "-c",
             "import json; from fleet_next_agent_monitor.runtime import tick; "
             "print(json.dumps(tick()))"],
            cwd=str(self.dir), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        row = json.loads(completed.stdout)
        self.assertEqual(row["state"], "BLOCKED_INPUT")
        self.assertEqual(row["why"], "BLOCKED_RUNTIME_ENTRY")


# --------------------------------------------------------------------------- #
# properties that are cheapest to prove in-process, next to the code
# --------------------------------------------------------------------------- #
class AuthorizationTest(unittest.TestCase):
    """Reads happen only after the grant says so — and a refusal costs zero reads."""

    def _source(self, tmpdir, *, grant_sessions=("ses-1",), grant_devices=("dev-1",)):
        sys.path.insert(0, str(PACKAGE_PARENT))
        self.addCleanup(lambda: sys.path.remove(str(PACKAGE_PARENT)))
        from fleet_next_agent_monitor.offline import OfflineFactsClient
        from fleet_next_agent_monitor.sources import Grant, LegacyFactsSource
        self._Grant = Grant
        self._Source = LegacyFactsSource
        client = OfflineFactsClient({
            "fleet_instance_id": "legacy-01", "route_id": "route-a",
            "boot_id": "boot-1", "runs": {}, "ci": {}})
        grant = Grant(principal="owner", devices=grant_devices, projects=("proj",),
                      sessions=grant_sessions, views=("run", "ci"))
        return client, LegacyFactsSource(client, grant)

    def test_denied_session_reads_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, source = self._source(tmp, grant_sessions=("other",))
            observation = source.observe_run(device_ref="dev-1", session_ref="ses-1",
                                             run_id="run-1")
            self.assertEqual(observation["status"], "DENIED")
            self.assertEqual(observation["reason"], "NOT_GRANTED")
            self.assertEqual(client.reads, 0,
                             "a denied watch still called the provider")

    def test_wildcard_grant_is_refused(self):
        with tempfile.TemporaryDirectory() as tmp:  # noqa: F841
            sys.path.insert(0, str(PACKAGE_PARENT))
            self.addCleanup(lambda: sys.path.remove(str(PACKAGE_PARENT)))
            from fleet_next_agent_monitor.contract import MonitorRefused
            from fleet_next_agent_monitor.sources import Grant
            with self.assertRaises(MonitorRefused):
                Grant(principal="owner", devices=("*",), views=("run",))

    def test_read_budget_is_bounded(self):
        with tempfile.TemporaryDirectory() as tmp:
            client, source = self._source(tmp)
            capped = self._Source(client, self._Grant(
                principal="owner", devices=("dev-1",), projects=("proj",),
                sessions=("ses-1",), views=("run",)), max_readings=1)
            capped.observe_run(device_ref="dev-1", session_ref="ses-1", run_id="run-1")
            second = capped.observe_run(device_ref="dev-1", session_ref="ses-1",
                                        run_id="run-1")
            self.assertEqual(second["reason"], "READINGS_CAPPED")
            self.assertEqual(client.reads, 1)


class SurfaceTest(unittest.TestCase):
    """The read-only boundary, asserted on the source rather than promised."""

    def package_root(self) -> Path:
        return PACKAGE_PARENT / "fleet_next_agent_monitor"

    def modules(self) -> list[Path]:
        """The files under test, with the empty-directory case made loud.

        Without this, a `glob` over a missing package yields nothing, the loop
        body never runs, and the test passes on a tree where the code does not
        exist at all — the vacuous green the rest of this file exists to avoid.
        """
        found = sorted(self.package_root().glob("*.py"))
        self.assertTrue(found,
                        f"no modules under {self.package_root()}; a scan over "
                        f"nothing proves nothing")
        return found

    def test_no_network_client_is_imported(self):
        forbidden = ("import requests", "import urllib", "import http.client",
                     "import httpx", "import socket", "import subprocess",
                     "import systemctl", "systemctl")
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, text,
                                 f"{path.name} carries {needle!r}; the monitor does "
                                 f"not reach the network or start processes")

    def test_no_write_verb_and_no_credential(self):
        forbidden = ("send_text", "create_session", "delete_session", "app_secret",
                     "tenant_access_token", "webhook")
        for path in self.modules():
            text = path.read_text(encoding="utf-8")
            for needle in forbidden:
                self.assertNotIn(needle, text,
                                 f"{path.name} carries {needle!r}; the monitor owns "
                                 f"no credential and no write verb")

    def test_vocabulary_has_no_word_for_done(self):
        sys.path.insert(0, str(PACKAGE_PARENT))
        self.addCleanup(lambda: sys.path.remove(str(PACKAGE_PARENT)))
        from fleet_next_agent_monitor import contract
        for word in ("DONE", "SUCCESS", "VERIFIED", "COMPLETED"):
            self.assertNotIn(word, contract.STATUSES,
                             f"{word!r} is a status; it must be expressible only as "
                             f"TERMINAL_VERIFIED with native activity behind it")
        self.assertNotIn("TERMINAL_VERIFIED", contract.NOT_SUCCESS)


if __name__ == "__main__":
    unittest.main()
