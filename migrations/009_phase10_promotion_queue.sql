CREATE TABLE IF NOT EXISTS promotion_queue (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id            TEXT NOT NULL REFERENCES archive(id) ON DELETE CASCADE,
    bridge_id             TEXT NOT NULL,
    session_id            TEXT NOT NULL,
    task_id               TEXT,
    task_kind             TEXT,
    content               TEXT NOT NULL,
    summary_payload       TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(summary_payload)),
    chain_extraction      TEXT CHECK (chain_extraction IN ('explicit_chain', 'inferred_chain', 'no_chain')),
    chain_reason          TEXT,
    status                TEXT NOT NULL DEFAULT 'pending'
                          CHECK (status IN ('pending', 'processing', 'processed', 'skipped', 'error')),
    attempt_count         INTEGER NOT NULL DEFAULT 0,
    last_error            TEXT,
    queued_at             TEXT NOT NULL DEFAULT (datetime('now')),
    last_attempted_at     TEXT,
    processed_at          TEXT,
    UNIQUE(archive_id)
);

CREATE INDEX IF NOT EXISTS idx_promotion_queue_status_queued_at ON promotion_queue(status, queued_at);
CREATE INDEX IF NOT EXISTS idx_promotion_queue_session_id ON promotion_queue(session_id);
CREATE INDEX IF NOT EXISTS idx_promotion_queue_task_kind ON promotion_queue(task_kind, queued_at);

INSERT INTO schema_migrations(version) VALUES ('009_phase10_promotion_queue');
