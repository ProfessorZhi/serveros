"""No network, secrets, OS user control, or deployment in these tests.

The positive control is a file (`tests/fixtures/fleet_next/release_evidence.candidate.json`)
rather than a dict built here, because the thing a reviewer has to agree with
is the *shape* of a passing manifest, and that shape should be readable without
running anything. Every receipt in it is a placeholder: it proves the gate
accepts a well-formed manifest and proves nothing about any machine. A mock
claiming `provenance: LIVE` is not evidence, and the gate says so in its own
warning rather than pretending the field is authenticated.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from fleet_mcp.release_evidence import (
    CAPABILITY_ACTIONS, LIFECYCLE, MANAGED_ACTIONS, SCHEMA, SCOPES, CHECKS, evaluate,
)

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "fleet_next"
           / "release_evidence.candidate.json")


def load():
    with FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)


def claude_row(doc):
    return next(r for r in doc["observed_runtimes"] if r["family"] == "claude")


def codex_row(doc):
    return next(r for r in doc["observed_runtimes"] if r["family"] == "codex")


def dell(os_user, uid, runtime_id, family="claude", instance=None, kind="cloudcli-deepseek"):
    return {
        "device": "device:llm-lab-dell", "os_user": os_user, "uid": uid,
        "runtime_id": runtime_id, "family": family,
        "credential_instance": instance or (runtime_id + "-cred"),
        "kind": kind, "projects": ["proj-lab-notes"],
    }


class ThePositiveControl(unittest.TestCase):
    """The fixture is the acceptance artifact; if it stops passing, the shape
    of a releasable manifest has changed and the documents quoting it are now
    wrong."""

    def test_fixture_is_a_review_candidate_and_never_more(self):
        v = evaluate(load())
        self.assertEqual(v["blockers"], [])
        self.assertEqual(v["decision"], "REVIEW_CANDIDATE_ONLY")
        self.assertEqual(v["effects"], [])
        self.assertIn("independent reviewer", v["warning"])
        self.assertIn("not a", v["warning"])

    def test_the_fixture_carries_the_two_dimensions_the_old_key_could_not(self):
        v = evaluate(load())
        # device, os_user, uid, runtime_id, family, credential_instance
        self.assertTrue(all(len(k) == 6 for k in v["managed_matrix"]))


class ThePromisedOperationsCannotBeWaived(unittest.TestCase):
    """`UNSUPPORTED` is an answer for a provider-gated operation and for
    nothing else. This is the half of the old flat rule that turned a real
    provider into a permanent blocker."""

    def test_a_provider_that_lacks_approval_does_not_block_the_release(self):
        v = evaluate(load())
        self.assertEqual(v["decision"], "REVIEW_CANDIDATE_ONLY")
        self.assertEqual(v["capability_unsupported"]["device:tokyo-llm-lab:vince:1002:codex-vince-main:codex:codex-vince-native"],
                         ["approval", "close", "reconfigure"])

    def test_a_promised_operation_may_not_be_declared_unsupported(self):
        x = load()
        row = claude_row(x)
        row["lifecycle"]["resume"] = "UNSUPPORTED"
        blockers = " ".join(evaluate(x)["blockers"])
        self.assertIn("cannot be waived as UNSUPPORTED", blockers)

    def test_the_capability_record_may_not_name_a_promised_operation(self):
        x = load()
        row = codex_row(x)
        row["capability_unsupported"]["interrupt"] = "evidence/capability/nope"
        self.assertIn("not the provider's to waive", " ".join(evaluate(x)["blockers"]))

    def test_unsupported_cannot_be_smuggled_in_as_a_pass(self):
        """The other direction: a declared-impossible operation reported as
        working is a false receipt, and the gate must not pick a side."""
        x = load()
        row = codex_row(x)
        row["lifecycle"]["close"] = "PASS"
        self.assertIn("is UNSUPPORTED in its capability record and PASS here",
                      " ".join(evaluate(x)["blockers"]))

    def test_unsupported_needs_its_own_receipt(self):
        x = load()
        row = codex_row(x)
        del row["capability_unsupported"]["reconfigure"]
        self.assertIn("claims UNSUPPORTED with no capability receipt",
                      " ".join(evaluate(x)["blockers"]))

    def test_a_blank_capability_receipt_is_not_a_receipt(self):
        x = load()
        codex_row(x)["capability_unsupported"]["close"] = "   "
        self.assertIn("needs its own receipt", " ".join(evaluate(x)["blockers"]))

    def test_unknown_is_not_an_answer_either(self):
        x = load()
        claude_row(x)["lifecycle"]["reconfigure"] = "UNKNOWN"
        self.assertIn("reconfigure unverified", " ".join(evaluate(x)["blockers"]))

    def test_the_two_groups_do_not_overlap(self):
        self.assertEqual(set(MANAGED_ACTIONS) & set(CAPABILITY_ACTIONS), set())
        self.assertEqual(set(LIFECYCLE), set(MANAGED_ACTIONS) | set(CAPABILITY_ACTIONS))
        self.assertIn("resume", MANAGED_ACTIONS)
        self.assertIn("get_run", MANAGED_ACTIONS)
        self.assertEqual(set(CAPABILITY_ACTIONS), {"approval", "reconfigure", "close"})


class TheManagedMatrixHasSixDimensions(unittest.TestCase):
    def test_two_claude_accounts_under_one_os_uid_are_two_runtimes(self):
        x = load()
        second = dell("vince", 1002, "claude-vince-secondary",
                      instance="cc-vince-secondary")
        x["required_runtimes"].append(second)
        # Named as required and never observed: the manifest has one row for
        # the account and the runtime is a different credential instance.
        self.assertIn("claude-vince-secondary",
                      " ".join(evaluate(x)["blockers"]))
        # …and the first runtime's row cannot stand in for it.
        rows = [r for r in x["observed_runtimes"] if r["os_user"] == "vince"]
        self.assertEqual(len(rows), 2)

    def test_two_accounts_on_one_dell_are_two_rows(self):
        x = load()
        x["required_runtimes"].append(dell("fjd", 1000, "claude-fjd-main"))
        observed = dict(claude_row(x))
        observed.update({"device": "device:llm-lab-dell", "os_user": "fjd", "uid": 1000,
                         "runtime_id": "claude-fjd-main",
                         "credential_instance": "claude-fjd-main-cred",
                         "receipt_ref": "evidence/runtime/claude-fjd-main"})
        x["observed_runtimes"].append(observed)
        self.assertEqual(evaluate(x)["decision"], "REVIEW_CANDIDATE_ONLY")

    def test_one_row_cannot_satisfy_both_dell_accounts(self):
        """The collision the six-dimension key exists to catch: the two rows
        differ only in the two fields the old key did not carry."""
        x = load()
        x["required_runtimes"].append(dell("fjd", 1000, "claude-fjd-main"))
        blocker = " ".join(evaluate(x)["blockers"])
        self.assertIn("no live observation", blocker)

    def test_an_os_account_reported_under_two_uids_is_a_contradiction(self):
        x = load()
        row = copy.deepcopy(claude_row(x))
        row["uid"] = 2001
        row["credential_instance"] = "cc-vince-shadow"
        x["observed_runtimes"].append(row)
        self.assertIn("under two different uids", " ".join(evaluate(x)["blockers"]))

    def test_a_missing_dimension_is_named_rather_than_guessed(self):
        x = load()
        del x["required_runtimes"][0]["os_user"]
        self.assertIn("os_user", " ".join(evaluate(x)["blockers"]))

    def test_an_unknown_profile_kind_is_not_a_verified_one(self):
        x = load()
        claude_row(x)["kind"] = "other"
        self.assertIn("profile kind unknown", " ".join(evaluate(x)["blockers"]))

    def test_a_project_that_was_never_exercised_blocks(self):
        x = load()
        x["required_runtimes"][0]["projects"] = ["proj-lab-notes", "proj-live-service"]
        self.assertIn("proj-live-service was never exercised",
                      " ".join(evaluate(x)["blockers"]))


class CodexNeedsItsProvider(unittest.TestCase):
    def test_codex_with_no_provider_blocks(self):
        x = load()
        row = codex_row(x)
        del row["provider"]
        self.assertIn("Codex names no provider", " ".join(evaluate(x)["blockers"]))

    def test_codex_provider_claim_without_a_receipt_blocks(self):
        x = load()
        codex_row(x)["provider_verified"] = False
        self.assertIn("provider is not verified by a receipt",
                      " ".join(evaluate(x)["blockers"]))

    def test_codex_cannot_borrow_the_anthropic_compatible_model(self):
        x = load()
        codex_row(x)["effective_model"] = "deepseek-v4-flash-vision-anthropic"
        self.assertIn("Codex requires a verified native model",
                      " ".join(evaluate(x)["blockers"]))


class TheDeepSeekDefaultIsTokyoGatewayZero(unittest.TestCase):
    """Owner, 2026-10-30: every CloudCLI Claude Code runtime defaults to
    DeepSeek through Tokyo gateway 0, not the FJ production gateway's admin."""

    def test_the_gateway_zero_model_passes(self):
        x = load()
        self.assertEqual(claude_row(x)["kind"], "cloudcli-deepseek")
        self.assertEqual(claude_row(x)["effective_model"], "v4.1flash-anthropic")
        self.assertNotIn("DeepSeek/CC owner default not verified",
                         " ".join(evaluate(x)["blockers"]))

    def test_the_production_admin_route_is_no_longer_the_default(self):
        x = load()
        claude_row(x)["effective_model"] = "deepseek-v4-flash-vision-anthropic"
        self.assertIn("DeepSeek/CC owner default not verified",
                      " ".join(evaluate(x)["blockers"]))


class ReachabilityAndOwnership(unittest.TestCase):
    def test_a_runtime_that_is_not_reachable_has_no_live_observation(self):
        x = load()
        claude_row(x)["reachable"] = False
        self.assertIn("not confirmed reachable", " ".join(evaluate(x)["blockers"]))

    def test_login_without_owner_identity_proof_does_not_pass(self):
        x = load()
        claude_row(x)["owner_verified"] = False
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_missing_credential_isolation_does_not_pass(self):
        x = load()
        claude_row(x)["auth_isolated"] = False
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_a_spec_with_no_runtime_is_refused(self):
        x = load()
        x["required_runtimes"] = []
        self.assertEqual(evaluate(x)["decision"], "BLOCKED")

    def test_a_missing_managed_runtime_blocks_rather_than_narrowing(self):
        x = load()
        x["required_runtimes"].append(dell("fjd", 1000, "codex-fjd-main", family="codex",
                                           kind="codex"))
        self.assertIn("no live observation", " ".join(evaluate(x)["blockers"]))


class MockFixturesCannotReplaceFieldEvidence(unittest.TestCase):
    def test_a_mock_receipt_is_not_live_evidence(self):
        x = load()
        x["checks"]["oauth_refresh"]["provenance"] = "MOCK"
        self.assertIn("checks.oauth_refresh", " ".join(evaluate(x)["blockers"]))

    def test_a_check_with_no_receipt_is_not_a_pass(self):
        x = load()
        x["checks"]["next_only_rollback"]["receipt_ref"] = ""
        self.assertIn("checks.next_only_rollback", " ".join(evaluate(x)["blockers"]))

    def test_the_runtime_receipt_is_required_too(self):
        x = load()
        claude_row(x)["provenance"] = "SIMULATED"
        self.assertIn("not backed by live receipt", " ".join(evaluate(x)["blockers"]))

    def test_a_wrong_schema_blocks_without_reading_further(self):
        verdict = evaluate({"schema": "something/else"})
        self.assertEqual(verdict["decision"], "BLOCKED")
        self.assertEqual(verdict["effects"], [])


class TheV1AcceptanceItemsAreNamed(unittest.TestCase):
    def test_every_named_check_is_required_and_present_in_the_fixture(self):
        x = load()
        self.assertEqual(set(x["checks"]), set(CHECKS))

    def test_the_items_the_task_adds_are_named(self):
        for label in ("oauth_dcr_pkce", "v1_health_before", "v1_health_after",
                      "next_service_cgroup_readback", "public_https",
                      "feishu_login", "oauth_refresh", "same_plugin_id_zip_update",
                      "backend_upgrade_without_plugin_reinstall", "next_only_rollback",
                      "psi_and_resource_caps"):
            with self.subTest(label=label):
                self.assertIn(label, CHECKS)

    def test_rollback_leaving_v1_untouched_is_a_gate_item(self):
        x = load()
        del x["checks"]["next_only_rollback"]
        self.assertIn("checks.next_only_rollback", " ".join(evaluate(x)["blockers"]))

    def test_a_client_tool_refresh_without_reinstall_is_a_gate_item(self):
        x = load()
        del x["checks"]["backend_upgrade_without_plugin_reinstall"]
        self.assertIn("checks.backend_upgrade_without_plugin_reinstall",
                      " ".join(evaluate(x)["blockers"]))

    def test_the_monitoring_item_is_the_precise_one_not_the_old_name(self):
        self.assertIn("psi_and_resource_caps", CHECKS)
        self.assertNotIn("real_time_monitoring", CHECKS)

    def test_no_v2_roadmap_item_is_a_release_precondition(self):
        joined = " ".join(CHECKS).lower()
        for v2 in ("openagent", "self_heal", "self-heal", "trend", "forecast",
                   "autonom", "federation"):
            with self.subTest(v2=v2):
                self.assertNotIn(v2, joined)


class TheSurfaceIsStillTheOneTheDocumentsDescribe(unittest.TestCase):
    def test_the_candidate_fixture_lists_exactly_what_the_build_serves(self):
        from fleet_mcp.tools import SERVED_TOOLS

        x = load()
        self.assertEqual(len(x["tool_surface"]["observed_tools"]), len(SERVED_TOOLS))
        self.assertEqual(set(x["tool_surface"]["authorized_scopes"]), set(SCOPES))

    def test_a_short_tool_list_blocks(self):
        from fleet_mcp.tools import SERVED_TOOLS

        x = load()
        x["tool_surface"]["observed_tools"].pop()
        self.assertIn(f"exactly {len(SERVED_TOOLS)}", " ".join(evaluate(x)["blockers"]))

    def test_a_missing_scope_blocks(self):
        x = load()
        x["tool_surface"]["authorized_scopes"].remove("fleet:approve")
        self.assertIn("six exact OAuth scopes", " ".join(evaluate(x)["blockers"]))

    def test_an_extra_scope_blocks(self):
        x = load()
        x["tool_surface"]["authorized_scopes"].append("fleet:admin")
        self.assertIn("six exact OAuth scopes", " ".join(evaluate(x)["blockers"]))


class TheGateNeverPromotesAnything(unittest.TestCase):
    def test_the_best_verdict_is_a_review_candidate(self):
        verdicts = {evaluate(load())["decision"], evaluate({})["decision"]}
        self.assertNotIn("PRODUCTION_READY", verdicts)
        self.assertNotIn("APPROVED", verdicts)

    def test_no_verdict_carries_an_effect(self):
        for doc in (load(), {}, {"schema": SCHEMA}):
            with self.subTest(doc=str(doc)[:40]):
                self.assertEqual(evaluate(doc)["effects"], [])

    def test_the_schema_is_unchanged_for_readers_of_the_old_documents(self):
        self.assertEqual(SCHEMA, "serveros/fleet-next-release-evidence/v1")


if __name__ == "__main__":
    unittest.main()
