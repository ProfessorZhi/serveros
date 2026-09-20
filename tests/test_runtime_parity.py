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
    """`capabilities.known_gaps` records the Codex workspace entry as absent.

    It is a real gap — a Codex session under the workspace root gets no
    ServerOS workspace entry, and a Claude session does — and it cannot be
    closed from here, because closing it means writing into the user's Agent
    configuration.

    A check that is red the day it is written gets turned off, so a defect the
    contract already records moves to `acknowledged` instead. These tests are
    about the ways that could go wrong: acknowledgement becoming deletion, and
    the matcher never firing at all.
    """

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
        result = parity.report(contract(), observed=self.OBSERVED_WITH_THE_GAP)
        self.assertEqual(result["findings"], [])
        self.assertEqual(len(result["acknowledged"]), 1)
        self.assertEqual(result["acknowledged"][0]["declared_gap"]["state"],
                         "ABSENT_ON_THIS_HOST")

    def test_acknowledging_it_does_not_delete_it(self):
        """The failure this design invites. Moving a defect out of `findings`
        must not remove it from the record, or the acknowledgement has become
        the thing it was meant to avoid."""
        result = parity.report(contract(), observed=self.OBSERVED_WITH_THE_GAP)
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
        result = parity.report(contract(), observed=unrecorded)
        self.assertEqual(len(result["acknowledged"]), 1)
        self.assertEqual([f["runtime"] for f in result["findings"]], ["claude-code"])

    def test_the_matcher_matches_on_the_path_and_not_on_prose(self):
        """Written as a direct test because the first version compared the
        declared `workspace AGENTS.md` against an absolute path containing a
        slash and so never matched anything — making the acknowledgement
        silently dead while the outcome still looked correct, since the gap
        was a finding either way."""
        gap = {"surface": "codex workspace AGENTS.md"}
        finding = {"runtime": "codex", "code": "SURFACE_MISSING_ON_DEVICE",
                   "path": "/opt/applications/data/home-hwz/workspace/AGENTS.md"}
        self.assertIsNotNone(parity._matches_a_declared_gap(finding, [gap]))
        other = {"runtime": "codex", "path": "/somewhere/else/AGENTS.md"}
        self.assertIsNone(parity._matches_a_declared_gap(other, [gap]))

    def test_a_recorded_gap_that_stops_being_observed_is_reported(self):
        """The other direction, which keeps the list honest: a gap written
        down and never seen again is either fixed or noise, and both want a
        look."""
        all_present = {
            runtime: {field: [{"path": "/x", "present": True}]
                      for field in parity.OBSERVABLE_SURFACES}
            for runtime in ("claude-code", "codex")}
        result = parity.report(contract(), observed=all_present)
        self.assertEqual(result["unobserved_declared_gaps"],
                         ["codex workspace AGENTS.md"])


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
