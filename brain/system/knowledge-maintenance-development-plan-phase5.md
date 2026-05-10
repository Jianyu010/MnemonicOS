# Knowledge Maintenance Phase 5: Semantic Dedup and Wiki Health

## Goal

Run weekly: find conceptually identical notes with different titles via embedding
similarity, route them to review. Produce a structured wiki health snapshot that
gives a complete picture of the wiki's state and feeds the maintenance overview.

## Context

The existing duplicate alias detection in `jobs.py` catches title/alias string
collisions. It does not catch two notes about the same concept that were written
with different vocabulary, by different agents, at different times. These
accumulate silently.

Similarly, the existing `write_maintenance_overview` in `ops.py` produces a
narrative report but does not include structured health stats: note counts by
lifecycle state, graph connectivity distribution, orphan counts, or the number
of lifecycle transitions made in the current run.

Phase 5 closes both gaps. It runs only in Layer 3 job because embedding
comparison is O(N²) in note count.

## Scope

### `SemanticDuplicatePair` dataclass in `maintenance.py`

```python
@dataclass(slots=True)
class SemanticDuplicatePair:
    note_id_a: str
    note_id_b: str
    similarity: float
    title_a: str
    title_b: str
    note_type: str
```

### `detect_semantic_duplicates(connection, config, *, dry_run=False) -> list[SemanticDuplicatePair]`

Algorithm:

1. Guard: if `not config.embeddings.enabled` or
   `not config.maintenance.semantic_dedup_enabled`, return `[]`.
2. Query all active, non-archived notes: `SELECT id, type, title, summary FROM notes
   WHERE status IS NULL OR status NOT IN ('retired', 'archived', 'superseded')
   ORDER BY type, id`.
3. Group by `type` (only compare within same type — a `concept` should not be
   compared against a `decision`).
4. For each type group with ≥ 2 notes:
   a. Encode `note.summary` via `encode_text(summary, dimensions=config.embeddings.dimensions)`
      from `semantics.py` (already exists, reuse).
   b. Compute cosine similarity for all pairs within the group.
   c. Collect pairs with similarity ≥ `config.maintenance.semantic_dedup_threshold`
      (default 0.92).
5. Deduplicate pairs (A,B == B,A).
6. If not `dry_run`: for each pair, call `create_review_item(config,
   review_type="merge_review", ...)` with both note IDs. Use
   `slug_seed=f"semantic-dedup-{note_id_a}-{note_id_b}"` to prevent duplicate
   review items across weekly runs.
7. Return list of `SemanticDuplicatePair`.

**Notes:**
- Only compare notes whose `lifecycle_state` is not `contested` or `archived`.
- If either note in a pair already has a `merge_review` item open, skip creating
  a duplicate (check via `load_open_review_items`).
- Encoding is expensive: cache results in a local dict by `note_id` within a
  single run.

### `WikiHealthStats` dataclass in `maintenance.py`

```python
@dataclass(slots=True)
class WikiHealthStats:
    total_notes: int
    notes_by_type: dict[str, int]
    notes_by_lifecycle: dict[str, int]
    notes_by_freshness: dict[str, int]
    orphan_note_count: int
    avg_in_degree: float
    avg_out_degree: float
    lifecycle_advanced_this_run: int
    semantic_dedup_candidates: int
    open_review_count: int
    open_relearn_count: int
```

### `compute_wiki_health_stats(connection, *, audit_result=None, dedup_pairs=None, open_review_count=0, open_relearn_count=0) -> WikiHealthStats`

Reads from:
- `notes` table for type/lifecycle counts
- `note_freshness` for freshness state distribution
- `graph_edges` for avg degree
- `detect_orphan_nodes(connection)` (Phase 2, already implemented)
- Passed-in results from audit and dedup functions

```sql
-- note counts by type
SELECT type, COUNT(*) AS cnt FROM notes
WHERE status IS NULL OR status NOT IN ('retired', 'archived')
GROUP BY type

-- notes by lifecycle
SELECT COALESCE(lifecycle_state, 'draft') AS state, COUNT(*) AS cnt
FROM notes WHERE status IS NULL OR status NOT IN ('retired', 'archived')
GROUP BY state

-- notes by freshness
SELECT COALESCE(freshness_state, 'unknown') AS state, COUNT(*) AS cnt
FROM note_freshness
GROUP BY state

-- avg degree
SELECT
    AVG(out_d.cnt) AS avg_out,
    AVG(in_d.cnt) AS avg_in
FROM (
    SELECT source_note_id, COUNT(*) AS cnt FROM graph_edges GROUP BY source_note_id
) out_d
CROSS JOIN (
    SELECT target_id, COUNT(*) AS cnt FROM graph_edges GROUP BY target_id
) in_d
```

### Wire into Layer 3 job in `jobs.py`

```python
if job_name == "Layer 3 job":
    dedup_pairs = detect_semantic_duplicates(connection, config, dry_run=dry_run)
    health_stats = compute_wiki_health_stats(
        connection,
        audit_result=audit_result,
        dedup_pairs=dedup_pairs,
        open_review_count=len(open_review_items),
        open_relearn_count=len(relearn_tasks),
    )
    stats["semantic_dedup_candidates"] = len(dedup_pairs)
    stats["wiki_total_notes"] = health_stats.total_notes
    stats["wiki_orphan_notes"] = health_stats.orphan_note_count
```

### Enhance `write_maintenance_overview` in `ops.py`

Add optional `wiki_health: WikiHealthStats | None = None` parameter. When
provided, prepend a **Wiki Health** section to the overview markdown:

```markdown
## Wiki Health

| Metric | Value |
|---|---|
| Total notes | {total_notes} |
| Canonical | {notes_by_lifecycle.canonical} |
| Verified | {notes_by_lifecycle.verified} |
| Draft | {notes_by_lifecycle.draft} |
| Contested | {notes_by_lifecycle.contested} |
| Orphan notes | {orphan_note_count} |
| Avg in-degree | {avg_in_degree:.1f} |
| Avg out-degree | {avg_out_degree:.1f} |
| Lifecycle advanced this run | {lifecycle_advanced_this_run} |
| Semantic dedup candidates | {semantic_dedup_candidates} |
```

## Milestones

1. `SemanticDuplicatePair` and `WikiHealthStats` dataclasses in `maintenance.py`.
2. `detect_semantic_duplicates` returns correct pairs for seeded near-identical notes.
3. `detect_semantic_duplicates` skips when embeddings disabled.
4. `compute_wiki_health_stats` returns correct counts from seeded DB state.
5. Layer 3 job calls both functions and includes stats.
6. `write_maintenance_overview` renders Wiki Health section when stats provided.
7. Dry-run: no review items created, dedup pairs still returned for counting.

## Verification

- Test: seed two notes of the same type with identical or near-identical summaries;
  mock `encode_text` to return known vectors; run `detect_semantic_duplicates`;
  verify pair found and merge_review item created.
- Test: seed same pair, run a second time → no duplicate review item (slug_seed
  idempotency).
- Test: embeddings disabled → function returns `[]` without calling `encode_text`.
- Test: `compute_wiki_health_stats` with 5 notes across types/lifecycle states →
  counts match.
- Test: `detect_orphan_nodes` count flows into `WikiHealthStats.orphan_note_count`.
- Integration: Layer 3 job dry-run completes; stats include `semantic_dedup_candidates`
  and `wiki_total_notes`.
- Integration: maintenance overview file after weekly run includes Wiki Health table.

## Out of scope

- Automatic note merging — always routes to review, never auto-merges.
- Cross-type semantic comparison (a concept and a procedure may share vocabulary
  without being duplicates).
- Graph-distance-weighted similarity (embedding similarity only for Phase 5).
- Embedding caching across job runs (in-memory cache within one job run only).
