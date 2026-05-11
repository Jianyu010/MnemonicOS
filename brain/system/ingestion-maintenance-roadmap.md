# Ingestion: Maintenance Roadmap

## Purpose

This roadmap records implementation progress against
`ingestion-maintenance-plan.md`, where implementation diverges from
plan, and what remains before the knowledge maintenance layer can be called
complete.

## Verified Breakpoint (pre-implementation)

- Full repository test suite: 291 tests passing.
- Intra-source relations (LLM/VLM Phase 8) complete: extraction queue stores
  `intra_source_relations_json` (index-based format), promotion worker applies
  them to note frontmatter and graph edges.
- `daily_consolidation` and `weekly_hygiene` jobs run: trust, freshness,
  contradiction detection, review routing, relearn tasks all operational.
- `rebuild_note_graph()` exists in `graph.py` but runs per-note at promotion
  time only — no bulk wiki scan.
- `corroboration_count` lives in markdown frontmatter only. Not queryable from DB.
- Lifecycle advancement only occurs at promotion time via `renderer.py`.
- No `note_importance` table exists.
- No `maintenance.py` module exists.
- No external embedding model installed at the original breakpoint.
  `encode_text()` existed as the local semantic encoder path, but the maintenance
  layer still needed a provider-shaped embedding API before it could support
  arbitrary user runtimes.
- No cross-wiki neighbor linking exists.
- No raw re-visitation job exists.

---

## Layer 1 — Cross-Wiki Linking (per-ingestion + scheduled sweep)

### Prerequisite: embedding provider

Status: **implemented mechanically; provider decoupling planned**.

Phases 6-7 require embeddings, but they should not require Ollama specifically.
The local hash encoder is a zero-install fallback and external embedding models
should be selected through a provider-shaped API. See
`ingestion-maintenance-embedding-model.md` for the original nomic/Ollama
rationale; that decision is now treated as one adapter choice, not a product
requirement.

### Phase 6: Embedding-Based Neighbor Linking

Status: **complete**.

Deliverables shipped:
- `migrations/021_note_embedding_scans.sql` — `note_embedding_scans` tracking table
- `NeighborMatch`, `find_embedding_neighbors`, `assert_similar_to_link`,
  `run_embedding_neighbor_pass`, `run_embedding_neighbor_backfill` in `maintenance.py`
  — reads from `note_vectors` (existing table); same-type-only links;
  similarity ≥ neighbor_threshold → `similar_to`; ≥ dedup_threshold → `merge_review`
- `MaintenanceConfig`: `embedding_neighbor_enabled`, `neighbor_similarity_threshold`,
  `neighbor_k`, `neighbor_scan_max_age_days`
- Per-promotion wire-up in `promotion/worker.py` after `_apply_intra_source_relations`
- `wiki_embedding_neighbor_backfill` job in `jobs.py` and CLI
- 5 tests in `EmbeddingNeighborTest`: similar_to link, type boundary, dedup review,
  backfill scan records, job stats
- 323 tests passing

### Phase 7: LLM Cluster Validation + LLM-free Pattern Discovery

Status: **complete**.

Deliverables shipped:
- `ArchetypeCandidate`, `ClusterValidationResult`, `PatternDiscoveryResult` in `maintenance.py`
- `run_cluster_validation(conn, config, llm_fn=None)`: union-find clusters over `similar_to`
  edges; LLM validation with pattern_tag write-back; training pair recording;
  invalid edge deletion; merge_review routing — union-find path compression bug fixed
- `run_pattern_discovery(conn, config, llm_fn=None)`: Channel 2 (9-dim role-vector cosine,
  threshold 0.85) + Channel 3 (failure/resolution symmetry between incident/procedure notes)
- `MaintenanceConfig`: cluster_validation_* and pattern_discovery_enabled; `load_config`
  now parses ALL Phase 6/7 maintenance fields from `[maintenance]` TOML section
- `migrations/022`: maintenance_training_pairs table
- `wiki_cluster_validation` + `wiki_pattern_discovery` jobs
- 5 ClusterValidationTest + 3 PatternDiscoveryTest
- 331 tests passing

### Phase 8: Raw Re-Visitation (Layer 3)

Status: **implemented; E2E blocker fixed 2026-05-11**.

Deliverables shipped:
- `migrations/023_extraction_versioning.sql` — adds `extraction_schema_version`,
  `extraction_model_version` columns to `extraction_queue`
- `CURRENT_SCHEMA_VERSION = "1.0"` constant in `extraction/queue.py`; `mark_finished`
  updated to write both version columns
- `RevisitationResult`, `run_raw_revisitation(conn, config, *, dry_run, retrieve_fn)` in
  `maintenance.py`: queries stale processed original rows (schema or model
  version mismatch), inserts fresh lineage-linked revisit queue rows, suppresses
  duplicate revisits, and preserves the historical ledger
- `MaintenanceConfig`: requeue_enabled, requeue_min_age_days, requeue_context_enabled,
  requeue_context_top_k, requeue_max_per_run; all parsed by `load_config`
- `wiki_raw_revisitation` job in `jobs.py` and CLI
- Revisitation tests cover fresh-row requeue, current-schema skip, cap
  enforcement, dry-run immutability, context injection, job stat keys, and
  promoted-source ledger preservation
- 337 tests passing

Release blocker found by isolated end-to-end run and fixed 2026-05-11:
- `wiki_raw_revisitation` previously reset an existing processed queue row to
  `pending`. When that row already has promoted candidates, the next extraction
  run deletes `extraction_candidates` for the row and fails on the
  `promotion_decisions` foreign key. This violates the Phase 8 invariant that old
  candidates remain historical record.

Repair purpose: **satisfied 2026-05-11**
- Make raw re-visitation produce a fresh extraction attempt without mutating or
  deleting the historical extraction/promotion ledger for the original queue row.

Repair plan: **implemented 2026-05-11**
1. Add queue lineage fields in a new migration:
   - `extraction_queue.revisit_of_queue_id`
   - `extraction_queue.revisit_reason`
   - `extraction_queue.revisit_context_json`
2. Change `run_raw_revisitation` to insert a new pending queue row for each
   eligible source instead of resetting the old row. The new row copies the
   source identity and content hash, records `revisit_of_queue_id`, and starts
   with null extraction version columns.
3. Preserve old `extraction_candidates`, `promotion_decisions`, visual snippets,
   and extraction metadata unchanged.
4. Pass cross-wiki context through `revisit_context_json` and into the extraction
   LLM packet when `requeue_context_enabled` is true.
5. Add an E2E regression: ingest + extraction + promotion, force version drift,
   run `wiki_raw_revisitation`, run extraction + promotion again, and assert no
   foreign-key failure, old candidate rows remain, and new queue/candidate rows
   exist.

Acceptance mark:
- The isolated E2E path completes through raw re-visitation and second-pass
  extraction/promotion with zero queue errors.

---

## Release Readiness Repair Plan

Status: **required before calling the maintenance layer finished**.

Purpose:
- Convert implemented maintenance primitives into an operational layer that is
  scheduled, observable, and internally consistent with the plans.

R1 — Raw re-visitation ledger safety: **implemented 2026-05-11**
- Owner phase: Phase 8.
- Purpose: preserve historical extraction/promotion records while revisiting
  true raw.
- Work: implemented migration `024_revisit_extraction_lineage.sql`, fresh-row
  revisitation insertion, prompt context plumbing, duplicate revisit suppression,
  and E2E regression coverage.

R2 — Scheduled maintenance orchestration: **implemented 2026-05-11**
- Owner files: `daemon_jobs.py`, `config/memory.example.toml`, `jobs.py`, `cli.py`.
- Purpose: make the layer run without manual CLI calls.
- Work: default and example daemon schedules now include
  `wiki_embedding_neighbor_backfill`, `wiki_cluster_validation`,
  `wiki_pattern_discovery`, `wiki_graph_rebuild`, `wiki_lifecycle_audit`,
  `wiki_importance_scoring`, `wiki_health_report`, and
  `wiki_raw_revisitation`; individual CLI jobs remain the manual diagnostics
  surface.

R3 — Maintenance overview publication: **implemented 2026-05-11**
- Owner phase: Phase 5.
- Purpose: make `wiki_health_report` produce the user-visible maintenance
  overview promised by the plan.
- Work: live `wiki_health_report` calls
  `write_maintenance_overview(..., wiki_health=health)` with open review/relearn
  context and semantic dedup stats; dry-run remains read-only.

R4 — Archetype persistence and graph propagation: **implemented 2026-05-11**
- Owner phase: Phase 7.
- Purpose: make pattern discovery durable rather than stats-only.
- Work: `migrations/025_archetype_storage.sql` adds `archetypes` table +
  `graph_edges` archetype columns (`archetype_id`, `archetype_score`,
  `instance_confidence`); `Archetype` dataclass; `run_pattern_discovery` persists
  Channel 3 heuristic archetypes and LLM-validated Channel 2 archetypes; annotates
  matching graph_edges; `occurrence_count` increments on re-discovery; dry_run safe.
  4 tests in `ArchetypePersistenceTest`.

R5 — Roadmap truth maintenance: **complete 2026-05-11**
- Owner files: this roadmap and phase plans.
- Purpose: prevent stale status blocks from masking release state.
- Work: single Phase 8 status entry; test summary reflects actual file; release
  blockers marked explicitly; gate mark updated below.

Release gate:
- Full unit suite passes.
- Isolated E2E passes: ingest → extraction → promotion → retrieval → Layer 1
  sweep → Layer 2a/2b governance → Layer 3 raw re-visitation → second extraction
  + promotion.
- Daemon status shows scheduled maintenance jobs.

Current release gate mark (2026-05-11):
- Full unit suite: **343 tests passing**.
- R1 E2E (ledger safety): **passes** — `test_revisit_promoted_source_uses_new_queue_row`.
- R2 (daemon schedule): **passes** — all maintenance jobs in `daemon_jobs.py`.
- R3 (overview publication): **passes** — `wiki_health_report` writes `maintenance.md`.
- R4 (archetype persistence): **passes** — `ArchetypePersistenceTest` (4 tests).
- **Release gate: SATISFIED.** No known remaining gaps.

---

## Inference Provider Decoupling Plan

Purpose:
- Remove Ollama as an implicit dependency for maintenance inference.
- Make embedding, cluster-validation, and future inference-backed maintenance
  routines depend on small provider contracts rather than one installed runtime.
- Keep deterministic maintenance jobs operational when no external model is
  available.

This plan concerns the maintenance inference engine only. It is not the
north-flange bridge and does not change Codex/Claude hook capture.

### Phase M1: Embedding Provider Interface

Status: planned.

Work:
1. Introduce `EmbeddingProvider`:

```python
class EmbeddingProvider(Protocol):
    provider_name: str
    model_name: str
    dimensions: int

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        ...

    def healthcheck(self) -> ProviderHealth:
        ...
```

2. Move the current hash encoder behind `HashEmbeddingProvider`.
3. Replace direct `encode_text(...)` calls in retrieval, ingest, sync, jobs, and
   maintenance with `build_embedding_provider(config).embed_texts(...)`.
4. Preserve stored vector metadata: provider, model, dimensions, source hash,
   and provider config fingerprint.

Acceptance:
- No maintenance job imports `encode_text` directly.
- `provider = "hash"` remains zero-install and test-friendly.
- Switching embedding providers does not require changing maintenance code.

### Phase M2: Provider Registry and Config

Status: planned.

Work:
1. Add an embedding provider registry:
   - `hash`
   - `ollama_embeddings`
   - `openai_compatible_embeddings`
   - later: `sentence_transformers`, `cli_command_embeddings`
2. Extend `[embeddings]` without breaking current configs:

```toml
[embeddings]
enabled = true
provider = "openai_compatible_embeddings"
model = "text-embedding-3-small"
dimensions = 1536
base_url = "https://api.openai.com/v1"
api_key_env = "OPENAI_API_KEY"
reembed_on_summary_change_only = true
```

3. Add `mnemonicos doctor inference` output for embedding health:
   - available
   - disabled
   - provider_unavailable
   - dimensions_mismatch
   - reembed_required

Acceptance:
- Existing `provider = "hash"` configs continue to work.
- External embedding providers can be selected by config only.
- Dimension mismatch is detected before neighbor jobs run.

### Phase M3: Maintenance Degraded Modes

Status: planned.

Work:
1. Make embedding-dependent jobs report degraded status rather than fail the
   daemon:
   - semantic dedup
   - embedding neighbor pass
   - embedding neighbor backfill
   - cluster validation when seeded by embedding links
2. Keep deterministic jobs running without embeddings:
   - graph rebuild
   - lifecycle audit
   - importance scoring from non-embedding signals
   - health report
   - raw re-visitation ledger checks
3. Add status counters to job results:
   - `embeddings_disabled`
   - `embedding_provider_unavailable`
   - `semantic_jobs_skipped`
   - `reembed_required`

Acceptance:
- A machine with no model runtime still runs maintenance safely.
- Daemon status distinguishes skipped inference from job failure.
- Retrieval remains usable through exact/BM25/graph channels.

### Phase M4: LLM-Backed Maintenance Adapter

Status: planned.

Work:
1. Reuse the extraction-layer `LLMProvider` contract for maintenance routines
   that need language judgment, such as cluster naming or validation.
2. Add a maintenance-specific wrapper API:

```python
validate_cluster(provider, cluster_packet) -> ClusterValidationResult
name_archetype(provider, archetype_packet) -> Archetype
```

3. Keep LLM calls optional. If no LLM is available, run LLM-free pattern
   discovery channels and queue unresolved validation work for later.

Acceptance:
- Maintenance can use Ollama, OpenAI-compatible, or CLI-command LLM adapters
  through the same contract.
- LLM absence does not block deterministic maintenance.
- LLM-backed maintenance output is validated before mutating graph/archetype
  records.

### Phase M5: Re-Embedding and Migration Policy

Status: planned.

Work:
1. Treat provider/model/dimensions/fingerprint changes as vector-version changes.
2. Add a re-embed planner:
   - count stale vectors
   - estimate work
   - run dry-run
   - backfill in batches
3. Keep old vectors until replacement succeeds.
4. Record provider lineage in job stats and maintenance overview.

Acceptance:
- Changing embedding provider does not silently mix incompatible vector spaces.
- Re-embedding is resumable and does not corrupt retrieval.
- Maintenance overview reports current embedding provider and stale-vector count.

---

## Layer 2a — Pattern Discovery (batched trigger: N≥10 new notes OR T≥1hr)

No dedicated phase yet. Currently designed as a Phase 7 extension. Will be split
into its own job once the archetype vocabulary is large enough.

**Architecture (decided):** LLM-free primary detection via two channels from
blueprint section 6.4a:
- **Channel 2**: 9-dim role-vector cosine clustering using the immutable role
  vocabulary (blueprint 3.5). No LLM required for detection; LLM names candidates.
- **Channel 3**: failure/resolution symmetry between `incident`-type notes (failure
  signature) and `procedure`-type notes (resolution signature). No LLM required
  for detection.

Channel 1 (deductive via root logics) is **deferred** — requires full inference engine.

**Archetype record schema (decided):**

```python
@dataclass
class Archetype:
    archetype_id: str
    label: str                        # LLM-named
    layer_a_description: str          # semantic cluster identity + structural motif
    layer_b_role_schema: dict         # field → role vocabulary term
    occurrence_count: int
    score: float
    relation_type: str                # from blueprint 6.5 vocabulary
    source_channel: int               # 2 or 3
```

**graph_edges additional fields (decided):**
- `archetype_id TEXT` — which archetype asserted this edge
- `archetype_score REAL` — archetype's current score at time of assertion
- `instance_confidence REAL` — per-edge confidence for this specific pair

**Trigger and schedule (decided):**
- Fires with Layer 2b: N≥10 new notes promoted OR T≥1hr since last run
- B-only routine also runs every 5 hours (role_schema refinement only)
- If B-only discovers a new archetype: trigger Layer 2b; if occurrence_count > M=5,
  also flag Layer 3 eager run

Planned when split:
- `run_pattern_discovery(conn, config, llm_provider) -> PatternDiscoveryResult`
  in `maintenance.py` (Channels 2 and 3)
- `run_b_only_refinement(conn, config) -> BOnlyResult` in `maintenance.py`
- `archetypes` table migration
- Scheduled Layer 2a + batched trigger wire-up
- Scheduled B-only routine (every 5 hours)

---

## Layer 2b — Wiki Governance (daily)

> **Phase 1 moved.** `corroboration_count` in DB was Phase 1 of this layer.
> It has been reclassified as a promotion-layer amendment and is now tracked in
> `ingestion-promotion-roadmap.md`. Implementation details preserved in
> `ingestion-maintenance-plan-phase1.md`.

### Phase 2: Graph Maintenance — bulk rebuild

Status: **complete**.

Deliverables shipped:
- `src/second_brain/maintenance.py` — new module with `GraphRebuildResult`,
  `rebuild_wiki_graph`, `detect_orphan_nodes`
- `wiki_graph_rebuild` job wired into `jobs.py` and `cli.py`
- 5 tests in `tests/test_knowledge_maintenance.py`: drift repair, stale edge pruning,
  orphan detection, dry-run idempotency, job stats keys
- 300 tests passing

### Phase 3: Lifecycle Auditing — advancement and drift

Status: **complete**.

Deliverables shipped:
- `MaintenanceConfig` in `config.py` with all threshold fields; wired into
  `AppConfig` and `load_config`
- `LifecycleAuditResult` and `audit_lifecycle_states` in `maintenance.py`:
  advancement pass (draft→verified→canonical) + drift pass (review item creation)
- `wiki_lifecycle_audit` job in `jobs.py` and CLI
- 7 tests in `LifecycleAuditTest`: all advancement paths, contested skip,
  drift review item, dry-run idempotency, job stats keys
- 307 tests passing

### Phase 4: Importance Scoring — unified signal

Status: **complete**.

Deliverables shipped:
- `migrations/020_note_importance.sql` — `note_importance` table + `retrieval_hit_features.importance_score` column
- `ImportanceSignal`, `compute_importance_scores` in `maintenance.py`: connectivity
  sigmoid + trust/freshness/lifecycle weights → upsert to `note_importance`
- `wiki_importance_scoring` job in `jobs.py` and CLI
- `retrieval.py`: importance_map load + stored in `retrieval_hit_features`
- 5 tests in `ImportanceScoringTest`: hub>spoke ordering, default signals, DB persist, feature column, job stats
- 307 tests passing

### Phase 5: Wiki Health Stats + Semantic Dedup

Status: **complete**.

Deliverables shipped:
- `SemanticDuplicatePair`, `WikiHealthStats`, `_cosine`, `detect_semantic_duplicates`,
  `compute_wiki_health_stats` in `maintenance.py`
- `MaintenanceConfig`: `semantic_dedup_threshold`, `semantic_dedup_enabled` fields
- `wiki_health_report` job in `jobs.py` and CLI
- `ops.py`: `write_maintenance_overview` extended with Wiki Health table (bug fix: syntax error on `lines.extend` resolved)
- Dedup slug idempotency: scans `wiki/review/semantic-dedup*.md` filenames before creating
- 6 tests in `WikiHealthTest`: counts, dedup disabled guard, near-identical detection, slug idempotency, job stats, overview table
- 318 tests passing

Note: semantic dedup is subsumed into Phase 6 (threshold θ=0.92 in
`run_embedding_neighbor_pass`). Phase 5 focuses on health stats only.

---

## Layer 3 — Raw Re-Visitation (daily 2AM + M=5 eager trigger)

### Phase 8: Raw Re-Visitation and Cross-Wiki Context Injection

Status: **superseded by implemented Phase 8 section above**.

The original not-started plan is retired. Phase 8 implementation exists, but
release readiness is blocked by R1 in the Release Readiness Repair Plan: raw
re-visitation must create a fresh queue row rather than resetting the historical
queue row.

---

## Final Phase — Layer 3 Scalability Engineering

### Phase 9: Layer 3 Incremental Re-Visitation

Status: **not started**. Deferred — implement only when raw corpus growth makes
nightly full re-visitation noticeably slow.

**Problem**: Layer 3 runs all raws through re-extraction every day. As the raw
corpus grows (new PDFs, sessions, artifacts), the nightly job becomes increasingly
heavy — O(raws) per night regardless of how many archetypes actually changed.

**Planned solutions (TBD at implementation):**
- **Version-based incremental**: track `last_revisited_at` and
  `archetype_version_at_revisit` per raw. Only re-visit raws whose oldest
  applicable archetype has changed since last run.
- **Pattern-relevance gating**: compute a relevance score for each raw against
  the changed archetypes. Only re-visit raws above a threshold. Skip raws where
  no changed archetype is plausibly applicable.
- **Batched nightly budget**: cap re-visitation at a configurable number of raws
  per night, prioritized by archetype relevance score.

This phase does not change the Layer 3 semantic contract — only the efficiency
of its scheduling. The daily 2AM run and M=5 eager trigger remain; this phase
makes them scale without growing proportionally to corpus size.

---

## Test Summary

`tests/test_knowledge_maintenance.py`: **written and active**.

Latest verification before readiness repairs: `python3 -m unittest discover -s
tests` passes with 337 tests. Add a dedicated end-to-end regression for R1 and
daemon schedule coverage for R2 before marking the layer finished.

---

## Architectural Conclusion

The promotion layer delivers notes to `brain/wiki/`. The knowledge maintenance
layer ensures the wiki evolves and deepens without silent drift. Four sub-layers
at increasing frequency cost complete the pipeline:

```text
west flange
    → LLM/VLM extraction (intra-source relations via candidate index)
    → promotion gate → decision engine → renderer → atomic write
    → sync_vault (writes corroboration_count + all fields to DB) + embed_backfill
    → Layer 1, per-ingestion + scheduled sweep:
          embedding neighbor pass → similar_to seed links (Phase 6)
    → Layer 2a, batched trigger (N≥10 new notes OR T≥1hr):
          Channel 2 role-vector clustering + Channel 3 failure-symmetry
          → scored archetype candidates; LLM validates/names only (Phase 7 sub-pass)
    → Layer 2b, same batched trigger:
          graph rebuild + lifecycle audit + importance scoring
          + wiki-wide link rerun applying current archetypes (Phases 2–5)
    → B-only routine, every 5 hours:
          refine layer_b_role_schema → propagate score updates to graph_edges
          → new archetype found? trigger Layer 2b + eager Layer 3 if M≥5
    → Layer 3, daily 2AM + eager trigger (occurrence_count > M=5):
          raw re-visitation + cross-wiki context injection (Phase 8)
    → [Phase 9] Layer 3 Scalability Engineering:
          version-based incremental + pattern-relevance gating
```

Design authority: `development/humanoid-MnemonicOS-core-blueprint.md`.

Layer 2b (Phases 2–5) has no embedding model dependency and ships first.
The self-evolving loop lives in Layers 1 and 2a. Layer 2b is deterministic and
idempotent on fixed material — it propagates archetype changes but does not
drive evolution itself.
