#!/usr/bin/env python3
"""The Core reading reaches the card — one device, on the same pipeline.

WHAT THIS FILE KEEPS FALSE
--------------------------
    "the route reached it" == "a document came back"   — route.state is checked,
                                                          not the health probe
    "not measured" == 0                                — UNKNOWN is immutable
    "the peer is missing, so it is not on the card"    — coverage does not drop
                                                          a declared device
    "the numbers are fresh"                            — freshness is read from
                                                          the source's own stamp
    "any dict with these fields is this device's"      — wrong device refuses
    "a low GPU means the service is up"                — utilisation is a
                                                          counter, not health

The summary shape is the one `fleet_mcp.core_lite_source.admit_core_lite`
returns; the readings become the ones `fleet_monitor_card` renders. NOTHING here
sends anything, opens a socket or reaches a real Core.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
RUNTIME = ROOT / "runtime"

NOW = "2026-10-16T06:00:00Z"
TOKYO = "device:tokyo-shared-dev-01"
A6000 = "device:llm-lab-dell"
PERSONAL = "device:personal-dell"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bridge = _load("serveros_fleet_core_bridge", RUNTIME / "fleet_core_bridge.py")
monitor = _load("serveros_fleet_monitor_card", RUNTIME / "fleet_monitor_card.py")
source = _load("serveros_fleet_monitor_source", RUNTIME / "fleet_monitor_source.py")


def stamp(minutes=0):
    base = datetime(2026, 10, 16, 6, 0, 0, tzinfo=timezone.utc)
    return (base + timedelta(minutes=minutes)).isoformat().replace("+00:00", "Z")


def readback(**over):
    """A well-formed admitted readback for one device."""
    doc = {
        "schema": bridge.CORE_READBACK_SCHEMA,
        "device_ref": TOKYO,
        "source": {"schema": "serveros/core-lite-status.v1", "verified_route": True,
                   "received_at": NOW},
        "device": {"declared": TOKYO, "state": "MATCH"},
        "principal": {"declared": "vince", "expected": "vince", "state": "MATCH",
                      "basis": "binding"},
        "connected": "CONNECTED",
        "live": {"claimed": True, "accepted": True, "why": None},
        "freshness": {"state": "FRESH", "age_seconds": 1.0, "bound_seconds": 60.0},
        "observations": {
            "cpu": {"state": "PRESENT", "value": {"used_percent": 32.5},
                    "completeness": "COMPLETE"},
            "memory": {"state": "PRESENT",
                       "value": {"total_bytes": 134217728000, "available_bytes": 67108864000,
                                 "used_percent": 50.0}, "completeness": "COMPLETE"},
            "swap": {"state": "PRESENT",
                     "value": {"total_bytes": 34359738368, "free_bytes": 17179869184,
                               "used_bytes": 17179869184, "pages_in_per_second": 12.5,
                               "pages_out_per_second": 3.0}, "completeness": "COMPLETE"},
            "psi.cpu": {"state": "PRESENT",
                        "value": {"some": {"avg10": 0.9}, "full": {"avg10": 0.0}},
                        "completeness": "COMPLETE"},
            "psi.io": {"state": "PRESENT",
                       "value": {"some": {"avg10": 0.1}, "full": {"avg10": 0.1}},
                       "completeness": "COMPLETE"},
            "storage.readonly": {"state": "PRESENT", "value": {
                "filesystems": {"roles": [
                    {"role": "ROOT_FS", "total_bytes": 100000000000,
                     "free_bytes": 60000000000, "available_bytes": 59000000000},
                    {"role": "DATA_FS", "total_bytes": 4000000000000,
                     "free_bytes": 3000000000000, "available_bytes": 2950000000000}]},
                "disk_io": {"roles": [
                    {"role": "ROOT_FS", "read_bytes_per_second": 2048.0,
                     "write_bytes_per_second": 4096.0}]}},
                "completeness": "COMPLETE"},
            "services": {"state": "PRESENT", "value": {"services": [
                {"id": "serverosd", "unit": "serverosd.service", "state": "ACTIVE"},
                {"id": "gateway0", "unit": "gateway0.service", "state": "FAILED"}]},
                "completeness": "COMPLETE"},
            "gpu.readonly": {"state": "PRESENT", "value": {
                "gpus": [{"index": 0, "label": "A6000", "gpu_util_percent": 71.0,
                          "memory_used_bytes": 40802189312,
                          "memory_total_bytes": 51539607552,
                          "temperature_c": 68.0}],
                "sample_time": NOW}, "completeness": "COMPLETE"},
        },
        "observations_unavailable": [],
        "integrity": "OK",
        "refusals": [],
        "route": {"state": "VERIFIED", "device_ref": TOKYO, "authenticated": True,
                  "reason": None, "failure": None},
    }
    doc.update(over)
    return doc


def readings_of(entry):
    return {r["label"]: r for r in entry["readings"]}


def _markdown(node, out=None):
    """Every markdown string in a card, folds included."""
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


class VerdictTest(unittest.TestCase):
    def _v(self, doc, **kw):
        return bridge.core_verdict(doc, device_ref=kw.pop("ref", TOKYO), now=NOW,
                                   stale_after_s=kw.pop("stale", 1800), **kw)

    def test_a_verified_fresh_readback_is_known(self):
        state, why, at = self._v(readback())
        self.assertEqual(state, monitor.STATE_KNOWN)
        self.assertIsNone(why)
        self.assertEqual(at, NOW)

    def test_an_unauthenticated_route_is_unknown_with_its_reason(self):
        doc = readback(route={"state": "UNAUTHENTICATED", "device_ref": TOKYO,
                              "authenticated": False,
                              "reason": "the source has no authenticated route to this device"})
        state, why, _ = self._v(doc)
        self.assertEqual(state, monitor.STATE_UNKNOWN)
        self.assertIn("UNAUTHENTICATED", why)
        self.assertIn("no authenticated route", why)

    def test_not_configured_is_a_different_sentence_from_not_connected(self):
        a = readback(route={"state": "NOT_CONFIGURED", "device_ref": TOKYO,
                            "authenticated": False, "reason": "no Core source configured"})
        b = readback(route={"state": "DEVICE_MISMATCH", "device_ref": "device:other",
                            "authenticated": False, "reason": "reaches a different device"})
        self.assertNotEqual(self._v(a)[1], self._v(b)[1])

    def test_a_live_claim_that_was_not_accepted_is_unknown(self):
        state, why, _ = self._v(readback(live={"claimed": True, "accepted": False,
                                               "why": "the document claimed live without a route"}))
        self.assertEqual(state, monitor.STATE_UNKNOWN)
        self.assertIn("live", why)

    def test_an_unknown_principal_withholds_the_numbers(self):
        state, why, _ = self._v(readback(principal={"declared": None, "expected": None,
                                                    "state": "UNKNOWN", "basis": None}))
        self.assertEqual(state, monitor.STATE_UNKNOWN)
        self.assertIn("账户", why)

    def test_a_future_stamp_is_refused(self):
        state, why, _ = self._v(readback(source={"schema": None, "verified_route": True,
                                                 "received_at": stamp(30)}))
        self.assertEqual(state, monitor.STATE_UNKNOWN)
        self.assertIn("未来", why)

    def test_a_stale_reading_is_stale_not_known(self):
        state, why, _ = self._v(readback(
            source={"schema": None, "verified_route": True,
                    "received_at": stamp(-120)}))
        self.assertEqual(state, monitor.STATE_STALE)
        self.assertIn("超过上限", why)

    def test_a_non_fresh_word_is_not_known_even_when_recent(self):
        state, _, _ = self._v(readback(
            freshness={"state": "UNVERIFIED", "age_seconds": 1.0, "bound_seconds": 60.0}))
        self.assertEqual(state, monitor.STATE_UNKNOWN)

    def test_a_wrong_device_refuses_rather_than_filing_it_here(self):
        with self.assertRaises(bridge.CoreRefused):
            self._v(readback(device_ref="device:somewhere-else"))

    def test_a_wrong_schema_refuses(self):
        with self.assertRaises(bridge.CoreRefused):
            self._v(readback(schema="something-else"))

    def test_a_non_object_refuses(self):
        with self.assertRaises(bridge.CoreRefused):
            self._v(["not", "an", "object"])


class ReadingsTest(unittest.TestCase):
    def setUp(self):
        self.entry = bridge.device_from_core_readback(
            readback(), device_ref=TOKYO, display_name="东京共享开发机",
            now=NOW, stale_after_s=1800)

    def test_the_device_entry_is_valid_for_the_model(self):
        model = monitor.monitor_model(devices=[self.entry], services=[], now=NOW)
        monitor.validate_model(model)  # must not raise
        self.assertEqual(model["devices"][0]["device_ref"], TOKYO)

    def test_cpu_and_memory_land_as_readings(self):
        r = readings_of(self.entry)
        self.assertEqual(r["CPU"]["value"], 32.5)
        self.assertEqual(r["内存"]["value"], 50.0)

    def test_swap_stock_and_flow_are_two_readings_not_one(self):
        r = readings_of(self.entry)
        self.assertIn("Swap 存量", r)
        self.assertIn("Swap 换页流量", r)
        self.assertEqual(r["Swap 存量"]["value"], 50.0)
        self.assertIn("12.5", str(r["Swap 换页流量"]["value"]))

    def test_psi_is_carried_for_cpu_and_io(self):
        r = readings_of(self.entry)
        self.assertIn("PSI CPU", r)
        self.assertIn("PSI IO", r)

    def test_storage_roles_become_disk_readings(self):
        r = readings_of(self.entry)
        self.assertIn("磁盘 ROOT_FS", r)
        self.assertIn("磁盘 DATA_FS", r)
        self.assertAlmostEqual(r["磁盘 ROOT_FS"]["value"], 41.0, places=1)

    def test_services_carry_their_state_and_severity(self):
        r = readings_of(self.entry)
        self.assertEqual(r["服务 serverosd"]["value"], "ACTIVE")
        self.assertEqual(r["服务 serverosd"]["severity"], "OK")
        self.assertEqual(r["服务 gateway0"]["severity"], "CRITICAL")

    def test_gpu_counters_are_per_card(self):
        r = readings_of(self.entry)
        self.assertIn("GPU A6000 利用率", r)
        self.assertIn("GPU A6000 VRAM", r)
        self.assertEqual(r["GPU A6000 利用率"]["value"], 71.0)

    def test_an_unavailable_block_is_unobserved_never_zero(self):
        doc = readback()
        doc["observations"]["cpu"] = {"state": "UNAVAILABLE", "reason": "NO_SOURCE"}
        doc["observations"]["swap"] = {"state": "UNAVAILABLE", "reason": "NO_SOURCE"}
        entry = bridge.device_from_core_readback(doc, device_ref=TOKYO, now=NOW)
        r = readings_of(entry)
        self.assertEqual(r["CPU"]["state"], monitor.STATE_UNKNOWN)
        self.assertIn("NO_SOURCE", r["CPU"]["reason"])
        self.assertNotIn(0, [r["CPU"].get("value")])
        # An UNAVAILABLE swap carries ONE unmeasured reading (the block as a
        # whole), not the stock/flow pair a measured one splits into.
        self.assertEqual(r["Swap"]["state"], monitor.STATE_UNKNOWN)

    def test_a_stale_reading_keeps_no_number_as_current(self):
        entry = bridge.device_from_core_readback(
            readback(source={"schema": None, "verified_route": True,
                             "received_at": stamp(-120)}),
            device_ref=TOKYO, now=NOW)
        r = readings_of(entry)
        self.assertEqual(r["CPU"]["state"], monitor.STATE_STALE)
        # The last value is kept for a stale reading, but the LINE says 已过期.
        self.assertIn("已过期", monitor.reading_line(r["CPU"]))

    def test_no_identity_or_command_line_reaches_the_device(self):
        blob = json.dumps(self.entry, ensure_ascii=False).lower()
        for token in ("argv", "cmdline", "environ", "cwd", "token", "secret",
                      "/home/", "ou_"):
            self.assertNotIn(token, blob)

    def test_the_card_renders_without_fake_numbers(self):
        # The readings live INSIDE the device's fold, so the collector walks the
        # card rather than reading the top level — the same reason the card
        # tests in the sibling file do.
        model = monitor.monitor_model(devices=[self.entry], services=[], now=NOW)
        text = " ".join(_markdown(monitor.render_cardkit(model)))
        self.assertIn("CPU", text)
        self.assertIn("32.5%", text)


class PeerModelSourceTest(unittest.TestCase):
    def _source(self, readbacks, *, archive=None):
        return bridge.peer_model_source(
            readbacks, local_device_ref=TOKYO, local_display_name="东京共享开发机",
            local_platform="linux",
            peers=[(A6000, "A6000 推理机"), (PERSONAL, "DellPCOS（个人机）")],
            archive=archive)

    def test_a_peer_with_a_readback_gets_its_readings(self):
        doc = readback(device_ref=A6000)
        doc["device_ref"] = A6000
        doc["route"]["device_ref"] = A6000
        model, rows = self._source({A6000: doc})(at=NOW)
        a6000 = next(d for d in model["devices"] if d["device_ref"] == A6000)
        labels = [r["label"] for r in a6000["readings"]]
        self.assertIn("GPU A6000 利用率", labels)
        self.assertEqual(rows, [])

    def test_a_peer_without_a_readback_stays_on_the_card_as_unknown(self):
        model, _ = self._source({})(at=NOW)
        refs = [d["device_ref"] for d in model["devices"]]
        self.assertIn(PERSONAL, refs, "coverage must not drop a declared device")
        personal = next(d for d in model["devices"] if d["device_ref"] == PERSONAL)
        self.assertEqual(personal["reachability"]["state"], "UNKNOWN")
        self.assertEqual(personal["readings"], [])
        self.assertFalse(personal["observed_ever"])

    def test_a_readback_for_the_wrong_device_is_not_filed_under_this_one(self):
        doc = readback(device_ref="device:somewhere-else")
        doc["route"]["device_ref"] = "device:somewhere-else"
        model, _ = self._source({A6000: doc})(at=NOW)
        a6000 = next(d for d in model["devices"] if d["device_ref"] == A6000)
        self.assertEqual(a6000["readings"], [])
        self.assertEqual(a6000["reachability"]["state"], "UNKNOWN")

    def test_core_samples_reach_the_shared_trend_ledger(self):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            archive = source.TrendArchive(Path(d) / "fleet-trend.json")
            doc = readback(device_ref=A6000)
            doc["route"]["device_ref"] = A6000
            self._source({A6000: doc}, archive=archive)(at=NOW)
            # The SAME store the card and the hourly report read from.
            self.assertIn(32.5, archive.series(A6000, "CPU"))

    def test_the_model_passes_the_card_validator(self):
        model, _ = self._source({})(at=NOW)
        monitor.validate_model(model)  # must not raise


if __name__ == "__main__":
    unittest.main(verbosity=2)
