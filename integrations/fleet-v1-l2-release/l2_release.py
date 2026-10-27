#!/usr/bin/env python3
"""Two L2 artifacts that can be released separately: the device node, the Tokyo hub.

What this is for
----------------
D7 of the V1 gap plan says the node connector and the Tokyo hub still share one
release surface: both are `fleet_mcp/`, versioned together, shipped together, and
rolled back together. That is not a packaging complaint. A device that changes
the connector's HTTP contract and a hub that changes its OAuth scopes are two
different upgrades with two different blast radii, and while they ride in one
artifact, neither can be released without the other.

This module defines the two artifacts — `l2-node` and `l2-hub` — as **two
kinds**, with two versions, two member lists, two dependency lists and two
verifiers. It builds them deterministically and offline from the source tree,
reads them back without extracting them, and refuses the ways a package can lie:

* a node payload carrying the hub's entry (`server.py`, `tools.py`, `oauth.py`)
  or any node machine's credentials/unit files;
* a hub payload carrying `fleet_mcp/node/**`;
* a member whose bytes are not the bytes the manifest recorded;
* an extra file the manifest does not list, or a listed file that is missing;
* a declared kind that is not the kind the payload is;
* a version in the manifest that is not the version that was requested;
* a third-party import that the manifest does not declare;
* a `depends_on` entry naming a module the payload does not carry;
* a vendored copy of the CloudCLI tool schema (the provider forwards the live
  schema; a copy is the thing that goes stale silently);
* a platform the payload claims but the kind does not support, or one it fails to
  claim although the kind ships there;
* an intra-package import the payload does not carry (`from .report import x`
  with no `fleet_mcp/report.py`);
* an `l2` block that contradicts the payload — a node claiming the hub's
  provides, a hub listing a shared surface, a `standalone` flag disagreeing with
  its own dependency lists;
* a member name with a second spelling (`fleet_mcp/./node/connector.py`), a
  duplicate name, a link, or bytecode;
* a pin entry the closure no longer reaches, so `NODE_SHARED_SURFACE` cannot rot
  into a list nobody checks.

It does not install anything, start anything, talk to anything, or read a
credential. `pair()` and `rollback_target()` describe a rollback; they perform
none.

What it does NOT claim
----------------------
The split is not real yet, and this module says so with numbers rather than with
a disclaimer. On `main` today both closures are **30 files** and they differ by
two modules on each side (`node/{__init__,connector}.py` versus
`{server,tools}.py`), so **28 modules are in both** — the `fleet_mcp` package is
the release surface, not a shared subset of it. The node additionally drags in
`fleet` (the repository root), `registry`, `audit`, `dispatch`, `privacy`,
`scopes`, `session_detail`, `feishu_sso`, `providers/fleet_node` and more,
because of three import edges that live in files #407 owns:

    fleet_mcp/node/connector.py  ->  ..fleet, ..config, ..providers.cloudcli
    fleet_mcp/providers/__init__ ->  providers.fleet_node
    fleet_mcp/config.py          ->  feishu_sso, discovery, owner_access,
                                     runtime_create_policy, scopes

Cutting those edges is a source change in files this package may not write. So
this module PINS the shared surface (`NODE_SHARED_SURFACE`) instead of pretending
it is zero: today's shape is legal and recorded, tomorrow's new coupling is a
build failure that names the module. Shrinking the pin is always allowed — and a
pin entry the closure no longer reaches is also a build failure, so the list
cannot rot into a claim nobody checks. See HANDOFF-407.md for the exact edges.

The manifest is `serveros/artifact.v1`: `scripts/build.py` writes it,
`scripts/release.py` reads it. The two kinds are new, so the release tool refuses
them by name today. The risk on the other side of registration is that
`verify_revision` falls through to `verify_node_runtime` for any *registered*
kind it does not name — an unknown kind is refused before dispatch
(`release.py:620`), a registered one is not. HANDOFF-419.md is the change that
closes that hole; until it lands, an L2 artifact cannot be promoted by accident.
"""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import re
import sys
import tarfile
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path, PurePosixPath

#: The manifest file name and schema, byte for byte what `scripts/release.py`
#: reads (`ARTIFACT_MANIFEST`, `ARTIFACT_MANIFEST_SCHEMA`). They are repeated
#: here rather than imported because this package must keep working while the
#: release tool is being rewritten by its owner; a test pins the two together.
ARTIFACT_MANIFEST = "ARTIFACT.json"
ARTIFACT_MANIFEST_SCHEMA = "serveros/artifact.v1"

L2_NODE = "l2-node"
L2_HUB = "l2-hub"
L2_KINDS = (L2_NODE, L2_HUB)

COMPOSITION = "fleet-l2"
NAMES = {L2_NODE: "fleet-l2-node", L2_HUB: "fleet-l2-hub"}

#: The module whose closure IS the artifact. A kind is defined by one entrypoint,
#: so "what is in this artifact" is computed, never listed by hand.
ENTRY = {L2_NODE: "fleet_mcp.node.connector", L2_HUB: "fleet_mcp.server"}

#: What each kind must contain, expressed as the file the kind exists for.
REQUIRED_MEMBERS = {
    L2_NODE: ("fleet_mcp/__init__.py", "fleet_mcp/node/__init__.py",
              "fleet_mcp/node/connector.py"),
    L2_HUB: ("fleet_mcp/__init__.py", "fleet_mcp/server.py"),
}

#: Kinds answer to different machines. A node ships to every device; the hub is
#: one Linux host. A payload that declares a platform its kind does not support is
#: refused, because "it packaged on Windows" is not "it runs there".
PLATFORMS = {L2_NODE: ("linux", "windows"), L2_HUB: ("linux",)}

#: What each kind is for. Stated once because the manifest writes it and the
#: verifier compares it: a payload cannot describe itself as providing something
#: the other kind provides.
PROVIDES = {L2_NODE: {"node-connector": True},
            L2_HUB: {"hub-ingress": True, "mcp-tools": True}}

#: Never in a node artifact, whatever the closure says: these are the hub's own
#: ingress and its operator face. The connector must not be able to start an MCP
#: server, serve tools, or run OAuth because it was imported.
NODE_DENIED_MODULES = (
    "fleet_mcp.server",
    "fleet_mcp.tools",
    "fleet_mcp.oauth",
    "fleet_mcp.owner_setup",
)
NODE_DENIED_PREFIXES = ("fleet_mcp/deploy/", "fleet_mcp/bin/")

#: Anything under this package belongs to the node and needs no pin: a new
#: node-private module is the node's own business, not evidence of hub coupling.
NODE_PUBLIC_PREFIX = "fleet_mcp.node"

#: Bytecode is build output, not source: a sourceless `fleet_mcp/server.pyc` is
#: importable, so a rule about `server.py` that ignores `server.pyc` is a rule
#: with a documented way around it.
FORBIDDEN_SUFFIXES = (".pyc", ".pyo")

#: The measured shared surface, frozen. These modules are in the node's closure
#: today because the three edges in the module docstring pull them in. They are
#: legal; a module NOT in this tuple — and not under `NODE_PUBLIC_PREFIX` — is
#: new coupling and fails the build. When an edge is cut, delete the entry here in
#: the same change: an entry the closure no longer reaches is *also* a build
#: failure, so this list cannot rot into a claim nobody checks.
NODE_SHARED_SURFACE = (
    "fleet_mcp.audit",
    "fleet_mcp.cloudcli_native",
    "fleet_mcp.core_lite_readback",
    "fleet_mcp.core_lite_source",
    "fleet_mcp.disclosure",
    "fleet_mcp.discovery",
    "fleet_mcp.dispatch",
    "fleet_mcp.feishu_sso",
    "fleet_mcp.fleet",
    "fleet_mcp.mcp_http",
    "fleet_mcp.owner_access",
    "fleet_mcp.privacy",
    "fleet_mcp.providers.fleet_node",
    "fleet_mcp.registry",
    "fleet_mcp.runtime_create_policy",
    "fleet_mcp.scopes",
    "fleet_mcp.session_detail",
)

#: A hub payload carries the hub. It must not carry the device half, and it must
#: not carry anything that is a property of one installed machine rather than of
#: the software: credentials, bindings, units, launchers, private keys.
HUB_DENIED_PREFIXES = ("fleet_mcp/node/",)
HUB_DENIED_PATTERNS = (
    "*.local.yaml", "*.local.yml", "bindings/*", "*.service", "*.timer",
    "*.service.in", "*.ps1", "*.vbs", "*.pem", "*.key", ".env",
)

#: Applied to non-source files only: a `token_cache.py` is code, a `tokens.json`
#: is a credential. Letting these match module names would make every future file
#: with "token" in its name an argument with this guard instead of a decision.
HUB_SECRET_PATTERNS = ("*credential*", "*token*")

VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.\-]*)?(?:\+[A-Za-z0-9][A-Za-z0-9.\-]*)?$")

#: A file that is a copy of the CloudCLI tool schema rather than a forwarder of
#: it. The provider asks the running CLI; a payload that answers from a baked copy
#: keeps answering after the CLI moves.
SCHEMA_MARKER = "inputSchema"
SCHEMA_TOOL_HINT = "run_get"


class Refused(Exception):
    """A package, or a build, that must not be released."""


# --------------------------------------------------------------------------- #
# the source tree -> the module closure of one entrypoint
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Closure:
    modules: tuple[str, ...]
    files: dict[str, Path]      # dotted module -> file
    third_party: tuple[str, ...]
    external_in_repo: tuple[str, ...] = ()
    edges: dict[str, tuple[str, ...]] | None = None   # package -> importers


def _resolve(dotted: str, root: Path) -> Path | None:
    base = root.joinpath(*dotted.split("."))
    if base.with_suffix(".py").is_file():
        return base.with_suffix(".py")
    if (base / "__init__.py").is_file():
        return base / "__init__.py"
    return None


def _imports(path: Path, root: Path) -> list[str]:
    """Every module name this file imports, relative imports made absolute.

    `from ..errors import X` inside `fleet_mcp/node/connector.py` yields
    `fleet_mcp.errors` — resolved here, because a closure that cannot resolve a
    relative import reports a dependency on nothing and hides the very edge that
    makes the two artifacts one.
    """
    tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    package = path.relative_to(root).parts[:-1]
    out: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = list(package[: len(package) - (node.level - 1)])
                module = ".".join(base + ([node.module] if node.module else []))
            else:
                module = node.module or ""
            out.append(module)
            out.extend(f"{module}.{alias.name}" for alias in node.names)
    return out


def _in_repo_packages(root: Path) -> dict[str, Path]:
    """Top-level packages other than `fleet_mcp` that live in this repository.

    An import of one of these is not a dependency of the released software; it is
    a dependency on a checkout. That distinction is the whole of "the artifact is
    standalone", so it is measured rather than assumed: `fleet_mcp.registry` does
    `from fleet.transport import ...` at module import, and a node payload that
    carries `registry` therefore cannot start without `fleet/` beside it.
    """
    found: dict[str, Path] = {}
    for child in root.iterdir():
        if child.name.startswith((".", "_")) or child.name in ("fleet_mcp", "tests"):
            continue
        if not child.is_dir():
            continue
        # `fleet/` has no `__init__.py` and is still importable: it is a
        # namespace package, which works exactly as long as its parent is on
        # sys.path. That is the shape a checkout-shaped dependency takes here,
        # so both flavours count.
        if (child / "__init__.py").is_file() or any(child.glob("*.py")):
            found[child.name] = child
    return found


def closure(entry: str, root: Path) -> Closure:
    """The transitive `fleet_mcp` closure of one entrypoint, sorted and stable."""
    stdlib = set(getattr(sys, "stdlib_module_names", frozenset()))
    in_repo = _in_repo_packages(root)
    seen: dict[str, Path] = {}
    third: set[str] = set()
    external: set[str] = set()
    edges: dict[str, set[str]] = {}
    stack = [entry]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        path = _resolve(name, root)
        if path is None:
            top = name.split(".")[0]
            if top and top != "fleet_mcp" and top not in stdlib:
                third.add(top)
            continue
        seen[name] = path
        # A module cannot be imported without its packages: `a.b.c` needs
        # `a/__init__.py` and `a/b/__init__.py` or the import fails at runtime
        # even though the leaf file is there. The package names are not in the
        # import statement, so they are walked in rather than looked for.
        parts = name.split(".")
        for depth in range(1, len(parts)):
            stack.append(".".join(parts[:depth]))
        for imported in _imports(path, root):
            if imported.startswith("fleet_mcp"):
                stack.append(imported)
            elif imported:
                top = imported.split(".")[0]
                if top in in_repo:
                    external.add(top)
                    edges.setdefault(top, set()).add(name)
                elif top not in stdlib and top != "__future__":
                    third.add(top)
    return Closure(tuple(sorted(seen)), dict(sorted(seen.items())),
                   tuple(sorted(third)), tuple(sorted(external)),
                   {key: tuple(sorted(value)) for key, value in sorted(edges.items())})


# --------------------------------------------------------------------------- #
# manifest
# --------------------------------------------------------------------------- #
def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _tree_digest(entries: list[dict]) -> str:
    recipe = "".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries)
    return _sha(recipe.encode("utf-8"))


def manifest_for(kind: str, version: str, *, root: Path,
                 platforms: tuple[str, ...] | None = None,
                 depends_on: tuple[str, ...] = (),
                 allow_external_in_repo: tuple[str, ...] = ()) -> tuple[dict, dict[str, bytes]]:
    """Compose the two artifacts' description and the bytes it describes."""
    if kind not in L2_KINDS:
        raise Refused(f"{kind!r} is not an L2 artifact kind ({list(L2_KINDS)})")
    if not VERSION_RE.match(version):
        raise Refused(f"version {version!r} is not <major>.<minor>.<patch>[+<cachebuster>]")

    found = closure(ENTRY[kind], root)
    if not found.files:
        raise Refused(
            f"{kind}'s entrypoint {ENTRY[kind]!r} does not resolve under {root}; a "
            f"closure of zero modules would build an artifact of nothing and call "
            f"it a release")
    members: dict[str, bytes] = {}
    for module, path in found.files.items():
        rel = path.relative_to(root).as_posix()
        data = path.read_bytes()
        members[rel] = data

    absent = [rel for rel in REQUIRED_MEMBERS[kind] if rel not in members]
    if absent:
        raise Refused(
            f"the {kind} closure of {ENTRY[kind]!r} does not contain its required "
            f"member(s) {absent}; the kind is defined by the file it exists for and "
            f"a payload without it is not that kind however it is named")

    _refuse_cross_layer(kind, members)
    _refuse_vendored_schema(members)

    undeclared = sorted(set(found.external_in_repo) - set(allow_external_in_repo))
    if undeclared:
        edges = "; ".join(f"{package} <- {list(found.edges.get(package, ()))}"
                          for package in undeclared)
        raise Refused(
            f"the {kind} artifact's closure leaves fleet_mcp and needs in-repo "
            f"package(s) {undeclared} that the artifact does not carry ({edges}). "
            f"Shipping it would install a payload that raises at import on a "
            f"machine without this checkout. Either cut the edge, or build with "
            f"allow_external_in_repo=... and accept a non-standalone artifact.")

    declared_platforms = tuple(platforms if platforms is not None else PLATFORMS[kind])
    unsupported = sorted(set(declared_platforms) - set(PLATFORMS[kind]))
    if unsupported:
        raise Refused(f"{kind} does not run on {unsupported}; it ships to "
                      f"{list(PLATFORMS[kind])}")
    # The verifier requires every platform the kind supports, so a build that
    # narrowed the list would produce an artifact its own verifier refuses. A
    # build that can emit what the release path rejects is a build nobody can
    # use; the rule lives in both places on purpose and is checked here first.
    if not set(PLATFORMS[kind]) <= set(declared_platforms):
        raise Refused(
            f"{kind} must declare every platform it ships to, "
            f"{list(PLATFORMS[kind])}; {list(declared_platforms)} would build a "
            f"release that claims less than the kind does")

    # `members` names the manifest itself, exactly as `scripts/build.py` does
    # (`members = ... | {ARTIFACT_MANIFEST_NAME}`): a list of what was written
    # that omitted one of the things written would be a false statement, and
    # `scripts/release.py:739` compares this list against the files on disk with
    # only `RELEASE.json` exempted — so an unlisted `ARTIFACT.json` would be
    # reported as "present that the build did not produce". It carries no digest
    # of its own, because a file cannot contain its own.
    digests = sorted(members)
    member_sha256 = {rel: _sha(members[rel]) for rel in digests}
    entries = [{"path": rel, "sha256": member_sha256[rel]} for rel in digests]
    members_list = sorted(set(digests) | {ARTIFACT_MANIFEST})

    manifest = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "composition": COMPOSITION,
        "name": NAMES[kind],
        "version": version,
        "kinds": [kind],
        # A node and a hub are not plugins and carry no skills: saying so in the
        # fields a reader already knows is cheaper than teaching every reader
        # that these kinds mean something else.
        "skills": [],
        "recommended": [],
        "mcp_servers": [],
        # `members` is a list of PATHS, not of records. That is the shape
        # `scripts/build.py` writes and `scripts/release.py` reads
        # (`set(manifest["members"])` against the files on disk), so a manifest
        # that put dicts here would raise TypeError inside the release tool's
        # undeclared-file check instead of being refused with a reason. The
        # digests live in their own additive field, which the existing reader
        # ignores and this verifier requires.
        "members": members_list,
        "member_sha256": member_sha256,
        "source": {
            "entry": ENTRY[kind],
            "module_count": len(member_sha256),
            "tree_sha256": _tree_digest(entries),
        },
        "l2": {
            "kind": kind,
            "entrypoint": ENTRY[kind],
            "platforms": list(declared_platforms),
            "depends_on": list(depends_on),
            "requires": {
                "python": ">=3.10",
                "modules": list(found.third_party),
            },
            # Packages this payload imports and does NOT carry. Named rather than
            # folded into `requires`, because a third-party wheel comes from pip
            # and this comes from a checkout of this repository — the second is a
            # packaging defect that a dependency line would make look solved.
            "external_in_repo": list(found.external_in_repo),
            "standalone": not found.external_in_repo and not depends_on,
            "shared_surface": (list(NODE_SHARED_SURFACE) if kind == L2_NODE else []),
            "provides": dict(PROVIDES[kind]),
        },
    }
    return manifest, members


def _refuse_node_members(names) -> None:
    """The node half of D7: no hub ingress, no unpinned coupling, no stale pin.

    Takes MEMBER NAMES rather than a `Closure` because the build and the verifier
    must apply the same rule to the same thing. They used to hold two copies of
    it — one over the closure's modules, one over the archive's names — and two
    copies of a guard is a build that passes and a release that refuses, or the
    reverse.
    """
    _refuse_bytecode(names, L2_NODE)
    denied = sorted({name for name in names
                     if _module_of(name) in NODE_DENIED_MODULES
                     or name.startswith(NODE_DENIED_PREFIXES)})
    if denied:
        raise Refused(
            f"the node artifact reaches hub-side module(s) {denied}; the connector "
            f"must not import the hub's ingress. Cut the edge or move the import "
            f"behind a handoff.")
    modules = {_module_of(name) for name in names} - {""}
    private = {module for module in modules
               if module == NODE_PUBLIC_PREFIX
               or module.startswith(NODE_PUBLIC_PREFIX + ".")}
    unpinned = sorted(modules - private - set(NODE_EXCLUSIVE)
                      - set(NODE_SHARED_SURFACE))
    if unpinned:
        raise Refused(
            f"new hub coupling in the node artifact: {unpinned}. The shared "
            f"surface is pinned because it is what makes the two artifacts one "
            f"release; a reviewed change adds the module to NODE_SHARED_SURFACE "
            f"with the reason, or puts it under {NODE_PUBLIC_PREFIX}/ if it is the "
            f"node's own.")
    unreached = sorted(set(NODE_SHARED_SURFACE) - modules)
    if unreached:
        raise Refused(
            f"the pin lists module(s) the closure no longer reaches: {unreached}. "
            f"An edge was cut and the pin was not trimmed with it; delete the "
            f"entry in the same change, so the pin stays a measurement instead of "
            f"a claim.")


def _refuse_bytecode(names, kind: str) -> None:
    """Sources ship, bytecode does not.

    A sourceless `.pyc` is importable (`importlib.machinery.BYTECODE_SUFFIXES` is
    `['.pyc']`), so on the hub side `fleet_mcp/server.pyc` would carry the
    connector's counterpart past a deny-list written against `.py`, and on the
    node side it would carry code the manifest's dependency check never parsed.
    """
    found = sorted(name for name in names if name.endswith(FORBIDDEN_SUFFIXES))
    if found:
        raise Refused(
            f"the {kind} artifact ships bytecode {found}; bytecode is build output "
            f"and it is importable without its source, so a payload that carries "
            f"it can be something this build never checked.")


def _refuse_hub_members(names) -> None:
    """The hub half: the hub is software, the device's state is not."""
    _refuse_bytecode(names, L2_HUB)
    leaked = sorted(name for name in names if name.startswith(HUB_DENIED_PREFIXES))
    if leaked:
        raise Refused(
            f"the hub artifact carries device-side file(s) {leaked}; a hub release "
            f"must not be able to install a node.")
    forbidden = sorted(name for name in names
                       if any(_matches(name, pattern) for pattern in HUB_DENIED_PATTERNS))
    if forbidden:
        raise Refused(
            f"the hub artifact carries machine-specific file(s) {forbidden}; "
            f"bindings, units and launchers belong to the device that has them, "
            f"not to the software.")
    # `*token*` / `*credential*` are about DATA, not module names: a source file
    # called `token_cache.py` is software, while a `tokens.json` beside it is a
    # credential. Restricting these two patterns to non-source files keeps the
    # guard from turning into an argument about renaming a module.
    secrets = sorted(name for name in names
                     if not name.endswith((".py", ".pyi"))
                     and any(_matches(name, pattern) for pattern in HUB_SECRET_PATTERNS))
    if secrets:
        raise Refused(
            f"the hub artifact carries credential-shaped file(s) {secrets}; a "
            f"release is not the place a machine's secrets travel in.")


def _refuse_cross_layer(kind: str, members) -> None:
    """The one check that is the whole point of D7, per kind."""
    if kind == L2_NODE:
        _refuse_node_members(members)
    else:
        _refuse_hub_members(members)


#: Modules the node artifact may contain that are not dragged in from the hub:
#: the connector itself and the four leaves it genuinely needs today.
NODE_EXCLUSIVE = frozenset({
    "fleet_mcp", "fleet_mcp.node", "fleet_mcp.node.connector",
    "fleet_mcp.node_protocol", "fleet_mcp.errors", "fleet_mcp.identity",
    "fleet_mcp.local_roots", "fleet_mcp.config", "fleet_mcp.runtime_identity",
    "fleet_mcp.providers", "fleet_mcp.providers.base",
    "fleet_mcp.providers.cloudcli", "fleet_mcp.version",
})


def _matches(path: str, pattern: str) -> bool:
    if pattern.startswith("*") and "/" not in pattern:
        return fnmatchcase(path.rsplit("/", 1)[-1], pattern)
    return fnmatchcase(path, pattern) or fnmatchcase(path.rsplit("/", 1)[-1], pattern)


def _refuse_vendored_schema(members: dict[str, bytes]) -> None:
    for path, data in members.items():
        if not path.endswith(".json") or len(data) > 262_144:
            continue
        try:
            value = json.loads(data)
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
        text = json.dumps(value)
        if SCHEMA_MARKER in text and SCHEMA_TOOL_HINT in text:
            raise Refused(
                f"{path} looks like a vendored CloudCLI tool schema "
                f"({SCHEMA_MARKER} + {SCHEMA_TOOL_HINT}); the provider forwards "
                f"the running CLI's schema, it does not ship a copy that can "
                f"silently disagree with it.")


# --------------------------------------------------------------------------- #
# deterministic archive
# --------------------------------------------------------------------------- #
def _refuse_unsafe_names(names) -> None:
    """One canonical spelling per member, or no release.

    `fleet_mcp/./node/connector.py` is the same file as
    `fleet_mcp/node/connector.py` to every filesystem and a different string to
    every guard that compares strings — which is how a deny-list written against
    `fleet_mcp/node/` gets walked past. So the rule is not "reject the traversal
    parts"; it is "accept exactly the canonical spelling and refuse the rest".
    """
    problems: list[str] = []
    for name in names:
        if not name or any(char in name for char in ("\0", "\n", "\r")):
            problems.append(repr(name))
        elif "\\" in name:
            # A Windows separator inside a POSIX archive is at best a name no
            # extractor agrees on, and at worst a second spelling of one file.
            problems.append(name)
        else:
            path = PurePosixPath(name)
            if path.is_absolute() or ".." in path.parts or path.as_posix() != name:
                problems.append(name)
    if problems:
        raise Refused(
            f"unsafe or non-canonical member name(s) {sorted(problems)}; a member "
            f"name is a path inside the payload and it has exactly one spelling, so "
            f"the guard and the filesystem are never looking at two names")


def _refuse_duplicate_names(names, where: str) -> None:
    duplicated = sorted({name for name in names if names.count(name) > 1}) \
        if len(names) != len(set(names)) else []
    if duplicated:
        raise Refused(
            f"{where} carries {duplicated} more than once; two members with one "
            f"name means the payload's contents depend on which one wins, and a "
            f"release whose contents depend on an extraction order is not the "
            f"release that was verified")


def write_artifact(manifest: dict, members: dict[str, bytes], output: Path) -> Path:
    """Write one `.tar.gz` whose bytes depend only on the manifest and members.

    No timestamp, no uid/gid, no hostile-to-diff metadata: two builds of the same
    tree produce the same file, so "did this artifact change" is a byte
    comparison and not a conversation. The manifest is a member but carries no
    digest — a file cannot contain its own, and `scripts/build.py` lists it in
    `members` for the same reason `scripts/release.py` exempts only `RELEASE.json`
    from its undeclared-file check.
    """
    payload = dict(members)
    payload[ARTIFACT_MANIFEST] = json.dumps(
        manifest, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    _refuse_unsafe_names(payload)
    output.parent.mkdir(parents=True, exist_ok=True)
    try:
        with output.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw,
                               mtime=0, compresslevel=9) as gz:
                with tarfile.open(fileobj=gz, mode="w",
                                  format=tarfile.USTAR_FORMAT) as tar:
                    for name in sorted(payload):
                        data = payload[name]
                        info = tarfile.TarInfo(name)
                        info.size, info.mtime, info.mode = len(data), 0, 0o644
                        info.uid = info.gid = 0
                        info.uname = info.gname = ""
                        tar.addfile(info, io.BytesIO(data))
    except ValueError as exc:
        # USTAR is the deterministic format and it has a 100-byte name field;
        # a deeply nested member overruns it. That is a refusal with a reason,
        # not a traceback out of the packaging tool.
        output.unlink(missing_ok=True)
        raise Refused(f"cannot write {output} as a USTAR archive: {exc}") from exc
    return output


@dataclass(frozen=True)
class Artifact:
    manifest: dict
    members: dict[str, bytes]
    digest: str

    @property
    def kind(self) -> str:
        kinds = self.manifest.get("kinds") or []
        return kinds[0] if len(kinds) == 1 else ""

    @property
    def version(self) -> str:
        return str(self.manifest.get("version", ""))


def read_artifact(path: Path) -> Artifact:
    """Read an artifact and verify every member against the manifest.

    Nothing is extracted. Reading the manifest first and the members second is
    the order that matters: a reader that extracted and then verified would have
    already written a file the manifest was about to reject.
    """
    try:
        with tarfile.open(path, "r:gz") as tar:
            infos = tar.getmembers()
            names = [info.name for info in infos]
            _refuse_unsafe_names(names)
            _refuse_duplicate_names(names, str(path))
            # A directory, a symlink or a device node is not a member: the
            # manifest describes file bytes and nothing else, so a payload that
            # carries a link would be verified as the thing it points at and
            # installed as the link.
            other = sorted(info.name for info in infos if not info.isfile())
            if other:
                raise Refused(
                    f"{path} carries member(s) that are not regular files "
                    f"{other}; an artifact is a set of file bytes, and a link or a "
                    f"directory is something the manifest cannot describe")
            if ARTIFACT_MANIFEST not in names:
                raise Refused(f"{path} carries no {ARTIFACT_MANIFEST}")
            raw = tar.extractfile(ARTIFACT_MANIFEST).read()
            manifest = json.loads(raw)
            members = {name: tar.extractfile(name).read()
                       for name in names if name != ARTIFACT_MANIFEST}
    except (tarfile.TarError, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise Refused(f"{path} is not a readable artifact: {exc}") from exc

    recorded = manifest.get("member_sha256")
    if not isinstance(recorded, dict):
        recorded = {}      # a shape `verify` refuses; reading must not crash on it
    for name, data in members.items():
        if name not in recorded:
            continue          # an undeclared file is a `verify` problem, not a read one
        if _sha(data) != recorded[name]:
            raise Refused(
                f"{name} does not match the manifest: recorded "
                f"{recorded[name][:12]}…, the bytes hash {_sha(data)[:12]}…. A "
                f"payload whose bytes are not its record is not a weaker release, "
                f"it is a different one.")
    return Artifact(manifest, members, _sha(path.read_bytes()))


# --------------------------------------------------------------------------- #
# verification
# --------------------------------------------------------------------------- #
def verify(artifact: Artifact, *, expect_kind: str | None = None,
           expect_version: str | None = None) -> dict:
    """Every way this artifact could be the wrong thing, refused by name."""
    manifest = artifact.manifest
    if manifest.get("schema") != ARTIFACT_MANIFEST_SCHEMA:
        raise Refused(f"manifest schema {manifest.get('schema')!r} is not "
                      f"{ARTIFACT_MANIFEST_SCHEMA!r}")

    kinds = manifest.get("kinds")
    if not isinstance(kinds, list) or len(kinds) != 1:
        raise Refused(f"an L2 artifact declares exactly one kind, not {kinds!r}")
    kind = kinds[0]
    if kind not in L2_KINDS:
        raise Refused(f"{kind!r} is not an L2 artifact kind ({list(L2_KINDS)})")
    if expect_kind is not None and kind != expect_kind:
        raise Refused(f"this payload is a {kind}, and a {expect_kind} was expected")

    if manifest.get("name") != NAMES[kind] or manifest.get("composition") != COMPOSITION:
        raise Refused(f"manifest names {manifest.get('name')!r}/"
                      f"{manifest.get('composition')!r}, expected "
                      f"{NAMES[kind]!r}/{COMPOSITION!r}")

    version = str(manifest.get("version", ""))
    if not VERSION_RE.match(version):
        raise Refused(f"version {version!r} is not <major>.<minor>.<patch>[+<cachebuster>]")
    if expect_version is not None and version != expect_version:
        raise Refused(f"version drift: manifest says {version!r}, the build "
                      f"asked for {expect_version!r}")

    listed = manifest.get("members")
    if not isinstance(listed, list) or not all(isinstance(item, str) for item in listed):
        raise Refused(
            f"members is {type(listed).__name__}, not a list of paths; the release "
            f"tool compares this list against the files on disk with `set()`, so a "
            f"manifest of records would fail inside that comparison as a TypeError "
            f"rather than as a refusal")
    recorded = manifest.get("member_sha256")
    if not isinstance(recorded, dict):
        raise Refused(
            "the manifest records no member digests; a payload whose bytes are not "
            "recorded is not a weaker artifact, it is an unsigned one")
    if len(listed) != len(set(listed)):
        duplicated = sorted({item for item in listed if listed.count(item) > 1})
        raise Refused(f"the manifest lists {duplicated} more than once")
    if ARTIFACT_MANIFEST not in listed:
        raise Refused(
            f"the member list omits {ARTIFACT_MANIFEST} itself; `scripts/release.py` "
            f"compares this list against the files on disk and exempts only "
            f"RELEASE.json, so a release staged from this payload would report its "
            f"own manifest as a file the build did not produce")
    # The manifest describes every member including itself; it is the one member
    # that cannot carry a digest of its own.
    digests = [name for name in listed if name != ARTIFACT_MANIFEST]
    payload = set(artifact.members) | {ARTIFACT_MANIFEST}
    missing = sorted(set(listed) - payload)
    extra = sorted(payload - set(listed))
    if missing or extra:
        raise Refused(f"member list does not match the payload: missing {missing}, "
                      f"undeclared {extra}")
    if sorted(recorded) != sorted(digests):
        raise Refused(
            f"member_sha256 covers {sorted(set(recorded) - set(digests)) or 'nothing'} "
            f"extra and omits {sorted(set(digests) - set(recorded)) or 'nothing'}; "
            f"every member except {ARTIFACT_MANIFEST} has exactly one digest")

    for name in digests:
        if _sha(artifact.members[name]) != recorded[name]:
            raise Refused(f"{name} does not match the manifest entry")

    if _tree_digest([{"path": name, "sha256": recorded[name]}
                     for name in digests]) != manifest.get("source", {}).get("tree_sha256"):
        raise Refused("source.tree_sha256 does not describe this member list")
    if manifest.get("source", {}).get("module_count") != len(digests):
        raise Refused(
            f"source.module_count says {manifest.get('source', {}).get('module_count')!r} "
            f"and the member list carries {len(digests)}; a count that disagrees "
            f"with the list it describes is a manifest written by hand")

    for required in REQUIRED_MEMBERS[kind]:
        if required not in artifact.members:
            raise Refused(f"{kind} artifact is missing its required member {required}")

    block = _verify_l2_block(kind, manifest)
    _verify_layer(kind, artifact)
    _verify_dependencies(manifest, artifact)
    _refuse_vendored_schema(artifact.members)

    return {"ok": True, "kind": kind, "version": version,
            "members": len(listed), "digest": artifact.digest,
            "standalone": bool(block.get("standalone"))}


def _verify_l2_block(kind: str, manifest: dict) -> dict:
    """The `l2` block, checked against what the code says the kind is.

    A block that restates the top level is not redundant if it is compared: the
    top level is what other readers use, this is what this kind means, and the
    failure mode being closed here is a manifest that says two things at once —
    a hub listing the node's shared surface, a node claiming to run where it does
    not, a payload whose `standalone` contradicts its own dependency lists.
    """
    block = manifest.get("l2")
    if not isinstance(block, dict):
        raise Refused("the manifest carries no l2 block; this is an L2 artifact and "
                      "a payload that does not describe itself as one cannot be "
                      "released as one")

    if block.get("kind") != kind:
        raise Refused(f"the l2 block says {block.get('kind')!r} and the kinds "
                      f"list says {kind!r}")
    if block.get("entrypoint") != ENTRY[kind]:
        raise Refused(f"the l2 block names entrypoint {block.get('entrypoint')!r}; "
                      f"{kind} is {ENTRY[kind]!r}")

    platforms = list(block.get("platforms") or [])
    unsupported = sorted(set(platforms) - set(PLATFORMS[kind]))
    if unsupported:
        raise Refused(f"{kind} declares unsupported platform(s) {unsupported}")
    absent = sorted(set(PLATFORMS[kind]) - set(platforms))
    if absent:
        raise Refused(f"{kind} must declare {list(PLATFORMS[kind])}, "
                      f"declared {platforms} (missing {absent}); a payload that "
                      f"claims fewer platforms than its kind ships to would install "
                      f"on a machine its release does not describe")

    external = list(block.get("external_in_repo") or [])
    depends_on = list(block.get("depends_on") or [])
    standalone = not external and not depends_on
    if bool(block.get("standalone")) != standalone:
        raise Refused(
            f"the l2 block says standalone={block.get('standalone')!r} while "
            f"external_in_repo={external} and depends_on={depends_on} say "
            f"otherwise; the one thing a verifier exists for is a payload that "
            f"describes itself two ways")

    provides = block.get("provides")
    if provides != PROVIDES[kind]:
        raise Refused(f"{kind} must provide exactly {PROVIDES[kind]}, "
                      f"declares {provides!r}")

    shared = sorted(block.get("shared_surface") or [])
    if kind == L2_NODE:
        if shared != sorted(NODE_SHARED_SURFACE):
            raise Refused(
                f"the node artifact lists shared surface {shared}; this build's pin "
                f"is {sorted(NODE_SHARED_SURFACE)}. An artifact is released against "
                f"the pin it was built with, and a manifest that records a "
                f"different one is describing a different release")
    elif shared:
        raise Refused(
            f"the hub artifact lists a shared surface {shared}; the pin is the "
            f"node's measurement of what it borrows from the hub, not the hub's")

    requires = block.get("requires")
    if not isinstance(requires, dict) or not isinstance(requires.get("modules"), list):
        raise Refused("the l2 block declares no requires.modules; the dependency "
                      "check has nothing to compare the payload's imports against")
    return block


def _verify_layer(kind: str, artifact: Artifact) -> None:
    """The same two functions the build calls, so the two cannot disagree."""
    _refuse_cross_layer(kind, set(artifact.members))


def _module_of(name: str) -> str:
    """The dotted module a member would import as, or "" for a data file.

    Sources and bytecode both count: `importlib.machinery.BYTECODE_SUFFIXES` is
    `['.pyc']`, so a sourceless `fleet_mcp/server.pyc` imports as
    `fleet_mcp.server`. A deny-list that reads only `.py` has a way around it.
    """
    for suffix in (".py", ".pyc", ".pyo", ".pyi"):
        if name.endswith(suffix):
            break
    else:
        return ""
    parts = name[: -len(suffix)].split("/")
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _verify_dependencies(manifest: dict, artifact: Artifact) -> None:
    block = manifest.get("l2", {})
    declared = set(block.get("requires", {}).get("modules") or [])
    measured: set[str] = set()
    internal: set[str] = set()
    stdlib = set(getattr(sys, "stdlib_module_names", frozenset()))
    for name, data in artifact.members.items():
        if not name.endswith(".py"):
            continue
        try:
            tree = ast.parse(data.decode("utf-8", "replace"))
        except SyntaxError as exc:
            raise Refused(f"{name} does not parse: {exc}") from exc
        package = name.split("/")[:-1]
        for node in ast.walk(tree):
            modules: list[str] = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = list(package[: len(package) - (node.level - 1)])
                    modules = [".".join(base + ([node.module] if node.module else []))]
                else:
                    modules = [node.module or ""]
            for module in modules:
                top = module.split(".")[0]
                if not top:
                    continue
                if module.startswith("fleet_mcp"):
                    # An intra-package import is not a dependency, it is a claim
                    # about the payload: `from .report import x` promises that
                    # `fleet_mcp/report.py` is in this artifact. Nothing else
                    # checks that — the member list only says what IS here.
                    internal.add(module.rstrip("."))
                    continue
                if top in stdlib or top == "__future__":
                    continue
                measured.add(top)
    present = {_module_of(name) for name in artifact.members} - {""}
    absent = sorted(module for module in internal if module not in present)
    if absent:
        raise Refused(
            f"the payload imports {absent} from its own package but does not carry "
            f"it; an artifact that names a module it does not contain imports into "
            f"an ImportError on the machine, not in the build")

    external = set(block.get("external_in_repo") or [])
    missing = sorted(measured - declared - external)
    if missing:
        raise Refused(f"artifact imports {missing} but its manifest declares only "
                      f"{sorted(declared)}; an artifact that does not name its "
                      f"dependencies installs into an ImportError")

    for dependency in block.get("depends_on") or []:
        wanted = str(dependency).replace(".", "/")
        if (f"{wanted}.py" in artifact.members
                or f"{wanted}/__init__.py" in artifact.members):
            continue
        raise Refused(f"depends_on names {dependency!r}, which this payload "
                      f"does not carry")


# --------------------------------------------------------------------------- #
# two versions, one rollback
# --------------------------------------------------------------------------- #
def pair(previous: Path, current: Path, *, kind: str | None = None) -> dict:
    """Read both revisions of one kind and say what a rollback would restore.

    This is the whole of "independent versions and rollback" that can be tested
    offline: both artifacts verify on their own, they are the same kind, and the
    older one is still readable after the newer one exists. It restarts nothing
    and installs nothing.
    """
    old, new = read_artifact(previous), read_artifact(current)
    # Both are verified whether or not a kind was named: "the previous artifact is
    # still readable and still verifies" is the entire property a rollback claim
    # rests on, and reporting a rollback to an artifact this build refuses would
    # be a rollback to something that was never a release.
    verify(old, expect_kind=kind)
    verify(new, expect_kind=kind)
    if old.kind != new.kind:
        raise Refused(f"cannot roll a {new.kind} back to a {old.kind}")
    return {
        "kind": new.kind,
        "from": old.version,
        "to": new.version,
        "rollback_to": old.version,
        "rollback_available": True,
    }


def extract(artifact: Path, destination: Path) -> Path:
    """Lay an artifact out as a directory, which is the shape `release.py` stages.

    `scripts/release.py` copies a *directory* (`shutil.copytree`), not an archive,
    so an artifact has to become one before it can be released. Extraction happens
    only after `read_artifact` has refused unsafe names, duplicates, links and
    tampered bytes: a reader that extracted first would have already written the
    file it was about to reject.
    """
    read = read_artifact(artifact)
    if destination.exists() and any(destination.iterdir()):
        raise Refused(f"{destination} already has contents; an extracted artifact "
                      f"is written into an empty directory so nothing of the last "
                      f"one is mistaken for part of this one")
    destination.mkdir(parents=True, exist_ok=True)
    for name, data in read.members.items():
        path = destination / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (destination / ARTIFACT_MANIFEST).write_text(
        json.dumps(read.manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return destination


def rollback_target(previous: Path, *, kind: str) -> Artifact:
    """The artifact a rollback would activate — verified before it is named."""
    artifact = read_artifact(previous)
    verify(artifact, expect_kind=kind)
    return artifact


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="l2_release", description=__doc__.split("\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    build = sub.add_parser("build", help="build one kind into a deterministic .tar.gz")
    build.add_argument("--kind", required=True, choices=L2_KINDS)
    build.add_argument("--version", required=True)
    build.add_argument("--output", required=True, type=Path)
    build.add_argument("--root", default=str(Path(__file__).resolve().parents[2]))
    build.add_argument("--allow-external-in-repo", action="append", default=[],
                       metavar="PACKAGE",
                       help="accept that this payload imports but does not carry an "
                            "in-repo package; the artifact is then marked "
                            "standalone: false. Named one at a time because it is a "
                            "defect to accept, not a default to inherit.")
    build.add_argument("--depends-on", action="append", default=[], metavar="MODULE")

    check = sub.add_parser("verify", help="read an artifact back and verify it")
    check.add_argument("artifact", type=Path)
    check.add_argument("--kind", choices=L2_KINDS)
    check.add_argument("--version")

    both = sub.add_parser("pair", help="verify two revisions of one kind")
    both.add_argument("previous", type=Path)
    both.add_argument("current", type=Path)
    both.add_argument("--kind", required=True, choices=L2_KINDS)

    unpack = sub.add_parser("extract", help="write an artifact out as a directory")
    unpack.add_argument("artifact", type=Path)
    unpack.add_argument("destination", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            manifest, members = manifest_for(
                args.kind, args.version, root=Path(args.root),
                depends_on=tuple(args.depends_on),
                allow_external_in_repo=tuple(args.allow_external_in_repo))
            path = write_artifact(manifest, members, args.output)
            print(json.dumps({"ok": True, "kind": args.kind, "version": args.version,
                              "members": len(members), "output": str(path),
                              "standalone": manifest["l2"]["standalone"],
                              "external_in_repo": manifest["l2"]["external_in_repo"],
                              "shared_surface": len(manifest["l2"]["shared_surface"])},
                             indent=2))
        elif args.command == "verify":
            print(json.dumps(verify(read_artifact(args.artifact),
                                    expect_kind=args.kind,
                                    expect_version=args.version), indent=2))
        elif args.command == "extract":
            path = extract(args.artifact, args.destination)
            print(json.dumps({"ok": True, "directory": str(path)}, indent=2))
        else:
            print(json.dumps(pair(args.previous, args.current, kind=args.kind), indent=2))
    except Refused as exc:
        print(f"REFUSED: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
