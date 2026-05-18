# Retrieval Signal Integration Phase 3: Summary and Quality Gate

## Goal

Classify whether each candidate has enough compact content signal to be trusted
as a primary retrieval result.

## Scope

- summary/control-signal quality classifier
- `quality_pass`
- `quality_reasons`
- weak-title and empty-body safeguards

## Rules

1. Stage 3 may inspect title, summary, compact control signal, excerpt, and
   metadata already carried by Stage 2.
2. Stage 3 must not read full note bodies for ranking decisions.
   Post-selection inspection hydration in direct retrieve output is allowed
   only after ranking and must not affect `quality_pass` or score features.
3. A summary equal to the title is `title_only`.
4. Missing or placeholder summary is `none`.
5. Vague summaries with no action, claim, decision, failure, entity, or relation
   signal are `weak`.
6. Useful summaries or gene/control strings can support `quality_pass=True`.
7. `quality_pass=False` does not remove the candidate; it becomes a warning and
   a demotion input downstream.

## Reason Codes

Suggested initial codes:

- `missing_summary`
- `title_only_summary`
- `placeholder_summary`
- `empty_content_signal`
- `weak_actionability`
- `fresh_but_empty`

## Verification

- empty fresh notes fail quality
- title-only summaries fail quality
- useful control-signal summaries pass quality
- suspect/stale notes can pass content quality while still carrying freshness warnings
