"""`serverosctl card fault` — what a person is told when the card did not arrive.

WHAT THIS FILE IS ABOUT
-----------------------
§7① has two halves and they fail in opposite directions. The first — «PATCH
FAILED 不得发送「状态已更新」» — is a refusal, and a refusal is silent; on its own
it reproduces the defect the whole section was written against, where something
happened and the phone said nothing. The second half is «应形成投递
finding/incident»: the failure has to become something a person is told. This
file is about that half.

WHAT IT IS NOT ABOUT
--------------------
Not about whether the send failed. That is `card_delivery.delivery_finding`'s
reading of the ledger and `tests/test_card_delivery.py` pins it; here the
daemon is a fake and the finding is a given. What is pinned here is the seam:
that the command READS that finding rather than re-deciding it, that the words
it composes carry no reading, and that a card that DID arrive gets no fault
message at all.

No clock and no transport: every instant is written down, the daemon records
what it was asked, and nothing here sends anything.

The year is synthetic, for the reason `tests/test_business_calendar.py` gives at
length.
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
cli = _load("serveros_runtime_cli_for_fault", ROOT / "runtime" / "cli.py")

WORKDAY = "2031-06-10"
AT = f"{WORKDAY}T17:00:00+08:00"
VERSION = "status@1a2b3c4d5e6f"
DEVICE = "device:tokyo-shared-dev-01"
SHORT_DEVICE = "tokyo-shared-dev-01"

#: The key the failed attempt gets. Built from the FAILED ROW's own instant and
#: not from the hour, which is the decision this constant exists to pin: a key
#: made of the hour would let a second failure inside one hour read as a re-run
#: of the first.
#:
#: The device segment is a DIGEST and a literal, for the reason
#: `test_card_notice.DIGESTED_DEVICE` gives: `delivery-fault:` plus a verbatim
#: ISO-8601 instant leaves 9 characters, the real ref is 19, and computing this
#: here would make the assertion follow the implementation instead of pinning
#: it. 15 + 9 + 26 is exactly the transport's 50 with nothing to spare.
FAULT_KEY = f"delivery-fault:5ca34bbb2/{AT}"

#: The sentence the finding carries, spelled here rather than imported from
#: `card_delivery.SAYS` — a test that quoted the producer's own words would
#: agree with it about a sentence that never reached a person. What the command
#: must do with a finding is the question; which sentence the finding has is
#: `tests/test_card_delivery.py`'s.
SAYS = "这一版卡片没有到达：投递台账里最后一次投递记着 FAILED。"


def receipt(state: str = "FAILED", *, version: str = VERSION) -> dict:
    return {"kind": "status", "episode": None, "at": AT, "state": state,
            "version": version, "payload_hash": "0" * 64, "payload_bytes": 1234,
            "idempotency_key": f"status-card:CN-Mainland/{WORKDAY}",
            "remote_id": None}


def finding(state: str = "FAILED", *, version: str = VERSION,
            fault: bool = True, says: str = SAYS) -> dict:
    word = None if state == "DELIVERED" else f"DELIVERY_{state}"
    return {"kind": "status", "finding": word, "fault": fault, "state": state,
            "version": version, "at": AT, "remote_id": None, "says": says}


def ledger(row: dict | None, item: dict | None) -> dict:
    return {"kind": "status", "entries": 1 if row else 0,
            "recent": [row] if row else [], "last": row,
            "last_delivered": row if (row or {}).get("state") == "DELIVERED"
            else None,
            "findings": {"status": item or {}}}


class TheFaultMessage(unittest.TestCase):
    """Driven through the command's own parser, with the daemon faked."""

    def setUp(self):
        self.sent: list[tuple] = []
        self.ledger = ledger(receipt(), finding())
        self._real_ask = cli.ask_daemon

        def answer(method, params=None, **kw):
            self.sent.append((method, params))
            return dict(self.ledger)

        cli.ask_daemon = answer
        self.addCleanup(setattr, cli, "ask_daemon", self._real_ask)
        # The same guard `test_card_notice.py` uses: this command quotes the
        # ledger and must not take its own reading of the machine. Stubbed to
        # explode rather than to count, so a command that started gathering
        # stops the test instead of quietly passing one more time.
        self._real_gather = source.gather

        def forbidden(*a, **k):
            raise AssertionError(
                "`card fault` gathered the machine. It says what the LEDGER "
                "records about a send; a fault message that took its own "
                "reading would be a second place the state is decided")

        source.gather = forbidden
        self.addCleanup(setattr, source, "gather", self._real_gather)

    def run_cli(self, *argv) -> tuple[int, str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            try:
                code = cli.main(list(argv))
            except SystemExit as exit_:
                code = int(exit_.code or 0)
        return code, out.getvalue()

    def fault(self, *extra, json_: bool = False):
        argv = ["card", "fault", "--device-ref", DEVICE, "--at", AT, *extra]
        if json_:
            argv.append("--json")
        return self.run_cli(*argv)

    # -- the case it exists for -------------------------------------------

    def test_a_failed_patch_gets_words_and_the_ledger_is_asked_once(self):
        code, text = self.fault()
        self.assertEqual(code, 0, text)
        self.assertIn("发/DELIVERY_FAILED", text)
        self.assertIn("当天卡没有送达", text)
        # The finding's own sentence, and not a label standing in for it.
        self.assertIn(SAYS, text)
        # One round trip. Two would let a send land between the reading and the
        # decision, so the refusal would be justified by a row that is gone.
        self.assertEqual([m for m, _ in self.sent], ["card.deliveries"])

    def test_the_message_names_the_version_that_did_not_arrive(self):
        """The two facts a repair needs, and the only two it carries.

        A fault message that reproduced the card's numbers would be a second
        copy of a card which, by construction, is not the one in the chat.
        """
        _code, text = self.fault()
        self.assertIn(f"那一版 {VERSION}", text)
        self.assertIn(f"{AT} 投递", text)

    def test_the_key_is_the_failed_attempt_and_not_the_hour(self):
        """Why `fault_key` takes an instant rather than a schedule.

        Keyed by the hour, a second failure inside one hour would look like a
        re-run of the first and the transport would drop it — a real second
        failure silently absorbed by a dedupe rule about a different question.
        """
        _code, text = self.fault()
        self.assertIn(f"去重身份    {FAULT_KEY}", text)

    def test_it_asks_for_nothing_from_the_transport(self):
        """`urgent_app` rides the transport that just failed.

        The bucket is ACTION_REQUIRED because a card that did not arrive stays
        not-arrived until somebody looks; the prompt is still `none`, because
        the one escalation available would have to arrive through the path that
        is known to be broken.
        """
        _code, text = self.fault()
        self.assertIn("ACTION_REQUIRED", text)
        self.assertIn("/ none", text)
        self.assertNotIn("urgent_app", text)

    def test_the_bucket_is_read_from_the_policy_and_not_spelled_here(self):
        """§7④. The command used to write `"ACTION_REQUIRED" if due else
        "ROUTINE"` and a hard-coded `"none"` — two answers the policy owns.

        A literal agrees with the contract on the day it is written, which is
        exactly the property reading it back cannot check. So this test MOVES
        the policy and asserts the command follows: a reintroduced literal
        fails it whatever value the literal is given.
        """
        document = dict(monitoring_policy.load())
        notification = dict(document["notification"])
        notification["fault"] = {"bucket": "ATTENTION", "prompt": "none"}
        document = dict(document, notification=notification)
        real_load = monitoring_policy.load
        monitoring_policy.load = lambda path=None: document
        self.addCleanup(setattr, monitoring_policy, "load", real_load)
        code, text = self.fault()
        self.assertEqual(code, 0, text)
        self.assertIn("ATTENTION", text)
        self.assertNotIn("ACTION_REQUIRED", text)

    def test_a_tick_that_sends_nothing_names_no_bucket(self):
        """A bucket word about a message that will not exist is a field filled
        with something plausible, and it reads like a decision somebody made.

        `ROUTINE` was that word: a refusal printed as a quiet approval.
        """
        self.ledger = ledger(receipt("DELIVERED"),
                             finding("DELIVERED", fault=False, says=None))
        code, text = self.fault()
        self.assertEqual(code, 0, text)
        self.assertIn("桶 / 提醒   （不发", text)
        self.assertNotIn("ROUTINE", text)

    # -- the case it must refuse ------------------------------------------

    def test_a_card_that_arrived_gets_no_message(self):
        self.ledger = ledger(receipt("DELIVERED"),
                             finding("DELIVERED", fault=False, says=None))
        code, text = self.fault()
        self.assertEqual(code, 0, text)
        self.assertIn("不发/", text)
        self.assertNotIn("当天卡没有送达", text)

    def test_a_ledger_with_no_status_row_says_nobody_attempted_it(self):
        """«Nobody tried» and «the transport failed» are different repairs.

        A node before its first send has no fault to announce, and reporting one
        would make the first hour of every day an alarm.
        """
        item = {"kind": "status", "finding": "DELIVERY_NOT_ATTEMPTED",
                "fault": False, "state": None, "version": None, "at": None,
                "remote_id": None, "says": "（没有记录）"}
        self.ledger = ledger(None, item)
        code, text = self.fault()
        self.assertEqual(code, 0, text)
        self.assertIn("不发/DELIVERY_NOT_ATTEMPTED", text)
        self.assertNotIn("当天卡没有送达", text)

    def test_a_message_that_was_never_due_has_no_version(self):
        """A frozen block for a message nobody will send is a version of
        nothing, and a receipt recorded against it would make the next tick
        read «already said» about a thing never said."""
        self.ledger = ledger(receipt("DELIVERED"),
                             finding("DELIVERED", fault=False, says=None))
        code, text = self.fault("--with-frozen")
        self.assertNotEqual(code, 0)
        self.assertIn("没有可冻结的东西", text)

    # -- the envelope ------------------------------------------------------

    def test_the_frozen_envelope_is_the_one_every_other_kind_sends(self):
        """The same `{card, frozen}` shape, under its own kind.

        Its own kind and not `notice`: `KEEP` is per kind, and a receipt filed
        under a name that does not describe it is a receipt the next reader
        finds while looking for something else.
        """
        _code, decided = self.fault(json_=True)
        code, text = self.fault("--with-frozen", json_=True)
        self.assertEqual(code, 0, text)
        payload = json.loads(text)
        self.assertEqual(payload["frozen"]["kind"], card_delivery.FAULT)
        # `fault@` and NOT `status@`: the version names the message that was
        # sent, and a fault message frozen under the failed card's version would
        # make the next reader take it for a second send of that card.
        self.assertTrue(payload["frozen"]["version"].startswith("fault@"),
                        payload["frozen"]["version"])
        # The words go INTO the hashed payload: what reaches a chat is the
        # sentence, so the version a receipt names has to cover it. Compared
        # against the SAME command without the envelope, so this pins the two
        # runs to one another rather than to a literal here.
        self.assertEqual(payload["card"]["text"],
                         json.loads(decided)["text"])
        self.assertIn(SAYS, payload["card"]["text"])

    def test_a_fault_receipt_may_not_carry_an_episode(self):
        """It is about one failed ATTEMPT, not about an incident.

        An episode here would make `carries` answer «the incident's card» with
        a fault message's receipt, and the next incident patch would aim at the
        message that says nothing arrived.
        """
        frozen = card_delivery.freeze_card(
            card_delivery.FAULT, {"text": SAYS}, at=AT,
            idempotency_key=FAULT_KEY)
        path = Path(tempfile.mkdtemp(prefix="card-fault-")) / "delivery.json"
        self.addCleanup(shutil.rmtree, path.parent, ignore_errors=True)
        delivered = card_delivery.CardLedger(path)
        with self.assertRaises(card_delivery.CardDeliveryError):
            delivered.record(card_delivery.FAULT, at=AT, state="DELIVERED",
                             version=frozen["version"], episode="abc123def456")
        # And the symmetric half: the kind IS recordable without one.
        delivered.record(card_delivery.FAULT, at=AT, state="DELIVERED",
                         version=frozen["version"],
                         idempotency_key=FAULT_KEY)
        self.assertIsNone(delivered.delivery_finding(card_delivery.FAULT)
                          ["finding"])


class TheWordsCarryNoReading(unittest.TestCase):
    """The renderer, tested without the command around it."""

    def setUp(self):
        self.cards = _load("serveros_runtime_cards_for_fault",
                           ROOT / "runtime" / "cards.py")

    def test_the_message_is_three_lines_and_names_no_measurement(self):
        lines = self.cards.fault_lines(finding(), node_label=DEVICE)
        self.assertEqual(len(lines), 3)
        self.assertEqual(lines[1], SAYS)
        # No percentage, no verdict word, no "current" anything: the numbers a
        # reader is missing are the card's, and reproducing them here would be
        # a second copy of the card that is not in the chat.
        self.assertNotIn("%", "\n".join(lines))
        for word in ("CPU", "内存", "磁盘", "Ready", "新鲜"):
            self.assertNotIn(word, "\n".join(lines))

    def test_a_non_fault_refuses_to_render_rather_than_previewing(self):
        for item in (finding("DELIVERED", fault=False, says=None), {},
                     {"finding": "DELIVERY_NOT_ATTEMPTED", "fault": False}):
            with self.subTest(item=item):
                with self.assertRaises(Exception):
                    self.cards.fault_lines(item, node_label=DEVICE)


class TheKeyIsAboutOneAttempt(unittest.TestCase):
    """`monitoring_policy.fault_key`, which the command calls."""

    def test_two_failures_in_one_hour_are_two_keys(self):
        first = monitoring_policy.fault_key(DEVICE, f"{WORKDAY}T17:00:00+08:00")
        second = monitoring_policy.fault_key(DEVICE, f"{WORKDAY}T17:40:00+08:00")
        self.assertNotEqual(first, second)

    def test_the_same_attempt_rerun_is_one_key(self):
        self.assertEqual(monitoring_policy.fault_key(DEVICE, AT),
                         monitoring_policy.fault_key(DEVICE, AT))

    def test_an_instant_is_required(self):
        with self.assertRaises(monitoring_policy.MonitoringPolicyError):
            monitoring_policy.fault_key(DEVICE, "")

    def test_a_device_is_required(self):
        with self.assertRaises(monitoring_policy.MonitoringPolicyError):
            monitoring_policy.fault_key("", AT)

    def test_a_long_device_is_compressed_rather_than_the_instant_cut(self):
        """The measured case: this node's own ref and a verbatim instant are 60
        characters against the transport's 50, so the first version of this
        function refused on the node it was written for.

        The instant survives and the device becomes a digest, because the
        instant is what tells one failed attempt from the next.
        """
        key = monitoring_policy.fault_key(DEVICE, AT)
        self.assertLessEqual(len(key),
                             monitoring_policy.TRANSPORT_KEY_MAX)
        self.assertTrue(key.endswith(f"/{AT}"))
        self.assertNotIn(SHORT_DEVICE, key)

    def test_an_instant_with_no_room_is_refused_rather_than_cut(self):
        """Cutting the instant would let two failures in one hour share a name,
        and the transport would drop the second as a re-run of the first."""
        with self.assertRaises(monitoring_policy.MonitoringPolicyError) as got:
            monitoring_policy.fault_key(DEVICE, AT + "." + "9" * 40)
        self.assertIn("不给键", str(got.exception))


if __name__ == "__main__":
    unittest.main()
