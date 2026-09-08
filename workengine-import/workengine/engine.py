"""ServerOS v2 control-plane service."""

from __future__ import annotations

import os
from pathlib import Path

from .model import Activity, ContextMode, ExecutionContext, RunState, WorkItem
from .store import ServerOSStore

DEFAULT_STATE_ROOT = Path("/opt/applications/data/home-hwz/state/serveros")
DEFAULT_ARTIFACTS_ROOT = Path("/opt/applications/data/home-hwz/artifacts")


def _absolute_override(name: str, default: Path) -> Path:
    raw = os.environ.get(name)
    if not raw:
        return default
    path = Path(raw)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return path.resolve()


def default_state_dir() -> Path:
    return _absolute_override("SERVEROS_STATE_DIR", DEFAULT_STATE_ROOT)


class ServerOS:
    def __init__(
        self,
        store: ServerOSStore | None = None,
        *,
        state_dir: str | Path | None = None,
        artifacts_root: str | Path | None = None,
    ) -> None:
        if store is None:
            store = ServerOSStore(state_dir if state_dir is not None else default_state_dir())
        self.store = store
        self.artifacts_root = Path(artifacts_root or _absolute_override("SERVEROS_ARTIFACTS_ROOT", DEFAULT_ARTIFACTS_ROOT))
        if not self.artifacts_root.is_absolute():
            raise ValueError("artifacts root must be absolute")

    def register_project(self, project: str, prefix: str) -> None:
        self.store.register_project(project, prefix)

    def create_item(self, project: str, title: str, kind: str, card_locator: str | None = None) -> WorkItem:
        item_id = self.store.allocate_item_id(project)
        locator = card_locator or f"records/projects/{project}/items/{item_id}/card.yaml"
        return self.store.create_item(WorkItem(item_id, project, title, kind, locator))

    def add_activity(self, item_id: str, activity_id: str, kind: str, needs: list[str] | None = None) -> Activity:
        return self.store.add_activity(Activity(item_id, activity_id, kind), needs)

    def attach_context(
        self,
        item_id: str,
        activity_id: str,
        repo: str,
        mode: ContextMode | str,
        *,
        revision: str,
        worktree: str = "",
        dirty: bool = False,
    ) -> ExecutionContext:
        context = ExecutionContext(item_id, activity_id, repo, ContextMode(mode), revision, worktree, dirty)
        return self.store.attach_context(context)

    def begin_run(self, item_id: str, activity_id: str, worker: str, idempotency_key: str):
        return self.store.begin_run(item_id, activity_id, worker, idempotency_key, self.artifacts_root)

    def advance_run(self, item_id: str, run_id: str, state: RunState | str):
        return self.store.advance_run(item_id, run_id, state)

    def resume_run(self, item_id: str, run_id: str):
        run = self.store.get_run(item_id, run_id)
        if run is None:
            raise KeyError(f"unknown Run: {item_id}/{run_id}")
        return run


WorkEngine = ServerOS
