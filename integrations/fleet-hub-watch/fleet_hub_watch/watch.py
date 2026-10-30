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
                listing = self.service.list_sessions(self.principal, device=device,
                                                     limit=self.session_limit)
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

    def observe(self) -> dict[str, Any]:
        now = self.clock()
        recorded, still = [], 0
        for watch in self.store.due(now):
            observation = self._observe(watch)
            status, reason = observation["status"], observation.get("reason")
            age = now - watch["discovered_at"]
            event = None
            if status == STATUS_TERMINAL_VERIFIED:
                event = self._event(watch, "run.completed", "notify",
                                    "completed with readable output")
            elif status == STATUS_FAILED:
                event = self._event(watch, "run.failed", "notify", f"failed ({reason})")
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
                recorded.append(self.inbox.record(event))
                self.store.drop(watch, now)
                continue
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
