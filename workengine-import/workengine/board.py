"""Regenerable human and machine board projections from ServerOS state."""

from __future__ import annotations

import json
import os
from pathlib import Path

from .model import now_iso
from .store import ServerOSStore


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_name(f".{path.name}.serveros.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def snapshot(store: ServerOSStore, project: str) -> dict:
    items = []
    with store.connection() as conn:
        for item in store.list_items(project):
            activity_rows = conn.execute(
                "SELECT activity_id,kind,state FROM activities WHERE item_id=? ORDER BY activity_id", (item.id,)
            ).fetchall()
            activities = []
            for activity in activity_rows:
                contexts = conn.execute(
                    "SELECT repo,mode,revision,worktree,dirty FROM execution_contexts WHERE item_id=? AND activity_id=? ORDER BY repo",
                    (item.id, activity["activity_id"]),
                ).fetchall()
                runs = conn.execute(
                    "SELECT run_id,state,worker,artifact_dir,attempt FROM runs WHERE item_id=? AND activity_id=? ORDER BY run_id",
                    (item.id, activity["activity_id"]),
                ).fetchall()
                activities.append(
                    {
                        "id": activity["activity_id"],
                        "kind": activity["kind"],
                        "state": activity["state"],
                        "contexts": [dict(row) | {"dirty": bool(row["dirty"])} for row in contexts],
                        "runs": [dict(row) for row in runs],
                    }
                )
            items.append(item.to_dict() | {"activities": activities, "deliverables": store.list_deliverables(item.id)})
    return {"schema": "serveros/board.v2", "project": project, "generated_at": now_iso(), "items": items}


def render(store: ServerOSStore, project: str, output_dir: str | Path) -> dict:
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise ValueError("board output directory must be absolute")
    data = snapshot(store, project)
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "current.json"
    markdown_path = output_dir / "current.md"
    _atomic_write(json_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    lines = [f"# {project} board", "", f"Generated: `{data['generated_at']}`", "", "| Item | Kind | State | Outcome | Runs | Deliverables |", "|---|---|---|---|---:|---:|"]
    for item in data["items"]:
        runs = sum(len(activity["runs"]) for activity in item["activities"])
        lines.append(
            f"| {item['id']} | {item['kind']} | {item['state']} | {item['outcome'] or ''} | {runs} | {len(item['deliverables'])} |"
        )
    _atomic_write(markdown_path, "\n".join(lines) + "\n")
    return {"project": project, "items": len(data["items"]), "json": str(json_path), "markdown": str(markdown_path)}
