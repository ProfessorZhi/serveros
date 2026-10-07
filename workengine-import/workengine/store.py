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


def _verdict_word(verdict) -> str:
    """One judgement, one spelling — the membership is left open on purpose.

    A verdict is judged by its WORD. `finalize_run` concludes a Run SUCCEEDED on
    `PASS` and FAILED on anything else, and `record_evidence` treats a changed
    word as a conflict, because the word is part of the evidence's identity.
    Nothing used to state which spelling was the one that counted, so `pass` and
    `PASS` were two different facts: the first concluded the Run FAILED and the
    second concluded it SUCCEEDED. A caller who wrote the lower-case one got
    back `{"state": "FAILED", "verdict": "pass"}` — a record that contradicts
    itself, produced by a call that reported no error.

    The membership stays open because the corpus is open: `PARTIAL`,
    `INCONCLUSIVE`, `SUCCESS` and `BUDGET_EXHAUSTED` are all recorded on this
    node's Runs, and closing the set would refuse history rather than spell it
    consistently. What is fixed here is the spelling.

    Case-folding is applied to what is compared as well as to what is written,
    so a row recorded before this change still replays as the SAME judgement
    instead of as a conflict about a different one.
    """
    return str(verdict).strip().upper()


class ActiveWorkerAckRequired(ValueError):
    """A RUNNING Run was terminated without anyone saying they meant to.

    Its own type because the caller has to be able to tell this refusal from
    every other refusal in the same call path and to act on it — an MCP tool
    that reported it as a generic failure would leave an agent re-reading a
    message to find out whether to retry, and the answer here is not "retry",
    it is "a human has to say the worker may still be running".

    A subclass of `ValueError` so that callers written against the older,
    untyped refusal keep working.
    """

    reason_code = "ACTIVE_WORKER_ACK_REQUIRED"


class EvidenceConflict(ValueError):
    """A registered evidence locator was replayed with different bytes or facts."""

    reason_code = "EVIDENCE_CONFLICT"


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
        """One candidate per requirement identity, and a collision says so.

        The two UNIQUE constraints on `item_candidates` ARE the idempotence of
        this entry point, and `INSERT OR IGNORE` is how the insert respects
        them. What `OR IGNORE` cannot do is tell apart the two reasons a row can
        be absent afterwards, and until 2026-09-25 it did not have to: the
        `candidate_claims` insert that follows ran anyway, referenced a
        candidate that had just been ignored, and the caller of a duplicate
        requirement was told `sqlite3.IntegrityError: FOREIGN KEY constraint
        failed`. Measured, on a fresh store, both for a repeated identity and
        for a repeated dedupe key.

        That is a wrong answer rather than a missing one: it names a foreign key
        as the problem when the problem is that this requirement already has a
        candidate, and a caller reading it looks at the wrong table. The row is
        therefore looked up FIRST and the two cases are answered separately:

          * the same `candidate_id`, and neither key half held by another row —
            a replay. The stored row is returned, which is what makes a retried
            `candidate create` idempotent. Note what a replay does NOT do: a
            different `title`, `kind` or `dedupe_key` in the second call is not
            written. New evidence does not revise the objective, and this is
            where that is enforced.
          * a different `candidate_id` for one requirement — a collision, and a
            refusal. Returning the other candidate's row would answer a question
            nobody asked (it is a different identity than the caller named), and
            inserting a second row is what the constraint exists to prevent.
            This is `invoke_once`'s rule one file over: same key with different
            input is refused, not reconciled.

        Either refusal names the half of the key that actually matched, read off
        the query that matched it, because a caller who is told the wrong half
        goes and looks at the wrong row.
        """
        if not claim_ids: raise ValueError("candidate requires claim provenance")
        with self.transaction(immediate=True) as c:
            if any(not c.execute("SELECT 1 FROM claims WHERE claim_id=?",(x,)).fetchone() for x in claim_ids): raise KeyError("unknown claim")
            # The identity half mirrors the INDEX it reports on, and `=` is what
            # mirrors it: SQLite treats every NULL as distinct in a UNIQUE
            # index, and `col = NULL` is false for every row, so a triple with a
            # NULL in it matches nothing here for the same reason it collides
            # with nothing there. Two candidates that both lack an external
            # identity are therefore two requirements, in the store and in this
            # lookup alike.
            #
            # A guard — `if external_id is not None and external_revision is not
            # None:` before adding the clause — was written here first, as the
            # more obvious way to say that. It was removed because mutating it
            # away changed no result: `=` already cannot match a NULL, so the
            # guard was decoration, and decoration is what a later reader
            # mistakes for the rule. `IS ?` is the form that WOULD be wrong —
            # NULL-safe, and therefore stricter than the index — and
            # `test_two_requirements_without_an_identity_are_two_candidates`
            # fails if anyone writes it.
            by_id = c.execute(
                "SELECT * FROM item_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
            # One query per UNIQUE constraint, and each a transcription of that
            # constraint. Which of them returns a row IS the reason the caller is
            # about to be refused, so the reason is read off the query that found
            # it rather than reconstructed by comparing the stored row back to
            # this call's arguments. That reconstruction was written here first,
            # as a single `OR`ed query followed by a message that interpolated
            # the CALLER's key: a collision on the identity half was then
            # reported as "already carries dedupe key 'd2'" when the stored row
            # held 'd1', measured on a real store. A refusal is read as a
            # statement of fact, and that one stated a fact that was not so.
            by_dedupe = c.execute(
                "SELECT * FROM item_candidates WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            by_identity = c.execute(
                "SELECT * FROM item_candidates"
                " WHERE domain = ? AND external_id = ? AND external_revision = ?",
                (domain, external_id, external_revision),
            ).fetchone()
            holders = {}
            if by_dedupe is not None:
                holders.setdefault(by_dedupe["candidate_id"], []).append(
                    f"dedupe key {dedupe_key!r}")
            if by_identity is not None:
                holders.setdefault(by_identity["candidate_id"], []).append(
                    f"requirement identity ({domain!r}, {external_id!r}, "
                    f"{external_revision!r})")
            # This call's own row is not a conflict with itself; what is left in
            # `holders` is by construction carried by someone else.
            holders.pop(candidate_id, None)
            if holders:
                naming = "; ".join(
                    f"candidate {cid} already carries {' and '.join(facts)}"
                    for cid, facts in holders.items())
                if by_id is None:
                    raise ValueError(
                        f"{naming}; this call named {candidate_id}. One "
                        f"requirement has one candidate, so reuse that id, or the "
                        f"requirement is not the one this call claims it is.")
                # Both halves are taken, and not by this call's row. Naming only
                # one of them would send the caller to fix half of a conflict.
                raise ValueError(
                    f"candidate {candidate_id} already exists, and {naming}. Two "
                    f"rows are being named for one requirement, and this call is "
                    f"not a replay of either.")
            if by_id is not None:
                for x in claim_ids: c.execute("INSERT OR IGNORE INTO candidate_claims VALUES (?,?)",(candidate_id,x))
                return dict(c.execute("SELECT * FROM item_candidates WHERE candidate_id=?",(candidate_id,)).fetchone())
            c.execute("INSERT INTO item_candidates(candidate_id,project,title,kind,state,dedupe_key,domain,external_id,external_revision,approved_item_id,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",(candidate_id,project,title,kind,"NEW",dedupe_key,domain,external_id,external_revision,None,now_iso()))
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

    def _require_run(self, connection, item_id, run_id):
        """The `runs` row for (item_id, run_id), or a refusal that names which half is wrong.

        Until 2026-10-08 `run evidence` took an Item and a Run as two bare
        positionals and checked neither. `record_evidence` wrote the pair
        through, so `workengine run evidence SRV-0011 19 --locator …` returned
        `evidence_id 224` for a Run that does not exist — measured on this
        node's production store, exit code 0 — and the ledger held a row
        attributed to a Run nobody can look up. The `evidence` table declares
        no foreign key (v4 SQL, `migrations.py`) while `publication_receipts`
        does, so the database never had an opinion either.

        An unknown Item and an unknown Run are two different answers and are
        reported as two different answers, because a caller who mistyped the
        Item and a caller who mistyped the Run have different repairs to make.
        A Run id that exists under some *other* Item is a third: the row the
        caller named is real, it is just not theirs, and saying "unknown Run"
        would send them looking for a typo that is not there.

        This is the lookup SQLite's own foreign key would have done, said in
        words — the same choice `create_candidate` makes one method above,
        where an `IntegrityError` naming a constraint is rejected in favour of
        naming the table the caller actually has to look at.
        """
        if connection.execute("SELECT 1 FROM items_index WHERE item_id=?", (item_id,)).fetchone() is None:
            raise KeyError(f"unknown Item: {item_id}")
        row = connection.execute("SELECT * FROM runs WHERE item_id=? AND run_id=?", (item_id, run_id)).fetchone()
        if row is not None:
            return row
        owners = [r["item_id"] for r in connection.execute(
            "SELECT item_id FROM runs WHERE run_id=? ORDER BY item_id", (run_id,))]
        if owners:
            raise KeyError(
                f"Run {run_id} belongs to Item{'s' if len(owners) > 1 else ''} "
                f"{', '.join(owners)}, not {item_id}")
        raise KeyError(f"unknown Run: {item_id}/{run_id}")

    def record_evidence(self, item_id, run_id, *, kind, locator, command, cases, exit_code, revision, dirty, verdict):
        verdict = _verdict_word(verdict)
        if not Path(locator).is_absolute() or not Path(locator).is_file(): raise ValueError("evidence must be an existing absolute file")
        digest=hashlib.sha256(Path(locator).read_bytes()).hexdigest()
        if not digest: raise ValueError("evidence requires SHA-256")
        _reject_secrets({'kind':kind,'locator':locator,'command':command,'cases':cases,'revision':revision,'verdict':verdict,'exit_code':exit_code,'dirty':dirty})
        with self.transaction(immediate=True) as c:
            self._require_run(c, item_id, run_id)
            previous = c.execute("SELECT * FROM evidence WHERE item_id=? AND run_id=? AND locator=?",
                                 (item_id, run_id, locator)).fetchone()
            requested = dict(kind=kind, sha256=digest, command=command, cases_json=json.dumps(cases),
                             exit_code=exit_code, revision=revision, dirty=int(dirty), verdict=verdict)
            if previous is not None:
                # The stored verdict is folded before comparison for the same
                # reason the arriving one is: a row written as `pass` and a
                # replay written as `PASS` are one judgement, and reporting them
                # as a conflict would refuse a caller who corrected their own
                # spelling. A genuinely different word (`FAIL` for `PASS`) still
                # differs after folding, so the conflict that test pins remains.
                changed = [field for field, value in requested.items()
                           if (_verdict_word(previous[field]) if field == "verdict" else previous[field]) != value]
                if changed:
                    raise EvidenceConflict(
                        f"evidence_id {previous['evidence_id']} already records this locator; "
                        f"changed fields: {', '.join(changed)}. Preserve the existing record and "
                        "register the corrected artifact at a new immutable versioned locator; "
                        "changing --kind does not create a new evidence identity.")
                return dict(previous)
            c.execute("INSERT INTO evidence(item_id,run_id,kind,locator,sha256,command,cases_json,exit_code,revision,dirty,verdict,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",(item_id,run_id,kind,locator,digest,command,json.dumps(cases),exit_code,revision,int(dirty),verdict,now_iso()))
            return dict(c.execute("SELECT * FROM evidence WHERE item_id=? AND run_id=? AND locator=?",(item_id,run_id,locator)).fetchone())

    def finalize_run(self, item_id, run_id, *, verdict, exit_code, command, cases, revision, dirty):
        verdict = _verdict_word(verdict)
        with self.transaction(immediate=True) as c:
            r=self._require_run(c, item_id, run_id)
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

    def list_runs(self, item_id: str | None = None, *, state: RunState | str | None = None) -> list[Run]:
        """Runs, newest first, optionally narrowed to one Item or one state.

        No default state filter. A caller that asked for "the runs" and got only
        the live ones would read the list as the whole history, and a finished
        Run is exactly the one somebody is usually asking about.
        """
        query = "SELECT * FROM runs"
        clauses: list[str] = []
        params: list = []
        if item_id is not None:
            clauses.append("item_id=?")
            params.append(item_id)
        if state is not None:
            clauses.append("state=?")
            params.append(RunState(state).value)
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY started_at DESC, run_id DESC"
        with self.connection() as conn:
            rows = conn.execute(query, params).fetchall()
        return [self._run(row) for row in rows]

    def run_events(self, item_id: str, run_id: str) -> list[dict]:
        """The Item's events that are about this Run, oldest first.

        Read from `item_events` rather than from a run-scoped table, because
        there is one event ledger and a second one would be a second history of
        the same work that disagrees with the first.

        `run_terminated` is the only run-scoped event type the ledger records
        today. That is a real limit of the evidence, not of this query: a Run's
        forward transitions live in `runs.state` and were never evented, so an
        empty list here means "nothing was recorded about this Run" and not
        "nothing happened to it". `run_events_summary` states the difference for
        callers that would otherwise read the empty list as the second thing.
        """
        return [
            record
            for record in self.item_events(item_id)
            if str((record.get("payload") or {}).get("run") or "") == str(run_id)
        ]

    def run_events_summary(self, item_id: str, run_id: str) -> dict:
        """`run_events`, wrapped with what the empty case does and does not mean.

        The extra keys exist so that a caller cannot read `events: []` as "this
        Run did nothing". An unknown Run is refused rather than answered with an
        empty history, because a Run that does not exist and a Run that nothing
        was recorded about are different findings.
        """
        run = self.get_run(item_id, run_id)
        if run is None:
            raise KeyError(f"unknown Run: {item_id}/{run_id}")
        events = self.run_events(item_id, run_id)
        return {
            "item": item_id,
            "run": run_id,
            "state": run.state.value,
            "events": events,
            "count": len(events),
            "recorded_event_types": sorted({record["event_type"] for record in events}),
            # Stated in the answer rather than left to the caller's memory of the
            # schema, because the wrong reading — "nothing happened" — is the one
            # an empty list invites.
            "ledger_covers": [
                "operator terminations (event_type run_terminated)",
            ],
            "not_recorded": (
                "Forward Run transitions are not evented. This Run's current "
                "state is authoritative in runs.state; its history through "
                "ALLOCATED/PREPARING/RUNNING/FINALIZING was never written to the "
                "event ledger, so an empty list here does not mean the Run did "
                "nothing."
            ),
        }

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
                raise ActiveWorkerAckRequired(
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
