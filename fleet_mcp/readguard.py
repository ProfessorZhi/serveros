"""Read-path coalescing: one measurement per question, however many askers.

Why this module exists
----------------------
Every read verb on this surface fans out, and every fan-out step is a network
round trip. `fleet_list_devices` probes each enrolled device, `fleet_status`
probes each bound provider, `fleet_list_sessions` calls each device's session
listing. On the node path one such call becomes a signed request over the
tunnel, which the connector answers by making three MCP round trips to the
CloudCLI gateway on a workstation somebody else is using.

None of that was ever deduplicated. The transport is a `ThreadingHTTPServer`,
so two identical calls arriving together each performed the whole fan-out --
two probes of the same device, two three-round-trip chains on the far machine,
for one question. A client that issues tool calls in parallel, or that retries
while the first is still running, multiplied the load by its own concurrency.
The connector refuses retries by nonce, so a retry cannot be collapsed on the
far side: it has to be collapsed here, before the request is signed.

What it does
------------
`ReadGuard.run(key, fn)` collapses *duplicate work that is in flight*. A caller
that arrives while a measurement of the same key is already running joins that
measurement instead of starting a second one, and receives the same value the
leader receives. This costs the caller nothing in truth: what it is handed is a
measurement concurrent with its own call, not one taken before it.

What it deliberately does not do
--------------------------------
Two properties are load-bearing and are not configurable:

*   **A failure is never stored.** When a measurement raises, every caller
    waiting on it gets that same refusal, and the entry is dropped before the
    exception is delivered. The next call re-measures. A guard that remembered
    a refusal would turn one bad minute into a sustained outage reported as a
    current fact, which is the failure mode this surface is built to avoid:
    "the device is down" and "we stopped asking" must not become the same
    answer.

*   **An answer from a completed earlier call is served only when `ttl_seconds`
    was explicitly configured, and when it is, the caller is told.** The
    default is zero, and `policy.sessions_cache_seconds` ships as 0.0. Every
    field these verbs report -- `running`, `hostState`, reachability,
    authorization -- is live state, and a cached value presented as a fresh
    measurement is exactly the confusion this surface exists to prevent. When
    an operator opts in, `run` returns a reuse record alongside the value and
    the caller is obliged to disclose it; see `FleetService.list_sessions`.

Key shape
---------
The key is the caller's identity, the verb, the device, and the filters. It is
built by `FleetService._read_key` and never from a bare device ref, so two
principals cannot share an answer, one principal's `limit=20` cannot be served
to its `limit=40`, and a session listing cannot be served as a device listing.

The guard is reached only *after* scope and rate admission, so a refused caller
never creates an entry: an authorization failure is neither cacheable nor
bypassable, because it never gets this far.

Bounded
-------
The table holds in-flight entries plus, only when a TTL is configured, completed
ones, capped at `max_keys` with the oldest completed entry evicted first.
In-flight entries are never evicted: dropping one would either lose a waiter or
force a duplicate measurement, which is the thing this module exists to avoid.
"""
from __future__ import annotations

import threading
import time
from typing import Any, Callable, TypeVar

from .errors import FleetRefusal

T = TypeVar("T")

# A waiter that has waited this long stops waiting for someone else's
# measurement and takes its own. Deliberately generous: every provider path is
# already bounded by its own connect and read timeouts, so reaching this bound
# means one of those bounds failed, not that the device is slow. Reaching it is
# not an error and is not reported as one -- the waiter measures for itself
# rather than reporting a non-answer it did not actually obtain.
DEFAULT_MAX_WAIT_SECONDS = 120.0

MAX_TRACKED_KEYS = 512

# Accepted for the key's filter values. A filter that is absent is tagged, and
# tagged differently from one that is an empty string.
_NULL = ("null", "")


def canonical_value(value: Any) -> tuple[str, str]:
    """One filter value, in a form two callers can agree on.

    Coercion is deliberately shallow, and erring toward "not the same". `None`
    is not `""` -- one asks for every state and the other for sessions whose
    state is the empty string. `"running"` is not an absent filter. `20` is not
    `"20"`. Over-normalising would answer a question nobody asked, which is the
    one mistake a key like this must not make; splitting two spellings of one
    filter only costs a duplicate measurement and tells nobody anything false.
    In practice the ambiguous cases do not arise: call sites cast `limit` to
    `int` before building the key.
    """
    if value is None:
        return _NULL
    if isinstance(value, bool):
        return ("bool", "1" if value else "0")
    if isinstance(value, int):
        return ("int", str(value))
    if isinstance(value, float):
        return ("float", repr(value))
    return ("str", str(value))


class _Entry:
    __slots__ = ("label", "event", "value", "error", "done", "at")

    def __init__(self, label: str) -> None:
        self.label = label
        self.event = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None
        self.done = False
        self.at = 0.0


def _clone(exc: BaseException) -> BaseException:
    """A second caller's copy of a refusal.

    Raising one exception object in two threads would leave it carrying
    whichever traceback was written last, so a `FleetRefusal` is rebuilt from
    its parts. Anything else is re-raised as-is: this guard is not in the
    business of reinterpreting an exception it was not given a code for.
    """
    if isinstance(exc, FleetRefusal):
        return FleetRefusal(exc.code, exc.detail, **exc.extra)
    return exc


class ReadGuard:
    """Collapse in-flight duplicate reads. Never store a failure; never serve
    a completed answer unless a TTL was configured and the caller is told."""

    def __init__(
        self,
        *,
        max_wait_seconds: float = DEFAULT_MAX_WAIT_SECONDS,
        max_keys: int = MAX_TRACKED_KEYS,
    ) -> None:
        self.max_wait_seconds = max_wait_seconds
        self.max_keys = max_keys
        self._entries: dict[Any, _Entry] = {}
        self._lock = threading.Lock()
        self._counters = {"measurements": 0, "joined": 0, "reused": 0, "failures": 0}

    # -- introspection -----------------------------------------------------

    def stats(self) -> dict[str, int]:
        """What this guard has done, for a load measurement rather than a log.

        `measurements` is the number of calls that actually reached a provider;
        `joined` and `reused` are the calls that did not. A before/after count
        of provider invocations is the evidence that this changed anything, and
        it is read from here rather than inferred from wall-clock time.
        """
        with self._lock:
            return dict(self._counters)

    def tracked(self) -> int:
        with self._lock:
            return len(self._entries)

    # -- the guard ---------------------------------------------------------

    def run(
        self,
        key: Any,
        fn: Callable[[], T],
        *,
        ttl_seconds: float = 0.0,
        label: str = "",
    ) -> tuple[T, dict[str, Any] | None]:
        """Measure `key`, or join the measurement already running.

        Returns `(value, reuse)` where `reuse` is `None` when the value was
        measured by this call and a record of the reuse otherwise. A caller that
        passes a non-zero `ttl_seconds` must disclose a non-`None` `reuse`; a
        caller that passes zero can never see one, because nothing completed is
        kept.
        """
        ttl = max(0.0, float(ttl_seconds))
        deadline = time.monotonic() + self.max_wait_seconds
        while True:
            leader: _Entry | None = None
            waiter: _Entry | None = None
            with self._lock:
                self._evict_locked()
                entry = self._entries.get(key)
                if entry is None:
                    leader = _Entry(label)
                    self._entries[key] = leader
                elif entry.done:
                    if entry.error is not None:
                        # Never served; a failure is dropped before it is
                        # delivered. Belt and braces: `_execute` already popped
                        # this slot, so this is only reachable if a pop lost a
                        # race.
                        self._entries.pop(key, None)
                        continue
                    age = time.monotonic() - entry.at
                    if ttl > 0 and age < ttl:
                        self._counters["reused"] += 1
                        return entry.value, {
                            "reused": True,
                            "scope": entry.label or label,
                            "age_ms": round(age * 1000, 1),
                            "ttl_ms": round(ttl * 1000, 1),
                        }
                    # Completed and not reusable under the configured TTL: this
                    # caller needs a measurement of its own, so it leads.
                    self._entries.pop(key, None)
                    continue
                else:
                    waiter = entry

            if leader is not None:
                return self._execute(key, leader, fn, ttl)

            assert waiter is not None
            if waiter.event.wait(max(0.0, deadline - time.monotonic())):
                with self._lock:
                    self._counters["joined"] += 1
                if waiter.error is not None:
                    raise _clone(waiter.error)
                return waiter.value, None

            # Outlived the guard's own bound. Take a measurement rather than
            # report somebody else's non-answer, and drop the orphaned entry so
            # the next caller does not join work that is not finishing.
            with self._lock:
                if waiter.done:
                    continue
                if self._entries.get(key) is waiter:
                    self._entries.pop(key, None)
            deadline = time.monotonic() + self.max_wait_seconds

    def _execute(
        self, key: Any, entry: _Entry, fn: Callable[[], T], ttl: float
    ) -> tuple[T, dict[str, Any] | None]:
        try:
            value = fn()
        except BaseException as exc:  # noqa: BLE001 - delivered, then forgotten
            with self._lock:
                self._counters["failures"] += 1
                if self._entries.get(key) is entry:
                    self._entries.pop(key, None)
                entry.error = exc
                entry.done = True
                entry.event.set()
            raise
        with self._lock:
            self._counters["measurements"] += 1
            entry.value = value
            entry.at = time.monotonic()
            entry.done = True
            if ttl <= 0 and self._entries.get(key) is entry:
                # Nothing may be served from a completed call, so the slot goes
                # away the moment it completes and the table holds only
                # in-flight work.
                self._entries.pop(key, None)
            entry.event.set()
        return value, None

    def _evict_locked(self) -> None:
        if len(self._entries) <= self.max_keys:
            return
        completed = sorted(
            ((k, e) for k, e in self._entries.items() if e.done),
            key=lambda item: item[1].at,
        )
        for k, _entry in completed:
            if len(self._entries) <= self.max_keys:
                break
            self._entries.pop(k, None)
