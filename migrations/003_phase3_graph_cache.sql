CREATE TABLE IF NOT EXISTS graph_nodes (
    id         TEXT PRIMARY KEY,
    node_type  TEXT NOT NULL CHECK (node_type IN ('note', 'entity')),
    kind       TEXT NOT NULL,
    label      TEXT NOT NULL,
    note_id    TEXT,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_graph_nodes_note_id ON graph_nodes(note_id);
CREATE INDEX IF NOT EXISTS idx_graph_nodes_kind ON graph_nodes(kind);

CREATE TABLE IF NOT EXISTS graph_edges (
    source_note_id TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    target_id      TEXT NOT NULL,
    target_kind    TEXT NOT NULL,
    relation_type  TEXT NOT NULL,
    weight         REAL NOT NULL DEFAULT 0.5,
    updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (source_note_id, target_id, relation_type)
);

CREATE INDEX IF NOT EXISTS idx_graph_edges_source ON graph_edges(source_note_id, relation_type);
CREATE INDEX IF NOT EXISTS idx_graph_edges_target ON graph_edges(target_id, relation_type);

CREATE TABLE IF NOT EXISTS retrieval_hits_phase3 (
    query_id    INTEGER NOT NULL REFERENCES retrieval_queries(id) ON DELETE CASCADE,
    note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    rank        INTEGER NOT NULL,
    channel     TEXT NOT NULL CHECK (channel IN ('exact', 'bm25', 'semantic', 'graph', 'archive', 'rerank')),
    raw_score   REAL,
    final_score REAL,
    selected    INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1)),
    useful      INTEGER CHECK (useful IN (0, 1)),
    PRIMARY KEY (query_id, note_id)
);

INSERT INTO retrieval_hits_phase3(query_id, note_id, rank, channel, raw_score, final_score, selected, useful)
SELECT query_id, note_id, rank, channel, raw_score, final_score, selected, useful
FROM retrieval_hits;

DROP TABLE retrieval_hits;
ALTER TABLE retrieval_hits_phase3 RENAME TO retrieval_hits;

CREATE INDEX IF NOT EXISTS idx_retrieval_hits_query_id ON retrieval_hits(query_id, rank);
CREATE INDEX IF NOT EXISTS idx_retrieval_hits_note_id ON retrieval_hits(note_id);

INSERT INTO schema_migrations(version) VALUES ('003_phase3_graph_cache');
