"""Cross-process QA of the Batch CLI (#481) and the activation wrapper (#482).

WHAT IS BEING TESTED, AND BY WHOM
---------------------------------
These are *independent* checks. Every process below is a real `python3` start of
the authors' own `cli.py`, wired to the authors' own offline fakes
(`test_fleet_v1_milestone_batch_cli_fakes`: `file_lark`, `scripted_source`). No
part of the monitor, the batch or the notifier is re-implemented here, and no
fake stands in for a real module under test.

The one thing this suite refuses to do is pretend the Agent Monitor exists. Its
core (`integrations/fleet-next-agent-monitor/`) is still absent from `main`, from
every open PR and from this tree; what #482 adds is a *wrapper*
(`fleet-next-agent-monitor-activation/`). Both facts are asserted as named
blocks so that they read as NOT RUN rather than as passing.

Measured on: #481 `9b7710a26180e1e6d00ce597827d447d0d2348b7`,
#482 `0f581e722c81a20152d23e81ecb666869c4fb3d5`.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BATCH = ROOT / "integrations" / "fleet-v1-milestone-batch"
CLI = BATCH / "cli.py"
REGISTRATION = BATCH / "registration.py"
FAKES = "test_fleet_v1_milestone_batch_cli_fakes"
FIXTURES = ROOT / "integrations" / "fleet-v1-milestone-observer" / "fixtures"
MONITOR_CORE = ROOT / "integrations" / "fleet-next-agent-monitor"
ACTIVATION = ROOT / "integrations" / "fleet-next-agent-monitor-activation"

BLOCKED_UPSTREAM_SOURCE = (
    "BLOCKED_UPSTREAM_SOURCE: integrations/fleet-next-agent-monitor/ is absent from "
    "this tree, from main and from every open PR. #482 ships an activation wrapper, "
    "not the monitor. No stub is substituted here.")
BLOCKED_RUNTIME_ENTRY = (
    "BLOCKED_RUNTIME_ENTRY: the activation wrapper's entry has not been shown to be "
    "wired to #481's CLI on this tree.")


def load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def registration_module():
    return load(REGISTRATION, "xproc_qa_registration")


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class Harness:
    """A controlled directory plus the argv a scheduler would use."""

    def __init__(self, case: unittest.TestCase):
        self.dir = Path(tempfile.mkdtemp(prefix="xproc-qa-"))
        case.addCleanup(shutil.rmtree, self.dir, True)
        self.registration = self.dir / "registration.json"
        self.ledger = self.dir / "ledger.json"
        self.state = self.dir / "state.json"
        self.journal = self.dir / "journal.jsonl"
        self.reads = self.dir / "reads.jsonl"
        self.provider_options = self.dir / "provider.json"
        self.source_options = self.dir / "source.json"
        self.write_options({}, {})

    def write_options(self, provider: dict, source: dict) -> None:
        provider = dict(provider, journal=str(self.journal))
        source = dict(source, reads=str(self.reads))
        self.provider_options.write_text(json.dumps(provider), encoding="utf-8")
        self.source_options.write_text(json.dumps(source), encoding="utf-8")

    @property
    def env(self) -> dict:
        # `-I` is deliberately NOT used: it ignores PYTHONPATH, which is how the
        # authors' fakes reach the CLI. Bytecode is still suppressed.
        return {**os.environ, "PYTHONPATH": str(ROOT / "tests"),
                "PYTHONDONTWRITEBYTECODE": "1"}

    def run(self, timeout: int = 180) -> tuple[int, dict]:
        done = subprocess.run(
            [sys.executable, str(CLI),
             "--registration", str(self.registration),
             "--ledger", str(self.ledger), "--state", str(self.state),
             "--provider-factory", f"{FAKES}:file_lark",
             "--source-factory", f"{FAKES}:scripted_source",
             "--provider-options", str(self.provider_options),
             "--source-options", str(self.source_options)],
            capture_output=True, text=True, timeout=timeout, env=self.env,
            cwd=str(ROOT))
        payload = {}
        for line in reversed(done.stdout.strip().splitlines()):
            try:
                payload = json.loads(line)
                break
            except ValueError:
                continue
        return done.returncode, payload

    def sent(self) -> list[dict]:
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in
                self.journal.read_text(encoding="utf-8").splitlines() if line.strip()]

    def file_registration(self, *, ref=None, task_id="t1", deadline=True) -> None:
        reg = registration_module()
        batch = reg._batch()
        member = reg.RegisteredMember(
            member=batch.BatchMember(
                source="cloudcli", ref=ref,
                device_ref="device:tokyo-shared-dev-01", role="tokyo",
                task_id=task_id, boot_id="boot-A" if ref else None),
            provider="fake", session_ref="session:dev:qa-1",
            delivery_id="delivery:qa-1")
        value = reg.Registration(
            batch_id="batch-qa-1", members=(member,),
            registered_at="2026-10-28T15:00:00Z", registered_by="fjd",
            conversation_ref="conversation:qa",
            expires_at="2026-12-31T00:00:00Z" if deadline else None)
        reg.save(self.registration, value)

    def amend(self, run_id="run_qa_1") -> None:
        reg = registration_module()
        reg.amend(self.registration, task_id="t1", run_id=run_id,
                  boot_id="boot-A", delivery_id="delivery:qa-1",
                  at="2026-10-28T15:10:00Z")

    def script_completed(self, run_id="run_qa_1") -> None:
        payload = json.loads(
            (FIXTURES / "cloudcli" / "completed.json").read_text(encoding="utf-8"))
        payload["run"]["runId"] = run_id
        payload["session_ref"] = "session:dev:qa-1"
        payload["run"]["sessionId"] = "session:dev:qa-1"
        self.write_options({}, {"script": {f"cloudcli:{run_id}": [payload]}})


class TheMonitorCoreStillDoesNotExist(unittest.TestCase):
    @unittest.skipUnless(MONITOR_CORE.is_dir(), BLOCKED_UPSTREAM_SOURCE)
    def test_the_monitor_runs_a_tick(self):
        self.fail("unreachable while the skip holds")

    def test_the_activation_wrapper_is_not_the_monitor(self):
        # A wrapper existing is not the monitor existing; saying so keeps
        # "activation landed" from being read as "monitor complete".
        self.assertTrue(ACTIVATION.is_dir(),
                        "#482's wrapper should be present on this combined tree")
        self.assertFalse(MONITOR_CORE.is_dir(),
                         "if the core appeared, the skip above must be replaced by "
                         "behavioural tests in the same change")


class ProcessADispatchThenExit(unittest.TestCase):
    """Process A: file the batch, then stop. The file is the whole handoff."""

    def test_a_filed_registration_outlives_the_process_that_wrote_it(self):
        h = Harness(self)
        h.file_registration()
        self.assertTrue(h.registration.is_file())
        reg = registration_module()
        loaded = reg.load(h.registration)
        self.assertEqual(loaded.batch_id, "batch-qa-1")
        self.assertIsNone(loaded.member("t1").member.ref,
                          "a queued task must have no Run in the file")
        self.assertIn("amendments", loaded.document())


class ProcessBAllQueuedIsBlockedInput(unittest.TestCase):
    """Process B: a second interpreter, everything queued, nothing sent."""

    def test_blocked_input_sends_nothing(self):
        h = Harness(self)
        h.file_registration()
        code, payload = h.run()
        self.assertEqual(code, 3, payload)
        self.assertEqual(payload["state"], "BLOCKED_INPUT")
        self.assertEqual(payload["counts"], {"PENDING": 1})
        self.assertEqual(payload["watches"], {"declared": 1, "issued": 0})
        self.assertIsNone(payload["summary"])
        self.assertEqual(payload["members"][0]["verdict"], "PENDING")
        self.assertIsNone(payload["members"][0]["run_id"])
        self.assertEqual(h.sent(), [],
                         "BLOCKED_INPUT must issue nothing at all")

    def test_a_queued_task_without_a_deadline_is_refused_at_filing(self):
        h = Harness(self)
        with self.assertRaises(Exception) as caught:
            h.file_registration(deadline=False)
        self.assertIn("NO_DEADLINE", str(caught.exception))


class ProcessCAmendmentThenOneSummary(unittest.TestCase):
    """Process C: amend in one process, tick in another, count the sends."""

    def test_amendment_unblocks_and_delivers_exactly_one_summary(self):
        h = Harness(self)
        h.file_registration()
        h.amend()
        h.script_completed()
        code, payload = h.run()
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["state"], "SETTLED")
        self.assertTrue(payload["settled"])
        self.assertEqual(payload["counts"], {"VERIFIED": 1})
        member = payload["members"][0]
        self.assertEqual(member["outcome"], "VERIFIED_GREEN")
        self.assertEqual(member["run_id"], "run_qa_1")
        self.assertEqual(member["delivery_id"], "delivery:qa-1",
                         "the run id and the delivery id must not be conflated")

        sent = h.sent()
        self.assertEqual(len(sent), 2,
                         "expected the member milestone and the batch summary, "
                         "not one and not three")
        keys = [record["idempotency_key"] for record in sent]
        self.assertEqual(len(keys), len(set(keys)), "two sends shared one key")
        self.assertEqual(payload["summary"]["delivery_key"],
                         "batch:batch-qa-1:summary")

    def test_the_same_document_is_unchanged_and_a_different_one_is_refused(self):
        h = Harness(self)
        h.file_registration()
        reg = registration_module()
        before = h.registration.read_bytes()

        # The same document again is UNCHANGED, and the file does not move.
        self.assertEqual(reg.save(h.registration, reg.load(h.registration)), "UNCHANGED")
        self.assertEqual(h.registration.read_bytes(), before)

        # A *different* member set under the same Batch ID re-scopes a dispatch
        # that is already being correlated, so it is refused rather than written.
        batch = reg._batch()
        other = reg.RegisteredMember(
            member=batch.BatchMember(source="cloudcli", ref=None,
                                     device_ref="device:tokyo-shared-dev-01",
                                     role="dell", task_id="t2"),
            provider="fake", session_ref="session:dev:qa-2",
            delivery_id="delivery:qa-2")
        resequenced = reg.Registration(
            batch_id="batch-qa-1", members=(other,),
            registered_at="2026-10-28T15:00:00Z", registered_by="fjd",
            conversation_ref="conversation:qa", expires_at="2026-12-31T00:00:00Z")
        with self.assertRaises(Exception) as caught:
            reg.save(h.registration, resequenced)
        self.assertIn("CONFLICTING_REGISTRATION", str(caught.exception))
        self.assertEqual(h.registration.read_bytes(), before,
                         "a refused re-scope must not touch the filed dispatch")


class ProcessDAReplayDoesNotResend(unittest.TestCase):
    """Process D: the same batch again, from a third interpreter."""

    def test_second_process_reports_duplicate_and_keeps_the_remote_id(self):
        h = Harness(self)
        h.file_registration()
        h.amend()
        h.script_completed()
        first_code, first = h.run()
        first_sends = len(h.sent())
        second_code, second = h.run()
        self.assertEqual(first_code, 0)
        self.assertEqual(second_code, 0)
        self.assertEqual(second["state"], "SETTLED")
        self.assertEqual(len(h.sent()), first_sends,
                         "a replay delivered a second time")
        self.assertEqual(second["summary"]["state"], "DUPLICATE")
        self.assertEqual(second["summary"]["remote_id"],
                         first["summary"]["remote_id"],
                         "a replay must name the same message, not a new one")
        self.assertIn("replay", second["members"][0]["why"])


class NotGreenInputsAreNotGreen(unittest.TestCase):
    """The inputs that must never be read as success."""

    def _settle(self, source_payload, *, run_id="run_qa_1"):
        h = Harness(self)
        h.file_registration()
        h.amend(run_id=run_id)
        h.write_options({}, {"script": {f"cloudcli:{run_id}": [source_payload]}})
        code, payload = h.run()
        return h, code, payload

    def test_run_not_found_never_greens(self):
        h, code, payload = self._settle({"$refuse": "RUN_NOT_FOUND"})
        self.assertNotEqual(payload["members"][0]["outcome"], "VERIFIED_GREEN")
        self.assertEqual(payload["summary"], None,
                         "nothing was verified, so there is no summary")
        self.assertEqual(h.sent(), [])

    def test_a_completed_run_without_native_output_never_greens(self):
        payload_in = json.loads(
            (FIXTURES / "cloudcli" / "completed-no-output.json").read_text(encoding="utf-8"))
        payload_in["run"]["runId"] = "run_qa_1"
        payload_in["session_ref"] = "session:dev:qa-1"
        h, code, payload = self._settle(payload_in)
        self.assertNotEqual(payload["members"][0]["outcome"], "VERIFIED_GREEN")
        self.assertEqual(payload["members"][0]["verdict"], "UNKNOWN")
        # `settled` means "every member reached a terminal fact", NOT "the batch
        # succeeded" — an UNKNOWN completion is terminal. The non-success signal
        # is the counts, and this asserts it rather than the friendlier field.
        self.assertNotIn("VERIFIED", payload["counts"])
        self.assertEqual(payload["counts"].get("UNKNOWN"), 1)

    def test_a_network_loss_never_greens(self):
        h, code, payload = self._settle({"$refuse": "NETWORK"})
        self.assertNotEqual(payload["members"][0]["outcome"], "VERIFIED_GREEN")
        self.assertFalse(payload["settled"])

    def test_a_completed_run_under_another_boot_is_not_this_watchs_green(self):
        payload_in = json.loads(
            (FIXTURES / "cloudcli" / "completed.json").read_text(encoding="utf-8"))
        payload_in["run"]["runId"] = "run_qa_1"
        payload_in["session_ref"] = "session:dev:qa-1"
        payload_in["run"]["sessionId"] = "session:dev:qa-1"
        payload_in["run"]["bootId"] = "boot-OTHER"
        h, code, payload = self._settle(payload_in)
        self.assertNotEqual(payload["members"][0]["outcome"], "VERIFIED_GREEN",
                            "another boot's completion is not this watch's milestone")


class TheShadowWritesNothingItDoesNotOwn(unittest.TestCase):
    def test_a_tick_does_not_mutate_the_registration(self):
        h = Harness(self)
        h.file_registration()
        h.amend()
        h.script_completed()
        before = sha(h.registration)
        h.run()
        self.assertEqual(sha(h.registration), before,
                         "the entrypoint re-scoped the dispatch it was reading")

    def test_the_ledger_and_the_state_are_separate_files(self):
        h = Harness(self)
        h.file_registration()
        h.amend()
        h.script_completed()
        h.run()
        self.assertTrue(h.ledger.is_file())
        self.assertTrue(h.state.is_file())
        self.assertNotEqual(h.ledger.resolve(), h.state.resolve(),
                            "a ledger that doubles as the state file loses one of "
                            "the two questions it has to answer")

    def test_a_second_concurrent_writer_is_refused_the_lock(self):
        h = Harness(self)
        h.file_registration()
        h.amend()
        h.script_completed()
        # Hold the lock the CLI is given, then ask it to run.
        lock = h.dir / "tick.lock"
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import fcntl,sys,time\n"
             f"f=open({str(lock)!r},'w')\n"
             "fcntl.flock(f,fcntl.LOCK_EX)\n"
             "print('held',flush=True)\n"
             "time.sleep(30)\n"],
            stdout=subprocess.PIPE, text=True)
        try:
            holder.stdout.readline()
            done = subprocess.run(
                [sys.executable, str(CLI),
                 "--registration", str(h.registration), "--ledger", str(h.ledger),
                 "--state", str(h.state), "--lock", str(lock),
                 "--provider-factory", f"{FAKES}:file_lark",
                 "--source-factory", f"{FAKES}:scripted_source",
                 "--provider-options", str(h.provider_options),
                 "--source-options", str(h.source_options)],
                capture_output=True, text=True, timeout=120, env=h.env, cwd=str(ROOT))
            self.assertNotEqual(done.returncode, 0,
                                "a second writer ran while the lock was held")
            self.assertIn("lock", (done.stdout + done.stderr).lower())
        finally:
            holder.kill()
            holder.wait(timeout=10)


class TheActivationWrapperIsNotWired(unittest.TestCase):
    """#482 ships a wrapper; being a wrapper is not being the monitor, and being
    present is not being wired to #481's CLI."""

    def test_the_wrapper_does_not_name_the_batch_cli(self):
        source = (ACTIVATION / "activation.py").read_text(encoding="utf-8")
        wired = any(token in source for token in
                    ("fleet-v1-milestone-batch", "milestone_batch_cli",
                     "cli.py", "batch.tick"))
        if not wired:
            self.skipTest(BLOCKED_RUNTIME_ENTRY)
        # If it does name the CLI, the entry must be exercised by its author's
        # tests rather than assumed here.
        self.assertTrue((ACTIVATION / "activation.py").is_file())


if __name__ == "__main__":
    unittest.main()
