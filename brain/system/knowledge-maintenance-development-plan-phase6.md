# Knowledge Maintenance Phase 6: Embedding-Based Neighbor Linking

## Purpose

Phase 6 is the foundational job of **Layer 1 — Cross-Wiki Linking**. It runs
per-promotion (triggered immediately after each new note lands in the wiki) and
daily (as a backfill sweep for notes not yet scanned). It finds each note's K
nearest semantic neighbors using `encode_text()` and asserts `similar_to` typed
cross-wiki links between them. At a higher threshold (θ=0.92), it also flags
near-duplicate pairs for merge review.

This is how MnemonicOS discovers that "cheetah chases deer" and "predator pursuing
prey" share an abstract structural pattern: the embedding model encodes semantic
meaning, not surface vocabulary.

## Frequency

- **Per-promotion**: `run_embedding_neighbor_pass(conn, config, note_id)` called
  immediately after each note is promoted in `promotion/worker.py`, before the
  next note in the batch.
- **Daily**: `run_embedding_neighbor_backfill(conn, config)` called in
  `daily_consolidation` to catch any notes whose last scan is older than
  `config.maintenance.neighbor_scan_max_age_days` (default: 7).

Both require `config.embeddings.enabled = True`.

## Prerequisites

- `nomic-embed-text` installed in Ollama (see `knowledge-maintenance-embedding-model.md`).
- `config.embeddings.enabled = true` in `config/memory.toml`.
- `note_embeddings` table populated (by `embed_backfill` job already in pipeline).

## New schema

### `note_embedding_scans` table

```sql
CREATE TABLE IF NOT EXISTS note_embedding_scans (
    note_id TEXT PRIMARY KEY,
    last_scanned_at TEXT NOT NULL,
    neighbor_count INT NOT NULL DEFAULT 0,
    scan_threshold REAL NOT NULL DEFAULT 0.78
);
```

Tracks when each note last had its neighbors scanned. The daily backfill uses
`last_scanned_at` to decide which notes need re-scanning.

Migration: `migrations/018_note_embedding_scans.sql` (renumbered as needed).

## New functions in `maintenance.py`

### `find_embedding_neighbors`

```python
def find_embedding_neighbors(
    conn,
    config: AppConfig,
    note_id: str,
    *,
    k: int = 10,
    threshold: float = 0.78,
) -> list[NeighborMatch]:
    """
    Embed note summary, query note_embeddings for K nearest by cosine similarity.
    Only returns notes of the same type as note_id (concept↔concept, etc.).
    Only returns notes with similarity >= threshold.
    Excludes the note itself.
    Returns list of NeighborMatch(note_id, similarity_score).
    """
```

Implementation uses:
- `encode_text(note.summary, config=config)` to get query vector
- `note_embeddings` table for stored vectors
- Cosine similarity in Python (no vector DB required at this scale)
- Note type filter via `notes.id` prefix (`concept/`, `procedure/`, etc.)

### `assert_similar_to_link`

```python
def assert_similar_to_link(
    conn,
    config: AppConfig,
    src_id: str,
    tgt_id: str,
    *,
    similarity_score: float,
) -> bool:
    """
    Write a similar_to relation to both notes' frontmatter and graph_edges.
    Idempotent: returns False if link already exists.
    Returns True if link was newly added.
    """
```

Implementation:
1. Call `apply_relation_to_note(src_path, target_note_id=tgt_id, relation_type="similar_to")`
   (already exists in `promotion/worker.py` — reuse or move to shared location).
2. Call `graph.rebuild_note_graph(conn, src_note)` to refresh graph edges.
3. Does NOT add reverse link automatically — Phase 7 decides directionality.

### `run_embedding_neighbor_pass`

```python
def run_embedding_neighbor_pass(
    conn,
    config: AppConfig,
    note_id: str,
) -> int:
    """
    Per-promotion entry point. Find neighbors for a single note.
    Assert similar_to links for neighbors above theta=0.78.
    Flag pairs above theta=0.92 as merge_review candidates via create_review_item.
    Update note_embedding_scans row.
    Returns count of links asserted.
    """
```

Thresholds:
- `config.maintenance.neighbor_similarity_threshold` (default: 0.78) → `similar_to`
- `config.maintenance.semantic_dedup_threshold` (default: 0.92) → `merge_review`

The dedup threshold (0.92) subsumes the original Phase 5 semantic dedup trigger.
Phase 5's `detect_semantic_duplicates` function is replaced by this per-promotion
path plus the daily backfill.

### `run_embedding_neighbor_backfill`

```python
def run_embedding_neighbor_backfill(
    conn,
    config: AppConfig,
) -> int:
    """
    Daily backfill. Scan all active notes whose last_scanned_at is older than
    config.maintenance.neighbor_scan_max_age_days or has no scan record.
    Calls run_embedding_neighbor_pass for each.
    Returns total links asserted across all scanned notes.
    """
```

## Config additions to `MaintenanceConfig`

```python
@dataclass
class MaintenanceConfig:
    # ... existing fields ...
    neighbor_similarity_threshold: float = 0.78
    semantic_dedup_threshold: float = 0.92
    neighbor_k: int = 10
    neighbor_scan_max_age_days: int = 7
```

## Wire-up

### Per-promotion: `promotion/worker.py`

After `_apply_intra_source_relations(conn, config, promoted_by_queue, result)`:

```python
if config.embeddings.enabled and config.maintenance.embedding_neighbor_enabled:
    from second_brain.maintenance import run_embedding_neighbor_pass
    for note_id in all_promoted_note_ids:
        result.relations_applied += run_embedding_neighbor_pass(conn, config, note_id)
```

### Daily backfill: `jobs.py` `daily_consolidation`

After Phase 4 (importance scoring):

```python
if config.embeddings.enabled and config.maintenance.embedding_neighbor_enabled:
    from second_brain.maintenance import run_embedding_neighbor_backfill
    links = run_embedding_neighbor_backfill(conn, config)
    stats["neighbor_links_asserted"] = links
```

## Invariants

- Only same-type note pairs are linked (concept↔concept, etc.).
- `similar_to` links are never asserted between a note and itself.
- Merge review items are created via `create_review_item`, never auto-merged.
- `dry_run=True` suppresses all writes.
- If embedding model is unavailable, the pass fails gracefully: logs warning,
  skips without error, does not block promotion or daily job.

## Verification

1. Unit: `EmbeddingNeighborTest` — seed two concept notes with similar summaries,
   mock `encode_text`, call `run_embedding_neighbor_pass` → assert `similar_to`
   graph edge added to both source and target notes.
2. Unit: deduplicate test — similarity > 0.92 → `merge_review` item created,
   not just `similar_to` link.
3. Unit: type boundary test — concept note does not link to procedure note at
   any threshold.
4. Integration: `run_embedding_neighbor_backfill` on seeded DB → all un-scanned
   notes gain scan records; links correctly counted.
5. Full suite: `python -m pytest tests/ -x`.

## What Phase 7 consumes from Phase 6

Phase 7 reads the `similar_to` graph edges produced here, groups them into
clusters, and calls the LLM to validate each cluster, name abstract patterns,
and route false positives for deletion.
