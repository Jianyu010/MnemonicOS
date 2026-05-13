# Retrieval Signal Integration Roadmap — Stage 3

## Status Key

- `done` — implemented and verified
- `partial` — partially implemented, gaps documented
- `not started` — not begun
- `deferred` — intentionally deferred pending gate condition

---

## Phase Status

| Phase | Description | Status |
|-------|-------------|--------|
| S3-P1 | `NoteSignals` / `SignalEnrichedCandidatePool` contract and defaults | done |
| S3-P2 | Batch loaders for trust, freshness, importance, passive feedback | done |
| S3-P3 | Summary/control-signal quality gate | done |
| S3-P4 | Passive feedback evidence vocabulary and confidence classes | done |
| S3-P5 | Integration assembly and instrumentation payload | done |

---

## Implementation Mark

**2026-05-13:** Stage 3 implemented and verified.

Implemented:
- `src/second_brain/signal_integration.py`
- `NoteSignals`, `ArchiveSignals`, `SignalEnrichedCandidate`,
  `SignalEnrichedArchiveCandidate`, and `SignalEnrichedCandidatePool`
- batch loading for trust, freshness, importance, and passive feedback
- summary/control-signal quality classification and `quality_pass`
- freshness multiplier precompute
- passive feedback selection by highest confidence, with
  `passive_misleading_likely` > `passive_useful_likely` >
  `passive_used_likely` as the tie break
- retrieval orchestration path: `parse_query -> recall -> integrate_signals`
- end-to-end feature logging through `retrieval_hit_features`

Verification:
- `python3 -m unittest tests.test_signal_integration` → 6 passed
- `python3 -m unittest tests.test_recall` → 7 passed
- targeted retrieval regression suite → 14 passed
- `python3 -m unittest discover -s tests` → 409 passed

Ingestion design sync:
- `note_passive_feedback` is read when Maintenance migration 026 has been
  applied.
- Pre-migration databases return `passive_feedback_state="none"` and
  `passive_feedback_confidence=0.0`.
- Stage 3 remains read-only and does not read
  `promotion_decisions.retrieval_attribution_json`.

---

## Legacy State

Previous behavior existed as inline enrichment inside `retrieve()`. It has been
migrated to `integrate_signals()`, with Stage 4 reranking still inline until the
Stage 4 implementation pass.

| Gap | Legacy location | Target |
|-----|----------------|--------|
| typed feature boundary | `retrieval.py:retrieve()` local candidate mutation | `integrate_signals()` |
| trust/freshness loading | inline calls to `load_note_trust()` / `load_note_freshness()` | Stage 3 loader |
| importance loading | inline SQL map, carried weakly | typed signal and Stage 4 feature |
| quality gate | mostly runtime-side `_hit_has_content_signal()` | Stage 3 candidate signal |
| freshness multiplier | computed inside scoring loop | Stage 3 precompute |
| passive feedback vocabulary | not explicit | Stage 3/maintenance shared definitions |

---

## Completion Gate

Stage 3 completion gate:

1. `integrate_signals(parsed_query, candidate_pool, ...)` exists. Done.
2. Every wiki candidate carries typed `NoteSignals`. Done.
3. Archive candidates carry separate archive signals. Done.
4. Missing trust/freshness/importance rows are deterministic and tested. Done.
5. Weak summaries and empty content fail quality with reason codes. Done.
6. Stage 4 consumes Stage 3 output without extra DB reads. Partial: current
   inline reranking consumes Stage 3 output; full Stage 4 module extraction
   remains in the Stage 4 roadmap.
7. Materialized passive feedback is loaded from `note_passive_feedback`. Done,
   with absent-table fallback for workspaces before Maintenance migration 026.

---

## Reference

**Detailed phase plans:**  
- [Phase 1](retrieval-signal-integration-plan-phase1.md) · [Phase 2](retrieval-signal-integration-plan-phase2.md) · [Phase 3](retrieval-signal-integration-plan-phase3.md)
- [Phase 4](retrieval-signal-integration-plan-phase4.md) · [Phase 5](retrieval-signal-integration-plan-phase5.md)

**Governing plan:** [retrieval-signal-integration-plan.md](retrieval-signal-integration-plan.md)  
**Overall roadmap:** [retrieval-development-roadmap.md](retrieval-development-roadmap.md)  
**Governance:** [RETRIEVAL-PATH-GOVERNANCE-DESIGN.md](RETRIEVAL-PATH-GOVERNANCE-DESIGN.md)
