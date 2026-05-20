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
9. [Phase 9: Retrieval Attribution in Session Extraction](ingestion-extraction-roadmap.md#phase-9-retrieval-attribution-in-session-extraction)
10. [Phase 10: Confidence Signal Clarification and Context Injection Removal](ingestion-extraction-plan-phase10.md)
11. [Phase 11: Expected-Time Queue Scheduling](ingestion-extraction-plan-phase11.md)
12. [Phase 12: Dual-Lane Concurrent Extraction](ingestion-extraction-plan-phase12.md)

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
- Extraction queue scheduling may reorder pending rows by coarse expected
  processing time. It must preserve FIFO order within the same lane and must not
  require opening a west-flange projection just to choose the next row.
- When dual-lane extraction is enabled, at most two extraction subprocesses run
  concurrently: one from the quick lane and one from the slow lane. Both items
  must be marked `processing` before either subprocess is spawned.

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

## Retrieval Attribution in Session Extraction

### Background

Agent sessions may contain retrieved MnemonicOS notes embedded as context before
the agent responds. The `mnemonic_context.sh` hook injects this as a
`## Memory Context` block at the head of the prompt, including note titles, types,
summaries, freshness flags, and note IDs under `current_hits:`, `stale_hits:`, and
`contested_hits:`. This block is part of the raw session text and is therefore
present in the text windows seen by the extraction LLM.

Without explicit instruction, the LLM may re-extract retrieved note content as new
candidates, producing redundant wiki notes, or may silently ignore it. Both
outcomes lose useful signal. The correct behavior is to recognize the block and
emit `retrieval_attribution` metadata that the promotion and maintenance layers can
use to derive passive feedback for the retrieved notes.

### `retrieval_attribution` Field

Each candidate in `mnemos-extraction-v1` may carry an optional top-level field:

```json
"retrieval_attribution": [
  {
    "note_id": "procedure/use-run-python-after-write-file",
    "evidence_type": "semantic_reliance",
    "confidence": 0.8
  }
]
```

Rules:
- Optional. Omit entirely if no retrieval context is identifiable.
- `note_id` must be a string matching a `current_hits:`, `stale_hits:`, or
  `contested_hits:` entry from the `## Memory Context` block in the session.
- `evidence_type` must be one of:
  - `paraphrase` — agent restated retrieved note content verbatim or nearly so
  - `semantic_reliance` — agent used the guidance without explicit restatement
  - `task_trajectory` — agent's action sequence matches retrieved procedure/decision
  - `correction` — agent corrected or updated the retrieved note's content
  - `contradiction` — agent's findings contradict the retrieved note
  - `supersession` — agent produced a newer version of the retrieved concept
- `confidence` is a float 0.0–1.0. Omit or set to 0.0 if attribution is weak.
- A single candidate may attribute to multiple notes (array).
- Attribution on a candidate means that candidate's session content provides
  evidence about that note — not that the candidate is a copy of that note.

### Outcome Label Scope

The extraction LLM can observe only what is in the current episode. Therefore:

- `passive_used_likely` is derivable inline: LLM sees retrieved note + session use.
- `passive_useful_likely` and `passive_misleading_likely` require task outcome
  evidence, which may appear only in a later episode. These labels are deferred to
  the maintenance post-hoc derivation path (Gap 3). The extraction LLM must not
  attempt to assign outcome-quality labels.

### Extraction Prompt Responsibility

The extraction prompt instructs the LLM to:
1. Scan text windows for a `<!-- MnemonicOS context block` marker and
   `## Memory Context` section.
2. If found, read `current_hits:`, `stale_hits:`, and `contested_hits:` note IDs.
3. For each candidate it produces: if the candidate's content appears to rely on,
   paraphrase, correct, or contradict a retrieved note, add `retrieval_attribution`
   to that candidate.
4. Do not re-extract retrieved note content as a standalone new candidate unless it
   genuinely contains new information not in the existing note.
5. Do not invent note IDs. Use only IDs present in the `## Memory Context` block.

### Validation

`retrieval_attribution` is optional. When present, validation checks:
- Is an array.
- Each entry has `note_id` (non-empty string) and `evidence_type` (from vocabulary).
- `confidence` if present is a float 0.0–1.0.
- Entries with unrecognized `evidence_type` are stripped and recorded as a
  validation warning (not a hard rejection).

Invalid `retrieval_attribution` does not block promotion. It produces a validation
warning and the field is treated as absent.

### What This Layer Does Not Do

- Does not modify existing wiki notes based on retrieval attribution.
- Does not assign outcome-quality feedback labels (useful/misleading).
- Does not call the retrieval API; it reads only what is already in the session raw.
- Does not produce `note_passive_feedback` rows. That is maintenance's job.

---

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
