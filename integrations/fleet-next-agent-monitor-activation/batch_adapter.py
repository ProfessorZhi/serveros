#!/usr/bin/env python3
"""The one place the activation wrapper is allowed to reach #481's Batch CLI.

WHY THIS IS A SEPARATE MODULE AND NOT `module:callable`
-------------------------------------------------------
`activation.py` takes a `module:callable` monitor entry because that is the
smallest thing a supervisor can name. #481's CLI is not that shape:

* it needs argv (`--registration`, `--ledger`, `--state`, `--provider-factory`,
  `--source-factory`, …), and a bare callable gets none;
* its public `main(argv)` returns an `int` exit code and prints JSON to stdout,
  not a mapping the wrapper can read;
* its private `_run(argv)` returns `(exit_code, document)` — the only shape that
  carries the batch's answer.

So a zero-argument adapter calling `cli.main` would either crash or, worse,
return a plausible-looking status that says nothing about the batch.

WHAT THE ADAPTER MAY AND MAY NOT READ
-------------------------------------
The argv is built **only** from `BatchCliConfig`, which is operator-supplied and
fixed. Nothing here reads a path, a module name or a factory spec out of the
Batch registration: #481's CLI is itself strict about that ("It never imports
anything named by the registration"), and an adapter that reintroduced the hole
from the other side would undo it. The only two dynamic imports are the operator's
`--provider-factory` / `--source-factory` strings, which are handed to the CLI as
strings and validated by #446's `load_factory` — never imported here.

There is no second provider and no second ledger: `_run` builds exactly one of
each, and this module only reads its returned document.
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

BLOCKED_RUNTIME_IMPORT = "BLOCKED_RUNTIME_IMPORT"
UNRECOGNISED = "UNRECOGNISED"

#: The states #481's CLI documents. Anything else is refused rather than passed
#: through: an unknown word is not a status, and treating it as one is how a
#: monitor reports on a batch it did not understand.
CLI_STATES = frozenset({"SETTLED", "UNCONFIRMED", "BLOCKED_INPUT", "TICKED",
                        "REFUSED"})

#: The only words that mean the summary actually reached the provider.
DELIVERED_STATES = frozenset({"DELIVERED", "DUPLICATE"})


#: One CLI per absolute path, per process. Not one CLI per process: two configs
#: with different paths must not share an import.
_LOADED: dict[str, Any] = {}


class AdapterRefused(Exception):
    def __init__(self, code: str, why: str):
        self.code = code
        self.why = why
        super().__init__(f"{code}: {why}")


@dataclass(frozen=True)
class BatchCliConfig:
    """Operator configuration. Every field is a choice, none is a discovery."""

    cli_path: str
    registration: str
    ledger: str
    state: str
    provider_factory: str
    source_factory: str
    lock: str | None = None
    provider_options: str | None = None
    source_options: str | None = None
    max_sends_per_minute: int | None = None


def _absolute(value: Any, name: str) -> str:
    text = str(value or "")
    if not text:
        raise AdapterRefused(BLOCKED_RUNTIME_IMPORT, f"{name} is required")
    if not Path(text).is_absolute():
        raise AdapterRefused(
            BLOCKED_RUNTIME_IMPORT,
            f"{name} must be an absolute path, got {text!r}; a relative path is "
            "resolved against whatever cwd the supervisor happens to use")
    return text


def _factory_spec(value: Any, name: str) -> str:
    text = str(value or "")
    module, sep, attribute = text.partition(":")
    if not sep or not module or not attribute or "." in attribute:
        raise AdapterRefused(
            BLOCKED_RUNTIME_IMPORT,
            f"{name} must be `module:callable`, got {text!r}")
    return text


def argv_for(config: BatchCliConfig) -> list[str]:
    """The CLI's argv, from the operator's configuration and nothing else."""
    argv = ["--registration", _absolute(config.registration, "registration"),
            "--ledger", _absolute(config.ledger, "ledger"),
            "--state", _absolute(config.state, "state"),
            "--provider-factory",
            _factory_spec(config.provider_factory, "provider_factory"),
            "--source-factory",
            _factory_spec(config.source_factory, "source_factory")]
    if config.lock:
        argv += ["--lock", _absolute(config.lock, "lock")]
    if config.provider_options:
        argv += ["--provider-options",
                 _absolute(config.provider_options, "provider_options")]
    if config.source_options:
        argv += ["--source-options",
                 _absolute(config.source_options, "source_options")]
    if config.max_sends_per_minute is not None:
        argv += ["--max-sends-per-minute", str(int(config.max_sends_per_minute))]
    return argv


def load_cli(config: BatchCliConfig):
    """#481's CLI, loaded from the operator's path.

    A missing or unimportable CLI is `BLOCKED_RUNTIME_IMPORT` — not an empty tick
    and not a green. The supervisor is allowed to find nothing to run; it is not
    allowed to pretend it ran.
    """
    path = Path(_absolute(config.cli_path, "cli_path"))
    if not path.is_file():
        raise AdapterRefused(
            BLOCKED_RUNTIME_IMPORT,
            f"no Batch CLI at {str(path)!r}; the supervisor has nothing to run")
    # Keyed by the RESOLVED path, not by a fixed name. A fixed name made the
    # second configuration in one process silently reuse the first CLI, which is
    # exactly the kind of "it ran" that is really "something else ran".
    key = path.resolve().as_posix()
    cached = _LOADED.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        "serveros_next_activation_batch_cli_" + format(abs(hash(key)) & 0xFFFFFFFF, "08x"),
        path)
    if spec is None or spec.loader is None:
        raise AdapterRefused(BLOCKED_RUNTIME_IMPORT,
                             f"{str(path)!r} is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as exc:  # noqa: BLE001 - any failure is "no entry"
        sys.modules.pop(spec.name, None)
        raise AdapterRefused(
            BLOCKED_RUNTIME_IMPORT,
            f"{str(path)!r} raised on import ({type(exc).__name__}: {exc})")
    _LOADED[key] = module
    if not callable(getattr(module, "_run", None)):
        raise AdapterRefused(
            BLOCKED_RUNTIME_IMPORT,
            f"{str(path)!r} has no `_run(argv) -> (exit_code, document)`; a "
            "`main` that only returns an exit code is not a batch answer")
    return module


def row_from(document: Mapping[str, Any], exit_code: int) -> dict:
    """One monitor row, carrying the CLI's word rather than a summary of it."""
    state = document.get("state")
    if state not in CLI_STATES:
        return {"state": UNRECOGNISED, "cli_state": state, "exit": exit_code,
                "green": False, "summary_state": None,
                "why": f"the CLI reported {state!r}, which is not one of "
                       f"{sorted(CLI_STATES)}"}
    summary = document.get("summary")
    summary_state = summary.get("state") if isinstance(summary, Mapping) else None
    return {
        "state": state,
        "cli_state": state,
        "exit": exit_code,
        "batch_id": document.get("batch_id"),
        "summary_state": summary_state,
        "green": is_green(state, summary_state),
        # An unconfirmed summary is exactly one undelivered receipt; a blocked
        # input names no delivery at all, so it is not counted as one.
        "unknown_receipts": 1 if state == "UNCONFIRMED" else 0,
        "backlog": 0,
        "why": document.get("why") or document.get("code") or "",
    }


def is_green(state: Any, summary_state: Any) -> bool:
    """Green is a *terminal batch whose summary was confirmed*, nothing else.

    This is the rule the owner asked to be unflattenable:

    * `TICKED` is progress, not success — an ACK-only member lands here.
    * `BLOCKED_INPUT` is a dispatch no run ever reached — never green.
    * `UNCONFIRMED` has terminal facts but no delivered summary — never green.
    * `SETTLED` is green only with a `DELIVERED`/`DUPLICATE` summary receipt.
    """
    return state == "SETTLED" and summary_state in DELIVERED_STATES


def tick(config: BatchCliConfig) -> dict:
    """One bounded CLI invocation, as a monitor row."""
    module = load_cli(config)
    exit_code, document = module._run(argv_for(config))
    if not isinstance(document, Mapping):
        return {"state": UNRECOGNISED, "cli_state": None, "exit": exit_code,
                "green": False, "summary_state": None,
                "why": f"the CLI returned {type(document).__name__}, not a document"}
    return row_from(document, exit_code)
