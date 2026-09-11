BEGIN IMMEDIATE;
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    path TEXT NOT NULL,
    document TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS analysis_runs (
    run_id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    request_hash TEXT NOT NULL,
    raw_request TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    attempt_id TEXT,
    lease_until REAL,
    document TEXT NOT NULL,
    UNIQUE(owner, idempotency_key)
);
CREATE INDEX IF NOT EXISTS run_dispatch ON analysis_runs(status, created_at);
PRAGMA user_version = 1;
COMMIT;
