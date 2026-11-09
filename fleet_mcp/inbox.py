"""The Fleet event inbox: one ordered record of what happened to runs and nodes.

Design: `docs/architecture/FLEET-V1-V2-ARCHITECTURE.md` §5.1, and the resident
monitor in `docs/plans/fleet-v1-resident-monitor-and-tokyo-hub.md` §3.

One writer appends (the resident watcher); readers page through it by sequence
number and may advance a cursor of their own. Three invariants are the point of
this module and its tests:

*   `seq` is monotonic, and it is the cursor;
*   `event_id` is unique, so observing the same fact twice records it once;
*   a reader's cursor only moves forward.

The store is SQLite, local to the hub that owns it (ARCHITECTURE §24). Other
devices never write it over a network share; they reach it through Fleet.
"""
from __future__ import annotations

import hashlib
import os
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

#: What the inbox records. A run's terminal states, the two ways a run asks for
#: a person, the owner's own direction, and what a node reports about itself.
KINDS = frozenset({
    "run.completed", "run.failed", "run.unknown", "run.stalled",
    "decision.required", "directive.issued", "preempted",
    "node.unreachable", "node.pressure", "node.service_down", "node.recovered",
    # A dispatched batch whose every run reached an outcome, recorded once.
    "batch.completed",
    # A setting an agent changed inside the owner's bounds (fleet_tune).
    "tuning.changed",
})
#: The run outcomes that close a batch member. `run.stalled` is still running.
TERMINAL = frozenset({"run.completed", "run.failed", "run.unknown"})
MAX_BATCH = 100
#: `auto` is recorded and read; `notify` is also told to a person; `decide`
#: needs one.
LEVELS = frozenset({"auto", "notify", "decide"})

MAX_LIMIT = 200
MAX_SUMMARY = 500
MAX_EVIDENCE = 300
_SCAN_BATCH = 500

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      TEXT NOT NULL UNIQUE,
    occurred_at   TEXT NOT NULL,
    recorded_at   TEXT NOT NULL,
    kind          TEXT NOT NULL,
    level         TEXT NOT NULL,
    device_ref    TEXT,
    runtime       TEXT,
    session_ref   TEXT,
    run_id        TEXT,
    project       TEXT,
    dispatched_by TEXT,
    work_ref      TEXT,
    agent_ref     TEXT,
    summary       TEXT NOT NULL,
    evidence      TEXT
);
CREATE TABLE IF NOT EXISTS batches (
    batch_id   TEXT PRIMARY KEY,
    name       TEXT NOT NULL,
    opened_by  TEXT NOT NULL,
    opened_at  TEXT NOT NULL,
    total      INTEGER NOT NULL,
    closed_seq INTEGER
);
CREATE TABLE IF NOT EXISTS batch_members (
    batch_id    TEXT NOT NULL,
    session_ref TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    outcome     TEXT,
    seq         INTEGER,
    PRIMARY KEY (batch_id, session_ref, run_id)
);
CREATE INDEX IF NOT EXISTS batch_member_run ON batch_members(session_ref, run_id);
CREATE TABLE IF NOT EXISTS dispatched (
    session_ref TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    project     TEXT,
    at          TEXT NOT NULL,
    PRIMARY KEY (session_ref, run_id)
);
CREATE TABLE IF NOT EXISTS cursors (
    subscriber TEXT PRIMARY KEY,
    seq        INTEGER NOT NULL,
    updated_at TEXT NOT NULL
);
"""

_COLUMNS = ("seq", "event_id", "occurred_at", "recorded_at", "kind", "level",
            "device_ref", "runtime", "session_ref", "run_id", "project",
            "dispatched_by", "work_ref", "agent_ref", "summary", "evidence")
_OPTIONAL = ("device_ref", "runtime", "session_ref", "run_id", "project",
             "dispatched_by", "work_ref", "agent_ref", "evidence")


class InboxError(ValueError):
    """An event or a read request the inbox refuses."""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _bounded(value: Any, name: str, limit: int, *, required: bool = False) -> str | None:
    if value is None:
        if required:
            raise InboxError(f"`{name}` is required")
        return None
    if not isinstance(value, str):
        raise InboxError(f"`{name}` must be a string")
    text = "".join(ch if ch.isprintable() or ch in "\n\t" else " " for ch in value).strip()
    if required and not text:
        raise InboxError(f"`{name}` must not be empty")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text or None


def event_id_for(event: dict[str, Any]) -> str:
    """The stable identity of one fact.

    A run's terminal fact is the same fact however often it is observed, so its
    time is not part of the identity. A node fact recurs — a node can go down,
    come back and go down again — so the transition time is.
    """
    kind = event["kind"]
    parts = [kind] + [str(event.get(name) or "") for name in
                      ("device_ref", "runtime", "session_ref", "run_id", "project")]
    if kind.startswith("node.") or kind in ("directive.issued", "preempted",
                                            "tuning.changed"):
        parts.append(str(event.get("occurred_at") or ""))
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Recorded:
    seq: int
    event_id: str
    created: bool


class EventInbox:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        parent = self.path.parent
        if not parent.is_dir() or parent.is_symlink():
            raise InboxError(f"inbox directory is missing or unsafe: {parent}")
        if self.path.is_symlink():
            raise InboxError(f"inbox file must not be a symlink: {self.path}")
        if not self.path.exists():
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            os.close(fd)
        elif stat.S_IMODE(self.path.stat().st_mode) & 0o077:
            raise InboxError(f"inbox file is readable by others: {self.path}")
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            yield conn
        finally:
            conn.close()

    # -- writing -------------------------------------------------------------

    def record(self, event: dict[str, Any]) -> Recorded:
        """Append one fact, or return the row already holding it."""
        kind = event.get("kind")
        if kind not in KINDS:
            raise InboxError(f"unknown event kind: {kind!r}")
        level = event.get("level")
        if level not in LEVELS:
            raise InboxError(f"unknown event level: {level!r}")
        row: dict[str, Any] = {
            "kind": kind,
            "level": level,
            "occurred_at": _bounded(event.get("occurred_at"), "occurred_at", 64) or _now(),
            "summary": _bounded(event.get("summary"), "summary", MAX_SUMMARY, required=True),
        }
        for name in _OPTIONAL:
            limit = MAX_EVIDENCE if name == "evidence" else 256
            row[name] = _bounded(event.get(name), name, limit)
        if row["device_ref"] is not None and not row["device_ref"].startswith("device:"):
            raise InboxError("`device_ref` must be a device:<id> reference")
        row["event_id"] = event.get("event_id") or event_id_for(row)
        row["recorded_at"] = _now()
        names = [c for c in _COLUMNS if c != "seq"]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            existing = conn.execute("SELECT seq FROM events WHERE event_id=?",
                                    (row["event_id"],)).fetchone()
            if existing is not None:
                conn.execute("COMMIT")
                return Recorded(existing["seq"], row["event_id"], False)
            if kind in TERMINAL and row["session_ref"] and row["run_id"] and not row["work_ref"]:
                member = conn.execute(
                    "SELECT batch_id FROM batch_members WHERE session_ref=? AND run_id=? "
                    "AND outcome IS NULL LIMIT 1", (row["session_ref"], row["run_id"])).fetchone()
                if member is not None:
                    # Tagged so a reader (the notifier) folds it into the batch.
                    row["work_ref"] = f"batch:{member['batch_id']}"
            cursor = conn.execute(
                f"INSERT INTO events ({','.join(names)}) VALUES ({','.join('?' * len(names))})",
                [row[n] for n in names])
            seq = cursor.lastrowid
            if kind in TERMINAL and row["session_ref"] and row["run_id"]:
                self._settle(conn, row["session_ref"], row["run_id"], kind, seq)
            conn.execute("COMMIT")
            return Recorded(seq, row["event_id"], True)

    # -- batches ---------------------------------------------------------------

    def open_batch(self, name: str, runs: list[dict[str, str]], *, opened_by: str) -> dict[str, Any]:
        """Group dispatched runs so their outcome is reported once, together.

        Runs that already have an outcome in this inbox are settled at once, so
        a batch opened after a fast run finished still closes. When the last
        member settles, one `batch.completed` event is recorded.
        """
        name = _bounded(name, "name", 120, required=True)
        if not isinstance(runs, list) or not 1 <= len(runs) <= MAX_BATCH:
            raise InboxError(f"`runs` must list 1-{MAX_BATCH} runs")
        members = []
        for i, run in enumerate(runs):
            if not isinstance(run, dict):
                raise InboxError(f"runs[{i}] must be {{session_ref, run_id}}")
            ref = _bounded(run.get("session_ref"), "session_ref", 256, required=True)
            rid = _bounded(run.get("run_id"), "run_id", 256, required=True)
            if (ref, rid) in members:
                raise InboxError(f"runs[{i}] repeats a run")
            members.append((ref, rid))
        batch_id = hashlib.sha256(
            "\x1f".join([name, opened_by, _now(), os.urandom(4).hex()]
                         + [f"{a}|{b}" for a, b in members]).encode("utf-8")).hexdigest()[:16]
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("INSERT INTO batches(batch_id, name, opened_by, opened_at, total) "
                         "VALUES (?,?,?,?,?)", (batch_id, name, opened_by, _now(), len(members)))
            conn.executemany("INSERT INTO batch_members(batch_id, session_ref, run_id) VALUES (?,?,?)",
                             [(batch_id, a, b) for a, b in members])
            for ref, rid in members:
                done = conn.execute(
                    "SELECT seq, kind FROM events WHERE session_ref=? AND run_id=? AND kind IN "
                    "('run.completed','run.failed','run.unknown') ORDER BY seq LIMIT 1",
                    (ref, rid)).fetchone()
                if done is not None:
                    self._settle(conn, ref, rid, done["kind"], done["seq"], only=batch_id)
            conn.execute("COMMIT")
        return self.batch(batch_id)

    def _settle(self, conn: sqlite3.Connection, session_ref: str, run_id: str, kind: str,
                seq: int, *, only: str | None = None) -> None:
        rows = conn.execute(
            "SELECT batch_id FROM batch_members WHERE session_ref=? AND run_id=? AND outcome IS NULL"
            + (" AND batch_id=?" if only else ""),
            (session_ref, run_id, only) if only else (session_ref, run_id)).fetchall()
        for row in rows:
            batch_id = row["batch_id"]
            conn.execute("UPDATE batch_members SET outcome=?, seq=? WHERE batch_id=? AND "
                         "session_ref=? AND run_id=?",
                         (kind.split(".", 1)[1], seq, batch_id, session_ref, run_id))
            open_left = conn.execute("SELECT COUNT(*) FROM batch_members WHERE batch_id=? AND "
                                     "outcome IS NULL", (batch_id,)).fetchone()[0]
            batch = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if open_left or batch["closed_seq"] is not None:
                continue
            counts = dict(conn.execute("SELECT outcome, COUNT(*) FROM batch_members WHERE "
                                       "batch_id=? GROUP BY outcome", (batch_id,)).fetchall())
            words = "，".join(f"{w} {counts[k]}" for k, w in (("completed", "完成"),
                                                              ("failed", "失败"),
                                                              ("unknown", "无法确认"))
                             if counts.get(k))
            failed = counts.get("failed", 0) + counts.get("unknown", 0)
            summary = f"{batch['name']}：{batch['total']} 个任务都有了结果（{words}）"
            values = {n: None for n in _COLUMNS if n != "seq"}
            values.update({
                "event_id": hashlib.sha256(f"batch.completed|{batch_id}".encode()).hexdigest(),
                "kind": "batch.completed", "level": "decide" if failed else "notify",
                "occurred_at": _now(), "recorded_at": _now(), "summary": summary[:MAX_SUMMARY],
                "work_ref": f"batch:{batch_id}", "dispatched_by": batch["opened_by"],
                "evidence": f"batch:{batch_id}"})
            names = list(values)
            cur = conn.execute(
                f"INSERT OR IGNORE INTO events ({','.join(names)}) "
                f"VALUES ({','.join('?' * len(names))})", [values[n] for n in names])
            conn.execute("UPDATE batches SET closed_seq=? WHERE batch_id=?",
                         (cur.lastrowid, batch_id))

    # -- runs to watch ----------------------------------------------------------

    def note_dispatch(self, session_ref: str, run_id: str, project: str | None = None) -> None:
        """Fleet started this run: the monitor watches it from the next tick.

        Without this the monitor only found runs still running when it looked
        (every 30 s), so a task that finished in seconds never got an outcome,
        and a batch holding it never closed and never reported.
        """
        session_ref = _bounded(session_ref, "session_ref", 256, required=True)
        run_id = _bounded(run_id, "run_id", 256, required=True)
        with self._connect() as conn:
            conn.execute("INSERT OR IGNORE INTO dispatched(session_ref, run_id, project, at) "
                         "VALUES (?,?,?,?)", (session_ref, run_id, project, _now()))

    def runs_to_watch(self, limit: int = 500) -> list[dict[str, Any]]:
        """Dispatched runs not yet handed to the monitor, and every member of an
        open batch that has no outcome yet (a batch may name runs Fleet did not
        start itself)."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT session_ref, run_id, project FROM dispatched "
                "UNION SELECT m.session_ref, m.run_id, NULL FROM batch_members m "
                "JOIN batches b ON b.batch_id = m.batch_id "
                "WHERE m.outcome IS NULL AND b.closed_seq IS NULL LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def forget_dispatch(self, session_ref: str, run_id: str) -> None:
        with self._connect() as conn:
            conn.execute("DELETE FROM dispatched WHERE session_ref=? AND run_id=?",
                         (session_ref, run_id))

    def batch(self, batch_id: str) -> dict[str, Any]:
        """One batch: its members, their outcomes so far, and whether it closed."""
        with self._connect() as conn:
            head = conn.execute("SELECT * FROM batches WHERE batch_id=?", (batch_id,)).fetchone()
            if head is None:
                raise InboxError(f"no batch {batch_id!r}")
            members = conn.execute("SELECT session_ref, run_id, outcome FROM batch_members "
                                   "WHERE batch_id=? ORDER BY rowid", (batch_id,)).fetchall()
        runs = [dict(m) for m in members]
        pending = sum(1 for r in runs if r["outcome"] is None)
        return {"batch_id": batch_id, "name": head["name"], "opened_by": head["opened_by"],
                "opened_at": head["opened_at"], "total": head["total"],
                "settled": head["total"] - pending, "pending": pending,
                "closed": head["closed_seq"] is not None, "runs": runs}

    # -- reading -------------------------------------------------------------

    def cursor(self, subscriber: str) -> int:
        with self._connect() as conn:
            found = conn.execute("SELECT seq FROM cursors WHERE subscriber=?",
                                 (subscriber,)).fetchone()
        return found["seq"] if found else 0

    def read(self, subscriber: str, *, since: int | None = None,
             kinds: list[str] | None = None, level: str | None = None,
             limit: int = 50, ack: bool = False,
             readable: Callable[[str | None], bool] = lambda _ref: True) -> dict[str, Any]:
        """Events after `since` (default: this subscriber's cursor), oldest first.

        `readable(device_ref)` drops events the caller may not see. Dropped
        events still move `next_since`: the caller could never read them, and a
        cursor parked in front of them would stall forever.
        """
        if not isinstance(subscriber, str) or not subscriber:
            raise InboxError("a subscriber is required")
        if since is not None and (not isinstance(since, int) or isinstance(since, bool)
                                  or since < 0):
            raise InboxError("`since` must be a non-negative integer")
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= MAX_LIMIT:
            raise InboxError(f"`limit` must be between 1 and {MAX_LIMIT}")
        if kinds is not None:
            unknown = sorted(set(kinds) - KINDS)
            if unknown:
                raise InboxError(f"unknown event kinds: {unknown}")
        if level is not None and level not in LEVELS:
            raise InboxError(f"unknown event level: {level!r}")

        start = self.cursor(subscriber) if since is None else since
        position = start
        events: list[dict[str, Any]] = []
        more = False
        clauses, params = ["seq > ?"], [0]
        if kinds:
            clauses.append(f"kind IN ({','.join('?' * len(kinds))})")
            params.extend(kinds)
        if level is not None:
            clauses.append("level = ?")
            params.append(level)
        query = (f"SELECT {','.join(_COLUMNS)} FROM events WHERE {' AND '.join(clauses)} "
                 f"ORDER BY seq LIMIT {_SCAN_BATCH}")
        with self._connect() as conn:
            while True:
                params[0] = position
                rows = conn.execute(query, params).fetchall()
                for row in rows:
                    if len(events) == limit:
                        more = True
                        break
                    position = row["seq"]
                    if readable(row["device_ref"]):
                        events.append({k: row[k] for k in _COLUMNS})
                if more or len(rows) < _SCAN_BATCH:
                    break
        result = {"events": events, "next_since": position, "more": more,
                  "cursor": self.cursor(subscriber)}
        if ack:
            result["cursor"] = self._advance(subscriber, position)
        return result

    def ack(self, subscriber: str, seq: int) -> int:
        """Move `subscriber`'s cursor to `seq` (never backwards). Returns it."""
        if not isinstance(subscriber, str) or not subscriber:
            raise InboxError("a subscriber is required")
        if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
            raise InboxError("`seq` must be a non-negative integer")
        return self._advance(subscriber, seq)

    def _advance(self, subscriber: str, seq: int) -> int:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            found = conn.execute("SELECT seq FROM cursors WHERE subscriber=?",
                                 (subscriber,)).fetchone()
            current = found["seq"] if found else 0
            if seq > current:
                conn.execute(
                    "INSERT INTO cursors(subscriber, seq, updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(subscriber) DO UPDATE SET seq=excluded.seq, "
                    "updated_at=excluded.updated_at",
                    (subscriber, seq, _now()))
                current = seq
            conn.execute("COMMIT")
        return current
