#!/usr/bin/env python3
"""Tests for Ability Topology V2 (§G).

The interesting half of this file is not the rendering. It is the refusal.

Before V2, ability structure was derived from the catalogue's `domain.primary`
alone, and that derivation is generous: any string a catalogue happens to carry
becomes a node, with no complaint and no record that nobody chose it. A typo
therefore produced a new family. So the tests below lean on the checks that
make the declaration a *check* — an undeclared taxon is blocking, a second
containment parent is blocking, a cycle is blocking, and an unattributable
origin is blocking.

Hermetic for everything synthetic: documents are built in memory so each
failure mode can be reached in isolation. The two tests that read the REAL
declaration and the REAL catalogue are marked because they depend on a sibling
checkout, and a test that silently passes when its input is absent is worse
than one that is skipped.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest
import yaml

REPO = Path(__file__).resolve().parent.parent
TAXONOMY_PATH = REPO / "abilities" / "taxonomy.v2.yaml"
ABILITY_REPO = REPO.parent / "ability-management"


def _load_taxonomy():
    name = "serveros_abilities_taxonomy"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, REPO / "abilities" / "taxonomy.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


taxonomy = _load_taxonomy()


def document(taxons, edges) -> dict:
    return {"schema": taxonomy.SCHEMA, "revision": 1, "taxons": taxons, "edges": edges}


def seed(taxon_id, **extra) -> dict:
    return {"id": taxon_id, "origin": "seed", "label": taxon_id.title(), **extra}


def declared(taxon_id, **extra) -> dict:
    return {"id": taxon_id, "origin": "declared", **extra}


def contains(parent, child) -> dict:
    return {"from": parent, "to": child, "kind": "contains"}


def codes(report: dict) -> set[str]:
    return {f["code"] for f in report["findings"]}


# ---------------------------------------------------------------------------
# The declaration as it actually ships
# ---------------------------------------------------------------------------


def test_the_declared_taxonomy_is_sound():
    report = taxonomy.validate(taxonomy.load(TAXONOMY_PATH))
    assert report["ok"], [f["detail"] for f in report["findings"]]
    assert len(report["seeds"]) == 10


def test_the_ten_seed_families_are_the_ones_the_spec_names():
    """A seed set that drifts by one is a navigation change nobody decided."""
    report = taxonomy.validate(taxonomy.load(TAXONOMY_PATH))
    labels = {
        n["label"] for n in taxonomy.load(TAXONOMY_PATH)["taxons"]
        if n.get("origin") == "seed"
    }
    assert labels == {
        "Agent & Coordination", "Runtime & Resources", "Safety & Compliance",
        "Work & Development", "Memory & Context", "Human & Communication",
        "Knowledge & Information", "Network & Environment",
        "Security & Credentials", "Creative & Automation",
    }
    assert len(report["seeds"]) == 10


def test_every_v1_node_records_where_it_came_from():
    """An unattributable node is one nobody can audit, so the field is required."""
    document_ = taxonomy.load(TAXONOMY_PATH)
    v1 = [n for n in document_["taxons"] if n.get("origin") == "v1"]
    assert v1, "the declaration should still carry inherited v1 nodes"
    for node in v1:
        assert "v1_parent" in node, node["id"]


# ---------------------------------------------------------------------------
# What the validator must refuse
# ---------------------------------------------------------------------------


def test_a_duplicate_id_is_blocking():
    report = taxonomy.validate(document(
        [seed("serveros.a"), declared("serveros.a")], []))
    assert "DUPLICATE_TAXON_ID" in codes(report)
    assert not report["ok"]


def test_an_edge_to_an_undeclared_node_is_blocking():
    """The shape the old derivation punished nobody for."""
    report = taxonomy.validate(document(
        [seed("serveros.a"), declared("serveros.a.b")],
        [contains("serveros.a", "serveros.a.typo")]))
    assert "EDGE_UNKNOWN_ENDPOINT" in codes(report)
    assert not report["ok"]


def test_two_containment_parents_are_blocking():
    report = taxonomy.validate(document(
        [seed("serveros.a"), seed("serveros.b"), declared("serveros.a.b")],
        [contains("serveros.a", "serveros.a.b"), contains("serveros.b", "serveros.a.b")]))
    assert "MULTIPLE_CONTAINS_PARENTS" in codes(report)
    assert not report["ok"]


def test_a_relates_edge_between_the_same_pair_is_not_blocking():
    """The DAG half has to remain available, or the rule above is a tree rule."""
    report = taxonomy.validate(document(
        [seed("serveros.a"), seed("serveros.b"), declared("serveros.a.b")],
        [contains("serveros.a", "serveros.a.b"),
         {"from": "serveros.b", "to": "serveros.a.b", "kind": "relates"}]))
    assert report["ok"], [f["detail"] for f in report["findings"]]


def test_a_containment_cycle_is_blocking():
    report = taxonomy.validate(document(
        [declared("serveros.a"), declared("serveros.a.b")],
        [contains("serveros.a", "serveros.a.b"), contains("serveros.a.b", "serveros.a")]))
    assert "CONTAINS_CYCLE" in codes(report)
    assert not report["ok"]


def test_an_unknown_origin_is_blocking():
    report = taxonomy.validate(document(
        [{"id": "serveros.a", "origin": "probably-fine", "label": "A"}], []))
    assert "INVALID_ORIGIN" in codes(report)


def test_a_seed_without_a_label_is_blocking():
    """A seed is the reader-facing name of a family; the dotted id is not one."""
    report = taxonomy.validate(document([{"id": "serveros.a", "origin": "seed"}], []))
    assert "SEED_WITHOUT_LABEL" in codes(report)


def test_a_root_that_is_not_a_seed_is_blocking():
    report = taxonomy.validate(document(
        [seed("serveros.a"), declared("serveros.stray")], []))
    assert "ROOT_NOT_SEED" in codes(report)
    assert not report["ok"]


def test_a_node_stranded_from_the_top_is_blocking():
    report = taxonomy.validate(document(
        [seed("serveros.a"), declared("serveros.b"), declared("serveros.b.c")],
        [contains("serveros.b", "serveros.b.c")]))
    assert "ROOT_NOT_SEED" in codes(report)


# ---------------------------------------------------------------------------
# The namespace root is derived, not declared
# ---------------------------------------------------------------------------


def test_a_single_root_containing_every_seed_is_the_namespace():
    report = taxonomy.validate(document(
        [declared("root"), seed("root.a"), seed("root.b")],
        [contains("root", "root.a"), contains("root", "root.b")]))
    assert report["ok"], [f["detail"] for f in report["findings"]]


def test_a_root_that_does_not_contain_every_seed_is_not_a_namespace():
    report = taxonomy.validate(document(
        [declared("root"), declared("other"), seed("root.a")],
        [contains("root", "root.a")]))
    assert not report["ok"]
    assert "ROOT_NOT_SEED" in codes(report)


def test_a_declared_seed_root_needs_no_namespace_at_all():
    report = taxonomy.validate(document(
        [seed("alpha"), seed("beta"), declared("alpha.child")],
        [contains("alpha", "alpha.child")]))
    assert report["ok"], [f["detail"] for f in report["findings"]]


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def test_the_tree_renders_families_at_the_top_not_the_namespace():
    projection = taxonomy.tree(taxonomy.load(TAXONOMY_PATH))
    assert projection["namespace"] == "serveros"
    assert all(n["depth"] == 0 for n in projection["nodes"]
               if n["origin"] == "seed")
    assert "serveros" not in {n["id"] for n in projection["nodes"]}


def test_a_container_is_not_rendered_as_an_eleventh_family():
    """The namespace's children are not all families.

    `serveros.operations` is a v1 grouping that outlived the tree it grouped.
    Rendered in the same list as the seeds it reads as the eleventh family,
    which is a claim about the fabric that nobody made.
    """
    projection = taxonomy.tree(taxonomy.load(TAXONOMY_PATH))
    assert len(projection["families"]) == 10
    assert projection["containers"], "the declaration carries a v1 container"
    assert not set(projection["families"]) & set(projection["containers"])
    # Every family is a seed and every container is not; that is the whole
    # distinction, so it is stated rather than implied by the split above.
    by_id = {n["id"]: n for n in taxonomy.load(TAXONOMY_PATH)["taxons"]}
    assert all(by_id[f]["origin"] == "seed" for f in projection["families"])
    assert all(by_id[c]["origin"] != "seed" for c in projection["containers"])
    # Families render first, so the rows a reader meets are the ten families.
    branches = [n["branch"] for n in projection["nodes"]]
    assert branches.index(projection["containers"][0]) > max(
        branches.index(f) for f in projection["families"])


def test_the_work_trunk_carries_every_branch_the_spec_names():
    """§H names seven. A branch with nothing in it is still a branch.

    The point of the check is that the fabric can SAY where the gap is. Three
    of these hold no ability today, and the test asserts they exist as nodes
    rather than asserting they are occupied.
    """
    # Read through the projection rather than by id prefix. Four of the seven
    # branches kept their v1 ids — `serveros.operations.workspace` is the
    # workspace branch, not a `serveros.work.` child — and a prefix test would
    # report a gap where there is a rename nobody made.
    projection = taxonomy.tree(taxonomy.load(TAXONOMY_PATH))
    under_work = {node["id"].split(".")[-1] for node in projection["nodes"]
                  if node.get("branch") == "serveros.work"}
    assert under_work >= {"intake", "workspace", "work", "test",
                          "analysis-design", "engineering", "reporting"}


def test_relates_edges_become_cross_refs_and_are_not_followed():
    projection = taxonomy.tree(taxonomy.load(TAXONOMY_PATH))
    assert projection["cross_refs"], "the declared DAG has cross-family edges"
    # A cross-ref must never manufacture a placement: the node it points at
    # still appears exactly once in the projected node list.
    ids = [n["id"] for n in projection["nodes"]]
    assert len(ids) == len(set(ids))


def test_the_projection_of_a_broken_document_terminates():
    """`tree` is reachable on a document that failed validation, so it must not hang."""
    broken = document(
        [declared("serveros.a"), declared("serveros.a.b")],
        [contains("serveros.a", "serveros.a.b"), contains("serveros.a.b", "serveros.a")])
    projection = taxonomy.tree(broken)  # must not raise or loop
    assert len({n["id"] for n in projection["nodes"]}) == len(projection["nodes"])


# ---------------------------------------------------------------------------
# Conformance — the check that makes this a check
# ---------------------------------------------------------------------------


ROWS = [
    {"name": "placed-here", "domain": {"primary": "serveros.a.b"},
     "taxonomy_refs": ["serveros.c"]},
    {"name": "placed-nowhere", "domain": None, "taxonomy_refs": []},
]


def test_a_catalogue_string_the_declaration_omits_is_blocking():
    report = taxonomy.conformance(
        document([seed("serveros.a"), declared("serveros.a.b")],
                 [contains("serveros.a", "serveros.a.b")]),
        ROWS)
    assert not report["ok"]
    # Membership on the axis under test rather than equality of the whole set.
    # The run also reports availability findings now — the fixture's rows name
    # abilities with nothing behind them — and those are a different question
    # from "is every catalogue string declared". Asserting the set exactly would
    # make this test fail whenever that other axis grows, which measures the
    # fixture and not the rule.
    blocking = {f["code"] for f in report["findings"] if f["severity"] == "blocking"}
    assert "UNDECLARED_TAXON" in blocking
    # The finding has to NAME the string. A count of undeclared taxons tells a
    # reader there is a problem and nothing about which one.
    assert any("serveros.c" in f["detail"] for f in report["findings"])


def test_a_prefix_of_a_declared_leaf_must_itself_be_declared():
    """`a.b.c` renders three nodes; a taxonomy that declares only the leaf is
    declaring one and rendering three."""
    report = taxonomy.conformance(
        document([seed("serveros.a.b.c")],
                 []),
        [{"name": "x", "domain": {"primary": "serveros.a.b.c"}, "taxonomy_refs": []}])
    assert not report["ok"]
    assert any(f.get("taxon") in {"serveros.a", "serveros.a.b"}
               for f in report["findings"])


def test_a_declared_taxon_with_nothing_in_it_is_not_blocking():
    """That is the whole reason a declared taxonomy exists."""
    report = taxonomy.conformance(
        document([seed("serveros"), declared("serveros.quiet")],
                 [contains("serveros", "serveros.quiet")]),
        [])
    assert report["ok"]
    assert report["summary"]["empty"] == 2


def test_the_four_states_are_distinguishable():
    """OCCUPIED vs DECLARED_ONLY vs SURFACE_ONLY vs EMPTY.

    The row carries `implementation` because that is now part of what a
    catalogue row means to this module. Leaving it off and expecting OCCUPIED
    would be asserting that a name in a list is evidence something exists,
    which is the reading §7 forbids — the fourth state is what that row gets.
    """
    doc = document(
        [seed("serveros"),
         declared("serveros.full"),
         declared("serveros.packaged"),
         declared("serveros.surface", surface="runtime/network_policy.py"),
         declared("serveros.nothing")],
        [contains("serveros", "serveros.full"),
         contains("serveros", "serveros.packaged"),
         contains("serveros", "serveros.surface"),
         contains("serveros", "serveros.nothing")])
    entries = taxonomy.membership(doc, [
        {"name": "an-ability", "domain": {"primary": "serveros.full"},
         "implementation": True},
        {"name": "a-name-only", "domain": {"primary": "serveros.packaged"}}])
    assert entries["serveros.full"]["state"] == taxonomy.OCCUPIED
    assert entries["serveros.packaged"]["state"] == taxonomy.DECLARED_ONLY
    assert entries["serveros.surface"]["state"] == taxonomy.SURFACE_ONLY
    assert entries["serveros.nothing"]["state"] == taxonomy.EMPTY


def test_a_cross_reference_never_counts_as_a_placement():
    doc = document(
        [seed("serveros"), declared("serveros.home"), declared("serveros.elsewhere")],
        [contains("serveros", "serveros.home"), contains("serveros", "serveros.elsewhere")])
    entries = taxonomy.membership(doc, [
        {"name": "roamer", "domain": {"primary": "serveros.home"},
         "taxonomy_refs": ["serveros.elsewhere"]}])
    assert entries["serveros.home"]["placed"] == ["roamer"]
    assert entries["serveros.elsewhere"]["placed"] == []
    assert entries["serveros.elsewhere"]["cross_referenced"] == ["roamer"]


# ---------------------------------------------------------------------------
# The declaration against the catalogue that actually exists
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not ABILITY_REPO.exists(),
                    reason="the ability-management checkout is not beside this repo")
def test_the_real_catalogue_conforms_to_the_declaration():
    """The invariant this round added: the catalogue may not invent taxons."""
    manifest, _ = _read_manifest()
    rows = list(manifest.get("abilities") or [])
    assert rows, "the catalogue should not be empty"
    report = taxonomy.conformance(taxonomy.load(TAXONOMY_PATH), rows)
    assert report["ok"], [f["detail"] for f in report["findings"]]
    assert report["summary"]["blocking"] == 0


@pytest.mark.skipif(not ABILITY_REPO.exists(),
                    reason="the ability-management checkout is not beside this repo")
def test_the_declaration_does_not_invent_abilities():
    """Every ability the taxonomy claims to hold exists in the catalogue."""
    manifest, _ = _read_manifest()
    rows = list(manifest.get("abilities") or [])
    catalogue_names = {r.get("name") for r in rows}
    entries = taxonomy.membership(taxonomy.load(TAXONOMY_PATH), rows)
    for taxon_id, entry in entries.items():
        for name in entry["placed"]:
            assert name in catalogue_names, f"{taxon_id} claims {name}"


def _read_manifest():
    projection = _load_projection()
    return projection.read_manifest(ABILITY_REPO, "origin/main")


def _load_projection():
    name = "serveros_ability_projection"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, REPO / "runtime" / "ability_projection.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# The file's own contract
# ---------------------------------------------------------------------------


def test_the_declaration_contains_no_ability_membership():
    """§G: taxonomy.v2 may hold taxon nodes and taxon edges, and nothing else.

    Stated as a test rather than a comment because a member list is the one
    thing here that would silently become a second catalogue, and it would look
    completely reasonable in review.
    """
    document_ = yaml.safe_load(TAXONOMY_PATH.read_text(encoding="utf-8"))
    allowed = {"schema", "revision", "generated", "catalogue", "taxons", "edges"}
    assert set(document_) <= allowed, set(document_) - allowed
    for node in document_["taxons"]:
        # `aliases` joins the allowed keys because it is part of a node's own
        # NAME, not a list of what lives under it. The guard that matters is
        # the next line, and it is unchanged: an alias says a word names this
        # node, and can never say that an ability is there.
        assert set(node) <= {"id", "origin", "label", "summary", "surface",
                             "v1_parent", "aliases"}, node["id"]
        assert "abilities" not in node and "members" not in node, node["id"]


def test_search_refuses_an_empty_query():
    """An empty query matches everything, which is enumeration by another name."""
    with pytest.raises(taxonomy.TaxonomyError):
        taxonomy.search(taxonomy.load(TAXONOMY_PATH), [], "")

# ---------------------------------------------------------------------------
# §Q3 · a taxonomy that cannot read its catalogue has to say so, and a home is
#        not a claim that anything lives in it
# ---------------------------------------------------------------------------


class _Args:
    """Just the attributes `_load_rows` reads off its argparse namespace."""

    def __init__(self, repository="", revision="origin/main", catalogue=""):
        self.repository = repository
        self.revision = revision
        self.catalogue = catalogue


def test_a_deployment_names_its_own_catalogue(monkeypatch, tmp_path):
    """`SERVEROS_ABILITY_REPO` beats the sibling lookup of a source checkout.

    Which checkout to read is a fact about a deployment, so the deployment says
    it. The sibling lookup is a convenience that is true only where the two
    repositories sit side by side — and this module also runs from a promoted
    artifact under `runtime-assets/<composition>/revisions/<rev>/`, where the
    sibling resolves to `revisions/ability-management` and has never existed.
    """
    monkeypatch.setenv("SERVEROS_ABILITY_REPO", str(tmp_path))
    assert taxonomy._default_ability_repo() == tmp_path


def test_a_render_without_a_catalogue_says_why_it_is_blank(capsys, monkeypatch,
                                                           tmp_path):
    """`[?]` is the right thing to print. Silence about it is not.

    `tree` renders without the catalogue, so it asks for rows with
    `required=False`, and that used to mean the reason never reached anybody:
    a whole taxonomy came back `[?]` with nothing on screen saying that the
    question had not been asked. That is also what made ten seeded families
    read as ten empty ones.
    """
    monkeypatch.setenv("SERVEROS_ABILITY_REPO", str(tmp_path / "absent"))
    assert taxonomy._load_rows(_Args(), required=False) is None
    err = capsys.readouterr().err
    assert "SERVEROS_ABILITY_REPO" in err
    assert "Occupancy is not shown" in err


def test_a_verdict_still_refuses_when_the_catalogue_is_absent(capsys, monkeypatch,
                                                              tmp_path):
    """The `required=True` case keeps its own, louder message."""
    monkeypatch.setenv("SERVEROS_ABILITY_REPO", str(tmp_path / "absent"))
    assert taxonomy._load_rows(_Args(), required=True) is None
    assert "report a verdict" in capsys.readouterr().err


def test_a_placement_is_navigation_and_never_promotes_the_ability():
    """§Q3: naming an ability under a taxon must not make it available.

    The two questions are answered from different fields and the test pins
    them apart. `domain.primary` moves the TAXON's state and nothing else; the
    ability's own rung is decided by `implementation`, compatibility and
    installation. So an ability that exists only as a name can occupy a
    branch's navigation and still be DECLARED.
    """
    rows = [{"name": "ghost", "domain": {"primary": "serveros.work"}}]
    entries = taxonomy.membership(document([seed("serveros.work")], []), rows)
    entry = entries["serveros.work"]
    assert entry["placed"] == ["ghost"]
    assert entry["availability"] == {"ghost": taxonomy.DECLARED}
    assert entry["materialised"] == []
    # Placed, and therefore not EMPTY — but not OCCUPIED either.
    assert entry["state"] == taxonomy.DECLARED_ONLY
    assert taxonomy.availability(rows[0]) == taxonomy.DECLARED


@pytest.mark.skipif(not ABILITY_REPO.exists(),
                    reason="the ability-management checkout is not beside this repo")
def test_the_senses_and_actuators_node_is_declared_and_occupied():
    """`serveros.agent.embodiment` is declared BECAUSE the catalogue uses it.

    A `declared` node the catalogue never names is a promise with nothing
    behind it. This one exists so that four real browser/desktop abilities
    have a home, and the conformance check would fail on it in either
    direction: undeclared in this file it is UNDECLARED_TAXON, and named by no
    ability the node is still legitimate but unreferenced. The second half is
    what this asserts.
    """
    manifest, _ = _read_manifest()
    rows = list(manifest.get("abilities") or [])
    entries = taxonomy.membership(taxonomy.load(TAXONOMY_PATH), rows)
    entry = entries["serveros.agent.embodiment"]
    assert entry["placed"], "the node is declared but nothing was placed in it"
    assert entry["origin"] == "declared"


@pytest.mark.skipif(not ABILITY_REPO.exists(),
                    reason="the ability-management checkout is not beside this repo")
def test_most_of_the_catalogue_has_a_home():
    """§Q3 measured the gap: 33 of 55 rows carried no `domain.primary`.

    A row with no placement is not shown anywhere a reader can browse to, so
    the families look thin for a reason that has nothing to do with what the
    fabric can do. The bound is deliberately loose — this is a floor against
    regression, not a claim that every row SHOULD be placed, because some rows
    are distribution channels and hook bundles and have no domain to be in.
    """
    manifest, _ = _read_manifest()
    rows = list(manifest.get("abilities") or [])
    placed = [r for r in rows if taxonomy._row_taxon(r)]
    assert len(placed) >= 35, f"only {len(placed)} of {len(rows)} rows are placed"


def test_a_query_in_another_language_finds_the_node():
    """§Q3 measured this one: "飞书" returned nothing while the node held two
    abilities, because its id, its label and all of its members are Latin."""
    doc = document([seed("serveros.human"),
                    {"id": "serveros.corporate.feishu", "origin": "v1",
                     "v1_parent": "serveros.corporate",
                     "aliases": ["飞书", "lark"]}],
                   [])
    rows = [{"name": "feishu-card",
             "domain": {"primary": "serveros.corporate.feishu"}}]
    result = taxonomy.search(doc, rows, "飞书")
    assert [h["taxon"] for h in result["hits"]] == ["serveros.corporate.feishu"]
    assert "alias" in result["hits"][0]["matched_on"]
    # …and the member is still found by its own name, so the alias widened
    # discovery rather than replacing it.
    assert taxonomy.search(doc, rows, "feishu-card")["count"] == 1


def test_every_word_of_a_query_has_to_match():
    """ "network safety" is one subject a person spells as two words.

    Matched as a literal phrase it found nothing. Split, it finds the one node
    whose own id carries both — and the conjunction stays a conjunction: a
    query whose words never co-occur is still empty rather than a loose OR.
    """
    doc = document([seed("serveros.network"), seed("serveros.safety"),
                    declared("serveros.safety.network-conformance")], [])
    result = taxonomy.search(doc, [], "network safety")
    assert [h["taxon"] for h in result["hits"]] == [
        "serveros.safety.network-conformance"]
    assert result["tokens"] == ["network", "safety"]
    assert taxonomy.search(doc, [], "network kubernetes")["count"] == 0


def test_an_alias_is_a_name_and_never_a_membership():
    """A node found by an alias is exactly as occupied as it was before.

    The failure this pins down is the tempting one: an alias makes a node
    ANSWER, and answering is what an occupied node does too. If a synonym ever
    counted as a member, "somebody calls it that" would read as "something is
    there".
    """
    doc = document([seed("serveros.creative", aliases=["art", "创作"])], [])
    entry = taxonomy.membership(doc, [])["serveros.creative"]
    assert entry["aliases"] == ["art", "创作"]
    assert entry["placed"] == []
    assert entry["materialised"] == []
    assert entry["state"] == taxonomy.EMPTY


def test_a_bare_string_alias_is_a_blocking_finding():
    """`aliases: wip` would be iterated per character and match on any letter."""
    assert "INVALID_ALIASES" in codes(taxonomy.validate(
        document([seed("serveros.work", aliases="wip")], [])))
    assert "INVALID_ALIASES" in codes(taxonomy.validate(
        document([seed("serveros.work", aliases=["ok", ""])], [])))
    assert "INVALID_ALIASES" not in codes(taxonomy.validate(
        document([seed("serveros.work", aliases=["wip", "需求"])], [])))


@pytest.mark.skipif(not ABILITY_REPO.exists(),
                    reason="the ability-management checkout is not beside this repo")
def test_the_nine_real_queries_all_reach_something():
    """The acceptance list §Q3 named, run against the real catalogue.

    Every one of these was a real question; five of them returned nothing
    before this round. Pinned as a list rather than as prose because the value
    of a search is exactly the set of things people asked it and could not
    find.
    """
    manifest, _ = _read_manifest()
    rows = list(manifest.get("abilities") or [])
    doc = taxonomy.load(TAXONOMY_PATH)
    for query in ("workspace", "飞书", "alert", "docker", "memory",
                  "requirement", "test", "report", "network safety"):
        found = taxonomy.search(doc, rows, query)
        assert found["count"] >= 1, f"{query!r} reaches nothing"
