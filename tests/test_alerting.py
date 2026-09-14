#!/usr/bin/env python3
"""§21 — an anomaly with a memory, and the sentences this has to keep false.

WHAT EACH SECTION KEEPS FALSE
-----------------------------
    producer      "somebody was told" (the module had zero production callers)
    identity      "it happened again" (it is the same alert, twice)
    cooldown      "you already knew" (the cooldown swallowed the first message)
    hysteresis    "it is fixed" (the value wobbled across one threshold)
    recovery      "no news is good news" (nobody said it ended)
    ack/snooze    "somebody dealt with it" (a button, not a repair)
    delivery      "you were told" (the transport was down)
    UNKNOWN       "the node is fine" (nothing was evaluated)
    §15           "the system handled it" (nothing was touched)

and the last section mutates the module five ways to prove the tests above are
watching the behaviour rather than the shape of the code. A test that cannot go
red for the failure it names is a comment with a green tick next to it.

WHY THE NUMBERS ARE LITERALS HERE
---------------------------------
The thresholds, margins and cooldowns below are written out rather than read
from the contracts they come from. A fixture that reads the policy it is testing
asserts only that the policy equals itself — move a margin to zero and every
hysteresis test here still passes. So the numbers are literals, and
`TheContractOwnsTheNumbers` is the join that fails if they ever stop being the
declared ones. Two records of one fact, and a test between them.
"""
from __future__ import annotations

import datetime
import importlib.util
import itertools
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

DEVICE = "device:server-beijing"
FS = "filesystem:/"
FS_DATA = "filesystem:/data"
GROUP = "runtime-group:codex-session:session-757"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Loaded under the names `alerting.py` itself looks them up with, so that the
# mutants below share these objects rather than loading second copies. The
# synthetic-name idiom is the tree's (zero cross-directory imports), and its
# hazard is the one this comment exists for: one file under two names is two
# classes, and `isinstance` then answers False about an object of its own type.
alerting = _load("serveros_runtime_alerting", ROOT / "runtime" / "alerting.py")
notifications = _load("serveros_runtime_notifications",
                      ROOT / "runtime" / "notifications.py")
anomaly = _load("serveros_runtime_anomaly", ROOT / "runtime" / "anomaly.py")
graph = _load("serveros_runtime_graph", ROOT / "runtime" / "graph.py")
store_io = _load("serveros_runtime_agent_control_store",
                 ROOT / "runtime" / "agent_control_store.py")

# -- the declared numbers, as literals --------------------------------------
FS_HIGH = 85.0
FS_MARGIN = 3.0
FS_FLOOR = 82.0            # FS_HIGH - FS_MARGIN, and the number in the sentence
CPU_HIGH = 100.0
CPU_MARGIN = 10.0
RSS_HIGH = 1073741824.0
RSS_MARGIN = 268435456.0
COOLDOWN_HIGH = 900
COOLDOWN_MEDIUM = 3600
COOLDOWN_LOW = 21600

# The two detectors, by the names a row stores — which is the label, not the
# module path. Written here as literals for the same reason the numbers above
# are: a fixture that read them back from the contract would be asserting the
# contract equals itself, and `TheContractOwnsTheNumbers` is the join that goes
# red if either side moves. Getting this wrong is not cosmetic — an alert row
# remembers the name of the detector that opened it and ONLY that detector may
# close it, so a fixture using a vocabulary of its own would exercise a state
# machine production never runs, and `TwoDetectorsOneStore` below would be
# testing this file's opinion instead of the rule.
DECLARATION = "declaration-anomaly"
MEASUREMENT = "measurement-anomaly"


def at(seconds: float = 0.0) -> str:
    base = datetime.datetime(2026, 9, 14, 2, 0, 0, tzinfo=datetime.timezone.utc)
    return (base + datetime.timedelta(seconds=seconds)).isoformat().replace(
        "+00:00", "Z")


T0 = at(0)


def level(code: str, subject: str, value, threshold, margin_key: str) -> dict:
    return {f"{code}|{subject}": {"code": code, "subject": subject,
                                  "value": value, "threshold": threshold,
                                  "margin_key": margin_key}}


def detection(*, signals=(), levels=None, ungraded=(),
              answered_by=MEASUREMENT, measured_at=T0) -> dict:
    return {"signals": list(signals), "levels": levels or {},
            "codes_not_evaluated": list(ungraded),
            "answered_by": answered_by, "measured_at": measured_at}


def disk_signal(*, used=93.0, severity="high", subject=FS) -> dict:
    return {"code": "DATA_FS_PRESSURE", "subject": subject, "severity": severity,
            "measured": {"used_percent": used, "threshold_percent": FS_HIGH},
            "why": f"the data filesystem is at {used}%"}


def disk_level(used, *, subject=FS) -> dict:
    return level("DATA_FS_PRESSURE", subject, used, FS_HIGH,
                 "filesystem_percent_recovery_margin")


def disk_state(used, *, severity="high", subject=FS, reported=True):
    """(signals, levels) for one filesystem, from ONE number.

    Production reaches an alert only through `resource_anomaly.detect()`, which
    emits a `levels` row for every code it grades — so a graded signal always
    arrives with the row its recovery margin is read from. A fixture that passed
    the signal without the level opened a DECLARATION-shaped alert about a disk,
    which then closed on the very next tick that stopped reporting it: a state
    production cannot reach, tested for, and the test would have been wrong
    rather than the code. Pairing the two halves in one helper is what makes
    that drift impossible to write by accident.
    """
    signals = ([] if severity is None else
               [disk_signal(used=used, severity=severity, subject=subject)])
    levels = disk_level(used, subject=subject) if reported else {}
    return signals, levels


def cpu_signal(*, cpu=279.5, processes=5, top=None, severity="high",
               subject=GROUP) -> dict:
    """A runtime group over its CPU threshold. §5's case, in miniature."""
    measured = {"cpu_percent": cpu, "threshold_percent": CPU_HIGH,
                "process_count": processes,
                "window": "lifetime average over 30938s"}
    if top is not None:
        measured["top_contributor"] = top
    return {"code": "HIGH_CPU_SUSTAINED", "subject": subject,
            "severity": severity, "measured": measured,
            "window": measured["window"],
            "why": f"{cpu}% of one core across {processes} process(es)"}


def cpu_level(cpu, *, subject=GROUP) -> dict:
    return level("HIGH_CPU_SUSTAINED", subject, cpu, CPU_HIGH,
                 "cpu_percent_recovery_margin")


class RecordingProvider:
    """A HumanControlProvider that records what it was asked to say."""

    def __init__(self, state="SUPPORTED", *, reason=None, notify_raises=None,
                 provider_id="provider:test-human-control"):
        self.provider_id = provider_id
        self._state = state
        self._reason = reason
        self._notify_raises = notify_raises
        self.calls: list[dict] = []

    def capabilities(self) -> dict:
        table = {"human.notification.transport":
                 self._state if self._reason is None
                 else {"state": self._state, "reason": self._reason}}
        return {"provider": self.provider_id, "transport": "test",
                "capabilities": table, "measured_by": "fixture"}

    def notify(self, *, conversation_ref: str, text: str,
               idempotency_key: str) -> dict:
        self.calls.append({"conversation_ref": conversation_ref, "text": text,
                           "idempotency_key": idempotency_key})
        if self._notify_raises:
            raise self._notify_raises
        return {"accepted": True, "message_ref": f"om_{len(self.calls)}"}


# ---------------------------------------------------------------------------
# The fixture every test below builds on
# ---------------------------------------------------------------------------


class AlertCase(unittest.TestCase):
    """One store on disk, and the two calls every pass makes."""

    def setUp(self):
        self.store = alerting.AlertStore(self.state_path("alerts.json"))

    def state_path(self, name: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return Path(directory.name) / "state" / name

    def run_pass(self, *, seconds=0.0, detector=None, **kwargs) -> dict:
        """One evaluation pass at `T0 + seconds`.

        `thresholds` is deliberately NOT passed. `evaluate` treats it as a
        process-wide override (`_THRESHOLD_CACHE.clear()`), so a fixture that
        supplied three keys would leave the module holding three, and the tests
        that read the declared policy — which run later in the alphabet — would
        be reading this file's literals back. The real contract is what these
        fixtures close against anyway.

        `detector` defaults to the MEASUREMENT detector rather than to a
        literal, and is resolved here instead of in the signature: one caller
        passes `""` on purpose, to make `evaluate` refuse a document nobody can
        own, and a default written as `detector or MEASUREMENT` would quietly
        turn that empty name into a real one.
        """
        if detector is None:
            detector = MEASUREMENT
        return alerting.evaluate(
            self.store, detection=detection(measured_at=at(seconds), **kwargs),
            device_ref=DEVICE, at=at(seconds), detector=detector)

    def disk(self, *, seconds=0.0, used=93.0, severity="high", subject=FS,
             reported=True, **kwargs) -> dict:
        """A filesystem tick: the reading and the signal it produced."""
        signals, levels = disk_state(used, severity=severity, subject=subject,
                                     reported=reported)
        return self.run_pass(seconds=seconds, signals=signals, levels=levels,
                             **kwargs)

    def rows(self) -> dict:
        return self.store.alerts()

    def only(self) -> dict:
        rows = self.rows()
        self.assertEqual(len(rows), 1, rows)
        return next(iter(rows.values()))

    def deliver(self, *, kind="opened", seconds=0.0, delivered=True,
                state="DELIVERED", reason=None) -> dict:
        return alerting.record_delivery(
            self.store, alert_ref=self.only()["alert_ref"], kind=kind,
            at=at(seconds), delivered=delivered, state=state, reason=reason)

    def speak(self, *, provider=None, kind="opened", seconds=0.0,
              conversation_ref="conversation:ops"):
        provider = provider or RecordingProvider()
        answer = alerting.notify_for(
            provider, alert=self.only(), kind=kind, device_ref=DEVICE,
            at=at(seconds), conversation_ref=conversation_ref)
        return provider, answer


# ---------------------------------------------------------------------------
# The producer. §2/§13/§19
# ---------------------------------------------------------------------------


class TheProducerRaisesTheTwoDeclaredEvents(AlertCase):
    def test_an_opened_alert_becomes_alert_opened(self):
        self.disk(used=93.0)
        provider, answer = self.speak()
        self.assertTrue(answer.delivered, answer.show())
        self.assertEqual(answer.event, "alert.opened")
        self.assertEqual(len(provider.calls), 1)
        text = provider.calls[0]["text"]
        self.assertIn("DATA_FS_PRESSURE", text)
        self.assertIn("filesystem:/", text)
        self.assertIn("93.0%", text)
        self.assertIn("85.0", text)

    def test_a_resolved_alert_becomes_alert_resolved(self):
        self.disk(used=93.0)
        self.disk(seconds=60, used=50.0, severity=None)
        self.assertEqual(self.only()["state"], "RESOLVED")
        provider, answer = self.speak(kind="resolved", seconds=60)
        self.assertEqual(answer.event, "alert.resolved")
        self.assertIn("RECOVERED", provider.calls[0]["text"])

    def test_the_producer_refuses_a_kind_it_has_no_template_for(self):
        self.disk()
        with self.assertRaises(alerting.AlertError):
            self.speak(kind="reminder")

    def test_a_repeat_a_cooldown_later_is_not_the_same_message(self):
        """The transport de-duplicates on the idempotency key, so a repeat one
        cooldown later must not carry the same one — the second message would be
        swallowed as a duplicate of the first, silently."""
        self.disk()
        provider = RecordingProvider()
        self.speak(provider=provider, seconds=0)
        self.speak(provider=provider, seconds=COOLDOWN_HIGH + 1)
        keys = [call["idempotency_key"] for call in provider.calls]
        self.assertEqual(len(keys), 2, provider.calls)
        self.assertNotEqual(keys[0], keys[1])

    def test_the_sentence_carries_no_action_claim(self):
        """§15 as text, in the same breath as the recommendation rather than in
        a field a renderer could drop."""
        self.disk()
        rendered = alerting.detail_for(self.only(), at=T0)
        self.assertIn("recommendation: REVIEW", rendered)
        self.assertIn("nothing was stopped, restarted or removed", rendered)


# ---------------------------------------------------------------------------
# Identity. §7/§19
# ---------------------------------------------------------------------------


class OneAlertPerIdentity(AlertCase):
    def test_the_same_anomaly_twice_is_one_alert_seen_twice(self):
        self.disk()
        first = self.only()
        self.disk(seconds=60)
        second = self.only()
        self.assertEqual(first["alert_ref"], second["alert_ref"])
        self.assertEqual(second["occurrence_count"], 2)
        self.assertEqual(second["first_seen"], T0)
        self.assertEqual(second["last_seen"], at(60))

    def test_a_relaunched_runtime_is_the_same_alert(self):
        """§7 named this one. The supervisor kills the process and starts a new
        one with a new PID; the alert is about the RUNTIME, so the message that
        would otherwise fire once per restart does not fire at all."""
        first = {"pid": 65716, "comm": "app-server", "cpu_percent": 279.5,
                 "rss_bytes": 302000000}
        second = {"pid": 79288, "comm": "app-server", "cpu_percent": 281.0,
                  "rss_bytes": 304000000}
        self.run_pass(signals=[cpu_signal(top=first)], levels=cpu_level(279.5))
        ref_a = self.only()["alert_ref"]
        self.run_pass(seconds=60, signals=[cpu_signal(top=second)],
                      levels=cpu_level(281.0))
        row = self.only()
        self.assertEqual(row["alert_ref"], ref_a)
        self.assertEqual(row["occurrence_count"], 2)
        self.assertEqual(row["measured"]["top_contributor"]["pid"], 79288)

    def test_two_filesystems_on_one_device_are_two_alerts(self):
        first = disk_state(93.0, subject=FS)
        second = disk_state(94.0, subject=FS_DATA)
        self.run_pass(signals=first[0] + second[0],
                      levels={**first[1], **second[1]})
        self.assertEqual(len(self.rows()), 2)

    def test_a_ref_is_derived_from_the_identity_and_not_assigned(self):
        """A counter would be renumbered by a restart of the process that writes
        this store, and `alert 3` would mean two different things on two sides
        of a restart."""
        expected = alerting.alert_ref(
            alerting.identity(code="DATA_FS_PRESSURE", device_ref=DEVICE,
                              subject_ref=FS))
        self.disk()
        self.assertEqual(self.only()["alert_ref"], expected)

    def test_a_pid_is_not_part_of_the_identity(self):
        declared = alerting.policy()["identity_is_not"]
        self.assertIn("pid", declared)
        self.assertIn("container_name", declared)
        self.assertIn("timestamp", declared)
        self.assertNotIn("pid", alerting.identity(
            code="HIGH_CPU_SUSTAINED", device_ref=DEVICE, subject_ref=GROUP))


# ---------------------------------------------------------------------------
# Cooldown. §8/§12/§19
# ---------------------------------------------------------------------------


class TheCooldownIsTheAttemptNotTheDelivery(AlertCase):
    def test_the_first_message_is_never_swallowed(self):
        self.disk()
        self.assertEqual([item["kind"] for item in
                          alerting.pending_for(self.store, at=T0)], ["opened"])

    def test_a_persisting_alert_does_not_repeat_within_the_cooldown(self):
        self.disk()
        self.deliver()
        result = self.disk(seconds=60)
        self.assertEqual(alerting.pending_for(self.store, at=at(60)), [])
        self.assertTrue(any("in cooldown" in entry["why"]
                            for entry in result["held"]), result["held"])

    def test_it_speaks_again_once_the_cooldown_has_passed(self):
        self.disk()
        self.deliver()
        self.disk(seconds=COOLDOWN_HIGH + 1)
        self.assertEqual([item["kind"] for item in
                          alerting.pending_for(self.store,
                                               at=at(COOLDOWN_HIGH + 1))],
                         ["opened"])

    def test_the_cooldown_runs_from_the_attempt_so_an_outage_does_not_silence_it(self):
        """§12's failure mode, and the bug this replaced. With the clock on
        `last_notified_at`, a transport that is down never sets it, the cooldown
        never elapses, and the alert goes quiet for exactly as long as the
        transport is broken — the one period in which somebody needs to hear
        about it."""
        self.disk()
        self.deliver(delivered=False, state="FAILED", reason="the bot is down")
        row = self.only()
        self.assertIsNone(row["last_notified_at"])
        self.assertEqual(row["delivery_failure"], "the bot is down")
        # Still held now, and due again one cooldown after the ATTEMPT — which
        # `record_delivery` has already moved forward, so this is not a loop.
        self.assertEqual(alerting.pending_for(self.store, at=at(300)), [])
        self.assertEqual(
            [item["kind"] for item in
             alerting.pending_for(self.store, at=at(COOLDOWN_HIGH + 1))],
            ["opened"])

    def test_a_severity_downgrade_is_not_news_but_the_recovery_still_is(self):
        self.disk(used=93.0, severity="high")
        self.deliver()
        # The cooldown consulted is the one for the CURRENT severity, so a
        # downgrade has to clear the longer of the two before it is considered
        # at all.
        result = self.disk(seconds=COOLDOWN_LOW, used=90.0, severity="low")
        self.assertTrue(any("downgrade is not news" in entry["why"]
                            for entry in result["held"]), result["held"])
        self.disk(seconds=COOLDOWN_LOW + 1, used=50.0, severity=None)
        self.assertEqual(
            [item["kind"] for item in
             alerting.pending_for(self.store, at=at(COOLDOWN_LOW + 1))],
            ["resolved"])


# ---------------------------------------------------------------------------
# Hysteresis. §9/§19
# ---------------------------------------------------------------------------


class RecoveryIsMeasuredNotAssumed(AlertCase):
    def test_it_does_not_oscillate_across_one_threshold(self):
        """85.1 → 84.9 → 85.5 → 84.8 is ONE condition, and the discriminating
        assertion is `first_seen`. A store that read the absence of the signal
        at 84.9 as a recovery would close the alert and reopen it at 85.5 as a
        new EPISODE — a fresh `first_seen` about a disk that never stopped being
        full — and a person would be told about a new problem twice in three
        minutes."""
        self.disk(used=85.1)
        self.assertEqual(self.only()["first_seen"], T0)
        for seconds, used in ((60, 84.9), (120, 85.5), (180, 84.8)):
            result = self.disk(seconds=seconds, used=used)
            row = self.only()
            self.assertEqual(row["state"], "OPEN", f"at {used}%")
            self.assertEqual(row["first_seen"], T0, f"at {used}%")
            self.assertIsNone(row["resolved_at"], f"at {used}%")
            # Whether the tick spoke or held, every entry names a state that is
            # still open.
            for entry in result["held"]:
                self.assertEqual(entry["state"], "OPEN")

    def test_it_closes_only_below_the_threshold_minus_the_margin(self):
        self.disk(used=93.0)
        # 82.0 is the floor. 82.9 is not a recovery.
        held = self.disk(seconds=60, used=82.9, severity=None)
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertTrue(any(f"above the {FS_FLOOR}" in entry["why"]
                            for entry in held["held"]), held["held"])
        self.disk(seconds=120, used=FS_FLOOR, severity=None)
        self.assertEqual(self.only()["state"], "RESOLVED")
        self.assertEqual(self.only()["resolved_at"], at(120))

    def test_a_subject_that_stopped_being_measured_has_not_recovered(self):
        """Absence of a measurement is not a measurement of zero. This is the
        same three-valued reading `codes_not_evaluated` makes one level down, and
        getting it wrong here closes an alert about a disk because the disk
        stopped answering."""
        self.disk(used=93.0)
        held = self.disk(seconds=60, severity=None, reported=False)
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertTrue(any("not reported this tick" in entry["why"]
                            for entry in held["held"]), held["held"])

    def test_a_declaration_closes_when_it_stops_being_reported(self):
        """No margin key means not a measurement — the code says a thing is true,
        and there is no distance for it to come back. This is the one shape where
        silence IS the fix."""
        self.run_pass(signals=[{"code": "RUNTIME_UNOWNED",
                                "subject": "runtime:container:abc",
                                "severity": "medium", "measured": {},
                                "why": "no owner is declared"}])
        self.assertIsNone(self.only()["margin_key"])
        self.run_pass(seconds=60)
        self.assertEqual(self.only()["state"], "RESOLVED")

    def test_a_recurrence_after_recovery_speaks_immediately(self):
        """Not a repeat: a person who was told it recovered needs to be told it
        did not stay recovered."""
        self.disk(used=93.0)
        self.disk(seconds=60, used=50.0, severity=None)
        self.assertEqual(self.only()["state"], "RESOLVED")
        self.disk(seconds=120, used=93.0)
        row = self.only()
        self.assertEqual(row["state"], "OPEN")
        self.assertEqual(row["occurrence_count"], 1)
        self.assertEqual(row["first_seen"], at(120))
        self.assertEqual([item["kind"] for item in
                          alerting.pending_for(self.store, at=at(120))],
                         ["opened"])


# ---------------------------------------------------------------------------
# §10 recovery, and the one message it sends
# ---------------------------------------------------------------------------


class RecoverySpeaksExactlyOnce(AlertCase):
    def test_a_recovery_sends_one_message_and_then_stops(self):
        self.disk(used=93.0)
        self.disk(seconds=60, used=50.0, severity=None)
        self.assertEqual(
            [item["kind"] for item in
             alerting.pending_for(self.store, at=at(60))], ["resolved"])
        self.deliver(kind="resolved", seconds=60)
        self.disk(seconds=120, used=50.0, severity=None)
        self.assertEqual(alerting.pending_for(self.store, at=at(120)), [])
        self.assertEqual(self.only()["state"], "RESOLVED")

    def test_a_recovery_from_an_acknowledged_alert_still_speaks(self):
        """§10 does not say "unless somebody pressed a button". An ACK records
        attention; it does not make the ending uninteresting."""
        self.disk(used=93.0)
        alerting.acknowledge(self.store, self.only()["alert_ref"], at=at(60))
        self.disk(seconds=60, used=50.0, severity=None)
        self.assertEqual(self.only()["state"], "RESOLVED")
        self.assertEqual(
            [item["kind"] for item in
             alerting.pending_for(self.store, at=at(60))], ["resolved"])


# ---------------------------------------------------------------------------
# §11 what a person may do
# ---------------------------------------------------------------------------


class AcknowledgedIsNotResolved(AlertCase):
    def test_acknowledging_does_not_close_anything(self):
        self.disk(used=93.0)
        row = alerting.acknowledge(self.store, self.only()["alert_ref"],
                                   at=at(60), note="looking at it")
        self.assertEqual(row["state"], "ACKNOWLEDGED")
        self.assertIsNone(row["resolved_at"])
        self.assertEqual(row["acknowledgement_note"], "looking at it")

    def test_an_acknowledged_alert_keeps_updating_and_is_not_reopened(self):
        """§11's whole content. ACK suppresses nothing but the repetition, and it
        is not a state the machine promotes to RESOLVED on its own."""
        self.disk(used=93.0)
        alerting.acknowledge(self.store, self.only()["alert_ref"], at=at(60))
        self.disk(seconds=60, used=95.0)
        row = self.only()
        self.assertEqual(row["state"], "ACKNOWLEDGED")
        self.assertEqual(row["occurrence_count"], 2)
        self.assertEqual(row["measured"]["used_percent"], 95.0)

    def test_an_acknowledged_alert_is_still_closed_by_the_world(self):
        self.disk(used=93.0)
        alerting.acknowledge(self.store, self.only()["alert_ref"], at=at(60))
        self.disk(seconds=120, used=50.0, severity=None)
        self.assertEqual(self.only()["state"], "RESOLVED")

    def test_a_resolved_alert_cannot_be_acknowledged(self):
        self.disk(used=93.0)
        self.disk(seconds=60, used=50.0, severity=None)
        with self.assertRaises(alerting.AlertError):
            alerting.acknowledge(self.store, self.only()["alert_ref"],
                                 at=at(120))

    def test_an_unknown_ref_is_refused_by_name(self):
        with self.assertRaises(alerting.AlertError):
            alerting.acknowledge(self.store, "alert:nope", at=T0)


class SnoozedIsNotResolvedEither(AlertCase):
    def test_a_snooze_stops_the_message_and_not_the_observation(self):
        self.disk(used=93.0)
        self.deliver()
        row = alerting.snooze(self.store, self.only()["alert_ref"], at=at(60),
                              seconds=3600)
        self.assertEqual(row["state"], "SNOOZED")
        result = self.disk(seconds=120, used=96.0)
        row = self.only()
        self.assertEqual(row["state"], "SNOOZED")
        self.assertEqual(row["occurrence_count"], 2)
        self.assertEqual(row["measured"]["used_percent"], 96.0)
        self.assertTrue(any("snoozed for another" in entry["why"]
                            for entry in result["held"]), result["held"])
        self.assertEqual(alerting.pending_for(self.store, at=at(120)), [])

    def test_a_snooze_withholds_a_message_that_had_not_gone_out_yet(self):
        """A snooze is a person asking not to be told. An alert whose first
        message has not been sent — because nothing has run `alert sync`, or
        because the last attempt failed — still holds a pending intent, and
        `pending_for()` answers "is this due" without consulting the state. So
        without an explicit withholding, the next surface to run `sync` would
        tell them anyway, for the whole window they asked not to be."""
        self.disk(used=93.0)
        self.assertEqual([item["kind"] for item in
                          alerting.pending_for(self.store, at=at(60))],
                         ["opened"])
        alerting.snooze(self.store, self.only()["alert_ref"], at=at(60),
                        seconds=3600)
        self.assertEqual(alerting.pending_for(self.store, at=at(60)), [])
        row = self.only()
        self.assertIsNone(row["pending"])
        self.assertEqual(row["withheld_kind"], "opened")
        self.assertEqual(row["state"], "SNOOZED")

    def test_an_expired_snooze_speaks_again_if_it_is_still_firing(self):
        self.disk(used=93.0)
        self.deliver()
        alerting.snooze(self.store, self.only()["alert_ref"], at=at(60),
                        seconds=360)
        # Past the expiry AND past the cooldown, which has been running since the
        # delivery the whole time — the expiry alone is not enough.
        later = COOLDOWN_HIGH + 100
        self.disk(seconds=later)
        row = self.only()
        self.assertEqual(row["state"], "OPEN")
        self.assertIsNone(row["snooze_until"])
        self.assertEqual([item["kind"] for item in
                          alerting.pending_for(self.store, at=at(later))],
                         ["opened"])

    def test_a_snooze_with_no_window_is_refused(self):
        """A snooze that never expires is a resolution by another name, and it
        would be indistinguishable from a fix."""
        self.disk()
        with self.assertRaises(alerting.AlertError):
            alerting.snooze(self.store, self.only()["alert_ref"], at=at(60),
                            seconds=0)

    def test_a_snooze_that_still_has_time_left_is_not_cut_short(self):
        self.disk(used=93.0)
        alerting.snooze(self.store, self.only()["alert_ref"], at=at(60),
                        seconds=COOLDOWN_LOW * 4)
        self.disk(seconds=COOLDOWN_LOW * 3)
        self.assertEqual(self.only()["state"], "SNOOZED")
        self.assertEqual(alerting.pending_for(self.store,
                                              at=at(COOLDOWN_LOW * 3)), [])


# ---------------------------------------------------------------------------
# §12 delivery
# ---------------------------------------------------------------------------


class ADeliveryThatDidNotArriveIsNotADelivery(AlertCase):
    def test_an_unsupported_transport_is_reported_and_changes_nothing(self):
        self.disk()
        provider, answer = self.speak(provider=RecordingProvider(
            "UNSUPPORTED", reason="no bot configured"))
        self.assertFalse(answer.delivered)
        self.assertEqual(answer.state, "UNSUPPORTED")
        self.assertEqual(answer.reason, "no bot configured")
        self.assertEqual(provider.calls, [])
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertIsNone(self.only()["last_notified_at"])

    def test_an_unknown_transport_is_not_reported_as_a_limitation(self):
        self.disk()
        _, answer = self.speak(provider=RecordingProvider("UNKNOWN"))
        self.assertFalse(answer.delivered)
        self.assertEqual(answer.state, "UNKNOWN")

    def test_a_provider_that_raises_is_a_failure_and_not_a_limitation(self):
        self.disk()
        _, answer = self.speak(provider=RecordingProvider(
            notify_raises=RuntimeError("gateway 502")))
        self.assertFalse(answer.delivered)
        self.assertIn("502", answer.reason)

    def test_a_notification_to_nobody_is_not_a_notification(self):
        self.disk()
        _, answer = self.speak(conversation_ref="")
        self.assertFalse(answer.delivered)
        self.assertIn("no conversation_ref", answer.reason)

    def test_a_failed_delivery_does_not_mark_it_delivered_or_resolve_it(self):
        self.disk(used=93.0)
        self.deliver(delivered=False, state="FAILED", reason="gateway 502")
        row = self.only()
        self.assertIsNone(row["last_notified_at"])
        self.assertEqual(row["last_delivery"], "FAILED")
        self.assertEqual(row["state"], "OPEN")
        self.assertEqual(row["delivery_failure"], "gateway 502")
        self.assertIsNotNone(row["pending"])
        self.assertEqual(row["pending"]["last_state"], "FAILED")

    def test_the_four_outcomes_are_the_only_four_and_none_resolves(self):
        for state in ("DELIVERED", "FAILED", "UNSUPPORTED", "UNKNOWN"):
            with self.subTest(state=state):
                store = alerting.AlertStore(self.state_path(f"{state}.json"))
                alerting.evaluate(
                    store, detection=detection(signals=[disk_signal()],
                                               levels=disk_level(93.0)),
                    device_ref=DEVICE, at=T0,
                    detector=MEASUREMENT)
                ref = next(iter(store.alerts().values()))["alert_ref"]
                alerting.record_delivery(store, alert_ref=ref, kind="opened",
                                         at=at(60),
                                         delivered=state == "DELIVERED",
                                         state=state)
                row = store.alerts()[ref]
                self.assertEqual(row["last_delivery"], state)
                self.assertEqual(row["state"], "OPEN")
                self.assertEqual(row["last_notified_at"],
                                 at(60) if state == "DELIVERED" else None)


# ---------------------------------------------------------------------------
# UNKNOWN. §3/§19
# ---------------------------------------------------------------------------


class NothingEvaluatedIsNotNothingWrong(AlertCase):
    def test_an_ungraded_code_never_becomes_an_alert(self):
        """`codes_not_evaluated` is the answer, not a footnote. A signal that
        contradicts its own document's "nobody looked at this" loses."""
        result = self.run_pass(
            signals=[{"code": "DOCKER_STORAGE_HIGH", "subject": "docker:store",
                      "severity": "high", "measured": {}}],
            ungraded=[{"code": "DOCKER_STORAGE_HIGH", "input": "containers",
                       "state": "UNKNOWN", "why": "no `docker system df`"}])
        self.assertEqual(self.rows(), {})
        self.assertTrue(any("not evaluated" in entry["why"]
                            for entry in result["held"]), result["held"])

    def test_an_unknown_severity_never_becomes_an_alert(self):
        """`unknown` is a level the detectors use for a code with no severity.
        Rounding it to `low` would report an ungraded finding as a small one."""
        result = self.disk(used=93.0, severity="unknown")
        self.assertEqual(self.rows(), {})
        self.assertTrue(any("UNKNOWN is not a level" in entry["why"]
                            for entry in result["held"]), result["held"])

    def test_an_open_alert_survives_a_tick_that_evaluated_nothing(self):
        self.disk(used=93.0)
        held = self.run_pass(seconds=60, ungraded=[
            {"code": "DATA_FS_PRESSURE", "input": "host_facts",
             "state": "UNKNOWN", "why": "the filesystem read failed"}])
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertTrue(any("was not evaluated this tick" in entry["why"]
                            for entry in held["held"]), held["held"])

    def test_a_document_with_no_detector_name_cannot_own_an_alert(self):
        with self.assertRaises(alerting.AlertError):
            self.run_pass(signals=[disk_signal()], detector="")

    def test_the_held_list_says_why_nothing_was_said(self):
        """Not a log. It is the answer to the question a person asks about a
        monitoring system that stayed quiet, and `held` names a state that is
        still open rather than one that is closed."""
        self.disk(used=93.0)
        self.deliver()
        result = self.disk(seconds=60, used=93.0)
        self.assertTrue(result["held"])
        for entry in result["held"]:
            self.assertEqual(entry["state"], "OPEN")
            self.assertTrue(entry["why"])
            self.assertIn(entry["stage"], ("notification", "resolution"))


class TwoDetectorsOneStore(AlertCase):
    def test_one_detector_does_not_close_another_detectors_alert(self):
        """The resource detector's silence about a code it never grades is not a
        recovery for an alert the runtime detector opened."""
        self.run_pass(signals=[cpu_signal()], levels=cpu_level(279.5),
                      detector=DECLARATION)
        self.assertEqual(self.only()["detector"], DECLARATION)
        self.run_pass(seconds=60, detector=MEASUREMENT)
        self.assertEqual(self.only()["state"], "OPEN")


# ---------------------------------------------------------------------------
# §14 the Codex case, as a fixture
# ---------------------------------------------------------------------------


class TheGroupIsNotTheFinding(unittest.TestCase):
    """§5's acceptance in miniature: five processes totalling 279.5%, and ONE of
    them is the writer. The alert must elevate "79288 is the busiest process"
    rather than repeat the total.

    The numbers are the ones observed on this host on 2026-09-14 — the group's
    lifetime average, its process count, and the top contributor's share. No
    Codex-specific code is exercised: the fixture is a runtime group, which is
    what the detector emits for any of them, and a system that knew what Codex
    was would be the watchdog §5 forbids building.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.store = alerting.AlertStore(Path(directory.name) / "alerts.json")
        alerting.evaluate(
            self.store,
            detection=detection(
                signals=[cpu_signal(top={"pid": 79288, "comm": "app-server",
                                         "cpu_percent": 279.5,
                                         "rss_bytes": 314572800})],
                levels=cpu_level(279.5), measured_at=T0),
            device_ref=DEVICE, at=T0, detector=DECLARATION)
        self.alert = next(iter(self.store.alerts().values()))

    def rendered(self, alert=None) -> str:
        return alerting.detail_for(alert or self.alert, at=T0)

    def test_the_top_contributor_is_named_with_its_pid_cpu_and_memory(self):
        rendered = self.rendered()
        self.assertIn("pid 79288", rendered)
        self.assertIn("279.5% of one core", rendered)
        self.assertIn("300.0 MB rss", rendered)

    def test_the_total_is_carried_with_the_process_count_it_covers(self):
        self.assertIn("279.5% of one core across 5 process(es)", self.rendered())

    def test_the_window_qualifies_the_number_it_sits_under(self):
        """279% over a lifetime average and 279% over five seconds are different
        claims, and §5's case is the one where a reader has to tell them apart."""
        self.assertIn("window: lifetime average over 30938s", self.rendered())

    def test_the_ranking_is_labelled_because_it_ranks_by_cpu(self):
        """`top contributor` over a memory figure reads as "this process holds
        the memory", which is not what was measured. The label says which
        question the ranking answered."""
        self.assertIn("top contributor (highest cpu):", self.rendered())

    def test_a_group_with_no_process_table_says_so_rather_than_showing_one(self):
        alert = dict(self.alert)
        alert["measured"] = {k: v for k, v in alert["measured"].items()
                             if k != "top_contributor"}
        self.assertIn("top contributor: not ranked", self.rendered(alert))

    def test_the_alert_names_the_runtime_and_not_the_pid_as_its_subject(self):
        self.assertEqual(self.alert["subject_ref"], GROUP)
        self.assertEqual(self.alert["resource_ref"], GROUP)
        self.assertNotIn("79288", self.alert["subject_ref"])

    def test_the_message_stays_short_enough_to_read_on_a_phone(self):
        """§13. A message that has become a JSON document has stopped being a
        message; the cap is deliberately generous but it is a cap."""
        text = notifications.render(notifications.Event(
            event="alert.opened", device_ref=DEVICE, at=T0, correlation_id="c1",
            subject_ref=GROUP, reason="HIGH_CPU_SUSTAINED",
            detail=alerting.detail_for(self.alert, at=T0)))
        self.assertLess(len(text), 1200)
        self.assertNotIn("{", text)


# ---------------------------------------------------------------------------
# §15 nothing acts
# ---------------------------------------------------------------------------


class NothingHereActs(unittest.TestCase):
    """§15's rule is a permission — the strongest consequence an alert may carry
    is a recommendation — and the enforcement is not written twice.

    `mutation_guard` already scans every product file for the verbs §26 names,
    and its contract requires every live call site to be declared by name. So the
    assertion is that `runtime/alerting.py` produces no hits at all: not "the hits
    are declared", but that none exist. A scan that had to exempt this file would
    mean the alert layer contains something that changes the host.
    """

    def setUp(self):
        self.guard = _load("serveros_mutation_guard",
                           ROOT / "skills" / "serveros" / "scripts" /
                           "mutation_guard.py")
        self.source = (ROOT / "runtime" / "alerting.py").read_text(
            encoding="utf-8")

    def test_the_alert_layer_has_no_mutating_call_site(self):
        self.assertEqual(
            self.guard.scan_file("runtime/alerting.py", self.source)["hits"], [])

    def test_the_scanner_this_relies_on_can_go_red(self):
        killer = ("import os\n"
                  "def f(pid, store):\n"
                  "    os.kill(pid, 9)\n"
                  "    store.remove('x')\n")
        hits = self.guard.scan_file("x.py", killer)
        self.assertTrue(hits["parsed"], hits)
        self.assertTrue(hits["hits"],
                        "the guard found nothing in a file that kills")

    def test_the_recommendation_vocabulary_is_closed_and_advisory(self):
        for severity in ("low", "medium", "high", "unknown", ""):
            self.assertIn(alerting.recommendation(severity),
                          ("REVIEW", "MONITOR", "GOVERNANCE_REQUIRED"))

    def test_no_field_of_an_alert_reads_as_authority(self):
        """`alert != authorization` stated as a property of the record: there is
        no field a later reader could mistake for a mandate."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        store = alerting.AlertStore(Path(directory.name) / "alerts.json")
        alerting.evaluate(
            store, detection=detection(signals=[disk_signal()],
                                       levels=disk_level(93.0)),
            device_ref=DEVICE, at=T0, detector=MEASUREMENT)
        row = next(iter(store.alerts().values()))
        for field in ("action", "execute", "command", "authorized", "may",
                      "approved", "permission", "consent", "mandate"):
            self.assertNotIn(field, row)


# ---------------------------------------------------------------------------
# The policy these fixtures close against
# ---------------------------------------------------------------------------


class TheContractOwnsTheNumbers(unittest.TestCase):
    """The join between this file's literals and the declared policy. Without it
    every hysteresis test above compares the arithmetic to itself."""

    def test_the_fixture_numbers_are_the_declared_ones(self):
        declared = alerting._thresholds()
        for key, value in (("filesystem_percent_high", FS_HIGH),
                           ("filesystem_percent_recovery_margin", FS_MARGIN),
                           ("cpu_percent_high", CPU_HIGH),
                           ("cpu_percent_recovery_margin", CPU_MARGIN),
                           ("memory_rss_high_bytes", RSS_HIGH),
                           ("memory_rss_recovery_margin", RSS_MARGIN)):
            with self.subTest(key=key):
                self.assertEqual(declared[key], value)
        self.assertIn("snooze_default_seconds",
                      alerting.policy()["acknowledgement"])

    def test_the_fixture_cooldowns_are_the_declared_ones(self):
        self.assertEqual(alerting.policy()["cooldown_seconds"],
                         {"low": COOLDOWN_LOW, "medium": COOLDOWN_MEDIUM,
                          "high": COOLDOWN_HIGH})

    def test_the_declared_detector_names_are_the_two_this_file_uses(self):
        """The join for `DECLARATION` / `MEASUREMENT`. The name is what a row
        stores and what decides who may close it, so a contract that renamed
        one — or a daemon that wrote a different one — would leave every
        persisted row belonging to nobody: the "only the opener may close it"
        rule matching nothing, silently. That is the bug
        `alerting.evaluate`'s docstring exists for, one layer down."""
        self.assertEqual(sorted(alerting.detectors()), [DECLARATION, MEASUREMENT])
        self.assertNotEqual(DECLARATION, MEASUREMENT)

    def test_every_declared_detector_names_a_file_that_exists(self):
        """A name with nothing behind it is the `declared-but-absent` pattern,
        and this module's own vocabulary is not exempt from it. Nothing loads
        these paths — `runtime/view.py` is the only loader — so they are a
        claim about which module answers to a name, and a claim is checkable."""
        for name, path in alerting.detectors().items():
            with self.subTest(detector=name):
                self.assertTrue(path.endswith(".py"), path)
                self.assertTrue((ROOT / path).is_file(),
                                f"`{name}` names {path}, which does not exist")

    def test_every_severity_has_a_cooldown_and_the_smallest_is_not_a_minute(self):
        """§8's "not every minute", as a number rather than an adjective."""
        cooldowns = alerting.policy()["cooldown_seconds"]
        self.assertEqual(set(cooldowns), set(alerting.severity_words()))
        self.assertGreaterEqual(min(cooldowns.values()), 60)

    def test_every_graded_code_can_derive_a_margin_that_is_declared(self):
        """An alert whose margin key does not exist can never close: it holds with
        "there is no distance to come back" forever, which reads as an open
        problem nobody is dealing with. That failure already happened once — the
        three byte-valued aggregates named their code but never their margin — so
        it is asserted rather than trusted."""
        declared = alerting._thresholds()
        graded = set(anomaly.THRESHOLD_KEYS.values()) | {
            # The resource layer's graded measurements, which carry their margin
            # key on the level row rather than through THRESHOLD_KEYS.
            "filesystem_percent_critical", "inode_percent_high",
            "memory_percent_high", "swap_percent_high",
            "docker_storage_percent_high", "container_log_bytes_high",
            "container_writable_layer_bytes_high",
            "docker_build_cache_bytes_high",
        }
        for key in sorted(graded):
            with self.subTest(threshold=key):
                margin = anomaly.margin_key(key)
                self.assertIsNotNone(margin, f"{key} derives no margin key")
                self.assertIn(margin, declared,
                              f"{key} derives {margin}, which is not declared — "
                              f"an alert against it can never close")

    def test_a_declaration_shaped_threshold_derives_no_margin(self):
        """The other half, and the reason an alert about `RUNTIME_UNOWNED` closes
        on the fact disappearing. A margin invented for one would make a
        declaration resolvable by a number, which it is not."""
        for key in ("cpu_sustained_seconds", "age_long_seconds",
                    "idle_grace_seconds", "error_window_seconds"):
            with self.subTest(threshold=key):
                self.assertIsNone(anomaly.margin_key(key))

    def test_a_missing_policy_block_is_refused_rather_than_defaulted(self):
        """`policy()` raises rather than returning `{}`, because a module with a
        fallback cooldown is a module whose real cadence is decided in Python and
        merely documented in YAML."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / "notification-contract.yaml"
        path.write_text("events:\n  values: []\n", encoding="utf-8")
        original = alerting.CONTRACT
        alerting.CONTRACT = path
        self.addCleanup(setattr, alerting, "CONTRACT", original)
        with self.assertRaises(alerting.AlertError):
            alerting.policy()


# ---------------------------------------------------------------------------
# The store at rest
# ---------------------------------------------------------------------------


class TheStoreRefusesToStartEmpty(AlertCase):
    def test_an_unreadable_store_is_refused_rather_than_replaced(self):
        """Every open alert would notify again as if it were new, which is the
        spam this module exists to prevent, produced by the failure path."""
        self.disk()
        self.store.path.write_text("{not json", encoding="utf-8")
        with self.assertRaises(alerting.AlertError):
            alerting.AlertStore(self.store.path)

    def test_a_resolved_alert_stays_as_a_row(self):
        """A store that dropped it would make every recurrence look like the
        first one — and the first one notifies immediately by design."""
        self.disk(used=93.0)
        self.disk(seconds=60, used=50.0, severity=None)
        self.assertEqual(self.only()["state"], "RESOLVED")
        self.assertEqual(len(self.rows()), 1)

    def test_the_listing_puts_what_needs_attention_first(self):
        self.disk(severity="low")
        self.disk(seconds=60, used=94.0, subject=FS_DATA, severity="high")
        # No signal for `/` this tick, and a graded measurement below its floor:
        # the quiet path to RESOLVED.
        self.disk(seconds=120, used=50.0, severity=None)
        listing = alerting.listing(self.store, at=at(120))
        self.assertEqual([row["state"] for row in listing["alerts"]],
                         ["OPEN", "RESOLVED"])
        self.assertEqual(listing["by_state"]["OPEN"], 1)
        self.assertEqual(listing["by_state"]["RESOLVED"], 1)
        self.assertEqual(listing["count"], 2)
        # The state reached the file, not only the object in memory.
        on_disk = json.loads(self.store.path.read_text(encoding="utf-8"))
        self.assertEqual(len(on_disk["alerts"]), 2)


# ---------------------------------------------------------------------------
# The five mutations. §19
# ---------------------------------------------------------------------------

_MUTANT_INDEX = itertools.count()


class Mutants(unittest.TestCase):
    """Edits `runtime/alerting.py` and loads the result under its own name.

    Every replacement must match EXACTLY ONCE. A mutation that silently matched
    nothing would leave the real module in place and the assertions below would
    pass while proving nothing, which is the failure mode this section exists to
    avoid.

    Only `alerting.py` is copied. Its siblings are reached through `sys.modules`
    under the names the real module already loaded them with (see the top of this
    file), so a mutant shares its dependencies and mutates only the logic under
    test. One global is repaired afterwards and named here rather than left
    implicit: `CONTRACT` is `_HERE / "notification-contract.yaml"`, and a copy of
    the file in a temporary directory would look for the contract beside itself.
    The mutant reads the same contract the real module does.
    """

    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def mutant(self, *replacements: tuple[str, str]):
        source = (ROOT / "runtime" / "alerting.py").read_text(encoding="utf-8")
        for old, new in replacements:
            count = source.count(old)
            assert count == 1, f"{old!r} matched {count} times, expected once"
            source = source.replace(old, new)
        path = self.directory / f"alerting_{next(_MUTANT_INDEX)}.py"
        path.write_text(source, encoding="utf-8")
        module = _load(f"serveros_alerting_mutant_{next(_MUTANT_INDEX)}", path)
        module.CONTRACT = alerting.CONTRACT
        return module

    def store_for(self, module):
        """A store of this body's own. Shared, the bodies below would read each
        other's rows and a later one would pass or fail on an earlier one's
        leftovers."""
        return module.AlertStore(
            self.directory / f"state_{next(_MUTANT_INDEX)}" / "alerts.json")

    def evaluate(self, module, store, *, seconds=0.0, **kwargs):
        return module.evaluate(
            store, detection=detection(measured_at=at(seconds), **kwargs),
            device_ref=DEVICE, at=at(seconds),
            detector=MEASUREMENT)

    # -- the bodies of the passing tests, re-run against a mutant ------------

    def assert_a_failed_delivery_is_not_a_delivery(self, module):
        store = self.store_for(module)
        self.evaluate(module, store, signals=[disk_signal()],
                      levels=disk_level(93.0))
        ref = next(iter(store.alerts().values()))["alert_ref"]
        module.record_delivery(store, alert_ref=ref, kind="opened", at=at(60),
                               delivered=False, state="FAILED",
                               reason="gateway 502")
        row = store.alerts()[ref]
        self.assertIsNone(row["last_notified_at"])
        self.assertIsNotNone(row["pending"])

    def assert_an_unevaluated_code_does_not_resolve(self, module):
        store = self.store_for(module)
        self.evaluate(module, store, signals=[disk_signal()],
                      levels=disk_level(93.0))
        self.evaluate(module, store, seconds=60,
                      ungraded=[{"code": "DATA_FS_PRESSURE",
                                 "input": "host_facts", "state": "UNKNOWN",
                                 "why": "no read"}])
        self.assertEqual(
            next(iter(store.alerts().values()))["state"], "OPEN")

    def assert_one_row_for_one_identity(self, module):
        store = self.store_for(module)
        for seconds in (0, 60):
            self.evaluate(module, store, seconds=seconds,
                          signals=[disk_signal()], levels=disk_level(93.0))
        self.assertEqual(len(store.alerts()), 1)
        self.assertEqual(
            next(iter(store.alerts().values()))["occurrence_count"], 2)

    def assert_acknowledged_is_not_resolved(self, module):
        store = self.store_for(module)
        self.evaluate(module, store, signals=[disk_signal()],
                      levels=disk_level(93.0))
        ref = next(iter(store.alerts().values()))["alert_ref"]
        module.acknowledge(store, ref, at=at(60))
        self.assertEqual(store.alerts()[ref]["state"], "ACKNOWLEDGED")
        self.assertIsNone(store.alerts()[ref]["resolved_at"])

    def assert_a_failed_delivery_does_not_retry_every_tick(self, module):
        """The whole sequence matters, and the first version of this body was
        wrong about it: with the alert opened and failing in the same tick,
        `first_seen` and the attempt time are the same instant and the two clocks
        agree. They diverge only once a SUCCESSFUL delivery is behind it — which
        is the case the defect is actually about."""
        store = self.store_for(module)
        ref = None
        self.evaluate(module, store, signals=[disk_signal()],
                      levels=disk_level(93.0))
        ref = next(iter(store.alerts().values()))["alert_ref"]
        module.record_delivery(store, alert_ref=ref, kind="opened", at=T0,
                               delivered=True, state="DELIVERED")
        # A repeat is due one cooldown later, and THAT attempt fails.
        self.evaluate(module, store, seconds=COOLDOWN_HIGH,
                      signals=[disk_signal()], levels=disk_level(93.0))
        self.assertIsNotNone(store.alerts()[ref]["pending"])
        module.record_delivery(store, alert_ref=ref, kind="opened",
                               at=at(COOLDOWN_HIGH), delivered=False,
                               state="FAILED")
        # One second after the failed attempt nothing may be due again.
        self.evaluate(module, store, seconds=COOLDOWN_HIGH + 1,
                      signals=[disk_signal()], levels=disk_level(93.0))
        self.assertEqual(
            module.pending_for(store, at=at(COOLDOWN_HIGH + 1)), [])

    # -- and each one must be able to go red --------------------------------

    def test_the_unmutated_bodies_pass(self):
        """The positive control for this whole section. If one of the bodies
        below were already failing, the mutant that "caught" it would be catching
        nothing."""
        for body in (self.assert_a_failed_delivery_is_not_a_delivery,
                     self.assert_an_unevaluated_code_does_not_resolve,
                     self.assert_one_row_for_one_identity,
                     self.assert_acknowledged_is_not_resolved,
                     self.assert_a_failed_delivery_does_not_retry_every_tick):
            with self.subTest(body=body.__name__):
                body(alerting)

    def test_a_delivery_failure_marked_as_delivered_is_caught(self):
        with self.assertRaises(AssertionError):
            self.assert_a_failed_delivery_is_not_a_delivery(self.mutant(
                ('    if delivered:\n        alert["last_notified_at"] = at',
                 '    if True:\n        alert["last_notified_at"] = at')))

    def test_treating_an_unevaluated_code_as_recovered_is_caught(self):
        with self.assertRaises(AssertionError):
            self.assert_an_unevaluated_code_does_not_resolve(self.mutant(
                ('    if code in ungraded:\n'
                 '        return "HELD", (f"{code} was not evaluated this tick "',
                 '    if code in ungraded:\n'
                 '        return "RESOLVED", (f"{code} was not evaluated '
                 'this tick "')))

    def test_removing_the_identity_dedupe_is_caught(self):
        with self.assertRaises(AssertionError):
            self.assert_one_row_for_one_identity(self.mutant(
                ("        alert = alerts.get(ref)\n", "        alert = None\n")))

    def test_treating_an_acknowledgement_as_a_resolution_is_caught(self):
        with self.assertRaises(AssertionError):
            self.assert_acknowledged_is_not_resolved(self.mutant(
                ('    alert["state"] = "ACKNOWLEDGED"\n',
                 '    alert["state"] = "RESOLVED"\n')))

    def test_putting_the_cooldown_clock_back_on_the_delivery_is_caught(self):
        """The bug this replaced, as a mutation. With the clock running from
        `last_notified_at`, a failed delivery never sets it, so after one
        success followed by a failure the cooldown appears already elapsed and
        every tick tries again — §12's forbidden retry loop, silent because a
        failure that is retried looks like diligence."""
        with self.assertRaises(AssertionError):
            self.assert_a_failed_delivery_does_not_retry_every_tick(self.mutant(
                ('    since = alert.get("last_attempt_at") or '
                 'alert.get("first_seen")',
                 '    since = alert.get("last_notified_at") or '
                 'alert.get("first_seen")')))

    def test_a_mutation_that_applied_is_a_different_module(self):
        """A guard on the guard: the assertions above depend on each edit having
        landed. `mutant()` raises on a non-unique match, so a passing mutation
        test is itself evidence that its edit applied — and this states that the
        loader really does produce a second object rather than handing back the
        real module under a new key."""
        module = self.mutant(("        alert = alerts.get(ref)\n",
                              "        alert = None\n"))
        self.assertIsNot(module, alerting)
        self.assertIsNot(module.AlertStore, alerting.AlertStore)
        self.assertEqual(module.OPENED, alerting.OPENED)


if __name__ == "__main__":
    unittest.main()
