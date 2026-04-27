# West-Facing Flange: Multi-Format Raw Intake Layer

## Context

MnemonicOS currently ingests session transcripts as raw markdown. The west-facing
flange extends intake to arbitrary raw materials such as PDFs, HTML/web clips,
DOCX, PPTX, Markdown, and plain text.

The purpose of this flange is not to summarize documents or become a general
document converter. Its job is to preserve submitted true raw artifacts and, when
possible, generate an in-memory "usable raw" stream for a later <=8B extraction
model. That later model will turn usable raw into structured candidates and
linked wiki material.

Usable raw is pipeline data. It is generated during ingest or reprocessing, fed
to the future extraction path, and then discarded.

---

## Design Constraints

- Preserve every submitted true raw artifact, including extraction failures.
- Store accepted and failed artifacts immutably under the existing `brain/raw/`
  vault root.
- Persist operational metadata needed to reproduce, route, audit, retry, repair,
  and warn about extraction.
- Do not persist extracted artifact content: no paragraphs, headings, captions,
  OCR text, slide text, table cells, summaries, semantic labels, VLM
  descriptions, or model interpretations.
- Generate usable raw in memory only; do not store it as a durable artifact and
  do not expose it through normal retrieval.
- Use light structural markers only. Markers are hints for downstream extraction,
  not a rigid schema or validation contract.
- Preserve source-provided companion image files as true raw attachments when
  they are submitted with the artifact.
- Extract embedded/rendered figures in memory for the future VLM path, but do
  not persist those extracted image copies by default.
- Keep embedded figure positions through in-memory placeholders and operational
  metadata that can regenerate the figure payload from true raw.
- Make figure metadata VLM-ready, but do not run VLM in this phase.
- Standalone screenshot intake remains out of scope for v1.
- No compression, summarization, or intentional truncation in the usable raw
  projection.

---

## Architecture Overview

```text
Submitted artifact
    PDF / HTML / DOCX / PPTX / Markdown / plain text

        |
        v

True Raw Store
    brain/raw/artifacts/{archive_id}/
        original.{ext}          # immutable submitted artifact
        manifest.json           # operational metadata only
        attachments/            # optional true-raw companion files submitted with artifact
            figure1.png
            source-image.jpeg

        |
        v

Connector Plugins
    format -> usable raw text + operational metadata + in-memory figure payloads

        |
        v

Usable Raw
    in-memory only
    light structural markers
    figure placeholders at approximate source positions
    no persistence
    no retrieval exposure

        |
        v

Future visual/text extraction path
    optional VLM over in-memory figure payloads / submitted attachments
    then <=8B main extraction LLM
    produces structured candidates / linked wiki material

        |
        v

Existing promotion and review machinery
```

This phase implements the archive, connector, and operational metadata layer. It
does not implement the VLM pass or the <=8B extraction daemon.

---

## Failure Policy

Every submitted artifact is retained.

| Extraction result | Ingest status | Behavior |
|---|---|---|
| Meaningful usable raw produced without major warnings | `accepted` | Archive true raw, write operational metadata, mark eligible for future extraction |
| Some usable raw produced, but with warnings | `accepted_with_warnings` | Archive true raw, write warnings, mark eligible for future extraction with caution |
| No meaningful usable raw produced | `failed_extraction` | Archive true raw, write failure reason, mark for manual repair or retry |
| Human/agent supplies a later repair/projection | `manual_fix_needed` or later repaired state | Keep original true raw and record repair metadata separately |

Failed extraction is not a rejected submission. It is a repairable artifact. A
human or agent should be able to inspect the true raw file, retry after connector
improvements, or manually provide a projection in a later workflow.

---

## Operational Metadata Policy

Persist metadata needed to reproduce, route, audit, retry, repair, and warn about
extraction. Do not persist content extracted from the artifact.

Allowed operational metadata:

- artifact id and archive id
- source format and original filename
- raw path, raw SHA-256, byte size
- submitted file timestamps when available
- connector name and connector version
- extraction started/finished timestamps
- ingest status:
  - `accepted`
  - `accepted_with_warnings`
  - `failed_extraction`
  - `manual_fix_needed`
- page count, slide count, figure count, table count
- submitted companion attachment paths, hashes, byte sizes, MIME types, and
  dimensions
- non-content figure regeneration metadata for embedded/rendered figures
- extraction warnings and failure reason
- retry eligibility and retry count
- flags such as:
  - `needs_visual_pass`
  - `needs_manual_projection`
  - `image_only_pdf`
  - `partial_extraction`

Forbidden persisted metadata:

- extracted paragraphs or body text
- headings or section titles
- captions or nearby textual context
- extracted embedded image bytes or duplicated rendered image files
- OCR text
- table cell values
- slide text or speaker notes
- document summaries
- semantic labels, categories, rationales, or model interpretations
- VLM descriptions or visual interpretations

Rule of thumb:

> Persist metadata that says how the artifact was handled. Do not persist
> metadata that says what the artifact says.

### Manifest and database split

The SQLite archive tables are the queryable operational index. They are the
source of truth for status, retry queues, maintenance filters, and cross-artifact
reports.

`manifest.json` is a human-readable snapshot written beside the true raw
artifact. It should mirror the same non-content operational metadata for local
inspection, manual repair, and disaster recovery if the database has to be
rebuilt. If the DB and manifest disagree, the DB is authoritative while the
daemon is running; maintenance tooling may later verify or repair manifests from
DB state.

Do not put extracted content in either place.

---

## Retrieval Boundary

True raw and usable raw are not normal retrieval payloads.

Normal agentic and human queries should receive sufficient, efficient context
from promoted wiki material or future structured candidates. Artifact records may
be referenced by id/path as provenance, warning surface, or repair work item, but
the raw artifact content and usable raw projection must not be injected into
query-time context.

A future explicit raw-read operation may let an agent ask the user whether to
open or inspect a true raw artifact. That operation is separate from normal
MnemonicOS retrieval.

---

## Soft Usable-Raw Markers

Usable raw uses light structural markup because it helps the future <=8B
extractor without forcing a rigid conversion schema.

Examples:

```markdown
## Page 3

The extracted page text appears here while the pipeline is running.

[FIGURE: fig_003.png page=3 index=2 needs_visual_pass=true]
```

```markdown
## Slide 4

Title and bullet text appear here while the pipeline is running.

[FIGURE: fig_004.png slide=4 index=1 needs_visual_pass=true]
```

Native tables may be projected as markdown tables in memory when the source
format exposes real table structure. The table content is not persisted by the
west flange.

Markers are hints only. Downstream processors may ignore them.

---

## Embedded Visual Preservation

Embedded figures, charts, diagrams, photos, and slide images must not be silently
dropped.

This phase should:

- archive companion image files when they are submitted as part of the raw
  material set, such as a paper PDF plus separately downloaded figure files
- extract embedded or rendered figures from PDFs/DOCX/PPTX in memory for the
  future VLM pass, but not write those extracted copies to disk by default
- assign stable figure ids within the artifact, such as `fig_001`
- place in-memory `[FIGURE: ...]` placeholders near the extracted image's source
  position
- persist non-content figure metadata for later VLM routing and regeneration
- mark visually important artifacts with `needs_visual_pass`
- avoid any visual interpretation in this phase

The promotion layer, not the west flange, decides whether an important extracted
figure should become a durable wiki asset. If a later VLM/LLM pass decides a
figure is worth preserving as part of structured memory, that promoted asset is
saved with the promoted wiki material and linked back to the original true raw.

VLM-ready figure metadata:

| Field | Meaning |
|---|---|
| `figure_id` | Stable id within the artifact, such as `fig_001` |
| `storage_policy` | `in_memory_extracted`, `source_attachment`, or `promoted_later` |
| `attachment_path` | Path only for submitted companion images archived as true raw attachments |
| `source_page` | PDF/page source location when known |
| `source_slide` | PPTX slide source location when known |
| `source_index` | Connector-local ordering on page/slide/document |
| `width_px`, `height_px` | Image dimensions when available |
| `mime_type` | Image MIME type when known |
| `byte_size` | Byte size when available without durable duplication |
| `sha256` | Content hash for source attachments; optional for in-memory extracted figures |
| `structural_location` | Non-content location hint, such as `page:3/image:2` |
| `has_caption` | Boolean only; do not persist caption text |
| `referenced_by_label` | Boolean only when detectable without storing label text |
| `needs_visual_pass` | Whether the later VLM path should inspect this figure |
| `warnings_json` | Extraction warnings for this figure |

For `referenced_by_label`, connectors should scan the in-memory usable raw text
for label-style figure references only. The boolean can be persisted, but the
matched label text and surrounding text must not be persisted.

Example patterns (non-exhaustive — expand during implementation):

```python
# Label-style: "Figure 3", "Fig. 2a", "Figure 3a–c"
LABEL_REF_RE = r"\b(Fig(?:ure)?\.?\s*\d+[a-zA-Z]?(?:[–\-]\d+[a-zA-Z]?)?)\b"

# Positional: "see below", "shown above", "as illustrated in the following"
POSITIONAL_REF_RE = (
    r"\b(see|shown|illustrat\w+|depict\w+|display\w+|as in|refer to)"
    r".{0,40}"
    r"\b(below|above|following|preceding|left|right|next|previous)\b"
)
```

Positional references may influence in-memory placeholder placement and VLM
routing during the same pipeline run. They do not need a separate durable field
in v1 — `referenced_by_label` covers the high-confidence label case.

The later visual path can regenerate embedded figure payloads from true raw,
read submitted companion attachments directly, generate in-memory visual
descriptions, and feed those into the main <=8B extraction LLM.

---

## Components To Build

### New: `src/second_brain/connectors/`

| File | Responsibility |
|---|---|
| `base_connector.py` | Shared models and connector interface |
| `connector_registry.py` | File extension / MIME routing |
| `passthrough_connector.py` | Markdown and plain text usable-raw identity transform |
| `html_connector.py` | HTML/web clip text projection and submitted/linked attachment detection |
| `docx_connector.py` | DOCX paragraphs, tables, relationships, and in-memory embedded image extraction |
| `pptx_connector.py` | PPTX slide text, notes when extractable, tables/shapes, and in-memory embedded image extraction |
| `pdf_connector.py` | PDF text/table extraction where available, in-memory rendered/embedded image extraction, image-only failure flags |

Connector return shape:

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
    usable_raw_text: str              # in memory only
    metadata: dict[str, object]       # operational metadata only
    figures: list[FigureMetadata]     # routing/regeneration refs; no extracted text
    figure_payloads: list[FigurePayload] # in memory only, not persisted
    warnings: list[str]
    status: str                       # accepted / accepted_with_warnings / failed_extraction
    failure_reason: str | None
```

`persist_extracted_figures` defaults to `False`. It exists only as an explicit
escape hatch for experiments or diagnostics; production v1 should avoid durable
duplication of images already contained in true raw.

### Modified: `src/second_brain/ingest.py`

Add artifact ingest without routing through `_archive_session()` unchanged:

1. Allocate an artifact archive id.
2. Copy submitted true raw to `brain/raw/artifacts/{archive_id}/original.{ext}`.
3. Run connector against the archived true raw.
4. Archive submitted companion attachments, if any.
5. Keep extracted embedded/rendered figure payloads in memory only by default.
6. Persist archive row and operational metadata.
7. Persist non-content figure metadata.
8. If connector fails completely, retain true raw and metadata with
   `failed_extraction`; do not enqueue usable raw for future LLM extraction.
9. If connector succeeds partially or fully, keep usable raw in memory only and
   mark the artifact eligible for the future extraction daemon.

### New migration: `migrations/010_artifact_fields.sql`

Add archive artifact fields and a figure metadata table. Suggested fields:

```sql
ALTER TABLE archive ADD COLUMN artifact_path TEXT;
ALTER TABLE archive ADD COLUMN source_format TEXT;
ALTER TABLE archive ADD COLUMN original_filename TEXT;
ALTER TABLE archive ADD COLUMN raw_sha256 TEXT;
ALTER TABLE archive ADD COLUMN byte_size INTEGER;
ALTER TABLE archive ADD COLUMN connector_name TEXT;
ALTER TABLE archive ADD COLUMN connector_version TEXT;
ALTER TABLE archive ADD COLUMN ingest_status TEXT;
ALTER TABLE archive ADD COLUMN page_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN slide_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN figure_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN table_count INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN warnings_json TEXT NOT NULL DEFAULT '[]';
ALTER TABLE archive ADD COLUMN failure_reason TEXT;
ALTER TABLE archive ADD COLUMN needs_visual_pass INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN needs_manual_projection INTEGER NOT NULL DEFAULT 0;
ALTER TABLE archive ADD COLUMN image_only_pdf INTEGER NOT NULL DEFAULT 0;

CREATE TABLE artifact_figures (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    archive_id TEXT NOT NULL REFERENCES archive(id) ON DELETE CASCADE,
    figure_id TEXT NOT NULL,
    storage_policy TEXT NOT NULL DEFAULT 'in_memory_extracted'
        CHECK (storage_policy IN ('in_memory_extracted', 'source_attachment', 'promoted_later')),
                        -- 'in_memory_extracted': embedded, not persisted; daemon re-extracts from true raw
                        -- 'source_attachment':   submitted companion file; attachment_path is set
                        -- 'promoted_later':      set by promotion layer after LLM decides to keep
    attachment_path TEXT,   -- set only for source_attachment; null for in_memory_extracted
    source_page INTEGER,
    source_slide INTEGER,
    source_index INTEGER,
    width_px INTEGER,
    height_px INTEGER,
    mime_type TEXT,
    byte_size INTEGER,
    sha256 TEXT,            -- set for source_attachment; optional for in_memory_extracted
    structural_location TEXT,
    has_caption INTEGER NOT NULL DEFAULT 0,
    referenced_by_label INTEGER NOT NULL DEFAULT 0,
    needs_visual_pass INTEGER NOT NULL DEFAULT 1,
    warnings_json TEXT NOT NULL DEFAULT '[]',
    UNIQUE(archive_id, figure_id)
);
```

### Public intake surface

- CLI: `mnemonicos ingest-artifact --file PATH --tag TAG --attachment PATH`
- HTTP: `POST /v1/ingest/artifact`
- CLI reprocess:
  - `mnemonicos reprocess-artifact --archive-id artifact/...`
  - `mnemonicos reprocess-artifact --failed`
- HTTP reprocess: `POST /v1/reprocess/artifact`

The response should include:

- `archive_id`
- `raw_path`
- `manifest_path`
- `ingest_status`
- `source_format`
- `figure_count`
- `warnings`
- `failure_reason`
- `needs_visual_pass`
- `needs_manual_projection`
- `image_only_pdf`

Reprocessing must update the same artifact archive id, manifest, archive row,
and figure metadata. It must not duplicate true raw or create artifact archive
chunks.

---

## Dependency Posture

Prefer dependency-light extraction in v1. Optional parser libraries may improve
coverage, but missing optional dependencies should not prevent true raw
retention.

Possible optional dependencies:

```text
pdfminer.six       # PDF text extraction
pdfplumber         # PDF native table extraction
pymupdf            # PDF figure/image extraction
python-docx        # DOCX high-level extraction
python-pptx        # PPTX high-level extraction
readability-lxml   # HTML main content extraction
beautifulsoup4     # HTML cleanup
```

If a dependency is missing and extraction cannot proceed, retain the true raw and
record `failed_extraction` or `accepted_with_warnings` as appropriate.

---

## Out Of Scope For This Phase

- Standalone screenshot intake.
- Running VLM or persisting VLM descriptions.
- Running the <=8B LLM extraction daemon.
- Persisting usable raw text or artifact chunks.
- Query-time retrieval of true raw or usable raw content.
- RL or fine-tuning pipeline for the extraction model.

---

## Verification

1. Successful PDF/DOCX/PPTX/HTML/plain-text ingest archives true raw and
   operational metadata without persisting extracted content.
2. Complete extraction failure still archives true raw and records
   `failed_extraction`, `failure_reason`, and `needs_manual_projection`.
3. Partial extraction archives true raw, submitted attachments, warnings, and
   `accepted_with_warnings`.
4. Embedded figures are represented by non-content metadata with stable ids,
   source location, regeneration hints, and visual-pass flags; extracted image
   copies are not persisted by default.
5. Image-heavy PDFs are retained and marked `image_only_pdf` /
   `needs_visual_pass` when appropriate.
6. Normal retrieval does not include true raw or usable raw artifact content.
7. Metadata queries can list failed artifacts, visual-pass candidates, and
   retryable connector failures.
8. Reprocessing can regenerate usable raw from true raw and connector version
   without relying on persisted extracted content.
