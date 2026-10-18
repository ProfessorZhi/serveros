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
                for run in runs:
                    evidence = conn.execute("SELECT verdict,revision,dirty FROM evidence WHERE item_id=? AND run_id=? ORDER BY evidence_id DESC LIMIT 1", (item.id, run["run_id"])).fetchone()
                    run = dict(run)
                    run["verdict"] = evidence["verdict"] if evidence else None
                    run["revision"] = evidence["revision"] if evidence else None
                    run["dirty"] = bool(evidence["dirty"]) if evidence else None
                    run["evidence_count"] = conn.execute("SELECT count(*) FROM evidence WHERE item_id=? AND run_id=?", (item.id, run["run_id"])).fetchone()[0]
                    runs = [dict(x) if not isinstance(x, dict) else x for x in runs]
                    runs[runs.index(next(x for x in runs if x["run_id"] == run["run_id"]))] = run
                activities.append(
                    {
                        "id": activity["activity_id"],
                        "kind": activity["kind"],
                        "state": activity["state"],
                        "contexts": [dict(row) | {"dirty": bool(row["dirty"])} for row in contexts],
                        "runs": [dict(row) for row in runs],
                    }
                )
            candidates=[dict(x) for x in conn.execute("SELECT c.candidate_id,c.state,c.domain,c.external_id,c.external_revision,c.approved_item_id, m.revision AS source_revision FROM item_candidates c LEFT JOIN external_item_mappings m ON m.domain=c.domain AND m.external_id=c.external_id AND m.revision=c.external_revision WHERE c.approved_item_id=? AND c.state='PROMOTED' ORDER BY c.candidate_id",(item.id,)).fetchall()]
            items.append(item.to_dict() | {"activities": activities, "deliverables": store.list_deliverables(item.id), "candidate_mappings": candidates})
    revisions=[c['source_revision'] for i in items for c in i['candidate_mappings'] if c.get('source_revision')]
    return {"schema": "serveros/board.v3", "project": project, "generated_at": now_iso(), "generation_source_revision": max(revisions) if revisions else None, "items": items}


def _board_item_ids(json_path: Path) -> set[str]:
    """A missing board is new; an unreadable existing board is not empty."""
    if not json_path.exists():
        return set()
    try:
        previous = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ValueError(f"refusing to render the board: existing projection is unreadable: {json_path}") from exc
    items = previous.get("items") if isinstance(previous, dict) else None
    if not isinstance(items, list) or any(not isinstance(row, dict) or not row.get("id") for row in items):
        raise ValueError(f"refusing to render the board: existing projection has invalid item identities: {json_path}")
    return {row["id"] for row in items}


def render(store: ServerOSStore, project: str, output_dir: str | Path, allow_empty: bool = False) -> dict:
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        raise ValueError("board output directory must be absolute")
    data = snapshot(store, project)
    json_path = output_dir / "current.json"
    markdown_path = output_dir / "current.md"
    missing = _board_item_ids(json_path) - {row["id"] for row in data["items"]}
    if missing and not (allow_empty and not data["items"]):
        erased = len(missing)
        if erased:
            raise ValueError(
                f"refusing to render the board for {project!r}: the board on disk holds "
                f"{erased} item(s) absent from this render ({', '.join(sorted(missing))}), so writing "
                f"would erase them. A board is a projection, but when a project's items are "
                f"absent from the state directory this file may be the only record of them, "
                f"and a second render cannot bring them back. {json_path} is unchanged. "
                f"Recover the missing owner records first. --allow-empty only acknowledges an explicitly empty board."
            )
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_write(json_path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    lines = [f"# {project} board", "", f"Generated: `{data['generated_at']}`", "", "| Item | Kind | State | Outcome | Runs | Deliverables |", "|---|---|---|---|---:|---:|"]
    for item in data["items"]:
        runs = sum(len(activity["runs"]) for activity in item["activities"])
        lines.append(
            f"| {item['id']} | {item['kind']} | {item['state']} | {item['outcome'] or ''} | {runs} | {len(item['deliverables'])} |"
        )
    _atomic_write(markdown_path, "\n".join(lines) + "\n")
    return {"project": project, "items": len(data["items"]), "json": str(json_path), "markdown": str(markdown_path)}
