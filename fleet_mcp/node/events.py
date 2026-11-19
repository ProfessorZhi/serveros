"""The device event spool (D-4), read by the node connector and by the hub.

`serveros-eventd` (native/rust/eventd) appends one JSON event per line to an
owner-fixed spool — run started, run ended (with CloudCLI's status word),
waiting for a person — and keeps a status file beside it. This module is the
ONE reader of both:

* `read_since(spool, after)` — the events with `seq > after`, bounded;
* `wait_since(spool, after, wait)` — the same, but waits up to `wait` seconds
  for the first new line (the long-poll behind `GET /v1/events`);
* `eventd_status(spool)` — whether the pusher is alive, from its own status
  file, so "no events" is never confused with "no pusher".

Only ids, kinds, states and times are in an event; nothing here reads text a
session produced.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA = "serveros/device-events.v1"
MAX_EVENTS = 200
MAX_WAIT = 25.0
#: The pusher polls about once a second; past this its status is stale.
ALIVE_WITHIN = 30.0
_KINDS = ("run.started", "run.ended", "run.changed", "interaction.waiting")
_FIELDS = ("seq", "kind", "session", "run", "status", "at", "observed_at")


def default_spool() -> Path:
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / "ServerOS" / "state" / "events.jsonl"
    return Path.home() / ".local" / "state" / "serveros" / "events.jsonl"


def _sidecar(spool: Path, suffix: str) -> Path:
    return spool.with_name(spool.name + suffix)


def _clean(event: Any) -> dict[str, Any] | None:
    if not isinstance(event, dict) or event.get("kind") not in _KINDS:
        return None
    if not isinstance(event.get("seq"), int):
        return None
    out = {}
    for key in _FIELDS:
        value = event.get(key)
        if key == "seq" or value is None or (isinstance(value, str) and len(value) <= 160):
            out[key] = value
    return out


def read_since(spool: Path, after: int, *, limit: int = MAX_EVENTS) -> list[dict[str, Any]]:
    """Events with seq > after, oldest first, at most `limit`."""
    events: list[dict[str, Any]] = []
    try:
        with open(spool, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                try:
                    event = _clean(json.loads(line))
                except ValueError:
                    continue
                if event is not None and event["seq"] > after:
                    events.append(event)
                    if len(events) >= limit:
                        break
    except OSError:
        return []
    return events


def eventd_status(spool: Path, *, now: float | None = None) -> dict[str, Any]:
    """The pusher's own liveness record, reduced to facts the hub can judge."""
    now = time.time() if now is None else now
    try:
        doc = json.loads(_sidecar(spool, ".status.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "NOT_RUNNING", "reason": "no status file: serveros-eventd has not run here"}
    if not isinstance(doc, dict) or doc.get("schema") != "serveros/eventd-status.v1":
        return {"state": "UNREADABLE"}
    try:
        last = datetime.fromisoformat(str(doc.get("last_poll_at")).replace("Z", "+00:00"))
        age = now - last.timestamp()
    except ValueError:
        age = None
    alive = age is not None and age <= ALIVE_WITHIN
    state = ("OK" if doc.get("ok") else "DEGRADED") if alive else "STALE"
    return {"state": state, "age_s": None if age is None else round(age, 1),
            "error": doc.get("error") if isinstance(doc.get("error"), str) else None,
            "version": doc.get("version"), "seq": doc.get("seq"), "events": doc.get("events")}


def wait_since(spool: Path, after: int, wait: float, *, limit: int = MAX_EVENTS,
               step: float = 0.2, sleep=time.sleep, clock=time.monotonic) -> dict[str, Any]:
    """The long-poll: return as soon as there is something after `after`, or
    after `wait` seconds with nothing. `next` is the cursor to send back."""
    wait = max(0.0, min(float(wait), MAX_WAIT))
    deadline = clock() + wait
    while True:
        events = read_since(spool, after, limit=limit)
        if events or clock() >= deadline:
            break
        sleep(step)
    latest = events[-1]["seq"] if events else after
    # A spool that was reset (eventd reinstalled) restarts its seq: tell the
    # caller, so it rewinds instead of waiting for a number that never comes.
    reset = not events and after > 0 and _max_seq(spool) < after
    return {"schema": SCHEMA, "events": events, "next": 0 if reset else latest,
            "reset": reset, "eventd": eventd_status(spool),
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")}


def _max_seq(spool: Path) -> int:
    last = 0
    for event in read_since(spool, 0, limit=10**9):
        last = max(last, event["seq"])
    return last
