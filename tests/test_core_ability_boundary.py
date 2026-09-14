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
    implementation. Measured today: the closure is 23 modules and reaches
    `runtime/` and `context/` only. Nothing in it is a provider.

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
        """
        reached = closure(CORE_ENTRY)
        offenders = sorted(
            f"{module} (via {module.parent.name}/)"
            for module in reached if module.parent.name in FABRIC_DIRS)
        self.assertEqual(offenders, [], "\n".join(
            ["the core process can load something from the fabric:",
             *offenders,
             "",
             "serverosd is frozen as the core (§1) and providers never become",
             "core (§24). Move the call behind a surface, or load it lazily in",
             "`serverosctl` where an operator asked for it."]))

    def test_the_core_process_reaches_only_core_directories(self):
        """The other half of the same claim, stated as whitelist rather than
        blacklist so that a NEW top-level directory is a failure by default
        instead of being silently outside the scan."""
        unexpected = sorted(
            str(module) for module in closure(CORE_ENTRY)
            if module.parent.name not in CORE_DIRS)
        self.assertEqual(unexpected, [], "\n".join([
            "the core process reaches a directory that is not part of it:",
            *unexpected,
            "",
            f"core directories are {CORE_DIRS}; add one deliberately, or do",
            "not load it from the daemon."]))

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
            str(ref) for ref in module_refs(RUNTIME / "cli.py")
            if ref.parent.name in FABRIC_DIRS)
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
            def root(self, name):
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
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


if __name__ == "__main__":
    unittest.main()
