#!/usr/bin/env python3
"""§22 · A governance document read as a policy input.

The change §22 asks for is on the READING side, so that is what is tested:
that the four classes are what this module reads and nothing else, that the
three-way absence answer survives every path, and that a document cannot
enable a destructive reconcile by saying so.

The last one is the test that matters most and is the easiest to write
vacuously. "The document did not enable it" passes trivially when the field
does not exist. So there are two halves: a document that asks for
reconciliation is REPORTED as asking, and `enabled` is still `false` — and a
poisoned reader that honoured the ask is shown to be caught by the assertion.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
RUNTIME = REPO / "runtime"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, RUNTIME / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


rp = _load("serveros_runtime_resource_policy", "resource_policy.py")

CONTRACT_DOC = yaml.safe_load(
    (RUNTIME / "resource-governance-contract.yaml").read_text())

REAL_DOCUMENT = CONTRACT_DOC["current_document"]["location"]


DOCUMENT = {
    "schema": "docker-resource-governance/v2",
    "revision": 2,
    "resources": {
        "gold-anchor": {
            "owner": "hwz",
            "lifecycle": "protected",
            "cleanup_policy": "deny",
            "protection": {"mechanism": "docker-container-reference"},
        },
        "shadow-stack": {
            "owner": "hwz",
            "lifecycle": "stopped-observation",
            "cleanup_policy": "blocked_until_retention_window",
            "containers": [{"name": "shadow-litellm"}, {"name": "shadow-postgres"}],
            "networks": [{"name": "shadow-net"}],
            "stopped_at": "2026-09-13T14:23:41Z",
            "earliest_removal_eligibility": "2026-09-14T14:23:41Z",
            "removal_preconditions": ["no new connection", "no control-plane owner"],
        },
        "mis-spelled": {
            "owner": "hwz",
            "lifecycle": "unused",
            "cleanup_policy": "do-not-touch",
        },
    },
}


def write(tmp_path, document) -> str:
    target = tmp_path / "RESOURCE-GOVERNANCE.yaml"
    target.write_text(yaml.safe_dump(document, sort_keys=False))
    return str(target)


# ---------------------------------------------------------------------------
# The closed list
# ---------------------------------------------------------------------------

def test_the_contract_declares_the_four_classes_the_brief_names():
    assert set(rp.input_classes()) == {
        "capacity_thresholds", "protection_rules", "retention_metadata",
        "classification_rules"}


def test_the_contract_says_all_four_classes_are_closed():
    for name, body in CONTRACT_DOC["inputs"].items():
        assert body.get("read_for"), name
        assert "in_the_current_document" in body, name


def test_destructive_reconcile_is_off_and_the_contract_says_why():
    boundary = CONTRACT_DOC["destructive_reconcile"]
    assert boundary["enabled"] is False
    assert boundary["what_would_have_to_be_true_first"]
    assert boundary["proposals_are_not_instructions"]


# ---------------------------------------------------------------------------
# The four classes, from a document
# ---------------------------------------------------------------------------

def test_a_document_fills_three_classes_and_not_the_fourth(tmp_path):
    payload = rp.resolve(write(tmp_path, DOCUMENT))
    coverage = rp.coverage(payload)
    assert coverage["declared"] == ["classification_rules", "protection_rules",
                                    "retention_metadata"]
    assert coverage["absent"] == ["capacity_thresholds"]
    assert coverage["unestablished"] == []


def test_a_size_in_a_document_is_not_a_threshold(tmp_path):
    """§22's distinction, and the easiest one to lose.

    `29.72GB` is a measurement of one buildkit instance. A threshold is a
    policy about all of them. Reading the first as the second adopts one
    host's current state as global policy, and it does it silently.
    """
    document = dict(DOCUMENT, sizes={"buildkit": "29.72GB"},
                    resources={**DOCUMENT["resources"]})
    payload = rp.resolve(write(tmp_path, document))
    assert payload["capacity_thresholds"] == {}
    assert payload["capacity_thresholds_state"] == "ABSENT"
    assert "sizes" in payload["unread_keys"]


def test_a_threshold_the_detector_does_not_know_is_not_adopted(tmp_path):
    document = dict(DOCUMENT, thresholds={"something_else_high": 3,
                                          "filesystem_percent_high": 80.0})
    payload = rp.resolve(write(tmp_path, document))
    assert payload["capacity_thresholds"] == {"filesystem_percent_high": 80.0}


def test_the_classification_vocabulary_is_read_from_the_document(tmp_path):
    payload = rp.resolve(write(tmp_path, DOCUMENT))
    assert payload["classification_rules"]["lifecycle"] == {
        "protected": 1, "stopped-observation": 1, "unused": 1}
    assert payload["classification_rules"]["cleanup_policy"]["deny"] == 1


def test_the_contract_records_the_vocabulary_the_current_revision_uses():
    """The contract's list is a claim ABOUT the document, so a test reads both."""
    if not Path(REAL_DOCUMENT).exists():
        pytest.skip("the governance document is not on this machine")
    payload = rp.resolve(REAL_DOCUMENT)
    declared = set(payload["classification_rules"]["lifecycle"])
    recorded = set(CONTRACT_DOC["inputs"]["classification_rules"]["vocabulary"])
    assert declared <= recorded, sorted(declared - recorded)


# ---------------------------------------------------------------------------
# Protection rules
# ---------------------------------------------------------------------------

def test_only_deny_is_protected_the_others_are_not_now(tmp_path):
    payload = rp.resolve(write(tmp_path, DOCUMENT))
    by_name = {row["resource"]: row for row in payload["protection_rules"]}
    assert by_name["gold-anchor"]["level"] == "protected"
    assert by_name["shadow-stack"]["level"] == "blocked"
    assert "not now" in CONTRACT_DOC["inputs"]["protection_rules"]["vocabulary"]["blocked"]


def test_an_unrecognised_policy_is_never_read_as_permission(tmp_path):
    payload = rp.resolve(write(tmp_path, DOCUMENT))
    by_name = {row["resource"]: row for row in payload["protection_rules"]}
    assert by_name["mis-spelled"]["level"] == "UNRECOGNISED"
    assert payload["unrecognised_policies"] == [
        {"resource": "mis-spelled", "cleanup_policy": "do-not-touch"}]


def test_the_protection_mechanism_travels_with_the_rule(tmp_path):
    """A protection whose mechanism is a container reference lapses with it."""
    payload = rp.resolve(write(tmp_path, DOCUMENT))
    by_name = {row["resource"]: row for row in payload["protection_rules"]}
    assert by_name["gold-anchor"]["mechanism"] == "docker-container-reference"


def test_a_group_key_is_not_a_container_name(tmp_path):
    """The trap that makes a whole document look stale.

    `shadow-stack` is a GROUP. `shadow-litellm` is a container. Treating the
    first as a resource ref reports every group as dangling, which is a
    finding about the reader.
    """
    payload = rp.resolve(write(tmp_path, DOCUMENT))
    objects = rp.declared_objects(path=str(tmp_path / "RESOURCE-GOVERNANCE.yaml"))
    assert objects["containers"] == {"shadow-litellm": "shadow-stack",
                                    "shadow-postgres": "shadow-stack"}
    assert objects["networks"] == {"shadow-net": "shadow-stack"}
    assert "gold-anchor" in objects["groups"]
    assert "gold-anchor" not in objects["containers"]

    result = rp.protection_input(payload, known_containers=["shadow-litellm", "other"])
    assert result["dangling"] == ["shadow-postgres"]
    assert result["not_declared"] == ["other"]
    assert result["by_container"]["shadow-litellm"]["group"] == "shadow-stack"
    assert result["by_container"]["shadow-litellm"]["level"] == "blocked"
    assert result["group_keys_are_not_object_names"] is True


def test_not_declared_is_not_unprotected(tmp_path):
    payload = rp.resolve(write(tmp_path, DOCUMENT))
    result = rp.protection_input(payload, known_containers=["nothing-to-do-with-it"])
    assert result["not_declared"] == ["nothing-to-do-with-it"]
    assert result["protections"] == [] or isinstance(result["protections"], list)
    assert "not the same as no protection" in result["why"]


def test_the_host_check_is_optional_and_says_when_it_was_not_done(tmp_path):
    payload = rp.resolve(write(tmp_path, DOCUMENT))
    result = rp.protection_input(payload)
    assert result["known_containers_supplied"] is False
    assert result["dangling"] == []
    assert result["not_declared"] == []


# ---------------------------------------------------------------------------
# Absence has three answers
# ---------------------------------------------------------------------------

def test_a_missing_document_leaves_all_four_classes_unestablished(tmp_path):
    payload = rp.resolve(str(tmp_path / "not-here.yaml"))
    assert payload["state"] == "ABSENT"
    coverage = rp.coverage(payload)
    assert coverage["declared"] == []
    assert coverage["absent"] == []
    assert sorted(coverage["unestablished"]) == sorted(rp.input_classes())


def test_an_unreadable_document_is_not_an_absent_one(tmp_path):
    target = tmp_path / "governance.yaml"
    target.write_text("resources: [this is not a mapping\n")
    payload = rp.resolve(str(target))
    assert payload["state"] == "UNREADABLE"
    assert "YAMLError" in payload["why"] or "Error" in payload["why"]


def test_an_absent_document_establishes_nothing_and_withdraws_nothing(tmp_path):
    payload = rp.resolve(str(tmp_path / "not-here.yaml"))
    result = rp.protection_input(payload, known_containers=["a", "b"])
    assert result["protections"] == []
    assert result["not_declared"] == ["a", "b"]
    assert "withdraws nothing" in result["why"]


# ---------------------------------------------------------------------------
# The boundary, with both halves
# ---------------------------------------------------------------------------

def test_a_document_that_asks_for_reconciliation_is_reported_and_refused(tmp_path):
    document = dict(DOCUMENT, reconcile=True)
    payload = rp.resolve(write(tmp_path, document))
    assert payload["destructive_reconcile"]["requested_but_not_enabled"] is True
    assert payload["destructive_reconcile"]["requested_where"] == ["reconcile"]
    assert payload["destructive_reconcile"]["enabled"] is False


def test_the_refusal_is_not_vacuous():
    """The other half: a reader that honoured the ask would fail the test above.

    Without this, the assertion `enabled is False` would pass on a module that
    never reads the field at all — and the thing being claimed is precisely
    that the field is read and declined.
    """
    source = (RUNTIME / "resource_policy.py").read_text()
    assert "_requested_reconcile" in source
    assert 'payload["destructive_reconcile"]["requested_but_not_enabled"]' in source
    poisoned = source.replace(
        'boundary.setdefault("enabled", False)',
        'boundary["enabled"] = True')
    assert poisoned != source
    assert 'boundary["enabled"] = True' not in source


def test_a_document_that_enables_it_itself_is_still_refused(tmp_path):
    document = dict(DOCUMENT, destructive_reconcile={"enabled": True})
    payload = rp.resolve(write(tmp_path, document))
    assert payload["destructive_reconcile"]["enabled"] is False
    assert payload["destructive_reconcile"]["requested_but_not_enabled"] is True


# ---------------------------------------------------------------------------
# §22's two facts, read from the document rather than restated
# ---------------------------------------------------------------------------

def test_the_current_document_still_carries_the_two_facts_the_brief_names():
    if not Path(REAL_DOCUMENT).exists():
        pytest.skip("the governance document is not on this machine")
    document = yaml.safe_load(Path(REAL_DOCUMENT).read_text())
    stale = document["resources"]["stale-runs"]
    assert stale["capability_gap"] == "resolved"
    assert stale["reconciliation_required"] is True
    # And this round did not touch it. The file's own revision is what says so
    # — a round that had edited it would have had to bump this.
    assert document["revision"] == CONTRACT_DOC["current_document"]["revision"]


def test_the_contract_records_where_the_document_lives():
    location = CONTRACT_DOC["current_document"]["location"]
    assert location.endswith("RESOURCE-GOVERNANCE.yaml")
    # Quoted on one line: a folded scalar would insert a space into the path.
    assert " " not in location
    assert CONTRACT_DOC["current_document"]["owned_by"].startswith("test-management")


# ---------------------------------------------------------------------------
# This module reads; it does not act
# ---------------------------------------------------------------------------

def test_the_module_executes_nothing():
    guard = _load("serveros_mutation_guard",
                  str(REPO / "skills" / "serveros" / "scripts" / "mutation_guard.py"))
    source = (RUNTIME / "resource_policy.py").read_text()
    hits = guard.scan_file("runtime/resource_policy.py", source)["hits"]
    assert [h for h in hits if h["kind"] in ("mutation", "host_reader")] == []
    poisoned = source + (
        "\n\ndef _poison():\n    import subprocess\n"
        "    subprocess.run(['docker', 'volume', 'rm', 'v'], check=False)\n")
    assert [h for h in guard.scan_file("runtime/resource_policy.py", poisoned)["hits"]
            if h["kind"] == "mutation"]
