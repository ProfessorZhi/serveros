"""Absolute evidence path boundary."""

from __future__ import annotations

import re
from pathlib import Path


class ArtifactBoundaryError(ValueError):
    pass


_SEGMENT = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
_ITEM = re.compile(r"^[A-Z][A-Z0-9]*-[0-9]{4,}$")
_RUN = re.compile(r"^RUN-[0-9]{4,}$")


def artifact_dir(root: Path | str, project: str, item_id: str, run_id: str) -> Path:
    root = Path(root)
    if not root.is_absolute():
        raise ValueError("artifacts root must be absolute")
    if not _SEGMENT.fullmatch(project) or not _ITEM.fullmatch(item_id) or not _RUN.fullmatch(run_id):
        raise ArtifactBoundaryError("invalid project, Item, or Run path segment")
    root = root.resolve()
    candidate = root / "work-items" / project / item_id / run_id
    if root not in candidate.parents:
        raise ArtifactBoundaryError("artifact path escapes artifacts root")
    return candidate
