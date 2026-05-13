# Retrieval Reranking Phase 1: RankedHits Contract and Lane Separation

## Goal

Define the typed Stage 4 output and preserve the wiki/archive lane separation
established by Stage 2 and enriched by Stage 3.

## Scope

- `RankedHits`
- `ScoredCandidate`
- `ScoredArchiveCandidate`
- ranked feature payload for logging

## Components

```text
src/second_brain/reranker.py       — new Stage 4 module
src/second_brain/models.py         — shared dataclasses if preferred
src/second_brain/retrieval.py      — migrate inline scoring out
```

## Contract Rules

1. Wiki hits and archive hits remain separate lists.
2. Each scored candidate carries final score, raw score, channel provenance, and
   the Stage 3 signal bundle or a reference to it.
3. Stage 5 decides packet placement; Stage 4 only orders candidates.
4. Stage 4 emits instrumentation-ready feature rows, but usefulness labels are
   attached later by runtime/maintenance.

## Verification

- archive candidate cannot appear in wiki ranked list
- ranked hits preserve candidate IDs and provenance
- Stage 5 fixture can consume `RankedHits` without DB access
