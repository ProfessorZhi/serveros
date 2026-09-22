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
