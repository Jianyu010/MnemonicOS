ALTER TABLE extraction_queue ADD COLUMN extraction_schema_version TEXT;
ALTER TABLE extraction_queue ADD COLUMN extraction_model_version TEXT;

INSERT INTO schema_migrations(version) VALUES ('023_extraction_versioning');
