# Chinese Support Roadmap

| Phase | Status | Evidence |
| --- | --- | --- |
| Phase 1: Text processing helpers and config | done | Added shared helpers and `[text_processing]` defaults. |
| Phase 2: Search-only lexical indexing | done | Added migration `028_cjk_search_fields.sql` and search-only sync/ingest fields. |
| Phase 3: Query parsing and BM25 retrieval | done | Query parsing uses CJK n-grams; BM25 receives parsed expanded terms. |
| Phase 4: CJK-safe archive chunking | done | Archive chunking now counts mixed Latin/CJK retrieval units without altering body text. |
| Phase 5: Embedding provider hygiene | done | Embedding model keys include provider/model/dimensions; hash health reports degraded multilingual status. |
| Phase 6: End-to-end tests and rollout | done | Added unit/integration coverage; `python3 -m unittest discover -s tests` passed 451 tests. |

## Current Notes

- Canonical note/archive body fields remain clean.
- Search-only fields carry generated CJK n-grams.
- Robust semantic Chinese support depends on configuring a multilingual
  `ollama_embeddings` or `openai_compatible_embeddings` provider.

## Test Evidence

- `python3 -m unittest tests.test_text_processing` passed.
- `python3 -m unittest tests.test_query_parser` passed.
- `python3 -m unittest tests.test_recall` passed.
- `python3 -m unittest tests.test_phase2` passed.
- `python3 -m unittest tests.test_inference_providers` passed.
- `python3 -m unittest discover -s tests` passed 451 tests.
