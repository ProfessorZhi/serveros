#!/usr/bin/env python3
"""G1 — the coverage matrix: 21 declared families, three device classes, and what is UNKNOWN.

WHY THIS FILE EXISTS
--------------------
The projection in `integrations/device-ability-projection/effective.py` answers a per-device,
per-ability question. Issue #249 asks a different one: for the authority's **21 semantic
families**, what is actually installed / observed / ready / allowed on Tokyo, on the Windows
node, and on the Dell A6000 — and where is the answer UNKNOWN rather than false?

The failure this file exists to prevent is the arithmetic that makes a coverage table look
better than it is:

  * a family with **no declared realization** rendered as `0 installed` (it is unowned, not
    empty);
  * a device that was **never observed** rendered as `0 observed` (nobody looked);
  * a device class with **no authority device id** rendered as `NOT_INSTALLED` (the identity
    is unknown, so the word is unavailable);
  * a name-only observation upgraded to identity (the authority's own rule: name-only is a
    PROBABLE match, never SAME);
  * 21 declared semantics read as 21 installed or tested abilities.

Hermetic: the semantic tests use a synthetic manifest and synthetic observations. The one
test that reads the committed matrix checks its shape and provenance, not a live device.
"""
from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HERE = REPO / "integrations" / "device-ability-projection"
FIXTURES = HERE / "fixtures"

FAMILY_IDS = ["G1", *[f"A{i}" for i in range(1, 11)], *[f"B{i}" for i in range(1, 11)]]


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


coverage = _load("device_ability_coverage", HERE / "coverage.py")

FAMILIES = json.loads((FIXTURES / "families.json").read_text(encoding="utf-8"))["families"]
SOURCE_REVISION = "4a46f2b178e5f93de12b7a27c9d0df00e45683ea"

MANIFEST = {
    "schema_version": 3,
    "abilities": [
        {
            "type": "skill", "id": "Owner/alpha", "name": "alpha",
            "stable_id": "Owner/alpha",
            "product_role": {"primary": "A1", "cross_refs": ["B1"]},
            "revisions": [{"installations": [
                {"device_id": "dev-x", "agent": "claude-code"}]}],
        },
        {
            "type": "skill", "id": "Owner/beta", "name": "beta",
            "product_role": {"primary": "A2"},
            "revisions": [{"installations": [
                {"device_id": "dev-x", "agent": "claude-code"}]}],
        },
        {
            "type": "skill", "id": "Owner/gamma", "name": "gamma",
            "revisions": [{"installations": [
                {"device_id": "dev-x", "agent": "claude-code"}]}],
        },
        {
            "type": "mcp", "id": "Owner/delta", "name": "delta",
            "product_role": {"primary": "A1"},
            "revisions": [{"installations": [
                {"device_id": "dev-y", "agent": "codex"}]}],
        },
    ],
}

DEVICES = [
    {
        "id": "dev-x-observed", "label": "dev-x (observed)",
        "device_ref": "device:dev-x", "device_ids": ["dev-x"],
        "state": "OBSERVED", "reason": None,
        "observations": [
            # Strong: stable_id names the identity.
            {"type": "skill", "name": "alpha", "agent": "claude-code",
             "stable_id": "Owner/alpha", "canonical_source": {"kind": "diy"}},
            # Probable: name only. The authority calls this PROBABLE, never SAME.
            {"type": "skill", "name": "beta", "agent": "claude-code",
             "canonical_source": {"kind": "unknown"}},
            # Nothing in the manifest names it.
            {"type": "skill", "name": "unregistered", "agent": "claude-code",
             "canonical_source": {"kind": "unknown"}},
        ],
    },
    {
        "id": "dev-y-not-observed", "label": "dev-y (not observed)",
        "device_ref": None, "device_ids": ["dev-y"],
        "state": "NOT_OBSERVED",
        "reason": "authority records installations for dev-y; no snapshot was supplied",
    },
    {
        "id": "unresolved-class", "label": "a class with no device id",
        "device_ref": None, "device_ids": [],
        "state": "UNRESOLVED",
        "reason": "the authority declares no device id for this class",
    },
]


def matrix(**over) -> dict:
    arguments = dict(families=FAMILIES, manifest=MANIFEST,
                     source_revision=SOURCE_REVISION, devices=DEVICES)
    arguments.update(over)
    return coverage.coverage(**arguments)


def row(result: dict, family: str) -> dict:
    for item in result["families"]:
        if item["family"] == family:
            return item
    raise KeyError(family)


def cell(result: dict, family: str, device: str) -> dict:
    return row(result, family)["devices"][device]


# ---------------------------------------------------------------------------
# The vocabulary is complete, and it is a vocabulary (not a registry)
# ---------------------------------------------------------------------------

class TestTheTwentyOneFamilies:
    def test_the_fixture_is_exactly_the_declared_twenty_one(self):
        assert [f["id"] for f in FAMILIES] == FAMILY_IDS

    def test_the_fixture_carries_no_artifact_identity(self):
        for family in FAMILIES:
            assert set(family) == {"id", "name"}
            assert not any(key in family for key in
                           ("revision", "fingerprint", "installations", "path"))

    def test_the_matrix_has_one_row_per_family_in_declared_order(self):
        result = matrix()
        assert [item["family"] for item in result["families"]] == FAMILY_IDS
        assert result["counts"]["families"] == 21


# ---------------------------------------------------------------------------
# Declared, realized, installed, observed are four different facts
# ---------------------------------------------------------------------------

class TestFourDifferentFacts:
    def test_a_realization_is_declared_by_product_role_primary(self):
        assert row(matrix(), "A1")["realizations"] == ["Owner/alpha", "Owner/delta"]
        assert row(matrix(), "A2")["realizations"] == ["Owner/beta"]

    def test_no_primary_realization_is_unowned_not_zero_coverage(self):
        b1 = row(matrix(), "B1")
        assert b1["realizations"] == []
        assert b1["gap"] == "NO_PRIMARY_REALIZATION"
        # B1 is still referenced by a realization that belongs to another family.
        assert b1["cross_referenced_by"] == ["Owner/alpha"]
        assert row(matrix(), "B3")["cross_referenced_by"] == []

    def test_installed_is_the_authority_record_not_the_observation(self):
        # alpha is installed on dev-x; delta is installed on dev-y only.
        assert cell(matrix(), "A1", "dev-x-observed")["installed"] == 1

    def test_an_identity_without_product_role_is_counted_but_not_attributed(self):
        counts = matrix()["counts"]
        assert counts["identities"] == 4
        assert counts["identities_unclassified"] == 1
        # It is still installed; it simply cannot be placed under a family.
        assert counts["identities_unclassified_installed"]["dev-x-observed"] == ["Owner/gamma"]


# ---------------------------------------------------------------------------
# Observation strength: strong / probable / unmatched
# ---------------------------------------------------------------------------

class TestObservationStrength:
    def test_a_stable_id_match_is_strong(self):
        assert cell(matrix(), "A1", "dev-x-observed")["observed_strong"] == 1

    def test_a_name_only_match_is_probable_and_never_upgraded(self):
        a2 = cell(matrix(), "A2", "dev-x-observed")
        assert a2["observed_strong"] == 0
        assert a2["observed_probable"] == 1

    def test_an_unmatched_observation_is_not_assigned_to_a_family(self):
        result = matrix()
        # The unmatched observation is reported once, at the device level...
        assert result["devices"][0]["counts"]["observed_unmatched"] == 1
        # ...and never invented into a family row.
        for item in result["families"]:
            for device in item["devices"].values():
                assert "observed_unmatched" not in device


# ---------------------------------------------------------------------------
# Not observed is not false; unresolved is not not-installed
# ---------------------------------------------------------------------------

class TestTheUnknowns:
    def test_a_not_observed_device_reports_authority_installations_but_no_observation(self):
        cell_y = cell(matrix(), "A1", "dev-y-not-observed")
        assert cell_y["identity"] == "RESOLVED"
        assert cell_y["installed"] == 1
        assert cell_y["observed"] == "NOT_OBSERVED"
        assert cell_y["observed_strong"] == "UNKNOWN"

    def test_an_unresolved_device_class_answers_unknown_not_false(self):
        unresolved = cell(matrix(), "A1", "unresolved-class")
        assert unresolved["identity"] == "UNRESOLVED"
        assert unresolved["installed"] == "UNKNOWN"
        assert unresolved["observed"] == "UNKNOWN"
        assert unresolved["observed_strong"] == "UNKNOWN"

    def test_ready_and_grant_are_unknown_when_no_health_or_policy_was_reported(self):
        for item in matrix()["families"]:
            for device in item["devices"].values():
                assert device["ready"] == "UNKNOWN"
                assert device["grant"] == "UNKNOWN"

    def test_effective_is_always_a_list_never_a_boolean(self):
        for item in matrix()["families"]:
            for device in item["devices"].values():
                assert isinstance(device["effective"], list)


# ---------------------------------------------------------------------------
# Malformed inputs are refused rather than answered
# ---------------------------------------------------------------------------

class TestRefusal:
    def test_a_projection_without_a_source_revision_is_refused(self):
        with pytest.raises(coverage.Refused):
            matrix(source_revision="")

    def test_a_duplicate_family_id_is_refused(self):
        with pytest.raises(coverage.Refused):
            matrix(families=[*FAMILIES, FAMILIES[0]])

    def test_an_observed_device_without_observations_is_refused(self):
        broken = [dict(DEVICES[0], observations=None)]
        with pytest.raises(coverage.Refused):
            matrix(devices=broken)

    def test_an_unresolved_device_with_an_id_is_refused(self):
        broken = [dict(DEVICES[2], device_ids=["dev-z"])]
        with pytest.raises(coverage.Refused):
            matrix(devices=broken)


# ---------------------------------------------------------------------------
# The committed matrix: shape, provenance and the absence of private paths
# ---------------------------------------------------------------------------

class TestTheCommittedMatrix:
    PATH = HERE / "DEVICE-COVERAGE-MATRIX.md"

    def text(self) -> str:
        assert self.PATH.exists(), "the coverage matrix has not been generated"
        return self.PATH.read_text(encoding="utf-8")

    def test_every_declared_family_has_a_row(self):
        text = self.text()
        for family in FAMILY_IDS:
            assert re.search(rf"^\| `?{family}`? \|", text, re.MULTILINE), family

    def test_the_provenance_is_pinned_to_the_authority_revision(self):
        assert SOURCE_REVISION in self.text()

    def test_the_generated_file_says_what_it_is_not(self):
        text = self.text()
        assert "not a registry" in text.lower()
        assert "UNKNOWN" in text

    def test_no_private_host_path_entered_the_repository(self):
        text = self.text()
        for needle in ("/data/home/", "/home/", "VM-16-5-ubuntu", "C:\\Users"):
            assert needle not in text, needle

    def test_the_two_unowned_families_are_named(self):
        text = self.text()
        assert "NO_PRIMARY_REALIZATION" in text
        assert "B1" in text and "B3" in text
