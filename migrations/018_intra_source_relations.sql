ALTER TABLE extraction_queue
ADD COLUMN intra_source_relations_json TEXT NOT NULL DEFAULT '[]';

INSERT INTO schema_migrations(version) VALUES ('018_intra_source_relations');
