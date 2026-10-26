"""Persistent application-path fence for owner-managed Work Items.

Management is derived from the immutable first lease-acquire receipt in the
existing Work database. The existing leases table remains the sole write
lease. A connection starts denied; the owner service enables an exact Item
only while holding the control-ledger lock and Work write transaction.

This prevents legacy application writes. A database owner who changes schema
or registers their own SQLite function is outside this application boundary.
"""
from __future__ import annotations
from contextlib import contextmanager
from pathlib import Path


def file_identity(path):
    """Existing local main-file identity, never a row-derived database ID."""
    path = Path(path)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("WORK_STORE_MISMATCH: existing absolute database required")
    path = path.resolve(strict=True)
    stat = path.stat()
    if not stat.st_ino:
        raise ValueError("WORK_STORE_MISMATCH: stable local file identity unavailable")
    return {"path": str(path), "device": stat.st_dev, "inode": stat.st_ino}


def migrate(conn):
    """Install receipts and row guards after the existing schema migrations."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS owner_operation_receipts (
            operation_id TEXT PRIMARY KEY, item_ref TEXT NOT NULL REFERENCES items_index(item_id),
            verb TEXT NOT NULL, digest TEXT NOT NULL, receipt_json TEXT NOT NULL)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS owner_receipts_scope
                        ON owner_operation_receipts(item_ref, verb)""")
        conn.execute("""CREATE TABLE IF NOT EXISTS owner_transaction_scopes (
            item_ref TEXT PRIMARY KEY CHECK(length(item_ref)>0),
            commit_barrier TEXT NOT NULL DEFAULT '' CHECK(commit_barrier=''),
            FOREIGN KEY(commit_barrier) REFERENCES owner_transaction_scopes(item_ref)
                DEFERRABLE INITIALLY DEFERRED)""")
        if conn.execute("SELECT 1 FROM owner_transaction_scopes LIMIT 1").fetchone():
            raise ValueError("OWNER_COMMIT_FENCE_CORRUPT: committed scope marker exists")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS owner_scope_insert BEFORE INSERT
            ON owner_transaction_scopes WHEN owner_commit_scope(NEW.item_ref) != 1
            BEGIN SELECT RAISE(ABORT, 'OWNER_COMMIT_FENCE_REQUIRED'); END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS owner_scope_update BEFORE UPDATE
            ON owner_transaction_scopes
            BEGIN SELECT RAISE(ABORT, 'OWNER_COMMIT_FENCE_IMMUTABLE_SCOPE'); END""")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS owner_scope_delete BEFORE DELETE
            ON owner_transaction_scopes WHEN owner_commit_scope(OLD.item_ref) != 1
            BEGIN SELECT RAISE(ABORT, 'OWNER_COMMIT_FENCE_REQUIRED'); END""")
        # The mappings are schema-owned, never supplied by a request.
        scopes = {table: "{row}.item_id" for table in (
            "items_index", "item_events", "activities", "activity_dependencies",
            "execution_contexts", "runs", "run_steps", "deliverables",
            "external_item_mappings", "evidence", "feedback", "publication_receipts")}
        scopes["leases"] = "CASE WHEN {row}.resource LIKE 'work-write:%' THEN substr({row}.resource,12) END"
        scopes["item_candidates"] = "{row}.approved_item_id"
        scopes["candidate_claims"] = "(SELECT approved_item_id FROM item_candidates WHERE candidate_id={row}.candidate_id)"
        for table, scope in scopes.items():
            _guards(conn, table, lambda row, scope=scope: "r.item_ref = " + scope.format(row=row))
        # Shared upstream facts may be linked to more than one managed Item.
        # Updating them needs every affected Item; this single-Item operation
        # therefore refuses shared multi-Item changes rather than widening scope.
        linked = ("SELECT ic.approved_item_id FROM item_candidates ic JOIN candidate_claims cc "
                  "ON cc.candidate_id=ic.candidate_id ")
        _guards(conn, "claims", lambda row: "r.item_ref IN (" + linked + f"WHERE cc.claim_id={row}.claim_id)")
        _guards(conn, "claim_observations", lambda row: "r.item_ref IN (" + linked + f"WHERE cc.claim_id={row}.claim_id)")
        _guards(conn, "observations", lambda row: "r.item_ref IN (" + linked +
                "JOIN claim_observations co ON co.claim_id=cc.claim_id " +
                f"WHERE co.observation_id={row}.observation_id "
                "UNION SELECT f.item_id FROM feedback f " +
                f"WHERE f.observation_id={row}.observation_id)")
        _guards(conn, "source_objects", lambda row: "r.item_ref IN (" + linked +
                "JOIN claim_observations co ON co.claim_id=cc.claim_id "
                "JOIN observations o ON o.observation_id=co.observation_id " +
                f"WHERE o.source_id={row}.source_id "
                "UNION SELECT f.item_id FROM feedback f "
                "JOIN observations o ON o.observation_id=f.observation_id " +
                f"WHERE o.source_id={row}.source_id)")
        _guards(conn, "id_sequences", lambda row:
                f"(r.item_ref={row}.scope OR 'work-write:' || r.item_ref={row}.scope)")
        for table in ("projects", "migration_intents"):
            _guards(conn, table, lambda row: "r.item_ref IN (SELECT item_id FROM items_index " +
                    f"WHERE project={row}.project)")
        conn.execute("DROP TRIGGER IF EXISTS owner_receipt_insert")
        conn.execute("DROP TRIGGER IF EXISTS owner_enrollment_migration")
        conn.execute("""CREATE TRIGGER owner_enrollment_migration BEFORE INSERT
            ON owner_operation_receipts WHEN NEW.verb='lease.acquire' AND EXISTS
                (SELECT 1 FROM migration_intents m JOIN items_index i ON i.project=m.project
                 WHERE i.item_id=NEW.item_ref AND m.status NOT IN ('PLANNED','COMPLETED','COMPENSATED'))
            BEGIN SELECT RAISE(ABORT, 'OWNER_MIGRATION_ACTIVE'); END""")
        conn.execute("""CREATE TRIGGER owner_receipt_insert BEFORE INSERT
            ON owner_operation_receipts WHEN NOT EXISTS
                (SELECT 1 FROM owner_transaction_scopes s WHERE s.item_ref=NEW.item_ref)
            BEGIN SELECT RAISE(ABORT, 'OWNER_COMMIT_FENCE_REQUIRED'); END""")
        for action in ("UPDATE", "DELETE"):
            conn.execute(f"""CREATE TRIGGER IF NOT EXISTS owner_receipt_{action.lower()}
                BEFORE {action} ON owner_operation_receipts
                BEGIN SELECT RAISE(ABORT, 'OWNER_COMMIT_FENCE_IMMUTABLE_RECEIPT'); END""")
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


def _guards(conn, table, predicate):
    for action, rows in (("INSERT", ("NEW",)), ("UPDATE", ("OLD", "NEW")), ("DELETE", ("OLD",))):
        condition = " OR ".join(
            "EXISTS (SELECT 1 FROM owner_operation_receipts r WHERE r.verb='lease.acquire' AND "
            f"({predicate(row)}) AND NOT EXISTS (SELECT 1 FROM owner_transaction_scopes s "
            "WHERE s.item_ref=r.item_ref))" for row in rows)
        conn.execute(f"DROP TRIGGER IF EXISTS owner_scope_{table}_{action.lower()}")
        conn.execute(f"""CREATE TRIGGER owner_scope_{table}_{action.lower()}
            BEFORE {action} ON {table} WHEN {condition}
            BEGIN SELECT RAISE(ABORT, 'OWNER_COMMIT_FENCE_REQUIRED'); END""")


@contextmanager
def scope(conn, item_ref):
    """Enable an already-authorized exact scope for this transaction only.

    The deferred self-FK cannot be satisfied by any nonempty table: every row
    references the forbidden empty key. Forgetting DELETE therefore fails
    COMMIT, and a process kill rolls the marker back with the mutation. This
    table can never contain committed authority on an owner connection.
    """
    if not conn.in_transaction or conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ValueError("OWNER_COMMIT_FENCE_REQUIRED: transaction with foreign keys required")
    conn.execute("INSERT INTO owner_transaction_scopes(item_ref) VALUES (?)", (item_ref,))
    try:
        yield
    finally:
        conn.execute("DELETE FROM owner_transaction_scopes WHERE item_ref=?", (item_ref,))
