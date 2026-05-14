# Retrieval Assembly Phase 1: Packet Contract and Archive Slot

## Goal

Define the Stage 5 packet contract before changing runtime behavior. The packet
must be readable by callers and mechanically sufficient for Stage 6 assessment.

## Scope

- packet type name: `RawPacket` vs evolved `MemoryPacket`
- canonical memory slots
- explicit `archive_context`
- structured `assembly_notes`
- serialization shape for Stage 7

## Components

```text
src/second_brain/models.py      — eventual packet dataclasses
src/second_brain/runtime.py     — eventual assembly producer
api/openapi.yaml                — later protocol surface if exposed externally
```

## Contract Rules

1. Archive chunks have a separate slot and cannot be represented as canonical
   wiki memories.
2. Every packet item carries ID, type, title, summary, body reference or body
   excerpt, provenance, freshness/lifecycle state, relation types, and final
   score.
3. `assembly_notes` is structured and machine-readable. It records packet-level
   facts Stage 6 should not recompute.
4. The packet can be assessed without DB access.

## Open Design Questions

- Should Stage 5 introduce a new `RawPacket` dataclass or extend
  `MemoryPacket`?
- Should archive context carry archive-specific fields (`archive_id`,
  `chunk_index`, `archive_path`) or normalize through the runtime memory item?
- Which body reference fields are needed for Stage 7 hydration: path only,
  content hash, byte range, or excerpt hash?

## Verification

- packet serialization includes every planned slot
- archive item fixtures cannot validate as primary memories
- Stage 6 fixture can classify packet state without a database
