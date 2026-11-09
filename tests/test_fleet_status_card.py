"""The 资源播报 card: each device renders its own, the hub is the one sender.

A device writes the card its serverosd renders (`fleet_mcp/node/status_card.py`);
the node connector serves it on `GET /v1/core/card`, signed over the caller's
nonce; the hub's monitor sends one card per device per day and patches it.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "tests", ROOT / "integrations" / "fleet-hub-watch"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_mcp import node_protocol  # noqa: E402
from fleet_mcp.inbox import EventInbox  # noqa: E402
from fleet_mcp.node import status_card  # noqa: E402
from fleet_hub_watch.notify import Notifier, rich_card  # noqa: E402
import test_fleet_device_versions as V  # noqa: E402

CARD = {"schema": "2.0", "config": {"width_mode": "default"},
        "header": {"template": "yellow", "title": {"tag": "plain_text", "content": "⚠️ Dell · 资源播报"}},
        "body": {"elements": [{"tag": "markdown", "content": "建议查看 与 12:00 相同"}]}}
NOW = datetime(2026, 11, 9, 2, 0, tzinfo=timezone.utc)          # Monday 10:00 Beijing


def doc(card=CARD, *, made=NOW, state="OK"):
    return {"schema": status_card.SCHEMA, "device_ref": V.NODE, "state": state,
            "made_at": made.strftime("%Y-%m-%dT%H:%M:%SZ"), "card": card}


class TheDeviceWritesItsOwnCard(unittest.TestCase):
    def test_serverosctl_output_becomes_an_owner_only_document(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            return SimpleNamespace(returncode=0, stdout=json.dumps(CARD))
        built = status_card.build("/x/serverosctl", "device:dell", run=run, now=0, options=[])
        self.assertEqual(calls[0][1:], ["card", "status", "--json", "--device-ref", "device:dell"])
        self.assertEqual((built["state"], built["card"]), ("OK", CARD))
        with tempfile.TemporaryDirectory() as raw:
            out = Path(raw) / "s" / "status-card.json"
            status_card.write(built, out)
            self.assertEqual(oct(out.stat().st_mode & 0o777), "0o600")
            self.assertEqual(json.loads(out.read_text())["state"], "OK")

    def test_the_machines_own_inputs_are_passed_through(self):
        with tempfile.TemporaryDirectory() as raw:
            opts = Path(raw) / "status-card.yaml"
            opts.write_text("gateway_binding: /etc/gw.yaml\nunknown: x\n")
            self.assertEqual(status_card._options(opts), ["--gateway-binding", "/etc/gw.yaml"])
            self.assertEqual(status_card._options(Path(raw) / "absent.yaml"), [])

    def test_a_failure_is_written_as_a_failure_not_an_old_card(self):
        run = lambda argv, **kw: SimpleNamespace(returncode=2, stdout="")
        self.assertEqual(status_card.build("x", "d", run=run)["state"], "ERROR")
        run = lambda argv, **kw: SimpleNamespace(returncode=0, stdout=json.dumps({"schema": "1"}))
        self.assertEqual(status_card.build("x", "d", run=run)["state"], "ERROR")
        big = {**CARD, "body": {"elements": [{"tag": "markdown", "content": "x" * 40000}]}}
        run = lambda argv, **kw: SimpleNamespace(returncode=0, stdout=json.dumps(big))
        self.assertIn("larger", status_card.build("x", "d", run=run)["reason"])


class TheRouteIsSignedAndNamed(unittest.TestCase):
    def test_declared(self):
        self.assertIn("GET /v1/core/card", node_protocol.ROUTES)

    def test_a_signed_round_trip_reaches_the_hub(self):
        with tempfile.TemporaryDirectory() as raw:
            tmp = Path(raw)
            node = V.T.RealConnector(tmp)
            try:
                node.config.status_card_path = V.write_record(tmp / "card.json", doc())
                cards = V.service_for(tmp, node.endpoint).status_cards(V.principal())
            finally:
                node.stop()
        self.assertEqual(cards[V.NODE]["card"], CARD)

    def test_an_unsigned_answer_is_not_the_devices_card(self):
        fake = V.T.FakeServer(body=doc())
        try:
            with tempfile.TemporaryDirectory() as raw:
                cards = V.service_for(Path(raw), fake.endpoint).status_cards(V.principal())
        finally:
            fake.stop()
        self.assertNotIn(V.NODE, cards)


class TheHubIsTheOneSender(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        tmp = Path(self._td.name)
        self.sent, self.patched = [], []

        class Recorder:
            def send(inner, card, *, idempotency_key):
                self.sent.append(card)
                return f"m{len(self.sent)}"

            def patch(inner, message_id, card, *, sent_at):
                self.patched.append((message_id, card))
                return True
        self.notifier = Notifier(EventInbox(tmp / "inbox.sqlite"), tmp / "n.sqlite", Recorder(),
                                 sleep=lambda _s: None)

    def tearDown(self):
        self._td.cleanup()

    def test_one_card_per_device_per_day_patched_in_place(self):
        view = {"device_ref": "device:win", "name": "Windows", "level": "OK", "readings": [],
                "observed_at": None}
        out = self.notifier.status([view], NOW, {V.NODE: doc()})
        self.assertEqual(out, "SENT,SENT", "Dell's own card, and the plain one for Windows only")
        rich = self.sent[0]
        self.assertTrue(rich["config"]["update_multi"])
        self.assertEqual(rich["header"]["title"]["content"], "⚠️ Dell · 资源播报")
        changed = json.loads(json.dumps(CARD))
        changed["body"]["elements"][0]["content"] = "建议查看 swap 上升"
        later = NOW + timedelta(minutes=31)
        self.notifier.status([view], later, {V.NODE: doc(changed, made=later)})
        self.assertEqual(len(self.sent), 2, "no second message for the same day")
        self.assertEqual(self.patched[-1][0], "m1")

    def test_a_stale_or_failed_card_falls_back_to_the_plain_readings(self):
        self.assertIsNone(rich_card(doc(made=NOW - timedelta(hours=1)), NOW))
        self.assertIsNone(rich_card(doc(state="ERROR"), NOW))
        view = {"device_ref": V.NODE, "name": "Dell", "level": "OK", "readings": [],
                "observed_at": None}
        self.notifier.status([view], NOW, {V.NODE: doc(state="ERROR")})
        self.assertEqual(len(self.sent), 1)
        self.assertNotEqual(self.sent[0]["header"]["title"]["content"], "⚠️ Dell · 资源播报")


if __name__ == "__main__":
    unittest.main()
