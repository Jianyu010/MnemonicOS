# Retrieval Reranking Phase 2: Heuristic Scoring Formula

## Goal

Move the cold-start weighted scoring formula out of `retrieval.py` and ensure it
uses every intended Stage 3 signal.

## Scope

- BM25 normalization
- semantic score
- graph score normalization
- type prior
- freshness score and multiplier
- trust score
- importance score
- quality pass/demotion
- historical bonus

## Formula

```text
final_score = (
  bm25_weight          * normalize_bm25(rank, pool_max)
  + semantic_weight    * semantic_score
  + graph_weight       * normalize_graph(graph_score, pool_max)
  + type_prior_weight  * type_prior_score
  + freshness_weight   * freshness_score
  + trust_weight       * trust_score
  + importance_weight  * normalize_importance(importance_score, pool_max)
  + quality_weight     * quality_pass
  + exact_alias_weight * exact_bonus
  + historical_bonus
) * freshness_multiplier
```

## Rules

1. `importance_score` must be active in scoring, not merely logged.
2. `quality_pass=False` should demote but not erase candidates.
3. For `what_changed` queries, freshness weight may be raised by policy.
4. Historical queries may apply a controlled relief/bonus for older notes.
5. Weights come from `rerank_weights` with config/default fallback.

## Verification

- importance changes ranking in a fixture
- stale/suspect freshness applies multiplier
- quality-failed candidate is demoted
- no DB access is needed beyond loading weights
