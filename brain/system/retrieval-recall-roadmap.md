# Retrieval Recall Roadmap — Stage 2: Multi-Channel Recall

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| S2-P1 | `CandidatePool` contract + exact channel | done |
| S2-P2 | BM25 channel (expanded terms, single query prep) | done |
| S2-P3 | Semantic channel (single query-vector encode, no double-encode) | done |
| S2-P4 | Graph channel (bounded seeds, depth-1 expansion, cold-start handling) | done |
| S2-P5 | Archive channel with policy gate | done |
| S2-P6 | Candidate merge + structural wiki/archive separation | done |

---

## Legacy State

Current behavior exists, but as one monolithic block inside `retrieve()`.

| Gap | Legacy location | Target |
|-----|----------------|--------|
| typed stage boundary | `retrieve()` monolith | `recall()` returning `CandidatePool` |
| exact channel contract | `_exact_hits()` | `run_exact_channel()` |
| BM25 over expanded terms | `_bm25_hits(query)` only | Stage 2 BM25 built from `ParsedQuery.expanded_terms` |
| semantic query encode efficiency | query encoded more than once | one encode per retrieve call |
| graph boundedness contract | implicit `graph_expand(...)` call | explicit seeds, depth, and fanout caps |
| archive policy | archive fallback only when no ranked wiki hits | explicit admissibility gate |
| wiki/archive separation | archive injected later by empty-hit fallback | separate candidate lanes in pool |

---

## Completion Gate

Stage 2 is complete when:

1. `recall(parsed_query, ...) -> CandidatePool` exists as a distinct typed stage
2. every recall channel is individually unit-tested
3. the total wiki candidate pool is bounded and deterministic under fixed test
   data
4. archive/raw candidates remain separate from wiki candidates through Stage 2
5. Stage 3 can batch-load signals from the Stage 2 output without reaching back
   into Stage 2 internals
6. raw-intent and normal knowledge queries diverge correctly on archive-policy
   behavior

---

## Reference

**Detailed phase plans:**  
- [Phase 1](retrieval-recall-plan-phase1.md) · [Phase 2](retrieval-recall-plan-phase2.md) · [Phase 3](retrieval-recall-plan-phase3.md)
- [Phase 4](retrieval-recall-plan-phase4.md) · [Phase 5](retrieval-recall-plan-phase5.md) · [Phase 6](retrieval-recall-plan-phase6.md)

**Governing plan:** [retrieval-recall-plan.md](retrieval-recall-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
