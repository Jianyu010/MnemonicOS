ALTER TABLE task_outcomes ADD COLUMN chain_id TEXT;

ALTER TABLE task_outcomes ADD COLUMN problem_class TEXT;

ALTER TABLE task_outcomes ADD COLUMN chain_extraction TEXT
    CHECK (chain_extraction IN ('explicit_chain', 'inferred_chain', 'no_chain'));

CREATE INDEX IF NOT EXISTS idx_task_outcomes_chain_id ON task_outcomes(chain_id, created_at);
CREATE INDEX IF NOT EXISTS idx_task_outcomes_problem_class ON task_outcomes(problem_class, created_at);

INSERT INTO schema_migrations(version) VALUES ('007_phase8_task_outcome_chains');
