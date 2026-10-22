"""Hermetic Fleet V2 role and Session preview checks; never contact a Node."""
import copy
import unittest
from runtime.v2_role_session_preview import SCHEMA, preview

NOW = 1200


def example():
    return {
        "schema": SCHEMA,
        "work": {
            "work_ref": "work:repair-17", "project_ref": "serveros", "epoch": 3,
            "allowed_executor_devices": ["device:tokyo", "device:dell"],
            "protected_effect": False,
        },
        "role": {
            "role_ref": "role:device-diagnosis", "project_ref": "serveros",
            "required_abilities": ["ability:device-observation"],
            "allowed_families": ["claude", "codex", "deepseek-harness"],
        },
        "runtimes": [{
            "device_ref": "device:tokyo", "os_principal": "vince",
            "runtime_ref": "claude-vince", "credential_instance": "cc-vince-a",
            "family": "claude", "support": "SUPPORTED", "readiness": "READY",
            "slot": "AVAILABLE", "observed_at": NOW - 5,
            "effective_abilities": ["ability:device-observation"],
            "clean_session_support": "SUPPORTED", "workspace_isolation": "VERIFIED",
            "session": None,
        }],
    }


def result(change=None):
    source = example()
    if change is not None:
        change(source)
    return preview(source, now=NOW)


class IdentityAndContext(unittest.TestCase):
    def test_clean_session_preview_does_not_grant_write(self):
        v = result()
        self.assertEqual(v["status"], "ROUTE_PREVIEW_ONLY")
        self.assertEqual(v["proposals"][0]["plan"], "CREATE_CLEAN_SESSION")
        self.assertEqual(v["writes"], [])
        self.assertEqual(v["admission"], "NOT_EVALUATED")
        self.assertTrue(v["proposals"][0]["requires_current_grant"])

    def test_same_work_same_epoch_idle_session_can_resume_in_preview(self):
        def change(x):
            r = x["runtimes"][0]
            r["session"] = self.session()
        v = result(change)
        self.assertEqual(v["proposals"][0]["plan"], "RESUME_SAME_WORK_SESSION")
        self.assertEqual(v["proposals"][0]["session_ref"], "session:original")

    @staticmethod
    def session():
        return {"session_ref": "session:original", "bound_work_ref": "work:repair-17",
                "bound_project_ref": "serveros", "bound_epoch": 3,
                "status": "IDLE", "runtime_ref": "claude-vince", "os_principal": "vince"}

    def test_other_work_never_reuses_its_private_session(self):
        def change(x):
            x["runtimes"][0]["session"] = {**self.session(), "bound_work_ref": "work:private-other"}
        self.assertEqual(result(change)["proposals"][0]["plan"], "CREATE_CLEAN_SESSION")

    def test_other_work_with_unverified_clean_workspace_refuses(self):
        def change(x):
            x["runtimes"][0]["session"] = {**self.session(), "bound_work_ref": "work:other"}
            x["runtimes"][0]["workspace_isolation"] = "UNKNOWN"
        self.assertEqual(result(change)["refusals"][0]["reasons"], ["CLEAN_WORKSPACE_UNVERIFIED"])

    def test_old_epoch_session_is_not_resumed_even_after_aba(self):
        def change(x):
            x["runtimes"][0]["session"] = {**self.session(), "bound_epoch": 1}
        self.assertEqual(result(change)["proposals"][0]["plan"], "CREATE_CLEAN_SESSION")

    def test_reviewer_must_get_independent_context(self):
        def change(x):
            x["runtimes"][0]["session"] = self.session()
            x["work"]["independent_from_session_ref"] = "session:original"
        self.assertEqual(result(change)["proposals"][0]["plan"], "CREATE_CLEAN_SESSION")

    def test_busy_agent_is_not_preempted(self):
        def change(x):
            x["runtimes"][0]["session"] = {**self.session(), "status": "RUNNING"}
        v = result(change)
        self.assertEqual(v["status"], "NO_SAFE_PREVIEW")
        self.assertIn("SESSION_NOT_PROVEN_IDLE", v["refusals"][0]["reasons"])

    def test_unknown_native_session_is_not_reinterpreted_as_free(self):
        def change(x):
            x["runtimes"][0]["session"] = {**self.session(), "status": "UNKNOWN"}
        self.assertEqual(result(change)["status"], "NO_SAFE_PREVIEW")

    def test_absent_session_inventory_cannot_be_treated_as_an_empty_slot(self):
        def change(x): del x["runtimes"][0]["session"]
        v = result(change)
        self.assertEqual(v["status"], "NO_SAFE_PREVIEW")
        self.assertIn("SESSION_INVENTORY_UNKNOWN", v["refusals"][0]["reasons"])

    def test_principal_or_runtime_mismatch_is_refused(self):
        def change(x):
            x["runtimes"][0]["session"] = {**self.session(), "os_principal": "fjd"}
        self.assertIn("SESSION_RUNTIME_OR_PRINCIPAL_MISMATCH", result(change)["refusals"][0]["reasons"])


class ScopeAndEvidence(unittest.TestCase):
    def test_role_does_not_confer_permission_to_repair(self):
        def change(x): x["work"]["protected_effect"] = True
        v = result(change)
        self.assertEqual(v["writes"], [])
        self.assertEqual(v["effect_commit"], "NOT_EVALUATED")
        self.assertTrue(v["proposals"][0]["requires_effect_approval"])

    def test_missing_device_scope_refuses(self):
        def change(x): x["work"]["allowed_executor_devices"] = ["device:dell"]
        self.assertIn("EXECUTOR_DEVICE_NOT_ALLOWED", result(change)["refusals"][0]["reasons"])

    def test_unknown_support_fails_closed(self):
        def change(x): x["runtimes"][0]["support"] = "UNKNOWN"
        self.assertIn("PROVIDER_SUPPORT_NOT_PROVEN", result(change)["refusals"][0]["reasons"])

    def test_unready_fails_closed(self):
        def change(x): x["runtimes"][0]["readiness"] = "UNAVAILABLE"
        self.assertIn("RUNTIME_NOT_READY_OR_FREE", result(change)["refusals"][0]["reasons"])

    def test_missing_effective_ability_refuses(self):
        def change(x): x["runtimes"][0]["effective_abilities"] = []
        self.assertIn("ABILITY_NOT_PROVEN_EFFECTIVE", result(change)["refusals"][0]["reasons"])

    def test_stale_or_future_evidence_refuses(self):
        for stamp in (NOW - 100, NOW + 1, None):
            def change(x): x["runtimes"][0]["observed_at"] = stamp
            with self.subTest(stamp=stamp):
                self.assertIn("RUNTIME_EVIDENCE_STALE_OR_UNKNOWN", result(change)["refusals"][0]["reasons"])

    def test_role_scope_cannot_cross_project(self):
        def change(x): x["role"]["project_ref"] = "private-project"
        self.assertEqual(result(change)["refusals"], [{"reason": "ROLE_PROJECT_MISMATCH"}])

    def test_deepseek_is_not_enabled_by_just_naming_it(self):
        def change(x):
            r = x["runtimes"][0]
            r["family"] = "deepseek-harness"
            r["support"] = "UNKNOWN"
        self.assertIn("PROVIDER_SUPPORT_NOT_PROVEN", result(change)["refusals"][0]["reasons"])

    def test_excluded_provider_refuses_even_if_runtime_looks_ready(self):
        def change(x): x["role"]["allowed_families"] = ["codex"]
        self.assertIn("PROVIDER_NOT_IN_ROLE_REQUIREMENTS", result(change)["refusals"][0]["reasons"])

    def test_duplicate_runtime_session_refuses_duplicate(self):
        def change(x): x["runtimes"].append(copy.deepcopy(x["runtimes"][0]))
        v = result(change)
        self.assertEqual(len(v["proposals"]), 1)
        self.assertIn("DUPLICATE_RUNTIME_SESSION_IDENTITY", v["refusals"][0]["reasons"])

    def test_malformed_schema_is_not_defaulted_into_an_admission(self):
        def change(x): del x["work"]["epoch"]
        self.assertEqual(result(change)["status"], "INVALID_PREVIEW_INPUT")

    def test_true_is_not_a_numeric_epoch(self):
        def change(x): x["work"]["epoch"] = True
        self.assertEqual(result(change)["status"], "INVALID_PREVIEW_INPUT")

    def test_missing_explicit_device_scope_refuses(self):
        def change(x): del x["work"]["allowed_executor_devices"]
        self.assertEqual(result(change)["status"], "INVALID_PREVIEW_INPUT")


if __name__ == "__main__":
    unittest.main()
