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

    `device_ids` is a set rather than one id on purpose. The manifest's
    `installations[].device_id` currently reads `linux-server`, which is this
    device's HISTORICAL id — `fleet/registry.yaml` keeps it in `legacy_ids`
    precisely so that earlier installation and revision evidence keeps
    resolving after the rename. Matching only the canonical id would report
    zero installed abilities with no error at all, which is exactly the
    declared-but-absent shape this project keeps finding: a check that returns
    the empty set rather than a failure.

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
       defaulted into a node.** 38 of this catalogue's 55 entries are in that
       state, and a default would put them somewhere plausible and wrong. §4 is
       explicit that the tree is generated from the real catalogue: an
       unclassified ability has no place because nobody has said where it goes,
       which is a fact about the catalogue and not a hole in this function.

    Returns nodes, placements, unplaced and cross_refs. It does not render, and
    it carries no display order: how a tree is drawn is the authority's
    presentation, and this node's job is to say where things are.
    """
    nodes: set[str] = set()
    placements: dict[str, str] = {}
    unplaced: list[str] = []
    cross_refs: dict[str, list[str]] = {}

    for row in rows or ():
        primary = row.get("primary")
        name = str(row.get("name"))
        if not primary:
            unplaced.append(name)
            continue
        segments = str(primary).split(".")
        # Every prefix is a node, including the single-segment root: the
        # authority's own renderer prints that root, and a tree whose top level
        # vanished would put every ability one level too high.
        for depth in range(1, len(segments) + 1):
            nodes.add(".".join(segments[:depth]))
        placements[name] = str(primary)

        # A reference to an ability's own node is not a cross-reference; it is
        # the placement, repeated. Only classified rows can carry one: an
        # unplaced ability has no home for a second path to be an alternative to.
        extra = [str(ref) for ref in (row.get("refs") or ()) if ref != primary]
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
            *, now: datetime | None = None) -> dict:
    """Read the authority and write the projection. Returns what was written."""
    moment = now or datetime.now(timezone.utc)
    path = state_root / "abilities" / PROJECTION_NAME
    try:
        manifest, resolved = read_manifest(repo, rev)
    except AuthorityUnreachable as exc:
        document = stale(read_existing(path), device_ids, reason=str(exc), now=moment)
        write_atomic(path, document)
        return document
    document = project(manifest, device_ids, resolved_sha=resolved, now=moment)
    write_atomic(path, document)
    return document


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--ability-repo", type=Path, required=True,
                    help="path to the ability-management checkout to read from")
    ap.add_argument("--ability-rev", required=True,
                    help="pinned revision the manifest is read at")
    ap.add_argument("--device-id", action="append", required=True,
                    help="an id that means THIS node (canonical first, then "
                         "legacy aliases); repeatable")
    ap.add_argument("--state-root", type=Path,
                    help="defaults to the contract's state root, not to cwd")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    state_root = args.state_root
    if state_root is None:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import paths as paths_mod  # noqa: PLC0415 - only needed on this path

        state_root = paths_mod.Paths().root("state")

    document = refresh(args.ability_repo, args.ability_rev, tuple(args.device_id),
                       state_root)
    if args.json:
        print(json.dumps(document, ensure_ascii=False, indent=2))
    else:
        print(f"{document['registry_status']}: "
              f"{len(document['abilities'])} abilities installed on this device "
              f"({document.get('source_revision') or 'no revision'})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
