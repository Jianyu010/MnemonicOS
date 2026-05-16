# Chinese Support Phase 1: Text Processing Helpers and Config

## Goal

Centralize Chinese-aware text preparation so ingestion, query parsing, and FTS
indexing share one behavior.

## Implementation

- Add `TextProcessingConfig` with CJK support enabled by default.
- Add helpers for CJK detection, CJK n-gram generation, query terms, FTS text,
  chunk unit counting, and unit-based chunking.
- Use Latin stopword/min-length filtering only for Latin tokens.

## Acceptance

- `中文 支持 检索` produces searchable Chinese terms.
- `系统支持中文检索和嵌入` produces unigrams, bigrams, and trigrams.
- Existing English query behavior remains stable.
