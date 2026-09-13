"""SQLite runtime store for ServerOS v2."""

from __future__ import annotations

import json
import hashlib
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
    RunCancelReason,
    RunState,
    WorkItem,
    now_iso,
)
from .state_machine import RUN_TERMINAL, reopen, transition, transition_run
from .migrations import migrate


_PREFIX = re.compile(r"^[A-Z][A-Z0-9]{1,9}$")
_SECRET = re.compile(r"(token|secret|cookie|password|api[_-]?key)", re.I)

def _reject_secrets(value):
    text=json.dumps(value, ensure_ascii=False)
    if _SECRET.search(text): raise ValueError("secret-like evidence metadata is forbidden")


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
            migrate(conn)

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

    def _event(self, conn, item_id: str, event_type: str, payload: dict, at: str | None = None) -> None:
        conn.execute(
            "INSERT INTO item_events(item_id,event_type,at,payload_json) VALUES (?,?,?,?)",
            (item_id, event_type, at or now_iso(), json.dumps(payload, sort_keys=True)),
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

    def set_outcome(self, item_id: str, outcome: Outcome | str) -> WorkItem:
        outcome = Outcome(outcome)
        with self.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM items_index WHERE item_id=?", (item_id,)).fetchone() is None:
                raise KeyError(f"unknown Item: {item_id}")
            at = now_iso()
            conn.execute("UPDATE items_index SET outcome=?,updated_at=? WHERE item_id=?", (outcome.value, at, item_id))
            self._event(conn, item_id, "outcome_recorded", {"outcome": outcome.value})
        return self.get_item(item_id)

    def add_deliverable(self, item_id: str, kind: str, locator: str) -> dict:
        if not kind.strip() or not locator.strip():
            raise ValueError("deliverable requires kind and locator")
        with self.transaction(immediate=True) as conn:
            if conn.execute("SELECT 1 FROM items_index WHERE item_id=?", (item_id,)).fetchone() is None:
                raise KeyError(f"unknown Item: {item_id}")
            at = now_iso()
            cursor = conn.execute(
                "INSERT INTO deliverables(item_id,kind,locator,created_at) VALUES (?,?,?,?)",
                (item_id, kind, locator, at),
            )
            self._event(conn, item_id, "deliverable_added", {"kind": kind, "locator": locator})
            deliverable_id = cursor.lastrowid
        return {"id": deliverable_id, "item": item_id, "kind": kind, "locator": locator, "created_at": at}

    def register_source(self, domain, stable_id, *, revision, locator):
        if not all(str(x).strip() for x in (domain, stable_id, revision, locator)): raise ValueError("source requires provenance")
        with self.transaction(immediate=True) as c:
            c.execute("INSERT OR IGNORE INTO source_objects VALUES (?,?,?,?,?,?)", (f"{domain}:{stable_id}:{revision}",domain,stable_id,revision,locator,now_iso()))
            r=c.execute("SELECT * FROM source_objects WHERE domain=? AND stable_id=? AND revision=?",(domain,stable_id,revision)).fetchone()
        return dict(r)

    def record_observation(self, source_id, observation_id, *, semantic_type, content_hash, source_origin="online", carrier="other", information_form="curated"):
        if not source_id or not observation_id or not semantic_type or not re.fullmatch(r"[0-9a-fA-F]{64}", content_hash): raise ValueError("observation requires provenance and SHA-256")
        with self.transaction(immediate=True) as c:
            if not c.execute("SELECT 1 FROM source_objects WHERE source_id=?",(source_id,)).fetchone(): raise KeyError(source_id)
            c.execute("INSERT OR IGNORE INTO observations VALUES (?,?,?,?,?,?,?,?)",(observation_id,source_id,semantic_type,source_origin,carrier,information_form,content_hash,now_iso()))
            return dict(c.execute("SELECT * FROM observations WHERE observation_id=?",(observation_id,)).fetchone())

    def record_claim(self, claim_id, locator, observation_ids, *, status="ACTIVE"):
        if not observation_ids: raise ValueError("claim requires observations")
        with self.transaction(immediate=True) as c:
            if any(not c.execute("SELECT 1 FROM observations WHERE observation_id=?",(x,)).fetchone() for x in observation_ids): raise KeyError("unknown observation")
            c.execute("INSERT OR IGNORE INTO claims VALUES (?,?,?,?)",(claim_id,locator,status,now_iso()))
            for x in observation_ids: c.execute("INSERT OR IGNORE INTO claim_observations VALUES (?,?)",(claim_id,x))
            return dict(c.execute("SELECT * FROM claims WHERE claim_id=?",(claim_id,)).fetchone())

    def create_candidate(self, candidate_id, project, title, kind, *, dedupe_key, claim_ids=(), domain="", external_id=None, external_revision=None):
        if not claim_ids: raise ValueError("candidate requires claim provenance")
        with self.transaction(immediate=True) as c:
            if any(not c.execute("SELECT 1 FROM claims WHERE claim_id=?",(x,)).fetchone() for x in claim_ids): raise KeyError("unknown claim")
            c.execute("INSERT OR IGNORE INTO item_candidates(candidate_id,project,title,kind,state,dedupe_key,domain,external_id,external_revision,approved_item_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(candidate_id,project,title,kind,"NEW",dedupe_key,domain,external_id,external_revision,None,now_iso()))
            for x in claim_ids: c.execute("INSERT OR IGNORE INTO candidate_claims VALUES (?,?)",(candidate_id,x))
            return dict(c.execute("SELECT * FROM item_candidates WHERE candidate_id=?",(candidate_id,)).fetchone())

    def match_candidate(self, candidate_id, item_id):
        with self.transaction(immediate=True) as c:
            candidate = c.execute(
                "SELECT * FROM item_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise KeyError(candidate_id)
            if not c.execute("SELECT 1 FROM items_index WHERE item_id=?",(item_id,)).fetchone(): raise KeyError(item_id)
            c.execute("UPDATE item_candidates SET state='MATCHED',approved_item_id=? WHERE candidate_id=?",(item_id,candidate_id))
            return dict(c.execute("SELECT * FROM item_candidates WHERE candidate_id=?",(candidate_id,)).fetchone())

    def promote_candidate(self, candidate_id, item_id=None):
        with self.transaction(immediate=True) as c:
            r=c.execute("SELECT * FROM item_candidates WHERE candidate_id=?",(candidate_id,)).fetchone()
            if not r: raise KeyError(candidate_id)
            if r['state']=='PROMOTED': return self.get_item(r['approved_item_id'])
            if not item_id or not r['approved_item_id'] and not item_id: raise ValueError("promotion requires approved Item mapping")
            target=item_id or r['approved_item_id']
            if not c.execute("SELECT 1 FROM items_index WHERE item_id=?",(target,)).fetchone(): raise ValueError("approved Item mapping required")
            c.execute("UPDATE item_candidates SET state='PROMOTED',approved_item_id=? WHERE candidate_id=?",(target,candidate_id))
            if r['domain'] and r['external_id'] and r['external_revision']:
                c.execute("INSERT OR IGNORE INTO external_item_mappings(domain,external_id,revision,item_id) VALUES(?,?,?,?)",(r['domain'],r['external_id'],r['external_revision'],target))
        return self.get_item(target)

    def promote_candidate_to_new_item(self, candidate_id, *, project, prefix, title, kind):
        with self.transaction(immediate=True) as c:
            candidate = c.execute(
                "SELECT * FROM item_candidates WHERE candidate_id=?", (candidate_id,)
            ).fetchone()
            if candidate is None:
                raise KeyError(candidate_id)
            if candidate["state"] == "PROMOTED":
                target = candidate["approved_item_id"]
            else:
                if candidate["project"] != project:
                    raise ValueError("candidate project does not match new Item project")
                if not project or not _PREFIX.fullmatch(prefix):
                    raise ValueError("invalid project or Item prefix")
                registered = c.execute(
                    "SELECT prefix FROM projects WHERE project=?", (project,)
                ).fetchone()
                if registered and registered["prefix"] != prefix:
                    raise ValueError(f"project {project} already uses prefix {registered['prefix']}")
                owner = c.execute("SELECT project FROM projects WHERE prefix=?", (prefix,)).fetchone()
                if owner and owner["project"] != project:
                    raise ValueError(f"Item prefix already registered: {prefix}")
                c.execute("INSERT OR IGNORE INTO projects(project,prefix) VALUES (?,?)", (project, prefix))
                c.execute(
                    "INSERT OR IGNORE INTO id_sequences(scope,kind,next_value) VALUES (?,?,1)",
                    (project, "item"),
                )
                sequence = c.execute(
                    "SELECT next_value FROM id_sequences WHERE scope=? AND kind='item'", (project,)
                ).fetchone()["next_value"]
                c.execute(
                    "UPDATE id_sequences SET next_value=? WHERE scope=? AND kind='item'",
                    (sequence + 1, project),
                )
                target = f"{prefix}-{sequence:04d}"
                item = WorkItem(
                    target,
                    project,
                    title,
                    kind,
                    f"records/projects/{project}/items/{target}/card.yaml",
                )
                c.execute(
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
                        None,
                        item.created_at,
                        item.updated_at,
                    ),
                )
                self._event(c, target, "created", {"state": item.state.value})
                c.execute(
                    "UPDATE item_candidates SET state='PROMOTED',approved_item_id=? WHERE candidate_id=?",
                    (target, candidate_id),
                )
                if candidate["domain"] and candidate["external_id"] and candidate["external_revision"]:
                    c.execute(
                        "INSERT OR IGNORE INTO external_item_mappings(domain,external_id,revision,item_id) VALUES(?,?,?,?)",
                        (
                            candidate["domain"],
                            candidate["external_id"],
                            candidate["external_revision"],
                            target,
                        ),
                    )
        return self.get_item(target)

    def transition_activity(self, item_id, activity_id, target):
        allowed={ActivityState.PENDING:{ActivityState.READY,ActivityState.CANCELLED},ActivityState.READY:{ActivityState.RUNNING,ActivityState.CANCELLED},ActivityState.RUNNING:{ActivityState.SUCCEEDED,ActivityState.FAILED,ActivityState.BLOCKED,ActivityState.CANCELLED},ActivityState.BLOCKED:{ActivityState.READY,ActivityState.CANCELLED}}
        with self.transaction(immediate=True) as c:
            r=c.execute("SELECT * FROM activities WHERE item_id=? AND activity_id=?",(item_id,activity_id)).fetchone()
            if not r: raise KeyError(activity_id)
            target=ActivityState(target); current=ActivityState(r['state'])
            if target not in allowed.get(current,set()): raise ValueError(f"illegal activity transition {current} -> {target}")
            c.execute("UPDATE activities SET state=? WHERE item_id=? AND activity_id=?",(target.value,item_id,activity_id))
        return Activity(item_id,activity_id,r['kind'],target,r['graph_revision'])

    def upsert_context(self, context):
        with self.transaction(immediate=True) as c:
            c.execute("INSERT INTO execution_contexts(item_id,activity_id,repo,mode,revision,worktree,dirty) VALUES(?,?,?,?,?,?,?) ON CONFLICT(item_id,activity_id,repo) DO UPDATE SET mode=excluded.mode,revision=excluded.revision,worktree=excluded.worktree,dirty=excluded.dirty",(context.item_id,context.activity_id,context.repo,context.mode.value,context.revision,context.worktree,int(context.dirty)))
        return context

    def record_evidence(self, item_id, run_id, *, kind, locator, command, cases, exit_code, revision, dirty, verdict):
        if not Path(locator).is_absolute() or not Path(locator).is_file(): raise ValueError("evidence must be an existing absolute file")
        digest=hashlib.sha256(Path(locator).read_bytes()).hexdigest()
        if not digest: raise ValueError("evidence requires SHA-256")
        _reject_secrets({'kind':kind,'locator':locator,'command':command,'cases':cases,'revision':revision,'verdict':verdict,'exit_code':exit_code,'dirty':dirty})
        with self.transaction(immediate=True) as c:
            c.execute("INSERT OR IGNORE INTO evidence(item_id,run_id,kind,locator,sha256,command,cases_json,exit_code,revision,dirty,verdict,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(item_id,run_id,kind,locator,digest,command,json.dumps(cases),exit_code,revision,int(dirty),verdict,now_iso()))
            return dict(c.execute("SELECT * FROM evidence WHERE item_id=? AND run_id=? AND locator=?",(item_id,run_id,locator)).fetchone())

    def finalize_run(self, item_id, run_id, *, verdict, exit_code, command, cases, revision, dirty):
        with self.transaction(immediate=True) as c:
            r=c.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?",(item_id,run_id)).fetchone()
            if not r: raise KeyError(run_id)
            if r['state'] in ('SUCCEEDED','FAILED','CANCELLED','INVALID'):
                e=c.execute("SELECT * FROM evidence WHERE item_id=? AND run_id=? ORDER BY evidence_id DESC LIMIT 1",(item_id,run_id)).fetchone()
                if e and not Path(e['locator']).exists():
                    tmp=Path(e['locator']+'.tmp')
                    if tmp.exists(): tmp.replace(e['locator'])
                    else: Path(e['locator']).write_text(json.dumps({"command":e['command'],"cases":json.loads(e['cases_json']),"exit_code":e['exit_code'],"revision":e['revision'],"dirty":bool(e['dirty']),"verdict":e['verdict']})+'\n')
                return {"state":r['state'],"verdict":e['verdict'] if e else verdict}
            if r['state']!='FINALIZING': raise ValueError("run must be FINALIZING")
            locator=str(Path(r['artifact_dir'])/'finalize.json'); Path(r['artifact_dir']).mkdir(parents=True,exist_ok=True)
            temporary=Path(locator+'.tmp'); payload=json.dumps({"command":command,"cases":cases,"exit_code":exit_code,"revision":revision,"dirty":dirty,"verdict":verdict})+'\n'; temporary.write_text(payload)
            _reject_secrets(json.loads(payload))
            digest=hashlib.sha256(payload.encode()).hexdigest()
            c.execute("INSERT INTO evidence(item_id,run_id,kind,locator,sha256,command,cases_json,exit_code,revision,dirty,verdict,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(item_id,run_id,'finalize',locator,digest,command,json.dumps(cases),exit_code,revision,int(dirty),verdict,now_iso()))
            state='SUCCEEDED' if verdict=='PASS' and exit_code==0 else 'FAILED'
            c.execute("UPDATE runs SET state=?,ended_at=? WHERE item_id=? AND run_id=?",(state,now_iso(),item_id,run_id))
        temporary.replace(locator)
        return {"state":state,"verdict":verdict}

    def record_feedback(self,item_id,locator,kind,observation_id):
        if not all(str(value).strip() for value in (item_id, locator, kind, observation_id)):
            raise ValueError("feedback requires Item, Observation, kind, and locator")
        with self.transaction(immediate=True) as c:
            if not c.execute("SELECT 1 FROM items_index WHERE item_id=?", (item_id,)).fetchone():
                raise KeyError(item_id)
            if not c.execute("SELECT 1 FROM observations WHERE observation_id=?", (observation_id,)).fetchone():
                raise KeyError(observation_id)
            c.execute(
                """INSERT OR IGNORE INTO feedback(item_id,observation_id,locator,kind,created_at)
                   VALUES(?,?,?,?,?)""",
                (item_id,observation_id,locator,kind,now_iso()),
            )
            row = c.execute(
                """SELECT * FROM feedback
                   WHERE item_id=? AND observation_id=? AND locator=? AND kind=?""",
                (item_id,observation_id,locator,kind),
            ).fetchone()
            return dict(row)

    def resolve_evidence_run(self, evidence_run):
        parts = str(evidence_run).split('/', 1)
        if len(parts) != 2 or not parts[0] or not re.fullmatch(r"RUN-[0-9]{4,}", parts[1]):
            raise ValueError("evidence Run must be ITEM/RUN-0001")
        item_id, run_id = parts
        if self.get_run(item_id, run_id) is None:
            raise KeyError(f"unknown Run: {evidence_run}")
        return item_id, run_id

    def record_publication_receipt(self, domain, locator, sha256, evidence_run):
        target = Path(locator)
        if not domain or not target.is_absolute() or not target.is_file():
            raise ValueError("publication receipt requires domain and existing absolute current locator")
        if not re.fullmatch(r"[0-9a-f]{64}", str(sha256)):
            raise ValueError("publication receipt requires SHA-256")
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
            raise ValueError("publication receipt hash does not match current projection")
        item_id, run_id = self.resolve_evidence_run(evidence_run)
        with self.transaction(immediate=True) as c:
            existing = c.execute(
                "SELECT * FROM publication_receipts WHERE domain=? AND locator=? AND evidence_run=?",
                (domain, str(target), evidence_run),
            ).fetchone()
            if existing is not None:
                if existing['sha256'] != sha256 or existing['item_id'] != item_id or existing['run_id'] != run_id:
                    raise ValueError("publication identity already has a different projection")
                return dict(existing)
            c.execute(
                """INSERT INTO publication_receipts
                   (domain,object_id,revision,locator,sha256,evidence_run,item_id,run_id,created_at)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (domain, 'current', sha256, str(target), sha256, evidence_run, item_id, run_id, now_iso()),
            )
            row = c.execute(
                "SELECT * FROM publication_receipts WHERE domain=? AND locator=? AND evidence_run=?",
                (domain, str(target), evidence_run),
            ).fetchone()
        return dict(row)

    def get_publication_receipt(self, domain, locator, evidence_run):
        with self.connection() as c:
            row = c.execute(
                "SELECT * FROM publication_receipts WHERE domain=? AND locator=? AND evidence_run=?",
                (domain, locator, evidence_run),
            ).fetchone()
        return dict(row) if row is not None else None

    def list_deliverables(self, item_id: str) -> list[dict]:
        with self.connection() as conn:
            rows = conn.execute(
                "SELECT deliverable_id,kind,locator,created_at FROM deliverables WHERE item_id=? ORDER BY deliverable_id",
                (item_id,),
            ).fetchall()
        return [
            {"id": row["deliverable_id"], "item": item_id, "kind": row["kind"], "locator": row["locator"], "created_at": row["created_at"]}
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
        return self.upsert_context(context)

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
        target = RunState(target)
        if target in {RunState.CANCELLED, RunState.INVALID}:
            # Those two are operator terminations, not forward steps. They owe a
            # reason, an actor, and an audit record, so they belong to cancel_run.
            raise ValueError(
                f"advance_run cannot end a Run as {target.value}; "
                "use cancel_run (CLI: run cancel / run abandon) so the reason, actor, "
                "and audit event are preserved"
            )
        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
            if row is None:
                raise KeyError(f"unknown Run: {item_id}/{run_id}")
            current = RunState(row["state"])
            # SUCCEEDED and FAILED are verdicts, not steps. A Run that reaches
            # either one through here would carry a claim — "this run succeeded"
            # — with no evidence row behind it: no verdict, no exit code, no
            # revision. The ledger would say a thing happened and nothing would
            # say on what basis, which is the failure this whole release is
            # about one plane over.
            #
            # FAILED is only refused from FINALIZING, where finalize_run is the
            # producer and records the verdict. From PREPARING or RUNNING it is
            # an abort rather than a judgement, finalize_run has no route to it
            # (it requires FINALIZING), and refusing it would remove a
            # capability instead of redirecting one.
            if target is RunState.SUCCEEDED or (
                target is RunState.FAILED and current is RunState.FINALIZING
            ):
                raise ValueError(
                    f"advance_run cannot conclude a Run as {target.value}; a verdict is "
                    "not a transition. Use finalize_run (CLI: run finalize), which "
                    "records the verdict, exit code and revision the claim rests on. "
                    "If the Run is being abandoned rather than judged, use cancel_run "
                    "(CLI: run abandon)."
                )
            target = transition_run(current, target)
            ended = now_iso() if target in RUN_TERMINAL else ""
            conn.execute(
                "UPDATE runs SET state=?,ended_at=? WHERE item_id=? AND run_id=?",
                (target.value, ended, item_id, run_id),
            )
        return self.get_run(item_id, run_id)

    def cancel_run(
        self,
        item_id: str,
        run_id: str,
        *,
        reason: str,
        actor: str,
        reason_code: RunCancelReason | str | None = None,
        target: RunState | str = RunState.CANCELLED,
        evidence: tuple[str, ...] = (),
        operation: str = "cancel",
        acknowledge_active_worker: bool = False,
        dry_run: bool = False,
    ) -> dict:
        """End a Run by explicit operator action, and record why.

        This is the audited way to reach CANCELLED or INVALID.  It moves the Run
        and appends one item_events record.  It deliberately does not touch the
        Item, artifacts, worktrees, or any runtime resource: terminating a Run is
        a control-plane fact, not a cleanup.
        """
        if operation not in {"cancel", "abandon"}:
            raise ValueError(f"unsupported termination operation: {operation}")
        if not str(reason).strip():
            raise ValueError("run termination requires a non-empty reason")
        if not str(actor).strip():
            raise ValueError("run termination requires a non-empty actor")
        reason_code = (
            RunCancelReason(reason_code)
            if reason_code
            else (RunCancelReason.ABANDONED if operation == "abandon" else RunCancelReason.OPERATOR_CANCELLED)
        )
        target = RunState(target)
        if target not in {RunState.CANCELLED, RunState.INVALID}:
            raise ValueError(f"run termination target must be CANCELLED or INVALID, not {target.value}")
        references = tuple(str(locator) for locator in evidence)
        if any(not Path(locator).is_absolute() for locator in references):
            raise ValueError("termination evidence references must be absolute paths")

        with self.transaction(immediate=True) as conn:
            row = conn.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
            if row is None:
                raise KeyError(f"unknown Run: {item_id}/{run_id}")
            current = RunState(row["state"])
            if current in RUN_TERMINAL:
                if current != target:
                    raise ValueError(
                        f"Run {item_id}/{run_id} already ended as {current.value}; "
                        f"refusing to restate it as {target.value}"
                    )
                # Nothing is written, so report what the ledger already says
                # rather than what this caller asked for: a reader asking why a
                # Run ended must not be handed a reason that was never recorded.
                recorded = self._recorded_termination(conn, item_id, run_id)
                return self._termination_record(
                    item_id, run_id,
                    recorded["operation"] if recorded else operation,
                    RunCancelReason(recorded["reason_code"]) if recorded else reason_code,
                    recorded["reason"] if recorded else "",
                    recorded["actor"] if recorded else "",
                    tuple(recorded["evidence"]) if recorded else (),
                    current, current, row["ended_at"],
                    runtime_may_still_be_active=bool(recorded and recorded["runtime_may_still_be_active"]),
                    already_terminal=True, dry_run=False,
                )
            # Legality stays with the state machine. cancel_run adds the reason,
            # the actor, and the audit record on top of it and nothing else.
            target = transition_run(current, target)
            # The control plane owns no worker handle: runs.state is a statement
            # of intent, not an observation of a live process. A RUNNING Run may
            # still have a worker executing, so require an explicit extra action.
            active = current == RunState.RUNNING
            if active and not acknowledge_active_worker:
                raise ValueError(
                    f"Run {item_id}/{run_id} is {current.value}: the control plane cannot observe or "
                    "stop a worker process, so terminating here would not end one. Re-run with "
                    "--acknowledge-active-worker to record the control-plane fact anyway."
                )
            at = now_iso()
            if not dry_run:
                conn.execute(
                    "UPDATE runs SET state=?,ended_at=? WHERE item_id=? AND run_id=?",
                    (target.value, at, item_id, run_id),
                )
                self._event(
                    conn,
                    item_id,
                    "run_terminated",
                    {
                        "run": run_id,
                        "operation": operation,
                        "from": current.value,
                        "to": target.value,
                        "reason_code": reason_code.value,
                        "reason": reason,
                        "actor": actor,
                        "evidence": list(references),
                        "runtime_may_still_be_active": active,
                    },
                    at=at,
                )
        return self._termination_record(
            item_id, run_id, operation, reason_code, reason, actor, references,
            current, target, at,
            runtime_may_still_be_active=active, already_terminal=False, dry_run=dry_run,
        )

    def _recorded_termination(self, conn, item_id: str, run_id: str) -> dict | None:
        """The operator termination already on record for this Run, if any."""
        rows = conn.execute(
            "SELECT payload_json FROM item_events WHERE item_id=? AND event_type='run_terminated' "
            "ORDER BY event_id DESC",
            (item_id,),
        ).fetchall()
        for row in rows:
            payload = json.loads(row["payload_json"])
            if payload.get("run") == run_id:
                return payload
        return None

    @staticmethod
    def _termination_record(
        item_id, run_id, operation, reason_code, reason, actor, references,
        from_state, to_state, ended_at, *, runtime_may_still_be_active, already_terminal, dry_run,
    ) -> dict:
        return {
            "item": item_id,
            "run": run_id,
            "operation": operation,
            "from": from_state.value,
            "to": to_state.value,
            "reason_code": reason_code.value,
            "reason": reason,
            "actor": actor,
            "evidence": list(references),
            "runtime_may_still_be_active": runtime_may_still_be_active,
            "already_terminal": already_terminal,
            "dry_run": dry_run,
            "ended_at": ended_at,
        }

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
