ALTER TABLE notes ADD COLUMN overlay_class TEXT
    CHECK (overlay_class IN ('fact', 'episode'));

ALTER TABLE notes ADD COLUMN lifecycle_state TEXT
    CHECK (lifecycle_state IN ('draft', 'review', 'verified', 'canonical', 'contested', 'archived'));

CREATE INDEX IF NOT EXISTS idx_notes_lifecycle_state ON notes(lifecycle_state, type);
CREATE INDEX IF NOT EXISTS idx_notes_overlay_class ON notes(overlay_class, type);

CREATE TABLE IF NOT EXISTS task_outcomes (
    task_id                 TEXT PRIMARY KEY,
    session_id              TEXT NOT NULL,
    bridge_id               TEXT NOT NULL,
    query_id                INTEGER REFERENCES retrieval_queries(id) ON DELETE SET NULL,
    task_kind               TEXT,
    outcome_type            TEXT NOT NULL,
    status                  TEXT NOT NULL,
    summary                 TEXT NOT NULL,
    fresh_research_needed   INTEGER NOT NULL DEFAULT 0 CHECK (fresh_research_needed IN (0, 1)),
    fresh_sources_checked   TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(fresh_sources_checked)),
    useful_ids              TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(useful_ids)),
    misleading_ids          TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(misleading_ids)),
    retrieval_state         TEXT,
    recommendation          TEXT,
    candidate_note_ids      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(candidate_note_ids)),
    created_at              TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_task_outcomes_session_id ON task_outcomes(session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_outcomes_query_id ON task_outcomes(query_id);

INSERT INTO schema_migrations(version) VALUES ('005_phase6_runtime_and_lifecycle');
