"""One task's way through the fleet, read from the hub's trace.

Every step is written on the hub (Tokyo, the command centre) as it happens:
Fleet dispatches → the monitor watches and observes → the reply is read → the
event is recorded → the batch closes → a Feishu card is sent, held or skipped
(and why). This module reads those steps back as one timeline and names the
step it stopped at, so neither a person nor an agent has to search six places.

    python3 -m fleet_mcp.trace [--config <binding>] (--run ID | --batch ID | --session REF | --minutes N)

It shows states, codes, ids and counts — never a reply, an instruction or a value.
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Any

#: What the last step means, and what to look at next.
_NEXT = [
    ("device waiting for a person", "WAITING_FOR_PERSON",
     "设备上的 agent 停下来在问人：去那台设备的会话里回答，或让指挥端中断它重新派"),
    ("card sent", "DELIVERED", "飞书卡片已发出"),
    ("card send FAILED", "CARD_FAILED", "飞书发送失败：在东京查 lark-cli 登录（feishu 自检卡）和 hub-watch 日志"),
    ("card held", "CARD_HELD", "卡片被挂起：播报暂停或本轮发送已到上限，下一轮会再试"),
    ("no card: told on the batch card", "WAITING_FOR_BATCH", "结果会出现在批次卡上：批次里还有任务没结束"),
    ("no card: a session's own turn", "NOT_A_TASK", "不是 Fleet 派出的任务，只计入日报"),
    ("queued for the 任务完成 card", "QUEUED", "已排进下一张任务完成卡（最多 5 分钟）"),
    ("batch completed", "BATCH_DONE", "批次已完成，正在发卡"),
    ("event run.", "RECORDED", "结果已记录，等通知器处理（下一轮，30 秒内）"),
    ("no reply in the session", "UNREADABLE", "任务结束但读不到回复：看 detail.shape，告诉维护者实际格式"),
    ("reply read from the session", "READ", "从会话里读到了回复，下一步记录结果"),
    ("dropped", "DROPPED", "监控放弃了它（不是派出的任务）"),
    ("device working", "WORKING", "设备上的 agent 在干活（看 detail.tools / turns）"),
    ("device finished", "RECORDED", "设备上的 agent 已结束，等记录结果"),
    ("observed", "WATCHING", "监控在看，还没结束（看 detail.status / reason）"),
    ("watching", "WATCHING", "监控已接手，等第一次读取"),
    ("joined batch", "DISPATCHED", "已派发并编进批次，等监控接手（30 秒内）"),
    ("batch opened", "DISPATCHED", "批次已建立"),
    ("dispatched", "DISPATCHED", "已派发，等监控接手（30 秒内；超过说明 hub-watch 没在跑）"),
]


_MARKS = (("dispatched", "派发"), ("watching", "监控接手"), ("observed", "第一次读到状态"),
          ("device finished", "设备干完"),
          ("reply read", "读到回复"), ("event run.", "结果记录"), ("card sent", "飞书卡发出"))


def timings(steps: list[dict[str, Any]]) -> list[str]:
    """Seconds from dispatch to each stage that happened — where the time went."""
    from datetime import datetime

    def at(step):
        return datetime.strptime(step["at"], "%Y-%m-%dT%H:%M:%SZ")
    firsts = {}
    for step in steps:
        for prefix, word in _MARKS:
            if step["step"].startswith(prefix) and word not in firsts:
                firsts[word] = at(step)
    start = firsts.get("派发")
    if start is None:
        return []
    return [f"{word} +{int((firsts[word] - start).total_seconds())}s"
            for _, word in _MARKS if word in firsts and word != "派发"]


def diagnose(steps: list[dict[str, Any]]) -> dict[str, Any]:
    """The stage it stopped at, and — per task — where the time went. Several
    tasks in one window are timed separately (owner, 2026-11-09: mixing them
    gave negative durations)."""
    verdict = _diagnose(steps)
    runs = sorted({s["run_id"] for s in steps if s.get("run_id")})
    if len(runs) <= 1:
        took = timings(steps)
        if took:
            verdict["timings"] = took
    else:
        per = {run[:8]: timings([s for s in steps if s.get("run_id") == run]) for run in runs}
        verdict["timings_by_run"] = {k: v for k, v in per.items() if v}
    return verdict


def _diagnose(steps: list[dict[str, Any]]) -> dict[str, Any]:
    # The commander's own calls are context, not the task's stage.
    steps = [s for s in steps if s.get("source") != "commander"] or steps
    if not steps:
        return {"stage": "NOT_FOUND",
                "next": "东京没有这条记录：任务不是经 Fleet 派发的，或早于 1.11（之前没有 trace）"}
    last = steps[-1]["step"]
    for prefix, stage, hint in _NEXT:
        if last.startswith(prefix):
            return {"stage": stage, "last_step": last, "at": steps[-1]["at"], "next": hint}
    return {"stage": "UNKNOWN", "last_step": last, "at": steps[-1]["at"], "next": "看完整时间线"}


def line(step: dict[str, Any]) -> str:
    who = step.get("run_id") or step.get("batch_id") or step.get("ref") or ""
    device = (step.get("device_ref") or "").removeprefix("device:")
    detail = ", ".join(f"{k}={v}" for k, v in (step.get("detail") or {}).items())
    return (f"{step['at'][11:19]}  {step['source']:<7} {step['step']}"
            + (f"  [{device}]" if device else "") + (f"  {who[:12]}" if who else "")
            + (f"  ({detail})" if detail else ""))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", default=None)
    parser.add_argument("--run"); parser.add_argument("--batch"); parser.add_argument("--session")
    parser.add_argument("--minutes", type=int)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    if not (args.run or args.batch or args.session or args.minutes):
        parser.error("name a --run, --batch, --session or --minutes")
    from .config import load_config
    from .inbox import EventInbox
    config = load_config(args.config)
    if config.inbox is None and args.config is None:
        # The hub's Fleet binding, where the inbox and its trace live.
        from pathlib import Path
        for candidate in ("~/.config/serveros/fleet-v1.yaml", "~/.config/serveros/fleet.yaml"):
            path = Path(candidate).expanduser()
            if path.is_file():
                config = load_config(str(path))
                if config.inbox is not None:
                    break
    if config.inbox is None:
        print("this binding has no inbox: nothing is traced here", file=sys.stderr)
        return 2
    steps = EventInbox(config.inbox.path).read_trace(
        run_id=args.run, batch_id=args.batch, session_ref=args.session, minutes=args.minutes)
    verdict = diagnose(steps)
    if args.json:
        print(json.dumps({"steps": steps, "diagnosis": verdict}, ensure_ascii=False, indent=2))
    else:
        for step in steps:
            print(line(step))
        print(f"\n→ {verdict['stage']}: {verdict['next']}")
        if verdict.get("timings"):
            print("  用时：" + " · ".join(verdict["timings"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
