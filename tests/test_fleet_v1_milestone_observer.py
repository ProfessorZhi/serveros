#!/usr/bin/env python3
"""#424 — the observer that keeps working after the GPT turn is gone.

WHAT THIS FILE PINS
-------------------
#423 proved the delivery half offline. This file proves the TICK: a bounded
observer, driven only by its injected clock and source adapters, keeps reading
real refs after the dispatching chat has closed, applies scope rules the
classifier cannot apply for it, and hands exactly the right facts to the one
Tokyo provider — once, with a receipt, across restarts.

The acceptance matrix from #424:

* GPT dispatch returns; the Run turns terminal LATER; the observer alone
  notices and sends one significant green.
* A restart before and after sending does not resend; the ledger, not the
  observer's state file, decides that.
* Two observers (replicas) observing one milestone produce one DELIVERED and
  one DUPLICATE.
* A Run that finished BEFORE the observer started is still observed once.
* A changed boot, a missing/expired Run and a terminal that answers for another
  ref are UNKNOWN — never a green.
* `completed` with no trusted native output is UNKNOWN; a later read that does
  carry output is a NEW correction, delivered once.
* CI may qualify ONLY on the exact source SHA the watch names; a green on
  another commit, a Draft/non-qualifying watch, yellow and ordinary green send
  nothing.
* A provider failure is DELIVERY_UNKNOWN, never DELIVERED; a replay does not
  resend; an explicit reconcile inside the transport window can recover it.
* A RED milestone still sends its deterministic template when the caretaker
  summarizer raises.

EVIDENCE LABELS
---------------
CODE + UNIT are this file. Every source is scripted and the provider is
`FakeLark`: no network, no CLI, no credential, no service, no real Feishu send.
HOSTED_CI is the stacked PR's workflow. REAL_DEVICE, CLIENT_E2E and
REAL_FEISHU_DELIVERY are NOT_RUN and are not claimed.
"""
from __future__ import annotations

import ast
import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OBSERVER_DIR = REPO / "integrations" / "fleet-v1-milestone-observer"
OBSERVER_MODULE = OBSERVER_DIR / "observer.py"
FIXTURES = OBSERVER_DIR / "fixtures"

CAPABILITY = "human.notification.transport"
RECIPIENT = "user:ou_0000000000000000000000000000"
DEVICE = "device:tokyo-shared-dev-01"
RUN_ID = "run_demo_1"
CI_ID = "40486346485"
CI_DEVICE = "device:ci-ProfessorZhi-serveros"
BOOT = "boot-A"
SHA_OK = "3e9428ab5f057b5ac6a1da1cebda7bf05c47901c"
START = datetime(2026, 10, 27, 10, 0, tzinfo=timezone.utc).timestamp()


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


observer = _load("serveros_fleet_v1_milestone_observer", OBSERVER_MODULE)
m = observer.NOTIFY  # #423's module, loaded once under its canonical name


def fixture(source: str, name: str) -> dict:
    return json.loads((FIXTURES / source / f"{name}.json").read_text(encoding="utf-8"))


def payload(source: str, name: str, **changes) -> dict:
    value = copy.deepcopy(fixture(source, name))
    value.update(changes)
    return value


class Clock:
    """A fake clock. The observer never reads the wall clock itself."""

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
        return {"delivered": True, "remote_id": "om_default",
                "provider": self.provider_id, "transport": self.transport}


class SourceRefused(Exception):
    """The typed refusal shape #423's sources branch publishes."""

    def __init__(self, code: str):
        self.code = code
        self.detail = code
        super().__init__(code)


class ScriptedSource:
    """A `SourceRead` whose answers are a script, not a network.

    A list is consumed one answer per read and then keeps returning its last
    answer, so an observer that re-reads a terminal run sees the same fact —
    which is exactly the replay case the ledger must absorb.
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


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-observer-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = self.tmp / "milestones.sqlite"
        self.state = self.tmp / "observer-state.json"
        self.clock = Clock()
        self.provider = FakeLark()

    def make(self, watches, source, *, provider=None, clock=None,
             options=None, ledger=None, state=None, summarizer=None):
        clock = clock or self.clock
        notifier = m.MilestoneNotifier(
            provider or self.provider, ledger or self.ledger,
            conversation_ref=RECIPIENT, summarizer=summarizer, clock=clock)
        return observer.MilestoneObserver(
            source=source, notifier=notifier, state_path=state or self.state,
            watches=watches, clock=clock, options=options or observer.Options())

    def run_watch(self):
        return observer.Watch(source="cloudcli", ref=RUN_ID, device_ref=DEVICE,
                              boot_id=BOOT,
                              expires_at="2026-10-28T10:00:00Z")

    def ci_watch(self, **changes):
        base = dict(source="github_actions", ref=CI_ID, device_ref=CI_DEVICE,
                    source_sha=SHA_OK, expires_at="2026-10-28T10:00:00Z")
        base.update(changes)
        return observer.Watch(**base)

    def row(self, report, ref=RUN_ID):
        for item in report["rows"]:
            if item["ref"] == ref:
                return item
        raise AssertionError(f"no row for {ref}: {report['rows']}")


class ADispatchOutlivesTheChat(Base):
    """The end-to-end GPT-gone case: dispatch, later terminal, one message."""

    def test_the_observer_alone_finishes_a_run_the_chat_never_saw(self):
        source = ScriptedSource({"cloudcli:run_demo_1": [
            fixture("cloudcli", "running"), fixture("cloudcli", "completed")]})
        instance = self.make([self.run_watch()], source)

        first = instance.run_once()
        self.assertEqual(self.row(first)["state"], observer.NO_EVENT)
        self.assertEqual(self.row(first)["outcome"], m.SILENT)
        self.assertEqual(self.provider.calls, [])

        self.clock.advance(60)
        second = instance.run_once()
        row = self.row(second)
        self.assertEqual(row["state"], observer.DELIVERED)
        self.assertEqual(row["outcome"], m.VERIFIED_GREEN)
        self.assertTrue(row["delivered"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertEqual(self.provider.calls[0]["conversation_ref"], RECIPIENT)
        self.assertIn("VERIFIED_GREEN", self.provider.calls[0]["text"])

        receipt = m.MilestoneLedger(self.ledger).get(row["delivery_key"])
        self.assertEqual(receipt["state"], m.DELIVERED)
        self.assertEqual(receipt["remote_id"], "om_default")

        # Restart after the send: same ledger, same state, fresh objects.
        restarted = self.make([self.run_watch()], source)
        third = restarted.run_once()
        self.assertEqual(self.row(third)["state"], observer.DUPLICATE)
        self.assertTrue(self.row(third)["delivered"])
        self.assertEqual(len(self.provider.calls), 1, "a restart must not resend")

    def test_a_run_that_finished_before_the_observer_started_is_still_observed(self):
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        report = self.make([self.run_watch()], source).run_once()
        self.assertEqual(self.row(report)["state"], observer.DELIVERED)
        self.assertEqual(len(self.provider.calls), 1)


class TheStateFileIsNotALedger(Base):
    def test_losing_the_state_file_does_not_resend(self):
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        first = self.make([self.run_watch()], source).run_once()
        self.assertEqual(self.row(first)["state"], observer.DELIVERED)
        self.assertTrue(self.state.is_file())

        self.state.unlink()
        again = self.make([self.run_watch()], source).run_once()
        self.assertEqual(self.row(again)["state"], observer.DUPLICATE)
        self.assertEqual(len(self.provider.calls), 1)

        saved = json.loads(self.state.read_text(encoding="utf-8"))
        self.assertEqual(saved["schema"], observer.STATE_SCHEMA)
        # Scheduling facts only; delivery state is the SQLite ledger's answer.
        self.assertNotIn("DELIVERED", json.dumps(saved))

    def test_the_periodic_entry_point_is_bounded_and_injected(self):
        source = ScriptedSource({"cloudcli:run_demo_1": [
            fixture("cloudcli", "running"), fixture("cloudcli", "running"),
            fixture("cloudcli", "completed")]})
        instance = self.make([self.run_watch()], source)
        slept = []

        def sleep(seconds):
            slept.append(seconds)
            self.clock.advance(seconds)

        reports = list(instance.run_periodic(interval_seconds=60, sleep=sleep,
                                             max_ticks=3))
        self.assertEqual([self.row(r)["state"] for r in reports],
                         [observer.NO_EVENT, observer.NO_EVENT, observer.DELIVERED])
        self.assertEqual(slept, [60, 60])
        self.assertEqual(len(self.provider.calls), 1)


class BackoffRateLimitsAndExpiry(Base):
    def test_a_missing_run_backs_off_and_then_expires_unknown(self):
        source = ScriptedSource({"cloudcli:run_demo_1": SourceRefused("RUN_NOT_FOUND")})
        watch = observer.Watch(source="cloudcli", ref=RUN_ID, device_ref=DEVICE,
                               boot_id=BOOT,
                               expires_at=datetime.fromtimestamp(
                                   START + 45, tz=timezone.utc).isoformat())
        instance = self.make([watch], source,
                             options=observer.Options(base_backoff_seconds=30,
                                                      max_backoff_seconds=120))

        first = instance.run_once()
        self.assertEqual(self.row(first)["state"], observer.REFUSED)
        self.assertEqual(self.row(first)["code"], "RUN_NOT_FOUND")
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(self.provider.calls, [], "a missing run is never a green")

        self.clock.advance(10)
        second = instance.run_once()
        self.assertEqual(self.row(second)["state"], observer.SKIPPED_BACKOFF)
        self.assertEqual(len(source.calls), 1, "backoff must not read")

        self.clock.advance(20)  # exactly the 30s backoff
        third = instance.run_once()
        self.assertEqual(self.row(third)["state"], observer.REFUSED)
        self.assertEqual(len(source.calls), 2)
        self.assertEqual(self.row(third)["outcome"], observer.UNKNOWN)

        self.clock.advance(20)  # past the watch window, still inside new backoff
        fourth = instance.run_once()
        self.assertEqual(self.row(fourth)["state"], observer.EXPIRED)
        self.assertEqual(len(source.calls), 2)
        self.assertEqual(self.provider.calls, [])

    def test_one_tick_reads_each_source_at_most_as_often_as_allowed(self):
        source = ScriptedSource({
            "cloudcli:run_demo_1": [fixture("cloudcli", "running")],
            "cloudcli:run_demo_2": [fixture("cloudcli", "running")],
        })
        watches = [self.run_watch(),
                   observer.Watch(source="cloudcli", ref="run_demo_2",
                                  device_ref=DEVICE, boot_id=BOOT)]
        report = self.make(watches, source,
                           options=observer.Options(max_reads_per_source=1)).run_once()
        states = sorted(row["state"] for row in report["rows"])
        self.assertEqual(states, [observer.NO_EVENT, observer.RATE_LIMITED])
        self.assertEqual(len(source.calls), 1)
        self.assertEqual(source.calls, ["cloudcli:run_demo_1"])

        # The next tick starts with the ref that was NOT read, so a source with
        # more refs than the budget rotates instead of starving its tail.
        self.clock.advance(60)
        second = self.make(watches, source,
                           options=observer.Options(max_reads_per_source=1)).run_once()
        self.assertEqual(source.calls[-1], "cloudcli:run_demo_2")
        self.assertTrue(any(row["state"] == observer.RATE_LIMITED
                            for row in second["rows"]))

    def test_the_minimum_interval_is_measured_from_the_last_read(self):
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "running")]})
        instance = self.make([self.run_watch()], source,
                             options=observer.Options(min_interval_seconds=60))
        self.assertEqual(self.row(instance.run_once())["state"], observer.NO_EVENT)
        self.clock.advance(10)
        self.assertEqual(self.row(instance.run_once())["state"], observer.RATE_LIMITED)
        self.assertEqual(len(source.calls), 1)
        self.clock.advance(50)
        self.assertEqual(self.row(instance.run_once())["state"], observer.NO_EVENT)
        self.assertEqual(len(source.calls), 2)


class DuplicateObserversShareOneReceipt(Base):
    def test_two_replicas_produce_one_delivery_and_one_duplicate(self):
        source_a = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        source_b = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        one = self.make([self.run_watch()], source_a,
                        state=self.tmp / "state-a.json")
        two = self.make([self.run_watch()], source_b,
                        state=self.tmp / "state-b.json")

        first = one.run_once()
        second = two.run_once()
        self.assertEqual(self.row(first)["state"], observer.DELIVERED)
        self.assertEqual(self.row(second)["state"], observer.DUPLICATE)
        self.assertEqual(first["rows"][0]["delivery_key"],
                         second["rows"][0]["delivery_key"])
        self.assertEqual(len(self.provider.calls), 1)

        # A duplicate callback on the replica that sent it is also a duplicate.
        third = one.run_once()
        self.assertEqual(self.row(third)["state"], observer.DUPLICATE)
        self.assertEqual(len(self.provider.calls), 1)


class IdentityAndBootScope(Base):
    def test_a_run_bound_to_another_boot_is_out_of_scope(self):
        wrong_boot = payload("cloudcli", "completed",
                             bound_boot_id="boot-B",
                             run={**fixture("cloudcli", "completed")["run"],
                                  "bootId": "boot-B"})
        source = ScriptedSource({"cloudcli:run_demo_1": [wrong_boot]})
        instance = self.make([self.run_watch()], source)
        report = instance.run_once()
        self.assertEqual(self.row(report)["state"], observer.BOOT_SCOPE_MISMATCH)
        self.assertEqual(self.provider.calls, [])
        # A scope mismatch is terminal for this ref, not retried every tick.
        again = instance.run_once()
        self.assertEqual(self.row(again)["state"], observer.BOOT_SCOPE_MISMATCH)
        self.assertEqual(len(source.calls), 1)

    def test_a_run_that_reports_a_different_boot_than_the_dispatch_is_unknown(self):
        drifted = payload("cloudcli", "completed",
                          run={**fixture("cloudcli", "completed")["run"],
                               "bootId": "boot-B"})
        source = ScriptedSource({"cloudcli:run_demo_1": [drifted]})
        report = self.make([self.run_watch()], source).run_once()
        row = self.row(report)
        self.assertEqual(row["outcome"], m.UNKNOWN)
        self.assertEqual(row["state"], observer.DELIVERED)
        self.assertIn("boot identity", self.provider.calls[0]["text"])
        self.assertNotIn("VERIFIED_GREEN", self.provider.calls[0]["text"])

    def test_a_terminal_that_answers_for_another_ref_is_refused(self):
        other = payload("cloudcli", "completed",
                        run={**fixture("cloudcli", "completed")["run"],
                             "runId": "run_other"})
        source = ScriptedSource({"cloudcli:run_demo_1": [other]})
        report = self.make([self.run_watch()], source).run_once()
        self.assertEqual(self.row(report)["state"], observer.REF_MISMATCH)
        self.assertEqual(self.provider.calls, [])


class UntrustedCompletionAndCorrection(Base):
    def test_completed_without_native_output_is_unknown_not_success(self):
        source = ScriptedSource({"cloudcli:run_demo_1": [
            fixture("cloudcli", "completed-no-output")]})
        report = self.make([self.run_watch()], source).run_once()
        row = self.row(report)
        self.assertEqual(row["outcome"], m.UNKNOWN)
        self.assertTrue(row["delivered"])
        self.assertEqual(len(self.provider.calls), 1)
        self.assertNotIn("VERIFIED_GREEN", self.provider.calls[0]["text"])
        first_key = row["delivery_key"]

        # A later read that DOES carry output is a correction, not a replay.
        source.script["cloudcli:run_demo_1"] = [
            fixture("cloudcli", "completed")]
        second = self.make([self.run_watch()], source).run_once()
        corrected = self.row(second)
        self.assertEqual(corrected["outcome"], m.VERIFIED_GREEN)
        self.assertEqual(corrected["state"], observer.DELIVERED)
        self.assertNotEqual(corrected["delivery_key"], first_key)
        self.assertEqual(len(self.provider.calls), 2)


class CILocksTheSourceSha(Base):
    def test_only_a_green_on_the_named_commit_qualifies(self):
        source = ScriptedSource({"github_actions:40486346485": [
            fixture("github_actions", "green")]})
        report = self.make([self.ci_watch()], source).run_once()
        row = self.row(report, CI_ID)
        self.assertEqual(row["outcome"], m.VERIFIED_GREEN)
        self.assertEqual(row["state"], observer.DELIVERED)
        self.assertIn("CI_PASSED", self.provider.calls[0]["text"])
        self.assertNotIn("RELEASE_ACCEPTED", self.provider.calls[0]["text"])

    def test_a_green_on_another_sha_is_never_this_milestone(self):
        source = ScriptedSource({"github_actions:40486346485": [
            fixture("github_actions", "green-other-sha")]})
        instance = self.make([self.ci_watch()], source)
        report = instance.run_once()
        self.assertEqual(self.row(report, CI_ID)["state"],
                         observer.SOURCE_SHA_MISMATCH)
        self.assertEqual(self.provider.calls, [])
        again = instance.run_once()
        self.assertEqual(self.row(again, CI_ID)["state"],
                         observer.SOURCE_SHA_MISMATCH)
        self.assertEqual(len(source.calls), 1, "a wrong-sha ref is terminal")

    def test_a_draft_or_non_qualifying_watch_is_never_even_read(self):
        source = ScriptedSource({"github_actions:40486346485": [
            fixture("github_actions", "green")]})
        report = self.make([self.ci_watch(qualifies=False)], source).run_once()
        self.assertEqual(self.row(report, CI_ID)["state"], observer.NOT_QUALIFYING)
        self.assertEqual(source.calls, [], "a non-qualifying ref is not polled")
        self.assertEqual(self.provider.calls, [])

    def test_yellow_and_ordinary_green_stay_on_the_card(self):
        neutral = ScriptedSource({"github_actions:40486346485": [
            fixture("github_actions", "neutral")]})
        report = self.make([self.ci_watch()], neutral).run_once()
        self.assertEqual(self.row(report, CI_ID)["state"], observer.CARD_ONLY)
        self.assertEqual(self.row(report, CI_ID)["outcome"], m.YELLOW)
        self.assertEqual(self.provider.calls, [])

        ordinary = payload("github_actions", "green", milestone=False)
        source = ScriptedSource({"github_actions:40486346485": [ordinary]})
        report = self.make([self.ci_watch()], source).run_once()
        self.assertEqual(self.row(report, CI_ID)["state"], observer.CARD_ONLY)
        self.assertEqual(self.provider.calls, [])

    def test_action_required_is_red_and_sent(self):
        source = ScriptedSource({"github_actions:40486346485": [
            fixture("github_actions", "action-required")]})
        report = self.make([self.ci_watch()], source).run_once()
        row = self.row(report, CI_ID)
        self.assertEqual(row["outcome"], m.RED)
        self.assertEqual(row["state"], observer.DELIVERED)
        text = self.provider.calls[0]["text"]
        self.assertIn("CI_ACTION_REQUIRED", text)
        self.assertIn("Severity: ACTION", text)

    def test_a_ci_watch_must_name_its_source_sha(self):
        with self.assertRaises(ValueError):
            observer.Watch(source="github_actions", ref=CI_ID, device_ref=CI_DEVICE)


class ProviderFailuresAndReconcile(Base):
    def test_a_failed_send_is_unknown_and_a_replay_does_not_resend(self):
        self.provider.receipts = [{"delivered": False, "why": "network drop"}]
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        instance = self.make([self.run_watch()], source,
                             options=observer.Options(reconcile_unknown=False))

        first = instance.run_once()
        row = self.row(first)
        self.assertEqual(row["state"], observer.DELIVERY_UNKNOWN)
        self.assertFalse(row["delivered"])
        self.assertEqual(len(self.provider.calls), 1)

        self.clock.advance(60)
        second = instance.run_once()
        self.assertEqual(self.row(second)["state"], observer.UNKNOWN_REPLAY)
        self.assertEqual(len(self.provider.calls), 1, "UNKNOWN is not permission to resend")

    def test_an_in_window_reconcile_can_recover_the_same_key(self):
        self.provider.receipts = [{"delivered": False, "why": "network drop"}]
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        instance = self.make([self.run_watch()], source)
        key = self.row(instance.run_once())["delivery_key"]
        self.assertIsNotNone(key)

        self.clock.advance(60)
        recovered = instance.reconcile(key)
        self.assertEqual(recovered.state, m.DELIVERED)
        self.assertTrue(recovered.delivered)
        self.assertEqual(len(self.provider.calls), 2)

    def test_outside_the_transport_window_reconcile_refuses(self):
        self.provider.receipts = [{"delivered": False, "why": "network drop"}]
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        instance = self.make([self.run_watch()], source)
        key = self.row(instance.run_once())["delivery_key"]
        self.clock.advance(m._idempotency_window() + 60)
        answer = instance.reconcile(key)
        self.assertEqual(answer.state, m.UNKNOWN_DELIVERY)
        self.assertEqual(len(self.provider.calls), 1)

    def test_an_unsupported_capability_never_becomes_delivered(self):
        provider = FakeLark(capability="UNSUPPORTED")
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        report = self.make([self.run_watch()], source, provider=provider).run_once()
        self.assertEqual(self.row(report)["state"], observer.DELIVERY_UNKNOWN)
        self.assertEqual(provider.calls, [])

    def test_a_red_template_survives_a_failing_caretaker(self):
        def boom(_facts):
            raise RuntimeError("model unavailable")

        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "failed")]})
        report = self.make([self.run_watch()], source, summarizer=boom).run_once()
        row = self.row(report)
        self.assertEqual(row["outcome"], m.RED)
        self.assertEqual(row["state"], observer.DELIVERED)
        text = self.provider.calls[0]["text"]
        self.assertIn("RUN_FAILED", text)
        self.assertNotIn("Summary from caretaker", text)


class RecoveryScopeAndBadState(Base):
    def test_the_next_tick_recovers_an_unknown_delivery_inside_the_window(self):
        self.provider.receipts = [{"delivered": False, "why": "network drop"}]
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        instance = self.make([self.run_watch()], source)
        key = self.row(instance.run_once())["delivery_key"]
        self.assertEqual(len(self.provider.calls), 1)

        self.clock.advance(60)  # inside the transport's dedupe window
        second = instance.run_once()
        self.assertEqual(self.row(second)["state"], observer.DELIVERED)
        self.assertEqual(len(self.provider.calls), 2)
        self.assertEqual(self.provider.calls[0]["idempotency_key"], key)
        self.assertEqual(self.provider.calls[1]["idempotency_key"], key,
                         "a recovery re-asks the SAME key, never a new message")

    def test_a_permission_refusal_is_terminal_not_retried_forever(self):
        source = ScriptedSource({"cloudcli:run_demo_1": SourceRefused("PERMISSION_DENIED")})
        instance = self.make([self.run_watch()], source)
        self.assertEqual(self.row(instance.run_once())["state"], observer.REFUSED)
        self.clock.advance(3600)
        again = instance.run_once()
        self.assertEqual(self.row(again)["state"], observer.REFUSED)
        self.assertEqual(len(source.calls), 1, "a bind refusal cannot clear by waiting")

    def test_a_cloudcli_watch_must_name_its_boot(self):
        with self.assertRaises(ValueError):
            observer.Watch(source="cloudcli", ref=RUN_ID, device_ref=DEVICE)

    def test_a_redefined_watch_clears_the_previous_terminal(self):
        source = ScriptedSource({"github_actions:40486346485": [
            fixture("github_actions", "green")]})
        report = self.make([self.ci_watch(qualifies=False)], source).run_once()
        self.assertEqual(self.row(report, CI_ID)["state"], observer.NOT_QUALIFYING)
        self.assertEqual(source.calls, [])

        # Same ref, now qualifying (a Draft moved to ready): the scheduling
        # entry resets, and the milestone is observed rather than suppressed.
        report = self.make([self.ci_watch()], source).run_once()
        self.assertEqual(self.row(report, CI_ID)["state"], observer.DELIVERED)
        self.assertEqual(len(self.provider.calls), 1)

    def test_corrupt_scheduling_state_does_not_wedge_the_tick(self):
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "completed")]})
        self.state.write_text(json.dumps({
            "schema": observer.STATE_SCHEMA,
            "entries": {f"cloudcli:{RUN_ID}": {"attempts": "not-a-number"}},
        }))
        report = self.make([self.run_watch()], source).run_once()
        self.assertEqual(self.row(report)["state"], observer.DELIVERED)

    def test_zero_ticks_is_no_tick(self):
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "running")]})
        instance = self.make([self.run_watch()], source)
        self.assertEqual(list(instance.run_periodic(
            interval_seconds=60, sleep=lambda _s: None, max_ticks=0)), [])


class OneProviderAndNoHiddenSideEffects(unittest.TestCase):
    def test_the_observer_constructs_no_provider_and_imports_no_io_client(self):
        tree = ast.parse(OBSERVER_MODULE.read_text(encoding="utf-8"))
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports |= {alias.name.split(".")[0] for alias in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.add(node.module.split(".")[0])
        forbidden = {"lark", "requests", "urllib", "socket", "subprocess",
                     "http", "httpx", "aiohttp"}
        self.assertEqual(imports & forbidden, set())

        calls = [node for node in ast.walk(tree)
                 if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name)
                 and node.func.id in {"MilestoneNotifier", "MilestoneLedger"}]
        self.assertEqual(calls, [], "the notifier and ledger are injected, not built")

    def test_the_state_file_has_only_scheduling_fields(self):
        tmp = Path(tempfile.mkdtemp(prefix="serveros-observer-state-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        clock = Clock()
        provider = FakeLark()
        source = ScriptedSource({"cloudcli:run_demo_1": [fixture("cloudcli", "running")]})
        notifier = m.MilestoneNotifier(provider, tmp / "l.sqlite",
                                       conversation_ref=RECIPIENT, clock=clock)
        instance = observer.MilestoneObserver(
            source=source, notifier=notifier, state_path=tmp / "s.json",
            watches=[observer.Watch(source="cloudcli", ref=RUN_ID,
                                    device_ref=DEVICE, boot_id=BOOT)], clock=clock)
        instance.run_once()
        saved = json.loads((tmp / "s.json").read_text(encoding="utf-8"))
        entry = saved["entries"][f"cloudcli:{RUN_ID}"]
        self.assertLessEqual(
            set(entry),
            {"attempts", "next_attempt_at", "last_read_at", "last_code",
             "last_state", "last_delivery_key", "terminal", "scope",
             "needs_reconcile"})


if __name__ == "__main__":
    unittest.main()
