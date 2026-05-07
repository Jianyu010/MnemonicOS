# OBSOLETE: MnemonicOS VLM/LLM Extraction Daemon Plan

This document is historical. It has been superseded by the governing
[`brain/system/llm-vlm-layer-development-plan.md`](../brain/system/llm-vlm-layer-development-plan.md)
and
[`brain/system/llm-vlm-layer-development-roadmap.md`](../brain/system/llm-vlm-layer-development-roadmap.md).

In particular, this document contains stale wording about direct wiki
auto-promotion. The current LLM/VLM layer stops at validated `promotion_ready`
candidates; deterministic wiki promotion is a separate missing layer.

## Summary

Build the extraction daemon as a source-agnostic pipeline over the completed west-flange RAM projection API:

```text
SourceRef
  -> ProjectionLease
  -> extraction_queue
  -> VLM snippets
  -> LLM candidate JSON
  -> strict validation
  -> auto-promote wiki
  -> sync_vault
  -> audit/reward traces
```

Locked decisions:

- Queue: remove legacy `promotion_queue` and replace it with `extraction_queue`.
- Models: local Ollama; VLM = `llama3.2-vision:latest`, LLM = `gemma4:e4b`.
- Candidate store: SQLite.
- Wiki writes: full auto-promote, but only through strict deterministic gates.
- Source scope: any west-flange `SourceRef`; extraction logic should consume projection output, not raw source-specific files directly.

## System Position

Design references:

- `brain/system/product-boundary.md`: source of truth for the product boundary.
- `docs/RUNTIME_PROTOCOL.md`: current north/east runtime contract and old promotion assumptions.
- `brain/system/west-flange-development-plan.md`: source of truth for west-flange true-raw intake and RAM projection.

Flange map:

- **North flange**: agent/human/API callers. Current code includes `orchestrator.py`, `runtime.py`, `cli.py`, `server.py`, bridge scripts, and bridge-facing helpers.
- **West flange**: raw material intake and projection. Current code includes artifact connectors, `session_capture.py`, `ingest_artifact`, and `projection/`. It archives true raw and returns `ProjectionLease`, `TextWindow`, and `ArtifactView`.
- **South flange**: storage and index substrate. Current implementation is SQLite plus markdown, FTS, hash vectors, graph tables, and sync/materialization code. Long term this is where database adapters would live.
- **East flange**: retrieval and context delivery. Current code includes `retrieval.py`, trust/freshness, graph expansion, archive fallback, and `runtime.resolve_task_context`.

The VLM/LLM extraction daemon is not itself a flange and is not part of the north-flange query path. It is the ingestion-side memory compiler between west projection and canonical memory:

```text
west projection
  -> extraction daemon
  -> canonical wiki + indexes
  -> east retrieval packets
  -> north callers
```

North-flange callers should not call the extraction daemon during normal query/retrieval work. The only acceptable north-facing touchpoints are administrative or explicit ingestion controls, such as a human/CLI/API request to queue a raw source for extraction. Agent task context still flows through east retrieval packets, not through the extraction daemon.

## Current Broken Link

The old promotion runtime is the broken west-to-wiki compiler. It was built before the west-flange projection layer existed, so it mixes several responsibilities that should now be separated:

- west-ish raw session handling
- old summary / explicit-memory-block extraction
- queue state
- duplicate/update/new decisions
- wiki file mutation
- review item creation
- sync/index refresh
- session metrics

Current old-path hotspots before Phase 1 removal:

- `promotion_queue` in the old phase-10 migration
- `_enqueue_promotion_candidate()`
- `_promote_session_content()`
- `ingest_session_promote()`
- `project_episode_for_promotion()`
- daemon job `process_promotion_queue`
- daemon job `backfill_promotion_queue`
- CLI flag `--promote-now`
- API/CLI surface `capture project-episode`

After clean removal, the only intended west-to-wiki path is:

```text
raw source or episode
  -> west-flange projection lease
  -> extraction_queue
  -> VLM/LLM candidate generation
  -> deterministic validation and promotion policy
  -> canonical wiki write
  -> sync_vault
```

Expected rewiring after the extraction queue exists:

- `ingest-session` should archive raw material; the new extraction queue will become the asynchronous compiler input.
- captured episodes should queue extraction by `conversation_id` and `episode_id`.
- daemon status should report extraction queue health, not promotion queue health.
- runtime/orchestrator writeback should not promote summaries directly; if it archives raw material, that archival path may create extraction work asynchronously.
- retrieval should remain unchanged in principle: it reads canonical notes/indexes and does not care which compiler created them.

Obsolete or stale surfaces to mark clearly during the refactor:

- `brain/system/promotion-detach-plan.md`: obsolete after clean-slate removal replaces transition planning.
- `docs/RUNTIME_PROTOCOL.md`: updated to remove the old promotion slow path; future updates should add extraction queue semantics when implemented.
- `docs/IMPLEMENTATION_PLAN.md`: stale where it treats old ingest promotion as current architecture.
- Tests named around promotion queue or `project_episode_for_promotion`: obsolete and should be rewritten for extraction queue behavior rather than carried forward.

Phase 1 implementation status:

- Legacy promotion CLI/API/job hooks are removed.
- `ingest-session` is raw archival only and does not enqueue a compiler job yet.
- `promotion_queue` is not created in fresh databases; a drop migration removes it from migrated databases.
- The extraction queue and daemon are intentionally deferred to the next phase.

## Key Changes

### Remove Legacy Promotion Runtime

This is a clean-slate refactor, not a compatibility migration. Remove the old promotion path entirely:

- Drop the `promotion_queue` table and all code paths that enqueue or process it.
- Remove `process_promotion_queue` and `backfill_promotion_queue` jobs from daemon config, CLI choices, server job handling, tests, and docs.
- Remove `--promote-now`, `ingest_session_promote()`, `_promote_session_content()`, and old explicit-memory-block promotion behavior.
- Replace `capture project-episode` with queueing into the new extraction daemon, or rename it to `capture queue-extraction` if the CLI is being cleaned up.
- Keep reusable pure helpers only if they still serve the new pipeline: note rendering, note path resolution, normalization, Obsidian link projection, parser validation, and vault sync.
- Update tests that expected old queue compatibility to assert the new extraction queue behavior instead.

### Add Extraction Queue And State

Add SQLite-backed extraction state as the only promotion/extraction queue:

- `extraction_queue`: pending work by `source_kind`, source identity, bridge/session/task identity, status, attempts, and errors.
- `extraction_candidates`: model candidate payloads, validation state, promoted note IDs, and promotion explanations.
- `extraction_visual_snippets`: VLM outputs keyed by queue row and artifact marker.

### Add Extraction Runtime

The extraction worker:

1. Opens `ProjectionService.open_projection(source_ref)`.
2. Skips exact duplicates unless no prior canonical extraction exists.
3. Sends `ArtifactView` payloads/source attachments to the Ollama VLM.
4. Stores visual snippets keyed by `artifact_id` and marker.
5. Sends `TextWindow` plus matching snippets to the Ollama LLM.
6. Parses strict JSON decomposition output. The LLM must break usable raw into
   source-grounded components rather than collapse it into a summary.
7. Stores all candidates in SQLite.
8. Validates candidates through MnemonicOS rules.
9. Auto-promotes valid candidates through existing note rendering and sync.
10. Closes the projection lease.

Ollama defaults:

- base URL: `http://127.0.0.1:11434`
- VLM model: `llama3.2-vision:latest`
- LLM model: `gemma4:e4b`
- temperature: `0`
- timeout: `180` seconds

### Candidate Output Contract

The LLM must output JSON only. It should preserve usable-raw information by
decomposing it into components. `summary` is an index hint; `components` is the
main payload that the deterministic promotion layer uses to build wiki notes,
procedures, timelines, and graph edges.

The LLM may decide how many nodes, components, traces, and task/document threads
the source contains. MnemonicOS software validates the proposed structure before
it can become `promotion_ready`.

```json
{
  "schema_version": "mnemos-extraction-v1",
  "source_summary": "...",
  "items": [
    {
      "type": "source|concept|procedure|decision|incident",
      "title": "...",
      "summary": "...",
      "body": "...",
      "confidence": "low|medium|high",
      "source_refs": ["artifact/... or episode/..."],
      "components": [
        {
          "component_id": "c1",
          "component_type": "user_request|agent_action|failure|observation|web_finding|milestone|instruction|definition|claim|experiment|derivation|theorem|example|procedure_step|open_question",
          "text": "...",
          "why_it_matters": "...",
          "evidence": [{"source_locator": "...", "quote": "..."}],
          "links": [{"type": "causes|supports|updates|next|derived_from|part_of", "target": "c2"}]
        }
      ],
      "evidence": [
        {
          "source_kind": "artifact|episode|session",
          "source_locator": "...",
          "char_start": 0,
          "char_end": 100,
          "artifact_id": "fig_001",
          "marker": "[FIGURE: ...]"
        }
      ],
      "entities": [],
      "relations": [],
      "temporal_order": ["c1", "c2"],
      "importance": "low|medium|high",
      "freshness_hint": "current|historical|unknown",
      "relevance_hint": "core|supporting|aside"
    }
  ]
}
```

Strict gate before wiki write:

- valid JSON and schema version
- supported note type
- non-empty title, summary, and body
- at least one provenance/evidence item
- components preserve source-local facts, chronology, and links for non-trivial
  sources
- every component has evidence
- source refs point back to the active queue source
- rendered markdown parses successfully
- duplicate/update decision runs before file write
- `sync_vault` succeeds after write

Invalid candidates stay in `extraction_candidates` with errors; they do not write wiki.

## Public Interfaces

CLI:

- `mnemonicos queue-extraction --source-kind artifact --archive-id ...`
- `mnemonicos queue-extraction --source-kind episode --conversation-id ... --episode-id ...`
- `mnemonicos run-job process_extraction_queue [--dry-run]`

HTTP:

- `POST /v1/extraction/queue`
- `POST /v1/extraction/process`
- `GET /v1/extraction/status?queue_id=...`

Config:

```toml
[extraction]
enabled = true
ollama_base_url = "http://127.0.0.1:11434"
vlm_model = "llama3.2-vision:latest"
llm_model = "gemma4:e4b"
temperature = 0
queue_limit = 5
timeout_seconds = 180
```

## Test Plan

- Migration creates the extraction tables.
- Legacy `promotion_queue` table, workers, and CLI/API surfaces are removed.
- Daemon schedules `process_extraction_queue`, not `process_promotion_queue`.
- `--promote-now` is not accepted.
- `ingest-session` archives raw material with no legacy promotion side path; explicit admin surfaces queue extraction.
- Queueing artifact, archived session, and episode sources inserts pending extraction rows.
- Dry-run `process_extraction_queue` opens projection but writes no wiki.
- Fixture VLM/LLM providers return deterministic JSON and mark valid `source` and `concept` candidates `promotion_ready`.
- Invalid JSON, missing evidence, unsupported type, and parse-invalid markdown are stored as invalid candidates with no wiki write.
- Exact duplicate artifacts skip extraction unless canonical extraction is missing.
- The LLM/VLM layer stops at candidate validation; deterministic wiki promotion remains a later layer.
- Ollama provider tests are optional/integration-gated so normal unit tests do not require models running.

## Assumptions

- `gemme:e4b` means the installed Ollama model `gemma4:e4b`.
- `llama3.2-vision:latest` remains the visual specialist for v1.
- Full auto-promote is allowed only after strict deterministic validation; model confidence alone never authorizes a write.
- West flange remains pull-only and does not call models.
- VLM descriptions and LLM interpretations are extraction-layer records, not west-flange metadata.
