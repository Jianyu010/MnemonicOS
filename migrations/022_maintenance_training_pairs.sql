CREATE TABLE IF NOT EXISTS maintenance_training_pairs (
    id INTEGER PRIMARY KEY,
    note_a_id TEXT NOT NULL,
    note_b_id TEXT NOT NULL,
    label INT NOT NULL,
    source TEXT NOT NULL DEFAULT 'cluster_validation',
    created_at TEXT NOT NULL
);

INSERT INTO schema_migrations(version) VALUES ('022_maintenance_training_pairs');
