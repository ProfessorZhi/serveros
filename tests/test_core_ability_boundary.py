#!/usr/bin/env python3
"""§23 · the core may not grow a dependency on the fabric it governs.

WHY THIS FILE EXISTS
--------------------
§1 freezes the core as `serverosd`. §24 says providers never become core, and
that the core must start with none of them. Both are true today and nothing
asserts either, which is the same arrangement that let `declared-but-absent`
accumulate elsewhere in this tree: a property that holds by construction holds
until somebody edits the construction.

WHY THE OBVIOUS VERSION OF THIS GATE WOULD HAVE BEEN WORTHLESS
--------------------------------------------------------------
The first scanner written for this file looked for `import x` / `from x import
y` and found ZERO cross-directory imports anywhere in the tree. That reading is
an artifact. Nothing in this repo imports its siblings by package name: every
module is loaded by `importlib.util.spec_from_file_location` under a synthetic
name, so the dependency graph is carried as *string arguments to loader calls*
and is invisible to an import scan.

The second scanner handled the common form — `_load("name", "file.py")` — and
still missed two things, both of which changed the answer:

  * `runtime/daemon.py` reaches `context/actor.py` through a module-level
    `_ACTOR_PATH = _HERE.parent / "context" / "actor.py"`, a division chain, so
    the `.py` string is not an argument to anything.
  * `runtime/cli.py` carries a SECOND loader, `_load_module_from(path, name)`,
    which takes a `Path` rather than a filename. Through it the CLI reaches
    `abilities/runtime_parity.py`, `abilities/resolver.py`, `memory/record.py`
    and `memory/projection.py`.

So the claim "no module under `runtime/` loads anything from `abilities/`" was
false, and the scanner said it was true. That is the failure mode this file
has to be built against, and `test_the_scanner_sees_a_division_chain` is the
positive control for it: it asserts the scanner finds the specific edge that
the naive form of this scan misses.

WHAT IS ACTUALLY ASSERTED
-------------------------
Two different claims, because the two entry points have different jobs.

  * `serverosd` (`runtime/daemon.py`) is the core process. Its loader closure
    must not reach the ability catalogue, a provider, or a transport
    implementation. Measured today: the closure is 24 modules and reaches
    `runtime/`, `context/`, `fleet/` and one recorded entry — see
    `KNOWN_CORE_CROSSINGS`, which is a widening of this gate and is argued for
    where it is declared. Nothing in it is a provider or a transport.

    The recorded entry is why this file has a second, dynamic assertion:
    `AStartedCoreHasLoadedNoFabric` imports the daemon in a fresh interpreter
    with every fabric load made to raise, and requires it to come up. §24's
    sentence is about what the core STARTS with, a static scan cannot see the
    difference between a load at module level and one inside a function, and
    that difference is the whole of the exception.

  * `serverosctl` (`runtime/cli.py`) is the operator surface, and answering
    `ability resolve` or `context startup` is its job. Its crossings into
    `abilities/`, `memory/` and `context/` are legitimate — and are enumerated
    below, so that the next one is a deliberate edit to a named constant
    rather than a line nobody reviews. Each entry must still describe a real
    edge, so a crossing that is removed cannot leave its record behind.

A third assertion is behavioural rather than structural: the daemon actually
starts with no registry entries, no providers and no ability projection, and
answers `device.show` and `ability.list` without refusing. §24 asks for that
sentence; this is the first thing in the tree to check it.
"""
from __future__ import annotations

import contextlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

# The scanner lives in `tests/loader_graph.py` because a second gate needs the
# same one. Two scanners that disagree about the same file is the failure mode
# this whole file is written against; there is one implementation and both
# gates read it.
sys.path.insert(0, str(Path(__file__).resolve().parent))
from loader_graph import REPO, closure, module_refs  # noqa: E402

RUNTIME = REPO / "runtime"

# The directories a daemon-closure module may live in. `fleet/` is here because
# `runtime/notifications.py` loads `fleet/transport.py` to ask the transport
# contract a question — a control-plane read, not a provider.
CORE_DIRS = ("runtime", "context", "fleet")

# A daemon that can load one of these is a daemon that cannot start without it.
FABRIC_DIRS = ("abilities", "mcp", "memory", "space", "relations", "scripts")


# Both tuples above name TOP-LEVEL directories, and the whitelist test below
# states its own question that way: whether "a NEW top-level directory" appeared.
# The checks nevertheless compared `path.parent.name`, which answers a different
# question as soon as a file sits one level down — and in both directions the
# answer was wrong:
#
#   `runtime/reports/business_calendar.py`  parent.name is `reports`, a name in
#       no table, so the whitelist reported a file OF the core as a stranger.
#       That is the shape every staged migration has — a product module moved
#       one level down under the directory that ships it — and what `runtime/`
#       contains is `docs/architecture/layers.yaml`'s decision, not this table's.
#
#   `abilities/sub/resolver.py`             parent.name is `sub`, so the
#       BLACKLIST never scanned it. A module one directory deeper than the
#       fabric's top level was outside §24's gate entirely. Nothing noticed
#       because no fabric directory is nested today; the hole was waiting for
#       the first one.
#
# So the rule is stated once, as the question the tables are about: which
# top-level directory is this file in. Stating it that way closes the blacklist
# hole and stops a staging step being read as a new layer. Both halves are
# measured by `TheDirectoryQuestionIsTopLevel` rather than left to this comment.
def _tree(path: Path) -> str:
    """The repository's top-level directory, for a repo-relative module path."""
    parts = path.parts
    return parts[0] if len(parts) > 1 else ""


def _key(path) -> str:
    """A module path spelled the way the tables spell it, on any host.

    `str(Path)` uses the host separator, so on Windows every recorded crossing
    compared unequal to its measured counterpart and the subtractions below
    matched nothing: the one argued-for edge was re-reported as an offender
    instead of being removed. The tables hold repository paths, which are `/`
    on every host, so the comparison is made in that spelling.
    """
    return Path(path).as_posix()

# The daemon's own crossings, and this table is a WIDENING of the gate that
# has to be argued for rather than a list that was always here.
#
# The claim above is a proxy. What §24 writes is that the core "must start with
# none of them" — a statement about STARTUP — and the sentence this file puts
# in `FABRIC_DIRS` ("a daemon that can load one of these is a daemon that
# cannot start without it") was true only while every load in `runtime/daemon.py`
# ran at module level. `abilities/resolver.py` was loaded that way, so the
# proxy and the property agreed by accident, and the first lazy load in this
# file would have made them disagree in the direction that matters: the gate
# would report a violation of a property that had just started holding.
#
# So the table exists, and it is bounded three ways rather than trusted:
#
#   * `test_no_unrecorded_core_edge_exists` — the measured set and this table
#     must be EQUAL, so an edge cannot be added without an entry and an entry
#     cannot outlive its edge.
#   * `AStartedCoreHasLoadedNoFabric` — a subprocess imports the daemon with
#     `importlib.util.spec_from_file_location` wrapped, and fails if any file
#     under a `FABRIC_DIRS` directory is loaded while the module body runs.
#     That is the property §24 actually states, checked dynamically, over the
#     whole fabric rather than over this one entry. An entry can therefore only
#     ever name something the core does NOT need in order to start.
#   * `test_the_table_is_deliberately_short` — a cap on the count, so that
#     widening this gate again is an edit a reader sees rather than a line
#     added to a list that has become a habit.
KNOWN_CORE_CROSSINGS = {
    "abilities/resolver.py": {
        "because": "`ability.resolve` is answered by THIS process. `mcp/node_mcp.py` "
                   "is an adapter and forwards the call over the Unix socket "
                   "(`DaemonClient`), so there is no other process that could run "
                   "the kernel; `fleet/mcp-contract.yaml` names the daemon as the "
                   "one that does. What makes it legitimate for the core to hold "
                   "is that the module is PURE — its own docstring: \"no "
                   "filesystem, no network, no clock\" — it is named in "
                   "`scripts/build.py`'s `CORE_MODULES`, so a built artifact "
                   "always carries it, and "
                   "it consults no authority; the daemon assembles the three "
                   "scalars a request requires — none of which is one of the "
                   "resolver's eight inputs, which are documents — and passes "
                   "all eight through as missing rather than guessing them. "
                   "What changed is WHEN: the "
                   "load is inside `_ability_resolver()` and runs at the first "
                   "`ability.resolve`, so a node without `abilities/` still comes "
                   "up and still answers every other method. The alternative — "
                   "routing the resolve through the CLI — would make the answer "
                   "depend on a process the caller did not ask for, and a second "
                   "copy of the kernel in `mcp/` is the second authority this "
                   "round keeps removing.",
        "disposition": "REDECLARED",
    },
}

# `runtime/cli.py`'s crossings, measured, each with why it is there. This is a
# record, not a failure: the CLI exists to answer questions the daemon cannot.
KNOWN_CLI_CROSSINGS = {
    "abilities/runtime_parity.py": {
        "because": "`serverosctl runtime parity` renders the registry-vs-contract "
                   "comparison, and the comparison is the kernel's, not a second "
                   "one written for the CLI.",
        "disposition": "REDECLARED",
    },
    "abilities/runtime_binding.py": {
        "because": "`serverosctl device agents` is the surface that answers "
                   "which runtime bindings a device has, what surface and "
                   "version were observed, and which runtime features are "
                   "available — §16 asks for it there rather than in a new "
                   "command. The bindings it renders are the kernel's "
                   "(`abilities/runtime_binding.py`); the CLI supplies the "
                   "observation it collected and renders the answer, and holds "
                   "no binding of its own.",
        "disposition": "REDECLARED",
    },
    "abilities/packs.py": {
        "because": "`serverosctl pack inspect` is §11's pack verdict — §F names "
                   "`pack.inspect` as one of the four discovery surfaces, and "
                   "until this command nothing in the product called "
                   "`packs.resolve()` at all. The kernel keeps its place: the "
                   "CLI supplies the declarations file, the catalogue revision "
                   "and the repo, and renders the verdict; it computes no "
                   "membership of its own, and the pack verdict it prints is "
                   "the one `scripts/build.py` settles against.",
        "disposition": "REDECLARED",
    },
    "abilities/resolver.py": {
        "because": "`serverosctl ability resolve` IS the resolver's operator "
                   "surface. §24 keeps the daemon free of it; the CLI is where "
                   "it is reachable.",
        "disposition": "REDECLARED",
    },
    "abilities/taxonomy.py": {
        "because": "`serverosctl taxonomy` is §G's projection surface — the DAG "
                   "stays the DAG internally and the CLI renders it as a tree, "
                   "which §G permits for CLI/UI and forbids for the storage. The "
                   "kernel keeps its place the same way `packs.py` does: the "
                   "topology, the taxon table and the verdict `tree` prints are "
                   "`abilities/taxonomy.py`'s, and the CLI supplies a repository "
                   "and renders the answer.",
        "disposition": "REDECLARED",
    },
    "mcp/human_control.py": {
        "because": "`serverosctl alert sync` is the ONE surface allowed to hold "
                   "a transport, and the transport is the HumanControlProvider. "
                   "The module is constructed inside `_alert_provider()` and "
                   "nowhere else, on the command's own path rather than at "
                   "import — so a `serverosctl` with no provider configured "
                   "still answers every other command, which is §24's property "
                   "kept by construction rather than by this record. The "
                   "alternative was routing the send through the daemon's "
                   "socket, and that is the one thing §2 forbids: a daemon that "
                   "can send is a daemon that cannot tell you the transport is "
                   "broken, which is when its answer is wanted.",
        "disposition": "REDECLARED",
    },
    "memory/record.py": {
        "because": "`serverosctl memory ...` reads and validates the ledger.",
        "disposition": "REDECLARED",
    },
    "memory/projection.py": {
        "because": "`serverosctl memory projection` builds the derived view.",
        "disposition": "REDECLARED",
    },
    "memory/scope.py": {
        "because": "`serverosctl memory subject` must state who is reading — "
                   "§18's firewall is a decision about a READER, and the "
                   "readers that exist are a person at a terminal and a "
                   "thread. Refusing at the CLI's own door would mean the CLI "
                   "deciding admissions the kernel owns, which is the second "
                   "authority this round keeps removing.",
        "disposition": "REDECLARED",
    },
    "space/resolver.py": {
        "because": "`serverosctl space resolve` IS the Space Resolver's operator "
                   "surface, and it is what makes the kernel nameable at all. "
                   "`space/` ships in the artifact and nothing loaded it: the "
                   "only way to run it was `python3 space/resolver.py --request "
                   "…` from a source checkout, which on an installed node is "
                   "indistinguishable from the verb not existing. §B9 asks "
                   "where a logical object canonically belongs; the daemon "
                   "cannot answer it, because the question is about one "
                   "caller's request document and a daemon has no request. The "
                   "kernel stays the authority — the CLI reads a document, hands "
                   "it over, and renders the record it gets back, and it holds "
                   "no placement table of its own. The kernel opens no socket "
                   "and mutates nothing, so this crossing carries none of the "
                   "risk the `mcp/human_control.py` one does.",
        "disposition": "REDECLARED",
    },
    "space/meeting_relevance.py": {
        "because": "`serverosctl space meeting` is the same seam for the other "
                   "half of the Space question: what a MeetingBundle "
                   "contributes to work, and what may be done to it afterwards. "
                   "It was unreachable from an installed node for the same "
                   "reason and by the same measurement — the module ships, "
                   "nothing named it. Routing it through the daemon would be "
                   "worse than for the resolver: this kernel evaluates expiry "
                   "against an explicit `as_of` and fails closed when it cannot, "
                   "and a daemon answering from its own clock would be the one "
                   "reading §7's rule against reading a clock. The CLI passes "
                   "the caller's document through and offers no `--as-of` of its "
                   "own, so there is no clock for a default to come from.",
        "disposition": "REDECLARED",
    },
    "space/meeting_bundle.py": {
        "because": "`serverosctl space meeting --sidecars` is the producer half "
                   "of the line above, and it is a crossing for the same "
                   "reason: the kernel could only be asked about a bundle "
                   "somebody hand-wrote, and a meeting's own material had no "
                   "path into it. The producer builds the bundle and puts NO "
                   "question of its own — the decision, the axes and the record "
                   "are `meeting_relevance.py`'s — and the reading it does "
                   "produce is a reading of the CALLER's declaration, which is "
                   "why it belongs on this side and not in the daemon: a daemon "
                   "has no declaration, and `--observed-at` is required "
                   "precisely so no clock in any process can supply one.",
        "disposition": "REDECLARED",
    },
}

# `runtime/daemon.py` is the core process, and this is the only place its name
# is spelled for the scanner.
CORE_ENTRY = Path("runtime/daemon.py")


class CoreDoesNotDependOnTheFabric(unittest.TestCase):
    def test_the_core_process_loads_nothing_it_governs(self):
        """§1 and §24 as one reachability claim.

        Asserted as a closure rather than as a list of forbidden names on
        purpose: a name list catches the providers somebody thought of, and a
        closure catches the one added next month under a name nobody listed.

        A recorded crossing is subtracted and re-asserted in
        `TheCoresCrossingsAreRecorded`, which is the only place an exception is
        available and the place that makes it loud. Everything else under
        `abilities/`, `mcp/`, `memory/`, `space/`, `relations/` and `scripts/`
        must stay out of the closure, and the subtraction is keyed on the exact
        path, so a second module from the same directory is not covered by the
        first one's record.
        """
        reached = closure(CORE_ENTRY)
        offenders = sorted(
            f"{module} (via {_tree(module)}/)"
            for module in reached if _tree(module) in FABRIC_DIRS
            and _key(module) not in KNOWN_CORE_CROSSINGS)
        self.assertEqual(offenders, [], "\n".join(
            ["the core process can load something from the fabric:",
             *offenders,
             "",
             "serverosd is frozen as the core (§1) and providers never become",
             "core (§24). Move the call behind a surface, or load it lazily in",
             "`serverosctl` where an operator asked for it.",
             "",
             "The one thing that is NOT an answer here: adding an entry to",
             "KNOWN_CORE_CROSSINGS so this goes green. That table is for an",
             "edge the core reaches only after a caller asks, it is capped on",
             "purpose, and each entry has to survive",
             "`AStartedCoreHasLoadedNoFabric` — which asks the daemon to start",
             "with `abilities/`, `mcp/`, `memory/`, `space/`, `relations/` and",
             "`scripts/` made to raise on load."]))

    def test_the_core_process_reaches_only_core_directories(self):
        """The other half of the same claim, stated as whitelist rather than
        blacklist so that a NEW top-level directory is a failure by default
        instead of being silently outside the scan.

        Narrowed by the same table as the test above, and by the same exact
        paths, so a new directory is still a failure unless somebody names the
        one file in it and says why."""
        unexpected = sorted(
            str(module) for module in closure(CORE_ENTRY)
            if _tree(module) not in CORE_DIRS
            and _key(module) not in KNOWN_CORE_CROSSINGS)
        self.assertEqual(unexpected, [], "\n".join([
            "the core process reaches a directory that is not part of it:",
            *unexpected,
            "",
            f"core directories are {CORE_DIRS}; add one deliberately, or do",
            "not load it from the daemon. A single file in another directory",
            "is not covered by this — it goes in KNOWN_CORE_CROSSINGS, with a",
            "reason and a `disposition`, and only if the core reaches it after",
            "that core has already started."]))

    def test_the_core_does_not_load_the_effect_reconciliation_integration(self):
        """#245 / S2: the provider observation adapter is INJECTED, not loaded.

        #312 wired `agent.dispatch.reconcile` by path-loading
        `integrations/v1-effect-reconciliation/effect_reconciliation.py` from
        `runtime/daemon.py`. That made the runtime Core depend on an outer
        integration directory, which is the edge the whitelist test above
        reports but does not name. This assertion names it, so the regression
        cannot be cleared by widening `CORE_DIRS` or by adding the file to
        `KNOWN_CORE_CROSSINGS` without a reader seeing the exact path.

        The legitimate seam is the daemon-held observation source
        (`dispatch_observation_source`), which is an object injected after the
        trusted context, principal, phase and grant checks — not a module path
        the Core resolves at runtime.
        """
        refs = sorted(str(path) for path in module_refs(CORE_ENTRY)
                      if path.parts and path.parts[0] == "integrations")
        self.assertEqual(refs, [],
                         "the daemon resolves a path into integrations/: "
                         f"{refs}")
        reached = sorted(str(module) for module in closure(CORE_ENTRY)
                         if module.parts and module.parts[0] == "integrations")
        self.assertEqual(reached, [],
                         "the daemon's loader closure reaches integrations/: "
                         f"{reached}")

    def test_the_scanner_sees_a_division_chain(self):
        """The positive control, and the reason this file is worth having.

        `runtime/cli.py` reaches `abilities/resolver.py` through
        `_load_module_from(REPO_ROOT / "abilities" / "resolver.py", ...)`.
        A scanner that looks only at `import` statements, or only at
        `_load(name, "file.py")` arguments, finds this file clean — and it is
        not clean, it is simply not read.

        Asserted on a real edge that exists right now, so this control cannot
        go stale without somebody noticing what it was protecting.
        """
        refs = module_refs(RUNTIME / "cli.py")
        self.assertIn(Path("abilities/resolver.py"), refs,
                      "the scanner has gone blind to the loader form the CLI "
                      "actually uses; every other assertion in this file is "
                      "now unfalsifiable")
        self.assertIn(Path("context/resolver.py"), refs)
        self.assertIn(Path("memory/record.py"), refs)

    def test_the_scanner_sees_a_loader_call_argument(self):
        """The other form: `_load("serveros_runtime_registry", "registry.py")`,
        resolved beside the calling module because that is what `_load` does."""
        refs = module_refs(Path("runtime/daemon.py"))
        self.assertIn(Path("runtime/registry.py"), refs)
        self.assertIn(Path("context/actor.py"), refs,
                      "the daemon's trust kernel is a module-level path "
                      "constant, not a loader argument")

    def test_the_scanner_sees_a_slash_bearing_loader_argument(self):
        """The third form, and the one this file was blind to until §C.

        `_load(name, "file.py")` was read, and `_load(name, "mcp/x.py")` was
        not: the loader branch skipped any argument containing a `/`. That
        guard was written to avoid reading a version string as a path, and it
        bought nothing — `resolve()` already refuses anything that is not an
        existing `.py` file — while costing the repo-root-relative form
        outright.

        The cost was measured rather than argued. Injecting
        `_load("serveros_botmux_provider", "mcp/botmux_provider.py")` into
        `runtime/daemon.py` left `closure(daemon.py)` unchanged, so §C's
        "none of the outer systems is reachable from the daemon" passed while
        the daemon loaded a provider. Both readings — the one that misses an
        edge and the one that misses a *guard* — produce the same green.

        Asserted by construction rather than on a live edge: no module in the
        tree currently uses this form, which is exactly why the control has to
        synthesize it. A control that needs the defect to exist in order to
        notice it would disappear the moment the defect was fixed.
        """
        import tempfile

        source = (
            "import importlib.util\n"
            "def _load(name, rel):\n"
            "    spec = importlib.util.spec_from_file_location(name, rel)\n"
            "    return spec\n"
            "_X = _load('srv_botmux_provider', 'mcp/botmux_provider.py')\n"
        )
        with tempfile.TemporaryDirectory() as tmp:
            probe = Path(tmp) / "probe.py"
            probe.write_text(source)
            refs = module_refs(probe)
        self.assertIn(
            Path("mcp/botmux_provider.py"), refs,
            "the scanner is blind to a repo-root-relative loader argument; the "
            "§C provider assertion is unfalsifiable without this",
        )

    def test_the_closure_is_not_empty(self):
        """A guard against the closure collapsing to nothing, which would make
        the two assertions above pass by having nothing to look at."""
        reached = closure(CORE_ENTRY)
        self.assertGreater(len(reached), 15, sorted(str(m) for m in reached))
        self.assertIn(Path("runtime/daemon.py"), reached)
        self.assertIn(Path("runtime/probes.py"), reached)


class TheClisCrossingsAreRecorded(unittest.TestCase):
    def test_every_recorded_crossing_is_still_real(self):
        """A record cannot outlive its edge. Delete the `_load_module_from`
        call and this fails, which is what stops the list becoming a place
        where crossings go to be forgotten."""
        refs = module_refs(RUNTIME / "cli.py")
        for target in KNOWN_CLI_CROSSINGS:
            self.assertIn(Path(target), refs,
                          f"{target} is recorded as a CLI crossing but the CLI "
                          "no longer loads it; remove the entry")

    def test_no_unrecorded_crossing_exists(self):
        """The live direction. The CLI is allowed to reach the fabric; it is
        not allowed to do it silently, because an unrecorded crossing is how
        "the daemon has no provider dependency" stops being a property and
        becomes a claim about a snapshot."""
        reached = sorted(
            _key(ref) for ref in module_refs(RUNTIME / "cli.py")
            if _tree(ref) in FABRIC_DIRS)
        self.assertEqual(reached, sorted(KNOWN_CLI_CROSSINGS), "\n".join(
            ["the CLI's crossings into the fabric changed:",
             f"  measured: {reached}",
             f"  recorded: {sorted(KNOWN_CLI_CROSSINGS)}",
             "",
             "A new crossing is a decision about §24, not a detail. Add it to",
             "KNOWN_CLI_CROSSINGS with a `because`, or route it behind the",
             "daemon's socket the way the other reads are."]))

    def test_each_recorded_crossing_says_why(self):
        for target, entry in KNOWN_CLI_CROSSINGS.items():
            self.assertTrue(entry.get("because"), f"{target} has no because")
            self.assertIn(entry.get("disposition"),
                          {"NEEDS_HUMAN", "REDECLARED", "DEFER"},
                          f"{target} has no usable disposition")


class TheCoresCrossingsAreRecorded(unittest.TestCase):
    """The daemon's own table, and the three things that keep it from becoming
    a place where the boundary is widened quietly.

    Modelled on `TheClisCrossingsAreRecorded` deliberately, including the rule
    that a record cannot outlive its edge. The difference is the last test: the
    CLI may reach the fabric while it answers and the CORE may reach it only
    after it has started, which is the whole of §24's sentence — and the table,
    which is a static reading, cannot state it. So it is asserted dynamically,
    in a fresh interpreter.
    """

    @staticmethod
    def _measured() -> list[str]:
        """Every module under a `FABRIC_DIRS` directory the daemon's loader
        closure reaches. The raw measurement, before any record is subtracted,
        so that the two directions below are checked against the same thing."""
        return sorted(
            _key(module) for module in closure(CORE_ENTRY)
            if _tree(module) in FABRIC_DIRS)

    def test_every_recorded_core_edge_is_still_real(self):
        measured = self._measured()
        for target in KNOWN_CORE_CROSSINGS:
            self.assertIn(target, measured,
                          f"{target} is recorded as a core crossing but the "
                          "core no longer reaches it; remove the entry")

    def test_no_unrecorded_core_edge_exists(self):
        self.assertEqual(self._measured(), sorted(KNOWN_CORE_CROSSINGS),
                         "\n".join(
            ["the core's crossings into the fabric changed:",
             f"  measured: {self._measured()}",
             f"  recorded: {sorted(KNOWN_CORE_CROSSINGS)}",
             "",
             "A new edge is a decision about §24, not a detail. A recorded one",
             "must be reachable only after a caller asks —",
             "`AStartedCoreHasLoadedNoFabric` will say whether it is."]))

    def test_each_recorded_core_edge_says_why(self):
        for target, entry in KNOWN_CORE_CROSSINGS.items():
            self.assertTrue(entry.get("because"), f"{target} has no because")
            self.assertIn(entry.get("disposition"),
                          {"NEEDS_HUMAN", "REDECLARED", "DEFER"},
                          f"{target} has no usable disposition")

    def test_the_table_is_deliberately_short(self):
        """The cap, and the reason it is a number here rather than a sentence
        in the table's comment.

        `test_no_unrecorded_core_edge_exists` is satisfied by adding an entry
        as readily as by removing an edge, so the table on its own is a gate
        that opens when it is leaned on. This makes leaning on it a visible
        edit: the count is the thing a reviewer sees change, and the message
        says what a new number is claiming.

        The number is not a budget. It is 1 because exactly one edge has been
        argued for, and a second argument is a person's to make.
        """
        self.assertEqual(
            len(KNOWN_CORE_CROSSINGS), 1,
            f"KNOWN_CORE_CROSSINGS now holds "
            f"{sorted(KNOWN_CORE_CROSSINGS)}. Each entry widens §24 from 'the "
            "core may not load the fabric' to 'the core may load the fabric "
            "after it has started', which is a weaker claim than the one this "
            "file's header makes. Raise this number only with a `because` for "
            "the new entry that names why no other process can answer it.")


class TheDirectoryQuestionIsTopLevel(unittest.TestCase):
    """The two directions the `parent.name` spelling got wrong, measured.

    This class exists because the change it guards is invisible in the tests it
    fixes: both gates still report an empty set on this tree either way, so a
    green suite says nothing about which question was asked. Each assertion
    below pairs the answer that is wanted with the answer the old expression
    gave, on the same input — the old one is asserted to be WRONG, so the
    difference cannot quietly disappear.
    """

    def test_a_file_nested_under_a_fabric_directory_is_now_scanned(self):
        """The direction that matters for §24: a hole one directory deep.

        `abilities/sub/resolver.py` is a fabric module by any reading, and the
        blacklist answered `sub` and moved on. Nothing in the tree nests there
        today, which is why the hole was never stepped in — and why closing it
        costs nothing but this line.
        """
        nested = Path("abilities/sub/resolver.py")
        self.assertIn(_tree(nested), FABRIC_DIRS)
        self.assertNotIn(nested.parent.name, FABRIC_DIRS)

    def test_a_file_nested_under_a_core_directory_is_part_of_it(self):
        """The direction that matters for a staged migration.

        A product module moved one level down under `runtime/` is still inside
        `runtime/`. Whether it BELONGS there is `layers.yaml`'s question; it is
        not a question about which directory name appears immediately above the
        file, and answering it here would have made every staged move look like
        a new layer.
        """
        nested = Path("runtime/reports/business_calendar.py")
        self.assertIn(_tree(nested), CORE_DIRS)
        self.assertNotIn(nested.parent.name, CORE_DIRS)

    def test_a_new_top_level_directory_is_still_a_failure(self):
        """The property the whitelist is for, re-asserted after the change.

        This is the assertion that stops the two above from being a general
        loosening: a directory that is new AT THE TOP is still outside the
        core, whether it holds one file or a hundred.
        """
        for stranger in ("reports/business_calendar.py",
                         "integrations/x/y.py",
                         "workengine/store.py",
                         "cards.py"):
            self.assertNotIn(_tree(Path(stranger)), CORE_DIRS, stranger)

    def test_a_recorded_crossing_is_subtracted_on_every_host(self):
        """`str(Path)` is not a repository path outside POSIX.

        The tables hold repository paths, spelled `/`. Measuring the same
        edge with `str(Path)` gave `abilities\\resolver.py` on Windows, so
        the subtraction matched nothing and the table's own entry came back
        as an offender. `_key` is that comparison, and the assertions below
        are ones both hosts make: the recorded spelling survives it.

        The control on the OLD expression is guarded by `os.name`, and the
        guard is the point of this docstring. An earlier version asserted,
        without the guard, that `as_posix()` turns a backslash-spelled path
        into the `/` spelling — false everywhere except Windows, because POSIX
        gives a backslash no special meaning and `as_posix` returns it as it
        found it.

        The Linux runners found it; a green local run could not. The guard
        is honest rather than a convenience: on POSIX `_key` and `str(Path)`
        agree on these inputs, so there is no bug there to demonstrate, and
        inventing one would be the same mistake again.
        """
        recorded = "abilities/resolver.py"          # the tables' spelling
        self.assertEqual(_key(recorded), recorded)
        self.assertEqual(_key(Path("runtime") / "daemon.py"),
                         "runtime/daemon.py")
        if os.name == "nt":
            # The one host where the old expression disagreed with the table.
            self.assertNotEqual(str(Path(recorded)), recorded)

    def test_the_tables_are_named_for_top_level_directories(self):
        """The premise: every entry is a directory that exists at the root."""
        for name in CORE_DIRS + FABRIC_DIRS:
            self.assertNotIn("/", name, name)
            self.assertTrue((RUNTIME.parent / name).is_dir(), name)


class AStartedCoreHasLoadedNoFabric(unittest.TestCase):
    """§24's startup sentence, checked by running it.

    The static tests above read the source. This one starts the core: it
    imports `runtime/daemon.py` in a fresh interpreter with
    `importlib.util.spec_from_file_location` wrapped so that loading any file
    under `abilities/`, `mcp/`, `memory/`, `space/`, `relations/` or `scripts/`
    raises, and asserts the import completes anyway.

    Why this is not redundant with the closure scan, in one sentence: the
    scanner walks the AST, so a loader call inside a function is
    indistinguishable from one at module level, and every claim about the core
    NOT NEEDING the fabric to come up is invisible to it. Before this test,
    `runtime/daemon.py` executed `abilities/resolver.py`'s module body while
    being imported, and nothing in the tree noticed.

    It covers the whole fabric rather than the one recorded entry, so it also
    holds the line against a load the scanner cannot see at all — a path built
    by f-string or read from a config file, which `loader_graph`'s own header
    names as its limit.
    """

    def test_importing_the_daemon_loads_nothing_from_the_fabric(self):
        script = (
            "import importlib.util, sys\n"
            "from pathlib import Path\n"
            f"REPO = Path({str(REPO)!r})\n"
            f"FABRIC = {FABRIC_DIRS!r}\n"
            "real = importlib.util.spec_from_file_location\n"
            "loaded = []\n"
            "def guarded(name, location=None, *args, **kwargs):\n"
            "    if location is not None:\n"
            "        try:\n"
            "            rel = Path(str(location)).resolve().relative_to(REPO)\n"
            "        except ValueError:\n"
            "            rel = None\n"
            "        if rel is not None and rel.parts and rel.parts[0] in FABRIC:\n"
            "            loaded.append(str(rel))\n"
            "    return real(name, location, *args, **kwargs)\n"
            "importlib.util.spec_from_file_location = guarded\n"
            "spec = real('probe_daemon', REPO / 'runtime' / 'daemon.py')\n"
            "module = importlib.util.module_from_spec(spec)\n"
            "sys.modules['probe_daemon'] = module\n"
            "spec.loader.exec_module(module)\n"
            "print('PROBE ' + repr(loaded))\n"
        )
        probe = subprocess.run(
            [sys.executable, "-c", script], cwd=str(REPO),
            capture_output=True, text=True,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(probe.returncode, 0,
                         f"importing `runtime/daemon.py` failed:\n"
                         f"{probe.stdout}\n{probe.stderr}")
        marker = [line for line in probe.stdout.splitlines()
                  if line.startswith("PROBE ")]
        self.assertEqual(len(marker), 1, probe.stdout)
        loaded = eval(marker[0][len("PROBE "):])  # noqa: S307 - our own literal
        self.assertEqual(
            loaded, [],
            "\n".join([
                "the core cannot start without the fabric it governs:",
                *[f"  importing runtime/daemon.py loaded {name}" for name in loaded],
                "",
                "§1 freezes serverosd as the core and §24 says it must start",
                "with none of the systems it governs. A load at module level",
                "makes the daemon's own existence depend on that file. Move it",
                "into the function that needs it — which is what makes the",
                "edge a crossing a caller asks for rather than a dependency.",
            ]))


class TheCoreStartsWithNothing(unittest.TestCase):
    """§24's behavioural half: the core starts with no providers, no ability
    projection, and no registry entries, and still answers.

    Structural tests say the daemon cannot *load* a provider. This says it does
    not need one — which is the sentence §24 actually writes, and which no test
    in this tree performed.
    """

    @classmethod
    def setUpClass(cls):
        global ProbeUnavailable

        sys.path.insert(0, str(RUNTIME))
        sys.dont_write_bytecode = True
        cls.daemon_mod = _load_for_test("daemon.py", "serveros_daemon_boundary")
        # `_NullHost` refuses by raising the class the daemon catches, so this
        # has to be the loaded module's own exception and not a local one that
        # merely shares a name.
        ProbeUnavailable = cls.daemon_mod.probes_mod.ProbeUnavailable

    @contextlib.contextmanager
    def _daemon_reading(self, state_root: Path):
        """A daemon that believes `state_root` is the state root.

        A context manager rather than a builder, because the daemon reads the
        state root when it ANSWERS, not when it is constructed. A version of
        this that restored the patch on return left `handle()` reading the
        machine's real projection — which is how the first draft of
        `test_the_projection_is_read_when_it_is_there` came to assert against
        29 real abilities it had not put there.
        """

        class Paths:
            def root(self, name, *, unbound_ok=False):
                assert name == "state"
                return state_root

        previous = self.daemon_mod.paths_mod.Paths
        self.daemon_mod.paths_mod.Paths = Paths
        try:
            registry = _load_for_test("registry.py", "serveros_registry_boundary")
            # A registry document that names nothing. `resources` is optional
            # in the schema; passing `[]` would be a different claim.
            document = {"schema": "serveros/managed-runtime-registry.v1"}
            yield self.daemon_mod.Daemon(
                registry.Registry(document),
                state_dir=state_root / "daemon",
                socket_path=state_root / "serveros.sock",
                host=_NullHost(),
                inventory=_NullInventory(),
                log=lambda message: None,
                now=lambda: 1_000_000.0,
            )
        finally:
            self.daemon_mod.paths_mod.Paths = previous

    def test_the_projection_is_read_when_it_is_there(self):
        """The first half of the pair, and the reason the second half means
        anything.

        `NOT_MATERIALIZED` is the shape a broken read also produces. Without
        this, `test_the_daemon_answers_...` below would pass on a daemon whose
        projection read was deleted, and would keep passing forever.
        """
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            abilities = state / "abilities"
            abilities.mkdir(parents=True)
            (abilities / "effective.json").write_text(json.dumps({
                "registry_status": "FRESH",
                "generated_at": "1970-01-12T13:46:40+00:00",  # the clock below
                "source_revision": "abc123",
                "abilities": [{"name": "a-projection-was-read"}],
            }), encoding="utf-8")

            with self._daemon_reading(state) as daemon:
                listing = daemon.handle({"method": "ability.list"})
            self.assertTrue(listing["ok"], listing)
            self.assertEqual(listing["result"]["registry_status"], "FRESH")
            self.assertEqual(
                [entry["name"] for entry in listing["result"]["abilities"]],
                ["a-projection-was-read"])

    def test_the_daemon_answers_with_an_empty_registry_and_no_projection(self):
        """§24's behavioural half: no registry entries, no providers, no
        projection — and the daemon still answers, without refusing.

        The refusal is the thing being ruled out. An earlier `_capability_view`
        refused `ability.*` when the projection was absent, which reads as
        careful and makes every node surface depend on ability-management
        being reachable. §28 exists to remove that coupling.
        """
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            state.mkdir()
            with self._daemon_reading(state) as daemon:
                device = daemon.handle({"method": "device.show"})
                listing = daemon.handle({"method": "ability.list"})

            self.assertTrue(device["ok"], device)
            self.assertTrue(listing["ok"], listing)
            self.assertEqual(listing["result"]["registry_status"],
                             "NOT_MATERIALIZED")
            self.assertEqual(listing["result"]["authority"],
                             "ability-management")
            self.assertIsNone(listing["result"]["abilities"],
                              "an absent projection must not read as an empty "
                              "catalogue; 'nobody has asked' and 'nothing is "
                              "installed' are different facts")


class _NullHost:
    """A host with no probes to answer.

    Following `tests/test_daemon.py`'s `FakeProbeHost`: anything not
    explicitly answered raises, so this test cannot pass because a fake
    returned something plausible. `now()` is answered because a clock is not
    a probe — the daemon needs one to stamp what it reports, and a test that
    refused to say what time it is would be testing the wrong absence.
    """

    name = "null-probe-host"

    def now(self):
        return "2026-09-14T00:00:00+00:00"

    def docker_inspect(self, name):
        raise ProbeUnavailable(f"no such container: {name}")

    def systemd_user_unit(self, unit):
        raise ProbeUnavailable(f"no such unit: {unit}")

    def process_pids(self, pattern):
        return []


class _NullInventory:
    """An inventory of nothing, in the shapes the daemon reads."""

    def docker_list(self, all_=True):
        return {"count": 0, "containers": []}

    def docker_volumes(self):
        return {"count": 0, "volumes": []}

    def disk_status(self):
        return {"roles": [], "others": []}

    def pressure(self):
        return {"advisory_only": True, "notes": []}

    def processes_top(self, limit=15, by="cpu"):
        return {"sorted_by": by, "rows": [], "scope_note": "null"}

    def host_summary(self):
        return {"cpu": {"model": "null"}, "memory": {"total_gib": 0.0}}


def _load_for_test(filename: str, name: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
