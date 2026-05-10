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

## Layer 2 — Wiki Governance (foundational, daily/weekly)

### Phase 1: Foundation — corroboration_count in DB

Status: **not started**.

Planned deliverables:
- `migrations/018_corroboration_count.sql` — ALTER TABLE notes ADD COLUMN
  corroboration_count INT DEFAULT 0
- `models.py` — `corroboration_count: int` field added to `NoteRecord`
- `parser.py` — parses `corroboration_count` from frontmatter
- `sync.py` — includes `corroboration_count` in `_upsert_note` INSERT/ON CONFLICT

### Phase 2: Graph Maintenance — bulk rebuild

Status: **not started**.

Planned deliverables:
- `src/second_brain/maintenance.py` — new module
- `rebuild_wiki_graph(connection, config) -> GraphRebuildResult`
- `detect_orphan_nodes(connection) -> list[str]`
- `daily_consolidation` job wired to call `rebuild_wiki_graph` before
  trust/freshness computation

### Phase 3: Lifecycle Auditing — advancement and drift

Status: **not started**.

Planned deliverables:
- `audit_lifecycle_states(connection, config) -> LifecycleAuditResult` in
  `maintenance.py`
- `MaintenanceConfig` dataclass in `config.py` with threshold fields
- `daily_consolidation` wired to call `audit_lifecycle_states`

### Phase 4: Importance Scoring — unified signal

Status: **not started**.

Planned deliverables:
- `migrations/019_note_importance.sql` — `note_importance` table
- `compute_importance_scores(connection) -> dict[str, float]` in `maintenance.py`
- `daily_consolidation` wired to call after trust/freshness
- `retrieval.py` updated to load importance_score and include in
  `retrieval_hit_features`

### Phase 5: Wiki Health Stats

Status: **not started**.

Planned deliverables:
- `compute_wiki_health_stats(connection) -> WikiHealthStats` in `maintenance.py`
- `weekly_hygiene` wired to call
- `write_maintenance_overview` in `ops.py` enhanced with `WikiHealthStats`

Note: semantic dedup is subsumed into Phase 6 (threshold θ=0.92 in
`run_embedding_neighbor_pass`). Phase 5 focuses on health stats only.

---

## Layer 1 — Cross-Wiki Linking (per-promotion + daily)

### Prerequisite: embedding model

Status: **not started**.

Before Phases 6–7 can run: `ollama pull nomic-embed-text` and set
`config.embeddings.enabled = true`. See
`knowledge-maintenance-embedding-model.md` for rationale and RL training path.

### Phase 6: Embedding-Based Neighbor Linking

Status: **not started**.

Frequency: per-promotion + daily backfill.

Planned deliverables:
- `migrations/018_note_embedding_scans.sql` — tracking table (or next available number)
- `find_embedding_neighbors(conn, config, note_id, ...)` in `maintenance.py`
- `assert_similar_to_link(conn, config, src_id, tgt_id, ...)` in `maintenance.py`
- `run_embedding_neighbor_pass(conn, config, note_id) -> int` in `maintenance.py`
- `run_embedding_neighbor_backfill(conn, config) -> int` in `maintenance.py`
- Per-promotion wire-up in `promotion/worker.py` after `_apply_intra_source_relations`
- Daily wire-up in `daily_consolidation`

### Phase 7: LLM Pattern Validation and Refinement

Status: **not started**.

Frequency: daily.

Planned deliverables:
- `run_cluster_validation(conn, config, llm_provider) -> ClusterValidationResult`
  in `maintenance.py`
- `maintenance_training_pairs` table migration for RL signal accumulation
- Daily wire-up in `daily_consolidation` after Phase 6 backfill

---

## Layer 3 — Raw Re-Visitation (weekly)

### Phase 8: Raw Re-Visitation and Cross-Wiki Context Injection

Status: **not started**.

Frequency: weekly.

Planned deliverables:
- `migrations/020_extraction_versioning.sql` — add `extraction_schema_version`
  and `extraction_model_version` columns to `extraction_queue`
- Update `mark_finished()` in `queue.py` to write both version columns
- `run_raw_revisitation(conn, config) -> RevisitationResult` in `maintenance.py`
- Weekly wire-up in `weekly_hygiene`

---

## Test Summary

`tests/test_knowledge_maintenance.py`: **not yet written**.

Will cover all functions in `maintenance.py` across Phases 1–8.

---

## Architectural Conclusion

The promotion layer delivers notes to `brain/wiki/`. The knowledge maintenance
layer ensures the wiki ages without silent drift. Three sub-layers at different
frequencies complete the pipeline:

```text
west flange
    → LLM/VLM extraction (intra-source relations via candidate index)
    → promotion gate → decision engine → renderer → atomic write
    → sync_vault + embed_backfill
    → Layer 1, per-promotion: embedding neighbor pass (Phase 6)
    → Layer 2, daily: graph rebuild + lifecycle audit + trust/freshness
                      + cluster validation (Phase 7) + importance scoring
    → Layer 2, weekly: wiki health stats
    → Layer 3, weekly: raw re-visitation + cross-wiki context injection (Phase 8)
```

Layer 2 (Phases 1–5) has no embedding model dependency and can ship first.
Layers 1 and 3 (Phases 6–8) require the embedding model and a mature wiki.
