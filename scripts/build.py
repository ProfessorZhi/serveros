#!/usr/bin/env python3
"""Deterministically build a hwz-server-os runtime artifact from a composition.

`plugin/bundle.yaml` is the `serveros-core` composition; `plugin/profiles/`
holds the wider ones, each `extends:`ing core. A profile is a composition, not a
fork: it adds members and carries no payload of its own. This script
materializes one composition into a self-contained runtime artifact:

    composition declaration             (this repository, pinned here)
  + ability-management reusable Skills  (read at an explicit pinned revision)
  + ability-management on-demand names  (declared, validated, NOT bundled)
  + serveros Core modules               (this repository)
                  |
                  v
    runtime artifact  ->  runtime-assets/<composition>  ->  Claude / Codex

Two properties matter and are enforced below:

1. Determinism. Identical inputs must produce byte-identical output, so the
   artifact can be verified rather than assumed. Byte-code caches are excluded
   because they embed absolute source paths and interpreter version headers.

2. Pinned source. Reusable Skills are read from an explicit ability-management
   revision via `git`, never from a working tree. A stale or dirty checkout on
   this machine therefore cannot silently downgrade the distribution, and no
   generated artifact ever references this machine's build paths.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

try:
    import yaml
except ImportError:  # pragma: no cover - environment guarantee
    sys.stderr.write("ERROR: PyYAML is required to read plugin/bundle.yaml\n")
    raise SystemExit(2)

EXCLUDED_DIRS = {"__pycache__", ".git"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}

# git's two executable tree modes. `100755` is an ordinary executable file;
# `100775` is a setgid one, which git can hold and which is executable too.
EXECUTABLE_GIT_MODES = {"100755", "100775"}


def is_executable(path: Path) -> bool:
    """Whether a source file is a program.

    The executable bit is part of what a file IS, not decoration, so the
    artifact reproduces it from the source instead of inferring it from a
    suffix. Inferring is what broke: the writer restored a mode only for `.sh`
    and `.py`, and `bin/serverosd` has no suffix, so it landed 0644 and systemd
    answered `status=203/EXEC`. Both entry points in the composition are
    extensionless, so the rule was wrong for exactly the files whose mode
    matters most.
    """
    return bool(path.stat().st_mode & 0o111)


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _git(repo: Path, *args: str) -> bytes:
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if proc.returncode != 0:
        raise ValueError(
            f"git {' '.join(args)} failed in {repo}: {proc.stderr.decode(errors='replace').strip()}"
        )
    return proc.stdout


# ServerOS Core modules that ship as plain code next to the skills. Each is
# declared in bundle.yaml under its own key; this tuple only fixes the order and
# the set of module names the builder is willing to look for.
#
# `mcp` is here for the same reason as the other three: a delegating CLI must be
# able to load it from the installed bundle instead of from a source checkout
# that moves. It differs in one respect — it is not a decision kernel but an
# entry point, so it additionally gets declared to the runtime as a server.
#
# `runtime` and `bin` are the other half of that same argument, and their
# absence was the defect. `mcp` ships an adapter that calls `serverosd`; until
# these two joined it, the artifact contained the caller and not the callee, so
# a node installed from this distribution had an MCP surface that could only
# ever answer `daemon not running`, and an operator who wanted a working node
# had to point a unit at a source checkout. `bin` is here for the same reason as
# `runtime` rather than for symmetry: the entry points resolve the runtime
# relative to themselves, so the pair is one unit and shipping either alone
# produces a shim that cannot import what it names.
#
# `fleet` is the same defect a second time, one layer up. `mcp/agent_mcp.py`
# loads `fleet/agent_control_router.py` at module scope and the router loads
# `fleet/transport.py` in turn, so the Agent MCP surface cannot start without
# the directory. It went unnoticed because every test and every manual run
# happened from a source checkout, where `fleet/` is simply present: the
# composition shipped an entry point whose first act is to import a directory
# the composition did not ship, and no test asked the artifact whether it could
# start its own declared surfaces. It is named in CORE_MODULES rather than
# only in the bundle so that `test_every_declared_core_module_is_present`
# covers it and a future drop is a failure rather than a discovery.
#
# `intake` is the third instance, and the sentence above is exactly what did
# not happen. `runtime/cli.py` loads `intake/global_board.py` at call time for
# `serverosctl requirement board`; the directory was named in neither this
# tuple nor `plugin/bundle.yaml`, so it shipped in no artifact and the verb
# raised FileNotFoundError on every installed node while working perfectly from
# a source checkout. It was found by installing the distribution and running
# the verb — not by a test, because `test_every_declared_core_module_is_present`
# iterates THIS tuple, and a module that was never added to it is not merely
# unchecked, it is invisible to the check.
#
# So the remedy is not a fourth name here. The requirement is now derived from
# the production loader closure — see `tests/loader_graph.py`'s
# `production_closure()` and `test_build.py`'s
# `test_the_artifact_answers_the_production_loader_closure`, which asserts the
# artifact satisfies it and carries a positive control proving the gate can go
# red. This tuple remains the builder's IMPLEMENTATION input: it is how the
# composition is assembled, and it is no longer the statement of what is
# required.
CORE_MODULES = ("context", "space", "relations", "mcp", "runtime", "bin",
                "abilities", "memory", "fleet", "intake", "documents")


def resolve_ability_rev(repo: Path, rev: str) -> str:
    """Resolve a revision to a full commit SHA, rejecting unknown or ambiguous input."""
    out = _git(repo, "rev-parse", "--verify", f"{rev}^{{commit}}").decode().strip()
    if not out:
        raise ValueError(f"cannot resolve ability revision: {rev}")
    return out


COMPOSITION_SCHEMA = "serveros/bundle.v1"

# The build's statement about the artifact it produced, written beside the
# payload. `release.py` reads it to choose a verifier, so the two files name the
# same key: a manifest written under one name and looked for under another is a
# release path that refuses every artifact for a reason nobody can see.
ARTIFACT_MANIFEST_NAME = "ARTIFACT.json"

# What the build is allowed to say about an ability the composition recommends
# but does not ship. There is deliberately no "unknown": the point of naming a
# status is that someone checked.
RECOMMENDED_STATUS = ("available", "provisional", "missing")


def _inside(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    if resolved != root and root not in resolved.parents:
        raise ValueError(f"composition escapes the repository root: {path}")
    return resolved


def _merge_recommended(parent: list, child: list, *, child_name: str) -> list[dict]:
    ordered: dict[str, dict] = {}
    for entry in list(parent) + list(child):
        name = (entry or {}).get("name")
        if not name:
            raise ValueError(f"{child_name}: a recommended entry must declare a `name`")
        ordered[name] = dict(entry)
    return list(ordered.values())


def _merge_composition(parent: dict, child: dict, *, child_name: str) -> dict:
    """A profile layered on the composition it extends."""
    merged = dict(parent)
    merged.update({k: v for k, v in child.items() if k not in ("skills", "recommended")})

    # `cachebuster` is the cache identity of a generated payload, and a profile's
    # payload is not its parent's. Inheriting it would let two different
    # payloads claim one version string, which is the one thing an installed
    # cache cannot recover from.
    if not child.get("cachebuster"):
        raise ValueError(f"{child_name}: a profile must declare its own `cachebuster`")

    skills: list[dict] = [dict(entry) for entry in parent.get("skills") or []]
    index = {entry["name"]: entry for entry in skills}
    for entry in child.get("skills") or []:
        name, source = entry.get("name"), entry.get("source")
        if not name:
            raise ValueError(f"{child_name}: a skill entry must declare a `name`")
        if name in index:
            if index[name]["source"] != source:
                raise ValueError(
                    f"{child_name}: {name} is `{index[name]['source']}` in the composition it "
                    f"extends and `{source}` here; one ability cannot have two sources")
            # Re-stating an inherited member is not an error: a profile that
            # enumerates its full set reads better than one that only lists the
            # delta, and the two must not be able to disagree.
            continue
        index[name] = dict(entry)
        skills.append(dict(entry))
    merged["skills"] = skills
    merged["recommended"] = _merge_recommended(
        parent.get("recommended") or [], child.get("recommended") or [], child_name=child_name)
    return merged


def load_composition(path: Path) -> dict:
    """Read a composition, resolving `extends` into one effective bundle.

    A profile is a composition that ADDS to another one. That it is not a fork
    is mechanical rather than editorial: a profile file carries no skill payload
    and no copy of its parent's declarations, so there is only one copy of every
    shared fact and the two cannot drift apart.
    """
    root = repo_root()
    stack: list[Path] = []

    def walk(current: Path, *, via_extends: bool) -> dict:
        # The root constraint applies to files reached THROUGH an `extends`
        # edge, not to the path the caller passed. `--bundle` has always been
        # the caller's decision about which file to read — tests write temp
        # compositions outside the repository on purpose. What must not escape
        # is a composition file naming somewhere else to read from, because
        # that is the file deciding, and the repository is its only legitimate
        # neighbourhood.
        resolved = _inside(current, root) if via_extends else current.resolve()
        if resolved in stack:
            chain = " -> ".join(p.name for p in stack + [resolved])
            raise ValueError(f"composition extends itself: {chain}")
        stack.append(resolved)
        try:
            raw = yaml.safe_load(resolved.read_text()) or {}
            if not isinstance(raw, dict):
                raise ValueError(f"{resolved.name}: a composition must be a mapping")
            if raw.get("schema") != COMPOSITION_SCHEMA:
                raise ValueError(
                    f"unsupported bundle schema: {raw.get('schema')!r} in {resolved.name}")
            parent_ref = raw.pop("extends", None)
            if parent_ref is None:
                if not raw.get("composition"):
                    raise ValueError(f"{resolved.name}: a composition must declare `composition`")
                return raw
            parent = walk(resolved.parent / str(parent_ref), via_extends=True)
            return _merge_composition(parent, raw, child_name=resolved.name)
        finally:
            stack.pop()

    return walk(path, via_extends=False)


def _has_ability_payload(repo: Path, rev: str, name: str) -> bool:
    listing = _git(repo, "ls-tree", "-r", "--name-only", rev, "--", f"skills/{name}/payload")
    return bool(listing.strip())


def validate_recommended(bundle: dict, local_root: Path, ability_repo: Path,
                         ability_rev: str) -> None:
    """Check that a recommendation matches the world it describes.

    A `recommended` entry is a claim about an ability's existence, and the claim
    is checkable, so it is checked in both directions. An entry marked
    `available` that resolves to nothing is a promise the node cannot keep; an
    entry marked `missing` that resolves is a stale note that outlives the gap
    it records. Neither is visible unless the build looks.
    """
    bundled = {entry["name"] for entry in bundle.get("skills") or []}
    for entry in bundle.get("recommended") or []:
        name = entry["name"]
        status = entry.get("status")
        if status not in RECOMMENDED_STATUS:
            raise ValueError(
                f"recommended {name}: status must be one of {list(RECOMMENDED_STATUS)}, "
                f"got {status!r}")
        if name in bundled:
            raise ValueError(
                f"recommended {name} is also composed into `skills`; an ability that ships "
                "cannot be recommended on demand — the declaration would say the round trip "
                "is optional while the payload says it is not")
        resolves = (local_root / name).is_dir() or _has_ability_payload(
            ability_repo, ability_rev, name)
        if status == "missing" and resolves:
            raise ValueError(
                f"recommended {name} is marked `missing` but resolves at {ability_rev[:12]}")
        if status != "missing" and not resolves:
            raise ValueError(
                f"recommended {name} is marked `{status}` but does not resolve at "
                f"{ability_rev[:12]}")


# Where the published pack declarations live inside ability-management. Named
# here rather than defaulted at each call site: `publishing.location` in
# `abilities/pack-contract.yaml` says "beside the manifest it names members
# from", and a reader that guessed a different filename would be reading a
# different contract from the one that was declared.
PACK_DECLARATIONS_FILE = "ability-packs.yaml"


def catalogued_abilities(ability_repo: Path, ability_rev: str) -> list[str]:
    """Every ability name in the manifest AT THE PINNED REVISION.

    Read from the git object, not from the checkout — the same discipline
    `read_manifest()` uses, and for the same reason: a dirty ability-management
    checkout must not be able to answer a question about what the catalogue
    contains. The names come out of `abilities[]`, which is the list the
    closure rule means by "the manifest". `authors[]` is not abilities, and a
    projection that read it would answer a different question.
    """
    raw = _git(ability_repo, "show", f"{ability_rev}:abilities-manifest.yaml")
    document = yaml.safe_load(raw.decode())
    if not isinstance(document, dict):
        raise ValueError(f"abilities-manifest.yaml at {ability_rev[:12]} is not a mapping")
    names = []
    for entry in document.get("abilities") or []:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(str(entry["name"]))
    if not names:
        # An empty catalogue makes every pack fail rather than pass, which is
        # the correct direction — but it is much more likely to mean the
        # manifest moved than that every ability was deleted, and the two are
        # worth telling apart before someone reads the wrong one.
        raise ValueError(
            f"abilities-manifest.yaml at {ability_rev[:12]} declares no abilities. "
            "A closure check against an empty catalogue must not be reported as "
            "a set of pack failures")
    return names


def published_packs(ability_repo: Path, ability_rev: str) -> list[dict]:
    """The pack declarations published in ability-management, at that revision.

    The declaration half of `serveros/ability-pack.v1`. `abilities/pack-contract.yaml`
    holds the schema half — the record shape, the member roles, the pin rule,
    what refuses — and its `publishing.location` has said since it was written
    that the packs themselves belong in ability-management, "beside the manifest
    it names members from". That sentence was carried as
    `location_status: DECLARED_NOT_IMPLEMENTED` for as long as it was untrue.

    Read from the git object and not from the checkout, exactly as
    `catalogued_abilities()` reads the manifest and for exactly the same
    reason: a dirty working copy must not be able to answer a question about
    what has been published. The two readers are deliberately symmetric — one
    reads the catalogue, one reads the packs that name members of it, and they
    must agree about which revision they are talking about or the closure
    answer is about two different worlds.
    """
    raw = _git(ability_repo, "show", f"{ability_rev}:{PACK_DECLARATIONS_FILE}")
    document = yaml.safe_load(raw.decode())
    if not isinstance(document, dict):
        raise ValueError(
            f"{PACK_DECLARATIONS_FILE} at {ability_rev[:12]} is not a mapping")
    packs = document.get("packs")
    if not isinstance(packs, list) or not packs:
        # Refusing rather than returning an empty list. An empty declaration
        # set would make every pack vanish and the build would report success
        # over a product with no packs at all — which is the failure mode this
        # whole reader exists to make impossible, arriving through the one door
        # left open: a file that parses and says nothing.
        raise ValueError(
            f"{PACK_DECLARATIONS_FILE} at {ability_rev[:12]} declares no packs. "
            "A build against an empty pack catalogue must not be reported as a "
            "product with nothing to check")
    return packs


def _packs_module():
    """`abilities/packs.py`, loaded by path.

    By path and not by import, because `abilities/` is not a package and this
    directory is not on `sys.path` — the same reason `runtime/cli.py` loads its
    modules this way. An import that works from one entry point and not another
    is how a check gets skipped without anybody noticing.
    """
    import importlib.util

    name = "serveros_abilities_packs"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, repo_root() / "abilities" / "packs.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _secrets_module():
    """`scripts/scan_secrets.py`, loaded by path, for the reason `_packs_module`
    states: this directory is not a package and is not on `sys.path`."""
    import importlib.util

    name = "serveros_scan_secrets"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, Path(__file__).resolve().parent / "scan_secrets.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def scan_composed(files, manifests, readme) -> list[dict]:
    """Credential findings in the bytes this build is about to write.

    §28 asks for the scan as a GATE, and until this existed the scanner was a
    tool with a docstring that said "so a release can fail instead of shipping
    one" while nothing in `build.py` or `release.py` called it. That is the
    pathology `scripts/publication.py` names in its own words — a promise in a
    docstring is not a check — arriving in the one place where failing to keep
    it means a credential leaves the node.

    Reads the composition in memory rather than the output directory, because
    the order matters: the caller writes only after this returns clean. A gate
    that must write first and check second reports the credential it has
    already shipped.

    The exemption list is deliberately not consulted. It names repository paths
    whose purpose is to hold fake credentials so that redaction can be tested;
    a release artifact is not a place any of that belongs, so an artifact
    finding is a finding whether or not some source file of the same name is
    exempt.
    """
    mod = _secrets_module()
    body = [(content, rel) for content, rel, _executable in files]
    for rel, value in manifests.items():
        body.append((_json_bytes(value), Path(rel)))
    body.append((readme.encode("utf-8"), Path("README.md")))

    findings: list[dict] = []
    for content, rel in body:
        if rel.suffix not in mod.TEXT_SUFFIXES:
            continue
        if len(content) > mod.MAX_BYTES:
            continue
        try:
            text = content.decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            # A binary is not evidence either way, and the composition is text
            # by construction — the same reason `scan_file` stays quiet here.
            continue
        for hit in mod.scan_text(text):
            findings.append({"path": rel.as_posix(), **hit})
    return findings


def validate_packs(contract_path: Path, ability_repo: Path, ability_rev: str) -> dict:
    """Refuse a pack that names an ability the catalogue does not have.

    `abilities/pack-contract.yaml` declared this rule and nothing implemented
    it. A `grep` for that filename over this repository returned one hit: the
    contract itself. The rule said "refused at build time, not carried with a
    marker beside it", and there was no build-time refusal anywhere — so three
    of the four packs named a required member that resolved to nothing, and the
    only place that surfaced was a reader who happened to look.

    The pin is checked here too, and separately. A pack whose members all
    resolve against the wrong catalogue is the case this cannot afford to miss:
    it is the one that goes green.
    """
    contract = yaml.safe_load(contract_path.read_text(encoding="utf-8"))
    packs = _packs_module()

    # Two sources, deliberately. The contract supplies the RULES and the
    # declarations supply the PACKS, and they now live in two repositories —
    # which is the point of `publishing.location`, and also the thing that
    # makes the assembly worth doing explicitly instead of letting `resolve()`
    # read `contract["packs"]` and quietly get whatever the local file had.
    # `resolve()` is unchanged: it still asks whether the packs a contract
    # declares close against a catalogue. This is where those packs come from.
    assembled = dict(contract, packs=published_packs(ability_repo, ability_rev))
    report = packs.resolve(assembled, catalogued_abilities(ability_repo, ability_rev),
                           catalogue_revision=ability_rev, catalogue_repo=str(ability_repo))

    if report["refused"]:
        # The MEMBER is named, not just the pack. "pack:0 does not exist" reads
        # as a broken pack; the thing that is actually absent is an ability, and
        # the two have different repairs — fix the pack, or add the ability.
        # The same argument the pack's own `resolution` block makes about never
        # dropping an unresolved member, applied to the message that reports it.
        missing = ["%s -> %s" % (p["id"], ", ".join(m["ability"] for m in p["unresolved_required"]))
                   for p in report["packs"] if p["refused"]]
        raise ValueError(
            f"pack(s) name a required ability that does not exist in "
            f"ability-management@{ability_rev[:12]}: {'; '.join(missing)}. A pack "
            "is names, and a name that resolves to nothing is installed happily "
            "and planned around")
    # The pin verdict is finished here rather than inside `resolve()`, and it is
    # finished by a call and not by a loop. It was a loop here for one revision
    # and that is exactly how this repository produced two answers to one
    # question: `abilities/packs.py` has a command-line entry point, and with
    # the ancestor rule living only in this file that entry point printed
    # `FAIL ... PIN_MISMATCH` over the very same files this function built
    # successfully. `settle_pins()` is the one implementation, and it lives
    # beside the verdict vocabulary it writes into.
    drifted = packs.settle_pins(report, revision=ability_rev, repo=ability_repo)

    # Summarized AFTER the pin upgrade, not before it. It was printed before
    # for one revision, and the build log then carried five `FAIL ...
    # PIN_MISMATCH` lines that the block above immediately upgraded and
    # accepted. A log that prints FAIL over a build that succeeds is worse than
    # a quiet one: it teaches whoever reads it that FAIL is noise, which is the
    # cost the whole pin mechanism was added to avoid paying.
    line = packs.summarize(report)
    if line:
        print(line)

    if drifted:
        raise ValueError(
            "packs pin a catalogue revision other than the one being built "
            f"against: {['%s=%s' % (v['pack'], str(v['pinned'])[:12]) for v in drifted]} "
            f"vs {ability_rev[:12]}. Revalidate and repin, or the closure above "
            "is about a catalogue nobody can produce")
    return report


def local_skill_files(root: Path) -> list[tuple[Path, Path]]:
    """(absolute source, path relative to the skill directory) for a local skill."""
    if not root.is_dir():
        raise ValueError(f"local skill directory is missing: {root}")
    pairs: list[tuple[Path, Path]] = []
    base = root.resolve()
    for path in sorted(root.rglob("*")):
        if any(part in EXCLUDED_DIRS for part in path.parts):
            continue
        if path.name in EXCLUDED_DIRS or path.suffix in EXCLUDED_SUFFIXES:
            continue
        if path.is_symlink():
            raise ValueError(f"symlinks are not allowed in a local skill: {path}")
        if path.is_file():
            if os.path.commonpath((str(path.resolve()), str(base))) != str(base):
                raise ValueError(f"local skill entry escapes its root: {path}")
            pairs.append((path, path.relative_to(root)))
    return pairs


def ability_skill_files(repo: Path, rev: str, name: str) -> list[tuple[bytes, Path, bool]]:
    """(content, path relative to the skill directory, executable) at a pinned revision.

    Reads objects directly, so the state of any working tree is irrelevant.

    The mode is read from the tree rather than guessed from the filename. git
    already tracks whether a path is a program, so a payload entry that is
    executable upstream arrives executable here; the suffix heuristic this
    replaces got the extensionless ones wrong, which is every entry point.
    """
    prefix = f"skills/{name}/payload/"
    listing = _git(repo, "ls-tree", "-r", rev, "--", f"skills/{name}/payload")
    if not listing.strip():
        raise ValueError(f"ability-management@{rev[:12]} has no payload for skill: {name}")
    pairs: list[tuple[bytes, Path, bool]] = []
    for line in sorted(listing.decode().splitlines()):
        if not line:
            continue
        # `<mode> SP <type> SP <object> TAB <path>`
        meta, _, entry = line.partition("\t")
        if not entry:
            raise ValueError(f"unparsable ls-tree entry: {line!r}")
        rel = entry[len(prefix):] if entry.startswith(prefix) else None
        if rel is None:
            raise ValueError(f"unexpected payload entry outside {prefix}: {entry}")
        parts = Path(rel).parts
        if any(part in EXCLUDED_DIRS for part in parts) or Path(rel).suffix in EXCLUDED_SUFFIXES:
            continue
        mode = meta.split(" ", 1)[0]
        pairs.append((_git(repo, "show", f"{rev}:{entry}"), Path(rel),
                      mode in EXECUTABLE_GIT_MODES))
    return pairs


def claude_plugin_manifest(bundle: dict, version: str, skills: list[str]) -> dict:
    manifest = {
        "name": bundle["name"],
        "version": version,
        "description": bundle["description"],
        "author": {"name": bundle["author"]},
        "skills": [f"./skills/{n}" for n in skills],
        "license": bundle["license"],
    }
    # Named explicitly rather than left to convention. A plugin-root `.mcp.json`
    # is discovered by default, so this field is redundant today — and it is the
    # one line that says, in the manifest itself, that this plugin owns a node
    # surface. A convention is not a declaration.
    if _declared_servers(bundle):
        manifest["mcpServers"] = "./.mcp.json"
    return manifest


def codex_plugin_manifest(bundle: dict, version: str) -> dict:
    manifest = {
        "name": bundle["name"],
        "version": version,
        "description": bundle["description"],
        "author": {"name": bundle["author"]},
        "skills": "./skills/",
        "license": bundle["license"],
        "interface": {
            "displayName": bundle["display_name"],
            "shortDescription": bundle["short_description"],
            "longDescription": bundle["long_description"],
            "developerName": bundle["author"],
            "category": bundle["category"],
            "capabilities": ["Skills", "Agent Engineering Workbench", "Federated governance"],
            "defaultPrompt": list(bundle["default_prompt"]),
            "brandColor": bundle["brand_color"],
        },
    }
    if _declared_servers(bundle):
        manifest["mcpServers"] = "./.codex-mcp.json"
    return manifest


def claude_marketplace_manifest(bundle: dict, version: str) -> dict:
    return {
        "name": bundle["name"],
        "description": f"{bundle['name']} self-hosted marketplace",
        "owner": {"name": bundle["author"]},
        "plugins": [
            {
                "name": bundle["name"],
                "source": ".",
                "version": version,
                "description": bundle["description"],
                "license": bundle["license"],
            }
        ],
    }


def _is_transient(rel: Path) -> bool:
    """Whether a path names a directory nobody promised to keep.

    A component beginning with a dot is one of those. `.staging-<revision>` is
    where a build is written before a release moves it into `revisions/`, and
    the name says as much. The descriptor below outlives it: measured on this
    node, `runtime-assets/hwz-server-os/.agents/plugins/marketplace.json` named
    `./.staging-c233946` while `current` resolved to the revision built out of
    that very directory. Nothing broke — nothing here reads that file — but a
    descriptor that names a disposable directory is one cleanup away from
    naming nothing, and the cleanup is exactly what §5's staging rule asks an
    operator to consider.
    """
    return any(part.startswith(".") for part in rel.parts)


def codex_marketplace(bundle: dict, output: Path, marketplace_path: Path) -> dict:
    """Render the local Codex marketplace that points at the generated payload."""
    marketplace_root = marketplace_path.resolve().parent.parent.parent
    relative_payload = Path(os.path.relpath(output.resolve(), marketplace_root))
    if relative_payload.parts[:1] == ("..",):
        raise ValueError("plugin output must be inside the Codex marketplace root")
    # Where the payload WILL live, which is not always where it was built. A
    # build to a scratch directory is a normal thing to do; recording the
    # scratch directory in a descriptor is not, because the descriptor is read
    # long after the build. `release.py` promotes a composition into
    # `<runtime-root>/<composition>/current`, so that is the stable name, and
    # it is derived from the bundle rather than from this invocation.
    source_path = relative_payload
    if _is_transient(relative_payload):
        source_path = Path(bundle["composition"]) / "current"
    return {
        "name": f"{bundle['name']}-local",
        "interface": {"displayName": f"{bundle['display_name']} Local"},
        "plugins": [
            {
                "name": bundle["name"],
                "source": {"source": "local", "path": "./" + source_path.as_posix()},
                "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
                "category": bundle["category"],
            }
        ],
    }


def _declared_servers(bundle: dict) -> list[dict]:
    module = bundle.get("mcp", {}).get("path")
    servers = bundle.get("mcp", {}).get("servers") or []
    if servers and not module:
        raise ValueError("bundle declares mcp servers but no mcp module path")
    for entry in servers:
        for key in ("name", "command", "entry"):
            if not entry.get(key):
                raise ValueError(f"mcp server entry is missing {key!r}: {entry!r}")
        # The entry is joined onto the module path, so a `..` in it would name a
        # path the artifact does not contain: the server would be declared and
        # unable to start, which reads as a runtime fault rather than a typo.
        parts = Path(entry["entry"]).parts
        if Path(entry["entry"]).is_absolute() or ".." in parts:
            raise ValueError(f"mcp entry must stay inside {module}/: {entry['entry']!r}")
        if not (repo_root() / module / entry["entry"]).is_file():
            raise ValueError(f"mcp entry does not exist: {module}/{entry['entry']}")
    return servers


def claude_mcp_manifest(bundle: dict) -> dict:
    """The plugin-root `.mcp.json`, in the `{"mcpServers": {...}}` shape.

    A plugin-root `.mcp.json` is not the same file as a project `.mcp.json`, but
    the runtime loader accepts the same two encodings, because it starts by
    taking `document.mcpServers || document` and only then iterates entries.
    Both therefore load identically, and every first-party plugin in the
    official marketplace ships the bare map. We emit the wrapped form anyway:

      * it is the documented one, so it is what a reader and `claude plugin
        validate` expect;
      * the bare form silently changes meaning if a server is ever named
        `mcpServers`, and "works until someone picks the wrong name" is not a
        property worth shipping.

    The difference is invisible at runtime, which is exactly why it is written
    down here rather than rediscovered later.
    """
    module = bundle.get("mcp", {}).get("path")
    servers = {}
    for entry in _declared_servers(bundle):
        server = {
            "command": entry["command"],
            "args": [f"${{CLAUDE_PLUGIN_ROOT}}/{module}/{entry['entry']}"],
        }
        if entry.get("env"):
            server["env"] = dict(entry["env"])
        servers[entry["name"]] = server
    return {"mcpServers": servers}


def codex_mcp_manifest(bundle: dict) -> dict:
    """The Codex equivalent.

    Two things differ from the Claude side and both are load-bearing:

      * Codex has no plugin-root placeholder, so `command`/`args` are relative
        and `cwd` is the plugin root.
      * `.codex-mcp.json` is NOT a filename Codex looks for. It appears nowhere
        in the codex binary. It is just a filename one installed plugin happens
        to use, and that plugin only reaches its servers because its manifest
        points at the file explicitly. The manifest below does the same, so the
        name is an arbitrary local choice rather than an assumed convention.

    Field order and the presence of `env` are kept identical to the Claude file
    so that the diff between the two is exactly the two differences above.
    """
    module = bundle.get("mcp", {}).get("path")
    servers = {}
    for entry in _declared_servers(bundle):
        server = {
            "command": entry["command"],
            "args": [f"./{module}/{entry['entry']}"],
        }
        if entry.get("env"):
            server["env"] = dict(entry["env"])
        server["cwd"] = "."
        servers[entry["name"]] = server
    return {"mcpServers": servers}


def build_readme(bundle: dict, version: str, skills: list[str], ability_rev: str) -> str:
    servers = _declared_servers(bundle)
    surface = ""
    if servers:
        surface = (
            "Node surface: " + ", ".join(e["name"] for e in servers) + "\n"
            "(`.mcp.json` for Claude, `.codex-mcp.json` for Codex). A thin stdio adapter over "
            "the local `serverosd` socket, holding no state of its own; when the daemon is not "
            "running it reports that rather than answering with an empty result.\n\n"
        )
    recommended = bundle.get("recommended") or []
    on_demand = ""
    if recommended:
        on_demand = (
            "Recommended, not shipped: "
            + ", ".join(f"`{e['name']}` ({e['status']})" for e in recommended)
            + ". These are resolved per node rather than bundled, so this artifact does not "
            "contain them; an entry marked `missing` is a gap the composition records rather "
            "than papers over.\n\n"
        )
    return (
        f"# ProfessorZhi/{bundle['name']}\n\n"
        "Generated by `src/serveros/scripts/build.py`. Do not edit by hand.\n\n"
        f"Composition: `{bundle['composition']}`. The composition file is the authority for "
        "what this artifact contains; the pin below is the authority for what it was read "
        "from.\n\n"
        "Provided skills: " + ", ".join(skills) + "\n\n"
        + surface
        + on_demand
        + f"Generated version: `{version}` (cachebuster suffix `{bundle['cachebuster']}`).\n\n"
        f"Reusable skills read from `ability-management@{ability_rev}`. "
        "Canonical identity alias: `ProfessorZhi/workspace-maintainer` is presented as the "
        "`workspace-management` runtime name/path.\n\n"
        "Keep the cachebuster fixed for byte-identical rebuilds; bump it once only when a "
        "new generated payload must invalidate an installed plugin cache.\n"
    )


def declared_artifact_kinds(bundle: dict, bundle_path: Path) -> list[str]:
    """Which artifact kinds this composition is released as.

    Required, and required HERE rather than at release time, because the two
    facts it separates were previously one fact: a composition's NAME was doing
    double duty as "what this is" and "what may be done with it". Core and the
    linux-work profile are both a node runtime AND a plugin payload — the same
    bytes, two faces, released by two different paths — and a manifest that could
    only say one of them would make the release path guess which. Guessing is what
    this key removes.

    The vocabulary of kinds is NOT defined here. This records what the composition
    says; `release.py` owns the closed set and refuses a kind it has no verifier
    for. Two copies of the list is how the two would drift.
    """
    kinds = bundle.get("artifacts")
    if not isinstance(kinds, list) or not kinds:
        raise ValueError(
            f"{bundle_path} declares no `artifacts:`. Every composition states "
            f"which artifact kinds it is released as (e.g. [node-runtime, plugin], "
            f"or [plugin] for a payload with no daemon). Without it an artifact "
            f"carries no statement about what it is, and the release path would "
            f"have to infer one from its name or its contents."
        )
    if not all(isinstance(k, str) and k.strip() for k in kinds):
        raise ValueError(f"{bundle_path}: `artifacts:` entries must be non-empty strings")
    if len(set(kinds)) != len(kinds):
        raise ValueError(f"{bundle_path}: `artifacts:` repeats a kind: {kinds}")
    return list(kinds)


def source_identity(repo: Path, ability_repo: Path, ability_rev: str) -> dict:
    """What this artifact was assembled FROM, as far as the build can prove it.

    Two claims that a reader keeps wanting to collapse into one, and the whole
    reason this record exists is that they are not one:

      the COMMIT     names a revision of the repository the artifact was read
                     from. It is a fact about that repository, and it is
                     available whenever the build ran inside a checkout.
      the CONTENT    is what actually went into the bytes. `tree_clean` is the
                     only field that connects the two: an artifact assembled
                     from a checkout with uncommitted work HAS a commit and HAS
                     no content identity, and reporting the commit without the
                     flag is exactly the false attribution this record exists to
                     prevent. The artifact's own tree hash answers a different
                     question — which bytes, not which source.

    A build outside a git checkout is not an error and not a zero: `basis` says
    which of the two facts is unavailable and why, so a reader gets UNKNOWN with
    a reason rather than a commit that was never observed.
    """
    def _run(*args: str) -> str | None:
        # `status --porcelain` is one of the subcommands below, and git is
        # allowed to refresh the index while answering it — which would make a
        # question about a checkout into a write to that checkout.
        # `GIT_OPTIONAL_LOCKS=0` is what makes the "read-only git" claim in
        # `runtime/actuator-contract.yaml` true for this call site, the same
        # way it does for `architecture_check.py` and `map_projection.py`; the
        # sibling `_git()` above needs no such guard because every subcommand
        # it is given is a `rev-parse` or a `ls-tree`.
        env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
        proc = subprocess.run(["git", "-C", str(repo), *args],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              env=env)
        if proc.returncode != 0:
            return None
        return proc.stdout.decode(errors="replace").strip()

    commit = _run("rev-parse", "--verify", "HEAD^{commit}")
    porcelain = _run("status", "--porcelain") if commit else None
    dirty = None if porcelain is None else [l for l in porcelain.splitlines() if l.strip()]
    ref = _run("rev-parse", "--abbrev-ref", "HEAD") if commit else None
    basis = ("read from the checkout this build ran in"
             if commit is not None else
             "this build did not run inside a git checkout, so no source "
             "revision can be named — the artifact's tree hash still identifies "
             "its bytes, and it does not identify a commit")
    return {
        "serveros": {
            "commit": commit,
            "ref": ref,
            "tree_clean": (None if dirty is None else not dirty),
            "worktree_changes": (None if dirty is None else len(dirty)),
            "basis": basis,
        },
        "ability_management": {
            "repo": str(ability_repo),
            "revision": ability_rev,
        },
    }


def artifact_manifest(bundle: dict, version: str, skills: list[str],
                      members: list[str], kinds: list[str],
                      source: dict) -> dict:
    """The build's statement about what it produced, written INTO the artifact.

    It travels with the bytes for the same reason the composition declaration in
    the README does: a consumer that has the artifact and not the repository is
    the normal case for a distribution, and every fact here is one such a
    consumer is entitled to check without a checkout.

    `members` is the complete list of what was written, and it is what turns "no
    undeclared files" into a comparison against a record instead of a rule about
    which filenames look suspicious.

    `source` is the artifact naming its own provenance, frozen at build time.
    It is here rather than derived by whoever reads the artifact because the
    derivation needs the repository, and the artifact is read precisely where
    the repository is not — the daemon that runs it holds no checkout and must
    never go looking for one.
    """
    return {
        "schema": "serveros/artifact.v1",
        "composition": bundle["composition"],
        "name": bundle["name"],
        "version": version,
        "cachebuster": bundle["cachebuster"],
        "kinds": kinds,
        "skills": sorted(skills),
        # Names, not the entries: the release-side check asks whether the MCP
        # manifests the payload carries declare the same SERVER NAMES as the
        # composition, and a record of the commands would be a second copy of
        # facts the payload already states.
        "mcp_servers": sorted(entry["name"] for entry in _declared_servers(bundle)),
        "members": members,
        "source": source,
    }


def plan(bundle_path: Path, ability_repo: Path, ability_rev: str, output: Path,
         marketplace_path: Path):
    bundle = load_composition(bundle_path)
    version = f"{bundle['version']}+{bundle['cachebuster']}"
    kinds = declared_artifact_kinds(bundle, bundle_path)

    local_root = repo_root() / "skills"
    validate_recommended(bundle, local_root, ability_repo, ability_rev)
    files: list[tuple[bytes, Path, bool]] = []
    skills: list[str] = []
    for entry in bundle["skills"]:
        name = entry["name"]
        source = entry["source"]
        skills.append(name)
        if source == "local":
            for src, rel in local_skill_files(local_root / name):
                files.append((src.read_bytes(), Path("skills") / name / rel,
                              is_executable(src)))
        elif source == "ability-management":
            for content, rel, executable in ability_skill_files(ability_repo, ability_rev, name):
                files.append((content, Path("skills") / name / rel, executable))
        else:
            raise ValueError(f"unknown skill source for {name}: {source!r}")

    # ServerOS Core modules ship verbatim. Each declares its own path under
    # bundle.yaml, so adding a module is one bundle key plus one directory —
    # never a second vocabulary of module names hardcoded here.
    for module in CORE_MODULES:
        module_rel = bundle.get(module, {}).get("path")
        if not module_rel:
            continue
        if module_rel != module:
            raise ValueError(f"{module}: bundle path must be {module!r}, got {module_rel!r}")
        for src, rel in local_skill_files(repo_root() / module_rel):
            files.append((src.read_bytes(), Path(module_rel) / rel, is_executable(src)))

    manifests = {
        Path(".claude-plugin/plugin.json"): claude_plugin_manifest(bundle, version, skills),
        Path(".claude-plugin/marketplace.json"): claude_marketplace_manifest(bundle, version),
        Path(".codex-plugin/plugin.json"): codex_plugin_manifest(bundle, version),
    }
    if _declared_servers(bundle):
        # Two files, two shapes. Emitted together so that neither runtime can
        # drift into being the only one this plugin actually reaches.
        manifests[Path(".mcp.json")] = claude_mcp_manifest(bundle)
        manifests[Path(".codex-mcp.json")] = codex_mcp_manifest(bundle)
    readme = build_readme(bundle, version, skills, ability_rev)
    marketplace = codex_marketplace(bundle, output, marketplace_path)

    # The artifact manifest is one of the generated JSON files, which is what
    # makes it written by `--write`, content-checked by `--check`, included in
    # the expected-file set, and present in `runtime_truth`'s recomputation —
    # all of it by being in `manifests`, rather than by four call sites each
    # remembering it exists. The member list is the fixed point of that choice:
    # it names the manifest itself, because a list of what was written that
    # omitted one of the things written would be a false statement.
    members = sorted({str(rel) for _c, rel, _e in files}
                     | {str(rel) for rel in manifests}
                     | {"README.md", ARTIFACT_MANIFEST_NAME})
    manifests[Path(ARTIFACT_MANIFEST_NAME)] = artifact_manifest(
        bundle, version, skills, members, kinds,
        source_identity(repo_root(), ability_repo, ability_rev))
    return files, manifests, readme, marketplace, version


def _json_bytes(value: dict) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode()


def build(bundle_path: Path, ability_repo: Path, ability_rev: str, output: Path,
          marketplace_path: Path, write: bool, pack_contract_path: Path) -> int:
    # Validated here and not in `plan()`. The two are used for different
    # questions: `build()` produces the artifact, and `runtime_truth.py` calls
    # `plan()` to RECOMPUTE an installed artifact from a recorded pin. A pack
    # pin and the revision being built against are the same fact when a release
    # is made, and two different facts when an old artifact is being re-derived
    # — so putting the check in `plan()` made every recomputation of a
    # historical artifact refuse for a reason that was about the release
    # calendar rather than about the artifact.
    validate_packs(pack_contract_path, ability_repo, ability_rev)
    files, manifests, readme, marketplace, _version = plan(
        bundle_path, ability_repo, ability_rev, output, marketplace_path
    )

    expected_files = {rel for _content, rel, _executable in files} | set(manifests) | {Path("README.md")}

    # Before either branch, and before anything is written or deleted.
    # `--check` also scans the tree on disk, because a file nobody composed
    # into this artifact is exactly where a credential would be hiding and
    # `--check` is the mode an operator runs against a promoted node.
    secrets = scan_composed(files, manifests, readme)
    if not write and output.exists():
        for hit in _secrets_module().scan(output)["findings"]:
            secrets.append(hit)

    def report_secrets() -> None:
        # A credential is not drift. `DRIFT` means the artifact differs from
        # what this composition says it should be, which is a state an operator
        # resolves by rebuilding; this is a state they resolve by rotating. One
        # word for two problems sends the reader to the wrong one.
        for hit in secrets:
            print(f"SECRET {hit['path']}:{hit['line']} {hit['rule']}", file=sys.stderr)

    if secrets and write:
        # Refuse BEFORE the prune below, so a refusal cannot have already
        # deleted the artifact it is refusing to replace.
        report_secrets()
        print(f"REFUSING: {len(secrets)} credential-shaped string(s) in the "
              f"composition. Nothing was written.", file=sys.stderr)
        return 1

    if not write:
        # A bare exit code is not actionable. Name every drifted path, so that a
        # red `--check` says what to fix rather than only that something changed.
        drifted: set[str] = set()
        actual = {p.relative_to(output) for p in output.rglob("*") if p.is_file()} if output.exists() else set()
        drifted |= {f"unexpected {rel}" for rel in actual - expected_files}
        drifted |= {f"missing {rel}" for rel in expected_files - actual}
        for content, rel, executable in files:
            if (output / rel).is_file() and (output / rel).read_bytes() != content:
                drifted.add(f"content {rel}")
            # Mode is checked, not just written. An artifact whose entry point
            # lost its executable bit has the right bytes and cannot run, and
            # `--check` reporting it clean is how that reaches a promoted node.
            elif executable and (output / rel).is_file() and not is_executable(output / rel):
                drifted.add(f"mode {rel} (not executable)")
        for rel, value in manifests.items():
            if (output / rel).is_file() and (output / rel).read_bytes() != _json_bytes(value):
                drifted.add(f"content {rel}")
        if (output / "README.md").is_file() and (output / "README.md").read_text() != readme:
            drifted.add("content README.md")
        if not marketplace_path.is_file():
            drifted.add(f"missing {marketplace_path}")
        elif marketplace_path.read_bytes() != _json_bytes(marketplace):
            drifted.add(f"content {marketplace_path}")
        # Both classes are reported in one run. `--check` writes nothing, so
        # stopping at the credential would only make the operator run it twice
        # to learn the rest — and the second run would report the same
        # credential first, every time.
        report_secrets()
        for reason in sorted(drifted):
            print(f"DRIFT {reason}", file=sys.stderr)
        return int(bool(drifted or secrets))

    # The output directory is generated-only: remove everything not in the
    # expected set, then prune directories left empty so no residue of excluded
    # build inputs survives.
    if output.exists():
        for path in sorted((p for p in output.rglob("*") if p.is_file()), reverse=True):
            if path.relative_to(output) not in expected_files:
                path.unlink()
        for path in sorted((p for p in output.rglob("*") if p.is_dir()), reverse=True):
            if not any(path.iterdir()):
                path.rmdir()

    for content, rel, executable in files:
        dst = output / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(content)
        # `.sh`/`.py` keeps its executable bit because those are scripts nobody
        # runs any other way; everything else follows the source. The previous
        # rule applied the bit only to those two suffixes, which excluded every
        # entry point in the composition.
        if executable or dst.suffix in (".sh", ".py"):
            dst.chmod(0o755)
    for rel, value in manifests.items():
        dst = output / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(_json_bytes(value))
    (output / "README.md").write_text(readme)
    marketplace_path.parent.mkdir(parents=True, exist_ok=True)
    marketplace_path.write_bytes(_json_bytes(marketplace))
    return 0


def main() -> int:
    root = repo_root()
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--check", action="store_true", help="report drift without writing")
    ap.add_argument("--write", action="store_true", help="regenerate the artifact")
    ap.add_argument("--bundle", type=Path, default=root / "plugin/bundle.yaml")
    ap.add_argument("--ability-repo", type=Path,
                    default=Path(os.environ.get("SERVEROS_ABILITY_REPO", "")) if os.environ.get("SERVEROS_ABILITY_REPO") else None)
    ap.add_argument("--ability-rev", required=True,
                    help="pinned ability-management revision the reusable Skills are read from")
    # The runtime artifact has exactly one home: the stable distribution root.
    # Requiring an explicit destination keeps this repository a source tree and
    # prevents a second, repo-local plugin payload from appearing alongside it.
    ap.add_argument("--output", type=Path, required=True,
                    help="runtime artifact destination, e.g. "
                         "$DATA_ROOT/runtime-assets/hwz-server-os/current")
    ap.add_argument("--pack-contract", type=Path,
                    help="the ability-pack contract to validate the catalogue against.\n                         Defaults to this repository's own. Injectable for the same\n                         reason --bundle is: a hermetic test builds a synthetic\n                         catalogue, and a contract pinning a real revision would\n                         refuse it for a reason that has nothing to do with the\n                         code under test.")
    ap.add_argument("--marketplace-path", type=Path, required=True,
                    help="Codex marketplace descriptor, e.g. "
                         "$DATA_ROOT/runtime-assets/hwz-server-os/.agents/plugins/marketplace.json")
    args = ap.parse_args()
    if args.check == args.write:
        ap.error("choose exactly one of --check or --write")
    if args.ability_repo is None:
        ap.error("--ability-repo is required (or set SERVEROS_ABILITY_REPO)")
    try:
        rev = resolve_ability_rev(args.ability_repo, args.ability_rev)
        if rev != args.ability_rev:
            raise ValueError(
                f"--ability-rev must be a full commit SHA; {args.ability_rev} resolves to {rev}"
            )
        return build(args.bundle, args.ability_repo, rev, args.output,
                     args.marketplace_path, args.write,
                     args.pack_contract or (root / "abilities" / "pack-contract.yaml"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
