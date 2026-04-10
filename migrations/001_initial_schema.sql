PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE notes (
    id               TEXT PRIMARY KEY,
    type             TEXT NOT NULL CHECK (
        type IN (
            'person', 'project', 'repo', 'decision', 'procedure',
            'concept', 'incident', 'source', 'journal', 'overview'
        )
    ),
    title            TEXT NOT NULL,
    status           TEXT,
    confidence       TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    tags             TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
    entities         TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(entities)),
    source_refs      TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(source_refs)),
    valid_from       TEXT,
    valid_to         TEXT,
    updated_at       TEXT NOT NULL,
    created_at       TEXT NOT NULL,
    last_verified_at TEXT,
    verified_by      TEXT,
    last_observed_at TEXT,
    body_path        TEXT NOT NULL UNIQUE,
    summary          TEXT NOT NULL DEFAULT '',
    content_hash     TEXT NOT NULL,
    revision         INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX idx_notes_type_status ON notes(type, status);
CREATE INDEX idx_notes_validity ON notes(valid_from, valid_to);
CREATE INDEX idx_notes_updated_at ON notes(updated_at);
CREATE INDEX idx_notes_last_verified_at ON notes(last_verified_at);

CREATE TABLE aliases (
    alias      TEXT NOT NULL COLLATE NOCASE,
    note_id    TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    PRIMARY KEY (alias, note_id)
);

CREATE INDEX idx_aliases_alias ON aliases(alias);
CREATE INDEX idx_aliases_note_id ON aliases(note_id);

CREATE TABLE claims (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    note_id          TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    claim            TEXT NOT NULL,
    attribute        TEXT,
    value            TEXT,
    valid_from       TEXT,
    valid_to         TEXT,
    source_ref       TEXT,
    confidence       TEXT CHECK (confidence IN ('high', 'medium', 'low')),
    last_observed_at TEXT,
    created_at       TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_claims_note_id ON claims(note_id);
CREATE INDEX idx_claims_attribute ON claims(attribute);

CREATE TABLE archive (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL CHECK (type IN ('session', 'doc', 'web_clip')),
    path        TEXT NOT NULL UNIQUE,
    ingested_at TEXT NOT NULL,
    agent       TEXT,
    tags        TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(tags)),
    chunk_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE archive_chunks (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id   TEXT NOT NULL REFERENCES archive(id) ON DELETE CASCADE,
    chunk_index  INTEGER NOT NULL,
    body         TEXT NOT NULL,
    summary      TEXT NOT NULL DEFAULT '',
    token_count  INTEGER,
    content_hash TEXT NOT NULL,
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (archive_id, chunk_index)
);

CREATE INDEX idx_archive_chunks_archive_id ON archive_chunks(archive_id, chunk_index);

CREATE TABLE notes_search (
    note_id      TEXT PRIMARY KEY REFERENCES notes(id) ON DELETE CASCADE,
    title        TEXT NOT NULL,
    aliases_flat TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    body         TEXT NOT NULL DEFAULT ''
);

CREATE TABLE sync_state (
    path           TEXT PRIMARY KEY,
    kind           TEXT NOT NULL CHECK (kind IN ('note', 'archive', 'system')),
    note_id        TEXT,
    content_hash   TEXT NOT NULL,
    last_synced_at TEXT NOT NULL,
    parse_status   TEXT NOT NULL DEFAULT 'ok' CHECK (parse_status IN ('ok', 'error', 'skipped')),
    last_error     TEXT
);

CREATE TABLE retrieval_queries (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    query                 TEXT NOT NULL,
    query_type            TEXT,
    classifier_confidence REAL,
    retrieved             TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(retrieved)),
    top_k                 INTEGER NOT NULL DEFAULT 5,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    useful                INTEGER CHECK (useful IN (0, 1)),
    top1_correct          INTEGER CHECK (top1_correct IN (0, 1))
);

CREATE TABLE retrieval_hits (
    query_id    INTEGER NOT NULL REFERENCES retrieval_queries(id) ON DELETE CASCADE,
    note_id     TEXT NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    rank        INTEGER NOT NULL,
    channel     TEXT NOT NULL CHECK (channel IN ('exact', 'bm25', 'semantic', 'archive', 'rerank')),
    raw_score   REAL,
    final_score REAL,
    selected    INTEGER NOT NULL DEFAULT 0 CHECK (selected IN (0, 1)),
    useful      INTEGER CHECK (useful IN (0, 1)),
    PRIMARY KEY (query_id, note_id)
);

CREATE INDEX idx_retrieval_hits_query_id ON retrieval_hits(query_id, rank);
CREATE INDEX idx_retrieval_hits_note_id ON retrieval_hits(note_id);

CREATE TABLE job_runs (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    job_name   TEXT NOT NULL,
    mode       TEXT,
    status     TEXT NOT NULL CHECK (status IN ('started', 'ok', 'error')),
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    finished_at TEXT,
    stats_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(stats_json)),
    error_text TEXT
);

CREATE VIRTUAL TABLE notes_fts USING fts5(
    title,
    aliases_flat,
    summary,
    body,
    content='notes_search',
    content_rowid='rowid',
    tokenize='porter unicode61 remove_diacritics 1'
);

CREATE TRIGGER notes_search_ai AFTER INSERT ON notes_search BEGIN
    INSERT INTO notes_fts(rowid, title, aliases_flat, summary, body)
    VALUES (new.rowid, new.title, new.aliases_flat, new.summary, new.body);
END;

CREATE TRIGGER notes_search_ad AFTER DELETE ON notes_search BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, aliases_flat, summary, body)
    VALUES ('delete', old.rowid, old.title, old.aliases_flat, old.summary, old.body);
END;

CREATE TRIGGER notes_search_au AFTER UPDATE ON notes_search BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, aliases_flat, summary, body)
    VALUES ('delete', old.rowid, old.title, old.aliases_flat, old.summary, old.body);
    INSERT INTO notes_fts(rowid, title, aliases_flat, summary, body)
    VALUES (new.rowid, new.title, new.aliases_flat, new.summary, new.body);
END;

CREATE VIRTUAL TABLE chunks_fts USING fts5(
    body,
    summary,
    content='archive_chunks',
    content_rowid='id',
    tokenize='porter unicode61 remove_diacritics 1'
);

CREATE TRIGGER archive_chunks_ai AFTER INSERT ON archive_chunks BEGIN
    INSERT INTO chunks_fts(rowid, body, summary)
    VALUES (new.id, new.body, new.summary);
END;

CREATE TRIGGER archive_chunks_ad AFTER DELETE ON archive_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, body, summary)
    VALUES ('delete', old.id, old.body, old.summary);
END;

CREATE TRIGGER archive_chunks_au AFTER UPDATE ON archive_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, body, summary)
    VALUES ('delete', old.id, old.body, old.summary);
    INSERT INTO chunks_fts(rowid, body, summary)
    VALUES (new.id, new.body, new.summary);
END;

INSERT INTO schema_migrations(version) VALUES ('001_initial_schema');
