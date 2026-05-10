# West-Flange Phase 5: Extraction Handoff and Verification

## Goal

Define how the later VLM/LLM extraction layer consumes west-flange output, and
lock the end-to-end verification plan. This phase does not implement the VLM or
LLM.

## Handoff Contract

The extraction layer consumes a `ProjectionLease`.

Flow:

1. Open a projection lease for an artifact or raw episode.
2. Inspect `duplicate_state`.
3. Send `ArtifactView` items to the VLM when payload or source attachment is
   available.
4. Convert VLM outputs into temporary visual snippets keyed by `artifact_id` and
   marker.
5. Stream `TextWindow` items plus matching visual snippets to the LLM.
6. Persist only structured candidates, promotion decisions, review items, and
   allowed operational metadata.
7. Close the projection lease.

West-flange output ends at text windows, artifact views, source locators, and
operational metadata. VLM descriptions and LLM interpretations belong to the
later extraction/promotion layer.

## Duplicate-State Handoff

- `exact_duplicate`: no new extraction. Reuse canonical prior outputs when
  available.
- `canonical_duplicate`: full projection is available. Downstream decides whether
  repetition strengthens an existing claim or is dropped.
- `possible_duplicate`: full projection is available and should create review
  surface when confidence is not enough to merge automatically.
- `overlap_related`: full projection is available and unique delta content must
  be preserved.
- `unique`: normal extraction.

## Retrieval Boundary

True raw and usable raw are not normal retrieval payloads.

Normal retrieval should use promoted wiki material or future structured
candidates. Artifact records may appear as provenance, warning surfaces, or
repair work items, but raw content and usable raw projections must not be injected
into query-time context.

An explicit future raw-read operation may inspect true raw with user intent. That
operation is separate from normal retrieval.

## Dependency Posture

Prefer dependency-light extraction. Missing optional parser dependencies must not
block true raw retention.

Optional parser dependencies:

- `pdfminer.six` for PDF text
- `pdfplumber` for PDF native tables
- `pymupdf` for PDF images/rendering
- `python-docx` for DOCX
- `python-pptx` for PPTX
- `readability-lxml` for HTML main content
- `beautifulsoup4` for HTML cleanup

## Verification Matrix

Each item maps to one or more tests.

1. Successful PDF/DOCX/PPTX/HTML/plain-text ingest archives true raw and
   operational metadata without persisted extracted content.
2. Complete extraction failure records `failed_extraction`, `failure_reason`, and
   `needs_manual_projection`.
3. Partial extraction records `accepted_with_warnings`.
4. Embedded figure metadata has stable IDs, source locations, regeneration hints,
   and visual-pass flags.
5. Image-heavy PDFs are retained and marked `image_only_pdf` /
   `needs_visual_pass`.
6. Normal retrieval does not include true raw or usable raw.
7. Metadata queries list failed artifacts, visual-pass candidates, and retryable
   connector failures.
8. Reprocess regenerates usable raw from true raw without persisted extracted
   content.
9. Byte-identical re-ingest records exact duplicate and skips connector/projection.
10. Canonically equivalent re-ingest records canonical duplicate and still
    projects.
11. Overlapping chat histories record overlap-related and preserve unique delta.
12. Cumulative captured-session snapshots project into delta-only windows.
13. Explicitly referenced figures attach all containing context windows.
14. Unreferenced figures get unique markers and nearest-context fallback.
15. Matching sidecar bypasses embedded extraction.
16. Tables, equations, and code-with-visuals emit stable markers and artifact
    views.
17. Canonical, possible, and overlap-related leases project fully; only exact
    duplicate leases are thin redirects.
18. Reprocess redirects duplicate rows according to Phase 2 policy.
19. Reprocess does not rerun Stage 0 and preserves duplicate state unless
    maintenance recomputes fingerprint versions.
20. Identity buckets are written only for `unique`, `possible_duplicate`, and
    `overlap_related`.
21. Exact duplicate v1 policy stores submission row only and no duplicate bytes.
22. Concurrent identical ingests resolve deterministically through the unique
    raw identity index.
23. Sidecar bytes are not deduplicated across parent artifacts in v1.
24. Payload over per-artifact cap yields metadata and warning, no payload bytes.
25. Total lease payload cap skips overflow payloads but still yields text windows.

## Test Fixtures

Fixtures live under `tests/fixtures/west_flange/` and are committed. Do not
regenerate them at test time.

```text
tests/fixtures/west_flange/
    pdf/
        sample.pdf
        sample_reexported.pdf
        sample_copy.pdf
        image_only_scan.pdf
        corrupt.pdf
    pdf_with_sidecar/
        paper.pdf
        fig1.png
        fig2.png
    docx/
        sample.docx
    pptx/
        sample.pptx
    html/
        sample.html
        linked_images/
            img1.png
            img2.jpg
    markdown/
        sample.md
    sessions/
        cumulative_prefix/
            events.jsonl
        overlap_related/
            events.jsonl
    snapshots/
        sample_pdf__archive_row.json
        sample_pdf__manifest.json
        sample_pdf__artifact_figures.jsonl
```

## Test Modules

```text
tests/west_flange/
    test_pdf_connector.py
    test_docx_connector.py
    test_pptx_connector.py
    test_html_connector.py
    test_passthrough_connector.py
    test_session_connector.py
    test_within_source_dedup.py
    test_identity_index.py
    test_figure_context.py
    test_marker_grammar.py
    test_chunker.py
    test_ingest_failure.py
    test_ingest_concurrency.py
    test_payload_caps.py
    test_sidecar_no_cross_parent_dedup.py
    test_projection_lease.py
    test_reprocess_redundancy.py
    test_manifest_schema.py
    test_retrieval_boundary.py
    test_metadata_queries.py
    test_reprocess_regenerates.py
```

## Snapshot Policy

Golden files under `snapshots/` are committed. Connector or schema changes that
change snapshots must update code and snapshots in the same pull request.

Review snapshot diffs for forbidden persisted content:

- extracted text
- captions
- OCR output
- table cell values
- slide text
- VLM descriptions
- LLM interpretations

## Implementation Readiness Checklist

- Phase docs are the source of truth.
- No phase requires persisted usable raw.
- No phase requires VLM/LLM execution inside the west flange.
- Every persisted field is operational metadata.
- Every abbreviation used by phase docs is defined in the governing overview.
- Every stage has a verification target.
