#!/usr/bin/env python3
"""#417 Wave9 — the Batch lane's bounded one-shot entrypoint.

WHAT THIS FILE PINS, AND WHY EACH ONE IS A FAILURE MODE RATHER THAN A FEATURE
-----------------------------------------------------------------------------
`dispatch.py` emits the `#446` config for a *resident* runner and correlates one
tick for a caller that already holds a source and a notifier. Neither is
something a supervisor can start:

* `runner_document` refuses `NO_WATCHES`, so a dispatch whose ACKs carried no
  `run_id` cannot be turned into a config at all;
* `tick_once`/`Ticker` refuse `NO_WATCHES` for the same reason.

That refusal is right — "a tick with nothing to read would be a no-op reported
as progress" — but it leaves the real operational question unanswered: a
dispatch that is entirely queued produces *silence* until somebody notices. The
tests below pin the two answers this lane is allowed to give, and the ones it is
not:

1. **Before the deadline** the entrypoint answers `BLOCKED_INPUT` and exits
   non-zero: a reviewable statement that nothing was issued, naming the tasks
   and the deadline, with no watch, no source call, no ledger and no message.
2. **After the deadline** the same batch is settled by `#464`'s own deadline
   rule as `UNOBSERVED` — a bounded alert per member (capped at `MAX_ALERTS`)
   plus one summary that says `0/N 已验证` and lists `UNOBSERVED`. That is not a
   completion and the tests say so explicitly.

The rest of the file is about the identities the owner asked to keep apart
(`taskId` / `sessionRef` / `runId` / `deliveryId` / `bootId`), about an ACK-only
or `RUN_NOT_FOUND` task never becoming a success, and about **two independent
Python processes** sharing one `#423` ledger: the second and third must learn
the delivery from the ledger instead of sending again, and a provider that never
confirmed must never be recorded as delivered.

LIVE IM NOT_RUN: every provider here is `FileLark`, a file-backend fake in
`tests/test_fleet_v1_milestone_batch_cli_fakes.py`. No credential is read, no
socket is opened, no model is called, no service is started.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
BATCH_DIR = ROOT / "integrations" / "fleet-v1-milestone-batch"
CLI_PATH = BATCH_DIR / "cli.py"

#: `module:callable` needs the tests directory importable — in this process for
#: the in-process tests, and through `PYTHONPATH` for the subprocess ones.
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

FAKES = "test_fleet_v1_milestone_batch_cli_fakes"
SCHEMA = "serveros/fleet-v1-milestone-batch-cli.v1"

#: A deadline no test will reach, and one every clock has already passed.
FUTURE = "2099-12-31T00:00:00Z"
PAST = "2026-10-27T11:00:00Z"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: The Batch lane's own end-to-end harness: the same cast (`FakeLark`,
#: `ScriptedSource`, the `cloudcli` fixtures) and the same registration filing.
#: Reused rather than re-derived, because a second copy of a fixture is a second
#: thing that can be made to agree with itself.
e2e = _load("batch_cli_e2e_support",
            HERE / "test_fleet_v1_milestone_batch_e2e_integration.py")
m = e2e.m
batch = e2e.batch
registration = e2e.registration
observer = e2e.observer
dispatch = e2e.dispatch_module()
runner = _load("serveros_fleet_v1_milestone_runner",
               ROOT / "integrations" / "fleet-v1-milestone-runner" / "runner.py")

HAS_SEAM = e2e.HAS_SEAM

BATCH_ID = e2e.BATCH_ID
RECIPIENT = e2e.RECIPIENT
RUN_A, RUN_B, RUN_C = e2e.RUN_A, e2e.RUN_B, e2e.RUN_C
DEV_A, DEV_B, DEV_C = e2e.DEV_A, e2e.DEV_B, e2e.DEV_C
BOOT_A, BOOT_B, BOOT_C = e2e.BOOT_A, e2e.BOOT_B, e2e.BOOT_C
SESSION_A, SESSION_B, SESSION_C = e2e.SESSION_A, e2e.SESSION_B, e2e.SESSION_C
DELIVERY_A, DELIVERY_B, DELIVERY_C = (e2e.DELIVERY_A, e2e.DELIVERY_B,
                                      e2e.DELIVERY_C)


def cli():
    """The module under test. Absent before this PR: that is the RED."""
    return _load("serveros_fleet_v1_milestone_batch_cli", CLI_PATH)


# --------------------------------------------------------------------------- #
class Base(e2e.Base):
    """The e2e harness, plus a file-backed provider and the CLI's argv."""

    def setUp(self):
        super().setUp()
        self.journal = self.tmp / "provider.jsonl"
        self.closed_log = self.tmp / "closed.jsonl"
        self.reads = self.tmp / "reads.jsonl"

    # -- filing ------------------------------------------------------------

    def file(self, *, ack_rows=None, plan_value=None, expires_at=FUTURE):
        return super().file(ack_rows=ack_rows, plan_value=plan_value,
                            expires_at=expires_at)

    # -- the operator's write side: options and argv ------------------------

    def options(self, name: str, value) -> str:
        path = self.tmp / f"{name}.json"
        path.write_text(json.dumps(value), encoding="utf-8")
        return str(path)

    def provider_path(self, **changes) -> str:
        value = {"journal": str(self.journal), "closed": str(self.closed_log)}
        value.update(changes)
        return self.options("provider", value)

    def source_path(self, script=None, **changes) -> str:
        value = {"script": script or {}, "reads": str(self.reads)}
        value.update(changes)
        return self.options("source", value)

    def argv(self, *, registration_path=None, ledger=None, state=None,
             lock=True, provider_factory=f"{FAKES}:file_lark",
             source_factory=f"{FAKES}:scripted_source",
             provider_options=None, source_options=None, max_sends=None,
             extra=()) -> list:
        out = ["--registration", str(registration_path or self.path),
               "--ledger", str(ledger or self.ledger_path),
               "--state", str(state or self.state_path),
               "--provider-factory", provider_factory,
               "--source-factory", source_factory,
               "--provider-options", provider_options or self.provider_path(),
               "--source-options", source_options or self.source_path()]
        if lock:
            out += ["--lock", str(self.lock_path)]
        if max_sends is not None:
            out += ["--max-sends-per-minute", str(max_sends)]
        return out + list(extra)

    # -- the two ways to run it --------------------------------------------

    def run_cli(self, **kwargs):
        """One invocation in this process. Returns (exit code, parsed stdout)."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = cli().main(self.argv(**kwargs))
        text = out.getvalue().strip()
        # A probe that parses nothing must not look like a passing run.
        self.assertTrue(text, "the entrypoint printed no JSON; exit "
                              f"code was {code}")
        return code, json.loads(text)

    def run_process(self, **kwargs):
        """One invocation in its own interpreter. Returns (code, doc, stdout).

        Read back as strict UTF-8 with no `PYTHONIOENCODING` hint, because that
        is what a supervisor's pipe looks like: on a Chinese-locale Windows the
        child's stdout would otherwise be cp936 and the em dash in `why` would
        arrive as two different documents depending on who launched it.
        """
        env = dict(os.environ)
        env.pop("PYTHONIOENCODING", None)
        prefix = str(HERE)
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = prefix if not existing else prefix + os.pathsep + existing
        completed = subprocess.run(
            [sys.executable, str(CLI_PATH)] + self.argv(**kwargs),
            capture_output=True, text=True, encoding="utf-8", errors="strict",
            env=env, timeout=180)
        text = (completed.stdout or "").strip()
        self.assertTrue(text, "the subprocess printed no JSON.\n"
                              f"exit={completed.returncode}\n"
                              f"stderr={completed.stderr}")
        return completed.returncode, json.loads(text), completed.stdout

    # -- reading the provider's side ---------------------------------------

    def sent(self) -> list:
        if not self.journal.exists():
            return []
        return [json.loads(line) for line in
                self.journal.read_text(encoding="utf-8").splitlines()
                if line.strip()]

    def read_keys(self) -> list:
        if not self.reads.exists():
            return []
        return [line for line in
                self.reads.read_text(encoding="utf-8").splitlines() if line.strip()]

    def completed_script(self) -> dict:
        rows = ((RUN_A, DEV_A, BOOT_A), (RUN_B, DEV_B, BOOT_B),
                (RUN_C, DEV_C, BOOT_C))
        return {f"cloudcli:{run}": [e2e.for_run("cloudcli", "completed",
                                               run_id=run, device=device,
                                               boot=boot)]
                for run, device, boot in rows}

    def queued(self, *, plan_value=None) -> dict:
        """A filed dispatch whose ACKs name a receipt but no Run."""
        return self.file(ack_rows=e2e.acks(runs=(None, None, None)),
                         plan_value=plan_value)


# --------------------------------------------------------------------------- #
# 1. it refuses what it cannot trust, before it reads anything
# --------------------------------------------------------------------------- #
class TheEntrypointRefusesWhatItCannotTrust(Base):
    def assertNothingHappened(self):
        self.assertFalse(self.ledger_path.exists(),
                         "a refused invocation must not open #423's ledger")
        self.assertEqual(self.sent(), [], "a refusal must not reach the provider")
        self.assertEqual(self.read_keys(), [], "a refusal must not read a source")

    def test_a_relative_registration_path_is_refused_by_name(self):
        code, doc = self.run_cli(registration_path="batches/queued.json")
        self.assertEqual(code, 2)
        self.assertEqual(doc["schema"], SCHEMA)
        self.assertEqual(doc["state"], "REFUSED")
        self.assertEqual(doc["code"], "BAD_PATH")
        self.assertIn("absolute", doc["why"])
        self.assertNothingHappened()

    def test_a_registration_that_carries_code_is_refused_as_data(self):
        # The registration is JSON read by `#464`'s loader. A field it does not
        # own is a contract change — and the point of the closed member field
        # set is that a batch file can never name something to import.
        filed = self.queued()
        value = json.loads(self.path.read_text(encoding="utf-8"))
        value["members"][0]["factory"] = "os:system"
        self.path.write_text(json.dumps(value), encoding="utf-8")

        code, doc = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(doc["state"], "REFUSED")
        self.assertEqual(doc["code"], "BAD_REGISTRATION")
        self.assertIn("factory", doc["why"])
        self.assertNothingHappened()
        self.assertEqual(filed.batch_id, BATCH_ID)

    def test_a_factory_spec_that_is_not_module_colon_callable_is_refused(self):
        code, doc = self.run_cli(provider_factory="os.system")
        self.assertEqual(code, 2)
        self.assertEqual(doc["code"], "BAD_FACTORY")
        self.assertIn("module:callable", doc["why"])
        self.assertNothingHappened()

    def test_a_factory_that_returns_something_else_is_refused(self):
        self.file()
        code, doc = self.run_cli(provider_factory=f"{FAKES}:bad_provider")
        self.assertEqual(code, 2)
        self.assertEqual(doc["state"], "REFUSED")
        self.assertEqual(doc["code"], "BAD_PROVIDER")
        self.assertNothingHappened()

    def test_a_factory_that_raises_is_a_refusal_and_not_a_traceback(self):
        self.file()
        code, doc = self.run_cli(provider_factory=f"{FAKES}:raising_factory")
        self.assertEqual(code, 2)
        self.assertEqual(doc["code"], "BAD_PROVIDER")
        self.assertIn("RuntimeError", doc["why"])
        self.assertNothingHappened()

    def test_a_second_tick_cannot_overlap_the_first(self):
        self.file()
        held = runner.SingleActiveLock(str(self.lock_path))
        held.acquire()
        self.addCleanup(held.release)

        code, doc = self.run_cli()
        self.assertEqual(code, 2)
        self.assertEqual(doc["code"], "LOCKED")
        self.assertIn("ANOTHER_RUNNER_ACTIVE", doc["why"])
        self.assertNothingHappened()


# --------------------------------------------------------------------------- #
# 2. a dispatch no Run ever reached
# --------------------------------------------------------------------------- #
class ABatchNoRunEverReached(Base):
    def test_a_batch_with_no_issued_run_is_blocked_not_settled(self):
        self.queued()
        code, doc = self.run_cli()

        self.assertEqual(code, 3, "a blocked batch is not a successful run")
        self.assertEqual(doc["state"], "BLOCKED_INPUT")
        self.assertEqual(doc["code"], "NO_ISSUED_RUN")
        self.assertEqual(doc["watches"], {"declared": 3, "issued": 0})
        self.assertFalse(doc["settled"])
        self.assertIsNone(doc["summary"])
        self.assertEqual(doc["alerts"], [])
        self.assertEqual(doc["counts"], {"PENDING": 3})

        for member in doc["members"]:
            self.assertEqual(member["verdict"], "PENDING")
            self.assertEqual(member["code"], "NEVER_ISSUED")
            self.assertIsNone(member["run_id"],
                              "a queued task has no Run and none may be invented")

        # Nothing was read, nothing was sent, nothing was recorded.
        self.assertFalse(self.ledger_path.exists())
        self.assertEqual(self.sent(), [])
        self.assertEqual(self.read_keys(), [])

    def test_the_blocked_answer_is_reviewable_for_the_owner(self):
        self.queued()
        _, doc = self.run_cli()

        self.assertEqual(doc["batch_id"], BATCH_ID)
        self.assertEqual(doc["deadline"], FUTURE)
        self.assertIn(FUTURE, doc["why"])
        self.assertIn("3", doc["why"])
        self.assertEqual(doc["registration"], str(self.path))
        # The owner can see WHICH dispatch is stuck without the chat history.
        self.assertEqual([member["task_id"] for member in doc["members"]],
                         ["t-tokyo", "t-dell", "t-fjd"])
        self.assertEqual([member["session_ref"] for member in doc["members"]],
                         [SESSION_A, SESSION_B, SESSION_C])
        self.assertEqual([member["delivery_id"] for member in doc["members"]],
                         [DELIVERY_A, DELIVERY_B, DELIVERY_C])
        self.assertIn("BLOCKED_INPUT", doc["why"])

    def test_the_library_still_refuses_a_bare_no_watches_tick(self):
        # The entrypoint answers a queued batch; it does not make `#464`'s
        # library pretend that a tick with nothing to read is a tick.
        filed = self.queued()
        with self.assertRaises(batch.BatchRefused) as caught:
            dispatch.tick_once(filed, source=e2e.ScriptedSource({}),
                               notifier=object(), state_path=self.state_path)
        self.assertEqual(caught.exception.code, "NO_WATCHES")

    def test_a_blocked_batch_is_not_a_dead_end_either(self):
        # Re-running it does not slowly "become" a success, and does not send.
        self.queued()
        first, one = self.run_cli()
        second, two = self.run_cli()
        self.assertEqual((first, second), (3, 3))
        self.assertEqual(one["state"], two["state"])
        self.assertEqual(self.sent(), [])
        self.assertFalse(self.ledger_path.exists())

    @unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
    def test_a_run_id_amended_into_the_file_turns_the_blocked_answer_into_a_tick(self):
        self.queued()
        code, doc = self.run_cli()
        self.assertEqual((code, doc["state"]), (3, "BLOCKED_INPUT"))

        for task_id, run_id, boot_id, delivery_id in (
                ("t-tokyo", RUN_A, BOOT_A, DELIVERY_A),
                ("t-dell", RUN_B, BOOT_B, DELIVERY_B),
                ("t-fjd", RUN_C, BOOT_C, DELIVERY_C)):
            registration.amend(self.path, task_id=task_id, run_id=run_id,
                               boot_id=boot_id, delivery_id=delivery_id,
                               at="2026-10-27T10:30:00+00:00")

        source_options = self.source_path(self.completed_script())
        code, doc = self.run_cli(source_options=source_options)

        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "SETTLED")
        self.assertEqual(doc["watches"], {"declared": 3, "issued": 3})
        self.assertEqual(doc["counts"], {"VERIFIED": 3})
        self.assertEqual([member["run_id"] for member in doc["members"]],
                         [RUN_A, RUN_B, RUN_C])
        # #428 decides the read order (its watches are keyed by source:ref);
        # this asserts the set and the count, not an order that is not ours.
        self.assertEqual(sorted(self.read_keys()),
                         sorted([f"cloudcli:{RUN_A}", f"cloudcli:{RUN_B}",
                                 f"cloudcli:{RUN_C}"]))


# --------------------------------------------------------------------------- #
# 3. the deadline, which is what bounds an unissued batch
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class TheDeadlineIsWhatClosesIt(Base):
    def test_past_the_deadline_the_unissued_batch_is_unobserved_not_complete(self):
        self.file(ack_rows=e2e.acks(runs=(None, None, None)), expires_at=PAST)

        code, doc = self.run_cli()

        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "SETTLED")
        self.assertTrue(doc["settled"])
        self.assertEqual(doc["counts"], {"UNOBSERVED": 3})
        self.assertEqual([member["verdict"] for member in doc["members"]],
                         ["UNOBSERVED", "UNOBSERVED", "UNOBSERVED"])
        self.assertEqual([member["code"] for member in doc["members"]],
                         ["NEVER_ISSUED"] * 3)

        # One bounded alert per unobserved member, plus the one summary.
        self.assertEqual(len(doc["alerts"]), 3)
        self.assertEqual(len(self.sent()), 4)
        self.assertEqual(len(self.batch_rows()), 4)

        summary = self.sent()[-1]["text"]
        self.assertIn("0/3 已验证", summary)
        self.assertIn(BATCH_ID, summary)
        for claim in ("1/3", "2/3", "3/3"):
            self.assertNotIn(claim, summary,
                             "an unissued dispatch may never count as verified")
        # The verdict word itself is in the alert, not only the label.
        self.assertIn("UNOBSERVED", self.sent()[0]["text"])

        # No watch was invented to get there: nothing was ever read.
        self.assertEqual(self.read_keys(), [])
        self.assertEqual(doc["watches"], {"declared": 3, "issued": 0})

    def test_the_unobserved_alerts_stay_bounded(self):
        devices = tuple((f"device:d{index}", "claude", f"t-{index}",
                         f"session:s{index}") for index in range(14))
        ack_rows = [{"task_id": f"t-{index}", "device_ref": f"device:d{index}",
                     "run_id": None, "delivery_id": f"om_queued_{index}"}
                    for index in range(14)]
        self.file(plan_value=e2e.plan(devices=devices), ack_rows=ack_rows,
                  expires_at=PAST)

        code, doc = self.run_cli()

        self.assertEqual(code, 0)
        self.assertEqual(doc["counts"], {"UNOBSERVED": 14})
        self.assertEqual(len(doc["alerts"]), batch.MAX_ALERTS)
        self.assertEqual(len(doc["suppressed"]), 14 - batch.MAX_ALERTS)
        # The cap is not silence: the remainder is named in the one summary.
        self.assertEqual(len(self.sent()), batch.MAX_ALERTS + 1)
        self.assertIn(f"另有 {14 - batch.MAX_ALERTS} 项未单独告警",
                      self.sent()[-1]["text"])

    def test_the_entrypoint_and_the_aggregator_agree_on_the_deadline(self):
        # cli.py restates #464's deadline rule rather than reach into a private
        # helper. Two statements of one rule drift unless something checks —
        # this is that check, on the tree where a notifier can be built.
        #
        # No `subTest`: under pytest a `unittest` subtest failure is reported as
        # SUBFAILED while the test itself is still counted as passed, which is a
        # green light for a run that established nothing.
        for deadline, expected in ((PAST, "UNOBSERVED"), (FUTURE, "PENDING")):
            self.path.unlink(missing_ok=True)
            filed = self.file(ack_rows=e2e.acks(runs=(None, None, None)),
                              expires_at=deadline)
            quiet = batch.BatchAggregator(filed.batch, notifier=self.notifier())
            agree = quiet.absorb_rows(()).members[0].verdict
            _, doc = self.run_cli()
            self.assertEqual(agree, expected, deadline)
            self.assertEqual(doc["members"][0]["verdict"], expected, deadline)
            self.assertEqual(doc["state"] == "BLOCKED_INPUT",
                             expected == "PENDING", deadline)


# --------------------------------------------------------------------------- #
# 4. an ACK, a refusal and a stranger's payload
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class NotEveryAnswerIsASuccess(Base):
    def test_an_ack_only_task_keeps_the_batch_open(self):
        rows = e2e.acks()
        rows[2]["ack_only"] = True
        self.file(ack_rows=rows)
        script = {f"cloudcli:{RUN_A}": [e2e.for_run("cloudcli", "completed",
                                                    run_id=RUN_A, device=DEV_A,
                                                    boot=BOOT_A)],
                  f"cloudcli:{RUN_B}": [e2e.for_run("cloudcli", "completed",
                                                    run_id=RUN_B, device=DEV_B,
                                                    boot=BOOT_B)]}

        code, doc = self.run_cli(source_options=self.source_path(script))

        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "TICKED")
        self.assertFalse(doc["settled"])
        self.assertIsNone(doc["summary"])
        self.assertEqual(doc["counts"], {"VERIFIED": 2, "PENDING": 1})
        last = doc["members"][2]
        self.assertEqual(last["task_id"], "t-fjd")
        self.assertEqual((last["verdict"], last["code"]),
                         ("PENDING", "NEVER_ISSUED"))
        self.assertIsNone(last["run_id"])
        self.assertEqual(len(self.batch_rows()), 0,
                         "an open batch has not settled and owes no summary")

    def test_a_run_not_found_read_stays_pending_and_is_read_once(self):
        self.file()
        code, doc = self.run_cli(
            source_options=self.source_path({}))  # every read refuses

        self.assertEqual(code, 0)
        self.assertEqual(doc["state"], "TICKED")
        self.assertFalse(doc["settled"])
        self.assertEqual(doc["counts"], {"PENDING": 3})
        self.assertEqual([member["code"] for member in doc["members"]],
                         ["RUN_NOT_FOUND"] * 3)
        self.assertEqual(sorted(self.read_keys()),
                         sorted([f"cloudcli:{RUN_A}", f"cloudcli:{RUN_B}",
                                 f"cloudcli:{RUN_C}"]),
                         "one invocation is one pass: a refusal is reported, "
                         "not silently retried")
        self.assertEqual(len(self.batch_rows()), 0)

    def test_a_payload_that_answers_for_another_boot_is_not_this_members_fact(self):
        self.file()
        script = self.completed_script()
        script[f"cloudcli:{RUN_C}"] = [
            e2e.for_run("cloudcli", "completed", run_id=RUN_C, device=DEV_C,
                        boot="boot-somebody-else")]

        code, doc = self.run_cli(source_options=self.source_path(script))

        self.assertEqual(code, 0)
        self.assertEqual(doc["counts"], {"VERIFIED": 2, "UNKNOWN": 1})
        third = doc["members"][2]
        self.assertEqual(third["verdict"], "UNKNOWN")
        self.assertIn(third["code"],
                      {"BOOT_SCOPE_MISMATCH", "DEVICE_SCOPE_MISMATCH",
                       "REF_MISMATCH", "SOURCE_SHA_MISMATCH"})
        self.assertEqual(third["boot_id"], BOOT_C,
                         "the member keeps its own boot id; the payload's is not adopted")


# --------------------------------------------------------------------------- #
# 5. two processes, one ledger
# --------------------------------------------------------------------------- #
class TwoProcessesShareOneLedger(Base):
    def settle_once(self):
        self.file()
        return self.run_process(source_options=self.source_path(
            self.completed_script()))

    @unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
    def test_the_second_process_learns_the_delivery_from_the_ledger(self):
        code, first, _ = self.settle_once()
        self.assertEqual((code, first["state"]), (0, "SETTLED"))
        self.assertEqual(first["summary"]["state"], "DELIVERED")
        self.assertEqual(len(self.sent()), 4,
                         "three milestone messages and the one summary")

        code, second, _ = self.run_process(
            source_options=self.source_path(self.completed_script()))

        self.assertEqual(code, 0)
        self.assertEqual(second["state"], "SETTLED")
        self.assertEqual(second["summary"]["state"], "DUPLICATE")
        self.assertEqual(second["summary"]["remote_id"],
                         first["summary"]["remote_id"])
        self.assertEqual(len(self.sent()), 4,
                         "a restarted process reads the receipt; it does not send")

    @unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
    def test_the_third_process_neither_resends_nor_invents_a_delivery(self):
        first = self.settle_once()[1]
        second = self.run_process(
            source_options=self.source_path(self.completed_script()))[1]
        third = self.run_process(
            source_options=self.source_path(self.completed_script()))[1]

        self.assertEqual(len(self.sent()), 4)
        self.assertEqual({second["summary"]["remote_id"],
                          third["summary"]["remote_id"]},
                         {first["summary"]["remote_id"]})
        # One `batch:` row — the summary. The three milestone messages carry
        # #423's own milestone keys, which is why a reader of the ledger can
        # never mistake a summary for a milestone.
        self.assertEqual(len(self.batch_rows()), 1)
        self.assertEqual(self.batch_rows()[0]["delivery_key"],
                         f"batch:{BATCH_ID}:summary")

    @unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
    def test_the_five_identities_are_never_folded_into_one(self):
        _, doc, _ = self.settle_once()

        for member in doc["members"]:
            values = [member[key] for key in
                      ("task_id", "session_ref", "run_id", "delivery_id",
                       "boot_id")]
            self.assertTrue(all(isinstance(value, str) and value
                                for value in values), values)
            self.assertEqual(len(set(values)), 5,
                             f"two identities share a value: {values}")
        self.assertEqual([member["run_id"] for member in doc["members"]],
                         [RUN_A, RUN_B, RUN_C])
        self.assertEqual([member["session_ref"] for member in doc["members"]],
                         [SESSION_A, SESSION_B, SESSION_C])
        self.assertNotIn(doc["summary"]["remote_id"],
                         {member["delivery_id"] for member in doc["members"]},
                         "the provider's message id is not one of the dispatch's ids")

    @unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
    def test_a_provider_unknown_is_never_recorded_as_delivered(self):
        self.file()
        options = self.provider_path(
            receipts=[None, None, None,
                      {"delivered": False, "why": "rate limited upstream"}])
        code, first, _ = self.run_process(
            provider_options=options,
            source_options=self.source_path(self.completed_script()))

        self.assertEqual(code, 4, "an unconfirmed summary is not a clean run")
        self.assertEqual(first["state"], "UNCONFIRMED")
        self.assertEqual(first["summary"]["state"], "DELIVERY_UNKNOWN")
        self.assertFalse(first["summary"]["delivered"])
        self.assertIsNone(first["summary"]["remote_id"])
        summary_row = [row for row in self.batch_rows()
                       if row["delivery_key"] == f"batch:{BATCH_ID}:summary"][0]
        self.assertEqual(summary_row["state"], m.UNKNOWN_DELIVERY,
                         "#423's row, not the entrypoint's word, is the record")
        self.assertIsNone(summary_row["remote_id"])

        sent_before = len(self.sent())
        code, second, _ = self.run_process(
            source_options=self.source_path(self.completed_script()))

        self.assertEqual(code, 4)
        self.assertEqual(second["state"], "UNCONFIRMED")
        self.assertEqual(len(self.sent()), sent_before,
                         "an UNKNOWN is never re-sent under the same key")
        summary_row = [row for row in self.batch_rows()
                       if row["delivery_key"] == f"batch:{BATCH_ID}:summary"][0]
        self.assertEqual(summary_row["state"], m.UNKNOWN_DELIVERY)

    @unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
    def test_the_provider_is_closed_after_one_tick(self):
        self.settle_once()
        self.assertEqual(
            self.closed_log.read_text(encoding="utf-8").splitlines(), ["closed"],
            "one tick holds exactly one provider, and it is closed on the way out")

    @unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
    def test_446_s_send_rate_limit_never_becomes_a_delivery(self):
        self.file()
        code, doc = self.run_cli(
            max_sends=1,
            source_options=self.source_path(self.completed_script()))

        self.assertEqual(len(self.sent()), 1, "the limit bounds the provider")
        self.assertEqual(code, 4)
        self.assertEqual(doc["state"], "UNCONFIRMED")
        self.assertEqual(doc["summary"]["state"], "DELIVERY_UNKNOWN")


class TheAnswerIsOneDocumentOnTheWire(Base):
    def test_the_document_is_utf8_through_a_real_pipe_whatever_the_locale(self):
        self.queued()
        code, doc, raw = self.run_process()

        self.assertEqual((code, doc["state"]), (3, "BLOCKED_INPUT"))
        self.assertTrue(any(ord(character) > 127 for character in raw),
                        "the document is not pure ASCII (an em dash in `why`); "
                        "a locale-encoded pipe would deliver different bytes "
                        "depending on the machine that ran it")


if __name__ == "__main__":
    unittest.main()
