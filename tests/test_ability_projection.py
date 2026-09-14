#!/usr/bin/env python3
"""Tests for the local capability projection (§27/§28).

The reader half of §28 is already covered in `test_daemon.py`, and covered
well: it distinguishes fresh from stale from never-materialized, and refuses to
report an unreadable projection as an empty one. This file covers the writer,
whose failure modes are the mirror image — every one of them ends with the
reader being confidently wrong rather than with an error.

So the tests below are mostly about what the writer must NOT do: must not
advance `generated_at` when nothing was read, must not erase abilities it could
not refresh, must not copy the authority's key material, and must not leave a
half-written file for the reader to find.

Hermetic: a real git repository in `tmp_path`, because the authority is read
from a git object rather than from a working tree, and a fixture that writes a
plain file would test a different function than the one that ships.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

import ability_tree_reference as reference

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


projection = _load("serveros_ability_projection", "ability_projection.py")

NOW = datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc)

# The canonical id first, then the historical one — which is how the fleet
# registry models this device: `device:server-beijing` with `linux-server` kept
# in `legacy_ids`.
DEVICE_IDS = ("server-beijing", "linux-server")


def manifest(*abilities) -> dict:
    return {"schema_version": 3, "abilities": list(abilities)}


def ability(name, *, device_id="linux-server", version="1.0.0", agents=("claude-code",),
            revisions=1):
    return {
        "type": "skill",
        "id": f"ProfessorZhi/{name}",
        "name": name,
        "author": "ProfessorZhi",
        "path": f"skills/{name}",
        "revisions": [
            {"fingerprint": f"sha256:{i:064d}", "version": version,
             "installations": [{"device_id": device_id, "os": "linux", "agent": a}
                               for a in agents]}
            for i in range(revisions)
        ],
    }


@pytest.fixture
def authority(tmp_path):
    """A real single-commit git repository holding a manifest."""
    repo = tmp_path / "ability-management"
    repo.mkdir()

    def write(document, name="abilities-manifest.yaml"):
        (repo / name).write_text(yaml.safe_dump(document, sort_keys=False))
        subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True,
                       stdout=subprocess.DEVNULL)
        subprocess.run(
            ["git", "-C", str(repo), "-c", "user.email=t@example.invalid",
             "-c", "user.name=t", "commit", "-m", "manifest"],
            check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                              check=True, capture_output=True).stdout.decode().strip()

    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    # `SimpleNamespace`, not a generated class: a function stored as a CLASS
    # attribute becomes a method, so `authority.write(document)` would silently
    # pass the namespace as the first argument.
    return SimpleNamespace(repo=repo, write=write)


def read(path: Path) -> dict:
    return json.loads(path.read_text())


# ---------------------------------------------------------------------------
# §28 · the projection exists, and says what it is
# ---------------------------------------------------------------------------


def test_a_fresh_projection_carries_the_three_fields_the_section_requires(
        authority, tmp_path):
    """§28 names `generated_at`, `source_revision` and `registry_status`
    explicitly. All three are asserted by value, not by presence: a
    `source_revision` of `None` satisfies "the key exists" while failing
    everything the key is for."""
    sha = authority.write(manifest(ability("session-manager")))
    document = projection.refresh(authority.repo, sha, DEVICE_IDS,
                                  tmp_path / "state", now=NOW)

    assert document["registry_status"] == "FRESH"
    assert document["generated_at"] == "2026-09-12T09:00:00+00:00"
    assert document["source_revision"] == sha
    assert document["authority"] == "ability-management"
    assert document["this_is"] == "a local observed projection, not the authority"
    assert [a["name"] for a in document["abilities"]] == ["session-manager"]

    # And it reached the disk, at the path the reader actually opens.
    on_disk = read(tmp_path / "state" / "abilities" / "effective.json")
    assert on_disk == document


def test_the_projection_records_only_abilities_installed_on_this_device(authority, tmp_path):
    authority.write(manifest(
        ability("here"),
        ability("elsewhere", device_id="windows-corporate"),
    ))
    document = projection.refresh(authority.repo, "HEAD", DEVICE_IDS, tmp_path / "state",
                                  now=NOW)
    assert [a["name"] for a in document["abilities"]] == ["here"]


def test_the_historical_device_id_still_resolves(authority, tmp_path):
    """The alias is the point, not a detail.

    `installations[].device_id` in the authority currently says `linux-server`,
    which is this device's HISTORICAL id — `fleet/registry.yaml` keeps it in
    `legacy_ids` so earlier installation evidence keeps resolving after the
    rename. A projection that matched only the canonical id would return an
    empty abilities list with no error whatsoever, which is indistinguishable
    from "this node has no abilities installed" — the failure this project has
    hit repeatedly, where a check returns the empty set instead of failing.
    """
    authority.write(manifest(ability("session-manager", device_id="linux-server")))
    document = projection.refresh(authority.repo, "HEAD", DEVICE_IDS, tmp_path / "state",
                                  now=NOW)
    assert [a["name"] for a in document["abilities"]] == ["session-manager"]

    # The negative control: canonical-only matching finds nothing here. This is
    # what the check would report if the alias were dropped, and it is why the
    # assertion above is worth having.
    canonical_only = projection.installed_abilities(
        manifest(ability("session-manager", device_id="linux-server")),
        ("server-beijing",))
    assert canonical_only == []


def test_the_winning_revision_is_the_last_one_and_the_count_is_carried(authority, tmp_path):
    """The manifest appends a revision when it recomputes one, so the last
    match is the newest. `revisions_installed` keeps a single-revision device
    and a thirty-four-revision device from looking identical."""
    authority.write(manifest(ability("session-manager", version="3.5.8", revisions=34)))
    document = projection.refresh(authority.repo, "HEAD", DEVICE_IDS, tmp_path / "state",
                                  now=NOW)
    entry = document["abilities"][0]
    assert entry["version"] == "3.5.8"
    assert entry["revisions_installed"] == 34


def test_no_key_material_reaches_the_projection(authority, tmp_path):
    """§30, asserted on the bytes rather than on the fields that were copied.

    The manifest carries `authors[].auth.public_key` and a fingerprint next to
    it. A projection is an ability inventory; a key is not an ability. Checking
    the serialized document rather than the dict means a future field copied
    wholesale cannot slip through.
    """
    document = manifest(ability("session-manager"))
    document["authors"] = [{
        "id": "ProfessorZhi",
        "auth": {"scheme": "ed25519", "public_key": "ed25519:AAAAsecretmaterial",
                 "fingerprint": "SHA256:deadbeef", "status": "verified"},
    }]
    sha = authority.write(document)
    projection.refresh(authority.repo, sha, DEVICE_IDS, tmp_path / "state", now=NOW)

    raw = (tmp_path / "state" / "abilities" / "effective.json").read_text()
    assert "secretmaterial" not in raw
    assert "deadbeef" not in raw
    assert "public_key" not in raw


# ---------------------------------------------------------------------------
# §28 · an unreadable authority degrades the answer, never the data
# ---------------------------------------------------------------------------


def test_an_unreachable_authority_is_stale_and_does_not_erase_what_was_known(
        authority, tmp_path):
    """The bootstrap path §28 is about.

    The node must keep working while ability-management is unreachable. The
    wrong implementation writes an empty abilities list with a STALE label,
    which is worse than not refreshing: it replaces what the node knew with
    nothing, and the reader — which is careful not to equate "empty" with
    "unknown" — has no way to tell that it happened.
    """
    sha = authority.write(manifest(ability("session-manager")))
    state = tmp_path / "state"
    good = projection.refresh(authority.repo, sha, DEVICE_IDS, state, now=NOW)

    later = NOW + timedelta(days=3)
    document = projection.refresh(authority.repo, "does-not-exist", DEVICE_IDS, state,
                                  now=later)

    assert document["registry_status"] == "STALE"
    assert document["abilities"] == good["abilities"], "abilities were erased"
    # `generated_at` is when the authority was last SUCCESSFULLY read. The
    # reader ages the projection from this field, so restamping it here would
    # report three-day-old data as three seconds old.
    assert document["generated_at"] == good["generated_at"]
    assert document["source_revision"] == sha
    assert document["last_attempt_at"] == "2026-09-15T09:00:00+00:00"
    assert "does-not-exist" in document["unreachable_reason"]

    # And the reader agrees: this is stale, and it still knows what it knew.
    on_disk = read(state / "abilities" / "effective.json")
    assert on_disk["registry_status"] == "STALE"
    assert [a["name"] for a in on_disk["abilities"]] == ["session-manager"]


def test_the_readers_age_check_sees_the_preserved_timestamp(tmp_path):
    """The two halves have to agree on the clock, so this asserts the join.

    `_capability_view` downgrades a projection that claims FRESH but is older
    than its limit. Because the stale path preserves `generated_at`, a
    projection written months after a successful read still ages correctly —
    which is the property that makes preserving the timestamp load-bearing
    rather than cosmetic.
    """
    daemon_mod = _load("serveros_runtime_daemon", "daemon.py")
    state = tmp_path / "state"
    (state / "abilities").mkdir(parents=True)
    written = projection.stale(
        {"generated_at": "2026-01-01T00:00:00+00:00", "source_revision": "abc",
         "abilities": [{"name": "session-manager"}]},
        DEVICE_IDS, reason="unreachable", now=NOW)
    (state / "abilities" / "effective.json").write_text(json.dumps(written))

    age = daemon_mod._age_seconds(written["generated_at"],
                                  datetime(2026, 9, 12, 9, 0, tzinfo=timezone.utc).timestamp())
    assert age > daemon_mod.MAX_PROJECTION_AGE_SECONDS


def test_a_first_run_against_an_unreachable_authority_still_produces_a_readable_file(
        tmp_path):
    """No previous projection exists and the authority is down. The file must
    still be written and still be honest — an empty list with `generated_at:
    null`, which the reader reports as stale with an unknown age rather than as
    a fresh empty inventory."""
    document = projection.refresh(tmp_path / "absent-repo", "HEAD", DEVICE_IDS,
                                  tmp_path / "state", now=NOW)
    assert document["registry_status"] == "STALE"
    assert document["abilities"] == []
    assert document["generated_at"] is None
    assert document["source_revision"] is None
    assert read(tmp_path / "state" / "abilities" / "effective.json")["abilities"] == []


def test_a_corrupt_previous_projection_is_not_allowed_to_break_the_refresh(authority, tmp_path):
    """The previous file cannot be read, so there is nothing to carry. The
    refresh must still write a valid projection rather than raising on a file
    it was only trying to preserve."""
    state = tmp_path / "state"
    (state / "abilities").mkdir(parents=True)
    (state / "abilities" / "effective.json").write_text("{ this is not json")
    document = projection.refresh(tmp_path / "absent-repo", "HEAD", DEVICE_IDS, state,
                                  now=NOW)
    assert document["registry_status"] == "STALE"
    assert document["abilities"] == []
    assert read(state / "abilities" / "effective.json")["abilities"] == []


# ---------------------------------------------------------------------------
# the write itself
# ---------------------------------------------------------------------------


def test_a_failed_write_leaves_the_previous_projection_intact(authority, tmp_path,
                                                             monkeypatch):
    """A projection is read by a different process than the one writing it, so
    a torn write is not hypothetical: truncated JSON is exactly the shape
    `_capability_view` reports as UNREADABLE. That would be a real outage
    fabricated by a writer that was working fine."""
    state = tmp_path / "state"
    sha = authority.write(manifest(ability("session-manager")))
    projection.refresh(authority.repo, sha, DEVICE_IDS, state, now=NOW)
    before = read(state / "abilities" / "effective.json")

    def explode(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(projection.os, "replace", explode)
    with pytest.raises(OSError):
        projection.write_atomic(state / "abilities" / "effective.json", {"abilities": []})

    assert read(state / "abilities" / "effective.json") == before
    residue = [p.name for p in (state / "abilities").iterdir() if p.name != "effective.json"]
    assert residue == [], f"temporary files left behind: {residue}"


def test_refreshing_twice_from_the_same_revision_is_byte_identical(authority, tmp_path):
    """Determinism, minus the clock. The second write must not accumulate
    fields from the first — a projection that grows a key per refresh is one
    that eventually disagrees with its own reader."""
    state = tmp_path / "state"
    sha = authority.write(manifest(ability("session-manager"), ability("here")))
    projection.refresh(authority.repo, sha, DEVICE_IDS, state, now=NOW)
    first = (state / "abilities" / "effective.json").read_bytes()
    projection.refresh(authority.repo, sha, DEVICE_IDS, state, now=NOW)
    assert (state / "abilities" / "effective.json").read_bytes() == first


def test_a_manifest_that_is_not_a_mapping_is_unreachable_rather_than_empty(authority,
                                                                          tmp_path):
    """`abilities: []` and "the file is a list" must not both produce an empty
    projection, because only one of them means the authority answered."""
    (authority.repo / "abilities-manifest.yaml").write_text("- just\n- a list\n")
    subprocess.run(["git", "-C", str(authority.repo), "add", "-A"], check=True,
                   stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-C", str(authority.repo), "-c", "user.email=t@example.invalid",
                    "-c", "user.name=t", "commit", "-m", "bad"],
                   check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    document = projection.refresh(authority.repo, "HEAD", DEVICE_IDS,
                                  tmp_path / "state", now=NOW)
    assert document["registry_status"] == "STALE"
    assert "not a mapping" in document["unreachable_reason"]


# The join between this writer and the daemon's reader — that the two agree on
# the directory, the filename and the field names — is asserted in
# `test_daemon.py`, where the hermetic daemon fixture already lives. Building a
# second one here would mean either duplicating that fixture or constructing a
# Daemon against the live host, and a test that reads this machine is not a test.


# ---------------------------------------------------------------------------
# §4 · the discovery tree, joined against the authority's own renderer
# ---------------------------------------------------------------------------
#
# This section is a JOIN, and that is the whole point of it. §4 asks for a tree
# generated from the real catalogue, and this repository now has two spellings
# of that derivation: `domain_tree()` here, and ability-management's
# `scripts/render_ability_map.py`, whose output is the committed
# `ABILITY-MAP.md`. Two implementations that never meet agree with nothing.
#
# So the expectations below are not written down from the spec. They are what
# the other implementation actually emitted, captured in
# `ability_tree_reference.py` along with the rows that produced them. If the two
# ever disagree, the failure is the join, which is a fact about this repository,
# rather than a matter of taste, which is not.


def test_the_local_tree_matches_the_nodes_the_authority_rendered():
    """The node set is a claim that something is under each node (§4)."""
    tree = projection.domain_tree(reference.ROWS)
    assert tree["nodes"] == sorted(reference.EXPECTED_NODES)


def test_every_classified_ability_lands_where_the_authority_placed_it():
    """Placement, ability by ability, against the rendered map."""
    tree = projection.domain_tree(reference.ROWS)
    assert tree["placements"] == reference.EXPECTED_PLACEMENTS


def test_cross_references_are_reported_beside_the_tree_and_not_inside_it():
    """`taxonomy_refs` are alternate reader paths, so they belong in their own
    field. The authority's renderer prints them separately for the same reason,
    and this compares against its list rather than against this function's.

    A cross-reference may or may not also be a node, and the difference is the
    point: `serveros.operations` is a node because ten abilities live in it,
    while `serveros.engineering.quality` is in `test-management`'s refs and is
    NOT a node, because no ability's primary path runs through it. Adding it
    would be materializing a node for a reader path, which is the same error as
    materializing one for an empty domain.
    """
    tree = projection.domain_tree(reference.ROWS)
    assert tree["cross_refs"] == reference.EXPECTED_CROSS_REFS
    # Only a placed ability has a home for a second path to be an alternative
    # to, so the two key sets nest and the cross-refs are a strict subset.
    assert set(tree["cross_refs"]) < set(tree["placements"])
    refs = {ref for refs in tree["cross_refs"].values() for ref in refs}
    assert "serveros.engineering.quality" in refs
    assert "serveros.engineering.quality" not in tree["nodes"]


def test_an_unclassified_ability_is_reported_as_unplaced_not_defaulted():
    """§4 generates the tree from the real catalogue, and most of this
    catalogue is unclassified. A default node would hide that, and the count is
    the part of the answer that says the tree is incomplete — so it is asserted
    rather than left to be inferred from a shortfall."""
    tree = projection.domain_tree(reference.ROWS)
    assert len(tree["unplaced"]) == reference.TOTAL - reference.CLASSIFIED
    assert tree["unplaced"] == sorted(tree["unplaced"])
    for name in ("feature-development", "design-and-contract", "archguard"):
        assert name in tree["unplaced"]
        assert name not in tree["placements"]


# --- positive controls -----------------------------------------------------
#
# "Every new gate has to be shown able to go red, or it is not a gate." The
# four assertions below are that demonstration, one failure mode each.


def test_the_fixture_is_not_empty_so_the_join_above_is_not_vacuous():
    """An emptied fixture would satisfy `domain_tree([])` trivially. The counts
    are asserted first so that truncation fails here rather than turning the
    comparisons above green."""
    assert len(reference.ROWS) == reference.TOTAL
    assert len(reference.EXPECTED_PLACEMENTS) == reference.CLASSIFIED
    assert reference.EXPECTED_NODES and reference.EXPECTED_CROSS_REFS
    tree = projection.domain_tree(reference.ROWS)
    assert tree["nodes"] and tree["placements"]
    # ...and the empty catalogue is genuinely empty, rather than erroring or
    # conjuring a skeleton. Both halves, or the positive control proves nothing.
    assert projection.domain_tree([])["nodes"] == []
    assert projection.domain_tree(None)["placements"] == {}


def test_placing_by_cross_reference_would_not_agree_with_the_authority():
    """The near miss this function's docstring names, executed.

    `taxonomy_refs` reads like a list of places an ability belongs, so placing
    by every reference is the implementation a careful reader writes — and it
    disagrees with the rendered map on both axes at once: it invents a node
    (`serveros.engineering.quality`, which no ability's primary path runs
    through) and it places `test-management` twice. Running it here is what
    makes the tests above evidence that the shipped algorithm is the right one,
    rather than evidence that a fixture agrees with itself.
    """
    naive_nodes, naive_pairs = set(), []
    for row in reference.ROWS:
        for node in [row["primary"], *row["refs"]] if row["primary"] else []:
            naive_pairs.append((row["name"], node))
            segments = node.split(".")
            for depth in range(1, len(segments) + 1):
                naive_nodes.add(".".join(segments[:depth]))

    assert naive_nodes != set(reference.EXPECTED_NODES)
    assert "serveros.engineering.quality" in naive_nodes
    assert "serveros.engineering.quality" not in reference.EXPECTED_NODES

    real = projection.domain_tree(reference.ROWS)["placements"]
    assert len(naive_pairs) > len(real)
    # `test-management` carries two refs beside its primary, so the naive
    # version places it three times; and because eleven abilities' `refs` repeat
    # their own primary, it double-counts those too. Neither is a hypothetical:
    # both are what the captured data actually contains.
    names = [name for name, _ in naive_pairs]
    assert names.count("test-management") == 3
    assert names.count("ability-management") == 2
    assert names.count("test-management") > 1


def test_the_tree_is_derived_from_classification_and_never_from_a_path():
    """§4's second sentence and §19's first, as a mutation test. `path` is a
    source address; the tree is a logical projection. If any of this read
    `path`, scrambling it would move abilities — so scrambling it must change
    nothing. A test that only asserted the output would pass just as happily
    against an implementation that sorted by directory."""
    scrambled = [dict(row, path=f"elsewhere/{i}/{row['name']}")
                 for i, row in enumerate(reference.ROWS)]
    assert projection.domain_tree(scrambled) == projection.domain_tree(reference.ROWS)


def test_the_row_shape_the_tree_consumes_carries_no_field_about_a_person():
    """`tree_rows()` is the boundary §30 needs: the projection already refuses
    to copy `authors`, and this is the second surface that would carry it out of
    the building. Asserting the key set is stronger than asserting the absence
    of one name, because a field added upstream reaches the tree by being
    projected, not by being read."""
    rows = projection.tree_rows([{
        "name": "shared-memory", "id": "ProfessorZhi/shared-memory", "type": "skill",
        "path": "skills/shared-memory", "version": "1.0.0", "agents": ["claude-code"],
        "authors": [{"name": "a person", "email": "p@example.invalid"}],
        "domain": {"primary": "serveros.memory"}, "taxonomy_refs": ["serveros.memory"],
    }])
    assert set(rows[0]) == {"id", "name", "path", "primary", "refs"}
    assert rows[0]["primary"] == "serveros.memory"
    # A row with no `domain` at all is unplaced, not an error: the manifest's
    # `domain` key is absent on 38 of 55 entries, and `None`/`{}`/`[]` all
    # arrive here in the wild.
    for empty in ({}, None, [], {"primary": None}, {"primary": ""}):
        row = projection.tree_rows([{"name": "x", "id": "x", "domain": empty}])[0]
        assert row["primary"] is None
        assert row["refs"] == []
