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
import time
import json
import os
import re
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
#: The owner's request, the prompt sent and the final answer, per trace step.
TRACE_TEXT_MAX = 4000
#: Key-shaped strings: refused by memory, masked on cards.
SECRETISH = re.compile(r"\b(?:sk|pk|ghp|gho|xox[abps]|AKIA)[-_A-Za-z0-9]{12,}|"
                       r"\b[A-Za-z0-9+/_=-]{40,}\b|-----BEGIN [A-Z ]*PRIVATE KEY")
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
CREATE TABLE IF NOT EXISTS memory (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    scope       TEXT NOT NULL,
    topic       TEXT NOT NULL,
    text        TEXT NOT NULL,
    source      TEXT,
    by          TEXT,
    forgotten   TEXT
);
CREATE INDEX IF NOT EXISTS memory_scope ON memory(scope);
CREATE TABLE IF NOT EXISTS trace (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    at          TEXT NOT NULL,
    source      TEXT NOT NULL,
    step        TEXT NOT NULL,
    session_ref TEXT,
    run_id      TEXT,
    batch_id    TEXT,
    device_ref  TEXT,
    ref         TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS trace_run ON trace(run_id);
CREATE INDEX IF NOT EXISTS trace_batch ON trace(batch_id);
CREATE INDEX IF NOT EXISTS trace_at ON trace(at);
CREATE TABLE IF NOT EXISTS run_labels (
    session_ref TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    label       TEXT NOT NULL,
    at          TEXT NOT NULL,
    PRIMARY KEY (session_ref, run_id)
);
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
            if row["run_id"] or kind.startswith("batch."):
                self._trace(conn, "inbox", f"event {kind}", session_ref=row["session_ref"],
                            run_id=row["run_id"], device_ref=row["device_ref"],
                            batch_id=(row["work_ref"] or "").removeprefix("batch:") or None,
                            seq=seq)
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
            self._trace(conn, "fleet", "batch opened", batch_id=batch_id, by=opened_by,
                        name=name, total=len(members))
            for ref, rid in members:
                self._trace(conn, "fleet", "joined batch", batch_id=batch_id,
                            session_ref=ref, run_id=rid)
            for ref, rid in members:
                done = conn.execute(
                    "SELECT seq, kind FROM events WHERE session_ref=? AND run_id=? AND kind IN "
                    "('run.completed','run.failed','run.unknown') ORDER BY seq LIMIT 1",
                    (ref, rid)).fetchone()
                if done is not None:
                    self._settle(conn, ref, rid, done["kind"], done["seq"], only=batch_id)
            # A batch that is still running gets its card NOW, with every task
            # 还在跑, so the person who dispatched sees it exists before any of
            # it comes back. If the batch closed before this call (every run was
            # already settled above) there is nothing to open: the completion
            # card is the only message, and no blue card flashes and turns green.
            left = conn.execute("SELECT COUNT(*) FROM batch_members WHERE batch_id=? AND "
                                "outcome IS NULL", (batch_id,)).fetchone()[0]
            if left:
                opened = {n: None for n in _COLUMNS if n != "seq"}
                opened.update({
                    "event_id": hashlib.sha256(f"batch.opened|{batch_id}".encode()).hexdigest(),
                    "kind": "batch.opened", "level": "notify",
                    "occurred_at": _now(), "recorded_at": _now(),
                    "summary": _bounded(f"{name}：{len(members)} 个任务已派出", "summary",
                                        MAX_SUMMARY, required=True),
                    "work_ref": f"batch:{batch_id}", "dispatched_by": opened_by,
                    "evidence": f"batch:{batch_id}"})
                cols = list(opened)
                conn.execute(f"INSERT OR IGNORE INTO events ({','.join(cols)}) "
                             f"VALUES ({','.join('?' * len(cols))})",
                             [opened[n] for n in cols])
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
            self._trace(conn, "inbox", "batch completed", batch_id=batch_id,
                        counts=counts)

    # -- batches, for reading back ----------------------------------------------

    def recent_batches(self, limit: int = 10) -> list[dict[str, Any]]:
        """Open batches first, then the most recently closed ones."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT b.*, (SELECT COUNT(*) FROM batch_members m WHERE m.batch_id=b.batch_id "
                "AND m.outcome IS NOT NULL) AS settled FROM batches b "
                "ORDER BY (b.closed_seq IS NOT NULL), b.opened_at DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

    def find_batch(self, text: str) -> str | None:
        """A batch by id, or the newest whose name contains `text`."""
        text = (text or "").strip()
        if not text:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT batch_id FROM batches WHERE batch_id=?", (text,)).fetchone()
            if row is None:
                row = conn.execute("SELECT batch_id FROM batches WHERE name LIKE ? "
                                   "ORDER BY opened_at DESC LIMIT 1", (f"%{text}%",)).fetchone()
        return row["batch_id"] if row else None

    # -- memory: what the fleet has learned, kept on the hub --------------------

    MEMORY_MAX = 600

    def remember(self, *, scope: str, topic: str, text: str, by: str,
                 source: str | None = None) -> dict[str, Any]:
        """One note: a confirmed finding, a rule the owner set, how a device is
        used. Never a value — a secret-shaped string is refused, not stored."""
        scope = _bounded(scope, "scope", 80, required=True)
        if not re.fullmatch(r"fleet|device:[a-z0-9-]+|project:[\w .-]+", scope):
            raise InboxError("scope is fleet, device:<id> or project:<name>")
        topic = _bounded(topic, "topic", 80, required=True)
        text = _bounded(text, "text", self.MEMORY_MAX, required=True)
        if SECRETISH.search(text) or SECRETISH.search(topic):
            raise InboxError("this looks like it contains a key or token; memory keeps "
                             "findings and rules, never values (the key center keeps those)")
        with self._connect() as conn:
            cur = conn.execute("INSERT INTO memory(at, scope, topic, text, source, by) "
                               "VALUES (?,?,?,?,?,?)",
                               (_now(), scope, topic, text, _bounded(source, "source", 200), by))
            self._trace(conn, "fleet", "memory added", ref=f"memory:{cur.lastrowid}",
                        scope=scope, topic=topic, by=by)
            return {"id": cur.lastrowid, "scope": scope, "topic": topic}

    def recall(self, query: str | None = None, *, scope: str | None = None,
               limit: int = 10) -> list[dict[str, Any]]:
        """Notes matching every word of `query` (topic or text), newest first;
        a device's notes come with the fleet-wide ones."""
        where, args = ["forgotten IS NULL"], []
        if scope:
            where.append("(scope=? OR scope='fleet')")
            args.append(scope)
        for word in (query or "").split()[:6]:
            where.append("(topic LIKE ? OR text LIKE ? OR scope LIKE ?)")
            args += [f"%{word}%"] * 3
        with self._connect() as conn:
            rows = conn.execute("SELECT id, at, scope, topic, text, source, by FROM memory WHERE "
                                + " AND ".join(where) + " ORDER BY id DESC LIMIT ?",
                                (*args, int(limit))).fetchall()
        return [dict(r) for r in rows]

    def forget(self, memory_id: int, *, by: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("UPDATE memory SET forgotten=? WHERE id=? AND forgotten IS NULL",
                               (_now(), int(memory_id)))
            if cur.rowcount:
                self._trace(conn, "fleet", "memory forgotten", ref=f"memory:{memory_id}", by=by)
            return bool(cur.rowcount)

    # -- the trace: one timeline per task, kept on the hub ----------------------

    TRACE_DAYS = 14
    TRACE_SOURCES = ("fleet", "inbox", "monitor", "notify", "device", "commander")

    def _trace(self, conn: sqlite3.Connection, source: str, step: str, *,
               session_ref: str | None = None, run_id: str | None = None,
               batch_id: str | None = None, device_ref: str | None = None,
               ref: str | None = None, **detail: Any) -> None:
        """One step. States, codes, ids and counts only — never a reply, an
        instruction or a value: the trace is read by people and by agents."""
        if device_ref is None and session_ref and "|" in session_ref:
            device_ref = session_ref.split("|", 1)[0]
        clean = {}
        for key, value in detail.items():
            if value is None:
                continue
            if key == "text" and isinstance(value, str):
                # The owner's words, the prompt sent, the final answer: kept
                # (owner, 2026-11-09), masked for anything key-shaped.
                clean[key] = SECRETISH.sub("‹已遮蔽›", value)[:TRACE_TEXT_MAX]
            else:
                clean[key] = value if not isinstance(value, str) else value[:300]
        text = json.dumps(clean, ensure_ascii=False, default=str) if clean else None
        conn.execute("INSERT INTO trace(at, source, step, session_ref, run_id, batch_id, "
                     "device_ref, ref, detail) VALUES (?,?,?,?,?,?,?,?,?)",
                     (_now(), source, step[:80], session_ref, run_id, batch_id, device_ref,
                      (ref or "")[:300] or None, text))

    def trace(self, source: str, step: str, **fields: Any) -> None:
        """Append one step from outside the inbox (Fleet, monitor, notifier)."""
        if source not in self.TRACE_SOURCES:
            raise InboxError(f"unknown trace source {source!r}")
        with self._connect() as conn:
            self._trace(conn, source, step, **fields)
            if int(time.time()) % 97 == 0:
                conn.execute(f"DELETE FROM trace WHERE at < "
                             f"strftime('%Y-%m-%dT%H:%M:%SZ','now','-{self.TRACE_DAYS} days')")

    def read_trace(self, *, run_id: str | None = None, session_ref: str | None = None,
                   batch_id: str | None = None, minutes: int | None = None,
                   limit: int = 300) -> list[dict[str, Any]]:
        """The steps for a run, a session, a batch (and its members), or the last
        `minutes` across the fleet — oldest first."""
        where, args = [], []
        if batch_id:
            with self._connect() as conn:
                runs = [r["run_id"] for r in conn.execute(
                    "SELECT run_id FROM batch_members WHERE batch_id=?", (batch_id,))]
            where.append("(batch_id=?" + "".join(" OR run_id=?" for _ in runs)
                         + " OR ref LIKE ?)")
            args += [batch_id, *runs, f"%{batch_id}%"]
        if run_id:
            where.append("(run_id=? OR ref LIKE ?)")
            args += [run_id, f"%{run_id}%"]
        if session_ref:
            where.append("(session_ref=? OR ref LIKE ?)")
            args += [session_ref, f"%{session_ref}%"]
        if minutes:
            where.append("at >= strftime('%Y-%m-%dT%H:%M:%SZ','now',?)")
            args.append(f"-{int(minutes)} minutes")
        sql = "SELECT * FROM trace" + (" WHERE " + " AND ".join(where) if where else "")
        sql += " ORDER BY seq DESC LIMIT ?"
        args.append(int(limit))
        with self._connect() as conn:
            rows = [dict(r) for r in conn.execute(sql, args).fetchall()]
        for row in rows:
            row["detail"] = json.loads(row["detail"]) if row.get("detail") else {}
            row.pop("seq", None)
        return list(reversed(rows))

    # -- runs to watch ----------------------------------------------------------

    def note_dispatch(self, session_ref: str, run_id: str, project: str | None = None,
                      label: str | None = None) -> None:
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
            if label:
                # What the owner reads on a card instead of a run id: the first
                # line of the instruction, bounded. Kept a week.
                conn.execute("INSERT OR REPLACE INTO run_labels(session_ref, run_id, label, at) "
                             "VALUES (?,?,?,?)", (session_ref, run_id, label[:60], _now()))
                conn.execute("DELETE FROM run_labels WHERE at < datetime('now', '-7 days')")

    def label_of(self, session_ref: str | None, run_id: str | None) -> str | None:
        if not session_ref or not run_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT label FROM run_labels WHERE session_ref=? AND run_id=?",
                               (session_ref, run_id)).fetchone()
        return row["label"] if row else None

    def session_was_dispatched_to(self, session_ref: str | None) -> bool:
        """Fleet sent work into this session (it is a device doing a task)."""
        if not session_ref:
            return False
        with self._connect() as conn:
            return conn.execute("SELECT 1 FROM dispatched WHERE session_ref=? LIMIT 1",
                                (session_ref,)).fetchone() is not None

    def recent_dispatches(self, since: str) -> list[dict[str, Any]]:
        """Runs Fleet dispatched at or after `since` (ISO), oldest first."""
        with self._connect() as conn:
            rows = conn.execute("SELECT session_ref, run_id, project, at FROM dispatched "
                                "WHERE at >= ? ORDER BY at", (since,)).fetchall()
        return [dict(r) for r in rows]

    def dispatched_at(self, session_ref: str | None, run_id: str | None) -> str | None:
        """When Fleet dispatched this run, or None."""
        if not session_ref or not run_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT at FROM dispatched WHERE session_ref=? AND run_id=?",
                               (session_ref, run_id)).fetchone()
        return row["at"] if row else None

    def batch_of(self, session_ref: str | None, run_id: str | None) -> str | None:
        """The batch a run belongs to, if any: its outcome is told on that card."""
        if not session_ref or not run_id:
            return None
        with self._connect() as conn:
            row = conn.execute("SELECT batch_id FROM batch_members WHERE session_ref=? AND run_id=? "
                               "LIMIT 1", (session_ref, run_id)).fetchone()
        return row["batch_id"] if row else None

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
        """Kept for callers of 1.8; the record now stays two days so the monitor
        can tell a task Fleet started from a resident session's own turn."""
        with self._connect() as conn:
            conn.execute("DELETE FROM dispatched WHERE at < datetime('now', '-2 days')")

    def was_dispatched(self, session_ref: str | None, run_id: str | None) -> bool:
        """Fleet started this run (send or create), or a batch names it."""
        if not session_ref or not run_id:
            return False
        with self._connect() as conn:
            row = conn.execute(
                "SELECT 1 FROM dispatched WHERE session_ref=? AND run_id=? UNION "
                "SELECT 1 FROM batch_members WHERE session_ref=? AND run_id=? LIMIT 1",
                (session_ref, run_id, session_ref, run_id)).fetchone()
        return row is not None

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
