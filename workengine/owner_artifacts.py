"""Recoverable local directory allocation for an owner Run transaction.

The immutable manifest precedes the SQL row/receipt commit. A process death
can leave an orphan directory, never a committed ready Run without a flushed
manifest. An identical retry adopts its manifest; another operation refuses.
An incomplete manifest/directory requires explicit local reconciliation.
"""
import json
import os
from pathlib import Path


def real_path(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("OWNER_LOCATION_MISMATCH: absolute canonical path required")
    for ancestor in (path, *path.parents):
        if ancestor.is_symlink() or (ancestor.exists() and ancestor.resolve() != ancestor):
            raise ValueError("OWNER_LOCATION_MISMATCH: symlink/reparse ancestor refused")
    return path


def allocate_directory(root, path, manifest):
    root, path = real_path(root), real_path(path)
    if not path.is_relative_to(root):
        raise ValueError("OWNER_LOCATION_MISMATCH: artifacts escape configured root")
    marker = path / ".owner-allocation.json"
    payload = json.dumps(manifest, sort_keys=True) + "\n"
    if path.exists():
        if not marker.is_file() or marker.is_symlink() or marker.read_text(encoding="utf-8") != payload:
            raise ValueError("OWNER_ARTIFACT_ORPHAN: allocation requires explicit reconciliation")
        return
    path.mkdir(parents=True, exist_ok=False)
    with marker.open("x", encoding="utf-8") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    # Directory fsync is supported by the production POSIX owner. Portable
    # Store tests do not substitute a different OS security mechanism.
    if os.name != "nt":
        for directory in (path, *path.parents):
            fd = os.open(directory, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
