#!/usr/bin/env python3
"""Tests for the state root's authority semantics, projections, and severity.

What this file is guarding, in one sentence
-------------------------------------------
"it is under `state/serveros`, so it is the truth."

Every test here exists because that sentence is the cheap answer, and because
the cheap answer is wrong in exactly the places people do not look. A generated
health view is under the state root and is not the health of anything. A lock
file is under the state root and is one holder's claim. An external machine
baseline is under the state root and is not the machine.

The second thing being guarded is quieter: the difference between "there is no
producer for this namespace" and "a producer was declared and its output is
missing". A checker that cannot tell them apart, or that closes the gap by
creating the directory, destroys the distinction for every later check — which
is why there is a test asserting this module creates nothing.

Honest scope note. Some of the contract's invariants are enforced in code
(`read_manifest` refuses an authority-claiming manifest, `authority_conflicts`
detects a mirrored path) and some are declared policy that the checks here can
only assert is DECLARED. `test_every_invariant_is_declared` covers the second
kind and says so, because a test that asserted "projections cannot overwrite
authority" while nothing enforced it would be this project's own signature
defect, written into the test suite.
"""
from __future__ import annotations

import importlib.util
import subprocess
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


state_root = _load("serveros_runtime_state_root", "state_root.py")
paths_mod = _load("serveros_runtime_paths", "paths.py")
conformance = _load("serveros_runtime_conformance", "conformance.py")

DATA_ROOT = Path(paths_mod.Paths().root("data"))


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def manifest(**overrides):
    document = {
        "schema": "serveros/projection.v1",
        "authority": False,
        "generated": True,
        "generator": "workspace-management",
        "generated_at": "2026-08-19T10:58:00+08:00",
        "source_refs": ["device:server-beijing"],
    }
    document.update(overrides)
    return document


def projection(tmp_path: Path, name: str, *, ref: str = "projections/environment", **overrides):
    directory = tmp_path / ref / name
    directory.mkdir(parents=True)
    (directory / "projection.yaml").write_text(yaml.safe_dump(manifest(**overrides)))
    (directory / "payload.txt").write_text("derived\n")
    return directory


# ---------------------------------------------------------------------------
# §22 — authority is a namespace-level property
# ---------------------------------------------------------------------------


def test_being_under_the_state_root_does_not_make_a_path_authoritative():
    """The one-sentence defect, as an assertion.

    `runtime/health` and `projections/environment` are both under
    `state/serveros`. One is an authority and one is explicitly not, and a
    classifier keyed on the prefix cannot tell them apart.
    """
    authority = state_root.classify("runtime/health")
    projection = state_root.classify("projections/environment/2026-08-19")

    assert authority["class"] == state_root.AUTHORITY
    assert authority["authority"] is True
    assert projection["class"] == state_root.PROJECTION
    assert projection["authority"] is False
    assert "NOT an authority" in projection["reason"]


def test_a_lock_is_admitted_and_is_not_an_authority():
    """A lock file records that someone CLAIMS a lease.

    If it were read as the lease itself, a lock outliving its holder would
    report an occupied resource forever, and nothing in the file would be
    wrong — which is what makes it worth a separate class rather than being
    folded in with projections.
    """
    lock = state_root.classify("locks/resource-leases/foo.lock")
    assert lock["namespace"] == "locks"
    assert lock["class"] == state_root.NON_AUTHORITY
    assert lock["authority"] is False


def test_the_longest_declared_namespace_wins():
    """`runtime/anomalies` is a projection inside an authority parent.

    Matching only the first segment would return AUTHORITY for it, and every
    child declaration in the contract would be decorative.
    """
    assert state_root.classify("runtime/anomalies/current.json")["class"] == state_root.PROJECTION
    assert state_root.classify("runtime/health/current.json")["class"] == state_root.AUTHORITY
    assert state_root.classify("runtime/registry/resources.yaml")["authority"] is True


def test_an_undeclared_path_is_foreign_and_is_not_a_permission():
    result = state_root.classify("somebodys-directory/thing.json")
    assert result["class"] == state_root.FOREIGN
    assert result["authority"] is False
    assert "not a permission" in result["reason"]


def test_the_root_itself_is_the_ownership_boundary():
    result = state_root.classify("")
    assert result["authority"] is True
    assert "durable state" in result["reason"]


# ---------------------------------------------------------------------------
# §23 — the declared shape, and the three absences
# ---------------------------------------------------------------------------


def test_the_declared_namespaces_match_the_frozen_target_shape():
    contract = state_root.load_contract()
    top = set(contract["namespaces"])
    assert top == {"node", "context", "abilities", "runtime", "fleet",
                   "source-index", "locks", "projections"}
    # And the tuple the resolver actually enforces is the same set — two
    # declarations of one shape is the defect this whole round is about.
    assert top == set(paths_mod.STATE_SERVEROS_TOP_LEVEL)
    assert set(contract["namespaces"]["runtime"]["children"]) == set(
        paths_mod.STATE_SERVEROS_RUNTIME)


def test_a_namespace_with_no_producer_is_absent_not_missing(tmp_path):
    """EXPECTED_ABSENT and MISSING_REQUIRED are different facts.

    Collapsing them means "nobody has written this yet" and "the producer is
    broken" produce the same output, and the second one stops being visible
    the day somebody creates the directory to clear the first.
    """
    report = state_root.namespace_report(root=tmp_path)
    by_name = {row["namespace"]: row for row in report["namespaces"]}

    assert by_name["context"]["state"] == state_root.EXPECTED_ABSENT
    assert "NOT created" in by_name["context"]["note"]
    assert report["counts"]["missing_required"] == 0

    (tmp_path / "context").mkdir()
    after = {row["namespace"]: row for row in
             state_root.namespace_report(root=tmp_path)["namespaces"]}
    assert after["context"]["state"] == state_root.PRESENT


def test_a_required_namespace_that_is_absent_is_the_one_fault(tmp_path):
    contract = state_root.load_contract()
    contract["namespaces"]["runtime"]["children"]["health"]["required"] = True
    report = state_root.namespace_report(root=tmp_path, contract=contract)
    row = next(r for r in report["namespaces"] if r["namespace"] == "runtime/health")
    assert row["state"] == state_root.MISSING_REQUIRED
    assert "the one absence that is a fault" in row["note"]
    assert report["counts"]["missing_required"] == 1


def test_the_checker_creates_nothing(tmp_path):
    """The obvious fix for an absent namespace, and the reason it is wrong.

    Creating it makes all three absence states indistinguishable to every
    later run, permanently, and the run that did it looks like a pass.
    """
    before = sorted(p.name for p in tmp_path.iterdir())
    state_root.namespace_report(root=tmp_path)
    state_root.projection_inventory(root=tmp_path)
    assert sorted(p.name for p in tmp_path.iterdir()) == before == []


def test_an_unreadable_contract_is_an_error_not_an_empty_one():
    """An authority that could not be read must not report as one that
    forbids nothing."""
    with pytest.raises(state_root.ContractUnreadable):
        state_root.load_contract(repo_root=Path("/nonexistent"))


def test_preexisting_entries_and_the_conformance_table_are_the_same_list():
    """Two tables listing the same directories is the failure this codebase
    has already paid for. If they must both exist, a test makes them agree."""
    contract = set(state_root.load_contract()["preexisting"]["entries"])
    table = set(conformance.EXCEPTIONS["state"])
    assert contract == table, (
        f"contract-only={sorted(contract - table)} table-only={sorted(table - contract)}"
    )


# ---------------------------------------------------------------------------
# §24 — projections
# ---------------------------------------------------------------------------


def test_a_projection_is_not_an_authority_whatever_directory_it_is_in(tmp_path):
    projection(tmp_path, "2026-08-19")
    contract = state_root.load_contract()
    result = state_root.classify("projections/environment/2026-08-19", contract=contract)
    assert result["authority"] is False


def test_a_manifest_claiming_authority_is_refused():
    """Including the string `"false"`, which is truthy.

    A manifest that gets this wrong is claiming authority in the one namespace
    where the claim is the thing being denied, and a truthiness check would
    accept it.
    """
    for bad in (True, "false", "no", 1):
        problems = state_root.read_manifest(_dir_with_manifest(bad))["problems"]
        assert any("must be the literal false" in p for p in problems), bad


def _dir_with_manifest(authority_value):
    import tempfile

    directory = Path(tempfile.mkdtemp())
    (directory / "projection.yaml").write_text(yaml.safe_dump(manifest(authority=authority_value)))
    return directory


def test_a_manifest_that_is_not_generated_is_refused():
    directory = _dir_with_manifest(False)
    (directory / "projection.yaml").write_text(
        yaml.safe_dump(manifest(generated="yes")))
    assert any("generated must be the literal true" in p
               for p in state_root.read_manifest(directory)["problems"])


def test_a_projection_directory_without_a_manifest_is_undeclared_material(tmp_path):
    """The only thing in this namespace that could be mistaken for an
    authority, because nothing says otherwise."""
    (tmp_path / "projections" / "mystery").mkdir(parents=True)
    inventory = state_root.projection_inventory(root=tmp_path)
    assert inventory["count"] == 0
    assert [u["ref"] for u in inventory["unlabelled"]] == ["projections/mystery"]
    assert "neither carries a projection.yaml" in inventory["unlabelled"][0]["why"]


def test_a_grouping_directory_of_projections_is_allowed(tmp_path):
    """`projections/environment/2026-08-19/` is the frozen target path, so a
    kind directory holding dated instances must not read as undeclared."""
    projection(tmp_path, "2026-08-19")
    projection(tmp_path, "2026-09-01")
    inventory = state_root.projection_inventory(root=tmp_path)
    assert inventory["count"] == 2
    assert inventory["unlabelled"] == []
    assert {p["ref"] for p in inventory["projections"]} == {
        "projections/environment/2026-08-19", "projections/environment/2026-09-01"}


def test_a_projection_cannot_use_another_projection_as_its_source(tmp_path):
    """A projection derived from a projection is a chain with no authority at
    its root, and it looks stable for as long as nobody regenerates the link
    in the middle."""
    directory = projection(tmp_path, "instance",
                           source_refs=["state/serveros/projections/environment/old"])
    problems = state_root.read_manifest(directory)["problems"]
    assert any("chain with no authority at its root" in p for p in problems)


def test_an_empty_source_ref_list_is_refused(tmp_path):
    """A projection with no source is a source."""
    directory = projection(tmp_path, "instance", source_refs=[])
    assert any("no source is a source" in p
               for p in state_root.read_manifest(directory)["problems"])


def test_a_foreign_generator_is_allowed_and_does_not_gain_authority(tmp_path):
    """The placement is the statement: writing here is not a claim of truth.

    This is the reason the external baseline is in `projections/` rather than
    in an authority namespace, and the reason a foreign tool is welcome to
    write here at all.
    """
    directory = projection(tmp_path, "instance", generator="some-external-scanner",
                           generator_kind="foreign")
    result = state_root.read_manifest(directory)
    assert result["problems"] == []
    assert state_root.classify("projections/environment/instance")["authority"] is False


def test_a_missing_manifest_is_not_a_projection_with_defaults():
    """Defaults would apply to every directory that lacks a manifest,
    including ones that are not projections at all."""
    import tempfile

    result = state_root.read_manifest(Path(tempfile.mkdtemp()))
    assert result["present"] is False
    assert result["authority"] is None
    assert "undeclared material in a promising-looking place" in result["problems"][0]


def test_a_projection_mirroring_an_authority_path_is_an_authority_conflict(tmp_path):
    """One fact with two authoritative answers, and the second one wins
    because it is easier to read."""
    projection(tmp_path, "x", ref="projections/runtime/health")
    conflicts = state_root.authority_conflicts(root=tmp_path)
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert conflict["id"] == "AUTHORITY_CONFLICT"
    assert conflict["severity"] == "HIGH"
    assert conflict["enforcement"] == "WARN"
    assert conflict["blocking"] is False
    assert conflict["authority_namespace"] == "runtime/health"


def test_a_projection_that_merely_agrees_with_an_authority_is_not_a_conflict(tmp_path):
    """Agreement is not conflict, and a check that flagged it would flag every
    correct projection and be switched off within a week."""
    projection(tmp_path, "2026-08-19", ref="projections/environment")
    assert state_root.authority_conflicts(root=tmp_path) == []


def test_a_projection_with_no_source_is_not_quietly_authoritative(tmp_path):
    projection(tmp_path, "instance", source_refs=["state/serveros/projections/other"])
    problems = state_root.read_manifest(tmp_path / "projections/environment/instance")["problems"]
    assert problems, "a self-referencing projection must not read as clean"


def test_every_invariant_is_declared():
    """Declared policy, and this test says so rather than implying enforcement.

    `NO_OVERWRITE_AUTHORITY`, `DRIFT_REGENERATES` and `LOSS_REPRODUCIBLE` are
    statements about how projections are maintained. There is no code path in
    this round that could enforce them — a projection is written by a producer
    that does not exist yet. Asserting they are DECLARED is the honest bound of
    what this suite can check today; claiming more would be the defect the
    module is about.
    """
    contract = state_root.load_contract()
    declared = {i["id"] for i in contract["projections"]["invariants"]}
    assert declared == {
        "NO_SOURCE_AUTHORITY", "NO_OVERWRITE_AUTHORITY", "DRIFT_REGENERATES",
        "LOSS_REPRODUCIBLE", "NO_SELF_REFERENCE",
    }
    for invariant in contract["projections"]["invariants"]:
        assert invariant["means"].strip()


# ---------------------------------------------------------------------------
# §24 — the migration, and the shape it must not have taken
# ---------------------------------------------------------------------------


def test_no_data_root_projections_root_was_introduced():
    """The frozen decision, and the reason for it: a second top-level root for
    derived material becomes a durable-looking location, which is the one
    thing a projections namespace must never be."""
    contract = state_root.load_contract()
    assert contract["projections"]["no_top_level_root"] == "DATA_ROOT/projections"
    assert "projections" not in paths_mod.DATA_ROOT_TOP_LEVEL
    assert not (DATA_ROOT / "projections").exists()


def test_the_projections_namespace_is_inside_the_state_root():
    resolved = paths_mod.Paths().resolve("state/projections")
    assert resolved["path"].startswith(str(paths_mod.Paths().root("state")))
    assert state_root.classify("projections")["authority"] is False


def test_the_environment_baseline_is_where_the_decision_says_it_is():
    state = paths_mod.Paths().root("state")
    moved = state / "projections/environment/2026-08-19"
    assert moved.is_dir(), "the migration the brief froze did not happen"
    assert not (state / "environment").exists(), (
        "the old path still exists. A rename within one filesystem leaves no "
        "residue, and a leftover copy is what `environment-old` would be."
    )
    # No residue of any of the three shapes the brief forbids.
    assert not (state / "environment-old").exists()
    assert not (state / "legacy-environment").exists()
    assert not (state / "environment").is_symlink()
    assert not (moved.parent.parent / "environment").is_symlink()


def test_the_migrated_baseline_declares_itself_non_authoritative():
    state = paths_mod.Paths().root("state")
    result = state_root.read_manifest(state / "projections/environment/2026-08-19")
    assert result["present"] is True
    assert result["problems"] == [], result["problems"]
    document = result["manifest"]
    assert document["authority"] is False
    assert document["generated"] is True
    assert document["generator"]
    assert document["generated_at"]
    assert document["source_refs"]
    assert all("projections" not in ref for ref in document["source_refs"])


def test_the_migrated_baseline_carries_its_retention_rather_than_losing_it():
    """The move put a TTL'd evidence capture inside the state root. It must
    not have silently become durable by moving, and it must not have silently
    become reproducible by being called a projection."""
    state = paths_mod.Paths().root("state")
    document = state_root.read_manifest(state / "projections/environment/2026-08-19")["manifest"]
    assert document["retention"] == "TTL_DELETE"
    assert document["expires_at"]
    assert document["reproducible"] is False
    assert "point-in-time" in document["not_reproducible_because"]
    assert document["supersedes"] == "state/serveros/environment/2026-08-19"


def test_the_old_environment_path_has_no_authority_consumer():
    """The check the brief required BEFORE the move, kept as a test so the
    claim is re-checkable rather than remembered.

    Only the regenerable inventory mentions the old path. If a real consumer
    ever appears, this test fails and the migration is reconsidered instead of
    the consumer being quietly broken.
    """
    result = subprocess.run(
        ["grep", "-rIl", "state/serveros/environment",
         str(REPO / "runtime"), str(REPO / "fleet"), str(REPO / "mcp"),
         str(REPO / "skills"), str(REPO / "docs")],
        capture_output=True, text=True,
    )
    hits = [line for line in result.stdout.splitlines() if line.strip()]
    # The manifest's `supersedes:` line is the one intentional mention.
    assert hits == [str(paths_mod.Paths().root("state")
                        / "projections/environment/2026-08-19/projection.yaml")] or hits == [], hits


def test_the_projections_inventory_reports_the_live_baseline():
    state = paths_mod.Paths().root("state")
    inventory = state_root.projection_inventory(root=state)
    assert inventory["exists"] is True
    refs = {p["ref"] for p in inventory["projections"]}
    assert "projections/environment/2026-08-19" in refs
    assert inventory["unlabelled"] == []


# ---------------------------------------------------------------------------
# §25 — runtime-assets: the semantic role, and what was not moved
# ---------------------------------------------------------------------------


def test_the_distribution_role_resolves_to_the_live_physical_path():
    """The frozen §25 decision: keep the current physical root, give it a
    semantic role, and let `path.resolve` find it.

    This test asserts the CURRENT location deliberately. When the physical
    move eventually happens, this line and `SEMANTIC_ROLES` change together —
    which is the point of the role: no consumer changes.
    """
    import os

    data_root = Path(os.environ.get("DATA_ROOT") or paths_mod.Paths().root("data"))
    resolved = paths_mod.Paths().role("runtime.serveros-distribution")
    assert resolved["path"] == str(data_root / "runtime-assets/hwz-server-os")
    assert resolved["is_role"] is True
    assert resolved["role"] == "runtime.serveros-distribution"
    assert not (data_root / "runtime-assets/serveros").exists(), (
        "the declared-but-absent path was created instead of being replaced by "
        "the role. That is the duplication the role exists to prevent."
    )


def test_the_browsers_root_was_not_relocated():
    """§25: classify, do not move. 1.7 GB of browser distributions moved out
    from under an undeclared consumer set is how a test environment stops
    working for a reason nobody connects to a move."""
    import os

    data_root = Path(os.environ.get("DATA_ROOT") or paths_mod.Paths().root("data"))
    resolved = paths_mod.Paths().role("runtime.browser-distribution")
    assert resolved["path"] == str(data_root / "runtime-assets/browsers")
    for substitute in ("caches", "software", "toolchains"):
        assert not (data_root / substitute / "browsers").exists(), substitute


def test_an_undeclared_role_is_refused_rather_than_defaulted():
    with pytest.raises(paths_mod.PathRefused, match="not a declared semantic role"):
        paths_mod.Paths().role("runtime.some-artifact-i-just-invented")


# ---------------------------------------------------------------------------
# §26 — severity and enforcement are orthogonal
# ---------------------------------------------------------------------------


def test_high_warn_is_non_blocking_and_says_why():
    """The row that could not be argued with: `[HIGH] OPEN`, exit 0, and no
    field anywhere explaining the pair."""
    row = conformance.finding("AUTHORITY_CONFLICT", "state/serveros", "d", "o", "a", "HIGH")
    assert row["severity"] == "HIGH"
    assert row["enforcement"] == "WARN"
    assert row["blocking"] is False
    assert row["enforcement_reason"], "a HIGH/WARN row must carry its reason"


def test_high_block_is_blocking():
    row = conformance.finding("AUTHORITY_CONFLICT", "x", "d", "o", "a", "HIGH",
                              enforcement="BLOCK")
    assert row["blocking"] is True


def test_blocking_is_derived_from_enforcement_not_carried_independently():
    """A field that can disagree with the thing it summarizes eventually
    will. Deriving it makes the disagreement unrepresentable."""
    assert conformance.BLOCKING_BY_ENFORCEMENT == {
        "OBSERVE": False, "WARN": False, "BLOCK": True}
    for enforcement, expected in conformance.BLOCKING_BY_ENFORCEMENT.items():
        assert conformance.blocking(enforcement) is expected


def test_an_unknown_enforcement_is_not_treated_as_non_blocking():
    """A typo must not silently disable a gate."""
    with pytest.raises(ValueError, match="not a declared enforcement"):
        conformance.blocking("BLOCKK")


def test_an_unknown_severity_is_refused():
    with pytest.raises(ValueError, match="not a declared severity"):
        conformance.finding("UNDECLARED_ENTRY", "x", "d", "o", "a", "SEVERE")


def test_severity_never_grants_automation():
    """Mechanical, across the whole severity range: a finding does not become
    actionable by being severe. Enforced by comparing the derived fields at
    every severity, which is the only way to keep the property from decaying
    one special case at a time."""
    seen = set()
    for severity in conformance.SEVERITIES:
        row = conformance.finding("UNDECLARED_ENTRY", "x", "d", "o", "a", severity)
        seen.add((row["enforcement"], row["blocking"]))
    assert seen == {("WARN", False)}


def test_the_summary_does_not_count_severity_as_blocking():
    """The live contradiction §26 names: `blocking_open` counted severity==HIGH
    while the process exited 0.

    Asserted against the `blocking_open` producer specifically. A blanket
    "severity is not in this file" assertion would be false — `high_open_
    nonblocking` legitimately reads severity, and the point is that the two
    counters are different questions, not that severity must not be named.
    """
    source = (RUNTIME / "conformance.py").read_text()
    start = source.index('"blocking_open"')
    end = source.index('"high_open_nonblocking"', start)
    producer = source[start:end]
    assert 'r["blocking"]' in producer
    assert "severity" not in producer, producer
    # And the derived-blocking counter is what drives the exit code, so a
    # warning cannot fail a build by being severe.
    tail = source[end:]
    assert 'return 1 if report["summary"]["blocking_open"] else 0' in tail


def test_phase_1_allows_high_warn_for_the_declared_cases():
    """unknown owner, declared-absent, preexisting namespace — the three the
    brief names as warn-only this phase."""
    for fid in ("UNDECLARED_ENTRY", "DECLARED_ENTRY_ABSENT", "EXEMPT_ENTRY"):
        assert conformance.enforcement_for(fid) in ("OBSERVE", "WARN")
        assert conformance.blocking(conformance.enforcement_for(fid)) is False


def test_every_check_declares_an_enforcement():
    """A check with no declared enforcement would silently default to
    OBSERVE, which is indistinguishable from a check that never ran."""
    for fid in ("UNDECLARED_ENTRY", "DECLARED_ENTRY_ABSENT", "EXEMPT_ENTRY",
                "DECLARATION_CONFLICT", "AUTHORITY_CONFLICT", "CHECK_UNAVAILABLE"):
        assert fid in conformance.PHASE_1_ENFORCEMENT, fid


def test_a_check_that_could_not_run_is_reported():
    """A check that silently does not run reports exactly what a check that
    found nothing reports."""
    row = conformance.finding("CHECK_UNAVAILABLE", "state/serveros", "d", "o", "a", "HIGH")
    assert row["blocking"] is False
    assert "not a check that passed" in row["enforcement_reason"]


def test_the_rendered_row_shows_all_three_fields():
    """Printing the severity alone is what produced the uninterpretable row."""
    source = (RUNTIME / "conformance.py").read_text()
    assert "blocking={str(row['blocking']):<5}" in source
    assert "row['enforcement']" in source
