"""The hub watcher: find runs on every bound runtime, classify them, record once.

Design: `docs/plans/fleet-v1-resident-monitor-and-tokyo-hub.md` §3 (M2).

One tick does two things:

1.  **discover** — list sessions on every enrolled device; for each session the
    provider reports as running, read its detail and take the current run id.
    A run not yet watched is added with its discovery time.
2.  **observe** — every watched run whose check is due is classified by the
    Agent Monitor's own classifier (`LegacyFactsSource.observe_run`) over a
    FleetService-backed client. A terminal answer becomes one inbox event and
    the watch is dropped; anything still moving is checked again later, on a
    10s → 30s → 2min → 10min backoff, for at most `max_age`.

Nothing here writes to a device. The principal carries `fleet:read` only, and
the only file written besides the watch list is the hub's own inbox.

Known gap, stated rather than hidden: a run that starts and finishes between two
discovery passes is never seen running, so it is never watched. A shorter
discovery interval narrows it; it does not close it.
"""
from __future__ import annotations

import json
import re
import sys

import os
import sqlite3
import stat
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator

from fleet_mcp.errors import FleetRefusal
from fleet_mcp.identity import SessionKey
from fleet_next_agent_monitor.contract import (
    STATUS_DENIED, STATUS_FAILED, STATUS_TERMINAL_VERIFIED, STATUS_UNKNOWN,
)
from fleet_next_agent_monitor.sources import Grant, LegacyFactsSource

from .facts import FleetFactsClient

BACKOFF_SECONDS = (10, 30, 120, 600)
#: A run read right after it started can say `completed` with no activity
#: record yet (serveros#531). Inside this window that is "not readable yet",
#: not a verdict; only after it does the run become 无法确认.
UNREADABLE_GRACE_SECONDS = 15 * 60
DEFAULT_MAX_AGE = 24 * 3600
DEFAULT_STALL_AFTER = 2 * 3600

_SCHEMA = """
CREATE TABLE IF NOT EXISTS watches (
    session_ref     TEXT NOT NULL,
    run_id          TEXT NOT NULL,
    device_ref      TEXT NOT NULL,
    runtime         TEXT,
    project         TEXT,
    discovered_at   REAL NOT NULL,
    next_check_at   REAL NOT NULL,
    checks          INTEGER NOT NULL DEFAULT 0,
    stall_reported  INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (session_ref, run_id)
);
-- A run whose outcome is recorded. A session can keep reporting the same run
-- as current after it ends (or while it is stuck past the window); without
-- this, discovery would add it straight back.
CREATE TABLE IF NOT EXISTS finished (
    session_ref TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    finished_at REAL NOT NULL,
    PRIMARY KEY (session_ref, run_id)
);
"""

FINISHED_RETENTION = 7 * 24 * 3600


class WatchStore:
    """The runs being watched. Survives a restart; the inbox holds the outcomes."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        if not self.path.parent.is_dir() or self.path.parent.is_symlink():
            raise ValueError(f"watch directory is missing or unsafe: {self.path.parent}")
        if self.path.is_symlink():
            raise ValueError(f"watch file must not be a symlink: {self.path}")
        if not self.path.exists():
            os.close(os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
        elif stat.S_IMODE(self.path.stat().st_mode) & 0o077:
            raise ValueError(f"watch file is readable by others: {self.path}")
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def add(self, *, session_ref: str, run_id: str, device_ref: str, runtime: str | None,
            project: str | None, now: float) -> bool:
        with self._connect() as conn:
            if conn.execute("SELECT 1 FROM finished WHERE session_ref=? AND run_id=?",
                            (session_ref, run_id)).fetchone():
                return False
            cursor = conn.execute(
                "INSERT OR IGNORE INTO watches(session_ref, run_id, device_ref, runtime, "
                "project, discovered_at, next_check_at) VALUES (?,?,?,?,?,?,?)",
                (session_ref, run_id, device_ref, runtime, project, now, now))
            return cursor.rowcount == 1

    def due(self, now: float) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM watches WHERE next_check_at <= ? "
                                "ORDER BY next_check_at", (now,)).fetchall()
        return [dict(row) for row in rows]

    def all(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [dict(r) for r in conn.execute("SELECT * FROM watches").fetchall()]

    def reschedule(self, watch: dict[str, Any], now: float, *, stall_reported: bool) -> None:
        checks = watch["checks"] + 1
        delay = BACKOFF_SECONDS[min(checks - 1, len(BACKOFF_SECONDS) - 1)]
        with self._connect() as conn:
            conn.execute("UPDATE watches SET checks=?, next_check_at=?, stall_reported=? "
                         "WHERE session_ref=? AND run_id=?",
                         (checks, now + delay, int(stall_reported),
                          watch["session_ref"], watch["run_id"]))

    def drop(self, watch: dict[str, Any], now: float) -> None:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM watches WHERE session_ref=? AND run_id=?",
                         (watch["session_ref"], watch["run_id"]))
            conn.execute("INSERT OR REPLACE INTO finished(session_ref, run_id, finished_at) "
                         "VALUES (?,?,?)", (watch["session_ref"], watch["run_id"], now))
            conn.execute("DELETE FROM finished WHERE finished_at < ?",
                         (now - FINISHED_RETENTION,))
            conn.execute("COMMIT")


def _text(value: Any) -> str | None:
    if isinstance(value, str):
        return value.strip() or None
    if isinstance(value, dict):
        return _text(value.get("content") if value.get("content") is not None else value.get("text"))
    if isinstance(value, list):
        parts = [t for t in (_text(v) for v in value) if t]
        return " ".join(parts) or None
    return None


def shape(value: Any, depth: int = 4) -> Any:
    """The structure of a value — keys, list lengths, type names — and no content."""
    if depth <= 0:
        return type(value).__name__
    if isinstance(value, dict):
        return {str(k): shape(v, depth - 1) for k, v in list(value.items())[:12]}
    if isinstance(value, list):
        return [len(value)] + ([shape(value[-1], depth - 1)] if value else [])
    return type(value).__name__


#: A plain-text transcript (CloudCLI's session_read on some devices): one entry
#: per `[timestamp] label: text` or `[timestamp] tool Name({...})`.
_ENTRY = re.compile(r"^\[[^\]\n]{4,48}\]\s+(?P<label>[^\s:(]{1,24})(?P<sep>:|\s|\()?", re.M)
#: Labels that carry the agent's own words; everything else (thinking, tool,
#: user, system, results) is not an answer.
ANSWER_LABELS = {"assistant", "claude", "answer", "response", "reply",
                 "final", "助手", "回复", "回答"}
ASK_TOOLS = ("AskUserQuestion", "ask_user", "AskUser")


def text_entries(text: str) -> list[dict[str, str]]:
    """[{label, body}] from a plain-text transcript, in order; [] when it is not one."""
    marks = list(_ENTRY.finditer(text or ""))
    entries = []
    for i, mark in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(text)
        body = text[mark.end():end].strip()
        if mark.group("sep") == "(":
            body = "(" + body
        entries.append({"label": mark.group("label"), "body": body})
    return entries


def _plain_answer(text: str) -> str | None:
    for entry in reversed(text_entries(text)):
        if entry["label"].lower() in ANSWER_LABELS and entry["body"]:
            return entry["body"]
    return None


def last_assistant_text(transcript: Any) -> str | None:
    """The last assistant reply in a transcript, whatever list the provider put
    its messages in. Used when a run reports `completed` with no activity record
    (CloudCLI without run receipts): the session itself still holds the answer."""
    if isinstance(transcript, str):
        # An MCP text block carrying the JSON answer, or a plain-text transcript.
        try:
            return last_assistant_text(json.loads(transcript))
        except ValueError:
            return _plain_answer(transcript)
    if isinstance(transcript, dict):
        # CloudCLI's session_read answers {"content": [...], "cursor", "mode"}.
        for key in ("content", "messages", "items", "entries", "turns", "transcript",
                    "data", "structuredContent"):
            if key in transcript:
                found = last_assistant_text(transcript[key])
                if found:
                    return found
        return None
    if not isinstance(transcript, list):
        return None
    for message in reversed(transcript):
        if not isinstance(message, dict):
            continue
        if message.get("type") == "text" and isinstance(message.get("text"), str) \
                and message["text"].lstrip().startswith(("{", "[")):
            found = last_assistant_text(message["text"])
            if found:
                return found
            continue
        inner = message.get("message") if isinstance(message.get("message"), dict) else {}
        role = message.get("role") or inner.get("role") or message.get("type")
        if role != "assistant":
            continue
        text = _text(message.get("content") if message.get("content") is not None
                     else inner.get("content") if inner.get("content") is not None
                     else message.get("text"))
        if text:
            return text
    return None


def _entries(transcript: Any) -> list[dict]:
    if isinstance(transcript, str):
        try:
            return _entries(json.loads(transcript))
        except ValueError:
            return []
    if isinstance(transcript, dict):
        for key in ("content", "messages", "items", "entries", "turns", "transcript", "data"):
            if key in transcript:
                found = _entries(transcript[key])
                if found:
                    return found
        return []
    if not isinstance(transcript, list):
        return []
    out = []
    for item in transcript:
        if isinstance(item, dict) and item.get("type") == "text" and isinstance(item.get("text"), str) \
                and item["text"].lstrip().startswith(("{", "[")):
            out += _entries(item["text"])
        elif isinstance(item, dict):
            out.append(item)
    return out


def _plain_text(transcript: Any) -> str | None:
    """The transcript's text when the provider sent it as one string, else None."""
    if isinstance(transcript, dict):
        transcript = transcript.get("content")
    if isinstance(transcript, str) and not transcript.lstrip().startswith(("{", "[{")):
        return transcript if text_entries(transcript) else None
    return None


#: How many entries to ask for when reading a reply: a device may cap the
#: whole read (CloudCLI at 4000 characters, from the oldest entry), so the
#: newest entry alone is tried first.
REPLY_LIMITS = (1, 3, 5)


def read_reply(service: Any, principal: Any, key: Any) -> str | None:
    """The session's last answer, read the way that survives a capped read."""
    for limit in REPLY_LIMITS:
        found = last_assistant_text(service.read_session(principal, key, limit=limit).get("transcript"))
        if found:
            return found
    return None


def work_summary(transcript: Any) -> dict[str, Any]:
    """What the device agent did, as metadata only: turns, tool calls by name,
    model, and whether it stopped to ask a person. Never text."""
    turns, tools, model, asking = 0, {}, None, False
    skills, mcp = [], []
    plain = _plain_text(transcript)
    for entry in text_entries(plain) if plain else []:
        label = entry["label"]
        if label.lower() in ("user", "human", "system"):
            continue
        turns += 1
        if label.lower() == "tool":
            name = re.match(r"[\w.:-]{1,60}", entry["body"])
            name = name.group()[:40] if name else "tool"
            tools[name] = tools.get(name, 0) + 1
            skill = re.search(r'"skill"\s*:\s*"([^"]{1,60})"', entry["body"]) if name == "Skill" else None
            if skill:
                skills.append(skill.group(1))
            if name.startswith("mcp__"):
                mcp.append(name[:60])
            asking = name in ASK_TOOLS
        elif label.lower() in ANSWER_LABELS:
            asking = False
    for entry in [] if plain else _entries(transcript):
        inner = entry.get("message") if isinstance(entry.get("message"), dict) else entry
        role = inner.get("role") or entry.get("type")
        if role != "assistant":
            continue
        turns += 1
        model = inner.get("model") or entry.get("model") or model
        content = inner.get("content")
        for block in content if isinstance(content, list) else []:
            if isinstance(block, dict) and block.get("type") == "tool_use":
                name = str(block.get("name") or "tool")[:40]
                given = block.get("input") if isinstance(block.get("input"), dict) else {}
                if name == "Skill" and isinstance(given.get("skill"), str):
                    skills.append(given["skill"][:60])
                if name.startswith("mcp__"):
                    mcp.append(name[:60])
                tools[name] = tools.get(name, 0) + 1
                asking = name in ASK_TOOLS
            elif isinstance(block, dict) and block.get("type") == "text":
                asking = False
    summary: dict[str, Any] = {"turns": turns}
    if tools:
        summary["tools"] = ", ".join(f"{k}×{v}" for k, v in sorted(tools.items(), key=lambda kv: -kv[1])[:8])
    if model:
        summary["model"] = str(model)[:60]
    if skills:
        summary["skills"] = ", ".join(dict.fromkeys(skills))
    if mcp:
        summary["mcp"] = ", ".join(dict.fromkeys(mcp))[:300]
    if asking:
        summary["waiting_for_person"] = True
    return summary


def _current_run_id(detail: Any) -> str | None:
    """The run id a session detail names as current, wherever the provider put it."""
    if not isinstance(detail, dict):
        return None
    for holder in (detail, detail.get("session") if isinstance(detail.get("session"), dict) else {}):
        run = holder.get("current_run") or holder.get("currentRun") or holder.get("run")
        if isinstance(run, dict):
            value = run.get("run_id") or run.get("runId") or run.get("id")
            if isinstance(value, str) and value.strip():
                return value.strip()
    return None


class Watcher:
    def __init__(self, service: Any, principal: Any, inbox: Any, store: WatchStore, *,
                 instance_id: str = "tokyo-hub",
                 clock: Callable[[], float] = time.time,
                 max_age: float = DEFAULT_MAX_AGE,
                 stall_after: float = DEFAULT_STALL_AFTER,
                 session_limit: int = 50) -> None:
        self.service = service
        self.principal = principal
        self.inbox = inbox
        self.store = store
        self.client = FleetFactsClient(service, principal, instance_id=instance_id)
        self.clock = clock
        self.max_age = max_age
        self.stall_after = stall_after
        self.session_limit = session_limit

    # -- discovery -----------------------------------------------------------

    def discover(self) -> dict[str, Any]:
        now = self.clock()
        added, unreachable, unreadable = [], [], []
        for entry in self.service.registry.all():
            device = entry.device_ref.ref
            # V1 covers company devices only; a personal device is out of scope
            # by rule, the same rule that keeps it out of every grant.
            if getattr(entry, "ownership", None) != "company":
                continue
            try:
                # Every runtime this device is bound for, not only the one it
                # serves its own account on. A machine whose second OS account
                # runs the sessions is exactly the machine this loop is for, and
                # asking for the default runtime answered it with a named gap
                # ("this runtime holds no route to it") that read like an outage.
                listing = self.service.list_sessions(self.principal, device=device,
                                                     limit=self.session_limit,
                                                     runtime="all")
            except FleetRefusal as exc:
                unreachable.append({"device_ref": device, "code": exc.code})
                continue
            unreachable.extend(listing.get("devices_unreachable") or [])
            for row in listing.get("sessions") or []:
                if row.get("running") is not True:
                    continue
                key = SessionKey.parse(row["session_ref"])
                try:
                    detail = self.service.get_session(self.principal, key)
                except FleetRefusal as exc:
                    unreadable.append({"session_ref": key.ref, "code": exc.code})
                    continue
                run_id = _current_run_id(detail)
                if run_id is None:
                    unreadable.append({"session_ref": key.ref, "code": "NO_CURRENT_RUN"})
                    continue
                if self.store.add(session_ref=key.ref, run_id=run_id, device_ref=device,
                                  runtime=row.get("provider"), project=row.get("project_id"),
                                  now=now):
                    added.append({"session_ref": key.ref, "run_id": run_id})
        # Runs Fleet dispatched (and open batch members): watched even when they
        # ended before this tick could see them running.
        to_watch = getattr(self.inbox, "runs_to_watch", None)
        for row in to_watch() if to_watch else []:
            ref = row["session_ref"]
            parts = ref.split("|")
            if len(parts) >= 3 and self.store.add(session_ref=ref, run_id=row["run_id"],
                                                  device_ref=parts[0], runtime=parts[1],
                                                  project=row.get("project"), now=now):
                added.append({"session_ref": ref, "run_id": row["run_id"]})
                self._trace("watching", session_ref=ref, run_id=row["run_id"],
                            origin="dispatched")
            self.inbox.forget_dispatch(ref, row["run_id"])
        return {"added": added, "unreachable": unreachable, "unreadable": unreadable}

    # -- observation ---------------------------------------------------------

    def _observe(self, watch: dict[str, Any]) -> dict[str, Any]:
        grant = Grant(principal=self.principal.name, devices=[watch["device_ref"]],
                      sessions=[watch["session_ref"]],
                      projects=[watch["project"]] if watch["project"] else [],
                      views=["run"])
        source = LegacyFactsSource(self.client, grant, max_readings=1)
        return source.observe_run(device_ref=watch["device_ref"],
                                  session_ref=watch["session_ref"],
                                  run_id=watch["run_id"], project=watch["project"])

    def _event(self, watch: dict[str, Any], kind: str, level: str, detail: str) -> dict[str, Any]:
        where = watch["project"] or "unassigned project"
        return {
            "kind": kind, "level": level,
            "device_ref": watch["device_ref"], "runtime": watch["runtime"],
            "session_ref": watch["session_ref"], "run_id": watch["run_id"],
            "project": watch["project"],
            # Identifiers and the classifier's verdict only: no transcript text.
            "summary": f"{where}: run {watch['run_id']} {detail}",
            "evidence": watch["session_ref"],
        }

    def _trace(self, step: str, **fields: Any) -> None:
        tracer = getattr(self.inbox, "trace", None)
        if tracer is not None:
            try:
                tracer("monitor", step, **fields)
            except Exception:  # noqa: BLE001 - the trace never stops the watch
                pass

    def _dispatched(self, watch: dict[str, Any]) -> bool:
        check = getattr(self.inbox, "was_dispatched", None)
        return bool(check and check(watch["session_ref"], watch["run_id"]))

    def _device_work(self, watch: dict[str, Any], *, final: bool) -> None:
        """Record what the device agent has done so far — once at the end, and
        while it runs only when it changed (every slower check)."""
        try:
            read = self.service.read_session(self.principal, SessionKey.parse(watch["session_ref"]),
                                             limit=100)
        except Exception:  # noqa: BLE001 - a session we cannot read has no summary
            return
        summary = work_summary(read.get("transcript"))
        if not summary.get("turns"):
            return
        key = (watch["session_ref"], watch["run_id"])
        seen = getattr(self, "_work_seen", {})
        self._work_seen = seen
        fingerprint = json.dumps(summary, sort_keys=True)
        if not final and seen.get(key) == fingerprint:
            return
        seen[key] = fingerprint
        step = ("device finished" if final else
                "device waiting for a person" if summary.get("waiting_for_person") else
                "device working")
        tracer = getattr(self.inbox, "trace", None)
        if tracer is not None:
            try:
                tracer("device", step, session_ref=watch["session_ref"], run_id=watch["run_id"],
                       **summary)
                if final:
                    answer = read_reply(self.service, self.principal,
                                        SessionKey.parse(watch["session_ref"]))
                    if answer:
                        tracer("device", "result", session_ref=watch["session_ref"],
                               run_id=watch["run_id"], text=answer)
            except Exception:  # noqa: BLE001
                pass

    def _answered(self, watch: dict[str, Any]) -> bool:
        key = SessionKey.parse(watch["session_ref"])
        try:
            if read_reply(self.service, self.principal, key) is not None:
                self._trace("reply read from the session", session_ref=watch["session_ref"],
                            run_id=watch["run_id"])
                return True
            read = self.service.read_session(self.principal, key, limit=1)
        except Exception:  # noqa: BLE001 - an unreadable session is not an answer
            return False
        # Keys only, never text: enough to see where a provider keeps its replies.
        found = shape(read.get("transcript"))
        plain = _plain_text(read.get("transcript"))
        if plain:
            # Entry labels are the transcript's own format words, not its text.
            found = {"plain_text": len(plain),
                     "labels": [e["label"][:24] for e in text_entries(plain)][-8:]}
        print(f"transcript without a readable reply: {found}", file=sys.stderr)
        self._trace("no reply in the session", session_ref=watch["session_ref"],
                    run_id=watch["run_id"], shape=found)
        return False

    def observe(self) -> dict[str, Any]:
        now = self.clock()
        recorded, still = [], 0
        for watch in self.store.due(now):
            observation = self._observe(watch)
            status, reason = observation["status"], observation.get("reason")
            age = now - watch["discovered_at"]
            self._trace("observed", session_ref=watch["session_ref"], run_id=watch["run_id"],
                        status=status, reason=reason, age_s=int(age))
            event = None
            if status == STATUS_TERMINAL_VERIFIED:
                event = self._event(watch, "run.completed", "notify",
                                    "completed with readable output")
            elif status == STATUS_FAILED:
                event = self._event(watch, "run.failed", "notify", f"failed ({reason})")
            elif status == STATUS_UNKNOWN and reason == "NO_NATIVE_ACTIVITY" and self._answered(watch):
                # No run receipt, but the session holds the reply: that is output.
                event = self._event(watch, "run.completed", "notify",
                                    "completed; reply read from the session")
            elif status == STATUS_UNKNOWN and reason == "NO_NATIVE_ACTIVITY" \
                    and not self._dispatched(watch):
                # A resident session's own turn, unreadable: not news. Owner,
                # 2026-11-09: these produced a 无法确认 card every few minutes.
                self._trace("dropped: a session's own turn, unreadable",
                            session_ref=watch["session_ref"], run_id=watch["run_id"])
                self.store.drop(watch, now)
                continue
            elif (status == STATUS_UNKNOWN and reason == "NO_NATIVE_ACTIVITY"
                  and age < UNREADABLE_GRACE_SECONDS):
                event = None                # not readable yet: look again later
            elif status == STATUS_DENIED or (status == STATUS_UNKNOWN
                                             and observation.get("retryable") is False):
                # Not a success and not a failure: the outcome could not be
                # confirmed, and a person decides what it means.
                event = self._event(watch, "run.unknown", "decide",
                                    f"outcome could not be confirmed ({reason})")
            elif age >= self.max_age:
                event = self._event(watch, "run.unknown", "decide",
                                    f"still {status} after the observation window ({reason})")
            if event is not None:
                if self._dispatched(watch):
                    self._device_work(watch, final=True)
                recorded.append(self.inbox.record(event))
                self.store.drop(watch, now)
                continue
            if watch["checks"] >= 2 and self._dispatched(watch):
                # Past the first quick checks: say what the device is doing.
                self._device_work(watch, final=False)
            stalled = bool(watch["stall_reported"])
            if not stalled and age >= self.stall_after:
                recorded.append(self.inbox.record(self._event(
                    watch, "run.stalled", "notify",
                    f"still {status} after {int(age // 60)} minutes")))
                stalled = True
            self.store.reschedule(watch, now, stall_reported=stalled)
            still += 1
        return {"recorded": [r.seq for r in recorded if r.created], "still_watching": still}

    def tick(self) -> dict[str, Any]:
        return {"discover": self.discover(), "observe": self.observe(),
                "watching": len(self.store.all())}
