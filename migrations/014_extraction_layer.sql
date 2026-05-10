CREATE TABLE IF NOT EXISTS extraction_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('artifact', 'episode', 'session')),
    archive_id TEXT,
    conversation_id TEXT,
    episode_id TEXT,
    bridge_id TEXT,
    session_id TEXT,
    task_id TEXT,
    duplicate_state TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'processing', 'processed', 'skipped', 'error')),
    attempt_count INTEGER NOT NULL DEFAULT 0,
    queued_at TEXT NOT NULL DEFAULT (datetime('now')),
    started_at TEXT,
    finished_at TEXT,
    last_error TEXT,
    projection_metadata_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(projection_metadata_json)),
    window_count INTEGER NOT NULL DEFAULT 0,
    artifact_count INTEGER NOT NULL DEFAULT 0,
    visual_snippet_count INTEGER NOT NULL DEFAULT 0,
    candidate_count INTEGER NOT NULL DEFAULT 0,
    validation_error_count INTEGER NOT NULL DEFAULT 0,
    CHECK (
        (source_kind = 'artifact' AND archive_id IS NOT NULL AND conversation_id IS NULL AND episode_id IS NULL)
        OR (source_kind = 'session' AND archive_id IS NOT NULL AND conversation_id IS NULL AND episode_id IS NULL)
        OR (source_kind = 'episode' AND archive_id IS NULL AND conversation_id IS NOT NULL AND episode_id IS NOT NULL)
    )
);

CREATE UNIQUE INDEX IF NOT EXISTS uniq_extraction_queue_archive_source
    ON extraction_queue(source_kind, archive_id)
    WHERE archive_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_extraction_queue_episode_source
    ON extraction_queue(source_kind, conversation_id, episode_id)
    WHERE conversation_id IS NOT NULL AND episode_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_extraction_queue_status_queued
    ON extraction_queue(status, queued_at, id);

CREATE TABLE IF NOT EXISTS extraction_visual_snippets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES extraction_queue(id) ON DELETE CASCADE,
    artifact_id TEXT NOT NULL,
    marker TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    snippet_json TEXT NOT NULL CHECK (json_valid(snippet_json)),
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_extraction_visual_snippets_queue
    ON extraction_visual_snippets(queue_id, artifact_id);

CREATE TABLE IF NOT EXISTS extraction_candidates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id INTEGER NOT NULL REFERENCES extraction_queue(id) ON DELETE CASCADE,
    candidate_index INTEGER NOT NULL,
    candidate_type TEXT NOT NULL,
    title TEXT NOT NULL,
    payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
    validation_status TEXT NOT NULL DEFAULT 'invalid'
        CHECK (validation_status IN ('invalid', 'valid', 'promotion_ready')),
    validation_errors_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(validation_errors_json)),
    promotion_state TEXT NOT NULL DEFAULT 'not_ready'
        CHECK (promotion_state IN ('not_ready', 'promotion_ready')),
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(queue_id, candidate_index)
);

CREATE INDEX IF NOT EXISTS idx_extraction_candidates_queue
    ON extraction_candidates(queue_id, validation_status);

INSERT INTO schema_migrations(version) VALUES ('014_extraction_layer');
