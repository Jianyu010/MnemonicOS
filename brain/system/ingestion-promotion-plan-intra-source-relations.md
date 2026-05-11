# Promotion Layer Amendment: Intra-Source Relation Application

## Purpose

This document describes the promotion layer changes required to apply intra-source
relations produced by LLM/VLM Phase 8 to wiki note frontmatter.

It is an amendment to `ingestion-promotion-plan.md`. It does not replace
any existing phase; it extends the promotion worker and renderer with bundle-aware
post-processing.

## Dependency

Requires LLM/VLM Phase 8 to be implemented first:
- `extraction_queue.intra_source_relations_json` column must exist.
- Validated relation entries must be stored by the extraction worker.

## Context

After extraction, the `extraction_queue` row holds a JSON column:

```json
[
  {
    "source_index": 1,
    "target_index": 0,
    "relation_type": "derived_from"
  }
]
```

`source_index` and `target_index` are 0-based positions in the `candidates` array the LLM
emitted. They map 1:1 to `candidate_index` in `extraction_candidates` (UNIQUE per queue row).
Index-based resolution eliminates the title-collision bug: if two candidates in the same bundle
have identical titles, index-based resolution is still unambiguous.

Note IDs are only knowable after promotion (`note_id = type/slug(title)`), so the promotion
worker resolves `candidate_index → note_id` from the `promoted_by_queue` dict it builds during
the per-candidate loop.

The promotion worker currently processes candidates **one at a time** without
awareness of which queue row (bundle) they belong to, or what other candidates in
that bundle were promoted. To apply intra-source relations, the worker needs
bundle-level awareness.

## Changes

### `promotion/worker.py` — bundle-aware post-processing

After the main per-candidate processing loop, add a second pass over the queue rows
that were processed in this run:

```python
def _apply_intra_source_relations(
    conn,
    config: AppConfig,
    promoted_by_queue: dict[int, dict[int, str]],
    # queue_id → {candidate_index → promoted_note_id}
    result: PromotionRunResult,
) -> None:
    """
    For each queue row processed, load its intra_source_relations_json.
    For each relation, resolve source_index and target_index to note IDs
    using promoted_by_queue. Append resolved relation to source note's
    frontmatter. Rebuild graph for each updated note.
    Updates result.relations_applied in-place.
    """
```

Called at the end of `process_promotion_queue`, after the per-candidate loop,
inside the `try` block while `conn` is still open.

**Index-to-ID resolution:** during the per-candidate loop, the worker collects
`promoted_by_queue[queue_id][candidate_index] = note_id` for each successfully
promoted (non-review) candidate. `candidate_index` is read from the SELECT query
(`ec.candidate_index`). `note_id` is read from `promotion_decisions.target_note_id`
after `write_promotion` succeeds.

**Skip conditions:**
- Source or target was not promoted in this run (review-routed or rejected) → skip.
- Source or target note file no longer exists on disk → skip, log warning.
- Relation already present in source note's frontmatter → skip (idempotent).

### `promotion/writer.py` or `maintenance.py` — `apply_relation_to_note`

```python
def apply_relation_to_note(
    note_path: Path,
    *,
    target_note_id: str,
    relation_type: str,
) -> bool:
    """
    Read note frontmatter. Append {target, type} to relations list if not already
    present. Write back atomically. Return True if changed.
    """
```

Implementation:
1. `split_frontmatter(note_path.read_text())` → `(metadata, body)`
2. `relations = list(metadata.get("relations") or [])`
3. If `{"target": target_note_id, "type": relation_type}` already in relations → return False.
4. Append. Re-serialize. Write `.tmp` → `os.rename()`.
5. Return True.

Then call `rebuild_note_graph(connection, parse_note(note_path))` for the updated
note.

### `sync.py` — no change needed

The atomic write + graph rebuild is sufficient. A full vault sync will pick up the
updated frontmatter on the next scheduled run regardless.

## Data model: `relations` frontmatter field

The `relations` frontmatter field already exists in the parser:

```python
raw_relations = metadata.get("relations")
if isinstance(raw_relations, list):
    for entry in raw_relations:
        target = str(entry.get("target", "")).strip()
        relation_type = str(entry.get("type", "")).strip()
        if target and relation_type:
            relations.append((target, relation_type))
```

So the `relations` field already flows into `entity_relations` and from there into
`graph_edges`. No schema change is needed. The renderer just needs to write to it.

Format: `relations: [{target: "concept/b-tree", type: "derived_from"}, ...]`

This is the generic escape hatch for typed relations that do not map to a named
frontmatter field like `linked_decisions` or `linked_procedures`. Using it keeps
the frontmatter clean for the common named fields and handles the richer relation
vocabulary from Phase 8.

## Queue grouping

The extraction worker processes queue rows sequentially (one per run tick). Within
one `process_promotion_queue` call, multiple candidates from the **same** queue row
may be processed. The worker groups them:

```python
promoted_by_queue: dict[int, dict[int, str]] = {}
# queue_row_id → {candidate_index → promoted_note_id}
```

For each successfully promoted candidate (SELECT query must include `ec.candidate_index`):
```python
promoted_by_queue.setdefault(queue_id, {})[candidate_index] = note_id
```

After the candidate loop:
```python
relations_applied = _apply_intra_source_relations(config, connection, promoted_by_queue)
result.relations_applied = relations_applied
```

`PromotionRunResult` gains a `relations_applied: int = 0` field.

## Milestones

1. `PromotionRunResult.relations_applied` field exists.
2. `apply_relation_to_note` exists and writes atomically.
3. `_apply_intra_source_relations` collects promoted-by-queue map and applies
   stored relations after the candidate loop.
4. Idempotency: re-running promotion on the same bundle does not duplicate
   frontmatter relations.
5. Skip conditions (review-routed, rejected, missing file) are handled cleanly.
6. Graph rebuilt for each note that receives a new relation.

## Verification

- Test: two candidates from same queue row both promoted → intra_source_relation
  between them applied to source note frontmatter → graph edge exists.
- Test: source candidate promoted, target candidate review-routed → relation
  skipped, no error.
- Test: run promotion twice on same bundle → frontmatter has exactly one relation
  entry, not two (idempotency).
- Test: `PromotionRunResult.relations_applied` = 1 after successful relation write.
- Integration: `process_promotion_queue` dry-run → no frontmatter writes, no graph
  rebuild.

## Out of scope

- Relations from a promoted note to an existing wiki note NOT from the same bundle.
  That is cross-source linking, addressed by the knowledge maintenance layer.
- Removing or inverting intra-source relations (no automated retraction).
- More than one pass: if both candidates in a relation are from different queue rows
  (different true raws), this code does not apply them. Those are cross-source links.
