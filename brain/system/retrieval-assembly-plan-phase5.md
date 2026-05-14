# Retrieval Assembly Phase 5: Deduplication and Slot Coherence

## Goal

Make the packet stable and coherent when the same note arrives through several
routes: exact, BM25, semantic, graph, same-episode, or relation expansion.

## Scope

- cross-slot deduplication
- authority order between slots
- stable tie-breaking
- separate preservation of score order and functional readout order
- packet-level coherence checks

## Components

```text
src/second_brain/runtime.py      — eventual packet coherence pass
tests/test_runtime_protocol.py   — eventual duplicate-route fixtures
```

## Deduplication Rules

1. Earlier, higher-authority slots win. Proposed order: primary,
   same-episode, topic, timeline, similarity, contrast.
2. Archive context has a separate namespace and is deduped only against archive
   chunks.
3. Deduplication must preserve all useful provenance. If a note appears through
   several routes, the retained item should include merged channel/relation
   evidence when feasible.
4. Slot order is deterministic for fixed ranked input.
5. Each retained item should preserve both rank metadata (`final_score`,
   rank/channel provenance) and assembly metadata (`assembly_role`,
   `assembly_order`) when functional ordering is available.

## Handoff Fields

Stage 6 needs to know:

- whether duplicate routes were collapsed
- whether merged provenance strengthened or weakened confidence
- whether readout order differs from score order, and why
- whether coherence checks found missing primaries, archive-only evidence, or
  relation conflicts

## Verification

- one note cannot appear in multiple canonical slots
- archive chunks remain separate from wiki-memory dedupe
- dedupe does not erase relation provenance needed for Stage 6
- dedupe does not collapse away the only relation that explains functional
  success/failure ordering
