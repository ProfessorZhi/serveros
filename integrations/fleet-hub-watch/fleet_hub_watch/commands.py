"""Ask in Feishu, get a card back — the pull side of the broadcast.

The owner sends the bot a short message; the hub answers with one card:

    状态            every device on one line: level and the readings that matter
    任务            open batches, then the ones that just finished
    批次 <名字|id>  one batch: each task, outcome and its answer
    资源 <设备>      that device's full 资源播报 card
    追踪 <id>       a run's or batch's way through the fleet, and where it stopped
    记忆 <词>       what the fleet has learned about it
    帮助            this list

Only the owner is answered (the same check the card buttons use). The push
side keeps only what needs a person now: alerts, a dispatched batch finishing,
and the daily report; everything else is asked for here.

Requires, once, in the Feishu developer console: Events & Callbacks → event
configuration → long connection, with `im.message.receive_v1`, and the bot's
"receive messages" permission.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any, Callable

from . import cards
from .levels import ALERT, ICON, OK, TEMPLATE, UNCONFIRMED, WARN, WORD, worst

CONSUME_MESSAGES = ["event", "consume", "im.message.receive_v1", "--as", "bot"]

COMMANDS = {
    "状态": "status", "status": "status",
    "任务": "tasks", "tasks": "tasks", "批次": "batch", "batch": "batch",
    "资源": "resource", "resource": "resource",
    "追踪": "trace", "trace": "trace",
    "记忆": "memory", "memory": "memory",
    "帮助": "help", "help": "help", "?": "help", "？": "help",
}

HELP = [("状态", "每台设备一行：是否正常、关键读数"),
        ("资源", "每台设备的完整资源播报卡（也可以直接问「资源情况」「东京内存怎么样」）"),
        ("任务", "进行中的批次，和刚完成的"),
        ("批次 名字", "一个批次里每个任务的结论"),
        ("资源 设备", "只看那一台的完整资源播报"),
        ("追踪 id", "一个任务/批次走到哪一步、卡在哪"),
        ("记忆 关键词", "指挥中心记下的结论和规矩")]


def parse(text: str) -> tuple[str, str] | None:
    """(command, argument) from what the owner typed, or None."""
    text = re.sub(r"@_user_\d+|@\S+", " ", text or "").strip()
    if not text:
        return None
    head, _, rest = text.partition(" ")
    command = COMMANDS.get(head.strip().lower())
    if command is None:
        # "资源东京" without a space
        for word, name in COMMANDS.items():
            if len(word) > 1 and text.startswith(word):
                return name, text[len(word):].strip()
        return intent(text)
    return command, rest.strip()


#: Words that mean "show me the resources" in what the owner types freely:
#: 资源情况 / 资源缺口 / 东京内存怎么样 / 显卡占用 …
RESOURCE_WORDS = ("资源", "内存", "显存", "显卡", "gpu", "cpu", "磁盘", "硬盘", "负载",
                  "swap", "缺口", "占用", "压力")
STATUS_WORDS = ("状态", "怎么样", "健康", "正常吗", "还好吗")
TASK_WORDS = ("任务", "在跑", "跑完", "进度")


def intent(text: str) -> tuple[str, str] | None:
    """(command, the whole text) for a free sentence, or None. The answerer
    finds a device named anywhere in the text; none named means all of them."""
    low = text.lower()
    for words, command in ((RESOURCE_WORDS, "resource"), (TASK_WORDS, "tasks"),
                           (STATUS_WORDS, "status")):
        if any(w in low for w in words):
            return command, text
    return None


def message_of(event: dict[str, Any], owner: str | None) -> tuple[str, str] | None:
    """(message_id, text) from a receive event, when the owner sent text; else None."""
    payload = event.get("event", event)
    sender = (payload.get("sender") or {}).get("sender_id") or {}
    who = sender.get("open_id") or payload.get("sender_id") or payload.get("open_id")
    if isinstance(who, dict):
        who = who.get("open_id")
    if not owner or who != owner:
        return None
    message = payload.get("message") or payload
    if (message.get("message_type") or message.get("msg_type")) not in ("text", None):
        return None
    content = message.get("content")
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except ValueError:
            content = {"text": content}
    text = (content or {}).get("text") if isinstance(content, dict) else None
    if not isinstance(text, str):
        return None
    return str(message.get("message_id") or payload.get("message_id") or ""), text


# -- the answers -----------------------------------------------------------------

def _reading(view: dict, key: str) -> dict | None:
    return next((r for r in view.get("readings") or [] if r.get("key") == key), None)


def _short(text: str) -> str:
    """'88% / 16 GiB' -> '88%', '100%（水位）' -> '100%': the number a phone needs."""
    text = re.sub(r"（[^）]*）", "", str(text or ""))
    return text.split(" / ")[0].split(" · ")[0].strip()


def status_card(views: list[dict], *, now: datetime, running: dict[str, int] | None = None) -> dict:
    """One block per device, two short lines each, the problem first:

        ⚠️ **FJPCOS**　内存 88%
        CPU 6% · 内存 88% · 2 个任务

    Owner, 2026-11-09: the first version ran every reading into one long line
    that wrapped mid-phrase, with （水位）（注意） brackets everywhere.
    """
    level = worst(v.get("level") for v in views) if views else UNCONFIRMED
    counts = {lv: sum(1 for v in views if v.get("level") == lv) for lv in (ALERT, WARN)}
    head = " · ".join(f"{counts[lv]} {WORD[lv]}" for lv in (ALERT, WARN) if counts[lv]) or "全部正常"
    blocks = []
    order = {ALERT: 0, WARN: 1, UNCONFIRMED: 2, OK: 3}
    for view in sorted(views, key=lambda v: order.get(v.get("level"), 4)):
        readings = {r.get("key"): r for r in view.get("readings") or []}
        bad = [r for r in view.get("readings") or [] if r.get("level") in (WARN, ALERT)]
        reason = " · ".join(
            f"<font color='{'red' if r['level'] == ALERT else 'orange'}'>{r['label']} {_short(r['text'])}</font>"
            for r in bad[:3]) or "<font color='green'>正常</font>"
        facts = [f"{label} {_short(readings[key]['text'])}" for key, label in
                 (("cpu", "CPU"), ("memory", "内存"), ("swap", "Swap"))
                 if key in readings and readings[key].get("text")]
        n = (running or {}).get(view.get("device_ref"), 0)
        if n:
            facts.append(f"{n} 个任务在跑")
        blocks.append(f"{ICON.get(view.get('level'), '⚪')} **{view.get('name')}**　{reason}\n"
                      f"<font color='grey'>{' · '.join(facts)}</font>")
    elements = [cards._md("\n\n".join(blocks) or "没有设备读数"),
                cards._md("<font color='grey'>「资源」看每台的完整卡 · 「资源 东京」只看一台 · 「任务」看在跑的</font>")]
    return cards._card(TEMPLATE[level], f"{len(views)} 台设备 · {head}",
                       cards._local(now, with_date=True), elements,
                       summary=f"Fleet 状态 · {head}", width="default")


def tasks_card(batches: list[dict], *, now: datetime) -> dict:
    open_ = [b for b in batches if b.get("closed_seq") is None]
    done = [b for b in batches if b.get("closed_seq") is not None][:5]
    lines = []
    if open_:
        lines.append("**进行中**")
        lines += [f"⏳ {b['name']}　{b['settled']}/{b['total']}　"
                  f"<font color='grey'>已 {cards._minutes(b['opened_at'], now)}</font>" for b in open_]
    if done:
        lines.append("**刚完成**")
        lines += [f"✅ {b['name']}　{b['total']} 个" for b in done]
    elements = [cards._md("\n".join(lines) or "最近没有派发的批次")]
    elements.append(cards._md("<font color='grey'>发「批次 名字」看每个任务的结论</font>"))
    return cards._card(TEMPLATE[OK], f"任务 · {len(open_)} 个批次进行中",
                       cards._local(now, with_date=True), elements,
                       summary=f"任务 · {len(open_)} 个进行中", width="default")


def text_card(title: str, lines: list[str], *, now: datetime, template: str = "blue") -> dict:
    return cards._card(template, title, cards._local(now, with_date=True),
                       [cards._md("\n".join(lines) or "（空）")], summary=title, width="default")


def help_card(*, now: datetime) -> dict:
    return text_card("可以问我这些", [f"**{c}**　{d}" for c, d in HELP], now=now)


class Answerer:
    """Builds the card for one command from what the hub already holds."""

    def __init__(self, *, service: Any, principal: Any, inbox: Any, notifier: Any,
                 names: dict[str, str], watching: Callable[[], list[dict]] | None = None) -> None:
        self.service, self.principal, self.inbox = service, principal, inbox
        #: The monitor's live watch list: runs not yet finished, by device.
        self.watching = watching or (lambda: [])
        self.notifier, self.names = notifier, names
        self.views: list[dict] = []
        self.rich: dict[str, dict] = {}

    ALIASES = {"东京": "tokyo", "a6000": "dell", "戴尔": "dell", "dell": "dell",
               "windows": "work", "win": "work", "fjpcos": "work", "work-fj": "work",
               "tokyo": "tokyo"}

    def _device(self, text: str) -> str | None:
        text = (text or "").strip().lower()
        if not text:
            return None
        for ref, name in self.names.items():
            if text in (name or "").lower() or text in ref.lower():
                return ref
        alias = self.ALIASES.get(text)
        found = next((r for r in self.names if alias and alias in r), None)
        if found:
            return found
        # A sentence: a device named anywhere in it.
        for word, alias in self.ALIASES.items():
            if word in text:
                found = next((r for r in self.names if alias in r), None)
                if found:
                    return found
        return next((ref for ref, name in self.names.items()
                     if name and len(name) > 2 and name.lower() in text), None)

    TREND_WINDOW = timedelta(hours=6)

    def _history(self, ref: str, now: datetime) -> dict:
        reader = getattr(self.notifier, "history", None)
        try:
            return reader(ref, now - self.TREND_WINDOW) if reader else {}
        except Exception:  # noqa: BLE001 - a card without a curve is still a card
            return {}

    def _resource(self, ref: str, now: datetime) -> dict:
        from .notify import rich_card
        view = next((v for v in self.views if v.get("device_ref") == ref), None)
        history = self._history(ref, now)
        card = rich_card(self.rich.get(ref) or {}, now)
        if card is not None:
            labels = {r["key"]: r["label"] for r in (view or {}).get("readings") or []}
            return cards.with_chart(card, cards.trend_chart(
                cards._eid("trend", ref), history, labels, title="近 6 小时（%）"))
        if view is None:
            return text_card("没有这台设备的读数", [self.names.get(ref, ref)], now=now)
        running = sum(1 for row in self.watching()
                      if (row.get("device_ref") or str(row.get("session_ref", "")).split("|", 1)[0]) == ref)
        return cards.device_card(view, now=now, running=running, history=history)

    def answer(self, command: str, arg: str, now: datetime) -> dict:
        if command == "status":
            running: dict[str, int] = {}
            for row in self.watching():
                ref = row.get("device_ref") or str(row.get("session_ref", "")).split("|", 1)[0]
                running[ref] = running.get(ref, 0) + 1
            return status_card(self.views, now=now, running=running)
        if command == "tasks":
            return tasks_card(self.inbox.recent_batches(15), now=now)
        if command == "batch":
            batch_id = self.inbox.find_batch(arg) if arg else None
            if batch_id is None:
                recent = self.inbox.recent_batches(1)
                batch_id = recent[0]["batch_id"] if recent else None
            if batch_id is None:
                return text_card("没找到这个批次", [f"「{arg}」没有匹配的批次；发「任务」看最近的"], now=now)
            head = self.inbox.batch(batch_id)
            runs, opened = self.notifier._batch_runs({"work_ref": f"batch:{batch_id}"})
            pending = head["total"] - head["settled"]
            event = {"summary": f"{head['name']}：{head['total']} 个任务"
                                + (f"，还有 {pending} 个没结束" if pending else ""),
                     "level": "notify"}
            return cards.batch_card(event, now=now, runs=runs, opened_at=opened,
                                    device_names=self.names)
        if command == "resource":
            ref = self._device(arg)
            if ref is not None:
                return self._resource(ref, now)
            # No device named (资源 / 资源情况 / 资源缺口): every device's full card.
            refs = [v.get("device_ref") for v in self.views if v.get("device_ref")] \
                or list(self.rich) or list(self.names)
            return [self._resource(r, now) for r in dict.fromkeys(refs)] or text_card(
                "还没有读数", ["设备读数还没到，稍后再问"], now=now)
        if command == "trace":
            from fleet_mcp.trace import diagnose, exchange, line
            if not arg:
                return text_card("追踪什么？", ["发「追踪 批次名或id」或「追踪 任务id」"], now=now)
            batch_id = self.inbox.find_batch(arg)
            steps = self.inbox.read_trace(batch_id=batch_id) if batch_id else \
                self.inbox.read_trace(run_id=arg)
            verdict = diagnose(steps)
            body = [f"**{verdict['stage']}**　{verdict['next']}"]
            if verdict.get("timings"):
                body.append("用时　" + " · ".join(verdict["timings"]))
            body += [f"**{label}**\n{text[:800]}" for label, text in exchange(steps)]
            body += [f"<font color='grey'>{line(s)}</font>" for s in steps[-15:]]
            return text_card(f"追踪 · {arg[:24]}", body, now=now,
                             template="green" if verdict["stage"] == "DELIVERED" else "orange")
        if command == "memory":
            notes = self.inbox.recall(arg or None, limit=8)
            return text_card(f"记忆 · {arg or '最近'}",
                             [f"**{n['topic']}**　<font color='grey'>{n['scope']} · {n['at'][:10]} · #{n['id']}</font>\n{n['text']}"
                              for n in notes] or ["还没有相关的记忆"], now=now)
        return help_card(now=now)


def _listener_base():
    from .feishu import ButtonListener
    return ButtonListener


class MessageListener(_listener_base()):
    """`lark-cli event consume im.message.receive_v1 --as bot`, one thread,
    restarted — the card-button listener's process discipline, with its own
    parsing: owner only, text only, one card back per message."""

    def __init__(self, owner: str | None, reply: Callable[[str, str, str], str]) -> None:
        super().__init__(owner, lambda value: "", argv=CONSUME_MESSAGES)
        self.reply = reply

    def feed(self, line: str) -> str | None:
        try:
            event = json.loads(line)
        except ValueError:
            return None
        got = message_of(event, self.owner)
        if got is None:
            return None
        message_id, text = got
        command, arg = parse(text) or ("help", "")
        self.handled += 1
        return self.reply(message_id, command, arg)
