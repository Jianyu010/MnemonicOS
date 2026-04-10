CREATE TABLE IF NOT EXISTS note_vectors (
    note_id      TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    model        TEXT NOT NULL,
    dimensions   INTEGER NOT NULL,
    vector_json  TEXT NOT NULL CHECK (json_valid(vector_json)),
    source_hash  TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_note_vectors_model ON note_vectors(model);

CREATE TABLE IF NOT EXISTS archive_chunk_vectors (
    chunk_id     INTEGER PRIMARY KEY REFERENCES archive_chunks(id) ON DELETE CASCADE,
    model        TEXT NOT NULL,
    dimensions   INTEGER NOT NULL,
    vector_json  TEXT NOT NULL CHECK (json_valid(vector_json)),
    source_hash  TEXT NOT NULL,
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_archive_chunk_vectors_model ON archive_chunk_vectors(model);

CREATE TABLE IF NOT EXISTS retrieval_archive_hits (
    query_id     INTEGER NOT NULL REFERENCES retrieval_queries(id) ON DELETE CASCADE,
    chunk_id     INTEGER NOT NULL REFERENCES archive_chunks(id) ON DELETE CASCADE,
    rank         INTEGER NOT NULL,
    channel      TEXT NOT NULL CHECK (channel IN ('archive_bm25', 'archive_semantic')),
    raw_score    REAL,
    final_score  REAL,
    PRIMARY KEY (query_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_archive_hits_query_id
    ON retrieval_archive_hits(query_id, rank);

INSERT INTO schema_migrations(version) VALUES ('002_phase2_semantic_and_ingest');
