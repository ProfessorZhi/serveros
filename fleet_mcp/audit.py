"""Audit trail and rate limiting.

An audit record answers "who asked what, of which device, and what came back" —
never "what did it say". Message bodies and transcript content are not written
to the audit log; the log is a control-plane record, and a control-plane record
that accumulates the payload of every session read is a second copy of every
conversation on every machine.

That rule used to be a property of the callers: every `record()` call was
written carefully, and the module trusted them to stay that way. It is now a
property of the module. `record()` takes `**fields` from anyone, so the field
names and value shapes are allowlisted by `disclosure.project_record`, and the
projection runs again on the way out — a log file written by an older build, or
edited by hand, is still read back as facts only. This matters because the audit
is not a dead file: `fleet_status` returns its tail, so anything recorded here
can be handed to a credential, and the caller of that credential reads it next.

Rotation is by size with a single `.1` generation. It is bounded on purpose: an
unbounded audit file on a shared host grows until the host notices, and a
"rotate everything" policy on a shared host reaches other users' files.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any

from .config import AuditBinding
from .disclosure import project_record


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
        """Write one control-plane fact.

        The fields are projected onto `disclosure.SAFE_AUDIT_FIELDS` rather than
        trusted, and anything dropped is named on stderr. A caller that needs a
        new field recorded adds it to that allowlist, in a diff somebody
        reviews; a caller that tries to record a credential loses the field and
        the attempt is visible, which is the difference between a control and a
        convention.
        """
        if not self.binding:
            return
        stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        entry, withheld = project_record({"ts": stamp, "event": event, **fields})
        line = json.dumps(entry, ensure_ascii=False, sort_keys=False)
        if withheld:
            # Counted, not quoted: naming the value would put it exactly where
            # the projection just stopped it from going, and stderr is
            # captured by the unit's journal.
            sys.stderr.write(
                f"fleet-mcp: audit withheld {withheld} field(s) on {event}; "
                f"name them in fleet_mcp/disclosure.py if they are facts\n"
            )
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
                sys.stderr.write(f"fleet-mcp: audit write failed for {event}\n")

    def record_grant(
        self,
        *,
        event: str,
        principal: str,
        device_ref: str,
        verb: str,
        decision: str,
        grant_reason: str,
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
        never a secret.

        `grant_reason` rather than `reason`, and the difference is the whole
        point: `reason` is a name a caller can fill through the generic
        `record()`, so the disclosure allowlist refuses to keep its value
        whoever wrote it. The code passed here is written by *this* surface from
        a closed set (`operator-grant`, `device-not-eligible`), which is a fact
        an audit of an inert grant exists to hold — "a grant that reads as
        authority and does nothing" is invisible to everyone except whoever hits
        it, and a withheld field would put it back out of sight. Two names,
        because they are two things wearing one word.
        """
        self.record(
            event,
            principal=principal,
            device_ref=device_ref,
            verb=verb,
            decision=decision,
            grant_reason=grant_reason,
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
        """Recent records, for `fleet_status`, each re-projected on the way out.

        Never returns transcript content, and no longer returns anything a
        different writer put in the file: this is the second application of the
        allowlist, and it is the one that covers records this build did not
        write. A line is kept only if it is a record — a JSON object naming an
        event, which is the unit this log is made of. A bare list, a number, or
        an object with no `event` is not a truncated record, it is not a record,
        and passing it along as whatever it happened to be is how a log stops
        being a log.
        """
        if not self.binding:
            return []
        try:
            lines = self.binding.path.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        out: list[dict[str, Any]] = []
        for line in lines[-limit:]:
            try:
                parsed = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(parsed, dict):
                continue
            if not isinstance(parsed.get("event"), str) or not parsed["event"]:
                continue
            projected, _ = project_record(parsed)
            out.append(projected)
        return out
