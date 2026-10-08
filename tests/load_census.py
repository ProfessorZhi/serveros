#!/usr/bin/env python3
"""A census of module EXECUTIONS, taken while the modules run.

`loader_graph.py` answers "is every registration guarded" by reading source. It
cannot answer the question underneath that one — *was this file's source
executed twice in this process* — and the difference is not academic. The guard
stops a name from being TAKEN. It does not stop a file from being executed
twice, because a loader that registers under a name nobody has used yet
satisfies every guard and still leaves the process holding two module objects,
two sets of classes, and two exception classes with one spelling.

Measured on this tree, before `context/actor.py` and
`abilities/provider_instance.py` were each given one name:

    context/actor.py            2  _serveros_context_actor, serveros_context_actor
    abilities/provider_instance.py  3  serveros_abilities_provider_instance,
                                       serveros_ability_provider_instance,
                                       serveros_provider_instance

`runtime/approval.py`'s own docstring records what the first of those costs: a
second copy raises its own `Rejected`, so an `except Rejected` written against
the other copy stops matching and the refusal arrives as its parent, silently.

HOW IT MEASURES
---------------
`importlib.machinery.SourceFileLoader.exec_module` is the one method every
`.py` execution goes through — `import`, importlib's own machinery, and every
`spec_from_file_location(...).loader.exec_module(...)` in this tree. For each
execution of a file under the repo root it records the name it ran as, the file
it came from, whether the name already held a DIFFERENT object before, whether
it holds this object after, and the first frame outside importlib.

Enabled as a pytest plugin, which is the only way it is started:

    PYTHONPATH=<this dir> LOAD_CENSUS_OUT=<file> LOAD_CENSUS_REPO=<repo> \\
      python -m pytest -p load_census ...

WHAT IT CANNOT SEE, WHICH IS WHY THE ROWS ARE READ RATHER THAN TRUSTED
---------------------------------------------------------------------
A loader that is not a `SourceFileLoader` (extension modules, frozen modules,
`zipimporter`), a module body run by `exec(compile(...))` rather than by a
loader — pytest's own assertion-rewriting hook runs every test module that way —
and anything executed in another process. The report carries that sentence in
its own `cannot_see` field so a reader of the JSON does not have to be told it
here.
"""
from __future__ import annotations

import atexit
import importlib.machinery
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

OUT = Path(os.environ["LOAD_CENSUS_OUT"]) if os.environ.get("LOAD_CENSUS_OUT") else None
REPO = Path(os.environ["LOAD_CENSUS_REPO"]).resolve() if os.environ.get("LOAD_CENSUS_REPO") else None
#: The substring test that keeps `resolve()` off every import of a stdlib
#: module. A path that does not spell the repo root cannot be under it, and the
#: ones that do are resolved before being believed: measured, the `resolve()`
#: alone took a full suite from 848s to over 1500s.
REPO_TEXT = str(REPO) if REPO is not None else None

_original = importlib.machinery.SourceFileLoader.exec_module

#: realpath -> one record per execution.
_executions: dict[str, list[dict]] = defaultdict(list)
_calls = 0
_instrument_failures = 0


def _caller() -> str:
    """The first frame outside importlib that led here.

    `_bootstrap._load` is frozen, and every dynamic loader in this tree calls
    `spec.loader.exec_module` itself, so the first non-importlib frame is the
    call site in either case and the two are told apart by it.
    """
    frame = sys._getframe(2)
    while frame is not None:
        filename = frame.f_code.co_filename
        if "importlib" not in filename:
            return f"{filename}:{frame.f_lineno}"
        frame = frame.f_back
    return "<unknown>"


def exec_module(self, module):
    global _calls, _instrument_failures
    _calls += 1
    try:
        if REPO_TEXT is None or REPO_TEXT not in str(self.path):
            return _original(self, module)
        path = Path(self.path)
        resolved = path.resolve()
        if REPO not in resolved.parents and resolved != REPO:
            return _original(self, module)

        spec = getattr(module, "__spec__", None)
        name = getattr(spec, "name", None) or getattr(module, "__name__", None)
        prior = sys.modules.get(name) if name else None
        caller = _caller()
    except Exception:  # the census must not be able to fail the run it measures
        _instrument_failures += 1
        return _original(self, module)

    _original(self, module)

    try:
        after = sys.modules.get(name) if name else None
        if prior is None:
            prior_kind = "absent"
        elif prior is module:
            prior_kind = "self"          # the machinery, or a guarded loader, wrote it first
        else:
            prior_kind = "other"
        _executions[str(resolved)].append({
            "name": name,
            "executed_as": str(path),
            "prior": prior_kind,
            "holds_this_after": after is module,
            "caller": caller,
        })
    except Exception:
        _instrument_failures += 1
    return None


def _names_where(predicate) -> list[str]:
    return sorted({r["name"] for records in _executions.values()
                   for r in records if r["name"] and predicate(r)})


def _write() -> None:
    if OUT is None:
        return
    rows = []
    for resolved, records in _executions.items():
        try:
            relative = str(Path(resolved).relative_to(REPO))
        except ValueError:
            relative = resolved
        rows.append({
            "file": relative,
            "executions": len(records),
            "names": sorted({r["name"] for r in records if r["name"]}),
            "records": records,
        })
    rows.sort(key=lambda row: (-row["executions"], row["file"]))
    report = {
        "repo": str(REPO),
        "source_file_executions_seen": _calls,
        "repo_files_executed": len(_executions),
        "files_executed_more_than_once": [r["file"] for r in rows if r["executions"] > 1],
        "names_that_held_a_different_object_before": _names_where(
            lambda r: r["prior"] == "other"),
        "executions_registered_over_a_different_object": _names_where(
            lambda r: r["prior"] == "other" and r["holds_this_after"]),
        "executions_leaving_the_name_on_another_object": _names_where(
            lambda r: not r["holds_this_after"]),
        "instrument_failures": _instrument_failures,
        "cannot_see": (
            "loaders that are not SourceFileLoader (extension modules, frozen "
            "modules, zipimporter); a module body run by exec(compile(...)) "
            "rather than by a loader -- pytest's assertion-rewriting hook runs "
            "every test module that way; anything executed in another process"
        ),
        "rows": rows,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2), encoding="utf-8")


importlib.machinery.SourceFileLoader.exec_module = exec_module
atexit.register(_write)
