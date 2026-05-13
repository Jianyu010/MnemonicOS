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
| S4-P1 | `RankedHits` contract and wiki/archive lane separation | not started |
| S4-P2 | Heuristic scoring formula with importance and quality | not started |
| S4-P3 | Exact bypass and archive scoring rules | not started |
| S4-P4 | Caps, diversification, deterministic stability | not started |
| S4-P5 | Training labels, weights, and feedback integration | not started |

---

## Legacy State

Current behavior exists as inline scoring inside `retrieve()`.

| Gap | Legacy location | Target |
|-----|----------------|--------|
| scoring formula | `retrieval.py:retrieve()` loop | `reranker.py` |
| exact bypass | inline `1_000_000 - exact_rank` | Phase 3 rule |
| archive ranking | inline empty-wiki fallback | separate archive lane |
| importance score | loaded but historically not scored | formula includes `importance_weight` |
| training trigger | `train_rerank_model()` exists but is not stage-owned | Stage 4 training hook |
| feedback hierarchy | implicit `selected/useful` fields | explicit exposure vs usefulness split |

---

## Completion Gate

Stage 4 is complete when:

1. `rerank()` returns typed ranked hits from Stage 3 fixtures.
2. exact, wiki, and archive candidates are tested separately.
3. final score includes importance and quality signals.
4. deterministic tie-breaking is stable under fixed inputs.
5. `retrieval_hit_features` can log all learned features.
6. training can distinguish explicit labels from passive derived labels.

---

## Reference

**Detailed phase plans:**  
- [Phase 1](retrieval-reranking-plan-phase1.md) · [Phase 2](retrieval-reranking-plan-phase2.md) · [Phase 3](retrieval-reranking-plan-phase3.md)
- [Phase 4](retrieval-reranking-plan-phase4.md) · [Phase 5](retrieval-reranking-plan-phase5.md)

**Governing plan:** [retrieval-reranking-plan.md](retrieval-reranking-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
