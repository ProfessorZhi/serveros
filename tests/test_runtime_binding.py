#!/usr/bin/env python3
"""§3/§5/§6/§7/§13/§14/§15/§18/§19 — a binding is a device fact.

WHAT THIS FILE IS ABOUT
-----------------------
`fleet/registry.yaml` used to say `agents: [claude-code, codex]`, and that was
the whole of what ServerOS knew about which runtime was where. It is membership:
two names on a line. It cannot be asked which installation, on which surface, at
which version, established when, by which probe, with which features — and
`abilities/agent-runtime-contract.yaml` meanwhile declared `version_observed_by`
and `capability_probe` as required fields that no program read.

`abilities/runtime_binding.py` is the answer to the first half and the removal
of the second. These tests are about the four ways it could still be decoration:

1. If the binding identity moved when a version did, it would be an observation
   rather than an identity, and every upgrade would orphan the device's record.
2. If an unobserved feature read as `UNAVAILABLE`, "nobody looked" would become
   "no", which is the most expensive confusion available here.
3. If a feature could be produced by the runtime's NAME, the vocabulary would be
   a restatement of the contract rather than a measurement of a machine.
4. If a version could be consumed with no `observed_at`, "it worked yesterday"
   and "it works today" would be the same record.

Each of the four has a probe in `MutationTests` that breaks it, because an
assertion of the form "nothing bad was found" is the shape a broken checker also
produces.
"""
from __future__ import annotations

import ast
import copy
import datetime
import importlib.util
import sys
import tempfile
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


binding = _load("serveros_runtime_binding",
                REPO / "abilities" / "runtime_binding.py")


def contract() -> dict:
    import yaml
    return yaml.safe_load(
        (REPO / "abilities/agent-runtime-contract.yaml").read_text(encoding="utf-8"))


def real_registry() -> dict:
    import yaml
    return yaml.safe_load((REPO / "fleet/registry.yaml").read_text(encoding="utf-8"))


# One observation of one real binding, used as the positive case throughout.
# `instruction.workspace` is UNAVAILABLE here because that is what a real Codex
# on a host with no workspace `AGENTS.md` reports — the contract's `known_gaps`
# entry and this fixture are the same fact written twice.
OBSERVED = binding.observation(
    runtime_id="codex",
    observed_at="2026-09-14T00:00:00Z",
    probe="cli-version",
    version="codex-cli 0.153.4",
    source="serverosctl device agents --probe",
    surface="cli",
    features={"mcp.client": {"state": "AVAILABLE"},
              "instruction.workspace": {"state": "UNAVAILABLE",
                                        "evidence": "no workspace instruction file"}})

REFERENCE = "2026-09-14T00:10:00Z"   # ten minutes later; inside the window


def declaration(device="device:work-fj", runtime="agent-runtime:codex", **over):
    base = {"device_ref": device, "runtime_ref": runtime,
            "declared_at": "2026-09-14", "declared_provenance": "fleet/registry.yaml"}
    base.update(over)
    return base


def device(ref="device:work-fj", runtimes=("agent-runtime:codex",), agents=None):
    entry = {"device_ref": ref,
             "runtime_bindings": [{"runtime_ref": r, "declared_at": "2026-09-14"}
                                  for r in runtimes]}
    entry["agents"] = ([r.split(":")[-1] for r in runtimes]
                       if agents is None else list(agents))
    return entry


def registry(*devices):
    return {"devices": list(devices)}


# ---------------------------------------------------------------------------
# §13 — identity
# ---------------------------------------------------------------------------


class IdentityTests(unittest.TestCase):
    """The identity is `device_ref + runtime_ref` and nothing else.

    §13 states the acceptance case directly: moving from the CLI to a desktop
    surface must be the same binding with a new observation. The way that fails
    in practice is not a wrong formula, it is a formula that reads one extra
    field — a path, a version, a surface — because the field was handy.
    """

    def test_the_identity_is_the_two_refs_and_not_a_third_namespace(self):
        self.assertEqual(binding.binding_ref("device:work-fj", "agent-runtime:codex"),
                         "binding:device:work-fj/agent-runtime:codex")

    def test_a_different_device_makes_a_different_binding(self):
        self.assertNotEqual(
            binding.binding_ref("device:work-fj", "agent-runtime:codex"),
            binding.binding_ref("device:server-beijing", "agent-runtime:codex"))

    def test_a_different_runtime_makes_a_different_binding(self):
        self.assertNotEqual(
            binding.binding_ref("device:work-fj", "agent-runtime:codex"),
            binding.binding_ref("device:work-fj", "agent-runtime:claude-code"))

    def test_a_cli_today_and_a_desktop_tomorrow_are_one_binding(self):
        """§13's case, run rather than described."""
        as_cli = binding.binding(declaration(), observed=binding.observation(
            runtime_id="codex", observed_at="2026-09-14T00:00:00Z",
            probe="cli-version", version="codex-cli 0.153.4", surface="cli"))
        as_desktop = binding.binding(declaration(), observed=binding.observation(
            runtime_id="codex", observed_at="2026-09-14T06:00:00Z",
            probe="cli-version", version="codex-desktop 0.160.0", surface="desktop"))
        self.assertEqual(as_cli["binding_ref"], as_desktop["binding_ref"])
        self.assertEqual(as_cli["declared_at"], as_desktop["declared_at"])

    def test_a_version_change_does_not_move_the_identity(self):
        before = binding.binding(declaration(), observed=OBSERVED)
        after = binding.binding(declaration(), observed={
            **OBSERVED, "version": "codex-cli 0.160.0"})
        self.assertEqual(before["binding_ref"], after["binding_ref"])

    def test_the_identity_round_trips_through_its_own_parser(self):
        """A ref that cannot be parsed back is a string that looks structured."""
        for device_ref, runtime_ref in (
                ("device:personal-dell", "agent-runtime:claude-code"),
                ("device:work-fj", "agent-runtime:codex")):
            ref = binding.binding_ref(device_ref, runtime_ref)
            self.assertEqual(binding.parse_binding_ref(ref),
                             (device_ref, runtime_ref))

    def test_a_ref_that_is_not_a_binding_is_refused(self):
        for bad in ("device:work-fj",                 # not a binding at all
                    "binding:agent-runtime:codex",    # one half
                    "binding:device:x",               # the other half
                    "binding:device:/agent-runtime:codex",  # an empty ref
                    "binding:agent-runtime:codex/device:x",  # halves swapped
                    "binding:device:x/agent-runtime:y/z"):  # a third half
            with self.assertRaises(binding.BindingError, msg=bad):
                binding.parse_binding_ref(bad)

    def test_a_bare_name_is_not_a_ref(self):
        """`codex` is a runtime id, and a binding is not built from ids."""
        with self.assertRaises(binding.BindingError):
            binding.binding_ref("device:work-fj", "codex")


# ---------------------------------------------------------------------------
# §5/§6 — the feature vocabulary
# ---------------------------------------------------------------------------


class VocabularyTests(unittest.TestCase):
    def test_the_vocabulary_has_the_four_state_words(self):
        self.assertEqual(binding.feature_states(),
                         ("AVAILABLE", "UNAVAILABLE", "UNKNOWN", "INCOMPATIBLE"))

    def test_a_feature_the_family_has_no_axis_for_is_incompatible(self):
        state, evidence = binding.feature("claude-code", "app.server", None)
        self.assertEqual(state, "INCOMPATIBLE")
        self.assertIn("declares no `app.server` axis", evidence)

    def test_a_feature_nobody_observed_is_unknown_and_not_unavailable(self):
        state, _ = binding.feature("codex", "gui", None)
        self.assertEqual(state, "UNKNOWN",
                         "an axis nobody asked about became a no")

    def test_an_observation_of_the_axis_is_what_answers_it(self):
        self.assertEqual(binding.feature("codex", "instruction.workspace",
                                         OBSERVED)[0], "UNAVAILABLE")
        self.assertEqual(binding.feature("codex", "mcp.client", OBSERVED)[0],
                         "AVAILABLE")

    def test_a_feature_outside_the_vocabulary_is_refused(self):
        with self.assertRaises(binding.BindingError):
            binding.feature("codex", "telepathy", None)

    def test_a_boolean_is_not_a_feature_state(self):
        """`True`/`False` would make "we did not ask" and "no" one value."""
        with self.assertRaises(binding.BindingError):
            binding.observation(runtime_id="codex", observed_at="2026-09-14T00:00:00Z",
                                surface="cli", features={"gui": True})
        with self.assertRaises(binding.BindingError):
            binding.observation(runtime_id="codex", observed_at="2026-09-14T00:00:00Z",
                                surface="cli", features={"gui": {"state": "YES"}})

    def test_a_product_invariant_is_answered_from_the_contract(self):
        """§9's adjudication, visible in the answer.

        `skill.native` is declared true of every supported runtime. That is a
        statement about the product, so it is answered for a binding nobody
        observed — and the evidence names the declaration it came from, so a
        reader can tell this apart from a probe.
        """
        state, evidence = binding.feature("codex", "skill.native", None)
        self.assertEqual(state, "AVAILABLE")
        self.assertIn("product_invariants", evidence)

    def test_the_invariant_declaration_names_its_enforcer(self):
        self.assertIn("runtime_binding.py",
                      binding.product_invariants()["skill.native"]["enforced_by"])

    def test_the_state_of_a_feature_always_says_where_it_came_from(self):
        record = binding.binding(declaration(), observed=OBSERVED,
                                 reference=REFERENCE)
        for name, entry in record["features"].items():
            self.assertTrue(entry["evidence"], f"{name} has no evidence")
            self.assertIn(entry["state"], binding.feature_states())


class NoInferenceFromTheNameTests(unittest.TestCase):
    """§6 — the family name may not produce a feature state.

    Two proofs, because they fail differently. The behavioural one shows the
    answer is UNKNOWN; the structural one shows there is no branch in the module
    that could have produced anything else, which is the half that survives a
    future contributor who is certain Codex Desktop has a GUI.
    """

    def test_a_family_with_the_axis_and_no_observation_is_unknown(self):
        for runtime_id in binding.runtime_ids():
            for name in binding.declared_axes(runtime_id):
                if name in binding.product_invariants():
                    continue
                self.assertEqual(binding.feature(runtime_id, name, None)[0],
                                 "UNKNOWN", f"{runtime_id}/{name}")

    def test_no_runtime_name_appears_in_the_module_as_a_value(self):
        self.assertEqual(literals_in_code(REPO / "abilities/runtime_binding.py"),
                         [],
                         "a runtime name is compared against in code; a feature "
                         "state that depends on which family it is cannot be a "
                         "measurement of a machine")

    def test_the_structural_probe_catches_a_name_compared_against(self):
        """Positive control. Without it, a scanner that stopped matching and a
        module that never had the defect are the same green result."""
        with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                         encoding="utf-8") as handle:
            handle.write('def f(runtime):\n'
                         '    if runtime == "codex":\n'
                         '        return "AVAILABLE"\n')
            sample = Path(handle.name)
        try:
            self.assertEqual(literals_in_code(sample), [(2, "codex")])
        finally:
            sample.unlink(missing_ok=True)


def literals_in_code(path) -> list[tuple[int, str]]:
    """Runtime ids used as values, docstrings excluded.

    A docstring is a `str` constant alone in an expression statement, and naming
    a runtime in prose is not the defect — `if runtime == "codex"` is. Everything
    else counts.
    """
    tree = ast.parse(Path(path).read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = getattr(node, "body", None) or []
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                docstrings.add(id(body[0].value))
    ids = set(binding.runtime_ids())
    return [(node.lineno, node.value) for node in ast.walk(tree)
            if (isinstance(node, ast.Constant) and isinstance(node.value, str)
                and node.value in ids and id(node) not in docstrings)]


# ---------------------------------------------------------------------------
# §3/§7 — the observed half
# ---------------------------------------------------------------------------


class ObservationTests(unittest.TestCase):
    def test_an_observed_binding_carries_its_probe_and_its_time(self):
        record = binding.binding(declaration(), observed=OBSERVED,
                                 reference=REFERENCE)
        self.assertEqual(record["observation"], "OBSERVED")
        self.assertEqual(record["probe"], "cli-version")
        self.assertEqual(record["version"], "codex-cli 0.153.4")
        self.assertEqual(record["version_state"], "FACT")
        self.assertEqual(record["surface"], "cli")
        self.assertEqual(record["observed_at"], "2026-09-14T00:00:00Z")
        self.assertEqual(record["provenance"]["observed"],
                         "serverosctl device agents --probe")
        self.assertLess(record["age_seconds"], 900)

    def test_a_version_with_no_observation_is_refused_rather_than_recorded(self):
        with self.assertRaises(binding.BindingError):
            binding.version_observation(runtime_id="codex", observed_at=None,
                                        probe="cli-version", version="0.153.4")

    def test_a_surface_with_no_observation_is_refused_too(self):
        with self.assertRaises(binding.BindingError):
            binding.observation(runtime_id="codex", surface="cli")

    def test_a_probe_the_family_does_not_declare_is_refused(self):
        """Otherwise two facts wear one field name."""
        with self.assertRaises(binding.BindingError):
            binding.version_observation(runtime_id="codex",
                                        observed_at="2026-09-14T00:00:00Z",
                                        probe="package-manifest", version="0.1")

    def test_an_undeclared_surface_is_refused(self):
        with self.assertRaises(binding.BindingError):
            binding.observation(runtime_id="codex",
                                observed_at="2026-09-14T00:00:00Z", surface="tmux")

    def test_a_probe_nobody_implements_is_not_accepted_as_accepted(self):
        """The contract accepts three probes and one has a producer, so the
        difference is stated rather than left to a reader to assume."""
        self.assertEqual(binding.implemented_probes(), ("cli-version",))
        self.assertEqual(
            set(binding.accepted_probes()) - set(binding.implemented_probes()),
            {"package-manifest", "service-handshake"})

    def test_a_probe_that_cannot_run_is_unknown_not_unavailable(self):
        """§7: no producer means UNKNOWN with a reason, never a negative."""
        record = binding.binding(
            declaration(), observed=None, reference=REFERENCE,
            unobserved_because="no observer of device:work-fj is reachable "
                               "from this node")
        self.assertEqual(record["observation"], "UNKNOWN")
        self.assertEqual(record["version_state"], "UNKNOWN")
        self.assertIsNone(record["version"])
        self.assertEqual(record["probe_state"], "UNKNOWN")
        self.assertEqual(record["surface_state"], "UNKNOWN")
        self.assertIsNone(record["age_seconds"], "an age of zero is a claim")
        self.assertIn("no observer", record["observation_because"])

    def test_an_aged_observation_stops_being_evidence(self):
        aged = binding.binding(declaration(), observed=OBSERVED,
                              reference="2026-09-14T02:00:00Z")
        self.assertEqual(aged["observation"], "UNKNOWN")
        self.assertIsNone(aged["observed_at"])
        self.assertIn("freshness window", aged["observation_because"])

    def test_ageing_moves_an_observation_to_unknown_and_never_to_a_negative(self):
        """The one direction this could start lying.

        One fixture feature was AVAILABLE and one was UNAVAILABLE; both age to
        `UNKNOWN`. Neither becomes AVAILABLE, which would be inventing a yes, and
        the UNAVAILABLE one does not stay a no either — a runtime that had no
        workspace file ten hours ago may have one now.
        """
        aged = binding.binding(declaration(), observed=OBSERVED,
                               reference="2026-09-14T02:00:00Z")
        self.assertEqual(aged["features"]["mcp.client"]["state"], "UNKNOWN")
        self.assertEqual(aged["features"]["instruction.workspace"]["state"], "UNKNOWN")

    def test_ageing_does_not_touch_a_declaration_with_no_device_in_it(self):
        """A product invariant is not an observation, so it has no age.

        `skill.native` is true of the product; a binding that was measured ten
        hours ago says nothing about whether the product still declares it.
        Ageing it would make a contract fact expire on a device's clock.
        """
        aged = binding.binding(declaration(), observed=OBSERVED,
                               reference="2026-09-14T02:00:00Z")
        self.assertEqual(aged["features"]["skill.native"]["state"], "AVAILABLE")
        self.assertIn("product_invariants",
                      aged["features"]["skill.native"]["evidence"])

    def test_the_freshness_window_is_the_runtime_contracts_own(self):
        """Two assertions, and the second is the point.

        Comparing this number against the provider contract's would prove
        nothing: both windows are 900 today, so a module reading the wrong file
        and a module reading the right one agree. What can be shown is that the
        number comes from the runtime contract's path, and that the window a
        caller passes is the one that decides staleness.
        """
        import yaml
        declared = yaml.safe_load(
            (REPO / "abilities/agent-runtime-contract.yaml")
            .read_text(encoding="utf-8"))["bindings"]["observation"]["freshness"]
        self.assertEqual(binding.freshness_seconds(), declared["window_seconds"])
        self.assertTrue(binding.is_stale("2026-09-14T00:00:00Z", REFERENCE, window=1))
        self.assertFalse(binding.is_stale("2026-09-14T00:00:00Z", REFERENCE,
                                          window=10 ** 9))

    def test_a_binding_declared_at_a_time_is_a_string_in_the_record(self):
        """`fleet/registry.yaml` writes a bare date and YAML returns a `date`,
        which serialises on every path except the one that JSON-dumps it."""
        record = binding.binding(declaration(declared_at=datetime.date(2026, 9, 14)))
        self.assertIsInstance(record["declared_at"], str)
        self.assertEqual(record["declared_at"], "2026-09-14")


# ---------------------------------------------------------------------------
# §4/§12 — authority, and the migration gate
# ---------------------------------------------------------------------------


class MembershipTests(unittest.TestCase):
    def test_agreeing_lists_produce_nothing(self):
        self.assertEqual(
            binding.membership_findings(registry(device(agents=["codex"]))), [])

    def test_a_runtime_claimed_and_not_bound_is_a_mismatch(self):
        """`agents` says codex, `runtime_bindings` says nothing about it."""
        found = binding.membership_findings(
            registry(device(runtimes=("agent-runtime:claude-code",),
                            agents=["claude-code", "codex"])))
        self.assertEqual([f["code"] for f in found], ["BINDING_MEMBERSHIP_MISMATCH"])
        self.assertEqual(found[0]["direction"], "binding_missing")
        self.assertEqual(found[0]["runtime"], "codex")

    def test_a_runtime_bound_and_not_claimed_is_a_mismatch(self):
        """The other direction, and it is a different finding: this device
        resolves a runtime the older view cannot see. Claude is in both lists
        so that the one finding is the one being asserted."""
        found = binding.membership_findings(
            registry(device(runtimes=("agent-runtime:claude-code",
                                      "agent-runtime:codex"),
                            agents=["claude-code"])))
        self.assertEqual([f["code"] for f in found], ["BINDING_MEMBERSHIP_MISMATCH"])
        self.assertEqual(found[0]["direction"], "legacy_missing")
        self.assertEqual(found[0]["runtime"], "codex")

    def test_a_fleet_can_hold_both_directions_at_once(self):
        found = binding.membership_findings(
            registry(device(ref="device:a", runtimes=("agent-runtime:codex",),
                            agents=["claude-code"]),
                     device(ref="device:b", runtimes=("agent-runtime:claude-code",),
                            agents=["codex"])))
        self.assertEqual({f["direction"] for f in found},
                         {"binding_missing", "legacy_missing"})

    def test_a_binding_naming_a_runtime_that_does_not_exist_is_its_own_finding(self):
        found = binding.membership_findings(
            registry(device(runtimes=("agent-runtime:gemini-cli",), agents=[])))
        self.assertEqual([f["code"] for f in found], ["BINDING_NAMES_UNKNOWN_RUNTIME"])

    def test_the_real_registry_agrees_with_itself(self):
        self.assertEqual(binding.membership_findings(real_registry()), [])

    def test_the_parity_check_is_where_a_mismatch_becomes_a_failure(self):
        """`abilities/runtime_parity.py — check_registry()` is the caller, and
        this is the join: a gate nothing calls is a docstring."""
        parity = _load("serveros_runtime_parity", REPO / "abilities" / "runtime_parity.py")
        broken = registry(device(runtimes=("agent-runtime:claude-code",),
                                 agents=["claude-code", "codex"]))
        codes = {f["code"] for f in parity.report(contract(), broken)["findings"]}
        self.assertIn("BINDING_MEMBERSHIP_MISMATCH", codes)

    def test_the_parity_check_passes_a_fleet_whose_lists_agree(self):
        parity = _load("serveros_runtime_parity", REPO / "abilities" / "runtime_parity.py")
        codes = {f["code"] for f in parity.report(contract(), real_registry())["findings"]}
        self.assertNotIn("BINDING_MEMBERSHIP_MISMATCH", codes)


# ---------------------------------------------------------------------------
# §14/§18 — devices that are not here
# ---------------------------------------------------------------------------


class ReachabilityTests(unittest.TestCase):
    def test_each_device_declaring_both_runtimes_resolves_both(self):
        # Two, not three, since `device:server-beijing` was retired on
        # 2026-10-21; it and the two Windows machines were the only devices
        # declaring two runtimes and it was one of them. Tokyo and the Dell LLM
        # Lab host declare `claude-code` alone, so "every device has two" was
        # never the property here — "two declarations resolve to two bindings"
        # is, and it is asserted per device rather than assumed.
        for ref in ("device:work-fj", "device:personal-dell"):
            listed = binding.list_bindings(real_registry(), ref, reference=REFERENCE)
            self.assertEqual(listed["state"], "BOUND", ref)
            self.assertEqual(listed["count"], 2, ref)
            self.assertTrue(all(row["binding_ref"].startswith("binding:")
                                for row in listed["bindings"]))

    def test_an_unreached_device_has_bindings_and_no_observation(self):
        """The declared half survives the machine being offline. Reporting it
        as "no binding" is how offline becomes never-enrolled."""
        listed = binding.list_bindings(
            real_registry(), "device:personal-dell", reference=REFERENCE,
            unobserved_because="this node has no route to device:personal-dell")
        self.assertEqual(listed["count"], 2)
        for row in listed["bindings"]:
            self.assertEqual(row["observation"], "UNKNOWN")
            self.assertEqual(row["version_state"], "UNKNOWN")
            self.assertIsNone(row["version"])
            self.assertIn("no route", row["observation_because"])
            self.assertEqual(row["declared_at"], "2026-09-14")

    def test_a_device_nobody_bound_says_so_rather_than_returning_an_empty_list(self):
        stripped = copy.deepcopy(real_registry())
        for entry in stripped["devices"]:
            entry.pop("runtime_bindings", None)
        listed = binding.list_bindings(stripped, "device:work-fj", reference=REFERENCE)
        self.assertEqual(listed["state"], "NOT_DECLARED")
        self.assertEqual(listed["bindings"], [])
        self.assertIn("statement about the registry", listed["state_because"])

    def test_a_device_ref_that_is_not_a_device_is_not_an_unbound_device(self):
        listed = binding.list_bindings(real_registry(), "device:typo",
                                       reference=REFERENCE)
        self.assertEqual(listed["state"], "NOT_DECLARED")
        self.assertIn("not a device in this registry", listed["state_because"])

    def test_only_the_host_that_probed_has_an_observation(self):
        """§14: one device gets a binding and no version. Filling its column
        from the row of the device that DID probe is the failure this asserts
        against — the probing device used to be `device:server-beijing`, retired
        on 2026-10-21, so it is the other device that declares both runtimes."""
        real = real_registry()
        here = binding.list_bindings(real, "device:personal-dell",
                                     observed={"agent-runtime:codex": OBSERVED},
                                     reference=REFERENCE)
        there = binding.list_bindings(real, "device:work-fj", reference=REFERENCE,
                                      unobserved_because="no current observer")
        by_ref = {row["runtime_ref"]: row for row in here["bindings"]}
        self.assertEqual(by_ref["agent-runtime:codex"]["version"], "codex-cli 0.153.4")
        self.assertEqual(by_ref["agent-runtime:claude-code"]["observation"], "UNKNOWN")
        for row in there["bindings"]:
            self.assertIsNone(row["version"], row["runtime_ref"])


# ---------------------------------------------------------------------------
# §15 — the levels are a split, not a differently-sorted copy
# ---------------------------------------------------------------------------


class LevelTests(unittest.TestCase):
    def test_l1_costs_nothing_and_l2_needs_a_device(self):
        record = binding.binding(declaration(), observed=OBSERVED, reference=REFERENCE)
        self.assertEqual(set(record["levels"]["L1"]),
                         {"binding_ref", "runtime_ref", "state"})
        self.assertEqual(record["levels"]["L1"]["state"], "BOUND")
        self.assertIn("features", record["levels"]["L2"])
        self.assertIn("provenance", record["levels"]["L3"])

    def test_l1_says_bound_unobserved_rather_than_bound(self):
        record = binding.binding(declaration(), reference=REFERENCE)
        self.assertEqual(record["levels"]["L1"]["state"], "BOUND_UNOBSERVED")

    def test_l3_carries_the_same_observation_the_record_does(self):
        record = binding.binding(declaration(), observed=OBSERVED, reference=REFERENCE)
        self.assertEqual(record["levels"]["L3"]["version"], record["version"])
        self.assertEqual(record["levels"]["L3"]["probe"], record["probe"])

    def test_the_levels_do_not_repeat_the_whole_record(self):
        record = binding.binding(declaration(), observed=OBSERVED, reference=REFERENCE)
        served = set().union(*(set(level) for level in record["levels"].values()))
        self.assertLess(len(served), len(record))

    def test_the_record_is_json_serialisable(self):
        """It is served by the CLI with `--json` and by an MCP surface, so a
        value that survives one path and not the other is a crash in waiting."""
        import json
        record = binding.binding(declaration(declared_at=datetime.date(2026, 9, 14)),
                                 observed=OBSERVED, reference=REFERENCE)
        self.assertIn("codex-cli 0.153.4", json.dumps(record))


# ---------------------------------------------------------------------------
# §19 — the mutations, each of which must go red
# ---------------------------------------------------------------------------


class MutationTests(unittest.TestCase):
    """Four plausible wrong implementations, run against the same input as the
    right one, asserting they differ. A mutation test that only asserts the good
    value is an assertion with a story attached."""

    def test_flattening_unknown_into_unavailable_changes_the_answer(self):
        record = binding.binding(declaration(), reference=REFERENCE)
        honest = record["features"]["gui"]["state"]
        flattened = {"UNKNOWN": "UNAVAILABLE"}.get(honest, honest)
        self.assertEqual(honest, "UNKNOWN")
        self.assertNotEqual(honest, flattened)

    def test_inferring_a_feature_from_the_name_changes_the_answer(self):
        """The naive implementation, spelled out beside the real one."""
        naive = lambda name: "AVAILABLE" if name == "gui" else "UNKNOWN"
        self.assertEqual(binding.feature("codex", "gui", None)[0], "UNKNOWN")
        self.assertEqual(naive("gui"), "AVAILABLE")

    def test_letting_the_two_membership_lists_drift_hides_a_finding(self):
        """A check that preferred `runtime_bindings` would make the legacy list
        unfalsifiable; one that preferred `agents` would undo the migration."""
        drifted = registry(device(runtimes=("agent-runtime:claude-code",),
                                  agents=["claude-code", "codex"]))
        self.assertTrue(binding.membership_findings(drifted))
        self.assertFalse(binding.membership_findings(
            registry(device(runtimes=("agent-runtime:claude-code",),
                            agents=["claude-code"]))))

    def test_consuming_a_version_with_no_observed_at_is_refused(self):
        """The mutation is `{"version": "0.153.4"}` reaching the record because
        the caller remembered it. The guard is the raise; the second assertion is
        that the record it would have produced is not otherwise reachable."""
        with self.assertRaises(binding.BindingError):
            binding.observation(runtime_id="codex", probe="cli-version",
                                version="0.153.4")
        record = binding.binding(declaration(), reference=REFERENCE)
        self.assertNotEqual(record["version_state"], "FACT")


if __name__ == "__main__":
    unittest.main()
