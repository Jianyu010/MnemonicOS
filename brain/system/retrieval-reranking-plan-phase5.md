# Retrieval Reranking Phase 5: Training Labels, Weights, and Feedback Integration

## Goal

Connect reranking to the long-term feedback loop without making Stage 4 interpret
raw sessions itself.

## Scope

- `rerank_weights` loading and update path
- `retrieval_hit_features` logging payload
- explicit vs passive label weighting
- `note_passive_feedback` as the maintenance-owned passive label source
- training trigger ownership

## Label Classes

| Class | Source | Suggested weight |
|-------|--------|------------------|
| explicit positive | `retrieval_hits.useful = 1` from runtime outcome | 1.0 |
| explicit negative | `retrieval_hits.useful = 0` from runtime outcome | 1.0 |
| passive used | `note_passive_feedback.feedback_state = passive_used_likely` | 0.2-0.5 × confidence |
| passive useful | `note_passive_feedback.feedback_state = passive_useful_likely` | 0.3-0.7 × confidence |
| passive negative | `note_passive_feedback.feedback_state = passive_misleading_likely` | 0.3-0.7 × confidence |
| exposure only | hit/selected without outcome | 0.0 for usefulness, analytics only |

## Passive Label Rule

Passive labels become training labels only after promotion/maintenance has
materialized them with provenance and confidence in `note_passive_feedback`.
Stage 4 never reads raw sessions, extraction candidates, or
`promotion_decisions.retrieval_attribution_json`, and never makes LLM
attribution calls.

## Training Rules

1. Use config/default weights until enough labeled examples exist.
2. Store learned weights in `rerank_weights`.
3. Keep fallback weights available when training data is sparse or invalid.
4. Include `importance_score` and `quality_pass` in feature rows.
5. Preserve label provenance and confidence from `note_passive_feedback`.
6. Treat `extraction_inline` rows as use evidence only; do not train them as
   outcome-quality success/failure labels.
7. Permit `passive_useful_likely` and `passive_misleading_likely` only from
   `maintenance_post_hoc` rows or equivalent maintenance-authorized derivations.

## Verification

- `train_rerank_model()` can ignore exposure-only rows
- explicit labels dominate passive labels
- passive labels with confidence affect training proportionally
- `extraction_inline` rows never become misleading labels
- invalid/missing weight rows fall back to defaults
