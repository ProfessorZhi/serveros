"""Event push on the hub (D-4): hear of a run the moment its device does.

One thread per company device long-polls that device's event spool
(`FleetService.device_events` → the connector's signed `GET /v1/events`, or the
hub's own spool for Tokyo). The thread only reads the network and queues what
it heard; the main loop does every database write and card, on its own thread:

* a run started or ended that the hub already watches → observed now, not on
  the next 30-second tick;
* a dispatched run not yet adopted → adopted first;
* a run nobody watches (the owner working in a session directly) → that one
  device is rediscovered now.

The 30-second tick stays as the safety net: if a device has no pusher, or the
pusher dies, nothing is lost, it is only slower — and the heartbeat says so.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

WAIT_SECONDS = 20.0
BACKOFF = (5, 10, 30, 60)
#: An old connector without the route: ask again rarely, not every few seconds.
UNSUPPORTED_BACKOFF = 600
#: Only events this recent can trigger a rediscovery (a fresh cursor replays).
FRESH_SECONDS = 600


def _age(iso: Any, now: float) -> float | None:
    try:
        return now - datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


class EventPump:
    def __init__(self, service: Any, principal: Any, devices: Callable[[], list[str]],
                 state_path: Path, *, wait: float = WAIT_SECONDS,
                 on_events: Callable[[], None] | None = None,
                 trace: Callable[..., None] | None = None,
                 clock: Callable[[], float] = time.time,
                 sleep: Callable[[float], None] = time.sleep) -> None:
        self.service, self.principal = service, principal
        self.devices = devices
        self.state_path = Path(state_path)
        self.wait = wait
        self.clock, self.sleep = clock, sleep
        self.wake = threading.Event()
        #: Called on the polling thread as soon as events are queued: the hub
        #: passes the fast lane, so a pushed run is observed and its card sent
        #: at once instead of after the current tick (7–13 s, 11-19).
        self.on_events = on_events
        #: The moment the hub hears of a run is traced on the polling thread
        #: itself ("heard"), before any lock: that is "the hub knows" (≤2 s).
        self.trace = trace
        self._lock = threading.Lock()
        self._queue: list[dict[str, Any]] = []
        self._threads: dict[str, threading.Thread] = {}
        self._stop = threading.Event()
        self.cursor: dict[str, int] = self._load()
        #: Per device: state, last event, last error, the pusher's own state.
        self.status: dict[str, dict[str, Any]] = {}

    # -- cursor -----------------------------------------------------------------

    def _load(self) -> dict[str, int]:
        try:
            doc = json.loads(self.state_path.read_text(encoding="utf-8"))
            return {k: int(v) for k, v in doc.items() if isinstance(v, int)}
        except (OSError, ValueError, AttributeError):
            return {}

    def _save(self) -> None:
        tmp = self.state_path.with_name(self.state_path.name + ".tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(self.cursor, handle)
        os.replace(tmp, self.state_path)

    # -- the per-device thread --------------------------------------------------

    def start(self) -> None:
        for device in self.devices():
            if device in self._threads:
                continue
            thread = threading.Thread(target=self._run, args=(device,), daemon=True,
                                      name=f"events:{device}")
            self._threads[device] = thread
            thread.start()

    def stop(self) -> None:
        self._stop.set()

    def poll_once(self, device: str) -> float:
        """One long-poll; returns how long to wait before the next. PURE of
        threads, so a test drives it directly."""
        from fleet_mcp.errors import FleetRefusal

        state = self.status.setdefault(device, {"state": "STARTING", "failures": 0})
        try:
            answer = self.service.device_events(self.principal, device=device,
                                                after=self.cursor.get(device, 0), wait=self.wait)
        except FleetRefusal as exc:
            state["failures"] = state.get("failures", 0) + 1
            unsupported = exc.code in ("CAPABILITY_UNSUPPORTED",) or "UNKNOWN_ROUTE" in (exc.detail or "")
            state.update(state="UNSUPPORTED" if unsupported else "ERROR", error=exc.code,
                         at=self.clock())
            return UNSUPPORTED_BACKOFF if unsupported else BACKOFF[min(state["failures"], len(BACKOFF)) - 1]
        except Exception as exc:  # noqa: BLE001 - a thread must not die on one bad answer
            state["failures"] = state.get("failures", 0) + 1
            state.update(state="ERROR", error=type(exc).__name__, at=self.clock())
            return BACKOFF[min(state["failures"], len(BACKOFF)) - 1]
        now = self.clock()
        events = answer.get("events") or []
        state.update(state="OK", failures=0, error=None, at=now,
                     eventd=(answer.get("eventd") or {}).get("state"))
        if answer.get("reset"):
            self.cursor[device] = 0
        elif isinstance(answer.get("next"), int):
            self.cursor[device] = answer["next"]
        if events:
            state["last_event_at"] = now
            runtime = answer.get("runtime")
            with self._lock:
                for event in events:
                    self._queue.append({"device": device, "runtime": runtime,
                                        "heard_at": now, **event})
            if self.trace is not None:
                for event in events:
                    if not str(event.get("kind", "")).startswith("run."):
                        continue
                    age = _age(event.get("at"), now)
                    try:
                        self.trace("heard " + event["kind"], run_id=event.get("run"),
                                   session=event.get("session"), device=device,
                                   lag_s=None if age is None else round(age, 1))
                    except Exception:  # noqa: BLE001 - the trace never stops the pump
                        pass
            self.wake.set()
            if self.on_events is not None:
                try:
                    self.on_events()
                except Exception:  # noqa: BLE001 - the main loop picks them up instead
                    pass
        try:
            self._save()
        except OSError:
            pass
        return 0.0

    def _run(self, device: str) -> None:
        while not self._stop.is_set():
            pause = self.poll_once(device)
            if pause:
                self._stop.wait(pause)

    # -- the main loop's half ---------------------------------------------------

    def drain(self) -> list[dict[str, Any]]:
        with self._lock:
            taken, self._queue = self._queue, []
        return taken

    def handle(self, watcher: Any, now: float | None = None) -> dict[str, Any]:
        """Act on what the threads heard. Runs on the main thread."""
        now = self.clock() if now is None else now
        events = self.drain()
        if not events:
            return {"events": 0}
        adopted = watcher.adopt_dispatched(now)
        expedited, rediscover = 0, set()
        named: set[tuple[str, str]] | None = {
            (row["session_ref"].split("|")[0], row["run_id"]) for row in adopted or []}
        for event in events:
            run = event.get("run")
            if not run or not event.get("kind", "").startswith("run."):
                continue
            if event["kind"] == "run.ended" and watcher.store.watching(event["device"], run):
                pushed = getattr(watcher, "pushed_end", None)
                if pushed is not None and len(pushed) < 1000:
                    pushed[(event["device"], run)] = str(event.get("status") or "")
            hit = watcher.store.expedite(event["device"], run, now)
            expedited += hit
            named.add((event["device"], run))
            age = _age(event.get("at"), now)
            heard = event.get("heard_at")
            if not hit and event["kind"] == "run.started" and age is not None and age <= FRESH_SECONDS:
                rediscover.add(event["device"])
            watcher._trace("pushed " + event["kind"], run_id=run,
                           session=event.get("session"), device=event["device"],
                           status=event.get("status"),
                           lag_s=None if age is None else round(age, 1),
                           waited_s=None if heard is None else round(now - heard, 1))
        if rediscover:
            watcher.discover(devices=rediscover)
            named = None           # what discovery just added is due too
        try:
            observed = watcher.observe(only=named)
        except TypeError:          # a watcher without the filter (tests' fakes)
            observed = watcher.observe()
        return {"events": len(events), "expedited": expedited,
                "rediscovered": sorted(rediscover), "observe": observed}

    def health(self) -> dict[str, Any]:
        now = self.clock()
        return {device: {k: (round(now - v, 1) if k in ("at", "last_event_at") and v else v)
                         for k, v in state.items()}
                for device, state in self.status.items()}
