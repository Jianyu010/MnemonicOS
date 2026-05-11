CREATE TABLE IF NOT EXISTS archetypes (
    archetype_id   TEXT PRIMARY KEY,
    label          TEXT NOT NULL DEFAULT '',
    layer_a_description TEXT NOT NULL DEFAULT '',
    layer_b_role_schema TEXT NOT NULL DEFAULT '{}',
    occurrence_count INT NOT NULL DEFAULT 1,
    score          REAL NOT NULL DEFAULT 0.5,
    relation_type  TEXT NOT NULL DEFAULT 'similar_to',
    source_channel INT NOT NULL DEFAULT 2,
    created_at     TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at     TEXT NOT NULL DEFAULT (datetime('now'))
);

ALTER TABLE graph_edges ADD COLUMN archetype_id TEXT;
ALTER TABLE graph_edges ADD COLUMN archetype_score REAL;
ALTER TABLE graph_edges ADD COLUMN instance_confidence REAL;

CREATE INDEX IF NOT EXISTS idx_graph_edges_archetype ON graph_edges(archetype_id)
    WHERE archetype_id IS NOT NULL;

INSERT INTO schema_migrations(version) VALUES ('025_archetype_storage');
