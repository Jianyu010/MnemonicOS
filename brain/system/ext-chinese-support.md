# External Chinese Support Governance Plan

## Contract

MnemonicOS supports Chinese by preserving canonical text exactly as written while
building separate search-only representations for retrieval. Generated CJK terms
must never leak into wiki bodies, archive bodies, summaries, or emitted memory
packets.

## Retrieval Guarantees

1. Chinese-only and mixed Chinese/English queries produce meaningful lexical
   terms instead of wildcard queries.
2. BM25 indexes search-only CJK n-grams for notes and archive chunks.
3. Archive chunking uses mixed Latin/CJK units so unspaced Chinese does not
   become one giant chunk.
4. Semantic Chinese retrieval requires a multilingual external embedding
   provider. The hash provider remains a deterministic degraded fallback.
5. Stored vector identity includes provider, model, and dimensions so incompatible
   embedding spaces are not mixed.

## Non-Goals

- No mandatory Chinese segmentation dependency in v1.
- No hardcoded cloud provider or embedding model.
- No mutation of canonical user-authored text to add generated search tokens.

## Phase Index

- [Phase 1: Text Processing Helpers and Config](ext-chinese-support-phase1.md)
- [Phase 2: Search-Only Lexical Indexing](ext-chinese-support-phase2.md)
- [Phase 3: Query Parsing and BM25 Retrieval](ext-chinese-support-phase3.md)
- [Phase 4: CJK-Safe Archive Chunking](ext-chinese-support-phase4.md)
- [Phase 5: Embedding Provider Hygiene](ext-chinese-support-phase5.md)
- [Phase 6: End-to-End Tests and Rollout](ext-chinese-support-phase6.md)
- [Roadmap](ext-chinese-support-roadmap.md)
