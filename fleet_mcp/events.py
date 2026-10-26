"""Durable derived Fleet events, delivery cursors and observer scheduling.

This database grants no authority. Callers supply current visibility at every
read; acknowledgement is bounded by ranges this subscriber actually scanned.
Only closed status summaries cross this boundary, never provider free text.
"""
from __future__ import annotations

from contextlib import contextmanager
import json
import math
from pathlib import Path
import sqlite3
from typing import Callable

from .identity import SessionKey
from .errors import FleetRefusal

KINDS = frozenset({"run.completed", "run.failed", "run.cancelled", "run.unknown", "run.corrected"})
LEVELS = frozenset({"auto", "notify", "decide"})
STATUSES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "UNKNOWN"})
_REQUIRED = {"event_id", "kind", "level", "device_ref", "runtime", "session_ref",
             "run_id", "dispatcher", "occurred_at", "summary"}
_OPTIONAL = {"project", "work_ref", "parent_ref", "agent_ref"}


def _text(value, name, maximum=512):
    if (not isinstance(value, str) or not value.strip() or len(value) > maximum
            or any(ord(c) < 32 for c in value)):
        raise ValueError(f"invalid {name}")
    return value


def _cursor(value):
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 2**63 - 1:
        raise ValueError("cursor must be a nonnegative integer")
    return value


def validate_event(event):
    if not isinstance(event, dict) or set(event) - _REQUIRED - _OPTIONAL or not _REQUIRED <= set(event):
        raise ValueError("event fields are closed")
    for name in (_REQUIRED | _OPTIONAL) - {"occurred_at", "summary"}:
        if name in event:
            _text(event[name], name)
    if event["kind"] not in KINDS or event["level"] not in LEVELS:
        raise ValueError("unknown event kind or level")
    summary = event["summary"]
    if (not isinstance(summary, dict) or set(summary) - {"status", "previous_status"}
            or not isinstance(summary.get("status"), str)
            or summary.get("status") not in STATUSES
            or ("previous_status" in summary and summary["previous_status"] != "UNKNOWN")):
        raise ValueError("summary accepts only closed run status codes")
    status = summary["status"]
    expected = "run." + status.lower()
    if event["kind"] == "run.corrected":
        if summary.get("previous_status") != "UNKNOWN" or status == "UNKNOWN":
            raise ValueError("correction requires a trusted terminal after UNKNOWN")
    elif event["kind"] != expected or "previous_status" in summary:
        raise ValueError("event kind contradicts summary")
    expected_level = "decide" if status in {"UNKNOWN", "FAILED"} else "notify"
    if event["level"] != expected_level:
        raise ValueError("event level contradicts status")
    at = event["occurred_at"]
    if isinstance(at, bool) or not isinstance(at, (int, float)) or not math.isfinite(at) or at < 0:
        raise ValueError("occurred_at must be a finite timestamp")
    try:
        key = SessionKey.parse(event["session_ref"])
    except FleetRefusal:
        raise ValueError("invalid event session reference") from None
    if key.device_ref != event["device_ref"] or key.provider != event["runtime"]:
        raise ValueError("event address contradicts its session")
    payload = json.dumps(event, sort_keys=True, ensure_ascii=False, allow_nan=False, separators=(",", ":"))
    if len(payload.encode("utf-8")) > 8192:
        raise ValueError("event exceeds byte bound")
    return payload


class EventStore:
    def __init__(self, path: Path):
        self.path = Path(path)
        if self.path.is_symlink():
            raise ValueError("event database cannot be a symlink")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE, payload TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS subscribers (
                    subscriber TEXT PRIMARY KEY, acknowledged INTEGER NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS deliveries (
                    subscriber TEXT NOT NULL, start INTEGER NOT NULL, end INTEGER NOT NULL,
                    PRIMARY KEY (subscriber, start, end));
                CREATE TABLE IF NOT EXISTS watches (
                    watch_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
                    first_seen REAL NOT NULL, next_poll REAL NOT NULL,
                    polls INTEGER NOT NULL DEFAULT 0, unknown_emitted INTEGER NOT NULL DEFAULT 0,
                    terminal INTEGER NOT NULL DEFAULT 0);
                CREATE INDEX IF NOT EXISTS watches_due ON watches(terminal, next_poll);
            """)
        self.path.chmod(0o600)

    @contextmanager
    def _transaction(self):
        db = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def _append(db, event):
        payload = validate_event(event)
        old = db.execute("SELECT seq, payload FROM events WHERE event_id=?", (event["event_id"],)).fetchone()
        if old:
            if old["payload"] != payload:
                raise ValueError("event_id already names different facts")
            return old["seq"]
        return db.execute("INSERT INTO events(event_id,payload) VALUES (?,?)",
                          (event["event_id"], payload)).lastrowid

    def append(self, event: dict) -> int:
        with self._transaction() as db:
            return self._append(db, event)

    def read(self, subscriber: str, *, since: int | None, limit: int,
             visible: Callable, kinds: list[str] | None = None, level: str | None = None) -> dict:
        _text(subscriber, "subscriber")
        if since is not None:
            _cursor(since)
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
            raise ValueError("limit must be an integer from 1 to 200")
        if kinds is not None and (not isinstance(kinds, list) or len(kinds) > len(KINDS)
                                  or any(not isinstance(k, str) or k not in KINDS for k in kinds)):
            raise ValueError("kinds must be a bounded list of known event kinds")
        if level is not None and (not isinstance(level, str) or level not in LEVELS):
            raise ValueError("unknown event level")
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO subscribers(subscriber) VALUES (?)", (subscriber,))
            acknowledged = db.execute("SELECT acknowledged FROM subscribers WHERE subscriber=?", (subscriber,)).fetchone()[0]
            start = acknowledged if since is None else since
            latest = db.execute("SELECT COALESCE(MAX(seq),0) FROM events").fetchone()[0]
            if start > latest:
                raise ValueError("cursor is beyond the event log")
            scanned = db.execute("SELECT seq,payload FROM events WHERE seq>? ORDER BY seq LIMIT ?", (start, limit)).fetchall()
            end = scanned[-1]["seq"] if scanned else start
            result = []
            for row in scanned:
                event = json.loads(row["payload"])
                if visible(event) and (kinds is None or event["kind"] in kinds) and (level is None or event["level"] == level):
                    result.append({**event, "seq": row["seq"]})
            if scanned:
                db.execute("INSERT OR IGNORE INTO deliveries VALUES (?,?,?)", (subscriber, start, end))
                # Compact contiguous scans; random since values cannot forge coverage.
                bound = acknowledged
                for row in db.execute("SELECT start,end FROM deliveries WHERE subscriber=? ORDER BY start", (subscriber,)):
                    if row["start"] <= bound:
                        bound = max(bound, row["end"])
                db.execute("DELETE FROM deliveries WHERE subscriber=? AND end<=?", (subscriber, bound))
                if bound > acknowledged:
                    db.execute("INSERT OR IGNORE INTO deliveries VALUES (?,?,?)", (subscriber, acknowledged, bound))
            return {"events": result, "next_cursor": end, "has_more": end < latest,
                    "acknowledged_cursor": acknowledged}

    def ack(self, subscriber: str, cursor: int) -> int:
        _text(subscriber, "subscriber")
        _cursor(cursor)
        with self._transaction() as db:
            row = db.execute("SELECT acknowledged FROM subscribers WHERE subscriber=?", (subscriber,)).fetchone()
            if row is None:
                raise ValueError("subscriber has no delivered range")
            previous = row[0]
            bound = previous
            for row in db.execute("SELECT start,end FROM deliveries WHERE subscriber=? ORDER BY start", (subscriber,)):
                if row["start"] <= bound:
                    bound = max(bound, row["end"])
            if cursor > bound:
                raise ValueError("cursor was not delivered to this subscriber")
            current = max(previous, cursor)
            db.execute("UPDATE subscribers SET acknowledged=? WHERE subscriber=?", (current, subscriber))
            db.execute("DELETE FROM deliveries WHERE subscriber=? AND end<=?", (subscriber, current))
            return current

    def discover_watch(self, watch_id, event, *, first_seen, now):
        """Project a validated accepted receipt; existing schedules never reset."""
        validate_event(event)
        with self._transaction() as db:
            db.execute("INSERT OR IGNORE INTO watches(watch_id,payload,first_seen,next_poll) VALUES (?,?,?,?)",
                       (watch_id, json.dumps(event), first_seen, now))

    def due_watches(self, now, *, limit=100):
        with self._transaction() as db:
            return [dict(row) for row in db.execute(
                "SELECT * FROM watches WHERE terminal=0 AND next_poll<=? ORDER BY next_poll,watch_id LIMIT ?",
                (now, limit))]

    def record_observation(self, watch_id, *, now, delay, event=None, terminal=False, unknown=False):
        """Append before completing the watch, in one crash-safe transaction."""
        with self._transaction() as db:
            watch = db.execute("SELECT terminal,unknown_emitted FROM watches WHERE watch_id=?", (watch_id,)).fetchone()
            if watch is None:
                raise ValueError("unknown observation task")
            if watch["terminal"]:
                return
            if unknown and watch["unknown_emitted"]:
                event = None
            if event is not None:
                if terminal and watch["unknown_emitted"]:
                    event = {**event, "kind": "run.corrected",
                             "summary": {**event["summary"], "previous_status": "UNKNOWN"}}
                self._append(db, event)
            db.execute("UPDATE watches SET next_poll=?,polls=polls+1,terminal=?,unknown_emitted=MAX(unknown_emitted,?) WHERE watch_id=?",
                       (now + delay, int(terminal), int(unknown), watch_id))
