#!/usr/bin/env python3
"""A sidecar release that can be taken back, and whose revoke can say no.

Wave 16 (`#502`) measured the sidecar at `fleet_mcp/deploy/next_tailscale_gate.py`
plus `fleet_mcp/deploy/next_tailscale_plan.example.json` as reproducible and
import-independent — and **not revocable**, because nothing in the tree undoes
it except a person typing `rm -rf`. That is the gap this module closes, and the
shape of the closure matters as much as the deletion:

  * a **release descriptor** pins the version, the commit, every file's
    `sha256` and one manifest digest over all of them, so "the sidecar" is a
    list of bytes rather than a directory someone remembers installing;
  * an **install receipt** is written *at install time*, inside the install
    root, recording what was created, where, on which device, on which boot and
    under whose grant;
  * an **ownership marker** written beside it, so a receipt copied into a
    foreign directory is not by itself evidence that this tool made that
    directory;
  * `revoke()` deletes **only** files that the receipt names, inside the root
    the receipt names, after every guard below has passed — and refuses, with a
    distinct code, everything else.

The refusal list is the product. A revoke that deletes whatever it finds under
a path it was handed is `rm -rf` with more typing, so every one of these is a
refusal rather than a warning:

    no grant / wrong action          BLOCKED_NO_GRANT
    floating version, short commit   BLOCKED_NOT_PINNED
    a path that leaves its root      BLOCKED_DESCRIPTOR_PATH, BLOCKED_OUTSIDE_INSTALL_ROOT
    a relative root, `/`, `$HOME`    BLOCKED_ROOT_NOT_ABSOLUTE, BLOCKED_ROOT_NOT_ISOLATED
    a linked root                    BLOCKED_ROOT_IS_SYMLINK
    a Legacy or shared root          BLOCKED_LEGACY_ROOT        (checked both directions)
    a directory that merely looks
      installed                      BLOCKED_NO_RECEIPT
    a receipt someone edited         BLOCKED_RECEIPT_TAMPERED
    a receipt for other bytes        BLOCKED_RECEIPT_MISMATCH
    a missing/edited marker          BLOCKED_OWNER_MARKER_MISSING, …_MISMATCH
    another device or boot           BLOCKED_IDENTITY_MISMATCH, BLOCKED_BOOT_MISMATCH
    a live writer                    BLOCKED_CONCURRENT_WRITER
    a link on the way to a file      BLOCKED_SYMLINK_TRAVERSAL
    a file edited after install      BLOCKED_MODIFIED_AFTER_INSTALL
    a file already gone              BLOCKED_FILE_ABSENT
    a file nobody's receipt claims   BLOCKED_FOREIGN_PRESENT

Three properties worth stating plainly:

  * **It is offline.** No socket, no service manager, no `systemctl`, no
    `shutil.rmtree`, no `os.system`. The only external process is `git`, read
    only (`rev-parse`, `cat-file`, `ls-tree`, `grep`), used to read the bytes a
    descriptor pins; it is confined to `_git()` and the release lifecycle never
    reaches it.
  * **A second revoke deletes nothing and still refuses.** Idempotence is about
    the tree, not about returning `OK`: once the receipt is gone the evidence is
    gone, and a revoke without evidence is exactly the operation this module
    exists to refuse. The second call reports `BLOCKED_NO_RECEIPT` with
    `deleted == ()`.
  * **It does not fabricate a past install.** If the real sidecar has no install
    receipt, `upstream_verdict()` says `BLOCKED_UPSTREAM` and names the changes
    the owning PR must make; if a receipt does exist, it says
    `BLOCKED_NOT_VERIFIED`, because then the missing piece is the device and not
    the artifact. It never signs a read-only simulation as a device pass. The
    receipt it looks for is a **file that is one** — a `RECEIPT_NAME` path whose
    body declares `RECEIPT_SCHEMA` — not a file that mentions the schema, since
    this module mentions it.

`install()` is here because a revoke needs something to revoke, and because the
descriptor is only meaningful if the bytes it describes are the bytes written.
It writes into a fresh, caller-supplied, isolated root — in the tests, a
`tempfile` directory. Nothing in this module knows how to install into `/opt`.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import posixpath
import re
import stat
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "serveros/fleet-next-wave17-sidecar-release.v1"
RECEIPT_SCHEMA = "serveros/fleet-sidecar-install-receipt.v1"
MARKER_SCHEMA = "serveros/fleet-sidecar-owner-marker.v1"
ARTIFACT_SCHEMA = "serveros/artifact.v1"
ARTIFACT_MANIFEST = "ARTIFACT.json"

RECEIPT_NAME = ".sidecar-install-receipt.json"
OWNER_MARKER_NAME = ".sidecar-owner.json"
LOCK_NAME = ".sidecar-install.lock"
BACKUP_DIR = ".sidecar-backup"

# ---- decisions and refusal codes ------------------------------------------- #
OK = "OK"
BLOCKED = "BLOCKED"

BLOCKED_NOT_PINNED = "BLOCKED_NOT_PINNED"
BLOCKED_DESCRIPTOR_PATH = "BLOCKED_DESCRIPTOR_PATH"
BLOCKED_NO_GRANT = "BLOCKED_NO_GRANT"
BLOCKED_ROOT_NOT_ABSOLUTE = "BLOCKED_ROOT_NOT_ABSOLUTE"
BLOCKED_ROOT_NOT_ISOLATED = "BLOCKED_ROOT_NOT_ISOLATED"
BLOCKED_ROOT_IS_SYMLINK = "BLOCKED_ROOT_IS_SYMLINK"
BLOCKED_ROOT_NOT_EMPTY = "BLOCKED_ROOT_NOT_EMPTY"
BLOCKED_LEGACY_ROOT = "BLOCKED_LEGACY_ROOT"
BLOCKED_ALREADY_INSTALLED = "BLOCKED_ALREADY_INSTALLED"
BLOCKED_UPGRADE_CHAIN = "BLOCKED_UPGRADE_CHAIN"
BLOCKED_SOURCE_DIGEST = "BLOCKED_SOURCE_DIGEST"
BLOCKED_NO_RECEIPT = "BLOCKED_NO_RECEIPT"
BLOCKED_RECEIPT_TAMPERED = "BLOCKED_RECEIPT_TAMPERED"
BLOCKED_RECEIPT_MISMATCH = "BLOCKED_RECEIPT_MISMATCH"
BLOCKED_OWNER_MARKER_MISSING = "BLOCKED_OWNER_MARKER_MISSING"
BLOCKED_OWNER_MARKER_MISMATCH = "BLOCKED_OWNER_MARKER_MISMATCH"
BLOCKED_IDENTITY_MISMATCH = "BLOCKED_IDENTITY_MISMATCH"
BLOCKED_BOOT_MISMATCH = "BLOCKED_BOOT_MISMATCH"
BLOCKED_CONCURRENT_WRITER = "BLOCKED_CONCURRENT_WRITER"
BLOCKED_OUTSIDE_INSTALL_ROOT = "BLOCKED_OUTSIDE_INSTALL_ROOT"
BLOCKED_SYMLINK_TRAVERSAL = "BLOCKED_SYMLINK_TRAVERSAL"
BLOCKED_MODIFIED_AFTER_INSTALL = "BLOCKED_MODIFIED_AFTER_INSTALL"
BLOCKED_FILE_ABSENT = "BLOCKED_FILE_ABSENT"
BLOCKED_FOREIGN_PRESENT = "BLOCKED_FOREIGN_PRESENT"
BLOCKED_BACKUP_MODIFIED = "BLOCKED_BACKUP_MODIFIED"
BLOCKED_UPSTREAM = "BLOCKED_UPSTREAM"
#: A receipt exists, so the missing piece is the device rather than the artifact.
#: Distinct from BLOCKED_UPSTREAM because that one asks a file's owner to add
#: evidence, and here the evidence is there.
BLOCKED_NOT_VERIFIED = "BLOCKED_NOT_VERIFIED"

#: Same pin as #473's artifact builder, so a version one accepts the other
#: accepts. Two spellings of "a version" would let a descriptor carry something
#: the artifact reader would have refused.
VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.\-]*)?(?:\+[A-Za-z0-9][A-Za-z0-9.\-]*)?$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: `git` verbs this module may run, and it may only run these. A reader that can
#: also `checkout`, `rm` or `reset` is a writer with a read-only reputation.
READ_ONLY_GIT = frozenset({"rev-parse", "cat-file", "ls-tree", "grep"})

#: Windows exposes a junction as a reparse point that `Path.is_symlink()` calls
#: False. Checking only `is_symlink()` would wave through exactly the shape a
#: traversal guard is for.
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


class Refused(Exception):
    """Raised when a descriptor cannot be built from what was supplied."""


# --------------------------------------------------------------------------- #
# results
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Verdict:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class InstallResult:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    written: tuple[str, ...] = ()
    receipt_path: str = ""


@dataclass(frozen=True)
class RevokeResult:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()


@dataclass(frozen=True)
class RestoreResult:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    restored: tuple[str, ...] = ()


@dataclass(frozen=True)
class UpstreamVerdict:
    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    required_changes: tuple[str, ...] = ()
    receipt_found: bool = False
    files: tuple[str, ...] = ()
    commit: str = ""
    manifest_digest: str = ""
    references: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# the descriptor
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Descriptor:
    """Exactly which bytes a release is, named so that two people agree."""

    name: str
    version: str
    commit: str
    files: tuple[tuple[str, str], ...]
    entrypoint: str

    def file_count(self) -> int:
        return len(self.files)

    def digests(self) -> dict[str, str]:
        return dict(self.files)

    def manifest_digest(self) -> str:
        """The L2 artifact recipe: `sha256  path\\n`, digest of the recipe.

        Two spaces, not a tab, and content hashes rather than git blob SHAs:
        this digest has to be recomputable from an *installed* file, where git
        is not present. #502's per-component digest uses blob SHAs for a
        different question (is the repository the same) and is not this one.
        """
        recipe = "".join(f"{digest}  {path}\n"
                         for path, digest in sorted(self.files))
        return "sha256:" + hashlib.sha256(recipe.encode("utf-8")).hexdigest()

    @classmethod
    def from_artifact(cls, artifact: Mapping[str, Any], *, commit: str) -> "Descriptor":
        """Turn a #473 L2 `ARTIFACT.json` into a descriptor without retyping it.

        `commit` is a required argument and is never defaulted: an artifact
        records a version and a tree digest, not the commit it was built from,
        and a descriptor that guessed one would pin the wrong thing in a way
        nothing downstream could detect.
        """
        if not isinstance(artifact, Mapping):
            raise Refused("an artifact is an object")
        if artifact.get("schema") != ARTIFACT_SCHEMA:
            raise Refused(f"artifact schema {artifact.get('schema')!r} is not {ARTIFACT_SCHEMA!r}")
        if not COMMIT_RE.match(commit or ""):
            raise Refused("a descriptor pins a 40-hex commit; the artifact carries none, "
                          "so the caller must supply the commit it was built from")
        version = str(artifact.get("version", ""))
        if not VERSION_RE.match(version):
            raise Refused(f"artifact version {version!r} is not a pinned version")
        member_sha = artifact.get("member_sha256")
        if not isinstance(member_sha, Mapping) or not member_sha:
            raise Refused("artifact has no member_sha256; a descriptor of no bytes is not a pin")
        entries: list[tuple[str, str]] = []
        for path, digest in sorted(member_sha.items()):
            if path == ARTIFACT_MANIFEST:
                # A file cannot carry its own digest, and #473 does not list it
                # here; treating it as a member would make install write the
                # old manifest over the new one.
                continue
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                raise Refused(f"{path}: {digest!r} is not a bare sha256 hex digest")
            entries.append((str(path), "sha256:" + digest))
        if not entries:
            raise Refused("artifact lists no members besides its own manifest")
        return cls(name=str(artifact.get("name") or "sidecar"), version=version,
                   commit=commit, files=tuple(entries),
                   entrypoint=_entry_path(artifact, [p for p, _ in entries]))


def _entry_path(artifact: Mapping[str, Any], members: Sequence[str]) -> str:
    entry = str((artifact.get("source") or {}).get("entry", ""))
    if not entry:
        return ""
    candidate = entry.replace(".", "/") + ".py"
    return candidate if candidate in members else ""


def check_descriptor(descriptor: Descriptor) -> Verdict:
    """Refuse a descriptor that does not pin bytes to a place."""
    if not VERSION_RE.match(descriptor.version or ""):
        return Verdict(BLOCKED, BLOCKED_NOT_PINNED,
                       (f"version {descriptor.version!r} is not an exact pin",))
    if not COMMIT_RE.match(descriptor.commit or ""):
        return Verdict(BLOCKED, BLOCKED_NOT_PINNED,
                       (f"commit {descriptor.commit!r} is not a 40-hex commit",))
    if not descriptor.files:
        return Verdict(BLOCKED, BLOCKED_NOT_PINNED, ("the manifest is empty",))
    seen: set[str] = set()
    for path, digest in descriptor.files:
        problem = _rel_path_problem(path)
        if problem:
            return Verdict(BLOCKED, BLOCKED_DESCRIPTOR_PATH, (problem,))
        if path in seen:
            return Verdict(BLOCKED, BLOCKED_NOT_PINNED,
                           (f"{path!r} is listed twice, so which digest wins?",))
        seen.add(path)
        if not DIGEST_RE.match(digest or ""):
            return Verdict(BLOCKED, BLOCKED_NOT_PINNED,
                           (f"{path}: {digest!r} is not sha256:<64 hex>",))
    if descriptor.entrypoint not in seen:
        return Verdict(BLOCKED, BLOCKED_NOT_PINNED,
                       (f"entrypoint {descriptor.entrypoint!r} is not one of the pinned files",))
    return Verdict(OK, OK)


def _rel_path_problem(path: str) -> str:
    """Why `path` is not a plain relative path inside a root, or '' if it is."""
    if not isinstance(path, str) or not path:
        return "an empty path is not a file"
    if "\\" in path:
        return f"{path!r} contains a backslash, so it means one thing here and another there"
    if posixpath.isabs(path):
        return f"{path!r} is absolute, so it is not inside any root"
    normal = posixpath.normpath(path)
    if normal != path:
        return f"{path!r} is not normalised (it normalises to {normal!r})"
    parts = path.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return f"{path!r} has an empty, '.' or '..' component"
    return ""


# --------------------------------------------------------------------------- #
# grant, receipt, marker
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Grant:
    grant_id: str
    operator: str
    action: str
    device_ref: str

    def covers(self, action: str) -> bool:
        return (self.action == action and bool(self.grant_id) and bool(self.operator)
                and bool(self.device_ref))


@dataclass(frozen=True)
class FileRecord:
    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class PreviousVersion:
    version: str
    manifest_digest: str
    backup_dir: str
    files: tuple[FileRecord, ...]


@dataclass(frozen=True)
class Receipt:
    name: str
    version: str
    commit: str
    manifest_digest: str
    install_root: str
    device_ref: str
    boot_id: str
    grant_id: str
    operator: str
    files: tuple[FileRecord, ...]
    previous: PreviousVersion | None
    digest: str
    raw: Mapping[str, Any] = field(default_factory=dict, repr=False)
    broken: bool = False

    def is_intact(self) -> bool:
        if self.broken or not self.raw:
            return False
        body = {key: value for key, value in self.raw.items()
                if key != "receipt_digest"}
        return receipt_digest(body) == self.digest

    def body(self) -> dict[str, Any]:
        return dict(self.raw)


def receipt_digest(body: Mapping[str, Any]) -> str:
    """Canonical JSON of the receipt body, digested.

    Canonical (sorted keys, no whitespace) so that a receipt re-serialised by a
    different writer still verifies; the digest covers every field, so editing
    `version` without recomputing it is visible as tampering rather than as a
    different receipt.
    """
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return "sha256:" + hashlib.sha256(canonical).hexdigest()


def _record(path: str, digest: str, size: int) -> dict[str, Any]:
    return {"path": path, "sha256": digest, "size": size}


def _receipt_body(descriptor: Descriptor, *, root_real: str, device_ref: str,
                  boot_id: str, grant: Grant, records: Sequence[FileRecord],
                  previous: PreviousVersion | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA,
        "name": descriptor.name,
        "version": descriptor.version,
        "commit": descriptor.commit,
        "manifest_digest": descriptor.manifest_digest(),
        "install_root": root_real,
        "device_ref": device_ref,
        "boot_id": boot_id,
        "grant_id": grant.grant_id,
        "operator": grant.operator,
        "files": [_record(rec.path, rec.sha256, rec.size) for rec in records],
        "previous": None,
    }
    if previous is not None:
        body["previous"] = {
            "version": previous.version,
            "manifest_digest": previous.manifest_digest,
            "backup_dir": previous.backup_dir,
            "files": [_record(rec.path, rec.sha256, rec.size)
                      for rec in previous.files],
        }
    return body


def read_receipt(root: Path | str) -> Receipt | None:
    """The receipt in `root`, or None. An unreadable receipt is `broken`."""
    path = Path(root) / RECEIPT_NAME
    try:
        if _is_link(path) or not path.is_file():
            return None
        raw = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return Receipt(name="", version="", commit="", manifest_digest="",
                       install_root="", device_ref="", boot_id="", grant_id="",
                       operator="", files=(), previous=None, digest="",
                       raw={}, broken=True)
    if not isinstance(raw, Mapping):
        return Receipt(name="", version="", commit="", manifest_digest="",
                       install_root="", device_ref="", boot_id="", grant_id="",
                       operator="", files=(), previous=None, digest="",
                       raw={}, broken=True)
    return _receipt_from(raw)


def _receipt_from(raw: Mapping[str, Any]) -> Receipt:
    files: list[FileRecord] = []
    for entry in raw.get("files") or ():
        if isinstance(entry, Mapping):
            files.append(FileRecord(path=str(entry.get("path", "")),
                                    sha256=str(entry.get("sha256", "")),
                                    size=int(entry.get("size", -1) or -1)))
    previous = None
    prev_raw = raw.get("previous")
    if isinstance(prev_raw, Mapping):
        prev_files = tuple(FileRecord(path=str(e.get("path", "")),
                                      sha256=str(e.get("sha256", "")),
                                      size=int(e.get("size", -1) or -1))
                           for e in prev_raw.get("files") or ()
                           if isinstance(e, Mapping))
        previous = PreviousVersion(version=str(prev_raw.get("version", "")),
                                   manifest_digest=str(prev_raw.get("manifest_digest", "")),
                                   backup_dir=str(prev_raw.get("backup_dir", "")),
                                   files=prev_files)
    return Receipt(name=str(raw.get("name", "")),
                   version=str(raw.get("version", "")),
                   commit=str(raw.get("commit", "")),
                   manifest_digest=str(raw.get("manifest_digest", "")),
                   install_root=str(raw.get("install_root", "")),
                   device_ref=str(raw.get("device_ref", "")),
                   boot_id=str(raw.get("boot_id", "")),
                   grant_id=str(raw.get("grant_id", "")),
                   operator=str(raw.get("operator", "")),
                   files=tuple(files), previous=previous,
                   digest=str(raw.get("receipt_digest", "")),
                   raw=dict(raw), broken=False)


def _read_marker(root: Path) -> Mapping[str, Any] | None:
    path = root / OWNER_MARKER_NAME
    try:
        if _is_link(path) or not path.is_file():
            return None
        body = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return body if isinstance(body, Mapping) else None


# --------------------------------------------------------------------------- #
# filesystem facts
# --------------------------------------------------------------------------- #
def _is_link(path: Path) -> bool:
    """True for a symlink **and** for a Windows reparse point.

    `Path.is_symlink()` is False for a junction, which is a directory link a
    user without `SeCreateSymbolicLinkPrivilege` can still create. A guard that
    only asked `is_symlink()` would let a junction walk out of the root.
    """
    try:
        info = os.lstat(path)
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return True
    return bool(getattr(info, "st_file_attributes", 0) & FILE_ATTRIBUTE_REPARSE_POINT)


def _digest_file(path: Path) -> tuple[str, int, bool]:
    """(digest, size, readable) for a regular file, without following links."""
    if _is_link(path) or not path.is_file():
        return "", -1, False
    try:
        body = path.read_bytes()
    except OSError:
        return "", -1, False
    return "sha256:" + hashlib.sha256(body).hexdigest(), len(body), True


def _root_real(root: Path) -> str:
    return os.path.realpath(str(root))


def _root_refusal(root: Path, legacy_roots: Iterable[Path]) -> Verdict | None:
    """Everything wrong with `root` itself, before a single file is considered."""
    if not str(root):
        return Verdict(BLOCKED, BLOCKED_ROOT_NOT_ABSOLUTE, ("no root was named",))
    if not root.is_absolute():
        return Verdict(BLOCKED, BLOCKED_ROOT_NOT_ABSOLUTE,
                       (f"{str(root)!r} is relative, so it means one thing per cwd",))
    anchor = Path(os.path.abspath(os.sep))
    home = Path.home()
    plain = Path(os.path.abspath(str(root)))
    for forbidden, why in ((anchor, "the filesystem root"),
                           (home, "the operator's home directory")):
        if plain == forbidden or plain in forbidden.parents:
            return Verdict(BLOCKED, BLOCKED_ROOT_NOT_ISOLATED,
                           (f"{str(root)!r} is {why}, which is never a sidecar root",))
    if _is_link(root):
        return Verdict(BLOCKED, BLOCKED_ROOT_IS_SYMLINK,
                       (f"{str(root)!r} is a link; a root reached through one is not "
                        f"a root this receipt can bound",))
    root_real = _root_real(root)
    for legacy in legacy_roots:
        legacy_real = _root_real(Path(legacy))
        if root_real == legacy_real:
            return Verdict(BLOCKED, BLOCKED_LEGACY_ROOT,
                           (f"{str(root)!r} is a Legacy or shared root",))
        if _inside(root_real, legacy_real):
            return Verdict(BLOCKED, BLOCKED_LEGACY_ROOT,
                           (f"{str(root)!r} is inside the Legacy root {str(legacy)!r}",))
        if _inside(legacy_real, root_real):
            return Verdict(BLOCKED, BLOCKED_LEGACY_ROOT,
                           (f"{str(root)!r} contains the Legacy root {str(legacy)!r}; "
                            f"deleting here would reach it",))
    return None


def _inside(child: str, parent: str) -> bool:
    """Slash-boundary containment, so `/opt/fleet-next` is not inside `/opt/fleet`."""
    child = child.replace("\\", "/").rstrip("/")
    parent = parent.replace("\\", "/").rstrip("/")
    return child != parent and child.startswith(parent + "/")


def _components(root: Path, rel: str) -> list[Path]:
    parts = rel.split("/")
    return [root.joinpath(*parts[: index + 1]) for index in range(len(parts))]


# --------------------------------------------------------------------------- #
# install
# --------------------------------------------------------------------------- #
def install(descriptor: Descriptor, root: Path | str,
            sources: Mapping[str, bytes], *, grant: Grant | None,
            device_ref: str, boot_id: str,
            legacy_roots: Iterable[Path] = (),
            replace: Receipt | None = None) -> InstallResult:
    """Write the pinned bytes and the receipt that makes them revocable.

    `sources` is supplied by the caller rather than read from a directory: this
    module never decides on its own which bytes are "the release", and a source
    that does not match the pin is refused before the root is even created.
    """
    verdict = check_descriptor(descriptor)
    if verdict.decision != OK:
        return InstallResult(BLOCKED, verdict.code, verdict.reasons)
    if grant is None or not grant.covers("install"):
        return InstallResult(BLOCKED, BLOCKED_NO_GRANT,
                             ("an install needs a grant whose action is 'install'",))
    root = Path(root)
    refusal = _root_refusal(root, legacy_roots)
    if refusal is not None:
        return InstallResult(BLOCKED, refusal.code, refusal.reasons)
    if _is_link(root / LOCK_NAME) or (root / LOCK_NAME).exists():
        return InstallResult(BLOCKED, BLOCKED_CONCURRENT_WRITER,
                             (f"{LOCK_NAME} is present: another writer holds this root",))

    missing: list[str] = []
    for rel, pinned in descriptor.files:
        body = sources.get(rel)
        if body is None:
            missing.append(f"{rel}: not supplied")
        elif "sha256:" + hashlib.sha256(body).hexdigest() != pinned:
            missing.append(f"{rel}: supplied bytes are not the pinned bytes")
    if missing:
        return InstallResult(BLOCKED, BLOCKED_SOURCE_DIGEST, tuple(missing))

    existing = read_receipt(root)
    previous: PreviousVersion | None = None
    if existing is not None and replace is None:
        return InstallResult(BLOCKED, BLOCKED_ALREADY_INSTALLED,
                             (f"{root} already carries a receipt for {existing.version}",))
    if replace is not None:
        if not existing or existing.digest != replace.digest:
            return InstallResult(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                                 ("the receipt handed in is not the one in the root",))
        if not existing.is_intact():
            return InstallResult(BLOCKED, BLOCKED_RECEIPT_TAMPERED,
                                 ("the receipt in the root does not verify",))
        if existing.previous is not None:
            return InstallResult(BLOCKED, BLOCKED_UPGRADE_CHAIN,
                                 ("this receipt already remembers one previous version; "
                                  "a second upgrade would overwrite that memory",))
        if existing.install_root != _root_real(root):
            return InstallResult(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                                 (f"the receipt names {existing.install_root!r}, not {_root_real(root)!r}",))
        if existing.device_ref != device_ref:
            return InstallResult(BLOCKED, BLOCKED_IDENTITY_MISMATCH,
                                 (f"the receipt belongs to {existing.device_ref!r}",))
        if existing.boot_id != boot_id:
            return InstallResult(BLOCKED, BLOCKED_BOOT_MISMATCH,
                                 (f"the receipt was written on boot {existing.boot_id!r}",))
        for probe in _file_problems(root, existing.files):
            return InstallResult(BLOCKED, probe.code, probe.reasons)
        previous = PreviousVersion(version=existing.version,
                                   manifest_digest=existing.manifest_digest,
                                   backup_dir=f"{BACKUP_DIR}/{existing.version}",
                                   files=existing.files)

    if existing is None and root.exists() and any(root.iterdir()):
        return InstallResult(BLOCKED, BLOCKED_ROOT_NOT_EMPTY,
                             (f"{root} is not empty and carries no receipt",))

    created_root = not root.exists()
    root.mkdir(parents=True, exist_ok=True)
    (root / LOCK_NAME).write_bytes(b"writer=install\n")
    written: list[str] = []
    records: list[FileRecord] = []
    try:
        if previous is not None:
            for record in previous.files:
                backup = root / previous.backup_dir / record.path
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes((root / record.path).read_bytes())
        for rel, _pinned in descriptor.files:
            body = sources[rel]
            target = root / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            # `write_bytes`, never `write_text`: the digest is over these bytes,
            # and `write_text` would translate newlines on Windows and move it.
            target.write_bytes(body)
            written.append(rel)
            records.append(FileRecord(path=rel,
                                      sha256="sha256:" + hashlib.sha256(body).hexdigest(),
                                      size=len(body)))
        body = _receipt_body(descriptor, root_real=_root_real(root),
                             device_ref=device_ref, boot_id=boot_id, grant=grant,
                             records=records, previous=previous)
        body["receipt_digest"] = receipt_digest(body)
        (root / RECEIPT_NAME).write_bytes(
            json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8"))
        marker = {"schema": MARKER_SCHEMA, "install_root": body["install_root"],
                  "device_ref": device_ref, "name": descriptor.name,
                  "version": descriptor.version,
                  "manifest_digest": body["manifest_digest"],
                  "receipt_digest": body["receipt_digest"]}
        (root / OWNER_MARKER_NAME).write_bytes(
            json.dumps(marker, sort_keys=True, indent=2).encode("utf-8"))
    finally:
        lock = root / LOCK_NAME
        if lock.exists() or _is_link(lock):
            try:
                lock.unlink()
            except OSError:
                pass
        if created_root and not any(root.iterdir()):
            try:
                root.rmdir()
            except OSError:
                pass
    return InstallResult(OK, OK, written=tuple(sorted(written)),
                         receipt_path=str(root / RECEIPT_NAME))


def _file_problems(root: Path, records: Sequence[FileRecord]) -> list[Verdict]:
    """Every reason these records do not describe what is on disk."""
    problems: list[Verdict] = []
    for record in records:
        for part in _components(root, record.path):
            if _is_link(part):
                problems.append(Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                                        (f"{record.path}: {part} is a link",)))
                break
        else:
            digest, size, readable = _digest_file(root / record.path)
            if not readable:
                problems.append(Verdict(BLOCKED, BLOCKED_FILE_ABSENT,
                                        (f"{record.path} is not a readable regular file",)))
            elif digest != record.sha256 or size != record.size:
                problems.append(Verdict(BLOCKED, BLOCKED_MODIFIED_AFTER_INSTALL,
                                        (f"{record.path} changed after install "
                                         f"(receipt {record.sha256}, disk {digest})",)))
    return problems


# --------------------------------------------------------------------------- #
# revoke: guards, in an order that refuses early
# --------------------------------------------------------------------------- #
@dataclass
class _Ctx:
    action: str
    descriptor: Descriptor
    root: Path
    grant: Grant | None
    device_ref: str
    boot_id: str
    legacy_roots: tuple[Path, ...]
    receipt: Receipt | None = None
    marker: Mapping[str, Any] | None = None
    #: Filled in right after construction: `os.path.realpath` of the root, so a
    #: receipt and the directory it names are compared after links are resolved.
    root_real: str = ""


def _guard_grant(ctx: _Ctx) -> Verdict | None:
    if ctx.grant is None or not ctx.grant.covers(ctx.action):
        return Verdict(BLOCKED, BLOCKED_NO_GRANT,
                       (f"a {ctx.action} needs a grant whose action is {ctx.action!r}",))
    return None


def _guard_descriptor(ctx: _Ctx) -> Verdict | None:
    verdict = check_descriptor(ctx.descriptor)
    return None if verdict.decision == OK else verdict


def _guard_root(ctx: _Ctx) -> Verdict | None:
    return _root_refusal(ctx.root, ctx.legacy_roots)


def _guard_receipt_present(ctx: _Ctx) -> Verdict | None:
    if ctx.receipt is None:
        return Verdict(BLOCKED, BLOCKED_NO_RECEIPT,
                       (f"{ctx.root} carries no {RECEIPT_NAME}; a directory that merely "
                        f"looks installed is not evidence that it was installed",))
    return None


def _guard_receipt_intact(ctx: _Ctx) -> Verdict | None:
    if not ctx.receipt.is_intact():
        return Verdict(BLOCKED, BLOCKED_RECEIPT_TAMPERED,
                       (f"{RECEIPT_NAME} does not verify against its own digest",))
    return None


def _guard_receipt_match(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    if r.install_root != ctx.root_real:
        return Verdict(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                       (f"the receipt names {r.install_root!r}, not {ctx.root_real!r}",))
    if r.version != ctx.descriptor.version:
        return Verdict(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                       (f"the receipt is for {r.version!r}, the descriptor for "
                        f"{ctx.descriptor.version!r}",))
    if r.commit != ctx.descriptor.commit:
        return Verdict(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                       (f"the receipt pins commit {r.commit!r}, the descriptor "
                        f"{ctx.descriptor.commit!r}",))
    if r.manifest_digest != ctx.descriptor.manifest_digest():
        return Verdict(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                       ("the receipt's manifest is not this descriptor's manifest",))
    return None


def _guard_owner_marker(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    if ctx.marker is None:
        return Verdict(BLOCKED, BLOCKED_OWNER_MARKER_MISSING,
                       (f"{OWNER_MARKER_NAME} is missing; ownership is asserted by two "
                        f"files, and one of them is gone",))
    marker = ctx.marker
    if (str(marker.get("install_root", "")) != ctx.root_real
            or str(marker.get("device_ref", "")) != r.device_ref
            or str(marker.get("receipt_digest", "")) != r.digest
            or str(marker.get("version", "")) != r.version):
        return Verdict(BLOCKED, BLOCKED_OWNER_MARKER_MISMATCH,
                       (f"{OWNER_MARKER_NAME} disagrees with the receipt about this root, "
                        f"device, version or receipt digest",))
    return None


def _guard_identity(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    if ctx.device_ref != r.device_ref:
        return Verdict(BLOCKED, BLOCKED_IDENTITY_MISMATCH,
                       (f"this is {ctx.device_ref!r}; the receipt belongs to {r.device_ref!r}",))
    assert ctx.grant is not None
    if ctx.grant.device_ref != r.device_ref:
        return Verdict(BLOCKED, BLOCKED_IDENTITY_MISMATCH,
                       (f"the grant is for {ctx.grant.device_ref!r}, the receipt for "
                        f"{r.device_ref!r}",))
    if ctx.boot_id != r.boot_id:
        return Verdict(BLOCKED, BLOCKED_BOOT_MISMATCH,
                       (f"this is boot {ctx.boot_id!r}; the receipt was written on "
                        f"{r.boot_id!r}",))
    return None


def _guard_writer(ctx: _Ctx) -> Verdict | None:
    lock = ctx.root / LOCK_NAME
    if lock.exists() or _is_link(lock):
        return Verdict(BLOCKED, BLOCKED_CONCURRENT_WRITER,
                       (f"{LOCK_NAME} is present: a writer holds this root",))
    return None


def _guard_containment(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    for record in r.files:
        problem = _rel_path_problem(record.path)
        if problem:
            return Verdict(BLOCKED, BLOCKED_OUTSIDE_INSTALL_ROOT,
                           (f"the receipt carries a path that is not inside the root: {problem}",))
        resolved = os.path.realpath(str(ctx.root / record.path))
        if not (resolved == ctx.root_real or _inside(resolved, ctx.root_real)):
            return Verdict(BLOCKED, BLOCKED_OUTSIDE_INSTALL_ROOT,
                           (f"{record.path} resolves to {resolved!r}, outside "
                            f"{ctx.root_real!r}",))
    return None


def _guard_symlink(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    for record in r.files:
        for part in _components(ctx.root, record.path):
            if _is_link(part):
                return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                               (f"{part} is a link on the way to {record.path}",))
    return None


def _guard_files(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    for record in r.files:
        digest, size, readable = _digest_file(ctx.root / record.path)
        if not readable:
            return Verdict(BLOCKED, BLOCKED_FILE_ABSENT,
                           (f"{record.path} is already gone; a half-installed tree is "
                            f"not something this revoke may finish by guessing",))
        if digest != record.sha256 or size != record.size:
            return Verdict(BLOCKED, BLOCKED_MODIFIED_AFTER_INSTALL,
                           (f"{record.path} changed after install (receipt "
                            f"{record.sha256}, disk {digest}); deleting it would delete "
                            f"someone else's edit",))
    return None


def _allowed_entries(receipt: Receipt) -> set[str]:
    """Every path this receipt owns, including the two files it was written in."""
    allowed = {record.path for record in receipt.files}
    allowed |= {RECEIPT_NAME, OWNER_MARKER_NAME}
    if receipt.previous is not None:
        prefix = receipt.previous.backup_dir.strip("/")
        allowed.add(prefix)
        allowed |= {f"{prefix}/{record.path}" for record in receipt.previous.files}
    return allowed


def _guard_foreign(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    allowed = _allowed_entries(r)
    extra: list[str] = []
    for path in sorted(ctx.root.rglob("*")):
        rel = path.relative_to(ctx.root).as_posix()
        if path.is_dir() and not _is_link(path):
            continue
        if rel not in allowed:
            extra.append(rel)
    if extra:
        return Verdict(BLOCKED, BLOCKED_FOREIGN_PRESENT,
                       (f"{ctx.root} holds files this receipt does not own: "
                        f"{', '.join(sorted(extra))}",))
    return None


#: The revoke guard pipeline, declared as **names**. Named functions rather
#: than inline `if`s, so a reviewer reads one ordered list; names rather than
#: function objects, because a tuple of objects is bound once at import and
#: rebinding a guard would then leave the pipeline unchanged — which makes
#: "disable exactly one guard and see what turns red" quietly unmeasurable.
#: `revoke_guards()` resolves the names at call time; a test asserts that.
REVOKE_GUARDS = (
    "_guard_grant",
    "_guard_descriptor",
    "_guard_root",
    "_guard_receipt_present",
    "_guard_receipt_intact",
    "_guard_receipt_match",
    "_guard_owner_marker",
    "_guard_identity",
    "_guard_writer",
    # Symlink before containment: a realpath that leaves the root is also caught
    # by `_guard_containment`, but "there is a link in the way" is the precise
    # diagnosis and the one an operator can act on.
    "_guard_symlink",
    "_guard_containment",
    "_guard_files",
    "_guard_foreign",
)

#: The restore pipeline. Restore is stricter than revoke: it must also agree
#: with what the receipt remembers and with the backup it is about to write.
RESTORE_GUARDS = (
    "_guard_grant",
    "_guard_descriptor",
    "_guard_root",
    "_guard_receipt_present",
    "_guard_receipt_intact",
    "_guard_install_root_match",
    "_guard_previous_match",
    "_guard_owner_marker",
    "_guard_identity",
    "_guard_writer",
    "_guard_symlink",
    "_guard_containment",
    "_guard_files",
    "_guard_backup",
    "_guard_foreign",
)


def _pipeline(names: tuple[str, ...]) -> tuple:
    """Resolve guard names against this module's globals, at call time."""
    resolved = []
    for name in names:
        guard = globals().get(name)
        if not callable(guard):
            raise AssertionError(f"guard {name!r} is not a function in this module")
        resolved.append(guard)
    return tuple(resolved)


def revoke_guards() -> tuple:
    """The revoke pipeline as it stands right now."""
    return _pipeline(REVOKE_GUARDS)


def restore_guards() -> tuple:
    """The restore pipeline as it stands right now."""
    return _pipeline(RESTORE_GUARDS)


def revoke(descriptor: Descriptor, root: Path | str, *, grant: Grant | None,
           device_ref: str, boot_id: str,
           legacy_roots: Iterable[Path] = ()) -> RevokeResult:
    """Delete the pinned sidecar, if and only if this root can prove it made it."""
    ctx = _Ctx(action="revoke", descriptor=descriptor, root=Path(root),
               grant=grant, device_ref=device_ref, boot_id=boot_id,
               legacy_roots=tuple(Path(p) for p in legacy_roots))
    ctx.root_real = _root_real(ctx.root)
    ctx.receipt = read_receipt(ctx.root) if ctx.root.is_dir() else None
    ctx.marker = _read_marker(ctx.root) if ctx.root.is_dir() else None

    for guard in revoke_guards():
        refusal = guard(ctx)
        if refusal is not None:
            return RevokeResult(refusal.decision, refusal.code, refusal.reasons)

    assert ctx.receipt is not None
    deleted = _delete_owned(ctx.root, ctx.receipt)
    return RevokeResult(OK, OK,
                        (f"deleted {len(deleted)} file(s) this receipt owned",),
                        tuple(sorted(deleted)))


def _delete_owned(root: Path, receipt: Receipt) -> list[str]:
    """Unlink exactly the receipt's files, then the evidence, then the root.

    No `rmtree`: every path here came from a verified receipt and was re-checked
    by the guards. Directories are removed only when they are empty, so a
    directory someone put a file in survives with that file in it.
    """
    deleted: list[str] = []
    for record in receipt.files:
        target = root / record.path
        if target.exists():
            target.unlink()
            deleted.append(record.path)
    if receipt.previous is not None:
        backup = root / receipt.previous.backup_dir
        if backup.is_dir() and not _is_link(backup):
            for record in receipt.previous.files:
                path = backup / record.path
                if path.exists():
                    path.unlink()
    _prune_empty_dirs(root)
    for name in (OWNER_MARKER_NAME, RECEIPT_NAME):
        path = root / name
        if path.exists() and not _is_link(path):
            path.unlink()
    _prune_empty_dirs(root)
    try:
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass
    return deleted


def _prune_empty_dirs(root: Path) -> None:
    """Remove empty directories under `root`, deepest first, never through a link.

    The backup directory is nested (`.sidecar-backup/<version>/…`), so a
    fixed list of parents would leave the outer shell behind and the root would
    survive an otherwise complete revoke.
    """
    for current, _dirs, _files in os.walk(root, topdown=False, followlinks=False):
        path = Path(current)
        if path == root or _is_link(path):
            continue
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# restore: undo one upgrade, not two
# --------------------------------------------------------------------------- #
def restore(previous: Descriptor, root: Path | str, *, grant: Grant | None,
            device_ref: str, boot_id: str,
            legacy_roots: Iterable[Path] = ()) -> RestoreResult:
    """Put back the bytes the receipt says were there before the last upgrade.

    Depth is exactly one. The receipt records one previous version and one
    backup directory, so a second restore has nothing to restore — pretending
    otherwise would be a rollback claim the artifact cannot back.
    """
    ctx = _Ctx(action="restore", descriptor=previous, root=Path(root),
               grant=grant, device_ref=device_ref, boot_id=boot_id,
               legacy_roots=tuple(Path(p) for p in legacy_roots))
    ctx.root_real = _root_real(ctx.root)
    ctx.receipt = read_receipt(ctx.root) if ctx.root.is_dir() else None
    ctx.marker = _read_marker(ctx.root) if ctx.root.is_dir() else None

    for guard in restore_guards():
        refusal = guard(ctx)
        if refusal is not None:
            return RestoreResult(refusal.decision, refusal.code, refusal.reasons)

    receipt = ctx.receipt
    assert receipt is not None
    previous_version = receipt.previous
    assert previous_version is not None

    restored: list[str] = []
    (ctx.root / LOCK_NAME).write_bytes(b"writer=restore\n")
    try:
        backup_root = ctx.root / previous_version.backup_dir
        keep = {record.path for record in previous_version.files}
        for record in receipt.files:
            if record.path not in keep:
                target = ctx.root / record.path
                if target.exists():
                    target.unlink()
        for record in previous_version.files:
            body = (backup_root / record.path).read_bytes()
            target = ctx.root / record.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            restored.append(record.path)
        body = _receipt_body(previous, root_real=ctx.root_real,
                             device_ref=device_ref, boot_id=boot_id,
                             grant=grant, records=previous_version.files,
                             previous=None)
        body["receipt_digest"] = receipt_digest(body)
        (ctx.root / RECEIPT_NAME).write_bytes(
            json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8"))
        marker = {"schema": MARKER_SCHEMA, "install_root": body["install_root"],
                  "device_ref": device_ref, "name": previous.name,
                  "version": previous.version,
                  "manifest_digest": body["manifest_digest"],
                  "receipt_digest": body["receipt_digest"]}
        (ctx.root / OWNER_MARKER_NAME).write_bytes(
            json.dumps(marker, sort_keys=True, indent=2).encode("utf-8"))
        backup = ctx.root / previous_version.backup_dir
        for record in previous_version.files:
            path = backup / record.path
            if path.exists():
                path.unlink()
        # The backup has been consumed; leaving its shell behind would make the
        # next upgrade look like a chain it is not.
        _prune_empty_dirs(ctx.root)
    finally:
        lock = ctx.root / LOCK_NAME
        if lock.exists():
            try:
                lock.unlink()
            except OSError:
                pass
    return RestoreResult(OK, OK,
                         (f"restored {len(restored)} file(s) to {previous.version}",),
                         tuple(sorted(restored)))


def _guard_install_root_match(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    if r.install_root != ctx.root_real:
        return Verdict(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                       (f"the receipt names {r.install_root!r}, not {ctx.root_real!r}",))
    return None


def _guard_previous_match(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    if r.previous is None:
        return Verdict(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                       (f"the installed {r.version} records no previous version",))
    if (r.previous.version != ctx.descriptor.version
            or r.previous.manifest_digest != ctx.descriptor.manifest_digest()):
        return Verdict(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                       (f"the receipt remembers {r.previous.version!r}, not "
                        f"{ctx.descriptor.version!r}",))
    return None


def _guard_backup(ctx: _Ctx) -> Verdict | None:
    """The backup is evidence, so it is verified exactly like the install."""
    r = ctx.receipt
    assert r is not None
    assert r.previous is not None
    for part in _components(ctx.root, r.previous.backup_dir):
        if _is_link(part):
            return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                           (f"{part} is a link on the way to the backup",))
    backup = ctx.root / r.previous.backup_dir
    for record in r.previous.files:
        problem = _rel_path_problem(record.path)
        if problem:
            return Verdict(BLOCKED, BLOCKED_OUTSIDE_INSTALL_ROOT,
                           (f"the backup carries a path that leaves the root: {problem}",))
        digest, size, readable = _digest_file(backup / record.path)
        if not readable:
            return Verdict(BLOCKED, BLOCKED_BACKUP_MODIFIED,
                           (f"backup of {record.path} is missing or unreadable",))
        if digest != record.sha256 or size != record.size:
            return Verdict(BLOCKED, BLOCKED_BACKUP_MODIFIED,
                           (f"backup of {record.path} is not the bytes that were backed "
                            f"up (receipt {record.sha256}, disk {digest})",))
    return None


# --------------------------------------------------------------------------- #
# the Legacy contract, held still
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class LegacyContract:
    session: Any
    run: Any
    deliveries: tuple[str, ...]


def observe_legacy(gateway: Any, *, session_key: str, run_key: str) -> LegacyContract:
    """Read the Legacy control path **without sending**.

    Reading and sending are separated on purpose: comparing a write by
    re-performing it is how a test becomes the second delivery it was written to
    detect. The caller sends once, before the experiment, and this function
    watches the gateway's delivery ledger instead.
    """
    return LegacyContract(session=gateway.get_session(session_key),
                          run=gateway.get_run(run_key),
                          deliveries=tuple(getattr(gateway, "deliveries", ())))


def legacy_drift(before: LegacyContract, after: LegacyContract) -> tuple[str, ...]:
    """Which of `get_session` / `get_run` / `send` moved, in that order."""
    drifted: list[str] = []
    if before.session != after.session:
        drifted.append("get_session")
    if before.run != after.run:
        drifted.append("get_run")
    if before.deliveries != after.deliveries:
        drifted.append("send")
    return tuple(drifted)


# --------------------------------------------------------------------------- #
# reading the repository (the only external process, and it is read-only)
# --------------------------------------------------------------------------- #
def _git(repo: Path | str, args: Sequence[str], *, allow_no_match: bool = False) -> bytes:
    if not args or args[0] not in READ_ONLY_GIT:
        raise AssertionError(f"refusing a non-read git verb: {args[0] if args else None!r}")
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
                          check=False)
    if proc.returncode != 0 and not (allow_no_match and proc.returncode == 1):
        raise Refused(f"git {' '.join(args)} failed: "
                      f"{proc.stderr.decode('utf-8', 'replace').strip()}")
    return proc.stdout


def _grep_paths(repo: Path | str, pattern: str, commit: str) -> tuple[str, ...]:
    """Paths whose content contains `pattern`, without the `commit:` prefix.

    `git grep` against a revision prints `<commit>:<path>`, and a path is what
    the caller wants to read; keeping the prefix would make every reason line
    start with a 40-hex string.
    """
    out = _git(repo, ["grep", "-l", "-F", pattern, commit],
               allow_no_match=True).decode("utf-8", "replace").split()
    return tuple(sorted(line.split(":", 1)[1] if line.startswith(commit + ":") else line
                        for line in out))


def _tree_paths(repo: Path | str, commit: str) -> tuple[str, ...]:
    """Every path in `commit`, recursively."""
    raw = _git(repo, ["ls-tree", "-r", "-z", "--name-only", commit])
    return tuple(path for path in raw.decode("utf-8", "surrogateescape").split("\0")
                 if path)


def _receipt_like_files(repo: Path | str, commit: str) -> tuple[str, ...]:
    """Files *named* like an install receipt, whatever they contain."""
    return tuple(path for path in _tree_paths(repo, commit)
                 if posixpath.basename(path) == RECEIPT_NAME)


def _receipts_in_tree(repo: Path | str, commit: str) -> tuple[str, ...]:
    """Paths that **are** install receipts, not paths that mention the schema.

    A `git grep` for the schema string matches the module that defines it —
    this one — so the first version of this check reported a receipt for its own
    source code. It looked right locally only because the module was still
    untracked: `git grep <rev>` reads a **commit**, not a worktree, so the
    answer depended on whether the file had been staged. The artifact is what
    counts — a file named `RECEIPT_NAME` whose body declares `RECEIPT_SCHEMA`.
    A mention of the schema is a mention.
    """
    found: list[str] = []
    for path in _receipt_like_files(repo, commit):
        try:
            body = json.loads(_git(repo, ["cat-file", "blob", f"{commit}:{path}"]))
        except (Refused, ValueError):
            continue
        if isinstance(body, Mapping) and body.get("schema") == RECEIPT_SCHEMA:
            found.append(path)
    return tuple(sorted(found))


def _commit_of(repo: Path | str, ref: str) -> str:
    commit = _git(repo, ["rev-parse", ref]).decode("ascii", "replace").strip()
    if not COMMIT_RE.match(commit):
        raise Refused(f"{ref!r} does not resolve to a commit ({commit!r})")
    return commit


def _blob(repo: Path | str, commit: str, path: str) -> bytes:
    return _git(repo, ["cat-file", "blob", f"{commit}:{path}"])


def _file_records(repo: Path | str, commit: str,
                  files: Sequence[str]) -> tuple[FileRecord, ...]:
    records = []
    for path in files:
        body = _blob(repo, commit, path)
        records.append(FileRecord(path=path,
                                  sha256="sha256:" + hashlib.sha256(body).hexdigest(),
                                  size=len(body)))
    return tuple(records)


def _manifest_of(records: Sequence[FileRecord]) -> str:
    recipe = "".join(f"{record.sha256}  {record.path}\n"
                     for record in sorted(records, key=lambda r: r.path))
    return "sha256:" + hashlib.sha256(recipe.encode("utf-8")).hexdigest()


def descriptor_from_git(repo: Path | str, ref: str, files: Sequence[str], *,
                        version: str, name: str = "sidecar") -> Descriptor:
    """Pin the files as they are in **git**, not in the worktree.

    Blob bytes are what the commit says; a worktree read can differ by line
    ending and would move every digest the moment someone cloned on Windows.
    """
    commit = _commit_of(repo, ref)
    records = _file_records(repo, commit, files)
    entrypoint = next((record.path for record in records
                       if record.path.endswith(".py")), files[0] if files else "")
    return Descriptor(name=name, version=version, commit=commit,
                      files=tuple(sorted((record.path, record.sha256)
                                         for record in records)),
                      entrypoint=entrypoint)


#: `revoke` is what #502 found missing, so the verdict is about the *evidence*
#: for a revoke, not about the files being present.
REQUIRED_CHANGES: tuple[str, ...] = (
    "write an install receipt (schema serveros/fleet-sidecar-install-receipt.v1) into "
    "the install_root at install time, before the sidecar serves anything",
    "write the ownership marker beside it, naming the same install_root, device_ref "
    "and receipt digest",
    "pin the release as a descriptor (version, 40-hex commit, per-file sha256, manifest "
    "digest) instead of a directory path",
    "install into its own root, and refuse a root that is, contains or is inside the "
    "Legacy root",
    "call revoke() from this module as the rollback, and keep no `rm -rf` path in the "
    "runbook",
)


def upstream_verdict(repo: Path | str, ref: str,
                     files: Sequence[str]) -> UpstreamVerdict:
    """Is the real sidecar revocable *today*? Answered from the repository only.

    The question this must never answer by assumption is "was it installed
    once". Absent a receipt, the answer is `BLOCKED_UPSTREAM`, with the changes
    the owning PR has to make — and explicitly **not verified**, because an
    offline reader cannot witness an install that has no record.
    """
    commit = _commit_of(repo, ref)
    records = _file_records(repo, commit, files)
    receipts = _receipts_in_tree(repo, commit)
    lookalikes = _receipt_like_files(repo, commit)
    name = posixpath.basename(files[0])
    reference_hits = _grep_paths(repo, name, commit)
    mentions = (f"{name} is mentioned by {len(reference_hits)} file(s) at {commit[:8]} — "
                f"{', '.join(reference_hits) or 'nothing'} — and a mention is not an "
                f"install: an installer is what would leave a receipt, and that is the "
                f"artifact being looked for")

    if receipts:
        return UpstreamVerdict(
            decision=BLOCKED, code=BLOCKED_NOT_VERIFIED,
            reasons=(f"an install receipt exists at {', '.join(receipts)}; a revoke can be "
                     f"attempted against the root it names, so the missing piece is no "
                     f"longer the receipt but the device",
                     "this reader will not run a revoke and could not witness one, so the "
                     "result is NOT VERIFIED; this is not a real-device pass",
                     mentions),
            required_changes=(), receipt_found=True, files=tuple(files),
            commit=commit, manifest_digest=_manifest_of(records),
            references=tuple(sorted(reference_hits)))

    reasons = [
        f"no install receipt appears anywhere at {commit[:8]}: a file named "
        f"{RECEIPT_NAME} whose body declares schema {RECEIPT_SCHEMA} is what an "
        f"installer leaves behind, and there is none — so no record of a real "
        f"install exists for these {len(files)} file(s)",
        mentions,
        "an offline reader cannot witness an install that has no record, so a revoke of "
        "the real sidecar is NOT VERIFIED; this is not a real-device pass",
    ]
    if lookalikes:
        reasons.insert(1, f"{', '.join(lookalikes)} is named like a receipt but its body "
                          f"does not declare {RECEIPT_SCHEMA}; a name is not an install")
    return UpstreamVerdict(decision=BLOCKED, code=BLOCKED_UPSTREAM,
                           reasons=tuple(reasons),
                           required_changes=REQUIRED_CHANGES,
                           receipt_found=False, files=tuple(files),
                           commit=commit, manifest_digest=_manifest_of(records),
                           references=tuple(sorted(reference_hits)))


# --------------------------------------------------------------------------- #
# what an operator still has to supply
# --------------------------------------------------------------------------- #
def owner_inputs() -> tuple[str, ...]:
    return (
        "grant — an operator grant whose action is install / revoke / restore, with an "
        "id, an operator and a device_ref; this module never mints one",
        "device_ref — the reviewed identity of the host, recorded at install and "
        "re-checked at revoke",
        "boot_id — the boot the install happened on; a revoke from another boot is "
        "refused because it is not the machine that installed",
        "install_root — the controlled directory the sidecar is installed into; it must "
        "not be, contain or sit inside a Legacy root",
        "receipt — the receipt the install wrote, plus the ownership marker beside it; "
        "without both, a revoke is refused rather than guessed",
        "legacy_roots — the directories a revoke must never reach, supplied by the "
        "caller rather than discovered",
        "real co-install / real revoke on a device — separate user permission; nothing "
        "in this module performs one, and none of its output may be reported as a "
        "real-device pass",
    )


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
SIDECAR_FILES: tuple[str, ...] = (
    "fleet_mcp/deploy/next_tailscale_gate.py",
    "fleet_mcp/deploy/next_tailscale_plan.example.json",
)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--describe", action="store_true",
                        help="pin the sidecar at --ref and report its revocability")
    parser.add_argument("--ref", default="HEAD")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--version", default="0.1.0+wave17")
    args = parser.parse_args(list(argv) if argv is not None else None)
    repo = Path(args.repo)

    if args.describe:
        descriptor = descriptor_from_git(repo, args.ref, SIDECAR_FILES,
                                         version=args.version)
        verdict = upstream_verdict(repo, args.ref, SIDECAR_FILES)
        payload = {
            "schema": SCHEMA,
            "descriptor": {
                "name": descriptor.name,
                "version": descriptor.version,
                "commit": descriptor.commit,
                "file_count": descriptor.file_count(),
                "files": [{"path": path, "sha256": digest}
                          for path, digest in descriptor.files],
                "manifest_digest": descriptor.manifest_digest(),
                "entrypoint": descriptor.entrypoint,
                "check": {"decision": check_descriptor(descriptor).decision},
            },
            "revoke": {
                "decision": verdict.decision,
                "code": verdict.code,
                "receipt_found": verdict.receipt_found,
                "reasons": list(verdict.reasons),
                "required_changes": list(verdict.required_changes),
            },
            "owner_inputs": list(owner_inputs()),
        }
        # Machine-readable output has to be UTF-8 whatever the console code page
        # is. On a GBK console the em dash in the reasons came out as 0xA1 0xAA
        # and the payload was not parseable as JSON — by the only reader this
        # mode has.
        try:
            sys.stdout.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
        json.dump(payload, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
