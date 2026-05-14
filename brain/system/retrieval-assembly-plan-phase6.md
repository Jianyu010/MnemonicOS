# Retrieval Assembly Phase 6: Context Budget and Body References

## Goal

Fit the assembled packet into the caller's pre-assessment budget while
preserving the references Stage 7 needs for final body hydration.

## Scope

- token-budget policy
- body references and hashes/paths
- summary and excerpt limits
- slot caps under budget pressure
- budget annotations

## Components

```text
src/second_brain/runtime.py      — eventual budget enforcement
src/second_brain/models.py       — packet reference / summary fields
tests/test_runtime_protocol.py   — eventual budget fixtures
```

## Budget Rules

1. IDs, titles, types, summaries, provenance, freshness/lifecycle state, and
   relation metadata are never dropped.
2. Full body text is not hydrated into Stage 5 packets. Stage 5 carries
   summaries/excerpts plus body paths or hashes for later hydration.
3. Primary memories keep more packet slots than related context under budget
   pressure.
4. Archive context is clearly labeled as raw evidence and carries references,
   not canonical note body text.
5. Budget pressure is recorded in structured assembly notes.
6. Stage 7 owns final full-body hydration, truncation, and emission formatting.

## Handoff Fields

Stage 6 needs to know:

- whether the packet was budget-limited
- which item bodies were deferred to Stage 7 by reference
- whether primary summaries/references remained intact
- whether missing context is due to no hit or budget pressure

## Verification

- summaries and metadata survive severe token budgets
- body references survive severe token budgets
- Stage 6 fixture distinguishes no-hit from budget-limited reference packets
