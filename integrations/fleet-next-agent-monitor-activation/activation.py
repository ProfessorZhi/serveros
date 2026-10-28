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
"""
from __future__ import annotations

import argparse
import importlib
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

SCHEMA = "serveros/fleet-next-agent-monitor-activation.v1"

EXIT_OK = 0
EXIT_REFUSED = 2
EXIT_BLOCKED_UPSTREAM = 3

BOUNDED_STOP = "BOUNDED_STOP"
TICK_FAILED = "TICK_FAILED"
LEGACY_UNREADABLE = "LEGACY_UNREADABLE"
NEXT_NOT_READ_ONLY = "NEXT_NOT_READ_ONLY"
MISCONFIGURED = "MISCONFIGURED"
BLOCKED_UPSTREAM_ENTRY = "BLOCKED_UPSTREAM_ENTRY"

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


def _result(state: str, *, exit_code: int, ticks: int, why: str,
            health: Mapping[str, Any] | None = None,
            warnings: list[dict] | None = None) -> dict:
    return {"schema": SCHEMA, "state": state, "exit": exit_code, "ticks": ticks,
            "why": why, "health": dict(health or {}),
            "warnings": list(warnings or [])}


def run_bounded(activation: Activation, *, monitor_entry: Any,
                legacy_read: Callable[[], Any],
                next_write: Callable[[], Any],
                clock: Callable[[], float] | None = None,
                cursor_age_seconds: float = 0.0) -> dict:
    """Preflight, then a bounded number of ticks, then stop.

    Order is the contract: configuration, then the entry, then what may be read,
    then what may not be written. A wrapper that ticked before any of those would
    be monitoring something it had no right to look at.
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

    clock = clock or (lambda: 0.0)
    backlog = 0
    unknown_receipts = 0
    done = 0
    why = "bounded activation finished"
    for _ in range(activation.ticks):
        try:
            row = tick()
        except Exception as exc:  # noqa: BLE001 - one failed tick is a fact
            return _result(TICK_FAILED, exit_code=EXIT_REFUSED, ticks=done + 1,
                           why=f"{type(exc).__name__}: {exc}"[:240])
        done += 1
        if isinstance(row, Mapping):
            backlog = int(row.get("backlog", backlog) or 0)
            unknown_receipts = int(row.get("unknown_receipts", unknown_receipts) or 0)

    health = {
        "last_tick": float(clock()),
        "cursor_age": float(cursor_age_seconds),
        "backlog": backlog,
        "unknown_receipts": unknown_receipts,
        "stale": float(cursor_age_seconds) > STALE_AFTER_SECONDS,
    }
    warnings: list[dict] = []
    if health["stale"]:
        warnings.append({"code": "STALE_MONITOR",
                         "why": f"the cursor is {int(cursor_age_seconds)}s old, over "
                                f"the {STALE_AFTER_SECONDS}s bound"})
    if unknown_receipts:
        warnings.append({"code": "UNKNOWN_RECEIPTS",
                         "why": f"{unknown_receipts} delivery receipts are UNKNOWN"})
    if backlog:
        warnings.append({"code": "BACKLOG",
                         "why": f"{backlog} observations are waiting"})

    return _result(BOUNDED_STOP, exit_code=EXIT_OK, ticks=done, why=why,
                   health=health, warnings=warnings)


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
                         next_write=probe(args.next_write))
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return int(result["exit"])


if __name__ == "__main__":
    raise SystemExit(main())
