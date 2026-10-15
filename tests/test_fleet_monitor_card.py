#!/usr/bin/env python3
"""Deterministic tests for the Fleet monitor card view model and renderer.

The sentences these tests keep false, one per rule the module writes down:

    "eleven services are fine, so the card is green"       — even when one was
                                                              never measured
    "the reading did not arrive, so it is zero"            — rule 1
    "the flow is 23 Mbps"  (about an unsplit total)         — the 公网 claim
    "this sample card is the fleet"                         — rule 5

Most of what follows builds its own tiny input rather than the fixture, so that
a failure names the rule it broke and not the fixture it happened to use. The
fixture is exercised at the end, for composition.
"""
from __future__ import annotations

import ast
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
CARD_MODULE = ROOT / "runtime" / "fleet_monitor_card.py"

NOW = "2026-10-13T06:00:00Z"


def _markdown(node, out=None):
    """Every markdown string anywhere in a card, folds included.

    The trend line lives INSIDE a device's fold, so a test that read only the
    top-level elements would pass while the trend was missing — which is the
    failure mode this helper exists to prevent.
    """
    out = [] if out is None else out
    if isinstance(node, dict):
        if node.get("tag") == "markdown" and isinstance(node.get("content"), str):
            out.append(node["content"])
        for value in node.values():
            _markdown(value, out)
    elif isinstance(node, list):
        for value in node:
            _markdown(value, out)
    return out


def dev(ref="device:x", *, reach="KNOWN", readings=(), name="设备 X",
        observed_at="2026-10-13T05:58:00Z", reason=None):
    reachability = ({"state": reach, "observed_at": observed_at}
                    if reach == "KNOWN" else {"state": reach, "reason": reason})
    return {"device_ref": ref, "display_name": name, "platform": "linux",
            "reachability": reachability, "readings": list(readings)}


class RuleTest(unittest.TestCase):
    """Each rule the module declares, checked where it is applied."""

    def test_new_service_enters_the_count_automatically(self):
        base = [{"name": "a", "state": "OK", "observed_at": NOW},
                {"name": "b", "state": "OK", "observed_at": NOW}]
        before = monitor.monitor_model(devices=[], services=base, now=NOW)
        after = monitor.monitor_model(
            devices=[],
            services=base + [{"name": "c", "state": "OK", "observed_at": NOW}],
            now=NOW)
        self.assertEqual(before["summary"]["services"]["total"], 2)
        self.assertEqual(after["summary"]["services"]["total"], 3)
        self.assertEqual(after["summary"]["services"]["ok"], 3)

    def test_service_counts_are_derived_never_authored(self):
        services = [{"name": "a", "state": "OK", "observed_at": NOW},
                    {"name": "b", "state": "CRITICAL", "reason": "挂了",
                     "observed_at": NOW},
                    {"name": "c", "state": "UNKNOWN", "reason": "未取到"}]
        counts = monitor.monitor_model(
            devices=[], services=services, now=NOW)["summary"]["services"]
        self.assertEqual(counts, {"total": 3, "ok": 1, "attention": 0,
                                  "critical": 1, "stale": 0, "unknown": 1})

    def test_anomaly_is_pinned_to_the_top(self):
        readings = [monitor.reading("磁盘 C:", 92, unit="%", severity="CRITICAL",
                                    observed_at=NOW),
                    monitor.reading("CPU", 12, unit="%", observed_at=NOW)]
        model = monitor.monitor_model(devices=[dev(readings=readings)], now=NOW)
        self.assertEqual(len(model["attention"]), 1)
        self.assertIn("磁盘 C:", model["attention"][0]["text"])
        # And it is on the FIRST screen: the attention block precedes any fold.
        card = monitor.render_cardkit(model)
        tags = [e.get("tag") for e in card["body"]["elements"]]
        self.assertIn("markdown", tags)  # the summary is first, then attention
        self.assertTrue(any("需要关注" in e.get("content", "")
                            for e in card["body"]["elements"]
                            if e.get("tag") == "markdown"))

    def test_lost_device_is_reported_and_not_healthy(self):
        model = monitor.monitor_model(
            devices=[dev(reach="UNKNOWN", reason="没有传输层报告")], now=NOW)
        self.assertEqual(model["summary"]["devices"]["unreached"], 1)
        self.assertEqual(model["attention"][0]["severity"], "CRITICAL")
        self.assertIn("失联", model["attention"][0]["text"])
        self.assertEqual(monitor._fleet_verdict(model), "CRITICAL")

    def test_unknown_reading_never_renders_as_zero_or_healthy(self):
        r = monitor.unobserved("GPU 温度", reason="nvidia-smi 未安装")
        line = monitor.reading_line(r)
        self.assertIn("无法确认", line)
        self.assertNotIn("0", line.replace("无法确认", ""))
        # And an UNKNOWN service does not let the fleet read OK.
        model = monitor.monitor_model(
            devices=[], services=[{"name": "s", "state": "UNKNOWN",
                                   "reason": "未取到"}], now=NOW)
        self.assertEqual(monitor._fleet_verdict(model), "UNCONFIRMED")

    def test_stale_reading_says_expired_and_keeps_its_last_value(self):
        r = monitor.reading("磁盘忙碌度", 6, unit="%",
                            observed_at="2026-10-13T05:00:00Z")
        model = monitor.monitor_model(devices=[dev(readings=[r])], now=NOW,
                                      stale_after_s=1800)
        got = model["devices"][0]["readings"][0]
        self.assertEqual(got["state"], "STALE")
        line = monitor.reading_line(got)
        self.assertIn("已过期", line)
        self.assertIn("6", line)  # the last value is kept, not hidden or zeroed

    def test_known_reading_without_observed_at_is_downgraded(self):
        r = monitor.reading("CPU", 12, unit="%")  # no observed_at
        model = monitor.monitor_model(devices=[dev(readings=[r])], now=NOW)
        got = model["devices"][0]["readings"][0]
        self.assertEqual(got["state"], "UNKNOWN")
        self.assertIn("观测时间", got["reason"])

    def test_model_is_idempotent_and_render_is_deterministic(self):
        readings = [monitor.reading("CPU", 3, unit="%", observed_at=NOW)]
        kwargs = dict(devices=[dev(readings=readings)],
                      services=[{"name": "a", "state": "OK"}], now=NOW)
        a = json.dumps(monitor.monitor_model(**kwargs), sort_keys=True)
        b = json.dumps(monitor.monitor_model(**kwargs), sort_keys=True)
        self.assertEqual(a, b)
        card_a = json.dumps(monitor.render_cardkit(monitor.monitor_model(**kwargs)),
                            sort_keys=True)
        card_b = json.dumps(monitor.render_cardkit(monitor.monitor_model(**kwargs)),
                            sort_keys=True)
        self.assertEqual(card_a, card_b)

    def test_lifecycle_declares_a_single_in_place_card(self):
        model = monitor.monitor_model(devices=[], now=NOW)
        lc = model["lifecycle"]
        self.assertTrue(lc["single_card"])
        self.assertEqual(lc["mode"], "in_place")
        self.assertEqual(lc["normal_refresh_seconds"], 600)
        self.assertEqual(lc["urgent_refresh"], "on_critical_immediately")
        self.assertEqual(lc["physical_rotation"], "on_api_limit")
        self.assertEqual(lc["card_key"], monitor.CARD_KEY)

    def test_sample_is_marked_and_live_is_not(self):
        live = monitor.monitor_model(devices=[], services=[], now=NOW, sample=False)
        sample = monitor.monitor_model(devices=[], services=[], now=NOW, sample=True)
        live_card = monitor.render_cardkit(live)
        sample_card = monitor.render_cardkit(sample)
        self.assertNotIn(monitor.SAMPLE_MARK, live_card["header"]["title"]["content"])
        self.assertIn(monitor.SAMPLE_MARK, sample_card["header"]["title"]["content"])
        # Reversible: the non-live line is on the body too, so a forwarded crop
        # that loses the header still cannot read the sample as live.
        body = " ".join(e.get("content", "") for e in sample_card["body"]["elements"]
                        if e.get("tag") == "markdown")
        self.assertIn("非实时", body)
        live_body = " ".join(e.get("content", "") for e in live_card["body"]["elements"]
                             if e.get("tag") == "markdown")
        self.assertNotIn("非实时", live_body)

    def test_network_undifferentiated_is_never_called_public(self):
        public = monitor.network_reading(4.1, scope="public", observed_at=NOW,
                                         cap_bps=100_000_000, cap_confirmed=True)
        total = monitor.network_reading(23.5, scope="undifferentiated",
                                        observed_at=NOW)
        self.assertEqual(public["label"], "公网出向")
        self.assertIn("100 Mbps", public["note"])
        self.assertNotIn("公网", total["label"])
        self.assertIn("未区分", total["label"])
        with self.assertRaises(monitor.MonitorCardError):
            monitor.network_reading(1, scope="totally-fine", observed_at=NOW)


class StructureTest(unittest.TestCase):
    """The structural rules `validate` enforces, and the gate on `chart`."""

    def _model(self):
        readings = [monitor.reading("CPU", 30, unit="%", observed_at=NOW,
                                    trend=[1, 2, 3])]
        return monitor.monitor_model(
            devices=[dev(readings=readings)],
            services=[{"name": "a", "state": "OK", "observed_at": NOW}],
            now=NOW)

    def test_element_ids_are_unique_and_schema_is_2_0(self):
        card = monitor.render_cardkit(self._model())
        self.assertEqual(card["schema"], "2.0")
        ids = monitor._walk_element_ids(card, [])
        self.assertTrue(ids)
        self.assertEqual(len(ids), len(set(ids)))

    def test_card_fits_the_byte_budget(self):
        card = monitor.render_cardkit(self._model())
        size = len(json.dumps(card, ensure_ascii=False).encode("utf-8"))
        self.assertLessEqual(size, monitor.MAX_CARD_BYTES)

    def test_validate_refuses_unknown_tag(self):
        card = monitor.render_cardkit(self._model())
        card["body"]["elements"].append({"tag": "made_up_widget"})
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate(card)

    def test_validate_refuses_duplicate_element_id(self):
        card = monitor.render_cardkit(self._model())
        existing = monitor._walk_element_ids(card, [])[0]
        card["body"]["elements"].append(
            {"tag": "collapsible_panel", "element_id": existing,
             "elements": [monitor._md("dup")]})
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate(card)

    def test_chart_is_gated_behind_verified_flag(self):
        model = self._model()
        # Neither gate open: the safe variant, and no chart.
        plain = json.dumps(monitor.render_cardkit(model))
        self.assertNotIn('"tag": "chart"', plain)
        self.assertNotIn('"tag": "column_set"', plain)
        # `charts_verified` alone is not enough — `rich` must be open too.
        self.assertNotIn("chart", monitor._walk_tags(
            monitor.render_cardkit(model, charts_verified=True), []))
        verified = monitor.render_cardkit(model, rich=True, charts_verified=True)
        self.assertIn("chart", monitor._walk_tags(verified, []))
        # And the trend is still visible without the chart: as a text line,
        # inside the device's fold.
        text = " ".join(_markdown(monitor.render_cardkit(model)))
        self.assertIn("趋势", text)

    def test_forbidden_topics_are_refused(self):
        readings = [monitor.reading("WiFi 型号", "Intel AX201", observed_at=NOW)]
        model = monitor.monitor_model(devices=[dev(readings=readings)], now=NOW)
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate_model(model)

    def test_validate_model_refuses_known_without_provenance(self):
        model = monitor.monitor_model(devices=[], now=NOW)
        model["devices"] = [dev(readings=[monitor.reading("CPU", 1)])]
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate_model(model)


class PurityTest(unittest.TestCase):
    """The seam that makes the model testable off the fleet."""

    def test_pure_module_imports_no_io(self):
        """Parse the imports rather than substring-matching the source.

        A substring check flags the docstring — which NAMES the adapter it is
        deliberately not, and names `card_source` as the module it does not
        import. What the rule is about is the import graph, so parse it.
        """
        tree = ast.parse(CARD_MODULE.read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        forbidden = {"subprocess", "os", "socket", "shutil", "requests", "urllib",
                     "http", "tempfile", "glob", "ssl", "pathlib",
                     "card_source", "fleet_monitor_source"}
        self.assertEqual(imported & forbidden, set(),
                         "the pure module reached for I/O")
        self.assertNotIn("open(", CARD_MODULE.read_text(encoding="utf-8"))


class AdapterTest(unittest.TestCase):
    """The read-only producer adapter composes snapshots; it measures nothing."""

    SNAPSHOT = {
        "at": "2026-10-13T05:58:00Z", "node_label": "东京共享开发机",
        "host": {"state": "OK",
                 "metrics": [{"label": "CPU", "value": "16 逻辑核", "note": ""},
                             {"label": "内存", "value": "58%", "note": "整机"}],
                 "lines": [], "why": "", "detail": {}},
        "gaps": [],
    }

    def test_adapter_composes_a_snapshot(self):
        device = source.device_from_snapshot("device:tokyo-shared-dev-01",
                                             self.SNAPSHOT)
        self.assertEqual(device["reachability"]["state"], "KNOWN")
        labels = [r["label"] for r in device["readings"]]
        self.assertIn("CPU", labels)
        self.assertTrue(all(r["observed_at"] == self.SNAPSHOT["at"]
                            for r in device["readings"]))

    def test_adapter_turns_a_gap_into_an_unknown_reading(self):
        snap = dict(self.SNAPSHOT, gaps=[{"read": "summary",
                                          "command": "serverosctl resource summary",
                                          "why": "命令超时"}])
        device = source.device_from_snapshot("device:x", snap)
        gap = [r for r in device["readings"] if r["state"] == "UNKNOWN"]
        self.assertEqual(len(gap), 1)
        self.assertIn("命令超时", gap[0]["reason"])

    def test_adapter_carries_a_missing_snapshot_as_unreachable(self):
        model = source.fleet_model(
            snapshots=[("device:work-fj", None)], services=[], now=NOW,
            inventory=[{"device_ref": "device:work-fj", "display_name": "FJPCOS",
                        "platform": "windows"}])
        self.assertEqual(len(model["devices"]), 1)
        self.assertEqual(model["devices"][0]["reachability"]["state"], "UNKNOWN")
        self.assertEqual(model["summary"]["devices"]["unreached"], 1)

    def test_the_default_inventory_makes_every_declared_device_a_row(self):
        # Coverage is a property of the declaration: with no snapshots at all,
        # every expected device is still a row, unreached and truthful.
        model = source.fleet_model(snapshots=[], services=[], now=NOW)
        self.assertEqual(len(model["devices"]), len(source.EXPECTED_DEVICES))
        self.assertEqual(sorted(d["device_ref"] for d in model["devices"]),
                         sorted(e["device_ref"] for e in source.EXPECTED_DEVICES))
        self.assertTrue(all(d["reachability"]["state"] == "UNKNOWN"
                            for d in model["devices"]))
        self.assertEqual(model["summary"]["devices"]["unreached"],
                         len(source.EXPECTED_DEVICES))

    def test_fixture_builds_a_valid_sample_card(self):
        model = source.model_from_fixture()
        self.assertTrue(model["sample"])
        card = monitor.render_cardkit(model)
        self.assertEqual(card["schema"], "2.0")
        self.assertIn(monitor.SAMPLE_MARK, card["header"]["title"]["content"])
        # Windows' full C: drive is an anomaly and is pinned near the top.
        self.assertTrue(any("磁盘 C:" in a["text"] for a in model["attention"]))
        # The unsplit Windows total is present and not called 公网.
        win = next(d for d in model["devices"]
                   if d["device_ref"] == "device:work-fj")
        net = next(r for r in win["readings"] if r["kind"] == "network")
        self.assertNotIn("公网", net["label"])

    def test_fixture_covers_the_whole_declared_inventory(self):
        # COVERAGE IS NOT OMISSION. Every declared device has a row; the laptop
        # with no provider is present and UNKNOWN rather than dropped. WHICH rows
        # a given recipient sees is the audience gate's job, not the fixture's.
        model = source.model_from_fixture()
        refs = sorted(d["device_ref"] for d in model["devices"])
        self.assertEqual(refs, ["device:llm-lab-dell", "device:personal-dell",
                                "device:tokyo-shared-dev-01", "device:work-fj"])
        personal = next(d for d in model["devices"]
                        if d["device_ref"] == "device:personal-dell")
        self.assertEqual(personal["reachability"]["state"], "UNKNOWN")
        self.assertEqual(personal["readings"], [],
                         "a device with no provider must carry no invented metrics")


class Phase2Test(unittest.TestCase):
    """Phase 2: the safe variant, four axes, evidence-gated network/share, the
    mobile-width budget, and privacy."""

    def test_default_card_is_the_safe_variant(self):
        model = source.model_from_fixture()
        tags = set(monitor._walk_tags(monitor.render_cardkit(model), []))
        self.assertTrue(tags <= {"markdown", "hr", "collapsible_panel",
                                 "plain_text", "standard_icon"},
                        f"unexpected tag(s): {tags}")
        self.assertNotIn("column_set", tags)
        self.assertNotIn("chart", tags)

    def test_rich_variant_restores_column_set(self):
        model = source.model_from_fixture()
        tags = set(monitor._walk_tags(
            monitor.render_cardkit(model, rich=True), []))
        self.assertIn("column_set", tags)

    def test_service_axes_match_health_vocabulary(self):
        # Imported only HERE, in a test: the pure module must not import the
        # module that loads the registry and its contracts.
        health = _load("serveros_runtime_health", ROOT / "runtime" / "health.py")
        self.assertEqual(tuple(monitor.SERVICE_AXES), tuple(health.AXES))

    def test_stale_service_is_not_counted_healthy(self):
        services = [{"name": "s", "state": "OK",
                     "observed_at": "2026-10-13T03:00:00Z"}]
        model = monitor.monitor_model(devices=[], services=services, now=NOW,
                                      stale_after_s=1800)
        self.assertEqual(model["services"][0]["state"], "STALE")
        self.assertEqual(model["summary"]["services"]["ok"], 0)
        self.assertEqual(model["summary"]["services"]["stale"], 1)
        self.assertEqual(monitor._fleet_verdict(model), "UNCONFIRMED")

    def test_unconfirmed_quota_is_absent_not_invented(self):
        self.assertIsNone(monitor.quota_reading(100_000_000, observed_at=NOW,
                                                confirmed=False))
        confirmed = monitor.quota_reading(100_000_000, observed_at=NOW,
                                          confirmed=True)
        self.assertEqual(confirmed["value"], 100)
        model = source.model_from_fixture()

        def quotas(ref):
            device = next(d for d in model["devices"] if d["device_ref"] == ref)
            return [r for r in device["readings"] if r["label"] == "公网出口配额"]

        self.assertEqual(len(quotas("device:tokyo-shared-dev-01")), 1)
        self.assertEqual(quotas("device:work-fj"), [])
        self.assertEqual(quotas("device:llm-lab-dell"), [])

    def test_host_share_needs_cgroup_attribution(self):
        self.assertIsNone(monitor.share_reading("3 核", attribution="guess",
                                                observed_at=NOW))
        kept = monitor.share_reading("3 核", attribution="cgroup",
                                     observed_at=NOW)
        self.assertEqual(kept["scope"], "cgroup")
        model = monitor.monitor_model(devices=[], now=NOW)
        model["devices"] = [dev(readings=[
            {"kind": "host-share", "label": "份额", "value": "3 核",
             "state": "KNOWN", "observed_at": NOW, "scope": "guess"}])]
        with self.assertRaises(monitor.MonitorCardError):
            monitor.validate_model(model)

    def test_every_markdown_line_fits_the_mobile_budget(self):
        card = monitor.render_cardkit(source.model_from_fixture(), rich=True)
        for content in _markdown(card):
            for line in content.split("\n"):
                self.assertLessEqual(len(line), monitor.MOBILE_LINE_CHARS,
                                     f"line too wide for 390px: {line!r}")

    def test_card_carries_no_private_material(self):
        card = monitor.render_cardkit(source.model_from_fixture(), rich=True,
                                      charts_verified=True)
        blob = json.dumps(card, ensure_ascii=False).lower()
        # Credential and home-path markers, not the bare word "token" — which is
        # a legitimate `standard_icon` field and appears in ordinary prose.
        for token in ("bearer ", "secret", "password", "authorization:",
                      "-----begin", ".secrets", "/home/vince", "cookie",
                      "private key", "om_", "oc_"):
            self.assertNotIn(token, blob, f"card leaked {token!r}")

    def test_is_urgent_tracks_critical(self):
        self.assertTrue(monitor.is_urgent(source.model_from_fixture()))
        self.assertFalse(monitor.is_urgent(
            monitor.monitor_model(devices=[], services=[], now=NOW)))


class AcceptanceSeamTest(unittest.TestCase):
    """The typed seam the C++ `NodeResourceSnapshot v1` is assembled through."""

    def _doc(self, **over):
        doc = {"schema": source.DEVICE_ENTRY_SCHEMA,
               "device_ref": "device:tokyo-shared-dev-01",
               "display_name": "东京共享开发机", "platform": "linux",
               "reachability": {"state": "KNOWN", "observed_at": NOW},
               "readings": [{"kind": "reading", "label": "CPU", "value": 32,
                             "unit": "%", "state": "KNOWN",
                             "observed_at": NOW}]}
        doc.update(over)
        return doc

    def test_accepts_a_well_formed_snapshot(self):
        entry = source.accept_device_snapshot(self._doc())
        self.assertEqual(entry["device_ref"], "device:tokyo-shared-dev-01")
        model = monitor.monitor_model(devices=[entry], services=[], now=NOW)
        self.assertEqual(model["devices"][0]["readings"][0]["label"], "CPU")

    def test_refuses_an_unknown_schema(self):
        with self.assertRaises(source.SnapshotRefused):
            source.accept_device_snapshot(self._doc(schema="something-else"))

    def test_refuses_known_without_provenance(self):
        doc = self._doc(readings=[{"kind": "reading", "label": "CPU",
                                   "value": 1, "state": "KNOWN"}])
        with self.assertRaises(source.SnapshotRefused):
            source.accept_device_snapshot(doc)

    def test_refuses_missing_identity(self):
        doc = self._doc()
        del doc["device_ref"]
        with self.assertRaises(source.SnapshotRefused):
            source.accept_device_snapshot(doc)

    def test_seam_fixture_is_accepted(self):
        path = ROOT / "runtime" / "fleet-monitor-device.fixture.json"
        doc = json.loads(path.read_text(encoding="utf-8"))
        entry = source.accept_device_snapshot(doc)
        self.assertEqual(entry["device_ref"], doc["device_ref"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
