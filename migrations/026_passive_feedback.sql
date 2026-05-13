-- Migration 026: passive retrieval feedback infrastructure.
--
-- 1. Add retrieval_attribution_json to promotion_decisions.
--    Stores the raw retrieval_attribution array from the candidate payload.
--    Written by the wiki writer when the candidate carries attribution evidence.
--    Read by the maintenance derive_passive_feedback job.
--
-- 2. Create note_passive_feedback table.
--    Written exclusively by the maintenance layer (derive_passive_feedback).
--    Read by retrieval Stage 3 signal integration (NoteSignals.passive_feedback_state).
--    Two methods: extraction_inline (from promotion_decisions.retrieval_attribution_json)
--    and maintenance_post_hoc (from cross-episode retrieval_hits analysis).

ALTER TABLE promotion_decisions ADD COLUMN retrieval_attribution_json TEXT;

-- Add archive_id to retrieval_queries so maintenance can pair retrieval hits with
-- the ingestion session that triggered them (archive_id = session archive_id).
ALTER TABLE retrieval_queries ADD COLUMN archive_id TEXT;

-- Add a surrogate id to retrieval_hits for stable reference in note_passive_feedback.
-- SQLite cannot ALTER TABLE to add a PRIMARY KEY, so we add a plain INTEGER column.
ALTER TABLE retrieval_hits ADD COLUMN id INTEGER;

CREATE TABLE IF NOT EXISTS note_passive_feedback (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id TEXT NOT NULL,
    retrieval_hit_id INTEGER,
    session_archive_id TEXT,
    feedback_state TEXT NOT NULL
        CHECK (feedback_state IN (
            'passive_used_likely',
            'passive_useful_likely',
            'passive_misleading_likely'
        )),
    confidence REAL NOT NULL DEFAULT 0.0,
    method TEXT NOT NULL
        CHECK (method IN ('extraction_inline', 'maintenance_post_hoc')),
    evidence_type TEXT,
    derived_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%SZ', 'now')),
    UNIQUE(note_id, session_archive_id, feedback_state, method)
);

CREATE INDEX IF NOT EXISTS idx_note_passive_feedback_note
    ON note_passive_feedback(note_id, derived_at DESC);

CREATE INDEX IF NOT EXISTS idx_note_passive_feedback_session
    ON note_passive_feedback(session_archive_id, derived_at DESC);

INSERT INTO schema_migrations(version) VALUES ('026_passive_feedback');
