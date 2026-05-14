# Retrieval Path Development Roadmap

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase 0 — Audit ✓ done

**Output:** `docs/east-flange-audit.md`
**Verdict:** extend — algorithms sound, 2 critical bugs, missing stage boundaries
**Date:** 2026-05-11

Critical bugs found:
- `importance_score` loaded per-query but not in scoring formula (dead code)
- `train_rerank_model()` exists but never called (training unreachable)

---

## Stage 1 — East Flange (Query Parsing)

**Plan:** [retrieval-east-flange-plan.md](retrieval-east-flange-plan.md)
**Roadmap:** [retrieval-east-flange-roadmap.md](retrieval-east-flange-roadmap.md)

| Phase | Description | Status |
|-------|-------------|--------|
| S1-P1 | `ParsedQuery` contract + tokenizer (+ `caller_type`, `persona`, `back_feed_hook`) | not started |
| S1-P2 | Caller identity detection (`detect_caller()`, `AGENT_BRIDGE_IDS`) | not started |
| S1-P3 | Persona classifier (`knowledge` · `skill` · `rag`, `BACK_FEED_HOOKS`) | not started |
| S1-P4 | Semantic type classifier (6 types, `TYPE_PRIORS`, `QUERY_KEYWORD_SIGNATURES`) | not started |
| S1-P5 | Temporality inference (4 states, `"unknown"` default) | not started |
| S1-P6 | Anchor term extraction (alias table + note ID prefix) | not started |
| S1-P7 | Term expansion (`SYNONYM_MAP` + alias title tokens, cap 3) | not started |
| S1-INT | `parse_query()` assembly + legacy migration + integration test | not started |

---

## Stage 2 — Multi-Channel Recall

**Plan:** [retrieval-recall-plan.md](retrieval-recall-plan.md)
**Roadmap:** [retrieval-recall-roadmap.md](retrieval-recall-roadmap.md)

| Phase | Description | Status |
|-------|-------------|--------|
| S2-P1 | `CandidatePool` contract + exact channel | done |
| S2-P2 | BM25 channel (expanded terms, single query prep) | done |
| S2-P3 | Semantic channel (single query-vector encode, no double-encode) | done |
| S2-P4 | Graph channel (bounded seeds, depth-1 expansion, cold-start handling) | done |
| S2-P5 | Archive channel with policy gate | done |
| S2-P6 | Candidate merge + structural wiki/archive separation | done |

**Gate:** `CandidatePool` correctly separates wiki and archive candidates.

---

## Stage 3 — Signal Integration

**Plan:** [retrieval-signal-integration-plan.md](retrieval-signal-integration-plan.md)
**Roadmap:** [retrieval-signal-integration-roadmap.md](retrieval-signal-integration-roadmap.md)

| Phase | Description | Status |
|-------|-------------|--------|
| S3-P1 | `NoteSignals` / `SignalEnrichedCandidatePool` contract and defaults | done |
| S3-P2 | Batch loaders for trust, freshness, importance, passive feedback | done |
| S3-P3 | Summary/control-signal quality gate | done |
| S3-P4 | Passive feedback evidence vocabulary and confidence classes | done |
| S3-P5 | Integration assembly and instrumentation payload | done |

**Gate:** v1 complete. `integrate_signals()` exists, Stage 3 emits typed
enriched candidates, `importance_score` is carried into retrieval feature
logging, passive feedback is loaded from `note_passive_feedback` when migration
026 is present, and the east-flange → recall → signal-integration path is
covered by tests. Pre-migration databases fall back to deterministic passive
feedback defaults.

---

## Stage 4 — Reranking

**Plan:** [retrieval-reranking-plan.md](retrieval-reranking-plan.md)
**Roadmap:** [retrieval-reranking-roadmap.md](retrieval-reranking-roadmap.md)

| Phase | Description | Status |
|-------|-------------|--------|
| S4-P1 | `RankedHits` contract and wiki/archive lane separation | done |
| S4-P2 | Heuristic scoring formula with importance and quality | done |
| S4-P3 | Exact bypass and archive scoring rules | done |
| S4-P4 | Caps, diversification, deterministic stability, dynamic top-k | done |
| S4-P5 | Training labels, weights, and feedback integration | done |
| S4-P6 | Multiplicative scoring, absolute BM25, relevance gate, 2D score vector | done |

**Gate:** v1 complete for the local-linear reranking path. `rerank()` owns final
scoring, `retrieval.py` no longer carries the scoring formula, importance and
quality participate in final scores, archive candidates remain in their own
lane, feature logging includes passive/quality metadata, and `rerank_training`
is available as a daemon job for scalar weight calibration. Phase 6 adds a
multiplicative score model (`final_score = relevance × quality`) with an
absolute relevance gate that suppresses irrelevant candidates before emission,
plus dynamic top-k replacing the fixed `top_k=5` cap. This is distinct from
the future DL retrieval north star.

---

## Stage 5 — Assembly

**Plan:** retrieval-assembly-plan.md
**Roadmap:** retrieval-assembly-roadmap.md

| Phase | Description | Status |
|-------|-------------|--------|
| S5-P1 | Packet contract and explicit `archive_context` slot | done |
| S5-P2 | Primary readout selection and quality/warning routing | done |
| S5-P3 | Same-episode context and chain path roles via `chain_id` / session lineage | done |
| S5-P4 | Related slots, functional path reconstruction, bounded trace completion, and assessment reservoir | done |
| S5-P5 | Cross-slot deduplication, stable slot ordering, and score-vs-functional order preservation | done |
| S5-P6 | Context budget enforcement and body references | done |
| S5-P7 | Contradictions, warnings, trace metadata, and Stage 6 handoff notes | done |

**Gate:** archive hits appear only in `archive_context[]`; bounded trace
completion is relation-only, purpose-aware, and emits assessment reservoir
metadata for Stage 6.

---

## Stage 6 — Assessment

**Plan:** [retrieval-assessment-plan.md](retrieval-assessment-plan.md)
**Roadmap:** [retrieval-assessment-roadmap.md](retrieval-assessment-roadmap.md)

| Phase | Description | Status |
|-------|-------------|--------|
| S6-P1 | `AssessedPacket` and `AssessmentDirective` contract | done |
| S6-P2 | Trustworthiness classifier and compatibility `retrieval_state` | done |
| S6-P3 | Functional completeness classifier over path/reservoir metadata | done |
| S6-P4 | Progressive disclosure branch selection and hydration policy | done |
| S6-P5 | Fresh Stage 2-5 threshold policy | done |
| S6-P6 | Runtime/shell/API assessment path consolidation | done |
| S6-P7 | Metrics, degradation behavior, and Stage 7 handoff fixtures | partial |

**Gate:** Stage 6 emits `AssessmentDirective` from a pure assessor module;
runtime compatibility fields are driven by the directive. HTTP parity passes;
directive-specific metrics remain as follow-up work.

---

## Stage 7 — Emission

**Plan:** [retrieval-emission-plan.md](retrieval-emission-plan.md)
**Roadmap:** [retrieval-emission-roadmap.md](retrieval-emission-roadmap.md)

| Phase | Description | Status |
|-------|-------------|--------|
| S7-P1 | Emission contract, render model, and output profiles | not started |
| S7-P2 | Canonical JSON renderer and direct/runtime parity | not started |
| S7-P3 | Markdown renderer and full selected-packet presentation | not started |
| S7-P4 | Archive, warning, contradiction, and missing-role presentation | not started |
| S7-P5 | Directive-guided branch filtering, deferred context, and focused requery handoff | not started |
| S7-P6 | Directive-specific metrics and retrieval trace emission | not started |
| S7-P7 | Shell/API consolidation and no-raise degraded output | not started |

**Gate:** Stage 7 formats from `AssessmentDirective + MemoryPacket` without
reassessment, keeps JSON/Markdown semantically equivalent, surfaces
`archive_context` separately, emits focused requery instructions as
caller-mediated handoffs with stable anchors for the next pass, supports
orchestrator-owned automatic focused retry with hard cutoffs, returns composed
multi-pass output when retry runs, and records directive metrics as
instrumentation.

---

## Cross-cutting — Control Signal Quality

**Roadmap:** [retrieval-control-signal-quality-roadmap.md](retrieval-control-signal-quality-roadmap.md)

Addresses signal quality failures causing agent loops despite relevant memory.
Spans Stages 3, 5, 6 and wiki content. See roadmap for phase status.

---

## Phase 2 — Signal Enrichment

Requires ≥50 eval samples in `retrieval_hits` with useful labels.

| Item | Status |
|------|--------|
| Recency signal (current queries, sigmoid over days_since_created, weight 0.05) | deferred |
| Semantic neighbor promotion from `note_embedding_scans` into `related_by_similarity` | deferred |
| Episode-aware phases 2–6 (per `episode-aware-retrieval.md`) | deferred |

---

## Phase 3 — DL North Star

Requires ≥200 training samples. Architecture in
[RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md).

| Component | Status |
|-----------|--------|
| Tower A — query encoder (BoW → 256) | not started |
| Tower B — note encoder (BoW + structural → 256, stored in `note_vectors`) | not started |
| ANN index (FAISS or sqlite-vec) | not started |
| Cross-encoder reranker (522 → 128 → 32 → 1) | not started |
| Joint training pipeline (InfoNCE + BCE) | not started |
| `brain/data/east_flange_model.npz` artifact | not started |
| Graduated rollout by sample count | not started |

---

## Dependency Graph

```
Phase 0 (Audit) → Stage 1 → Stage 2 → Stage 3 → Stage 4 → Stage 5 → Stage 6 → Stage 7
                                                                                      │
                                             [≥50 eval samples] → Phase 2 ───────────┤
                                             [≥200 samples]     → Phase 3 (DL) ──────┘
```

Stages implement in order. Each stage's typed output is the next stage's typed input.
A stage is complete only when its downstream stage accepts its output in an
end-to-end integration test.
