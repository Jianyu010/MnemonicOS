# Retrieval Reranking Roadmap — Stage 4

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| S4-P1 | `RankedHits` contract and wiki/archive lane separation | done |
| S4-P2 | Heuristic scoring formula with importance and quality | done |
| S4-P3 | Exact bypass and archive scoring rules | done |
| S4-P4 | Caps, diversification, deterministic stability, dynamic top-k | done |
| S4-P5 | Training labels, weights, and feedback integration | done |
| S4-P6 | Multiplicative scoring, absolute BM25, relevance gate, dynamic top-k, 2D score vector | done |

---

## Implementation Mark

**2026-05-13:** Stage 4 P1–P5 implemented and verified (local-linear reranking path).

Implemented in P1–P5:
- `src/second_brain/reranker.py`
- `RankedHits`, `ScoredCandidate`, `ScoredArchiveCandidate`, and
  `RerankFeatureRow`
- deterministic wiki scoring with BM25, semantic, graph, type-prior,
  freshness, trust, importance, quality, exact alias, and historical signals
- exact-match bypass: `1_000_000 - exact_rank`
- separate archive lane with archive-only ranked hits
- top-k cap, title/id tie-breaks, and deterministic same-chain diversity cap
- Stage 4 feature logging fields for quality and passive feedback metadata
- `rerank_training` job and daemon schedule for local linear weight updates
- passive-label training with confidence weighting; explicit labels dominate

**2026-05-14:** Stage 4 P6 implemented (multiplicative scoring, relevance gate, dynamic top-k).

Implemented in P6:
- Multiplicative final score: `final_score = relevance_score × quality_score`
- `relevance_score`: normalized [0,1] query-dependent sub-score over BM25 +
  semantic + graph signals; absolute BM25 via `bm25_score / bm25_saturation`
  (saturation=15.0) replacing rank-based inflation
- `quality_score`: normalized [0,1] node-intrinsic sub-score over type-prior,
  freshness, trust, importance, quality-pass, historical-bonus
- Relevance gate: candidates below `min_relevance_score=0.25` suppressed;
  exact matches exempt; `suppressed_by_gate_count` + `passed_gate_count` emitted
- Dynamic top-k: `max(min_hits, ceil(pool × pool_fraction))`, capped by
  `max_hits` and token-budget backstop; caller override still acts as hard cap
- Chain diversity cap: `max(2, ceil(top_k / 3))` instead of fixed 2
- 2D score vector (`relevance_score`, `quality_score`) on every `RetrieveHit`
- Suppression counts propagated: reranker → retrieval → runtime assembly_notes
  → assessor `_retrieval_state_for_packet` (new reason code
  `relevance_gate_suppressed_all` vs `no_primary_or_archive`)
- New `RetrievalConfig` params: `min_relevance_score`, `bm25_saturation`,
  `min_hits`, `max_hits`, `pool_fraction`, `tokens_per_hit_estimate`

Verification:
- `python3 -m unittest tests.test_reranker` → 12 passed (5 new gate/dynamic-k tests)
- `python3 -m unittest discover -s tests` → 444 passed
- E2E: "quantum mechanics dark matter" → final=0.037 [LOW], 33/34 suppressed
  (was 0.36–0.47 before multiplicative scoring)

---

## Legacy State

Previous behavior existed as inline scoring inside `retrieve()`. It has been
migrated to `reranker.py`, while `retrieve()` remains responsible for
orchestration and DB persistence.

| Gap | Legacy location | Target |
|-----|----------------|--------|
| scoring formula | `retrieval.py:retrieve()` loop | `reranker.py` |
| exact bypass | inline `1_000_000 - exact_rank` | Phase 3 rule |
| archive ranking | inline empty-wiki fallback | separate archive lane |
| importance score | loaded but historically not scored | done: formula includes `importance_weight` |
| training trigger | `train_rerank_model()` existed but was not stage-owned | done: `rerank_training` job |
| feedback hierarchy | implicit `selected/useful` fields | done: exposure vs usefulness split documented and logged |

---

## Completion Gate

Stage 4 is complete when:

1. `rerank()` returns typed ranked hits from Stage 3 fixtures.
2. exact, wiki, and archive candidates are tested separately.
3. final score includes importance and quality signals.
4. deterministic tie-breaking is stable under fixed inputs.
5. `retrieval_hit_features` can log all learned features.
6. training can distinguish explicit labels from passive derived labels.

All done for the local-linear Stage 4 path. DL reranking remains the separate
north-star path in the overall retrieval roadmap.

---

## Reference

**Detailed phase plans:**  
- [Phase 1](retrieval-reranking-plan-phase1.md) · [Phase 2](retrieval-reranking-plan-phase2.md) · [Phase 3](retrieval-reranking-plan-phase3.md)
- [Phase 4](retrieval-reranking-plan-phase4.md) · [Phase 5](retrieval-reranking-plan-phase5.md) · [Phase 6](retrieval-reranking-plan-phase6.md)

**Governing plan:** [retrieval-reranking-plan.md](retrieval-reranking-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
