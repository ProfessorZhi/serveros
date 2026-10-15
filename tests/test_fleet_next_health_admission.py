"""Pure off-host health admission tests, never launch a process.

The snapshot is read from `tests/fixtures/fleet_next/health_snapshot.dell_two_accounts.json`
so that the shape of a snapshot an operator is meant to produce is readable
without running anything. Its `source: LIVE` is a field, not a measurement: a
fixture cannot be field evidence, and nothing here claims it is.
"""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from fleet_mcp.health_admission import SCHEMA, assess, runtime_key

FIXTURE = (Path(__file__).resolve().parent / "fixtures" / "fleet_next"
           / "health_snapshot.dell_two_accounts.json")

NOW = 1010


def load():
    with FIXTURE.open(encoding="utf-8") as handle:
        return json.load(handle)


def row_for(snapshot, runtime_id):
    return next(r for r in snapshot["runtimes"] if r["runtime_id"] == runtime_id)


def decision_for(snapshot, runtime_id, now=NOW):
    for admission in assess(snapshot, now=now)["admissions"]:
        if admission["runtime_id"] == runtime_id:
            return admission
    raise AssertionError("no admission for " + runtime_id)


class TheFixtureIsTheShapeAnOperatorProduces(unittest.TestCase):
    def test_all_three_runtimes_are_admitted(self):
        result = assess(load(), now=NOW)
        self.assertEqual(result["status"], "READ_ONLY_ASSESSMENT")
        self.assertEqual([a["decision"] for a in result["admissions"]],
                         ["GO_ONE_BOUNDED_RUN"] * 3)

    def test_the_fixture_is_not_a_measurement(self):
        """`source: LIVE` is a field the gate requires; it is not proof, and
        the module never says otherwise."""
        result = assess(load(), now=NOW)
        self.assertEqual(result["writes"], [])
        self.assertIn("never executes", result["note"])

    def test_full_swap_occupancy_alone_does_not_blanket_block(self):
        self.assertEqual(decision_for(load(), "claude-vince-main")["decision"],
                         "GO_ONE_BOUNDED_RUN")


class TheKeyIsSixDimensional(unittest.TestCase):
    def test_the_two_dell_accounts_are_two_keys(self):
        snapshot = load()
        a = runtime_key(row_for(snapshot, "claude-vince-main"))
        b = runtime_key(row_for(snapshot, "claude-fjd-main"))
        self.assertNotEqual(a, b)
        self.assertNotEqual(a[1:3], b[1:3])  # os_user and uid both differ

    def test_two_claude_accounts_under_one_os_uid_are_two_keys(self):
        snapshot = load()
        second = copy.deepcopy(row_for(snapshot, "claude-vince-main"))
        second.update({"runtime_id": "claude-vince-secondary",
                       "credential_instance": "cc-vince-secondary"})
        snapshot["runtimes"].append(second)
        a = runtime_key(row_for(snapshot, "claude-vince-main"))
        b = runtime_key(row_for(snapshot, "claude-vince-secondary"))
        self.assertNotEqual(a, b)
        # Device, account, uid and family are identical — the two are told
        # apart only by the runtime and the credential instance.
        self.assertEqual((a[0], a[1], a[2], a[4]), (b[0], b[1], b[2], b[4]))
        self.assertNotEqual(a[5], b[5])
        self.assertEqual(len(assess(snapshot, now=NOW)["admissions"]), 4)

    def test_one_account_reported_under_two_uids_is_refused(self):
        snapshot = load()
        imposter = copy.deepcopy(row_for(snapshot, "claude-fjd-main"))
        imposter["uid"] = 1002  # claims to be vince's account
        snapshot["runtimes"].append(imposter)
        reasons = decision_for(snapshot, "claude-fjd-main")["reasons"]
        self.assertIn("OS account reported under two uids on one device", reasons)

    def test_an_incomplete_identity_names_the_dimension_it_lacks(self):
        snapshot = load()
        del row_for(snapshot, "claude-vince-main")["os_user"]
        reasons = decision_for(snapshot, "claude-vince-main")["reasons"]
        self.assertTrue(any("managed identity incomplete" in r and "os_user" in r
                            for r in reasons), reasons)

    def test_a_bare_uid_is_not_an_account(self):
        snapshot = load()
        row_for(snapshot, "claude-vince-main")["uid"] = 0
        reasons = decision_for(snapshot, "claude-vince-main")["reasons"]
        self.assertTrue(any("uid" in r for r in reasons), reasons)

    def test_a_duplicate_managed_identity_is_refused(self):
        snapshot = load()
        snapshot["runtimes"].append(copy.deepcopy(row_for(snapshot, "claude-vince-main")))
        # The *second* row is the one refused: the report keeps an admission per
        # row so that a caller counting rows still sees the duplicate.
        self.assertIn("duplicate runtime identity",
                      assess(snapshot, now=NOW)["admissions"][-1]["reasons"])


class AStuckSessionIsNeitherFreeNorInvisible(unittest.TestCase):
    """The two wrong answers, and the one this module gives instead."""

    def test_a_stuck_session_with_a_free_slot_still_blocks(self):
        snapshot = load()
        row = row_for(snapshot, "claude-vince-main")
        row["running_sessions"], row["stuck_sessions"], row["max_sessions"] = 0, 1, 4
        admission = decision_for(snapshot, "claude-vince-main")
        self.assertEqual(admission["decision"], "NO_GO")
        self.assertIn("a stuck session holds this runtime", admission["reasons"])

    def test_a_stuck_session_consumes_its_slot(self):
        snapshot = load()
        row = row_for(snapshot, "claude-vince-main")
        row["running_sessions"], row["stuck_sessions"], row["max_sessions"] = 1, 1, 2
        self.assertIn("runtime concurrency ceiling reached",
                      decision_for(snapshot, "claude-vince-main")["reasons"])

    def test_sessions_with_no_liveness_report_are_unknown_not_healthy(self):
        snapshot = load()
        del row_for(snapshot, "claude-vince-main")["stuck_sessions"]
        admission = decision_for(snapshot, "claude-vince-main")
        self.assertEqual(admission["decision"], "NO_GO")
        self.assertIn("runtime concurrency or liveness budget unknown", admission["reasons"])

    def test_a_stuck_session_lifts_when_it_is_reaped(self):
        snapshot = load()
        row = row_for(snapshot, "claude-vince-main")
        row["running_sessions"], row["stuck_sessions"], row["max_sessions"] = 0, 1, 4
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"], "NO_GO")
        row["stuck_sessions"] = 0
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"],
                         "GO_ONE_BOUNDED_RUN")


class CredentialsAndProviders(unittest.TestCase):
    def test_a_revoked_credential_is_not_a_runtime(self):
        snapshot = load()
        row_for(snapshot, "claude-vince-main")["credential_state"] = "revoked"
        admission = decision_for(snapshot, "claude-vince-main")
        self.assertEqual(admission["decision"], "NO_GO")
        self.assertIn("credential revoked", admission["reasons"])

    def test_recovery_is_the_ordinary_path_not_a_shortcut(self):
        """A restored credential is `active` and owes a fresh sample like any
        other; recovery must not admit on the strength of the old one."""
        snapshot = load()
        row = row_for(snapshot, "claude-vince-main")
        row["credential_state"] = "revoked"
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"], "NO_GO")
        row["credential_state"] = "active"
        row["sampled_at"] = NOW
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"],
                         "GO_ONE_BOUNDED_RUN")
        # …and a stale sample after restoration is still stale.
        row["sampled_at"] = NOW - 300
        self.assertIn("runtime observation stale",
                      decision_for(snapshot, "claude-vince-main")["reasons"])

    def test_an_unstated_credential_state_is_not_a_yes(self):
        snapshot = load()
        del row_for(snapshot, "claude-vince-main")["credential_state"]
        self.assertIn("credential state unknown",
                      decision_for(snapshot, "claude-vince-main")["reasons"])

    def test_codex_without_a_provider_fails(self):
        snapshot = load()
        del row_for(snapshot, "codex-vince-main")["provider"]
        admission = decision_for(snapshot, "codex-vince-main")
        self.assertEqual(admission["decision"], "NO_GO")
        self.assertIn("Codex provider missing", admission["reasons"])

    def test_a_provider_on_a_claude_runtime_is_not_required(self):
        snapshot = load()
        self.assertNotIn("provider", row_for(snapshot, "claude-vince-main"))
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"],
                         "GO_ONE_BOUNDED_RUN")


class HostPressure(unittest.TestCase):
    def test_memory_psi_full_blocks(self):
        snapshot = load()
        snapshot["hosts"][0]["memory_psi_full_avg10"] = 74
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"], "NO_GO")

    def test_active_swap_out_blocks(self):
        snapshot = load()
        snapshot["hosts"][0]["swap_out_mib_s"] = 15
        self.assertIn("active swap writes",
                      decision_for(snapshot, "claude-vince-main")["reasons"])

    def test_stale_telemetry_is_unknown_not_healthy(self):
        snapshot = load()
        self.assertEqual(decision_for(snapshot, "claude-vince-main", now=NOW + 300)["decision"],
                         "NO_GO")

    def test_owner_grant_is_required(self):
        snapshot = load()
        row_for(snapshot, "claude-vince-main")["owner_approved"] = False
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"], "NO_GO")

    def test_disconnected_runtime_is_not_admitted(self):
        snapshot = load()
        row_for(snapshot, "claude-vince-main")["reachable"] = False
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"], "NO_GO")

    def test_disconnected_host_is_not_admitted(self):
        snapshot = load()
        snapshot["hosts"][0]["reachable"] = False
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"], "NO_GO")

    def test_duplicate_host_identity_fails_closed(self):
        snapshot = load()
        snapshot["hosts"].append(copy.deepcopy(snapshot["hosts"][0]))
        self.assertIn("host observation ambiguous",
                      decision_for(snapshot, "claude-vince-main")["reasons"])

    def test_concurrency_ceiling(self):
        snapshot = load()
        row = row_for(snapshot, "claude-vince-main")
        row["running_sessions"], row["max_sessions"] = 2, 2
        self.assertIn("runtime concurrency ceiling reached",
                      decision_for(snapshot, "claude-vince-main")["reasons"])

    def test_one_wedged_runtime_does_not_deny_its_neighbour(self):
        """vince's stuck session is vince's answer; fjd's runtime is untouched."""
        snapshot = load()
        row_for(snapshot, "claude-vince-main")["stuck_sessions"] = 1
        self.assertEqual(decision_for(snapshot, "claude-vince-main")["decision"], "NO_GO")
        self.assertEqual(decision_for(snapshot, "claude-fjd-main")["decision"],
                         "GO_ONE_BOUNDED_RUN")

    def test_an_unhealthy_host_does_not_block_a_healthy_one(self):
        snapshot = load()
        snapshot["hosts"][0]["memory_psi_full_avg10"] = 74
        snapshot["hosts"].append({
            "device_ref": "device:work-windows", "sampled_at": NOW, "source": "LIVE",
            "reachable": True, "memory_psi_full_avg10": 0, "swap_out_mib_s": 0,
            "blocked_tasks": 0,
        })
        second = copy.deepcopy(row_for(snapshot, "claude-vince-main"))
        second.update({"device_ref": "device:work-windows", "os_user": "vince",
                       "runtime_id": "claude-windows", "sampled_at": NOW})
        snapshot["runtimes"].append(second)
        self.assertEqual(decision_for(snapshot, "claude-windows")["decision"],
                         "GO_ONE_BOUNDED_RUN")


class TheAssessmentHasNoSideEffects(unittest.TestCase):
    def test_no_writes_and_no_status_other_than_read_only(self):
        snapshot = load()
        row_for(snapshot, "claude-vince-main")["stuck_sessions"] = 1
        result = assess(snapshot, now=NOW)
        self.assertEqual(result["writes"], [])
        self.assertEqual(result["status"], "READ_ONLY_ASSESSMENT")

    def test_a_bad_snapshot_is_invalid_rather_than_approved(self):
        for bad in ({}, {"schema": SCHEMA}, {"schema": SCHEMA, "hosts": [], "runtimes": {}}):
            with self.subTest(bad=str(bad)[:40]):
                self.assertEqual(assess(bad, now=NOW)["status"], "INVALID_SNAPSHOT")

    def test_a_malformed_row_does_not_take_the_others_down(self):
        snapshot = load()
        snapshot["runtimes"].append("not a mapping")
        result = assess(snapshot, now=NOW)
        self.assertEqual(result["status"], "READ_ONLY_ASSESSMENT")
        self.assertEqual(result["admissions"][-1]["decision"], "NO_GO")

    def test_the_module_exposes_the_key_it_uses(self):
        self.assertEqual(runtime_key(row_for(load(), "claude-vince-main")),
                         ("device:llm-lab-dell", "vince", 1002, "claude-vince-main",
                          "claude", "cc-vince-primary"))


if __name__ == "__main__":
    unittest.main()
