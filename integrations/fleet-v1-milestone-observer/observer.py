#!/usr/bin/env python3
"""#424 — the tick that survives the chat, not a second event system.

WHAT THIS IS
------------
#417's flow is: GPT dispatches, returns immediately; later, real evidence about
a CloudCLI Run or a GitHub Actions run has to reach the owner on Feishu.
#423 built the delivery half (normalize -> decide -> deterministic template ->
one injected provider -> claim/receipt ledger) and #423's README names its one
unwritten seam:

    class SourceRead(Protocol):
        def read(self, source: str, ref: str) -> Mapping[str, Any]: ...

`integrations/fleet-v1-milestone-sources/` (owned by the Windows lane) is that
seam's production half. THIS module is the thing that owns a **tick**: which
refs are being watched, when each may be read again, what a read's refusal
means, and whether the result is allowed to be applied to the one Tokyo
provider. It owns no source client, no provider, no credential and no model.

It is deliberately not:
  * a ledger — the receipt ledger is #423's SQLite table. Deleting this
    module's state file must not cause a second message, and a test proves it;
  * an event bus — no subscribers, no queue, no thread;
  * a scheduler for agents — it wakes itself, reads facts, and hands them to
    the notifier. It does not start or continue a Fleet Run;
  * a second decision engine — the outcome words are #423's `decide()`.

WHAT IT ADDS AROUND `read`
--------------------------
1. **Scope before delivery.** A watch entry names the device, and (for a Run)
   the boot the dispatch was bound to, and (for CI) the exact commit that may
   qualify. A payload that answers for another device, another boot or another
   commit is settled UNKNOWN and never reaches `observe_payload` — the
   classifier cannot turn another ref's green into this watch's milestone.
2. **Backoff and bounds per source.** A refused read that can clear
   (RUN_NOT_FOUND while a run is being indexed, a timeout) makes the entry
   wait. Each TICK reads a source at most `max_reads_per_source` times, with a
   `min_interval_seconds` distance between them, ordered least-recently-read
   first so the budget rotates. A refusal that cannot clear (permission,
   malformed payload, a configuration mismatch) is terminal for that ref
   instead of being retried every tick forever.
3. **Restart in a file that is not a ledger.** Scheduling state (attempts,
   next attempt, last code, last delivery key) is a small JSON file written
   atomically. It is not consulted to decide whether something was delivered:
   that is the ledger's answer, which is why losing this file cannot resend.
4. **A bounded periodic entry point** (`run_periodic`) whose sleep and clock
   are injected. Starting it as a service is a separate, later release and is
   NOT done here.

THE ONE PROVIDER
----------------
The observer does not construct a notifier. The caller injects a
`MilestoneNotifier` bound to the one Tokyo outbound `HumanControlProvider`
(`runtime/direct_lark_notify.py` + `runtime/message_transport.py` in
production). Every delivery for this observer goes through that single object,
so there is one recipient, one capability probe and one receipt ledger. The
optional caretaker summarizer is passed through to #423, which calls it once
per first delivery and keeps the deterministic template when it fails.

AUTHORITY BOUNDARY
------------------
No network, no `lark-cli`, no credential, no service, no clock other than the
injected one, no model in this module. `run_once` returns facts; it never
claims REAL_FEISHU_DELIVERY — that is the provider receipt's word.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

SCHEMA = "serveros/fleet-milestone-observer.v1"
STATE_SCHEMA = "serveros/fleet-milestone-observer-state.v1"

# The two sources a watch may name. `phase` is a signed milestone a person
# enters; it is delivered by calling the notifier directly, not by polling.
WATCH_SOURCES = ("cloudcli", "github_actions")

# Observer states. These are NOT milestone outcomes: they say what the tick did
# with a ref, and they never replace #423's `decide()` word in a row.
UNKNOWN = "UNKNOWN"
NO_EVENT = "NO_EVENT"
CARD_ONLY = "CARD_ONLY"
DELIVERED = "DELIVERED"
DUPLICATE = "DUPLICATE"
DELIVERY_UNKNOWN = "DELIVERY_UNKNOWN"
UNKNOWN_REPLAY = "UNKNOWN_REPLAY"
SKIPPED_BACKOFF = "SKIPPED_BACKOFF"
RATE_LIMITED = "RATE_LIMITED"
NOT_QUALIFYING = "NOT_QUALIFYING"
EXPIRED = "EXPIRED"
REFUSED = "REFUSED"
SCOPE_MISMATCH = "SCOPE_MISMATCH"
SOURCE_SHA_MISMATCH = "SOURCE_SHA_MISMATCH"
BOOT_SCOPE_MISMATCH = "BOOT_SCOPE_MISMATCH"
DEVICE_SCOPE_MISMATCH = "DEVICE_SCOPE_MISMATCH"
REF_MISMATCH = "REF_MISMATCH"
MILESTONE_REFUSED = "MILESTONE_REFUSED"

# Read failures that can clear themselves. Everything else is a configuration
# mismatch: retrying it every tick would be noise, not observation.
_TRANSIENT_CODES = frozenset({
    "RUN_NOT_FOUND", "UNKNOWN_RUN", "SOURCE_TIMEOUT", "SOURCE_UNAVAILABLE",
    "NETWORK", "DEADLINE", "TIMEOUT", "OSError", "ConnectionError",
    "TimeoutError", "socket.timeout",
})


def _atomic_writer():
    """The atomic JSON write this tree has ALREADY declared.

    `runtime/actuator-contract.yaml` declares `atomic-write-commit` and
    `atomic-write-temp-cleanup` for `runtime/ability_projection.py`'s
    `write_atomic`. Reusing that chokepoint is deliberate: a second raw
    `os.replace`/`os.unlink` in this directory would be a new, undeclared
    mutation reachable from a shipped surface. The temporary file is created
    with `mkstemp` (mode 0600) in the destination directory, so the state file
    lands private and atomically without this module owning a mutation verb.
    """
    name = "serveros_runtime_ability_projection"
    module = sys.modules.get(name)
    if module is None:
        path = (Path(__file__).resolve().parents[2]
                / "runtime" / "ability_projection.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module.write_atomic


def _load_notify():
    """#423's module, loaded by path so this composition imports nothing global."""
    name = "serveros_fleet_v1_milestone_notify"
    module = sys.modules.get(name)
    if module is None:
        path = (Path(__file__).resolve().parents[1]
                / "fleet-v1-milestone-notify" / "milestone_notify.py")
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


NOTIFY = _load_notify()


def _text(value: Any, field: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return value.strip()[:maximum]


def _when(value: Any, field: str) -> float | None:
    """ISO-8601 to epoch seconds. `None`/empty is "not set", not an error."""
    if value in (None, ""):
        return None
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        raise ValueError(f"{field} must be an ISO-8601 timestamp") from None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.timestamp()


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()


def _now(clock: Callable[[], Any]) -> float:
    value = clock()
    if isinstance(value, bool):
        raise ValueError("clock must return datetime or epoch seconds")
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, datetime):
        moment = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return moment.timestamp()
    raise ValueError("clock must return datetime or epoch seconds")


@dataclass(frozen=True)
class Watch:
    """One ref this observer is responsible for, and the scope that qualifies it.

    `ref` is the native Run id for CloudCLI and the workflow-run id for GitHub
    Actions — the ids #423's normalizer keys on. It is not a delivery id: #416
    established that a deliveryId can answer RUN_NOT_FOUND while the runId
    still reads.
    """

    source: str
    ref: str
    device_ref: str
    boot_id: str | None = None
    source_sha: str | None = None
    qualifies: bool = True
    expires_at: str | None = None

    def __post_init__(self):
        if self.source not in WATCH_SOURCES:
            raise ValueError(f"source must be one of {list(WATCH_SOURCES)}")
        _text(self.ref, "ref", 128)
        _text(self.device_ref, "device_ref", 256)
        if self.source == "cloudcli" and not self.boot_id:
            # The dispatch committed a boot. A watch without one cannot tell a
            # run of THIS boot from a run of a previous one, and #423 would
            # happily call a self-consistent payload green. Refuse the watch
            # rather than observe a fact that cannot be scoped.
            raise ValueError(
                "a cloudcli watch must name the boot_id the dispatch was bound "
                "to; a run from another boot is not this task's fact")
        if self.source == "github_actions" and not self.source_sha:
            raise ValueError(
                "a CI watch must name the exact source_sha it may qualify; a "
                "green on another commit is not this milestone")
        if self.source_sha is not None:
            _text(self.source_sha, "source_sha", 64)
        if self.boot_id is not None:
            _text(self.boot_id, "boot_id", 128)
        if not isinstance(self.qualifies, bool):
            raise ValueError("qualifies must be a bool")
        _when(self.expires_at, "expires_at")

    @property
    def key(self) -> str:
        return f"{self.source}:{self.ref}"


@dataclass(frozen=True)
class Options:
    """Bounds. Defaults are one tick per minute with a bounded retry cycle."""

    max_reads_per_source: int = 8
    min_interval_seconds: float = 0.0
    base_backoff_seconds: float = 30.0
    max_backoff_seconds: float = 900.0
    # A delivery left UNKNOWN is re-asked under the SAME key on a later tick,
    # inside #423's transport window. This is not a resend: the transport
    # deduplicates the key, and outside the window #423 refuses.
    reconcile_unknown: bool = True

    def __post_init__(self):
        if self.max_reads_per_source < 1:
            raise ValueError("max_reads_per_source must be at least 1")
        for name in ("min_interval_seconds", "base_backoff_seconds",
                     "max_backoff_seconds"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or value < 0:
                raise ValueError(f"{name} must be a non-negative number")


class ObserverState:
    """Scheduling facts only. The delivery receipt lives in #423's ledger.

    A deliberate consequence: deleting this file must not cause a resend. On a
    fresh file the observer reads again, the notifier finds the ledger row and
    answers DUPLICATE — the ledger, not this file, decides what was delivered.
    """

    def __init__(self, path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            raw = self.path.read_text(encoding="utf-8")
        except OSError:
            return {"schema": STATE_SCHEMA, "entries": {}}
        try:
            doc = json.loads(raw)
        except ValueError:
            # A torn file is not permission to resend: start the scheduling
            # bookkeeping over; the ledger still owns delivery.
            return {"schema": STATE_SCHEMA, "entries": {}}
        if not isinstance(doc, dict) or doc.get("schema") != STATE_SCHEMA:
            return {"schema": STATE_SCHEMA, "entries": {}}
        if not isinstance(doc.get("entries"), dict):
            doc["entries"] = {}
        return doc

    def save(self, doc: dict) -> None:
        # Reuses the tree's declared atomic writer; see `_atomic_writer`.
        _atomic_writer()(self.path, doc)


def _scope_fingerprint(watch: Watch) -> str:
    """What redefining a watch may change without changing its ref.

    A re-dispatch may correct the boot, extend the window or move a Draft to
    ready while the runId stays the same. Carrying the previous watch's
    `terminal` into that would be a missed milestone, so a changed fingerprint
    resets the scheduling entry. It does NOT touch the receipt ledger: what was
    delivered stays delivered.
    """
    return "|".join([
        watch.source, watch.ref, watch.device_ref, watch.boot_id or "-",
        watch.source_sha or "-", "1" if watch.qualifies else "0",
        watch.expires_at or "-",
    ])


def _entry(doc: dict, watch: Watch) -> dict:
    entries = doc.setdefault("entries", {})
    entry = entries.get(watch.key)
    if not isinstance(entry, dict):
        entry = {}
        entries[watch.key] = entry
    fingerprint = _scope_fingerprint(watch)
    if entry.get("scope") != fingerprint:
        entry.clear()
        entry["scope"] = fingerprint
    entry.setdefault("attempts", 0)
    try:
        entry["attempts"] = max(0, int(entry.get("attempts") or 0))
    except (TypeError, ValueError):
        entry["attempts"] = 0
    entry.setdefault("next_attempt_at", None)
    entry.setdefault("last_read_at", None)
    entry.setdefault("last_code", None)
    entry.setdefault("last_state", None)
    entry.setdefault("last_delivery_key", None)
    entry.setdefault("terminal", False)
    entry.setdefault("needs_reconcile", False)
    return entry


def _row(watch: Watch, *, state: str, outcome: str | None = None,
         delivery_key: str | None = None, delivered: bool = False,
         code: str | None = None, why: str = "",
         message: str | None = None) -> dict:
    return {
        "source": watch.source, "ref": watch.ref,
        "device_ref": watch.device_ref, "state": state,
        "outcome": outcome, "delivery_key": delivery_key,
        "delivered": delivered, "code": code, "why": why,
        "message": message,
    }


class MilestoneObserver:
    """One bounded tick over a set of watched refs.

    `source` is any object implementing #423's `SourceRead`
    (`read(source, ref) -> Mapping`). `notifier` is #423's `MilestoneNotifier`
    bound to the one Tokyo provider; this class never constructs either.
    """

    def __init__(self, *, source, notifier, state_path, watches: Sequence[Watch],
                 clock: Callable[[], Any] | None = None,
                 options: Options | None = None):
        self.source = source
        self.notifier = notifier
        self.state = ObserverState(state_path)
        self.watches = tuple(watches)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.options = options or Options()
        keys = [watch.key for watch in self.watches]
        if len(set(keys)) != len(keys):
            raise ValueError("two watches name the same source:ref")

    # -- one tick ----------------------------------------------------------

    def run_once(self, watches: Sequence[Watch] | None = None) -> dict:
        """Read every due ref at most once and return what happened.

        Never raises for a source or provider failure: a failure becomes an
        UNKNOWN row, because a tick that crashes is a tick that stops guarding
        the other refs. It does not swallow programming errors in the payload
        scopes — those are settled as typed rows too.
        """
        batch = tuple(watches if watches is not None else self.watches)
        now = _now(self.clock)
        doc = self.state.load()
        rows: list[dict] = []
        reads: dict[str, int] = {}
        source_last: dict[str, float] = {}
        for watch in batch:
            entry = _entry(doc, watch)
            last = entry.get("last_read_at")
            if isinstance(last, (int, float)):
                source_last[watch.source] = max(source_last.get(watch.source, 0.0), last)

        # Least-recently-read first. A tick that always took the first N refs
        # of a source would never reach the rest once a source owns more refs
        # than `max_reads_per_source`; ordering by the read cursor is what
        # makes the bound a budget instead of a permanent cut.
        batch = tuple(sorted(batch, key=lambda w: self._due_key(doc, w)))
        for watch in batch:
            entry = _entry(doc, watch)
            if entry.get("terminal"):
                rows.append(_row(
                    watch, state=entry.get("last_state")
                    or entry.get("last_code") or EXPIRED,
                    outcome=UNKNOWN, code=entry.get("last_code"),
                    why="this ref is no longer observed; the scope mismatch "
                        "or expiry is recorded"))
                continue
            if not watch.qualifies:
                entry["terminal"] = True
                entry["last_code"] = NOT_QUALIFYING
                rows.append(_row(watch, state=NOT_QUALIFYING, outcome=UNKNOWN,
                                 code=NOT_QUALIFYING,
                                 why="the caller marked this ref non-qualifying "
                                     "(e.g. a Draft PR); no read is made, because "
                                     "a green here is not the milestone"))
                continue
            expires = _when(watch.expires_at, "expires_at")
            if expires is not None and now >= expires:
                entry["terminal"] = True
                entry["last_code"] = EXPIRED
                rows.append(_row(watch, state=EXPIRED, outcome=UNKNOWN,
                                 code=EXPIRED,
                                 why=f"the observation window ended at {watch.expires_at}"))
                continue
            # A previous tick left a delivery UNKNOWN. Re-ask the SAME key once
            # per tick inside the transport window; a recovery is reported and
            # no new milestone is observed. Outside the window #423 refuses.
            if (entry.get("needs_reconcile") and entry.get("last_delivery_key")
                    and self.options.reconcile_unknown):
                try:
                    answer = self.notifier.reconcile(entry["last_delivery_key"])
                except Exception:  # noqa: BLE001 - a refused reconcile is unknown
                    answer = None
                if answer is not None and answer.state in (DELIVERED, DUPLICATE):
                    entry["needs_reconcile"] = False
                    entry["last_state"] = answer.state
                    entry["attempts"] = 0
                    entry["next_attempt_at"] = None
                    rows.append(_row(
                        watch, state=answer.state, outcome=answer.outcome,
                        delivery_key=entry["last_delivery_key"],
                        delivered=True, why=answer.why))
                    continue
            next_at = entry.get("next_attempt_at")
            if isinstance(next_at, (int, float)) and now < next_at:
                rows.append(_row(watch, state=SKIPPED_BACKOFF, outcome=UNKNOWN,
                                 code=entry.get("last_code"),
                                 why=f"backoff until {_iso(next_at)}"))
                continue
            if reads.get(watch.source, 0) >= self.options.max_reads_per_source:
                rows.append(_row(watch, state=RATE_LIMITED, outcome=UNKNOWN,
                                 why=f"at most {self.options.max_reads_per_source} "
                                     f"{watch.source} reads per tick"))
                continue
            gap = self.options.min_interval_seconds
            if gap and watch.source in source_last:
                if now - source_last[watch.source] < gap:
                    rows.append(_row(watch, state=RATE_LIMITED, outcome=UNKNOWN,
                                     why=f"minimum interval {gap}s between "
                                         f"{watch.source} reads"))
                    continue

            reads[watch.source] = reads.get(watch.source, 0) + 1
            entry["last_read_at"] = now
            source_last[watch.source] = now
            rows.append(self._observe(watch, entry, now))

        state_error = None
        try:
            self.state.save(doc)
        except OSError as exc:  # a full disk must not stop the tick
            state_error = f"{type(exc).__name__}: {exc}"
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["state"]] = counts.get(row["state"], 0) + 1
        report = {"schema": SCHEMA, "observed_at": _iso(now),
                  "reads": reads, "counts": counts, "rows": rows}
        if state_error:
            report["state_error"] = state_error
        return report

    @staticmethod
    def _due_key(doc: dict, watch: Watch):
        entry = (doc.get("entries") or {}).get(watch.key) or {}
        last = entry.get("last_read_at")
        never = 0 if not isinstance(last, (int, float)) else 1
        return (never, last if isinstance(last, (int, float)) else 0.0,
                watch.source, watch.ref)

    def _observe(self, watch: Watch, entry: dict, now: float) -> dict:
        try:
            payload = self.source.read(watch.source, watch.ref)
        except Exception as exc:  # noqa: BLE001 - every refusal is a fact here
            code = str(getattr(exc, "code", "") or type(exc).__name__)
            return self._refused(watch, entry, now, code,
                                 str(getattr(exc, "detail", "") or exc)[:240],
                                 transient=code in _TRANSIENT_CODES)
        if not isinstance(payload, Mapping):
            return self._refused(watch, entry, now, "BAD_PAYLOAD",
                                 "source returned no mapping", transient=False)

        scope = self._scope(watch, payload)
        if scope is not None:
            code, why, terminal = scope
            if terminal:
                entry["terminal"] = True
            entry["last_code"] = code
            entry["last_state"] = code
            entry["needs_reconcile"] = False
            entry["attempts"] = 0
            entry["next_attempt_at"] = None
            return _row(watch, state=code, outcome=UNKNOWN, code=code, why=why)

        try:
            observation = self.notifier.observe_payload(watch.source, dict(payload))
        except Exception as exc:  # noqa: BLE001 - a refused milestone is not a green
            code = str(getattr(exc, "code", "") or MILESTONE_REFUSED)
            return self._refused(watch, entry, now, code,
                                 str(getattr(exc, "detail", "") or exc)[:240],
                                 transient=False)

        entry["attempts"] = 0
        entry["next_attempt_at"] = None
        entry["last_code"] = None
        entry["last_state"] = observation.state
        entry["needs_reconcile"] = observation.state in (DELIVERY_UNKNOWN,
                                                         UNKNOWN_REPLAY)
        if observation.delivery_key:
            entry["last_delivery_key"] = observation.delivery_key
        return _row(watch, state=observation.state, outcome=observation.outcome,
                    delivery_key=observation.delivery_key,
                    delivered=observation.delivered,
                    why=observation.why, message=observation.message)

    def _refused(self, watch: Watch, entry: dict, now: float,
                 code: str, detail: str, *, transient: bool) -> dict:
        """A read/parse refusal is UNKNOWN. It is retried only if it can clear.

        `transient` is decided from the refusal CODE, not from the exception's
        origin: RUN_NOT_FOUND and transport timeouts can clear, while a
        permission denial or a payload the boundary refused will not become a
        milestone by being read again.
        """
        try:
            attempts = max(0, int(entry.get("attempts") or 0)) + 1
        except (TypeError, ValueError):
            attempts = 1
        entry["attempts"] = attempts
        entry["last_code"] = code
        entry["last_state"] = REFUSED
        if transient or code in _TRANSIENT_CODES:
            backoff = min(self.options.max_backoff_seconds,
                          self.options.base_backoff_seconds * (2 ** (attempts - 1)))
            entry["next_attempt_at"] = now + backoff
            why = (f"{code}: a read that can clear; next attempt after "
                   f"{backoff:g}s")
        else:
            # A payload the boundary refuses is not a measurement that changes
            # on its own. Keep reporting it, but stop spending reads on it.
            entry["terminal"] = True
            entry["next_attempt_at"] = None
            why = f"{code}: refused; this ref cannot become a milestone as read"
        return _row(watch, state=REFUSED, outcome=UNKNOWN, code=code,
                    why=f"{why}" + (f" ({detail})" if detail else ""))

    def _scope(self, watch: Watch, payload: Mapping) -> tuple[str, str, bool] | None:
        """Reject a payload that answers for another ref, device, boot or commit.

        The classifier is allowed to say "this fact is UNKNOWN"; it is never
        allowed to apply one ref's fact to another watch. These checks are the
        only place that distinction is made, and they run before delivery.
        """
        device = payload.get("device_ref")
        if device is not None and str(device) != watch.device_ref:
            return (DEVICE_SCOPE_MISMATCH,
                    f"payload is for {device!r}, watch is for {watch.device_ref!r}",
                    True)
        if watch.source == "cloudcli":
            run = payload.get("run")
            run_id = run.get("runId") if isinstance(run, Mapping) else None
            if str(run_id) != watch.ref:
                return (REF_MISMATCH,
                        f"payload answers for run {run_id!r}, watch is {watch.ref!r}",
                        True)
            if watch.boot_id is not None:
                bound = payload.get("bound_boot_id")
                if str(bound or "") != watch.boot_id:
                    return (BOOT_SCOPE_MISMATCH,
                            f"dispatch was bound to boot {watch.boot_id!r}, "
                            f"payload was read for {bound!r}", True)
            return None
        run = payload.get("workflow_run")
        run_id = run.get("id") if isinstance(run, Mapping) else None
        if str(run_id) != watch.ref:
            return (REF_MISMATCH,
                    f"payload answers for workflow run {run_id!r}, "
                    f"watch is {watch.ref!r}", True)
        head = run.get("head_sha") if isinstance(run, Mapping) else None
        if str(head or "") != watch.source_sha:
            return (SOURCE_SHA_MISMATCH,
                    f"workflow ran on {head!r}; this watch may only qualify "
                    f"{watch.source_sha!r}", True)
        return None

    # -- delivery receipt follow-up ---------------------------------------

    def reconcile(self, delivery_key: str):
        """Ask #423's notifier to re-check the SAME key inside its own window."""
        return self.notifier.reconcile(delivery_key)

    # -- the periodic entry point (not started anywhere) ------------------

    def run_periodic(self, *, interval_seconds: float, sleep: Callable[[float], None],
                     max_ticks: int | None = None):
        """Yield one run_once result per tick. The caller owns the loop.

        This is the seam a future service would drive with a real sleep and a
        real clock. Starting it, unit rendering and restart policy are a
        separate release; nothing here writes a service file or wakes a model.
        """
        if interval_seconds < 0:
            raise ValueError("interval_seconds must be non-negative")
        if max_ticks is not None and max_ticks <= 0:
            return
        tick = 0
        while True:
            yield self.run_once()
            tick += 1
            if max_ticks is not None and tick >= max_ticks:
                return
            sleep(interval_seconds)


__all__ = [
    "MilestoneObserver", "Options", "Watch", "ObserverState",
    "SCHEMA", "STATE_SCHEMA", "WATCH_SOURCES",
    "NO_EVENT", "CARD_ONLY", "DELIVERED", "DUPLICATE", "DELIVERY_UNKNOWN",
    "UNKNOWN_REPLAY", "SKIPPED_BACKOFF", "RATE_LIMITED", "NOT_QUALIFYING",
    "EXPIRED", "REFUSED", "SCOPE_MISMATCH", "SOURCE_SHA_MISMATCH",
    "BOOT_SCOPE_MISMATCH", "DEVICE_SCOPE_MISMATCH", "REF_MISMATCH",
    "MILESTONE_REFUSED",
]
