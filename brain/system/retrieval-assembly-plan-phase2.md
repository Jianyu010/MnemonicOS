# Retrieval Assembly Phase 2: Primary Readout and Quality Routing

## Goal

Select the primary canonical memories that form the main assembled note, while
preserving quality and freshness concerns for later assessment.

## Scope

- primary slot selection from ranked wiki hits
- type-bucket caps and diversity
- quality-failed, suspect, stale, contested, and archived lifecycle routing
- warning-only candidates

## Components

```text
src/second_brain/runtime.py      — eventual primary selection logic
src/second_brain/models.py       — packet item fields
tests/test_runtime_protocol.py   — eventual protocol fixtures
```

## Selection Rules

1. Primary memories come only from ranked wiki hits, never archive chunks.
2. Higher-ranked candidates are considered first, but type buckets prevent one
   note class from dominating the assembled note.
3. A weak or suspect hit is not silently dropped if it explains uncertainty.
   It is routed into warnings, verification needs, or assembly notes.
4. The primary slot should represent what the caller can reuse directly, not
   every relevant thing retrieval found.
5. Primary selection preserves Stage 4 score order as evidence priority, but
   does not force final readout order. A selected procedure can be the current
   reusable conclusion while failures and pivots appear earlier in
   `functional_path`.

## Handoff Fields

Stage 6 needs to know:

- which primary IDs were selected
- which candidates were excluded because of quality or budget
- which selected primaries have stale/suspect/contested signals
- whether primary coverage is absent, thin, or archive-only
- whether a primary memory is a current conclusion, a failed attempt, a
  diagnosis, or a decision/pivot in the reconstructed path

## Verification

- archive candidates cannot enter `primary_memories`
- low-quality candidates are visible as warnings or verification needs
- type caps keep procedures, facts, episodes, and context notes balanced
- primary score order is retained in metadata even when assembly order differs
