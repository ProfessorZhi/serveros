"""Render one sample of every card and level into this directory.

    PYTHONPATH=<repo>:<repo>/integrations/fleet-next-agent-monitor:<repo>/integrations/fleet-hub-watch \
      python3 integrations/fleet-hub-watch/samples/build_samples.py

Every sample is marked 示例 in its title and its first line (CARDS-STYLE R13).
Paste a file's contents into Feishu's card builder (卡片搭建工具, CardKit 2.0)
to see it rendered; the JSON here is exactly what the hub would send.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from fleet_hub_watch import cards
from fleet_hub_watch import spec as broadcast_spec
from fleet_hub_watch.levels import ALERT, OK, UNCONFIRMED, WARN

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 29, 6, 30, tzinfo=timezone.utc)     # Thu 14:30 Beijing


def reading(key, label, text, level=OK):
    return {"key": key, "label": label, "text": text, "level": level}


TOKYO = {"device_ref": "device:tokyo-shared-dev-01", "name": "Tokyo", "level": OK,
         "observed_at": "2026-10-29T06:29:00Z", "readings": [
             reading("reach", "连接", "已连接"), reading("cpu", "CPU", "18%"),
             reading("memory", "内存", "61% / 64 GiB"), reading("swap", "Swap", "4%（水位）"),
             reading("psi", "内存压力", "some 0.0% · full 0.0%"),
             reading("fs:ROOT_FS", "磁盘 ROOT_FS", "41% · 余 118 GiB"),
             reading("fs:DATA", "磁盘 DATA", "57% · 余 860 GiB")]}
DELL_WARN = {"device_ref": "device:llm-lab-dell", "name": "Dell A6000", "level": WARN,
             "observed_at": "2026-10-29T06:29:30Z", "readings": [
                 reading("reach", "连接", "已连接"), reading("cpu", "CPU", "72%"),
                 reading("memory", "内存", "91% / 256 GiB", WARN),
                 reading("fs:DATA", "磁盘 DATA", "78% · 余 820 GiB"),
                 reading("gpu:0", "GPU0 显存", "41/48 GiB"),
                 reading("gpu:1", "GPU1 显存", "12/48 GiB")]}
DELL_ALERT = {**DELL_WARN, "level": ALERT, "readings": DELL_WARN["readings"][:3] + [
    reading("fs:DATA", "磁盘 DATA", "93% · 余 260 GiB", ALERT)] + DELL_WARN["readings"][4:]}
WINDOWS = {"device_ref": "device:work-fj", "name": "FJPCOS", "level": UNCONFIRMED,
           "observed_at": None, "readings": [reading("reach", "连接", "未接入资源读数", UNCONFIRMED)]}

EPISODE = {"episode": "device:llm-lab-dell|fs:DATA", "key": "fs:DATA",
           "device_ref": "device:llm-lab-dell", "kind": "node.pressure",
           "subject": "Dell A6000 · 磁盘 DATA",
           "first_seen": "2026-10-29T06:02:00Z", "last_seen": "2026-10-29T06:30:00Z",
           "evidence": "fs:DATA|ALERT"}

def samples(spec: dict) -> dict:
    return {
    "resource-green": cards.resource_card([TOKYO, {**DELL_WARN, "level": OK, "readings": [
        r if r["key"] != "memory" else reading("memory", "内存", "64% / 256 GiB")
        for r in DELL_WARN["readings"]]}], now=NOW, sample=True, spec=spec),
    "resource-yellow": cards.resource_card([TOKYO, DELL_WARN, WINDOWS], now=NOW, sample=True, spec=spec),
    "resource-red": cards.resource_card([TOKYO, DELL_ALERT, WINDOWS], now=NOW, sample=True, spec=spec),
    "alert-yellow": cards.alert_card({**EPISODE, "level": WARN,
                                      "text": "已用 86% · 余 560 GiB（注意线 85%）"}, now=NOW, sample=True, spec=spec),
    "alert-red": cards.alert_card({**EPISODE, "level": ALERT,
                                   "text": "已用 93% · 余 260 GiB（报警线 92%）"}, now=NOW, sample=True, spec=spec),
    "alert-escalated": cards.alert_card({**EPISODE, "level": WARN, "state": "ESCALATED",
                                         "text": "已用 86% · 余 560 GiB（注意线 85%）"}, now=NOW, sample=True, spec=spec),
    "alert-recovered": cards.alert_card({**EPISODE, "level": ALERT, "state": "RECOVERED",
                                         "recovered_at": "2026-10-29T07:10:00Z",
                                         "text": "已用 80% · 余 800 GiB"}, now=NOW, sample=True, spec=spec),
    "run-failed": cards.alert_card({
        "episode": "run|device:tokyo-shared-dev-01|claude|s-1|run-7f3a", "key": "run:run-7f3a",
        "kind": "run.failed", "device_ref": "device:tokyo-shared-dev-01", "level": ALERT,
        "subject": "任务失败 · serveros", "text": "run-7f3a（Tokyo）执行失败（RUN_FAILED）",
        "first_seen": "2026-10-29T06:25:00Z", "last_seen": "2026-10-29T06:25:00Z",
        "evidence": "device:tokyo-shared-dev-01|claude|s-1"}, now=NOW, sample=True, spec=spec),
    "run-stalled": cards.alert_card({
        "episode": "run|device:llm-lab-dell|claude-fjd|s-9|run-22c1", "key": "run:run-22c1",
        "kind": "run.stalled", "device_ref": "device:llm-lab-dell", "level": WARN,
        "subject": "任务运行时间过长 · lab-notes", "text": "run-22c1（Dell A6000）仍在运行，已超过 2 小时",
        "first_seen": "2026-10-29T04:20:00Z", "last_seen": "2026-10-29T06:20:00Z"}, now=NOW, sample=True, spec=spec),
    "completed": cards.completion_card([
        {"run_id": "run-11ab", "project": "serveros", "device_name": "Tokyo"},
        {"run_id": "run-11ac", "project": "ability-management", "device_name": "Tokyo"},
        {"run_id": "run-90fe", "project": "lab-notes", "device_name": "Dell A6000"}], now=NOW, sample=True, spec=spec),
    "daily": cards.daily_card({
        "runs": {"completed": 6, "failed": 1, "unknown": 1, "stalled": 0},
        "problems": [
            {"subject": "任务失败 · serveros", "text": "run-7f3a（Tokyo）执行失败（RUN_FAILED）", "level": ALERT},
            {"subject": "任务结果无法确认 · litellm", "text": "run-5b20（Tokyo）没能确认它完成了，也没能确认它失败了", "level": WARN}],
        "resources": [{"subject": "Dell A6000 · 磁盘 DATA", "text": "已用 93% · 余 260 GiB",
                       "level": ALERT, "recovered": True}],
        "open_now": [{"subject": "任务运行时间过长 · lab-notes", "text": "run-22c1（Dell A6000）仍在运行，已超过 2 小时",
                      "level": WARN}]},
        now=NOW.replace(hour=10), sample=True, spec=spec),
}


SAMPLES = samples(broadcast_spec.DEFAULT_SPEC)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spec", help="render with this broadcast spec instead of the default")
    parser.add_argument("--out", help="write here instead of samples/")
    args = parser.parse_args(argv)
    out = Path(args.out) if args.out else HERE
    out.mkdir(parents=True, exist_ok=True)
    rendered = samples(broadcast_spec.load(args.spec)) if args.spec else SAMPLES
    for name, card in rendered.items():
        (out / f"{name}.json").write_text(json.dumps(card, ensure_ascii=False, indent=2) + "\n",
                                           encoding="utf-8")


if __name__ == "__main__":
    main()
