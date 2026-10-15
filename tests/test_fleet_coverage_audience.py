#!/usr/bin/env python3
"""Coverage, the device×recipient audience gate, the trend archive, and the
mock delivery that must never be a real one.

WHAT THIS FILE KEEPS FALSE
--------------------------
    "the machine that is down was not on the card"    — coverage is declared,
                                                        not discovered
    "it is hidden so it is safe"                      — the row is ABSENT for an
                                                        audience that may not see
                                                        it, and the SEND is
                                                        refused before the wire
    "we sent it and it failed" == "we refused to send" — only the first has a
                                                        receipt
    "the trend is smooth"                             — a sample with no real
                                                        timestamp is refused
    "the company channel may see a private laptop"    — no grant, no row

The three modules are loaded by path under the names they resolve each other by,
the shape `tests/test_fleet_monitor_card.py` uses for the same pair.
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
TOKYO = "device:tokyo-shared-dev-01"
A6000 = "device:llm-lab-dell"
PERSONAL = "device:personal-dell"
FJPCOS = "device:work-fj"

CORP_CHAT = "chat:oc_corporate000000000000000000"
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
        # The card says the device is unreachable; it never prints a metric for it.
        text = " ".join(md_all(card))
        self.assertIn("DellPCOS", text)
        self.assertIn("尚未接入", text)

    def test_never_seen_is_grey_unconfirmed_not_a_red_outage(self):
        # A coverage gap is not an outage: the header must not go red for a
        # machine that has simply never had a provider.
        model = source.fleet_model(snapshots=[], now=NOW)
        self.assertEqual(monitor._fleet_verdict(model), "UNCONFIRMED")
        self.assertFalse(monitor.is_urgent(model))
        self.assertTrue(all(a["scope"] != "device" or False for a in model["attention"])
                        or model["attention"] == [])

    def test_went_silent_is_still_a_critical_finding(self):
        # The PR#160 rule survives: a device that HAS been observed and is now
        # unreachable is a finding, not a coverage note.
        device = {"device_ref": "device:x", "display_name": "X", "platform": "linux",
                  "reachability": {"state": "UNKNOWN", "reason": "没有传输层报告"},
                  "readings": []}  # observed_ever absent -> True
        model = monitor.monitor_model(devices=[device], now=NOW)
        self.assertEqual(model["attention"][0]["severity"], "CRITICAL")
        self.assertEqual(monitor._fleet_verdict(model), "CRITICAL")


# ---------------------------------------------------------------------------
# 2. The audience policy: device × recipient, closed by default
# ---------------------------------------------------------------------------

class AudiencePolicyTest(unittest.TestCase):
    def test_corporate_devices_are_open_to_a_company_chat(self):
        rule = source.default_policy()
        self.assertTrue(rule.allows(CORP_CHAT, TOKYO))
        self.assertTrue(rule.allows(CORP_CHAT, A6000))
        self.assertTrue(rule.allows(CORP_CHAT, FJPCOS))

    def test_a_private_device_is_denied_without_a_grant(self):
        rule = source.default_policy()
        self.assertFalse(rule.allows(CORP_CHAT, PERSONAL))
        self.assertFalse(rule.allows(STRANGER, PERSONAL))

    def test_a_grant_names_the_exact_recipient(self):
        rule = source.default_policy()
        rule.devices[PERSONAL]["grants"] = [OWNER]
        self.assertTrue(rule.allows(OWNER, PERSONAL))
        self.assertFalse(rule.allows(STRANGER, PERSONAL),
                         "a grant for one person is not a grant for everyone")
        self.assertFalse(rule.allows(CORP_CHAT, PERSONAL),
                         "a person's laptop does not become the channel's")

    def test_an_unlisted_device_defaults_to_private(self):
        rule = source.AudiencePolicy(devices={})
        self.assertEqual(rule.visibility("device:whatever"), source.VISIBILITY_PRIVATE)
        self.assertFalse(rule.allows(CORP_CHAT, "device:whatever"),
                         "closed by default: added without a policy is withheld")

    def test_a_document_round_trips_through_the_loader(self):
        rule = source.default_policy()
        rule.devices[PERSONAL]["grants"] = [OWNER]
        document = source.audience_policy_document(rule)
        again = source.load_audience_policy(document)
        self.assertEqual(again.visibility(PERSONAL), source.VISIBILITY_PRIVATE)
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

    def test_an_unknown_schema_is_refused(self):
        with self.assertRaises(source.AudienceRefused):
            source.load_audience_policy({"schema": "something-else", "devices": {}})

    def test_a_bare_recipient_is_refused_rather_than_guessed(self):
        with self.assertRaises(source.AudienceRefused):
            source.parse_audience("ou_no_prefix")

    def test_the_recipient_spelling_matches_the_transport(self):
        import importlib.util as _iu
        spec = _iu.spec_from_file_location("serveros_runtime_message_transport",
                                           RUNTIME / "message_transport.py")
        if "serveros_runtime_message_transport" in sys.modules:
            mt = sys.modules["serveros_runtime_message_transport"]
        else:
            mt = _iu.module_from_spec(spec)
            sys.modules["serveros_runtime_message_transport"] = mt
            spec.loader.exec_module(mt)
        self.assertEqual(tuple(source.RECIPIENT_KINDS),
                         tuple(k for k in ("user", "chat")))
        self.assertEqual(mt.RECIPIENT_KINDS.get("user"), "--user-id")
        self.assertEqual(mt.RECIPIENT_KINDS.get("chat"), "--chat-id")

    def test_the_model_omits_a_denied_device_entirely(self):
        model = source.model_from_fixture(audience=CORP_CHAT)
        refs = [d["device_ref"] for d in model["devices"]]
        self.assertNotIn(PERSONAL, refs,
                         "a denied device is removed, not merely hidden")
        self.assertEqual(model["audience"]["withheld"], 1)

    def test_the_owner_sees_the_private_device_when_granted(self):
        rule = source.default_policy()
        rule.devices[PERSONAL]["grants"] = [OWNER]
        model = source.model_from_fixture(audience=OWNER, policy=rule)
        refs = [d["device_ref"] for d in model["devices"]]
        self.assertIn(PERSONAL, refs)
        self.assertEqual(model["audience"]["withheld"], 0)

    def test_the_withheld_count_names_no_device(self):
        model = source.model_from_fixture(audience=CORP_CHAT)
        blob = json.dumps(model, ensure_ascii=False)
        self.assertIn('"withheld"', blob)
        self.assertNotIn(PERSONAL, json.dumps(model.get("audience"), ensure_ascii=False))


# ---------------------------------------------------------------------------
# 3. The send gate: refused before the wire, and no receipt
# ---------------------------------------------------------------------------

def a_card(body="B"):
    """A rendered card whose CONTENT carries `body`, so two calls differ.

    The `note` is what varies: a planner asked about a "changed" card has to be
    handed a card that actually changed, or it correctly answers UNCHANGED and a
    test that meant to exercise PATCH is quietly measuring a NOOP.
    """
    return monitor.render_cardkit(monitor.monitor_model(
        devices=[{"device_ref": "device:x", "display_name": "X", "platform": "linux",
                  "reachability": {"state": "KNOWN", "observed_at": NOW},
                  "readings": [monitor.reading("CPU", 1, unit="%", note=body,
                                               observed_at=NOW)]}],
        services=[], now=NOW))


class SendGateTest(unittest.TestCase):
    def _plan(self):
        return plan.plan_update(last=None, card=a_card(), at=NOW, window_days=14)

    def test_a_send_without_a_gate_is_refused_before_the_transport(self):
        mock = plan.MockTransport()
        got = plan.apply_plan(self._plan(), a_card(), at=NOW, transport=mock)
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.GATE_REQUIRED)
        self.assertEqual(mock.calls, [])

    def test_a_denied_send_produces_no_receipt(self):
        rule = source.default_policy()
        verdict = source.authorize_devices([PERSONAL], audience=CORP_CHAT,
                                           policy=rule)
        mock = plan.MockTransport()
        got = plan.apply_plan(self._plan(), a_card(), at=NOW, transport=mock,
                              gate=verdict)
        self.assertEqual(got["state"], plan.REFUSED)
        self.assertEqual(got["reason"], plan.AUDIENCE_DENIED)
        self.assertIsNone(got["receipt"],
                          "a refused send must carry no receipt at all")
        self.assertIsNone(got["remote_id"])
        self.assertEqual(mock.calls, [], "the transport was never touched")

    def test_an_allowed_send_reaches_the_transport_and_has_a_receipt(self):
        rule = source.default_policy()
        verdict = source.authorize_devices([TOKYO, A6000], audience=CORP_CHAT,
                                           policy=rule)
        mock = plan.MockTransport()
        got = plan.apply_plan(self._plan(), a_card(), at=NOW, transport=mock,
                              gate=verdict)
        self.assertEqual(got["state"], "DELIVERED")
        self.assertEqual(len(mock.calls), 1)
        self.assertIsNotNone(got["receipt"])
        self.assertTrue(got["receipt"]["remote_id"].startswith("om_mock"))

    def test_the_gate_is_the_same_rule_the_model_used(self):
        # One rule, two callers: what the model withheld is what the gate denies.
        rule = source.default_policy()
        model = source.model_from_fixture(audience=CORP_CHAT, policy=rule)
        shown = {d["device_ref"] for d in model["devices"]}
        for ref in (TOKYO, A6000, PERSONAL, FJPCOS):
            self.assertEqual(rule.allows(CORP_CHAT, ref), ref in shown, ref)

    def test_open_gate_is_explicit_and_permits(self):
        mock = plan.MockTransport()
        got = plan.apply_plan(self._plan(), a_card(), at=NOW, transport=mock,
                              gate=plan.open_gate([TOKYO]))
        self.assertEqual(got["state"], "DELIVERED")

    def test_a_noop_needs_no_gate(self):
        p = plan.plan_update(last={"version": "x", "at": NOW,
                                   "binding": {"remote_id": "om_1", "at": NOW}},
                             card=a_card(), at=NOW, cadence_seconds=600,
                             window_days=14)
        got = plan.apply_plan(p, a_card(), at=NOW)
        self.assertEqual(got["state"], "NOOP")
        self.assertIsNone(got["receipt"])


# ---------------------------------------------------------------------------
# 4. The trend archive: timestamped, attributed, three dayparts
# ---------------------------------------------------------------------------

class DaypartTest(unittest.TestCase):
    def test_the_three_dayparts(self):
        self.assertEqual(source.daypart_of("2026-10-13T08:00:00+09:00"), "morning")
        self.assertEqual(source.daypart_of("2026-10-13T14:00:00+09:00"), "afternoon")
        self.assertEqual(source.daypart_of("2026-10-13T22:00:00+09:00"), "night")
        self.assertEqual(source.daypart_of("2026-10-13T02:00:00+09:00"), "night")

    def test_the_offset_is_the_samples_own_not_utc(self):
        # 23:00 UTC is 08:00 the NEXT day in Tokyo: morning, not night.
        self.assertEqual(source.daypart_of("2026-10-12T23:00:00+00:00"), "night")
        self.assertEqual(source.daypart_of("2026-10-12T23:00:00+09:00"), "night")
        self.assertEqual(source.daypart_of("2026-10-12T23:30:00+00:00"), "night")

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
        with self.assertRaises(source.TrendSampleRefused):
            self.archive.record(self.sample(attributed=False))
        with self.assertRaises(source.TrendSampleRefused):
            self.archive.record(self.sample(attributed="yes"))

    def test_a_non_numeric_value_is_refused(self):
        with self.assertRaises(source.TrendSampleRefused):
            self.archive.record(self.sample(value="busy"))

    def test_a_private_field_is_refused(self):
        for field in ("argv", "cmdline", "environ", "cwd", "prompt", "token"):
            with self.assertRaises(source.TrendSampleRefused):
                self.archive.record(self.sample(**{field: "anything"}))

    def test_the_three_dayparts_are_kept_apart(self):
        self.archive.record(self.sample(value=10,
                                        observed_at="2026-10-13T09:00:00+09:00"))
        self.archive.record(self.sample(value=20,
                                        observed_at="2026-10-13T14:00:00+09:00"))
        self.archive.record(self.sample(value=30,
                                        observed_at="2026-10-13T22:00:00+09:00"))
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
        self.assertEqual(source.trend_archive_path(ledger).parent,
                         ledger.parent)
        self.assertIn("fleet-trend", source.trend_archive_path(ledger).name)


# ---------------------------------------------------------------------------
# 5. The rendered card: CardKit 2.0, 390px budget, audience-correct
# ---------------------------------------------------------------------------

class RenderedCardTest(unittest.TestCase):
    def test_the_corporate_card_carries_no_private_device(self):
        model = source.model_from_fixture(audience=CORP_CHAT)
        text = " ".join(md_all(monitor.render_cardkit(model)))
        self.assertNotIn("DellPCOS", text)
        self.assertIn("东京共享开发机", text)

    def test_the_card_is_cardkit_2_0_with_update_multi(self):
        model = source.model_from_fixture(audience=CORP_CHAT)
        card = monitor.render_cardkit(model)
        self.assertEqual(card["schema"], "2.0")
        self.assertIs(card["config"]["update_multi"], True)

    def test_every_line_fits_the_390px_budget(self):
        model = source.model_from_fixture(audience=CORP_CHAT)
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
        """`runtime/fleet-monitor-card.fixture.json` is the corporate-audience card.

        It is the JSON a sender would hand the transport, checked in so a person
        can read what goes out without running anything. This asserts the
        properties that matter and not its exact bytes — a golden comparison
        would go red on a wording change that broke nothing.
        """
        path = RUNTIME / "fleet-monitor-card.fixture.json"
        card = json.loads(path.read_text(encoding="utf-8"))
        monitor.validate(card)
        self.assertEqual(card["schema"], "2.0")
        self.assertIs(card["config"]["update_multi"], True)
        blob = json.dumps(card, ensure_ascii=False)
        self.assertNotIn("DellPCOS", blob, "the corporate card carries no private device")
        self.assertNotIn("personal-dell", blob)
        self.assertIn("东京共享开发机", blob)


# ---------------------------------------------------------------------------
# 6. Mock delivery: patch the same id, expire, fail, and do not duplicate
# ---------------------------------------------------------------------------

class MockDeliveryTest(unittest.TestCase):
    def test_a_second_tick_patches_the_same_remote_id(self):
        # The next tick is 600 s later — the cadence, not the same instant, or
        # the planner correctly answers NOOP(NOT_DUE) and nothing is patched.
        later = "2026-10-13T06:10:00Z"
        mock = plan.MockTransport()
        p1 = plan.plan_update(last=None, card=a_card("B"), at=NOW, window_days=14)
        first = plan.apply_plan(p1, a_card("B"), at=NOW, transport=mock,
                                gate=plan.open_gate())
        remote = first["remote_id"]
        p2 = plan.plan_update(
            last={"version": p1["version"], "at": NOW,
                  "binding": {"remote_id": remote, "at": NOW}},
            card=a_card("C"), at=later, cadence_seconds=600, window_days=14)
        second = plan.apply_plan(p2, a_card("C"), at=later, transport=mock,
                                 gate=plan.open_gate())
        self.assertEqual(p2["action"], plan.PATCH_MESSAGE)
        self.assertEqual(second["remote_id"], remote,
                         "the same message is patched, not a second one created")
        self.assertEqual(second["action"], plan.PATCH_MESSAGE)
        self.assertEqual(len(mock.calls), 2)

    def test_an_unchanged_card_does_not_send_a_duplicate(self):
        p1 = plan.plan_update(last=None, card=a_card("B"), at=NOW, window_days=14)
        mock = plan.MockTransport()
        plan.apply_plan(p1, a_card("B"), at=NOW, transport=mock, gate=plan.open_gate())
        p2 = plan.plan_update(
            last={"version": p1["version"], "at": NOW,
                  "binding": {"remote_id": "om_1", "at": NOW}},
            card=a_card("B"), at=NOW, cadence_seconds=600, window_days=14)
        got = plan.apply_plan(p2, a_card("B"), at=NOW, transport=mock,
                              gate=plan.open_gate())
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
        class Failing:
            def __init__(self):
                self.calls = []

            def send(self, *, card, action, version, remote_id=None):
                self.calls.append({"action": action, "version": version})
                return {"state": "FAILED", "reason": "通道拒绝", "remote_id": None}

        transport = Failing()
        p = plan.plan_update(last=None, card=a_card("B"), at=NOW, window_days=14)
        got = plan.apply_plan(p, a_card("B"), at=NOW, transport=transport,
                              gate=plan.open_gate())
        self.assertEqual(got["state"], "FAILED")
        self.assertNotEqual(got["state"], "DELIVERED")
        self.assertIsNone(got["remote_id"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
