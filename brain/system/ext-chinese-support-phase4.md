# Chinese Support Phase 4: CJK-Safe Archive Chunking

## Goal

Prevent unspaced Chinese sessions and documents from becoming single huge archive
chunks.

## Implementation

- Chunk by mixed retrieval units: Latin non-whitespace runs and individual CJK
  characters.
- Slice chunks by original character spans so stored archive body text remains
  faithful.
- Store approximate unit counts in `token_count`.

## Acceptance

- Long Chinese text creates bounded archive chunks.
- Chunk bodies preserve original Chinese text, not generated token spacing.
