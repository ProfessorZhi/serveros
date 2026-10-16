#!/usr/bin/env python3
"""Pre-generate the release staging tree and a secret-free manifest.

The builder cannot write a release while two inputs are missing: the two
approved PNGs and an observed Next URL. This module repairs neither and invents
neither. It writes the part of the release that is already known — the staging
tree of source members — and a manifest that names every member the release
will carry, with the digest of each one that exists and the *pinned* digest of
each one that does not.

**It writes no archive, and it must not.** A ZIP is the one artifact that gets
installed, and a ZIP assembled from a tree that cannot bind a server is the
output the builder exists to prevent. The output here is a directory named
`staging/` and a file named `<name>-v<version>-STAGING-MANIFEST.json`; a name
that says "staging" is what keeps it from being read as a release.

A run is **one-shot**. If *either* output path already exists it refuses before
writing either, so a second run can never blend into a first one's evidence or
overwrite a manifest left by an incomplete first one — and `--out` at or below
`--source` is refused outright, because a tree created inside the audited source
changes what the next run audits and copies.

The gates are the builder's own, called through and not re-implemented:
`audit_source`, `load_manifest`, `check_skills` and `check_presentation` are
run as-is, and the two blocking gates (`check_assets`, `check_endpoint`) are
run to *learn why they block*. Their refusal is recorded in the manifest rather
than swallowed, and nothing tries to repair it — a staging run that could paper
over a blocker would be a second, quieter release path.

`mcp.json` is never staged. It is generated from an observed URL, and there is
no observed URL while the endpoint gate is blocked, so a file written here
would be a fabricated binding by another name.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import build_first_install as builder  # noqa: E402  (same package, loaded by path)

STAGING_SCHEMA = "serveros/fleet-v1-first-install-staging.v1"
STAGING_DIR_NAME = "staging"
MANIFEST_SUFFIX = "-STAGING-MANIFEST.json"

#: Never a source member: the binding is generated from the observed URL.
GENERATED_MEMBER = "mcp.json"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _verdict(gate, *args, **kwargs):
    """Run a gate for its answer. Returns (value, None, None) or (None, status, detail)."""
    try:
        return gate(*args, **kwargs), None, None
    except builder.Refused as refusal:
        return None, refusal.status, refusal.detail


def staging_manifest(root: Path, lock: Path, *, next_mcp_url=None,
                     observation=None, v1_mcp_url=None) -> dict:
    """Describe the release without building one.

    The tree is audited first, for the builder's own reason: a credential must
    be refused before anything else is read, and a staging tree is a copy that
    leaves this machine exactly as an archive does.
    """
    root = Path(root).resolve()
    lock_doc = builder._read_json(Path(lock).resolve(), "brand-assets.lock.json")

    audited = builder.audit_source(root)
    manifest = builder.load_manifest(root)
    skills = builder.check_skills(root)
    presentation = builder.check_presentation(manifest, lock_doc)

    assets, asset_status, asset_detail = _verdict(builder.check_assets, root, lock_doc)
    url, endpoint_status, endpoint_detail = _verdict(
        builder.check_endpoint, next_mcp_url, observation, v1_mcp_url=v1_mcp_url)

    present: list[dict] = []
    absent: list[str] = []
    for entry in builder.PAYLOAD_ENTRIES:
        if entry == GENERATED_MEMBER:
            continue
        path = root / entry
        if path.is_file():
            present.append({"member": entry, "sha256": _sha256(path)})
        elif path.is_dir():
            for child in sorted(path.rglob("*")):
                if child.is_file():
                    present.append({"member": child.relative_to(root).as_posix(),
                                    "sha256": _sha256(child)})
        else:
            absent.append(entry)

    blockers: list[dict] = []
    if asset_status:
        blockers.append({"gate": "assets", "status": asset_status,
                         "detail": asset_detail})
    if endpoint_status:
        blockers.append({"gate": "endpoint", "status": endpoint_status,
                         "detail": endpoint_detail})
    if not blockers:
        # Every gate passed, so this is a release build and not a staging run.
        # Saying so beats emitting a "staging" tree for something installable:
        # the two outputs differ, and the operator wants the other one.
        blockers.append({"gate": "builder", "status": builder.READY_TO_BUILD,
                         "detail": "every gate passed — build the release with "
                                   "build_first_install.py; there is nothing left "
                                   "to stage"})

    return {
        "schema": STAGING_SCHEMA,
        "distribution": "SOURCE_STAGED_NOT_A_RELEASE",
        "installable": False,
        "name": manifest["name"],
        "version": manifest["version"],
        "skills": skills,
        "present_members": present,
        "absent_members": absent,
        "presentation": presentation,
        "pinned_assets": [{"slot": slot["slot"],
                           "shipped_path": slot["shipped_path"],
                           "expected_sha256": slot["expected_sha256"]}
                          for slot in lock_doc.get("slots", [])],
        "asset_readback": assets,
        "mcp_url": url,
        "source_members": audited,
        "blockers": blockers,
        # The same honesty the release record carries: a URL typed here is a
        # claim, and this tool has no network to check it against.
        "endpoint_evidence": "OPERATOR_SUPPLIED_RECEIPT" if url else None,
        "writes": [],
        "uploads": [],
        "installs": [],
        "creates_plugin": False,
        "warning": ("A staging tree is not a release and cannot become one by "
                    "being zipped: it carries no `mcp.json`, and no archive is "
                    "written here. Nothing in this record uploads, registers, "
                    "installs or authorises."),
    }


def stage(root: Path, out_dir: Path, lock: Path, **kwargs) -> dict:
    """Write `<out>/staging/` and `<out>/<name>-v<version>-STAGING-MANIFEST.json`.

    Both arguments are refused before anything is read or written, because each
    of the two mistakes makes the *next* run disagree with this one:

    - an `--out` at or below `--source` puts the staging tree inside the tree
      the audit reads. The audit sees the source as it is *now*, so a directory
      created under it afterwards is one the next run audits and copies: the
      source has been changed by describing it;
    - either output path already existing means a previous run left evidence
      here. Overwriting it destroys a record and blends two runs into a tree
      that matches neither manifest.

    The second is checked on **both** paths. A directory-only check misses the
    incomplete run — the one that wrote its manifest and no tree, or the reverse
    — and that is the run whose bytes are most tempting to write over.
    """
    root = Path(root).resolve()
    out_dir = Path(out_dir).resolve()

    # Both paths are already real paths, so a symlink aimed at the source gives
    # the same answer as naming the source: this compares targets, not spellings.
    if out_dir == root or root in out_dir.parents:
        raise builder.Refused(
            builder.SOURCE_REFUSED,
            f"--out {out_dir} is the source {root}, or is inside it. The audit "
            f"reads the source before any output is written, so a staging tree "
            f"created under it changes what the next run audits and copies. Keep "
            f"the output in a separate evidence directory.",
        )

    record = staging_manifest(root, lock, **kwargs)

    stage_dir = out_dir / STAGING_DIR_NAME
    manifest_path = out_dir / f"{record['name']}-v{record['version']}{MANIFEST_SUFFIX}"
    existing = [path for path in (stage_dir, manifest_path) if path.exists()]
    if existing:
        raise builder.Refused(
            builder.SOURCE_REFUSED,
            f"{existing[0]} already exists. A staging run writes once into an empty "
            f"directory and never over an earlier run's evidence; remove it "
            f"deliberately rather than letting a second run blend into it.",
        )
    stage_dir.mkdir(parents=True)

    for entry in builder.PAYLOAD_ENTRIES:
        if entry == GENERATED_MEMBER:
            continue
        source = root / entry
        if source.is_file():
            (stage_dir / entry).write_bytes(source.read_bytes())
        elif source.is_dir():
            for child in sorted(source.rglob("*")):
                if not child.is_file():
                    continue
                dest = stage_dir / child.relative_to(root)
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(child.read_bytes())

    manifest_path.write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    record["staging_dir"] = STAGING_DIR_NAME
    record["manifest_file"] = manifest_path.name
    return record


def main(argv: list[str] | None = None) -> int:
    here = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--source", default=str(here / "plugin"))
    parser.add_argument("--lock", default=str(here / "brand-assets.lock.json"))
    parser.add_argument("--out", default="dist")
    parser.add_argument("--next-mcp-url", default=None)
    parser.add_argument("--observation", default=None)
    parser.add_argument("--v1-mcp-url", default=None)
    args = parser.parse_args(argv)

    root = Path(args.source).resolve()
    try:
        record = stage(root, Path(args.out).resolve(), Path(args.lock).resolve(),
                       next_mcp_url=args.next_mcp_url, observation=args.observation,
                       v1_mcp_url=args.v1_mcp_url)
    except builder.Refused as refusal:
        print(f"{refusal.status}: {refusal.detail}", file=sys.stderr)
        return 2

    print(f"{record['name']} v{record['version']} STAGED (not a release)")
    print(f"staging_dir={record['staging_dir']} manifest={record['manifest_file']}")
    print(f"present={len(record['present_members'])} absent={record['absent_members']} "
          f"skills={len(record['skills'])}")
    for blocker in record["blockers"]:
        print(f"blocked[{blocker['gate']}]={blocker['status']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
