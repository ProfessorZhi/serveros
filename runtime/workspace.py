#!/usr/bin/env python3
"""workspace-management — placement and governance, made answerable (§I).

What this module is for
-----------------------
The failure it prevents is an agent deciding for itself where something goes.
On this node the difference between `workspace/src`, `workspace/worktrees`,
`workspace/test-envs`, `workspace/records` and `artifacts/` is not stylistic:
each has an invariant the others do not, and a file put in the wrong one is
either a canonical checkout that someone has started editing, or evidence that
exists only on one machine. Neither failure announces itself. Both are found
later, by a person, at the worst time.

So this module answers five questions about placement, and it answers them from
the path contract rather than from a directory listing:

    overview    what the workspace is, area by area, as declared
    locate      where a THING of a known kind belongs — before it is created
    inspect     what KIND an existing path is, and what that obliges
    reconcile   the declared shape against the shape on disk
    drift       per-checkout git state, which is what a stale worktree looks like
    policy      the placement rules themselves, readable without running anything

What it deliberately does NOT own
---------------------------------
Item and Run lifecycle (work-engine), container supervision (the runtime), and
the contents of any cloud document (the provider that holds it). Those are
three different obligations and this module would be a fourth thing to keep in
sync if it answered any of them. `drift` reads git and the filesystem and
nothing else — a `workspace drift` that also reported container health would
mean a workspace question could not be answered without Docker running.

Why it builds on `runtime/paths.py` instead of its own table
-----------------------------------------------------------
`paths.py` already owns the root contract, the closed top-level vocabularies
and `admit()`. A second copy of that table here would be a second authority on
where things live, and the two would disagree the first time one was edited.
This module adds what `paths.py` intentionally does not carry — the SEMANTIC
classes (§I's src / worktree / test-env / record / artifact / scratch) and
their invariants — and resolves every path through `Paths` so that a class can
never point somewhere `admit()` would refuse.

Read-only, all of it. `reconcile` reports that a declared area is missing; it
does not create it. A check that creates the directory it is checking has
answered its own question, which is the same reasoning `runtime/conformance.py`
carries.
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

SCHEMA = "serveros/workspace.v1"
THIS_IS = "a placement and governance view, not a lifecycle authority"

# ---------------------------------------------------------------------------
# The six classes.
#
# `root` and `rel` are resolved through `Paths.resolve`, so a class whose path
# `admit()` would refuse raises at resolve time rather than being reported as a
# location something could be put in.
#
# Three fields deserve their own sentence, because they are the ones a reader
# will try to collapse into each other:
#
#   durable      losing this costs something that cannot be recomputed
#   rebuildable  it can be recomputed, and the recipe is elsewhere
#   mutable      an agent may edit it in place without asking
#
# They are not opposites. `src` is durable AND rebuildable — a fresh clone
# restores it exactly, which is why editing it in place is still wrong. `scratch`
# is mutable and neither durable nor rebuildable: it is not recomputed, it is
# dropped. A single "temporary: true" flag would flatten all three.
# ---------------------------------------------------------------------------
PLACEMENT_CLASSES: dict[str, dict] = {
    "src": {
        "root": "workspace",
        "rel": "src",
        "is_": "the canonical clean checkout of an upstream repository",
        "must_be": [
            "a git checkout whose remote is the authoritative source",
            "on its tracking branch, with no local commits of its own",
            "clean — no uncommitted work, ever",
        ],
        "must_not_be": [
            "where work is done. Editing here produces a dirty canonical "
            "checkout, and the next `git pull` either conflicts or silently "
            "loses the reason the edit was made",
            "where a build runs. Build output belongs to a worktree, a "
            "test-env, or a run's artifact directory",
            "a place to keep a second copy of anything. One checkout of one "
            "repository; a second is a worktree",
        ],
        "durable": True,
        "rebuildable": True,
        "mutable": False,
    },
    "worktree": {
        "root": "workspace",
        "rel": "worktrees",
        "is_": "an execution or feature context: a git worktree of a src checkout",
        "must_be": [
            "a git worktree whose `src` sibling still exists and is clean",
            "attributable to the work it is doing — an item, a branch, a run",
        ],
        "must_not_be": [
            "the canonical source. A worktree is disposable; if it were "
            "canonical, `git worktree remove` would destroy something",
            "a long-term home. A worktree that outlives its branch is a "
            "checkout nobody can explain",
        ],
        "durable": False,
        "rebuildable": True,
        "mutable": True,
    },
    "test-env": {
        "root": "workspace",
        "rel": "test-envs",
        "is_": "optional runtime isolation — how to bring an environment up",
        "must_be": [
            "a DEFINITION that can be brought up from a clean machine",
            "accompanied by whatever the definition needs to be reproducible",
        ],
        "must_not_be": [
            "the only copy of anything. The runtime half is rebuilt, not "
            "migrated; a test-env whose value lives in its running state is "
            "not a test-env, it is an unbacked service",
            "assumed to be running. Nothing here asserts liveness — that is "
            "the runtime's question and it is asked elsewhere",
        ],
        "durable": True,
        "rebuildable": True,
        "mutable": True,
    },
    "record": {
        "root": "workspace",
        "rel": ("records", "knowledge", "ops"),
        "is_": "human-readable durable work — the thing a person reads later",
        "must_be": [
            "readable without the tool that produced it",
            "attributable: what was decided, by whom, on what evidence",
        ],
        "must_not_be": [
            "machine state. A state root that is also a document store is two "
            "authorities wearing one path",
            "a secret store. Records are the most-copied directory on a node; "
            "a credential in one is a credential everywhere",
        ],
        "durable": True,
        "rebuildable": False,
        "mutable": True,
    },
    "artifact": {
        "root": "artifacts",
        "rel": ("work-items", "skills"),
        "is_": "Item and Run evidence: output addressed by what produced it",
        "must_be": [
            "addressable by the Item or Run id that produced it, under the "
            "canonical namespace for its kind",
            "sufficient to re-check a claim without re-running the work",
        ],
        "must_not_be": [
            "a durable document. If someone would read it for its own sake it "
            "is a record, and records survive artifacts",
            "a second copy of source, or of a record",
            "the only copy of something a person would miss — artifacts are "
            "pruned by age, and that is the design",
        ],
        "durable": False,
        "rebuildable": False,
        "mutable": False,
    },
    "scratch": {
        "root": "tmp",
        "rel": (),
        "is_": "rebuildable or droppable intermediate space",
        "must_be": [
            "recreatable from something that is not in it",
        ],
        "must_not_be": [
            "the only copy of anything, at any moment, for any reason. This "
            "is the one rule, and it is absolute",
            "where a result lives because it was convenient to write it there",
        ],
        "durable": False,
        "rebuildable": True,
        "mutable": True,
    },
}

# Surfaces that behave as scratch but are NOT under `workspace` or the `tmp`
# root, declared here so `inspect` can classify them instead of reporting
# `external`. Each carries its own provenance, because "we decided this is
# scratch" and "the host's storage policy says this is scratch" are different
# claims and only one of them is ours to make.
SCRATCH_SURFACES = (
    {"path": "/data/scratch/vince", "class": "scratch",
     "provenance": "host_profile.scratch_area.this_account_path",
     "note": "the host's own scratch area. Mode 0700, and DISPOSABLE_REBUILDABLE "
             "per the host profile — it is on a different filesystem from the "
             "home directory, so it is also the right place for large "
             "temporary output that would otherwise fill the quota."},
)

# `caches`, `logs` and `downloads` are neither of the six and are not claimed
# to be. Named explicitly so `inspect` says "cache, and here is its rule"
# rather than "unknown", which reads as "nobody has thought about this".
ADJACENT_SURFACES = {
    "caches": ("cache", "recomputable, and safe to lose — but not safe to lose "
                        "silently, so it is not scratch"),
    "logs": ("log", "append-only operational history. Not durable evidence: it "
                    "is pruned, and it is not addressed by an Item or Run id"),
    "downloads": ("download", "acquired data. Kept because re-acquiring may be "
                              "expensive; never the only copy of a published "
                              "artifact"),
    "software": ("install", "installed tooling. Rebuildable from a recipe; not "
                            "a place for anything authored here"),
    "toolchains": ("toolchain", "language and build toolchains, by the "
                                "TOOLCHAINS_ROOT contract"),
    "envs": ("env", "virtual environments. Rebuildable by definition, and the "
                    "rebuild recipe is not stored here"),
    "data": ("data", "data root content that is not addressed as an artifact"),
    "runtime-assets": ("runtime-asset",
                       "distributions and browser bundles. Resolved by semantic "
                       "role, not by this path, because the role is stable and "
                       "the path moves"),
    "state": ("state", "machine state under the state-root contract. Being "
                       "here is not a claim of authority — see `paths.py`'s "
                       "PROJECTION_NON_AUTHORITATIVE"),
}


class WorkspaceError(Exception):
    """The question cannot be answered from the contract. Never a default."""


# ---------------------------------------------------------------------------
# Resolution
# ---------------------------------------------------------------------------


def _class_roots(paths) -> dict[str, list[str]]:
    """class -> the absolute paths it governs, resolved through `Paths`.

    Resolution rather than string joining: a class whose prefix `admit()` would
    refuse raises here, at the moment the contract is read, instead of being
    offered to an agent as a place to put something.
    """
    governed: dict[str, list[str]] = {}
    for name, spec in PLACEMENT_CLASSES.items():
        rel = spec["rel"]
        rels = rel if isinstance(rel, tuple) else (rel,)
        resolved = []
        for one in rels or ("",):
            ref = spec["root"] if not one else f"{spec['root']}/{one}"
            resolved.append(paths.resolve(ref)["path"])
        governed[name] = resolved
    return governed


def require_bound(paths, verb: str) -> None:
    """Refuse to answer placement from roots this machine did not declare.

    `Paths.bound` is false when a root fell back to the compiled-in literal,
    and those literals are the first node's — Beijing's paths. `_expand` keeps
    them on purpose (they are fixture data elsewhere in this repository), but
    a PLACEMENT answer is the one place where the difference between a
    configured root and an inherited one is the whole value of the answer.

    The failure this prevents is not a wrong string. It is a right-looking
    string. In a non-login process on this node, `paths.py` resolves
    `workspace` to `/opt/applications/data/home-hwz/workspace` and every verb
    here would happily report it — as a location that does not exist (so
    `locate` advises a path nothing can be written to), or, on a machine that
    has both, as a location that does. Neither announces itself, which is
    precisely the situation §I says an agent must not be left to guess at.

    So the refusal IS the feature. The remedy is one export, named in the
    message.
    """
    if paths.bound:
        return
    named = list(paths.unconfigured)
    shown = ", ".join(named[:4]) + (", ..." if len(named) > 4 else "")
    raise WorkspaceError(
        f"`{verb}` will not answer here: this process resolved {len(named)} "
        f"root(s) to the compiled-in defaults ({shown}), which are the first "
        f"node's paths. The answer would name directories on another machine "
        f"and would look exactly like a correct one. Source this node's roots "
        f"first — `~/.config/serveros-node-env.sh` sets DATA_ROOT, and a "
        f"login shell already sources it. `policy` stays readable without it: "
        f"the placement rules are the same on every node, only the paths are "
        f"not.")


def classify(paths, target: str) -> dict:
    """What kind of path this is, from the contract. Never from a pattern alone.

    Several claims can cover one path — the host's scratch area and this
    node's own roots are independent — so the rule is most specific wins, by
    prefix length. An ordering of the tables would answer the same question
    differently depending on which one a future editor put first, and the two
    answers differ in exactly the way that matters: `src` is durable and not
    editable in place, `scratch` is neither.
    """
    require_bound(paths, "inspect")
    absolute = str(Path(target).expanduser())
    governed = _class_roots(paths)

    # Every claim that covers this path, and then the MOST SPECIFIC one wins —
    # by prefix length, not by the order the tables happen to be written in.
    #
    # This was a real ordering bug and the shape of it is worth keeping. The
    # host's scratch area and this node's own roots are independent claims,
    # and on a node whose `TMPDIR` is the scratch area the two NEST: every
    # path under the workspace is also under `/data/scratch/<account>`. A
    # first-match-wins loop then answered "scratch" for a path inside
    # `workspace/src` — durable, non-editable, canonical — depending only on
    # which table was consulted first. Longest prefix removes the ordering as
    # a variable: `workspace/records` is a longer claim than the scratch
    # surface it sits inside, so it wins, and so does it on any future node
    # where the nesting runs the other way.
    claims: list[tuple[int, int, dict]] = []
    for surface in SCRATCH_SURFACES:
        prefix = surface["path"]
        if absolute == prefix or absolute.startswith(prefix + os.sep):
            claims.append((len(prefix), 1, {
                "path": absolute,
                "class": surface["class"],
                "governed_by": surface["provenance"],
                "note": surface["note"],
                **{k: PLACEMENT_CLASSES[surface["class"]][k]
                   for k in ("must_be", "must_not_be", "durable",
                             "rebuildable", "mutable")},
            }))

    for name in ("src", "worktree", "test-env", "record", "artifact", "scratch"):
        for root in governed[name]:
            if absolute == root or absolute.startswith(root + os.sep):
                spec = PLACEMENT_CLASSES[name]
                # Tie-break index 0: at equal specificity this node's own
                # contract outranks an advisory host-surface claim.
                claims.append((len(root), 0, {
                    "path": absolute,
                    "class": name,
                    "governed_by": f"paths.py root `{spec['root']}`",
                    "is_": spec["is_"],
                    "must_be": spec["must_be"],
                    "must_not_be": spec["must_not_be"],
                    "durable": spec["durable"],
                    "rebuildable": spec["rebuildable"],
                    "mutable": spec["mutable"],
                }))

    if claims:
        claims.sort(key=lambda claim: (-claim[0], claim[1]))
        return claims[0][2]

    # Everything else. `Paths.admit` is asked rather than the root table being
    # re-read, so this module and the path governor cannot come to disagree
    # about which roots exist.
    try:
        admission = paths.admit(absolute, writing=False)
    except Exception:  # noqa: BLE001 - a refusal is an answer here, not an error
        admission = None

    root_name = admission["root"] if admission else None
    adjacent = ADJACENT_SURFACES.get(root_name)
    if adjacent:
        kind, note = adjacent
        return {
            "path": absolute,
            "class": "adjacent",
            "adjacent_kind": kind,
            "governed_by": f"paths.py root `{root_name}`",
            "note": note,
            "must_be": [],
            "must_not_be": [],
            "durable": False,
            "rebuildable": True,
            "mutable": True,
        }
    return {
        "path": absolute,
        "class": "external",
        "governed_by": None,
        "note": (
            "not under any declared root, so no placement rule governs it. "
            "That is a finding, not a gap in the classifier: something written "
            "outside every declared root is invisible to `reconcile` and to "
            "every artifact-retention rule this node has."
            if admission is None else
            f"under the `{root_name}` root but outside the six placement "
            f"classes and the named adjacent kinds"
        ),
        "must_be": [],
        "must_not_be": [],
        "durable": None,
        "rebuildable": None,
        "mutable": None,
    }


def locate(paths, class_name: str, name: str | None = None) -> dict:
    """Where a thing of this class belongs. Refuses an unknown class.

    This is the verb that exists so an agent never has to decide. It returns a
    path that has been through `admit()`, the class's invariants, and whether
    the target exists — three things a caller would otherwise assemble from
    memory and get wrong in the direction of writing something somewhere new.

    There is no default class. A `locate` that guessed would place a record in
    a scratch directory the first time it was asked about something new.
    """
    require_bound(paths, "locate")
    if class_name not in PLACEMENT_CLASSES:
        raise WorkspaceError(
            f"{class_name!r} is not one of the declared placement classes "
            f"{sorted(PLACEMENT_CLASSES)}. There is no default class: "
            f"placement is the decision this command exists to make, and a "
            f"guess here is a file in the wrong root that nothing will find.")
    governed = _class_roots(paths)[class_name]
    spec = PLACEMENT_CLASSES[class_name]
    # The first declared prefix is the primary one. `record` and `artifact`
    # have several; `locate` names all of them rather than picking silently.
    base = governed[0]
    target = base if not name else str(Path(base) / name)
    try:
        admission = paths.admit(target, writing=False)
        canonical = admission["canonical"]
    except Exception as exc:  # noqa: BLE001 - the refusal is the answer
        raise WorkspaceError(
            f"{target}: the path governor refuses this location ({exc}). "
            f"`locate` does not offer a path the node would not admit.") from None
    return {
        "schema": SCHEMA,
        "class": class_name,
        "path": target,
        "name": name,
        "governs": governed,
        "exists": Path(target).exists(),
        "canonical": canonical,
        "is_": spec["is_"],
        "must_be": spec["must_be"],
        "must_not_be": spec["must_not_be"],
        "durable": spec["durable"],
        "rebuildable": spec["rebuildable"],
        "mutable": spec["mutable"],
    }


# ---------------------------------------------------------------------------
# Overview and reconcile
# ---------------------------------------------------------------------------


def _measure(directory: Path) -> dict:
    """What is at this path, without walking it.

    Top-level entry count only, and `bytes: null` rather than a size. A deep
    total on `workspace/src` walks tens of thousands of objects and turns an
    overview into a wait; reporting a number that is cheap to compute and
    labelling it correctly is better than reporting one that is not and
    implying it is the whole story.
    """
    if not directory.exists():
        return {"exists": False, "entries": None, "bytes": None,
                "kind": None}
    if directory.is_file():
        return {"exists": True, "entries": 1, "bytes": directory.stat().st_size,
                "kind": "file"}
    try:
        names = list(os.scandir(directory))
    except PermissionError as exc:
        return {"exists": True, "entries": None, "bytes": None, "kind": "directory",
                "unreadable": str(exc)}
    return {"exists": True, "entries": len(names), "bytes": None,
            "kind": "directory", "children": sorted(n.name for n in names)}


def overview(paths) -> dict:
    require_bound(paths, "overview")
    areas = []
    for name, spec in PLACEMENT_CLASSES.items():
        governed = _class_roots(paths)[name]
        for path in governed:
            measured = _measure(Path(path))
            areas.append({
                "class": name,
                "path": path,
                "is_": spec["is_"],
                "durable": spec["durable"],
                "rebuildable": spec["rebuildable"],
                "mutable": spec["mutable"],
                **measured,
            })
    for surface in SCRATCH_SURFACES:
        measured = _measure(Path(surface["path"]))
        if measured["exists"]:
            areas.append({"class": surface["class"], "path": surface["path"],
                          "is_": PLACEMENT_CLASSES["scratch"]["is_"],
                          "governed_by": surface["provenance"],
                          "durable": False, "rebuildable": True,
                          "mutable": True, **measured})
    return {
        "schema": SCHEMA,
        "basis": "paths.py root contract",
        "this_is": THIS_IS,
        "areas": areas,
        "note": (
            "A declared area that does not exist is reported as existing: "
            "false, not omitted. An area silently absent from an overview "
            "reads as an area with nothing in it, which is a different "
            "finding."),
        "sizes_not_measured": (
            "top-level entry counts only. A deep total on src/ walks tens of "
            "thousands of git objects; the number would be correct and the "
            "command would stop being an overview."),
    }


def reconcile(paths) -> dict:
    """The declared shape against the shape on disk. Read-only, creates nothing."""
    require_bound(paths, "reconcile")
    workspace = Path(paths.roots["workspace"])
    declared = set(paths.vocabulary("workspace"))
    findings: list[dict] = []

    if not workspace.exists():
        # The same shape as the full return, so a caller renders one payload.
        # The earlier version of this branch omitted `root`, and the CLI
        # renderer reads it unconditionally: a missing workspace root reported
        # as a KeyError is a check that crashes on the state it exists to name.
        return {
            "schema": SCHEMA, "ok": False,
            "root": str(workspace),
            "declared": sorted(declared),
            "present": [],
            "findings": [{"severity": "blocking", "code": "WORKSPACE_ROOT_MISSING",
                          "detail": f"{workspace} does not exist"}],
            "summary": {"present": 0, "declared_absent": len(declared),
                        "undeclared": 0, "blocking": 1},
        }

    present = {p.name for p in workspace.iterdir()}
    for name in sorted(declared - present):
        findings.append({
            "severity": "informational", "code": "DECLARED_ABSENT", "entry": name,
            "detail": f"workspace/{name} is declared by the contract and is not "
                      f"on disk. Not blocking: an area with nothing in it yet "
                      f"is a normal state, and creating it here would make this "
                      f"check a producer rather than a check",
        })
    for name in sorted(present - declared):
        findings.append({
            "severity": "blocking", "code": "UNDECLARED_WORKSPACE_ENTRY", "entry": name,
            "detail": f"workspace/{name} exists and no contract declares it. "
                      f"A seventh top-level entry is how `workspace/artifacts` "
                      f"came to exist beside `artifacts/` — two names for one "
                      f"thing, and a reader has to guess which one is real",
        })

    # The classes are checked against the contract, not against each other:
    # a class whose prefix the governor would refuse is a placement this
    # command would otherwise recommend and the node would reject.
    for name, spec in PLACEMENT_CLASSES.items():
        for governed in _class_roots(paths)[name]:
            try:
                paths.admit(governed, writing=False)
            except Exception as exc:  # noqa: BLE001 - the refusal is the finding
                findings.append({
                    "severity": "blocking", "code": "CLASS_NOT_ADMITTED",
                    "entry": name,
                    "detail": f"the {name} class resolves to {governed}, which "
                              f"the path governor refuses: {exc}",
                })

    blocking = [f for f in findings if f["severity"] == "blocking"]
    return {
        "schema": SCHEMA,
        "ok": not blocking,
        "root": str(workspace),
        "declared": sorted(declared),
        "present": sorted(present),
        "findings": findings,
        "summary": {
            "present": len(present & declared),
            "declared_absent": len(declared - present),
            "undeclared": len(present - declared),
            "blocking": len(blocking),
        },
    }


# ---------------------------------------------------------------------------
# Drift — the git half, and only the git half
# ---------------------------------------------------------------------------


def _git(path: Path, *args: str) -> tuple[int, str]:
    try:
        proc = subprocess.run(["git", "-C", str(path), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              timeout=20)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 128, str(exc)
    return proc.returncode, proc.stdout.decode(errors="replace").strip()


def _checkout_state(path: Path) -> dict:
    code, _ = _git(path, "rev-parse", "--git-dir")
    if code != 0:
        return {"path": str(path), "is_checkout": False}
    _, branch = _git(path, "rev-parse", "--abbrev-ref", "HEAD")
    _, porcelain = _git(path, "status", "--porcelain")
    _, upstream = _git(path, "rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")
    ahead = behind = None
    if upstream and not upstream.startswith("fatal"):
        _, counts = _git(path, "rev-list", "--left-right", "--count",
                         f"{upstream}...HEAD")
        parts = counts.split()
        if len(parts) == 2 and all(p.isdigit() for p in parts):
            behind, ahead = int(parts[0]), int(parts[1])
    dirty = [line for line in porcelain.splitlines() if line.strip()]
    return {
        "path": str(path),
        "is_checkout": True,
        "branch": branch,
        "upstream": None if upstream.startswith("fatal") else upstream,
        "ahead": ahead,
        "behind": behind,
        "dirty_files": len(dirty),
        "clean": not dirty,
    }


def _declared_children(directory: Path) -> list[Path]:
    """The directories directly under `directory`, minus VCS internals.

    Dot-entries are excluded because `.git` is a directory that answers
    `rev-parse --git-dir` — it IS inside a repository — so a scanner that
    walked every directory reported the canonical checkout's own metadata
    directory as a dirty checkout of the parent repository. Measured, and the
    reason the exclusion is here rather than in the caller.
    """
    return [entry for entry in sorted(directory.iterdir())
            if entry.is_dir() and not entry.name.startswith(".")]


def _scan_checkouts(directory: Path) -> list[dict]:
    """Every checkout under a class root, one level or two.

    Two levels because that is the layout this node actually has: worktrees
    are addressed by the repository they branch from —
    `worktrees/<repo>/<worktree>` — while `test-envs` holds its checkouts
    directly. Scanning only the first level reported the eight repository
    containers as "not a git checkout" and missed every worktree beneath
    them, which is the answer the command exists to produce.

    A child that is itself a checkout is taken as the leaf and not descended
    into. A child that is not is treated as a container and its own children
    are offered; anything left over is a leaf that is not a checkout, and the
    caller decides whether that is a finding.
    """
    found: list[dict] = []
    for entry in _declared_children(directory):
        state = _checkout_state(entry)
        if state["is_checkout"]:
            found.append(state)
            continue
        grandchildren = _declared_children(entry) if entry.exists() else []
        if not grandchildren:
            found.append(state)
            continue
        for child in grandchildren:
            nested = _checkout_state(child)
            nested["container"] = entry.name
            found.append(nested)
    return found


def drift(paths, *, include_dirty_detail: bool = False) -> dict:
    """Git state of every checkout under `src`, `worktrees` and `test-envs`.

    Deliberately not a container check, a port check or a liveness check. Those
    are the runtime's questions, they need a daemon and a probe, and a workspace
    command that could not answer without them would be a workspace command
    nobody can run on a quiet machine.

    A dirty `src` is reported as a fault and a dirty worktree as normal. That
    asymmetry is the whole point of the two classes: a worktree is where work
    happens, so uncommitted work in one is the expected state, while a dirty
    canonical checkout means somebody has been editing the thing that is
    supposed to be restorable by cloning it again.
    """
    require_bound(paths, "drift")
    findings: list[dict] = []
    checkouts: list[dict] = []
    for class_name, root_name in (("src", "workspace/src"),
                                  ("worktree", "workspace/worktrees"),
                                  ("test-env", "workspace/test-envs")):
        directory = Path(paths.resolve(root_name)["path"])
        if not directory.exists():
            continue
        for state in _scan_checkouts(directory):
            name = Path(state["path"]).name
            entry = Path(state["path"])
            state["class"] = class_name
            if include_dirty_detail and state.get("is_checkout"):
                _, porcelain = _git(entry, "status", "--porcelain")
                state["dirty_detail"] = porcelain.splitlines()[:50]
            checkouts.append(state)
            if not state.get("is_checkout"):
                # Blocking under src, informational elsewhere, and the
                # asymmetry is not a preference. `src` is DECLARED to be
                # checkouts, so anything else there is a contract violation.
                # `worktrees` and `test-envs` are containers that may hold a
                # checkout; a directory under them that is not one is worth
                # naming and is not itself a breach.
                findings.append({
                    "severity": "blocking" if class_name == "src" else "informational",
                    "code": ("SRC_NOT_A_CHECKOUT" if class_name == "src"
                             else "NOT_A_CHECKOUT"),
                    "entry": name,
                    "detail": f"{entry} is under {class_name} and is not a git "
                              f"checkout."
                              + (" src/ is the canonical checkout of each "
                                 "repository; something else there is either "
                                 "misplaced or a copy of a checkout, and a "
                                 "copy cannot be updated."
                                 if class_name == "src" else
                                 " Nothing is broken by that; it is named so "
                                 "it is not read later as a checkout that "
                                 "stopped being one."),
                })
                continue
            if class_name == "src" and not state["clean"]:
                findings.append({
                    "severity": "blocking", "code": "SRC_DIRTY",
                    "entry": name,
                    "detail": f"{state['dirty_files']} uncommitted path(s) in "
                              f"the canonical checkout of {name}. src/ "
                              f"is supposed to be reproducible by cloning; "
                              f"work belongs in a worktree",
                })
            if class_name == "worktree" and state["upstream"] is None:
                findings.append({
                    "severity": "informational", "code": "WORKTREE_NO_UPSTREAM",
                    "entry": name,
                    "detail": "no upstream tracking branch — expected for a "
                              "detached worktree, worth naming so it is not "
                              "read as a broken remote",
                })

    blocking = [f for f in findings if f["severity"] == "blocking"]
    return {
        "schema": SCHEMA,
        "ok": not blocking,
        "workspace_root": str(paths.roots["workspace"]),
        "checkouts": checkouts,
        "findings": findings,
        "summary": {
            "checkouts": sum(1 for c in checkouts if c.get("is_checkout")),
            "src": sum(1 for c in checkouts if c.get("class") == "src"),
            "worktrees": sum(1 for c in checkouts
                             if c.get("class") == "worktree" and c.get("is_checkout")),
            "test_envs": sum(1 for c in checkouts
                             if c.get("class") == "test-env" and c.get("is_checkout")),
            "dirty_worktrees": sum(1 for c in checkouts
                                   if c.get("class") == "worktree"
                                   and c.get("clean") is False),
            "dirty_src": sum(1 for c in checkouts
                             if c.get("class") == "src" and c.get("clean") is False),
            "blocking": len(blocking),
        },
    }


# ---------------------------------------------------------------------------
# Policy, as data
# ---------------------------------------------------------------------------


def policy(paths) -> dict:
    """The rules, readable without running anything.

    Output as data rather than prose because the point of this module is that
    an agent can be TOLD the rule instead of inferring it. Prose in a
    repository is for people; a caller that has to parse a document to learn
    where a file goes will instead guess.
    """
    # The one verb that answers when the roots are unbound, because the rules
    # are node-independent and an agent that cannot read them cannot place
    # anything. The resolved paths are NOT node-independent, so they are
    # withheld rather than printed: a reader who sees a path list assumes it
    # is this node's, and `paths_resolved: false` beside a plausible list is a
    # disclaimer nobody reads.
    governed = _class_roots(paths) if paths.bound else None
    classes = []
    for name, spec in PLACEMENT_CLASSES.items():
        classes.append({
            "class": name,
            "paths": governed[name] if governed else None,
            "is_": spec["is_"],
            "must_be": spec["must_be"],
            "must_not_be": spec["must_not_be"],
            "durable": spec["durable"],
            "rebuildable": spec["rebuildable"],
            "mutable": spec["mutable"],
        })
    return {
        "schema": SCHEMA,
        "this_is": THIS_IS,
        "bound": paths.bound,
        "paths_resolved": paths.bound,
        "unresolved_because": (
            None if paths.bound else
            "this process did not resolve this node's roots, so the paths "
            "below are withheld. The rules are readable without them; the "
            "paths are not, because the fallback is another machine's"),
        "classes": classes,
        "adjacent": [{"root": k, "kind": v[0], "note": v[1]}
                     for k, v in sorted(ADJACENT_SURFACES.items())],
        "scratch_surfaces": [dict(s) for s in SCRATCH_SURFACES],
        "rules": [
            "Placement is decided before the write, not discovered after it. "
            "`locate` answers where a thing of a known kind goes; if the kind "
            "is not known, that is the question to answer first.",
            "src is never edited. If work has to happen in a checkout, it "
            "happens in a worktree of it.",
            "A record is readable without its producer. If it is not, it is an "
            "artifact and it will be pruned.",
            "An artifact is addressed by the Item or Run that produced it. An "
            "artifact nobody can address is a file in a directory.",
            "Nothing lives only in scratch. This is the one absolute rule in "
            "this file.",
            "A new top-level area is a contract change, not a mkdir.",
        ],
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


def _load_paths():
    import importlib.util

    here = Path(__file__).resolve().parent
    spec = importlib.util.spec_from_file_location(
        "serveros_runtime_paths", here / "paths.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["serveros_runtime_paths"] = module
    spec.loader.exec_module(module)
    return module.Paths()


def _render_overview(payload: dict) -> list[str]:
    lines = [f"schema      {payload['schema']}",
             f"basis       {payload['basis']}",
             ""]
    for area in payload["areas"]:
        mark = " " if area["exists"] else "-"
        entries = area["entries"]
        if not area["exists"]:
            count = "absent"
        elif entries is None:
            # Distinct from absent and from empty. An area that exists and
            # could not be listed is a different finding from one that is not
            # there, and rendering both as `-` would collapse them.
            count = "unreadable"
        else:
            count = f"{entries} entries"
        flags = "".join([
            "D" if area["durable"] else " ",
            "R" if area["rebuildable"] else " ",
            "M" if area["mutable"] else " ",
        ])
        lines.append(f"{mark} {area['class']:<10} {area['path']:<44} "
                     f"[{flags}] {count}")
    lines.append("")
    lines.append("  D durable   R rebuildable   M mutable-in-place")
    return lines


def _render_policy(payload: dict) -> list[str]:
    lines = ["The six classes (§I). Placement is decided before the write.", ""]
    if not payload["paths_resolved"]:
        lines += ["  THIS NODE'S ROOTS ARE UNRESOLVED, so no paths are shown.",
                  "  " + payload["unresolved_because"] + ".",
                  ""]
    for entry in payload["classes"]:
        lines.append(f"{entry['class']}  —  {entry['is_']}")
        for path in entry["paths"] or ():
            lines.append(f"    where   {path}")
        for rule in entry["must_be"]:
            lines.append(f"    must    {rule}")
        for rule in entry["must_not_be"]:
            lines.append(f"    never   {rule}")
        lines.append("")
    lines.append("Rules")
    for rule in payload["rules"]:
        lines.append(f"  - {rule}")
    return lines


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="workspace", description=__doc__.split("\n")[0])
    parser.add_argument("--json", action="store_true")
    sub = parser.add_subparsers(dest="subcommand", required=True)
    sub.add_parser("overview")
    sub.add_parser("reconcile")
    sub.add_parser("policy")
    p_locate = sub.add_parser("locate")
    p_locate.add_argument("class_name")
    p_locate.add_argument("name", nargs="?")
    p_inspect = sub.add_parser("inspect")
    p_inspect.add_argument("path")
    p_drift = sub.add_parser("drift")
    p_drift.add_argument("--dirty-detail", action="store_true")

    args = parser.parse_args(argv)
    paths = _load_paths()

    try:
        if args.subcommand == "overview":
            payload = overview(paths)
            _emit(payload, args.json, _render_overview(payload))
            return 0
        if args.subcommand == "policy":
            payload = policy(paths)
            _emit(payload, args.json, _render_policy(payload))
            return 0
        if args.subcommand == "reconcile":
            payload = reconcile(paths)
            lines = [f"root        {payload['root']}",
                     f"declared    {len(payload['declared'])}",
                     f"present     {payload['summary']['present']}",
                     f"absent      {payload['summary']['declared_absent']}",
                     f"undeclared  {payload['summary']['undeclared']}"]
            for finding in payload["findings"]:
                lines.append(f"  [{finding['severity']:<13}] {finding['code']}: "
                             f"{finding['detail']}")
            _emit(payload, args.json, lines)
            return 0 if payload["ok"] else 1
        if args.subcommand == "locate":
            payload = locate(paths, args.class_name, args.name)
            lines = [f"class       {payload['class']}",
                     f"path        {payload['path']}",
                     f"exists      {payload['exists']}",
                     f"canonical   {payload['canonical']}",
                     f"is          {payload['is_']}"]
            for rule in payload["must_not_be"]:
                lines.append(f"never       {rule}")
            _emit(payload, args.json, lines)
            return 0
        if args.subcommand == "inspect":
            payload = classify(paths, args.path)
            lines = [f"path        {payload['path']}",
                     f"class       {payload['class']}",
                     f"governed_by {payload['governed_by']}"]
            if payload.get("is_"):
                lines.append(f"is          {payload['is_']}")
            if payload.get("note"):
                lines.append(f"note        {payload['note']}")
            for rule in payload.get("must_not_be") or []:
                lines.append(f"never       {rule}")
            _emit(payload, args.json, lines)
            return 0
        if args.subcommand == "drift":
            payload = drift(paths, include_dirty_detail=args.dirty_detail)
            summary = payload["summary"]
            lines = [f"checkouts   {summary['checkouts']} "
                     f"({summary['src']} src, {summary['worktrees']} worktrees, "
                     f"{summary['test_envs']} test-envs)"]
            root = payload["workspace_root"] + os.sep
            for checkout in payload["checkouts"]:
                # Shown relative to the workspace root: the absolute prefix is
                # the same on every row, and a reader scanning for the one
                # checkout that moved should not have to skip past it 40 times.
                where = checkout["path"]
                shown = where[len(root):] if where.startswith(root) else where
                if not checkout.get("is_checkout"):
                    lines.append(f"  {checkout['class']:<9} {'':<34} "
                                 f"{'—':<12}  {shown}  NOT a git checkout")
                    continue
                state = "clean" if checkout["clean"] else f"dirty ({checkout['dirty_files']})"
                divergence = ""
                if checkout["ahead"] is not None and (checkout["ahead"] or checkout["behind"]):
                    divergence = f"  +{checkout['ahead']}/-{checkout['behind']}"
                lines.append(f"  {checkout['class']:<9} {checkout['branch']:<34} "
                             f"{state}{divergence}  {shown}")
            for finding in payload["findings"]:
                lines.append(f"  [{finding['severity']:<13}] {finding['code']}: "
                             f"{finding['detail']}")
            _emit(payload, args.json, lines)
            return 0 if payload["ok"] else 1
    except WorkspaceError as exc:
        sys.stderr.write(f"workspace: {exc}\n")
        return 2

    parser.error(f"unknown subcommand {args.subcommand!r}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
