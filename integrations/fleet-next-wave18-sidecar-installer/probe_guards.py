#!/usr/bin/env python3
"""Withdraw one guard at a time, and name the test that notices.

A refusal that no test can observe is a refusal nobody has measured. This probe
makes the measurement mechanical: it runs the suite once to establish a green
baseline, then for every guard it changes **one** thing and requires at least one
test that was green to turn red. A guard whose withdrawal changes nothing is
reported as SILENT, and the probe exits non-zero.

Three axes, because a guard can go missing three ways:

  * **body**  — the function is replaced by one that returns None. This is the
    question "does anything depend on this check?", asked directly.
  * **tuple** — the name is dropped from the pipeline that runs it. This catches
    a guard that is correct but no longer called, which the body axis cannot see
    because it leaves the pipeline intact.
  * **helper** — a small function the guards are built on (`_is_link`,
    `_staging_candidates`, `pid_alive`) is replaced by a plausible wrong answer.
    Guards can all be present and still be wrong if what they ask is wrong.

Tests that run `installer.py` in a **separate interpreter** cannot observe an
in-process rebinding, so they are excluded and listed: including them would make
the baseline longer without making any mutation measurable.

Nothing here touches a device, a service, a port or anything outside a
`tempfile` directory. Run it from the repository root:

    python integrations/fleet-next-wave18-sidecar-installer/probe_guards.py
"""
from __future__ import annotations

import importlib.util
import sys
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TESTS = ROOT / "tests" / "test_fleet_next_wave18_sidecar_installer.py"

#: Tests that run the module in another process, and so cannot see a rebinding
#: made in this one.
EXCLUDED_CLASSES = frozenset({"IndependentProcesses"})
EXCLUDED_IDS = frozenset({
    "LockingAndConcurrency.test_another_process_holding_the_lock_refuses_this_one",
    "LockingAndConcurrency.test_a_liveness_probe_does_not_stop_the_process_it_checks",
    "SourceHygiene.test_the_module_can_be_run_as_a_script",
})


def load_tests_module():
    spec = importlib.util.spec_from_file_location("wave18_tests", str(TESTS))
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {TESTS}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["wave18_tests"] = module
    spec.loader.exec_module(module)
    return module


def selected(module) -> list[unittest.TestCase]:
    """Every test in the suite except the ones that cannot see a rebinding."""
    suite = unittest.TestLoader().loadTestsFromModule(module)
    cases: list[unittest.TestCase] = []

    def walk(item) -> None:
        if isinstance(item, unittest.TestSuite):
            for child in item:
                walk(child)
        else:
            class_name = type(item).__name__
            short = f"{class_name}.{item._testMethodName}"
            if class_name in EXCLUDED_CLASSES or short in EXCLUDED_IDS:
                return
            cases.append(item)

    walk(suite)
    return cases


def run_one(test: unittest.TestCase) -> tuple[bool, str]:
    """Run one test on a fresh instance. Returns (red, why)."""
    case = type(test)(test._testMethodName)
    result = unittest.TestResult()
    case.run(result)
    if result.failures:
        return True, (result.failures[0][1].strip().splitlines() or ["failed"])[-1]
    if result.errors:
        return True, (result.errors[0][1].strip().splitlines() or ["error"])[-1]
    return False, ""


def baseline(cases: list[unittest.TestCase]) -> tuple[list[unittest.TestCase], list[str]]:
    green: list[unittest.TestCase] = []
    red: list[str] = []
    for test in cases:
        failed, why = run_one(test)
        if failed:
            red.append(f"{type(test).__name__}.{test._testMethodName}: {why}")
        else:
            green.append(test)
    return green, red


# --------------------------------------------------------------------------- #
# the mutations
# --------------------------------------------------------------------------- #
def _allow(ctx):
    """A guard that permits everything: the check, withdrawn."""
    return None


def guard_mutations(installer) -> list[tuple[str, str, callable, callable]]:
    """(axis, label, apply, restore) for every declared guard, twice."""
    out = []
    seen: list[str] = []
    for pipeline in ("INSTALL_GUARDS", "REVOKE_GUARDS", "RESTORE_GUARDS"):
        for name in getattr(installer, pipeline):
            if name not in seen:
                seen.append(name)

    for name in seen:
        original = getattr(installer, name)

        def apply_body(name=name, original=original):
            setattr(installer, name, _allow)

        def restore_body(name=name, original=original):
            setattr(installer, name, original)

        out.append(("body", name, apply_body, restore_body))

    for pipeline in ("INSTALL_GUARDS", "REVOKE_GUARDS", "RESTORE_GUARDS"):
        original = getattr(installer, pipeline)
        for name in original:
            def apply_tuple(pipeline=pipeline, name=name):
                setattr(installer, pipeline,
                        tuple(entry for entry in getattr(installer, pipeline)
                              if entry != name))

            def restore_tuple(pipeline=pipeline, original=original):
                setattr(installer, pipeline, original)

            out.append((f"tuple:{pipeline.split('_')[0].lower()}", name,
                        apply_tuple, restore_tuple))
    return out


def helper_mutations(installer) -> list[tuple[str, str, callable, callable]]:
    """Helpers the guards are built on, replaced by a plausible wrong answer."""
    out = []

    for name, wrong in (
        # A junction is not a symlink by mode, and `_is_link` is the only thing
        # that knows it: replace it with `is_symlink` and the ancestor walk stops
        # seeing the link that redirects the write.
        ("_is_link", lambda path: Path(path).is_symlink()),
        # Debris beside the root becomes invisible, so a second install proceeds
        # past a staging directory an interrupted run left.
        ("_staging_candidates", lambda root: ()),
        # Every recorded pid counts as running, so a lock left by a dead
        # process is reported as a live writer and `recover()` can never clear it.
        ("pid_alive", lambda pid: True),
    ):
        original = getattr(installer, name)

        def apply_helper(name=name, wrong=wrong):
            setattr(installer, name, wrong)

        def restore_helper(name=name, original=original):
            setattr(installer, name, original)

        out.append(("helper", name, apply_helper, restore_helper))
    return out


# --------------------------------------------------------------------------- #
def main() -> int:
    # Withdrawing a guard lets the code *write*, so the probe runs with its
    # working directory inside a temporary directory: a mutation that lets a
    # relative root through must not land in the checkout.
    import os
    import tempfile

    here = os.getcwd()
    sandbox = tempfile.mkdtemp(prefix="wave18-probe-")
    os.chdir(sandbox)
    try:
        return _measure()
    finally:
        os.chdir(here)
        import shutil
        shutil.rmtree(sandbox, ignore_errors=True)


def _measure() -> int:
    module = load_tests_module()
    installer = sys.modules["sidecar_installer"]
    cases = selected(module)
    print(f"suite: {len(cases)} selected tests "
          f"({len(EXCLUDED_CLASSES)} class + {len(EXCLUDED_IDS)} test excluded: "
          f"they run the module in another process)")

    started = time.time()
    green, red = baseline(cases)
    print(f"baseline: {len(green)} green, {len(red)} red, "
          f"{time.time() - started:.1f}s")
    if red:
        for line in red:
            print(f"  RED  {line}")
        print("\nthe probe needs a green baseline; refusing to measure against a red one")
        return 2

    mutations = guard_mutations(installer) + helper_mutations(installer)
    silent: list[str] = []
    for axis, label, apply_mutation, restore in mutations:
        started = time.time()
        flipped = ""
        try:
            apply_mutation()
            for test in green:
                failed, why = run_one(test)
                if failed:
                    flipped = f"{type(test).__name__}.{test._testMethodName}"
                    break
        finally:
            restore()
        took = time.time() - started
        if flipped:
            print(f"  FLIPPED  {axis:<16} {label:<24} {flipped}  ({took:.1f}s)")
        else:
            print(f"  SILENT   {axis:<16} {label:<24} — nothing noticed  ({took:.1f}s)")
            silent.append(f"{axis}:{label}")

    print(f"\n{len(mutations)} mutations, {len(mutations) - len(silent)} flipped, "
          f"{len(silent)} silent")
    if silent:
        print("a guard nobody depends on is not a guard:")
        for name in silent:
            print(f"  {name}")
        return 1
    print("every declared guard, and every helper they stand on, is load-bearing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
