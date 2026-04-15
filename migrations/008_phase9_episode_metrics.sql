CREATE TABLE IF NOT EXISTS session_metrics (
    session_id              TEXT PRIMARY KEY,
    bridge_id               TEXT NOT NULL,
    archive_id              TEXT NOT NULL,
    task_id                 TEXT,
    task_kind               TEXT,
    chain_extraction        TEXT NOT NULL,
    chain_reason            TEXT,
    promoted_count          INTEGER NOT NULL DEFAULT 0,
    review_count            INTEGER NOT NULL DEFAULT 0,
    new_count               INTEGER NOT NULL DEFAULT 0,
    update_count            INTEGER NOT NULL DEFAULT 0,
    confirm_count           INTEGER NOT NULL DEFAULT 0,
    skip_count              INTEGER NOT NULL DEFAULT 0,
    note_delta              INTEGER NOT NULL DEFAULT 0,
    created_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_session_metrics_task_kind ON session_metrics(task_kind, created_at);
CREATE INDEX IF NOT EXISTS idx_session_metrics_chain_extraction ON session_metrics(chain_extraction, created_at);

CREATE TABLE IF NOT EXISTS runtime_packet_metrics (
    query_id                     INTEGER PRIMARY KEY REFERENCES retrieval_queries(id) ON DELETE CASCADE,
    task_id                      TEXT,
    session_id                   TEXT,
    task_kind                    TEXT,
    retrieval_state              TEXT,
    recommendation               TEXT,
    primary_memory_count         INTEGER NOT NULL DEFAULT 0,
    same_episode_context_count   INTEGER NOT NULL DEFAULT 0,
    related_memory_count         INTEGER NOT NULL DEFAULT 0,
    created_at                   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_runtime_packet_metrics_task_kind ON runtime_packet_metrics(task_kind, created_at);

INSERT INTO schema_migrations(version) VALUES ('008_phase9_episode_metrics');
