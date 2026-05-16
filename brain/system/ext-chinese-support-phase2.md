# Chinese Support Phase 2: Search-Only Lexical Indexing

## Goal

Make Chinese BM25 retrieval work without contaminating canonical text fields.

## Implementation

- Add search-only fields to `notes_search` and `archive_chunks`.
- Rebuild `notes_fts` and `chunks_fts` to index search-only fields.
- Keep retrieval joins pointed at canonical title, summary, and body columns.
- Populate search-only fields during vault sync and archive ingestion.

## Acceptance

- Chinese note and archive bodies are indexed with generated CJK terms.
- Returned memory packet text never includes generated CJK n-grams.
