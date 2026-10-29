#!/usr/bin/env python3
"""The public send seam a batch summary needs, and its refusals.

WHY THIS FILE EXISTS
--------------------
#459's `BatchAggregator` raises `NOTIFIER_SEAM_MISSING` because it has no public
way to send a caller-authored sentence: `observe()` derives the whole message
from a payload and `_send()` is private. A batch summary is a statement about
*several* milestones, not a milestone, so it cannot be expressed as a payload.

The seam must be the *existing* outbound half with a caller-supplied sentence:
same ledger claim, same 50-character transport digest, same UNKNOWN, same
receipt, same reconcile. Anything else is a second write path to the one
provider, and two replicas could both send.

These are **negative** tests in the sense that matters: each one names a way the
seam could quietly become a second sender and refuses it.

EVIDENCE LABELS
---------------
CODE + UNIT are this file. No network, no CLI, no credential, no service, no
model, no Feishu. `SDK` — a real notification through the product IM — is
**NOT_RUN** and is not claimed here.
"""
from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = (REPO / "integrations" / "fleet-v1-milestone-notify"
               / "milestone_notify.py")
MODULE_NAME = "serveros_milestone_notify_batch_seam"
CAPABILITY = "human.notification.transport"
RECIPIENT = "user:ou_0000000000000000000000000000"
BASE = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)

BATCH_SUMMARY = "BATCH_SUMMARY"
BATCH_ALERT = "BATCH_ALERT"


def _load():
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


m = _load()


class Clock:
    def __init__(self, at: datetime = BASE):
        self.at = at

    def __call__(self):
        return self.at

    def advance(self, **kwargs):
        self.at = self.at + timedelta(**kwargs)


class FakeLark:
    """The one outbound provider, standing in. No CLI, no socket, no token."""

    provider_id = "provider:fake-lark"
    transport = "fake"

    def __init__(self, receipts=None, capability="SUPPORTED"):
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
        return {"delivered": True, "remote_id": "om_batch_1",
                "provider": self.provider_id, "transport": self.transport}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-batch-seam-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = self.tmp / "milestones.sqlite"
        self.clock = Clock()
        self.provider = FakeLark()
        self.requires_seam()

    def requires_seam(self):
        if not hasattr(m.MilestoneNotifier, "send_text"):
            self.fail(
                "MilestoneNotifier has no public `send_text`; #459's aggregator "
                "cannot send a batch sentence without reaching into `_send`, "
                "which would be a second write path to the one provider")

    def notifier(self, provider=None, ledger=None):
        return m.MilestoneNotifier(
            provider or self.provider, ledger or self.ledger,
            conversation_ref=RECIPIENT, clock=self.clock)

    def send(self, notifier=None, *, key="batch:b-1:summary",
             text="Three devices finished.", outcome=BATCH_SUMMARY,
             milestone_key="batch:b-1"):
        return (notifier or self.notifier()).send_text(
            delivery_key=key, milestone_key=milestone_key, outcome=outcome,
            text=text, why="batch settled")


class TheSeamIsPublicAndNarrow(unittest.TestCase):
    def test_send_text_is_public_and_send_stays_private(self):
        """The seam is a public method; `_send` stays the private write path."""
        self.assertTrue(callable(getattr(m.MilestoneNotifier, "send_text", None)),
                        "send_text must exist and be public")
        self.assertTrue(hasattr(m.MilestoneNotifier, "_send"),
                        "_send must still exist; the seam delegates to it")
        self.assertFalse(
            any(name == "send" for name in vars(m.MilestoneNotifier)),
            "the module must not grow a second, unrestricted `send`")

    def test_the_seam_matches_the_signature_the_batch_contract_calls(self):
        """#459 calls `send_text(*, delivery_key, milestone_key, outcome, text, why)`.

        #459 detects the seam with `callable(getattr(notifier, "send_text"))` and
        then calls it with those five keywords. A seam with a different shape
        would satisfy the detection and fail at the call, which is the worst of
        both — construction passes and the summary is lost at the moment it is
        due.
        """
        import inspect
        signature = inspect.signature(m.MilestoneNotifier.send_text)
        parameters = signature.parameters
        required = {"delivery_key", "milestone_key", "outcome", "text", "why"}
        self.assertLessEqual(required, set(parameters))
        for name in required:
            self.assertEqual(parameters[name].kind,
                             inspect.Parameter.KEYWORD_ONLY,
                             f"{name} must be keyword-only, as #459 calls it")
        missing = [name for name in required
                   if parameters[name].default is not inspect.Parameter.empty]
        self.assertEqual(missing, [], "every argument the contract passes is required")

    def test_the_seam_takes_no_provider_and_no_credential(self):
        """A caller cannot substitute a provider through the seam."""
        import inspect
        signature = inspect.signature(m.MilestoneNotifier.send_text)
        for forbidden in ("provider", "transport", "token", "credential",
                          "webhook", "channel"):
            self.assertNotIn(forbidden, signature.parameters,
                             f"send_text must not accept {forbidden!r}; the "
                             "provider is the notifier's, injected once")


class ABatchIsNotAMilestone(Base):
    def test_batch_outcomes_are_not_in_the_milestone_vocabulary(self):
        self.assertNotIn(BATCH_SUMMARY, m.MILESTONE_KINDS)
        self.assertNotIn(BATCH_ALERT, m.MILESTONE_KINDS)
        for kind in m.MILESTONE_KINDS:
            self.assertNotIn("BATCH", kind)

    def test_the_seam_refuses_a_milestone_outcome(self):
        """The mechanical half of "a batch must not pretend to be a milestone"."""
        for milestone in sorted(m.MILESTONE_KINDS):
            with self.subTest(outcome=milestone):
                with self.assertRaises(m.MilestoneRefused):
                    self.send(outcome=milestone)
        self.assertEqual(self.provider.calls, [],
                         "a refused outcome must never reach the provider")

    def test_the_two_sets_are_disjoint(self):
        self.assertEqual(m.NON_MILESTONE_OUTCOMES & m.MILESTONE_KINDS, set())

    def test_the_ledger_row_keeps_the_batch_outcome(self):
        self.send()
        self.assertEqual(len(self.provider.calls), 1)
        row = m.MilestoneLedger(self.ledger).get("batch:b-1:summary")
        self.assertIsNotNone(row)
        self.assertEqual(row["outcome"], BATCH_SUMMARY,
                         "the seam must not relabel a batch as a milestone")

    def test_an_ack_only_member_is_never_reported_as_success(self):
        """A batch whose member never started is not a green milestone."""
        observation = self.send(
            text="batch b-1: 1 queued (ACK only), 2 settled — not a success")
        self.assertEqual(observation.outcome, BATCH_SUMMARY)
        self.assertNotEqual(observation.outcome, "VERIFIED_GREEN")
        row = m.MilestoneLedger(self.ledger).get(observation.delivery_key)
        self.assertEqual(row["outcome"], BATCH_SUMMARY,
                         "a batch that reports a queued member must keep its own "
                         "outcome rather than borrowing a milestone one")


class OneDispatchSendsOneSummary(Base):
    def test_a_summary_naming_three_devices_is_sent_once(self):
        observation = self.send(
            text=("batch b-1 settled — device:tokyo 2/2, device:work-fj 1/1, "
                  "device:llm-lab-dell 1/1 (4 members)"))
        self.assertEqual(len(self.provider.calls), 1)
        self.assertTrue(observation.delivered)
        self.assertIn("device:tokyo", self.provider.calls[0]["text"])
        self.assertIn("device:llm-lab-dell", self.provider.calls[0]["text"])

    def test_two_batch_identities_do_not_collide(self):
        """Different batches get different transport keys, both delivered."""
        self.send(key="batch:b-1:summary", milestone_key="batch:b-1")
        self.send(key="batch:b-2:summary", milestone_key="batch:b-2",
                  text="Second batch settled.")
        self.assertEqual(len(self.provider.calls), 2)
        first, second = (call["idempotency_key"] for call in self.provider.calls)
        self.assertNotEqual(first, second,
                            "two batches shared one transport key, so the "
                            "provider would dedupe the second away")
        self.assertLessEqual(len(first), 50)
        self.assertLessEqual(len(second), 50)

    def test_a_summary_and_an_alert_for_one_batch_do_not_collide(self):
        self.send(key="batch:b-1:summary")
        self.send(key="batch:b-1:alert:cloudcli:run_9:FAILED",
                  outcome=BATCH_ALERT, text="batch b-1: one member failed")
        self.assertEqual(len(self.provider.calls), 2)
        keys = {call["idempotency_key"] for call in self.provider.calls}
        self.assertEqual(len(keys), 2)


class ReplayAndRestartDoNotResend(Base):
    def test_a_duplicate_send_under_the_same_key_does_not_resend(self):
        self.send()
        self.send()
        self.assertEqual(len(self.provider.calls), 1,
                         "a repeated key is the same fact, not a second message")

    def test_a_restart_replays_the_receipt_and_sends_nothing(self):
        self.send()
        self.assertEqual(len(self.provider.calls), 1)

        # A new process: new notifier object, same ledger, same provider stand-in.
        restarted = self.notifier()
        observation = self.send(notifier=restarted)
        self.assertEqual(len(self.provider.calls), 1,
                         "the restart re-sent a summary the ledger already owns")
        self.assertTrue(observation.delivered)
        self.assertEqual(observation.delivery_key, "batch:b-1:summary")

    def test_the_transport_key_is_the_short_digest(self):
        self.send()
        sent = self.provider.calls[0]["idempotency_key"]
        self.assertLessEqual(len(sent), 50)
        self.assertNotEqual(sent, "batch:b-1:summary",
                            "the full identity must not be handed to the "
                            "transport, which truncates at 50")


class ProviderRefusalsAreUnknownNotDelivered(Base):
    def test_an_unsupported_capability_is_unknown(self):
        self.provider = FakeLark(capability="UNSUPPORTED")
        observation = self.send()
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)
        self.assertFalse(observation.delivered)
        self.assertEqual(self.provider.calls, [],
                         "an unsupported transport must not be called")

    def test_a_raising_provider_is_unknown_and_leaves_no_in_flight(self):
        self.provider = FakeLark(receipts=[RuntimeError("lark-cli exited 2")])
        observation = self.send()
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)
        self.assertFalse(observation.delivered)
        row = m.MilestoneLedger(self.ledger).get("batch:b-1:summary")
        self.assertNotEqual(row["state"], m.IN_FLIGHT,
                            "a failed send must not strand the claim in flight")

    def test_a_receipt_without_a_message_id_is_unknown(self):
        self.provider = FakeLark(receipts=[{"delivered": True}])
        observation = self.send()
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY,
                         "an explicit message id is what makes a delivery claim")
        self.assertFalse(observation.delivered)

    def test_an_in_window_reconcile_may_recover_by_asking_again(self) -> None:
        """The documented recovery: same key, inside the transport window.

        This is not a resend under a new key — the provider dedupes on the key
        it receives, which is why the seam must hand it the same digest.
        """
        self.provider = FakeLark(receipts=[RuntimeError("boom")])
        observation = self.send()
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)
        prior = self.notifier().reconcile("batch:b-1:summary")
        self.assertTrue(prior.delivered)
        self.assertEqual(len(self.provider.calls), 2,
                         "the recovery is one re-ask under the same key")
        self.assertEqual(self.provider.calls[0]["idempotency_key"],
                         self.provider.calls[1]["idempotency_key"],
                         "a recovery that uses a new key is a second message")

    def test_outside_the_window_never_issued_stays_unknown(self) -> None:
        """Past the window a repeat could be a second message, so it is refused."""
        self.provider = FakeLark(receipts=[RuntimeError("boom")])
        observation = self.send()
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)

        window = m._idempotency_window()
        self.clock.advance(seconds=window + 60)
        prior = self.notifier().reconcile("batch:b-1:summary")
        self.assertFalse(prior.delivered,
                         "reconcile must not promote a never-delivered row "
                         "outside the dedupe window; a repeat there could be a "
                         "second message")
        self.assertEqual(len(self.provider.calls), 1,
                         "no second provider call is made outside the window")

if __name__ == "__main__":
    unittest.main()
