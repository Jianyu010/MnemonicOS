-- Expand extraction_candidates.promotion_state to include post-promotion states.
-- SQLite does not support ALTER TABLE to modify CHECK constraints, so we
-- recreate the table with the expanded constraint.
--
-- Migration 014b_extraction_candidates_reshape runs before this migration and
-- guarantees extraction_candidates has the new schema and is empty, so no data
-- migration is needed here.

DROP TABLE IF EXISTS extraction_candidates;

CREATE TABLE extraction_candidates (
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
        CHECK (promotion_state IN ('not_ready', 'promotion_ready', 'promoted', 'review_routed', 'rejected')),
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(queue_id, candidate_index)
);

CREATE INDEX IF NOT EXISTS idx_extraction_candidates_queue
    ON extraction_candidates(queue_id, validation_status);

CREATE INDEX IF NOT EXISTS idx_extraction_candidates_promotion
    ON extraction_candidates(promotion_state, created_at);

-- Ledger table: one row per promotion attempt, idempotent via unique index.
CREATE TABLE IF NOT EXISTS promotion_decisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id INTEGER NOT NULL REFERENCES extraction_candidates(id),
    decision TEXT NOT NULL CHECK (decision IN ('new', 'confirm', 'update', 'review', 'rejected')),
    target_note_id TEXT,
    archive_id TEXT,
    check_errors_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(check_errors_json)),
    promotion_state TEXT NOT NULL CHECK (promotion_state IN ('promoted', 'review_routed', 'rejected')),
    bridge_id TEXT NOT NULL DEFAULT 'job:process-promotion-queue',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS promotion_decisions_candidate_id
    ON promotion_decisions(candidate_id);

INSERT INTO schema_migrations(version) VALUES ('015_promotion_decisions');
