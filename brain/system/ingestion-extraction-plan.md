# Ingestion: Extraction Plan (LLM/VLM Layer)

## Purpose

This plan directly follows `brain/system/ingestion-west-flange-plan.md`. The
west flange owns true-raw intake and RAM-only projection. This LLM/VLM layer
starts at the Phase 5 handoff: it consumes `ProjectionLease`, `TextWindow`, and
`ArtifactView`, then produces validated, promotion-ready structured candidates.
It terminates before wiki writes. Canonical wiki mutation remains deterministic
and software-driven in a later promotion layer.

This governing file defines scope, vocabulary, invariants, and phase order. The
implementation details live in the phase files:

1. [Phase 1: Legacy Promotion Removal](ingestion-extraction-plan-phase1.md)
2. [Phase 2: Extraction Queue and Candidate Store](ingestion-extraction-plan-phase2.md)
3. [Phase 3: Projection Consumer](ingestion-extraction-plan-phase3.md)
4. [Phase 4: VLM Visual Snippet Layer](ingestion-extraction-plan-phase4.md)
5. [Phase 5: LLM Candidate Bundle Layer](ingestion-extraction-plan-phase5.md)
6. [Phase 6: Deterministic Candidate Validation](ingestion-extraction-plan-phase6.md)
7. [Phase 7: Daemon and Admin Surfaces](ingestion-extraction-plan-phase7.md)
8. [Phase 8: Intra-Source Relation Extraction](ingestion-extraction-plan-phase8.md)

## Verified Breakpoint

Do not claim west-flange Phases 1-5 are fully complete. Current verification:

- `tests.test_west_flange`: passes.
- `tests.test_session_capture`: passes.
- `tests.test_extraction_layer`: passes.
- Artifact intake/projection is substantially implemented: true raw archive,
  duplicate labels, projection leases, text windows, artifact lane, sidecar
  attachments, payload caps, and retrieval boundary.
- Phase 5's fixture/test matrix is not fully materialized as written. The repo
  has consolidated coverage in `tests/test_west_flange.py`, not the full
  `tests/fixtures/west_flange/` and `tests/west_flange/*` tree documented in
  the west-flange phase file.
- The old promotion runtime has been removed from active code. The new
  extraction queue, candidate store, fixture VLM/LLM providers, projection
  consumer, validation gate, CLI/API/admin surfaces, and daemon job are now the
  only implemented west-to-candidate path.

The implemented west-flange breakpoint is sufficient to run this layer over
artifact, archived session, and episode projections. The west-flange Phase 5
fixture matrix is still not fully materialized, so format expansion should add
fixtures at the projection boundary before relying on a new format in model
extraction.

## Boundary

This layer is an ingestion-side compiler. It is not a north-flange query path
and it is not the deterministic wiki writer.

Inputs:

- `SourceRef`
- `ProjectionLease`
- `TextWindow`
- `ArtifactView`
- duplicate-state and projection metadata from the west flange

Outputs:

- extraction queue records
- visual snippet records
- structured candidate records
- validation status and validation errors
- promotion-ready candidate records

Out of scope:

- direct wiki writes
- query-time retrieval
- north-flange task context resolution
- raw file parsing outside west-flange projection
- replacing deterministic promotion policy
- reinforcement learning or fine-tuning pipelines

## Extraction Philosophy

The LLM is not a summarizer. Every source contains a specific information shape —
document sections, procedure steps, causal chains, experimental results, failure
modes, decisions with rationale. The LLM must preserve that shape, not collapse it
into prose.

Concretely:

- `summary` is an index hint. It is not the payload.
- `body` is a faithful, dense rendering of the source content.
- `components` are the loss-minimizing units: each is a distinct, source-grounded
  atom of knowledge with its own type, text, and evidence binding.
- The number of candidates from one source is bounded only by what the source
  actually contains, not by an arbitrary limit. A book chapter may yield a dozen
  candidates; a short session note may yield one.
- The number of components within a candidate is bounded by the source's
  structural complexity. A multi-step procedure produces one component per step,
  not one component for the whole procedure.
- `intra_source_relations` (Phase 8) are links the LLM asserts between the
  candidates it produces from the same source. They are structurally grounded in
  the source's own organization — not invented.

The VLM applies the same principle: its output is a description of what the image
contains, not a paraphrase. Missing or unrecognizable visual content produces a
warning, not an invented description.

## Non-Negotiable Invariants

- The west flange remains pull-based and does not call VLMs or LLMs.
- This layer consumes west-flange RAM projections; it does not persist usable raw
  text as west-flange metadata.
- VLM descriptions and LLM interpretations may be persisted only as
  extraction-layer records.
- No code in this layer writes or mutates `brain/wiki/`.
- A candidate can become `promotion_ready` only after deterministic schema and
  provenance validation.
- Normal north-flange retrieval does not call the extraction daemon.
- East retrieval reads canonical notes and indexes; it does not read usable raw
  or unvalidated extraction candidates as normal memory.

## High-Level Flow

```text
West-flange source
    |
    v
ProjectionLease + TextWindow + ArtifactView
    |
    v
Phase 2: extraction_queue work item
    |
    v
Phase 3: source-agnostic projection consumer
    |
    v
Phase 4: VLM visual snippets
    |
    v
Phase 5: LLM candidate bundle
    |
    v
Phase 6: deterministic validation
    |
    v
promotion_ready candidates
    |
    v
STOP: later deterministic wiki promotion layer
```

## System Position

- **North flange**: agent/human/API callers. It may expose explicit ingestion or
  admin controls, but normal task context flows through east retrieval.
- **West flange**: true raw intake and projection trimming. This layer starts
  after west returns a projection lease.
- **South flange**: storage and index substrate. This layer initially uses
  SQLite tables for queue, snippets, and candidates.
- **East flange**: retrieval and context delivery. It remains decoupled from
  extraction processing.

## Acceptance Criteria

The LLM/VLM layer is complete when:

- legacy promotion runtime is gone;
- west-flange projection is the only extraction input;
- VLM and LLM providers are swappable;
- candidates are persisted with provenance, validation status, model metadata,
  and errors;
- valid candidates can reach `promotion_ready`;
- no code in this layer writes `brain/wiki/`;
- east retrieval and north query/runtime flows remain decoupled from extraction
  processing.
