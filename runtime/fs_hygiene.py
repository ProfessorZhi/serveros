#!/usr/bin/env python3
"""§16A/§16B · Filesystem and directory hygiene — where things ARE.

The question §16A asks is narrower than "is this tidy":

    can ServerOS detect that a file or a resource has been put in the wrong
    place?

Detection is the whole of it. Nothing here moves, renames, symlinks, chmods or
deletes anything, and §16B forbids the automatic path from doing so even where
the answer is obvious. A classifier that could act would be asked to act on the
strength of a name, which is the failure mode §17 spends its whole length
refusing one level down.

Two roots, classified together
------------------------------
`/home/hwz` and `$DATA_ROOT` are classified in one pass because the explanation
for an entry in one of them is often a fact in the other. `$DATA_ROOT/
.vscode-server` is a 6.4 GB tree at DATA_ROOT's top level whose name is not in
`DATA_ROOT_TOP_LEVEL` — undeclared, apparently drift. It is not: `~/.vscode-
server` is a symlink pointing at it, which is a deliberate relocation of a
multi-gigabyte tool cache off HOME. A classifier that looked at DATA_ROOT alone
would report that as misplaced on every run, and a report that is wrong about
the same thing every run is a report nobody reads. So the symlink relation
between the two roots is a first-class input here, not a footnote.

`RETENTION_REQUIRED` is in the vocabulary because §16A names it, and it is NOT
a synonym for LEGACY
----------------------------------------------------------------------------
LEGACY here means exactly one thing: the entry's name is in
`paths.FORBIDDEN_SUFFIXES`, the near-canonical names that outlive the task that
created them, and whose disposition is "migrate the contents and retire the
path". RETENTION_REQUIRED means the opposite: the entry is correctly placed, it
was produced on purpose, and its consumers are real — `.prev-<version>` beside
a `current` pointer under `runtime-assets/hwz-server-os` is the rollback target
of the release that superseded it. Collapsing the two would label the rollback
target as debris, which is how a rollback target gets deleted.

What is NOT established here, and is said rather than implied
------------------------------------------------------------
HOME's top level has no declared vocabulary on this node. `paths.py` declares
closed sets for DATA_ROOT, WORKSPACE_ROOT and `state/serveros`, and for HOME it
declares only the `~/.serveros` allow-list and a refusal of `~/files` — so
`~/`'s sixty-odd entries are largely unchecked by construction, and an entry
there cannot be MISPLACED against a list that does not exist. This module
reports that gap instead of inventing a list to close it, because a list
invented here would be a second statement of HOME's shape authored by the
module that is supposed to be checking it.

There is likewise no per-directory size producer in this product, so `sizes` is
an INPUT. When none is supplied the row says `size_unmeasured` and the
OVERSIZED question is left open rather than answered with a zero — the same
distinction `inventory.py` makes between a measured zero and an unmeasured one.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent


def _load(name: str, filename: str):
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, _HERE / filename)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


paths = _load("serveros_runtime_paths", "paths.py")

# ---------------------------------------------------------------------------
# The vocabulary
# ---------------------------------------------------------------------------

CANONICAL = "CANONICAL"
ALLOWED_EXCEPTION = "ALLOWED_EXCEPTION"
LEGACY = "LEGACY"
MISPLACED = "MISPLACED"
OVERSIZED = "OVERSIZED"
DUPLICATE_SEMANTIC_ROOT = "DUPLICATE_SEMANTIC_ROOT"
RETENTION_REQUIRED = "RETENTION_REQUIRED"
UNKNOWN = "UNKNOWN"

CLASSES = (CANONICAL, ALLOWED_EXCEPTION, LEGACY, MISPLACED, OVERSIZED,
           DUPLICATE_SEMANTIC_ROOT, RETENTION_REQUIRED, UNKNOWN)

# Classes this round may never act on. All of them, listed explicitly so that
# adding one forces the decision rather than inheriting it. §22 and §16B both
# forbid the move; this is the same fact written where a reader will look for
# it.
NEVER_ACTIONABLE = frozenset(CLASSES)

# ---------------------------------------------------------------------------
# The three HOME exceptions (§16B, by name)
#
# Not "the dotfiles we happened to find". §16B names exactly three tool roots
# that must be excluded from any HOME cleanup consideration, and each is a root
# a tool writes into by convention and that moving would break in a way the
# tool reports as its own missing state. The mapping carries WHY, because a
# bare allow-list is the thing that gets widened by whoever hits it next.
# ---------------------------------------------------------------------------

HOME_ALLOWED_EXCEPTIONS = {
    ".claude": ("Claude Code's own user root: settings, plugins, session "
                "archives and the memory directory. Relocating it under "
                "DATA_ROOT would be a change to a tool's contract, not a tidy-up"),
    ".codex": ("Codex's user root. Same shape as `~/.claude`, same reason: the "
               "tool owns the location and reports its absence as NODE_"
               "UNBOUND-style state rather than as a missing file"),
    ".config": ("The XDG config root, named by a published specification rather "
                "than by this product. Half the tools on this host resolve "
                "`$XDG_CONFIG_HOME` or default to it"),
}

# A retained sibling of a `current` pointer: `.prev-<version>`, `.build-<name>`.
# Matched by pattern rather than by listing versions, because the version
# changes every release and a list transcribed here would be stale by the next
# one — which is the defect `paths.py` records against its own HOME allow-list.
RETAINED_PATTERN = re.compile(r"^\.(prev|build)-")

# Reporting threshold, not policy. Named as a default so a caller can see what
# produced a verdict and pass its own; a threshold silently baked into a
# comparison is a number nobody can review.
DEFAULT_OVERSIZED_BYTES = 1 << 30  # 1 GiB

# Which root name is governed by which declared vocabulary, where one exists.
#
# `home` is deliberately ABSENT. `paths.VOCABULARIES["home"]` is the `~/.serveros`
# allow-list, NOT the shape of `~/` — using it here would classify `~/.bashrc`
# as a misplaced entry, which would be wrong and would be wrong loudly enough
# that somebody would widen the list to shut it up. The absence is the finding;
# see the module docstring.
ROOT_VOCABULARY = {
    "data": "DATA_ROOT_TOP_LEVEL",
    "workspace": "WORKSPACE_TOP_LEVEL",
    "state": "STATE_SERVEROS_TOP_LEVEL",
}

# The three roots whose top level is declared a closed set. `state` is declared
# closed in policy but `paths.admit` does not enforce it the way it enforces
# `data` and `workspace`; the report says which of the two it is, because "a
# rule exists and nothing checks it" is a different finding from "there is no
# rule".
ENFORCED_BY_ADMIT = {"data", "workspace"}

# Names that are absent from a declared vocabulary ON THE RECORD, with the
# reason already written down elsewhere. These are UNKNOWN rather than
# MISPLACED, and the distinction is not cosmetic: `paths.py` says of both of
# these that they are "unresolved rather than designed", so calling them
# misplaced would be this module overruling a decision the contract states it
# has not made yet. A classifier that promotes "awaiting a ruling" to "in the
# wrong place" manufactures a finding out of somebody else's open question.
#
# Read from `paths.py` rather than re-argued here: the tuple below names them
# because the contract names them, and the reason string is the contract's.
AWAITING_A_RULING = {
    "document-stewardship": (
        "`paths.py` records this as deliberately NOT in the state vocabulary: "
        "its producer exists only in an unmerged worktree. UNKNOWN rather than "
        "MISPLACED, because the contract states it has not decided"
    ),
    "migrations": (
        "`paths.py` records this as expired evidence that two declarations "
        "disagree about. UNKNOWN rather than MISPLACED for the same reason: "
        "the open question is which declaration wins, not where the directory "
        "belongs"
    ),
}


def _vocabulary(root_name: str) -> tuple[str, ...]:
    attribute = ROOT_VOCABULARY.get(root_name)
    return tuple(getattr(paths, attribute)) if attribute else ()


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def _scan(directory: Path) -> list[dict]:
    """One level, never following a symlink and never recursing.

    Recursion is where a hygiene check becomes a filesystem walk over a 6.4 GB
    tree, and the questions §16A asks — is this in the right place, is it
    duplicated, is it retained — are all answerable from the top level.
    """
    out = []
    try:
        entries = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError as exc:
        return [{"name": None, "error": str(exc)}]
    for entry in entries:
        is_link = entry.is_symlink()
        target = None
        if is_link:
            try:
                target = os.readlink(entry.path)
            except OSError:
                target = None
        out.append({
            "name": entry.name,
            "path": entry.path,
            "is_dir": entry.is_dir(follow_symlinks=False),
            "is_file": entry.is_file(follow_symlinks=False),
            "is_symlink": is_link,
            "link_target": target,
            "resolved": os.path.realpath(entry.path) if is_link else None,
        })
    return out


def symlink_relations(*, home: Path, data: Path) -> dict:
    """HOME entries that are symlinks into DATA_ROOT, by resolved target.

    This is the fact that turns an undeclared DATA_ROOT entry into an
    explained one. Returned as a mapping rather than checked inline so that the
    reason can be carried into the row: a classifier that silently accepted
    `.vscode-server` would leave the next reader unable to tell "explained" from
    "not noticed".
    """
    relations: dict[str, dict] = {}
    for entry in _scan(home):
        if not entry.get("is_symlink") or not entry.get("resolved"):
            continue
        resolved = Path(entry["resolved"])
        if resolved == data or data in resolved.parents:
            relations[str(resolved)] = {
                "home_entry": entry["name"],
                "home_path": entry["path"],
                "target": str(resolved),
                "relative": str(resolved.relative_to(data)) if resolved != data else "",
                "why": (
                    f"HOME carries `{entry['name']}` as a symlink to this path, "
                    f"so the entry here is a relocation this host made on "
                    f"purpose rather than a directory that drifted. The link is "
                    f"the declaration, and it lives in the other root — which "
                    f"is why the two roots are classified together"
                ),
            }
    return relations


# ---------------------------------------------------------------------------
# Classifying one entry
# ---------------------------------------------------------------------------


def classify_entry(entry: dict, *, root_name: str, vocabulary: tuple[str, ...],
                   relations: dict, sizes: dict, oversized_bytes: int,
                   duplicates: set, is_role_location: bool = False) -> dict:
    """One entry's class, plus every other class its facts support.

    `matches` keeps the weaker answers visible for the same reason §16 keeps
    `also_matches`: MISPLACED and UNKNOWN are routinely true of the same entry,
    and a single word that hid the second would hide the question.
    """
    name = entry["name"]
    matches: list[tuple] = []
    size = sizes.get(entry["path"])
    size_note = "size_unmeasured" if size is None else None

    is_retained = bool(RETAINED_PATTERN.match(name))
    # The relation is keyed by a path, and the entry being explained is usually
    # the one at the END of the symlink — a real directory whose own entry has
    # no `link_target` at all. Keying on `entry["resolved"]` alone reported
    # `$DATA_ROOT/.vscode-server` as MISPLACED while the same fact was being
    # used correctly for `~/.vscode-server`, which is the same entry seen from
    # the other root. So the lookup resolves the entry itself.
    explained = relations.get(os.path.realpath(entry["path"]))
    in_vocabulary = name in vocabulary
    awaiting = AWAITING_A_RULING.get(name)

    if in_vocabulary:
        matches.append((CANONICAL, (
            f"`{name}` is one of the {len(vocabulary)} names this root declares "
            f"as its top level"
        )))

    if is_retained and is_role_location:
        matches.append((RETENTION_REQUIRED, (
            f"`{name}` matches the retained-sibling pattern beside a `current` "
            f"pointer, so it is a previous published artifact kept deliberately. "
            f"It is the rollback target of whatever superseded it, its consumers "
            f"are the release procedure, and this round may not verify or "
            f"retire it (§29). RETENTION_REQUIRED is the opposite of LEGACY: "
            f"the contents are not to be migrated anywhere"
        )))

    if name.lower() in paths.FORBIDDEN_SUFFIXES:
        matches.append((LEGACY, (
            f"`{name}` is one of {list(paths.FORBIDDEN_SUFFIXES)}. These "
            f"outlive the task that created them and become structure by "
            f"accident; the disposition is to migrate the contents and retire "
            f"the path, which is a job for a person"
        )))

    if explained:
        matches.append((ALLOWED_EXCEPTION, explained["why"]))

    if name in HOME_ALLOWED_EXCEPTIONS:
        matches.append((ALLOWED_EXCEPTION, HOME_ALLOWED_EXCEPTIONS[name]))

    if name in duplicates:
        matches.append((DUPLICATE_SEMANTIC_ROOT, (
            f"`{name}` is a declared root name and it is materialized under "
            f"more than one root. §16A reports this and moves nothing: which of "
            f"the copies is canonical is a decision that needs the consumers of "
            f"both, and a move made without them is how a live path stops "
            f"resolving for a reason nobody connects to the move"
        )))

    if size is not None and size > oversized_bytes:
        matches.append((OVERSIZED, (
            f"measured at {size} bytes, above the {oversized_bytes}-byte "
            f"reporting threshold. OVERSIZED is a size, not a verdict: a large "
            f"directory is not thereby removable, and nothing here may remove it"
        )))

    if (entry["is_dir"] and vocabulary and not in_vocabulary
            and not explained and name not in HOME_ALLOWED_EXCEPTIONS
            and not is_retained and not awaiting):
        matches.append((MISPLACED, (
            f"`{name}` is a DIRECTORY under a root whose top level this product "
            f"declares a closed set of {len(vocabulary)}, and it is not one of "
            f"them and nothing explains it"
            + (" — this root's vocabulary is enforced on write by `admit()`, so "
               "the entry predates the rule or was made outside it"
               if root_name in ENFORCED_BY_ADMIT else
               " — this root's vocabulary is declared in policy and NOT enforced "
               "by `admit()`, so nothing refused the write")
        )))

    if not matches:
        matches.append((UNKNOWN, (
            (awaiting + ". " if awaiting else "")
            + f"nothing established where `{name}` belongs: it is not in "
            + (f"this root's declared top level {list(vocabulary)}" if vocabulary
               else f"any declared top level for {root_name!r}, because this "
                    f"root has none")
            + (f", it is a {('directory' if entry['is_dir'] else 'file')}"
               + (" and the vocabularies here are lists of roots, so a file at "
                  "this level is not covered by them either way. Whether it is "
                  "an entry document or drift is a judgement this module does "
                  "not have the input to make" if entry["is_file"] else "")
               if entry["is_dir"] or entry["is_file"] else "")
            + f". §16A's rule: an entry no rule covers is UNKNOWN, and it is not "
              f"downgraded to something with a disposition attached just because "
              f"the report looks firmer that way"
        )))

    primary, why = matches[0]
    return {
        "name": name,
        "path": entry["path"],
        "root": root_name,
        "kind": ("symlink" if entry["is_symlink"] else
                 "directory" if entry["is_dir"] else
                 "file" if entry["is_file"] else "unknown"),
        "link_target": entry.get("link_target"),
        "class": primary,
        "why": why,
        "matches": [{"class": c, "why": w} for c, w in matches],
        "also_matches": [c for c, _ in matches[1:]],
        "in_declared_vocabulary": in_vocabulary,
        "size_bytes": size,
        "size_note": size_note,
        "actionable": False,
        "schema": "serveros/fs-hygiene-entry.v1",
    }


def find_duplicate_semantic_roots(roots: dict, *, root_names) -> dict:
    """Declared root names materialized under a root that does not own them.

    `artifacts` appears in DATA_ROOT's vocabulary; if it also appears under
    WORKSPACE_ROOT, that is the substitute-root shape the layout forbids —
    and it is precisely what `~/files` and `workspace/artifacts` were. The
    check is by NAME against every declared vocabulary, so a new root name
    added to `paths.py` is covered without a second list here.
    """
    declared = set()
    for attribute in set(ROOT_VOCABULARY.values()):
        declared |= set(getattr(paths, attribute))
    # A name is only duplicated if it appears somewhere OTHER than the root
    # whose vocabulary declares it. Compiling that ownership from the same
    # attribute map keeps one source: add a root to `paths.py` and this follows.
    owned_by = {}
    for root_name, attribute in ROOT_VOCABULARY.items():
        for name in getattr(paths, attribute):
            owned_by.setdefault(name, set()).add(root_name)

    found: dict[str, list[dict]] = {}
    for root_name in root_names:
        root = Path(roots[root_name])
        if not root.is_dir():
            continue
        for entry in _scan(root):
            name = entry["name"]
            if name not in declared or not entry["is_dir"]:
                continue
            if root_name in owned_by.get(name, set()):
                continue
            found.setdefault(name, []).append({
                "root": root_name, "path": entry["path"],
                "belongs_under": sorted(owned_by.get(name, ())),
            })
    return found


# ---------------------------------------------------------------------------
# The report
# ---------------------------------------------------------------------------


def _home_exception_of(path: Path, home: Path):
    """Which of §16B's three, if any, this path is inside.

    Compared on the FIRST segment under HOME, not on the whole path: the
    question is whether the write is going into one of the three roots, and
    comparing the full path to the root's own path would only ever match a
    write to the directory itself.
    """
    try:
        relative = path.relative_to(home)
    except ValueError:
        return None
    return relative.parts[0] if relative.parts else None


def placement(path, *, paths_registry=None) -> dict:
    """§16B · where a NEW write may go, and what may never be done about it.

    The gate itself is `paths.Paths.admit` and is not restated here: a second
    copy of a placement rule is a second copy that can disagree with the first,
    and `admit` is the one the producers actually call. What this adds is the
    verdict form — `admit` raises, which is right for a producer that must not
    proceed, and wrong for a report that has to classify many paths and say
    what happened to each.

    `automatic_action` is always None, on every path, admitted or refused.
    §16B is explicit that nothing may symlink or migrate a location, so the
    field is present and permanently empty rather than absent: a caller that
    wanted to act has to find a field saying there is nothing to act on, which
    is a harder thing to misread than a missing key.
    """
    registry = paths_registry or paths.Paths()
    home = Path(registry.roots["home"])
    exception = _home_exception_of(Path(str(path)), home)
    in_exception = exception in HOME_ALLOWED_EXCEPTIONS
    try:
        verdict = registry.admit(str(path), writing=True)
    except paths.PathRefused as refusal:
        return {
            "path": str(path),
            "admitted": False,
            "refused_because": str(refusal),
            "canonical": None,
            "existing_exception": in_exception,
            "automatic_action": None,
            "why": (
                "refused by the placement contract. §16B forbids resolving this "
                "by symlink or by migrating the location: the fix is to write "
                "where the contract says, not to make the wrong place work"
                + (f" (`{exception}` is one of the three permitted HOME roots, "
                   f"so the refusal here is about what is being written inside "
                   f"it, not about the root" if in_exception else "")
            ),
            "schema": "serveros/fs-placement.v1",
        }
    return {
        "path": str(path),
        "admitted": True,
        "refused_because": None,
        "canonical": verdict["canonical"],
        "root": verdict["root"],
        "existing_exception": in_exception,
        "automatic_action": None,
        "why": (
            "admitted, and nothing is done to it. "
            + (f"`{exception}` is one of the three HOME roots §16B permits by "
               f"name; the permission is to LEAVE IT ALONE, not to relocate it, "
               f"and a later round that wants it under DATA_ROOT needs the "
               f"consumer list first"
               if in_exception else
               f"the contract classifies this as {verdict['canonical']}")
        ),
        "schema": "serveros/fs-placement.v1",
    }


def report(*, home=None, data=None, sizes=None, oversized_bytes=None) -> dict:
    """Classify both roots, change nothing, and say which question is open.

    `sizes` maps an absolute path to bytes, produced by whoever already
    measures. Passing none is legitimate and results in `size_unmeasured` on
    every row rather than in a silent zero.

    A root that is not there is reported as `absent` with its reason rather
    than omitted: a report over one root that reads as a report over two is
    how a check stops covering half of what it claims.
    """
    home = Path(home) if home else Path(paths.Paths().roots["home"])
    data = Path(data) if data else Path(paths.Paths().roots["data"])
    sizes = sizes or {}
    oversized_bytes = oversized_bytes if oversized_bytes is not None else DEFAULT_OVERSIZED_BYTES
    relations = symlink_relations(home=home, data=data)
    root_names = ("home", "data", "workspace", "state")
    roots = {"home": str(home), "data": str(data),
             "workspace": str(data / "workspace"), "state": str(data / "state/serveros")}
    duplicates = find_duplicate_semantic_roots(roots, root_names=root_names)

    classified = []
    coverage = {}
    for root_name in root_names:
        directory = Path(roots[root_name])
        vocabulary = _vocabulary(root_name)
        if not directory.is_dir():
            coverage[root_name] = {"state": "absent", "path": str(directory),
                                   "entries": 0,
                                   "why": "this node has no such root; nothing here"}
            continue
        entries = []
        for entry in _scan(directory):
            if entry.get("name") is None:
                coverage[root_name] = {"state": "unreadable", "path": str(directory),
                                       "entries": 0, "why": entry.get("error")}
                break
            entries.append(classify_entry(
                entry, root_name=root_name, vocabulary=vocabulary,
                relations=relations, sizes=sizes, oversized_bytes=oversized_bytes,
                duplicates=set(duplicates)))
        else:
            coverage[root_name] = {
                "state": "read", "path": str(directory), "entries": len(entries),
                "declared_top_level": list(vocabulary),
                "vocabulary_enforced_on_write": root_name in ENFORCED_BY_ADMIT,
                "why": (None if vocabulary else
                        "this root has no declared top-level vocabulary, so an "
                        "entry here cannot be MISPLACED against a list that does "
                        "not exist. Recorded rather than closed: a list invented "
                        "here would be authored by the module that checks it"),
            }
        classified.extend(entries)

    # The declared semantic-role locations, one level in.
    #
    # Without this pass RETENTION_REQUIRED is unreachable: `.prev-<version>` and
    # `.build-<name>` live INSIDE `$DATA_ROOT/runtime-assets/hwz-server-os/`,
    # not at DATA_ROOT's top level, so a report that only classified root
    # children never saw the one entry class §16A names by example. It was
    # caught by running the report and reading counts, not by reading the code —
    # which is the same way the `stale_candidate` reachability defect surfaced.
    #
    # One level, declared locations only, no recursion: these are the paths
    # `paths.SEMANTIC_ROLES` points at, so the set is the contract's and a role
    # that moves is a change in one table.
    role_rows = []
    for role, declared in sorted(paths.SEMANTIC_ROLES.items()):
        location = Path(declared.replace("{DATA_ROOT}", str(data)))
        if not location.is_dir():
            continue
        for entry in _scan(location):
            if entry.get("name") is None:
                continue
            role_rows.append(classify_entry(
                entry, root_name=f"role:{role}", vocabulary=(), relations=relations,
                sizes=sizes, oversized_bytes=oversized_bytes, duplicates=set(),
                is_role_location=True))
    classified.extend(role_rows)

    counts = {name: 0 for name in CLASSES}
    for row in classified:
        counts[row["class"]] = counts.get(row["class"], 0) + 1

    open_questions = [row for row in classified
                      if row["class"] in (MISPLACED, DUPLICATE_SEMANTIC_ROOT, LEGACY)]
    return {
        "schema": "serveros/fs-hygiene-report.v1",
        "roots": {name: {"path": roots[name],
                         "class": _vocabulary(name) and "DECLARED_CLOSED" or "NO_VOCABULARY"}
                  for name in root_names},
        "coverage": coverage,
        "entries": classified,
        "counts": counts,
        "role_locations": {
            "declared": sorted(paths.SEMANTIC_ROLES),
            "entries": len(role_rows),
            "why": ("the declared semantic-role locations, one level in. The "
                    "retained artifacts §16A names by example live here rather "
                    "than at a root's top level, so this pass is what makes "
                    "RETENTION_REQUIRED reachable at all"),
        },
        "duplicate_semantic_roots": duplicates,
        "open_questions": open_questions,
        "symlinks_into_data_root": relations,
        "thresholds": {"oversized_bytes": oversized_bytes,
                       "oversized_bytes_source": "this module's default; a "
                       "reporting threshold, not a policy"},
        "note": (
            "§16A/§16B: this report was produced by reading. Nothing was moved, "
            "renamed, linked, chmod'ed or deleted, and nothing downstream of it "
            "may be either — a move made on the strength of a classification is "
            "a move made on the strength of a name. `.botmux-clone.log` and "
            "`.ccbridge-bridge.log` are classified and left alone by name. "
            "Retained release artifacts are RETENTION_REQUIRED, which is the "
            "opposite of LEGACY"
        ),
    }
