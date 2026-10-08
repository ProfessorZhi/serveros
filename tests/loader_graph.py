#!/usr/bin/env python3
"""Who can load whom, in a repository that does not use `import`.

WHY THIS IS NOT AN IMPORT SCAN
------------------------------
Nothing in this tree imports its siblings by package name. Every module is
loaded by `importlib.util.spec_from_file_location` under a synthetic name, so
the dependency graph is carried as *string arguments to loader calls* and is
entirely invisible to `import x` / `from x import y`.

Three successive scanners were written against this tree before this one, and
each was blind to a form the previous one handled:

  1. an import scan                      — found nothing anywhere, and was
                                           read as "the tree has no cross-
                                           directory dependencies". It was not
                                           true; it was not read.
  2. loader-call arguments only          — missed `runtime/daemon.py`'s edge to
                                           `context/actor.py`, which is bound to
                                           a module-level name first:
                                           `_ACTOR_PATH = _HERE.parent /
                                           "context" / "actor.py"`.
  3. (2) plus that one shape             — missed `runtime/cli.py`'s SECOND
                                           loader, `_load_module_from(path,
                                           name)`, which takes a `Path` rather
                                           than a filename and reaches
                                           `abilities/resolver.py` and four
                                           others.

So the claim "no module under `runtime/` loads anything from `abilities/`" was
false while two scanners said it was true. The forms below are precisely the
ones this tree uses, and `test_core_ability_boundary.py` carries a positive
control asserting that the specific edge scanners 2 and 3 missed is found.

WHAT IT DOES NOT DO
-------------------
Reflection is not followed. A path built by `f"{name}.py"` or read from a
config file is not an edge, and neither is a module reached through
`getattr`. That is a real limit rather than a detail: this scan can say "the
core does not load a provider", and it cannot say "nothing ever will". The
gates built on it assert the first thing.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Names a loader call by. `_load` and `_load_module_from` are this repo's two
# helpers; the others are the stdlib primitives they are built from, and are
# listed so a module that calls the primitive directly is still seen.
LOADER_NAMES = frozenset({
    "_load", "_load_module_from", "load_module", "import_module",
    "spec_from_file_location", "module_from_spec",
})


def _prose_nodes(tree: ast.AST) -> set[int]:
    """Every string constant that is a bare expression statement.

    These are prose. Several modules discuss filenames in their docstrings, and
    a filename inside a sentence is not a reference to anything.
    """
    skip: set[int] = set()
    for node in ast.walk(tree):
        if (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            skip.add(id(node.value))
    return skip


def module_refs(path: Path) -> set[Path]:
    """Every python module `path` can load, as paths relative to `REPO`.

    Handles the three forms this tree uses:

      * `import x` / `from x import y`         — the ordinary form. It appears
                                                 nowhere between sibling
                                                 directories today, and is read
                                                 anyway so that a scan cannot go
                                                 quiet when one is added.
      * `_load("name", "registry.py")`         — a bare filename, resolved
                                                 beside the calling module,
                                                 because that is what the
                                                 helper does with it.
      * `_load("name", "mcp/botmux_provider.py")` — the same call with a path
                                                 relative to the repo root.
                                                 `resolve()` already tried
                                                 `REPO / candidate` first, so
                                                 this cost nothing to support;
                                                 it was excluded by a `"/" not
                                                 in ...` guard until §C's
                                                 mutation testing showed the
                                                 guard was the whole of the
                                                 blind spot (below).
      * `_load_module_from(REPO / "a" / "b.py")` — a division chain, joined from
                                                 its string parts.

    THE GUARD THAT WAS REMOVED, AND WHY IT MATTERED
    -----------------------------------------------
    The loader-argument branch used to skip any string constant containing a
    `/`. The intent was to avoid reading a version string or a sentence as a
    path, but `resolve()` already refuses anything that is not an existing
    `.py` file, so the guard bought nothing and cost the repo-root-relative
    form outright.

    Measured, not reasoned: injecting `_load("serveros_botmux_provider",
    "mcp/botmux_provider.py")` into `runtime/daemon.py` left
    `closure(daemon.py)` unchanged, so §C's "none of the outer systems is
    reachable from the daemon" passed while the daemon loaded a provider.
    That is the failure this module's own header warns about — "the scanner
    said it was true" — reproduced on the newest gate rather than the oldest.
    `test_the_scanner_sees_a_slash_bearing_loader_argument` is the positive
    control, and it synthesizes the form rather than reading a live edge,
    because no module in the tree uses it today.
    """
    path = path if path.is_absolute() else REPO / path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    prose = _prose_nodes(tree)
    found: set[Path] = set()

    def resolve(candidate: str) -> None:
        candidate = candidate.strip("/")
        if not candidate.endswith(".py"):
            return
        target = REPO / candidate
        if target.is_file():
            found.add(target.relative_to(REPO))
            return
        beside = path.parent / Path(candidate).name
        if beside.is_file():
            found.add(beside.relative_to(REPO))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                resolve("/".join(alias.name.split(".")) + ".py")
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            resolve("/".join(node.module.split(".")) + ".py")

    # Division chains, collected anywhere in the file rather than only inside a
    # loader call. `_ACTOR_PATH = _HERE.parent / "context" / "actor.py"` is
    # bound to a name first and handed to the loader second.
    for node in ast.walk(tree):
        if not isinstance(node, ast.BinOp) or not isinstance(node.op, ast.Div):
            continue
        parts: list[str] = []
        current: ast.AST = node
        while isinstance(current, ast.BinOp) and isinstance(current.op, ast.Div):
            if isinstance(current.right, ast.Constant) and isinstance(current.right.value, str):
                parts.append(current.right.value)
                current = current.left
            else:
                break
        if parts:
            resolve("/".join(reversed(parts)))

    # Filenames handed to a loader: beside the caller, or relative to the repo
    # root. Both go through `resolve()`, which only accepts an existing `.py`
    # file, so no guard on the string's shape is needed — and the one that used
    # to be here is what hid the repo-root-relative form (see the docstring).
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else (
            fn.id if isinstance(fn, ast.Name) else None)
        if name not in LOADER_NAMES:
            continue
        for arg in node.args:
            if (isinstance(arg, ast.Constant) and isinstance(arg.value, str)
                    and id(arg) not in prose):
                resolve(arg.value)

    return found


def closure(entry: Path) -> set[Path]:
    """Every module reachable from `entry` by loading, transitively."""
    seen: set[Path] = set()
    frontier = [entry if not entry.is_absolute() else entry.relative_to(REPO)]
    while frontier:
        current = frontier.pop()
        if current in seen or not (REPO / current).is_file():
            continue
        seen.add(current)
        for reference in module_refs(current):
            if reference not in seen:
                frontier.append(reference)
    return seen


# What the composition ships and starts. Each is an entry point a node runs:
# the two CLI/daemon shims, the two MCP adapters a manifest names, and the
# modules behind them.
#
# This is the one place a shipped surface is named as an entry, and it is
# deliberately short. It is NOT a second module list: what each entry needs is
# computed from this tree by `production_closure()`, so naming an entry here
# cannot disagree with what that entry actually loads.
PRODUCTION_ENTRIES = (
    "bin/serverosctl",
    "bin/serverosd",
    "mcp/bin/serveros-node-mcp",
    "mcp/bin/serveros-agent-mcp",
    "runtime/cli.py",
    "runtime/daemon.py",
    "mcp/node_mcp.py",
    "mcp/agent_mcp.py",
)


def production_closure() -> set[Path]:
    """Every repository file the shipped surfaces need, transitively.

    This is the packaging REQUIREMENT — the set an artifact must contain for the
    surfaces it declares to run. `scripts/build.py`'s `CORE_MODULES` is how the
    builder satisfies it and is not the statement of it.

    The distinction is the whole of the C4 defect. A gate that reads
    `CORE_MODULES` asks "is everything on this list present", which is true by
    construction for every module that was put on the list — and says nothing at
    all about a module nobody put there. Deriving the set from the loader graph
    instead makes the question "is everything these surfaces load present",
    which a forgotten module fails rather than evades.

    The scan's limits are `loader_graph`'s: reflection is not followed, and a
    path built by f-string or read from a config file is not an edge. So this is
    a LOWER bound on what a surface needs — it can say "this artifact is missing
    something its CLI loads", and cannot say "nothing else is missing".
    """
    needed: set[Path] = set()
    for entry in PRODUCTION_ENTRIES:
        needed.add(Path(entry))
        needed |= closure(Path(entry))
    return needed


def missing_from(root: Path) -> list[Path]:
    """The subset of `production_closure()` that is not a file under `root`.

    Relative paths, sorted, so a red gate prints the same list twice.
    """
    return sorted(path for path in production_closure()
                  if not (root / path).is_file())


# ---------------------------------------------------------------------------
# Who consults the cache before overwriting it
# ---------------------------------------------------------------------------
#
# `spec_from_file_location(name, path)` takes a NAME as well as a path, and
# `sys.modules[name] = module` is what makes that name mean anything afterwards.
# Because every module in this tree is loaded under a synthetic name, that
# namespace is shared: two loaders that register one name do not each get a
# module, the second one gets the name and the first one's readers keep the
# object they already hold.
#
# That is not a cosmetic duplication. Each copy carries its own classes, so a
# `Refused` raised by one is not the `Refused` an `except` in the other was
# written against. Measured on this tree at 15ee942, with
# `tests/test_workchain.py` collected before `tests/test_source_policy.py`:
#
#     load#1 serveros_intake_workchain by=test_workchain.py:54      Refused=597570448
#     load#2 serveros_intake_workchain by=test_source_policy.py:72  Refused=599941616
#     IDENTITY a is b -> False
#
# and the nine-module batch that reads those sentences went from 1 failed to 1
# passed once each registrant consulted the cache first.
#
# THE FORM THIS SCANNER ACCEPTS
# -----------------------------
# A scope that writes `sys.modules[...]` must read the cache at an earlier line
# in the same scope: `K in sys.modules`, `sys.modules.get(K)` or
# `sys.modules[K]` on the right-hand side. `if name in sys.modules: return
# sys.modules[name]` and `module = sys.modules.get(name); if module is None:`
# are both this repository's own guarded shape.
#
# WHAT IT DOES NOT DO
# -------------------
# Ordering is by line within one scope, and the scope is the innermost enclosing
# function. A guard living in the caller of the writer is not modelled, and
# neither is an aliased `sys` (`import sys as _s`). Both are absent from this
# tree; a gate that says "every registrant consults first" would be a stronger
# claim than this scan can make, so it does not make it.
#
# The consult also does not have to name the key being written: any earlier read
# of `sys.modules` in the scope satisfies the scan, because the property being
# enforced is structural — a registration is preceded by a look at the cache —
# rather than "the guard is about this key". A consult in a DIFFERENT scope,
# which is what a helper's own `if name in sys.modules` is to a registration
# written beside it, does not count; that distinction is the whole of the
# §A defect this was written for, where a guarded helper and an unguarded one
# sat in the same file.


def _is_sys_modules(node: ast.AST) -> bool:
    """`sys.modules`, as an expression."""
    return (isinstance(node, ast.Attribute) and node.attr == "modules"
            and isinstance(node.value, ast.Name) and node.value.id == "sys")


def _is_consult(node: ast.AST) -> bool:
    """A read of `sys.modules`: `sys.modules[K]`, `sys.modules.get(K)`, `K in sys.modules`."""
    if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get" and _is_sys_modules(node.func.value)):
        return True
    if (isinstance(node, ast.Subscript) and isinstance(node.ctx, ast.Load)
            and _is_sys_modules(node.value)):
        return True
    if isinstance(node, ast.Compare):
        return any(isinstance(op, (ast.In, ast.NotIn)) and _is_sys_modules(right)
                   for op, right in zip(node.ops, node.comparators))
    return False


def _registration_writes(tree: ast.AST) -> list[ast.Assign]:
    """Every `sys.modules[...] = ...` in `tree`."""
    writes: list[ast.Assign] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if (isinstance(target, ast.Subscript) and isinstance(target.ctx, ast.Store)
                and _is_sys_modules(target.value)):
            writes.append(node)
    return writes


def _enclosing_function(node: ast.AST, parents: dict[int, ast.AST]):
    """The innermost function containing `node`, or `None` at module level."""
    current = parents.get(id(node))
    while current is not None:
        if isinstance(current, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return current
        current = parents.get(id(current))
    return None


def unguarded_in_source(source: str, label: str) -> list[str]:
    """Registrations in `source` with no earlier read of the cache to check against.

    "Earlier" and "same scope" are both literal: the consult must be at a lower
    line number and in the same enclosing function — or, at module level, in the
    module's own top level. A guard that lives in some other function of the
    same file, which is what a helper's own `if name in sys.modules` is, does
    not count for a registration made beside it.

    Takes source rather than a path so the control below can hand it a form this
    tree no longer contains; `label` is only what a finding is printed as.
    """
    tree = ast.parse(source)
    parents = {id(child): node
               for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    consults = [(node.lineno, _enclosing_function(node, parents))
                for node in ast.walk(tree) if _is_consult(node)]
    found: list[str] = []
    for write in _registration_writes(tree):
        own = _enclosing_function(write, parents)
        if any(line < write.lineno and scope is own for line, scope in consults):
            continue
        name = getattr(own, "name", "<module>")
        found.append(f"{label}:{write.lineno} in {name}")
    return found


def unguarded_registrations(path: Path) -> list[str]:
    """`unguarded_in_source` for one file of the tree."""
    return unguarded_in_source(path.read_text(encoding="utf-8"),
                               str(path.relative_to(REPO)))


def registration_sites(root: Path = REPO) -> list[str]:
    """EVERY registration in the tree, guarded or not.

    The floor under the gate: "no unguarded registration" is worth nothing if
    the scan has stopped seeing registrations at all, and the two are
    indistinguishable from the number zero.
    """
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        found.extend(f"{path.relative_to(REPO)}:{write.lineno}"
                     for write in _registration_writes(tree))
    return found


def unguarded_registrants(root: Path = REPO) -> list[str]:
    """Every unguarded registration in the tree, sorted.

    Sorted so a red gate prints the same list twice, and derived from the tree
    rather than from a list of files somebody remembered to update.
    """
    found: list[str] = []
    for path in sorted(root.rglob("*.py")):
        found.extend(unguarded_registrations(path))
    return found
