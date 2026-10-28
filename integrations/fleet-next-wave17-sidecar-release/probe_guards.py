#!/usr/bin/env python3
"""Withdraw one protection at a time and count the tests that notice.

`tests/test_fleet_next_wave17_sidecar_release.py` claims that every guard in the
revoke/restore pipelines is load-bearing. That is a claim about the tests, not
about the module, so it cannot be checked by reading the module — this script
checks it by breaking the module on purpose.

Two mutation shapes, because they fail differently:

  * **body** — the guard is replaced by a function that always refuses. If no
    test changes its outcome, no test depends on *what the guard checks*.
  * **removal** — the guard's name is deleted from the declared pipeline. If no
    test changes its outcome, no test depends on the guard being *there*, and it
    could be deleted by a future edit without a red run.

Exit status is 1 if any mutation flips nothing, so this can gate a release.

Two things it learned the hard way, both now properties of the module:

  * The pipelines must be resolved **per call**. When they were tuples of
    function objects bound at import, every body mutation was a no-op and this
    script reported thirteen guards as undepended-on — a clean bill of health
    that measured nothing. `REVOKE_GUARDS`/`RESTORE_GUARDS` hold names for
    exactly this reason.
  * A mutation may make the code *write*, because a disabled guard is a guard
    that no longer refuses. The script therefore runs with its cwd inside a
    temporary directory: it must not be able to leave anything behind, and a
    leftover from a previous run must not be able to change a result.

No network, no service, no device. Only Python temporary directories.
"""
from __future__ import annotations

import argparse
import importlib.util
import io
import os
import sys
import tempfile
import unittest
from pathlib import Path

DEFAULT_TESTS = (Path(__file__).resolve().parents[2] / "tests"
                 / "test_fleet_next_wave17_sidecar_release.py")

#: Guards whose body is replaced. The value is a Verdict factory; a constant
#: refusal is the harshest possible body, so anything that depends on the guard
#: refusing *for the right reason* will notice.
GUARD_BODIES = (
    "_guard_grant",
    "_guard_descriptor",
    "_guard_root",
    "_guard_receipt_present",
    "_guard_receipt_intact",
    "_guard_receipt_match",
    "_guard_owner_marker",
    "_guard_identity",
    "_guard_writer",
    "_guard_symlink",
    "_guard_containment",
    "_guard_files",
    "_guard_foreign",
    "_guard_previous_match",
    "_guard_backup",
    "_guard_install_root_match",
)

#: Helpers a guard delegates to. Breaking these tests the guard's *predicate*
#: rather than its wiring: a guard that calls a broken helper in the wrong
#: direction is a guard that passes everything.
HELPERS = {
    "check_descriptor": lambda d: None,          # a descriptor is always valid
    "_is_link": lambda p: False,                 # nothing is ever a link
    "_rel_path_problem": lambda p: "",           # every path is inside its root
    "_inside": lambda c, p: False,               # nothing contains anything
    "_prune_empty_dirs": lambda r: None,         # leave the shells behind
    "_root_refusal": lambda r, l: None,          # any root is acceptable
    "_file_problems": lambda r, recs: [],        # nothing is ever wrong with a file
}

PIPELINES = ("REVOKE_GUARDS", "RESTORE_GUARDS")


def load_module(tests: Path):
    spec = importlib.util.spec_from_file_location("wave17_probe_target", str(tests))
    module = importlib.util.module_from_spec(spec)
    sys.modules["wave17_probe_target"] = module
    spec.loader.exec_module(module)
    return module


def failing_ids(tests) -> tuple[int, list[str]]:
    suite = unittest.defaultTestLoader.loadTestsFromModule(tests)
    result = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    ids = sorted({f"{case.id().split('.')[-2]}.{case.id().split('.')[-1]}"
                  for case, _ in result.failures + result.errors})
    return result.testsRun, ids


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tests", default=str(DEFAULT_TESTS))
    parser.add_argument("--quiet", action="store_true",
                        help="only print the summary and any failure to flip")
    args = parser.parse_args(argv)

    tests = Path(args.tests).resolve()
    if not tests.is_file():
        print(f"test file not found: {tests}", file=sys.stderr)
        return 2

    # A disabled guard can let the code write. Keep that inside this directory.
    os.chdir(tempfile.mkdtemp(prefix="wave17-guard-probe-"))

    tests_module = load_module(tests)
    target = tests_module.m
    verdict = lambda *a, **k: target.Verdict(  # noqa: E731
        target.BLOCKED, "BLOCKED_PROBE", ("this guard was withdrawn by the probe",))

    run, baseline = failing_ids(tests_module)
    print(f"baseline: testsRun={run} flipped={len(baseline)}")
    for test_id in baseline:
        print(f"  baseline already failing: {test_id}")
    if baseline:
        print("a probe over an already-red baseline measures nothing; stopping")
        return 2

    rows: list[tuple[str, str, int, str]] = []
    weak: list[str] = []

    def mutate(axis: str, label: str, apply, restore) -> None:
        apply()
        try:
            _run, flipped = failing_ids(tests_module)
        finally:
            restore()
        rows.append((axis, label, len(flipped), flipped[0] if flipped else ""))
        if not flipped:
            weak.append(f"{axis}:{label}")

    for name in GUARD_BODIES:
        original = getattr(target, name)
        mutate("body", name,
               lambda n=name: setattr(target, n, verdict),
               lambda n=name, o=original: setattr(target, n, o))

    for name, replacement in HELPERS.items():
        original = getattr(target, name)
        mutate("helper", name,
               lambda n=name, r=replacement: setattr(target, n, r),
               lambda n=name, o=original: setattr(target, n, o))

    for pipeline in PIPELINES:
        declared = getattr(target, pipeline)
        for guard in declared:
            mutate("removal", f"{pipeline}:{guard}",
                   lambda p=pipeline, g=guard:
                       setattr(target, p, tuple(x for x in getattr(target, p) if x != g)),
                   lambda p=pipeline, d=declared: setattr(target, p, d))

    if not args.quiet:
        print()
        print(f"{'axis':8} {'protection':34} {'flipped':>7}  first test it breaks")
        print("-" * 104)
        for axis, label, count, first in rows:
            mark = "  !! NOTHING FLIPPED" if count == 0 else f"  {first}"
            print(f"{axis:8} {label:34} {count:>7}{mark}")
        print("-" * 104)

    total = len(rows)
    print(f"mutations={total}  load-bearing={total - len(weak)}  "
          f"not-load-bearing={len(weak)}")
    for name in weak:
        print(f"  !! no test depends on this: {name}")
    return 1 if weak else 0


if __name__ == "__main__":
    raise SystemExit(main())
