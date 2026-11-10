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

from fleet_hub_watch import cards, commands
from fleet_hub_watch import spec as broadcast_spec
from fleet_hub_watch.levels import ALERT, OK, UNCONFIRMED, WARN

HERE = Path(__file__).resolve().parent
NOW = datetime(2026, 10, 29, 6, 30, tzinfo=timezone.utc)     # Thu 14:30 Beijing


def reading(key, label, text, level=OK, **more):
    return {"key": key, "label": label, "text": text, "level": level, **more}


TOKYO = {"device_ref": "device:tokyo-shared-dev-01", "name": "Tokyo", "level": OK,
         "observed_at": "2026-10-29T06:29:00Z", "readings": [
             reading("reach", "连接", "已连接"), reading("cpu", "CPU", "18%"),
             reading("memory", "内存", "61% / 64 GiB"), reading("swap", "交换区", "4%", value=4),
             reading("psi", "内存吃紧", "程序有 0.0% 的时间在等内存", short="0.0%"),
             reading("fs:ROOT_FS", "系统盘", "41% · 余 118 GiB", value=41),
             reading("fs:DATA", "数据盘", "57% · 余 860 GiB", value=57)]}
TOKYO_WARN = {**TOKYO, "level": WARN, "readings": TOKYO["readings"][:3] + [
    reading("swap", "交换区", "100%", WARN, value=100),
    reading("psi", "内存吃紧", "程序有 2.1% 的时间在等内存", WARN, short="2.1%")] + TOKYO["readings"][5:]}
FJ_WARN = {"device_ref": "device:work-fj", "name": "FJPCOS", "level": WARN,
           "observed_at": "2026-10-29T06:29:00Z", "readings": [
               reading("reach", "连接", "已连接"), reading("cpu", "CPU", "63%", value=63),
               reading("memory", "内存", "87% / 16 GiB", WARN, value=87),
               reading("fs:ROOT_FS", "系统盘", "74% · 余 61 GiB", value=74)]}
HISTORY = {"cpu": [(f"2026-10-29T{h:02d}:{m:02d}:00Z", 40 + (i * 7) % 30)
                   for i, (h, m) in enumerate((h, m) for h in range(1, 7) for m in (0, 30))],
           "memory": [(f"2026-10-29T{h:02d}:{m:02d}:00Z", 78 + i * 0.8)
                      for i, (h, m) in enumerate((h, m) for h in range(1, 7) for m in (0, 30))]}
PROBE = "做一次只读验收探针：获取本机 hostname 和内存"
BATCH_RUNS = [
    {"session_ref": "device:tokyo-shared-dev-01|claude|s-1", "run_id": "r-1", "label": PROBE,
     "outcome": "completed", "excerpt": "VM-16-5-ubuntu · 内存 58.9%（145 / 246 GiB）"},
    {"session_ref": "device:llm-lab-dell|claude-fjd|s-2", "run_id": "r-2", "label": PROBE,
     "outcome": "completed", "excerpt": "FJ-NJ-SER-1009 · 内存 67.1%（20.5 / 30.6 GiB）"},
    {"session_ref": "device:work-fj|claude|s-3", "run_id": "r-3", "label": PROBE, "outcome": "unknown"}]
NAMES = {"device:tokyo-shared-dev-01": "Tokyo", "device:llm-lab-dell": "Dell A6000",
         "device:work-fj": "FJPCOS"}
DELL_WARN = {"device_ref": "device:llm-lab-dell", "name": "Dell A6000", "level": WARN,
             "observed_at": "2026-10-29T06:29:30Z", "readings": [
                 reading("reach", "连接", "已连接"), reading("cpu", "CPU", "72%"),
                 reading("memory", "内存", "91% / 256 GiB", WARN),
                 reading("fs:DATA", "数据盘", "78% · 余 820 GiB"),
                 reading("gpu:0", "GPU0 显存", "41/48 GiB"),
                 reading("gpu:1", "GPU1 显存", "12/48 GiB")]}
DELL_ALERT = {**DELL_WARN, "level": ALERT, "readings": DELL_WARN["readings"][:3] + [
    reading("fs:DATA", "数据盘", "93% · 余 260 GiB", ALERT)] + DELL_WARN["readings"][4:]}
WINDOWS = {"device_ref": "device:work-fj", "name": "FJPCOS", "level": UNCONFIRMED,
           "observed_at": None, "readings": [reading("reach", "连接", "未接入资源读数", UNCONFIRMED)]}

EPISODE = {"episode": "device:llm-lab-dell|fs:DATA", "key": "fs:DATA",
           "device_ref": "device:llm-lab-dell", "kind": "node.pressure",
           "subject": "Dell A6000 · 数据盘",
           "first_seen": "2026-10-29T06:02:00Z", "last_seen": "2026-10-29T06:30:00Z",
           "evidence": "fs:DATA|ALERT"}

def as_sample(card: dict) -> dict:
    """Mark a card whose builder takes no `sample=` (R13: title and first line)."""
    card["header"]["title"]["content"] += f" · {cards.SAMPLE_TITLE}"
    card["body"]["elements"].insert(0, cards._md(cards.SAMPLE_LINE))
    return card


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
        "subject": "任务失败 · serveros", "text": "Tokyo：执行出错，没有完成",
        "first_seen": "2026-10-29T06:25:00Z", "last_seen": "2026-10-29T06:25:00Z",
        "evidence": "device:tokyo-shared-dev-01|claude|s-1 · run run-7f3a · RUN_FAILED"}, now=NOW, sample=True, spec=spec),
    "run-stalled": cards.alert_card({
        "episode": "run|device:llm-lab-dell|claude-fjd|s-9|run-22c1", "key": "run:run-22c1",
        "kind": "run.stalled", "device_ref": "device:llm-lab-dell", "level": WARN,
        "subject": "任务跑了很久 · lab-notes", "text": "Dell A6000：已运行超过 2 小时，还没结束",
        "first_seen": "2026-10-29T04:20:00Z", "last_seen": "2026-10-29T06:20:00Z"}, now=NOW, sample=True, spec=spec),
    "completed": cards.completion_card([
        {"run_id": "run-11ab", "label": "跑一遍 serveros 的单元测试", "device_name": "Tokyo",
         "excerpt": "全部通过：12 317 个测试，用时 4 分钟"},
        {"run_id": "run-11ac", "label": "更新能力目录", "device_name": "Tokyo"},
        {"run_id": "run-90fe", "label": "整理实验笔记", "device_name": "Dell A6000",
         "excerpt": "整理了 14 篇，合并重复的 3 篇"}], now=NOW, sample=True, spec=spec),
    "daily": cards.daily_card({
        "runs": {"completed": 6, "failed": 1, "unknown": 2, "stalled": 1},
        "problems": [
            {"subject": "任务失败 · serveros", "text": "Tokyo：执行出错，没有完成", "level": ALERT},
            {"subject": "任务结果不明 · 只输出 hostname", "text": "FJPCOS：已经结束，但读不到它的输出", "level": WARN},
            {"subject": "任务结果不明 · 只输出 hostname", "text": "FJPCOS：已经结束，但读不到它的输出", "level": WARN},
            {"subject": "任务跑了很久 · lab-notes", "text": "Dell A6000：已运行超过 2 小时，还没结束", "level": WARN}],
        "resources": [{"subject": "Dell A6000 · 数据盘", "text": "已用 93% · 余 260 GiB",
                       "level": ALERT, "recovered": True}],
        "open_now": [{"subject": "任务跑了很久 · lab-notes", "text": "Dell A6000：已运行超过 2 小时，还没结束",
                      "level": WARN}]},
        now=NOW.replace(hour=10), sample=True, spec=spec),
    "daily-quiet": cards.daily_card({"runs": {"completed": 9}, "problems": [], "resources": [],
                                     "open_now": []}, now=NOW.replace(hour=10), sample=True, spec=spec),
    "status": as_sample(commands.status_card([FJ_WARN, TOKYO_WARN, {**DELL_WARN, "level": OK, "readings": [
        r if r["key"] != "memory" else reading("memory", "内存", "67% / 256 GiB")
        for r in DELL_WARN["readings"]]}], now=NOW, running={"device:work-fj": 2})),
    "device-warn": as_sample(cards.device_card(FJ_WARN, now=NOW, running=2, history=HISTORY)),
    "device-ok": as_sample(cards.device_card(TOKYO, now=NOW, history=HISTORY)),
    "batch": as_sample(cards.batch_card({"summary": "journal 验收 11-10：3 个任务"}, now=NOW, runs=BATCH_RUNS,
                              opened_at="2026-10-29T06:26:00Z", device_names=NAMES)),
    "batch-done": as_sample(cards.batch_card({"summary": "journal 验收 11-10：2 个任务"}, now=NOW,
                                   runs=BATCH_RUNS[:2], opened_at="2026-10-29T06:26:00Z",
                                   device_names=NAMES)),
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
