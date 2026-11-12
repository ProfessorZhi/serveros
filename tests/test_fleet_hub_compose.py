"""A 资源 card composed for the question (owner, 2026-11-12: «卡片单一 …
多种花样的表、图，监控 agent 自己聪明地组合»)."""
from __future__ import annotations

import json
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
for extra in (ROOT, ROOT / "integrations" / "fleet-hub-watch",
              ROOT / "integrations" / "fleet-next-agent-monitor"):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from fleet_hub_watch import cards, compose, intent  # noqa: E402

NOW = datetime(2026, 11, 12, 3, 0, tzinfo=timezone.utc)
TOKYO, DELL, WORK = "device:tokyo-shared-dev-01", "device:llm-lab-dell", "device:work-fj"
NAMES = {TOKYO: "Tokyo", DELL: "Dell A6000", WORK: "FJPCOS"}


def reading(key, label, value, level="OK"):
    return {"key": key, "label": label, "text": f"{value:.0f}%", "level": level, "value": value}


def views():
    return [
        {"device_ref": TOKYO, "name": "Tokyo", "level": "WARN", "readings": [
            reading("cpu", "CPU", 23), reading("memory", "内存", 64),
            reading("swap", "交换区", 97, "WARN"), reading("fs:DATA", "数据盘", 71)]},
        {"device_ref": DELL, "name": "Dell A6000", "level": "OK", "readings": [
            reading("cpu", "CPU", 8), reading("memory", "内存", 31),
            reading("fs:DATA", "数据盘", 44), reading("gpu:0", "GPU0 显存", 62)]},
        {"device_ref": WORK, "name": "FJPCOS", "level": "OK", "readings": [
            reading("cpu", "CPU", 41), reading("memory", "内存", 77), reading("fs:SYSTEM", "系统盘", 58)]},
    ]


def histories():
    out = {}
    for v in views():
        h = {}
        for r in v["readings"]:
            h[r["key"]] = [((NOW - timedelta(minutes=30 * (6 - i))).strftime("%Y-%m-%dT%H:%M:%SZ"),
                            max(0, r["value"] - 6 + i)) for i in range(7)]
        out[v["device_ref"]] = h
    return out


def tags(card):
    found = []

    def walk(x):
        if isinstance(x, dict):
            if "tag" in x:
                found.append(x["tag"] if x["tag"] != "chart" else x["chart_spec"]["type"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(card["body"])
    return found


class TheModelChoosesTheShape(unittest.TestCase):
    def test_a_layout_is_checked_against_the_closed_set(self):
        got = compose.read_layout([
            {"block": "radar"}, {"block": "bar", "metric": "disk"},
            {"block": "tiles"},                                   # no device: dropped
            {"block": "tiles", "device": "Tokyo"},
            {"block": "exec", "device": "Tokyo"},                 # not a block
            {"block": "ring", "metric": "rm -rf", "device": "nobody"},
            {"block": "radar"},                                   # a repeat
            {"block": "trend", "metric": "gpu"},
        ], NAMES)
        self.assertEqual(got, [
            {"block": "radar", "metric": None, "device": None},
            {"block": "bar", "metric": "disk", "device": None},
            {"block": "tiles", "metric": None, "device": TOKYO},
            {"block": "ring", "metric": None, "device": None},
        ], "at most four, each known, devices by name only")
        self.assertEqual(compose.read_layout("table", NAMES), [])

    def test_the_numbers_are_the_hubs_not_the_models(self):
        card = compose.compose([{"block": "bar", "metric": "memory", "device": None}],
                               views(), now=NOW)
        (chart,) = [e for e in card["body"]["elements"] if e.get("tag") == "chart"]
        self.assertEqual({v["device"]: v["value"] for v in chart["chart_spec"]["data"]["values"]},
                         {"Tokyo": 64, "Dell A6000": 31, "FJPCOS": 77})

    def test_the_recognizer_keeps_the_layout_of_a_resource_answer(self):
        reply = json.dumps({"command": "resource", "device": None, "detail": False, "focus": None,
                            "query": "", "layout": [{"block": "radar"}, {"block": "rank"}],
                            "reply": "东京交换区快满了。"}, ensure_ascii=False)
        ask = intent.Recognizer(["model"], run=lambda *a, **k: subprocess.CompletedProcess(a, 0, reply, ""))
        self.assertEqual(ask("谁最吃紧", NAMES)[0], "resource")
        self.assertEqual([b["block"] for b in ask.layout], ["radar", "rank"])
        self.assertIn("radar", intent.prompt_for("资源情况", NAMES), "the model is told the blocks")


class TheReadingsChooseWithoutAModel(unittest.TestCase):
    def test_a_fleet_with_a_problem_leads_with_it_and_varies_its_charts(self):
        layout = compose.auto_layout(views())
        self.assertEqual([b["block"] for b in layout], ["rank", "table", "bar", "trend"])
        self.assertEqual(layout[-1]["metric"], "swap", "the curve is the reading under pressure")
        card = compose.compose(layout, views(), now=NOW, histories=histories())
        self.assertEqual([t for t in tags(card) if t in ("table", "bar", "line")],
                         ["table", "bar", "line"])
        from fleet_hub_watch.levels import TEMPLATE
        self.assertEqual(card["header"]["template"], TEMPLATE["WARN"])
        text = cards.card_text(card)
        self.assertIn("交换区", text)
        self.assertIn("Tokyo | 注意", text.replace("**", ""))

    def test_one_device_gets_tiles_rings_and_its_curves(self):
        card = compose.compose(compose.auto_layout(views(), device=DELL), views(), now=NOW,
                               histories=histories())
        kinds = tags(card)
        self.assertIn("pie", kinds)
        self.assertIn("line", kinds)
        self.assertEqual(card["header"]["title"]["content"], "Dell A6000 · 资源")

    def test_every_block_renders_and_validates(self):
        for block in compose.BLOCKS:
            with self.subTest(block=block):
                card = compose.compose([{"block": block, "metric": None,
                                         "device": TOKYO if block == "tiles" else None}],
                                       views(), now=NOW, histories=histories())
                self.assertGreater(len(card["body"]["elements"]), 1, "the block drew something")
                plain = cards.plain(card)
                self.assertNotIn("table", tags(plain))
                self.assertFalse([t for t in tags(plain) if t in ("line", "bar", "pie", "radar")])

    def test_nothing_to_draw_is_said(self):
        card = compose.compose(compose.auto_layout([]), [], now=NOW)
        self.assertIn("还没有可画的读数", cards.card_text(card))


if __name__ == "__main__":
    unittest.main()
