CREATE TABLE IF NOT EXISTS note_trust (
    note_id                 TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    usefulness_score        REAL NOT NULL DEFAULT 0.0,
    successful_top1_count   INTEGER NOT NULL DEFAULT 0,
    successful_top5_count   INTEGER NOT NULL DEFAULT 0,
    selected_count          INTEGER NOT NULL DEFAULT 0,
    useful_count            INTEGER NOT NULL DEFAULT 0,
    not_useful_count        INTEGER NOT NULL DEFAULT 0,
    failure_count           INTEGER NOT NULL DEFAULT 0,
    last_used_at            TEXT,
    updated_at              TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS note_freshness (
    note_id                  TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    staleness_score          REAL NOT NULL DEFAULT 0.0,
    freshness_state          TEXT NOT NULL DEFAULT 'fresh'
                             CHECK (freshness_state IN ('fresh', 'aging', 'suspect', 'stale', 'contested')),
    contradiction_count      INTEGER NOT NULL DEFAULT 0,
    linked_incident_count    INTEGER NOT NULL DEFAULT 0,
    failure_signal_count     INTEGER NOT NULL DEFAULT 0,
    miss_signal_count        INTEGER NOT NULL DEFAULT 0,
    superseded_flag          INTEGER NOT NULL DEFAULT 0 CHECK (superseded_flag IN (0, 1)),
    newer_evidence_count     INTEGER NOT NULL DEFAULT 0,
    relearn_stage            TEXT
                             CHECK (relearn_stage IN ('reverify', 'crosscheck', 'targeted_relearn_task', 'full_relearn_task')),
    last_computed_at         TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_note_freshness_state ON note_freshness(freshness_state, staleness_score);

CREATE TABLE IF NOT EXISTS rerank_weights (
    model_name    TEXT PRIMARY KEY,
    sample_count  INTEGER NOT NULL DEFAULT 0,
    weights_json  TEXT NOT NULL CHECK (json_valid(weights_json)),
    updated_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS relearn_tasks (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id             TEXT NOT NULL UNIQUE,
    note_id             TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    stage               TEXT NOT NULL
                         CHECK (stage IN ('reverify', 'crosscheck', 'targeted_relearn_task', 'full_relearn_task')),
    status              TEXT NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open', 'resolved', 'dismissed')),
    reason              TEXT NOT NULL,
    signals_json        TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(signals_json)),
    suggested_evidence  TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(suggested_evidence)),
    expected_output     TEXT NOT NULL,
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at          TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_relearn_tasks_stage_status ON relearn_tasks(stage, status);
CREATE INDEX IF NOT EXISTS idx_relearn_tasks_note_id ON relearn_tasks(note_id, status);

CREATE TABLE IF NOT EXISTS retrieval_hit_features (
    query_id           INTEGER NOT NULL REFERENCES retrieval_queries(id) ON DELETE CASCADE,
    note_id            TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    exact_hit          INTEGER NOT NULL DEFAULT 0 CHECK (exact_hit IN (0, 1)),
    bm25_rank          INTEGER,
    bm25_score         REAL,
    semantic_score     REAL,
    graph_score        REAL,
    freshness_score    REAL,
    type_prior_score   REAL,
    trust_score        REAL,
    PRIMARY KEY (query_id, note_id)
);

CREATE INDEX IF NOT EXISTS idx_retrieval_hit_features_query_id ON retrieval_hit_features(query_id);

INSERT INTO schema_migrations(version) VALUES ('004_phase4_trust_freshness');
