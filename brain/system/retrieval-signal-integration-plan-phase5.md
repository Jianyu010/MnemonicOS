# Retrieval Signal Integration Phase 5: Integration Assembly and Instrumentation Payload

## Goal

Assemble enriched candidate pools and expose the feature payload Stage 4 and
instrumentation need without giving Stage 4 direct DB access.

## Scope

- `integrate_signals()` public entry point
- wiki/archive enriched lane separation
- feature payload for `retrieval_hit_features`
- migration out of `retrieval.py`

## Assembly Rules

1. Preserve Stage 2 candidate order until Stage 4 reranks.
2. Attach `NoteSignals` immutably or by copy; do not mutate `RecallCandidate` in
   place if avoidable.
3. Keep archive candidate signals separate.
4. Include all fields required by Stage 4 scoring and training logs:
   exact hit, BM25 rank/score, semantic score, graph score, freshness score,
   type prior, trust, importance, quality, and passive feedback state.
5. Passive feedback payload comes only from `note_passive_feedback`, not from
   raw sessions, extraction candidates, or promotion ledger attribution JSON.
6. Do not write instrumentation rows here; Stage 4/ranking output determines
   which candidates became selected/ranked hits.

## Verification

- Stage 4 can score fixtures from Stage 3 without DB reads
- feature payload contains `importance_score`
- feature payload can carry passive feedback state/confidence after migration 026
- archive candidates cannot appear in wiki lane
- `retrieval.py` becomes orchestration rather than enrichment logic
