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

# The canonical id first, then the historical one — the shape the fleet registry
# gives ANY renamed device, here `device:server-beijing` with `linux-server` kept
# in its `legacy_ids`. It is a fixture, not this node's ids: the node this
# runtime is installed on has an empty `legacy_ids`, and passing Beijing's ids
# as if they were its own is what produced a 30-ability reading from a machine
# that is not this one.
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

    The authority records an installation under the device id that was in force
    when it happened, so a renamed device's evidence is split across its
    canonical id and the historical ones `fleet/registry.yaml` keeps in
    `legacy_ids`. A projection that matched only the canonical id would return
    an empty abilities list with no error whatsoever, which is indistinguishable
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
    # A refresh writes two artifacts, and BOTH must survive a failed write —
    # the catalogue is not a scratch file. Naming them rather than meaning
    # "everything except effective.json" keeps this a test about temp-file
    # residue when a third artifact is added later.
    assert (state / "abilities" / projection.CATALOGUE_NAME).exists()
    residue = [p.name for p in (state / "abilities").iterdir()
               if p.name not in {"effective.json", projection.CATALOGUE_NAME}]
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
# §35 · the catalogue is a second artifact, and it names no device
# ---------------------------------------------------------------------------
# The projection answers "what is installed HERE"; the catalogue answers "what
# the authority lists at all". §35 keeps the first local on purpose, so the
# wider answer is a separate file rather than a widened one. What these tests
# hold is the separation itself: a catalogue that quietly grew an installation
# field would make the two indistinguishable, and a reader could no longer tell
# "absent from this device" from "absent from the world" — which is the single
# distinction the pair exists to draw.


def test_a_fresh_catalogue_carries_the_whole_manifest_not_just_this_device(
        authority, tmp_path):
    """The fact the projection cannot state, and the reason there are two files.

    Three abilities, installed on three machines. The projection records the one
    installed here; the catalogue records all three, because "which device has
    it" is not a question it answers.
    """
    sha = authority.write(manifest(
        ability("installed-here"),
        ability("installed-elsewhere", device_id="windows-corporate"),
        ability("installed-nowhere-at-all", device_id="some-third-machine"),
    ))
    projection.refresh(authority.repo, sha, DEVICE_IDS, tmp_path / "state", now=NOW)

    local = read(tmp_path / "state" / "abilities" / "effective.json")
    catalogue = read(tmp_path / "state" / "abilities" / "catalogue.json")

    assert [a["name"] for a in local["abilities"]] == ["installed-here"]
    assert [e["name"] for e in catalogue["entries"]] == [
        "installed-elsewhere", "installed-here", "installed-nowhere-at-all"]

    # One read, one revision: the two can never be two readings of two
    # revisions, and a reader that sees them drift apart is looking at a
    # half-completed write rather than at a difference of opinion.
    assert catalogue["source_revision"] == local["source_revision"] == sha
    assert catalogue["generated_at"] == local["generated_at"]
    assert catalogue["registry_status"] == "FRESH"
    assert catalogue["authority"] == "ability-management"
    assert catalogue["schema"] == "serveros/ability-catalogue.v1"

    # No device in it, at any depth. Asserted over the serialized bytes so a
    # field that arrived by a path other than the copy loop is caught too.
    raw = json.dumps(catalogue)
    for leak in ("windows-corporate", "some-third-machine", "device_id",
                 "installations", "revisions", "fingerprint", "version"):
        assert leak not in raw, f"the catalogue carries installation data: {leak}"


def test_a_catalogue_entry_carries_only_classification_metadata(authority, tmp_path):
    """`ABILITY_FIELDS` as a set equality, not a subset check.

    A field added to the copy would still satisfy "every field is allowed" if
    the assertion were one-directional, and the allow-list is the whole reason
    the copy is written field by field.
    """
    sha = authority.write(manifest(ability("session-manager")))
    projection.refresh(authority.repo, sha, DEVICE_IDS, tmp_path / "state", now=NOW)

    entries = read(tmp_path / "state" / "abilities" / "catalogue.json")["entries"]
    assert len(entries) == 1
    assert set(entries[0]) == set(projection.ABILITY_FIELDS)


def test_no_key_material_reaches_the_catalogue(authority, tmp_path):
    """§30, over the bytes. The catalogue is the WIDER file, so it is the one
    most likely to be handed to something that will render it."""
    document = manifest(ability("session-manager"))
    document["authors"] = [{
        "id": "ProfessorZhi",
        "auth": {"scheme": "ed25519", "public_key": "ed25519:AAAAsecretmaterial",
                 "fingerprint": "SHA256:deadbeef", "status": "verified"},
    }]
    sha = authority.write(document)
    projection.refresh(authority.repo, sha, DEVICE_IDS, tmp_path / "state", now=NOW)

    raw = (tmp_path / "state" / "abilities" / "catalogue.json").read_text()
    assert "secretmaterial" not in raw
    assert "deadbeef" not in raw
    assert "public_key" not in raw
    # The per-ability `author` the manifest also writes. Quoted, because
    # `"authority"` is a legitimate key of this document and a bare substring
    # test would flag it.
    assert '"author"' not in raw


def test_an_unreachable_authority_does_not_empty_the_catalogue(authority, tmp_path):
    """`a failure never erases`, one file over — and the stakes are higher here.

    An empty projection says "this device has nothing", which is at least
    scoped to a device. An empty catalogue is a positive claim that the
    authority lists no abilities at all, manufactured by a failed read.
    """
    state = tmp_path / "state"
    sha = authority.write(manifest(ability("session-manager"), ability("shared-memory")))
    projection.refresh(authority.repo, sha, DEVICE_IDS, state, now=NOW)
    before = read(state / "abilities" / "catalogue.json")

    document = projection.refresh(authority.repo, "does-not-exist", DEVICE_IDS, state,
                                  now=NOW + timedelta(hours=3))
    after = read(state / "abilities" / "catalogue.json")

    assert after["registry_status"] == "STALE"
    assert after["entries"] == before["entries"]
    assert [e["name"] for e in after["entries"]] == ["session-manager", "shared-memory"]
    # `generated_at` is when the authority was last READ, not when this file was
    # written; advancing it would make a stale catalogue look current.
    assert after["generated_at"] == before["generated_at"]
    assert after["last_attempt_at"] != before["generated_at"]
    # And the projection degraded in the SAME call, so the pair never disagrees
    # about which read produced them.
    assert document["registry_status"] == "STALE"


def test_a_first_run_against_an_unreachable_authority_still_writes_a_readable_catalogue(
        tmp_path):
    """No previous catalogue to carry: the file must still be a document the
    reader can open, saying `entries: []` with `registry_status: STALE` — not
    absent, which the reader would report as NOT_MATERIALIZED, or unparseable,
    which it would report as UNREADABLE."""
    state = tmp_path / "state"
    projection.refresh(tmp_path / "absent-repo", "HEAD", DEVICE_IDS, state, now=NOW)
    catalogue = read(state / "abilities" / "catalogue.json")

    assert catalogue["registry_status"] == "STALE"
    assert catalogue["entries"] == []
    assert catalogue["generated_at"] is None
    assert catalogue["unreachable_reason"]


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
    assert len(tree["unplaced"]) == reference.DISTINCT_ABILITIES - reference.CLASSIFIED
    assert tree["unplaced"] == sorted(tree["unplaced"])
    # ...and once each. Five abilities in this fixture ship in two forms and
    # neither form is classified, so a tree that named records would report 38
    # unplaced where the catalogue has 33 — a record count wearing an ability's
    # name, which is the number a reader would quote.
    assert len(tree["unplaced"]) == len(set(tree["unplaced"]))
    for name in ("feature-development", "design-and-contract", "archguard"):
        assert name in tree["unplaced"]
        assert name not in tree["placements"]


def test_an_ability_that_ships_twice_is_placed_once_and_not_also_unplaced():
    """The catalogue describes five abilities in two forms, and only one form
    carries the classification. Read as abilities, the rows put the same one in
    both answers — `mcps/meta-cc` places it and `plugins/meta-cc` reports it as
    having no `domain.primary` — so the node says where the thing is and that
    nobody has said where it is, in one reply.

    The fixture above cannot catch this: there both forms are unclassified, so
    the records collapse to the right answer by accident and only the count
    moves. This case is the live shape.
    """
    rows = [
        {"name": "meta-cc", "id": "ProfessorZhi/meta-cc",
         "path": "mcps/meta-cc", "primary": "serveros.memory",
         "refs": ["serveros.engineering"]},
        {"name": "meta-cc", "id": "ProfessorZhi/meta-cc",
         "path": "plugins/meta-cc", "primary": None, "refs": []},
    ]
    tree = projection.domain_tree(rows)
    assert tree["placements"] == {"meta-cc": "serveros.memory"}
    assert tree["unplaced"] == []
    assert tree["nodes"] == ["serveros", "serveros.memory"]
    # The reader paths are the ability's, not the classified form's: either
    # form may be the one that declares them.
    assert tree["cross_refs"] == {"meta-cc": ["serveros.engineering"]}


def test_two_homes_for_one_ability_are_reported_rather_than_resolved():
    """The refusal at the far end of the same rule.

    If two forms of one ability name different homes, any single placement this
    function emitted would be invented. It reports the conflict instead, and
    loudly — a view that quietly picked one would put an ability somewhere the
    catalogue never said.
    """
    rows = [
        {"name": "meta-cc", "id": "ProfessorZhi/meta-cc",
         "path": "mcps/meta-cc", "primary": "serveros.memory", "refs": []},
        {"name": "meta-cc", "id": "ProfessorZhi/meta-cc",
         "path": "plugins/meta-cc", "primary": "serveros.agent", "refs": []},
    ]
    with pytest.raises(ValueError) as caught:
        projection.domain_tree(rows)
    assert "different homes" in str(caught.value)
    assert "serveros.agent" in str(caught.value)


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


# ---------------------------------------------------------------------------
# §28 · a refresh that would speak for nobody is refused, not written
# ---------------------------------------------------------------------------
#
# The failure these cover was destructive and it reported success. On
# 2026-10-05 `serverosctl ability refresh --device-id
# device:tokyo-shared-dev-01` matched nothing, wrote
# `{"abilities": [], "registry_status": "FRESH"}` over a projection holding 18
# abilities, and exited 0. Both halves of that are here: the id was spelled
# with a prefix the manifest does not use, AND a zero match was written as an
# answer rather than refused.
#
# The tests below are the two guards, and each has a control beside it — the
# spelling test carries the raw comparison that produced the wipe, and the
# refusal tests carry `allow_empty`, which is the old behaviour made explicit.


def test_a_device_id_that_names_nothing_is_refused_instead_of_written(authority, tmp_path):
    """"Found nothing" is not an answer about a device; it is a bad question.

    A projection that says FRESH/[] is a claim that the authority was read and
    this node has no abilities. A zero match cannot support that claim — it is
    equally consistent with the authority having been read and the id having
    named some other machine, and the second is what actually happened.
    """
    sha = authority.write(manifest(ability("session-manager", device_id="linux-server")))
    state = tmp_path / "state"

    with pytest.raises(projection.NoInstallationMatched) as raised:
        projection.refresh(authority.repo, sha, ("tokyo-shared-dev-02",), state, now=NOW)

    assert not (state / "abilities" / "effective.json").exists()
    assert "tokyo-shared-dev-02" in str(raised.value)


def test_a_refused_refresh_leaves_every_existing_artifact_byte_identical(authority, tmp_path):
    """Both files, not just the projection.

    The catalogue is written from the same read and shares `generated_at` and
    `source_revision` with the projection, so that a half-completed write shows
    up as the two drifting apart. Writing the catalogue alone would be exactly
    that drift, produced on purpose, in the file whose comment says it exists to
    make drift visible.
    """
    sha = authority.write(manifest(ability("session-manager", device_id="linux-server")))
    state = tmp_path / "state"
    projection.refresh(authority.repo, sha, DEVICE_IDS, state, now=NOW)
    before = {p.name: p.read_bytes() for p in sorted((state / "abilities").iterdir())}
    assert sorted(before) == ["catalogue.json", "effective.json"]

    with pytest.raises(projection.NoInstallationMatched):
        projection.refresh(authority.repo, sha, ("tokyo-shared-dev-02",), state,
                           now=NOW + timedelta(days=1))

    after = {p.name: p.read_bytes() for p in sorted((state / "abilities").iterdir())}
    assert after == before


def test_allow_empty_writes_the_empty_projection_it_was_asked_for(authority, tmp_path):
    """The control for the refusal: the destructive shape is now opt-in.

    A brand-new node genuinely has no installations yet, and a refusal with no
    way past it would make bootstrap impossible. So the old behaviour survives
    as a flag, which is the difference between a floor and a wall.
    """
    sha = authority.write(manifest(ability("session-manager", device_id="linux-server")))
    state = tmp_path / "state"

    document = projection.refresh(authority.repo, sha, ("tokyo-shared-dev-02",), state,
                                  allow_empty=True, now=NOW)

    assert document["registry_status"] == "FRESH"
    assert document["abilities"] == []
    assert read(state / "abilities" / "effective.json") == document


def test_the_device_ref_spelling_names_the_same_device_as_the_bare_id(authority, tmp_path):
    """`device:x` and `x` are one id in two vocabularies, and this node uses both.

    ServerOS names a device to its own surfaces as `device:<id>` — that is the
    spelling in the node's own identity line and in every `device` argument —
    while the authority records an installation under the bare id. A command
    that accepts only one of them is a command whose most natural input is the
    one that finds nothing.
    """
    authority.write(manifest(ability("session-manager", device_id="tokyo-shared-dev-01")))
    state = tmp_path / "state"

    document = projection.refresh(authority.repo, "HEAD", ("device:tokyo-shared-dev-01",),
                                  state, now=NOW)

    assert [a["name"] for a in document["abilities"]] == ["session-manager"]
    # Recorded in the vocabulary the authority speaks, not the one it was typed
    # in: what is written down is what was matched.
    assert document["device_ids_matched"] == ["tokyo-shared-dev-01"]
    # Both spellings of one id at once is one id, not two rows.
    both = projection.refresh(authority.repo, "HEAD",
                              ("device:tokyo-shared-dev-01", "tokyo-shared-dev-01"),
                              tmp_path / "state2", now=NOW)
    assert both["device_ids_matched"] == ["tokyo-shared-dev-01"]
    assert [a["name"] for a in both["abilities"]] == ["session-manager"]

    # The negative control, and it is the live failure: matching the prefixed
    # spelling without normalizing finds nothing at all. This is what the
    # command did on 2026-10-05, and why the projection came back empty.
    raw = projection.read_manifest(authority.repo, "HEAD")[0]
    assert projection.installed_abilities(raw, ("device:tokyo-shared-dev-01",)) == []


def test_the_refusal_names_the_ids_the_manifest_actually_uses(authority, tmp_path):
    """A refusal an operator cannot act on is a wall, not an answer.

    The whole cost of this bug was that the operator had to guess. So the
    refusal carries the ids as typed and the ids the manifest names, which is
    the difference between "check your spelling" and a typo the reader can see.
    """
    authority.write(manifest(
        ability("here", device_id="tokyo-shared-dev-01"),
        ability("elsewhere", device_id="dell-work"),
    ))

    with pytest.raises(projection.NoInstallationMatched) as raised:
        projection.refresh(authority.repo, "HEAD", ("tokyo-shared-dev-0l",),
                           tmp_path / "state", now=NOW)

    exc = raised.value
    assert exc.asked == ("tokyo-shared-dev-0l",)
    assert exc.manifest_device_ids == ("dell-work", "tokyo-shared-dev-01")
    assert exc.records == 2
    assert "tokyo-shared-dev-0l" in str(exc)
    assert "dell-work" in str(exc) and "tokyo-shared-dev-01" in str(exc)
