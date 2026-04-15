ALTER TABLE notes ADD COLUMN chain_id TEXT;

ALTER TABLE notes ADD COLUMN chain_confidence TEXT
    CHECK (chain_confidence IN ('high', 'medium', 'low'));

ALTER TABLE notes ADD COLUMN chain_question TEXT;

ALTER TABLE notes ADD COLUMN chain_summary TEXT;

ALTER TABLE notes ADD COLUMN problem_class TEXT;

ALTER TABLE notes ADD COLUMN failure_summary TEXT;

ALTER TABLE notes ADD COLUMN attempt_summaries TEXT NOT NULL DEFAULT '[]'
    CHECK (json_valid(attempt_summaries));

ALTER TABLE notes ADD COLUMN solution_rationale TEXT;

CREATE INDEX IF NOT EXISTS idx_notes_chain_id ON notes(chain_id, type);
CREATE INDEX IF NOT EXISTS idx_notes_problem_class ON notes(problem_class, type);

INSERT INTO schema_migrations(version) VALUES ('006_phase7_episode_metadata');
