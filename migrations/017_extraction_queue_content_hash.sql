-- Add content_hash to extraction_queue for content-change-aware re-extraction.
-- When the same (source_kind, archive_id) or (source_kind, conversation_id, episode_id)
-- is re-enqueued with a different hash, the ON CONFLICT logic resets status to 'pending'.
ALTER TABLE extraction_queue ADD COLUMN content_hash TEXT;
