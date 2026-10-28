#!/usr/bin/env python3
"""The `serverosctl` surface a split of `runtime/cli.py` may not move.

D10 will eventually move handlers out of a 16 000-line file. Every such move is
safe until the day one of them drops a verb, rebinds a verb to a different
function, or loses a global flag on the way — and none of those three is
visible from the command that keeps working. This file is the gate that makes
them visible before a user finds them.

WHAT IS FROZEN, AND WHY EACH ONE IS THE REAL FAILURE MODE

  * **The verb set and its bindings** (`docs/architecture/cli-surface-floor-
    20261028.json`). A split's characteristic defect is a verb that stops
    existing, or one that still exists but now calls something else. Both break
    callers that this tree cannot see: 195 files name the CLI, and 660
    invocations of it are written down in tests, docs, skills, contracts and
    workbench pages.
    Additions are deliberately OUTSIDE the floor. A gate that also failed on a
    new verb would go red on every in-flight PR that adds one, and a gate that
    goes red for a reason the author cannot fix from their own branch gets
    edited rather than obeyed.

  * **Reachability, measured twice.** The floor is read from the source by
    `scripts/cli_surface_inventory.py`; this file ALSO walks the real argparse
    tree and asserts every leaf parses to a `cmd_*` function. Two independent
    readings that must agree — if the AST reader is wrong, this catches it
    instead of freezing the same mistake twice.

  * **The two global flags, on every verb.** `--json` and `--registry` live on
    an `add_help=False` parent parser, and the source records what happens when
    a later edit declares one of them again on a subparser: argparse writes into
    the same namespace, the subparser's default clobbers the parent's value, and
    `serverosctl --registry X runtime list` silently reads the default registry.
    `tests/test_view.py` proves the flag works on one verb; this proves the
    inheritance holds on all of them.

  * **The exit vocabulary.** Four constants, four meanings. A split that
    renumbers one turns a refused path into a fault in every log that reads it.

  * **One dispatch line.** `main()` returns `args.func(args)` exactly once.
    That single call is what makes a split mechanical — and what a second
    dispatch path would quietly duplicate.
"""
from __future__ import annotations

import argparse
import ast
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "runtime"
FLOOR = REPO / "docs" / "architecture" / "cli-surface-floor-20261028.json"
SCRIPT = REPO / "scripts" / "cli_surface_inventory.py"

#: The top-level directories `runtime/cli.py` loads modules from. A new entry is
#: not necessarily wrong, but it is a NEW cross-layer edge out of the CLI and
#: has to be a decision, not a side effect of adding a verb.
DECLARED_REACHES = frozenset({
    "abilities", "context", "documents", "intake", "memory", "runtime", "space",
    "(path)", "(_HERE)",
})


def _load(name: str, path: Path):
    """The repository's loader convention: consult the cache before overwriting."""
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def cli():
    return _load("serveros_cli_surface_under_test", RUNTIME / "cli.py")


@pytest.fixture(scope="module")
def inventory_script():
    return _load("serveros_cli_surface_inventory", SCRIPT)


@pytest.fixture(scope="module")
def floor() -> dict:
    return json.loads(FLOOR.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def data(inventory_script):
    """One measurement per run.

    `inventory()` parses 16 000 lines and builds the parser; calling it from
    each test made this file cost 25 seconds for 16 assertions. The measurement
    is deterministic, so sharing it is not a shortcut — it is the same reading.
    """
    return inventory_script.inventory()


def _leaf_parsers(parser: argparse.ArgumentParser, prefix: str = ""):
    """Every deepest subparser, as (path, parser). Group parsers are skipped."""
    import argparse as _a

    for action in parser._actions:
        if not isinstance(action, _a._SubParsersAction):
            continue
        for name, sub in action.choices.items():
            path = f"{prefix} {name}".strip()
            if any(isinstance(a, _a._SubParsersAction) for a in sub._actions):
                yield from _leaf_parsers(sub, path)
            else:
                yield path, sub


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse((RUNTIME / "cli.py").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def leafs(cli) -> dict:
    """Every leaf verb, as {path: parser}, built once.

    `build_parser()` constructs all 164 verbs (some in loops over tables) and
    costs about a second. Five tests read the same tree, so it is built here
    rather than five times.
    """
    return dict(_leaf_parsers(cli.build_parser()))


# ---------------------------------------------------------------------------
# The floor
# ---------------------------------------------------------------------------


class TestTheFloorHolds:
    def test_no_verb_was_lost_or_rebound(self, inventory_script, data, floor) -> None:
        """The gate. A removal or a rebinding is a compatibility break."""
        live = inventory_script.floor(data)
        lost = sorted(set(floor["verbs"]) - set(live["verbs"]))
        rebound = sorted(path for path in set(floor["verbs"]) & set(live["verbs"])
                         if floor["verbs"][path] != live["verbs"][path])
        assert lost == [], (
            "these verbs no longer parse to a handler, so every caller of them "
            f"now fails: {lost}")
        rebindings = [(path, floor["verbs"][path], live["verbs"][path])
                      for path in rebound]
        assert rebound == [], (
            "these verbs were rebound to a different handler, which changes "
            f"what they do without changing what they are called: {rebindings}")

    def test_the_floor_is_not_a_smaller_list_that_still_passes(self, floor) -> None:
        """A floor measured from an empty parse would satisfy the test above."""
        assert len(floor["verbs"]) >= 120, len(floor["verbs"])
        assert len(floor["top_level"]) >= 35, floor["top_level"]
        assert floor["schema"] == "serveros/cli-surface-floor.v1"

    def test_the_floor_names_every_top_level_command_the_parser_has(self, floor) -> None:
        declared = {path.split()[0] for path in floor["verbs"]}
        assert set(floor["top_level"]) == declared, (
            "the floor's two sections disagree, so one of them was edited by hand")


# ---------------------------------------------------------------------------
# Two independent readings of the same surface
# ---------------------------------------------------------------------------


class TestEveryLeafVerbReachesAHandler:
    def test_every_leaf_parses_to_a_callable(self, leafs) -> None:
        assert len(leafs) >= 120, sorted(leafs)
        missing = sorted(path for path, sub in leafs.items()
                         if not callable(sub.get_default("func")))
        assert missing == [], (
            "these verbs parse but dispatch to nothing, which argparse accepts "
            f"and running them raises AttributeError: {missing}")

    def test_every_leaf_handler_is_a_cmd_function_in_this_file(self, leafs) -> None:
        """A split moves `cmd_*` functions; a lambda or a closure cannot move."""
        odd = sorted({sub.get_default("func").__name__ for sub in leafs.values()
                      if not sub.get_default("func").__name__.startswith("cmd_")})
        assert odd == [], odd

    def test_the_source_scan_is_a_strict_subset_of_the_parser(
            self, leafs, data) -> None:
        """The two readings must NOT agree, and the gap is the finding.

        Three groups (`lease`, `test`, `architecture`) declare their verbs in a
        loop over a table, so the names are values rather than literals. A
        source scan — regex or AST — cannot see them, and it is short by 28
        verbs: 17% of the surface.

        This assertion is written as "strict subset with a floor on the gap"
        rather than "equal" because the earlier, tempting form (equal) was the
        one that failed first and thereby found this. If someone later makes the
        scan see the table-driven verbs, the gap collapses to zero and this
        test says so instead of silently passing.
        """
        from_parser = set(leafs)
        from_source = set(data["source_scan"])
        assert from_source < from_parser, (
            "the source scan is no longer a strict subset — either it caught up "
            f"(then delete this class's premise) or it broke: "
            f"{sorted(from_source - from_parser)}")
        assert len(from_parser - from_source) >= 20, len(from_parser - from_source)
        assert set(data["not_in_source_scan"]) == from_parser - from_source

    def test_the_parser_still_covers_the_whole_floor(self, leafs, floor) -> None:
        """A floor is a floor: additions are allowed, losses are not."""
        assert set(floor["verbs"]) <= set(leafs)


# ---------------------------------------------------------------------------
# The two global flags
# ---------------------------------------------------------------------------


class TestTheGlobalFlagsAreInheritedByEveryVerb:
    def test_every_leaf_declares_both_dests(self, leafs) -> None:
        """Structural sweep: the parent's actions are inherited by every child.

        Checked on the parser objects rather than by parsing argv, because most
        verbs have required positionals: a `parse_args([path, "--json"])` on
        those exits 2 for the missing argument and says nothing about the flag.

        `workengine` is the one declared exception: it forwards its whole tail
        to the engine's own parser, so claiming `--json` here would be a second
        surface for the same flag. It is listed rather than skipped, because an
        exception written as a filter is an exception that grows.
        """
        broken = sorted(path for path, sub in leafs.items()
                        if not {"json", "registry"} <= {a.dest for a in sub._actions})
        assert broken == ["workengine"], (
            "these verbs would reject the global flags, and only the verb that "
            f"forwards its tail is allowed to: {broken}")

    def test_the_deepest_verb_takes_the_registry_on_either_side(self, cli) -> None:
        """A behavioural control at depth 3, where inheritance is least obvious."""
        args = cli.build_parser().parse_args(
            ["work", "trust", "status", "--registry", "/tmp/x.yaml"])
        assert args.registry == "/tmp/x.yaml"
        args = cli.build_parser().parse_args(
            ["--registry", "/tmp/x.yaml", "work", "trust", "status"])
        assert args.registry == "/tmp/x.yaml", (
            "the flag before the subcommand was clobbered — this is the "
            "SUPPRESS interaction recorded in build_parser's comments")

    def test_an_unset_flag_is_absent_and_then_normalised(self, cli) -> None:
        """`main()` fills the missing attributes once; no subparser has a default."""
        parser = cli.build_parser()
        args = parser.parse_args(["work", "trust", "status"])
        assert not hasattr(args, "json") and not hasattr(args, "registry"), (
            "a default leaked onto a subparser; a parent-supplied value would "
            "now be overwritten by it")


# ---------------------------------------------------------------------------
# Exit codes and dispatch
# ---------------------------------------------------------------------------


class TestTheExitVocabularyIsFixed:
    def test_the_four_meanings_keep_their_numbers(self, cli) -> None:
        assert cli.EXIT_OK == 0
        assert cli.EXIT_FAULT == 1
        assert cli.EXIT_COULD_NOT_ANSWER == 2
        assert cli.EXIT_REFUSED == 3

    def test_nothing_returns_an_exit_code_that_is_not_declared(self, tree) -> None:
        """A literal `return 4` would be a fifth meaning nobody documented."""
        declared = {name for node in tree.body if isinstance(node, ast.Assign)
                    for name in (t.id for t in node.targets
                                 if isinstance(t, ast.Name))
                    if name.startswith("EXIT_")}
        assert declared == {"EXIT_OK", "EXIT_FAULT", "EXIT_COULD_NOT_ANSWER",
                            "EXIT_REFUSED"}, sorted(declared)


class TestDispatchHasOneShape:
    def test_main_dispatches_through_args_func_exactly_once(self, tree) -> None:
        main = next(n for n in tree.body
                    if isinstance(n, ast.FunctionDef) and n.name == "main")
        calls = [n for n in ast.walk(main)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "func"]
        assert len(calls) == 1, (
            "the split assumed a single dispatch point; there are "
            f"{len(calls)}")

    def test_no_function_is_defined_twice_in_the_module(self, tree) -> None:
        """A copy of a handler is the drift a move is supposed to avoid."""
        names = [n.name for n in tree.body if isinstance(n, ast.FunctionDef)]
        duplicates = sorted({n for n in names if names.count(n) > 1})
        assert duplicates == [], duplicates


# ---------------------------------------------------------------------------
# What the CLI reaches into
# ---------------------------------------------------------------------------


class TestTheCliReachesOnlyTheDeclaredLayers:
    def test_no_new_top_level_directory_appears(self, data) -> None:
        reaches = data["reaches"]
        unexpected = sorted(set(reaches) - set(DECLARED_REACHES))
        assert unexpected == [], (
            "runtime/cli.py now loads from directories nobody declared. That is "
            "a new cross-layer edge out of the CLI, and it changes what a split "
            f"has to carry: {unexpected}")

    def test_the_reach_census_is_not_empty(self, data) -> None:
        """The control: a scan that found nothing must not read as 'no edges'."""
        assert data["load_sites"] >= 50
