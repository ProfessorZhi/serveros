#!/usr/bin/env python3
"""One module per name: no registration happens without looking at the cache.

THE DEFECT THIS HOLDS SHUT
--------------------------
Nothing in this tree imports its siblings by package name. Every module is
loaded by `importlib.util.spec_from_file_location(name, path)` under a synthetic
name, and `sys.modules[name] = module` is what makes that name mean anything
afterwards. The namespace is therefore shared by the whole process, so a second
loader that registers a name the first one already used does not create a second
name — it takes the name, and leaves the first loader's readers holding an
object that the name no longer reaches.

Each copy carries its own classes. `intake/workchain.py` defines `Refused`, and
`runtime/cli.py`'s `_workchain_refusals` wraps a call in `except Refused`. When
`tests/test_workchain.py` loaded the module first and `tests/test_source_policy.py`
registered the same name second, the `Refused` raised inside the wrapper was not
the `Refused` the wrapper was catching. Measured at 15ee942:

    load#1 serveros_intake_workchain by=test_workchain.py:54      Refused=597570448
    load#2 serveros_intake_workchain by=test_source_policy.py:72  Refused=599941616
    IDENTITY a is b -> False

and the nine-module batch that reads those sentences went from `1 failed` to
`1 passed` once every registrant consulted the cache first.

WHY A SCAN AND NOT A TEST OF THE SYMPTOM
----------------------------------------
The symptom is order-dependent, which is the worst property a defect can have
here: collected alphabetically, `test_source_policy.py` came before
`test_workchain.py` and the guarded loader returned the module the other file
had registered, so every assertion agreed. The batch that went red was red
because of the order its files happened to be listed in. A test that runs one
order can only ever be green.

So the property is checked where it is actually held — in the source of every
registration — and `loader_graph.unguarded_registrants()` is the scanner. Its
own header records what it does not see.

THE CONTROL IS SYNTHETIC, AND SAYS SO
-------------------------------------
After this package, no unguarded registrant remains in the tree, which is the
point of the gate and also the reason its positive control cannot be read off
the disk. `test_core_ability_boundary.py`'s is the same case and says the same
thing: `synthesized the form rather than reading a live edge, because no module
in the tree uses it today`. The red tests below hand the scanner the forms this
tree used to contain, including the exact one that caused the failure above.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loader_graph import (  # noqa: E402
    registration_sites,
    unguarded_in_source,
    unguarded_registrants,
)

LABEL = "<synthetic>"

# The shape seven test helpers in this tree used, and the one that displaced
# `serveros_intake_workchain`: a helper that registers unconditionally.
UNGUARDED_HELPER = '''\
import importlib.util
import sys


def _load(name, relative):
    spec = importlib.util.spec_from_file_location(
        "serveros_intake_" + name, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_intake_" + name] = module
    spec.loader.exec_module(module)
    return module
'''

# The shape 185 helpers in this tree use, and the one every site was moved to.
GUARDED_HELPER = '''\
import importlib.util
import sys


def _load(name, relative):
    registered = "serveros_intake_" + name
    if registered in sys.modules:
        return sys.modules[registered]
    spec = importlib.util.spec_from_file_location(
        registered, REPO_ROOT / "intake" / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[registered] = module
    spec.loader.exec_module(module)
    return module
'''

# `module = sys.modules.get(K)` then `if module is None:` — how the CLI, the
# daemon and `runtime/workspace.py` guard, and the form the second variant of
# the accepted rule exists for.
GUARDED_GET_FORM = '''\
import importlib.util
import sys

module = sys.modules.get("serveros_runtime_paths")
if module is None:
    spec = importlib.util.spec_from_file_location(
        "serveros_runtime_paths", HERE / "paths.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_runtime_paths"] = module
    spec.loader.exec_module(module)
'''

# A registration at module level behind the negated membership test the daemon
# uses. The consult and the write are on different lines, and the scan has to
# read the first one to accept the second.
GUARDED_MODULE_LEVEL = '''\
import importlib.util
import sys

if "_serveros_context_actor" not in sys.modules:
    _spec = importlib.util.spec_from_file_location("_serveros_context_actor", ACTOR)
    _module = importlib.util.module_from_spec(_spec)
    sys.modules["_serveros_context_actor"] = _module
    _spec.loader.exec_module(_module)
'''

# The two halves of the §A defect in one file: a guarded helper, and a
# registration beside it that the helper's guard does not cover. A scan that
# accepted "the file reads sys.modules somewhere above" would pass this.
GUARD_IN_A_NEIGHBOUR = '''\
import importlib.util
import sys


def _load(name, filename):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_release_spec = importlib.util.spec_from_file_location(
    "serveros_release", REPO / "scripts" / "release.py")
release = importlib.util.module_from_spec(_release_spec)
sys.modules["serveros_release"] = release
_release_spec.loader.exec_module(release)
'''

# Module-level and unguarded, with no helper anywhere in the file to borrow a
# guard from. `tests/test_alert_digest.py` registered this way inside a test
# method; `tests/test_bounded_executor.py` and `tests/test_daemon.py` did it at
# module level.
UNGUARDED_MODULE_LEVEL = '''\
import importlib.util
import sys

spec = importlib.util.spec_from_file_location(
    "serveros_bounded_executor", ROOT / "runtime" / "bounded_executor.py")
executor = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = executor
spec.loader.exec_module(executor)
'''


class TheScanCanGoRed(unittest.TestCase):
    """The positive controls. Each hands the scanner a form this tree used to
    contain and asserts it is caught, or — for the accepted forms — that it is
    not, so that "the tree is clean" is a reading rather than a silence."""

    def test_it_flags_a_helper_that_registers_without_consulting(self):
        found = unguarded_in_source(UNGUARDED_HELPER, LABEL)
        self.assertEqual(len(found), 1, found)
        self.assertIn("in _load", found[0])

    def test_it_flags_a_module_level_registration_with_no_guard(self):
        found = unguarded_in_source(UNGUARDED_MODULE_LEVEL, LABEL)
        self.assertEqual(len(found), 1, found)
        self.assertIn("in <module>", found[0])

    def test_it_stays_quiet_on_the_shape_the_tree_uses(self):
        self.assertEqual(unguarded_in_source(GUARDED_HELPER, LABEL), [])

    def test_it_stays_quiet_on_the_get_form(self):
        self.assertEqual(unguarded_in_source(GUARDED_GET_FORM, LABEL), [])

    def test_it_stays_quiet_on_a_module_level_registration_behind_a_membership_test(self):
        self.assertEqual(unguarded_in_source(GUARDED_MODULE_LEVEL, LABEL), [])

    def test_a_guard_in_a_neighbouring_function_does_not_cover_a_registration(self):
        """The §A defect exactly: `test_daemon.py` carried a guarded `_load` and,
        forty lines below it, a `serveros_release` registration with no guard of
        its own. A scan that accepted any earlier read in the file would have
        called that file clean."""
        found = unguarded_in_source(GUARD_IN_A_NEIGHBOUR, LABEL)
        self.assertEqual(len(found), 1, found)
        self.assertIn("in <module>", found[0])


class EveryRegistrationConsultsTheCacheFirst(unittest.TestCase):
    """The gate itself, over the tree rather than over a sample of it."""

    def test_no_registration_precedes_a_read_of_the_cache(self):
        found = unguarded_registrants()
        self.assertEqual(
            found, [],
            "\n".join([
                "these registrations put a module under a name without first",
                "looking at what that name already means:",
                *[f"  {site}" for site in found],
                "",
                "Every module in this tree is loaded under a synthetic name, so",
                "that name is process-wide and the second writer of it takes it",
                "from the first. Each copy carries its own classes, which is how",
                "a `Refused` gets raised as one class and caught as another.",
                "",
                "Read `sys.modules` before writing to it — `if K in sys.modules:",
                "return sys.modules[K]`, or `module = sys.modules.get(K)` with the",
                "write behind `if module is None:`. Both are already this tree's",
                "own shape, 185 times.",
            ]))

    def test_the_scan_is_seen_to_see_registrations(self):
        """A floor under the gate. "No unguarded registration" and "the scanner
        stopped recognising registrations" are both the number zero, and only
        one of them is good news."""
        sites = registration_sites()
        self.assertGreaterEqual(
            len(sites), 180,
            "the scan sees fewer registrations than this tree has; before "
            "raising or lowering this number, check that the scanner still "
            "matches the forms the tree uses:\n  "
            + "\n  ".join(sorted(sites)))
        for known in ("runtime/cli.py", "runtime/workspace.py",
                      "tests/test_source_policy.py"):
            self.assertTrue(
                any(site.startswith(known + ":") for site in sites),
                f"the scan no longer sees a registration in {known}, which has "
                f"one")


class TheFormerDefectSitesAreGuarded(unittest.TestCase):
    """Named, so that a future edit to one of these helpers has to be a decision
    rather than a drift. Each of these registered a name another file in the tree
    also registers, which is the condition the gate above is about."""

    def _consult_lines(self, relative: str) -> tuple[int, int]:
        from loader_graph import _enclosing_function, _is_consult, _registration_writes
        import ast
        source = (ROOT / relative).read_text(encoding="utf-8")
        tree = ast.parse(source)
        parents = {id(c): n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
        writes = _registration_writes(tree)
        self.assertTrue(writes, f"no registration found in {relative}")
        write = writes[0]
        own = _enclosing_function(write, parents)
        earlier = [n.lineno for n in ast.walk(tree)
                   if _is_consult(n)
                   and n.lineno < write.lineno
                   and _enclosing_function(n, parents) is own]
        return write.lineno, (min(earlier) if earlier else -1)

    def test_the_helper_that_displaced_the_workchain_module_consults_first(self):
        write, consult = self._consult_lines("tests/test_source_policy.py")
        self.assertGreater(consult, 0)
        self.assertLess(consult, write)

    def test_the_paths_loader_consults_first(self):
        write, consult = self._consult_lines("runtime/workspace.py")
        self.assertGreater(consult, 0)
        self.assertLess(consult, write)


if __name__ == "__main__":
    unittest.main()
