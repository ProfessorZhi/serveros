PRAGMA journal_mode = WAL;
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS projects (
    project TEXT PRIMARY KEY,
    prefix TEXT NOT NULL UNIQUE
);
CREATE TABLE IF NOT EXISTS id_sequences (
    scope TEXT NOT NULL,
    kind TEXT NOT NULL,
    next_value INTEGER NOT NULL,
    PRIMARY KEY (scope, kind)
);
CREATE TABLE IF NOT EXISTS items_index (
    item_id TEXT PRIMARY KEY,
    project TEXT NOT NULL REFERENCES projects(project),
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    card_locator TEXT NOT NULL,
    state TEXT NOT NULL,
    outcome TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS item_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items_index(item_id),
    event_type TEXT NOT NULL,
    at TEXT NOT NULL,
    payload_json TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS activities (
    item_id TEXT NOT NULL REFERENCES items_index(item_id),
    activity_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL,
    graph_revision INTEGER NOT NULL DEFAULT 1,
    PRIMARY KEY (item_id, activity_id)
);
CREATE TABLE IF NOT EXISTS activity_dependencies (
    item_id TEXT NOT NULL,
    activity_id TEXT NOT NULL,
    needs_activity_id TEXT NOT NULL,
    PRIMARY KEY (item_id, activity_id, needs_activity_id),
    FOREIGN KEY (item_id, activity_id) REFERENCES activities(item_id, activity_id),
    FOREIGN KEY (item_id, needs_activity_id) REFERENCES activities(item_id, activity_id)
);
CREATE TABLE IF NOT EXISTS execution_contexts (
    context_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL,
    activity_id TEXT NOT NULL,
    repo TEXT NOT NULL,
    mode TEXT NOT NULL,
    revision TEXT NOT NULL,
    worktree TEXT NOT NULL DEFAULT '',
    dirty INTEGER NOT NULL DEFAULT 0,
    UNIQUE (item_id, activity_id, repo),
    FOREIGN KEY (item_id, activity_id) REFERENCES activities(item_id, activity_id)
);
CREATE TABLE IF NOT EXISTS runs (
    item_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    activity_id TEXT NOT NULL,
    worker TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    state TEXT NOT NULL,
    artifact_dir TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (item_id, run_id),
    UNIQUE (item_id, activity_id, idempotency_key),
    FOREIGN KEY (item_id, activity_id) REFERENCES activities(item_id, activity_id)
);
CREATE TABLE IF NOT EXISTS run_steps (
    item_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_no INTEGER NOT NULL,
    name TEXT NOT NULL,
    state TEXT NOT NULL,
    compensation TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (item_id, run_id, step_no),
    FOREIGN KEY (item_id, run_id) REFERENCES runs(item_id, run_id)
);
CREATE TABLE IF NOT EXISTS leases (
    resource TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    token TEXT NOT NULL,
    expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS deliverables (
    deliverable_id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT NOT NULL REFERENCES items_index(item_id),
    kind TEXT NOT NULL,
    locator TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS migration_intents (
    project TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    cursor INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL
);
