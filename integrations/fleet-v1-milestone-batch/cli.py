#!/usr/bin/env python3
"""#417 Wave9 — one bounded tick of a filed Batch, runnable by a supervisor.

WHY THIS EXISTS AT ALL
----------------------
`registration.py` files the declaration, `batch.py` correlates one tick of it,
and `dispatch.py` emits the `#446` runner config for it. None of the three is
something a scheduler can start:

* `runner_document` refuses `NO_WATCHES`, so a dispatch whose ACKs carried no
  `run_id` cannot even be turned into a config;
* `tick_once` / `Ticker` refuse `NO_WATCHES` for the same reason, and that
  refusal is correct — "a tick with nothing to read would be a no-op reported as
  progress".

But a dispatch that is entirely queued then produces *silence* until a person
notices the chat stopped moving. This module is the entrypoint that answers that
case instead of refusing it, and it adds nothing else:

    绝对路径的登记文件 + 授权 factory
        -> #464 Registration.load        (the declaration, re-read from disk)
        -> #446 load_factory             (the only dynamic import there is)
        -> #423 MilestoneNotifier on #423's one ledger
        -> #428 MilestoneObserver  +  #464 BatchAggregator
        -> one tick, structured JSON, exit

It is **not** a scheduler: one invocation is one tick and it exits. Being
re-runnable is the whole design — every delivery key is derived from the batch
id and the member key, so the next invocation reads the receipt instead of
sending again. A periodic scheduler is expected to call this file, not to be
built here.

WHAT IT REFUSES TO DO
---------------------
* It never imports anything named by the registration. The registration is JSON
  read by `#464`'s loader, whose member field set is closed, so a Batch file
  cannot name a module. The only dynamic import is the operator's
  `--provider-factory` / `--source-factory`, validated by `#446`'s own
  `load_factory` (`module:callable`, identifier-checked).
* It never builds a second provider or a second ledger. One factory call, one
  `MilestoneNotifier`, one provider, closed on the way out — optionally wrapped
  in `#446`'s `RateLimitedProvider`, whose limit can turn a send into UNKNOWN
  but never into a delivery.
* It never invents a watch. When no member has a Run, no `#428` watch is built
  and no source is called; the deadline branch asks `BatchAggregator` to
  correlate an *empty* report, which is exactly "this tick established nothing".

THE THREE ANSWERS, AND WHAT EACH ONE MEANS
------------------------------------------
`state` is what the batch's correlation is; `summary.state` is what the
provider said. They are separate on purpose.

* `BLOCKED_INPUT` (exit 3) — no member has a Run and the deadline is still
  ahead. Nothing was read, nothing was sent, no ledger was opened. The document
  names every task with `verdict=PENDING`, `code=NEVER_ISSUED` and its
  `session_ref` / `delivery_id`, so the owner can see which dispatch is stuck
  without the chat history. Re-running produces the same answer until a real
  `runId` is amended into the file or the deadline passes.
* `SETTLED` (exit 0) — every member has a terminal fact and the summary is
  confirmed (`DELIVERED` or, on a later process, `DUPLICATE`).
* `UNCONFIRMED` (exit 4) — every member has a terminal fact but the summary was
  NOT confirmed by the provider (`DELIVERY_UNKNOWN`). It is reported, never
  retried under a new key, and never written to the ledger as delivered.
* `TICKED` (exit 0) — the tick ran and the batch is still open. An ACK-only
  task, a `RUN_NOT_FOUND` read and a rate-limited read all land here: the entry
  one step closer to the deadline, not a success.
* `REFUSED` (exit 2) — the entrypoint itself would not run (bad path, a
  registration the loader refuses, a factory it cannot resolve, or another tick
  holding `#446`'s single-active lock).

`why` strings in `members` are the operator's own stdout, not a message body:
`batch.py` keeps them out of every IM payload because a refusal detail can quote
what it refused. Nothing here sends them anywhere.

AUTHORITY BOUNDARY
------------------
No credential is read, no socket is opened, no model is called, no service is
started, no systemd unit is written. The provider and the source are whatever
the two factory specs return.
"""
from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import sys
from datetime import datetime, timezone
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
    return _sibling("serveros_fleet_v1_milestone_observer", "observer.py")


def _runner():
    """`#446`'s runner, read-only: the factory grammar and the lock are its."""
    return _sibling("serveros_fleet_v1_milestone_runner",
                    "../fleet-v1-milestone-runner/runner.py")


def _notify():
    """`#423`'s module as `#428` exposes it — one module object, one ledger."""
    return _observer().NOTIFY


SCHEMA = "serveros/fleet-v1-milestone-batch-cli.v1"

#: What this invocation did about the declaration. Never a claim about delivery;
#: that is `summary.state`, which carries #423's own word.
TICKED = "TICKED"
SETTLED = "SETTLED"
UNCONFIRMED = "UNCONFIRMED"
BLOCKED_INPUT = "BLOCKED_INPUT"
REFUSED = "REFUSED"

EXIT_OK = 0          # TICKED / SETTLED
EXIT_REFUSED = 2     # the entrypoint would not run
EXIT_BLOCKED = 3     # nothing was issued and the deadline is ahead
EXIT_UNCONFIRMED = 4 # settled, but the provider did not confirm the summary

#: The reason a blocked dispatch is blocked. Not `NO_WATCHES`: that is the
#: library's refusal to tick at all, and this entrypoint did answer.
NO_ISSUED_RUN = "NO_ISSUED_RUN"

#: `#464`'s word for "dispatched, never issued" (`BatchMember` / `MemberState`).
#: Restated here rather than reached for, because `_state_for` needs a notifier
#: with the seam to build and a queued dispatch has no message to send. The
#: agreement between the two is asserted by
#: `test_the_entrypoint_and_the_aggregator_agree_on_the_deadline`.
NEVER_ISSUED = "NEVER_ISSUED"

#: `#446`'s start-refusal codes, in this entrypoint's vocabulary.
_LOCK_CODES = {"ANOTHER_RUNNER_ACTIVE": "LOCKED", "LOCK_UNAVAILABLE": "LOCKED"}


class _Refused(Exception):
    """This entrypoint will not run. A closed code and one sentence."""

    def __init__(self, code: str, why: str):
        super().__init__(f"{code}: {why}")
        self.code = code
        self.why = why


# --------------------------------------------------------------------------- #
# the arguments
# --------------------------------------------------------------------------- #
def _arguments(argv):
    parser = argparse.ArgumentParser(
        prog="fleet-v1-milestone-batch-cli",
        description="Run one bounded tick of a filed Fleet V1 batch, then exit.")
    parser.add_argument("--registration", required=True,
                        help="absolute path to the #464 registration JSON")
    parser.add_argument("--ledger", required=True,
                        help="absolute path to #423's one milestone ledger")
    parser.add_argument("--state", required=True,
                        help="absolute path to #428's scheduling state file")
    parser.add_argument("--lock", default=None,
                        help="absolute path to #446's single-active lock; "
                             "optional, held for this tick when given")
    parser.add_argument("--provider-factory", required=True,
                        help="module:callable returning the one provider")
    parser.add_argument("--source-factory", required=True,
                        help="module:callable returning a SourceRead")
    parser.add_argument("--provider-options", default=None,
                        help="absolute path to a JSON object passed to the "
                             "provider factory")
    parser.add_argument("--source-options", default=None,
                        help="absolute path to a JSON object passed to the "
                             "source factory")
    parser.add_argument("--max-sends-per-minute", type=int, default=None,
                        help="wrap the provider in #446's rate limiter")
    return parser.parse_args(argv)


def _absolute(value: Any, name: str) -> Path:
    try:
        path = Path(value)
    except TypeError:
        raise _Refused("BAD_PATH", f"{name} must be a path") from None
    if not path.is_absolute():
        raise _Refused("BAD_PATH",
                       f"{name} must be absolute, not {str(path)!r}")
    return path


def _options(path, name: str) -> dict:
    if path is None:
        return {}
    path = _absolute(path, name)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise _Refused("BAD_OPTIONS",
                       f"{path.name}: {type(exc).__name__}: {exc}") from None
    try:
        value = json.loads(text)
    except ValueError as exc:
        raise _Refused("BAD_OPTIONS", f"{path.name} is not JSON: {exc}") from None
    if not isinstance(value, Mapping):
        raise _Refused("BAD_OPTIONS", f"{path.name} is not a JSON object")
    return dict(value)


# --------------------------------------------------------------------------- #
# the clock, and #464's deadline rule
# --------------------------------------------------------------------------- #
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _deadline(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        raise _Refused(
            "BAD_DEADLINE",
            f"the batch deadline {value!r} is not an ISO-8601 instant") from None
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def _deadline_passed(value: Any, now: datetime) -> bool:
    moment = _deadline(value)
    return moment is not None and now >= moment


# --------------------------------------------------------------------------- #
# the authorized factories
# --------------------------------------------------------------------------- #
def _built(factory: Callable, options: Mapping, code: str):
    """One factory call with the operator's options, as #446 makes it."""
    try:
        return factory(copy.deepcopy(dict(options)))
    except Exception as exc:
        raise _Refused(code, f"{type(exc).__name__}: {exc}") from None


def _provider(factory: Callable, options: Mapping, limit) -> Any:
    provider = _built(factory, options, "BAD_PROVIDER")
    if not callable(getattr(provider, "notify", None)) or not callable(
            getattr(provider, "capabilities", None)):
        raise _Refused(
            "BAD_PROVIDER",
            "the provider factory did not return a notifier provider "
            "(notify/capabilities); a batch summary has exactly one provider")
    if limit is None:
        return provider
    return _runner().RateLimitedProvider(provider, max_sends_per_minute=limit)


def _source(factory: Callable, options: Mapping) -> Any:
    source = _built(factory, options, "BAD_SOURCE")
    if not callable(getattr(source, "read", None)):
        raise _Refused("BAD_SOURCE",
                       "the source factory did not return a SourceRead (read)")
    return source


# --------------------------------------------------------------------------- #
# the document
# --------------------------------------------------------------------------- #
def _receipt(answer) -> dict | None:
    if answer is None:
        return None
    return {"delivery_key": getattr(answer, "delivery_key", None),
            "outcome": getattr(answer, "outcome", None),
            "state": getattr(answer, "state", None),
            "remote_id": getattr(answer, "remote_id", None),
            "delivered": bool(getattr(answer, "delivered", False))}


def _member(staged, state) -> dict:
    """One task, with the five identities kept in five fields.

    `task_id` is the dispatcher's, `session_ref` is where it was dispatched,
    `run_id` is what the source reads, `delivery_id` is the receipt that carried
    the ACK, `boot_id` is the boot the Run is scoped to. `#464`'s `Registered
    Member` refuses to pair two of them, and this projection never joins them
    either — a reader can compare them without guessing which is which.
    """
    registered = staged.member(state.member.task_id)
    member = state.member
    return {"task_id": member.task_id,
            "source": member.source,
            "device_ref": member.device_ref,
            "boot_id": member.boot_id,
            "run_id": member.ref,
            "session_ref": registered.session_ref,
            "delivery_id": registered.delivery_id,
            "verdict": state.verdict,
            "code": state.code,
            "outcome": getattr(state, "outcome", None),
            "why": getattr(state, "why", None)}


def _blocked(staged, declared: int, registration_path: Path) -> dict:
    """The docstring's `BLOCKED_INPUT`: an answer that costs nothing to give."""
    members = []
    for registered in staged.members:
        members.append({
            "task_id": registered.task_id,
            "source": registered.source,
            "device_ref": registered.device_ref,
            "boot_id": registered.boot_id,
            "run_id": registered.run_id,
            "session_ref": registered.session_ref,
            "delivery_id": registered.delivery_id,
            "verdict": _batch().PENDING,
            "code": NEVER_ISSUED,
            "outcome": None,
            "why": (f"task {registered.task_id!r} was dispatched and its ACK "
                    f"carried no runId")})
    return {"schema": SCHEMA, "state": BLOCKED_INPUT, "batch_id": staged.batch_id,
            "registration": str(registration_path),
            "observed_at": _now().isoformat(),
            "deadline": staged.expires_at,
            "watches": {"declared": declared, "issued": 0},
            "counts": {_batch().PENDING: declared},
            "settled": False,
            "members": members,
            "summary": None, "alerts": [], "suppressed": [],
            "code": NO_ISSUED_RUN,
            "why": (f"batch {staged.batch_id!r} has {declared} task(s) and not one "
                    f"has a Run; the deadline {staged.expires_at} is still ahead, "
                    f"so nothing was read and nothing was sent. BLOCKED_INPUT: "
                    f"the dispatch needs a real runId — an amendment to the "
                    f"registration — or the deadline. Re-run this entrypoint "
                    f"after either.")}


def _document(staged, tick, declared: int, issued: int,
              registration_path: Path) -> tuple[int, dict]:
    receipt = _receipt(tick.summary)
    settled = bool(tick.settled)
    if not settled:
        state, code = TICKED, EXIT_OK
    elif receipt is not None and receipt["state"] in (
            _batch().DELIVERED, _batch().DUPLICATE):
        state, code = SETTLED, EXIT_OK
    else:
        # Settled, but the provider never confirmed the one summary. Saying so
        # is the whole point: "settled" without a receipt is not a delivery, and
        # a scheduler must not read this invocation as a clean one.
        state, code = UNCONFIRMED, EXIT_UNCONFIRMED
    return code, {"schema": SCHEMA, "state": state, "batch_id": staged.batch_id,
                  "registration": str(registration_path),
                  "observed_at": tick.observed_at,
                  "deadline": staged.expires_at,
                  "watches": {"declared": declared, "issued": issued},
                  "counts": dict(tick.counts), "settled": settled,
                  "members": [_member(staged, value) for value in tick.members],
                  "summary": receipt,
                  "alerts": [_receipt(answer) for answer in tick.alerts],
                  "suppressed": list(tick.suppressed),
                  "code": None, "why": None}


# --------------------------------------------------------------------------- #
# one tick
# --------------------------------------------------------------------------- #
def _run(argv) -> tuple[int, dict]:
    arguments = _arguments(argv)

    registration_path = _absolute(arguments.registration, "registration")
    ledger_path = _absolute(arguments.ledger, "ledger")
    state_path = _absolute(arguments.state, "state")
    lock_path = (_absolute(arguments.lock, "lock")
                 if arguments.lock is not None else None)
    if arguments.max_sends_per_minute is not None and \
            arguments.max_sends_per_minute < 1:
        raise _Refused("BAD_ARGUMENT",
                       "--max-sends-per-minute must be at least 1")

    # Resolve the two specs before reading anything: #446's grammar is the
    # authority on what a factory may be named, and a spec it refuses is not a
    # spec this entrypoint gets to defer.
    provider_factory = _runner().load_factory(arguments.provider_factory)
    source_factory = _runner().load_factory(arguments.source_factory)
    provider_options = _options(arguments.provider_options, "provider_options")
    source_options = _options(arguments.source_options, "source_options")

    staged = _registration().load(registration_path)
    watches = staged.batch.watches()      # refuses NO_DEADLINE on its own
    declared = len(staged.members)

    lock = _runner().SingleActiveLock(lock_path) if lock_path is not None else None
    provider = None
    try:
        if lock is not None:
            lock.acquire()
        if not watches and not _deadline_passed(staged.batch.expires_at, _now()):
            return EXIT_BLOCKED, _blocked(staged, declared, registration_path)

        provider = _provider(provider_factory, provider_options,
                             arguments.max_sends_per_minute)
        notifier = _notify().MilestoneNotifier(
            provider, ledger_path, conversation_ref=staged.conversation_ref)
        aggregator = _batch().BatchAggregator(staged.batch, notifier=notifier)

        if not watches:
            # Past the deadline with nothing issued. No watch is invented and no
            # source is called: an EMPTY report is the honest input, and
            # #464's own deadline rule turns it into UNOBSERVED.
            tick = aggregator.absorb_rows(())
        else:
            source = _source(source_factory, source_options)
            report = _observer().MilestoneObserver(
                source=source, notifier=notifier, state_path=state_path,
                watches=watches).run_once()
            tick = aggregator.absorb(report)
        return _document(staged, tick, declared, len(watches), registration_path)
    finally:
        if provider is not None:
            close = getattr(provider, "close", None)
            if callable(close):
                close()
        if lock is not None:
            lock.release()


def _configure_stdout() -> None:
    """JSON is UTF-8 on the wire, whatever the console's code page is.

    A supervisor reads this through a pipe, and on Windows a pipe's default
    encoding is the ANSI code page (cp936 on this machine) unless it is pinned.
    The document is not ASCII: `why` carries an em dash and a settled batch
    repeats `#464`'s Chinese summary head. Left to the locale, the bytes a
    reader gets would depend on the machine that produced them — and a strict
    UTF-8 reader would then fail on a document that is perfectly well formed.
    A replaced stdout (a test's StringIO) has no `reconfigure` and is left alone.
    """
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError, OSError):
        pass


def main(argv=None) -> int:
    """Print one JSON document on stdout and return the exit code."""
    _configure_stdout()
    try:
        code, document = _run(argv)
    except _Refused as exc:
        code, document = EXIT_REFUSED, _refusal(exc.code, exc.why)
    except _batch().BatchRefused as exc:
        code, document = EXIT_REFUSED, _refusal(exc.code, exc.why)
    except _runner().RunnerError as exc:
        code, document = EXIT_REFUSED, _refusal(
            _LOCK_CODES.get(exc.code, exc.code), f"{exc.code}: {exc.why}")
    json.dump(document, sys.stdout, ensure_ascii=False, sort_keys=True)
    sys.stdout.write("\n")
    return code


def _refusal(code: str, why: str) -> dict:
    return {"schema": SCHEMA, "state": REFUSED, "code": code, "why": why}


if __name__ == "__main__":
    sys.exit(main())
