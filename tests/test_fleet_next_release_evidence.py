"""No network, secrets, OS user control, or deployment in these tests."""
import copy
import unittest

from fleet_mcp.release_evidence import evaluate, SCHEMA, CHECKS, LIFECYCLE, SCOPES


def sample():
    claude = {"device": "device:tokyo", "runtime_id": "claude-vince-main",
              "family": "claude", "kind": "cloudcli-deepseek"}
    codex = {"device": "device:tokyo", "runtime_id": "codex-vince-main",
             "family": "codex", "kind": "codex"}
    return {
        "schema": SCHEMA,
        "identity": {
            "v1_plugin_id": "plugins-v1-123", "next_plugin_id": "plugins-next-123",
            "next_release_id": "release-candidate-1", "next_backend_sha": "abcdef0123",
            "v1_mcp_url": "https://old.example.invalid/mcp",
            "next_mcp_url": "https://next.example.invalid/mcp",
        },
        "tool_surface": {
            "observed_tools": ["fleet_tool_" + str(i) for i in range(33)],
            "authorized_scopes": sorted(SCOPES),
        },
        "checks": {
            k: {"result": "PASS", "provenance": "LIVE", "receipt_ref": "evidence/" + k}
            for k in CHECKS
        },
        "required_runtimes": [claude, codex],
        "observed_runtimes": [
            {**claude, "owner_verified": True, "auth_isolated": True,
             "provenance": "LIVE", "receipt_ref": "evidence/claude",
             "effective_model": "deepseek-v4-flash-vision-anthropic",
             "effective_permission_mode": "bypassPermissions",
             "lifecycle": dict.fromkeys(LIFECYCLE, "PASS")},
            {**codex, "owner_verified": True, "auth_isolated": True,
             "provenance": "LIVE", "receipt_ref": "evidence/codex",
             "effective_model": "codex-model-owner-verified",
             "effective_permission_mode": "workspace-write",
             "lifecycle": dict.fromkeys(LIFECYCLE, "PASS")},
        ],
    }


class ReadinessGate(unittest.TestCase):
    def test_complete_manifest_is_only_a_review_candidate(self):
        v = evaluate(sample())
        self.assertEqual(v["decision"], "REVIEW_CANDIDATE_ONLY")
        self.assertEqual(v["effects"], [])
        self.assertIn("independent reviewer", v["warning"])

    def test_empty_runtime_catalog_is_refused(self):
        x = sample()
        x["required_runtimes"] = []
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_missing_dell_codex_blocks(self):
        x = sample()
        x["required_runtimes"].append({
            "device": "device:dell", "runtime_id": "codex-fjd",
            "family": "codex", "kind": "codex"})
        self.assertIn("no live observation", " ".join(evaluate(x)["blockers"]))

    def test_duplicate_runtime_blocks(self):
        x = sample()
        x["observed_runtimes"].append(copy.deepcopy(x["observed_runtimes"][0]))
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_same_plugin_endpoint_blocks(self):
        x = sample()
        x["identity"]["next_mcp_url"] = x["identity"]["v1_mcp_url"]
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_missing_approval_scope_blocks(self):
        x = sample()
        x["tool_surface"]["authorized_scopes"].remove("fleet:approve")
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_mock_receipt_cannot_be_live_evidence(self):
        x = sample()
        x["checks"]["oauth_refresh"]["provenance"] = "MOCK"
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_resume_failure_blocks(self):
        x = sample()
        del x["observed_runtimes"][0]["lifecycle"]["resume"]
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_codex_cannot_claim_anthropic_deepseek_model(self):
        x = sample()
        x["observed_runtimes"][1]["effective_model"] = "deepseek-v4-flash-vision-anthropic"
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_owner_scoped_bypass_must_be_observed(self):
        x = sample()
        x["observed_runtimes"][0]["effective_permission_mode"] = "default"
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_wrong_profile_does_not_inherit_credential(self):
        x = sample()
        x["observed_runtimes"][0]["kind"] = "official-claude"
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_login_without_owner_identity_proof_does_not_pass(self):
        x = sample()
        x["observed_runtimes"][0]["owner_verified"] = False
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")


if __name__ == "__main__":
    unittest.main()
