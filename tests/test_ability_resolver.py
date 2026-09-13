"""The Ability Resolver.

The property under test is that `EffectiveAbility` is an INTERSECTION. Eighty
tests of a union resolver and eighty of an intersection resolver look almost
identical, and they differ in exactly the cases that cost real time: one input
saying yes while seven say nothing, and a definite no arriving after an
unreadable input.

So the two load-bearing tests here are the negative controls — the ones that
fail if the resolver ever stops being an intersection:

  `test_an_input_that_says_nothing_is_not_a_yes`
  `test_a_definite_refusal_outranks_an_unreadable_input`

Everything else is the vocabulary, and it exists so a new reason value cannot
be added to the contract without a case that produces it.
"""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("ability_resolver",
                                               ROOT / "abilities" / "resolver.py")
resolver = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(resolver)


ABILITY = "ability:browser-automation"

CATALOGUE = {ABILITY: {"revision": "abc123"}, "ability:test-management": {}}


def provider(**over):
    base = {
        "id": "provider:playwright-mcp",
        "ability": ABILITY,
        "kind": "mcp-server",
        "materialization": {"path_role": "toolchains", "relative": "playwright/cli.js"},
        "probe": {"kind": "mcp-tools-list"},
    }
    base.update(over)
    return base


PROFILE = {"paths": {"toolchains": {"path": "/opt/x/toolchains", "state": "BOUND"}}}
INSTANCE = [{"provider": "provider:playwright-mcp", "device_ref": "device:server-beijing",
             "observed_at": "2026-09-13T00:00:00Z", "status": "AVAILABLE"}]
RUNTIME = {"id": "claude-code",
           "consumable_provider_kinds": ["skill", "mcp-server", "cli"]}


def request(**over):
    base = {
        "ability": ABILITY,
        "device_ref": "device:server-beijing",
        "runtime": "claude-code",
        "abilities": CATALOGUE,
        "providers": [provider()],
        "instances": INSTANCE,
        "profile": PROFILE,
        "runtime_record": RUNTIME,
    }
    base.update(over)
    return base


class AvailableTests(unittest.TestCase):
    def test_every_input_agreeing_is_available(self):
        record = resolver.resolve(request())
        self.assertEqual(record["status"], "AVAILABLE")
        self.assertEqual(record["provider"], "provider:playwright-mcp")
        self.assertIsNone(record["reason"])
        self.assertEqual(record["schema"], "serveros/capability-resolution.v1")

    def test_the_record_names_the_device_and_the_time_it_is_about(self):
        """There is no fleet-wide availability, only per-device answers."""
        record = resolver.resolve(request())
        self.assertEqual(record["device_ref"], "device:server-beijing")
        self.assertIn("resolved_at", record)


class IntersectionTests(unittest.TestCase):
    """The two that fail if this stops being an intersection."""

    def test_an_input_that_says_nothing_is_not_a_yes(self):
        """The original defect: a capability visible because one input
        mentioned it, with every other input unconsulted. Here the provider
        exists, the device is fine, and no instance of it was ever registered
        on this machine — which must not read as available."""
        record = resolver.resolve(request(instances=[]))
        self.assertEqual(record["status"], "UNAVAILABLE")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertEqual(record["candidates"][0]["refused_by"], "device_binding")

    def test_a_definite_refusal_outranks_an_unreadable_input(self):
        """UNKNOWN and UNAVAILABLE call for opposite actions, so an unreadable
        input must not shadow a definite no. Without this, a device with
        nothing installed reports UNKNOWN for everything — which reads as
        "we have not looked" and hides the finding that nothing is there."""
        record = resolver.resolve(request(profile=None, instances=[]))
        self.assertEqual(record["status"], "UNAVAILABLE",
                         "an absent profile shadowed a definite no")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertIn("device_profile", record["inputs_missing"])

    def test_a_provider_for_another_ability_is_not_a_provider_for_this_one(self):
        """The empty-provider case, which is a definite no and not an unknown:
        nothing here was unreadable, there is simply no provider for this
        ability on this device."""
        record = resolver.resolve(request(
            providers=[provider(id="provider:other", ability="ability:something-else")],
        ))
        self.assertEqual(record["status"], "UNAVAILABLE")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")

    def test_when_nothing_refuses_and_an_input_is_unreadable_the_answer_is_unknown(self):
        """The other half, and the one this test used to get wrong: its name
        said UNKNOWN while its assertion said UNAVAILABLE, because the request
        it built filtered its own provider away before any candidate existed.

        Nothing is supplied that could refuse — the provider matches, the path
        role is bound, the runtime can consume the kind — and the instance list
        is absent, which is the one input that answers "is it installed here".
        """
        request_without_instances = request()
        del request_without_instances["instances"]
        record = resolver.resolve(request_without_instances)
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertIn("device_binding", record["candidates"][0]["unevaluated"])
        self.assertIn("device_binding", record["detail"])

    def test_a_catalogue_entry_and_a_provider_are_not_an_available_ability(self):
        """The defect this module exists to prevent, reintroduced one level
        down and caught by probe rather than by argument.

        A request carrying only a catalogue entry and a provider used to come
        back `AVAILABLE`, `summarize` rendered it "available via p1", and
        `inputs_missing` listed seven of the eight inputs — the module's own
        docstring, contradicted by the module. The intersection was being
        enforced only between whichever inputs happened to be present.

        Asserted on `inputs_seen` as well as the status, because a future
        change that returns UNKNOWN for some other reason would satisfy the
        status assertion while leaving this defect in place.

        The provider declares no on-disk footprint, which is what makes this
        the defect's exact shape: a provider needing a bound path would be
        refused outright at device_profile, and a definite refusal is the
        right answer there. Nothing here can refuse.
        """
        record = resolver.resolve({
            "ability": ABILITY,
            "device_ref": "device:server-beijing",
            "runtime": "claude-code",
            "abilities": CATALOGUE,
            "providers": [provider(materialization={"path_role": "none"})],
        })
        self.assertNotEqual(record["status"], "AVAILABLE")
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertEqual(record["inputs_seen"], ["ability"])
        self.assertNotIn("available via", resolver.summarize(record))

    def test_the_record_always_says_which_inputs_it_could_see(self):
        """A confident answer computed from whichever inputs happened to be
        readable is the failure `inputs_seen` exists to expose."""
        record = resolver.resolve(request(health=None, policy=None))
        self.assertIn("device_binding", record["inputs_seen"])
        self.assertIn("runtime_health", record["inputs_missing"])
        self.assertIn("policy", record["inputs_missing"])
        self.assertEqual(len(record["inputs_seen"]) + len(record["inputs_missing"]),
                         len(resolver.INPUT_ORDER))


class ReasonTests(unittest.TestCase):
    """One case per reason value, so a value cannot be added to the contract
    without a case that produces it."""

    def test_an_ability_not_in_the_catalogue(self):
        record = resolver.resolve(request(ability="ability:document-stewardship"))
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertIn("pinned catalogue", record["detail"])

    def test_a_path_role_that_is_not_bound(self):
        profile = {"paths": {"toolchains": {"state": "UNBOUND"}}}
        record = resolver.resolve(request(profile=profile))
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertEqual(record["candidates"][0]["refused_by"], "device_profile")

    def test_a_device_missing_a_declared_dependency(self):
        record = resolver.resolve(request(
            providers=[provider(requirements={"python3": True})],
            device_capabilities={"python3": False}))
        self.assertEqual(record["reason"], "DEVICE_DEPENDENCY_MISSING")
        self.assertEqual(record["candidates"][0]["refused_by"], "device_capability")

    def test_an_unreachable_network_requirement_is_its_own_reason(self):
        """"no python" and "no route to the registry" produce the same status
        and call for different repairs, so they are different reasons."""
        record = resolver.resolve(request(
            providers=[provider(requirements={"network": "registry.example"})],
            device_capabilities={"network": None}))
        self.assertEqual(record["reason"], "NETWORK_UNAVAILABLE")

    def test_a_runtime_that_cannot_consume_the_provider_kind(self):
        record = resolver.resolve(request(
            runtime_record={"id": "codex", "consumable_provider_kinds": ["skill"]}))
        self.assertEqual(record["status"], "INCOMPATIBLE")
        self.assertEqual(record["reason"], "AGENT_UNSUPPORTED")

    def test_a_provider_whose_functional_probe_fails(self):
        record = resolver.resolve(request(
            health={"provider:playwright-mcp": {"functional": "FAILED"}}))
        self.assertEqual(record["reason"], "RUNTIME_UNHEALTHY")

    def test_a_policy_denial(self):
        record = resolver.resolve(request(policy={"denied_abilities": [ABILITY]}))
        self.assertEqual(record["reason"], "POLICY_DENIED")

    def test_every_contract_reason_is_produced_by_some_case_in_this_file(self):
        """Keeps the list above honest: adding a reason to the contract
        without adding a case here fails, rather than quietly shipping a
        vocabulary value nothing can emit."""
        source = Path(__file__).read_text(encoding="utf-8")
        for reason in resolver.REASONS:
            self.assertIn(reason, source, f"no test constructs {reason}")


class DegradationTests(unittest.TestCase):
    def test_a_floating_dependency_is_degraded_not_available(self):
        """Case C. The provider works, and it cannot be said to be the same
        provider tomorrow. Reporting AVAILABLE would make "this machine can do
        browser automation" a claim about a registry's current resolution."""
        instances = [{**INSTANCE[0], "floating_dependency": True}]
        record = resolver.resolve(request(instances=instances))
        self.assertEqual(record["status"], "DEGRADED")
        self.assertEqual(record["finding"], "PERSISTENT_RUNTIME_FLOATING_DEPENDENCY")

    def test_a_degraded_substitute_is_chosen_only_when_the_preferred_one_is_not(self):
        degraded = provider(id="provider:slow", preference=100)
        preferred = provider(id="provider:fast", preference=1)
        record = resolver.resolve(request(
            providers=[degraded, preferred],
            instances=[{**INSTANCE[0], "floating_dependency": True},
                       {"provider": "provider:fast", "device_ref": "device:server-beijing",
                        "observed_at": "x", "status": "AVAILABLE"}]))
        self.assertEqual(record["status"], "AVAILABLE")
        self.assertEqual(record["provider"], "provider:fast")

    def test_a_missing_materialization_is_never_degraded(self):
        """"Not installed" and "a bit worse than intended" are different
        findings and only one of them is the ability being present."""
        record = resolver.resolve(request(instances=[]))
        self.assertNotEqual(record["status"], "DEGRADED")


class PurityTests(unittest.TestCase):
    """The resolver is a decision kernel. Everything that touches the machine
    is a probe the caller performs and passes in as a record."""

    def test_the_module_imports_nothing_that_can_reach_out(self):
        """Checked over the decision functions, not the whole file.

        The first version of this test searched the module text and failed on
        `Path(` — which appears in `main`, where reading the request file the
        user named on the command line is the entire job. A purity check that
        covers the CLI is a purity check that will be relaxed until it covers
        nothing. So it is scoped: `main` may read files, and nothing else may.
        """
        import ast

        tree = ast.parse((ROOT / "abilities" / "resolver.py").read_text(encoding="utf-8"))
        forbidden_roots = {"socket", "subprocess", "urllib", "requests", "os", "time",
                           "datetime", "open", "Path"}
        impure = []

        for node in tree.body:
            if not isinstance(node, ast.FunctionDef) or node.name == "main":
                continue
            for inner in ast.walk(node):
                # `<forbidden>.<attr>` — os.environ, time.time, socket.socket.
                # Matched as an ATTRIBUTE rather than as a bare name, because
                # the first version of this check flagged `count_abilities`,
                # whose parameter is literally named `requests`.
                if (isinstance(inner, ast.Attribute)
                        and isinstance(inner.value, ast.Name)
                        and inner.value.id in forbidden_roots):
                    impure.append(f"{node.name} references {inner.value.id}.{inner.attr}")
                # A bare call — open(...), Path(...).
                if (isinstance(inner, ast.Call) and isinstance(inner.func, ast.Name)
                        and inner.func.id in forbidden_roots):
                    impure.append(f"{node.name} calls {inner.func.id}")

        self.assertEqual(impure, [], "the decision kernel reaches out of itself")

    def test_no_module_level_import_can_reach_out(self):
        import ast

        tree = ast.parse((ROOT / "abilities" / "resolver.py").read_text(encoding="utf-8"))
        imported = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                imported |= {a.name.split(".")[0] for a in node.names}
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        self.assertEqual(imported - {"argparse", "json", "sys", "pathlib",
                                     "__future__"}, set())

    def test_the_same_request_resolves_the_same_way_twice(self):
        first = resolver.resolve(request())
        second = resolver.resolve(request())
        first.pop("resolved_at"), second.pop("resolved_at")
        self.assertEqual(first, second)

    def test_a_malformed_request_is_rejected_rather_than_answered(self):
        with self.assertRaises(resolver.Rejected):
            resolver.resolve({"ability": ABILITY})


class SummaryTests(unittest.TestCase):
    """The disclosure surfaces. A summary is one line — a relationship a
    session can read without spending context on it."""

    def test_available_reads_as_available(self):
        line = resolver.summarize(resolver.resolve(request()))
        self.assertIn("available", line)
        self.assertIn("playwright", line)

    def test_unknown_names_what_is_missing(self):
        record = {"status": "UNKNOWN", "ability": ABILITY,
                  "inputs_missing": ["device_profile"]}
        self.assertIn("device_profile", resolver.summarize(record))

    def test_a_refusal_names_its_reason(self):
        record = resolver.resolve(request(instances=[]))
        self.assertIn("PROVIDER_NOT_INSTALLED", resolver.summarize(record))

    def test_the_count_runs_no_probe_and_separates_undecided_from_unusable(self):
        """The capability L0 line. `undecided` is counted apart from `usable`
        because a session that reads them as one number will act on a
        false negative."""
        counted = resolver.count_abilities([request(), request(instances=[])])
        self.assertEqual(counted["total"], 2)
        self.assertEqual(counted["usable"], 1)
        self.assertEqual(counted["by_status"]["UNAVAILABLE"], 1)


class CliTests(unittest.TestCase):
    def test_the_cli_prints_a_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps(request()), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "abilities" / "resolver.py"),
                 "--request", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["status"], "AVAILABLE")

    def test_a_malformed_request_exits_two_without_a_record(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "request.json"
            path.write_text(json.dumps({"ability": ABILITY}), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "abilities" / "resolver.py"),
                 "--request", str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertEqual(proc.stdout, "")


if __name__ == "__main__":
    unittest.main()
