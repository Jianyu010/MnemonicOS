CREATE TABLE IF NOT EXISTS note_embedding_scans (
    note_id TEXT PRIMARY KEY,
    last_scanned_at TEXT NOT NULL,
    neighbor_count INT NOT NULL DEFAULT 0,
    scan_threshold REAL NOT NULL DEFAULT 0.78
);

INSERT INTO schema_migrations(version) VALUES ('021_note_embedding_scans');
