# Knowledge Maintenance Layer Development Roadmap

## Purpose

This roadmap records implementation progress against
`knowledge-maintenance-development-plan.md`, where implementation diverges from
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
- No embedding model installed. `encode_text()` exists in `semantics.py` but
  requires `nomic-embed-text` via Ollama.
- No cross-wiki neighbor linking exists.
- No raw re-visitation job exists.

---

## Layer 1 — Cross-Wiki Linking (per-ingestion + scheduled sweep)

### Prerequisite: embedding model

Status: **not started**.

Before Phases 6–7 can run: `ollama pull nomic-embed-text` and set
`config.embeddings.enabled = true`. See
`knowledge-maintenance-embedding-model.md` for rationale and RL training path.

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

Status: **complete**.

Deliverables shipped:
- `migrations/023_extraction_versioning.sql` — adds `extraction_schema_version`,
  `extraction_model_version` columns to `extraction_queue`
- `CURRENT_SCHEMA_VERSION = "1.0"` constant in `extraction/queue.py`; `mark_finished`
  updated to write both version columns
- `RevisitationResult`, `run_raw_revisitation(conn, config, *, dry_run, retrieve_fn)` in
  `maintenance.py`: queries stale processed rows (schema or model version mismatch),
  resets status to 'pending'; optional `retrieve_fn` for context injection counting
- `MaintenanceConfig`: requeue_enabled, requeue_min_age_days, requeue_context_enabled,
  requeue_context_top_k, requeue_max_per_run; all parsed by `load_config`
- `wiki_raw_revisitation` job in `jobs.py` and CLI
- 6 tests in `RevisitationTest`: stale row reset, current-schema skip, cap enforcement,
  dry-run immutability, context injection counting, job stats keys
- 337 tests passing

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
> `promotion-layer-development-roadmap.md`. Implementation details preserved in
> `knowledge-maintenance-development-plan-phase1.md`.

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

Status: **not started**.

Frequency: **daily at 2AM** (safety net — ensures all new archetypes are applied
to raws within 24 hours regardless of M threshold) **plus** an eager trigger when
any archetype's `occurrence_count` exceeds M=5 (signals enough evidence to warrant
immediate re-processing). Supports the convergence loop: new archetypes from
Layer 2a → richer re-extraction → new links → new archetypes.

Planned deliverables:
- `migrations/021_extraction_versioning.sql` — add `extraction_schema_version`
  and `extraction_model_version` columns to `extraction_queue`
- Update `mark_finished()` in `queue.py` to write both version columns
- `run_raw_revisitation(conn, config) -> RevisitationResult` in `maintenance.py`
- Layer 3 job wire-up

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

`tests/test_knowledge_maintenance.py`: **not yet written**.

Will cover all functions in `maintenance.py` across Phases 2–8.

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
