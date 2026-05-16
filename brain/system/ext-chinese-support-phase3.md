# Chinese Support Phase 3: Query Parsing and BM25 Retrieval

## Goal

Make Chinese and mixed Chinese/English queries enter retrieval as meaningful
terms instead of wildcard or whole-phrase-only tokens.

## Implementation

- Query parsing uses shared text-processing helpers.
- CJK terms bypass Latin minimum-length and English stopword filtering.
- Existing BM25 query construction continues to consume `ParsedQuery.tokens` and
  `expanded_terms`.

## Acceptance

- Queries like `中文`, `支持中文`, `检索`, and `系统支持中文吗` retrieve Chinese
  content through BM25.
- English synonym expansion remains unchanged.
