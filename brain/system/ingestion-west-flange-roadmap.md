# Ingestion: West-Flange Roadmap

## Purpose

This roadmap records how far implementation has marched against
`ingestion-west-flange-plan.md`, where the implementation diverges from the
original plan, and what remains before the west flange can be called fully
complete.

Current conclusion: the west flange is sufficiently complete for the LLM/VLM
extraction layer. The remaining Phase 5 gaps are verification breadth and fixture
coverage, not a blocker in the `ProjectionLease`, `TextWindow`, or
`ArtifactView` handoff contract.

Phase 6 (capture-to-promotion push pipeline) is implemented. The
`ingest_artifact()`, `reprocess_artifact()`, `ingest_session()`, and
`end_episode()` paths call `enqueue_extraction()` inline and trigger processing
when `auto_trigger_pipeline` is enabled. The daemon's scheduled extraction and
promotion jobs are now classified as maintenance/cleanup, not the primary
processing path.

Phase 7 (embedded image extraction for text PDFs) is **implemented** (2026-05-17).
The VLM lane was producing zero output because embedded XObject images in
text-based PDFs were never extracted. Phase 7 adds `_extract_embedded_images()`
via `fitz.page.get_images()` + `doc.extract_image(xref)` for the non-image-only
branch of `PdfConnector.extract()`. Key config: `max_embedded_images = 100`,
`min_embedded_image_px = 64` (both in `memory.toml` and threaded through
`ExtractionConfig` → `ProjectionOptions` → `ConnectorConfig`). 7 stale artifact
PDFs requeued (`status` reset to `pending`). All 27 west-flange + extraction
tests pass. See `ingestion-west-flange-plan-phase7.md`.

## Current Implementation State

Implemented surfaces:

- `ingest-artifact` and `/v1/ingest/artifact`
- `reprocess-artifact` and `/v1/reprocess/artifact`
- raw episode capture through `session_capture.py`
- true-raw archive layout under `brain/raw/`
- operational archive metadata in SQLite
- duplicate-state metadata
- in-memory projection leases
- text windows
- artifact views
- sidecar attachment handling
- payload caps
- retrieval boundary tests

Current test evidence:

- `tests.test_west_flange` passes.
- `tests.test_session_capture` passes.
- `tests.test_extraction_layer` passes against the west-flange handoff.
- Full repository test suite passes: 198 tests.

## Phase 1: Intake and Archive

Status: substantially implemented.

What is done:

- True raw artifact files are archived under `brain/raw/artifacts/`.
- `manifest.json` is written with operational metadata.
- Source attachments can be archived as sidecars.
- Artifact rows carry source format, file identity, connector status, counts,
  warnings, and failure flags.
- Connector modules exist for passthrough, HTML, DOCX, PPTX, and PDF sources.
- Reprocess flows regenerate usable raw from preserved true raw.
- Extracted content is not persisted as west-flange metadata.

Divergences from plan:

- The implemented tests are consolidated mainly in `tests/test_west_flange.py`
  instead of the planned per-format tree under `tests/west_flange/`.
- Fixture coverage is lighter than the planned `tests/fixtures/west_flange/`
  matrix.

Impact:

- The implemented substrate is usable by downstream extraction.
- Format-specific regressions may be harder to localize until the planned fixture
  tree is expanded.

Conclusion:

- Phase 1 is complete enough for the current architecture and for LLM/VLM layer
  consumption.

## Phase 2: Identity and Redundancy

Status: substantially implemented.

What is done:

- `duplicate_state` exists on archive rows.
- Exact byte duplicates are detected before expensive processing.
- Exact duplicates are represented as duplicate submission rows and redirect to
  canonical archive identity.
- Canonical duplicate, possible duplicate, and overlap-related labels are
  represented.
- Identity bucket storage exists for downstream lookup.
- Projection behavior honors duplicate state: exact duplicates are thin
  redirects; non-exact duplicate states remain projectable.

Divergences from plan:

- The original plan describes a broad verification matrix for canonical,
  possible, overlap-related, reprocess, and concurrency cases. The repo has
  meaningful but more consolidated coverage.
- The plan describes the intended identity algorithm in more detail than the
  current tests prove across real-world document families.

Impact:

- The identity contract exposed to downstream extraction is usable:
  `duplicate_state` and `canonical_source_ref` are available.
- Further work should focus on more realistic duplicate fixtures and stress
  tests, not on changing the handoff shape.

Conclusion:

- Phase 2 is good enough for LLM/VLM queue policy. The extraction layer can skip
  exact duplicates and process non-exact duplicate states without special source
  logic.

## Phase 3: Projection Lease API

Status: implemented for current consumers.

What is done:

- `ProjectionService` exposes `open_projection`, `iter_text_windows`,
  `iter_artifacts`, and `close_projection`.
- `SourceRef`, `ProjectionLease`, `TextWindow`, `ArtifactView`, `SourceSpan`,
  and `ArtifactMarker` exist as explicit dataclasses.
- Artifact projection regenerates usable raw in memory from true raw.
- Episode projection assembles captured events into in-memory text windows.
- Archived session projection is supported for extraction queue sources.
- Exact duplicates return thin redirect leases.
- Text windows are not persisted as archive chunks.

Divergences from plan:

- HTTP projection transport remains deferred, as planned for v2.
- Lease eviction and memory-budget behavior are implemented in a pragmatic
  in-process form rather than as a full service lifecycle.
- Session projection support is currently enough for archived session text and
  raw episodes; richer sidecar-aware episode assembly remains a future expansion.

Impact:

- The LLM/VLM layer has the stable pull-based API it needs.
- More elaborate lease lifecycle behavior can be added without changing the
  extraction layer if the API remains stable.

Conclusion:

- Phase 3 is complete enough and is now the central west-to-extraction contract.

## Phase 4: Artifact Lane and Visual Context

Status: implemented for current figure/sidecar handoff.

What is done:

- `ArtifactView` separates visual/binary payloads from text windows.
- Artifact markers are emitted with stable marker text.
- Source-attachment artifacts can pass through the artifact lane.
- Context-window attachment exists.
- Payload caps produce warnings instead of blocking text streaming.
- VLM descriptions are not persisted in west-flange metadata.

Divergences from plan:

- The phase plan lists tables, equations, code-with-visuals, rendered regions,
  and richer visual-context fixtures. Current implementation is strongest for
  figures and sidecar attachments.
- The planned fixture matrix for explicit labels, unreferenced figures, native
  tables, equations, and code visuals is not fully materialized.

Impact:

- The current artifact lane is enough for the VLM snippet layer to consume
  `ArtifactView` objects.
- More artifact kinds can be added incrementally as `ArtifactView.kind` variants
  and tests without changing the extraction queue or candidate schema.

Conclusion:

- Phase 4 is complete enough for VLM/LLM extraction, with known fixture and
  artifact-kind expansion left.

## Phase 5: Extraction Handoff and Verification

Status: handoff contract is active; verification matrix is incomplete.

What is done:

- The extraction layer consumes `ProjectionLease`, `TextWindow`, and
  `ArtifactView`.
- Exact duplicate handoff is honored by the extraction worker.
- VLM outputs are stored as extraction-layer visual snippets, not west-flange
  metadata.
- LLM outputs are stored as extraction candidates, not west-flange metadata.
- The retrieval boundary remains intact: normal retrieval does not consume true
  raw or usable raw projections.

What is not finished:

- The planned `tests/fixtures/west_flange/` corpus is not fully committed.
- The planned `tests/west_flange/*` module split is not fully materialized.
- Format-by-format verification for PDF, DOCX, PPTX, HTML, image-only scans,
  corrupt files, sidecar matching, tables, equations, and code visuals needs
  broader coverage.
- Golden snapshot policy is documented but not fully exercised as a committed
  snapshot corpus.
- Optional parser dependency behavior is not exhaustively tested across every
  planned format.

Impact:

- These gaps do not block the LLM/VLM layer because the core handoff is already
  implemented and tested at the API boundary.
- The main risk is format-specific quality, not architectural coupling.
- New source formats should add west-flange fixtures before relying on them in
  model extraction.

Conclusion:

- Phase 5 is not fully complete as originally written, but it successfully
  establishes the handoff needed by the LLM/VLM layer. Continue VLM/LLM and
  promotion-layer work now; expand Phase 5 fixture breadth in parallel as new
  formats matter.

## Phase 6: Capture-to-Promotion Push Pipeline

Status: **complete**.

**Architectural principle:** every intake path immediately triggers the full
extraction + promotion pipeline. The daemon's scheduled extraction and promotion
jobs are maintenance/cleanup (retry errors, unstick stale rows) — they are not
the primary processing path and cannot scan true-raw for unprocessed items.

Implemented pipeline:

```
capture end / ingest-artifact / ingest-session / reprocess-artifact
    → raw material in true-raw archive
    → enqueue_extraction() inline (with content_hash)
    → trigger_pipeline(): daemon wake signal → fallback inline
    → LLM/VLM extraction → candidates → promotion → wiki
    (daemon tick: retry errors only + stale episode expiry)
```

What is done:

- All four intake/mutation paths fully wired:
  - `ingest_artifact()`: enqueue + `trigger_pipeline()`, `content_hash=raw_hash`
  - `reprocess_artifact()`: re-enqueue + `trigger_pipeline()` after repaired
    archive/manifest rewrite, `content_hash=raw_hash`
  - `ingest_session()`: enqueue + `trigger_pipeline()`, `content_hash=sha256(content)`
  - `end_episode()`: enqueue + `trigger_pipeline()`, `content_hash=_sha256_path(events_path)`
- `pipeline.py` (new): `trigger_pipeline()` fires daemon wake signal; falls back
  to inline `process_extraction_queue` + `process_promotion_queue` if unreachable.
- `IngestConfig.auto_trigger_pipeline = True` guards trigger calls; test configs
  set `false` to avoid interfering with explicit test control.
- **Gap 1 + 3 closed**: `expire_stale_episodes` daemon job (hourly, `"0 * * * *"`)
  auto-closes orphaned `active` episodes older than `stale_episode_threshold_hours`
  (default: 2h) by calling `end_episode(status='partial')`, triggering full pipeline.
- **Gap 2 closed**: `extraction_queue.content_hash TEXT` column (migration `017`);
  ON CONFLICT resets status to `pending` when hash changes, enabling re-processing
  of updated episodes.
- **Attachment provenance patch closed**: submitted sidecars are archived under
  collision-safe filenames so duplicate basenames cannot overwrite earlier
  attachments or invalidate figure hash/path provenance.
- `DaemonConfig.stale_episode_threshold_hours` config field added.

## Remaining West-Flange Work

- Build the planned fixture corpus under `tests/fixtures/west_flange/`.
- Split consolidated coverage into targeted `tests/west_flange/*` modules when
  test volume grows.
- Add golden snapshots for manifests and non-content metadata.
- Broaden real-format tests for PDF/DOCX/PPTX/HTML and image-heavy sources.
- Deepen sidecar matching tests.
- Add richer table/equation/code artifact-lane coverage.
- Stress test duplicate identity with larger and more realistic corpora.

## Architectural Conclusion

The west flange is conceptually decoupled and practically usable. It owns true
raw intake and RAM-only projection. It does not call VLMs or LLMs, and it does
not write wiki. Phase 6 establishes the push model: every intake path triggers
its own extraction and promotion immediately, without relying on the scheduled
daemon tick. The daemon becomes a maintenance layer — retrying errors, clearing
stale states, running wiki hygiene — not the primary processor. It cannot scan
true-raw for unprocessed items; the push enqueue at intake is the only injection
point into the pipeline. The remaining work after Phase 6 is verification depth
and source-format coverage.
