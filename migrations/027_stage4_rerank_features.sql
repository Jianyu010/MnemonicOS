ALTER TABLE retrieval_hit_features ADD COLUMN quality_pass INTEGER NOT NULL DEFAULT 0 CHECK (quality_pass IN (0, 1));
ALTER TABLE retrieval_hit_features ADD COLUMN quality_score REAL NOT NULL DEFAULT 0.0;
ALTER TABLE retrieval_hit_features ADD COLUMN passive_feedback_state TEXT NOT NULL DEFAULT 'none';
ALTER TABLE retrieval_hit_features ADD COLUMN passive_feedback_confidence REAL NOT NULL DEFAULT 0.0;

INSERT INTO schema_migrations(version) VALUES ('027_stage4_rerank_features');
