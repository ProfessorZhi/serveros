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
import tempfile
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


class ANobodyAskedInputIsNotANoEither(unittest.TestCase):
    """The intersection's guard, in the REFUSAL direction.

    `IntersectionTests` guards the yes: it fails if an absent input is read as
    agreement. Nothing guarded the no. A request supplying no catalogue and no
    provider set used to come back

        status UNAVAILABLE · reason PROVIDER_NOT_INSTALLED
        detail "runtime-management is not in the pinned catalogue"
        inputs_seen [] · inputs_missing [all eight]

    — two sentences, each a definite finding about a set that was never handed
    over, printed on the same record whose `inputs_seen` is empty. `inputs_seen`
    said "nothing was consulted" while `detail` said "this was looked up and it
    is not there", and a reader takes the sentence, not the field.

    The four cases below are two pairs. Within each pair the ONLY difference is
    whether the set was supplied, and the two must not answer alike — which is
    the property, not either answer on its own. A test that only asserted the
    new UNKNOWN would be satisfied by a resolver that had stopped refusing
    altogether.
    """

    def test_a_request_supplying_no_catalogue_is_not_a_catalogue_miss(self):
        request_without_catalogue = request()
        del request_without_catalogue["abilities"]
        record = resolver.resolve(request_without_catalogue)
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertIsNone(record["reason"])
        self.assertNotIn("ability", record["inputs_seen"])
        self.assertIn("ability", record["inputs_missing"])
        self.assertIn("the ability catalogue", record["detail"])
        # The sentence that used to be here, and the reason it was wrong: it
        # names a finding about a catalogue this request never carried.
        self.assertNotEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertNotIn("is not in the pinned catalogue", record["detail"])

    def test_a_catalogue_that_was_supplied_and_misses_is_still_a_finding(self):
        """The contrast. Same ability, same absence of providers — only the
        catalogue is now in hand, and a miss against a list somebody handed
        over is what `PROVIDER_NOT_INSTALLED` is for."""
        record = resolver.resolve(request(ability="ability:not-a-real-ability"))
        self.assertEqual(record["status"], "UNAVAILABLE")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertIn("pinned catalogue", record["detail"])
        self.assertIn("ability", record["inputs_seen"])

    def test_a_request_supplying_no_provider_set_is_not_a_provider_miss(self):
        """The second pair, and the one the shipped example request lands in:
        a catalogue in hand, no providers at all. The old answer was
        `UNAVAILABLE · "no provider implements runtime-management"` with
        `inputs_seen: ["ability"]` — a definite finding about a provider set
        nobody supplied, decided on the strength of the one input that was."""
        request_without_providers = request()
        del request_without_providers["providers"]
        record = resolver.resolve(request_without_providers)
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertIsNone(record["reason"])
        self.assertIn("ability", record["inputs_seen"])
        self.assertIn("the provider set", record["detail"])
        self.assertFalse(record["candidates"])

    def test_a_provider_set_that_was_supplied_and_does_not_match_is_a_finding(self):
        """Supplied and empty, or supplied for other abilities, are both a
        published closed list that does not mention this ability. Either way
        the list was in hand, and the answer is a finding rather than a gap."""
        for providers in ([], [provider(ability="ability:test-management")]):
            with self.subTest(providers=len(providers)):
                record = resolver.resolve(request(providers=providers))
                self.assertEqual(record["status"], "UNAVAILABLE")
                self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
                self.assertIn("supplied", record["detail"])

    def test_an_explicitly_empty_catalogue_was_read_and_misses(self):
        """`{}` is a published closed list with nothing in it — a thing a caller
        can mean — so it is a catalogue that was READ, and the answer is a
        finding against it rather than a gap about it.

        Read with `bool()` instead of `is not None`, the record contradicted
        itself in this one case: `inputs_missing` named `ability` while the
        refusal above said the ability "is not in the pinned catalogue", which
        is a statement that it was looked up. The CLI will not manufacture this
        input — a file of no names is a mistake, and it refuses rather than
        answering against one — so the case is reachable through the resolver's
        own API, which is where this test puts it.
        """
        record = resolver.resolve(request(abilities={}))
        self.assertEqual(record["status"], "UNAVAILABLE")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertIn("ability", record["inputs_seen"])
        self.assertNotIn("ability", record["inputs_missing"])

    def test_the_provider_set_is_not_a_ninth_input(self):
        """It is named in the detail and nowhere else. `inputs_seen` is the
        field that says how much of the eight-way intersection was evaluated,
        and a ninth entry of a different kind would make it say something else.
        """
        request_without_providers = request()
        del request_without_providers["providers"]
        record = resolver.resolve(request_without_providers)
        self.assertNotIn("providers", record["inputs_seen"])
        self.assertNotIn("provider_set", record["inputs_missing"])
        self.assertEqual(
            len(record["inputs_seen"]) + len(record["inputs_missing"]),
            len(resolver.INPUT_ORDER))
        for name in record["inputs_seen"] + record["inputs_missing"]:
            self.assertIn(name, resolver.INPUT_ORDER)


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


class OperationTests(unittest.TestCase):
    """§10 — `ability:<id>#<verb>`, resolved against the provider that
    implements it.

    The rule this class exists to defend is a NEGATIVE one: a bare reference
    never consults the operation table. If it did, every provider that declares
    no `operations:` would answer UNKNOWN to an ordinary `#read`, and the
    operation axis would have withdrawn capabilities that already worked.
    """

    def test_a_bare_reference_never_consults_the_table(self):
        """The negative control. This provider declares no `operations:` — and
        the bare form is available, because nobody asked about a verb."""
        record = resolver.resolve(request())
        self.assertEqual(record["status"], "AVAILABLE")
        self.assertIsNone(record["operation"])
        self.assertIsNone(record["candidates"][0]["operations_state"])

    def test_a_bare_reference_records_no_state_even_when_a_table_exists(self):
        """A provider that DOES declare a table still gets no state for a bare
        reference. The reader must be able to tell "checked and agreed" from
        "never in question", and inventing SUPPORTED here would erase that."""
        record = resolver.resolve(request(
            providers=[provider(operations=["read", "write"])]))
        self.assertEqual(record["status"], "AVAILABLE")
        self.assertIsNone(record["candidates"][0]["operations_state"])

    def test_a_declared_operation_is_checked_and_recorded(self):
        record = resolver.resolve(request(
            ability=f"{ABILITY}#read",
            providers=[provider(operations=["read", "write"])]))
        self.assertEqual(record["status"], "AVAILABLE")
        self.assertEqual(record["ability"], ABILITY, "the record keeps the bare id")
        self.assertEqual(record["operation"], "read")
        self.assertEqual(record["candidates"][0]["operations_state"], "SUPPORTED")

    def test_a_verb_outside_a_declared_table_is_a_definite_no(self):
        """A closed list that omits the verb has said no. `ACTION_UNSUPPORTED`
        is the word the rest of the product already uses for this, taken from
        `target-contract.yaml`, not invented here."""
        record = resolver.resolve(request(
            ability=f"{ABILITY}#delete",
            providers=[provider(operations=["read", "write"])]))
        self.assertEqual(record["status"], "INCOMPATIBLE")
        self.assertEqual(record["reason"], "ACTION_UNSUPPORTED")
        self.assertEqual(record["candidates"][0]["refused_by"], "operation")
        self.assertEqual(record["candidates"][0]["operations_state"], "UNSUPPORTED")

    def test_no_table_is_unknown_not_a_no(self):
        """`target.py#_support_state`, copied rather than paraphrased: a
        provider that declares no table has NOT been asked. The distinction is
        the one between "nobody looked" and "there is nothing there"."""
        record = resolver.resolve(request(ability=f"{ABILITY}#read"))
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertEqual(record["candidates"][0]["operations_state"], "UNKNOWN")
        self.assertIn("operations table", record["candidates"][0]["detail"])

    def test_an_unasked_operation_does_not_reach_the_records_reason(self):
        """`target-contract.yaml`'s ruling, applied to this record rather than
        re-decided here: an UNEVALUABLE verdict is not a REFUSAL, so the word
        stays on the candidate — the step's own entry — and `record.reason`
        stays null.

        If ACTION_UNSUPPORTED meant "refused" on one record and "nobody asked"
        on another, a consumer switching on `reason` would read the second as
        the first.
        """
        unasked = resolver.resolve(request(ability=f"{ABILITY}#read"))
        self.assertEqual(unasked["status"], "UNKNOWN")
        self.assertIsNone(unasked["reason"])
        self.assertEqual(unasked["candidates"][0]["reason"], "ACTION_UNSUPPORTED")

        # The control: the same word on the same field DOES mean refused when
        # the provider was asked and said no. That is what makes the null above
        # a distinction rather than a gap.
        refused = resolver.resolve(request(
            ability=f"{ABILITY}#delete",
            providers=[provider(operations=["read"])]))
        self.assertEqual(refused["reason"], "ACTION_UNSUPPORTED")
        self.assertEqual(refused["status"], "INCOMPATIBLE")

    def test_a_definite_device_refusal_outranks_an_unasked_operation(self):
        """The design decision in `_evaluate_one`, and the one most likely to
        be "simplified" away: the indefinite answer does NOT return early.

        Nothing is installed here, which is a definite no. Had the unasked
        operation returned immediately, this device would report UNKNOWN — and
        UNKNOWN reads as "we have not looked", hiding the finding that there is
        nothing here at all.
        """
        record = resolver.resolve(request(ability=f"{ABILITY}#read", instances=[]))
        self.assertEqual(record["status"], "UNAVAILABLE")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertEqual(record["candidates"][0]["refused_by"], "device_binding")

    def test_the_catalogue_is_keyed_by_the_bare_ability(self):
        """Naming a verb must not turn a catalogue hit into a catalogue miss —
        a refusal about the catalogue, produced by a question about a verb."""
        record = resolver.resolve(request(
            ability="ability:test-management#read",
            providers=[provider(ability="ability:test-management",
                                operations=["read"])]))
        self.assertEqual(record["status"], "AVAILABLE")
        self.assertEqual(record["ability"], "ability:test-management")
        self.assertEqual(record["operation"], "read")

    def test_the_operation_is_not_a_ninth_input(self):
        """It rides on the reference, so the eight stay eight. A ninth input
        would be one more thing a caller can forget to supply — and this module
        exists because seven unreadable inputs once read as agreement."""
        record = resolver.resolve(request(ability=f"{ABILITY}#read"))
        self.assertEqual(len(resolver.INPUT_ORDER), 8)
        self.assertEqual(len(record["inputs_seen"]) + len(record["inputs_missing"]), 8)

    def test_a_malformed_verb_is_rejected_rather_than_answered(self):
        for bad in ("ability:x#", "ability:x#Read", "ability:x#re_ad",
                    "ability:x#-read", "ability:x#read-", "ability:x#a--b",
                    "ability:x#read#write", "#read"):
            with self.assertRaises(resolver.Rejected, msg=bad):
                resolver.resolve(request(ability=bad))

    def test_the_verb_shape_is_ascii_and_not_whatever_has_a_case(self):
        """`str.islower` is true for `é` and `"²".isdigit()` is true. The
        contract says which characters may appear in an identifier; "happens to
        have a lowercase form" is a different and much larger set."""
        for bad in ("ability:x#café", "ability:x#r²ad", "ability:x#read​"):
            with self.assertRaises(resolver.Rejected, msg=bad):
                resolver.resolve(request(ability=bad))
        self.assertTrue(resolver._is_operation_word("read-write2"))

    def test_the_reference_round_trips_through_its_own_parser(self):
        for ref in (ABILITY, f"{ABILITY}#read", f"{ABILITY}#read-write"):
            ability, operation = resolver.parse_ability_ref(ref)
            self.assertEqual(resolver.ability_ref(ability, operation), ref)

    def test_the_summary_names_the_verb_that_was_asked_about(self):
        """The two answers are allowed to differ, so the line must not be the
        same. A summary that drops `#read` reports a resolution of a question
        the caller did not ask."""
        asked = resolver.summarize(resolver.resolve(request(
            ability=f"{ABILITY}#read",
            providers=[provider(operations=["read"])])))
        bare = resolver.summarize(resolver.resolve(request(
            providers=[provider(operations=["read"])])))
        self.assertIn("#read", asked)
        self.assertNotIn("#read", bare)

    def test_an_unknown_summary_names_the_operation_not_a_missing_input(self):
        """`unknown (all eight inputs)` would send the caller to supply a
        record that would not have changed the answer — the cause is not a
        missing input, it is that nobody was asked."""
        line = resolver.summarize(resolver.resolve(request(ability=f"{ABILITY}#read")))
        self.assertIn("read", line)
        self.assertNotIn("inputs", line)
        # And the bare form of the very same request names no verb at all, so
        # it has no such cause to name.
        bare = resolver.summarize(resolver.resolve(request()))
        self.assertNotIn("#", bare)


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


class RuntimeFeatureTests(unittest.TestCase):
    """§11 — the second half of the agent_runtime input.

    `agents: [claude-code, codex]` could only ever answer "this family exists".
    A provider that needs a feature of the INSTALLATION now says so, and the
    three answers are three different outcomes: available resolves, unavailable
    refuses with the word the provider contract already uses for "the agent side
    cannot", and unknown is UNKNOWN — because writing it as AGENT_UNSUPPORTED
    would say the runtime cannot do it on the strength of nobody having asked.
    """

    @staticmethod
    def binding(**states):
        """A binding record shaped as `abilities/runtime_binding.py` builds one."""
        return {"schema": "serveros/runtime-binding.v1",
                "binding_ref": "binding:device:server-beijing/agent-runtime:claude-code",
                "device_ref": "device:server-beijing",
                "runtime_ref": "agent-runtime:claude-code",
                "runtime_id": "claude-code",
                "observation": "OBSERVED",
                "features": {name: {"state": state} for name, state in states.items()}}

    def needs(self, *features, **over):
        """A provider that requires runtime features, and a binding that answers."""
        states = over.pop("states", None)
        return request(
            providers=[provider(required_runtime_features=list(features))],
            runtime_record=None,
            runtime_binding=self.binding(**(states or {})), **over)

    def test_the_record_the_resolver_reads_is_the_one_the_kernel_built(self):
        """The join, and the reason every test above is not enough on its own.

        The helper above writes the binding record's SHAPE by hand, which proves
        the resolver handles that shape and proves nothing about whether that
        shape is what `abilities/runtime_binding.py` produces. A mutation that
        made `feature()` answer `UNAVAILABLE` where it should say `UNKNOWN` left
        every test in this class green, because the states here are literals in
        a dict rather than answers from the function.

        So this one builds the record the kernel builds — authored declaration,
        authored registry, a real observation — and feeds it straight in. It is
        the only test here that can fail on account of `feature()`.
        """
        spec = importlib.util.spec_from_file_location(
            "serveros_runtime_binding", ROOT / "abilities" / "runtime_binding.py")
        kernel = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(kernel)

        observed = kernel.observation(
            runtime_id="claude-code", observed_at="2026-09-14T00:00:00Z",
            probe="cli-version", version="2.1.234 (Claude Code)", source="test",
            surface="cli", features={"mcp.client": {"state": "AVAILABLE"}})
        record = kernel.binding(
            {"device_ref": "device:server-beijing",
             "runtime_ref": "agent-runtime:claude-code",
             "declared_at": "2026-09-14"},
            observed=observed, reference="2026-09-14T00:10:00Z")

        # The two states the kernel has to produce, not the test.
        self.assertEqual(record["features"]["mcp.client"]["state"], "AVAILABLE")
        self.assertEqual(record["features"]["gui"]["state"], "UNKNOWN")

        answered = resolver.resolve(request(
            providers=[provider(required_runtime_features=["mcp.client"])],
            runtime_record=None, runtime_binding=record))
        self.assertEqual(answered["status"], "AVAILABLE")

        # UNKNOWN is carried, not refused. A verifier that flattened it into
        # UNAVAILABLE would refuse here with AGENT_UNSUPPORTED.
        unobserved = resolver.resolve(request(
            providers=[provider(required_runtime_features=["gui"])],
            runtime_record=None, runtime_binding=record))
        self.assertEqual(unobserved["status"], "UNKNOWN")
        self.assertIsNone(unobserved["reason"])

        # And the same question asked of the OTHER family, which is the half
        # that catches an inference from the name. A `feature()` that answered
        # "codex has a GUI" would make this resolve AVAILABLE — and the test
        # above would still be green, because it never says `codex`.
        codex = kernel.binding(
            {"device_ref": "device:server-beijing",
             "runtime_ref": "agent-runtime:codex", "declared_at": "2026-09-14"},
            observed=kernel.observation(
                runtime_id="codex", observed_at="2026-09-14T00:00:00Z",
                probe="cli-version", version="codex-cli 0.153.4", source="test",
                surface="cli",
                features={"instruction.workspace": {"state": "UNAVAILABLE"}}),
            reference="2026-09-14T00:10:00Z")
        asked_of_codex = resolver.resolve(request(
            providers=[provider(required_runtime_features=["gui"])],
            runtime_record=None, runtime_binding=codex))
        self.assertEqual(asked_of_codex["status"], "UNKNOWN")
        self.assertIsNone(asked_of_codex["reason"])

    def test_a_binding_alone_satisfies_the_runtime_input(self):
        """The binding names its own runtime ref, so the family record is not
        required to have one — and the input must not be recorded as missing."""
        record = resolver.resolve(self.needs("mcp.client", states={"mcp.client": "AVAILABLE"}))
        self.assertEqual(record["status"], "AVAILABLE")
        self.assertIn("agent_runtime", record["inputs_seen"])
        self.assertNotIn("agent_runtime", record["inputs_missing"])

    def test_a_required_feature_that_is_available_resolves(self):
        record = resolver.resolve(
            self.needs("mcp.client", "skill.native",
                       states={"mcp.client": "AVAILABLE", "skill.native": "AVAILABLE"}))
        self.assertEqual(record["status"], "AVAILABLE")

    def test_a_required_feature_that_is_unavailable_refuses(self):
        record = resolver.resolve(
            self.needs("gui", states={"gui": "UNAVAILABLE"}))
        self.assertEqual(record["reason"], "AGENT_UNSUPPORTED")
        candidate = record["candidates"][0]
        self.assertEqual(candidate["refused_by"], "agent_runtime")
        self.assertEqual(candidate["reason"], "AGENT_UNSUPPORTED")

    def test_the_refusal_is_incompatible_because_the_runtime_is_the_blocker(self):
        """`INCOMPATIBLE`, which is the word this resolver already uses one
        plane up for "the runtime cannot consume that".

        It matters that it is not `UNAVAILABLE`: an unavailable provider is one
        to install, and a runtime that cannot hold the feature is not. A
        consumer that read the two as one word would try to fix a working
        provider by reinstalling it.
        """
        record = resolver.resolve(self.needs("gui", states={"gui": "UNAVAILABLE"}))
        self.assertEqual(record["status"], "INCOMPATIBLE")
        self.assertIn(record["status"], resolver.STATUSES)
        self.assertEqual(record["detail"], record["candidates"][0]["detail"])

    def test_an_axis_the_family_lacks_refuses_the_same_way(self):
        record = resolver.resolve(self.needs("app.server", states={"app.server": "INCOMPATIBLE"}))
        self.assertEqual(record["reason"], "AGENT_UNSUPPORTED")

    def test_a_required_feature_nobody_observed_is_unknown_and_not_a_refusal(self):
        record = resolver.resolve(
            self.needs("instruction.workspace",
                       states={"instruction.workspace": "UNKNOWN"}))
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertIsNone(record["reason"],
                          "an unobserved feature was written as a refusal")
        self.assertEqual(record["candidates"][0]["runtime_features_state"], "UNKNOWN")
        self.assertNotIn("agent_runtime", record["inputs_missing"])

    def test_the_unknown_names_the_feature_axis_as_its_cause(self):
        record = resolver.resolve(
            self.needs("instruction.workspace",
                       states={"instruction.workspace": "UNKNOWN"}))
        line = resolver.summarize(record)
        self.assertIn("unknown", line)
        self.assertIn("features", line)
        self.assertNotIn("no inputs", line)

    def test_a_binding_silent_about_the_feature_is_unknown_too(self):
        """Absent from the record and UNKNOWN in it are the same answer here:
        neither established anything about this device."""
        record = resolver.resolve(self.needs("gui", states={}))
        self.assertEqual(record["status"], "UNKNOWN")

    def test_a_definite_refusal_outranks_an_unobserved_feature(self):
        """The intersection property, one axis over: a `no` must not be
        shadowed by a `we did not look`."""
        record = resolver.resolve(self.needs(
            "gui", instances=[], states={"gui": "UNKNOWN"}))
        self.assertEqual(record["status"], "UNAVAILABLE",
                         "an unobserved feature shadowed a definite no")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")

    def test_a_provider_that_requires_no_features_is_unaffected(self):
        record = resolver.resolve(request())
        self.assertEqual(record["status"], "AVAILABLE")
        self.assertIsNone(record["candidates"][0]["runtime_features_state"])

    def test_flipping_unknown_to_unavailable_turns_the_answer_into_a_refusal(self):
        """§19's mutation, at the consumer.

        The two records differ in one field. If the resolver read UNKNOWN as a
        no, "nobody asked" and "it cannot" would be one outcome — and this test
        would fail on the first assertion rather than the second.
        """
        unknown = resolver.resolve(self.needs("gui", states={"gui": "UNKNOWN"}))
        no = resolver.resolve(self.needs("gui", states={"gui": "UNAVAILABLE"}))
        self.assertEqual(unknown["status"], "UNKNOWN")
        self.assertEqual(no["status"], "INCOMPATIBLE")
        self.assertEqual(unknown["reason"], None)
        self.assertEqual(no["reason"], "AGENT_UNSUPPORTED")


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


GRILL_ME = "ability:unknown/grill-me"

# `unknown/grill-me` at the revision the golden sample was enrolled at. The
# values are the manifest's, copied here so this file does not depend on
# ability-management's HEAD: an assertion about what the resolver DOES with a
# declaration must not start failing because somebody edited the declaration.
GRILL_ME_LINK = {
    "product_role": [{
        "primary": "B6",
        "subroles": ["plan-stress-test", "decision-interview"],
        "realization_role": "specialist",
        "cross_refs": ["B3", "B5"],
    }],
    "routing": [{
        "placement": ["design-session"],
        "trigger_hints": ["计划已成文但仍有未决问题"],
        "avoid_hints": ["已有代码上的评审"],
    }],
    "handoff": [{
        "consumes": ["plan-or-raw-idea", "codebase-facts"],
        "produces": ["decision-record", "open-questions", "alignment-confirmation"],
    }],
    "authority": [{
        "owns": [],
        "may_propose": ["design-decision"],
        "human_required": ["decision-answers", "alignment-confirmation"],
    }],
}


def linked(link=GRILL_ME_LINK, **over):
    """A request for one ability, whose catalogue carries a Thin Link."""
    over.setdefault("ability", GRILL_ME)
    over.setdefault("providers", [provider(ability=GRILL_ME)])
    over.setdefault("abilities", {GRILL_ME: dict(link)})
    return request(**over)


class ThinAbilityLinkTests(unittest.TestCase):
    """§14 — the resolver must actually USE the Thin Ability Link.

    Until 2026-10-06 it could not, and the reason was three fields deep rather
    than a missing feature: the manifest declared the link, the projection's
    allow-list did not carry it, the CLI's catalogue values were `{}`, and
    `_input_ability` asked one question of that mapping (is the ability in it).
    Three separate drops, each one sufficient, and every test in this file
    passed throughout — because a resolver that has never seen a declaration
    and one that was never given one produce the same record.

    The two load-bearing tests here are `test_the_link_grants_nothing` and
    `test_a_subrole_does_not_make_an_operation_supported`: they are what keeps
    §18 true, and a later edit that lets a declaration reach `status` or a
    provider's operation table would pass every other test in this class.
    """

    def test_the_thin_link_reaches_the_record(self):
        record = resolver.resolve(linked())
        self.assertEqual(record["product_role_state"], "DECLARED")
        self.assertEqual(record["semantic_owner"], "B6")
        self.assertEqual(record["realization_role"], "specialist")
        self.assertEqual(record["cross_refs"], ["B3", "B5"])
        self.assertEqual(record["subroles"], ["plan-stress-test", "decision-interview"])
        self.assertEqual(record["human_boundary"],
                         ["decision-answers", "alignment-confirmation"])

    def test_a_catalogue_that_never_carried_a_link_is_not_an_orphan(self):
        """The fourth state, and it is the one §12 needs.

        `--catalogue` reads a file of keys and declares nothing about anything;
        every request that supplies only `revision` is in the same position.
        Reporting those as UNDECLARED would manufacture an orphan out of a
        question nobody put, and §12's "no silent orphan" would then be
        satisfied by a resolver that cried wolf on every ability it was asked
        about twice.
        """
        record = resolver.resolve(request())          # the module's own CATALOGUE
        self.assertEqual(record["product_role_state"], "UNKNOWN")
        self.assertIsNone(record["semantic_owner"])
        self.assertIsNone(record["subroles"])
        self.assertIsNone(record["human_boundary"])

        # And with no catalogue at all — the same reading by a different route.
        record = resolver.resolve(request(abilities=None))
        self.assertEqual(record["product_role_state"], "UNKNOWN")

    def test_an_ability_that_declares_no_product_role_is_the_orphan(self):
        """`[]` — carried, and this ability declares nothing. §12's finding."""
        link = {field: [] for field in resolver.THIN_LINK_FIELDS}
        record = resolver.resolve(linked(abilities={GRILL_ME: link}))
        self.assertEqual(record["product_role_state"], "UNDECLARED")
        self.assertIsNone(record["semantic_owner"])

    def test_two_records_of_one_identity_that_disagree_are_not_a_value(self):
        """§6 — a plugin is a distribution identity, not one semantic ability.

        `microsoft/playwright-mcp` is an `mcp` record and a `plugin` record
        under one id, so the catalogue carries a LIST of the distinct
        declarations. Two of them is a question with no answer, and picking the
        last would make `semantic_owner` depend on manifest order.
        """
        first = dict(GRILL_ME_LINK["product_role"][0])
        second = dict(first, primary="B7")
        link = dict(GRILL_ME_LINK, product_role=[first, second])
        record = resolver.resolve(linked(abilities={GRILL_ME: link}))
        self.assertEqual(record["product_role_state"], "AMBIGUOUS")
        self.assertIsNone(record["semantic_owner"])
        self.assertIsNone(record["subroles"])

    def test_the_subrole_is_a_membership_test_and_not_a_match(self):
        """The one place a name is DECIDED. It is decided against a declared
        closed list — the same shape as input 1 and `_operation_state` — and
        never by similarity, which is the line §14 draws where it refuses to
        make the resolver an LLM planner."""
        named = resolver.resolve(linked(ability=f"{GRILL_ME}#plan-stress-test"))
        self.assertEqual(named["subrole"], "plan-stress-test")
        # The verb travels beside the identity, exactly as before §14: naming a
        # subrole must not change which ability was resolved.
        self.assertEqual(named["ability"], GRILL_ME)

        # A verb the ability does not declare as a subrole is NOT one — and it
        # is not a refusal either; the ability's declaration cannot refuse.
        near = resolver.resolve(linked(ability=f"{GRILL_ME}#stress-test-this-idea"))
        self.assertIsNone(near["subrole"])
        self.assertEqual(near["subroles"], ["plan-stress-test", "decision-interview"])

        # Nor is a subrole invented for a bare reference.
        bare = resolver.resolve(linked())
        self.assertIsNone(bare["subrole"])

    def test_a_subrole_does_not_make_an_operation_supported(self):
        """§18, and the symmetric half §18 does not spell out.

        `plan-stress-test` is a responsibility the ability declares it fills.
        Whether a provider IMPLEMENTS it is a different question with a
        different owner, and this provider publishes no operations table — so
        the answer stays UNKNOWN. Letting the declaration answer for the
        provider would transfer an ability's word into a provider's capability,
        and `Thin Link 不授予权限` forbids the transfer in that direction too.
        """
        record = resolver.resolve(linked(ability=f"{GRILL_ME}#plan-stress-test"))
        self.assertEqual(record["subrole"], "plan-stress-test")
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertEqual(record["candidates"][0]["operations_state"], "UNKNOWN")
        self.assertIsNone(record["reason"])

    def test_the_link_grants_nothing(self):
        """The negative control, and the reason the field went on `record`
        rather than into a decision.

        §18 says a Thin Link confers no permission. The record must therefore
        be IDENTICAL under every declaration the catalogue could carry, in
        every field the link does not own — a declaration that moved `status`,
        refused an input or reordered the candidates would be granting
        something, and a declaration that moves nothing is the only kind that is
        safe to read.

        All FOUR states, not a pair, and the reason is the half of §18 that has
        no slogan: a declaration must not DENY either. A rule that only compared
        DECLARED against UNKNOWN would pass an implementation that refused every
        ability declaring no product role — which is precisely the authority a
        link may not have, and the one a well-meaning edit adds when it reads
        «不能 silent orphan» as an instruction to do something about the orphan.
        """
        baseline = resolver.resolve(linked(abilities={GRILL_ME: {"revision": "x"}}))
        untouched = [key for key in baseline
                     if key not in resolver.THIN_LINK_RECORD_FIELDS]
        self.assertTrue(untouched)
        # The two sets PARTITION the record. Without this, a later field could
        # quietly join the excluded side and the loop below would stop covering
        # it — the exclusion list would grow into a hole rather than a scope.
        self.assertEqual(set(baseline) - set(untouched),
                         set(resolver.THIN_LINK_RECORD_FIELDS))
        self.assertEqual(baseline["product_role_state"], "UNKNOWN")

        first = dict(GRILL_ME_LINK["product_role"][0])
        for state, link in (
            ("DECLARED", GRILL_ME_LINK),
            ("UNDECLARED", {field: [] for field in resolver.THIN_LINK_FIELDS}),
            ("AMBIGUOUS", dict(GRILL_ME_LINK,
                               product_role=[first, dict(first, primary="B7")])),
        ):
            with_link = resolver.resolve(linked(abilities={GRILL_ME: link}))
            self.assertEqual(with_link["product_role_state"], state)
            for key in untouched:
                self.assertEqual(with_link[key], baseline[key], f"{state}: {key}")
            # The positive control for the loop above, state by state: the
            # record DID carry the state, so an implementation that dropped the
            # link entirely cannot pass this by making everything equal.
            self.assertNotEqual(with_link["product_role_state"], "UNKNOWN")

    def test_installed_is_three_valued(self):
        """The positive half of input 4, which was never reported before: an
        instance that WAS found left no trace beyond a null `refused_by`, so
        "registered here" and "nobody looked" wore the same face."""
        self.assertTrue(resolver.resolve(linked())["candidates"][0]["installed"])

        absent = resolver.resolve(linked(instances=[]))
        self.assertFalse(absent["candidates"][0]["installed"])
        self.assertEqual(absent["candidates"][0]["refused_by"], "device_binding")

        unasked = resolver.resolve(linked(instances=None))
        self.assertIsNone(unasked["candidates"][0]["installed"])

    def test_why_candidate_names_the_two_tables_it_read(self):
        """Deterministic and complete: it says which filter put the provider in
        the set, and — when a verb was named — which of the two declarations
        answered it. Nothing here ranks or guesses."""
        bare = resolver.resolve(linked())["candidates"][0]["why_candidate"]
        self.assertEqual(bare, f"it declares {GRILL_ME}")

        named = resolver.resolve(
            linked(ability=f"{GRILL_ME}#plan-stress-test"))["candidates"][0]
        # The subrole clause, present because the ABILITY declares it...
        self.assertIn("as a subrole it fills", named["why_candidate"])
        # ...and the provider clause, absent because this provider declares no
        # operations table. Two answers, two owners, and the record keeps them
        # apart rather than letting one stand in for the other.
        self.assertNotIn("this provider declares", named["why_candidate"])

        declaring = resolver.resolve(linked(
            ability=f"{GRILL_ME}#plan-stress-test",
            providers=[provider(ability=GRILL_ME, operations=["plan-stress-test"])],
        ))["candidates"][0]
        self.assertIn("this provider declares plan-stress-test",
                      declaring["why_candidate"])

    def test_the_two_modules_that_cannot_import_each_other_agree(self):
        """`runtime/ability_projection.py` owns the manifest allow-list and
        `abilities/resolver.py` owns the reading, and the resolver's import
        list is closed (`argparse`, `json`, `sys`, `pathlib`) so neither can
        import the other. Without this test a field added to one would be
        silently dropped by the other — which is exactly the three-drop chain
        this whole change exists to close.
        """
        spec = importlib.util.spec_from_file_location(
            "ability_projection_for_link_test",
            ROOT / "runtime" / "ability_projection.py")
        projection = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(projection)
        self.assertEqual(set(projection.THIN_LINK_FIELDS),
                         set(resolver.THIN_LINK_FIELDS))


CONTRACT_PATH = ROOT / "abilities" / "capability-contract.yaml"


def _contract() -> dict:
    import yaml
    return yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8")) or {}


class CapabilityContractTests(unittest.TestCase):
    """The record `capability-contract.yaml` declares, against the one returned.

    Until 2026-10-06 nothing in this tree read that file. It was four lists and
    seven invariants of prose, and because nothing compared it to the code it
    had drifted exactly as far as prose does: four fields the resolver has
    returned for as long as it has existed — `runtime`, `inputs_missing`,
    `detail`, `finding` — appeared in none of its lists, while a fifth, `note`,
    is written by the CLI and by nothing in `resolver.py`. A contract with no
    reader and the code it claims to describe are two documents, and the one
    that is wrong is the one a person reads.

    The teeth are the unions below: every branch the resolver has is driven
    once and the union of what came back is compared against the file. A field
    added to the code without a declaration fails here; a declared field that
    stops being produced is named rather than quietly ignored.
    """

    #: Every branch the resolver can return from, so the union of the keys it
    #: produces is as close to complete as a branch table can make it. Not a
    #: proof — a branch that does not exist yet cannot be driven — which is why
    #: the assertion is on the union rather than on one record: a new field has
    #: to be reachable from somewhere, and the reachable set is what is pinned.
    def _produced(self) -> list[dict]:
        both = {"product_role": [
            {"primary": "B6", "subroles": ["plan-stress-test"],
             "realization_role": "specialist", "cross_refs": ["B3"]}]}
        requests = [
            request(),                                    # AVAILABLE, provider chosen
            request(ability=ABILITY + "#read"),           # a verb was named
            request(abilities=None),                      # input 1 never supplied
            request(abilities={}),                        # supplied, and empty
            request(ability="ability:nope"),              # a catalogue miss
            request(providers=None),                      # the provider set never supplied
            request(providers=[]),                        # supplied, and empty
            request(instances=None),                      # device_binding unread
            request(runtime_record=None),                 # agent_runtime unread
            request(runtime_record=dict(RUNTIME,
                                        consumable_provider_kinds=[])),  # AGENT_UNSUPPORTED
            linked(),                                     # a declared Thin Link
            linked(link={}),                              # present in input 1, declaring nothing
            linked(link={"product_role": []}),            # carried, and declares no role
            linked(link={"product_role": [{"primary": "B6"},
                                          {"primary": "B7"}]}),   # two records disagreeing
            linked(link={"product_role": both}),
        ]
        return [resolver.resolve(r) for r in requests]

    def _produced_keys(self) -> set:
        return set().union(*[set(rec) for rec in self._produced()])

    def test_the_declared_schema_is_the_one_the_resolver_emits(self):
        self.assertEqual(_contract().get("schema"), resolver.SCHEMA)

    def test_every_field_the_resolver_returns_is_declared(self):
        """The direction that was broken: the code wrote fields the file did not
        name. The repair is to declare them, never to stop returning them — a
        record is read by consumers that would lose a fact either way."""
        declared = set(_contract()["record"]["required_fields"]) | \
            set(_contract()["record"]["optional_fields"])
        self.assertEqual(
            self._produced_keys() - declared, set(),
            "the resolver returns a field `capability-contract.yaml` does not "
            "declare; declare it in `record.optional_fields`")

    def test_the_only_declared_field_the_resolver_never_produces_is_note(self):
        """`note` is real, and its writer is named in the file rather than left
        to be rediscovered as a phantom. Every other declared field must be
        reachable, because a declared field nothing produces is a contract
        describing a record that does not exist."""
        declared = set(_contract()["record"]["required_fields"]) | \
            set(_contract()["record"]["optional_fields"])
        undeclared_in_code = declared - self._produced_keys()
        self.assertEqual(undeclared_in_code, {"note"})
        added_by = _contract()["record"].get("added_by") or {}
        self.assertIn("note", added_by,
                      "`note` is never produced by the resolver: the contract "
                      "must name what does produce it, or drop it")

    def test_every_required_field_is_on_every_record(self):
        required = set(_contract()["record"]["required_fields"])
        for rec in self._produced():
            self.assertEqual(required - set(rec), set(),
                             f"{rec.get('ability')}: a required field is absent")

    def test_every_candidate_row_has_exactly_the_declared_shape(self):
        """A table whose rows have different keys cannot be read as a table, and
        three of these fields arrived in this round with nothing to be out of
        date against."""
        rows = [c for rec in self._produced()
                for c in (rec.get("candidates") or [])]
        self.assertTrue(rows, "no branch produced a candidate to check")
        self.assertEqual(set().union(*[set(c) for c in rows]),
                         set(_contract()["record"]["candidate_fields"]))

    def test_no_forbidden_field_reaches_the_record(self):
        """`forbidden_fields` names two shapes. One is mechanically checkable:
        an absolute path. Paths are resolved per device, so a path in the record
        is a path from whichever machine happened to answer."""
        def strings(value):
            if isinstance(value, str):
                yield value
            elif isinstance(value, dict):
                for k, v in value.items():
                    yield from strings(k)
                    yield from strings(v)
            elif isinstance(value, (list, tuple)):
                for item in value:
                    yield from strings(item)
        for rec in self._produced():
            for text in strings(rec):
                self.assertFalse(
                    text.startswith("/"),
                    f"an absolute path reached the record: {text!r}")

    def test_the_declared_vocabulary_is_the_vocabulary_the_resolver_holds(self):
        """Both directions, because the two failures are different failures.

        A value in the code and not in the file is a value a reader of the
        contract does not know exists. A value in the file and not in the code
        is a branch a caller writes for and never reaches. The `assertIn` before
        the comparison is what keeps 'the contract declares nothing' from
        comparing equal to 'the code holds nothing' — an empty list on both
        sides is how a broken gate reads as a passing one.
        """
        vocab = _contract().get("vocabulary") or {}
        for name in ("STATUSES", "REASONS", "OPERATION_STATES",
                     "PRODUCT_ROLE_STATES", "THIN_LINK_FIELDS",
                     "THIN_LINK_RECORD_FIELDS"):
            with self.subTest(name=name):
                held = getattr(resolver, name)
                self.assertTrue(held, f"resolver.{name} is empty")
                self.assertIn(name, vocab,
                              f"the contract declares no {name}")
                self.assertEqual(sorted(vocab[name]), sorted(held),
                                 f"{name} differs between contract and code")

    def test_the_declared_inputs_are_the_inputs_in_evaluation_order(self):
        """Ordered, so compared as a sequence: the reason an input refused is
        the FIRST one that did, and a set comparison would not notice a
        reordering that changed which reason a caller is told."""
        names = [entry.get("name") for entry in (_contract().get("inputs") or [])]
        self.assertEqual(tuple(names), resolver.INPUT_ORDER)


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


class TheCliNamesInputOne(unittest.TestCase):
    """`serverosctl ability resolve` — where input 1 comes from.

    The resolver is pure and takes a catalogue in a request. The CLI is the
    layer that can go and find one, and until now it could not: the verb's own
    docstring said the inputs come from `--request` "and deliberately from
    nowhere else yet", so the shipped example request — which supplies no
    catalogue — came back asserting that its ability was not in the pinned
    catalogue. `ability pack` had refused that exact shape one verb over.

    These run the real entry point against real files, because the defect was
    not in the resolver's logic: it was in what the CLI handed it.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.request_path = self.dir / "request.json"
        self.request_path.write_text(json.dumps({
            "ability": ABILITY,
            "device_ref": "device:server-beijing",
            "runtime": "claude-code",
        }), encoding="utf-8")

    def cli(self, *args):
        return subprocess.run(
            [sys.executable, str(ROOT / "bin" / "serverosctl"),
             "ability", "resolve", "--request", str(self.request_path),
             *args, "--json"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=120)

    def catalogue_file(self, *lines, name="catalogue.txt"):
        path = self.dir / name
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    def test_no_catalogue_named_is_unknown_and_says_how_to_name_one(self):
        """The example request's answer, at the CLI. `inputs_seen: []` and
        `inputs_missing` naming `ability` is a GAP; the sentence that used to be
        printed instead was a finding."""
        proc = self.cli()
        self.assertEqual(proc.returncode, 2, proc.stderr)
        record = json.loads(proc.stdout)
        self.assertEqual(record["status"], "UNKNOWN")
        self.assertIsNone(record["reason"])
        self.assertIn("ability", record["inputs_missing"])
        self.assertNotIn("is not in the pinned catalogue", record.get("detail") or "")
        # The advice, because an UNKNOWN that does not say how to replace
        # itself costs the caller a second run to discover the same gap.
        self.assertIn("--ability-repo", proc.stderr)
        self.assertIn("--catalogue", proc.stderr)

    def test_a_catalogue_of_references_is_read_and_the_gap_shrinks(self):
        path = self.catalogue_file("# a catalogue", f"{ABILITY}")
        proc = self.cli("--catalogue", str(path))
        self.assertEqual(proc.returncode, 2, proc.stderr)
        record = json.loads(proc.stdout)
        self.assertEqual(record["inputs_seen"], ["ability"])
        self.assertNotIn("ability", record["inputs_missing"])
        self.assertEqual(
            len(record["inputs_seen"]) + len(record["inputs_missing"]),
            len(resolver.INPUT_ORDER))
        # The provenance, on the answer: a resolution that says an ability is
        # absent from "the catalogue" and does not say WHICH catalogue cannot
        # be told apart from one computed against a file somebody typed.
        self.assertIn(str(path), record["note"])

    def test_a_catalogue_that_was_read_and_misses_is_a_finding_not_a_gap(self):
        path = self.catalogue_file("ability:something-else")
        proc = self.cli("--catalogue", str(path))
        self.assertEqual(proc.returncode, 1, proc.stderr)
        record = json.loads(proc.stdout)
        self.assertEqual(record["status"], "UNAVAILABLE")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertIn("ability", record["inputs_seen"])

    def test_a_bare_name_in_a_catalogue_file_is_refused(self):
        """The trap this check was written for, measured: the first draft of the
        shipped example catalogue listed `ability:runtime-management` as a NAME,
        which the composing branch prefixed into `ability:ability:runtime-
        management` — a definite catalogue miss produced by one doubled word."""
        path = self.catalogue_file(ABILITY.removeprefix("ability:"))
        proc = self.cli("--catalogue", str(path))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("is not an ability reference", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_a_key_carrying_a_verb_is_refused(self):
        """The catalogue is keyed by the bare ability — the operation is split
        off the reference BEFORE the lookup, which is the whole point of
        `test_the_catalogue_is_keyed_by_the_bare_ability`. A key carrying a verb
        can therefore never be asked for."""
        path = self.catalogue_file(f"{ABILITY}#read")
        proc = self.cli("--catalogue", str(path))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("names an operation", proc.stderr)

    def test_an_empty_catalogue_file_is_refused_rather_than_answered_against(self):
        path = self.catalogue_file("# only a comment")
        proc = self.cli("--catalogue", str(path))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("names no abilities", proc.stderr)
        self.assertEqual(proc.stdout, "")

    def test_a_catalogue_that_cannot_be_read_is_an_answer_and_not_a_crash(self):
        proc = self.cli("--catalogue", str(self.dir / "absent.txt"))
        self.assertEqual(proc.returncode, 2)
        self.assertIn("cannot read", proc.stderr)

    def test_a_manifest_is_read_at_the_revision_that_was_resolved(self):
        """`--ability-repo` + `--ability-rev`: the design's own source for input
        1 (`ability-management manifest, pinned revision`). Asserts the note
        names the RESOLVED full SHA — `--ability-rev` may be a branch, and a
        catalogue that cannot be produced again is not one an answer may cite.
        """
        import yaml
        repo = self.dir / "ability-management"
        repo.mkdir()

        def commit(document):
            (repo / "abilities-manifest.yaml").write_text(
                yaml.safe_dump(document, sort_keys=False))
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                           stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@x.invalid",
                            "-c", "user.name=t", "commit", "-m", "manifest"],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                  check=True, capture_output=True,
                                  text=True).stdout.strip()

        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        sha = commit({"abilities": [{"name": ABILITY.removeprefix("ability:")}]})

        proc = self.cli("--ability-repo", str(repo), "--ability-rev", "HEAD")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        record = json.loads(proc.stdout)
        self.assertEqual(record["inputs_seen"], ["ability"])
        self.assertIn(sha[:12], record["note"])

        # And a revision that does not name an ability is a finding against the
        # catalogue that WAS read — the contrast that keeps the pair apart.
        sha2 = commit({"abilities": [{"name": "something-else"}]})
        proc = self.cli("--ability-repo", str(repo), "--ability-rev", sha2)
        self.assertEqual(proc.returncode, 1, proc.stderr)
        self.assertEqual(json.loads(proc.stdout)["reason"], "PROVIDER_NOT_INSTALLED")

    def test_a_manifest_that_names_one_ability_twice_says_so(self):
        """The canonical manifest lists an ability once per SOURCE, and its own
        header says so: `path = storage locator（name != identity != path）`,
        `跨渠道 ≠ 不同 Ability`. Composing catalogue keys by name therefore
        collapses two entries into one ability, and the collapse is right.

        Doing it SILENTLY is not. Measured at the revision this round pins,
        `fbd0b1a72257`: 56 entries, 51 distinct names. A note reporting only
        `51 abilities` cannot be told apart from five abilities lost on the way
        in — same number, two readings, nothing to choose between them. Both
        counts are printed, so the collapse is visible as a decision.
        """
        import yaml
        repo = self.dir / "ability-management"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        bare = ABILITY.removeprefix("ability:")
        (repo / "abilities-manifest.yaml").write_text(yaml.safe_dump({
            "abilities": [
                {"name": bare, "path": "mcps/x"},
                {"name": bare, "path": "plugins/x"},
                {"name": "another-ability", "path": "skills/y"},
            ]}, sort_keys=False), encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@x.invalid",
                        "-c", "user.name=t", "commit", "-m", "manifest"],
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)

        proc = self.cli("--ability-repo", str(repo), "--ability-rev", "HEAD")
        self.assertEqual(proc.returncode, 2, proc.stderr)
        note = json.loads(proc.stdout)["note"]
        self.assertIn("2 abilities", note)
        self.assertIn("3 manifest entries", note)
        self.assertIn("1 of them names", note)

    def test_a_manifest_carries_the_thin_link_across_the_cli(self):
        """The third of the three drop points, at the boundary where it was.

        The resolver's input is a request, and the CLI is the only thing in the
        product that can compose input 1 from the authority. Every value it
        wrote was `{}`, so the resolver's `_input_ability` — which asks one
        question of that mapping, is the ability in it — was right and
        unreachable. A declaration that stops at the CLI has not been used by
        anything, whatever the resolver does with it.

        The pair below is §6 read back: `browser-automation` is an `mcp` record
        and a `plugin` record under one id, so the catalogue key is written
        twice and each record declares. When they agree the key carries ONE
        declaration; when they disagree it carries two, and the resolver reports
        `AMBIGUOUS` rather than picking by manifest order.
        """
        import yaml
        repo = self.dir / "ability-management"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        bare = ABILITY.removeprefix("ability:")
        role = {"primary": "A7", "subroles": ["browser-e2e"],
                "realization_role": "provider", "cross_refs": ["B1", "B5"]}
        authority = {"owns": [], "may_propose": ["test-result"],
                     "human_required": []}

        def commit(second):
            (repo / "abilities-manifest.yaml").write_text(yaml.safe_dump({
                "abilities": [
                    {"name": bare, "path": "mcps/x", "product_role": dict(role),
                     "authority": dict(authority)},
                    {"name": bare, "path": "plugins/x",
                     "product_role": dict(role, **second),
                     "authority": dict(authority)},
                ]}, sort_keys=False), encoding="utf-8")
            subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                           stdout=subprocess.DEVNULL)
            subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@x.invalid",
                            "-c", "user.name=t", "commit", "-m", "manifest"],
                           check=True, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
            return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                  check=True, capture_output=True,
                                  text=True).stdout.strip()

        sha = commit({})
        proc = self.cli("--ability-repo", str(repo), "--ability-rev", sha)
        record = json.loads(proc.stdout)
        self.assertEqual(record["product_role_state"], "DECLARED")
        self.assertEqual(record["semantic_owner"], "A7")
        self.assertEqual(record["realization_role"], "provider")
        self.assertEqual(record["cross_refs"], ["B1", "B5"])
        # `[]` — declared, and it declares no human boundary. Not `None`, which
        # would say the authority block was never carried: "a provider proposes
        # its own result and no person is required" is a claim, and an unread
        # block is not.
        self.assertEqual(record["human_boundary"], [])

        sha2 = commit({"primary": "B7"})
        proc = self.cli("--ability-repo", str(repo), "--ability-rev", sha2)
        record = json.loads(proc.stdout)
        self.assertEqual(record["product_role_state"], "AMBIGUOUS")
        self.assertIsNone(record["semantic_owner"])
        # Ambiguous about its owner, and still resolvable: the declaration
        # cannot refuse, which is the half of §18 that has no slogan.
        self.assertIn("candidates", record)

    def test_a_file_of_names_answers_unknown_and_not_undeclared(self):
        """`--catalogue` names abilities; it declares nothing about them.

        The two readings must not be collapsed, and this is the one place the
        product can tell them apart: UNDECLARED is a finding against an ability
        (§12's orphan), and manufacturing it out of a text file would make the
        finding unfalsifiable.
        """
        path = self.catalogue_file(f"{ABILITY}")
        record = json.loads(self.cli("--catalogue", str(path)).stdout)
        self.assertEqual(record["product_role_state"], "UNKNOWN")
        self.assertIsNone(record["semantic_owner"])

    def test_the_shipped_example_request_uses_a_reference(self):
        """The example is the product's own claim about how the verb is called,
        and it was the one object in the tree that wrote a bare id. It failed by
        a different route than a wrong answer: the lookup missed a catalogue
        that contains the ability."""
        example = json.loads(
            (ROOT / "abilities" / "requests" / "resolve-example.json")
            .read_text(encoding="utf-8"))
        self.assertTrue(example["ability"].startswith("ability:"),
                        "the example names an ability the way no caller does")

    def test_the_shipped_example_catalogue_matches_the_key_shape_it_declares(self):
        """The workbench state matrix re-runs a row against this file, so a file
        that drifts from the declared key shape turns a green row red — which is
        the check, not an accident."""
        lines = [ln.strip() for ln in
                 (ROOT / "abilities" / "requests" / "catalogue-example.txt")
                 .read_text(encoding="utf-8").splitlines()
                 if ln.strip() and not ln.startswith("#")]
        self.assertTrue(lines)
        for key in lines:
            self.assertTrue(key.startswith("ability:"),
                            f"{key} is not the key shape the contract declares")
            self.assertNotIn("#", key)

    def test_the_catalogue_key_is_the_name_and_not_the_manifest_id(self):
        """The two spellings agree in every other fixture, which is why nothing
        said which one the key was.

        Measured 2026-10-06 against `thin-ability-link-20261006@5e666575`: the
        authority gives a record both an `id` (`ProfessorZhi/runtime-management`)
        and a `name` (`runtime-management`), and `ability-packs.yaml` writes its
        members as `ability:<kebab-id>` — the NAME. `capability-contract.yaml`
        said `ability:<id>` for as long as it has existed, and a reader who took
        `<id>` for the manifest's `id` field would write a catalogue that
        resolves nothing: `ability:unknown/grill-me` comes back with the Thin
        Link unread, while `ability:grill-me` reaches it.

        A manifest whose two fields DIFFER is the only place the choice is
        visible, so this test builds one. It pins the name side so the
        convention cannot flip silently the next time somebody reads the
        contract's vocabulary loosely.
        """
        import yaml
        repo = self.dir / "named"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "abilities-manifest.yaml").write_text(yaml.safe_dump({
            "abilities": [{
                "id": "author/thing",
                "name": "thing",
                "path": "skills/thing",
                "product_role": {"primary": "B6", "subroles": ["plan-stress-test"],
                                 "realization_role": "specialist",
                                 "cross_refs": ["B3"]},
                "authority": {"owns": [], "may_propose": [],
                              "human_required": []},
            }]}, sort_keys=False), encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@x.invalid",
                        "-c", "user.name=t", "commit", "-m", "manifest"],
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        sha = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                             check=True, capture_output=True,
                             text=True).stdout.strip()

        def ask(ref):
            path = self.dir / f"req-{ref.replace('/', '_').replace(':', '-')}.json"
            path.write_text(json.dumps({
                "ability": ref, "device_ref": "device:server-beijing",
                "runtime": "claude-code"}), encoding="utf-8")
            proc = subprocess.run(
                [sys.executable, str(ROOT / "bin" / "serverosctl"),
                 "ability", "resolve", "--request", str(path),
                 "--ability-repo", str(repo), "--ability-rev", sha, "--json"],
                cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, timeout=120)
            return json.loads(proc.stdout)

        by_name = ask("ability:thing")
        self.assertEqual(by_name["product_role_state"], "DECLARED")
        self.assertEqual(by_name["semantic_owner"], "B6")

        by_id = ask("ability:author/thing")
        self.assertEqual(by_id["product_role_state"], "UNKNOWN",
                         "the catalogue key is the `name` field; a ref written "
                         "with the manifest's `id` must miss, and this test is "
                         "what says which of the two the key is")
        self.assertIsNone(by_id["semantic_owner"])


class ScaleFixtureTests(unittest.TestCase):
    """§21 — a synthetic catalogue of 500 abilities, and what only shows at that size.

    The Thin Link was proven on two golden samples and a handful of fixtures,
    and every one of those is small enough that three failure modes are
    invisible in it:

      * an O(N^2) walk over the catalogue — 4 abilities and 500 cost the same;
      * a per-name special case — with two names in play, "handled specially"
        and "handled generally" produce the same answer;
      * a collapse that depends on manifest ORDER — the de-duplication in
        `runtime/cli.py::_distinct_declarations` is the only place two records
        of one identity are compared, and with one record per name there is
        nothing for order to decide.

    The third is the one this fixture is built around, because it is the only
    one of the three that is silent rather than slow. A resolver whose
    `semantic_owner` changed because a file was re-sorted would keep passing
    every small test in this file, including
    `test_a_manifest_carries_the_thin_link_across_the_cli`.

    What it does NOT prove, said plainly so the next reader does not take more
    from a green than is here: 505 records is not a load test. It does not
    measure the resolver's cost, and it does not exercise the authority's own
    ingestion path — that is `ability-management`'s, and its scale fixtures
    live there.

    The two golden samples are placed at the two ENDS rather than next to each
    other, so a walk that is correct for a prefix and wrong after the first
    collision cannot pass by accident.

    Measured 2026-10-06 against `thin-ability-link-20261006@5e666575`: the
    declarations below are the ones the authority actually ships for these two
    names — `grill-me` primary B6 with subroles `plan-stress-test` /
    `decision-interview` cross-referencing B3/B5, and `playwright-mcp` primary
    A7 cross-referencing B1/B5.
    """

    FILLERS = 500

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)
        self.request_path = self.dir / "request.json"
        self.request_path.write_text(json.dumps({
            "ability": "ability:grill-me",
            "device_ref": "device:server-beijing",
            "runtime": "claude-code",
        }), encoding="utf-8")

    # The record shape is `runtime/ability_projection.py`'s, trimmed to the
    # four fields `_named_catalogue` reads: `name` composes the key, and the
    # three Thin Link fields are what the resolver reports back.
    def _record(self, name, product_role=None, authority=None, path=None):
        record = {"id": f"ProfessorZhi/{name}", "name": name,
                  "path": path or f"skills/{name}"}
        if product_role is not None:
            record["product_role"] = product_role
        if authority is not None:
            record["authority"] = authority
        return record

    def _manifest(self, reverse=False):
        """500 filler abilities, then the four names that carry the questions.

        Order is the parameter rather than a second fixture, because the only
        thing the reversed copy is FOR is to be compared against this one.
        """
        records = [
            self._record(
                f"scale-{i:04d}",
                # A distinct declaration per filler, so the collapse is asked to
                # compare 500 different things rather than 500 copies of one —
                # a marker-keyed dedup that hashed the wrong field would look
                # correct against identical records.
                product_role={"primary": "B6" if i % 2 else "A6",
                              "subroles": [f"filler-{i:04d}"],
                              "realization_role": "specialist",
                              "cross_refs": ["B3"]},
                authority={"owns": [], "may_propose": [], "human_required": []},
            )
            for i in range(self.FILLERS)
        ]
        records += [
            # One name, two records, declarations that DISAGREE. This is the
            # only input on which `_distinct_declarations` returns two elements,
            # and therefore the only one that can produce AMBIGUOUS.
            self._record("hover-thing", product_role={
                "primary": "A7", "subroles": [], "realization_role": "provider",
                "cross_refs": []}),
            self._record("hover-thing", product_role={
                "primary": "A6", "subroles": [], "realization_role": "provider",
                "cross_refs": []}, path="plugins/hover-thing"),
            # Two records that say the SAME thing in a different key order. They
            # are one declaration, and the only reason `_distinct_declarations`
            # can tell is that its marker is a canonical rendering — the
            # `sort_keys=True` that a reader would otherwise delete as noise.
            # Drop it and this name becomes AMBIGUOUS, which is the whole test.
            self._record("same-thing", product_role={
                "primary": "A7", "realization_role": "provider",
                "cross_refs": [], "subroles": []}),
            self._record("same-thing", product_role={
                "subroles": [], "cross_refs": [], "realization_role": "provider",
                "primary": "A7"}, path="plugins/same-thing"),
            # A name the authority declares nothing about. It must read
            # UNDECLARED — an ability that has no product role — and not
            # UNKNOWN, which is the word for a catalogue that was never asked.
            self._record("quiet-thing"),
            # The two golden samples, at the two ends of the special block.
            self._record("grill-me", product_role={
                "primary": "B6",
                "subroles": ["plan-stress-test", "decision-interview"],
                "realization_role": "specialist",
                "cross_refs": ["B3", "B5"]},
                authority={"owns": [], "may_propose": [], "human_required": []}),
            self._record("playwright-mcp", product_role={
                "primary": "A7", "subroles": ["browser-e2e"],
                "realization_role": "provider",
                "cross_refs": ["B1", "B5"]},
                authority={"owns": [], "may_propose": [], "human_required": []}),
        ]
        if reverse:
            records.reverse()
        return {"abilities": records}

    def _pinned(self, tag, reverse=False):
        """A git repo holding one manifest, and the SHA the CLI must resolve."""
        import yaml
        repo = self.dir / tag
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        (repo / "abilities-manifest.yaml").write_text(
            yaml.safe_dump(self._manifest(reverse=reverse), sort_keys=False),
            encoding="utf-8")
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@x.invalid",
                        "-c", "user.name=t", "commit", "-m", "manifest"],
                       check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL)
        return repo, subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                                    check=True, capture_output=True,
                                    text=True).stdout.strip()

    def _ask(self, ref, repo, sha, tag=""):
        path = self.dir / f"req-{tag or ref.replace('/', '_').replace(':', '-')}.json"
        path.write_text(json.dumps({
            "ability": ref, "device_ref": "device:server-beijing",
            "runtime": "claude-code"}), encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "bin" / "serverosctl"),
             "ability", "resolve", "--request", str(path),
             "--ability-repo", str(repo), "--ability-rev", sha, "--json"],
            cwd=str(ROOT), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, timeout=300)
        # The verb's exit convention, which is why this is not `== 2`: `1` is a
        # record carrying a definite finding (a catalogue miss, say) and `2` is
        # a gap or a refusal. Both print a record; a crash prints neither.
        self.assertIn(proc.returncode, (1, 2), proc.stderr)
        self.assertTrue(proc.stdout.strip(), "no record was printed")
        return json.loads(proc.stdout)

    def test_the_two_golden_samples_survive_a_catalogue_of_500(self):
        repo, sha = self._pinned("natural")
        first = self._ask("ability:grill-me", repo, sha)
        self.assertEqual(first["product_role_state"], "DECLARED")
        self.assertEqual(first["semantic_owner"], "B6")
        self.assertEqual(first["realization_role"], "specialist")
        self.assertEqual(first["subroles"], ["plan-stress-test", "decision-interview"])
        self.assertEqual(first["cross_refs"], ["B3", "B5"])

        # The last special name, 500 fillers away from the first lookup. If the
        # walk were correct only up to the first collision this is where it
        # would come apart, and nowhere earlier.
        last = self._ask("ability:playwright-mcp", repo, sha)
        self.assertEqual(last["product_role_state"], "DECLARED")
        self.assertEqual(last["semantic_owner"], "A7")
        self.assertEqual(last["cross_refs"], ["B1", "B5"])

    def test_a_subrole_is_matched_at_500_as_it_is_at_two(self):
        """The subrole test is a MEMBERSHIP test, and membership is the thing a
        scale fixture can most easily break by accident — a `startswith`, a
        substring, a hash-prefix comparison all agree with equality on small
        inputs and diverge at 500."""
        repo, sha = self._pinned("natural")
        hit = self._ask("ability:grill-me#plan-stress-test", repo, sha)
        self.assertEqual(hit["subrole"], "plan-stress-test")
        # `plan` is a PREFIX of a declared subrole and a substring of it, and it
        # is not one. A membership test written as `any(op in s for s in
        # subroles)` agrees with equality on every fixture in this file that
        # names a whole subrole, and disagrees here.
        for near_miss in ("plan", "plan-stress", "stress-test"):
            self.assertIsNone(
                self._ask(f"ability:grill-me#{near_miss}", repo, sha)["subrole"],
                f"{near_miss!r} is not a subrole of grill-me, only part of one")
        # And a name that is not a subrole of anything near it.
        self.assertIsNone(self._ask("ability:grill-me#filler-0000", repo, sha)["subrole"])

    def test_one_identity_declared_twice_is_ambiguous_and_settles_nothing(self):
        """The disagreement is REPORTED, not resolved — §18's symmetric half.
        Two records of one identity must not be collapsed by picking one, and
        the ability must still resolve as an ability."""
        repo, sha = self._pinned("natural")
        record = self._ask("ability:hover-thing", repo, sha)
        self.assertEqual(record["product_role_state"], "AMBIGUOUS")
        self.assertIsNone(record["semantic_owner"],
                          "a disputed owner is not an owner; picking one would "
                          "make the answer depend on manifest order")

    def test_a_name_the_authority_declares_nothing_about_is_undeclared(self):
        """UNDECLARED is a finding about the ability; UNKNOWN is a finding about
        the QUESTION. The two must not merge at scale, where a catalogue has
        plenty of both."""
        repo, sha = self._pinned("natural")
        record = self._ask("ability:quiet-thing", repo, sha)
        self.assertEqual(record["product_role_state"], "UNDECLARED")
        self.assertIsNone(record["semantic_owner"])
        # The control, one field over: the note proves a catalogue WAS read, so
        # UNDECLARED cannot be UNKNOWN wearing the wrong word.
        self.assertIn("abilities", record["note"])

    def test_the_note_counts_500_and_says_how_many_records_collapsed(self):
        """The two-number note, at the size where the second number matters.

        A reader shown only `501` cannot tell a de-duplication from five
        abilities lost on the way in. `hover-thing` is the one name that is two
        records, so the two numbers must differ by exactly one — and the pair
        must be produced from the manifest, not asserted beside it."""
        repo, sha = self._pinned("natural")
        note = self._ask("ability:grill-me", repo, sha)["note"]
        # 500 fillers, then hover-thing, same-thing, quiet-thing, grill-me and
        # playwright-mcp: six more names. Two of them are written twice.
        self.assertIn(f"{self.FILLERS + 5} abilities", note)
        self.assertIn(f"{self.FILLERS + 7} manifest entries", note)
        self.assertIn("2 of them name an ability another one already names", note)

    def test_two_records_that_differ_only_in_key_order_are_one_declaration(self):
        """The canonical marker, tested where it is the ONLY thing that decides.

        Both records of `same-thing` declare the same product_role; one was
        written in a different key order. Equality alone would not survive that
        (dicts compare equal, but the marker is a JSON string), so the marker is
        rendered with `sort_keys=True`. Remove it and this name turns AMBIGUOUS
        — a disagreement manufactured out of a re-serialisation."""
        repo, sha = self._pinned("natural")
        record = self._ask("ability:same-thing", repo, sha)
        self.assertEqual(record["product_role_state"], "DECLARED")
        self.assertEqual(record["semantic_owner"], "A7")

    def test_the_answer_does_not_depend_on_the_order_of_the_manifest(self):
        """The load-bearing assertion of this fixture.

        Same records, same SHAs-of-content, opposite order. Every field the
        resolver reports must be identical — including the ambiguous one, whose
        whole point is that it is reported rather than decided. A collapse that
        picked `[-1]` instead of collecting distinct declarations passes every
        other test in this file and fails here.
        """
        natural_repo, natural_sha = self._pinned("natural")
        reversed_repo, reversed_sha = self._pinned("reversed", reverse=True)
        self.assertNotEqual(natural_sha, reversed_sha,
                            "the two fixtures must be different trees, or this "
                            "compares a tree against itself")
        for ref in ("ability:grill-me", "ability:playwright-mcp",
                    "ability:hover-thing", "ability:same-thing",
                    "ability:quiet-thing",
                    "ability:scale-0000", "ability:scale-0499"):
            forward = self._ask(ref, natural_repo, natural_sha)
            backward = self._ask(ref, reversed_repo, reversed_sha)
            for field in ("product_role_state", "semantic_owner",
                          "realization_role", "cross_refs", "subroles",
                          "subrole", "human_boundary"):
                self.assertEqual(
                    forward[field], backward[field],
                    f"{ref}.{field} changed when the manifest was re-sorted")

    def test_two_runs_of_one_request_agree(self):
        """Determinism, on the name with something to be non-deterministic
        about. `hover-thing` is the ambiguous one; an implementation that
        settled the disagreement by picking a record would still be stable
        within a run and could differ across runs."""
        repo, sha = self._pinned("natural")
        twice = [json.dumps(self._ask("ability:hover-thing", repo, sha), sort_keys=True)
                 for _ in range(2)]
        self.assertEqual(twice[0], twice[1])

    def test_an_ability_absent_from_500_is_a_finding_not_a_gap(self):
        """The other side of scale: a big catalogue is not a permissive one.
        Absence from a published list of 500 is a definite no."""
        repo, sha = self._pinned("natural")
        record = self._ask("ability:not-here", repo, sha)
        self.assertEqual(record["status"], "UNAVAILABLE")
        self.assertEqual(record["reason"], "PROVIDER_NOT_INSTALLED")
        self.assertNotIn("ability", record["inputs_missing"])


if __name__ == "__main__":
    unittest.main()
