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
| S4-P4 | Caps, diversification, deterministic stability | done |
| S4-P5 | Training labels, weights, and feedback integration | done |

---

## Implementation Mark

**2026-05-13:** Stage 4 implemented and verified.

Implemented:
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

Verification:
- `python3 -m unittest tests.test_reranker` -> 7 passed
- targeted retrieval regression suite -> 23 passed
- `python3 -m unittest discover -s tests` -> 417 passed

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
- [Phase 4](retrieval-reranking-plan-phase4.md) · [Phase 5](retrieval-reranking-plan-phase5.md)

**Governing plan:** [retrieval-reranking-plan.md](retrieval-reranking-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
