#!/usr/bin/env python3
"""The V13 per-UID account block, and the CardKit 2.0 contract it must obey.

What this file keeps false
--------------------------
    "3.2/8 核 therefore 8 is a cgroup limit"   — a quota here is a SUGGESTION;
                                                  nothing throttles anyone
    "the reading did not arrive, so the quota is 0"
    "here is what that account ran"             — no argv, cwd, env or secret
    "the ids were fine"                          — every id is letter-first ≤20

The account block is the V13 change: one per-UID CPU/RAM line (used / suggested
allowance / soft warning line) inside each device's own fold, plus the admin
collapsed ranking. The privacy and soft-line rules live in
`fleet_monitor_card.validate_model` and `fleet_monitor_source.accept_uid_summary`,
and are checked here where a violation would be built rather than promised.
"""
from __future__ import annotations

import importlib.util
import json
import sys
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


monitor = _load("serveros_fleet_monitor_card", ROOT / "runtime" / "fleet_monitor_card.py")
source = _load("serveros_fleet_monitor_source", ROOT / "runtime" / "fleet_monitor_source.py")

NOW = "2026-10-13T06:00:00Z"
AT = "2026-10-13T05:58:00Z"


def dev(ref="device:x", *, accounts=(), readings=(), reach="KNOWN"):
    reachability = ({"state": reach, "observed_at": AT}
                    if reach == "KNOWN" else {"state": reach, "reason": "没有采集"})
    return {"device_ref": ref, "display_name": "设备 X", "platform": "linux",
            "reachability": reachability, "readings": list(readings),
            "accounts": list(accounts)}


def model_with(*devices):
    model = monitor.monitor_model(devices=list(devices), now=NOW)
    monitor.validate_model(model)
    return model


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


class AccountBlockTest(unittest.TestCase):
    def test_account_is_rendered_inside_the_device_fold(self):
        acct = monitor.account(uid=1000, display_name="vince", observed_at=AT,
                               cpu_used=3.2, cpu_quota=8.0, cpu_warn=6.0,
                               ram_used_gib=12.0, ram_quota_gib=32.0,
                               ram_warn_gib=28.0)
        card = monitor.render_cardkit(model_with(dev(accounts=[acct])))
        body = json.dumps(card, ensure_ascii=False)
        self.assertIn("账户用量", body)
        text = " ".join(md_all(card))
        self.assertIn("vince", text)
        self.assertIn("3.2/8.0", text)
        self.assertIn("警戒线", text)

    def test_the_soft_line_caveat_is_on_the_card(self):
        acct = monitor.account(uid=1000, display_name="vince", observed_at=AT,
                               cpu_used=3.2, cpu_quota=8.0, ram_used_gib=12.0,
                               ram_quota_gib=32.0)
        text = " ".join(md_all(monitor.render_cardkit(model_with(dev(accounts=[acct])))))
        self.assertIn("软线", text)
        self.assertIn("cgroup", text)
        self.assertIn("非 cgroup 硬限制", text)
        self.assertNotIn("limit", text)

    def test_missing_allowance_renders_undefined_not_zero(self):
        acct = monitor.account(uid=1000, display_name="vince", observed_at=AT,
                               cpu_used=3.2, ram_used_gib=12.0)
        text = " ".join(md_all(monitor.render_cardkit(model_with(dev(accounts=[acct])))))
        self.assertIn("未定义", text)
        self.assertNotIn("3.2/0", text)

    def test_an_account_over_its_line_is_pinned_to_the_top(self):
        # The whole point of the block: a runaway account reaches the first
        # screen without anyone opening a fold.
        acct = monitor.account(uid=1000, display_name="vince", observed_at=AT,
                               cpu_used=9.0, cpu_warn=6.0, ram_used_gib=30.0,
                               ram_warn_gib=28.0, severity="CRITICAL")
        model = model_with(dev(accounts=[acct]))
        self.assertEqual(len(model["attention"]), 1)
        self.assertEqual(model["attention"][0]["scope"], "account")
        self.assertIn("vince", model["attention"][0]["text"])
        self.assertEqual(monitor._fleet_verdict(model), "CRITICAL")

    def test_ranking_orders_severe_first_then_largest(self):
        quiet = monitor.account(uid=1001, display_name="quiet", observed_at=AT,
                                ram_used_gib=1.0)
        loud = monitor.account(uid=1002, display_name="loud", observed_at=AT,
                               ram_used_gib=2.0, severity="ATTENTION")
        big = monitor.account(uid=1003, display_name="big", observed_at=AT,
                              ram_used_gib=40.0)
        ordered = sorted([quiet, big, loud], key=monitor._account_order)
        self.assertEqual([a["display_name"] for a in ordered],
                         ["loud", "big", "quiet"])

    def test_unknown_account_states_its_reason(self):
        acct = monitor.account_unobserved(uid=1000, reason="权限被拒绝（hidepid）")
        text = " ".join(md_all(monitor.render_cardkit(model_with(dev(accounts=[acct])))))
        self.assertIn("无法确认", text)
        self.assertIn("hidepid", text)


class AccountPrivacyTest(unittest.TestCase):
    """The boundary: an account block measures an account, it does not read it."""

    def _refuses(self, acct):
        model = monitor.monitor_model(devices=[dev(accounts=[acct])], now=NOW)
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate_model(model)

    def test_a_command_line_is_refused(self):
        acct = dict(monitor.account(uid=1000, observed_at=AT, ram_used_gib=1.0))
        acct["argv"] = ["python", "-m", "pytest"]
        self._refuses(acct)

    def test_an_environment_or_cwd_is_refused(self):
        for field in ("env", "environ", "cwd", "prompt", "token", "open_files"):
            acct = dict(monitor.account(uid=1000, observed_at=AT, ram_used_gib=1.0))
            acct[field] = "anything"
            self._refuses(acct)

    def test_uid_must_be_a_kernel_uid_not_a_name(self):
        acct = dict(monitor.account(uid=1000, observed_at=AT, ram_used_gib=1.0))
        acct["uid"] = "vince"
        self._refuses(acct)

    def test_known_account_without_provenance_is_refused(self):
        # Injected AFTER the model step, the way `test_fleet_monitor_card.py`
        # does for readings: `monitor_model` would downgrade this to UNKNOWN, and
        # what is under test is the validator's own rule, not the downgrade.
        acct = dict(monitor.account(uid=1000, observed_at=AT, ram_used_gib=1.0))
        acct["observed_at"] = None
        model = monitor.monitor_model(devices=[dev()], now=NOW)
        model["devices"][0]["accounts"] = [acct]
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate_model(model)

    def test_the_card_carries_no_private_material_from_accounts(self):
        acct = monitor.account(uid=1000, display_name="vince", observed_at=AT,
                               cpu_used=3.2, cpu_quota=8.0, ram_used_gib=12.0,
                               ram_quota_gib=32.0)
        blob = json.dumps(monitor.render_cardkit(model_with(dev(accounts=[acct]))),
                          ensure_ascii=False).lower()
        # Credential and home-path markers, not the bare word "token" — which is
        # a legitimate `standard_icon` field (see the sibling test in
        # test_fleet_monitor_card.py for the same distinction).
        for token in ("bearer ", "secret", "password", "authorization:",
                      "-----begin", ".secrets", "/home/vince", "cookie",
                      "private key", "argv", "cmdline", "environ", "cwd"):
            self.assertNotIn(token, blob)


class TrustedContractTest(unittest.TestCase):
    """The CardKit 2.0 rules read from the SDK's own component reference."""

    def _card(self):
        acct = monitor.account(uid=1000, display_name="vince", observed_at=AT,
                               cpu_used=3.2, cpu_quota=8.0, ram_used_gib=12.0,
                               ram_quota_gib=32.0, trend=[1, 2, 3])
        readings = [monitor.reading("CPU", 30, unit="%", observed_at=NOW,
                                    trend=[1, 2, 3])]
        return monitor.render_cardkit(model_with(dev(accounts=[acct],
                                                     readings=readings)))

    def test_config_declares_update_multi_true(self):
        # «共享卡片，v2 仅支持 true» — this is what makes the in-place patch legal.
        card = self._card()
        self.assertIs(card["config"]["update_multi"], True)
        self.assertIn("width_mode", card["config"])
        self.assertEqual(card["schema"], "2.0")

    def test_every_element_id_is_letter_first_and_at_most_20_chars(self):
        for eid in monitor._walk_element_ids(self._card(), []):
            self.assertIsInstance(eid, str)
            self.assertRegex(eid, r"^[A-Za-z][A-Za-z0-9_-]*$", eid)
            self.assertLessEqual(len(eid), monitor.ELEMENT_ID_MAX, eid)

    def test_validate_refuses_a_too_long_element_id(self):
        card = self._card()
        card["body"]["elements"][0]["element_id"] = "x" * 21
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate(card)

    def test_validate_refuses_a_digit_first_element_id(self):
        card = self._card()
        card["body"]["elements"][0]["element_id"] = "1bad"
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate(card)

    def test_chart_data_lives_inside_chart_spec_and_stays_gated(self):
        # The documented shape puts the series in chart_spec.data.values. The
        # gate is still the caller's — a documented schema is not a real-device
        # verification, so the default card carries no chart at all.
        model = monitor.monitor_model(
            devices=[dev(readings=[monitor.reading("CPU", 3, unit="%",
                                                   observed_at=NOW,
                                                   trend=[1, 2, 3])])], now=NOW)
        plain = monitor.render_cardkit(model)
        self.assertNotIn("chart", monitor._walk_tags(plain, []))
        verified = monitor.render_cardkit(model, rich=True, charts_verified=True)
        charts = [e for e in _walk_tagged(verified, "chart")]
        self.assertTrue(charts)
        spec = charts[0]["chart_spec"]
        self.assertIn("values", spec["data"])
        self.assertNotIn("data", {k: v for k, v in charts[0].items()
                                  if k != "chart_spec"})
        self.assertEqual(spec["xField"], "x")
        self.assertEqual(spec["yField"], "y")


def _walk_tagged(node, tag, out=None):
    out = [] if out is None else out
    if isinstance(node, dict):
        if node.get("tag") == tag:
            out.append(node)
        for v in node.values():
            _walk_tagged(v, tag, out)
    elif isinstance(node, list):
        for v in node:
            _walk_tagged(v, tag, out)
    return out


class UidSummaryBridgeTest(unittest.TestCase):
    """`accept_uid_summary` — the typed seam for #167's producer (not merged)."""

    def _doc(self, **over):
        doc = {
            "schema": source.UID_SUMMARY_SCHEMA,
            "uid": 1000,
            "display_name": "vince",
            "process_count": 12,
            "anonymous_bytes": 12884901888,          # 12 GiB
            "rss_estimate_bytes": 16106127360,       # 15 GiB (the fallback)
            "host_mem_total_bytes": 274877906944,    # 256 GiB
            "host_percent": 4.7,
            "cpu_cores": 3.2,
            "quota": {"cpu_cores": 8.0, "ram_bytes": 34359738368},   # 32 GiB
            "warn": {"cpu_cores": 6.0, "ram_bytes": 30064771072},    # 28 GiB
            "severity": "OK",
            "trend": [10.1, 10.8, 11.5, 11.9, 12.0],
            "sampled_at": AT,
            "provenance": {"live": False, "collector": "uid-summary-fixture"},
        }
        doc.update(over)
        return doc

    def test_it_maps_a_summary_to_an_account(self):
        acct = source.accept_uid_summary(self._doc(), now=NOW,
                                         freshness_bound=1800)
        self.assertEqual(acct["uid"], 1000)
        self.assertEqual(acct["kind"], monitor.ACCOUNT)
        # anonymous_bytes is preferred over the rss estimate, and converted.
        self.assertAlmostEqual(acct["ram"]["used"], 12.0, places=2)
        self.assertAlmostEqual(acct["ram"]["quota"], 32.0, places=2)
        self.assertAlmostEqual(acct["ram"]["warn"], 28.0, places=2)
        self.assertEqual(acct["cpu"]["used"], 3.2)

    def test_it_falls_back_to_the_rss_estimate(self):
        acct = source.accept_uid_summary(
            self._doc(anonymous_bytes=None), now=NOW, freshness_bound=1800)
        self.assertAlmostEqual(acct["ram"]["used"], 15.0, places=2)

    def test_a_stale_summary_becomes_stale_not_current(self):
        acct = source.accept_uid_summary(
            self._doc(sampled_at="2026-10-13T04:00:00Z"), now=NOW,
            freshness_bound=1800)
        self.assertEqual(acct["state"], monitor.STATE_STALE)
        self.assertIn("超过", acct["reason"])
        # The last numbers survive a stale reading rather than vanishing.
        self.assertAlmostEqual(acct["ram"]["used"], 12.0, places=2)

    def test_no_reading_at_all_fails_closed(self):
        acct = source.accept_uid_summary(
            self._doc(anonymous_bytes=None, rss_estimate_bytes=None, cpu_cores=None),
            now=NOW, freshness_bound=1800)
        self.assertEqual(acct["state"], monitor.STATE_UNKNOWN)
        self.assertIsNone(acct["ram"]["used"])
        self.assertIn("没有被测量", acct["reason"])

    def test_an_unknown_schema_is_refused(self):
        with self.assertRaises(source.UidSummaryRefused):
            source.accept_uid_summary(self._doc(schema="something-else"),
                                      now=NOW, freshness_bound=1800)

    def test_provenance_live_must_be_a_boolean(self):
        with self.assertRaises(source.UidSummaryRefused):
            source.accept_uid_summary(self._doc(provenance={"live": "yes"}),
                                      now=NOW, freshness_bound=1800)

    def test_uid_must_be_an_integer(self):
        with self.assertRaises(source.UidSummaryRefused):
            source.accept_uid_summary(self._doc(uid="vince"), now=NOW,
                                      freshness_bound=1800)

    def test_an_unset_threshold_is_absent_not_invented(self):
        acct = source.accept_uid_summary(self._doc(warn={}, quota={}), now=NOW,
                                         freshness_bound=1800)
        self.assertIsNone(acct["ram"]["warn"])
        self.assertIsNone(acct["ram"]["quota"])
        self.assertIsNone(acct["cpu"]["warn"])

    def test_the_bridged_account_passes_the_card_validator(self):
        acct = source.accept_uid_summary(self._doc(), now=NOW, freshness_bound=1800)
        model = monitor.monitor_model(
            devices=[dev(accounts=[acct])], now=NOW, sample=True)
        monitor.validate_model(model)  # must not raise
        text = " ".join(md_all(monitor.render_cardkit(model)))
        self.assertIn("vince", text)


if __name__ == "__main__":
    unittest.main(verbosity=2)
