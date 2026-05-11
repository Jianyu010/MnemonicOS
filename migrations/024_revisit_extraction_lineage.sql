ALTER TABLE extraction_queue ADD COLUMN revisit_of_queue_id INTEGER REFERENCES extraction_queue(id);
ALTER TABLE extraction_queue ADD COLUMN revisit_reason TEXT;
ALTER TABLE extraction_queue ADD COLUMN revisit_context_json TEXT;

DROP INDEX IF EXISTS uniq_extraction_queue_archive_source;
DROP INDEX IF EXISTS uniq_extraction_queue_episode_source;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_extraction_queue_archive_source
    ON extraction_queue(source_kind, archive_id)
    WHERE archive_id IS NOT NULL AND revisit_of_queue_id IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uniq_extraction_queue_episode_source
    ON extraction_queue(source_kind, conversation_id, episode_id)
    WHERE conversation_id IS NOT NULL AND episode_id IS NOT NULL AND revisit_of_queue_id IS NULL;

CREATE INDEX IF NOT EXISTS idx_extraction_queue_revisit_source
    ON extraction_queue(revisit_of_queue_id);

INSERT INTO schema_migrations(version) VALUES ('024_revisit_extraction_lineage');
