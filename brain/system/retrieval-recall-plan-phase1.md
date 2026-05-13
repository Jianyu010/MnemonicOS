# Retrieval Recall Phase 1: CandidatePool Contract and Exact Channel

## Goal

Define the Stage 2 data contracts and implement the exact channel that resolves
known identifiers, anchor terms, aliases, and exact-title matches into direct
wiki candidates.

## Scope

- `CandidatePool`, `RecallCandidate`, `ArchiveCandidate`, `RecallMetrics`,
  `ArchivePolicyDecision`
- `run_exact_channel()` function
- exact-match lookup by:
  - full raw query
  - anchor terms from `ParsedQuery.anchor_terms`
  - exact alias
  - exact note ID
  - exact title

## Components

```
src/second_brain/recall.py         — new Stage 2 module
src/second_brain/retrieval.py      — migrate exact logic to Stage 2 entry point
src/second_brain/models.py         — optional shared dataclasses if preferred
```

## Exact Channel Rules

1. Query exact identity on the full raw query first.
2. Query each anchor term from `ParsedQuery.anchor_terms`.
3. Deduplicate by note ID.
4. Preserve exact rank in first-match order.
5. Exclude retired and invalidated notes.
6. Return at most 5 exact candidates.

## Failure Policy

The exact channel never raises for "no match." It returns an empty list.

## Verification

- note ID anchor returns a direct exact candidate
- alias anchor returns the linked note
- exact title returns the linked note
- duplicate anchors do not duplicate candidates
- max exact candidates capped at 5
