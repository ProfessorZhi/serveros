#!/usr/bin/env python3
"""Answer one question: is the runtime actually running the code we wrote?

Why this file exists
--------------------
Every other check in this repository verifies a *source* artifact: tests pass,
`build.py --check` is clean, the bundle is well formed. None of them can answer
"which revision is the Agent actually loading?", because that is a fact about
**this machine's installed state**, not about the repository.

The gap is not hypothetical. On 2026-09-11 the skeleton truth matrix found that
`space/` and `relations/` were implemented, tested and green, declared in
`plugin/bundle.yaml`, and asserted by `build.py`'s own tests to ship — while the
installed runtime contained neither. Nothing was broken and nothing reported
anything. The discovery took a hand-written `diff` against `site-packages`.

So the rule this module enforces:

    The drift detector must not run inside the component it inspects.

That is why this lives here and not as a `serverosctl` subcommand. `serverosctl`
*is* the drifting component on this machine — the installed `workengine` is a
non-editable copy in a virtualenv that no build step refreshes. A doctor command
shipped inside it would have reported its own stale opinion of itself. A verifier
has to stand outside the thing it verifies, which is why this file lives in the
source tree and reads the installed state as data.

Three questions, not one
------------------------
On 2026-09-11 the same checkpoint found a second failure of the same shape, one
level up. The installed artifact matched its declared build pin byte for byte
and this file reported `OK` — correctly, because there was no drift. But the
pin itself pointed at a revision that predated the fix being shipped, so the
runtime was faithfully running code we had already replaced. "Internally
consistent" and "the right release" are different questions, and a checker that
only answers the first will report OK through the second.

So a release has to answer all three:

    CHAIN_CONSISTENT        source == built == installed, at every hop.
                            The bytes agree with each other. (`verdict` above.)

    CANONICAL_PROVENANCE    the pinned revision is contained in the canonical
                            branch, so the artifact is reproducible from
                            something that will still exist. A pin that no
                            branch contains is `PIN_NOT_CANONICAL`. A pin that
                            is merely OLDER than canonical head is NOT a defect
                            — a release may legitimately pin a superseded
                            revision, and flagging that would make this check
                            fire on a correct machine until it was ignored.

    MATERIALIZATION_UNIQUE  each bundle member reaches each runtime exactly
                            once. A member present both through plugin-native
                            discovery and flattened into the runtime skill root
                            is two active materializations of one thing.

`RELEASE_COMPLETE` requires all three.

An earlier revision of this file declared `PIN_SUPERSEDED` deliberately
unimplemented, on the grounds that deciding it "needs a registry-side
declaration of which revisions are superseded, and that declaration does not
exist". That reasoning was wrong, and the way it was wrong is worth keeping:
the declaration does exist. The registry's `revisions` list is append-only and
ordered, and every append is made by `control_plane.py sync-self` when the
artifact's bytes actually change — so an artifact whose payload no longer
matches canonical head's has, by construction, been superseded. The missing
data was never missing; it was one `git diff` away, and the entry stayed
unimplemented because a sentence said it had to. That is the failure mode this
checkpoint keeps finding, committed by this file about this file.

Supersession is decided on CONTENT, not on commit order: `git diff --quiet
<pin> <canonical_head> -- <payload>` over the ability's payload root — the exact
directory the registry fingerprints. A pin that is merely an older commit with
the same payload is `VALID_HISTORICAL`, not superseded; a release may
legitimately be built from an older commit, and saying otherwise would make
this axis fire on correct machines until someone turned it off.

Which of the four states a pin is in — `CURRENT`, `VALID_HISTORICAL`,
`SUPERSEDED`, `INVALID` — is reported as `state`. `revision exists` and `the
release is current` are different questions: a pin can name a revision canonical
genuinely contains, that rebuilds byte-for-byte, and still not be the release
anyone should be running. Only `INVALID` (and a pin that was never recorded at
all, `PIN_MISSING`) blocks; `SUPERSEDED` is reported and does not.


Two drift classes, kept apart on purpose
----------------------------------------
`INSTALL_RECORD_PROVENANCE_DRIFT` and `PIN_SUPERSEDED` are both "a revision in
this report is not the revision you expected", and they are not the same thing.
Merging them would make one word cover two different repairs.

  INSTALL_RECORD_PROVENANCE_DRIFT   the platform's install record disagrees with
                                    the artifact. The artifact is authority; the
                                    record is an observation of it. IMPLEMENTED,
                                    and it never blocks: the release is measured
                                    from the bytes, and the platform record is
                                    one of the things being measured.

  PIN_SUPERSEDED                    the bundle pins a revision that canonical has
                                    since superseded. IMPLEMENTED -- and, like
                                    the record class, it never blocks. It is
                                    decided by comparing the pinned payload
                                    against canonical head's, which is a
                                    disagreement between two real authorities
                                    (a past release and the current source),
                                    not a guess about either.

The asymmetry is the point. One is a disagreement between an authority and a
record of it; the other is a disagreement between two authorities. Neither
blocks, because neither is a byte-level lie: the bytes are what they claim, and
what changed is whether they are still the ones to ship.

What it does NOT do
-------------------
No network. No LLM. No database. No full-disk scan. No mutation of any kind —
every path below is opened read-only, and the one subprocess it runs is
`git rev-parse`. Output contains no clock reading, so two runs over an unchanged
machine produce identical bytes and can be compared.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import yaml

EXCLUDED_DIRS = {"__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

BUNDLE_NAME = "hwz-server-os"
CLAUDE_PLUGIN_KEY = "hwz-server-os@hwz-server-os"
CODEX_MARKETPLACE = "hwz-server-os-local"

# The branch a build pin must be reachable from. Named here rather than
# inlined so the fixture can point it at its own synthetic repository.
DEFAULT_CANONICAL_REF = "refs/heads/main"

OK = "OK"
SOURCE_RUNTIME_DRIFT = "SOURCE_RUNTIME_DRIFT"
BUILT_RUNTIME_DRIFT = "BUILT_RUNTIME_DRIFT"
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"

# Drift classes that are NOT about the bytes on disk agreeing with each other.
# A chain can be internally perfect and still be the wrong release; these name
# the ways that happens.
PIN_NOT_CANONICAL = "PIN_NOT_CANONICAL"
PIN_MISSING = "PIN_MISSING"
PIN_SUPERSEDED = "PIN_SUPERSEDED"
NAME_COLLISION = "NAME_COLLISION"

# The four states a pin can be in. NOT the same question as "is the pin valid":
# a revision can be real, canonical, and reproducible while the release built
# from it is no longer the one to ship. Only INVALID and MISSING block.
PIN_STATE_CURRENT = "CURRENT"
PIN_STATE_HISTORICAL = "VALID_HISTORICAL"
PIN_STATE_SUPERSEDED = "SUPERSEDED"
PIN_STATE_INVALID = "INVALID"
PIN_STATE_MISSING = "MISSING"

# The ability whose revisions the bundle pins, and the file it declares its
# payload path in. Named rather than inlined so the fixture can point it at its
# own synthetic registry.
PINNED_ABILITY = "ProfessorZhi/ability-management"
ABILITY_MANIFEST = "abilities-manifest.yaml"

# The three questions a release has to answer. Every one of them must PASS
# before this tool will say RELEASE_COMPLETE — "the bytes match" is only the
# first.
AXIS_CHAIN = "CHAIN_CONSISTENT"
AXIS_PROVENANCE = "CANONICAL_PROVENANCE"
AXIS_MATERIALIZATION = "MATERIALIZATION_UNIQUE"
AXES = (AXIS_CHAIN, AXIS_PROVENANCE, AXIS_MATERIALIZATION)

PASS = "PASS"
FAIL = "FAIL"
RELEASE_COMPLETE = "RELEASE_COMPLETE"
RELEASE_INCOMPLETE = "RELEASE_INCOMPLETE"


# --------------------------------------------------------------------------
# primitives
# --------------------------------------------------------------------------

def _load_build(serveros_repo: Path):
    """Import build.py so this file reuses ITS definition of artifact identity.

    Reimplementing the expected file set here would create a second definition
    of "what should be installed", and two definitions of one thing is the
    failure mode this whole checkpoint is about. `plan()` is the authority.

    Loaded from `serveros_repo` rather than from this file's own directory, so
    that the answer describes the CANONICAL source even when this script is run
    from a worktree. `build.repo_root()` then resolves to that checkout, which
    is what makes the recomputation meaningful — it must read what canonical
    says, not what the caller happens to have on disk.
    """
    path = serveros_repo / "scripts/build.py"
    spec = importlib.util.spec_from_file_location("serveros_build", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _git(repo: Path, *args: str) -> str | None:
    try:
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode(errors="replace").strip()


def checkout_state(repo: Path) -> tuple[bool, list[str]]:
    """(is_a_git_checkout, uncommitted_paths).

    A missing repository is a fact to REPORT, not an exception to raise. The
    first version of this file called `.splitlines()` on the None that `_git`
    returns for a non-repo and crashed — an inspector that dies on an unexpected
    machine tells you nothing about that machine.
    """
    status = _git(repo, "status", "--porcelain")
    if status is None:
        return False, []
    return True, status.splitlines()


def _iter_files(root: Path):
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in EXCLUDED_DIRS)
        for name in sorted(files):
            if Path(name).suffix in EXCLUDED_SUFFIXES:
                continue
            path = Path(current) / name
            if path.is_symlink():
                continue
            yield path.relative_to(root), path


def tree_hash(root: Path) -> str | None:
    """Deterministic content hash of a directory tree.

    Hash of sorted (relative path, sha256 of bytes) pairs — so it is stable
    across machines, mtimes, and walk order, and two trees that differ only in
    a path name or a byte produce different values.
    """
    if not root.is_dir():
        return None
    digest = hashlib.sha256()
    found = False
    for rel, path in _iter_files(root):
        found = True
        digest.update(str(rel).encode())
        digest.update(b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).hexdigest().encode())
        digest.update(b"\n")
    return digest.hexdigest() if found else None


def tree_drift(expected: dict[Path, bytes], root: Path) -> list[str]:
    """Name every difference between an expected file set and a directory.

    Content, not just presence: an installed copy with the right file names and
    stale bytes is exactly the failure this module exists to catch, and a
    presence-only check would call it clean.
    """
    if not root.is_dir():
        return [f"missing tree {root}"]
    reasons: list[str] = []
    actual = {rel for rel, _ in _iter_files(root)}
    expected_rels = set(expected)
    reasons += [f"missing {rel}" for rel in sorted(expected_rels - actual)]
    reasons += [f"unexpected {rel}" for rel in sorted(actual - expected_rels)]
    for rel in sorted(expected_rels & actual):
        if (root / rel).read_bytes() != expected[rel]:
            reasons.append(f"content {rel}")
    return reasons


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------

def read_claude_install(plugins_json: Path) -> dict:
    """Claude's own record of what is installed and where it was installed from."""
    if not plugins_json.is_file():
        return {}
    try:
        data = json.loads(plugins_json.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    entries = data.get("plugins", {}).get(CLAUDE_PLUGIN_KEY) or []
    return entries[0] if entries else {}


def read_codex_install(config_toml: Path) -> dict:
    """Codex records activation in config.toml and materializes into a cache dir.

    Parsed by hand rather than with tomllib/tomli: this must run under the same
    interpreter that runs the rest of these scripts, and the two keys needed are
    unambiguous single-line assignments. A full TOML parser would be a new
    dependency for two string reads.
    """
    out = {"enabled": False, "marketplace_source": None}
    if not config_toml.is_file():
        return out
    section = None
    for raw in config_toml.read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section == f"marketplaces.{CODEX_MARKETPLACE}" and line.startswith("source"):
            out["marketplace_source"] = line.split("=", 1)[1].strip().strip('"')
        if section == f'plugins."{BUNDLE_NAME}@{CODEX_MARKETPLACE}"' and line.startswith("enabled"):
            out["enabled"] = line.split("=", 1)[1].strip() == "true"
    return out


def composition_files(build) -> dict[str, Path]:
    """Every composition this repository declares, by composition name."""
    root = build.repo_root()
    found: dict[str, Path] = {}
    candidates = [root / "plugin/bundle.yaml"]
    candidates += sorted((root / "plugin/profiles").glob("*.yaml"))
    for path in candidates:
        if not path.is_file():
            continue
        loaded = yaml.safe_load(path.read_text()) or {}
        name = loaded.get("composition")
        if name:
            found[name] = path
    return found


def expected_artifact(build, ability_repo: Path, ability_rev: str, output: Path,
                      marketplace_path: Path) -> tuple[dict[Path, bytes], str] | None:
    """Recompute the artifact from current canonical source + the recorded pin.

    This is what makes the question answerable without the build recording its
    own source revision. If the recomputed bytes equal the installed bytes, the
    installed runtime *is* current canonical; if they differ, the difference is
    named path by path. Either way the answer is derived, not asserted.

    The composition is read from the artifact rather than assumed to be core.
    Once the product has more than one composition, recomputing every artifact
    against `plugin/bundle.yaml` would accuse a correctly-built profile of
    drifting from a composition it never claimed — a false report produced by
    the checker, in the same shape as the false report it exists to catch.
    """
    composition = declared_composition(output)
    if not composition:
        return None
    path = composition_files(build).get(composition)
    if path is None:
        return None
    try:
        files, manifests, readme, marketplace, version = build.plan(
            path, ability_repo, ability_rev, output, marketplace_path)
    except (OSError, ValueError) as exc:
        return None
    payload = {rel: content for content, rel, _executable in files}
    payload.update({rel: build._json_bytes(value) for rel, value in manifests.items()})
    payload[Path("README.md")] = readme.encode()
    return payload, version


def recompute_blocker(build, output: Path) -> str:
    """Why the artifact could not be recomputed, named rather than generalized.

    "Cannot verify" is true but not actionable. Every reason this function can
    name is a specific missing or unreadable declaration, so the report says
    which one instead of leaving the reader to guess.
    """
    if not (output / "README.md").is_file():
        return "missing README.md (it carries the composition declaration)"
    composition = declared_composition(output)
    if not composition:
        return "README.md declares no composition"
    if composition not in composition_files(build):
        return f"no composition file declares `{composition}`"
    return "artifact not recomputable"


COMPOSITION_PATTERN = re.compile(r"^Composition: `([^`]+)`\.", re.MULTILINE)


def declared_composition(artifact: Path) -> str | None:
    """The composition the artifact itself says it is.

    Read from the generated README for the same reason the pin is: the claim
    travels with the bytes it describes, so recomputing against it is a check of
    the artifact rather than a restatement of the checker's own assumption.
    """
    readme = artifact / "README.md"
    if not readme.is_file():
        return None
    match = COMPOSITION_PATTERN.search(readme.read_text(errors="replace"))
    return match.group(1) if match else None


PIN_PATTERN = re.compile(r"ability-management@([0-9a-f]{40})")


def declared_pin(artifact: Path) -> str:
    """The pin the artifact itself says it was built from.

    The install record is NOT a reliable source for this. Claude's
    `gitCommitSha` is carried forward from the previous install when the
    marketplace source is a local path, because there is no git revision for it
    to re-read. On 2026-09-11 that record still named `972a872` after an update
    that had installed `82777ad` bytes — the machine's own account of itself
    being wrong, which is the same pathology this file exists to catch, one
    level further in. Worse, it made the pin row accuse a correct artifact of
    drifting from a revision it had never claimed.

    `build.py` writes the pin into the generated README, so the claim travels
    with the bytes it describes. Using it is not circular: the pin is then
    verified twice over — rebuilding from it must reproduce these exact bytes
    (CHAIN_CONSISTENT), and it must be reachable from canonical
    (CANONICAL_PROVENANCE).
    """
    readme = artifact / "README.md"
    if not readme.is_file():
        return ""
    try:
        found = PIN_PATTERN.search(readme.read_text(errors="replace"))
    except OSError:
        return ""
    return found.group(1) if found else ""


def _git_object_at(ability_repo: Path, ref: str, path: str):
    """`<ref>:<path>` 的对象类型, 不存在则 None。读的是 git 对象, 不是工作树 ——
    工作树可能脏、可能在别的分支, 用它会得到"此刻磁盘上恰好是什么"而不是"那个 revision
    是什么"。判定 provenance 的每一步都以 ref 为准。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(ability_repo), "cat-file", "-t", f"{ref}:{path}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return proc.stdout.decode(errors="replace").strip() or None


def _ability_payload_root(ability_repo: Path, ref: str, ability_id: str):
    """canonical 在 `ref` 上给这个 ability 的 payload 根(与 registry 的 `_payload_root` 同规则:
    优先 `<path>/payload`, 否则整个 `<path>`)。找不到该 ability 返回 None。"""
    try:
        proc = subprocess.run(
            ["git", "-C", str(ability_repo), "show", f"{ref}:{ABILITY_MANIFEST}"],
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    try:
        doc = yaml.safe_load(proc.stdout.decode("utf-8", "replace")) or {}
    except yaml.YAMLError:
        return None
    for entry in doc.get("abilities") or []:
        if (entry.get("id") or entry.get("name")) != ability_id:
            continue
        base = (entry.get("path") or "").strip("/")
        if not base:
            return None
        return f"{base}/payload" if _git_object_at(ability_repo, ref, f"{base}/payload") == "tree" else base
    return None


def pin_superseded(ability_repo: Path, pin: str, canonical_head: str,
                   ability_id: str = PINNED_ABILITY):
    """canonical head 上这个 ability 的 payload 是否已经和 pin 里的不是同一份?

    True  = 变了(superseded)/ False = 没变; None = 判不了。三态的理由和别处一致:
    判不了不是"没问题", 默认 unknown -> not superseded 会让检查器开始撒谎。

    判据是内容不是提交序: `git diff --quiet` 覆盖的正是 registry 做指纹的那个目录。
    只比 commit 先后会把"更早的提交、同样的内容"报成 superseded —— 那是一次完全
    合法的发布, 而一个会在正确机器上报警的检查, 第三次就会被关掉。
    """
    root = _ability_payload_root(ability_repo, canonical_head, ability_id)
    if root is None:
        return None
    try:
        proc = subprocess.run(
            ["git", "-C", str(ability_repo), "diff", "--quiet", pin, canonical_head, "--", root],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return None
    if proc.returncode == 0:
        return False
    if proc.returncode == 1:
        return True
    return None


def canonical_provenance(ability_repo: Path, pin: str, canonical_ref: str) -> dict:
    """Is the pinned build revision contained in the canonical branch?

    Reachability, not equality. `pin == canonical head` and `pin == an older
    commit on canonical` are both legitimate releases; only a pin that canonical
    does not contain at all means the artifact was built from something that
    will not survive a branch being rebased or retired.

    `reachable` is a tri-state on purpose. `None` means "could not determine",
    and the axis treats that as NOT passing — an unverifiable pin is not a
    verified one, and defaulting an unknown to PASS is how a checker starts
    lying.
    """
    out = {"pin": pin, "canonical_ref": canonical_ref, "reachable": None,
           "state": PIN_STATE_MISSING, "drift": "", "detail": ""}
    if not pin:
        # 没有 pin 和"pin 指向 canonical 之外"是两件事: 前者是没记, 后者是记错了。
        # 混成一个词会让一句"artifact 与 canonical 不符"覆盖一个"artifact 什么都没说"。
        out["drift"] = PIN_MISSING
        out["detail"] = ("no full 40-hex pin recorded, so the revision the artifact "
                         "was built from is unknown")
        return out
    if not (ability_repo / ".git").exists():
        out["detail"] = f"not a git checkout: {ability_repo}"
        return out
    head = _git(ability_repo, "rev-parse", canonical_ref)
    if head is None:
        out["detail"] = f"no such ref in {ability_repo}: {canonical_ref}"
        return out
    out["canonical_head"] = head
    if _git(ability_repo, "cat-file", "-t", pin) is None:
        out["reachable"] = False
        out["state"] = PIN_STATE_INVALID
        out["drift"] = PIN_NOT_CANONICAL
        out["detail"] = f"pinned revision {pin[:12]} does not exist in {ability_repo}"
        return out
    try:
        proc = subprocess.run(
            ["git", "-C", str(ability_repo), "merge-base", "--is-ancestor", pin, canonical_ref],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        out["detail"] = "git merge-base could not be run"
        return out
    if proc.returncode == 0:
        out["reachable"] = True
        if head == pin:
            out["state"] = PIN_STATE_CURRENT
            out["detail"] = f"pin is exactly {canonical_ref}"
            return out
        behind = _git(ability_repo, "rev-list", "--count", f"{pin}..{canonical_ref}") or "?"
        # pin 比 head 老不是缺陷 —— 但那不等于"这个发布就是当前发布"。两个问题分开问:
        # 老提交 + 同样的 payload = 一次合法的、内容仍然当前的发布;
        # 老提交 + 变了的 payload = canonical 已经发布过更新的 revision。
        moved = pin_superseded(ability_repo, pin, head)
        if moved is None:
            out["state"] = PIN_STATE_HISTORICAL
            out["detail"] = (f"pin is on {canonical_ref}, {behind} commit(s) behind its head; "
                             "the ability's payload could not be compared against head, so "
                             "whether it is still current was NOT established")
        elif moved:
            out["state"] = PIN_STATE_SUPERSEDED
            out["drift"] = PIN_SUPERSEDED
            out["detail"] = (f"pin is on {canonical_ref}, {behind} commit(s) behind its head, "
                             "and the ability's payload has changed since — canonical has "
                             "published a newer revision. The pinned bytes are real and "
                             "reproducible; they are just not the ones to ship")
        else:
            out["state"] = PIN_STATE_HISTORICAL
            out["detail"] = (f"pin is on {canonical_ref}, {behind} commit(s) behind its head, "
                             "but the ability's payload is unchanged since the pin — an older "
                             "commit carrying the current content; not a defect")
        return out
    if proc.returncode == 1:
        out["reachable"] = False
        out["state"] = PIN_STATE_INVALID
        out["drift"] = PIN_NOT_CANONICAL
        ahead = _git(ability_repo, "rev-list", "--count", f"{canonical_ref}..{pin}") or "?"
        out["detail"] = (f"pinned revision {pin[:12]} is NOT reachable from {canonical_ref} "
                         f"({ahead} commit(s) outside canonical) — the artifact was built "
                         "from a revision canonical does not contain")
        return out
    out["detail"] = f"git merge-base exited {proc.returncode}"
    return out


def materialization_audit(artifact: Path, runtimes: list[tuple[str, Path | None, Path | None]]) -> dict:
    """How many times does each bundle member reach each runtime?

    Ground truth is the filesystem, not any planner. Asking the installed
    control plane what it *would* do would reproduce its contract — including
    the bug — rather than test it; the pre-fix planner cheerfully reported
    `INSTALL` for members it was about to flatten, which is exactly the plan
    that was wrong.

    A flat copy that is byte-identical to the plugin member is a genuine second
    materialization and fails the axis. A flat skill of the same NAME with
    different content is reported as `NAME_COLLISION` instead: the name resolves
    to two different artifacts, which is worth naming, but it is not this
    contract's violation — nothing was duplicated. Conflating the two would
    either force this axis permanently red on a known deferred item, or tempt
    someone to exempt that item by hardcoding its name.
    """
    members_dir = artifact / "skills"
    if not members_dir.is_dir():
        return {"verdict": UNKNOWN, "members": [], "duplicates": [], "collisions": [],
                "notes": [f"no skills/ directory in the artifact at {artifact}"],
                "detail": "cannot enumerate bundle members"}
    members = sorted(p.name for p in members_dir.iterdir() if p.is_dir())
    duplicates: list[dict] = []
    collisions: list[dict] = []
    notes: list[str] = []
    for label, payload, flat_root in runtimes:
        if payload is None or not Path(payload).is_dir():
            notes.append(f"{label}: plugin payload not installed, so no member can "
                         "be materialized a second time there")
            continue
        flat_root = Path(flat_root) if flat_root else None
        if flat_root is None or not flat_root.is_dir():
            notes.append(f"{label}: no runtime skill root at {flat_root}")
            continue
        for member in members:
            flat = flat_root / member
            if not (flat / "SKILL.md").is_file():
                continue
            plugin_copy = Path(payload) / "skills" / member
            record = {"runtime": label, "member": member, "path": str(flat)}
            if tree_hash(flat) == tree_hash(plugin_copy):
                record["detail"] = ("flat copy is byte-identical to the plugin member — "
                                    "a second active materialization of the same thing")
                duplicates.append(record)
            else:
                record["detail"] = ("a skill of this name sits at the runtime root with "
                                    "DIFFERENT content; the name resolves to two artifacts "
                                    "(not a duplicate, so not counted against this axis)")
                collisions.append(record)
    if duplicates:
        detail = (f"{len(duplicates)} member(s) materialized twice: "
                  + ", ".join(f"{d['member']}@{d['runtime']}" for d in duplicates[:4]))
    else:
        detail = f"{len(members)} member(s), each reaching every installed runtime once"
    return {"verdict": PASS if not duplicates else FAIL, "members": members,
            "duplicates": duplicates, "collisions": collisions, "notes": notes,
            "detail": detail}


# --------------------------------------------------------------------------
# report
# --------------------------------------------------------------------------

def collect(args) -> dict:
    build = _load_build(args.serveros_repo)
    rows: list[dict] = []

    def row(component, canonical, built, installed, verdict, detail="", note="", axis=None):
        rows.append({"component": component, "canonical": canonical,
                     "built": built, "installed": installed,
                     "verdict": verdict, "detail": detail, "note": note, "axis": axis})

    # --- 1. the bundle source repository --------------------------------
    serveros_head = _git(build.repo_root(), "rev-parse", "HEAD") or UNKNOWN
    serveros_is_repo, serveros_dirty = checkout_state(build.repo_root())
    if not serveros_is_repo:
        s_verdict, s_detail = UNKNOWN, f"not a git checkout: {build.repo_root()}"
    elif serveros_dirty:
        s_verdict = UNKNOWN
        s_detail = (f"{len(serveros_dirty)} uncommitted path(s) — the installed runtime "
                    "cannot be attributed to a clean revision")
    else:
        s_verdict, s_detail = OK, ""
    row("serveros (bundle source)", serveros_head[:12], "", f"{build.repo_root()}",
        s_verdict, s_detail)

    # --- 2. the control plane repository --------------------------------
    engine_head = _git(args.engine_repo, "rev-parse", "HEAD") or UNKNOWN
    engine_is_repo, engine_dirty = checkout_state(args.engine_repo)
    if not engine_is_repo:
        e_verdict, e_detail = UNKNOWN, f"not a git checkout: {args.engine_repo}"
    elif engine_dirty:
        e_verdict = UNKNOWN
        e_detail = f"{len(engine_dirty)} uncommitted path(s)"
    else:
        e_verdict, e_detail = OK, ""
    row("work-engine (canonical)", engine_head[:12], "", "", e_verdict, e_detail)

    # --- 3. installed workengine vs canonical workengine ----------------
    installed_engine = args.engine_installed
    engine_verdict, engine_detail = UNKNOWN, ""
    if not installed_engine.is_dir():
        engine_verdict = MISSING
        engine_detail = f"no installed package at {installed_engine}"
    else:
        inst_hash = tree_hash(installed_engine)
        canon_hash = tree_hash(args.engine_repo / "workengine")
        if canon_hash is None:
            engine_detail = "canonical workengine/ not found"
        elif inst_hash == canon_hash:
            engine_verdict = OK
        else:
            engine_verdict = SOURCE_RUNTIME_DRIFT
            diffs = tree_drift({rel: (args.engine_repo / "workengine" / rel).read_bytes()
                                for rel, _ in _iter_files(args.engine_repo / "workengine")},
                               installed_engine)
            # Only report the source-side files; an installed copy may simply be
            # older, in which case "unexpected" files are the older set and the
            # missing ones are the whole story.
            named = [d for d in diffs if d.startswith("missing")]
            engine_detail = (f"installed != canonical ({len(named)} missing path(s)"
                             f"{'; e.g. ' + ', '.join(named[:3]) if named else ''})")
    row("workengine (installed)", engine_head[:12], "",
        f"{installed_engine}  sha256={tree_hash(installed_engine) or 'NONE'}",
        engine_verdict, engine_detail)

    # --- 4/5/6. the artifact, at each hop of the chain -------------------
    claude = read_claude_install(args.claude_plugins_json)
    codex = read_codex_install(args.codex_config)

    recorded_pin = claude.get("gitCommitSha") or ""
    declared = declared_pin(args.output)
    ability_rev = args.ability_rev or declared or recorded_pin
    pinned = ability_rev if len(ability_rev) == 40 else ""
    record_note = ""
    record_class = None
    record_why = ""
    if declared and recorded_pin and declared != recorded_pin:
        # Named, not blocking: this is the install record being wrong about the
        # artifact, which nothing in this repository can repair (a `local`
        # marketplace source gives Claude no revision to record). Making it
        # block would leave RELEASE_COMPLETE unreachable on a machine that is
        # in fact correct, and a gate that can never pass gets ignored.
        record_class = "INSTALL_RECORD_PROVENANCE_DRIFT"
        record_note = (f"install record claims {recorded_pin[:12]} but the artifact was "
                       f"built from {declared[:12]} — the record is stale, the bytes are not")
        # Why the record lags, established by reading what the platform was
        # given rather than by assuming: the marketplace source is a directory,
        # so there is no revision for it to re-read on update. Claude carries
        # the first-install value forward. Nothing in this control plane writes
        # `gitCommitSha` -- it is read here and nowhere written -- so this is
        # platform record semantics, and the repair is in the reading, not in
        # the file. Editing installed_plugins.json by hand would make the record
        # agree with the artifact while destroying its value as an independent
        # observation of what the platform actually recorded.
        record_why = (
            "marketplace source is a directory (source: directory); an update "
            "installs new bytes but has no git revision to re-read, so the "
            "platform carries the first-install value forward. Nothing in the "
            "ServerOS control plane writes this field.")
    provenance = canonical_provenance(
        args.ability_repo, pinned, getattr(args, "canonical_ref", DEFAULT_CANONICAL_REF))
    # The pin row used to read OK whenever a 40-hex revision was present, which
    # is a statement about the FORMAT of the pin, not about the pin. It now
    # carries the reachability answer, because "the pin is well-formed" and
    # "the pin points into canonical" were being read as the same row.
    # 行的 verdict 说"这个 pin 本身是不是有效的 canonical revision"; superseded 是一个
    # 有效的 revision, 所以它在行里仍是 OK, 由 note 和 axis 的 state 说出来。把 superseded
    # 降级成失败会让一次完全可复现的发布看起来像坏了。
    if provenance["reachable"] is None:
        pin_verdict, pin_detail = UNKNOWN, provenance["detail"]
    elif provenance["reachable"]:
        pin_verdict, pin_detail = OK, provenance["detail"]
    else:
        pin_verdict, pin_detail = PIN_NOT_CANONICAL, provenance["detail"]
    if provenance.get("state") == PIN_STATE_SUPERSEDED:
        pin_detail = f"[{PIN_STATE_SUPERSEDED}] " + pin_detail
    row("ability-management (build pin)", pinned[:12] or UNKNOWN, declared[:12] or pinned[:12],
        recorded_pin[:12] or UNKNOWN,
        pin_verdict, pin_detail, note=record_note, axis=AXIS_PROVENANCE)

    built_ver = installed_ver = UNKNOWN
    manifest_path = args.output / ".claude-plugin/plugin.json"
    if manifest_path.is_file():
        try:
            built_ver = json.loads(manifest_path.read_text()).get("version", UNKNOWN)
        except (OSError, json.JSONDecodeError):
            built_ver = UNKNOWN
    installed_ver = claude.get("version", UNKNOWN)

    row(f"{BUNDLE_NAME} (bundle identity)", "", built_ver, installed_ver,
        OK if built_ver == installed_ver and built_ver != UNKNOWN else SOURCE_RUNTIME_DRIFT,
        "" if built_ver == installed_ver else
        f"runtime-assets built {built_ver} but Claude has {installed_ver} installed")

    # --- 7. does the installed payload equal what canonical builds? ------
    expected = None
    if pinned and args.ability_repo.is_dir():
        got = expected_artifact(build, args.ability_repo, pinned, args.output,
                                args.marketplace_path)
        if got:
            expected, recomputed_version = got
            row(f"{BUNDLE_NAME} (payload reproducibility)", recomputed_version, "",
                installed_ver, OK if recomputed_version == built_ver else SOURCE_RUNTIME_DRIFT,
                "" if recomputed_version == built_ver else
                f"canonical source builds {recomputed_version}, disk has {built_ver} "
                "— the artifact is stale relative to source")

    # The two runtimes nest differently and neither is guessable from the other:
    # Claude's install record gives the payload leaf directly, while Codex adds
    # <cache>/<marketplace>/<plugin>/<version>/. Both levels were gotten wrong
    # once here — first a false MISSING, then a false drift — which is the
    # argument for resolving the Codex leaf by listing rather than by pattern.
    codex_leaf, codex_note = None, ""
    codex_plugin_dir = args.codex_cache / CODEX_MARKETPLACE / BUNDLE_NAME
    if codex_plugin_dir.is_dir():
        versions = sorted(p for p in codex_plugin_dir.iterdir() if p.is_dir())
        if len(versions) == 1:
            codex_leaf = versions[0]
        elif versions:
            # Codex is documented as single-slot: an old generation sitting
            # beside a new one is a fact worth naming, not silently taking the
            # newest of.
            codex_leaf = versions[-1]
            codex_note = (f"{len(versions)} version dirs present "
                          f"({', '.join(p.name for p in versions)}); Codex is single-slot")

    targets = [("runtime-assets (built)", args.output),
               ("Claude plugin cache", Path(claude.get("installPath", "")) if claude.get("installPath") else None),
               ("Codex plugin cache", codex_leaf)]
    for label, root in targets:
        if root is None or not Path(root).is_dir():
            row(f"{BUNDLE_NAME} @ {label}", "", "", MISSING, MISSING, f"not present: {root}")
            continue
        root = Path(root)
        if expected is None:
            row(f"{BUNDLE_NAME} @ {label}", "", "", f"sha256={tree_hash(root) or 'NONE'}",
                UNKNOWN, "cannot verify: " + recompute_blocker(build, args.output))
            continue
        drift = tree_drift(expected, root)
        # Core modules are named separately because they are the specific thing
        # this checkpoint exists to land — but only as a NOTE. Whether the
        # absence is a defect depends on whether canonical has them at all, and
        # that is `drift`'s job. Conflating the two made a matching install read
        # as a failure on the first run of this file.
        absent = [m for m in build.CORE_MODULES if not (root / m).is_dir()]
        note = f"core module(s) absent: {', '.join(absent)}" if absent else ""
        if codex_note and label.startswith("Codex"):
            note = f"{note}; {codex_note}" if note else codex_note
        verdict = OK if not drift else SOURCE_RUNTIME_DRIFT
        detail = f"{len(drift)} drifted path(s); e.g. {', '.join(drift[:3])}" if drift else ""
        row(f"{BUNDLE_NAME} @ {label}", "", "", f"sha256={tree_hash(root) or 'NONE'}",
            verdict, detail, note)

    # The chain verdict answers only "do the bytes agree at every hop". The pin
    # row is excluded because reachability is a different question, answered on
    # its own axis below; letting it leak into the chain verdict would make a
    # perfectly consistent chain read as UNKNOWN for a reason that has nothing
    # to do with consistency.
    chain_verdicts = {r["verdict"] for r in rows if r["axis"] != AXIS_PROVENANCE}
    if SOURCE_RUNTIME_DRIFT in chain_verdicts:
        overall = SOURCE_RUNTIME_DRIFT
    elif chain_verdicts <= {OK}:
        overall = OK
    elif MISSING in chain_verdicts:
        overall = MISSING
    else:
        overall = UNKNOWN

    claude_skills = getattr(args, "claude_skills_root", None)
    codex_skills = getattr(args, "codex_skills_root", None)
    materialization = materialization_audit(args.output, [
        ("Claude", claude.get("installPath") or None, claude_skills),
        ("Codex", codex_leaf, codex_skills),
    ])

    axes = {
        AXIS_CHAIN: {
            "verdict": PASS if overall == OK else FAIL,
            "detail": ("source, built and installed bytes agree at every hop"
                       if overall == OK else f"chain verdict is {overall}"),
        },
        AXIS_PROVENANCE: {
            # reachable=True 覆盖 CURRENT / VALID_HISTORICAL / SUPERSEDED 三态:
            # 三者都指向 canonical 里真实存在、可重建的 revision。区别在于"是不是
            # 当前该发的那个", 那是 state 的问题, 不是这个轴成败的问题。
            "verdict": PASS if provenance["reachable"] else FAIL,
            "state": provenance.get("state", PIN_STATE_MISSING),
            "detail": provenance["detail"] or "pin reachability undetermined",
            "drift": provenance["drift"],
        },
        AXIS_MATERIALIZATION: {
            "verdict": materialization["verdict"],
            "detail": materialization["detail"],
        },
    }
    blocking = [name for name in AXES if axes[name]["verdict"] != PASS]

    return {"schema": "serveros/runtime-truth.v2", "verdict": overall, "rows": rows,
            "release": {
                "verdict": RELEASE_COMPLETE if not blocking else RELEASE_INCOMPLETE,
                "axes": axes, "blocking": blocking,
                "materialization": materialization,
                "pin": {"used": pinned, "declared": declared, "recorded": recorded_pin,
                        "state": provenance.get("state", PIN_STATE_MISSING),
                        "record_agrees": declared == recorded_pin if declared else None,
                        # The artifact is authority; the platform's install
                        # record is an observation OF the artifact. When they
                        # disagree, the observation is what is wrong -- and it is
                        # still worth reporting, because a record that silently
                        # disagrees with reality is how a future reader gets
                        # misled.
                        "record_authority": "artifact",
                        "record_is_observation": True,
                        "record_class": record_class,
                        "record_why": record_why,
                        "not_to_be_confused_with": {
                            "PIN_SUPERSEDED":
                                "reported separately, in `state`: a pin that is a real, "
                                "canonical, reproducible revision which canonical has "
                                "since superseded. It is not a disagreement between the "
                                "artifact and this record — both can be right"}},
            },
            "codex": {"enabled": codex.get("enabled"),
                      "marketplace_source": codex.get("marketplace_source")}}


def render(report: dict) -> None:
    width = max(len(r["component"]) for r in report["rows"])
    print(f"{'component':<{width}}  {'canonical':<14} {'built':<24} {'installed':<44} verdict")
    for r in report["rows"]:
        print(f"{r['component']:<{width}}  {r['canonical']:<14} {r['built']:<24} "
              f"{r['installed']:<44} {r['verdict']}")
        if r["detail"]:
            print(f"{'':<{width}}  !! {r['detail']}")
        if r.get("note"):
            print(f"{'':<{width}}  -- {r['note']}")
    print()
    print(f"VERDICT: {report['verdict']}")

    release = report.get("release")
    if not release:
        return
    print()
    for name in AXES:
        axis = release["axes"][name]
        print(f"  {name:<22} {axis['verdict']:<5} {axis['detail']}")
    for entry in release["materialization"]["collisions"]:
        # Not a materialization failure, but the name resolves to two different
        # artifacts and that should never scroll past unread.
        print(f"  -- {NAME_COLLISION}: {entry['member']} @ {entry['runtime']} "
              f"({entry['path']}) differs from the plugin member")
    print()
    print(f"RELEASE: {release['verdict']}"
          + (f"  (blocking: {', '.join(release['blocking'])})" if release["blocking"] else ""))


def main() -> int:
    home = Path(os.path.expanduser("~"))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serveros-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/src/serveros"),
                    help="canonical bundle-source checkout whose build.py defines "
                         "what the artifact should contain")
    ap.add_argument("--ability-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_ABILITY_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/src/ability-management"))
    ap.add_argument("--ability-rev", default="",
                    help="build pin; default = the revision Claude's install record carries")
    ap.add_argument("--output", type=Path,
                    default=Path(os.environ.get("SERVEROS_OUTPUT")
                                 or "/opt/applications/data/home-hwz/runtime-assets/hwz-server-os/current"))
    ap.add_argument("--marketplace-path", type=Path,
                    default=Path(os.environ.get("SERVEROS_MARKETPLACE")
                                 or "/opt/applications/data/home-hwz/runtime-assets/hwz-server-os/.agents/plugins/marketplace.json"))
    ap.add_argument("--engine-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_ENGINE_REPO")
                                 or "/opt/applications/data/home-hwz/workspace/src/work-engine"))
    ap.add_argument("--engine-installed", type=Path,
                    default=Path(os.environ.get("SERVEROS_ENGINE_INSTALLED")
                                 or "/opt/applications/data/home-hwz/envs/python/serveros-control-plane/lib/python3.12/site-packages/workengine"))
    ap.add_argument("--claude-plugins-json", type=Path,
                    default=Path(os.environ.get("SERVEROS_CLAUDE_PLUGINS_JSON")
                                 or home / ".claude/plugins/installed_plugins.json"))
    ap.add_argument("--codex-config", type=Path,
                    default=Path(os.environ.get("SERVEROS_CODEX_CONFIG") or home / ".codex/config.toml"))
    ap.add_argument("--codex-cache", type=Path,
                    default=Path(os.environ.get("SERVEROS_CODEX_CACHE") or home / ".codex/plugins/cache"))
    ap.add_argument("--canonical-ref", default=os.environ.get("SERVEROS_CANONICAL_REF")
                    or DEFAULT_CANONICAL_REF,
                    help="the branch the build pin must be reachable from for the "
                         "release to be reproducible; reachability, not equality")
    ap.add_argument("--claude-skills-root", type=Path,
                    default=Path(os.environ.get("SERVEROS_CLAUDE_SKILLS")
                                 or home / ".claude/skills"),
                    help="runtime skill root that plugin-native discovery makes "
                         "redundant; a bundle member here is a second materialization")
    ap.add_argument("--codex-skills-root", type=Path,
                    default=Path(os.environ.get("SERVEROS_CODEX_SKILLS")
                                 or home / ".codex/skills"))
    args = ap.parse_args()

    report = collect(args)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        render(report)
    # Exit reflects RELEASE_COMPLETE, not merely "the chain agrees". The two
    # differed on 2026-09-11 and the difference was the whole point: a
    # chain-consistent, correctly-noncanonical release must not exit 0.
    return 0 if report["release"]["verdict"] == RELEASE_COMPLETE else 1


if __name__ == "__main__":
    raise SystemExit(main())
