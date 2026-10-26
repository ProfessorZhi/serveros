"""Persistent application-path fence for owner-managed Work Items.

Management is derived from the immutable first lease-acquire receipt in the
existing Work database. The existing leases table remains the sole write
lease. A connection starts denied; the owner service enables an exact Item
only while holding the control-ledger lock and Work write transaction.

This prevents legacy application writes. A database owner who changes schema
or registers their own SQLite function is outside this application boundary.
"""
from __future__ import annotations


def migrate(conn):
    """Install receipts and row guards after the existing schema migrations."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("""CREATE TABLE IF NOT EXISTS owner_operation_receipts (
            operation_id TEXT PRIMARY KEY, item_ref TEXT NOT NULL REFERENCES items_index(item_id),
            verb TEXT NOT NULL, digest TEXT NOT NULL, receipt_json TEXT NOT NULL)""")
        conn.execute("""CREATE INDEX IF NOT EXISTS owner_receipts_scope
                        ON owner_operation_receipts(item_ref, verb)""")
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
                f"WHERE co.observation_id={row}.observation_id)")
        _guards(conn, "source_objects", lambda row: "r.item_ref IN (" + linked +
                "JOIN claim_observations co ON co.claim_id=cc.claim_id "
                "JOIN observations o ON o.observation_id=co.observation_id " +
                f"WHERE o.source_id={row}.source_id)")
        _guards(conn, "id_sequences", lambda row:
                f"(r.item_ref={row}.scope OR 'work-write:' || r.item_ref={row}.scope)")
        for table in ("projects", "migration_intents"):
            _guards(conn, table, lambda row: "r.item_ref IN (SELECT item_id FROM items_index " +
                    f"WHERE project={row}.project)")
        conn.execute("""CREATE TRIGGER IF NOT EXISTS owner_receipt_insert BEFORE INSERT
            ON owner_operation_receipts WHEN owner_commit_scope(NEW.item_ref) != 1
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
            f"({predicate(row)}) AND owner_commit_scope(r.item_ref) != 1)" for row in rows)
        conn.execute(f"""CREATE TRIGGER IF NOT EXISTS owner_scope_{table}_{action.lower()}
            BEFORE {action} ON {table} WHEN {condition}
            BEGIN SELECT RAISE(ABORT, 'OWNER_COMMIT_FENCE_REQUIRED'); END""")
