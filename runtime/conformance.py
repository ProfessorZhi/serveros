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
    # No `artifacts` entry. It used to exempt four pre-two-namespace buckets
    # (builds/exports/packages/e2e) as CONTRACT_GAP. All four were retired on
    # 2026-09-13 — three were measurably empty, and `e2e/` moved byte-identical
    # into the Items that own it — so an exemption with nothing left to exempt
    # would be a declaration that outlives its subject.
    "data": {
        ".vscode-server": {
            "class": "FOREIGN_TOOL",
            "reason": "editor remote-server state; created and owned by VS Code, "
                      "not by any ServerOS producer",
            "authority": "not named by any contract in this repository",
            "disposition": "KEEP",
        },
    },
    # No `home` entry, and the absence is a ruling rather than an omission.
    # `~/.serveros/README.md` was briefly classified here as an ENTRY_DOCUMENT.
    # §41 reversed that: the node root holds bindings only, and it gives
    # documentation the disposition "repo/docs or artifacts" — because a
    # document inside that root is a second statement of what
    # `node-root-contract.yaml` already states, in a root whose whole design
    # principle is that a thing has one answer. The file was retired on
    # 2026-09-13 with its bytes preserved in
    # artifacts/work-items/serveros/SRV-0007/RUN-0001/, so an exemption here
    # would have no subject left to exempt — the same reason the `artifacts`
    # block above is gone. A README reappearing there is now reported as
    # UNDECLARED_ENTRY, and that report is correct rather than a false alarm.
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
        # `federation`, `boards`, `work-items` and `resource-leases` were listed
        # here as CONTRACT_GAP — which this file defines as "the vocabulary is
        # behind reality and the fix is a ruling that widens it, not a move".
        # §38 made that ruling: all four are now in
        # `paths.STATE_SERVEROS_TOP_LEVEL`, so they are declarations rather than
        # exemptions, and there is nothing left to exempt. Their authority
        # arguments moved into the vocabulary with them.
        #
        # The two below did NOT move, and the difference is the point: widening
        # the vocabulary asserts a namespace belongs to this root's design.
        # These two are unresolved, and declaring them would settle the question
        # by fiat instead of answering it.
        # `environment` was here and is gone deliberately, and it went further
        # than the projections namespace: the 2026-08-19 capture turned out to
        # be non-reproducible, which makes it evidence rather than a
        # projection, and it now lives in the evidence plane
        # (artifacts/skills/workspace-management/RUN-20260819-environment-baseline).
        # The exemption has nothing left to exempt, and an exception for a
        # directory that no longer exists is how an exception table stops
        # describing anything. A test asserts this table and
        # `fleet/state-root-contract.yaml#preexisting` list exactly the same
        # entries.
        "migrations": {
            "class": "UNKNOWN_OWNER",
            # This said "nothing on this node declares who writes it". That was
            # wrong: THREE declarations name this directory, and they disagree,
            # which is a different and harder problem than silence. Recorded
            # rather than tidied, because the disagreement is the finding.
            #   its own `20260824-home-finalize/EXPIRY.md` — TTL 14 days from
            #     2026-08-24, so expired 2026-09-07, and then "删除本目录",
            #     conditioned on a clean report having been archived to
            #     records/archive/migrations/
            #   ops/reports/2026-09-06-serveros-directory-convergence.md — move
            #     it to artifacts/evidence/migrations-20260824-home-finalize/,
            #     marked done
            #   §0 of the standing safety policy — preserve historical evidence
            #     regardless of old naming
            # The expiry's own precondition is not met: nothing is archived at
            # records/archive/migrations/, and no README claims the extension
            # EXPIRY.md provides for. Deleting on an unmet precondition is the
            # one action no declaration supports, so nothing was deleted. The
            # snapshots (before/after git-status, docker-ps, docker-mounts,
            # worktrees, remotes) are not reproducible.
            "reason": "expired rollback snapshots from the 2026-08-24 home "
                      "finalize; three declarations disagree on delete / move / "
                      "preserve, and the expiry's precondition is unmet",
            "authority": "self-refuting: EXPIRY.md, the 2026-09-06 convergence "
                         "report, and §0 each claim it",
            "disposition": "UNKNOWN_OWNER",
        },
        "document-stewardship": {
            "class": "UNKNOWN_OWNER",
            # Also changed from "no declaration of producer or consumer": a
            # producer exists, it just has not landed. DEFERRED_PARALLEL_WIP
            # rather than unknown — an unmerged worktree writing here is a
            # reason to wait, not a reason to guess.
            "reason": "holds one per-project JSON document; the producer exists "
                      "only in an unmerged worktree, so this is deferred "
                      "parallel WIP rather than unknown material",
            "authority": "producer present in a worktree; no merged declaration "
                         "yet",
            "disposition": "UNKNOWN_OWNER",
        },
        # The two below were added 2026-09-23, and the fact that they had to be
        # is the finding: `archive/` sat on disk from 2026-09-20 and
        # `laya-shared/` from 2026-09-23, and neither was in this table, in
        # `fleet/state-root-contract.yaml#preexisting`, or in the state
        # vocabulary. Two reports read those lists and neither could say what
        # these were, so `fs_hygiene` fell through to MISPLACED — whose
        # disposition is a move, not a question.
        "archive": {
            "class": "HISTORICAL_EVIDENCE",
            "reason": "holds one entry, `beijing-20260918/` — a byte-copy of the "
                      "sealed Beijing node's state root (`serveros.db`, "
                      "`federation/`, `requirement-board/`, `runtime/`, "
                      "`abilities/`, `projections/`, each byte suffixed "
                      "`.beijing-20260918`). A point-in-time capture of a node "
                      "that is sealed and no longer changes, so nothing on this "
                      "host can reproduce it",
            "authority": "produced by the 2026-09-18 Beijing → Tokyo migration. "
                         "No ServerOS producer writes here and no contract in "
                         "this repository names the path",
            "disposition": "OWNER_CHECK",
        },
        "laya-shared": {
            "class": "FOREIGN_TOOL",
            # This is the entry the round did not stop at reporting for. It is
            # the socket root of a service that is RUNNING, and `fs_hygiene`
            # classified it MISPLACED with `protected: false` — a cleanup acting
            # on that classification would unlink a live service's socket. The
            # disposition is deliberately not KEEP: whether a provider's state
            # root belongs under ServerOS's state root is §41's boundary
            # question, and answering it here is the fiat this table exists to
            # avoid.
            "reason": "holds exactly one entry, `laya.sock` — the AF_UNIX socket "
                      "of `laya-shared.service`, active on this node. Directory "
                      "0700 and socket 0600, both carrying the service's start "
                      "time",
            "authority": "the provider's own binding, "
                         "`~/.config/serveros/laya-shared-binding.yaml`, names "
                         "this socket path; the service belongs to "
                         "ability-management's shared-provider contract. No "
                         "ServerOS contract declares the directory",
            "disposition": "OWNER_CHECK",
        },
    },
}


# ---------------------------------------------------------------------------
# Severity and enforcement: two dimensions, and only one of them blocks (§26)
# ---------------------------------------------------------------------------
#
# The defect this replaces was not a wrong value, it was a missing distinction.
# Findings were emitted as `severity: HIGH` and the process exited 0, and there
# was no field anywhere that said why — so the same row meant "this is serious
# and we are ignoring it" and "this is serious and it stops the line", and a
# reader could not tell which without opening the source.
#
#   severity     how bad the problem IS. A property of the finding, and of
#                nothing else. It does not vary by rollout phase.
#   enforcement  what this phase DOES about it. A property of the phase.
#
# They are independent on purpose, and `blocking` is DERIVED from ENFORCEMENT
# rather than carried as its own field. A field that can disagree with the
# thing it summarizes eventually will; deriving it makes the disagreement
# unrepresentable.
SEVERITIES = ("INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL")
ENFORCEMENTS = ("OBSERVE", "WARN", "BLOCK")

BLOCKING_BY_ENFORCEMENT = {
    "OBSERVE": False,
    "WARN": False,
    "BLOCK": True,
}
# One entry, so a caller asking "does this block" does not get to answer it
# with its own opinion about severity, which is the mistake being removed.

# Which enforcement each check gets in Phase 1, and why. Kept beside the
# severity table so that "severity HIGH, enforcement WARN" is a declared
# decision with a reason rather than a contradiction somebody has to explain.
PHASE_1_ENFORCEMENT = {
    "UNDECLARED_ENTRY": "WARN",
    "DECLARED_ENTRY_ABSENT": "OBSERVE",
    "EXEMPT_ENTRY": "OBSERVE",
    "DECLARATION_CONFLICT": "WARN",
    "AUTHORITY_CONFLICT": "WARN",
    "CHECK_UNAVAILABLE": "WARN",
}
ENFORCEMENT_REASONS = {
    "UNDECLARED_ENTRY": (
        "Phase 1 is a directory-governance rollout. Several namespaces carry "
        "unknown-owner or preexisting content that no producer has claimed "
        "yet; blocking on them would stop every run until a decision that is "
        "not this check's to make. Promotable to BLOCK once owners are known."
    ),
    "DECLARED_ENTRY_ABSENT": (
        "A declared namespace with no producer is the EXPECTED_ABSENT state, "
        "not a fault. Observed so the shape of the disk is visible."
    ),
    "EXEMPT_ENTRY": (
        "An exempted entry is reported so the exception table stays visible. "
        "Nothing is enforced about an entry that was already classified."
    ),
    "DECLARATION_CONFLICT": (
        "Two declarations disagreeing is a contract defect with an owner. "
        "Warned rather than blocked while the second declaration is retired."
    ),
    "AUTHORITY_CONFLICT": (
        "No projection has yet been observed contradicting an authority "
        "namespace. The check runs from the day the namespace exists so that "
        "the first one is caught by a check that has always been on, rather "
        "than by a check added after the damage."
    ),
    "CHECK_UNAVAILABLE": (
        "A check that could not run is not a check that passed, and saying so "
        "is the whole point of emitting this finding. Warned rather than "
        "blocked because the usual cause is a transient import or read "
        "failure, and blocking would stop unrelated work for it."
    ),
}


def enforcement_for(fid: str, *, phase: str = "phase-1") -> str:
    """The declared enforcement for one check. Never inferred from severity."""
    if phase != "phase-1":  # pragma: no cover - only one phase exists
        raise ValueError(f"unknown rollout phase {phase!r}")
    return PHASE_1_ENFORCEMENT.get(fid, "OBSERVE")


def blocking(enforcement: str) -> bool:
    """The ONLY way `blocking` is produced. See BLOCKING_BY_ENFORCEMENT."""
    try:
        return BLOCKING_BY_ENFORCEMENT[enforcement]
    except KeyError:
        raise ValueError(
            f"{enforcement!r} is not a declared enforcement "
            f"{list(ENFORCEMENTS)}. An unknown enforcement is not treated as "
            f"non-blocking, because a typo would then silently disable a gate."
        ) from None


def finding(fid, cls, desired, observed, authority, severity, status="OPEN", **extra) -> dict:
    if severity not in SEVERITIES:
        raise ValueError(f"{severity!r} is not a declared severity {list(SEVERITIES)}")
    enforcement = extra.pop("enforcement", None) or enforcement_for(fid)
    # Named `enforcement_reason`, not `reason`: `EXEMPT_ENTRY` already carries a
    # `reason` describing the exemption, and one key holding two different
    # explanations is how a row ends up explaining the wrong thing.
    reason = extra.pop("enforcement_reason", None) or ENFORCEMENT_REASONS.get(fid, "")
    row = {
        "id": fid,
        "class": cls,
        "desired": desired,
        "observed": observed,
        "authority": authority,
        "severity": severity,
        # Both dimensions, always, plus the derived field and the reason the
        # pair is what it is. A row that printed `HIGH` and nothing else is
        # the row that could not be argued with.
        "enforcement": enforcement,
        "blocking": blocking(enforcement),
        "enforcement_reason": reason,
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

    # The projections namespace is checked HERE rather than in a separate
    # command, so that "is this node's state root telling one story" is one
    # run with one exit code. A second checker with its own severity table is
    # the second table this file's own docstring warns about.
    try:
        import importlib.util as _ilu

        _spec = _ilu.spec_from_file_location(
            "serveros_runtime_state_root", Path(__file__).resolve().parent / "state_root.py")
        _state_root = _ilu.module_from_spec(_spec)
        sys.modules["serveros_runtime_state_root"] = _state_root
        _spec.loader.exec_module(_state_root)
        rows.extend(_state_root.authority_conflicts(root=paths.root("state")))
    except Exception as exc:  # noqa: BLE001 - an unreadable contract is a finding
        # NOT swallowed. If the authority check cannot run, that is the fact
        # the reader most needs, because a check that silently does not run
        # reports exactly what a check that found nothing reports.
        rows.append(finding(
            "CHECK_UNAVAILABLE", "state/serveros",
            "the projections namespace is checked for AUTHORITY_CONFLICT",
            f"the check could not run: {type(exc).__name__}: {exc}",
            "fleet/state-root-contract.yaml",
            "HIGH", "OPEN",
            enforcement="WARN",
            enforcement_reason=(
                "an unavailable check is not a pass. Warned rather than "
                "blocked because the cause is usually a transient import or "
                "read failure, and blocked would stop unrelated work."
            ),
        ))

    summary = {
        "findings": len(rows),
        # Derived from `enforcement`, NOT from `severity`. The previous version
        # counted severity==HIGH as blocking while exiting 0, which is the
        # contradiction §26 names: a run that says "3 blocking findings" and
        # returns success teaches its reader to ignore the number.
        "blocking_open": sum(1 for r in rows
                             if r["blocking"] and r["status"] == "OPEN"),
        "high_open_nonblocking": sum(
            1 for r in rows
            if r["severity"] == "HIGH" and r["status"] == "OPEN" and not r["blocking"]),
        "by_enforcement": {name: sum(1 for r in rows if r["enforcement"] == name)
                           for name in ENFORCEMENTS},
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
        if s["high_open_nonblocking"]:
            print(f"  note: {s['high_open_nonblocking']} HIGH finding(s) are "
                  f"non-blocking this phase (enforcement WARN/OBSERVE)")
        for row in report["findings"]:
            # severity AND enforcement AND whether it blocks. Printing the
            # severity alone is what produced the row a reader could not
            # interpret: `[HIGH] OPEN` with a successful exit.
            print(f"  [{row['severity']:<8} {row['enforcement']:<7} "
                  f"blocking={str(row['blocking']):<5}] {row['status']:<10} "
                  f"{row['id']:<28} {row['class']}/{row.get('entry', '')}")
    return 1 if report["summary"]["blocking_open"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
