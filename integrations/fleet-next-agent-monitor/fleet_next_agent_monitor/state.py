"""Durable cursor, boot epoch and heartbeat — one file, written atomically.

Everything a restart must remember lives here and nowhere else. The reasons are
the three failures an observer actually has:

*   **It forgets.** A cursor in memory means a restarted monitor re-dispatches
    what it already delivered. The `cursor` map is keyed by `watch_key` and
    stores the fingerprint of the last fact that was handed on, so a re-read of
    the same fact is free and a *different* fact is not blocked.
*   **Two of it run at once.** A `flock` on a sidecar lock file makes "one
    writer" a fact rather than a hope: a second tick refuses immediately
    (`SECOND_WRITER`) instead of writing beside the first. `boot_epoch` names
    the process that adopted the state; when it differs from the previous
    epoch the restart is recorded as `recovered_from` rather than pretended
    away.
*   **It stops and nobody notices.** `last_ok_at` and `consecutive_failures`
    are the monitor's own vitals. A cursor older than the bound is `stale`,
    which is reported, never repaired by the monitor itself.

The write is atomic (temp file in the same directory, `fsync`, `os.replace`,
`fsync` of the directory) because the failure this file must not have is a
half-written cursor that reads as "nothing was ever delivered".
"""
from __future__ import annotations

import calendar
import fcntl
import json
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from .contract import SCHEMA_STATE, MonitorRefused

#: A cursor older than this is a stalled monitor.
STALE_AFTER_SECONDS = 3600.0


def _utc_iso(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


def _parse_utc(stamp: Any) -> float | None:
    """A UTC stamp back to epoch seconds, or None.

    `calendar.timegm` rather than `time.mktime`: the stamp is written in UTC, and
    reading it as local time would shift a staleness bound by the host's offset.
    """
    if not isinstance(stamp, str):
        return None
    try:
        return float(calendar.timegm(time.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError):
        return None


def _empty(epoch: str, pid: int, now: float) -> dict[str, Any]:
    return {
        "schema": SCHEMA_STATE,
        "boot_epoch": epoch,
        "writer_pid": pid,
        "last_tick_at": None,
        "last_ok_at": None,
        "consecutive_failures": 0,
        "cursor": {},
        "alerts": {},
        "recovered_from": None,
        "_first_seen_at": now,
    }


class DurableState:
    """The monitor's one durable file. No database, no second store."""

    def __init__(self, path: str | Path, *,
                 clock: Callable[[], float] | None = None,
                 pid: int | None = None) -> None:
        self.path = Path(path).expanduser()
        if not self.path.is_absolute():
            raise MonitorRefused("BAD_STATE", "the state path must be absolute")
        if not self.path.parent.is_dir():
            raise MonitorRefused(
                "BAD_STATE", f"{self.path.parent} is not a directory; a monitor "
                             f"never creates the directory it writes beside a cursor")
        self._clock = clock or time.time
        self._pid = pid if pid is not None else os.getpid()
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self._lock_fd: int | None = None

    # -- single writer ------------------------------------------------------

    def acquire(self) -> None:
        """Take the one-writer lock, or refuse. Released by `release`/the process.

        `flock` is released by the kernel when the holder exits, so a crashed
        monitor never leaves a lock that a restart cannot take — which is the
        one property a lock file alone does not have.
        """
        if self._lock_fd is not None:
            return
        fd = os.open(self.lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise MonitorRefused(
                "SECOND_WRITER",
                f"another monitor holds {self.lock_path}; one writer, and this is "
                f"not it") from None
        self._lock_fd = fd

    def release(self) -> None:
        if self._lock_fd is None:
            return
        try:
            fcntl.flock(self._lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(self._lock_fd)
            self._lock_fd = None

    def __enter__(self) -> "DurableState":
        self.acquire()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.release()

    # -- io -----------------------------------------------------------------

    def _read(self) -> dict[str, Any] | None:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None
        except OSError as exc:
            raise MonitorRefused(
                "BAD_STATE", f"the state file is unreadable ({type(exc).__name__}); "
                             f"an unreadable cursor is not an empty one") from None
        try:
            document = json.loads(raw)
        except ValueError:
            raise MonitorRefused("BAD_STATE", "the state file is not JSON") from None
        if not isinstance(document, dict):
            raise MonitorRefused("BAD_STATE", "the state file is not an object")
        if document.get("schema") != SCHEMA_STATE:
            raise MonitorRefused(
                "BAD_STATE", f"unknown state schema {document.get('schema')!r}")
        for name in ("boot_epoch", "cursor", "alerts"):
            if name not in document:
                raise MonitorRefused("BAD_STATE", f"the state file has no `{name}`")
        if not isinstance(document["cursor"], dict) or not isinstance(document["alerts"], dict):
            raise MonitorRefused("BAD_STATE", "`cursor` and `alerts` must be objects")
        return document

    def _write(self, document: dict[str, Any]) -> None:
        payload = json.dumps(document, ensure_ascii=False, sort_keys=True, indent=2)
        directory = self.path.parent
        handle, tmp_name = tempfile.mkstemp(prefix=".monitor-state.", dir=str(directory))
        try:
            with os.fdopen(handle, "w", encoding="utf-8") as stream:
                stream.write(payload)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp_name, self.path)
            tmp_name = None  # type: ignore[assignment]
            dir_fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        finally:
            if tmp_name is not None:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    # -- lifecycle ----------------------------------------------------------

    def open(self, *, epoch: str | None = None) -> dict[str, Any]:
        """Load the state and adopt a boot epoch. Caller holds the writer lock.

        Adoption happens *before* any read of the far end: a restart that
        discovered who it was only after sampling would have delivered twice.
        """
        epoch = epoch or uuid.uuid4().hex
        now = self._clock()
        document = self._read()
        if document is None:
            fresh = _empty(epoch, self._pid, now)
            self._write(fresh)
            return fresh

        previous_epoch = document.get("boot_epoch")
        if previous_epoch != epoch:
            # A different epoch is a restart. It is recorded, not refused: the
            # lock above is what decides whether two monitors may coexist.
            document["recovered_from"] = previous_epoch
        document["boot_epoch"] = epoch
        document["writer_pid"] = self._pid
        self._write(document)
        return document

    # -- cursor -------------------------------------------------------------

    @staticmethod
    def dispatched(document: dict[str, Any], key: str, fingerprint: str) -> bool:
        """Was *this exact fact* already handed on by a previous process?"""
        row = document.get("cursor", {}).get(key) or {}
        return row.get("fingerprint") == fingerprint

    def record(self, document: dict[str, Any], *, key: str, fingerprint: str,
               status: str, reason: str | None, now: float | None = None) -> None:
        document.setdefault("cursor", {})[key] = {
            "fingerprint": fingerprint,
            "status": status,
            "reason": reason,
            "recorded_at": _utc_iso(now if now is not None else self._clock()),
        }

    # -- heartbeat ----------------------------------------------------------

    def tick(self, document: dict[str, Any], *, ok: bool,
             now: float | None = None) -> dict[str, Any]:
        moment = now if now is not None else self._clock()
        document["last_tick_at"] = _utc_iso(moment)
        if ok:
            document["last_ok_at"] = _utc_iso(moment)
            document["consecutive_failures"] = 0
        else:
            document["consecutive_failures"] = int(
                document.get("consecutive_failures") or 0) + 1
        return document

    def cursor_age(self, document: dict[str, Any], *, now: float | None = None) -> float | None:
        """Seconds since the last *successful* tick, or None if there never was one."""
        when = _parse_utc(document.get("last_ok_at"))
        if when is None:
            return None
        moment = now if now is not None else self._clock()
        return max(0.0, moment - when)

    def save(self, document: dict[str, Any]) -> None:
        self._write(document)

    def new_epoch(self) -> str:
        return uuid.uuid4().hex
