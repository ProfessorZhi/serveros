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

WHAT THE FIRST VERSION OF THIS GATE MISSED
------------------------------------------
The gate first asked a weaker question — "did an earlier line in this scope read
`sys.modules`?" — as a deliberate trade, to avoid resolving names. It was too
weak, and that was measured rather than suspected: handed

    def _load(name, path):
        existing = sys.modules.get(name)     # <- a consult, and it satisfied
        spec = spec_from_file_location(...)  #    the old rule
        module = module_from_spec(spec)
        sys.modules[name] = module           # <- still displaces

the old scan returned `[]`. The write is reached whether or not `name` was
already registered, which is the whole defect, so a gate that passes it is
standing next to the property instead of holding it. `CONSULT_THEN_WRITE_ANYWAY`
and `MEMBERSHIP_THEN_WRITE_ANYWAY` below are those two sources, kept as controls.

The rule is now the property itself: **the write must happen only on the path
where the name is still missing**. Three shapes say that — an early return, a
`sys.modules.get(...)` behind a `None` test, and the `else` of a membership
test — and the tree uses all three and nothing else, measured: 192 / 18 / 1.

THE OTHER HALF, WHICH NO SCAN OF SOURCE CAN HOLD
------------------------------------------------
A guard stops a name from being TAKEN. It does not stop a file from being
EXECUTED twice: a loader registering a name nobody has used yet satisfies every
guard and still leaves the process with two module objects and two exception
classes with one spelling. Three packages of guarding left that shape standing
in this tree, twice — `context/actor.py` under two names,
`abilities/provider_instance.py` under three — and neither was visible in source
as anything but a correct guard. It was found by executing the suite and
counting executions, so that is where the two classes at the end of this file
ask it. `OneFileIsOneModuleObject` asks the loaders that exist;
`NoFileIsExecutedTwiceInOneProcess` runs a real process under
`tests/load_census.py` and reads what it did.

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

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loader_graph import (  # noqa: E402
    guarded_shapes,
    registration_sites,
    unguarded_in_source,
    unguarded_registrants,
)

LABEL = "<synthetic>"

# The consult is there, and the write is unconditional anyway. The first
# version of this gate accepted both of these; they displace the first
# registrant exactly as the shape below does, which is why they are controls
# rather than footnotes.
CONSULT_THEN_WRITE_ANYWAY = '''\
import importlib.util
import sys


def _load(name, path):
    existing = sys.modules.get(name)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
'''

MEMBERSHIP_THEN_WRITE_ANYWAY = '''\
import importlib.util
import sys


def _load(name, path):
    if name in sys.modules:
        print("already loaded")
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
'''

# A guard that reads a DIFFERENT key from the one the write takes. The shape is
# the accepted early-return, so a scan that reads shapes and never compares keys
# calls this guarded — and the `if` can never be true for the key being written,
# so the write displaces every time. `intake/understanding_brief.py`'s
# `_sibling` was this, in production, until it was found by comparing the keys:
# it tested `name` and registered `f"serveros_intake_{name}"`.
GUARD_READS_ANOTHER_KEY = '''\
import importlib.util
import sys


def _sibling(name, path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(f"serveros_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[f"serveros_{name}"] = module
    spec.loader.exec_module(module)
    return module
'''

# The same shape with the two keys agreeing, which IS a guard and must stay
# quiet. Without this the rule above could be satisfied by flagging every
# f-string key.
GUARD_READS_THE_SAME_KEY = '''\
import importlib.util
import sys


def _sibling(name, path):
    registered = f"serveros_{name}"
    if registered in sys.modules:
        return sys.modules[registered]
    spec = importlib.util.spec_from_file_location(registered, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[registered] = module
    spec.loader.exec_module(module)
    return module
'''

# A `None` test that is not about the consulted value. `flag` was never read
# out of the cache, so `if flag is None:` says nothing about whether the name
# is taken.
NONE_TEST_ON_ANOTHER_NAME = '''\
import importlib.util
import sys

flag = None
if flag is None:
    spec = importlib.util.spec_from_file_location("K", "k.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["K"] = module
    spec.loader.exec_module(module)
'''

# `or` widens a guard instead of narrowing it, so the write is reached when the
# name IS registered. Reading the whole test tree for a `not in` would call
# this guarded.
OR_WIDENS_THE_GUARD = '''\
import importlib.util
import sys

if "K" not in sys.modules or True:
    spec = importlib.util.spec_from_file_location("K", "k.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["K"] = module
    spec.loader.exec_module(module)
'''

# The `else` of a membership test: `tests/test_declared_commands.py` loads the
# CLI parser this way, and the write there is on the missing path.
GUARDED_ELSE_OF_A_PRESENT_TEST = '''\
import importlib.util
import sys

if "cli" in sys.modules:
    cli = sys.modules["cli"]
else:
    spec = importlib.util.spec_from_file_location("cli", "cli.py")
    cli = importlib.util.module_from_spec(spec)
    sys.modules["cli"] = cli
    spec.loader.exec_module(cli)
'''

# `if not sys.modules.get(K):` — the same guard as the `is None` form, written
# as a truth test.
GUARDED_NOT_GET_FORM = '''\
import importlib.util
import sys

if not sys.modules.get("K"):
    spec = importlib.util.spec_from_file_location("K", "k.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["K"] = module
    spec.loader.exec_module(module)
'''

# `and` narrows, so an extra `and True` keeps the write on the missing path.
GUARDED_AND_NARROWS = '''\
import importlib.util
import sys

if "K" not in sys.modules and True:
    spec = importlib.util.spec_from_file_location("K", "k.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["K"] = module
    spec.loader.exec_module(module)
'''

# The get-and-None shape at MODULE level, which is where
# `tests/test_bounded_executor.py`, `tests/test_daemon.py` and
# `runtime/bounded_executor.py` all register. The name bound from `.get` and the
# write are at the same scope, and the scope is the module.
GUARDED_MODULE_LEVEL_GET = '''\
import importlib.util
import sys

_spec = importlib.util.spec_from_file_location("K", "k.py")
module = sys.modules.get("K")
if module is None:
    module = importlib.util.module_from_spec(_spec)
    sys.modules["K"] = module
    _spec.loader.exec_module(module)
'''

# A `None` test on a name that came from somewhere else, at function scope,
# where the module-level version above would be checked against the module's
# own bindings.
CONSULT_IN_A_NEIGHBOUR = '''\
import importlib.util
import sys


def _one():
    return sys.modules.get("K")


def _two():
    kept = None
    if kept is None:
        spec = importlib.util.spec_from_file_location("K", "k.py")
        module = importlib.util.module_from_spec(spec)
        sys.modules["K"] = module
        spec.loader.exec_module(module)
'''

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

    def test_it_flags_a_consult_the_write_does_not_depend_on(self):
        """The first version of this gate ACCEPTED both of these. A consult that
        the write is not on the other side of leaves the write reached when the
        name is already taken, which is the defect."""
        for label, source in (("sys.modules.get", CONSULT_THEN_WRITE_ANYWAY),
                              ("`in sys.modules`", MEMBERSHIP_THEN_WRITE_ANYWAY)):
            found = unguarded_in_source(source, LABEL)
            self.assertEqual(len(found), 1, f"{label}: {found}")
            self.assertIn("in _load", found[0])

    def test_it_flags_a_none_test_on_a_name_that_did_not_come_from_the_cache(self):
        found = unguarded_in_source(NONE_TEST_ON_ANOTHER_NAME, LABEL)
        self.assertEqual(len(found), 1, found)

    def test_it_flags_a_consult_in_a_neighbouring_function(self):
        found = unguarded_in_source(CONSULT_IN_A_NEIGHBOUR, LABEL)
        self.assertEqual(len(found), 1, found)
        self.assertIn("in _two", found[0])

    def test_it_flags_a_guard_that_widens_with_or(self):
        """`if K not in sys.modules or <anything>:` reaches the write when `K`
        IS registered. Reading the whole test tree for a `not in` would accept
        it; the operand has to be the guard."""
        found = unguarded_in_source(OR_WIDENS_THE_GUARD, LABEL)
        self.assertEqual(len(found), 1, found)

    def test_it_stays_quiet_on_the_else_of_a_membership_test(self):
        self.assertEqual(unguarded_in_source(GUARDED_ELSE_OF_A_PRESENT_TEST, LABEL), [])

    def test_it_stays_quiet_on_the_negated_get_form(self):
        self.assertEqual(unguarded_in_source(GUARDED_NOT_GET_FORM, LABEL), [])

    def test_it_stays_quiet_when_the_guard_narrows_with_and(self):
        self.assertEqual(unguarded_in_source(GUARDED_AND_NARROWS, LABEL), [])

    def test_it_stays_quiet_on_the_get_form_at_module_level(self):
        """Three files in this tree register at module level, and the name
        bound from `.get` and the write are both at the module's own scope. A
        scan that looked only inside functions would report all three."""
        self.assertEqual(unguarded_in_source(GUARDED_MODULE_LEVEL_GET, LABEL), [])

    def test_it_flags_a_guard_that_reads_a_different_key_from_the_write(self):
        """The accepted shape, doing nothing.

        `if name in sys.modules` over a write to `sys.modules[f"serveros_{name}"]`
        satisfies every shape this scan knew, and guards nothing: the test can
        never be true for the key being written. Found in the tree, in
        `intake/understanding_brief.py`'s `_sibling`, by comparing the keys.
        """
        found = unguarded_in_source(GUARD_READS_ANOTHER_KEY, LABEL)
        self.assertEqual(len(found), 1, found)
        self.assertIn("in _sibling", found[0])

    def test_it_stays_quiet_when_the_guard_reads_the_key_the_write_takes(self):
        """The same two-key shape with the keys agreeing, which is the fix."""
        self.assertEqual(unguarded_in_source(GUARD_READS_THE_SAME_KEY, LABEL), [])

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

    def test_each_accepted_shape_is_seen_a_nonzero_number_of_times(self):
        """The floor the count above cannot give.

        A guard classifier that had stopped recognising guards would call every
        registration unguarded and the gate would go red — that direction is
        safe. One that had stopped recognising REGISTRATIONS would report zero
        of both, and `unguarded == []` would be green for the same reason a
        broken scan is. So the accepted shapes are counted one at a time, and
        each has to still be seen. The numbers are floors, not the readings:
        this tree has 192 / 18 / 1.
        """
        shapes = guarded_shapes()
        self.assertEqual(shapes.get("unguarded", 0), 0, shapes)
        for shape, floor in (("early-return", 150),
                             ("absent-if", 10),
                             ("present-if-else", 1)):
            self.assertGreaterEqual(
                shapes.get(shape, 0), floor,
                f"the scan no longer recognises the `{shape}` guard, which "
                f"{floor}+ registrations in this tree use: {shapes}")


class OneFileIsOneModuleObject(unittest.TestCase):
    """The other half of the property, which the scan above cannot hold.

    A guarded registration stops a name from being TAKEN. It does not stop a
    file from being EXECUTED twice, because a loader that writes a name nobody
    has used yet satisfies every guard above and still leaves this process with
    two module objects — two sets of classes, and two exception classes with one
    spelling. `runtime/approval.py` says what that costs: "a second copy would
    raise its own `Rejected` — caught by name below, and therefore slipping
    through as its parent. The grammar itself would be identical; the failure
    would be silent."

    Measured on this tree before this package, in one process:

        context/actor.py                2 names   (approval/delegation/store/trust)
        abilities/provider_instance.py  3 names   (target/runtime_binding/mcp)

    and, for the first of them, the failure itself:

        delegation raised ForgedContext
        approval's except names Rejected; caught = False

    The tests below ask the loaders that exist, rather than the source, so a
    loader that stops agreeing is a failure here instead of a silence.
    """

    def _load(self, name: str, relative: str):
        """The tree's own idiom, so the loaded module is the one the tree uses."""
        if name in sys.modules:
            return sys.modules[name]
        spec = importlib.util.spec_from_file_location(name, ROOT / relative)
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        return module

    def _actor_loaders(self) -> dict[str, object]:
        return {
            "runtime/approval.py":
                self._load("serveros_runtime_approval",
                           "runtime/approval.py")._actor_module(),
            "context/delegation.py":
                self._load("serveros_context_delegation",
                           "context/delegation.py")._actor_module(),
            "context/trust_binding.py":
                self._load("serveros_context_trust_binding",
                           "context/trust_binding.py")._actor_module(),
            "runtime/agent_control_store.py":
                self._load("serveros_runtime_agent_control_store",
                           "runtime/agent_control_store.py")._actor_module(),
        }

    def test_every_loader_of_the_actor_module_reaches_one_object(self):
        loaders = self._actor_loaders()
        distinct = {id(module) for module in loaders.values()}
        self.assertEqual(
            len(distinct), 1,
            "these loaders of `context/actor.py` did not reach the same module "
            f"object: {sorted(loaders)} -> {len(distinct)} objects. A second "
            "copy is a second `Rejected`, and an `except` written against one "
            "copy does not catch the other's refusal.")

    def test_the_actor_modules_refusal_classes_are_one_class(self):
        loaders = self._actor_loaders()
        classes = {id(module.Rejected) for module in loaders.values()}
        self.assertEqual(
            len(classes), 1,
            "`Rejected` is a different class object in different copies of "
            "`context/actor.py`; every `except Rejected` in this tree catches "
            "only the copy it was written against")

    def test_a_refusal_from_one_loader_is_caught_by_another_loader(self):
        """The symptom, not the shape: what a reader of `approval.py` sees."""
        approval = self._load("serveros_runtime_approval", "runtime/approval.py")
        delegation = self._load("serveros_context_delegation",
                                "context/delegation.py")
        with self.assertRaises(approval._actor_module().Rejected):
            delegation._actor_module().refuse_if_forged({"actor": "human:1"})

    def test_every_loader_of_the_provider_instance_module_reaches_one_object(self):
        """`ReadinessError` is the class at stake here; nothing catches it yet,
        which is why this one is a shape rather than a symptom."""
        target = self._load("serveros_abilities_target", "abilities/target.py")
        binding = self._load("serveros_abilities_runtime_binding",
                             "abilities/runtime_binding.py")
        service = self._load("serveros_agent_session_service",
                             "mcp/agent_session_service.py")
        loaders = {
            "abilities/target.py": target.provider_instance,
            "abilities/runtime_binding.py": binding._provider_instance,
            "mcp/agent_session_service.py": service.readiness,
        }
        distinct = {id(module) for module in loaders.values()}
        self.assertEqual(
            len(distinct), 1,
            "these loaders of `abilities/provider_instance.py` did not reach "
            f"the same module object: {sorted(loaders)} -> {len(distinct)}. "
            "`mcp/agent_mcp.py` composes all three into one process.")


class NoFileIsExecutedTwiceInOneProcess(unittest.TestCase):
    """The census, over a declared set, read rather than trusted.

    The scan above reads source and asks about guards. This runs a real process
    and asks what was actually executed twice, which is the only question that
    catches a loader registering a name nobody has used yet — the shape that
    survived three packages of guarding. `tests/load_census.py` is the
    instrument, and its header records what it cannot see.

    The set is declared rather than the whole suite because this measures a
    process and has to start one: `tests/test_approval.py` and
    `tests/test_wake.py` composed in one process reach both families that used
    to disagree about `context/actor.py`, and both of the loaders of
    `abilities/provider_instance.py` that did. Measured on this set, before the
    two files were given one name each:

        57 executions, 2 executed more than once
        abilities/provider_instance.py  3
        context/actor.py                2

    What it does not cover is stated rather than implied: a file loaded twice by
    a pair of loaders this set does not reach is not measured here.
    """

    DECLARED_SET = ("tests/test_approval.py", "tests/test_wake.py")

    def test_the_declared_set_executes_no_file_twice(self):
        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / "census.json"
            existing = os.environ.get("PYTHONPATH")
            env = {
                **os.environ,
                "PYTHONPATH": str(Path(__file__).resolve().parent)
                              + ((os.pathsep + existing) if existing else ""),
                "LOAD_CENSUS_OUT": str(out),
                "LOAD_CENSUS_REPO": str(ROOT),
            }
            done = subprocess.run(
                [sys.executable, "-m", "pytest", *self.DECLARED_SET,
                 "-p", "load_census", "-q"],
                cwd=ROOT, env=env, capture_output=True, text=True, timeout=900)
            self.assertEqual(
                done.returncode, 0,
                f"the measured run failed:\n{done.stdout[-3000:]}\n"
                f"{done.stderr[-3000:]}")
            census = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(census["instrument_failures"], 0, census)
            self.assertGreaterEqual(
                census["repo_files_executed"], 40,
                "the census saw too few files to be a reading: "
                f"{census['repo_files_executed']}")
            self.assertEqual(
                census["files_executed_more_than_once"], [],
                "a file was executed more than once in one process, which is "
                "two module objects and two sets of classes with one spelling: "
                f"{[row for row in census['rows'] if row['executions'] > 1]}")


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
