# Retrieval Assessment Phase 5: Fresh Pipeline Threshold Policy

## Goal

Decide when the current Stage 5 packet/reservoir is insufficient and a fresh
Stage 2-5 retrieval pass is justified. Stage 6 does not run the new pass; it
only emits a threshold reason and follow-up policy.

## Scope

- `fresh_pipeline_threshold`
- `followup_query_policy`
- missing-role thresholds
- new-error-signature thresholds
- contradiction-blocked thresholds
- archive-only-actionable thresholds
- budget-trimmed-primary thresholds

## Components

```text
src/second_brain/assessor.py     — eventual threshold policy
src/second_brain/models.py       — directive fields
tests/test_runtime_protocol.py   — threshold fixtures
```

## Thresholds

| Threshold | Trigger |
|-----------|---------|
| `none` | Existing reservoir is enough or deferred branches can wait. |
| `no_anchor` | No primary/reservoir item matches query anchors. |
| `missing_required_role` | Required role remains missing after trace completion. |
| `new_error_signature` | Caller provides concrete error/log/test failure not represented in reservoir. |
| `contradiction_blocks_use` | No safe branch can be selected due to contradiction evidence. |
| `archive_only_actionable` | Only raw archive evidence exists for action needing canonical guidance. |
| `budget_trimmed_primary` | Budget pressure removed summaries/references needed for assessment. |

## Policy Rules

1. Do not request a fresh pipeline run merely because deferred reservoir
   branches exist.
2. Prefer `on_first_failure` when a plausible deferred branch exists but there
   is no concrete error signature yet.
3. Use `immediate_focused_requery` for `new_error_signature`,
   `missing_required_role`, and `contradiction_blocks_use`.
4. Use `fresh_research_required` when memory authority is insufficient for the
   action, especially archive-only actionable guidance.
5. Include a compact suggested query seed only if Stage 7 has a field for it;
   otherwise emit the reason code only.

## Error Signature Matching

This phase should be deterministic. It may compare caller text to reservoir
terms, warnings, and branch summaries, but must not call an LLM. Concrete error
signals include:

- stack traces
- exception names
- failing test names
- command stderr
- HTTP/status codes
- log snippets
- exact changed constraints supplied after first attempt

## Verification

- deferred branches alone do not trigger fresh pipeline
- missing required success/diagnosis role triggers `missing_required_role`
- concrete new error triggers `new_error_signature`
- contradiction-blocked packet triggers `contradiction_blocks_use`
- archive-only actionable packet triggers `archive_only_actionable`
- budget-limited primary metadata triggers `budget_trimmed_primary`
