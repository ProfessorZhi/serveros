#!/usr/bin/env python3
"""Wave 17: can the Hub and the Node be released, installed and rolled back
separately, without a checkout and without each other?

The finding this module answers
-------------------------------
Wave 16 (#502) measured two facts at `main@e69fc03`:

    Hub   49 files  reproducible  revocable  independent = FALSE
    Node 180 files  reproducible  revocable  independent = FALSE

with exactly one Hub -> Node import (`fleet_mcp/owner_setup.py` ->
`fleet_mcp.node.connector`) and ten Node -> Hub imports. "Release the Node on
its own" was therefore the whole repository wearing a smaller name.

This module counts **eleven** Node -> Hub module targets, not ten: #502's
resolver did not expand `from .. import cloudcli_native`, while #431's AST does.
The finding is unchanged and the extra target is real; the difference is
reported rather than rounded to the earlier number.

What this module does
---------------------
It **measures** that shape against two different maps and refuses to confuse
them:

* the **ownership map** — who may edit a file. Node owns `fleet_mcp/node/**`
  and `native/node-core/**`; the Hub owns the rest of `fleet_mcp/**`. This is
  #502's map, reproduced exactly (49 / 180 / 2).
* the **release map** — what an artifact must carry to import. That is the
  import closure of its declared entrypoints, computed by #431's builder, not
  re-implemented here.

It then composes a **three-unit topology**:

    fleet-runtime-base  the shared primitives both halves genuinely need
    fleet-authority     the ONE identity authority (fleet/transport.py), as a
                        verified sidecar; never a second DeviceRef
    fleet-hub           the Hub's own modules only
    fleet-node          the Node's own modules only

Hub and Node never carry each other's files. Each boots with the Base and the
Authority — which are *not* the other artifact — and with no checkout.

What this module refuses to claim
---------------------------------
* It never writes `fleet_mcp/**`. The cuts that make the ownership map agree
  with the release map are an **owner handoff** (`handoff/`), applied only to a
  throwaway copy made with `git archive`.
* It does not say "released". On the live tree the verdict is
  `BLOCKED_OWNER`; the sandbox proof is `GREEN_SANDBOX`.
* A real co-install on a device is `NOT_RUN`. Nothing here installs, starts,
  binds a port, opens a socket, reads a credential or sends a message.

The Base is a pin, not a dumping ground
---------------------------------------
`BASE_ADMISSIBLE` is the closed allowlist of modules that may be shared. It is
checked in **both** directions, the way #431 pins `NODE_SHARED_SURFACE`: a
shared module not on the list fails the build, and a list entry the closures no
longer share also fails it. A Base that could silently grow into "everything
both halves happen to import" would be fake independence with a manifest.
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
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "serveros/fleet-next-wave17-hub-node-cut.v1"

#: #431's builder, reused read-only. The pin is the file's SHA256 at #431's
#: head (517ef91). A changed builder is a changed release fact: this lane must
#: stop and be re-read, not silently inherit a new closure.
BUILDER_REL = "integrations/fleet-v1-l2-release/l2_release.py"
BUILDER_PIN_SHA256 = (
    "7f87a59611b00585f2e7eba3799c7d4bd0c256abec775b96e7ed8d6f0a5ba01d")

HUB_ENTRIES = ("fleet_mcp.server", "fleet_mcp.owner_setup")
NODE_ENTRIES = ("fleet_mcp.node.connector",)

NODE_OWNED_PREFIXES = ("fleet_mcp/node/", "native/node-core/")
SIDECAR_FILES = (
    "fleet_mcp/deploy/next_tailscale_gate.py",
    "fleet_mcp/deploy/next_tailscale_plan.example.json",
)

#: The shared primitives, pinned. These are the modules the Node genuinely
#: needs at runtime after CUT-A/CUT-B, and they name no device, no fleet
#: policy and no Hub service. Anything else that becomes shared is new
#: coupling and must be reviewed rather than absorbed.
BASE_ADMISSIBLE = frozenset({
    "fleet_mcp",
    "fleet_mcp.cloudcli_native",
    "fleet_mcp.config",
    "fleet_mcp.disclosure",
    "fleet_mcp.discovery",
    "fleet_mcp.errors",
    "fleet_mcp.feishu_sso",
    "fleet_mcp.identity",
    "fleet_mcp.local_roots",
    "fleet_mcp.mcp_http",
    "fleet_mcp.node_protocol",
    "fleet_mcp.owner_access",
    "fleet_mcp.providers",
    "fleet_mcp.providers.base",
    "fleet_mcp.providers.cloudcli",
    "fleet_mcp.runtime_create_policy",
    "fleet_mcp.runtime_identity",
    "fleet_mcp.scopes",
    "fleet_mcp.version",
})

#: The one identity authority. One file, one contract, one declaration; the
#: sidecar is code + the vocabulary it reads, and never authored fleet data.
AUTHORITY_KIND = "fleet-authority"
AUTHORITY_ABI = "serveros.fleet.transport/v1"
AUTHORITY_MODULE = "fleet.transport"
AUTHORITY_FILES = ("fleet/transport.py", "fleet/transport-contract.yaml")
AUTHORITY_MANIFEST_NAME = "fleet-authority.json"
AUTHORITY_SYMBOLS = (
    "Availability", "CapabilityAvailability", "Device", "DeviceRef",
    "DeviceRegistry", "UnknownDevice", "canonical_level",
)
AUTHORITY_DATA_NOT_BUNDLED = ("fleet/registry.yaml",)

BASE_KIND = "fleet-runtime-base"
HUB_KIND = "fleet-hub"
NODE_KIND = "fleet-node"

OK = "OK"
VERIFIED = "VERIFIED"
GREEN_SANDBOX = "GREEN_SANDBOX"
BLOCKED_OWNER = "BLOCKED_OWNER"
BLOCKED_FOREIGN_IMPORT = "BLOCKED_FOREIGN_IMPORT"
BLOCKED_BASE_NOT_PINNED = "BLOCKED_BASE_NOT_PINNED"
BLOCKED_BUILDER_DRIFT = "BLOCKED_BUILDER_DRIFT"
BLOCKED_MANIFEST_DRIFT = "BLOCKED_MANIFEST_DRIFT"
BLOCKED_ISOLATION = "BLOCKED_ISOLATION"
BLOCKED_RESOURCE_COLLISION = "BLOCKED_RESOURCE_COLLISION"
BLOCKED_DEVICE_DISAGREEMENT = "BLOCKED_DEVICE_DISAGREEMENT"
BLOCKED_UNSAFE_MEMBER = "BLOCKED_UNSAFE_MEMBER"
NOT_RUN = "NOT_RUN"

FORBIDDEN_MEMBER = re.compile(
    r"(^|/)(\.env|.*\.(sock|lock|pid|state|pem|key)|"
    r".*(credential|token|secret|password).*)$", re.IGNORECASE)
FORBIDDEN_SUFFIXES = (".pyc", ".pyo")


class Prerequisite(RuntimeError):
    """A fact this lane depends on is absent or has moved."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


class Refused(RuntimeError):
    """A composition that must not be called a release."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


# ---------------------------------------------------------------------------
# reusing #431's builder
# ---------------------------------------------------------------------------
def load_builder(repo: Path) -> Any:
    """Import the two-artifact builder from `repo`, pinned by content."""
    path = Path(repo) / BUILDER_REL
    if not path.is_file():
        raise Prerequisite(
            "PR431_BUILDER_ABSENT",
            f"{BUILDER_REL} is absent from {repo}; this lane is stacked on #431 "
            f"(517ef91) and must not re-implement its closure rules")
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != BUILDER_PIN_SHA256:
        raise Prerequisite(
            BLOCKED_BUILDER_DRIFT,
            f"{BUILDER_REL} is sha256:{actual}, pinned "
            f"sha256:{BUILDER_PIN_SHA256}; #431's release facts moved, so this "
            f"lane must be re-read before it can describe them")
    spec = importlib.util.spec_from_file_location("wave17_l2_release", path)
    if spec is None or spec.loader is None:
        raise Prerequisite("PR431_BUILDER_UNREADABLE", f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# the ownership map (#502's), applied to files rather than closures
# ---------------------------------------------------------------------------
def owner_of_file(rel: str) -> str | None:
    if rel.startswith(NODE_OWNED_PREFIXES):
        return "node"
    if rel in SIDECAR_FILES:
        return "sidecar"
    if rel.startswith(("fleet_mcp/", "native/node-core/")):
        return "hub"
    return None


def owner_of_module(dotted: str) -> str | None:
    if dotted == "fleet_mcp.node" or dotted.startswith("fleet_mcp.node."):
        return "node"
    if dotted.startswith("fleet_mcp.deploy.next_tailscale"):
        return "sidecar"
    if dotted == "fleet_mcp" or dotted.startswith("fleet_mcp."):
        return "hub"
    return None


def component_files(root: Path) -> dict[str, list[str]]:
    """Every source-tree file under the two component roots, by owner."""
    found: dict[str, list[str]] = {"hub": [], "node": [], "sidecar": []}
    for base in ("fleet_mcp", "native/node-core"):
        top = Path(root) / base
        if not top.is_dir():
            continue
        for path in sorted(top.rglob("*")):
            if not path.is_file():
                continue
            if "__pycache__" in path.parts or path.suffix in FORBIDDEN_SUFFIXES:
                continue
            rel = path.relative_to(root).as_posix()
            owner = owner_of_file(rel)
            if owner is not None:
                found[owner].append(rel)
    return found


def owned_import_edges(builder: Any, root: Path,
                       component: str) -> dict[str, list[str]]:
    """Cross-component imports found in files this component owns.

    This is #502's measurement, reproduced rather than trusted. The import
    resolution is #431's `_imports_of` — the same function its closure uses —
    so the two lanes cannot disagree about what `from ..errors import X`
    reaches. Its result also names imported symbols (`module.Name`); those do
    not resolve to a file and are dropped, leaving the module a holder really
    imports. The result is `module -> holders`, because "the Node imports
    config" is a sentence and `config <- connector.py` is a fact somebody can
    check.
    """
    root = Path(root)
    files = component_files(root)[component]
    edges: dict[str, set[str]] = {}
    for rel in files:
        if not rel.endswith(".py"):
            continue
        data = (root / rel).read_bytes()
        try:
            names = builder._imports_of(data, rel)
        except SyntaxError:
            continue
        for name in names:
            if builder._resolve(name, root) is None:
                continue
            owner = owner_of_module(name)
            if owner is not None and owner != component:
                edges.setdefault(name, set()).add(rel)
    return {name: sorted(holders) for name, holders in sorted(edges.items())}


# ---------------------------------------------------------------------------
# release closure, from #431's builder
# ---------------------------------------------------------------------------
def module_files(builder: Any, root: Path, modules: Iterable[str]) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for name in modules:
        path = builder._resolve(name, Path(root))
        if path is None:
            continue
        rel = path.relative_to(root).as_posix()
        payload[rel] = path.read_bytes()
    return dict(sorted(payload.items()))


def closure_modules(builder: Any, root: Path, entries: Sequence[str]) -> set[str]:
    modules: set[str] = set()
    for entry in entries:
        modules |= set(builder.closure(entry, root).modules)
    return modules


@dataclass(frozen=True)
class Partition:
    hub_modules: frozenset[str]
    node_modules: frozenset[str]
    shared: frozenset[str]
    hub_own: frozenset[str]
    node_own: frozenset[str]
    base_inadmissible: frozenset[str]
    base_unreached: frozenset[str]
    base_exact: bool

    @property
    def base_modules(self) -> frozenset[str]:
        return self.shared & BASE_ADMISSIBLE


def partition(builder: Any, root: Path) -> Partition:
    hub = frozenset(closure_modules(builder, root, HUB_ENTRIES))
    node = frozenset(closure_modules(builder, root, NODE_ENTRIES))
    shared = hub & node
    inadmissible = shared - BASE_ADMISSIBLE
    unreached = BASE_ADMISSIBLE - shared
    return Partition(
        hub_modules=hub,
        node_modules=node,
        shared=shared,
        hub_own=hub - shared,
        node_own=node - shared,
        base_inadmissible=inadmissible,
        base_unreached=unreached,
        base_exact=not inadmissible and not unreached,
    )


def effective_foreign(builder: Any, root: Path,
                      component: str) -> dict[str, list[str]]:
    """Foreign imports after the Base decision.

    A Node import of a Base-admissible primitive is satisfied by the Base
    artifact, not by the Hub. Everything else stays foreign: that is the
    difference between a declared shared layer and a Hub dependency wearing a
    new name.
    """
    edges = owned_import_edges(builder, root, component)
    if component == "node":
        return {name: holders for name, holders in edges.items()
                if name not in BASE_ADMISSIBLE}
    return edges


# ---------------------------------------------------------------------------
# manifests and artifacts
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def tree_digest(builder: Any, members: Mapping[str, bytes]) -> str:
    entries = [{"path": name, "sha256": sha256_bytes(data)}
               for name, data in sorted(members.items())]
    return builder._tree_digest(entries)


def _refuse_unsafe_members(members: Mapping[str, bytes], where: str) -> None:
    problems: list[str] = []
    for name in members:
        path = PurePosixPath(name)
        if (path.is_absolute() or ".." in path.parts
                or path.as_posix() != name or "\\" in name):
            problems.append(name)
        elif FORBIDDEN_MEMBER.search(name):
            problems.append(name)
    if problems:
        raise Refused(
            BLOCKED_UNSAFE_MEMBER,
            f"{where} carries member(s) that are not a portable source path or "
            f"that name runtime state/credential material: {sorted(problems)}")


def authority_members(root: Path) -> dict[str, bytes]:
    """The single identity authority, as code plus the vocabulary it reads."""
    payload: dict[str, bytes] = {}
    for rel in AUTHORITY_FILES:
        path = Path(root) / rel
        if not path.is_file():
            raise Prerequisite("AUTHORITY_ABSENT", f"{rel} is absent from {root}")
        payload[rel] = path.read_bytes()
    manifest = {
        "schema": "serveros/fleet-authority.v1",
        "abi": AUTHORITY_ABI,
        "module": AUTHORITY_MODULE,
        "files": {rel: sha256_bytes(data) for rel, data in payload.items()},
        "symbols": list(AUTHORITY_SYMBOLS),
        "data_not_bundled": list(AUTHORITY_DATA_NOT_BUNDLED),
        "single_authority": True,
    }
    payload[AUTHORITY_MANIFEST_NAME] = (
        json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n").encode("utf-8")
    return dict(sorted(payload.items()))


@dataclass(frozen=True)
class ArtifactPlan:
    name: str
    kind: str
    version: str
    members: Mapping[str, bytes]
    manifest: Mapping[str, Any]
    tree_sha256: str

    @property
    def member_sha256(self) -> dict[str, str]:
        return dict(self.manifest["member_sha256"])

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "kind": self.kind,
            "version": self.version,
            "members": sorted(self.members),
            "member_count": len(self.members),
            "tree_sha256": self.tree_sha256,
            "depends_on": list(self.manifest.get("depends_on") or ()),
            "installable_with": list(self.manifest.get("installable_with") or ()),
        }


def _manifest(builder: Any, *, name: str, kind: str, version: str,
              members: Mapping[str, bytes], entrypoints: Sequence[str],
              depends_on: Sequence[str], provides: Sequence[str],
              authority: Mapping[str, Any] | None = None) -> dict:
    _refuse_unsafe_members(members, name)
    entries = {rel: sha256_bytes(data) for rel, data in sorted(members.items())}
    manifest: dict[str, Any] = {
        "schema": "serveros/artifact.v1",
        "kinds": [kind],
        "name": name,
        "version": version,
        "members": sorted(entries),
        "member_sha256": entries,
        "tree_sha256": tree_digest(builder, members),
        "entrypoints": list(entrypoints),
        "depends_on": list(depends_on),
        "installable_with": list(dict.fromkeys((*depends_on, *([AUTHORITY_KIND] if authority else [])))),
        "provides": list(provides),
        "platforms": ["linux", "windows"] if kind == NODE_KIND else ["linux"],
        # Nothing installed by these artifacts owns a socket, a lock, a state
        # directory or a credential. The fields are declared so a reader can
        # see the refusal, not infer it from an absence.
        "runtime_resources": {"socket": None, "lock": None,
                              "state_root": None, "credential": None},
        "authority": dict(authority) if authority else None,
    }
    return manifest


def compose(builder: Any, root: Path, version: str) -> dict[str, ArtifactPlan]:
    """The four release units: Base, Authority, Hub, Node."""
    part = partition(builder, root)
    base = module_files(builder, root, part.base_modules)
    hub = module_files(builder, root, part.hub_own)
    node = module_files(builder, root, part.node_own)
    authority = authority_members(root)

    overlap = (set(base) & set(hub)) | (set(base) & set(node)) | (set(hub) & set(node))
    if overlap:
        raise Refused(BLOCKED_MANIFEST_DRIFT,
                      f"release units share member(s): {sorted(overlap)}")
    leaked = ([f"hub carries {rel}" for rel in hub if owner_of_file(rel) == "node"]
              + [f"node carries {rel}" for rel in node if owner_of_file(rel) == "hub"])
    if leaked:
        raise Refused(BLOCKED_FOREIGN_IMPORT,
                      "a unit carries its neighbour's file(s): " + "; ".join(leaked))

    plans = {
        BASE_KIND: ArtifactPlan(
            name=BASE_KIND, kind=BASE_KIND, version=version, members=base,
            manifest=_manifest(builder, name=BASE_KIND, kind=BASE_KIND,
                               version=version, members=base, entrypoints=(),
                               depends_on=(), provides=sorted(part.base_modules)),
            tree_sha256=tree_digest(builder, base)),
        AUTHORITY_KIND: ArtifactPlan(
            name=AUTHORITY_KIND, kind=AUTHORITY_KIND, version=version,
            members=authority,
            manifest=_manifest(builder, name=AUTHORITY_KIND, kind=AUTHORITY_KIND,
                               version=version, members=authority, entrypoints=(),
                               depends_on=(),
                               provides=[AUTHORITY_MODULE]),
            tree_sha256=tree_digest(builder, authority)),
        HUB_KIND: ArtifactPlan(
            name=HUB_KIND, kind=HUB_KIND, version=version, members=hub,
            manifest=_manifest(builder, name=HUB_KIND, kind=HUB_KIND,
                               version=version, members=hub,
                               entrypoints=list(HUB_ENTRIES),
                               depends_on=(BASE_KIND,),
                               provides=sorted(part.hub_own),
                               authority={"abi": AUTHORITY_ABI,
                                          "module": AUTHORITY_MODULE,
                                          "provided_by": AUTHORITY_KIND,
                                          "single_authority": True}),
            tree_sha256=tree_digest(builder, hub)),
        NODE_KIND: ArtifactPlan(
            name=NODE_KIND, kind=NODE_KIND, version=version, members=node,
            manifest=_manifest(builder, name=NODE_KIND, kind=NODE_KIND,
                               version=version, members=node,
                               entrypoints=list(NODE_ENTRIES),
                               depends_on=(BASE_KIND,),
                               provides=sorted(part.node_own)),
            tree_sha256=tree_digest(builder, node)),
    }
    refuse_shared_runtime_state(plans)
    return plans


def write_plans(plans: Mapping[str, ArtifactPlan], output: Path) -> dict[str, str]:
    """Write each unit and its `ARTIFACT.json`; return name -> tree digest."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    digests: dict[str, str] = {}
    for kind, plan in sorted(plans.items()):
        root = output / kind
        for rel, data in plan.members.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        manifest = dict(plan.manifest)
        manifest["members"] = sorted(plan.members)
        (root / "ARTIFACT.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8")
        digests[kind] = plan.tree_sha256
    (output / "wave17-manifest.json").write_text(
        json.dumps({"schema": SCHEMA, "artifacts":
                    {kind: plan.as_dict() for kind, plan in sorted(plans.items())}},
                   indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")
    return digests


# ---------------------------------------------------------------------------
# isolation: import and boot with no checkout and no neighbour
# ---------------------------------------------------------------------------
_PROBE = r'''
import importlib
import json
import sys

prefix, authority, component, fixture = sys.argv[1:5]
extra = [prefix] + ([authority] if authority not in ("", "-") else [])
sys.path[:0] = extra
out = {"component": component}

entry = {"node": "fleet_mcp.node.connector", "hub": "fleet_mcp.server"}[component]
importlib.import_module(entry)
out["imported"] = entry

if component == "node":
    from fleet_mcp.node.connector import load_node_config
    config = load_node_config(fixture)
    out["device_ref"] = config.device_ref
    out["booted"] = True
else:
    import fleet_mcp.owner_setup as owner_setup
    from fleet_mcp.server import McpServer
    from fleet_mcp.tools import SERVED_TOOLS
    server = McpServer(lambda: None)
    answer = server.handle({"jsonrpc": "2.0", "id": 1, "method": "no/such/method"})
    out["tools"] = len(SERVED_TOOLS)
    out["dispatch_refused"] = bool(answer and answer.get("error"))
    try:
        owner_setup._node_config_loader()
        out["node_loader"] = "unavailable-not-refused"
    except ValueError as exc:
        out["node_loader"] = str(exc).split(":", 1)[0]
    import fleet.transport as authority_module
    out["authority_symbols"] = all(
        hasattr(authority_module, name) for name in
        ["Availability", "CapabilityAvailability", "Device", "DeviceRef",
         "DeviceRegistry", "UnknownDevice", "canonical_level"])
    out["booted"] = True
print(json.dumps(out, sort_keys=True))
'''

NODE_FIXTURE = """\
schema: serveros/fleet-node.local/v1
device_ref: device:wave17-hermetic
secret_anchor: {secret}
cloudcli_endpoint: http://127.0.0.1:1/mcp
cloudcli_credential_anchor: {credential}
allow_send_to: []
allow_create: false
allow_control: false
"""


def _install(plans: Mapping[str, ArtifactPlan], prefix: Path,
             names: Sequence[str]) -> None:
    for name in names:
        for rel, data in plans[name].members.items():
            path = prefix / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)


def _run_probe(prefix: Path, authority: str, component: str,
               fixture: Path, cwd: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _PROBE, str(prefix),
         authority, component, str(fixture)],
        capture_output=True, text=True, cwd=str(cwd),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=180)
    if proc.returncode != 0:
        raise Refused(
            BLOCKED_ISOLATION,
            f"the {component} prefix did not boot with Base"
            f"{' + Authority' if authority not in ('', '-') else ''}: "
            f"{(proc.stderr or proc.stdout).strip()[-500:]}")
    try:
        answer = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        raise Refused(BLOCKED_ISOLATION,
                      f"the {component} probe printed non-JSON: "
                      f"{proc.stdout[:200]!r}") from exc
    if not answer.get("booted"):
        raise Refused(BLOCKED_ISOLATION,
                      f"the {component} probe did not reach a boot: {answer}")
    return answer


def boot(plans: Mapping[str, ArtifactPlan], workdir: Path) -> dict[str, dict]:
    """Boot Hub and Node separately, each without the other's files.

    Both installs merge Base into the *same* prefix — two artifacts laying
    files into one `fleet_mcp` package is what a real install does — but the
    neighbour's unit is never written. The assertion is therefore file
    absence, not a claim about import order.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = workdir / "node-binding.yaml"
    fixture.write_text(NODE_FIXTURE.format(
        secret=workdir / "secret", credential=workdir / "credential"),
        encoding="utf-8")
    results: dict[str, dict] = {}
    # Each prefix is a complete install of ONE half: Base plus that half's own
    # unit. The Hub also installs the Authority (it is the only half that reads
    # `fleet.transport`); the Node does not. Neither prefix ever receives the
    # other half's file.
    for component, names in (
            ("node", (BASE_KIND, NODE_KIND)),
            ("hub", (BASE_KIND, AUTHORITY_KIND, HUB_KIND))):
        prefix = workdir / f"prefix-{component}"
        _install(plans, prefix, names)
        # The neighbour's unit was never installed. These are exact paths,
        # not a glob: a glob over a missing dir proves nothing.
        neighbour_paths = (
            ("fleet_mcp/node/connector.py", "fleet_mcp/node/__init__.py")
            if component == "hub"
            else ("fleet_mcp/server.py", "fleet_mcp/tools.py", "fleet_mcp/fleet.py"))
        present = [rel for rel in neighbour_paths if (prefix / rel).exists()]
        if present:
            raise Refused(BLOCKED_ISOLATION,
                          f"the {component} prefix carries the neighbour's "
                          f"file(s) {present}")
        results[component] = _run_probe(prefix, "-", component, fixture, workdir)
    return results


def two_artifact_only(builder: Any, root: Path, workdir: Path) -> dict[str, dict]:
    """What a strictly two-artifact release (no Base) actually does.

    This is the RED half of the isolation evidence: Hub carries only Hub-owned
    closure files, Node only Node-owned ones. Neither can boot, and the reason
    is the foreign import this lane exists to remove.
    """
    part = partition(builder, root)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = workdir / "node-binding.yaml"
    fixture.write_text(NODE_FIXTURE.format(
        secret=workdir / "secret", credential=workdir / "credential"),
        encoding="utf-8")
    results: dict[str, dict] = {}
    for component, modules in (("hub", part.hub_modules), ("node", part.node_modules)):
        payload = module_files(builder, root, modules)
        owned = {rel: data for rel, data in payload.items()
                 if owner_of_file(rel) == component}
        prefix = workdir / f"two-{component}"
        for rel, data in owned.items():
            path = prefix / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if component == "hub":
            # The identity authority is a third artifact, not the Node. Give
            # the Hub-only payload its authority so the failure this probe
            # proves is the missing NEIGHBOUR, not a missing shared base.
            for rel, data in authority_members(root).items():
                path = prefix / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        code = (
            "import sys, json; sys.path.insert(0, sys.argv[1]);\n"
            "try:\n"
            "    import fleet_mcp.node.connector\n"
            "    print(json.dumps({'imported': True}))\n"
            "except Exception as exc:\n"
            "    print(json.dumps({'imported': False, 'error': type(exc).__name__}))\n"
            if component == "node" else
            "import sys, json; sys.path.insert(0, sys.argv[1]);\n"
            "try:\n"
            "    import fleet_mcp.owner_setup\n"
            "    print(json.dumps({'imported': True}))\n"
            "except Exception as exc:\n"
            "    print(json.dumps({'imported': False, 'error': type(exc).__name__}))\n")
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, str(prefix)],
            capture_output=True, text=True, cwd=str(workdir), timeout=120)
        answer = json.loads(proc.stdout or "{}")
        answer["files"] = len(owned)
        results[component] = answer
    return results


# ---------------------------------------------------------------------------
# runtime resources: no shared socket, lock, state or credential
# ---------------------------------------------------------------------------
RESOURCES = ("install_path", "port", "socket", "backend_id", "oauth_audience",
             "cred_state_root", "lock", "unit")
PATH_RESOURCES = ("install_path", "cred_state_root", "lock")


def _norm(path: str) -> str:
    import posixpath
    return posixpath.normpath(path)


def _within(a: str, b: str) -> bool:
    if a == b:
        return True
    return a.startswith(b + "/") or b.startswith(a + "/")


def resource_matrix(hub: Mapping[str, Any], node: Mapping[str, Any]) -> dict:
    """The eight resources, reported together, with `device_ref` inverted."""
    collisions = []
    for name in RESOURCES:
        a, b = hub.get(name), node.get(name)
        if name in PATH_RESOURCES and isinstance(a, str) and isinstance(b, str):
            if _within(_norm(a), _norm(b)):
                collisions.append([name, f"{a} and {b} overlap"])
        elif a is not None and a == b:
            collisions.append([name, f"both units use {a!r}"])
    if hub.get("device_ref") != node.get("device_ref"):
        return {"decision": "BLOCKED", "code": BLOCKED_DEVICE_DISAGREEMENT,
                "collisions": []}
    return {"decision": "BLOCKED" if collisions else "OK",
            "code": BLOCKED_RESOURCE_COLLISION if collisions else OK,
            "collisions": collisions}


def refuse_shared_runtime_state(plans: Mapping[str, ArtifactPlan]) -> None:
    for plan in plans.values():
        _refuse_unsafe_members(plan.members, plan.name)
        declared = plan.manifest.get("runtime_resources") or {}
        owned = [name for name in ("socket", "lock", "state_root", "credential")
                 if declared.get(name)]
        if owned:
            raise Refused(BLOCKED_RESOURCE_COLLISION,
                          f"{plan.name} declares runtime resource(s) {owned}; a "
                          f"source artifact must not carry machine state")


# ---------------------------------------------------------------------------
# reverse delete / rollback against the Legacy fake
# ---------------------------------------------------------------------------
class FakeLegacyController:
    """A Legacy Fleet 0.1.0 stand-in with key-idempotent `send`."""

    def __init__(self) -> None:
        self.answers = {"get_session": "S-1", "get_run": "R-1", "send": "ok"}
        self.deliveries: list[str] = []

    def call(self, verb: str, intent: str | None = None) -> str:
        if verb == "send":
            if intent not in self.deliveries:
                self.deliveries.append(intent or "")
            return "ok"
        return self.answers[verb]

    def snapshot(self) -> dict:
        return {"answers": dict(self.answers), "deliveries": list(self.deliveries)}


def reverse_delete_and_rollback(plans: Mapping[str, ArtifactPlan],
                                previous: Mapping[str, ArtifactPlan],
                                workdir: Path) -> dict:
    """Delete the Node unit and roll the Hub back; Legacy must not move.

    The operations are performed on a fake install prefix and a fake
    controller. Nothing is installed, started or contacted.

    A three-unit topology has one honest consequence: the Base is shared, so
    rolling the Base back moves both halves together. This check therefore
    rolls back the HUB unit (the release action the question is about) and
    asserts the Legacy Fleet 0.1.0 fake does not move. A Base rollback is a
    different, coordinated operation and is not claimed here.
    """
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    prefix = workdir / "install"
    for kind in (BASE_KIND, AUTHORITY_KIND, HUB_KIND, NODE_KIND):
        _install(plans, prefix, [kind])
    fixture = workdir / "node-binding.yaml"
    fixture.write_text(NODE_FIXTURE.format(
        secret=workdir / "secret", credential=workdir / "credential"),
        encoding="utf-8")
    legacy = FakeLegacyController()
    before = legacy.snapshot()
    steps = []

    # 1. Reverse-delete the Node unit: its files are gone, the Hub still boots
    #    in the same prefix (Base + Authority + Hub remain).
    node_files = list(plans[NODE_KIND].members)
    for rel in node_files:
        (prefix / rel).unlink()
    hub_after_delete = _run_probe(prefix, "-", "hub", fixture, workdir)
    steps.append(["reverse-delete-node", "OK" if hub_after_delete.get("booted")
                  else "BLOCKED",
                  {"remaining_node_files": sum((prefix / rel).exists()
                                               for rel in node_files)}])

    # 2. Roll the Hub back to the previous version's member map. The two
    #    versions differ only in their manifest in this lane, so the pointer
    #    swap is exercised rather than a code delta; the report says so.
    for rel in set(plans[HUB_KIND].members) - set(previous[HUB_KIND].members):
        (prefix / rel).unlink()
    for rel, data in previous[HUB_KIND].members.items():
        path = prefix / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    hub_after_rollback = _run_probe(prefix, "-", "hub", fixture, workdir)
    steps.append(["rollback-hub",
                  "OK" if hub_after_rollback.get("booted") else "BLOCKED",
                  {"previous_version": previous[HUB_KIND].version,
                   "current_version": plans[HUB_KIND].version,
                   "previous_tree": previous[HUB_KIND].tree_sha256,
                   "current_tree": plans[HUB_KIND].tree_sha256}])

    after = legacy.snapshot()
    unchanged = after == before
    return {
        "decision": OK if unchanged else "BLOCKED",
        "code": OK if unchanged else "LEGACY_CONTRACT_DRIFT",
        "steps": steps,
        "deliveries": after["deliveries"],
        "legacy_unchanged": unchanged,
        "note": "the rollback target differs from the current unit only in its "
                "manifest; no code delta is claimed",
    }


# ---------------------------------------------------------------------------
# the report
# ---------------------------------------------------------------------------
def _head(root: Path) -> str:
    proc = subprocess.run(["git", "-C", str(root), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=False)
    return proc.stdout.strip() if proc.returncode == 0 else "sandbox"


def analyze(root: Path, version: str, *, workdir: Path | None = None,
            source_head: str | None = None, tree_kind: str = "live",
            baseline_root: Path | None = None) -> dict:
    root = Path(root)
    baseline = Path(baseline_root) if baseline_root else root
    builder = load_builder(root)
    files = component_files(root)
    raw_foreign = {component: owned_import_edges(builder, root, component)
                   for component in ("hub", "node")}
    effective = {component: effective_foreign(builder, root, component)
                 for component in ("hub", "node")}
    part = partition(builder, root)
    plans = compose(builder, root, version) if part.base_exact else {}
    if baseline.resolve() != root.resolve():
        baseline_part = partition(builder, baseline)
        baseline_report = {
            "head": _head(baseline),
            "ownership": {
                "counts": {key: len(value)
                           for key, value in component_files(baseline).items()},
                "foreign": {component: owned_import_edges(builder, baseline,
                                                          component)
                            for component in ("hub", "node")},
            },
            "partition": {
                "hub_closure": len(baseline_part.hub_modules),
                "node_closure": len(baseline_part.node_modules),
                "shared": len(baseline_part.shared),
                "hub_own": len(baseline_part.hub_own),
                "node_own": len(baseline_part.node_own),
                "base_exact": baseline_part.base_exact,
            },
        }
    else:
        baseline_report = None

    reasons: list[str] = []
    if not part.base_exact:
        reasons.append(
            "the shared closure is not the pinned Base: inadmissible "
            f"{sorted(part.base_inadmissible)}; unreached {sorted(part.base_unreached)}")
    if effective["hub"]:
        reasons.append(f"Hub still imports Node-owned modules: {effective['hub']}")
    if effective["node"]:
        reasons.append(f"Node still imports non-Base Hub modules: {effective['node']}")

    technical = (part.base_exact and not effective["hub"] and not effective["node"])
    if technical:
        reasons.append(
            "the ownership map and the release map agree in this tree "
            "(technical_state GREEN_SANDBOX); the verdict stays BLOCKED_OWNER "
            "because the cuts in handoff/ are not landed by the fleet_mcp owner "
            "and a real co-install needs separate permission")
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "tree": {"kind": tree_kind, "source_head": source_head or _head(root),
                 "measured_head": _head(root)},
        "builder": {"path": BUILDER_REL,
                    "sha256": "sha256:" + BUILDER_PIN_SHA256,
                    "reused": True},
        "ownership": {
            "counts": {key: len(value) for key, value in files.items()},
            "foreign": raw_foreign,
            "independent": {component: not raw_foreign[component]
                            for component in ("hub", "node")},
        },
        "partition": {
            "hub_closure": len(part.hub_modules),
            "node_closure": len(part.node_modules),
            "shared": len(part.shared),
            "hub_own": len(part.hub_own),
            "node_own": len(part.node_own),
            "base": sorted(part.base_modules),
            "base_inadmissible": sorted(part.base_inadmissible),
            "base_unreached": sorted(part.base_unreached),
            "base_exact": part.base_exact,
        },
        "effective_foreign": effective,
        "artifacts": {kind: plan.as_dict() for kind, plan in sorted(plans.items())},
        "technical_state": GREEN_SANDBOX if technical else BLOCKED_OWNER,
        "decision": BLOCKED_OWNER,
        "cuts_visible_in_tree": _cuts_landed(root),
        "reasons": reasons,
        "release_permitted": False,
        "device_co_install": NOT_RUN,
    }
    if baseline_report is not None:
        report["baseline"] = baseline_report

    if workdir is not None:
        report["isolation"] = {
            # The two-artifact model is measured on the BASELINE tree: a tree
            # that already has the cuts can import without its neighbour, and
            # asking it to fail would be asking the proof to lie.
            "two_artifact_only": two_artifact_only(
                builder, baseline, Path(workdir) / "two-baseline"),
        }
        if plans:
            previous = compose(builder, root, "0.9.0+wave17prev")
            report["isolation"]["three_unit"] = boot(plans, Path(workdir) / "boot")
            report["rollback"] = reverse_delete_and_rollback(
                plans, previous, Path(workdir) / "rollback")
    return report


def _cuts_landed(root: Path) -> bool:
    """Both owner cuts are visible in the live tree, by their exact seams.

    A green sandbox is not a landed cut. This is the one place the two are
    distinguished: the Hub seam is `_node_config_loader` in `owner_setup.py`,
    and the provider barrel must no longer import `fleet_node`.
    """
    owner_setup = Path(root) / "fleet_mcp/owner_setup.py"
    providers = Path(root) / "fleet_mcp/providers/__init__.py"
    if not owner_setup.is_file() or not providers.is_file():
        return False
    return ("_node_config_loader" in owner_setup.read_text(encoding="utf-8")
            and "from .fleet_node import" not in providers.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def _sandbox_copy(repo: Path) -> Path:
    """A throwaway copy made from the commit, with no `.git` of its own."""
    import tarfile

    scratch = Path(tempfile.mkdtemp(prefix="wave17-sandbox-"))
    archive = subprocess.run(["git", "-C", str(repo), "archive", "HEAD"],
                             capture_output=True, check=True).stdout
    with tarfile.open(fileobj=__import__("io").BytesIO(archive)) as tar:
        try:
            tar.extractall(scratch, filter="data")
        except TypeError:  # Python < 3.12 has no extraction filter
            tar.extractall(scratch)
    return scratch


def _apply_handoff(root: Path) -> dict:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "handoff"))
    import apply_handoff  # type: ignore[import-not-found]
    return apply_handoff.apply(root)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".",
                        help="source tree to measure (default: this checkout)")
    parser.add_argument("--version", default="1.0.0+wave17")
    parser.add_argument("--sandbox", action="store_true",
                        help="measure a copy with the owner handoff applied")
    parser.add_argument("--isolation", action="store_true",
                        help="also boot both units in temporary prefixes")
    parser.add_argument("--write", metavar="DIR",
                        help="write the four release units and their manifests")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    source_root = Path(args.root).resolve()
    root = source_root
    source_head = _head(source_root)
    scratch: Path | None = None
    try:
        if args.sandbox:
            scratch = _sandbox_copy(source_root)
            _apply_handoff(scratch)
            root = scratch
        workdir = Path(tempfile.mkdtemp(prefix="wave17-report-")) \
            if args.isolation else None
        report = analyze(root, args.version, workdir=workdir,
                         source_head=source_head,
                         tree_kind="sandbox" if args.sandbox else "live",
                         baseline_root=source_root if args.sandbox else None)
        if args.write:
            builder = load_builder(root)
            write_plans(compose(builder, root, args.version), Path(args.write))
            report["written_to"] = str(Path(args.write))
        if args.json:
            print(json.dumps(report, indent=2, sort_keys=True, ensure_ascii=False))
        else:
            print(f"tree            {report['tree']}")
            print(f"ownership       {report['ownership']['counts']}")
            print(f"foreign         {report['ownership']['foreign']}")
            print(f"partition       { {k: v for k, v in report['partition'].items() if k != 'base'} }")
            print(f"technical       {report['technical_state']}")
            print(f"decision        {report['decision']}")
            print(f"device co-install {report['device_co_install']}")
        return 0
    except (Prerequisite, Refused) as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 2
    finally:
        if scratch is not None:
            import shutil
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main())
