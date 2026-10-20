#!/usr/bin/env python3
"""The outbound Feishu transport, and the hop that was UNKNOWN before it.

What this file keeps false
--------------------------
    "a model was asked, therefore a person was told"   — the BotMux failure this
                                                          provider replaces
    "we sent it, therefore it was delivered"           — a failed send raises
    "sending a message authenticates a person"         — inbound stays UNSUPPORTED
    "the card went out and then a second card appeared" — PATCH names one id

The provider is `runtime/direct_lark_notify.py`; the transport under it is
`runtime/message_transport.py`, and the CLI is stood in for by monkeypatching
`run` — no subprocess, no socket, no Feishu.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / "runtime"


def _load(name: str, path: Path):
    """Load under `name` if it is not already registered, and return it.

    The name matters: `direct_lark_notify` looks `message_transport` up as
    `serveros_runtime_message_transport`, so the test registers it under that
    name to share one object rather than get a second copy.
    """
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# message_transport FIRST, under the name the provider resolves it by.
mt = _load("serveros_runtime_message_transport", RUNTIME / "message_transport.py")
direct = _load("serveros_direct_lark_notify", RUNTIME / "direct_lark_notify.py")
notifications = _load("serveros_runtime_notifications", RUNTIME / "notifications.py")

RECIPIENT = "user:ou_0000000000000000000000000000"
DEVICE = "device:tokyo-shared-dev-01"


def _cap_state(entry):
    """A capability entry's state, whether it is a bare word or {state, reason}."""
    return entry if isinstance(entry, str) else str(entry.get("state"))


def fake_run_factory(seen, *, message_id="om_real_1", fail=None):
    def fake_run(argv, *, timeout=120, cwd=None):
        seen.append(list(argv))
        if fail is not None:
            raise fail
        return {"ok": True, "data": {"message_id": message_id, "chat_id": "oc_1"}}
    return fake_run


class CapabilityTest(unittest.TestCase):
    def test_notification_transport_is_supported(self):
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        cap = provider.capabilities()["capabilities"][direct.CAPABILITY]
        self.assertEqual(_cap_state(cap), "SUPPORTED")

    def test_the_capability_name_is_the_one_notifications_probes(self):
        # The two strings are declared in two files; a test is what stops them
        # drifting into a provider that is never asked anything.
        self.assertEqual(direct.CAPABILITY, notifications.CAPABILITY)

    def test_inbound_responsibilities_are_unsupported_not_unknown(self):
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        caps = provider.capabilities()["capabilities"]
        for name in ("human.identity.verify", "human.approval.binding"):
            self.assertEqual(caps[name]["state"], "UNSUPPORTED", name)

    def test_inbound_calls_refuse_rather_than_improvise(self):
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        with self.assertRaises(direct.DirectLarkUnsupported):
            provider.request_approval(conversation_ref="c", question={},
                                      idempotency_key="k")
        with self.assertRaises(direct.DirectLarkUnsupported):
            provider.identify(conversation_ref="c")


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.seen = []
        self._real_run = mt.run
        mt.run = fake_run_factory(self.seen)
        self.addCleanup(self._restore)

    def _restore(self):
        mt.run = self._real_run

    def test_notify_sends_over_the_message_verb_as_the_bot(self):
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        answer = provider.notify(conversation_ref="conversation:ops",
                                 text="磁盘 / 已到 93%", idempotency_key="k" * 4)
        self.assertTrue(answer["delivered"])
        self.assertEqual(answer["remote_id"], "om_real_1")
        argv = self.seen[0]
        self.assertEqual(argv[0], mt.LARK_CLI)
        self.assertEqual(argv[1:3], ["im", "+messages-send"])
        self.assertIn("--as", argv)
        self.assertEqual(argv[argv.index("--as") + 1], "bot")
        self.assertIn(RECIPIENT.split(":", 1)[1], argv)
        self.assertIn("--idempotency-key", argv)
        self.assertIn("磁盘 / 已到 93%", argv)

    def test_a_send_that_did_not_arrive_raises_so_it_is_not_recorded(self):
        mt.run = fake_run_factory(self.seen, fail=mt.TransportFault("gateway 502"))
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        with self.assertRaises(direct.DirectLarkNotifyFault):
            provider.notify(conversation_ref="c", text="x", idempotency_key="k")

    def test_a_failed_state_is_not_read_as_delivered(self):
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        provider.sender = type("S", (), {
            "send_text": staticmethod(lambda text, *, idempotency_key: {
                "state": "FAILED", "why": "通道拒绝"})})()
        with self.assertRaises(direct.DirectLarkNotifyFault):
            provider.notify(conversation_ref="c", text="x", idempotency_key="k")

    def test_a_receipt_with_no_message_id_says_so(self):
        mt.run = fake_run_factory(self.seen, message_id=None)
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        answer = provider.notify(conversation_ref="c", text="x", idempotency_key="k")
        self.assertTrue(answer["delivered"])
        self.assertIsNone(answer["remote_id"])


class EnvTest(unittest.TestCase):
    def test_from_env_needs_a_destination(self):
        with self.assertRaises(direct.DirectLarkUnsupported):
            direct.DirectLarkHumanNotifyProvider.from_env(environ={})

    def test_from_env_reads_the_recipient(self):
        provider = direct.DirectLarkHumanNotifyProvider.from_env(
            environ={direct.RECIPIENT_ENV: RECIPIENT})
        self.assertEqual(provider.recipient, RECIPIENT)

    def test_a_bare_identifier_is_refused_at_construction(self):
        # The transport's own prefix rule, reached eagerly so a malformed
        # destination fails when the provider is built, not at the first alert.
        with self.assertRaises(mt.TransportFault):
            direct.DirectLarkHumanNotifyProvider("ou_no_prefix")

    def test_it_holds_no_credential(self):
        source = (RUNTIME / "direct_lark_notify.py").read_text(encoding="utf-8")
        for token in ("tenant_access_token", "app_secret", "Bearer ", "-----BEGIN"):
            self.assertNotIn(token, source)
        # It reads a destination and an identity, never a secret.
        self.assertNotIn("TOKEN", source)


class DeliverIntegrationTest(unittest.TestCase):
    """The hop `runtime/notifications.py#deliver` takes, end to end."""

    def setUp(self):
        self.seen = []
        self._real_run = mt.run
        self.addCleanup(self._restore)

    def _restore(self):
        mt.run = self._real_run

    def test_a_delivered_notification_is_delivered(self):
        mt.run = fake_run_factory(self.seen)
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        event = notifications.Event(event="alert.opened",
                                    device_ref="device:tokyo-shared-dev-01",
                                    at="2026-10-13T06:00:00Z",
                                    correlation_id="c1", subject_ref="filesystem:/",
                                    reason="DATA_FS_PRESSURE")
        delivery = notifications.deliver(provider, event,
                                         conversation_ref="conversation:ops")
        self.assertTrue(delivery.delivered)
        self.assertEqual(len(self.seen), 1)

    def test_a_failing_transport_is_not_delivered(self):
        mt.run = fake_run_factory(self.seen, fail=mt.TransportFault("timeout"))
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        event = notifications.Event(event="alert.opened",
                                    device_ref="device:tokyo-shared-dev-01",
                                    at="2026-10-13T06:00:00Z",
                                    correlation_id="c1", subject_ref="filesystem:/")
        delivery = notifications.deliver(provider, event,
                                         conversation_ref="conversation:ops")
        self.assertFalse(delivery.delivered)
        self.assertIn("TransportFault", delivery.reason or "")

    def test_an_unsupported_transport_is_never_called(self):
        # The capability gate, restated for this provider: if it ever declared
        # anything but SUPPORTED, `deliver` must not call it — the property that
        # makes the old BotMux path quiet rather than wrong.
        calls = []
        mt.run = fake_run_factory(calls)
        provider = direct.DirectLarkHumanNotifyProvider(RECIPIENT)
        provider.capabilities = lambda: {"capabilities": {
            direct.CAPABILITY: "UNKNOWN"}}
        event = notifications.Event(event="alert.opened",
                                    device_ref=DEVICE,
                                    at="2026-10-13T06:00:00Z", correlation_id="c")
        delivery = notifications.deliver(provider, event,
                                         conversation_ref="conversation:ops")
        self.assertFalse(delivery.delivered)
        self.assertEqual(calls, [])
        self.assertEqual(delivery.state, "UNKNOWN")


class CardCreateThenPatchTest(unittest.TestCase):
    """card-create -> PATCH the SAME remote_id, which is what V13's cadence needs."""

    def setUp(self):
        self.seen = []
        self._real_run = mt.run
        self.ids = iter(["om_card_1", "om_card_1"])
        self.addCleanup(self._restore)

    def _restore(self):
        mt.run = self._real_run

    def _run(self, argv, *, timeout=120, cwd=None):
        self.seen.append(list(argv))
        return {"ok": True, "data": {"message_id": next(self.ids)}}

    def test_create_returns_an_id_and_patch_names_it(self):
        mt.run = self._run
        sender = mt.sender(RECIPIENT)
        card = {"schema": "2.0", "config": {"update_multi": True},
                "header": {"template": "green",
                           "title": {"tag": "plain_text", "content": "Fleet 监控"}},
                "body": {"direction": "vertical",
                         "elements": [{"tag": "markdown", "content": "hi"}]}}
        created = sender.send_card(card, idempotency_key="card-1")
        self.assertEqual(created["state"], "DELIVERED")
        remote_id = created["provider"]["remote_id"]
        self.assertEqual(remote_id, "om_card_1")
        # The send verb and the interactive message type, per the CLI's contract.
        self.assertEqual(self.seen[0][1:3], ["im", "+messages-send"])
        self.assertIn("--msg-type", self.seen[0])
        self.assertEqual(self.seen[0][self.seen[0].index("--msg-type") + 1],
                         "interactive")
        # The card travels as JSON content, not as markdown.
        content = self.seen[0][self.seen[0].index("--content") + 1]
        self.assertEqual(json.loads(content)["schema"], "2.0")

        patched = sender.update({"message_id": remote_id,
                                 "sent_at": "2026-10-13T06:00:00Z", "card": card})
        self.assertEqual(patched["state"], "DELIVERED")
        argv = self.seen[1]
        self.assertEqual(argv[1:3], ["im", "messages"])
        self.assertEqual(argv[3], "patch")
        self.assertEqual(argv[argv.index("--message-id") + 1], remote_id)
        # The body carries the card as a STRING inside content, per the contract.
        body = json.loads(argv[argv.index("--data") + 1])
        self.assertIsInstance(body["content"], str)
        self.assertEqual(json.loads(body["content"])["schema"], "2.0")

    def test_a_patch_with_no_id_refuses_and_does_not_send_a_second_card(self):
        mt.run = self._run
        sender = mt.sender(RECIPIENT)
        answer = sender.update({"message_id": "", "sent_at": "2026-10-13T06:00:00Z",
                                "card": {"schema": "2.0"}})
        self.assertEqual(answer["state"], "FAILED")
        self.assertEqual(self.seen, [])  # nothing was sent — no second card

    def test_a_patch_past_the_window_refuses_and_does_not_send(self):
        mt.run = self._run
        sender = mt.sender(RECIPIENT)
        answer = sender.update({"message_id": "om_old",
                                "sent_at": "2020-01-01T00:00:00+00:00",
                                "card": {"schema": "2.0"}})
        self.assertEqual(answer["state"], "FAILED")
        self.assertEqual(self.seen, [])


class AlertProviderSelectionTest(unittest.TestCase):
    """`runtime/cli.py#_alert_provider` picks the direct transport when told to."""

    @classmethod
    def setUpClass(cls):
        cls.cli = _load("serveros_runtime_cli", RUNTIME / "cli.py")

    def test_a_configured_recipient_selects_the_direct_provider(self):
        import os
        prior = os.environ.get("SERVEROS_ALERT_RECIPIENT")
        os.environ["SERVEROS_ALERT_RECIPIENT"] = RECIPIENT
        self.addCleanup(self._restore, prior)
        provider = self.cli._alert_provider()
        self.assertEqual(provider.provider_id, direct.PROVIDER_ID)
        cap = provider.capabilities()["capabilities"][direct.CAPABILITY]
        self.assertEqual(_cap_state(cap), "SUPPORTED")

    def test_without_it_the_botmux_provider_is_kept(self):
        import os
        prior = os.environ.get("SERVEROS_ALERT_RECIPIENT")
        os.environ.pop("SERVEROS_ALERT_RECIPIENT", None)
        self.addCleanup(self._restore, prior)
        provider = self.cli._alert_provider()
        # Not removed, still constructible — the inbound surface depends on it.
        self.assertEqual(provider.provider_id, "provider:botmux-human-control")

    @staticmethod
    def _restore(prior):
        import os
        if prior is None:
            os.environ.pop("SERVEROS_ALERT_RECIPIENT", None)
        else:
            os.environ["SERVEROS_ALERT_RECIPIENT"] = prior


if __name__ == "__main__":
    unittest.main(verbosity=2)
