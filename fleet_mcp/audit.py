"""Audit trail and rate limiting.

An audit record answers "who asked what, of which device, and what came back" —
never "what did it say". Message bodies and transcript content are not written
to the audit log; the log is a control-plane record, and a control-plane record
that accumulates the payload of every session read is a second copy of every
conversation on every machine.

Rotation is by size with a single `.1` generation. It is bounded on purpose: an
unbounded audit file on a shared host grows until the host notices, and a
"rotate everything" policy on a shared host reaches other users' files.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import AuditBinding

# How much of the end of the audit file is read to satisfy a `tail`. A record
# is a few hundred bytes, so this holds hundreds of them; the full file is read
# only when it does not, which keeps the cheap path and the correct path from
# being different answers.
_TAIL_WINDOW_BYTES = 64 * 1024


class RateLimiter:
    """A sliding-window limiter, per credential identity.

    Sliding rather than fixed-window: a fixed window lets a caller spend the
    whole budget at the end of one window and again at the start of the next,
    which is twice the intended rate at exactly the moment a burst matters.
    """

    def __init__(self, per_minute: int, window_seconds: float = 60.0) -> None:
        self.per_minute = max(1, int(per_minute))
        self.window = window_seconds
        self._events: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def check(self, identity: str) -> tuple[bool, float]:
        """Return (allowed, retry_after_seconds)."""
        now = time.monotonic()
        with self._lock:
            events = self._events.setdefault(identity, deque())
            cutoff = now - self.window
            while events and events[0] < cutoff:
                events.popleft()
            if len(events) >= self.per_minute:
                retry_after = max(0.0, self.window - (now - events[0]))
                return False, retry_after
            events.append(now)
            return True, 0.0


class AuditLog:
    def __init__(self, binding: AuditBinding | None) -> None:
        self.binding = binding
        self._lock = threading.Lock()
        self.limiter = RateLimiter(binding.rate_limit_per_minute) if binding else None
        if binding:
            binding.path.parent.mkdir(parents=True, exist_ok=True)

    def enabled(self) -> bool:
        return self.binding is not None

    def record(self, event: str, **fields: Any) -> None:
        if not self.binding:
            return
        entry = {"ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "event": event, **fields}
        line = json.dumps(entry, ensure_ascii=False, sort_keys=False)
        with self._lock:
            try:
                self._rotate_if_needed()
                with open(self.binding.path, "a", encoding="utf-8") as handle:
                    handle.write(line + "\n")
            except OSError:
                # An audit write failure must not take the control plane down,
                # and must not be silent. It goes to stderr, which the unit
                # captures; the alternative — raising — turns a full disk into
                # an outage of the surface that reports outages.
                import sys

                sys.stderr.write(f"fleet-mcp: audit write failed for {event}\n")

    def _rotate_if_needed(self) -> None:
        assert self.binding is not None
        path: Path = self.binding.path
        try:
            if not path.exists() or path.stat().st_size < self.binding.max_bytes:
                return
        except OSError:
            return
        rotated = path.with_suffix(path.suffix + ".1")
        try:
            os.replace(path, rotated)
        except OSError:
            pass

    def tail(self, limit: int = 50) -> list[dict[str, Any]]:
        """Recent records, for `fleet_status`. Never returns transcript content.

        Read from the end of the file rather than by splitting all of it.
        `fleet_status` is the tool a caller reaches for when a call has failed,
        which makes it the one most likely to be called repeatedly, and reading
        up to `max_bytes` and splitting every line to keep the last twenty is
        work that grows with the file and returns the same answer. The same
        last-`limit` records are produced either way: the window is widened to
        the whole file whenever it did not hold enough records to fill the
        request, so a short answer is never silently short.
        """
        if not self.binding:
            return []
        lines = self._tail_lines(limit)
        out: list[dict[str, Any]] = []
        for line in lines:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def _tail_lines(self, limit: int) -> list[str]:
        """The last `limit` lines, without reading the whole file when it fits
        inside one window."""
        path = self.binding.path
        try:
            size = path.stat().st_size
            with open(path, "rb") as handle:
                if size > _TAIL_WINDOW_BYTES:
                    handle.seek(size - _TAIL_WINDOW_BYTES)
                    raw = handle.read()
                    # The window almost certainly opens mid-record; dropping the
                    # partial first line is what makes the count honest.
                    _, _, raw = raw.partition(b"\n")
                else:
                    raw = handle.read()
        except OSError:
            return []
        lines = raw.decode("utf-8", "replace").splitlines()
        if len(lines) >= limit or size <= _TAIL_WINDOW_BYTES:
            return lines[-limit:]
        # The window was smaller than the request -- records longer than
        # expected, or a single line with no newline in it. Fall back to the
        # whole file so the answer does not shrink just because it was cheap.
        try:
            return path.read_text(encoding="utf-8").splitlines()[-limit:]
        except OSError:
            return []
