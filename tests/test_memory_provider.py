#!/usr/bin/env python3
"""§25/§54/§60 — the Layer C provider contract, its fixture, and its status.

WHAT THIS FILE IS ABOUT
-----------------------
`memory/provider-contract.yaml` was a complete interface declaration with two
of its three named deliverables missing. It says:

    fixture: "a deterministic in-process double; see `memory/fixtures/`"

and `memory/fixtures/` did not exist. It says:

    status_rule: "the status is decided by the conditions above and by nothing
                  else"

and nothing decided it. It says:

    enforcement: "the daemon's imports are checked by test"

and the check searched for three substrings.

The contract is a good contract — the interface is right, the authority rule is
right, and `NEEDS_HUMAN` as a first-class outcome is the decision §60 asks for.
That is exactly what makes the absences worth fixing rather than writing off: a
contract with no fixture is one nobody can test against, and a status rule
nothing executes is a sentence that cannot fail.

§60 governs the outcome throughout: OpenViking may not become the reason the
round does not finish and may not become a dependency of the node runtime. So
what is delivered is the contract, the fixture and the decider — and the
deployment is deferred, with the blocker named, which the contract calls a
first-class result rather than a failure.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
MEMORY = REPO / "memory"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


double_mod = _load("openviking_double", MEMORY / "fixtures" / "openviking_double.py")
status = _load("memory_provider_status", MEMORY / "provider_status.py")

import yaml  # noqa: E402

CONTRACT = yaml.safe_load((MEMORY / "provider-contract.yaml").read_text(encoding="utf-8"))

RECORDS = [
    {"id": "mem:one", "revision": "r1", "text": "the tunnel is task scoped"},
    {"id": "mem:two", "revision": "r1", "text": "unrelated content"},
]


def a_double():
    return double_mod.OpenVikingDouble()


# ---------------------------------------------------------------------------
# The fixture, against the four rules the contract states
# ---------------------------------------------------------------------------


class FixtureTests(unittest.TestCase):
    def test_indexing_the_same_records_twice_is_idempotent(self):
        double = a_double()
        self.assertEqual(double.index(RECORDS), {"indexed": 2, "skipped": 0})
        self.assertEqual(double.index(RECORDS), {"indexed": 0, "skipped": 2})

    def test_a_new_revision_replaces_the_old_one_rather_than_joining_it(self):
        """The failure this rule exists for. An index that keeps both serves a
        superseded fact with a citation that resolves — which is worse than
        serving nothing, because it looks checked."""
        double = a_double()
        double.index(RECORDS)
        double.index([{"id": "mem:one", "revision": "r2", "text": "the tunnel is task scoped"}])
        self.assertEqual(double.held(), [("mem:one", "r2"), ("mem:two", "r1")])

    def test_a_record_with_no_revision_is_refused_rather_than_indexed(self):
        """`mem:` ids are what makes a hit citeable, and the revision is what
        makes the citation resolve to the thing that was indexed."""
        double = a_double()
        with self.assertRaises(ValueError):
            double.index([{"id": "mem:three", "text": "no revision"}])

    def test_a_record_with_no_id_is_refused_rather_than_given_one(self):
        """`must_not: "modify a record, or invent ids"`. The second half is the
        one worth a test: inventing an id would make an unciteable record
        look citeable."""
        double = a_double()
        with self.assertRaises(ValueError):
            double.index([{"revision": "r1", "text": "no id"}])

    def test_every_hit_carries_a_layer_b_id_and_a_revision(self):
        double = a_double()
        double.index(RECORDS)
        hits = double.search("tunnel")
        self.assertEqual([h["record_id"] for h in hits], ["mem:one"])
        self.assertEqual(hits[0]["revision"], "r1")

    def test_invalidate_is_correct_for_a_record_the_index_never_held(self):
        """Rebuild paths call it speculatively. An implementation that raised
        on an unknown id would make `invalidate` unusable as the first step of
        the rebuild the contract calls the supported operation."""
        double = a_double()
        double.index(RECORDS)
        self.assertEqual(double.invalidate(["mem:never-held"]), {"removed": 0})
        self.assertEqual(double.invalidate(["mem:two", "mem:never-held"]),
                         {"removed": 1})
        self.assertEqual(double.invalidate(["mem:two"]), {"removed": 0})

    def test_health_reaches_nothing(self):
        """The daemon may ask this, and the daemon's tick may not touch the
        network. A health check that probed a remote service would put that
        service on the path of a node designed to answer without one."""
        import ast
        tree = ast.parse((MEMORY / "fixtures" / "openviking_double.py").read_text())
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported - {"__future__", "argparse", "json", "sys"}, set())
        self.assertEqual(a_double().health()["status"], "AVAILABLE")

    def test_the_fixture_names_itself_as_a_fixture(self):
        """A status reader has to be able to tell a fixture from a deployment,
        and a double that reported itself as the provider would make every
        test that passed against it a claim about OpenViking."""
        self.assertEqual(a_double().kind, "fixture")

    def test_the_fixtures_own_selftest_passes(self):
        """It is a deliverable named by a contract, so it is runnable without
        a test runner — and the selftest is how somebody with neither the
        suite nor the round's context checks it."""
        proc = subprocess.run(
            [sys.executable, str(MEMORY / "fixtures" / "openviking_double.py"),
             "--selftest"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("4 contract properties hold", proc.stdout)


# ---------------------------------------------------------------------------
# The status rule, which was a sentence nothing executed
# ---------------------------------------------------------------------------


class StatusRuleTests(unittest.TestCase):
    def test_the_declared_conditions_are_the_contracts_conditions(self):
        """Two lists of adoption conditions is the defect this round has found
        four times. The contract is the authority; this module's copy is a
        transcription, and this asserts the transcription."""
        declared = next(c for c in CONTRACT["candidates"]
                        if c["id"] == "provider:openviking")
        self.assertEqual([text for _, text, _ in status.CONDITIONS],
                         declared["adoption_conditions"])

    def test_the_status_values_are_the_contracts_status_values(self):
        self.assertEqual(list(status.STATUSES), CONTRACT["status"]["values"])

    def test_nothing_observed_is_needs_human_and_not_ready_to_promote(self):
        """§60's outcome, and the one that must be reached by default.

        `READY_TO_PROMOTE` means the contract, the adapter and the fixture all
        exist and pass. A caller that observed nothing has no basis for that
        claim, and defaulting to it would make the fixture's existence look
        like a deployment.
        """
        result = status.evaluate(None)
        self.assertEqual(result["status"], "NEEDS_HUMAN")
        self.assertEqual(len(result["unobserved"]), len(status.CONDITIONS))
        self.assertNotEqual(result["status"], "READY_TO_PROMOTE")

    def test_every_condition_holding_is_ready_to_promote_and_not_available(self):
        """The other end, so the test above is not passed by a function that
        refuses everything. Note it is READY_TO_PROMOTE and not AVAILABLE:
        running the provider is a deployment decision nobody has taken."""
        result = status.evaluate({key: True for key, _, _ in status.CONDITIONS})
        self.assertEqual(result["status"], "READY_TO_PROMOTE")
        self.assertTrue(result["not_yet_running"])
        self.assertNotEqual(result["status"], "AVAILABLE")

    def test_the_first_failing_condition_names_the_blocker(self):
        """Ordered by the contract, because the blocker a reader is told about
        is whichever fails first — and an unobserved condition is not a
        failing one, so this measures the ORDER and not the count."""
        result = status.evaluate({
            "no_dependency_in_the_runtime_artifact": True,
            "provider_is_independently_supervised": False,
            "private_authenticated_endpoint": False,
        })
        self.assertEqual(result["status"], "NEEDS_HUMAN")
        self.assertIn("provider_is_independently_supervised", result["decided_by"])
        self.assertIn("a function of serverosd", result["decided_by"])

    def test_the_authority_conditions_are_asked_before_the_deployment_ones(self):
        """The contract's order is load-bearing, and this is the assertion that
        says WHICH order. Version 2 put the daemon boundary and the authority
        relationship first: a node whose provider outranks its own current
        records has a worse problem than one whose endpoint is not yet
        authenticated, and a reader told the second when the first is true has
        been told about the wrong thing."""
        keys = [key for key, _, _ in status.CONDITIONS]
        self.assertEqual(keys[:4], [
            "no_dependency_in_the_runtime_artifact",
            "no_network_call_in_the_tick_loop",
            "not_the_ability_catalogue",
            "current_records_outrank_retrieved_memory"])

    def test_a_running_provider_is_available_and_not_ready_to_promote(self):
        """The other half of the pair above. AVAILABLE and READY_TO_PROMOTE
        differ in exactly one fact, and it is not a condition — which is why it
        is a separate argument and why this test passes the same observations
        twice with one argument changed."""
        observations = {key: True for key, _, _ in status.CONDITIONS}
        self.assertEqual(status.evaluate(observations)["status"],
                         "READY_TO_PROMOTE")
        running = status.evaluate(observations, deployed=True)
        self.assertEqual(running["status"], "AVAILABLE")
        self.assertNotIn("not_yet_running", running)

    def test_a_failed_condition_outranks_a_running_provider(self):
        """AVAILABLE is not 'something answered on the port'. A provider that
        is up and violates the contract is NEEDS_HUMAN, and a decider that let
        `deployed=True` override a failure would turn the whole condition list
        into a formality."""
        observations = {key: True for key, _, _ in status.CONDITIONS}
        observations["current_records_outrank_retrieved_memory"] = False
        result = status.evaluate(observations, deployed=True)
        self.assertEqual(result["status"], "NEEDS_HUMAN")
        self.assertIn("current_records_outrank_retrieved_memory",
                      result["decided_by"])

    def test_an_unobserved_condition_is_not_a_failed_one(self):
        """`UNOBSERVED` and `FAILED` call for different actions — go and look,
        versus do not adopt — and collapsing them would make "we have not
        checked" read as "it is unsuitable"."""
        partial = {key: True for key, _, _ in status.CONDITIONS}
        partial.pop("no_network_call_in_the_tick_loop")
        result = status.evaluate(partial)
        states = {c["condition"]: c["state"] for c in result["conditions"]}
        self.assertEqual(states["no_network_call_in_the_tick_loop"], "UNOBSERVED")
        self.assertIn("no_network_call_in_the_tick_loop", result["decided_by"])

    def test_the_decision_comes_from_the_conditions_and_from_nothing_else(self):
        """The contract's status rule, asserted as behaviour: two runs with
        the same observations agree whatever the machine is like."""
        observations = {key: True for key, _, _ in status.CONDITIONS}
        self.assertEqual(status.evaluate(observations),
                         status.evaluate(dict(observations)))

    def test_the_cli_prints_a_status_for_the_declared_candidate(self):
        proc = subprocess.run(
            [sys.executable, str(MEMORY / "provider_status.py"),
             "--candidate", "provider:openviking"],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        record = json.loads(proc.stdout)
        self.assertEqual(record["candidate"], "provider:openviking")
        self.assertEqual(record["status"], "NEEDS_HUMAN")


if __name__ == "__main__":
    unittest.main()
