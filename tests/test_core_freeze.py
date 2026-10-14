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

WHY THERE IS NOW A SECOND UNIT IN THE TREE
------------------------------------------
§C's claim is about the CORE: one resident process, named `serverosd`, and the
five obvious next daemons refused by name. This gate has always been a name
list, and this round added a name — on purpose, in its own commit, as a diff.

`fleet_mcp/deploy/serveros-fleet-mcp.service` is the ingress for ONE host's
external MCP clients (`fleet/fleet-mcp-contract.yaml`); it is a USER unit, so it
is not even in the same supervisor scope as the node's, and `test_the_ingress_*`
below holds it to the three properties that keep it from being a second core
daemon: it starts only the ingress entry point, it lives outside the directory
the core's units live in, and `plugin/bundle.yaml` — the composition that ships
to every node — does not name its package. It IS resident, and the gate says so
rather than pretending a web surface with `Restart=` is a oneshot. What the gate
refuses is a second thing that can be *down for the node*; what this is, is a
listener that can be down for one operator's ChatGPT session while the node is
untouched, which is why it is a fifth set here and not a second entry in
`RESIDENT_UNITS`.
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
    "mcp/bin/serveros-continuation-mcp",
    "runtime/cli.py",  # not an entry point — see the module docstring
    # Not an entry point either, and for a reason worth writing down: it is the
    # Parent launcher `scripts/release.py continuation-wake` RENDERS and installs
    # to the operator's own bin directory. It ships executable because it is
    # installed executable, and it is a template rather than a program until that
    # render happens — `tests/test_wake_parent.py` is what reads it, and it fails
    # if the placeholder survived into somewhere it could be started.
    "runtime/serveros-wake-parent",
    # Not an entry point of the CORE, and listed for the reason the whole set
    # exists: it is a tracked executable, so leaving it out would make the diff
    # that added it silent. It is the launcher
    # `fleet_mcp/deploy/serveros-fleet-mcp.service` starts — one host's
    # off-host MCP ingress, shipped to no node. See the module docstring.
    "fleet_mcp/bin/serveros-fleet-mcp",
}

# The daemon itself. `scripts/release.py` refuses an artifact without this file,
# and `test_the_unit_starts_the_core` asserts the unit names it.
CORE_BINARY = "bin/serverosd"

# The unit files in the tree. systemd is the supervisor (§20); this is the whole
# of what it is asked to supervise.
RESIDENT_UNITS = {"runtime/serverosd.service"}

# §W's delivery runtime: a oneshot and the timer that fires it. It is listed
# here rather than left implicit so that adding a third unit is a diff, and it
# is kept SEPARATE from RESIDENT_UNITS because that separation is the whole
# claim — §W says the delivery worker is "Managed Runtime / HumanControl
# delivery worker，不是第二个 ServerOS daemon", and a comment is not a gate.
#
# `test_every_other_unit_is_provably_not_resident` reads `Type=` and `Restart=`
# out of each of these and requires that systemd would NOT keep them running.
# The resident unit is put through the same predicate as a positive control: if
# it did not come back resident, the predicate would be answering yes to
# everything and the assertion under it would be worth nothing.
DELIVERY_UNITS = {
    "runtime/serveros-alert-sync.service",
    "runtime/serveros-alert-sync.timer",
}

# §8's daily workbench render. A THIRD set rather than a third entry in
# DELIVERY_UNITS, because the two are different claims and folding them would
# lose one: the delivery worker talks to a provider and sends something, and
# this one sends nothing, takes no action on the world, and writes a directory a
# person opens. What they share, and what is asserted of both below, is that
# systemd would not keep either running.
#
# The unit lives in the same directory as `serverosd.service`, which is the
# accidental reason a gate about the RESIDENT surface is the gate that should
# still see it: a file next to the daemon's unit is the easiest place to add a
# second daemon by accident.
SCHEDULED_UNITS = {
    "runtime/serveros-daily.service",
    "runtime/serveros-daily.timer",
}

# §9.48's continuation wake routine: a set of its own rather than more entries
# above, because the claim is different in kind. The delivery worker sends
# something and the daily render writes a directory a person opens; this pair
# STARTS A PARENT, and of the two sets above it is the only one that spends
# authority when it fires — which is why what it must not carry is worth its own
# line here rather than a footnote on somebody else's. ("Of the two above" rather
# than a count of every set in this file: the evening pair below was added after
# this comment, and a count written here would have had to be edited to stay
# true, which is the kind of sentence that is not edited and stops being true.)
#
# It is listed rather than left implicit so that another unit file is still a
# diff, and it is asserted NOT resident for the same reason as the other two: a
# resident unit is a process with a lifetime the node did not choose, while
# these two are a oneshot a timer fires and a timer that fires it.
WAKE_UNITS = {
    "runtime/serveros-continuation-wake.service",
    "runtime/serveros-continuation-wake.timer",
}

# The evening brief: a FIFTH set, added 2026-10-08, and the reason it is a set of
# its own is that the claim it carries is unlike the other four. The delivery
# worker talks to a provider and sends an alert; the daily render writes a
# directory a person opens; the wake routine starts a Parent. This one does the
# second half of the day's work — it composes tonight's brief out of the 19:20
# preparation and sends it to one person — and until this pair was listed here it
# did all of that from a file under `~/.config/systemd/user/` that no build
# shipped, no check rendered and no test could see. Listing it is what makes the
# live producer and the declared producer the same thing.
#
# It is asserted NOT resident for the same reason as the other three: a oneshot a
# timer fires is a command somebody asked for, while the resident daemon is a
# process with a lifetime the node did not choose.
EVENING_UNITS = {
    "runtime/serveros-evening-alignment.service",
    "runtime/serveros-evening-alignment.timer",
}

# Every unit that is not the resident daemon. Named once so the sub-tests below
# cannot cover a set somebody added and forgot to wire in.
NON_RESIDENT_UNITS = (DELIVERY_UNITS | SCHEDULED_UNITS | WAKE_UNITS | EVENING_UNITS)

# A fifth set, and the only one that is RESIDENT without being the core.
#
# It is kept out of RESIDENT_UNITS rather than added to it, because the two
# claims are different: `RESIDENT_UNITS` is "this is the node's process, and it
# is the only one" — `test_the_unit_starts_the_core_and_nothing_else` reads
# `next(iter(RESIDENT_UNITS))` and would become a coin flip the moment a second
# name joined it. This set's claim is the negative one §C actually wants: a
# resident thing that is not the daemon must start the daemon nowhere, live
# outside the core's directory, and reach no node.
#
# It is IN the tree as a template rather than only rendered by the installer,
# because a unit that exists only inside a Python string is a unit no reviewer
# of this repository can read. `fleet_mcp/deploy/install.py` renders it into
# `~/.config/systemd/user/`; what is installed is that rendering, and the
# placeholder below is what keeps it from being startable in place.
INGRESS_ENTRY = "fleet_mcp/bin/serveros-fleet-mcp"  # also in ENTRY_POINTS above
INGRESS_UNITS = {"fleet_mcp/deploy/serveros-fleet-mcp.service"}

# The daemon may not open a network socket (§W: "serverosd 不做网络调用"). It
# is checked as an AST property of the daemon's own loader closure rather than
# by reading one file, because the call this refuses is the one added by a
# future edit to a module two hops away.
NETWORK_SOCKET_FAMILIES = ("AF_INET", "AF_INET6")

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


UNIT_SUFFIXES = (".service", ".timer")


def _unit_files() -> set[str]:
    return {
        p.relative_to(REPO).as_posix()
        for p in REPO.rglob("*")
        if p.suffix in UNIT_SUFFIXES and ".git" not in p.parts
    }


def directives(text: str) -> list[tuple[str, str, str]]:
    """(section, key, value) for every `Key=value` line, case-folded on the key.

    A parse rather than a substring search, because the claim below is about
    what systemd would READ — and systemd reads neither the prose block at the
    top of these files nor a key that appears in the wrong section. `[Service]`
    keys written under `[Unit]` are logged as unknown and ignored, which is a
    property `serverosd.service` already documents having been bitten by.
    """
    found, section = [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        found.append((section, key.strip().lower(), value.strip()))
    return found


def is_resident(text: str) -> bool:
    """Would systemd keep this unit running?

    The question §C actually asks, asked about a file rather than about a name.
    A unit that is resident is a process with a lifetime the node does not
    choose, and that is the surface that has to stay closed. A oneshot is a
    command someone asked for; it exits, and nothing about it can be "down".

    `Restart=` is checked as well as `Type=` because a oneshot with
    `Restart=always` is a loop wearing a oneshot's clothes — systemd would
    re-run it forever, which is the same failure the daemon's unit refuses in
    its own header.
    """
    keys = directives(text)
    if next((v for _, k, v in keys if k == "type"), "") != "oneshot":
        return True
    restart = next((v for _, k, v in keys if k == "restart"), None)
    return restart is not None and restart.lower() != "no"


class TheResidentSurfaceIsClosed(unittest.TestCase):
    def test_the_executable_surface_is_exactly_this(self):
        self.assertEqual(_tracked_executables(), ENTRY_POINTS)

    def test_the_tree_declares_exactly_these_units(self):
        # Was `== RESIDENT_UNITS` and said "exactly one unit file". That was the
        # right assertion while it was true and the wrong one the moment it was
        # not: §W adds a delivery runtime, and a gate that can only be satisfied
        # by deleting the new unit would be a gate arguing against the feature.
        # It now names every non-resident set, so another unit file is still a
        # diff. It has been that diff three times: §W's delivery pair, §9.48's
        # wake pair, and 2026-10-08's `serveros-evening-alignment.{service,timer}`
        # — each one met by declaring the pair, never by widening the assertion.
        #
        # A fourth time, and a fifth set: the one unit that is resident without
        # being the core (`INGRESS_UNITS`). It is named here for the same reason,
        # so a sixth unit is still a diff rather than a surprise.
        self.assertEqual(_unit_files(),
                         RESIDENT_UNITS | NON_RESIDENT_UNITS | INGRESS_UNITS)

    def test_every_other_unit_is_provably_not_resident(self):
        """§W: the delivery worker is not a second ServerOS daemon.

        Not "it is written as a oneshot in a comment", and not "it lives in a
        different file" — `Type=` and `Restart=` as systemd would read them.
        """
        for rel in sorted(NON_RESIDENT_UNITS):
            with self.subTest(unit=rel):
                text = (REPO / rel).read_text()
                if Path(rel).suffix == ".timer":
                    # A timer runs nothing itself; what it must not carry is an
                    # ExecStart, which would be a second thing that starts a
                    # process on its own.
                    self.assertEqual(
                        [v for _, k, v in directives(text) if k == "execstart"], [],
                        f"{rel} carries an ExecStart; a timer names a Unit=",
                    )
                    self.assertTrue(
                        any(k == "unit" for _, k, _ in directives(text)),
                        f"{rel} does not name the unit it fires",
                    )
                    continue
                self.assertFalse(
                    is_resident(text),
                    f"{rel} would be kept running by systemd — Type= is not "
                    f"oneshot, or it carries a Restart= that re-runs it. §W "
                    f"allows a delivery worker and refuses a second daemon.",
                )

    def test_that_predicate_calls_the_resident_unit_resident(self):
        # The positive control, and the reason the assertion above is worth
        # anything: a predicate that answered False to everything would pass it
        # while checking nothing. `serverosd.service` IS the resident surface,
        # so the predicate has to say so about it.
        self.assertTrue(is_resident((REPO / next(iter(RESIDENT_UNITS))).read_text()))

    def test_the_delivery_worker_holds_no_socket_directory(self):
        # `RuntimeDirectory=` is the one property a copy-paste from the daemon's
        # unit would carry, and it would be actively harmful here: systemd
        # REMOVES a RuntimeDirectory when the unit that declared it stops, so a
        # oneshot declaring `RuntimeDirectory=serveros` would delete %t/serveros
        # — and with it the daemon's socket — on every run.
        for rel in sorted(NON_RESIDENT_UNITS):
            with self.subTest(unit=rel):
                keys = {k for _, k, _ in directives((REPO / rel).read_text())}
                self.assertNotIn("runtimedirectory", keys, rel)
                self.assertNotIn("listenstream", keys, rel)

    def test_the_ingress_unit_is_resident_and_starts_only_the_ingress(self):
        """The honest half first: it IS resident, and the gate says so.

        A web surface systemd restarts is a process with a lifetime nobody
        chose, and filing it under `NON_RESIDENT_UNITS` so the existing
        `is_resident` assertions would skip it is exactly the move this file
        exists to refuse. What §C needs from it is narrower: that it cannot
        start, or replace, the node.
        """
        self.assertTrue(INGRESS_UNITS, "the set is empty; this test would check nothing")
        for rel in sorted(INGRESS_UNITS):
            with self.subTest(unit=rel):
                text = (REPO / rel).read_text(encoding="utf-8")
                self.assertTrue(is_resident(text), f"{rel} is not written as a resident unit")
                starts = [v for _, k, v in directives(text) if k == "execstart"]
                self.assertEqual(len(starts), 1, starts)
                self.assertIn(INGRESS_ENTRY, starts[0], starts[0])
                self.assertNotIn(CORE_BINARY, starts[0], starts[0])

    def test_the_ingress_lives_outside_the_core_directory(self):
        """The accidental-daemon argument, applied to this unit.

        The three non-resident sets live under `runtime/`, beside
        `serverosd.service`, and the comment on `SCHEDULED_UNITS` says why a
        gate about the resident surface should still be looking there: "a file
        next to the daemon's unit is the easiest place to add a second daemon by
        accident". This one is not in that directory, and `fleet_mcp` is not a
        core directory — the package is `fleet_mcp`, a near-neighbour of the
        `fleet` block `bundle.yaml` does ship, and the two are different things.
        """
        self.assertNotIn(Path(INGRESS_ENTRY).parts[0], CORE_DIRS)
        for rel in sorted(INGRESS_UNITS):
            with self.subTest(unit=rel):
                self.assertNotIn(Path(rel).parts[0], CORE_DIRS, rel)
                self.assertNotEqual(Path(rel).parts[0],
                                    Path(next(iter(RESIDENT_UNITS))).parts[0], rel)

    def test_the_ingress_ships_to_no_node(self):
        """The composition gate, read rather than paraphrased.

        `plugin/bundle.yaml` is the `serveros-core` composition: the members
        shipped to every node. This surface is ONE host's ingress, so its name
        appearing there would put a listener on every node in the fleet — which
        is the argument `fleet/fleet-mcp-contract.yaml` records under
        `in_the_core_composition: false`.
        """
        text = (REPO / "plugin" / "bundle.yaml").read_text(encoding="utf-8")
        self.assertNotIn("fleet_mcp", text)
        for rel in sorted(INGRESS_UNITS):
            with self.subTest(unit=rel):
                self.assertNotIn(Path(rel).name.removesuffix(".service"), text)

    def test_the_entry_point_set_is_not_empty(self):
        # The positive control. A scanner that returned the empty set would
        # satisfy both assertions above if the constant were ever emptied, and
        # a gate that passes when it sees nothing is the failure mode this
        # whole round is written against.
        self.assertTrue(ENTRY_POINTS)
        self.assertTrue(DELIVERY_UNITS)
        self.assertTrue(SCHEDULED_UNITS)
        # The ingress entry point is in the set it is held to, not merely
        # declared as a constant beside it.
        self.assertIn(INGRESS_ENTRY, ENTRY_POINTS)

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

    def test_the_delivery_worker_runs_the_cli_and_not_the_provider(self):
        """§W's split, at the one place an ExecStart can violate it.

        The worker must go through `serverosctl alert sync`, which asks the
        daemon what has to be said and records what happened. An ExecStart that
        called a provider or a module directly would be a second producer of the
        dedupe, cooldown and recovery decisions the register already owns.
        """
        text = (REPO / "runtime/serveros-alert-sync.service").read_text()
        starts = [v for _, k, v in directives(text) if k == "execstart"]
        self.assertEqual(len(starts), 1, starts)
        self.assertIn("bin/serverosctl", starts[0])
        self.assertIn("alert sync", starts[0])
        for forbidden in ("human_control", "python3 -c", "curl", "notifications.py"):
            self.assertNotIn(forbidden, starts[0])


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
    #
    # `CATALOGUE_NAME` is a filename, and it is admitted rather than hardcoded
    # on purpose: the daemon reads the catalogue artifact the producer writes,
    # so the two must agree on what it is called, and importing the one
    # constant is how that agreement is enforced instead of duplicated. Reading
    # a file the producer wrote is not reaching the authority — that is
    # `refresh`, which stays in AUTHORITY_NAMES below.
    #
    # `resolution_catalogue` was admitted 2026-10-07, when
    # `test_the_daemon_reaches_only_the_pure_half` went red on main with
    # `unexpected: {'resolution_catalogue', …}`. It arrived with 9cc4c47
    # ("connect task-oriented ability discovery and monitoring receipts") and it
    # is admitted because it meets the criterion the rest of this set meets, not
    # because something already used it: it normalizes records its caller
    # already read — the daemon passes `catalogue["entries"]` out of the
    # producer's artifact under `state/abilities/` — with dict operations and
    # one `json.dumps` for de-duplication.
    #
    # `product_tree` was admitted 2026-10-08, and it took the same route as
    # `resolution_catalogue` the day before: `test_the_daemon_reaches_only_the_pure_half`
    # went red on main with `unexpected: {'product_tree', …}`. It arrived with
    # 2a83462 ("expose the full product responsibility tree on demand"),
    # 50 commits after the round that admitted the name before it and on the
    # same day — so this is the second widening rather than the first, and the
    # reason it keeps happening is worth stating: the daemon grows views, a view
    # is a call into this module, and the set is written by hand. The gate is
    # the only thing that notices.
    #
    # It is admitted on the criterion and not on the arrival. The daemon reads
    # the producer's artifact first (`declared_catalogue()`, then
    # `source["entries"]` and `source["product_vocabulary"]`) and hands those
    # to `product_tree`, which groups them by identity, compares each record's
    # declared `product_role` against a vocabulary its caller already read, and
    # sorts the result. Pure functions of materials the caller supplied; the
    # name that forked git is `refresh`, and it stays in AUTHORITY_NAMES.
    #
    # The admission is not taken on trust. `test_every_admitted_name_is_pure`
    # requires of every admitted name that its intra-module call closure cannot
    # reach `_git`, with `_git`/`read_manifest`/`refresh` as the control that
    # the predicate does not answer "pure" to everything.
    ALLOWED_NAMES = {"TREE_SCHEMA", "domain_tree", "tree_rows", "ABILITY_FIELDS",
                     "CATALOGUE_NAME", "resolution_catalogue", "product_tree"}

    # The names that reach the authority. Reaching these from the daemon would
    # make `git show` — in another repository, on another filesystem — part of
    # the control loop's latency budget, which is exactly what §28 refuses.
    #
    # This is a POLICY set, not a mechanical one, and the difference is worth
    # knowing before reading the gate below as a proof about all four: `_git`,
    # `read_manifest` and `refresh` fork git, and `project` does not — it takes
    # a manifest its caller already read. `project` is here because building the
    # projection is Core-2's job, so a daemon that called it would be Core-2
    # running inside the control loop. Only the three that fork are usable as
    # the control for a mechanical predicate, and that is the three the control
    # test names.
    AUTHORITY_NAMES = {"_git", "read_manifest", "refresh", "project"}

    def _daemon_uses(self) -> set[str]:
        tree = ast.parse((REPO / "runtime" / "daemon.py").read_text())
        used = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                if node.value.id == "ability_projection_mod":
                    used.add(node.attr)
        return used

    def _defined_functions(self) -> dict[str, ast.AST]:
        tree = ast.parse(self.PROJECTION.read_text())
        return {
            n.name: n
            for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        }

    def _defined_constants(self) -> set[str]:
        tree = ast.parse(self.PROJECTION.read_text())
        names: set[str] = set()
        for node in tree.body:
            if isinstance(node, ast.Assign):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                names.add(node.target.id)
        return names

    def _call_closure(self, name: str) -> set[str]:
        """Module-level functions `name` can reach by calling, transitively.

        Intra-module only. An imported helper is a different question, and the
        loader-closure tests in the sibling file are the ones that ask it.
        """
        funcs = self._defined_functions()
        seen: set[str] = set()
        stack = [name]
        while stack:
            fn = funcs.get(stack.pop())
            if fn is None:  # a constant, or a name that is not defined here
                continue
            for node in ast.walk(fn):
                if isinstance(node, ast.Name) and node.id in funcs:
                    if node.id not in seen:
                        seen.add(node.id)
                        stack.append(node.id)
        return seen

    def _reaches_git(self, name: str) -> bool:
        """Whether asking the daemon to touch `name` would fork git.

        `_git` itself counts: it IS the fork, so the walk has nothing to find
        and the name comparison is what answers for it.
        """
        return name == "_git" or "_git" in self._call_closure(name)

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

    def test_every_admitted_name_is_pure(self):
        """The allow-list is checked, not trusted.

        `test_the_daemon_reaches_only_the_pure_half` asserts which NAMES the
        daemon touches, and on its own that is satisfied by adding any name to
        ALLOWED_NAMES — including `refresh`. This is the other half, and it is
        the half the docstring above claims: an admitted name must be pure,
        which here has one mechanical meaning — it cannot reach `_git`, so it
        cannot put a `git show` against another repository onto the control
        loop's latency budget (§28).

        A constant is admitted by being declared at module level. A name that is
        neither a function nor a declared constant is admitted by nothing: it
        is a typo, and a typo here is worse than an unused entry, because it
        looks like it admitted something while the daemon's use of it would
        still have to be caught by the other test.
        """
        funcs = self._defined_functions()
        constants = self._defined_constants()
        for name in sorted(self.ALLOWED_NAMES):
            if name in funcs:
                self.assertFalse(
                    self._reaches_git(name),
                    f"{name} is admitted to the daemon but reaches _git",
                )
            else:
                self.assertIn(
                    name,
                    constants,
                    f"{name} is admitted but is neither a function nor a "
                    "module-level constant of the projection",
                )

        # Both halves of the list must be doing work, or the loop above is
        # passing over an empty set.
        self.assertTrue(funcs, "no module-level functions parsed")
        self.assertTrue(
            self.ALLOWED_NAMES & set(funcs),
            "no admitted name is a function — the purity half asserts nothing",
        )
        self.assertTrue(
            self.ALLOWED_NAMES & constants,
            "no admitted name is a constant — the declaration half asserts nothing",
        )

    def test_the_purity_predicate_is_not_vacuous(self):
        """The negative control for the gate above.

        `_reaches_git` is a call-closure walk, and a walk that finds nothing
        reports "pure" for every input. Two things make the assertion above mean
        something: the module's actual git path must come back impure, and the
        predicate must return BOTH answers across the module rather than one
        answer to everything.
        """
        funcs = self._defined_functions()
        for name in ("_git", "read_manifest", "refresh"):
            self.assertIn(name, funcs, f"{name} is gone from the projection")
            self.assertTrue(
                self._reaches_git(name),
                f"the predicate reports {name} as pure; it forks git",
            )
        verdicts = {self._reaches_git(name) for name in funcs}
        self.assertEqual(
            verdicts,
            {True, False},
            f"the predicate is not discriminating: it answered {verdicts} for "
            f"all {len(funcs)} module-level functions",
        )


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


def network_socket_calls(source: str) -> list[str]:
    """Every `socket.socket(...)` in `source` that is NOT an AF_UNIX one.

    Read from the AST and not from text, because the property is about what the
    call RESOLVES TO. A text search for `AF_INET` is satisfied by a comment
    explaining why there is none — which is exactly the shape this repository
    keeps finding in its own declarations.

    A first argument that is not a literal `socket.AF_*` attribute is reported
    as unresolved rather than assumed safe, so that `socket.socket(family)`
    cannot pass by hiding its family in a variable.
    """
    out = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not (isinstance(func, ast.Attribute) and func.attr == "socket"
                and isinstance(func.value, ast.Name) and func.value.id == "socket"):
            continue
        if not node.args:
            out.append("socket.socket() with no family — defaults to AF_INET")
            continue
        first = node.args[0]
        if isinstance(first, ast.Attribute) and first.attr == "AF_UNIX":
            continue
        if isinstance(first, ast.Attribute):
            out.append(f"socket.socket(socket.{first.attr}, ...)")
        else:
            out.append(f"socket.socket(<{type(first).__name__}>, ...) — family "
                       f"is not a literal, so it cannot be checked")
    return out


# Modules that exist to speak to something over the network. §W puts the network
# call in the delivery worker and keeps it out of the daemon; an import of any
# of these in the daemon's closure would be that boundary crossed silently, two
# hops from the file somebody was editing.
NETWORK_MODULES = ("urllib", "http", "requests", "httpx", "xmlrpc", "ftplib",
                   "smtplib", "telnetlib", "asyncore", "socketserver")

# The closure files that DO reach a network module, each with the measurement
# that says what it actually does. An allow-list with a reason per entry rather
# than a boolean, because the entries are not equivalent and the difference is
# the finding.
#
# MEASURED 2026-09-15 over `closure(Path("runtime/daemon.py"))` — 30 modules,
# three of them naming a network module:
#
#   context/actor.py           from urllib.parse import quote, unquote
#   runtime/agent_sessions.py  from urllib.parse import quote, unquote
#   runtime/probes.py          import urllib.error / import urllib.request
#
# RE-MEASURED 2026-09-20 — 36 modules, four naming a network module. The fourth
# arrived with SYS-02's producer:
#
#   runtime/lease_store.py     from urllib.parse import quote
#
# It escapes a resource name into a filename (`browser:chromium` ->
# `browser%3Achromium.json`), which is the same string handling the other two
# entries do and opens nothing. The closure grew from 30 to 36 modules for
# other reasons as well; recorded here rather than quietly corrected, because
# the count is the only thing that shows an allow-list being widened one
# plausible entry at a time.
#
# `urllib.parse` is string handling and opens nothing. `runtime/probes.py` is
# the real one, and it is a FINDING rather than something this round repaired:
# `Probes.http_get` and `McpToolsList` open an outbound HTTP connection, so the
# daemon's closure retains the capability to reach the network — but §W's
# sentence ("serverosd 不做网络调用") holds TODAY **by registry contents and not
# by construction**. Of the seven registrations in
# `state/serveros/runtime/registry/resources.yaml`, none declares `http-health`
# or `mcp-tools-list`; every one of them is a local probe (`systemd-unit-state`,
# `relay-socket`, `relay-presence`, `journal-diagnostic`, `container-state`,
# `container-healthcheck`, `process-exists`). So no HTTP probe runs, and adding
# one registration is all it would take to make one run.
#
# Both imports are function-local, so the daemon does not even load
# `urllib.request` at startup. That is a real mitigation and it is not a gate.
#
# This is recorded rather than repaired because the repair is an architecture
# change — moving the HTTP probes behind a provider, or refusing `http-health`
# from the daemon's own probe engine — and §AO forbids inventing architecture in
# the release round. What this gate does supply is the thing that was missing:
# **a second place the daemon can reach the network is now a red test**.
NETWORK_CAPABLE = {
    "runtime/probes.py": "http_get / McpToolsList open an outbound connection",
    "context/actor.py": "urllib.parse only — no I/O",
    "runtime/agent_sessions.py": "urllib.parse only — no I/O",
    "runtime/lease_store.py": "urllib.parse only — no I/O",
}

# The one module that can actually open a connection, as opposed to parsing a
# URL. Named separately so that a new network-capable file has to be added to
# two places, and the second one is the sentence above.
THE_ONE_NETWORK_CAPABLE_MODULE = "runtime/probes.py"


def network_imports(source: str) -> list[str]:
    """`import`/`from` statements naming a network module, deduplicated."""
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found |= {f"import {a.name}" for a in node.names
                      if a.name.split(".")[0] in NETWORK_MODULES}
        elif isinstance(node, ast.ImportFrom) and node.module:
            if node.module.split(".")[0] in NETWORK_MODULES:
                found.add(f"from {node.module} import ...")
    return sorted(found)


class TheDaemonDoesNotReachTheNetwork(unittest.TestCase):
    """§W: 网络调用属于投递 worker，`serverosd` 不做。

    The daemon's whole claim is that it answers "what is true" without depending
    on anything that can be unreachable. A socket to another host is the
    sharpest possible violation: the answer would start depending on the network
    a person is usually asking about BECAUSE it is broken.

    Scanned over the daemon's loader closure rather than over `daemon.py`, and
    over the AST rather than the text, for the same reason the sibling gate is:
    the call this refuses is one a future edit adds two hops away.

    `socket` itself is NOT forbidden. The daemon's IPC is an `AF_UNIX` socket in
    the runtime directory (§22), and §22's "nothing here binds a port" is the
    first assertion below.
    """

    def test_the_daemon_opens_only_unix_sockets(self):
        offenders = []
        for path in sorted(closure(Path("runtime/daemon.py"))):
            for hit in network_socket_calls((REPO / path).read_text()):
                offenders.append(f"{path.as_posix()}: {hit}")
        self.assertEqual(offenders, [], "\n".join(offenders))

    def test_the_network_capable_modules_are_exactly_these(self):
        # The gate. Not "nothing imports a network module" — that is false — but
        # "the set of things that do is exactly the set somebody has looked at
        # and written down". A new one is a red test rather than a silent
        # widening of what the daemon can depend on.
        found = {}
        for path in sorted(closure(Path("runtime/daemon.py"))):
            hits = network_imports((REPO / path).read_text())
            if hits:
                found[path.as_posix()] = hits
        self.assertEqual(sorted(found), sorted(NETWORK_CAPABLE),
                         f"the daemon's closure reaches the network from "
                         f"{sorted(found)}; it may only do so from "
                         f"{sorted(NETWORK_CAPABLE)} — see the constant for what "
                         f"each entry was measured doing")

    def test_only_one_module_can_actually_open_a_connection(self):
        users = [rel for rel in NETWORK_CAPABLE
                 if any("urllib.request" in hit or "urllib.request" == hit.split()[-1]
                        for hit in network_imports((REPO / rel).read_text()))]
        self.assertEqual(users, [THE_ONE_NETWORK_CAPABLE_MODULE],
                         "a second module can open an outbound connection; §W's "
                         "'serverosd 不做网络调用' now has two ways to be false")

    def test_the_http_probe_kinds_are_named_as_the_network_capability(self):
        # The measurement behind §W's sentence, made checkable: the capability
        # lives in two probe classes, and a resource has to DECLARE one of them
        # for it to run. If that stops being true — if a probe kind starts
        # reaching the network without a registration naming it — then "no
        # registration declares it, so nothing calls it" is no longer the reason
        # the daemon is quiet.
        source = (REPO / THE_ONE_NETWORK_CAPABLE_MODULE).read_text()
        for kind in ("http-health", "mcp-tools-list"):
            self.assertIn(kind, source,
                          f"{kind} is gone from {THE_ONE_NETWORK_CAPABLE_MODULE}: "
                          f"either the probe was removed, or this entry is stale")

    def test_the_scan_finds_the_one_socket_the_daemon_does_open(self):
        # The positive control for the first assertion. If the scanner returned
        # an empty list for every input, "no AF_INET socket" would be true and
        # meaningless — so it is required to SEE the AF_UNIX socket that
        # `daemon.py` genuinely opens, and to say that it is fine.
        source = (REPO / "runtime" / "daemon.py").read_text()
        self.assertEqual(network_socket_calls(source), [])
        self.assertIn("AF_UNIX", source,
                      "daemon.py no longer opens a unix socket — either the IPC "
                      "moved, or this control has stopped controlling anything")

    def test_the_scanners_catch_a_network_use(self):
        # The other half of the control: scanners that ignored everything would
        # pass every assertion above. The same two functions, on sources that do
        # the forbidden thing.
        self.assertEqual(
            network_socket_calls("import socket\ns = socket.socket(socket.AF_INET, 1)\n"),
            ["socket.socket(socket.AF_INET, ...)"])
        self.assertTrue(
            network_socket_calls("import socket\ns = socket.socket(socket.AF_UNIX, 1)\n"
                                 "t = socket.socket()\n"))
        self.assertEqual(network_imports("import urllib.request\n"),
                         ["import urllib.request"])
        self.assertEqual(network_imports("import os\nfrom pathlib import Path\n"), [])


if __name__ == "__main__":
    unittest.main()
