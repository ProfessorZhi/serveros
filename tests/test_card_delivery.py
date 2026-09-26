"""§3 — the version of a card that was sent, and the receipt that says so.

§3 asks for two rhythms over one set of facts — a status broadcast a clock
produces and an alert digest a delta produces — sharing the facts and the
presentation rules but NOT the send-trigger semantics. Two things have to be
true for that to hold, and this file is those two things:

    the two rhythms keep separate ledgers, and
    every send saves the version of the payload it sent.

The second is only worth anything if the version and the payload belong to one
another, so `verify` recomputes the hash from the card and the tests below
exercise both the agreeing case and the forged one.
"""
from __future__ import annotations

import importlib.util
import json
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
alert_digest = _load("serveros_runtime_alert_digest_for_cards",
                     ROOT / "runtime" / "alert_digest.py")

AT = "2026-09-22T14:40:00+08:00"
STATE = "/state/alerts/state.json"


def card(**over):
    payload = {
        "schema": "2.0",
        "config": {"wide_screen_mode": True},
        "header": {"template": "grey",
                   "title": {"tag": "plain_text", "content": "本机 · 资源播报"}},
        "body": {"elements": [{"tag": "markdown", "content": "CPU 3.2%"}]},
    }
    payload.update(over)
    return payload


class Fixture(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="card-delivery-test-"))
        self.path = self.dir / "cards" / "delivery.json"
        self.ledger = card_delivery.CardLedger(self.path)

    def tearDown(self):
        import shutil
        shutil.rmtree(self.dir, ignore_errors=True)

    def frozen(self, *, kind="status", payload=None, at=AT, key=None):
        return card_delivery.freeze_card(kind, payload or card(), at=at,
                                        idempotency_key=key)

    def record(self, **over):
        payload = self.frozen()
        params = {"kind": payload["kind"], "at": AT, "state": "DELIVERED",
                  "version": payload["version"],
                  "payload_hash": payload["payload_hash"],
                  "payload_bytes": payload["payload_bytes"],
                  "idempotency_key": payload["idempotency_key"]}
        params.update(over)
        return self.ledger.record(**params)


# ---------------------------------------------------------------------------
# the version names the payload
# ---------------------------------------------------------------------------


class TheVersionNamesThePayload(unittest.TestCase):
    def test_the_version_is_a_hash_of_the_card_and_nothing_else(self):
        frozen = card_delivery.freeze_card("status", card(), at=AT)
        self.assertEqual(frozen["version"],
                         f"status@{frozen['payload_hash'][:12]}")
        self.assertEqual(frozen["payload_bytes"],
                         len(card_delivery.canonical(card())))
        self.assertEqual(frozen["schema"], "serveros/card-frozen.v1")

    def test_two_identical_cards_an_hour_apart_are_one_version(self):
        """A timestamp cannot be the identity of a payload.

        The broadcast is hourly and a quiet host produces the same card twice.
        A version derived from the clock would call those two sends different
        versions, and the receipt would then be unable to answer the only
        question it is asked.
        """
        first = card_delivery.freeze_card("status", card(), at=AT)
        second = card_delivery.freeze_card(
            "status", card(), at="2026-09-22T15:40:00+08:00")
        self.assertEqual(first["version"], second["version"])
        self.assertNotEqual(first["idempotency_key"], second["idempotency_key"],
                            "the window moved even though the payload did not")

    def test_one_changed_byte_is_a_different_version(self):
        first = card_delivery.freeze_card("status", card(), at=AT)
        changed = card()
        changed["body"]["elements"][0]["content"] = "CPU 3.3%"
        second = card_delivery.freeze_card("status", changed, at=AT)
        self.assertNotEqual(first["version"], second["version"])

    def test_a_kind_no_renderer_produces_is_refused(self):
        with self.assertRaises(card_delivery.CardDeliveryError):
            card_delivery.freeze_card("weekly-report", card(), at=AT)

    def test_an_empty_card_is_refused(self):
        with self.assertRaises(card_delivery.CardDeliveryError):
            card_delivery.freeze_card("status", {}, at=AT)

    def test_the_caller_may_state_the_window_and_the_default_states_the_instant(self):
        explicit = card_delivery.freeze_card("status", card(), at=AT,
                                             idempotency_key="status-2026-09-22T14")
        self.assertEqual(explicit["idempotency_key"], "status-2026-09-22T14")
        default = card_delivery.freeze_card("status", card(), at=AT)
        self.assertEqual(default["idempotency_key"], f"card-status-{AT}")


class TheReceiptMustNameTheCardItWasSentAs(unittest.TestCase):
    def test_a_card_that_does_not_hash_to_the_version_is_refused(self):
        frozen = card_delivery.freeze_card("status", card(), at=AT)
        edited = card()
        edited["header"]["template"] = "red"
        with self.assertRaises(card_delivery.CardDeliveryError) as caught:
            card_delivery.verify(frozen, edited)
        self.assertIn("two different payloads", str(caught.exception))

    def test_the_agreeing_pair_verifies(self):
        payload = card()
        card_delivery.verify(card_delivery.freeze_card("status", payload, at=AT),
                             payload)

    def test_a_json_round_trip_does_not_change_the_version(self):
        """The envelope travels through a file, so it is re-serialised."""
        payload = card()
        frozen = card_delivery.freeze_card("status", payload, at=AT)
        again = json.loads(json.dumps({"card": payload, "frozen": frozen}))
        card_delivery.verify(again["frozen"], again["card"])
        self.assertEqual(card_delivery.freeze_card("status", again["card"], at=AT),
                         {**frozen, "at": AT})


# ---------------------------------------------------------------------------
# the ledger
# ---------------------------------------------------------------------------


class WhatWasSentIsRecorded(Fixture):
    def test_a_delivered_send_records_the_version(self):
        frozen = self.frozen()
        out = self.record()
        self.assertTrue(out["recorded"])
        self.assertEqual(out["entry"]["version"], frozen["version"])
        self.assertTrue(out["entry"]["delivered"])
        self.assertEqual(self.ledger.last("status")["version"], frozen["version"])

    def test_a_failed_send_keeps_the_version_it_attempted(self):
        """`null` would make "the card that failed to arrive" and "a send
        nobody composed a card for" read the same."""
        out = self.record(state="FAILED", reason="timeout")
        self.assertFalse(out["entry"]["delivered"])
        self.assertEqual(out["entry"]["version"], self.frozen()["version"])
        self.assertEqual(out["entry"]["reason"], "timeout")
        self.assertIsNone(self.ledger.last_delivered("status"))

    def test_an_outcome_nobody_can_name_is_refused(self):
        with self.assertRaises(card_delivery.CardDeliveryError):
            self.record(state="PROBABLY")

    def test_the_receipt_keeps_the_message_the_send_produced(self):
        """Without it the identity is dropped at the ledger, and the only way to
        update that card afterwards is a second message — V28's «只 patch 一张»
        is unenforceable from the card alone."""
        out = self.record(remote_id="om_recorded")
        self.assertEqual(out["entry"]["remote_id"], "om_recorded")
        self.assertEqual(self.ledger.last_delivered("status")["remote_id"],
                         "om_recorded")

    def test_a_send_with_no_message_id_keeps_the_absence(self):
        """The transport reports this case rather than refusing it, so the
        ledger has to be able to hold it — and a receipt that filled the gap
        with a guess would point a later patch at a message nobody can name."""
        out = self.record(remote_id=None)
        self.assertIsNone(out["entry"]["remote_id"])
        self.assertIsNone(self.ledger.last_delivered("status")["remote_id"])
        self.assertIn("remote_id", json.loads(self.path.read_text(encoding="utf-8"))
                      ["entries"][-1])

    def test_the_ledger_keeps_the_kinds_apart(self):
        self.record()
        self.record(kind="recovery")
        self.assertEqual(len(self.ledger.entries()), 2)
        self.assertEqual(len(self.ledger.entries("status")), 1)
        self.assertEqual(self.ledger.last("recovery")["kind"], "recovery")

    def test_the_ledger_is_bounded(self):
        for index in range(card_delivery.KEEP + 5):
            self.record(at=f"{AT}#{index}")
        self.assertEqual(len(self.ledger.entries()), card_delivery.KEEP)
        self.assertEqual(self.ledger.last()["at"], f"{AT}#{card_delivery.KEEP + 4}")

    def test_an_unreadable_ledger_is_refused_rather_than_emptied(self):
        self.record()
        self.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(card_delivery.CardDeliveryError) as caught:
            card_delivery.CardLedger(self.path)
        self.assertIn("Refusing to start with an empty ledger", str(caught.exception))

    def test_a_ledger_with_no_entries_list_is_refused(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"schema": "x", "sends": []}),
                             encoding="utf-8")
        with self.assertRaises(card_delivery.CardDeliveryError):
            card_delivery.CardLedger(self.path)

    def test_the_ledger_is_a_sibling_of_the_register_not_a_key_inside_it(self):
        self.assertEqual(card_delivery.ledger_path(STATE),
                         Path("/state/cards/delivery.json"))

    def test_the_two_rhythms_do_not_share_a_ledger(self):
        """§3. One ledger would let the hourly card advance the alert delta
        window and quietly consume the escalations of the same hour."""
        self.assertNotEqual(card_delivery.ledger_path(STATE),
                            alert_digest.cursor_path(STATE))


# ---------------------------------------------------------------------------
# the id the receipt keeps is a message id or it is nothing
# ---------------------------------------------------------------------------


class WhatCountsAsAMessageId(unittest.TestCase):
    """The receipt is the only place the send's message id is kept, so it is
    also the only place that can catch a value which is not one.

    The failure this prevents is quiet: a receipt filed against a conversation,
    or against a hand-typed value with a space in it, reads exactly like a
    receipt filed against a message — and the next update of that card then
    aims at something nobody can name.
    """

    def test_an_absent_id_is_none_and_not_an_empty_string(self):
        """`''` would be a claim about a message; `None` is the absence of one.
        The ledger has to be able to hold the absence, because the transport
        reports "no id in the answer" rather than refusing the send."""
        for absent in (None, "", "   "):
            self.assertIsNone(card_delivery.check_remote_id(absent), repr(absent))

    def test_a_value_that_is_not_text_is_refused_rather_than_stringified(self):
        """`str(b"om_x")` is `"b'om_x'"`: an id-shaped value naming nothing.
        This is how a missing `.decode()` upstream would reach the ledger."""
        for not_text in (b"om_x", 7, ["om_x"]):
            with self.assertRaises(card_delivery.CardDeliveryError) as caught:
                card_delivery.check_remote_id(not_text)
            self.assertIn("arrives as text", str(caught.exception))

    def test_a_message_id_comes_back_as_one_token(self):
        self.assertEqual(card_delivery.check_remote_id("om_abc123"), "om_abc123")
        self.assertEqual(card_delivery.check_remote_id("  om_abc123  "),
                         "om_abc123")

    def test_an_identity_that_is_not_a_message_is_refused_without_echoing_it(self):
        for value in ("oc_a_conversation", "ou_a_person", "on_a_union"):
            with self.assertRaises(card_delivery.CardDeliveryError) as caught:
                card_delivery.check_remote_id(value)
            text = str(caught.exception)
            self.assertNotIn(value.split("_", 1)[1], text,
                             "the refusal copies the identifier into a log")

    def test_a_value_with_whitespace_inside_it_is_refused(self):
        """It cannot address a message, and it is exactly what a hand-run
        command produces when a shell variable is quoted empty."""
        with self.assertRaises(card_delivery.CardDeliveryError) as caught:
            card_delivery.check_remote_id("om_a om_b")
        self.assertIn("one token", str(caught.exception))

    def test_the_record_path_applies_the_rule_and_not_only_the_helper(self):
        """A rule the row writer does not call is a rule the daemon can walk
        around — and the daemon is the only writer over the socket."""
        with tempfile.TemporaryDirectory() as tmp:
            ledger = card_delivery.CardLedger(
                Path(tmp) / "cards" / "delivery.json")
            with self.assertRaises(card_delivery.CardDeliveryError):
                ledger.record("status", at=AT, state="DELIVERED",
                              version="status@abc", remote_id="oc_a_room")
            self.assertEqual(ledger.entries(), [])


# ---------------------------------------------------------------------------
# the hop the id travels: the command that files the receipt
# ---------------------------------------------------------------------------


class TheCommandCarriesTheMessageToTheLedger(unittest.TestCase):
    """`serverosctl card record-delivery` is the ONE surface that writes a
    receipt, and the id reaches the ledger through a daemon request.

    The fake here is a daemon; the parser, the envelope reading and the request
    the command builds are the ones that ship. What it exists to catch is a
    keyword dropped between the flag and the params — which would leave every
    receipt looking complete and every later update unable to find its message.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="card-receipt-cli-"))
        self.addCleanup(__import__("shutil").rmtree, self.dir, ignore_errors=True)
        cli = _load("serveros_runtime_cli_for_receipts",
                    ROOT / "runtime" / "cli.py")
        self.cli = cli
        self.sent = []
        self._real = cli.ask_daemon

        def answer(method, params=None, **kw):
            self.sent.append((method, params))
            return {"recorded": True,
                    "entry": {"kind": params.get("kind"), "state": params.get("state"),
                              "version": params.get("frozen", {}).get("version"),
                              "payload_bytes": 10, "payload_hash": "a" * 64,
                              "remote_id": (params.get("remote_id") or "") or None,
                              "delivered": True}}

        cli.ask_daemon = answer
        self.addCleanup(lambda: setattr(cli, "ask_daemon", self._real))
        self.card = card()
        self.frozen = card_delivery.freeze_card("status", self.card, at=AT)
        self.envelope = self.dir / "envelope.json"
        self.envelope.write_text(
            json.dumps({"card": self.card, "frozen": self.frozen}),
            encoding="utf-8")

    def run_cli(self, *argv):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = self.cli.main(list(argv))
        return code, out.getvalue() + err.getvalue()

    def test_the_message_id_reaches_the_daemon_request(self):
        code, text = self.run_cli(
            "card", "record-delivery", "--kind", "status", "--state", "DELIVERED",
            "--card-file", str(self.envelope), "--message-id", "om_cli_receipt")
        self.assertEqual(code, 0, text)
        method, params = self.sent[-1]
        self.assertEqual(method, "card.record-delivery")
        self.assertEqual(params["remote_id"], "om_cli_receipt")

    def test_without_the_flag_the_request_says_there_is_no_id(self):
        """Not `None` invented at the flag and not a placeholder: the absence is
        passed on as the empty value the daemon turns into «there was none»."""
        code, text = self.run_cli(
            "card", "record-delivery", "--kind", "status", "--state", "DELIVERED",
            "--card-file", str(self.envelope))
        self.assertEqual(code, 0, text)
        _method, params = self.sent[-1]
        self.assertIn("remote_id", params)
        self.assertFalse(params["remote_id"])

    def test_the_command_prints_which_message_it_filed_the_receipt_against(self):
        code, text = self.run_cli(
            "card", "record-delivery", "--kind", "status", "--state", "DELIVERED",
            "--card-file", str(self.envelope), "--message-id", "om_cli_receipt")
        self.assertEqual(code, 0, text)
        self.assertIn("om_cli_receipt", text)

    def test_a_receipt_with_no_message_says_so_rather_than_leaving_a_gap(self):
        """The line has to be readable: a blank column and «this one cannot be
        updated in place» are different facts about the same send."""
        code, text = self.run_cli(
            "card", "record-delivery", "--kind", "status", "--state", "DELIVERED",
            "--card-file", str(self.envelope))
        self.assertEqual(code, 0, text)
        self.assertIn("改不了这一条", text)


# ---------------------------------------------------------------------------
# it records and does not send
# ---------------------------------------------------------------------------


class ThisIsALedgerAndNotATransport(unittest.TestCase):
    def test_it_imports_no_transport_and_no_subprocess(self):
        source = (ROOT / "runtime" / "card_delivery.py").read_text(encoding="utf-8")
        for banned in ("import subprocess", "import socket", "urllib",
                       "requests", "docker", "lark"):
            self.assertNotIn(banned, source,
                             f"a receipt ledger that can reach a transport is "
                             f"one that will eventually send: {banned}")

    def test_the_kinds_are_the_three_the_design_names(self):
        self.assertEqual(card_delivery.KINDS, ("status", "alert", "recovery"))

    def test_the_diagnostic_reads_one_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "alerts" / "state.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            card_delivery.CardLedger(card_delivery.ledger_path(path)).record(
                "status", at=AT, state="DELIVERED", version="status@abc",
                payload_hash="a" * 64, payload_bytes=10)
            import io
            from contextlib import redirect_stdout
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                self.assertEqual(card_delivery.main(["--state", str(path),
                                                     "--kind", "status"]), 0)
            summary = json.loads(buffer.getvalue())
            self.assertEqual(summary["entries"], 1)
            self.assertEqual(summary["kind"], "status")


if __name__ == "__main__":
    unittest.main()
