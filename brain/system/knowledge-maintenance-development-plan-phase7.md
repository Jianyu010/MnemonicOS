# Knowledge Maintenance Phase 7: Pattern Detection and LLM Validation

## Purpose

Phase 7 is the second job of **Layer 1 — Cross-Wiki Linking** and the initial
implementation of **Layer 2a — Pattern Discovery**. It has two sub-passes that
will eventually split into independent jobs as the archetype vocabulary matures:

**Sub-pass A (Layer 1):** reads the `similar_to` graph edges produced by Phase 6,
groups them into clusters, and routes merge candidates to review. False positive
links are deleted.

**Sub-pass B (Layer 2a):** discovers new archetypes from accumulated notes using
two LLM-free detection channels (Channel 2 and Channel 3). LLM is used only to
validate and name archetype candidates — not for primary detection.

Phase 6 finds neighbors by geometric proximity. Phase 7 decides what the proximity
*means* and whether it reveals an archetype.

## Frequency

Fires on the batched trigger: N≥10 new notes promoted OR T≥1hr since last run.
Same trigger as Layer 2b (both layers fire together).

Requires `config.embeddings.enabled = True` and an LLM provider configured in
`config.extraction` (Ollama or equivalent).

## Detection channels (Layer 2a sub-pass)

### Channel 2 — Role-vocabulary cosine clustering (LLM-free)

Each note is projected into a 9-dim role vector using the immutable role vocabulary
(see plan doc Role Vocabulary section). Cosine similarity identifies note pairs
with matching role signatures. Clustered pairs become archetype candidates.

No LLM call for detection. LLM validates and names the candidate only.

### Channel 3 — Failure/resolution symmetry (LLM-free)

Scans `incident`-type notes for failure-signature tokens. Scans `procedure`-type
notes for resolution-signature tokens. Pairs with reciprocal signatures are
archetype candidates for the `same_failure_mode` / `evidence_for` relation family.

No LLM call for detection. LLM validates and names the candidate only.

### Channel 1 — deferred

Deductive via root logics. Requires full inference engine. Out of scope for Phase 7.

## Archetype candidate output

Each channel produces archetype candidates with this structure:

```python
@dataclass
class ArchetypeCandidate:
    source_channel: int              # 2 or 3
    note_pairs: list[tuple[str, str]]
    role_signature: dict             # dominant role terms (Channel 2)
    failure_token: str | None        # matched token (Channel 3)
    resolution_token: str | None
    candidate_score: float
```

LLM validation prompt receives the candidate pairs, their role signatures,
and titles/summaries. Expected output: `archetype_id`, `label`,
`layer_a_description`, `layer_b_role_schema`, `relation_type` (from blueprint 6.5),
`valid: bool`, `reject_reason: str | None`.

## Cluster construction (Layer 1 sub-pass)

A cluster is a connected component of `similar_to` graph edges within a single
note type. Construction:

1. Query `graph_edges WHERE relation_type = 'similar_to'`.
2. Build adjacency list; find connected components via union-find.
3. Exclude clusters of size 1 (singleton notes with no `similar_to` edges).
4. Cap cluster size at `config.maintenance.cluster_validation_max_size` (default: 12)
   to limit LLM prompt size. Larger components are split by highest-similarity
   edge ordering.

## LLM validation prompt (Layer 1 sub-pass — cluster validation)

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

## New functions in `maintenance.py`

### `run_cluster_validation` (Layer 1 sub-pass)

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

### `run_pattern_discovery` (Layer 2a sub-pass)

```python
def run_pattern_discovery(
    conn,
    config: AppConfig,
    llm_provider,
) -> PatternDiscoveryResult:
    """
    Channel 2: project notes into role vectors, cluster by cosine similarity.
    Channel 3: scan incident/procedure note pairs for failure/resolution symmetry.
    For each archetype candidate: call LLM to validate and name.
    Store confirmed archetypes in archetypes table.
    Propagate archetype_id + score to matching graph_edges rows.
    Returns: candidates_found, archetypes_confirmed, archetypes_rejected, edges_updated.
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

### `PatternDiscoveryResult`

```python
@dataclass
class PatternDiscoveryResult:
    channel_2_candidates: int = 0
    channel_3_candidates: int = 0
    archetypes_confirmed: int = 0
    archetypes_rejected: int = 0
    edges_updated: int = 0
    layer_2b_triggered: bool = False
    layer_3_eager_triggered: bool = False
```

## Wire-up in batched trigger job (`jobs.py`)

Phase 7 fires on the batched trigger (N≥10 new notes OR T≥1hr), alongside
Layer 2b. Order within the trigger: Layer 2a sub-pass first (pattern discovery),
then Layer 2b (graph rebuild through importance scoring), then Layer 1 sub-pass
(cluster validation on newly built similar_to links).

```python
# Layer 2a sub-pass: pattern discovery (Channel 2 + Channel 3)
if config.maintenance.pattern_discovery_enabled:
    from second_brain.maintenance import run_pattern_discovery
    llm = build_llm_provider(config)
    discovery_result = run_pattern_discovery(conn, config, llm)
    stats["pattern_discovery"] = asdict(discovery_result)

# ... Layer 2b phases (graph rebuild, lifecycle audit, importance scoring) ...

# Layer 1 sub-pass: cluster validation
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
4. Integration: full Layer 2b job dry-run with embeddings enabled →
   completes without error, stats include `cluster_validation`.
5. Full suite: `python -m pytest tests/ -x`.
