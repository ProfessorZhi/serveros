"""`python -m fleet_hub_watch` — the hub's resident watcher.

    python -m fleet_hub_watch --config <fleet binding> [--interval 30] [--once]
        [--notify journal | lark:<user:open_id|chat:chat_id> | off]
        [--card-spec card-spec.yaml]
    python -m fleet_hub_watch --check-spec card-spec.yaml

The broadcast spec decides what the cards say and when they go out (titles,
headings, advice, the workday window, the 日报 time and sections). It is read at
start and again whenever the file changes; a spec that fails its check is
refused by name and the last good one stays in use, so an edit can never stop
the watch. `--check-spec` proves a spec without starting anything.

Each tick: discover and classify runs (M2), read every company device's
Core-Lite reading and record level changes (M4), then send or patch Feishu
cards (M3). `--notify journal` (the default) writes the cards it would send to
`notify.journal.jsonl` and sends nothing; `lark:<recipient>` sends for real and
is the owner's decision.

The binding must name `inbox.path`: the watcher writes the same inbox the Fleet
surface reads through `fleet_inbox`, and a watcher writing somewhere the surface
does not read would be a monitor nobody can see. Its watch list and lock sit in
the inbox's directory.

One watcher per inbox: a second process exits 3 instead of double-recording.
Every tick writes a heartbeat file, so the hub's own service-health reading can
tell a watcher that stopped from one that has nothing to say.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import sys
import time
from pathlib import Path

from fleet_mcp.config import load_config
from fleet_mcp.fleet import FleetService, Principal
from fleet_mcp.inbox import EventInbox

from datetime import datetime, timezone

from . import resources
from . import spec as broadcast_spec
from .notify import JournalTransport, LarkTransport, Notifier
from .watch import Watcher, WatchStore

DEFAULT_CONTRACT = Path(__file__).resolve().parents[3] / "runtime" / "attribution-contract.yaml"
DEFAULT_SPEC_FILE = Path(__file__).resolve().parents[1] / "card-spec.yaml"


class SpecWatcher:
    """The current broadcast spec, reloaded when its file changes."""

    def __init__(self, path: Path | None) -> None:
        self.path = path
        self.mtime: float | None = None
        self.spec = broadcast_spec.load(None)
        self.state = "DEFAULT"
        self.refresh()

    def refresh(self) -> bool:
        """True when a new spec took effect."""
        if self.path is None or not self.path.exists():
            return False
        mtime = self.path.stat().st_mtime
        if mtime == self.mtime:
            return False
        self.mtime = mtime
        try:
            self.spec = broadcast_spec.load(self.path)
        except broadcast_spec.SpecError as exc:
            self.state = f"REFUSED: {exc}"
            print(f"card spec refused, keeping the previous one: {exc}", file=sys.stderr)
            return False
        self.state = f"LOADED {self.path.name}"
        return True

EXIT_ANOTHER_WATCHER = 3
EXIT_NO_INBOX = 2


def _heartbeat(path: Path, document: dict) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    fd = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle, ensure_ascii=False)
    os.replace(temporary, path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="fleet_hub_watch", description=__doc__)
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between ticks")
    parser.add_argument("--instance-id", default="tokyo-hub")
    parser.add_argument("--once", action="store_true", help="one tick, then exit")
    parser.add_argument("--notify", default="journal",
                        help="journal (default, sends nothing) | lark:<recipient> | off")
    parser.add_argument("--contract", default=str(DEFAULT_CONTRACT),
                        help="attribution contract holding the resource thresholds")
    parser.add_argument("--card-spec", default=str(DEFAULT_SPEC_FILE),
                        help="broadcast spec (what the cards say and when)")
    parser.add_argument("--check-spec", metavar="SPEC",
                        help="prove a broadcast spec and exit (0 good, 2 refused)")
    parser.add_argument("--config", help="the Fleet binding file")
    args = parser.parse_args(argv)

    if args.check_spec:
        try:
            broadcast_spec.load(args.check_spec)
        except broadcast_spec.SpecError as exc:
            print(f"REFUSED: {exc}", file=sys.stderr)
            return EXIT_NO_INBOX
        print("OK: every card renders and passes the CardKit check")
        return 0
    if not args.config:
        parser.error("--config is required")

    config = load_config(args.config)
    if config.inbox is None:
        print("the binding names no inbox.path; refusing to watch into nowhere", file=sys.stderr)
        return EXIT_NO_INBOX
    directory = config.inbox.path.parent
    lock = open(directory / "watch.lock", "a")  # noqa: SIM115 - held for the process lifetime
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("another watcher holds this inbox", file=sys.stderr)
        return EXIT_ANOTHER_WATCHER

    service = FleetService(config)
    principal = Principal(name="hub-watch", scopes=frozenset({"fleet:read"}),
                          credential_kind="local-service")
    watcher = Watcher(service, principal, EventInbox(config.inbox.path),
                      WatchStore(directory / "watch.sqlite"), instance_id=args.instance_id)
    thresholds = resources.load_thresholds(args.contract)
    levels = resources.LevelStore(directory / "resources.sqlite")
    specs = SpecWatcher(Path(args.card_spec) if args.card_spec else None)
    names = {e.device_ref.ref: (e.display_name or e.device_ref.ref) for e in service.registry.all()}
    notifier = None
    if args.notify == "journal":
        notifier = Notifier(watcher.inbox, directory / "notify.sqlite",
                            JournalTransport(directory / "notify.journal.jsonl"), device_names=names,
                            spec=specs.spec)
    elif args.notify.startswith("lark:"):
        notifier = Notifier(watcher.inbox, directory / "notify.sqlite",
                            LarkTransport(args.notify[len("lark:"):]), device_names=names,
                            spec=specs.spec)
    elif args.notify != "off":
        print(f"unknown --notify {args.notify!r}", file=sys.stderr)
        return EXIT_NO_INBOX
    beat = directory / "watch.heartbeat.json"
    while True:
        started = time.time()
        try:
            if specs.refresh() and notifier is not None:
                notifier.spec = specs.spec
            result = watcher.tick()
            now = datetime.now(timezone.utc)
            views = resources.sweep(service, principal, watcher.inbox, levels, thresholds,
                                    now_iso=now.strftime("%Y-%m-%dT%H:%M:%SZ"))
            sent = notifier.tick(views, now) if notifier else None
            _heartbeat(beat, {"last_tick_at": started, "ok": True,
                              "watching": result["watching"],
                              "recorded": result["observe"]["recorded"],
                              "unreachable": result["discover"]["unreachable"],
                              "devices": {v["device_ref"]: v["level"] for v in views},
                              "notify": sent, "card_spec": specs.state})
        except Exception as exc:  # noqa: BLE001 - one bad tick must not end the watch
            _heartbeat(beat, {"last_tick_at": started, "ok": False,
                              "error": type(exc).__name__})
            print(f"tick failed: {type(exc).__name__}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(max(0.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
