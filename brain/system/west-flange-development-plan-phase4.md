# West-Flange Phase 4: Artifact Lane and Visual Context

## Goal

Separate plain text from visual or otherwise outstanding artifacts while keeping
stable links between them. This phase prepares artifacts for a later VLM without
running the VLM.

## Artifact Kinds

Supported v1 artifact kinds:

- `figure`
- `table`
- `equation`
- `code`
- rendered page or slide region, represented by the closest kind above

## Text Lane and Artifact Lane

The projection output has two lanes:

1. **Text lane**: text windows with structural markers and no binary payloads.
2. **Artifact lane**: `ArtifactView` objects with payload bytes or source
   attachment paths, context-window links, source locators, and warnings.

True raw is never mutated. "Removing" an image means excluding binary payload
bytes from the text lane and placing a marker in the in-memory projection when
needed.

## Marker Grammar

Markers are hints for downstream extraction. They are stable within a projection
lease.

```markdown
[FIGURE: fig_003 source=page:3/image:2 needs_visual_pass=true]
[TABLE: tbl_002 source=page:5/table:1 rendered=true needs_visual_pass=false]
[EQUATION: eq_011 source=page:7/eq:3 rendered=true needs_visual_pass=true]
[CODE: code_004 source=slide:9/shape:2 language=python]
```

ID prefixes:

- `fig_`
- `tbl_`
- `eq_`
- `code_`

IDs are stable within a source projection and deterministic from source order.

## Figure Handling

Algorithm:

1. Discover embedded/rendered figures from connector output.
2. Check submitted sidecars before extracting/rendering.
3. If a sidecar matches, set `storage_policy = 'source_attachment'` and use
   `source_attachment_path`.
4. If no sidecar matches, use in-memory extracted/rendered payload bytes and set
   `storage_policy = 'in_memory_extracted'`.
5. Assign stable `figure_id`.
6. Attach text context:
   - explicit label references first
   - positional references second
   - nearest surrounding text last
7. Insert marker according to marker policy.
8. Persist non-content figure metadata only.

## Sidecar Match Algorithm

Match sidecars against embedded figures with a weighted score. Threshold: `>= 0.6`.
Checksum match auto-accepts.

| Signal | Weight | Notes |
|---|---:|---|
| SHA-256 checksum identical to extracted bytes | 1.0 | Auto-accept |
| Filename matches embedded reference | 0.4 | Case-insensitive, strip extension |
| Embedded relationship/source hint matches | 0.5 | DOCX/PPTX relationship targets |
| Dimensions within +/- 2% | 0.2 | Tie-breaker |
| MIME type match | 0.1 | Tie-breaker |
| Connector-specific source hint | 0.3 | e.g. PDF page anchor |

Compute score per sidecar/embedded pair inside the same parent artifact. v1 does
not deduplicate sidecars across different parent artifacts.

## Reference Detection

Explicit label pattern:

```python
LABEL_REF_RE = r"\b(Fig(?:ure)?\.?\s*\d+[a-zA-Z]?(?:[–\-]\d+[a-zA-Z]?)?)\b"
```

Positional pattern:

```python
POSITIONAL_REF_RE = (
    r"\b(see|shown|illustrat\w+|depict\w+|display\w+|as in|refer to)"
    r".{0,40}"
    r"\b(below|above|following|preceding|left|right|next|previous)\b"
)
```

Context policy:

- If explicit labels exist, attach every containing paragraph/window.
- If no label exists, attach strong positional-reference segments associated by
  source order/layout.
- If neither exists, attach nearest preceding and following paragraph/window.

Persisted metadata may include booleans such as `referenced_by_label` and
`has_caption`. It must not persist the matched text or caption text.

## Marker Policy

- Clear label references may be enough; a physical-location marker is still
  allowed as a structural hint.
- No clear reference requires a unique marker at the source location.
- Sidecars with no source position get markers in an attachment appendix segment.
- Native clean tables may appear inline as Markdown tables and still be
  cross-listed in the artifact lane when a rendered view is useful.
- `CODE` markers are optional. Insert only when the code has attached visual
  artifacts or rendered context.

## Figure Metadata

Persist these non-content fields in `artifact_figures`:

- `figure_id`
- `storage_policy`
- `attachment_path`
- `source_page`
- `source_slide`
- `source_index`
- `width_px`
- `height_px`
- `mime_type`
- `byte_size`
- `sha256` for source attachments
- `structural_location`
- `has_caption`
- `referenced_by_label`
- `needs_visual_pass`
- `warnings_json`

Do not persist extracted image bytes, captions, OCR text, nearby paragraphs,
visual descriptions, or semantic labels.

## Payload Caps

Phase 3 owns lease memory budgets. Phase 4 must honor them:

- Artifact over `max_per_figure_bytes` emits metadata and warning, no payload.
- Total lease payload overflow emits per-artifact warning, no payload for overflow
  artifacts.
- Artifact metadata remains available even when payload bytes are skipped.

## Components

```text
src/second_brain/projection/
    figure_context.py
    markers.py
```

Responsibilities:

- `figure_context.py`: sidecar matching, reference scans, nearest-text fallback,
  context-window attachment, payload cap warnings.
- `markers.py`: marker grammar, deterministic ID minting, parser/formatter for
  artifact markers.

## Verification

- Explicitly referenced figures link to all containing context windows.
- Unreferenced figures get source-position markers and nearest-context fallback.
- Matching sidecar bypasses in-memory extraction and sets source-attachment
  metadata.
- Non-figure artifacts emit valid markers and artifact views.
- Native table text is not persisted in metadata.
- Oversized payloads emit warnings without blocking text windows.
