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
| S1-P1 | `ParsedQuery` typed contract + tokenizer (+ `caller_type`, `persona`, `back_feed_hook` fields) | done |
| S1-P2 | Caller identity detection (`detect_caller()`, `AGENT_BRIDGE_IDS`) | done |
| S1-P3 | Persona classifier (`knowledge` · `skill` · `rag`, `BACK_FEED_HOOKS`) | done |
| S1-P4 | Semantic type classifier (6 types, `TYPE_PRIORS`, `QUERY_KEYWORD_SIGNATURES`) | done |
| S1-P5 | Temporality inference (4 states, `"unknown"` default replacing legacy `"current"`) | done |
| S1-P6 | Anchor term extraction (alias table + note ID prefix, single DB read) | done |
| S1-P7 | Term expansion (`SYNONYM_MAP` + alias title tokens, cap 3) | done |
| S1-INT | `parse_query()` assembly + legacy migration + integration test | done |

---

## Legacy State

Partial implementations exist inline. All are superseded by this stage.

| Gap | Legacy location | Target |
|-----|----------------|--------|
| Typed `ParsedQuery` output | bare `tuple[str\|None, float]` in `retrieval.py` | frozen `ParsedQuery` dataclass |
| Caller identity | not modeled | `detect_caller()` from `bridge_id` |
| Persona classification | not modeled | `classify_persona()` — 3 values |
| Temporality | 2 states, defaults `"current"` (`trust.py:72`) | 4 states, defaults `"unknown"` |
| Temporality duplication | computed independently in `retrieve()` and `runtime.py` | computed once in `parse_query()` |
| Semantic type classifier | `_classify_query()` inline in `retrieval.py:126` | `classify_query()` in `query_parser.py` |
| Keyword signatures | narrow `QUERY_HINTS` in `retrieval.py:55` | expanded primary+secondary `QUERY_KEYWORD_SIGNATURES` |
| Term expansion | none | `SYNONYM_MAP` + alias titles |
| Anchor extraction | implicit (exact channel in Stage 2) | explicit `anchor_terms` field |
| Module boundary | logic inline in `retrieval.py` + `trust.py` | `query_parser.py` |

---

## Completion Gate

Stage 1 complete when:

1. `parse_query()` is the sole query-parsing entry point — `_classify_query()`
   removed from `retrieval.py`
2. `ParsedQuery` carries `caller_type`, `persona`, `back_feed_hook` in addition
   to all originally planned fields
3. All S1 phase unit tests pass (`tests/test_query_parser.py`)
4. `ParsedQuery` accepted by Stage 2 `recall()` in end-to-end integration test
5. `trust.query_temporality()` deprecated as canonical path
6. No regression on `evals/queries.jsonl` top-1 recall vs. legacy baseline

---

## Reference

**Detailed phase plans:**
- [Phase 1](retrieval-east-flange-plan-phase1.md) · [Phase 2](retrieval-east-flange-plan-phase2.md) · [Phase 3](retrieval-east-flange-plan-phase3.md) · [Phase 4](retrieval-east-flange-plan-phase4.md)
- [Phase 5](retrieval-east-flange-plan-phase5.md) · [Phase 6](retrieval-east-flange-plan-phase6.md) · [Phase 7](retrieval-east-flange-plan-phase7.md) · [Integration](retrieval-east-flange-plan-int.md)

**Governing plan:** [retrieval-east-flange-plan.md](retrieval-east-flange-plan.md)
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
