#!/usr/bin/env python3
"""Directory conformance — the check the path contract never had (§31–§42).

Why this exists
---------------
`paths.py` declares four closed vocabularies — DATA_ROOT's top level (§31),
WORKSPACE_ROOT's (§32), `state/serveros`'s (§35) and the artifact namespaces
(§34) — and `admit()` refuses a path outside them. What nothing did was compare
those declarations against the directories that are actually there. The only
test that touched the vocabulary asserted `workspace_top_level == list(
WORKSPACE_TOP_LEVEL)`, which is the declaration compared with itself.

A contract with no enforcement does not fail. It agrees with itself, in every
run, forever, while the disk drifts. This project has now found that shape
three times: the `A1` comment that disagreed with its own code, this module's
subject, and the `~/.serveros` allow-list that refused a write to `node.yaml`
while a stale copy of the same list stood in for the authority.

Three properties, and each is here because the obvious implementation is wrong:

1. READ-ONLY, and it never creates a directory. A checker that "fixes" an
   absent §35 directory by creating it turns a finding into a fact about
   nothing: an empty `state/serveros/projections/` is indistinguishable from a
   populated one to every later check, and §42's mechanics put migration behind
   an owner check rather than in front of it.

2. An undeclared entry is reported, never inferred. Several entries under
   `state/serveros/` are named as canonical SSOT by other documents in this
   repository. That makes them a *conflict between declarations*, which is a
   finding about the contract — not a licence for this module to widen the
   contract or to move the directory. Both of those are decisions with owners.

3. The exception table is explicit and every entry is REPORTED. Each exemption
   carries its class, the authority that justifies it and a proposed
   disposition, and each one is emitted at `EXEMPT_ENTRY`/OBSERVED. A table
   that silently absorbs entries is how a vocabulary stops being closed.

Read-only by construction: the only filesystem calls below are `iterdir` and
`is_dir`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    yaml = None

SCHEMA = "serveros/directory-conformance.v1"

# The authority for `~/.serveros/`, transcribed into `paths.py`. Named here so
# the comparison below has something to compare *against* rather than a second
# hand-written list — see `_declaration_conflicts`.
NODE_ROOT_CONTRACT = "fleet/node-root-contract.yaml"


# ---------------------------------------------------------------------------
# The exception table.
#
# Every entry is (class, reason, authority, disposition). `disposition` uses
# §42's vocabulary, because that is the decision each of these is waiting on —
# none of them is "this is fine, stop looking":
#
#   KEEP            correct where it is; the vocabulary should not claim it
#   FROZEN          §3-protected; not a candidate for anything
#   CONTRACT_GAP    genuinely canonical and named by another document; the
#                   vocabulary is behind reality and the fix is a ruling that
#                   widens it, not a move
#   OWNER_CHECK     produced by another domain under this root; whether it
#                   belongs here is §41's boundary question
#   UNKNOWN_OWNER   nothing on this node says who writes it. Defer: the standing
#                   rule is that an unknown owner is never guessed from a name.
# ---------------------------------------------------------------------------

EXCEPTIONS: dict[str, dict[str, dict]] = {
    "artifacts": {
        namespace: {
            "class": "PREEXISTING_NAMESPACE",
            "reason": f"`{namespace}` predates the two-namespace rule and holds "
                      f"artifacts that were written before it",
            "authority": "§34 constrains where NEW writes go; it does not "
                         "retroactively relocate what is already there",
            "disposition": "CONTRACT_GAP",
        }
        for namespace in ("builds", "e2e", "exports", "packages")
    },
    "data": {
        ".vscode-server": {
            "class": "FOREIGN_TOOL",
            "reason": "editor remote-server state; created and owned by VS Code, "
                      "not by any ServerOS producer",
            "authority": "not named by any contract in this repository",
            "disposition": "KEEP",
        },
    },
    "workspace": {
        "CLAUDE.md": {
            "class": "ENTRY_DOCUMENT",
            "reason": "the short entry point for this workspace; it deliberately "
                      "does not restate the directory map",
            "authority": "workspace/CLAUDE.md itself, and §32's shape describes "
                         "directories rather than the entry documents beside them",
            "disposition": "KEEP",
        },
        "README.md": {
            "class": "ENTRY_DOCUMENT",
            "reason": "workspace-level readme",
            "authority": "as above",
            "disposition": "KEEP",
        },
        "lit0104-build-evidence": {
            "class": "PROTECTED",
            "reason": "build evidence for a protected parallel lane",
            "authority": "§3 — named as a resource that must not be touched",
            "disposition": "FROZEN",
        },
    },
    "state": {
        "README.md": {
            "class": "ENTRY_DOCUMENT",
            "reason": "declares the state vocabulary in prose (`work-items/`, "
                      "`resource-leases/`) and is itself the second declaration "
                      "this check compares against",
            "authority": "state/serveros/README.md",
            "disposition": "KEEP",
        },
        "serveros.db": {
            "class": "CITED_CANONICAL",
            "reason": "the control plane's Item/Activity/Run/lease current state; "
                      "a live reader opens it",
            "authority": "ARCHITECTURE.md — control plane state; read by "
                         "context/resolver.py",
            "disposition": "CONTRACT_GAP",
        },
        "serveros.db-shm": {
            "class": "CITED_CANONICAL",
            "reason": "SQLite WAL sidecar of `serveros.db`; arrives and departs "
                      "with the database, so it is not independently governable",
            "authority": "as `serveros.db`",
            "disposition": "CONTRACT_GAP",
        },
        "serveros.db-wal": {
            "class": "CITED_CANONICAL",
            "reason": "SQLite WAL sidecar of `serveros.db`",
            "authority": "as `serveros.db`",
            "disposition": "CONTRACT_GAP",
        },
        "federation": {
            "class": "CITED_CANONICAL",
            "reason": "locator-only observed projections for external domains "
                      "(feishu, github)",
            "authority": "HWZ-MAP.md and FEISHU-MAP.md name "
                         "`federation/<domain>/current.jsonl` as the L3 observed "
                         "SSOT; GITHUB-MAP.md likewise",
            "disposition": "CONTRACT_GAP",
        },
        "boards": {
            "class": "DERIVED_REGENERABLE",
            "reason": "derived Board current view; regenerated wholesale by "
                      "`serverosctl board render`, never authoritative",
            "authority": "HWZ-MAP.md — derived, rebuildable, not the state DB",
            "disposition": "CONTRACT_GAP",
        },
        "work-items": {
            "class": "CITED_CANONICAL",
            "reason": "runtime stage / liveness for one Work Item, explicitly "
                      "not stored in Git",
            "authority": "state/serveros/README.md and DEV-OS-SPEC name "
                         "`work-items/<project>/<WI>.json` as the work-state "
                         "authority",
            "disposition": "CONTRACT_GAP",
        },
        "resource-leases": {
            "class": "CITED_CANONICAL",
            "reason": "current resource occupancy; empty at the time of this "
                      "observation, which is a state and not an absence",
            "authority": "state/serveros/README.md; ops/workspace/serveros/"
                         "resources.yaml reads lease state from here",
            "disposition": "CONTRACT_GAP",
        },
        "environment": {
            "class": "OWNER_CHECK",
            "reason": "dated environment captures written by another domain's "
                      "producer under this root",
            "authority": "ops/inventory files record generated_by "
                         "= workspace-management",
            "disposition": "OWNER_CHECK",
        },
        "migrations": {
            "class": "UNKNOWN_OWNER",
            "reason": "holds a single dated directory; nothing on this node "
                      "declares who writes it or what reads it",
            "authority": "none found — referenced only by the inventory scan",
            "disposition": "UNKNOWN_OWNER",
        },
        "document-stewardship": {
            "class": "UNKNOWN_OWNER",
            "reason": "holds a single per-project JSON document; no declaration "
                      "of producer or consumer",
            "authority": "none found — referenced only by the inventory scan",
            "disposition": "UNKNOWN_OWNER",
        },
    },
}


def finding(fid, cls, desired, observed, authority, severity, status="OPEN", **extra) -> dict:
    row = {
        "id": fid,
        "class": cls,
        "desired": desired,
        "observed": observed,
        "authority": authority,
        "severity": severity,
        "status": status,
    }
    row.update(extra)
    return row


# ---------------------------------------------------------------------------
# the surfaces
#
# Each surface is: an area name (which selects the exception table), the
# declared vocabulary, the directory it is checked against, and what to call a
# child — areas hold directories, the `~/.serveros` root holds files.
# ---------------------------------------------------------------------------


def _children(directory: Path) -> list[str]:
    """Immediate child names, sorted. A missing directory is an empty one.

    Deliberately not a recursive walk and deliberately not a mkdir: this module
    reports the shape of the disk, and the shape of a directory that is not
    there is "nothing", not "create me".
    """
    if not directory.is_dir():
        return []
    return sorted(p.name for p in directory.iterdir())


def _surface(name: str, area: str, directory: Path, vocabulary) -> dict:
    """A labeled directory and the closed set of children it is allowed to hold.

    `area` is the exception table's key, and it is passed rather than derived
    from `name`: the label is for a human reading a report, the area is for
    this module's lookup, and a lookup key derived from a display string is one
    rename away from silently finding no exceptions at all.
    """
    return {"surface": name, "area": area, "vocabulary": tuple(vocabulary),
            "directory": directory}


def surfaces(paths, home: str) -> list[dict]:
    """Every closed vocabulary, paired with the directory it governs.

    The vocabulary comes from `paths.vocabulary(area)` rather than from a list
    written here. A checker that carried its own copy of the vocabulary would
    report on the copy, and the copy would drift from the contract exactly the
    way the `~/.serveros` allow-list drifted from its authority.
    """
    state = paths.root("state")
    return [
        _surface("DATA_ROOT", "data", paths.root("data"), paths.vocabulary("data")),
        _surface("WORKSPACE_ROOT", "workspace", paths.root("workspace"),
                 paths.vocabulary("workspace")),
        _surface("state/serveros", "state", state, paths.vocabulary("state")),
        _surface("state/serveros/runtime", "state", state / "runtime",
                 paths.vocabulary("runtime")),
        _surface("ARTIFACTS_ROOT", "artifacts", paths.root("artifacts"),
                 paths.vocabulary("artifacts")),
        _surface("~/.serveros", "home", Path(home) / ".serveros",
                 paths.vocabulary("home")),
    ]


def compare(surface: dict, exceptions: dict[str, dict],
            absent_means: dict[str, str] | None = None) -> list[dict]:
    """One surface, both directions.

    Both directions matter and they are not symmetric. An entry on disk that no
    vocabulary declares is the drift a closed list exists to catch. An entry the
    vocabulary declares that is not on disk is the *declared-but-absent* shape —
    quieter, and worse, because `§35` naming a directory that was never created
    reads as "this area exists" to every consumer that trusts the contract.

    `absent_means` carries the vocabulary's own statement of what a missing
    entry means, where it makes one. `~/.serveros/controller.yaml` is the case
    that matters: its absence is `REMOTE_CONTROLLER_NOT_EXPOSED`, a defined
    state, and `fleet/README.md` warns that rendering it as an error is how a
    caller ends up reporting an empty fleet. A checker that cannot tell a
    defined absence from an unfinished one will eventually be read as saying
    the second when it means the first.
    """
    rows = []
    name = surface["surface"]
    vocabulary = set(surface["vocabulary"])
    present = set(_children(surface["directory"]))

    for entry in sorted(present - vocabulary):
        exempt = exceptions.get(entry)
        if exempt is None:
            rows.append(finding(
                "UNDECLARED_ENTRY", name,
                f"a child of {name} that is one of {sorted(vocabulary)}",
                f"{entry!r} exists and no declaration claims it",
                f"{name}'s closed vocabulary",
                "MEDIUM", "OPEN", entry=entry))
        else:
            rows.append(finding(
                "EXEMPT_ENTRY", name,
                f"a child of {name} that is one of {sorted(vocabulary)}",
                f"{entry!r} exists and is exempt as {exempt['class']}",
                exempt["authority"],
                "LOW" if exempt["disposition"] == "KEEP" else "MEDIUM",
                "OBSERVED", entry=entry, exemption_class=exempt["class"],
                disposition=exempt["disposition"], reason=exempt["reason"]))

    for entry in sorted(vocabulary - present):
        means = (absent_means or {}).get(entry)
        rows.append(finding(
            "DECLARED_ENTRY_ABSENT", name,
            f"{entry!r} exists under {name}",
            f"the contract declares {entry!r} and no such child exists"
            + (f"; absence is the defined state {means}" if means else ""),
            f"{name}'s closed vocabulary",
            "LOW", "OBSERVED", entry=entry,
            **({"absent_means": means} if means else {})))

    if not surface["directory"].is_dir():
        rows.append(finding(
            "SURFACE_ABSENT", name,
            f"{surface['directory']} is a directory",
            "the declared root does not exist",
            f"{name}'s closed vocabulary",
            "MEDIUM", "OPEN", entry=str(surface["directory"])))
    return rows


# ---------------------------------------------------------------------------
# declaration vs declaration
# ---------------------------------------------------------------------------


def node_root_contract(repo_root: Path) -> dict | None:
    """The `~/.serveros/` file contract, read from its authority.

    `None` when it is unreadable — which is NOT an empty dict, for the reason
    this whole project keeps rediscovering: an authority that could not be read
    must not present as an authority that forbids nothing.
    """
    path = repo_root / NODE_ROOT_CONTRACT
    if yaml is None or not path.is_file():
        return None
    try:
        document = yaml.safe_load(path.read_text())
    except Exception:  # noqa: BLE001 - an unparseable contract is a fact
        return None
    files = (document or {}).get("files")
    if not isinstance(files, dict):
        return None
    return {name: (spec or {}) for name, spec in files.items()}


def node_root_files(repo_root: Path) -> list[str] | None:
    contract = node_root_contract(repo_root)
    return None if contract is None else sorted(contract)


def declaration_conflicts(paths, repo_root: Path) -> list[dict]:
    """Where two authorities declare one vocabulary differently.

    `~/.serveros/` has two declarations: `fleet/node-root-contract.yaml`, which
    `fleet/node.py` reads and which names the files it opens, and the tuple
    `paths.py` carries so `admit()` can refuse a stray write. A transcription
    that disagrees with its source is not a style problem — `admit()` returned
    REFUSED for `~/.serveros/node.yaml`, a canonical path, while the test that
    covered the branch asserted `targets.yaml`, the one name both versions
    happened to contain. Testing the intersection of two disagreeing lists is
    how the disagreement survives.
    """
    authority = node_root_files(repo_root)
    if authority is None:
        return [finding(
            "DECLARATION_AUTHORITY_UNREADABLE", "~/.serveros",
            f"{NODE_ROOT_CONTRACT} is readable and declares the file vocabulary",
            f"{NODE_ROOT_CONTRACT} could not be read, so the transcription in "
            f"paths.py cannot be checked against anything",
            "fleet/node-root-contract.yaml",
            "MEDIUM", "OPEN",
            declared=list(paths.vocabulary("home")))]
    declared = sorted(paths.vocabulary("home"))
    if declared == authority:
        return []
    return [finding(
        "DECLARATION_CONFLICT", "~/.serveros",
        f"paths.py's allow-list equals {NODE_ROOT_CONTRACT}::files",
        f"paths.py={declared} authority={authority} "
        f"paths-only={sorted(set(declared) - set(authority))} "
        f"authority-only={sorted(set(authority) - set(declared))}",
        "fleet/node-root-contract.yaml owns the vocabulary; paths.py transcribes it",
        "HIGH", "OPEN")]


# ---------------------------------------------------------------------------


def collect(paths, *, repo_root: Path, home: str | None = None) -> dict:
    home = home or str(Path.home())
    node_contract = node_root_contract(repo_root) or {}
    rows: list[dict] = []
    for surface in surfaces(paths, home):
        means = ({name: (spec or {}).get("absent_means")
                  for name, spec in node_contract.items()}
                 if surface["area"] == "home" else None)
        rows.extend(compare(surface, EXCEPTIONS.get(surface["area"], {}),
                            {k: v for k, v in (means or {}).items() if v}))
    rows.extend(declaration_conflicts(paths, repo_root))

    summary = {
        "findings": len(rows),
        "blocking_open": sum(1 for r in rows if r["severity"] == "HIGH" and r["status"] == "OPEN"),
        "undeclared": sum(1 for r in rows if r["id"] == "UNDECLARED_ENTRY"),
        "exempt": sum(1 for r in rows if r["id"] == "EXEMPT_ENTRY"),
        "absent": sum(1 for r in rows if r["id"] == "DECLARED_ENTRY_ABSENT"),
    }
    return {"schema": SCHEMA, "summary": summary, "findings": rows}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--data-root")
    ap.add_argument("--workspace-root")
    ap.add_argument("--home")
    ap.add_argument("--repo-root", type=Path,
                    help="defaults to the checkout this module lives in")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import paths as paths_mod  # noqa: PLC0415 - only needed on this path

    roots = {}
    if args.data_root:
        roots["data_root"] = args.data_root
    if args.workspace_root:
        roots["workspace_root"] = args.workspace_root
    if args.home:
        roots["home"] = args.home

    repo_root = args.repo_root or Path(__file__).resolve().parent.parent
    report = collect(paths_mod.Paths(roots or None), repo_root=repo_root, home=args.home)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        s = report["summary"]
        print(f"schema: {report['schema']}")
        print(f"findings: {s['findings']} (undeclared {s['undeclared']}, "
              f"exempt {s['exempt']}, declared-absent {s['absent']})")
        for row in report["findings"]:
            print(f"  [{row['severity']:<6}] {row['status']:<10} {row['id']:<28} "
                  f"{row['class']}/{row.get('entry', '')}")
    return 1 if report["summary"]["blocking_open"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
