# Promotion Layer Phase 3: Note Renderer

## Goal

Convert a candidate payload and a `PromotionDecision` into a canonical
`NoteRecord` markdown document ready for atomic wiki write.

## Context

The extraction layer produces structured candidate payloads in the
`mnemos-extraction-v1` schema. These payloads are not canonical wiki notes: they
use extraction vocabulary (components, traces, evidence IDs) rather than note
vocabulary (body, steps, failure_modes, chain_id). The note renderer is a
deterministic translation layer between those two vocabularies.

The renderer reuses the existing `NoteRecord` model, note templates, lifecycle
defaults, and slugify helpers. It does not invent new schemas.

## Scope

New module: `src/second_brain/promotion/renderer.py`

The renderer does not write files. It returns a `(note_record, rendered_markdown)`
pair for the wiki writer to consume.

## Field Mapping

### Identity fields

| Candidate field | NoteRecord field | Rule |
|-----------------|-----------------|------|
| `type` | `type` | direct |
| `title` | `title` | direct |
| `title` (slugified) | `id` | `{type}/{slugify(title)}` |

### Content fields

| Candidate field | NoteRecord field | Rule |
|-----------------|-----------------|------|
| `summary` | `summary` | direct |
| `body` | `body` (base) | direct; components appended as sub-sections |
| `components` | `body` (appended) | each component rendered as `### {component_type}\n{text}` |
| `confidence` | `confidence` | direct |

### Provenance fields

| Source | NoteRecord field | Rule |
|--------|-----------------|------|
| `candidate["source_refs"]` | `source_refs` | direct list |
| `candidate_id` (from DB row) | `source_refs` | appended as `candidate:{candidate_id}` |
| `archive_id` (from queue row) | `source_refs` | appended as `archive:{archive_id}` |

### Lifecycle fields

| Source | NoteRecord field | Rule |
|--------|-----------------|------|
| `LIFECYCLE_DEFAULTS[type]` | `lifecycle_state` | from `src/second_brain/ingest.py` |
| current UTC timestamp | `created_at` | ISO-8601 |
| current UTC timestamp | `updated_at` | ISO-8601 |

### Type-specific fields

| Candidate component types | NoteRecord field | Rule |
|--------------------------|-----------------|------|
| `procedure_step` components | `steps` | ordered list; extracted from matching components |
| `failure` components | `failure_modes` | list of failure text values |
| `traces[0].trace_id` (if single trace) | `chain_id` | only when exactly one trace present |

### Decision-specific adjustments

| Decision | Adjustment |
|----------|------------|
| `confirm` | `lifecycle_state` preserved from existing note; body not overwritten |
| `update` | Fields merged: body appended, source_refs unioned, confidence updated if higher, `updated_at` set |
| `new` | Full render from candidate payload |
| `review` | Prepend `## Promotion Review\n\nReason: {reason}\n\n` to body; `lifecycle_state = "review"` |

## Renderer Interface

```python
def render(
    candidate: dict,
    *,
    decision: PromotionDecision,
    candidate_id: int,
    archive_id: str,
    existing_note: NoteRecord | None,
) -> tuple[NoteRecord, str]:
    ...
```

Returns `(note_record, markdown_string)`.
`existing_note` is `None` for `new`; required for `confirm`, `update`, `review`
when routing to an existing path.

## Milestones

1. `renderer.py` module exists with `render()` function.
2. `render()` produces a `NoteRecord` that round-trips through `parser.py`
   without errors.
3. `render()` produces valid markdown for all four decision types.
4. Provenance fields (`candidate:{id}`, `archive:{id}`) appear in `source_refs`.
5. `procedure_step` components populate `steps`.
6. `failure` components populate `failure_modes`.
7. Single-trace candidates populate `chain_id`.
8. `render()` does not write files or the database.

## Verification

- `concept` candidate renders with body containing component sub-sections.
- `procedure` candidate with `procedure_step` components renders `steps` list.
- `incident` candidate with `failure` component renders `failure_modes`.
- Candidate with one trace renders `chain_id` equal to that trace ID.
- Candidate with multiple traces does not set `chain_id`.
- `confirm` render preserves existing note body.
- `update` render merges new source_refs and updates body.
- `review` render prepends review header.
- Rendered markdown parses cleanly through `parser.py` for all note types.
- `brain/wiki/` is unchanged after rendering.

## Out Of Scope

- Writing files (Phase 4).
- Ledger writes (Phase 4).
- Changing `NoteRecord`, `parser.py`, or note templates.
