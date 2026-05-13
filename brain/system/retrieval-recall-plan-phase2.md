# Retrieval Recall Phase 2: BM25 Channel

## Goal

Implement lexical recall over canonical wiki notes using `ParsedQuery.tokens`
plus `ParsedQuery.expanded_terms`, producing a bounded set of direct wiki
candidates with BM25 provenance.

## Scope

- `run_bm25_channel()` function
- single MATCH query construction from original + expanded terms
- deterministic rank assignment
- no archive/raw lookup in this phase

## Components

```
src/second_brain/recall.py         — BM25 channel implementation
src/second_brain/query_parser.py   — source of tokens and expanded_terms
```

## BM25 Query Construction

The BM25 channel must:

1. start from `ParsedQuery.tokens`
2. append `expanded_terms` not already present
3. preserve deterministic order
4. treat `["*"]` as "no term constraint" and return `[]`
5. execute a single FTS query

Cap: `top_k * 4` candidates.

## Failure Policy

No-match returns `[]`. Query-construction bugs are exceptions and should fail
tests rather than degrade silently.

## Verification

- expanded terms improve recall without duplicating tokens
- wildcard sentinel yields no BM25 candidates
- notes excluded by retired/validity filters do not appear
- candidate count respects `top_k * 4`
