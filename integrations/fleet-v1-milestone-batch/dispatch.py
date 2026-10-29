#!/usr/bin/env python3
"""#417 — the finite tick a supervisor can run, and the #446 config it runs from.

WHAT THIS IS
------------
`registration.py` writes the declaration and `batch.py` correlates one tick of
it. Neither of them answers the operational question the owner actually asked:

    派发一批任务后退出，后台凭持久登记持续 Observer

Something outside the chat has to (1) rebuild the declaration from the file on
every start, (2) do one bounded amount of reading, and (3) exit or wait — not
become a second schedule service. This module is that something, and only that:

    registration file  ->  Registration.batch  ->  #428 MilestoneObserver
                       ->  BatchAggregator.absorb()  ->  #423 ledger

It adds **no** store, **no** lock manager of its own, **no** resident loop and
**no** transport. Two functions and one small object:

* :func:`runner_document` — emit exactly the config `#446`'s runner already
  validates (`serveros/fleet-v1-milestone-runner.v1`), so an external
  supervisor is configured from a filed registration rather than a hand-kept
  watch list. It is validated through #446's own `RunnerConfig.from_dict`
  before it is returned: emitting a document nobody could load is not an
  adapter.
* :func:`tick_once` / :func:`tick_from_file` — one correlation step. The file
  is re-read on every call, which is what makes a restart resume rather than
  replay from a memory that no longer exists.
* :class:`Ticker` — the smallest thing that can hold a live aggregator across
  several ticks, re-reading the file when — and only when — its bytes changed.

WHY RESTART IS SAFE WITHOUT A SECOND STATE FILE
-----------------------------------------------
A restart forgets `BatchAggregator._confirmed` and `_alerted`. It does not
matter, because it re-asks #423's ledger for every key it would have used, and
the ledger is the only place a delivery is a fact:

* the summary key is `batch:<batch_id>:summary`, derived from the ID in the
  file — identical across processes, so the second process is told `DUPLICATE`
  and gets the first process's `remote_id`;
* the alert key is derived from the member key and the verdict, so the same
  restarted process cannot re-alert a member it already alerted.

`#428`'s state file carries scheduling facts only (backoff, windows), and its
docstring already says deleting it must not cause a resend. Neither file is
consulted to decide whether a message was delivered.

WHAT THIS IS NOT
----------------
* not a scheduler: no sleep loop, no daemon, no second ledger, no lock file
  ownership. `#446`'s `RunnerConfig` already refuses an unbounded lifecycle and
  `#446`'s `SingleActiveLock` already owns single-instance; this module refuses
  to emit a config that would contradict either.
* not a notifier: it never constructs a provider, reads a credential, or calls
  a private method. The notifier is injected, and its public `send_text` is the
  only path to the one provider.
* not a `#456` commander: it consumes the registration the commander filed.

AUTHORITY BOUNDARY
------------------
No network, no CLI invocation, no credential, no service, no model, no real IM
send. The provider is whatever the caller injects.
"""
from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path
from typing import Any, Callable, Mapping

_HERE = Path(__file__).resolve().parent


def _sibling(name: str, filename: str):
    """A module beside this file, cache-first, under the name its tests use."""
    module = sys.modules.get(name)
    if module is not None:
        return module
    path = _HERE / filename
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _batch():
    return _sibling("serveros_fleet_v1_milestone_batch", "batch.py")


def _registration():
    return _sibling("serveros_fleet_v1_milestone_batch_registration",
                    "registration.py")


def _observer():
    return _sibling("serveros_fleet_v1_milestone_observer",
                    "observer.py")


def _runner():
    """`#446`'s runner, read-only: its schema is the config contract, not ours."""
    return _sibling("serveros_fleet_v1_milestone_runner",
                    "../fleet-v1-milestone-runner/runner.py")


def _refused(code: str, why: str):
    return _batch().BatchRefused(code, why)


# --------------------------------------------------------------------------- #
# the declaration, as an external supervisor's config
# --------------------------------------------------------------------------- #
def runner_document(registration_value, *, ledger_path, state_path, lock_path,
                    conversation_ref: str, interval_seconds: float = 0.0,
                    max_ticks: int | None = 1,
                    max_runtime_seconds: float | None = None,
                    max_sends_per_minute: int | None = None,
                    options: Mapping | None = None,
                    provider_options: Mapping | None = None,
                    source_options: Mapping | None = None,
                    summarizer_options: Mapping | None = None) -> dict:
    """One filed registration -> one `#446` runner config.

    The watch list is *derived* from the members, never passed in: a config
    that could name different watches from the ones the dispatch declared
    would be a second declaration, and the two would drift.

    A queued member contributes no watch, exactly as it contributes no read to
    `Batch.watches()` — it is part of the dispatch but not yet readable.
    """
    batch = _batch()
    registration = _registration()
    if not isinstance(registration_value, registration.Registration):
        raise _refused(
            "BAD_REGISTRATION",
            "runner_document takes the Registration a commander filed; a Batch "
            "built in-process has no durable identity to restart from")

    path_batch = registration_value.batch
    watches = []
    for member in path_batch.members:
        if not member.watchable:
            continue
        watches.append({"source": member.source, "ref": member.ref,
                        "device_ref": member.device_ref,
                        "boot_id": member.boot_id,
                        "source_sha": member.source_sha,
                        "qualifies": True,
                        "expires_at": member.expires_at})
    if not watches:
        raise _refused(
            "NO_WATCHES",
            f"batch {registration_value.batch_id!r} has no issued Run yet; "
            f"there is nothing for a runner to watch until an ACK names one")

    # Refuse an unbounded lifecycle HERE rather than let #446 raise a config
    # error: the caller of this function is the one choosing to be a service,
    # and this contract's answer is "no".
    if max_ticks is None and max_runtime_seconds is None:
        raise _refused(
            "UNBOUNDED_LIFECYCLE",
            "a batch tick must be bounded by max_ticks or max_runtime_seconds; "
            "this module has no resident loop to hand you")

    document: dict[str, Any] = {
        "schema": _runner().SCHEMA,
        "ledger_path": _absolute(ledger_path, "ledger_path"),
        "state_path": _absolute(state_path, "state_path"),
        "lock_path": _absolute(lock_path, "lock_path"),
        "conversation_ref": conversation_ref,
        "watches": watches,
        "interval_seconds": float(interval_seconds),
        "max_ticks": max_ticks,
        "max_runtime_seconds": max_runtime_seconds,
    }
    if max_sends_per_minute is not None:
        document["max_sends_per_minute"] = max_sends_per_minute
    for name, value in (("options", options),
                        ("provider_options", provider_options),
                        ("source_options", source_options),
                        ("summarizer_options", summarizer_options)):
        if value is not None:
            document[name] = dict(value)

    # Self-check against #446's own validator. A document that does not load is
    # not a config, and finding that out at deploy time is worse than here.
    _runner().RunnerConfig.from_dict(document)
    return document


def _absolute(value: Any, name: str) -> str:
    if not isinstance(value, (str, Path)):
        raise _refused("BAD_PATH", f"{name} must be a path")
    path = Path(value)
    if not path.is_absolute():
        raise _refused("BAD_PATH", f"{name} must be absolute, not {str(path)!r}")
    return str(path)


# --------------------------------------------------------------------------- #
# one tick
# --------------------------------------------------------------------------- #
def tick_once(registration_value, *, source, notifier,
              state_path, clock: Callable[[], Any] | None = None,
              options: Any = None):
    """One correlation step over an already-loaded declaration.

    Returns the `BatchTick`. Nothing is persisted here: the delivery facts are
    #423's, the scheduling facts are #428's, and this call adds neither.
    """
    batch = _batch()
    observer = _observer()
    declaration = registration_value.batch
    watches = declaration.watches()
    if not watches:
        raise _refused("NO_WATCHES",
                       f"batch {declaration.batch_id!r} has no issued Run; "
                       f"a tick with nothing to read would be a no-op reported "
                       f"as progress")

    aggregator = batch.BatchAggregator(declaration, notifier=notifier,
                                       clock=clock)
    instance = observer.MilestoneObserver(
        source=source, notifier=notifier, state_path=state_path,
        watches=watches, clock=clock, options=options)
    return aggregator.absorb(instance.run_once())


def tick_from_file(path, *, source, notifier, state_path,
                   clock: Callable[[], Any] | None = None, options: Any = None):
    """Re-read the declaration from disk, then tick once. Returns (reg, tick).

    The re-read is the feature: a process that starts ten minutes after the
    dispatch — or after a crash — reconstructs the same Batch from the same
    bytes, so every delivery key it derives is the one the previous process
    used.
    """
    staged = _registration().load(path)
    return staged, tick_once(staged, source=source, notifier=notifier,
                             state_path=state_path, clock=clock,
                             options=options)


class Ticker:
    """Several ticks over one declaration, re-reading the file when it changes.

    The only reason this is an object rather than a function: the aggregator
    remembers which keys the ledger confirmed *within* a process, and an
    amendment must be adopted through `BatchAggregator.refresh` rather than by
    building a second aggregator. Neither behaviour is a scheduler.
    """

    def __init__(self, path, *, source, notifier, state_path,
                 clock: Callable[[], Any] | None = None, options: Any = None):
        self.path = Path(path)
        self.source = source
        self.notifier = notifier
        self.state_path = state_path
        self.clock = clock
        self.options = options
        self.registration = None
        self._digest: str | None = None
        self._aggregator = None

    def _staged(self):
        """Load the file, reloading only when its bytes changed."""
        raw = self.path.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        if self.registration is not None and digest == self._digest:
            return self.registration
        staged = _registration().load(self.path)
        if self._aggregator is None:
            self._aggregator = _batch().BatchAggregator(
                staged.batch, notifier=self.notifier, clock=self.clock)
        else:
            # A queued task learned its Run. `refresh` refuses anything else —
            # an added task, a renamed ref, a moved deadline.
            self._aggregator.refresh(staged.batch)
        self.registration = staged
        self._digest = digest
        return staged

    def tick(self):
        # Deliberately NOT `tick_once`: that would build a fresh aggregator and
        # lose the set of keys the ledger already confirmed, which is exactly
        # what keeps a second tick from sending a second alert.
        staged = self._staged()
        watches = staged.batch.watches()
        if not watches:
            raise _refused("NO_WATCHES",
                           f"batch {staged.batch_id!r} has no issued Run; "
                           f"a tick with nothing to read would be a no-op "
                           f"reported as progress")
        instance = _observer().MilestoneObserver(
            source=self.source, notifier=self.notifier,
            state_path=self.state_path, watches=watches, clock=self.clock,
            options=self.options)
        return self._aggregator.absorb(instance.run_once())


__all__ = ["runner_document", "tick_once", "tick_from_file", "Ticker"]
