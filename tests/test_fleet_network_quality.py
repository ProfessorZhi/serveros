"""Network quality, three ways (owner, 2026-11-12: «加一个网络质量检测 …
尤其是我这边本地的 … 三设备网络质量检测»).

1. the hub times every read it makes of a device (`link`);
2. each device probes its own paths into `network.readonly`;
3. the 资源 card has a 网络 block that says which path is better today.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NET = ROOT / "native" / "node-core" / "core-lite" / "net"
for extra in (ROOT, NET, ROOT / "integrations" / "fleet-hub-watch",
              ROOT / "integrations" / "fleet-next-agent-monitor"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

import fold_network  # noqa: E402
import probe_network  # noqa: E402
from fleet_hub_watch import cards, commands, compose, resources  # noqa: E402
from fleet_hub_watch.journal import Journal  # noqa: E402
from fleet_hub_watch.levels import OK, WARN  # noqa: E402
from fleet_mcp import core_lite_readback as readback  # noqa: E402
from fleet_mcp.inbox import EventInbox  # noqa: E402

NOW = datetime(2026, 11, 12, 2, 0, tzinfo=timezone.utc)
WORK = "device:work-fj"
TOKYO = "device:tokyo-shared-dev-01"


class TheDeviceProbesItsOwnPaths(unittest.TestCase):
    def test_median_and_loss_from_tcp_connects(self):
        times = iter([0.0, 0.100, 1.0, 1.300, 2.0, 3.0, 3.200])
        attempts = iter([True, True, False, True])

        class Conn:
            def close(self):
                pass

        def connect(address, timeout):
            if not next(attempts):
                raise OSError("reset")
            return Conn()
        got = probe_network.measure("h", 22, 4, 1.0, connect=connect, clock=lambda: next(times))
        self.assertEqual(got, {"rtt_ms": 200.0, "loss_percent": 25.0, "samples": 4})

    def test_every_connect_failing_is_no_rtt_and_full_loss(self):
        def connect(address, timeout):
            raise OSError("timed out")
        self.assertEqual(probe_network.measure("h", 22, 3, 0.1, connect=connect),
                         {"rtt_ms": None, "loss_percent": 100.0, "samples": 3})

    def test_only_a_lower_case_name_reaches_the_file(self):
        self.assertEqual(probe_network.parse_target("tokyo-public=example.net:22"),
                         ("tokyo-public", "example.net", 22))
        for bad in ("Tokyo=h:22", "tokyo=h", "tokyo=h:99999", "=h:22", "a b=h:22"):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                probe_network.parse_target(bad)

    def test_a_fresh_probe_is_folded_and_an_old_one_is_not(self):
        status = {"schema": "serveros/core-lite-status.v1", "observations": [{"name": "cpu"}]}
        fresh = {"paths": [{"name": "local", "rtt_ms": 9.0, "loss_percent": 0.0, "samples": 3}],
                 "sample_time": (NOW - timedelta(minutes=4)).strftime("%Y-%m-%dT%H:%M:%SZ")}
        folded = fold_network.fold(json.loads(json.dumps(status)), fresh, NOW, 900)
        self.assertEqual(folded["observations"][-1]["name"], "network.readonly")
        old = dict(fresh, sample_time=(NOW - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%SZ"))
        self.assertEqual(fold_network.fold(json.loads(json.dumps(status)), old, NOW, 900), status)
        self.assertEqual(fold_network.fold(json.loads(json.dumps(status)), None, NOW, 900), status)


def net_doc(paths):
    return {"observations": [{"name": "network.readonly", "state": "PRESENT",
                              "completeness": "COMPLETE", "value": {"paths": paths}}]}


class TheReaderChecksTheShape(unittest.TestCase):
    def test_a_good_row_is_carried(self):
        out = readback.normalize_observations(net_doc(
            [{"name": "tokyo-public", "rtt_ms": 1200.5, "loss_percent": 15.0, "samples": 4},
             {"name": "tokyo-tailnet", "rtt_ms": None, "loss_percent": 100, "samples": 4}]))
        self.assertEqual(out["network.readonly"].state, "PRESENT")

    def test_a_host_cannot_ride_in_as_a_name(self):
        for row in ({"name": "10.0.0.1", "rtt_ms": 1, "loss_percent": 0, "samples": 1},
                    {"name": "ok", "rtt_ms": None, "loss_percent": 50, "samples": 2},
                    {"name": "ok", "rtt_ms": -1, "loss_percent": 0, "samples": 1},
                    {"name": "ok", "rtt_ms": 1, "loss_percent": 0, "samples": 1, "host": "x"}):
            with self.subTest(row=row):
                out = readback.normalize_observations(net_doc([row]))
                self.assertEqual(out["network.readonly"].state, "UNAVAILABLE")


def view_with_paths(paths, previous=None):
    summary = {"connected": "CONNECTED", "freshness": {"state": "FRESH"},
               "observations": {"network.readonly": {"state": "PRESENT", "value": {"paths": paths}}}}
    return resources.device_view(WORK, "FJPCOS", summary, THRESHOLDS, previous or {})


THRESHOLDS = {k: 90.0 for k in resources.THRESHOLD_KEYS}
THRESHOLDS.update({k: 5.0 for k in resources.THRESHOLD_KEYS if k.endswith("recovery_margin")})


class TheHubJudgesIt(unittest.TestCase):
    def test_a_lossy_path_is_warn_and_clears_only_well_below(self):
        bad = view_with_paths([{"name": "tokyo-public", "rtt_ms": 1200, "loss_percent": 25, "samples": 4}])
        (net,) = [r for r in bad["readings"] if r["key"] == "net:tokyo-public"]
        self.assertEqual((net["level"], net["text"]), (WARN, "1200 ms · 丢 25%"))
        middling = [{"name": "tokyo-public", "rtt_ms": 900, "loss_percent": 15, "samples": 4}]
        held = view_with_paths(middling, {"net:tokyo-public": WARN})
        self.assertEqual([r["level"] for r in held["readings"] if r["key"].startswith("net:")], [WARN])
        fresh = view_with_paths(middling)
        self.assertEqual([r["level"] for r in fresh["readings"] if r["key"].startswith("net:")], [OK])

    def test_a_path_that_never_connected_is_not_an_alarm(self):
        dead = [{"name": "tokyo-tailnet", "rtt_ms": None, "loss_percent": 100, "samples": 3}]
        never = view_with_paths(dead)
        self.assertEqual([r["level"] for r in never["readings"] if r["key"].startswith("net:")],
                         ["UNCONFIRMED"])
        broke = view_with_paths(dead, {"net:tokyo-tailnet": OK})
        self.assertEqual([r["level"] for r in broke["readings"] if r["key"].startswith("net:")], [WARN])

    def test_the_hub_times_its_reads_and_grades_the_link(self):
        with tempfile.TemporaryDirectory() as td:
            store = resources.LevelStore(Path(td) / "r.sqlite")
            for i in range(12):
                at = (NOW + timedelta(minutes=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
                store.note_link(WORK, 12.0 if i % 3 == 0 else 0.8, i % 4 != 0, at)
            stats = store.link_stats(WORK, NOW.strftime("%Y-%m-%dT%H:%M:%SZ"))
            self.assertEqual(stats["n"], 12)
            self.assertEqual(stats["fail"], 25.0)
            reading = resources.link_reading(stats, None)
            self.assertEqual(reading["level"], WARN)
            self.assertIn("失败 25%", reading["text"])
            self.assertIsNone(store.link_stats(TOKYO, NOW.strftime("%Y-%m-%dT%H:%M:%SZ")),
                              "no grade before there are enough reads")


def fleet():
    work = view_with_paths([
        {"name": "tokyo-public", "rtt_ms": 1250, "loss_percent": 15, "samples": 4},
        {"name": "tokyo-tailnet", "rtt_ms": 900, "loss_percent": 0, "samples": 4},
        {"name": "local", "rtt_ms": 12, "loss_percent": 0, "samples": 4}])
    work["readings"].append(resources.link_reading({"n": 30, "p50": 0.8, "p95": 3.1, "fail": 4.0}, None))
    return [work]


class TheCardSaysWhichPathIsBetter(unittest.TestCase):
    def test_a_network_question_gets_the_network_block(self):
        layout = compose.auto_layout(fleet(), focus="network")
        self.assertEqual(layout[0]["block"], "network")
        card = compose.compose(layout, fleet(), now=NOW)
        text = cards.card_text(card)
        self.assertIn("东京读它", text)
        self.assertIn("到东京走 tailnet 更好（900 ms，公网 1.2 s 丢 15%）", text)
        self.assertTrue(card["header"]["title"]["content"].startswith("Fleet 网络"))
        self.assertNotIn("table", json.dumps(card), "one column per device, not a wide table")
        self.assertNotIn("example", text)

    def test_a_path_that_never_connects_is_named_not_blamed_on_the_route(self):
        """11-12 10:4x: A6000's tailnet probe never connected; the card said
        «本地出口正常，慢在去东京的那一段» while its public path was fine."""
        dell = view_with_paths([
            {"name": "tokyo-public", "rtt_ms": 472, "loss_percent": 0, "samples": 3},
            {"name": "tokyo-tailnet", "rtt_ms": None, "loss_percent": 100, "samples": 3},
            {"name": "local", "rtt_ms": 13, "loss_percent": 0, "samples": 3}])
        said = compose.advice([dell])
        self.assertEqual(len(said), 1)
        self.assertIn("tailnet 连不上东京", said[0])
        card = compose.compose([{"block": "bar", "metric": "network", "device": None}], [dell], now=NOW)
        self.assertFalse([e for e in card["body"]["elements"] if e.get("tag") == "chart"],
                         "no 100% loss bar: the network is drawn as its own block")

    def test_the_owner_can_just_ask(self):
        self.assertEqual(commands.parse("网络怎么样")[0], "resource")
        self.assertEqual(commands.Answerer._focus(commands.Answerer, "FJPCOS 老断")[0], "network")


class TheJournalKeepsTheMachinesState(unittest.TestCase):
    def test_a_snapshot_on_change_and_every_ten_minutes(self):
        with tempfile.TemporaryDirectory() as td:
            clock = [1000.0]
            journal = Journal(EventInbox(Path(td) / "i.sqlite"), Path(td) / "clone",
                              clock=lambda: clock[0])
            self.assertIsNotNone(journal.note_resources(fleet(), NOW))
            clock[0] += 60
            self.assertIsNone(journal.note_resources(fleet(), NOW), "nothing changed, too soon")
            clock[0] += 600
            path = journal.note_resources(fleet(), NOW + timedelta(minutes=11))
            text = path.read_text(encoding="utf-8")
            self.assertIn("resources", str(path))
            self.assertIn("网络 tokyo-public 1250 ms · 丢 15%", text)
            self.assertIn("东京读它", text)
            self.assertEqual(text.count("## "), 2)


if __name__ == "__main__":
    unittest.main()
