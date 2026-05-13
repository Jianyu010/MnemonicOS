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
| S4-P1 | `RankedHits` contract and wiki/archive lane separation | not started |
| S4-P2 | Heuristic scoring formula with importance and quality | not started |
| S4-P3 | Exact bypass and archive scoring rules | not started |
| S4-P4 | Caps, diversification, deterministic stability | not started |
| S4-P5 | Training labels, weights, and feedback integration | not started |

**Gate:** `train_rerank_model()` triggered by daemon; weights confirmed in
`rerank_weights` table. This fixes the second critical bug from the audit.

---

## Stage 5 — Assembly

**Plan:** retrieval-assembly-plan.md (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S5-P1 | `RawPacket` typed contract with `archive_context` slot | not started |
| S5-P2 | Primary slot fill with quality gate routing | not started |
| S5-P3 | `same_episode_context` via `chain_id` | not started |
| S5-P4 | Related slots (topic / timeline / similarity / contrast) | not started |
| S5-P5 | Cross-slot deduplication | not started |
| S5-P6 | Context budget enforcement (token cap + body truncation) | not started |
| S5-P7 | Contradiction detection and coherence annotation | not started |

**Gate:** archive hits appear only in `archive_context[]`.

---

## Stage 6 — Assessment

**Plan:** retrieval-assessment-plan.md (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S6-P1 | `AssessedPacket` contract + state classifier | not started |
| S6-P2 | Quality gate across ALL primary hits (not just top hit) | not started |
| S6-P3 | Historical recommendation → `reuse_with_verification` (not `reuse`) | not started |
| S6-P4 | Deduplication of assess paths (runtime vs. shell) | not started |

---

## Stage 7 — Emission

**Plan:** retrieval-emission-plan.md (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S7-P1 | `archive_context` slot in JSON + Markdown outputs | not started |
| S7-P2 | Assessment fields (`retrieval_state`, `recommendation`) in direct `retrieve()` path | not started |
| S7-P3 | `runtime_packet_metrics` logging separated from instrumentation | not started |

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
