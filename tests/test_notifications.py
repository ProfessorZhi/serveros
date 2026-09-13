#!/usr/bin/env python3
"""Tests for ServerOS semantic events and their delivery (§10).

Every test here keeps false a sentence somebody would otherwise write: "the
person was told", "it could not be delivered so it must be broken", "the
provider said so", "we only sent it once".
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


notifications = _load("serveros_runtime_notifications",
                      ROOT / "runtime" / "notifications.py")
target = _load("serveros_abilities_target", ROOT / "abilities" / "target.py")

# The nine names §10 states. Written out here rather than imported from the
# module under test, so that a dropped event is a failing assertion instead of a
# smaller vocabulary that every other test agrees with.
STATED_EVENTS = (
    "session.created", "session.waiting_input", "turn.completed", "turn.failed",
    "approval.required", "device.unavailable", "provider.degraded",
    "handoff.completed", "run.terminated",
)

DEVICE = "device:server-beijing"


def support(state: str, reason: str | None = None) -> dict:
    return {"state": state, "reason": reason}


class RecordingProvider:
    """A HumanControlProvider that answers capability probes and records calls.

    `state` is what its capability table says about the notification transport.
    `raises` makes the capability call itself fail, which is a different thing:
    a provider nobody could ask, rather than a provider that answered no.
    """

    def __init__(self, state="SUPPORTED", *, reason=None, raises=False,
                 notify_raises=None, provider_id="provider:test-human-control",
                 omit=False):
        self.provider_id = provider_id
        self._state = state
        self._reason = reason
        self._raises = raises
        self._notify_raises = notify_raises
        self._omit = omit
        self.calls: list[dict] = []
        self.capability_probes = 0

    def capabilities(self) -> dict:
        self.capability_probes += 1
        if self._raises:
            raise RuntimeError("the transport is not answering")
        table = {}
        if not self._omit:
            table["human.notification.transport"] = (
                self._state if self._reason is None
                else {"state": self._state, "reason": self._reason})
        return {"provider": self.provider_id, "transport": "test",
                "capabilities": table, "measured_by": "fixture"}

    def notify(self, *, conversation_ref: str, text: str, idempotency_key: str) -> dict:
        self.calls.append({"conversation_ref": conversation_ref, "text": text,
                           "idempotency_key": idempotency_key})
        if self._notify_raises:
            raise self._notify_raises
        return {"accepted": True, "message_ref": f"om_{len(self.calls)}"}


def event(name="turn.failed", **kwargs) -> "notifications.Event":
    fields = {"event": name, "device_ref": DEVICE, "at": "2026-09-14T00:00:00Z",
              "correlation_id": "corr-1", "session_ref": "session:srv:1"}
    fields.update(kwargs)
    return notifications.Event(**fields)


class TestTheVocabularyIsClosed(unittest.TestCase):
    def test_the_nine_events_are_the_nine_events(self):
        self.assertEqual(notifications.events(), STATED_EVENTS)

    def test_an_invented_event_name_is_refused(self):
        for name in ("error", "session.thing", "turn.done", "SESSION.CREATED", ""):
            with self.subTest(name=name):
                with self.assertRaises(notifications.NotificationError):
                    event(name)

    def test_every_event_has_a_sentence(self):
        # An event with no template is an event that cannot be delivered as one,
        # and the failure would land at delivery time, in the path that has no
        # second chance. This asserts the gap is closed at the source.
        for name in notifications.events():
            with self.subTest(event=name):
                self.assertTrue(notifications.render(event(name)))

    def test_every_event_has_a_severity_from_the_declared_set(self):
        # `severity()` defaults to INFO for an unlisted event, so a missing entry
        # would not raise — it would quietly arrive at the lowest volume. The
        # coverage assertion is the one that catches it.
        declared = notifications.contract()["events"]["severity"]
        self.assertEqual(set(declared), set(notifications.events()))
        for name in notifications.events():
            with self.subTest(event=name):
                self.assertIn(notifications.severity(name),
                              ("INFO", "ATTENTION", "ACTION"))

    def test_the_module_does_not_keep_a_second_list(self):
        # The contract owns the vocabulary. A literal copy in the module is the
        # copy that falls behind, and it would be the one a reader trusts.
        source = (ROOT / "runtime" / "notifications.py").read_text(encoding="utf-8")
        body = source.split('"""', 2)[-1]
        for name in notifications.events():
            with self.subTest(event=name):
                self.assertNotIn(f'"{name}"', body)
                self.assertNotIn(f"'{name}'", body)


class TestTheSubjectIsStable(unittest.TestCase):
    def test_an_event_without_a_device_is_refused(self):
        # §7/#10 name stable device ids. An event that cannot say which machine
        # it is about is an event a person cannot act on, and rendering it would
        # produce a sentence with the location missing.
        for bad in ("", "server-beijing", "work-fj", "localhost", "device:"):
            with self.subTest(device=bad):
                with self.assertRaises(notifications.NotificationError):
                    event(device_ref=bad)

    def test_the_device_identity_is_the_transports_identity(self):
        # Borrowed, not restated. A second pattern here would accept ids the
        # fleet cannot resolve, and the notification path is the one nobody
        # re-tests when the id form changes.
        transport = _load("serveros_fleet_transport", ROOT / "fleet" / "transport.py")
        for bad in ("device:", "device:Server_Beijing", "device:work fj", "device:-x"):
            with self.subTest(device=bad):
                with self.assertRaises(Exception):
                    transport.DeviceRef.parse(bad)
                with self.assertRaises(notifications.NotificationError):
                    event(device_ref=bad)
        for good in ("device:work-fj", "device:personal-dell", "device:server-beijing"):
            with self.subTest(device=good):
                self.assertEqual(transport.DeviceRef.parse(good).ref, good)

    def test_the_rendered_sentence_names_the_subject_and_the_device(self):
        line = notifications.render(event())
        self.assertIn("session:srv:1", line)
        self.assertIn(DEVICE, line)

    def test_a_reason_reaches_the_reader(self):
        self.assertIn("TIMEOUT", notifications.render(event(reason="TIMEOUT")))

    def test_the_reason_placeholder_is_not_left_hanging(self):
        # A delivery with no reason still has to read as a sentence. The
        # alternative — an empty slot or a bare `None` — is a message a person
        # has to interpret.
        for name in notifications.events():
            with self.subTest(event=name):
                line = notifications.render(event(name, reason=None))
                self.assertNotIn("None", line)
                self.assertNotIn("{", line)


class TestServerOSAuthorsTheSentence(unittest.TestCase):
    def test_the_provider_is_handed_serveros_text(self):
        provider = RecordingProvider()
        line = "Turn failed on session:srv:1 (device:server-beijing): TIMEOUT."
        answer = notifications.deliver(provider, event(reason="TIMEOUT"),
                                       conversation_ref="oc_1")
        self.assertTrue(answer.delivered)
        self.assertEqual(provider.calls[0]["text"], line)

    def test_the_renderer_is_here_and_not_in_the_provider(self):
        # §10: BotMux does not decide ServerOS semantic truth. The provider is
        # handed a finished sentence; it is never handed the fields and asked to
        # compose one, which would put the claim inside the adapter.
        provider = RecordingProvider()
        notifications.deliver(provider, event(reason="TIMEOUT"), conversation_ref="oc_1")
        self.assertEqual(set(provider.calls[0]), {"conversation_ref", "text",
                                                  "idempotency_key"})

    def test_a_provider_reason_is_not_dressed_up_as_an_event_name(self):
        # The reason word is carried as a reason, inside the sentence. Promoting
        # a provider's word to the event name would make the provider's
        # vocabulary the ServerOS one by accident — and it is the natural bug,
        # because the two vocabularies overlap in spelling for TURN_FAILED.
        line = notifications.render(event(reason="PROVIDER_ERROR"))
        self.assertIn("PROVIDER_ERROR", line)
        with self.assertRaises(notifications.NotificationError):
            event("PROVIDER_ERROR")
        with self.assertRaises(notifications.NotificationError):
            event("TURN_FAILED")


class TestACapabilityGapIsNotAFailure(unittest.TestCase):
    def test_an_unsupported_transport_is_reported_not_raised(self):
        provider = RecordingProvider("UNSUPPORTED", reason="no callback surface")
        answer = notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertFalse(answer.delivered)
        self.assertEqual(answer.state, "UNSUPPORTED")
        self.assertEqual(answer.reason, "no callback surface")
        self.assertEqual(provider.calls, [], "an unsupported transport was asked anyway")

    def test_an_unsupported_transport_is_never_asked(self):
        provider = RecordingProvider("UNSUPPORTED")
        notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertEqual(provider.calls, [])

    def test_the_event_still_exists_when_nobody_was_told(self):
        # "Not delivered" is not "did not happen". The delivery reports false and
        # still carries what would have been said, so an operator reading the
        # result knows which sentence went nowhere.
        provider = RecordingProvider("UNSUPPORTED")
        answer = notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertIn("session:srv:1", answer.show()["detail"]["rendered"])

    def test_an_unanswerable_probe_is_unknown_not_unsupported(self):
        # UNKNOWN and UNSUPPORTED need different repairs — one is a test, the
        # other is a transport. Collapsing them would report an untested
        # transport as a permanent limitation.
        provider = RecordingProvider(raises=True)
        answer = notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertFalse(answer.delivered)
        self.assertEqual(answer.state, "UNKNOWN")
        self.assertIn("RuntimeError", answer.reason)

    def test_an_unanswerable_probe_still_does_not_deliver(self):
        provider = RecordingProvider(raises=True)
        notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertEqual(provider.calls, [])

    def test_a_capability_missing_from_a_published_table_is_unsupported(self):
        # The provider answered; the answer does not include the transport. That
        # is a "no" for the hole, not an unknown.
        provider = RecordingProvider(omit=True)
        answer = notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertEqual(answer.state, "UNSUPPORTED")
        self.assertIn("capability table", answer.reason)

    def test_an_unreadable_capability_entry_is_unknown(self):
        provider = RecordingProvider()
        provider.capabilities = lambda: {"capabilities": {
            "human.notification.transport": 7}}
        answer = notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertEqual(answer.state, "UNKNOWN")

    def test_the_state_can_only_be_one_of_the_declared_three(self):
        self.assertEqual(notifications.support_states(),
                         ("SUPPORTED", "UNSUPPORTED", "UNKNOWN"))


class TestDeliveryIsHonest(unittest.TestCase):
    def test_a_delivered_event_says_so_with_the_providers_answer(self):
        provider = RecordingProvider()
        answer = notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertTrue(answer.delivered)
        self.assertEqual(answer.detail["provider_answer"]["message_ref"], "om_1")

    def test_a_provider_that_fails_is_called_once_and_reported(self):
        # No internal retry. A loop here would be a second delivery path with its
        # own opinion about how often a person should be told the same thing.
        provider = RecordingProvider(notify_raises=RuntimeError("502 from transport"))
        answer = notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertFalse(answer.delivered)
        self.assertEqual(len(provider.calls), 1)
        self.assertIn("502 from transport", answer.reason)
        self.assertEqual(answer.state, "SUPPORTED", "the transport supported it; the call failed")

    def test_a_successful_call_is_never_reported_as_a_failure(self):
        provider = RecordingProvider()
        self.assertTrue(notifications.deliver(provider, event(),
                                              conversation_ref="oc_1").delivered)

    def test_delivery_to_nobody_is_refused_before_the_call(self):
        provider = RecordingProvider()
        answer = notifications.deliver(provider, event(), conversation_ref="")
        self.assertFalse(answer.delivered)
        self.assertEqual(provider.calls, [])

    def test_the_result_envelope_names_the_capability_it_checked(self):
        answer = notifications.deliver(RecordingProvider(), event(),
                                       conversation_ref="oc_1").show()
        self.assertEqual(answer["capability"], "human.notification.transport")
        self.assertEqual(answer["event"], "turn.failed")
        self.assertEqual(answer["provider"], "provider:test-human-control")

    def test_two_deliveries_are_two_deliveries(self):
        # The module does not dedupe, and must not pretend to: idempotency is
        # the transport's job via the key. A memory of what was sent would be a
        # second, quieter record of what the transport accepted.
        provider = RecordingProvider()
        notifications.deliver(provider, event(), conversation_ref="oc_1")
        notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertEqual(len(provider.calls), 2)
        self.assertEqual(provider.calls[0]["idempotency_key"],
                         provider.calls[1]["idempotency_key"])


class TestIdempotencyKeyIsDerived(unittest.TestCase):
    def test_the_same_event_yields_the_same_key(self):
        self.assertEqual(event().idempotency_key(), event().idempotency_key())

    def test_a_different_occurrence_yields_a_different_key(self):
        # Two turns that failed identically on the same session are two events.
        # A key that did not carry the correlation id would make the second one
        # a silent no-op at the transport.
        first = event(correlation_id="corr-1").idempotency_key()
        second = event(correlation_id="corr-2").idempotency_key()
        self.assertNotEqual(first, second)

    def test_a_different_subject_yields_a_different_key(self):
        self.assertNotEqual(event(session_ref="session:a").idempotency_key(),
                            event(session_ref="session:b").idempotency_key())

    def test_a_different_event_on_one_subject_yields_a_different_key(self):
        self.assertNotEqual(event("turn.failed").idempotency_key(),
                            event("turn.completed").idempotency_key())

    def test_the_key_is_the_one_the_provider_receives(self):
        provider = RecordingProvider()
        delivered = event()
        notifications.deliver(provider, delivered, conversation_ref="oc_1")
        self.assertEqual(provider.calls[0]["idempotency_key"],
                         delivered.idempotency_key())


class TestReasonsMapOntoEvents(unittest.TestCase):
    def test_the_mapping_speaks_the_router_vocabulary(self):
        # A mapping keyed by a reason word nobody can return is a dead branch
        # that reads as coverage. The vocabulary owner is `abilities/target.py`.
        vocabulary = set(target.reason_vocabulary())
        mapping = notifications.contract()["from_reason"]
        for reason in mapping:
            with self.subTest(reason=reason):
                self.assertIn(reason, vocabulary)

    def test_every_mapped_value_is_a_declared_event(self):
        for reason, names in notifications.contract()["from_reason"].items():
            for name in names:
                with self.subTest(reason=reason, event=name):
                    self.assertIn(name, notifications.events())

    def test_an_unmapped_refusal_produces_nothing(self):
        # Not a generic "something went wrong". A refusal nobody wrote a
        # notification for is a refusal nobody is told about, and that is
        # visible here rather than disguised as coverage.
        self.assertEqual(notifications.events_for("POLICY_DENIED"), ())
        self.assertEqual(notifications.events_for("never-a-reason"), ())

    def test_a_mapped_refusal_produces_its_events(self):
        self.assertIn("device.unavailable",
                      notifications.events_for("NODE_RUNTIME_UNAVAILABLE"))

    def test_a_policy_decision_is_not_reported_as_a_device_failure(self):
        # POLICY_DENIED is a decision the caller is already holding. Reporting it
        # out-of-band as a device problem would be a second answer to a question
        # that already had one, and a wrong one.
        self.assertEqual(notifications.events_for("POLICY_DENIED"), ())
        self.assertEqual(notifications.events_for("APPROVAL_REQUIRED"), ())

    def test_every_event_is_reachable_from_some_reason(self):
        # An event no reason can raise is a sentence nobody will ever read, and
        # it would sit in the vocabulary looking supported.
        reachable = {name for names in notifications.contract()["from_reason"].values()
                     for name in names}
        producers = set(notifications.contract()["events"]["producers"])
        self.assertEqual(reachable | producers, set(notifications.events()))
        self.assertEqual(reachable & producers, set())


class TestTheProviderContractIsTheOneQueried(unittest.TestCase):
    def test_the_capability_name_matches_the_human_control_provider(self):
        # The name is the join between two files that are edited independently.
        # Renaming it on one side would make every notification UNSUPPORTED with
        # a reason that reads as a transport limitation.
        source = (ROOT / "mcp" / "human_control.py").read_text(encoding="utf-8")
        self.assertIn(f'"{notifications.CAPABILITY}"', source)

    def test_the_provider_protocol_exposes_what_is_called(self):
        source = (ROOT / "mcp" / "human_control.py").read_text(encoding="utf-8")
        self.assertIn("def notify(self, *, conversation_ref: str, text: str",
                      source)

    def test_the_real_provider_capability_shape_is_understood(self):
        # A table in the shape `mcp/human_control.py` publishes, fed through the
        # reader that will consume it.
        provider = RecordingProvider()
        real = {"provider": "provider:botmux-human-control", "transport": "feishu",
                "capabilities": {"human.notification.transport": support(
                    "SUPPORTED", "POST /api/trigger carries a ServerOS sentence "
                                 "into the conversation")},
                "measured_by": "mcp/human_control.py"}
        provider.capabilities = lambda: real
        state, reason = notifications.capability_of(provider)
        self.assertEqual(state, "SUPPORTED")
        self.assertIn("/api/trigger", reason)


class TestNothingSensitiveLeaves(unittest.TestCase):
    def test_a_detail_sentence_cannot_smuggle_a_credential_into_a_chat(self):
        # The notification leaves the machine and lands in a conversation. This
        # asserts the boundary explicitly: what the producer writes in `detail`
        # is a person-facing sentence, and the renderer adds no payload of its
        # own — there is no provider response body in the sentence.
        provider = RecordingProvider()
        notifications.deliver(provider, event(reason="TIMEOUT"), conversation_ref="oc_1")
        sent = provider.calls[0]["text"]
        for forbidden in ("Bearer", "token", "cookie", "password", "-----BEGIN"):
            with self.subTest(needle=forbidden):
                self.assertNotIn(forbidden, sent)

    def test_the_provider_answer_does_not_reach_the_sentence(self):
        provider = RecordingProvider()
        answer = notifications.deliver(provider, event(), conversation_ref="oc_1")
        self.assertNotIn("om_1", answer.detail["rendered"])


class TestTheCli(unittest.TestCase):
    def _run(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "runtime" / "notifications.py"), *args],
            capture_output=True, text=True, cwd=str(ROOT), timeout=60,
            env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})

    def test_it_renders_an_event(self):
        done = self._run("--event", "turn.failed", "--device", DEVICE,
                         "--reason", "TIMEOUT")
        self.assertEqual(done.returncode, 0, done.stderr)
        import json
        out = json.loads(done.stdout)
        self.assertIn("TIMEOUT", out["rendered"])
        self.assertEqual(out["severity"], "ATTENTION")

    def test_it_refuses_to_invent_an_event(self):
        done = self._run("--device", DEVICE)
        self.assertNotEqual(done.returncode, 0)

    def test_it_refuses_an_unknown_event(self):
        done = self._run("--event", "made.up", "--device", DEVICE)
        self.assertNotEqual(done.returncode, 0)


if __name__ == "__main__":
    unittest.main()
