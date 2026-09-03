"""WorkRequest facade.

The stable seam a future server façade uses to turn an incoming requirement
into a Work Item. ``WorkRequest.submit(requirement)`` returns a persisted
Work Item in ``NEW`` state. Keep this thin: server-side concerns (transport,
auth, intake) live outside this crate.
"""

from __future__ import annotations

from .engine import WorkEngine
from .model import WorkItem


class WorkRequest:
    """Turn a requirement into a trackable, resumable Work Item."""

    def __init__(self, engine: WorkEngine | None = None) -> None:
        self.engine = engine or WorkEngine()

    def submit(
        self,
        requirement: str,
        *,
        title: str | None = None,
        repo: str = "",
        worktree: str = "",
        plan: list[str] | None = None,
        item_id: str | None = None,
    ) -> WorkItem:
        """Create a Work Item from ``requirement`` and persist it.

        ``title`` defaults to a short prefix of the requirement when omitted.
        """
        title = (title or requirement).strip() or "Untitled work item"
        return self.engine.create(
            title=title,
            requirement=requirement,
            repo=repo,
            worktree=worktree,
            plan=plan,
            item_id=item_id,
        )
