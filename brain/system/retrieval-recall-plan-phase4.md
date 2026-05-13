# Retrieval Recall Phase 4: Graph Channel

## Goal

Expand from a bounded seed set of direct wiki candidates to gather nearby
context without materializing the whole linked wiki graph.

## Scope

- `run_graph_channel()` function
- seed selection from top direct candidates
- depth-1 graph expansion only
- fanout and total-result caps
- cold-start behavior when no direct seeds exist

## Components

```
src/second_brain/recall.py
src/second_brain/graph.py
```

## Seed Strategy

Seeds come from the strongest direct candidates by:

1. exact rank
2. BM25 rank
3. semantic score

Use at most `graph.focal_limit` seeds.

## Expansion Rules

- depth = 1 only
- graph channel may add new candidates or enrich existing ones
- preserve `relation_types`
- record `seed_from_ids`
- record `graph_depth = 1`
- cap expansion by `graph.expand_limit`

## Cold Start

If there are no direct wiki candidates, graph expansion does not run.

## Failure Policy

No seeds means `[]`, not an exception.

## Verification

- graph expansion never runs with zero seeds
- graph-added candidates carry `graph_score`
- relation types and seed provenance are preserved
- fanout is capped
