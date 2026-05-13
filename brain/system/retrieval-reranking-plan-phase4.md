# Retrieval Reranking Phase 4: Caps, Diversification, and Stability

## Goal

Keep ranked output useful and deterministic under broad recall pools.

## Scope

- top-k cap
- deterministic tie-breaking
- optional type-bucket caps
- duplicate/same-chain handling
- stable ranking under equal scores

## Rules

1. Sort by descending `final_score`, then stable title/id tie-breakers.
2. Apply requested `top_k` after scoring.
3. Preserve enough lower-ranked warning candidates for Stage 5/6 if needed.
4. Avoid flooding top results with same-chain duplicates when distinct evidence
   is available.
5. Diversification must be deterministic and testable; no random sampling.

## Verification

- equal-score candidates sort stably
- top-k cap is enforced
- repeated same-chain candidates do not crowd out all other types when caps apply
- warning candidates remain available for assessment when policy requires
