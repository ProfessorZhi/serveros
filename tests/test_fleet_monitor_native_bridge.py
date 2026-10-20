#!/usr/bin/env python3
"""Cross-PR contract tests: PR#161's native measurement envelope -> this card.

The fixture is derived READ-ONLY from PR#161's own desensitised
`adapter/fixtures/snapshot.v1.example.json`, so these tests exercise the REAL
shape and not one invented here. What they keep true, in the words the bridge
writes down:

    "free and available are the same number"   — they are not: 22 GiB apart
    "a link speed is a public quota"            — virtio reports none; even
                                                  docker0's 10 Gb/s is not the
                                                  internet
    "the collector's cgroup is vince's share"   — it is one process's cgroup
    "no reading came back, so it is zero"       — it is UNKNOWN, with a reason

Nothing here runs a collector, opens a socket, or reaches Feishu.
"""
from __future__ import annotations

import copy
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

FIXTURE = ROOT / "runtime" / "native-node-measurement.fixture.json"
ENVELOPE = json.loads(FIXTURE.read_text(encoding="utf-8"))
# The example's observed_at is 2026-01-01T00:00:03.3Z; this reader looked ~7s later.
NOW = "2026-01-01T00:00:10+00:00"
BOUND = 300
REF = "device:tokyo-shared-dev-01"


def accept(envelope=ENVELOPE, *, now=NOW, bound=BOUND, ref=REF):
    return source.accept_native_measurement(envelope, device_ref=ref, now=now,
                                            freshness_bound=bound)


def readings_of(entry, *, kind=None):
    return [r for r in entry["readings"] if kind is None or r.get("kind") == kind]


class BridgeTest(unittest.TestCase):

    def test_fixture_runs_through_to_a_valid_card(self):
        entry = accept()
        model = monitor.monitor_model(devices=[entry], services=[], now=NOW,
                                      stale_after_s=BOUND)
        card = monitor.render_cardkit(model)  # validates internally
        self.assertEqual(card["schema"], "2.0")
        # The envelope says live=false, so the card must say sample.
        self.assertIn(monitor.SAMPLE_MARK, card["header"]["title"]["content"])

    def test_cpu_and_memory_are_carried(self):
        entry = accept()
        cpu = [r for r in entry["readings"] if r["label"] == "CPU"][0]
        self.assertEqual(cpu["value"], 11.7)
        memory = [r for r in entry["readings"] if r["label"] == "内存"][0]
        self.assertIn("GiB", memory["note"])
        self.assertIn("MemAvailable", memory["note"])

    def test_filesystems_use_available_not_free_bytes(self):
        entry = accept()
        root = [r for r in entry["readings"] if r["label"] == "磁盘 ROOT_FS"][0]
        # available_bytes 58e9 -> 54.0 GiB; free_bytes 60e9 -> 55.9 GiB. Using
        # the wrong field is the 22 GiB root-fs bug the C++ side warns about.
        self.assertIn("54.0 GiB", root["note"])
        self.assertNotIn("55.9 GiB", root["note"])
        self.assertIn("f_bavail", root["note"])

    def test_interface_throughput_is_never_called_public(self):
        entry = accept()
        nets = readings_of(entry, kind="network")
        self.assertTrue(nets)
        for reading in nets:
            self.assertNotIn("公网出向", reading["label"])
            self.assertNotIn("公网出口配额", reading["label"])
            self.assertEqual(reading["scope"], "undifferentiated")
            self.assertIn("未区分公网/内网", reading["note"])

    def test_an_unavailable_speed_is_not_a_quota(self):
        entry = accept()
        # eth0's speed_mbps is UNAVAILABLE (virtio reports -1): no quota appears.
        self.assertEqual([r for r in entry["readings"]
                          if r["label"] == "公网出口配额"], [])
        eth0 = [r for r in entry["readings"] if r["label"] == "网卡 eth0 吞吐"]
        self.assertEqual(len(eth0), 1)
        self.assertNotIn("链路速率", eth0[0]["note"])

    def test_disk_io_is_a_local_throughput_not_network(self):
        entry = accept()
        io = readings_of(entry, kind="disk-io")
        self.assertTrue(io)
        for reading in io:
            self.assertIn("非公网", reading["note"])
            self.assertEqual(reading["scope"], "local-device")

    def test_self_cgroup_is_not_a_host_share(self):
        entry = accept()
        share = [r for r in entry["readings"] if r["kind"] == "cgroup-self"]
        self.assertEqual(len(share), 1)
        self.assertIn("不是用户整机占比", share[0]["note"])
        # No reading masquerades as the user's host-wide share.
        self.assertEqual([r for r in entry["readings"]
                          if r["label"] == "vince 资源份额"], [])
        monitor.validate_model(monitor.monitor_model(devices=[entry], now=NOW))

    def test_gpu_agent_and_service_are_not_invented(self):
        entry = accept()
        labels = [r["label"] for r in entry["readings"]]
        self.assertFalse(any("GPU" in l for l in labels))
        self.assertFalse(any("Agent" in l for l in labels))
        self.assertFalse(any("服务" in l for l in labels))


class FailClosedTest(unittest.TestCase):
    """Every uncertainty lands on a word with a reason, never on 0 or green."""

    def test_readings_unavailable_fails_closed(self):
        env = copy.deepcopy(ENVELOPE)
        env["readings_available"] = False
        env["measurements"] = {"state": "UNAVAILABLE", "reason": "collector 退出 2"}
        entry = accept(env)
        self.assertEqual(entry["reachability"]["state"], "UNKNOWN")
        reading = entry["readings"][0]
        self.assertEqual(reading["state"], "UNKNOWN")
        self.assertIsNone(reading["value"])
        self.assertIn("退出 2", reading["reason"])
        model = monitor.monitor_model(devices=[entry], services=[], now=NOW)
        self.assertNotEqual(monitor._fleet_verdict(model), "OK")

    def test_stale_reading_is_stale_not_current(self):
        entry = accept(now="2026-01-01T02:00:00+00:00")  # ~2h after observed_at
        cpu = [r for r in entry["readings"] if r["label"] == "CPU"][0]
        self.assertEqual(cpu["state"], "STALE")
        self.assertIn("已过期", monitor.reading_line(cpu))

    def test_replayed_is_marked_and_ages_normally(self):
        env = copy.deepcopy(ENVELOPE)
        env["provenance"]["replayed"] = True
        env["provenance"]["executed_now"] = False
        entry = accept(env, now="2026-01-01T02:00:00+00:00")
        self.assertTrue(entry["native"]["replayed"])
        self.assertFalse(entry["native"]["executed_now"])
        # A replay of an old sample is STALE, not a fresh reading.
        cpu = [r for r in entry["readings"] if r["label"] == "CPU"][0]
        self.assertEqual(cpu["state"], "STALE")

    def test_non_live_forces_the_sample_mark(self):
        live = copy.deepcopy(ENVELOPE)
        live["provenance"]["live"] = True
        entry = accept(live)
        model = monitor.monitor_model(devices=[entry], services=[], now=NOW)
        self.assertFalse(model["sample"])
        self.assertNotIn(monitor.SAMPLE_MARK,
                         monitor.render_cardkit(model)["header"]["title"]["content"])
        # The fixture itself is live=false and must be marked.
        self.assertTrue(monitor.monitor_model(devices=[accept()], now=NOW)["sample"])

    def test_missing_window_is_unknown_not_fresh(self):
        env = copy.deepcopy(ENVELOPE)
        env["freshness"] = {"state": "UNKNOWN", "why": "没有记录窗口"}
        entry = accept(env)
        cpu = [r for r in entry["readings"] if r["label"] == "CPU"][0]
        self.assertEqual(cpu["state"], "UNKNOWN")
        self.assertIn("窗口", cpu["reason"])

    def test_unavailable_cpu_measurement_is_unknown(self):
        env = copy.deepcopy(ENVELOPE)
        env["measurements"]["cpu"] = {"state": "UNAVAILABLE", "reason": "无 /proc/stat"}
        entry = accept(env)
        cpu = [r for r in entry["readings"] if r["label"] == "CPU"][0]
        self.assertEqual(cpu["state"], "UNKNOWN")
        self.assertIsNone(cpu["value"])


class ContractBreakTest(unittest.TestCase):
    """A document this bridge cannot accept raises rather than guessing."""

    def _refuses(self, env):
        with self.assertRaises(source.NativeMeasurementRefused):
            accept(env)

    def test_wrong_schema_is_refused(self):
        env = copy.deepcopy(ENVELOPE)
        env["schema"] = "serveros/whatever.v2"
        self._refuses(env)

    def test_scope_not_host_wide_is_refused(self):
        env = copy.deepcopy(ENVELOPE)
        env["scope"] = "account"
        self._refuses(env)

    def test_attribution_scope_not_self_is_refused(self):
        env = copy.deepcopy(ENVELOPE)
        env["attribution"]["scope"] = "user"
        self._refuses(env)

    def test_missing_live_flag_is_refused(self):
        env = copy.deepcopy(ENVELOPE)
        del env["provenance"]["live"]
        self._refuses(env)

    def test_non_boolean_readings_available_is_refused(self):
        env = copy.deepcopy(ENVELOPE)
        env["readings_available"] = "yes"
        self._refuses(env)

    def test_missing_measurements_is_unknown_not_a_crash(self):
        env = copy.deepcopy(ENVELOPE)
        del env["measurements"]
        entry = accept(env)
        self.assertEqual(entry["readings"][0]["state"], "UNKNOWN")


class AppendAndTimeTest(unittest.TestCase):

    def test_two_devices_append(self):
        a = accept(ref="device:a")
        b = accept(ref="device:b")
        model = monitor.monitor_model(devices=[a, b], services=[], now=NOW,
                                      stale_after_s=BOUND)
        self.assertEqual(model["summary"]["devices"]["total"], 2)
        self.assertEqual([d["device_ref"] for d in model["devices"]],
                         ["device:a", "device:b"])

    def test_timezone_offsets_compare_as_one_instant(self):
        # 08:00:10+08:00 is 00:00:10Z — the same instant as NOW.
        entry = accept(now="2026-01-01T08:00:10+08:00")
        cpu = [r for r in entry["readings"] if r["label"] == "CPU"][0]
        self.assertEqual(cpu["state"], "KNOWN")


if __name__ == "__main__":
    unittest.main(verbosity=2)
