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

The distribution is not the node
--------------------------------
Everything above is about an artifact: what a release contains, where it went,
and whether the bytes agree. None of it is about the thing this file's first
sentence promises — *is the runtime actually running the code we wrote* — and
until 2026-09-15 nothing here measured a runtime at all. The file's own history
made that easy to miss: it named `runtime-assets/hwz-server-os/current` and
called it "the installed runtime", when that path is the **plugin distribution**
a marketplace installs from. The node's own runtime is a different composition
at a different path, reached through a symlink this file never opened, started
by a unit it never read, in a process it never looked for. The reference to
`git rev-parse` in the paragraph below was, in the same way, a claim about a
subprocess that no longer matched the code around it — a small lie of exactly
the kind this file spends its length warning about.

So the node is measured here on its own terms, and the three facts are kept
apart because they have three different repairs:

    DECLARATION   what the unit file says will be started. Read as data, not
                  asked of systemd: the question is what a supervisor would do,
                  and the unit file is where that is written down.

    EXECUTION     which of this machine's live processes actually runs that
                  program. Answered from `/proc`, by exact argument match —
                  never by substring, because a substring match finds the shell
                  that ran the search. That is not hypothetical: the first draft
                  of this scan reported its own shell as the running daemon.

    REVISION      which revision directory that program lives in, and whether
                  its bytes are the ones canonical source builds today.

A node can be right about any one of those and wrong about the others, and the
repairs do not transfer: a service running a superseded revision needs a
restart, a unit pointing at a retired revision needs a re-render, and an
artifact whose bytes no longer match canonical needs a rebuild. Collapsing them
into one word would name the symptom and hide which of the three to do.

What it does NOT do
-------------------
No network. No LLM. No database. No full-disk scan. No mutation of any kind —
every path below is opened read-only, the subprocesses it runs read `git`
objects and nothing else, and it never asks systemd anything: a running unit is
a fact in `/proc`, and a supervisor's intention is a fact in a unit file. Both
are files. Output contains no clock reading, so two runs over an unchanged
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
MISSING = "MISSING"
UNKNOWN = "UNKNOWN"

# The node's own surface, and the three states it can be in. Each names a
# different repair, which is the whole reason there are three of them:
#
#   BUILT_RUNTIME_DRIFT  a live process is running a revision that is not the
#                        one `current` points at. `release.py promote` swaps
#                        the symlink and reloads units but does NOT restart the
#                        service, so this is what a promote looks like until
#                        somebody restarts — the running code is real, staged,
#                        and superseded. REPAIR: restart the node.
#
#   UNIT_STALE           the supervisor is configured to start a program that is
#                        not this composition's `current/bin/serverosd`. REPAIR:
#                        re-render the unit (re-promote, or roll back).
#
#   NOT_RUNNING          the unit declares the right program and nothing is
#                        executing it. REPAIR: start the node.
#
# This constant was declared and never used from the day it was written until
# 2026-09-15 — a name with no branch, in a file about names with no branches.
# It is used now because the situation it names turned out to be reachable, and
# reachable by the ordinary path: promote does not restart.
BUILT_RUNTIME_DRIFT = "BUILT_RUNTIME_DRIFT"
UNIT_STALE = "UNIT_STALE"
NOT_RUNNING = "NOT_RUNNING"

# The composition the NODE runs from, the supervisor that starts it, and the
# manifest `release.py promote` writes into a staged revision. These are not the
# plugin distribution: that is a different composition (`serveros-core`, built
# into the marketplace root) whose members a Claude or Codex install consumes.
# Both are measured; they are different things and this file used to call only
# one of them "the installed runtime".
NODE_COMPOSITION = "serveros-linux-work"
NODE_UNIT = "serverosd.service"
TREE_MANIFEST = "RELEASE.json"

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


def _load_release(serveros_repo: Path):
    """Import release.py so this file reuses ITS fingerprint recipe.

    A staged revision carries a `tree_hash` that `release.py promote` computed,
    and the only way to check that number is to recompute it the same way. Doing
    that here with a second function would produce two hashes over the same
    directory that are both correct and cannot be compared — the failure mode
    release.py's own `fingerprint` docstring is written about, and the one this
    module exists to catch. So the recipe is imported, not restated, for the
    same reason `build.plan()` is.
    """
    path = serveros_repo / "scripts/release.py"
    spec = importlib.util.spec_from_file_location("serveros_release", path)
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


def tree_drift(expected: dict[Path, bytes], root: Path,
               ignore: tuple[Path, ...] = ()) -> list[str]:
    """Name every difference between an expected file set and a directory.

    Content, not just presence: an installed copy with the right file names and
    stale bytes is exactly the failure this module exists to catch, and a
    presence-only check would call it clean.

    `ignore` is for the one directory that legitimately holds a file the
    artifact does not: a staged revision, into which `release.py promote` writes
    `RELEASE.json` after computing the hash that file records. Skipping it here
    is not skipping the check on it — that file is verified against the tree it
    describes by `node_revision`, which is a stronger question than "is it one
    of the expected members". The default is empty, so every existing caller
    keeps comparing whole trees.
    """
    if not root.is_dir():
        return [f"missing tree {root}"]
    skip = set(ignore)
    reasons: list[str] = []
    actual = {rel for rel, _ in _iter_files(root) if rel not in skip}
    expected_rels = set(expected)
    reasons += [f"missing {rel}" for rel in sorted((expected_rels - skip) - actual)]
    reasons += [f"unexpected {rel}" for rel in sorted(actual - expected_rels)]
    for rel in sorted((expected_rels - skip) & actual):
        if (root / rel).read_bytes() != expected[rel]:
            reasons.append(f"content {rel}")
    return reasons


# --------------------------------------------------------------------------
# the chain
# --------------------------------------------------------------------------

def read_claude_install(plugins_json: Path) -> dict:
    """Claude's own record of what is installed and where it was installed from."""
    return read_claude_plugins(plugins_json).get(CLAUDE_PLUGIN_KEY) or {}


def read_claude_plugins(plugins_json: Path) -> dict[str, dict]:
    """Every installed plugin Claude records, by its key.

    Keyed rather than returning one because "which plugin does this node run" is
    a per-composition fact and this file used to answer it with a literal. See
    `plugin_key`.
    """
    if not plugins_json.is_file():
        return {}
    try:
        data = json.loads(plugins_json.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    out: dict[str, dict] = {}
    for key, entries in (data.get("plugins") or {}).items():
        if isinstance(entries, list) and entries and isinstance(entries[0], dict):
            out[key] = entries[0]
    return out


def read_enabled_plugins(settings_json: Path) -> dict[str, bool]:
    """Which plugins a session on this node actually LOADS.

    Not the same question as the install record, and the install record cannot
    answer it: it has no enabled field, so a plugin that is installed and
    disabled is present there and in no session. Measured 2026-09-23 —
    `hwz-server-os@hwz-server-os` is in `installed_plugins.json` and `false` in
    `settings.json`'s `enabledPlugins` — which is exactly the state a migration
    leaves behind and exactly the state a presence check calls healthy.
    """
    if not settings_json.is_file():
        return {}
    try:
        data = json.loads(settings_json.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    enabled = data.get("enabledPlugins")
    return {k: bool(v) for k, v in enabled.items()} if isinstance(enabled, dict) else {}


def plugin_key(composition_name: str) -> str:
    """Claude's install-record key for a composition's plugin: `<name>@<name>`.

    Both halves are the composition's declared `name`, because `build.py` names
    the marketplace entry and the plugin entry from the bundle's own `name`. The
    key is therefore derivable from the composition rather than being a second
    literal that a rename has to remember.

    It was one. `CLAUDE_PLUGIN_KEY = "hwz-server-os@hwz-server-os"` named the
    core composition, and when this node migrated its default entry to the
    `serveros-linux-work` profile the literal went on naming the composition
    that had just been DISABLED. The report then carried a green row for a
    plugin no session loads and no row at all for the one every session loads —
    a checker measuring a subject the product had moved away from, which is the
    finding this whole file exists to produce one level down.
    """
    return f"{composition_name}@{composition_name}"


def composition_index(build) -> dict[str, dict]:
    """Each declared composition: composition name -> its plugin name and file.

    Keyed by the COMPOSITION, and carrying the plugin name beside it, because
    the two are different strings that coincide only for core. A composition is
    what the artifact's README declares and what `declared_composition()`
    returns; the plugin name is what Claude and Codex key their records on. They
    were conflated here for exactly one commit — the map was keyed by plugin
    name, the materialization audit looked it up by composition name, and the
    lookup silently missed for every composition except core.
    """
    out: dict[str, dict] = {}
    for composition, path in composition_files(build).items():
        try:
            document = yaml.safe_load(path.read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError):
            continue
        name = document.get("name") if isinstance(document, dict) else None
        if isinstance(name, str) and name:
            out[composition] = {"name": name, "path": path}
    return out


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


def read_codex_plugins(config_toml: Path) -> dict[str, bool]:
    """Every plugin Codex records as enabled, by `name@marketplace`.

    The counterpart of `read_enabled_plugins` for the other runtime, and it is
    read from the same kind of place: Codex keeps the switch in its config, not
    in the cache directory it materializes into. Both are needed to say whether
    a plugin is present AND loaded.
    """
    out: dict[str, bool] = {}
    if not config_toml.is_file():
        return out
    section = None
    for raw in config_toml.read_text().splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        if section is None or not section.startswith('plugins."'):
            continue
        if line.startswith("enabled"):
            out[section[len("plugins."):].strip('"')] = (
                line.split("=", 1)[1].strip() == "true")
    return out


def codex_marketplace_name(plugin_name: str) -> str:
    """Codex's marketplace name for a PLUGIN: `<name>-local`.

    The builder renders `f"{bundle['name']}-local"`, and `name` in a bundle is
    the plugin name — `hwz-server-os`, not `serveros-core`. Calling this with a
    composition name would produce `serveros-core-local`, which is a marketplace
    nothing has ever created. Mirrored rather than imported for the same reason
    `default_root` is: the two files ship from different roots. A test pins this
    against the builder's own output, so the mirror cannot drift without
    failing — which is what happened to the Claude plugin key, where the second
    copy was a literal nobody compared with anything.
    """
    return f"{plugin_name}-local"


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
                      marketplace_path: Path,
                      layout_at: Path | None = None) -> tuple[dict[Path, bytes], str] | None:
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

    `layout_at` is not a detail of the build, it is the repair of a check whose
    answer depended on where two unrelated directories happened to sit. `plan()`
    takes one output path and uses it twice: in `build()` it is where the
    payload is written, and in `plan()` it is only the plugin's position inside
    a Codex marketplace root, which `codex_marketplace` computes with
    `os.path.relpath` and REFUSES when it escapes. A node revision lives under
    the runtime root and is in no marketplace, so recomputing it against its own
    directory raises — and on this machine it did not, for no better reason than
    that `runtime-assets/` and the marketplace root share a parent. A verdict
    that flips when someone moves a directory is not a measurement. The
    marketplace file is written beside the marketplace, never into the artifact,
    so the value passed here reaches only a dictionary this function discards.
    """
    composition = declared_composition(output)
    if not composition:
        return None
    path = composition_files(build).get(composition)
    if path is None:
        return None
    try:
        files, manifests, readme, marketplace, version = build.plan(
            path, ability_repo, ability_rev, layout_at or output, marketplace_path)
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


def node_revision(runtime_root: Path, composition: str, release) -> dict:
    """What the node would start, read as data rather than asked of anything.

    Three separate facts, because they have three separate repairs:

      the symlink      `current` → `revisions/<label>`. A `current` that is not
                       a symlink is a node nobody promoted into; a symlink whose
                       target is gone is a node that cannot start.
      the manifest     `<rev>/RELEASE.json`, which records the tree hash of the
                       artifact AS STAGED. Verified by recomputing it over the
                       same directory minus the manifest itself — a file cannot
                       contain its own digest, so the comparison has to be
                       against the tree the number describes.
      the label        `main@0a98bb6` is a CLAIM made at promote time about
                       which source revision the bytes came from. It is reported
                       as a claim. Whether it is true is answered by
                       `expected_artifact`, which rebuilds from the pin in the
                       artifact's own README and compares bytes.

    Returns a state word in every case. "There is no node runtime here" and
    "there is one and it is broken" are different, and a caller that cannot tell
    them apart will either block a machine with no node or wave through one
    whose node is gone.
    """
    out = {"composition": composition, "root": str(runtime_root / composition),
           "state": "ABSENT", "current": None, "previous": None,
           "revision_dir": None, "manifest": None, "tree_hash_ok": None,
           "detail": ""}
    root = runtime_root / composition
    if not root.is_dir():
        out["detail"] = (f"no node runtime for composition {composition!r} at "
                         f"{root} — this machine has no installed node to measure")
        return out
    link = root / "current"
    if not link.is_symlink():
        out["state"] = "NOT_PROMOTED"
        out["detail"] = f"{link} is not a symlink; nothing has been promoted here"
        return out
    target = Path(os.readlink(link))
    out["current"] = target.name
    prev = root / "previous"
    if prev.is_symlink():
        out["previous"] = Path(os.readlink(prev)).name
    # Resolve to the real directory BEFORE deciding it is missing. A revision
    # may be reached through a relative link, and `revisions/<name>` is not the
    # only legal spelling of it.
    resolved = (link.parent / target).resolve() if not target.is_absolute() else target.resolve()
    if not resolved.is_dir():
        out["state"] = "REVISION_MISSING"
        out["detail"] = (f"current points at {target}, which does not exist — the "
                         "node would fail to start; roll back rather than promote")
        return out
    out["revision_dir"] = str(resolved)
    manifest_path = resolved / TREE_MANIFEST
    if manifest_path.is_file():
        try:
            out["manifest"] = json.loads(manifest_path.read_text())
        except (OSError, json.JSONDecodeError) as exc:
            out["state"] = "MANIFEST_UNREADABLE"
            out["detail"] = f"{manifest_path} could not be read: {exc}"
            return out
    else:
        # A revision directory with no manifest is one this machine did not
        # stage through `release.py`. Reported rather than assumed: an older
        # staging layout is not a defect, but the recorded hash is genuinely
        # unavailable and the axis must not read that as agreement.
        out["state"] = "PROMOTED"
        out["tree_hash_ok"] = None
        out["detail"] = (f"no {TREE_MANIFEST} in the revision, so the staged tree "
                         "hash cannot be checked against anything")
        return out
    recorded = (out["manifest"] or {}).get("tree_hash")
    actual = release.fingerprint(resolved, ignore=(TREE_MANIFEST,))
    out["tree_hash_ok"] = (recorded == actual) if recorded else None
    out["state"] = "PROMOTED"
    out["detail"] = (
        f"staged tree hash {'matches' if out['tree_hash_ok'] else 'DOES NOT MATCH'} "
        f"the tree on disk" if recorded else
        "the manifest records no tree hash; the staged revision cannot be identified")
    return out


def unit_exec_start(unit_dir: Path, unit_name: str) -> dict:
    """The program a supervisor is configured to start.

    Read from the unit file rather than asked of `systemctl`. Two reasons, and
    the second is the one that matters: a unit file is a fact this process can
    read on any machine, and asking the supervisor would make the answer depend
    on a supervisor being up — so on a node whose systemd session is not running
    this file would report UNKNOWN about a unit it could have simply read.
    """
    out = {"unit": unit_name, "path": None, "state": "ABSENT", "detail": ""}
    path = unit_dir / unit_name
    if not path.is_file():
        out["detail"] = f"no unit at {path}"
        return out
    line = ""
    try:
        for raw in path.read_text(errors="replace").splitlines():
            if raw.strip().startswith("ExecStart="):
                line = raw.strip()[len("ExecStart="):]
                break
    except OSError as exc:
        out["state"] = "UNREADABLE"
        out["detail"] = f"{path} could not be read: {exc}"
        return out
    if not line:
        out["state"] = "NO_EXEC_START"
        out["detail"] = f"{path} declares no ExecStart"
        return out
    # systemd prefixes an argv with `-` (ignore failure), `@` (argv[0]), `+`/`!`
    # (privilege). Any of them still leaves the program as the first token; a
    # quote would make "the first token" a guess, so it is refused instead.
    if '"' in line or "'" in line:
        out["state"] = "UNPARSEABLE"
        out["detail"] = (f"ExecStart is quoted, and splitting it would be a guess: "
                         f"{line!r}")
        return out
    # Checked on the whole value and BEFORE the prefix strip, because `@` is both
    # systemd's argv[0] marker and the delimiter of a placeholder: stripping the
    # prefix first turns `@SERVEROS_RUNTIME@/bin/serverosd` into
    # `SERVEROS_RUNTIME@/bin/serverosd`, which no longer looks like anything and
    # would be reported as a declared path that simply does not exist — the wrong
    # finding, with a plausible detail. `release.py` renders these at install
    # time, and an unrendered one is inert to systemd, so the unit would start a
    # literal path rather than failing.
    if re.search(r"@[A-Z][A-Z0-9_]*@", line):
        out["state"] = "UNRENDERED"
        out["detail"] = (f"ExecStart still carries a placeholder: {line!r} — the "
                         "unit was installed without being rendered")
        return out
    token = line.lstrip("-@+!:").strip().split()[0] if line.strip() else ""
    if not token:
        out["state"] = "UNPARSEABLE"
        out["detail"] = f"ExecStart carries no program: {line!r}"
        return out
    out["path"] = token
    out["state"] = "DECLARED"
    out["detail"] = token
    return out


def live_processes(proc_root: Path) -> list[tuple[int, list[str]]]:
    """Every process this user can see, as (pid, argv).

    `/proc` rather than `ps`, because a command line is a list of arguments and
    this is how the kernel stores one. It has to be a list: every comparison
    below is an exact argument match, and a checker that substring-matched a
    command line would find the shell that typed the search string. The first
    draft of this function did exactly that and reported its own parent as the
    running daemon.
    """
    out: list[tuple[int, list[str]]] = []
    if not proc_root.is_dir():
        return out
    for entry in proc_root.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            raw = (entry / "cmdline").read_bytes()
        except OSError:
            # A process that exited between the listing and the read is not an
            # error; a process owned by somebody else is not ours to read. Both
            # are simply not observations.
            continue
        if not raw:
            continue
        argv = [part.decode(errors="replace") for part in raw.split(b"\0") if part]
        out.append((int(entry.name), argv))
    return sorted(out)


def process_started_at(pid: int, proc_root: Path, boot: float | None) -> float | None:
    """Wall-clock seconds at which `pid` started, or None.

    `starttime` in `/proc/<pid>/stat` is in clock ticks SINCE BOOT, so it is not
    a time until boot time is added — and the two clocks are the same clock, so
    the sum is comparable to a file mtime. `comm` is not always a bare name: it
    is the second field, wrapped in parentheses, and a program whose name
    contains a space or a `)` shifts every later field by one. Splitting on the
    LAST `)` is what makes field 22 field 22 rather than field 20 for those
    programs — the kind of parsing bug that produces a plausible number and no
    error at all.
    """
    if boot is None:
        return None
    try:
        raw = (proc_root / str(pid) / "stat").read_text(errors="replace")
    except OSError:
        return None
    tail = raw.rsplit(")", 1)
    if len(tail) != 2:
        return None
    fields = tail[1].split()
    # After the ')' the fields resume at #3 (state), so #22 is index 19.
    try:
        ticks = int(fields[19])
    except (IndexError, ValueError):
        return None
    try:
        hz = os.sysconf("SC_CLK_TCK")
    except (OSError, ValueError):
        return None
    return boot + ticks / hz


def boot_epoch(proc_root: Path) -> float | None:
    """The kernel's boot time, from `/proc/stat`'s `btime` line."""
    try:
        for line in (proc_root / "stat").read_text(errors="replace").splitlines():
            if line.startswith("btime "):
                return float(line.split()[1])
    except (OSError, ValueError, IndexError):
        return None
    return None


def execution_of(declared: str, composition_root: Path, processes) -> dict:
    """Which live process runs `declared`, and which runs something under this node.

    Two questions, because they are the two ways a running surface can disagree
    with its installation, and the second is invisible if only the first is
    asked: `declared` itself is running (fine), or some OTHER program under this
    node's `revisions/` is running (the promote-without-restart case, where the
    unit and the symlink agree and the process does not).
    """
    out = {"declared": declared, "pids": [], "elsewhere": [], "detail": ""}
    if not declared:
        out["detail"] = "no declared program to look for"
        return out
    revisions = composition_root / "revisions"
    for pid, argv in processes:
        if declared in argv:
            out["pids"].append(pid)
            continue
        for token in argv:
            if not token.startswith("/"):
                continue
            # `revisions in path.parents` rather than counting `.parent`s: the
            # token can be at any depth under the revision, and a fixed number
            # of hops would silently stop matching the moment `bin/` moved.
            if revisions in Path(token).parents:
                out["elsewhere"].append({"pid": pid, "argv0": token})
                break
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
    release = _load_release(args.serveros_repo)
    rows: list[dict] = []

    def row(component, canonical, built, installed, verdict, detail="", note="",
            axis=None, applies=True):
        rows.append({"component": component, "canonical": canonical,
                     "built": built, "installed": installed,
                     "verdict": verdict, "detail": detail, "note": note,
                     "axis": axis, "applies": applies})

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
    targets = [("runtime-assets (built)", args.output),
               ("Claude plugin cache",
                Path(claude.get("installPath", "")) if claude.get("installPath") else None)]
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
        verdict = OK if not drift else SOURCE_RUNTIME_DRIFT
        detail = f"{len(drift)} drifted path(s); e.g. {', '.join(drift[:3])}" if drift else ""
        row(f"{BUNDLE_NAME} @ {label}", "", "", f"sha256={tree_hash(root) or 'NONE'}",
            verdict, detail, note)

    # --- the plugin the Agent actually LOADS ------------------------------
    #
    # The rows above measure the DISTRIBUTION this repository builds and are
    # named `BUNDLE_NAME` because that is the distribution's own name. Which
    # plugin a session on this node loads is a different fact, and it is a
    # per-composition one: a node's default entry is a profile composition.
    #
    # This section exists because the two came apart on 2026-09-23. This node
    # migrated its default entry from `hwz-server-os` to
    # `hwz-server-os-linux-work`, disabling the former; the rows above went on
    # reporting the disabled composition as though it were the node's, and the
    # composition every session now loads had no row at all. The subject of a
    # measurement is part of the measurement.
    #
    # The subject is derived, not listed: `enabledPlugins` says which plugins a
    # session loads, and a plugin is this checker's business exactly when its
    # name is one of the compositions THIS repository declares. Another
    # product's plugin in the same file is skipped by that test rather than by
    # a hardcoded ignore list.
    enabled = read_enabled_plugins(args.claude_settings_json)
    installed = read_claude_plugins(args.claude_plugins_json)
    codex_enabled = read_codex_plugins(args.codex_config)
    compositions = composition_index(build)
    plugin_names = {entry["name"] for entry in compositions.values()}
    by_plugin = {entry["name"]: composition for composition, entry in compositions.items()}

    # Where each runtime put each composition, resolved once and then used by
    # both the rows below and the materialization audit further down. It used to
    # be resolved separately at each site against `BUNDLE_NAME`, which meant the
    # audit paired the NODE revision with CORE's installed plugin — two
    # compositions, compared as though they were one. Nothing said so, because
    # with a single installed plugin every pairing happens to be self-consistent.
    plugin_roots: dict[str, dict] = {}
    for composition, info in compositions.items():
        name = info["name"]
        record = installed.get(plugin_key(name)) or {}
        install_path = record.get("installPath")
        codex_dir = args.codex_cache / codex_marketplace_name(name) / name
        versions = sorted(p for p in codex_dir.iterdir() if p.is_dir()) \
            if codex_dir.is_dir() else []
        plugin_roots[composition] = {
            "claude": Path(install_path) if install_path else None,
            "codex_versions": versions,
            "codex": versions[-1] if versions else None,
        }

    for key in sorted(enabled):
        if key.split("@")[0] not in plugin_names:
            continue
        state = "enabled" if enabled[key] else "disabled"
        entry = installed.get(key)
        label = f"{key} (Claude plugin, {state})"
        if entry is None:
            row(label, "", "", MISSING, MISSING,
                "declared in settings.json and absent from the install record")
            continue
        payload = Path(entry.get("installPath") or "")
        if not payload.is_dir():
            row(label, "", str(entry.get("version") or ""), MISSING, MISSING,
                f"not present: {payload}")
            continue
        installed_version = str(entry.get("version") or "")
        if not pinned or not args.ability_repo.is_dir():
            row(label, "", "", f"version={installed_version}  sha256="
                f"{tree_hash(payload) or 'NONE'}", UNKNOWN,
                "cannot verify: " + recompute_blocker(build, payload))
            continue
        # The composition is read from the INSTALLED payload's own README, so a
        # plugin is recomputed against what it declares rather than against the
        # distribution that happens to sit beside it.
        got = expected_artifact(build, args.ability_repo, pinned, payload,
                                args.marketplace_path, layout_at=args.output)
        if got is None:
            row(label, "", "", f"version={installed_version}  sha256="
                f"{tree_hash(payload) or 'NONE'}", UNKNOWN,
                "cannot verify: " + recompute_blocker(build, payload))
            continue
        expected_plugin, plugin_built = got
        # The node-runtime face of the same payload writes `RELEASE.json`; the
        # plugin face does not. Where a marketplace source is a promoted
        # revision directory — which it is when one payload serves both faces,
        # exactly as both compositions here declare — the manifest arrives with
        # the directory and is not part of the composition's content. The node
        # row below already excludes it for the same reason; this is that
        # decision applied to the same bytes at the other end of the same chain.
        drift = tree_drift(expected_plugin, payload, ignore=(Path(TREE_MANIFEST),))
        row(label, "", plugin_built, f"version={installed_version}  sha256="
            f"{tree_hash(payload) or 'NONE'}",
            OK if not drift else SOURCE_RUNTIME_DRIFT,
            "" if not drift else
            f"canonical source builds {plugin_built}, and the installed plugin "
            f"differs in {len(drift)} path(s); e.g. {', '.join(drift[:3])}")

    # Codex, read the same way and for the same reason. It keeps its switch in
    # config.toml and materializes into <cache>/<marketplace>/<plugin>/<version>/,
    # so the leaf is resolved by LISTING the version directory rather than by
    # asking the config which version is installed — the config does not record
    # one. A second version dir beside the first is named, not silently resolved
    # to the newest: Codex is single-slot, so a generation sitting beside another
    # is a fact about this machine.
    for key in sorted(codex_enabled):
        name, _, marketplace = key.partition("@")
        # The record is keyed by PLUGIN name; `plugin_roots` is keyed by
        # COMPOSITION. Translating rather than conflating, because the two are
        # the same string for exactly one of this product's compositions and a
        # lookup that is right by coincidence is a lookup that stops being right
        # when a profile is added. The marketplace name, by contrast, is built
        # from the plugin name — `codex_marketplace()` in the builder derives it
        # from `bundle['name']` — so that argument stays as it is.
        composition = by_plugin.get(name)
        if composition is None or marketplace != codex_marketplace_name(name):
            continue
        state = "enabled" if codex_enabled[key] else "disabled"
        label = f"{key} (Codex plugin, {state})"
        versions = plugin_roots[composition]["codex_versions"]
        if not versions:
            row(label, "", "", MISSING, MISSING,
                f"not present: {args.codex_cache / marketplace / name}")
            continue
        note = "" if len(versions) == 1 else (
            f"{len(versions)} version dirs present ({', '.join(p.name for p in versions)}); "
            "Codex is single-slot")
        leaf = versions[-1]
        installed_version = leaf.name
        if not pinned or not args.ability_repo.is_dir():
            row(label, "", "", f"version={installed_version}  sha256="
                f"{tree_hash(leaf) or 'NONE'}", UNKNOWN,
                "cannot verify: " + recompute_blocker(build, leaf), note=note)
            continue
        got = expected_artifact(build, args.ability_repo, pinned, leaf,
                                args.marketplace_path, layout_at=args.output)
        if got is None:
            row(label, "", "", f"version={installed_version}  sha256="
                f"{tree_hash(leaf) or 'NONE'}", UNKNOWN,
                "cannot verify: " + recompute_blocker(build, leaf), note=note)
            continue
        expected_codex, codex_built = got
        drift = tree_drift(expected_codex, leaf, ignore=(Path(TREE_MANIFEST),))
        row(label, "", codex_built, f"version={installed_version}  sha256="
            f"{tree_hash(leaf) or 'NONE'}",
            OK if not drift else SOURCE_RUNTIME_DRIFT,
            ("" if not drift else
             f"canonical source builds {codex_built}, and the installed plugin "
             f"differs in {len(drift)} path(s); e.g. {', '.join(drift[:3])}"),
            note=note)

    # --- 8. the node: revision, unit, process ----------------------------
    #
    # Everything above measures the DISTRIBUTION — what a release contains and
    # where a marketplace put it. The node runs a different composition from a
    # different path under a different supervisor, and until this section existed
    # none of it was measured. `args.runtime_root / args.composition` is where
    # that node lives; `args.output` above is where the plugin payload lives.
    node = node_revision(args.runtime_root, args.composition, release)
    unit = unit_exec_start(args.unit_dir, NODE_UNIT)
    processes = live_processes(args.proc_root)
    rev_dir = Path(node["revision_dir"]) if node["revision_dir"] else None
    unit_program = unit["path"]
    exec_state = execution_of(unit_program or "", args.runtime_root / args.composition,
                              processes)

    # `argv` alone cannot say which revision a process is running when the path
    # it was started from goes through `current`: the string is identical before
    # and after a promote, because a promote moves the symlink and the process
    # keeps the argv it was born with. The one fact that does distinguish them is
    # time — `release.py promote` replaces the symlink, and a process that
    # started BEFORE that replacement is executing what `current` used to name.
    #
    # Both numbers come from the same clock, so comparing them is sound rather
    # than merely suggestive: `starttime` is ticks since boot and `btime` is
    # boot, so their sum is wall time, and a file mtime is wall time.
    repointed: list[dict] = []
    started: dict[int, float | None] = {}
    current_link = args.runtime_root / args.composition / "current"
    link_mtime: float | None = None
    if current_link.is_symlink():
        try:
            link_mtime = os.lstat(current_link).st_mtime
        except OSError:
            link_mtime = None
    if link_mtime is not None:
        boot = boot_epoch(args.proc_root)
        for pid in exec_state["pids"]:
            when = process_started_at(pid, args.proc_root, boot)
            started[pid] = when
            # One second of slack: the promote that repoints the link and a
            # restart that follows it are allowed to be the same second. A
            # genuine "promoted and never restarted" is minutes or days apart.
            if when is not None and when < link_mtime - 1.0:
                repointed.append({"pid": pid, "started_at": when,
                                  "current_repointed_at": link_mtime})

    # The supervisor's declaration against the installation it is supposed to be
    # pointing at. A unit naming a path outside this composition's `current` is
    # not a running problem yet — it is a unit somebody promoted past and did not
    # re-render, and the repair happens before the service is touched.
    #
    # The comparison is by REAL PATH, not by string: the unit names
    # `.../current/bin/serverosd` and the revision lives at
    # `.../revisions/<label>/bin/serverosd`, so comparing the text would report
    # every correctly rendered unit as stale. The string form is what makes the
    # `/proc` match exact and the real path is what makes this comparison right;
    # using either one for the other's question gets it wrong in both directions.
    def _same_program(value: str, path: Path | None) -> bool:
        if not value or path is None:
            return False
        return os.path.realpath(value) == os.path.realpath(path)

    if unit["state"] != "DECLARED":
        unit_verdict, unit_detail = UNKNOWN, unit["detail"]
    elif node["state"] != "PROMOTED":
        unit_verdict, unit_detail = UNIT_STALE, (
            f"the unit starts {unit_program}, but this node has no promoted revision "
            f"to compare it against ({node['state']}: {node['detail']})")
    elif not _same_program(unit_program or "", rev_dir / "bin/serverosd" if rev_dir else None):
        unit_verdict, unit_detail = UNIT_STALE, (
            f"the unit starts {unit_program}, which is not this node's current "
            f"revision ({node['current']}) — the unit was rendered for a revision "
            "`current` no longer points at")
    elif repointed:
        entry = repointed[0]
        unit_verdict, unit_detail = BUILT_RUNTIME_DRIFT, (
            f"pid {entry['pid']} started {link_mtime - entry['started_at']:.0f}s "
            f"before `current` was last repointed, and it is still running — it is "
            "executing the revision that `current` used to name. `release.py "
            "promote` reloads units but does NOT restart, so this is what a "
            "promote looks like until the node is restarted")
    elif exec_state["pids"]:
        unit_verdict, unit_detail = OK, f"pid {', '.join(map(str, exec_state['pids']))}"
    elif exec_state["elsewhere"]:
        where = exec_state["elsewhere"][0]
        unit_verdict, unit_detail = BUILT_RUNTIME_DRIFT, (
            f"nothing runs {unit_program}; pid {where['pid']} is running "
            f"{where['argv0']} — a revision this node has already moved past")
    else:
        unit_verdict, unit_detail = NOT_RUNNING, (
            f"{unit_program} is declared and is not executing; no process under this "
            "node's revisions/ is running either")
    row(f"{NODE_UNIT} (running unit)", args.composition,
        f"pid={exec_state['pids'][0]}" if exec_state["pids"] else "",
        unit_program or UNKNOWN, unit_verdict,
        # The pid is the answer when the row passes, and `render` prints every
        # detail as a problem marker. Saying "pid 1653" in the shape of a
        # finding teaches a reader to skip that marker, which is the one thing
        # it must never learn to do.
        "" if unit_verdict == OK else unit_detail,
        # A machine with no unit file is not a machine whose unit is broken: the
        # question "is the declared program running" has no declaration to be
        # asked of, and answering NOT_RUNNING would report an absent supervisor
        # as a stopped node. Keyed on the FILE, never on a check having failed,
        # and named in `release.not_applicable` like the node row below.
        applies=unit["state"] != "ABSENT")

    # The revision's bytes against what canonical source builds from the pin the
    # artifact itself declares. This is the same question the payload rows ask of
    # the plugin, asked of the thing that is actually executing.
    node_verdict, node_detail, node_built = UNKNOWN, node["detail"], ""
    node_expected = None
    if node["state"] != "PROMOTED" or rev_dir is None:
        node_verdict = MISSING if node["state"] == "ABSENT" else UNKNOWN
    elif node["tree_hash_ok"] is False:
        # The recorded hash and the directory disagree, so the revision is not
        # the one that was staged. Rebuilding from source would answer a
        # different question, so this is decided here and nothing is recomputed.
        node_verdict = SOURCE_RUNTIME_DRIFT
        node_detail = ("the staged revision's tree hash does not match its own "
                       "directory — these bytes are not the ones that were promoted")
    else:
        pin = declared_pin(rev_dir)
        if not pin or not args.ability_repo.is_dir():
            node_detail = recompute_blocker(build, rev_dir)
        else:
            # `layout_at=args.output`: the node revision is not a plugin and has
            # no marketplace position, so it borrows the one the plugin rows are
            # already required to have rather than being asked for its own.
            got = expected_artifact(build, args.ability_repo, pin, rev_dir,
                                    args.marketplace_path, layout_at=args.output)
            if got is None:
                node_detail = recompute_blocker(build, rev_dir)
            else:
                node_expected, node_built = got
                drift = tree_drift(node_expected, rev_dir, ignore=(Path(TREE_MANIFEST),))
                node_verdict = OK if not drift else SOURCE_RUNTIME_DRIFT
                node_detail = ("" if not drift else
                               f"canonical source builds {node_built}, and the running "
                               f"revision differs in {len(drift)} path(s); "
                               f"e.g. {', '.join(drift[:3])}")
    node_label = (node["manifest"] or {}).get("revision") or node["current"] or ""
    row(f"{args.composition} (node revision)", node_label, node_built,
        f"{node['revision_dir'] or node['root']}"
        + (f"  sha256={node['manifest']['tree_hash'][:12]}" if node["manifest"] else ""),
        node_verdict, node_detail,
        note=node["detail"] if node_verdict == OK else "",
        # A machine with no node runtime for this composition has no running
        # surface to converge, and reporting that as a failure would make this
        # file unusable on every machine that is not this node. The exclusion is
        # keyed on the DISTRIBUTION ROOT being absent — a fact about what is
        # installed — and never on a check having failed. It is reported in
        # `release.not_applicable` so it cannot be silent.
        applies=node["state"] != "ABSENT")

    # The chain verdict answers only "do the bytes agree at every hop". The pin
    # row is excluded because reachability is a different question, answered on
    # its own axis below; letting it leak into the chain verdict would make a
    # perfectly consistent chain read as UNKNOWN for a reason that has nothing
    # to do with consistency.
    chain_verdicts = {r["verdict"] for r in rows
                      if r["axis"] != AXIS_PROVENANCE and r.get("applies", True)}
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
    # Two compositions, two member sets, one question. Auditing only the plugin
    # payload would leave the members the NODE actually ships unchecked, and
    # those are the ones a runtime skill root could duplicate. Both are audited
    # and merged, with the per-composition breakdown kept so a duplicate can be
    # attributed rather than merely counted.
    audit_targets = [(args.output, "plugin payload"),
                     (rev_dir, "node revision")] if rev_dir is not None else \
                    [(args.output, "plugin payload")]
    materialization = {
        "verdict": PASS, "members": [], "duplicates": [], "collisions": [],
        "notes": [], "detail": "", "compositions": {},
    }
    for artifact, label in audit_targets:
        if artifact is None:
            continue
        # The installed roots for the composition THIS artifact declares, not
        # for whatever distribution happens to be named by a constant. The two
        # audit targets are two different compositions, so one pair of roots
        # cannot serve both — and pairing the node revision with core's plugin
        # made the audit ask whether core's members reach the node, which is a
        # question about the wrong artifact that answers itself when the
        # compositions overlap.
        roots = plugin_roots.get(declared_composition(artifact) or "", {})
        found = materialization_audit(artifact, [
            ("Claude", roots.get("claude"), claude_skills),
            ("Codex", roots.get("codex"), codex_skills),
        ])
        materialization["compositions"][label] = {
            "artifact": str(artifact),
            "verdict": found["verdict"],
            "members": found["members"],
            "detail": found["detail"],
        }
        materialization["members"] += found["members"]
        materialization["duplicates"] += found["duplicates"]
        materialization["collisions"] += found["collisions"]
        materialization["notes"] += found["notes"]
        if found["verdict"] != PASS:
            materialization["verdict"] = found["verdict"]
    materialization["members"] = sorted(set(materialization["members"]))
    if materialization["duplicates"]:
        materialization["detail"] = (
            f"{len(materialization['duplicates'])} member(s) materialized twice: "
            + ", ".join(f"{d['member']}@{d['runtime']}"
                        for d in materialization["duplicates"][:4]))
    else:
        materialization["detail"] = (
            f"{len(materialization['members'])} member(s) across "
            f"{len(materialization['compositions'])} composition(s), each reaching "
            "every installed runtime once")

    not_applicable = [r["component"] for r in rows if not r.get("applies", True)]
    axes = {
        AXIS_CHAIN: {
            "verdict": PASS if overall == OK else FAIL,
            "detail": ("source, built, installed and running agree at every hop"
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

    return {"schema": "serveros/runtime-truth.v3", "verdict": overall, "rows": rows,
            "release": {
                "verdict": RELEASE_COMPLETE if not blocking else RELEASE_INCOMPLETE,
                "axes": axes, "blocking": blocking,
                "not_applicable": not_applicable,
                "materialization": materialization,
                # The node's own surface, kept as its own block because a reader
                # asking "is the daemon running the code we wrote" should not
                # have to find the answer by pattern-matching row labels. Every
                # field is what was measured, including the ones that say the
                # measurement could not be made.
                "node": {
                    "composition": args.composition,
                    "state": node["state"],
                    "current": node["current"],
                    "previous": node["previous"],
                    "revision_dir": node["revision_dir"],
                    "revision_label": node_label,
                    "tree_hash_recorded": (node["manifest"] or {}).get("tree_hash"),
                    "tree_hash_ok": node["tree_hash_ok"],
                    "unit": {"name": NODE_UNIT, "state": unit["state"],
                             "exec_start": unit_program, "detail": unit["detail"]},
                    "processes": {"running": exec_state["pids"],
                                  "running_a_superseded_revision":
                                      exec_state["elsewhere"],
                                  "started_before_current_was_repointed":
                                      repointed,
                                  "visible": len(processes)},
                    "detail": node["detail"],
                },
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
    node = release.get("node")
    if node:
        running = ", ".join(map(str, node["processes"]["running"])) or "none"
        print(f"  {'NODE':<22} {node['state']:<5} {node['composition']}@"
              f"{node['current'] or '(none)'}  unit={node['unit']['state']}  "
              f"running pid={running}")
        for entry in node["processes"]["running_a_superseded_revision"]:
            print(f"  -- running a superseded revision: pid {entry['pid']} "
                  f"{entry['argv0']}")
        for entry in node["processes"]["started_before_current_was_repointed"]:
            print(f"  -- pid {entry['pid']} started {entry['current_repointed_at'] - entry['started_at']:.0f}s "
                  "before `current` was last repointed; restart to converge")
    for component in release.get("not_applicable", []):
        # Named rather than silent. A row excluded from the chain verdict is a
        # hole in the gate, and a hole nobody can see is how a gate stops being
        # one — this line is the difference between an exclusion and an absence.
        print(f"  -- not applicable on this machine: {component}")
    print()
    print(f"RELEASE: {release['verdict']}"
          + (f"  (blocking: {', '.join(release['blocking'])})" if release["blocking"] else ""))


# --------------------------------------------------------------------------
# where this inspector looks when nobody says
# --------------------------------------------------------------------------
#
# There is no built-in path, and the absence is the point. Until §Q6 seven of
# the defaults below were `/opt/applications/data/home-hwz/...` — one machine's
# layout compiled into a program whose whole job is to report what is true of
# the machine it is running on. On a host with that path absent it did not fail
# loudly; it produced a report about a directory nobody had asked about, which
# reads exactly like a report about this one.
#
# This is the same resolver `architecture_check.py` carries, written out a
# second time rather than imported. The two are invoked as subprocesses from
# different roots — one ships inside the Skill payload, this one inside the
# repo's `scripts/` — so a shared import would be a dependency between two
# things the release is free to move apart. What they share is the AUTHORITY,
# not the code: flags, then environment, then this host's own profile, then a
# refusal. `architecture_check.py` additionally forwards the two roots it was
# given explicitly, so a check run against a particular checkout reads that
# checkout rather than the profile's.

HOST_PROFILE_ENV = "SERVEROS_HOST_PROFILE"
DEFAULT_HOST_PROFILE = Path("~/.config/serveros/host-profile.yaml")

#: argument -> (environment variable, profile root keys, path under that root).
#:
#: The suffixes are product layout — the bundle name, the control-plane venv's
#: own path — and are the same on every node that runs this distribution. The
#: part that differs per machine is the three roots, and those come from the
#: profile.
ROOT_SOURCES = {
    "serveros_repo": ("SERVEROS_REPO", ("workspace_src",), ("serveros",)),
    "ability_repo": ("SERVEROS_ABILITY_REPO", ("workspace_src",), ("ability-management",)),
    "engine_repo": ("SERVEROS_ENGINE_REPO", ("workspace_src",), ("work-engine",)),
    "output": ("SERVEROS_OUTPUT", ("runtime_assets_root",), (BUNDLE_NAME, "current")),
    "marketplace_path": ("SERVEROS_MARKETPLACE", ("runtime_assets_root",),
                         (BUNDLE_NAME, ".agents", "plugins", "marketplace.json")),
    "runtime_root": ("SERVEROS_RUNTIME_ROOT", ("runtime_assets_root",), (BUNDLE_NAME,)),
    "engine_installed": ("SERVEROS_ENGINE_INSTALLED", ("envs_root",),
                         ("python", "serveros-control-plane", "lib", "python3.12",
                          "site-packages", "workengine")),
}


def host_profile_roots() -> dict:
    """This node's declared roots, or {} — never a fabricated path."""
    raw = os.environ.get(HOST_PROFILE_ENV)
    path = Path(raw).expanduser() if raw else DEFAULT_HOST_PROFILE.expanduser()
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    if not isinstance(document, dict):
        return {}
    node = document.get("node")
    roots = node.get("roots") if isinstance(node, dict) else None
    return roots if isinstance(roots, dict) else {}


def default_root(argument: str):
    """The environment's answer, else this host's, else None. Never a guess.

    The two sources are not interchangeable, and the difference is the suffix.
    An environment variable names the thing itself: `SERVEROS_REPO` and
    `SERVEROS_ABILITY_REPO` are declared in `ops/workspace/environment.yaml` as
    the checkouts, and `build.py`, `taxonomy.py` and `runtime/cli.py` all read
    them that way. The profile names the ROOT a thing hangs under — `workspace_src`
    is `<WORKSPACE_ROOT>/src` — so the product layout is appended to it.

    Appending on both branches resolved `…/src/ability-management` to
    `…/src/ability-management/ability-management`, and the consumer reported
    `not a git checkout`: a wrong path wearing a broken repository's message.
    This is the second copy of `architecture_check.py`'s resolver; see
    `test_the_two_copies_of_the_root_resolver_agree`, which is what keeps the
    two from parting again.
    """
    env_name, profile_keys, suffix = ROOT_SOURCES[argument]
    raw = os.environ.get(env_name)
    if raw:
        return Path(raw)
    roots = host_profile_roots()
    base = next((Path(roots[key]) for key in profile_keys
                 if isinstance(roots.get(key), str) and roots[key]), None)
    if base is None:
        return None
    return base.joinpath(*suffix) if suffix else base


def main() -> int:
    home = Path(os.path.expanduser("~"))
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--serveros-repo", type=Path,
                    default=default_root("serveros_repo"),
                    help="canonical bundle-source checkout whose build.py defines "
                         "what the artifact should contain")
    ap.add_argument("--ability-repo", type=Path,
                    default=default_root("ability_repo"))
    ap.add_argument("--ability-rev", default="",
                    help="build pin; default = the revision Claude's install record carries")
    ap.add_argument("--output", type=Path,
                    default=default_root("output"))
    ap.add_argument("--marketplace-path", type=Path,
                    default=default_root("marketplace_path"))
    ap.add_argument("--engine-repo", type=Path,
                    default=default_root("engine_repo"))
    ap.add_argument("--engine-installed", type=Path,
                    default=default_root("engine_installed"))
    ap.add_argument("--claude-plugins-json", type=Path,
                    default=Path(os.environ.get("SERVEROS_CLAUDE_PLUGINS_JSON")
                                 or home / ".claude/plugins/installed_plugins.json"))
    ap.add_argument("--codex-config", type=Path,
                    default=Path(os.environ.get("SERVEROS_CODEX_CONFIG") or home / ".codex/config.toml"))
    ap.add_argument("--claude-settings-json", type=Path,
                    default=Path(os.environ.get("SERVEROS_CLAUDE_SETTINGS_JSON")
                                 or home / ".claude/settings.json"),
                    help="which plugins a session actually loads; the install "
                         "record has no enabled field, so it cannot answer this")
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
    # --- the node, which is not the distribution -------------------------
    # `--output` above is where the PLUGIN payload is built and installed from;
    # this is where the NODE runs from. They are different paths, different
    # compositions and different names, and the file conflated them for as long
    # as it existed because only one of the two was ever on the machine when it
    # was written.
    ap.add_argument("--runtime-root", type=Path,
                    default=default_root("runtime_root"),
                    help="distribution root holding one directory per composition, "
                         "each with `current` -> `revisions/<label>`")
    ap.add_argument("--composition", default=os.environ.get("SERVEROS_COMPOSITION")
                    or NODE_COMPOSITION,
                    help="the composition the node RUNS from; the plugin "
                         "composition is read from the artifact's own README")
    ap.add_argument("--unit-dir", type=Path,
                    default=Path(os.environ.get("SERVEROS_UNIT_DIR")
                                 or home / ".config/systemd/user"),
                    help="where the supervisor's unit files live; read as data, so "
                         "this works on a machine whose systemd session is not up")
    ap.add_argument("--proc-root", type=Path,
                    default=Path(os.environ.get("SERVEROS_PROC_ROOT") or "/proc"),
                    help="where to look for live processes; a parameter so the "
                         "fixture can supply a synthetic one rather than describe "
                         "whatever happens to be running when the suite runs")
    args = ap.parse_args()

    # Refused, not guessed — and refused before `collect()`, so an unanswerable
    # root can never be reported as a RELEASE_* verdict about a directory that
    # does not exist. Exit 2, because 0 here means RELEASE_COMPLETE and nothing
    # else may borrow it.
    unset = [name for name in ROOT_SOURCES if getattr(args, name) is None]
    if unset:
        env_names = ", ".join(ROOT_SOURCES[name][0] for name in unset)
        flags = ", ".join("--" + name.replace("_", "-") for name in unset)
        sys.stderr.write(
            "runtime_truth: no root for " + flags + ".\n"
            "  This inspector carries no built-in path, because a path that is "
            "right on one machine is a wrong answer on the next one.\n"
            "  Pass the flag, set " + env_names + ", or point "
            + HOST_PROFILE_ENV + " at a host profile whose `node.roots` "
            "declares them.\n")
        return 2

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
