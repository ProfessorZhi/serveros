#!/usr/bin/env python3
"""#417 — the REAL cross-PR chain: #456 plan -> #464 registration -> #428 tick
-> #460 `send_text` -> #423 ledger -> one provider call.

`test_fleet_v1_milestone_batch_integration.py` (on #464) proves the composition
with a **sealed stub** for the one method #423 did not have. This file proves
the same wire with the method that now exists, owned by someone else:

    integration under test      exact SHA              owned by
    ----------------------------------------------------------------
    milestone_notify.send_text  4fc9fcbc (#460, #423 stack)  Dell lane
    observer.run_once()         dde95777 (#428)            #428 lane
    batch/registration          d72befe7 (#464)            this lane
    runner config (read only)   9b1b1ad1 (#446)            #446 lane

There is no notifier subclass anywhere in this file: `m.MilestoneNotifier` is
constructed directly, and `test_the_notifier_is_423_s_own_class_not_a_subtype`
asserts the type. `FakeLark` is only the *injected provider* — the same seam
#423 already takes at construction — so nothing is sent to a real IM.

WHY THIS FILE IS SEPARATE
-------------------------
On this lane's own base (#464, stacked on #459) `send_text` does not exist, so
every test here that sends is RED with `NOTIFIER_SEAM_MISSING` — exactly the
upfront refusal the aggregator is supposed to make. Green requires #460 in the
tree. Evidence for both sides is recorded in the PR.
"""
from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BATCH_DIR = REPO / "integrations" / "fleet-v1-milestone-batch"
OBSERVER_DIR = REPO / "integrations" / "fleet-v1-milestone-observer"
RUNNER_DIR = REPO / "integrations" / "fleet-v1-milestone-runner"
FIXTURES = OBSERVER_DIR / "fixtures"

CAPABILITY = "human.notification.transport"
RECIPIENT = "chatgpt:conv-2026-10-28"

DEV_A, BOOT_A = "device:tokyo-shared-dev-01", "boot-tokyo-1"
DEV_B, BOOT_B = "device:dell-5820", "boot-dell-1"
DEV_C, BOOT_C = "device:win-fjd", "boot-win-1"
RUN_A, RUN_B, RUN_C = "run_demo_1", "run_dell_1", "run_fjd_1"
SESSION_A, SESSION_B, SESSION_C = ("session:tokyo-alpha", "session:dell-beta",
                                   "session:fjd-gamma")
DELIVERY_A, DELIVERY_B, DELIVERY_C = "om_dispatch_1", "om_dispatch_2", "om_dispatch_3"

BATCH_ID = "dispatch-2026-10-28-e2e"
DEADLINE = "2026-10-27T11:00:00Z"
REGISTERED_AT = "2026-10-27T10:00:00+00:00"
START = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc).timestamp()


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


observer = _load("serveros_fleet_v1_milestone_observer",
                 OBSERVER_DIR / "observer.py")
m = observer.NOTIFY  # #423's module (with #460's send_text when it is in tree)
batch = _load("serveros_fleet_v1_milestone_batch", BATCH_DIR / "batch.py")
registration = _load("serveros_fleet_v1_milestone_batch_registration",
                     BATCH_DIR / "registration.py")

#: #460 is the seam. Its absence is what the RED half of this PR records.
HAS_SEAM = hasattr(m.MilestoneNotifier, "send_text")


def dispatch_module():
    """Lazily imported: the loader is what this PR adds, so it must be RED first."""
    return _load("serveros_fleet_v1_milestone_batch_dispatch",
                 BATCH_DIR / "dispatch.py")


# --------------------------------------------------------------------------- #
# the cast: an injected provider and scripted reads. Nothing else.
# --------------------------------------------------------------------------- #
class Clock:
    def __init__(self, at: float = START):
        self.at = float(at)

    def __call__(self) -> float:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += float(seconds)


class FakeLark:
    """The one injected provider. No CLI, no socket, no token, no real IM."""

    provider_id = "provider:fake-lark"
    transport = "fake"

    def __init__(self, receipts=None, capability: str = "SUPPORTED"):
        self.receipts = list(receipts or [])
        self.capability = capability
        self.calls = []
        self.lock = threading.Lock()

    def capabilities(self):
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {CAPABILITY: {"state": self.capability}}}

    def notify(self, *, conversation_ref, text, idempotency_key):
        with self.lock:
            self.calls.append({"conversation_ref": conversation_ref,
                               "text": text,
                               "idempotency_key": idempotency_key})
            receipt = self.receipts.pop(0) if self.receipts else None
        if isinstance(receipt, BaseException):
            raise receipt
        if receipt is not None:
            return receipt
        return {"delivered": True, "remote_id": f"om_{len(self.calls)}",
                "provider": self.provider_id, "transport": self.transport}


class SourceRefused(Exception):
    def __init__(self, code: str):
        self.code = code
        self.detail = code
        super().__init__(code)


class ScriptedSource:
    def __init__(self, script: dict):
        self.script = {key: list(value) if isinstance(value, list) else [value]
                       for key, value in script.items()}
        self.calls = []

    def read(self, source: str, ref: str) -> dict:
        key = f"{source}:{ref}"
        self.calls.append(key)
        answers = self.script.get(key)
        if not answers:
            raise SourceRefused("RUN_NOT_FOUND")
        value = answers.pop(0) if len(answers) > 1 else answers[0]
        if isinstance(value, BaseException):
            raise value
        return copy.deepcopy(value)


def fixture(source: str, name: str) -> dict:
    return json.loads((FIXTURES / source / f"{name}.json").read_text(encoding="utf-8"))


def for_run(source: str, name: str, *, run_id: str, device: str, boot: str,
            **changes) -> dict:
    value = copy.deepcopy(fixture(source, name))
    value["device_ref"] = device
    if "bound_boot_id" in value:
        value["bound_boot_id"] = boot
    node = value.get("run") or value.get("workflow_run") or {}
    if "runId" in node:
        node["runId"] = run_id
    if "bootId" in node:
        node["bootId"] = boot
    value.update(changes)
    return value


class Commander:
    """#456's contract shape (#456 is not merged; the seam is the call itself)."""

    def validate_batch(self, plan, snapshot):
        return {"verdict": "PASS", "batch_id": plan.get("batch_id"),
                "task_count": len(plan.get("tasks") or []), "blocked": []}


def plan(*, devices=None) -> dict:
    devices = devices or ((DEV_A, "claude", "t-tokyo", SESSION_A),
                          (DEV_B, "codex", "t-dell", SESSION_B),
                          (DEV_C, "claude", "t-fjd", SESSION_C))
    return {"batch_id": BATCH_ID, "tasks": [
        {"task_id": task_id, "device_ref": device, "runtime": runtime,
         "project_id": "serveros", "worktree": f"/w/{task_id}",
         "scope": "integrations/fleet-v1-milestone-batch", "acceptance": "a",
         "tests": "b", "report": "c", "idempotency_key": f"idem-{task_id}",
         "session_ref": session}
        for device, runtime, task_id, session in devices]}


def acks(*, runs=(RUN_A, RUN_B, RUN_C), deliveries=(DELIVERY_A, DELIVERY_B,
                                                   DELIVERY_C),
         boots=(BOOT_A, BOOT_B, BOOT_C), devices=None) -> list:
    devices = devices or ((DEV_A, "t-tokyo"), (DEV_B, "t-dell"),
                          (DEV_C, "t-fjd"))
    return [{"task_id": devices[index][1], "device_ref": devices[index][0],
             "run_id": runs[index], "delivery_id": deliveries[index],
             "boot_id": boots[index]} for index in range(len(runs))]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-batch-e2e-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger_path = self.tmp / "milestones.sqlite"
        self.state_path = self.tmp / "observer-state.json"
        self.lock_path = self.tmp / "runner.lock"
        self.directory = self.tmp / "batches"
        self.path = registration.path_for(self.directory, BATCH_ID)
        self.clock = Clock()
        self.provider = FakeLark()

    # -- the dispatch ------------------------------------------------------

    def file(self, *, ack_rows=None, plan_value=None, expires_at=DEADLINE):
        filed, outcome = registration.from_commander(
            plan_value or plan(),
            ack_rows if ack_rows is not None else acks(),
            contract=Commander(), snapshot={"devices": {}}, source="cloudcli",
            registered_at=REGISTERED_AT, conversation_ref=RECIPIENT,
            expires_at=expires_at, path=self.path)
        self.assertEqual(outcome, registration.REGISTERED)
        return filed

    # -- the real notifier, #460's method included -------------------------

    def notifier(self, *, provider=None, ledger=None, clock=None,
                 conversation_ref=RECIPIENT):
        return m.MilestoneNotifier(provider or self.provider,
                                   ledger or self.ledger_path,
                                   conversation_ref=conversation_ref,
                                   summarizer=None, clock=clock or self.clock)

    def observer_for(self, filed, source, *, notifier=None, options=None):
        return observer.MilestoneObserver(
            source=source, notifier=notifier or self.notifier(),
            state_path=self.state_path, watches=filed.batch.watches(),
            clock=self.clock, options=options or observer.Options())

    def aggregator(self, filed, *, notifier=None):
        return batch.BatchAggregator(filed.batch,
                                     notifier=notifier or self.notifier(),
                                     clock=self.clock)

    def step(self, agg, instance, *, times=1):
        out = []
        for _ in range(times):
            out.append(agg.absorb(instance.run_once()))
            self.clock.advance(60.0)
        return out

    def all_completed(self):
        return ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_B}": for_run("cloudcli", "completed", run_id=RUN_B,
                                         device=DEV_B, boot=BOOT_B),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C)})

    # -- reads -------------------------------------------------------------

    def texts(self):
        return [call["text"] for call in self.provider.calls]

    def rows(self):
        db = sqlite3.connect(self.ledger_path)
        db.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in db.execute(
                "SELECT * FROM milestone_deliveries ORDER BY rowid")]
        finally:
            db.close()

    def batch_rows(self):
        return [row for row in self.rows()
                if str(row["delivery_key"]).startswith("batch:")]

    def summary_key(self, filed):
        return filed.batch.summary_key

    def store_names(self):
        """The durable stores only: no registration file, no SQLite sidecar."""
        return {path.name for path in self.tmp.rglob("*")
                if path.is_file() and path.name.endswith((".sqlite", ".json"))}


# --------------------------------------------------------------------------- #
# 0. the gate: this file's whole premise, asserted on BOTH trees
# --------------------------------------------------------------------------- #
class TheSeamIsTheOnlyGate(Base):
    """Runs unconditionally, so the RED side is a fact and not a skip.

    Without #460 the real notifier is refused up front (`NOTIFIER_SEAM_MISSING`)
    and every sending test below is skipped — the honest RED for this PR. With
    #460 the same construction is accepted and the chain is exercised.
    """

    def test_the_real_notifier_is_accepted_exactly_when_460_is_in_the_tree(self):
        filed = self.file()
        real = self.notifier()
        self.assertIs(type(real), m.MilestoneNotifier)

        if HAS_SEAM:
            self.assertTrue(callable(real.send_text))
            batch.BatchAggregator(filed.batch, notifier=real, clock=self.clock)
            return
        self.assertFalse(hasattr(real, "send_text"),
                         "#460 has landed; delete the BLOCKED note in the README")
        with self.assertRaises(batch.BatchRefused) as caught:
            batch.BatchAggregator(filed.batch, notifier=real, clock=self.clock)
        self.assertEqual(caught.exception.code, "NOTIFIER_SEAM_MISSING")
        self.assertEqual(self.provider.calls, [], "a refusal sends nothing")


class NoTwoTasksShareOneRun(Base):
    """Runs on both trees: this one is #464's own refusal, composed here."""

    def test_a_run_id_declared_for_two_tasks_never_reaches_a_tick(self):
        # Two tasks on one ref would let "2/2" stand for a Run that only ever
        # answered for one of them. The declaration refuses it by name, before
        # any watch, tick or message exists.
        with self.assertRaises(batch.BatchRefused) as caught:
            self.file(ack_rows=acks(runs=(RUN_A, RUN_A, RUN_C)))
        self.assertEqual(caught.exception.code, "DUPLICATE_REF")
        self.assertIn(RUN_A, caught.exception.why)
        self.assertEqual(self.provider.calls, [])
        self.assertFalse(self.ledger_path.exists(),
                         "a refused declaration must not open a ledger")


# --------------------------------------------------------------------------- #
# 1. the notifier is #423's, and its seam is the real one
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class TheRealNotifierIsTheOneThatSends(Base):
    def test_the_notifier_is_423_s_own_class_not_a_subtype(self):
        notifier = self.notifier()
        self.assertIs(type(notifier), m.MilestoneNotifier,
                      "the chain must use #423's class — a subtype here would "
                      "be a Fake-only notifier and would prove nothing")
        self.assertTrue(callable(getattr(notifier, "send_text", None)))

    def test_the_seam_refuses_a_milestone_word_outcome(self):
        # #460's closed set is the mechanical half of "a batch must not pretend
        # to be a milestone": the batch words and MILESTONE_KINDS are disjoint.
        self.assertIn(batch.BATCH_SUMMARY, m.NON_MILESTONE_OUTCOMES)
        self.assertIn(batch.BATCH_ALERT, m.NON_MILESTONE_OUTCOMES)
        self.assertEqual(set(m.NON_MILESTONE_OUTCOMES)
                         & set(m.MILESTONE_KINDS), set())
        for word in sorted(m.MILESTONE_KINDS):
            with self.subTest(word=word), self.assertRaises(m.MilestoneRefused):
                self.notifier().send_text(
                    delivery_key=f"batch:{BATCH_ID}:forged",
                    milestone_key=f"batch:{BATCH_ID}:summary",
                    outcome=word, text="a summary pretending to be a milestone",
                    why="unit test")
        self.assertEqual(self.provider.calls, [], "a refusal must not send")

    def test_the_ledger_row_is_423_s_shape_with_a_50_char_transport_key(self):
        filed = self.file()
        self.aggregator(filed).absorb(self.observer_for(
            filed, self.all_completed()).run_once())

        rows = self.batch_rows()
        self.assertEqual(len(rows), 1)
        row = rows[0]
        self.assertEqual(row["delivery_key"], self.summary_key(filed))
        self.assertEqual(row["outcome"], m.BATCH_SUMMARY)
        self.assertEqual(row["state"], m.DELIVERED)
        self.assertTrue(str(row["remote_id"]).startswith("om_"))
        self.assertEqual(row["transport_key"],
                         m.transport_key_for(self.summary_key(filed)))
        self.assertLessEqual(len(row["transport_key"]), 50)
        # The provider saw exactly that digest, not the full identity.
        self.assertEqual(self.provider.calls[-1]["idempotency_key"],
                         row["transport_key"])
        self.assertNotEqual(self.provider.calls[-1]["idempotency_key"],
                            self.summary_key(filed))

    def test_an_unknown_delivery_is_reconciled_under_the_same_key_not_resent(self):
        # The provider refuses the SUMMARY (the three milestone messages before
        # it are fine). #423 must record UNKNOWN, not DELIVERED, and the
        # recovery must reuse the SAME delivery key.
        self.provider = FakeLark(receipts=[None, None, None,
                                           {"delivered": False,
                                            "why": "rate limited by the provider"}])
        filed = self.file()
        notifier = self.notifier()
        agg = self.aggregator(filed, notifier=notifier)
        first = agg.absorb(self.observer_for(filed, self.all_completed(),
                                             notifier=notifier).run_once())

        self.assertTrue(first.settled)
        row = self.batch_rows()[0]
        self.assertEqual(row["state"], m.UNKNOWN_DELIVERY)
        self.assertIsNone(row["remote_id"])
        self.assertIn("rate limited", str(row["why"]))
        self.assertFalse(first.summary.delivered)

        # The provider recovers; #423 re-asks under the same key.
        recovered = notifier.reconcile(self.summary_key(filed))
        self.assertEqual(recovered.state, m.DELIVERED)
        self.assertTrue(recovered.delivered)
        self.assertEqual(len(self.batch_rows()), 1, "reconcile may not add a row")
        self.assertEqual(self.batch_rows()[0]["state"], m.DELIVERED)
        self.assertEqual(self.provider.calls[-1]["idempotency_key"],
                         m.transport_key_for(self.summary_key(filed)))

        # And a later tick must not send again: the ledger now answers DELIVERED.
        again = agg.absorb(self.observer_for(filed, self.all_completed(),
                                             notifier=notifier).run_once())
        self.assertEqual(again.state, batch.REPLAY)
        self.assertEqual(len(self.provider.calls), 5,
                         "3 milestones + one refused attempt + one reconcile, "
                         "and nothing else")


# --------------------------------------------------------------------------- #
# 2. the owner's scenario, through the real seam
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class ARegisteredDispatchIsOneSummary(Base):
    def test_three_devices_all_verified_produce_exactly_one_summary(self):
        filed = self.file()
        tick = self.aggregator(filed).absorb(
            self.observer_for(filed, self.all_completed()).run_once())

        self.assertTrue(tick.settled)
        self.assertEqual(tick.state, batch.SENT)
        self.assertEqual(tick.verified, 3)
        self.assertEqual(len(self.provider.calls), 4,
                         "3 milestone messages (#423) + 1 summary (#460)")
        self.assertIn("3/3", self.texts()[-1])
        self.assertEqual(len(self.batch_rows()), 1)
        self.assertTrue(tick.summary.delivered)

    def test_the_summary_carries_the_provider_receipt(self):
        filed = self.file()
        tick = self.aggregator(filed).absorb(
            self.observer_for(filed, self.all_completed()).run_once())

        receipt = tick.receipts[0]
        self.assertEqual(receipt.delivery_key, self.summary_key(filed))
        self.assertEqual(receipt.state, m.DELIVERED)
        self.assertEqual(receipt.remote_id, self.batch_rows()[0]["remote_id"])
        self.assertEqual(receipt.remote_id, f"om_{len(self.provider.calls)}")
        self.assertTrue(receipt.confirmed)


# --------------------------------------------------------------------------- #
# 3. mixed failure / UNKNOWN: real reasons, bounded alerts
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class AMixedDispatchTellsTheTruth(Base):
    def test_real_reasons_reach_the_summary_and_alerts_stay_bounded(self):
        fourth = "device:llm-lab-dell"
        run_fourth = "run_lab_1"
        dispatch = ((DEV_A, "claude", "t-tokyo", SESSION_A),
                    (DEV_B, "codex", "t-dell", SESSION_B),
                    (DEV_C, "claude", "t-fjd", SESSION_C),
                    (fourth, "claude", "t-lab", "session:lab-delta"))
        filed = self.file(
            plan_value=plan(devices=dispatch),
            ack_rows=acks(runs=(RUN_A, RUN_B, RUN_C, run_fourth),
                          deliveries=(DELIVERY_A, DELIVERY_B, DELIVERY_C, "om_4"),
                          boots=(BOOT_A, BOOT_B, BOOT_C, "boot-dell-2"),
                          devices=tuple((row[0], row[2]) for row in dispatch)))
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_B}": for_run("cloudcli", "failed", run_id=RUN_B,
                                         device=DEV_B, boot=BOOT_B),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed-no-output",
                                         run_id=RUN_C, device=DEV_C,
                                         boot=BOOT_C),
            # Another boot's payload is never this task's fact.
            f"cloudcli:{run_fourth}": for_run("cloudcli", "completed",
                                              run_id=run_fourth, device=fourth,
                                              boot=BOOT_A)})

        tick = self.aggregator(filed).absorb(
            self.observer_for(filed, source).run_once())

        self.assertTrue(tick.settled)
        self.assertEqual(tick.counts[batch.VERIFIED], 1)
        self.assertEqual(tick.counts[batch.FAILED], 1)
        self.assertEqual(tick.counts[batch.UNKNOWN], 2)
        self.assertEqual(len(tick.alerts), 1, "only the unobservable member")
        self.assertEqual(len(self.provider.calls), 5,
                         "3 milestones + 1 alert + 1 summary")

        summary = self.texts()[-1]
        for run_id in (RUN_A, RUN_B, RUN_C, run_fourth):
            self.assertIn(run_id, summary)
        self.assertIn("1/4", summary)
        # The alert names the one member nobody could observe, by ref.
        self.assertIn(run_fourth, self.texts()[-2])
        self.assertIn("批次", self.texts()[-2])
        # The summary repeats #423's closed codes, never a payload sentence.
        self.assertNotIn("the deliverable is ready", summary)

    def test_a_payload_from_another_boot_never_verifies_a_member(self):
        filed = self.file()
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_B}": for_run("cloudcli", "completed", run_id=RUN_B,
                                         device=DEV_B, boot=BOOT_A),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C)})
        tick = self.aggregator(filed).absorb(
            self.observer_for(filed, source).run_once())

        verdicts = {state.member.ref: state.verdict for state in tick.members}
        self.assertEqual(verdicts[RUN_B], batch.UNKNOWN)
        self.assertEqual(tick.verified, 2)
        self.assertIn("2/3", self.texts()[-1])


# --------------------------------------------------------------------------- #
# 4. queued / ACK-only / RUN_NOT_FOUND are never green
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class AQueuedTaskIsNeverGreen(Base):
    def test_ack_only_and_run_not_found_never_verify(self):
        # tokyo ACK-only, dell dispatched but not yet found, fjd green.
        filed = self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C)})
        agg = self.aggregator(filed)
        instance = self.observer_for(filed, source)

        ticks = self.step(agg, instance, times=3)
        for tick in ticks:
            self.assertFalse(tick.settled, "one queued task keeps it open")
            self.assertIsNone(tick.summary)
        verdicts = {state.member.task_id: state.verdict
                    for state in ticks[-1].members}
        self.assertEqual(verdicts["t-dell"], batch.PENDING)
        self.assertEqual(self.batch_rows(), [])
        self.assertEqual(len(self.provider.calls), 2,
                         "only the two Runs that really finished")

    def test_a_queued_member_is_not_merged_into_another_member_s_run(self):
        # The queued task must not be handed the next Run that appears.
        filed = self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        members = {member.task_id: member for member in filed.batch.members}
        self.assertIsNone(members["t-dell"].ref)
        self.assertEqual(members["t-dell"].device_ref, DEV_B)

        amended, outcome = registration.amend(
            self.path, task_id="t-dell", run_id=RUN_B, boot_id=BOOT_B,
            delivery_id="om_dispatch_2b", at="2026-10-27T10:02:00+00:00")
        self.assertEqual(outcome, registration.REGISTERED)
        after = {member.task_id: member for member in amended.batch.members}
        self.assertEqual(after["t-dell"].ref, RUN_B)
        self.assertEqual(after["t-tokyo"].ref, RUN_A)
        self.assertEqual(after["t-fjd"].ref, RUN_C)

        # The other two columns moved with it and did not become the Run.
        filed_rows = {row["task_id"]: row for row in
                      registration.load(self.path).document()["members"]}
        self.assertEqual(filed_rows["t-dell"]["run_id"], RUN_B)
        self.assertEqual(filed_rows["t-dell"]["delivery_id"], "om_dispatch_2b")
        self.assertEqual(filed_rows["t-tokyo"]["delivery_id"], DELIVERY_A)


# --------------------------------------------------------------------------- #
# 5. no tick, restart or concurrent claim sends twice
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class NoTickSendsTwice(Base):
    def test_a_second_tick_on_the_same_registration_sends_nothing(self):
        filed = self.file()
        notifier = self.notifier()
        agg = self.aggregator(filed, notifier=notifier)
        self.step(agg, self.observer_for(filed, self.all_completed(),
                                         notifier=notifier), times=1)
        sent = len(self.provider.calls)

        again = self.step(agg, self.observer_for(filed, self.all_completed(),
                                                 notifier=notifier), times=3)
        self.assertEqual(len(self.provider.calls), sent)
        self.assertTrue(all(tick.state == batch.REPLAY for tick in again))
        self.assertEqual(len(self.batch_rows()), 1)

    def test_a_restart_from_the_file_sends_nothing(self):
        filed = self.file()
        self.step(self.aggregator(filed),
                  self.observer_for(filed, self.all_completed()), times=1)
        sent = len(self.provider.calls)

        reloaded = registration.load(self.path)
        self.assertEqual(reloaded.batch.origin, batch.ORIGIN_REGISTRATION)
        tick = self.step(self.aggregator(reloaded),
                         self.observer_for(reloaded, self.all_completed()),
                         times=1)[0]

        self.assertEqual(tick.state, batch.REPLAY)
        self.assertEqual(len(self.provider.calls), sent)
        self.assertEqual(tick.receipts[0].state, m.DUPLICATE)
        self.assertTrue(tick.receipts[0].remote_id,
                        "the replay names the delivered receipt")

    def test_two_instances_racing_one_key_send_once(self):
        # The claim is the INSERT: the ledger is the only arbiter, so this is
        # the same code path a second replica would take.
        filed = self.file()
        key = self.summary_key(filed)
        transport = m.transport_key_for(key)
        barrier = threading.Barrier(2)
        answers = []
        failures = []
        # Both notifiers exist before the race: what must be arbitrated is the
        # CLAIM, not SQLite's schema creation.
        notifiers = [self.notifier() for _ in range(2)]

        def send(notifier):
            try:
                barrier.wait(timeout=10)
                answers.append(notifier.send_text(
                    delivery_key=key, milestone_key=key,
                    outcome=m.BATCH_SUMMARY, text="one summary, two claimants",
                    why="concurrency unit test"))
            except BaseException as exc:  # surfaced below, never swallowed
                failures.append(exc)

        threads = [threading.Thread(target=send, args=(notifier,))
                   for notifier in notifiers]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)

        self.assertEqual(failures, [])
        self.assertEqual(len(answers), 2)
        self.assertEqual(len(self.provider.calls), 1,
                         "two claimants must not both reach the provider")
        # The loser of the claim is told one of two things, and both are
        # #423's own: DUPLICATE if the winner had already finished, or
        # UNKNOWN_REPLAY if it is still IN_FLIGHT. Neither is a second send,
        # and neither may be reported to a person as a fresh delivery.
        states = sorted(answer.state for answer in answers)
        self.assertEqual(states.count(m.DELIVERED), 1, states)
        self.assertIn([state for state in states if state != m.DELIVERED],
                      ([m.DUPLICATE], [m.UNKNOWN_REPLAY]))
        # `delivered=True` on a DUPLICATE answer means "this fact is delivered",
        # not "this call delivered it" — so both answers may carry it while the
        # provider was reached exactly once, which is what the count above pins.
        self.assertEqual(len(self.batch_rows()), 1)
        self.assertEqual(self.batch_rows()[0]["transport_key"], transport)


# --------------------------------------------------------------------------- #
# 6. a refusal is not a delivery
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class ARefusalIsNotADelivery(Base):
    def test_a_provider_exception_is_unknown_not_delivered(self):
        # The summary's send raises; the three milestone messages before it do
        # not. A claim must never be left IN_FLIGHT or read as delivered.
        self.provider = FakeLark(receipts=[None, None, None,
                                           RuntimeError("transport exploded")])
        filed = self.file()
        tick = self.aggregator(filed).absorb(
            self.observer_for(filed, self.all_completed()).run_once())

        self.assertTrue(tick.settled, "settlement is by outcome, not delivery")
        row = self.batch_rows()[0]
        self.assertEqual(row["state"], m.UNKNOWN_DELIVERY)
        self.assertIn("RuntimeError", str(row["why"]))
        self.assertFalse(tick.summary.delivered)
        self.assertEqual(tick.receipts[0].state, m.UNKNOWN_DELIVERY)

    def test_an_unsupported_transport_is_unknown_and_sends_nothing(self):
        self.provider = FakeLark(capability="UNSUPPORTED")
        filed = self.file()
        tick = self.aggregator(filed).absorb(
            self.observer_for(filed, self.all_completed()).run_once())

        self.assertEqual(self.provider.calls, [],
                         "an UNSUPPORTED probe must not reach the provider")
        self.assertEqual(self.batch_rows()[0]["state"], m.UNKNOWN_DELIVERY)
        self.assertIn("UNSUPPORTED", str(self.batch_rows()[0]["why"]))
        self.assertFalse(tick.summary.delivered)


# --------------------------------------------------------------------------- #
# 7. the loader #446 can be configured from
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class TheRunnerAdapterIsOnlyConfig(Base):
    def runner(self):
        return _load("serveros_fleet_v1_milestone_runner",
                     RUNNER_DIR / "runner.py")

    def document(self, filed, **changes):
        values = {"ledger_path": str(self.ledger_path),
                  "state_path": str(self.state_path),
                  "lock_path": str(self.lock_path),
                  "conversation_ref": RECIPIENT, "max_ticks": 1}
        values.update(changes)
        return dispatch_module().runner_document(filed, **values)

    def test_the_document_validates_through_446_s_closed_schema(self):
        filed = self.file()
        document = self.document(filed)
        self.assertEqual(document["schema"],
                         self.runner().SCHEMA)

        # #446's own validator, not a re-implementation of it.
        config = self.runner().RunnerConfig.from_dict(document)
        self.assertEqual(len(config.watches), 3)
        self.assertEqual([watch.ref for watch in config.watches],
                         [RUN_A, RUN_B, RUN_C])
        self.assertEqual({watch.device_ref for watch in config.watches},
                         {DEV_A, DEV_B, DEV_C})
        self.assertEqual(config.max_ticks, 1)
        self.assertIsNone(config.max_runtime_seconds)

    def test_a_queued_member_never_becomes_a_watch(self):
        filed = self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        document = self.document(filed)
        refs = [item["ref"] for item in document["watches"]]
        self.assertEqual(refs, [RUN_A, RUN_C])
        self.assertNotIn("t-dell", json.dumps(document),
                         "a queued task has no ref to watch")
        config = self.runner().RunnerConfig.from_dict(document)
        self.assertEqual(len(config.watches), 2)

    def test_the_document_is_bounded_and_carries_no_credential(self):
        filed = self.file()
        document = self.document(filed, max_ticks=5)
        blob = json.dumps(document, ensure_ascii=False)
        for forbidden in ("token", "secret", "password", "api_key", "webhook"):
            self.assertNotIn(forbidden, blob.lower())
        self.assertEqual(document["max_ticks"], 5)
        for key in ("ledger_path", "state_path", "lock_path"):
            self.assertTrue(Path(document[key]).is_absolute())

    def test_the_document_refuses_an_unbounded_lifecycle(self):
        filed = self.file()
        with self.assertRaises(batch.BatchRefused) as caught:
            self.document(filed, max_ticks=None)
        self.assertEqual(caught.exception.code, "UNBOUNDED_LIFECYCLE")



# --------------------------------------------------------------------------- #
# 8. one bounded tick, and restart from the file
# --------------------------------------------------------------------------- #
@unittest.skipUnless(HAS_SEAM, "RED: #460 send_text is not in this tree")
class AOneShotTickNeedsOnlyTheFileAndTheLedger(Base):
    def test_one_tick_from_the_file_sends_one_summary(self):
        self.file()
        loaded, tick = dispatch_module().tick_from_file(
            self.path, source=self.all_completed(), notifier=self.notifier(),
            state_path=self.state_path, clock=self.clock)

        self.assertEqual(loaded.batch.batch_id, BATCH_ID)
        self.assertTrue(tick.settled)
        self.assertEqual(len(self.provider.calls), 4)
        self.assertEqual(len(self.batch_rows()), 1)

    def test_a_second_one_shot_process_sees_an_amendment_made_between_them(self):
        # "重启能恢复" for the one-shot path: the second process must read the
        # file, not remember what the first one did. A cache here would keep
        # reporting a queued task for ever.
        self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        _, first = dispatch_module().tick_from_file(
            self.path, source=self.all_completed(), notifier=self.notifier(),
            state_path=self.state_path, clock=self.clock)
        self.assertFalse(first.settled)
        self.assertEqual(first.members[1].verdict, batch.PENDING)

        registration.amend(self.path, task_id="t-dell", run_id=RUN_B,
                           boot_id=BOOT_B, delivery_id="om_dispatch_2b",
                           at="2026-10-27T10:02:00+00:00")
        self.clock.advance(60.0)
        second, tick = dispatch_module().tick_from_file(
            self.path, source=self.all_completed(), notifier=self.notifier(),
            state_path=self.state_path, clock=self.clock)

        self.assertEqual(second.batch.members[1].ref, RUN_B)
        self.assertTrue(tick.settled)
        self.assertEqual(tick.verified, 3)
        self.assertEqual(len(self.provider.calls), 4,
                         "3 milestones + 1 summary across the two processes")
        self.assertEqual(len(self.batch_rows()), 1)

    def test_a_second_one_shot_process_replays_instead_of_resending(self):
        self.file()
        first = dispatch_module().tick_from_file(
            self.path, source=self.all_completed(), notifier=self.notifier(),
            state_path=self.state_path, clock=self.clock)[1]
        sent = len(self.provider.calls)
        self.clock.advance(60.0)

        second = dispatch_module().tick_from_file(
            self.path, source=self.all_completed(), notifier=self.notifier(),
            state_path=self.state_path, clock=self.clock)[1]

        self.assertEqual(first.state, batch.SENT)
        self.assertEqual(second.state, batch.REPLAY)
        self.assertEqual(len(self.provider.calls), sent)
        self.assertEqual(len(self.batch_rows()), 1)

    def test_the_ticker_reloads_an_amended_registration_once(self):
        self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        dispatch = dispatch_module()
        ticker = dispatch.Ticker(
            self.path, source=ScriptedSource({
                f"cloudcli:{RUN_A}": for_run("cloudcli", "completed",
                                             run_id=RUN_A, device=DEV_A,
                                             boot=BOOT_A),
                f"cloudcli:{RUN_C}": for_run("cloudcli", "completed",
                                             run_id=RUN_C, device=DEV_C,
                                             boot=BOOT_C),
                f"cloudcli:{RUN_B}": for_run("cloudcli", "completed",
                                             run_id=RUN_B, device=DEV_B,
                                             boot=BOOT_B)}),
            notifier=self.notifier(), state_path=self.state_path,
            clock=self.clock)

        self.assertFalse(ticker.tick().settled)
        self.assertIsNone(ticker.registration.batch.members[1].ref)

        registration.amend(self.path, task_id="t-dell", run_id=RUN_B,
                           boot_id=BOOT_B, delivery_id="om_dispatch_2b",
                           at="2026-10-27T10:02:00+00:00")
        self.clock.advance(60.0)
        settled = ticker.tick()

        self.assertEqual(ticker.registration.batch.members[1].ref, RUN_B)
        self.assertTrue(settled.settled)
        self.assertEqual(settled.verified, 3)
        self.assertEqual(len(self.provider.calls), 4,
                         "3 milestones + 1 summary, after adopting the Run once")
        self.assertEqual(len(self.batch_rows()), 1)

    def test_the_loader_opens_no_second_ledger(self):
        # One ledger, one state file: the loader adds no store of its own.
        self.file()
        before = self.store_names()
        dispatch_module().tick_from_file(
            self.path, source=self.all_completed(), notifier=self.notifier(),
            state_path=self.state_path, clock=self.clock)
        after = self.store_names()

        self.assertEqual(after - before,
                         {"milestones.sqlite", "observer-state.json"},
                         "the only stores are #423's ledger and #428's state")
        self.assertEqual(len([name for name in after
                              if name.endswith(".sqlite")]), 1)


if __name__ == "__main__":
    unittest.main()
