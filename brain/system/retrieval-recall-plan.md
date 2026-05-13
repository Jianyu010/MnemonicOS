# Retrieval Recall Plan — Stage 2: Multi-Channel Recall

## Purpose

Stage 2 is the retrieval path's recall engine. It accepts a frozen `ParsedQuery`
from Stage 1 and emits a bounded `CandidatePool` that is broad in reach but
still cheap enough for downstream scoring, assembly, and assessment.

This stage must not materialize a "comprehensive reservoir" of fully hydrated
wiki content. Its job is to gather candidate identity and provenance across
multiple channels, not to build the final working packet. Stage 5 owns packet
assembly; Stage 6 owns sufficiency assessment.

This governing file defines scope, vocabulary, invariants, and phase order.
Implementation details live in the phase files:

1. [Phase 1: CandidatePool Contract and Exact Channel](retrieval-recall-plan-phase1.md)
2. [Phase 2: BM25 Channel](retrieval-recall-plan-phase2.md)
3. [Phase 3: Semantic Channel](retrieval-recall-plan-phase3.md)
4. [Phase 4: Graph Channel](retrieval-recall-plan-phase4.md)
5. [Phase 5: Archive Channel and Policy Gate](retrieval-recall-plan-phase5.md)
6. [Phase 6: Candidate Merge and Structural Separation](retrieval-recall-plan-phase6.md)

---

## Glossary

- **Recall**: candidate gathering before signal integration and reranking.
- **CandidatePool**: typed Stage 2 output containing wiki and archive candidate
  sets plus pool-level metadata.
- **Direct candidate**: candidate surfaced directly from exact, BM25, or semantic
  matching against canonical wiki notes.
- **Adjacent candidate**: candidate surfaced by graph expansion from top direct
  seeds.
- **Archive candidate**: candidate surfaced from raw/archive fallback and kept
  structurally separate from wiki candidates.
- **Seed set**: top direct candidates used to drive graph expansion.
- **Recall budget**: per-channel and total caps used to bound latency and memory.
- **Reach**: how much of the memory graph Stage 2 can touch.
- **Payload**: how much note content Stage 2 hydrates and carries forward.
- **Raw-trace intent**: explicit user request for original/raw/transcript/quote/
  provenance material rather than summarized canonical memory.

---

## Design Position

Stage 2 should build a **comprehensive logical reservoir**, not a
**comprehensive materialized reservoir**.

That means:

- broad enough to avoid missing relevant memory
- shallow enough to keep latency bounded
- sparse enough to avoid turning Stage 2 into premature assembly

The stage should gather candidate IDs, scores, relation metadata, and minimal
content needed for later ranking. It should not eagerly expand all linked notes,
load all bodies in a trace, or mix raw/archive material into normal wiki recall.

This is also the compatibility stance for the DL north star: Stage 2's contract
is defined by the shape of the `CandidatePool`, not by BM25-specific query
construction. BM25 is the v1 implementation path, not the architectural center.

---

## Boundary

Stage 2 owns:

- exact-match candidate retrieval from anchor terms and explicit identifiers
- BM25 recall over original plus expanded terms
- semantic recall over the query embedding
- graph expansion from a bounded seed set
- archive/raw fallback recall under explicit policy
- candidate merge and deduplication
- structural separation between wiki and archive candidate lanes

Stage 2 does not own:

- trust, freshness, importance, or quality-pass computation (Stage 3)
- final scoring formula (Stage 4)
- packet slot composition (`primary_memories`, `related_*`, `archive_context`)
  (Stage 5)
- sufficiency classification or recommendation (`reuse`,
  `reuse_with_verification`, `research_fresh`) (Stage 6)
- rendering, emission, or writeback decisions (Stage 7 / runtime)

---

## Non-Negotiable Invariants

- Stage 2 input is the frozen `ParsedQuery` from Stage 1. No mutation.
- Stage 2 output is a typed `CandidatePool`. No downstream stage should infer
  hidden meaning from ad hoc dicts or raw SQL rows.
- Stage 2 is **bounded**. Every channel has an explicit cap, and the total pool
  size is capped.
- Stage 2 is **shallow**. Graph expansion is depth-1 only in v1.
- Stage 2 is **separated**. Archive/raw candidates never mix into the wiki
  candidate lane.
- Stage 2 is **policy-aware**. Archive/raw recall is gated by explicit intent or
  downstream fallback policy, not treated as ordinary canonical memory.
- Stage 2 is **lazy about payload**. It should carry minimal candidate content
  sufficient for scoring and later hydration, not eagerly gather a full trace.
- Stage 2 is **retriever-agnostic at the boundary**. Downstream stages depend on
  candidate identity, provenance, and generic scores, not on BM25-specific
  mechanics.
- Stage 2 performs no DB writes.

---

## CandidatePool Contract

Stage 2 emits one typed pool:

```python
@dataclass(slots=True)
class CandidatePool:
    parsed_query: ParsedQuery
    wiki_candidates: list[RecallCandidate]
    archive_candidates: list[ArchiveCandidate]
    recall_metrics: RecallMetrics
    archive_policy: ArchivePolicyDecision
```

### RecallCandidate

```python
@dataclass(slots=True)
class RecallCandidate:
    id: str
    title: str
    type: str
    status: str | None
    chain_id: str | None
    lifecycle_state: str | None
    body_path: str
    summary: str
    body: str
    channels: set[str]                   # exact|bm25|semantic|graph
    relation_types: list[str]
    graph_depth: int | None              # None for non-graph direct hits
    seed_from_ids: list[str]             # graph provenance only
    exact_rank: int | None
    bm25_rank: int | None
    bm25_score: float | None
    semantic_score: float | None
    graph_score: float | None
    updated_at: str | None
    created_at: str | None
    last_verified_at: str | None
    learned_score: float | None = None       # future learned retriever score
```

### ArchiveCandidate

```python
@dataclass(slots=True)
class ArchiveCandidate:
    id: str                              # archive_id#chunk-N
    archive_id: str
    chunk_index: int
    archive_path: str
    summary: str
    excerpt: str
    channels: set[str]                   # archive
    bm25_rank: int | None
    bm25_score: float | None
```

### ArchivePolicyDecision

```python
@dataclass(frozen=True, slots=True)
class ArchivePolicyDecision:
    allowed: bool
    mode: str                            # disabled|explicit_raw_intent|fallback_only
    reasons: list[str]
```

### RecallMetrics

```python
@dataclass(slots=True)
class RecallMetrics:
    exact_count: int
    bm25_count: int
    semantic_count: int
    graph_count: int
    archive_count: int
    merged_wiki_count: int
    seed_count: int
    graph_seed_ids: list[str]
```

## Learned Retriever Compatibility

Stage 2 is intentionally designed so that learned retrieval can be introduced
later without refactoring the whole retrieval path.

### What stays stable

- `ParsedQuery` remains the Stage 1 input
- `CandidatePool` remains the Stage 2 output
- `wiki_candidates` and `archive_candidates` remain structurally separate
- downstream stages continue to consume candidates through generic fields such
  as `id`, `channels`, `relation_types`, and score-bearing attributes

### What may change

- BM25 may remain as a channel, become a fallback channel, or be retired
- the current semantic channel may be replaced by a learned bi-encoder channel
- Stage 2 may add a `learned` channel that returns direct candidates from ANN or
  another indexed retrieval backend
- Stage 4 may consume `learned_score` or a future `cross_encoder_score` in
  addition to current heuristic signals

### What must not be frozen into the architecture

- BM25 query strings as the stage interface
- anchor and expanded terms as the only valid candidate-generation mechanism
- reranking formulas that assume every candidate has `bm25_rank`
- graph expansion rules that depend on lexical channels being present

### Compatibility Rule

The Stage 2 boundary is retrieval-model-agnostic:

- any retriever may participate if it accepts natural-language query input (or a
  derived Stage 1 representation) and emits bounded candidates that fit the
  `RecallCandidate` or `ArchiveCandidate` contract
- learned retrievers are allowed to replace a channel, augment a channel, or
  provide a merged candidate list, as long as Stage 2 still emits a bounded
  `CandidatePool`

In short: v1 is "BM25 + semantic + graph + archive under one contract." Future
v2 may be "learned retriever + exact + archive under the same contract."

---

## Recall Strategy

The recall strategy in v1 is:

1. exact channel for anchor terms and full-query exact identity matches
2. BM25 channel over original + expanded terms
3. semantic channel over the original query
4. graph channel over a bounded top-seed set
5. archive/raw channel only when policy allows it
6. merge wiki candidates, dedupe by note ID, preserve per-channel provenance

This is intentionally asymmetrical:

- direct channels maximize precision and topicality
- graph channel recovers nearby context without loading an entire subgraph
- archive/raw stays isolated and policy-gated

This sequence is a v1 strategy, not a permanent lock-in. Future learned
retrievers may replace BM25 and/or semantic recall as long as the stage emits
the same bounded candidate contract.

---

## Raw / Archive Decision Model

The question "should we trace to raw?" is split into two decisions:

### Decision A — Admissibility in Stage 2

Stage 2 determines whether archive/raw recall is even allowed for this query.
This is based on explicit raw-trace intent or caller policy.

Examples that allow archive/raw recall:

- "show the original"
- "quote the exact wording"
- "what did the raw session say"
- "give me the transcript"
- "show provenance"

### Decision B — Necessity in Stage 6

Stage 6 determines whether canonical memory was insufficient and raw/archive
fallback is now needed.

Examples:

- canonical notes are stale or contested
- the assembled packet is partial
- no canonical hit survives quality gating

This split prevents Stage 2 from over-eager raw expansion while still preserving
the fallback path.

---

## Budget Model

Stage 2 must obey fixed ceilings in v1:

- exact hits: max 5
- BM25 hits: max `top_k * 4`
- semantic hits: max `top_k * 4`
- graph seeds: max `graph.focal_limit`
- graph fanout: max `graph.expand_limit`
- graph depth: exactly 1
- total merged wiki candidates: capped after merge
- archive hits: max `top_k`

These are implementation defaults, not user promises. They may evolve, but the
boundedness invariant may not.

---

## Legacy State

Current retrieval code in [retrieval.py](/Users/jianyulong/ai_memory/src/second_brain/retrieval.py)
already contains partial Stage 2 behavior inline:

| Legacy location | Concern | Target |
|----------------|---------|--------|
| `_exact_hits()` | exact recall | Phase 1 |
| `_bm25_hits()` | lexical recall | Phase 2 |
| `_semantic_hits()` | embedding recall | Phase 3 |
| `graph_expand(...)` call inside `retrieve()` | graph recall | Phase 4 |
| `_archive_hits()` | archive recall | Phase 5 |
| monolithic `candidates` dict in `retrieve()` | merge + dedupe | Phase 6 |

The Stage 2 plan formalizes and extracts these concerns into a typed boundary.

---

## Completion Gate

Stage 2 is complete when:

1. recall is implemented as a distinct typed stage that consumes `ParsedQuery`
   and emits `CandidatePool`
2. archive/raw candidates are structurally separated from wiki candidates
3. graph expansion uses bounded seeds and depth-1 expansion only
4. per-channel provenance survives dedupe and is visible downstream
5. Stage 3 accepts `CandidatePool` directly in an end-to-end test
6. explicit raw-intent queries can surface archive candidates without allowing
   archive material to leak into the wiki lane

---

## Reference

**Roadmap:** [retrieval-recall-roadmap.md](retrieval-recall-roadmap.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
