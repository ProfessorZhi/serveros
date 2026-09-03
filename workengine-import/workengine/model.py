"""Work Item data model.

Fields are intentionally broad so a single persisted object carries
everything a future session needs to resume work: identity, requirement,
repo/worktree location, plan, evidence pointers, current state, and a full
append-only history.
"""

from __future__ import annotations

import datetime
import uuid
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class State(StrEnum):
    """Work Item lifecycle states.

    States are the single source of truth for where a Work Item sits in the
    delivery pipeline. ``StrEnum`` keeps them JSON/CLI friendly (value == name).
    """

    NEW = "NEW"
    TRIAGED = "TRIAGED"
    READY = "READY"
    IMPLEMENTING = "IMPLEMENTING"
    VERIFYING = "VERIFYING"
    PR_OPEN = "PR_OPEN"
    REVIEWING = "REVIEWING"
    READY_TO_MERGE = "READY_TO_MERGE"
    MERGED = "MERGED"

    # Bypass states (side tracks, entered/exited via explicit decision).
    BLOCKED = "BLOCKED"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    CANCELLED = "CANCELLED"


def now_iso() -> str:
    """UTC timestamp in ISO-8601 (with timezone)."""
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def new_id() -> str:
    """Generate a process-independent, human-readable Work Item id."""
    return f"WI-{uuid.uuid4().hex[:8].upper()}"


@dataclass
class WorkItem:
    """A persisted delivery Work Item.

    ``id`` is the stable key used by ``resume`` (across sessions/processes).
    ``worktree`` and every entry in ``evidence`` are stored as **absolute
    paths** so a new session can locate them regardless of its cwd.
    """

    id: str
    title: str
    requirement: str
    repo: str = ""
    worktree: str = ""
    plan: list[str] = field(default_factory=list)
    evidence: list[dict] = field(default_factory=list)  # list of {kind, path, note, at}
    status: State = State.NEW
    history: list[dict] = field(default_factory=list)  # list of {action, from, to, at, note}
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)

    # -- lifecycle mutation helpers -------------------------------------------------

    def record(self, action: str, from_state: State | None, to_state: State, note: str | None) -> None:
        """Append an immutable history entry and bump ``updated_at``."""
        entry = {
            "action": action,
            "from": None if from_state is None else from_state.value,
            "to": to_state.value,
            "at": now_iso(),
            "note": note or "",
        }
        self.history.append(entry)
        self.updated_at = entry["at"]

    def add_evidence(self, path: str, kind: str = "file", note: str | None = None) -> dict:
        """Record an evidence pointer (absolute path) and bump ``updated_at``."""
        entry = {
            "kind": kind,
            "path": path,
            "note": note or "",
            "at": now_iso(),
        }
        self.evidence.append(entry)
        self.updated_at = entry["at"]
        return entry

    # -- serialization --------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkItem":
        d = dict(data)
        raw_status = d.pop("status", State.NEW)
        status = State(raw_status) if isinstance(raw_status, str) else State(raw_status)
        return cls(status=status, **d)
