#!/usr/bin/env python3
"""Coverage, the device×recipient audience gate, the trend archive, and the
mock delivery that must never be a real one.

WHAT THIS FILE KEEPS FALSE
--------------------------
    "the machine that is down was not on the card"     — coverage is declared,
                                                         not discovered
    "it is hidden so it is safe"                       — the row is ABSENT for an
                                                         audience that may not
                                                         see it, and the SEND is
                                                         refused before the wire
    "we sent it and it failed" == "we refused to send" — only the first has a
                                                         receipt
    "the trend is smooth"                              — a sample with no real
                                                         timestamp is refused
    "a company channel may see a private laptop"       — no grant, no row
    "I said ok, so it is ok"                           — see THE GATE section:
                                                         a boolean a caller
                                                         writes authorizes nothing
    "the log is fine, it is only an id"                — a refusal never echoes
                                                         the recipient it refused

THE GATE SECTION IS THE POINT OF THIS REVISION. The first version let a caller
satisfy the send-time gate with `{"ok": True}` and answered `True` for every
corporate device no matter who asked. Both are checked here from the outside:
the bypass is attempted and must fail, and the default answer to an unlisted
recipient must be no.

The modules are loaded by path under the names they resolve each other by, the
shape `tests/test_fleet_monitor_card.py` uses for the same triple.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / "runtime"

NOW = "2026-10-13T06:00:00Z"
LATER = "2026-10-13T06:10:00Z"
TOKYO = "device:tokyo-shared-dev-01"
A6000 = "device:llm-lab-dell"
PERSONAL = "device:personal-dell"
FJPCOS = "device:work-fj"

CORP_CHAT = "chat:oc_corporate000000000000000000"
OTHER_CHAT = "chat:oc_unapproved0000000000000000"
OWNER = "user:ou_owner0000000000000000000000"
STRANGER = "user:ou_stranger000000000000000000"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


monitor = _load("serveros_fleet_monitor_card", RUNTIME / "fleet_monitor_card.py")
source = _load("serveros_fleet_monitor_source", RUNTIME / "fleet_monitor_source.py")
plan = _load("serveros_fleet_monitor_plan", RUNTIME / "fleet_monitor_plan.py")
audience = _load("serveros_fleet_audience",
                  RUNTIME / "fleet_hub" / "fleet_audience.py")


def md_all(node, out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        if node.get("tag") == "markdown" and isinstance(node.get("content"), str):
            out.append(node["content"])
        for v in node.values():
            md_all(v, out)
    elif isinstance(node, list):
        for v in node:
            md_all(v, out)
    return out


def snapshot(label="CPU", value=30, at=NOW):
    return {"at": at, "node_label": "东京共享开发机",
            "host": {"state": "OK",
                     "metrics": [{"label": label, "value": value, "note": ""}],
                     "lines": [], "why": "", "detail": {}},
            "gaps": []}


def corp_policy(**over):
    """A policy that APPROVES the one company chat — the approval is explicit."""
    kwargs = {"approved_corporate": [CORP_CHAT]}
    kwargs.update(over)
    return source.default_policy(**kwargs)


def owner_policy(**over):
    """The owner's FULL card: approved for the company AND granted the laptop."""
    kwargs = {"approved_corporate": [CORP_CHAT, OWNER]}
    kwargs.update(over)
    rule = source.default_policy(**kwargs)
    rule.devices[PERSONAL]["grants"] = [OWNER]
    return rule


def owner_only_policy(**over):
    """The owner WITHOUT a corporate approval: their private card, and no more.

    This is the audience separation in one value: being the owner of the laptop
    is a grant for the LAPTOP, and says nothing about the company's machines.
    """
    kwargs = {"approved_corporate": []}
    kwargs.update(over)
    rule = source.default_policy(**kwargs)
    rule.devices[PERSONAL]["grants"] = [OWNER]
    return rule


def a_card(body="B"):
    """A rendered card whose CONTENT carries `body`, so two calls differ."""
    return monitor.render_cardkit(monitor.monitor_model(
        devices=[{"device_ref": "device:x", "display_name": "X", "platform": "linux",
                  "reachability": {"state": "KNOWN", "observed_at": NOW},
                  "readings": [monitor.reading("CPU", 1, unit="%", note=body,
                                               observed_at=NOW)]}],
        services=[], now=NOW))


class RealTransport:
    """NOT the mock: `type(...) is not MockTransport`, so the REAL checks apply."""

    def __init__(self, *, recipient=None):
        self.recipient = recipient
        self.calls = []

    def send(self, *, card, action, version, remote_id=None):
        self.calls.append({"action": action, "version": version,
                           "remote_id": remote_id})
        return {"state": "DELIVERED", "remote_id": remote_id or "om_real_0001"}


# ---------------------------------------------------------------------------
# 1. Coverage: the declared inventory is what the card has, not what answered
# ---------------------------------------------------------------------------

class CoverageTest(unittest.TestCase):
    def test_every_declared_device_is_a_row_with_no_snapshots_at_all(self):
        model = source.fleet_model(snapshots=[], now=NOW)
        self.assertEqual(len(model["devices"]), len(source.EXPECTED_DEVICES))

    def test_a_device_with_no_provider_is_present_and_unknown(self):
        model = source.fleet_model(snapshots=[], now=NOW)
        personal = next(d for d in model["devices"] if d["device_ref"] == PERSONAL)
        self.assertEqual(personal["reachability"]["state"], "UNKNOWN")
        self.assertEqual(personal["readings"], [])
        self.assertFalse(personal["observed_ever"])

    def test_a_snapshot_device_is_known_and_the_rest_are_not(self):
        model = source.fleet_model(snapshots=[(TOKYO, snapshot())], now=NOW)
        states = {d["device_ref"]: d["reachability"]["state"] for d in model["devices"]}
        self.assertEqual(states[TOKYO], "KNOWN")
        self.assertEqual(states[PERSONAL], "UNKNOWN")

    def test_a_snapshot_for_an_undeclared_device_is_appended_not_dropped(self):
        model = source.fleet_model(
            snapshots=[("device:brand-new", snapshot())], now=NOW)
        refs = [d["device_ref"] for d in model["devices"]]
        self.assertEqual(len(refs), len(source.EXPECTED_DEVICES) + 1)
        self.assertIn("device:brand-new", refs)

    def test_the_row_order_follows_the_inventory(self):
        model = source.fleet_model(snapshots=[], now=NOW)
        self.assertEqual([d["device_ref"] for d in model["devices"]],
                         [e["device_ref"] for e in source.EXPECTED_DEVICES])

    def test_an_unknown_row_renders_with_no_fake_number(self):
        model = source.model_from_fixture()
        card = monitor.render_cardkit(model)
        personal = next(d for d in model["devices"] if d["device_ref"] == PERSONAL)
        self.assertEqual(personal["readings"], [])
        text = " ".join(md_all(card))
        self.assertIn("DellPCOS", text)
        self.assertIn("尚未接入", text)

    def test_never_seen_is_grey_unconfirmed_not_a_red_outage(self):
        model = source.fleet_model(snapshots=[], now=NOW)
        self.assertEqual(monitor._fleet_verdict(model), "UNCONFIRMED")
        self.assertFalse(monitor.is_urgent(model))
        self.assertEqual(model["attention"], [])

    def test_went_silent_is_still_a_critical_finding(self):
        device = {"device_ref": "device:x", "display_name": "X", "platform": "linux",
                  "reachability": {"state": "UNKNOWN", "reason": "没有传输层报告"},
                  "readings": []}  # observed_ever absent -> True
        model = monitor.monitor_model(devices=[device], now=NOW)
        self.assertEqual(model["attention"][0]["severity"], "CRITICAL")
        self.assertEqual(monitor._fleet_verdict(model), "CRITICAL")


# ---------------------------------------------------------------------------
# 2. The audience policy: default DENIED, corporate is not public
# ---------------------------------------------------------------------------

class AudiencePolicyTest(unittest.TestCase):
    def test_nothing_is_visible_by_default(self):
        # The bug this pins: corporate devices answered True for ANY recipient.
        rule = source.default_policy()
        for ref in (TOKYO, A6000, FJPCOS, PERSONAL):
            self.assertFalse(rule.allows(CORP_CHAT, ref), ref)
            self.assertFalse(rule.allows(STRANGER, ref), ref)

    def test_an_unapproved_chat_is_refused_even_for_corporate_devices(self):
        rule = source.default_policy()  # nobody approved
        self.assertFalse(rule.allows(OTHER_CHAT, TOKYO))
        rule2 = corp_policy()
        self.assertTrue(rule2.allows(CORP_CHAT, TOKYO))
        self.assertFalse(rule2.allows(OTHER_CHAT, TOKYO),
                         "approving one chat approves exactly that chat")

    def test_an_unknown_recipient_is_refused(self):
        rule = corp_policy()
        for weird in ("", "ou_no_prefix", "everything", "chat:", ":x", None):
            self.assertFalse(rule.allows(weird, TOKYO), repr(weird))

    def test_a_private_device_is_denied_without_a_grant(self):
        rule = corp_policy()
        self.assertFalse(rule.allows(CORP_CHAT, PERSONAL))
        self.assertFalse(rule.allows(STRANGER, PERSONAL))

    def test_a_corporate_approval_never_reaches_a_private_device(self):
        # The inference that must not exist: "approved for the company" is not
        # "approved for somebody's own laptop".
        rule = corp_policy()
        self.assertTrue(rule.allows(CORP_CHAT, TOKYO))
        self.assertFalse(rule.allows(CORP_CHAT, PERSONAL))
        self.assertFalse(rule.allows(OWNER, PERSONAL),
                         "an owner still needs a grant; being the owner is not one")

    def test_a_grant_names_the_exact_recipient(self):
        rule = owner_policy()
        self.assertTrue(rule.allows(OWNER, PERSONAL))
        self.assertFalse(rule.allows(STRANGER, PERSONAL),
                         "a grant for one person is not a grant for everyone")
        self.assertFalse(rule.allows(CORP_CHAT, PERSONAL),
                         "a person's laptop does not become the channel's")

    def test_an_unlisted_device_defaults_to_private(self):
        rule = source.AudiencePolicy(devices={})
        self.assertEqual(rule.visibility("device:whatever"), source.VISIBILITY_PRIVATE)
        self.assertFalse(rule.allows(CORP_CHAT, "device:whatever"))

    def test_a_document_round_trips_through_the_loader(self):
        rule = owner_policy()
        again = source.load_audience_policy(source.audience_policy_document(rule))
        self.assertFalse(again.allows(OTHER_CHAT, TOKYO))
        self.assertTrue(again.allows(CORP_CHAT, TOKYO))
        self.assertTrue(again.allows(OWNER, PERSONAL))
        self.assertFalse(again.allows(CORP_CHAT, PERSONAL))

    def test_an_unknown_visibility_word_is_refused(self):
        with self.assertRaises(source.AudienceRefused):
            source.load_audience_policy({
                "schema": source.AUDIENCE_SCHEMA,
                "devices": {PERSONAL: {"visibility": "sort-of-private"}}})

    def test_a_malformed_grant_is_refused(self):
        with self.assertRaises(source.AudienceRefused):
            source.load_audience_policy({
                "schema": source.AUDIENCE_SCHEMA,
                "devices": {PERSONAL: {"visibility": "private",
                                       "grants": ["ou_no_prefix"]}}})

    def test_a_malformed_corporate_approval_is_refused(self):
        with self.assertRaises(source.AudienceRefused):
            source.load_audience_policy({
                "schema": source.AUDIENCE_SCHEMA, "devices": {},
                "approved_corporate": ["nope"]})

    def test_an_unknown_schema_is_refused(self):
        with self.assertRaises(source.AudienceRefused):
            source.load_audience_policy({"schema": "something-else", "devices": {}})

    def test_the_recipient_spelling_matches_the_transport(self):
        spec = importlib.util.spec_from_file_location(
            "serveros_runtime_message_transport", RUNTIME / "message_transport.py")
        mt = sys.modules.get("serveros_runtime_message_transport")
        if mt is None:
            mt = importlib.util.module_from_spec(spec)
            sys.modules["serveros_runtime_message_transport"] = mt
            spec.loader.exec_module(mt)
        self.assertEqual(tuple(source.RECIPIENT_KINDS),
                         tuple(audience.RECIPIENT_KINDS))
        self.assertEqual(mt.RECIPIENT_KINDS.get("user"), "--user-id")
        self.assertEqual(mt.RECIPIENT_KINDS.get("chat"), "--chat-id")

    def test_the_model_omits_a_denied_device_entirely(self):
        model = source.model_from_fixture(audience=CORP_CHAT, policy=corp_policy())
        refs = [d["device_ref"] for d in model["devices"]]
        self.assertNotIn(PERSONAL, refs, "a denied device is removed, not hidden")
        self.assertEqual(model["audience"]["withheld"], 1)

    def test_an_unapproved_audience_gets_no_devices_at_all(self):
        # Default denial reaches the card: the whole inventory is withheld.
        model = source.model_from_fixture(audience=OTHER_CHAT)
        self.assertEqual(model["devices"], [])
        self.assertEqual(model["audience"]["withheld"], len(source.EXPECTED_DEVICES))

    def test_the_owner_sees_the_private_device_when_granted(self):
        model = source.model_from_fixture(audience=OWNER, policy=owner_policy())
        refs = [d["device_ref"] for d in model["devices"]]
        self.assertIn(PERSONAL, refs)
        self.assertEqual(model["audience"]["withheld"], 0)

    def test_the_private_card_is_the_laptop_and_not_the_company_machines(self):
        # The other direction of the separation: a grant for the laptop is not a
        # corporate approval, so this owner's card holds the laptop and nothing
        # else — the two audiences are not the same audience.
        model = source.model_from_fixture(audience=OWNER,
                                          policy=owner_only_policy())
        refs = [d["device_ref"] for d in model["devices"]]
        self.assertEqual(refs, [PERSONAL])
        self.assertEqual(model["audience"]["withheld"],
                         len(source.EXPECTED_DEVICES) - 1)

    def test_the_company_card_has_three_devices_and_no_private_one(self):
        model = source.model_from_fixture(audience=CORP_CHAT, policy=corp_policy())
        refs = sorted(d["device_ref"] for d in model["devices"])
        self.assertEqual(refs, ["device:llm-lab-dell", "device:tokyo-shared-dev-01",
                                "device:work-fj"])


# ---------------------------------------------------------------------------
# 3. The GATE: a signed verdict, bound to recipient + devices + card
# ---------------------------------------------------------------------------

class SendGateTest(unittest.TestCase):
    def _plan(self, body="B"):
        return plan.plan_update(last=None, card=a_card(body), at=NOW, window_days=14)

    def _card(self, body="B"):
        return a_card(body)

    def _sealed(self, devices, *, recipient, policy, card=None, body="B"):
        card = card if card is not None else self._card(body)
        return source.authorize_devices(
            devices, audience=recipient, policy=policy,
            card_signature=plan.freeze(card)["payload_hash"])

    # -- the bypasses, attempted from the outside --------------------------

    def test_a_plain_ok_dictionary_authorizes_nothing(self):
        mock = plan.MockTransport()
        got = plan.apply_plan(self._plan(), self._card(), at=NOW, transport=mock,
                              authorization={"ok": True, "audience": CORP_CHAT,
                                             "denied": []})
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.VERDICT_UNTRUSTED)
        self.assertEqual(mock.calls, [])
        self.assertIsNone(got["receipt"])

    def test_a_string_or_none_or_object_authorizes_nothing(self):
        for junk in ("ok", None, 1, object(), [{"ok": True}]):
            got = plan.apply_plan(self._plan(), self._card(), at=NOW,
                                  transport=plan.MockTransport(),
                                  authorization=junk)
            self.assertEqual(got["state"], plan.REFUSED, repr(junk))
            self.assertEqual(got["reason"], plan.VERDICT_UNTRUSTED, repr(junk))

    def test_a_hand_built_verdict_is_refused_where_it_is_built(self):
        # The type is signed, so the forgery cannot even be constructed.
        with self.assertRaises(source.AudienceRefused):
            audience.AudienceVerdict(
                audience=CORP_CHAT, device_refs=(TOKYO,), device_signature="x",
                allowed=(TOKYO,), denied=(), ok=True, issuer="policy",
                card_signature=None, reason=None, mac="0" * 64)

    def test_a_preview_gate_cannot_authorize_a_real_transport(self):
        real = RealTransport()
        preview = plan.preview_gate([TOKYO, PERSONAL], audience=CORP_CHAT)
        got = plan.apply_plan(self._plan(), self._card(), at=NOW, transport=real,
                              authorization=preview, recipient=CORP_CHAT)
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.PREVIEW_ONLY)
        self.assertEqual(real.calls, [], "the transport was never touched")
        self.assertIsNone(got["receipt"])

    def test_a_preview_gate_still_works_for_the_local_mock(self):
        # Nothing leaves, so a local render needs no recipient authorization.
        got = plan.apply_plan(self._plan(), self._card(), at=NOW,
                              transport=plan.MockTransport(),
                              authorization=plan.preview_gate([TOKYO]))
        self.assertEqual(got["state"], "DELIVERED")

    def test_a_verdict_for_another_recipient_is_refused(self):
        real = RealTransport(recipient=OTHER_CHAT)
        verdict = self._sealed([TOKYO], recipient=CORP_CHAT, policy=corp_policy())
        got = plan.apply_plan(self._plan(), self._card(), at=NOW, transport=real,
                              authorization=verdict)
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.AUDIENCE_MISMATCH)
        self.assertEqual(real.calls, [])

    def test_a_verdict_for_another_card_is_refused(self):
        # The attack this stops: get a verdict for a harmless card, then send
        # the card with the private row added to it.
        real = RealTransport()
        verdict = self._sealed([TOKYO], recipient=CORP_CHAT, policy=corp_policy(),
                               body="B")
        tampered = self._card(body="B-WITH-PRIVATE-ADDED")
        got = plan.apply_plan(self._plan("B-WITH-PRIVATE-ADDED"), tampered, at=NOW,
                              transport=real, authorization=verdict,
                              recipient=CORP_CHAT)
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.VERDICT_MISMATCH)
        self.assertEqual(real.calls, [])

    def test_a_verdict_over_another_device_set_is_refused(self):
        real = RealTransport()
        verdict = self._sealed([TOKYO, A6000], recipient=CORP_CHAT,
                               policy=corp_policy())
        got = plan.apply_plan(self._plan(), self._card(), at=NOW, transport=real,
                              authorization=verdict, recipient=CORP_CHAT,
                              device_refs=[TOKYO, A6000, PERSONAL])
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.DEVICE_MISMATCH)
        self.assertEqual(real.calls, [])

    def test_a_policy_verdict_with_no_card_binding_cannot_send_for_real(self):
        real = RealTransport()
        verdict = source.authorize_devices([TOKYO], audience=CORP_CHAT,
                                           policy=corp_policy())  # no card sig
        got = plan.apply_plan(self._plan(), self._card(), at=NOW, transport=real,
                              authorization=verdict, recipient=CORP_CHAT)
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.VERDICT_MISMATCH)

    def test_an_unapproved_recipient_is_denied_and_nothing_is_sent(self):
        real = RealTransport()
        verdict = source.authorize_devices([TOKYO], audience=OTHER_CHAT,
                                           policy=corp_policy())
        self.assertFalse(verdict.ok)
        got = plan.apply_plan(self._plan(), self._card(), at=NOW, transport=real,
                              authorization=verdict, recipient=OTHER_CHAT)
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.AUDIENCE_DENIED)
        self.assertEqual(real.calls, [])
        self.assertIsNone(got["receipt"])

    def test_the_private_device_is_denied_to_the_company_chat_by_the_gate(self):
        real = RealTransport()
        verdict = source.authorize_devices([PERSONAL], audience=CORP_CHAT,
                                           policy=owner_policy())
        self.assertFalse(verdict.ok)
        got = plan.apply_plan(self._plan(), self._card(), at=NOW, transport=real,
                              authorization=verdict, recipient=CORP_CHAT)
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.AUDIENCE_DENIED)
        self.assertEqual(real.calls, [])

    # -- and the send that IS allowed --------------------------------------

    def test_an_allowed_send_reaches_a_real_transport_with_a_receipt(self):
        real = RealTransport(recipient=CORP_CHAT)
        verdict = self._sealed([TOKYO, A6000], recipient=CORP_CHAT,
                               policy=corp_policy())
        got = plan.apply_plan(self._plan(), self._card(), at=NOW, transport=real,
                              authorization=verdict, recipient=CORP_CHAT,
                              device_refs=[TOKYO, A6000])
        self.assertEqual(got["state"], "DELIVERED")
        self.assertEqual(len(real.calls), 1)
        self.assertIsNotNone(got["receipt"])

    def test_the_gate_is_the_same_rule_the_model_used(self):
        rule = corp_policy()
        model = source.model_from_fixture(audience=CORP_CHAT, policy=rule)
        shown = {d["device_ref"] for d in model["devices"]}
        for ref in (TOKYO, A6000, PERSONAL, FJPCOS):
            self.assertEqual(rule.allows(CORP_CHAT, ref), ref in shown, ref)

    def test_a_noop_needs_no_authorization(self):
        p = plan.plan_update(last={"version": "x", "at": NOW,
                                   "binding": {"remote_id": "om_1", "at": NOW}},
                             card=self._card(), at=NOW, cadence_seconds=600,
                             window_days=14)
        got = plan.apply_plan(p, self._card(), at=NOW)
        self.assertEqual(got["state"], "NOOP")
        self.assertIsNone(got["receipt"])


# ---------------------------------------------------------------------------
# 4. Nothing leaks the recipient
# ---------------------------------------------------------------------------

class NoRecipientLeakTest(unittest.TestCase):
    def test_the_parser_refusal_does_not_echo_what_it_refused(self):
        secretive = "ou_thisShouldNotAppearAnywhere0000"
        with self.assertRaises(source.AudienceRefused) as caught:
            audience.parse_recipient(secretive)
        self.assertNotIn(secretive, str(caught.exception))
        self.assertNotIn("thisShouldNotAppear", str(caught.exception))

    def test_the_visible_refusal_does_not_echo_the_recipient(self):
        rule = corp_policy()
        got = rule.visible(STRANGER, [TOKYO, A6000])
        self.assertNotIn(STRANGER, json.dumps(got, ensure_ascii=False))
        self.assertNotIn("stranger", json.dumps(got, ensure_ascii=False))

    def test_the_model_carries_no_recipient(self):
        model = source.model_from_fixture(audience=CORP_CHAT, policy=corp_policy())
        blob = json.dumps(model, ensure_ascii=False)
        self.assertNotIn(CORP_CHAT, blob)
        self.assertNotIn("oc_corporate", blob)
        self.assertEqual(set(model["audience"]), {"withheld", "kind"})

    def test_the_rendered_card_carries_no_recipient(self):
        model = source.model_from_fixture(audience=CORP_CHAT, policy=corp_policy())
        blob = json.dumps(monitor.render_cardkit(model), ensure_ascii=False)
        self.assertNotIn(CORP_CHAT, blob)
        self.assertNotIn("oc_corporate", blob)

    def test_a_refusal_result_carries_no_recipient(self):
        got = plan.apply_plan(self._plan_card(), self._plan_card(), at=NOW,
                              transport=RealTransport(), authorization=None)
        self.assertNotIn(CORP_CHAT, json.dumps(got, ensure_ascii=False))

    @staticmethod
    def _plan_card():
        card = a_card()
        return plan.plan_update(last=None, card=card, at=NOW, window_days=14)


# ---------------------------------------------------------------------------
# 5. The trend archive: timestamped, attributed, three dayparts
# ---------------------------------------------------------------------------

class DaypartTest(unittest.TestCase):
    def test_the_three_dayparts(self):
        self.assertEqual(source.daypart_of("2026-10-13T08:00:00+09:00"), "morning")
        self.assertEqual(source.daypart_of("2026-10-13T14:00:00+09:00"), "afternoon")
        self.assertEqual(source.daypart_of("2026-10-13T22:00:00+09:00"), "night")
        self.assertEqual(source.daypart_of("2026-10-13T02:00:00+09:00"), "night")

    def test_the_offset_is_the_samples_own_not_utc(self):
        self.assertEqual(source.daypart_of("2026-10-12T23:00:00+09:00"), "night")
        self.assertEqual(source.daypart_of("2026-10-13T09:00:00+09:00"), "morning")

    def test_an_unparseable_instant_has_no_daypart(self):
        self.assertIsNone(source.daypart_of("not-a-time"))
        self.assertIsNone(source.daypart_of(None))


class TrendArchiveTest(unittest.TestCase):
    def setUp(self):
        import tempfile
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "state" / "fleet-trend.json"
        self.archive = source.TrendArchive(self.path)

    def sample(self, **over):
        doc = {"device_ref": TOKYO, "label": "CPU", "value": 30,
               "observed_at": "2026-10-13T09:00:00+09:00", "attributed": True,
               "source": "fixture"}
        doc.update(over)
        return doc

    def test_a_good_sample_is_stored_and_read_back(self):
        self.archive.record(self.sample())
        self.assertEqual(self.archive.series(TOKYO, "CPU"), [30.0])

    def test_a_sample_without_a_timestamp_is_refused(self):
        with self.assertRaises(source.TrendSampleRefused):
            self.archive.record(self.sample(observed_at=None))

    def test_a_sample_that_is_not_attributed_is_refused(self):
        for bad in (False, "yes", None):
            with self.assertRaises(source.TrendSampleRefused):
                self.archive.record(self.sample(attributed=bad))

    def test_a_non_numeric_value_is_refused(self):
        with self.assertRaises(source.TrendSampleRefused):
            self.archive.record(self.sample(value="busy"))

    def test_a_private_field_is_refused(self):
        for field in ("argv", "cmdline", "environ", "cwd", "prompt", "token"):
            with self.assertRaises(source.TrendSampleRefused):
                self.archive.record(self.sample(**{field: "anything"}))

    def test_the_three_dayparts_are_kept_apart(self):
        for value, at in ((10, "2026-10-13T09:00:00+09:00"),
                          (20, "2026-10-13T14:00:00+09:00"),
                          (30, "2026-10-13T22:00:00+09:00")):
            self.archive.record(self.sample(value=value, observed_at=at))
        buckets = self.archive.buckets(TOKYO, "CPU")
        self.assertEqual(buckets["morning"], [10.0])
        self.assertEqual(buckets["afternoon"], [20.0])
        self.assertEqual(buckets["night"], [30.0])

    def test_it_round_trips_through_the_file(self):
        self.archive.record(self.sample(value=42))
        self.archive.commit()
        again = source.TrendArchive(self.path)
        self.assertEqual(again.series(TOKYO, "CPU"), [42.0])

    def test_an_unreadable_file_is_refused_not_replaced(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{ not json", encoding="utf-8")
        with self.assertRaises(source.TrendSampleRefused):
            source.TrendArchive(self.path).record(self.sample())

    def test_a_bad_daypart_name_is_refused(self):
        with self.assertRaises(source.TrendSampleRefused):
            self.archive.series(TOKYO, "CPU", daypart="teatime")

    def test_a_device_from_the_archive_carries_the_stored_trend(self):
        for value, at in ((10, "2026-10-13T08:00:00+09:00"),
                          (20, "2026-10-13T14:00:00+09:00"),
                          (30, "2026-10-13T22:00:00+09:00")):
            self.archive.record(self.sample(value=value, observed_at=at))
        device = source.device_from_archive(TOKYO, self.archive, labels=["CPU"],
                                            display_name="东京", observed_at=NOW)
        self.assertEqual(device["readings"][0]["trend"], [10.0, 20.0, 30.0])

    def test_a_label_with_no_sample_yields_an_unobserved_reading(self):
        device = source.device_from_archive(TOKYO, self.archive, labels=["GPU"],
                                            observed_at=NOW)
        self.assertEqual(device["readings"][0]["state"], monitor.STATE_UNKNOWN)
        self.assertIn("归档", device["readings"][0]["reason"])

    def test_the_archive_path_sits_beside_the_ledger(self):
        ledger = Path("/tmp/state/serveros/alerts/state.json")
        self.assertEqual(source.trend_archive_path(ledger).parent, ledger.parent)
        self.assertIn("fleet-trend", source.trend_archive_path(ledger).name)


# ---------------------------------------------------------------------------
# 6. The rendered card: CardKit 2.0, 390px budget, audience-correct
# ---------------------------------------------------------------------------

class RenderedCardTest(unittest.TestCase):
    def test_the_corporate_card_carries_no_private_device(self):
        model = source.model_from_fixture(audience=CORP_CHAT, policy=corp_policy())
        text = " ".join(md_all(monitor.render_cardkit(model)))
        self.assertNotIn("DellPCOS", text)
        self.assertIn("东京共享开发机", text)

    def test_the_card_is_cardkit_2_0_with_update_multi(self):
        model = source.model_from_fixture(audience=CORP_CHAT, policy=corp_policy())
        card = monitor.render_cardkit(model)
        self.assertEqual(card["schema"], "2.0")
        self.assertIs(card["config"]["update_multi"], True)

    def test_every_line_fits_the_390px_budget(self):
        model = source.model_from_fixture(audience=CORP_CHAT, policy=corp_policy())
        card = monitor.render_cardkit(model, rich=True, charts_verified=True)
        for content in md_all(card):
            for line in content.split("\n"):
                self.assertLessEqual(len(line), monitor.MOBILE_LINE_CHARS, line)

    def test_element_ids_stay_inside_the_contract(self):
        model = source.model_from_fixture()
        for eid in monitor._walk_element_ids(monitor.render_cardkit(model), []):
            self.assertRegex(eid, r"^[A-Za-z][A-Za-z0-9_-]*$")
            self.assertLessEqual(len(eid), monitor.ELEMENT_ID_MAX)

    def test_the_card_serialises_and_fits_the_byte_budget(self):
        model = source.model_from_fixture()
        card = monitor.render_cardkit(model)
        blob = json.dumps(card, ensure_ascii=False).encode("utf-8")
        self.assertLessEqual(len(blob), monitor.MAX_CARD_BYTES)
        monitor.validate(card)

    def test_the_checked_in_card_fixture_is_valid_and_audience_correct(self):
        path = RUNTIME / "fleet-monitor-card.fixture.json"
        card = json.loads(path.read_text(encoding="utf-8"))
        monitor.validate(card)
        self.assertEqual(card["schema"], "2.0")
        self.assertIs(card["config"]["update_multi"], True)
        blob = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("DellPCOS", blob)
        self.assertNotIn("personal-dell", blob)
        self.assertIn("东京共享开发机", blob)


# ---------------------------------------------------------------------------
# 7. Mock delivery: patch the same id, expire, fail, and do not duplicate
# ---------------------------------------------------------------------------

class MockDeliveryTest(unittest.TestCase):
    MOCK_GATE = staticmethod(lambda refs=(): plan.preview_gate(refs))

    def test_a_second_tick_patches_the_same_remote_id(self):
        mock = plan.MockTransport()
        p1 = plan.plan_update(last=None, card=a_card("B"), at=NOW, window_days=14)
        first = plan.apply_plan(p1, a_card("B"), at=NOW, transport=mock,
                                authorization=self.MOCK_GATE())
        remote = first["remote_id"]
        p2 = plan.plan_update(
            last={"version": p1["version"], "at": NOW,
                  "binding": {"remote_id": remote, "at": NOW}},
            card=a_card("C"), at=LATER, cadence_seconds=600, window_days=14)
        second = plan.apply_plan(p2, a_card("C"), at=LATER, transport=mock,
                                 authorization=self.MOCK_GATE())
        self.assertEqual(p2["action"], plan.PATCH_MESSAGE)
        self.assertEqual(second["remote_id"], remote,
                         "the same message is patched, not a second one created")
        self.assertEqual(len(mock.calls), 2)

    def test_an_unchanged_card_does_not_send_a_duplicate(self):
        p1 = plan.plan_update(last=None, card=a_card("B"), at=NOW, window_days=14)
        mock = plan.MockTransport()
        plan.apply_plan(p1, a_card("B"), at=NOW, transport=mock,
                        authorization=self.MOCK_GATE())
        p2 = plan.plan_update(
            last={"version": p1["version"], "at": NOW,
                  "binding": {"remote_id": "om_1", "at": NOW}},
            card=a_card("B"), at=LATER, cadence_seconds=600, window_days=14)
        got = plan.apply_plan(p2, a_card("B"), at=LATER, transport=mock,
                              authorization=self.MOCK_GATE())
        self.assertEqual(got["state"], "NOOP")
        self.assertEqual(len(mock.calls), 1)

    def test_an_expired_binding_rotates_to_a_new_message(self):
        p = plan.plan_update(
            last={"version": "v-old", "at": "2020-01-01T00:00:00+00:00",
                  "binding": {"remote_id": "om_old",
                              "at": "2020-01-01T00:00:00+00:00"}},
            card=a_card("B"), at=NOW, window_days=14)
        self.assertEqual(p["action"], plan.NEW_MESSAGE)
        self.assertEqual(p["change"], "rotate")

    def test_a_failed_patch_is_not_recorded_as_delivered(self):
        class Failing(RealTransport):
            def send(self, *, card, action, version, remote_id=None):
                self.calls.append({"action": action})
                return {"state": "FAILED", "reason": "通道拒绝", "remote_id": None}

        transport = Failing(recipient=CORP_CHAT)
        card = a_card("B")
        verdict = source.authorize_devices(
            [TOKYO], audience=CORP_CHAT, policy=corp_policy(),
            card_signature=plan.freeze(card)["payload_hash"])
        p = plan.plan_update(last=None, card=card, at=NOW, window_days=14)
        got = plan.apply_plan(p, card, at=NOW, transport=transport,
                              authorization=verdict, recipient=CORP_CHAT)
        self.assertEqual(got["state"], "FAILED")
        self.assertNotEqual(got["state"], "DELIVERED")
        self.assertIsNone(got["remote_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
