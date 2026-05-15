# Retrieval Reranking Phase 6: Multiplicative Scoring and Absolute Relevance Gate

## Goal

Replace the single additive scoring formula with a two-layer multiplicative
model. Relevance and quality are computed as independent normalized sub-scores
and multiplied into a composite. A minimum relevance threshold gates all
candidates before they can reach Stage 5 assembly.

This directly addresses the score floor problem: high-quality notes currently
inflate composite scores for completely irrelevant queries because metadata
signals (freshness, trust, importance) accumulate unconditionally. Under the new
model, low relevance caps composite to near-zero regardless of note quality.

## Scope

- Two-layer scoring: `relevance_score` and `quality_score` as separate [0, 1]
  normalized values
- `composite_score = relevance_score × quality_score`
- Absolute (score-based) BM25 normalization replacing rank-based normalization
  for the relevance layer
- `min_relevance_score` gate that suppresses candidates before Stage 5
- 2D score triple `(relevance, quality, composite)` emitted on each `RetrieveHit`
- Config params: `min_relevance_score`, `bm25_saturation`

## Components

```text
src/second_brain/reranker.py     — scoring formula split and gate enforcement
src/second_brain/models.py       — RetrieveHit: add relevance_score, quality_score fields
config/memory.example.toml       — new params: min_relevance_score, bm25_saturation
tests/test_reranker.py           — tests for gate, split scores, composite
```

## Two-Layer Formula

### Relevance Sub-Score (query-dependent)

Relevance captures how well the candidate matches the query. All components
require the query to contribute — they are not meaningful without it.

```text
bm25_abs     = min(bm25_score / bm25_saturation, 1.0)   # absolute, not rank-based
semantic_abs = semantic_score                            # cosine similarity [0, 1]
graph_abs    = normalize_graph(graph_score, graph_max)   # [0, 1]
exact_abs    = 1.0 if exact_rank is not None else 0.0

relevance_raw = (
  bm25_weight          * bm25_abs
  + semantic_weight    * semantic_abs
  + graph_weight       * graph_abs
  + exact_alias_weight * exact_abs
)

relevance_weight_sum = bm25_weight + semantic_weight + graph_weight + exact_alias_weight
relevance_score = relevance_raw / relevance_weight_sum   # → [0, 1]
```

Default config weights: bm25=0.30, semantic=0.25, graph=0.20, exact=0.10
→ `relevance_weight_sum = 0.85`

`bm25_saturation` (default: 15.0): the BM25 score at which relevance saturates.
FTS5 scores for strong matches typically fall between 5 and 20. Tune up if wiki
is large and BM25 scores are systematically high.

**Why absolute BM25, not rank-based?**  
Rank-based normalization gives rank-1 a bm25_norm of 1.0 even if the actual BM25
score is near zero (as happens when the query shares only stopwords with the
corpus). Absolute scoring properly expresses near-zero relevance for queries
with no real lexical overlap.

### Quality Sub-Score (node-intrinsic)

Quality captures how trustworthy and important the note is, independent of the
query.

```text
quality_raw = (
  type_prior_weight   * type_prior_score
  + freshness_weight  * freshness_score
  + trust_weight      * trust_score
  + importance_weight * importance_norm
  + quality_weight    * (1.0 if quality_pass else 0.0)
  + historical_bonus(temporality, freshness_state)
)

quality_weight_sum_max = (
  type_prior_weight + freshness_weight + trust_weight
  + importance_weight + quality_weight + historical_bonus_max
)
quality_score = quality_raw / quality_weight_sum_max     # → [0, 1]
```

Default: type_prior=0.15, freshness=0.10, memory_strength=0.10, importance=0.10,
quality=0.08, historical_bonus_max=0.20
→ `quality_weight_sum_max = 0.73`

### Composite Score

```text
composite_score = relevance_score * quality_score        # → [0, 1]
```

The product ensures that near-zero relevance dominates: a perfect-quality note
with relevance_score=0.05 gets composite=0.05 × quality ≤ 0.05.

Ranking uses `composite_score`. `final_score` in the existing model maps to
`composite_score` for downstream compatibility; `raw_score` continues to reflect
the single strongest channel signal.

### Exact-Match Bypass

Exact alias/title matches retain the existing bypass rule but are re-expressed
in the new vocabulary:

```text
relevance_score = 1.0
quality_score   = computed normally
composite_score = 1_000_000 - exact_rank   # bypass for ranking stability
```

The `relevance_score = 1.0` ensures exact matches pass the relevance gate and
are correctly represented in the 2D vector.

## Relevance Gate

After scoring all candidates, apply the gate before Stage 5 handoff:

```text
for each candidate:
  if candidate.relevance_score < min_relevance_score:
    suppress(candidate)   # do not include in RankedHits
```

`min_relevance_score` default: **0.35** (raised from 0.25 — 0.25 sits inside the embedding noise floor; 0.35 clears it)

### Threshold Rationale

Empirical estimates of relevance_score under the normalized formula:

| Query type | bm25_abs | semantic | graph | relevance_score |
|-----------|---------|---------|-------|-----------------|
| Off-topic (quantum mechanics) | ~0.00 | ~0.25 | 0 | ~0.07 |
| Topic-adjacent miss (SWE failures) | ~0.10 | ~0.35 | 0 | ~0.14 |
| Legitimate domain hit (mnemonicos design) | ~0.30 | ~0.50 | 0 | ~0.25 |
| Strong in-corpus hit | ~0.60 | ~0.65 | 0.30 | ~0.42 |

A threshold of 0.25 screens out off-topic and topic-adjacent misses while
passing legitimate domain hits at the boundary. Notes are admitted on the
"highly relevant" side: false negatives (borderline notes suppressed) are
preferred over false positives (noise admitted).

Threshold is configurable via `[retrieval] min_relevance_score` for domains
where the corpus is very narrow (higher threshold appropriate) or very broad
(lower threshold may help recall).

### Gate Interaction with `insufficient_no_hit`

When all candidates are suppressed by the gate, Stage 5 will assemble an empty
primary_memories slot. Stage 6 must then classify `insufficient_no_hit` with
reason `relevance_gate_suppressed_all`, not the legacy `no_primary_or_archive`
reason. This distinction is important: the system found candidates but correctly
rejected them for low relevance, which is different from a corpus miss.

## 2D Score Triple on RetrieveHit

`RetrieveHit` gains two new fields:

```python
@dataclass
class RetrieveHit:
    ...
    relevance_score: float   # [0, 1] query-dependent sub-score
    quality_score: float     # [0, 1] node-intrinsic sub-score
    # final_score continues to hold composite_score for ranking compatibility
```

`final_score` = composite_score. No breaking change to downstream consumers that
only read `final_score`. The new fields are additive.

## New Config Parameters

```toml
[retrieval]
min_relevance_score = 0.25
bm25_saturation = 15.0
```

`bm25_saturation`: the BM25 raw score at which `bm25_abs` saturates to 1.0.
Start at 15.0 and calibrate from the `retrieval_hit_features.bm25_score`
distribution after a week of retrieval traffic.

## Rules

1. `relevance_score` and `quality_score` are each independently normalized to
   [0, 1] via their respective weight-sum denominators. **Exception: archive
   candidates** (`type="archive_chunk"`) use a rank-based fallback score
   (`1.0 - ((rank - 1) / top_k)`) and have no wiki metadata signals; their
   `relevance_score` and `quality_score` fields are meaningless defaults (0.0)
   and must not be displayed or consumed as if they were the wiki 2D scores.
2. Exact matches set `relevance_score = 1.0` and bypass composite scoring for
   the ranking column; the 2D triple is still emitted for observability.
3. `bm25_abs` uses the raw score from `retrieval_hit_features.bm25_score`, not
   a rank-derived value.
4. The gate applies to all non-exact candidates. Exact matches are never
   suppressed by the gate (relevance = 1.0 by definition).
5. Gate suppression must be logged as a suppression event per candidate for
   learning signal recovery; suppressed hits should be logged in
   `retrieval_hits` with `selected = 0` and `suppression_reason = 'relevance_gate'`.
6. `composite_score` is the only ranking key. Tie-breaking continues to use
   title/id stability rules from Phase 4.
7. `quality_weight_sum_max` is a static normalizer derived from config weights +
   `historical_bonus_max = 0.20`. It does not vary per candidate.
8. Stage 4 must pass both `relevance_score` and `quality_score` in the feature
   row for training label use in Phase 5.

## Verification

- Off-topic query produces relevance_score < 0.25 for all candidates → gate
  suppresses all → primary_memories empty → Stage 6 emits insufficient_no_hit
- Exact match hit has relevance_score = 1.0 and is never suppressed
- Additive formula score and multiplicative composite score are compared on a
  shared fixture; composite is verifiably lower for quality-heavy / relevance-weak
  candidates
- `bm25_abs` for a zero-BM25-score candidate is 0.0 (not rank-1 inflated)
- `retrieval_hit_features` logs `relevance_score` and `quality_score` fields
- Suppressed candidates appear in `retrieval_hits` with `selected = 0`

---

## Reference

**Governing plan:** [retrieval-reranking-plan.md](retrieval-reranking-plan.md)
**Prior phases:** [P1](retrieval-reranking-plan-phase1.md) · [P2](retrieval-reranking-plan-phase2.md) · [P3](retrieval-reranking-plan-phase3.md) · [P4](retrieval-reranking-plan-phase4.md) · [P5](retrieval-reranking-plan-phase5.md)
**Assessment gate interaction:** [retrieval-assessment-plan-phase8.md](retrieval-assessment-plan-phase8.md)
**Emission:** [retrieval-emission-plan-phase8.md](retrieval-emission-plan-phase8.md)
