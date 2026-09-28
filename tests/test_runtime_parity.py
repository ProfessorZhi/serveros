#!/usr/bin/env python3
"""§9/§18/§53 — the two-runtime parity claim, and what a program can check.

WHAT THIS FILE IS ABOUT
-----------------------
`docs/claude-codex-parity.md` says ServerOS is "one capability system with two
runtime adapters". `abilities/agent-runtime-contract.yaml` declares the record
that would make it checkable. Until `abilities/runtime_parity.py`, both were
prose to every program in this repository, and the contract's parity block — a
field-by-field comparison of two records — was a claim two documents agreed
on.

Two things are worth stating about the tests below, because they are choices
rather than coverage:

1. They do not test that the two runtimes behave the same. They cannot, and a
   check that inferred behaviour from configuration would be a claim about
   configuration wearing a claim about behaviour. `docs/claude-codex-parity.md`
   lists ten smoke items; eight need a session in each runtime to establish.

2. Every assertion here is of the form "nothing bad was found", which is the
   shape a broken checker also produces. So each check has a probe beside it
   that breaks the input and asserts the finding fires — and one that asserts
   the real contract is still conformant, because a checker that fails
   everything passes every negative test in this file.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


parity = _load("serveros_runtime_parity", REPO / "abilities" / "runtime_parity.py")

import yaml  # noqa: E402

CONTRACT_PATH = REPO / "abilities/agent-runtime-contract.yaml"
REGISTRY_PATH = REPO / "fleet/registry.yaml"


def contract() -> dict:
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))


def registry() -> dict:
    return yaml.safe_load(REGISTRY_PATH.read_text(encoding="utf-8"))


def codes(result: dict) -> set[str]:
    return {f["code"] for f in result["findings"]}


# ---------------------------------------------------------------------------
# The real files
# ---------------------------------------------------------------------------


class LiveContractTests(unittest.TestCase):
    def test_the_contract_that_ships_is_internally_consistent(self):
        """The whole point of the reader running against the shipped file
        rather than a fixture. A checker that only ever sees fixtures agrees
        with whatever fixture the author wrote."""
        result = parity.report(contract(), registry())
        self.assertEqual(result["findings"], [],
                         f"{result['findings']}")
        self.assertEqual(result["verdict"], "CONFORMANT")

    def test_every_runtime_the_fleet_names_has_an_adapter_declaration(self):
        """The specific cross-file claim. `fleet/registry.yaml` names two
        agents on each of three devices and nothing compared those to the
        closed `ids` set."""
        result = parity.report(contract(), registry())
        self.assertEqual([u["code"] for u in result["unknowns"]
                          if u["code"] == "DEVICE_NAMES_UNKNOWN_AGENT"], [])

    def test_the_check_says_what_it_cannot_establish(self):
        """A CONFORMANT that a reader takes as "the two runtimes behave the
        same" has been taken as something this check cannot establish. The
        scope is therefore in the record, not only in the docstring."""
        result = parity.report(contract(), registry())
        self.assertIn("behavioural", result["scope"])
        self.assertIn("not inferred", result["scope"])


# ---------------------------------------------------------------------------
# Probes — each breaks one thing and asserts the finding fires
# ---------------------------------------------------------------------------


class ContractProbes(unittest.TestCase):
    def test_a_runtime_id_with_no_adapter_surfaces_is_a_finding(self):
        """Naming a runtime in `ids` and declaring nothing else about it.

        The set is no longer asserted exactly, and the loss is deliberate. A
        runtime added to `ids` alone is missing an adapter surface AND has no
        entry in every per-runtime compared capability, so two independent
        checks are right to fire — `SURFACE_NOT_DECLARED` for the surface it
        was promised, `PARITY_COMPARES_A_SHARED_VALUE` for the comparison that
        cannot differ for it. Asserting the pair would pin this test to the
        number of per-runtime capabilities the contract happens to have today.
        What must not become possible is a fresh contract reporting findings;
        that is `test_the_contract_that_ships_is_internally_consistent`.
        """
        broken = copy.deepcopy(contract())
        broken["ids"] = [*broken["ids"], "gemini-cli"]
        self.assertIn("SURFACE_NOT_DECLARED", codes(parity.report(broken)))

    def test_a_surface_for_a_runtime_that_does_not_exist_is_a_finding(self):
        broken = copy.deepcopy(contract())
        broken["capabilities"]["mcp_surfaces"]["aider"] = {"path_role": "home"}
        self.assertIn("SURFACE_FOR_UNKNOWN_RUNTIME",
                      codes(parity.report(broken)))

    def test_parity_naming_a_field_that_cannot_differ_is_a_finding(self):
        """`allowed_differences` exempting a field nothing declares is an
        exemption that reads as though it exempted something."""
        broken = copy.deepcopy(contract())
        broken["parity"]["allowed_differences"].append("nonexistent_field")
        self.assertIn("PARITY_NAMES_AN_ABSENT_FIELD",
                      codes(parity.report(broken)))

    def test_a_field_both_compared_and_excused_is_a_finding(self):
        """The field this breaks is the one `compared_fields` really carries.

        It used to name `skill_discovery`, which by W7 is not a parity field at
        all — it is a runtime feature. Appending it to `allowed_differences`
        still produced a finding, but the wrong one
        (`PARITY_NAMES_AN_ABSENT_FIELD`), so this probe was passing while
        proving something other than what its name says. Taking the field from
        the contract under test means a future rename breaks this test loudly
        instead of quietly re-aiming it.
        """
        broken = copy.deepcopy(contract())
        compared = list(broken["parity"]["compared_fields"])
        self.assertTrue(compared, "no compared field to excuse")
        broken["parity"]["allowed_differences"].append(compared[0])
        self.assertIn("FIELD_BOTH_COMPARED_AND_ALLOWED",
                      codes(parity.report(broken)))

    def test_a_contract_with_no_runtimes_says_so_rather_than_passing(self):
        """The vacuous case. Two records compared, zero runtimes declared, and
        a naive implementation reports perfect parity."""
        broken = copy.deepcopy(contract())
        broken["ids"] = []
        self.assertIn("NO_RUNTIMES_DECLARED", codes(parity.report(broken)))

    def test_a_device_naming_an_unknown_agent_is_a_finding(self):
        broken = copy.deepcopy(registry())
        broken["devices"][0]["agents"] = ["claude-code", "gemini-cli"]
        self.assertIn("DEVICE_NAMES_UNKNOWN_AGENT",
                      codes(parity.report(contract(), broken)))


# ---------------------------------------------------------------------------
# The session-start drivers
# ---------------------------------------------------------------------------


class DriverTests(unittest.TestCase):
    """A driver is registered inside a file that exists either way.

    That sentence is the whole reason this half is not a third `*_surfaces`
    entry. A hook surface's file is present on a node where the driver fires
    nowhere, so a presence probe answers `present: true` and the report goes on
    saying the node is wired — the shape this repository keeps finding, one
    level down. These tests are about the reading that cannot be had that way.
    """

    def _observed(self, state="NOT_REGISTERED", program_present=True):
        return {"drift_sync": {
            "state": state, "event": "SessionStart",
            "surface": "/home/x/.claude/settings.json",
            "registrations": 1,
            "program": "/home/x/workspace/records/information/raw/drift_sync.py",
            "program_present": program_present,
            "because": "no command names it"}}

    def _without_the_recorded_gap(self):
        """The contract with the `drift_sync` entry removed.

        The mechanism tests below ask whether a defect produces a finding, and
        the shipped contract already records this one — so on the shipped file
        the answer is `acknowledged`, and a test naming `findings` would be
        asserting the absence of the very acknowledgement that
        `test_the_live_gap_is_acknowledged_and_still_in_the_record` pins. Both
        questions are worth asking and they need different inputs.
        """
        broken = copy.deepcopy(contract())
        gaps = broken["capabilities"]["known_gaps"]
        broken["capabilities"]["known_gaps"] = [g for g in gaps
                                                if not g.get("driver")]
        self.assertTrue(len(broken["capabilities"]["known_gaps"]) < len(gaps),
                        "no recorded driver gap to remove")
        return broken

    def test_an_unregistered_driver_is_a_finding(self):
        result = parity.report(self._without_the_recorded_gap(),
                               observed_drivers=self._observed())
        self.assertIn("DRIVER_NOT_REGISTERED", codes(result))

    def test_a_driver_nobody_observed_is_unknown_and_not_absent(self):
        """The distinction the surface half already makes, applied here. A
        driver nobody looked for must not read as one that is not registered;
        assuming it fires is how this check would be true by default."""
        result = parity.report(contract(), observed_drivers={})
        self.assertIn("DRIVER_NOT_OBSERVED",
                      {u["code"] for u in result["unknowns"]})
        self.assertNotIn("DRIVER_NOT_REGISTERED", codes(result))
        self.assertEqual(result["verdict"], "CONFORMANT",
                         "an unobserved driver was counted as a defect")

    def test_no_driver_observation_at_all_is_unknown_not_conformant(self):
        result = parity.report(contract())
        self.assertIn("DRIVERS_NOT_OBSERVED",
                      {u["code"] for u in result["unknowns"]})

    def test_an_unreadable_surface_is_unknown_and_not_a_missing_registration(self):
        """A file that is there and cannot be parsed is a question this node
        could not answer. Reporting it as NOT_REGISTERED would be inventing a
        fact about a file nobody could read."""
        result = parity.report(contract(),
                               observed_drivers=self._observed(state="UNREADABLE"))
        self.assertIn("DRIVER_SURFACE_UNREADABLE",
                      {u["code"] for u in result["unknowns"]})
        self.assertNotIn("DRIVER_NOT_REGISTERED", codes(result))

    def test_a_registration_pointing_at_nothing_is_a_finding_of_its_own(self):
        """Opposite failures with one cause between them: a marker with no
        program behind it, and a program nobody registered. The finding carries
        `registered` so a reader can tell which one they are looking at
        without parsing the sentence."""
        unregistered = parity.report(
            self._without_the_recorded_gap(),
            observed_drivers=self._observed(program_present=False))
        self.assertIn("DRIVER_PROGRAM_ABSENT", codes(unregistered))
        self.assertFalse([f for f in unregistered["findings"]
                          if f["code"] == "DRIVER_PROGRAM_ABSENT"][0]["registered"])

        broken_marker = parity.report(
            self._without_the_recorded_gap(),
            observed_drivers=self._observed(state="REGISTERED",
                                            program_present=False))
        self.assertTrue([f for f in broken_marker["findings"]
                         if f["code"] == "DRIVER_PROGRAM_ABSENT"][0]["registered"])

    def test_a_gap_records_the_finding_it_covers_and_not_the_driver(self):
        """A recorded gap is not a silencer for every defect about its subject.

        A driver can fail two ways and the entry records one of them. Without
        `acknowledges`, `DRIVER_PROGRAM_ABSENT` on the same driver would be
        acknowledged by an entry about the registration — a defect nobody
        recorded, reported as one they did.
        """
        result = parity.report(contract(),
                               observed_drivers=self._observed(program_present=False))
        self.assertIn("DRIVER_PROGRAM_ABSENT", codes(result))
        self.assertEqual([a["code"] for a in result["acknowledged"]],
                         ["DRIVER_NOT_REGISTERED"],
                         "one gap acknowledged two different findings")

    def test_the_live_gap_is_acknowledged_and_still_in_the_record(self):
        """On this node the driver is installed and unwired, and the contract
        records that as a gap. A check that is red the day it is written gets
        turned off, so it is acknowledged instead — and acknowledgement must
        not become deletion."""
        result = parity.report(contract(), observed_drivers=self._observed())
        self.assertEqual(result["findings"], [])
        self.assertEqual([a["declared_gap"]["driver"] for a in result["acknowledged"]],
                         ["drift_sync"])
        self.assertIn("drift_sync", json.dumps(result))

    def test_a_recorded_driver_gap_nobody_observed_is_not_reported_as_closed(self):
        """The driver half of the staleness trap, and the reason it is in this
        class rather than the one above: an unobserved driver would otherwise
        make its own recorded gap read as fixed."""
        result = parity.report(contract(), observed_drivers={})
        self.assertEqual(result["unobserved_declared_gaps"], [])

    def test_a_registered_driver_makes_its_recorded_gap_stale(self):
        """And the direction that matters operationally: the day somebody wires
        the hook, the record says so rather than going quiet."""
        result = parity.report(contract(),
                               observed_drivers=self._observed(state="REGISTERED"))
        self.assertEqual(result["unobserved_declared_gaps"], ["drift_sync"])

    def test_the_matcher_matches_a_driver_gap_on_the_driver(self):
        """A path match cannot work here: the driver's surface file exists
        either way, so the path is never the thing that is missing."""
        gap = {"driver": "drift_sync", "state": "UNWIRED_ON_THIS_HOST",
               "acknowledges": ["DRIVER_NOT_REGISTERED"]}
        finding = {"driver": "drift_sync", "code": "DRIVER_NOT_REGISTERED"}
        self.assertIsNotNone(parity._matches_a_declared_gap(finding, [gap]))
        self.assertIsNone(parity._matches_a_declared_gap(
            {"driver": "other", "code": "DRIVER_NOT_REGISTERED"}, [gap]))
        # The same driver, a finding the entry does not name.
        self.assertIsNone(parity._matches_a_declared_gap(
            {"driver": "drift_sync", "code": "DRIVER_PROGRAM_ABSENT"}, [gap]))
        # And an entry that names no finding acknowledges none, rather than
        # everything about its driver.
        self.assertIsNone(parity._matches_a_declared_gap(
            finding, [{"driver": "drift_sync", "state": "UNWIRED_ON_THIS_HOST"}]))

    def test_the_driver_rows_are_read_from_what_was_observed(self):
        rows = parity.observed_driver_rows(self._observed(), contract())
        self.assertEqual([r["driver"] for r in rows], ["drift_sync"])
        self.assertEqual(rows[0]["state"], "NOT_REGISTERED")
        # `registered` and not `present`: a row here answers "is this driver
        # wired", which is not "is this path there", and the two must not read
        # as one key.
        self.assertFalse(rows[0]["registered"])
        self.assertNotIn("present", rows[0])
        self.assertEqual(parity.observed_driver_rows({}, contract()), [])


class ObservationProbes(unittest.TestCase):
    def _observed(self, claude="present", codex="present"):
        def one(state):
            return [{"path": "/x/CLAUDE.md", "present": state == "present"}]
        return {"claude-code": {"instruction_surfaces": one(claude),
                                "mcp_surfaces": one("present")},
                "codex": {"instruction_surfaces": one(codex),
                          "mcp_surfaces": one("present")}}

    def test_a_declared_surface_missing_on_the_device_is_a_finding(self):
        result = parity.report(contract(), observed=self._observed(codex="absent"))
        self.assertIn("SURFACE_MISSING_ON_DEVICE", codes(result))

    def test_a_surface_nobody_looked_at_is_unknown_and_not_absent(self):
        """The distinction the whole record is built on. "We did not look" and
        "it is not there" call for different actions, and collapsing them
        makes the second unobservable."""
        result = parity.report(contract(), observed={"codex": {}})
        self.assertIn("SURFACE_UNOBSERVED", {u["code"] for u in result["unknowns"]})
        self.assertNotIn("SURFACE_MISSING_ON_DEVICE", codes(result))
        self.assertEqual(result["verdict"], "CONFORMANT",
                         "an unobserved surface was counted as a defect")

    def test_nothing_observed_at_all_is_unknown_not_conformant_by_default(self):
        result = parity.report(contract(), registry())
        self.assertIn("SURFACES_NOT_OBSERVED", {u["code"] for u in result["unknowns"]})


# ---------------------------------------------------------------------------
# The declared gap, and the trap in the way it is acknowledged
# ---------------------------------------------------------------------------


class DeclaredGapTests(unittest.TestCase):
    """A declared gap, injected, and the traps on the way to acknowledging it.

    The gap exercised here used to be one the contract shipped: the Codex half
    of the workspace instruction pair, `workspace/AGENTS.md`, absent — so a
    Codex session started under the workspace root got no ServerOS workspace
    entry while a Claude session did. It could not be closed from inside this
    repository (closing it means writing into the user's Agent configuration),
    so it was recorded as `ABSENT_ON_THIS_HOST` instead.

    The file was created on 2026-09-18, and the entry was retired on
    2026-09-28. `serverosctl device agents` reported it as an
    `unobserved_declared_gap` a moment before that — which is the check doing
    the job it was written for, rather than a person noticing, and is why the
    entry had to go: a gap whose subject no longer exists is a line every
    reader has to re-derive.

    So the gap is now INJECTED into a copy of the contract rather than read out
    of it. The injection keeps the contract's own declared surface text as its
    subject, because `_matches_a_declared_gap` matches on the role and the
    relative path — a made-up subject would be a fixture of the matcher rather
    than of the input, and the matcher is one of the things under test.

    A check that is red the day it is written gets turned off, so a defect the
    contract already records moves to `acknowledged` instead. These tests are
    about the ways that could go wrong: acknowledgement becoming deletion, and
    the matcher never firing at all.
    """

    #: The subject, spelled the way the contract spells it. Injected, not read:
    #: see the class docstring for why this is a fixture now.
    RECORDED_GAP = {
        "surface": "codex workspace AGENTS.md",
        "state": "ABSENT_ON_THIS_HOST",
        "because": "injected — in this scenario the file is absent.",
        "closing_action": "NEEDS_HUMAN — injected for the mechanism tests.",
    }

    def _contract_with_the_gap_recorded(self) -> dict:
        """The shipped contract, plus one declared gap.

        Copied and mutated rather than written out, for the reason
        `DriverTests._without_the_recorded_gap` exists: a scenario contract
        that has drifted from the shipped one tests the scenario.
        """
        with_the_gap = copy.deepcopy(contract())
        with_the_gap["capabilities"]["known_gaps"].append(
            copy.deepcopy(self.RECORDED_GAP))
        return with_the_gap

    OBSERVED_WITH_THE_GAP = {
        "codex": {"instruction_surfaces": [
            {"path": "/opt/applications/data/home-hwz/workspace/AGENTS.md",
             "present": False},
            {"path": "/home/hwz/AGENTS.md", "present": True}],
            "mcp_surfaces": [{"path": "/home/hwz/.codex/config.toml",
                              "present": True}]},
        "claude-code": {"instruction_surfaces": [
            {"path": "/opt/applications/data/home-hwz/workspace/CLAUDE.md",
             "present": True}],
            "mcp_surfaces": [{"path": "/home/hwz/.claude.json",
                              "present": True}]},
    }

    def test_the_recorded_gap_is_acknowledged_rather_than_reported(self):
        result = parity.report(self._contract_with_the_gap_recorded(),
                               observed=self.OBSERVED_WITH_THE_GAP)
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["acknowledged"]), 1)
        self.assertEqual(result["acknowledged"][0]["declared_gap"]["state"],
                         "ABSENT_ON_THIS_HOST")

    def test_acknowledging_it_does_not_delete_it(self):
        """The failure this design invites. Moving a defect out of `findings`
        must not remove it from the record, or the acknowledgement has become
        the thing it was meant to avoid."""
        result = parity.report(self._contract_with_the_gap_recorded(),
                               observed=self.OBSERVED_WITH_THE_GAP)
        rendered = json.dumps(result)
        self.assertIn("/opt/applications/data/home-hwz/workspace/AGENTS.md",
                      rendered)
        self.assertEqual(result["acknowledged"][0]["code"],
                         "SURFACE_MISSING_ON_DEVICE")

    def test_only_a_recorded_gap_is_acknowledged(self):
        """An unrecorded defect stays a defect. Without this, the
        acknowledgement path would be a general silencer."""
        unrecorded = copy.deepcopy(self.OBSERVED_WITH_THE_GAP)
        unrecorded["claude-code"]["mcp_surfaces"] = [
            {"path": "/home/hwz/.claude.json", "present": False}]
        result = parity.report(self._contract_with_the_gap_recorded(),
                               observed=unrecorded)
        self.assertEqual(len(result["acknowledged"]), 1)
        self.assertEqual([f["runtime"] for f in result["findings"]], ["claude-code"])

    def test_the_matcher_matches_on_the_path_and_not_on_prose(self):
        """Written as a direct test because the first version compared the
        declared `workspace AGENTS.md` against an absolute path containing a
        slash and so never matched anything — making the acknowledgement
        silently dead while the outcome still looked correct, since the gap
        was a finding either way."""
        gap = {"surface": self.RECORDED_GAP["surface"]}
        finding = {"runtime": "codex", "code": "SURFACE_MISSING_ON_DEVICE",
                   "path": "/opt/applications/data/home-hwz/workspace/AGENTS.md"}
        self.assertIsNotNone(parity._matches_a_declared_gap(finding, [gap]))
        other = {"runtime": "codex", "path": "/somewhere/else/AGENTS.md"}
        self.assertIsNone(parity._matches_a_declared_gap(other, [gap]))

    def _all_present(self):
        """Every declared surface observed and present, at the paths the
        contract actually declares.

        The paths used to be a placeholder — `{"path": "/x"}` — and that
        placeholder is why the sibling test below exists. Staleness is now
        decided by whether the gap's SUBJECT was observed, and a gap whose
        subject is a path can only be recognised by that path's tail; a
        fixture of `/x` therefore observes nothing and would have made this
        test pass by reporting no gap at all rather than by reporting a fixed
        one. The literal tail is not decoration, it is the input.
        """
        return {
            "codex": {"instruction_surfaces": [
                {"path": "/x/workspace/AGENTS.md", "present": True},
                {"path": "/x/AGENTS.md", "present": True}],
                "mcp_surfaces": [{"path": "/x/.codex/config.toml",
                                  "present": True}]},
            "claude-code": {"instruction_surfaces": [
                {"path": "/x/workspace/CLAUDE.md", "present": True}],
                "mcp_surfaces": [{"path": "/x/.claude.json",
                                  "present": True}]},
        }

    def test_a_recorded_gap_that_stops_being_observed_is_reported(self):
        """The other direction, which keeps the list honest: a gap written
        down and never seen again is either fixed or noise, and both want a
        look."""
        result = parity.report(self._contract_with_the_gap_recorded(),
                               observed=self._all_present())
        self.assertEqual(result["unobserved_declared_gaps"],
                         ["codex workspace AGENTS.md"])

    def test_a_gap_nobody_looked_at_is_not_reported_as_fixed(self):
        """The trap on the other side of the test above, and it was live.

        Staleness means "observed, and the defect is gone" — so a run that read
        nothing must not produce it. `report(contract, observed={"codex": {}})`
        used to list the AGENTS.md gap as stale, i.e. it reported "either it was
        fixed or the record is wrong" about a surface nobody had opened. Same
        collapse as everywhere else here: an absence of measurement, rendered as
        a measurement of absence.
        """
        result = parity.report(self._contract_with_the_gap_recorded(),
                               observed={"codex": {}})
        self.assertEqual(result["unobserved_declared_gaps"], [])
        self.assertIn("SURFACE_UNOBSERVED",
                      {u["code"] for u in result["unknowns"]})


# ---------------------------------------------------------------------------
# The surface
# ---------------------------------------------------------------------------


class CliTests(unittest.TestCase):
    def test_device_agents_runs_and_reports_a_verdict(self):
        """The command runs, and its exit code agrees with what it reported.

        `rc == 0` would have been "this node is conformant", which is a
        property of the node and not of this code — and one this node does not
        have. It reports DEFECT for three genuinely absent surfaces:
        `workspace/CLAUDE.md`, `workspace/AGENTS.md`, and codex's
        `.codex/config.toml`, which does not exist here at all (`~/.codex/`
        holds sessions and nothing else). Closing that means writing the user's
        Agent configuration, which the report itself marks NEEDS_HUMAN.

        So the assertion is the coherence of the command: the ids it compared,
        a verdict it is allowed to reach, and an exit code that matches the
        verdict. A broken CLI still fails this.
        """
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "serverosctl"),
             "device", "agents", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        record = json.loads(proc.stdout)
        self.assertEqual(record["runtimes"], ["claude-code", "codex"])
        self.assertIn(record["verdict"], ("CONFORMANT", "DEFECT"))
        expected = 0 if record["verdict"] == "CONFORMANT" else 1
        self.assertEqual(proc.returncode, expected,
                         f"verdict {record['verdict']} with rc "
                         f"{proc.returncode}\n{proc.stderr}")

    def test_the_surface_reports_what_it_observed_not_what_is_declared(self):
        """End to end, on this host. The command reads the disk; a version of
        it that echoed the declaration back would pass every other test here,
        and would be the declared-but-absent pathology with a CLI on top."""
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "serverosctl"),
             "device", "agents", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        record = json.loads(proc.stdout)
        rows = record["observed_surfaces"]
        self.assertTrue(rows, "no observed surface reached the record")
        paths = [row["path"] for row in rows]
        self.assertTrue(all(p and p.startswith("/") for p in paths))
        # The assertion above would also hold for a record that echoed the
        # contract's declared spec list back, because those paths are absolute
        # too. This is what separates the two: a declared path is one the
        # contract names; an observed one is one this run actually read. So
        # re-read the filesystem here and require the record to agree with it.
        for row in rows:
            self.assertIsInstance(row["present"], bool)
            self.assertEqual(row["present"], Path(row["path"]).exists(),
                             f"{row['path']} is reported present="
                             f"{row['present']} but the filesystem says "
                             f"{Path(row['path']).exists()}")
        # And the record must carry the field each row belongs to, or a reader
        # cannot tell an instruction surface from an MCP one.
        self.assertTrue(all(row["field"] for row in rows))

    def test_the_driver_row_agrees_with_an_independent_read_of_the_surface(self):
        """End to end, on this host, and it is the assertion the surface half
        cannot make for the driver half.

        A driver row that echoed the declaration back would say `REGISTERED`
        because the contract says the driver must be — and the surface's own
        rows would look identical, since the settings file is present either
        way. So this recomputes the answer here, from the file, and requires the
        command's record to agree with it. A marker search here rather than a
        call into the module on purpose: a test that reused the code under test
        to check the code under test would pass with the marker match broken.

        `registrations` is asserted too, and the mutation run is why. Without
        it this test was **green against a collector that found no commands at
        all**: `drift_sync` is not registered on this node, so "I read the
        surface and none of its commands match" and "I read no commands" both
        produce `NOT_REGISTERED` — the assertion could not tell a measured
        absence from an empty measurement, which is the one distinction this
        whole record is built on. Pinning the count pins the collection half
        independently of the matching half, so breaking either one fails here.
        """
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "serverosctl"),
             "device", "agents", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        record = json.loads(proc.stdout)
        rows = record["observed_drivers"]
        self.assertTrue(rows, "no observed driver reached the record")
        declared = {d["name"]: d for d in parity.declared_drivers(contract())}
        for row in rows:
            spec = declared[row["driver"]]
            self.assertEqual(row["event"], spec["event"])
            surface = Path(row["surface"])
            self.assertTrue(surface.is_file(), f"{surface} is not a file")
            document = json.loads(surface.read_text(encoding="utf-8"))
            event = document.get("hooks", {}).get(spec["event"])

            def commands(node):
                # Written out as loops rather than comprehensions: the nested
                # comprehension over `node.values()` cannot see the first one's
                # `k`, and a test that reads as a puzzle is a test nobody
                # re-reads when it goes red.
                out = []
                if isinstance(node, dict):
                    for key, value in node.items():
                        if key == "command" and isinstance(value, str):
                            out.append(value)
                        else:
                            out.extend(commands(value))
                elif isinstance(node, list):
                    for item in node:
                        out.extend(commands(item))
                return out

            found = commands(event)
            self.assertEqual(row["registrations"], len(found),
                             "the record's command count is not the one on disk")
            expected = any(spec["marker"] in c for c in found)
            self.assertEqual(row["state"],
                             "REGISTERED" if expected else "NOT_REGISTERED",
                             f"{surface} says {expected} for {spec['marker']} "
                             f"and the record says {row['state']}")
            # The program's presence is a second, independent fact.
            self.assertEqual(row["program_present"],
                             Path(row["program"]).is_file() if row["program"] else None)


class ObservedSurfaceRowsTest(unittest.TestCase):
    """The machine-readable half of "say what you looked at".

    This class exists because the API-level assertion above is **vacuous on
    this host**: all seven rows here are `present: True`, so "the record agrees
    with the filesystem" cannot tell a record that measured from one that
    writes `True` unconditionally. A synthetic observation with one missing
    path is what gives that sentence teeth — and, more to the point, it is the
    only place the absence branch is exercised at all.
    """

    def test_a_path_that_was_read_and_is_absent_is_a_finding_with_a_reason(self):
        observed = {"claude-code": {"instruction_surfaces": [
            {"path": "/data/home/vince/CLAUDE.md", "present": True},
            {"path": "/data/home/vince/gone.md", "present": False},
        ]}}
        rows = parity.observed_surface_rows(observed, ["claude-code"])
        self.assertEqual([r["path"] for r in rows],
                         ["/data/home/vince/CLAUDE.md",
                          "/data/home/vince/gone.md"])
        self.assertIsNone(rows[0]["because"])
        self.assertEqual(rows[1]["because"],
                         "the path was read and is not there")

    def test_a_runtime_that_was_never_observed_produces_no_row(self):
        """"没有测量"不得渲染成"测量到不存在"。前者是缺口，后者是发现。"""
        self.assertEqual(parity.observed_surface_rows({}, ["claude-code"]), [])

    def test_a_row_carries_the_field_it_belongs_to(self):
        """少了 field，读者分不出 instruction surface 和 MCP surface。"""
        observed = {"r": {"mcp_surfaces": [{"path": "/x", "present": True}]}}
        rows = parity.observed_surface_rows(observed, ["r"])
        self.assertEqual(rows[0]["field"], "mcp_surfaces")
        self.assertEqual(rows[0]["runtime"], "r")

    def test_the_surfaces_are_a_closed_set(self):
        """一个没声明的 field 不会被当成新的一类悄悄放行。"""
        observed = {"r": {"something_new": [{"path": "/x", "present": True}]}}
        self.assertEqual(parity.observed_surface_rows(observed, ["r"]), [])


if __name__ == "__main__":
    unittest.main()
