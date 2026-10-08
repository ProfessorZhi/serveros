#!/usr/bin/env python3
"""Ability Topology V2 — the declared navigation DAG, and the checks on it.

This module owns exactly one question: *what is declared, and is the
declaration internally sound*. It answers it without a catalogue, without a
device and without a daemon, because a taxonomy that can only be validated on a
machine that has a running agent is a taxonomy nobody validates.

What it deliberately does NOT own
---------------------------------
Membership. Which abilities sit in a taxon is read from the canonical catalogue
(`domain.primary` / `taxonomy_refs`) and never written here. The one function
that needs a catalogue — `conformance` — takes the rows as an argument rather
than fetching them, so the caller decides which revision of the authority it is
checking against. A module that fetched its own authority would make every
caller agree with it by construction.

Two derivations, two questions
------------------------------
    placement     "what IS under this node"      domain.primary   ability_projection
    navigation    "what COULD be, and where"     taxonomy.v2      this module

They are allowed to differ in SHAPE and are not allowed to differ about which
abilities exist. `conformance` is where that is enforced, and it fails closed:
an ability whose `domain.primary` names a taxon this file does not declare is a
blocking finding, not a node materialised silently.

That last sentence is a change in behaviour, and it is the point of the module.
Under `domain_tree` alone, a typo in a catalogue's `domain.primary` produces a
brand new node with one ability under it, and the result is indistinguishable
from a deliberate classification. It is now an error that names the string.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read taxonomy.v2.yaml\n")
    raise SystemExit(2)

SCHEMA = "serveros/taxonomy.v2"
THIS_FILE = "abilities/taxonomy.v2.yaml"

# Closed vocabularies. Every one of these is closed for the same reason: an
# open set cannot be checked, and a check that cannot fail is not a check.
ORIGINS = ("seed", "v1", "declared")
EDGE_KINDS = ("contains", "relates")

# What a catalogue row entitles a reader to say about an ability, as a LADDER
# where each rung requires the one below it. §7's vocabulary, and the reason it
# has to exist is measurable rather than stylistic.
#
# `feishu-report` and `feishu-space-management` sit in one taxon and both have
# a payload tree at `skills/<name>/`. Asking only "is there an implementation"
# answers YES for both and merges them. They are not the same:
#
#     feishu-report            compatibility: linux/claude-code, linux/codex
#                              installations: linux-server x 2 agents
#     feishu-space-management  compatibility: []
#                              installations: []
#
# One is installed on a device and claimed by two runtimes. The other is
# packaged, declared, and in use by nobody. `OCCUPIED` was the same word for
# both, which makes it unusable for deciding where to invest.
#
# IMPLEMENTATION IS THE GATE, not a rung. An ability with no tree at its `path`
# is DECLARED and nothing more, however many installations its row claims. That
# is §7's rule — no implementation, no READY — enforced here rather than left
# to a reader to notice.
DECLARED = "DECLARED"
IMPLEMENTED = "IMPLEMENTED"
SUPPORTED = "SUPPORTED"
READY = "READY"
AVAILABILITY = (DECLARED, IMPLEMENTED, SUPPORTED, READY)

# The four states a TAXON can be in, and why four rather than two.
#
# The tempting pair is "has abilities / does not have abilities", and it is
# wrong because it collapses the interesting cases. A branch holding six real
# enforcement modules and no ability is not the same finding as a branch holding
# nothing at all, and neither is the same as a branch whose members are names in
# a list with nothing behind them. Only some of them are gaps, and they are
# different gaps. §H's rule — never report an unimplemented ability as
# AVAILABLE — needs each distinction to be sayable, and DECLARED_ONLY is the
# one that was missing: it is the state that used to be reported as OCCUPIED.
OCCUPIED = "OCCUPIED"
DECLARED_ONLY = "DECLARED_ONLY"
SURFACE_ONLY = "SURFACE_ONLY"
EMPTY = "EMPTY"


class TaxonomyError(Exception):
    """The declaration itself is unreadable. Never a reason to invent one."""


# ---------------------------------------------------------------------------
# Load
# ---------------------------------------------------------------------------


def default_path(repo_root: Path | None = None) -> Path:
    root = repo_root or Path(__file__).resolve().parent.parent
    return root / THIS_FILE


def load(path: Path | None = None) -> dict:
    """The declared document, structurally checked.

    Structural checking is separated from semantic checking on purpose. `load`
    raises for a document that is not a taxonomy at all; `validate` returns
    findings for a document that is one and is wrong. A malformed file is a
    different problem from a misdesigned one and they get different exits.
    """
    target = path or default_path()
    if not target.exists():
        raise TaxonomyError(
            f"{target} does not exist. The declared topology is the input to "
            f"every navigation answer this node gives; without it there is "
            f"nothing to navigate and this command will not guess one."
        )
    try:
        document = yaml.safe_load(target.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - an unparseable file is a fact
        raise TaxonomyError(f"{target} is not readable YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise TaxonomyError(f"{target} is not a mapping")
    observed = document.get("schema")
    if observed != SCHEMA:
        raise TaxonomyError(f"{target} declares schema {observed!r}, expected {SCHEMA!r}")
    for key in ("taxons", "edges"):
        if not isinstance(document.get(key), list):
            raise TaxonomyError(f"{target}: {key!r} must be a list")
    return document


# ---------------------------------------------------------------------------
# Validate — the declaration against itself
# ---------------------------------------------------------------------------


def _finding(findings: list[dict], severity: str, code: str, detail: str, **extra) -> None:
    findings.append({"severity": severity, "code": code, "detail": detail, **extra})


def validate(document: dict) -> dict:
    """Every way this declaration can be wrong, checked, not spot-checked.

    Returns findings rather than raising, because a caller repairing a taxonomy
    wants all of them at once. The one exception is a duplicate id: past that
    point "the node with id X" is ambiguous and every later check would be
    running against a coin flip, so it is reported and the node set is built
    first-wins, with the ambiguity named in the finding.
    """
    findings: list[dict] = []
    taxons = document.get("taxons") or []
    edges = document.get("edges") or []

    by_id: dict[str, dict] = {}
    for node in taxons:
        if not isinstance(node, dict):
            _finding(findings, "blocking", "TAXON_NOT_A_MAPPING", repr(node)[:120])
            continue
        taxon_id = node.get("id")
        if not isinstance(taxon_id, str) or not taxon_id:
            _finding(findings, "blocking", "TAXON_WITHOUT_ID", repr(node)[:120])
            continue
        if taxon_id in by_id:
            _finding(
                findings, "blocking", "DUPLICATE_TAXON_ID",
                f"{taxon_id} is declared more than once; the second declaration "
                f"is ignored and everything below is checked against the first",
                taxon=taxon_id)
            continue
        by_id[taxon_id] = node

        origin = node.get("origin")
        if origin not in ORIGINS:
            _finding(
                findings, "blocking", "INVALID_ORIGIN",
                f"origin {origin!r} is not one of {list(ORIGINS)}. An "
                f"unattributable node is one nobody can audit",
                taxon=taxon_id)
        if origin == "seed" and not node.get("label"):
            _finding(
                findings, "blocking", "SEED_WITHOUT_LABEL",
                "a seed family is a reader-facing name; without a label the "
                "navigation view can only show the dotted id",
                taxon=taxon_id)
        # `v1_parent` must be PRESENT and may be null: null says "this was a
        # top-level node in v1", and an absent key says "nobody recorded it".
        # Those look identical to a reader and are not the same claim.
        if origin == "v1" and "v1_parent" not in node:
            _finding(
                findings, "blocking", "V1_PARENT_NOT_RECORDED",
                "a v1 node must record v1_parent explicitly, using null when it "
                "was top-level. An absent key is indistinguishable from an "
                "unrecorded one",
                taxon=taxon_id)
        # `aliases` is searched, so a malformed one is a discovery hole rather
        # than a cosmetic problem: a bare string would be iterated CHARACTER BY
        # CHARACTER and quietly match on any letter it contains, which is how a
        # typo turns into a node that answers every query.
        aliases = node.get("aliases")
        if aliases is not None and (not isinstance(aliases, list) or not all(
                isinstance(a, str) and a.strip() for a in aliases)):
            _finding(
                findings, "blocking", "INVALID_ALIASES",
                f"aliases must be a list of non-empty strings, got "
                f"{aliases!r}. A bare string is iterated per character and "
                f"would match on any letter in it",
                taxon=taxon_id)

    # --- edges -------------------------------------------------------------
    contains_parents: dict[str, list[str]] = {}
    adjacency: dict[str, set[str]] = {}
    for edge in edges:
        if not isinstance(edge, dict):
            _finding(findings, "blocking", "EDGE_NOT_A_MAPPING", repr(edge)[:120])
            continue
        parent, child, kind = edge.get("from"), edge.get("to"), edge.get("kind")
        if kind not in EDGE_KINDS:
            _finding(
                findings, "blocking", "INVALID_EDGE_KIND",
                f"kind {kind!r} is not one of {list(EDGE_KINDS)}",
                edge=f"{parent}->{child}")
            continue
        if parent not in by_id or child not in by_id:
            missing = parent if parent not in by_id else child
            _finding(
                findings, "blocking", "EDGE_UNKNOWN_ENDPOINT",
                f"{parent}->{child} names {missing!r}, which no taxon declares. "
                f"An edge to a node that does not exist is how a taxonomy "
                f"acquires a node nobody declared",
                edge=f"{parent}->{child}")
            continue
        if parent == child:
            _finding(findings, "blocking", "EDGE_SELF_LOOP", f"{parent} points at itself", taxon=parent)
            continue
        adjacency.setdefault(parent, set()).add(child)
        if kind == "contains":
            contains_parents.setdefault(child, []).append(parent)

    for child, parents in sorted(contains_parents.items()):
        if len(parents) > 1:
            _finding(
                findings, "blocking", "MULTIPLE_CONTAINS_PARENTS",
                f"{child} is contained by {sorted(parents)}. A tree projection "
                f"would have to choose one silently; use `relates` for the "
                f"second link so the choice stays visible",
                taxon=child)

    # --- roots, reachability, cycles ---------------------------------------
    seeds = sorted(i for i, n in by_id.items() if n.get("origin") == "seed")
    has_contains_parent = {c for c, parents in contains_parents.items() if parents}
    roots = sorted(set(by_id) - has_contains_parent)

    # The namespace root is DERIVED, not declared, and that is deliberate.
    # Every taxon id shares a leading segment — `serveros` today — and that
    # segment is not a family: an ability classified as `serveros` is not in a
    # family, it is unclassified at the top level. Calling it an eleventh seed
    # would be wrong, and letting it through as an ordinary root would make
    # every taxonomy built on a namespace trip the same check.
    #
    # The rule is narrow enough to fail: it permits exactly one non-seed root,
    # and only if every seed id is strictly below it. Two such roots, or one
    # that does not contain all the seeds, is still `ROOT_NOT_SEED`.
    namespace = None
    if len(roots) == 1 and roots[0] not in seeds:
        candidate = roots[0]
        if seeds and all(s.startswith(candidate + ".") for s in seeds):
            namespace = candidate

    for root in roots:
        if root in seeds or root == namespace:
            continue
        _finding(
            findings, "blocking", "ROOT_NOT_SEED",
            f"{root} has no containing parent and is neither one of the ten "
            f"seed families nor the namespace root"
            + (f" (the namespace is {namespace})" if namespace else "")
            + f". Nothing can reach it from the top",
            taxon=root)

    if namespace is None and len(set(roots) - set(seeds)) > 1:
        _finding(
            findings, "blocking", "AMBIGUOUS_NAMESPACE",
            f"{sorted(set(roots) - set(seeds))} are all rootless and none of "
            f"them contains every seed, so this declaration has no single top",
            taxon=None)

    # Reachability starts at the TOP, which is the namespace root when there
    # is one and the seed set otherwise. Starting at the seeds would flag the
    # namespace and everything the namespace carries beside them — the v1
    # container, for one — as unreachable, which is true and useless: the
    # question this check exists to answer is whether anything is stranded
    # away from the top of the tree, not whether it is below a family.
    reachable: set[str] = set()
    frontier = [namespace] if namespace else list(seeds)
    while frontier:
        node = frontier.pop()
        if node in reachable:
            continue
        reachable.add(node)
        frontier.extend(adjacency.get(node, ()))
    for taxon_id in sorted(set(by_id) - reachable):
        _finding(
            findings, "blocking", "UNREACHABLE_FROM_TOP",
            f"{taxon_id} cannot be reached from "
            f"{namespace or 'any seed family'} by following containment edges",
            taxon=taxon_id)

    # A cycle among `contains` edges would have shown up as zero roots, but a
    # cycle that also has an escape edge does not. Walked explicitly rather
    # than inferred from the root count.
    cycle = _find_contains_cycle(contains_parents)
    if cycle:
        _finding(
            findings, "blocking", "CONTAINS_CYCLE",
            " -> ".join(cycle) + " closes a containment loop",
            taxon=cycle[0])

    blocking = [f for f in findings if f["severity"] == "blocking"]
    return {
        "schema": SCHEMA,
        "basis": "declaration",
        "ok": not blocking,
        "seeds": seeds,
        "taxon_count": len(by_id),
        "edge_count": len(edges),
        "findings": findings,
        "summary": {
            "blocking": len(blocking),
            "informational": len(findings) - len(blocking),
        },
    }


def _find_contains_cycle(contains_parents: dict[str, list[str]]) -> list[str]:
    """The first containment cycle, as the path that closes it. [] if none."""
    colour: dict[str, int] = {}

    def walk(node: str, path: list[str]) -> list[str]:
        if colour.get(node) == 1:
            return path[path.index(node):] + [node]
        if colour.get(node) == 2:
            return []
        colour[node] = 1
        for parent in contains_parents.get(node, ()):
            found = walk(parent, path + [node])
            if found:
                return found
        colour[node] = 2
        return []

    for start in sorted(contains_parents):
        found = walk(start, [])
        if found:
            return list(reversed(found))
    return []


# ---------------------------------------------------------------------------
# Project — the DAG as a tree, and what that projection loses
# ---------------------------------------------------------------------------


def tree(document: dict) -> dict:
    """The navigation DAG projected onto a tree, with the loss made explicit.

    `contains` edges are followed; `relates` edges are returned as `cross_refs`
    rather than followed. That asymmetry is the whole reason a DAG projection
    can be honest: the tree says where a node lives, the cross-refs say what
    else it touches, and a reader can see that the second set exists instead of
    having to know it was dropped.

    Node ORDER is pre-order depth-first with siblings sorted by id. Sorted
    rather than declaration-ordered so that reordering the YAML — which is not
    a semantic act — cannot change the rendered view.
    """
    by_id = {n["id"]: n for n in document["taxons"] if isinstance(n, dict) and n.get("id")}
    children: dict[str, list[str]] = {}
    cross: dict[str, list[str]] = {}
    for edge in document["edges"]:
        if not isinstance(edge, dict):
            continue
        parent, child, kind = edge.get("from"), edge.get("to"), edge.get("kind")
        if parent not in by_id or child not in by_id or parent == child:
            continue
        if kind == "contains":
            children.setdefault(parent, []).append(child)
        elif kind == "relates":
            cross.setdefault(parent, []).append(child)
            cross.setdefault(child, []).append(parent)

    seeds = sorted(i for i, n in by_id.items() if n.get("origin") == "seed")
    # `children` is parent -> [children], so the set of nodes that have a
    # parent is the union of its VALUES. Reading it as its keys gives the set
    # of nodes that HAVE children, which is the opposite set, and the symptom
    # is a tree with no namespace and every node at depth zero — a view that
    # looks like a flat taxonomy rather than a broken traversal.
    has_parent = {child for kids in children.values() for child in kids}
    roots = sorted(set(by_id) - has_parent)
    # The namespace root, derived the same way `validate` derives it so the two
    # cannot disagree about which node is the top. The tree is rendered from
    # the namespace's CHILDREN rather than from the namespace itself: a family
    # indented one level in to make room for a node whose only content is a
    # prefix spends the leftmost column on nothing.
    namespace = None
    if len(roots) == 1 and roots[0] not in seeds and seeds \
            and all(s.startswith(roots[0] + ".") for s in seeds):
        namespace = roots[0]
    starts = sorted(children.get(namespace, ())) if namespace else roots
    # The namespace's children are not all families. A v1 container is a
    # grouping that outlived the tree it grouped: it carries cross-references
    # and no abilities of its own. Rendered beside the seeds it reads as an
    # eleventh family, which is a claim about the fabric that nobody made. So
    # the two are separated here and the projection says which is which. The
    # DAG itself is untouched — a reader who wants the container follows its
    # cross-references, which is exactly what `relates` is for.
    families = [s for s in starts if s in seeds]
    containers = [s for s in starts if s not in seeds]

    nodes: list[dict] = []
    seen: set[str] = set()

    def walk(node: str, depth: int, branch: str) -> None:
        # Depth-first with a seen-guard rather than trusting the validator:
        # `tree` is also called on a document that failed validation, so the
        # rendering of a broken taxonomy is available for a reader to look at.
        if node in seen:
            return
        seen.add(node)
        entry = by_id[node]
        nodes.append({
            "id": node,
            "depth": depth,
            "branch": branch,
            "label": entry.get("label"),
            "origin": entry.get("origin"),
            "surface": entry.get("surface"),
        })
        for child in sorted(children.get(node, ())):
            walk(child, depth + 1, branch)

    # Families first, then containers, so the rendered top level is the ten
    # families and nothing else. Order within each group is by id, so
    # reordering the declaration cannot move a row.
    for start in families:
        walk(start, 0, start)
    for start in containers:
        walk(start, 0, start)

    orphans = sorted(set(by_id) - seen - ({namespace} if namespace else set()))
    return {
        "schema": SCHEMA,
        "basis": "declared-taxonomy",
        "namespace": namespace,
        "families": families,
        "containers": containers,
        "note": (
            "This is the NAVIGATION view. `serverosctl ability tree` answers "
            "the different question of where abilities are actually placed, "
            "from the catalogue's domain.primary. The two are checked against "
            "each other by `serverosctl taxonomy validate`."
        ),
        "nodes": nodes,
        "cross_refs": {k: sorted(set(v)) for k, v in sorted(cross.items())},
        "unreachable": orphans,
        "seed_count": len(seeds),
    }


# ---------------------------------------------------------------------------
# Membership and conformance — the declaration against the catalogue
# ---------------------------------------------------------------------------


def _row_taxon(row: dict) -> str | None:
    domain = row.get("domain")
    if isinstance(domain, dict):
        primary = domain.get("primary")
        return primary if isinstance(primary, str) and primary else None
    return None


def _refs(row: dict) -> list[str]:
    return [r for r in (row.get("taxonomy_refs") or []) if isinstance(r, str) and r]


def _prefixes(path: str) -> list[str]:
    """Every dotted prefix, inclusive. `a.b.c` -> ['a', 'a.b', 'a.b.c']."""
    parts = path.split(".")
    return [".".join(parts[: i + 1]) for i in range(len(parts))]


def has_implementation(declared_path, paths) -> bool:
    """Is there a tree at the catalogue's `path` in the authority revision.

    `paths` is `ls-tree -r` output, which contains FILES ONLY. A catalogue
    `path` names a directory — `skills/feishu-report` — so equality alone
    answers NO for every ability in the fleet. The separator is what makes the
    prefix test safe: without it, `skills/feishu` would be satisfied by a file
    under `skills/feishu-report/`, which is a different ability.
    """
    if not declared_path:
        return False
    prefix = str(declared_path).rstrip("/") + "/"
    return any(entry == declared_path or entry.startswith(prefix) for entry in paths)


def _declares_support(row: dict) -> bool:
    """Does any revision claim this ability runs on a platform.

    `compatibility` is a list of `{agent, os}` the catalogue asserts. An empty
    list is the absence of a claim, not a claim of universal support — which
    is why it is read with `or []` and answered False rather than skipped.
    """
    for revision in row.get("revisions") or []:
        if isinstance(revision, dict) and (revision.get("compatibility") or []):
            return True
    return False


def _declares_installation(row: dict) -> bool:
    """Does any revision record this ability installed on some device."""
    for revision in row.get("revisions") or []:
        if isinstance(revision, dict) and (revision.get("installations") or []):
            return True
    return False


def availability(row: dict) -> str:
    """The highest rung this row's own fields support.

    Fails closed at the first question. A row that does not carry
    `implementation` — a synthetic row, or a caller that never read the
    authority — is DECLARED. The module does not assume a path exists because
    a catalogue mentions one, and it does not promote to READY on the strength
    of an installation record for an artifact nobody can point at.
    """
    if not row.get("implementation"):
        return DECLARED
    if not _declares_support(row):
        return IMPLEMENTED
    if not _declares_installation(row):
        return SUPPORTED
    return READY


def membership(document: dict, rows: list[dict]) -> dict:
    """Which abilities sit in each taxon, READ from the catalogue's own fields.

    `placed` is `domain.primary` — exactly one taxon per ability, which is the
    placement rule `domain_tree` already enforces. `cross_referenced` is
    `taxonomy_refs` — any number, and never a second placement. Keeping the two
    as separate lists rather than one merged one is what stops a cross-reference
    from looking like a home.
    """
    placed: dict[str, list[str]] = {}
    referenced: dict[str, list[str]] = {}
    by_name: dict[str, dict] = {}
    for row in rows:
        name = row.get("name") or row.get("id")
        if not name:
            continue
        by_name[name] = row
        primary = _row_taxon(row)
        if primary:
            placed.setdefault(primary, []).append(name)
        for ref in _refs(row):
            referenced.setdefault(ref, []).append(name)

    entries: dict[str, dict] = {}
    for node in document["taxons"]:
        if not isinstance(node, dict) or not node.get("id"):
            continue
        taxon_id = node["id"]
        here = sorted(placed.get(taxon_id, []))
        there = sorted(referenced.get(taxon_id, []))
        # What each member is, not merely that it was named. Reported per
        # member so that a taxon holding one deployed ability and one packaged
        # one shows both facts instead of one word covering them.
        rungs = {name: availability(by_name.get(name) or {}) for name in here}
        materialised = sorted(n for n, rung in rungs.items() if rung != DECLARED)
        # The four states, decided in one place. OCCUPIED now requires a
        # MATERIALISED member: naming an ability under a taxon is a declaration
        # about navigation, and this is the line where it stops being read as
        # evidence that the ability exists. `surface` separates "declared and
        # unoccupied" from "declared, unoccupied, and backed by code that really
        # runs" — see the OCCUPIED/DECLARED_ONLY/SURFACE_ONLY/EMPTY note.
        if materialised:
            state = OCCUPIED
        elif here:
            state = DECLARED_ONLY
        elif node.get("surface"):
            state = SURFACE_ONLY
        else:
            state = EMPTY
        entries[taxon_id] = {
            "state": state,
            "placed": here,
            "materialised": materialised,
            "availability": rungs,
            "cross_referenced": there,
            "surface": node.get("surface"),
            "origin": node.get("origin"),
            "label": node.get("label"),
            "aliases": [str(a) for a in (node.get("aliases") or [])],
        }
    return entries


def conformance(document: dict, rows: list[dict]) -> dict:
    """Every catalogue taxon string against the declaration. Fails closed.

    The blocking direction is `UNDECLARED_TAXON`: the catalogue names a node
    this file does not declare. Under the old behaviour that silently created a
    node. It is now an error naming the exact string, because a taxonomy that
    grows to fit whatever it is shown is not a taxonomy.

    Prefixes are checked, not just whole values. A `domain.primary` of
    `a.b.c` makes `a` and `a.b` visible nodes in the tree the node renders, so
    a taxonomy that declared only `a.b.c` would be declaring a leaf and
    rendering three nodes.
    """
    declared = {n["id"] for n in document["taxons"] if isinstance(n, dict) and n.get("id")}
    fields: dict[str, set[str]] = {}
    for row in rows:
        name = row.get("name") or row.get("id") or "<unnamed>"
        primary = _row_taxon(row)
        if primary:
            for prefix in _prefixes(primary):
                fields.setdefault(prefix, set()).add(f"domain.primary of {name}")
        for ref in _refs(row):
            for prefix in _prefixes(ref):
                fields.setdefault(prefix, set()).add(f"taxonomy_refs of {name}")

    findings: list[dict] = []
    for path in sorted(set(fields) - declared):
        sources = sorted(fields[path])
        _finding(
            findings, "blocking", "UNDECLARED_TAXON",
            f"the catalogue names {path!r} ({', '.join(sources[:4])}"
            f"{', …' if len(sources) > 4 else ''}) but {THIS_FILE} does not "
            f"declare it. Either declare the node or fix the catalogue string; "
            f"a taxonomy that materialises whatever it is shown is not a check",
            taxon=path)

    entries = membership(document, rows)
    occupied = sorted(k for k, v in entries.items() if v["state"] == OCCUPIED)
    declared_only = sorted(k for k, v in entries.items() if v["state"] == DECLARED_ONLY)
    surface_only = sorted(k for k, v in entries.items() if v["state"] == SURFACE_ONLY)
    empty = sorted(k for k, v in entries.items() if v["state"] == EMPTY)

    # Two findings, because they are two situations with two remedies. A taxon
    # with nothing materialised is "this family has no implementation behind it";
    # a taxon with SOME members materialised and one that is not is "this member
    # is packaged and in use by nobody", which is a different decision. Both are
    # warnings: neither makes the declaration unsound, and calling them blocking
    # would stop the command from rendering a tree that is worth looking at.
    for taxon_id in declared_only:
        entry = entries[taxon_id]
        _finding(
            findings, "warning", "DECLARED_NOT_IMPLEMENTED",
            f"{taxon_id} is declared and names {len(entry['placed'])} ability/ies "
            f"({', '.join(entry['placed'][:4])}), none of which has an "
            f"implementation at its catalogue `path` in this revision. The taxon "
            f"is not occupied. Naming an ability in a catalogue is a statement "
            f"about navigation, not evidence that anything exists",
            taxon=taxon_id)

    for taxon_id, entry in sorted(entries.items()):
        packaged = sorted(n for n, rung in entry["availability"].items()
                          if rung == IMPLEMENTED)
        if not packaged:
            continue
        _finding(
            findings, "warning", "PACKAGED_NOT_IN_USE",
            f"{taxon_id} holds {', '.join(packaged)}: implemented, but no "
            f"revision declares a `compatibility` with any runtime. Packaged and "
            f"unclaimed — nothing claims it can run and no device reports it "
            f"installed",
            taxon=taxon_id)

    blocking = [f for f in findings if f["severity"] == "blocking"]
    return {
        "schema": SCHEMA,
        "basis": "catalogue",
        "ok": not blocking,
        "ability_count": len(rows),
        "placements": entries,
        "occupied": occupied,
        "surface_only": surface_only,
        "empty": empty,
        "findings": findings,
        "summary": {
            "blocking": len(blocking),
            "declared": len(declared),
            "occupied": len(occupied),
            "declared_only": len(declared_only),
            "surface_only": len(surface_only),
            "empty": len(empty),
        },
        "occupied": occupied,
        "declared_only": declared_only,
        "availability": sorted(
            {rung for v in entries.values() for rung in v["availability"].values()}),
    }


def _haystacks(taxon_id: str, entry: dict) -> list[tuple[str, str]]:
    """Every string this taxon can be found by, paired with WHAT KIND it is.

    The kind is not decoration. It is what lets a reader see that `飞书` matched
    an alias rather than a taxon id — the difference between "this node is
    called that" and "somebody calls it that" — and the difference between
    finding a node by its own name and finding it through an ability that
    happens to live there.
    """
    pairs = [("taxon id", taxon_id)]
    if entry.get("label"):
        pairs.append(("label", entry["label"]))
    pairs += [("placed ability", a) for a in entry.get("placed") or []]
    pairs += [("cross-referenced ability", a)
              for a in entry.get("cross_referenced") or []]
    pairs += [("alias", a) for a in entry.get("aliases") or []]
    return pairs


def search(document: dict, rows: list[dict], query: str) -> dict:
    """Cases-insensitive substring match over ids, labels, aliases, ability names.

    Search-first is the discovery rule (§AA): a caller should be able to ask
    "what is there about testing" without knowing that the answer lives under
    `serveros.operations.test` and that `test-management` is the ability. This
    is deliberately a substring match and not a ranking function — a ranker
    that is wrong is harder to notice than a matcher that is simple.

    Two things it does that a single-needle match did not, and both were asked
    of it by real queries rather than invented:

      * a multi-word query is split, and a taxon matches only if EVERY word is
        found somewhere in it. "network safety" is a person naming one subject
        they expect to be one thing; matched as a literal phrase it found
        nothing, and the node that is about both words went unreported.
        Conjunction, not ranking: nothing is ordered by how well it matched
        and the answer stays a set.
      * `aliases` are searched alongside the id. The catalogue is written in
        one language and asked about in another: `serveros.corporate.feishu`
        holds two abilities and the query "飞书" found none of them, because
        the node's id, its label and its members are all spelled in Latin. An
        alias is a declaration that a word names this node. It is not a second
        label and it is not membership.
    """
    tokens = [t for t in (query or "").strip().lower().split() if t]
    if not tokens:
        raise TaxonomyError("search needs a query; an empty one matches everything")
    entries = membership(document, rows)
    hits = []
    for taxon_id, entry in sorted(entries.items()):
        haystacks = _haystacks(taxon_id, entry)
        matched: list[str] = []
        for token in tokens:
            # Every token is checked against the WHOLE taxon, never against
            # what earlier tokens already found. Deduplicating here instead of
            # on the way out is a bug this round actually shipped and the
            # `network safety` test caught: the second word matched the same
            # field as the first, that field was already in `matched`, and the
            # check collapsed to "this word is nowhere" — the node about both
            # words was the one node excluded.
            kinds = [kind for kind, value in haystacks if token in value.lower()]
            if not kinds:
                matched = []
                break
            matched.extend(k for k in kinds if k not in matched)
        if matched:
            hits.append({"taxon": taxon_id, "matched_on": matched, **entry})
    return {
        "schema": SCHEMA,
        "basis": "declared-taxonomy",
        "query": query,
        "tokens": tokens,
        "hits": hits,
        "count": len(hits),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _emit(payload: dict, as_json: bool, lines: list[str]) -> None:
    if as_json:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True, default=str)
        sys.stdout.write("\n")
    else:
        sys.stdout.write("\n".join(lines) + "\n")


def _render_tree(projection: dict, entries: dict, declared: set[str]) -> list[str]:
    lines = [f"authority   {projection['basis']}",
             f"namespace   {projection.get('namespace') or '(none)'}",
             f"seeds       {projection['seed_count']}",
             ""]
    # A container is not a family, and the two must not sit in one list. The
    # heading names the distinction at the point the reader meets it, rather
    # than in a note at the bottom that a reader scanning rows will not reach.
    container_branch = set(projection.get("containers") or ())
    # Two renderings of a node's name, chosen by ambiguity rather than by
    # taste. The last segment is what makes the tree readable — `engineering`
    # says more than `serveros.engineering` when the indentation already
    # carries the ancestry. But `serveros.work` and `serveros.operations.work`
    # are both rendered as `work`, and two rows reading `work` in one list is a
    # view that cannot be checked against the declaration. Where a last segment
    # is not unique, the full id is printed instead.
    tails: dict[str, int] = {}
    for taxon_id in declared:
        tail = taxon_id.split(".")[-1]
        tails[tail] = tails.get(tail, 0) + 1

    nodes = projection["nodes"]
    subtree_size = _subtree_sizes(nodes)
    heading_shown = False
    for index, node in enumerate(nodes):
        if node.get("branch") in container_branch and not heading_shown:
            lines += ["", "v1 containers — grouping, not families "
                          "(cross-referenced from the branches above)"]
            heading_shown = True
        taxon_id = node["id"]
        tail = taxon_id.split(".")[-1]
        shown = taxon_id if tails.get(tail, 0) > 1 else tail
        label = f"  {node['label']}" if node.get("label") else ""
        if entries is None:
            # The catalogue did not answer. Printing `[EMPTY 0]` here would
            # report "this branch has nothing in it" when the truth is "nobody
            # asked" — the exact conflation this repository refuses elsewhere.
            lines.append(f"{'  ' * node['depth']}{shown}{label}  [?]")
            continue
        entry = entries.get(taxon_id, {})
        state = entry.get("state", "UNKNOWN")
        # Two counts, not one — the same distinction `serverosctl ability tree`
        # makes and for the same reason. `serveros.work` is a node AND a family
        # parent, so a single `(0)` beside it reads as "this branch is empty"
        # when it means "nothing is placed at exactly this level".
        here = len(entry.get("placed") or [])
        below = sum(len(entries.get(other["id"], {}).get("placed") or [])
                    for other in nodes[index + 1: index + 1 + subtree_size[index]])
        counts = f"{here} here, {below} below" if below else f"{here}"
        suffix = f"  [{state} {counts}]"
        lines.append(f"{'  ' * node['depth']}{shown}{label}{suffix}")
    if projection["unreachable"]:
        lines.append("")
        lines.append(f"unreachable  {', '.join(projection['unreachable'])}")
    return lines


def _subtree_sizes(nodes: list[dict]) -> list[int]:
    """For each node in a pre-order list, how many entries follow it in its subtree.

    Pre-order gives every subtree a contiguous span, so the size is the run of
    strictly deeper entries that follows. Computed once here rather than
    re-walked per node, and derived from the rendering order the reader is
    actually looking at — a count that disagrees with the indentation beside
    it would be worse than no count.
    """
    sizes = []
    for index, node in enumerate(nodes):
        depth = node["depth"]
        size = 0
        for follower in nodes[index + 1:]:
            if follower["depth"] <= depth:
                break
            size += 1
        sizes.append(size)
    return sizes


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="taxonomy", description=__doc__.split("\n")[0])
    parser.add_argument("--file", help=f"override {THIS_FILE}")
    parser.add_argument("--catalogue", help="path to abilities-manifest.yaml (for validate)")
    parser.add_argument("--revision", default="origin/main",
                        help="catalogue revision to read (default: origin/main)")
    parser.add_argument("--repository", help="path to the ability-management checkout")
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="subcommand")

    sub.add_parser("tree")
    sub.add_parser("validate")
    p_show = sub.add_parser("show")
    p_show.add_argument("taxon")
    p_search = sub.add_parser("search")
    p_search.add_argument("query")

    args = parser.parse_args(argv)
    path = Path(args.file) if args.file else default_path()
    try:
        document = load(path)
    except TaxonomyError as exc:
        sys.stderr.write(f"taxonomy: {exc}\n")
        return 2

    report = validate(document)
    if not report["ok"]:
        # Fail before the catalogue is even consulted. A declaration that is
        # internally broken cannot be checked against anything, and reporting
        # conformance against a broken tree would give the second set of
        # findings a false air of authority.
        if args.json:
            json.dump(report, sys.stdout, indent=2, sort_keys=True)
            sys.stdout.write("\n")
        else:
            sys.stderr.write("taxonomy: the declaration is not sound\n")
            for finding in report["findings"]:
                sys.stderr.write(f"  [{finding['severity']}] {finding['code']}: {finding['detail']}\n")
        return 1

    declared = {n["id"] for n in document["taxons"]
                if isinstance(n, dict) and n.get("id")}

    if args.subcommand in (None, "tree"):
        projection = tree(document)
        # The navigation tree is useful without a catalogue and its header
        # says which one it was drawn against, so an unreadable authority is
        # recorded in the output rather than being a reason to refuse.
        rows = _load_rows(args, required=False)
        entries = membership(document, rows) if rows is not None else None
        projection["catalogue"] = {
            "status": "READ" if rows is not None else "UNAVAILABLE",
            "ability_count": len(rows) if rows is not None else None,
        }
        _emit(projection, args.json, _render_tree(projection, entries, declared))
        return 0

    rows = _load_rows(args, required=True)
    if rows is None:
        return 2
    entries = membership(document, rows)

    if args.subcommand == "validate":
        result = conformance(document, rows)
        if args.json:
            json.dump(result, sys.stdout, indent=2, sort_keys=True, default=str)
            sys.stdout.write("\n")
        else:
            summary = result["summary"]
            sys.stdout.write(
                f"declared     {summary['declared']}\n"
                f"occupied     {summary['occupied']}\n"
                f"declared-only {summary['declared_only']}\n"
                f"surface-only {summary['surface_only']}\n"
                f"empty        {summary['empty']}\n"
                f"blocking     {summary['blocking']}\n")
            for finding in result["findings"]:
                sys.stdout.write(f"  [{finding['severity']}] {finding['code']}: {finding['detail']}\n")
        return 0 if result["ok"] else 1

    if args.subcommand == "show":
        entry = entries.get(args.taxon)
        if entry is None:
            sys.stderr.write(
                f"taxonomy: {args.taxon!r} is not a declared taxon. This "
                f"command does not invent one — `taxonomy search` will find "
                f"the nearest declared name.\n")
            return 2
        payload = {"schema": SCHEMA, "taxon": args.taxon, **entry}
        lines = [f"taxon        {args.taxon}",
                 f"state        {entry['state']}",
                 f"origin       {entry['origin']}"]
        if entry.get("label"):
            lines.append(f"label        {entry['label']}")
        if entry.get("surface"):
            lines.append(f"surface      {entry['surface']}")
        lines.append(f"placed       {', '.join(entry['placed']) or '(none)'}")
        # Per member, not per taxon. `state` above answers "is this family
        # staffed"; this answers "what is each thing under it", which is the
        # question `OCCUPIED` was previously, and wrongly, taken to answer.
        for name in entry["placed"]:
            lines.append(f"  {entry['availability'].get(name, DECLARED):<12} {name}")
        lines.append(f"cross-refs   {', '.join(entry['cross_referenced']) or '(none)'}")
        _emit(payload, args.json, lines)
        return 0

    if args.subcommand == "search":
        result = search(document, rows, args.query)
        lines = [f"{hit['taxon']}  [{hit['state']}]  (matched on {', '.join(hit['matched_on'])})"
                 for hit in result["hits"]]
        _emit(result, args.json, lines or [f"no declared taxon matches {args.query!r}"])
        return 0

    parser.error(f"unknown subcommand {args.subcommand!r}")
    return 2


def _load_rows(args, *, required: bool) -> list[dict] | None:
    """Catalogue rows, or None with the reason already on stderr.

    Imported lazily and by path because this module is loaded standalone by the
    CLI's `_load` idiom; a top-level import of `ability_projection` would make
    the two modules impossible to test separately.
    """
    import importlib.util

    #: Registered under the name the rest of the repository uses for this file,
    #: so a caller that already loaded it gets the same module. Two copies would
    #: be two sets of classes, and an `except` written against one would not
    #: recognise the other's.
    projection = sys.modules.get("serveros_ability_projection")
    if projection is None:
        here = Path(__file__).resolve().parent
        spec = importlib.util.spec_from_file_location(
            "serveros_ability_projection",
            here.parent / "runtime" / "ability_projection.py")
        projection = importlib.util.module_from_spec(spec)
        sys.modules["serveros_ability_projection"] = projection
        spec.loader.exec_module(projection)

    repo = Path(args.repository) if args.repository else _default_ability_repo()
    if repo is None or not (repo / ".git").exists():
        # Said out loud in BOTH cases, not only when a verdict depends on it.
        # `tree` renders without the catalogue, so it passes `required=False`
        # and this branch used to return None in silence — which is how a whole
        # taxonomy came to read `[?]` with nothing anywhere saying that the
        # question had not been asked. `[?]` is the right thing to print; a
        # reader who cannot find out why is the defect.
        if required:
            sys.stderr.write(
                "taxonomy: no ability-management checkout found. Pass "
                "--repository, or set SERVEROS_ABILITY_REPO. Conformance is "
                "checked against the catalogue and this command will not "
                "report a verdict without one.\n")
        else:
            sys.stderr.write(
                f"taxonomy: no ability-management checkout at "
                f"{repo if repo is not None else '(unresolved)'}. Occupancy is "
                f"not shown — pass --repository, or set "
                f"SERVEROS_ABILITY_REPO.\n")
        return None
    try:
        manifest, _resolved = projection.read_manifest(
            repo, args.revision, args.catalogue or "abilities-manifest.yaml")
        # The SAME revision the manifest came from. Two readings of one
        # authority at two revisions is how "the catalogue says X" and "there is
        # no X" get said in one breath, which is the shape of the finding this
        # round had to correct: a claim that an ability has no implementation,
        # made against a checkout on a branch the taxonomy never reads.
        #
        # A tree that cannot be listed is not "nothing is implemented" — that
        # would turn a git failure into a page of DECLARED_ONLY findings about
        # abilities that exist. It is the authority being unreachable, and it is
        # handled by the same except clause as an unreadable manifest.
        paths = projection.paths_in_tree(repo, _resolved)
    except projection.AuthorityUnreachable as exc:
        if required:
            sys.stderr.write(f"taxonomy: catalogue unreadable: {exc}\n")
        else:
            sys.stderr.write(
                f"taxonomy: catalogue unreadable, occupancy is not shown: {exc}\n")
        return None
    # EVERY ability in the manifest, not only the ones installed here. The
    # question "does the catalogue name a taxon we do not declare" is about the
    # whole catalogue; filtering by this device's installations would make the
    # check pass on a machine that happens to have few abilities installed,
    # which is the machine most likely to have the problem.
    rows = []
    for ability in manifest.get("abilities") or []:
        if not isinstance(ability, dict):
            continue
        # The implementation fact, attached where the authority was read so
        # that `membership` and `availability` stay pure functions over rows
        # and every caller of conformance can see the basis it decided on.
        ability["implementation"] = has_implementation(ability.get("path"), paths)
        rows.append(ability)
    return rows


def _default_ability_repo() -> Path | None:
    """Where the catalogue lives, in the order that keeps the machine out of code.

    `SERVEROS_ABILITY_REPO` first. Which checkout to read is a fact about a
    deployment, so the deployment names it — the same split the host profile
    makes for the network policy, and for the same reason: a path that is true
    on one machine is a bug on the next one.

    The sibling lookup below is a convenience for a SOURCE checkout, where
    `serveros` and `ability-management` sit side by side. It deliberately does
    not grow more candidates. This module is also executed from a promoted
    artifact under `runtime-assets/<composition>/revisions/<rev>/`, where the
    sibling resolves to `revisions/ability-management` and does not exist; the
    honest answer there is "this deployment has not named its catalogue", and
    the caller now says exactly that instead of the search quietly wandering
    until something happens to hit a path that exists on one machine.
    """
    import os

    configured = os.environ.get("SERVEROS_ABILITY_REPO")
    if configured:
        return Path(configured)
    sibling = Path(__file__).resolve().parent.parent.parent / "ability-management"
    return sibling if (sibling / ".git").exists() else None


if __name__ == "__main__":
    sys.exit(main())
