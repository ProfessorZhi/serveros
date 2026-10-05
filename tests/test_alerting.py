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
from unittest import mock

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
# The detector's own code-to-threshold table, which is where "which key does
# this code close against" actually lives. Loaded under the name
# `test_resource_anomaly.py` loads it with so the two files share one module
# object; the failure that would follow from two copies is the one the comment
# above names.
resource_anomaly = _load("serveros_runtime_resource_anomaly",
                         ROOT / "runtime" / "resource_anomaly.py")
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

# The pressure axis. `anomaly.margin_key` turns the `_high` suffix into a
# `_recovery_margin`, and whether the contract declares that derived key is
# the difference between a stall alert that closes and one that never can.
PSI_HIGH = 1.0
PSI_MARGIN = 0.5
PSI_FLOOR = 0.5            # PSI_HIGH - PSI_MARGIN
PSI_SUBJECT = "host:memory-pressure"

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


def detection(*, signals=(), levels=None, ungraded=(), subjects=None,
              answered_by=MEASUREMENT, measured_at=T0) -> dict:
    """One detection document. `subjects` is the pass's inventory of what it
    enumerated, and `None` OMITS the key rather than sending an empty list —
    the two are different documents. A document without the key was written
    before the inventory existed (or by a hand-built caller) and is evaluated
    exactly as it was before; a document with `subjects=[]` says the pass
    enumerated nothing, which the alert layer refuses to read as "everything
    is gone". Both are tested below, and a helper that conflated them would
    make one of those tests unwriteable.
    """
    document = {"signals": list(signals), "levels": levels or {},
                "codes_not_evaluated": list(ungraded),
                "answered_by": answered_by, "measured_at": measured_at}
    if subjects is not None:
        document["subjects"] = list(subjects)
    return document


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


def psi_signal(*, some=6.8, severity="medium", subject=PSI_SUBJECT) -> dict:
    """MEMORY_PRESSURE_ACTIVE, in the shape `_memory_signals` emits.

    The readings are this host's own, from the 2026-09-29 window the split was
    made for: swap at 80.5%, below its 90% threshold, so the watermark code
    stayed silent — while memory PSI some avg60 ran 6.80 → 10.53 against a
    threshold of 1.0, with 53,238 pages/second leaving the machine. The
    watermark and the pressure are deliberately carried on each other's
    signals, because neither number means what it looks like alone.
    """
    return {"code": "MEMORY_PRESSURE_ACTIVE", "subject": subject,
            "severity": severity,
            "measured": {
                "memory_pressure": {
                    "state": "PRESENT",
                    "some": {"avg10": 9.4, "avg60": some, "avg300": 5.1},
                    "full": {"avg10": 9.3, "avg60": round(some - 0.06, 2),
                             "avg300": 5.0}},
                "psi_memory_some_avg60_percent_high": PSI_HIGH,
                "psi_memory_full_avg60_percent_high": 0.5,
                "available_bytes": 201863462912,
                "available_percent": 77.8,
                "swap_used_percent": 80.5,
                "paging": {"state": "PRESENT", "window_seconds": 1.02,
                           "pswpin_per_second": 0.0,
                           "pswpout_per_second": 53238.0},
            },
            "why": f"tasks on this host are stalling on memory ({some}%)"}


def psi_level(some, *, subject=PSI_SUBJECT) -> dict:
    return level("MEMORY_PRESSURE_ACTIVE", subject, some, PSI_HIGH,
                 "psi_memory_some_avg60_percent_recovery_margin")


def psi_state(some, *, severity="medium", subject=PSI_SUBJECT,
              reported=True):
    """(signals, levels) for one stall reading, from ONE number.

    Paired for the reason `disk_state` is: the detector emits a level row for
    every code it grades, so a signal without its row describes an alert state
    production cannot reach — here, one holding `unmeasured` against a machine
    once again stalling 6.8%.

    `severity=None` is how the caller says the code did NOT fire: a level row is
    emitted whatever the value, a signal only above the threshold. Both halves
    matter, and getting the second wrong is the quieter mistake — a fixture that
    kept signalling at 0.4% would be asserting that an alert closes while the
    thing it is about is still happening.
    """
    signals = ([] if severity is None else
               [psi_signal(some=some, severity=severity, subject=subject)])
    levels = psi_level(some, subject=subject) if reported else {}
    return signals, levels


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

    def test_a_subject_the_pass_did_not_enumerate_is_gone_and_closes(self):
        """The other half of the test above, and the half that was missing.

        A runtime group's ref embeds a pid, so the ref is a LIFETIME: when the
        group ends, its ref never comes back. Held as `unmeasured` such an
        alert said "a subject that stopped being measured has not recovered"
        forever. Measured on this node 2026-10-04, 1,088 alerts were frozen
        OPEN saying exactly that, and every one of the 1,088 subjects no longer
        existed — checked against the live process table, not against the
        inventory that made the decision.

        The distinction the two tests draw between them is the whole change: a
        subject that was LOOKED AT and gave nothing keeps holding, and a subject
        that was not there to look at has ended. Both are an absence from
        `levels`, and until now they were one answer.

        Nothing was announced here — this fixture never delivers — and the
        closure therefore owes no message. `pending` is a claim about what will
        be SAID, and a recovery message takes back an announcement; the digest
        side has read this same ending as a `silent_resolution` since before the
        register agreed with it. The assertion below is the agreement.
        """
        self.disk(used=93.0, subjects=[FS])
        self.disk(seconds=60, severity=None, reported=False,
                  subjects=["filesystem:/somewhere-else"])
        row = self.only()
        self.assertEqual(row["state"], "RESOLVED")
        self.assertEqual(row["resolved_at"], at(60))
        self.assertIsNone(row["hold_kind"])
        self.assertIsNone(row["pending"])
        self.assertIsNone(row["last_notified_at"])

    def test_the_gone_verdict_is_named_rather_than_read_from_the_sentence(self):
        """`subject_gone` is a fourth `kind`, and it is asserted here because a
        RESOLVED verdict does not store one on the alert — so nothing else in
        the suite would notice if this word changed. It is the machine-readable
        half: a consumer that had to recognise this case by matching prose would
        change meaning the day somebody fixed a typo in the reason."""
        verdict, _why, kind = alerting._graded_measurement(
            {"code": "DATA_FS_PRESSURE", "subject_ref": FS,
             "margin_key": "filesystem_percent_recovery_margin"},
            levels={}, ungraded={}, subjects={"filesystem:/somewhere-else"})
        self.assertEqual(verdict, "RESOLVED")
        self.assertEqual(kind, "subject_gone")

    def test_a_subject_that_came_back_unreadable_still_holds(self):
        """The regression guard for the change. An inventory that CONTAINS the
        subject with no level row behind it is the unreadable case — a mount
        that was enumerated and whose `statvfs` gave nothing — and it must keep
        holding, because no reading is not a reading of zero. Without this test
        the new branch could be widened to "absent from `levels`" and the suite
        would stay green while every unreadable disk closed its own alert."""
        self.disk(used=93.0, subjects=[FS])
        result = self.disk(seconds=60, severity=None, reported=False,
                           subjects=[FS])
        row = self.only()
        self.assertEqual(row["state"], "OPEN")
        self.assertEqual(row["hold_kind"], "unmeasured")
        self.assertTrue(any("not reported this tick" in entry["why"]
                            for entry in result["held"]), result["held"])

    def test_an_empty_inventory_is_not_an_inventory_of_nothing(self):
        """A pass that enumerated no subjects has not established that every
        subject is gone. `evaluate` reads an empty list as "no inventory was
        taken" precisely so that a document carrying none cannot mass-resolve
        the register — the failure mode this whole branch has to avoid."""
        self.disk(used=93.0, subjects=[FS])
        self.disk(seconds=60, severity=None, reported=False, subjects=[])
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertEqual(self.only()["hold_kind"], "unmeasured")

    def test_a_document_without_an_inventory_behaves_exactly_as_before(self):
        """Every caller that predates the key — hand-built documents, tests,
        older producers — must be unaffected. This is the same assertion as the
        unreadable case above, reached by omitting the key rather than by
        putting the subject in it, and it is what makes the change additive."""
        self.disk(used=93.0)
        self.disk(seconds=60, severity=None, reported=False)
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertEqual(self.only()["hold_kind"], "unmeasured")

    def test_a_degraded_pass_holds_before_the_inventory_is_consulted(self):
        """The safety property, as an ordering rather than an intention.

        When the process table fails to answer, the group codes are reported as
        not evaluated — and they are ALSO absent from that pass's inventory,
        because nothing enumerated the groups. If the inventory were consulted
        first, a failed `/proc` read would close every group alert on the node
        at once. The `not_evaluated` branch runs first for exactly this reason,
        and this test fails if somebody moves it.
        """
        self.run_pass(signals=[cpu_signal()], levels=cpu_level(279.5),
                      subjects=[GROUP])
        self.assertEqual(self.only()["state"], "OPEN")
        result = self.run_pass(seconds=60, subjects=[],
                               ungraded=[{"code": "HIGH_CPU_SUSTAINED",
                                          "why": "no runtime groups were supplied"}])
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertEqual(self.only()["hold_kind"], "not_evaluated")
        self.assertTrue(any(entry["why"] and "not evaluated" in entry["why"]
                            for entry in result["held"]), result["held"])

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
        # The opening message ARRIVES first, and that is not scene-setting: a
        # recovery is a reply to it, and the rule that decides whether one is
        # owed reads the record of the reply's subject. Without this line the
        # fixture was asking for a recovery message about an episode nobody had
        # been told about — the shape 11,077 rows on this node were in.
        self.disk(used=93.0)
        self.deliver()
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
        self.deliver()
        alerting.acknowledge(self.store, self.only()["alert_ref"], at=at(60))
        self.disk(seconds=60, used=50.0, severity=None)
        self.assertEqual(self.only()["state"], "RESOLVED")
        self.assertEqual(
            [item["kind"] for item in
             alerting.pending_for(self.store, at=at(60))], ["resolved"])


# ---------------------------------------------------------------------------
# `pending` is a claim about what will be SAID, and §2 decides what may be
# ---------------------------------------------------------------------------


class WhatMayBeSaidIsTheContractsQuestion(AlertCase):
    """`pending` governed by `alerts.attention.on_the_face`.

    Measured on this node 2026-10-04, which is why this class exists: 13,207
    rows carried a `pending` and 1,204 had ever been announced, and 1,193 of
    those announcements were for codes the contract has never put on a card.
    The register was keeping a ledger of messages it had no surface to send.
    """

    def watch_alert(self, *, cpu=279.5, seconds=0.0):
        """One WATCH-code alert: `HIGH_CPU_SUSTAINED` is not on the face."""
        return self.run_pass(seconds=seconds, signals=[cpu_signal(cpu=cpu)],
                             levels=cpu_level(cpu))

    def test_the_face_is_read_from_the_contract_and_not_from_here(self):
        """The guard against a second attention table. `alerting` must agree
        with `presentation` about which codes a card may carry; if this set were
        written out in `alerting.py` the two would part company the first time
        somebody moved a code between buckets."""
        presentation = alerting._presentation()
        face = tuple(presentation.attention_policy().get("on_the_face") or ())
        expected = {code for code, entry
                    in presentation.code_expectations().items()
                    if str(entry.get("level") or "") in face}
        self.assertEqual(alerting._sayable_codes(), frozenset(expected))
        self.assertIn("DATA_FS_PRESSURE", expected)
        self.assertNotIn("HIGH_CPU_SUSTAINED", expected)

    def test_a_code_off_the_face_opens_without_an_intent_and_says_why(self):
        """Both halves. An intent that cannot be delivered is not owed, and a
        finding that is not going to be spoken about is still accounted for —
        `held` is the list that answers "why was I not told about this", and for
        a code off the face the answer is a fact about the contract."""
        result = self.watch_alert()
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertIsNone(self.only()["pending"])
        self.assertEqual(alerting.pending_for(self.store, at=T0), [])
        self.assertTrue([entry for entry in result["held"]
                         if entry["stage"] == "notification"
                         and "HIGH_CPU_SUSTAINED" in entry["why"]
                         and "not on a card's face" in entry["why"]],
                        result["held"])

    def test_a_code_on_the_face_still_opens_owing_a_message(self):
        """The positive control for the test above, and the reason it is not a
        test that passes because nothing is ever queued."""
        self.disk(used=93.0)
        self.assertEqual(self.only()["pending"]["kind"], "opened")
        self.assertEqual(
            [item["kind"] for item in
             alerting.pending_for(self.store, at=T0)], ["opened"])

    def test_a_row_from_before_the_rule_is_brought_into_line_by_a_pass(self):
        """The migration is the pass, not a separate run over the store. A rule
        that governed only new rows would leave 13,207 claims on the books and
        the Status Card printing the number."""
        self.watch_alert()
        stale = self.store.alerts()
        ref = self.only()["alert_ref"]
        # Exactly the shape the old code wrote: an OPEN row owing an `opened`.
        stale[ref]["pending"] = {"kind": "opened", "not_before": T0}
        self.store.commit(stale)
        self.assertEqual(len(alerting.pending_for(self.store, at=T0)), 1)

        result = self.watch_alert(seconds=60)
        self.assertIsNone(self.only()["pending"])
        self.assertEqual(alerting.pending_for(self.store, at=at(60)), [])
        self.assertTrue([entry for entry in result["held"]
                         if "withdrawn" in entry["why"]], result["held"])
        # The finding itself is untouched: still open, still graded, still the
        # same episode. Only the claim about saying it went away.
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertEqual(self.only()["occurrence_count"], 2)

    def test_a_pass_that_closes_a_thousand_never_announced_alerts_owes_none(self):
        """The case the rules exist for. This node will close the 1,024 frozen
        `HIGH_MEMORY_SUSTAINED` rows in one pass when the subject inventory
        reaches the live daemon, and the shape of that pass must not be 1,024
        recovery messages about episodes nobody was ever told about."""
        subjects = [f"runtime-group:batch:{index}" for index in range(1000)]
        self.run_pass(signals=[cpu_signal(subject=subject) for subject in subjects],
                      levels={key: row
                              for subject in subjects
                              for key, row in cpu_level(279.5,
                                                        subject=subject).items()},
                      subjects=subjects)
        self.assertEqual(len(self.rows()), 1000)
        # Every one of them is gone from the next pass's inventory, and they are
        # all WATCH codes: the closure says nothing, and no row claims otherwise.
        self.run_pass(seconds=60, subjects=["runtime-group:batch:0"])
        resolved = [row for row in self.rows().values()
                    if row["state"] == "RESOLVED"]
        self.assertEqual(len(resolved), 999)
        self.assertEqual(alerting.pending_for(self.store, at=at(60)), [])
        self.assertTrue(all(row["pending"] is None for row in resolved))

    def test_one_detectors_rows_are_not_reconciled_by_the_other(self):
        """Reconciliation is per detector for the same reason closing is: this
        pass never saw that document, and it has no standing to rule on it."""
        self.run_pass(signals=[cpu_signal()], levels=cpu_level(279.5),
                      detector=DECLARATION)
        rows = self.store.alerts()
        ref = self.only()["alert_ref"]
        rows[ref]["pending"] = {"kind": "opened", "not_before": T0}
        self.store.commit(rows)

        self.watch_alert(seconds=60)          # the MEASUREMENT detector's pass
        self.assertEqual(len(alerting.pending_for(self.store, at=at(60))), 1)
        self.assertEqual(self.only()["detector"], DECLARATION)


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


class ASwapWatermarkIsNotAPressure(AlertCase):
    """SWAP_PRESSURE reaches the card resting on both of its readings.

    Graded on the watermark alone it reported a full swap file as pressure.
    Measured on this host 2026-09-15: 99.2% of swap in use, memory PSI at 0.00
    across every window, and `pswpout` unmoved across two readings forty
    minutes apart — nothing was paging. `inventory._swap_note` has separated a
    watermark from a pressure since it was written; this is the same separation
    on the face a person reads.
    """

    WATERMARK = {"used_percent": 99.2, "threshold_percent": 90.0,
                 "used_bytes": 17040000000, "total_bytes": 17179865088}

    @staticmethod
    def _psi(state: str, some=0.0, full=0.0) -> dict:
        if state == "UNAVAILABLE":
            return {"state": "UNAVAILABLE",
                    "reason": "/proc/pressure/memory is not readable"}
        return {"state": "PRESENT",
                "some": {"avg10": some, "avg60": some, "avg300": some},
                "full": {"avg10": full, "avg60": full, "avg300": full}}

    def _rendered(self, **measured) -> str:
        base = dict(self.WATERMARK)
        base.update(measured)
        return alerting._measurement_line(
            {"code": "SWAP_PRESSURE", "measured": base}, base)

    def test_the_stall_figures_are_printed_beside_the_thresholds(self):
        line = self._rendered(memory_pressure=self._psi("PRESENT"),
                              psi_memory_some_avg60_percent_high=1.0,
                              psi_memory_full_avg60_percent_high=0.5)
        self.assertIn("99.2% (≥ threshold 90.0%)", line)
        self.assertIn("some avg60=0.0% (threshold 1.0%)", line)
        self.assertIn("full avg60=0.0% (threshold 0.5%)", line)
        # The watermark is above its line and says so; the two stall figures
        # are below theirs and therefore claim nothing. The operator is a
        # comparison, and a comparison is only printed when it holds.
        self.assertNotIn("avg60=0.0% (≥", line)

    def test_a_stalling_host_still_prints_both_and_decides_neither(self):
        """The other side of the same branch. The renderer prints the reading
        and the threshold; it does not declare a verdict, here or anywhere."""
        line = self._rendered(memory_pressure=self._psi("PRESENT", some=4.5,
                                                        full=2.5),
                              psi_memory_some_avg60_percent_high=1.0,
                              psi_memory_full_avg60_percent_high=0.5)
        self.assertIn("some avg60=4.5% (≥ threshold 1.0%)", line)
        self.assertIn("full avg60=2.5% (≥ threshold 0.5%)", line)

    def test_an_unreadable_psi_is_not_reported_as_no_pressure(self):
        """"Cannot tell" is not "fine", and the same conflation that
        `pressure_stall` refuses to make in a field must not reappear in a
        sentence."""
        line = self._rendered(memory_pressure=self._psi("UNAVAILABLE"),
                              psi_memory_some_avg60_percent_high=1.0,
                              psi_memory_full_avg60_percent_high=0.5)
        self.assertIn("NOT established", line)
        self.assertIn("not readable", line)
        self.assertIn("watermark", line)
        self.assertNotIn("some avg60=0.0%", line)

    def test_a_row_from_before_the_psi_was_threaded_gains_no_clause(self):
        """A digest row written before this change has no `memory_pressure`,
        and the card must not invent a stall reading for it — nor lose the
        watermark it does have."""
        line = self._rendered()
        self.assertIn("99.2% (≥ threshold 90.0%)", line)
        self.assertNotIn("memory stall", line)
        self.assertNotIn("NOT established", line)

    def test_a_reading_below_its_line_prints_the_line_and_claims_nothing(self):
        """§4 wants value, operator and threshold together — and the operator
        is a claim. `80.0%（≥ 90.0%）` would be this renderer asserting an
        inequality its own two numbers contradict, which is the same failure as
        omitting the operator, reached from the other side. Both halves are
        asserted here because either one alone passes on the wrong code."""
        below = self._rendered(used_percent=80.0)
        self.assertIn("80.0% (threshold 90.0%)", below)
        self.assertNotIn("80.0% (≥", below)

        above = self._rendered(used_percent=92.0)
        self.assertIn("92.0% (≥ threshold 90.0%)", above)

    def test_an_operator_comes_from_the_declared_key_and_never_from_a_guess(self):
        """`direction()` reads a suffix, and a key of neither shape gets no
        operator rather than a default. `cpu_sustained_seconds` is a window,
        not a line to cross: there is no comparison to print, and inventing one
        would put a claim on the face that no detector ever made."""
        def line_for(code, key, value=95.0):
            measured = {"used_percent": value, "threshold_percent": 90.0}
            with mock.patch.object(alerting, "_THRESHOLD_KEY_CACHE",
                                   {code: key}):
                return alerting._measurement_line(
                    {"code": code, "measured": measured}, measured)

        self.assertEqual(line_for("DISK_WARNING", "filesystem_percent_high"),
                         "95.0% (≥ threshold 90.0%)")
        self.assertEqual(line_for("DISK_CRITICAL", "filesystem_percent_critical"),
                         "95.0% (≥ threshold 90.0%)")
        self.assertEqual(line_for("SOMETHING_WINDOWED", "cpu_sustained_seconds"),
                         "95.0% (threshold 90.0%)")
        self.assertEqual(line_for("A_CODE_NOBODY_GRADES", None),
                         "95.0% (threshold 90.0%)")
        # A lower bound is read when the contract grows one, without this
        # function having to change to read it correctly — and it is read the
        # same way round, off the key's own spelling.
        self.assertEqual(line_for("SOMETHING_LOW", "free_percent_low", 85.0),
                         "85.0% (≤ threshold 90.0%)")
        self.assertEqual(line_for("SOMETHING_LOW", "free_percent_low", 95.0),
                         "95.0% (threshold 90.0%)")

    def test_a_psi_that_arrived_without_a_window_is_not_read_as_zero(self):
        """`some` and `full` are read separately: a `some` line the kernel did
        not print is a measurement that did not happen, not a reading of 0.0."""
        line = self._rendered(memory_pressure={"state": "PRESENT", "full": {}},
                              psi_memory_some_avg60_percent_high=1.0,
                              psi_memory_full_avg60_percent_high=0.5)
        self.assertIn("NOT established", line)
        self.assertNotIn("avg60=0.0%", line)


class ThePressureClosesOnItsOwnMargin(AlertCase):
    """MEMORY_PRESSURE_ACTIVE: does it stop, and does it stop for a reason.

    A code added to `refuse` is a code that will interrupt somebody, so the
    question worth asking about it is not whether it fires. It is whether the
    number that lets it close EXISTS. The margin is derived rather than
    written: `_THRESHOLDS` points the code at `psi_memory_some_avg60_percent_high`,
    `anomaly.margin_key` turns that into
    `psi_memory_some_avg60_percent_recovery_margin`, and a contract that never
    declares the derived key leaves the alert holding `margin_undeclared` for
    good. Nothing else in the tree would notice — no import fails, no file
    changes, and the register simply carries one more open row, which is
    indistinguishable from a register of real problems.
    """

    def tick(self, some, *, seconds=0.0, reported=True):
        """One pass, with the signal present exactly when the code fires."""
        severity = "medium" if some >= PSI_HIGH else None
        signals, levels = psi_state(some, severity=severity, reported=reported)
        return self.run_pass(seconds=seconds, signals=signals, levels=levels)

    def test_a_stall_that_comes_back_down_closes_the_alert(self):
        self.tick(6.8)
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertEqual(self.only()["margin_key"],
                         "psi_memory_some_avg60_percent_recovery_margin")
        self.tick(0.4, seconds=60)
        self.assertEqual(self.only()["state"], "RESOLVED")
        self.assertEqual(self.only()["resolved_at"], at(60))

    def test_the_floor_is_the_threshold_minus_the_declared_margin(self):
        """0.5 is the line, and the line is arithmetic rather than a mood: at
        0.6 the machine is still stalling and the alert holds, at 0.5 it
        closes. A margin of zero would make it chatter across its own
        threshold, which is why the number is declared rather than inferred."""
        self.tick(6.8)
        self.tick(0.6, seconds=60)
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertEqual(self.only()["hold_kind"], "above_floor")
        self.assertIn("0.5", self.only()["hold_reason"])
        self.tick(PSI_FLOOR, seconds=120)
        self.assertEqual(self.only()["state"], "RESOLVED")
        self.assertEqual(self.only()["resolved_at"], at(120))

    def test_a_margin_that_was_never_declared_is_the_failure_this_guards(self):
        """The positive control for the contract entry.

        Deleting `psi_memory_some_avg60_percent_recovery_margin` from
        `attribution-contract.yaml` breaks nothing that anything else checks.
        The alert does not crash and does not fire late: it holds, for the rest
        of the decade, with a sentence about there being no distance to come
        back — a stall that has plainly ended, reported as still open. So both
        halves are asserted: the declared key closes the alert, and the same
        alert with that one key withheld does not.
        """
        declared = alerting._thresholds()
        self.assertIn("psi_memory_some_avg60_percent_recovery_margin", declared)
        alert = {"code": "MEMORY_PRESSURE_ACTIVE", "subject_ref": PSI_SUBJECT,
                 "margin_key": "psi_memory_some_avg60_percent_recovery_margin"}
        levels = psi_level(PSI_FLOOR)

        verdict, _, kind = alerting._graded_measurement(
            alert, levels=levels, ungraded={})
        self.assertEqual((verdict, kind), ("RESOLVED", "below_floor"))

        without = {k: v for k, v in declared.items()
                   if k != "psi_memory_some_avg60_percent_recovery_margin"}
        with mock.patch.object(alerting, "_thresholds", return_value=without):
            verdict, why, kind = alerting._graded_measurement(
                alert, levels=levels, ungraded={})
        self.assertEqual((verdict, kind), ("HELD", "margin_undeclared"))
        self.assertIn("no distance to come back", why)

    def test_the_watermark_still_closes_after_it_stopped_refusing_things(self):
        """`SWAP_PRESSURE` moved from `refuse` to `advisory`. Admission is a
        permission and recovery is arithmetic, and the two are not the same
        axis: a code that is no longer grounds for refusing the creation of
        anything is still a code that has to be able to say the watermark came
        back down. Nothing about the regrade touches this path — which is the
        reason to assert it rather than to leave it to be inferred from a diff.
        """
        declared = alerting._thresholds()
        margin = "swap_percent_recovery_margin"
        alert = {"code": "SWAP_PRESSURE", "subject_ref": "host:swap",
                 "margin_key": margin}
        floor = declared["swap_percent_high"] - declared[margin]
        verdict, _, kind = alerting._graded_measurement(
            alert, levels=level("SWAP_PRESSURE", "host:swap", floor,
                                declared["swap_percent_high"], margin),
            ungraded={})
        self.assertEqual((verdict, kind), ("RESOLVED", "below_floor"))

    def test_an_unreadable_stall_figure_holds_rather_than_closes(self):
        """"Nobody looked" is not "it recovered", asserted at the alert layer
        rather than only at the detector's, because the two are different
        claims and only one of them can close an alert."""
        self.tick(6.8)
        self.run_pass(seconds=60, ungraded=[
            {"code": "MEMORY_PRESSURE_ACTIVE",
             "input": "host_summary()['pressure']['memory']",
             "state": "UNAVAILABLE",
             "why": "the memory pressure-stall figures were not readable"}])
        self.assertEqual(self.only()["state"], "OPEN")
        self.assertEqual(self.only()["hold_kind"], "not_evaluated")


class TheFaceIsOneOfThePartsAndNotASecondReading(AlertCase):
    """One producer, two registers — asserted as a relationship.

    Incident Card V2 puts the leading reading on the first screen, and the way
    that goes wrong is a renderer that reaches into the register row a second
    time to get it. Two readers of one payload agree today and drift on the day
    somebody changes the wording in one of them, and the drift shows up as a
    face and a fold beneath it printing two figures for one finding.

    So the face is not re-derived: `measurement_face` returns one of the parts
    `measurement_parts` already produced, and `_measurement_line` is the join of
    the same list. The class above pins the STRINGS this produces; these cases
    pin the SHAPE that keeps them from becoming two sources, which is the part
    a rendering test cannot see.
    """

    #: A percentage against its tier: the ordinary case.
    DISK = {"used_percent": 92.9, "threshold_percent": 92.0}
    #: Two readings at once, so "which one is on the face" is a real question.
    SWAP = {"used_percent": 99.2, "threshold_percent": 90.0,
            "memory_pressure": {"state": "PRESENT",
                                "some": {"avg10": 4.5, "avg60": 4.5,
                                         "avg300": 4.5},
                                "full": {"avg10": 2.5, "avg60": 2.5,
                                         "avg300": 2.5}},
            "psi_memory_some_avg60_percent_high": 1.0,
            "psi_memory_full_avg60_percent_high": 0.5}
    #: A comparison whose own threshold is the declaration on one side.
    DECLARATION = {"desired_state": "running",
                   "actual_derived_state": "NOT_RUNNING",
                   "actual_raw": "exited"}

    def test_the_face_is_a_part_this_function_already_produced(self):
        for code, measured in (("DISK_CRITICAL", self.DISK),
                               ("SWAP_PRESSURE", self.SWAP),
                               ("DESIRED_ACTUAL_MISMATCH", self.DECLARATION)):
            with self.subTest(code=code):
                alert = {"code": code, "measured": measured}
                self.assertIn(alerting.measurement_face(alert, measured),
                              alerting.measurement_parts(alert, measured))

    def test_the_face_is_the_leading_reading_and_the_watermark_leads(self):
        """The order is the contract, not a preference: the percentage against
        its tier comes before the stall measured behind it, because the second
        is context for the first and a face that led with context would be
        answering the question one step early."""
        alert = {"code": "SWAP_PRESSURE", "measured": self.SWAP}
        readings = [p["axis"] for p in alerting.measurement_parts(alert, self.SWAP)
                    if p["measured"] and p["face"]]
        self.assertGreater(len(readings), 1, "one reading makes the order untested")
        self.assertEqual(alerting.measurement_face(alert, self.SWAP)["axis"],
                         readings[0])

    def test_the_folds_sentence_is_the_join_of_the_parts_own_words(self):
        """`_measurement_line` was the whole of this function once. It is the
        same sentence now, and it has to stay the same sentence: the fold is
        what the contract's readers check the face against.

        Every case here is one where the parts list holds something the face
        does not print — the declaration row is all non-readings, the caveated
        one carries a part the face skips. A join that had quietly started
        keeping only the measured parts would still pass on three readings.
        """
        caveated = dict(self.DECLARATION, caveat="behind a boundary")
        for code, measured in (("DISK_CRITICAL", self.DISK),
                               ("SWAP_PRESSURE", self.SWAP),
                               ("DESIRED_ACTUAL_MISMATCH", self.DECLARATION),
                               ("DESIRED_ACTUAL_MISMATCH", caveated),
                               ("FUNCTIONAL_PROBE_FAILED", {})):
            with self.subTest(code=code, caveat=bool(measured.get("caveat"))):
                alert = {"code": code, "measured": measured, "why": ""}
                parts = alerting.measurement_parts(alert, measured)
                self.assertEqual(
                    alerting._measurement_line(alert, measured),
                    ", ".join(p["says"] for p in parts))
                if not any(p["measured"] for p in parts):
                    self.assertTrue(parts, "a code with no parts prints nothing")

    def test_a_declaration_has_no_face_and_still_has_its_sentence(self):
        """Both halves. A code that measures nothing must not take the face —
        a card printing 「本代码是一个声明」 where the other cards print a number
        is answering a question nobody asked, in the place the answer to a real
        one goes — and the fold must still say why there is no number, or the
        reader is left with a blank."""
        alert = {"code": "FUNCTIONAL_PROBE_FAILED", "measured": {}, "why": ""}
        self.assertIsNone(alerting.measurement_face(alert, {}))
        self.assertIn("no measurement", alerting._measurement_line(alert, {}))

    def test_a_caveat_never_takes_the_number_off_the_face(self):
        """§15's qualifier is a sentence ABOUT a reading, not a reading. If it
        could take the face, the card would print the hedge where the figure
        goes — and the figure it hedges would be nowhere on the first screen."""
        measured = dict(self.DECLARATION)
        measured["caveat"] = ("the process may be running behind a boundary "
                              "this uid cannot cross")
        alert = {"code": "DESIRED_ACTUAL_MISMATCH", "measured": measured}
        self.assertEqual(alerting.measurement_face(alert, measured)["axis"],
                         "desired_state")
        self.assertIn("caveat:", alerting._measurement_line(alert, measured))


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
        it is asserted rather than trusted.

        The set is DERIVED, and that is the repair. It used to be a hand-written
        list of the resource layer's keys, which is a list a person has to
        remember to extend, and the memory-pressure split walked straight into
        it: `psi_memory_some_avg60_percent_high` became a code's threshold, its
        margin key became derivable, and the list that exists to notice exactly
        that did not mention it. Reading the two code-to-key tables is the same
        assertion without the memory requirement.
        """
        declared = alerting._thresholds()
        graded = set(anomaly.THRESHOLD_KEYS.values()) | {
            key for key in resource_anomaly._THRESHOLDS.values() if key
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
        # The state reached the FILES, not only the object in memory — and
        # since 2026-10-04 that is two files, split by role. Asserting the
        # union alone would pass while every row sat in the hot file, which is
        # the whole cost this split exists to remove, so each file is checked
        # for the rows it is supposed to hold and the union for completeness.
        on_disk = json.loads(self.store.path.read_text(encoding="utf-8"))
        ledger = json.loads(self.store.ledger_path.read_text(encoding="utf-8"))
        self.assertEqual([row["state"] for row in on_disk["alerts"].values()],
                         ["OPEN"])
        self.assertEqual([row["state"] for row in ledger["alerts"].values()],
                         ["RESOLVED"])
        self.assertEqual(sorted(ledger["alerts"]) + sorted(on_disk["alerts"]),
                         sorted(row["alert_ref"] for row in listing["alerts"]))


class TheRegisterIsSplitByRoleAndNotByAge(AlertCase):
    """The hot file carries what a pass may still act on; the ledger carries
    every episode that has resolved. Nothing is dropped for being old, so this
    class has to show the split is about ROLE: the saving is a file that was
    not written, and the equivalence is a query that answers the same."""

    def hot(self) -> dict:
        return json.loads(self.store.path.read_text(encoding="utf-8"))["alerts"]

    def cold(self) -> dict:
        return json.loads(self.store.ledger_path.read_text(encoding="utf-8"))["alerts"]

    def resolve(self):
        """One open alert, then the tick that closes it. Returns its ref."""
        self.disk(used=93.0)
        ref = self.only()["alert_ref"]
        self.disk(seconds=60, used=50.0, severity=None)
        self.assertEqual(self.only()["state"], "RESOLVED")
        return ref

    def test_a_routine_pass_does_not_touch_the_ledger(self):
        """The whole of the saving, stated as a file that was not written.
        Inode and `st_mtime_ns` together: a rewrite landing inside one
        filesystem timestamp tick would otherwise read as "untouched"."""
        self.resolve()
        before = self.store.ledger_path.stat()
        self.disk(seconds=120, used=94.0)
        after = self.store.ledger_path.stat()
        self.assertEqual((before.st_ino, before.st_mtime_ns),
                         (after.st_ino, after.st_mtime_ns))
        self.assertEqual(self.only()["state"], "OPEN")

    def test_the_ledger_keeps_the_episode_after_the_identity_reopens(self):
        """The ledger only ever gains. A row that re-opens is still a row that
        resolved; deleting it would make the ledger a second opinion about the
        present rather than the record of what is over."""
        ref = self.resolve()
        self.disk(seconds=120, used=94.0)
        self.assertEqual(self.hot()[ref]["state"], "OPEN")
        self.assertEqual(self.cold()[ref]["state"], "RESOLVED")

    def test_a_recovery_announcement_reaches_the_ledger_copy(self):
        """`record_delivery` stamps `last_notified_at` on whichever ref it was
        given, and for a recovery that ref is RESOLVED. The stamp has to land
        in the ledger, because that is the copy a later query reads."""
        self.disk(used=93.0)
        self.deliver(seconds=30)
        self.disk(seconds=60, used=50.0, severity=None)
        self.deliver(seconds=90, kind="resolved")
        self.assertEqual(self.cold()[self.only()["alert_ref"]]["last_notified_at"],
                         at(90))

    def test_a_register_written_before_the_split_loads_to_the_same_rows(self):
        """Migration equivalence by construction rather than by a migration
        step: an old single-file register is read as the union, so a caller
        sees the rows it saw before, and the split happens on the first
        ordinary commit with no window in which a row is in neither file."""
        # Two identities, so that each file has a row to be wrong about: one
        # that stays open and one that closes.
        self.disk(severity="low")
        self.disk(seconds=60, used=94.0, subject=FS_DATA, severity="high")
        self.disk(seconds=120, used=50.0, severity=None)
        whole = self.store.alerts()
        self.assertEqual(sorted(r["state"] for r in whole.values()),
                         ["OPEN", "RESOLVED"])
        self.store.ledger_path.unlink()
        self.store.path.write_text(
            json.dumps({"schema": alerting.SCHEMA, "alerts": whole}),
            encoding="utf-8")

        reopened = alerting.AlertStore(self.store.path)
        self.assertEqual(reopened.alerts(), whole)
        reopened.commit(reopened.alerts())
        self.assertEqual(reopened.alerts(), whole)
        self.assertEqual([r["state"] for r in self.hot().values()], ["OPEN"])
        self.assertEqual([r["state"] for r in self.cold().values()], ["RESOLVED"])
        self.assertEqual({**self.cold(), **self.hot()}, whole)

    def test_a_row_left_in_both_files_resolves_to_the_live_one(self):
        """The crash residue. The ledger is written first, so an interruption
        before the hot write leaves a resolved row in the ledger under a hot
        copy that still says OPEN. The hot copy wins: reading it the other way
        would show a row as RESOLVED while it is still firing, which is a
        person not being told, and the next pass resolves it again anyway."""
        ref = self.resolve()
        ledger = self.cold()
        self.store.path.write_text(json.dumps({
            "schema": alerting.SCHEMA,
            "alerts": {ref: {**ledger[ref], "state": "OPEN", "resolved_at": None}},
        }), encoding="utf-8")
        fresh = alerting.AlertStore(self.store.path)
        self.assertEqual(fresh.alerts()[ref]["state"], "OPEN")

    def test_a_query_against_two_files_answers_what_one_file_answered(self):
        """`listing` reads `alerts()` and nothing else, so this is the claim
        that the union — not the hot file — is what every reader meets."""
        self.resolve()
        fresh = alerting.AlertStore(self.store.path)
        listing = alerting.listing(fresh, at=at(60))
        self.assertEqual([row["state"] for row in listing["alerts"]],
                         ["RESOLVED"])
        self.assertEqual(listing["by_state"]["RESOLVED"], 1)
        summary = fresh.summary()
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["ledger_rows"], 1)


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


"""Appended to tests/test_alerting.py by §F7.

The output of `listing()` was never wrong; its COST was the defect. A test
that only compared rows would have passed against the broken version, so this
one counts reads of the contract document instead of comparing output.
"""
class ListingReadsTheContractOnce(unittest.TestCase):
    """`alert list` has to be cheaper than the cap on asking for it.

    `listing()` renders one row per alert and the severity word on each row
    comes from the contract document. Reading that document per row made a
    612-row register take 6.5 s to list on this node, past the 5.0 s cap the
    CLI puts on a daemon request -- so `serverosctl alert list` answered
    "timed out" and §7's on-demand detail path (`alert inspect`) had no way to
    learn a ref. The word does not depend on the row, so the number of reads
    must not depend on the number of rows.
    """

    class _Register:
        """Just enough store for `listing()`: it calls `.alerts()` and nothing else."""

        def __init__(self, rows):
            self._rows = rows

        def alerts(self):
            return self._rows

    def _rows(self, count):
        return {
            "alert:%04d" % index: {
                "alert_ref": "alert:%04d" % index,
                "code": "ORPHAN_PROCESS_TREE",
                "severity": "high",
                "state": "OPEN",
                "device_ref": "device:test",
                "subject_ref": "subject:%04d" % index,
                "first_seen": "2026-01-01T00:00:00+00:00",
                "occurrence_count": 1,
            }
            for index in range(count)
        }

    def _reads(self, count):
        """How many times `policy()` was consulted to list `count` alerts."""
        calls = []
        real = alerting.policy

        def counting(*args, **kwargs):
            calls.append(1)
            return real(*args, **kwargs)

        with mock.patch.object(alerting, "policy", counting):
            result = alerting.listing(self._Register(self._rows(count)),
                                      at="2026-01-01T00:00:10+00:00")
        return len(calls), result

    def test_the_number_of_reads_does_not_grow_with_the_register(self):
        small, _ = self._reads(2)
        large, _ = self._reads(400)
        self.assertEqual(
            small, large,
            "listing() consulted the contract %d times for 2 alerts and %d "
            "times for 400; the word it renders is the same for every row, so "
            "a per-row read is a per-row file parse" % (small, large))

    def test_a_listing_does_not_parse_the_contract_per_alert(self):
        large, _ = self._reads(400)
        self.assertLess(
            large, 10,
            "400 alerts cost %d reads of the contract document" % large)

    def test_the_row_still_carries_the_word_the_contract_declares(self):
        """The hoist must not have been paid for by dropping the field."""
        _, result = self._reads(3)
        words = alerting.severity_words()
        for row in result["alerts"]:
            self.assertEqual(row["severity_word"], words["high"])
        self.assertEqual(result["count"], 3)
        self.assertEqual(result["by_state"]["OPEN"], 3)


# ---------------------------------------------------------------------------
# §7 the owner is on the sentence
# ---------------------------------------------------------------------------


class TheOwnerRidesOnTheSentence(unittest.TestCase):
    """§7. The sentence a person is sent has to name the owner it measured.

    The record has carried `measured.ownership` -- role, uid, session, and the
    cgroup clause that produced them -- since the round that fixed attribution,
    and `detail_for` read none of it. So the one line a person received for an
    ORPHAN_PROCESS_TREE said that a tree had outlived its parent and never said
    whose, which is both the field §7 names and the difference between a
    finding its reader can act on and one they can only worry about.

    None of these tests assert a word the contract does not already carry.
    `EXTERNAL_USER_SESSION` and `OBSERVE_ONLY` are the producer's own
    vocabulary, and the assertion that they survive to the sentence is the
    whole claim.
    """

    OWNED = {
        "code": "ORPHAN_PROCESS_TREE",
        "severity": "high",
        "state": "OPEN",
        "first_seen": "2026-09-16T03:07:18+00:00",
        "occurrence_count": 1836,
        "window": "lineage at observation time",
        "provenance": {"observed_at": "2026-09-17T09:59:11+00:00",
                       "freshness": None, "source": "serverosd"},
        "measured": {"root_ppid": 1, "ownership": {
            "class": "EXTERNAL",
            "role": "EXTERNAL_USER_SESSION",
            "lifecycle": "OBSERVE_ONLY",
            "provenance": "cgroup",
            "uid": "1001",
            "session": "134",
            "unit": None,
        }},
    }

    def rendered(self, alert=None) -> str:
        return alerting.detail_for(alert or self.OWNED, at=T0)

    def test_the_role_the_uid_and_the_session_all_reach_the_sentence(self):
        """The three clauses the cgroup was read for, on the one line."""
        self.assertIn(
            "owner: EXTERNAL_USER_SESSION · uid 1001 · 134 · "
            "lifecycle OBSERVE_ONLY", self.rendered())

    def test_the_lifecycle_that_says_nothing_here_may_act_is_on_that_line(self):
        """§15. OBSERVE_ONLY is the permission, and it is the reason a reader
        who has been told whose tree this is must not conclude they may touch
        it. A line that named the owner and dropped the permission would be an
        invitation rather than a report."""
        self.assertIn("lifecycle OBSERVE_ONLY", self.rendered())

    def test_an_alert_nothing_was_attributed_to_gains_no_owner_line(self):
        """The clause is `when the cgroup names one`. An alert whose measurement
        carries no ownership has no owner to state, and printing an empty one
        would be inventing a reading the probe never took."""
        alert = dict(self.OWNED, measured={"root_ppid": 1})
        self.assertNotIn("owner:", self.rendered(alert))

    def test_a_clause_the_probe_did_not_record_reads_as_an_absence(self):
        """uid with no session, no unit and no slice. The line says so.

        Dropping the empty clause instead would make "the probe recorded no
        session" and "this formatter chose not to print one" the same string,
        and only one of those is a fact about the node."""
        alert = dict(self.OWNED)
        alert["measured"] = {"root_ppid": 1, "ownership": {
            "class": "EXTERNAL", "role": "EXTERNAL_UNSLICED",
            "lifecycle": "OBSERVE_ONLY", "uid": "1001",
            "session": None, "unit": None, "slice": None}}
        self.assertIn("no session or unit recorded", self.rendered(alert))

    def test_an_ownership_block_with_no_lifecycle_says_not_stated(self):
        """The contract has a closed set for lifecycle and this formatter does
        not get to guess a member of it. An answer the producer did not give is
        reported as one it did not give."""
        alert = dict(self.OWNED)
        alert["measured"] = {"root_ppid": 1, "ownership": {
            "class": "UNKNOWN", "role": None, "lifecycle": None, "uid": None,
            "session": None}}
        rendered = self.rendered(alert)
        self.assertIn("unattributed", rendered)
        self.assertIn("lifecycle not stated", rendered)

    def test_the_contract_declares_the_field_this_code_emits(self):
        """The join between the two files, and the reason the edit touched both.

        The notification contract declares the shape of this block. A field
        added here and not there leaves the document that owns the shape
        disagreeing with the code that renders it, and the next reader cannot
        tell which of the two is the stale one."""
        import yaml
        contract = Path(alerting.__file__).resolve().parent / "notification-contract.yaml"
        shape = yaml.safe_load(contract.read_text(encoding="utf-8"))
        declared = shape["alerts"]["text"]["detail_shape"]
        self.assertTrue(any("owner" in str(item) for item in declared),
                        f"detail_shape does not declare an owner line: {declared}")
