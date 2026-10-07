"""Additive recovery of Item-owned history from a sealed Work Engine archive.

Reads an explicit archive, never treats a board projection as authority. Existing
Items are never overwritten, and leases/grants/Session control are not imported.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from .model import now_iso

# Parent-first order; surrogate row IDs are local and regenerated.
TABLES = {
    "item_events": "event_id", "activities": None,
    "activity_dependencies": None, "execution_contexts": "context_id",
    "runs": None, "run_steps": None, "deliverables": "deliverable_id",
    "evidence": "evidence_id",
}


def restore_items(store, archive, projects, *, expected_sha256=None, apply=False):
    source = Path(archive)
    if (not source.is_absolute() or source != source.resolve() or not source.is_file()
            or source == store.db_path.resolve()):
        raise ValueError("archive must be a separate absolute regular file without symlinks")
    if Path(str(source) + "-wal").exists():
        raise ValueError("archive must be sealed without a WAL; take a SQLite backup first")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    if apply and expected_sha256 != digest:
        raise ValueError("archive SHA256 must match the explicitly inspected snapshot")
    names = sorted(set(projects))
    if not names or any(not name for name in names):
        raise ValueError("explicit projects are required")
    with sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True) as old:
        old.row_factory = sqlite3.Row
        if old.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise ValueError("archive SQLite integrity check failed")
        project_rows = []
        items = []
        children = {}
        for name in names:
            project = old.execute("SELECT * FROM projects WHERE project=?", (name,)).fetchone()
            if project is None:
                raise ValueError(f"archive has no project {name!r}")
            project_rows.append(dict(project))
            items.extend(dict(row) for row in old.execute("SELECT * FROM items_index WHERE project=? ORDER BY item_id", (name,)))
        for item in items:
            children[item["item_id"]] = {
                table: [dict(row) for row in old.execute(f"SELECT * FROM {table} WHERE item_id=?", (item["item_id"],))]
                for table in TABLES
            }
        sequences = [dict(row) for row in old.execute("SELECT * FROM id_sequences")]
    if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
        raise ValueError("archive changed during inspection")
    result = {"archive": str(source), "archive_sha256": digest, "projects": names,
              "apply": apply, "restored": [], "already_restored": [],
              "copied_rows": {}, "historical_nonterminal_runs": [],
              "leases_imported": False, "session_control_imported": False}
    with store.transaction(immediate=apply) as conn:
        pending = []
        for item in items:
            exists = conn.execute("SELECT 1 FROM items_index WHERE item_id=?", (item["item_id"],)).fetchone()
            if exists:
                events = conn.execute("SELECT payload_json FROM item_events WHERE item_id=? AND event_type='archive_restored'", (item["item_id"],)).fetchall()
                if any(json.loads(e[0]).get("archive_sha256") == digest for e in events):
                    result["already_restored"].append(item["item_id"])
                    continue
                raise ValueError(f"existing Item {item['item_id']} would be overwritten; recovery refused")
            pending.append(item)
        for project in project_rows:
            existing = conn.execute("SELECT prefix FROM projects WHERE project=?", (project["project"],)).fetchone()
            owner = conn.execute("SELECT project FROM projects WHERE prefix=?", (project["prefix"],)).fetchone()
            if (existing and existing[0] != project["prefix"]) or (owner and owner[0] != project["project"]):
                raise ValueError("project/prefix binding conflicts with archive")
            if apply and existing is None:
                conn.execute("INSERT INTO projects(project,prefix) VALUES (?,?)", (project["project"], project["prefix"]))
        for item in pending:
            item_id = item["item_id"]
            result["restored"].append(item_id)
            if apply:
                columns = list(item)
                conn.execute(f"INSERT INTO items_index({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [item[k] for k in columns])
            for table, surrogate in TABLES.items():
                rows = children[item_id][table]
                result["copied_rows"][table] = result["copied_rows"].get(table, 0) + len(rows)
                for row in rows:
                    if table == "runs" and row["state"] not in ("SUCCEEDED", "FAILED", "CANCELLED", "INVALID"):
                        result["historical_nonterminal_runs"].append({"item": item_id, "run": row["run_id"], "state": row["state"]})
                    if apply:
                        columns = [k for k in row if k != surrogate]
                        conn.execute(f"INSERT INTO {table}({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})", [row[k] for k in columns])
            if apply:
                store._event(conn, item_id, "archive_restored", {
                    "archive": str(source), "archive_sha256": digest,
                    "source_updated_at": item["updated_at"],
                    "note": "historical records restored; no worker, lease, grant or external effect resumed",
                })
        if apply and pending:
            selected = set(result["restored"])
            for row in sequences:
                if row["scope"] in names or row["scope"] in selected:
                    conn.execute("INSERT INTO id_sequences(scope,kind,next_value) VALUES (?,?,?) ON CONFLICT(scope,kind) DO UPDATE SET next_value=max(next_value,excluded.next_value)", (row["scope"], row["kind"], row["next_value"]))
            # Even an old archive with an incomplete sequence cannot reuse IDs.
            for project in project_rows:
                suffixes = [int(row[0].rsplit('-', 1)[1]) for row in conn.execute("SELECT item_id FROM items_index WHERE project=?", (project["project"],))]
                floor = max(suffixes, default=0) + 1
                conn.execute("INSERT INTO id_sequences(scope,kind,next_value) VALUES (?,'item',?) ON CONFLICT(scope,kind) DO UPDATE SET next_value=max(next_value,excluded.next_value)", (project["project"], floor))
            if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("restored history has broken foreign-key references")
    return result
