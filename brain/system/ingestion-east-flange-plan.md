# Ingestion: East-Flange Plan

## Status

This is the governing implementation plan for the MnemonicOS east flange — the
read-only retrieval path from natural-language query to bounded memory packet.

See `ingestion-east-flange-roadmap.md` for phase completion status.
See `docs/east-flange-audit.md` for Phase 0 audit of the legacy implementation.

---

## Design Mandate

The east flange:
- receives a natural-language query (possibly with metadata hints)
- fetches, sorts, arranges, and assembles content from the canonical wiki
- returns a self-consistent, bounded memory packet without modifying any stored content
- reflects the current state of the maintenance layer (freshness, trust, importance)
  automatically, without any coupling code

It is **read-only by contract**. It may write to instrumentation tables
(`retrieval_queries`, `retrieval_hits`, `retrieval_hit_features`, `retrieval_archive_hits`)
as side-channel signals — not canonical wiki state.

---

## Pipeline Overview

```
Natural Language Query
        │
        ▼
┌─────────────────────┐
│  Stage 1            │
│  QUERY PARSING      │
└──────────┬──────────┘
           │ ParsedQuery
           ▼
┌─────────────────────┐
│  Stage 2            │
│  MULTI-CHANNEL      │
│  RECALL             │
└──────────┬──────────┘
           │ CandidatePool
           ▼
┌─────────────────────┐
│  Stage 3            │
│  SIGNAL             │
│  INTEGRATION        │
└──────────┬──────────┘
           │ CandidatePool + NoteSignals
           ▼
┌─────────────────────┐
│  Stage 4            │
│  RERANKING          │
└──────────┬──────────┘
           │ RankedHits
           ▼
┌─────────────────────┐
│  Stage 5            │
│  ASSEMBLY           │
└──────────┬──────────┘
           │ RawPacket
           ▼
┌─────────────────────┐
│  Stage 6            │
│  ASSESSMENT         │
└──────────┬──────────┘
           │ AssessedPacket
           ▼
┌─────────────────────┐
│  Stage 7            │
│  EMISSION           │
└─────────────────────┘
           │
           ▼
     MemoryPacket (caller)
```

---

## Stage Specifications

### Stage 1 — Query Parsing

**Contract:**
```
Input:  raw query: str
        hints: QueryHints (type_hint, temporality, token_budget, scope, tags)
Output: ParsedQuery
```

**ParsedQuery fields:**
- `raw: str` — original query, unmodified
- `tokens: list[str]` — lowercased, stopword-filtered tokens
- `query_type: str | None` — one of: how_to, decision, preference_identity,
  who_where_relation, what_changed, broad_synthesis
- `classifier_confidence: float` — [0, 1]
- `temporality: str` — current | historical | mixed | unknown
- `anchor_terms: list[str]` — high-confidence terms likely to match aliases or titles exactly
- `expanded_terms: list[str]` — additional terms from synonym map and alias table
- `scope: QueryScope | None` — workspace, repo, tags from caller hints

**Responsibilities:**
1. Tokenize query (Unicode word boundary, stopword filter)
2. Classify query type via keyword hint match + confidence scoring
3. Detect temporality: scan for historical/current indicator phrases; default to `"unknown"`
   (not "current") when ambiguous
4. Extract anchor terms: tokens that appear in aliases table or match known note prefixes
   (procedure/, decision/, etc.)
5. Expand terms: for each anchor token, add up to 2 synonyms from the domain synonym map
   and up to 1 alias from the alias table; cap total expansion at 3 additional terms
6. Return `ParsedQuery` — immutable, typed

**Boundary:** Stage 1 does no DB writes and no network I/O. Single alias table read
for anchor detection is permitted.

**Error contract:** Never raises. If classification fails, `query_type=None`,
`classifier_confidence=0.0`. If alias table unavailable, `anchor_terms=[]`.

---

### Stage 2 — Multi-Channel Recall

**Contract:**
```
Input:  ParsedQuery
        config: AppConfig
        connection: sqlite3.Connection
Output: CandidatePool
```

**CandidatePool:** dict mapping `note_id → Candidate` with per-channel raw scores.

**Channels (in priority order for exact match):**
1. **Exact channel** — alias table lookup, note ID match, lowercased title match.
   Returns notes in any non-retired, non-expired state. Exact hits assigned sentinel
   score `1_000_000 - rank` to guarantee they appear first in Stage 4.
2. **BM25 channel** — FTS5 query over `notes_fts` (title + aliases_flat + summary + body).
   Query built from `ParsedQuery.tokens + ParsedQuery.expanded_terms`. Over-fetch `4 × top_k`.
   Retired and expired notes excluded.
3. **Semantic channel** — cosine similarity between query vector and note vectors from
   `note_vectors` table. Query vector computed **once** at Stage 2 entry and reused.
   Model key from config. Over-fetch `4 × top_k`. Score threshold: > 0.0.
4. **Graph channel** — bidirectional edge expansion from top-`focal_limit` candidates
   by seed score. Seed score = exact rank sentinel OR `1/bm25_rank + semantic_score`.
   Graph-only hits added to pool with `graph_score` from edge weight sum.
5. **Archive channel** — BM25 over `archive_chunks` FTS table. Gated by policy:
   - Wiki hit count = 0 (empty pool after channels 1–4)
   - `query_type NOT IN {"preference_identity"}`
   - At least one temporal signal or problem-class keyword in query tokens

**Archive channel contract:** archive results are tagged `source="archive"` on the
`Candidate` and must be kept in a separate slot through all downstream stages.
Archive candidates must never enter the `primary_memories` slot in Stage 5.

**Candidate merging:** if the same `note_id` appears in multiple channels, merge into
one `Candidate` by accumulating all channel names and keeping the best raw score per
channel. Do not overwrite a higher-rank channel entry with a lower one.

**Query vector:** computed once at Stage 2 start (if semantic channel enabled), stored
in a local variable, passed to cosine comparison — not re-computed per candidate row.

**Error contract:** if any channel fails (DB error, embedding provider unavailable),
that channel returns empty results; other channels proceed. Never raises.

---

### Stage 3 — Signal Integration

**Contract:**
```
Input:  CandidatePool
        connection: sqlite3.Connection
        config: AppConfig
        parsed_query: ParsedQuery
Output: CandidatePool with NoteSignals attached per candidate
```

**NoteSignals fields per candidate:**
- `trust_score: float` — from `note_trust.usefulness_score`; 0.5 (neutral) if no row
- `freshness_state: str` — from `note_freshness.freshness_state`; "unknown" if no row
- `staleness_score: float` — from `note_freshness.staleness_score`; 0.5 if no row
- `importance_score: float` — from `note_importance.importance_score`; 0.0 if no row
- `quality_pass: bool` — content quality gate result
- `freshness_multiplier: float` — precomputed from freshness_state + query temporality

**Content quality gate** (`quality_pass`):
A candidate fails if ALL of:
- `summary` is empty or identical to `title` (case-insensitive), AND
- `excerpt` word count < 20, AND
- `excerpt` contains none of the actionable markers: WHEN, DO, Steps, Cause, Fix, Avoid, →, ->, NOT

Candidates that fail quality are not discarded — they receive `quality_pass=False` and
are routed to `warnings[]` during Stage 5 assembly.

**Freshness fallback:** if `note_freshness` row is absent, state = "unknown",
`freshness_multiplier` = 0.85 (slight penalty for unscored notes, not the default "fresh").

**Batch load:** all signals loaded in single queries per table using `IN` clauses,
not per-candidate round-trips. Signals for archive candidates are not loaded
(archive chunks have no trust/freshness/importance rows).

**Signal provenance:** signals are direct outputs of the maintenance layer. Stage 3
reads only; it does not compute or update any signal.

---

### Stage 4 — Reranking

**Contract:**
```
Input:  CandidatePool with NoteSignals
        config: AppConfig
        parsed_query: ParsedQuery
        connection: sqlite3.Connection
Output: RankedHits (sorted list of ScoredCandidate)
```

**Scoring formula for non-exact candidates:**
```
raw_base = (
    bm25_weight        * normalize_bm25(bm25_rank, pool_max_rank)
  + semantic_weight    * max(semantic_score, 0.0)
  + graph_weight       * normalize_graph(graph_score, pool_max_graph)
  + type_prior_weight  * type_prior(query_type, note_type)
  + freshness_weight   * max(1.0 - staleness_score, 0.0)
  + trust_weight       * trust_score
  + importance_weight  * normalize(importance_score, pool_max_importance)
  + exact_alias_weight * (1.0 if "exact" in channels else 0.0)
  + historical_bonus(temporality, freshness_state)
)

final_score = raw_base * freshness_multiplier
```

**Exact candidates:** `final_score = 1_000_000 - exact_rank` (bypasses formula).

**historical_bonus:** applied only when `temporality == "historical"`:
- stale state: +0.20
- suspect state: +0.12
- aging state: +0.06

**Weight source:** weights loaded from `rerank_weights` table (model = `"east-flange-v1"`).
If no trained row exists or sample_count < `min_training_samples`, use config defaults.
Config defaults serve as the cold-start fallback.

**Normalization:**
- `bm25_rank` → `1.0 - (rank - 1) / max_rank_in_pool`
- `graph_score` → `score / max_graph_score_in_pool` (if max > 0)
- `importance_score` → `score / max_importance_in_pool` (if max > 0)
- All normalizations clamp to `[0.0, 1.0]`

**Deduplication:** if `note_id` appears in both "exact" and "bm25" channel,
the exact-channel sentinel score takes precedence. Implemented at merge time in Stage 2;
Stage 4 treats each note_id as appearing once.

**Archive candidates:** scored using BM25 rank only (no trust/freshness/importance).
Score = `1.0 - (rank - 1) / top_k`. Archive candidates are sorted separately from
wiki candidates and never merged into the same ranking list.

**Output:** two separate sorted lists:
1. `wiki_hits: list[ScoredCandidate]` — top-k wiki notes
2. `archive_hits: list[ScoredCandidate]` — top-k archive chunks (may be empty)

**Logging:** after sorting, log to `retrieval_queries`, `retrieval_hits`,
`retrieval_hit_features` (one row per wiki hit with all signal values).

---

### Stage 5 — Assembly

**Contract:**
```
Input:  RankedHits (wiki_hits + archive_hits)
        ParsedQuery
        config: AppConfig
        connection: sqlite3.Connection
        token_budget: int | None
Output: RawPacket
```

**RawPacket slots:**
- `primary_memories: list[AssembledNote]` — top-k ranked, quality-passed wiki notes
- `same_episode_context: list[AssembledNote]` — notes sharing chain_id with primary hit
- `related_by_topic: list[AssembledNote]`
- `related_by_timeline: list[AssembledNote]`
- `related_by_similarity: list[AssembledNote]`
- `related_by_contrast: list[AssembledNote]`
- `archive_context: list[ArchiveNote]` — archive fallback hits, always separate
- `contradictions: list[ContradictionPair]` — supersedes/superseded_by edge pairs
- `warnings: list[str]` — quality-failed notes, stale/contested notes

**Assembly rules:**
1. Fill `primary_memories` from `wiki_hits` in ranked order:
   - Skip if `quality_pass=False` (route to `warnings[]` instead)
   - Apply type-bucket caps: ≤2 procedure, ≤2 fact (decision/concept/source), ≤1 episode
   - Stop at `max_primary` (derived from token_budget)
2. If `primary_memories` is empty after quality filtering: relax caps and include
   quality-failed notes (with warning appended)
3. Fill `same_episode_context`: query notes with matching `chain_id` to top primary hit
   (only if primary hit has chain_id and final_score > 0). Cap at `max_related`.
4. Fill related slots from remaining ranked hits (not in primary or same_episode):
   - Route by edge type: before/after/supersedes → timeline; similar_to → similarity;
     contrasts_with → contrast; all others or no relation → topic
5. Fill `archive_context` from `archive_hits` list (always separate, never in primary)
6. Detect contradictions: scan graph_edges for `supersedes`/`superseded_by` pairs
   among assembled notes; add to `contradictions[]` with both note IDs and relation label
7. **Cross-slot deduplication:** maintain `assembled_ids` set; skip any note already present
   in a higher-priority slot
8. **Coherence annotation:** if note A supersedes note B and both appear in any slot,
   annotate note B's `summary` field (in the packet, not in storage) with
   `"[superseded by: <A.title>]"` appended

**Context budget:**
- Track estimated token count: `len(note.summary.split()) * 1.4 + len(note.body_excerpt.split())`
- When budget exceeded: truncate `body_excerpt` of lowest-ranked notes until under budget
- Never truncate: `title`, `type`, `freshness_state`, `summary` (first 220 chars)

**Archive separation contract:** archive notes have `source="archive"` and appear only
in `archive_context[]`. Assembly must assert this invariant before returning.

---

### Stage 6 — Assessment

**Contract:**
```
Input:  RawPacket
        ParsedQuery
Output: AssessedPacket (RawPacket + retrieval_state + recommendation + verification_action)
```

**Retrieval state classification:**

| State | Conditions |
|-------|-----------|
| `sufficient_current` | ≥1 primary_memory, freshness ∈ {fresh, aging}, quality_pass=True for ALL primary hits |
| `sufficient_historical` | ≥1 primary_memory, temporality="historical", any freshness |
| `partial` | primary_memories non-empty but ≥1 primary hit fails quality_pass OR freshness ∈ {suspect} |
| `insufficient_no_hit` | primary_memories empty AND archive_context empty |
| `insufficient_stale` | primary_memories non-empty but all freshness ∈ {stale} |
| `insufficient_contested` | contradiction pairs detected in primary_memories |

**Precedence (from most to least severe):**
1. `insufficient_contested` (contradictions detected)
2. `insufficient_stale` (all primary stale)
3. `insufficient_no_hit` (nothing found)
4. `sufficient_historical` (temporality=historical)
5. `partial` (mixed freshness or quality gaps)
6. `sufficient_current` (clean hit)

**Quality gate across all primary hits:** `sufficient_current` requires ALL primary hits
to pass quality gate, not just the top hit.

**Recommendation derivation:**
- `sufficient_current` → `reuse`
- `sufficient_historical` → `reuse_with_verification`
- `partial` → `reuse_with_verification`
- `insufficient_*` → `research_fresh`

**Verification action derivation:**
- `reuse` → `none`
- `sufficient_historical` → `treat_as_historical_only`
- `partial` → `verify_current_truth`
- `research_fresh` → `check_recent_sources`

**Assessment side-effects:** none. Assessment is pure computation over the RawPacket.

---

### Stage 7 — Emission

**Contract:**
```
Input:  AssessedPacket
        query_id: int (from Stage 4 logging)
        output_format: "json" | "markdown"
Output: str (formatted output to caller)
```

**Output contract (all formats):**
- Every note field in `primary_memories`, `related_*`, `archive_context` is verbatim
  from stored note fields — no paraphrasing, no summarization beyond excerpt truncation
- `retrieval_state`, `recommendation`, `verification_action` included as structured header
- Archive context always labeled with `source: raw_session` or equivalent marker
- `contradictions[]` surfaced explicitly
- `warnings[]` included after primary content

**Markdown format (for agent hooks):**
```markdown
## Memory Context

retrieval_state: <state>
recommendation: <recommendation>
verification_action: <verification_action>

### Primary
- [<title>] (<type>, <freshness_state>) [<channels>]
  <summary> (max 220 chars)

### Episode Context
...

### Related
...

### Archive Context (raw sessions — not canonical)
...

### Warnings
...

### Contradictions
...
```

**JSON format (for API callers):** full `AssessedPacket` serialized, including all signal
scores, channels, provenance, query_id, query_type, classifier_confidence.

**Logging:** Stage 7 does not perform logging — that is Stage 4's responsibility.
If called via `resolve_task_context`, `runtime_packet_metrics` are recorded here.

**Exit contract:** Emission never raises. On any serialization error, emit a minimal
`insufficient_no_hit` packet with error detail in `warnings[]`.

---

## Boundary Rules

These rules apply to all stages and all future implementations:

1. **Read-only wiki:** no stage may write to `notes`, `aliases`, `claims`, `graph_nodes`,
   `graph_edges`, `note_trust`, `note_freshness`, `note_importance`, `note_vectors`,
   or any other canonical wiki table.
2. **Instrumentation writes:** `retrieval_queries`, `retrieval_hits`,
   `retrieval_hit_features`, `retrieval_archive_hits`, `runtime_packet_metrics` are
   write-permitted (side-channel signals only).
3. **Archive separation:** archive chunk results must appear in `archive_context[]` only.
   They must never appear in `primary_memories[]` or any `related_*` slot.
4. **No promotion:** east flange never triggers extraction or promotion.
5. **No mutation of content fields:** `summary`, `body`, `title`, `type` fields read
   from wiki are passed through verbatim. Coherence annotations (`[superseded by: ...]`)
   are added to the assembled packet's in-memory repr only, never written back.
6. **Exit 0 always:** the east flange entry point (shell or API) never blocks an agent
   by raising unhandled exceptions. All errors degrade gracefully to empty/partial output.

---

## Module Responsibility Matrix

The east flange is implemented as a set of Python modules under `src/second_brain/`
(or a dedicated `src/second_brain/east_flange/` sub-package if stage separation warrants it).

| Stage | Module | Key types |
|-------|--------|-----------|
| 1 | `query_parser.py` | `ParsedQuery`, `QueryHints`, `QueryScope` |
| 2 | `recall.py` | `CandidatePool`, `Candidate`, `RecallConfig` |
| 3 | `signal_integration.py` | `NoteSignals`, quality gate |
| 4 | `reranker.py` | `ScoredCandidate`, `RankedHits`, `RerankerWeights` |
| 5 | `assembler.py` | `RawPacket`, `AssembledNote`, `ArchiveNote`, `ContradictionPair` |
| 6 | `assessor.py` | `AssessedPacket`, `RetrievalState`, `Recommendation` |
| 7 | `emitter.py` | format functions, protocol wrapper |
| Shared | `east_flange_types.py` | all dataclasses |

These modules are called in sequence by `east_flange.py` (the pipeline orchestrator).
`runtime.py` calls `east_flange.py` as its retrieval substrate.

---

## Testing Requirements

Each stage requires tests at two levels:

**Unit tests (per stage):**
- Happy path with representative inputs
- Empty input (empty query, empty pool, no wiki notes)
- Error/degradation path (DB unavailable, embedding provider down)
- Boundary conditions (exact top_k, budget exactly at limit)

**Integration test (pipeline):**
- Full pipeline run against an in-memory SQLite DB with seeded notes
- Verify that all 7 stages produce correct typed output in sequence
- Verify instrumentation writes occurred
- Verify no canonical wiki tables were modified

**Regression gate:** before marking any stage as complete, run `evals/queries.jsonl`
against the implementation and confirm top1 recall ≥ baseline from prior phase.

---

## What Is Not Part Of This Layer

- Ingestion (west flange + extraction pipeline)
- Promotion, review, maintenance
- Writeback and session recording
- North-facing agent APIs above the protocol contract (orchestrator, bridge hooks)
- Storage backend decisions (south boundary)

---

## Governing References

- `brain/system/ingestion-east-flange-overview.md` — legacy state stub and boundary rules
- `brain/system/ingestion-east-flange-roadmap.md` — phase completion status
- `brain/system/ingestion-ingestion-east-flange-overview-stage1.md` — Stage 1 detailed plan
- `brain/system/runtime-protocol.md` — MemoryPacket and operation contract
- `brain/system/architecture.md` — authority order, system shape
- `brain/system/ingestion-control-signal-roadmap.md` — content quality gate spec
- `brain/system/episode-aware-retrieval.md` — chain_id and same_episode_context gates
- `docs/east-flange-audit.md` — Phase 0 audit of legacy implementation
