-- The live DB had extraction_queue created by a since-deleted migration
-- (013_extraction_queue) with: session_id NOT NULL, no projection_metadata_json,
-- last_attempted_at/processed_at instead of started_at/finished_at.
-- This migration recreates extraction_queue with the current schema.
--
-- On fresh DBs (migration 014 already applied), extraction_queue has the new
-- schema with started_at/finished_at — only session_id nullable fix is needed.
-- We unify both paths by dropping and recreating the table.
-- started_at/finished_at are set to NULL (they are timing metadata, not content).

CREATE TABLE IF NOT EXISTS extraction_queue_new (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_kind TEXT NOT NULL CHECK (source_kind IN ('artifact', 'episode', 'session')),
    archive_id TEXT,
    conversation_id TEXT,
    episode_id TEXT,
    bridge_id TEXT NOT NULL DEFAULT 'job:backfill',
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

INSERT INTO extraction_queue_new
    (id, source_kind, archive_id, conversation_id, episode_id,
     bridge_id, session_id, task_id, duplicate_state, status,
     attempt_count, queued_at, last_error)
    SELECT
        id,
        source_kind,
        archive_id,
        conversation_id,
        episode_id,
        COALESCE(bridge_id, 'job:backfill'),
        session_id,
        task_id,
        duplicate_state,
        status,
        attempt_count,
        COALESCE(queued_at, datetime('now')),
        last_error
    FROM extraction_queue;

DROP TABLE extraction_queue;

ALTER TABLE extraction_queue_new RENAME TO extraction_queue;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_extraction_queue_archive_source
    ON extraction_queue(source_kind, archive_id)
    WHERE archive_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_extraction_queue_episode_source
    ON extraction_queue(source_kind, conversation_id, episode_id)
    WHERE conversation_id IS NOT NULL AND episode_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_extraction_queue_status_queued
    ON extraction_queue(status, queued_at, id);

INSERT INTO schema_migrations(version) VALUES ('016_extraction_queue_reshape');
