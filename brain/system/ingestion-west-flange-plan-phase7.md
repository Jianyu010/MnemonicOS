# West-Flange Phase 7: Embedded Image Extraction for Text PDFs

## Problem

The VLM lane has never produced output. `visual_snippet_count = 0` across all
1 300+ processed queue items. Root cause: `ArtifactView` objects are only
emitted for image-only PDFs (pages rendered via `_render_pages_as_figures`).
Text PDFs with embedded XObject images — the common case for scanned textbooks
and figure-heavy documents — produce zero `ArtifactView` objects, so the VLM is
never invoked.

**Evidence:**
- `artifact_figures` table: 0 rows.
- All processed artifact queue items: `visual_snippet_count = 0`.
- `complex-variables` PDF (477 pages, `image_only_pdf = 1`): processed when
  figure extraction was not working; `projection_metadata.figure_count = 0`.
- `principles-of-economics` PDF (836 pages, `image_only_pdf = 0`): has 836
  embedded JPEG images (verified via `page.get_images()`) but `artifact_count = 0`.

**Confirmed working:** `fitz.Document.extract_image(xref)` correctly returns
full JPEG bytes (~200–400 KB per image) for embedded figures in text PDFs. This
does not require OCR or page rendering.

## Design

### Extraction method

Use `fitz.page.get_images(full=True)` to enumerate XObject images per page, then
`doc.extract_image(xref)` to retrieve bytes. This extracts the actual embedded
figures rather than rendering full page screenshots. Advantages:

- Preserves original image quality and color fidelity.
- Produces manageable file sizes (figure images, not full-page bitmaps).
- Works for text PDFs regardless of OCR availability.
- Does not double-extract image-only PDFs (those use `_render_pages_as_figures`).

### Deduplication

The same image xref can appear on multiple pages (e.g., a repeated logo). Track
seen xrefs; extract each xref only once. Record `source_page` as the first page
where the xref appears.

### Filtering

Skip:
- Images with `smask` (transparency-mask-only images that are not figures).
- Images smaller than `min_embedded_image_px` on either axis (default 64 px).
  Catches 1-px rules, bullet glyphs, and sub-pixel decorative elements.

### Cap

Hard cap: `max_embedded_images` images per PDF (default 100). Documents like
`principles-of-economics` have 836 embedded images; processing all at VLM cost
is impractical. The cap takes the first N unique xrefs in page order, giving the
document's opening material the best coverage.

### Scope guard

Run embedded extraction only when:
```
not image_only_pdf and figures_enabled and fitz available
```

Image-only PDFs continue to use `_render_pages_as_figures`. The two paths are
mutually exclusive.

## Implementation

### Phase 7-A: `ConnectorConfig` additions

File: `src/second_brain/connectors/base_connector.py`

Add two fields to `ConnectorConfig`:

```python
max_embedded_images: int = 100
min_embedded_image_px: int = 64
```

### Phase 7-B: `_extract_embedded_images()` in `pdf_connector.py`

New private function:

```python
def _extract_embedded_images(
    path: Path,
    config: ConnectorConfig,
    max_pages: int = _MAX_OCR_PAGES,
) -> tuple[list[FigureMetadata], list[FigurePayload]]:
    figures: list[FigureMetadata] = []
    payloads: list[FigurePayload] = []
    try:
        import fitz

        doc = fitz.open(str(path))
        seen_xrefs: set[int] = set()
        for page_num in range(len(doc)):
            if len(figures) >= config.max_embedded_images:
                break
            page = doc[page_num]
            for img_info in page.get_images(full=True):
                xref = img_info[0]
                smask = img_info[1]
                if xref in seen_xrefs or smask == xref:
                    continue
                seen_xrefs.add(xref)
                extracted = doc.extract_image(xref)
                data: bytes = extracted["image"]
                w = extracted.get("width") or 0
                h = extracted.get("height") or 0
                if w < config.min_embedded_image_px or h < config.min_embedded_image_px:
                    continue
                if config.max_in_memory_figure_bytes and len(data) > config.max_in_memory_figure_bytes:
                    continue
                ext = str(extracted.get("ext") or "png")
                mime = f"image/{ext}" if ext != "jpeg" else "image/jpeg"
                figure_id = f"emb_{len(figures) + 1:04d}"
                figures.append(
                    FigureMetadata(
                        figure_id=figure_id,
                        storage_policy="in_memory_extracted",
                        source_page=page_num + 1,
                        source_index=len(figures) + 1,
                        width_px=w,
                        height_px=h,
                        mime_type=mime,
                        byte_size=len(data),
                        structural_location=f"page:{page_num + 1}:xref:{xref}",
                        needs_visual_pass=True,
                    )
                )
                payloads.append(FigurePayload(figure_id=figure_id, data=data, mime_type=mime))
                if len(figures) >= config.max_embedded_images:
                    break
        doc.close()
    except Exception as exc:
        # Non-fatal: VLM pass degrades gracefully to text-only if extraction fails.
        pass
    return figures, payloads
```

### Phase 7-C: Wire into `PdfConnector.extract()`

In the existing `extract()` method, after `text, warnings = _extract_text_pymupdf(path)`
and the `image_only_pdf` branch, add the embedded extraction path:

```python
# Existing code:
text, warnings = _extract_text_pymupdf(path)
figures: list[FigureMetadata] = []
figure_payloads: list[FigurePayload] = []
image_only_pdf = bool(page_count and figure_count and not text.strip())

if image_only_pdf and config.figures_enabled:
    # existing image-only path unchanged
    ocr_text, ocr_warnings = _extract_text_ocr(path)
    ...
    figures, figure_payloads = _render_pages_as_figures(path, config)

# NEW: embedded image extraction for text PDFs
elif text.strip() and config.figures_enabled and figure_count > 0:
    emb_figures, emb_payloads = _extract_embedded_images(path, config)
    figures.extend(emb_figures)
    figure_payloads.extend(emb_payloads)
    if emb_figures:
        usable = f"{text.strip()}\n\n[EMBEDDED_FIGURES: count={len(emb_figures)} needs_visual_pass=true]"
```

Update `rendered_figure_count` and `needs_visual_pass` to account for embedded
figures:

```python
rendered_figure_count = max(figure_count, len(figures))
```

This is already the pattern used for image-only figures; it continues to work.

### Phase 7-D: Config exposure

File: `src/second_brain/config.py`

Add to `ExtractionConfig` (or equivalent section):

```python
max_embedded_images: int = 100
min_embedded_image_px: int = 64
```

Pass through to `ConnectorConfig` inside `_open_artifact`:

```python
result = connector.extract(
    raw_path,
    config=ConnectorConfig(
        figures_enabled=options.figures_enabled,
        max_embedded_images=options.max_embedded_images,
        min_embedded_image_px=options.min_embedded_image_px,
    ),
)
```

Add to `ProjectionOptions`:

```python
max_embedded_images: int = 100
min_embedded_image_px: int = 64
```

Expose in `memory.toml` under `[extraction]`:

```toml
max_embedded_images = 100
min_embedded_image_px = 64
```

### Phase 7-E: Requeue stale artifact items

Items already `processed` with `visual_snippet_count = 0` and
`needs_visual_pass = 1` must be requeued to benefit from Phase 7.

Query to identify them:

```sql
SELECT eq.id, eq.archive_id
FROM extraction_queue eq
JOIN archive a ON a.id = eq.archive_id
WHERE eq.source_kind = 'artifact'
  AND eq.status = 'processed'
  AND eq.visual_snippet_count = 0
  AND a.needs_visual_pass = 1;
```

Reset them via `reprocess-artifact` (preferred, ensures content_hash refresh)
or direct SQL:

```sql
UPDATE extraction_queue
SET status = 'pending', started_at = NULL, finished_at = NULL
WHERE id IN (<ids from above>);
```

**Affected items (as of 2026-05-17):**
- `artifact/2026-05-14-194600-818552-algebra` (559 figs)
- `artifact/2026-05-14-200615-609717-complex-variables-and-applications` (477 figs, image-only)
- `artifact/2026-05-14-200805-161767-discrete-mathematics-with-applications` (75 figs)
- `artifact/2026-05-14-203857-170988-frank-robert-h-principles-of-economics-5th-edition` (836 figs)
- `artifact/2026-05-14-204616-999788-gasiorowicz-s-quantum-physics` (348 figs)
- `artifact/2026-05-14-205612-494880-introduction-to-smooth-manifolds` (1 fig)
- `artifact/2026-05-15-033048-374439-schiff-s-quantum-mechanics` (563 figs)

### Phase 7-F: Tests

1. **Unit: `_extract_embedded_images` filtering** — mock `fitz` to return images
   of varying sizes; confirm smask and sub-threshold images are skipped,
   deduplication by xref works, cap is honored.

2. **Unit: `PdfConnector.extract()` with embedded images** — use a minimal
   fixture PDF containing one embedded image; confirm connector returns
   `len(figures) == 1`, `len(figure_payloads) == 1`,
   `metadata['needs_visual_pass'] == True`.

3. **Integration: projection → VLM hand-off** — use fixture PDF + `FixtureVLMProvider`;
   confirm `iter_artifacts()` yields one `ArtifactView` and
   `vlm.describe()` produces a `VisualSnippet` with non-empty
   `visual_description`.

4. **Regression: image-only PDF path unchanged** — re-run existing tests for
   `_render_pages_as_figures` path; confirm no interference.

## Invariants Preserved

- West-flange remains pull-based. Image extraction runs inside the projection
  service at extraction time, not during intake.
- `artifact_figures` table remains unused for in-memory extracted figures (same
  as current image-only page renders). Only `source_attachment` figures land in
  the table.
- `ArtifactView` contract is unchanged: embedded images surface through the same
  `payload` field with same `kind = "figure"`.
- Exact-duplicate short-circuit path is unaffected.
- If `fitz` is unavailable or extraction fails, the connector returns normally
  with `figures = []`; VLM is skipped gracefully.

## Known Limitations

- **Cap is arbitrary**: 100 images per document means later figures in long
  textbooks are not VLM-annotated. This is acceptable for an initial pass;
  revisit with a selective page-range or importance-score-based sampler.
- **No persistent storage**: embedded figures are not saved to disk or
  `artifact_figures`. Each reprocessing re-extracts them. Consider adding a
  `storage_policy = "in_memory_extracted_cached"` or persisting to disk for
  large PDFs if latency becomes a concern.
- **Smask detection**: the `smask == xref` heuristic is a best-effort filter.
  Some mask-only images may pass through if `smask` is 0.
- **fitz dependency**: embedded extraction degrades silently if `fitz` is not
  installed (same behavior as existing `_render_pages_as_figures`).

## Success Criteria

- `visual_snippet_count > 0` for at least one reprocessed text-PDF artifact item.
- `extraction_visual_snippets` table has rows after requeue + daemon tick.
- `FixtureVLMProvider` test confirms `ArtifactView` flows through the VLM lane.
- `pytest tests/ -x -q` continues to pass.
