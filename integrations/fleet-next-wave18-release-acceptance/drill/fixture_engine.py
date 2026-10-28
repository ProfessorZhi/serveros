#!/usr/bin/env python3
"""A fixture double of #506's sidecar engine, for the Wave 18 drill.

Why a double exists at all
--------------------------
The drill has to prove *the drill works*: that a package can be installed, that
an observer is refused when it has nothing to observe, that a failed upgrade
leaves the root alone, that a revoke deletes exactly what a receipt owns, and
that a Legacy snapshot is identical across six operating-system processes. If
that proof were built only against the pinned engine, a mistake in the harness
and a mistake in the engine would look the same. So the fixture world runs on
this file: same API, same refusal codes, same write order — and no import of
anything outside the standard library, so it runs on this machine.

The real run uses the pinned engine
-----------------------------------
`wave18_acceptance.py` extracts `integrations/fleet-next-wave17-sidecar-release/
sidecar_release.py` from the pinned #506 commit and hands *that* to the same
drill. Nothing in this file is evidence about the release; it is evidence about
the harness. The distinction is carried in the layer evidence as
`engine.kind` ("fixture" or "pinned").

Deliberately faithful in one unpleasant place
---------------------------------------------
`install()` writes each file as it goes and writes the receipt last, with no
rollback. That is #506's design (the receipt is the last thing to exist, so a
half-written tree is never *claimed* to be installed), and it is reproduced
here because the drill has to be able to see what a mid-write failure leaves
behind. Reproducing it is not a claim that this file's behaviour is the
release's behaviour; the real engine is measured separately.
"""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

SCHEMA = "serveros/fleet-next-wave17-sidecar-release.v1"
RECEIPT_SCHEMA = "serveros/fleet-sidecar-install-receipt.v1"
MARKER_SCHEMA = "serveros/fleet-sidecar-owner-marker.v1"

RECEIPT_NAME = ".sidecar-install-receipt.json"
OWNER_MARKER_NAME = ".sidecar-owner.json"
LOCK_NAME = ".sidecar-install.lock"
BACKUP_DIR = ".sidecar-backup"

OK = "OK"
BLOCKED = "BLOCKED"

BLOCKED_NOT_PINNED = "BLOCKED_NOT_PINNED"
BLOCKED_DESCRIPTOR_PATH = "BLOCKED_DESCRIPTOR_PATH"
BLOCKED_NO_GRANT = "BLOCKED_NO_GRANT"
BLOCKED_ROOT_NOT_ABSOLUTE = "BLOCKED_ROOT_NOT_ABSOLUTE"
BLOCKED_LEGACY_ROOT = "BLOCKED_LEGACY_ROOT"
BLOCKED_ROOT_NOT_EMPTY = "BLOCKED_ROOT_NOT_EMPTY"
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
BLOCKED_MODIFIED_AFTER_INSTALL = "BLOCKED_MODIFIED_AFTER_INSTALL"
BLOCKED_FILE_ABSENT = "BLOCKED_FILE_ABSENT"
BLOCKED_FOREIGN_PRESENT = "BLOCKED_FOREIGN_PRESENT"
BLOCKED_BACKUP_MODIFIED = "BLOCKED_BACKUP_MODIFIED"
BLOCKED_DEVICE_DISAGREEMENT = "BLOCKED_DEVICE_DISAGREEMENT"
BLOCKED_INCOMPLETE = "BLOCKED_INCOMPLETE"
BLOCKED_RESOURCE_COLLISION = "BLOCKED_RESOURCE_COLLISION"
BLOCKED_CROSS_TRACK = "BLOCKED_CROSS_TRACK"

VERSION_RE = re.compile(
    r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z][0-9A-Za-z.\-]*)?(?:\+[A-Za-z0-9][A-Za-z0-9.\-]*)?$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")

#: The eight resources #502 refuses to share, in reporting order.
RESOURCES = ("install_path", "port", "socket", "backend_id", "oauth_audience",
             "cred_state_root", "lock", "unit")
PATH_RESOURCES = ("install_path", "cred_state_root", "lock")


class Refused(Exception):
    pass


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
class Descriptor:
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
        recipe = "".join(f"{digest}  {path}\n"
                         for path, digest in sorted(self.files))
        return "sha256:" + hashlib.sha256(recipe.encode("utf-8")).hexdigest()


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
        body = {k: v for k, v in self.raw.items() if k != "receipt_digest"}
        return receipt_digest(body) == self.digest

    def body(self) -> dict[str, Any]:
        return dict(self.raw)


# --------------------------------------------------------------------------- #
# paths and digests
# --------------------------------------------------------------------------- #
def _sha(body: bytes) -> str:
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _rel_path_problem(path: str) -> str:
    if not isinstance(path, str) or not path:
        return "an empty path is not a file"
    if "\\" in path:
        return f"{path!r} contains a backslash"
    if posixpath.isabs(path):
        return f"{path!r} is absolute"
    if posixpath.normpath(path) != path:
        return f"{path!r} is not normalised"
    if any(part in ("", ".", "..") for part in path.split("/")):
        return f"{path!r} has an empty, '.' or '..' component"
    return ""


def _inside(child: str, parent: str) -> bool:
    child = os.path.normcase(os.path.realpath(child)).rstrip("\\/")
    parent = os.path.normcase(os.path.realpath(parent)).rstrip("\\/")
    return child == parent or child.startswith(parent + os.sep)


def _root_real(root: Path) -> str:
    return os.path.realpath(str(root))


def _root_refusal(root: Path, legacy_roots: Iterable[Path]) -> Verdict | None:
    if not root.is_absolute():
        return Verdict(BLOCKED, BLOCKED_ROOT_NOT_ABSOLUTE,
                       (f"{root} is not absolute",))
    real = _root_real(root)
    for legacy in legacy_roots:
        lreal = _root_real(Path(legacy))
        if _inside(real, lreal) or _inside(lreal, real):
            return Verdict(BLOCKED, BLOCKED_LEGACY_ROOT,
                           (f"{real} and the Legacy root {lreal} contain each other",))
    return None


def _is_link(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    if path.is_symlink():
        return True
    return bool(getattr(info, "st_file_attributes", 0) & 0x400)


# --------------------------------------------------------------------------- #
# descriptor, receipt
# --------------------------------------------------------------------------- #
def check_descriptor(descriptor: Descriptor) -> Verdict:
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
                           (f"{path!r} is listed twice",))
        seen.add(path)
        if not DIGEST_RE.match(digest or ""):
            return Verdict(BLOCKED, BLOCKED_NOT_PINNED,
                           (f"{path}: {digest!r} is not sha256:<64 hex>",))
    if descriptor.entrypoint not in seen:
        return Verdict(BLOCKED, BLOCKED_NOT_PINNED,
                       (f"entrypoint {descriptor.entrypoint!r} is not a pinned file",))
    return Verdict(OK, OK)


def receipt_digest(body: Mapping[str, Any]) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"),
                           ensure_ascii=False).encode("utf-8")
    return _sha(canonical)


def _record(path: str, digest: str, size: int) -> dict[str, Any]:
    return {"path": path, "sha256": digest, "size": size}


def _receipt_body(descriptor: Descriptor, *, root_real: str, device_ref: str,
                  boot_id: str, grant: Grant, records: Sequence[FileRecord],
                  previous: PreviousVersion | None) -> dict[str, Any]:
    body: dict[str, Any] = {
        "schema": RECEIPT_SCHEMA, "name": descriptor.name,
        "version": descriptor.version, "commit": descriptor.commit,
        "manifest_digest": descriptor.manifest_digest(),
        "install_root": root_real, "device_ref": device_ref,
        "boot_id": boot_id, "grant_id": grant.grant_id,
        "operator": grant.operator,
        "files": [_record(r.path, r.sha256, r.size) for r in records],
        "previous": None,
    }
    if previous is not None:
        body["previous"] = {
            "version": previous.version,
            "manifest_digest": previous.manifest_digest,
            "backup_dir": previous.backup_dir,
            "files": [_record(r.path, r.sha256, r.size) for r in previous.files],
        }
    return body


def read_receipt(root: Path | str) -> Receipt | None:
    path = Path(root) / RECEIPT_NAME
    try:
        if _is_link(path) or not path.is_file():
            return None
        raw = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return Receipt("", "", "", "", "", "", "", "", "", (), None, "",
                       raw={}, broken=True)
    if not isinstance(raw, Mapping):
        return Receipt("", "", "", "", "", "", "", "", "", (), None, "",
                       raw={}, broken=True)
    files = tuple(FileRecord(str(e.get("path", "")), str(e.get("sha256", "")),
                             int(e.get("size", -1) or -1))
                  for e in raw.get("files") or () if isinstance(e, Mapping))
    prev_raw = raw.get("previous")
    previous = None
    if isinstance(prev_raw, Mapping):
        previous = PreviousVersion(
            str(prev_raw.get("version", "")),
            str(prev_raw.get("manifest_digest", "")),
            str(prev_raw.get("backup_dir", "")),
            tuple(FileRecord(str(e.get("path", "")), str(e.get("sha256", "")),
                             int(e.get("size", -1) or -1))
                  for e in prev_raw.get("files") or () if isinstance(e, Mapping)))
    return Receipt(str(raw.get("name", "")), str(raw.get("version", "")),
                   str(raw.get("commit", "")), str(raw.get("manifest_digest", "")),
                   str(raw.get("install_root", "")), str(raw.get("device_ref", "")),
                   str(raw.get("boot_id", "")), str(raw.get("grant_id", "")),
                   str(raw.get("operator", "")), files, previous,
                   str(raw.get("receipt_digest", "")), raw=raw)


def _read_marker(root: Path) -> Mapping[str, Any] | None:
    path = root / OWNER_MARKER_NAME
    try:
        if _is_link(path) or not path.is_file():
            return None
        raw = json.loads(path.read_bytes())
    except (OSError, ValueError):
        return None
    return raw if isinstance(raw, Mapping) else None


def _file_problems(root: Path, records: Sequence[FileRecord]) -> list[Verdict]:
    problems: list[Verdict] = []
    for record in records:
        path = root / record.path
        try:
            body = path.read_bytes()
        except OSError:
            problems.append(Verdict(BLOCKED, BLOCKED_FILE_ABSENT,
                                    (f"{record.path} is gone",)))
            continue
        if _sha(body) != record.sha256 or len(body) != record.size:
            problems.append(Verdict(BLOCKED, BLOCKED_MODIFIED_AFTER_INSTALL,
                                    (f"{record.path} changed after install",)))
    return problems


# --------------------------------------------------------------------------- #
# install
# --------------------------------------------------------------------------- #
def install(descriptor: Descriptor, root: Path | str,
            sources: Mapping[str, bytes], *, grant: "Grant | None",
            device_ref: str, boot_id: str,
            legacy_roots: Iterable[Path] = (),
            replace: "Receipt | None" = None) -> InstallResult:
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
                             (f"{LOCK_NAME} is present",))

    missing: list[str] = []
    for rel, pinned in descriptor.files:
        body = sources.get(rel)
        if body is None:
            missing.append(f"{rel}: not supplied")
        elif _sha(body) != pinned:
            missing.append(f"{rel}: supplied bytes are not the pinned bytes")
    if missing:
        return InstallResult(BLOCKED, BLOCKED_SOURCE_DIGEST, tuple(missing))

    existing = read_receipt(root)
    previous: PreviousVersion | None = None
    if existing is not None and replace is None:
        return InstallResult(BLOCKED, BLOCKED_ALREADY_INSTALLED,
                             (f"{root} already carries a receipt for "
                              f"{existing.version}",))
    if replace is not None:
        if not existing or existing.digest != replace.digest:
            return InstallResult(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                                 ("the receipt handed in is not the one in the root",))
        if not existing.is_intact():
            return InstallResult(BLOCKED, BLOCKED_RECEIPT_TAMPERED,
                                 ("the receipt in the root does not verify",))
        if existing.previous is not None:
            return InstallResult(BLOCKED, BLOCKED_UPGRADE_CHAIN,
                                 ("this receipt already remembers a previous version",))
        if existing.install_root != _root_real(root):
            return InstallResult(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                                 ("the receipt names another root",))
        if existing.device_ref != device_ref:
            return InstallResult(BLOCKED, BLOCKED_IDENTITY_MISMATCH,
                                 (f"the receipt belongs to {existing.device_ref!r}",))
        if existing.boot_id != boot_id:
            return InstallResult(BLOCKED, BLOCKED_BOOT_MISMATCH,
                                 (f"the receipt was written on boot "
                                  f"{existing.boot_id!r}",))
        probe = _file_problems(root, existing.files)
        if probe:
            return InstallResult(BLOCKED, probe[0].code, probe[0].reasons)
        previous = PreviousVersion(existing.version, existing.manifest_digest,
                                   f"{BACKUP_DIR}/{existing.version}",
                                   existing.files)

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
            target.write_bytes(body)
            written.append(rel)
            records.append(FileRecord(rel, _sha(body), len(body)))
        body = _receipt_body(descriptor, root_real=_root_real(root),
                             device_ref=device_ref, boot_id=boot_id, grant=grant,
                             records=records, previous=previous)
        body["receipt_digest"] = receipt_digest(body)
        (root / RECEIPT_NAME).write_bytes(
            json.dumps(body, sort_keys=True, indent=2,
                       ensure_ascii=False).encode("utf-8"))
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
        if created_root and root.is_dir() and not any(root.iterdir()):
            try:
                root.rmdir()
            except OSError:
                pass
    return InstallResult(OK, OK, written=tuple(sorted(written)),
                         receipt_path=str(root / RECEIPT_NAME))


# --------------------------------------------------------------------------- #
# revoke
# --------------------------------------------------------------------------- #
def _allowed_entries(receipt: Receipt) -> set[str]:
    allowed = {record.path for record in receipt.files}
    allowed |= {RECEIPT_NAME, OWNER_MARKER_NAME}
    if receipt.previous is not None:
        prefix = receipt.previous.backup_dir.strip("/")
        allowed.add(prefix)
        allowed |= {f"{prefix}/{record.path}" for record in receipt.previous.files}
    return allowed


def _prune_empty_dirs(root: Path) -> None:
    for current, _dirs, _files in os.walk(root, topdown=False, followlinks=False):
        path = Path(current)
        if path == root or _is_link(path):
            continue
        try:
            if path.is_dir() and not any(path.iterdir()):
                path.rmdir()
        except OSError:
            pass


def _delete_owned(root: Path, receipt: Receipt) -> list[str]:
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


def _guard_foreign(root: Path, receipt: Receipt) -> Verdict | None:
    allowed = _allowed_entries(receipt)
    extra: list[str] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if path.is_dir() and not _is_link(path):
            continue
        if rel not in allowed:
            extra.append(rel)
    if extra:
        return Verdict(BLOCKED, BLOCKED_FOREIGN_PRESENT,
                       (f"{root} holds files this receipt does not own: "
                        f"{', '.join(sorted(extra))}",))
    return None


def revoke(descriptor: Descriptor, root: Path | str, *, grant: "Grant | None",
           device_ref: str, boot_id: str,
           legacy_roots: Iterable[Path] = ()) -> RevokeResult:
    verdict = check_descriptor(descriptor)
    if verdict.decision != OK:
        return RevokeResult(BLOCKED, verdict.code, verdict.reasons)
    if grant is None or not grant.covers("revoke"):
        return RevokeResult(BLOCKED, BLOCKED_NO_GRANT,
                            ("a revoke needs a grant whose action is 'revoke'",))
    root = Path(root)
    refusal = _root_refusal(root, legacy_roots)
    if refusal is not None:
        return RevokeResult(BLOCKED, refusal.code, refusal.reasons)
    receipt = read_receipt(root) if root.is_dir() else None
    if receipt is None:
        return RevokeResult(BLOCKED, BLOCKED_NO_RECEIPT,
                            (f"{root} carries no {RECEIPT_NAME}",))
    if not receipt.is_intact():
        return RevokeResult(BLOCKED, BLOCKED_RECEIPT_TAMPERED,
                            (f"{RECEIPT_NAME} does not verify",))
    root_real = _root_real(root)
    if (receipt.install_root != root_real
            or receipt.version != descriptor.version
            or receipt.commit != descriptor.commit
            or receipt.manifest_digest != descriptor.manifest_digest()):
        return RevokeResult(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                            ("the receipt is not this descriptor's receipt",))
    marker = _read_marker(root)
    if marker is None:
        return RevokeResult(BLOCKED, BLOCKED_OWNER_MARKER_MISSING,
                            (f"{OWNER_MARKER_NAME} is missing",))
    if (str(marker.get("install_root", "")) != root_real
            or str(marker.get("device_ref", "")) != receipt.device_ref
            or str(marker.get("receipt_digest", "")) != receipt.digest
            or str(marker.get("version", "")) != receipt.version):
        return RevokeResult(BLOCKED, BLOCKED_OWNER_MARKER_MISMATCH,
                            (f"{OWNER_MARKER_NAME} disagrees with the receipt",))
    if device_ref != receipt.device_ref:
        return RevokeResult(BLOCKED, BLOCKED_IDENTITY_MISMATCH,
                            (f"this is {device_ref!r}; the receipt belongs to "
                             f"{receipt.device_ref!r}",))
    if grant.device_ref != receipt.device_ref:
        return RevokeResult(BLOCKED, BLOCKED_IDENTITY_MISMATCH,
                            (f"the grant is for {grant.device_ref!r}",))
    if boot_id != receipt.boot_id:
        return RevokeResult(BLOCKED, BLOCKED_BOOT_MISMATCH,
                            (f"this is boot {boot_id!r}; the receipt was written on "
                             f"{receipt.boot_id!r}",))
    if (root / LOCK_NAME).exists() or _is_link(root / LOCK_NAME):
        return RevokeResult(BLOCKED, BLOCKED_CONCURRENT_WRITER,
                            (f"{LOCK_NAME} is present",))
    for record in receipt.files:
        if _rel_path_problem(record.path):
            return RevokeResult(BLOCKED, BLOCKED_OUTSIDE_INSTALL_ROOT,
                                (f"{record.path!r} is not inside the root",))
        if not _inside(str(root / record.path), root_real):
            return RevokeResult(BLOCKED, BLOCKED_OUTSIDE_INSTALL_ROOT,
                                (f"{record.path} resolves outside {root_real}",))
    problems = _file_problems(root, receipt.files)
    if problems:
        return RevokeResult(BLOCKED, problems[0].code, problems[0].reasons)
    foreign = _guard_foreign(root, receipt)
    if foreign is not None:
        return RevokeResult(BLOCKED, foreign.code, foreign.reasons)
    deleted = _delete_owned(root, receipt)
    return RevokeResult(OK, OK, (f"deleted {len(deleted)} file(s)",),
                        tuple(sorted(deleted)))


# --------------------------------------------------------------------------- #
# restore (depth exactly one)
# --------------------------------------------------------------------------- #
def restore(previous: Descriptor, root: Path | str, *, grant: "Grant | None",
            device_ref: str, boot_id: str,
            legacy_roots: Iterable[Path] = ()) -> RestoreResult:
    if grant is None or not grant.covers("restore"):
        return RestoreResult(BLOCKED, BLOCKED_NO_GRANT,
                             ("a restore needs a grant whose action is 'restore'",))
    root = Path(root)
    receipt = read_receipt(root) if root.is_dir() else None
    if receipt is None:
        return RestoreResult(BLOCKED, BLOCKED_NO_RECEIPT, ("no receipt",))
    if not receipt.is_intact():
        return RestoreResult(BLOCKED, BLOCKED_RECEIPT_TAMPERED, ("bad digest",))
    prev = receipt.previous
    if prev is None:
        return RestoreResult(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                             ("the receipt remembers no previous version",))
    if prev.version != previous.version or prev.manifest_digest != previous.manifest_digest():
        return RestoreResult(BLOCKED, BLOCKED_RECEIPT_MISMATCH,
                             ("the descriptor is not the remembered version",))
    backup = root / prev.backup_dir
    restored: list[str] = []
    for record in prev.files:
        source = backup / record.path
        if not source.is_file():
            return RestoreResult(BLOCKED, BLOCKED_BACKUP_MODIFIED,
                                 (f"{record.path} is missing from the backup",))
        body = source.read_bytes()
        if _sha(body) != record.sha256:
            return RestoreResult(BLOCKED, BLOCKED_BACKUP_MODIFIED,
                                 (f"{record.path} in the backup is not the recorded "
                                  f"bytes",))
        target = root / record.path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)
        restored.append(record.path)
    body = dict(receipt.raw)
    body["version"] = prev.version
    body["manifest_digest"] = prev.manifest_digest
    body["files"] = [_record(r.path, r.sha256, r.size) for r in prev.files]
    body["previous"] = None
    body.pop("receipt_digest", None)
    body["receipt_digest"] = receipt_digest(body)
    (root / RECEIPT_NAME).write_bytes(
        json.dumps(body, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8"))
    marker = dict(_read_marker(root) or {})
    marker.update({"version": prev.version,
                   "manifest_digest": prev.manifest_digest,
                   "receipt_digest": body["receipt_digest"]})
    (root / OWNER_MARKER_NAME).write_bytes(
        json.dumps(marker, sort_keys=True, indent=2).encode("utf-8"))
    if backup.is_dir():
        for record in prev.files:
            path = backup / record.path
            if path.exists():
                path.unlink()
    _prune_empty_dirs(root)
    return RestoreResult(OK, OK, (f"restored {len(restored)} file(s)",),
                         tuple(sorted(restored)))


# --------------------------------------------------------------------------- #
# the resource matrix, reproduced from #502 for the drill's control cases
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Collision:
    resource: str
    detail: str


@dataclass(frozen=True)
class Matrix:
    decision: str
    code: str
    collisions: tuple[Collision, ...] = ()
    incomplete: tuple[str, ...] = ()
    legacy: Any = None
    next: Any = None


@dataclass(frozen=True)
class Footprint:
    track: str
    install_path: str
    port: int
    socket: str | None
    backend_id: str
    oauth_audience: str
    cred_state_root: str
    lock: str
    unit: str
    device_ref: str
    version: str
    artifact_digest: str
    rollback_version: str = ""
    rollback_digest: str = ""


def _gaps(footprint: Footprint) -> list[str]:
    gaps: list[str] = []
    for name in RESOURCES:
        value = getattr(footprint, name, None)
        if value is None or (isinstance(value, str) and not value.strip()):
            gaps.append(f"{footprint.track}.{name} is empty")
        elif name in PATH_RESOURCES and not str(value).startswith("/"):
            gaps.append(f"{footprint.track}.{name} {value!r} is not an absolute "
                        f"posix path")
    for name in ("device_ref", "version", "artifact_digest"):
        if not str(getattr(footprint, name, "") or "").strip():
            gaps.append(f"{footprint.track}.{name} is empty")
    return gaps


def _overlap(left: str, right: str) -> bool:
    """True when two absolute paths are equal or one contains the other."""
    left = left.rstrip("/") or "/"
    right = right.rstrip("/") or "/"
    if left == right:
        return True
    return (right.startswith(left + "/") or left.startswith(right + "/"))


def resource_matrix(legacy: Footprint, next_: Footprint) -> Matrix:
    gaps = tuple(_gaps(legacy) + _gaps(next_))
    if gaps:
        return Matrix(BLOCKED, BLOCKED_INCOMPLETE, incomplete=gaps,
                      legacy=legacy, next=next_)
    if legacy.device_ref != next_.device_ref:
        return Matrix(BLOCKED, BLOCKED_DEVICE_DISAGREEMENT,
                      (Collision("device_ref",
                                 f"{legacy.device_ref!r} != {next_.device_ref!r}"),),
                      legacy=legacy, next=next_)
    collisions: list[Collision] = []
    for a in PATH_RESOURCES:
        for b in PATH_RESOURCES:
            left = str(getattr(legacy, a))
            right = str(getattr(next_, b))
            if _overlap(left, right):
                collisions.append(Collision(a, f"{left} and {right} overlap"))
    if legacy.socket and next_.socket:
        if legacy.socket == next_.socket:
            collisions.append(Collision("socket", f"both tracks own {legacy.socket}"))
    if legacy.port == next_.port:
        collisions.append(Collision("port", f"both tracks listen on {legacy.port}"))
    if legacy.backend_id == next_.backend_id:
        collisions.append(Collision("backend_id",
                                    f"both tracks answer as {legacy.backend_id}"))
    if legacy.oauth_audience.rstrip("/") == next_.oauth_audience.rstrip("/"):
        collisions.append(Collision("oauth_audience",
                                    f"both tracks are {legacy.oauth_audience}"))
    if legacy.unit == next_.unit:
        collisions.append(Collision("unit", f"both tracks are {legacy.unit}"))
    if collisions:
        return Matrix(BLOCKED, BLOCKED_RESOURCE_COLLISION, tuple(collisions),
                      legacy=legacy, next=next_)
    return Matrix(OK, OK, (), legacy=legacy, next=next_)
