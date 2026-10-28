#!/usr/bin/env python3
"""L3 role overlays — selection, admission and the two non-negotiables.

WHAT THIS FILE PINS
-------------------
`role-overlays.yaml` gives three company devices a per-task attach set over the
ONE ability-management catalogue:

  * Tokyo primary: the requirements→work→implementation→acceptance spine;
  * Dell A6000: company model-serving, reusing L1 `gpu.readonly` and A4/A7/A8
    semantics instead of wrapping native telemetry in a Skill;
  * FJ corporate Windows: native adapter observations, not packaged Skills.

Two behaviours are the reason this is code and not a table:

  * NEGATIVE — an authorized personal GPU job on the company A6000 does not
    create a Work Item, does not force B3/B4, writes no company report, sends
    nothing to Feishu and syncs nothing, even if a caller hands it a Work Engine
    acceptance record. Company load defers it.
  * POSITIVE — a Tokyo company requirement becomes a canonical Work Item only
    when the Work Engine itself accepted it; the role cannot mint one and a
    foreign authority is refused.

Also pinned: a role never attaches the whole registry, a native observation is
never resolved as a Skill, no plan claims installed/ready/effective (source ≠
device state), and no cross-device/domain sync is a role side effect.

Run: `python3 -m unittest -v tests.test_fleet_v1_role_overlays`
"""
from __future__ import annotations

import copy
import importlib.util
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
HERE = REPO / "integrations" / "fleet-v1-role-overlays"
DATA = HERE / "role-overlays.yaml"
MODULE = HERE / "role_overlays.py"
GPU_CONTRACT = REPO / "native" / "node-core" / "contract" / "core-lite-status.v1.md"
ABILITIES = REPO / "docs" / "architecture" / "ABILITY-REQUIREMENTS.md"

REV = "a" * 40
CATALOGUE = {
    "ability-management", "runtime-management", "workspace-management",
    "work-management", "information-management", "test-management",
    "feishu-report", "document-stewardship", "delivery-pr-report",
    "inventory-publishing", "fleet-coordination", "shared-memory",
}
LOCAL = {"serveros"}
ACCEPTED = {"authority": "work-engine", "accepted": True,
            "canonical_ref": "item:WORK-0001", "requirement_ref": "req:R-1"}


def _load():
    name = "serveros_role_overlays"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, MODULE)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


roles = _load()


def document() -> dict:
    return roles.load(DATA)


def plan(role: str, task: str, **kwargs) -> dict:
    return roles.plan(document(), role=role, task=task,
                      catalogue=CATALOGUE, catalogue_revision=REV, **kwargs)


class TheOverlayIsADataContract(unittest.TestCase):
    def test_schema_authorship_and_three_roles(self):
        doc = document()
        self.assertEqual(doc["schema"], roles.SCHEMA)
        self.assertEqual(doc["authority"]["catalogue"], "ability-management")
        self.assertEqual(doc["authority"]["work_item"], "work-engine")
        self.assertEqual(set(doc["roles"]), {
            "tokyo-development-primary", "dell-gpu-compute",
            "work-fj-corporate-windows"})
        self.assertEqual(doc["devices"]["device:llm-lab-dell"]["role"],
                         "dell-gpu-compute")
        self.assertEqual(doc["devices"]["device:llm-lab-dell"]["class"],
                         "corporate-gpu")

    def test_the_gpu_family_adds_exactly_three_gaps(self):
        gaps = {row["id"] for row in document()["gaps"]}
        self.assertEqual(gaps, {"gpu.model-serving-lifecycle",
                                "gpu.isolated-environment",
                                "gpu.benchmark-acceptance"})

    def test_the_gaps_reuse_native_telemetry_and_a4_a7_a8(self):
        by_id = {gap["id"]: gap for gap in document()["gaps"]}
        lifecycle = by_id["gpu.model-serving-lifecycle"]["reuses"]
        self.assertEqual(lifecycle["lease"], "A4")
        self.assertEqual(lifecycle["acceptance"], "A7")
        self.assertEqual(lifecycle["rollback"], "A8")
        self.assertIn("§6c", lifecycle["telemetry"])
        isolated = by_id["gpu.isolated-environment"]["reuses"]
        self.assertEqual(isolated["lease"], "A4")
        benchmark = by_id["gpu.benchmark-acceptance"]["reuses"]
        self.assertEqual(benchmark["acceptance"], "A7")
        self.assertEqual(benchmark["rollback"], "A8")
        self.assertTrue(GPU_CONTRACT.is_file())
        contract = GPU_CONTRACT.read_text(encoding="utf-8")
        self.assertIn("gpu.readonly", contract)
        self.assertIn("Windows adapter", contract)
        abilities = ABILITIES.read_text(encoding="utf-8")
        self.assertIn("## A4 Runtime & Resource Management", abilities)
        self.assertIn("## A7 Automated Testing & Acceptance", abilities)
        self.assertIn("## A8 Build & Release", abilities)

    def test_the_reader_side_gpu_gap_is_not_overclaimed(self):
        contract = GPU_CONTRACT.read_text(encoding="utf-8")
        self.assertIn("STILL A GAP", contract,
                      "the producer exists, but the Fleet reader is still a gap; "
                      "the overlay must not claim it is wired")


class ValidationIsNotVacuous(unittest.TestCase):
    def test_an_empty_catalogue_is_refused(self):
        with self.assertRaises(roles.Refused):
            roles.validate(document(), set())

    def test_an_unknown_ability_is_refused(self):
        doc = copy.deepcopy(document())
        doc["roles"]["tokyo-development-primary"]["tasks"][
            "company_requirement"]["attach"].append("ghost-ability")
        with self.assertRaises(roles.Refused) as caught:
            roles.validate(doc, CATALOGUE)
        self.assertIn("ghost-ability", str(caught.exception))

    def test_attaching_the_whole_registry_is_refused(self):
        doc = copy.deepcopy(document())
        doc["roles"]["tokyo-development-primary"]["tasks"][
            "company_requirement"]["attach"] = sorted(CATALOGUE | LOCAL)
        with self.assertRaises(roles.Refused):
            roles.validate(doc, CATALOGUE)

    def test_a_native_observation_cannot_be_a_catalogue_skill(self):
        with self.assertRaises(roles.Refused) as caught:
            roles.validate(document(), CATALOGUE | {"gpu.readonly"})
        self.assertIn("second identity", str(caught.exception))

    def test_a_native_observation_cannot_be_a_local_skill_either(self):
        doc = copy.deepcopy(document())
        doc["authority"]["local_skills"].append("gpu.readonly")
        with self.assertRaises(roles.Refused) as caught:
            roles.validate(doc, CATALOGUE)
        # A local skill outside the bootstrap set is refused before the
        # native-id check; both paths prove the catalogue is not widened.
        self.assertIn("only the repository bootstrap", str(caught.exception))

    def test_a_personal_task_must_forbid_the_company_side_effects(self):
        doc = copy.deepcopy(document())
        task = doc["roles"]["dell-gpu-compute"]["tasks"]["personal_idle_experiment"]
        task["forbid"] = ["feishu_report"]
        with self.assertRaises(roles.Refused):
            roles.validate(doc, CATALOGUE)

    def test_an_unknown_role_or_task_is_refused(self):
        with self.assertRaises(roles.Refused):
            plan("dell-gpu-compute", "nope")
        with self.assertRaises(roles.Refused):
            plan("nope", "company_model_serving")

    def test_a_short_catalogue_revision_is_refused(self):
        with self.assertRaises(roles.Refused):
            roles.plan(document(), role="tokyo-development-primary",
                       task="company_requirement", catalogue=CATALOGUE,
                       catalogue_revision="abc")


class AttachIsPerTaskNotTheRegistry(unittest.TestCase):
    def test_no_task_attaches_the_whole_resolvable_set(self):
        known = CATALOGUE | LOCAL
        for role_name, role in document()["roles"].items():
            for task_name, task in role["tasks"].items():
                with self.subTest(role=role_name, task=task_name):
                    self.assertLess(len(task["attach"]), len(known))
                    self.assertNotIn("install_registry", task["attach"])

    def test_the_role_union_is_still_a_strict_subset(self):
        union = {name for role in document()["roles"].values()
                 for task in role["tasks"].values() for name in task["attach"]}
        self.assertLess(len(union), len(CATALOGUE | LOCAL))
        self.assertIn("serveros", union)


class CompanyGPUIsAModelServingTask(unittest.TestCase):
    def test_it_attaches_minimally_and_names_the_three_gaps(self):
        state = plan("dell-gpu-compute", "company_model_serving",
                     acceptance=ACCEPTED, requirement_ref="req:R-1")
        self.assertEqual(state["attach"],
                         ["runtime-management", "serveros", "test-management"])
        self.assertEqual(state["gaps"], ["gpu.benchmark-acceptance",
                                         "gpu.isolated-environment",
                                         "gpu.model-serving-lifecycle"])
        self.assertIn("gpu.readonly", state["native"])
        self.assertEqual(state["priority"], "company")
        self.assertEqual(state["admission"]["decision"], "ALLOWED_COMPANY")
        self.assertEqual(state["reports"], [])
        self.assertEqual(state["cross_device_sync"], [])

    def test_a_work_item_needs_the_work_engine(self):
        no_accept = plan("dell-gpu-compute", "company_model_serving",
                         requirement_ref="req:R-1")
        self.assertFalse(no_accept["work_item"]["creates"])
        foreign = plan("dell-gpu-compute", "company_model_serving",
                       acceptance={"authority": "role", "accepted": True,
                                   "canonical_ref": "item:X"})
        self.assertFalse(foreign["work_item"]["creates"])
        refused = plan("dell-gpu-compute", "company_model_serving",
                       acceptance={**ACCEPTED, "accepted": False}, requirement_ref="req:R-1")
        self.assertFalse(refused["work_item"]["creates"])
        accepted = plan("dell-gpu-compute", "company_model_serving",
                        acceptance=ACCEPTED, requirement_ref="req:R-1")
        self.assertTrue(accepted["work_item"]["creates"])
        self.assertEqual(accepted["work_item"]["canonical_ref"], "item:WORK-0001")


class PersonalGPUProducesNothing(unittest.TestCase):
    def test_company_load_defers_it(self):
        state = plan("dell-gpu-compute", "personal_idle_experiment",
                     approval=True, company_active=True)
        self.assertEqual(state["admission"]["decision"], "DEFER_COMPANY_FIRST")
        self.assertFalse(state["work_item"]["creates"])

    def test_approval_is_required(self):
        for approval in (None, False):
            with self.subTest(approval=approval):
                state = plan("dell-gpu-compute", "personal_idle_experiment",
                             approval=approval, company_active=False)
                self.assertEqual(state["admission"]["decision"],
                                 "REFUSED_APPROVAL_REQUIRED")
                self.assertFalse(state["work_item"]["creates"])

    def test_an_authorized_idle_job_creates_no_item_report_or_sync(self):
        state = plan("dell-gpu-compute", "personal_idle_experiment",
                     approval=True, company_active=False,
                     acceptance=ACCEPTED, requirement_ref="req:R-1")
        self.assertEqual(state["admission"]["decision"], "ALLOWED_PERSONAL_IDLE")
        # A valid acceptance is IGNORED: this is not a company requirement.
        self.assertFalse(state["work_item"]["creates"])
        self.assertTrue(state["work_item"]["acceptance_ignored"])
        self.assertIsNone(state["work_item"]["canonical_ref"])
        self.assertEqual(state["reports"], [])
        self.assertEqual(state["cross_device_sync"], [])
        self.assertEqual(state["data_boundary"], "local_only")
        for forbidden in ("b3_requirement_board", "b4_canonical_work_item",
                          "company_report", "feishu_report", "cross_device_sync"):
            self.assertIn(forbidden, state["forbidden"])

    def test_it_cannot_request_a_cross_device_sync(self):
        with self.assertRaises(roles.Refused):
            plan("dell-gpu-compute", "personal_idle_experiment", approval=True,
                 requested_sync=["device:tokyo-shared-dev-01"])


class TokyoCompanyRequirementIsWorkEngineAccepted(unittest.TestCase):
    def test_the_role_alone_creates_nothing(self):
        state = plan("tokyo-development-primary", "company_requirement")
        self.assertFalse(state["work_item"]["creates"])
        self.assertIn("no acceptance", state["work_item"]["why"])

    def test_the_work_engine_acceptance_creates_the_canonical_item(self):
        state = plan("tokyo-development-primary", "company_requirement",
                     acceptance=ACCEPTED, requirement_ref="req:R-1")
        self.assertTrue(state["work_item"]["creates"])
        self.assertEqual(state["work_item"]["authority"], "work-engine")
        self.assertEqual(state["work_item"]["canonical_ref"], "item:WORK-0001")
        self.assertEqual(state["admission"]["decision"], "ALLOWED_COMPANY")
        self.assertEqual(state["cross_device_sync"], [])
        # Per-task: the report abilities are not forced into the requirement set.
        self.assertNotIn("feishu-report", state["attach"])

    def test_a_foreign_authority_cannot_accept(self):
        state = plan("tokyo-development-primary", "company_requirement",
                     acceptance={"authority": "shell-script", "accepted": True,
                                 "canonical_ref": "item:X"})
        self.assertFalse(state["work_item"]["creates"])

    def test_the_report_task_does_not_mint_a_second_item(self):
        state = plan("tokyo-development-primary", "company_delivery_report",
                     acceptance=ACCEPTED, requirement_ref="req:R-1")
        self.assertFalse(state["work_item"]["creates"])
        self.assertTrue(state["work_item"]["acceptance_ignored"])
        self.assertEqual(state["reports"], ["feishu-report"])


class WindowsNativeIsNotASkill(unittest.TestCase):
    def test_the_native_adapter_is_an_observation_not_a_packaged_skill(self):
        state = plan("work-fj-corporate-windows", "company_task",
                     acceptance=ACCEPTED, requirement_ref="req:R-1")
        self.assertIn("windows.core-lite-adapter", state["native"])
        self.assertNotIn("windows.core-lite-adapter", state["attach"])
        self.assertNotIn("windows.core-lite-adapter", CATALOGUE | LOCAL)
        self.assertTrue(state["work_item"]["creates"])
        self.assertEqual(state["cross_device_sync"], [])


class AdditionalNegativesAreClosed(unittest.TestCase):
    def test_a_personal_task_cannot_attach_a_report_ability(self):
        doc = copy.deepcopy(document())
        doc["roles"]["dell-gpu-compute"]["tasks"][
            "personal_idle_experiment"]["attach"] = ["serveros", "feishu-report"]
        with self.assertRaises(roles.Refused) as caught:
            roles.validate(doc, CATALOGUE)
        self.assertIn("may attach only", str(caught.exception))

    def test_a_free_form_local_skill_is_refused(self):
        doc = copy.deepcopy(document())
        doc["authority"]["local_skills"].append("ghost-ability")
        with self.assertRaises(roles.Refused) as caught:
            roles.validate(doc, CATALOGUE)
        self.assertIn("only the repository bootstrap", str(caught.exception))

    def test_company_load_must_be_stated_for_a_personal_task(self):
        state = plan("dell-gpu-compute", "personal_idle_experiment",
                     approval=True)
        self.assertEqual(state["admission"]["decision"],
                         "REFUSED_COMPANY_LOAD_UNKNOWN")
        self.assertFalse(state["work_item"]["creates"])

    def test_truthy_non_bool_approval_is_not_approval(self):
        state = plan("dell-gpu-compute", "personal_idle_experiment",
                     approval="yes", company_active=False)
        self.assertEqual(state["admission"]["decision"],
                         "REFUSED_APPROVAL_REQUIRED")

    def test_truthy_non_bool_acceptance_is_not_acceptance(self):
        state = plan("tokyo-development-primary", "company_requirement",
                     requirement_ref="req:R-1",
                     acceptance={**ACCEPTED, "accepted": 1})
        self.assertFalse(state["work_item"]["creates"])

    def test_an_empty_canonical_ref_creates_nothing(self):
        state = plan("tokyo-development-primary", "company_requirement",
                     requirement_ref="req:R-1",
                     acceptance={**ACCEPTED, "canonical_ref": "  "})
        self.assertFalse(state["work_item"]["creates"])
        self.assertIn("canonical Work Item", state["work_item"]["why"])

    def test_an_acceptance_for_another_requirement_creates_nothing(self):
        state = plan("tokyo-development-primary", "company_requirement",
                     requirement_ref="req:OTHER",
                     acceptance=ACCEPTED)
        self.assertFalse(state["work_item"]["creates"])
        self.assertIn("req:R-1", state["work_item"]["why"])

    def test_an_arbitrary_device_state_word_is_refused(self):
        with self.assertRaises(roles.Refused):
            plan("dell-gpu-compute", "company_model_serving",
                 requirement_ref="req:R-1", acceptance=ACCEPTED,
                 device_state="READY_EFFECTIVE")

    def test_forbidden_everywhere_is_enforced(self):
        state = plan("tokyo-development-primary", "company_requirement",
                     requirement_ref="req:R-1", acceptance=ACCEPTED)
        for forbidden in document()["forbidden_everywhere"]:
            self.assertIn(forbidden, state["forbidden"])


class SourceIsNotADeviceState(unittest.TestCase):
    def test_no_plan_claims_installed_ready_or_effective(self):
        for role_name, role in document()["roles"].items():
            for task_name in role["tasks"]:
                with self.subTest(role=role_name, task=task_name):
                    state = plan(role_name, task_name, approval=True,
                                 company_active=False, acceptance=ACCEPTED,
                                 requirement_ref="req:R-1")
                    observation = state["observation"]
                    self.assertEqual(observation["installed"], "UNKNOWN")
                    self.assertEqual(observation["ready"], "UNKNOWN")
                    self.assertIsNone(observation["effective"])
                    self.assertEqual(observation["device_state"], "UNKNOWN")
                    self.assertIn("Fleet device report", observation["basis"])
                    self.assertEqual(state["evidence"], "SOURCE_CODE")

    def test_a_reported_device_state_is_only_echoed(self):
        state = plan("dell-gpu-compute", "company_model_serving",
                     acceptance=ACCEPTED, device_state="OBSERVED")
        self.assertEqual(state["observation"]["device_state"], "OBSERVED")
        self.assertEqual(state["observation"]["installed"], "UNKNOWN")


class NothingSyncsAcrossDevices(unittest.TestCase):
    def test_every_plan_has_an_empty_sync_set(self):
        for role_name, role in document()["roles"].items():
            for task_name in role["tasks"]:
                with self.subTest(role=role_name, task=task_name):
                    state = plan(role_name, task_name, approval=True,
                                 company_active=False, acceptance=ACCEPTED,
                                 requirement_ref="req:R-1")
                    self.assertEqual(state["cross_device_sync"], [])


if __name__ == "__main__":
    unittest.main()
