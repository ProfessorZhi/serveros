#!/usr/bin/env python3
"""§7 — a name in a catalogue is not an implementation.

The defect this file pins down, as measured:

    serverosctl taxonomy show serveros.corporate.feishu
      state   OCCUPIED
      placed  feishu-report, feishu-space-management

OCCUPIED was reached from one input — whether any catalogue row's
`domain.primary` names the taxon — and the module never asked whether the
ability exists. Two members that are not alike got one word:

    feishu-report             compatibility: linux/claude-code, linux/codex
                              installations: linux-server x 2 agents
    feishu-space-management   compatibility: []   installations: []

The brief for this round described the second as having "no implementation".
Read against the revision the command actually reads, that is not true — it has
four files under `skills/feishu-space-management/`. What is true is that
nothing claims it can run and no device reports it installed, and that is what
these tests hold the module to.

The rule under test is §7's: **no implementation, no READY, no OCCUPIED.** It
is checked at both levels, because the defect was at both — a rung that could be
climbed without an artifact, and a taxon that could be occupied without one.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = REPO / "abilities" / "taxonomy.v2.yaml"
ABILITY_REPO = REPO.parent / "ability-management"


def _load(name: str, path: Path):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


taxonomy = _load("serveros_abilities_taxonomy", REPO / "abilities" / "taxonomy.py")


def document(taxons, edges) -> dict:
    return {"schema": taxonomy.SCHEMA, "revision": 1, "taxons": taxons, "edges": edges}


def seed(taxon_id, **extra) -> dict:
    return {"id": taxon_id, "origin": "seed", "label": taxon_id.title(), **extra}


def declared(taxon_id, **extra) -> dict:
    return {"id": taxon_id, "origin": "declared", **extra}


def contains(parent, child) -> dict:
    return {"from": parent, "to": child, "kind": "contains"}


def row(name, primary, *, implementation=None, compatibility=None, installations=None):
    """A catalogue row shaped the way `_load_rows` hands them to `membership`.

    `implementation` is omitted rather than defaulted when the caller does not
    name it, because "the caller did not say" is one of the conditions under
    test and a default would hide it.
    """
    entry = {"name": name, "domain": {"primary": primary}}
    if implementation is not None:
        entry["implementation"] = implementation
    revisions = []
    if compatibility is not None or installations is not None:
        revisions.append({"compatibility": compatibility or [],
                          "installations": installations or []})
    entry["revisions"] = revisions
    return entry


# ---------------------------------------------------------------------------
# The ladder, one rung at a time
# ---------------------------------------------------------------------------

def test_a_row_the_caller_said_nothing_about_is_only_declared():
    """The module does not assume a path exists because a catalogue names one.

    `membership` is handed rows by a caller. When the caller has not read the
    authority — a synthetic fixture, or a code path that skipped the tree — the
    honest answer is the bottom rung, not a guess upward.
    """
    assert taxonomy.availability({"name": "x"}) == taxonomy.DECLARED


def test_an_ability_with_no_implementation_can_never_be_ready():
    """§7's rule, stated as the thing that must not happen.

    This row carries everything a READY verdict could be built from — two
    runtimes declare it compatible and it is recorded installed on a device —
    and no implementation. It is DECLARED. Implementation is the GATE, so a
    catalogue row cannot describe an artifact into existence.
    """
    assert taxonomy.availability(row(
        "ghost", "serveros.a", implementation=False,
        compatibility=[{"agent": "claude-code", "os": "linux"}],
        installations=[{"device_id": "linux-server", "agent": "claude-code",
                        "os": "linux"}])) == taxonomy.DECLARED


def test_each_rung_needs_the_one_below_it():
    """DECLARED → IMPLEMENTED → SUPPORTED → READY, climbed one field at a time.

    Asserted as a sequence rather than as four independent cases, because the
    claim is that they are ordered: a change that lets SUPPORTED be reached
    without IMPLEMENTED would still satisfy four separate assertions made in
    isolation.
    """
    rungs = [
        taxonomy.availability({"name": "ladder", "implementation": True}),
        taxonomy.availability({"name": "ladder", "implementation": True,
                               "revisions": [{"compatibility": [{"agent": "claude-code",
                                                                 "os": "linux"}]}]}),
        taxonomy.availability({"name": "ladder", "implementation": True,
                               "revisions": [{"compatibility": [{"agent": "claude-code",
                                                                 "os": "linux"}],
                                              "installations": [{"device_id": "d",
                                                                 "agent": "claude-code",
                                                                 "os": "linux"}]}]}),
    ]
    assert rungs == [taxonomy.IMPLEMENTED, taxonomy.SUPPORTED, taxonomy.READY]


def test_implemented_without_support_is_not_supported():
    """An empty `compatibility` is the ABSENCE of a claim, not a claim of
    universal support — which is the reading that would have made the packaged
    ability look deployable."""
    assert taxonomy.availability({"name": "x", "implementation": True,
                                  "revisions": [{"compatibility": [],
                                                 "installations": []}]}) == taxonomy.IMPLEMENTED


# ---------------------------------------------------------------------------
# The taxon state
# ---------------------------------------------------------------------------

def test_a_taxon_of_names_is_not_occupied():
    """§7's rule at the taxon level, and the exact shape the defect had."""
    # `serveros.quiet`, not `serveros.a.b`: the prefix check makes every
    # intermediate segment a node the declaration must also carry, so a deeper
    # name would report a blocking finding alongside the one under test and
    # `ok` would be false for a reason that has nothing to do with occupancy.
    # The subject is one taxon holding a name and nothing else.
    entries = taxonomy.membership(
        document([seed("serveros"), declared("serveros.quiet")],
                 [contains("serveros", "serveros.quiet")]),
        [row("a-name-in-a-list", "serveros.quiet")])
    assert entries["serveros.quiet"]["state"] == taxonomy.DECLARED_ONLY
    assert entries["serveros.quiet"]["state"] != taxonomy.OCCUPIED


def test_occupancy_survives_one_member_being_unimplemented():
    """A taxon is occupied when SOMETHING under it is, and the member that is
    not says so on its own line rather than dragging the verdict down with it.

    Both halves matter. Demoting the taxon would lose the real ability; keeping
    it occupied without a per-member line would put the two members back under
    one word, which is the defect.
    """
    entries = taxonomy.membership(
        document([seed("serveros"), declared("serveros.corporate.feishu")],
                 [contains("serveros", "serveros.corporate.feishu")]),
        [row("feishu-report", "serveros.corporate.feishu", implementation=True,
             compatibility=[{"agent": "claude-code", "os": "linux"}],
             installations=[{"device_id": "linux-server", "agent": "claude-code",
                             "os": "linux"}]),
         row("packaged-not-in-use", "serveros.corporate.feishu", implementation=True)])

    entry = entries["serveros.corporate.feishu"]
    assert entry["state"] == taxonomy.OCCUPIED
    assert entry["materialised"] == ["feishu-report", "packaged-not-in-use"]
    assert entry["availability"] == {"feishu-report": taxonomy.READY,
                                     "packaged-not-in-use": taxonomy.IMPLEMENTED}


def test_a_taxon_with_nothing_materialised_is_reported_as_a_warning_not_a_block():
    """The taxonomy is still sound, so the command still renders.

    A warning is the right severity: the declaration is internally consistent,
    and calling this blocking would refuse to draw the tree — losing the view
    that tells a reader which families are staffed.
    """
    report = taxonomy.conformance(
        document([seed("serveros"), declared("serveros.quiet")],
                 [contains("serveros", "serveros.quiet")]),
        [row("a-name-in-a-list", "serveros.quiet")])
    assert report["ok"], [f["detail"] for f in report["findings"]]
    assert report["declared_only"] == ["serveros.quiet"]
    assert "DECLARED_NOT_IMPLEMENTED" in {f["code"] for f in report["findings"]}
    assert all(f["severity"] == "warning" for f in report["findings"])


# ---------------------------------------------------------------------------
# Which paths count as an implementation
# ---------------------------------------------------------------------------

def test_a_catalogue_path_names_a_directory_and_ls_tree_lists_files():
    """The bug this fix shipped with for one run.

    `git ls-tree -r --name-only` emits the four FILES under
    `skills/feishu-report/`, never the directory the catalogue names. Written
    as `declared_path in paths`, every ability in the fleet read as
    unimplemented and all thirteen taxons came back DECLARED_ONLY — including
    ones holding skills that plainly exist.
    """
    files = {"skills/feishu-report/ABILITY.md",
             "skills/feishu-report/tests/test_contract.py"}
    assert taxonomy.has_implementation("skills/feishu-report", files) is True


def test_a_prefix_must_end_on_a_separator():
    """`skills/feishu` is not satisfied by `skills/feishu-report/…`.

    Two abilities in this very fleet are named that way, so the naive prefix
    test would have reported one implemented on the evidence of the other.
    """
    files = {"skills/feishu-report/ABILITY.md"}
    assert taxonomy.has_implementation("skills/feishu", files) is False


def test_a_path_with_no_files_under_it_is_not_implemented():
    assert taxonomy.has_implementation("skills/absent", {"skills/other/x"}) is False
    assert taxonomy.has_implementation(None, {"skills/other/x"}) is False


# ---------------------------------------------------------------------------
# The measured case
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not ABILITY_REPO.exists(),
                    reason="the ability-management checkout is not beside this repo")
def test_the_real_catalogue_separates_packaged_from_ready():
    """The pair from the defect, asserted as the distinction §7 asked to be
    sayable — against the real catalogue, not a fixture.

    Read at `origin/main`, the revision `taxonomy` defaults to. Reading the
    working tree instead is how an earlier pass concluded the second ability
    had no implementation at all: this checkout is on a branch that does not
    carry it.
    """
    projection = _load("serveros_ability_projection",
                       REPO / "runtime" / "ability_projection.py")
    manifest, resolved = projection.read_manifest(ABILITY_REPO, "origin/main")
    rows = [dict(r) for r in manifest.get("abilities") or []]

    paths = projection.paths_in_tree(ABILITY_REPO, resolved)
    for entry in rows:
        entry["implementation"] = taxonomy.has_implementation(entry.get("path"), paths)

    rungs = {r["name"]: taxonomy.availability(r) for r in rows}
    assert rungs.get("feishu-report") == taxonomy.READY
    assert rungs.get("feishu-space-management") == taxonomy.IMPLEMENTED
    # The distinction is the assertion. Both are real artifacts in the
    # catalogue, and one word for both is what this round removed.
    assert rungs["feishu-report"] != rungs["feishu-space-management"]


@pytest.mark.skipif(not ABILITY_REPO.exists(),
                    reason="the ability-management checkout is not beside this repo")
def test_no_ability_in_the_real_catalogue_is_ready_without_an_implementation():
    """The rule, swept over everything rather than checked on the pair."""
    projection = _load("serveros_ability_projection",
                       REPO / "runtime" / "ability_projection.py")
    manifest, resolved = projection.read_manifest(ABILITY_REPO, "origin/main")
    paths = projection.paths_in_tree(ABILITY_REPO, resolved)

    for entry in manifest.get("abilities") or []:
        if not isinstance(entry, dict):
            continue
        implementation = taxonomy.has_implementation(entry.get("path"), paths)
        if not implementation:
            assert taxonomy.availability(dict(entry, implementation=False)) \
                == taxonomy.DECLARED, entry.get("name")
