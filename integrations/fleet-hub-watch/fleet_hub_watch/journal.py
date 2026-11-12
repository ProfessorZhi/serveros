"""The fleet journal: every finished batch and task as one Markdown file in a
private Git repository, pushed from the hub.

What a file holds — the exchange between the commander (GPT) and the devices,
not the devices' own working:

    主人的原话            what the owner asked (the commander's batch note)
    发给设备的提示词       what each device was sent
    设备的最终回复         the device's final answer, in full — what the
                          commander read back
    做了什么              metadata only: turns, tools, skills, MCP, model, time
    过程                  the trace lines, metadata only

The device's own step-by-step work stays where it ran, in its session: the file
names that session (`session_ref`) so it can be opened there. Nothing secret is
written: text passes the inbox's key-shaped mask, and no transcript is copied.

It is a subscriber of the hub inbox with its own cursor, so it sees each
finished batch and each finished stand-alone task once, and a restart picks up
where it stopped. Commits are gathered and pushed at most every `push_every`
seconds; a push that fails is retried on a later tick, never lost.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from .levels import WORD

SUBSCRIBER = "fleet-journal"
KIND_WORDS = {"chat": "聊天类（ChatGPT、Grok 等）", "agent": "agent 类（Claude Code、Codex 等）",
              "other": "其他"}
CN = ZoneInfo("Asia/Shanghai")
#: On the first tick only events this recent are written; older history is not back-filled.
FIRST_RUN_WINDOW = timedelta(days=1)
RUN_KINDS = ("run.completed", "run.failed", "run.unknown")
OUTCOME = {"completed": "✅ 完成", "failed": "❌ 失败", "unknown": "⚪ 结果不明", None: "⏳ 未结束"}
MAX_REPLY = 60_000          # a final answer longer than this is cut, and says so

README = """# Fleet journal

ServerOS Fleet 的任务记录，由东京 hub 自动写入（`fleet_hub_watch.journal`），不要手改。

- `runs/YYYY/MM/DD/` 每个批次或单个任务一个文件：主人的原话、发给设备的提示词、
  设备的最终回复（原文）、设备做了什么（只有元数据），以及过程的时间线。
- 每天的 `INDEX.md` 一行一条。
- `commander/chat/YYYY/MM/DD.md` 主人和聊天类指挥端（ChatGPT、Grok 等）的对话
- `commander/agent/YYYY/MM/DD.md` 主人和 agent 类指挥端（Claude Code、Codex 等）的对话
  （GPT 用 `fleet_batch action=reply` 写回）。
- `cards/YYYY/MM/DD.md` 每一张发到飞书的卡片（发送和原位更新），按文字留档。
- `chat/YYYY/MM/DD.md` 主人在飞书里和管家的每一次对话：问了什么、怎么理解的、
  管家说了什么、用了多久。
- 设备的具体执行过程留在设备本地的会话里；文件里写了会话的 `session_ref`。
- 不含任何密钥：文本写入前按密钥的样子遮蔽，不复制会话原文。
"""


def _mask(text: Any) -> str:
    from fleet_mcp.inbox import SECRETISH
    return SECRETISH.sub("‹已遮蔽›", str(text or ""))


def _slug(text: str) -> str:
    text = re.sub(r"[\\/:*?\"<>|\s]+", "-", text or "").strip("-.")
    return text[:40] or "task"


def _local(moment: str | None) -> str:
    if not moment:
        return "—"
    try:
        at = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return moment
    return at.astimezone(CN).strftime("%Y-%m-%d %H:%M:%S")


def _span(delta: timedelta) -> str:
    seconds = int(delta.total_seconds())
    if seconds <= 0:
        return "—"
    if seconds < 60:
        return f"{seconds} 秒"
    if seconds < 3600:
        return f"{seconds // 60} 分 {seconds % 60} 秒"
    return f"{seconds // 3600} 小时 {seconds % 3600 // 60} 分"


def _fence(text: str) -> str:
    longest = max([len(m) for m in re.findall(r"`+", text)] + [2])
    ticks = "`" * (longest + 1)
    return f"{ticks}text\n{text}\n{ticks}"


class Journal:
    def __init__(self, inbox: Any, root: Path, *,
                 reply: Callable[[str, str], str | None] | None = None,
                 names: dict[str, str] | None = None,
                 push_every: float = 600.0, ssh_key: str | None = None,
                 clock: Callable[[], float] | None = None,
                 git: Callable[..., subprocess.CompletedProcess] | None = None) -> None:
        self.inbox, self.root = inbox, Path(root)
        self.reply = reply
        self.names = names or {}
        self.push_every = push_every
        self.ssh_key = ssh_key
        self.clock = clock or __import__("time").time
        self._git = git or self._run_git
        self._last_push = 0.0
        self._pending = 0
        self._chat_lock = __import__("threading").Lock()
        #: Events older than a day before this process started are not back-filled.
        self._born = datetime.now(timezone.utc)
        #: `commander_log.AgentSessions`, when the hub reads agent commanders.
        self.agents = None

    # -- reading what happened -------------------------------------------------

    def _steps(self, **where: Any) -> list[dict[str, Any]]:
        try:
            return self.inbox.read_trace(**where, limit=5000)
        except Exception:  # noqa: BLE001 - a file without a timeline is still a record
            return []

    @staticmethod
    def _text(steps: list[dict], name: str, run_id: str | None = None) -> str | None:
        found = None
        for step in steps:
            if step["step"] == name and (run_id is None or step.get("run_id") == run_id):
                text = (step.get("detail") or {}).get("text")
                if text:
                    found = text
        return found

    @staticmethod
    def _detail(steps: list[dict], name: str, run_id: str | None = None) -> dict:
        found: dict = {}
        for step in steps:
            if step["step"] == name and (run_id is None or step.get("run_id") == run_id):
                found = step.get("detail") or {}
        return found

    def _answer(self, session_ref: str, run_id: str, steps: list[dict]) -> str | None:
        """This run's final answer. The trace kept it when the run finished; it
        is clipped at the trace's limit, so a reply at that length is read again
        from the session — and used only if it is the same answer (the session
        may have moved on to a later task since)."""
        from fleet_mcp.inbox import TRACE_TEXT_MAX
        kept = self._text(steps, "result", run_id)
        text = kept
        if (kept is None or len(kept) >= TRACE_TEXT_MAX) and self.reply is not None:
            try:
                live = self.reply(session_ref, run_id)
            except Exception:  # noqa: BLE001 - the trace keeps a clipped copy
                live = None
            if live and (kept is None or _mask(live).startswith(kept[:200])):
                text = live
        if text and len(text) > MAX_REPLY:
            text = text[:MAX_REPLY] + f"\n…（超过 {MAX_REPLY} 字，后面截掉了；全文在设备会话里）"
        return text

    def _dispatcher(self, opened_by: str | None, steps: list[dict]) -> str:
        """Who dispatched: the commander's name and kind, from its own calls."""
        client = next(((s.get("detail") or {}).get("client") for s in steps
                       if s.get("source") == "commander" and (s.get("detail") or {}).get("client")
                       and (s.get("detail") or {}).get("by") in (opened_by, None)), None)
        if client is None and opened_by != "local-owner":
            return opened_by or "—"
        kind, name = self.commander_kind(client, opened_by)
        return f"{name}（{ {'chat': '聊天类', 'agent': 'agent 类'}.get(kind, '其他') }）"

    def _device(self, session_ref: str) -> str:
        ref = session_ref.split("|", 1)[0]
        return self.names.get(ref) or ref.removeprefix("device:")

    # -- writing ----------------------------------------------------------------

    def _run_section(self, member: dict, steps: list[dict], heading: str = "###") -> list[str]:
        ref, run_id = member["session_ref"], member["run_id"]
        work = self._detail(steps, "device finished", run_id)
        out = [f"{heading} {self._device(ref)} · {OUTCOME.get(member.get('outcome'), member.get('outcome'))}",
               "",
               f"- run `{run_id}` · 会话 `{ref}`（具体执行过程在这台设备的会话里）"]
        facts = [f"{k} {work[k]}" for k in ("turns", "tools", "skills", "mcp", "model") if work.get(k)]
        if work.get("waiting_for_person"):
            facts.append("停下来等人回答")
        if facts:
            out.append("- 做了什么：" + " · ".join(str(f) for f in facts))
        prompt = self._text(steps, "prompt sent", run_id)
        if prompt:
            out += ["", "**发给设备的提示词**", "", _fence(_mask(prompt))]
        answer = self._answer(ref, run_id, steps)
        out += ["", "**设备的最终回复（原文）**", "",
                _fence(_mask(answer)) if answer else "_没读到回复_", ""]
        return out

    def _timeline(self, steps: list[dict]) -> list[str]:
        """The trace in Beijing time; a run of 'observed' lines that say the same
        thing is folded into its first line and a count."""
        from fleet_mcp.trace import line
        if not steps:
            return []
        rows, last_key, folded = [], None, 0
        for step in steps:
            detail = step.get("detail") or {}
            key = ((step.get("run_id"), detail.get("status"), detail.get("reason"))
                   if step["step"] == "observed" else None)
            if key is not None and key == last_key:
                folded += 1
                continue
            if folded:
                rows.append(f"          …同样的观测又 {folded} 次")
                folded = 0
            last_key = key
            local = _local(step.get("at"))[11:19]
            rows.append(local + line(step)[8:])
        if folded:
            rows.append(f"          …同样的观测又 {folded} 次")
        return ["## 过程（元数据，北京时间）", "", "<details><summary>时间线</summary>", "",
                _fence(_mask("\n".join(rows))), "", "</details>", ""]

    # -- the chain, timed --------------------------------------------------------
    #
    # Owner, 2026-11-12: «记录整个链路的时间戳，比如我给指挥侧发消息的时间».
    # A chat commander (ChatGPT) does not tell Fleet when the owner wrote; the
    # first Fleet call it made in that turn is the closest fact there is, and
    # is labelled as such.

    #: Calls closer together than this are one commander turn.
    TURN_GAP = 180
    TURN_LOOKBACK_MINUTES = 30

    def _turn_start(self, by: str | None, first_dispatch: str | None) -> str | None:
        """When the commander began the turn that dispatched: its earliest call
        in an unbroken run of calls ending at the dispatch."""
        if not by or not first_dispatch or not hasattr(self.inbox, "trace_between"):
            return None
        try:
            end = datetime.fromisoformat(first_dispatch.replace("Z", "+00:00"))
            start = end - timedelta(minutes=self.TURN_LOOKBACK_MINUTES)
            calls = self.inbox.trace_between(start.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                             first_dispatch, source="commander")
        except Exception:  # noqa: BLE001 - a page without this line is still a page
            return None
        stamps = [c["at"] for c in calls if (c.get("detail") or {}).get("by") == by]
        if not stamps:
            return None
        earliest = stamps[-1]
        for previous in reversed(stamps[:-1]):
            gap = (datetime.fromisoformat(earliest.replace("Z", "+00:00"))
                   - datetime.fromisoformat(previous.replace("Z", "+00:00"))).total_seconds()
            if gap > self.TURN_GAP:
                break
            earliest = previous
        return earliest

    @staticmethod
    def _first(steps: list[dict], names: tuple[str, ...], run_id: str | None = None,
               last: bool = False, **detail: Any) -> str | None:
        found = None
        for step in steps:
            if step["step"] not in names or (run_id and step.get("run_id") != run_id):
                continue
            if any((step.get("detail") or {}).get(k) != v for k, v in detail.items()):
                continue
            found = step["at"]
            if not last:
                return found
        return found

    def _chain(self, steps: list[dict], runs: list[dict], by: str | None) -> list[str]:
        """## 链路时间: each hop of the chain with its time and the wait before it."""
        dispatch = self._first(steps, ("prompt sent", "dispatched (create)", "dispatched (send)",
                                       "dispatched"))
        rows: list[tuple[str, str | None]] = []
        began = self._turn_start(by, dispatch)
        if began:
            rows.append(("指挥端开始处理（主人发消息就在这之前）", began))
        rows.append(("指挥端派出任务", dispatch))
        card = self._first(steps, ("card sent", "card sent: task dispatched"))
        if card:
            rows.append(("飞书「进行中」卡片发出", card))
        for run in runs:
            rid, name = run["run_id"], self._device(run["session_ref"])
            rows.append((f"{name} 开始干活", self._first(steps, ("device working",), rid)
                         or self._first(steps, ("observed",), rid, status="RUNNING")))
            rows.append((f"{name} 做完", self._first(steps, ("device finished",), rid)))
            rows.append((f"Fleet 取回 {name} 的结果", self._first(steps, ("result",), rid)))
        done = self._first(steps, ("batch completed",))
        if done:
            rows.append(("全部结束（批次关闭）", done))
        updated = self._first(steps, ("card updated",), last=True)
        if updated:
            rows.append(("飞书卡片最后一次更新", updated))
        for step in steps:
            if step["step"] == "reply to owner":
                rows.append(("指挥端回复主人", step["at"]))
        rows = [(what, at) for what, at in rows if at]
        if not rows:
            return []
        rows.sort(key=lambda r: r[1])
        out = ["## 链路时间（北京时间）", "", "| 环节 | 时间 | 距上一步 | 距开始 |", "|---|---|---|---|"]
        start = previous = datetime.fromisoformat(rows[0][1].replace("Z", "+00:00"))
        for what, at in rows:
            moment = datetime.fromisoformat(at.replace("Z", "+00:00"))
            out.append(f"| {what} | {_local(at)[11:]} | {_span(moment - previous)} | "
                       f"{_span(moment - start)} |")
            previous = moment
        return out + [""]

    def write_batch(self, batch_id: str, event: dict) -> Path | None:
        head = self.inbox.batch(batch_id)
        steps = self._steps(batch_id=batch_id)
        ended = event.get("occurred_at") or next(
            (s["at"] for s in reversed(steps) if s["step"] == "batch completed"), None)
        opened = self._detail(steps, "owner request")
        counts: dict[str, int] = {}
        for run in head["runs"]:
            counts[run.get("outcome") or "pending"] = counts.get(run.get("outcome") or "pending", 0) + 1
        verdict = ("全部完成" if counts.get("completed") == head["total"]
                   else "，".join(f"{OUTCOME.get(k, k)} {v}" for k, v in counts.items()))
        lines = [f"# {_mask(head['name'])} · {verdict}", "",
                 f"- 批次 `{batch_id}` · 派发者 {self._dispatcher(head.get('opened_by'), steps)} · "
                 f"{head['total']} 个任务",
                 f"- 开始 {_local(head.get('opened_at'))} · 结束 {_local(ended)}（北京时间）"]
        if opened.get("skills"):
            lines.append(f"- 指挥侧用的 skill：{opened['skills']}")
        commander = [s for s in steps if s.get("source") == "commander"
                     and s["step"] not in ("owner request", "reply to owner")]
        if commander:
            calls: dict[str, int] = {}
            for s in commander:
                calls[s["step"]] = calls.get(s["step"], 0) + 1
            lines.append("- 指挥侧调用：" + " · ".join(f"{k}×{v}" for k, v in calls.items()))
        request = self._text(steps, "owner request")
        lines += ["", "## 主人的原话", "", _fence(_mask(request)) if request else "_没有记录_", "",
                  "## 每台设备", ""]
        for run in head["runs"]:
            lines += self._run_section(run, steps)
        lines += self._chain(steps, head["runs"], head.get("opened_by"))
        replies = [s for s in steps if s["step"] == "reply to owner" and (s.get("detail") or {}).get("text")]
        if replies:
            lines += ["## 指挥端给主人的回复", ""]
            for step in replies:
                lines += [f"{_local(step['at'])}", "", _fence(_mask(step["detail"]["text"])), ""]
        lines += self._timeline(steps)
        stamp = head.get("opened_at") or event.get("occurred_at")
        return self._save(stamp, f"{_slug(head['name'])}-{batch_id[:8]}", head["name"], verdict,
                          "\n".join(lines))

    def write_run(self, event: dict) -> Path | None:
        ref, run_id = event["session_ref"], event["run_id"]
        steps = self._steps(run_id=run_id)
        label = None
        finder = getattr(self.inbox, "label_of", None)
        if finder:
            label = finder(ref, run_id)
        label = label or event.get("project") or "一个任务"
        outcome = event["kind"].split(".", 1)[1]
        verdict = OUTCOME.get(outcome, outcome)
        lines = [f"# {_mask(label)} · {verdict}", "",
                 f"- 单个任务（不在批次里）· 结束 {_local(event.get('occurred_at'))}（北京时间）"]
        request = self._text(steps, "owner request")
        if request:
            lines += ["", "## 主人的原话", "", _fence(_mask(request))]
        lines += ["", "## 设备", ""]
        lines += self._run_section({"session_ref": ref, "run_id": run_id, "outcome": outcome},
                                   steps)
        by = next(((s.get("detail") or {}).get("by") for s in steps
                   if s.get("source") == "commander" and (s.get("detail") or {}).get("by")), None)
        lines += self._chain(steps, [{"session_ref": ref, "run_id": run_id}], by)
        lines += self._timeline(steps)
        return self._save(event.get("occurred_at"), f"{_slug(label)}-{run_id[:8]}", label, verdict,
                          "\n".join(lines))

    def _save(self, stamp: str | None, stem: str, title: str, verdict: str, body: str) -> Path:
        try:
            at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00")).astimezone(CN)
        except ValueError:
            at = datetime.now(CN)
        day = self.root / "runs" / f"{at:%Y}" / f"{at:%m}" / f"{at:%d}"
        day.mkdir(parents=True, exist_ok=True)
        path = day / f"{at:%H%M}-{stem}.md"
        path.write_text(body.rstrip() + "\n", encoding="utf-8")
        index = day / "INDEX.md"
        entry = f"- {at:%H:%M} [{_mask(title)}]({path.name}) · {verdict}\n"
        existing = index.read_text(encoding="utf-8") if index.exists() else f"# {at:%Y-%m-%d}\n\n"
        if entry not in existing:
            index.write_text(existing + entry, encoding="utf-8")
        readme = self.root / "README.md"
        if not readme.exists():
            readme.write_text(README, encoding="utf-8")
        self._pending += 1
        return path

    # -- the owner's conversation with the butler ---------------------------------

    def note_chat(self, record: dict, now: datetime | None = None) -> Path:
        """One exchange in Feishu, appended to the day's `chat/YYYY/MM/DD.md`:
        what the owner asked, how it was understood, what the butler said.
        Called from the message listener's thread; pushed with the next flush."""
        at = (now or datetime.now(timezone.utc)).astimezone(CN)
        day = self.root / "chat" / f"{at:%Y}" / f"{at:%m}"
        path = day / f"{at:%d}.md"
        answer = record.get("command") or "—"
        if record.get("argument"):
            answer += f" {record['argument']}"
        lines = [f"## {at:%H:%M:%S} {_mask(record.get('asked'))}", "",
                 f"- 理解方式：{record.get('understood_by') or '—'}　回答：{_mask(answer)}"
                 f"　用时：{record.get('total_seconds', '—')} 秒（想了 {record.get('seconds', '—')} 秒）"
                 f"　发送：{record.get('sent') or '—'}"]
        if record.get("model_problem"):
            lines.append(f"- 模型没答上来：{_mask(record['model_problem'])}")
        hops = [(word, record.get(key)) for word, key in
                (("主人发出", "owner_sent_at"), ("管家收到", "received_at"), ("回复发出", "answered_at"))]
        hops = [(word, at) for word, at in hops if at]
        if hops:
            lines.append("- 时间：" + " → ".join(f"{word} {_local(at)[11:]}" for word, at in hops))
        if record.get("said"):
            lines += ["", _mask(record["said"])]
        with self._chat_lock:
            day.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"# {at:%Y-%m-%d} 与管家的对话\n\n", encoding="utf-8")
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n\n")
            self._pending += 1
        return path

    # -- the machines' own state ------------------------------------------------
    #
    # Owner, 2026-11-12: «硬件信息这些也存入日志，方便排查网络质量、内存、硬盘».
    # Every RESOURCE_EVERY a snapshot of every device — each reading as the
    # card would say it, the network paths and how fast the hub reads it — and
    # at once whenever a device's level changes, so a night that went wrong
    # can be read back minute by minute. Readings only: no host, no address.

    RESOURCE_EVERY = 600.0

    def note_resources(self, views: list[dict], now: datetime) -> Path | None:
        # A change is a device's level, or which of its readings need a look —
        # not a reading appearing or a number moving, which every sweep does.
        state = tuple(sorted((str(v.get("device_ref")), str(v.get("level")),
                              tuple(sorted(str(r.get("key")) for r in v.get("readings") or []
                                           if r.get("level") in ("WARN", "ALERT"))))
                             for v in views))
        changed = state != getattr(self, "_resource_state", None)
        if not views or (not changed and self.clock() - getattr(self, "_resource_at", 0.0)
                         < self.RESOURCE_EVERY):
            return None
        first = not hasattr(self, "_resource_state")
        self._resource_state, self._resource_at = state, self.clock()
        at = now.astimezone(CN)
        day = self.root / "resources" / f"{at:%Y}" / f"{at:%m}"
        path = day / f"{at:%d}.md"
        why = "定时" if first or not changed else "有变化"
        lines = [f"## {at:%H:%M:%S}（{why}）", ""]
        for view in views:
            readings = view.get("readings") or []
            said = []
            for r in readings:
                text = f"{r.get('label')} {r.get('text')}"
                if r.get("level") in ("WARN", "ALERT"):
                    text = f"**{text}**（{WORD.get(r.get('level'), r.get('level'))}）"
                said.append(text)
            lines.append(f"- **{view.get('name') or view.get('device_ref')}**　"
                         f"{WORD.get(view.get('level'), '无法确认')}　" + "；".join(_mask(x) for x in said))
        with self._chat_lock:
            day.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"# {at:%Y-%m-%d} 设备状态（资源、网络、东京读它）\n\n",
                                encoding="utf-8")
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n\n")
            self._pending += 1
        return path

    # -- the owner's conversation with the commander -------------------------------
    #
    # Two kinds of commander (Owner, 2026-11-11): a **chat** — ChatGPT, Grok,
    # Claude on the web — where the owner talks and the commander only
    # dispatches; and an **agent** — Claude Code, Codex — that also works on a
    # machine of its own. Each goes to its own folder, named by the client.

    COMMANDER_STEPS = ("owner request", "reply to owner")

    #: Words in a client's registered name, and what kind of commander it is.
    CHAT_WORDS = ("chatgpt", "openai", "grok", "xai", "gemini", "claude.ai", "claude web",
                  "deepseek", "kimi", "doubao", "qwen", "chat")
    AGENT_WORDS = ("claude code", "claude-code", "claudecode", "codex", "cursor", "cline",
                   "windsurf", "copilot", "agent", "local")

    @classmethod
    def commander_kind(cls, client: str | None, by: str | None = None) -> tuple[str, str]:
        """(kind, name) for whoever made a commander call: kind is chat,
        agent or other; name is what to call it on the page."""
        name = (client or "").strip()
        low = name.lower()
        if low == "local" or by == "local-owner":
            return "agent", "本机 agent（Claude Code / Codex）"
        if any(w in low for w in cls.AGENT_WORDS):
            return "agent", name
        if any(w in low for w in cls.CHAT_WORDS):
            return "chat", name
        return "other", name or (by or "未知指挥端")

    def _state_path(self) -> Path:
        # Inside .git: never committed, and it travels with the clone it describes.
        base = self.root / ".git" if (self.root / ".git").is_dir() else self.root / ".journal-state"
        return base / "fleet-commander.json"

    def _commander_state(self) -> dict:
        path = self._state_path()
        try:
            return __import__("json").loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"after": None, "seen": []}

    def note_commander(self, now: datetime | None = None) -> int:
        """Both sides of the owner's conversation with the commander (GPT), from
        the trace: what the owner asked and what the commander answered, a page
        a day at `commander/YYYY/MM/DD.md`. A reply about a batch that is
        already written rewrites that batch's file so it carries the reply."""
        import hashlib
        import json as _json
        try:
            steps = self.inbox.read_trace(minutes=24 * 60, limit=2000)
        except Exception:  # noqa: BLE001
            return 0
        state = self._commander_state()
        seen = set(state.get("seen") or [])
        first = state.get("after") is None
        floor = (min(self._born, now or datetime.now(timezone.utc)) - FIRST_RUN_WINDOW)
        written, batches = 0, set()
        for step in steps:
            if step.get("source") != "commander" or step["step"] not in self.COMMANDER_STEPS:
                continue
            text = (step.get("detail") or {}).get("text")
            if not text:
                continue
            mark = hashlib.sha256(f"{step['at']}|{step['step']}|{text}".encode()).hexdigest()[:20]
            if mark in seen:
                continue
            try:
                at = datetime.fromisoformat(step["at"].replace("Z", "+00:00"))
            except ValueError:
                continue
            seen.add(mark)
            if first and at < floor:
                continue
            local = at.astimezone(CN)
            detail = step.get("detail") or {}
            kind, client = self.commander_kind(detail.get("client"), detail.get("by"))
            day = self.root / "commander" / kind / f"{local:%Y}" / f"{local:%m}"
            path = day / f"{local:%d}.md"
            batch_id = step.get("batch_id")
            name = ""
            if batch_id:
                try:
                    head = self.inbox.batch(batch_id)
                    name = f" · 批次「{_mask(head['name'])}」"
                    if step["step"] == "reply to owner" and head.get("closed"):
                        batches.add(batch_id)
                except Exception:  # noqa: BLE001
                    pass
            who = (f"主人 → {client}" if step["step"] == "owner request" else f"{client} → 主人")
            with self._chat_lock:
                day.mkdir(parents=True, exist_ok=True)
                if not path.exists():
                    path.write_text(f"# {local:%Y-%m-%d} 主人与{KIND_WORDS[kind]}指挥端的对话\n\n",
                                    encoding="utf-8")
                with path.open("a", encoding="utf-8") as handle:
                    handle.write(f"## {local:%H:%M:%S} {who}{name}\n\n{_fence(_mask(text))}\n\n")
                self._pending += 1
            written += 1
        for batch_id in batches:
            try:
                self.write_batch(batch_id, {})
            except Exception as exc:  # noqa: BLE001
                print(f"journal: batch {batch_id[:8]} not rewritten ({type(exc).__name__})", file=sys.stderr)
        path = self._state_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps(
            {"after": datetime.now(timezone.utc).isoformat(timespec="seconds"),
             "seen": sorted(seen)[-2000:]}), encoding="utf-8")
        return written

    # -- an agent commander's whole conversation, from its own session --------------

    def _agent_state_path(self):
        return self._state_path().with_name("fleet-agent-commander.json")

    def note_agent(self, session_ref: str, who: str, entries: list[dict],
                   now: datetime | None = None) -> int:
        """Every owner message and agent answer in an agent commander's session
        not written before, into `commander/agent/YYYY/MM/DD.md` (Owner,
        2026-11-11: «完整记录»). Tool calls are left out: runs/ holds the work."""
        import json as _json
        from .commander_log import mark
        path = self._agent_state_path()
        try:
            seen = set(_json.loads(path.read_text(encoding="utf-8")).get("seen") or [])
        except (OSError, ValueError):
            seen = set()
        floor = min(self._born, now or datetime.now(timezone.utc)) - FIRST_RUN_WINDOW
        written = 0
        for entry in entries:
            if entry.get("who") not in ("owner", "agent") or not entry.get("text"):
                continue
            key = mark(session_ref, entry)
            if key in seen:
                continue
            seen.add(key)
            try:
                at = datetime.fromisoformat(str(entry.get("at")).replace("Z", "+00:00"))
                if at.tzinfo is None:
                    at = at.replace(tzinfo=timezone.utc)
            except ValueError:
                at = now or datetime.now(timezone.utc)
            if at < floor:
                continue
            local = at.astimezone(CN)
            day = self.root / "commander" / "agent" / f"{local:%Y}" / f"{local:%m}"
            page = day / f"{local:%d}.md"
            head = f"主人 → {who}" if entry["who"] == "owner" else f"{who} → 主人"
            with self._chat_lock:
                day.mkdir(parents=True, exist_ok=True)
                if not page.exists():
                    page.write_text(f"# {local:%Y-%m-%d} 主人与{KIND_WORDS['agent']}指挥端的对话\n\n",
                                    encoding="utf-8")
                with page.open("a", encoding="utf-8") as handle:
                    handle.write(f"## {local:%H:%M:%S} {head}\n\n"
                                 f"<sub>会话 `{session_ref}`</sub>\n\n{_fence(_mask(entry['text']))}\n\n")
                self._pending += 1
            written += 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(_json.dumps({"seen": sorted(seen)[-20000:]}), encoding="utf-8")
        return written

    # -- the cards that were sent ---------------------------------------------------

    def note_card(self, card: dict, *, message_id: str, how: str = "发送",
                  key: str | None = None, now: datetime | None = None) -> Path:
        """Every card delivered to Feishu, as text, appended to the day's
        `cards/YYYY/MM/DD.md` — so what the owner was shown can be read later,
        not just that a card went out."""
        from .cards import card_text
        at = (now or datetime.now(timezone.utc)).astimezone(CN)
        day = self.root / "cards" / f"{at:%Y}" / f"{at:%m}"
        path = day / f"{at:%d}.md"
        text = _mask(card_text(card))
        title, _, rest = text.partition("\n")
        lines = [f"## {at:%H:%M:%S} {how} · {title}", "",
                 f"<sub>message {message_id}" + (f" · key {key[:40]}" if key else "") + "</sub>", "",
                 "```text", rest.replace("```", "ˋˋˋ") or "（空）", "```"]
        with self._chat_lock:
            day.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                path.write_text(f"# {at:%Y-%m-%d} 发出的飞书卡片\n\n", encoding="utf-8")
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n".join(lines) + "\n\n")
            self._pending += 1
        return path

    # -- the tick ---------------------------------------------------------------

    def tick(self, now: datetime) -> dict[str, Any]:
        page = self.inbox.read(SUBSCRIBER, limit=100)
        written, handled_to = 0, page["cursor"]
        oldest = min(self._born, now) - FIRST_RUN_WINDOW
        for event in page["events"]:
            handled_to = event["seq"]
            if event.get("occurred_at"):
                try:
                    if datetime.fromisoformat(event["occurred_at"].replace("Z", "+00:00")) < oldest:
                        continue
                except ValueError:
                    pass
            try:
                if event["kind"] == "batch.completed":
                    batch_id = str(event.get("work_ref") or "").removeprefix("batch:")
                    if batch_id and self.write_batch(batch_id, event):
                        written += 1
                elif event["kind"] in RUN_KINDS:
                    ref, run_id = event.get("session_ref"), event.get("run_id")
                    in_batch = self.inbox.batch_of(ref, run_id) is not None
                    dispatched = self.inbox.was_dispatched(ref, run_id)
                    if ref and run_id and dispatched and not in_batch and self.write_run(event):
                        written += 1
            except Exception as exc:  # noqa: BLE001 - one bad record does not stop the rest
                print(f"journal: {event.get('kind')} not written ({type(exc).__name__})",
                      file=sys.stderr)
        if handled_to > page["cursor"]:
            self.inbox.ack(SUBSCRIBER, handled_to)
        talked = 0
        try:
            talked = self.note_commander(now)
        except Exception as exc:  # noqa: BLE001 - the rest of the journal still goes out
            print(f"journal: commander not written ({type(exc).__name__})", file=sys.stderr)
        agent_talk = 0
        agents = getattr(self, "agents", None)
        if agents is not None and agents.due():
            try:
                was = getattr(self.inbox, "session_was_dispatched_to", lambda _ref: False)
                for ref, who, entries in agents.scan(dispatched=was):
                    agent_talk += self.note_agent(ref, who, entries, now)
            except Exception as exc:  # noqa: BLE001 - the rest of the journal still goes out
                print(f"journal: agent commanders not read ({type(exc).__name__})", file=sys.stderr)
        pushed = self.flush()
        out = {"written": written, "pushed": pushed}
        if talked:
            out["commander"] = talked
        if agent_talk:
            out["agent"] = agent_talk
        return out

    # -- git ------------------------------------------------------------------

    def _run_git(self, *args: str) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        if self.ssh_key:
            env["GIT_SSH_COMMAND"] = (f"ssh -i {self.ssh_key} -o IdentitiesOnly=yes "
                                      "-o StrictHostKeyChecking=accept-new")
        return subprocess.run(["git", *args], cwd=self.root, env=env, capture_output=True,
                              text=True, timeout=120)

    def flush(self, *, force: bool = False) -> bool:
        """Commit what was written and push, at most every `push_every` seconds."""
        if not (self.root / ".git").exists():
            return False
        if not force and self.clock() - self._last_push < self.push_every:
            return False
        status = self._git("status", "--porcelain")
        if status.returncode != 0:
            return False
        if not status.stdout.strip() and self._git("rev-parse", "--verify", "-q", "HEAD").returncode != 0:
            return False                    # nothing written yet: nothing to push
        if status.stdout.strip():
            self._git("add", "-A")
            done = self._git("-c", "user.name=fleet-hub", "-c", "user.email=fleet-hub@localhost",
                             "commit", "-q", "-m", f"fleet: {self._pending or 1} 条记录")
            if done.returncode != 0:
                return False
            self._pending = 0
        ahead = self._git("rev-list", "--count", "@{u}..HEAD")
        if ahead.returncode == 0 and ahead.stdout.strip() == "0":
            self._last_push = self.clock()
            return False
        upstream = self._git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
        if ahead.returncode == 0 and upstream.returncode == 0 and "/" in upstream.stdout:
            # Explicitly to the tracked branch: the local branch may be named
            # differently (an empty clone's `master` tracking `origin/main`).
            remote, _, branch = upstream.stdout.strip().partition("/")
            pushed = self._git("push", "-q", remote, f"HEAD:{branch}")
        else:
            # No upstream yet: the repository was empty when it was cloned. The
            # first push creates `main` there and tracks it from then on.
            pushed = self._git("push", "-q", "-u", "origin", "HEAD:main")
        if pushed.returncode != 0:
            print("journal: push failed, kept locally; retried next time", file=sys.stderr)
            self._last_push = self.clock()          # the next try waits the same interval
            return False
        self._last_push = self.clock()
        return True
