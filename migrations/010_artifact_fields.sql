ALTER TABLE archive ADD COLUMN artifact_path TEXT;
ALTER TABLE archive ADD COLUMN source_format TEXT;
ALTER TABLE archive ADD COLUMN original_filename TEXT;
ALTER TABLE archive ADD COLUMN raw_sha256 TEXT;
ALTER TABLE archive ADD COLUMN byte_size INTEGER;
ALTER TABLE archive ADD COLUMN connector_name TEXT;
ALTER TABLE archive ADD COLUMN connector_version TEXT;
ALTER TABLE archive ADD COLUMN ingest_status TEXT;
ALTER TABLE archive ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN slide_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN figure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN table_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(warnings_json));
ALTER TABLE archive ADD COLUMN failure_reason TEXT;
ALTER TABLE archive ADD COLUMN needs_visual_pass INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN needs_manual_projection INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN image_only_pdf INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS artifact_figures (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id          TEXT NOT NULL REFERENCES archive(id) ON DELETE CASCADE,
    figure_id           TEXT NOT NULL,
    storage_policy      TEXT NOT NULL DEFAULT 'in_memory_extracted'
                        CHECK (storage_policy IN ('in_memory_extracted', 'source_attachment', 'promoted_later')),
    attachment_path     TEXT,
    source_page         INTEGER,
    source_slide        INTEGER,
    source_index        INTEGER,
    width_px            INTEGER,
    height_px           INTEGER,
    mime_type           TEXT,
    byte_size           INTEGER,
    sha256              TEXT,
    structural_location TEXT,
    has_caption         INTEGER NOT NULL DEFAULT 0 CHECK (has_caption IN (0, 1)),
    referenced_by_label INTEGER NOT NULL DEFAULT 0 CHECK (referenced_by_label IN (0, 1)),
    needs_visual_pass   INTEGER NOT NULL DEFAULT 1 CHECK (needs_visual_pass IN (0, 1)),
    warnings_json       TEXT NOT NULL DEFAULT '[]' CHECK (json_valid(warnings_json)),
    UNIQUE(archive_id, figure_id)
);

CREATE INDEX IF NOT EXISTS idx_artifact_figures_archive_id ON artifact_figures(archive_id);
CREATE INDEX IF NOT EXISTS idx_artifact_figures_needs_visual_pass ON artifact_figures(needs_visual_pass);

INSERT INTO schema_migrations(version) VALUES ('010_artifact_fields');
