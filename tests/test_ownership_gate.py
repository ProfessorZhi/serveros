#!/usr/bin/env python3
"""C2 §五 · Ownership / protected mutation gate, the five mutation tests.

WHY THIS FILE EXISTS
--------------------
`network-contract.yaml` declared an `ownership` input to its compose step and
said what it means, and nothing produced it. `network_mutation.run()` called
`preflight(plan_record, policy=policy)` with the argument absent, `compose()`
read the parameter, and the only caller that ever filled it in was a test in
`tests/test_network_policy.py`. That is this repository's most-repeated
pathology — a declaration running ahead of the code that would satisfy it, in
a position where nothing fails when it is missing — and it is the reason this
file exists rather than a paragraph in a report.

THE ARRANGEMENT, WHICH IS THE POINT
-----------------------------------
Every mutation below is a pair: the condition that must REFUSE, and the same
subject one field away with that condition removed, which must be ADMITTED. A
gate that refuses everything and a gate that refuses nothing produce identical
evidence from the refusal half alone, and the half that is easy to write is
the one that cannot tell them apart.

Two properties are asserted directly rather than inferred:

  * the actuator is never entered. `network_mutation.execute` refuses on its
    own reading of the verdict, and the Recorder proves nothing was run — and
    the fake host's docker listing is compared before and after, because a
    `runner.calls == []` assertion alone would pass against an executor that
    reached the daemon by some other road.

  * `ownership_gate` imports no mutating verb. It is read here from its own
    source, so a future edit that adds one turns this red instead of being
    caught by a reviewer who is skimming a diff.

WHAT IS DELIBERATELY NOT TESTED HERE
------------------------------------
The container adapter (`resolve_container`) is exercised through the real
producers — `containers.evidence`, `containers.inspect_all`,
`containers.attribute`, `hygiene.Hygiene.classify_container` — rather than
against a dict written in this file. A hand-written shape agrees with the test
that wrote it, and this repository has already paid for that lesson once.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load("serveros_runtime_ownership_gate", "ownership_gate.py")
hygiene_mod = _load("serveros_runtime_hygiene", "hygiene.py")
containers = _load("serveros_runtime_containers", "containers.py")
probes_mod = _load("serveros_runtime_probes", "probes.py")
mutation_mod = _load("serveros_runtime_network_mutation", "network_mutation.py")

CONTRACT = yaml.safe_load(
    (RUNTIME / "ownership-contract.yaml").read_text(encoding="utf-8"))

CONTEXT = {"context_ref": "ctx:test", "actor_ref": "actor:test"}
SUBJECT = {"kind": "container", "name": "some-container"}
MANAGED = {"class": "MANAGED", "confidence": "high",
           "because": "the owner declared itself", "decided_by": "serveros.owner label"}


def managed(**overrides) -> dict:
    return {**MANAGED, **overrides}


def evaluate(**overrides) -> dict:
    """Evaluate a subject whose facts are given directly.

    `resolved` rather than live resolution, because these five mutations are
    about the gate's refusals and not about this host. The adapter that fills
    `resolved` from real producers is tested separately, below, against those
    producers.

    `evidence=` lands inside `resolved`, because that is where `evaluate` reads
    it from — `resolved.get("evidence")`. Offering it as a top-level keyword
    is a shape the product does not have, and a test that spoke it would be
    testing its own vocabulary.
    """
    resolved = {"owner": managed(), "protection": False, "classification": None}
    resolved.update(overrides.pop("resolved", None) or {})
    if "evidence" in overrides:
        resolved["evidence"] = overrides.pop("evidence")
    kwargs = {
        "subject": SUBJECT,
        "action": {"kind": "additive"},
        "context": CONTEXT,
        "resolved": resolved,
    }
    kwargs.update(overrides)
    return gate.evaluate(**kwargs)


# ---------------------------------------------------------------------------
# §C2.5 · the five mutations, each with its positive control
# ---------------------------------------------------------------------------

class TheFiveMutations(unittest.TestCase):

    # -- 1 ------------------------------------------------------------------
    def test_unknown_owner_is_denied(self):
        result = evaluate(resolved={"owner": {"class": "UNKNOWN",
                                              "because": "no decisive source fired"},
                                    "protection": False})
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "OWNER_UNKNOWN")
        self.assertFalse(result["eligible"])

    def test_the_same_subject_with_a_named_owner_is_admitted(self):
        """The control. Without it, `test_unknown_owner_is_denied` passes
        against a gate that returns DENY for everything."""
        self.assertEqual(evaluate()["decision"], "ALLOW")
        self.assertTrue(evaluate()["eligible"])

    # -- 2 ------------------------------------------------------------------
    def test_an_external_owner_is_denied(self):
        result = evaluate(resolved={
            "owner": {"class": "EXTERNAL", "confidence": "high",
                      "because": ("the working directory is under another "
                                  "user's home"),
                      "decided_by": "cgroup"},
            "protection": False})
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "OWNER_EXTERNAL")
        self.assertFalse(result["eligible"])

    def test_being_managed_rather_than_external_is_the_control(self):
        self.assertEqual(evaluate()["decision"], "ALLOW")

    # -- 3 ------------------------------------------------------------------
    def test_a_protected_subject_refuses_a_destructive_action(self):
        result = evaluate(action={"kind": "destructive", "tier": "STOP"},
                          resolved={"owner": managed(), "protection": True,
                                    "classification": "PROTECTED"})
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "PROTECTED_SUBJECT")
        self.assertFalse(result["eligible"])

    def test_a_protected_subject_needs_approval_for_an_additive_action(self):
        """The other half of §C2.1's PROTECTED row, and the only producer of
        REQUIRE_APPROVAL in the product. Creating on a protected subject
        changes it and destroys nothing — which is the whole difference from
        the test above, and it is why one is a refusal and the other is a
        question."""
        result = evaluate(action={"kind": "additive"},
                          resolved={"owner": managed(), "protection": True,
                                    "classification": "PROTECTED"})
        self.assertEqual(result["decision"], "REQUIRE_APPROVAL")
        self.assertEqual(result["code"], "PROTECTED_SUBJECT")
        self.assertTrue(result["eligible"], "an approval is not a refusal")

    def test_an_unprotected_subject_is_the_control_for_both(self):
        destructive = evaluate(action={"kind": "destructive", "tier": "STOP"},
                               resolved={"owner": managed(), "protection": False,
                                         "classification": "STALE_CANDIDATE"})
        self.assertEqual(destructive["decision"], "ALLOW")
        self.assertEqual(evaluate()["decision"], "ALLOW")

    def test_undeclared_protection_is_reported_as_undeclared_and_not_as_denied(self):
        """`protection.resolve` returns True, False or None, and None is not
        False. The decision is the same and the RECORD is not: an undeclared
        protection rendered as "not protected" is a bypass performed by a
        default value."""
        result = evaluate(resolved={"owner": managed(), "protection": None})
        self.assertEqual(result["decision"], "ALLOW")
        self.assertIsNone(result["protection"]["protected"])
        self.assertFalse(result["protection"]["declared"])
        self.assertIn("undeclared", result["reason"])

    # -- 4 ------------------------------------------------------------------
    def test_no_trusted_context_is_denied(self):
        result = evaluate(context=None)
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "NO_TRUSTED_CONTEXT")

    def test_a_context_with_no_actor_is_not_a_context(self):
        result = evaluate(context={"context_ref": "ctx:test"})
        self.assertEqual(result["code"], "NO_TRUSTED_CONTEXT")

    def test_presenting_a_context_is_the_control(self):
        self.assertEqual(evaluate(context=CONTEXT)["decision"], "ALLOW")

    # -- 5 ------------------------------------------------------------------
    def test_locator_only_evidence_cannot_become_an_authoritative_owner(self):
        """§C2.2, all seven locators, each on its own.

        The contract enumerates them because the list IS the rule: every one
        is a string that correlates with an owner, and each has been mistaken
        for evidence on this host at least once. A container called
        `redis-cache-2` is a container called that.
        """
        for locator in gate.locator_evidence():
            with self.subTest(locator=locator):
                result = evaluate(
                    resolved={"owner": managed(), "protection": False},
                    evidence=[{"source": locator, "value": "looks-convincing"}])
                self.assertEqual(result["decision"], "DENY")
                self.assertIn("WEAK_EVIDENCE_ONLY", result["codes"])
                self.assertEqual(result["code"], "WEAK_EVIDENCE_ONLY")
                self.assertEqual(result["owner"]["class"], "UNKNOWN")

    def test_one_decisive_source_is_the_control(self):
        for decisive in gate.decisive_evidence():
            with self.subTest(decisive=decisive):
                result = evaluate(
                    resolved={"owner": managed(), "protection": False},
                    evidence=[{"source": decisive, "value": "v"}])
                self.assertEqual(result["decision"], "ALLOW")

    def test_a_locator_beside_a_decisive_source_still_corroborates(self):
        """§C2.2's second sentence: corroboration may raise confidence in an
        owner a decisive source already established. It may not establish
        one — and it does not veto one either."""
        result = evaluate(
            resolved={"owner": managed(), "protection": False},
            evidence=[{"source": "docker-label", "value": "serveros.owner"},
                      {"source": "port", "value": "8080"}])
        self.assertEqual(result["decision"], "ALLOW")
        self.assertEqual(result["evidence_strength"], "decisive")

    def test_an_unrecognised_source_is_not_treated_as_proof_by_omission(self):
        """A source that is neither decisive nor a declared locator counts
        toward the weak side. The alternative — treating any string that is
        not on a list of non-proofs as proof — is how a name becomes a title."""
        result = evaluate(
            resolved={"owner": managed(), "protection": False},
            evidence=[{"source": "vibes", "value": "it looked like ours"}])
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "WEAK_EVIDENCE_ONLY")


# ---------------------------------------------------------------------------
# §C2.3 · the kinds that cannot be attributed are refused by construction
# ---------------------------------------------------------------------------

class TheKindsWithNoDecisiveSource(unittest.TestCase):
    """`resource_kinds` records `decisive_source: ABSENT` for a tunnel and for
    BuildKit's cache, and ABSENT is not "unimplemented". No decisive source
    exists for those kinds in this product, so every subject of one is UNKNOWN
    and every mutation of one is DENY — and the refusal is a property of the
    declaration rather than of a code path somebody has not written yet."""

    def test_a_tunnel_and_a_builder_cache_are_refused_for_both_action_kinds(self):
        for kind in ("tunnel", "builder_cache"):
            for action in ({"kind": "additive"},
                           {"kind": "destructive", "tier": "PRUNE"}):
                with self.subTest(kind=kind, action=action["kind"]):
                    result = gate.evaluate(
                        subject={"kind": kind, "name": "whatever"},
                        action=action, context=CONTEXT,
                        resolved={"owner": managed(), "protection": False})
                    self.assertEqual(result["decision"], "DENY")
                    self.assertEqual(result["code"], "NO_DECISIVE_SOURCE")
                    self.assertEqual(result["kind_decisive_source"], "ABSENT")

    def test_a_declared_kind_is_the_control(self):
        self.assertNotEqual(evaluate()["code"], "NO_DECISIVE_SOURCE")
        self.assertEqual(gate.kind_decisive_source("container"), "PRESENT")

    def test_the_absent_kinds_are_the_ones_the_contract_marks_absent(self):
        """Read from the contract, not restated. A second copy of this list in
        Python would agree with it until the day one of them was edited."""
        declared = sorted(k for k, v in gate.resource_kinds().items()
                          if v.get("decisive_source") == "ABSENT")
        self.assertEqual(declared, ["builder_cache", "tunnel"])

    def test_a_subject_with_no_kind_is_malformed_rather_than_unknown(self):
        """A subject that cannot be classified cannot be refused for a
        nameable reason either, which is a different output and a different
        repair."""
        for subject in ({}, {"kind": "spaceship"}, {"kind": None}):
            with self.subTest(subject=subject):
                result = evaluate(subject=subject)
                self.assertEqual(result["code"], "SUBJECT_MALFORMED")

    def test_an_action_kind_the_gate_cannot_weigh_is_rejected_not_guessed(self):
        with self.assertRaises(gate.Rejected):
            evaluate(action={"kind": "whatever"})
        with self.assertRaises(gate.Rejected):
            evaluate(action={"kind": "destructive"})
        with self.assertRaises(gate.Rejected):
            evaluate(action={"kind": "destructive", "tier": "DELETE_EVERYTHING"})


# ---------------------------------------------------------------------------
# §C2.3 · six resource kinds, one gate
# ---------------------------------------------------------------------------

class TheSixKindsOneGate(unittest.TestCase):
    """container, network, volume, process, tunnel, builder_cache — and not six
    frameworks.

    `resolve` dispatches once and the same `evaluate` decides for every kind;
    the only per-kind thing in the product is which evidence exists. Where it
    is declared ABSENT the refusal is a property of that declaration, and where
    it exists but was not gathered the answer is UNKNOWN — fail-closed in both
    directions, and distinguishable, because "nobody wrote an evidence source
    for this kind" and "this call did not ask for one" are repaired differently.
    """

    def _inspect(self, name: str) -> dict:
        return {"Id": "a" * 64, "Name": f"/{name}",
                "Config": {"Image": "example:latest", "Labels": {}},
                "State": {"Status": "running", "Running": True},
                "NetworkSettings": {"Ports": {}}, "Mounts": [], "HostConfig": {}}

    def _empty_registry(self) -> dict:
        return {"registry_present": True, **containers.build_registry_index({})}

    def _resolve(self, kind: str) -> dict:
        subject = {"kind": kind, "name": "whatever"}
        if kind == "container":
            return gate.resolve_container(subject=subject,
                                          registry=self._empty_registry(),
                                          inspected=self._inspect("whatever"))
        return gate.resolve(subject=subject, registry=self._empty_registry())

    def test_every_declared_kind_is_answered_by_the_same_evaluate(self):
        declared = gate.resource_kinds()
        # The host kinds, pinned, because a vocabulary that drifts silently is
        # how a gate starts answering about things nobody declared it for.
        for kind in ("builder_cache", "container", "network", "process",
                     "tunnel", "volume"):
            self.assertIn(kind, declared)
        # And the declared set is that plus exactly one entry that is not a
        # thing on the host at all. Named here rather than left as "len > 6",
        # because the point of the extension is that it was a decision: C3's
        # subject is a projection, the gate refused it as SUBJECT_MALFORMED,
        # and widening this table is how that refusal became an ownership
        # answer instead of a vocabulary gap.
        self.assertEqual(sorted(set(declared) - {
            "builder_cache", "container", "network", "process", "tunnel",
            "volume"}), ["projection"])
        self.assertTrue(declared["projection"].get("through"),
                        "an added kind must still say where its evidence comes from")
        for kind, block in sorted(declared.items()):
            with self.subTest(kind=kind):
                resolved = self._resolve(kind)
                verdict = gate.evaluate(subject={"kind": kind, "name": "whatever"},
                                        action={"kind": "additive"},
                                        context=CONTEXT, resolved=resolved)
                self.assertEqual(verdict["decision"], "DENY")
                if block["decisive_source"] == "ABSENT":
                    self.assertEqual(verdict["code"], "NO_DECISIVE_SOURCE")
                    self.assertEqual(verdict["kind_decisive_source"], "ABSENT")
                else:
                    self.assertEqual(verdict["code"], "OWNER_UNKNOWN")
                    self.assertEqual(resolved["owner"]["class"], "UNKNOWN")

    def test_the_one_gate_is_not_six_refusals(self):
        """The control for the loop above: the same call, the same code path,
        with a registration that names the owner. One kind is enough to show
        the gate admits at all, and the dispatch is the thing under test — not
        a per-kind special case that happens to answer."""
        document = {"resources": [{
            "id": "c2-fixture", "kind": "runtime",
            "owner": {"ref": "work-item:C2-FIXTURE", "type": "work-item"},
            "protection": {"level": "normal"},
            "lifecycle": {"class": "service"},
            "probes": [{"container": "whatever"}]}]}
        resolved = gate.resolve_container(
            subject={"kind": "container", "name": "whatever"},
            registry={"registry_present": True,
                      **containers.build_registry_index(document)},
            inspected=self._inspect("whatever"))
        verdict = gate.evaluate(subject={"kind": "container", "name": "whatever"},
                                action={"kind": "additive"}, context=CONTEXT,
                                resolved=resolved)
        self.assertEqual(verdict["decision"], "ALLOW")
        self.assertEqual(resolved["owner"]["decided_by"], "registry-match")

    def test_an_undeclared_kind_falls_through_the_same_dispatcher(self):
        """The one thing that IS different per kind is whether the dispatcher
        knows it, and a kind it does not know says so rather than being
        refused as though it had been weighed."""
        resolved = gate.resolve(subject={"kind": "spaceship", "name": "x"},
                                registry=self._empty_registry())
        self.assertFalse(resolved["found"])
        self.assertIn("not a resource kind", resolved["why"])


# ---------------------------------------------------------------------------
# §C2.1 · the fold, and why `eligible` is not called `allowed`
# ---------------------------------------------------------------------------

class TheTierFold(unittest.TestCase):

    def test_ownership_admits_and_the_tier_policy_still_refuses(self):
        """`eligible: true` says the subject got past ownership. It is not a
        permission, and this is the case that shows why the weaker word was
        chosen: a STOP is ALLOW_UNDER_POLICY and a REMOVE_VOLUME is
        NEEDS_APPROVAL, and both are reached only after ownership admitted."""
        stop = evaluate(action={"kind": "destructive", "tier": "STOP"},
                        resolved={"owner": managed(), "protection": False,
                                  "classification": "STALE_CANDIDATE"})
        self.assertEqual(stop["decision"], "ALLOW")
        self.assertEqual(stop["tier_policy"]["decision"], "ALLOW_UNDER_POLICY")

        volume = evaluate(action={"kind": "destructive", "tier": "REMOVE_VOLUME"},
                          resolved={"owner": managed(), "protection": False,
                                    "classification": "STALE_CANDIDATE"})
        self.assertEqual(volume["decision"], "REQUIRE_APPROVAL")
        self.assertEqual(volume["tier_policy"]["decision"], "NEEDS_APPROVAL")
        self.assertIn("NEEDS_APPROVAL", volume["reason"])

    def test_prune_is_denied_by_the_tier_policy_which_has_no_override(self):
        result = evaluate(action={"kind": "destructive", "tier": "PRUNE"},
                          resolved={"owner": managed(), "protection": False,
                                    "classification": "STALE_CANDIDATE"})
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["tier_policy"]["decision"], "DENY_BY_DEFAULT")

    def test_an_unclassified_subject_is_not_actionable(self):
        """A §13 class the classifier could not produce is not a licence. The
        tier policy refuses an unclassified subject, and the gate reports that
        refusal rather than inventing a class for it."""
        result = evaluate(action={"kind": "destructive", "tier": "STOP"},
                          resolved={"owner": managed(), "protection": False,
                                    "classification": None})
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["tier_policy"]["decision"], "DENY_NOT_ACTIONABLE")

    def test_the_tier_policy_is_hygiene_admissions_own_record_not_a_copy(self):
        """The fold has one implementation of "may this tier be taken" and it
        is not in this module. Checked by comparing against a direct call."""
        direct = hygiene_mod.admission("STOP", "STALE_CANDIDATE", protection=None)
        folded = evaluate(action={"kind": "destructive", "tier": "STOP"},
                          resolved={"owner": managed(), "protection": False,
                                    "classification": "STALE_CANDIDATE"})
        self.assertEqual(folded["tier_policy"], direct)

    def test_a_managed_subject_with_no_tier_names_the_tier_as_the_repair(self):
        """The case where the tier rule is the one that answers.

        A MANAGED subject reaches it, and the sentence names the repair rather
        than sending the caller to a registry that already knows them. At the
        verb this is exit 2, because no verdict about the subject exists.
        """
        with self.assertRaises(gate.Rejected) as caught:
            evaluate(action={"kind": "destructive", "tier": None})
        self.assertIn("tier", str(caught.exception))

        # The control: the same subject and the same action WITH a tier does
        # not raise. The rule is about the tier, and not about `destructive`
        # being refused on sight.
        try:
            evaluate(action={"kind": "destructive", "tier": "STOP"})
        except gate.Rejected as exc:
            self.fail(f"the tier rule fired for a named tier: {exc}")

    def test_an_additive_action_never_consults_a_tier(self):
        """Not "the tier allowed it" — the tier was never asked. `tier_policy`
        is absent rather than null-for-allowed, because `hygiene.admission`
        answers a question about removals and has no answer for creations."""
        self.assertNotIn("tier_policy", evaluate())


# ---------------------------------------------------------------------------
# §C2.4 · the gate is not optional, and the actuator is never entered
# ---------------------------------------------------------------------------

class TheActuatorIsNeverEntered(unittest.TestCase):
    """The refusals below are reached through `network_mutation.run`, which is
    the entry point the CLI uses. A gate that is only reachable from a test is
    a checker, and the distinction is the one C1 §七 drew first."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)

    SUBJECT_REF = "work-item:C2-FIXTURE"

    def _request(self, owner_ref: str) -> Path:
        path = self.tmp / "req.json"
        path.write_text(json.dumps({"network": {
            "network_name": "c2-control", "owner_ref": owner_ref,
            "subnet": "10.247.9.0/24"}}), encoding="utf-8")
        return path

    def _policy(self) -> Path:
        """COMPLETE, with the candidate's own pool declared.

        `allowed_pools` is what the network policy allocates FROM and
        `corporate_reserved_cidrs` is what it must never allocate INTO, and
        they are different lists: a pool is not a reservation. Giving the
        candidate's range both would leave two rules refusing it and the test
        unable to say which one a green path had satisfied.
        """
        path = self.tmp / "HOST-PROFILE.yaml"
        path.write_text(yaml.safe_dump({"network_policy": {
            "coverage": "COMPLETE",
            "allowed_pools": [{"cidr": "10.247.0.0/16", "provenance": "C2 fixture"}],
            "corporate_reserved_cidrs": [{"cidr": "10.10.0.0/16",
                                          "provenance": "C2 fixture"}],
            "vpn_reserved_cidrs": [],
            "infrastructure_reserved_cidrs": [],
        }}), encoding="utf-8")
        return path

    def _context(self) -> Path:
        path = self.tmp / "context.json"
        path.write_text(json.dumps({"context_ref": "ctx:c2",
                                    "actor_ref": "actor:c2"}), encoding="utf-8")
        return path

    def _registry(self, *, protection: str = "normal",
                  owner_ref: str | None = None) -> dict:
        """A registry that knows exactly one ref: `SUBJECT_REF`.

        Deliberately parameterized by the REGISTERED ref rather than by the
        requested one. A fixture built from the request would register whatever
        the test asked about, so the three refusal cases below would each be
        handed a registry that admits them and would pass only by accident.
        """
        path = self.tmp / "registry.yaml"
        path.write_text(yaml.safe_dump({"resources": [{
            "id": "c2-fixture", "kind": "project",
            "owner": {"ref": owner_ref or self.SUBJECT_REF, "type": "work-item"},
            "protection": {"level": protection},
            "lifecycle": {"class": "task"}}]}), encoding="utf-8")
        return containers.load_registry(str(path))

    class Host:
        """The five reads `network_evidence` makes, and nothing else.

        Three of them are the network reads C1's tests use; the empty network
        list is what makes the candidate's subnet collide with nothing, which
        is what a test of OWNERSHIP needs — a green path that C1 refused for
        its own reasons would prove nothing about this gate.
        """

        def __init__(self, decisions=None):
            self.decisions = decisions or {}
            self.probed: list[str] = []

        def docker_network_ls(self):
            return []

        def docker_network_inspect(self, name):
            raise probes_mod.ProbeUnavailable(f"no such network {name}")

        def interface_cidrs(self):
            return [{"ifname": "eth0", "addr_info": [
                {"family": "inet", "local": "10.200.6.77", "prefixlen": 27,
                 "scope": "global"}]}]

        def route_table(self):
            return [{"dst": "default", "gateway": "10.200.6.65", "dev": "eth0"},
                    {"dst": "local", "dev": "lo", "type": "local"}]

        def route_get(self, address):
            self.probed.append(address)
            return self.decisions.get(address, {
                "address": address, "raw": f"{address} via 10.200.6.65 dev eth0",
                "dev": "eth0", "via": "10.200.6.65", "local": False,
                "reachable": True})

    class Recorder:
        def __init__(self):
            self.calls: list[list[str]] = []

        def __call__(self, argv):
            self.calls.append(list(argv))
            return {"returncode": 0, "stdout": "created", "stderr": ""}

    def _run(self, *, owner_ref: str, context: bool, execute: bool = True,
             protection: str = "normal"):
        """One end-to-end call through `mutation_mod.run` — the entry point.

        Everything is provided except ownership, which is the point: `run`
        takes no ownership argument and has no way to be handed one. If the
        composed verdict below is not the one this test expects, the only
        producer it could have come from is the one under test.
        """
        argv = ["create", "--request", str(self._request(owner_ref)),
                "--policy", str(self._policy())]
        if context:
            argv += ["--context", str(self._context())]
        if execute:
            argv.append("--execute")
        runner = self.Recorder()
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = mutation_mod.run(argv, host=self.Host(), runner=runner,
                                    registry=self._registry(protection=protection))
        return code, json.loads(out.getvalue() or "{}"), err.getvalue(), runner

    # -- the wiring ---------------------------------------------------------

    def test_a_missing_context_refuses_and_names_that_reason(self):
        """`--context` is the argument `run` was calling `preflight` without,
        and its absence is a DENY with a code rather than an argparse error."""
        code, payload, _, runner = self._run(owner_ref=self.SUBJECT_REF,
                                             context=False)
        self.assertEqual(code, 10)
        self.assertEqual(payload["ownership"]["decision"], "DENY")
        self.assertEqual(payload["ownership"]["code"], "NO_TRUSTED_CONTEXT")
        self.assertEqual(runner.calls, [])

    def test_an_unregistered_requester_refuses_even_with_a_context(self):
        code, payload, _, runner = self._run(owner_ref="actor:who-is-this",
                                             context=True)
        self.assertEqual(code, 10)
        self.assertEqual(payload["ownership"]["code"], "OWNER_UNKNOWN")
        self.assertEqual(runner.calls, [])

    def test_a_registered_requester_with_a_context_is_admitted(self):
        """The control for both refusals above, and the proof that the gate is
        not simply refusing everything. Exit 0 is C1's verdict; the ownership
        line beside it is C2's."""
        code, payload, _, runner = self._run(owner_ref=self.SUBJECT_REF,
                                             context=True)
        self.assertEqual(code, 0)
        self.assertEqual(payload["ownership"]["decision"], "ALLOW")
        self.assertTrue(payload["ownership"]["eligible"])
        self.assertEqual(payload["ownership"]["owner"]["decided_by"],
                         "registry-match")

    def test_the_registry_decides_and_a_name_does_not(self):
        """§C2.2 at the entry point. These four strings differ by nothing
        except whether the registry has a registration under them; one is an
        owner and the other three are strings."""
        for ref in ("actor:who-is-this", "work-item:C2-FIXTUR", "c2-fixture",
                    "work-item:c2-fixture"):
            with self.subTest(ref=ref):
                code, payload, _, runner = self._run(owner_ref=ref, context=True)
                self.assertEqual(code, 10, f"{ref!r} was admitted")
                self.assertEqual(runner.calls, [])

    # -- the actuator -------------------------------------------------------

    def test_the_executor_refuses_a_deny_on_its_own_reading(self):
        """`execute` is handed a DENY directly, without a gate in front of it.
        It reads the verdict itself and does not take the caller's word."""
        plan = {"candidate": {"network_name": "c2-control",
                              "subnet": "10.247.9.0/24"}}
        runner = self.Recorder()
        outcome = mutation_mod.execute(
            plan, {"decision": "DENY", "code": "OWNER_UNKNOWN", "reason": "fixture"},
            runner=runner, enabled=True)
        self.assertFalse(outcome["performed"])
        self.assertFalse(outcome["runner_called"])
        self.assertEqual(runner.calls, [], "the actuator was entered on a DENY")

    def test_a_require_approval_is_also_not_executed(self):
        """REQUIRE_APPROVAL is the value C1's compose block says only this gate
        produces. It is not ALLOW and the executor is not near it."""
        plan = {"candidate": {"network_name": "c2-control",
                              "subnet": "10.247.9.0/24"}}
        runner = self.Recorder()
        outcome = mutation_mod.execute(
            plan, {"decision": "REQUIRE_APPROVAL", "code": "PROTECTED_SUBJECT"},
            runner=runner, enabled=True)
        self.assertFalse(outcome["performed"])
        self.assertEqual(runner.calls, [])

    def test_a_protected_subject_asks_for_approval_and_runs_nothing(self):
        """§C2.4's protected-list path, end to end: the requester is registered
        AND protected, so C1 is satisfied and C2 requires approval. The
        container0 / rollback-anchor / other-user rows of that list are the
        same shape with a different registration."""
        code, payload, _, runner = self._run(owner_ref=self.SUBJECT_REF,
                                             context=True, protection="protected")
        self.assertEqual(payload["ownership"]["decision"], "REQUIRE_APPROVAL")
        self.assertEqual(payload["ownership"]["code"], "PROTECTED_SUBJECT")
        self.assertEqual(code, 11,
                         "an approval is not a success; `&& ` must not proceed")
        self.assertFalse(payload["execution"]["performed"])
        self.assertEqual(runner.calls, [])

    def test_only_an_explicit_execute_flag_lets_the_runner_run(self):
        """The positive control for all four refusals above. This is the one
        configuration on this fixture in which the actuator is entered, and it
        is entered because a person passed `--execute` on an admitted
        subject."""
        code, payload, _, runner = self._run(owner_ref=self.SUBJECT_REF,
                                             context=True, execute=True)
        self.assertEqual(code, 0)
        self.assertTrue(payload["execution"]["performed"])
        self.assertEqual(len(runner.calls), 1)
        self.assertEqual(runner.calls[0][:3], ["docker", "network", "create"])

    def test_without_execute_the_same_admitted_subject_still_runs_nothing(self):
        code, payload, _, runner = self._run(owner_ref=self.SUBJECT_REF,
                                             context=True, execute=False)
        self.assertEqual(code, 0)
        self.assertFalse(payload["execution"]["performed"])
        self.assertEqual(runner.calls, [])

    def test_no_non_allow_verdict_reaches_the_runner(self):
        """§C2.5 rows 1, 2 and 3 as one property, because at the actuator they
        are one property: the only value `execute` proceeds on is ALLOW.

        EXTERNAL is produced by container evidence — a resource whose decisive
        evidence places it under another actor — and the create path cannot
        reach it, since a network that does not exist yet is asked about
        through its `owner_ref`. It is constructed here rather than omitted:
        "unreachable from this entry point" and "unhandled by this executor"
        are different claims, and only the first one is true.
        """
        plan = {"candidate": {"network_name": "c2-control",
                              "subnet": "10.247.9.0/24"}}
        for verdict in (
                {"decision": "DENY", "code": "OWNER_UNKNOWN",
                 "reason": "no decisive evidence names an owner"},
                {"decision": "DENY", "code": "OWNER_EXTERNAL",
                 "reason": "the subject's decisive evidence places it under "
                           "another actor"},
                {"decision": "DENY", "code": "PROTECTED_SUBJECT",
                 "reason": "protection is declared and the action removes"},
                {"decision": "REQUIRE_APPROVAL", "code": "PROTECTED_SUBJECT",
                 "reason": "protection is declared and the action creates"}):
            with self.subTest(code=verdict["code"], decision=verdict["decision"]):
                runner = self.Recorder()
                outcome = mutation_mod.execute(plan, verdict, runner=runner,
                                               enabled=True)
                self.assertFalse(outcome["performed"])
                self.assertFalse(outcome["runner_called"])
                self.assertEqual(runner.calls, [])

    def test_ownership_admission_is_not_the_action_policys_admission(self):
        """§C2.1's `「有资格继续」!=「自动允许」`, at the entry point.

        The requester is registered and unprotected, so this gate ALLOWS — and
        the candidate's subnet is outside the pools C1's policy allocates from,
        so the action policy refuses. Ownership's ALLOW is a statement about
        who the subject belongs to; it is not a statement about whether the
        change is permitted, and a product where the two were one field would
        report this request as authorised.
        """
        request = self.tmp / "req-outside.json"
        request.write_text(json.dumps({"network": {
            "network_name": "c2-outside", "owner_ref": self.SUBJECT_REF,
            "subnet": "10.99.7.0/24"}}), encoding="utf-8")
        runner = self.Recorder()
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = mutation_mod.run(
                ["create", "--request", str(request),
                 "--policy", str(self._policy()),
                 "--context", str(self._context()), "--execute"],
                host=self.Host(), runner=runner, registry=self._registry())
        payload = json.loads(out.getvalue() or "{}")
        self.assertEqual(payload["ownership"]["decision"], "ALLOW")
        self.assertTrue(payload["ownership"]["eligible"])
        # The preflight is reported inside the disclosure envelope, and the
        # code below is what names the rule that refused — `allowed_pools`,
        # which is C1's, and not an ownership code standing in for it.
        self.assertEqual(payload["preflight"]["state"], "DISCLOSED")
        verdict = payload["preflight"]["value"]
        self.assertEqual(verdict["decision"], "DENY")
        self.assertEqual(verdict["codes"], ["NETWORK_OUTSIDE_ALLOWED_POOL"])
        self.assertEqual(code, 10)
        self.assertFalse(payload["execution"]["performed"])
        self.assertEqual(runner.calls, [],
                         "the action policy refused and the actuator ran anyway")


# ---------------------------------------------------------------------------
# The verb, and its exit codes
# ---------------------------------------------------------------------------

class TheExitCode(unittest.TestCase):
    """The verdict as a process exit code.

    Three outcomes, three codes, because they have three repairs — and the one
    that would be easiest to collapse is the one that matters. `eligible` is
    True for an approval; that is what `eligible` means, and it is why the field
    is not called `allowed`. A CLI that mapped `eligible` to the exit code
    would report "a person must approve this" as success to every caller that
    reads `$?` — and those are the callers that do the next thing.

    Driven through `exit_code` rather than through a live `check`, because the
    mapping takes a record and producing one should not require a container.
    The end-to-end path is covered in `TheActuatorIsNeverEntered`, where the
    same three codes are read off `network_mutation.run`.
    """

    def _verdict(self, decision: str) -> dict:
        return {"decision": decision, "eligible": decision != "DENY"}

    def test_the_four_codes(self):
        self.assertEqual(gate.exit_code(self._verdict("ALLOW")), 0)
        self.assertEqual(gate.exit_code(self._verdict("REQUIRE_APPROVAL")), 11)
        self.assertEqual(gate.exit_code(self._verdict("DENY")), 10)

    def test_the_three_outcomes_have_three_distinct_codes(self):
        codes = {gate.exit_code(self._verdict(d))
                 for d in ("ALLOW", "REQUIRE_APPROVAL", "DENY")}
        self.assertEqual(len(codes), 3)

    def test_an_approval_is_not_a_success(self):
        self.assertNotEqual(gate.exit_code(self._verdict("REQUIRE_APPROVAL")), 0)

    def test_a_verdict_that_cannot_be_read_is_denied_and_not_permitted(self):
        """The mapping's own fail-closed direction: `eligible` absent is not
        `eligible` true, and a record this function cannot read must not come
        out as one of the two codes a caller proceeds on."""
        self.assertEqual(gate.exit_code({}), 10)
        self.assertEqual(gate.exit_code({"decision": "ALLOW"}), 10)

    def test_the_codes_come_from_the_module_and_not_from_the_cli(self):
        """`cmd_ownership` returns what this returns. A second mapping in
        `cli.py` would be a second answer to one question — which is the
        defect this round has now found in three places."""
        source = (REPO / "runtime" / "cli.py").read_text(encoding="utf-8")
        self.assertIn("_ownership_module().run(argv)", source)


# ---------------------------------------------------------------------------
# The verb's own refusals, which do not need a container to produce
# ---------------------------------------------------------------------------

class TheVerbRefuses(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.registry = self.tmp / "registry.yaml"
        self.registry.write_text(yaml.safe_dump({"resources": []}),
                                 encoding="utf-8")

    def _run(self, argv):
        import io
        from contextlib import redirect_stdout, redirect_stderr
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = gate.run(argv)
        return code, out.getvalue() + err.getvalue()

    def test_a_destructive_action_with_no_tier_is_never_admitted(self):
        """The property, and it is the one that matters: whichever rule answers
        first, leaving the tier out cannot produce exit 0.

        Each shape names the rule that actually answered, because they are
        different rules with different repairs and the test would be weaker if
        it only asserted "nonzero":

          * no context — NO_TRUSTED_CONTEXT, exit 10. Nobody was named as
            asking, which is checked before the subject is even resolved.
          * with a context — the subject does not resolve on this host, so
            OWNER_UNKNOWN, exit 10.

        `TheTierFold` holds the case where the tier rule is the one that
        answers: a MANAGED subject, which raises `Rejected` and reaches the
        verb as exit 2.
        """
        context = self.tmp / "ctx.json"
        context.write_text(json.dumps({"context_ref": "ctx:c2",
                                       "actor_ref": "actor:c2"}), encoding="utf-8")
        base = ["check", "--kind", "container", "--name", "x",
                "--action", "destructive", "--registry", str(self.registry)]
        for label, argv, answering in (
                ("no context", base, "NO_TRUSTED_CONTEXT"),
                ("with context", base + ["--context", str(context)], "OWNER_UNKNOWN")):
            with self.subTest(shape=label):
                code, text = self._run(argv)
                self.assertEqual(code, 10)
                self.assertIn(answering, text)

    def test_a_refusal_that_names_nobody_is_reached_without_asking_a_host(self):
        """The ordering, and the ordering is the whole of this test.

        `evaluate` resolves the subject before `decide` applies any rule, and
        resolving a container reaches Docker. So a question that names nobody
        to ask about was answerable only while the daemon happened to be up:
        with the daemon down the verb returned 2 — `EXIT_UNASKABLE`, the
        question could not be put — where the honest answer is 10, `EXIT_DENY`
        `NO_TRUSTED_CONTEXT`. A refusal that names the wrong reason is bad
        enough; this one also made the refusal depend on a daemon it never
        mentioned, which is not a property a gate may have.

        The daemon is made to RAISE rather than merely to be absent, and that
        is the assertion: this is a statement that the call did not happen, not
        that it happened to succeed.
        """
        from unittest import mock

        asked = []

        def the_daemon_cannot_be_asked(**kwargs):
            asked.append(kwargs)
            raise gate.containers.DockerUnavailable("no daemon at this instant")

        base = ["check", "--kind", "container", "--name", "x",
                "--action", "destructive", "--registry", str(self.registry)]
        with mock.patch.object(gate, "resolve", the_daemon_cannot_be_asked):
            code, text = self._run(base)
        self.assertEqual(code, gate.EXIT_DENY)
        self.assertIn("NO_TRUSTED_CONTEXT", text)
        self.assertEqual(
            asked, [],
            "the host was asked about a question it cannot affect, so the "
            "answer can still change with the daemon's mood")

    def test_a_question_that_names_somebody_still_reaches_the_host(self):
        """The positive control for the rule above: it fires on a missing
        context and on nothing else.

        Without this, the precondition could be widened until it answered every
        question, and the test above would go on passing — a gate that refuses
        everything looks exactly like one that refuses correctly to a test that
        only ever asks it the refused question.
        """
        from unittest import mock

        context = self.tmp / "ctx.json"
        context.write_text(json.dumps({"context_ref": "ctx:c2",
                                       "actor_ref": "actor:c2"}),
                           encoding="utf-8")
        asked = []

        def the_host_answers_once(**kwargs):
            asked.append(kwargs)
            return {"found": False, "why": "fixture: nothing by that name",
                    "owner": {"class": "UNKNOWN", "confidence": "unknown",
                              "because": "fixture", "decided_by": None},
                    "protection": None, "classification": None}

        with mock.patch.object(gate, "resolve", the_host_answers_once):
            code, text = self._run(["check", "--kind", "container", "--name", "x",
                                    "--action", "destructive",
                                    "--registry", str(self.registry),
                                    "--context", str(context)])
        self.assertEqual(len(asked), 1)
        self.assertEqual(code, gate.EXIT_DENY)
        self.assertIn("OWNER_UNKNOWN", text)

    def test_the_verdict_says_it_did_not_look_rather_than_that_it_found_nothing(self):
        """`resolved` is three-valued here for the same reason it is everywhere
        else in this module: not-looked and looked-and-found-nothing are
        different answers, and a record that renders them the same way is one a
        reader cannot use to tell a daemon that was down from a subject that is
        not there.
        """
        verdict = gate.evaluate(subject={"kind": "container", "name": "x"},
                                action={"kind": "additive"})
        self.assertEqual(verdict["code"], "NO_TRUSTED_CONTEXT")
        # `None` and not `False`: `False` is the record a lookup that ran and
        # found nothing produces, and `_NOT_LOOKED` must not reach it.
        self.assertIsNone(verdict["resolved"]["found"])
        self.assertIn("not looked", verdict["resolved"]["why"])
        self.assertIsNone(verdict["resolved"]["classification"])
        self.assertIsNone(verdict["resolved"]["management"])

    def test_a_kind_nobody_can_weigh_is_refused_before_a_verdict_is_invented(self):
        """`--kind` is a closed set and it is read from the contract, so a kind
        the gate has no evidence for cannot be asked about at all. argparse
        exits 2 rather than returning it, and that is the same sentence the
        module uses elsewhere: the question could not be put."""
        with self.assertRaises(SystemExit) as caught:
            self._run(["check", "--kind", "spaceship", "--name", "x"])
        self.assertEqual(caught.exception.code, 2)


# ---------------------------------------------------------------------------
# The container adapter, against the real producers
# ---------------------------------------------------------------------------

class TheContainerAdapter(unittest.TestCase):
    """`resolve_container` is a wiring, and a wiring is only worth what its
    inputs are. These call the real `containers.evidence` and
    `containers.attribute`, so a change to the shape they return turns this
    red rather than being absorbed."""

    def _inspect(self, name: str, *, labels: dict | None = None) -> dict:
        return {
            "Id": "a" * 64,
            "Name": f"/{name}",
            "Config": {"Image": "example:latest", "Labels": labels or {}},
            "State": {"Status": "running", "Running": True},
            "NetworkSettings": {"Ports": {}},
            "Mounts": [],
            "HostConfig": {},
        }

    def _registry(self, owner_ref: str, *, protection: str = "normal") -> dict:
        document = {"resources": [{
            "id": "lit-0104-dev100", "kind": "runtime",
            "owner": {"ref": owner_ref, "type": "work-item"},
            "protection": {"level": protection},
            "lifecycle": {"class": "service"},
            "probes": [{"container": "lit-0104-dev100-litellm"}],
        }]}
        return {"registry_present": True, **containers.build_registry_index(document)}

    def test_a_registered_container_is_managed_on_decisive_evidence(self):
        record = self._inspect("lit-0104-dev100-litellm")
        resolved = gate.resolve_container(
            subject={"kind": "container", "name": "lit-0104-dev100-litellm"},
            registry=self._registry("work-item:LIT-0104"), inspected=record)
        self.assertTrue(resolved["found"])
        self.assertEqual(resolved["owner"]["class"], "MANAGED")
        self.assertEqual(resolved["owner"]["decided_by"], "registry-match")

    def test_the_same_container_with_a_label_only_name_is_unknown(self):
        """The name is unchanged and the registry entry is removed. §11
        reaches UNKNOWN_OWNER, and the gate refuses on it."""
        record = self._inspect("lit-0104-dev100-litellm")
        resolved = gate.resolve_container(
            subject={"kind": "container", "name": "lit-0104-dev100-litellm"},
            registry={"registry_present": True, **containers.build_registry_index({})},
            inspected=record)
        self.assertEqual(resolved["owner"]["class"], "UNKNOWN")
        result = gate.evaluate(subject={"kind": "container",
                                        "name": "lit-0104-dev100-litellm"},
                               action={"kind": "additive"}, context=CONTEXT,
                               resolved=resolved)
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "OWNER_UNKNOWN")

    def test_a_registered_protected_container_carries_its_protection_through(self):
        record = self._inspect("lit-0104-dev100-litellm")
        resolved = gate.resolve_container(
            subject={"kind": "container", "name": "lit-0104-dev100-litellm"},
            registry=self._registry("work-item:LIT-0104", protection="protected"),
            inspected=record)
        self.assertIs(resolved["protection"], True)
        result = gate.evaluate(subject={"kind": "container",
                                        "name": "lit-0104-dev100-litellm"},
                               action={"kind": "destructive", "tier": "REMOVE_CONTAINER"},
                               context=CONTEXT, resolved=resolved)
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "PROTECTED_SUBJECT")

    def test_a_container_that_is_not_there_is_unknown_and_not_absent_silently(self):
        resolved = gate.resolve_container(
            subject={"kind": "container", "name": "no-such-thing"},
            registry={"registry_present": True},
            inspected=None,
            host=type("H", (), {"docker_ps": lambda self, all_=True: [],
                                "docker_inspect": lambda self, n: None})())
        self.assertFalse(resolved["found"])
        self.assertEqual(resolved["owner"]["class"], "UNKNOWN")

    def test_a_name_that_declares_an_owner_is_still_not_under_management(self):
        """§11's two axes, kept apart at this layer.

        A container writing `serveros.owner` names its owner — a decisive
        source, §11's first rung — and does NOT thereby declare itself
        managed. `serveros.managed` does that, and the registry does. So the
        management class is UNKNOWN and the gate refuses, which is the
        fail-closed direction and also the correct one: this module takes §11's
        class rather than re-deriving a class from the owner ref it can see,
        because re-deriving is the second attribution system the contract
        forbids.
        """
        record = self._inspect("somebody-elses-thing",
                               labels={"serveros.owner": "project:other"})
        resolved = gate.resolve_container(
            subject={"kind": "container", "name": "somebody-elses-thing"},
            registry={"registry_present": True},
            inspected=record)
        self.assertEqual(resolved["owner"]["class"], "UNKNOWN")
        result = gate.evaluate(subject={"kind": "container",
                                        "name": "somebody-elses-thing"},
                               action={"kind": "additive"}, context=CONTEXT,
                               resolved=resolved)
        self.assertEqual(result["decision"], "DENY")
        self.assertEqual(result["code"], "OWNER_UNKNOWN")

    def test_the_managed_label_is_what_makes_it_managed(self):
        """The control, one label away, and the repair the refusal above
        names: `serveros.managed` is a statement and `serveros.owner` is not
        one about management."""
        record = self._inspect("our-own-thing",
                               labels={"serveros.owner": "project:ours",
                                       "serveros.managed": "true"})
        resolved = gate.resolve_container(
            subject={"kind": "container", "name": "our-own-thing"},
            registry={"registry_present": True}, inspected=record)
        self.assertEqual(resolved["owner"]["class"], "MANAGED")
        result = gate.evaluate(subject={"kind": "container",
                                        "name": "our-own-thing"},
                               action={"kind": "additive"}, context=CONTEXT,
                               resolved=resolved)
        self.assertEqual(result["decision"], "ALLOW")


# ---------------------------------------------------------------------------
# The declaration and the code agree
# ---------------------------------------------------------------------------

class TheSurfaceDeclarationAgreesWithTheCode(unittest.TestCase):
    """The contract now names its verb and its exit codes, and both are
    implemented elsewhere.

    That is the arrangement this round keeps having to repair: a declaration
    and an implementation that agree on the day they are written and have
    nothing between them afterwards. The two tests here are the join — one for
    the codes, one for the absence of a mutating verb — and
    `tests/test_declared_commands.py` is the third, for the command name.
    """

    def test_the_declared_exit_codes_are_the_implemented_ones(self):
        declared = {str(code) for code in CONTRACT["surface"]["exit_codes"]}
        self.assertEqual(declared, {"0", "10", "11", "2"})
        implemented = {str(gate.EXIT_ALLOW), str(gate.EXIT_DENY),
                       str(gate.EXIT_REQUIRE_APPROVAL), str(gate.EXIT_UNASKABLE)}
        self.assertEqual(declared, implemented)
        # And each name maps to the code the contract lists it under, so the
        # constants cannot be reordered underneath a declaration that stayed
        # put.
        self.assertEqual(gate.EXIT_ALLOW, 0)
        self.assertEqual(gate.EXIT_DENY, 10)
        self.assertEqual(gate.EXIT_REQUIRE_APPROVAL, 11)
        self.assertEqual(gate.EXIT_UNASKABLE, 2)

    def test_the_declared_surface_claims_no_way_to_act(self):
        surface = CONTRACT["surface"]
        self.assertIs(surface["read_only"], True)
        self.assertEqual(surface["execute_flag"], "none")
        self.assertTrue(surface["mutating_verbs"].startswith("none"),
                        "the contract claims a mutating verb; if that is now "
                        "true, this gate has changed kind and the claim above "
                        "about `execute_flag: none` is not the only thing that "
                        "has to change")

    def test_the_declared_command_is_a_two_word_verb(self):
        """The shape, and not the existence.

        Existence is `tests/test_declared_commands.py`'s question: it walks
        `cli.build_parser()` and fails when a scanned file names a verb the
        parser does not accept — I checked by writing `serverosctl ownership
        verify` into this contract and watching it go red. Restating that here
        by grepping cli.py for the verb would be a second gate for one
        question, and the first draft of this test was exactly that, and broke
        the moment the call was formatted across two lines.
        """
        self.assertEqual(CONTRACT["surface"]["command"].split()[:2],
                         ["serverosctl", "ownership"])


class TheContractAgreesWithTheCode(unittest.TestCase):

    def test_every_code_the_contract_declares_is_ordered_and_has_a_repair(self):
        """The pathology this repository keeps finding is a declaration with
        no producer. The converse is equally a defect: a code the code can
        emit that the contract does not document, or documents without saying
        how to repair it."""
        declared = sorted(CONTRACT["codes"])
        ordered = [c for c in gate.code_precedence() if c in declared]
        self.assertEqual(sorted(ordered), declared,
                         "every declared code has a place in the precedence")
        for name in declared:
            with self.subTest(code=name):
                for key in ("rule", "means", "repair"):
                    self.assertTrue(CONTRACT["codes"][name].get(key),
                                    f"{name} declares no {key}")

    def test_the_precedence_has_no_entry_the_contract_does_not_declare(self):
        self.assertEqual(sorted(set(gate.code_precedence())), sorted(CONTRACT["codes"]))

    def test_the_decisions_are_the_contracts_decisions(self):
        self.assertEqual(gate.decisions(), tuple(CONTRACT["decisions"]))

    def test_every_decision_a_verdict_can_carry_is_declared(self):
        """Driven, not asserted from the source. A fourth value added to the
        code and not to the contract would be a decision nobody documented."""
        produced = set()
        cases = [
            dict(),
            dict(context=None),
            dict(resolved={"owner": {"class": "UNKNOWN"}, "protection": False}),
            dict(resolved={"owner": {"class": "EXTERNAL"}, "protection": False}),
            dict(action={"kind": "destructive", "tier": "STOP"},
                 resolved={"owner": managed(), "protection": True}),
            dict(resolved={"owner": managed(), "protection": True}),
        ]
        for case in cases:
            produced.add(evaluate(**case)["decision"])
        self.assertTrue(produced.issubset(set(gate.decisions())), produced)
        self.assertIn("REQUIRE_APPROVAL", produced,
                      "the value C1's compose block says is only reachable here")

    def test_the_decision_table_covers_every_class_protection_action_combination(self):
        """Totality over the SPECIFIC combinations only.

        `any` is a wildcard in the table and not a value of the axis, so it
        cannot appear on both sides of this check: a set built with `any` in
        the expected side asks whether a wildcard row covers a wildcard
        question, which is a question about the notation rather than about the
        product. The pairs below are the ones `decide` can actually be handed.
        """
        table = {(row["owner_class"], row["protection"], row["action"])
                 for row in CONTRACT["decision_table"]}

        def covered(cls, prot, act):
            return ((cls, prot, act) in table or (cls, "any", act) in table
                    or (cls, prot, "any") in table or (cls, "any", "any") in table)

        for cls in ("UNKNOWN", "EXTERNAL", "MANAGED"):
            for prot in ("protected", "not_protected"):
                for act in ("destructive", "additive"):
                    with self.subTest(owner=cls, protection=prot, action=act):
                        self.assertTrue(covered(cls, prot, act),
                                        f"no row covers ({cls}, {prot}, {act})")

        # And the wildcards have to be earning their place: an `any` row is
        # only admissible where it is the whole truth, which is the two
        # classes whose decision does not depend on the action at all.
        for row in CONTRACT["decision_table"]:
            if row["action"] == "any" or row["protection"] == "any":
                self.assertIn(row["owner_class"], ("UNKNOWN", "EXTERNAL"),
                              f"{row} uses a wildcard where the answer is specific")

    def test_the_entry_point_cannot_be_called_without_composing_a_verdict(self):
        """C1's contract says of the ownership input: "Neither is implemented
        here." That sentence was true of `network-contract.yaml` when it was
        written and it is still true of that file — this gate is elsewhere.
        What must not survive is `network_mutation` calling `preflight`
        without the argument, and that is what is checked."""
        import inspect

        source = inspect.getsource(mutation_mod.run)
        self.assertIn("ownership", source,
                      "run() must pass an ownership verdict to preflight")
        self.assertIn("ownership_verdict", inspect.getsource(mutation_mod))

    def test_this_gate_has_no_mutating_verb_in_it(self):
        """`hygiene.py`'s sentence, kept: the caller that executes is a
        different component, deliberately. Read from the source so that a
        future edit turns this red rather than being caught by a reviewer."""
        source = (RUNTIME / "ownership_gate.py").read_text(encoding="utf-8")
        body = "\n".join(
            line for line in source.splitlines()
            if not line.lstrip().startswith("#"))
        for verb in ("docker stop", "docker rm", "docker rmi", "docker prune",
                     "subprocess.run", "subprocess.Popen", "os.system",
                     "os.remove", "shutil.rmtree"):
            with self.subTest(verb=verb):
                self.assertNotIn(verb, body)

    def test_the_disclosure_levels_are_the_contracts_axis_and_not_a_second_one(self):
        """Same four levels as `disclosure-contract.yaml`. This is a rendering
        of one record, not a second coordinate system."""
        verdict = evaluate()
        self.assertEqual(gate.disclose(verdict, "L0"),
                         verdict["decision"])
        self.assertIn(verdict["reason"], gate.disclose(verdict, "L1"))
        self.assertIn("protect", gate.disclose(verdict, "L2"))
        self.assertIn("resolved", gate.disclose(verdict, "L3"))
        with self.assertRaises(gate.Rejected):
            gate.disclose(verdict, "L9")


if __name__ == "__main__":
    unittest.main()
