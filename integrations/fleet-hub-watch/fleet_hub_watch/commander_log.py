"""The owner's whole conversation with an **agent** commander (Claude Code,
Codex), read from the agent's own session on a fleet device.

Owner, 2026-11-11: «我和指挥端能不能完整记录». A chat commander (ChatGPT,
Grok) only tells Fleet what it chooses to; an agent commander runs as a session
on one of the fleet's own devices, so the hub can read it the way it reads any
session: every message the owner typed and every answer the agent gave, in
order. A session is a commander when its transcript calls Fleet's own tools.
Tool calls themselves are not copied — the runs/ files hold what was
dispatched — only the words between the owner and the agent.

Nothing here writes to a device or a session. Reads are bounded: only sessions
active in the last `ACTIVE_WINDOW`, the newest `READ_LIMIT` entries, at most
every `EVERY` seconds.
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterator

EVERY = 300.0
ACTIVE_WINDOW = timedelta(minutes=30)
READ_LIMIT = 80

#: A tool call that is Fleet's own: what makes a session a commander.
FLEET_TOOL = re.compile(r"fleet_(?:send_to_session|create_session|batch|list_sessions|status|"
                        r"get_run|read_session|list_devices|inbox|trace|memory)")
_PLAIN = re.compile(r"^\[(?P<at>[^\]\n]{4,48})\]\s+(?P<label>[^\s:(]{1,24})(?P<sep>:|\s|\()?", re.M)
_OWNER_LABELS = {"user", "human", "owner", "主人"}
_AGENT_LABELS = {"assistant", "claude", "codex", "answer", "response", "reply", "助手", "回复"}


def _text_of(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text" and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(p for p in parts if p).strip()
    if isinstance(content, dict):
        return _text_of(content.get("content") if content.get("content") is not None else content.get("text"))
    return ""


def _tools_of(content: Any) -> list[str]:
    if not isinstance(content, list):
        return []
    return [str(b.get("name")) for b in content
            if isinstance(b, dict) and b.get("type") == "tool_use" and b.get("name")]


def _has_tool_result(content: Any) -> bool:
    return isinstance(content, list) and any(
        isinstance(b, dict) and b.get("type") == "tool_result" for b in content)


def exchange(transcript: Any) -> list[dict[str, Any]]:
    """[{at, who: owner|agent|tool, text, tools}] in order, from either
    transcript shape the providers answer with (entry lists, or one string)."""
    from .watch import _entries, _plain_text
    plain = _plain_text(transcript)
    out: list[dict[str, Any]] = []
    if plain:
        marks = list(_PLAIN.finditer(plain))
        for i, mark in enumerate(marks):
            end = marks[i + 1].start() if i + 1 < len(marks) else len(plain)
            body = plain[mark.end():end].strip()
            label = mark.group("label").lower()
            if label == "tool":
                name = re.match(r"[\w.:-]{1,80}", body)
                out.append({"at": mark.group("at"), "who": "tool", "text": "",
                            "tools": [name.group()] if name else []})
            elif label in _OWNER_LABELS:
                out.append({"at": mark.group("at"), "who": "owner", "text": body, "tools": []})
            elif label in _AGENT_LABELS:
                out.append({"at": mark.group("at"), "who": "agent", "text": body, "tools": []})
        return out
    for entry in _entries(transcript):
        inner = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        role = inner.get("role") or entry.get("type")
        content = inner.get("content") if inner.get("content") is not None else entry.get("text")
        at = str(entry.get("timestamp") or entry.get("at") or inner.get("timestamp") or "")
        if role == "assistant":
            tools = _tools_of(content)
            text = _text_of(content)
            if tools:
                out.append({"at": at, "who": "tool", "text": "", "tools": tools})
            if text:
                out.append({"at": at, "who": "agent", "text": text, "tools": []})
        elif role in ("user", "human") and not _has_tool_result(content):
            text = _text_of(content)
            if text:
                out.append({"at": at, "who": "owner", "text": text, "tools": []})
    return out


def is_commander(entries: list[dict[str, Any]]) -> bool:
    return any(FLEET_TOOL.search(name) for e in entries for name in e.get("tools") or [])


def agent_name(session_ref: str) -> str:
    runtime = (session_ref.split("|") + ["", ""])[1].lower()
    if "codex" in runtime:
        return "Codex"
    if "claude" in runtime:
        return "Claude Code"
    return runtime or "agent"


def _recent(row: dict[str, Any], now: datetime) -> bool:
    if row.get("running") is True:
        return True
    activity = row.get("last_activity")
    stamps = []
    if isinstance(activity, dict):
        stamps = [v for v in activity.values() if isinstance(v, str)]
    elif isinstance(activity, str):
        stamps = [activity]
    for stamp in stamps:
        try:
            at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)
        if now - at <= ACTIVE_WINDOW:
            return True
    return False


class AgentSessions:
    """Reads the fleet's active sessions as the hub's read-only principal and
    yields the ones that are agent commanders, with their exchange."""

    def __init__(self, service: Any, principal: Any, *, names: dict[str, str] | None = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.service, self.principal = service, principal
        self.names = names or {}
        self.clock = clock
        self._last = 0.0
        #: sessions found not to be a commander, and when: not read again for
        #: an hour (a session can start calling Fleet later).
        self._not_commander: dict[str, float] = {}

    def due(self) -> bool:
        return self.clock() - self._last >= EVERY

    def scan(self, *, dispatched: Callable[[str], bool] = lambda _ref: False
             ) -> Iterator[tuple[str, str, list[dict[str, Any]]]]:
        """(session_ref, who, entries) for each active agent-commander session."""
        from fleet_mcp.identity import SessionKey
        self._last = self.clock()
        now = datetime.now(timezone.utc)
        for entry in self.service.registry.all():
            if getattr(entry, "ownership", None) != "company":
                continue
            device = entry.device_ref.ref
            try:
                listing = self.service.list_sessions(self.principal, device=device, limit=20,
                                                     runtime="all")
            except Exception:  # noqa: BLE001 - a quiet device is not an error here
                continue
            for row in listing.get("sessions") or []:
                ref = row.get("session_ref")
                if not ref or not _recent(row, now) or dispatched(ref):
                    continue
                if self.clock() - self._not_commander.get(ref, -1e18) < 3600:
                    continue
                try:
                    read = self.service.read_session(self.principal, SessionKey.parse(ref),
                                                     limit=READ_LIMIT)
                except Exception:  # noqa: BLE001
                    continue
                entries = exchange(read.get("transcript"))
                if not is_commander(entries):
                    self._not_commander[ref] = self.clock()
                    continue
                where = self.names.get(device, device.removeprefix("device:"))
                yield ref, f"{agent_name(ref)}（{where}）", entries


def mark(session_ref: str, entry: dict[str, Any]) -> str:
    import hashlib
    raw = json.dumps([session_ref, entry.get("at"), entry.get("who"), entry.get("text")],
                     ensure_ascii=False)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:20]
