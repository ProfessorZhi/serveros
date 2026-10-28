#!/usr/bin/env python3
"""A bounded local process entry for the #428 milestone observer.

WHAT THIS IS
------------
The observer tick (#428) and the notifier/ledger (#423) are complete, and the
read-only sources (#426) are injectable. What was missing is a small process
that an operator (or a proposed timer) can start with an authorised config:
one set of watches, one Tokyo provider factory, one receipt ledger, one
scheduling state file — and a finite lifecycle with a stop request.

This module owns only that assembly:

    config -> single-active lock -> provider/source factories -> #428 observer
           -> run_periodic(bounded) -> status JSON

It does not classify milestones (the observer/notifier do), does not construct a
second event bus or Work ledger, does not run an LLM on a timer, and never reads
a credential, an environment variable, a socket or a service manager. The
provider and source are INJECTED, authorised callables; the runner only passes
them their own options from the config.

WHAT IT REFUSES
---------------
* A second runner on the same lock: fail-closed with ``ANOTHER_RUNNER_ACTIVE``
  before either factory is called.
* An unbounded run: at least one of ``max_ticks`` / ``max_runtime_seconds`` is
  required, and both have hard ceilings. This is not a resident service.
* A ledger/state/lock path that is relative, symlinked, a directory, or not
  unique: the receipt ledger is one file and the runner will not invent a second.
* A provider that is not a single object: the factory is called once; its
  capability probe and sends go through one rate-limited wrapper.
* A source/provider refusal is the observer's UNKNOWN fact; an unexpected
  runner-level exception is recorded as ``RUNNER_ERROR`` and the process exits
  non-zero. Neither is ever reported as DELIVERED.

CODE + OFFLINE FAKE E2E are what the tests prove. ``SERVICE_NOT_INSTALLED`` is
the service state: systemd / Windows Task Scheduler units are a PROPOSAL in the
README, not installed, enabled or started here.
"""
from __future__ import annotations

import argparse
import copy
import importlib
import importlib.util
import json
import os
import signal
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

SCHEMA = "serveros/fleet-v1-milestone-runner.v1"
SERVICE_STATE = "SERVICE_NOT_INSTALLED"

RUNNING = "RUNNING"
STOPPED = "STOPPED"
BOUNDED_STOP = "BOUNDED_STOP"
ERROR = "ERROR"
START_REFUSED = "START_REFUSED"

MAX_TICKS_HARD = 10_000
MAX_RUNTIME_SECONDS_HARD = 24 * 60 * 60
DEFAULT_MAX_SENDS_PER_MINUTE = 30
RATE_WINDOW_SECONDS = 60.0

_REPO = Path(__file__).resolve().parents[2]
_OBSERVER_PATH = (_REPO / "integrations" / "fleet-v1-milestone-observer"
                  / "observer.py")


class RunnerError(RuntimeError):
    """A config/assembly refusal with a short, stable code."""

    def __init__(self, code: str, why: str):
        self.code = code
        self.why = why
        super().__init__(f"{code}: {why}")


class RunnerConfigError(RunnerError):
    pass


class RunnerStartRefused(RunnerError):
    pass


class _StopRequested(RunnerError):
    def __init__(self, state: str):
        self.state = state
        super().__init__("stop", state)


def _load_module(name: str, path: Path):
    """Cache-first, REGISTERED BEFORE execution (the observer's dataclasses
    resolve their module through sys.modules while the module body runs)."""
    module = sys.modules.get(name)
    if module is None:
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
    return module


_OBSERVER = _load_module("serveros_fleet_v1_milestone_observer", _OBSERVER_PATH)
_NOTIFY = _OBSERVER.NOTIFY


# --------------------------------------------------------------------------- #
# config: closed, absolute, one ledger
# --------------------------------------------------------------------------- #
_CONFIG_FIELDS = frozenset({
    "schema", "ledger_path", "state_path", "lock_path", "conversation_ref",
    "watches", "interval_seconds", "max_ticks", "max_runtime_seconds",
    "max_sends_per_minute", "options", "provider_options", "source_options",
    "summarizer_options",
})
_WATCH_FIELDS = frozenset({
    "source", "ref", "device_ref", "boot_id", "source_sha", "qualifies",
    "expires_at",
})
_OPTION_FIELDS = frozenset({
    "max_reads_per_source", "min_interval_seconds", "base_backoff_seconds",
    "max_backoff_seconds", "reconcile_unknown",
})


def _text(value: Any, name: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RunnerConfigError("BAD_CONFIG", f"{name} must be non-empty text")
    text = value.strip()
    if len(text) > maximum or any(ord(ch) < 32 for ch in text):
        raise RunnerConfigError("BAD_CONFIG", f"{name} is not a bounded one-line value")
    return text


def _number(value: Any, name: str, *, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RunnerConfigError("BAD_CONFIG", f"{name} must be a number")
    number = float(value)
    if not (minimum <= number <= maximum):
        raise RunnerConfigError(
            "BAD_CONFIG", f"{name} must be between {minimum} and {maximum}")
    return number


def _optional_int(value: Any, name: str, *, minimum: int, maximum: int) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise RunnerConfigError("BAD_CONFIG", f"{name} must be an integer")
    if not (minimum <= value <= maximum):
        raise RunnerConfigError(
            "BAD_CONFIG", f"{name} must be between {minimum} and {maximum}")
    return value


def _mapping(value: Any, name: str) -> dict:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise RunnerConfigError("BAD_CONFIG", f"{name} must be an object")
    return copy.deepcopy(value)


def _closed(value: Any, allowed: frozenset[str], what: str) -> dict:
    if not isinstance(value, dict):
        raise RunnerConfigError("BAD_CONFIG", f"{what} must be an object")
    extra = sorted(set(value) - allowed)
    if extra:
        raise RunnerConfigError(
            "BAD_CONFIG", f"{what} has fields this runner does not read: {extra}")
    return value


def _path(value: Any, name: str) -> Path:
    text = _text(value, name, 4096)
    path = Path(text)
    if not path.is_absolute():
        raise RunnerConfigError("BAD_CONFIG", f"{name} must be absolute")
    if path.is_symlink():
        raise RunnerConfigError("BAD_CONFIG", f"{name} must not be a symlink")
    if path.exists() and path.is_dir():
        raise RunnerConfigError("BAD_CONFIG", f"{name} must not be a directory")
    if not path.parent.is_dir():
        raise RunnerConfigError("BAD_CONFIG", f"{name} parent directory is missing")
    return path


@dataclass(frozen=True)
class RunnerConfig:
    ledger_path: Path
    state_path: Path
    lock_path: Path
    conversation_ref: str
    watches: tuple
    interval_seconds: float
    max_ticks: int | None
    max_runtime_seconds: float | None
    max_sends_per_minute: int | None
    options: Any
    provider_options: dict
    source_options: dict
    summarizer_options: dict

    @classmethod
    def from_dict(cls, data: dict) -> "RunnerConfig":
        data = _closed(data, _CONFIG_FIELDS, "runner config")
        if data.get("schema") != SCHEMA:
            raise RunnerConfigError("BAD_CONFIG", f"schema must be {SCHEMA}")
        ledger = _path(data.get("ledger_path"), "ledger_path")
        state = _path(data.get("state_path"), "state_path")
        lock = _path(data.get("lock_path"), "lock_path")
        if len({ledger, state, lock}) != 3:
            raise RunnerConfigError(
                "BAD_CONFIG", "ledger_path, state_path and lock_path must be distinct")
        conversation = _text(data.get("conversation_ref"), "conversation_ref", 256)
        watches_raw = data.get("watches")
        if not isinstance(watches_raw, list) or not watches_raw:
            raise RunnerConfigError("BAD_CONFIG", "watches must be a non-empty list")
        watches = []
        for index, item in enumerate(watches_raw):
            item = _closed(item, _WATCH_FIELDS, f"watches[{index}]")
            try:
                watches.append(_OBSERVER.Watch(**item))
            except (TypeError, ValueError) as exc:
                raise RunnerConfigError(
                    "BAD_CONFIG", f"watches[{index}]: {exc}") from None
        if len({watch.key for watch in watches}) != len(watches):
            raise RunnerConfigError("BAD_CONFIG", "two watches name the same source:ref")
        interval = _number(data.get("interval_seconds", 0), "interval_seconds",
                           minimum=0.0, maximum=3600.0)
        max_ticks = _optional_int(data.get("max_ticks"), "max_ticks",
                                  minimum=1, maximum=MAX_TICKS_HARD)
        max_runtime = data.get("max_runtime_seconds")
        if max_runtime is not None:
            max_runtime = _number(max_runtime, "max_runtime_seconds",
                                  minimum=0.05,
                                  maximum=MAX_RUNTIME_SECONDS_HARD)
        if max_ticks is None and max_runtime is None:
            raise RunnerConfigError(
                "UNBOUNDED_LIFECYCLE",
                "set max_ticks or max_runtime_seconds; this is not a resident service")
        if max_ticks is None and interval < 0.05:
            raise RunnerConfigError(
                "BAD_CONFIG",
                "a runtime-bounded runner needs interval_seconds >= 0.05; "
                "a zero-interval loop is not a rate limit")
        max_sends = _optional_int(
            data.get("max_sends_per_minute", DEFAULT_MAX_SENDS_PER_MINUTE),
            "max_sends_per_minute", minimum=0, maximum=10_000)
        options_raw = _closed(data.get("options", {}), _OPTION_FIELDS, "options")
        try:
            options = _OBSERVER.Options(**options_raw)
        except (TypeError, ValueError) as exc:
            raise RunnerConfigError("BAD_CONFIG", f"options: {exc}") from None
        return cls(
            ledger_path=ledger, state_path=state, lock_path=lock,
            conversation_ref=conversation, watches=tuple(watches),
            interval_seconds=interval, max_ticks=max_ticks,
            max_runtime_seconds=max_runtime, max_sends_per_minute=max_sends,
            options=options,
            provider_options=_mapping(data.get("provider_options"), "provider_options"),
            source_options=_mapping(data.get("source_options"), "source_options"),
            summarizer_options=_mapping(data.get("summarizer_options"),
                                        "summarizer_options"))


def load_config(path) -> RunnerConfig:
    try:
        raw = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise RunnerConfigError("BAD_CONFIG", f"cannot read {path}: {exc}") from None
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise RunnerConfigError("BAD_CONFIG", f"config is not JSON: {exc}") from None
    return RunnerConfig.from_dict(data)


# --------------------------------------------------------------------------- #
# one active process
# --------------------------------------------------------------------------- #
class SingleActiveLock:
    """A non-blocking OS file lock; a held lock is a start refusal.

    POSIX uses ``flock``; Windows uses ``msvcrt.locking``. If neither can be
    acquired, construction fails closed — it never falls back to "no lock".
    """

    def __init__(self, path):
        self.path = Path(path)
        self._fd: int | None = None

    def acquire(self) -> None:
        if self._fd is not None:
            return
        try:
            self._fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        except OSError as exc:
            raise RunnerStartRefused(
                "LOCK_UNAVAILABLE", f"cannot open the lock file: {exc}") from None
        try:
            if os.name == "nt":
                import msvcrt
                if os.fstat(self._fd).st_size == 0:
                    os.write(self._fd, b"\0")
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            raise RunnerStartRefused(
                "ANOTHER_RUNNER_ACTIVE",
                f"another runner holds {self.path}: {exc}") from None

    def release(self) -> None:
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        finally:
            os.close(self._fd)
            self._fd = None


class RateLimitedProvider:
    """The one provider, with a bounded send rate.

    The wrapper never turns a limited send into a delivery: it returns
    ``delivered: False`` and #423 records DELIVERY_UNKNOWN. The limit is an
    in-memory attempt count, not a second queue or store.
    """

    def __init__(self, provider, *, max_sends_per_minute: int | None,
                 monotonic: Callable[[], float] = time.monotonic):
        self.provider = provider
        self.limit = max_sends_per_minute
        self.monotonic = monotonic
        self._sent: deque[float] = deque()

    def capabilities(self):
        return self.provider.capabilities()

    def notify(self, *, conversation_ref: str, text: str, idempotency_key: str):
        if self.limit is not None:
            now = self.monotonic()
            while self._sent and now - self._sent[0] >= RATE_WINDOW_SECONDS:
                self._sent.popleft()
            if len(self._sent) >= self.limit:
                return {"delivered": False,
                        "why": (f"runner send rate limit {self.limit}/"
                                f"{int(RATE_WINDOW_SECONDS)}s reached; "
                                f"no message was sent"),
                        "provider": getattr(self.provider, "provider_id",
                                            "provider:rate-limited")}
            self._sent.append(now)
        return self.provider.notify(conversation_ref=conversation_ref, text=text,
                                    idempotency_key=idempotency_key)

    def close(self) -> None:
        close = getattr(self.provider, "close", None)
        if callable(close):
            close()


# --------------------------------------------------------------------------- #
# the runner
# --------------------------------------------------------------------------- #
class MilestoneRunner:
    """Assemble #423 + #426 + #428 once, then run a bounded lifecycle."""

    def __init__(self, config: RunnerConfig, *, provider_factory: Callable,
                 source_factory: Callable,
                 summarizer_factory: Callable | None = None,
                 clock: Callable | None = None,
                 monotonic: Callable[[], float] = time.monotonic,
                 sleep: Callable[[float], None] | None = None,
                 lock_factory: Callable = SingleActiveLock):
        if not callable(provider_factory):
            raise RunnerConfigError("BAD_FACTORY", "provider_factory is not callable")
        if not callable(source_factory):
            raise RunnerConfigError("BAD_FACTORY", "source_factory is not callable")
        if summarizer_factory is not None and not callable(summarizer_factory):
            raise RunnerConfigError("BAD_FACTORY", "summarizer_factory is not callable")
        self.config = config
        self._provider_factory = provider_factory
        self._source_factory = source_factory
        self._summarizer_factory = summarizer_factory
        self._clock = clock
        self._monotonic = monotonic
        self._sleep = sleep
        self._lock = lock_factory(config.lock_path)
        self._stop = threading.Event()
        self._started = False
        self._closed = False
        self._deadline: float | None = None
        self.provider = None
        self.source = None
        self.notifier = None
        self.observer = None
        self.tick_count = 0
        self.ticks: deque[dict] = deque(maxlen=200)
        self.last: dict | None = None
        self.error: str | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self) -> "MilestoneRunner":
        if self._closed:
            raise RunnerStartRefused(
                "RUNNER_STOPPED", "this runner was already stopped")
        if self._started:
            raise RunnerStartRefused("ALREADY_STARTED", "this runner is already started")
        self._lock.acquire()
        try:
            try:
                provider = self._provider_factory(
                    copy.deepcopy(self.config.provider_options))
            except Exception as exc:
                raise RunnerStartRefused(
                    "BAD_PROVIDER",
                    f"provider factory failed: {type(exc).__name__}") from None
            if not callable(getattr(provider, "notify", None)) or not callable(
                    getattr(provider, "capabilities", None)):
                raise RunnerStartRefused(
                    "BAD_PROVIDER", "provider factory did not return a notifier provider")
            provider = RateLimitedProvider(
                provider, max_sends_per_minute=self.config.max_sends_per_minute,
                monotonic=self._monotonic)
            self.provider = provider
            try:
                source = self._source_factory(
                    copy.deepcopy(self.config.source_options))
            except Exception as exc:
                raise RunnerStartRefused(
                    "BAD_SOURCE",
                    f"source factory failed: {type(exc).__name__}") from None
            if not callable(getattr(source, "read", None)):
                raise RunnerStartRefused(
                    "BAD_SOURCE", "source factory did not return a SourceRead")
            self.source = source
            summarizer = None
            if self._summarizer_factory is not None:
                try:
                    summarizer = self._summarizer_factory(
                        copy.deepcopy(self.config.summarizer_options))
                except Exception as exc:
                    raise RunnerStartRefused(
                        "BAD_SUMMARIZER",
                        f"summarizer factory failed: {type(exc).__name__}") from None
                if summarizer is not None and not callable(summarizer):
                    raise RunnerStartRefused(
                        "BAD_SUMMARIZER", "summarizer factory did not return a callable")
            try:
                notifier = _NOTIFY.MilestoneNotifier(
                    provider, self.config.ledger_path,
                    conversation_ref=self.config.conversation_ref,
                    summarizer=summarizer, clock=self._clock)
                observer = _OBSERVER.MilestoneObserver(
                    source=source, notifier=notifier,
                    state_path=self.config.state_path,
                    watches=self.config.watches, clock=self._clock,
                    options=self.config.options)
            except RunnerError:
                raise
            except Exception as exc:
                raise RunnerStartRefused(
                    "ASSEMBLY_FAILED",
                    f"observer assembly failed: {type(exc).__name__}: {exc}") from None
        except BaseException:
            self._release()
            raise
        self.notifier = notifier
        self.observer = observer
        self._started = True
        return self

    def request_stop(self) -> None:
        """Windows-testable stop interface; no signal is required."""
        self._stop.set()

    def stop(self) -> None:
        self.request_stop()
        self._release()

    def _release(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.provider is not None:
                close = getattr(self.provider, "close", None)
                if callable(close):
                    close()
        finally:
            self._lock.release()

    # -- one bounded run ---------------------------------------------------
    def _tick_cap(self) -> int:
        if self.config.max_ticks is not None:
            return self.config.max_ticks
        return MAX_TICKS_HARD

    def _sleep_once(self, seconds: float) -> None:
        if self._stop.is_set():
            raise _StopRequested(STOPPED)
        if self._sleep is not None:
            self._sleep(seconds)
        else:
            self._stop.wait(seconds)
        if self._stop.is_set():
            raise _StopRequested(STOPPED)
        if self._deadline is not None and self._monotonic() >= self._deadline:
            raise _StopRequested(BOUNDED_STOP)

    @staticmethod
    def _summary(report: dict) -> dict:
        rows = []
        for row in report.get("rows", []):
            rows.append({key: row.get(key) for key in
                         ("source", "ref", "state", "outcome", "delivered",
                          "delivery_key", "code", "why")})
        return {"observed_at": report.get("observed_at"),
                "counts": report.get("counts", {}), "rows": rows}

    def run(self) -> dict:
        if not self._started:
            self.start()
        self._deadline = (self._monotonic() + self.config.max_runtime_seconds
                          if self.config.max_runtime_seconds is not None else None)
        state = BOUNDED_STOP
        try:
            for report in self.observer.run_periodic(
                    interval_seconds=self.config.interval_seconds,
                    sleep=self._sleep_once, max_ticks=self._tick_cap()):
                if self._stop.is_set():
                    state = STOPPED
                    break
                summary = self._summary(report)
                self.last = summary
                self.ticks.append(summary)
                self.tick_count += 1
                if self._deadline is not None and self._monotonic() >= self._deadline:
                    state = BOUNDED_STOP
                    break
        except _StopRequested as stop:
            state = stop.state
        except Exception as exc:  # noqa: BLE001 - record, never report delivered
            state = ERROR
            self.error = f"{type(exc).__name__}: {exc}"
            self.ticks.append({"state": "RUNNER_ERROR", "why": self.error[:240]})
        finally:
            self._release()
        return {"schema": SCHEMA, "state": state, "service_state": SERVICE_STATE,
                "ticks": self.tick_count, "last": self.last, "error": self.error}


# --------------------------------------------------------------------------- #
# CLI: factories are injected by name, never discovered from a credential
# --------------------------------------------------------------------------- #
def load_factory(spec: str) -> Callable:
    module_name, separator, attr = str(spec or "").partition(":")
    if not separator or not module_name or not attr:
        raise RunnerConfigError(
            "BAD_FACTORY", "factory must be module:callable")
    if any(ch in module_name for ch in ("/", "\\", ":")) or not all(
            part.isidentifier() for part in module_name.split(".")):
        raise RunnerConfigError("BAD_FACTORY", f"bad module name {module_name!r}")
    if not attr.isidentifier():
        raise RunnerConfigError("BAD_FACTORY", f"bad factory name {attr!r}")
    module = importlib.import_module(module_name)
    factory = getattr(module, attr, None)
    if not callable(factory):
        raise RunnerConfigError(
            "BAD_FACTORY", f"{spec} is not a callable factory")
    return factory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--config", required=True)
    parser.add_argument("--provider-factory", required=True,
                        help="module:callable returning the one provider")
    parser.add_argument("--source-factory", required=True,
                        help="module:callable returning a SourceRead")
    parser.add_argument("--summarizer-factory", default=None,
                        help="optional module:callable returning a summarizer")
    args = parser.parse_args(argv)
    try:
        config = load_config(args.config)
        provider_factory = load_factory(args.provider_factory)
        source_factory = load_factory(args.source_factory)
        summarizer_factory = (load_factory(args.summarizer_factory)
                              if args.summarizer_factory else None)
        runner = MilestoneRunner(
            config, provider_factory=provider_factory,
            source_factory=source_factory, summarizer_factory=summarizer_factory)
    except RunnerError as exc:
        json.dump({"schema": SCHEMA, "state": START_REFUSED,
                   "service_state": SERVICE_STATE, "code": exc.code,
                   "why": exc.why}, sys.stdout, ensure_ascii=False, sort_keys=True)
        sys.stdout.write("\n")
        return 2
    for sig in (getattr(signal, "SIGTERM", None), getattr(signal, "SIGINT", None)):
        if sig is None:
            continue
        try:
            signal.signal(sig, lambda *_args: runner.request_stop())
        except (ValueError, OSError):
            pass
    try:
        result = runner.run()
    except RunnerStartRefused as exc:
        result = {"schema": SCHEMA, "state": START_REFUSED,
                  "service_state": SERVICE_STATE, "code": exc.code,
                  "why": exc.why, "ticks": runner.tick_count}
    json.dump(result, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return 0 if result.get("state") in (STOPPED, BOUNDED_STOP) else 2


if __name__ == "__main__":
    raise SystemExit(main())
