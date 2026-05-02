# West-Flange Development Plan

## Purpose

The west flange is MnemonicOS's raw-material boundary. It accepts externally
supplied material, preserves it as true raw, and regenerates temporary usable raw
for later extraction.

This governing file defines scope, vocabulary, invariants, and phase order. The
implementation details live in the phase files:

1. [Phase 1: Intake and Archive](west-flange-development-plan-phase1.md)
2. [Phase 2: Identity and Redundancy](west-flange-development-plan-phase2.md)
3. [Phase 3: Projection Lease API](west-flange-development-plan-phase3.md)
4. [Phase 4: Artifact Lane and Visual Context](west-flange-development-plan-phase4.md)
5. [Phase 5: Extraction Handoff and Verification](west-flange-development-plan-phase5.md)

## Glossary

- **MnemonicOS**: local-first memory runtime that owns the true-raw archive,
  projection, promotion, retrieval, and review lifecycle.
- **West flange**: the MnemonicOS boundary for true raw intake and MnemonicOS-owned
  usable-raw projection.
- **True raw**: immutable submitted source material, such as a PDF, DOCX, PPTX,
  HTML file, Markdown file, plain text file, captured agent episode, or submitted
  sidecar attachment.
- **Usable raw**: temporary, in-memory projection of true raw for extraction. It
  may contain text windows, artifact markers, and in-memory payload references,
  but it is not persisted and is not normal retrieval context.
- **Artifact lane**: in-memory artifact views for figures, tables, equations,
  code-with-visuals, rendered regions, and sidecar attachments.
- **Text lane**: in-memory text windows with structural markers but no binary
  payloads.
- **Projection lease**: time-limited handle used by extraction code to pull text
  windows and artifact views from the west flange.
- **VLM**: visual-language model used later to describe image-like artifacts.
- **LLM**: language model used later to extract structured memory candidates from
  text plus temporary visual context.
- **LSH**: locality-sensitive hashing, used to find near-duplicate artifacts
  without scanning the whole archive.
- **TTL**: time to live for an open projection lease.
- **OCR**: optical character recognition. OCR output is extracted text and must
  not be persisted by the west flange.
- **SHA-256**: cryptographic hash used for byte identity checks.
- **PDF**: Portable Document Format.
- **HTML**: HyperText Markup Language.
- **DOCX**: Microsoft Word Open XML document format.
- **PPTX**: Microsoft PowerPoint Open XML presentation format.
- **JSON**: JavaScript Object Notation.
- **MIME type**: media type string such as `image/png` or `text/html`.
- **SQL**: Structured Query Language.
- **SQLite**: local embedded SQL database used by MnemonicOS.
- **MB / GB**: megabyte / gigabyte.
- **UUID**: universally unique identifier.
- **TLS**: Transport Layer Security.
- **API**: application programming interface.
- **CLI**: command-line interface.
- **HTTP API**: future process boundary for projection transport. Phase 3 ships an
  in-process Python API only.
- **v1 / v2**: version 1 / version 2 of the west-flange design or transport.

## Boundary

The west flange has two sublayers:

1. **True raw intake**: accept submitted material, preserve it under `brain/raw/`,
   archive submitted sidecars, and persist operational metadata.
2. **Usable raw projection**: regenerate an in-memory extraction-ready view from
   true raw.

The west flange does not summarize, classify, or interpret source content. It
normalizes source shape, preserves provenance, exposes structural markers, and
supplies artifact payloads to later extraction code.

## Non-Negotiable Invariants

- Preserve every submitted true raw artifact, including extraction failures.
- True raw files are immutable witness material.
- Persist operational metadata only: paths, hashes, byte sizes, connector
  versions, status, counts, duplicate state, warnings, and non-content source
  locators.
- Never persist extracted paragraphs, headings, captions, OCR text, slide text,
  table cell values, summaries, semantic labels, visual descriptions, or model
  interpretations in west-flange metadata.
- Usable raw is in memory only. It is not written as archive chunks and is not
  exposed through normal retrieval.
- Submitted sidecar attachments are true raw attachments. Embedded/rendered
  figures extracted from source files are in-memory payloads by default.
- The west flange is pull-based. It does not call the VLM or LLM.
- Only exact byte duplicates short-circuit foreground processing. Canonical,
  possible, and overlap-related duplicates still project so downstream promotion
  can decide whether repetition matters.

## High-Level Flow

```text
Submitted source
    |
    v
Phase 1: true raw archive + connector metadata
    |
    v
Phase 2 checkpoint 0: pre-parse exact byte identity
    | hit: record duplicate submission, skip connector/projection
    | miss
    v
Connector parse
    |
    v
Phase 2 checkpoint 4: canonical / LSH identity labels
    | labels only; projection continues
    v
Phase 3: projection lease + text windows
    |
    v
Phase 4: artifact lane + visual context markers
    |
    v
Phase 5: later VLM/LLM extraction handoff
    |
    v
Structured candidates -> promotion/review machinery
```

## Phase Dependencies

- Phase 1 can be implemented and tested independently.
- Phase 2 depends on Phase 1 archive rows and `raw_sha256` / `byte_size`.
- Phase 3 depends on Phase 1 connectors and can initially run without Phase 2
  canonical identity, but final Phase 3 lease metadata must expose Phase 2
  duplicate state.
- Phase 4 depends on Phase 3 artifact views and marker stability.
- Phase 5 depends on Phases 1-4 and defines downstream handoff, fixtures, and
  verification.

## Out Of Scope

- Running VLMs or LLMs inside the west flange.
- Persisting VLM descriptions or LLM interpretations as west-flange output.
- Persisting usable raw text or artifact payload bytes.
- Query-time retrieval of true raw or usable raw content.
- Standalone screenshot intake in v1.
- HTTP projection transport in v1.
- Reinforcement learning or fine-tuning pipelines.

## Readiness

The design is ready to implement once the five phase files are treated as the
source of truth. Each phase contains concrete modules, schema changes, algorithms,
API shapes, and verification requirements.
