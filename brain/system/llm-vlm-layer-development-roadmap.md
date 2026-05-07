# LLM/VLM Layer Development Roadmap

## Purpose

This roadmap records implementation progress against
`llm-vlm-layer-development-plan.md`, what remains, and what the current layer can
claim.

Current conclusion: the LLM/VLM layer is implemented as an ingestion-side,
source-agnostic compiler from west-flange projections to `promotion_ready`
candidate rows. It does not write wiki. The next architectural layer should be a
deterministic promotion layer that consumes those rows and canonicalizes them
into MnemonicOS wiki notes.

## Current Implementation State

Implemented package:

```text
src/second_brain/extraction/
    __init__.py
    models.py
    providers.py
    queue.py
    store.py
    validation.py
    worker.py
```

Implemented storage:

- `extraction_queue`
- `extraction_visual_snippets`
- `extraction_candidates`

Implemented surfaces:

- `mnemonicos queue-extraction`
- `mnemonicos extraction-status`
- `mnemonicos run-job process_extraction_queue`
- `POST /v1/extraction/queue`
- `POST /v1/extraction/process`
- `GET /v1/extraction/status`
- daemon schedule for `process_extraction_queue`
- daemon status showing extraction queue health

Current test evidence:

- `tests.test_extraction_layer` passes.
- `tests.test_west_flange` passes.
- `tests.test_session_capture` passes.
- Full repository test suite passes: 198 tests.

## Phase 1: Legacy Promotion Removal

Status: implemented.

What is done:

- Legacy `promotion_queue` runtime was removed from active code.
- Legacy workers `process_promotion_queue` and `backfill_promotion_queue` were
  removed from CLI, jobs, daemon schedule, API surfaces, and tests.
- `--promote-now` was removed from the CLI path and rejected by the postflight
  script.
- `ingest_session_promote`, `_promote_session_content`,
  `_enqueue_promotion_candidate`, and `project_episode_for_promotion` were
  removed as active wiki-writing paths.
- A drop migration removes the legacy `promotion_queue` table.
- Old promotion tests were deleted or rewritten around raw intake and extraction
  behavior.

Divergences from plan:

- The plan allowed keeping some legacy pure helpers. The actual implementation
  removed the live promotion runtime more aggressively, while leaving only
  generally reusable note/parser/sync code elsewhere.

Impact:

- Clean slate achieved. There is no competing west-to-wiki compiler.

Conclusion:

- Phase 1 is complete.

## Phase 2: Extraction Queue and Candidate Store

Status: implemented.

What is done:

- `extraction_queue` stores source identity, bridge/session/task identity,
  duplicate state, status, attempts, errors, projection counts, snippet counts,
  and candidate counts.
- `extraction_visual_snippets` stores VLM outputs keyed by queue row and
  artifact identity.
- `extraction_candidates` stores model candidate payloads, validation status,
  validation errors, model metadata, and promotion-readiness state.
- Queue identity is deterministic for artifact/session archive IDs and
  episode `(conversation_id, episode_id)` pairs.
- Queue operations do not write wiki.

Divergences from plan:

- Candidate rows use `promotion_state` and `validation_status` but do not yet
  record promoted note IDs, because deterministic wiki promotion is intentionally
  not implemented in this layer.

Impact:

- The store is ready for a later promotion layer to consume
  `promotion_ready` candidates and later add promotion result bookkeeping.

Conclusion:

- Phase 2 is complete for the current architecture.

## Phase 3: Projection Consumer

Status: implemented.

What is done:

- The worker loads pending extraction rows.
- Queue rows are converted into west-flange `SourceRef` objects.
- The worker opens `ProjectionService.open_projection`.
- It pulls `TextWindow` and `ArtifactView` lanes.
- It records projection metadata, warnings, window counts, and artifact counts.
- It closes leases on success and failure.
- Dry-run mode opens projection and reports counts without mutating queue rows.
- Exact duplicates are skipped through thin projection redirect behavior.
- Artifact, archived session, and episode sources are covered in tests.

Divergences from plan:

- The original plan focused on artifact and episode projection. Implementation
  also supports archived session sources because that is a useful bridge from
  raw intake to extraction.

Impact:

- The layer is more source-flexible without adding source-specific parsing logic
  to extraction.

Conclusion:

- Phase 3 is complete for current west-flange consumers.

## Phase 4: VLM Visual Snippet Layer

Status: implemented with fixture and Ollama provider boundaries.

What is done:

- VLM provider interface exists.
- Fixture VLM provider exists for deterministic tests.
- Ollama VLM adapter exists behind the same interface.
- `ArtifactView` payloads or source attachment paths are converted to visual
  snippets.
- Missing visual payloads produce warnings, not crashes.
- Visual snippets are persisted in extraction-layer tables.
- West-flange metadata remains free of VLM descriptions.

Divergences from plan:

- Ollama provider behavior is not covered by normal unit tests because it
  requires a running local Ollama service and model availability.
- Fixture tests prove the contract; integration tests for real VLM output remain
  future work.

Impact:

- The provider seam is real and swappable.
- Model-quality and latency risks are isolated from the deterministic queue and
  validation code.

Conclusion:

- Phase 4 is complete as a software boundary. Real-model integration hardening
  remains.

## Phase 5: LLM Candidate Bundle Layer

Status: implemented with fixture and Ollama provider boundaries.

What is done:

- LLM provider interface exists.
- Fixture LLM provider emits deterministic `mnemos-extraction-v1` JSON.
- Ollama LLM adapter exists behind the same interface.
- The LLM input packet includes active source identity, projection metadata,
  text windows, source spans, artifact markers, and visual snippets.
- Candidate bundle output is parsed and converted into candidate rows.
- Invalid bundle JSON is represented as an invalid candidate row rather than
  causing wiki mutation.
- No LLM path writes wiki.

Divergences from plan:

- The fixture provider emits decomposed `source` and `concept` candidates,
  including agentic-session timelines and document-local components for concepts,
  experiments, derivations, and theorems.
- Ollama output quality is not unit-tested.
- The current implementation makes `components` first-class and validates
  component evidence, temporal order, trace membership, and dangling links.

Impact:

- The structured candidate pipe now uses loss-minimizing decomposition
  candidates rather than summary-only candidates.
- Richer prompt shaping and real-model evals can improve content quality without
  changing the downstream candidate contract.

Conclusion:

- Phase 5 is complete for pipeline mechanics and the first decomposer contract:
  LLM output preserves source-local components and trace hints, while summaries
  remain index hints.

## Phase 6: Deterministic Candidate Validation

Status: implemented for core gates.

What is done:

- `validation.py` checks schema version, supported candidate type, required
  fields, confidence vocabulary, active source refs, and evidence binding to the
  projection packet.
- Valid candidates become `promotion_ready`.
- Invalid candidates persist stable validation errors.
- Validation does not write wiki.

Divergences from plan:

- Type-specific normalization checks are intentionally shallow. They verify core
  candidate shape but do not yet deeply validate every future note-family field.
- There is not yet a deterministic promotion input model separate from the raw
  candidate payload.
- Component-level evidence, temporal order, trace membership, and link validation
  are implemented for the first decomposer contract.

Impact:

- The gate is strong enough to prevent ungrounded or malformed model output from
  reaching promotion readiness.
- The next promotion layer should deepen normalization from candidate payload to
  canonical note model.

Conclusion:

- Phase 6 is complete as a first validation gate. Promotion-layer normalization
  is the next place to add stricter note-family rules.

## Phase 7: Daemon and Admin Surfaces

Status: implemented.

What is done:

- CLI can queue artifact, episode, and archived session extraction.
- CLI can inspect extraction status.
- CLI and job runner can run `process_extraction_queue` with dry-run support.
- HTTP API mirrors queue/process/status behavior.
- Daemon schedule includes `process_extraction_queue`.
- Daemon status reports extraction queue health.
- Normal retrieval/runtime tests remain separate from extraction.
- No admin surface writes wiki.

Divergences from plan:

- Status output is intentionally minimal and operational. A richer review/admin
  UI is out of scope.

Impact:

- The layer is operable as ingestion/admin infrastructure while normal
  north/east retrieval stays decoupled.

Conclusion:

- Phase 7 is complete for current CLI/API/daemon operation.

## Remaining LLM/VLM Layer Work

- Add integration-gated tests for Ollama VLM and LLM providers.
- Add model availability checks and clearer operational errors for missing
  Ollama models.
- Expand fixture providers to simulate procedures, decisions, incidents, and
  invalid type-specific fields.
- Deepen type-specific validation before promotion consumption.
- Expand decomposer tests for later user corrections, multiple traces in one
  session, richer textbook section boundaries, examples, and historical asides.
- Add richer extraction status reporting for candidate/error inspection.
- Add metrics useful for future reward/RL design, such as candidate acceptance
  rate, validation error categories, and promotion outcome feedback.

## Architectural Conclusion

The LLM/VLM layer is good enough to hand off to the next layer. It consumes only
west-flange projections, stores VLM and LLM outputs only in extraction-layer
tables, validates candidate structure and provenance, and terminates at
`promotion_ready`. The next system milestone should be the deterministic
promotion layer: consume `promotion_ready` candidates, decide create/update/
confirm/review, render canonical wiki notes, sync indexes, and record promotion
decisions.
