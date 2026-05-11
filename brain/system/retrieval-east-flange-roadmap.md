# East Flange Roadmap — Retrieval Path Stage 1: Query Parsing

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| S1-P1 | `ParsedQuery` typed contract + tokenizer | not started |
| S1-P2 | Query type classifier (typed output, `TYPE_PRIORS` constant) | not started |
| S1-P3 | Temporality inference (4 states, default `"unknown"`) | not started |
| S1-P4 | Anchor term extraction (alias table + note ID prefix) | not started |
| S1-P5 | Term expansion (synonym map + alias title tokens) | not started |
| S1-INT | `parse_query()` assembly + integration test | not started |

---

## Legacy State

A partial implementation exists inline in `retrieval.py` (`_classify_query()`)
and `trust.py` (`query_temporality()`). Gaps identified in Phase 0 audit:

| Gap | Legacy behavior | Target behavior |
|-----|----------------|-----------------|
| Typed output | bare `tuple[str\|None, float]` | frozen `ParsedQuery` |
| Temporality | 2 states, defaults `"current"` | 4 states, defaults `"unknown"` |
| Temporality duplication | computed independently in `retrieve()` and `runtime.py` | computed once in `parse_query()` |
| Term expansion | none | synonym map + alias title (≤3 terms) |
| Anchor extraction | implicit (exact channel in Stage 2) | explicit `anchor_terms` field |
| Module boundary | inline in `retrieval.py` | `query_parser.py` |

---

## Completion Gate

Stage 1 complete when:
1. `parse_query()` is sole entry point — inline classification removed from `retrieval.py`
2. `ParsedQuery` accepted by Stage 2 in end-to-end integration test
3. All unit and integration tests pass
4. No regression on `evals/queries.jsonl` top-1 recall vs. legacy baseline

---

## Reference

**Detailed plan:** [retrieval-east-flange-plan.md](retrieval-east-flange-plan.md)
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
