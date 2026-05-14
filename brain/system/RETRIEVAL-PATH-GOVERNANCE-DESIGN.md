# Retrieval Path Governance Design

## Purpose

Single reference for MnemonicOS retrieval architecture. Maps every stage from
query parsing to memory packet emission, links each stage's governing plan and
roadmap, defines what the retrieval path owns and does not own, and documents
the DL north star vision.

**Retrieval path = east flange + internal retrieval stages.**
The term "east flange" refers specifically to Stage 1 (Query Parsing) — the
query intake boundary, symmetric to the west flange on the ingestion side.

---

## Retrieval Path Diagram

```
Natural Language Query  +  QueryHints (type hint, temporality, budget, scope)
        │
        │  [mnemonic_context.sh · POST /v1/retrieve · resolve_task_context]
        ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 1 · East Flange  (query intake boundary)     │
│  retrieval-east-flange-plan.md                      │
│  retrieval-east-flange-roadmap.md                   │
│  Tokenizes query, classifies type, infers           │
│  temporality, extracts anchors, expands terms.      │
│  Emits typed ParsedQuery. Single alias-table read.  │
└────────────────────┬────────────────────────────────┘
                     │ ParsedQuery
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 2 · Multi-Channel Recall                     │
│  retrieval-recall-plan.md                           │
│  Exact match · BM25 · Semantic · Graph · Archive.   │
│  Produces typed CandidatePool. Archive channel      │
│  gated by policy. Single query-vector encode.       │
└────────────────────┬────────────────────────────────┘
                     │ CandidatePool (wiki + archive, separate)
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 3 · Signal Integration                       │
│  retrieval-signal-integration-plan.md               │
│  Loads trust, freshness, importance per candidate   │
│  from maintenance layer (read-only). Computes       │
│  quality gate (quality_pass) and freshness          │
│  multiplier. Emits NoteSignals per candidate.       │
└────────────────────┬────────────────────────────────┘
                     │ CandidatePool + NoteSignals
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 4 · Reranking                                │
│  retrieval-reranking-plan.md                        │
│  Multi-factor scoring: BM25, semantic, graph,       │
│  type prior, freshness, trust, importance, alias.   │
│  Freshness multiplier applied. Weights from         │
│  rerank_weights table (trained or config default).  │
│  Separate wiki and archive ranking lists.           │
└────────────────────┬────────────────────────────────┘
                     │ RankedHits (wiki + archive)
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 5 · Assembly                                 │
│  retrieval-assembly-plan.md                         │
│  Composes bounded packet slots: primary_memories,   │
│  same_episode_context, related_*, archive_context,  │
│  contradictions, warnings, assembly_notes.          │
│  Cross-slot dedup. Context budget enforcement.      │
└────────────────────┬────────────────────────────────┘
                     │ RawPacket
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 6 · Assessment                               │
│  retrieval-assessment-plan.md                       │
│  Classifies retrieval_state. Derives recommendation │
│  and verification_action. Quality gate across all   │
│  primary hits. Pure computation, no side-effects.   │
└────────────────────┬────────────────────────────────┘
                     │ AssessedPacket
                     ▼
┌─────────────────────────────────────────────────────┐
│  STAGE 7 · Emission                                 │
│  retrieval-emission-plan.md                         │
│  retrieval-emission-roadmap.md                      │
│  Formats AssessedPacket as JSON or Markdown from    │
│  one render model. Surfaces archive_context         │
│  separately. Emits focused requery instructions     │
│  when requested. Logs directive metrics. Never      │
│  raises. Automatic retry is orchestrator-owned,     │
│  budgeted, and cutoff on no-new results.            │
└─────────────────────────────────────────────────────┘
                     │
                     ▼
            Bounded Memory Packet
     (primary · episode · related · archive ·
      contradictions · warnings · assessment)
```

### Cross-cutting concern

```
retrieval-control-signal-quality-roadmap.md
  ↳ Improves signal quality fed into Stages 3–6:
    gene strings in summary fields, content quality gate,
    chain_id episode arc machinery.
```

---

## Stage Descriptions

### Stage 1 — East Flange (Query Parsing)
**Plan:** [retrieval-east-flange-plan.md](retrieval-east-flange-plan.md)
**Roadmap:** [retrieval-east-flange-roadmap.md](retrieval-east-flange-roadmap.md)

The query intake boundary. Accepts raw natural language + optional caller hints.
Tokenizes, classifies query type (6 types), infers temporality (4 states including
`unknown`), extracts anchor terms via alias table, expands terms via synonym map.
Returns a frozen `ParsedQuery` struct — the single typed input for Stage 2.
One alias-table read permitted; no writes.

Internal phases:
[P1 ParsedQuery contract](retrieval-east-flange-plan-phase1.md) ·
[P2 Caller identity](retrieval-east-flange-plan-phase2.md) ·
[P3 Persona classifier](retrieval-east-flange-plan-phase3.md) ·
[P4 Semantic type classifier](retrieval-east-flange-plan-phase4.md) ·
[P5 Temporality](retrieval-east-flange-plan-phase5.md) ·
[P6 Anchor extraction](retrieval-east-flange-plan-phase6.md) ·
[P7 Term expansion](retrieval-east-flange-plan-phase7.md) ·
[INT Assembly](retrieval-east-flange-plan-int.md)

---

### Stage 2 — Multi-Channel Recall
**Plan:** [retrieval-recall-plan.md](retrieval-recall-plan.md)
**Roadmap:** [retrieval-recall-roadmap.md](retrieval-recall-roadmap.md)

Runs five retrieval channels in sequence against the canonical wiki and raw
archive: exact alias/title match, BM25 full-text (FTS5), semantic vector
similarity, graph traversal from seed notes, and archive BM25 fallback.
Archive channel is gated by policy (empty wiki pool + temporal/debug query).
Query vector encoded **once** per call and reused. Produces typed `CandidatePool`
with wiki and archive candidates structurally separated.

---

### Stage 3 — Signal Integration
**Plan:** [retrieval-signal-integration-plan.md](retrieval-signal-integration-plan.md)
**Roadmap:** [retrieval-signal-integration-roadmap.md](retrieval-signal-integration-roadmap.md)

Batch-loads maintenance signals for all candidates:
- `note_trust.usefulness_score` → trust signal
- `note_freshness.freshness_state` + `staleness_score` → freshness signal
- `note_importance.importance_score` → importance signal (currently dead in legacy — fix here)
- `note_passive_feedback.feedback_state` + `confidence` → passive feedback signal
  after Maintenance migration 026

Applies content quality gate (`quality_pass: bool`) per candidate. Precomputes
freshness multiplier from freshness_state × query temporality. Emits `NoteSignals`
per candidate. No DB writes.

Boundary:
```
RecallCandidate + ParsedQuery
  -> Stage 3 Signal Integration
  -> SignalEnrichedCandidate
  -> Stage 4 Reranking
  -> RankedHits
```

Stage 3 answers: "What do we know about this candidate?" It may read title,
summary, compact control signal, and metadata, but it must not inspect full note
content for ranking decisions.

Passive feedback is an input to this stage only after maintenance has
materialized it into `note_passive_feedback` or downstream trust/freshness
signals. Stage 3 does not read raw sessions, extraction payloads, or
`promotion_decisions.retrieval_attribution_json`, and it performs no LLM
attribution.

---

### Stage 4 — Reranking
**Plan:** [retrieval-reranking-plan.md](retrieval-reranking-plan.md)
**Roadmap:** [retrieval-reranking-roadmap.md](retrieval-reranking-roadmap.md)

Scores candidates using a two-layer multiplicative model (Phase 6):

```
# Relevance sub-score [0, 1] — query-dependent
relevance_score = (
  bm25_weight          * bm25_abs              # absolute BM25, not rank-normalized
  + semantic_weight    * semantic_score
  + graph_weight       * normalize_graph(graph_score, pool_max)
  + exact_alias_weight * exact_bonus
) / relevance_weight_sum

# Quality sub-score [0, 1] — node-intrinsic
quality_score = (
  type_prior_weight  * type_prior(query_type, note_type)
  + freshness_weight * freshness_score
  + trust_weight     * trust_score
  + importance_weight* normalize_importance(importance_score, pool_max)
  + quality_weight   * quality_pass
  + historical_bonus(temporality, freshness_state)
) / quality_weight_sum_max

# Composite used for ranking
composite_score = relevance_score × quality_score
final_score = composite_score    # backward-compatible alias
```

**Relevance gate**: candidates with `relevance_score < min_relevance_score`
(default 0.25) are suppressed before Stage 5. Exact matches are exempt
(`relevance_score = 1.0` set explicitly).

**Dynamic top-k** (Phase 4 updated): fixed `top_k = 5` replaced by pool-fraction
strategy — `max(min_hits, ceil(pool * pool_fraction))` capped at `max_hits = 15`
and a token budget backstop. See [retrieval-reranking-plan-phase4.md].

`bm25_abs = min(bm25_score / bm25_saturation, 1.0)` using the raw FTS5 score.
Weights loaded from `rerank_weights` (model `east-flange-v1`); config defaults
are the cold-start fallback. Archive candidates scored separately (BM25 rank only).
Training triggered by `scripts/mnemonic_eval_train.sh` (daily daemon job) when
`sample_count ≥ min_training_samples`.

Stage 4 answers: "Given the integrated signals, where should this candidate
rank?" It consumes `SignalEnrichedCandidate` only; it must not load maintenance
tables or full note bodies.

Feedback is hierarchical:
- `hit`: candidate appeared in recall/ranking output; weak exposure signal.
- `selected`: candidate was emitted into the packet/result; LFU-style exposure
  signal already represented by `retrieval_hits.selected`.
- `explicit_used`: caller or outcome assessment marked the note useful; strong positive
  relevance signal represented by `retrieval_hits.useful = 1`.
- `explicit_misleading`: caller or outcome assessment marked the note harmful; strong
  negative signal represented by `retrieval_hits.useful = 0`.

Stage 3/4 may log hit/selected feature rows for learning. Post-assessment and
runtime outcome capture own used/misleading labels. Do not promote mere exposure
into usefulness.

Most feedback is expected to be passive rather than explicit. Raw sessions may
later be promoted into evidence that a retrieved note was used, helpful,
stale, contradicted, or misleading. Extraction may emit
`retrieval_attribution`; promotion stores it in the ledger; maintenance is the
sole writer of `note_passive_feedback`. Stage 4 consumes only Stage 3 signals or
training labels derived from those maintenance-owned rows.

---

### Stage 5 — Assembly
**Plan:** retrieval-assembly-plan.md
**Roadmap:** retrieval-assembly-roadmap.md

Composes the `RawPacket` from ranked hits and episode context. Stage 5 is the
readout layer: it forms a coherent assembled note-like packet for the query
party while preserving enough machine-readable structure for Stage 6 assessment.
Slots:

| Slot | Contents |
|------|---------|
| `primary_memories` | top-k quality-passed wiki hits, type-bucket capped |
| `same_episode_context` | notes sharing `chain_id` with top primary hit |
| `related_by_topic` | graph-adjacent notes not in primary |
| `related_by_timeline` | before/after/supersedes-related notes |
| `related_by_similarity` | semantic neighbors from `note_embedding_scans` |
| `related_by_contrast` | `contrasts_with`-related notes |
| `archive_context` | archive fallback hits (always separate, labeled `source: raw_session`) |
| `contradictions` | supersedes/superseded_by, contested, or conflict evidence among assembled notes |
| `warnings` | quality-failed, stale, contested notes |
| `assembly_notes` | structured budget, archive-only, dedupe, body-reference, and handoff facts for Stage 6 |
| `functional_path` | reconstructed success/failure path when chain or relation evidence supports it |

Cross-slot deduplication enforced. Context budget applies to packet slots,
summaries, excerpts, metadata, and body references; full body hydration and
final truncation are Stage 7 responsibilities. Archive hits must not appear in
any slot except `archive_context`.

Stage 4 rank order is evidence priority, not necessarily readout order. Stage 5
must preserve rank metadata while arranging selected entries by explanatory
priority when maintenance links show a functional path: failures, diagnoses,
pivots, successful resolution, current reusable procedure, and cautions.

Stage 5 also owns bounded trace completion when the initially assembled path is
structurally incomplete. This is not a fresh recall loop. It is a deterministic
extension over explicit maintenance relations already reachable from assembled
notes: `chain_id`, `before`, `after`, `supersedes`, `failed_due_to`,
`attempts_to_solve`, `supports_solution`, `derived_from`, and direct
incident/decision/procedure links. Trace completion may add missing functional
roles such as goal, failed attempt, diagnosis, pivot, success, or caution, but
it must obey hard caps on relation families, depth, and total added nodes. It
must not run semantic expansion, archive search, or another Stage 2-4 pass.

The output of this pass is an in-memory assessment reservoir inside `RawPacket`.
The reservoir may be larger than the eventual user-facing message, but it stays
bounded and structured. Stage 6 can then choose which branch to emit without
rerunning Stages 2-5. Reservoir entries keep full provenance, slot assignment,
functional role, body reference, score metadata, and warnings; Stage 7 decides
final body hydration and truncation from Stage 6 instructions.

---

### Stage 6 — Assessment
**Plan:** [retrieval-assessment-plan.md](retrieval-assessment-plan.md)
**Roadmap:** [retrieval-assessment-roadmap.md](retrieval-assessment-roadmap.md)

Pure computation — no DB access, no side-effects.

**Phase 8 addition**: Stage 6 reads `assembly_notes.suppressed_by_gate_count`
forwarded from Stage 4. When all candidates were suppressed by the relevance
gate, `insufficient_no_hit` is classified with reason
`relevance_gate_suppressed_all` (distinct from `no_primary_or_archive` for true
corpus misses). Suppression rate and counts surface in `AssessmentDirective`.

Stage 6 evaluates the assembled packet along two axes:

1. **Trustworthiness:** whether the memory is safe to use. This covers freshness,
   quality gate, contradiction status, authority tier, archive-only status, and
   whether the primary memories actually match the query anchors and query type.
2. **Completeness for the caller's immediate intent:** whether the packet
   contains enough of the functional path to help the current task. For build and
   debug work, this usually means the reusable success path, prerequisites,
   major failure/caution branches, and enough causal or timeline context to avoid
   repeating known mistakes.

Retrieval states (precedence: most severe first):

| State | Conditions |
|-------|-----------|
| `insufficient_contested` | Contradiction pairs in primary_memories |
| `insufficient_stale` | All primary hits have freshness ∈ {stale} |
| `insufficient_no_hit` | primary_memories empty AND archive_context empty |
| `sufficient_historical` | temporality = historical and ≥1 primary hit |
| `partial` | ≥1 primary hit fails quality_pass OR freshness = suspect |
| `sufficient_current` | ALL primary hits: fresh/aging, quality_pass=True |

Quality gate applies to all primary hits (not just top hit).
Historical recommendation: `reuse_with_verification` (not `reuse`).

Stage 6 must also classify functional completeness. This classification is
separate from `retrieval_state`; a packet can be trustworthy but incomplete, or
complete but too large to emit in full.

| Completion state | Meaning |
|------------------|---------|
| `complete_path` | Required functional roles are present for the caller intent. |
| `success_only` | A reusable success path exists, but failure/caution context is thin. |
| `partial_chain` | Some causal/timeline links are present, but important roles are missing. |
| `fragmentary` | Isolated hits exist but do not form an actionable path. |
| `overabundant` | The reservoir contains multiple branches or full history beyond first-response needs. |

Stage 6 emits an `AssessmentDirective` for Stage 7. At minimum it should carry:

| Field | Purpose |
|-------|---------|
| `retrieval_state` | Trust/freshness/quality sufficiency state. |
| `completion_state` | Functional path completeness. |
| `reuse_mode` | `direct_reuse`, `reuse_with_verification`, `scaffold_only`, `diagnose_on_failure`, or `fresh_research_required`. |
| `emission_profile` | `concise_success_path`, `success_plus_major_failures`, `full_chain`, `warning_only`, or `no_memory`. |
| `hydration_policy` | `hydrate_primary_only`, `hydrate_primary_and_failure_branches`, `hydrate_full_functional_path`, or `summaries_only`. |
| `missing_roles` | Functional roles absent from the reservoir. |
| `deferred_branches` | Reservoir branches intentionally withheld from first emission. |
| `followup_query_policy` | `none`, `on_first_failure`, or `immediate_focused_requery`. |
| `fresh_pipeline_threshold` | Reason code for when a new Stage 2-5 run is justified. |

The default for overabundant build/debug packets is progressive disclosure:
emit the current success path, prerequisites, and major cautions first; defer
deep historical branches until the agent reports a concrete failure. Stage 6
should prefer `followup_query_policy = on_first_failure` over immediate rerun
when the reservoir already contains plausible branches but the caller has not
yet produced an error signature.

A fresh Stage 2-5 pipeline run is justified only when one of these thresholds is
met:

- `no_anchor`: no primary or reservoir entry matches the query anchors.
- `missing_required_role`: the caller intent requires a role that trace
  completion could not fill, such as success resolution for an implementation
  task or diagnosis for a failure task.
- `new_error_signature`: the caller provides a concrete error, stack trace, log,
  test failure, or changed constraint not represented in the reservoir.
- `contradiction_blocks_use`: primary contradictions prevent selecting a safe
  branch.
- `archive_only_actionable`: only raw archive evidence is available for an
  action that needs canonical or verified guidance.
- `budget_trimmed_primary`: budget pressure removed summaries or references
  needed to assess the primary path.

Otherwise, Stage 6 should instruct Stage 7 to emit selectively from the
assessment reservoir rather than asking the pipeline to recall again.

---

### Stage 7 — Emission
**Plan:** retrieval-emission-plan.md (not yet written)

Serializes `AssessedPacket` to JSON (API callers) or Markdown (agent hooks).
All note content verbatim — no paraphrasing. `archive_context` slot always
labeled. `retrieval_state`, `recommendation`, `verification_action` in header.
Logs `runtime_packet_metrics`. Never raises: errors degrade to minimal packet.
Stage 7 follows the `AssessmentDirective`: it hydrates only the requested
branches, labels deferred reservoir branches, surfaces missing roles and
follow-up query policy, and keeps archive material structurally separate.

**Phase 8 additions**: each emitted hit carries a 2D score triple
(`relevance_score`, `quality_score`, `composite_score` = `final_score`).
When `insufficient_no_hit` is caused by relevance gate suppression, a
human-readable message distinguishes it from a true corpus miss and surfaces
the suppressed candidate count. CLI text output includes a confidence band
label (HIGH/MEDIUM/LOW) per hit; agent hook Markdown does not.

---

## DL North Star

The learned retrieval core replaces hash-256 embeddings and the heuristic
reranking formula with a wiki-native neural system. **Not a transformer or LLM.**

```
Stage 1: Query Encoder (Tower A)
  BoW(query, ~8000 vocab) → Linear(8000→256) → ReLU → LayerNorm → L2-norm
  Output: query_vec ∈ ℝ²⁵⁶

Stage 2: Note Encoder (Tower B) — stored in note_vectors (model=wiki-native-v1)
  Input: BoW(summary+title+body[:500]) + type_embed(16) + freshness_embed(8)
         + confidence_embed(4) + graph_scalars(3) + trust_scalar(1)
  BoW → Linear(8000→256) → ReLU → LayerNorm
  meta → Linear(32→64) → ReLU
  [bow_repr | meta_repr] → Linear(320→256) → L2-norm → note_vec

Stage 2: ANN Index
  FAISS IndexFlatIP over note_vecs (or sqlite-vec fallback)
  Tower A query_vec → top-50 ANN candidates, merged with BM25 + exact channels

Stage 3: Signal Fusion (optional learned component)
  Learned linear(4 weights) over [trust, freshness, importance, quality_pass]
  Replaces hand-tuned signal weights

Stage 4: Cross-Encoder Reranker
  [query_vec(256) | note_vec(256) | feature_vec(10)] → Linear(522→128)
  → GELU → Dropout(0.1) → Linear(128→32) → GELU → Linear(32→1) → Sigmoid
  feature_vec(10): bm25, semantic, graph, exact, type_prior, freshness, trust,
                   importance, type_match, alias_match
  ~70K parameters. CPU-trainable in seconds.
```

**Training:** InfoNCE contrastive loss (towers) + BCE (reranker). Positives:
`retrieval_hits.selected=1 AND useful=1`. Negatives: `useful=0` or hard negatives
(`rank ≥ top_k+5`). Pure numpy or optional torch backend.

**Storage:** note_vecs → `note_vectors` table; weights → `brain/data/east_flange_model.npz`;
ANN index → `brain/data/note_ann.index`.

**Graduated rollout by sample count:**

| Samples | Active model |
|---------|-------------|
| < 8 | config weight defaults |
| 8 – 49 | logistic reranker (current legacy) |
| 50 – 199 | cross-encoder, hash-256 embeddings |
| ≥ 200 | cross-encoder + wiki-native bi-encoder |
| ≥ 1000 | full model with signal fusion layer |

Compatibility note: this rollout assumes the Stage 2 recall boundary stays
stable while retrieval internals evolve. BM25, hash-256 semantic search, ANN
retrieval, and future learned recall are all implementations of candidate
generation under the same bounded `CandidatePool` contract. Upgrading the
retriever should not require refactoring Stages 3–7 as long as candidate
identity, provenance, structural separation, and boundedness remain intact.

---

## Boundary Rules

These rules apply to all stages and all future implementations, including the
DL north star:

1. **Read-only wiki.** No stage may write to `notes`, `aliases`, `claims`,
   `graph_nodes`, `graph_edges`, `note_trust`, `note_freshness`,
   `note_importance`, `note_vectors`, or any other canonical wiki table.
2. **Instrumentation writes only.** Permitted write targets: `retrieval_queries`,
   `retrieval_hits`, `retrieval_hit_features`, `retrieval_archive_hits`,
   `runtime_packet_metrics`. These are side-channel signals, not canonical state.
3. **Archive separation.** Archive chunk results appear only in `archive_context[]`.
   They must never appear in `primary_memories[]` or any `related_*` slot.
4. **No promotion trigger.** The retrieval path never enqueues extraction or triggers
   promotion. If retrieval reveals a gap, the agent (not the retrieval path) decides
   whether to write back.
5. **Verbatim content.** Note fields (`summary`, `body`, `title`, `type`) are passed
   through unmodified. Coherence annotations (`[superseded by: ...]`) are added to
   the in-memory packet only — never written to storage.
6. **Exit 0 always.** The retrieval entry points (shell or API) never block an agent
   by raising unhandled exceptions. All errors degrade to empty or partial output.
7. **Learned components are advisory.** The DL north star may suggest, rank, and
   classify. It has no mutation authority. All canonicity boundaries remain enforced
   by the software layer above it.

---

## Module Responsibility Matrix

| Stage | Module | Key types |
|-------|--------|-----------|
| 1 | `query_parser.py` | `ParsedQuery`, `QueryHints`, `QueryScope` |
| 2 | `recall.py` | `CandidatePool`, `RecallCandidate`, `ArchiveCandidate` |
| 3 | `signal_integration.py` | `NoteSignals`, `SignalEnrichedCandidatePool`, quality gate |
| 4 | `reranker.py` | `ScoredCandidate`, `RankedHits`, `RerankerWeights` |
| 5 | `assembler.py` | `RawPacket`, `AssembledNote`, `ArchiveNote`, `ContradictionPair` |
| 6 | `assessor.py` | `AssessedPacket`, `RetrievalState`, `Recommendation` |
| 7 | `emitter.py` | format functions, protocol wrapper |
| Shared | `retrieval_types.py` | all dataclasses and enums |
| Orchestrator | `east_flange.py` | pipeline entry point, calls stages in sequence |

`east_flange.py` is the public entry point. `runtime.py` calls `east_flange.py`
as its retrieval substrate.

---

## Testing Requirements

**Per-stage unit tests:**
- Happy path with representative inputs
- Empty/degraded input (empty query, empty pool, no wiki notes)
- Error path (DB unavailable, embedding provider down)
- Boundary conditions (exact top_k, budget at limit)

**Pipeline integration test:**
- Full 7-stage run against in-memory SQLite with seeded notes
- Verify no canonical wiki table modified (assert no writes to governed tables)
- Verify instrumentation tables updated correctly
- Regression gate: `evals/queries.jsonl` top-1 recall ≥ prior-phase baseline

---

## Governing References

### Plans and Roadmaps
- [retrieval-east-flange-plan.md](retrieval-east-flange-plan.md) — Stage 1 detailed plan
- [retrieval-east-flange-roadmap.md](retrieval-east-flange-roadmap.md) — Stage 1 phase status
- [retrieval-recall-plan.md](retrieval-recall-plan.md) — Stage 2 detailed plan
- [retrieval-recall-roadmap.md](retrieval-recall-roadmap.md) — Stage 2 phase status
- [retrieval-signal-integration-plan.md](retrieval-signal-integration-plan.md) — Stage 3 detailed plan
- [retrieval-signal-integration-roadmap.md](retrieval-signal-integration-roadmap.md) — Stage 3 phase status
- [retrieval-reranking-plan.md](retrieval-reranking-plan.md) — Stage 4 detailed plan
- [retrieval-reranking-roadmap.md](retrieval-reranking-roadmap.md) — Stage 4 phase status
- [retrieval-assembly-plan.md](retrieval-assembly-plan.md) — Stage 5 detailed plan
- [retrieval-assembly-roadmap.md](retrieval-assembly-roadmap.md) — Stage 5 phase status
- [retrieval-assessment-plan.md](retrieval-assessment-plan.md) — Stage 6 detailed plan
- [retrieval-assessment-roadmap.md](retrieval-assessment-roadmap.md) — Stage 6 phase status
- [retrieval-development-roadmap.md](retrieval-development-roadmap.md) — all 7 stages + DL north star status
- [retrieval-control-signal-quality-roadmap.md](retrieval-control-signal-quality-roadmap.md) — cross-cutting signal quality

### Adjacent Systems
- [INGESTION-PATH-GOVERNANCE-DESIGN.md](INGESTION-PATH-GOVERNANCE-DESIGN.md) — ingestion path (feeds the wiki this path reads)
- [runtime-protocol.md](runtime-protocol.md) — MemoryPacket schema, resolve_task_context contract
- [episode-aware-retrieval.md](episode-aware-retrieval.md) — chain_id and same_episode_context gates

### Audit and Quality
- [docs/east-flange-audit.md](../../docs/east-flange-audit.md) — Phase 0 audit: module map, bugs, extend verdict
