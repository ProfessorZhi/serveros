#!/usr/bin/env python3
"""Fleet Next Agent Monitor — bounded activation wrapper.

WHAT THIS IS
------------
A one-shot, bounded process that the owner *can* put behind a systemd **user**
unit so the Next-track monitor keeps watching after the ChatGPT turn is gone.
It runs a fixed number of monitor ticks and exits; it is not a daemon, a
supervisor, a scheduler or a second event bus.

WHAT IT IS NOT
--------------
It does not start, stop or restart anything, and it cannot: it imports no
process-launching module and names no service manager. It never touches the
Legacy Fleet's service, socket, port, MCP route, plugin id, OAuth audience,
database, state directory or lock — the isolation checks below refuse a
configuration that would share any of them.

The monitor itself is **injected** (`--monitor-entry module:callable`). That is
deliberate: the upstream one-shot CLI does not exist yet, so this wrapper must be
able to say so instead of pretending. An unresolvable entry is
`BLOCKED_UPSTREAM_ENTRY`, exit 3, zero ticks.

    RED is the current state, not a hypothetical: #470 at 56fcb927 ships
    `tick_once`/`tick_from_file` and no `main`; #446's `runner.py` is the only
    real bounded CLI in this area; the Fleet Next one-shot CLI has no published
    SHA. See docs/integration/FLEET-NEXT-AGENT-MONITOR-ACTIVATION.md.

WHAT A STOP MAY SAY ABOUT THE BATCH
-----------------------------------
`BOUNDED_STOP` is the wrapper finishing, and #495 measured the wrapper saying it
for a monitor that had just refused: the tick's `state`, `exit`, `why` and
`health` were dropped, so `BLOCKED_INPUT` (exit 3) and `UNCONFIRMED` (exit 4)
arrived at a supervisor as exit 0. That is the one thing this wrapper may not do
— report its own stop where the batch's verdict belongs. So:

* a tick's control word is carried through; `BOUNDED_STOP` is emitted **only**
  for a green tick, and is the only word that comes with `completed: true`;
* a row that names no readable state is `UNRECOGNISED`, never "probably fine";
* staleness that nothing measured is `null`, never `false`.

The result is a closed schema: `RESULT_KEYS`, `TICK_RECORD_KEYS` and `HEALTH_KEYS`
are checked by `_close`, so a field added in a hurry fails here rather than in a
supervisor that silently stopped reading it.
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "serveros/fleet-next-agent-monitor-activation.v1"

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_BLOCKED_UPSTREAM = 3
EXIT_UNCONFIRMED = 4
EXIT_UNRECOGNISED = 5

BOUNDED_STOP = "BOUNDED_STOP"
TICK_FAILED = "TICK_FAILED"
LEGACY_UNREADABLE = "LEGACY_UNREADABLE"
NEXT_NOT_READ_ONLY = "NEXT_NOT_READ_ONLY"
MISCONFIGURED = "MISCONFIGURED"
BLOCKED_UPSTREAM_ENTRY = "BLOCKED_UPSTREAM_ENTRY"
UNRECOGNISED = "UNRECOGNISED"

#: The words a monitor tick may answer with. #493's core and #481's Batch CLI
#: publish the same five; anything else is not a status, so it is `UNRECOGNISED`.
TICK_STATES = frozenset({"SETTLED", "TICKED", "UNCONFIRMED", "BLOCKED_INPUT",
                         "REFUSED", UNRECOGNISED})
#: The wrapper's own words: its preflight refusals and its one word for a run in
#: which every tick was green.
ACTIVATION_STATES = frozenset({MISCONFIGURED, BLOCKED_UPSTREAM_ENTRY,
                               LEGACY_UNREADABLE, NEXT_NOT_READ_ONLY,
                               TICK_FAILED, BOUNDED_STOP})
RESULT_STATES = ACTIVATION_STATES | TICK_STATES

#: How bad a tick's word is, for picking one word out of several ticks. Only the
#: tick states are ranked: a preflight word cannot co-occur with a tick.
SEVERITY = {"SETTLED": 0, "TICKED": 1, "UNCONFIRMED": 2, "REFUSED": 3,
            UNRECOGNISED: 4, "BLOCKED_INPUT": 5}

#: The number a supervisor reads when it does not read `state`. Only a bounded
#: stop may be 0. `TICKED` keeps #493's 0 because the monitor itself says
#: `EXIT_SETTLED` for it: the state word, not this number, is what says "still in
#: flight", and inventing a different code for it would put a second vocabulary
#: in the wrapper's mouth.
EXIT_FOR_STATE = {
    BOUNDED_STOP: EXIT_OK, "SETTLED": EXIT_OK, "TICKED": EXIT_OK,
    "REFUSED": EXIT_REFUSED, MISCONFIGURED: EXIT_REFUSED,
    LEGACY_UNREADABLE: EXIT_REFUSED, NEXT_NOT_READ_ONLY: EXIT_REFUSED,
    TICK_FAILED: EXIT_REFUSED,
    "BLOCKED_INPUT": EXIT_BLOCKED_UPSTREAM,
    BLOCKED_UPSTREAM_ENTRY: EXIT_BLOCKED_UPSTREAM,
    "UNCONFIRMED": EXIT_UNCONFIRMED,
    UNRECOGNISED: EXIT_UNRECOGNISED,
}

RESULT_KEYS = frozenset({"schema", "state", "exit", "ticks", "completed", "why",
                         "health", "warnings", "ticks_report"})
TICK_RECORD_KEYS = frozenset({"index", "state", "exit", "why", "backlog",
                              "unknown_receipts", "completed", "health", "warnings"})
HEALTH_KEYS = frozenset({"last_tick", "cursor_age", "stale", "backlog",
                         "unknown_receipts", "reported"})

#: Upper bound on one activation. A unit that wants to run longer re-executes;
#: it does not ask this process to loop.
MAX_TICKS = 64
#: A cursor older than this is a stalled monitor, which is reported, never fixed.
STALE_AFTER_SECONDS = 3600


class ActivationRefused(Exception):
    def __init__(self, code: str, why: str):
        self.code = code
        self.why = why
        super().__init__(f"{code}: {why}")


@dataclass(frozen=True)
class Activation:
    """One Next-track activation. Every field is Next's, none is Legacy's."""

    legacy_root: str
    next_root: str
    state: str
    lock: str
    port: int
    legacy_port: int
    service_name: str
    legacy_service_name: str
    plugin_id: str
    legacy_plugin_id: str
    ticks: int = 1

    def paths(self) -> tuple[tuple[str, str], ...]:
        return (("next_root", self.next_root), ("state", self.state),
                ("lock", self.lock))


def _inside(path: str, root: str) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except (ValueError, OSError):
        return False


def problems_with_activation(activation: Activation) -> list[str]:
    """Every way a Next activation would reach into the Legacy track."""
    problems: list[str] = []

    for name, value in activation.paths():
        if not Path(value).is_absolute():
            problems.append(f"{name} must be an absolute path, got {value!r}")

    legacy_root = activation.legacy_root
    if legacy_root and not Path(legacy_root).is_absolute():
        problems.append("legacy_root must be an absolute path")
    if legacy_root:
        for name, value in activation.paths():
            if Path(value).is_absolute() and _inside(value, legacy_root):
                problems.append(
                    f"{name} {value!r} is inside the legacy root {legacy_root!r}; "
                    "the Next track keeps its own state")

    if not Path(activation.next_root).is_absolute():
        pass  # already reported above
    else:
        for name in ("state", "lock"):
            value = getattr(activation, name)
            if Path(value).is_absolute() and not _inside(value, activation.next_root):
                problems.append(
                    f"{name} {value!r} is outside the Next root "
                    f"{activation.next_root!r}; a stray state file is how two "
                    "tracks end up sharing one")

    if activation.port == activation.legacy_port:
        problems.append(
            f"port {activation.port} is the legacy port; the Next track binds its own")
    if not 0 < activation.port < 65536:
        problems.append(f"port {activation.port} is not a valid port")

    if activation.service_name == activation.legacy_service_name:
        problems.append(
            f"service name {activation.service_name!r} is the legacy service name")
    if not activation.service_name.startswith("serveros-next-"):
        problems.append(
            f"service name {activation.service_name!r} must be namespaced "
            "`serveros-next-*` so a unit listing cannot confuse the two tracks")

    if activation.plugin_id == activation.legacy_plugin_id:
        problems.append(
            f"plugin id {activation.plugin_id!r} is the legacy plugin id; a shared "
            "plugin id is a shared OAuth audience")

    if not isinstance(activation.ticks, int) or not 1 <= activation.ticks <= MAX_TICKS:
        problems.append(
            f"ticks must be an integer in 1..{MAX_TICKS}, got {activation.ticks!r}")

    return problems


def resolve_entry(spec: Any) -> Callable[[], Any]:
    """A monitor tick, from a callable or a `module:callable` spec."""
    if callable(spec):
        return spec
    if not isinstance(spec, str) or ":" not in spec:
        raise ActivationRefused(
            BLOCKED_UPSTREAM_ENTRY,
            f"{spec!r} is not a `module:callable` monitor entry")
    module_name, _, attribute = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001 - any failure is "no entry"
        raise ActivationRefused(
            BLOCKED_UPSTREAM_ENTRY,
            f"{module_name} cannot be imported ({type(exc).__name__}: {exc}); the "
            "upstream one-shot CLI is not published, so there is nothing to run")
    entry = getattr(module, attribute, None)
    if not callable(entry):
        raise ActivationRefused(
            BLOCKED_UPSTREAM_ENTRY,
            f"{module_name}.{attribute} is not callable; the upstream module "
            "exists but has no entry this wrapper can run")
    return entry


def _probe(probe: Callable[[], Any], *, what: str) -> bool:
    """A probe that raises is 'not available', never 'assume yes'."""
    try:
        return bool(probe())
    except Exception:  # noqa: BLE001 - an unreadable probe is an unreadable fact
        return False


def _close(document: dict, keys: frozenset[str], what: str) -> dict:
    """A closed schema is only closed if something refuses the extra key."""
    if set(document) != set(keys):
        raise RuntimeError(
            f"{what} is not the closed schema: extra {sorted(set(document) - keys)}, "
            f"missing {sorted(keys - set(document))}")
    return document


def _count(value: Any) -> int | None:
    """An integer count, or None. `True` is not a count and neither is a string."""
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _result(state: str, *, exit_code: int, ticks: int, why: str, completed: bool = False,
            health: Mapping[str, Any] | None = None,
            warnings: list[dict] | None = None,
            ticks_report: list[dict] | None = None) -> dict:
    """One activation's answer. `health` is None when nothing was measured.

    `{}` used to stand for both "no problems found" and "nothing looked at"; the
    two are not the same answer, and a supervisor cannot tell them apart.
    """
    if state not in RESULT_STATES:
        raise RuntimeError(f"{state!r} is not a state this wrapper may report")
    return _close({
        "schema": SCHEMA, "state": state, "exit": exit_code, "ticks": ticks,
        "completed": bool(completed), "why": why,
        "health": dict(health) if health is not None else None,
        "warnings": list(warnings or []),
        "ticks_report": list(ticks_report or []),
    }, RESULT_KEYS, "the activation result")


def _record(index: int, row: Any) -> dict:
    """One tick's answer, closed, with the monitor's words left where they were.

    Nothing here improves on the tick: a state that is not in the vocabulary is
    `UNRECOGNISED` with the word it actually said, and the tick's own health is
    copied rather than recomputed.
    """
    if not isinstance(row, Mapping):
        return _close({
            "index": index, "state": UNRECOGNISED, "exit": None, "completed": False,
            "why": f"the monitor returned {type(row).__name__}, not a mapping",
            "backlog": None, "unknown_receipts": None, "health": None,
            "warnings": []}, TICK_RECORD_KEYS, "a tick record")

    raw_state = row.get("state")
    # `isinstance` first: a list or dict is unhashable, and a producer bug must
    # not be able to take the supervisor's process down with it.
    accepted = isinstance(raw_state, str) and raw_state in TICK_STATES
    state = raw_state if accepted else UNRECOGNISED
    why = row.get("why")
    why = why if isinstance(why, str) else ""
    if not accepted:
        note = ("the monitor's row names no state; a row without a verdict cannot "
                "be read as a finished one" if raw_state is None else
                f"the monitor reported {raw_state!r}, which is not a state this "
                "wrapper can read")
        why = f"{why}; {note}" if why else note

    health = row.get("health")
    warnings = row.get("warnings")
    # `green` is #481's explicit floor. Absent it the tick's own `SETTLED` is the
    # claim; `green: false` overrules it, so a settled batch whose summary never
    # reached the provider cannot be a completion here either.
    green = row.get("green")
    return _close({
        "index": index,
        "state": state,
        "exit": _count(row.get("exit")),
        "completed": bool(state == "SETTLED" and green is not False),
        "why": why,
        "backlog": _count(row.get("backlog")),
        "unknown_receipts": _count(row.get("unknown_receipts")),
        "health": dict(health) if isinstance(health, Mapping) else None,
        "warnings": ([dict(w) for w in warnings]
                     if isinstance(warnings, list)
                     and all(isinstance(w, Mapping) for w in warnings) else []),
    }, TICK_RECORD_KEYS, "a tick record")


def _effective_state(record: Mapping[str, Any]) -> str:
    """The word to rank a tick by — the producer's, unless it contradicts itself."""
    if record["state"] == "SETTLED" and not record["completed"]:
        return "UNCONFIRMED"
    return record["state"]


def _last_count(records: list[dict], key: str) -> int | None:
    for record in reversed(records):
        if record[key] is not None:
            return record[key]
    return None


def _health(records: list[dict], clock: Callable[[], float],
            cursor_age_seconds: float | None) -> dict:
    """The five things a stalled monitor hides, plus the monitor's own health.

    `stale` is the producer's own boolean when it states one — #493 computes it
    from its configured bound, which this wrapper does not know. When the
    producer is silent the wrapper derives it, and when nothing measured the age
    it is None: unknown, because "not stale" is a claim nobody made.
    """
    reported = None
    for record in reversed(records):
        if isinstance(record["health"], Mapping):
            reported = record["health"]
            break

    age: float | None = None
    if cursor_age_seconds is not None:
        age = float(cursor_age_seconds)
    elif reported is not None:
        reported_age = reported.get("cursor_age")
        if isinstance(reported_age, (int, float)) and not isinstance(reported_age, bool):
            age = float(reported_age)

    producer_stale = reported.get("stale") if reported is not None else None
    if isinstance(producer_stale, bool):
        stale: bool | None = producer_stale
    elif age is not None:
        stale = age > STALE_AFTER_SECONDS
    else:
        stale = None

    return _close({
        "last_tick": float(clock()),
        "cursor_age": age,
        "stale": stale,
        "backlog": _last_count(records, "backlog"),
        "unknown_receipts": _last_count(records, "unknown_receipts"),
        "reported": dict(reported) if reported is not None else None,
    }, HEALTH_KEYS, "health")


def _warnings(records: list[dict], health: Mapping[str, Any]) -> list[dict]:
    """The monitor's warnings as given, then only the ones it did not raise."""
    warnings = [dict(w) for record in records for w in record["warnings"]]
    codes = {w.get("code") for w in warnings}
    if health["stale"] is True and "STALE_MONITOR" not in codes:
        age = health["cursor_age"]
        warnings.append({
            "code": "STALE_MONITOR",
            "why": (f"the cursor is {int(age)}s old, over the "
                    f"{STALE_AFTER_SECONDS}s bound" if age is not None else
                    "the monitor reports a stale cursor, without an age")})
    if health["unknown_receipts"] and "UNKNOWN_RECEIPTS" not in codes:
        warnings.append({"code": "UNKNOWN_RECEIPTS",
                         "why": f"{health['unknown_receipts']} delivery receipts are UNKNOWN"})
    if health["backlog"] and "BACKLOG" not in codes:
        warnings.append({"code": "BACKLOG",
                         "why": f"{health['backlog']} observations are waiting"})
    return warnings


def run_bounded(activation: Activation, *, monitor_entry: Any,
                legacy_read: Callable[[], Any],
                next_write: Callable[[], Any],
                clock: Callable[[], float] | None = None,
                cursor_age_seconds: float | None = None) -> dict:
    """Preflight, then a bounded number of ticks, then the monitor's own verdict.

    Order is the contract: configuration, then the entry, then what may be read,
    then what may not be written. A wrapper that ticked before any of those would
    be monitoring something it had no right to look at.

    After the ticks the answer is the **monitor's**, not the wrapper's: the worst
    tick's state is reported as this result's state, and `BOUNDED_STOP` is
    reserved for a run in which every tick was green. `ticks` counts the attempts
    made; `ticks_report` has one record per answer actually received.
    """
    problems = problems_with_activation(activation)
    if problems:
        return _result(MISCONFIGURED, exit_code=EXIT_REFUSED, ticks=0,
                       why="; ".join(problems))

    try:
        tick = resolve_entry(monitor_entry)
    except ActivationRefused as refusal:
        return _result(refusal.code, exit_code=EXIT_BLOCKED_UPSTREAM, ticks=0,
                       why=refusal.why)

    if not _probe(legacy_read, what="legacy read"):
        return _result(LEGACY_UNREADABLE, exit_code=EXIT_REFUSED, ticks=0,
                       why="the Legacy Fleet read path is not available; a monitor "
                           "that cannot read the authorised facts cannot report on them")

    if _probe(next_write, what="next write"):
        return _result(NEXT_NOT_READ_ONLY, exit_code=EXIT_REFUSED, ticks=0,
                       why="the Next track would be reachable for writes; the shadow "
                           "is read-only, so activation refuses")

    clock = clock or time.time
    records: list[dict] = []
    attempts = 0
    for index in range(activation.ticks):
        attempts = index + 1
        try:
            row = tick()
        except Exception as exc:  # noqa: BLE001 - one failed tick is a fact
            health = _health(records, clock, cursor_age_seconds)
            return _result(TICK_FAILED, exit_code=EXIT_REFUSED, ticks=attempts,
                           why=f"{type(exc).__name__}: {exc}"[:240],
                           health=health, warnings=_warnings(records, health),
                           ticks_report=records)
        records.append(_record(index, row))

    health = _health(records, clock, cursor_age_seconds)
    warnings = _warnings(records, health)

    def _deciding() -> dict:
        return max(records, key=lambda r: (SEVERITY[_effective_state(r)], r["index"]))

    completed = bool(records) and all(record["completed"] for record in records)
    if completed and health["stale"] is True:
        # The producer called the tick green while its own cursor is older than
        # the bound. A run that stale did not finish being watched.
        state, completed = "UNCONFIRMED", False
        why = (f"every tick was green, but the cursor is "
               f"{int(health['cursor_age'])}s old"
               if health["cursor_age"] is not None else
               "every tick was green, but the monitor reports a stale cursor")
    elif completed:
        state = BOUNDED_STOP
        why = "bounded activation finished"
    else:
        deciding = _deciding()
        state = _effective_state(deciding)
        why = deciding["why"] or f"{state} at tick {deciding['index'] + 1}"

    return _result(state, exit_code=EXIT_FOR_STATE[state], ticks=attempts or len(records),
                   completed=completed, why=why, health=health, warnings=warnings,
                   ticks_report=records)


def probe_true() -> bool:
    """A probe that answers `available`. Exists so a unit can name it as a string."""
    return True


def probe_false() -> bool:
    """A probe that answers `not available`."""
    return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--legacy-root", required=True)
    parser.add_argument("--next-root", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--lock", required=True)
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--legacy-port", type=int, required=True)
    parser.add_argument("--service-name", required=True)
    parser.add_argument("--legacy-service-name", required=True)
    parser.add_argument("--plugin-id", required=True)
    parser.add_argument("--legacy-plugin-id", required=True)
    parser.add_argument("--ticks", type=int, default=1)
    parser.add_argument("--monitor-entry", required=True,
                        help="module:callable performing one monitor tick. There is "
                             "no default on purpose: an unpublished upstream entry "
                             "must fail closed rather than be guessed at.")
    parser.add_argument("--legacy-read", required=True,
                        help="module:callable returning True when the Legacy Fleet "
                             "read path is available")
    parser.add_argument("--next-write", required=True,
                        help="module:callable returning True when the Next shadow "
                             "would be reachable for writes (must be False)")
    parser.add_argument("--cursor-age-seconds", type=float, default=None,
                        help="How old the monitor's cursor is, when the operator "
                             "measures it. Without it the age is taken from the "
                             "tick's own health; with neither, staleness is reported "
                             "as unknown rather than as false.")
    args = parser.parse_args(argv)

    activation = Activation(
        legacy_root=args.legacy_root, next_root=args.next_root,
        state=args.state, lock=args.lock, port=args.port,
        legacy_port=args.legacy_port, service_name=args.service_name,
        legacy_service_name=args.legacy_service_name, plugin_id=args.plugin_id,
        legacy_plugin_id=args.legacy_plugin_id, ticks=args.ticks)

    def probe(name: str):
        try:
            return resolve_entry(name)
        except ActivationRefused:
            return lambda: False

    result = run_bounded(activation, monitor_entry=args.monitor_entry,
                         legacy_read=probe(args.legacy_read),
                         next_write=probe(args.next_write),
                         # The wall clock, so `last_tick` is when this ran rather
                         # than the 0.0 a defaulted clock used to write.
                         clock=time.time,
                         cursor_age_seconds=args.cursor_age_seconds)
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return int(result["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
