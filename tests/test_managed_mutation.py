#!/usr/bin/env python3
"""C3 · The managed path, driven through every stage it declares.

What this file is trying to establish, in the order it matters:

  * the plan exists and writes nothing. A pipeline whose first stage can touch
    the disk is not a pipeline, it is an executor with extra steps.
  * the executor is never entered unless compliance said ALLOW — and never
    entered when it did — and `--execute` is a SEPARATE gate from both. The
    controls below drive each refusal and assert on the writer's call log, not
    on a comment.
  * the positive path performs a real mutation, and the audit of it is scoped:
    the scope is a path list chosen before the write, `machine_wide` is False,
    and three genuinely different causes are kept apart.
  * the rollback is verified by re-observation rather than asserted, in both
    of its cases: a path that was not there is removed, and a path that WAS
    there comes back byte-for-byte.

The fixture registry is built by writing a registry document and reading it
with the real loader. `build_registry_index` is the producer of the index
shape, and a test that hand-writes that shape passes even when the producer
changes — which is the failure this project has already paid for once.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_RUNTIME = _REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


mutation = _load("serveros_runtime_managed_mutation", "managed_mutation.py")
gate = _load("serveros_runtime_ownership_gate", "ownership_gate.py")

CONTEXT = {"context_ref": "ctx:c3-test", "actor_ref": "work-item:C3-FIXTURE"}

FIXTURE_REGISTRY = """\
schema: serveros/managed-runtime-registry.v1
device_ref: device:c3-fixture
generated_by: tests/test_managed_mutation.py
protected_refs:
  - ref: work-item:C3-PROTECTED
    because: a protected lane, so that the protected control has something to refuse
resources:
  - id: c3-fixture-lane
    kind: fixture
    owner:
      type: work-item
      ref: work-item:C3-FIXTURE
    scope: user
    lifecycle:
      class: task-scoped
    protection:
      level: unprotected
  - id: c3-protected-lane
    kind: fixture
    owner:
      type: work-item
      ref: work-item:C3-PROTECTED
    scope: user
    lifecycle:
      class: task-scoped
    protection:
      level: protected
"""


def request_for(name="c3-fixture", **over):
    body = {
        "action": "projection.publish",
        "owner_ref": "work-item:C3-FIXTURE",
        "subject": {"name": name, "reproducible": True},
        "generator": "tests/test_managed_mutation.py",
        "source_refs": ["registry:work-item:C3-FIXTURE"],
        "content": {"note": "C3 fixture"},
    }
    body.update(over)
    return body


class Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name) / "state"
        (self.root / "projections").mkdir(parents=True)
        self.registry_path = Path(self._tmp.name) / "registry.yaml"
        self.registry_path.write_text(FIXTURE_REGISTRY, encoding="utf-8")
        # Through the real loader, so the shape under test is the producer's.
        self.index = gate.build_registry_index(str(self.registry_path))

    def tearDown(self):
        self._tmp.cleanup()

    def a_plan(self, **over):
        return mutation.plan(request_for(**over), root=self.root,
                             now="2026-09-15T00:00:00+00:00")

    def stage(self, plan_record, *, context=CONTEXT):
        return mutation.compliance(plan_record, context=context,
                                   registry=self.index)


class ThePlanIsTransient(Base):
    def test_the_plan_names_its_paths_and_policy_before_anything_is_written(self):
        record = self.a_plan()
        self.assertTrue(record["transient"])
        self.assertEqual(record["recovery"], "REVERSIBLE")
        self.assertEqual([c["path"] for c in record["proposed_changes"]],
                         [str(self.root / "projections/c3-fixture/projection.yaml"),
                          str(self.root / "projections/c3-fixture/current.json")])
        self.assertEqual(record["policy_context"]["namespace"], "projections")
        self.assertFalse(any(self.root.rglob("projection.yaml")),
                         "planning wrote something")

    def test_an_action_the_pipeline_does_not_declare_is_refused_not_improvised(self):
        with self.assertRaises(mutation.Rejected):
            mutation.plan(request_for(action="docker.network.create"), root=self.root,
                          now="2026-09-15T00:00:00+00:00")

    def test_a_caller_cannot_choose_the_destination(self):
        for name in ("../registry", "a/b", ".hidden", "", "  x  ", ".."):
            with self.subTest(name=name):
                with self.assertRaises(mutation.Rejected):
                    self.a_plan(name=name)


class TheDestinationRuleIsReal(Base):
    """NO_OVERWRITE_AUTHORITY, at the stage boundary the CLI cannot reach.

    `projection.publish` computes its destination from the declared namespace,
    so no CLI request can name an authority target. The rule still has to fire
    when one is named, or it is a sentence rather than a check — and a rule
    that arrives with the first caller that needs it is an exemption.
    """

    def test_a_plan_aimed_at_an_authority_namespace_is_refused(self):
        record = self.a_plan()
        record["subject_rel"] = "runtime/registry/resources.yaml"
        verdict = self.stage(record)
        self.assertEqual(verdict["decision"], "DENY")
        self.assertIn("TARGET_IS_AUTHORITY", verdict["codes"])

    def test_a_plan_outside_every_declared_namespace_is_refused(self):
        record = self.a_plan()
        record["subject_rel"] = "not-a-namespace/thing"
        verdict = self.stage(record)
        self.assertEqual(verdict["decision"], "DENY")
        self.assertIn("TARGET_UNDECLARED_NAMESPACE", verdict["codes"])

    def test_the_live_plan_lands_in_the_projection_namespace(self):
        verdict = self.stage(self.a_plan())
        self.assertEqual(verdict["destination"]["class"], "PROJECTION")
        self.assertTrue(verdict["destination"]["admitted"])

    def test_a_projection_derived_from_a_projection_is_refused(self):
        record = self.a_plan(source_refs=["projections/memory/current.json"])
        verdict = self.stage(record)
        self.assertEqual(verdict["decision"], "DENY")
        self.assertIn("SOURCE_IS_A_PROJECTION", verdict["codes"])

    def test_a_projection_that_cannot_be_regenerated_is_refused(self):
        record = self.a_plan(subject={"name": "c3-fixture", "reproducible": False})
        verdict = self.stage(record)
        self.assertEqual(verdict["decision"], "DENY")
        self.assertIn("NOT_REPRODUCIBLE", verdict["codes"])


class TheExecutorIsNeverEntered(Base):
    """Every refusal, one control each, and the call log is the assertion."""

    def _run_with(self, *, context=CONTEXT, enabled=False, **over):
        writer = mutation.FilesystemWriter()
        record = self.a_plan(**over)
        verdict = self.stage(record, context=context)
        execution = mutation.execute(record, verdict, writer=writer, enabled=enabled)
        return verdict, execution, writer

    def test_no_context_is_not_an_owner(self):
        verdict, execution, writer = self._run_with(context=None, enabled=True)
        self.assertEqual(verdict["decision"], "DENY")
        self.assertEqual(verdict["code"], "NO_TRUSTED_CONTEXT")
        self.assertFalse(execution["performed"])
        self.assertEqual(writer.written, [], "the actuator ran on a DENY")
        self.assertEqual(mutation.exit_code(verdict), 10)

    def test_an_owner_the_registry_has_never_heard_of_is_refused(self):
        verdict, execution, writer = self._run_with(
            owner_ref="work-item:NOT-REGISTERED", enabled=True)
        self.assertEqual(verdict["decision"], "DENY")
        self.assertEqual(verdict["code"], "OWNER_UNKNOWN")
        self.assertEqual(writer.written, [], "the actuator ran on an UNKNOWN owner")
        self.assertEqual(mutation.exit_code(verdict), 10)

    def test_a_protected_lane_needs_a_person_and_the_executor_still_does_not_run(self):
        verdict, execution, writer = self._run_with(
            owner_ref="work-item:C3-PROTECTED", enabled=True)
        self.assertEqual(verdict["decision"], "REQUIRE_APPROVAL")
        self.assertIn("PROTECTED_SUBJECT", verdict["codes"])
        self.assertEqual(writer.written, [], "the actuator ran on an approval nobody gave")
        self.assertEqual(mutation.exit_code(verdict), 11)

    def test_allow_without_the_execute_flag_writes_nothing(self):
        verdict, execution, writer = self._run_with(enabled=False)
        self.assertEqual(verdict["decision"], "ALLOW")
        self.assertFalse(execution["performed"])
        self.assertFalse(execution["runner_called"])
        self.assertEqual(writer.written, [],
                         "an ALLOW performed the mutation without --execute")
        self.assertEqual(mutation.exit_code(verdict), 0)

    def test_the_writer_is_never_reached_by_a_non_allow_verdict(self):
        for over, context in (({"owner_ref": "work-item:NOT-REGISTERED"}, CONTEXT),
                              ({"owner_ref": "work-item:C3-PROTECTED"}, CONTEXT),
                              ({}, None)):
            with self.subTest(over=over, context=context):
                _, execution, writer = self._run_with(context=context, enabled=True, **over)
                self.assertFalse(execution["performed"])
                self.assertEqual(writer.written, [])


class ThePositivePath(Base):
    """The ALLOW row, and it is executed rather than asserted."""

    def test_a_managed_self_owned_unprotected_fixture_is_published_and_audited(self):
        writer = mutation.FilesystemWriter()
        record = self.a_plan()
        verdict = self.stage(record)
        self.assertEqual(verdict["decision"], "ALLOW")
        self.assertFalse(verdict["ownership"]["protection"]["protected"],
                         "the positive control needs a lane that is not protected")

        scope = [c["path"] for c in record["proposed_changes"]]
        dest = Path(record["destination"])
        before = mutation.Observation.take(scope, directory=dest)
        protected_before = mutation.protected_snapshot(self.index)

        execution = mutation.execute(record, verdict, writer=writer, enabled=True)
        self.assertTrue(execution["performed"])

        after = mutation.Observation.take(scope, directory=dest)
        result = mutation.audit(
            record, execution, before=before, after=after,
            protected_before=protected_before,
            protected_after=mutation.protected_snapshot(self.index),
            manifest=_state_root_module().read_manifest(dest))

        self.assertEqual(result["verdict"], "AUDIT_OK", result["codes"])
        self.assertFalse(result["scope"]["machine_wide"])
        self.assertTrue((dest / "projection.yaml").is_file())
        self.assertTrue((dest / "current.json").is_file())

    def test_what_it_published_satisfies_the_state_roots_own_manifest_gate(self):
        """Not "the files exist" — the contract's own reader decides."""
        writer = mutation.FilesystemWriter()
        record = self.a_plan()
        mutation.execute(record, self.stage(record), writer=writer, enabled=True)
        read = _state_root_module().read_manifest(Path(record["destination"]))
        self.assertTrue(read["present"])
        self.assertEqual(read["problems"], [])
        self.assertFalse(read["manifest"]["authority"])
        self.assertTrue(read["manifest"]["reproducible"])


def _state_root_module():
    return _load("serveros_runtime_state_root", "state_root.py")


class TheAuditIsScoped(Base):
    def _publish(self):
        writer = mutation.FilesystemWriter()
        record = self.a_plan()
        verdict = self.stage(record)
        scope = [c["path"] for c in record["proposed_changes"]]
        dest = Path(record["destination"])
        before = mutation.Observation.take(scope, directory=dest)
        execution = mutation.execute(record, verdict, writer=writer, enabled=True)
        return record, execution, before, dest, scope

    def test_a_dry_run_does_not_report_a_missing_target(self):
        writer = mutation.FilesystemWriter()
        record = self.a_plan()
        verdict = self.stage(record)
        scope = [c["path"] for c in record["proposed_changes"]]
        dest = Path(record["destination"])
        before = mutation.Observation.take(scope, directory=dest)
        execution = mutation.execute(record, verdict, writer=writer, enabled=False)
        result = mutation.audit(
            record, execution,
            before=before, after=mutation.Observation.take(scope, directory=dest),
            protected_before=mutation.protected_snapshot(self.index),
            protected_after=mutation.protected_snapshot(self.index),
            manifest=_state_root_module().read_manifest(dest))
        self.assertEqual(result["verdict"], "AUDIT_OK")
        self.assertNotIn("PLAN_TARGET_NOT_WRITTEN", result["codes"],
                         "a plan was reported as a failed mutation")

    def test_somebody_elses_file_is_reported_and_not_attributed_to_us(self):
        record, execution, before, dest, scope = self._publish()
        (dest / "somebody-elses.json").write_text("{}", encoding="utf-8")
        after = mutation.Observation.take(scope, directory=dest)
        result = mutation.audit(
            record, execution, before=before, after=after,
            protected_before=mutation.protected_snapshot(self.index),
            protected_after=mutation.protected_snapshot(self.index),
            manifest=_state_root_module().read_manifest(dest))
        self.assertEqual(result["verdict"], "CONCURRENT_EXTERNAL_CHANGE")
        self.assertIn("+somebody-elses.json",
                      result["concurrent_listing_change"][str(dest)])
        self.assertEqual(result["concurrent_external_change"], [])
        self.assertNotIn("UNEXPECTED_SIDE_EFFECT", result["codes"])

    def test_our_own_write_is_not_mistaken_for_somebody_elses(self):
        record, execution, before, dest, scope = self._publish()
        result = mutation.audit(
            record, execution,
            before=before, after=mutation.Observation.take(scope, directory=dest),
            protected_before=mutation.protected_snapshot(self.index),
            protected_after=mutation.protected_snapshot(self.index),
            manifest=_state_root_module().read_manifest(dest))
        self.assertEqual(result["verdict"], "AUDIT_OK")
        self.assertEqual(result["concurrent_listing_change"], {})

    def test_an_executor_that_writes_outside_its_plan_is_an_unexpected_side_effect(self):
        class Straying(mutation.FilesystemWriter):
            def __init__(self, stray: Path):
                super().__init__()
                self._stray = stray

            def write(self, path, payload):
                super().write(path, payload)
                super().write(self._stray, {"not": "planned"})

        record = self.a_plan()
        verdict = self.stage(record)
        scope = [c["path"] for c in record["proposed_changes"]]
        dest = Path(record["destination"])
        stray = dest / "unplanned.json"
        writer = Straying(stray)
        before = mutation.Observation.take(scope, directory=dest)
        execution = mutation.execute(record, verdict, writer=writer, enabled=True)
        result = mutation.audit(
            record, execution,
            before=before, after=mutation.Observation.take(scope, directory=dest),
            protected_before=mutation.protected_snapshot(self.index),
            protected_after=mutation.protected_snapshot(self.index),
            manifest=_state_root_module().read_manifest(dest))
        self.assertIn("UNEXPECTED_SIDE_EFFECT", result["codes"],
                      "a writer that ignored its own plan was reported as clean")

    def test_a_protected_resource_that_moved_is_named(self):
        record, execution, before, dest, scope = self._publish()
        after_protected = mutation.protected_snapshot(self.index)
        after_protected["work-item:C3-PROTECTED"] = {"protection": "unprotected"}
        result = mutation.audit(
            record, execution,
            before=before, after=mutation.Observation.take(scope, directory=dest),
            protected_before=mutation.protected_snapshot(self.index),
            protected_after=after_protected,
            manifest=_state_root_module().read_manifest(dest))
        self.assertIn("PROTECTED_RESOURCE_CHANGED", result["codes"])


class TheRollbackIsVerified(Base):
    def test_what_it_created_is_removed_and_the_scope_comes_back_identical(self):
        record = self.a_plan()
        scope = [c["path"] for c in record["proposed_changes"]]
        dest = Path(record["destination"])
        before = mutation.Observation.take(scope, directory=dest)

        mutation.execute(record, self.stage(record),
                         writer=mutation.FilesystemWriter(), enabled=True)
        self.assertTrue((dest / "projection.yaml").is_file())

        mutation.rollback(record, before=before, writer=mutation.FilesystemWriter())
        verdict = mutation.rollback_verdict(
            before, mutation.Observation.take(scope, directory=dest))
        self.assertEqual(verdict["verdict"], "ROLLBACK_VERIFIED", verdict["differing"])
        self.assertFalse(dest.exists(), "the fixture directory outlived its rollback")

    def test_what_it_overwrote_comes_back_byte_for_byte(self):
        """The REVERSIBLE claim with teeth.

        This action regenerates an existing projection as readily as it creates
        one. A rollback that only knew how to delete would call itself
        REVERSIBLE while destroying the thing it overwrote — so the prior bytes
        are captured before the write and compared after the restore.
        """
        dest = self.root / "projections/c3-fixture"
        dest.mkdir(parents=True)
        prior = {"previously": "here", "n": 7}
        (dest / "current.json").write_text(json.dumps(prior), encoding="utf-8")
        prior_manifest = {"authority": False, "generated": True,
                          "generator": "an earlier run", "generated_at": "then",
                          "source_refs": ["registry:work-item:C3-FIXTURE"],
                          "reproducible": True}
        (dest / "projection.yaml").write_text(json.dumps(prior_manifest), encoding="utf-8")

        record = self.a_plan()
        scope = [c["path"] for c in record["proposed_changes"]]
        before = mutation.Observation.take(scope, directory=dest)

        mutation.execute(record, self.stage(record),
                         writer=mutation.FilesystemWriter(), enabled=True)
        self.assertNotEqual(json.loads((dest / "current.json").read_text()), prior)

        mutation.rollback(record, before=before, writer=mutation.FilesystemWriter())
        self.assertEqual(json.loads((dest / "current.json").read_text()), prior)
        self.assertEqual(json.loads((dest / "projection.yaml").read_text()), prior_manifest)
        verdict = mutation.rollback_verdict(
            before, mutation.Observation.take(scope, directory=dest))
        self.assertEqual(verdict["verdict"], "ROLLBACK_VERIFIED", verdict["differing"])

    def test_the_vocabulary_is_the_contracts(self):
        self.assertIn(self.a_plan()["recovery"], ("REVERSIBLE", "COMPENSATABLE",
                                                  "IRREVERSIBLE"))
        self.assertEqual(self.a_plan()["recovery"], "REVERSIBLE")


class TheSurfaceAgreesWithTheCode(Base):
    def test_the_declared_exit_codes_are_the_ones_the_code_returns(self):
        contract = _contract()
        declared = contract["surface"]["exit_codes"]
        self.assertEqual(mutation.exit_code({"eligible": True, "decision": "ALLOW"}),
                         mutation.EXIT_ALLOW)
        self.assertEqual(mutation.EXIT_ALLOW, int("0"))
        self.assertEqual(mutation.EXIT_DENY, 10)
        self.assertEqual(mutation.EXIT_REQUIRE_APPROVAL, 11)
        self.assertEqual(mutation.EXIT_UNASKABLE, 2)
        self.assertEqual(mutation.EXIT_AUDIT_NOT_CLEAN, 12)
        for code in (mutation.EXIT_DENY, mutation.EXIT_REQUIRE_APPROVAL,
                     mutation.EXIT_UNASKABLE, mutation.EXIT_AUDIT_NOT_CLEAN):
            self.assertIn(str(code), declared,
                          f"exit {code} is returned and not declared")

    def test_the_exit_mapping_is_a_pure_function_of_the_verdict(self):
        self.assertEqual(mutation.exit_code({}), mutation.EXIT_DENY)
        self.assertEqual(mutation.exit_code({"decision": "ALLOW"}), mutation.EXIT_DENY,
                         "eligibility was ignored; ALLOW without eligible is a refusal")
        self.assertEqual(mutation.exit_code({"eligible": True,
                                             "decision": "REQUIRE_APPROVAL"}), 11)
        self.assertEqual(mutation.exit_code({"eligible": False,
                                             "decision": "REQUIRE_APPROVAL"}), 10)

    def test_the_declared_recovery_is_the_one_the_plans_carry(self):
        self.assertEqual(_contract()["actions"][0]["recovery"], mutation.RECOVERY)
        self.assertEqual(_contract()["actions"][0]["id"], mutation.ACTION)
        self.assertEqual(_contract()["rollback"]["this_round"], "REVERSIBLE")

    def test_the_pipeline_declares_no_plan_store(self):
        forbidden = " ".join(_contract()["forbidden"]).lower()
        for word in ("database", "daemon", "scheduler", "state machine"):
            self.assertIn(word, forbidden)
        source = (_RUNTIME / "managed_mutation.py").read_text(encoding="utf-8")
        for word in ("sqlite", "shelve", "pickle.dump", "atexit"):
            self.assertNotIn(word, source,
                             f"the plan became durable through {word}")

    def test_every_declared_command_parses(self):
        cli = _load("cli", "cli.py")
        parser = cli.build_parser()
        for command in _contract()["surface"]["commands"]:
            argv = command.split()[1:] + ["--request", "x.yaml"]
            with self.subTest(command=command):
                self.assertTrue(callable(getattr(parser.parse_args(argv), "func", None)))


def _contract():
    import yaml
    return yaml.safe_load(
        (_RUNTIME / "managed-mutation-contract.yaml").read_text(encoding="utf-8"))


class TheRequestPathThroughTheVerb(Base):
    """The CLI's own exit codes, on a host-free fixture root."""

    def _write(self, name, body):
        path = Path(self._tmp.name) / name
        path.write_text(body, encoding="utf-8")
        return str(path)

    def test_an_unknown_action_is_exit_two_and_writes_nothing(self):
        req = self._write("req.json", json.dumps(request_for(action="nope")))
        code = mutation.run(["run", "--request", req, "--execute"], root=self.root,
                            writer=mutation.FilesystemWriter(), registry=self.index)
        self.assertEqual(code, mutation.EXIT_UNASKABLE)
        self.assertFalse(any(self.root.rglob("*.json")))

    def test_a_run_without_execute_returns_zero_and_writes_nothing(self):
        req = self._write("req.json", json.dumps(request_for()))
        ctx = self._write("ctx.json", json.dumps(CONTEXT))
        writer = mutation.FilesystemWriter()
        code = mutation.run(["run", "--request", req, "--context", ctx],
                            root=self.root, writer=writer, registry=self.index)
        self.assertEqual(code, mutation.EXIT_ALLOW)
        self.assertEqual(writer.written, [])

    def test_the_verb_publishes_audits_and_returns_zero(self):
        req = self._write("req.json", json.dumps(request_for()))
        ctx = self._write("ctx.json", json.dumps(CONTEXT))
        writer = mutation.FilesystemWriter()
        code = mutation.run(["run", "--request", req, "--context", ctx, "--execute"],
                            root=self.root, writer=writer, registry=self.index)
        self.assertEqual(code, mutation.EXIT_ALLOW)
        self.assertTrue((self.root / "projections/c3-fixture/projection.yaml").is_file())


if __name__ == "__main__":
    unittest.main()
