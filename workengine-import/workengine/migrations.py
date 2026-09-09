"""Additive, versioned SQLite migrations for the ServerOS v3 control plane."""
from __future__ import annotations
import json
from .model import now_iso

V3_SQL = """
CREATE TABLE IF NOT EXISTS source_objects (source_id TEXT PRIMARY KEY, domain TEXT NOT NULL, stable_id TEXT NOT NULL, revision TEXT NOT NULL, locator TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(domain,stable_id,revision));
CREATE TABLE IF NOT EXISTS observations (observation_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source_objects(source_id), semantic_type TEXT NOT NULL, source_origin TEXT NOT NULL DEFAULT 'online', carrier TEXT NOT NULL DEFAULT 'other', information_form TEXT NOT NULL DEFAULT 'curated', content_hash TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(source_id,observation_id));
CREATE TABLE IF NOT EXISTS claims (claim_id TEXT PRIMARY KEY, locator TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'ACTIVE', created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_observations (claim_id TEXT NOT NULL REFERENCES claims(claim_id), observation_id TEXT NOT NULL REFERENCES observations(observation_id), PRIMARY KEY(claim_id,observation_id));
CREATE TABLE IF NOT EXISTS item_candidates (candidate_id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'NEW', dedupe_key TEXT NOT NULL UNIQUE, external_id TEXT, external_revision TEXT, approved_item_id TEXT, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidate_claims (candidate_id TEXT NOT NULL REFERENCES item_candidates(candidate_id), claim_id TEXT NOT NULL REFERENCES claims(claim_id), PRIMARY KEY(candidate_id,claim_id));
CREATE TABLE IF NOT EXISTS external_item_mappings (domain TEXT NOT NULL, external_id TEXT NOT NULL, revision TEXT NOT NULL, item_id TEXT NOT NULL REFERENCES items_index(item_id), PRIMARY KEY(domain,external_id,revision));
CREATE TABLE IF NOT EXISTS evidence (evidence_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL, run_id TEXT NOT NULL, kind TEXT NOT NULL, locator TEXT NOT NULL, sha256 TEXT NOT NULL, command TEXT NOT NULL, cases_json TEXT NOT NULL, exit_code INTEGER NOT NULL, revision TEXT NOT NULL, dirty INTEGER NOT NULL, verdict TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(item_id,run_id,locator));
CREATE TABLE IF NOT EXISTS feedback (feedback_id INTEGER PRIMARY KEY AUTOINCREMENT, item_id TEXT NOT NULL REFERENCES items_index(item_id), locator TEXT NOT NULL, kind TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS publication_receipts (receipt_id INTEGER PRIMARY KEY AUTOINCREMENT, domain TEXT NOT NULL, object_id TEXT NOT NULL, revision TEXT NOT NULL, locator TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(domain,object_id,revision));
"""

V5_SQL = """
CREATE TABLE publication_receipts_v5 (
    receipt_id INTEGER PRIMARY KEY AUTOINCREMENT,
    domain TEXT NOT NULL,
    object_id TEXT NOT NULL,
    revision TEXT NOT NULL,
    locator TEXT NOT NULL,
    sha256 TEXT,
    evidence_run TEXT,
    item_id TEXT,
    run_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(domain,locator,evidence_run),
    FOREIGN KEY(item_id,run_id) REFERENCES runs(item_id,run_id)
);
INSERT INTO publication_receipts_v5
    (receipt_id,domain,object_id,revision,locator,created_at)
    SELECT receipt_id,domain,object_id,revision,locator,created_at
    FROM publication_receipts;
DROP TABLE publication_receipts;
ALTER TABLE publication_receipts_v5 RENAME TO publication_receipts;
"""

def migrate(conn) -> None:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)")
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=3").fetchone() is None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in V3_SQL.split(';'):
                if statement.strip(): conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(3,?)", (now_iso(),))
            conn.commit()
        except Exception:
            conn.rollback(); raise
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=4").fetchone() is None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            cols={r[1] for r in conn.execute("PRAGMA table_info(item_candidates)")}
            if 'domain' not in cols:
                conn.execute("CREATE TABLE item_candidates_v4 (candidate_id TEXT PRIMARY KEY, project TEXT NOT NULL, title TEXT NOT NULL, kind TEXT NOT NULL, state TEXT NOT NULL DEFAULT 'NEW', dedupe_key TEXT NOT NULL UNIQUE, domain TEXT NOT NULL DEFAULT '', external_id TEXT, external_revision TEXT, approved_item_id TEXT, created_at TEXT NOT NULL, UNIQUE(domain,external_id,external_revision))")
                conn.execute("INSERT INTO item_candidates_v4(candidate_id,project,title,kind,state,dedupe_key,domain,external_id,external_revision,approved_item_id,created_at) SELECT candidate_id,project,title,kind,state,dedupe_key,'',external_id,external_revision,approved_item_id,created_at FROM item_candidates")
                conn.execute("CREATE TABLE candidate_claims_v4 (candidate_id TEXT NOT NULL REFERENCES item_candidates_v4(candidate_id), claim_id TEXT NOT NULL REFERENCES claims(claim_id), PRIMARY KEY(candidate_id,claim_id))")
                conn.execute("INSERT INTO candidate_claims_v4 SELECT * FROM candidate_claims")
                conn.execute("DROP TABLE candidate_claims")
                conn.execute("DROP TABLE item_candidates")
                conn.execute("ALTER TABLE item_candidates_v4 RENAME TO item_candidates")
                conn.execute("ALTER TABLE candidate_claims_v4 RENAME TO candidate_claims")
            else:
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS candidate_external_identity ON item_candidates(domain,external_id,external_revision)")
            conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(4,?)", (now_iso(),))
            conn.commit()
        except Exception:
            conn.rollback(); raise
    if conn.execute("SELECT 1 FROM schema_migrations WHERE version=5").fetchone() is None:
        conn.execute("BEGIN IMMEDIATE")
        try:
            for statement in V5_SQL.split(';'):
                if statement.strip(): conn.execute(statement)
            conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES(5,?)", (now_iso(),))
            conn.commit()
        except Exception:
            conn.rollback(); raise
