# Retrieval Recall Phase 6: Candidate Merge and Structural Separation

## Goal

Merge all wiki-side recall channels into a deduplicated `wiki_candidates` list,
preserve per-channel provenance, and keep archive/raw candidates structurally
separate in the final `CandidatePool`.

## Scope

- `merge_wiki_candidates()` function
- pool-level metrics computation
- final `CandidatePool` construction
- no scoring, no assessment

## Components

```
src/second_brain/recall.py
```

## Merge Rules

1. Merge key is note ID.
2. Preserve all contributing channels.
3. Preserve per-channel ranks/scores on the merged candidate.
4. Graph may enrich direct candidates or introduce new adjacent candidates.
5. Archive candidates are never merged into `wiki_candidates`.
6. Total merged wiki candidates must remain bounded.

## Structural Separation

The final `CandidatePool` has:

- `wiki_candidates`: canonical note candidates only
- `archive_candidates`: raw/archive candidates only

This separation is a contract, not a UI choice.

## Failure Policy

Merging empty channel outputs yields an empty but valid `CandidatePool`.

## Verification

- one note surfaced by multiple channels appears once in `wiki_candidates`
- merged candidates preserve multi-channel provenance
- archive candidates remain outside `wiki_candidates`
- `RecallMetrics` counts reflect pre-merge and post-merge volumes
