# Retrieval Signal Integration Phase 2: Maintenance Signal Loading

## Goal

Batch-load trust, freshness, importance, and materialized passive feedback for
all wiki candidates without per-candidate SQL loops.

## Scope

- `load_note_trust(connection, note_ids)`
- `load_note_freshness(connection, note_ids)`
- `note_importance.importance_score`
- `note_passive_feedback.feedback_state` + `confidence` once migration 026 exists
- normalization into `NoteSignals`

## Loading Rules

1. Load all wiki candidate IDs in one batch per signal family.
2. Never load archive candidates from canonical wiki maintenance tables.
3. Preserve raw maintenance fields needed by Stage 5/6 warnings:
   `freshness_state`, contradiction count if available, miss/failure counts if
   represented in the signal model.
4. Convert `staleness_score` to `freshness_score = max(0, 1 - staleness_score)`.
5. Keep `importance_score` as a first-class signal so Stage 4 can fix the audit
   bug where importance was loaded but not scored.
6. Read `note_passive_feedback` only as a maintenance-owned derived signal. Pick
   one row per note for `NoteSignals`: highest confidence wins; ties prefer
   `passive_misleading_likely`, then `passive_useful_likely`, then
   `passive_used_likely`.
7. Do not read `promotion_decisions.retrieval_attribution_json` in Stage 3.
   Maintenance owns conversion from attribution evidence to passive feedback rows.

## Failure Policy

Signal loading should degrade to defaults when tables are absent during tests or
early migrations, but production migrations should create the expected tables.

## Verification

- all rows loaded in bounded query count
- missing rows receive defaults
- stale/suspect/contested freshness states are preserved
- importance is carried to Stage 4 fixtures
- passive feedback rows populate `passive_feedback_state` and confidence when
  migration 026 is present
