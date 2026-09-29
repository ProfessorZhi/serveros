#!/usr/bin/env python3
"""The Project Testing Contract: loading, resolving, and drift.

WHY THIS FILE EXISTS
--------------------
`serverosctl test resolve` is a command whose whole output is a claim about where
each value came from. A resolver that silently drops an override, or that lets a
run lower something an item owns, still prints a table — a complete-looking one,
with every row filled in. Nothing about the output says the answer is wrong, so
the thing that has to say it is a test that asks for the bad configuration on
purpose and requires a refusal.

WHAT IS FIXTURED, AND WHAT THAT COSTS
-------------------------------------
Everything. The workspace, the control plane, the declared schema, the ability
repo holding L1. A test that read this node's live `ops/workspace/serveros/`
would be measuring one afternoon's editing, and would break the first time the
control plane is reorganised — which is the opposite of what a contract test is
for. `WORKSPACE_ROOT` and `SERVEROS_ABILITY_REPO` are the two names this
codebase already uses to point at another work area, so the fixture sets those
rather than inventing a third way.

The vocabulary fixture is deliberately NOT a copy of the real declaration. It
declares a smaller set of `mutability` values and the same three-valued property
is tested with `criticality` absent — because the property under test is that the
resolver reads its legal values from the declaration instead of holding a second
copy of them in Python. A fixture identical to the real file could not tell those
two designs apart.

NEGATIVE CONTROLS
-----------------
Four of these tests exist because of a way this code was wrong first:

* stacks are not summed across files (the first version added up every
  `mem_limit` under `test-envs/` and reported 66 GiB — a true number and a false
  conclusion);
* a stack with no `mem_limit` at all is not a stack within budget;
* the run layer may not change what an item is tested with;
* the declared schema's vocabulary is what refuses an unknown `mutability`.
"""
from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


test_contract = _load("serveros_runtime_testing_contract",
                      ROOT / "runtime" / "testing_contract.py")
resolver = _load("serveros_context_resolver_for_test_contract",
                 ROOT / "context" / "resolver.py")
cli = _load("serveros_runtime_cli_for_test_contract", ROOT / "runtime" / "cli.py")

#: The declaration the resolver must obey. Smaller than the real one on purpose:
#: see the module docstring.
SCHEMA = """\
schema: serveros/schema-contract.v1
name: serveros/project-testing/v1
required: [schema, project, repo, default_tiers, evidence_tiers,
           containerized_floor, test_surfaces, project_semantics,
           e2e_defaults, knobs, resource_limits]
header:
  required: [schema, project, updated, declared_by]
  computed_by_reader: [revision]
knobs:
  required_metadata: [default, mutability, criticality, reason]
  mutability:
    fixed: nobody may change it
    project-default: a long-term adjustment
    item-override: one work item may change it
    run-override: one run may choose it
  criticality:
    informational: nothing to explain
    important: record it
    acceptance-critical: give a reason
    safety-critical: give a reason and the evidence
default_tiers:
  required: [bugfix, feature, ui, unit]
evidence_tiers:
  known: [E0, E1, E2, E3, E4]
environment_capabilities:
  known:
    real-chain: it really serves
    postgres: a relational store is on the chain
    redis: shared state crosses processes
    multi-worker: more than one worker is serving
    browser: a real browser can reach it
containerized_floor:
  required: [requires, why, declared_by]
violations:
  INVALID_OVERRIDE: a lower layer broke an upper constraint
  REFUSED: the request is meaningless under this contract
  MISSING_REASON: a lowering arrived without a reason
  CAPABILITY_UNMET: the chosen environment cannot provide what this level needs
  TEST_PROFILE_DRIFT: what was observed disagrees with what is declared
resolution_order: [global-invariant, reusable-profile, project-default,
                   item-override, run-override]
resource_limits:
  required: [stack_memory_hard_max_bytes]
"""

POLICY = """\
schema: serveros/testing-policy/v1
name: fixture policy
invariants:
  - id: no-secret-in-evidence
    title: evidence carries no credential
    rule: a test environment definition holds no token, key or password
    why: evidence is copied, projected and shown to other audiences
    enforced_by: fixture
  - id: ab-or-explicit-na
    title: A/B or an explicit N/A
    rule: a behaviour change needs a baseline or a written why-not
    why: an absent baseline and an undecided one must not look the same
    enforced_by: fixture
"""

CONTRACT = """\
schema: serveros/project-testing/v1
project: demo
updated: "2026-09-29"
declared_by: fixture
repo:
  canonical: demo
  test_envs: test-envs/demo
default_tiers:
  bugfix: E3
  feature: E4
  ui: E4
  unit: E2
evidence_tiers:
  E0: {requires: [], why: a declaration only, so no environment is needed}
  E1: {requires: [], why: it ran once and nobody independently checked}
  E2: {requires: [], why: a replayable judgement on constructed inputs}
  E3: {requires: [real-chain, postgres, redis, multi-worker],
       why: the real stack this project's inputs come from}
  E4: {requires: [real-chain, postgres, redis, multi-worker, browser],
       why: E3 plus the entry a person walks}
containerized_floor:
  requires: [postgres]
  why: a containerised environment here keeps its production shape
  declared_by: fixture
test_surfaces:
  unit: {requires: [], what: pure functions}
  proxy_api: {requires: [real-chain, postgres], what: the HTTP surface}
project_semantics:
  dynamic_models: {authority: database}
  tracked_config: {role: bootstrap-only}
  model_catalogue: {must_not_be_hardcoded: true}
  admin_ui: {must_support_model_crud: true}
  multi_worker: {production_relevant: true}
e2e_defaults:
  # 不点名档位。满足这一级的那一套由能力匹配从 L1 里选出来 —— 点名会让
  # 档位表里「我提供什么」变成一句无人核对的话。
  evidence_tier: E4
  test_env:
    dir: test-envs/demo/stack
    compose: docker-compose.yml
knobs:
  workers:
    default: 2
    minimum: 1
    recommended_max: 8
    mutability: item-override
    criticality: acceptance-critical
    reason: production runs more than one worker
    lowering_requires_reason: true
    # `implies_capability` 只是双向对照的**出处**（哪条旋钮说的话对应哪项能力），
    # 解析器不读它：要求集来自契约的 `evidence_tiers` / `test_surfaces` / 地板。
    implies_capability: multi-worker
  stack_memory_bytes:
    default: 10737418240
    mutability: item-override
    criticality: safety-critical
    reason: a resource promise to the rest of the host
  runtime_flavour:
    default: postgres
    mutability: fixed
    criticality: acceptance-critical
    reason: the model authority is the database
  fault_injection:
    default: "off"
    mutability: run-override
    criticality: important
    reason: a single run may narrow what it injects; an item may not fix it for its whole life
  release_tier:
    default: stable
    mutability: project-default
    criticality: informational
    reason: which tier of the project this is; adjusted by editing the contract
  distributed_state_redis:
    default: enabled
    mutability: fixed
    criticality: acceptance-critical
    reason: shared state across workers is a first-class test surface
    implies_capability: redis
  model_authority:
    default: database
    mutability: fixed
    criticality: acceptance-critical
    reason: catalogue authority lives in the database
resource_limits:
  stack_memory_hard_max_bytes: 10737418240
"""

#: L1, written by test-management. Each profile declares **what it can provide**;
#: nothing here names a tier, and the project contract does not name a profile —
#: the two meet on capabilities instead. One knob is pinned, above the project,
#: so the resolution order is observable rather than asserted in prose.
PROFILES = """\
schema: serveros/reusable-test-profiles/v1
profiles:
  demo-unit:
    what: pure functions, no environment at all
    capabilities: []
  demo-integration:
    what: the proxy HTTP surface on the real stack
    capabilities: [real-chain, postgres, redis, multi-worker]
  demo-full-e2e:
    what: the whole stack, browser included
    capabilities: [real-chain, postgres, redis, multi-worker, browser]
    env:
      # 锚在契约的 `repo.test_envs` 上，与契约点名的那一套是同一个目录。
      dir: stack
      compose: docker-compose.yml
    knobs:
      workers: {value: 3}
  demo-narrow:
    what: one process, for a minimal reproduction
    narrowing: true
    capabilities: [real-chain]
"""

COMPOSE_REDIS_AND_LIMITS = """\
services:
  proxy:
    image: demo/proxy:latest
    command: ["--num_workers=2"]
    mem_limit: 2g
    environment:
      STORE_MODEL_IN_DB: "True"
  cache:
    image: redis:7-alpine
    mem_limit: 512m
  db:
    image: postgres:16
    mem_limit: 1g
"""

#: A second stack, larger than the named one. It exists so that a resolver which
#: summed limits across files would produce a number above the 10 GiB cap — the
#: failure this fixture has to be able to detect. Without it, summing and not
#: summing give the same verdict and the test proves nothing.
COMPOSE_SECOND_STACK = """\
services:
  proxy:
    image: demo/other-proxy:latest
    mem_limit: 8g
  cache:
    image: redis:7-alpine
    mem_limit: 1g
"""


class Fixture:
    """A workspace whose control plane and environment definitions are known."""

    def __init__(self, compose: str | None = COMPOSE_REDIS_AND_LIMITS,
                 profiles: str | None = PROFILES,
                 extra_stack: str | None = None,
                 extra_stack_name: str = "other/docker-compose.yml",
                 profiles_layout: str = "payload",
                 schema: str = SCHEMA):
        self.root = Path(tempfile.mkdtemp(prefix="test-contract-"))
        self.workspace = self.root / "workspace"
        control = self.workspace / "ops" / "workspace" / "serveros"
        (control / "schemas").mkdir(parents=True)
        (control / "testing").mkdir(parents=True)
        (control / "schemas" / "project-testing.schema.yaml").write_text(schema, encoding="utf-8")
        (control / "testing" / "_policy.yaml").write_text(POLICY, encoding="utf-8")
        (control / "testing" / "demo.yaml").write_text(CONTRACT, encoding="utf-8")

        stack = self.workspace / "test-envs" / "demo" / "stack"
        stack.mkdir(parents=True)
        if compose is not None:
            (stack / "docker-compose.yml").write_text(compose, encoding="utf-8")
        if extra_stack is not None:
            path = self.workspace / "test-envs" / "demo" / extra_stack_name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(extra_stack, encoding="utf-8")

        self.ability_repo = self.root / "ability-management"
        skill = self.ability_repo / "skills" / "test-management"
        targets = {"payload": [skill / "payload"],
                   "installed": [skill],
                   "both": [skill / "payload", skill]}[profiles_layout]
        if profiles is not None:
            for target in targets:
                target.mkdir(parents=True, exist_ok=True)
                (target / "profiles.yaml").write_text(profiles, encoding="utf-8")
        self.env = mock.patch.dict(os.environ, {
            "WORKSPACE_ROOT": str(self.workspace),
            "SERVEROS_ABILITY_REPO": str(self.ability_repo),
        })
        self.env.start()

    def close(self):
        self.env.stop()
        shutil.rmtree(self.root, ignore_errors=True)

    @property
    def roots(self):
        return resolver.Roots.from_env()

    def contract(self) -> dict:
        loaded = test_contract.load_contract(self.roots, "demo")
        assert loaded.ok, loaded.reason
        return loaded.declaration

    def profiles(self) -> dict:
        loaded = test_contract.load_profiles(self.roots)
        assert loaded.ok, loaded.reason
        return loaded.declaration

    def vocabulary(self) -> dict:
        return test_contract.vocabulary(test_contract.load_schema_contract(self.roots))

    def resolve(self, **kwargs) -> dict:
        loaded = test_contract.load_contract(self.roots, "demo")
        policy = test_contract.load_policy(self.roots)
        profiles = test_contract.load_profiles(self.roots)
        schema = test_contract.load_schema_contract(self.roots)
        return test_contract.resolve(
            loaded.declaration,
            policy.declaration,
            profiles.declaration,
            vocab=test_contract.vocabulary(schema),
            **kwargs)

    def run_cli(self, *argv) -> tuple[int, dict | str]:
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            code = cli.main(list(argv))
        text = out.getvalue()
        try:
            return code, json.loads(text)
        except ValueError:
            return code, text

    def current(self, **kwargs) -> dict:
        return test_contract.observe(self.roots, self.contract())


class ContractTestCase(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture()
        self.addCleanup(self.fx.close)


# --------------------------------------------------------------------- 装载

class LoadingTest(ContractTestCase):

    def test_the_revision_is_computed_from_the_bytes_not_declared_in_the_file(self):
        """A revision written by hand is a lie from the first edit onwards."""
        loaded = test_contract.load_contract(self.fx.roots, "demo")
        self.assertTrue(loaded.ok, loaded.reason)
        self.assertRegex(loaded.revision, r"^sha256:[0-9a-f]{64}$")
        self.assertNotIn("revision", loaded.declaration)

        path = loaded.path
        original = path.read_text(encoding="utf-8")
        again = test_contract.load_contract(self.fx.roots, "demo").revision
        self.assertEqual(loaded.revision, again, "the same bytes must give the same revision")

        # A change that touches a VALUE, not a comment, has to move it.
        path.write_text(original.replace("default: 2", "default: 4"), encoding="utf-8")
        moved = test_contract.load_contract(self.fx.roots, "demo").revision
        self.assertNotEqual(loaded.revision, moved)
        path.write_text(original, encoding="utf-8")

    def test_a_declared_revision_is_not_believed(self):
        """A hand-written `revision:` is a number that was true once.

        Honouring it would make the value a TestRun copies into
        `testing_contract_revision` a claim about an earlier draft of the file
        — which is indistinguishable from a correct one at the point of use.
        """
        stale = "sha256:" + "a" * 64
        path = test_contract.contract_path(self.fx.roots, "demo")
        fresh = CONTRACT.replace("declared_by: fixture", f"declared_by: fixture\nrevision: {stale}")
        self.assertIn(stale, fresh)
        path.write_text(fresh, encoding="utf-8")

        loaded = test_contract.load_contract(self.fx.roots, "demo")
        self.assertTrue(loaded.ok, loaded.reason)
        self.assertNotEqual(loaded.revision, stale)
        self.assertEqual(loaded.revision,
                         "sha256:" + hashlib.sha256(fresh.encode("utf-8")).hexdigest())

    def test_a_document_declaring_another_schema_is_not_a_contract(self):
        path = test_contract.contract_path(self.fx.roots, "demo")
        path.write_text(CONTRACT.replace("serveros/project-testing/v1", "something/else/v9"),
                        encoding="utf-8")
        loaded = test_contract.load_contract(self.fx.roots, "demo")
        self.assertFalse(loaded.ok)
        self.assertIn("something/else/v9", loaded.reason)

    def test_an_unreadable_contract_is_not_an_empty_one(self):
        """`{}` and "could not be read" are different answers; only one is a fact."""
        loaded = test_contract.load_contract(self.fx.roots, "no-such-project")
        self.assertFalse(loaded.ok)
        self.assertIsNone(loaded.declaration)
        self.assertIsNotNone(loaded.reason)

    def test_only_underscore_free_names_are_offered_as_projects(self):
        names = test_contract.discover_projects(self.fx.roots)
        self.assertEqual(names, ["demo"])

    def test_the_L1_profiles_are_read_from_the_declared_ability_repo(self):
        loaded = test_contract.load_profiles(self.fx.roots)
        self.assertTrue(loaded.ok, loaded.reason)
        self.assertIn("demo-full-e2e", loaded.declaration["profiles"])

    def test_a_missing_ability_repo_is_reported_not_assumed_away(self):
        with mock.patch.dict(os.environ, {"SERVEROS_ABILITY_REPO": str(self.fx.root / "nope")}):
            loaded = test_contract.load_profiles(self.fx.roots)
        self.assertFalse(loaded.ok)
        self.assertIn("profiles.yaml", loaded.reason)

    def test_a_deployed_skill_directory_is_read_without_the_payload_level(self):
        """An installed ability is `skills/<name>/profiles.yaml`, not `.../payload/profiles.yaml`.

        `_copy_payload` deploys the payload's *contents*; it does not carry the
        `payload/` directory along. So a reader that only knows the repository
        layout works all through development and reports "no such file" the
        first time it runs against something that was installed — and that
        message reads like the file is genuinely absent rather than like the
        reader was looking in one of two places.
        """
        fx = Fixture(profiles_layout="installed")
        self.addCleanup(fx.close)
        loaded = test_contract.load_profiles(fx.roots)
        self.assertTrue(loaded.ok, loaded.reason)
        self.assertNotIn("/payload/", str(loaded.path))
        self.assertIn("demo-full-e2e", loaded.declaration["profiles"])

    def test_the_checkout_layout_wins_when_both_are_present(self):
        """Both layouts exist → the answer is decided, not whichever came first.

        A `glob` that happened to return the installed copy would read a file
        nobody is editing, and every review of the checkout would be reviewing
        something else. So the two are made to differ, and the test says which
        one the reader must take.
        """
        repo_layout = PROFILES.replace("demo-full-e2e", "checkout-only-profile")
        fx = Fixture(profiles=repo_layout, profiles_layout="both")
        self.addCleanup(fx.close)

        installed = fx.ability_repo / "skills" / "test-management" / "profiles.yaml"
        self.assertTrue(installed.is_file(), "两个布局都要在场，这条才有内容")
        installed.write_text(PROFILES, encoding="utf-8")

        loaded = test_contract.load_profiles(fx.roots)
        self.assertTrue(loaded.ok, loaded.reason)
        self.assertIn("checkout-only-profile", loaded.declaration["profiles"])
        self.assertNotIn("demo-full-e2e", loaded.declaration["profiles"])


# --------------------------------------------------------------------- 解析

class ResolutionTest(ContractTestCase):

    def test_every_value_carries_its_source_and_its_declarer(self):
        result = self.fx.resolve(project="demo")
        self.assertTrue(result["resolved"], result["violations"])
        worker = result["values"]["workers"]
        self.assertEqual(worker["source"], "project-default")
        self.assertEqual(worker["declared_by"], "demo/testing-contract")
        self.assertEqual(worker["criticality"], "acceptance-critical")
        fixed = result["values"]["runtime_flavour"]
        self.assertEqual(fixed["value"], "postgres")
        self.assertEqual(fixed["mutability"], "fixed")

    def test_the_reusable_profile_sits_above_the_project_default(self):
        """L1 pins workers at 3; the project's default is 2. The profile wins."""
        result = self.fx.resolve(project="demo", profile_name="demo-full-e2e")
        worker = result["values"]["workers"]
        self.assertEqual(worker["value"], 3)
        self.assertEqual(worker["source"], "reusable-profile")
        self.assertEqual(worker["declared_by"], "test-management/demo-full-e2e")

    def test_a_reusable_profile_cannot_cross_the_projects_hard_ceiling(self):
        """A ceiling with a layer it does not bind is not a ceiling.

        The profile is the recipe for the environment, so it narrows things —
        that is what it is for. It is not the place where the project's
        resource promise to the rest of the machine gets rewritten, and a pin
        that crossed the ceiling would otherwise be the one path into the
        effective environment that no check guarded.
        """
        greedy = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-full-e2e:
                what: the whole stack, generously
                capabilities: [real-chain, postgres, redis, multi-worker, browser]
                knobs:
                  stack_memory_bytes: {value: 34359738368}
            """)
        fx = Fixture(profiles=greedy)
        self.addCleanup(fx.close)
        result = fx.resolve(project="demo", profile_name="demo-full-e2e")
        self.assertFalse(result["resolved"])
        violation = result["violations"][0]
        self.assertEqual(violation["code"], "INVALID_OVERRIDE")
        self.assertEqual(violation["layer"], "reusable-profile")
        self.assertEqual(result["values"]["stack_memory_bytes"]["value"], 10737418240,
                         "the contract's own value stands, not the profile's")

    def test_a_profile_that_lowers_a_guarded_knob_has_to_say_why_there(self):
        """Being reviewed is not the same as having written it down.

        `workers` is acceptance-critical with `lowering_requires_reason`: the
        contract says a single-worker run cannot support the cross-worker
        claims. A profile is allowed to be the single-worker shape — that is
        what profiles are for — but the reason belongs in the profile, because
        the profile is the thing a reviewer signs.
        """
        silent = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-integration:
                what: the proxy HTTP surface, one process
                capabilities: [real-chain, postgres, redis]
                knobs:
                  workers: {value: 1}
            """)
        fx = Fixture(profiles=silent)
        self.addCleanup(fx.close)
        result = fx.resolve(project="demo", profile_name="demo-integration")
        self.assertFalse(result["resolved"])
        violation = result["violations"][0]
        self.assertEqual(violation["code"], "MISSING_REASON")
        self.assertEqual(violation["layer"], "reusable-profile")
        self.assertEqual(result["values"]["workers"]["value"], 2,
                         "an unjustified pin does not become the value")

        justified = silent.replace("workers: {value: 1}",
                                  'workers: {value: 1, reason: the proxy surface is one process}')
        fx2 = Fixture(profiles=justified)
        self.addCleanup(fx2.close)
        result2 = fx2.resolve(project="demo", profile_name="demo-integration")
        self.assertTrue(result2["resolved"], result2["violations"])
        self.assertEqual(result2["values"]["workers"]["value"], 1)
        self.assertEqual(result2["values"]["workers"]["source"], "reusable-profile")

    def test_the_profile_the_matcher_chose_is_applied_and_not_only_reported(self):
        """Naming the profile and arriving at it by matching must give the same values.

        The reported `profile` is the same in both cases, so if only an explicit
        `--profile` applied the pins, two runs reading the same line would come
        out different — with nothing in either answer saying which one ran.
        """
        mid = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-integration:
                what: the proxy HTTP surface on the real stack
                capabilities: [real-chain, postgres, redis, multi-worker]
                knobs:
                  fault_injection: {value: "on"}
              demo-full-e2e:
                what: the whole stack, browser included
                capabilities: [real-chain, postgres, redis, multi-worker, browser]
            """)
        fx = Fixture(profiles=mid)
        self.addCleanup(fx.close)

        by_match = fx.resolve(project="demo")
        by_name = fx.resolve(project="demo", profile_name="demo-integration")
        self.assertEqual(by_match["profile"], "demo-integration")
        self.assertEqual(by_match["profile_source"], "matched")
        self.assertEqual(by_match["values"]["fault_injection"]["value"], "on")
        self.assertEqual(by_match["values"]["fault_injection"]["source"],
                         "reusable-profile")
        self.assertEqual(by_match["values"]["fault_injection"],
                         by_name["values"]["fault_injection"])

    def test_a_pin_on_a_knob_the_contract_does_not_have_is_not_a_no_op(self):
        """L1 and L2 have to be describing the same environment.

        A pin on a knob nobody declared changes nothing, so the resolved values
        are not the ones the profile promised — and they look exactly like the
        ones it did promise.
        """
        drifting = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-full-e2e:
                what: the whole stack
                capabilities: [real-chain, postgres, redis, multi-worker, browser]
                knobs:
                  workers: {value: 3}
                  redis_maxmemory: {value: 4294967296}
            """)
        fx = Fixture(profiles=drifting)
        self.addCleanup(fx.close)
        result = fx.resolve(project="demo", profile_name="demo-full-e2e")
        self.assertFalse(result["resolved"])
        violation = [v for v in result["violations"]
                     if v["code"] == "REFUSED" and v.get("layer") == "reusable-profile"]
        self.assertEqual(len(violation), 1, result["violations"])
        self.assertIn("redis_maxmemory", violation[0]["message"])
        self.assertIn("demo-full-e2e", violation[0]["message"])

    def test_a_profile_bound_to_another_project_is_refused(self):
        """A profile that says which project it is for is checked against it.

        Without the check the field would be a comment: the values would resolve
        exactly as though the binding held, and the one document that could have
        caught the mix-up would be the one nobody read.
        """
        other = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-full-e2e:
                project: somebody-else
                what: the whole stack
                capabilities: [real-chain, postgres, redis, multi-worker, browser]
            """)
        fx = Fixture(profiles=other)
        self.addCleanup(fx.close)
        result = fx.resolve(project="demo", profile_name="demo-full-e2e")
        self.assertFalse(result["resolved"])
        violation = result["violations"][0]
        self.assertEqual(violation["code"], "REFUSED")
        self.assertEqual(violation["layer"], "reusable-profile")
        self.assertIn("somebody-else", violation["message"])

        # The same profile with no binding declared is not refused: silence about
        # the project is not a claim about a different one.
        unbound = other.replace("    project: somebody-else\n", "")
        self.assertNotIn("somebody-else", unbound, "the binding really was removed")
        fx2 = Fixture(profiles=unbound)
        self.addCleanup(fx2.close)
        self.assertTrue(fx2.resolve(project="demo", profile_name="demo-full-e2e")["resolved"])

    def test_a_named_profile_that_L1_does_not_define_is_reported(self):
        result = self.fx.resolve(project="demo", profile_name="not-in-l1")
        self.assertFalse(result["profile_known"])
        self.assertFalse(result["resolved"])
        self.assertEqual([v["code"] for v in result["violations"]], ["REFUSED"])
        self.assertIn("not-in-l1", result["violations"][0]["message"])

    def test_the_profile_is_picked_by_what_it_provides_not_by_a_name_in_the_contract(self):
        """The two authorities meet on capabilities, and the contract names no profile.

        The contract answers "how far this change class has to be proven"; each
        level answers "what the environment must therefore be able to do"; the
        profile table answers "what I can do". Nothing in any of the three names
        a profile, so the answer to "which environment" is a subset match — and
        the smallest cover wins, because a bigger environment than the claim
        needs is a cost with no matching evidence.
        """
        small = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-unit:
                what: pure functions, no environment at all
                capabilities: []
              demo-proxy:
                what: the proxy HTTP surface on the real stack
                capabilities: [real-chain, postgres, redis, multi-worker]
              demo-full-e2e:
                what: the whole stack, browser included
                capabilities: [real-chain, postgres, redis, multi-worker, browser]
            """)
        fx = Fixture(profiles=small)
        self.addCleanup(fx.close)

        # bugfix → E3 要求在真实栈上对照，但不需要人走的那条入口。
        e3 = fx.resolve(project="demo")
        self.assertEqual(e3["profile"], "demo-proxy")
        self.assertEqual(e3["profile_source"], "matched")
        self.assertTrue(e3["profile_known"])
        self.assertEqual(e3["capability_check"], "ok")
        self.assertEqual(e3["verification"]["evidence_tier"], "E3")
        self.assertEqual(e3["verification"]["tier_source"], "contract-default:bugfix")
        self.assertEqual([c["profile"] for c in e3["environment_profile"]["candidates"]],
                         ["demo-proxy", "demo-full-e2e"],
                         "落选者也留名：为什么不是那一档要能被核对")

        # feature → E4 多要一个浏览器，于是答案换了一档。
        e4 = fx.resolve(project="demo", change_class="feature")
        self.assertEqual(e4["profile"], "demo-full-e2e")
        self.assertEqual(e4["verification"]["tier_source"], "contract-default:feature")

        # unit → E2 不要求任何能力，最小覆盖是「什么都不建」的那一档。
        e2 = fx.resolve(project="demo", change_class="unit")
        self.assertEqual(e2["profile"], "demo-unit")
        self.assertEqual(e2["requirement"]["required"], [])
        self.assertEqual(e2["verification"]["evidence_tier"], "E2")

    def test_a_level_no_profile_can_reach_is_not_answered_by_the_nearest_one(self):
        """「没有环境能满足 E4」must be checkable, so the losers are named.

        The two ways to get this wrong are to fall back to the largest profile
        there is, and to report the level as though it had been reached. Both
        produce a run that claims more than the environment can support.
        """
        small = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-proxy:
                what: the proxy HTTP surface on the real stack
                capabilities: [real-chain, postgres, redis, multi-worker]
            """)
        fx = Fixture(profiles=small)
        self.addCleanup(fx.close)

        result = fx.resolve(project="demo", change_class="feature")
        self.assertFalse(result["resolved"])
        self.assertIsNone(result["profile"])
        self.assertIsNone(result["profile_source"])
        self.assertEqual(result["capability_check"], "unmet")
        violation = [v for v in result["violations"] if v["code"] == "CAPABILITY_UNMET"][0]
        self.assertEqual(violation["detail"]["required"]["browser"], ["tier:E4"])
        rejected = result["environment_profile"]["rejected"]
        self.assertEqual([r["profile"] for r in rejected], ["demo-proxy"])
        self.assertEqual(rejected[0]["missing"], ["browser"])

        # 同一份档位表，只把要证明的级别降下来，答案就有了 —— 所以上一条
        # 报的确实是「这一级」而不是「这份档位表」。
        lower = fx.resolve(project="demo", change_class="bugfix")
        self.assertTrue(lower["resolved"], lower["violations"])
        self.assertEqual(lower["profile"], "demo-proxy")

    def test_a_surface_and_a_level_together_ask_for_the_union_and_say_who_asked(self):
        """Two questions, one requirement set — and each capability keeps its source.

        "Why does this run need redis" has to be answerable as "because E3
        requires it" or "because the distributed_state surface does". An answer
        of "because the environment has one" cannot be argued with, and cannot
        release the environment when the need goes away.
        """
        result = self.fx.resolve(project="demo", change_class="bugfix",
                                 surfaces=["proxy_api"])
        required = {entry["capability"]: entry["required_by"]
                    for entry in result["requirement"]["required"]}
        self.assertEqual(sorted(required),
                         ["multi-worker", "postgres", "real-chain", "redis"])
        self.assertEqual(required["postgres"], ["tier:E3", "surface:proxy_api"])
        self.assertEqual(required["multi-worker"], ["tier:E3"])
        self.assertEqual(result["requirement"]["surfaces"], ["proxy_api"])

    def test_a_surface_the_contract_does_not_declare_is_refused(self):
        """A surface nobody described has no requirements — which is not the
        same as requiring nothing, and only one of those is safe to run."""
        result = self.fx.resolve(project="demo", surfaces=["nobody_described_this"])
        self.assertFalse(result["resolved"])
        violation = [v for v in result["violations"] if v["code"] == "REFUSED"][0]
        self.assertEqual(violation["detail"]["surface"], "nobody_described_this")
        self.assertEqual(result["requirement"]["surfaces"], [],
                         "an unheard-of surface is dropped, not kept as a requirement")

    def test_one_capability_written_as_a_bare_scalar_is_one_capability(self):
        """`requires: real-chain` 是一个能力，不是十个。

        这个形状很容易写出来 —— YAML 里单元素列表常被写成裸标量 —— 而且它错了
        以后**看起来是对的**：把字符串当序列读，`r`/`e`/`a`… 就成了十个能力名，
        没有一档提供得了它们，于是解析器报出一句真的拒绝、带着一个假的理由
        （「没有环境满足要求」）。档位那一侧的同一个形状反过来：十个人造能力
        谁都不要求，于是那一档被选中，而它其实什么都没提供。
        """
        contract = self.fx.contract()
        contract["evidence_tiers"]["E3"]["requires"] = "real-chain"
        # `Fixture.resolve` 自己装载契约，所以这一条直接调解析器 —— 变异的是
        # **契约文档**，不是工作区里的那份文件。
        result = test_contract.resolve(contract, None, self.fx.profiles(),
                                       project="demo", tier="E3",
                                       vocab=self.fx.vocabulary())
        self.assertEqual([entry["capability"]
                          for entry in result["requirement"]["required"]],
                         ["real-chain"])
        self.assertEqual(result["requirement"]["required"][0]["required_by"], ["tier:E3"])

        # 地板在这一探针里让开：这一条问的是「一个裸标量被读成什么」，
        # 不是「谁满足地板」。留着地板会把这一档按另一条理由拒掉，
        # 于是断言在两种读法下都可能通过 —— 那就什么都没探到。
        no_floor = dict(contract, containerized_floor={
            "requires": [], "why": "probe only", "declared_by": "test"})
        selection = test_contract.select_profile(
            no_floor, {"profiles": {"demo-one": {"capabilities": "real-chain"}}},
            required=["real-chain"], project="demo")
        self.assertEqual([item["profile"] for item in selection["candidates"]],
                         ["demo-one"],
                         "自称提供 real-chain 的档位要能被这一项匹配到")

    def test_a_tier_outside_the_declared_vocabulary_is_refused(self):
        """The level names live in the declaration; there is no second copy here.

        A copy in the resolver is the one that stays behind when the vocabulary
        moves, and it would be the one the resolution trusts.
        """
        result = self.fx.resolve(project="demo", tier="E9")
        self.assertFalse(result["resolved"])
        self.assertEqual([v["code"] for v in result["violations"]], ["REFUSED"])
        self.assertIn("E9", result["violations"][0]["message"])
        self.assertIsNone(result["verification"]["evidence_tier"])
        self.assertIsNone(result["profile"],
                          "一个没人定义过的级别不产出环境")
        # 也不做能力核对：要求集缺了那一级该提的部分，比对出来的「够」
        # 就是把「没被回答」读成「不需要」的第二种形态。
        self.assertEqual(result["capability_check"], "not-evaluated")
        self.assertEqual(result["capability_check_reason"], "tier-unanswered")

    def test_a_level_name_the_vocabulary_never_defined_is_its_own_refusal(self):
        """词表是闭的：契约多写一截，不把词表撑大。

        两种「这一级回答不了」必须分得开：一个名字**词表里根本没有**（有人把
        一个不存在的级别写了进来），与一个名字词表认识、而这个契约从没写过它
        要求什么（这个项目漏了一级）。它们的处置不同，句子的出处也不同。

        更要紧的是：让第二种判据顺带承担第一种，会让「契约可以自己发明级别名」
        静默成立 —— 词表不再闭，只是没有任何东西会响。所以这里把词表收窄到
        契约之外，然后点名那个只存在于契约里的级别。
        """
        narrow = SCHEMA.replace("known: [E0, E1, E2, E3, E4]", "known: [E0, E1, E2, E3]")
        self.assertIn("known: [E0, E1, E2, E3]\n", narrow, "词表真的被收窄了")
        fx = Fixture(schema=narrow)
        self.addCleanup(fx.close)
        result = fx.resolve(project="demo", tier="E4")
        self.assertFalse(result["resolved"])
        violation = [v for v in result["violations"] if v["code"] == "REFUSED"][0]
        self.assertEqual(violation["detail"]["known"], ["E0", "E1", "E2", "E3"],
                         "拒它的理由是词表里没有这个名字，不是契约没写它")
        self.assertNotIn("mapped", violation["detail"],
                         "这是词表的问题，不是这个契约漏了一级")
        self.assertEqual(result["capability_check_reason"], "tier-unanswered")

    def test_a_tier_the_contract_never_mapped_is_a_refusal_not_an_empty_requirement(self):
        """「这一级要求什么，没人回答过」与「这一级什么也不要求」是两件事。

        Treating an unmapped level as an empty requirement set is how a level
        whose environment needs were never written down turns into a green run
        on no environment at all.
        """
        contract = self.fx.contract()
        del contract["evidence_tiers"]["E3"]
        result = test_contract.resolve(contract, None, None, project="demo", tier="E3")
        self.assertFalse(result["resolved"])
        violation = [v for v in result["violations"] if v["code"] == "REFUSED"][0]
        self.assertEqual(violation["detail"]["mapped"], ["E0", "E1", "E2", "E4"])
        self.assertEqual(result["requirement"]["required"], [],
                         "要求集是空的，但这一次是「没被回答」，不是「不需要」")

    def test_a_lowering_without_a_reason_is_not_a_value(self):
        result = self.fx.resolve(project="demo", item="SRV-0001",
                                 item_overrides={"workers": {"value": 1}})
        self.assertFalse(result["resolved"])
        codes = [v["code"] for v in result["violations"]]
        self.assertEqual(codes, ["MISSING_REASON"])
        self.assertIn("必须给出理由", result["violations"][0]["message"])

    def test_the_same_lowering_with_a_reason_resolves(self):
        result = self.fx.resolve(
            project="demo", item="SRV-0001",
            item_overrides={"workers": {"value": 1, "reason": "reproducing a serialisation bug"}})
        self.assertTrue(result["resolved"], result["violations"])
        worker = result["values"]["workers"]
        self.assertEqual(worker["value"], 1)
        self.assertEqual(worker["source"], "item-override")
        self.assertEqual(worker["declared_by"], "SRV-0001")
        self.assertEqual(worker["override_reason"], "reproducing a serialisation bug")

    def test_a_run_may_not_change_what_an_item_is_tested_with(self):
        """`item-override` is a ceiling on who may change it, not a floor."""
        result = self.fx.resolve(project="demo", run_id="run-1",
                                 run_overrides={"workers": {"value": 8, "reason": "faster"}})
        self.assertFalse(result["resolved"])
        self.assertEqual([v["code"] for v in result["violations"]], ["INVALID_OVERRIDE"])
        self.assertIn("run-override 无权改它", result["violations"][0]["message"])

    def test_the_run_layer_decides_where_both_layers_may_speak(self):
        """`run-override` includes `item-override`: both may speak, the later one decides.

        This is what a `run-override` knob is FOR — the project is saying *a
        run may choose this*. The two layers are both applied, item first, so
        an item value is reported whenever the run has nothing to say.
        """
        item_only = self.fx.resolve(
            project="demo", item="SRV-0001",
            item_overrides={"fault_injection": {"value": "all"}})
        self.assertTrue(item_only["resolved"], item_only["violations"])
        self.assertEqual(item_only["values"]["fault_injection"]["source"], "item-override")

        both = self.fx.resolve(
            project="demo", item="SRV-0001", run_id="run-7",
            item_overrides={"fault_injection": {"value": "all"}},
            run_overrides={"fault_injection": {"value": "io-only"}})
        self.assertTrue(both["resolved"], both["violations"])
        entry = both["values"]["fault_injection"]
        self.assertEqual(entry["value"], "io-only")
        self.assertEqual(entry["source"], "run-override")
        self.assertEqual(entry["declared_by"], "run-7")

    def test_a_refused_run_override_does_not_clobber_the_items_value(self):
        """Refusing is not "and then apply it anyway".

        The item lowered `workers` with a reason; the run asks for 8, which an
        `item-override` knob refuses. The reported value has to stay the item's
        — a refusal that still changed the number would be a refusal in name
        only, and the exit code alone would not say which happened.
        """
        result = self.fx.resolve(
            project="demo", item="SRV-0001", run_id="run-7",
            item_overrides={"workers": {"value": 1, "reason": "serialising a race"}},
            run_overrides={"workers": {"value": 8, "reason": "faster"}})
        self.assertFalse(result["resolved"])
        self.assertEqual([v["code"] for v in result["violations"]], ["INVALID_OVERRIDE"])
        worker = result["values"]["workers"]
        self.assertEqual(worker["value"], 1)
        self.assertEqual(worker["source"], "item-override")
        self.assertEqual(worker["declared_by"], "SRV-0001")

    def test_the_decider_is_whoever_changed_the_value(self):
        """`declared_by` names the layer that decided, not everyone in the room.

        Both an item and a run override are present; the run's value is the one
        reported, so the run is the decider. Reporting the item would attribute
        a decision to somebody who did not make it.
        """
        result = self.fx.resolve(
            project="demo", item="SRV-0001", run_id="run-7",
            item_overrides={"fault_injection": {"value": "all"}},
            run_overrides={"fault_injection": {"value": "io-only"}})
        entry = result["values"]["fault_injection"]
        self.assertEqual(entry["source"], "run-override")
        self.assertEqual(entry["declared_by"], "run-7")
        self.assertNotEqual(entry["declared_by"], "SRV-0001")

    def test_a_reason_belongs_to_the_value_it_explains(self):
        """An item's reason must not survive onto a value the run chose.

        Both layers change `fault_injection`; only the item gave a reason. The
        reported reason would then explain a value it was never written about —
        and it would read as though the run had justified itself.
        """
        result = self.fx.resolve(
            project="demo", item="SRV-0001", run_id="run-7",
            item_overrides={"fault_injection": {"value": "all",
                                                "reason": "this item is about the fault path"}},
            run_overrides={"fault_injection": {"value": "io-only"}})
        self.assertTrue(result["resolved"], result["violations"])
        entry = result["values"]["fault_injection"]
        self.assertEqual(entry["value"], "io-only")
        self.assertNotIn("override_reason", entry)

        item_only = self.fx.resolve(
            project="demo", item="SRV-0001",
            item_overrides={"fault_injection": {"value": "all",
                                                "reason": "this item is about the fault path"}})
        self.assertEqual(item_only["values"]["fault_injection"]["override_reason"],
                         "this item is about the fault path")

    def test_a_project_default_knob_is_not_the_items_to_change(self):
        """The middle rung: adjusted by editing the contract, not by an override.

        Without this, "this knob is a long-term project decision" and "this
        knob is this item's business" would be the same statement.
        """
        result = self.fx.resolve(
            project="demo", item="SRV-0001",
            item_overrides={"release_tier": {"value": "canary", "reason": "we want canary"}})
        self.assertFalse(result["resolved"])
        violation = result["violations"][0]
        self.assertEqual(violation["code"], "INVALID_OVERRIDE")
        self.assertIn("标为 project-default", violation["message"])
        self.assertNotIn("fixed", violation["message"])

    def test_a_fixed_knob_refuses_every_layer(self):
        for layer in ("item-override", "run-override"):
            with self.subTest(layer=layer):
                result = self.fx.resolve(
                    project="demo",
                    **{f"{layer.replace('-', '_')}s": {"runtime_flavour": {"value": "sqlite"}}})
                self.assertFalse(result["resolved"])
                self.assertEqual([v["code"] for v in result["violations"]], ["REFUSED"])

    def test_a_hard_ceiling_has_no_give_a_reason_tier(self):
        result = self.fx.resolve(
            project="demo", item="SRV-0001",
            item_overrides={"stack_memory_bytes": {"value": 17179869184,
                                                   "reason": "a large model this time"}})
        self.assertFalse(result["resolved"])
        violation = result["violations"][0]
        self.assertEqual(violation["code"], "INVALID_OVERRIDE")
        self.assertIn("硬上限没有", violation["message"])

    def test_a_value_below_its_declared_minimum_is_refused(self):
        result = self.fx.resolve(project="demo", item="SRV-0001",
                                 item_overrides={"workers": {"value": 0, "reason": "it will not start"}})
        self.assertFalse(result["resolved"])
        self.assertIn("低于下界 1", result["violations"][0]["message"])

    def test_above_the_recommended_maximum_resolves_and_says_so(self):
        result = self.fx.resolve(
            project="demo", item="SRV-0001",
            item_overrides={"workers": {"value": 12, "reason": "driving a concurrency case"}})
        self.assertTrue(result["resolved"], result["violations"])
        self.assertTrue(result["values"]["workers"]["above_recommended_max"])

    def test_the_vocabulary_of_mutability_comes_from_the_declaration(self):
        """The one case that tells the two designs apart.

        `run-override` is in this resolver's ladder AND in the fixture's own
        declaration — so a resolver holding a private copy of the words would
        accept it. Handing it a declaration that does not list the word is what
        makes the difference visible, and it must come out as "this contract is
        not legal" rather than "this resolver cannot do it": the file, not the
        code, is where the word is missing.
        """
        contracts = self.fx.contract()
        contracts["knobs"]["runtime_flavour"]["mutability"] = "run-override"
        self.assertIn("run-override", test_contract.MUTABILITY_RANK,
                      "the ladder does know this word — that is the point")
        self.assertTrue(self.fx.resolve(project="demo")["resolved"],
                        "the fixture's declaration does list it, so it is legal there")

        result = test_contract.resolve(
            contracts,
            None, None,
            project="demo", item="SRV-0001",
            item_overrides={"runtime_flavour": {"value": "sqlite", "reason": "why not"}},
            vocab={"mutability": ["fixed", "project-default", "item-override"]})
        self.assertFalse(result["resolved"])
        violation = result["violations"][0]
        self.assertEqual(violation["code"], "INVALID_OVERRIDE")
        self.assertIn("不在声明允许的", violation["message"])

    def test_a_mutability_the_resolver_has_no_rule_for_is_a_different_answer(self):
        """Declared and legal, but not executable. That is REFUSED, not INVALID.

        `mutability` is a LADDER, and a new rung is a resolution decision that
        this code has to make. Calling it "an invalid contract" would send the
        reader to the wrong file.
        """
        contract = self.fx.contract()
        contract["knobs"]["runtime_flavour"]["mutability"] = "tenant-override"
        result = test_contract.resolve(
            contract,
            None, None,
            project="demo",
            item_overrides={"runtime_flavour": {"value": "sqlite"}},
            vocab={"mutability": ["fixed", "project-default", "item-override",
                                  "tenant-override"]})
        self.assertFalse(result["resolved"])
        self.assertEqual([v["code"] for v in result["violations"]], ["REFUSED"])
        self.assertIn("这个解析器没有", result["violations"][0]["message"])

    def test_a_contract_with_no_knobs_resolves_to_nothing_and_says_why(self):
        result = test_contract.resolve({"project": "demo", "knobs": {}}, None, None,
                                       project="demo")
        self.assertFalse(result["resolved"])
        self.assertEqual(result["violations"][0]["code"], "REFUSED")
        self.assertEqual(result["values"], {})

    def test_the_global_policy_is_carried_along_as_constraints(self):
        policy = test_contract.load_policy(self.fx.roots)
        result = self.fx.resolve(project="demo")
        ids = [c["id"] for c in result["constraints"]]
        self.assertEqual(ids, [i["id"] for i in policy.declaration["invariants"]])


# --------------------------------------------------------------------- 窄档

class NarrowProfileTest(ContractTestCase):
    """A profile that does not meet the containerised floor.

    A single-process reproduction is a real thing to do, so this is allowed —
    as a *named* use with a reason. What is not allowed is arriving at it
    silently, or taking a level's name from a run whose environment cannot
    support it: 窄档免掉的是地板，不是那一级本身。
    """

    def test_a_narrow_profile_is_never_matched(self):
        """Even where nothing is asked of the environment, the floor still bars it."""
        result = self.fx.resolve(project="demo", change_class="unit")
        self.assertEqual(result["profile"], "demo-unit",
                         "E2 的最小覆盖是「什么都不建」的那一档")
        rejected = {r["profile"]: r["why"]
                    for r in result["environment_profile"]["rejected"]}
        self.assertIn("只能被显式点名使用", rejected["demo-narrow"])

    def test_a_requirement_a_narrow_profile_cannot_meet_is_still_a_requirement(self):
        result = self.fx.resolve(project="demo", change_class="bugfix")
        rejected = {r["profile"]: r["missing"]
                    for r in result["environment_profile"]["rejected"]}
        self.assertEqual(rejected["demo-narrow"],
                         ["multi-worker", "postgres", "redis"])
        self.assertNotIn("demo-narrow",
                         [c["profile"] for c in result["environment_profile"]["candidates"]])

    def test_naming_it_without_a_reason_does_not_resolve(self):
        result = self.fx.resolve(project="demo", profile_name="demo-narrow")
        self.assertFalse(result["resolved"])
        self.assertEqual([v["code"] for v in result["violations"]], ["MISSING_REASON"])
        self.assertEqual(result["violations"][0]["detail"]["waived"], ["postgres"])
        self.assertTrue(result["environment_profile"]["narrowing"])
        self.assertIsNone(result["environment_profile"]["chosen_because"])

    def test_naming_it_with_a_reason_resolves_and_the_reason_is_carried(self):
        result = self.fx.resolve(project="demo", profile_name="demo-narrow",
                                 chosen_because="只复现单个进程内的序列化缺陷")
        self.assertTrue(result["resolved"], result["violations"])
        env = result["environment_profile"]
        self.assertTrue(env["narrowing"])
        self.assertEqual(env["waived"], ["postgres"])
        self.assertEqual(env["chosen_because"], "只复现单个进程内的序列化缺陷")
        self.assertEqual(result["capability_check"], "narrowed")

    def test_a_reason_cannot_buy_a_capability_the_profile_lacks(self):
        """The floor is waivable; the level's own requirement is not.

        If a reason could cover a missing capability, naming a narrow profile
        would be the way to claim any level at all — and the claim would read
        exactly like one that was measured.
        """
        result = self.fx.resolve(project="demo", profile_name="demo-narrow",
                                 change_class="bugfix",
                                 chosen_because="理由不能把它补上")
        self.assertFalse(result["resolved"])
        self.assertEqual([v["code"] for v in result["violations"]], ["CAPABILITY_UNMET"])
        detail = result["violations"][0]["detail"]
        self.assertEqual(detail["missing"], ["multi-worker", "postgres", "redis"])
        self.assertEqual(detail["required_by"]["redis"], ["tier:E3"])
        self.assertEqual(result["capability_check"], "unmet")

    def test_a_narrow_profile_may_be_used_where_nothing_is_asked_of_the_environment(self):
        """E2 is judged on constructed inputs, so it asks the environment for
        nothing — and a single-process profile answers that honestly."""
        result = self.fx.resolve(project="demo", profile_name="demo-narrow",
                                 change_class="unit",
                                 chosen_because="在构造输入上复现，不需要真实环境")
        self.assertTrue(result["resolved"], result["violations"])
        self.assertEqual(result["verification"]["evidence_tier"], "E2")
        self.assertEqual(result["requirement"]["required"], [])
        self.assertEqual(result["capability_check"], "narrowed")


# --------------------------------------------------------------------- 漂移

class DriftTest(ContractTestCase):

    def test_a_stack_that_agrees_with_the_contract_has_no_drift(self):
        self.assertEqual(test_contract.drift(self.fx.contract(), self.fx.current()), [])

    def test_a_worker_count_that_disagrees_is_reported_not_written_back(self):
        fx = Fixture(compose=COMPOSE_REDIS_AND_LIMITS.replace("--num_workers=2",
                                                              "--num_workers=5"))
        self.addCleanup(fx.close)
        contract = fx.contract()
        before = json.loads(json.dumps(contract))
        findings = test_contract.drift(contract, fx.current())
        self.assertEqual([f["knob"] for f in findings], ["workers"])
        self.assertEqual(findings[0]["declared"], 2)
        self.assertEqual(findings[0]["observed"], [5])
        self.assertIn("不被自动改成观测值", findings[0]["message"])
        self.assertEqual(contract, before, "the contract must come back unchanged")

    def test_stacks_are_not_summed_across_files(self):
        """The first version of this added up every `mem_limit` under the project.

        Two stacks that are never run together added up to 11.5 GiB, over the
        10 GiB cap, and the finding was about a machine that cannot be built.
        Each file is a stack; only the named one is graded — and the named one
        is 3.5 GiB, which is fine.

        The second stack is deliberately large: with a small one, summing and
        not summing agree and this test would pass either way.
        """
        fx = Fixture(extra_stack=COMPOSE_SECOND_STACK,
                     extra_stack_name="other/docker-compose.yml")
        self.addCleanup(fx.close)
        observed = fx.current()
        self.assertEqual(len(observed["stacks"]), 2, "both stacks were observed")
        self.assertEqual(test_contract.drift(fx.contract(), observed), [])

    def test_a_stack_with_no_memory_limit_is_not_a_stack_within_budget(self):
        """Not writing a limit is not the same as staying under one."""
        no_limits = COMPOSE_REDIS_AND_LIMITS.replace("    mem_limit: 2g\n", "") \
                                           .replace("    mem_limit: 512m\n", "") \
                                           .replace("    mem_limit: 1g\n", "")
        fx = Fixture(compose=no_limits)
        self.addCleanup(fx.close)
        findings = test_contract.drift(fx.contract(), fx.current())
        knobs = [f["knob"] for f in findings]
        self.assertIn("stack_memory_bytes", knobs)
        message = [f["message"] for f in findings if f["knob"] == "stack_memory_bytes"][0]
        self.assertIn("没有 mem_limit", message)

    def test_a_limit_that_exceeds_the_ceiling_is_reported(self):
        over = COMPOSE_REDIS_AND_LIMITS.replace("mem_limit: 2g", "mem_limit: 200g")
        fx = Fixture(compose=over)
        self.addCleanup(fx.close)
        findings = test_contract.drift(fx.contract(), fx.current())
        message = [f["message"] for f in findings if f["knob"] == "stack_memory_bytes"][0]
        self.assertIn("超过契约硬上限", message)

    def test_a_required_service_that_is_absent_is_reported(self):
        """Spelled out rather than filtered from the good stack.

        Filtering lines out of a YAML document silently changes its shape — the
        first version of this deleted the `cache:` line and left its children
        behind, so the file stopped parsing and an unreadable stack read as a
        clean one.
        """
        no_redis = textwrap.dedent("""\
            services:
              proxy:
                image: demo/proxy:latest
                command: ["--num_workers=2"]
                mem_limit: 2g
                environment:
                  STORE_MODEL_IN_DB: "True"
              db:
                image: postgres:16
                mem_limit: 1g
            """)
        fx = Fixture(compose=no_redis)
        self.addCleanup(fx.close)
        observed = fx.current()
        self.assertIsNotNone(observed["graded"], observed["not_observed"])
        findings = test_contract.drift(fx.contract(), observed)
        self.assertIn("distributed_state_redis", [f["knob"] for f in findings])

    def test_a_stack_that_does_not_hand_the_catalogue_to_the_database_is_reported(self):
        """The check is on the wiring, not on the word.

        `STORE_MODEL_IN_DB` absent means the tracked config is the catalogue —
        which is the one thing `project_semantics.model_catalogue` forbids. A
        stack can have a postgres service and still be config-backed.
        """
        config_backed = COMPOSE_REDIS_AND_LIMITS.replace('      STORE_MODEL_IN_DB: "True"\n', "")
        self.assertNotIn("STORE_MODEL_IN_DB", config_backed)
        fx = Fixture(compose=config_backed)
        self.addCleanup(fx.close)
        observed = fx.current()
        self.assertIsNotNone(observed["graded"], observed["not_observed"])
        findings = test_contract.drift(fx.contract(), observed)
        self.assertIn("model_authority", [f["knob"] for f in findings])

    def test_a_named_stack_that_is_not_there_is_not_a_clean_stack(self):
        """`not observed` and `observed and fine` must not render the same."""
        fx = Fixture(compose=None)
        self.addCleanup(fx.close)
        observed = fx.current()
        self.assertIsNone(observed["graded"])
        self.assertTrue(observed["not_observed"])
        self.assertEqual(test_contract.drift(fx.contract(), observed), [])

    def test_an_unreadable_compose_file_is_reported_as_unreadable(self):
        fx = Fixture()
        self.addCleanup(fx.close)
        broken = fx.workspace / "test-envs" / "demo" / "stack" / "docker-compose.yml"
        broken.write_text("services:\n  proxy: [this is not a mapping\n", encoding="utf-8")
        observed = fx.current()
        self.assertEqual(observed["stacks"], [])
        self.assertTrue(observed["unreadable"] or observed["not_observed"])


# ----------------------------------------------------------------- 档位表漂移

class ProfileDriftTest(ContractTestCase):
    """本文件与 L1 档位表的静态一致性。

    这一条就是那条接缝的**静态形态**：曾经的组合是契约把 redis 标成 fixed，
    而档位表里有一档写着「不需要 redis」——两份文件各自都成立，组合出的环境
    却不适用于生产，而没有任何东西会响。静态可查的不一致不该等到运行时才暴露。
    """

    def drift(self, fx) -> list[dict]:
        return test_contract.profile_drift(fx.contract(), fx.profiles(), fx.vocabulary())

    def test_a_profile_table_that_agrees_with_the_contract_has_no_drift(self):
        self.assertEqual(self.drift(self.fx), [])

    def test_a_containerised_profile_that_skips_the_floor_is_reported(self):
        half = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-integration:
                what: the proxy HTTP surface
                capabilities: [real-chain, redis]
            """)
        fx = Fixture(profiles=half)
        self.addCleanup(fx.close)
        findings = self.drift(fx)
        self.assertEqual([f["knob"] for f in findings], ["containerized_floor"])
        self.assertIn("postgres", findings[0]["message"])
        self.assertIn("demo-integration", findings[0]["message"])

    def test_a_profile_that_declares_itself_narrow_is_exempt_from_the_floor(self):
        """豁免的是地板，不是「说清自己提供什么」这件事。"""
        narrow = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-narrow:
                what: one process, for a minimal reproduction
                narrowing: true
                capabilities: [real-chain]
            """)
        fx = Fixture(profiles=narrow)
        self.addCleanup(fx.close)
        self.assertEqual(self.drift(fx), [])

        undeclared = narrow.replace("    capabilities: [real-chain]\n", "")
        self.assertNotIn("capabilities", undeclared, "真的把那一行删掉了")
        fx2 = Fixture(profiles=undeclared)
        self.addCleanup(fx2.close)
        findings = self.drift(fx2)
        self.assertEqual(len(findings), 1)
        self.assertIn("capabilities", findings[0]["message"])

    def test_a_capability_outside_the_declared_vocabulary_is_not_a_stronger_claim(self):
        """词表外的能力不是「更强」，是没法核对 —— 匹配会当它不存在。"""
        wild = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-full-e2e:
                what: the whole stack and then some
                capabilities: [real-chain, postgres, quantum-telepathy]
            """)
        fx = Fixture(profiles=wild)
        self.addCleanup(fx.close)
        findings = self.drift(fx)
        self.assertEqual(len(findings), 1, findings)
        self.assertIn("quantum-telepathy", findings[0]["message"])

    def test_a_profile_building_a_stack_nobody_grades_is_reported(self):
        """两个相对路径来自两条规则，比较之前要先把锚点接上。

        `env.dir` 锚在契约的 `repo.test_envs` 上，契约点名的那一套锚在工作区根。
        直接比字符串会把「同一个目录的两种写法」报成两套环境 —— 这条判据自己
        就成了它要防的那个错误。所以这里两边都换过锚点，报出来的才是真的不同。
        """
        elsewhere = PROFILES.replace("      dir: stack",
                                     "      dir: some-other-stack")
        self.assertNotIn("dir: stack", elsewhere)
        fx = Fixture(profiles=elsewhere)
        self.addCleanup(fx.close)
        findings = self.drift(fx)
        self.assertEqual([f["profile"] for f in findings], ["demo-full-e2e"])
        self.assertIn("两套不同", findings[0]["message"])

        # 同一个目录写成显式相对路径时不报 —— 判的是目录，不是字符串。
        same = PROFILES.replace("      dir: stack", "      dir: ./stack")
        self.assertIn("dir: ./stack", same)
        fx2 = Fixture(profiles=same)
        self.addCleanup(fx2.close)
        self.assertEqual(self.drift(fx2), [])


# ------------------------------------------------------------------- 命令行

class CommandTest(ContractTestCase):

    def test_projects_lists_the_contract_with_its_revision(self):
        code, payload = self.fx.run_cli("test", "projects", "--json")
        self.assertEqual(code, 0)
        self.assertEqual([p["project"] for p in payload["projects"]], ["demo"])
        self.assertTrue(payload["projects"][0]["revision"].startswith("sha256:"))

    def test_project_prints_the_two_columns(self):
        code, text = self.fx.run_cli("test", "project", "demo")
        self.assertEqual(code, 0)
        self.assertIn("可调整", text)
        self.assertIn("不可降低", text)
        self.assertIn("runtime_flavour", text)

    def test_project_strict_turns_drift_into_an_exit_code(self):
        """Reading is not a gate; a gate is a flag. Both are tested."""
        code, _ = self.fx.run_cli("test", "project", "demo")
        self.assertEqual(code, 0)
        code, _ = self.fx.run_cli("test", "project", "demo", "--strict")
        self.assertEqual(code, 0, "the fixture agrees with itself")

        broken = self.fx.workspace / "test-envs" / "demo" / "stack" / "docker-compose.yml"
        broken.write_text(COMPOSE_REDIS_AND_LIMITS.replace("--num_workers=2", "--num_workers=5"),
                          encoding="utf-8")
        code, text = self.fx.run_cli("test", "project", "demo")
        self.assertEqual(code, 0)
        self.assertIn("TEST_PROFILE_DRIFT", text)
        code, _ = self.fx.run_cli("test", "project", "demo", "--strict")
        self.assertEqual(code, cli.EXIT_FAULT)

    def test_a_gate_that_could_not_read_an_input_is_not_a_gate_that_passed(self):
        """`没比` 与 `比过了、一致` 不能是同一个绿。

        这一条是真的踩到的：L1 档位表当时只在 worktree 里，而解析器读的是
        canonical 检出 —— `load_profiles` 返回 `ok=False`，`profile_drift(None)`
        返回 `[]`，`--strict` 于是对一个**从没读过的**档位表给出 rc=0。
        闸门坏掉的样子，与核对通过的样子，是同一个样子。
        """
        fx = Fixture(profiles=None)
        self.addCleanup(fx.close)
        code, text = fx.run_cli("test", "project", "demo", "--strict")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertIn(test_contract.INPUT_UNREADABLE, text)
        self.assertIn("没比成", text)
        self.assertNotIn("漂移：未发现", text)

    def test_a_named_stack_that_is_not_there_fails_the_strict_gate(self):
        """`drift()` 在没有可判决的那一套时返回 `[]` —— 上游必须自己把它变成故障。

        没有这一条，「契约点名的那一套不在盘上」与「契约与盘上那一套一致」
        在闸门这里退出码相同。
        """
        fx = Fixture(compose=None)
        self.addCleanup(fx.close)
        code, text = fx.run_cli("test", "project", "demo", "--strict")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertIn(test_contract.INPUT_UNJUDGED, text)
        self.assertIn("被点名的这一套", text)

    def test_a_contract_that_names_nothing_is_not_a_fault(self):
        """没声明要判决哪一套，与声明了而它不在，是两件事 —— 只有后者是故障。"""
        self.assertEqual(
            test_contract.unjudged_input({"graded_absent": "nothing-named"}), [])
        self.assertEqual(
            test_contract.unjudged_input({"graded_absent": "repo-undeclared"}), [])
        self.assertEqual(test_contract.unjudged_input({"graded_absent": None}), [])
        self.assertEqual(
            [f["code"] for f in test_contract.unjudged_input(
                {"graded_absent": "named-stack-missing", "not_observed": ["它不在"]})],
            [test_contract.INPUT_UNJUDGED])

    def test_a_readable_input_is_never_reported_as_unreadable(self):
        """反证的正控：闸门认得出一项输入是好的，否则上面几条只是「永远报错」。"""
        fx = Fixture()
        self.addCleanup(fx.close)
        code, payload = fx.run_cli("test", "project", "demo", "--strict", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload["drift"], [])
        self.assertTrue(payload["environment_profiles"]["loaded"])

    def test_resolve_exits_nonzero_on_an_override_that_was_refused(self):
        code, payload = self.fx.run_cli(
            "test", "resolve", "--project", "demo", "--json",
            "--item-override", "workers=1")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertFalse(payload["resolved"])
        self.assertEqual([v["code"] for v in payload["violations"]], ["MISSING_REASON"])

    def test_resolve_reports_an_override_for_a_knob_that_does_not_exist(self):
        """A silently ignored override is the failure this verb exists to prevent."""
        code, payload = self.fx.run_cli(
            "test", "resolve", "--project", "demo", "--json", "--run-override", "turbo=9")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertFalse(payload["resolved"])
        self.assertIn("不在这个契约的旋钮表里",
                      [v["message"] for v in payload["violations"]][0])

    def test_resolve_reports_an_override_it_could_not_even_parse(self):
        code, payload = self.fx.run_cli(
            "test", "resolve", "--project", "demo", "--json", "--run-override", "workers")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertIn("不是 KNOB=VALUE 的形状",
                      [v["message"] for v in payload["violations"]][0])

    def test_resolve_carries_both_revisions_into_the_answer(self):
        """Three months later a report has to be explainable."""
        code, payload = self.fx.run_cli("test", "resolve", "--project", "demo", "--json")
        self.assertEqual(code, 0)
        self.assertTrue(payload["testing_contract_revision"].startswith("sha256:"))
        self.assertTrue(payload["environment_profile_revision"].startswith("sha256:"))
        self.assertTrue(payload["policy_revision"].startswith("sha256:"))

    def test_resolve_says_when_L1_could_not_be_read(self):
        fx = Fixture(profiles=None)
        self.addCleanup(fx.close)
        code, payload = fx.run_cli("test", "resolve", "--project", "demo", "--json")
        self.assertEqual(code, 0)
        self.assertIsNone(payload["environment_profile_revision"])
        self.assertFalse(payload["profile_known"])
        self.assertIn("读不出来", payload["profiles_source"])
        # 「没有核对」的原因是档位表读不出来，不是要求集不完整 —— 两者
        # 处置不同（去修档位表 / 去把那一级的映射写下来），不能印成同一句话。
        self.assertEqual(payload["capability_check"], "not-evaluated")
        self.assertEqual(payload["capability_check_reason"], "no-profiles")
        code, text = fx.run_cli("test", "resolve", "--project", "demo")
        self.assertIn("档位表读不出来", text)

    def test_a_project_that_does_not_exist_is_not_a_fault(self):
        """It is a missing document, which is a different answer from a refusal."""
        code, text = self.fx.run_cli("test", "project", "nobody")
        self.assertEqual(code, cli.EXIT_COULD_NOT_ANSWER)
        self.assertIn("读不出来", text)

    def test_the_declared_vocabulary_is_reported_alongside_the_contract(self):
        code, payload = self.fx.run_cli("test", "contract", "demo", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(payload["vocabulary"]["mutability"],
                         ["fixed", "project-default", "item-override", "run-override"])
        self.assertIn("MISSING_REASON", payload["vocabulary"]["violations"])
        # 能力词表与级别词表也在声明里 —— 解析器读的就是这两份，
        # 所以「这一档声称的能力算不算数」「这个级别名认不认」各有唯一出处。
        self.assertIn("postgres", payload["vocabulary"]["capabilities"])
        self.assertEqual(payload["vocabulary"]["evidence_tiers"],
                         ["E0", "E1", "E2", "E3", "E4"])
        self.assertEqual(payload["vocabulary"]["change_classes"],
                         ["bugfix", "feature", "ui", "unit"])

    def test_resolve_reports_which_authority_decided_what(self):
        """三份答案分开放，因为权威不同：级别来自 automated-testing，
        要求来自项目契约，提供的能力来自 test-management。"""
        code, payload = self.fx.run_cli("test", "resolve", "--project", "demo", "--json")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["verification"]["evidence_tier"], "E3")
        self.assertEqual(payload["verification"]["change_class"], "bugfix")
        self.assertEqual(payload["environment_profile"]["name"], "demo-integration")
        self.assertEqual(payload["environment_profile"]["source"], "matched")
        self.assertEqual(payload["capability_check"], "ok")
        asked = {e["capability"]: e["required_by"]
                 for e in payload["requirement"]["required"]}
        self.assertEqual(asked["redis"], ["tier:E3"])
        self.assertEqual(payload["requirement"]["floor"], ["postgres"])
        self.assertEqual(payload["requirement"]["floor_source"], "containerized_floor")

    def test_the_command_line_can_name_a_tier_a_surface_and_a_verification_profile(self):
        """这一次要证明到哪一级，说得进来，也被记下来。

        `--verification` 只被记录、不被解释：verification profile 的**名字**是
        automated-testing 的词，ServerOS 抄一份它的取值表就是第二份会漂移的拷贝。
        """
        code, payload = self.fx.run_cli(
            "test", "resolve", "--project", "demo", "--json",
            "--evidence-tier", "E4", "--surface", "proxy_api",
            "--verification", "ab-e2e")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["verification"]["evidence_tier"], "E4")
        self.assertEqual(payload["verification"]["tier_source"], "named")
        self.assertEqual(payload["verification"]["decided_by"], "automated-testing")
        self.assertEqual(payload["verification"]["profile"], "ab-e2e")
        self.assertEqual(payload["environment_profile"]["name"], "demo-full-e2e")
        asked = {e["capability"]: e["required_by"]
                 for e in payload["requirement"]["required"]}
        self.assertEqual(asked["browser"], ["tier:E4"])
        self.assertEqual(asked["postgres"], ["tier:E4", "surface:proxy_api"])

    def test_a_level_name_the_declaration_does_not_know_is_a_refusal_not_a_usage_error(self):
        """级别名不是 argparse 的 `choices`。

        抄一份取值表进命令行，词表就有了第二份拷贝，而**漂移后的那一份恰好是
        解析器信任的那一份**。它还会把这个答案报成用法错误（退 2），
        与「这个契约没有回答过这一级」（退 1）看起来完全不同。
        """
        code, payload = self.fx.run_cli(
            "test", "resolve", "--project", "demo", "--json", "--evidence-tier", "E9")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertEqual([v["code"] for v in payload["violations"]], ["REFUSED"])
        self.assertIn("E9", payload["violations"][0]["message"])

    def test_a_narrow_profile_named_on_the_command_line_needs_its_reason_there(self):
        code, payload = self.fx.run_cli(
            "test", "resolve", "--project", "demo", "--json",
            "--profile", "demo-narrow", "--change-class", "unit")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertEqual([v["code"] for v in payload["violations"]], ["MISSING_REASON"])

        code, payload = self.fx.run_cli(
            "test", "resolve", "--project", "demo", "--json",
            "--profile", "demo-narrow", "--change-class", "unit",
            "--chosen-because", "只复现单个进程内的序列化缺陷")
        self.assertEqual(code, 0, payload)
        self.assertEqual(payload["environment_profile"]["chosen_because"],
                         "只复现单个进程内的序列化缺陷")
        self.assertEqual(payload["capability_check"], "narrowed")

    def test_a_run_with_no_environment_says_which_kind_of_none_it_was(self):
        """三种「没有档位」不能印成同一句话。人读的第一行正是他据以判断
        「是环境不够还是要求没说清」的那一行。"""
        tight = textwrap.dedent("""\
            schema: serveros/reusable-test-profiles/v1
            profiles:
              demo-proxy:
                what: the proxy HTTP surface on the real stack
                capabilities: [real-chain, postgres, redis, multi-worker]
            """)
        fx = Fixture(profiles=tight)
        self.addCleanup(fx.close)
        code, text = fx.run_cli("test", "resolve", "--project", "demo",
                                "--change-class", "feature")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertIn("环境档位未定", text)
        self.assertIn("没有一档覆盖得了", text)
        self.assertNotIn("没有可选的档位", text)

        code, text = fx.run_cli("test", "resolve", "--project", "demo",
                                "--evidence-tier", "E9")
        self.assertEqual(code, cli.EXIT_FAULT)
        self.assertIn("要求集不完整", text)
        self.assertNotIn("没有一档覆盖得了", text)
        self.assertNotIn("档位表读不出来", text)

        # 匹配到档位时当然印它自己的名字，两种说法不会同时出现。
        code, text = fx.run_cli("test", "resolve", "--project", "demo")
        self.assertEqual(code, 0, text)
        self.assertIn("环境档位 demo-proxy（按能力匹配）", text)
        self.assertNotIn("环境档位未定", text)

        # 第三件事：档位表读不出来。放在最后，因为这个 fixture 的环境变量
        # 会盖住前一个（后起的 patch 生效），换回去会读到别人的工作区。
        blind = Fixture(profiles=None)
        self.addCleanup(blind.close)
        code, text = blind.run_cli("test", "resolve", "--project", "demo")
        self.assertEqual(code, 0, text)
        self.assertIn("档位表读不出来", text)
        self.assertNotIn("要求集不完整", text)
        self.assertNotIn("注意：档位 None", text,
                         "没点名档位时不该说某个档位找不到定义")


if __name__ == "__main__":
    unittest.main()
