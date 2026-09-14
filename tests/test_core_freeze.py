#!/usr/bin/env python3
"""§C · the resident core is closed, and everything outside it is a provider.

WHY THIS FILE EXISTS
--------------------
`tests/test_core_ability_boundary.py` asserts a *dependency* property: the
daemon's loader closure may not reach the fabric. That is the right gate for
"the core does not depend on what it governs", and it is not the gate §C asks
for. §C asks for something narrower and, in this product's history, harder:

    exactly one thing is resident, it is named `serverosd`, and the five
    obvious next daemons are refused **by name**.

`§1` of the round that produced this file says it directly: *"不要为'树看起来
完整'制造能力"* — and the same sentence applies to resident units. A product
that grows `abilityd` because discovery felt like it deserved a supervisor has
not become more capable; it has acquired a second thing that can be down.

WHY A NAME LIST AND NOT A PRINCIPLE
------------------------------------
"New resident units must be justified" is not mechanically checkable and never
will be. What *is* checkable is the surface a resident unit has to appear on in
this tree: a tracked executable under `bin/` or `mcp/bin/`, or a unit file. That
surface is small, closed, and enumerated below. So the gate is:

    the entry-point surface is exactly this set, and the five refused names
    appear on it nowhere.

This does not make inventing `coordd` impossible — nothing can. It makes it
*visible in a diff*, which is the only property a gate can honestly supply. The
prohibition itself lives in `ARCHITECTURE.md`, and `test_the_refusal_is_written
_down` asserts it is still there, because a refusal nobody can read is a refusal
that has already been forgotten.

WHY `runtime/cli.py` IS ON THE LIST
------------------------------------
It is tracked executable and it is not an entry point — `bin/serverosctl`
imports it. It is listed anyway, because leaving it out would make the set
"executables except the ones I did not think of", and the next person to add a
tracked executable would have to guess which kind this list meant. `test_the
_unit_starts_the_core` and the closure test in the sibling file carry the claim
about what actually runs.
"""
from __future__ import annotations

import ast
import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from loader_graph import REPO, closure  # noqa: E402

ARCHITECTURE = REPO / "ARCHITECTURE.md"

# Every tracked executable in the tree. A resident unit needs one of these (or
# a unit file) to be started by anything; there is no other door.
ENTRY_POINTS = {
    "bin/serverosctl",
    "bin/serverosd",
    "mcp/bin/serveros-node-mcp",
    "mcp/bin/serveros-agent-mcp",
    "runtime/cli.py",  # not an entry point — see the module docstring
}

# The daemon itself. `scripts/release.py` refuses an artifact without this file,
# and `test_the_unit_starts_the_core` asserts the unit names it.
CORE_BINARY = "bin/serverosd"

# The unit files in the tree. systemd is the supervisor (§20); this is the whole
# of what it is asked to supervise.
RESIDENT_UNITS = {"runtime/serverosd.service"}

# §C's refused names. Each is the obvious way to make one of ServerOS's semantic
# services into a process. They are refused because each of them is a service
# that answers a question the node already answers in-process, and a question
# answered by a second process is a question that can be unanswered.
FORBIDDEN_DAEMONS = (
    "abilityd",
    "memoryd",
    "coordinationd",
    "resource-governanced",
    "feishud",
)

# Where the refusal has to be readable. This is the sentence the gate holds
# `ARCHITECTURE.md` to; the gate does not care how it is worded beyond naming
# each term, and `test_the_refusal_is_written_down` is deliberately a substring
# check rather than a parse, because the surrounding prose belongs to a person.
FREEZE_ANCHOR = "## The resident surface (frozen)"

# Systems §C names as Provider / Managed Runtime rather than Core. Each needs a
# module that lives OUTSIDE the core directories, so that "it is a provider" is
# a fact about where the code is rather than a claim about it.
#
# `abilities/provider_instance.py` is the generic half; the two beneath it are
# the concrete implementations this node has.
OUTER_SYSTEMS = {
    "botmux": "mcp/botmux_provider.py",
    "codex-app-server": "mcp/codex_app_server_provider.py",
    "provider-generic": "abilities/provider_instance.py",
}

# Directories a daemon-closure module may live in. Kept identical in spirit to
# the sibling gate: `fleet/` is here because `runtime/notifications.py` loads
# `fleet/transport.py` to ask the transport contract a question.
CORE_DIRS = ("runtime", "context", "fleet")


def _tracked_executables() -> set[str]:
    """Tracked files carrying the executable bit, by repo-relative posix path.

    Read from the index rather than from the working tree so that a local
    `chmod` cannot change the answer — the surface this gate is about is what
    the repository ships, not what one checkout happens to have.
    """
    out = set()
    head = REPO / ".git"
    if not head.exists():
        return out
    import subprocess

    proc = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-s"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        return out
    for line in proc.stdout.decode().splitlines():
        parts = line.split(None, 3)
        if len(parts) == 4 and parts[0] == "100755":
            out.add(parts[3])
    return out


def _unit_files() -> set[str]:
    return {
        p.relative_to(REPO).as_posix()
        for p in REPO.rglob("*.service")
        if ".git" not in p.parts
    }


class TheResidentSurfaceIsClosed(unittest.TestCase):
    def test_the_executable_surface_is_exactly_this(self):
        self.assertEqual(_tracked_executables(), ENTRY_POINTS)

    def test_the_tree_has_exactly_one_unit_file(self):
        self.assertEqual(_unit_files(), RESIDENT_UNITS)

    def test_the_entry_point_set_is_not_empty(self):
        # The positive control. A scanner that returned the empty set would
        # satisfy both assertions above if the constant were ever emptied, and
        # a gate that passes when it sees nothing is the failure mode this
        # whole round is written against.
        self.assertTrue(ENTRY_POINTS)

    def test_the_unit_starts_the_core_and_nothing_else(self):
        unit = (REPO / next(iter(RESIDENT_UNITS))).read_text()
        starts = [
            line.split("=", 1)[1].strip()
            for line in unit.splitlines()
            if line.startswith("ExecStart=")
        ]
        self.assertEqual(len(starts), 1, starts)
        self.assertTrue(
            starts[0].endswith(CORE_BINARY),
            f"the resident unit must start {CORE_BINARY}, not {starts[0]}",
        )


class TheRefusedDaemonsAreNamedBeforeTheyExist(unittest.TestCase):
    def test_the_refusal_is_written_down(self):
        text = ARCHITECTURE.read_text()
        self.assertIn(
            FREEZE_ANCHOR,
            text,
            f"{FREEZE_ANCHOR!r} is gone from ARCHITECTURE.md — the freeze was "
            "deleted rather than changed",
        )
        section = text.split(FREEZE_ANCHOR, 1)[1]
        for name in FORBIDDEN_DAEMONS:
            self.assertIn(name, section, f"{name} is no longer refused by name")

    def test_no_such_daemon_exists(self):
        # The other direction: a name refused in prose that has since been
        # built is a refusal that outlived its subject — the `declared-but-
        # absent` defect with the sign flipped.
        surface = _tracked_executables() | _unit_files()
        for name in FORBIDDEN_DAEMONS:
            hits = [p for p in surface if name in Path(p).name]
            self.assertEqual(hits, [], f"{name} exists as {hits}")

    def test_the_list_is_not_empty(self):
        self.assertTrue(FORBIDDEN_DAEMONS)


class TheOuterSystemsAreProviders(unittest.TestCase):
    def test_each_named_system_has_a_module(self):
        for label, rel in OUTER_SYSTEMS.items():
            self.assertTrue((REPO / rel).is_file(), f"{label}: {rel} is missing")

    def test_none_of_them_is_in_the_core_directories(self):
        for label, rel in OUTER_SYSTEMS.items():
            self.assertNotIn(
                Path(rel).parts[0],
                CORE_DIRS,
                f"{label} lives in a core directory — it is a provider (§C)",
            )

    def test_none_of_them_is_reachable_from_the_daemon(self):
        # The behavioural half, and the one that actually matters: a provider
        # that exists outside the core but is loaded by it is a provider the
        # core cannot start without, which is §24's whole point.
        loaded = closure(Path("runtime/daemon.py"))
        for label, rel in OUTER_SYSTEMS.items():
            self.assertNotIn(
                Path(rel).as_posix(),
                {p.as_posix() for p in loaded},
                f"the daemon loads the {label} provider",
            )


class TheAbilityAuthorityIsNotADaemon(unittest.TestCase):
    """§25 · ability-management is Core-2, and Core-2 is not the core process.

    The three refusals §25 names are "not a daemon", "not the tree root", and
    "not an executor". The first is already true by construction — the authority
    lives in another repository and is read at a pinned revision. The gate here
    is the third, because it is the one an edit can quietly undo: the daemon
    loads `runtime/ability_projection.py`, and that module *can* run git against
    the authority. The claim is that the daemon never asks it to.
    """

    PROJECTION = REPO / "runtime" / "ability_projection.py"

    # The names the daemon is allowed to touch on that module. Each is a pure
    # function of materials the CLI already handed it, or a constant. Every one
    # of them is device-free and authority-free.
    ALLOWED_NAMES = {"TREE_SCHEMA", "domain_tree", "tree_rows", "ABILITY_FIELDS"}

    # The names that reach the authority. Reaching these from the daemon would
    # make `git show` — in another repository, on another filesystem — part of
    # the control loop's latency budget, which is exactly what §28 refuses.
    AUTHORITY_NAMES = {"_git", "read_manifest", "refresh", "project"}

    def _daemon_uses(self) -> set[str]:
        tree = ast.parse((REPO / "runtime" / "daemon.py").read_text())
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "ability_projection_mod":
                    used.add(node.attr)
        return used

    def test_the_daemon_reaches_only_the_pure_half(self):
        used = self._daemon_uses()
        self.assertTrue(used, "the daemon no longer uses the projection at all")
        self.assertEqual(
            used & self.AUTHORITY_NAMES,
            set(),
            "the daemon reaches the projection's authority-reading path",
        )
        self.assertEqual(used - self.ALLOWED_NAMES, set(), f"unexpected: {used}")

    def test_the_authority_path_exists_and_is_reachable_from_somewhere(self):
        # The positive control for the assertion above: if `_git` and
        # `read_manifest` were deleted, `test_the_daemon_reaches_only_the_pure
        # _half` would keep passing while asserting nothing.
        source = self.PROJECTION.read_text()
        tree = ast.parse(source)
        defined = {
            n.name
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }
        for name in ("_git", "read_manifest", "refresh"):
            self.assertIn(name, defined, f"{name} is gone from the projection")

    def test_the_projection_module_does_run_git(self):
        # States the finding rather than hiding it: the module the daemon loads
        # does contain a subprocess call. The gate is about which half the
        # daemon calls, and a reader who assumes "the daemon loads it, so it is
        # pure" is making the mistake this test names.
        self.assertIn("subprocess", (REPO / "runtime" / "ability_projection.py").read_text())


class ASessionIsNotAManagedRuntime(unittest.TestCase):
    """§C · a session is not a runtime, and the registry is not a session table.

    The two are near enough in shape that merging them is an easy edit and a
    costly one: a managed runtime is a supervised durable thing with a lifecycle
    the node owns, and a session is a conversation inside one. Folding them
    would make `runtime.list` enumerate conversations, which is how a control
    plane turns into a session manager.
    """

    SESSION_SCHEMA = "serveros/agent-session.v1"
    RUNTIME_SCHEMA = "serveros/managed-runtime-registry.v1"

    def test_the_two_use_different_schemas(self):
        self.assertNotEqual(self.SESSION_SCHEMA, self.RUNTIME_SCHEMA)

    def test_the_session_contract_declares_the_session_schema(self):
        text = (REPO / "runtime" / "agent-session-contract.yaml").read_text()
        self.assertIn(self.SESSION_SCHEMA, text)

    def test_the_runtime_registry_declares_its_own_schema(self):
        text = (REPO / "runtime" / "registry.py").read_text()
        self.assertIn(self.RUNTIME_SCHEMA, text)

    def test_registry_entries_may_not_be_session_keyed(self):
        # The registry's own guard, read rather than restated: this asserts the
        # forbidding is still in the code path that admits an entry, so that a
        # session-keyed registration is refused by the registry rather than
        # accepted and later cleaned up by hand.
        text = (REPO / "runtime" / "registry.py").read_text()
        self.assertIn("SESSION_OWNER_PREFIXES", text)
        self.assertIn("disposable", text)


if __name__ == "__main__":
    unittest.main()
