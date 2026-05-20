-- Add structured audit column for rejected candidates.
-- Enables: SELECT rejection_reason, COUNT(*) FROM extraction_candidates
--           WHERE promotion_state = 'rejected' GROUP BY rejection_reason;
ALTER TABLE extraction_candidates ADD COLUMN rejection_reason TEXT;
