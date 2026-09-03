"""Local persistence for Work Items.

``WorkItemStore`` writes one JSON file per Work Item into a state directory.
The directory is located by an **absolute path** (resolved at construction),
so any process or session can read it regardless of its own cwd.

Writes are atomic (temp file + ``os.replace``) which makes them safe across
concurrent readers and, combined with per-id files, robust to partial failure.

File layout::

    <state_dir>/
      WI-XXXX.json ...     (one file per Work Item)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .model import WorkItem


class WorkItemStore:
    """Persist ``WorkItem`` objects to a local directory as JSON files."""

    def __init__(self, state_dir: str | os.PathLike) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, item_id: str) -> Path:
        return self.state_dir / f"{item_id}.json"

    def save(self, item: WorkItem) -> None:
        """Atomically persist ``item`` to ``<state_dir>/<id>.json``."""
        data = item.to_dict()
        path = self._path(item.id)
        tmp = path.with_name(f".{item.id}.tmp.{os.getpid()}")
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp.write_text(text, encoding="utf-8")
        os.replace(tmp, path)

    def load(self, item_id: str) -> WorkItem | None:
        """Return the Work Item for ``item_id``, or ``None`` if absent/corrupt."""
        path = self._path(item_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        try:
            return WorkItem.from_dict(data)
        except (KeyError, ValueError, TypeError):
            return None

    def list(self) -> list[WorkItem]:
        """Return all Work Items in the store (sorted by id)."""
        items: list[WorkItem] = []
        for path in sorted(self.state_dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            try:
                items.append(WorkItem.from_dict(data))
            except (KeyError, ValueError, TypeError):
                continue
        return items
