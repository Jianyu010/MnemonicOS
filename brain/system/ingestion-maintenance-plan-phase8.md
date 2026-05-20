# Knowledge Maintenance Phase 8: Raw Re-Visitation and Cross-Wiki Context Injection

## Purpose

Phase 8 is **Layer 3 — Raw Re-Visitation**. It runs on the Layer 3 schedule
after the lighter wiki-governance jobs have converged.
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

Daily at 2AM as the safety net, plus an eager trigger when an archetype's
`occurrence_count` exceeds M=5. Phase 8 is intentionally the least frequent
maintenance job because it re-triggers the full extraction→promotion pipeline for
selected sources, which is expensive.

## Mechanism 1: Stale re-queue

### Staleness criteria

A source is eligible for re-queue if ALL of the following hold:

1. `extraction_queue.status = 'processed'` (already extracted at least once).
2. `extraction_queue.finished_at < NOW() - config.maintenance.requeue_min_age_days` (default: 30).
3. At least one of:
   - `extraction_queue.extraction_schema_version < current SCHEMA_VERSION` (schema changed since extraction)
   - `extraction_queue.extraction_model_version != config.extraction.llm_model` (model changed)

### Re-queue process

1. Query `extraction_queue` for eligible rows.
2. For each eligible row: insert a **new** pending extraction queue row with the
   same source identity and `content_hash`.
3. Record lineage on the new row: `revisit_of_queue_id`, `revisit_reason`, and
   optional `revisit_context_json`.
4. Suppress duplicate revisits while a lineage-linked revisit row for the same
   source is already pending, processing, or processed.
5. The new row enters the standard extraction → promotion pipeline.
6. The promotion gate handles corroboration: if the re-extraction produces the
   same note, `corroboration_count` increments. If richer, the note is updated.
7. The old `extraction_queue` row, its `extraction_candidates`, and its
   `promotion_decisions` are NOT deleted — they remain historical record.

Implementation mark (2026-05-11): this is implemented by
`migrations/024_revisit_extraction_lineage.sql`, `enqueue_revisit_extraction()`,
and the revisitation query guard in `maintenance.py`.

### Schema additions

Add columns to `extraction_queue` (migration):

```sql
ALTER TABLE extraction_queue ADD COLUMN extraction_schema_version TEXT;
ALTER TABLE extraction_queue ADD COLUMN extraction_model_version TEXT;
ALTER TABLE extraction_queue ADD COLUMN revisit_of_queue_id INTEGER REFERENCES extraction_queue(id);
ALTER TABLE extraction_queue ADD COLUMN revisit_reason TEXT;
ALTER TABLE extraction_queue ADD COLUMN revisit_context_json TEXT;
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

### Historical Context Retrieval Design

This section records the original Phase 8 context-injection design. It is now
superseded for extraction packet construction by
`ingestion-extraction-plan-phase10.md`: extraction must read only the raw source
projection, while cross-wiki comparison belongs to promotion/review.

The earlier design retrieved the top-K wiki notes related to the source text
using `retrieval.retrieve()`:

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

The context block was originally injected into the LLM packet as a top-level key
named `"existing_knowledge_context"`. This packet key is no longer valid.
Historical `revisit_context_json` may remain in queued rows, but the extraction
worker must ignore it when building LLM packets.

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

Context injection is removed from the extraction path. Any future use of
revisit context must happen outside `make_llm_packet()`, preferably in the
promotion or review layer.

## New function in `maintenance.py`

### `run_raw_revisitation`

```python
def run_raw_revisitation(
    conn,
    config: AppConfig,
) -> RevisitationResult:
    """
    Find eligible stale extraction_queue rows.
    For each: enqueue a lineage-preserving re-extraction trigger.
    Returns: sources_requeued, sources_skipped.
    """
```

### `RevisitationResult`

```python
@dataclass
class RevisitationResult:
    sources_requeued: int = 0
    sources_skipped: int = 0
    context_injected_count: int = 0  # historical metric; no longer used by extraction
```

## Wire-up in Layer 3 job (`jobs.py`)

After `detect_semantic_duplicates` (Phase 5) and health stats:

```python
if config.maintenance.requeue_enabled:
    from second_brain.maintenance import run_raw_revisitation
    revisit_result = run_raw_revisitation(conn, config)
    stats["raw_revisitation"] = asdict(revisit_result)
```

## Release Repair: Historical Ledger Safety

Purpose:
- Prevent raw re-visitation from corrupting the extraction/promotion ledger for
  already-promoted sources.

Problem found in E2E:
- Resetting the original processed queue row to `pending` causes the next
  extraction pass to replace candidates for that row. If those candidates already
  have `promotion_decisions`, deletion fails on foreign-key constraints.

Concrete implementation plan:
1. Add the lineage columns listed above in the next migration.
2. Update `run_raw_revisitation` to insert fresh queue rows, never reset the
   original row.
3. Add a helper such as `enqueue_revisit_extraction(conn, row, reason, context)`
   so revisit insertion is explicit and testable.
4. Keep `revisit_context_json` as historical lineage metadata only; do not pass
   it to extraction LLM packets.
5. After the new row is inserted, trigger extraction/promotion through the normal
   pipeline or leave it pending for the scheduled queue job, depending on the
   caller's mode.

Acceptance tests:
- Seed a processed queue row with promoted candidates and `promotion_decisions`.
  Run raw re-visitation, then process extraction and promotion. Assert:
  old queue row remains processed;
  old candidates and promotion decisions still exist;
  new revisit queue row is processed;
  no foreign-key error occurs.
- Seed context retrieval results and assert the new queue row may store
  `revisit_context_json`, but the LLM packet does not receive it.
- Dry-run creates no new queue rows.

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
   rows, `extraction_candidates`, visual snippets, and `promotion_decisions`
   remain intact. The re-queue produces new rows.
2. **Context injection is read-only.** Retrieved wiki summaries are never
   modified by the re-extraction process.
3. **`requeue_max_per_run` caps throughput.** At most N sources are re-queued
   per weekly run to prevent runaway resource consumption.
4. **Corroboration is the merge mechanism.** The promotion gate handles
   de-duplication via `corroboration_count`. Phase 8 does not implement
   custom merge logic.
5. **`dry_run=True` suppresses all re-queue calls and context writes.**

## Schema migration

`migrations/023_extraction_versioning.sql` exists for version columns. Add the
lineage fields in the next available migration:

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
5. Integration: promoted-source revisit E2E → completes without foreign-key
   failure and produces a new processed revisit queue row.
6. Integration: full Layer 3 job dry-run → completes without error,
   stats include `raw_revisitation`.
7. Full suite: `python -m unittest discover -s tests`.

## Maintenance job flow (all phases complete)

```
Layer 1 job (per-ingestion + scheduled sweep):
  1. run_embedding_neighbor_pass()           ← Phase 6, per new note
  2. run_embedding_neighbor_backfill()       ← Phase 6, sweep for unscanned notes
  3. run_cluster_validation()                ← Phase 7, validates similar_to edges

Layer 2a job (every few hours):
  1. run_pattern_discovery()                 ← Phase 7 extension
     (names abstract patterns from accumulated clusters)

Layer 2b job (daily):
  1. rebuild_wiki_graph()                    ← Phase 2
  2. detect_orphan_nodes()                   ← Phase 2
  3. audit_lifecycle_states()                ← Phase 3
  4. compute_note_trust_stats()              ← trust.py
  5. compute_note_freshness()                ← trust.py
  6. compute_importance_scores()             ← Phase 4
  7. compute_wiki_health_stats()             ← Phase 5
  8. duplicate alias detection + review routing
  9. verification debt + stale note review routing
  10. relearn task creation
  11. refresh_active_file()
  12. write_maintenance_overview()

Layer 3 job (every 2–3 days):
  1. run_raw_revisitation()                  ← Phase 8
  2. train_rerank_model()                    ← trust.py
```
