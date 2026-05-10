# Knowledge Maintenance Phase 7: LLM Pattern Validation and Refinement

## Purpose

Phase 7 is the second job of **Layer 1 — Cross-Wiki Linking**. It runs daily
within `daily_consolidation`. It reads the `similar_to` graph edges produced by
Phase 6, groups them into clusters, asks the LLM to validate each cluster, name
the abstract pattern shared by the notes, and optionally refine the relation type.
False positive links are deleted. True semantic duplicates are escalated to merge
review.

Phase 6 finds neighbors by geometric proximity in embedding space. Phase 7 decides
what the proximity *means* in MnemonicOS's domain.

## Frequency

Daily, within `daily_consolidation`, after Phase 6 backfill.

Requires `config.embeddings.enabled = True` and an LLM provider configured in
`config.extraction` (Ollama or equivalent).

## Cluster construction

A cluster is a connected component of `similar_to` graph edges within a single
note type. Construction:

1. Query `graph_edges WHERE relation_type = 'similar_to'`.
2. Build adjacency list; find connected components via union-find.
3. Exclude clusters of size 1 (singleton notes with no `similar_to` edges).
4. Cap cluster size at `config.maintenance.cluster_validation_max_size` (default: 12)
   to limit LLM prompt size. Larger components are split by highest-similarity
   edge ordering.

## LLM validation prompt

For each cluster, the prompt includes:
- All note titles and summaries in the cluster
- The pairwise similarity scores that produced the edges
- The question: "Are these notes genuinely semantically related? What abstract
  pattern or structural relationship do they share? Which pairs (if any) are
  near-duplicates rather than merely related?"

Expected JSON response:

```json
{
  "cluster_valid": true,
  "abstract_pattern": "Recovery operation after a failure event",
  "pattern_tag": "pattern:recovery-after-failure",
  "relations": [
    {
      "src_note_id": "incident/rollback-after-migration",
      "tgt_note_id": "procedure/undo-broken-deploy",
      "valid": true,
      "refined_relation_type": "similar_to",
      "merge_candidate": false
    },
    {
      "src_note_id": "incident/rollback-after-migration",
      "tgt_note_id": "incident/cache-flush-after-bad-push",
      "valid": true,
      "refined_relation_type": "similar_to",
      "merge_candidate": true,
      "merge_rationale": "Both describe the same recovery action in different surface vocabulary."
    }
  ],
  "invalid_pairs": [
    {
      "src_note_id": "incident/rollback-after-migration",
      "tgt_note_id": "concept/database-migration-strategy",
      "reason": "Embedding proximity due to shared 'migration' keyword, not semantic relationship."
    }
  ]
}
```

## Response handling

### Valid links

- If `refined_relation_type` differs from `similar_to`, update the frontmatter
  and graph edge relation type accordingly.
- Add `pattern_tag` to both notes' frontmatter tags if not already present.

### Invalid links

- Remove `similar_to` relation from both notes' frontmatter.
- Delete the `graph_edges` row for the pair.
- Log as `false_positive_removed`.

### Merge candidates

- Call `create_review_item` with `review_type="merge_review"` and the LLM's
  `merge_rationale` as context.
- Do NOT remove the `similar_to` link — it remains until a human resolves the
  review item.

### Pattern tag storage

Pattern tags take the form `pattern:<slug>` (e.g., `pattern:recovery-after-failure`).
They are written to notes' frontmatter `tags` list. They can be queried to find all
notes in a given cluster pattern without walking graph edges.

## New function in `maintenance.py`

### `run_cluster_validation`

```python
def run_cluster_validation(
    conn,
    config: AppConfig,
    llm_provider,
) -> ClusterValidationResult:
    """
    Build similar_to clusters from graph_edges.
    For each cluster: call LLM to validate, refine, and name pattern.
    Apply results: update/delete graph edges, add pattern tags, route merge reviews.
    Returns: clusters_processed, links_confirmed, links_removed, merge_reviews_created.
    """
```

### `ClusterValidationResult`

```python
@dataclass
class ClusterValidationResult:
    clusters_processed: int = 0
    links_confirmed: int = 0
    links_removed: int = 0
    merge_reviews_created: int = 0
    patterns_named: int = 0
```

## Wire-up in `daily_consolidation` (`jobs.py`)

After Phase 6 backfill, before importance scoring:

```python
if config.embeddings.enabled and config.maintenance.cluster_validation_enabled:
    from second_brain.maintenance import run_cluster_validation
    llm = build_llm_provider(config)
    cluster_result = run_cluster_validation(conn, config, llm)
    stats["cluster_validation"] = asdict(cluster_result)
```

## Config additions to `MaintenanceConfig`

```python
@dataclass
class MaintenanceConfig:
    # ... existing fields ...
    cluster_validation_enabled: bool = True
    cluster_validation_max_size: int = 12
    cluster_validation_min_cluster_size: int = 2
```

## Invariants

- Phase 7 never asserts new `similar_to` links. It only validates or removes
  links Phase 6 already asserted.
- Pattern tags are additive — Phase 7 never removes existing tags from a note.
- Merge review items follow the existing `create_review_item` contract.
  Phase 7 does not implement any merge logic itself.
- `dry_run=True` suppresses all writes and review item creation.
- If the LLM call fails for a cluster (timeout, parse error), that cluster is
  skipped and logged; the job continues with remaining clusters.

## RL training data production

Every Phase 7 run produces validated pairs for embedding model fine-tuning:

- Confirmed link → positive pair `(note_a_id, note_b_id, label=1)`
- Removed link → negative pair `(note_a_id, note_b_id, label=0)`

Store in `maintenance_training_pairs` table:

```sql
CREATE TABLE IF NOT EXISTS maintenance_training_pairs (
    id INTEGER PRIMARY KEY,
    note_a_id TEXT NOT NULL,
    note_b_id TEXT NOT NULL,
    label INT NOT NULL,  -- 1=positive, 0=negative
    source TEXT NOT NULL DEFAULT 'cluster_validation',
    created_at TEXT NOT NULL
);
```

When `len(maintenance_training_pairs) >= config.maintenance.contrastive_finetune_threshold`
(default: 500), the weekly job can trigger a fine-tuning run (Phase 7 extension,
not implemented in initial delivery).

## Verification

1. Unit: `ClusterValidationTest` — seed three concept notes with `similar_to`
   edges, mock LLM to return one valid link, one invalid, one merge candidate →
   assert invalid edge deleted, merge_review item created, pattern tag added.
2. Unit: cluster size cap test — component of size 15 splits into two clusters
   of ≤ 12 before LLM call.
3. Unit: LLM parse failure test — malformed JSON response → cluster skipped,
   no writes, job continues.
4. Integration: full `daily_consolidation` dry-run with embeddings enabled →
   completes without error, stats include `cluster_validation`.
5. Full suite: `python -m pytest tests/ -x`.
