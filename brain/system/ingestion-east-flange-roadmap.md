# Ingestion: East-Flange Roadmap

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase 0 — Audit ✓ done

Audit the legacy east flange implementation against the 7-stage pipeline.
Produce module-to-stage map, debt list, bug inventory, extend/redesign verdict.

**Output:** `docs/east-flange-audit.md`
**Verdict:** extend — algorithms sound, 2 critical bugs, missing stage boundaries
**Date completed:** 2026-05-11

---

## Stage 1 — Query Parsing

**Governing plan:** `brain/system/ingestion-ingestion-east-flange-overview-stage1.md`

| Phase | Description | Status |
|-------|-------------|--------|
| S1-P1 | `ParsedQuery` typed contract + tokenizer | not started |
| S1-P2 | Query type classifier (clean module, typed output) | not started |
| S1-P3 | Temporality inference (current / historical / mixed / unknown) | not started |
| S1-P4 | Anchor term extraction | not started |
| S1-P5 | Term expansion (synonym map + alias table) | not started |

**Gate:** S1 complete when `ParsedQuery` is accepted as input by Stage 2 (end-to-end test passes).

---

## Stage 2 — Multi-Channel Recall

**Governing plan:** `brain/system/ingestion-ingestion-east-flange-overview-stage2.md` (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S2-P1 | `CandidatePool` typed contract + exact channel | not started |
| S2-P2 | BM25 channel using expanded query terms | not started |
| S2-P3 | Semantic channel (single query vector encode) | not started |
| S2-P4 | Graph channel with cold-start handling | not started |
| S2-P5 | Archive channel with policy gate | not started |
| S2-P6 | Candidate merging and deduplication | not started |

**Gate:** S2 complete when `CandidatePool` correctly separates wiki and archive candidates.

---

## Stage 3 — Signal Integration

**Governing plan:** `brain/system/ingestion-ingestion-east-flange-overview-stage3.md` (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S3-P1 | `NoteSignals` typed struct | not started |
| S3-P2 | Batch signal load (trust, freshness, importance) | not started |
| S3-P3 | Content quality gate (`quality_pass` field) | not started |
| S3-P4 | Freshness multiplier precompute | not started |

**Gate:** S3 complete when `importance_score` is loaded and carried through to Stage 4.

**Known legacy bug:** `importance_score` is loaded but unused in current formula.
Fixing this is the primary S3 deliverable.

---

## Stage 4 — Reranking

**Governing plan:** `brain/system/ingestion-ingestion-east-flange-overview-stage4.md` (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S4-P1 | Scoring formula with all signals (including importance) | not started |
| S4-P2 | Separate archive scoring | not started |
| S4-P3 | Training trigger: `mnemonic_eval_train.sh` + daemon job | not started |
| S4-P4 | Weight key name alignment (trust.py ↔ config) | not started |
| S4-P5 | Instrumentation logging moved to Stage 4 (out of Stage 2 monolith) | not started |

**Gate:** S4 complete when `train_rerank_model()` is triggered by daemon and weights
confirmed in `rerank_weights` table.

**Known legacy bug:** `train_rerank_model()` exists but is never called. Training data
from `evals/candidates.jsonl` (82 miss candidates) not connected to training loop.

---

## Stage 5 — Assembly

**Governing plan:** `brain/system/ingestion-ingestion-east-flange-overview-stage5.md` (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S5-P1 | `RawPacket` typed contract with `archive_context` slot | not started |
| S5-P2 | Primary slot fill with quality gate routing | not started |
| S5-P3 | `same_episode_context` via chain_id | not started |
| S5-P4 | Related slots (topic / timeline / similarity / contrast) | not started |
| S5-P5 | Cross-slot deduplication | not started |
| S5-P6 | Context budget enforcement (token cap + body truncation) | not started |
| S5-P7 | Contradiction detection and coherence annotation | not started |

**Gate:** S5 complete when archive hits appear only in `archive_context[]` and
`primary_memories[]` is empty if no wiki hit passes quality gate.

**Known legacy gap:** archive hits currently routed through the same slot as wiki hits
(filtered by type check, not structural separation). No `archive_context` field in output.

---

## Stage 6 — Assessment

**Governing plan:** `brain/system/ingestion-ingestion-east-flange-overview-stage6.md` (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S6-P1 | `AssessedPacket` contract + state classifier | not started |
| S6-P2 | Quality gate across ALL primary hits (not just top hit) | not started |
| S6-P3 | Recommendation and verification_action derivation | not started |
| S6-P4 | Deduplication of assess paths (runtime vs. shell) | not started |

**Gate:** S6 complete when quality gate covers all primary hits and historical recommendation
returns `reuse_with_verification` (not `reuse`).

**Known legacy gap:** quality gate checks top_hit only. Historical queries return `reuse`
(should be `reuse_with_verification`).

---

## Stage 7 — Emission

**Governing plan:** `brain/system/ingestion-ingestion-east-flange-overview-stage7.md` (not yet written)

| Phase | Description | Status |
|-------|-------------|--------|
| S7-P1 | `archive_context` slot in JSON and Markdown outputs | not started |
| S7-P2 | Assessment fields in direct `retrieve()` output path | not started |
| S7-P3 | `runtime_packet_metrics` logging separated from instrumentation | not started |

**Gate:** S7 complete when `archive_context`, `retrieval_state`, `recommendation` all
appear in both JSON and Markdown emission paths.

---

## Phase 2 — Signal Enrichment

Requires ≥50 eval samples in `retrieval_hits` with useful labels.

| Item | Status |
|------|--------|
| Recency signal for current queries | deferred (≥50 samples gate) |
| Semantic neighbor promotion from `note_embedding_scans` | deferred |
| Episode-aware phases 2–6 (per episode-aware-retrieval.md) | deferred |

---

## Phase 3 — DL North Star

Requires ≥200 training samples in `retrieval_hits`.

| Item | Status |
|------|--------|
| Tower A: query encoder (BoW → 256) | not started |
| Tower B: note encoder (BoW + structural → 256, stored in note_vectors) | not started |
| ANN index (FAISS or sqlite-vec over note_vectors) | not started |
| Cross-encoder reranker (522 → 128 → 32 → 1) | not started |
| Training pipeline (InfoNCE + BCE joint loss) | not started |
| Graduated rollout by sample count | not started |

**Architecture:** `brain/system/ingestion-ingestion-east-flange-overview.md` § Phase 3

---

## Dependency Graph

```
Phase 0 (Audit) ──► Stage 1 ──► Stage 2 ──► Stage 3 ──► Stage 4 ──► Stage 5 ──► Stage 6 ──► Stage 7
                                                                                                    │
                                                              [≥50 samples] ──► Phase 2 ────────────┤
                                                              [≥200 samples] ─► Phase 3 (DL star) ──┘
```

Stages are implemented in order. Each stage's typed output is the next stage's input.
A stage is not considered complete until its downstream stage accepts its output in an
end-to-end integration test.

---

## Open Architecture Gaps

These are tracked here pending resolution:

- **South boundary:** whether SQLite + FTS + vectors should be formalized as a south
  flange with explicit adapter contracts is an open question per `architecture.md`.
  Stage 2 recall implementation should not hard-code SQLite; abstract the channel
  interface so the backing store can be swapped.
- **Embedding model replacement:** hash-256-v1 is the current default. Ollama and
  OpenAI-compatible providers are pluggable. Wiki-native Tower B (Phase 3) becomes
  the third provider. The provider interface must remain stable across all three.
- **Eval coverage:** only 82 miss candidates exist. Phase 2 signal enrichment and
  Phase 3 DL north star both require more. Retrieval logging infrastructure is in
  place; the gap is usefulness feedback from task_outcomes flowing to retrieval_hits.
