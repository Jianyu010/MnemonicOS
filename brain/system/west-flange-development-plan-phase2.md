# West-Flange Phase 2: Identity and Redundancy

## Goal

Detect duplicate, canonical-equivalent, possible-duplicate, and overlap-related
raw artifacts efficiently, without turning redundancy detection into semantic
interpretation.

## Definitions

- **Exact duplicate**: same `raw_sha256` and `byte_size` for the main submitted
  artifact bytes.
- **Canonical duplicate**: byte-different source that represents the same paper,
  book, document, or other source with high normalized structural/text overlap.
- **Possible duplicate**: near match that needs review or downstream judgment.
- **Overlap-related**: sources share a large prefix/suffix but have meaningful
  unique delta content, such as a longer chat history.
- **Processing dedup**: skip repeated processing work; do not decide whether
  repeated information is important.

## Duplicate State

`duplicate_state` values:

- `unique`
- `exact_duplicate`
- `canonical_duplicate`
- `possible_duplicate`
- `overlap_related`

Rule: only `exact_duplicate` short-circuits connector/projection work. All other
states label the artifact and allow projection to continue.

## Stage 0: Pre-Parse Exact Identity

Runs after true raw copy and hash, before connector parsing.

Algorithm:

1. Compute `raw_sha256` and `byte_size` for the main artifact only.
2. Look up `(raw_sha256, byte_size)` in `archive`.
3. On miss, continue to connector parsing.
4. On hit:
   - create a duplicate submission row
   - set `duplicate_state = 'exact_duplicate'`
   - set `canonical_archive_id` to the existing canonical row
   - skip connector parsing, projection, VLM work, and LLM work
   - return a thin duplicate lease when projection is requested

Sidecar attachments inherit the parent artifact's duplicate state in Stage 0.
They are not independently deduplicated across parent artifacts in v1. If the
same sidecar is later submitted as a standalone artifact, it gets its own Stage 0
check.

## Exact-Duplicate Storage Policy

v1 uses **submission-row-only** storage for exact duplicates:

- Do not keep a second `original.{ext}` file for exact duplicate bytes.
- If a temporary duplicate copy was created before Stage 0 resolves, delete it.
- The duplicate archive row references canonical bytes through
  `canonical_archive_id`.
- Canonical rows cannot be deleted while duplicate rows reference them. Enforce
  with `ON DELETE RESTRICT` or an equivalent app-level guard.

The duplicate submission still exists as provenance. It just does not create
duplicate bytes or duplicate processing work.

## Stage 4: Post-Parse Identity

Runs after connector parsing, within-source dedup, and fingerprint generation.

Algorithm:

1. Mint `identity_fingerprint` and `identity_fingerprint_version`.
2. Look up exact canonical fingerprint matches.
3. Query LSH buckets from `artifact_identity_buckets`.
4. Run targeted verification only on returned candidates.
5. Set duplicate metadata:
   - `canonical_duplicate` for high-confidence canonical equivalence
   - `possible_duplicate` for near match below canonical threshold
   - `overlap_related` for meaningful prefix/suffix overlap with unique delta
   - `unique` otherwise
6. Write LSH buckets only for `unique`, `possible_duplicate`, and
   `overlap_related` rows. Do not write buckets for `exact_duplicate` or
   `canonical_duplicate`.

Stage 4 never short-circuits. Projection continues for canonical, possible, and
overlap-related artifacts.

## Fingerprint Families

`identity_fingerprint_version` names the fingerprint family and version. Raw
bytes never change, but fingerprint algorithms can.

### `text_norm_v1`

Inputs:

- `source_format`
- normalized title from metadata or first heading-like line
- normalized sorted author list when available
- first 1000 normalized body characters

Normalization: lowercase, strip punctuation, collapse whitespace.

Hash: `SHA-256(f"{format}|{title}|{authors}|{first_1000}")`.

Used by PDF, DOCX, and HTML when title/author or heading-like text is available.

### `pdf_struct_v1`

Inputs:

- page count
- image count
- table count
- first-page first 1000 normalized characters

Hash: SHA-256 of the joined tuple.

Used by PDFs when title/author metadata is unavailable.

Version bump rule: adding/removing inputs creates a new fingerprint family
version, such as `text_norm_v2`. Different versions are not comparable.

## LSH Parameters

v1 uses SimHash only. MinHash is deferred.

- Bit width: 64
- Bands: 4 bands x 16 bits
- Bucket rows:
  - `bucket_kind = 'simhash_band_0'` through `simhash_band_3`
  - `bucket_value = '<16-bit hex>'`
- Shingles: 5-token shingles over normalized text
- Same-cluster Hamming threshold: `<= 3` over 64 bits

## Verification Thresholds

After LSH candidate selection, verify with normalized-text Jaccard or
longest-common-substring ratio over the same shingle space.

| Overlap ratio | State |
|---|---|
| `>= 0.95` | `canonical_duplicate` |
| `0.60 <= ratio < 0.95` | `possible_duplicate` |
| `< 0.60` | `unique` unless prefix/suffix scan says `overlap_related` |

`possible_duplicate` rows point `canonical_archive_id` at the closest cluster
representative and remain eligible for projection.

## Overlap-Related Sources

Chat histories and captured episodes are not canonical duplicates merely because
one includes a large prefix of another.

Use prefix/suffix scan to detect overlap. If the new source has meaningful unique
delta content, set `duplicate_state = 'overlap_related'` and preserve projection
eligibility.

For captured raw episodes, overlap/dedup counters are returned as
`ProjectionLease.projection_metadata` and warnings. They are not stored in
`archive.overlap_summary_json`, because raw episodes live in `raw_episodes`.

## Within-Source Dedup

Within-source dedup repairs mechanically duplicated projection shape. It does
not remove topically similar content.

Captured-session algorithm:

1. Walk events in chronological order.
2. Tokenize event payloads, ignoring timestamp/id noise.
3. Compute longest common prefix between the new event and prior retained stream.
4. Drop fully contained snapshot duplicates.
5. Retain only non-overlapping suffix for partial overlap.
6. Record counters:
   - `deduplicated_repeated_prefix`
   - `cumulative_prefix_collapsed`
   - `deduplicated_event_count`
   - `episode_event_count`
   - `unique_event_count`

For non-session sources, within-source dedup is a no-op in v1.

## Migration

Add a new migration after `011_raw_episodes.sql`, for example
`migrations/012_artifact_identity.sql`.

```sql
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
ALTER TABLE archive ADD COLUMN overlap_summary_json TEXT NOT NULL DEFAULT '{}';

CREATE UNIQUE INDEX uniq_archive_raw_identity
    ON archive(raw_sha256, byte_size)
    WHERE raw_sha256 IS NOT NULL;

CREATE INDEX idx_archive_identity_fp ON archive(identity_fingerprint);
CREATE INDEX idx_archive_dup_state ON archive(duplicate_state);

CREATE TABLE artifact_identity_buckets (
    archive_id TEXT NOT NULL REFERENCES archive(id) ON DELETE CASCADE,
    bucket_kind TEXT NOT NULL,
    bucket_value TEXT NOT NULL,
    PRIMARY KEY (archive_id, bucket_kind, bucket_value)
);

CREATE INDEX idx_identity_lookup
    ON artifact_identity_buckets(bucket_kind, bucket_value);
```

`artifact_identity_buckets` stores non-content keys only. It never stores
extracted text.

## Concurrency

Two simultaneous ingests of identical bytes:

1. Both compute hash and size.
2. Both miss the initial lookup.
3. Both attempt to insert.
4. SQLite unique index chooses a winner.
5. Loser catches `IntegrityError`, re-reads the winner, deletes temporary
   duplicate bytes, and records an `exact_duplicate` submission row.

No app-level lock is required.

## Reprocess

Reprocess targets projection, not identity discovery.

- Reprocess skips Stage 0.
- Reprocess on `exact_duplicate` is a no-op against duplicate raw bytes and
  redirects to canonical metadata.
- Reprocess on `canonical_duplicate` redirects to `canonical_archive_id`.
- Reprocess on the canonical row updates connector-dependent metadata for the
  cluster.
- Foreground reprocess does not recompute `identity_fingerprint` unless the
  current fingerprint family version differs. Version-mismatch recompute belongs
  to maintenance.
- Maintenance recompute rewrites `artifact_identity_buckets` using the same
  bucket-write policy as ingest.

## Verification

- Byte-identical re-ingest records `exact_duplicate`, points to canonical, and
  skips connector/projection.
- Canonically equivalent but byte-different re-ingest records
  `canonical_duplicate`, preserves provenance, and still projects.
- Overlapping chat histories record `overlap_related`, preserve unique delta, and
  still project.
- `possible_duplicate` rows remain eligible for projection and review.
- New `unique`, `possible_duplicate`, and `overlap_related` rows write LSH
  buckets.
- `exact_duplicate` and `canonical_duplicate` rows do not write LSH buckets.
- Concurrent identical ingests deterministically create one canonical row and one
  exact-duplicate submission row.
- Sidecars are not deduplicated across parent artifacts in v1.
