CREATE TABLE IF NOT EXISTS raw_episodes (
    conversation_id       TEXT NOT NULL,
    episode_id            TEXT NOT NULL,
    bridge_id             TEXT NOT NULL,
    task_id               TEXT,
    status                TEXT NOT NULL DEFAULT 'active'
                          CHECK (status IN ('active', 'success', 'failure', 'partial', 'abandoned')),
    conversation_path     TEXT NOT NULL,
    episode_path          TEXT NOT NULL,
    events_path           TEXT NOT NULL,
    transcript_path       TEXT NOT NULL,
    artifacts_path        TEXT NOT NULL,
    event_count           INTEGER NOT NULL DEFAULT 0,
    artifact_count        INTEGER NOT NULL DEFAULT 0,
    content_hash          TEXT,
    projection_status     TEXT NOT NULL DEFAULT 'not_projected'
                          CHECK (projection_status IN ('not_projected', 'projected', 'skipped', 'error')),
    promoted_count        INTEGER NOT NULL DEFAULT 0,
    review_count          INTEGER NOT NULL DEFAULT 0,
    last_projection_error TEXT,
    started_at            TEXT,
    ended_at              TEXT,
    created_at            TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at            TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (conversation_id, episode_id)
);

CREATE INDEX IF NOT EXISTS idx_raw_episodes_status_updated
    ON raw_episodes(status, updated_at);

CREATE INDEX IF NOT EXISTS idx_raw_episodes_projection_status
    ON raw_episodes(projection_status, updated_at);

CREATE INDEX IF NOT EXISTS idx_raw_episodes_bridge
    ON raw_episodes(bridge_id, updated_at);

INSERT INTO schema_migrations(version) VALUES ('011_raw_episodes');
