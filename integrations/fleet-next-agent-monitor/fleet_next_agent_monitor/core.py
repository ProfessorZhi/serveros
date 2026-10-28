"""One bounded monitor tick: authorize, sample, decide, hand over at most once.

`run_once()` is the whole monitor. It is not a daemon, a scheduler or an event
bus, and it owns no credential. What it owns is the *decision* about each fact:

1.  **Sample** every watch through the read-only source, which checks the grant
    before it reads anything. A denied watch spends zero provider calls.
2.  **Classify** into the vocabulary of `contract.py` — where `completed`
    without native activity is `UNKNOWN`, never success.
3.  **Deduplicate** against the durable cursor: the fingerprint of the last fact
    handed over is stored *before* the hand-over, so a crash between "sent" and
    "recorded" reads as UNKNOWN afterwards, not as permission to send again.
4.  **Hand over** only `TERMINAL_VERIFIED` and `FAILED`, through the single
    injected #423 notifier. Nothing else is dispatchable.
5.  **Report** its own vitals — `last_tick`, cursor age, backlog, consecutive
    failures — plus bounded, de-bounced warnings for UNKNOWN/offline.
6.  **Save** the state atomically, then return a tick document.

Nothing in this module sends a message, starts a process, or writes anywhere
except the one state file.
"""
from __future__ import annotations

import time
from typing import Any, Callable, Mapping

from .contract import (
    DISPATCHABLE,
    EXIT_BLOCKED_INPUT,
    EXIT_REFUSED,
    EXIT_SETTLED,
    EXIT_UNCONFIRMED,
    NOT_SUCCESS,
    SCHEMA_TICK,
    STATE_BLOCKED_INPUT,
    STATE_REFUSED,
    STATE_SETTLED,
    STATE_TICKED,
    STATE_UNCONFIRMED,
    STATUS_DENIED,
    STATUS_FAILED,
    STATUS_OFFLINE,
    STATUS_STALE,
    STATUS_TERMINAL_VERIFIED,
    STATUS_UNKNOWN,
    MonitorRefused,
    dispatch_fingerprint,
    require_watch,
    watch_key,
)
from .notifier import BLOCKED_LEDGER, NotifierUnavailable, payload_for
from .sources import LegacyFactsSource, observe_watch
from .state import STALE_AFTER_SECONDS, DurableState

#: Warnings a tick may raise. Each is a fact about the monitor, not about the
#: far end, and each is de-bounced so a permanently offline route cannot become
#: a message every tick.
WARN_STALE = "STALE_MONITOR"
WARN_BACKLOG = "BACKLOG"
WARN_UNKNOWN_RECEIPTS = "UNKNOWN_RECEIPTS"
WARN_UNKNOWN_OBSERVATION = "UNKNOWN_OBSERVATION"
WARN_OFFLINE = "SOURCE_OFFLINE"
WARN_DENIED = "SOURCE_DENIED"
WARN_BLOCKED_LEDGER = "BLOCKED_LEDGER"
WARN_FAILURES = "CONSECUTIVE_FAILURES"


def _iso(now: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now))


class MonitorCore:
    """The monitor body. One source, one notifier, one state file, one tick."""

    def __init__(self, *, source: LegacyFactsSource,
                 notifier: Any | None,
                 state: DurableState,
                 watches: list[Mapping[str, Any]],
                 clock: Callable[[], float] | None = None,
                 stale_after: float = STALE_AFTER_SECONDS,
                 debounce_ticks: int = 2,
                 max_warnings: int = 6,
                 epoch: str | None = None) -> None:
        if not watches:
            raise MonitorRefused(
                "BAD_CONFIG",
                "a monitor with no watches has nothing to observe; an empty watch "
                "list is a misconfiguration, not a quiet monitor")
        self._source = source
        self._notifier = notifier
        self._state = state
        self._watches = [require_watch(dict(w)) for w in watches]
        keys = [watch_key(w) for w in self._watches]
        if len(set(keys)) != len(keys):
            raise MonitorRefused(
                "BAD_CONFIG", "two watches name the same fact; one would shadow the other")
        self._clock = clock or time.time
        self._stale_after = float(stale_after)
        self._debounce = max(1, int(debounce_ticks))
        self._max_warnings = max(1, int(max_warnings))
        self._epoch = epoch

    # -- alerts -------------------------------------------------------------

    def _alert(self, document: dict, *, kind: str, key: str,
               status: str, reason: str | None, raised: list[dict]) -> None:
        """De-bounce one warning, and raise it once per distinct fact."""
        alerts = document.setdefault("alerts", {})
        row = alerts.setdefault(key, {"count": 0, "raised": False, "status": status,
                                      "reason": reason})
        row["count"] = int(row.get("count") or 0) + 1
        if row.get("status") != status or row.get("reason") != reason:
            # A different fact is a different warning.
            row.update({"status": status, "reason": reason, "raised": False})
        if row["count"] >= self._debounce and not row.get("raised"):
            row["raised"] = True
            raised.append({"code": kind, "watch": key.split("|", 1)[0],
                           "status": status, "reason": reason,
                           "why": f"{status}{('/' + reason) if reason else ''} seen "
                                  f"{row['count']} tick(s) in a row"})

    # -- one tick -----------------------------------------------------------

    def run_once(self) -> dict[str, Any]:
        started = self._clock()
        # One writer, then fencing, then any read. A concurrent process refuses
        # here — before it samples a fact it would otherwise deliver twice.
        try:
            self._state.acquire()
        except MonitorRefused as refusal:
            return {
                "schema": SCHEMA_TICK, "state": STATE_REFUSED, "exit": EXIT_REFUSED,
                "why": refusal.code, "detail": refusal.why, "watches": [],
                "counts": {}, "dispatched": 0, "delivered": 0, "duplicate": 0,
                "unknown_receipts": 0, "backlog": 0,
                "health": {"last_tick": _iso(started), "last_ok": None,
                           "cursor_age": None, "consecutive_failures": None,
                           "stale": None},
                "warnings": [],
            }
        try:
            document = self._state.open(epoch=self._epoch)
            return self._tick(started, document)
        finally:
            self._state.release()

    def _tick(self, started: float, document: dict[str, Any]) -> dict[str, Any]:

        warnings: list[dict] = []
        rows: list[dict] = []
        counts: dict[str, int] = {}
        dispatched = delivered = duplicate = unknown_receipts = 0
        blocked_ledger = False
        hard_failure = False
        dispatched_any = False

        for watch in self._watches:
            key = watch_key(watch)
            try:
                observation = observe_watch(self._source, watch)
            except MonitorRefused as refusal:
                # A refusal of *this watch* (a bad grant, a bad watch) is a fact
                # about the watch; it must not stop the other watches.
                observation = {"view": watch["kind"], "status": STATUS_DENIED,
                               "reason": refusal.code, "identity": {}, "native": None,
                               "readings": [], "readingsUsed": 0}
            except Exception as exc:  # noqa: BLE001 - an unreadable watch is UNKNOWN
                hard_failure = True
                observation = {"view": watch["kind"], "status": STATUS_UNKNOWN,
                               "reason": f"SOURCE_{type(exc).__name__}",
                               "identity": {}, "native": None, "readings": [],
                               "readingsUsed": 0}

            status = observation.get("status")
            reason = observation.get("reason")
            counts[status] = counts.get(status, 0) + 1
            row = {"key": key, "view": observation.get("view"), "status": status,
                   "reason": reason, "dispatched": False, "receipt": None}

            if status in (STATUS_UNKNOWN, STATUS_OFFLINE, STATUS_STALE, STATUS_DENIED):
                # A correct UNKNOWN is the monitor *working*; only an exception
                # escaping the source is a failure of the tick itself.
                kind = {STATUS_OFFLINE: WARN_OFFLINE, STATUS_DENIED: WARN_DENIED,
                        STATUS_STALE: WARN_STALE}.get(status, WARN_UNKNOWN_OBSERVATION)
                self._alert(document, kind=kind, key=f"{key}|{status}|{reason}",
                            status=status, reason=reason, raised=warnings)

            fingerprint = dispatch_fingerprint(observation)
            already = self._state.dispatched(document, key, fingerprint)
            if status in DISPATCHABLE and not already:
                if self._notifier is None:
                    blocked_ledger = True
                    row["receipt"] = BLOCKED_LEDGER
                else:
                    source_name, payload = payload_for(observation, watch,
                                                       observed_at=_iso(self._clock()))
                    # Recorded BEFORE the hand-over: a crash in `observe_payload`
                    # must read as UNKNOWN, never as "retry the send".
                    self._state.record(document, key=key, fingerprint=fingerprint,
                                       status=status, reason=reason)
                    self._state.save(document)
                    try:
                        answer = self._notifier.observe_payload(source_name, payload)
                        state, remote = _receipt(answer)
                    except Exception as exc:  # noqa: BLE001 - the notifier is not ours
                        state, remote = "DELIVERY_UNKNOWN", None
                        row["receipt_why"] = f"{type(exc).__name__}: {exc}"[:200]
                    dispatched += 1
                    dispatched_any = True
                    row["dispatched"] = True
                    row["receipt"] = state
                    if remote:
                        row["remote_id"] = remote
                    if state == "DELIVERED":
                        delivered += 1
                    elif state == "DUPLICATE":
                        duplicate += 1
                    else:
                        unknown_receipts += 1
                        self._alert(document, kind=WARN_UNKNOWN_RECEIPTS,
                                    key=f"{key}|receipt|{state}", status=status,
                                    reason=state, raised=warnings)
            elif status in DISPATCHABLE and already:
                row["receipt"] = "DUPLICATE"
                duplicate += 1

            rows.append(row)

        backlog = sum(1 for row in rows if row["status"] not in
                      (STATUS_TERMINAL_VERIFIED, STATUS_FAILED))
        ok = not blocked_ledger and not hard_failure
        self._state.tick(document, ok=ok, now=self._clock())
        age = self._state.cursor_age(document)
        stale = age is not None and age > self._stale_after
        if stale:
            warnings.append({"code": WARN_STALE, "status": None, "reason": None,
                             "why": f"the last successful tick is {int(age)}s old, "
                                    f"over the {int(self._stale_after)}s bound"})
        if backlog:
            warnings.append({"code": WARN_BACKLOG, "status": None, "reason": None,
                             "why": f"{backlog} watch(es) have no terminal verdict"})
        if blocked_ledger:
            warnings.append({"code": WARN_BLOCKED_LEDGER, "status": None, "reason": None,
                             "why": "a terminal fact is waiting and no #423 notifier is "
                                    "bound; nothing was delivered and nothing will be "
                                    "substituted for it"})
        failures = int(document.get("consecutive_failures") or 0)
        if failures:
            warnings.append({"code": WARN_FAILURES, "status": None, "reason": None,
                             "why": f"{failures} consecutive tick(s) did not succeed"})
        self._state.save(document)

        if blocked_ledger:
            state, exit_code = STATE_BLOCKED_INPUT, EXIT_BLOCKED_INPUT
        elif unknown_receipts:
            state, exit_code = STATE_UNCONFIRMED, EXIT_UNCONFIRMED
        elif stale:
            state, exit_code = STATE_UNCONFIRMED, EXIT_UNCONFIRMED
        elif backlog == 0:
            # Every question has a terminal verdict and every delivery is settled.
            state, exit_code = STATE_SETTLED, EXIT_SETTLED
        else:
            state, exit_code = STATE_TICKED, EXIT_SETTLED

        return {
            "schema": SCHEMA_TICK,
            "state": state,
            "exit": exit_code,
            "boot_epoch": document.get("boot_epoch"),
            "recovered_from": document.get("recovered_from"),
            "observed_at": _iso(started),
            "reads": self._source.readings_used,
            "counts": counts,
            "dispatched": dispatched,
            "delivered": delivered,
            "duplicate": duplicate,
            "unknown_receipts": unknown_receipts,
            "backlog": backlog,
            "watches": rows,
            "dispatched_any": dispatched_any,
            "health": {
                "last_tick": document.get("last_tick_at"),
                "last_ok": document.get("last_ok_at"),
                "cursor_age": age,
                "consecutive_failures": failures,
                "stale": stale,
            },
            "warnings": warnings[: self._max_warnings],
        }


def _receipt(answer: Any) -> tuple[str, str | None]:
    """Read a #423 Observation (or a mapping) for its delivery state.

    Only an explicit `DELIVERED` with a remote id counts. A notifier that
    returned nothing says UNKNOWN — it does not say "probably fine".
    """
    if answer is None:
        return "DELIVERY_UNKNOWN", None
    state = getattr(answer, "state", None)
    delivered = getattr(answer, "delivered", None)
    remote = getattr(answer, "remote_id", None)
    if isinstance(answer, Mapping):
        state = answer.get("state", state)
        delivered = answer.get("delivered", delivered)
        remote = answer.get("remote_id", remote)
    state = str(state or "DELIVERY_UNKNOWN")
    if state == "DELIVERED" and delivered is not True:
        # A state without the flag is a claim without the receipt.
        return "DELIVERY_UNKNOWN", remote
    return state, remote if isinstance(remote, str) else None


def notifier_or_none(spec: Any | None, *, config: Mapping[str, Any] | None = None
                     ) -> tuple[Any | None, str | None]:
    """Resolve the notifier, keeping a missing binding as a *named* blocker."""
    if spec is None:
        return None, "no notifier binding was configured"
    from .notifier import load_notifier
    try:
        return load_notifier(spec, config=config), None
    except NotifierUnavailable as refusal:
        return None, refusal.why


def statuses_that_are_not_success() -> frozenset[str]:
    """A hook for the test that pins the whole not-success set at once."""
    return NOT_SUCCESS
