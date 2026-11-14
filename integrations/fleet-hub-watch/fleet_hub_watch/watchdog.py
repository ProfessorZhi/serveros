"""Watch the watcher (requirement O-3).

    python -m fleet_hub_watch.watchdog --config <fleet-v1.yaml> --notify lark:owner

Run by its own timer (``serveros-fleet-watchdog.timer``, every 5 minutes), in
its own process, so that it still runs when the watcher is the thing that is
broken. It reads the heartbeat the watcher writes every tick
(``watch.heartbeat.json`` beside the inbox) and tells the owner, once, when:

- the heartbeat is missing, or older than ``STALE_SECONDS`` (the watcher
  stopped, hangs, or never started);
- the heartbeat says ``ok: false`` on ``FAIL_STREAK`` checks in a row (the
  watcher runs but every tick fails).

Why: from 2026-11-12 19:03 to the next afternoon every tick failed with
``TransportFault``; the service stayed "active", nothing restarted it, and
nobody heard about it for a day. The watcher cannot report its own death.

It sends one card when a problem opens, a reminder every ``REMIND_SECONDS``
while it stays open, and one card when it clears. Its own state lives in
``watchdog.state.json`` next to the heartbeat. A send that fails leaves the
problem un-announced so the next run tries again.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

STALE_SECONDS = 300          # ten 30 s ticks
FAIL_STREAK = 3              # three 5-minute checks: about 15 minutes of failing ticks
REMIND_SECONDS = 6 * 3600


def read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, document: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False)
    os.replace(tmp, path)


def diagnose(beat: dict | None, state: dict, now: float) -> tuple[str | None, str, dict]:
    """(problem or None, what to say, the updated failure streak state)."""
    streak = int(state.get("streak") or 0)
    if beat is None:
        return "missing", "没有找到监控的心跳文件：监控进程可能从没启动过，或者心跳路径变了。", \
            {"streak": 0}
    last = beat.get("last_tick_at")
    age = now - float(last) if isinstance(last, (int, float)) else None
    if age is None or age > STALE_SECONDS:
        minutes = "未知" if age is None else "%d 分钟" % (age // 60)
        return "stopped", "监控已经 %s 没有完成一轮检查：进程可能停了或卡住了。" % minutes, \
            {"streak": 0}
    if beat.get("ok") is False:
        streak += 1
        error = str(beat.get("error") or "未知错误")[:300]
        if streak >= FAIL_STREAK:
            return "failing", "监控在运行，但最近 %d 次检查每轮都失败：%s" % (streak, error), \
                {"streak": streak, "error": error}
        return None, "", {"streak": streak, "error": error}
    return None, "", {"streak": 0}


TITLES = {"missing": "Fleet 监控没有心跳", "stopped": "Fleet 监控停了",
          "failing": "Fleet 监控每轮都在失败"}


def card(problem: str | None, text: str, *, now: float, since: float | None,
         reminder: bool = False) -> dict:
    from . import cards
    moment = datetime.fromtimestamp(now, timezone.utc)
    if problem is None:
        lasted = "" if since is None else "，持续了 %d 分钟" % ((now - since) // 60)
        return cards._card("green", "Fleet 监控已恢复", cards._local(moment, with_date=True) + "（北京时间）",
                           [cards._md("监控恢复正常%s。" % lasted)],
                           tags=[("已恢复", "green")], summary="Fleet 监控已恢复", width="compact")
    advice = ("在东京看：`systemctl --user status serveros-fleet-watch` 和 "
              "`journalctl --user -u serveros-fleet-watch --since -30min | tail -50`。")
    return cards._card("red", TITLES[problem] + ("（仍未恢复）" if reminder else ""),
                       cards._local(moment, with_date=True) + "（北京时间）",
                       [cards._md(text), cards._md(advice)],
                       tags=[("需要处理", "red")], summary=TITLES[problem], width="compact")


def run_once(heartbeat: Path, state_path: Path, send, *, now: float | None = None) -> dict:
    """One check. ``send(card, key) -> bool``. Returns the new state."""
    now = time.time() if now is None else now
    state = read_json(state_path) or {}
    problem, text, streak = diagnose(read_json(heartbeat), state, now)
    open_problem = state.get("open")
    new = {**streak, "open": open_problem, "since": state.get("since"),
           "alerted_at": state.get("alerted_at"), "checked_at": now}

    if problem and problem != open_problem:
        if send(card(problem, text, now=now, since=None), "watchdog:%s:%d" % (problem, now)):
            new.update(open=problem, since=now, alerted_at=now)
    elif problem and problem == open_problem:
        if now - float(state.get("alerted_at") or 0) > REMIND_SECONDS:
            if send(card(problem, text, now=now, since=state.get("since"), reminder=True),
                    "watchdog:%s:remind:%d" % (problem, now)):
                new["alerted_at"] = now
    elif not problem and open_problem:
        if send(card(None, "", now=now, since=state.get("since")),
                "watchdog:recovered:%d" % now):
            new.update(open=None, since=None, alerted_at=None)
    write_json(state_path, new)
    return new


def lark_sender(spec: str):
    from . import feishu
    from .notify import LarkTransport
    transport = LarkTransport(feishu.resolve(spec))

    def send(card_doc: dict, key: str) -> bool:
        return bool(transport.send(card_doc, idempotency_key=key[:50]))
    return send


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="watch the Fleet hub watcher")
    parser.add_argument("--config", help="the hub binding; the heartbeat sits beside its inbox")
    parser.add_argument("--heartbeat", type=Path, help="the heartbeat file, instead of --config")
    parser.add_argument("--notify", default="lark:owner")
    ns = parser.parse_args(argv)
    if ns.heartbeat:
        heartbeat = ns.heartbeat
    else:
        from fleet_mcp.config import load_config
        heartbeat = load_config(ns.config).inbox.path.parent / "watch.heartbeat.json"
    state_path = heartbeat.with_name("watchdog.state.json")
    spec = ns.notify.split(":", 1)[1] if ns.notify.startswith("lark:") else ns.notify
    result = run_once(heartbeat, state_path, lark_sender(spec))
    print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
