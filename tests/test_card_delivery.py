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
# The CLI's own module name, so this is the copy `runtime/cli.py` and
# `runtime/card_day.py` read the window from and not a third one.
transport = _load("serveros_runtime_message_transport",
                  ROOT / "runtime" / "message_transport.py")

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
        # Frozen as the kind being recorded, so the version in the row names a
        # card of that kind: an alert receipt carrying a status card's version
        # would be a row nobody could match against anything.
        payload = self.frozen(kind=over.get("kind", "status"))
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
        self.record(kind="recovery", episode="ep_hand_one")
        self.assertEqual(len(self.ledger.entries()), 2)
        self.assertEqual(len(self.ledger.entries("status")), 1)
        self.assertEqual(self.ledger.last("recovery")["kind"], "recovery")

    def test_the_ledger_is_bounded(self):
        for index in range(card_delivery.KEEP + 5):
            self.record(at=f"{AT}#{index}")
        self.assertEqual(len(self.ledger.entries()), card_delivery.KEEP)
        self.assertEqual(self.ledger.last()["at"], f"{AT}#{card_delivery.KEEP + 4}")

    def test_a_chatty_kind_cannot_evict_another_kinds_receipt(self):
        """The day's status receipt is what the next patch aims at.

        `notice` adds thirteen rows a workday. Bounded over the whole document,
        that is thirteen rows of room taken from the receipts that carry
        `remote_id` — and the failure is not a missing receipt but a SECOND
        card: `binding_verdict` finds nothing to patch, and a new message is
        then the only option left. A bound on the shared list is a bound one
        kind can spend on another kind's behalf.
        """
        self.record(remote_id="om_the_days_card")
        for index in range(card_delivery.KEEP + 5):
            self.record(kind="notice", at=f"{AT}#{index}")
        self.assertEqual(len(self.ledger.entries("notice")), card_delivery.KEEP)
        self.assertEqual(self.ledger.last_delivered("status")["remote_id"],
                         "om_the_days_card")

    def test_the_bound_is_per_kind_rather_than_over_the_document(self):
        for index in range(card_delivery.KEEP + 5):
            self.record(at=f"{AT}#{index}")
            self.record(kind="notice", at=f"{AT}#{index}")
        self.assertEqual(len(self.ledger.entries()), 2 * card_delivery.KEEP)

    def test_the_trim_keeps_the_newest_and_leaves_the_order_alone(self):
        # The list's order IS the order — nothing re-sorts by `at` — so a trim
        # that reversed it would make `last` answer with the oldest row. Spelled
        # as an exact sequence rather than as `sorted(...)`: these `at` values
        # are strings, and `"#10" < "#2"` lexically, so a `sorted` here would be
        # comparing a different order than the one the file is in.
        for index in range(card_delivery.KEEP + 5):
            self.record(kind="notice", at=f"{AT}#{index}")
        ats = [row["at"] for row in self.ledger.entries("notice")]
        self.assertEqual(ats, [f"{AT}#{index}" for index in range(5, card_delivery.KEEP + 5)])
        self.assertEqual(self.ledger.last("notice")["at"],
                         f"{AT}#{card_delivery.KEEP + 4}")

    def test_a_notice_receipt_must_not_name_an_episode(self):
        """A notice is about the CARD, and the version field already says which.

        An episode here would make `carries` answer «the incident's card» with a
        notice's receipt, and the next incident patch would aim at the notice.
        """
        with self.assertRaises(card_delivery.CardDeliveryError):
            self.record(kind="notice", episode="ep_hand_one")

    def test_a_notice_records_like_any_other_kind(self):
        out = self.record(kind="notice", remote_id="om_the_notice")
        self.assertTrue(out["recorded"])
        self.assertEqual(out["entry"]["kind"], "notice")
        self.assertEqual(self.ledger.last_delivered("notice")["remote_id"],
                         "om_the_notice")

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
# §7① — was the reader told, on the most recent attempt
# ---------------------------------------------------------------------------


class TheDeliveryFinding(Fixture):
    """The question `last_delivered` cannot answer.

    Every test here is written against a ledger that really has the two rows in
    it rather than against a stubbed answer, because the defect this exists to
    prevent is a reader that finds the WRONG ROW and is answered correctly about
    it.
    """

    def test_a_kind_nobody_has_attempted_is_not_a_failure(self):
        """«Nobody asked» and «the transport did not do it» are different.

        The finding names the first one too, because a reader that had only
        `None` for it could not tell an hour before its first send from an hour
        whose send arrived.
        """
        found = self.ledger.delivery_finding("status")
        self.assertEqual(found["finding"], card_delivery.DELIVERY_NOT_ATTEMPTED)
        self.assertFalse(found["fault"])
        self.assertIsNone(found["state"])
        self.assertIn(found["finding"], card_delivery.SAYS)

    def test_a_delivered_card_produces_no_finding_at_all(self):
        self.record()
        found = self.ledger.delivery_finding("status")
        self.assertIsNone(found["finding"])
        self.assertFalse(found["fault"])
        self.assertIsNone(found["says"])
        self.assertEqual(found["state"], card_delivery.DELIVERED)

    def test_each_non_arrival_has_its_own_word_and_its_own_sentence(self):
        """One per outcome, and the set is derived from `OUTCOMES`.

        This is the test that fails when somebody adds a fifth outcome to that
        tuple: the finding word is derived and so appears by itself, and the
        SENTENCE does not — which is the case where the vocabulary can describe
        a state and nothing can tell a person about it.
        """
        self.assertEqual(set(card_delivery.FAULTS),
                         {f"DELIVERY_{state}" for state in card_delivery.OUTCOMES
                          if state != card_delivery.DELIVERED})
        self.assertEqual(set(card_delivery.FINDINGS),
                         set(card_delivery.FAULTS)
                         | {card_delivery.DELIVERY_NOT_ATTEMPTED})
        self.assertEqual(set(card_delivery.SAYS), set(card_delivery.FINDINGS))
        for state in card_delivery.OUTCOMES:
            if state == card_delivery.DELIVERED:
                continue
            with self.subTest(state=state):
                self.record(state=state)
                found = self.ledger.delivery_finding("status")
                self.assertEqual(found["finding"], f"DELIVERY_{state}")
                self.assertTrue(found["fault"])
                self.assertTrue(found["says"])
                self.ledger = card_delivery.CardLedger(self.path)

    def test_a_failed_patch_is_not_read_as_having_arrived(self):
        """The kill shot, and the shape of the real failure.

        A card that did not change is re-patched with the SAME version, so an
        older DELIVERED row and the newer FAILED row carry the same version —
        `last_delivered` finds the older one and reports that something arrived.
        The finding has to read the NEWEST row, and the only way to tell the two
        answers apart is a row whose `at` is the failed one's.
        """
        self.record(at="2026-09-22T14:40:00+08:00")
        self.ledger = card_delivery.CardLedger(self.path)
        self.record(state="FAILED", at="2026-09-22T15:40:00+08:00",
                    reason="transport answered 500")
        older, newest = self.ledger.entries("status")
        # The two rows really do name the same version: without this the test
        # would pass for the wrong reason, on a ledger where the versions differ
        # and any reader could tell the rows apart.
        self.assertEqual(older["version"], newest["version"])
        self.assertEqual(self.ledger.last_delivered("status"), older)
        found = self.ledger.delivery_finding("status")
        self.assertEqual(found["finding"], "DELIVERY_FAILED")
        self.assertEqual(found["at"], newest["at"])
        self.assertEqual(found["version"], newest["version"])

    def test_the_finding_is_about_one_kind(self):
        """A day's card arriving says nothing about this hour's notice.

        Kinds are separate on purpose — `KEEP` is per kind for the same reason —
        and a finding that read the newest row of whatever kind happened to be
        last would let a delivered notice vouch for a status card that failed.
        """
        self.record(kind="notice")
        self.record(kind="status", state="FAILED")
        self.assertIsNone(self.ledger.delivery_finding("notice")["finding"])
        self.assertEqual(self.ledger.delivery_finding("status")["finding"],
                         "DELIVERY_FAILED")

    def test_a_state_this_module_cannot_read_is_refused(self):
        """A hand-edited row is a document problem and not a transport one.

        Reported as «not DELIVERED» it would file an unreadable ledger as a
        failed send, and the repair for those two is not the same repair.
        """
        self.record()
        data = json.loads(self.path.read_text(encoding="utf-8"))
        data["entries"][-1]["state"] = "PARTLY"
        self.path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(card_delivery.CardDeliveryError) as caught:
            card_delivery.CardLedger(self.path).delivery_finding("status")
        self.assertIn("PARTLY", str(caught.exception))

    def test_a_kind_that_is_not_a_kind_is_refused(self):
        with self.assertRaises(card_delivery.CardDeliveryError):
            self.ledger.delivery_finding("daily-card")


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
                              "episode": (params.get("episode") or "") or None,
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

    def alert_envelope(self) -> Path:
        frozen = card_delivery.freeze_card("alert", self.card, at=AT)
        path = self.dir / "alert-envelope.json"
        path.write_text(json.dumps({"card": self.card, "frozen": frozen}),
                        encoding="utf-8")
        return path

    def test_the_episode_reaches_the_daemon_request(self):
        """The word a later hour looks this receipt up by. Dropped between the
        flag and the params, it would leave a receipt that looks complete and
        that no incident lookup can ever find."""
        code, text = self.run_cli(
            "card", "record-delivery", "--kind", "alert", "--state", "DELIVERED",
            "--card-file", str(self.alert_envelope()), "--message-id", "om_a",
            "--episode", "abc123def456")
        self.assertEqual(code, 0, text)
        _method, params = self.sent[-1]
        self.assertEqual(params["episode"], "abc123def456")

    def test_without_the_flag_the_request_says_there_is_no_episode(self):
        """Not invented at the flag: the absence is passed on, and the receipt
        writer — which is where «this kind must name one» lives — is what
        refuses it."""
        code, text = self.run_cli(
            "card", "record-delivery", "--kind", "alert", "--state", "DELIVERED",
            "--card-file", str(self.alert_envelope()))
        self.assertEqual(code, 0, text)
        _method, params = self.sent[-1]
        self.assertIn("episode", params)
        self.assertFalse(params["episode"])

    def test_the_line_names_the_episode_the_receipt_was_filed_under(self):
        code, text = self.run_cli(
            "card", "record-delivery", "--kind", "alert", "--state", "DELIVERED",
            "--card-file", str(self.alert_envelope()),
            "--episode", "abc123def456")
        self.assertEqual(code, 0, text)
        self.assertIn("abc123def456", text)

    def test_a_workdays_receipt_says_nothing_at_all_about_an_episode(self):
        """Not «episode: None»: a day has no episode, and a line saying there is
        none would read as a receipt that lost one."""
        code, text = self.run_cli(
            "card", "record-delivery", "--kind", "status", "--state", "DELIVERED",
            "--card-file", str(self.envelope))
        self.assertEqual(code, 0, text)
        self.assertNotIn("episode", text)


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

    def test_the_kinds_are_the_ones_the_design_names(self):
        # Five, and `fault` is the one that is not a card: it is the message a
        # FAILED patch gets INSTEAD of a notice, which §7① requires and which
        # has no card version to be filed under — the version not arriving is
        # what it is about. Its own kind rather than riding on `notice`, because
        # `KEEP` is per kind and a receipt filed under a name that does not
        # describe it is one the next reader finds while looking for something
        # else.
        self.assertEqual(card_delivery.KINDS,
                         ("status", "alert", "recovery", "notice", "fault"))

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



# ---------------------------------------------------------------------------
# an episode is one token, and only some receipts carry one
# ---------------------------------------------------------------------------

#: Two instants that are the same verdict far apart: six hours after the receipt
#: is well inside the service's fourteen-day window, a month later is outside it.
LATER_SAME_DAY = "2026-09-22T20:40:00+08:00"
LATER_MONTH = "2026-10-20T14:40:00+08:00"


class WhatCountsAsAnEpisode(unittest.TestCase):
    """An episode is the only word a later hour — or a later day — can look the
    same incident up by, so a value that is not one has to be refused at the
    door rather than stored.

    The failure this prevents is quiet and expensive: an episode that cannot be
    compared is an episode no lookup finds, and a lookup that finds nothing
    answers «nothing was ever sent for this incident» — which is a second card
    for one incident, the thing V29 forbids by name.
    """

    def test_an_absent_episode_is_none_and_not_an_empty_string(self):
        for absent in (None, "", "   "):
            self.assertIsNone(card_delivery.check_episode(absent), repr(absent))

    def test_a_value_that_is_not_text_is_refused_rather_than_stringified(self):
        """`str([...])` is a value shaped like an episode and naming nothing.
        This is how a missing unwrap upstream would reach the ledger."""
        for not_text in (b"ep_x", 7, ["ep_x"], {"episode": "ep_x"}):
            with self.assertRaises(card_delivery.CardDeliveryError) as caught:
                card_delivery.check_episode(not_text)
            self.assertIn("arrives as text", str(caught.exception))

    def test_an_episode_comes_back_as_one_token(self):
        self.assertEqual(card_delivery.check_episode("  abc123def456  "),
                         "abc123def456")

    def test_a_value_with_whitespace_inside_it_is_refused(self):
        with self.assertRaises(card_delivery.CardDeliveryError) as caught:
            card_delivery.check_episode("ep_a ep_b")
        self.assertIn("one token", str(caught.exception))


class TheKindDecidesWhetherThereIsAnEpisode(Fixture):
    """Symmetric, and both halves are load-bearing.

    An alert or a recovery receipt without an episode makes «same episode, one
    card» unanswerable — which is the difference between patching the message
    already in the chat and sending a second card for one incident. A status
    receipt WITH one is the other error: it would make the same word name a
    workday's card and an incident at once, and the incident lookup would then
    find a day.
    """

    def test_a_receipt_for_an_incident_must_name_its_episode(self):
        for kind in card_delivery.PER_EPISODE_KINDS:
            with self.assertRaises(card_delivery.CardDeliveryError) as caught:
                self.record(kind=kind)
            self.assertIn("ONE episode", str(caught.exception))
        self.assertEqual(self.ledger.entries(), [])

    def test_a_receipt_for_a_workday_must_not_name_one(self):
        with self.assertRaises(card_delivery.CardDeliveryError) as caught:
            self.record(episode="abc123def456")
        self.assertIn("workday", str(caught.exception))
        self.assertEqual(self.ledger.entries(), [])

    def test_the_row_keeps_the_episode_it_was_filed_under(self):
        out = self.record(kind="alert", episode="abc123def456")
        self.assertEqual(out["entry"]["episode"], "abc123def456")
        self.assertEqual(self.ledger.last("alert")["episode"], "abc123def456")
        # …and a workday's receipt says «no episode» by carrying nothing, which
        # is not the same as carrying a word that means «no episode».
        self.assertIsNone(self.record()["entry"]["episode"])

    def test_a_row_whose_episode_cannot_be_compared_is_refused_on_read(self):
        """The row is what a later lookup reads, so an unreadable one has to stop
        the ledger being served at all: comparing it to a real episode answers
        «different», and «different» means a second card."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"schema": card_delivery.SCHEMA,
             "entries": [{"kind": "alert", "episode": ["abc123def456"]}]}),
            encoding="utf-8")
        with self.assertRaises(card_delivery.CardDeliveryError) as caught:
            card_delivery.CardLedger(self.path)
        self.assertIn("entry 0", str(caught.exception))

    def test_a_row_written_before_episodes_existed_is_still_readable(self):
        """The absence is left alone rather than filled in: what is refused is a
        MALFORMED episode, and inventing one for an older row would put a
        fabricated incident into the ledger."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(
            {"schema": card_delivery.SCHEMA,
             "entries": [{"kind": "alert", "at": AT, "state": "DELIVERED",
                          "version": "alert@abc"}]}), encoding="utf-8")
        self.assertEqual(len(card_delivery.CardLedger(self.path).entries()), 1)


class TheMessageThatCarriesAnEpisode(Fixture):
    """«Which message holds this incident» is a question about what was SENT and
    not about what is true now, so it is answered out of the receipt list — and
    the two ways of reading that list apart are the whole of this class.
    """

    def test_an_episode_nothing_was_sent_for_has_no_message(self):
        self.assertIsNone(self.ledger.carries("alert", "abc123def456"))

    def test_the_answer_is_the_message_the_receipt_names(self):
        self.record(kind="alert", episode="abc123def456",
                    remote_id="om_alert_one")
        row = self.ledger.carries("alert", "abc123def456")
        self.assertEqual(row["remote_id"], "om_alert_one")
        self.assertEqual(row["version"],
                         self.frozen(kind="alert")["version"])

    def test_a_refused_attempt_does_not_hide_the_message_that_went_out(self):
        """The last row for an episode is not necessarily the row that says
        which message carries it: a patch the service declined is a statement
        about an attempt. Reading it as the binding would answer «a card was
        sent and the receipt lost its id» — a repair aimed at an intact receipt
        — and the reader would be one step from a second card."""
        self.record(kind="alert", episode="abc123def456",
                    remote_id="om_alert_one")
        self.record(kind="alert", episode="abc123def456", remote_id=None,
                    state="FAILED", reason="通道拒绝了这次 patch")
        self.assertEqual(self.ledger.carries("alert", "abc123def456")["remote_id"],
                         "om_alert_one")

    def test_a_receipt_that_recorded_no_message_still_answers(self):
        """«Recorded, and names no message» has to stay distinct from «nothing
        recorded»: the first is a broken receipt, the second is a card that was
        never sent, and they have different repairs."""
        self.record(kind="alert", episode="abc123def456", remote_id=None)
        row = self.ledger.carries("alert", "abc123def456")
        self.assertIsNotNone(row)
        self.assertIsNone(row["remote_id"])

    def test_the_lookup_is_about_one_episode_and_not_about_one_kind(self):
        self.record(kind="alert", episode="aaa111", remote_id="om_one")
        self.record(kind="alert", episode="bbb222", remote_id="om_two")
        self.assertEqual(self.ledger.carries("alert", "aaa111")["remote_id"],
                         "om_one")
        self.assertEqual(self.ledger.carries("alert", "bbb222")["remote_id"],
                         "om_two")

    def test_a_lookup_that_names_no_episode_is_refused(self):
        for absent in ("", "  ", None):
            with self.assertRaises(card_delivery.CardDeliveryError):
                self.ledger.carries("alert", absent)


class WhatTheNextSendAboutAnEpisodeDoes(Fixture):
    """V29's answer: «同 episode 跨小时、跨天只 patch 一张，不重复 NEW».

    It is a verdict about the MESSAGE and not about whether the finding is worth
    saying — «this hour has nothing to report» is `alert_digest.decision`'s
    judgement and V35's suppression lives there. What this class pins is that
    each of the five answers is reachable and that none is reached by guessing.
    """

    def verdict(self, episode="abc123def456", *, at=AT, kind="alert"):
        return card_delivery.incident_verdict(self.ledger, kind=kind,
                                              episode=episode, at=at)

    def test_an_episode_with_no_receipt_is_a_first_send(self):
        verdict = self.verdict()
        self.assertEqual(verdict["action"], card_delivery.NEW_MESSAGE)
        self.assertEqual(verdict["reason"], card_delivery.NEVER_SENT)
        self.assertIsNone(verdict["remote_id"])
        self.assertIsNone(verdict["version"])

    def test_an_episode_whose_message_is_in_hand_is_patched(self):
        self.record(kind="alert", episode="abc123def456",
                    remote_id="om_alert_one")
        verdict = self.verdict(at=LATER_SAME_DAY)
        self.assertEqual(verdict["action"], card_delivery.PATCH_MESSAGE)
        self.assertEqual(verdict["reason"], card_delivery.IN_HAND)
        self.assertEqual(verdict["remote_id"], "om_alert_one")
        self.assertEqual(verdict["version"], self.frozen(kind="alert")["version"])
        self.assertGreater(verdict["age_days"], 0)

    def test_a_message_that_aged_out_is_a_successor_and_not_a_second_card(self):
        """The one case where a new card is allowed — and it is not permitted to
        be an unrelated one: the successor has to be marked as the same episode.
        """
        self.record(kind="alert", episode="abc123def456",
                    remote_id="om_alert_one")
        verdict = self.verdict(at=LATER_MONTH)
        self.assertEqual(verdict["action"], card_delivery.SUCCESSOR_MESSAGE)
        self.assertEqual(verdict["reason"], card_delivery.IDENTITY_EXPIRED)
        self.assertEqual(verdict["remote_id"], "om_alert_one")
        self.assertIn("continuity", verdict["says"])

    def test_a_receipt_that_lost_the_id_is_not_a_first_send(self):
        self.record(kind="alert", episode="abc123def456", remote_id=None)
        verdict = self.verdict()
        self.assertEqual(verdict["action"], card_delivery.NEW_MESSAGE)
        self.assertEqual(verdict["reason"], card_delivery.IDENTITY_MISSING)
        self.assertIn("--message-id", verdict["says"])

    def test_an_instant_that_cannot_be_placed_refuses_to_claim_a_patch(self):
        self.record(kind="alert", episode="abc123def456",
                    remote_id="om_alert_one", at="上周二")
        verdict = self.verdict()
        self.assertEqual(verdict["action"], card_delivery.NEW_MESSAGE)
        self.assertEqual(verdict["reason"], card_delivery.IDENTITY_UNDATED)

    def test_a_card_family_that_is_about_a_workday_is_refused(self):
        with self.assertRaises(card_delivery.CardDeliveryError) as caught:
            self.verdict(kind="status")
        self.assertIn("workday", str(caught.exception))

    def test_a_verdict_about_no_episode_is_refused(self):
        """Refused HERE rather than one layer down.

        `carries` refuses an empty episode too, so a verdict that dropped its
        own guard would still fail — and every assertion about the exception
        TYPE would stay green while the caller was handed a sentence about a
        lookup this call never got to make. Which of the two speaks is the
        difference between «an incident verdict is about one episode» (the call
        was wrong) and «which message carries an episode…» (the ledger was
        asked a question it could not answer), and only the first sends the
        reader to the place the mistake is.
        """
        for absent in ("", "  ", None):
            with self.assertRaises(card_delivery.CardDeliveryError) as caught:
                self.verdict(episode=absent)
            self.assertIn("verdict", str(caught.exception), repr(absent))

    def test_the_window_it_measures_against_is_the_transports_own_number(self):
        """A literal 14 inside `incident_verdict` would keep every other test in
        this class green while the service moved its window — and the day would
        go on promising a patch the transport refuses."""
        self.record(kind="alert", episode="abc123def456",
                    remote_id="om_alert_one")
        self.addCleanup(setattr, transport, "CARD_UPDATE_WINDOW_DAYS",
                        transport.CARD_UPDATE_WINDOW_DAYS)
        transport.CARD_UPDATE_WINDOW_DAYS = 3
        narrow = self.verdict(at="2026-09-27T14:40:00+08:00")
        transport.CARD_UPDATE_WINDOW_DAYS = 40
        wide = self.verdict(at="2026-09-27T14:40:00+08:00")
        self.assertEqual(narrow["window_days"], 3)
        self.assertEqual(wide["window_days"], 40)
        self.assertEqual(narrow["action"], card_delivery.SUCCESSOR_MESSAGE)
        self.assertEqual(wide["action"], card_delivery.PATCH_MESSAGE)

    def test_no_answer_is_a_day_cards_answer(self):
        """The judgment is shared with `card_day`; the sentences are not. An
        incident verdict that told the reader about 当天卡 would be describing
        the wrong thing at the moment somebody acts on it."""
        self.record(kind="alert", episode="abc123def456", remote_id=None,
                    at="上周二")
        answers = [self.verdict(episode="nothing_sent"),
                   self.verdict(episode="abc123def456"),
                   self.verdict(episode="expired_one", at=LATER_MONTH)]
        for verdict in answers:
            self.assertNotIn("当天卡", verdict["says"])
            self.assertIn("episode", verdict["says"])


class TheSameJudgmentServesBothCardFamilies(unittest.TestCase):
    """`binding_verdict` is the ONE place «may this recorded message still be
    changed» is answered, because a day's card and an incident ask it about
    different messages under the same rule.

    Two answers would be two chances to read the fourteen-day window wrongly,
    and the wrong one would be the answer that promises a patch the transport
    refuses. What differs between the two callers is the sentence, which is why
    this returns a verdict rather than a line of prose.
    """

    def test_the_module_it_reads_is_the_same_copy_the_cli_loads(self):
        self.assertIs(card_delivery._transport(), transport)

    def test_the_window_is_the_transports_own_number_and_not_a_copy(self):
        self.assertEqual(card_delivery.update_window_days(),
                         transport.CARD_UPDATE_WINDOW_DAYS)

    def test_the_four_answers_are_the_four_the_writer_can_produce(self):
        in_hand = card_delivery.binding_verdict(
            {"remote_id": "om_one", "at": AT}, at=LATER_SAME_DAY, window_days=14)
        self.assertEqual((in_hand["action"], in_hand["reason"]),
                         (card_delivery.PATCH_MESSAGE, card_delivery.IN_HAND))
        self.assertEqual(in_hand["remote_id"], "om_one")
        expired = card_delivery.binding_verdict(
            {"remote_id": "om_one", "at": AT}, at=LATER_MONTH, window_days=14)
        self.assertEqual(expired["reason"], card_delivery.IDENTITY_EXPIRED)
        undated = card_delivery.binding_verdict(
            {"remote_id": "om_one", "at": "上周二"}, at=AT, window_days=14)
        self.assertEqual(undated["reason"], card_delivery.IDENTITY_UNDATED)
        missing = card_delivery.binding_verdict(
            {"at": AT}, at=AT, window_days=14)
        self.assertEqual(missing["reason"], card_delivery.IDENTITY_MISSING)
        self.assertIsNone(missing["remote_id"])
        # Every one of them new-sends except the one that patches, so a caller
        # cannot read «NEW_MESSAGE» as «nothing is wrong».
        for verdict in (expired, undated, missing):
            self.assertEqual(verdict["action"], card_delivery.NEW_MESSAGE)

    def test_an_absent_binding_is_not_a_binding(self):
        for absent in (None, {}, {"remote_id": ""}, {"remote_id": "   "}):
            verdict = card_delivery.binding_verdict(absent, at=AT, window_days=14)
            self.assertEqual(verdict["reason"], card_delivery.IDENTITY_MISSING,
                             repr(absent))

    def test_a_naive_instant_is_refused_rather_than_read_as_utc(self):
        """The window is fourteen days wide, so assuming a zone for a value that
        carries none can move the answer by nearly a day in either direction —
        which is the difference between patching a message and refusing to."""
        verdict = card_delivery.binding_verdict(
            {"remote_id": "om_one", "at": "2026-09-22T14:40:00"},
            at=LATER_SAME_DAY, window_days=14)
        self.assertEqual(verdict["reason"], card_delivery.IDENTITY_UNDATED)


class TheIncidentCommandAsksTheLedger(unittest.TestCase):
    """`serverosctl card incident` answers «what does the next send about this
    episode do to the message carrying it».

    Driven through the command's own parser, because what this catches is an
    argument the parser never declares or a keyword the caller forgets — both
    invisible until somebody runs it on the node. The fake here is the daemon;
    the parser, the flags and the request the command builds are the ones that
    ship.
    """

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="card-incident-cli-"))
        self.addCleanup(__import__("shutil").rmtree, self.dir, ignore_errors=True)
        cli = _load("serveros_runtime_cli_for_receipts",
                    ROOT / "runtime" / "cli.py")
        self.cli = cli
        self.sent = []
        self._real = cli.ask_daemon
        self.verdict = {"kind": "alert", "episode": "abc123def456",
                        "at": AT, "action": card_delivery.PATCH_MESSAGE,
                        "reason": card_delivery.IN_HAND, "remote_id": "om_x",
                        "version": "alert@abc", "window_days": 14,
                        "age_days": 0.4, "says": "承载这个 episode 的消息在手"}

        def answer(method, params=None, **kw):
            self.sent.append((method, params))
            return dict(self.verdict)

        cli.ask_daemon = answer
        self.addCleanup(lambda: setattr(cli, "ask_daemon", self._real))

    def run_cli(self, *argv):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            try:
                code = self.cli.main(list(argv))
            except SystemExit as exit_:   # argparse refuses, and says why
                code = int(exit_.code or 0)
        return code, out.getvalue() + err.getvalue()

    def test_the_kind_the_episode_and_the_instant_reach_the_request(self):
        code, text = self.run_cli("card", "incident", "--kind", "alert",
                                  "--episode", "abc123def456", "--at", AT)
        self.assertEqual(code, 0, text)
        method, params = self.sent[-1]
        self.assertEqual(method, "card.incident")
        self.assertEqual(params["kind"], "alert")
        self.assertEqual(params["episode"], "abc123def456")
        self.assertEqual(params["at"], AT)

    def test_the_answer_and_its_sentence_are_what_the_reader_gets(self):
        code, text = self.run_cli("card", "incident", "--kind", "alert",
                                  "--episode", "abc123def456", "--at", AT)
        self.assertEqual(code, 0, text)
        self.assertIn("PATCH_MESSAGE/IN_HAND", text)
        self.assertIn("om_x", text)
        self.assertIn("承载这个 episode 的消息在手", text)
        self.assertIn("不发送", text)

    def test_an_episode_is_required_rather_than_defaulted(self):
        """There is no «the episode» to fall back on: this command is about ONE
        incident, and a default would answer about whichever one somebody meant.
        """
        code, text = self.run_cli("card", "incident", "--kind", "alert")
        self.assertEqual(code, 2, text)
        self.assertEqual(self.sent, [])

    def test_a_workday_card_family_is_refused_by_the_parser(self):
        """The per-episode kinds are read from the module rather than listed in
        the parser, so this refuses the same set `record` refuses."""
        code, text = self.run_cli("card", "incident", "--kind", "status",
                                  "--episode", "abc123def456")
        self.assertEqual(code, 2, text)
        self.assertEqual(self.sent, [])

if __name__ == "__main__":
    unittest.main()
