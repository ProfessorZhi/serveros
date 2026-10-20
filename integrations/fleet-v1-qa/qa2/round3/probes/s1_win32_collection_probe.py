#!/usr/bin/env python3
"""Synthetic-Windows collection probe (TEST-ONLY, not a real Windows run).

Windows FJPCOS cannot import `runtime/daemon.py` (it imports `fcntl`), so the
three POSIX S1 test modules must skip at module level before that import. This
probe reproduces the collection precondition on Linux:

  * the imported test module sees `os.name == "nt"`;
  * `fcntl` is unavailable;
  * pytest and pathlib keep the real POSIX os module, so the probe itself can
    build paths on Linux.

It reports, per file:

  SKIPPED    module-level pytest.skip with its reason (the expected fix)
  ERROR      collection failure (the pre-fix RED)
  COLLECTED  no module-level skip (a cross-platform module may collect)

Run each file in its own process, as `qa2`/pytest single-file collection does:

  python tests/probes/s1_win32_collection_probe.py <repo> <file> [...]

Exit: 0 when there are no collection errors (skips are fine); 1 on an error.
This is NOT real Windows evidence; the FJPCOS Agent must run pytest there.
"""
from __future__ import annotations

import importlib.util
import os as real_os
import pathlib  # noqa: F401 - imported before the os proxy; Path stays POSIX
import sys
from pathlib import Path

import pytest

ROOT = Path(sys.argv[1]).resolve()
FILES = sys.argv[2:]


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


def main() -> int:
    sys.path.insert(0, str(ROOT))
    sys.path.insert(0, str(ROOT / "tests"))
    blocker = _NoFcntl()
    sys.modules.pop("fcntl", None)
    sys.meta_path.insert(0, blocker)
    sys.modules["os"] = _WindowsOS()
    skipped = errors = collected = 0
    try:
        for name in FILES:
            path = ROOT / name
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
    print(f"summary skipped={skipped} errors={errors} collected={collected}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
