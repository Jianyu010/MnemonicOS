-- Reshape extraction_candidates from the original schema (candidate_payload_json,
-- no candidate_index, no title, vlm_model/llm_model instead of model_name) to the
-- schema defined in 014_extraction_layer.sql.
--
-- On fresh DBs (migration 014 already created the new schema), this migration drops
-- the correctly-structured table and recreates it empty — a safe no-op in terms of
-- data because extraction_candidates are transient artifacts, not canonical state.
--
-- On live DBs that ran migration 014 with the old schema, this migration drops the
-- incompatible table and creates the new one, discarding unreachable old rows.

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
        CHECK (promotion_state IN ('not_ready', 'promotion_ready')),
    model_provider TEXT NOT NULL,
    model_name TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(queue_id, candidate_index)
);

CREATE INDEX IF NOT EXISTS idx_extraction_candidates_queue
    ON extraction_candidates(queue_id, validation_status);

INSERT INTO schema_migrations(version) VALUES ('014b_extraction_candidates_reshape');
