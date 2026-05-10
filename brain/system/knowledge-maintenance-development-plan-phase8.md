# Knowledge Maintenance Phase 8: Raw Re-Visitation and Cross-Wiki Context Injection

## Purpose

Phase 8 is **Layer 3 — Raw Re-Visitation**. It runs weekly within `weekly_hygiene`.
It addresses a fundamental limitation: the first extraction pass over a source
happens before the wiki has any related notes. Later, as the wiki matures, the
same source could yield richer candidates — especially if the LLM is given the
wiki's existing knowledge as context during re-extraction.

Phase 8 has two mechanisms:

1. **Stale re-queue**: re-submit sources whose extraction is stale due to
   schema version drift or model change. Produces new extraction queue rows
   through the standard pipeline.
2. **Cross-wiki context injection**: when re-extracting, inject summaries of
   existing wiki notes related to the same source into the LLM extraction prompt.
   The LLM can then detect conflicts with existing knowledge and produce richer
   candidates that reference prior wiki state.

## Frequency

Weekly, within `weekly_hygiene`. Phase 8 is intentionally the least frequent
maintenance job because it re-triggers the full extraction→promotion pipeline for
selected sources, which is expensive.

## Mechanism 1: Stale re-queue

### Staleness criteria

A source is eligible for re-queue if ALL of the following hold:

1. `extraction_queue.status = 'finished'` (already extracted at least once).
2. `extraction_queue.last_extracted_at < NOW() - config.maintenance.requeue_min_age_days` (default: 30).
3. At least one of:
   - `extraction_queue.extraction_schema_version < current SCHEMA_VERSION` (schema changed since extraction)
   - `extraction_queue.extraction_model_version != config.extraction.llm_model` (model changed)

### Re-queue process

1. Query `extraction_queue` for eligible rows.
2. For each eligible row: call `trigger_pipeline(source_ref, config)` to submit
   a new extraction queue row with the current schema version and model.
3. The new row enters the standard extraction → promotion pipeline.
4. The promotion gate handles corroboration: if the re-extraction produces the
   same note, `corroboration_count` increments. If richer, the note is updated.
5. The old `extraction_queue` row is NOT deleted — it remains as historical record.

### Schema additions

Add columns to `extraction_queue` (migration):

```sql
ALTER TABLE extraction_queue ADD COLUMN extraction_schema_version TEXT;
ALTER TABLE extraction_queue ADD COLUMN extraction_model_version TEXT;
```

`mark_finished()` in `queue.py` must be updated to write these columns when
finishing an extraction.

## Mechanism 2: Cross-wiki context injection

### Concept

During re-extraction, the LLM extraction prompt is extended with a "Existing
Knowledge Context" section containing summaries of wiki notes related to the
same source. This lets the LLM:

- Avoid re-extracting facts already well-represented in the wiki.
- Detect contradictions between the source and existing wiki state.
- Produce candidates that explicitly reference related wiki notes (richer `relations`
  frontmatter).
- Identify richer structure (e.g., a source that previously yielded one `source`
  note now yields a `procedure` note because the wiki has an analogous procedure
  that provides framing).

### Context retrieval

Before submitting a re-extraction, retrieve the top-K wiki notes related to the
source's text using `retrieval.retrieve()`:

```python
related_notes = retrieve(
    query=source_summary,
    config=config,
    top_k=config.maintenance.requeue_context_top_k,  # default: 5
)
context_block = "\n\n".join(
    f"[{note.note_id}] {note.title}\n{note.summary}"
    for note in related_notes
)
```

The context block is injected into the LLM packet as a new top-level key:
`"existing_knowledge_context": context_block`.

### LLM prompt extension (Ollama provider)

The re-extraction prompt adds after the standard CANDIDATE RULES section:

```
EXISTING KNOWLEDGE CONTEXT:
The following notes already exist in the wiki for this domain.
Use them to avoid duplicating well-represented knowledge.
If the source contradicts any existing note, include the
contradiction in the relevant candidate's "body" field.
Do NOT fabricate information not present in the source.

{context_block}
```

Context injection is suppressed when `context_block` is empty or when
`config.maintenance.requeue_context_enabled = False`.

## New function in `maintenance.py`

### `run_raw_revisitation`

```python
def run_raw_revisitation(
    conn,
    config: AppConfig,
) -> RevisitationResult:
    """
    Find eligible stale extraction_queue rows.
    For each: retrieve related wiki context, inject into re-extraction trigger.
    Returns: sources_requeued, sources_skipped, context_injected_count.
    """
```

### `RevisitationResult`

```python
@dataclass
class RevisitationResult:
    sources_requeued: int = 0
    sources_skipped: int = 0
    context_injected_count: int = 0
```

## Wire-up in `weekly_hygiene` (`jobs.py`)

After `detect_semantic_duplicates` (Phase 5) and health stats:

```python
if config.maintenance.requeue_enabled:
    from second_brain.maintenance import run_raw_revisitation
    revisit_result = run_raw_revisitation(conn, config)
    stats["raw_revisitation"] = asdict(revisit_result)
```

## Config additions to `MaintenanceConfig`

```python
@dataclass
class MaintenanceConfig:
    # ... existing fields ...
    requeue_enabled: bool = True
    requeue_min_age_days: int = 30
    requeue_context_enabled: bool = True
    requeue_context_top_k: int = 5
    requeue_max_per_run: int = 20  # cap to prevent runaway re-queueing
```

## Invariants

1. **Re-visitation never deletes original candidates.** Old `extraction_queue`
   rows and their `extraction_candidates` remain intact. The re-queue produces
   new rows.
2. **Context injection is read-only.** Retrieved wiki summaries are never
   modified by the re-extraction process.
3. **`requeue_max_per_run` caps throughput.** At most N sources are re-queued
   per weekly run to prevent runaway resource consumption.
4. **Corroboration is the merge mechanism.** The promotion gate handles
   de-duplication via `corroboration_count`. Phase 8 does not implement
   custom merge logic.
5. **`dry_run=True` suppresses all re-queue calls and context writes.**

## Schema migration

`migrations/020_extraction_versioning.sql` (or next available number):

```sql
ALTER TABLE extraction_queue ADD COLUMN extraction_schema_version TEXT;
ALTER TABLE extraction_queue ADD COLUMN extraction_model_version TEXT;
```

## Verification

1. Unit: `RevisitationTest` — seed one `extraction_queue` row with old
   schema_version → call `run_raw_revisitation` → assert new queue row submitted,
   old row untouched.
2. Unit: context injection test — seed wiki notes, mock `retrieve()` → assert
   returned context block appears in the re-extraction packet.
3. Unit: `requeue_max_per_run` cap — 25 eligible sources, cap=20 → exactly 20
   re-queued.
4. Unit: dry-run test — eligible sources exist → dry_run=True → no new queue
   rows, result counts reflect what would have happened.
5. Integration: full `weekly_hygiene` dry-run → completes without error,
   stats include `raw_revisitation`.
6. Full suite: `python -m pytest tests/ -x`.

## Revised daemon job flow (all phases complete)

```
daily_consolidation (3 AM):
  1. rebuild_wiki_graph()                    ← Phase 2
  2. detect_orphan_nodes()                   ← Phase 2
  3. audit_lifecycle_states()                ← Phase 3
  4. compute_note_trust_stats()              ← existing
  5. compute_note_freshness()                ← existing
  6. run_embedding_neighbor_backfill()       ← Phase 6 (if embeddings enabled)
  7. run_cluster_validation()               ← Phase 7 (if embeddings enabled)
  8. compute_importance_scores()             ← Phase 4
  9. sync_eval_candidates()                  ← existing
  10. _verification_debt_rows()              ← existing
  11. duplicate alias detection              ← existing
  12. _stale_rows_from_freshness()           ← existing
  13. create review/relearn items            ← existing
  14. refresh_active_file()                  ← existing

weekly_hygiene (Sunday 4 AM):
  all of daily_consolidation, plus:
  15. compute_wiki_health_stats()            ← Phase 5
  16. train_rerank_model()                   ← existing
  17. run_raw_revisitation()                 ← Phase 8
  18. write_maintenance_overview()           ← existing (enhanced)
```
