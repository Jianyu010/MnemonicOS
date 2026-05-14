# Retrieval Reranking Phase 4: Caps, Diversification, and Stability

## Goal

Keep ranked output useful and deterministic under broad recall pools. The fixed
`top_k = 5` config parameter is replaced by a dynamic strategy that adapts to
pool size while enforcing a token budget cap and hard maximum.

## Scope

- Dynamic top-k replacing fixed `top_k` config parameter
- Token budget cap on ranked output
- Deterministic tie-breaking
- Optional type-bucket caps
- Duplicate/same-chain handling
- Stable ranking under equal scores

## Dynamic Top-K Strategy

After the relevance gate (Phase 6) suppresses below-threshold candidates, the
remaining passing pool drives a dynamic emission count:

```text
passing_pool   = candidates with relevance_score >= min_relevance_score
pool_size      = len(passing_pool)

target_k       = max(min_hits, ceil(pool_size * pool_fraction))
target_k       = min(target_k, max_hits)

# Token budget backstop (prevent context block from exceeding budget)
available_tokens = total_context_budget - pinned_token_budget
budget_k         = floor(available_tokens / tokens_per_hit_estimate)
final_k          = min(target_k, budget_k)
```

### Config Parameters

```toml
[retrieval]
min_hits              = 3      # floor: emit at least this many if pool allows
max_hits              = 15     # ceiling: never emit more than this
pool_fraction         = 0.10   # emit top 10% of passing pool
tokens_per_hit_estimate = 120  # rough tokens per hit in compact format
# top_k deprecated — remove from config; ignored if present
```

### Rationale

**Why 10% of pool?**  
BM25 recall pools typically hold 20–100 candidates. 10% yields 2–10 hits, which
combined with the `min_hits=3` floor and `max_hits=15` ceiling covers the useful
range without flooding the context. For large wikis (200+ notes), the 15-hit
ceiling prevents context bloat.

**Why min_hits=3?**  
Procedures and decision chains often span 2–4 related notes. A single hit can
give a misleading partial picture. Three hits gives Stage 5 enough to detect
simple chains.

**Why max_hits=15?**  
At 120 tokens/hit compact estimate, 15 hits ≈ 1800 tokens. Combined with 2000
pinned tokens, total context stays within 4000 tokens — well under most LLM
context budgets. Emitting more hits adds noise faster than signal for typical
queries. For deep research sessions needing full chain context, `max_hits` can be
raised in config.

**On token budget (total_context_budget = 6000):**  
6000 tokens for the full retrieval block is appropriate for agent-hook usage
where retrieval competes with other system context. 10k would increase noise and
LLM processing cost without proportional benefit for typical episodic memory
queries. Reserve 10k+ only for full-body-hydrated deep research paths, controlled
by a separate `hydration_policy` from Stage 6.

### Warning Candidate Preservation

After computing `final_k` for primary emission, Stage 4 must retain an
additional buffer of up to 5 quality-failed or stale candidates for Stage 5/6
assessment use. These are not counted toward `final_k` and are not emitted to
the caller but are available to Stage 5 assembly for contradiction and warning
slot population.

## Rules

1. Sort by descending `composite_score` (Phase 6), then stable title/id
   tie-breakers.
2. Apply dynamic top-k via the formula above, not a fixed `top_k` parameter.
3. The `top_k` config parameter is deprecated. If present, it is ignored by
   Stage 4. Callers may still pass `--top-k` as an override cap (applies as an
   additional `min(target_k, caller_top_k)` constraint).
4. Preserve up to 5 additional warning/stale candidates beyond `final_k` for
   Stage 5/6 use; do not count them in the emitted hit list.
5. Avoid flooding top results with same-chain duplicates when distinct evidence
   is available. Apply a per-chain cap of ceil(final_k / 3) for chain deduplication.
6. Diversification must be deterministic and testable; no random sampling.
7. If `pool_size < min_hits`, emit all passing candidates (no artificial padding).

## Verification

- `pool_fraction=0.10` with pool=20 → target_k=max(3, 2)=3
- `pool_fraction=0.10` with pool=80 → target_k=max(3, 8)=8
- `pool_fraction=0.10` with pool=200 → target_k=min(20, 15)=15 (max_hits ceiling)
- token budget: available=4000, estimate=120 → budget_k=33; max_hits=15 is binding
- equal-score candidates sort stably
- top-k cap is enforced
- repeated same-chain candidates do not crowd out all other types when caps apply
- warning candidates (up to 5) preserved beyond final_k

---

## Reference

**Governing plan:** [retrieval-reranking-plan.md](retrieval-reranking-plan.md)
**Phase 6 (relevance gate):** [retrieval-reranking-plan-phase6.md](retrieval-reranking-plan-phase6.md)
