ALTER TABLE archive ADD COLUMN duplicate_state TEXT NOT NULL DEFAULT 'unique'
    CHECK (duplicate_state IN (
        'unique',
        'exact_duplicate',
        'canonical_duplicate',
        'possible_duplicate',
        'overlap_related'
    ));

ALTER TABLE archive ADD COLUMN canonical_archive_id TEXT
    REFERENCES archive(id) ON DELETE RESTRICT;

ALTER TABLE archive ADD COLUMN identity_fingerprint TEXT;
ALTER TABLE archive ADD COLUMN identity_fingerprint_version TEXT;
ALTER TABLE archive ADD COLUMN duplicate_confidence REAL;
ALTER TABLE archive ADD COLUMN overlap_summary_json TEXT NOT NULL DEFAULT '{}' CHECK (json_valid(overlap_summary_json));

CREATE UNIQUE INDEX IF NOT EXISTS uniq_archive_raw_identity
    ON archive(raw_sha256, byte_size)
    WHERE raw_sha256 IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_archive_identity_fp ON archive(identity_fingerprint);
CREATE INDEX IF NOT EXISTS idx_archive_dup_state ON archive(duplicate_state);

CREATE TABLE IF NOT EXISTS artifact_identity_buckets (
    archive_id TEXT NOT NULL REFERENCES archive(id) ON DELETE CASCADE,
    bucket_kind TEXT NOT NULL,
    bucket_value TEXT NOT NULL,
    PRIMARY KEY (archive_id, bucket_kind, bucket_value)
);

CREATE INDEX IF NOT EXISTS idx_identity_lookup
    ON artifact_identity_buckets(bucket_kind, bucket_value);

INSERT INTO schema_migrations(version) VALUES ('012_artifact_identity');
