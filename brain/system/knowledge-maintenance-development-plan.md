# Knowledge Maintenance Layer Development Plan

## Purpose

This plan directly follows
`brain/system/promotion-layer-development-roadmap.md`. The promotion layer owns
the candidate-to-wiki-note path: gate → decision → render → atomic write. It
terminates at a promoted wiki note on disk and a ledger row in
`promotion_decisions`.

The knowledge maintenance layer starts there. It operates on the **full wiki**,
not on individual candidates. It treats the wiki as a living graph and governs it
holistically: rebuilding relation graphs, auditing lifecycle states across all
notes, computing unified importance signals, and building cross-wiki links by
finding notes that share abstract patterns or semantic neighborhoods. It also
periodically re-visits original true raws to extract richer structure as the wiki
and its schemas mature.

The existing `daily_consolidation` and `weekly_hygiene` daemon jobs are precursors.
They already do trust/freshness computation, contradiction detection, and review
routing. This layer fills the gaps they do not yet address.

This governing file defines scope, vocabulary, invariants, and phase order.
Implementation details live in the phase files:

### Layer 2 — Wiki Governance (foundational, daily/weekly)

1. [Phase 1: Foundation — corroboration_count in DB](knowledge-maintenance-development-plan-phase1.md)
2. [Phase 2: Graph Maintenance — bulk rebuild](knowledge-maintenance-development-plan-phase2.md)
3. [Phase 3: Lifecycle Auditing — advancement and drift](knowledge-maintenance-development-plan-phase3.md)
4. [Phase 4: Importance Scoring — unified signal](knowledge-maintenance-development-plan-phase4.md)
5. [Phase 5: Semantic Dedup and Wiki Health](knowledge-maintenance-development-plan-phase5.md)

### Layer 1 — Cross-Wiki Linking (per-promotion + daily)

6. [Phase 6: Embedding-Based Neighbor Linking](knowledge-maintenance-development-plan-phase6.md)
7. [Phase 7: LLM Pattern Validation and Refinement](knowledge-maintenance-development-plan-phase7.md)

### Layer 3 — Raw Re-Visitation (weekly)

8. [Phase 8: Raw Re-Visitation and Cross-Wiki Context Injection](knowledge-maintenance-development-plan-phase8.md)

### Supporting decision

- [Embedding Model Decision](knowledge-maintenance-embedding-model.md)

## Verified Breakpoint

Current state at start of this layer:

- Full repository test suite passes: 291 tests.
- `tests.test_promotion_layer` passes (43 tests).
- `tests.test_extraction_layer` passes.
- `tests.test_intra_source_relations` passes (24 tests).
- All intake paths (artifact, session, episode) trigger full extraction → promotion
  immediately via `trigger_pipeline()`.
- Intra-source relations (LLM/VLM Phase 8) are fully implemented: extraction queue
  stores `intra_source_relations_json` (index-based format), promotion worker applies
  them to note frontmatter and graph edges in a bundle-aware second pass.
- `daily_consolidation` and `weekly_hygiene` jobs exist and run: trust/freshness
  signals, contradiction detection, duplicate alias detection, review routing, and
  relearn task creation are all operational.
- `rebuild_note_graph()` in `graph.py` exists and runs per-note at promotion time.
- `compute_note_trust_stats()` and `compute_note_freshness()` in `trust.py` are
  fully implemented and write to `note_trust` and `note_freshness` tables.
- `encode_text()` in `semantics.py` exists but requires an embedding model in Ollama.
  No embedding model is currently installed (`nomic-embed-text` is the planned pull target).
- `corroboration_count` lives in markdown frontmatter only — not in the DB.
- No job does a bulk graph rebuild across all wiki notes.
- No job audits lifecycle states for advancement.
- No unified importance score exists.
- No cross-wiki neighbor linking exists (requires embedding model).
- No raw re-visitation job exists.

## Glossary

- **Knowledge maintenance layer**: the MnemonicOS layer that treats the wiki as
  a living graph and governs it holistically, independent of any individual
  candidate intake event. Comprises three sub-layers at different frequencies.
- **Layer 1 — Cross-Wiki Linking**: per-promotion embedding neighbor search plus
  daily LLM cluster validation. Finds notes that share abstract semantic patterns
  regardless of shared vocabulary, asserts `similar_to` typed cross-wiki links,
  and surfaces merge candidates.
- **Layer 2 — Wiki Governance**: the foundational daily/weekly maintenance jobs.
  Bulk graph rebuild, lifecycle auditing, importance scoring, semantic dedup, and
  wiki health reporting. Treats the wiki as a living graph and governs it
  holistically.
- **Layer 3 — Raw Re-Visitation**: weekly job that re-processes true raws through
  the extraction pipeline with updated schemas and cross-wiki context injection.
  Produces richer candidates from sources that have already been extracted once.
- **Bulk graph rebuild**: iterating all active wiki notes and rebuilding their
  graph edges from the entity_relations in their frontmatter, repairing any drift
  since the last promotion.
- **Lifecycle audit**: scanning all active notes to advance lifecycle states
  (draft → verified → canonical) when accumulated corroboration thresholds are
  met, without waiting for new promotions.
- **Lifecycle drift**: a note remaining in `draft` for longer than the configured
  threshold without any corroboration — a signal of orphaned or unevidenced
  content.
- **Importance score**: a unified per-note signal combining graph connectivity,
  retrieval trust, and freshness. Used to prioritize what surfaces first in
  retrieval and what to curate in maintenance.
- **Connectivity score**: a normalized function of a note's in-degree (notes that
  point to it) and out-degree (notes it points to) in the relation graph.
- **Cross-wiki link**: a typed relation between notes that originated from
  different ingestion sources. Asserted by embedding similarity; relation type
  refined by LLM cluster validation.
- **Semantic neighborhood**: the set of notes within cosine similarity θ of a
  query note in embedding space. Used to seed cross-wiki link candidates.
- **Pattern cluster**: a group of notes connected by `similar_to` links that
  share an abstract structural pattern discovered by LLM cluster validation.
- **Abstract pattern**: a structural relationship (e.g., "predator pursuing prey")
  that transcends surface vocabulary but is detectable by embedding similarity
  plus LLM naming. Two notes may share an abstract pattern without sharing any
  keywords.
- **Semantic dedup**: comparing note summaries via embeddings to find
  conceptually identical notes that have different titles and would not be caught
  by alias collision detection. Subsumes into Phase 6 at a higher threshold (θ=0.92).
- **Wiki health**: a structured snapshot of the wiki's state: note counts by
  type, lifecycle state, and freshness state; graph connectivity statistics;
  orphan counts; and lifecycle transitions made in the current maintenance run.
- **Orphan node**: a graph node (note or entity) with no inbound or outbound
  edges after graph rebuild.
- **Stale edge**: a graph_edges row whose target_id no longer matches any active
  note in the notes table.
- **corroboration_count**: the number of times a wiki note has been confirmed or
  updated by an independent extraction candidate. Stored in markdown frontmatter
  and, after Phase 1, also in the notes DB table.
- **lifecycle_state**: the current maturity state of a note.
  `draft` → `verified` (corroboration_count ≥ 2) → `canonical` (≥ 4).
  `contested` and `archived` are terminal states reached by review or archival.
- **Raw re-visitation**: re-queueing a source's true raw through the extraction
  pipeline when its extraction is stale due to schema version drift or model change.
- **Cross-wiki context injection**: providing the LLM extraction prompt with
  summaries of existing wiki notes related to the same source, so re-extracted
  candidates are richer and conflict-aware.
- **nomic-embed-text**: the planned embedding model for neighbor search.
  137M parameters, 768 dimensions, Apache-2.0, BERT-style (fine-tunable).
- **Contrastive fine-tuning**: the RL training path for the embedding model.
  Positive pairs from LLM-confirmed `similar_to` links; negative pairs from
  LLM-rejected links. Uses SimCSE / InfoNCE loss.
- **maintenance.py**: the new module housing all bulk wiki-maintenance functions.
- **MaintenanceConfig**: the new config section in `AppConfig` governing
  thresholds for this layer.
- **daily_consolidation**: the 3 AM daemon job. After this layer: runs graph
  rebuild, lifecycle audit, importance scoring, and LLM cluster validation in
  addition to existing work.
- **weekly_hygiene**: the Sunday 4 AM daemon job. After this layer: runs wiki
  health stats and raw re-visitation in addition to everything in
  daily_consolidation.

## Invariants

1. **Maintenance jobs never write extraction candidates or promotion decisions.**
   They operate only on wiki notes (disk + notes table + graph tables + trust
   tables + importance table).
2. **Graph rebuild is idempotent.** Running it twice in a row produces the same
   graph state as running it once.
3. **Lifecycle advancement writes back to both the markdown file and the DB.**
   Frontmatter and DB row must remain consistent.
4. **Importance scoring reads from already-computed trust and freshness signals.**
   It does not re-derive them; it must run after `compute_note_trust_stats` and
   `compute_note_freshness` in the job ordering.
5. **Semantic dedup and cross-wiki merge detection never auto-merge notes.** Both
   route candidates to `brain/wiki/review/` via `create_review_item`. Merges
   require human approval.
6. **Dry-run mode is respected at every maintenance function boundary.** No disk
   write, DB write, or review item creation occurs when `dry_run=True`.
7. **Cross-wiki similarity links respect note type boundaries.** `similar_to`
   links are only asserted between notes of the same type (concept↔concept,
   procedure↔procedure, etc.) to prevent spurious cross-type matches.
8. **Raw re-visitation never deletes original extracted candidates.** Re-queued
   items produce new extraction queue rows. The original rows remain intact.
   The promotion gate handles corroboration_count increments for duplicates.
9. **Cross-wiki context injection is read-only.** Wiki summaries injected into
   re-extraction prompts are never modified by the re-extraction process itself;
   only the new extracted candidates can create new wiki notes or corroborate
   existing ones through the normal promotion path.

## Existing infrastructure reused (do not rebuild)

| Component | File | What it does |
|---|---|---|
| `compute_note_trust_stats` | `trust.py:102` | Writes usefulness_score to `note_trust` |
| `compute_note_freshness` | `trust.py:354` | Writes staleness_score to `note_freshness` |
| `train_rerank_model` | `trust.py:247` | Weekly rerank weight training |
| `rebuild_note_graph` | `graph.py:107` | Per-note graph rebuild — reused by bulk job |
| `graph_expand` | `graph.py:186` | Graph traversal for retrieval |
| `_decision_contradictions` | `jobs.py:358` | Active decision overlap detection |
| `_claim_contradictions` | `jobs.py:377` | Multi-value attribute conflict detection |
| `_procedure_conflicts` | `jobs.py:401` | Procedure-vs-newer-incident detection |
| `create_review_item` | `review.py` | Creates review items in `brain/wiki/review/` |
| `load_open_review_items` | `ops.py` | Lists open review items |
| `refresh_active_file` | `ops.py` | Rewrites `brain/system/ACTIVE.md` |
| `write_maintenance_overview` | `ops.py` | Writes weekly maintenance report |
| `encode_text` | `semantics.py` | Embedding generation — reused for neighbor linking |
| `parse_note` | `parser.py:187` | Parses markdown frontmatter → NoteRecord |
| `sync_vault` | `sync.py` | Full or incremental vault sync to DB |
| `mark_finished` | `extraction/queue.py` | Stores extraction results — reused by raw re-visitation |
| `ingest_artifact` | `ingest.py` | Artifact intake — re-visitation re-uses for raw re-queue |
| `trigger_pipeline` | `pipeline.py` | Triggers extraction+promotion — called by re-visitation |

## Upstream dependencies

### LLM/VLM Phase 8 (intra-source relations) — COMPLETE

The graph rebuild (Phase 2) iterates all wiki notes and rebuilds edges from their
`entity_relations` frontmatter. Those relations come from the promotion worker's
bundle-aware second pass (LLM/VLM Phase 8), which is now fully implemented.

### Embedding model — required for Phases 6 and 7

Cross-wiki neighbor linking (Phase 6) and LLM cluster validation (Phase 7) both
require `encode_text()` in `semantics.py` to be operational. This requires an
embedding model to be available via Ollama. The planned pull target is
`nomic-embed-text`. See [Embedding Model Decision](knowledge-maintenance-embedding-model.md).

Phases 1–5 (Layer 2 wiki governance) have no embedding model dependency and can
ship before the embedding model is installed.

### Raw re-visitation (Phase 8 of maintenance layer) — requires mature wiki

Cross-wiki context injection is only useful once the wiki has enough notes to
provide meaningful context. Phase 8 should be treated as a Phase 2 deliverable —
it will have little effect on an empty or sparse wiki.

## Phase ordering rationale

### Layer 2 phases (1–5)

Phases 1–3 are a single dependency chain: the DB must carry `corroboration_count`
(Phase 1) before lifecycle audit can query advancement candidates (Phase 3).
Graph rebuild (Phase 2) runs before trust/freshness in the job so that
connectivity-dependent freshness signals (linked_incident_count) are accurate.

Phase 4 (importance scoring) reads from already-computed graph, trust, and
freshness data — it must run last among the daily functions.

Phase 5 (wiki health) runs weekly only. It has no hard dependency on Phases 1–4
but benefits from accurate graph and lifecycle state.

Phases 1–3 ship together. Phase 4 ships next. Phase 5 ships last.

### Layer 1 phases (6–7)

Phase 6 (embedding neighbor linking) runs per-promotion. It requires the embedding
model to be installed and `config.embeddings.enabled = True`. It produces `similar_to`
links that are then consumed by Phase 7.

Phase 7 (LLM cluster validation) runs daily. It reads `similar_to` graph edges
from Phase 6, validates them, names abstract patterns, and routes merge candidates
to review. Phase 7 is only meaningful if Phase 6 has populated `similar_to` edges.

Phase 6 ships before Phase 7. Both require the embedding model to be operational.

### Layer 3 phase (8)

Phase 8 (raw re-visitation) runs weekly. It has no hard dependency on any earlier
phase but benefits from cross-wiki context injection being meaningful (i.e., the
wiki is sufficiently populated). It should ship after Phases 1–5 are stable.

### Overall delivery order

1. Phases 1–3 (wiki governance foundation) — one session
2. Phase 4 (importance scoring) — one session
3. Phase 5 (wiki health) — one session
4. Install embedding model (`nomic-embed-text`) — prerequisite for Phases 6–7
5. Phase 6 (neighbor linking) — one session
6. Phase 7 (cluster validation) — one session
7. Phase 8 (raw re-visitation) — one session
