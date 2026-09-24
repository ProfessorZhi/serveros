#!/usr/bin/env python3
"""§Q2 — several alerts, one message, and the sentences that must stay false.

WHAT EACH SECTION KEEPS FALSE
-----------------------------
    baseline    "this just happened" (a week-old backlog announced as news)
    deltas      "nothing changed" / "everything changed" (the four kinds)
    dedupe      "it happened twice" (two sightings of one identity, one row)
    severity    "this is urgent" (LOW reaching an out-of-band send)
    cadence     "say it again" (a quiet hour producing a card) and
                "wait for the interval" (a CRITICAL held behind an hourly clock)
    two axes    "there is something to report" (a card caused by a finding the
                card cannot print — the failure that sent an empty-faced card
                once an hour on this node, through the out-of-band door)
    record      "you were told" (a composed digest marked announced by composing)
    contract    "the numbers live in the module" (the join below fails if they do)
    shape       "this is a detector" (it decides nothing the register did not)

and the last section reads the module's own source to prove it holds no
transport, no subprocess and no path to the engine's delivery hop. §Q2 forbids
`alert → agent reasoning → Feishu` as the normal path, and the way that shape
comes back is a helper nobody re-read.

WHY THE NUMBERS ARE LITERALS HERE
---------------------------------
The interval, the top-N and the severity split below are written out rather
than read from the contract they come from. A fixture that reads the policy it
is testing asserts only that the policy equals itself — set `top_n` to zero and
a test that read it would still pass. So the numbers are literals, and
`TheContractOwnsTheNumbers` is the join that fails if they stop matching.
"""
from __future__ import annotations

import ast
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent

DEVICE = "device:tokyo-shared-dev-01"
FS = "filesystem:/"
GROUP = "runtime-group:process-tree:codex-session:abc"
MEM = "host:memory"

#: The code the fixture's alerts carry unless a test names one.
#:
#: WHY THIS IS A REAL CODE AND USED TO NOT BE. This fixture used to pass the
#: SUBJECT in the code position (`code=FS`, i.e. `code="filesystem:/"`), which
#: is not a code any detector raises — `filesystem:/` is not in
#: `anomaly.SEVERITY` or `resource_anomaly.CODES` at all. Nothing noticed,
#: because `decision` read severity alone and an unrecognised code was simply
#: carried through. It is not carried through any more: `decision` asks
#: `presentation` whether a finding may occupy a LINE on the card, and an
#: undecided code answers NO — `presentation.alert_model` counts those in its
#: own `undecided` bucket rather than printing them, so a card caused by one
#: would be a card with an empty face. That is the shape this round exists to
#: stop, which means the fixture has to put register rows in the register:
#: `DISK_CRITICAL` is the boundary-registration code for a filesystem subject,
#: it is decided by `alerts.attention.codes`, and it is on the face.
#:
#: `CPU_SATURATED` went the same way for the same reason — it is not a code
#: either, and the real spelling of what it meant is `HIGH_CPU_SUSTAINED`.
CODE = "DISK_CRITICAL"
CPU_CODE = "HIGH_CPU_SUSTAINED"

# -- the declared numbers, as literals --------------------------------------
INTERVAL = 3600
TOP_N = 5
IMMEDIATE = ("high",)
INCLUDED = ("high", "medium", "low")
# §27. Which severities may make a card EXIST once the interval has elapsed,
# as opposed to which may preempt it. Literals for the same reason as the rest:
# a fixture that reads the policy it is testing asserts only that the policy
# equals itself.
PROACTIVE = ("high", "medium")


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


digest = _load("serveros_runtime_alert_digest",
               ROOT / "runtime" / "alert_digest.py")
alerting = _load("serveros_runtime_alerting", ROOT / "runtime" / "alerting.py")


def iso(seconds: int) -> str:
    """A timestamp `graph.seconds_between` reads, offset from a fixed instant."""
    base = 1789000000 + seconds
    import datetime
    return datetime.datetime.fromtimestamp(
        base, datetime.timezone.utc).isoformat().replace("+00:00", "Z")


T0 = iso(0)
LATER = lambda s: iso(s)  # noqa: E731 - a fixture, and the name is the doc


def alert(ref: str, *, code=CODE, severity="high", state="OPEN", subject=FS,
          first_seen=T0, resolved_at=None, occurrence=1, device=DEVICE):
    return {
        "alert_ref": ref, "code": code, "severity": severity,
        "device_ref": device, "subject_ref": subject, "resource_ref": subject,
        "state": state, "first_seen": first_seen, "last_seen": first_seen,
        "resolved_at": resolved_at, "occurrence_count": occurrence,
        "detector": "measurement-anomaly",
    }


def ref_of(code: str, subject: str) -> str:
    """The ref the register itself would mint — not one this test invents.

    Built through `alerting.identity`/`alerting.alert_ref` on purpose: a test
    that typed a hex string would pass even if the store stopped deriving refs
    from code+device+subject, which is the whole of the dedupe key.
    """
    return alerting.alert_ref(alerting.identity(code=code, device_ref=DEVICE,
                                                subject_ref=subject))


class Fixture(unittest.TestCase):
    """A register and a cursor in a temporary directory, nothing else."""

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="q2-test-"))
        self.state = self.dir / "alerts" / "state.json"
        self.state.parent.mkdir(parents=True, exist_ok=True)
        self.store = alerting.AlertStore(self.state)
        self.cursor = digest.DeliveryCursor(digest.cursor_path(self.state))

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    # -- fixture helpers --------------------------------------------------

    def put(self, *rows):
        alerts = self.store.alerts()
        for row in rows:
            alerts[row["alert_ref"]] = row
        self.store.commit(alerts)
        return self

    def baseline(self, at=T0):
        return digest.ensure_baseline(self.cursor, self.store, at=at,
                                      device_ref=DEVICE)

    def compose(self, at=T0):
        return digest.compose(self.store, self.cursor, at=at, device_ref=DEVICE)

    def kinds(self, document):
        out = {}
        for row in document["rows"]:
            out.setdefault(row["kind"], []).append(row["key"])
        return out


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------


class TheBaselineIsNotTheEvent(Fixture):
    def test_an_alert_that_predates_the_ledger_is_never_announced_as_new(self):
        self.put(alert(ref_of(CODE, FS), first_seen=iso(-86400)))
        taken = self.baseline()
        self.assertTrue(taken["taken"])
        self.assertEqual(taken["baseline_keys"], 1)
        document = self.compose(at=LATER(60))
        self.assertEqual(document["counts"]["NEW"], 0)
        self.assertEqual(document["backlog"], 1)
        self.assertEqual(document["standing"]["OPEN"], 1)
        self.assertIn("OPEN", document["standing"])

    def test_taking_the_baseline_twice_changes_nothing(self):
        self.put(alert(ref_of(CODE, FS), first_seen=iso(-86400)))
        first = self.baseline(at=T0)
        second = self.baseline(at=LATER(600))
        self.assertTrue(first["taken"])
        self.assertFalse(second["taken"])
        self.assertEqual(second["baseline_at"], T0)
        # And a real delta that arrived in between is still a delta: a
        # re-baseline would have swallowed it.
        self.put(alert(ref_of(CODE, GROUP), code="HIGH_MEMORY_SUSTAINED",
                       severity="medium", subject=GROUP, first_seen=LATER(300)))
        document = self.compose(at=LATER(600))
        self.assertEqual(document["counts"]["NEW"], 1)

    def test_a_delta_composed_without_a_baseline_is_refused(self):
        self.put(alert(ref_of(CODE, FS)))
        with self.assertRaises(digest.DigestError) as caught:
            self.compose()
        self.assertIn("baseline", str(caught.exception))

    def test_an_unreadable_cursor_is_refused_rather_than_emptied(self):
        self.baseline()
        self.cursor.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(digest.DigestError) as caught:
            digest.DeliveryCursor(self.cursor.path)
        self.assertIn("Refusing to start with an empty delivery cursor",
                      str(caught.exception))

    def test_the_closure_of_a_never_announced_alert_is_not_announced_either(self):
        """A person cannot be told a thing recovered when they were never told
        it was broken. With a backlog that is the difference between a card and
        a wall of ticks."""
        self.put(alert(ref_of(CODE, FS), first_seen=iso(-86400)))
        self.baseline()
        self.put(alert(ref_of(CODE, FS), first_seen=iso(-86400), state="RESOLVED",
                       resolved_at=LATER(60)))
        document = self.compose(at=LATER(120))
        self.assertEqual(document["counts"]["RECOVERED"], 0)
        self.assertEqual(document["silent_resolutions"], 1)

    def test_an_alert_that_opened_and_closed_since_the_baseline_is_reported(self):
        """The opposite case, and the reason the rule is not simply 'never
        announce an unannounced recovery': a CRITICAL that came and went inside
        one interval is the only chance anybody gets to hear about it."""
        self.put(alert(ref_of(CODE, FS), first_seen=iso(-86400)))
        self.baseline()
        self.put(alert(ref_of(CODE, GROUP), code=CPU_CODE, subject=GROUP,
                       first_seen=LATER(60), state="RESOLVED",
                       resolved_at=LATER(90)))
        document = self.compose(at=LATER(120))
        self.assertEqual(document["counts"]["RECOVERED"], 1)
        self.assertFalse(document["rows"][0]["was_announced"])


# ---------------------------------------------------------------------------
# the four deltas
# ---------------------------------------------------------------------------


class TheFourDeltas(Fixture):
    def test_a_new_identity_is_NEW(self):
        self.baseline()
        self.put(alert(ref_of(CODE, GROUP), code=CPU_CODE, subject=GROUP,
                       first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        self.assertEqual(document["counts"]["NEW"], 1)

    def test_a_severity_that_went_up_is_ESCALATED_and_a_downgrade_is_nothing(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, severity="medium", first_seen=LATER(60)))
        self.assertEqual(self.by_kind(at=LATER(120)), {"NEW": 1})
        digest.record(self.cursor, self.store, at=LATER(130),
                      generation=1, state="DELIVERED", shown=self.shown(at=LATER(120)))
        self.put(alert(ref, severity="high", first_seen=LATER(60)))
        self.assertEqual(self.by_kind(at=LATER(200)), {"ESCALATED": 1})
        # Back down again: the alert is still open and still visible, and the
        # message about it is not worth spending.
        digest.record(self.cursor, self.store, at=LATER(210), generation=2,
                      state="DELIVERED", shown=self.shown(at=LATER(200)))
        self.put(alert(ref, severity="medium", first_seen=LATER(60)))
        self.assertEqual(self.by_kind(at=LATER(300)), {})

    def test_a_resolved_alert_the_person_was_told_about_is_RECOVERED(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=self.shown(at=LATER(120)))
        self.put(alert(ref, first_seen=LATER(60), state="RESOLVED",
                       resolved_at=LATER(200)))
        self.assertEqual(self.by_kind(at=LATER(300)), {"RECOVERED": 1})

    def test_a_resolved_alert_that_fires_again_is_REOPENED_not_NEW(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=self.shown(at=LATER(120)))
        self.put(alert(ref, first_seen=LATER(60), state="RESOLVED",
                       resolved_at=LATER(200)))
        digest.record(self.cursor, self.store, at=LATER(300), generation=2,
                      state="DELIVERED", shown=self.shown(at=LATER(300)))
        self.put(alert(ref, first_seen=LATER(400)))
        self.assertEqual(self.by_kind(at=LATER(500)), {"REOPENED": 1})

    def test_an_alert_a_person_acknowledged_is_not_a_delta(self):
        """ACKNOWLEDGED and SNOOZED are what a person did. The identity was
        announced, the condition has not changed, and a card that re-announced
        somebody's own acknowledgement back at them is the system talking to
        itself."""
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=self.shown(at=LATER(120)))
        self.put(alert(ref, first_seen=LATER(60), state="ACKNOWLEDGED"))
        self.assertEqual(self.by_kind(at=LATER(300)), {})
        self.put(alert(ref, first_seen=LATER(60), state="SNOOZED"))
        self.assertEqual(self.by_kind(at=LATER(400)), {})

    def test_an_open_alert_that_was_already_told_and_did_not_change_is_nothing(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=self.shown(at=LATER(120)))
        self.assertEqual(self.by_kind(at=LATER(600)), {})

    # -- helpers ----------------------------------------------------------

    def by_kind(self, *, at):
        return {kind: len(keys)
                for kind, keys in self.kinds(self.compose(at=at)).items()}

    def shown(self, *, at):
        return self.compose(at=at)["shown"]


# ---------------------------------------------------------------------------
# dedupe
# ---------------------------------------------------------------------------


class OneIdentityOneRow(Fixture):
    def test_the_dedupe_key_is_the_alerts_own_ref(self):
        """Not a second key derived here. Two systems that disagree about
        whether two sightings are the same alert is how a person is told
        twice."""
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60), occurrence=9))
        document = self.compose(at=LATER(120))
        self.assertEqual([row["key"] for row in document["rows"]], [ref])
        self.assertEqual(document["rows"][0]["key"],
                         self.store.get(ref)["alert_ref"])

    def test_two_subjects_on_one_device_are_two_rows(self):
        self.baseline()
        self.put(alert(ref_of(CODE, FS), subject=FS, first_seen=LATER(60)),
                 alert(ref_of(CODE, "/data"), subject="/data", first_seen=LATER(61)))
        document = self.compose(at=LATER(120))
        self.assertEqual(document["counts"]["NEW"], 2)
        self.assertEqual(len({row["key"] for row in document["rows"]}), 2)

    def test_the_same_subject_under_two_codes_is_two_rows(self):
        self.baseline()
        self.put(alert(ref_of(CODE, FS), code=CODE, subject=FS, first_seen=LATER(60)),
                 alert(ref_of(CPU_CODE, FS), code=CPU_CODE,
                       subject=FS, first_seen=LATER(60)))
        self.assertEqual(self.compose(at=LATER(120))["counts"]["NEW"], 2)


# ---------------------------------------------------------------------------
# severity filter
# ---------------------------------------------------------------------------


class TheSeveritySplit(Fixture):
    def test_only_a_declared_immediate_severity_may_go_out_of_band(self):
        self.baseline()
        self.put(alert(ref_of(CODE, GROUP), code="HIGH_MEMORY_SUSTAINED",
                       severity="medium", subject=GROUP, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        self.assertEqual(len(document["rows"]), 1)
        self.assertFalse(document["rows"][0]["immediate"])
        self.assertEqual(document["immediate"], [])

    def test_a_low_finding_is_in_the_card_and_never_immediate(self):
        self.baseline()
        self.put(alert(ref_of(CODE, GROUP), code="AGE_WITHOUT_ACTIVITY",
                       severity="low", subject=GROUP, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        self.assertEqual(document["counts"]["NEW"], 1)
        self.assertFalse(document["rows"][0]["immediate"])

    def test_high_is_immediate(self):
        self.baseline()
        self.put(alert(ref_of(CODE, FS), severity="high", first_seen=LATER(60)))
        self.assertTrue(self.compose(at=LATER(120))["rows"][0]["immediate"])

    def test_a_recovery_is_never_immediate_however_severe_it_was(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=self.compose(at=LATER(120))["shown"])
        self.put(alert(ref, first_seen=LATER(60), state="RESOLVED",
                       resolved_at=LATER(200)))
        self.assertFalse(self.compose(at=LATER(300))["rows"][0]["immediate"])

    def test_the_document_keeps_every_row_and_counts_the_remainder(self):
        self.baseline()
        for index in range(TOP_N + 3):
            subject = f"filesystem:/mnt/vol{index}"
            self.put(alert(ref_of(CODE, subject), subject=subject,
                           first_seen=LATER(60 + index)))
        document = self.compose(at=LATER(120))
        self.assertEqual(len(document["rows"]), TOP_N + 3)
        self.assertEqual(len(document["shown"]), TOP_N)
        self.assertEqual(document["more"], 3)
        self.assertEqual(len(document["shown_keys"]), TOP_N)


# ---------------------------------------------------------------------------
# cadence
# ---------------------------------------------------------------------------


class WhenItIsWorthAMessage(Fixture):
    def test_a_quiet_hour_produces_no_card(self):
        self.baseline()
        verdict = digest.decision(self.compose(at=LATER(60)), self.cursor,
                                  at=LATER(60))
        self.assertFalse(verdict["send"])
        self.assertIn("changed", verdict["why"])

    def test_a_critical_does_not_wait_for_the_interval(self):
        self.baseline()
        self.put(alert(ref_of(CODE, FS), severity="high", first_seen=LATER(60)))
        verdict = digest.decision(self.compose(at=LATER(60)), self.cursor,
                                  at=LATER(60))
        self.assertTrue(verdict["send"])
        self.assertTrue(verdict["immediate"])

    def test_an_hour_of_nothing_but_recoveries_is_not_a_card(self):
        """§27. The interval is a clock, not a reason.

        Watched in production: the only news in an hour was a batch of
        recoveries and the card still went out, full height, every hour. A
        person who is told "32 things recovered" when they were never told
        those 32 things were broken learns to skip the card, and the card is
        the only surface a real finding has.
        """
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        first = self.compose(at=LATER(120))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=first["shown"])
        self.put(alert(ref, first_seen=LATER(60), state="RESOLVED",
                       resolved_at=LATER(200)))
        late = self.compose(at=LATER(120 + INTERVAL + 1))
        self.assertEqual(late["counts"]["RECOVERED"], 1)
        verdict = digest.decision(late, self.cursor,
                                  at=LATER(120 + INTERVAL + 1))
        self.assertFalse(verdict["send"])
        self.assertIn("recoveries", verdict["why"])

    def test_an_hour_of_nothing_but_notices_is_not_a_card(self):
        """The same for LOW. A NOTICE is a line in a card somebody is already
        reading; on a quiet host it would otherwise be an hourly card forever."""
        self.baseline()
        self.put(alert(ref_of(CODE, GROUP), code="AGE_WITHOUT_ACTIVITY",
                       severity="low", subject=GROUP, first_seen=LATER(60)))
        late = self.compose(at=LATER(120 + INTERVAL + 1))
        self.assertEqual(late["counts"]["NEW"], 1)
        verdict = digest.decision(late, self.cursor,
                                  at=LATER(120 + INTERVAL + 1))
        self.assertFalse(verdict["send"])
        self.assertIn("none at", verdict["why"])

    def test_a_warning_still_earns_the_interval(self):
        """The half that keeps this from being a silencer. A medium finding is
        not immediate and must not preempt the hour — and must still be said
        when the hour arrives. Narrowing the gate to HIGH alone would have
        passed the two tests above and gone quiet on every WARNING."""
        self.baseline()
        self.put(alert(ref_of(CODE, FS), severity="medium", first_seen=LATER(60)))
        at = LATER(120 + INTERVAL + 1)
        verdict = digest.decision(self.compose(at=at), self.cursor, at=at)
        self.assertTrue(verdict["send"])
        self.assertFalse(verdict["immediate"])

    def test_a_measured_noise_finding_does_not_preempt_the_interval(self):
        """THE FAILURE THIS ROUND EXISTS FOR, in the shape the register had it.

        A high `HIGH_CPU_SUSTAINED` is `immediate` by severity and is a
        `consumption` code, which the contract carries at WATCH — so the card it
        would preempt the hour to send cannot print it, and the reader gets a
        card whose face is empty. Measured on this node 2026-09-24: EVERY one of
        the four most recent cards arrived less than INTERVAL after the one
        before it, so the out-of-band door, not the interval, was the door that
        was firing. Reading severity alone is what let it.

        Both axes are asserted here, because the whole claim is that they are
        read together: the document still says `immediate`, and the verdict
        still says no.
        """
        self.baseline()
        self.put(alert(ref_of(CPU_CODE, GROUP), code=CPU_CODE, severity="high",
                       subject=GROUP, first_seen=LATER(60)))
        document = self.compose(at=LATER(60))
        self.assertTrue(document["rows"][0]["immediate"])
        verdict = digest.decision(document, self.cursor, at=LATER(60))
        self.assertFalse(verdict["send"])
        self.assertEqual(verdict["immediate"], [])
        self.assertIn("may occupy a line", verdict["why"])

    def test_a_measured_noise_finding_does_not_earn_the_interval_either(self):
        """The same row at the hour, so the out-of-band door is not the whole
        of it: an interval card caused by a finding it cannot print says the
        same nothing, only later."""
        self.baseline()
        self.put(alert(ref_of(CPU_CODE, GROUP), code=CPU_CODE, severity="high",
                       subject=GROUP, first_seen=LATER(60)))
        at = LATER(120 + INTERVAL + 1)
        verdict = digest.decision(self.compose(at=at), self.cursor, at=at)
        self.assertFalse(verdict["send"])
        self.assertIn("may occupy a line", verdict["why"])

    def test_a_recovery_rides_along_in_a_card_it_did_not_create(self):
        """§26. A RECOVERED is not suppressed — it appears in a card that
        exists for another reason. What it may never be is the reason."""
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        first = self.compose(at=LATER(120))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=first["shown"])
        self.put(alert(ref, first_seen=LATER(60), state="RESOLVED",
                       resolved_at=LATER(200)))
        # THE OTHER REASON HAS TO BE ONE THE CARD WOULD PRINT. This fixture
        # used to make the card exist with a `HIGH_MEMORY_SUSTAINED` at medium
        # — a `consumption` code, which the contract carries at WATCH and the
        # renderer COUNTS instead of printing. Under the old severity-only gate
        # that produced a card whose face was empty, so this test was asserting
        # the defect: "a card exists for another reason" where the other reason
        # was a row that could not appear on it. The claim it is making is
        # unchanged and is the one worth keeping — the RECOVERED rides along and
        # is not the cause — so the fixture supplies a reason that reaches the
        # face.
        other = ref_of(CODE, "/data")
        self.put(alert(other, code=CODE, severity="medium", subject="/data",
                       first_seen=LATER(200)))
        at = LATER(120 + INTERVAL + 1)
        document = self.compose(at=at)
        self.assertIn("RECOVERED", self.kinds(document))
        verdict = digest.decision(document, self.cursor, at=at)
        self.assertTrue(verdict["send"])
        # 两个 finding 里只有一个够格 —— 另一个正是那条恢复。这就是 §26 的形状：
        # 恢复跟着走，但它不是这张卡存在的原因。
        self.assertIn("1 of 2", verdict["why"])

    def test_a_non_immediate_delta_waits_for_the_interval(self):
        """The first digest goes out whenever it is composed — nobody has been
        told anything yet. After that the interval is what stops a medium
        finding from producing a card every time the timer fires."""
        self.baseline()
        self.put(alert(ref_of(CODE, GROUP), code="HIGH_MEMORY_SUSTAINED",
                       severity="medium", subject=GROUP, first_seen=LATER(60)))
        first = self.compose(at=LATER(120))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=first["shown"])
        self.put(alert(ref_of(CODE, "/data"), severity="medium", subject="/data",
                       first_seen=LATER(200)))
        early = self.compose(at=LATER(300))
        self.assertEqual(early["counts"]["NEW"], 1)
        self.assertFalse(digest.decision(early, self.cursor, at=LATER(300))["send"])
        late = self.compose(at=LATER(120 + INTERVAL + 1))
        self.assertTrue(digest.decision(late, self.cursor,
                                        at=LATER(120 + INTERVAL + 1))["send"])

    def test_an_immediate_retry_is_held_by_the_severity_cooldown(self):
        """§12's rule, applied to the batch: the clock is the last ATTEMPT, so
        a transport that is down does not go silent — and a transport that is
        up is not asked the same thing five times in a minute."""
        self.baseline()
        ref = ref_of(CODE, FS)
        self.put(alert(ref, severity="high", first_seen=LATER(60)))
        first = digest.decision(self.compose(at=LATER(60)), self.cursor,
                                at=LATER(60))
        digest.record(self.cursor, self.store, at=LATER(65),
                      generation=first["generation"], state="FAILED",
                      shown=self.compose(at=LATER(60))["shown"],
                      reason="transport down")
        held = digest.decision(self.compose(at=LATER(120)), self.cursor,
                               at=LATER(120))
        self.assertFalse(held["send"])
        self.assertIn("cooldown", held["why"])
        # The delta is still there — a failure does not consume it.
        self.assertEqual(self.compose(at=LATER(120))["counts"]["NEW"], 1)

    def test_the_idempotency_key_names_a_generation_and_not_a_clock(self):
        self.baseline()
        self.put(alert(ref_of(CODE, FS), severity="high", first_seen=LATER(60)))
        first = digest.decision(self.compose(at=LATER(60)), self.cursor,
                                at=LATER(60))
        self.assertEqual(first["generation"], 1)
        self.assertIn("-1", first["idempotency_key"])
        self.assertIn(DEVICE, first["idempotency_key"])
        again = digest.decision(self.compose(at=LATER(61)), self.cursor,
                                at=LATER(61))
        self.assertEqual(again["idempotency_key"], first["idempotency_key"])


# ---------------------------------------------------------------------------
# record
# ---------------------------------------------------------------------------


class WhatActuallyHappened(Fixture):
    def test_only_a_DELIVERED_attempt_advances_the_generation(self):
        self.baseline()
        self.put(alert(ref_of(CODE, FS), first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        verdict = digest.decision(document, self.cursor, at=LATER(120))
        for state in ("FAILED", "UNSUPPORTED", "UNKNOWN"):
            out = digest.record(self.cursor, self.store, at=LATER(130),
                                generation=verdict["generation"], state=state,
                                shown=document["shown"], reason="nope")
            self.assertFalse(out["delivered"])
            self.assertEqual(self.cursor.generation(), 0)
            self.assertFalse(self.cursor.announced(document["shown"][0]["key"]))
        out = digest.record(self.cursor, self.store, at=LATER(140),
                            generation=verdict["generation"], state="DELIVERED",
                            shown=document["shown"])
        self.assertTrue(out["delivered"])
        self.assertEqual(self.cursor.generation(), verdict["generation"])

    def test_a_delivered_digest_marks_exactly_what_was_on_the_card(self):
        self.baseline()
        for index in range(TOP_N + 2):
            subject = f"filesystem:/mnt/vol{index}"
            self.put(alert(ref_of(CODE, subject), subject=subject,
                           first_seen=LATER(60 + index)))
        document = self.compose(at=LATER(120))
        digest.record(self.cursor, self.store, at=LATER(130), generation=1,
                      state="DELIVERED", shown=document["shown"])
        shown = set(document["shown_keys"])
        everything = {row["key"] for row in document["rows"]}
        for key in shown:
            self.assertTrue(self.cursor.announced(key))
        for key in everything - shown:
            self.assertFalse(self.cursor.announced(key),
                             "a row that did not fit on the card was marked told")

    def test_the_register_is_stamped_so_the_register_does_not_contradict_it(self):
        """`alert list` reads `last_notified_at`. A person who has been told
        five digests about a disk must not find it null there."""
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        digest.record(self.cursor, self.store, at=LATER(130), generation=1,
                      state="DELIVERED", shown=document["shown"])
        stored = self.store.get(ref)
        self.assertEqual(stored["last_notified_at"], LATER(130))
        self.assertEqual(stored["last_notified_severity"], "high")
        self.assertEqual(stored["last_delivery"], "DELIVERED")
        self.assertEqual(stored["last_digest_generation"], 1)

    def test_the_engine_own_pending_intent_is_left_alone(self):
        """`pending` belongs to `alert sync`'s hop. A second surface clearing
        it would be one surface editing another's ledger — and that ledger is
        what makes the engine's silence explainable."""
        ref = ref_of(CODE, FS)
        alerts = {ref: alert(ref, first_seen=LATER(60))}
        alerts[ref]["pending"] = {"kind": "opened", "not_before": T0}
        self.store.commit(alerts)
        self.baseline()
        document = self.compose(at=LATER(120))
        digest.record(self.cursor, self.store, at=LATER(130), generation=1,
                      state="DELIVERED", shown=document["shown"])
        self.assertIsNotNone(self.store.get(ref)["pending"])

    def test_a_delivery_result_nobody_can_name_is_refused(self):
        self.baseline()
        with self.assertRaises(digest.DigestError):
            digest.record(self.cursor, self.store, at=LATER(10), generation=1,
                          state="PROBABLY", shown=[])

    def test_a_shown_row_the_register_no_longer_holds_is_still_recorded(self):
        self.baseline()
        ref = ref_of(CODE, FS)
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        alerts = self.store.alerts()
        del alerts[ref]
        self.store.commit(alerts)
        out = digest.record(self.cursor, self.store, at=LATER(130),
                            generation=1, state="DELIVERED",
                            shown=document["shown"])
        self.assertIn(ref, out["gone_from_register"])
        self.assertTrue(self.cursor.announced(ref))

    def test_a_diagnostic_may_stamp_a_bare_key_without_inventing_a_kind(self):
        self.baseline()
        ref = ref_of(CODE, FS)
        self.put(alert(ref, first_seen=LATER(60)))
        digest.record(self.cursor, self.store, at=LATER(130), generation=1,
                      state="DELIVERED", shown=[ref])
        self.assertTrue(self.cursor.announced(ref))
        self.assertIsNone(self.cursor.known(ref)["kind"])


# ---------------------------------------------------------------------------
# §4 · the receipt names the version that was sent
# ---------------------------------------------------------------------------


class TheReceiptNamesTheVersionThatWasSent(Fixture):
    """The register moves between composing and recording, and must not win.

    Every test here is the same shape: compose, change the world, then record.
    Before the frozen version the recorded severity and state were read off the
    register at record time, so each of these produced a ledger that described
    the world after the send instead of the card that was sent — and in the
    first three cases the difference is a state change that is then never
    announced to anybody, because the comparison that would find it has already
    been satisfied.
    """

    def deliver(self, document, *, generation, at=LATER(130)):
        return digest.record(self.cursor, self.store, at=at, generation=generation,
                             state="DELIVERED", shown=document["shown"],
                             frozen=document["frozen"])

    def test_a_send_time_escalation_stays_pending_and_is_announced_next(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, severity="medium", first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        self.assertEqual(document["shown"][0]["severity"], "medium")

        # The detector escalates while the transport is thinking.
        self.put(alert(ref, severity="high", first_seen=LATER(60)))
        out = self.deliver(document, generation=1)

        self.assertEqual(out["payload_version"], document["frozen"]["version"])
        self.assertEqual(self.cursor.known(ref)["severity"], "medium",
                         "the cursor recorded a severity the card never showed")
        self.assertEqual(self.store.get(ref)["last_notified_severity"], "medium",
                         "`alert list` now tells a person they were told HIGH")
        # B is a pending delta, not a consumed one.
        self.assertEqual(self.compose(at=LATER(200))["counts"]["ESCALATED"], 1)

    def test_a_send_time_resolution_becomes_a_recovery_and_not_a_silence(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))

        self.put(alert(ref, first_seen=LATER(60), state="RESOLVED",
                       resolved_at=LATER(125)))
        self.deliver(document, generation=1)

        self.assertEqual(self.cursor.known(ref)["state"], "OPEN",
                         "the cursor recorded a closure the card never showed")
        # Recording RESOLVED here is what would make the recovery silent
        # forever: the next delta compares against the recorded state.
        self.assertEqual(self.compose(at=LATER(200))["counts"]["RECOVERED"], 1)

    def test_a_send_time_reopen_is_REOPENED_and_not_swallowed(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        self.deliver(self.compose(at=LATER(120)), generation=1)

        self.put(alert(ref, first_seen=LATER(60), state="RESOLVED",
                       resolved_at=LATER(180)))
        recovered = self.compose(at=LATER(200))
        self.assertEqual(recovered["counts"]["RECOVERED"], 1)

        # It fires again before the recovery card is recorded as delivered.
        self.put(alert(ref, first_seen=LATER(210)))
        self.deliver(recovered, generation=2, at=LATER(220))

        self.assertEqual(self.cursor.known(ref)["state"], "RESOLVED",
                         "the cursor recorded the reopen instead of the card")
        self.assertEqual(self.compose(at=LATER(300))["counts"]["REOPENED"], 1)

    def test_a_timeout_the_provider_accepted_is_recovered_by_the_retry(self):
        """FAILED does not advance the generation, so the retry is the same
        document and the same idempotency key — which is what lets the
        transport collapse the two into one message."""
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        verdict = digest.decision(document, self.cursor, at=LATER(120))

        timed_out = digest.record(self.cursor, self.store, at=LATER(130),
                                  generation=verdict["generation"],
                                  state="FAILED", shown=document["shown"],
                                  frozen=document["frozen"], reason="timeout")
        self.assertFalse(timed_out["delivered"])
        self.assertIsNone(timed_out["payload_version"])
        self.assertIsNone(self.cursor.summary()["last_payload"],
                          "a failed attempt recorded a version as announced")
        self.assertEqual(self.cursor.generation(), 0)

        retry = digest.record(self.cursor, self.store, at=LATER(140),
                              generation=verdict["generation"],
                              state="DELIVERED", shown=document["shown"],
                              frozen=document["frozen"])
        self.assertTrue(retry["delivered"])
        self.assertEqual(self.cursor.generation(), verdict["generation"])
        self.assertEqual(self.cursor.summary()["last_payload"]["payload_hash"],
                         document["frozen"]["payload_hash"])
        # Same generation, so the same key: the transport sees one message.
        self.assertEqual(verdict["idempotency_key"],
                         document["frozen"]["idempotency_key"])

    def test_a_duplicate_retry_of_one_document_is_idempotent(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        first = self.deliver(document, generation=1)
        second = self.deliver(document, generation=1, at=LATER(140))
        self.assertEqual(first["superseded"], [])
        self.assertEqual(second["superseded"], [],
                         "a retry at the same generation is not stale")
        self.assertEqual(self.cursor.known(ref)["severity"], "high")
        self.assertEqual(self.compose(at=LATER(200))["counts"]["NEW"], 0)

    def test_a_cursor_crash_re_composes_the_same_generation(self):
        """Nothing was recorded, so nothing was told. The next digest is the
        same generation — the same idempotency key — and the deltas are still
        there rather than having been consumed by a send nobody recorded."""
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        first = self.compose(at=LATER(120))
        second = self.compose(at=LATER(180))
        self.assertEqual(self.cursor.generation(), 0)
        self.assertEqual(first["generation"], second["generation"])
        self.assertEqual(first["frozen"]["idempotency_key"],
                         second["frozen"]["idempotency_key"])
        self.assertEqual(second["counts"]["NEW"], 1)
        self.assertEqual(self.cursor.summary()["last_payload"], None)

    def test_a_late_answer_does_not_overwrite_the_newer_record(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, severity="medium", first_seen=LATER(60)))
        first = self.compose(at=LATER(120))
        self.deliver(first, generation=1)

        self.put(alert(ref, severity="high", first_seen=LATER(60)))
        second = self.compose(at=LATER(200))
        self.deliver(second, generation=2, at=LATER(210))
        self.assertEqual(self.cursor.known(ref)["severity"], "high")

        late = self.deliver(first, generation=1, at=LATER(220))
        self.assertEqual(late["superseded"], [ref])
        self.assertEqual(self.cursor.known(ref)["severity"], "high",
                         "a late answer for an older version moved the cursor back")
        self.assertEqual(self.cursor.known(ref)["generation"], 2)
        self.assertEqual(self.compose(at=LATER(300))["counts"]["ESCALATED"], 0,
                         "the escalation was announced a second time")

    def test_the_frozen_rows_are_the_rows_the_card_carried(self):
        self.baseline()
        for index in range(TOP_N + 2):
            subject = f"filesystem:/mnt/vol{index}"
            self.put(alert(ref_of(CODE, subject), subject=subject,
                           first_seen=LATER(60 + index)))
        document = self.compose(at=LATER(120))
        frozen = document["frozen"]
        self.assertEqual(frozen["generation"], document["generation"])
        self.assertEqual(frozen["row_count"], len(document["shown"]))
        self.assertEqual([row["key"] for row in frozen["rows"]],
                         document["shown_keys"])
        self.assertNotIn("rows", frozen["payload_hash"])
        # The hash is over the document as composed, and only over it: the
        # frozen block is excluded so that a reader who re-serialises the
        # document still computes the same version.
        again = json.loads(json.dumps(document))
        self.assertEqual(digest.freeze(again)["payload_hash"],
                         frozen["payload_hash"])
        self.assertEqual(digest.freeze(again)["version"], frozen["version"])

    def test_a_row_that_disagrees_with_the_frozen_table_does_not_win(self):
        """The frozen half is what the composing instant wrote down.

        The message really was sent, so refusing would leave a delivery
        unrecorded; the frozen table is recorded instead and the disagreement
        is named in the receipt. It cannot happen on the real path — the two
        halves are two readings of ONE document — so `disagreed` being non-empty
        is a statement that a caller rebuilt one of them.
        """
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        forged = json.loads(json.dumps(document))
        forged["shown"][0]["severity"] = "low"
        out = self.deliver(forged, generation=1)
        self.assertEqual(out["disagreed_rows"], [ref])
        self.assertEqual(self.cursor.known(ref)["severity"], "high",
                         "the edited row won over the frozen one")

    def test_a_row_the_frozen_payload_does_not_name_is_not_read_from_the_register(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        extra = dict(document["shown"][0])
        extra["key"] = ref_of(CODE, GROUP)
        out = digest.record(self.cursor, self.store, at=LATER(130), generation=1,
                            state="DELIVERED",
                            shown=document["shown"] + [extra],
                            frozen=document["frozen"])
        self.assertEqual(out["unnamed_rows"], [extra["key"]])
        self.assertIsNone(self.cursor.known(extra["key"])["severity"],
                          "a row nobody froze was recorded with a post-send value")

    def test_a_document_without_a_frozen_block_is_recorded_as_legacy(self):
        """Absent is not the same as contradictory. An older daemon's document
        is recorded the old way, and the cursor says which way that was."""
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        out = digest.record(self.cursor, self.store, at=LATER(130), generation=1,
                            state="DELIVERED", shown=document["shown"])
        self.assertIsNone(out["payload_version"])
        self.assertFalse(self.cursor.known(ref)["frozen"])
        self.assertTrue(self.cursor.announced(ref))
        self.assertIsNone(self.cursor.summary()["last_payload"])

    def test_a_frozen_generation_that_is_not_the_one_being_recorded_is_refused(self):
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        with self.assertRaises(digest.DigestError) as caught:
            self.deliver(document, generation=7)
        self.assertIn("generation", str(caught.exception))


# ---------------------------------------------------------------------------
# the contract owns the numbers
# ---------------------------------------------------------------------------


class TheContractOwnsTheNumbers(unittest.TestCase):
    def setUp(self):
        import yaml
        self.block = yaml.safe_load(
            (ROOT / "runtime" / "notification-contract.yaml").read_text(
                encoding="utf-8"))["alerts"]["digest"]

    def test_the_declared_numbers_are_the_ones_this_file_tests(self):
        self.assertEqual(self.block["interval_seconds"], INTERVAL)
        self.assertEqual(self.block["top_n"], TOP_N)
        self.assertEqual(tuple(self.block["immediate_severities"]), IMMEDIATE)
        self.assertEqual(tuple(self.block["include_severities"]), INCLUDED)
        self.assertEqual(tuple(self.block["proactive_severities"]), PROACTIVE)

    def test_the_kinds_are_the_four_the_module_implements(self):
        self.assertEqual(tuple(self.block["kinds"]), digest.KINDS)

    def test_the_cursor_is_a_sibling_of_the_register(self):
        path = digest.cursor_path("/somewhere/runtime/alerts/state.json")
        self.assertEqual(path.name, self.block["cursor_file"])
        self.assertEqual(path.parent.name, "alerts")

    def test_the_forbidden_path_is_stated_as_policy(self):
        """§Q2 forbids `alert → agent reasoning → Feishu` as the normal path.
        Written in the contract rather than in one module's comment, because
        the shape it forbids is the one somebody reintroduces as a feature."""
        forbidden = " ".join(self.block["not_the_path"])
        self.assertIn("agent turn", forbidden)

    def test_the_immediate_split_is_a_subset_of_what_is_included(self):
        for severity in self.block["immediate_severities"]:
            self.assertIn(severity, self.block["include_severities"])

    def test_the_cadence_floor_is_a_subset_of_what_is_included(self):
        """A severity may not cause a card without being allowed to appear in
        one — otherwise the gate would send a card whose own row the document
        had been told to leave out."""
        for severity in self.block["proactive_severities"]:
            self.assertIn(severity, self.block["include_severities"])

    def test_the_cadence_floor_excludes_the_lowest_level(self):
        """The one assertion that would catch a well-meaning widening. LOW is
        context, not news; if it ever becomes cause-for-a-card the hourly card
        returns and this file's own cadence tests cannot see it, because they
        all use one alert."""
        self.assertNotIn("low", self.block["proactive_severities"])


# ---------------------------------------------------------------------------
# shape — read the module, not the behaviour
# ---------------------------------------------------------------------------


class ThisIsAComposerAndNotATransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "runtime" / "alert_digest.py").read_text(
            encoding="utf-8")
        cls.tree = ast.parse(cls.source)

    def imported_names(self) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Import):
                names.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                names.add((node.module or "").split(".")[0])
        return names

    def loaded_modules(self) -> list[str]:
        out = []
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                    and node.func.id == "_load" and node.args:
                first = node.args[0]
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    out.append(first.value)
        return out

    def test_it_imports_no_transport_and_no_subprocess(self):
        for forbidden in ("subprocess", "socket", "requests", "urllib",
                          "http", "shutil"):
            self.assertNotIn(forbidden, self.imported_names(),
                             f"{forbidden} has no business in a composer")

    def test_it_cannot_reach_the_engine_delivery_hop(self):
        """`alert sync` → BotMux `/api/trigger` is the path §Q2 forbids as the
        normal one, and the way it comes back is one helper nobody re-read."""
        for name in self.loaded_modules():
            self.assertNotIn("human_control", name)
            self.assertNotIn("botmux", name)
            self.assertNotIn("notifications", name)

    def test_it_does_not_import_a_detector(self):
        """It reads the register. A composer that loaded `anomaly` or
        `resource_anomaly` could decide what is true, and then there would be
        two places that decide."""
        for name in self.loaded_modules():
            self.assertNotIn("anomaly.py", name)
            self.assertNotIn("resource_anomaly", name)
            self.assertNotIn("view.py", name)

    def code_words(self) -> set[str]:
        """Every name, attribute and non-docstring string in the module.

        Docstrings are excluded ON PURPOSE. This module's own docstring says it
        does not stop, restart, kill or prune anything — a test that searched
        the raw text would go red on the sentence that states the property,
        and the fix for that would be to delete the sentence.
        """
        docstrings = set()
        for node in ast.walk(self.tree):
            body = getattr(node, "body", None)
            if not isinstance(body, list) or not body:
                continue
            first = body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) \
                    and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
        words: set[str] = set()
        for node in ast.walk(self.tree):
            if isinstance(node, ast.Name):
                words.add(node.id.lower())
            elif isinstance(node, ast.Attribute):
                words.add(node.attr.lower())
            elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                    and id(node) not in docstrings:
                words.add(node.value.lower())
        return words

    def test_it_holds_no_mutating_verb(self):
        words = ("kill", "restart", "prune", "reclaim", "rmtree", "unlink",
                 "docker", "systemctl", "signal", "terminate")
        found = self.code_words()
        for word in words:
            self.assertFalse(any(word in name for name in found),
                             f"a digest must not be able to {word} anything: "
                             f"{sorted(n for n in found if word in n)}")

    def test_that_check_can_go_red(self):
        """The scan above reads identifiers, and the module's prose DOES use
        these words — "nothing here stops, restarts, kills, prunes". If the
        filter ever stops excluding docstrings, this fails first and says so,
        rather than the property quietly becoming untested."""
        self.assertIn("restart", self.source.lower())
        self.assertFalse(any("restart" in name for name in self.code_words()))
        self.assertTrue(self.code_words(), "the scan found no names at all")

    def test_the_only_file_it_writes_is_the_cursor(self):
        """One `_atomic_write`, and its subject is the delivery ledger. The
        register is written through `AlertStore.commit`, which is the store's
        own idiom rather than a second path to the same file."""
        calls = [node for node in ast.walk(self.tree)
                 if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                 and node.func.attr in ("_atomic_write", "write_text")]
        self.assertEqual(len(calls), 1)
        self.assertIn("_atomic_write", self.source)
        self.assertIn("cursor", self.source)

    def test_it_does_not_read_proc_or_run_a_probe(self):
        self.assertNotIn("/proc", self.source)
        self.assertNotIn("psutil", self.source)


if __name__ == "__main__":
    unittest.main()
