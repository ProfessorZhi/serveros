"""SQLite runtime store for ServerOS v2."""

from __future__ import annotations

import json
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from .model import (
    Activity,
    ActivityState,
    ContextMode,
    ExecutionContext,
    ItemState,
    Outcome,
    Run,
    RunState,
    WorkItem,
    now_iso,
)
from .state_machine import reopen, transition, transition_run


_PREFIX = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")


class ServerOSStore:
    def __init__(self, state_dir: str | Path) -> None:
        state_dir = Path(state_dir)
        if not state_dir.is_absolute():
            raise ValueError("ServerOS state directory must be absolute")
        self.state_dir = state_dir.resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = self.state_dir / "serveros.db"
        schema = (Path(__file__).with_name("schema.sql")).read_text(encoding="utf-8")
        with self.connection() as conn:
            conn.executescript(schema)

    def connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA busy_timeout = 30000")
        return conn

    @contextmanager
    def transaction(self, immediate: bool = False):
        conn = self.connection()
        try:
            conn.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def register_project(self, project: str, prefix: str) -> None:
        if not project or not _PREFIX.fullmatch(prefix):
            raise ValueError("invalid project or Item prefix")
        try:
            with self.transaction(immediate=True) as conn:
                existing = conn.execute("SELECT prefix FROM projects WHERE project=?", (project,)).fetchone()
                if existing and existing["prefix"] != prefix:
                    raise ValueError(f"project {project} already uses prefix {existing['prefix']}")
                owner = conn.execute("SELECT project FROM projects WHERE prefix=?", (prefix,)).fetchone()
                if owner and owner["project"] != project:
                    raise ValueError(f"Item prefix already registered: {prefix}")
                conn.execute("INSERT OR IGNORE INTO projects(project,prefix) VALUES (?,?)", (project, prefix))
                conn.execute(
                    "INSERT OR IGNORE INTO id_sequences(scope,kind,next_value) VALUES (?,?,1)",
                    (project, "item"),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"Item prefix already registered: {prefix}") from exc

    def allocate_item_id(self, project: str) -> str:
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT prefix FROM projects WHERE project=?", (project,)).fetchone()
            if row is None:
                raise KeyError(f"unknown project: {project}")
            seq = conn.execute(
                "SELECT next_value FROM id_sequences WHERE scope=? AND kind='item'", (project,)
            ).fetchone()["next_value"]
            conn.execute(
                "UPDATE id_sequences SET next_value=? WHERE scope=? AND kind='item'", (seq + 1, project)
            )
            return f"{row['prefix']}-{seq:04d}"

    def create_item(self, item: WorkItem) -> WorkItem:
        prefix = item.id.split("-", 1)[0]
        with self.transaction(immediate=True) as conn:
            project = conn.execute("SELECT prefix FROM projects WHERE project=?", (item.project,)).fetchone()
            if project is None or project["prefix"] != prefix:
                raise ValueError("Item id does not match registered project prefix")
            conn.execute(
                """INSERT INTO items_index
                   (item_id,project,title,kind,card_locator,state,outcome,created_at,updated_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (
                    item.id,
                    item.project,
                    item.title,
                    item.kind,
                    item.card_locator,
                    item.state.value,
                    item.outcome.value if item.outcome else None,
                    item.created_at,
                    item.updated_at,
                ),
            )
            self._event(conn, item.id, "created", {"state": item.state.value})
        return item

    def _event(self, conn, item_id: str, event_type: str, payload: dict) -> None:
        conn.execute(
            "INSERT INTO item_events(item_id,event_type,at,payload_json) VALUES (?,?,?,?)",
            (item_id, event_type, now_iso(), json.dumps(payload, sort_keys=True)),
        )

    def get_item(self, item_id: str) -> WorkItem | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM items_index WHERE item_id=?", (item_id,)).fetchone()
        if row is None:
            return None
        return WorkItem(
            id=row["item_id"],
            project=row["project"],
            title=row["title"],
            kind=row["kind"],
            card_locator=row["card_locator"],
            state=ItemState(row["state"]),
            outcome=Outcome(row["outcome"]) if row["outcome"] else None,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    def list_items(self, project: str | None = None) -> list[WorkItem]:
        sql = "SELECT item_id FROM items_index"
        params: tuple = ()
        if project:
            sql += " WHERE project=?"
            params = (project,)
        sql += " ORDER BY item_id"
        with self.connection() as conn:
            ids = [row["item_id"] for row in conn.execute(sql, params)]
        return [item for item_id in ids if (item := self.get_item(item_id)) is not None]

    def transition_item(self, item_id: str, target: ItemState | str, note: str = "", **side) -> WorkItem:
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT state FROM items_index WHERE item_id=?", (item_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown Item: {item_id}")
            current = ItemState(row["state"])
            target = transition(current, target, **side)
            at = now_iso()
            conn.execute("UPDATE items_index SET state=?,updated_at=? WHERE item_id=?", (target.value, at, item_id))
            self._event(
                conn,
                item_id,
                "transitioned",
                {"from": current.value, "to": target.value, "note": note, **{k: str(v) for k, v in side.items()}},
            )
        return self.get_item(item_id)

    def reopen_item(self, item_id: str, reason: str, source: str) -> WorkItem:
        if not reason.strip() or not source.strip():
            raise ValueError("reopen requires reason and source")
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT state FROM items_index WHERE item_id=?", (item_id,)).fetchone()
            if row is None:
                raise KeyError(f"unknown Item: {item_id}")
            target = reopen(ItemState(row["state"]))
            at = now_iso()
            conn.execute("UPDATE items_index SET state=?,updated_at=? WHERE item_id=?", (target.value, at, item_id))
            self._event(conn, item_id, "reopened", {"reason": reason, "source": source, "to": target.value})
        return self.get_item(item_id)

    def item_events(self, item_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT event_type,at,payload_json FROM item_events WHERE item_id=? ORDER BY event_id", (item_id,)
            ).fetchall()
        return [
            {"event_type": row["event_type"], "at": row["at"], "payload": json.loads(row["payload_json"])}
            for row in rows
        ]

    def add_activity(self, activity: Activity, needs: list[str] | None = None) -> Activity:
        with self.transaction(immediate=True) as conn:
            conn.execute(
                "INSERT INTO activities(item_id,activity_id,kind,state,graph_revision) VALUES (?,?,?,?,?)",
                (activity.item_id, activity.id, activity.kind, activity.state.value, activity.graph_revision),
            )
            for dependency in needs or ():
                conn.execute(
                    "INSERT INTO activity_dependencies(item_id,activity_id,needs_activity_id) VALUES (?,?,?)",
                    (activity.item_id, activity.id, dependency),
                )
        return activity

    def attach_context(self, context: ExecutionContext) -> ExecutionContext:
        if context.worktree and not Path(context.worktree).is_absolute():
            raise ValueError("worktree path must be absolute")
        with self.transaction(immediate=True) as conn:
            conn.execute(
                """INSERT INTO execution_contexts
                   (item_id,activity_id,repo,mode,revision,worktree,dirty) VALUES (?,?,?,?,?,?,?)""",
                (
                    context.item_id,
                    context.activity_id,
                    context.repo,
                    context.mode.value,
                    context.revision,
                    context.worktree,
                    int(context.dirty),
                ),
            )
        return context

    def execution_contexts(self, item_id: str, activity_id: str) -> list[ExecutionContext]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT * FROM execution_contexts WHERE item_id=? AND activity_id=? ORDER BY repo",
                (item_id, activity_id),
            ).fetchall()
        return [
            ExecutionContext(
                item_id=row["item_id"],
                activity_id=row["activity_id"],
                repo=row["repo"],
                mode=ContextMode(row["mode"]),
                revision=row["revision"],
                worktree=row["worktree"],
                dirty=bool(row["dirty"]),
            )
            for row in rows
        ]

    def begin_run(self, item_id: str, activity_id: str, worker: str, key: str, artifact_root: Path) -> Run:
        from .artifacts import artifact_dir

        with self.transaction(immediate=True) as conn:
            existing = conn.execute(
                "SELECT * FROM runs WHERE item_id=? AND activity_id=? AND idempotency_key=?",
                (item_id, activity_id, key),
            ).fetchone()
            if existing:
                return self._run(existing)
            sequence = conn.execute("SELECT COUNT(*) AS n FROM runs WHERE item_id=?", (item_id,)).fetchone()["n"] + 1
            run_id = f"RUN-{sequence:04d}"
            attempt = conn.execute(
                "SELECT COUNT(*) AS n FROM runs WHERE item_id=? AND activity_id=?", (item_id, activity_id)
            ).fetchone()["n"] + 1
            path = artifact_dir(artifact_root, self.get_item(item_id).project, item_id, run_id)
            started = now_iso()
            conn.execute(
                """INSERT INTO runs
                   (item_id,run_id,activity_id,worker,idempotency_key,state,artifact_dir,attempt,started_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (item_id, run_id, activity_id, worker, key, RunState.ALLOCATED.value, str(path), attempt, started),
            )
            row = conn.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
        path.mkdir(parents=True, exist_ok=True)
        return self._run(row)

    def get_run(self, item_id: str, run_id: str) -> Run | None:
        with self.connection() as conn:
            row = conn.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
        return self._run(row) if row else None

    def advance_run(self, item_id: str, run_id: str, target: RunState | str) -> Run:
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
            if row is None:
                raise KeyError(f"unknown Run: {item_id}/{run_id}")
            target = transition_run(RunState(row["state"]), target)
            ended = now_iso() if target in {RunState.SUCCEEDED, RunState.FAILED, RunState.CANCELLED, RunState.INVALID} else ""
            conn.execute(
                "UPDATE runs SET state=?,ended_at=? WHERE item_id=? AND run_id=?",
                (target.value, ended, item_id, run_id),
            )
        return self.get_run(item_id, run_id)

    @staticmethod
    def _run(row) -> Run:
        return Run(
            item_id=row["item_id"],
            activity_id=row["activity_id"],
            id=row["run_id"],
            worker=row["worker"],
            idempotency_key=row["idempotency_key"],
            state=RunState(row["state"]),
            artifact_dir=row["artifact_dir"],
            attempt=row["attempt"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
        )


# Old name remains as a Python import alias only; storage is SQLite v2.
WorkItemStore = ServerOSStore
