#!/usr/bin/env python3
"""Materialize the local capability projection (§27/§28).

`serverosd` already knows how to READ a capability projection: it labels every
answer with the authority, says whether the view is fresh, stale, or has never
been materialized, and refuses to collapse "nothing is installed" into "nobody
has asked". What it cannot do is produce one, because producing one means
reading ability-management — and §27 makes ability-management the authority.

This module is that producer, and it is deliberately on the ServerOS side: the
projection is a *local observation*, written under the node's own state root,
and a projection that lived on the authority's disk would be the authority
answering, which is the coupling §28 exists to remove.

Three properties are load-bearing, and each is here because the obvious
implementation gets it wrong:

1. Derived, never authored. Every ability in the output comes from the
   authority's manifest at an explicitly pinned revision. Nothing is carried
   over from a previous projection except on the STALE path below, where
   carrying over is the entire point.

2. `generated_at` means "when the authority was last successfully read", not
   "when this file was last written". Those differ on every failed refresh, and
   conflating them is how a three-day-old capability list reads as current:
   the reader ages the projection from this field, so advancing it on a failed
   refresh would reset the age of data that did not change.

3. A failure never erases. If the authority cannot be read, the previous
   abilities survive verbatim and `registry_status` becomes STALE. Writing an
   empty list instead would turn "I could not ask" into "there is nothing",
   which is the same conflation the reader goes out of its way to avoid.

4. A question that names nobody is refused, not answered. Reading the authority
   and matching nothing in it are two different facts, and the second cannot be
   written down as a projection: `FRESH` with `abilities: []` is a claim that
   this node has none, which a zero match does not support. So a zero match
   raises and writes nothing, and `allow_empty` is how a caller says the empty
   answer was the question. Until 2026-10-05 this module wrote it unasked, and
   a mistyped `--device-id` erased eighteen abilities and reported success.

It writes TWO files from ONE read, and §35 is why they are two files rather
than two keys of one:

  * `effective.json` — the local projection: what the authority records as
    **installed on this device**.
  * `catalogue.json` — the authority's catalogue at the same pinned revision:
    every ability it lists, as bounded metadata, with no device in it at all.

A node whose only local answer is the projection cannot tell an ability the
catalogue has never heard of from one it has merely not recorded here, and
those two demand opposite responses. Both are facts a discovery surface needs,
and a surface that reads only the first reports the second as `matched: 0` —
which is how `ability-management`, installed in the composition this node
runs, came back as "does not exist". §35 keeps the projection local on
purpose, so the wider answer is a second artifact and not a widened first one.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read abilities-manifest.yaml\n")
    raise SystemExit(2)

SCHEMA = "serveros/ability-projection.v1"
AUTHORITY = "ability-management"
THIS_IS = "a local observed projection, not the authority"
PROJECTION_NAME = "effective.json"

CATALOGUE_SCHEMA = "serveros/ability-catalogue.v1"
CATALOGUE_NAME = "catalogue.json"
CATALOGUE_THIS_IS = ("the authority's catalogue at a pinned revision, not an "
                     "installation record")

DEVICE_REF_PREFIX = "device:"

# The manifest keys this projection is allowed to read. An allow-list rather
# than a deny-list: the manifest also carries `authors[]`, which holds public
# keys and fingerprints. A key is not an ability, and a projection is an ability
# inventory, so none of it is copied here (§30). Naming what may be read means a
# future field is excluded by default rather than included by accident.
#
# The rule for adding one: **every field on this list has to be a classification
# OF an ability, and never a fact about a person.** `domain` and `taxonomy_refs`
# qualify — they say where an ability sits in the tree, which is what §4 asks a
# projection to answer and what the four discovery tools read. `authors` is why
# the list exists and is the shape of what must never be added: the moment a
# projection carries a name, an email or a key, the projection is a directory of
# people and every downstream reader inherits that. `tests/test_ability_projection.py`
# asserts this over serialized bytes rather than over the list, so a field that
# reached the output by another path is caught too.
ABILITY_FIELDS = ("name", "id", "type", "path", "domain", "taxonomy_refs")


class AuthorityUnreachable(Exception):
    """The authority could not be read. Never a reason to invent a projection."""


class NoInstallationMatched(Exception):
    """The authority was read and none of the ids given name an installation.

    Raised INSTEAD of writing, and that is the entire design. A zero match is
    consistent with two opposite facts — "this node has no abilities" and "these
    ids name some other machine or are misspelled" — and a projection can only
    state the first. Writing it turns a question that was answered about nobody
    into an answer about this node, which is the shape this project keeps
    finding: a check that returns the empty set instead of failing.

    It carries the inputs as typed and the ids the manifest actually names,
    because the cost of the failure it replaces was that the operator had to
    guess which of the two they were looking at.
    """

    def __init__(self, asked: tuple[str, ...], normalized: tuple[str, ...],
                 manifest_device_ids: tuple[str, ...], records: int) -> None:
        self.asked = asked
        self.normalized = normalized
        self.manifest_device_ids = manifest_device_ids
        self.records = records
        named = ", ".join(manifest_device_ids) or "no installation at all"
        spelled = ("" if normalized == asked
                   else f" (normalized: {', '.join(normalized)})")
        super().__init__(
            f"no installation in the manifest names this device; asked for "
            f"{', '.join(asked) or '(nothing)'}{spelled}, and the manifest's "
            f"{records} record(s) name {named}. Nothing was written: an empty "
            f"projection would claim this node has no abilities, which a zero "
            f"match does not establish. Pass `--allow-empty` if an empty "
            f"projection is what you intend."
        )


def bare_device_ids(device_ids: tuple[str, ...]) -> tuple[str, ...]:
    """The ids with ServerOS's `device:` ref prefix removed, deduplicated.

    Two vocabularies name one device and this node uses both. ServerOS names a
    device to its own surfaces as `device:<id>` — the node's identity line and
    every `device` argument are spelled that way — while the authority records
    an installation under the bare id. Accepting only one of them makes the
    spelling an operator is most likely to copy and paste the one that finds
    nothing and reports no error, which is precisely what happened: on
    2026-10-05 `--device-id device:tokyo-shared-dev-01` matched no
    installation, and the empty result was written over a projection holding
    eighteen abilities.

    Stripping is done here, in one place, so that the ids compared against the
    manifest and the ids recorded in the projection are the same ones. An empty
    id is dropped rather than carried: it can never name a device, and a caller
    that passes one gets the refusal above rather than a silent match on `""`.
    """
    names: list[str] = []
    for raw in device_ids:
        name = str(raw)
        if name.startswith(DEVICE_REF_PREFIX):
            name = name[len(DEVICE_REF_PREFIX):]
        if name and name not in names:
            names.append(name)
    return tuple(names)


def manifest_device_ids(manifest: dict) -> tuple[str, ...]:
    """Every device id the manifest names, sorted — the vocabulary to compare against.

    Reported by the zero-match refusal rather than kept private, because the
    one thing the operator cannot see from their own command line is which
    spelling the authority uses.
    """
    names = set()
    for entry in manifest.get("abilities") or []:
        if not isinstance(entry, dict):
            continue
        for revision in entry.get("revisions") or []:
            if not isinstance(revision, dict):
                continue
            for installation in revision.get("installations") or []:
                if isinstance(installation, dict) and installation.get("device_id"):
                    names.add(str(installation["device_id"]))
    return tuple(sorted(names))


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise AuthorityUnreachable(
            f"git {' '.join(args)} failed in {repo}: "
            f"{proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


def read_manifest(repo: Path, rev: str, path: str = "abilities-manifest.yaml") -> tuple[dict, str]:
    """(manifest, full revision SHA) read from a git object, not a working tree.

    Reading the object rather than the file means a stale or dirty checkout on
    this machine cannot silently become the thing the node believes about its
    own capabilities — the same discipline the bundle build uses for the
    reusable Skills, for the same reason.
    """
    resolved = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    if not resolved:
        raise AuthorityUnreachable(f"cannot resolve authority revision: {rev}")
    raw = _git(repo, "show", f"{resolved}:{path}")
    try:
        document = yaml.safe_load(raw.decode())
    except Exception as exc:  # noqa: BLE001 - an unparseable manifest is a fact
        raise AuthorityUnreachable(f"{path} at {resolved[:12]} is not readable YAML: {exc}") from exc
    if not isinstance(document, dict):
        raise AuthorityUnreachable(f"{path} at {resolved[:12]} is not a mapping")
    return document, resolved


def paths_in_tree(repo: Path, rev: str) -> set[str]:
    """Every path in the authority's tree at `rev`.

    The implementation fact for an ability is "there is a tree at its `path`",
    and it is asked of the same revision the manifest was read from. Asking the
    working tree instead would let a checkout's branch answer for the authority
    — which is how a claim that an ability has no implementation got made about
    an ability that has four files, on a ref nobody had checked out.

    A path here may be a directory or a file; callers test membership of the
    ability's own `path`, which the catalogue writes as a directory.
    """
    resolved = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    if not resolved:
        raise AuthorityUnreachable(f"cannot resolve authority revision: {rev}")
    raw = _git(repo, "ls-tree", "-r", "--name-only", resolved)
    return {line for line in raw.decode(errors="replace").splitlines() if line}


def _matching_installations(revision: dict, device_ids: tuple[str, ...]) -> list[dict]:
    found = []
    for installation in revision.get("installations") or []:
        if not isinstance(installation, dict):
            continue
        if str(installation.get("device_id") or "") in device_ids:
            found.append(installation)
    return found


def installed_abilities(manifest: dict, device_ids: tuple[str, ...]) -> list[dict]:
    """The abilities the authority records as installed on this device.

    `device_ids` is a set rather than one id on purpose, and the reason is a
    property of the manifest rather than of any one device. The manifest records
    each installation under the id that was in force when it happened, so a
    device that has been renamed carries evidence under more than one id —
    `fleet/registry.yaml` keeps the older ones in that device's `legacy_ids`
    precisely so it keeps resolving after the rename. Matching only the canonical
    id would report zero installed abilities with no error at all, which is
    exactly the declared-but-absent shape this project keeps finding: a check
    that returns the empty set rather than a failure.

    The ids are the CALLER's to supply, and supplying another device's ids is
    the failure this signature cannot catch. `linux-server` is the legacy id of
    `device:server-beijing`, not of the node this runtime is installed on —
    whose `legacy_ids` is empty — so passing it answers about Beijing and looks
    exactly like answering about here. It did: this device's projection read 30
    abilities through that id, every one of them from a machine that is not this
    one. The CLI help for `--device-id` states the rule ("an id that means THIS
    node"); this paragraph is the reason to believe it.

    Revisions are taken in manifest order and the LAST match wins, because the
    manifest appends a revision when it re-computes one. `revisions_installed`
    is carried alongside so that "this device has one revision" and "this
    device has accumulated thirty-four of them" do not look the same to a
    reader who only sees the winner.
    """
    abilities = []
    for entry in manifest.get("abilities") or []:
        if not isinstance(entry, dict):
            continue
        matches = [r for r in (entry.get("revisions") or [])
                   if isinstance(r, dict) and _matching_installations(r, device_ids)]
        if not matches:
            continue
        newest = matches[-1]
        agents = sorted({str(i.get("agent")) for i in _matching_installations(newest, device_ids)
                         if i.get("agent")})
        record = {field: entry.get(field) for field in ABILITY_FIELDS}
        record.update({
            "version": newest.get("version"),
            "fingerprint": newest.get("fingerprint"),
            "agents": agents,
            "revisions_installed": len(matches),
        })
        abilities.append(record)
    return sorted(abilities, key=lambda a: str(a.get("name")))


TREE_SCHEMA = "serveros/ability-tree.v1"


def tree_rows(abilities) -> list[dict]:
    """The exact metadata the discovery tree may consume, and nothing else.

    A separate function rather than a step inside `domain_tree()` because the
    split is a claim of its own: **the tree is derived from classification
    metadata, never from `path`.** §4 says the tree is a logical projection and
    §19 says physical storage stays flat, so the one input the algorithm must
    not see is a source address. Passing rows rather than ability records means
    the algorithm cannot read `path` even by accident, and `path` is still
    carried in the row because a reader of the output wants to know where the
    thing lives.

    This mirrors `_projection()` in ability-management's
    `scripts/render_ability_map.py` deliberately, down to the sort key, so that
    the two implementations can be compared on the same input.
    `tests/ability_tree_reference.py` holds that shared input captured from the
    real catalogue, and `tests/test_ability_projection.py` asserts the two
    agree.
    """
    rows = []
    for ability in abilities or ():
        if not isinstance(ability, dict):
            continue
        domain = ability.get("domain") or {}
        primary = domain.get("primary") if isinstance(domain, dict) else None
        refs = ability.get("taxonomy_refs") or []
        rows.append({
            "id": ability.get("id"),
            "name": ability.get("name"),
            "path": ability.get("path"),
            # A primary is a dotted path or it is nothing, and "nothing" gets
            # ONE spelling here. The catalogue writes it four ways — the key
            # absent, `null`, `{}`, and `""` — and `domain_tree()` happens to
            # treat all four the same only because Python treats them as falsy.
            # A row that carries `""` would put a consumer testing `is None` in
            # disagreement with the tree, with nothing anywhere to say which of
            # them is right. Normalizing at the boundary is what makes the row
            # a declared shape rather than whatever the manifest happened to say.
            "primary": primary if isinstance(primary, str) and primary else None,
            # An empty reference is not a path to anything; keeping it would make
            # `domain_tree()` emit a cross-reference that names no node.
            "refs": sorted(str(ref) for ref in refs if ref)
            if isinstance(refs, list) else [],
        })
    rows.sort(key=lambda row: (str(row["name"]), str(row["id"])))
    return rows


def domain_tree(rows) -> dict:
    """§4's tree, derived from `domain.primary` alone.

    Three properties, each because the obvious implementation gets it wrong:

    1. **An internal node exists because an ability's primary path runs through
       it, never because the taxonomy says it should.** There is no five-domain
       skeleton here and no default node. A tree that materialized the empty
       domains would report Structure that no ability is in, which is the
       "manufacture an ability so the tree looks complete" failure §4 forbids,
       one level up: a node is a claim that something is under it.

    2. **An ability appears exactly once**, under its primary. `taxonomy_refs`
       are cross-references — alternate reader paths — and are returned in their
       own field rather than duplicated into the tree. The near miss is placing
       by refs as well, which makes `test-management` appear twice and turns a
       tree into a search result.

    3. **An ability with no `domain.primary` is listed as unplaced, not
       defaulted into a node.** A large fraction of the catalogue is in that
       state — 33 of 50 abilities, which are 55 records, measured on
       `ability-management@origin/main` 2026-09-15 — and the number moves every
       time an ability is classified, so it is recorded with the ref it was
       read at rather than as a fact about the catalogue. A default would put
       them somewhere plausible and wrong. §4 is
       explicit that the tree is generated from the real catalogue: an
       unclassified ability has no place because nobody has said where it goes,
       which is a fact about the catalogue and not a hole in this function.

    4. **A record is not an ability.** Several abilities in this catalogue ship
       in two forms — an `mcps/` record and a `plugins/` record — sharing one
       canonical id and one name, so the 55 rows above describe 50 abilities.
       The danger is not arithmetic. Read as abilities, one form carries the
       classification while the other carries none, and the same ability is
       then placed *and* reported unplaced in a single answer: it says where
       the thing is and that nobody has said where it is. So rows are grouped
       by ability before either list is built, and an ability whose records
       declare different homes raises rather than being resolved — one home
       picked out of two would be invented, which is the same refusal as
       point 3's, one step further in.

    Returns nodes, placements, unplaced and cross_refs. It does not render, and
    it carries no display order: how a tree is drawn is the authority's
    presentation, and this node's job is to say where things are.
    """
    nodes: set[str] = set()
    placements: dict[str, str] = {}
    unplaced: list[str] = []
    cross_refs: dict[str, list[str]] = {}

    # Rows are per-record; the tree is per-ability, and point 4 above is why
    # those are different counts. Grouping first is what keeps one ability out
    # of both answers at once.
    by_name: dict[str, list[dict]] = {}
    for row in rows or ():
        by_name.setdefault(str(row.get("name")), []).append(row)

    for name, records in by_name.items():
        declared = sorted({str(r["primary"]) for r in records if r.get("primary")})
        if len(declared) > 1:
            # Reported, not resolved. These records are one ability by the
            # authority's own identity field, and any single home chosen from
            # them would be this view's invention rather than the catalogue's
            # statement — the failure point 3 refuses for an absent primary.
            raise ValueError(
                f"{name!r} is one ability whose records declare different "
                f"homes: {declared}. This view reports a tree; it does not "
                f"resolve a catalogue conflict.")
        if not declared:
            unplaced.append(name)
            continue
        primary = declared[0]
        segments = primary.split(".")
        # Every prefix is a node, including the single-segment root: the
        # authority's own renderer prints that root, and a tree whose top level
        # vanished would put every ability one level too high.
        for depth in range(1, len(segments) + 1):
            nodes.add(".".join(segments[:depth]))
        placements[name] = primary

        # A reference to an ability's own node is not a cross-reference; it is
        # the placement, repeated. Only classified abilities can carry one: an
        # unplaced ability has no home for a second path to be an alternative
        # to. Gathered across the ability's records, since either form may be
        # the one that declares the reader paths.
        extra: list[str] = []
        for record in records:
            for ref in (record.get("refs") or ()):
                ref = str(ref)
                if ref != primary and ref not in extra:
                    extra.append(ref)
        if extra:
            cross_refs[name] = sorted(extra)

    return {
        "schema": TREE_SCHEMA,
        "basis": "domain.primary",
        "nodes": sorted(nodes),
        "placements": dict(sorted(placements.items())),
        "unplaced": sorted(unplaced),
        "cross_refs": dict(sorted(cross_refs.items())),
    }


def project(manifest: dict, device_ids: tuple[str, ...], *, resolved_sha: str,
            now: datetime) -> dict:
    """A FRESH projection. Only ever built from an authority read that worked."""
    return {
        "schema": SCHEMA,
        "authority": AUTHORITY,
        "this_is": THIS_IS,
        "registry_status": "FRESH",
        "generated_at": _iso(now),
        "source_revision": resolved_sha,
        "device_ids_matched": list(device_ids),
        "abilities": installed_abilities(manifest, device_ids),
    }


def catalogue(manifest: dict, *, resolved_sha: str, now: datetime) -> dict:
    """The whole catalogue's bounded metadata, one entry per manifest record.

    `ABILITY_FIELDS` and nothing else, for exactly the reason the allow-list
    exists one function up: the manifest also carries `authors[]`, and a
    node-local file that names people is a directory of people. An entry here
    is a classification OF an ability — what it is and where it sits — never an
    installation and never a fact about a person.

    One entry per record, not per name. The manifest lists an ability once per
    source (`mcps/X` and `plugins/X`), and collapsing the two would drop a path
    a caller may be searching for. `installed_abilities` keeps the same shape
    for the same reason, so a reader of both files sees one convention.

    **This is not a projection and does not stand in for one.** It says what
    the authority's catalogue contains; it says nothing whatever about this
    device. An ability present here and absent from `effective.json` is not a
    defect in either file — it is the single fact the two files exist to tell
    apart, and it is the fact the discovery surface was missing.
    """
    entries = []
    for entry in manifest.get("abilities") or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        entries.append({field: entry.get(field) for field in ABILITY_FIELDS})
    entries.sort(key=lambda row: (str(row.get("name")), str(row.get("id"))))
    return {
        "schema": CATALOGUE_SCHEMA,
        "authority": AUTHORITY,
        "this_is": CATALOGUE_THIS_IS,
        "registry_status": "FRESH",
        "generated_at": _iso(now),
        "source_revision": resolved_sha,
        "entries": entries,
    }


def catalogue_stale(previous: dict | None, *, reason: str, now: datetime) -> dict:
    """Carried through on the same terms as the projection, for the same reason.

    The catalogue in hand did not change because a later read failed. Writing
    `entries: []` here would turn "I could not ask" into "the catalogue is
    empty" — the conflation this module refuses one file over, and a worse one,
    because an empty catalogue is a positive claim that nothing exists.
    """
    carried = previous or {}
    return {
        "schema": CATALOGUE_SCHEMA,
        "authority": AUTHORITY,
        "this_is": CATALOGUE_THIS_IS,
        "registry_status": "STALE",
        "generated_at": carried.get("generated_at"),
        "source_revision": carried.get("source_revision"),
        "entries": carried.get("entries") or [],
        "last_attempt_at": _iso(now),
        "unreachable_reason": reason,
        "note": (
            "the authority could not be read. The entries below are the last "
            "known ones and are carried through unchanged; `generated_at` is "
            "when they were last SUCCESSFULLY read, not when this file was "
            "written. This is the catalogue, not an installation record: it "
            "names no device, and do not install from it (§28)."
        ),
    }


def _iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).isoformat()


def write_atomic(path: Path, document: dict) -> None:
    """Replace the projection in one step.

    A projection is read by a different process than the one writing it, so a
    torn write is not hypothetical: a reader that opens the file mid-write sees
    truncated JSON, and `_capability_view` correctly reports that as UNREADABLE
    — a real outage fabricated by a writer that was working fine. `os.replace`
    is atomic within a filesystem, and the temporary file is created in the
    destination directory so the two are never on different ones.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".effective-", suffix=".tmp")
    try:
        with os.fdopen(handle, "w") as stream:
            json.dump(document, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def read_existing(path: Path) -> dict | None:
    try:
        document = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def stale(previous: dict | None, device_ids: tuple[str, ...], *, reason: str,
          now: datetime) -> dict:
    """The projection to write when the authority could not be read.

    `generated_at` and `abilities` are carried through UNCHANGED, and that is
    the whole design. The reader computes staleness from `generated_at`, so
    restamping it here would make a projection that has not been refreshed in
    three days look three seconds old. The age is the information; the failure
    is recorded in fields that do not feed it.
    """
    carried = previous or {}
    return {
        "schema": SCHEMA,
        "authority": AUTHORITY,
        "this_is": THIS_IS,
        "registry_status": "STALE",
        "generated_at": carried.get("generated_at"),
        "source_revision": carried.get("source_revision"),
        "device_ids_matched": list(device_ids),
        "abilities": carried.get("abilities") or [],
        "last_attempt_at": _iso(now),
        "unreachable_reason": reason,
        "note": (
            "the authority could not be read. The abilities below are the last "
            "known ones and are carried through unchanged; `generated_at` is "
            "when they were last SUCCESSFULLY read, not when this file was "
            "written. Usable for bootstrap, not an installation authority — do "
            "not install from it (§28)."
        ),
    }


def refresh(repo: Path, rev: str, device_ids: tuple[str, ...], state_root: Path,
            *, allow_empty: bool = False, now: datetime | None = None) -> dict:
    """Read the authority and write BOTH artifacts. Returns the projection.

    One read, one pinned revision, two files. They share `source_revision` and
    `generated_at` whenever both are FRESH, so a reader can notice the two
    drifting apart — which is what a half-completed write would look like, and
    is worth being visible rather than silent.

    The projection is returned because every caller so far wants it and the
    catalogue is additive. That is a convenience, not a ranking: neither file
    is derived from the other.

    Two ways to end without writing, and they are not the same way:

      * `AuthorityUnreachable` — the authority could not be READ. The previous
        abilities are carried through under `STALE`, which is writing a
        different thing rather than failing to write.
      * `NoInstallationMatched` — the authority was read and none of
        `device_ids` names an installation in it. Nothing is written at all,
        including the catalogue: the two files are written from one read so
        that a half-completed write shows up as the two drifting apart, and a
        fresh catalogue beside an untouched projection would be that drift.
        `allow_empty` is how a caller states that the empty answer was the
        question being asked.

    The ids are matched in the vocabulary the authority speaks, so a leading
    `device:` is stripped first and recorded in its stripped form.
    """
    moment = now or datetime.now(timezone.utc)
    asked = tuple(str(i) for i in device_ids)
    ids = bare_device_ids(asked)
    directory = state_root / "abilities"
    path = directory / PROJECTION_NAME
    catalogue_path = directory / CATALOGUE_NAME
    try:
        manifest, resolved = read_manifest(repo, rev)
    except AuthorityUnreachable as exc:
        document = stale(read_existing(path), ids, reason=str(exc), now=moment)
        write_atomic(path, document)
        write_atomic(catalogue_path,
                     catalogue_stale(read_existing(catalogue_path),
                                     reason=str(exc), now=moment))
        return document
    document = project(manifest, ids, resolved_sha=resolved, now=moment)
    if not document["abilities"] and not allow_empty:
        raise NoInstallationMatched(
            asked=asked, normalized=ids,
            manifest_device_ids=manifest_device_ids(manifest),
            records=len(manifest.get("abilities") or []))
    write_atomic(path, document)
    write_atomic(catalogue_path,
                 catalogue(manifest, resolved_sha=resolved, now=moment))
    return document


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ability-repo", type=Path, required=True,
                    help="path to the ability-management checkout to read from")
    ap.add_argument("--ability-rev", required=True,
                    help="pinned revision the manifest is read at")
    ap.add_argument("--device-id", action="append", required=True,
                    help="an id that means THIS node (canonical first, then "
                         "legacy aliases; either spelling, `x` or `device:x`); "
                         "repeatable")
    ap.add_argument("--allow-empty", action="store_true",
                    help="write an empty projection when no installation names "
                         "this device; off by default, because a zero match is "
                         "equally what a mistyped id looks like")
    ap.add_argument("--state-root", type=Path,
                    help="defaults to the contract's state root, not to cwd")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    state_root = args.state_root
    if state_root is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import paths as paths_mod  # noqa: PLC0415 - only needed on this path

        state_root = paths_mod.Paths().root("state")

    try:
        document = refresh(args.ability_repo, args.ability_rev, tuple(args.device_id),
                           state_root, allow_empty=args.allow_empty)
    except NoInstallationMatched as exc:
        # REFUSED, and nothing was written. `serverosctl ability refresh` turns
        # this into its own exit code; this entry point is a debugging one, so
        # the message is the whole of the answer.
        sys.stderr.write(f"REFUSED: {exc}\n")
        return 3
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    else:
        print(f"{document['registry_status']}: "
              f"{len(document['abilities'])} abilities installed on this device "
              f"({document.get('source_revision') or 'no revision'})")
        # Named separately, never summed with the line above. "N installed"
        # and "M in the catalogue" are different facts, and their difference is
        # the answer to "why can I not find the ability I know exists".
        written = read_existing(state_root / "abilities" / CATALOGUE_NAME) or {}
        entries = written.get("entries")
        print(f"catalogue: {written.get('registry_status') or 'MISSING'} · "
              f"{'unreadable' if entries is None else len(entries)} entries · "
              f"{written.get('source_revision') or 'no revision'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
