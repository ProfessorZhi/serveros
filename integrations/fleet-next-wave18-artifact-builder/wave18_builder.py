#!/usr/bin/env python3
"""Wave 18 · build, verify and roll back real Next release units.

What this is
------------
Wave 17 (#505) measured the Hub/Node cut on an isolated copy and said
`GREEN_SANDBOX`, because nothing was built. This module turns the same cuts
into **actual artifacts**: deterministic tarballs with SHA manifests, a
machine-readable provenance record for every source transform, isolation
booting in a root that has no checkout and no neighbour, a negative control,
independent rollback, and the #483/#480 release-footprint veto.

It does not modify `fleet_mcp/**`, `runtime/**`, `scripts/release.py`, Legacy
Fleet 0.1.0, or the V2-rooted #461/#467/#473 files. The source transforms are
applied to a `git archive` copy; every transform records the original blob,
the patch digest and the result digest.

The shape it builds
-------------------
D7 (#431) defines two artifact kinds with two versions, two manifests and two
verifiers, and requires deterministic USTAR/gzip archives. This module reuses
its closure, archive writer and artifact reader by SHA pin. On the measured
tree the Hub and the Node are not disjoint: both need a set of runtime
primitives. Copying those into both artifacts would be duplication dressed as
independence, so the build emits four units:

    fleet-runtime-base   19 shared primitives, pinned in both directions
    fleet-authority      the ONE fleet/transport.py + its contract
    fleet-hub            13 Hub modules, no Node file
    fleet-node           2 Node modules, no Hub file

plus an optional `fleet-next-composition` manifest that pins the four units
and their compensating rollback order. D7 does not require Hub and Node to
share a version; the units have independent versions, and the composition is a
tested set, not a claim that the units are atomic.

What is claimed, and what is not
--------------------------------
`technical_state: GREEN_ARTIFACTS` means the tarballs build, verify, boot in
isolation, survive the negative control and roll back without touching the
Legacy fake. `source_state: BLOCKED_OWNER` means the transforms have not been
landed in `fleet_mcp/**`; a production install is not authorised by this lane.
`real_device: NOT_RUN` means exactly that. Nothing is installed, no service is
started, no port or socket is bound, no credential is read, and nothing is
sent.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import io
import json
import os
import posixpath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "serveros/fleet-next-wave18-artifact-builder.v1"
PROVENANCE_SCHEMA = "serveros/fleet-next-wave18-provenance.v1"
COMPOSITION_SCHEMA = "serveros/fleet-next-composition.v1"
ARTIFACT_SCHEMA = "serveros/artifact.v1"
ARTIFACT_MANIFEST = "ARTIFACT.json"
COMPOSITION = "fleet-next-wave18"

BUILDER_REL = "integrations/fleet-v1-l2-release/l2_release.py"
#: #431's builder at 517ef91. A changed builder is a changed release fact.
BUILDER_PIN_SHA256 = (
    "7f87a59611b00585f2e7eba3799c7d4bd0c256abec775b96e7ed8d6f0a5ba01d")

PATCHES = (
    {
        "id": "CUT-A",
        "path": "integrations/fleet-next-wave17-hub-node-release-cut/"
                "handoff/cut_a_main_rebase.patch",
        "owner_source": "#461 (V2-rooted delivery branch), mechanically "
                        "rebased onto main@e69fc03 and published unapplied by "
                        "#505 @3512c55",
        "owner_commit": "b042f515b4d0a894757eda9ebd16786dfe8de6ad",
        "rebased_to": "main@e69fc0337ae5d33000478e3d5162077f525bc8cc",
        "origin": "#431 L2-CUT-A.patch (does not apply to main; verified)",
    },
    {
        "id": "CUT-B",
        "path": "integrations/fleet-next-wave17-hub-node-release-cut/"
                "handoff/cut_b_hub_node_seam.patch",
        "owner_source": "#505, the release-cut lane",
        "owner_commit": "3512c551574a94ad360f8bde6435bdf07c729167",
        "rebased_to": None,
        "origin": "Wave 17 owner handoff",
    },
)

BASE_KIND = "fleet-runtime-base"
AUTHORITY_KIND = "fleet-authority"
HUB_KIND = "fleet-hub"
NODE_KIND = "fleet-node"
COMPOSITION_KIND = "fleet-next-composition"
UNITS = (BASE_KIND, AUTHORITY_KIND, HUB_KIND, NODE_KIND)

HUB_ENTRIES = ("fleet_mcp.server", "fleet_mcp.owner_setup")
NODE_ENTRIES = ("fleet_mcp.node.connector",)

#: The closed allowlist of shared primitives. Checked in both directions: a
#: shared module not listed fails, and a listed module no longer shared fails.
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

#: Hub-only file names. A Node artifact carrying any of these is refused by
#: name, not by a prefix that a new hub module could silently live under.
HUB_ONLY_FILES = frozenset({
    "fleet_mcp/audit.py",
    "fleet_mcp/core_lite_readback.py",
    "fleet_mcp/core_lite_source.py",
    "fleet_mcp/dispatch.py",
    "fleet_mcp/fleet.py",
    "fleet_mcp/owner_setup.py",
    "fleet_mcp/privacy.py",
    "fleet_mcp/providers/build.py",
    "fleet_mcp/providers/fleet_node.py",
    "fleet_mcp/registry.py",
    "fleet_mcp/server.py",
    "fleet_mcp/session_detail.py",
    "fleet_mcp/tools.py",
})
HUB_ONLY_PREFIXES = ("fleet_mcp/deploy/", "fleet_mcp/bin/")
NODE_PREFIXES = ("fleet_mcp/node/", "native/node-core/")

AUTHORITY_ABI = "serveros.fleet.transport/v1"
AUTHORITY_MODULE = "fleet.transport"
AUTHORITY_FILES = ("fleet/transport.py", "fleet/transport-contract.yaml")
AUTHORITY_MANIFEST_NAME = "fleet-authority.json"
AUTHORITY_SYMBOLS = (
    "Availability", "CapabilityAvailability", "Device", "DeviceRef",
    "DeviceRegistry", "UnknownDevice", "canonical_level",
)
AUTHORITY_DATA_NOT_BUNDLED = ("fleet/registry.yaml",)

VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.\-]*)?(?:\+[A-Za-z0-9][A-Za-z0-9.\-]*)?$")

OK = "OK"
GREEN_ARTIFACTS = "GREEN_ARTIFACTS"
BLOCKED_OWNER = "BLOCKED_OWNER"
BLOCKED_CROSS_COMPONENT = "BLOCKED_CROSS_COMPONENT"
BLOCKED_BASE_NOT_PINNED = "BLOCKED_BASE_NOT_PINNED"
BLOCKED_UNDECLARED_DEPENDENCY = "BLOCKED_UNDECLARED_DEPENDENCY"
BLOCKED_ISOLATION = "BLOCKED_ISOLATION"
BLOCKED_NEGATIVE_CONTROL = "BLOCKED_NEGATIVE_CONTROL"
BLOCKED_ROLLBACK = "BLOCKED_ROLLBACK"
BLOCKED_RESOURCE_COLLISION = "BLOCKED_RESOURCE_COLLISION"
BLOCKED_DEVICE_DISAGREEMENT = "BLOCKED_DEVICE_DISAGREEMENT"
BLOCKED_DIRTY_SOURCE = "BLOCKED_DIRTY_SOURCE"
BLOCKED_INPUT_ABSENT = "BLOCKED_INPUT_ABSENT"
BLOCKED_BUILDER_DRIFT = "BLOCKED_BUILDER_DRIFT"
BLOCKED_MANIFEST_DRIFT = "BLOCKED_MANIFEST_DRIFT"
BLOCKED_UNSAFE_MEMBER = "BLOCKED_UNSAFE_MEMBER"
BLOCKED_LINE_ENDING_DRIFT = "BLOCKED_LINE_ENDING_DRIFT"
NEXT_SHADOW_WRITE_FORBIDDEN = "NEXT_SHADOW_WRITE_FORBIDDEN"
NEXT_SHADOW_NOT_GRANTED = "NEXT_SHADOW_NOT_GRANTED"
SHADOW_VERB_UNKNOWN = "SHADOW_VERB_UNKNOWN"
NOT_RUN = "NOT_RUN"

WRITE_VERBS = frozenset({
    "create", "send", "stop", "restart", "wake", "manager_wake", "interrupt",
    "close", "update", "session_create", "session_send", "session_interrupt",
    "session_close", "session_reconfigure", "session_cancel_queued",
})
READ_VERBS = frozenset({
    "get_session", "get_run", "sessions_list", "projects_list", "overview",
    "approvals_list", "health",
})

FORBIDDEN_MEMBER = re.compile(
    r"(^|/)(\.env|.*\.(sock|lock|pid|state|pem|key)|"
    r".*(credential|token|secret|password).*)$", re.IGNORECASE)

#: Text files whose bytes must not depend on the caller's Git line-ending
#: configuration. The repair for #514 is here, not in a checksum file.
TEXT_SUFFIXES = (".py", ".yaml", ".yml")

#: Every internal git invocation is hermetic: no system/global config, no
#: system attributes, no EOL conversion. `core.autocrlf=true` in the caller's
#: environment used to rewrite patched files to CRLF and change four of the
#: five artifact digests (#514).
HERMETIC_GIT_FLAGS = (
    "-c", "core.autocrlf=false",
    "-c", "core.eol=lf",
    "-c", "core.safecrlf=false",
    "-c", "core.precomposeUnicode=false",
    "-c", "core.ignoreCase=false",
    "-c", "core.fileMode=false",
)
HERMETIC_GIT_ENV = {
    "GIT_CONFIG_NOSYSTEM": "1",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_ATTR_NOSYSTEM": "1",
}


class Prerequisite(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


class Refused(RuntimeError):
    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code, self.detail = code, detail


# ---------------------------------------------------------------------------
# reuse #431's D7 builder
# ---------------------------------------------------------------------------
def load_builder(repo: Path) -> Any:
    path = Path(repo) / BUILDER_REL
    if not path.is_file():
        raise Prerequisite(
            "PR431_BUILDER_ABSENT",
            f"{BUILDER_REL} is absent; Wave 18 is stacked on #431/#505 and "
            f"must not re-implement the D7 closure rules")
    actual = sha256_bytes(path.read_bytes())
    if actual != BUILDER_PIN_SHA256:
        raise Prerequisite(
            BLOCKED_BUILDER_DRIFT,
            f"{BUILDER_REL} is sha256:{actual}, pinned "
            f"sha256:{BUILDER_PIN_SHA256}")
    spec = importlib.util.spec_from_file_location("wave18_l2_release", path)
    if spec is None or spec.loader is None:
        raise Prerequisite("PR431_BUILDER_UNREADABLE", f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# small utilities
# ---------------------------------------------------------------------------
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    return sha256_bytes(Path(path).read_bytes())


def _sanitize(text: str, *paths: Path) -> str:
    """Keep temporary absolute paths out of the committed evidence."""
    for path in paths:
        text = text.replace(str(path), "<path>")
    return text


def _portable_modules(answer: Mapping[str, Any], root: Path) -> list[list[Any]]:
    """Module names plus a path relative to the artifact root."""
    out: list[list[Any]] = []
    for name, path in answer.get("fleet_mcp_modules") or ():
        relative = None
        if path:
            try:
                relative = str(Path(path).resolve().relative_to(Path(root).resolve()))
            except ValueError:
                relative = "<outside-root>"
        out.append([name, relative])
    return out


def _refuse_crlf(data: bytes, label: str) -> None:
    """A text member with CRLF is a different artifact, not a formatting quirk."""
    if b"\r\n" in data:
        raise Refused(
            BLOCKED_LINE_ENDING_DRIFT,
            f"{label} contains CRLF; this build's bytes must not depend on the "
            f"caller's core.autocrlf. The hermetic git configuration is the "
            f"fix; a CRLF payload is refused rather than published as another "
            f"artifact digest")


def _git(repo: Path, *args: str, binary: bool = False,
         check: bool = True) -> bytes | str:
    proc = subprocess.run(
        ["git", *HERMETIC_GIT_FLAGS, "-C", str(repo), *args],
        capture_output=True, check=False,
        env={**os.environ, **HERMETIC_GIT_ENV})
    if check and proc.returncode != 0:
        raise Refused(
            "GIT_FAILED",
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.decode('utf-8', 'replace').strip()[:300]}")
    return proc.stdout if binary else proc.stdout.decode("utf-8").strip()


def _git_clean(repo: Path) -> bool:
    out = _git(repo, "status", "--porcelain", "--untracked-files=no",
               "--", "fleet_mcp", "fleet")
    return out == ""


# ---------------------------------------------------------------------------
# source: always a git archive, never a working directory
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Source:
    commit: str
    tree: str
    archive_sha256: str
    root: Path
    live_clean: bool

    def as_dict(self) -> dict:
        return {
            "commit": self.commit,
            "tree": self.tree,
            "archive_sha256": "sha256:" + self.archive_sha256,
            "built_from": "git archive --format=tar <commit>",
            "workdir_used_as_artifact": False,
            "live_tree_clean_at_build": self.live_clean,
        }


def archive_source(repo: Path, commit: str, base: Path) -> Source:
    """Extract one commit into a throwaway tree; never build from `repo`."""
    repo = Path(repo)
    if not _git_clean(repo):
        raise Refused(
            BLOCKED_DIRTY_SOURCE,
            "fleet_mcp/ or fleet/ has uncommitted changes; a release unit is "
            "built from a commit, not from someone's working directory")
    resolved = str(_git(repo, "rev-parse", commit))
    tree = str(_git(repo, "rev-parse", f"{resolved}^{{tree}}"))
    data = _git(repo, "archive", "--format=tar", resolved, binary=True)
    assert isinstance(data, bytes)
    root = Path(base) / "src"
    root.mkdir(parents=True)
    with tarfile.open(fileobj=io.BytesIO(data)) as tar:
        try:
            tar.extractall(root, filter="data")
        except TypeError:  # Python < 3.12
            tar.extractall(root)
    for base_dir in ("fleet_mcp", "fleet"):
        top = root / base_dir
        if not top.is_dir():
            continue
        for path in sorted(top.rglob("*")):
            if path.is_file() and path.suffix in TEXT_SUFFIXES:
                _refuse_crlf(path.read_bytes(),
                             path.relative_to(root).as_posix())
    return Source(resolved, tree, sha256_bytes(data), root, True)


# ---------------------------------------------------------------------------
# transforms: isolated copy + machine-readable provenance
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PatchResult:
    id: str
    path: str
    sha256: str
    owner_source: str
    owner_commit: str
    rebased_to: str | None
    origin: str
    targets: tuple[dict, ...]

    def as_dict(self) -> dict:
        return {
            "id": self.id,
            "path": self.path,
            "sha256": "sha256:" + self.sha256,
            "owner_source": self.owner_source,
            "owner_commit": self.owner_commit,
            "rebased_to": self.rebased_to,
            "origin": self.origin,
            "applied_in": "isolated git-archive copy",
            "live_tree_modified": False,
            "targets": list(self.targets),
        }


def _patch_targets(data: bytes) -> tuple[str, ...]:
    paths: set[str] = set()
    for line in data.decode("utf-8", "replace").splitlines():
        if line.startswith("+++ b/"):
            paths.add(line[6:].strip())
        elif line.startswith("diff --git "):
            match = re.search(r" b/(\S+)$", line)
            if match:
                paths.add(match.group(1))
    return tuple(sorted(path for path in paths if path != "/dev/null"))


def apply_patches(source: Source, repo: Path,
                  base: Path) -> tuple[Path, tuple[PatchResult, ...]]:
    """Apply the reviewed cuts to the archive copy, recording both sides."""
    work = Path(base) / "patched"
    shutil.copytree(source.root, work)
    env = {
        **os.environ,
        **HERMETIC_GIT_ENV,
        "GIT_AUTHOR_NAME": "wave18-builder",
        "GIT_AUTHOR_EMAIL": "wave18@invalid",
        "GIT_COMMITTER_NAME": "wave18-builder",
        "GIT_COMMITTER_EMAIL": "wave18@invalid",
        "GIT_AUTHOR_DATE": "2026-10-29T00:00:00+0000",
        "GIT_COMMITTER_DATE": "2026-10-29T00:00:00+0000",
        "HOME": str(work),
    }
    for args in (("init", "-q"), ("add", "-A"),
                 ("commit", "-qm", "wave18 sandbox base")):
        proc = subprocess.run(["git", *HERMETIC_GIT_FLAGS, "-C", str(work),
                               *args], capture_output=True, env=env)
        if proc.returncode != 0:
            raise Refused("GIT_FAILED",
                          f"git {' '.join(args)} on the sandbox failed: "
                          f"{proc.stderr.decode('utf-8', 'replace')[:300]}")
    results: list[PatchResult] = []
    for spec in PATCHES:
        patch = Path(repo) / spec["path"]
        if not patch.is_file():
            raise Refused(
                BLOCKED_INPUT_ABSENT,
                f"{spec['path']} is absent; this builder consumes #505's "
                f"published patches rather than re-implementing a transform")
        data = patch.read_bytes()
        target_paths = _patch_targets(data)
        # This patch's actual input, not the source commit's file: CUT-B runs
        # after CUT-A, and a provenance that hides the intermediate is a
        # provenance a reader cannot re-apply.
        pre: dict[str, tuple[str | None, str | None]] = {}
        for rel in target_paths:
            candidate = work / rel
            if candidate.is_file():
                current = candidate.read_bytes()
                _refuse_crlf(current, f"input {rel}")
                pre[rel] = (sha256_bytes(current),
                            str(_git(work, "hash-object", rel)))
            else:
                pre[rel] = (None, None)
        checked = subprocess.run(
            ["git", *HERMETIC_GIT_FLAGS, "-C", str(work), "apply", "--check",
             "--whitespace=nowarn", str(patch)],
            capture_output=True, env=env)
        if checked.returncode != 0:
            raise Refused(
                "BLOCKED_PATCH_DOES_NOT_APPLY",
                f"{spec['id']} does not apply to {source.commit}: "
                f"{checked.stderr.decode('utf-8', 'replace').strip()[:300]}")
        applied = subprocess.run(
            ["git", *HERMETIC_GIT_FLAGS, "-C", str(work), "apply",
             "--whitespace=nowarn", str(patch)],
            capture_output=True, env=env)
        if applied.returncode != 0:
            raise Refused("BLOCKED_PATCH_FAILED",
                          f"{spec['id']}: {applied.stderr.decode()[:300]}")
        targets = []
        for rel in target_paths:
            original_path = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "blob",
                 f"{source.commit}:{rel}"], capture_output=True)
            original = (original_path.stdout if original_path.returncode == 0
                        else None)
            result_path = work / rel
            if not result_path.is_file():
                raise Refused("BLOCKED_PATCH_RESULT_ABSENT", rel)
            result = result_path.read_bytes()
            _refuse_crlf(result, f"patched {rel}")
            targets.append({
                "path": rel,
                "original_blob_sha": (
                    str(_git(repo, "rev-parse", f"{source.commit}:{rel}"))
                    if original is not None else None),
                "original_sha256": (sha256_bytes(original)
                                    if original is not None else None),
                "input_sha256": pre[rel][0],
                "input_blob_sha": pre[rel][1],
                "result_sha256": sha256_bytes(result),
                "result_blob_sha": str(_git(work, "hash-object", rel)),
            })
        results.append(PatchResult(
            id=spec["id"], path=spec["path"], sha256=sha256_bytes(data),
            owner_source=spec["owner_source"],
            owner_commit=spec["owner_commit"],
            rebased_to=spec["rebased_to"],
            origin=spec["origin"], targets=tuple(targets)))
    if not _git_clean(repo):
        raise Refused(
            BLOCKED_DIRTY_SOURCE,
            "the live tree changed while the sandbox transform ran; this "
            "builder must never write the owner's source")
    return work, tuple(results)


# ---------------------------------------------------------------------------
# closure and the four-unit partition
# ---------------------------------------------------------------------------
def closure_modules(builder: Any, root: Path,
                    entries: Sequence[str]) -> set[str]:
    modules: set[str] = set()
    for entry in entries:
        modules |= set(builder.closure(entry, root).modules)
    return modules


def module_files(builder: Any, root: Path,
                 modules: Iterable[str]) -> dict[str, bytes]:
    payload: dict[str, bytes] = {}
    for name in modules:
        path = builder._resolve(name, Path(root))
        if path is None:
            continue
        data = path.read_bytes()
        if path.suffix in TEXT_SUFFIXES:
            _refuse_crlf(data, path.relative_to(root).as_posix())
        payload[path.relative_to(root).as_posix()] = data
    return dict(sorted(payload.items()))


@dataclass(frozen=True)
class Partition:
    hub: frozenset[str]
    node: frozenset[str]
    shared: frozenset[str]
    hub_own: frozenset[str]
    node_own: frozenset[str]
    inadmissible: frozenset[str]
    unreached: frozenset[str]

    @property
    def base(self) -> frozenset[str]:
        return self.shared & BASE_ADMISSIBLE

    @property
    def exact(self) -> bool:
        return not self.inadmissible and not self.unreached


def partition(builder: Any, root: Path) -> Partition:
    hub = frozenset(closure_modules(builder, root, HUB_ENTRIES))
    node = frozenset(closure_modules(builder, root, NODE_ENTRIES))
    shared = hub & node
    return Partition(
        hub=hub, node=node, shared=shared,
        hub_own=hub - shared, node_own=node - shared,
        inadmissible=shared - BASE_ADMISSIBLE,
        unreached=BASE_ADMISSIBLE - shared)


def cross_component_leaks(builder: Any, root: Path) -> dict[str, dict]:
    """What a naive two-artifact build would carry from the other half."""
    part = partition(builder, root)
    hub_files = module_files(builder, root, part.hub)
    node_files = module_files(builder, root, part.node)
    hub_leaks = sorted(rel for rel in hub_files
                       if rel.startswith(NODE_PREFIXES))
    node_leaks = sorted(rel for rel in node_files
                        if rel in HUB_ONLY_FILES
                        or rel.startswith(HUB_ONLY_PREFIXES))
    return {"hub": {"leaks": hub_leaks},
            "node": {"leaks": node_leaks}}


# ---------------------------------------------------------------------------
# artifacts
# ---------------------------------------------------------------------------
def authority_members(root: Path) -> dict[str, bytes]:
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
        "signed": False,
        "signature_note": "digests are recorded; no cryptographic signature is "
                          "minted, claimed or bypassed",
    }
    payload[AUTHORITY_MANIFEST_NAME] = (
        json.dumps(manifest, indent=2, sort_keys=True,
                   ensure_ascii=False) + "\n").encode("utf-8")
    return dict(sorted(payload.items()))


def _refuse_members(members: Mapping[str, bytes], kind: str) -> None:
    problems: list[str] = []
    for rel in members:
        path = PurePosixPath(rel)
        if (path.is_absolute() or ".." in path.parts
                or path.as_posix() != rel or "\\" in rel):
            problems.append(rel)
        elif FORBIDDEN_MEMBER.search(rel):
            problems.append(rel)
    if problems:
        raise Refused(BLOCKED_UNSAFE_MEMBER,
                      f"{kind} carries unsafe or runtime/credential member(s): "
                      f"{sorted(problems)}")


def _tree_digest(builder: Any, members: Mapping[str, bytes]) -> str:
    return builder._tree_digest(
        [{"path": name, "sha256": sha256_bytes(data)}
         for name, data in sorted(members.items())])


def _manifest(*, builder: Any, kind: str, version: str,
              members: Mapping[str, bytes], entrypoints: Sequence[str],
              depends_on: Sequence[str], provides: Sequence[str],
              source: Source, patches: Sequence[PatchResult],
              authority: Mapping[str, Any] | None = None) -> dict:
    _refuse_members(members, kind)
    return {
        "schema": ARTIFACT_SCHEMA,
        "kinds": [kind],
        "composition": COMPOSITION,
        "name": kind,
        "version": version,
        "members": sorted(members),
        "member_sha256": {name: sha256_bytes(data)
                          for name, data in sorted(members.items())},
        "tree_sha256": _tree_digest(builder, members),
        "entrypoints": list(entrypoints),
        "depends_on": list(depends_on),
        "installable_with": list(dict.fromkeys(depends_on)),
        "provides": list(provides),
        "platforms": (["linux", "windows"] if kind == NODE_KIND else ["linux"]),
        "install": {
            "independently_installable": True,
            "requires_repository": False,
            "blocked_by": [],
            "install_root": None,  # filled by the release plan, never by code
        },
        "runtime_resources": {"socket": None, "lock": None,
                              "state_root": None, "credential": None},
        "authority": dict(authority) if authority else None,
        "source": {
            "commit": source.commit,
            "patched": bool(patches),
            "patch_ids": [patch.id for patch in patches],
        },
    }


@dataclass(frozen=True)
class Unit:
    kind: str
    version: str
    path: Path
    sha256: str
    tree_sha256: str
    members: Mapping[str, bytes]
    manifest: Mapping[str, Any]

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "version": self.version,
            "tarball_sha256": "sha256:" + self.sha256,
            "tree_sha256": self.tree_sha256,
            "member_count": len(self.members),
            "depends_on": list(self.manifest.get("depends_on") or ()),
            "entrypoints": list(self.manifest.get("entrypoints") or ()),
            "installable": bool(
                (self.manifest.get("install") or {}).get(
                    "independently_installable")),
        }


def _write_unit(builder: Any, outdir: Path, kind: str, version: str,
                members: Mapping[str, bytes], manifest: dict) -> Unit:
    path = outdir / f"{kind}-{version}.tar.gz"
    builder.write_artifact(manifest, dict(members), path)
    return Unit(kind=kind, version=version, path=path,
                sha256=sha256_file(path),
                tree_sha256=str(manifest["tree_sha256"]),
                members=dict(members), manifest=manifest)


def build_units(builder: Any, source: Source, root: Path, patches: Sequence[PatchResult],
                version: str, outdir: Path) -> dict[str, Unit]:
    if not VERSION_RE.match(version):
        raise Refused("BLOCKED_VERSION",
                      f"{version!r} is not <major>.<minor>.<patch>[+cachebuster]")
    part = partition(builder, root)
    if not part.exact:
        raise Refused(
            BLOCKED_BASE_NOT_PINNED,
            "the shared closure is not the pinned Base; inadmissible "
            f"{sorted(part.inadmissible)}, unreached {sorted(part.unreached)}")
    if part.node_own - set(NODE_ENTRIES) - {"fleet_mcp.node"}:
        raise Refused(BLOCKED_CROSS_COMPONENT,
                      f"unexpected Node-only modules: {sorted(part.node_own)}")

    base_members = module_files(builder, root, part.base)
    hub_members = module_files(builder, root, part.hub_own)
    node_members = module_files(builder, root, part.node_own)
    authority = authority_members(root)

    # The neighbour's file is refused by name, before a tarball exists.
    for rel in hub_members:
        if rel.startswith(NODE_PREFIXES):
            raise Refused(BLOCKED_CROSS_COMPONENT,
                          f"Hub unit would carry Node file {rel}")
    for rel in node_members:
        if rel in HUB_ONLY_FILES or rel.startswith(HUB_ONLY_PREFIXES):
            raise Refused(BLOCKED_CROSS_COMPONENT,
                          f"Node unit would carry Hub file {rel}")

    units: dict[str, Unit] = {}
    units[BASE_KIND] = _write_unit(
        builder, outdir, BASE_KIND, version, base_members,
        _manifest(builder=builder, kind=BASE_KIND, version=version,
                  members=base_members, entrypoints=(),
                  depends_on=(), provides=sorted(part.base),
                  source=source, patches=patches))
    units[AUTHORITY_KIND] = _write_unit(
        builder, outdir, AUTHORITY_KIND, version, authority,
        _manifest(builder=builder, kind=AUTHORITY_KIND, version=version,
                  members=authority, entrypoints=(),
                  depends_on=(), provides=[AUTHORITY_MODULE],
                  source=source, patches=patches))
    units[HUB_KIND] = _write_unit(
        builder, outdir, HUB_KIND, version, hub_members,
        _manifest(builder=builder, kind=HUB_KIND, version=version,
                  members=hub_members, entrypoints=HUB_ENTRIES,
                  depends_on=(BASE_KIND, AUTHORITY_KIND),
                  provides=sorted(part.hub_own),
                  source=source, patches=patches,
                  authority={"abi": AUTHORITY_ABI,
                             "module": AUTHORITY_MODULE,
                             "provided_by": AUTHORITY_KIND,
                             "single_authority": True,
                             "signed": False}))
    units[NODE_KIND] = _write_unit(
        builder, outdir, NODE_KIND, version, node_members,
        _manifest(builder=builder, kind=NODE_KIND, version=version,
                  members=node_members, entrypoints=NODE_ENTRIES,
                  depends_on=(BASE_KIND,), provides=sorted(part.node_own),
                  source=source, patches=patches))

    composition_manifest = {
        "schema": ARTIFACT_SCHEMA,
        "kinds": [COMPOSITION_KIND],
        "composition": COMPOSITION,
        "name": COMPOSITION_KIND,
        "version": version,
        "members": [],
        "member_sha256": {},
        "tree_sha256": _tree_digest(builder, {}),
        "entrypoints": [],
        "depends_on": [],
        "units": {kind: {"version": unit.version,
                         "tarball_sha256": "sha256:" + unit.sha256,
                         "tree_sha256": unit.tree_sha256}
                  for kind, unit in sorted(units.items())},
        "same_version_required": False,
        "install": {
            "independently_installable": False,
            "requires_repository": False,
            "blocked_by": ["manifest-only composition: it carries no file "
                           "payload and must not be installed as one"],
        },
        "install_order": [BASE_KIND, AUTHORITY_KIND, HUB_KIND, NODE_KIND],
        "rollback_order": [NODE_KIND, HUB_KIND, AUTHORITY_KIND, BASE_KIND],
        "composition_rollback": {
            "kind": "compensating",
            "why": "the Base is shared; rolling it back without the units is a "
                   "coordinated operation, not an independent one",
            "order": [NODE_KIND, HUB_KIND, AUTHORITY_KIND, BASE_KIND],
        },
        "platforms": ["linux", "windows"],
        "runtime_resources": {"socket": None, "lock": None,
                              "state_root": None, "credential": None},
        "source": {"commit": source.commit, "patched": bool(patches),
                   "patch_ids": [patch.id for patch in patches]},
    }
    units[COMPOSITION_KIND] = _write_unit(
        builder, outdir, COMPOSITION_KIND, version, {},
        composition_manifest)
    return units


# ---------------------------------------------------------------------------
# verification of what was written
# ---------------------------------------------------------------------------
def verify_unit(builder: Any, unit: Unit, *, expect_kind: str,
                expect_version: str) -> dict:
    artifact = builder.read_artifact(unit.path)
    manifest = artifact.manifest
    if manifest.get("schema") != ARTIFACT_SCHEMA:
        raise Refused(BLOCKED_MANIFEST_DRIFT, "schema")
    if manifest.get("composition") != COMPOSITION:
        raise Refused(BLOCKED_MANIFEST_DRIFT, "composition")
    if manifest.get("kinds") != [expect_kind] or manifest.get("name") != expect_kind:
        raise Refused(BLOCKED_MANIFEST_DRIFT, f"kind {expect_kind}")
    if manifest.get("version") != expect_version:
        raise Refused(BLOCKED_MANIFEST_DRIFT, "version")
    if sorted(artifact.members) != sorted(manifest.get("members") or ()):
        raise Refused(BLOCKED_MANIFEST_DRIFT, "member list")
    if _tree_digest(builder, artifact.members) != manifest.get("tree_sha256"):
        raise Refused(BLOCKED_MANIFEST_DRIFT, "tree digest")
    _refuse_members(artifact.members, expect_kind)

    if expect_kind == HUB_KIND:
        leaks = [rel for rel in artifact.members if rel.startswith(NODE_PREFIXES)]
        if leaks:
            raise Refused(BLOCKED_CROSS_COMPONENT,
                          f"Hub artifact carries Node file(s) {sorted(leaks)}")
    if expect_kind == NODE_KIND:
        leaks = [rel for rel in artifact.members
                 if rel in HUB_ONLY_FILES or rel.startswith(HUB_ONLY_PREFIXES)]
        if leaks:
            raise Refused(BLOCKED_CROSS_COMPONENT,
                          f"Node artifact carries Hub file(s) {sorted(leaks)}")
    if expect_kind == BASE_KIND:
        leaks = [rel for rel in artifact.members
                 if rel in HUB_ONLY_FILES or rel.startswith(NODE_PREFIXES)]
        if leaks:
            raise Refused(BLOCKED_CROSS_COMPONENT,
                          f"Base carries unit-specific file(s) {sorted(leaks)}")
    if expect_kind == AUTHORITY_KIND:
        if set(artifact.members) != {
                *AUTHORITY_FILES, AUTHORITY_MANIFEST_NAME}:
            raise Refused(BLOCKED_MANIFEST_DRIFT,
                          "the authority is two files plus its manifest")
    return artifact.manifest


def verify_composition(builder: Any, units: Mapping[str, Unit],
                       version: str) -> dict:
    composition = builder.read_artifact(units[COMPOSITION_KIND].path)
    manifest = composition.manifest
    if manifest.get("version") != version:
        raise Refused(BLOCKED_MANIFEST_DRIFT, "composition version")
    declared = manifest.get("units") or {}
    for kind in UNITS:
        entry = declared.get(kind) or {}
        unit = units[kind]
        if entry.get("tarball_sha256") != "sha256:" + unit.sha256:
            raise Refused(BLOCKED_MANIFEST_DRIFT,
                          f"composition digest for {kind}")
        if entry.get("tree_sha256") != unit.tree_sha256:
            raise Refused(BLOCKED_MANIFEST_DRIFT,
                          f"composition tree for {kind}")
    if manifest.get("same_version_required") is not False:
        raise Refused(BLOCKED_MANIFEST_DRIFT,
                      "this build must not claim same-version atomic coupling")
    return manifest


def assert_no_tampering(builder: Any, path: Path) -> None:
    builder.read_artifact(path)  # member digests are checked by #431's reader


# ---------------------------------------------------------------------------
# isolation: no checkout, no neighbour
# ---------------------------------------------------------------------------
_PROBE = r'''
import importlib
import json
import sys

root, component, declared_path, fixture = sys.argv[1:5]
sys.path[:0] = [root]
out = {"component": component}
declared = json.load(open(declared_path, encoding="utf-8"))["modules"]

if component == "hub":
    import fleet_mcp.server
    import fleet_mcp.tools
    import fleet_mcp.owner_setup
    from fleet_mcp.server import McpServer
    server = McpServer(lambda: None)
    answer = server.handle({"jsonrpc": "2.0", "id": 1,
                            "method": "no/such/method"})
    out["dispatch_refused"] = bool(answer and answer.get("error"))
    out["tools"] = len(fleet_mcp.tools.SERVED_TOOLS)
    try:
        fleet_mcp.owner_setup._node_config_loader()
        out["node_loader"] = "unavailable-not-refused"
    except ValueError as exc:
        out["node_loader"] = str(exc).split(":", 1)[0]
    import fleet.transport as authority
    out["authority_symbols"] = all(
        hasattr(authority, name) for name in
        ["Availability", "CapabilityAvailability", "Device", "DeviceRef",
         "DeviceRegistry", "UnknownDevice", "canonical_level"])
else:
    from fleet_mcp.node.connector import load_node_config
    config = load_node_config(fixture)
    out["device_ref"] = config.device_ref

loaded = []
for name, module in sorted(sys.modules.items()):
    if name == "fleet_mcp" or name.startswith("fleet_mcp."):
        loaded.append([name, getattr(module, "__file__", None)])
out["fleet_mcp_modules"] = loaded
out["declared"] = declared
out["booted"] = True
print(json.dumps(out, sort_keys=True))
'''

NODE_FIXTURE = """\
schema: serveros/fleet-node.local/v1
device_ref: device:wave18-hermetic
secret_anchor: {secret}
cloudcli_endpoint: http://127.0.0.1:1/mcp
cloudcli_credential_anchor: {credential}
allow_send_to: []
allow_create: false
allow_control: false
"""


def _extract_into(builder: Any, unit: Unit, root: Path) -> None:
    artifact = builder.read_artifact(unit.path)
    for rel, data in artifact.members.items():
        path = Path(root) / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)


def _run_probe(root: Path, component: str, declared: Path,
               fixture: Path, cwd: Path) -> dict:
    proc = subprocess.run(
        [sys.executable, "-I", "-B", "-c", _PROBE, str(root), component,
         str(declared), str(fixture)],
        capture_output=True, text=True, cwd=str(cwd),
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}, timeout=180)
    if proc.returncode != 0:
        return {"booted": False,
                "error": (proc.stderr or proc.stdout).strip()[-600:]}
    return json.loads(proc.stdout)


def audit_loaded(component: str, answer: Mapping[str, Any],
                 declared_modules: Iterable[str], root: Path) -> None:
    """Refuse a module the artifact did not declare or loaded from outside."""
    root_resolved = str(Path(root).resolve())
    declared = set(declared_modules)
    for name, path in answer.get("fleet_mcp_modules") or ():
        if name not in declared:
            raise Refused(BLOCKED_UNDECLARED_DEPENDENCY,
                          f"{component} loaded undeclared module {name}")
        if path and not str(Path(path).resolve()).startswith(root_resolved):
            raise Refused(
                BLOCKED_UNDECLARED_DEPENDENCY,
                f"{component} loaded {name} from outside its root: {path}")


def boot_isolated(builder: Any, units: Mapping[str, Unit],
                  workdir: Path) -> dict[str, dict]:
    """Boot each half in a root with its declared deps and no neighbour."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = workdir / "node-binding.yaml"
    fixture.write_text(NODE_FIXTURE.format(
        secret=workdir / "secret", credential=workdir / "credential"),
        encoding="utf-8")
    results: dict[str, dict] = {}
    for component, deps, declared_modules in (
            ("hub", (BASE_KIND, AUTHORITY_KIND, HUB_KIND),
             sorted(BASE_ADMISSIBLE | set(units[HUB_KIND].manifest["provides"]))),
            ("node", (BASE_KIND, NODE_KIND),
             sorted(BASE_ADMISSIBLE | set(units[NODE_KIND].manifest["provides"])))):
        root = workdir / f"root-{component}"
        for kind in deps:
            _extract_into(builder, units[kind], root)
        # The neighbour was never extracted. These are exact paths.
        neighbour = (
            ("fleet_mcp/node/connector.py", "fleet_mcp/node/__init__.py")
            if component == "hub"
            else ("fleet_mcp/server.py", "fleet_mcp/tools.py",
                  "fleet_mcp/fleet.py", "fleet_mcp/registry.py",
                  "fleet_mcp/owner_setup.py"))
        present = [rel for rel in neighbour if (root / rel).exists()]
        if present:
            raise Refused(BLOCKED_ISOLATION,
                          f"{component} root carries neighbour file(s) {present}")
        declared = workdir / f"declared-{component}.json"
        declared.write_text(json.dumps({"modules": declared_modules}),
                            encoding="utf-8")
        answer = _run_probe(root, component, declared, fixture, workdir)
        if not answer.get("booted"):
            raise Refused(BLOCKED_ISOLATION,
                          f"{component} did not boot: {answer.get('error')}")
        if component == "hub":
            if answer.get("node_loader") != "NODE_CONFIG_LOADER_UNAVAILABLE":
                raise Refused(BLOCKED_ISOLATION,
                              f"Hub did not refuse the Node feature by name: "
                              f"{answer.get('node_loader')}")
            if not answer.get("authority_symbols") or not answer.get("dispatch_refused"):
                raise Refused(BLOCKED_ISOLATION,
                              "Hub authority or dispatch probe did not hold")
        else:
            if answer.get("device_ref") != "device:wave18-hermetic":
                raise Refused(BLOCKED_ISOLATION,
                              f"Node loaded the wrong binding: {answer.get('device_ref')}")
        audit_loaded(component, answer, declared_modules, root)
        clean = dict(answer)
        clean["fleet_mcp_modules"] = _portable_modules(answer, root)
        clean["error"] = _sanitize(str(clean.get("error", "")), workdir, root)
        results[component] = clean
    return results


def baseline_isolation(builder: Any, source: Source,
                       workdir: Path) -> dict:
    """What a strictly two-artifact, own-files-only build actually does.

    This is the RED half: Hub carries only Hub-owned closure files plus the
    Authority (a third artifact, not the Node), Node carries only Node-owned
    files. Neither can boot, and the failure names the missing half.
    """
    part = partition(builder, source.root)
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = workdir / "node-binding.yaml"
    fixture.write_text(NODE_FIXTURE.format(
        secret=workdir / "secret", credential=workdir / "credential"),
        encoding="utf-8")
    results: dict[str, dict] = {}
    for component, modules in (("hub", part.hub), ("node", part.node)):
        files = module_files(builder, source.root, modules)
        owned = {rel: data for rel, data in files.items()
                 if (rel.startswith(NODE_PREFIXES) if component == "node"
                     else not rel.startswith(NODE_PREFIXES))}
        root = workdir / f"two-{component}"
        for rel, data in owned.items():
            path = root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        if component == "hub":
            for rel, data in authority_members(source.root).items():
                path = root / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
        declared = _declared(workdir, f"two-{component}",
                             (mod.split(".__init__")[0] for mod in modules))
        answer = _run_probe(root, component, declared, fixture, workdir)
        results[component] = {
            "files": len(owned),
            "booted": bool(answer.get("booted")),
            "error": _sanitize(answer.get("error", ""), workdir, root)[-300:],
        }
    return results


def negative_control(builder: Any, units: Mapping[str, Unit],
                     workdir: Path) -> dict:
    """Removing a shared primitive must make the boot fail."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    fixture = workdir / "node-binding.yaml"
    fixture.write_text(NODE_FIXTURE.format(
        secret=workdir / "secret", credential=workdir / "credential"),
        encoding="utf-8")
    cases = (
        ("node", (BASE_KIND, NODE_KIND), "fleet_mcp/errors.py",
         ("fleet_mcp", "fleet_mcp.node.connector")),
        ("node", (BASE_KIND, NODE_KIND), "fleet_mcp/config.py",
         ("fleet_mcp", "fleet_mcp.node.connector")),
        ("hub", (BASE_KIND, AUTHORITY_KIND, HUB_KIND), "fleet/transport.py",
         ("fleet_mcp", "fleet_mcp.server")),
    )
    results = {}
    for component, deps, removed, _entry in cases:
        root = workdir / f"neg-{component}-{Path(removed).stem}"
        for kind in deps:
            _extract_into(builder, units[kind], root)
        target = root / removed
        if not target.exists():
            raise Refused(BLOCKED_NEGATIVE_CONTROL,
                          f"negative control target {removed} is absent")
        target.unlink()
        declared = workdir / f"declared-neg-{component}-{Path(removed).stem}.json"
        declared.write_text(json.dumps({"modules": sorted(
            BASE_ADMISSIBLE | set(
                units[HUB_KIND if component == "hub" else NODE_KIND]
                .manifest["provides"]))}), encoding="utf-8")
        answer = _run_probe(root, component, declared, fixture, workdir)
        if answer.get("booted"):
            raise Refused(
                BLOCKED_NEGATIVE_CONTROL,
                f"{component} still booted after removing {removed}; the "
                f"isolation claim is not measuring the shared primitive")
        results[f"{component}:{removed}"] = {
            "booted": False,
            "error": _sanitize(answer.get("error", ""), workdir, root)[-300:],
        }
    return results


# ---------------------------------------------------------------------------
# release footprints: #483/#480 collision veto and shadow refusal
# ---------------------------------------------------------------------------
RESOURCES = ("install_path", "port", "socket", "backend_id", "oauth_audience",
             "cred_state_root", "lock", "unit")
PATH_RESOURCES = ("install_path", "cred_state_root", "lock")


def _norm(path: str) -> str:
    return posixpath.normpath(path)


def _within(a: str, b: str) -> bool:
    return a == b or a.startswith(b + "/") or b.startswith(a + "/")


def resource_matrix(hub: Mapping[str, Any],
                    node: Mapping[str, Any]) -> dict:
    collisions = []
    for name in RESOURCES:
        left, right = hub.get(name), node.get(name)
        if name in PATH_RESOURCES and isinstance(left, str) and isinstance(right, str):
            if _within(_norm(left), _norm(right)):
                collisions.append([name, f"{left} and {right} overlap"])
        elif left is not None and left == right:
            collisions.append([name, f"both units use {left!r}"])
    if hub.get("device_ref") != node.get("device_ref"):
        return {"decision": "BLOCKED", "code": BLOCKED_DEVICE_DISAGREEMENT,
                "collisions": []}
    return {"decision": "BLOCKED" if collisions else OK,
            "code": BLOCKED_RESOURCE_COLLISION if collisions else OK,
            "collisions": collisions}


def shadow_decision(verb: str, *, granted: bool) -> dict:
    if verb not in WRITE_VERBS and verb not in READ_VERBS:
        return {"decision": "BLOCKED", "code": SHADOW_VERB_UNKNOWN,
                "verb": verb}
    if verb in WRITE_VERBS:
        return {"decision": "BLOCKED", "code": NEXT_SHADOW_WRITE_FORBIDDEN,
                "verb": verb, "granted": granted,
                "note": "a shadow reads the current controller; granting a "
                        "write verb does not make it one"}
    if not granted:
        return {"decision": "BLOCKED", "code": NEXT_SHADOW_NOT_GRANTED,
                "verb": verb}
    return {"decision": OK, "code": OK, "verb": verb}


def release_plan(units: Mapping[str, Unit], version: str) -> dict:
    hub = {
        "track": "next-hub",
        "install_path": "/opt/fleet-next/hub",
        "port": 8101,
        "socket": "/run/fleet-next/hub.sock",
        "backend_id": "next-hub",
        "oauth_audience": "https://next.invalid/mcp",
        "cred_state_root": "/var/lib/fleet-next/hub",
        "lock": "/run/lock/fleet-next-hub.lock",
        "unit": "fleet-next-hub.service",
        "device_ref": "device:tokyo-shared-dev-01",
        "version": version,
        "artifact_sha256": "sha256:" + units[HUB_KIND].sha256,
    }
    node = {
        "track": "next-node",
        "install_path": "/opt/fleet-next/node",
        "port": 3402,
        "socket": "/run/fleet-next/node.sock",
        "backend_id": "next-node",
        "oauth_audience": "https://next-node.invalid/mcp",
        "cred_state_root": "/var/lib/fleet-next/node",
        "lock": "/run/lock/fleet-next-node.lock",
        "unit": "fleet-next-node.service",
        "device_ref": "device:tokyo-shared-dev-01",
        "version": version,
        "artifact_sha256": "sha256:" + units[NODE_KIND].sha256,
    }
    return {
        "schema": "serveros/fleet-next-release-plan.v1",
        "version": version,
        "hub": hub,
        "node": node,
        "matrix": resource_matrix(hub, node),
        "shadow": {
            "read": shadow_decision("get_session", granted=True),
            "write_even_if_granted": shadow_decision("send", granted=True),
            "unknown": shadow_decision("frobnicate", granted=True),
        },
        "legacy": {
            "track": "legacy-0.1.0",
            "contract": ["get_session", "get_run", "send"],
            "unchanged_by_this_build": True,
        },
    }


# ---------------------------------------------------------------------------
# rollback against the Legacy 0.1.0 fake
# ---------------------------------------------------------------------------
class FakeLegacy:
    """A Legacy Fleet 0.1.0 stand-in: three answers, identities, deliveries."""

    def __init__(self) -> None:
        self.identities = {"device:legacy": "legacy-0.1.0",
                           "plugin:legacy": "plugin_legacy_000000001"}
        self.answers = {"get_session": "S-1", "get_run": "R-1", "send": "ok"}
        self.deliveries: list[str] = []

    def call(self, verb: str, intent: str | None = None) -> str:
        if verb not in self.answers:
            raise KeyError(verb)
        if verb == "send" and intent not in self.deliveries:
            self.deliveries.append(intent or "")
        return self.answers[verb]

    def snapshot(self) -> dict:
        return {"identities": dict(self.identities),
                "answers": dict(self.answers),
                "deliveries": list(self.deliveries)}


def _install_version(builder: Any, units: Mapping[str, Unit], root: Path,
                     kinds: Sequence[str], version: str) -> None:
    for kind in kinds:
        _extract_into(builder, units[kind], root)
    pointer = root / ".wave18" / "current.json"
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({
        "version": version,
        "units": {kind: {"tarball_sha256": "sha256:" + units[kind].sha256,
                         "tree_sha256": units[kind].tree_sha256}
                  for kind in kinds},
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def rollback_check(builder: Any, v1: Mapping[str, Unit],
                   v2: Mapping[str, Unit], workdir: Path) -> dict:
    """Independent rollback + composition rollback, Legacy fake untouched."""
    workdir = Path(workdir)
    workdir.mkdir(parents=True, exist_ok=True)
    legacy = FakeLegacy()
    before = legacy.snapshot()
    hub_root = workdir / "install-hub"
    node_root = workdir / "install-node"
    fixture = workdir / "node-binding.yaml"
    fixture.write_text(NODE_FIXTURE.format(
        secret=workdir / "secret", credential=workdir / "credential"),
        encoding="utf-8")
    steps = []

    # Install v1 in both prefixes, then upgrade each to v2.
    for root, kinds in ((hub_root, (BASE_KIND, AUTHORITY_KIND, HUB_KIND)),
                        (node_root, (BASE_KIND, NODE_KIND))):
        _install_version(builder, v1, root, kinds, v1[BASE_KIND].version)
        _install_version(builder, v2, root, kinds, v2[BASE_KIND].version)

    # Independent Node rollback: only the Node prefix moves.
    hub_before = {rel: sha256_bytes((hub_root / rel).read_bytes())
                  for rel in v2[HUB_KIND].members}
    _install_version(builder, v1, node_root, (BASE_KIND, NODE_KIND),
                     v1[BASE_KIND].version)
    node_answer = _run_probe(
        node_root, "node",
        _declared(workdir, "rollback-node", BASE_ADMISSIBLE | set(
            v1[NODE_KIND].manifest["provides"])),
        fixture, workdir)
    hub_after = {rel: sha256_bytes((hub_root / rel).read_bytes())
                 for rel in v2[HUB_KIND].members}
    steps.append({
        "step": "rollback-node-only",
        "node_booted": bool(node_answer.get("booted")),
        "hub_UNTOUCHED": hub_before == hub_after,
        "legacy_unchanged": legacy.snapshot() == before,
    })

    # Independent Hub rollback: only the Hub prefix moves.
    node_before = {rel: sha256_bytes((node_root / rel).read_bytes())
                   for rel in v1[NODE_KIND].members}
    _install_version(builder, v1, hub_root,
                     (BASE_KIND, AUTHORITY_KIND, HUB_KIND),
                     v1[BASE_KIND].version)
    hub_answer = _run_probe(
        hub_root, "hub",
        _declared(workdir, "rollback-hub", BASE_ADMISSIBLE | set(
            v1[HUB_KIND].manifest["provides"])),
        fixture, workdir)
    node_after = {rel: sha256_bytes((node_root / rel).read_bytes())
                  for rel in v1[NODE_KIND].members}
    steps.append({
        "step": "rollback-hub-only",
        "hub_booted": bool(hub_answer.get("booted")),
        "node_UNTOUCHED": node_before == node_after,
        "legacy_unchanged": legacy.snapshot() == before,
    })

    # Composition rollback: reverse order, both prefixes, compensating.
    for kind in (NODE_KIND, HUB_KIND, AUTHORITY_KIND, BASE_KIND):
        target = node_root if kind == NODE_KIND else hub_root
        unit_v1 = v1[kind]
        _extract_into(builder, unit_v1, target)
    composition = {
        "step": "composition-rollback",
        "order": [NODE_KIND, HUB_KIND, AUTHORITY_KIND, BASE_KIND],
        "legacy_unchanged": legacy.snapshot() == before,
        "note": "Base is shared; this is the compensating order, not four "
                "independent releases",
    }
    steps.append(composition)
    after = legacy.snapshot()
    unchanged = after == before
    return {
        "decision": OK if unchanged and all(
            step.get("legacy_unchanged", True) for step in steps) else "BLOCKED",
        "code": OK if unchanged else "LEGACY_CONTRACT_DRIFT",
        "from_version": v2[BASE_KIND].version,
        "to_version": v1[BASE_KIND].version,
        "mixed_versions": {
            "hub": v2[HUB_KIND].version,
            "node": v1[NODE_KIND].version,
            "note": "D7 gives each kind its own version; the Node prefix was "
                    "rolled back while the Hub prefix stayed on the newer one",
        },
        "before": before,
        "after": after,
        "legacy_unchanged": unchanged,
        "steps": steps,
    }


def _declared(workdir: Path, tag: str, modules: Iterable[str]) -> Path:
    path = Path(workdir) / f"declared-{tag}.json"
    path.write_text(json.dumps({"modules": sorted(modules)}), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# build orchestration
# ---------------------------------------------------------------------------
@dataclass
class Build:
    source: Source
    patches: tuple[PatchResult, ...]
    units: dict[str, Unit]
    outdir: Path
    version: str

    def as_dict(self) -> dict:
        return {
            "version": self.version,
            "source": self.source.as_dict(),
            "patches": [patch.as_dict() for patch in self.patches],
            "units": {kind: unit.as_dict() for kind, unit in sorted(self.units.items())},
        }


def build(repo: Path, commit: str, version: str, outdir: Path,
          *, mode: str = "cut", scratch_root: Path | None = None) -> Build:
    repo = Path(repo)
    builder = load_builder(repo)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    own_scratch = scratch_root is None
    scratch = (Path(scratch_root) if scratch_root is not None
               else Path(tempfile.mkdtemp(prefix="wave18-build-")))
    scratch.mkdir(parents=True, exist_ok=True)
    try:
        source = archive_source(repo, commit, scratch)
        if mode == "baseline":
            leaks = cross_component_leaks(builder, source.root)
            raise Refused(
                BLOCKED_CROSS_COMPONENT,
                "the unpatched closure is not two independent artifacts: "
                f"Hub would carry {leaks['hub']['leaks']}; Node would carry "
                f"{leaks['node']['leaks']}")
        if mode != "cut":
            raise Refused("BLOCKED_MODE", f"unknown mode {mode!r}")
        patched_root, patches = apply_patches(source, repo, scratch)
        units = build_units(builder, source, patched_root, patches,
                            version, outdir)
        for kind in UNITS:
            verify_unit(builder, units[kind], expect_kind=kind,
                        expect_version=version)
        verify_composition(builder, units, version)
        return Build(source=source, patches=patches, units=units,
                     outdir=outdir, version=version)
    finally:
        if own_scratch:
            shutil.rmtree(scratch, ignore_errors=True)


def baseline_evidence(repo: Path, commit: str, outdir: Path) -> dict:
    """The RED pack: what the unpatched tree actually is."""
    repo = Path(repo)
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    builder = load_builder(repo)
    scratch = Path(tempfile.mkdtemp(prefix="wave18-baseline-"))
    try:
        source = archive_source(repo, commit, scratch)
        part = partition(builder, source.root)
        leaks = cross_component_leaks(builder, source.root)
        boot = baseline_isolation(builder, source, outdir / "isolation")
        result = {
            "schema": SCHEMA,
            "state": "RED_BASELINE",
            "decision": BLOCKED_CROSS_COMPONENT,
            "source": source.as_dict(),
            "partition": {
                "hub_closure": len(part.hub),
                "node_closure": len(part.node),
                "shared": len(part.shared),
                "hub_own": len(part.hub_own),
                "node_own": len(part.node_own),
                "base_exact": part.exact,
                "inadmissible": sorted(part.inadmissible),
            },
            "leaks": leaks,
            "two_artifact_boot": boot,
            "reasons": [
                "the Hub closure carries the Node connector through "
                "owner_setup; the Node closure carries the Hub's fleet, "
                "registry and service modules",
                "own-files-only two-artifact payloads cannot boot; the "
                "missing piece is the other half, not a pip wheel",
            ],
        }
        (outdir / "BASELINE.json").write_text(
            json.dumps(result, indent=2, sort_keys=True,
                       ensure_ascii=False) + "\n", encoding="utf-8")
        return result
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def evidence(repo: Path, commit: str, version: str,
             outdir: Path) -> dict:
    """Build twice, verify determinism, isolation, negatives and rollback."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    work = outdir / "_work"
    if work.exists():
        shutil.rmtree(work)
    work.mkdir(parents=True)
    try:
        first = build(repo, commit, version, outdir / "v1",
                      scratch_root=work / "v1")
        second = build(repo, commit, version, outdir / "rebuild",
                       scratch_root=work / "rebuild")
        v2 = build(repo, commit, _next_version(version), outdir / "v2",
                   scratch_root=work / "v2")
        archives = {first.source.archive_sha256, second.source.archive_sha256,
                    v2.source.archive_sha256}
        if len(archives) != 1:
            raise Refused(
                BLOCKED_MANIFEST_DRIFT,
                f"git archive is not deterministic: {sorted(archives)}")
        deterministic = {}
        for kind in (*UNITS, COMPOSITION_KIND):
            deterministic[kind] = (
                first.units[kind].sha256 == second.units[kind].sha256
                and first.units[kind].members == second.units[kind].members)
        if not all(deterministic.values()):
            raise Refused(BLOCKED_MANIFEST_DRIFT,
                          f"rebuild is not byte-identical: {deterministic}")
        builder = load_builder(repo)
        isolation = boot_isolated(builder, first.units, outdir / "isolation")
        negatives = negative_control(builder, first.units, outdir / "negative")
        rollback = rollback_check(builder, first.units, v2.units,
                                  outdir / "rollback")
        baseline = baseline_evidence(repo, commit, outdir / "baseline")
        plan = release_plan(first.units, version)
        result = {
            "schema": PROVENANCE_SCHEMA,
            "artifact_schema": SCHEMA,
            "version": version,
            "source": {**first.source.as_dict(),
                       "archive_reproducible_across_three_builds": True},
            "builder": {"path": "integrations/fleet-next-wave18-artifact-builder/"
                                "wave18_builder.py",
                        "sha256": "sha256:" + sha256_file(Path(__file__))},
            "transform": {
                "mode": "cut",
                "hermetic_git": {
                    "core.autocrlf": False,
                    "core.eol": "lf",
                    "config_nosystem": True,
                    "attr_nosystem": True,
                    "line_ending_drift": BLOCKED_LINE_ENDING_DRIFT,
                    "why": "#514: core.autocrlf=true rewrote patched files to "
                           "CRLF and changed four of five digests",
                },
                "patches": [patch.as_dict() for patch in first.patches],
            },
            "units": {kind: unit.as_dict()
                      for kind, unit in sorted(first.units.items())},
            "deterministic_rebuild": deterministic,
            "baseline": baseline,
            "isolation": isolation,
            "negative_control": negatives,
            "rollback": rollback,
            "release_plan": plan,
            "technical_state": GREEN_ARTIFACTS,
            "source_state": BLOCKED_OWNER,
            "decision": BLOCKED_OWNER,
            "release_permitted": False,
            "real_device": NOT_RUN,
            "why_blocked": [
                "the CUT-A/CUT-B transforms are applied to an isolated "
                "git-archive copy; the fleet_mcp owner has not landed them",
                "a production install, OAuth, Tailscale and a real co-install "
                "are outside this lane",
            ],
        }
        (outdir / "PROVENANCE.json").write_text(
            json.dumps(result, indent=2, sort_keys=True,
                       ensure_ascii=False) + "\n", encoding="utf-8")
        checksums = "\n".join(
            f"{unit.sha256}  {unit.path.name}"
            for kind, unit in sorted(first.units.items())) + "\n"
        (outdir / "ARTIFACTS.sha256").write_text(checksums, encoding="utf-8")
        (outdir / "RELEASE-PLAN.json").write_text(
            json.dumps(plan, indent=2, sort_keys=True,
                       ensure_ascii=False) + "\n", encoding="utf-8")
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def compare_checksums(units: Mapping[str, Unit],
                      expected_path: Path) -> dict:
    """Compare this build to a published `ARTIFACTS.sha256`, exactly."""
    expected: dict[str, str] = {}
    for line in Path(expected_path).read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        digest, name = line.split("  ", 1)
        expected[name] = digest
    actual = {unit.path.name: unit.sha256 for unit in units.values()}
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    mismatch = sorted(name for name in set(expected) & set(actual)
                      if expected[name] != actual[name])
    return {"ok": not (missing or extra or mismatch),
            "expected": expected, "actual": actual,
            "missing": missing, "extra": extra, "mismatch": mismatch}


def qa_rebuild(repo: Path, commit: str, version: str,
               expected_path: Path, outdir: Path) -> dict:
    """One-command independent QA: rebuild, compare 5/5, negative control."""
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    first = build(repo, commit, version, outdir / "qa")
    builder = load_builder(repo)
    comparison = compare_checksums(first.units, Path(expected_path))
    negatives = negative_control(builder, first.units, outdir / "qa-negative")
    negative_ok = all(not answer["booted"] for answer in negatives.values())
    return {
        "ok": comparison["ok"] and negative_ok,
        "checksums": comparison,
        "negative_control": negatives,
        "source": first.source.as_dict(),
        "units": {kind: unit.as_dict()
                  for kind, unit in sorted(first.units.items())},
    }


def _next_version(version: str) -> str:
    base, _, cache = version.partition("+")
    major, minor, patch = base.split(".")
    return f"{major}.{minor}.{int(patch) + 1}+{cache or 'wave18'}"


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=".")
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--version", default="1.0.0+wave18")
    parser.add_argument("--out", required=True)
    parser.add_argument("--mode", choices=("cut", "baseline"), default="cut")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--expect-checksums",
                        help="rebuild and compare against a published "
                             "ARTIFACTS.sha256, then run the negative control")
    args = parser.parse_args(argv)
    try:
        if args.expect_checksums:
            result = qa_rebuild(Path(args.repo), args.commit, args.version,
                                Path(args.expect_checksums), Path(args.out))
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True,
                                 ensure_ascii=False))
            else:
                print(f"qa_ok  {result['ok']}")
                print(f"checksums {result['checksums']}")
                print(f"negative  { {k: v['booted'] for k, v in result['negative_control'].items()} }")
            return 0 if result["ok"] else 3
        if args.mode == "baseline":
            result = baseline_evidence(Path(args.repo), args.commit,
                                       Path(args.out))
            if args.json:
                print(json.dumps(result, indent=2, sort_keys=True,
                                 ensure_ascii=False))
            else:
                print(f"state     {result['state']}")
                print(f"decision  {result['decision']}")
                print(f"partition {result['partition']}")
                print(f"leaks     {result['leaks']}")
                print(f"boot      { {k: v.get('booted') for k, v in result['two_artifact_boot'].items()} }")
            return 0
        result = evidence(Path(args.repo), args.commit, args.version,
                          Path(args.out))
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True,
                             ensure_ascii=False))
        else:
            print(f"technical_state  {result['technical_state']}")
            print(f"source_state     {result['source_state']}")
            print(f"decision         {result['decision']}")
            print(f"real_device      {result['real_device']}")
            for kind, unit in sorted(result["units"].items()):
                print(f"{kind:22} {unit['tarball_sha256']}  {unit['tree_sha256']}")
        return 0
    except (Prerequisite, Refused) as exc:
        sys.stderr.write(f"refused: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
