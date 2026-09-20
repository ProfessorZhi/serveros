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


if __name__ == "__main__":
    unittest.main()
