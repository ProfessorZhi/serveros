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
          first_seen=T0, resolved_at=None, occurrence=1, device=DEVICE,
          hold_kind=None):
    return {
        "alert_ref": ref, "code": code, "severity": severity,
        "device_ref": device, "subject_ref": subject, "resource_ref": subject,
        "state": state, "first_seen": first_seen, "last_seen": first_seen,
        "resolved_at": resolved_at, "occurrence_count": occurrence,
        "detector": "measurement-anomaly",
        # Null on a firing alert and a string on one the pass is holding,
        # which is exactly what `alerting.evaluate` writes: it clears the field
        # at the top of every firing row and sets it in the not-fired loop. The
        # UPDATED section at the end of this file is the only reader.
        "hold_kind": hold_kind,
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

    def test_a_held_backlog_is_named_in_the_decline(self):
        """§八-B. `held` was read by neither door, so a register with a
        backlog declined a card with the sentence "nothing in the register
        changed". Measured on this node 2026-10-04: 1,090 rows held and the
        sentence said nothing was. The decline still declines — a held row has
        not changed — but it now NAMES what it declined over."""
        for index in range(6):
            self.put(alert(ref_of(CODE, f"filesystem:/mnt/h{index}"),
                           subject=f"filesystem:/mnt/h{index}",
                           hold_kind="unmeasured", first_seen=iso(-3600)))
        self.baseline()
        verdict = digest.decision(self.compose(at=LATER(60)), self.cursor,
                                  at=LATER(60))
        self.assertFalse(verdict["send"])
        self.assertEqual(verdict["held"]["total"], 6)
        self.assertEqual(verdict["held"]["by_kind"], {"unmeasured": 6})
        self.assertIn("6 条主体处于持有状态", verdict["why"])
        self.assertIn("不等于「登记表没有变化」", verdict["why"])

    def test_a_quiet_register_still_says_only_that_nothing_changed(self):
        """The other half of the same rule. With nothing held the sentence is
        the one it always was — the attribution is added, not substituted."""
        self.baseline()
        verdict = digest.decision(self.compose(at=LATER(60)), self.cursor,
                                  at=LATER(60))
        self.assertEqual(verdict["held"]["total"], 0)
        self.assertNotIn("持有状态", verdict["why"])

    def test_a_declared_subject_losing_observation_can_cause_a_card(self):
        """§八-B, the door that was missing. A subject the node DECLARED should
        exist and can no longer be observed is a finding in `held`, not a delta
        in `rows`, so neither door could see it and no card could ever be sent
        for it. It takes the interval door: it is worth saying on its own terms
        and it carries no severity for the immediate door's cooldown table."""
        self.put(alert(ref_of("DESIRED_ACTUAL_MISMATCH", "runtime:thing"),
                       code="DESIRED_ACTUAL_MISMATCH", subject="runtime:thing",
                       hold_kind="unmeasured", first_seen=iso(-3600)))
        self.baseline()
        verdict = digest.decision(self.compose(at=LATER(7200)), self.cursor,
                                  at=LATER(7200))
        self.assertTrue(verdict["send"])
        self.assertTrue(verdict["held"]["integrity_on_the_face"])
        self.assertIn("monitoring-integrity", verdict["why"])
        self.assertIn("REVIEW", verdict["why"])

    def test_an_integrity_finding_off_the_face_says_why_it_is_not_on_it(self):
        """`consumption` codes are held in bulk on this node and the contract
        puts them at WATCH — off the face. They must not cause a card, and the
        decline must state that rather than leave it to be inferred."""
        for index in range(4):
            self.put(alert(ref_of("HIGH_MEMORY_SUSTAINED", f"runtime-group:{index}"),
                           code="HIGH_MEMORY_SUSTAINED",
                           subject=f"runtime-group:{index}",
                           hold_kind="unmeasured", first_seen=iso(-3600)))
        self.baseline()
        verdict = digest.decision(self.compose(at=LATER(7200)), self.cursor,
                                  at=LATER(7200))
        self.assertFalse(verdict["send"])
        self.assertEqual(verdict["held"]["total"], 4)
        self.assertIsNone(verdict["held"]["integrity_finding"])
        self.assertFalse(verdict["held"]["integrity_on_the_face"])

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

    # -- §八-B: a hole in the catalogue is not a decision ----------------

    def test_an_undecided_code_refuses_rather_than_declining(self):
        """«We never decided» and «we decided it is off the face» are different
        sentences, and only the first is a defect in this node's own
        declaration.

        `may_interrupt` answers False for both. Without this, a detector that
        shipped a code nobody added to `alerts.attention.codes` had its
        findings dropped from every card behind a decline that named them and
        said «none of them may occupy a line» — a policy decision nobody made,
        stated as fact. The contract's rule for the catalogue is the opposite:
        a code that appears in none is a code nobody decided about, and it is
        reported rather than quietly levelled. Measured on this node
        2026-10-04, the register holds 13,242 rows and 0 undecided codes, so
        this fires on a defect and never on data that is merely old.
        """
        self.put(alert(ref_of(CPU_CODE, GROUP), code=CPU_CODE, severity="high",
                       subject=GROUP))
        self.baseline()
        self.put(alert(ref_of("NOBODY_DECIDED_THIS", MEM), code="NOBODY_DECIDED_THIS",
                       severity="high", subject=MEM, first_seen=LATER(60)))
        at = LATER(120 + INTERVAL + 1)
        document = self.compose(at=at)
        with self.assertRaises(digest.DigestError) as got:
            digest.decision(document, self.cursor, at=at)
        self.assertIn("NOBODY_DECIDED_THIS", str(got.exception))
        # And the decided row beside it is NOT what raised: the message names
        # the hole and not the code that was merely put off the face.
        self.assertNotIn(CPU_CODE, str(got.exception))

    def test_a_held_row_on_an_undecided_code_is_a_hole_too(self):
        """The integrity path carries codes as well, and a code it cannot level
        is the same hole on the other half of the document."""
        self.baseline()
        self.put(alert(ref_of("NOBODY_DECIDED_THIS", MEM), code="NOBODY_DECIDED_THIS",
                       severity="medium", subject=MEM, first_seen=T0,
                       hold_kind="unmeasured"))
        holes = digest.undecided_codes(self.compose(at=LATER(60))["held"])
        self.assertEqual(holes, ["NOBODY_DECIDED_THIS"])
        with self.assertRaises(digest.DigestError):
            digest.decision(self.compose(at=LATER(60)), self.cursor, at=LATER(60))

    def test_the_catalogue_has_no_hole_on_the_codes_the_detectors_emit(self):
        """The same rule `test_attention.py` enforces, read from here so that a
        reader of THIS file learns that the table is closed. Not a duplicate:
        that test reads the detectors' own tables, this one reads the one
        helper `decision` uses and asserts it is built from them."""
        import importlib.util as _u
        spec = _u.spec_from_file_location("serveros_runtime_resource_anomaly",
                                          ROOT / "runtime" / "resource_anomaly.py")
        ra = _u.module_from_spec(spec)
        sys.modules["serveros_runtime_resource_anomaly"] = ra
        spec.loader.exec_module(ra)
        rows = [{"code": code} for code in ra.CODES]
        self.assertEqual(digest.undecided_codes(rows), [])

    def test_the_face_is_read_once_and_not_once_per_row(self):
        """The rule whose breach cost this node its control plane for 25 s.

        Measured 2026-09-24, hours after the two-axis gate went in: the first
        revision of `may_interrupt` asked `presentation.attention_of` once per
        row, and each of those calls re-read and re-parsed a 46 KB contract —
        53.8 ms a row. The real register had 459 such rows, so `alert.digest`
        took 24.7 s while its client waits 5.0 s. The gate answered correctly
        and nobody could hear it: the command that should have returned
        "nothing to report" returned a timeout.

        COUNTED, NOT TIMED. A duration is a property of this machine and of
        whatever else is running on it; the rule is that the table belongs to
        the contract and not to the row, and a count states exactly that.
        `may_interrupt` with no table builds one, so a caller that reverts to
        the per-row call makes this read twelve times instead of once.
        """
        self.baseline()
        for i in range(12):
            subject = f"/proc/{i}"
            self.put(alert(ref_of(CPU_CODE, subject), code=CPU_CODE,
                           severity="high", subject=subject,
                           first_seen=LATER(60 + i)))
        reads = []
        real = digest.face_table
        digest.face_table = lambda: (reads.append(1), real())[1]
        try:
            verdict = digest.decision(self.compose(at=LATER(120)), self.cursor,
                                      at=LATER(120))
        finally:
            digest.face_table = real
        self.assertFalse(verdict["send"])
        self.assertIn("may occupy a line", verdict["why"])
        self.assertEqual(
            len(reads), 1,
            "the face was read %d times for 12 rows; it is a property of the "
            "contract, so it is read once and handed to the rows" % len(reads))

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
# the pass that declined — the ledger is maintained on a tick nobody was told
# ---------------------------------------------------------------------------


#: A code whose alerts are MEDIUM, and it matters that it is not HIGH. The
#: `immediate` severities are the ones a row may interrupt somebody for, and
#: `account` refuses those. Everything in this section about a row being
#: accounted for has to be about a row that is not immediate, or it is
#: measuring the refusal instead of the accounting.
QUIET_CODE = "HIGH_MEMORY_SUSTAINED"


class TheLedgerMovesWhenNothingWasSent(Fixture):
    """`NEW` means the tick an identity first opened, and it has to keep meaning it.

    WHAT WENT WRONG, MEASURED. The classification in `deltas` was never the
    defect: `record is None` really does mean "not in the ledger", and that
    really is what a first sighting looks like. The defect was that the ledger
    was only ever written on DELIVERED, so a pass that declined to send wrote
    nothing and the next pass found the same absence and called it new again.
    On this node that had run since a baseline delivery at 2026-10-03T15:39:57Z,
    leaving 827 OPEN rows re-derived as NEW every tick against a register that
    had not changed.

    So these tests are about the WRITER and not about the classification, and
    the one that matters is the first: a declined pass, then a second compose,
    and no delta at all.
    """

    def decline(self, document, *, at, why="nobody was told", owed=()):
        return digest.account(self.cursor, at=at, digest=document, why=why,
                              owed=owed)

    def quiet(self, ref, *, first_seen, severity="medium", **kw):
        """A post-baseline OPEN alert at a severity that may not interrupt.

        `medium` by default and it matters: `immediate` is reached at severity
        alone, so a row of this severity is one `account` will actually account
        for rather than refuse.
        """
        return alert(ref, code=QUIET_CODE, severity=severity,
                     subject=GROUP, first_seen=first_seen, **kw)

    def test_a_declined_pass_advances_the_ledger_and_the_row_stops_being_new(self):
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        first = self.compose(at=LATER(120))
        self.assertEqual(first["counts"]["NEW"], 1)
        self.assertEqual([r["key"] for r in first["rows"]], [ref])

        out = self.decline(first, at=LATER(120), why="the interval has not elapsed")
        self.assertEqual(out["accounted_keys"], 1)
        self.assertEqual(out["accounted"], [ref])
        self.assertEqual(out["unaccounted_immediate"], [])

        second = self.compose(at=LATER(180))
        self.assertEqual(second["rows"], [],
                         "an unchanged open alert is not news on the tick after "
                         "the tick that already dealt with it")
        self.assertEqual(second["counts"]["NEW"], 0)

    def test_the_same_row_is_new_once_and_not_once_per_pass(self):
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        seen = []
        for tick in (120, 180, 240, 300):
            document = self.compose(at=LATER(tick))
            seen.append(document["counts"]["NEW"])
            self.decline(document, at=LATER(tick))
        self.assertEqual(seen, [1, 0, 0, 0])

    def test_accounting_is_not_announcing(self):
        """The two axes. `announced` is unchanged by this and stays the answer
        to the narrower question, "was a person told"."""
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        self.decline(document, at=LATER(120), why="nothing worth a card this hour")

        entry = self.cursor.known(ref)
        self.assertFalse(entry["announced"])
        self.assertFalse(self.cursor.announced(ref))
        self.assertEqual(entry["accounted_why"], "nothing worth a card this hour")
        self.assertEqual(entry["accounted_at"], LATER(120))
        self.assertEqual(entry["kind"], "NEW")
        self.assertEqual(entry["state"], "OPEN")

    def test_accounting_does_not_move_the_delivery_clocks(self):
        """A declined send is not an attempt and is not an arrival. A function
        that stamped either would make the interval door believe a card had
        gone out.

        The ledger DOES gain the key — that is the entire repair — and it gains
        no announcement, which is the distinction the two axes exist for.
        """
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        before = self.cursor.summary()
        self.decline(self.compose(at=LATER(120)), at=LATER(120))
        after = self.cursor.summary()
        self.assertIsNone(after["last_digest_at"])
        self.assertIsNone(after["last_attempt_at"])
        self.assertEqual(after["generation"], before["generation"])
        self.assertEqual(after["last_delivery"], before["last_delivery"])
        self.assertEqual(after["announced_keys"], 0)
        self.assertEqual(after["known_keys"], before["known_keys"] + 1)
        self.assertIn(ref, self.cursor.keys())

    def test_a_send_that_failed_leaves_the_shown_rows_owed(self):
        """`owed` is what the caller is still going to say. A card that went out
        and did not arrive has told nobody, so its rows stay deltas."""
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        document = self.compose(at=LATER(120))
        out = self.decline(document, at=LATER(120), why="the transport timed out",
                           owed=document["shown_keys"])
        self.assertEqual(out["accounted_keys"], 0)
        self.assertEqual(out["owed"], [ref])
        self.assertEqual(self.compose(at=LATER(180))["counts"]["NEW"], 1)

    def test_a_row_that_was_never_a_delta_is_not_accounted(self):
        ref = ref_of(CODE, FS)
        self.put(alert(ref, first_seen=iso(-86400)))
        self.baseline()
        document = self.compose(at=LATER(60))
        self.assertEqual(document["rows"], [])
        out = self.decline(document, at=LATER(60))
        self.assertEqual(out["accounted_keys"], 0)
        self.assertIsNone(self.cursor.known(ref).get("accounted_at"))
        self.assertNotIn("accounted_why", self.cursor.known(ref))

    def test_an_immediate_row_is_refused_rather_than_accounted(self):
        """A row at an immediate severity has been declared worth interrupting
        somebody for. Accounting it would mark a finding that should have been
        said as dealt with, and that is the silent drop the admission contract
        forbids — so it is refused and reported instead."""
        ref = ref_of(CODE, FS)
        self.baseline()
        self.put(alert(ref, first_seen=LATER(60)))  # DISK_CRITICAL, HIGH
        document = self.compose(at=LATER(120))
        self.assertTrue(document["rows"][0]["immediate"])
        out = self.decline(document, at=LATER(120))
        self.assertEqual(out["accounted_keys"], 0)
        self.assertEqual([r["key"] for r in out["unaccounted_immediate"]], [ref])
        self.assertIn("dealt with", out["unaccounted_immediate"][0]["why"])
        self.assertEqual(self.compose(at=LATER(180))["counts"]["NEW"], 1,
                         "a refused row is left un-accounted so the next pass "
                         "re-derives it rather than losing it")

    def test_a_severity_that_rose_after_a_decline_is_still_escalated(self):
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        self.decline(self.compose(at=LATER(120)), at=LATER(120))
        self.put(self.quiet(ref, first_seen=LATER(60), severity="high"))
        document = self.compose(at=LATER(180))
        self.assertEqual(document["counts"]["ESCALATED"], 1)
        self.assertEqual(document["counts"]["NEW"], 0)

    def test_a_row_that_resolved_after_a_decline_is_still_recovered(self):
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        self.decline(self.compose(at=LATER(120)), at=LATER(120))
        self.put(self.quiet(ref, first_seen=LATER(60), state="RESOLVED",
                            resolved_at=LATER(200)))
        document = self.compose(at=LATER(240))
        self.assertEqual(document["counts"]["RECOVERED"], 1)
        self.assertEqual(document["silent_resolutions"], 0)

    def test_a_row_that_reopened_after_a_decline_is_still_reopened(self):
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        self.decline(self.compose(at=LATER(120)), at=LATER(120))
        self.put(self.quiet(ref, first_seen=LATER(60), state="RESOLVED",
                            resolved_at=LATER(200)))
        self.decline(self.compose(at=LATER(240)), at=LATER(240))
        self.put(self.quiet(ref, first_seen=LATER(300)))
        document = self.compose(at=LATER(360))
        self.assertEqual(document["counts"]["REOPENED"], 1)
        self.assertEqual(document["counts"]["NEW"], 0)

    def test_the_generation_a_delivery_was_recorded_at_survives_accounting(self):
        """An accounting entry keeps the delivery axis it found. Writing zero
        here would un-supersede a late ACK that is older than what the ledger
        already knows, and the row it describes would be announced a second
        time.

        The delta accounted for here is a RECOVERY and not an escalation, and
        that is not incidental: `immediate` is reached at severity alone, so an
        ESCALATED row at a high severity is refused by `account` and never
        reaches the code this test is about.
        """
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.quiet(ref, first_seen=LATER(60)))
        carried = self.compose(at=LATER(120))
        digest.record(self.cursor, self.store, at=LATER(120), generation=1,
                      state="DELIVERED", shown=carried["shown"],
                      frozen=carried["frozen"])
        self.assertEqual(self.cursor.known(ref)["generation"], 1)
        self.assertTrue(self.cursor.announced(ref))

        self.put(self.quiet(ref, first_seen=LATER(60), state="RESOLVED",
                            resolved_at=LATER(160)))
        document = self.compose(at=LATER(180))
        self.assertEqual(document["counts"]["RECOVERED"], 1)
        out = self.decline(document, at=LATER(180), why="no card this hour")
        self.assertEqual(out["accounted_keys"], 1)
        entry = self.cursor.known(ref)
        self.assertEqual(entry["generation"], 1)
        self.assertTrue(entry["announced"],
                        "an accounted row that was announced before is still "
                        "one somebody heard about")


# ---------------------------------------------------------------------------
# UPDATED — the one change to an open alert that is not a severity
# ---------------------------------------------------------------------------


class AChangeOfKindIsANewFact(Fixture):
    """Severity and state the same; whether this alert IS a measurement changed.

    An alert held at `above_floor` says "we looked, the value is still up". The
    same identity held at `unmeasured` says "nobody got a reading". Those are
    the same code, the same subject, the same severity and the same state, and
    opposite facts — one is a result and one is a loss of observation, and the
    contract builds monitoring-integrity findings out of the second and not the
    first. A ledger that could not see the change between them would let an
    alert stop being measured without anybody being told.
    """

    def decline(self, document, *, at, why="nobody was told", owed=()):
        return digest.account(self.cursor, at=at, digest=document, why=why,
                              owed=owed)

    def held(self, ref, kind, *, first_seen=LATER(60), severity="medium"):
        return alert(ref, code=QUIET_CODE, severity=severity, subject=GROUP,
                     first_seen=first_seen, hold_kind=kind)

    def test_a_hold_kind_that_changed_is_updated(self):
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.held(ref, "above_floor"))
        self.decline(self.compose(at=LATER(120)), at=LATER(120))
        self.put(self.held(ref, "unmeasured"))
        document = self.compose(at=LATER(180))
        self.assertEqual(document["counts"]["UPDATED"], 1)
        self.assertEqual(document["counts"]["NEW"], 0)
        self.assertEqual([r["key"] for r in document["rows"]], [ref])

    def test_a_first_classification_is_not_a_change(self):
        """Null to stated is not a transition between two facts; it is a row
        that had no classification being classified. Calling that UPDATED would
        make every held row emit one the first time it was held."""
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.held(ref, None))
        self.decline(self.compose(at=LATER(120)), at=LATER(120))
        self.put(self.held(ref, "above_floor"))
        document = self.compose(at=LATER(180))
        self.assertEqual(document["counts"]["UPDATED"], 0)

    def test_a_kind_that_did_not_change_is_not_a_delta(self):
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.held(ref, "above_floor"))
        self.decline(self.compose(at=LATER(120)), at=LATER(120))
        document = self.compose(at=LATER(180))
        self.assertEqual(document["rows"], [])
        self.assertEqual(document["counts"]["UPDATED"], 0)

    def test_an_updated_row_can_be_printed_and_cannot_cause_a_card(self):
        ref = ref_of(QUIET_CODE, GROUP)
        self.baseline()
        self.put(self.held(ref, "above_floor"))
        self.decline(self.compose(at=LATER(120)), at=LATER(120))
        self.put(self.held(ref, "unmeasured"))
        document = self.compose(at=LATER(180))
        row = document["rows"][0]
        self.assertFalse(row["immediate"],
                         "an UPDATED row may occupy a line on a card that "
                         "exists for another reason; it may not be the reason")
        self.assertNotIn(digest.UPDATED, digest._PROACTIVE_KINDS)
        self.assertIn(digest.UPDATED, digest.KINDS)
        self.assertEqual(set(digest._KIND_ORDER), set(digest.KINDS),
                         "every kind a card may read is in the order table")


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
