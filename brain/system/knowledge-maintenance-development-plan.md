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

The precursor `daily_consolidation` and `weekly_hygiene` daemon jobs have been
removed as a clean slate. Their trust/freshness, contradiction detection, and
review routing logic will be rebuilt inside this layer's job structure.

This governing file defines scope, vocabulary, invariants, and phase order.
Implementation details live in the phase files.

Layers are ordered innermost-to-outermost, lightest-to-heaviest:

### Layer 1 — Cross-Wiki Linking (per-ingestion + scheduled sweep)

6. [Phase 6: Embedding-Based Neighbor Linking](knowledge-maintenance-development-plan-phase6.md)
7. [Phase 7: LLM Pattern Validation and Refinement](knowledge-maintenance-development-plan-phase7.md)

### Layer 2a — Pattern Discovery (batched trigger: N≥10 new notes OR T≥1hr)

Discovers new archetypes from accumulated notes using LLM-free detection channels. Runs
in the same daemon trigger as Layer 2b (both are batched together). The output of Layer 2a
is a scored set of archetype candidates — not individual links. Layer 2b consumes these
archetypes on the next wiki-wide pass to assert typed cross-wiki relations.

**Primary detection channels (LLM-free):**
- **Channel 2 — Role-vocabulary cosine clustering**: each note is projected into a 9-dim
  role vector using the immutable role vocabulary (see [Role Vocabulary](#role-vocabulary)
  below). Notes that cluster by role signature share structural motifs independent of
  surface vocabulary. Output: archetype candidates keyed on dominant role signature.
- **Channel 3 — Failure/resolution symmetry**: scans `incident`-type notes for shared
  failure-signature tokens against `procedure`-type notes for matching resolution-signature
  tokens. Pairs with reciprocal signatures are archetype candidates for the
  `same_failure_mode` relation family.

Channel 1 (deductive via root logics) is deferred — requires the full inference engine.

LLM is used only to name and validate archetype candidates produced by Channels 2 and 3,
not to drive primary detection.

Design authority: `development/humanoid-MnemonicOS-core-blueprint.md`, sections 3.4
(Pattern two-layer), 3.5 (Role vocabulary + inference engine), 6.4a (detection channels).

No separate phase file yet — currently designed as a Phase 7 extension once Layer 1
is stable.

### Layer 2b — Wiki Governance (batched trigger: N≥10 new notes OR T≥1hr)

> **Note:** Phase 1 (corroboration_count in DB) has been moved to the promotion layer
> roadmap as an amendment (`promotion-layer-development-roadmap.md`). It belongs there
> because `renderer.py` already produces the value and `sync_vault` already fires inline
> post-promotion — only the DB projection was missing. The phase file
> `knowledge-maintenance-development-plan-phase1.md` is preserved as a reference.

2. [Phase 2: Graph Maintenance — bulk rebuild](knowledge-maintenance-development-plan-phase2.md)
3. [Phase 3: Lifecycle Auditing — advancement and drift](knowledge-maintenance-development-plan-phase3.md)
4. [Phase 4: Importance Scoring — unified signal](knowledge-maintenance-development-plan-phase4.md)
5. [Phase 5: Semantic Dedup and Wiki Health](knowledge-maintenance-development-plan-phase5.md)

### Layer 3 — Raw Re-Visitation (daily 2AM + M=5 eager trigger)

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
- Precursor `daily_consolidation` and `weekly_hygiene` daemon jobs have been
  removed (clean slate). Their logic (trust/freshness, contradiction detection,
  duplicate alias detection, review routing, relearn task creation) will be
  rebuilt in this layer's job structure.
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

## Role Vocabulary

Immutable 9-term feature space for Channel 2 pattern detection. Source: blueprint section 3.5.
Never updated by any automated job.

| Term | Meaning |
|---|---|
| `source_of_truth` | Authoritative record — other notes derive from or cite this |
| `derived_artifact` | Produced by or dependent on another note |
| `sync_mechanism` | Describes a process that keeps two things in sync |
| `failure_mode` | Documents a way something can fail |
| `dependency` | Something that must exist/hold for something else to work |
| `output` | Result or artifact produced by a process |
| `tradeoff` | Documents a tension between two desiderata |
| `scope_marker` | Defines the boundary or applicability of a concept |
| `negation_marker` | Explicitly contradicts or limits another claim |

Each note is scored against all 9 terms to produce a role vector. Role vectors
are compared by cosine similarity to find notes with matching structural roles
regardless of domain vocabulary.

## Glossary

- **Knowledge maintenance layer**: the MnemonicOS layer that treats the wiki as
  a living graph and governs it holistically, independent of any individual
  candidate intake event. Comprises four sub-layers at different frequencies,
  ordered innermost-to-outermost (lightest to heaviest).
- **Layer 1 — Cross-Wiki Linking**: per-ingestion embedding neighbor search plus
  a scheduled sweep. Builds links using current known patterns — fires immediately
  after new notes are promoted, and on a recurring schedule to backfill.
  Asserts `similar_to` and `structurally_isomorphic` typed cross-wiki links.
- **Layer 2a — Pattern Discovery**: batched-trigger job (fires when N≥10 new notes
  or T≥1hr since last run). Discovers new archetypes using LLM-free Channel 2
  (role-vocabulary cosine clustering) and Channel 3 (failure/resolution symmetry).
  LLM is used only to name and validate archetype candidates, not for primary detection.
  Produces a scored archetype set consumed by Layer 2b on its next wiki-wide pass.
  The self-evolving loop lives here (Layers 1 + 2a): new archetypes emerge from
  accumulated evidence without requiring new ingestion.
- **Layer 2b — Wiki Governance**: batched-trigger job (same trigger as Layer 2a —
  N≥10 new notes OR T≥1hr). Bulk graph rebuild, lifecycle auditing, importance
  scoring, and wiki-wide link rerun applying archetypes identified by Layer 2a.
  DETERMINISTIC on fixed material — idempotent after convergence. Does not drive
  self-evolution; it propagates changes from Layers 1 and 2a.
- **Layer 3 — Raw Re-Visitation**: runs daily at 2AM (safety net) plus an eager
  trigger when a new archetype's occurrence_count exceeds M=5 (signals enough
  evidence to warrant re-processing raws immediately). Re-processes true raws
  through the extraction pipeline with updated schemas and cross-wiki context
  injection. Produces richer candidates from sources that have already been
  extracted once. Daily run ensures all new archetypes are applied to raws within
  24 hours even without the M threshold being reached.
  Long-term scalability burden — grows heavier as raw corpus expands; see final
  phase (Layer 3 Scalability Engineering).
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
  different ingestion sources. Relation type vocabulary follows blueprint section
  6.5: `same_failure_mode`, `analogy_to`, `contrast_with`, `generalizes`,
  `specializes`, `evidence_for`, `evidence_against`, `depends_on`, `supersedes`,
  `derived_from`, `challenged_by`, `similar_to`, `structurally_isomorphic`.
  Asserted by Layer 2b on archetype application; initial `similar_to` links seeded
  by Layer 1 embedding neighbor pass.
- **Semantic neighborhood**: the set of notes within cosine similarity θ of a
  query note in embedding space. Used to seed cross-wiki link candidates.
- **Pattern cluster**: a group of notes connected by `similar_to` links that
  share an abstract structural pattern discovered by LLM cluster validation.
- **Archetype**: the unified pattern type used by Layer 2a. Maps directly to
  blueprint section 3.4 "Pattern" two-layer structure:
  - **Layer A** (communicable surface): semantic cluster identity + structural
    motif label. Stable once discovered; persists in the archetype registry.
  - **Layer B** (role schema): maps note fields to role vocabulary terms. Refines
    over time as more notes join the archetype. Updated by the B-only routine.
  Each archetype record carries: `archetype_id`, `label`, `layer_a_description`,
  `layer_b_role_schema` (dict of field→role), `occurrence_count`, `score`,
  `relation_type` (from blueprint 6.5 vocabulary), `source_channel` (2 or 3).
  Graph edges that belong to an archetype carry: `archetype_id`,
  `archetype_score`, `instance_confidence`.
- **Abstract pattern**: see Archetype above. "Abstract pattern" is the informal
  term; "archetype" is the formal type used in implementation.
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
- **Role vocabulary**: 9 immutable, domain-independent terms from blueprint
  section 3.5 used as the feature space for Channel 2 role-vector clustering.
  Never auto-updated. Terms: `source_of_truth`, `derived_artifact`,
  `sync_mechanism`, `failure_mode`, `dependency`, `output`, `tradeoff`,
  `scope_marker`, `negation_marker`. Each note is projected into a 9-dim vector
  by scoring how strongly each term applies to the note's content. Notes sharing
  dominant role signatures form archetype candidates.
- **B-only routine**: a 5-hour scheduled job that refines the `layer_b_role_schema`
  of existing archetypes only (no new archetype discovery, no wiki-wide link pass).
  Two output modes: (1) score update only → bulk UPDATE to all `graph_edges` rows
  with that `archetype_id`, no Layer 2b trigger; (2) new archetype discovered →
  triggers Layer 2b, and if `occurrence_count > M=5` also flags Layer 3 eager run.
- **Pattern detection channels**: the three detection methods described in blueprint
  section 6.4a. Channel 1 (deductive via root logics) is deferred. Channel 2
  (role-vocabulary cosine, LLM-free) and Channel 3 (failure/resolution symmetry,
  LLM-free) are the initial implementation targets for Layer 2a.
- **maintenance jobs**: the scheduled daemon jobs corresponding to the four
  maintenance sub-layers. Locked schedule: Layer 1 per-ingestion + sweep;
  Layers 2a+2b batched trigger (N≥10 new notes OR T≥1hr); B-only routine every
  5 hours; Layer 3 daily 2AM + eager trigger if occurrence_count > M=5.
  Replace the removed `daily_consolidation` and `weekly_hygiene` precursor jobs.

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

Layer 2b (Phases 2–5, wiki governance) has no embedding model dependency and can
ship before the embedding model is installed.

### Raw re-visitation (Phase 8 of maintenance layer) — requires mature wiki

Cross-wiki context injection is only useful once the wiki has enough notes to
provide meaningful context. Phase 8 should be treated as a Phase 2 deliverable —
it will have little effect on an empty or sparse wiki.

## Phase ordering rationale

### Layer 2b phases (2–5)

`corroboration_count` in DB (formerly Phase 1) is now a promotion-layer amendment
that ships independently. Once it lands, Phases 2–3 are an unblocked dependency
chain: graph rebuild (Phase 2) must run before lifecycle audit (Phase 3) so that
connectivity-dependent signals (`linked_incident_count`) are accurate when
advancement candidates are queried.

Phase 4 (importance scoring) reads from already-computed graph, trust, and
freshness data — it must run last among the daily Layer 2b functions.

Phase 5 (wiki health) has no hard dependency on Phases 2–4 but benefits from
accurate graph and lifecycle state.

Phases 2–3 ship together. Phase 4 ships next. Phase 5 ships last.

### Layer 1 phases (6–7)

Phase 6 (embedding neighbor linking) fires per-ingestion and on a scheduled
backfill sweep. Requires the embedding model installed and
`config.embeddings.enabled = True`. Produces `similar_to` links consumed by
Phase 7 and eventually `structurally_isomorphic` links once Phase 7 is extended
with a structural role extraction sub-pass.

Phase 7 (LLM cluster validation + pattern naming) validates `similar_to` edges,
names abstract patterns, and routes merge candidates to review. The pattern-naming
sub-pass is the seed of Layer 2a — initially bundled with Phase 7, split into an
independent Layer 2a job once the pattern vocabulary is large enough to warrant
its own schedule.

Phase 6 ships before Phase 7. Both require the embedding model operational.

### Layer 2a

No dedicated phase yet. Ships as a Phase 7 extension. The trigger to split it out
is when accumulated pattern labels stabilize and benefit from running at a higher
frequency than full cluster validation.

### Layer 3 phase (8)

Phase 8 (raw re-visitation) has no hard dependency on earlier phases but benefits
from a sufficiently populated wiki for context injection. Ships after Phases 2–5
are stable. Runs daily at 2AM as a safety net, plus an eager trigger when any
archetype's occurrence_count exceeds M=5. The daily run ensures all new archetypes
are applied to raws within 24 hours even without M being reached.

Phase 9 (Layer 3 scalability engineering) is deferred until raw corpus growth
makes nightly full re-visitation noticeably slow. It does not change the semantic
contract of Phase 8 — only the efficiency of scheduling.

### Overall delivery order

1. Promotion layer amendment: `corroboration_count` in DB (unblocked, ships first)
2. Layer 2b — Phases 2–3 (graph rebuild + lifecycle audit) — one session
3. Layer 2b — Phase 4 (importance scoring) — one session
4. Layer 2b — Phase 5 (wiki health) — one session
5. Install embedding model (`nomic-embed-text`) — prerequisite for Phases 6–7
6. Layer 1 — Phase 6 (neighbor linking) — one session
7. Layer 1 + 2a — Phase 7 (cluster validation + Channel 2/3 pattern discovery) — one session
8. Layer 3 — Phase 8 (raw re-visitation, daily 2AM + M=5 eager trigger) — one session
9. Layer 2a — split pattern discovery from Phase 7 into own job (post-Phase 7)
10. [Final] Phase 9 — Layer 3 Scalability Engineering (deferred, triggered by corpus growth)

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
          → scored archetype candidates (LLM validates/names only)
    → Layer 2b, same batched trigger:
          graph rebuild + lifecycle audit + importance scoring
          + wiki-wide link rerun applying current archetypes (Phases 2–5)
    → B-only routine, every 5 hours:
          refine layer_b_role_schema of existing archetypes
          → score updates propagated to graph_edges
          → if new archetype found: trigger Layer 2b + eager Layer 3 if M≥5
    → Layer 3, daily 2AM + eager trigger (occurrence_count > M=5):
          raw re-visitation + cross-wiki context injection (Phase 8)
    → [final phase] Layer 3 Scalability Engineering:
          version-based incremental + pattern-relevance gating
          (long-term, grows heavier as raw corpus expands)
```

Design authority: `development/humanoid-MnemonicOS-core-blueprint.md` governs
the pattern/archetype model, role vocabulary, inference layers, and detection
channels. Implementation decisions diverging from the blueprint must be noted here.

Layer 2b (Phases 2–5) has no embedding model dependency and ships first.
Layers 1, 2a, and 3 (Phases 6–8) require the embedding model and a sufficiently
populated wiki. The self-evolving loop lives in Layers 1 and 2a — Layer 2b is
deterministic/idempotent on fixed material and propagates their output.
