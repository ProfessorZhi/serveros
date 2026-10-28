#!/usr/bin/env python3
"""What D4's MOVE may not break — the other half of #441.

D4 relocates `runtime/agent_control_store.py` (3 100 lines, L0) into
`workengine/agent_control/`. `#407` holds that file and the whole `workengine/`
directory, so the move is not this turn's work. Two read-only contracts are,
and they divide cleanly:

    #441, tests/test_work_control_fence_contract.py
        what the lease / epoch / fence BOUNDARY means — the three counters
        named `epoch`, the four-word writer axis, fail-closed expiry.

    this file
        what a MOVE changes. Nothing about the axis; everything about the fact
        that the file, its name in `sys.modules`, and its document on disk are
        three separate interfaces, and a relocation edits all three.

Behaviour is not re-tested here. `test_agent_control_store.py` (two writers,
the lock target), `test_v1_grant_epoch_authority.py` (monotonic across grants
and restarts), `test_v1_write_fence.py`, `test_owner_policy_binding.py` and
`test_continuation.py` own it. A seventh file re-asserting it would be
coverage theatre.

THE THREE THINGS A MOVE DOES CHANGE

  1. **The module's identity.** `_load(name, path)` caches in `sys.modules` by
     NAME. Move the file and every loader that was not updated either keeps
     pointing at a path that no longer exists, or loads the same bytes under a
     second name — and a second module object is a second `StoreError`, a second
     class for `isinstance`, and, for a store that reads-modifies-writes, a
     second in-memory document racing the first. That is the failure D2 and D3
     met on their first stage, in the one module where it costs the most.

  2. **The document's address on disk.** Every deployment that has ever run has
     a JSON file at that path. The schema string and the section names are the
     interface to those files; a move that changes either invalidates them
     silently — a store that cannot read its own document refuses to start, and
     a store that starts empty reports every approval it held as never granted.

  3. **Nothing else.** The lock is derived from the store path, and the reader
     fills missing sections with `setdefault`, so lock and sections travel with
     the file.

THE FINDING, AND THE ONE LIMIT

Measured 2026-10-28: **34 files load this store, and `tests/loader_graph.py`
resolves 20 of them.** The 14 it cannot see all compose the path from a
directory constant rather than the caller's own directory — `RUNTIME /
"agent_control_store.py"` in `tests/`, where `RUNTIME = REPO / "runtime"`. Its
division-chain walker keeps the literal tail and then tries `REPO /
"agent_control_store.py"` and `<caller's dir> / "agent_control_store.py"`;
neither exists, so the edge vanishes.

Today every invisible edge is out of a `tests/` file, so `production_closure()`
is not wrong yet. The same form is legal in `runtime/`, which is what the
second class below forbids: a blind edge out of production code is a boundary
gate that passes across a dependency it cannot see — and a move is exactly how
one would get there.

Fixing the resolver is #439's file, not this one's: reported to #429 rather
than patched here, because the branch this file lives on does not carry #439.
"""
from __future__ import annotations

import ast
import functools
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RUNTIME = REPO / "runtime"
STORE = RUNTIME / "agent_control_store.py"

#: The name every loader must use. It is the cache key, so it is the thing that
#: decides whether two callers share one module object.
CANONICAL = "serveros_runtime_agent_control_store"

#: The two loaders that deliberately do NOT use it, both in `tests/`.
#:
#: They want an instance no other test module has loaded, which is a legitimate
#: isolation trick: the store caches its document per module object. The cost is
#: that a `StoreError` raised through one of these is not the class in
#: `CANONICAL`, so `except` across the two does not catch. That is fine in a
#: test and would not be in `runtime/`. Listed, not filtered, so that a THIRD
#: off-name loader — which could be in `runtime/` — is visible.
NAMED_FRESH_INSTANCE_LOADERS = frozenset({
    "tests/test_fleet_v1_t10_producer_binding.py",
    "tests/test_grant_window_authority.py",
})

#: The document's collections. Nine sections the reader fills with `setdefault`,
#: plus the schema string and the durable grant epoch.
SECTIONS = frozenset({
    "contexts", "approvals", "bindings", "selections", "grants", "admissions",
    "checkpoints", "dispatch_attempts",
})
SCHEMA = "serveros/agent-control-store.v1"

LOADER_NAMES = frozenset({"_load", "_load_module_from"})


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


@functools.lru_cache(maxsize=1)
def _store_loads() -> tuple[tuple[str, str | None], ...]:
    """(caller, module name) for every loader call that names this store.

    No path resolution: the name is a literal or it is not, and the census of
    names is what decides whether the tree has one module object or two. Files
    are pre-filtered by a substring so the parse cost stays on the ~70 files
    that mention the module rather than the whole tree.
    """
    found: list[tuple[str, str | None]] = []
    for path in sorted(REPO.rglob("*.py")):
        if ".git" in path.parts or "node_modules" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        if "agent_control_store" not in text:
            continue
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        rel = path.relative_to(REPO).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            called = (fn.attr if isinstance(fn, ast.Attribute)
                      else fn.id if isinstance(fn, ast.Name) else None)
            if called not in LOADER_NAMES:
                continue
            strings = [arg.value for arg in node.args
                       if isinstance(arg, ast.Constant)
                       and isinstance(arg.value, str)]
            if not any("agent_control_store" in s for s in strings):
                continue
            # `_load(NAME, path)` and `_load_module_from(path, NAME)`: in both,
            # the name is the argument that is not a filename.
            name = next((s for s in strings if not s.endswith(".py")), None)
            found.append((rel, name))
    return tuple(found)


# ---------------------------------------------------------------------------
# 1. The module's identity
# ---------------------------------------------------------------------------


class TestTheModuleIsOneObject:
    def test_the_census_is_wide_enough_to_be_a_census(self) -> None:
        """The control: a scan that found two loaders would satisfy the rest."""
        calls = _store_loads()
        assert len(calls) >= 30, len(calls)
        families = {caller.split("/")[0] for caller, _ in calls}
        assert {"runtime", "tests"} <= families, families
        assert "runtime" in families, (
            "the census found only callers outside production, so it is not "
            "measuring the thing a move would break")

    def test_every_loader_uses_the_canonical_name_or_is_one_of_the_two(self) -> None:
        """Two names for one file is two module objects — the D2/D3 failure."""
        off_name = sorted({caller for caller, name in _store_loads()
                           if name != CANONICAL})
        assert off_name == sorted(NAMED_FRESH_INSTANCE_LOADERS), (
            "a loader uses a name other than the canonical one. If it is in "
            "`runtime/`, the CLI and the daemon no longer share a store: "
            f"{off_name}")

    def _unresolved(self) -> list[str]:
        """Callers that name the store but that the loader authority cannot resolve."""
        sys.path.insert(0, str(REPO / "tests"))
        import loader_graph

        target = Path("runtime/agent_control_store.py")
        return sorted(caller for caller in {caller for caller, _ in _store_loads()}
                      if target not in loader_graph.module_refs(REPO / caller))

    def test_the_loader_authority_still_sees_most_of_the_store_s_callers(
            self) -> None:
        """The control: a resolver that resolved nothing would pass the next test.

        Measured on 2026-10-28: 34 loaders, 14 unresolved — 41% of the store's
        inbound edges are invisible to `module_refs`. They are exactly the
        callers composing the path from a directory constant instead of the
        caller's own directory (`RUNTIME / "agent_control_store.py"`, where
        `RUNTIME = REPO / "runtime"`). The ceiling is here to notice the blind
        spot CHANGING CLASS, not to police test files: it is a floor on what the
        authority sees, not equality with today's number.
        """
        unresolved = self._unresolved()
        assert len(unresolved) <= 20, unresolved
        assert len(unresolved) < len({caller for caller, _ in _store_loads()}), (
            "the loader authority resolves none of the store's callers, which "
            "means the resolver broke rather than that the tree changed")

    def test_the_authority_blind_spot_never_reaches_production_code(self) -> None:
        """The invariant that matters, stated as an invariant.

        An unresolved edge out of a `tests/` file costs coverage. The same edge
        out of `runtime/` costs `production_closure()` its answer: the gate would
        report that the daemon does not reach a module it reaches. So the blind
        spot is allowed to exist and is not allowed to move into production
        code — which is also the thing a MOVE could silently do, by relocating
        the store and leaving a runtime loader on the unresolvable form.
        """
        production = [caller for caller in self._unresolved()
                      if not caller.startswith("tests/")]
        assert production == [], (
            "these production modules name the store through a form the loader "
            "authority cannot resolve, so `production_closure()` no longer sees "
            f"the edge and a boundary gate can pass across it: {production}")


# ---------------------------------------------------------------------------
# 2. The document's address on disk
# ---------------------------------------------------------------------------


class TestTheDocumentIsTheInterface:
    def test_the_schema_string_is_the_one_existing_stores_carry(self) -> None:
        """It is read back and compared; a new one would need a migration."""
        module_level = {node.targets[0].id: node.value.value
                        for node in _tree(STORE).body
                        if isinstance(node, ast.Assign)
                        and isinstance(node.targets[0], ast.Name)
                        and isinstance(node.value, ast.Constant)}
        assert module_level["SCHEMA"] == SCHEMA

    def test_the_reader_still_defaults_every_section_it_documents(self) -> None:
        """The sections are the file's interface to stores written before them.

        Read from the source rather than from an empty store, so the check also
        runs on the host where `fcntl` makes the module unimportable. A section
        that stops being defaulted is the move dropping a collection: the old
        document still has the key, and every reader of that collection starts
        answering "nothing was ever recorded".
        """
        defaulted = {
            node.args[0].value
            for node in ast.walk(_tree(STORE))
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "setdefault"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        }
        assert SECTIONS <= defaulted, sorted(SECTIONS - defaulted)
        assert "grant_epoch" in defaulted, (
            "the durable counter is read with setdefault too: an older document "
            "reads as 0, and the first new grant after it gets 1")
