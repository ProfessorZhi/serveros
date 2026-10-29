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

#: Where a write is assembled before any installed file moves. The staging tree
#: lives **inside** the root rather than beside it: `os.replace` is only atomic
#: within one filesystem, and a sibling of a root that is itself a mount point is
#: a different filesystem — the rename would silently become copy-then-delete. It
#: is also the only way to guarantee that every byte a transaction writes is
#: inside the boundary the receipt names.
STAGING_DIR = ".sidecar-staging"
#: The write-ahead record of one transaction. It names every path the transaction
#: may touch — and therefore the only paths a repair may touch. Without it, a
#: crash is indistinguishable from a hostile directory, and the safe answer to
#: both is the same: touch nothing.
JOURNAL_NAME = ".sidecar-journal.json"
JOURNAL_SCHEMA = "serveros/fleet-sidecar-txn-journal.v1"

#: What this module can honestly say about the evidence it reads and writes.
#:
#: It is the whole of the guarantee, stated here rather than implied: the receipt
#: digest is a checksum over a canonical body, which detects an inconsistency and
#: an accident. It does **not** detect a forger, because there is no key, no
#: signature and no trust root outside the root itself — a writer who can write
#: the root can write a receipt and a marker that agree with each other. Turning
#: that into authentication needs something this artifact deliberately does not
#: contain: a signing key, a transparency log, or a pin delivered through another
#: channel. `tests/test_fleet_next_wave19_sidecar_transactions.py` keeps a
#: self-consistent forgery as a passing test so the limit stays visible.
RECEIPT_AUTHENTICATION = (
    "checksum-only: sha256 over the canonical receipt body; NOT a signature and "
    "NOT authenticated against a key outside the root, so a writer with access to "
    "the root can forge a receipt and a marker that agree with each other and "
    "be undetected")

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
#: A path the release needs is occupied by something that is not a directory it
#: can write into — a regular file where a directory is required, or a directory
#: where a file is required. Found **before** the first byte is committed, which
#: is the difference between a refusal and ten orphan files.
BLOCKED_UPGRADE_OBSTACLE = "BLOCKED_UPGRADE_OBSTACLE"
#: A transaction journal is in the root: a write died, or is still running.
BLOCKED_TXN_PENDING = "BLOCKED_TXN_PENDING"
#: `recover()` with nothing to recover. Idempotent: a second call refuses and
#: touches nothing, rather than reporting a success it did not perform.
BLOCKED_NO_JOURNAL = "BLOCKED_NO_JOURNAL"
BLOCKED_JOURNAL_UNREADABLE = "BLOCKED_JOURNAL_UNREADABLE"
#: The journal names a path outside the root, or names a path that leaves it.
BLOCKED_JOURNAL_PATH = "BLOCKED_JOURNAL_PATH"
#: The tree no longer matches either end of the transaction: someone edited a
#: file after the crash, so a repair would overwrite an edit no receipt records.
BLOCKED_TXN_FOREIGN_STATE = "BLOCKED_TXN_FOREIGN_STATE"
#: The transaction could not be finished *or* undone from what is on disk.
BLOCKED_TXN_INCOMPLETE = "BLOCKED_TXN_INCOMPLETE"
#: A write failed and was undone; the root is exactly as it was before the call.
BLOCKED_INSTALL_ABORTED = "BLOCKED_INSTALL_ABORTED"
#: A lock that carries no process identity cannot be shown to be a corpse.
BLOCKED_WRITER_UNKNOWN = "BLOCKED_WRITER_UNKNOWN"
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
class RecoverResult:
    """What a repair did, in two lists that are not interchangeable.

    `resumed` are files the interrupted transaction had committed and that the
    repair finished; `removed` are transient artifacts (staging copies, the
    journal, the lock, a consumed backup) that are gone. Nothing in either list
    is a file the release did not name.
    """

    decision: str
    code: str
    reasons: tuple[str, ...] = ()
    resumed: tuple[str, ...] = ()
    removed: tuple[str, ...] = ()


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
# transactions: a write that can be undone, and one that can be finished
# --------------------------------------------------------------------------- #
# #514 measured the hole this section closes. An upgrade used to copy the old
# bytes into `.sidecar-backup/`, then write each new file in place, then the
# receipt. An obstacle the *filesystem* supplied — a stale regular file named
# `zz` where the release needs a directory — was only met when the writer reached
# it, after it had already written `next/aaa-extra.py` and eight backup copies.
# The receipt was never written, so `revoke()` refused `BLOCKED_FOREIGN_PRESENT`
# and the root was trapped: too dirty to revoke, too unowned to clean.
#
# Three rules make that shape unreachable:
#
#   1. **Nothing installed moves until everything is proven.** Every byte of the
#      target state is written into `.sidecar-staging/` inside the root and
#      re-read and digested there. A failure during staging has touched nothing.
#   2. **Every obstacle is a refusal before the first commit.** A parent that is a
#      file or a link is found by `_write_obstacles`, not by `mkdir` halfway
#      through the list, so the answer is `BLOCKED_UPGRADE_OBSTACLE` with the
#      root exactly as it was.
#   3. **A journal names what may be touched.** The write-ahead journal is the
#      only authority a compensation or a repair accepts: it lists the exact
#      paths, digests and evidence bodies of this transaction. A repair deletes
#      nothing outside that list, and refuses rather than guesses when a path
#      holds bytes that match neither end of the transaction.
@dataclass(frozen=True)
class _Entry:
    path: str
    sha256: str
    size: int

    def body(self) -> dict[str, Any]:
        return _record(self.path, self.sha256, self.size)


def _entries(records: Iterable[Any]) -> tuple[_Entry, ...]:
    return tuple(_Entry(path=str(rec.path), sha256=str(rec.sha256), size=int(rec.size))
                 for rec in records)


def _entry_list(raw: Any) -> tuple[_Entry, ...] | None:
    if not isinstance(raw, list):
        return None
    out: list[_Entry] = []
    for item in raw:
        if not isinstance(item, Mapping):
            return None
        path, digest, size = item.get("path"), item.get("sha256"), item.get("size")
        if not isinstance(path, str) or not isinstance(digest, str):
            return None
        if not isinstance(size, int) or isinstance(size, bool):
            return None
        out.append(_Entry(path, digest, size))
    return tuple(out)


def _json_bytes(body: Mapping[str, Any]) -> bytes:
    return json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")


def _write_atomic(target: Path, body: bytes) -> None:
    """Write `body` beside `target`, then rename it on. Atomic within the root."""
    tmp = target.with_name(target.name + f".tmp-{os.getpid()}")
    try:
        tmp.write_bytes(body)
        os.replace(tmp, target)
    finally:
        if tmp.exists() or _is_link(tmp):
            try:
                tmp.unlink()
            except OSError:
                pass


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except OSError:
        pass


def _token() -> str:
    return f"{os.getpid()}-{os.urandom(6).hex()}"


_WIN32 = None


def _kernel32():
    """kernel32 with the signatures used here declared, built once.

    ctypes assumes `c_int` for a restype, which truncates a 64-bit HANDLE. The
    first draft of `_process_start` did exactly that: `GetProcessTimes` failed on
    the maimed handle, the lock recorded `null` where a start time belonged, and
    the identity check silently degraded to a bare pid comparison.
    """
    global _WIN32
    if _WIN32 is None:  # pragma: no cover - Windows only
        import ctypes
        from ctypes import wintypes

        class _FileTime(ctypes.Structure):
            _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.GetExitCodeProcess.argtypes = (wintypes.HANDLE,
                                                ctypes.POINTER(wintypes.DWORD))
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,
                                             ctypes.POINTER(_FileTime),
                                             ctypes.POINTER(_FileTime),
                                             ctypes.POINTER(_FileTime),
                                             ctypes.POINTER(_FileTime))
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        _WIN32 = (ctypes, wintypes, kernel32, _FileTime)
    return _WIN32


def _proc_stat(pid: int) -> list[str] | None:
    """The fields of `/proc/<pid>/stat` after the command name, or `None`.

    Split from the right of the last `)`, because the command name itself may
    contain spaces and parentheses. Field 3 (index 0 here) is the state.
    """
    try:
        text = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8",
                                                   errors="replace")
    except OSError:
        return None
    return text[text.rfind(")") + 1:].split()


def _process_start(pid: int) -> str | None:
    """When process `pid` started, as a string, or `None` if it cannot be read.

    A pid is not an identity: Windows hands a freed pid to the next process it
    creates, and the process that lands on it can be *the repair itself*. A lock
    judged by pid alone then answers "a writer is running" about the very process
    asking, and the interrupted write traps the root — the #514 shape in a third
    place, and the one an operator cannot work around without deleting a file by
    hand. The start time is what tells two processes that shared a pid apart.
    """
    if pid <= 0:
        return None
    if os.name == "nt":  # pragma: no cover - the POSIX branch runs on CI too
        ctypes, _wintypes, kernel32, file_time = _kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return None
        try:
            created, exited, kernel, user = (file_time() for _ in range(4))
            ok = kernel32.GetProcessTimes(handle, ctypes.byref(created),
                                          ctypes.byref(exited), ctypes.byref(kernel),
                                          ctypes.byref(user))
            if not ok:
                return None
            return str((created.high << 32) | created.low)
        finally:
            kernel32.CloseHandle(handle)
    fields = _proc_stat(pid)
    # Field 22 of /proc/<pid>/stat, counted from the command name's closing
    # parenthesis: 3 (state) + 19 more.
    return fields[19] if fields and len(fields) > 19 else None


def _lock_writer_is_alive(body: Mapping) -> bool:
    """Whether the process named by a lock body may still be the writer.

    Two things, not one: the pid is still running, *and* it started when the lock
    says it did. Without the second, a repair that inherits a dead writer's pid
    refuses to repair the write that killed it.
    """
    pid = body.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return False
    if not _pid_alive(pid):
        return False
    recorded = body.get("process_start")
    live = _process_start(pid)
    if isinstance(recorded, str) and live is not None:
        return live == recorded
    # No start time on either side: the pid is all there is, and a running pid is
    # a running pid. Refusing is the safe answer; it is only wrong when it is
    # asking about itself, which is the case the start time was added for.
    return True


def _take_lock(root: Path, purpose: str, boot_id: str, token: str) -> None:
    """Claim the root for this write, named well enough to be judged a corpse.

    The lock is JSON rather than an empty file because `recover()` has to be able
    to tell "a writer died here" from "a writer is running right now", and a bare
    lock cannot answer that without guessing. `process_start` is the part of the
    answer a pid cannot give: see `_process_start`.
    """
    (root / LOCK_NAME).write_bytes(_json_bytes(
        {"pid": os.getpid(), "boot_id": boot_id, "purpose": purpose, "token": token,
         "process_start": _process_start(os.getpid())}))


def _pid_alive(pid: int) -> bool:
    """Whether `pid` names a running process. Never `os.kill` on Windows.

    On Windows `os.kill(pid, 0)` is `TerminateProcess`, not a probe: asking
    whether a writer is alive would kill it. `OpenProcess` with query-only
    access is the probe. Error 87 (invalid parameter) is the only answer that
    means "no such process"; access-denied means the process is there.

    A successful `OpenProcess` is *not* the answer, though — that was the second
    half of this trap. Windows keeps a process object alive while any handle to
    it is open, so a killed writer whose handle somebody else still holds (a
    security product, a debugger, a launcher) stays addressable with its original
    pid and its original start time, and a repair reading only that refuses
    forever with "a writer is running". `GetExitCodeProcess` is the call that
    separates the two: `STILL_ACTIVE` (259) is a running process, and every other
    exit code is a corpse whose object has not been collected yet.
    """
    if pid <= 0:
        return False
    if os.name == "nt":  # pragma: no cover - the POSIX branch runs on CI too
        ctypes, wintypes, kernel32, _file_time = _kernel32()
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return kernel32.GetLastError() != 87
        try:
            code = wintypes.DWORD()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return True  # it exists and refuses to say; assume the worst
            return code.value == 259  # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    # An unreaped zombie still answers `kill(pid, 0)`; its state field says `Z`.
    fields = _proc_stat(pid)
    return not fields or fields[0] != "Z"


def _staging(root: Path) -> Path:
    return root / STAGING_DIR


def _journal_path(root: Path) -> Path:
    return root / JOURNAL_NAME


def _staged_new(root: Path, rel: str) -> Path:
    return root.joinpath(STAGING_DIR, "new", *rel.split("/"))


def _staged_undo(root: Path, rel: str) -> Path:
    return root.joinpath(STAGING_DIR, "undo", *rel.split("/"))


def _staged_evidence(root: Path, name: str) -> Path:
    return root / STAGING_DIR / "undo" / "__evidence__" / name


def _evidence_is_base(root: Path, journal: Mapping[str, Any], name: str) -> bool:
    """Whether `name` on disk is still exactly what this write started from.

    Used only when the staged copy is gone: that can mean "the commit never
    started" (nothing to undo) or "the staged copy was destroyed" (do not claim
    an undo). Distinguishing the two is done by *checking the file*, not by
    trusting the order of operations.
    """
    base = journal.get("base_receipt_digest")
    if name == RECEIPT_NAME:
        on_disk = read_receipt(root)
        if base is None:
            return on_disk is None
        return on_disk is not None and on_disk.is_intact() and on_disk.digest == str(base)
    marker = _read_marker(root)
    if base is None:
        return marker is None
    return marker is not None and str(marker.get("receipt_digest", "")) == str(base)


#: Every file this module puts in place with `_write_atomic`, and therefore the
#: three names whose `.tmp-<pid>` siblings are its own litter after a crash.
_ATOMIC_TARGETS = (JOURNAL_NAME, OWNER_MARKER_NAME, RECEIPT_NAME)


def _write_temps(root: Path) -> list[Path]:
    """`_write_atomic`'s temp files, left behind by a process that died mid-write.

    A kill lands *instead of* the rename, so `_write_atomic`'s `finally` never
    runs and the temp file stays in the root — where a revoke rightly calls it
    foreign and refuses. Naming the three prefixes here is what lets `recover()`
    remove them instead. Nothing else is in reach: the name has to be this
    module's own file name plus its own temp suffix.
    """
    found = []
    for path in sorted(root.iterdir() if root.is_dir() else ()):
        if (not _is_link(path) and path.is_file()
                and any(path.name.startswith(target + ".tmp-")
                        for target in _ATOMIC_TARGETS)):
            found.append(path)
    return found


def _journal_temps(root: Path) -> list[Path]:
    """Just the journal's, for the places asking "had a write begun"."""
    return [path for path in _write_temps(root)
            if path.name.startswith(JOURNAL_NAME + ".tmp-")]


def _lock_is_ours(lock: Path) -> bool:
    """Whether the lock file is this module's own, unreadable bodies included.

    A file that merely carries the name is not evidence; one with the shape
    `_take_lock` writes is. `token` is required because every writer mints one.
    """
    try:
        body = json.loads(lock.read_bytes())
    except (OSError, ValueError):
        return False
    return (isinstance(body, Mapping)
            and isinstance(body.get("pid"), int) and not isinstance(body.get("pid"), bool)
            and isinstance(body.get("token"), str) and bool(body.get("token"))
            and body.get("purpose") in ("install", "restore"))


def _root_is_ours(ctx: "_Ctx") -> bool:
    """Whether this root carries the two files that prove this module wrote here."""
    receipt = ctx.receipt
    marker = ctx.marker
    return (receipt is not None and receipt.is_intact()
            and receipt.install_root == ctx.root_real
            and marker is not None
            and str(marker.get("receipt_digest", "")) == receipt.digest)


def _pending_transaction(root: Path) -> str | None:
    """Why this root is not free to take a new write, if it is not."""
    journal = _journal_path(root)
    if _is_link(journal) or journal.exists():
        return (f"{JOURNAL_NAME} is present: a write to this root began and did not "
                f"finish, and recover() is the only verb allowed to look at it")
    staging = _staging(root)
    if _is_link(staging) or staging.exists():
        return (f"{STAGING_DIR} is present: a write to this root began and did not "
                f"finish, and recover() is the only verb allowed to clean it")
    temps = _journal_temps(root)
    if temps:
        return (f"{temps[0].name} is present: a journal was being written here and "
                f"did not finish, and recover() is the only verb allowed to clean it")
    return None


def _write_obstacles(root: Path, rels: Sequence[str]) -> Verdict | None:
    """Everything that would make a write fail *after* the first file is committed.

    Deliberately checked before the first rename: an obstacle found halfway
    through the commit is six written files and no receipt. A component that is
    a link is refused with the traversal code, because writing through it would
    put a byte outside the root the receipt bounds.
    """
    seen: set[str] = set()
    for rel in rels:
        parts = rel.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent in seen:
                continue
            seen.add(parent)
            here = root.joinpath(*parts[:depth])
            if _is_link(here):
                return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                               (f"{parent} is a link on the way to {rel}",))
            if here.exists() and not here.is_dir():
                return Verdict(BLOCKED, BLOCKED_UPGRADE_OBSTACLE,
                               (f"{parent} is a file where {rel} needs a directory; "
                                f"nothing was written",))
        target = root.joinpath(*parts)
        if _is_link(target):
            return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                           (f"{rel} is a link, so writing it would write through it",))
        if target.exists() and target.is_dir():
            return Verdict(BLOCKED, BLOCKED_UPGRADE_OBSTACLE,
                           (f"{rel} is a directory where the release needs a file; "
                            f"nothing was written",))
    return None


def _missing_dirs(root: Path, rels: Sequence[str]) -> list[str]:
    """Directories below `root` these paths need and do not have yet."""
    missing: list[str] = []
    for rel in rels:
        parts = rel.split("/")
        for depth in range(1, len(parts)):
            parent = "/".join(parts[:depth])
            if parent in missing:
                continue
            here = root.joinpath(*parts[:depth])
            if not here.exists() and not _is_link(here):
                missing.append(parent)
    return sorted(missing)


def _parent_rels(rels: Iterable[str]) -> list[str]:
    """Every directory below the root that these paths pass through."""
    out: set[str] = set()
    for rel in rels:
        parts = str(rel).split("/")
        for depth in range(1, len(parts)):
            out.add("/".join(parts[:depth]))
    return sorted(out)


def _begin_transaction(root: Path, *, action: str, receipt_body: Mapping[str, Any],
                       marker_body: Mapping[str, Any], new: tuple[_Entry, ...],
                       undo: tuple[_Entry, ...], deletes: Sequence[str],
                       consume: tuple[_Entry, ...], base_receipt_digest: str | None,
                       base_evidence: Sequence[str], created_root: bool,
                       token: str, backup: Sequence[_Entry] = (),
                       created_backup: bool = False) -> dict[str, Any]:
    """Write the journal that authorises — and only authorises — this write.

    `backup` is what this write deposited under `.sidecar-backup/`: the old bytes
    an upgrade keeps so a later revoke can name them. It is in the journal for one
    reason — a compensation that puts the previous release back has to take those
    copies away again, or the root keeps files nothing owns.
    """
    journal: dict[str, Any] = {
        "schema": JOURNAL_SCHEMA,
        "action": action,
        "root_real": _root_real(root),
        "device_ref": str(receipt_body["device_ref"]),
        "boot_id": str(receipt_body["boot_id"]),
        "grant_id": str(receipt_body["grant_id"]),
        "operator": str(receipt_body["operator"]),
        "pid": os.getpid(),
        "token": token,
        "staging": STAGING_DIR,
        "lock": LOCK_NAME,
        "created_root": bool(created_root),
        "created_dirs": _missing_dirs(root, [e.path for e in new]
                                      + [RECEIPT_NAME, OWNER_MARKER_NAME]),
        "base_receipt_digest": base_receipt_digest,
        "base_evidence": list(base_evidence),
        "new": [e.body() for e in new],
        "undo": [e.body() for e in undo],
        "deletes": list(deletes),
        "consume": [e.body() for e in consume],
        "backup": [e.body() for e in backup],
        "created_backup": bool(created_backup),
        "receipt_body": dict(receipt_body),
        "marker_body": dict(marker_body),
    }
    journal["journal_digest"] = receipt_digest(journal)
    _write_atomic(_journal_path(root), _json_bytes(journal))
    return journal


def _stage_bytes(root: Path, rel: str, body: bytes) -> None:
    target = _staged_new(root, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)


def _stage_from(root: Path, source: Path, staged: Path) -> None:
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(source.read_bytes())


def _stage_evidence(root: Path, receipt_path: Path, marker_path: Path) -> None:
    for name, path in ((RECEIPT_NAME, receipt_path), (OWNER_MARKER_NAME, marker_path)):
        if path.is_file() and not _is_link(path):
            _stage_from(root, path, _staged_evidence(root, name))


def _verify_staged(root: Path, new: Sequence[_Entry]) -> Verdict | None:
    """Re-read what was staged: a short write is a failure, not a smaller file."""
    for entry in new:
        digest, size, readable = _digest_file(_staged_new(root, entry.path))
        if not readable or digest != entry.sha256 or size != entry.size:
            return Verdict(BLOCKED, BLOCKED_INSTALL_ABORTED,
                           (f"{entry.path}: the staged copy is not the bytes it was "
                            f"staged from (expected {entry.sha256}, staged {digest or 'absent'})",))
    return None


def _commit(root: Path, journal: Mapping[str, Any]) -> list[str]:
    """Move the staged files onto the release, then the marker, then the receipt.

    A file that already holds its new bytes is not moved again: that is what a
    commit interrupted *after* its rename looks like, and `recover()` resumes it
    by this same function. Replacing it from the staging copy would still be
    correct, but the copy is gone — the rename consumed it — so the honest
    statement is "already done".
    """
    new = _entry_list(journal.get("new")) or ()
    committed: list[str] = []
    for entry in new:
        target = root.joinpath(*entry.path.split("/"))
        staged = _staged_new(root, entry.path)
        digest, size, readable = _digest_file(target)
        if readable and digest == entry.sha256 and size == entry.size:
            _unlink_quietly(staged)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(staged, target)
        committed.append(entry.path)
    for rel in journal.get("deletes") or ():
        target = root.joinpath(*str(rel).split("/"))
        if (target.exists() or _is_link(target)) and not target.is_dir():
            target.unlink()
    _write_atomic(root / OWNER_MARKER_NAME, _json_bytes(journal["marker_body"]))
    _write_atomic(root / RECEIPT_NAME, _json_bytes(journal["receipt_body"]))
    return committed


def _discard_staging(root: Path) -> list[str]:
    """Remove the staging tree, file by file, never through a link.

    Only files: an empty directory is removed afterwards, and a directory that
    still holds something survives with it. Nothing outside the staging tree is
    addressed, so no receipt-owned path and no stranger's file is in reach.
    """
    removed: list[str] = []
    staging = _staging(root)
    if _is_link(staging) or not staging.is_dir():
        return removed
    for current, _dirs, files in os.walk(staging, topdown=False, followlinks=False):
        here = Path(current)
        if _is_link(here):
            continue
        for name in files:
            path = here / name
            if _is_link(path):
                continue
            try:
                path.unlink()
                removed.append(path.relative_to(root).as_posix())
            except OSError:
                pass
        try:
            here.rmdir()
        except OSError:
            pass
    return removed


def _prune_created_dirs(root: Path, rels: Sequence[str]) -> None:
    for rel in sorted(rels, key=lambda r: r.count("/"), reverse=True):
        path = root.joinpath(*rel.split("/"))
        try:
            if path.is_dir() and not _is_link(path) and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass


def _finish(root: Path, journal: Mapping[str, Any], *, consume: bool = False) -> list[str]:
    """Remove this transaction's transient artifacts. The release itself stays."""
    removed = _discard_staging(root)
    if _is_link(_staging(root)) or _staging(root).exists():
        # The journal stays behind on purpose: `recover()` is what gets to finish
        # clearing a staging tree, and it needs the record of what is in it.
        return removed
    if consume:
        for entry in _entry_list(journal.get("consume")) or ():
            path = root.joinpath(*entry.path.split("/"))
            digest, size, readable = _digest_file(path)
            if readable and digest == entry.sha256 and size == entry.size:
                _unlink_quietly(path)
                removed.append(entry.path)
    journal_path = _journal_path(root)
    if (journal_path.exists() or _is_link(journal_path)) and not _is_link(journal_path):
        _unlink_quietly(journal_path)
        removed.append(JOURNAL_NAME)
    for temp in _write_temps(root):
        # A previous process died mid-`_write_atomic`; its temp file is this
        # module's litter, and finishing the transaction is what removes it.
        _unlink_quietly(temp)
        removed.append(temp.name)
    _unlink_quietly(root / LOCK_NAME)
    _prune_created_dirs(root, journal.get("created_dirs") or ())
    if journal.get("created_root"):
        try:
            if root.is_dir() and not any(root.iterdir()):
                root.rmdir()
        except OSError:
            pass
    return removed


def _compensate(root: Path, journal: Mapping[str, Any]) -> tuple[str, ...]:
    """Undo exactly what the journal names, and report what could not be undone.

    The only sources of truth are the journal's own lists. A target whose bytes
    match neither the new state nor the recorded old one is **left alone** and
    reported: it is somebody's edit, and this module does not delete an edit no
    receipt records.
    """
    stuck: list[str] = []
    new = _entry_list(journal.get("new")) or ()
    undo = {entry.path: entry for entry in (_entry_list(journal.get("undo")) or ())}
    for entry in reversed(new):
        target = root.joinpath(*entry.path.split("/"))
        digest, size, readable = _digest_file(target)
        if not readable:
            continue
        back = undo.get(entry.path)
        if back is not None and digest == back.sha256 and size == back.size:
            # Already at the state this write started from: the commit never
            # reached this file, so there is nothing to undo and nothing to
            # complain about. Without this, every failure before the last file
            # would be reported as if the operator had edited it.
            continue
        if digest != entry.sha256 or size != entry.size:
            stuck.append(f"{entry.path}: the file now holds bytes this write did not "
                         f"put there, so undoing it would delete someone else's edit")
            continue
        if back is None:
            _unlink_quietly(target)
            continue
        staged = _staged_undo(root, entry.path)
        digest_back, size_back, readable_back = _digest_file(staged)
        if (not readable_back or digest_back != back.sha256 or size_back != back.size):
            stuck.append(f"{entry.path}: the recorded old bytes are gone from "
                         f"{STAGING_DIR}, so this file cannot be put back")
            continue
        try:
            os.replace(staged, target)
        except OSError as exc:
            # The disk failed once already — that is why this runs — and it can
            # fail again on the undo. That is not a reason to raise out of the
            # caller: the journal is still on disk and `recover()` is the verb
            # that decides, with the fault gone, which direction is finishable.
            stuck.append(f"{entry.path}: putting the old bytes back failed "
                         f"({type(exc).__name__}: {exc})")
    for rel in journal.get("deletes") or ():
        target = root.joinpath(*str(rel).split("/"))
        if target.exists() or _is_link(target):
            continue
        back = undo.get(str(rel))
        if back is None:
            continue
        staged = _staged_undo(root, str(rel))
        digest_back, size_back, readable_back = _digest_file(staged)
        if (not readable_back or digest_back != back.sha256 or size_back != back.size):
            stuck.append(f"{rel}: the recorded old bytes are gone from {STAGING_DIR}")
            continue
        try:
            os.replace(staged, target)
        except OSError as exc:
            stuck.append(f"{rel}: putting the old bytes back failed "
                         f"({type(exc).__name__}: {exc})")
    if journal.get("created_backup"):
        # This write made the backup directory. Undo means the previous release is
        # the release again, and the receipt that would have named these copies is
        # not written — so the copies are the writing's own leftovers, not content.
        for entry in _entry_list(journal.get("backup")) or ():
            path = root.joinpath(*entry.path.split("/"))
            digest, size, readable = _digest_file(path)
            if readable and digest == entry.sha256 and size == entry.size:
                _unlink_quietly(path)
        _prune_created_dirs(root, _parent_rels(entry.path
                                               for entry in
                                               (_entry_list(journal.get("backup")) or ())))
    present = set(journal.get("base_evidence") or ())
    for name in (OWNER_MARKER_NAME, RECEIPT_NAME):
        path = root / name
        if name not in present:
            # There was no such file before this write, so the one there now is
            # this write's own and removing it restores "absent".
            if (path.exists() or _is_link(path)) and not _is_link(path):
                _unlink_quietly(path)
            continue
        staged = _staged_evidence(root, name)
        if not staged.is_file() or _is_link(staged):
            if _evidence_is_base(root, journal, name):
                # Evidence is staged before the first commit, so a missing staged
                # copy means the commit never started — and the file on disk is
                # still the one this write began from. Verified, not assumed.
                continue
            stuck.append(f"{name}: the evidence this write started from is gone "
                         f"from {STAGING_DIR}")
            continue
        try:
            _write_atomic(root / name, staged.read_bytes())
        except OSError as exc:
            stuck.append(f"{name}: putting the evidence back failed "
                         f"({type(exc).__name__}: {exc})")
    return tuple(stuck)


def _retract_empty_root(root: Path, created_root: bool) -> None:
    if not created_root:
        return
    try:
        if root.is_dir() and not any(root.iterdir()):
            root.rmdir()
    except OSError:
        pass


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
    pending = _pending_transaction(root)
    if pending is not None:
        # Before the lock check: an interrupted write leaves its own lock behind,
        # and "a write began and did not finish, recover() is the next verb" is
        # the sentence that gets an operator out; "another writer holds this root"
        # sends them looking for a process that is already gone.
        return InstallResult(BLOCKED, BLOCKED_TXN_PENDING, (pending,))
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
    pending = _pending_transaction(root)
    if pending is not None:
        _retract_empty_root(root, created_root)
        return InstallResult(BLOCKED, BLOCKED_TXN_PENDING, (pending,))
    obstacle = _write_obstacles(root, [rel for rel, _pinned in descriptor.files]
                                + [RECEIPT_NAME, OWNER_MARKER_NAME])
    if obstacle is not None:
        _retract_empty_root(root, created_root)
        return InstallResult(BLOCKED, obstacle.code, obstacle.reasons)

    records = [FileRecord(path=rel,
                          sha256="sha256:" + hashlib.sha256(sources[rel]).hexdigest(),
                          size=len(sources[rel]))
               for rel, _pinned in descriptor.files]
    receipt_body = _receipt_body(descriptor, root_real=_root_real(root),
                                 device_ref=device_ref, boot_id=boot_id, grant=grant,
                                 records=records, previous=previous)
    receipt_body["receipt_digest"] = receipt_digest(receipt_body)
    marker_body = {"schema": MARKER_SCHEMA, "install_root": receipt_body["install_root"],
                   "device_ref": device_ref, "name": descriptor.name,
                   "version": descriptor.version,
                   "manifest_digest": receipt_body["manifest_digest"],
                   "receipt_digest": receipt_body["receipt_digest"]}
    new = _entries(records)
    undo = _entries(previous.files) if previous is not None else ()
    base_evidence = [name for name, path in
                     ((RECEIPT_NAME, root / RECEIPT_NAME),
                      (OWNER_MARKER_NAME, root / OWNER_MARKER_NAME))
                     if path.is_file() and not _is_link(path)]
    backup_records = previous.files if previous is not None else ()
    backup_dir = previous.backup_dir if previous is not None else ""
    backup = tuple(_Entry(path=f"{backup_dir}/{record.path}", sha256=record.sha256,
                          size=record.size) for record in backup_records)
    created_backup = bool(backup) and not (root / backup_dir).exists()
    token = _token()
    _take_lock(root, "install", boot_id, token)
    try:
        journal = _begin_transaction(
            root, action="upgrade" if previous is not None else "install",
            receipt_body=receipt_body, marker_body=marker_body, new=new, undo=undo,
            deletes=(), consume=(),
            base_receipt_digest=(existing.digest if existing else None),
            base_evidence=base_evidence, created_root=created_root, token=token,
            backup=backup, created_backup=created_backup)
    except OSError as exc:
        # The journal is written before anything else, so its absence is the
        # statement that there is nothing to recover — but the lock is ours and
        # must go, or the next write is refused as concurrent for no reason.
        _unlink_quietly(root / LOCK_NAME)
        _retract_empty_root(root, created_root)
        return InstallResult(BLOCKED, BLOCKED_INSTALL_ABORTED,
                             (f"{type(exc).__name__}: {exc}; nothing was written",))
    try:
        if previous is not None:
            for record in previous.files:
                source = root / record.path
                backup = root / previous.backup_dir / record.path
                backup.parent.mkdir(parents=True, exist_ok=True)
                backup.write_bytes(source.read_bytes())
                digest, size, readable = _digest_file(backup)
                if not readable or digest != record.sha256 or size != record.size:
                    raise OSError(f"{record.path}: the backup copy is not the bytes "
                                  f"the receipt recorded")
                _stage_from(root, source, _staged_undo(root, record.path))
            _stage_evidence(root, root / RECEIPT_NAME, root / OWNER_MARKER_NAME)
        for record in records:
            # `write_bytes`, never `write_text`: the digest is over these bytes,
            # and `write_text` would translate newlines on Windows and move it.
            _stage_bytes(root, record.path, sources[record.path])
        refused = _verify_staged(root, new)
        if refused is not None:
            # Nothing is committed yet, but the transaction has already written
            # things: the backup copies of the previous release and the staging
            # tree. Finishing alone would leave the copies behind, and they are
            # files no receipt owns — the #514 shape in a new place. So undo.
            stuck = _compensate(root, journal)
            if stuck:
                _unlink_quietly(root / LOCK_NAME)
                return InstallResult(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                                     refused.reasons + stuck)
            _finish(root, journal)
            return InstallResult(BLOCKED, refused.code, refused.reasons)
        _commit(root, journal)
    except Exception as exc:  # every failure is undone and then named
        stuck = _compensate(root, journal)
        if stuck:
            # The journal stays: it is what `recover()` needs to finish either
            # direction, and it is also the record an operator reads.
            _unlink_quietly(root / LOCK_NAME)
            return InstallResult(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                                 (f"{type(exc).__name__}: {exc}",) + stuck)
        _finish(root, journal)
        return InstallResult(BLOCKED, BLOCKED_INSTALL_ABORTED,
                             (f"{type(exc).__name__}: {exc}; the root was left as it "
                              f"was before this call",))
    except BaseException:
        # A caller's Ctrl-C is not a write failure to report in band, but the
        # tree still must not be left half written.
        if not _compensate(root, journal):
            _finish(root, journal)
        raise
    _finish(root, journal)
    if _is_link(_staging(root)) or _staging(root).exists():
        return InstallResult(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                             (f"the release is installed but {STAGING_DIR} could not "
                              f"be removed; recover() is what finishes clearing it",))
    return InstallResult(OK, OK,
                         written=tuple(sorted(record.path for record in records)),
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
    #: `recover()` only: the write-ahead journal, and why there is none.
    journal: Mapping[str, Any] | None = None
    journal_error: str = ""
    #: `recover()` only: set by `_guard_journal_present` when the root carries no
    #: journal but does carry this module's own leftovers, which may be removed.
    no_journal_cleanup: bool = False
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


def _guard_transaction(ctx: _Ctx) -> Verdict | None:
    """A root with a journal or a staging tree is not a root to delete from.

    Revoke and restore both read the installed tree and then act on it. During a
    write — or after one died — that tree is a mixture by construction, and the
    receipt on disk describes only one end of it. The verb that is allowed to
    look is `recover()`.
    """
    pending = _pending_transaction(ctx.root)
    if pending is None:
        return None
    return Verdict(BLOCKED, BLOCKED_TXN_PENDING, (pending,))


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
    # A dead or running write leaves a tree that is a mixture of two versions;
    # the receipt on disk describes one end of it, so deleting from it is a guess.
    # Before `_guard_writer`: an interrupted write leaves its own lock behind, and
    # "a write began and did not finish" is the useful sentence, while "a writer
    # holds this root" would send an operator looking for a process that is gone.
    "_guard_transaction",
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
    "_guard_transaction",
    "_guard_writer",
    "_guard_symlink",
    "_guard_containment",
    "_guard_files",
    "_guard_backup",
    "_guard_obstacles",
    "_guard_foreign",
)

#: The repair pipeline. Every question is about the *journal*: who wrote it, on
#: which boot, for which root, and whether the tree still matches one of its two
#: ends. The receipt is consulted only as evidence of which end.
RECOVER_GUARDS = (
    "_guard_grant",
    "_guard_root",
    "_guard_journal_present",
    "_guard_journal_readable",
    "_guard_journal_identity",
    "_guard_journal_writer",
    "_guard_journal_evidence",
    "_guard_journal_state",
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


def recover_guards() -> tuple:
    """The repair pipeline as it stands right now."""
    return _pipeline(RECOVER_GUARDS)


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

    receipt_body = _receipt_body(previous, root_real=ctx.root_real,
                                 device_ref=device_ref, boot_id=boot_id,
                                 grant=grant, records=previous_version.files,
                                 previous=None)
    receipt_body["receipt_digest"] = receipt_digest(receipt_body)
    # Built on the marker that is already there, not beside it: #513 hangs the
    # Next backend identity (`next_backend_id`) on this same file, and a restore
    # that minted a seven-key dict would silently drop it — after which the
    # installer's own revoke refuses BLOCKED_BACKEND_MISSING. The five keys this
    # verb owns are overwritten; every other key is carried through.
    marker_body = dict(ctx.marker or {})
    marker_body.update({"schema": MARKER_SCHEMA,
                        "install_root": receipt_body["install_root"],
                        "device_ref": device_ref, "name": previous.name,
                        "version": previous.version,
                        "manifest_digest": receipt_body["manifest_digest"],
                        "receipt_digest": receipt_body["receipt_digest"]})
    keep = {record.path for record in previous_version.files}
    deletes = [record.path for record in receipt.files if record.path not in keep]
    base_evidence = [name for name, path in
                     ((RECEIPT_NAME, ctx.root / RECEIPT_NAME),
                      (OWNER_MARKER_NAME, ctx.root / OWNER_MARKER_NAME))
                     if path.is_file() and not _is_link(path)]
    # The backup copies are what a completed restore consumes — not the release
    # files, which are the very thing this verb has just put back.
    consume = tuple(_Entry(path=f"{previous_version.backup_dir}/{record.path}",
                           sha256=record.sha256, size=record.size)
                    for record in previous_version.files)
    token = _token()
    _take_lock(ctx.root, "restore", boot_id, token)
    try:
        journal = _begin_transaction(
            ctx.root, action="restore", receipt_body=receipt_body,
            marker_body=marker_body, new=_entries(previous_version.files),
            undo=_entries(receipt.files), deletes=deletes, consume=consume,
            base_receipt_digest=receipt.digest, base_evidence=base_evidence,
            created_root=False, token=token)
    except OSError as exc:
        _unlink_quietly(ctx.root / LOCK_NAME)
        return RestoreResult(BLOCKED, BLOCKED_INSTALL_ABORTED,
                             (f"{type(exc).__name__}: {exc}; nothing was written",))
    try:
        backup_root = ctx.root / previous_version.backup_dir
        for record in previous_version.files:
            _stage_from(ctx.root, backup_root / record.path,
                        _staged_new(ctx.root, record.path))
        for record in receipt.files:
            source = ctx.root / record.path
            if source.is_file() and not _is_link(source):
                _stage_from(ctx.root, source, _staged_undo(ctx.root, record.path))
        _stage_evidence(ctx.root, ctx.root / RECEIPT_NAME, ctx.root / OWNER_MARKER_NAME)
        refused = _verify_staged(ctx.root, _entries(previous_version.files))
        if refused is not None:
            _finish(ctx.root, journal)
            return RestoreResult(BLOCKED, refused.code, refused.reasons)
        _commit(ctx.root, journal)
    except Exception as exc:  # every failure is undone and then named
        stuck = _compensate(ctx.root, journal)
        if stuck:
            _unlink_quietly(ctx.root / LOCK_NAME)
            return RestoreResult(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                                 (f"{type(exc).__name__}: {exc}",) + stuck)
        _finish(ctx.root, journal)
        return RestoreResult(BLOCKED, BLOCKED_INSTALL_ABORTED,
                             (f"{type(exc).__name__}: {exc}; the root was left as it "
                              f"was before this call",))
    except BaseException:
        if not _compensate(ctx.root, journal):
            _finish(ctx.root, journal)
        raise
    _finish(ctx.root, journal, consume=True)
    # The backup has been consumed; leaving its shell behind would make the next
    # upgrade look like a chain it is not.
    _prune_empty_dirs(ctx.root)
    if _is_link(_staging(ctx.root)) or _staging(ctx.root).exists():
        return RestoreResult(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                             (f"the restore completed but {STAGING_DIR} could not be "
                              f"removed; recover() is what finishes clearing it",))
    return RestoreResult(OK, OK,
                         (f"restored {len(previous_version.files)} file(s) to "
                          f"{previous.version}",),
                         tuple(sorted(record.path for record in previous_version.files)))


# --------------------------------------------------------------------------- #
# recover: finish a write that died, or say why it cannot be finished
# --------------------------------------------------------------------------- #
def _load_journal(root: Path) -> tuple[Mapping[str, Any] | None, str]:
    path = _journal_path(root)
    if _is_link(path):
        return None, "symlink"
    if not path.is_file():
        return None, "absent"
    try:
        body = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None, "unreadable"
    return (body, "") if isinstance(body, Mapping) else (None, "unreadable")


def _descriptor_from_journal(journal: Mapping[str, Any] | None) -> Descriptor:
    """The release a journal names, rebuilt so `_Ctx` has something to carry."""
    body = (journal or {}).get("receipt_body")
    if not isinstance(body, Mapping):
        return Descriptor(name="sidecar", version="0.0.0", commit="0" * 40,
                          files=(), entrypoint="")
    files = tuple((str(item.get("path")), str(item.get("sha256")))
                  for item in body.get("files") or () if isinstance(item, Mapping))
    return Descriptor(name=str(body.get("name") or "sidecar"),
                      version=str(body.get("version") or "0.0.0"),
                      commit=str(body.get("commit") or "0" * 40),
                      files=files, entrypoint="")


def _writer_refusal(root: Path, token: Any = None) -> Verdict | None:
    """Whether the in-root lock belongs to something that may still be writing."""
    lock = root / LOCK_NAME
    if _is_link(lock):
        return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                       (f"{LOCK_NAME} is a link",))
    if not lock.exists():
        return None
    try:
        body = json.loads(lock.read_bytes())
    except (OSError, ValueError):
        body = None
    if not isinstance(body, Mapping):
        return Verdict(BLOCKED, BLOCKED_WRITER_UNKNOWN,
                       (f"{LOCK_NAME} carries no process identity, so it cannot be "
                        f"shown to be a corpse",))
    pid = body.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return Verdict(BLOCKED, BLOCKED_WRITER_UNKNOWN,
                       (f"{LOCK_NAME} names no pid, so it cannot be shown to be a "
                        f"corpse",))
    if token is not None and str(body.get("token", "")) != str(token):
        return Verdict(BLOCKED, BLOCKED_CONCURRENT_WRITER,
                       (f"{LOCK_NAME} belongs to a different write",))
    if _lock_writer_is_alive(body):
        return Verdict(BLOCKED, BLOCKED_CONCURRENT_WRITER,
                       (f"{LOCK_NAME} names pid {pid}, which is running",))
    return None


def _guard_journal_present(ctx: _Ctx) -> Verdict | None:
    if ctx.journal is not None:
        return None
    if ctx.journal_error == "symlink":
        return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                       (f"{JOURNAL_NAME} is a link",))
    if ctx.journal_error == "unreadable":
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       (f"{JOURNAL_NAME} is present but is not a journal this module "
                        f"can read; an operator has to read it, not a repair script",))
    # No journal. Some states are still repairable without one, and the test is
    # always the same: did *this module* leave the thing behind?
    #
    #   * a root whose receipt and marker name this root, and which still holds a
    #     staging tree, a temp journal or a lock — the module wrote here and the
    #     journal is gone (its own cleanup got that far, or somebody deleted it);
    #   * a lock with the shape `_take_lock` writes and no staging tree — a write
    #     died between taking the lock and writing the journal, so nothing was
    #     staged, nothing was committed, and the lock is a corpse to be cleared.
    #
    # Everything else is refused, because "no journal" is not evidence of
    # anything, and this verb deletes files.
    lock = ctx.root / LOCK_NAME
    staging = _staging(ctx.root)
    has_staging = _is_link(staging) or staging.exists()
    has_lock = _is_link(lock) or lock.exists()
    temps = _write_temps(ctx.root)
    if not (has_staging or has_lock or temps):
        # Nothing was left behind at all. This is the second `recover()` on a
        # finished transaction, and saying OK would claim work that did not
        # happen — so it is the same name as "never began".
        return Verdict(BLOCKED, BLOCKED_NO_JOURNAL,
                       (f"{ctx.root} carries no {JOURNAL_NAME} and none of this "
                        f"module's transient files: there is nothing to finish",))
    usable_lock = has_lock and not _is_link(lock) and _lock_is_ours(lock)
    if _root_is_ours(ctx) or (usable_lock and not has_staging):
        ctx.no_journal_cleanup = True
        return None
    return Verdict(BLOCKED, BLOCKED_NO_JOURNAL,
                   (f"{ctx.root} carries no {JOURNAL_NAME}: there is nothing here that "
                    f"this verb could finish, and it does not delete on a guess",))


def _guard_journal_readable(ctx: _Ctx) -> Verdict | None:
    """The journal must be self-consistent before any path in it is trusted.

    This is the guard that keeps a journal from being a second, weaker receipt:
    the receipt body it carries must verify against its own digest, name *this*
    root, and describe exactly the file list the journal claims to write.
    """
    journal = ctx.journal
    if journal is None:
        return None
    if journal.get("schema") != JOURNAL_SCHEMA:
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       (f"journal schema {journal.get('schema')!r} is not "
                        f"{JOURNAL_SCHEMA!r}",))
    body = {key: value for key, value in journal.items() if key != "journal_digest"}
    if receipt_digest(body) != journal.get("journal_digest"):
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       ("the journal does not verify against its own digest",))
    new = _entry_list(journal.get("new"))
    undo = _entry_list(journal.get("undo"))
    consume = _entry_list(journal.get("consume") or [])
    backup = _entry_list(journal.get("backup") or [])
    if new is None or undo is None or consume is None or backup is None:
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       ("the journal's file lists are not lists of records",))
    if not isinstance(journal.get("created_backup", False), bool):
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       ("the journal's backup flag is not a boolean",))
    deletes = journal.get("deletes") or []
    if not isinstance(deletes, list) or not all(isinstance(rel, str) for rel in deletes):
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       ("the journal's delete list is not a list of paths",))
    for entry in new + undo + consume + backup:
        problem = _rel_path_problem(entry.path)
        if problem:
            return Verdict(BLOCKED, BLOCKED_JOURNAL_PATH,
                           (f"the journal names {entry.path!r}: {problem}",))
        if not DIGEST_RE.match(entry.sha256 or ""):
            return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                           (f"the journal names {entry.path!r} with digest "
                            f"{entry.sha256!r}, which is not a sha256",))
        resolved = os.path.realpath(str(ctx.root.joinpath(*entry.path.split("/"))))
        if not (resolved == ctx.root_real or _inside(resolved, ctx.root_real)):
            return Verdict(BLOCKED, BLOCKED_JOURNAL_PATH,
                           (f"{entry.path} resolves to {resolved!r}, outside "
                            f"{ctx.root_real!r}",))
    for rel in deletes:
        problem = _rel_path_problem(rel)
        if problem:
            return Verdict(BLOCKED, BLOCKED_JOURNAL_PATH,
                           (f"the journal deletes {rel!r}: {problem}",))
    receipt_body = journal.get("receipt_body")
    marker_body = journal.get("marker_body")
    if not isinstance(receipt_body, Mapping) or not isinstance(marker_body, Mapping):
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       ("the journal carries no receipt body or no marker body",))
    stripped = {key: value for key, value in receipt_body.items()
                if key != "receipt_digest"}
    digest = str(receipt_body.get("receipt_digest", ""))
    if receipt_digest(stripped) != digest:
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       ("the receipt body the journal carries does not verify against "
                        "its own digest",))
    if str(receipt_body.get("install_root", "")) != ctx.root_real:
        return Verdict(BLOCKED, BLOCKED_TXN_FOREIGN_STATE,
                       (f"the journal's receipt names {receipt_body.get('install_root')!r}, "
                        f"not {ctx.root_real!r}",))
    if receipt_body.get("files") != [entry.body() for entry in new]:
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       ("the receipt body and the journal's file list describe "
                        "different files",))
    for key in ("device_ref", "boot_id"):
        if str(receipt_body.get(key, "")) != str(journal.get(key, "")):
            return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                           (f"the journal and its receipt disagree about {key}",))
    if str(marker_body.get("receipt_digest", "")) != digest:
        return Verdict(BLOCKED, BLOCKED_JOURNAL_UNREADABLE,
                       ("the marker the journal carries does not name its receipt",))
    return None


def _guard_journal_identity(ctx: _Ctx) -> Verdict | None:
    journal = ctx.journal
    if journal is None:
        return None
    if str(journal.get("device_ref", "")) != ctx.device_ref:
        return Verdict(BLOCKED, BLOCKED_IDENTITY_MISMATCH,
                       (f"this is {ctx.device_ref!r}; the interrupted write was on "
                        f"{journal.get('device_ref')!r}",))
    if ctx.grant is not None and ctx.grant.device_ref != ctx.device_ref:
        return Verdict(BLOCKED, BLOCKED_IDENTITY_MISMATCH,
                       (f"the grant is for {ctx.grant.device_ref!r}, this device is "
                        f"{ctx.device_ref!r}",))
    if str(journal.get("boot_id", "")) != ctx.boot_id:
        return Verdict(BLOCKED, BLOCKED_BOOT_MISMATCH,
                       (f"this is boot {ctx.boot_id!r}; the interrupted write was on "
                        f"boot {journal.get('boot_id')!r}. A repair does not cross a "
                        f"boot, because nothing on disk says what survived one",))
    return None


def _guard_journal_writer(ctx: _Ctx) -> Verdict | None:
    journal = ctx.journal
    token = None if journal is None else journal.get("token")
    return _writer_refusal(ctx.root, token)


def _guard_journal_evidence(ctx: _Ctx) -> Verdict | None:
    """Which end of the transaction the evidence on disk is at — or neither."""
    journal = ctx.journal
    if journal is None:
        return None
    base = journal.get("base_receipt_digest")
    receipt_body = journal.get("receipt_body") or {}
    new_digest = str(receipt_body.get("receipt_digest", ""))
    on_disk = ctx.receipt
    if base is None:
        # A fresh install had no receipt before it ran. The only receipt this
        # root may carry is the one this transaction was going to write.
        if on_disk is not None and on_disk.digest != new_digest:
            return Verdict(BLOCKED, BLOCKED_TXN_FOREIGN_STATE,
                           (f"{ctx.root} carries a receipt this write did not write",
                            ))
        return None
    if on_disk is None:
        return Verdict(BLOCKED, BLOCKED_NO_RECEIPT,
                       (f"the journal remembers {RECEIPT_NAME} digest {base}, and the "
                        f"root no longer has that receipt",))
    if not on_disk.is_intact():
        return Verdict(BLOCKED, BLOCKED_RECEIPT_TAMPERED,
                       (f"{RECEIPT_NAME} does not verify against its own digest",))
    if on_disk.digest not in (str(base), new_digest):
        return Verdict(BLOCKED, BLOCKED_TXN_FOREIGN_STATE,
                       (f"{RECEIPT_NAME} is neither the receipt this write started "
                        f"from nor the one it was going to write",))
    return None


def _guard_journal_state(ctx: _Ctx) -> Verdict | None:
    """Every path in the journal must still sit at one of the two ends."""
    journal = ctx.journal
    if journal is None:
        return None
    new = _entry_list(journal.get("new")) or ()
    undo = {entry.path: entry for entry in (_entry_list(journal.get("undo")) or ())}
    staging = _staging(ctx.root)
    if _is_link(staging):
        return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                       (f"{STAGING_DIR} is a link",))
    for entry in new:
        target = ctx.root.joinpath(*entry.path.split("/"))
        if _is_link(target):
            return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                           (f"{entry.path} is now a link; a repair does not write "
                            f"through one",))
        digest, size, readable = _digest_file(target)
        if readable and (digest != entry.sha256 or size != entry.size):
            back = undo.get(entry.path)
            if back is None or digest != back.sha256 or size != back.size:
                return Verdict(BLOCKED, BLOCKED_TXN_FOREIGN_STATE,
                               (f"{entry.path} holds {digest}, which is neither the "
                                f"new bytes nor the ones this write recorded as old; "
                                f"a repair would overwrite somebody's edit",))
        staged = _staged_new(ctx.root, entry.path)
        staged_digest, staged_size, staged_ok = _digest_file(staged)
        if staged_ok and (staged_digest != entry.sha256 or staged_size != entry.size):
            return Verdict(BLOCKED, BLOCKED_TXN_FOREIGN_STATE,
                           (f"{entry.path}: the staged copy was changed after it was "
                            f"written",))
    for rel in journal.get("deletes") or ():
        target = ctx.root.joinpath(*str(rel).split("/"))
        if target.is_dir() or _is_link(target):
            return Verdict(BLOCKED, BLOCKED_TXN_FOREIGN_STATE,
                           (f"{rel} is a directory or a link; this repair does not "
                            f"remove either",))
    for entry in _entry_list(journal.get("backup")) or ():
        if _is_link(ctx.root.joinpath(*entry.path.split("/"))):
            return Verdict(BLOCKED, BLOCKED_SYMLINK_TRAVERSAL,
                           (f"{entry.path} is now a link",))
        digest, size, readable = _digest_file(ctx.root.joinpath(*entry.path.split("/")))
        if readable and (digest != entry.sha256 or size != entry.size):
            return Verdict(BLOCKED, BLOCKED_TXN_FOREIGN_STATE,
                           (f"{entry.path}: the backup copy this write made has been "
                            f"changed since",))
    return None


def _forward_problem(root: Path, new: Sequence[_Entry]) -> Verdict | None:
    missing: list[str] = []
    for entry in new:
        digest, size, readable = _digest_file(root.joinpath(*entry.path.split("/")))
        if readable and digest == entry.sha256 and size == entry.size:
            continue
        staged_digest, staged_size, staged_ok = _digest_file(
            _staged_new(root, entry.path))
        if staged_ok and staged_digest == entry.sha256 and staged_size == entry.size:
            continue
        missing.append(entry.path)
    if missing:
        return Verdict(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                       ("the staged copies for these files are gone and the files on "
                        "disk are not the new bytes: " + ", ".join(sorted(missing)),))
    return None


def _undo_problem(root: Path, journal: Mapping[str, Any]) -> Verdict | None:
    new = _entry_list(journal.get("new")) or ()
    undo = {entry.path: entry for entry in (_entry_list(journal.get("undo")) or ())}
    for entry in new:
        digest, size, readable = _digest_file(root.joinpath(*entry.path.split("/")))
        if not readable or digest != entry.sha256 or size != entry.size:
            continue
        back = undo.get(entry.path)
        if back is None:
            continue
        staged_digest, staged_size, staged_ok = _digest_file(
            _staged_undo(root, entry.path))
        if (not staged_ok or staged_digest != back.sha256
                or staged_size != back.size):
            return Verdict(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                           (f"{entry.path} was committed and the old bytes recorded "
                            f"for it are gone from {STAGING_DIR}",))
    for name in journal.get("base_evidence") or ():
        staged = _staged_evidence(root, str(name))
        if not staged.is_file() or _is_link(staged):
            if _evidence_is_base(root, journal, str(name)):
                continue
            return Verdict(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                           (f"{name}: the evidence this write started from is gone "
                            f"from {STAGING_DIR}",))
    return None


def recover(root: Path | str, *, grant: Grant | None, device_ref: str, boot_id: str,
            legacy_roots: Iterable[Path] = ()) -> RecoverResult:
    """Finish an interrupted write, or undo it — never guess, never widen the set.

    Two directions, both gated on evidence rather than on a timestamp:

      * **finish** when every file is either already the new bytes or has a staged
        copy that still digests to them, and the evidence on disk is one of the
        two ends the journal names;
      * **undo** when finishing is impossible but every committed file still has
        its recorded old bytes in the staging tree.

    A journal with neither direction is reported, not repaired. A second call is
    a refusal (`BLOCKED_NO_JOURNAL`) that deletes nothing, which is the same
    idempotence `revoke()` has: once the evidence is gone, the operation that
    would need it is exactly the one this module exists to refuse.
    """
    root_path = Path(root)
    ctx = _Ctx(action="recover", descriptor=_descriptor_from_journal(None),
               root=root_path, grant=grant, device_ref=device_ref, boot_id=boot_id,
               legacy_roots=tuple(Path(p) for p in legacy_roots))
    ctx.root_real = _root_real(root_path)
    ctx.receipt = read_receipt(root_path) if root_path.is_dir() else None
    ctx.marker = _read_marker(root_path) if root_path.is_dir() else None
    ctx.journal, ctx.journal_error = _load_journal(root_path)
    ctx.descriptor = _descriptor_from_journal(ctx.journal)

    for guard in recover_guards():
        refusal = guard(ctx)
        if refusal is not None:
            return RecoverResult(refusal.decision, refusal.code, refusal.reasons)

    if ctx.journal is None:
        # Allowed here only because `_guard_journal_present` established that the
        # leftovers are this module's own: it removes three transient names and
        # nothing else, so no release byte and no stranger's file is in reach.
        removed = _discard_staging(root_path)
        for tmp in _write_temps(root_path):
            _unlink_quietly(tmp)
            removed.append(tmp.name)
        lock = root_path / LOCK_NAME
        if (lock.exists() or _is_link(lock)) and not _is_link(lock):
            _unlink_quietly(lock)
            removed.append(LOCK_NAME)
        if _is_link(_staging(root_path)) or _staging(root_path).exists():
            return RecoverResult(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                                 (f"{STAGING_DIR} could not be removed",),
                                 removed=tuple(removed))
        return RecoverResult(OK, OK,
                             ("discarded this module's own leftovers; the root holds "
                              "no interrupted transaction and nothing was committed",),
                             removed=tuple(removed))

    journal = ctx.journal
    forward = _forward_problem(root_path, _entry_list(journal.get("new")) or ())
    if forward is None:
        resumed = _commit(root_path, journal)
        removed = _finish(root_path, journal, consume=True)
        reason = ("finished an interrupted write" if resumed else
                  "the write had already finished; only its transient files were "
                  "removed")
        return RecoverResult(OK, OK, (reason,), resumed=tuple(sorted(resumed)),
                             removed=tuple(removed))
    undo = _undo_problem(root_path, journal)
    if undo is None:
        stuck = _compensate(root_path, journal)
        if stuck:
            _unlink_quietly(root_path / LOCK_NAME)
            return RecoverResult(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                                 forward.reasons + stuck)
        removed = _finish(root_path, journal)
        return RecoverResult(OK, OK,
                             ("the write could not be finished, so it was undone; "
                              "nothing it wrote is left",), removed=tuple(removed))
    return RecoverResult(BLOCKED, BLOCKED_TXN_INCOMPLETE,
                         forward.reasons + undo.reasons)


def _guard_obstacles(ctx: _Ctx) -> Verdict | None:
    r = ctx.receipt
    assert r is not None
    assert r.previous is not None
    return _write_obstacles(ctx.root, [record.path for record in r.previous.files]
                            + [RECEIPT_NAME, OWNER_MARKER_NAME])


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
