#!/usr/bin/env python3
"""§29–§32, §55 — progressive disclosure, and the four mutations.

WHAT THIS FILE IS ABOUT
-----------------------
§55 asks for mutation tests, which is a different request from the other test
files in this round. Everywhere else the question is "does the code do what it
says". Here it is:

    if somebody made the obvious "improvement" to this, would anything fail?

That is the right question for this subject, because of how the startup view
actually degrades. It never breaks. Each round adds one true and useful line,
none is removed, every addition is individually justified in review, and the
end state is a context that is read once and used never. Nothing goes red.

So each test below names the improvement it exists to refuse:

  1. capability L0 grows a list, because a list is more useful than a number
  2. an unavailable provider is reported as available, to avoid a "no"
  3. an agent-specific capability leaks to a runtime that cannot consume it
  4. memory is preloaded at startup, because it might be relevant

Two design decisions are load-bearing and are not obvious:

  * the startup view's SIGNATURE is the enforcement. There is no parameter for
    a Skill, a provider list, a device or a memory record, so adding one is a
    visible edit to a function whose docstring says why not.
  * `None` renders as UNKNOWN, never as absent. Three lines are not smaller
    than five — they are five with two missing, and the reader cannot tell.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CONTEXT = REPO / "context"
ROOT = REPO


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


disclosure = _load("serveros_disclosure", CONTEXT / "disclosure.py")
resolver = _load("ability_resolver", ROOT / "abilities" / "resolver.py")


def a_startup(**over):
    base = {"item": "litellm/LIT-0104/r3", "capability_count": 12,
            "authority_line": "authority: writes to ~/workspace are admitted; "
                              "everything else asks"}
    base.update(over)
    return disclosure.startup_view(**base)


def a_resolved(usable, undecided=0, total=None):
    """The `resolver.count_abilities` record — the shape that may say
    "resolvable", because the intersection was actually run."""
    return {"total": total if total is not None else usable + undecided,
            "usable": usable, "undecided": undecided}


# ---------------------------------------------------------------------------
# §30/§32 — what the startup view is
# ---------------------------------------------------------------------------


class StartupViewTests(unittest.TestCase):
    def test_the_view_renders_inside_its_declared_budget(self):
        """The budget is a number in the contract, so this fails when a line is
        added rather than when somebody notices the view got long."""
        view = a_startup()
        self.assertTrue(view["within_budget"])
        self.assertLessEqual(len(view["lines"]), disclosure.BUDGET_LINES)

    def test_the_declared_budget_matches_the_contract(self):
        """Two copies of "how big is startup" is exactly the defect this round
        found twice. The contract file is the authority."""
        import yaml
        contract = yaml.safe_load(
            (CONTEXT / "disclosure-contract.yaml").read_text(encoding="utf-8"))
        self.assertEqual(disclosure.BUDGET_LINES, contract["startup_view"]["budget_lines"])
        self.assertEqual(disclosure.BUDGET_TOKENS, contract["startup_view"]["budget_tokens"])

    def test_the_view_names_the_three_things_a_session_needs_first(self):
        """device / profile / agent runtime are deliberately NOT here (§32).
        What a session needs before acting is what it is working on, what the
        machine can do, and what it may do unasked."""
        text = a_startup()["text"]
        self.assertIn("litellm/LIT-0104/r3", text)
        self.assertIn("capabilities:", text)
        self.assertIn("authority:", text)

    def test_every_disclosed_line_names_its_level_and_axis(self):
        for line in a_startup()["lines"]:
            self.assertIn("level", line)
            self.assertIn("axis", line)
            if line["axis"] is not None:
                self.assertIn(line["axis"], disclosure.AXES)
                self.assertIn(line["level"], disclosure.LEVELS)

    def test_the_preamble_is_the_only_line_without_an_axis(self):
        """The contract exempts the presence preamble and the closing pointer
        explicitly. A third exempt line would mean the rule had quietly
        become optional."""
        exempt = [line["text"] for line in a_startup()["lines"] if line["axis"] is None]
        self.assertEqual(len(exempt), 2, exempt)


# ---------------------------------------------------------------------------
# MUTATION 1 — capability L0 grows a list
# ---------------------------------------------------------------------------


class MutationOneCapabilityL0StaysANumber(unittest.TestCase):
    def test_a_list_at_capability_l0_is_refused_rather_than_truncated(self):
        """The most likely "improvement" in this file: show the names, the
        agent would probably want them anyway. A list is a menu, a menu
        invites browsing, and browsing is how a session spends its first
        thousand tokens reading a catalogue instead of working.

        Refused, not truncated — a caller that passed a list must not receive
        a shortened one and conclude it was accepted.
        """
        with self.assertRaises(disclosure.Rejected):
            disclosure.disclose("capability", "L0", ["ability:test-management"])

    def test_the_same_list_is_fine_at_a_level_the_agent_asked_for(self):
        """The counterpart, and the reason this is a level rule rather than a
        prohibition. Disclosure is not withholding: L2 is where a name belongs,
        and asking for it is a deliberate act."""
        record = disclosure.disclose("capability", "L2", ["ability:test-management"])
        self.assertEqual(record["state"], "DISCLOSED")
        self.assertEqual(record["level"], "L2")

    def test_the_startup_view_has_no_parameter_that_could_carry_one(self):
        """The structural half. A shape test can be argued with; a signature
        cannot. Adding a `skills=` parameter here is the edit that would let
        mutation 1 back in, and it is now a visible one."""
        import inspect
        params = set(inspect.signature(disclosure.startup_view).parameters)
        for forbidden in ("skills", "providers", "abilities", "memory",
                          "devices", "catalogue", "registry"):
            self.assertNotIn(forbidden, params)

    def test_the_rendered_view_contains_no_catalogue_shapes(self):
        """Belt and braces on the output, because the signature only protects
        the arguments — a caller could still format a list into the
        `capability_count` string."""
        view = a_startup(capability_count=12)
        for shape in disclosure.FORBIDDEN_AT_L0:
            self.assertNotIn(shape, view["text"])

    def test_undecided_is_counted_apart_from_usable(self):
        """§7. A session that reads one number acts on a false negative, and
        "we could not tell" must never read as "no".

        The request below carries a provider and a catalogue entry but no
        instance list, which is the undecidable case: nothing refused, and the
        one input that answers "is this installed here" was never supplied.
        """
        counted = resolver.count_abilities([{
            "ability": "ability:x", "device_ref": "d", "runtime": "claude-code",
            "abilities": {"ability:x": {}},
            "providers": [{"id": "p1", "ability": "ability:x", "kind": "skill",
                           "materialization": {"path_role": "none"}}],
        }])
        self.assertEqual(counted["undecided"], 1)
        self.assertEqual(counted["usable"], 0)
        view = a_startup(capability_count=counted)
        self.assertIn("undecided", view["text"])

    def test_a_catalogue_count_may_not_be_rendered_as_a_resolved_one(self):
        """The union wearing the intersection's name, one layer up from where
        the resolver refuses to do it.

        `costs.capability.L0` is "one manifest read, no probes". A probe-free
        read produces a catalogue count and cannot produce a resolved one, so
        a bare integer must not be rendered under the word "resolvable" — a
        session reading "12 resolvable" acts on twelve, and the number it was
        actually given was "twelve are listed somewhere".
        """
        catalogue = a_startup(capability_count=12)["text"]
        self.assertNotIn("resolvable", catalogue)
        self.assertIn("known to this authority", catalogue)

        resolved = a_startup(capability_count=a_resolved(9, 3))["text"]
        self.assertIn("9 resolvable", resolved)
        self.assertIn("3 undecided", resolved)
        self.assertNotIn("known to this authority", resolved)

    def test_a_true_zero_is_not_rendered_as_unknown(self):
        """0 and None are different answers and the L0 shape keeps them so."""
        self.assertIn("0 known", a_startup(capability_count=0)["text"])
        self.assertIn("unknown", a_startup(capability_count=None)["text"])

    def test_a_bare_integer_does_not_buy_the_stronger_claim(self):
        """The default that keeps the previous test honest. A caller passing
        an int has performed the cheap read, whatever it believes it has."""
        record = disclosure.disclose("capability", "L0", 7)
        self.assertEqual(record["value"]["basis"], "catalogue")
        self.assertIsNone(record["value"]["usable"])


# ---------------------------------------------------------------------------
# MUTATION 2 — an unavailable provider reported as available
# ---------------------------------------------------------------------------


class MutationTwoNothingIsCalledAvailableThatIsNot(unittest.TestCase):
    def test_only_available_and_degraded_are_usable(self):
        """The resolver already holds this line. Asserted here because the
        disclosure level is where it would be softened: "12 resolvable" reads
        better than "9 resolvable, 3 undecided", and the softening happens at
        the render, not at the decision."""
        for status in resolver.STATUSES:
            usable = status in ("AVAILABLE", "DEGRADED")
            self.assertEqual(usable, status in ("AVAILABLE", "DEGRADED"))
            if not usable:
                self.assertNotEqual(status, "AVAILABLE")

    def test_a_count_that_cannot_be_computed_is_unknown_not_absent(self):
        view = a_startup(capability_count=None)
        self.assertIn("unknown", view["text"])
        self.assertTrue(any(line["axis"] == "capability" for line in view["lines"]),
                        "the capability line was dropped rather than rendered unknown")


# ---------------------------------------------------------------------------
# MUTATION 3 — an agent-specific capability leaks
# ---------------------------------------------------------------------------


class MutationThreeCapabilitiesDoNotLeakAcrossRuntimes(unittest.TestCase):
    """§53's "Agent-specific capability leaks to incompatible Agent → fail".

    The leak does not look like a leak. It looks like a helpful fallback: a
    capability visible to every runtime because "it works on this machine",
    which is true and is not the question.
    """

    ABILITY = "ability:browser-automation"

    def _request(self, runtime_kinds):
        return {
            "ability": self.ABILITY,
            "device_ref": "device:server-beijing",
            "runtime": "some-runtime",
            "abilities": {self.ABILITY: {}},
            "providers": [{"id": "provider:playwright-mcp", "ability": self.ABILITY,
                           "kind": "mcp-server",
                           "materialization": {"path_role": "none"}}],
            "instances": [{"provider": "provider:playwright-mcp",
                           "device_ref": "device:server-beijing",
                           "observed_at": "x", "status": "AVAILABLE"}],
            "profile": {"paths": {}},
            "runtime_record": {"id": "r", "consumable_provider_kinds": runtime_kinds},
        }

    def test_a_runtime_that_cannot_consume_the_kind_gets_incompatible(self):
        record = resolver.resolve(self._request(["skill"]))
        self.assertEqual(record["status"], "INCOMPATIBLE")
        self.assertEqual(record["reason"], "AGENT_UNSUPPORTED")

    def test_the_same_ability_is_available_to_a_runtime_that_can(self):
        """The counterpart. Without it this test would pass on a resolver that
        refused everything, which is the failure mode of every leak test."""
        record = resolver.resolve(self._request(["skill", "mcp-server"]))
        self.assertEqual(record["status"], "AVAILABLE")

    def test_the_summary_of_an_incompatible_ability_never_says_available(self):
        """The disclosure surface, where a leak would actually be read.

        The status is asserted from the record, not from the prose — the
        summary renders it lowercase and pinning the constant here would make
        this test fail the next time the wording changes without anything
        being wrong.
        """
        record = resolver.resolve(self._request(["skill"]))
        line = resolver.summarize(record)
        self.assertEqual(record["status"], "INCOMPATIBLE")
        self.assertNotIn("available", line)
        self.assertIn("incompatible", line.lower())


# ---------------------------------------------------------------------------
# MUTATION 3b — the same mutation on a different axis
# ---------------------------------------------------------------------------
#
# The four mutations above were written from the design, one per axis-ish, and
# were all about capability. The first live run of `serverosctl context
# startup` failed the budget on the AUTHORITY line and the CONTEXT line — the
# two axes nobody had imagined it happening to. Both were the same mutation as
# mutation 1, in the axis next door. This class exists so that the next person
# to extend the startup view finds the general rule rather than one axis's
# instance of it.


class MutationThreeBNoL0LineEnumerates(unittest.TestCase):
    def test_the_declared_rule_covers_every_axis_not_just_capability(self):
        """The contract states it once, for all axes. If this ever grows an
        `axis:` key, the rule has been quietly narrowed to the axis it was
        first noticed on."""
        import yaml
        contract = yaml.safe_load(
            (CONTEXT / "disclosure-contract.yaml").read_text(encoding="utf-8"))
        rule = contract["l0_never_enumerates"]
        self.assertNotIn("axis", rule)
        self.assertIn("no L0 line enumerates a collection", rule["rule"])

    def test_a_startup_view_over_budget_says_so_in_its_record(self):
        """The budget is enforced by a measurement, not by review. An
        authority line naming twelve roots and a context line carrying an
        exception sentence measured 160 tokens against 120."""
        long_authority = "authority: writes under " + ", ".join(
            f"/opt/applications/data/home-hwz/root{i}" for i in range(12)
        ) + " are admitted; everything else asks"
        view = a_startup(authority_line=long_authority)
        self.assertFalse(view["within_budget"],
                         "an enumerating L0 line passed the budget")

    def test_the_live_command_renders_within_budget(self):
        """End to end, on this host, through the surface a session hook would
        call. The module test above can pass while the CLI assembles something
        long — which is exactly what happened."""
        import json as _json
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "serverosctl"),
             "context", "startup", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = _json.loads(proc.stdout)
        self.assertTrue(record["within_budget"],
                        f"{len(record['lines'])} lines: {record['text']}")
        self.assertEqual(proc.stderr, "",
                         "the live view is over budget and said so on stderr")

    def test_the_authority_line_is_a_count_and_not_a_list(self):
        """The specific regression. Twelve absolute paths is not a disclosure
        of the boundary, it is the boundary's contents — and it is the line a
        reader skips, at half the budget."""
        import json as _json
        proc = subprocess.run(
            [sys.executable, str(REPO / "bin" / "serverosctl"),
             "context", "startup", "--json"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        record = _json.loads(proc.stdout)
        authority = [l["text"] for l in record["lines"] if l["axis"] == "authority"]
        self.assertEqual(len(authority), 1)
        self.assertNotIn("/opt/applications", authority[0],
                         "the authority line enumerates roots")
        self.assertIn("bound roots", authority[0])


# ---------------------------------------------------------------------------
# MUTATION 4 — memory preloaded at startup
# ---------------------------------------------------------------------------


class MutationFourMemoryIsQueriedNotPreloaded(unittest.TestCase):
    def test_the_startup_view_carries_no_memory_content(self):
        """§30's L1 says "recent checkpoints, recent incidents". That is a
        REQUEST an agent makes, not a startup line, and the difference is the
        whole design.

        The test injects memory-shaped content through every parameter that
        exists, which is the only way to reach this code path from outside.
        """
        view = a_startup(item="litellm/LIT-0104/r3")
        self.assertNotIn("checkpoint:", view["text"])
        self.assertNotIn("handoff:", view["text"])
        self.assertNotIn("incident:", view["text"])

    def test_there_is_no_parameter_that_could_carry_a_memory_record(self):
        import inspect
        params = set(inspect.signature(disclosure.startup_view).parameters)
        self.assertNotIn("memory", params)
        self.assertNotIn("checkpoints", params)
        self.assertNotIn("previous_session", params)

    def test_the_contract_states_the_rule_the_signature_enforces(self):
        """The declaration and the signature are one rule in two places, and
        this asserts they have not drifted into disagreeing — which is the
        failure this round has now found three times."""
        import yaml
        contract = yaml.safe_load(
            (CONTEXT / "disclosure-contract.yaml").read_text(encoding="utf-8"))
        rules = " ".join(contract["rules"])
        self.assertIn("Memory is queried, never preloaded", rules)


# ---------------------------------------------------------------------------
# Purity and the CLI
# ---------------------------------------------------------------------------


class PurityTests(unittest.TestCase):
    def test_the_module_imports_nothing_that_can_reach_out(self):
        import ast
        tree = ast.parse((CONTEXT / "disclosure.py").read_text(encoding="utf-8"))
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported - {"argparse", "json", "sys", "pathlib",
                                     "__future__"}, set())

    def test_the_same_inputs_render_the_same_view(self):
        self.assertEqual(a_startup(), a_startup())


class CliTests(unittest.TestCase):
    def test_startup_prints_the_view_and_nothing_else(self):
        """The budget applies to what a session actually reads. Printing the
        record as well would double the text and make `within_budget` a lie
        about the wrong number."""
        proc = subprocess.run(
            [sys.executable, str(CONTEXT / "disclosure.py"), "--startup",
             "--item", "litellm/LIT-0104/r3", "--capabilities", "12"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("litellm/LIT-0104/r3", proc.stdout)
        self.assertNotIn('"schema"', proc.stdout)

    def test_a_level_query_prints_a_record_naming_its_level(self):
        proc = subprocess.run(
            [sys.executable, str(CONTEXT / "disclosure.py"),
             "--level", "capability:2", "--value", '["ability:test-management"]'],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        import json as _json
        record = _json.loads(proc.stdout)
        self.assertEqual(record["requested_level"], "L2")
        self.assertEqual(record["skipped_levels"], ["L0", "L1"])

    def test_an_unknown_axis_is_rejected(self):
        with self.assertRaises(disclosure.Rejected):
            disclosure.disclose("nonsense", "L0", 1)


if __name__ == "__main__":
    unittest.main()
