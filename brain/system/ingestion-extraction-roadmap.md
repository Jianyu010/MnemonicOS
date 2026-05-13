# Ingestion: Extraction Roadmap

## Purpose

This roadmap records implementation progress against
`ingestion-extraction-plan.md`, what remains, and what the current layer can
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

## Phase 8: Intra-Source Relation Extraction

Status: **not started**.

Planned deliverables:
- `intra_source_relations` top-level field added to `mnemos-extraction-v1` bundle schema.
- `OllamaLLMProvider` prompt extended with intra-source relation rules.
- `FixtureLLMProvider` emits deterministic intra_source_relations for multi-candidate
  bundles.
- `validate_intra_source_relations(relations, candidate_titles, errors)` in
  `validation.py` — invalid entries dropped, bundle not rejected.
- Migration adds `intra_source_relations_json TEXT` to `extraction_queue`.
- Worker stores validated relations on the queue row.
- Promotion layer amendment applies stored relations to wiki note frontmatter after
  bundle candidates are promoted (tracked in
  `ingestion-promotion-plan-intra-source-relations.md`).

## Remaining LLM/VLM Layer Work

- Add provider-conformance tests for fixture, Ollama, OpenAI-compatible, and
  CLI-command adapters.
- Add model availability checks and clearer operational errors for missing
  provider models, endpoints, credentials, or unsupported capabilities.
- Expand fixture providers to simulate procedures, decisions, incidents, and
  invalid type-specific fields.
- Deepen type-specific validation before promotion consumption.
- Expand decomposer tests for later user corrections, multiple traces in one
  session, richer textbook section boundaries, examples, and historical asides.
- Add richer extraction status reporting for candidate/error inspection.
- Add metrics useful for future reward/RL design, such as candidate acceptance
  rate, validation error categories, and promotion outcome feedback.

## Provider Decoupling Plan

Purpose:
- Remove Ollama as an architectural assumption while keeping it as one supported
  local adapter.
- Make the VLM/LLM extraction layer easy to adapt to OpenAI-compatible servers,
  OpenLLM, hosted APIs, CLI-backed engines, or deterministic local fixtures.
- Keep this scoped to extraction inference. This is not the north-flange bridge
  and does not change agent hook behavior.

### Phase A: Provider Contract Cleanup

Status: planned.

Work:
1. Keep the existing `VLMProvider` and `LLMProvider` protocols, but make their
   contracts capability-oriented:
   - `provider_name`
   - `model_name`
   - `capabilities` (`text_json`, `vision_json`, `streaming`, `batch`, etc.)
   - `healthcheck() -> ProviderHealth`
2. Move provider-specific prompt transport into adapter classes. The extraction
   worker should only call:
   - `vlm.describe(artifact)`
   - `llm.extract_candidates(packet)`
3. Add a shared provider error vocabulary:
   - `provider_unavailable`
   - `model_missing`
   - `auth_missing`
   - `timeout`
   - `invalid_provider_response`
   - `capability_unsupported`
4. Store provider errors on the extraction queue without deleting source
   material or mutating wiki.

Acceptance:
- Extraction worker contains no Ollama-specific branches.
- Missing provider dependencies produce queue errors with actionable messages.
- Fixture providers remain the default unit-test path.

### Phase B: Registry and Config Shape

Status: planned.

Work:
1. Replace hard-coded `if provider == "ollama"` builders with provider
   registries:
   - `build_vlm_provider(config)`
   - `build_llm_provider(config)`
2. Keep backward-compatible `[extraction]` keys for one release, but introduce a
   provider-shaped config:

```toml
[models.llm]
provider = "openai_compatible"
model = "qwen3"
base_url = "http://127.0.0.1:8000/v1"
api_key_env = "OPENAI_API_KEY"

[models.vlm]
provider = "ollama"
model = "llama3.2-vision:latest"
base_url = "http://127.0.0.1:11434"
```

3. Let one provider serve both LLM and VLM roles only when its capabilities say
   it can.
4. Add `mnemonicos doctor inference` to report configured providers and
   capability health.

Acceptance:
- Users can switch providers by config only.
- Ollama config remains supported but is no longer privileged in code.
- `doctor inference` reports full, text-only, vision-missing, and unavailable
  states.

### Phase C: Adapters

Status: planned.

Adapter order:
1. `fixture`: deterministic tests, no external dependency.
2. `ollama`: existing local adapter, retained.
3. `openai_compatible`: HTTP adapter for OpenAI-compatible local or hosted
   servers, including OpenLLM-style deployments.
4. `cli_command`: explicit escape hatch for environments where only a CLI engine
   is available. This adapter runs a configured command, passes a prompt or JSON
   packet on stdin/argv, captures stdout, applies timeout limits, and validates
   the returned JSON. It is slower and less deterministic than HTTP adapters.

Acceptance:
- Each adapter passes the same conformance tests.
- CLI-command adapter is opt-in and never selected implicitly.
- Provider metadata in candidate/snippet rows records provider, model, endpoint
  family, and adapter version.

### Phase D: Degraded Modes

Status: planned.

Work:
1. If no VLM is available, process text windows normally and persist visual
   artifact references with `visual_description_pending`.
2. If no LLM is available, leave extraction rows pending or mark them
   `provider_unavailable` according to queue retry policy.
3. If an adapter lacks JSON reliability, run strict parse/repair/validate before
   candidates reach `promotion_ready`.

Acceptance:
- VLM absence does not block text-only extraction.
- Provider failure never creates promoted candidates.
- All degraded behavior is visible in extraction status and daemon status.

## Phase 9: Retrieval Attribution in Session Extraction

Status: **complete 2026-05-13**.

Owner plan: `ingestion-extraction-plan.md` — "Retrieval Attribution in Session Extraction" section.

### Goal

Enable the extraction LLM to recognize `## Memory Context` blocks in session text
and emit `retrieval_attribution` arrays in candidate payloads, giving the promotion
and maintenance layers evidence about which retrieved notes the session used,
corroborated, corrected, or contradicted.

### Scope

- `src/second_brain/extraction/providers.py` — extend `_candidate_extraction_prompt()`
  with retrieval context recognition instructions
- `src/second_brain/extraction/providers.py` — extend `FixtureLLMProvider` to emit
  `retrieval_attribution` in fixture output when packet contains `retrieval_context_note_ids`
- `src/second_brain/extraction/validation.py` — validate and strip-on-error
  `retrieval_attribution` field in candidate payloads
- `src/second_brain/extraction/providers.py` — extend `make_llm_packet()` to accept
  optional `retrieval_context_note_ids: list[str]` for downstream tests
- Tests: session fixture with `## Memory Context` block → validate attribution
  propagates through extraction → promotion ledger

### Constraints

- Validation warning on malformed attribution does not block promotion.
- Extraction LLM may only reference `note_id` values from the `## Memory Context`
  block in the session text — invented IDs are stripped at validation.
- Only `passive_used_likely`-compatible evidence types emitted by extraction;
  outcome-quality labels deferred to maintenance post-hoc path.

### Acceptance

- Extraction prompt instructs LLM to recognize retrieval context markers.
- Valid `retrieval_attribution` arrays pass validation and reach
  `promotion_decisions.retrieval_attribution_json`.
- Malformed entries produce warnings, not rejections.
- Fixture provider supports attribution for deterministic tests.
- Test covering full path: session with `## Memory Context` → extraction →
  `retrieval_attribution` in candidate → promotion → ledger row.

---

## Architectural Conclusion

The LLM/VLM layer is good enough to hand off to the next layer. It consumes only
west-flange projections, stores VLM and LLM outputs only in extraction-layer
tables, validates candidate structure and provenance, and terminates at
`promotion_ready`. Phase 8 extends this with intra-source relation extraction:
the LLM, having consumed the full projection of one source, asserts typed
relations between the candidates it produces. These relations are stored on the
extraction queue row and applied to wiki note frontmatter by the promotion layer
after the bundle's candidates are promoted. Phase 8 is the primary source of graph
edges between notes that come from the same raw material — the only point in the
pipeline with visibility across all candidates from one source simultaneously.

Phase 9 adds passive feedback signal production: extraction sessions that include
retrieved notes in their raw material now annotate candidates with attribution
evidence, closing the feedback loop between retrieval and ingestion without
requiring a separate LLM attribution pass at maintenance time for those sessions.
