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
  provides, a hub listing a shared surface;
* `import_standalone`, `runtime_standalone`, the `probe` record or the `install`
  block disagreeing with what the payload actually does when it is imported in a
  fresh interpreter (`_verify_standalone`);
* an external edge that is not the pinned one: `fleet` reached from a second
  module, through a module other than `fleet.transport`, by `import`/`from fleet
  import ...` rather than the pinned seven-name `ImportFrom`, or with a different
  number of names (`EXTERNAL_EDGES`);
* a pin entry the payload carries but never reaches, so `NODE_SHARED_SURFACE`
  cannot rot into a list nobody checks — in either direction;
* a member name with a second spelling (`fleet_mcp/./node/connector.py`), a
  duplicate name, a link, or bytecode.

It does not install anything, start anything, connect to anything, or read a
credential. `pair()` and `rollback_target()` describe a rollback; they perform
none. The one process it starts is a child interpreter running the fixed `_PROBE`
source against a temporary copy of the payload — the only way to answer "does this
import with nothing else on the path" without the answer depending on what this
process has already imported.

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

The dependency on the repository is pinned the same way and with the same
discipline: `EXTERNAL_EDGES` records **one** holder (`fleet_mcp.identity_adapter_port`), **one**
module (`fleet.transport`) and **exactly seven** names, because "the node needs
`fleet`" is a sentence that stays true forever and distinguishes nothing. Cutting
edge A in HANDOFF-407 deletes that edge outright; while it exists, this module
measures what it costs.

Two flags, not one
------------------
`standalone: false` was too blunt to act on. There are two different questions and
the node answers them differently:

    import_standalone   can `import fleet_mcp.node.connector` succeed on a machine
                        with this payload and nothing else?          node: TRUE
    runtime_standalone  can the payload *run* — including the modules its runtime
                        path reaches — on that machine?              node: FALSE

The node imports because the connector defers `from ..fleet import FleetService`
into function bodies; it cannot run because `fleet_mcp/registry.py:33` raises
`FleetRefusal(INTERNAL)` without a checkout and `fleet_mcp/fleet.py` imports it at
module level. The hub does neither: its import chain is eager, so it fails to
import in the same sandbox. Both numbers are produced by importing the payload in
a separate interpreter (`measure_standalone`), and the verifier re-runs the probe
rather than reading the manifest's word for it.

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
import os
import re
import subprocess
import sys
import tarfile
import tempfile
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
#:
#: The reduction after the node cut (measured, not guessed — `closure()` on the
#: cut source): five entries stopped being reached once `connector` stopped
#: importing the service (`audit`, `dispatch`, `fleet`, `privacy`, `registry`),
#: and one entry had to be ADDED because `config` reads it on the cut line
#: (`fleet_mcp.principals`). 17 - 5 + 1 = 13. A reduction is not always a
#: subtraction: a line that moves can hand the payload a module the pin never
#: named, and pinning it is the reviewed act that keeps it honest.
NODE_SHARED_SURFACE = (
    "fleet_mcp.cloudcli_native",
    "fleet_mcp.core_lite_readback",
    "fleet_mcp.core_lite_source",
    "fleet_mcp.disclosure",
    "fleet_mcp.discovery",
    "fleet_mcp.feishu_sso",
    "fleet_mcp.mcp_http",
    "fleet_mcp.owner_access",
    "fleet_mcp.principals",
    "fleet_mcp.providers.fleet_node",
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

#: The one edge out of `fleet_mcp` into the repository, measured on `main` and
#: frozen: **one** holder, **one** module, **exactly seven** names.
#:
#:     fleet_mcp/registry.py:33   from fleet.transport import (
#:                                    Availability, CapabilityAvailability, Device,
#:                                    DeviceRef, DeviceRegistry, UnknownDevice,
#:                                    canonical_level)
#:
#: This is not a limitation being celebrated. It is the narrowest possible form of
#: a real dependency, and pinning it is what makes the dependency *countable*: the
#: moment a second module reaches into `fleet`, or one of these seven becomes eight,
#: or someone writes `import fleet.registry`, the artifact stops building and names
#: the new edge. Without the pin, "the node needs `fleet`" is true forever and
#: nothing distinguishes today's seven names from next month's twenty.
#:
#: The dictionary is keyed by the external package and is *complete*: a package
#: reached but not listed here is new coupling and is refused, not inherited.
#: The holder is part of the pin, not a detail of it. The identity authority's
#: import moved from `fleet_mcp.registry` to `fleet_mcp.identity_adapter_port`
#: when the Hub gained a versioned seam (#466); the edge is the same edge, but a
#: pin that keeps naming the old holder refuses a Hub whose dependency is in fact
#: narrower and better described. Widen this only by naming the module that
#: actually holds the import, and say why in the same change.
EXTERNAL_EDGES = {
    "fleet": {
        "module": "fleet.transport",
        "holder": "fleet_mcp.identity_adapter_port",
        "symbols": ("Availability", "CapabilityAvailability", "Device", "DeviceRef",
                    "DeviceRegistry", "UnknownDevice", "canonical_level"),
    },
}

#: Import forms that reach an external package *more* broadly than one pinned
#: `from <module> import (...)` line: `import fleet`, `import fleet.registry`,
#: `from fleet import transport`. Each of them pulls something the seven-name pin
#: does not describe, so they are refused by shape rather than by symbol count.
EDGE_FORM = "from-module-import-names"


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


def _imports_of(data: bytes, member: str) -> list[str]:
    """Every module name this source imports, relative imports made absolute.

    `member` is the payload-relative path (`fleet_mcp/node/connector.py`), which is
    what a relative import is resolved against. Reading from BYTES rather than
    from a file on disk is what lets the same walk run over an artifact that was
    never extracted — the build and the verifier both call it, so what a payload
    is measured to import cannot depend on which side asked.
    """
    tree = ast.parse(data.decode("utf-8", "replace"))
    package = member.split("/")[:-1]
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


def _imports(path: Path, root: Path) -> list[str]:
    """`_imports_of` for a file in a source tree.

    `from ..errors import X` inside `fleet_mcp/node/connector.py` yields
    `fleet_mcp.errors` — resolved here, because a closure that cannot resolve a
    relative import reports a dependency on nothing and hides the very edge that
    makes the two artifacts one.
    """
    return _imports_of(path.read_bytes(), path.relative_to(root).as_posix())


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
    # The edge, before the allowance: an allowed package is still a package whose
    # *shape* has to be the pinned one. Accepting that a payload needs `fleet` is a
    # decision; needing it through `import fleet.registry` is a different decision
    # and the allowance is not the place it is made.
    holders = _verify_coupling(kind, members, third_party=found.third_party)

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
    # An allowance is a named permission, not a standing one. Naming a package the
    # closure does not reach would leave a permit in force for a dependency nobody
    # has today, which is how a build grows a dependency without the manifest
    # changing.
    unused = sorted(set(allow_external_in_repo) - set(found.external_in_repo))
    if unused:
        raise Refused(
            f"allow_external_in_repo names {unused}, which the {kind} closure does "
            f"not reach; an allowance for a package nothing imports is a permit "
            f"left in force, and the next dependency arrives under it")

    standalone = measure_standalone(members, entry=ENTRY[kind])
    blockers = [f"{holder} -> {EXTERNAL_EDGES[package]['module']} "
                f"({len(EXTERNAL_EDGES[package]['symbols'])} symbols)"
                for package in sorted(holders) for holder in holders[package]]
    blockers += [f"depends_on {module} (not carried by this payload)"
                 for module in depends_on]
    # `standalone` is kept as the LEGACY spelling of the strong claim, because
    # #433 and #436 have readers that look for it; the two fields below are what it
    # means. Making `standalone` follow `import_standalone` instead would let a
    # payload that cannot run describe itself as standalone on the strength of an
    # import that happens to be deferred.
    installable = standalone.runtime_imports and not blockers

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
            # --- measured, not declared ---------------------------------------
            # Every one of these was produced by importing the payload in a
            # separate interpreter with nothing but the payload on `sys.path`, and
            # the verifier re-runs the same probe. They are not author assertions.
            "import_standalone": standalone.entry_imports,
            "runtime_standalone": standalone.runtime_imports,
            "standalone": standalone.runtime_imports,       # legacy, see above
            "probe": dict(standalone.probe),
            # Which module holds each external edge — the dependency named, not
            # merely permitted. This is what `allow_external_in_repo` is NOT: an
            # allowance says "the build may proceed", this says "specifically
            # `fleet_mcp/registry.py` needs `fleet`, and nothing else does".
            "install_requires_repo": {package: list(names)
                                      for package, names in sorted(holders.items())},
            "install": {
                # The one field a deployment reader can act on. It is false
                # whenever anything at all must come from the repository, so no
                # combination of `external_in_repo` + a permissive build flag can
                # make this artifact look installable on its own.
                "independently_installable": installable,
                "requires_repository": {package: list(names)
                                        for package, names in sorted(holders.items())},
                # A second, DIFFERENT route to a deployable Hub. `installable`
                # above answers "does this payload carry everything it imports";
                # this answers "may it be deployed when a verified companion
                # artifact supplies the dependency". They are separate fields on
                # purpose: folding the second into the first would let a Hub with
                # a missing authority read as standalone, which is the exact
                # claim `identity_adapter_port` refuses to make.
                "authority": (authority_declaration() if kind == L2_HUB else None),
                "installable_with": ([AUTHORITY_KIND] if kind == L2_HUB else []),
                "blocked_by": blockers + (["fleet-authority sidecar not supplied"]
                                          if kind == L2_HUB else []),
            },
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

#: Modules under ``fleet_mcp/`` that **neither** artifact's closure reaches, as
#: measured on this tree. A snapshot, and deliberately not an endorsement: most
#: of these are hub-side or legacy files that simply stopped being imported, and
#: two of the entries are the ones an owner runtime outside ``fleet_mcp/**``
#: imports. The tuple exists so that a module leaving both artifacts has to be a
#: decision someone wrote down rather than a file that quietly fell out, and so
#: that a branch adding such a module fails here by name.
ORPHANS_UNDECIDED = (
    "fleet_mcp.deploy", "fleet_mcp.deploy.binding_migration",
    "fleet_mcp.deploy.dell_fjd_cutover", "fleet_mcp.deploy.install",
    "fleet_mcp.deploy.next_dual_stack_preflight",
    "fleet_mcp.deploy.next_install", "fleet_mcp.deploy.next_tailscale_gate",
    "fleet_mcp.deploy.release_plan", "fleet_mcp.health_admission",
    "fleet_mcp.http_transport", "fleet_mcp.human_auth", "fleet_mcp.oauth",
    "fleet_mcp.owner_setup", "fleet_mcp.release_evidence", "fleet_mcp.runtime",
)


def orphaned_modules(root: Path) -> tuple[str, ...]:
    """``fleet_mcp`` modules reachable from neither entrypoint, sorted.

    Both artifacts are import closures, so a module nobody imports is in
    neither payload. That is fine when it is a decision and invisible when it is
    not: this returns the set so a test can hold it still.
    """
    reached = set()
    for kind in (L2_NODE, L2_HUB):
        reached |= set(closure(ENTRY[kind], root).modules)
    on_disk = set()
    for path in sorted((Path(root) / "fleet_mcp").rglob("*.py")):
        dotted = path.relative_to(Path(root)).as_posix()[:-3].replace("/", ".")
        on_disk.add(dotted[: -len(".__init__")] if dotted.endswith(".__init__") else dotted)
    return tuple(sorted(on_disk - reached))


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
# the coupling, measured: the pinned edge, and the two kinds of "standalone"
# --------------------------------------------------------------------------- #
def _external_edges(members: dict[str, bytes]) -> dict[str, list[dict]]:
    """Every place the payload leaves its own package, with the form it leaves by.

    Measured over the payload's BYTES, not over the source tree, because the
    question is "what does this artifact do", and an artifact is a set of bytes.

    The answer is deliberately *wider* than `EXTERNAL_EDGES`: it lists every
    top-level name outside `fleet_mcp` and the standard library, third-party or
    not, and the caller decides which of them are in-repo. Narrowing the walk to
    the pinned names would make the pin a filter instead of a check — a payload
    could reach `runtime/` and the walk would report nothing at all.
    """
    stdlib = set(getattr(sys, "stdlib_module_names", frozenset()))
    found: dict[str, list[dict]] = {}
    for name in sorted(members):
        if not name.endswith(".py"):
            continue
        try:
            tree = ast.parse(members[name].decode("utf-8", "replace"))
        except SyntaxError as exc:
            raise Refused(f"{name} does not parse: {exc}") from exc
        package = name.split("/")[:-1]
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    top = alias.name.split(".")[0]
                    if not top or top == "fleet_mcp" or top in stdlib:
                        continue
                    found.setdefault(top, []).append(
                        {"holder": _module_of(name), "module": alias.name,
                         "symbols": [], "form": "import"})
            elif isinstance(node, ast.ImportFrom):
                if node.level:
                    base = list(package[: len(package) - (node.level - 1)])
                    module = ".".join(base + ([node.module] if node.module else []))
                else:
                    module = node.module or ""
                top = module.split(".")[0]
                if not top or top in ("fleet_mcp", "__future__") or top in stdlib:
                    continue
                # `from fleet.transport import X` names the module it wants.
                # `from fleet import transport` does not: it asks for whatever
                # `fleet/__init__` is willing to hand out, which is a wider
                # dependency than the pin describes and a different one tomorrow.
                form = (EDGE_FORM if "." in module else "from-package-import-names")
                found.setdefault(top, []).append(
                    {"holder": _module_of(name), "module": module,
                     "symbols": sorted(alias.name for alias in node.names),
                     "form": form})
    return found


def _refuse_external_edges(kind: str, members: dict[str, bytes],
                           *, third_party=()) -> dict[str, list[str]]:
    """The pinned edge, enforced. Returns {package: [holder modules]} as measured.

    One holder, one module, the pinned symbols, and nothing else reaching out. A
    package outside `EXTERNAL_EDGES` is acceptable only if the manifest declares it
    as a *third-party* module — a wheel comes from pip, while anything in this
    repository is a dependency on a checkout and has to be pinned. The pin is a
    count: a second holder is how seven names become twenty without anyone
    deciding to.
    """
    measured = _external_edges(members)
    declared = set(third_party)
    problems: list[str] = []
    for package in sorted(measured):
        edges = measured[package]
        rule = EXTERNAL_EDGES.get(package)
        if rule is None:
            if package in declared:
                continue
            problems.append(
                f"{package} <- {sorted({edge['holder'] for edge in edges})}: this "
                f"package is neither a declared third-party module nor a pinned "
                f"external edge, so the payload depends on the repository in a way "
                f"no manifest records")
            continue
        holders = sorted({edge["holder"] for edge in edges})
        if holders != [rule["holder"]]:
            problems.append(
                f"{package} is reached from {holders}; the pin says exactly one "
                f"holder, {rule['holder']!r}")
            continue
        for edge in edges:
            if edge["form"] != EDGE_FORM or edge["module"] != rule["module"]:
                problems.append(
                    f"{rule['holder']} enters {package} as `{edge['form']} "
                    f"{edge['module']}`; the pinned edge is one `{EDGE_FORM} "
                    f"{rule['module']}`")
                continue
            wanted, got = sorted(rule["symbols"]), edge["symbols"]
            if got != wanted:
                problems.append(
                    f"{rule['holder']} imports {len(got)} name(s) from "
                    f"{rule['module']}; the pin is exactly {len(wanted)}. Added: "
                    f"{sorted(set(got) - set(wanted))}, removed: "
                    f"{sorted(set(wanted) - set(got))}")
                continue
    if problems:
        raise Refused(
            f"the {kind} artifact's dependency on the repository is not the one that "
            f"was pinned: " + "; ".join(problems) + ". The edge is pinned because "
            f"'the node needs fleet' is true forever and distinguishes nothing — "
            f"a reviewed change widens EXTERNAL_EDGES with the reason.")
    # Only the pinned packages: `requires_repository` is about checkouts, and
    # `yaml` is not one. A wheel is named in `requires.modules` and checked there.
    return {package: sorted({edge["holder"] for edge in edges})
            for package, edges in measured.items() if package in EXTERNAL_EDGES}


def _reachable_from(entry: str, members: dict[str, bytes]) -> set[str]:
    """The modules the payload really reaches from its entrypoint, walked by AST.

    Membership and reachability are different claims. A pin entry that is *present*
    but no longer imported is a module the artifact ships for no reason; a pin
    entry that is absent is a cut edge whose list was not trimmed. This answers the
    first question, `_refuse_node_members` answers the second.
    """
    index: dict[str, str] = {}
    for name in members:
        module = _module_of(name)
        if module:
            index[module] = name
    seen: set[str] = set()
    stack = [entry]
    while stack:
        module = stack.pop()
        if module in seen or module not in index:
            continue
        seen.add(module)
        parts = module.split(".")
        stack.extend(".".join(parts[:depth]) for depth in range(1, len(parts)))
        for imported in _imports_of(members[index[module]], index[module]):
            if imported.startswith("fleet_mcp"):
                stack.append(imported)
    return seen


def _refuse_pin_unreachable(kind: str, members: dict[str, bytes]) -> None:
    """All 17 pinned modules are reachable from the entrypoint, not merely present."""
    if kind != L2_NODE:
        return
    unreachable = sorted(set(NODE_SHARED_SURFACE) - _reachable_from(ENTRY[kind], members))
    if unreachable:
        raise Refused(
            f"the pin lists module(s) the node payload carries but never reaches "
            f"from {ENTRY[kind]!r}: {unreachable}. An artifact that ships a module "
            f"nothing imports is carrying dead weight in a release and inflating the "
            f"shared surface it reports")


def _verify_coupling(kind: str, members: dict[str, bytes],
                     *, third_party=()) -> dict[str, list[str]]:
    """The build and the verifier call this one function, so they cannot disagree."""
    holders = _refuse_external_edges(kind, members, third_party=third_party)
    _refuse_pin_unreachable(kind, members)
    return holders


#: The child that answers "does this import with nothing but the payload beside
#: it" — in a *separate process*, because the question is about a fresh
#: interpreter with an empty `sys.path` cache, and because a probe that ran in
#: this process could not un-import what the build already imported.
_PROBE = r'''
import importlib, json, os, sys
root = sys.argv[1]
# Only the payload. `-I` already dropped PYTHONPATH, the user site directory and
# the cwd; the filter below additionally drops any site-packages that happens to
# contain the package we are trying to prove is missing, because a probe that
# finds `fleet/` in site-packages would report the opposite of the truth.
sys.path[:] = [root] + [p for p in sys.path
                        if p and os.path.abspath(p) != root
                        and not os.path.isdir(os.path.join(p, "fleet"))]
answer = {}
for module in sys.argv[2:]:
    try:
        importlib.import_module(module)
        answer[module] = ["imports", ""]
    except BaseException as exc:            # noqa: BLE001 - the answer, not an error
        answer[module] = [type(exc).__name__, str(getattr(exc, "name", "") or "")]
print(json.dumps(answer))
'''


@dataclass(frozen=True)
class Standalone:
    """What a payload can do with nothing but itself on `sys.path`.

    Two facts, deliberately separate, because one boolean cannot say both and the
    gap between them is the whole of today's packaging defect:

    * `entry_imports` — `import <entrypoint>` succeeds. The node's does; the hub's
      does not, because the hub's import chain is eager and reaches `registry`.
    * `runtime_imports` — the entrypoint *and* every module that holds an external
      edge import. The node's does not: `fleet_mcp/registry.py` raises
      `FleetRefusal(INTERNAL)` without `fleet`, and `fleet_mcp/fleet.py` imports it
      at module level, so the first time the connector's runtime needs a fleet it
      dies — later, on a device, instead of here.
    """

    entry: str
    entry_imports: bool
    runtime_imports: bool
    probe: tuple[tuple[str, str], ...]      # module -> "imports" | "ExceptionClass"

    def as_dict(self) -> dict:
        return {"entry": self.entry,
                "import_standalone": self.entry_imports,
                "runtime_standalone": self.runtime_imports,
                "probe": dict(self.probe)}


_PROBE_CACHE: dict[str, Standalone] = {}


def measure_standalone(members: dict[str, bytes], *, entry: str,
                       timeout: float = 180.0) -> Standalone:
    """Import the payload in a fresh interpreter with only the payload on the path.

    Measured, never declared. A manifest field that says "standalone" and is not
    re-measured by the verifier is a field an author writes by hand once and
    nothing ever corrects — which is the exact failure this whole module exists to
    refuse in other people's packages.
    """
    _refuse_unsafe_names(members)
    # Only the edges into this repository are probed. A missing wheel (`yaml`) is a
    # pip dependency the manifest declares in `requires.modules`; counting it here
    # would make "standalone" mean "the environment is complete", which is a
    # different and much less interesting question than "does this need a checkout".
    holders = sorted({edge["holder"]
                      for package, edges in _external_edges(members).items()
                      if package in EXTERNAL_EDGES
                      for edge in edges})
    checks = tuple(sorted({entry, *holders}))
    key = entry + "|" + _tree_digest(
        [{"path": name, "sha256": _sha(members[name])} for name in sorted(members)])
    cached = _PROBE_CACHE.get(key)
    if cached is not None:
        return cached

    try:
        with tempfile.TemporaryDirectory(prefix="l2-standalone-") as scratch:
            root = Path(scratch) / "payload"
            root.mkdir()
            for name, data in members.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
            # cwd is an empty directory that is not the payload: `-I` already keeps
            # the cwd off `sys.path`, and this makes that independent of the flag.
            empty = Path(scratch) / "cwd"
            empty.mkdir()
            env = {key: value for key, value in os.environ.items()
                   if key not in ("PYTHONPATH", "PYTHONHOME")}
            env["PYTHONDONTWRITEBYTECODE"] = "1"
            done = subprocess.run(
                [sys.executable, "-I", "-B", "-c", _PROBE, str(root), *checks],
                capture_output=True, text=True, cwd=empty, env=env, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        raise Refused(
            f"the standalone probe could not run ({type(exc).__name__}: {exc}); an "
            f"artifact whose isolation nothing measured cannot be described as one "
            f"that was measured to be isolated") from exc

    if done.returncode != 0:
        raise Refused(
            f"the standalone probe failed ({done.returncode}): "
            f"{(done.stderr or done.stdout).strip()[-400:]}")
    try:
        answer = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise Refused(f"the standalone probe printed something that is not JSON: "
                      f"{done.stdout[:200]!r}") from exc

    missing = [module for module in checks if module not in answer]
    if missing:
        raise Refused(f"the standalone probe did not report {missing}")
    probe = tuple((module, answer[module][0]) for module in checks)
    verdict = Standalone(entry=entry,
                         entry_imports=answer[entry][0] == "imports",
                         runtime_imports=all(answer[module][0] == "imports"
                                             for module in checks),
                         probe=probe)
    _PROBE_CACHE[key] = verdict
    return verdict


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
    # Completeness first ("is this import declared at all?"), then shape ("is the
    # declared dependency the ONE that was pinned?"). In the other order every new
    # in-repo edge is reported as a pin violation, which is true but buries the
    # simpler fact that the manifest does not mention it.
    _verify_dependencies(manifest, artifact)
    holders = _verify_coupling(
        kind, artifact.members,
        third_party=(block.get("requires") or {}).get("modules") or [])
    measured = _verify_standalone(kind, artifact, block, holders)
    _refuse_vendored_schema(artifact.members)

    return {"ok": True, "kind": kind, "version": version,
            "members": len(listed), "digest": artifact.digest,
            # Both halves of the answer, never one: a reader that sees
            # `import_standalone: true` and stops has read the half that says
            # "it loads", not the half that says "it runs".
            "import_standalone": measured.entry_imports,
            "runtime_standalone": measured.runtime_imports,
            "standalone": measured.runtime_imports,
            "independently_installable": bool(
                (block.get("install") or {}).get("independently_installable")),
            "install_requires_repo": holders}


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

    # `standalone`, `import_standalone`, `runtime_standalone` and the `install`
    # block are NOT checked by arithmetic here. They used to be, as
    # `standalone == (not external_in_repo and not depends_on)` — true by
    # definition, so a field that could never catch the thing it was named for.
    # They are now compared to a probe that imports the payload, in
    # `_verify_standalone`, which is the only kind of check worth having on a
    # claim about isolation.
    for field in ("external_in_repo", "depends_on", "shared_surface"):
        value = block.get(field)
        if value is not None and not (isinstance(value, list)
                                      and all(isinstance(item, str) for item in value)):
            raise Refused(f"the l2 block's {field} is {value!r}, not a list of "
                          f"module names")

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
    # A repository package declared as a wheel. `pip install fleet` would find some
    # unrelated distribution and the payload would import it; the dependency this
    # artifact actually has is a checkout, and it is pinned as one.
    disguised = sorted(set(requires["modules"]) & set(EXTERNAL_EDGES))
    if disguised:
        raise Refused(
            f"the l2 block declares {disguised} under requires.modules; "
            f"{disguised} is a package in this repository, not a wheel pip can "
            f"install, and describing it as a third-party module hides the "
            f"dependency instead of satisfying it")
    return block


def _verify_standalone(kind: str, artifact: Artifact, block: dict,
                       holders: dict[str, list[str]]) -> Standalone:
    """Re-measure isolation and compare it to what the manifest says.

    Every field here is a claim about behaviour on a machine that has this payload
    and nothing else, and every one of them is answered by running the payload
    rather than by reading it. The verifier does not trust the build's answer for
    the same reason a release does not trust a build: the manifest travels without
    the code that wrote it.
    """
    measured = measure_standalone(artifact.members, entry=ENTRY[kind])
    problems: list[str] = []

    for field, actual in (("import_standalone", measured.entry_imports),
                          ("runtime_standalone", measured.runtime_imports)):
        declared = block.get(field)
        if not isinstance(declared, bool):
            problems.append(f"l2.{field} is {declared!r}, not a boolean")
        elif declared != actual:
            problems.append(
                f"l2.{field} says {declared} and importing the payload in an "
                f"interpreter with nothing but the payload on sys.path measures "
                f"{actual} ({dict(measured.probe)})")

    if block.get("standalone") != block.get("runtime_standalone"):
        problems.append(
            f"l2.standalone={block.get('standalone')!r} disagrees with "
            f"l2.runtime_standalone={block.get('runtime_standalone')!r}; "
            f"`standalone` is the legacy spelling of the same claim, and two "
            f"spellings of one claim is how they come to differ")

    declared_probe = block.get("probe")
    if not isinstance(declared_probe, dict):
        problems.append("the l2 block carries no probe record; the claim and the "
                        "evidence for it are one field apart on purpose")
    elif {str(name): str(verdict) for name, verdict in declared_probe.items()} \
            != dict(measured.probe):
        problems.append(
            f"l2.probe records {dict(declared_probe)!r} and the payload measures "
            f"{dict(measured.probe)!r}")

    if block.get("install_requires_repo") != holders:
        problems.append(
            f"l2.install_requires_repo says {block.get('install_requires_repo')!r} "
            f"and the payload reaches the repository from {holders!r}")

    install = block.get("install")
    # What an independent install needs, computed from the measurement and the
    # declared dependencies — never read back from the manifest.
    allowed = not holders and not (block.get("depends_on") or [])
    expect_installable = measured.runtime_imports and allowed
    if not isinstance(install, dict):
        problems.append(
            "the l2 block carries no install block; whether this artifact can be "
            "installed on its own is the question a manifest exists to answer, and "
            "leaving it to be inferred from `external_in_repo` is how a payload "
            "that needs a checkout gets read as a payload that does not")
    else:
        declared_installable = install.get("independently_installable")
        if not isinstance(declared_installable, bool):
            problems.append(
                f"install.independently_installable is {declared_installable!r}, "
                f"not a boolean")
        elif declared_installable and not expect_installable:
            problems.append(
                f"install.independently_installable says True and this payload "
                f"cannot be: runtime_standalone={measured.runtime_imports}, "
                f"repository dependencies {holders!r}, depends_on "
                f"{block.get('depends_on')!r}. An artifact that needs a checkout "
                f"beside it is not made installable by a manifest field, however "
                f"permissively it was built")
        elif declared_installable != expect_installable:
            problems.append(
                f"install.independently_installable says {declared_installable} and "
                f"the payload measures {expect_installable}")
        if install.get("requires_repository") != holders:
            problems.append(
                f"install.requires_repository says "
                f"{install.get('requires_repository')!r} and the payload reaches "
                f"{holders!r}")
        blocked_by = install.get("blocked_by")
        if not isinstance(blocked_by, list):
            problems.append(f"install.blocked_by is {blocked_by!r}, not a list")
        elif bool(blocked_by) == bool(expect_installable):
            problems.append(
                f"install.blocked_by is {blocked_by!r}; a payload that is "
                f"independently installable ({expect_installable}) must name what "
                f"blocks it exactly when something does")

    if problems:
        raise Refused(f"the {kind} artifact's isolation claim does not match what "
                      f"the payload does: " + "; ".join(problems))
    return measured


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
# The L0 authority sidecar
#
# WHY A SECOND ARTIFACT AND NOT A WIDER HUB
# -----------------------------------------
# The Hub's only remaining repository dependency is the identity authority
# (`fleet_mcp/registry.py` -> `fleet.transport`). Three answers are refused:
# vendor it (a second `DeviceRef`, which agrees on the day it is written and
# diverges at the first rename), fall back to local data (a fleet that exists
# only inside the Hub), and quietly treat the Hub as standalone (the claim the
# Hub's own port exists to prevent).
#
# What is shipped instead is the dependency as its OWN versioned artifact: the
# canonical module bytes, a manifest that names the ABI and digests every file,
# and a Hub that declares it needs exactly that. The authority stays single —
# this is the same `fleet/transport.py`, distributed, not re-implemented — and
# the Hub's `standalone` stays false, because a Hub alone still cannot import.
# --------------------------------------------------------------------------- #

#: The sidecar's own kind. Deliberately NOT added to `L2_KINDS`: the release
#: tool registers kinds one at a time (#433 owns that list), and an unregistered
#: kind must not be buildable through a parser that would then hand it to a
#: verifier that has never heard of it.
AUTHORITY_KIND = "fleet-authority"
AUTHORITY_ABI = "serveros.fleet.transport/v1"
AUTHORITY_MODULE = "fleet.transport"
AUTHORITY_SOURCE = "fleet/transport.py"
#: The module's OWN contract: `transport.py` reads it at module level
#: (`CAPABILITY_LEVELS = tuple(transport_contract()["capability_levels"]["order"])`
#: and two neighbours), so a payload carrying only the .py imports a module that
#: raises FileNotFoundError. It is vocabulary — capability levels, aliases,
#: availability values — and names no device, which is why it may travel with
#: the code while `registry.yaml` may not.
AUTHORITY_CONTRACT = "fleet/transport-contract.yaml"
#: The closed file set. Anything else in the payload is refused, not ignored.
AUTHORITY_FILES = (AUTHORITY_SOURCE, AUTHORITY_CONTRACT)
AUTHORITY_MANIFEST_NAME = "fleet-authority.json"
#: Authored data, never bundled: a registry is a statement about a fleet, and a
#: payload that shipped one would be inventing devices.
AUTHORITY_DATA_NOT_BUNDLED = ("fleet/registry.yaml",)
AUTHORITY_MAX_SOURCE_BYTES = 512 * 1024
AUTHORITY_SECRET_HINTS = ("credential", "token", "secret", "password", "private_key")


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _authority_file(root: Path, rel: str) -> bytes:
    path = root / rel
    if not path.is_file():
        raise Refused(f"the identity authority file {rel} is absent from {root}")
    if path.is_symlink():
        raise Refused(f"{rel} is a symlink; the authority is a file, not a pointer")
    data = path.read_bytes()
    if len(data) > AUTHORITY_MAX_SOURCE_BYTES:
        raise Refused(f"{rel} is {len(data)} bytes, beyond the "
                      f"{AUTHORITY_MAX_SOURCE_BYTES}-byte bound this sidecar accepts")
    return data


def authority_declaration(expected_payload_sha256: str | None = None) -> dict:
    """What the Hub publishes about the dependency it cannot carry itself.

    `expected_payload_sha256` is optional on purpose. Left empty the Hub states
    the ABI it speaks and accepts whatever verified sidecar arrives; set, the
    Hub pins one payload and a mismatch is refused. `hub_install_plan` reports
    which of the two it was, so "pinned" is never inferred.
    """
    return {
        "abi": AUTHORITY_ABI,
        "module": AUTHORITY_MODULE,
        "provided_by": AUTHORITY_KIND,
        "required": True,
        "expected_payload_sha256": expected_payload_sha256,
        # The Hub still may not call itself standalone: the sidecar is a second
        # install, and a deployment that forgets it fails at import.
        "hub_standalone_with_satisfied": False,
        "data_not_bundled": list(AUTHORITY_DATA_NOT_BUNDLED),
    }


def authority_members(root: Path) -> dict[str, bytes]:
    """The sidecar's bytes: the canonical module and a manifest that digests it."""
    payload = {rel: _authority_file(root, rel) for rel in AUTHORITY_FILES}
    source = payload[AUTHORITY_SOURCE]
    manifest = {
        "schema": "serveros/fleet-authority.v1",
        "abi": AUTHORITY_ABI,
        "module": AUTHORITY_MODULE,
        # The closed declaration `identity_adapter_port.verify_payload` reads:
        # every .py/.yaml in the payload must appear here, and the authority
        # module is mandatory.
        "files": {rel: _sha256_bytes(data) for rel, data in payload.items()},
        "data_not_bundled": list(AUTHORITY_DATA_NOT_BUNDLED),
        "single_authority": True,
    }
    payload[AUTHORITY_MANIFEST_NAME] = json.dumps(
        manifest, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8") + b"\n"
    return payload


def verify_authority_members(members: dict[str, bytes]) -> dict:
    """Refuse a sidecar payload that is not exactly what it declares.

    The checks are closed in the same sense the Hub's port is closed: a file the
    manifest does not name is refused, not ignored, because it would sit on
    `sys.path` as if it were part of the authority.
    """
    for name in members:
        if name.startswith("/") or ".." in Path(name).parts:
            raise Refused(f"sidecar member {name!r} escapes the payload")
        if any(hint in name.lower() for hint in AUTHORITY_SECRET_HINTS):
            raise Refused(f"sidecar member {name!r} looks like credential material; "
                          f"an authority payload carries code and a declaration only")
        if name.endswith(FORBIDDEN_SUFFIXES):
            raise Refused(f"sidecar member {name!r} is bytecode")
    if AUTHORITY_MANIFEST_NAME not in members:
        raise Refused(f"sidecar has no {AUTHORITY_MANIFEST_NAME}; an undeclared "
                      f"payload is refused rather than imported")
    try:
        declared = json.loads(members[AUTHORITY_MANIFEST_NAME].decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise Refused(f"sidecar manifest is not readable JSON ({type(exc).__name__})") from None
    if declared.get("abi") != AUTHORITY_ABI:
        raise Refused(f"sidecar declares ABI {declared.get('abi')!r}; the contract is "
                      f"{AUTHORITY_ABI!r}")
    if declared.get("module") != AUTHORITY_MODULE:
        raise Refused(f"sidecar declares module {declared.get('module')!r}; the contract "
                      f"is {AUTHORITY_MODULE!r}")
    files = declared.get("files")
    if not isinstance(files, dict) or AUTHORITY_SOURCE not in files:
        raise Refused(f"sidecar manifest must declare {AUTHORITY_SOURCE!r} in `files`")
    missing_decl = [rel for rel in AUTHORITY_FILES if rel not in files]
    if missing_decl:
        raise Refused(f"sidecar manifest does not declare {missing_decl}; the authority "
                      f"is {AUTHORITY_SOURCE} plus its own contract")
    unexpected = sorted(n for n in members
                        if n != AUTHORITY_MANIFEST_NAME and n not in AUTHORITY_FILES)
    if unexpected:
        raise Refused(f"sidecar carries members outside the closed authority set: "
                      f"{unexpected}; the payload is two named files, not a directory")
    undeclared = sorted(n for n in members
                        if n != AUTHORITY_MANIFEST_NAME
                        and (n.endswith((".py", ".yaml", ".yml")))
                        and n not in files)
    if undeclared:
        raise Refused(f"sidecar carries files its manifest does not declare: {undeclared}")
    for name, digest in files.items():
        if name not in members:
            raise Refused(f"sidecar declares {name!r}, which is absent")
        actual = _sha256_bytes(members[name])
        if not isinstance(digest, str) or actual != digest.lower():
            raise Refused(f"sidecar digest does not match {name}: declared {digest!r}, "
                          f"measured {actual!r}. An unverified payload is refused.")
    for forbidden in AUTHORITY_DATA_NOT_BUNDLED:
        if forbidden in members:
            raise Refused(f"sidecar carries {forbidden!r}; authored fleet data is the "
                          f"operator's to supply, not this payload's to invent")
    return declared


def authority_manifest_for(version: str, *, root: Path,
                           platforms: tuple[str, ...] = ("linux", "windows")
                           ) -> tuple[dict, dict[str, bytes]]:
    """Compose the sidecar's description and bytes."""
    if not VERSION_RE.match(version):
        raise Refused(f"version {version!r} is not <major>.<minor>.<patch>[+<cachebuster>]")
    members = authority_members(root)
    declared = verify_authority_members(members)
    entries = [{"path": name, "sha256": _sha256_bytes(data)}
               for name, data in sorted(members.items())]
    manifest = {
        "schema": ARTIFACT_MANIFEST_SCHEMA,
        "kinds": [AUTHORITY_KIND],
        "composition": COMPOSITION,
        "name": "fleet-l2-authority",
        "version": version,
        "platforms": sorted(platforms),
        "members": [e["path"] for e in entries],
        "member_sha256": {e["path"]: e["sha256"] for e in entries},
        "source": {"module": AUTHORITY_MODULE, "module_count": len(members),
                   "tree_sha256": _tree_digest(entries)},
        "authority": {
            "abi": AUTHORITY_ABI,
            "module": AUTHORITY_MODULE,
            # The Hub compares these two against its own declaration; a sidecar
            # for a different ABI is a dependency it cannot satisfy.
            "payload_sha256": declared["files"][AUTHORITY_SOURCE],
            "single_authority": True,
            "data_not_bundled": list(AUTHORITY_DATA_NOT_BUNDLED),
        },
    }
    return manifest, members


def verify_authority_artifact(path: Path, *, version: str | None = None) -> Artifact:
    """Read a sidecar back and verify it, including the payload contract."""
    artifact = read_artifact(path)
    kinds = artifact.manifest.get("kinds") or []
    if kinds != [AUTHORITY_KIND]:
        raise Refused(f"{path} is {kinds}, not a [{AUTHORITY_KIND!r}] artifact")
    if version is not None and artifact.version != version:
        raise Refused(f"{path} is version {artifact.version!r}, expected {version!r}")
    declared = verify_authority_members(artifact.members)
    block = artifact.manifest.get("authority") or {}
    if block.get("abi") != AUTHORITY_ABI or block.get("module") != AUTHORITY_MODULE:
        raise Refused(f"{path} does not declare the {AUTHORITY_ABI} contract")
    if block.get("payload_sha256") != declared["files"][AUTHORITY_SOURCE]:
        raise Refused(f"{path} disagrees with its own payload digest")
    return artifact


def authority_rollback_target(previous: Path, *, version: str | None = None) -> Artifact:
    """The sidecar a rollback would activate — verified before it is named."""
    return verify_authority_artifact(previous, version=version)


def authority_imports_without_checkout(artifact: Artifact) -> bool:
    """Extract the payload into an empty tree and import it there.

    The claim "installable" is only worth the measurement behind it: the payload
    is unpacked somewhere with no repository, put alone on `sys.path`, and the
    seven symbols must actually resolve.
    """
    with tempfile.TemporaryDirectory(prefix="l2-authority-import-") as tmp:
        tree = Path(tmp) / "payload"
        tree.mkdir()
        for name, data in artifact.members.items():
            target = tree / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        script = (
            "import json,sys\n"
            f"sys.path.insert(0, {str(tree)!r})\n"
            "import fleet.transport as t\n"
            "need = ['Availability','CapabilityAvailability','Device','DeviceRef',\n"
            "        'DeviceRegistry','UnknownDevice','canonical_level']\n"
            "missing = [n for n in need if not hasattr(t, n)]\n"
            "print(json.dumps({'missing': missing, 'file': getattr(t,'__file__','')}))\n"
        )
        done = subprocess.run([sys.executable, "-I", "-c", script],
                              capture_output=True, text=True, timeout=120,
                              env={"PATH": "/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        if done.returncode != 0:
            raise Refused(f"the authority payload does not import in an empty tree: "
                          f"{done.stderr.strip()[-400:]}")
        answer = json.loads(done.stdout.strip().splitlines()[-1])
        if answer["missing"]:
            raise Refused(f"the payload imports but is not the authority it claims: "
                          f"missing {answer['missing']}")
        if str(tree) not in answer["file"]:
            raise Refused("the imported authority did not come from the payload")
    return True


def hub_install_plan(hub: Path, authority: Path | None) -> dict:
    """May this Hub be deployed? Only when the companion authority verifies too.

    Returns a record rather than raising, because "not installable" is an answer
    a release reader needs, not an exception to swallow. Both artifacts are
    verified; either failing leaves the plan un-installable with the reason.
    """
    blockers: list[str] = []
    verified_hub = None
    try:
        verified_hub = read_artifact(hub)
        verify(verified_hub, expect_kind=L2_HUB)
    except Exception as exc:  # noqa: BLE001 - surfaced as a blocker
        blockers.append(f"hub: {type(exc).__name__}: {exc}")
    verified_authority = None
    if authority is None:
        blockers.append(f"no {AUTHORITY_KIND} sidecar supplied; the Hub's "
                        f"{AUTHORITY_ABI} dependency is unmet")
    else:
        try:
            verified_authority = verify_authority_artifact(authority)
            authority_imports_without_checkout(verified_authority)
        except Exception as exc:  # noqa: BLE001
            verified_authority = None
            blockers.append(f"authority: {type(exc).__name__}: {exc}")
    if verified_hub is not None:
        declared = ((verified_hub.manifest.get("l2") or {}).get("install") or {}).get("authority")
        if not isinstance(declared, dict) or declared.get("abi") != AUTHORITY_ABI:
            blockers.append("hub does not declare the authority dependency")
        elif verified_authority is not None:
            provided = (verified_authority.manifest.get("authority") or {})
            if provided.get("abi") != declared.get("abi"):
                blockers.append("hub and sidecar name different ABIs")
            pinned = declared.get("expected_payload_sha256")
            if pinned is not None and provided.get("payload_sha256") != pinned:
                blockers.append("the hub pins an authority payload this sidecar is not")
    installable = not blockers and verified_hub is not None and verified_authority is not None
    return {
        "installable": installable,
        "hub_alone_standalone": False if verified_hub is None else
                                bool(((verified_hub.manifest.get("l2") or {})
                                      .get("standalone"))),
        "with": [AUTHORITY_KIND] if installable else [],
        "blocked_by": blockers,
        "hub_digest": None if verified_hub is None else verified_hub.digest,
        "authority_digest": None if verified_authority is None else verified_authority.digest,
        "authority_abi": AUTHORITY_ABI,
        # Pinned means the Hub named the payload digest it will accept. Unpinned
        # means it accepts any verified sidecar speaking the same ABI; the plan
        # says which, rather than implying one.
        "authority_pinned": bool(
            ((verified_hub.manifest.get("l2") or {}).get("install") or {})
            .get("authority", {}).get("expected_payload_sha256")) if verified_hub else False,
    }


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

    probe = sub.add_parser(
        "measure", help="import a payload in isolation and report what it needs")
    probe.add_argument("artifact", type=Path)

    args = parser.parse_args(argv)
    try:
        if args.command == "build":
            manifest, members = manifest_for(
                args.kind, args.version, root=Path(args.root),
                depends_on=tuple(args.depends_on),
                allow_external_in_repo=tuple(args.allow_external_in_repo))
            path = write_artifact(manifest, members, args.output)
            block = manifest["l2"]
            print(json.dumps({"ok": True, "kind": args.kind, "version": args.version,
                              "members": len(members), "output": str(path),
                              "import_standalone": block["import_standalone"],
                              "runtime_standalone": block["runtime_standalone"],
                              "standalone": block["standalone"],
                              "independently_installable":
                                  block["install"]["independently_installable"],
                              "external_in_repo": block["external_in_repo"],
                              "install_requires_repo": block["install_requires_repo"],
                              "shared_surface": len(block["shared_surface"])},
                             indent=2))
        elif args.command == "verify":
            print(json.dumps(verify(read_artifact(args.artifact),
                                    expect_kind=args.kind,
                                    expect_version=args.version), indent=2))
        elif args.command == "measure":
            read = read_artifact(args.artifact)
            if read.kind not in ENTRY:
                raise Refused(f"{args.artifact} declares kind(s) "
                              f"{read.manifest.get('kinds')!r}, which have no "
                              f"entrypoint to import")
            holders = _refuse_external_edges(
                read.kind, read.members,
                third_party=((read.manifest.get("l2") or {}).get("requires")
                             or {}).get("modules") or [])
            measured = measure_standalone(read.members, entry=ENTRY[read.kind])
            depends_on = (read.manifest.get("l2") or {}).get("depends_on") or []
            answer = measured.as_dict()
            answer.update({
                "ok": True, "kind": read.kind, "version": read.version,
                "install_requires_repo": holders,
                # Same expression the build writes into `install`, so this command
                # cannot report a payload installable that the manifest refused to.
                "independently_installable":
                    measured.runtime_imports and not holders and not depends_on,
            })
            print(json.dumps(answer, indent=2))
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
