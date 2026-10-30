"""The hub's requirement board and project understanding, read through Fleet.

Design: docs/plans/fleet-v1-resident-monitor-and-tokyo-hub.md §4. Requirements
live in ONE place — the Tokyo Work Engine (`state/serveros.db`): a Candidate
carries its source and reason, and only the owner turns it into an Item. The
board here is a projection of that store, never a second backlog; project
understanding is the owner's records under `records/projects/<project>/`.

What a commander may do through this module:

* read the board of a project (candidates waiting for a decision, items by
  state, each with where its evidence is);
* read a project's current understanding (the markdown records, as written);
* propose a candidate — with a source and a reason, and only that. Accepting,
  prioritising and closing stay with the owner; there is no verb for them here.

A proposal is recorded the way every other requirement enters the Work Engine:
source → observation → claim → candidate, so its provenance survives and a
second proposal of the same requirement from the same source is the same
candidate rather than a duplicate.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Where a requirement came from — the owner's own words for it.
SOURCE_KINDS = ("conversation", "feishu", "idea", "online-error", "issue", "review", "other")
#: What the requirement is — the Work Engine's own item kinds, so an accepted
#: candidate becomes an item of the same kind without translation.
CANDIDATE_KINDS = ("bug", "feature", "investigation", "test", "ops", "report", "decision")

#: Item states as the board groups them.
COLUMNS = {
    "INBOX": "todo", "AVAILABLE": "todo", "READY": "todo",
    "ACTIVE": "doing", "WAITING": "doing", "NEEDS_HUMAN": "doing", "DELEGATED": "doing",
    "DELIVERED": "review",
    "CLOSED": "done", "CANCELLED": "done",
}

MAX_RECORD_BYTES = 64 * 1024
PROJECT = re.compile(r"[a-z0-9][a-z0-9._-]{0,63}")


class WorkBoardError(ValueError):
    """A request this board will not answer as asked."""


@dataclass(frozen=True)
class WorkBinding:
    #: The Work Engine state directory (holds `serveros.db`).
    state_dir: Path
    #: The owner's records root; project understanding is under `projects/`.
    records_root: Path | None = None


def _project(value: Any) -> str:
    if not isinstance(value, str) or not PROJECT.fullmatch(value):
        raise WorkBoardError("`project` must be a lower-case project name, e.g. serveros")
    return value


def _text(value: Any, name: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise WorkBoardError(f"`{name}` is required")
    value = value.strip()
    if len(value) > limit:
        raise WorkBoardError(f"`{name}` is longer than {limit} characters")
    return value


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


class WorkBoard:
    def __init__(self, binding: WorkBinding, *, store=None) -> None:
        self.binding = binding
        if store is None:
            from workengine.store import ServerOSStore
            store = ServerOSStore(binding.state_dir)
        self.store = store

    # -- reads ---------------------------------------------------------------

    def board(self, project: Any) -> dict[str, Any]:
        project = _project(project)
        columns: dict[str, list[dict]] = {"candidates": [], "todo": [], "doing": [],
                                          "review": [], "done": []}
        with self.store.connection() as conn:
            for row in conn.execute(
                    "SELECT candidate_id,title,kind,state,created_at FROM item_candidates "
                    "WHERE project=? AND state IN ('NEW','DEFERRED') ORDER BY created_at",
                    (project,)):
                sources = [r["locator"] for r in conn.execute(
                    "SELECT cl.locator FROM candidate_claims cc JOIN claims cl "
                    "ON cl.claim_id=cc.claim_id WHERE cc.candidate_id=?", (row["candidate_id"],))]
                columns["candidates"].append(dict(row) | {"sources": sources})
            for item in self.store.list_items(project):
                record = item.to_dict()
                runs = conn.execute(
                    "SELECT run_id,state,artifact_dir FROM runs WHERE item_id=? ORDER BY run_id",
                    (item.id,)).fetchall()
                columns[COLUMNS.get(record["state"], "todo")].append({
                    "item_id": item.id, "title": record.get("title"), "state": record["state"],
                    "outcome": record.get("outcome"),
                    "runs": [{"run_id": r["run_id"], "state": r["state"],
                              "evidence": r["artifact_dir"]} for r in runs],
                    "deliverables": self.store.list_deliverables(item.id),
                })
        return {"schema": "serveros/fleet-work-board.v1", "project": project,
                "read_at": _now(), "authority": "work-engine@hub",
                "counts": {name: len(rows) for name, rows in columns.items()},
                "columns": columns}

    def understanding(self, project: Any) -> dict[str, Any]:
        project = _project(project)
        if self.binding.records_root is None:
            return {"project": project, "understanding": "NOT_CONFIGURED", "records": [],
                    "detail": "this hub has no records root bound"}
        directory = (self.binding.records_root / "projects" / project).resolve()
        root = (self.binding.records_root / "projects").resolve()
        if root not in directory.parents or not directory.is_dir():
            return {"project": project, "understanding": "NOT_RECORDED", "records": [],
                    "detail": f"no understanding is recorded for {project} yet"}
        records = []
        for path in sorted(directory.glob("*.md")):
            if path.is_symlink():
                continue
            raw = path.read_bytes()
            records.append({
                "name": path.name,
                "updated_at": datetime.fromtimestamp(path.stat().st_mtime, timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "truncated": len(raw) > MAX_RECORD_BYTES,
                "content": raw[:MAX_RECORD_BYTES].decode("utf-8", "replace"),
            })
        return {"project": project, "understanding": "OK" if records else "NOT_RECORDED",
                "records": records}

    # -- the one write -------------------------------------------------------

    def propose(self, *, project: Any, title: Any, kind: Any, source_kind: Any,
                source: Any, reason: Any, proposed_by: str) -> dict[str, Any]:
        project = _project(project)
        title = _text(title, "title", 200)
        reason = _text(reason, "reason", 2000)
        source = _text(source, "source", 500)
        if kind not in CANDIDATE_KINDS:
            raise WorkBoardError(f"`kind` must be one of {list(CANDIDATE_KINDS)}")
        if source_kind not in SOURCE_KINDS:
            raise WorkBoardError(f"`source_kind` must be one of {list(SOURCE_KINDS)}")

        key = _digest(project, " ".join(title.lower().split()), source_kind, source)
        domain = f"fleet-intake:{source_kind}"
        stable = key[:24]
        candidate_id = f"cnd-{key[:16]}"
        existing = self._candidate(candidate_id)
        if existing is not None:
            return {"candidate": existing, "created": False,
                    "detail": "this requirement from this source is already on the board"}
        registered = self.store.register_source(domain, stable, revision="1", locator=source)
        observation = f"obs-{key[:20]}"
        self.store.record_observation(
            registered["source_id"], observation, semantic_type="requirement",
            content_hash=_digest(title, reason, proposed_by), carrier=source_kind,
            information_form="proposed")
        claim = f"clm-{key[:20]}"
        self.store.record_claim(claim, source, [observation])
        self.store.create_candidate(candidate_id, project, title, kind, dedupe_key=key,
                                    claim_ids=[claim])
        return {"candidate": self._candidate(candidate_id), "created": True,
                "detail": "proposed; the owner accepts, defers or rejects it"}

    def _candidate(self, candidate_id: str) -> dict | None:
        with self.store.connection() as conn:
            row = conn.execute(
                "SELECT candidate_id,project,title,kind,state,created_at FROM item_candidates "
                "WHERE candidate_id=?", (candidate_id,)).fetchone()
        return dict(row) if row else None
