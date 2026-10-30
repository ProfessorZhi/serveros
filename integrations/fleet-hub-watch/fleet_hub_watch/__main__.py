"""`python -m fleet_hub_watch` — the hub's resident watcher.

    python -m fleet_hub_watch --config <fleet binding> [--interval 30] [--once]

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

from .watch import Watcher, WatchStore

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
    parser.add_argument("--config", required=True, help="the Fleet binding file")
    parser.add_argument("--interval", type=float, default=30.0, help="seconds between ticks")
    parser.add_argument("--instance-id", default="tokyo-hub")
    parser.add_argument("--once", action="store_true", help="one tick, then exit")
    args = parser.parse_args(argv)

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
    beat = directory / "watch.heartbeat.json"
    while True:
        started = time.time()
        try:
            result = watcher.tick()
            _heartbeat(beat, {"last_tick_at": started, "ok": True,
                              "watching": result["watching"],
                              "recorded": result["observe"]["recorded"],
                              "unreachable": result["discover"]["unreachable"]})
        except Exception as exc:  # noqa: BLE001 - one bad tick must not end the watch
            _heartbeat(beat, {"last_tick_at": started, "ok": False,
                              "error": type(exc).__name__})
            print(f"tick failed: {type(exc).__name__}", file=sys.stderr)
        if args.once:
            return 0
        time.sleep(max(0.0, args.interval - (time.time() - started)))


if __name__ == "__main__":
    sys.exit(main())
