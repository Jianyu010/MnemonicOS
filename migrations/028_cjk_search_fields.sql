DROP TRIGGER IF EXISTS notes_search_ai;
DROP TRIGGER IF EXISTS notes_search_ad;
DROP TRIGGER IF EXISTS notes_search_au;
DROP TRIGGER IF EXISTS archive_chunks_ai;
DROP TRIGGER IF EXISTS archive_chunks_ad;
DROP TRIGGER IF EXISTS archive_chunks_au;

DROP TABLE IF EXISTS notes_fts;
DROP TABLE IF EXISTS chunks_fts;

ALTER TABLE notes_search ADD COLUMN search_title TEXT NOT NULL DEFAULT '';
ALTER TABLE notes_search ADD COLUMN search_aliases_flat TEXT NOT NULL DEFAULT '';
ALTER TABLE notes_search ADD COLUMN search_summary TEXT NOT NULL DEFAULT '';
ALTER TABLE notes_search ADD COLUMN search_body TEXT NOT NULL DEFAULT '';

ALTER TABLE archive_chunks ADD COLUMN search_body TEXT NOT NULL DEFAULT '';
ALTER TABLE archive_chunks ADD COLUMN search_summary TEXT NOT NULL DEFAULT '';

UPDATE notes_search
SET search_title = CASE WHEN search_title = '' THEN title ELSE search_title END,
    search_aliases_flat = CASE WHEN search_aliases_flat = '' THEN aliases_flat ELSE search_aliases_flat END,
    search_summary = CASE WHEN search_summary = '' THEN summary ELSE search_summary END,
    search_body = CASE WHEN search_body = '' THEN body ELSE search_body END;

UPDATE archive_chunks
SET search_body = CASE WHEN search_body = '' THEN body ELSE search_body END,
    search_summary = CASE WHEN search_summary = '' THEN summary ELSE search_summary END;

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
    VALUES (
        new.rowid,
        COALESCE(NULLIF(new.search_title, ''), new.title),
        COALESCE(NULLIF(new.search_aliases_flat, ''), new.aliases_flat),
        COALESCE(NULLIF(new.search_summary, ''), new.summary),
        COALESCE(NULLIF(new.search_body, ''), new.body)
    );
END;

CREATE TRIGGER notes_search_ad AFTER DELETE ON notes_search BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, aliases_flat, summary, body)
    VALUES (
        'delete',
        old.rowid,
        COALESCE(NULLIF(old.search_title, ''), old.title),
        COALESCE(NULLIF(old.search_aliases_flat, ''), old.aliases_flat),
        COALESCE(NULLIF(old.search_summary, ''), old.summary),
        COALESCE(NULLIF(old.search_body, ''), old.body)
    );
END;

CREATE TRIGGER notes_search_au AFTER UPDATE ON notes_search BEGIN
    INSERT INTO notes_fts(notes_fts, rowid, title, aliases_flat, summary, body)
    VALUES (
        'delete',
        old.rowid,
        COALESCE(NULLIF(old.search_title, ''), old.title),
        COALESCE(NULLIF(old.search_aliases_flat, ''), old.aliases_flat),
        COALESCE(NULLIF(old.search_summary, ''), old.summary),
        COALESCE(NULLIF(old.search_body, ''), old.body)
    );
    INSERT INTO notes_fts(rowid, title, aliases_flat, summary, body)
    VALUES (
        new.rowid,
        COALESCE(NULLIF(new.search_title, ''), new.title),
        COALESCE(NULLIF(new.search_aliases_flat, ''), new.aliases_flat),
        COALESCE(NULLIF(new.search_summary, ''), new.summary),
        COALESCE(NULLIF(new.search_body, ''), new.body)
    );
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
    VALUES (
        new.id,
        COALESCE(NULLIF(new.search_body, ''), new.body),
        COALESCE(NULLIF(new.search_summary, ''), new.summary)
    );
END;

CREATE TRIGGER archive_chunks_ad AFTER DELETE ON archive_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, body, summary)
    VALUES (
        'delete',
        old.id,
        COALESCE(NULLIF(old.search_body, ''), old.body),
        COALESCE(NULLIF(old.search_summary, ''), old.summary)
    );
END;

CREATE TRIGGER archive_chunks_au AFTER UPDATE ON archive_chunks BEGIN
    INSERT INTO chunks_fts(chunks_fts, rowid, body, summary)
    VALUES (
        'delete',
        old.id,
        COALESCE(NULLIF(old.search_body, ''), old.body),
        COALESCE(NULLIF(old.search_summary, ''), old.summary)
    );
    INSERT INTO chunks_fts(rowid, body, summary)
    VALUES (
        new.id,
        COALESCE(NULLIF(new.search_body, ''), new.body),
        COALESCE(NULLIF(new.search_summary, ''), new.summary)
    );
END;

INSERT INTO notes_fts(rowid, title, aliases_flat, summary, body)
SELECT rowid, search_title, search_aliases_flat, search_summary, search_body
FROM notes_search;

INSERT INTO chunks_fts(rowid, body, summary)
SELECT id, search_body, search_summary
FROM archive_chunks;

INSERT INTO schema_migrations(version) VALUES ('028_cjk_search_fields');
