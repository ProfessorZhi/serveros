"""One bounded monitor tick: authorize, sample, decide, hand over at most once.

`run_once()` is the whole monitor. It is not a daemon, a scheduler or an event
bus, and it owns no credential. What it owns is the *decision* about each fact:

1.  **Sample** every watch through the read-only source, which checks the grant
    before it reads anything. A denied watch spends zero provider calls.
2.  **Classify** into the vocabulary of `contract.py` — where `completed`
    without native activity is `UNKNOWN`, never success.
3.  **Deduplicate** against the durable cursor. The cursor distinguishes what
    was *attempted* from what was *delivered*: a hand-over is recorded before
    it is sent (so a crash leaves a claim, not an absence), and the claim only
    becomes terminal when a real ledger receipt — `DELIVERED` or `DUPLICATE`,
    naming *this* fact's delivery key — comes back. A claim without a receipt is
    reconciled through the ledger's own `reconcile(delivery_key)`, never re-sent
    blind, and never reported as settled.
4.  **Hand over** only `TERMINAL_VERIFIED` and `FAILED`, through the single
    injected #423 notifier, under a typed subject the grant authorized. Nothing
    else is dispatchable, and a watch with no authorized subject is a named
    `BLOCKED_*` refusal rather than a message with an empty field.
5.  **Report** its own vitals — `last_tick`, cursor age, backlog, unconfirmed
    hand-overs, consecutive failures — plus bounded, de-bounced warnings.
6.  **Save** the state atomically, then return a tick document.

Nothing in this module sends a message, starts a process, or writes anywhere
except the one state file.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .contract import (
    BLOCKED_WORDS,
    DISPATCHABLE,
    EXIT_BLOCKED_INPUT,
    EXIT_REFUSED,
    EXIT_SETTLED,
    EXIT_UNCONFIRMED,
    LIVE_STATUSES,
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
from .notifier import (
    BLOCKED_LEDGER,
    NotifierUnavailable,
    delivery_subject,
    payload_for,
    receipt_identity,
)
from .sources import LegacyFactsSource, observe_watch
from .state import STALE_AFTER_SECONDS, DurableState

#: Warnings a tick may raise. Each is a fact about the monitor, not about the
#: far end, and each is de-bounced so a permanently offline route cannot become
#: a message every tick.
WARN_STALE = "STALE_MONITOR"
WARN_BACKLOG = "BACKLOG"
WARN_UNOBSERVED = "UNOBSERVED"
WARN_UNKNOWN_RECEIPTS = "UNKNOWN_RECEIPTS"
WARN_UNKNOWN_OBSERVATION = "UNKNOWN_OBSERVATION"
WARN_OFFLINE = "SOURCE_OFFLINE"
WARN_DENIED = "SOURCE_DENIED"
WARN_BLOCKED_LEDGER = "BLOCKED_LEDGER"
WARN_BLOCKED_SUBJECT = "BLOCKED_SUBJECT"
WARN_FAILURES = "CONSECUTIVE_FAILURES"

#: The two states a ledger uses to say "this fact arrived". `DUPLICATE` counts
#: because it means an earlier attempt of *this* delivery landed; both still
#: need a delivery key that names this fact.
CONFIRMING = frozenset({"DELIVERED", "DUPLICATE"})


@dataclass(frozen=True)
class _Receipt:
    """What the ledger actually said, with nothing inferred."""
    state: str
    delivered: bool
    remote_id: str | None
    delivery_key: str | None
    why: str | None = None


@dataclass(frozen=True)
class _Outcome:
    """What this tick did about one dispatchable fact."""
    sent: bool
    receipt: str | None
    confirmed: bool = False
    remote_id: str | None = None
    why: str | None = None
    blocked: bool = False


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
        outstanding = 0
        blocked_ledger = blocked_subject = False
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
            fingerprint = dispatch_fingerprint(observation)
            row = {"key": key, "view": observation.get("view"), "status": status,
                   "reason": reason, "dispatched": False, "receipt": None,
                   "confirmed": False}

            if status == STATUS_DENIED:
                # An authorization *claim* the source refused carries the delivery
                # word it corresponds to (`BLOCKED_CI_SUBJECT`), so the tick says
                # which dimension failed instead of a bare `DENIED`. It is a
                # blocked subject: the fact is real and it may not be sent.
                native = observation.get("native")
                word = native.get("blocked") if isinstance(native, Mapping) else None
                if isinstance(word, str) and word in BLOCKED_WORDS:
                    row["receipt"] = word
                    blocked_subject = True

            if status in (STATUS_UNKNOWN, STATUS_OFFLINE, STATUS_STALE, STATUS_DENIED):
                # A correct UNKNOWN is the monitor *working*; only an exception
                # escaping the source is a failure of the tick itself.
                kind = {STATUS_OFFLINE: WARN_OFFLINE, STATUS_DENIED: WARN_DENIED,
                        STATUS_STALE: WARN_STALE}.get(status, WARN_UNKNOWN_OBSERVATION)
                self._alert(document, kind=kind, key=f"{key}|{status}|{reason}",
                            status=status, reason=reason, raised=warnings)

            if status in DISPATCHABLE:
                outcome = self._settle(key=key, fingerprint=fingerprint, watch=watch,
                                       observation=observation, status=status,
                                       reason=reason, row=row, document=document)
                dispatched_any = dispatched_any or outcome.sent
                dispatched += 1 if outcome.sent else 0
                row["dispatched"] = outcome.sent
                row["receipt"] = outcome.receipt
                row["confirmed"] = outcome.confirmed
                if outcome.remote_id:
                    row["remote_id"] = outcome.remote_id
                if outcome.why:
                    row["receipt_why"] = outcome.why
                if outcome.blocked:
                    if outcome.receipt == BLOCKED_LEDGER:
                        blocked_ledger = True
                        blocked_kind = WARN_BLOCKED_LEDGER
                    else:
                        blocked_subject = True
                        blocked_kind = WARN_BLOCKED_SUBJECT
                    self._alert(document, kind=blocked_kind, key=f"{key}|{outcome.receipt}",
                                status=status, reason=outcome.receipt, raised=warnings)
                elif outcome.confirmed:
                    if outcome.receipt == "DELIVERED":
                        delivered += 1
                    else:
                        duplicate += 1
                else:
                    # Attempted (or reconciled) and still no receipt. This is the
                    # #495 §4.1 defect made loud: it is NOT a duplicate and NOT
                    # settled, however many ticks have seen the same fact.
                    unknown_receipts += 1
                    outstanding += 1
                    self._alert(document, kind=WARN_UNKNOWN_RECEIPTS,
                                key=f"{key}|receipt|{outcome.receipt}", status=status,
                                reason=outcome.receipt, raised=warnings)
            else:
                # A watch that may not be handed over still owes the operator an
                # answer about an earlier hand-over of the *same* fact.
                claim = self._state.claim(document, key, fingerprint)
                if claim is not None and not self._state.confirmation(claim):
                    outstanding += 1
                    row["receipt"] = "DELIVERY_UNKNOWN"
                    row["receipt_why"] = ("an earlier hand-over of this fact was never "
                                          "confirmed by the ledger")

            rows.append(row)

        backlog = sum(1 for row in rows if row["status"] not in
                      (STATUS_TERMINAL_VERIFIED, STATUS_FAILED))
        # A backlog is not one thing. "This run is still running" is *work in
        # flight*: the monitor looked, it knows what it saw, and a supervisor may
        # be told to look again. "I could not see" — UNKNOWN, OFFLINE, STALE,
        # DENIED — is not work in flight and is not a verdict; it is a fact the
        # monitor could not decide. #495 Wave 16 §4.2 measured the consequence of
        # flattening the two: an expired or unobservable watch reached the
        # supervisor as `TICKED` exit 0, which is the number that means "nothing
        # to do here". So the two are counted apart and they leave by different
        # doors, and RUNNING is never dressed up as FAILED to make a code say no.
        unobserved = sum(1 for row in rows
                         if row["status"] not in (STATUS_TERMINAL_VERIFIED, STATUS_FAILED)
                         and row["status"] not in LIVE_STATUSES)
        ok = not blocked_ledger and not blocked_subject and not hard_failure
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
        if unobserved:
            warnings.append({"code": WARN_UNOBSERVED, "status": None, "reason": None,
                             "why": f"{unobserved} watch(es) have no terminal verdict "
                                    f"and no work in flight; the monitor could not "
                                    f"decide them, so this tick is not a success"})
        if blocked_ledger:
            warnings.append({"code": WARN_BLOCKED_LEDGER, "status": None, "reason": None,
                             "why": "a terminal fact is waiting and no #423 notifier is "
                                    "bound; nothing was delivered and nothing will be "
                                    "substituted for it"})
        if blocked_subject:
            warnings.append({"code": WARN_BLOCKED_SUBJECT, "status": None, "reason": None,
                             "why": "a terminal fact has no typed, authorized delivery "
                                    "subject; nothing was sent and none was invented"})
        if outstanding:
            warnings.append({"code": WARN_UNKNOWN_RECEIPTS, "status": None, "reason": None,
                             "why": f"{outstanding} fact(s) were handed over and have no "
                                    f"ledger receipt; they are not settled"})
        failures = int(document.get("consecutive_failures") or 0)
        if failures:
            warnings.append({"code": WARN_FAILURES, "status": None, "reason": None,
                             "why": f"{failures} consecutive tick(s) did not succeed"})
        self._state.save(document)

        if blocked_ledger or blocked_subject:
            state, exit_code = STATE_BLOCKED_INPUT, EXIT_BLOCKED_INPUT
        elif unknown_receipts or outstanding:
            # §4.1: a durable cursor entry is an attempt until the ledger says
            # otherwise. An unconfirmed hand-over keeps the tick out of SETTLED.
            state, exit_code = STATE_UNCONFIRMED, EXIT_UNCONFIRMED
        elif stale:
            state, exit_code = STATE_UNCONFIRMED, EXIT_UNCONFIRMED
        elif unobserved:
            # No terminal verdict and no work in flight: the monitor looked and
            # could not decide (expired, never issued, offline, denied, capped).
            # That is `UNCONFIRMED`, never `SETTLED` and never a 0 a supervisor
            # can read as "nothing here".
            state, exit_code = STATE_UNCONFIRMED, EXIT_UNCONFIRMED
        elif backlog:
            # Every outstanding watch is real work still in flight, and nothing
            # abnormal was seen: `TICKED`, and the tick's own `counts` say which
            # progression statuses are live.
            state, exit_code = STATE_TICKED, EXIT_SETTLED
        else:
            # Every question has a terminal verdict and every delivery is settled.
            state, exit_code = STATE_SETTLED, EXIT_SETTLED

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
            "unsettled": outstanding,
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

    # -- one fact -----------------------------------------------------------

    def _settle(self, *, key: str, fingerprint: str, watch: Mapping[str, Any],
                observation: Mapping[str, Any], status: str, reason: str | None,
                row: dict, document: dict[str, Any]) -> _Outcome:
        """Decide what to do about one dispatchable fact, and record what is true."""
        prior = self._state.claim(document, key, fingerprint)
        if prior is not None:
            if self._state.confirmation(prior):
                # A receipt for exactly this fact already exists. Nothing is sent
                # again — and nothing has to be asked, because the answer is on
                # disk with the ledger's own delivery key beside it.
                row["attempts"] = int(prior.get("attempts") or 0)
                return _Outcome(sent=False, receipt="DUPLICATE", confirmed=True,
                                remote_id=prior.get("remote_id"),
                                why="a ledger receipt for this fact is already recorded")
            return self._reconcile(key=key, fingerprint=fingerprint, prior=prior,
                                   row=row, document=document)

        if self._notifier is None:
            return _Outcome(sent=False, receipt=BLOCKED_LEDGER, blocked=True,
                            why="no #423 notifier is bound; nothing was delivered and "
                                "nothing will be substituted for it")
        try:
            source_name, payload = payload_for(observation, watch,
                                               observed_at=_iso(self._clock()))
            subject = delivery_subject(observation, watch)
        except MonitorRefused as refusal:
            # No authorized subject is a *refusal*, not a payload with an empty
            # field: nothing reaches the ledger and the tick says why.
            row["receipt_why"] = refusal.why
            return _Outcome(sent=False, receipt=refusal.code, blocked=True,
                            why=refusal.why)

        # The claim is written before the send, and it says ATTEMPTED. A crash
        # from here on leaves an outstanding claim a later tick can reconcile —
        # not an empty cursor that reads as "never tried" and invites a blind
        # second message.
        self._state.begin(document, key=key, fingerprint=fingerprint,
                          status=status, reason=reason)
        self._state.save(document)
        try:
            answer = self._notifier.observe_payload(source_name, payload)
        except Exception as exc:  # noqa: BLE001 - the notifier is not ours
            receipt = _Receipt("DELIVERY_UNKNOWN", False, None, None,
                               f"{type(exc).__name__}: {exc}"[:200])
        else:
            receipt = _receipt(answer)
        return self._judge(key=key, fingerprint=fingerprint, source=source_name,
                           subject=subject, receipt=receipt, row=row,
                           document=document, attempts=1, sent=True)

    def _judge(self, *, key: str, fingerprint: str, source: str, subject: str,
               receipt: _Receipt, row: dict, document: dict[str, Any],
               attempts: int, sent: bool) -> _Outcome:
        """Turn a ledger answer into a cursor fact — green only with a real receipt."""
        expected = receipt_identity(source, subject)
        keyed = receipt.delivery_key
        if keyed is not None and not keyed.startswith(expected):
            why = (f"the receipt names {keyed[:80]!r}, not this fact "
                   f"({expected}...); a receipt for another delivery does not settle "
                   f"this one")
            self._state.settle_unconfirmed(
                document, key=key, fingerprint=fingerprint,
                delivery_state="IDENTITY_MISMATCH", delivery_key=None,
                attempts=attempts, why=why)
            return _Outcome(sent=sent, receipt="IDENTITY_MISMATCH", confirmed=False,
                            why=why)

        if receipt.state in CONFIRMING and receipt.delivered and keyed is not None:
            self._state.confirm(document, key=key, fingerprint=fingerprint,
                                delivery_state=receipt.state, delivery_key=keyed,
                                remote_id=receipt.remote_id, attempts=attempts)
            return _Outcome(sent=sent, receipt=receipt.state, confirmed=True,
                            remote_id=receipt.remote_id)

        why = receipt.why
        if keyed is None and receipt.state in CONFIRMING:
            why = (f"the ledger said {receipt.state} but named no delivery key; a "
                   f"receipt with no identity cannot be bound to this fact")
        reported = "DELIVERY_UNKNOWN" if receipt.state in CONFIRMING else receipt.state
        self._state.settle_unconfirmed(
            document, key=key, fingerprint=fingerprint,
            delivery_state=receipt.state, delivery_key=keyed,
            attempts=attempts, why=why)
        return _Outcome(sent=sent, receipt=reported, confirmed=False, why=why)

    def _reconcile(self, *, key: str, fingerprint: str, prior: dict[str, Any],
                   row: dict, document: dict[str, Any]) -> _Outcome:
        """Re-ask the ledger about the SAME delivery. Never a blind re-send.

        #423's `reconcile(delivery_key)` re-issues the same call under the same
        transport key inside its dedupe window; that is the only retry this
        monitor is allowed to make, because it is the ledger's decision and not
        an assumption here. A notifier that cannot be asked, or a claim that
        never learned its key (the send died before an answer existed), leaves
        the fact unconfirmed: there is nothing to ask about.
        """
        attempts = int(prior.get("attempts") or 0) + 1
        row["attempts"] = attempts
        stored = prior.get("delivery_key")
        stored = stored if isinstance(stored, str) and stored.strip() else None
        reconcile = (getattr(self._notifier, "reconcile", None)
                     if self._notifier is not None else None)
        if stored is None or not callable(reconcile):
            why = ("this fact was handed over and no receipt came back. "
                   + ("No delivery key was ever recorded, so there is nothing to ask "
                      "the ledger about" if stored is None else
                      "The bound notifier cannot be re-asked about a delivery key")
                   + ", and nothing is re-sent: an unconfirmed hand-over stays "
                     "unconfirmed until a receipt exists.")
            self._state.settle_unconfirmed(
                document, key=key, fingerprint=fingerprint,
                delivery_state=prior.get("delivery_state") or "DELIVERY_UNKNOWN",
                delivery_key=stored, attempts=attempts, why=why)
            return _Outcome(sent=False, receipt="DELIVERY_UNKNOWN", confirmed=False,
                            why=why)

        try:
            receipt = _receipt(reconcile(stored))
        except Exception as exc:  # noqa: BLE001 - the notifier is not ours
            receipt = _Receipt("DELIVERY_UNKNOWN", False, None, stored,
                               f"{type(exc).__name__}: {exc}"[:200])
        if receipt.delivery_key is not None and receipt.delivery_key != stored:
            why = ("the ledger answered a different delivery key than the one it was "
                   "asked about; that receipt cannot settle this fact")
            self._state.settle_unconfirmed(
                document, key=key, fingerprint=fingerprint,
                delivery_state="IDENTITY_MISMATCH", delivery_key=stored,
                attempts=attempts, why=why)
            return _Outcome(sent=False, receipt="IDENTITY_MISMATCH", confirmed=False,
                            why=why)

        if receipt.state in CONFIRMING and receipt.delivered:
            self._state.confirm(document, key=key, fingerprint=fingerprint,
                                delivery_state=receipt.state, delivery_key=stored,
                                remote_id=receipt.remote_id, attempts=attempts)
            return _Outcome(sent=False, receipt=receipt.state, confirmed=True,
                            remote_id=receipt.remote_id,
                            why="the ledger confirmed the earlier hand-over")

        reported = "DELIVERY_UNKNOWN" if receipt.state in CONFIRMING else receipt.state
        self._state.settle_unconfirmed(
            document, key=key, fingerprint=fingerprint,
            delivery_state=receipt.state, delivery_key=stored,
            attempts=attempts, why=receipt.why)
        return _Outcome(sent=False, receipt=reported, confirmed=False,
                        why=receipt.why)


def _receipt(answer: Any) -> _Receipt:
    """Read a #423 Observation (or a mapping) for its delivery state.

    Only an explicit `DELIVERED`/`DUPLICATE` carrying `delivered is True` *and*
    a delivery key counts. A notifier that returned nothing says UNKNOWN — it
    does not say "probably fine" — and a state without the flag is a claim
    without the receipt.
    """
    if answer is None:
        return _Receipt("DELIVERY_UNKNOWN", False, None, None,
                        "the notifier returned nothing")
    state = getattr(answer, "state", None)
    delivered = getattr(answer, "delivered", None)
    remote = getattr(answer, "remote_id", None)
    key = getattr(answer, "delivery_key", None)
    why = getattr(answer, "why", None)
    if isinstance(answer, Mapping):
        state = answer.get("state", state)
        delivered = answer.get("delivered", delivered)
        remote = answer.get("remote_id", remote)
        key = answer.get("delivery_key", key)
        why = answer.get("why", why)
    state = str(state or "DELIVERY_UNKNOWN")
    key = key.strip() if isinstance(key, str) and key.strip() else None
    remote = remote if isinstance(remote, str) else None
    why = why if isinstance(why, str) else None
    if state in CONFIRMING and delivered is not True:
        # A state without the flag is a claim without the receipt.
        return _Receipt("DELIVERY_UNKNOWN", False, remote, key,
                        why or f"{state} without delivered=True is a claim, not a receipt")
    return _Receipt(state, delivered is True, remote, key, why)


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
