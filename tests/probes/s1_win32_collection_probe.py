#!/usr/bin/env python3
"""Synthetic-Windows collection probe (TEST-ONLY, not a real Windows run).

Windows FJPCOS cannot import `runtime/daemon.py` (it imports `fcntl`), so the
POSIX S1 test modules must skip at module level before that import. This probe
reproduces the collection precondition on Linux:

  * the imported test module sees `os.name == "nt"`;
  * `fcntl` is unavailable;
  * pytest and pathlib keep the real POSIX os module, so the probe itself can
    build paths on Linux.

It reports, per file:

  SKIPPED    module-level pytest.skip with its reason (the expected fix)
  ERROR      collection failure (the pre-fix RED)
  COLLECTED  no module-level skip (a cross-platform module may collect)

Usage (each file is imported in one process; pass every module you intend to
verify, including the cross-platform adapter):

  python tests/probes/s1_win32_collection_probe.py <repo> <test-file> [...]

Exit codes:
  0  at least one module was collected and there were no collection errors
     (skips are expected and are not passes)
  1  a module failed at collection (the RED this probe exists to catch)
  2  invalid invocation, or zero modules collected: a run with no arguments,
     a missing file, or only skips is not evidence that the probe ran

This is NOT real Windows evidence; the FJPCOS Agent must run pytest there.
"""
from __future__ import annotations

import importlib.util
import os as real_os
import pathlib  # noqa: F401 - imported before the os proxy; Path stays POSIX
import sys
from pathlib import Path

import pytest

USAGE = ("usage: python tests/probes/s1_win32_collection_probe.py "
         "<repo> <test-file> [<test-file> ...]")


class _UsageError(Exception):
    """The probe was not given enough valid inputs to mean anything."""


class _NoFcntl:
    def find_spec(self, fullname, path=None, target=None):
        if fullname == "fcntl":
            raise ModuleNotFoundError("No module named 'fcntl'")
        return None


class _WindowsOS:
    """What the imported test module sees: os.name == 'nt', everything else."""

    name = "nt"

    def __getattr__(self, item):
        return getattr(real_os, item)


def _parse_args(argv: list[str]) -> tuple[Path, list[tuple[str, Path]]]:
    # argv excludes the program name.
    if len(argv) < 2:
        raise _UsageError(
            "no test files: pass <repo> and at least one test file; " + USAGE)
    root = Path(argv[0]).resolve()
    if not root.is_dir():
        raise _UsageError(f"repo is not a directory: {argv[0]!r}; " + USAGE)
    files: list[tuple[str, Path]] = []
    for raw in argv[1:]:
        candidate = Path(raw)
        path = (root / candidate if not candidate.is_absolute()
                else candidate).resolve()
        if not path.is_file():
            raise _UsageError(f"test file does not exist: {raw!r}; " + USAGE)
        if path.suffix != ".py":
            raise _UsageError(f"test file is not a .py file: {raw!r}; " + USAGE)
        files.append((raw, path))
    return root, files


def main(argv: list[str]) -> int:
    try:
        root, files = _parse_args(argv)
    except _UsageError as exc:
        print(f"ERROR   {exc}")
        return 2

    sys.path.insert(0, str(root))
    sys.path.insert(0, str(root / "tests"))
    blocker = _NoFcntl()
    sys.modules.pop("fcntl", None)
    sys.meta_path.insert(0, blocker)
    sys.modules["os"] = _WindowsOS()
    skipped = errors = collected = 0
    try:
        for name, path in files:
            module_name = "simwin_" + path.stem
            try:
                spec = importlib.util.spec_from_file_location(module_name, path)
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)
            except pytest.skip.Exception as exc:
                skipped += 1
                print(f"SKIPPED {name}: {exc}")
            except BaseException as exc:  # collection error
                errors += 1
                print(f"ERROR   {name}: {type(exc).__name__}: {exc}")
            else:
                collected += 1
                print(f"COLLECTED {name}")
    finally:
        sys.modules["os"] = real_os
        try:
            sys.meta_path.remove(blocker)
        except ValueError:
            pass

    if errors:
        rc = 1
        print("ERROR   collection failed; this is the RED the probe exists to catch")
    elif collected == 0:
        rc = 2
        print("ERROR   zero modules collected: skips alone do not prove the probe "
              "ran; pass the POSIX modules plus the cross-platform adapter")
    else:
        rc = 0
    print(f"summary skipped={skipped} errors={errors} collected={collected} rc={rc}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
