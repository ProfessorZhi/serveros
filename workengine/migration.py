"""Crash-resumable, hash-checked filesystem migration journal."""

from __future__ import annotations

import hashlib
import errno
import json
import os
import shutil
from pathlib import Path

from .model import now_iso
from .store import ServerOSStore


class MigrationError(ValueError):
    pass


def path_sha256(path: Path) -> str:
    return _path_sha256_excluding(path, ())


def _path_sha256_excluding(path: Path, excluded: tuple[Path, ...]) -> str:
    if path.is_symlink():
        raise MigrationError(f"symlink sources are not supported: {path}")
    digest = hashlib.sha256()
    if path.is_file():
        digest.update(path.read_bytes())
        return digest.hexdigest()
    if not path.is_dir():
        raise MigrationError(f"source does not exist: {path}")
    def is_excluded(candidate: Path) -> bool:
        return any(candidate == item or item in candidate.parents for item in excluded)

    symlink = next((p for p in path.rglob("*") if p.is_symlink() and not is_excluded(p)), None)
    if symlink is not None:
        raise MigrationError(f"symlink sources are not supported: {symlink}")
    for child in sorted(p for p in path.rglob("*") if p.is_file() and not is_excluded(p)):
        digest.update(child.relative_to(path).as_posix().encode())
        digest.update(b"\0")
        digest.update(child.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _fsync_path(path: Path) -> None:
    """Flush a copied tree before publishing it with an atomic rename."""
    if path.is_dir():
        for child in (p for p in path.rglob("*") if p.is_file()):
            with child.open("rb") as handle:
                os.fsync(handle.fileno())
    else:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    descriptor = os.open(path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _move_verified(source: Path, target: Path, expected: str) -> None:
    """Move a path, using copy-verify-publish-remove when EXDEV is raised."""
    try:
        os.rename(source, target)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    temporary = target.with_name(f".{target.name}.serveros-migrate-{expected[:12]}.tmp")
    if temporary.exists():
        if path_sha256(temporary) != expected:
            _remove_path(temporary)
    if not temporary.exists():
        if source.is_dir():
            shutil.copytree(source, temporary)
        else:
            shutil.copy2(source, temporary)
    if path_sha256(temporary) != expected:
        raise MigrationError(f"cross-device copy hash mismatch: {source}")
    _fsync_path(temporary)
    os.rename(temporary, target)
    _remove_path(source)


class MigrationManager:
    def __init__(self, store: ServerOSStore) -> None:
        self.store = store

    def _validate(self, manifest: dict) -> None:
        if not manifest.get("project") or not isinstance(manifest.get("operations"), list):
            raise MigrationError("manifest requires project and operations")
        seen_targets: list[tuple[Path, Path]] = []
        for index, operation in enumerate(manifest["operations"]):
            if operation.get("type") != "move":
                raise MigrationError(f"operation {index}: unsupported type")
            source = Path(operation.get("source", ""))
            target = Path(operation.get("target", ""))
            if not source.is_absolute() or not target.is_absolute():
                raise MigrationError(f"operation {index}: paths must be absolute")
            if source == target or source in target.parents or target in source.parents:
                raise MigrationError(f"operation {index}: overlapping source and target")
            if any(target == prior_target for _, prior_target in seen_targets):
                raise MigrationError(f"operation {index}: duplicate target")
            for prior_source, prior_target in seen_targets:
                if target in prior_target.parents:
                    raise MigrationError(f"operation {index}: ancestor target must be earlier")
                if prior_target in target.parents:
                    relative = target.relative_to(prior_target)
                    if (prior_source / relative).exists():
                        raise MigrationError(f"operation {index}: nested target would overwrite parent source content")
            seen_targets.append((source, target))
            if target.exists():
                raise MigrationError(f"operation {index}: target already exists: {target}")
            actual = path_sha256(source)
            expected = operation.get("sha256")
            if expected and expected != actual:
                raise MigrationError(f"operation {index}: source hash mismatch")
            operation["sha256"] = actual

    def plan(self, manifest: dict) -> dict:
        manifest = json.loads(json.dumps(manifest))
        self._validate(manifest)
        project = manifest["project"]
        with self.store.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM migration_intents WHERE project=?", (project,)).fetchone():
                raise MigrationError(f"migration intent already exists: {project}")
            conn.execute(
                "INSERT INTO migration_intents(project,status,manifest_json,cursor,updated_at) VALUES (?,?,?,?,?)",
                (project, "PLANNED", json.dumps(manifest, sort_keys=True), 0, now_iso()),
            )
        return {"project": project, "status": "PLANNED", "operations": len(manifest["operations"])}

    def inspect(self, project: str) -> dict:
        with self.store.connection() as conn:
            row = conn.execute("SELECT * FROM migration_intents WHERE project=?", (project,)).fetchone()
        if row is None:
            raise MigrationError(f"unknown migration: {project}")
        return {
            "project": project,
            "status": row["status"],
            "cursor": row["cursor"],
            "manifest": json.loads(row["manifest_json"]),
        }

    def apply(self, project: str, crash_after: int | None = None) -> dict:
        intent = self.inspect(project)
        manifest = intent["manifest"]
        cursor = intent["cursor"]
        with self.store.transaction(immediate=True) as conn:
            self.store._refuse_managed_effect(conn, project=project)
            if intent["status"] == "COMPENSATING":
                raise MigrationError("compensation must finish before applying")
            conn.execute(
                "UPDATE migration_intents SET status='APPLYING',updated_at=? WHERE project=?",
                (now_iso(), project),
            )
        applied_this_call = 0
        for index in range(cursor, len(manifest["operations"])):
            operation = manifest["operations"][index]
            source = Path(operation["source"])
            target = Path(operation["target"])
            expected = operation["sha256"]
            if source.exists() and not target.exists():
                if path_sha256(source) != expected:
                    raise MigrationError(f"operation {index}: source changed after plan")
                target.parent.mkdir(parents=True, exist_ok=True)
                _move_verified(source, target, expected)
            elif target.exists() and not source.exists():
                if path_sha256(target) != expected:
                    raise MigrationError(f"operation {index}: resumed target hash mismatch")
            elif source.exists() and target.exists():
                if path_sha256(source) != expected or path_sha256(target) != expected:
                    raise MigrationError(f"operation {index}: ambiguous source/target state")
                _remove_path(source)
            else:
                raise MigrationError(f"operation {index}: ambiguous source/target state")
            with self.store.transaction(immediate=True) as conn:
                conn.execute(
                    "UPDATE migration_intents SET cursor=?,updated_at=? WHERE project=?",
                    (index + 1, now_iso(), project),
                )
            applied_this_call += 1
            if crash_after is not None and applied_this_call == crash_after:
                raise RuntimeError("simulated migration controller crash")
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE migration_intents SET status='COMPLETED',updated_at=? WHERE project=?",
                (now_iso(), project),
            )
        return {"project": project, "status": "COMPLETED", "cursor": len(manifest["operations"])}

    def verify(self, project: str) -> dict:
        intent = self.inspect(project)
        failures = []
        operations = intent["manifest"]["operations"]
        for index, operation in enumerate(operations):
            source = Path(operation["source"])
            target = Path(operation["target"])
            nested_targets = tuple(
                later_target
                for later in operations[index + 1 :]
                if target in (later_target := Path(later["target"])).parents
            )
            if source.exists() or not target.exists() or _path_sha256_excluding(target, nested_targets) != operation["sha256"]:
                failures.append(index)
        return {"project": project, "status": "PASS" if not failures else "FAIL", "failures": failures}

    def compensate(self, project: str) -> dict:
        intent = self.inspect(project)
        operations = intent["manifest"]["operations"]
        # Durable state fences first owner enrollment across every per-step
        # commit and process crash; rollback cannot undo a filesystem move.
        with self.store.transaction(immediate=True) as conn:
            self.store._refuse_managed_effect(conn, project=project)
            conn.execute(
                "UPDATE migration_intents SET status='COMPENSATING',updated_at=? WHERE project=?",
                (now_iso(), project))
        for index in reversed(range(intent["cursor"])):
            operation = operations[index]
            source = Path(operation["source"])
            target = Path(operation["target"])
            if source.exists() and not target.exists():
                continue
            if target.exists() and not source.exists() and path_sha256(target) == operation["sha256"]:
                source.parent.mkdir(parents=True, exist_ok=True)
                _move_verified(target, source, operation["sha256"])
            else:
                raise MigrationError(f"operation {index}: cannot compensate ambiguous state")
            with self.store.transaction(immediate=True) as conn:
                conn.execute("UPDATE migration_intents SET cursor=?,updated_at=? WHERE project=?",
                             (index, now_iso(), project))
        with self.store.transaction(immediate=True) as conn:
            conn.execute(
                "UPDATE migration_intents SET status='COMPENSATED',cursor=0,updated_at=? WHERE project=?",
                (now_iso(), project),
            )
        return {"project": project, "status": "COMPENSATED", "cursor": 0}
