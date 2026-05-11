# LLM/VLM Layer Phase 8: Intra-Source Relation Extraction

## Goal

When the LLM processes one source's projection and produces N candidates, it must
also assert typed relations between those N candidates. These intra-source relations
are the first and most reliable source of graph edges: the LLM has seen the full
projection and is the only point in the pipeline capable of reasoning about how the
knowledge units it extracted relate to each other.

## Terminology

- **Intra-source**: within one extraction run — one true raw → one extraction queue
  row → one LLM call → one bundle → N candidates plus M intra-source relations.
- **Intra-candidate**: component links within a single candidate (`components[].links`).
  These already exist and are not changed here.
- **Cross-source**: relations between notes produced from different true raws. Out
  of scope for Phase 8; addressed later by the knowledge maintenance layer.

## Context

The current `mnemos-extraction-v1` schema has `components[].links` to connect
components within one candidate. It also allows an optional `relations` field per
candidate, but this was designed for entity-level hints (e.g. `linked_projects`,
`entities`), not for expressing how one candidate from the same bundle relates to
another.

The bundle-level field for cross-candidate relations does not exist. Nothing in the
pipeline asks the LLM to reason about how its own output candidates connect. As a
result, every promoted wiki note is a graph orphan relative to notes from the same
source.

A book chapter on database indexing might yield:

- `concept/b-tree-index-structure`
- `procedure/create-a-b-tree-index`
- `decision/when-to-use-b-tree-vs-hash-index`
- `incident/full-table-scan-degraded-query-performance`

The LLM, having read the chapter, knows that the procedure `derived_from` the
concept, that the decision `uses_evidence_from` the concept, and that the incident
`resolved_by` the procedure. This is the signal Phase 8 captures.

## Scope

### 1. Schema extension: `intra_source_relations` top-level field

Add an optional top-level field to the `mnemos-extraction-v1` bundle:

```json
{
  "schema_version": "mnemos-extraction-v1",
  "candidates": [...],
  "intra_source_relations": [
    {
      "source_index": 1,
      "target_index": 0,
      "relation_type": "derived_from",
      "rationale": "The procedure directly implements the structure described in the concept."
    }
  ]
}
```

Fields per relation entry:

| Field | Required | Description |
|---|---|---|
| `source_index` | yes | 0-based position of the source candidate in the `candidates` array |
| `target_index` | yes | 0-based position of the target candidate (must differ from `source_index`) |
| `relation_type` | yes | One of the supported relation types |
| `rationale` | no | Short explanation grounded in the source |

`relation_type` must be one of the existing relation vocabulary (from
`graph.py:RELATION_WEIGHTS`): `derived_from`, `refines`, `supersedes`,
`evidence_for`, `depends_on`, `part_of`, `related_to`, `caused_by`, `resolved_by`,
`attempts_to_solve`, `failed_due_to`, `supports_solution`, `similar_to`,
`contrasts_with`, `before`, `after`, `part_of_chain`, `linked_decision`,
`linked_procedure`.

**Why indices, not titles:** `source_index`/`target_index` are 0-based positions in
the `candidates` array the LLM just emitted. They map 1:1 to `candidate_index` in the
`extraction_candidates` table (already UNIQUE-constrained per queue row). This eliminates
the title-collision class of bug: if two candidates happen to have identical or very
similar titles, index-based resolution is still unambiguous. Titles are not used for
relation resolution at any layer.

### 2. LLM prompt extension in `providers.py`

`OllamaLLMProvider.extract_candidates` prompt gains an `INTRA-SOURCE RELATION
RULES` section:

```
INTRA-SOURCE RELATION RULES:
- After listing all candidates, add a top-level "intra_source_relations" array.
- Each entry: source_index (0-based integer index of the source candidate in the
  candidates array you just produced), target_index (0-based integer index of the
  target candidate, must differ from source_index), relation_type (one of:
  derived_from, refines, supersedes, evidence_for, depends_on, part_of, related_to,
  caused_by, resolved_by, attempts_to_solve, failed_due_to, supports_solution,
  similar_to, contrasts_with, before, after, part_of_chain, linked_decision,
  linked_procedure), and optional rationale (short string grounded in the source).
- source_index != target_index (no self-relations).
- Both indices must be valid positions in the candidates array (0 to N-1).
- Only assert relations the source explicitly supports. Do not invent connections.
- If no clear relations exist between candidates, emit an empty array.
```

Fixture LLM provider (`FixtureLLMProvider`) gains a deterministic `intra_source_relations`
entry using `source_index: 1, target_index: 0` when it produces multiple candidates, for
use in tests.

### 3. Validation in `validation.py`

Function: `validate_intra_source_relations(relations, candidate_count)`

Rules:
1. `source_index` and `target_index` must be non-negative integers < `candidate_count`.
2. `source_index != target_index` (no self-loops).
3. `relation_type` must be in the supported vocabulary.
4. Deduplicate on `(source_index, target_index, relation_type)`.
5. Entries failing any rule are dropped; the bundle itself is not rejected.

Returns the cleaned (valid-only) list of `{source_index, target_index, relation_type[, rationale]}` dicts.

`parse_intra_source_relations(raw_output)` computes `candidate_count = len(candidates)`
from the bundle and passes it to `validate_intra_source_relations`.

### 4. Storage: `extraction_candidates` or new table

Two options:

**Option A — Store on the queue row**: add `intra_source_relations_json TEXT` to
the `extraction_queue` table. The relations are bundle-scoped, not candidate-scoped,
so the queue row is the natural owner.

**Option B — New table**: `extraction_intra_relations(id, queue_id, source_title,
target_title, relation_type, rationale, valid)`.

**Decision: Option A** — a JSON column on `extraction_queue` avoids a join and keeps
the bundle data together. Relations are consumed once at promotion time and not
queried individually. Migration 018 (or 020 if 018 is taken by corroboration_count)
adds the column.

Migration:
```sql
ALTER TABLE extraction_queue
ADD COLUMN intra_source_relations_json TEXT;
```

The worker stores `json.dumps(cleaned_relations)` in this column after validation.
If no relations or all invalid: `json.dumps([])`.

### 5. Promotion layer requirement (cross-layer dependency)

Phase 8 terminates at storing validated relations in the extraction_queue row.
Applying them to wiki note frontmatter is the promotion layer's responsibility.

The promotion worker (`promotion/worker.py`) must be amended to:

1. After processing all candidates from one queue row (grouped by `queue_id`):
2. Load `intra_source_relations_json` for that queue row.
3. For each relation entry, resolve titles to note IDs:
   - `source_note_id = type/slug(source_title)` — look up in the promoted notes
     from this run or in the `notes` table.
   - `target_note_id = type/slug(target_title)` — same.
   - If either note was not promoted (review-routed or rejected), skip that relation.
4. For each resolved relation: update the source note's frontmatter to add the
   relation to its `relations: [{target, type}]` list (append, deduplicate).
5. Trigger `rebuild_note_graph` for the updated source note.

This amendment is documented separately in
`ingestion-promotion-plan-intra-source-relations.md`.

## Data flow after Phase 8

```
One true raw
    ↓ west flange projection
TextWindow[] + ArtifactView[]
    ↓ LLM (OllamaLLMProvider / FixtureLLMProvider)
bundle JSON: {
  "candidates": [C0, C1, C2, ...],        ← 0-indexed positions
  "intra_source_relations": [
    {source_index: 1, target_index: 0, relation_type: "derived_from"},
    ...
  ]
}
    ↓ validate_intra_source_relations(relations, candidate_count=N)
      (out-of-range indices, self-loops, unknown types dropped)
    ↓ store on extraction_queue.intra_source_relations_json
    ↓ extraction_candidates stores C0 as candidate_index=0, C1 as candidate_index=1, ...
    ↓ [promotion layer]
C0 promoted → note/slug0  (candidate_index=0)
C1 promoted → note/slug1  (candidate_index=1)
relation: candidate_index 1 → derived_from → candidate_index 0
    → resolve: promoted_by_queue[queue_id][1] = "note/slug1"
               promoted_by_queue[queue_id][0] = "note/slug0"
    ↓ apply_relation_to_note(note/slug1, target="note/slug0", type="derived_from")
    ↓ rebuild_note_graph(note/slug1)
graph_edges: note/slug1 → derived_from → note/slug0 (weight: 0.7)
```

## Milestones

1. `intra_source_relations` field is parsed from LLM bundle output.
2. `validate_intra_source_relations` cleans the list; invalid entries drop without
   rejecting the bundle.
3. Fixture LLM provider emits at least one valid intra_source_relation when it
   produces multiple candidates.
4. Migration adds `intra_source_relations_json` to `extraction_queue`.
5. Worker stores cleaned relations on the queue row after processing.
6. Promotion layer amendment applies relations to frontmatter (tracked separately).

## Verification

- Test: bundle with two candidates and one valid relation → relation stored on
  queue row; both candidates reach `promotion_ready`.
- Test: relation with `source_title` not in bundle candidates → entry dropped;
  bundle still valid.
- Test: self-relation (`source_title == target_title`) → dropped.
- Test: unsupported `relation_type` → dropped.
- Test: bundle with zero intra_source_relations (legitimate) → `[]` stored; no
  error.
- Test: fixture provider produces deterministic intra_source_relations.

## Out of scope

- Relations from candidate to an existing wiki note that was NOT produced by this
  bundle (cross-source). That is the knowledge maintenance layer's responsibility.
- LLM asserting relations to external entities not in the bundle (e.g.,
  `entities: [person/jianyu-long]` — those stay in the per-candidate `entities`
  field).
- Applying relations to frontmatter (promotion layer amendment, tracked separately).
- Bidirectional inference — only the asserted direction is stored; `graph_expand`
  handles reverse traversal at query time.
