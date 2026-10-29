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

#: #498's activation wrapper. It is not on this branch (#497 stacks on #493),
#: so the wrapper integration test below skips when the file is absent and runs
#: for real in the composed tree (#497 + #498). It is never modified: the test
#: starts a subprocess of it, exactly as a supervisor would.
ACTIVATION_MODULE = (REPO / "integrations" / "fleet-next-agent-monitor-activation"
                     / "activation.py")

SHA_GREEN = "a" * 40
SHA_OTHER = "b" * 40


# --------------------------------------------------------------------------- #
# notifier bindings, resolved by the SUBPROCESS through `--notifier-entry`
# --------------------------------------------------------------------------- #
def delivery_key_for(source, payload):
    """The identity shape #423's `delivery_key_for` writes.

    `milestone:<source>:<subject>:<kind>:<fact>:<outcome>`. The monitor only
    checks the `milestone:<source>:<subject>:` prefix, because the suffix is
    #423's business and re-deriving it here would be a second copy of a
    contract this PR does not own. Every fake below therefore produces a *real*
    shape, and the fakes that break it do so deliberately.
    """
    subject = payload.get("device_ref") or "device:?"
    if source == "github_actions":
        run = payload.get("workflow_run") or {}
        return (f"milestone:{source}:{subject}:CI_PASSED:"
                f"{run.get('name')}:{run.get('id')}:VERIFIED_GREEN")
    run = payload.get("run") or {}
    return f"milestone:{source}:{subject}:RUN_COMPLETED:{run.get('runId')}:VERIFIED_GREEN"


class _JournalNotifier:
    """Appends one line per hand-over. A real file, so two processes share it."""

    def __init__(self, path: str) -> None:
        self.path = path

    def observe_payload(self, source, payload):
        with open(self.path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"source": source, "payload": payload},
                                    sort_keys=True) + "\n")
        return {"state": "DELIVERED", "delivered": True,
                "remote_id": f"om_{source}",
                "delivery_key": delivery_key_for(source, payload)}


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

    def observe_payload(self, source, payload):
        return {"state": "DELIVERY_UNKNOWN", "delivered": False, "remote_id": None,
                "delivery_key": delivery_key_for(source, payload)}


class _ForeignReceiptNotifier:
    """`DELIVERED`, but the receipt names somebody else's delivery.

    #423 hands back the key of the milestone it settled. A receipt whose key
    is not this watch's fact is evidence about a *different* delivery, however
    green it looks.
    """

    def observe_payload(self, source, payload):  # noqa: ARG002
        return {"state": "DELIVERED", "delivered": True, "remote_id": "om_elsewhere",
                "delivery_key": "milestone:cloudcli:device:someone-else:RUN_COMPLETED:"
                                "run-other:VERIFIED_GREEN"}


class _AnonymousReceiptNotifier:
    """`DELIVERED` with no identity at all: a claim with nothing behind it."""

    def observe_payload(self, source, payload):  # noqa: ARG002
        return {"state": "DELIVERED", "delivered": True, "remote_id": "om_anon"}


class _Ledger:
    """A file-backed stand-in for #423's ledger, and nothing more.

    It is deliberately NOT a second sender: it models the three answers
    `MilestoneNotifier` can give — `observe_payload` (claim, then attempt),
    `reconcile(delivery_key)` (re-ask the *same* key) and a ledger row that
    says `DELIVERED` or `DELIVERY_UNKNOWN`. The journal records every *provider
    call*, which is what a real Feishu send would cost.
    """

    def __init__(self, journal: str | Path, ledger: str | Path,
                 mode: str | Path) -> None:
        self.journal = Path(journal)
        self.ledger = Path(ledger)
        self.mode_path = Path(mode)

    def mode(self) -> str:
        try:
            return self.mode_path.read_text(encoding="utf-8").strip() or "deliver"
        except OSError:
            return "deliver"

    def rows(self) -> list[dict]:
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in
                self.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]

    def get(self, key: str) -> dict | None:
        for row in self.rows():
            if row["delivery_key"] == key:
                return row
        return None

    def put(self, row: dict) -> None:
        kept = [r for r in self.rows() if r["delivery_key"] != row["delivery_key"]]
        kept.append(row)
        self.ledger.write_text(
            "".join(json.dumps(r, sort_keys=True) + "\n" for r in kept),
            encoding="utf-8")

    def call(self, source: str, payload: dict, key: str) -> str:
        """One provider call. The only place a message could be sent twice."""
        with open(self.journal, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"source": source, "payload": payload},
                                    sort_keys=True) + "\n")
        if self.mode() == "deliver":
            remote = f"om_{len(self.rows()) + 1}"
            self.put({"delivery_key": key, "state": "DELIVERED", "remote_id": remote,
                      "source": source, "payload": payload})
            return "DELIVERED"
        self.put({"delivery_key": key, "state": "DELIVERY_UNKNOWN", "remote_id": None,
                  "source": source, "payload": payload})
        return "DELIVERY_UNKNOWN"


class _LedgerNotifier:
    """#423's notifier *shape*: `observe_payload` and `reconcile`."""

    def __init__(self, journal: str | Path, ledger: str | Path, mode: str | Path):
        self.store = _Ledger(journal, ledger, mode)

    def observe_payload(self, source, payload):
        key = delivery_key_for(source, payload)
        prior = self.store.get(key)
        if prior and prior["state"] == "DELIVERED":
            return {"state": "DUPLICATE", "delivered": True,
                    "delivery_key": key, "remote_id": prior["remote_id"]}
        state = self.store.call(source, payload, key)
        return {"state": state, "delivered": state == "DELIVERED",
                "delivery_key": key,
                "remote_id": (self.store.get(key) or {}).get("remote_id")}

    def reconcile(self, delivery_key):
        prior = self.store.get(delivery_key)
        if prior is None:
            return {"state": "MISSING", "delivered": False,
                    "delivery_key": delivery_key, "remote_id": None}
        if prior["state"] == "DELIVERED":
            return {"state": "DUPLICATE", "delivered": True,
                    "delivery_key": delivery_key, "remote_id": prior["remote_id"]}
        if self.store.mode() != "deliver":
            # The provider still has no receipt. Nothing is re-sent: the ledger
            # has authorised no second message.
            return {"state": "DELIVERY_UNKNOWN", "delivered": False,
                    "delivery_key": delivery_key, "remote_id": None}
        # The provider now answers. The ledger re-issues the SAME key, which is
        # what #423 does inside its dedupe window.
        state = self.store.call(prior["source"], prior["payload"], delivery_key)
        return {"state": state, "delivered": state == "DELIVERED",
                "delivery_key": delivery_key,
                "remote_id": (self.store.get(delivery_key) or {}).get("remote_id")}


def journal_factory(config=None):  # noqa: ARG001 - the binding takes a mapping
    return _JournalNotifier(os.environ["FNAM_TEST_JOURNAL"])


def raising_factory(config=None):  # noqa: ARG001
    return _RaisingNotifier(os.environ["FNAM_TEST_JOURNAL"])


def unknown_factory(config=None):  # noqa: ARG001
    return _UnknownNotifier()


def foreign_receipt_factory(config=None):  # noqa: ARG001
    return _ForeignReceiptNotifier()


def anonymous_receipt_factory(config=None):  # noqa: ARG001
    return _AnonymousReceiptNotifier()


def ledger_factory(config=None):  # noqa: ARG001
    return _LedgerNotifier(os.environ["FNAM_TEST_JOURNAL"],
                           os.environ["FNAM_TEST_LEDGER"],
                           os.environ["FNAM_TEST_MODE"])


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
        self.ledger = self.dir / "ledger.jsonl"
        self.mode = self.dir / "mode.txt"
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
                        devices=("device:dev-1",), projects=("proj",)):
        return {
            "schema": "serveros/fleet-next-agent-monitor-config.v1",
            "grant": {"principal": "owner", "devices": list(devices),
                      "projects": list(projects), "sessions": list(sessions),
                      "views": list(views)},
            "watches": watches,
        }

    # -- the one door ------------------------------------------------------

    def run_cli(self, *, notifier="journal_factory", extra_args=(),
                config=None, facts=None, extra_env=None):
        if config is not None:
            self.write(self.config, config)
        if facts is not None:
            self.write(self.facts, facts)
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([
            str(PACKAGE_PARENT), str(TESTS_DIR)])
        env["FNAM_TEST_JOURNAL"] = str(self.journal)
        env["FNAM_TEST_LEDGER"] = str(self.ledger)
        env["FNAM_TEST_MODE"] = str(self.mode)
        env.update(extra_env or {})
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

    def ledger_rows(self) -> list[dict]:
        if not self.ledger.exists():
            return []
        return [json.loads(line) for line in
                self.ledger.read_text(encoding="utf-8").splitlines() if line.strip()]

    def set_mode(self, mode: str) -> None:
        self.mode.write_text(mode + "\n", encoding="utf-8")

    def state_document(self) -> dict:
        return json.loads(self.state.read_text(encoding="utf-8"))

    def run_watch(self, **overrides):
        watch = {"kind": "run", "device_ref": "device:dev-1", "session_ref": "ses-1",
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
                "instanceId": "legacy-01", "deviceRef": "device:dev-1",
                "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                "status": "completed"}}))
        tick = self.document(completed)
        self.assertEqual(tick["counts"], {"UNKNOWN": 1})
        self.assertNotIn("TERMINAL_VERIFIED", json.dumps(tick["counts"]))
        self.assertEqual(tick["watches"][0]["reason"], "NO_NATIVE_ACTIVITY")
        self.assertEqual(self.journal_rows(), [],
                         "an UNKNOWN must never reach the notifier")
        # #495 Wave 16 §4.2: "the monitor could not decide" is not exit 0. This
        # assertion used to read `0`, which is the number a supervisor reads as
        # "nothing to do here" — so a permanently unobservable watch was never
        # looked at again.
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(completed.returncode, 4)

    def test_device_note_vetoes_a_message(self):
        """The device itself says there was no readable message: not evidence."""
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs={"run-1": {
                "instanceId": "legacy-01", "deviceRef": "device:dev-1",
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
                "instanceId": "legacy-01", "deviceRef": "device:dev-1",
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


class NativeTranscriptTest(MonitorProcessTest):
    """The native carrier the adapter reads: `transcript` / `messages`.

    #508 P1 measured one run fact classified two ways: the adapter's path reached
    `TERMINAL_VERIFIED`, while this monitor's closed reader refused the key
    before any classifier saw it (`UNKNOWN` / `REMOTE_ValueError`). Accepting a
    carrier closes the divergence; it must not lower the bar. Every case below
    runs the real CLI in a fresh interpreter against the real offline reader, so
    what is proven is the shipping acceptance, not a mocked one.
    """

    def fact(self, **overrides):
        fact = {"instanceId": "legacy-01", "deviceRef": "device:dev-1",
                "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                "status": "completed"}
        fact.update(overrides)
        return fact

    def observe(self, *, watch=None, **overrides):
        completed = self.run_cli(
            config=self.config_document([watch or self.run_watch()]),
            facts=self.facts_document(runs={"run-1": self.fact(**overrides)}))
        return completed, self.document(completed)

    def assert_no_evidence(self, *, reason="NO_NATIVE_ACTIVITY", watch=None, **overrides):
        completed, tick = self.observe(watch=watch, **overrides)
        self.assertEqual(tick["counts"], {"UNKNOWN": 1},
                         f"a carrier without assistant text was counted as output: {tick}")
        self.assertEqual(tick["watches"][0]["reason"], reason)
        self.assertEqual(self.journal_rows(), [],
                         "an UNKNOWN must never reach the notifier")
        self.assertEqual(completed.returncode, 4)

    # -- carriers that do carry output --------------------------------------

    def test_an_assistant_transcript_verifies_a_completed_run(self):
        completed, tick = self.observe(
            transcript=[{"role": "assistant", "content": "real output"}])
        self.assertEqual(tick["counts"], {"TERMINAL_VERIFIED": 1})
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(completed.returncode, 0)
        rows = self.journal_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "cloudcli")

    def test_an_assistant_turn_inside_messages_verifies_too(self):
        completed, tick = self.observe(messages=[
            {"role": "user", "content": "the prompt"},
            {"role": "assistant", "content": "the answer"}])
        self.assertEqual(tick["counts"], {"TERMINAL_VERIFIED": 1})
        self.assertEqual(len(self.journal_rows()), 1)

    # -- carriers that do not ------------------------------------------------

    def test_bare_strings_name_no_role_and_are_not_output(self):
        self.assert_no_evidence(transcript=["done", "looks finished"])

    def test_a_user_only_transcript_is_not_the_runs_output(self):
        self.assert_no_evidence(transcript=[{"role": "user", "content": "please run"}])

    def test_a_blank_assistant_turn_is_not_output(self):
        self.assert_no_evidence(transcript=[{"role": "assistant", "content": "   \n "}])

    def test_a_turn_from_another_run_is_not_this_runs_output(self):
        self.assert_no_evidence(transcript=[
            {"role": "assistant", "content": "someone else's answer",
             "runId": "run-other"}])

    def test_a_declared_blank_message_is_not_upgraded_by_a_carrier(self):
        self.assert_no_evidence(
            lastAssistantMessage={"content": ""},
            transcript=[{"role": "assistant", "content": "a carrier says otherwise"}])

    def test_a_transcript_over_the_turn_bound_is_refused_not_truncated(self):
        self.assert_no_evidence(reason="BAD_PAYLOAD",
                                transcript=[{"role": "assistant", "content": "x"}] * 65)

    def test_a_transcript_over_the_byte_bound_is_refused_not_truncated(self):
        self.assert_no_evidence(
            reason="BAD_PAYLOAD",
            transcript=[{"role": "assistant", "content": "x" * (64 * 1024 + 1)}])

    # -- adding a carrier must not loosen the other evidence -----------------

    def test_a_verified_artifact_without_a_sha_is_not_evidence(self):
        self.assert_no_evidence(
            watch=self.run_watch(expected_source_sha=SHA_GREEN),
            artifacts=[{"verified": True, "name": "report"}])

    def test_a_verified_artifact_on_another_sha_is_not_evidence(self):
        self.assert_no_evidence(
            watch=self.run_watch(expected_source_sha=SHA_GREEN),
            artifacts=[{"verified": True, "sourceSHA": SHA_OTHER}])

    def test_a_wrong_boot_beats_a_good_transcript(self):
        completed, tick = self.observe(
            watch=self.run_watch(expected_boot_id="boot-2"),
            transcript=[{"role": "assistant",
                         "content": "output from another boot"}])
        self.assertEqual(tick["watches"][0]["reason"], "BOOT_MISMATCH")
        self.assertEqual(self.journal_rows(), [])

    def test_a_transcript_on_a_running_run_proves_nothing(self):
        completed, tick = self.observe(
            status="running",
            transcript=[{"role": "assistant", "content": "a partial turn"}])
        self.assertEqual(tick["watches"][0]["status"], "RUNNING")
        self.assertEqual(tick["state"], "TICKED")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(self.journal_rows(), [],
                         "a running run was delivered as if it had finished")


class FencingTest(MonitorProcessTest):
    def test_boot_mismatch_fails_closed(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch(expected_boot_id="boot-2")]),
            facts=self.facts_document(runs={"run-1": {
                "instanceId": "legacy-01", "deviceRef": "device:dev-1",
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
            "instanceId": "legacy-01", "deviceRef": "device:dev-1", "sessionRef": "ses-1",
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
# #495 §4.1 — a receipt settles a fact; a cursor does not. Each tick below is a
# separate interpreter, because the bug is about what a *second process* reads.
# --------------------------------------------------------------------------- #
class ReceiptTest(MonitorProcessTest):
    """An unconfirmed hand-over must never become `SETTLED` on the next tick."""

    def green_run(self):
        return self.facts_document(runs={"run-1": {
            "instanceId": "legacy-01", "deviceRef": "device:dev-1",
            "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
            "status": "completed", "lastAssistantMessage": {"content": "output"}}})

    def typed_watch(self, **overrides):
        return self.run_watch(device_ref="device:dev-1", **overrides)

    def test_unconfirmed_delivery_never_settles_across_four_processes(self):
        """The reported bug: tick1 UNCONFIRMED, tick2 must not say SETTLED.

        Four *separate* interpreters share one durable state file and one
        file-backed ledger. The provider answers "no receipt" for the first two
        ticks and then answers; only the third and fourth ticks may be green.
        """
        self.set_mode("unknown")
        first = self.run_cli(notifier="ledger_factory",
                             config=self.config_document([self.typed_watch()]),
                             facts=self.green_run())
        tick1 = self.document(first)
        self.assertEqual(tick1["state"], "UNCONFIRMED")
        self.assertEqual(first.returncode, 4, "an unconfirmed hand-over is not exit 0")
        self.assertEqual(tick1["dispatched"], 1)
        self.assertEqual(tick1["delivered"], 0)
        self.assertEqual(tick1["unknown_receipts"], 1)
        self.assertEqual(tick1["watches"][0]["receipt"], "DELIVERY_UNKNOWN")
        self.assertEqual(len(self.journal_rows()), 1, "the first attempt did not run")
        self.assertEqual([r["state"] for r in self.ledger_rows()], ["DELIVERY_UNKNOWN"])

        # Same provider answer, a different process: the durable cursor says
        # "attempted", and that is all it may say.
        second = self.run_cli(notifier="ledger_factory")
        tick2 = self.document(second)
        self.assertEqual(tick2["state"], "UNCONFIRMED",
                         "an unconfirmed delivery was reported as settled")
        self.assertEqual(second.returncode, 4)
        self.assertEqual(tick2["delivered"], 0)
        self.assertEqual(tick2["unknown_receipts"], 1)
        self.assertEqual(len(self.journal_rows()), 1,
                         "an outstanding claim was re-sent without a receipt")

        # Now the provider answers. Only the ledger may unlock green.
        self.set_mode("deliver")
        third = self.run_cli(notifier="ledger_factory")
        tick3 = self.document(third)
        self.assertEqual(tick3["state"], "SETTLED")
        self.assertEqual(third.returncode, 0)
        states = [r["state"] for r in self.ledger_rows()]
        self.assertEqual(states, ["DELIVERED"], "the ledger holds two rows for one fact")

        # A settled fact is not re-delivered by a later process.
        sent = len(self.journal_rows())
        fourth = self.run_cli(notifier="ledger_factory")
        tick4 = self.document(fourth)
        self.assertEqual(tick4["state"], "SETTLED")
        self.assertEqual(fourth.returncode, 0)
        self.assertEqual(tick4["dispatched"], 0, "a settled fact was dispatched again")
        self.assertEqual(len(self.journal_rows()), sent,
                         "a settled fact produced a second message")

    def test_a_foreign_receipt_never_settles(self):
        """`DELIVERED` for somebody else's delivery is not this fact's receipt."""
        completed = self.run_cli(
            notifier="foreign_receipt_factory",
            config=self.config_document([self.typed_watch()]),
            facts=self.green_run())
        tick = self.document(completed)
        self.assertEqual(tick["delivered"], 0,
                         "a receipt naming another delivery was counted as delivered")
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(completed.returncode, 4)

    def test_a_receipt_without_an_identity_is_not_a_receipt(self):
        """A `DELIVERED` claim with no delivery key has nothing to bind it to."""
        completed = self.run_cli(
            notifier="anonymous_receipt_factory",
            config=self.config_document([self.typed_watch()]),
            facts=self.green_run())
        tick = self.document(completed)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(completed.returncode, 4)

    def test_an_unknown_receipt_is_still_unconfirmed_without_a_ledger(self):
        """No reconcile, no receipt: the fact stays unconfirmed, never green."""
        self.set_mode("unknown")
        first = self.run_cli(notifier="ledger_factory",
                             config=self.config_document([self.typed_watch()]),
                             facts=self.green_run())
        self.assertEqual(first.returncode, 4)
        # A notifier that can neither confirm nor be re-asked: the honest answer
        # is still UNCONFIRMED, and nothing is sent a second time.
        second = self.run_cli(notifier="raising_factory")
        tick = self.document(second)
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(second.returncode, 4)
        self.assertEqual(tick["dispatched"], 0)


class CiSubjectTest(MonitorProcessTest):
    """#495 §4.2 — a CI event needs a typed subject, and never an empty string."""

    def ci_watch(self, sha=SHA_GREEN, **overrides):
        watch = {"kind": "ci", "project": "proj", "expected_source_sha": sha,
                 "major": True}
        watch.update(overrides)
        return watch

    def green_ci(self, sha=SHA_GREEN, **overrides):
        payload = {"status": "completed", "conclusion": "success",
                   "head_sha": sha, "id": 13, "name": "proj"}
        payload.update(overrides)
        return {sha: payload}

    def test_the_ci_subject_is_the_granted_project_container(self):
        completed = self.run_cli(
            config=self.config_document([self.ci_watch()]),
            facts=self.facts_document(ci=self.green_ci()))
        tick = self.document(completed)
        self.assertEqual(tick["delivered"], 1)
        rows = self.journal_rows()
        self.assertEqual(len(rows), 1)
        subject = rows[0]["payload"]["device_ref"]
        self.assertEqual(subject, "device:ci:proj",
                         "the CI event did not carry a typed container subject")
        self.assertTrue(subject.startswith("device:"),
                        "an untyped or empty subject cannot reach #423")

    def test_a_ci_event_on_the_exact_sha_reaches_the_ledger(self):
        self.set_mode("deliver")
        completed = self.run_cli(
            notifier="ledger_factory",
            config=self.config_document([self.ci_watch()]),
            facts=self.facts_document(ci=self.green_ci()))
        tick = self.document(completed)
        self.assertEqual(tick["state"], "SETTLED")
        self.assertEqual(completed.returncode, 0)
        rows = self.ledger_rows()
        self.assertEqual([r["state"] for r in rows], ["DELIVERED"])
        self.assertEqual(rows[0]["payload"]["workflow_run"]["head_sha"], SHA_GREEN)
        self.assertEqual(rows[0]["delivery_key"].split(":")[2], "device")

    def test_a_ci_draft_never_reaches_the_notifier(self):
        completed = self.run_cli(
            config=self.config_document([self.ci_watch()]),
            facts=self.facts_document(ci=self.green_ci(draft=True)))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["reason"], "DRAFT_CI")
        self.assertEqual(self.journal_rows(), [])
        self.assertEqual(tick["dispatched"], 0)

    def test_a_ci_green_on_another_sha_never_reaches_the_notifier(self):
        completed = self.run_cli(
            config=self.config_document([self.ci_watch()]),
            facts=self.facts_document(ci={SHA_GREEN: {
                "status": "completed", "conclusion": "success",
                "head_sha": SHA_OTHER, "id": 14, "name": "proj"}}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["reason"], "SHA_MISMATCH")
        self.assertEqual(self.journal_rows(), [])

    def test_a_ci_watch_with_no_authorised_subject_is_blocked_not_sent(self):
        """No project, no declared subject: refuse, do not invent one."""
        completed = self.run_cli(
            config=self.config_document([{"kind": "ci",
                                          "expected_source_sha": SHA_GREEN}]),
            facts=self.facts_document(ci=self.green_ci()))
        tick = self.document(completed)
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_CI_SUBJECT")
        self.assertEqual(self.journal_rows(), [],
                         "a CI fact with no subject was sent anyway")

    def test_a_declared_ci_subject_must_already_be_typed(self):
        completed = self.run_cli(
            config=self.config_document([self.ci_watch(device_ref="proj")]),
            facts=self.facts_document(ci=self.green_ci()))
        tick = self.document(completed)
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(self.journal_rows(), [],
                         "an untyped subject was repaired by synthesising a device")

    def test_a_ci_watch_never_borrows_a_run_watch_identity(self):
        """Two watches, one config: the CI subject is the project, not the device."""
        run_watch = self.run_watch(device_ref="device:dev-1")
        completed = self.run_cli(
            config=self.config_document([run_watch, self.ci_watch()],
                                        devices=("device:dev-1",)),
            facts=self.facts_document(
                runs={"run-1": {"instanceId": "legacy-01",
                                "deviceRef": "device:dev-1", "sessionRef": "ses-1",
                                "runId": "run-1", "bootId": "boot-1",
                                "status": "completed",
                                "lastAssistantMessage": {"content": "output"}}},
                ci=self.green_ci()))
        subjects = [row["payload"].get("device_ref") for row in self.journal_rows()]
        self.assertIn("device:ci:proj", subjects)
        self.assertIn("device:dev-1", subjects)
        for row in self.journal_rows():
            self.assertNotEqual(
                (row["payload"].get("device_ref"), row["source"]),
                ("device:dev-1", "github_actions"),
                "the CI fact carried the run watch's device")


class CiAuthorizationTest(MonitorProcessTest):
    """Wave 16 §4.1 — a typed subject is a *claim*; the grant is the answer.

    A CI watch may declare `device_ref` as its delivery subject (#497). The
    source authorizes `project` and then, before it charges the budget or calls
    the provider, the *declared device* against `grant.devices`. The measured
    defect this pins: project granted + `device_ref: "device:victim"` with
    `grant.devices=["device:dev-1"]` produced a `DELIVERED` message whose text
    and delivery key both named the victim — a real pipeline fact delivered as
    somebody else's device.
    """

    def ci_watch(self, sha=SHA_GREEN, **overrides):
        watch = {"kind": "ci", "project": "proj", "expected_source_sha": sha,
                 "major": True}
        watch.update(overrides)
        return watch

    def green_ci(self, sha=SHA_GREEN, **overrides):
        payload = {"status": "completed", "conclusion": "success",
                   "head_sha": sha, "id": 13, "name": "proj"}
        payload.update(overrides)
        return {sha: payload}

    def test_a_ci_watch_must_not_deliver_under_an_ungranted_device(self):
        completed = self.run_cli(
            config=self.config_document(
                [self.ci_watch(device_ref="device:victim")],
                devices=("device:dev-1",), projects=("proj",)),
            facts=self.facts_document(ci=self.green_ci()))
        tick = self.document(completed)
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["watches"][0]["status"], "DENIED")
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_CI_SUBJECT")
        self.assertEqual(tick["reads"], 0,
                         "an ungranted device still cost a provider call")
        self.assertEqual(self.journal_rows(), [],
                         "a fact was delivered under a device the grant omits")
        self.assertEqual(self.ledger_rows(), [],
                         "a ledger claim was written for an unauthorized subject")

    def test_a_ci_watch_under_a_granted_device_is_delivered_under_it(self):
        """The positive control: without it the refusal above proves nothing."""
        completed = self.run_cli(
            config=self.config_document(
                [self.ci_watch(device_ref="device:dev-1")],
                devices=("device:dev-1",), projects=("proj",)),
            facts=self.facts_document(ci=self.green_ci()))
        tick = self.document(completed)
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(tick["state"], "SETTLED")
        rows = self.journal_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"]["device_ref"], "device:dev-1")

    def test_both_dimensions_must_be_granted_not_either_one(self):
        """Granted device, ungranted project: still nothing is read or sent."""
        completed = self.run_cli(
            config=self.config_document(
                [self.ci_watch(device_ref="device:dev-1")],
                devices=("device:dev-1",), projects=("some-other-project",)),
            facts=self.facts_document(ci=self.green_ci()))
        tick = self.document(completed)
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["watches"][0]["status"], "DENIED")
        self.assertEqual(tick["reads"], 0,
                         "an ungranted project still cost a provider call")
        self.assertEqual(self.journal_rows(), [])
        self.assertNotEqual(completed.returncode, 0)

    def test_the_project_container_subject_needs_no_device_grant(self):
        """A CI watch with no declared device is still `device:ci:<project>`."""
        completed = self.run_cli(
            config=self.config_document([self.ci_watch()],
                                        devices=(), projects=("proj",)),
            facts=self.facts_document(ci=self.green_ci()))
        tick = self.document(completed)
        self.assertEqual(tick["delivered"], 1)
        self.assertEqual(self.journal_rows()[0]["payload"]["device_ref"],
                         "device:ci:proj")


class NotifierSubjectContractTest(unittest.TestCase):
    """The hand-over boundary refuses the same thing the source does.

    Two layers, one word (`BLOCKED_CI_SUBJECT`): the source refuses an
    unauthorized declared device before it *reads*, and the notifier refuses it
    again before it *sends*. This class drives the second layer directly,
    because the CLI path never reaches it — the source has already refused.
    """

    def setUp(self):
        sys.path.insert(0, str(PACKAGE_PARENT))
        self.addCleanup(lambda: sys.path.remove(str(PACKAGE_PARENT)))
        from fleet_next_agent_monitor import contract, notifier
        self.contract = contract
        self.notifier = notifier
        self.watch = {"kind": "ci", "device_ref": "device:victim",
                      "project": "proj", "expected_source_sha": SHA_GREEN}

    def test_a_typed_ci_subject_is_not_authorized_by_being_typed(self):
        observation = {"view": "ci", "identity": {"deviceRef": "device:dev-1"}}
        with self.assertRaises(self.contract.MonitorRefused) as caught:
            self.notifier.delivery_subject(observation, self.watch)
        self.assertEqual(caught.exception.code, "BLOCKED_CI_SUBJECT")
        self.assertIn("typed is not authorized", caught.exception.why)

    def test_a_ci_subject_the_source_never_authorized_is_refused(self):
        observation = {"view": "ci", "identity": {}}
        with self.assertRaises(self.contract.MonitorRefused) as caught:
            self.notifier.delivery_subject(observation, self.watch)
        self.assertEqual(caught.exception.code, "BLOCKED_CI_SUBJECT")

    def test_a_ci_subject_the_source_authorized_is_handed_over(self):
        observation = {"view": "ci", "identity": {"deviceRef": "device:victim"}}
        self.assertEqual(self.notifier.delivery_subject(observation, self.watch),
                         "device:victim")


class DeviceSubjectTest(MonitorProcessTest):
    """The same rule on the run view: a bare device id is not a #423 subject."""

    def test_a_bare_device_ref_is_blocked_and_never_synthesised(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch(device_ref="dev-1")],
                                        devices=("dev-1",)),
            facts=self.facts_document(runs={"run-1": {
                "instanceId": "legacy-01", "deviceRef": "dev-1",
                "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                "status": "completed",
                "lastAssistantMessage": {"content": "output"}}}))
        tick = self.document(completed)
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_SUBJECT")
        self.assertEqual(self.journal_rows(), [],
                         "a bare device id was sent to a ledger that refuses it")

    def test_a_typed_device_ref_is_passed_through_unchanged(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch(device_ref="device:dev-1")]),
            facts=self.facts_document(runs={"run-1": {
                "instanceId": "legacy-01", "deviceRef": "device:dev-1",
                "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                "status": "completed",
                "lastAssistantMessage": {"content": "output"}}}))
        tick = self.document(completed)
        self.assertEqual(tick["delivered"], 1)
        row = self.journal_rows()[0]
        self.assertEqual(row["payload"]["device_ref"], "device:dev-1")
        # The boot identity travels as its own field, never folded into the
        # subject: #495 §4.7's caveat is that a watch-level boot expectation and
        # the device's own boot are different facts about different things.
        self.assertEqual(row["payload"]["bound_boot_id"], "boot-1")
        self.assertEqual(row["payload"]["run"]["bootId"], "boot-1")
        self.assertNotEqual(row["payload"]["run"]["bootId"],
                            row["payload"]["device_ref"])


# --------------------------------------------------------------------------- #
# the #488 contract: a ZERO-ARGUMENT tick, in a separate interpreter
# --------------------------------------------------------------------------- #
class RunIdentityTest(MonitorProcessTest):
    """P1 — a `delivery_id`-only watch must not be accepted and then never sent.

    `contract.require_watch` allows a run watch that names only a `delivery_id`.
    The run id #423's envelope needs may then come from the answer itself (the
    far end says which run this delivery is) — that is an observation, not an
    invention. When nothing names a run at all, the monitor must refuse *by
    name* before it writes a claim, instead of handing #423 a `runId: null` that
    the ledger rejects after the attempt has been recorded.
    """

    def delivery_watch(self, **overrides):
        watch = {"kind": "run", "device_ref": "device:dev-1", "session_ref": "ses-1",
                 "delivery_id": "del-1", "expected_boot_id": "boot-1", "major": True}
        watch.update(overrides)
        return watch

    def fact(self, *, run_id="run-1", message="output"):
        payload = {"instanceId": "legacy-01", "deviceRef": "device:dev-1",
                   "sessionRef": "ses-1", "deliveryId": "del-1", "bootId": "boot-1",
                   "status": "completed"}
        if run_id is not None:
            payload["runId"] = run_id
        if message is not None:
            payload["lastAssistantMessage"] = {"content": message}
        return {"delivery:del-1": payload}

    def test_a_delivery_id_only_watch_is_delivered_under_the_native_run_id(self):
        completed = self.run_cli(
            config=self.config_document([self.delivery_watch()]),
            facts=self.facts_document(runs=self.fact()))
        tick = self.document(completed)
        self.assertEqual(tick["delivered"], 1, completed.stderr)
        rows = self.journal_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["payload"]["run"]["runId"], "run-1",
                         "the far end's own runId was not carried into the envelope")

    def test_a_run_with_no_identity_anywhere_is_refused_by_name(self):
        completed = self.run_cli(
            config=self.config_document([self.delivery_watch()]),
            facts=self.facts_document(runs=self.fact(run_id=None)))
        tick = self.document(completed)
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(tick["delivered"], 0)
        self.assertEqual(tick["watches"][0]["receipt"], "BLOCKED_RUN_IDENTITY")
        self.assertEqual(tick["state"], "BLOCKED_INPUT")
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(self.journal_rows(), [],
                         "a payload #423 cannot key was handed over anyway")
        self.assertEqual(self.ledger_rows(), [],
                         "a claim was written for a fact that can never be delivered")


class ActivationEntryTest(MonitorProcessTest):
    def test_zero_argument_tick_returns_the_wrapper_shape(self):
        config = self.config_document([self.run_watch()])
        self.write(self.config, config)
        self.write(self.facts, self.facts_document(runs={"run-1": {
            "instanceId": "legacy-01", "deviceRef": "device:dev-1", "sessionRef": "ses-1",
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
# Wave 16 §4.2 — what a tick may say with its exit code, and to whom
# --------------------------------------------------------------------------- #
class SupervisorVerdictTest(MonitorProcessTest):
    """"I could not decide" must not leave the tick as exit 0.

    `SETTLED`/`TICKED`/`UNCONFIRMED` are three different sentences. Flattening
    the last two into 0 is how an expired or unobservable watch became invisible:
    a supervisor that reads only the number saw "nothing to do here" forever.
    The mirror-image error is just as bad — a run that is *running* is real work,
    not a failure — so the two are asserted together.
    """

    def completed_without_message(self):
        return {"run-1": {"instanceId": "legacy-01", "deviceRef": "device:dev-1",
                          "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                          "status": "completed"}}

    def running(self):
        return {"run-1": {"instanceId": "legacy-01", "deviceRef": "device:dev-1",
                          "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
                          "status": "running"}}

    def test_an_expired_run_never_leaves_the_tick_at_exit_zero(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs={"run-1": {"raise": "EXPIRED"}}))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["reason"], "EXPIRED")
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(completed.returncode, 4)
        self.assertEqual(tick["dispatched"], 0)
        self.assertEqual(self.journal_rows(), [])

    def test_an_unobservable_run_never_leaves_the_tick_at_exit_zero(self):
        for name, runs in (("unknown", self.completed_without_message()),
                           ("never-issued", {})):
            with self.subTest(name):
                self._reset()
                completed = self.run_cli(
                    config=self.config_document([self.run_watch()]),
                    facts=self.facts_document(runs=runs))
                tick = self.document(completed)
                self.assertEqual(tick["state"], "UNCONFIRMED")
                self.assertEqual(completed.returncode, 4)
                self.assertNotEqual(tick["watches"][0]["status"], "FAILED",
                                    "an unobservable run was reported as a failure")
                self.assertTrue(any(w["code"] == "UNOBSERVED"
                                    for w in tick["warnings"]))
                self.assertEqual(self.journal_rows(), [])

    def test_running_work_is_ticked_and_not_reported_as_failed(self):
        completed = self.run_cli(
            config=self.config_document([self.run_watch()]),
            facts=self.facts_document(runs=self.running()))
        tick = self.document(completed)
        self.assertEqual(tick["watches"][0]["status"], "RUNNING")
        self.assertEqual(tick["state"], "TICKED",
                         "work in flight was not reported as work in flight")
        self.assertEqual(completed.returncode, 0)
        self.assertEqual(tick["backlog"], 1)
        self.assertFalse(any(w["code"] == "UNOBSERVED" for w in tick["warnings"]),
                         "a running watch was counted as unobservable")
        self.assertEqual(self.journal_rows(), [],
                         "a RUNNING status was dispatched as an alert")

    def test_one_blind_watch_makes_the_whole_tick_unconfirmed(self):
        """Live work does not outvote a fact the monitor could not read."""
        watches = [self.run_watch(),
                   self.run_watch(run_id="run-2", device_ref="device:dev-1")]
        completed = self.run_cli(
            config=self.config_document(watches),
            facts=self.facts_document(runs={
                "run-1": self.running()["run-1"],
                "run-2": {"raise": "EXPIRED"}}))
        tick = self.document(completed)
        self.assertEqual(tick["state"], "UNCONFIRMED")
        self.assertEqual(completed.returncode, 4)
        self.assertEqual(tick["backlog"], 2)
        statuses = {row["status"] for row in tick["watches"]}
        self.assertEqual(statuses, {"RUNNING", "UNKNOWN"})

    def _reset(self) -> None:
        for path in (self.state, self.state.with_name(self.state.name + ".lock"),
                     self.journal, self.ledger):
            try:
                path.unlink()
            except FileNotFoundError:
                pass


class ActivationWrapperTest(MonitorProcessTest):
    """#498's wrapper, unmodified, started as a real subprocess.

    The wrapper is what a supervisor reads. #495 Wave 16 §4.2 measured the seam:
    the tick said `TICKED` exit 0 for a watch with no verdict, and the wrapper —
    which is not at fault — carried that word and number through. The fix is in
    the monitor core, so the proof has to cross the real boundary: a subprocess
    of #498's own activation module, over this monitor's zero-argument tick.

    #498 is not on this branch. In the composed tree (#497 + #498) these tests
    run for real; here they skip, and the skip is the honest statement rather
    than a green on something that never executed.
    """

    def setUp(self):
        super().setUp()
        if not ACTIVATION_MODULE.is_file():
            self.skipTest(
                f"{ACTIVATION_MODULE} is not on this branch; the wrapper "
                f"integration runs for real in the composed tree (#497 + #498)")
        self.legacy = self.dir / "legacy"
        self.next_root = self.dir / "next"
        self.legacy.mkdir()
        self.next_root.mkdir()
        self.state = self.next_root / "state.json"
        self.lock = self.next_root / "monitor.lock"

    def run_wrapper(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([
            str(PACKAGE_PARENT), str(TESTS_DIR), str(ACTIVATION_MODULE.parent)])
        env.update({
            "FNAM_TEST_JOURNAL": str(self.journal),
            "FNAM_TEST_LEDGER": str(self.ledger),
            "FNAM_TEST_MODE": str(self.mode),
            "FLEET_NEXT_AGENT_MONITOR_CONFIG": str(self.config),
            "FLEET_NEXT_AGENT_MONITOR_STATE": str(self.state),
            "FLEET_NEXT_AGENT_MONITOR_OFFLINE_FACTS": str(self.facts),
            "FLEET_NEXT_AGENT_MONITOR_NOTIFIER": f"{__name__}:journal_factory",
        })
        argv = [
            sys.executable, str(ACTIVATION_MODULE),
            "--legacy-root", str(self.legacy), "--next-root", str(self.next_root),
            "--state", str(self.state), "--lock", str(self.lock),
            "--port", "19301", "--legacy-port", "19300",
            "--service-name", "serveros-next-agent-monitor",
            "--legacy-service-name", "serveros-legacy",
            "--plugin-id", "serveros-next-monitor",
            "--legacy-plugin-id", "serveros-legacy",
            "--ticks", "1",
            "--monitor-entry", "fleet_next_agent_monitor.runtime:tick",
            "--legacy-read", "activation:probe_true",
            "--next-write", "activation:probe_false",
        ]
        done = subprocess.run(argv, cwd=str(self.dir), env=env,
                              capture_output=True, text=True, timeout=180)
        document = json.loads(done.stdout) if done.stdout.strip() else {}
        return done, document

    def _reset(self) -> None:
        for path in (self.state, self.lock, self.journal, self.ledger):
            try:
                path.unlink()
            except FileNotFoundError:
                pass

    def test_the_real_wrapper_never_reports_an_unobserved_watch_as_exit_zero(self):
        cases = (
            ("expired", {"run-1": {"raise": "EXPIRED"}}),
            ("unknown", {"run-1": {"instanceId": "legacy-01",
                                   "deviceRef": "device:dev-1",
                                   "sessionRef": "ses-1", "runId": "run-1",
                                   "bootId": "boot-1", "status": "completed"}}),
        )
        for name, runs in cases:
            with self.subTest(name):
                self._reset()
                self.write(self.config, self.config_document([self.run_watch()]))
                self.write(self.facts, self.facts_document(runs=runs))
                done, result = self.run_wrapper()
                # `ticks` proves the wrapper really ran a tick: a wrapper that
                # refused before ticking would also be non-zero, and would prove
                # nothing about the seam.
                self.assertEqual(result.get("ticks"), 1,
                                 f"the wrapper did not tick: {done.stderr[-400:]}")
                self.assertNotEqual(done.returncode, 0,
                                    f"the supervisor still sees exit 0: {result}")
                self.assertEqual(result["state"], "UNCONFIRMED")
                self.assertFalse(result["completed"])
                self.assertEqual(self.journal_rows(), [])

    def test_the_real_wrapper_still_exits_zero_for_work_in_flight(self):
        """The control: the fix must not turn RUNNING into a failure."""
        self.write(self.config, self.config_document([self.run_watch()]))
        self.write(self.facts, self.facts_document(runs={"run-1": {
            "instanceId": "legacy-01", "deviceRef": "device:dev-1",
            "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
            "status": "running"}}))
        done, result = self.run_wrapper()
        self.assertEqual(result.get("ticks"), 1, done.stderr[-400:])
        self.assertEqual(result["state"], "TICKED")
        self.assertEqual(done.returncode, 0)
        self.assertFalse(result["completed"], "a run in flight was called complete")
        self.assertEqual(result["health"]["backlog"], 1)


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


#: #522's shared evidence module. `main` carries the adapter package at its
#: pre-fix bytes and no `evidence.py`, so this joint test skips here and runs for
#: real in the composed tree (#522 + this branch). The adapter is never
#: modified: the test imports it in a subprocess and compares verdicts.
ADAPTER_EVIDENCE = (REPO / "integrations" / "fleet-next-monitor-source-adapter"
                    / "fleet_next_monitor_source_adapter" / "evidence.py")

#: One fact, one expected verdict, on both read paths.
JOINT_MATRIX = (
    ("assistant transcript",
     {"transcript": [{"role": "assistant", "content": "out"}]}, True),
    ("assistant messages",
     {"messages": [{"role": "user", "content": "q"},
                   {"role": "assistant", "content": "a"}]}, True),
    ("bare strings", {"transcript": ["done"]}, False),
    ("user only", {"transcript": [{"role": "user", "content": "hi"}]}, False),
    ("blank assistant",
     {"transcript": [{"role": "assistant", "content": "  "}]}, False),
    ("foreign run",
     {"transcript": [{"role": "assistant", "content": "x",
                      "runId": "run-other"}]}, False),
    ("declared blank + carrier",
     {"lastAssistantMessage": {"content": ""},
      "transcript": [{"role": "assistant", "content": "x"}]}, False),
    ("artifact without sha", {"artifacts": [{"verified": True}]}, False),
    ("artifact on another sha",
     {"artifacts": [{"verified": True, "sourceSHA": SHA_OTHER}]}, False),
)


class JointAdapterAgreementTest(unittest.TestCase):
    """Both read paths, one standard of evidence (#508 P1).

    The monitor's classifier and #522's adapter normaliser are separate
    implementations of one rule. This test runs the same facts through both in a
    single subprocess and requires the same verdict, so a future change to
    either path goes red instead of silently diverging again.
    """

    def setUp(self):
        if not ADAPTER_EVIDENCE.exists():
            self.skipTest("#522's evidence.py is not in this tree; the joint run "
                          "belongs to the composed tree (#522 + this branch)")

    @staticmethod
    def _script():
        return r'''
import json, sys
from fleet_next_monitor_source_adapter.evidence import normalise_run_fact, native_evidence
from fleet_next_agent_monitor.offline import OfflineFactsClient
from fleet_next_agent_monitor.sources import Grant, LegacyFactsSource

matrix = json.loads(sys.argv[1])
sha = sys.argv[2]
out = []
for entry in matrix:
    fact = {k: v for k, v in entry.items() if k != "expected"}
    body = {"instanceId": "legacy-01", "deviceRef": "device:dev-1",
            "sessionRef": "ses-1", "runId": "run-1", "bootId": "boot-1",
            "status": "completed"}
    body.update(fact)
    document = {"fleet_instance_id": "legacy-01", "route_id": "route-a",
                "boot_id": "boot-1", "provider": "cloudcli",
                "runs": {"run-1": body}, "ci": {}}
    source = LegacyFactsSource(OfflineFactsClient(document),
        Grant(principal="owner", devices=["device:dev-1"],
              sessions=["ses-1"], views=["run"]))
    observation = source.observe_run(device_ref="device:dev-1",
        session_ref="ses-1", run_id="run-1", expected_boot_id="boot-1",
        expected_source_sha=sha)
    out.append({
        "monitor": observation["status"] == "TERMINAL_VERIFIED",
        "adapter": bool(native_evidence(normalise_run_fact(body), sha)),
        "status": observation["status"],
        "reason": observation["reason"],
    })
print(json.dumps(out))
'''

    def test_the_two_paths_agree_on_the_evidence_matrix(self):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join([
            str(PACKAGE_PARENT),
            str(REPO / "integrations" / "fleet-next-monitor-source-adapter")])
        matrix = [dict(fact, expected=expected)
                  for _, fact, expected in JOINT_MATRIX]
        completed = subprocess.run(
            [sys.executable, "-c", self._script(), json.dumps(matrix), SHA_GREEN],
            cwd=str(REPO), env=env, capture_output=True, text=True, timeout=120)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        rows = json.loads(completed.stdout)
        self.assertEqual(len(rows), len(JOINT_MATRIX))
        for (name, _, expected), row in zip(JOINT_MATRIX, rows):
            with self.subTest(name):
                self.assertEqual(row["monitor"], expected, f"{name}: monitor path")
                self.assertEqual(row["adapter"], expected, f"{name}: adapter path")
                self.assertEqual(row["monitor"], row["adapter"],
                                 f"{name}: the two paths disagree")


if __name__ == "__main__":
    unittest.main()
