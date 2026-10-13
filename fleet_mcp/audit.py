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

    def record_grant(
        self,
        *,
        event: str,
        principal: str,
        device_ref: str,
        verb: str,
        decision: str,
        reason: str,
        session_ref: str | None = None,
    ) -> None:
        """A grant decision, written through a closed field set.

        The allowlist here is not a filter applied to the fields — it *is* the
        signature. There is no `**fields`, so a message body, a transcript, a
        session title or a credential is not rejected by this method; it is not
        expressible through it. That is the stronger form of the rule, and it is
        the one that survives someone adding a call site in a hurry: a filter
        has to be remembered, a signature does not.

        The fields that *are* here are all identifiers the control plane already
        handles — `principal` is the credential's own name (`oauth:<client_id>`
        or `owner-pat`, the same value `fleet.oauth.*` records as `client_id`),
        never a secret. `reason` is written by this surface, not by a caller.
        """
        self.record(
            event,
            principal=principal,
            device_ref=device_ref,
            verb=verb,
            decision=decision,
            reason=reason,
            session_ref=session_ref,
        )

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
        """Recent records, for `fleet_status`. Never returns transcript content."""
        if not self.binding:
            return []
        try:
            lines = self.binding.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
