# Retrieval Assembly Phase 3: Same-Episode Context

## Goal

Add near-context from the same task, session, or chain without letting episode
material overwhelm the canonical readout.

## Scope

- `chain_id` expansion
- session lineage fallback if/when available
- episode-role ordering
- chain-local functional roles: goal, failed attempt, diagnosis, decision,
  retry, success, unresolved follow-up
- interaction with primary-slot deduplication

## Components

```text
src/second_brain/runtime.py      — eventual same-episode loader
src/second_brain/graph.py        — possible lineage relation source
tests/test_runtime_protocol.py   — eventual chain fixtures
```

## Context Rules

1. Same-episode context is anchored by primary memories, not by every candidate.
2. Procedure-primary packets may include incident/decision/concept context that
   explains why the procedure exists.
3. Incident-primary packets prefer procedure, decision, and concept context that
   explains repair or outcome.
4. Same-episode context is capped and deduped after primary selection.
5. Same-episode context should preserve the chain's functional sequence when
   relation evidence exists. Do not show a successful resolution before the
   failed attempts and pivots that explain it merely because it ranked higher.

## Handoff Fields

Stage 6 needs to know:

- chain IDs used for expansion
- whether context is same-episode or merely topically related
- whether the episode chain contains failures, decisions, or unresolved
  follow-up
- per-item `assembly_role` and `assembly_order` when the chain supports path
  reconstruction

## Verification

- same-chain notes appear in `same_episode_context`, not primary by accident
- duplicate primary IDs are removed from same-episode context
- no-chain factual queries remain flat and do not invent episode context
- scattered same-chain failure and success notes reconstruct in causal order,
  with Stage 4 scores still retained separately
