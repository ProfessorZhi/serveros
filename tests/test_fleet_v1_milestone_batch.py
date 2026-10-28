#!/usr/bin/env python3
"""#417 — one dispatch, N devices, ONE summary.

WHAT THIS FILE PINS
-------------------
#417's owner scenario: a ChatGPT turn dispatches several Runs across devices and
returns without waiting. #423 (delivery), #426 (sources), #428 (the tick) and
#446 (the bounded runner) already keep reading those Runs. Nobody owns the
CORRELATION: when every Run of that one dispatch is terminal, exactly one batch
summary must reach the owner through the same provider, and a member that
failed, went unknown or went offline must raise a finite, actionable alert
instead of silence.

The matrix this file pins, all offline:

* N devices, one dispatch: no message while any member is still running, and
  exactly one summary once every member is terminal.
* A queued ACK (dispatched, not started) is not a start and not an end.
* Partial success: one summary, and one alert for the member that failed.
* A completed Run without trusted native output is UNKNOWN — counted, alerted,
  never VERIFIED.
* `RUN_NOT_FOUND` keeps the batch open (it is a read that can clear) and the
  batch completes when the Run appears.
* A restart replays the receipt: the ledger decides, not a second send.
* Cross-boot: one runId cannot be declared for two boots, and a payload read
  for another boot never verifies this dispatch's member.
* The batch-complete receipt is one ledger row, and an unconfirmed send is
  reconciled under the SAME transport key, never resent under a new one.

EVIDENCE LABELS
---------------
CODE + UNIT are this file. Every source is scripted, the provider is `FakeLark`
and the notifier is `SeamedNotifier`: a SEALED wrapper around #423's real
`MilestoneNotifier` that adds the one public method this contract needs using
only #423's public surface. No network, no CLI, no credential, no service, no
model, no real Feishu send.

HOSTED_CI is the PR's workflow. REAL_DEVICE, CLIENT_E2E and
REAL_FEISHU_DELIVERY are NOT_RUN and are not claimed. Sending a test IM would
need a separate owner decision and is not done here.

THE ONE SEAM — BLOCKED
----------------------
`BatchAggregator` never touches #423's ledger and never calls a private method.
It calls `notifier.send_text(...)`, which #423 does not have yet: at
`d9b2948a` the public surface is `observe` and the ledger, so the seam is
BLOCKED on its owner (see the README, "BLOCKED"). `SeamedNotifier` stands in
for it — and is deliberately SEALED: any attribute whose name starts with `_`
is refused, so neither a test nor a production path can quietly turn into a
second write path through `_send`. When the owner lands `send_text`, the
production wiring swaps this class for the real notifier and nothing else
changes.
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
BATCH_MODULE = BATCH_DIR / "batch.py"
OBSERVER_DIR = REPO / "integrations" / "fleet-v1-milestone-observer"
OBSERVER_MODULE = OBSERVER_DIR / "observer.py"
FIXTURES = OBSERVER_DIR / "fixtures"

CAPABILITY = "human.notification.transport"
RECIPIENT = "user:ou_0000000000000000000000000000"

#: One dispatch, three devices with their own boots. Distinct boots are what
#: makes the cross-boot test mean something.
DEV_A, BOOT_A = "device:tokyo-shared-dev-01", "boot-tokyo-1"
DEV_B, BOOT_B = "device:dell-5820", "boot-dell-1"
DEV_C, BOOT_C = "device:win-fjd", "boot-win-1"
RUN_A, RUN_B, RUN_C = "run_demo_1", "run_dell_1", "run_fjd_1"
SESSION = "session:dev:observer-1"

BATCH_ID = "dispatch-2026-10-28-alpha"
WINDOW_END = "2026-10-28T10:00:00Z"
DEADLINE = "2026-10-27T11:00:00Z"
START = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc).timestamp()


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


#: The canonical names the lane already uses, so the observer is executed once
#: and `Watch` is one class in this process.
observer = _load("serveros_fleet_v1_milestone_observer", OBSERVER_MODULE)
m = observer.NOTIFY  # #423's module, loaded once under its canonical name
batch = _load("serveros_fleet_v1_milestone_batch", BATCH_MODULE)
#: The stub's capability probe, loaded under the name the runtime imports it by
#: so it is the same module object the observer's own probe resolves to.
notifications = _load("serveros_runtime_notifications",
                      REPO / "runtime" / "notifications.py")


def fixture(source: str, name: str) -> dict:
    return json.loads((FIXTURES / source / f"{name}.json").read_text(encoding="utf-8"))


def for_run(source: str, name: str, *, run_id: str, device: str, boot: str,
            **changes) -> dict:
    """One fixture, re-addressed to a member of this dispatch.

    The payload keeps the shape #426 produces: the envelope's `device_ref` and
    `bound_boot_id`, and the verbatim `run_get` result nested under `run`.
    """
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


class Clock:
    """A fake clock. Neither the observer nor the aggregator reads the wall."""

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
    """The typed refusal shape #426's sources branch publishes."""

    def __init__(self, code: str):
        self.code = code
        self.detail = code
        super().__init__(code)


class ScriptedSource:
    """A `SourceRead` whose answers are a script, not a network.

    A list is consumed one answer per read and then keeps returning its last
    answer, so an observer that re-reads a terminal run sees the same fact —
    the replay case the ledger must absorb.
    """

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


class SeamedNotifier:
    """#423's notifier plus the ONE public method this contract needs.

    The proposal (README, "the one exact patch") adds ONE public method:

        def send_text(self, *, delivery_key, milestone_key, outcome, text, why)

    #423 @ d9b2948a does not have it, so the seam is BLOCKED on its owner and
    this stub stands in — built from the PUBLIC surface only
    (`ledger.claim/finish/get`, `transport_key_for`, `Observation`, `provider`,
    `conversation_ref`, `clock`, `runtime/notifications.capability_of`).

    It is sealed: non-underscore attributes are forwarded to the real notifier
    (so `observe` is #423's own), and anything starting with `_` is refused.
    That is what keeps this suite from becoming a second write path while the
    owner works on the seam.
    """

    def __init__(self, provider, ledger_path, *, conversation_ref,
                 summarizer=None, clock=None):
        self.__dict__["_target"] = m.MilestoneNotifier(
            provider, ledger_path, conversation_ref=conversation_ref,
            summarizer=summarizer, clock=clock)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(
                f"{name!r} is #423's private surface; the batch contract is "
                f"BLOCKED on a public send_text and may not reach past it")
        return getattr(self.__dict__["_target"], name)

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
                    f"{state}: {reason or 'transport is not SUPPORTED'}"[:240])
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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-batch-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = self.tmp / "milestones.sqlite"
        self.state = self.tmp / "observer-state.json"
        self.clock = Clock()
        self.provider = FakeLark()

    # -- declarations ------------------------------------------------------

    def member(self, ref: str, device: str, boot: str, role: str = ""):
        return batch.BatchMember(source="cloudcli", ref=ref, device_ref=device,
                                 boot_id=boot, role=role,
                                 expires_at=WINDOW_END)

    def three_devices(self, **changes):
        options = {"batch_id": BATCH_ID,
                   "members": (self.member(RUN_A, DEV_A, BOOT_A, "tokyo"),
                               self.member(RUN_B, DEV_B, BOOT_B, "dell"),
                               self.member(RUN_C, DEV_C, BOOT_C, "fjd"))}
        options.update(changes)
        return batch.Batch(**options)

    # -- wiring ------------------------------------------------------------

    def notifier(self, provider=None, ledger=None, clock=None):
        return SeamedNotifier(provider or self.provider,
                              ledger or self.ledger,
                              conversation_ref=RECIPIENT, summarizer=None,
                              clock=clock or self.clock)

    def observer_for(self, declaration, source, *, notifier=None,
                     state=None, clock=None, options=None):
        return observer.MilestoneObserver(
            source=source, notifier=notifier or self.notifier(),
            state_path=state or self.state,
            watches=declaration.watches(), clock=clock or self.clock,
            options=options or observer.Options())

    def aggregator(self, declaration, *, notifier=None, clock=None):
        return batch.BatchAggregator(declaration,
                                     notifier=notifier or self.notifier(),
                                     clock=clock or self.clock)

    def tick(self, agg, instance, times: int = 1):
        """One observer tick, then one correlation step. The real order."""
        out = []
        for _ in range(times):
            out.append(agg.absorb(instance.run_once()))
            self.clock.advance(60.0)
        return out

    # -- reads -------------------------------------------------------------

    def summary_calls(self, declaration):
        key = m.transport_key_for(declaration.summary_key)
        return [c for c in self.provider.calls if c["idempotency_key"] == key]

    def alert_calls(self, declaration, member_key, verdict):
        key = m.transport_key_for(declaration.alert_key(member_key, verdict))
        return [c for c in self.provider.calls if c["idempotency_key"] == key]

    def ledger_rows(self, prefix="batch:"):
        db = sqlite3.connect(self.ledger)
        db.row_factory = sqlite3.Row
        try:
            rows = db.execute(
                "SELECT * FROM milestone_deliveries WHERE delivery_key LIKE ? "
                "ORDER BY delivery_key", (prefix + "%",)).fetchall()
        finally:
            db.close()
        return [dict(row) for row in rows]

    def verdicts(self, tick) -> dict:
        return {state.member.ref: state.verdict for state in tick.members}


class ADispatchOfNDispatchedRuns(Base):
    """The owner's scenario: one dispatch, several devices, one summary."""

    def test_three_running_devices_send_nothing_and_keep_the_batch_open(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "running", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "running", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "running", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        [tick] = self.tick(agg, instance)

        self.assertFalse(tick.settled)
        self.assertEqual(tick.state, "OPEN")
        self.assertEqual(set(tick.verdicts.values()), {"PENDING"})
        self.assertEqual(self.summary_calls(declaration), [])
        self.assertEqual(self.provider.calls, [])

    def test_one_dispatch_across_three_devices_sends_exactly_one_summary(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        [tick] = self.tick(agg, instance)

        self.assertTrue(tick.settled)
        self.assertEqual(tick.state, "SENT")
        self.assertEqual(set(tick.verdicts.values()), {"VERIFIED"})
        self.assertEqual(tick.counts["VERIFIED"], 3)
        # Three per-Run milestones plus exactly one batch summary, and the
        # summary is its own message, not a fourth milestone.
        summaries = self.summary_calls(declaration)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(len(self.provider.calls), 4)
        self.assertIn("3/3", summaries[0]["text"])
        self.assertEqual(tick.summary.state, m.DELIVERED)

    def test_a_queued_ack_is_not_a_start_and_not_an_end(self):
        # `queued` is the ACK that came back before the Run began. #423 maps it
        # to RUNNING, so it must be silent: a dispatch that was accepted is not
        # a dispatch that finished.
        queued = for_run("cloudcli", "running", run_id=RUN_A, device=DEV_A,
                         boot=BOOT_A)
        queued["run"]["status"] = "queued"
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [queued],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        _, tick = self.tick(agg, instance, times=2)

        self.assertEqual(self.verdicts(tick)[RUN_A], "PENDING")
        self.assertFalse(tick.settled)
        self.assertEqual(self.summary_calls(declaration), [])


class APartialSuccess(Base):
    def test_partial_success_sends_one_summary_and_no_second_message(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "failed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        [first, second] = self.tick(agg, instance, times=2)

        self.assertTrue(first.settled)
        self.assertEqual(self.verdicts(first)[RUN_B], "FAILED")
        self.assertEqual(first.counts["VERIFIED"], 2)
        self.assertEqual(first.counts["FAILED"], 1)
        self.assertEqual(len(self.summary_calls(declaration)), 1)
        # #423 already sent the RED as its own milestone, as it happened. A
        # second message about the same fact is exactly the noise #417's owner
        # asked to be spared: the batch's contribution is the summary line.
        self.assertEqual(first.alerts, ())
        self.assertEqual(self.alert_calls(declaration, f"cloudcli:{RUN_B}",
                                          "FAILED"), [])
        # The second tick is a replay of the same facts: nothing new goes out.
        self.assertEqual(second.state, "REPLAY")
        self.assertEqual(second.alerts, ())
        self.assertEqual(len(self.summary_calls(declaration)), 1)

    def test_the_failure_is_still_actionable_in_the_summary(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "failed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()

        [tick] = self.tick(self.aggregator(declaration),
                           self.observer_for(declaration, source))

        text = self.summary_calls(declaration)[0]["text"]
        line = [row for row in text.splitlines() if RUN_B in row][0]
        # A person reading the summary can act on it: role, ref, verdict and
        # #423's own outcome word. The batch does not paraphrase #423's reason
        # sentence — that sentence is already in the red message itself.
        self.assertIn("失败", line)
        self.assertIn("RED", line)
        self.assertIn("dell", line)

    def test_the_alert_is_not_a_second_summary(self):
        # Member A's read answers for the previous boot: #428 settles it before
        # #423 ever sees a payload, so nothing is said about this member unless
        # the batch says it.
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "failed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = batch.Batch(batch_id=BATCH_ID, members=(
            self.member(RUN_A, DEV_A, "boot-tokyo-2"),
            self.member(RUN_B, DEV_B, BOOT_B),
            self.member(RUN_C, DEV_C, BOOT_C)))
        agg = self.aggregator(declaration)

        [tick] = self.tick(agg, self.observer_for(declaration, source))

        self.assertEqual(len(self.provider.calls), 4)  # 2 milestones + alert + summary
        alert = self.alert_calls(declaration, f"cloudcli:{RUN_A}", "UNKNOWN")
        self.assertEqual(len(alert), 1)
        # An alert is about ONE member: it names that ref and the reason, and it
        # does not repeat the batch's tally or carry the other members.
        self.assertIn(RUN_A, alert[0]["text"])
        self.assertNotIn(RUN_B, alert[0]["text"])
        self.assertNotIn("2/3", alert[0]["text"])
        self.assertIn("BOOT_SCOPE_MISMATCH", alert[0]["text"])
        self.assertEqual(tick.alerts[0].delivered, True)


class AnUnknownMember(Base):
    def test_a_completed_run_without_native_output_is_unknown_not_verified(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed-no-output",
                                          run_id=RUN_A, device=DEV_A,
                                          boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)

        [tick] = self.tick(agg, self.observer_for(declaration, source))

        self.assertEqual(self.verdicts(tick)[RUN_A], "UNKNOWN")
        self.assertTrue(tick.settled)
        self.assertEqual(tick.counts["VERIFIED"], 2)
        # #423 already delivered the UNKNOWN as its own milestone message, so
        # the member is covered and no second message is raised for it.
        self.assertEqual(self.alert_calls(declaration, f"cloudcli:{RUN_A}",
                                          "UNKNOWN"), [])
        # The summary must not claim a verification it did not see.
        self.assertIn("2/3", self.summary_calls(declaration)[0]["text"])


class ARefusedRead(Base):
    def test_run_not_found_keeps_the_batch_open_and_then_completes(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [SourceRefused("RUN_NOT_FOUND"),
                                  for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices(expires_at=WINDOW_END)
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        first = agg.absorb(instance.run_once())
        self.assertEqual(self.verdicts(first)[RUN_A], "PENDING")
        self.assertFalse(first.settled)
        self.assertEqual(self.summary_calls(declaration), [])
        self.assertEqual(first.alerts, ())
        # A read that can clear is not an alert; it is a wait.
        self.assertEqual(self.ledger_rows(), [])

        self.clock.advance(30.0)
        second = agg.absorb(instance.run_once())
        self.assertTrue(second.settled)
        self.assertEqual(self.verdicts(second)[RUN_A], "VERIFIED")
        self.assertEqual(len(self.summary_calls(declaration)), 1)

    def test_a_backoff_row_establishes_nothing_and_alerts_nothing(self):
        # RUN_NOT_FOUND puts the ref on a 30s backoff. Inside that window #428
        # returns SKIPPED_BACKOFF stamped `outcome=UNKNOWN` — the same word a
        # genuinely unestablished outcome carries. Reading the outcome before
        # the state would alert on a Run that is merely being retried.
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [SourceRefused("RUN_NOT_FOUND")],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        agg.absorb(instance.run_once())
        self.clock.advance(5.0)
        report = instance.run_once()
        row = [r for r in report["rows"] if r["ref"] == RUN_A][0]

        tick = agg.absorb(report)

        self.assertEqual(row["state"], observer.SKIPPED_BACKOFF)
        self.assertEqual(row["outcome"], m.UNKNOWN)  # the trap, pinned
        self.assertEqual(self.verdicts(tick)[RUN_A], "PENDING")
        self.assertFalse(tick.settled)
        self.assertEqual(tick.alerts, ())
        self.assertEqual(self.summary_calls(declaration), [])

    def test_a_rate_limited_tick_establishes_nothing_either(self):
        # #428's per-source read budget is a scheduling bound, not a fact about
        # a Run: a member that was not reached this tick is not an unknown Run.
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)
        instance = self.observer_for(
            declaration, source,
            options=observer.Options(max_reads_per_source=1))

        [tick] = self.tick(agg, instance)

        self.assertFalse(tick.settled)
        self.assertIn("RATE_LIMITED", {state.delivery_state for state in tick.members})
        self.assertEqual(tick.alerts, ())
        self.assertEqual(self.summary_calls(declaration), [])


class ARestart(Base):
    def test_a_restart_replays_the_receipt_and_sends_nothing(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        first_agg = self.aggregator(declaration)
        [first] = self.tick(first_agg, self.observer_for(declaration, source))
        self.assertEqual(first.state, "SENT")
        sent = len(self.provider.calls)

        # Everything in memory is gone: a new aggregator, a NEW observer state
        # file, the same ledger. The ledger is the only thing that decides.
        restarted_state = self.tmp / "observer-state-after-restart.json"
        second_agg = self.aggregator(declaration)
        restarted = self.observer_for(declaration, source, state=restarted_state)
        [second] = self.tick(second_agg, restarted)

        self.assertTrue(second.settled)
        self.assertEqual(second.state, "REPLAY")
        self.assertEqual(second.summary.delivered, True)
        self.assertEqual(len(self.provider.calls), sent)
        self.assertEqual(len(self.summary_calls(declaration)), 1)


class ACrossBootCollision(Base):
    """A runId is not an identity across boots. It is an identity within one."""

    def test_the_same_run_id_cannot_be_declared_for_two_boots(self):
        with self.assertRaises(batch.BatchRefused) as caught:
            batch.Batch(batch_id=BATCH_ID,
                        members=(self.member(RUN_A, DEV_A, BOOT_A),
                                 self.member(RUN_A, DEV_A, BOOT_B)))
        self.assertEqual(caught.exception.code, "DUPLICATE_REF")
        # The declaration is refused for the same reason #428 refuses two
        # watches: one tick cannot hold two facts about one ref.
        self.assertIn("source:ref", str(caught.exception))

    def test_a_payload_from_another_boot_never_verifies_a_member(self):
        # The dispatch was rebound to BOOT_B, but the Run that answers is the
        # previous boot's Run with the same runId. #428 settles it
        # BOOT_SCOPE_MISMATCH; the batch must read that as "not established".
        stale = for_run("cloudcli", "completed", run_id=RUN_A, device=DEV_A,
                        boot=BOOT_A)
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [stale],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        # Member A is declared for a DIFFERENT boot than the payload answers.
        declaration = batch.Batch(batch_id=BATCH_ID, members=(
            self.member(RUN_A, DEV_A, "boot-tokyo-2"),
            self.member(RUN_B, DEV_B, BOOT_B),
            self.member(RUN_C, DEV_C, BOOT_C)))
        agg = self.aggregator(declaration)

        [tick] = self.tick(agg, self.observer_for(declaration, source))

        self.assertEqual(self.verdicts(tick)[RUN_A], "UNKNOWN")
        self.assertEqual(tick.counts["VERIFIED"], 2)
        self.assertEqual(len(self.alert_calls(declaration, f"cloudcli:{RUN_A}",
                                              "UNKNOWN")), 1)
        self.assertIn("2/3", self.summary_calls(declaration)[0]["text"])


class AOneShotReceipt(Base):
    def test_the_settled_batch_has_exactly_one_receipt_row(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)

        self.tick(agg, self.observer_for(declaration, source), times=3)

        rows = self.ledger_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["delivery_key"], declaration.summary_key)
        self.assertEqual(rows[0]["state"], m.DELIVERED)
        self.assertEqual(rows[0]["outcome"], batch.BATCH_SUMMARY)
        self.assertEqual(rows[0]["milestone_key"], f"batch:{BATCH_ID}")
        self.assertLessEqual(len(rows[0]["delivery_key"]), 512)

    def test_an_unconfirmed_summary_is_reconciled_not_resent(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        # The three milestone sends succeed; the summary's send fails.
        self.provider = FakeLark(receipts=[
            {"delivered": True, "remote_id": "om_a"},
            {"delivered": True, "remote_id": "om_b"},
            {"delivered": True, "remote_id": "om_c"},
            TimeoutError("transport timeout")])
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        first = agg.absorb(instance.run_once())
        self.assertTrue(first.settled)
        self.assertEqual(first.state, "UNCONFIRMED")
        self.assertEqual(first.summary.state, m.UNKNOWN_DELIVERY)
        self.assertEqual(len(self.summary_calls(declaration)), 1)

        self.clock.advance(30.0)
        second = agg.absorb(instance.run_once())

        # Same transport key, so the real transport sees one message twice, not
        # two messages. Nothing is resent under a new key.
        self.assertEqual(second.state, "SENT")
        self.assertEqual(len(self.summary_calls(declaration)), 2)
        keys = {call["idempotency_key"] for call in self.summary_calls(declaration)}
        self.assertEqual(keys, {m.transport_key_for(declaration.summary_key)})
        self.assertEqual(self.ledger_rows()[0]["state"], m.DELIVERED)


class TheBoundsAreReal(Base):
    def test_a_second_tick_sends_nothing_again(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        self.tick(agg, instance, times=1)
        after_first = len(self.provider.calls)
        self.tick(agg, instance, times=4)

        self.assertEqual(len(self.provider.calls), after_first)
        self.assertEqual(len(self.ledger_rows()), 1)

    def test_a_fact_once_seen_does_not_become_pending_again(self):
        # A Run that has been read as completed does not become un-completed
        # because the next read failed: a purged Run, a device that went away,
        # a source timeout. Losing that would re-open a batch that had already
        # answered, and every later tick would be a fresh chance to lose it.
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A),
                                  SourceRefused("RUN_NOT_FOUND")],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        first, second = self.tick(agg, instance, times=2)

        self.assertTrue(first.settled)
        self.assertEqual(self.verdicts(first)[RUN_A], "VERIFIED")
        self.assertTrue(second.settled)
        self.assertEqual(self.verdicts(second)[RUN_A], "VERIFIED")
        self.assertEqual(second.state, "REPLAY")
        self.assertEqual(len(self.summary_calls(declaration)), 1)

    def test_the_summary_carries_no_payload_text(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices()
        agg = self.aggregator(declaration)

        [tick] = self.tick(agg, self.observer_for(declaration, source))

        text = self.summary_calls(declaration)[0]["text"]
        # The Run's assistant message is real payload text; the summary is a
        # verdict list, so it cannot carry it — structurally, not by scrub.
        self.assertNotIn("the deliverable is ready", text)
        for ref in (RUN_A, RUN_B, RUN_C):
            self.assertIn(ref, text)
        self.assertNotIn("\n\n", text)
        self.assertLessEqual(len(text), 1600)

    def test_an_offline_member_is_bounded_by_the_deadline(self):
        # The device never answers and the refusal is transient, so the observer
        # keeps waiting. Without a batch deadline the batch would wait forever;
        # the deadline is what turns "never answered" into one finite alert.
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [SourceRefused("RUN_NOT_FOUND")],
            f"cloudcli:{RUN_B}": [for_run("cloudcli", "completed", run_id=RUN_B,
                                          device=DEV_B, boot=BOOT_B)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        declaration = self.three_devices(expires_at=DEADLINE)
        agg = self.aggregator(declaration)
        instance = self.observer_for(declaration, source)

        first = agg.absorb(instance.run_once())
        self.assertFalse(first.settled)
        self.assertEqual(self.verdicts(first)[RUN_A], "PENDING")

        self.clock.advance(3601.0)
        second = agg.absorb(instance.run_once())

        self.assertTrue(second.settled)
        self.assertEqual(self.verdicts(second)[RUN_A], "OFFLINE")
        self.assertEqual(len(self.alert_calls(declaration, f"cloudcli:{RUN_A}",
                                              "OFFLINE")), 1)
        self.assertEqual(len(self.summary_calls(declaration)), 1)

    def test_the_aggregator_refuses_a_notifier_without_the_seam(self):
        # #460 has landed, so the refusal is no longer demonstrated with the
        # real notifier (that would now be refused for the wrong reason). It is
        # still the rule: a notifier without a public `send_text` is refused up
        # front, not at the moment a summary is due, because reaching into
        # #423's private `_send` would be a second write path to its provider.
        declaration = self.three_devices()
        plain = m.MilestoneNotifier(self.provider, self.ledger,
                                    conversation_ref=RECIPIENT, clock=self.clock)
        self.assertTrue(callable(plain.send_text), "#460 is in the tree")
        without_seam = type("NotifierWithoutSeam", (), {
            name: getattr(plain, name)
            for name in ("observe", "reconcile", "ledger", "provider")})
        with self.assertRaises(batch.BatchRefused) as caught:
            batch.BatchAggregator(declaration, notifier=without_seam(),
                                  clock=self.clock)
        self.assertEqual(caught.exception.code, "NOTIFIER_SEAM_MISSING")
        self.assertEqual(self.provider.calls, [])

    def test_the_retryable_set_matches_the_observers_transient_codes(self):
        # A guard against drift, not a copy: if #428 changes what it considers a
        # read that can clear, this batch must change with it or a permanent
        # refusal would wait for the deadline.
        self.assertEqual(set(batch.RETRYABLE_REFUSALS),
                         set(observer._TRANSIENT_CODES))

    def test_the_words_this_module_branches_on_are_theirs(self):
        # The classifier is a pure function of a row dict, so it restates the
        # words #428 and #423 emit. Restating is only safe while a drift is loud.
        for name in ("NO_EVENT", "SKIPPED_BACKOFF", "RATE_LIMITED",
                     "NOT_QUALIFYING", "EXPIRED", "REFUSED", "MILESTONE_REFUSED"):
            self.assertEqual(getattr(batch, name), getattr(observer, name), name)
        for code in batch.SCOPE_MISMATCH_CODES:
            self.assertEqual(code, getattr(observer, code), code)
        for name in ("VERIFIED_GREEN", "ORDINARY_GREEN", "YELLOW", "RED",
                     "UNKNOWN_OUTCOME", "SILENT", "DELIVERED", "DUPLICATE",
                     "UNKNOWN_DELIVERY"):
            mine = getattr(batch, name)
            theirs = getattr(m, "UNKNOWN" if name == "UNKNOWN_OUTCOME" else name)
            self.assertEqual(mine, theirs, name)
        self.assertEqual(batch.BATCH_SUMMARY not in m.MILESTONE_KINDS, True)
        self.assertEqual(batch.BATCH_ALERT not in m.MILESTONE_KINDS, True)
        for word in (batch.BATCH_SUMMARY, batch.BATCH_ALERT):
            self.assertNotIn(word, {m.VERIFIED_GREEN, m.ORDINARY_GREEN,
                                    m.YELLOW, m.RED, m.UNKNOWN, m.SILENT})

    def test_a_batch_needs_members_and_a_key_safe_id(self):
        with self.assertRaises(batch.BatchRefused) as empty:
            batch.Batch(batch_id=BATCH_ID, members=())
        self.assertEqual(empty.exception.code, "NO_MEMBERS")

        for bad in ("", "has space", "has:colon", "line\nbreak", "x" * 129):
            with self.assertRaises(batch.BatchRefused) as caught:
                batch.Batch(batch_id=bad, members=(
                    self.member(RUN_A, DEV_A, BOOT_A),))
            self.assertEqual(caught.exception.code, "BAD_BATCH_ID", bad)

    def test_a_member_cannot_be_something_the_observer_refuses_to_watch(self):
        # The declaration is validated by #428's own `Watch`, so a member is
        # never something the tick would refuse to construct at start time.
        with self.assertRaises(batch.BatchRefused) as no_boot:
            batch.BatchMember(source="cloudcli", ref=RUN_A, device_ref=DEV_A)
        self.assertEqual(no_boot.exception.code, "BAD_MEMBER")
        with self.assertRaises(batch.BatchRefused) as no_sha:
            batch.BatchMember(source="github_actions", ref="40486346485",
                              device_ref="device:ci-ProfessorZhi-serveros")
        self.assertEqual(no_sha.exception.code, "BAD_MEMBER")

    def test_the_watches_are_the_declaration_and_nothing_else(self):
        declaration = self.three_devices()
        watches = declaration.watches()
        self.assertEqual([watch.ref for watch in watches],
                         [RUN_A, RUN_B, RUN_C])
        self.assertEqual([watch.boot_id for watch in watches],
                         [BOOT_A, BOOT_B, BOOT_C])
        self.assertTrue(all(watch.expires_at == WINDOW_END for watch in watches))
        self.assertTrue(all(isinstance(watch, observer.Watch) for watch in watches))


class ARealObserverReport(Base):
    """The contract is the observer's report, not a dict this file invented."""

    def test_the_report_is_the_shape_the_aggregator_reads(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)]})
        declaration = batch.Batch(batch_id=BATCH_ID, members=(
            self.member(RUN_A, DEV_A, BOOT_A),))
        report = self.observer_for(declaration, source).run_once()

        self.assertEqual(report["schema"], observer.SCHEMA)
        row = report["rows"][0]
        self.assertEqual(set(row), {"source", "ref", "device_ref", "state",
                                    "outcome", "delivery_key", "delivered",
                                    "code", "why", "message"})
        tick = self.aggregator(declaration).absorb(report)
        self.assertEqual(self.verdicts(tick)[RUN_A], "VERIFIED")

    def test_rows_for_refs_outside_the_batch_are_ignored(self):
        source = ScriptedSource({
            f"cloudcli:{RUN_A}": [for_run("cloudcli", "completed", run_id=RUN_A,
                                          device=DEV_A, boot=BOOT_A)],
            f"cloudcli:{RUN_C}": [for_run("cloudcli", "completed", run_id=RUN_C,
                                          device=DEV_C, boot=BOOT_C)]})
        # The observer watches A and C; this dispatch owns only A.
        watched = batch.Batch(batch_id="observer-watches-both", members=(
            self.member(RUN_A, DEV_A, BOOT_A),
            self.member(RUN_C, DEV_C, BOOT_C)))
        mine = batch.Batch(batch_id=BATCH_ID, members=(
            self.member(RUN_A, DEV_A, BOOT_A),))
        report = self.observer_for(watched, source).run_once()

        tick = self.aggregator(mine).absorb(report)

        self.assertEqual(list(tick.verdicts), [f"cloudcli:{RUN_A}"])
        self.assertTrue(tick.settled)


if __name__ == "__main__":
    unittest.main()
