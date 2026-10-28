#!/usr/bin/env python3
"""#417 — the whole wire: a filed dispatch, the real observer, one summary.

`test_fleet_v1_milestone_batch.py` proves the correlation contract in
isolation. This suite proves the *composition* the owner asked for:

    #456 commander plan + ACKs
      -> registration.py      (durable: one file, one Batch ID)
      -> registration.batch.watches()
      -> #428 observer.run_once()          (the real one, unchanged)
      -> BatchAggregator.absorb(report)
      -> #423's ledger                    (the only delivery truth)

WHAT IS STUBBED, AND WHY IT IS SAID OUT LOUD
--------------------------------------------
#423 @ d9b2948a has no public `send_text`. That is a BLOCKED state, recorded in
`test_the_real_notifier_without_the_seam_is_refused_up_front` and in the PR.
The behavioural tests still have to run, so they use `PublicSeamStub`: a
**sealed** wrapper that adds `send_text` using ONLY #423's public surface
(`ledger.claim/finish/get`, `transport_key_for`, `Observation`, `provider`,
`conversation_ref`, `clock`, and `runtime/notifications.capability_of`) and
refuses every attribute starting with `_`. Nothing here calls `_send`,
`_now` or `_prior_observation`, and `TheSeamIsHonest` pins that.

No network, no CLI, no credential, no service, no model, no real IM send.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import shutil
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BATCH_DIR = REPO / "integrations" / "fleet-v1-milestone-batch"
OBSERVER_DIR = REPO / "integrations" / "fleet-v1-milestone-observer"
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

BATCH_ID = "dispatch-2026-10-28-alpha"
WINDOW_END = "2026-10-28T10:00:00Z"
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
m = observer.NOTIFY
batch = _load("serveros_fleet_v1_milestone_batch", BATCH_DIR / "batch.py")
registration = _load("serveros_fleet_v1_milestone_batch_registration",
                     BATCH_DIR / "registration.py")
notifications = _load("serveros_runtime_notifications",
                      REPO / "runtime" / "notifications.py")


# --------------------------------------------------------------------------- #
# the notifier seam, as a sealed public-API stand-in
# --------------------------------------------------------------------------- #
class PublicSeamStub:
    """#423's notifier plus the ONE public method this contract needs.

    Sealed: only non-underscore attributes are forwarded, so a test (or a
    production path) that tries `_send` fails loudly instead of quietly
    becoming a second write path to the one provider. When #423's owner lands
    `send_text`, this class is deleted and the real notifier is passed instead
    — nothing else in this suite changes.
    """

    def __init__(self, notifier):
        self.__dict__["_target"] = notifier

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(
                f"{name!r} is #423's private surface; the batch contract is "
                f"BLOCKED on a public send_text and may not reach past it")
        return getattr(self.__dict__["_target"], name)

    # -- the seam ----------------------------------------------------------

    def send_text(self, *, delivery_key, milestone_key, outcome, text, why):
        target = self.__dict__["_target"]
        transport = m.transport_key_for(delivery_key)
        intent = json.dumps({"text": text, "transport_key": transport},
                            ensure_ascii=False, sort_keys=True)
        now = _now_iso(target)
        prior = target.ledger.claim(
            delivery_key=delivery_key, milestone_key=milestone_key,
            outcome=outcome, attempted_at=now, intent=intent,
            transport_key=transport)
        if prior is not None:
            return self._prior(prior, delivery_key)
        state, remote_id, send_why = self._dispatch(target, text, transport)
        target.ledger.finish(delivery_key=delivery_key, state=state,
                             remote_id=remote_id, attempted_at=now,
                             why=send_why, intent=intent)
        row = target.ledger.get(delivery_key) or {"state": state,
                                                  "remote_id": remote_id}
        if row["state"] == m.DELIVERED and state != m.DELIVERED:
            send_why = "another observation recorded the delivered receipt"
        return m.Observation(
            outcome, row["state"], delivery_key, row["remote_id"], text, None,
            send_why if state != m.DELIVERED else (why or send_why),
            row["state"] == m.DELIVERED, transport)

    def _dispatch(self, target, text, transport):
        state, reason = notifications.capability_of(target.provider)
        if state != "SUPPORTED":
            return (m.UNKNOWN_DELIVERY, None,
                    f"{state}: {reason or 'transport is not SUPPORTED'}")[:3]
        try:
            answer = target.provider.notify(
                conversation_ref=target.conversation_ref, text=text,
                idempotency_key=transport)
        except Exception as exc:
            return m.UNKNOWN_DELIVERY, None, f"{type(exc).__name__}: {exc}"[:240]
        remote = answer.get("remote_id") if isinstance(answer, dict) else None
        if (isinstance(answer, dict) and answer.get("delivered") is True
                and isinstance(remote, str) and remote.strip()):
            return m.DELIVERED, remote.strip()[:256], "provider returned a message id"
        why = ((answer.get("why") if isinstance(answer, dict) else None)
               or "provider returned no message id")
        return m.UNKNOWN_DELIVERY, None, str(why)[:240]

    def _prior(self, prior, delivery_key):
        """What a replay may say: re-expressed from the row, not borrowed."""
        transport = prior.get("transport_key") or m.legacy_transport_key(
            prior["delivery_key"])
        if prior["state"] == m.DELIVERED:
            return m.Observation(prior["outcome"], m.DUPLICATE, delivery_key,
                                 prior["remote_id"], None, None,
                                 "already delivered; a replay does not resend",
                                 True, transport)
        return m.Observation(prior["outcome"], m.UNKNOWN_REPLAY, delivery_key,
                             None, None, None,
                             "previous delivery is UNKNOWN; a replay does not "
                             "resend", False, transport)


def _now_iso(target) -> str:
    value = target.clock()
    if isinstance(value, bool):
        raise AssertionError("a clock must not return a bool")
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()
    return value.isoformat()


# --------------------------------------------------------------------------- #
# the surrounding cast
# --------------------------------------------------------------------------- #
class Clock:
    def __init__(self, at: float = START):
        self.at = float(at)

    def __call__(self) -> float:
        return self.at

    def advance(self, seconds: float) -> None:
        self.at += float(seconds)


class FakeLark:
    """The one outbound provider, standing in. No CLI, no socket, no token."""

    provider_id = "provider:fake-lark"
    transport = "fake"

    def __init__(self, receipts=None, capability: str = "SUPPORTED"):
        self.receipts = list(receipts or [])
        self.capability = capability
        self.calls = []

    def capabilities(self):
        return {"provider": self.provider_id, "transport": self.transport,
                "capabilities": {CAPABILITY: {"state": self.capability}}}

    def notify(self, *, conversation_ref, text, idempotency_key):
        self.calls.append({"conversation_ref": conversation_ref, "text": text,
                           "idempotency_key": idempotency_key})
        if self.receipts:
            receipt = self.receipts.pop(0)
            if isinstance(receipt, BaseException):
                raise receipt
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
    """#456's contract shape (#456 is not on this base; see the README)."""

    def validate_batch(self, plan, snapshot):
        return {"verdict": "PASS", "batch_id": plan.get("batch_id"),
                "task_count": len(plan.get("tasks") or []), "blocked": []}


def plan(*, devices=None, sessions=None) -> dict:
    devices = devices or ((DEV_A, "claude", "t-tokyo", SESSION_A),
                          (DEV_B, "codex", "t-dell", SESSION_B),
                          (DEV_C, "claude", "t-fjd", SESSION_C))
    sessions = sessions or {}
    return {"batch_id": BATCH_ID, "tasks": [
        {"task_id": task_id, "device_ref": device, "runtime": runtime,
         "project_id": "serveros", "worktree": f"/w/{task_id}",
         "scope": "integrations/fleet-v1-milestone-batch", "acceptance": "a",
         "tests": "b", "report": "c", "idempotency_key": f"idem-{task_id}",
         "session_ref": sessions.get(task_id, session)}
        for device, runtime, task_id, session in devices]}


def acks(*, runs=(RUN_A, RUN_B, RUN_C), deliveries=(DELIVERY_A, DELIVERY_B,
                                                   DELIVERY_C),
         boots=(BOOT_A, BOOT_B, BOOT_C), devices=None) -> list:
    """#456's ACKs, one per Run, in the plan's task order."""
    devices = devices or ((DEV_A, "t-tokyo"), (DEV_B, "t-dell"),
                          (DEV_C, "t-fjd"))
    return [{"task_id": devices[index][1], "device_ref": devices[index][0],
             "run_id": runs[index], "delivery_id": deliveries[index],
             "boot_id": boots[index]} for index in range(len(runs))]


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-batch-int-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger_path = self.tmp / "milestones.sqlite"
        self.state_path = self.tmp / "observer-state.json"
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

    # -- the process -------------------------------------------------------

    def notifier(self):
        return PublicSeamStub(m.MilestoneNotifier(
            self.provider, self.ledger_path, conversation_ref=RECIPIENT,
            summarizer=None, clock=self.clock))

    def all_completed(self):
        """The happy path's source: every registered Run is green and current."""
        return ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_B}": for_run("cloudcli", "completed", run_id=RUN_B,
                                         device=DEV_B, boot=BOOT_B),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C)})

    def observer_for(self, filed, source, notifier=None, options=None):
        return observer.MilestoneObserver(
            source=source, notifier=notifier or self.notifier(),
            state_path=self.state_path, watches=filed.batch.watches(),
            clock=self.clock, options=options or observer.Options())

    def aggregator(self, filed, notifier=None):
        return batch.BatchAggregator(filed.batch,
                                     notifier=notifier or self.notifier(),
                                     clock=self.clock)

    def step(self, agg, instance, *, times=1, watches=None):
        out = []
        for _ in range(times):
            out.append(agg.absorb(instance.run_once(watches)))
            self.clock.advance(60.0)
        return out

    # -- reads -------------------------------------------------------------

    def text_calls(self):
        return [call["text"] for call in self.provider.calls]

    def summary_calls(self, filed):
        key = m.transport_key_for(filed.batch.summary_key)
        return [call for call in self.provider.calls
                if call["idempotency_key"] == key]


# --------------------------------------------------------------------------- #
# the owner's scenario, all the way through
# --------------------------------------------------------------------------- #
class ARegisteredDispatchReachesTheOwnerOnce(Base):
    def test_one_registered_dispatch_is_one_summary_and_three_milestones(self):
        filed = self.file()
        agg = self.aggregator(filed)
        # The Runs are still going when the tick first looks, then they finish.
        instance = self.observer_for(filed, ScriptedSource({
            f"cloudcli:{RUN_A}": [
                for_run("cloudcli", "running", run_id=RUN_A, device=DEV_A,
                        boot=BOOT_A),
                for_run("cloudcli", "completed", run_id=RUN_A, device=DEV_A,
                        boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [
                for_run("cloudcli", "running", run_id=RUN_B, device=DEV_B,
                        boot=BOOT_B),
                for_run("cloudcli", "completed", run_id=RUN_B, device=DEV_B,
                        boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [
                for_run("cloudcli", "running", run_id=RUN_C, device=DEV_C,
                        boot=BOOT_C),
                for_run("cloudcli", "completed", run_id=RUN_C, device=DEV_C,
                        boot=BOOT_C)]}))

        first, second = self.step(agg, instance, times=2)

        self.assertEqual(first.state, batch.OPEN)
        self.assertTrue(second.settled)
        self.assertEqual(second.state, batch.SENT)
        self.assertEqual(second.verified, 3)
        self.assertEqual(len(self.summary_calls(filed)), 1)
        # Three milestones (#423, as they happened) and one summary (this
        # contract). Nothing else.
        self.assertEqual(len(self.provider.calls), 4)
        self.assertIn("3/3", self.text_calls()[-1])

    def test_every_batch_message_carries_a_provider_receipt(self):
        filed = self.file()
        agg = self.aggregator(filed)
        instance = self.observer_for(filed, self.all_completed())
        tick = self.step(agg, instance, times=1)[0]

        receipts = tick.receipts
        self.assertEqual(len(receipts), 1)
        receipt = receipts[0]
        self.assertEqual(receipt.delivery_key, filed.batch.summary_key)
        self.assertEqual(receipt.outcome, batch.BATCH_SUMMARY)
        self.assertEqual(receipt.state, m.DELIVERED)
        self.assertTrue(receipt.confirmed)
        self.assertEqual(receipt.remote_id, f"om_{len(self.provider.calls)}",
                         "the receipt is the provider's message id, not ours")
        self.assertEqual(receipt.transport_key,
                         m.transport_key_for(filed.batch.summary_key))
        self.assertLessEqual(len(receipt.transport_key), 50,
                             "#423's transport truncates at 50")

    def test_a_restart_replays_the_receipt_and_sends_nothing(self):
        filed = self.file()
        instance = self.observer_for(filed, self.all_completed())
        self.step(self.aggregator(filed), instance, times=1)
        sent = len(self.provider.calls)
        self.assertEqual(len(self.summary_calls(filed)), 1)

        # A new process: new aggregator, new observer, and the declaration read
        # back off the disk rather than remembered.
        reloaded = registration.load(self.path)
        self.assertEqual(reloaded.batch.origin, batch.ORIGIN_REGISTRATION)
        again = self.step(self.aggregator(reloaded),
                          self.observer_for(reloaded, self.all_completed()),
                          times=1)[0]

        self.assertEqual(again.state, batch.REPLAY)
        self.assertEqual(len(self.provider.calls), sent,
                         "a restart may not send a second summary")
        self.assertEqual(len(self.summary_calls(filed)), 1)
        self.assertEqual(again.receipts[0].state, m.DUPLICATE)
        self.assertTrue(again.receipts[0].remote_id,
                        "the replay still names the delivered receipt")


# --------------------------------------------------------------------------- #
# ACK, queued, unobservable
# --------------------------------------------------------------------------- #
class AQueuedTaskIsNeitherSuccessNorSilence(Base):
    def test_a_queued_task_is_not_counted_as_done(self):
        filed = self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        agg = self.aggregator(filed)
        instance = self.observer_for(filed, ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C)}))

        ticks = self.step(agg, instance, times=3)

        for tick in ticks:
            self.assertFalse(tick.settled,
                             "two of three verified is not a finished dispatch")
            self.assertIsNone(tick.summary)
        self.assertEqual(len(self.summary_calls(filed)), 0)
        self.assertEqual(self.verdict_of(ticks[-1], "t-dell"), batch.PENDING)
        self.assertEqual(filed.batch.members[1].task_id, "t-dell")

    def test_a_queued_task_that_starts_is_read_from_then_on(self):
        filed = self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        agg = self.aggregator(filed)
        instance = self.observer_for(filed, ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C),
            f"cloudcli:{RUN_B}": for_run("cloudcli", "completed", run_id=RUN_B,
                                         device=DEV_B, boot=BOOT_B)}))
        self.step(agg, instance, times=1)

        amended, outcome = registration.amend(
            self.path, task_id="t-dell", run_id=RUN_B, boot_id=BOOT_B,
            delivery_id="om_dispatch_2b", at="2026-10-27T10:02:00+00:00")
        self.assertEqual(outcome, registration.REGISTERED)
        agg.refresh(amended.batch)
        tick = agg.absorb(instance.run_once(amended.batch.watches()))

        self.assertTrue(tick.settled)
        self.assertEqual(tick.verified, 3)
        self.assertIn("3/3", self.text_calls()[-1])
        self.assertEqual(len(self.summary_calls(filed)), 1)

    def test_a_queued_task_that_never_starts_is_one_actionable_notice(self):
        filed = self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        agg = self.aggregator(filed)
        instance = self.observer_for(filed, ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C)}))
        self.step(agg, instance, times=1)
        before = len(self.provider.calls)

        self.clock.at = datetime(2026, 10, 27, 11, 0, 1,
                                 tzinfo=timezone.utc).timestamp()
        tick = self.step(agg, instance, times=1)[0]

        self.assertTrue(tick.settled)
        self.assertEqual(self.verdict_of(tick, "t-dell"),
                         batch.UNOBSERVED)
        # One alert for the member nothing else could speak about, one summary.
        self.assertEqual(len(tick.alerts), 1)
        alert = self.text_calls()[before]
        self.assertIn("UNOBSERVED", alert)
        self.assertIn("dell-5820", alert)
        self.assertIn("2/3", self.text_calls()[-1])
        sent = len(self.provider.calls)

        # And it is not a stream: three more ticks say nothing new.
        self.step(agg, instance, times=3)
        self.assertEqual(len(self.provider.calls), sent)

    def test_a_late_run_cannot_rescue_a_member_the_owner_was_told_about(self):
        filed = self.file(ack_rows=acks(runs=(RUN_A, None, RUN_C)))
        agg = self.aggregator(filed)
        instance = self.observer_for(filed, ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C)}))
        self.step(agg, instance, times=1)
        self.clock.at = datetime(2026, 10, 27, 11, 0, 1,
                                 tzinfo=timezone.utc).timestamp()
        self.step(agg, instance, times=1)
        amended, _ = registration.amend(
            self.path, task_id="t-dell", run_id=RUN_B, boot_id=BOOT_B,
            delivery_id="om_dispatch_2b", at="2026-10-27T11:30:00+00:00")

        with self.assertRaises(batch.BatchRefused) as caught:
            agg.refresh(amended.batch)
        self.assertEqual(caught.exception.code, "TOO_LATE")

    def verdict_of(self, tick, task_id):
        for state in tick.members:
            if state.member.task_id == task_id:
                return state.verdict
        raise AssertionError(f"{task_id} is not in this tick")


# --------------------------------------------------------------------------- #
# one voice per fact
# --------------------------------------------------------------------------- #
class AMixedBatchSpeaksOnceAboutEachMember(Base):
    def test_a_failed_member_is_a_milestone_and_not_a_second_alert(self):
        filed = self.file()
        agg = self.aggregator(filed)
        instance = self.observer_for(filed, ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_B}": for_run("cloudcli", "failed", run_id=RUN_B,
                                         device=DEV_B, boot=BOOT_B),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed", run_id=RUN_C,
                                         device=DEV_C, boot=BOOT_C)}))
        tick = self.step(agg, instance, times=1)[0]

        self.assertEqual(tick.alerts, ())
        self.assertEqual(len(self.provider.calls), 4,
                         "3 milestones + 1 summary; a RED already spoke")
        self.assertIn("2/3", self.text_calls()[-1])

    def test_a_mixed_batch_names_every_member_and_alerts_only_the_silent(self):
        # Four members: green, red, unknown, and one the tick cannot observe at
        # all (a payload from another boot). Only the last needs a batch alert.
        fourth = "device:llm-lab-dell"
        boot_fourth = "boot-dell-2"
        run_fourth = "run_lab_1"
        dispatch = ((DEV_A, "claude", "t-tokyo", SESSION_A),
                    (DEV_B, "codex", "t-dell", SESSION_B),
                    (DEV_C, "claude", "t-fjd", SESSION_C),
                    (fourth, "claude", "t-lab", "session:lab-delta"))
        filed = self.file(
            plan_value=plan(devices=dispatch),
            ack_rows=acks(runs=(RUN_A, RUN_B, RUN_C, run_fourth),
                          deliveries=(DELIVERY_A, DELIVERY_B, DELIVERY_C, "om_4"),
                          boots=(BOOT_A, BOOT_B, BOOT_C, boot_fourth),
                          devices=tuple((row[0], row[2]) for row in dispatch)))
        agg = self.aggregator(filed)
        instance = self.observer_for(filed, ScriptedSource({
            f"cloudcli:{RUN_A}": for_run("cloudcli", "completed", run_id=RUN_A,
                                         device=DEV_A, boot=BOOT_A),
            f"cloudcli:{RUN_B}": for_run("cloudcli", "failed", run_id=RUN_B,
                                         device=DEV_B, boot=BOOT_B),
            f"cloudcli:{RUN_C}": for_run("cloudcli", "completed-no-output",
                                         run_id=RUN_C, device=DEV_C,
                                         boot=BOOT_C),
            # A payload that answers for another boot is never this task's fact.
            f"cloudcli:{run_fourth}": for_run("cloudcli", "completed",
                                              run_id=run_fourth, device=fourth,
                                              boot=BOOT_A)}))

        tick = self.step(agg, instance, times=1)[0]

        self.assertTrue(tick.settled)
        self.assertEqual(len(tick.alerts), 1, "only the unobservable member")
        self.assertEqual(len(self.provider.calls), 5,
                         "3 milestones + 1 alert + 1 summary, and no more")
        self.assertEqual(tick.counts[batch.VERIFIED], 1)
        self.assertEqual(tick.counts[batch.FAILED], 1)
        self.assertEqual(tick.counts[batch.UNKNOWN], 2)
        summary = self.text_calls()[-1]
        for run_id in (RUN_A, RUN_B, RUN_C, run_fourth):
            self.assertIn(run_id, summary)
        self.assertIn("1/4", summary)

    def test_the_alert_budget_is_finite_and_the_summary_carries_the_rest(self):
        # Twelve devices whose payloads all answer for another boot: nothing
        # #423 can speak about, so without a budget this is twelve notifications.
        devices = [(f"device:fleet-{index:02d}", "claude", f"t-{index:02d}",
                    f"session:fleet-{index:02d}") for index in range(12)]
        runs = tuple(f"run_fleet_{index:02d}" for index in range(12))
        filed = self.file(
            plan_value=plan(devices=devices),
            ack_rows=acks(
                runs=runs,
                deliveries=tuple(f"om_{index:02d}" for index in range(12)),
                boots=tuple(f"boot-fleet-{index:02d}" for index in range(12)),
                devices=tuple((row[0], row[2]) for row in devices)))
        agg = self.aggregator(filed)
        # #428's per-source read cap is per tick and configurable: the whole
        # dispatch has to fit in one look for the alert budget itself to be what
        # this test measures.
        instance = self.observer_for(
            filed,
            ScriptedSource({
                f"cloudcli:{run_id}": for_run("cloudcli", "completed",
                                              run_id=run_id,
                                              device=devices[index][0],
                                              boot=BOOT_A)
                for index, run_id in enumerate(runs)}),
            options=observer.Options(max_reads_per_source=16))

        tick = self.step(agg, instance, times=1)[0]

        self.assertTrue(tick.settled)
        self.assertEqual(len(tick.alerts), batch.MAX_ALERTS)
        self.assertEqual(len(tick.suppressed), 12 - batch.MAX_ALERTS)
        self.assertEqual(len(self.provider.calls), batch.MAX_ALERTS + 1)
        self.assertIn(f"另有 {12 - batch.MAX_ALERTS} 项未单独告警",
                      self.text_calls()[-1])
        self.assertIn("0/12", self.text_calls()[-1])


# --------------------------------------------------------------------------- #
# the seam this all rests on
# --------------------------------------------------------------------------- #
class TheSeamIsHonest(Base):
    def test_the_real_notifier_without_the_seam_is_refused_up_front(self):
        # #423 @ d9b2948a: the public method does not exist. Recorded as
        # BLOCKED, not worked around.
        real = m.MilestoneNotifier(self.provider, self.ledger_path,
                                   conversation_ref=RECIPIENT, clock=self.clock)
        self.assertFalse(hasattr(real, "send_text"),
                         "#423 has landed send_text — delete PublicSeamStub "
                         "and the BLOCKED note, and pass the real notifier")
        with self.assertRaises(batch.BatchRefused) as caught:
            batch.BatchAggregator(self.file().batch, notifier=real)
        self.assertEqual(caught.exception.code, "NOTIFIER_SEAM_MISSING")

    def test_the_sealed_stub_refuses_a_private_reach_through(self):
        stub = self.notifier()
        for name in ("_send", "_now", "_prior_observation"):
            with self.subTest(name=name), self.assertRaises(AttributeError):
                getattr(stub, name)
        self.assertTrue(callable(stub.reconcile))

    def test_the_production_modules_never_reach_into_a_private_member(self):
        # Dotted, so the docstring may still *name* the seam it refuses.
        for name in ("batch.py", "registration.py"):
            source = (BATCH_DIR / name).read_text(encoding="utf-8")
            for private in ("._send(", "._prior_observation", "._now(",
                            "._scrub("):
                self.assertFalse(
                    private in source,
                    f"{name} calls {private}—that is #423's private surface; "
                    f"the batch contract is BLOCKED on a public seam")
        self.assertIn("send_text(", (BATCH_DIR / "batch.py").read_text(
            encoding="utf-8"), "the public seam must be the only send path")


if __name__ == "__main__":
    unittest.main()
