"""The release captain's arithmetic, exercised in the direction that matters.

Every negative test here is written so that removing the guard it covers turns
it red. That is not decoration: a gate whose failure path is never driven is a
gate that has only ever printed the word "GREEN".

Two kinds of control recur, and they are different jobs:

* **A positive control** (`*_reaches_ready`, `*_reaches_canary`,
  `*_reaches_promote`) proves the red tests are not red because the fixture was
  broken. Without it, "blocked" and "the checker cannot see anything" print the
  same.
* **A mutation control** (`..._is_not_evidence`, `..._moved`, `..._another_tree`)
  feeds the checker a fact that is green for a *different* object and asserts it
  is refused. That is the failure mode this file exists for: a green result for
  somebody else's commit is a different fact wearing the same word.

Nothing here deploys, installs, starts, resolves a name, reads a credential or
touches a device. The "executor" is a list.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MODULE_PATH = (REPO / "integrations" / "fleet-next-v1-release-captain"
               / "release_captain.py")
MANIFEST_PATH = (REPO / "integrations" / "fleet-next-v1-release-captain"
                 / "release-manifest.2026-10-29.json")
SCHEMA_PATH = (REPO / "integrations" / "fleet-next-v1-release-captain"
               / "schema" / "release-manifest.v1.schema.json")
CI_SNAPSHOT_PATH = (REPO / "integrations" / "fleet-next-v1-release-captain"
                    / "evidence" / "CI-SNAPSHOT-2026-10-29.json")


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


captain = _load("release_captain", MODULE_PATH)

HEAD = "a" * 40
TREE = "b" * 40
COMPONENT = "c" * 40
PRE_DIGEST = "d" * 64
ARTIFACT_SHA = "e" * 64
OBSERVED_AT = "2026-10-29T00:00:00Z"


# ---------------------------------------------------------------------------
# Fixtures. `green()` is the positive control: if it stops reaching READY then
# every red test below is passing for the wrong reason.
# ---------------------------------------------------------------------------

def green_device(order: int = 1, device_id: str = "device:tokyo-shared-dev-01") -> dict:
    return {
        "device_id": device_id,
        "order": order,
        "grant": {
            "present": True,
            "principal": "vince",
            "scope": ["canary"],
            "source": "local-operator",
            "approved_at": OBSERVED_AT,
        },
        "reachability": {"reachable": True, "observed_at": OBSERVED_AT, "error": None},
        "resource": {"checked": True, "conflict": False, "detail": None},
        "rollback": {"manifest": "rollback-tokyo.json", "verified": True},
        "canary": None,
        "accepted": None,
        "independent_acceptance": None,
    }


def green() -> dict:
    return {
        "schema": captain.SCHEMA,
        "release_id": "fleet-next-v1-test",
        "authority": {"scope": ["canary"], "requested": []},
        "legacy": {
            "track": "legacy-fleet-0.1.0",
            "control_plane": "chatgpt",
            "version": "0.1.0",
            "must_remain_available": True,
            "identity_observed": True,
            "service_unit": "serveros-fleet-mcp.service",
            "plugin_id": "plugin_legacy",
            "backend_id": "backend-legacy",
            "oauth_audience": "audience-legacy",
            "ports": [8401],
            "socket": "/run/legacy/fleet.sock",
            "state_root": "/srv/legacy/state",
            "lock_path": "/run/legacy/fleet.lock",
            "runtime_root": "/srv/legacy/ingress",
            "config_path": "/etc/legacy/fleet.yaml",
            "oauth_state_root": "/var/legacy/oauth",
            "snapshot_pre": {
                "service_unit": "serveros-fleet-mcp.service",
                "version": "0.1.0",
                "digest": PRE_DIGEST,
                "observed_at": OBSERVED_AT,
            },
            "snapshot_post": None,
        },
        "next": {
            "service_unit": "serveros-fleet-next-mcp.service",
            "ports": [8402],
            "socket": "/run/next/fleet.sock",
            "plugin_id": "plugin_next",
            "backend_id": "backend-next",
            "oauth_audience": "audience-next",
            "state_root": "/srv/next/state",
            "lock_path": "/run/next/fleet.lock",
            "runtime_root": "/srv/next/ingress",
            "config_path": "/etc/next/fleet.yaml",
            "oauth_state_root": "/var/next/oauth",
            "public_mcp_url": "https://next.example.invalid/mcp",
            "installed_revision": "UNKNOWN",
            "previous_revision": "UNKNOWN",
        },
        "source": {
            "head_sha": HEAD,
            "tree_sha": TREE,
            "components": [
                {"pr": 500, "role": "rc", "sha": COMPONENT, "expected_sha": COMPONENT},
            ],
        },
        "ci": {
            "head_sha": HEAD,
            "run_id": 40694269346,
            "required_jobs": 5,
            "jobs": [
                {"name": f"job-{index}", "status": "completed", "conclusion": "success"}
                for index in range(5)
            ],
        },
        "qa": {"head_sha": HEAD, "verdict": "GREEN", "independent": True},
        "artifacts": [
            {
                "name": "fleet-next.tar.gz",
                "sha256": ARTIFACT_SHA,
                "provenance": {
                    "builder": "github-actions",
                    "command": "python3 scripts/build.py --next",
                    "generated_at": OBSERVED_AT,
                    "source_tree": TREE,
                },
            }
        ],
        "devices": [green_device()],
        "owner_approval": None,
        "independent_acceptance": None,
    }


def promote_ready() -> dict:
    """`green()` plus everything PROMOTE additionally requires."""
    manifest = green()
    manifest["devices"][0]["canary"] = {
        "started": True,
        "probes": [{"name": "next-health", "verdict": "PASS"}],
        "legacy_during": True,
    }
    manifest["devices"][0]["accepted"] = True
    manifest["devices"][0]["independent_acceptance"] = True
    manifest["legacy"]["snapshot_post"] = dict(manifest["legacy"]["snapshot_pre"])
    manifest["independent_acceptance"] = {"verdict": "PASS"}
    manifest["owner_approval"] = {
        "granted": True,
        "reference": "issue#478 comment 2026-10-29",
        "granted_at": OBSERVED_AT,
    }
    return manifest


def codes(result: dict) -> set[str]:
    return set(result["codes"])


def evaluate(manifest: dict, target: str = "READY", device_id: str | None = None) -> dict:
    return captain.evaluate(manifest, target, device_id)


# ---------------------------------------------------------------------------
# The checked-in documents must be the documents they say they are.
# ---------------------------------------------------------------------------

class TheCheckedInDocumentsAreConsistent(unittest.TestCase):

    def test_the_manifest_is_the_schema_it_claims(self):
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual(captain.validate_manifest(document)["schema"], captain.SCHEMA)

    def test_the_manifest_names_the_three_target_devices_in_order(self):
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertEqual([d["device_id"] for d in document["devices"]],
                         ["device:tokyo-shared-dev-01", "device:llm-lab-dell",
                          "device:work-fj"])
        self.assertEqual([d["order"] for d in document["devices"]], [1, 2, 3])

    def test_the_manifest_pins_the_five_heads_the_brief_names(self):
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        pinned = {component["pr"]: component["sha"]
                  for component in document["source"]["components"]}
        self.assertEqual(pinned, {
            500: "80220a4ad1b009946f11bda0ce96dd8ff8e05468",
            503: "b7f1f6134a1e3dcee4b916068c693ff059c097bd",
            504: "6286bfbc885771a45415efcb33405bb21a841fde",
            505: "3512c551574a94ad360f8bde6435bdf07c729167",
            506: "30d7c4d34ce7ebf76d751b8e306fa2c623c4cac7",
        })

    def test_the_manifest_does_not_claim_a_composed_head_it_does_not_have(self):
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        self.assertTrue(captain.is_unknown(document["source"]["head_sha"]))
        self.assertTrue(captain.is_unknown(document["source"]["tree_sha"]))

    def test_the_checked_in_manifest_reaches_no_release_state(self):
        """The honest state on 2026-10-29. If this ever passes, edit the
        manifest deliberately — do not make the assertion weaker."""
        document = json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))
        state = captain.release_state(document)
        self.assertEqual(state["state"], "PRE_FLIGHT")
        # Pre-flight means collection has not finished. Every open item must be
        # a missing observation, never a red one pretending to be a gap.
        self.assertEqual(set(state["targets"]["READY"]["codes"]), {"BLOCKED_UNKNOWN"})

    def test_the_ci_snapshot_says_no_exact_head_is_green(self):
        snapshot = json.loads(CI_SNAPSHOT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(snapshot["verdict"], "NO_EXACT_HEAD_IS_5_OF_5_GREEN")
        for head in snapshot["heads"]:
            conclusions = [job["conclusion"] for job in head["jobs"]]
            green = conclusions and all(c == "success" for c in conclusions)
            self.assertFalse(green, f"{head['pr']} was recorded as fully green")


# ---------------------------------------------------------------------------
# Manifest shape.
# ---------------------------------------------------------------------------

class AManifestIsRefusedWhenItIsNotOne(unittest.TestCase):

    def test_the_wrong_schema_string_is_refused(self):
        with self.assertRaises(captain.ManifestRefused):
            captain.validate_manifest({"schema": "something-else"})

    def test_an_unsupported_top_level_field_is_refused(self):
        document = green()
        document["credential"] = "not-a-real-secret"
        with self.assertRaises(captain.ManifestRefused) as caught:
            captain.validate_manifest(document)
        self.assertIn("credential", str(caught.exception))
        self.assertNotIn("not-a-real-secret", str(caught.exception))

    def test_a_device_without_an_id_is_refused(self):
        document = green()
        document["devices"][0]["device_id"] = "UNKNOWN"
        with self.assertRaises(captain.ManifestRefused):
            captain.validate_manifest(document)

    def test_a_missing_next_block_is_refused(self):
        document = green()
        del document["next"]
        with self.assertRaises(captain.ManifestRefused):
            captain.validate_manifest(document)


# ---------------------------------------------------------------------------
# The positive control. Without this the whole file could be green because the
# checker sees nothing.
# ---------------------------------------------------------------------------

class ACompleteManifestReachesItsTarget(unittest.TestCase):

    def test_a_complete_manifest_reaches_ready(self):
        result = evaluate(green(), "READY")
        self.assertEqual(result["state"], "READY", result["blocked"])

    def test_a_complete_manifest_reaches_canary_on_tokyo(self):
        result = evaluate(green(), "CANARY")
        self.assertEqual(result["state"], "CANARY", result["blocked"])

    def test_a_complete_manifest_reaches_promote(self):
        result = evaluate(promote_ready(), "PROMOTE")
        self.assertEqual(result["state"], "PROMOTE", result["blocked"])

    def test_release_state_reports_promote(self):
        self.assertEqual(captain.release_state(promote_ready())["state"], "PROMOTE")

    def test_every_reason_code_is_reachable(self):
        """A vocabulary entry nothing can emit is a word for a state the
        checker cannot actually report."""
        observed = set()
        for mutation in GUARD_MUTATIONS:
            target, device = GUARD_TARGETS.get(mutation, ("READY", None))
            observed |= codes(evaluate(mutation(), target, device))
        self.assertEqual(set(captain.REASONS) - observed, set())


# ---------------------------------------------------------------------------
# UNKNOWN is never green.
# ---------------------------------------------------------------------------

class AnUnknownIsNotAGreen(unittest.TestCase):

    def test_an_unobserved_manifest_is_pre_flight_and_not_ready(self):
        empty = green()
        empty["source"]["head_sha"] = "UNKNOWN"
        empty["source"]["tree_sha"] = "UNKNOWN"
        empty["ci"] = {"head_sha": "UNKNOWN", "required_jobs": 5, "jobs": []}
        empty["qa"] = {"head_sha": "UNKNOWN", "verdict": "UNKNOWN", "independent": None}
        empty["artifacts"] = []
        empty["legacy"]["identity_observed"] = False
        empty["legacy"]["snapshot_pre"] = None
        result = evaluate(empty, "READY")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertEqual(captain.release_state(empty)["state"], "PRE_FLIGHT")

    def test_an_unobserved_qa_verdict_blocks(self):
        manifest = green()
        manifest["qa"]["verdict"] = "UNKNOWN"
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_an_unobserved_reachability_blocks_the_canary(self):
        manifest = green()
        manifest["devices"][0]["reachability"]["reachable"] = None
        result = evaluate(manifest, "CANARY")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_an_unmeasured_resource_blocks_the_canary(self):
        manifest = green()
        manifest["devices"][0]["resource"] = {"checked": False, "conflict": None}
        result = evaluate(manifest, "CANARY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_an_unregistered_plugin_id_blocks_promote_but_not_ready(self):
        manifest = green()
        manifest["next"]["plugin_id"] = "UNKNOWN"
        self.assertEqual(evaluate(manifest, "READY")["state"], "READY")
        result = evaluate(promote_ready_with(plugin_id="UNKNOWN"), "PROMOTE")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_an_unverified_rollback_blocks_the_canary(self):
        manifest = green()
        manifest["devices"][0]["rollback"]["verified"] = None
        result = evaluate(manifest, "CANARY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))


def promote_ready_with(**changes) -> dict:
    manifest = promote_ready()
    for field, value in changes.items():
        manifest["next"][field] = value
    return manifest


# ---------------------------------------------------------------------------
# Evidence belongs to a tree.
# ---------------------------------------------------------------------------

class EvidenceBelongsToTheTreeItNames(unittest.TestCase):

    def test_a_green_ci_run_for_another_commit_is_not_evidence(self):
        manifest = green()
        manifest["ci"]["head_sha"] = "f" * 40
        result = evaluate(manifest, "READY")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("BLOCKED_SOURCE_IDENTITY", codes(result))

    def test_a_qa_verdict_for_another_commit_is_not_evidence(self):
        manifest = green()
        manifest["qa"]["head_sha"] = "f" * 40
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_SOURCE_IDENTITY", codes(result))

    def test_a_component_that_moved_is_refused(self):
        manifest = green()
        manifest["source"]["components"][0]["sha"] = "1" * 40
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_SOURCE_IDENTITY", codes(result))

    def test_a_component_sha_that_is_not_a_sha_is_refused(self):
        manifest = green()
        manifest["source"]["components"][0]["sha"] = "not-a-sha"
        manifest["source"]["components"][0]["expected_sha"] = "not-a-sha"
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_SOURCE_IDENTITY", codes(result))

    def test_an_artifact_built_from_another_tree_is_refused(self):
        manifest = green()
        manifest["artifacts"][0]["provenance"]["source_tree"] = "9" * 40
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_PROVENANCE", codes(result))


# ---------------------------------------------------------------------------
# CI: five is five.
# ---------------------------------------------------------------------------

class FiveIsFive(unittest.TestCase):

    def test_four_successful_jobs_are_not_five(self):
        manifest = green()
        manifest["ci"]["jobs"] = manifest["ci"]["jobs"][:4]
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_CI", codes(result))

    def test_one_failing_shard_blocks(self):
        manifest = green()
        manifest["ci"]["jobs"][3]["conclusion"] = "failure"
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_CI", codes(result))

    def test_one_running_shard_is_not_green(self):
        manifest = green()
        manifest["ci"]["jobs"][2] = {"name": "job-2", "status": "in_progress",
                                     "conclusion": None}
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_CI", codes(result))

    def test_a_job_with_no_conclusion_is_unknown_rather_than_green(self):
        manifest = green()
        manifest["ci"]["jobs"][2] = {"name": "job-2", "status": "completed"}
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_five_green_jobs_pass(self):
        self.assertEqual(evaluate(green(), "READY")["state"], "READY")


# ---------------------------------------------------------------------------
# Artifacts and provenance.
# ---------------------------------------------------------------------------

class AnArtifactIsBytesPlusWhereTheyCameFrom(unittest.TestCase):

    def test_an_artifact_without_a_sha256_is_unknown(self):
        manifest = green()
        manifest["artifacts"][0]["sha256"] = "UNKNOWN"
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_a_sha256_that_is_not_64_hex_is_refused(self):
        manifest = green()
        manifest["artifacts"][0]["sha256"] = "abc"
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_ARTIFACT", codes(result))

    def test_an_artifact_without_provenance_is_refused(self):
        manifest = green()
        manifest["artifacts"][0]["provenance"] = {}
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_PROVENANCE", codes(result))

    def test_provenance_without_a_command_is_refused(self):
        manifest = green()
        del manifest["artifacts"][0]["provenance"]["command"]
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_PROVENANCE", codes(result))

    def test_no_artifacts_at_all_is_unknown(self):
        manifest = green()
        manifest["artifacts"] = []
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))


# ---------------------------------------------------------------------------
# The two tracks share nothing.
# ---------------------------------------------------------------------------

class TheTwoTracksShareNoResource(unittest.TestCase):

    def test_a_shared_service_unit_is_a_collision(self):
        manifest = green()
        manifest["next"]["service_unit"] = manifest["legacy"]["service_unit"]
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_LEGACY_COLLISION", codes(result))

    def test_a_shared_port_is_a_collision(self):
        manifest = green()
        manifest["next"]["ports"] = [8401, 8402]
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_LEGACY_COLLISION", codes(result))

    def test_a_nested_state_root_is_a_collision(self):
        manifest = green()
        manifest["next"]["state_root"] = "/srv/legacy/state/next"
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_LEGACY_COLLISION", codes(result))

    def test_a_prefix_sibling_is_not_a_collision(self):
        manifest = green()
        manifest["next"]["state_root"] = "/srv/legacy-state"
        result = evaluate(manifest, "READY")
        self.assertNotIn("BLOCKED_LEGACY_COLLISION", codes(result))

    def test_a_shared_plugin_id_is_a_collision(self):
        manifest = green()
        manifest["next"]["plugin_id"] = manifest["legacy"]["plugin_id"]
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_LEGACY_COLLISION", codes(result))

    def test_a_shared_oauth_audience_is_a_collision(self):
        manifest = green()
        manifest["next"]["oauth_audience"] = manifest["legacy"]["oauth_audience"]
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_LEGACY_COLLISION", codes(result))

    def test_every_collision_is_reported_not_just_the_first(self):
        manifest = green()
        manifest["next"]["service_unit"] = manifest["legacy"]["service_unit"]
        manifest["next"]["ports"] = [8401]
        manifest["next"]["backend_id"] = manifest["legacy"]["backend_id"]
        manifest["next"]["lock_path"] = manifest["legacy"]["lock_path"]
        result = evaluate(manifest, "READY")
        fields = {item["detail"].split(":")[0] for item in result["blocked"]
                  if item["code"] == "BLOCKED_LEGACY_COLLISION"}
        self.assertEqual(fields, {"service_unit", "ports", "backend_id", "lock_path"})

    def test_next_identity_that_was_never_observed_blocks(self):
        manifest = green()
        manifest["next"]["socket"] = "UNKNOWN"
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_an_empty_next_port_list_is_unknown_not_disjoint(self):
        manifest = green()
        manifest["next"]["ports"] = []
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))


# ---------------------------------------------------------------------------
# Legacy survives, and its identity was measured.
# ---------------------------------------------------------------------------

class LegacySurvivesAndItsIdentityWasMeasured(unittest.TestCase):

    def test_a_plan_that_retires_legacy_is_blocked_legacy_parity(self):
        manifest = green()
        manifest["legacy"]["must_remain_available"] = False
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_LEGACY_PARITY", codes(result))

    def test_an_unobserved_legacy_identity_blocks(self):
        manifest = green()
        manifest["legacy"]["identity_observed"] = False
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_a_missing_pre_snapshot_blocks(self):
        manifest = green()
        manifest["legacy"]["snapshot_pre"] = None
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_a_pre_snapshot_digest_that_is_not_sha_is_refused(self):
        manifest = green()
        manifest["legacy"]["snapshot_pre"]["digest"] = "0.1.0"
        result = evaluate(manifest, "READY")
        self.assertIn("BLOCKED_LEGACY_SNAPSHOT", codes(result))

    def test_legacy_drift_across_the_canary_blocks_promote(self):
        manifest = promote_ready()
        manifest["legacy"]["snapshot_post"]["digest"] = "9" * 64
        result = evaluate(manifest, "PROMOTE")
        self.assertIn("BLOCKED_LEGACY_SNAPSHOT", codes(result))

    def test_a_missing_post_snapshot_blocks_promote(self):
        manifest = promote_ready()
        manifest["legacy"]["snapshot_post"] = None
        result = evaluate(manifest, "PROMOTE")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))


# ---------------------------------------------------------------------------
# Authority: the four words that are not in the grant.
# ---------------------------------------------------------------------------

class TheOwnerGrantIsClosed(unittest.TestCase):

    def test_main_merge_is_blocked_owner(self):
        manifest = green()
        manifest["authority"]["requested"] = ["main_merge"]
        result = evaluate(manifest, "READY")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("BLOCKED_OWNER", codes(result))

    def test_replacing_the_legacy_plugin_url_is_blocked_owner(self):
        manifest = green()
        manifest["authority"]["requested"] = ["legacy_plugin_url_replace"]
        self.assertIn("BLOCKED_OWNER", codes(evaluate(manifest, "READY")))

    def test_an_external_feishu_send_is_blocked_owner(self):
        manifest = green()
        manifest["authority"]["requested"] = ["external_feishu_send"]
        self.assertIn("BLOCKED_OWNER", codes(evaluate(manifest, "READY")))

    def test_a_credential_or_oauth_provider_change_is_blocked_owner(self):
        manifest = green()
        manifest["authority"]["requested"] = [
            "production_auth_secret_oauth_provider_change"]
        self.assertIn("BLOCKED_OWNER", codes(evaluate(manifest, "READY")))

    def test_a_paid_model_trial_is_blocked_owner(self):
        manifest = green()
        manifest["authority"]["requested"] = ["paid_model_trial"]
        self.assertIn("BLOCKED_OWNER", codes(evaluate(manifest, "READY")))

    def test_an_irreversible_delete_is_blocked_owner(self):
        manifest = green()
        manifest["authority"]["requested"] = ["irreversible_delete"]
        self.assertIn("BLOCKED_OWNER", codes(evaluate(manifest, "READY")))

    def test_no_requested_action_is_green(self):
        self.assertEqual(evaluate(green(), "READY")["state"], "READY")


# ---------------------------------------------------------------------------
# Devices: Tokyo first, and a grant is local.
# ---------------------------------------------------------------------------

class DevicesAreGatedInOrder(unittest.TestCase):

    def test_dell_cannot_canary_before_tokyo_has_accepted(self):
        manifest = green()
        manifest["devices"].append(green_device(2, "device:llm-lab-dell"))
        result = evaluate(manifest, "CANARY", "device:llm-lab-dell")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("BLOCKED_DEVICE_ORDER", codes(result))

    def test_dell_can_canary_after_tokyo_accepted_independently(self):
        manifest = green()
        manifest["devices"].append(green_device(2, "device:llm-lab-dell"))
        manifest["devices"][0]["accepted"] = True
        manifest["devices"][0]["independent_acceptance"] = True
        result = evaluate(manifest, "CANARY", "device:llm-lab-dell")
        self.assertEqual(result["state"], "CANARY", result["blocked"])

    def test_an_acceptance_without_independent_acceptance_does_not_unlock_dell(self):
        manifest = green()
        manifest["devices"].append(green_device(2, "device:llm-lab-dell"))
        manifest["devices"][0]["accepted"] = True
        manifest["devices"][0]["independent_acceptance"] = None
        result = evaluate(manifest, "CANARY", "device:llm-lab-dell")
        self.assertIn("BLOCKED_DEVICE_ORDER", codes(result))

    def test_a_remote_grant_is_not_a_local_operator_grant(self):
        manifest = green()
        manifest["devices"][0]["grant"]["source"] = "gateway-asserted"
        result = evaluate(manifest, "CANARY")
        self.assertIn("BLOCKED_DEVICE_GRANT", codes(result))

    def test_an_absent_grant_blocks(self):
        manifest = green()
        manifest["devices"][0]["grant"]["present"] = False
        result = evaluate(manifest, "CANARY")
        self.assertIn("BLOCKED_DEVICE_GRANT", codes(result))

    def test_a_grant_without_scope_blocks(self):
        manifest = green()
        manifest["devices"][0]["grant"]["scope"] = []
        result = evaluate(manifest, "CANARY")
        self.assertIn("BLOCKED_DEVICE_GRANT", codes(result))

    def test_a_gateway_timeout_is_blocked_device(self):
        manifest = green()
        manifest["devices"][0]["reachability"] = {
            "reachable": False, "observed_at": OBSERVED_AT,
            "error": "gateway timeout after 10s"}
        result = evaluate(manifest, "CANARY")
        self.assertEqual(result["state"], "BLOCKED")
        self.assertIn("BLOCKED_DEVICE", codes(result))

    def test_a_resource_conflict_is_blocked_resource(self):
        manifest = green()
        manifest["devices"][0]["resource"] = {
            "checked": True, "conflict": True, "detail": "another Run holds the port"}
        result = evaluate(manifest, "CANARY")
        self.assertIn("BLOCKED_RESOURCE", codes(result))

    def test_an_unknown_device_id_is_refused(self):
        with self.assertRaises(captain.ManifestRefused):
            evaluate(green(), "CANARY", "device:no-such-device")


# ---------------------------------------------------------------------------
# Promote.
# ---------------------------------------------------------------------------

class PromoteIsItsOwnGate(unittest.TestCase):

    def test_promote_needs_owner_approval(self):
        manifest = promote_ready()
        manifest["owner_approval"] = None
        result = evaluate(manifest, "PROMOTE")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))

    def test_an_owner_approval_without_a_reference_is_refused(self):
        manifest = promote_ready()
        manifest["owner_approval"] = {"granted": True, "reference": "UNKNOWN",
                                      "granted_at": OBSERVED_AT}
        result = evaluate(manifest, "PROMOTE")
        self.assertIn("BLOCKED_OWNER", codes(result))

    def test_promote_needs_independent_acceptance(self):
        manifest = promote_ready()
        manifest["independent_acceptance"] = {"verdict": "UNKNOWN"}
        result = evaluate(manifest, "PROMOTE")
        self.assertIn("BLOCKED_QA", codes(result))

    def test_promote_needs_a_legacy_observation_during_the_canary(self):
        manifest = promote_ready()
        manifest["devices"][0]["canary"]["legacy_during"] = False
        result = evaluate(manifest, "PROMOTE")
        self.assertIn("BLOCKED_LEGACY_PARITY", codes(result))

    def test_a_failing_canary_probe_blocks_promote(self):
        manifest = promote_ready()
        manifest["devices"][0]["canary"]["probes"] = [
            {"name": "next-health", "verdict": "FAIL"}]
        result = evaluate(manifest, "PROMOTE")
        self.assertIn("BLOCKED_DEVICE", codes(result))

    def test_a_canary_with_no_probes_is_unknown(self):
        manifest = promote_ready()
        manifest["devices"][0]["canary"]["probes"] = []
        result = evaluate(manifest, "PROMOTE")
        self.assertIn("BLOCKED_UNKNOWN", codes(result))


# ---------------------------------------------------------------------------
# Rollback: Next-only, and idempotent.
# ---------------------------------------------------------------------------

class RollbackIsNextOnlyAndIdempotent(unittest.TestCase):

    def test_rolling_back_nothing_installed_is_refused(self):
        with self.assertRaises(captain.RollbackRefused) as caught:
            captain.build_rollback_plan(green())
        self.assertEqual(caught.exception.code, "ROLLBACK_NOTHING_INSTALLED")

    def test_a_previous_equal_to_installed_is_a_no_op_and_is_refused(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-2"
        manifest["next"]["previous_revision"] = "rev-2"
        with self.assertRaises(captain.RollbackRefused) as caught:
            captain.build_rollback_plan(manifest)
        self.assertEqual(caught.exception.code, "ROLLBACK_SAME_REVISION")

    def test_a_first_install_withdraws_rather_than_reverting(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        plan_document = captain.build_rollback_plan(manifest)
        self.assertEqual(plan_document["mode"], "withdraw")
        ids = [step["id"] for step in plan_document["steps"]]
        self.assertIn("remove-next-unit", ids)
        self.assertIn("remove-next-state", ids)
        self.assertNotIn("repoint-current", ids)

    def test_a_second_install_reverts_to_the_previous_revision(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-2"
        manifest["next"]["previous_revision"] = "rev-1"
        plan_document = captain.build_rollback_plan(manifest)
        self.assertEqual(plan_document["mode"], "revert")
        repoint = [step for step in plan_document["steps"]
                   if step["id"] == "repoint-current"][0]
        self.assertEqual((repoint["from"], repoint["to"]), ("rev-2", "rev-1"))

    def test_no_step_writes_a_legacy_resource(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-2"
        manifest["next"]["previous_revision"] = "rev-1"
        plan_document = captain.build_rollback_plan(manifest)
        legacy_targets = {manifest["legacy"]["service_unit"], manifest["legacy"]["state_root"],
                          manifest["legacy"]["runtime_root"], manifest["legacy"]["lock_path"]}
        for step in plan_document["steps"]:
            if step["kind"] == "read-only-legacy":
                continue
            self.assertNotIn(step["target"], legacy_targets)

    def test_a_plan_step_that_targets_legacy_is_refused(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        plan_document = captain.build_rollback_plan(manifest)
        plan_document["steps"].append({
            "id": "sneaky", "kind": "next-only", "idempotent": True,
            "action": "stop", "target": manifest["legacy"]["service_unit"]})
        with self.assertRaises(captain.RollbackRefused) as caught:
            captain.assert_next_only(plan_document, manifest)
        self.assertEqual(caught.exception.code, "ROLLBACK_TOUCHES_LEGACY")

    def test_a_nested_path_into_legacy_is_refused(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        plan_document = captain.build_rollback_plan(manifest)
        plan_document["steps"].append({
            "id": "sneaky", "kind": "next-only", "idempotent": True,
            "action": "rm", "target": "/srv/legacy/state/next"})
        with self.assertRaises(captain.RollbackRefused):
            captain.assert_next_only(plan_document, manifest)

    def test_an_unknown_step_kind_is_refused(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        plan_document = captain.build_rollback_plan(manifest)
        plan_document["steps"].append({
            "id": "odd", "kind": "mutate-legacy", "idempotent": True,
            "action": "restart", "target": "anything"})
        with self.assertRaises(captain.RollbackRefused) as caught:
            captain.assert_next_only(plan_document, manifest)
        self.assertEqual(caught.exception.code, "ROLLBACK_UNKNOWN_STEP_KIND")

    def test_a_dry_run_writes_and_executes_nothing(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        run = captain.rollback_dry_run(manifest)
        self.assertEqual(run["mode"], "DRY_RUN")
        self.assertEqual(run["writes"], [])
        self.assertEqual(run["executes"], [])
        self.assertTrue(run["changed"])

    def test_applying_a_plan_twice_changes_nothing(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        first = captain.rollback_dry_run(manifest)
        second = captain.rollback_dry_run(manifest, applied=first["pending"])
        self.assertFalse(second["changed"])
        self.assertEqual(second["pending"], [])
        self.assertEqual(second["writes"], [])
        self.assertEqual(second["applied"], [step["id"] for step in first["steps"]])

    def test_every_step_claims_idempotency(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-2"
        manifest["next"]["previous_revision"] = "rev-1"
        for step in captain.build_rollback_plan(manifest)["steps"]:
            self.assertTrue(step["idempotent"], step["id"])

    def test_execution_without_owner_approval_is_refused(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        run = captain.build_rollback_plan(manifest)
        with self.assertRaises(captain.ExecutionRefused) as caught:
            captain.execute_plan(run, lambda step: None, manifest=manifest,
                                 evidence_dir="/tmp/evidence")
        self.assertEqual(caught.exception.code, "BLOCKED_OWNER")

    def test_execution_outside_the_canary_is_refused(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        run = captain.build_rollback_plan(manifest)
        with self.assertRaises(captain.ExecutionRefused) as caught:
            captain.execute_plan(run, lambda step: None, manifest=manifest,
                                 owner_approval={"granted": True},
                                 evidence_dir="/tmp/evidence",
                                 environment="production")
        self.assertEqual(caught.exception.code, "BLOCKED_OWNER")

    def test_execution_without_an_evidence_directory_is_refused(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        run = captain.build_rollback_plan(manifest)
        with self.assertRaises(captain.ExecutionRefused):
            captain.execute_plan(run, lambda step: None, manifest=manifest,
                                 owner_approval={"granted": True})

    def test_execution_drives_each_step_once(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        run = captain.build_rollback_plan(manifest)
        seen: list[str] = []
        receipt = captain.execute_plan(
            run, lambda step: seen.append(step["id"]), manifest=manifest,
            owner_approval={"granted": True}, evidence_dir="/tmp/evidence")
        self.assertEqual(seen, [step["id"] for step in run["steps"]])
        self.assertEqual(receipt["executed"], seen)

    def test_a_second_execution_with_the_same_receipt_is_a_no_op(self):
        manifest = green()
        manifest["next"]["installed_revision"] = "rev-1"
        manifest["next"]["previous_revision"] = "UNKNOWN"
        run = captain.build_rollback_plan(manifest)
        first: list[str] = []
        receipt = captain.execute_plan(
            run, lambda step: first.append(step["id"]), manifest=manifest,
            owner_approval={"granted": True}, evidence_dir="/tmp/evidence")
        second: list[str] = []
        again = captain.execute_plan(
            run, lambda step: second.append(step["id"]), manifest=manifest,
            owner_approval={"granted": True}, evidence_dir="/tmp/evidence",
            applied=receipt["executed"])
        self.assertEqual(second, [])
        self.assertEqual(again["executed"], [])
        self.assertEqual(again["skipped"], receipt["executed"])


# ---------------------------------------------------------------------------
# CLI: dry by default, and it has no executor.
# ---------------------------------------------------------------------------

class TheCliIsDryByDefault(unittest.TestCase):

    def _run(self, *arguments: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-I", str(MODULE_PATH), "--manifest",
             str(MANIFEST_PATH), *arguments],
            capture_output=True, text=True, cwd=str(REPO), check=False)

    def test_state_on_the_checked_in_manifest_is_pre_flight(self):
        completed = self._run("state")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["state"], "PRE_FLIGHT")

    def test_evaluate_on_the_checked_in_manifest_is_blocked_and_exits_2(self):
        completed = self._run("evaluate", "--target", "READY")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["state"], "BLOCKED")

    def test_rollback_dry_run_prints_no_writes(self):
        completed = self._run("rollback")
        self.assertEqual(completed.returncode, 3, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["error"],
                         "ROLLBACK_NOTHING_INSTALLED")

    def test_plan_executes_nothing(self):
        completed = self._run("plan")
        self.assertEqual(completed.returncode, 0, completed.stderr)
        document = json.loads(completed.stdout)
        self.assertEqual(document["writes"], [])
        self.assertEqual(document["executes"], [])

    def test_execute_reports_that_the_cli_has_no_executor(self):
        completed = self._run("rollback", "--execute", "--owner-approved", "x",
                              "--evidence", "/tmp/e")
        self.assertEqual(completed.returncode, 2, completed.stderr)
        self.assertEqual(json.loads(completed.stdout)["error"], "EXECUTOR_ABSENT")

    def test_a_malformed_manifest_is_refused_with_exit_3(self):
        completed = subprocess.run(
            [sys.executable, "-I", str(MODULE_PATH), "--manifest",
             str(SCHEMA_PATH), "state"],
            capture_output=True, text=True, cwd=str(REPO), check=False)
        self.assertEqual(completed.returncode, 3)
        self.assertEqual(json.loads(completed.stdout)["error"], "MANIFEST_REFUSED")


# ---------------------------------------------------------------------------
# Mutation controls: each function returns a manifest that must produce the
# stated code. `test_every_reason_code_is_reachable` walks this table, so a
# reason that no fixture can produce is a finding, not a hidden branch.
# ---------------------------------------------------------------------------

def _unknown_source() -> dict:
    manifest = green()
    manifest["source"]["head_sha"] = "UNKNOWN"
    return manifest


def _identity_mismatch() -> dict:
    manifest = green()
    manifest["ci"]["head_sha"] = "f" * 40
    return manifest


def _ci_red() -> dict:
    manifest = green()
    manifest["ci"]["jobs"][0]["conclusion"] = "failure"
    return manifest


def _qa_red() -> dict:
    manifest = green()
    manifest["qa"]["verdict"] = "RED"
    return manifest


def _artifact_bad() -> dict:
    manifest = green()
    manifest["artifacts"][0]["sha256"] = "short"
    return manifest


def _provenance_bad() -> dict:
    manifest = green()
    manifest["artifacts"][0]["provenance"] = {}
    return manifest


def _collision() -> dict:
    manifest = green()
    manifest["next"]["service_unit"] = manifest["legacy"]["service_unit"]
    return manifest


def _legacy_snapshot_bad() -> dict:
    manifest = green()
    manifest["legacy"]["snapshot_pre"]["digest"] = "nope"
    return manifest


def _device_unreachable() -> dict:
    manifest = green()
    manifest["devices"][0]["reachability"] = {
        "reachable": False, "observed_at": OBSERVED_AT, "error": "timeout"}
    return manifest


def _device_grant_missing() -> dict:
    manifest = green()
    manifest["devices"][0]["grant"]["present"] = False
    return manifest


def _device_order() -> dict:
    manifest = green()
    manifest["devices"].append(green_device(2, "device:llm-lab-dell"))
    return manifest


def _resource_conflict() -> dict:
    manifest = green()
    manifest["devices"][0]["resource"] = {"checked": True, "conflict": True}
    return manifest


def _owner_action() -> dict:
    manifest = green()
    manifest["authority"]["requested"] = ["main_merge"]
    return manifest


def _legacy_parity() -> dict:
    manifest = green()
    manifest["legacy"]["must_remain_available"] = False
    return manifest


#: code -> a manifest that must produce it. Target is chosen by the code:
#: device and owner gates need a different target from the static ones.
GUARD_MUTATIONS = (
    _unknown_source,
    _identity_mismatch,
    _ci_red,
    _qa_red,
    _artifact_bad,
    _provenance_bad,
    _collision,
    _legacy_snapshot_bad,
    _device_unreachable,
    _device_grant_missing,
    _device_order,
    _resource_conflict,
    _owner_action,
    _legacy_parity,
)

#: Which target each mutation is evaluated at, so the guard it is meant to
#: exercise is actually reached.
GUARD_TARGETS = {
    _device_unreachable: ("CANARY", None),
    _device_grant_missing: ("CANARY", None),
    _device_order: ("CANARY", "device:llm-lab-dell"),
    _resource_conflict: ("CANARY", None),
}


if __name__ == "__main__":
    unittest.main()
