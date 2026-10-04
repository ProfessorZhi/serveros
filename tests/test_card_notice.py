"""`serverosctl card notice` — whether this tick gets a light message, and its words.

WHAT THIS FILE IS ABOUT
-----------------------
One measured fact: a patched message raises no notification. Through 2026-09-30
the day's card was patched on the hour with every patch delivered, and the phone
stayed silent the whole time — from the phone, an updated card and a monitor
that died at 08:00 are the same thing. So the day gets one CARD and as many
light messages as its hours need, and this command decides the second one.

WHAT IT IS NOT ABOUT, AND WHY THAT IS ASSERTED RATHER THAN ASSUMED
-----------------------------------------------------------------
The notice is not a second status card, and two things keep it from becoming
one: it names the version of the card it is about, and it QUOTES that card's own
row instead of deciding anything. Both are tested here from the outside — the
version is in the document a transport carries, and the row is the one the day
store holds, so a command that re-rendered would be quoting a second reading.
`test_a_notice_never_re_gathers` is that assertion: `card notice` must not call
`card_source.gather`, because a notice that rendered its own hour would be a
second place the state is decided, and the two would disagree on the hour that
mattered.

WHAT IS NOT IN THIS FILE
------------------------
No clock and no transport. Every instant is written down, the daemon is a fake
that records what it was asked, and nothing here sends anything.

The years are SYNTHETIC for the reason `tests/test_business_calendar.py` gives
at length, and the calendar is the same one `tests/test_monitoring_policy.py`
uses so that the two files answer about the same day.
"""
from __future__ import annotations

import contextlib
import importlib.util
import io
import json
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


card_delivery = _load("serveros_runtime_card_delivery",
                      ROOT / "runtime" / "card_delivery.py")
monitoring_policy = _load("serveros_runtime_monitoring_policy",
                          ROOT / "runtime" / "monitoring_policy.py")
source = _load("serveros_runtime_card_source",
               ROOT / "runtime" / "card_source.py")
cli = _load("serveros_runtime_cli_for_notice", ROOT / "runtime" / "cli.py")

#: A synthetic year, and the same one the policy's own tests use.
WORKDAY = "2031-06-10"
REST_DAY = "2031-06-09"

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

#: 08:00–20:00 is the window, so `tick = hour - 8 + 1`. Spelled out here rather
#: than computed, because a test that derived the tick from the same expression
#: as the module would agree with a wrong one.
HOUR_16 = f"{WORKDAY}T16:00:00+08:00"   # tick 9 — the hour already folded
HOUR_17 = f"{WORKDAY}T17:00:00+08:00"   # tick 10 — the hour being asked about
TICK_16, TICK_17 = 9, 10

VERSION = "status@1a2b3c4d5e6f"
DEVICE = "device:tokyo-shared-dev-01"
#: What the device segment becomes once it has to fit beside the day and hour.
#: `status-notice:` is 14 characters and `/CN-Mainland/<workday>/17` is 26, so
#: 10 of the transport's 50 are left for a device whose real ref is 18 — the
#: segment is a digest, not the ref, and `identity()`'s docstring says why
#: (a truncated ref would make two machines' same hour into one notice).
#: A literal, and deliberately so: computing it here would make the assertions
#: below follow the implementation instead of pinning it, so a change to how
#: the segment is compressed has to be an edit in this file too.
DIGESTED_DEVICE = "5ca34bbb29"
NOTICE_KEY_17 = f"status-notice:{DIGESTED_DEVICE}/CN-Mainland/{WORKDAY}/17"


def row(tick: int, hhmm: str, *, verdict: str = "OK",
        observation: str = "新鲜", sections: dict | None = None,
        gaps: int = 0) -> dict:
    """One hour of the day, in `card_day.check_row`'s shape.

    `gaps` is a COUNT and not a list: the row carries `len(health["gaps"])`, so a
    fixture that handed over a tuple would be testing a shape no producer makes.
    """
    return {"tick": tick, "hhmm": hhmm,
            "local": f"{WORKDAY}T{hhmm}:00+08:00",
            "verdict": verdict, "observation": observation,
            "sections": sections or {"mine": "OK", "host": "OK",
                                     "gateway": "UNBOUND"},
            "gaps": gaps}


def day_document(latest: dict | None, history: list[dict] | None = None,
                 *, day_ref: str = f"CN-Mainland/{WORKDAY}") -> dict:
    """The day store as the two commands agree on it. `latest` is the open hour."""
    return {"schema": "serveros/card-day.v1",
            "open": None if latest is None else {
                "day_ref": day_ref, "kind": "WORKDAY",
                "opened_at": f"{WORKDAY}T15:00:00+08:00",
                "updated_at": f"{WORKDAY}T17:00:00+08:00",
                "missed_ticks": 7, "message": None,
                "latest": latest, "history": list(history or [])},
            "previous": None}


def status_row(state: str = "DELIVERED", *, version: str = VERSION,
               at: str = f"{WORKDAY}T17:00:00+08:00") -> dict:
    """One `status` receipt row, in `card_delivery.record`'s shape.

    The fields the command reads are `state` and `version`; the rest are here so
    that the fixture is the row the ledger actually writes rather than a
    convenience with two keys in it.
    """
    return {"kind": "status", "episode": None, "at": at, "state": state,
            "version": version, "payload_hash": "0" * 64, "payload_bytes": 1234,
            # A status send is keyed by the WORKDAY and not by the hour, which
            # is why this key is not the notice's: the two ledgers dedupe at
            # different rhythms, and only the VERSION is comparable between them.
            "idempotency_key": f"status-card:CN-Mainland/{WORKDAY}",
            "remote_id": "synthetic-card-message",
            "delivered": state == "DELIVERED"}


#: The state → finding word, spelled here rather than imported, and that is
#: deliberate: this fixture's job is to BE the daemon's answer, so a test that
#: borrowed the producer's own vocabulary to build the answer would agree with
#: it about a word that neither of them ever puts on the wire. Which words the
#: producer writes is `test_card_delivery.TheDeliveryFinding`'s question; what
#: this file pins is what the COMMAND does with an answer that carries one.
FINDING_WORD = {"FAILED": "DELIVERY_FAILED", "UNKNOWN": "DELIVERY_UNKNOWN",
                "UNSUPPORTED": "DELIVERY_UNSUPPORTED"}


def delivery_finding(row: dict | None) -> dict:
    """`card_delivery.delivery_finding`'s answer, for this fixture's row."""
    state = str((row or {}).get("state") or "")
    if row is None:
        finding = "DELIVERY_NOT_ATTEMPTED"
    elif state == "DELIVERED":
        finding = None
    else:
        finding = FINDING_WORD.get(state, f"DELIVERY_{state}")
    return {"kind": "status", "finding": finding,
            "fault": finding in ("DELIVERY_FAILED", "DELIVERY_UNKNOWN",
                                 "DELIVERY_UNSUPPORTED"),
            "state": state or None, "version": (row or {}).get("version"),
            "at": (row or {}).get("at"),
            "remote_id": (row or {}).get("remote_id"),
            "says": f"（{finding}）" if finding else None}


def status_ledger(row: dict | None = None) -> dict:
    """What the daemon answers for `kind: status` — newest row of any state.

    No argument is the ordinary node, and `empty_status_ledger()` is the other
    case by its own name rather than by an argument whose absence would have to
    mean two things at once.
    """
    row = status_row() if row is None else row
    return {"kind": "status", "entries": 1, "recent": [row], "last": row,
            "last_delivered": row if row.get("state") == "DELIVERED" else None,
            # The daemon answers `findings` for every kind in one payload, and
            # `status` is the only one this command reads. The other three are
            # left out rather than faked: an answer that carried them would be
            # this fixture claiming to know what the daemon says about kinds no
            # test here exercises.
            "findings": {"status": delivery_finding(row)}}


def empty_status_ledger() -> dict:
    """A ledger that holds no `status` receipt at all.

    Not a defect: it is what this node's ledger looks like before a card has
    been recorded, and the command has to tell it apart from a card that was
    sent and refused.
    """
    return {"kind": "status", "entries": 0, "recent": [], "last": None,
            "last_delivered": None, "findings": {"status": delivery_finding(None)}}


class TheCommandAndItsRefusals(unittest.TestCase):
    """Driven through the command's own parser, with the daemon faked.

    The fake is the daemon and nothing else: the parser, the flags, the store,
    the calendar and the renderer are the ones that ship. What this catches is
    an argument the parser never declares or a keyword the caller forgets — both
    invisible until somebody runs it on the node.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="card-notice-cli-"))
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.calendar = self.dir / "business-calendar.yaml"
        self.calendar.write_text(CALENDAR, encoding="utf-8")
        self.sent: list[tuple] = []
        #: Two ledgers, because the command now reads two: this hour's NOTICE
        #: receipts and the day's STATUS card receipts. They are separate kinds
        #: in one document and they answer different questions, so a fake that
        #: answered one dict for both would silently test neither.
        self.ledger = {"kind": "notice", "entries": 0, "recent": [],
                       "last": None, "last_delivered": None}
        #: The card's own ledger. The default is the ordinary node: the patch
        #: that carried this hour's card arrived, and this hour's notice is
        #: being asked about. The other cases are named in the tests below.
        self.card = status_ledger()
        self._real_ask = cli.ask_daemon

        def answer(method, params=None, **kw):
            self.sent.append((method, params))
            return dict(self.card if (params or {}).get("kind") == "status"
                        else self.ledger)

        cli.ask_daemon = answer
        self.addCleanup(setattr, cli, "ask_daemon", self._real_ask)
        #: `card notice` must not gather. Stubbed to explode rather than to
        #: count, so a command that started rendering stops the test instead of
        #: quietly passing one more time.
        self._real_gather = source.gather

        def forbidden(*a, **k):
            raise AssertionError(
                "`card notice` gathered the machine. It quotes the row the "
                "day store holds; a notice that took its own reading would be "
                "a second place the state is decided")

        source.gather = forbidden
        self.addCleanup(setattr, source, "gather", self._real_gather)

    def store(self, document: dict, name: str = "day.json") -> Path:
        path = self.dir / name
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = cli.main(list(argv))
            except SystemExit as exit_:   # argparse refuses, and says why
                code = int(exit_.code or 0)
        return code, out.getvalue()

    def notice(self, store: Path, at: str, *extra, json_: bool = False):
        argv = ["card", "notice", "--card-version", VERSION,
                "--device-ref", DEVICE, "--business-calendar", str(self.calendar),
                "--day-store", str(store), "--at", at, *extra]
        if json_:
            argv.append("--json")
        return self.run_cli(*argv)

    def delivered(self, key: str) -> None:
        """The ledger as it looks after a notice of this key has gone out."""
        self.ledger = {"kind": "notice", "entries": 1, "recent": [],
                       "last": None,
                       "last_delivered": {"kind": "notice", "state": "DELIVERED",
                                          "version": "notice@83e2e1bd8f4a",
                                          "idempotency_key": key,
                                          "remote_id": "synthetic-message"}}

    # -- the tick that needs no notice ------------------------------------

    def test_the_first_hour_of_a_day_needs_no_second_message(self):
        """The card arrives NEW at its first tick, so that hour's notification
        already happened. A notice here would make 08:00 the one hour with two
        messages about it — and this is the bug the live run caught: asked after
        `card day-advance`, `card_day.decide` reads the day as already created
        and answers PATCH, so «was this tick the creation» has to come from the
        document rather than from that call.
        """
        store = self.store(day_document(row(TICK_17, "17:00")))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("不发/ON_CREATE_CARD_MESSAGE", text)
        self.assertIn("当天卡本身是这一小时的新消息", text)

    def test_a_day_that_already_folded_an_hour_gets_told(self):
        """The same store shape with one folded hour under it is a PATCH tick,
        and that is exactly the case the whole command exists for."""
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("发/MODE_ALWAYS", text)

    def test_a_calendar_that_has_not_decided_a_tick_has_no_identity(self):
        """`None == None` on two absences must not read as «the card was built
        now». A rest day leaves no tick, and the answer is about the calendar."""
        store = self.store(day_document(None))
        code, text = self.notice(store, f"{REST_DAY}T10:00:00+08:00")
        self.assertEqual(code, 0, text)
        self.assertIn("不发/NOT_A_WORKDAY", text)
        self.assertIn("（没有：这一拍没有可命名的时点）", text)

    # -- the tick the card does not hold ----------------------------------

    def test_an_hour_the_card_does_not_hold_is_refused(self):
        """The notice names one hour and quotes the row of another. Refused
        rather than sent with the mismatch smoothed over, and the fix is named.
        """
        store = self.store(day_document(row(TICK_17, "17:00")))
        code, text = self.notice(store, HOUR_16)
        self.assertEqual(code, 1, text)
        self.assertIn("card day-advance", text)
        # Both ordinals are printed, and they are different numbers: which of
        # them is «this tick» and which is «the card» is the whole content of
        # the refusal. `TICK_16` is 9 rather than 8 because the window opens at
        # 08:00 — the arithmetic is spelled out at the top of the file.
        self.assertIn(f"第 {TICK_16} 个", text)   # this tick
        self.assertIn(f"第 {TICK_17} 个", text)   # what the card holds

    def test_yesterday_is_not_today(self):
        """A tick ordinal is the same number on every workday, so a store left
        over from another day passes a tick-only test. It is the same defect one
        day wide, and the day is compared for that reason.
        """
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")],
                                        day_ref="CN-Mainland/2031-06-09"))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 1, text)
        self.assertIn("CN-Mainland/2031-06-09", text)
        self.assertIn(f"CN-Mainland/{WORKDAY}", text)

    def test_the_printed_hour_comes_from_this_tick_and_not_from_the_card(self):
        """A row that disagrees with its own tick, so the two sources are told
        apart: 「那一拍」 is this tick's answer and 「卡上那一行」 is what the
        card holds. Reading both from one of them printed «17:00 · 第 9/13 小时»
        — a coherent sentence about an hour that does not exist.
        """
        store = self.store(day_document(row(TICK_17, "16:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn(f"那一拍      CN-Mainland/{WORKDAY} · 17:00 · 第 10/13 小时",
                      text)
        self.assertIn("卡上那一行  16:00", text)

    # -- what the ledger is asked ------------------------------------------

    def test_the_ledger_is_asked_about_this_hours_notice(self):
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        asked = [params["kind"] for method, params in self.sent
                 if method == "card.deliveries"]
        # Both, and in this order: the card arrives, and only then is this
        # hour's notice a question with an answer. Asserting the whole list
        # rather than the last call keeps the order itself under test — a
        # reversal here would mean composing a notice identity before knowing
        # whether its card was ever delivered.
        self.assertEqual(asked, ["status", "notice"])

    # -- §7: the card has to have arrived --------------------------------

    def test_a_patch_that_did_not_arrive_produces_no_notice(self):
        """The order is enforced, and this is the case it exists for.

        A PATCH that FAILED leaves the card in the chat at the previous version.
        The notice's own line is «当天卡已更新 · <version>»; sending it here
        would tell a reader the update succeeded when it did not, which is worse
        than the silence the notice was invented to break.
        """
        self.card = status_ledger(status_row("FAILED"))
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("不发/CARD_NOT_DELIVERED", text)
        self.assertNotIn("发/MODE_ALWAYS", text)
        # And the line a person reads says what was found rather than only that
        # something was refused.
        self.assertIn("卡的回执    FAILED", text)

    def test_a_failed_patch_is_not_announced_on_an_older_cards_strength(self):
        """Why the newest row of ANY state is read, and not the newest arrival.

        A card whose reading did not change is re-patched with the SAME version,
        and that patch fails. The ledger then holds a DELIVERED row for this
        version from the hour before, and a FAILED row after it. A command that
        asked for «the newest DELIVERED status receipt» would find the older
        one, see its version match, and announce an update that did not happen —
        by exactly the mechanism this precondition exists to stop. So `last` is
        what is read, whatever state it is in, and the same version in an older
        row does not stand in for this hour's send.
        """
        older = status_row(at=f"{WORKDAY}T16:00:00+08:00")
        failed = status_row("FAILED", at=f"{WORKDAY}T17:00:00+08:00")
        self.card = {"kind": "status", "entries": 2,
                     "recent": [failed, older], "last": failed,
                     "last_delivered": older,
                     "findings": {"status": delivery_finding(failed)}}
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("不发/CARD_NOT_DELIVERED", text)
        self.assertNotIn("发/MODE_ALWAYS", text)

    def test_a_refusal_names_the_delivery_finding_and_not_only_the_rule(self):
        """§7①. The refusal has to say WHAT happened, in a sentence.

        `不发/CARD_NOT_DELIVERED` is the rule the command applied. What a person
        has to act on is the finding underneath it — a send that did not arrive —
        and that sentence is the difference between a refusal and a diagnosis.
        It is read from the same round trip as the receipt, so the two lines
        cannot describe two different rows.
        """
        self.card = status_ledger(status_row("FAILED"))
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("投递发现    DELIVERY_FAILED", text)
        self.assertIn("没有到达", text)   # the finding's own sentence, not a label
        code, text = self.notice(store, HOUR_17, json_=True)
        self.assertEqual(code, 0, text)
        payload = json.loads(text)
        self.assertTrue(payload["delivery_finding"]["fault"])
        self.assertEqual(payload["delivery_finding"]["finding"],
                         "DELIVERY_FAILED")
        # The row the finding is about is the same row the decision was checked
        # against. One round trip, one answer.
        self.assertEqual(payload["delivery_finding"]["at"],
                         payload["status_receipt"]["at"])

    def test_an_ordinary_hour_carries_no_delivery_finding_line(self):
        """A green hour must not print one, and the reason is not symmetry.

        An hour whose card has not been sent yet is `DELIVERY_NOT_ATTEMPTED`,
        which is the shape of every hour before its patch — so a line saying so
        would print thirteen times a day and the eye that learns to skip it
        skips the one that says a send failed.
        """
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("发/MODE_ALWAYS", text)
        self.assertNotIn("投递发现", text)

    def test_a_notice_is_not_composed_for_a_version_nobody_received(self):
        """A card arrived; it was not this one.

        The version is what the notice points at, so a notice naming a version
        the ledger never saw delivered would point at nothing — the thing that
        makes a notice a card.
        """
        self.card = status_ledger(status_row(version="status@ffffffffffff"))
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("不发/CARD_VERSION_NOT_DELIVERED", text)
        self.assertIn("status@ffffffffffff", text)

    def test_an_empty_status_ledger_is_a_refusal_and_not_a_quiet_no(self):
        """«Nobody can say this card arrived» is not «this card arrived».

        A node whose ledger holds no `status` rows at all — the state of this
        node before any card has been recorded — has nothing to check the
        notice's words against, and that is reported as the refusal it is.
        """
        self.card = empty_status_ledger()
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("不发/CARD_NOT_DELIVERED", text)
        self.assertIn("（台账里没有 status 回执）", text)

    def test_a_rest_day_is_answered_by_the_calendar_and_not_by_the_ledger(self):
        """The delivery question is asked after the calendar.

        No status card is sent on a rest day, so there is nothing to check — and
        a command that asked anyway would report a delivery fault on every rest
        day of the year, which is how a real one stops being read.
        """
        self.card = status_ledger(status_row("FAILED"))
        store = self.store(day_document(None))
        code, text = self.notice(store, f"{REST_DAY}T10:00:00+08:00")
        self.assertEqual(code, 0, text)
        self.assertIn("不发/NOT_A_WORKDAY", text)
        # The DECISION line, not the whole output: the closing paragraph names
        # both refusal reasons as prose, so a substring test over everything
        # would fail on the explanation of a refusal that did not happen.
        self.assertNotIn("不发/CARD_NOT_DELIVERED", text)

    def test_an_announced_hour_is_not_announced_twice(self):
        """Per-tick idempotency, and it is the LEDGER that answers it rather
        than a hope that the service collapses two identical sends."""
        self.delivered(NOTICE_KEY_17)
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("不发/ALREADY_DELIVERED", text)

    def test_another_hours_receipt_does_not_silence_this_one(self):
        """The key is compared and not merely «a notice exists». A ledger that
        holds 16:00 is not a reason to stay quiet at 17:00, and a node that read
        it that way would announce the first hour of a day and no other."""
        self.delivered(NOTICE_KEY_17.replace("/17", "/16"))
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn("发/MODE_ALWAYS", text)

    def test_a_ledger_nobody_can_read_is_not_an_unsent_notice(self):
        """«No notice went out» and «nobody can say» are different answers, and
        returning the second as the first is how a duplicate is sent by the
        command whose whole purpose is not to be one."""
        def refuse(method, params=None, **kw):
            self.sent.append((method, params))
            raise cli.DaemonUnavailable("no node runtime at the socket")

        cli.ask_daemon = refuse
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 2, text)
        self.assertNotIn("发/MODE_ALWAYS", text)

    # -- the words, and the envelope a sender carries ----------------------

    def test_the_words_name_the_card_and_the_place_in_the_day(self):
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17)
        self.assertEqual(code, 0, text)
        self.assertIn(f"当天卡已更新 · {VERSION}", text)
        self.assertIn("状态更新 17:00 · 第 10/13 小时", text)
        # The row is quoted, not re-worded: `observation` is the store's own
        # short word and not a sentence this command composed.
        self.assertIn("正常 · 新鲜", text)

    def test_the_frozen_envelope_is_the_one_every_other_kind_sends(self):
        """`{card, frozen}`, so `card record-delivery --kind notice` works with
        no second envelope reader. The version covers the WORDS, because what
        goes to a chat is the words and not the decision behind them."""
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17, "--with-frozen", json_=True)
        self.assertEqual(code, 0, text)
        envelope = json.loads(text)
        self.assertEqual(sorted(envelope), ["card", "frozen"])
        frozen = envelope["frozen"]
        self.assertEqual(frozen["kind"], "notice")
        self.assertEqual(frozen["version"], f"notice@{frozen['payload_hash'][:12]}")
        # The dedupe key the receipt will be filed under is the tick's own, and
        # not the instant: `freeze_card`'s default names the instant, and a
        # re-run of the same hour would then be a second send.
        self.assertEqual(frozen["idempotency_key"], NOTICE_KEY_17)
        self.assertEqual(envelope["card"]["idempotency_key"], NOTICE_KEY_17)
        card_delivery.verify(frozen, envelope["card"])

    def test_the_version_covers_the_words_and_not_the_decision_alone(self):
        """Two ticks whose decisions differ only in their `text` must not share
        a version. They cannot today — `text` is a function of the decision —
        and this asserts the property rather than the coincidence: the payload
        that was hashed is the one with the words in it.
        """
        store = self.store(day_document(row(TICK_17, "17:00"),
                                        [row(TICK_16, "16:00")]))
        code, text = self.notice(store, HOUR_17, "--with-frozen", json_=True)
        self.assertEqual(code, 0, text)
        envelope = json.loads(text)
        self.assertIn("text", envelope["card"])
        self.assertEqual(envelope["card"]["text"],
                         envelope["card"]["text"].strip())
        self.assertIn("当天卡已更新", envelope["card"]["text"])

    def test_a_tick_that_will_not_be_sent_has_no_version(self):
        """A version of a notice nobody will send is a version of nothing, and a
        receipt filed against it would make the next firing of that hour read
        «already announced» and stay silent."""
        store = self.store(day_document(row(TICK_17, "17:00")))
        code, text = self.notice(store, HOUR_17, "--with-frozen", json_=True)
        self.assertEqual(code, 1, text)
        self.assertIn("没有可冻结的提醒", text)
        self.assertNotIn("\"frozen\"", text)

    def test_the_card_version_is_required_rather_than_defaulted(self):
        """A notice that cannot name the card it is about is a second status
        card, so there is no version to fall back on."""
        code, text = self.run_cli("card", "notice", "--device-ref", DEVICE)
        self.assertEqual(code, 2, text)
        self.assertIn("--card-version", text)

    def test_the_device_is_required_because_the_identity_names_it(self):
        """Two machines' same hour must not dedupe into one message."""
        code, text = self.run_cli("card", "notice", "--card-version", VERSION)
        self.assertEqual(code, 2, text)
        self.assertIn("--device-ref", text)


class TheNoticeNeverGathersItsOwnReading(unittest.TestCase):
    """The row a notice quotes is the CARD's, and the card is the day store.

    Stated as its own class because it is the load-bearing half of «a notice is
    not a second card»: a notice that took its own reading would be a second
    place the state is decided, and the two would disagree on the hour that
    mattered. The assertion is that `card_source.gather` is never reached — the
    stub in `TheCommandAndItsRefusals` raises if it is, and this one proves the
    stub would have fired.
    """

    def test_the_stub_that_guards_it_really_does_fire(self):
        before = source.gather

        def forbidden(*a, **k):
            raise AssertionError("gathered")

        source.gather = forbidden
        try:
            with self.assertRaises(AssertionError):
                source.gather()
        finally:
            source.gather = before


if __name__ == "__main__":
    unittest.main()
