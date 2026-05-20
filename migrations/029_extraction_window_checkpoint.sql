ALTER TABLE extraction_queue ADD COLUMN completed_window_count INTEGER NOT NULL DEFAULT 0;

INSERT INTO schema_migrations(version) VALUES ('029_extraction_window_checkpoint');
