"""Config, the one-shot CLI, and the zero-argument tick #488 injects.

Three doors, one monitor:

*   `build_monitor(...)` — the composition used by everything else. It resolves
    the grant, the read-only source and the notifier *before* constructing the
    core, so a missing grant or a missing ledger is a named refusal rather than
    a tick that half-ran.
*   `cli.main(argv)` — a real one-shot process. It prints one JSON tick
    document and exits `0 SETTLED | 2 REFUSED | 3 BLOCKED_INPUT | 4 UNCONFIRMED`.
*   `tick()` — the **zero-argument** callable PR #488's activation wrapper
    resolves from `--monitor-entry module:callable` and calls with no arguments.
    That is the exact contract gap #482/#488/#491 recorded
    (`BLOCKED_RUNTIME_ENTRY`): the wrapper injects a zero-arg tick, and until
    this module there was no such entry to inject. Configuration comes from the
    environment, because a zero-argument callable has nowhere else to read it.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

from .contract import (
    EXIT_BLOCKED_INPUT,
    EXIT_REFUSED,
    SCHEMA_CONFIG,
    STATE_BLOCKED_INPUT,
    STATE_REFUSED,
    VIEWS,
    MonitorRefused,
    require_watch,
)
from .core import MonitorCore, notifier_or_none
from .offline import OfflineFactsClient
from .sources import Grant, LegacyFactsSource
from .state import DurableState

CONFIG_ENV = "FLEET_NEXT_AGENT_MONITOR_CONFIG"
STATE_ENV = "FLEET_NEXT_AGENT_MONITOR_STATE"
NOTIFIER_ENV = "FLEET_NEXT_AGENT_MONITOR_NOTIFIER"
CLIENT_ENV = "FLEET_NEXT_AGENT_MONITOR_CLIENT"
OFFLINE_ENV = "FLEET_NEXT_AGENT_MONITOR_OFFLINE_FACTS"

_BOUND_KEYS = frozenset({
    "max_readings", "stale_after_seconds", "debounce_ticks", "max_warnings",
})
_CONFIG_KEYS = frozenset({"schema", "grant", "watches", "bounds", "label"})
_DEFAULT_BOUNDS = {
    "max_readings": 500,
    "stale_after_seconds": 3600,
    "debounce_ticks": 2,
    "max_warnings": 6,
}


def load_config(document: Any) -> dict[str, Any]:
    """Validate the config as a closed shape.

    A typo'd key is a question the operator thought they were asking and the
    monitor would silently not answer, so it is refused here.
    """
    if not isinstance(document, Mapping):
        raise MonitorRefused("BAD_CONFIG", "the config is a JSON object")
    unexpected = set(document) - _CONFIG_KEYS
    if unexpected:
        raise MonitorRefused("BAD_CONFIG", f"unknown config key(s): {sorted(unexpected)}")
    if document.get("schema") != SCHEMA_CONFIG:
        raise MonitorRefused(
            "BAD_CONFIG", f"schema must be {SCHEMA_CONFIG!r}, got {document.get('schema')!r}")
    grant = Grant.from_mapping(document.get("grant"))
    if not grant.views:
        raise MonitorRefused(
            "BAD_CONFIG",
            "the grant names no view; a monitor with no readable view is not a "
            "restricted monitor, it is a misconfigured one")
    watches = document.get("watches")
    if not isinstance(watches, list) or not watches:
        raise MonitorRefused(
            "BAD_CONFIG", "`watches` must be a non-empty list; an empty watch list "
                          "would run every tick and observe nothing")
    checked = [require_watch(dict(w)) for w in watches]
    for watch in checked:
        if watch["kind"] not in grant.views:
            raise MonitorRefused(
                "BAD_CONFIG",
                f"watch kind {watch['kind']!r} is outside the grant's views "
                f"{sorted(grant.views)}")
    bounds = dict(_DEFAULT_BOUNDS)
    raw_bounds = document.get("bounds")
    if raw_bounds is not None:
        if not isinstance(raw_bounds, Mapping):
            raise MonitorRefused("BAD_CONFIG", "`bounds` must be an object")
        unknown = set(raw_bounds) - _BOUND_KEYS
        if unknown:
            raise MonitorRefused("BAD_CONFIG", f"unknown bound(s): {sorted(unknown)}")
        for key, value in raw_bounds.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
                raise MonitorRefused("BAD_CONFIG", f"bound `{key}` must be a positive number")
            bounds[key] = value
    return {"grant": grant, "watches": checked, "bounds": bounds,
            "label": str(document.get("label") or "fleet-next-agent-monitor")}


def resolve_factory(spec: Any) -> Callable[[], Any]:
    """`module:callable` -> a zero-argument factory. Refused by name otherwise."""
    if not isinstance(spec, str) or ":" not in spec:
        raise MonitorRefused("BAD_CONFIG", f"{spec!r} is not a `module:callable`")
    module_name, _, attribute = spec.partition(":")
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:  # noqa: BLE001
        raise MonitorRefused(
            "BLOCKED_UPSTREAM",
            f"{module_name} cannot be imported ({type(exc).__name__}: {exc})") from None
    factory = getattr(module, attribute, None)
    if not callable(factory):
        raise MonitorRefused("BAD_CONFIG", f"{module_name}.{attribute} is not callable")
    return factory


def build_source(config: Mapping[str, Any], *, client: Any | None = None,
                 offline_facts: str | Path | None = None) -> LegacyFactsSource:
    """Resolve the read-only source. Exactly one client, or a named refusal."""
    if client is not None and offline_facts is not None:
        raise MonitorRefused(
            "BAD_CONFIG", "a live client and an offline facts file are two different "
                          "answers to the same question; pick one")
    if client is None:
        if offline_facts is None:
            raise MonitorRefused(
                "BLOCKED_RUNTIME_ENTRY",
                "no Legacy client and no offline facts were given; the monitor has no "
                "read-only source to sample")
        client = OfflineFactsClient.from_path(offline_facts)
    return LegacyFactsSource(client, config["grant"],
                             max_readings=int(config["bounds"]["max_readings"]))


def build_monitor(config_document: Mapping[str, Any], *, state_path: str | Path,
                  notifier_spec: str | None = None,
                  client: Any | None = None,
                  offline_facts: str | Path | None = None,
                  clock: Callable[[], float] | None = None,
                  epoch: str | None = None) -> tuple[MonitorCore, dict[str, Any], str | None]:
    """Compose one monitor. The notifier is injected, never created here."""
    config = load_config(config_document)
    source = build_source(config, client=client, offline_facts=offline_facts)
    notifier, blocked_why = notifier_or_none(notifier_spec, config=config_document)
    state = DurableState(state_path, clock=clock)
    core = MonitorCore(source=source, notifier=notifier, state=state,
                       watches=config["watches"], clock=clock,
                       stale_after=float(config["bounds"]["stale_after_seconds"]),
                       debounce_ticks=int(config["bounds"]["debounce_ticks"]),
                       max_warnings=int(config["bounds"]["max_warnings"]),
                       epoch=epoch)
    return core, config, blocked_why


def _document_for(path: str | Path) -> dict[str, Any]:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise MonitorRefused(
            "BAD_CONFIG", f"the config is unreadable ({type(exc).__name__})") from None
    try:
        return json.loads(raw)
    except ValueError:
        raise MonitorRefused("BAD_CONFIG", "the config is not JSON") from None


def _blocked(why: str, detail: str, *, exit_code: int, state: str) -> dict[str, Any]:
    return {"schema": "serveros/fleet-next-agent-monitor-tick.v1", "state": state,
            "exit": exit_code, "why": why, "detail": detail, "watches": [],
            "counts": {}, "dispatched": 0, "delivered": 0, "duplicate": 0,
            "unknown_receipts": 0, "backlog": 0, "health": {}, "warnings": []}


def run_from_paths(*, config_path: str | Path, state_path: str | Path,
                   notifier_spec: str | None = None,
                   client_spec: str | None = None,
                   offline_facts: str | Path | None = None,
                   epoch: str | None = None) -> dict[str, Any]:
    """The whole one-shot path, from two paths to one tick document."""
    try:
        document = _document_for(config_path)
        client = resolve_factory(client_spec)() if client_spec else None
        core, _config, blocked = build_monitor(
            document, state_path=state_path, notifier_spec=notifier_spec,
            client=client, offline_facts=offline_facts, epoch=epoch)
    except MonitorRefused as refusal:
        state = STATE_BLOCKED_INPUT if refusal.code.startswith("BLOCKED_") else STATE_REFUSED
        exit_code = EXIT_BLOCKED_INPUT if state == STATE_BLOCKED_INPUT else EXIT_REFUSED
        return _blocked(refusal.code, refusal.why, exit_code=exit_code, state=state)
    tick = core.run_once()
    if blocked:
        # The tick ran and reported its vitals; the missing ledger is stated once.
        warnings = list(tick.get("warnings") or [])
        if not any(w.get("code") == "BLOCKED_LEDGER" for w in warnings):
            warnings.append({"code": "BLOCKED_LEDGER", "status": None, "reason": None,
                             "why": blocked})
        tick = dict(tick, warnings=warnings)
    return tick


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="One bounded Fleet Next Agent Monitor tick. It reads, decides "
                    "and hands over at most once; it never restarts anything.")
    parser.add_argument("--config", required=True)
    parser.add_argument("--state", required=True)
    parser.add_argument("--notifier-entry", default=None,
                        help="module:callable returning #423's notifier. There is no "
                             "default: a missing ledger is a named blocker.")
    parser.add_argument("--client-entry", default=None)
    parser.add_argument("--offline-facts", default=None)
    parser.add_argument("--epoch", default=None)
    args = parser.parse_args(argv)
    tick = run_from_paths(config_path=args.config, state_path=args.state,
                          notifier_spec=args.notifier_entry,
                          client_spec=args.client_entry,
                          offline_facts=args.offline_facts, epoch=args.epoch)
    json.dump(tick, sys.stdout, ensure_ascii=False, sort_keys=True, indent=2)
    sys.stdout.write("\n")
    return int(tick.get("exit", EXIT_REFUSED))


def tick() -> dict[str, Any]:
    """The zero-argument tick PR #488's `--monitor-entry module:callable` injects.

    Returns #488's shape (`backlog`, `unknown_receipts` are top-level keys) and
    never raises for a configuration problem: it returns a named `BLOCKED_INPUT`
    document instead, so the wrapper reports a refusal rather than a crash.
    """
    config_path = os.environ.get(CONFIG_ENV)
    state_path = os.environ.get(STATE_ENV)
    if not config_path or not state_path:
        return _blocked(
            "BLOCKED_RUNTIME_ENTRY",
            f"{CONFIG_ENV} and {STATE_ENV} must both name absolute paths; a "
            f"zero-argument tick has nowhere else to read its configuration",
            exit_code=EXIT_BLOCKED_INPUT, state=STATE_BLOCKED_INPUT)
    try:
        return run_from_paths(
            config_path=config_path, state_path=state_path,
            notifier_spec=os.environ.get(NOTIFIER_ENV) or None,
            client_spec=os.environ.get(CLIENT_ENV) or None,
            offline_facts=os.environ.get(OFFLINE_ENV) or None)
    except MonitorRefused as refusal:
        return _blocked(refusal.code, refusal.why,
                        exit_code=EXIT_BLOCKED_INPUT, state=STATE_BLOCKED_INPUT)


if __name__ == "__main__":
    raise SystemExit(main())
