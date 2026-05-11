# West-Flange Phase 1: Intake and Archive

## Goal

Implement true raw artifact intake and operational metadata persistence without
persisting extracted content. This phase establishes the archive substrate that
later identity, projection, and extraction phases build on.

## Scope

Supported source kinds:

- PDF
- HTML or web clip
- DOCX
- PPTX
- Markdown
- plain text
- captured session source references, without full session projection yet

Supported public surfaces:

- `mnemonicos ingest-artifact --file PATH --tag TAG --attachment PATH_OR_DIR`
- `mnemonicos ingest-artifact --attachment-recursive`
- `POST /v1/ingest/artifact`
- `mnemonicos reprocess-artifact --archive-id artifact/...`
- `mnemonicos reprocess-artifact --failed`
- `POST /v1/reprocess/artifact`

## Archive Layout

```text
brain/raw/artifacts/{archive_slug}/
    original.{ext}
    manifest.json
    attachments/
        submitted-sidecar.png
```

`original.{ext}` is the immutable true raw source. Files under `attachments/`
are submitted true raw sidecars. Attachment archive paths must be unique within
the artifact directory even when submitted source paths share the same basename,
so figure rows keep stable path/hash provenance for the exact sidecar bytes they
describe.

## Connector Contract

Connectors parse archived true raw and return temporary output plus operational
metadata. Extracted text and embedded payloads are not persisted.

```python
@dataclass
class ConnectorConfig:
    figures_enabled: bool = True
    persist_extracted_figures: bool = False
    archive_source_attachments: bool = True
    max_in_memory_figure_bytes: int | None = None
    optional_dependencies: dict[str, bool] = field(default_factory=dict)

@dataclass
class ConnectorResult:
    usable_raw_text: str
    metadata: dict[str, object]
    figures: list[FigureMetadata]
    figure_payloads: list[FigurePayload]
    warnings: list[str]
    status: str
    failure_reason: str | None
```

Valid `status` values:

- `accepted`
- `accepted_with_warnings`
- `failed_extraction`
- `manual_fix_needed`

`usable_raw_text` and `figure_payloads` are in memory only.

## Components

```text
src/second_brain/connectors/
    base_connector.py
    connector_registry.py
    passthrough_connector.py
    html_connector.py
    docx_connector.py
    pptx_connector.py
    pdf_connector.py
```

Responsibilities:

- `base_connector.py`: dataclasses, marker helpers, figure metadata, connector
  protocol, connector version constant.
- `connector_registry.py`: extension and MIME routing.
- `passthrough_connector.py`: Markdown and plain text identity projection.
- `html_connector.py`: HTML text projection and linked/submitted image detection.
- `docx_connector.py`: paragraphs, tables, relationship metadata, embedded image
  detection.
- `pptx_connector.py`: slide text, speaker notes where available, tables, shapes,
  embedded image detection.
- `pdf_connector.py`: text extraction where available, page count, image count,
  image-only failure flags.

## Ingest Algorithm

1. Allocate an `archive_id` and archive directory.
2. Copy the submitted file to `original.{ext}`.
3. Compute `raw_sha256` and `byte_size`.
4. Run the connector for the source format.
5. Copy submitted sidecar attachments into collision-safe paths under
   `attachments/`.
6. Persist the `archive` row with `chunk_count = 0`.
7. Persist `artifact_figures` rows for source attachments and non-content
   embedded figure metadata.
8. Write `manifest.json`.
9. Return ingest result.

Phase 1 does not perform cross-artifact identity. Phase 2 inserts the pre-parse
exact duplicate checkpoint before connector execution.

## Failure Policy

Every submission is retained unless the source path itself is invalid.

| Connector result | Archive status | Behavior |
|---|---|---|
| Meaningful usable raw and no major warnings | `accepted` | Archive true raw and metadata |
| Some usable raw with warnings | `accepted_with_warnings` | Archive true raw, warnings, and retry metadata |
| No meaningful usable raw | `failed_extraction` | Archive true raw and mark manual repair/retry |
| Human/agent repair exists | `manual_fix_needed` or repaired status | Keep original true raw and repair metadata |

Failed extraction is not rejected ingestion.

## Existing Migration

`migrations/010_artifact_fields.sql` owns Phase 1 archive artifact fields and
figure metadata. Do not add Phase 2 identity fields to this migration once it may
have been applied locally.

Required fields:

- `archive.artifact_path`
- `archive.source_format`
- `archive.original_filename`
- `archive.raw_sha256`
- `archive.byte_size`
- `archive.connector_name`
- `archive.connector_version`
- `archive.ingest_status`
- `archive.page_count`
- `archive.slide_count`
- `archive.figure_count`
- `archive.table_count`
- `archive.warnings_json`
- `archive.failure_reason`
- `archive.needs_visual_pass`
- `archive.needs_manual_projection`
- `archive.image_only_pdf`
- `artifact_figures` table

## Manifest Schema

`manifest.json` mirrors non-content operational metadata.

```json
{
  "manifest_schema_version": "1",
  "archive_id": "artifact/2026-05-02-sample",
  "submitted_at": "2026-05-02T12:34:56Z",
  "ingest_started_at": "2026-05-02T12:34:57Z",
  "ingest_finished_at": "2026-05-02T12:35:12Z",
  "source_format": "pdf",
  "original_filename": "paper.pdf",
  "artifact_path": "brain/raw/artifacts/2026-05-02-sample/original.pdf",
  "raw_sha256": "ab12...",
  "byte_size": 4823917,
  "connector_name": "pdf_connector",
  "connector_version": "1.0.0",
  "ingest_status": "accepted",
  "failure_reason": null,
  "warnings": [],
  "page_count": 12,
  "slide_count": 0,
  "figure_count": 4,
  "table_count": 1,
  "needs_visual_pass": true,
  "needs_manual_projection": false,
  "image_only_pdf": false,
  "attachments": [],
  "figures": []
}
```

Manifest invariants:

- `manifest_schema_version` changes only when the manifest schema changes.
- `figures[]` mirrors `artifact_figures`.
- `attachments[]` mirrors submitted sidecars.
- Forbidden fields: extracted paragraphs, captions, OCR text, table cells, slide
  text, summaries, semantic labels, VLM descriptions, LLM output.

## Operational Metadata Policy

Allowed:

- IDs, paths, source format, original filename
- raw SHA-256 and byte size
- submitted timestamps when available
- connector name/version
- ingest status, warnings, failure reason
- page, slide, figure, and table counts
- sidecar paths, hashes, byte sizes, MIME types, dimensions
- non-content figure regeneration metadata
- visual/manual projection flags

Forbidden:

- extracted body text
- captions or nearby context
- OCR text
- table cell values
- slide text or speaker notes
- document summaries
- semantic labels or rationales
- visual descriptions

## Reprocess

Reprocess regenerates connector metadata from preserved true raw. It updates the
same archive row, manifest, and figure rows. It never creates archive chunks and
never duplicates true raw.

`--failed` targets `failed_extraction` and `manual_fix_needed` rows.

Phase 2 modifies reprocess behavior for duplicate rows.

## Verification

- Successful PDF/DOCX/PPTX/HTML/plain-text ingest archives true raw and
  operational metadata without persisted extracted content.
- Complete extraction failure records `failed_extraction`, `failure_reason`, and
  `needs_manual_projection`.
- Partial extraction records `accepted_with_warnings`.
- Submitted sidecars are archived as true raw attachments.
- Submitted sidecars with duplicate basenames do not overwrite each other, and
  each `artifact_figures.attachment_path` points to bytes matching that row's
  hash.
- Embedded image copies are not persisted by default.
- Artifact archive rows have `chunk_count = 0`.
- Normal retrieval does not include artifact usable raw.
