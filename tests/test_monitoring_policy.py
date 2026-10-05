#!/usr/bin/env python3
"""Whether saying something is due — the decision, and the refusals it makes.

The module under test answers one question per tick: is a Status Update Notice
due, and what may it ask of the transport? Every test here is one of the ways
that answer can be wrong in a way nobody notices, and they are grouped by which
way:

    a notice that does not name its card is a second status card
    a notice that fires twice at 14:00 is a duplicate
    a notice that does not fire when nothing is wrong is not a heartbeat
    a notice whose bucket defaults quietly is a notice that fails silently

WHAT IS NOT HERE
----------------
No clock, no store, no ledger and no transport. `notice()` takes every input as
an argument, so every instant below is written down and nothing in
`runtime/monitoring_policy.py` reads the machine. The calendar years are
SYNTHETIC for the reason `tests/test_business_calendar.py` gives at length, and
the schedules below come from the real `routine_window` rather than from a
hand-built mapping — the point is the schedule the node would actually compute.

The one cross-module claim is `TheCadenceIsTheOneTheClockRuns`: the policy
declares `hourly` and does NOT declare a number, because a cadence written in
two places disagrees with itself the first time the window moves. That test
walks a workday and holds the policy to the calendar's own count.
"""
from __future__ import annotations

import importlib.util
import shutil
import sys
import tempfile
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


business_calendar = _load("serveros_runtime_business_calendar",
                          ROOT / "runtime" / "business_calendar.py")
policy_module = _load("serveros_runtime_monitoring_policy",
                      ROOT / "runtime" / "monitoring_policy.py")

PLAIN_WORKDAY = "2031-06-10"
REST_DAY = "2031-06-09"
#: The workday as it reaches an identity: `<jurisdiction>/<date>`. Spelled out
#: rather than derived here, because the length of THIS string is what the
#: transport cap bites on and a test that computed it could not notice the
#: calendar growing a longer jurisdiction.
DAY = "CN-Mainland/2031-06-10"

#: What `device:tokyo-shared-dev-01` becomes once it has to fit beside `DAY`.
#: A literal, and deliberately so: this is the OBSERVABLE, and computing it here
#: would make every assertion below follow the implementation instead of
#: pinning it. A change to how the segment is compressed has to be an edit in
#: this file too, which is the point.
_DIGESTED_DEVICE = "5ca34bbb29"

#: The card version a notice in this file points at. A name rather than a
#: repeated literal because it is now two things at once: what the notice names
#: AND what the delivery receipt says arrived, and the ordinary node is exactly
#: the case where those two are the same string. The assertion below keeps
#: spelling the value out, so a change to it is an edit here too.
VERSION = "status@abc123def456"

CALENDAR = """\
schema: serveros/business-calendar.v1
jurisdiction: CN-Mainland
timezone: Asia/Shanghai
source:
  document_id: synthetic-test-notice-2031
  revision: synthetic-rev-1
years:
  "2031":
    workdays: ["2031-06-07"]
    non_working_days: ["2031-06-09"]
imported_at: "2030-12-01T00:00:00+08:00"
verified_at: "2030-12-01T00:00:00+08:00"
"""

_READING = None
_CALENDAR_DIR = None

#: The contract this node actually ships, loaded once in `setUpModule` and read
#: by the tests below that must be asserting a property of the policy rather
#: than of a literal that happens to agree with it today.
SHIPPED = None


def reading() -> dict:
    """`read_declaration`'s own answer, from one synthetic file written once."""
    global _READING, _CALENDAR_DIR
    if _READING is None:
        _CALENDAR_DIR = Path(tempfile.mkdtemp(prefix="monitoring-policy-calendar-"))
        path = _CALENDAR_DIR / "business-calendar.yaml"
        path.write_text(CALENDAR, encoding="utf-8")
        _READING = business_calendar.read_declaration(path)
    return _READING


def setUpModule():
    global SHIPPED
    reading()
    #: And the shipped contract, so that a test below asserting a property of
    #: «the policy» is asserting it of the file this node ships rather than of a
    #: fixture that happens to agree with it today.
    SHIPPED = policy_module.load()


def tearDownModule():
    global _READING, _CALENDAR_DIR
    if _CALENDAR_DIR is not None:
        shutil.rmtree(_CALENDAR_DIR, ignore_errors=True)
    _READING, _CALENDAR_DIR = None, None


def at(day, hour, minute=0, second=0, offset="+08:00") -> str:
    return f"{day}T{hour:02d}:{minute:02d}:{second:02d}{offset}"


def schedule(instant, *, reading_=None) -> dict:
    """The window this instant falls in, computed the way the node computes it."""
    return business_calendar.routine_window(instant,
                                            reading=reading_ or reading())


def no_calendar(instant) -> dict:
    """The same call on a node that has imported no calendar."""
    return business_calendar.routine_window(
        instant, reading=business_calendar.read_declaration(
            Path("/nonexistent/business-calendar.yaml")))


def row(*, verdict="OK", observation="运行正常", sections=None, gaps=0) -> dict:
    """One day-store row, shaped the way `card_day.row` shapes one.

    Two things here are the real shape and not a convenience: `gaps` is a COUNT
    (`card_day.row` stores `len(health["gaps"])`), and `hhmm`, `tick` and
    `local` are absent on purpose — `ROW_FIELDS` carries them and none of them
    is a reading, so a row that omits them is the strongest way to say that
    `state_of` must not be able to see them.
    """
    return {"verdict": verdict, "observation": observation,
            "sections": dict({"mine": "OK", "host": "OK", "gateway": "UNBOUND"},
                             **(sections or {})),
            "gaps": gaps}


def receipt(state: str = "DELIVERED", version: str = VERSION,
            at: str = "") -> dict:
    """One `status` receipt row, in `card_delivery.record`'s shape.

    Only the four fields this decision reads are here, and they are the ones the
    ledger writes: a fixture that carried the whole row would be a second copy
    of a shape this module does not own.
    """
    return {"kind": "status", "state": state, "version": version, "at": at}


#: «This call did not vary what became of the card» — a sentinel rather than
#: `None`, because `None` is itself a case this file has to be able to state: a
#: ledger holding no `status` receipt at all is not the same as one whose newest
#: status send FAILED. Both are refused, and both print the pair they found.
CARD_ARRIVED = object()


def decide(instant, *, latest=None, previous=None, created=False,
           delivered=False, mode=None, policy=None, version=VERSION,
           device="device:tokyo-shared-dev-01", card=CARD_ARRIVED) -> dict:
    """`notice()` with the arguments this file mostly does not vary.

    ``card`` is the delivery ledger's newest `status` receipt — what became of
    the card the notice points at. It defaults to a DELIVERED send carrying the
    version this call names, which is the ordinary node: the patch went out, the
    ledger recorded it, and then the hour's notice was asked for. A test that
    wants another case names one: `receipt(state="FAILED")`,
    `receipt(version="status@000000000000")`, or `None` for a ledger that holds
    no status row at all.
    """
    active = policy
    if active is None:
        active = dict(SHIPPED)
        if mode is not None:
            active = dict(active)
            active["notification"] = dict(active["notification"], mode=mode)
    if card is CARD_ARRIVED:
        card = receipt(version=version, at=instant) if version else None
    return policy_module.notice(
        policy=active, schedule=schedule(instant), latest=latest, previous=previous,
        card_version=version, device_ref=device, at=instant, created=created,
        already_delivered=delivered, status_receipt=card)


class TheNoticeNamesTheCardItIsAbout(unittest.TestCase):
    """A notice that owns a fact is a card, and a card here is one per day.

    This is the refusal the whole design rests on, so it is a raise rather than
    a `due: false`: a caller that forgot the version has not decided anything,
    and returning a quiet answer would let it send a notice that points at
    nothing — which is precisely the second status card being avoided.
    """

    def test_no_card_version_is_refused_rather_than_composed(self):
        for nothing in ("", "   ", None):
            with self.subTest(version=repr(nothing)):
                with self.assertRaises(policy_module.MonitoringPolicyError):
                    decide(at(PLAIN_WORKDAY, 14), version=nothing)

    def test_the_answer_carries_the_version_it_named(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row())
        self.assertEqual(answer["card_version"], "status@abc123def456")

    def test_the_identity_is_the_one_a_person_reads(self):
        """The workday and the hour stay legible; the device does not have to.

        This host's real pair — `tokyo-shared-dev-01` with a
        `CN-Mainland/2031-06-10` workday — is 59 characters against a transport
        that takes 50, and the transport applies that cap as a silent
        truncation. What a truncated key cuts off first is the END, which is
        the hour: every hour of one workday would share a key, the service
        would take 15:00 for a repeat of 14:00, and a day would deliver one
        notice while every layer reported thirteen. So the device is what gives
        way, and the two parts that tell one hour from the next are the parts a
        reader and a dedupe both need.
        """
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row())
        key = answer["idempotency_key"]
        self.assertEqual(key,
                         f"status-notice:{_DIGESTED_DEVICE}/CN-Mainland/2031-06-10/14")
        self.assertLessEqual(len(key), policy_module.TRANSPORT_KEY_MAX)
        self.assertTrue(key.endswith("/CN-Mainland/2031-06-10/14"))

    def test_two_devices_do_not_collapse_into_one_key(self):
        """The failure a truncated NAME would cause, one level down.

        `tokyo-shared-dev-01` and `tokyo-shared-dev-02` share nineteen
        characters. A device segment shortened by cutting would make two
        machines' same hour into one notice — the same defect as the hour being
        cut off, with a smaller blast radius and the same silence.
        """
        a = policy_module.identity("device:tokyo-shared-dev-01", DAY, 14)
        b = policy_module.identity("device:tokyo-shared-dev-02", DAY, 14)
        self.assertNotEqual(a, b)
        self.assertLessEqual(len(a), policy_module.TRANSPORT_KEY_MAX)
        self.assertLessEqual(len(b), policy_module.TRANSPORT_KEY_MAX)

    def test_the_hour_survives_in_every_hour_of_the_day(self):
        """The positive half of the same claim: thirteen hours, thirteen keys.

        Asserted over the hours the calendar actually produces rather than over
        two of them, because the defect this guards against makes the key the
        SAME for all of them — a check that compared two hours would still pass
        if the cut landed between them.
        """
        keys = [policy_module.identity("device:tokyo-shared-dev-01", DAY, h)
                for h in range(8, 21)]
        self.assertEqual(len(keys), 13)
        self.assertEqual(len(set(keys)), 13)
        for key in keys:
            self.assertLessEqual(len(key), policy_module.TRANSPORT_KEY_MAX)

    def test_a_day_that_leaves_no_room_is_refused_rather_than_trimmed(self):
        """When even the digest cannot fit, this raises — it does not truncate.

        A key that is one character too long is not a shorter name for the same
        message; it is a name for a different one, and the transport will not
        say so. The refusal is the last honest answer available, and it names
        the room that was left because the only fix is to shorten the workday
        or the device.

        The boundary is scanned for rather than written down, but the scan
        asserts WHERE it landed: a guard that only refused once the room went
        negative would still produce a boundary, one seven characters later,
        and every key it produced in between would be silently shorter than it
        claims to be. So the last day that answers must have room for a digest
        and the first that does not must not.
        """
        device = "tokyo-shared-dev-01"
        room = lambda day: (policy_module.TRANSPORT_KEY_MAX - len("status-notice:")
                            - len(f"/{day}/14"))  # noqa: E731
        fits = refuses = None
        for n in range(1, 80):
            day = "D" * n
            try:
                key = policy_module.identity(f"device:{device}", day, 14)
            except policy_module.MonitoringPolicyError:
                refuses = day
                break
            fits = (day, key)
            # The key always ends in the workday and the hour: neither is ever
            # the thing that gives way when the key gets tight.
            self.assertTrue(key.endswith(f"/{day}/14"), key)
            self.assertLessEqual(len(key), policy_module.TRANSPORT_KEY_MAX)
            segment = key.split("/")[0]
            if room(day) >= len(device):
                # It fits whole, so nothing was shortened at all.
                self.assertEqual(segment, f"status-notice:{device}")
            elif room(day) >= 13:
                # A readable prefix, a hyphen, then a real digest of the WHOLE
                # name. A prefix of the name (`tokyo-shared-d`) would be a
                # different answer wearing the same shape; so would a digest
                # that came from somewhere other than the device.
                self.assertEqual(
                    segment,
                    f"status-notice:{device[:room(day) - 9]}-{_DIGESTED_DEVICE[:8]}")
            else:
                # Too tight for a prefix that would identify anything, so the
                # whole segment is the digest, and it is the room that decides
                # how much of it fits — never one character more, and never a
                # prefix of the device name instead.
                full = "status-notice:" + _DIGESTED_DEVICE
                self.assertTrue(full.startswith(segment) or segment.startswith(full),
                                key)
                self.assertEqual(len(segment), len("status-notice:") + room(day))
        self.assertIsNotNone(fits)
        self.assertIsNotNone(refuses, "再长的日子也必须有一个拒绝，而不是一直短下去")
        # The boundary is where the guard says it is, not merely somewhere.
        # A guard that only refused once the room went negative would put this
        # boundary seven characters later and shorten every key in between.
        self.assertEqual(len(refuses), len(fits[0]) + 1)
        self.assertGreaterEqual(room(fits[0]), 8)
        self.assertLess(room(refuses), 8)

    def test_a_device_that_fits_is_left_alone(self):
        """The readable form is the one to keep; compressing is the fallback.

        A short workday — which is what a non-jurisdiction-prefixed day looks
        like — leaves room, and there a reader gets the device name back. The
        digest is only for the case that does not fit.
        """
        key = policy_module.identity("device:tokyo-shared-dev-01", "2031-06-10", 8)
        self.assertEqual(key, "status-notice:tokyo-shared-dev-01/2031-06-10/08")

    def test_a_full_device_ref_is_shortened_but_a_bare_one_is_not_invented(self):
        self.assertEqual(policy_module.short_device("device:alpha"), "alpha")
        self.assertEqual(policy_module.short_device("alpha"), "alpha")
        # Not defaulted to the local device name: a key with a guessed device in
        # it dedupes two machines into one, and the second machine goes unsent.
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.short_device("")


class TheNoticeQuotesTheCardAndOwnsNothing(unittest.TestCase):
    """Every word a notice says is checkable against the card it names.

    The row goes out BESIDE the version rather than being recomputed here, and
    the keys are closed: the document is exactly the fields below, so adding a
    finding of the notice's own means changing this test on purpose. A notice
    that derived a verdict would be a second place the state is decided, and the
    two would disagree on the hour that mattered.
    """

    #: Spelled out rather than counted, because the point is which fields, not
    #: how many. A new field arriving here is a new claim on the face.
    KEYS = frozenset({
        "schema", "kind", "at", "day_ref", "hour", "hhmm", "tick",
        # The denominator of «第 7/13 小时». It is in the document rather than
        # counted by the reader, because §10's window decides it and a reader
        # counting the hours of a window they had to be told about is a second
        # derivation of the same number.
        "ticks_per_workday",
        "policy_mode", "card_version", "device_ref",
        "verdict", "observation", "sections", "gaps",
        # What the card this notice points at did on the way to the reader: the
        # state and version of the newest `status` receipt. A claim of the
        # notice's own, and the last one it was allowed to make — §7 requires
        # «PATCH DELIVERED, and only then this notice» to be enforced rather
        # than described, and this is the pair it is enforced against.
        "status_receipt",
        "due", "reason", "idempotency_key", "bucket", "prompt", "says",
    })

    def test_the_document_carries_exactly_these_fields(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row())
        self.assertEqual(set(answer), set(self.KEYS))

    def test_the_row_is_quoted_verbatim_and_not_reworded(self):
        odd = "运行正常（采样窗口 59.8s）"
        latest = row(observation=odd, sections={"host": "ATTENTION"}, gaps=2)
        answer = decide(at(PLAIN_WORKDAY, 14), latest=latest)
        self.assertEqual(answer["observation"], odd)
        self.assertEqual(answer["sections"], latest["sections"])
        self.assertEqual(answer["gaps"], 2)
        self.assertEqual(answer["verdict"], latest["verdict"])

    def test_the_notice_says_which_version_of_the_card_it_is_about(self):
        # The pointer is the whole difference between a notice and a card, and
        # it is in the document a transport would carry — so the frozen hash of
        # the notice covers it and a reader can follow it to the card.
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(),
                        version="status@0123456789ab")
        self.assertEqual(answer["card_version"], "status@0123456789ab")
        self.assertEqual(answer["kind"], "status-update")


class TheCadenceIsTheOneTheClockRuns(unittest.TestCase):
    """The policy says `hourly` and does not say «thirteen».

    The count lives in `business_calendar.ROUTINE_UPDATES_PER_WORKDAY`, derived
    from the window. If it is also written in the policy the two disagree the
    first time the window moves, and the one that is wrong is whichever nobody
    re-read. So the test walks the window and holds the policy to the calendar.
    """

    def test_a_workday_is_one_notice_identity_per_hour_and_no_collisions(self):
        keys = []
        for hour in range(business_calendar.WINDOW_FIRST_HOUR,
                          business_calendar.WINDOW_LAST_HOUR + 1):
            answer = decide(at(PLAIN_WORKDAY, hour), latest=row())
            self.assertTrue(answer["due"], f"{hour:02d}:00 应到点")
            keys.append(answer["idempotency_key"])
        self.assertEqual(len(keys), business_calendar.ROUTINE_UPDATES_PER_WORKDAY)
        self.assertEqual(len(set(keys)), len(keys), "同一工作日内两小时撞成一个身份")

    def test_the_window_is_inclusive_at_both_ends(self):
        for hour in (business_calendar.WINDOW_FIRST_HOUR,
                     business_calendar.WINDOW_LAST_HOUR):
            with self.subTest(hour=hour):
                self.assertTrue(schedule(at(PLAIN_WORKDAY, hour))["in_window"])

    def test_the_shipped_cadence_is_the_only_one_the_calendar_implements(self):
        self.assertEqual(SHIPPED["status_card"]["cadence"], "hourly")
        self.assertEqual(policy_module.CADENCES, ("hourly",))
        self.assertEqual(business_calendar.ROUTINE_UPDATES_PER_WORKDAY,
                         business_calendar.WINDOW_LAST_HOUR
                         - business_calendar.WINDOW_FIRST_HOUR + 1)


class TheCreateTickIsAlreadyAnnounced(unittest.TestCase):
    """08:00 needs no notice: the card message itself is a new message.

    A notice here would make the first hour of every workday the one hour with
    two messages about it — and the second of them would say «更新成功» about
    the very message that had just arrived.
    """

    def test_the_tick_that_builds_the_card_does_not_also_notice(self):
        answer = decide(at(PLAIN_WORKDAY, 8), latest=row(), created=True)
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.ON_CREATE_ITSELF)

    def test_the_refusal_still_names_the_identity_it_declined(self):
        # Not a defect: the hour exists and a reader asking «did 08:00 get
        # told?» deserves the identifier that would have carried it.
        answer = decide(at(PLAIN_WORKDAY, 8), latest=row(), created=True)
        self.assertEqual(answer["idempotency_key"],
                         f"status-notice:{_DIGESTED_DEVICE}/CN-Mainland/2031-06-10/08")

    def test_the_same_hour_without_the_create_flag_is_due(self):
        answer = decide(at(PLAIN_WORKDAY, 8), latest=row(), created=False)
        self.assertTrue(answer["due"])


class TheCardHasToHaveArrived(unittest.TestCase):
    """§7's order, enforced where the decision is made.

    A notice's own line is «当天卡已更新 · <version>». When the patch it follows
    did not arrive — or arrived as a different version — that sentence is false
    about the card the reader will open, and a monitoring message that states
    something untrue is worse than the silence it was invented to break. So the
    answer is a refusal, and the reason says which of the two happened because
    they send a reader to different places: one to the transport, one to
    whatever version the caller thought it was sending.
    """

    def test_a_patch_that_did_not_arrive_produces_no_notice(self):
        for state in ("FAILED", "UNKNOWN", "UNSUPPORTED"):
            with self.subTest(state=state):
                answer = decide(at(PLAIN_WORKDAY, 14), latest=row(),
                                previous=row(), card=receipt(state=state))
                self.assertFalse(answer["due"])
                self.assertEqual(answer["reason"],
                                 policy_module.CARD_NOT_DELIVERED)
                # The refusal still names the hour it declined, so a reader
                # asking «was 14:00 told?» gets the identifier that would have
                # carried it — and so does whoever retries it.
                self.assertTrue(answer["idempotency_key"])

    def test_a_ledger_with_no_status_row_refuses_rather_than_assumes(self):
        """The default is `None`, and it refuses.

        A default of «assume it arrived» would make the enforcement something a
        caller opts into by remembering an argument — and the caller that
        forgets is the one whose patch just failed. On a node whose ledger holds
        no status rows at all, there is nothing to check the notice's words
        against, and that is reported as the refusal it is rather than as a
        quiet answer that looks like a policy decision.
        """
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(), previous=row(),
                        card=None)
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.CARD_NOT_DELIVERED)
        self.assertIsNone(answer["status_receipt"])

    def test_a_version_that_never_went_out_is_not_announced(self):
        """A card did arrive; it was not this one.

        Reachable without anybody making a mistake: a tick whose patch recorded
        no receipt leaves the previous hour's version as the newest `status`
        row, and the notice about to go out would name a version nobody has.
        """
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(), previous=row(),
                        card=receipt(version="status@000000000000"))
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"],
                         policy_module.CARD_VERSION_NOT_DELIVERED)
        # And it says which version did arrive, which is the whole use of the
        # answer: «nothing arrived» and «this other thing arrived» are read
        # differently by whoever has to repair it.
        self.assertEqual(answer["status_receipt"]["version"],
                         "status@000000000000")

    def test_the_same_tick_is_due_once_the_card_has_arrived(self):
        """The positive control, and it is not decoration.

        Every refusal above would also pass against an implementation that
        refused everything — the shape of failure this file has already been
        bitten by once. Same instant, same row, same version, and the only
        difference is the receipt.
        """
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(), previous=row())
        self.assertTrue(answer["due"])
        self.assertEqual(answer["reason"], policy_module.MODE_ALWAYS)
        self.assertEqual(answer["status_receipt"]["state"], "DELIVERED")
        self.assertEqual(answer["status_receipt"]["version"], VERSION)

    def test_a_rest_day_is_still_a_rest_day_with_a_failed_patch(self):
        """The check is asked after the calendar, and this is why.

        No status card is sent on a rest day, so «did the card arrive» has no
        answer there — and a version that asked it anyway would report a
        delivery fault on every rest day of the year, which is how a real
        delivery fault stops being read.
        """
        answer = decide(at(REST_DAY, 14), latest=row(), created=False,
                        card=receipt(state="FAILED"))
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.NOT_A_WORKDAY)

    def test_a_create_tick_whose_card_failed_is_reported_as_a_delivery(self):
        """The check is asked before `created`, and this is why.

        On the tick that builds the day's card, a send that failed leaves no
        message in the chat. Answering «the card itself is the new message»
        would describe a message that does not exist as the reason nothing else
        was said, and the reader would look for a card that is not there.
        """
        answer = decide(at(PLAIN_WORKDAY, 8), latest=row(), created=True,
                        card=receipt(state="FAILED"))
        self.assertEqual(answer["reason"], policy_module.CARD_NOT_DELIVERED)

    def test_the_local_spelling_of_delivered_is_the_ledger_s(self):
        """A second copy of a closed-set value, held equal by a test.

        This module imports no sibling — it is loaded by path — so it cannot
        read `card_delivery.OUTCOMES`. Rather than leave the copy unguarded,
        this fails the moment either side renames the outcome, and the failure
        names both spellings.
        """
        ledger = _load("serveros_runtime_card_delivery_for_policy",
                       ROOT / "runtime" / "card_delivery.py")
        self.assertIn(policy_module.DELIVERED, ledger.OUTCOMES)

    def test_every_answer_carries_what_was_checked(self):
        """Including the ones that agreed to send.

        A message that was allowed out is a message somebody may later have to
        justify, and the pair it was cleared by is the justification.
        """
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(), previous=row())
        self.assertEqual(set(answer["status_receipt"]),
                         {"state", "version", "at"})


class APatchedTickGetsTold(unittest.TestCase):
    """`always` — the default, and the reason it is the default.

    On 2026-09-30 nothing changed and nothing needed attention for seven hours,
    and under either quieter mode the reader would have received nothing at all.
    From a phone that is indistinguishable from a monitor that died, so the
    quieter modes are available and are not the default.
    """

    def test_a_patch_tick_in_the_window_is_due_under_always(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row())
        self.assertTrue(answer["due"])
        self.assertEqual(answer["reason"], policy_module.MODE_ALWAYS)
        self.assertEqual(answer["policy_mode"], "always")

    def test_the_default_mode_is_always(self):
        self.assertEqual(policy_module.mode_of(SHIPPED), "always")

    def test_the_tick_is_the_hours_ordinal_within_the_window(self):
        # 14:00 is the seventh hour of 08:00–20:00. Both numbers are carried in
        # the answer because a reader checking «7/13» against the card needs the
        # tick, and the identity is built from the hour.
        self.assertEqual(schedule(at(PLAIN_WORKDAY, 14))["tick"], 7)
        self.assertEqual(schedule(at(PLAIN_WORKDAY, 14))["hour"], 14)


class AnAnsweredTickIsNotAnsweredTwice(unittest.TestCase):
    """Per-tick idempotency, answered by the ledger rather than by hope.

    The identity already means a re-run of the timer produces the same key. That
    is only load-bearing if `card notice` consults it: a decision that says
    `due: true` twice and trusts the far side to collapse the two is a duplicate
    waiting for the day the far side changes.
    """

    def test_a_delivered_tick_is_not_due_again(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(), delivered=True)
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.ALREADY_DELIVERED)

    def test_the_identity_it_declines_is_the_one_it_would_have_used(self):
        first = decide(at(PLAIN_WORKDAY, 14), latest=row())
        second = decide(at(PLAIN_WORKDAY, 14), latest=row(), delivered=True)
        self.assertEqual(first["idempotency_key"], second["idempotency_key"])

    def test_the_next_hour_is_a_different_tick_and_is_due(self):
        answer = decide(at(PLAIN_WORKDAY, 15), latest=row())
        self.assertTrue(answer["due"])


class ChangesOnlyReadsTheReadingNotTheNumbers(unittest.TestCase):
    """`changes-only`: the reading moved, not the arithmetic.

    Numbers never reach `notice()` — `card_day.check_row` is the reason, and it
    is also what makes this mode meaningful: two hours that differ only in a CPU
    percentage are the same reading, and a mode that told a reader otherwise
    would be a mode that tells them «nothing changed» is never true.
    """

    def test_a_row_that_says_the_same_thing_is_not_a_change(self):
        before = row(observation="运行正常", sections={"gateway": "OK"})
        after = row(observation="运行正常 · 采样 14:00", sections={"gateway": "OK"})
        answer = decide(at(PLAIN_WORKDAY, 14), latest=after, previous=before,
                        mode="changes-only")
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.UNCHANGED)

    def test_a_section_that_moved_is_a_change(self):
        before = row(sections={"host": "OK"})
        after = row(sections={"host": "ATTENTION"})
        answer = decide(at(PLAIN_WORKDAY, 14), latest=after, previous=before,
                        mode="changes-only")
        self.assertTrue(answer["due"])
        self.assertEqual(answer["reason"], policy_module.CHANGED)

    def test_a_verdict_that_moved_is_a_change(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(verdict="ATTENTION"),
                        previous=row(verdict="OK"), mode="changes-only")
        self.assertEqual(answer["reason"], policy_module.CHANGED)

    def test_a_gap_that_appeared_is_a_change(self):
        # A collection gap is part of what the row says, not a rendering detail:
        # an hour that could not be read is not the same reading as one that was.
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(gaps=1),
                        previous=row(gaps=0), mode="changes-only")
        self.assertEqual(answer["reason"], policy_module.CHANGED)

    def test_no_previous_reading_is_not_reported_as_no_change(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(), previous=None,
                        mode="changes-only")
        self.assertTrue(answer["due"])
        self.assertEqual(answer["reason"], policy_module.NO_PREVIOUS_ROW)

    def test_always_is_deaf_to_this_distinction(self):
        before = row()
        after = row()
        self.assertEqual(decide(at(PLAIN_WORKDAY, 14), latest=after,
                                previous=before)["reason"],
                         policy_module.MODE_ALWAYS)


class AttentionOnlyIsDeafToRoutine(unittest.TestCase):
    """`attention-only`: only when there is something a person should look at."""

    def test_a_good_reading_is_not_due(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(verdict="OK"),
                        mode="attention-only")
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.ATTENTION_ABSENT)

    def test_a_reading_that_asks_for_attention_is_due(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(verdict="ATTENTION"),
                        mode="attention-only")
        self.assertTrue(answer["due"])
        self.assertEqual(answer["reason"], policy_module.ATTENTION_PRESENT)
        self.assertEqual(answer["bucket"], "ATTENTION")

    def test_an_unconfirmed_reading_is_not_treated_as_a_good_one(self):
        # §6: 有项目尚未读到. Nobody is stopped, so it is not ACTION_REQUIRED,
        # and an incomplete reading is not a good one, so it is not ROUTINE.
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(verdict="UNCONFIRMED"),
                        mode="attention-only")
        self.assertTrue(answer["due"])
        self.assertEqual(answer["bucket"], "ATTENTION")


class NoUnrecognisedReadingIsRoutine(unittest.TestCase):
    """The positive control on the one bucket that is silent.

    `bucket_of` is a two-way branch and the second arm is the quiet one, so a
    word added later — or misspelled — would land in ROUTINE and make the node
    quieter than it was without anything failing. This is the test that fails
    when that happens.
    """

    def test_a_word_this_module_has_not_been_taught_is_not_routine(self):
        for word in ("WEIRD", "ok", "", None, "DEGRADED", "WARNING"):
            with self.subTest(verdict=repr(word)):
                self.assertEqual(policy_module.bucket_of(word), "ATTENTION")

    def test_only_the_literal_ok_is_routine(self):
        self.assertEqual(policy_module.bucket_of("OK"), "ROUTINE")

    def test_an_untaught_verdict_lands_in_attention_on_a_real_notice(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(verdict="WEIRD"))
        self.assertEqual(answer["bucket"], "ATTENTION")
        self.assertEqual(answer["reason"], policy_module.MODE_ALWAYS)


class RoutineNeverBuzzes(unittest.TestCase):
    """`prompt` is what the transport may do, and it is deliberately not severity.

    READ FROM THE SHIPPED CONTRACT rather than from a literal here: the value of
    this test is that the file a node actually reads maps ROUTINE to `none`, and
    a copy of the mapping in the test would agree with itself while the contract
    drifted.
    """

    def test_the_shipped_mapping_keeps_the_buzz_for_action_required(self):
        self.assertEqual(policy_module.prompt_for(SHIPPED, "ROUTINE"), "none")
        self.assertEqual(policy_module.prompt_for(SHIPPED, "ATTENTION"), "none")
        self.assertEqual(policy_module.prompt_for(SHIPPED, "ACTION_REQUIRED"),
                         "urgent_app")

    def test_a_routine_notice_asks_the_transport_for_nothing(self):
        answer = decide(at(PLAIN_WORKDAY, 14), latest=row(verdict="OK"))
        self.assertTrue(answer["due"])
        self.assertEqual(answer["prompt"], "none")

    def test_an_untaught_bucket_is_refused_rather_than_defaulted(self):
        # Defaulting to `none` downgrades a notice that needed somebody; to
        # `urgent_app` buzzes for a bucket nobody thought about. Neither is a
        # default this module is willing to make on a reader's behalf.
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.prompt_for(SHIPPED, "CRITICAL")


class TheFaultMessageDeclaresItsOwnBucket(unittest.TestCase):
    """§7④. The one message that escalates must not name its own bucket.

    `card fault` used to write `"ACTION_REQUIRED" if due else "ROUTINE"` and a
    hard-coded `"none"`. Both are answers the policy owns, and a pair spelled
    inside a sender is a pair no reader can look up and no other consumer is
    held to: the policy above could be edited and re-validated without the path
    that actually escalates ever being read.

    READ FROM THE SHIPPED CONTRACT, for the reason `RoutineNeverBuzzes` gives.
    """

    def test_the_shipped_contract_declares_the_bucket_and_the_prompt(self):
        notice = policy_module.fault_notice(SHIPPED)
        self.assertEqual(notice["bucket"], "ACTION_REQUIRED")
        self.assertEqual(notice["prompt"], "none")

    def test_it_is_not_the_notice_mapping_read_a_second_time(self):
        """The two answer about different messages, and the difference is the
        transport's state rather than the reader's importance.

        ACTION_REQUIRED may buzz a NOTICE — that mapping is the whole point of
        the `prompt` table. This path asks for `none` because `urgent_app`
        rides the transport whose last status send is exactly what the finding
        reports as failed. Pinned so that «the fault quietly started asking for
        a buzz» and «the notice table quietly stopped escalating» stay two
        failures instead of one silent convergence.
        """
        self.assertEqual(policy_module.prompt_for(SHIPPED, "ACTION_REQUIRED"),
                         "urgent_app")
        self.assertEqual(policy_module.fault_notice(SHIPPED)["prompt"], "none")

    def test_a_misspelled_bucket_is_refused_rather_than_defaulted(self):
        for bucket in ("ACTION_REQUIRED ", "critical", "", None):
            with self.subTest(bucket=bucket):
                document = self._document(bucket=bucket)
                with self.assertRaises(policy_module.MonitoringPolicyError):
                    policy_module.check(document)

    def test_a_misspelled_prompt_is_refused(self):
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.check(self._document(prompt="urgent"))

    def test_an_absent_fault_block_is_refused(self):
        # Absence is not «no opinion»: a policy that stopped declaring the
        # bucket would leave the command to invent one again, which is the
        # state this block exists to end.
        document = self._document(drop=True)
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.check(document)
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.fault_notice(document)

    def _document(self, *, drop: bool = False, **fault) -> dict:
        notification = dict(SHIPPED["notification"])
        if drop:
            notification.pop("fault", None)
        else:
            notification["fault"] = dict(notification["fault"], **fault)
        return {"schema": policy_module.SCHEMA,
                "status_card": dict(SHIPPED["status_card"]),
                "notification": notification,
                "incident": dict(SHIPPED["incident"])}


class ACalendarThatHasNotDecidedIsNotSilence(unittest.TestCase):
    """Three refusals that look alike and mean different things.

    «今天不是工作日», «现在不在窗口里» and «我不知道今天是什么日子» are three
    different findings, and a single `due: false` would hide two of them. They
    are also the reason the reason codes are a closed set: a reader asking why
    the 14:00 notice did not arrive has to be able to tell «nothing to say» from
    «nobody asked».
    """

    def test_a_rest_day_is_not_due_and_has_no_hour_to_be_identified_by(self):
        answer = decide(at(REST_DAY, 14), latest=row(), created=False)
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.NOT_A_WORKDAY)
        self.assertIsNone(answer["idempotency_key"])
        self.assertIsNone(answer["bucket"])

    def test_an_unknown_calendar_is_reported_as_unknown(self):
        instant = at(PLAIN_WORKDAY, 14)
        answer = policy_module.notice(
            policy=SHIPPED, schedule=no_calendar(instant), latest=row(),
            previous=None, card_version="status@abc123def456",
            device_ref="device:tokyo-shared-dev-01", at=instant, created=False,
            # Handed in even though this answer never reaches the delivery
            # check: a test about the calendar should fail for calendar reasons
            # if the order of the refusals ever moves, and not because the
            # fixture stopped carrying the receipt the later check reads.
            status_receipt=receipt())
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.CALENDAR_UNKNOWN)
        self.assertIsNone(answer["idempotency_key"])

    def test_before_the_window_is_a_different_answer_from_a_rest_day(self):
        answer = decide(at(PLAIN_WORKDAY, 6), latest=row())
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.OUTSIDE_WINDOW)
        # The hour IS known here — the day is a workday and the clock is just
        # early — so the identity exists even though nothing is due.
        self.assertEqual(answer["idempotency_key"],
                         f"status-notice:{_DIGESTED_DEVICE}/CN-Mainland/2031-06-10/06")

    def test_after_the_window_is_the_same_answer_as_before_it(self):
        answer = decide(at(PLAIN_WORKDAY, 21), latest=row())
        self.assertFalse(answer["due"])
        self.assertEqual(answer["reason"], policy_module.OUTSIDE_WINDOW)

    def test_every_reason_this_file_produced_is_in_the_closed_set(self):
        seen = set()
        for instant, kwargs in (
                (at(PLAIN_WORKDAY, 8), {"created": True}),
                (at(PLAIN_WORKDAY, 9), {}),
                (at(PLAIN_WORKDAY, 9), {"delivered": True}),
                (at(PLAIN_WORKDAY, 9), {"mode": "changes-only"}),
                (at(PLAIN_WORKDAY, 9), {"mode": "changes-only", "previous": row()}),
                (at(PLAIN_WORKDAY, 9), {"mode": "attention-only"}),
                (at(REST_DAY, 9), {}),
                (at(PLAIN_WORKDAY, 6), {}),
        ):
            seen.add(decide(instant, latest=row(), **kwargs)["reason"])
        self.assertTrue(seen <= set(policy_module.REASONS),
                        f"出现了词表之外的 reason：{sorted(seen - set(policy_module.REASONS))}")
        # And the walk actually reached several of them, so that a `notice()`
        # stuck on one answer cannot pass this by producing a one-element set.
        self.assertGreaterEqual(len(seen), 6, f"只走到了 {sorted(seen)}")


class ThePolicyIsCheckedNotTrusted(unittest.TestCase):
    """Every vocabulary is closed, and a typo has to fail loudly.

    The failure mode being defended is silence: `mode: change-only` is not a
    validation error in any format, it is just a string that matches nothing —
    and a node whose quiet mode was misspelled is a node that never speaks.
    """

    def contract(self, **notification) -> dict:
        return {"schema": policy_module.SCHEMA,
                "status_card": {"cadence": "hourly",
                                "persistent_card": "one-per-workday"},
                "notification": dict(SHIPPED["notification"], **notification),
                "incident": dict(SHIPPED["incident"])}

    def test_the_shipped_contract_checks(self):
        self.assertIs(policy_module.check(SHIPPED), SHIPPED)

    def test_a_misspelled_mode_is_refused(self):
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.check(self.contract(mode="change-only"))

    def test_a_misspelled_cadence_is_refused(self):
        document = self.contract()
        document["status_card"] = {"cadence": "quarter-hourly",
                                   "persistent_card": "one-per-workday"}
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.check(document)

    def test_a_prompt_mapping_missing_a_bucket_is_refused(self):
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.check(self.contract(prompt={"ROUTINE": "none"}))

    def test_a_modes_list_that_drifted_from_the_vocabulary_is_refused(self):
        # The list exists so that «the mode table was written down somewhere
        # else too» is something a test can catch. A list that merely includes
        # the three words is not that; it has to BE them.
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.check(self.contract(modes=["always", "changes-only"]))

    def test_a_missing_top_level_section_is_refused(self):
        document = self.contract()
        del document["incident"]
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.check(document)

    def test_an_absent_contract_is_an_error_rather_than_an_empty_policy(self):
        # A node with no policy has not decided to be quiet; it has not decided
        # anything, and the two must not read the same from the caller.
        with self.assertRaises(policy_module.MonitoringPolicyError):
            policy_module.load(Path("/nonexistent/monitoring-policy.yaml"))

    def test_every_reason_the_module_can_give_has_a_sentence(self):
        policy_module.check_vocabulary()
        for reason in policy_module.REASONS:
            with self.subTest(reason=reason):
                self.assertTrue(policy_module.SAYS.get(reason),
                                "空白的解释读起来像「没什么可说」")

    def test_a_reason_without_a_sentence_is_refused(self):
        original = policy_module.SAYS
        policy_module.SAYS = {key: value for key, value in original.items()
                              if key != policy_module.CHANGED}
        try:
            with self.assertRaises(policy_module.MonitoringPolicyError):
                policy_module.check_vocabulary()
        finally:
            policy_module.SAYS = original

    def test_a_sentence_for_an_answer_that_cannot_happen_is_refused(self):
        # The other direction, and it is the one that rots: a sentence left
        # behind by a reason that was removed is never printed and never read,
        # so nothing else would ever notice it.
        original = policy_module.SAYS
        policy_module.SAYS = dict(original, SOMETHING_ELSE="一句没人会看到的话")
        try:
            with self.assertRaises(policy_module.MonitoringPolicyError):
                policy_module.check_vocabulary()
        finally:
            policy_module.SAYS = original

    def test_every_answer_carries_a_non_empty_reason_and_sentence(self):
        for instant, kwargs in ((at(PLAIN_WORKDAY, 9), {}),
                                (at(PLAIN_WORKDAY, 9), {"delivered": True}),
                                (at(PLAIN_WORKDAY, 8), {"created": True}),
                                (at(REST_DAY, 9), {}),
                                (at(PLAIN_WORKDAY, 6), {})):
            with self.subTest(instant=instant, **kwargs):
                answer = decide(instant, latest=row(), **kwargs)
                self.assertTrue(answer["reason"])
                self.assertTrue(answer["says"])
                self.assertIn(answer["reason"], policy_module.REASONS)


if __name__ == "__main__":
    unittest.main()
