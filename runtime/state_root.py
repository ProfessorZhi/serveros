#!/usr/bin/env python3
"""state/serveros — namespace authority, and the projections that must not be it.

The one-sentence defect this module exists to make impossible
------------------------------------------------------------
"it is under `state/serveros`, so it is the truth."

That sentence is cheap, it is almost always right, and it is wrong in exactly
the places that matter. A generated view of the health directory is under
`state/serveros` and is not the health of anything. An external machine
baseline captured by a probe is under `state/serveros` and is not the machine.
A lock file is under `state/serveros` and is a claim by a holder, not a lease.

So authority is resolved per NAMESPACE here, from
`fleet/state-root-contract.yaml`, and the answer for a path under the
projections namespace is `PROJECTION` — a class that is explicitly NOT an
authority, whatever directory it is nested in.

The other half is what a checker is allowed to do about an absence. There are
three shapes and only one of them is a fault:

    PRESENT           a producer exists and has written
    EXPECTED_ABSENT   no producer exists yet; this is the correct state
    MISSING_REQUIRED  a producer is declared and its output is not there

The obvious implementation — `if not path.exists(): create it` — destroys the
distinction permanently, because afterwards all three look identical to every
later check. This module is read-only, and so is `conformance.py`: neither
creates a directory, ever. A namespace appears when a producer writes it.

Read-only by construction: the only filesystem calls below are `exists`,
`is_dir`, `iterdir` and `read_text`.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    yaml = None

_HERE = Path(__file__).resolve().parent
CONTRACT_RELATIVE = "fleet/state-root-contract.yaml"

SCHEMA = "serveros/state-root-report.v1"

# The four classes a path under the state root can have. `PROJECTION` and
# `NON_AUTHORITY` are both "admitted, and not the truth"; they are kept apart
# because a lock and a derived view fail differently.
AUTHORITY = "AUTHORITY"
PROJECTION = "PROJECTION"
NON_AUTHORITY = "NON_AUTHORITY"
FOREIGN = "FOREIGN"

# Namespace states. `UNDECLARED_NAMESPACE` is not an error either: it is a
# directory that exists and no contract claims, which is a finding about the
# contract rather than about the directory.
PRESENT = "PRESENT"
EXPECTED_ABSENT = "EXPECTED_ABSENT"
MISSING_REQUIRED = "MISSING_REQUIRED"
UNDECLARED_NAMESPACE = "UNDECLARED_NAMESPACE"

MANIFEST_NAME = "projection.yaml"


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ContractUnreadable(Exception):
    """The state-root contract could not be read.

    Raised rather than returning `{}`. An authority that could not be read must
    not present as an authority that permits nothing — and it must not present
    as one that permits everything either, which is what an empty namespace
    table produces when it is fed to a loop.
    """


def load_contract(*, repo_root: Path | None = None) -> dict:
    path = (repo_root or _HERE.parent) / CONTRACT_RELATIVE
    if yaml is None:  # pragma: no cover - environment guarantee
        raise ContractUnreadable("PyYAML is not available")
    try:
        document = yaml.safe_load(path.read_text())
    except OSError as exc:
        raise ContractUnreadable(f"{path} could not be read: {exc}") from exc
    except Exception as exc:  # noqa: BLE001 - an unparseable contract is a fact
        raise ContractUnreadable(f"{path} is not valid YAML: {exc}") from exc
    namespaces = (document or {}).get("namespaces")
    if not isinstance(namespaces, dict) or not namespaces:
        raise ContractUnreadable(f"{path} declares no namespaces")
    return document


def namespace_classes(contract: dict) -> dict[str, dict]:
    """Every namespace, flat, keyed by its path relative to the state root.

    Nested declarations (`runtime/attribution`) are flattened here rather than
    at each call site, so that a child namespace gets the same class handling
    as a top-level one. The alternative — looking up only the first path
    segment — would classify `runtime/anomalies` as whatever `runtime` is,
    which is the whole distinction this module is here to draw.
    """
    flat: dict[str, dict] = {}

    def walk(prefix: str, table: dict) -> None:
        for name, spec in table.items():
            spec = spec or {}
            ref = f"{prefix}/{name}" if prefix else name
            flat[ref] = {
                "ref": ref,
                "class": spec.get("class", "authority"),
                "authority": bool(spec.get("authority", spec.get("class") == "authority")),
                "producer": spec.get("producer"),
                "required": bool(spec.get("required", False)),
                "holds": spec.get("holds"),
                "absent_means": spec.get("absent_means"),
            }
            if isinstance(spec.get("children"), dict):
                walk(ref, spec["children"])

    walk("", contract.get("namespaces") or {})
    return flat


def classify(rel: str, *, contract: dict | None = None) -> dict:
    """What KIND of thing is at this path under the state root.

    Longest-prefix match, so `runtime/anomalies/current.json` resolves to the
    `runtime/anomalies` namespace and not to `runtime`. A shorter match would
    make the nested declaration decorative.
    """
    contract = contract if contract is not None else load_contract()
    flat = namespace_classes(contract)
    parts = [p for p in str(rel).split("/") if p]
    if not parts:
        return {"relative": "", "namespace": None, "class": AUTHORITY,
                "authority": True, "reason": "the state root itself is ServerOS-owned durable state"}

    for depth in range(len(parts), 0, -1):
        ref = "/".join(parts[:depth])
        spec = flat.get(ref)
        if spec is None:
            continue
        return {
            "relative": "/".join(parts),
            "namespace": ref,
            "class": spec["class"].upper(),
            "authority": spec["authority"],
            # Said in a sentence as well as a field, because the field is the
            # one a caller forgets to read.
            "reason": (
                f"{ref!r} is declared class {spec['class']}"
                + (" and IS an authority for what it holds"
                   if spec["authority"]
                   else " and is NOT an authority: what is here is derived, "
                        "or a claim, and not the thing it describes")
            ),
        }

    return {
        "relative": "/".join(parts),
        "namespace": None,
        "class": FOREIGN,
        "authority": False,
        "reason": (
            f"no declared namespace covers {parts[0]!r}. Being under the state "
            f"root does not make a path authoritative, and it does not make it "
            f"admitted either — this is a finding about the contract, not a "
            f"permission."
        ),
    }


def namespace_report(*, root: Path | None = None, contract: dict | None = None) -> dict:
    """The declared shape against the disk, with the three absences kept apart."""
    contract = contract if contract is not None else load_contract()
    root = Path(root) if root is not None else _default_root()
    flat = namespace_classes(contract)

    rows = []
    for ref, spec in sorted(flat.items()):
        # A namespace is "present" when its directory is there. Parents of a
        # present child are not inferred to be present — `runtime/` being
        # absent while `runtime/health` exists is a shape worth seeing.
        exists = (root / ref).is_dir()
        if exists:
            state = PRESENT
        elif spec["required"]:
            state = MISSING_REQUIRED
        else:
            state = EXPECTED_ABSENT
        rows.append({
            "namespace": ref,
            "class": spec["class"].upper(),
            "authority": spec["authority"],
            "state": state,
            "producer": spec["producer"],
            "required": spec["required"],
            "absent_means": spec["absent_means"],
            "note": _state_note(state, spec),
        })

    declared = {ref.split("/")[0] for ref in flat}
    preexisting = set((contract.get("preexisting") or {}).get("entries") or [])
    undeclared = []
    if root.is_dir():
        for child in sorted(root.iterdir()):
            if child.name not in declared:
                undeclared.append(child.name)
    for name in undeclared:
        known = name in preexisting
        rows.append({
            "namespace": name,
            "class": FOREIGN,
            "authority": False,
            "state": UNDECLARED_NAMESPACE,
            "producer": None,
            "required": False,
            "absent_means": None,
            "preexisting": known,
            "note": (
                "exists on disk, has no producer declaration, and IS listed in "
                "the contract's preexisting table. Its disposition is "
                "UNKNOWN_OWNER rather than undeclared: it has an owner who is "
                "not this contract."
                if known else
                "exists on disk and nothing — neither a namespace nor the "
                "preexisting table — claims it. Reported rather than admitted: "
                "a directory that appears without a declaration has an owner "
                "who is not in this contract."
            ),
        })

    return {
        "schema": SCHEMA,
        "root": str(root),
        "namespaces": rows,
        "counts": {
            "present": sum(1 for r in rows if r["state"] == PRESENT),
            "expected_absent": sum(1 for r in rows if r["state"] == EXPECTED_ABSENT),
            "missing_required": sum(1 for r in rows if r["state"] == MISSING_REQUIRED),
            "undeclared": len(undeclared),
        },
        "note": (
            "EXPECTED_ABSENT and MISSING_REQUIRED are different facts and are "
            "never collapsed. A run that created the first kind to make a "
            "report look complete would destroy the distinction permanently."
        ),
    }


def _state_note(state: str, spec: dict) -> str:
    if state == PRESENT:
        return f"present; holds {spec['holds']}" if spec.get("holds") else "present"
    if state == EXPECTED_ABSENT:
        means = f" Its absence is the defined state {spec['absent_means']}." if spec.get("absent_means") else ""
        return (
            f"no producer has written it yet, and nothing requires one to."
            f"{means} Declared so the name is reserved; NOT created so the "
            f"report looks complete."
        )
    return (
        f"the contract declares a producer ({spec['producer']}) and its output "
        f"is not here. This is the one absence that is a fault."
    )


def _default_root() -> Path:
    paths_mod = _load("serveros_runtime_paths", "paths.py")
    return paths_mod.Paths().root("state")


# ---------------------------------------------------------------------------
# Projections (§24)
# ---------------------------------------------------------------------------


def read_manifest(directory: Path) -> dict:
    """The manifest of one projection, and every way it can be untrue.

    A missing manifest is not "a projection with defaults". Defaults would be
    `authority: false, generated: true` for every directory that has no
    manifest — including directories that are not projections at all — and the
    manifest would then be decorative.
    """
    path = Path(directory) / MANIFEST_NAME
    problems: list[str] = []
    if not path.is_file():
        return {
            "present": False,
            "path": str(path),
            "manifest": None,
            "problems": [
                "no projection.yaml. A directory under projections/ without a "
                "manifest is undeclared material in a promising-looking place, "
                "and it is the one thing here that could be mistaken for an "
                "authority, because nothing says otherwise."
            ],
            "authority": None,
        }
    try:
        document = yaml.safe_load(path.read_text()) if yaml else None
    except Exception as exc:  # noqa: BLE001 - an unparseable manifest is a fact
        return {"present": True, "path": str(path), "manifest": None,
                "problems": [f"projection.yaml is not valid YAML: {exc}"],
                "authority": None}
    if not isinstance(document, dict):
        return {"present": True, "path": str(path), "manifest": None,
                "problems": ["projection.yaml is not a mapping"],
                "authority": None}

    for field in ("authority", "generated", "generator", "generated_at", "source_refs"):
        if field not in document:
            problems.append(f"manifest declares no {field!r}")
    if document.get("authority") is not False:
        # Identity check against the literal False, not falsiness: the string
        # "false" is truthy, and a manifest that gets this wrong is claiming
        # authority in the one namespace where the claim is the thing being
        # denied.
        problems.append(
            f"authority must be the literal false; found {document.get('authority')!r}"
        )
    if document.get("generated") is not True:
        problems.append(
            f"generated must be the literal true; found {document.get('generated')!r}"
        )
    refs = document.get("source_refs")
    if refs is not None and not isinstance(refs, list):
        problems.append("source_refs must be a list")
    elif not refs:
        problems.append("source_refs is empty: a projection with no source is a source")
    else:
        for ref in refs:
            if not isinstance(ref, str):
                problems.append(f"source_ref of unexpected type {type(ref).__name__}")
            elif ref.strip().startswith("state/serveros/projections") or \
                    ref.strip().startswith("projections/"):
                problems.append(
                    f"source_ref {ref!r} points into the projections namespace "
                    f"itself. A projection derived from a projection is a chain "
                    f"with no authority at its root."
                )
    return {"present": True, "path": str(path), "manifest": document,
            "problems": problems, "authority": document.get("authority")}


def projection_inventory(*, root: Path | None = None) -> dict:
    """Every projection, with its manifest verdict. Read-only.

    A directory under `projections/` is a projection when it carries a
    manifest. When it does not, it may still be a GROUPING directory — one
    whose children are each a projection — which is how a kind with several
    dated instances is laid out (`projections/environment/2026-08-19/`).

    Grouping is permitted only when every child is a projection. A directory
    that neither carries a manifest nor holds projections is undeclared
    material in a namespace whose entire meaning is "this is derived and
    labelled" — and it is the only thing here that could be mistaken for an
    authority, because nothing says otherwise.
    """
    root = Path(root) if root is not None else _default_root()
    namespace = root / "projections"
    projections, unlabelled = [], []
    if namespace.is_dir():
        for child in sorted(namespace.iterdir()):
            if not child.is_dir():
                continue
            if (child / MANIFEST_NAME).is_file():
                projections.append({"ref": f"projections/{child.name}", **read_manifest(child)})
                continue
            children = [g for g in sorted(child.iterdir()) if g.is_dir()]
            if children and all((g / MANIFEST_NAME).is_file() for g in children):
                for grandchild in children:
                    projections.append({
                        "ref": f"projections/{child.name}/{grandchild.name}",
                        "grouping": f"projections/{child.name}",
                        **read_manifest(grandchild),
                    })
                continue
            unlabelled.append({
                "ref": f"projections/{child.name}",
                "why": (
                    "neither carries a projection.yaml nor holds only "
                    "projections. It is not a projection and cannot be read as "
                    "one, and it is not deleted by this module either."
                ),
            })
    return {
        "schema": SCHEMA,
        "namespace": str(namespace),
        "exists": namespace.is_dir(),
        "projections": projections,
        "unlabelled": unlabelled,
        "count": len(projections),
        "note": (
            "an empty namespace is the correct state before any projection is "
            "written. It is NOT created by this module."
        ),
    }


def authority_conflicts(*, root: Path | None = None, contract: dict | None = None) -> list[dict]:
    """One fact declared authoritative twice — the projections namespace wins.

    Detected structurally rather than by comparing values: a projection whose
    relative path mirrors an authority namespace (`projections/runtime/health/
    current.json` against `runtime/health/current.json`) is the shape that
    becomes a second authority. Mere agreement of VALUES is not a conflict, and
    a check that flagged it would flag every correct projection and be switched
    off within a week.
    """
    conformance = _load("serveros_runtime_conformance", "conformance.py")
    contract = contract if contract is not None else load_contract()
    root = Path(root) if root is not None else _default_root()
    namespace = root / "projections"
    flat = namespace_classes(contract)

    rows: list[dict] = []
    if not namespace.is_dir():
        return rows
    for child in sorted(namespace.iterdir()):
        if not child.is_dir():
            continue
        # `projections/environment/2026-08-19` has no authority counterpart.
        # `projections/runtime/health` would.
        #
        # The depth matters and it is not the top segment. `projections/runtime`
        # mirrors the authority namespace `runtime` on its own, so stopping there
        # would report the coarser namespace for every conflict beneath it and
        # name the wrong thing in the one message a reader gets. The subtree is
        # walked to the deepest declared authority namespace it mirrors, and the
        # result is ONE finding per top-level entry: `projections/runtime` and
        # `projections/runtime/health` are the same conflict, and reporting both
        # would inflate the count of a problem that has one cause.
        mirror = _deepest_mirror(child, namespace, flat)
        if mirror is not None:
            rows.append(conformance.finding(
                "AUTHORITY_CONFLICT", "state/serveros",
                f"nothing under projections/ mirrors the authoritative {mirror!r}",
                f"projections/{mirror} mirrors the AUTHORITY namespace {mirror!r}",
                CONTRACT_RELATIVE,
                "HIGH", "OPEN",
                projection=f"projections/{mirror}",
                authority_namespace=mirror,
                enforcement_reason=(
                    "a projection that mirrors an authority path is the second "
                    "answer to one question, and the second answer wins because "
                    "it is easier to read. Phase-1 warns; the check exists now "
                    "so the first real one is caught by a check that has always "
                    "been on."
                ),
            ))
    return rows


def _deepest_mirror(child: Path, namespace: Path, flat: dict) -> str | None:
    """The deepest declared AUTHORITY namespace this projection subtree mirrors.

    Walks the whole subtree because the interesting mirror is usually one level
    below the kind directory: for `projections/runtime/health/current.json` the
    answer is `runtime/health`, and a check that stopped at the top segment
    would name `runtime` — technically true, and the wrong thing to print in the
    one message a reader gets about this conflict.

    Returns None when nothing in the subtree is an authority, which is the
    common and correct case: `projections/environment/2026-08-19` has no
    authority counterpart, and a check that flagged it would flag every correct
    projection and be switched off within a week.
    """
    best: str | None = None
    for entry in (child, *sorted(child.rglob("*"))):
        ref = "/".join(entry.relative_to(namespace).parts)
        for depth in range(1, len(ref.split("/")) + 1):
            candidate = "/".join(ref.split("/")[:depth])
            spec = flat.get(candidate)
            if spec is not None and spec["authority"]:
                # Longest match wins, so `runtime/health` beats `runtime`.
                if best is None or len(candidate) > len(best):
                    best = candidate
    return best


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path)
    ap.add_argument("--repo-root", type=Path)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    try:
        contract = load_contract(repo_root=args.repo_root)
    except ContractUnreadable as exc:
        print(f"could not answer: {exc}", file=sys.stderr)
        return 2

    report = namespace_report(root=args.root, contract=contract)
    report["projections"] = projection_inventory(root=args.root)
    report["authority_conflicts"] = authority_conflicts(root=args.root, contract=contract)

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
        return 0

    c = report["counts"]
    print(f"state root: {report['root']}")
    print(f"namespaces: {c['present']} present, {c['expected_absent']} expected-absent, "
          f"{c['missing_required']} missing-required, {c['undeclared']} undeclared")
    for row in report["namespaces"]:
        mark = "*" if row["authority"] else " "
        print(f"  [{row['state']:<20}]{mark} {row['namespace']:<20} {row['class']}")
    for row in report["authority_conflicts"]:
        print(f"  [{row['severity']} {row['enforcement']}] {row['id']}: {row['observed']}")
    print("  (* = authoritative namespace; unmarked are admitted and NOT "
          "authoritative)")
    # Exit is driven by the derived `blocking`, never by severity.
    return 1 if any(r["blocking"] for r in report["authority_conflicts"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
