"""The hub monitor's control surface: what Fleet may read about it and ask of it.

The resident watcher (`integrations/fleet-hub-watch`) runs beside the hub inbox.
This module is the one place both sides meet:

* **status** — the watcher's heartbeat (last tick, devices, card spec state),
  the alert episodes still open, the mutes in force, whether broadcasting is
  paused, and the last commands with their results;
* **commands** — a small, closed set of verbs Fleet may queue. The watcher
  applies them on its next tick and records a result; nothing here acts on a
  machine. Every verb changes only what the watcher SAYS (or when), never what
  it observes or what any device does.

| verb | args | effect on the watcher |
|---|---|---|
| `mute` | `subject` (device ref or episode), `minutes` 1–1440, `reason` | no new message or patch for that target until it expires; the event is still recorded and counted in the 日报 |
| `unmute` | `subject` | ends a mute early |
| `pause_broadcast` | `minutes` 1–1440, `reason` | nothing is sent or patched; events keep being recorded |
| `resume_broadcast` | — | ends a pause early |
| `send_daily_now` | — | builds and sends today's 日报 now (once per day, like the scheduled one) |
| `reload_spec` | — | re-reads `card-spec.yaml` on the next tick |

The store is SQLite next to the inbox, owner-only (0600), like the inbox.
"""
from __future__ import annotations

import json
import os
import sqlite3
import stat
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator

CONTROL_FILE = "monitor-control.sqlite"
HEARTBEAT_FILE = "watch.heartbeat.json"
NOTIFY_FILE = "notify.sqlite"

VERBS = ("mute", "unmute", "pause_broadcast", "resume_broadcast", "send_daily_now", "reload_spec")
MAX_MINUTES = 24 * 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    id         TEXT PRIMARY KEY,
    queued_at  TEXT NOT NULL,
    queued_by  TEXT NOT NULL,
    verb       TEXT NOT NULL,
    args       TEXT NOT NULL,
    state      TEXT NOT NULL DEFAULT 'QUEUED',
    applied_at TEXT,
    result     TEXT
);
CREATE TABLE IF NOT EXISTS mutes (
    target     TEXT PRIMARY KEY,
    until      TEXT NOT NULL,
    reason     TEXT NOT NULL,
    by         TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL
);
"""


class ControlError(ValueError):
    """A command this monitor will not queue, with the reason."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def _private(path: Path) -> Path:
    if path.is_symlink():
        raise ControlError(f"refusing a symlinked control file: {path.name}")
    if not path.exists():
        os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600))
    elif stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ControlError(f"control file is readable by others: {path.name}")
    return path


def validate(verb: Any, args: dict[str, Any]) -> dict[str, Any]:
    """The command's arguments, checked; raises ControlError by name."""
    if verb not in VERBS:
        raise ControlError(f"`verb` must be one of {list(VERBS)}")
    if not isinstance(args, dict):
        raise ControlError("`args` must be an object")
    clean: dict[str, Any] = {}
    if verb in ("mute", "unmute"):
        subject = args.get("subject")
        if not isinstance(subject, str) or not subject.strip() or len(subject) > 300:
            raise ControlError("`subject` must name a device ref or an episode")
        clean["target"] = subject.strip()
    if verb in ("mute", "pause_broadcast"):
        minutes = args.get("minutes")
        if not isinstance(minutes, int) or isinstance(minutes, bool) or not 1 <= minutes <= MAX_MINUTES:
            raise ControlError(f"`minutes` must be an integer 1-{MAX_MINUTES}")
        reason = args.get("reason")
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 500:
            raise ControlError("`reason` is required (why the owner is not to be told)")
        clean["minutes"] = minutes
        clean["reason"] = reason.strip()
    unexpected = set(args) - {"subject", "minutes", "reason"}
    if unexpected:
        raise ControlError(f"unexpected argument(s) {sorted(unexpected)}")
    return clean


class MonitorControl:
    """The control store in `directory` (the hub inbox's directory)."""

    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)
        self.path = _private(self.directory / CONTROL_FILE)
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

    # -- Fleet side -----------------------------------------------------------

    def queue(self, verb: Any, args: dict[str, Any], *, by: str,
              now: datetime | None = None) -> dict[str, Any]:
        clean = validate(verb, args)
        command = {"id": f"cmd-{uuid.uuid4().hex[:12]}", "queued_at": _iso(now or _now()),
                   "queued_by": by, "verb": verb, "args": clean, "state": "QUEUED"}
        with self._connect() as conn:
            conn.execute("INSERT INTO commands(id, queued_at, queued_by, verb, args) VALUES (?,?,?,?,?)",
                         (command["id"], command["queued_at"], by, verb,
                          json.dumps(clean, ensure_ascii=False)))
        return command

    def status(self, *, now: datetime | None = None, recent: int = 10) -> dict[str, Any]:
        now = now or _now()
        heartbeat = self._heartbeat(now)
        with self._connect() as conn:
            mutes = [dict(r) for r in conn.execute(
                "SELECT target, until, reason, by FROM mutes WHERE until > ? ORDER BY until",
                (_iso(now),))]
            commands = [self._command_row(r) for r in conn.execute(
                "SELECT * FROM commands ORDER BY queued_at DESC LIMIT ?", (recent,))]
        return {"schema": "serveros/fleet-monitor-status.v1", "read_at": _iso(now),
                "watcher": heartbeat, "broadcast": self.broadcast_state(now),
                "open_episodes": self._open_episodes(), "mutes": mutes,
                "recent_commands": commands}

    def _heartbeat(self, now: datetime) -> dict[str, Any]:
        path = self.directory / HEARTBEAT_FILE
        try:
            beat = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {"state": "NO_HEARTBEAT",
                    "detail": "the watcher has not written a heartbeat here"}
        last = beat.get("last_tick_at")
        age = (now.timestamp() - float(last)) if isinstance(last, (int, float)) else None
        state = ("UNKNOWN" if age is None else "RUNNING" if age <= 180 else "STALE")
        return {"state": state, "age_seconds": None if age is None else round(age),
                "ok": beat.get("ok"), "error": beat.get("error"),
                "devices": beat.get("devices"), "card_spec": beat.get("card_spec"),
                "notify": beat.get("notify"), "watching": beat.get("watching")}

    def _open_episodes(self) -> list[dict[str, Any]]:
        path = self.directory / NOTIFY_FILE
        if not path.exists():
            return []
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT episode, body FROM episodes WHERE state='OPEN'").fetchall()
        except sqlite3.Error:
            return []
        finally:
            conn.close()
        out = []
        for row in rows:
            body = json.loads(row["body"])
            out.append({"episode": row["episode"], "level": body.get("level"),
                        "subject": body.get("subject"), "text": body.get("text"),
                        "first_seen": body.get("first_seen"), "device_ref": body.get("device_ref")})
        return out

    @staticmethod
    def _command_row(row: sqlite3.Row) -> dict[str, Any]:
        out = dict(row)
        out["args"] = json.loads(out["args"])
        if out.get("result"):
            out["result"] = json.loads(out["result"])
        return out

    # -- watcher side ---------------------------------------------------------

    def broadcast_state(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or _now()
        with self._connect() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key='paused'").fetchone()
        if row is None:
            return {"state": "ON"}
        paused = json.loads(row["value"])
        if _parse(paused["until"]) <= now:
            return {"state": "ON"}
        return {"state": "PAUSED", **paused}

    def muted(self, keys: list[str | None], now: datetime | None = None) -> bool:
        """True when any of `keys` (device ref, episode) is muted now."""
        wanted = [k for k in keys if k]
        if not wanted:
            return False
        now = now or _now()
        with self._connect() as conn:
            row = conn.execute(
                f"SELECT 1 FROM mutes WHERE until > ? AND target IN ({','.join('?' * len(wanted))})",
                (_iso(now), *wanted)).fetchone()
        return row is not None

    def pending(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            return [self._command_row(r) for r in conn.execute(
                "SELECT * FROM commands WHERE state='QUEUED' ORDER BY queued_at")]

    def apply(self, command: dict[str, Any], *, now: datetime | None = None,
              daily: Any = None, reload_spec: Any = None) -> dict[str, Any]:
        """Apply one queued command; `daily` / `reload_spec` are the watcher's hooks."""
        now = now or _now()
        verb, args = command["verb"], command["args"]
        with self._connect() as conn:
            if verb == "mute":
                until = _iso(now + timedelta(minutes=args["minutes"]))
                conn.execute("INSERT OR REPLACE INTO mutes(target, until, reason, by) VALUES (?,?,?,?)",
                             (args["target"], until, args["reason"], command["queued_by"]))
                result = {"muted_until": until}
            elif verb == "unmute":
                gone = conn.execute("DELETE FROM mutes WHERE target=?", (args["target"],)).rowcount
                result = {"removed": gone}
            elif verb == "pause_broadcast":
                until = _iso(now + timedelta(minutes=args["minutes"]))
                conn.execute("INSERT OR REPLACE INTO settings(key, value) VALUES ('paused', ?)",
                             (json.dumps({"until": until, "reason": args["reason"],
                                          "by": command["queued_by"]}, ensure_ascii=False),))
                result = {"paused_until": until}
            elif verb == "resume_broadcast":
                conn.execute("DELETE FROM settings WHERE key='paused'")
                result = {"state": "ON"}
            elif verb == "send_daily_now":
                result = {"daily": daily() if callable(daily) else "NOT_AVAILABLE"}
            else:  # reload_spec
                result = {"card_spec": reload_spec() if callable(reload_spec) else "NOT_AVAILABLE"}
            conn.execute("UPDATE commands SET state='APPLIED', applied_at=?, result=? WHERE id=?",
                         (_iso(now), json.dumps(result, ensure_ascii=False), command["id"]))
        return result
