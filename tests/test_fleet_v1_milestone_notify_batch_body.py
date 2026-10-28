#!/usr/bin/env python3
"""The send seam's message BODY: newlines are structure, other controls are not.

WHY THIS FILE EXISTS
--------------------
`send_text` validated its sentence with `_text`, which refuses every character
below space because it validates **identities** — a device ref or a run id
containing a newline is corrupt. A message body is the opposite: `compose()`
builds milestone text out of newline-separated labelled lines, so refusing LF
here refused every real batch summary, as #470 found.

The fix must not loosen the identity check with it. So the rules are tested as a
pair:

* a body may contain newlines and keep them;
* a body may **not** contain CR, TAB, NUL or any other control character — those
  are refused rather than silently stripped, because a caller that sends one has
  a bug and a mangled message hides it;
* `delivery_key`, `milestone_key` and `outcome` stay strict single-line values.

EVIDENCE LABELS
---------------
CODE + UNIT are this file. No network, no CLI, no credential, no service, no
model, no Feishu. `SDK` — a real IM notification — is **NOT_RUN**.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MODULE_PATH = (REPO / "integrations" / "fleet-v1-milestone-notify"
               / "milestone_notify.py")
MODULE_NAME = "serveros_milestone_notify_batch_body"
CAPABILITY = "human.notification.transport"
RECIPIENT = "user:ou_0000000000000000000000000000"
BASE = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)

SUMMARY = "BATCH_SUMMARY"

#: A summary of the shape the batch aggregator actually composes: labelled lines.
MULTILINE = ("batch b-1 settled — 3 devices, 4 members\n"
             "device:tokyo 2/2 verified\n"
             "device:work-fj 1/1 verified\n"
             "device:llm-lab-dell 1/1 queued (ACK only)")


def _load():
    if MODULE_NAME in sys.modules:
        return sys.modules[MODULE_NAME]
    spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


m = _load()


class FakeLark:
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
        return {"delivered": True, "remote_id": "om_body_1",
                "provider": self.provider_id, "transport": self.transport}


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="serveros-batch-body-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ledger = self.tmp / "milestones.sqlite"
        self.provider = FakeLark()

    def notifier(self):
        return m.MilestoneNotifier(self.provider, self.ledger,
                                   conversation_ref=RECIPIENT,
                                   clock=lambda: BASE)

    def send(self, text=MULTILINE, **overrides):
        kwargs = dict(delivery_key="batch:b-1:summary",
                      milestone_key="batch:b-1", outcome=SUMMARY, text=text,
                      why="batch settled")
        kwargs.update(overrides)
        return self.notifier().send_text(**kwargs)


class ABodyMayHaveNewlines(Base):
    def test_a_multiline_summary_is_accepted_and_kept(self):
        """The regression #470 found: real summaries are labelled lines."""
        observation = self.send()
        self.assertTrue(observation.delivered)
        self.assertEqual(len(self.provider.calls), 1)
        sent = self.provider.calls[0]["text"]
        self.assertEqual(sent.count("\n"), MULTILINE.count("\n"),
                         "the newlines are structure and must survive")
        self.assertIn("device:llm-lab-dell 1/1 queued (ACK only)", sent)

    def test_the_ledger_keeps_the_multiline_intent(self):
        self.send()
        row = m.MilestoneLedger(self.ledger).get("batch:b-1:summary")
        self.assertEqual(row["outcome"], SUMMARY)
        self.assertIn("device:tokyo", row["intent"])
        self.assertIn("\\n", row["intent"],
                      "the intent is JSON, so a newline appears escaped — its "
                      "presence is what proves the body was stored whole")

    def test_a_multiline_body_still_gets_a_short_transport_key(self):
        self.send()
        key = self.provider.calls[0]["idempotency_key"]
        self.assertLessEqual(len(key), 50)
        self.assertNotIn("\n", key)

    def test_a_duplicate_multiline_send_does_not_resend(self):
        self.send()
        self.send()
        self.assertEqual(len(self.provider.calls), 1)


class ControlsOtherThanNewlineAreRefused(Base):
    def _refused(self, text):
        with self.assertRaises(m.MilestoneRefused):
            self.send(text=text)
        self.assertEqual(self.provider.calls, [],
                         "a refused body must never reach the provider")

    def test_a_carriage_return_is_refused(self):
        """CRLF is a Windows line ending and would reach the IM as a raw CR."""
        self._refused("line one\r\nline two")

    def test_a_tab_is_refused(self):
        self._refused("device:tokyo\t2/2")

    def test_a_nul_is_refused(self):
        self._refused("device:tokyo\x00 2/2")

    def test_other_c_control_characters_are_refused(self):
        for control in ("\x07", "\x1b", "\x0b", "\x0c", "\x08"):
            with self.subTest(control=hex(ord(control))):
                self.setUp()
                self._refused(f"device:tokyo{control} 2/2")

    def test_blank_and_whitespace_only_bodies_are_refused(self):
        for blank in ("", "   ", "\n", " \n \n "):
            with self.subTest(blank=repr(blank)):
                self.setUp()
                self._refused(blank)

    def test_an_oversized_body_is_refused(self):
        self._refused("x" * 2001)

    def test_a_body_at_the_limit_is_accepted(self):
        observation = self.send(text="x" * 2000)
        self.assertTrue(observation.delivered)


class IdentitiesStayStrict(Base):
    def test_the_delivery_key_still_refuses_a_newline(self):
        with self.assertRaises(m.MilestoneRefused):
            self.send(delivery_key="batch:b-1\nsummary")
        self.assertEqual(self.provider.calls, [])

    def test_the_milestone_key_still_refuses_a_newline(self):
        with self.assertRaises(m.MilestoneRefused):
            self.send(milestone_key="batch:b-1\nother")
        self.assertEqual(self.provider.calls, [])

    def test_the_outcome_still_refuses_a_newline(self):
        """An *interior* newline. Trailing whitespace is normalised by `_text`'s
        `.strip()` for every identity, which is existing behaviour and not
        something this change may alter."""
        with self.assertRaises(m.MilestoneRefused):
            self.send(outcome="BATCH\nSUMMARY")
        self.assertEqual(self.provider.calls, [])

    def test_the_identity_validator_is_unchanged(self):
        """`_text` still refuses every control character, LF included."""
        for value in ("a\nb", "a\tb", "a\rb", "a\x00b"):
            with self.subTest(value=repr(value)):
                with self.assertRaises(m.MilestoneRefused):
                    m._text(value, "identity", 64)


class ProviderSafetyIsUnchanged(Base):
    def test_a_multiline_send_that_raises_is_unknown_not_delivered(self):
        self.provider = FakeLark(receipts=[RuntimeError("lark-cli exited 2")])
        observation = self.send()
        self.assertEqual(observation.state, m.UNKNOWN_DELIVERY)
        self.assertFalse(observation.delivered)
        row = m.MilestoneLedger(self.ledger).get("batch:b-1:summary")
        self.assertNotEqual(row["state"], m.IN_FLIGHT)

    def test_secrets_are_still_redacted_from_a_multiline_body(self):
        observation = self.send(
            text="batch b-1 settled\nauthorization: bearer abcdef123456")
        self.assertTrue(observation.delivered)
        self.assertNotIn("abcdef123456", self.provider.calls[0]["text"])


if __name__ == "__main__":
    unittest.main()
