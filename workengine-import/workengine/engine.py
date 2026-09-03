"""Work Engine orchestration.

Ties together the data model, the state machine, and the store. This is the
object the CLI and future server façade call. Everything here is deliberately
stateless beyond the store: a fresh ``WorkEngine`` bound to the same state
directory will resume exactly where a previous session left off.
"""

from __future__ import annotations

import os
from pathlib import Path

from .model import State, WorkItem, new_id, now_iso
from .state_machine import allowed_transitions, transition
from .store import WorkItemStore
from .resolver import AbilityResolver


def default_state_dir() -> Path:
    """Absolute default state directory.

    ``WORKENGINE_STATE_DIR`` wins; otherwise the ``state/`` directory next to
    this package (repository root), resolved to an absolute path so it works
    from any cwd.
    """
    env = os.environ.get("WORKENGINE_STATE_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent.parent / "state"


class WorkEngine:
    """Create, advance, inspect, and resume Work Items.

    ``WorkEngine`` is a thin wrapper over a :class:`WorkItemStore`. Two
    processes holding two ``WorkEngine`` instances over the same ``store`` see
    the same persisted Work Items.
    """

    def __init__(self, store: WorkItemStore | None = None, state_dir: str | os.PathLike | None = None) -> None:
        if store is not None:
            self.store = store
        else:
            self.store = WorkItemStore(state_dir or default_state_dir())
        self.resolver = AbilityResolver()

    # -- create ---------------------------------------------------------------------

    def create(
        self,
        title: str,
        requirement: str,
        *,
        repo: str = "",
        worktree: str = "",
        plan: list[str] | None = None,
        item_id: str | None = None,
    ) -> WorkItem:
        """Create a new Work Item in ``NEW`` state and persist it."""
        item = WorkItem(
            id=item_id or new_id(),
            title=title,
            requirement=requirement,
            repo=repo,
            worktree=(str(Path(worktree).expanduser().resolve()) if worktree else ""),
            plan=list(plan or []),
            status=State.NEW,
        )
        item.record("create", None, State.NEW, "created")
        self.store.save(item)
        return item

    # -- read -----------------------------------------------------------------------

    def get(self, item_id: str) -> WorkItem | None:
        """Load a Work Item by id, or ``None`` if unknown."""
        return self.store.load(item_id)

    def require(self, item_id: str) -> WorkItem:
        """Load a Work Item by id, raising ``KeyError`` if unknown."""
        item = self.store.load(item_id)
        if item is None:
            raise KeyError(f"unknown work item id: {item_id}")
        return item

    def list(self, status: str | State | None = None) -> list[WorkItem]:
        """List persisted Work Items, optionally filtered by exact status."""
        items = self.store.list()
        if status is None:
            return items
        target = State(status) if not isinstance(status, State) else status
        return [i for i in items if i.status == target]

    # -- advance (state machine) ----------------------------------------------------

    def advance(self, item_id: str, to: str | State, note: str | None = None) -> WorkItem:
        """Advance a Work Item to ``to`` if legal; persist and return it.

        Raises ``KeyError`` for an unknown id and ``IllegalTransition`` for a
        disallowed state move.
        """
        item = self.require(item_id)
        to_state = State(to) if not isinstance(to, State) else to
        # Validate first (raises IllegalTransition on a bad move).
        transition(item.status, to_state)
        item.record("advance", item.status, to_state, note)
        item.status = to_state
        self.store.save(item)
        return item

    # -- evidence -------------------------------------------------------------------

    def attach_evidence(
        self,
        item_id: str,
        path: str,
        kind: str = "file",
        note: str | None = None,
    ) -> WorkItem:
        """Record an evidence pointer (absolute path) on a Work Item."""
        item = self.require(item_id)
        item.add_evidence(str(Path(path).expanduser().resolve()), kind=kind, note=note)
        self.store.save(item)
        return item

    # -- resume ---------------------------------------------------------------------

    def resume(self, item_id: str) -> dict:
        """Return everything a fresh session needs to continue a Work Item.

        The returned dict contains:
          ``item``  the loaded, fully persisted :class:`WorkItem`
          ``next``  the sorted list of legal next ``State`` values
        """
        item = self.require(item_id)
        return {"item": item, "next": sorted(allowed_transitions(item.status), key=lambda s: s.value)}
