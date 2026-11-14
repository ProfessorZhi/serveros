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

It also watches the Fleet MCP sign-in (requirement D-5), from two files the
OAuth issuer writes beside its signing key (reasons, counts and times only):

- ``key-regenerated.json``: the signing key was created anew while clients
  were registered, so every token they hold is now invalid (2026-11-04);
- ``auth-failures.json``: ``AUTH_MIN_FAILURES`` or more refused tokens since
  the last check and not one accepted: clients need to authorize again.

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
AUTH_MIN_FAILURES = 5
#: Refusals that mean "this client must sign in again", not ordinary expiry
#: (an expired access token is refreshed by the client on its own).
AUTH_REAUTH_REASONS = ("signature does not verify",
                       "this client's tokens were revoked; authorize again",
                       "this client was revoked; its tokens no longer work",
                       "this token was revoked")


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
          "failing": "Fleet 监控每轮都在失败",
          "auth_failing": "Fleet MCP 认证一直失败",
          "key_regenerated": "Fleet 签名密钥被重新生成"}
RECOVERED = {"watcher": "Fleet 监控已恢复", "auth": "Fleet MCP 认证已恢复"}
ADVICE = {
    "watcher": ("在东京看：`systemctl --user status serveros-fleet-watch` 和 "
                "`journalctl --user -u serveros-fleet-watch --since -30min | tail -50`。"),
    "auth": ("在每个连 Fleet 的客户端重新授权：Claude Code 里 `/mcp` → serveros-fleet → "
             "Authenticate；Codex 里重新登录 serveros-fleet；ChatGPT / claude.ai 的连接器断开后重连。"),
}


def diagnose_auth(store_root: Path | None, state: dict) -> tuple[str | None, str, dict]:
    """(problem or None, what to say, what to remember) for the Fleet MCP sign-in."""
    if store_root is None:
        return None, "", {}
    seen = {"key_seen_at": state.get("key_seen_at"), "counts": state.get("counts") or {}}
    marker = read_json(store_root / "key-regenerated.json")
    if marker and float(marker.get("at") or 0) > float(state.get("key_seen_at") or 0):
        seen["key_seen_at"] = float(marker["at"])
        when = datetime.fromtimestamp(float(marker["at"]), timezone.utc)
        from . import cards
        return "key_regenerated", (
            "签名密钥在 %s（北京时间）被重新生成，当时已有 %s 个客户端登记过。"
            "它们手里的令牌全部失效，要逐个重新授权。"
            % (cards._local(when, with_date=True), marker.get("registered_clients", "?"))), seen
    doc = read_json(store_root / "auth-failures.json") or {}
    counts = {k: int((v or {}).get("count") or 0) for k, v in (doc.get("counts") or {}).items()}
    before = seen["counts"] if state.get("pid") == doc.get("pid") else {}
    seen.update(counts=counts, pid=doc.get("pid"))
    delta = {k: counts[k] - int(before.get(k) or 0) for k in counts}
    refused = {k: n for k, n in delta.items() if k in AUTH_REAUTH_REASONS and n > 0}
    if sum(refused.values()) >= AUTH_MIN_FAILURES and delta.get("ok", 0) <= 0:
        reasons = "；".join("%s × %d" % (k, n) for k, n in refused.items())
        return "auth_failing", ("上次检查以来 Fleet MCP 拒绝了 %d 次令牌，没有一次成功（%s）。"
                                "连 Fleet 的客户端很可能都要重新授权。"
                                % (sum(refused.values()), reasons)), seen
    return None, "", seen


def card(problem: str | None, text: str, *, now: float, since: float | None,
         reminder: bool = False, domain: str = "watcher") -> dict:
    from . import cards
    moment = datetime.fromtimestamp(now, timezone.utc)
    if problem is None:
        lasted = "" if since is None else "，持续了 %d 分钟" % ((now - since) // 60)
        title = RECOVERED[domain]
        return cards._card("green", title, cards._local(moment, with_date=True) + "（北京时间）",
                           [cards._md("已恢复正常%s。" % lasted)],
                           tags=[("已恢复", "green")], summary=title, width="compact")
    return cards._card("red", TITLES[problem] + ("（仍未恢复）" if reminder else ""),
                       cards._local(moment, with_date=True) + "（北京时间）",
                       [cards._md(text), cards._md(ADVICE[domain])],
                       tags=[("需要处理", "red")], summary=TITLES[problem], width="compact")


def _step(domain: str, problem: str | None, text: str, old: dict, send, now: float,
          *, one_shot: bool = False) -> dict:
    """Open, remind, or clear one domain's problem; return its new state."""
    open_problem = old.get("open")
    new = {"open": open_problem, "since": old.get("since"), "alerted_at": old.get("alerted_at")}
    if problem and problem != open_problem:
        if send(card(problem, text, now=now, since=None, domain=domain),
                "watchdog:%s:%d" % (problem, now)):
            new.update(open=None if one_shot else problem, since=now, alerted_at=now)
    elif problem and problem == open_problem:
        if now - float(old.get("alerted_at") or 0) > REMIND_SECONDS:
            if send(card(problem, text, now=now, since=old.get("since"), reminder=True,
                         domain=domain), "watchdog:%s:remind:%d" % (problem, now)):
                new["alerted_at"] = now
    elif not problem and open_problem:
        if send(card(None, "", now=now, since=old.get("since"), domain=domain),
                "watchdog:%s:recovered:%d" % (domain, now)):
            new.update(open=None, since=None, alerted_at=None)
    return new


def run_once(heartbeat: Path, state_path: Path, send, *, now: float | None = None,
             store_root: Path | None = None) -> dict:
    """One check. ``send(card, key) -> bool``. Returns the new state."""
    now = time.time() if now is None else now
    state = read_json(state_path) or {}
    problem, text, streak = diagnose(read_json(heartbeat), state, now)
    new = {**streak, **_step("watcher", problem, text, state, send, now), "checked_at": now}

    auth_old = state.get("auth") or {}
    problem, text, seen = diagnose_auth(store_root, auth_old)
    if problem == "key_regenerated":
        # A one-shot event: told once, never "recovered".
        stepped = _step("auth", problem, text, {}, send, now, one_shot=True)
        if stepped.get("alerted_at") is None:      # the send failed: ask again next time
            seen["key_seen_at"] = auth_old.get("key_seen_at")
        new["auth"] = {**auth_old, **seen}
    else:
        new["auth"] = {**seen, **_step("auth", problem, text, auth_old, send, now)}
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
    parser.add_argument("--oauth-store", type=Path,
                        help="the OAuth issuer's directory, instead of the one --config names")
    ns = parser.parse_args(argv)
    from fleet_mcp.config import load_config
    if ns.heartbeat:
        heartbeat = ns.heartbeat
    else:
        heartbeat = load_config(ns.config).inbox.path.parent / "watch.heartbeat.json"
    state_path = heartbeat.with_name("watchdog.state.json")
    store_root = ns.oauth_store
    if store_root is None and ns.config:
        from fleet_mcp.runtime import default_store_root
        store_root = default_store_root(load_config(ns.config))
    spec = ns.notify.split(":", 1)[1] if ns.notify.startswith("lark:") else ns.notify
    result = run_once(heartbeat, state_path, lark_sender(spec), store_root=store_root)
    print(json.dumps(result, ensure_ascii=False), file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
