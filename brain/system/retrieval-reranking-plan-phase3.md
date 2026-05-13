# Retrieval Reranking Phase 3: Exact and Archive Ranking Rules

## Goal

Make non-ordinary ranking lanes explicit: exact wiki matches and raw/archive
fallback candidates.

## Exact Rules

1. Exact candidates bypass heuristic scoring.
2. Exact final score is `1_000_000 - exact_rank`.
3. Exact rank is stable from Stage 2 exact-channel order.
4. Exact matches still carry freshness/quality warnings for Stage 5/6.

## Archive Rules

1. Archive candidates are ranked separately from wiki candidates.
2. Archive candidates use archive-channel evidence, initially BM25 rank/score.
3. Archive candidates are never treated as canonical primary memories.
4. Stage 5 places archive candidates only in `archive_context`.

## Verification

- exact match beats non-exact candidate
- exact stale note still carries warning metadata
- archive candidate cannot outrank into wiki lane
- archive rank is deterministic under fixed BM25 ranks
