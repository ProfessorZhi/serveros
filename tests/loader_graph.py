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
      * `_load_module_from(REPO / "a" / "b.py")` — a division chain, joined from
                                                 its string parts.
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

    # Bare filenames handed to a loader, which resolves them beside itself.
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
                    and id(arg) not in prose and "/" not in arg.value):
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
