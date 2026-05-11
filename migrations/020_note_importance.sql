CREATE TABLE IF NOT EXISTS note_importance (
    note_id            TEXT PRIMARY KEY,
    importance_score   REAL NOT NULL DEFAULT 0.0,
    in_degree          INT  NOT NULL DEFAULT 0,
    out_degree         INT  NOT NULL DEFAULT 0,
    connectivity_score REAL NOT NULL DEFAULT 0.0,
    trust_score        REAL NOT NULL DEFAULT 0.0,
    freshness_score    REAL NOT NULL DEFAULT 0.0,
    lifecycle_weight   REAL NOT NULL DEFAULT 0.5,
    updated_at         TEXT
);

ALTER TABLE retrieval_hit_features ADD COLUMN importance_score REAL;

INSERT INTO schema_migrations(version) VALUES ('020_note_importance');
