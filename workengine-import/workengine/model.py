"""ServerOS v2 domain values.

Stable intent lives in records cards.  The objects here represent only the
runtime projection required to schedule, resume, and audit work.
"""

from __future__ import annotations

import datetime
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, ClassVar


def now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


class ItemState(StrEnum):
    INBOX = "INBOX"
    AVAILABLE = "AVAILABLE"
    READY = "READY"
    ACTIVE = "ACTIVE"
    DELIVERED = "DELIVERED"
    CLOSED = "CLOSED"
    WAITING = "WAITING"
    NEEDS_HUMAN = "NEEDS_HUMAN"
    DELEGATED = "DELEGATED"
    CANCELLED = "CANCELLED"


class Outcome(StrEnum):
    SUCCESS = "SUCCESS"
    FAIL = "FAIL"
    PARTIAL = "PARTIAL"
    INCONCLUSIVE = "INCONCLUSIVE"
    NOT_APPLICABLE = "NOT_APPLICABLE"


class ActivityState(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"


class RunState(StrEnum):
    ALLOCATED = "ALLOCATED"
    PREPARING = "PREPARING"
    RUNNING = "RUNNING"
    FINALIZING = "FINALIZING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INVALID = "INVALID"


class RunCancelReason(StrEnum):
    """Why an operator ended a Run.

    These are operation semantics, not Run states.  Every one of them lands in
    the existing terminal states CANCELLED or INVALID; none of them introduces
    a new state.
    """

    OPERATOR_CANCELLED = "OPERATOR_CANCELLED"
    ABANDONED = "ABANDONED"
    SUPERSEDED = "SUPERSEDED"
    SCOPE_CHANGED = "SCOPE_CHANGED"
    ENVIRONMENT_UNNEEDED = "ENVIRONMENT_UNNEEDED"


class ContextMode(StrEnum):
    INSPECT = "inspect"
    TEST = "test"
    DEVELOP = "develop"
    RELEASE = "release"

class CandidateState(StrEnum):
    NEW = "NEW"
    MATCHED = "MATCHED"
    PROMOTED = "PROMOTED"
    MERGED = "MERGED"
    REJECTED = "REJECTED"
    DEFERRED = "DEFERRED"


@dataclass
class WorkItem:
    ALLOWED_KINDS: ClassVar[frozenset[str]] = frozenset(
        {
            "feature",
            "bug",
            "test",
            "investigation",
            "analysis",
            "report",
            "deploy",
            "ops",
            "support",
            "decision",
        }
    )

    id: str
    project: str
    title: str
    kind: str
    card_locator: str
    state: ItemState = ItemState.INBOX
    outcome: Outcome | None = None
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if self.kind not in self.ALLOWED_KINDS:
            raise ValueError(f"unsupported item kind: {self.kind}")
        if not self.created_at:
            self.created_at = now_iso()
        if not self.updated_at:
            self.updated_at = self.created_at
        self.state = ItemState(self.state)
        if self.outcome is not None:
            self.outcome = Outcome(self.outcome)

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["state"] = self.state.value
        value["outcome"] = self.outcome.value if self.outcome else None
        return value


@dataclass(frozen=True)
class Activity:
    item_id: str
    id: str
    kind: str
    state: ActivityState = ActivityState.PENDING
    graph_revision: int = 1


@dataclass(frozen=True)
class ExecutionContext:
    item_id: str
    activity_id: str
    repo: str
    mode: ContextMode
    revision: str
    worktree: str = ""
    dirty: bool = False


@dataclass(frozen=True)
class Run:
    item_id: str
    activity_id: str
    id: str
    worker: str
    idempotency_key: str
    state: RunState
    artifact_dir: str
    attempt: int
    started_at: str
    ended_at: str = ""


# Transitional import alias only. It names the v2 state model and does not
# preserve any v0 state values or persistence behavior.
State = ItemState
