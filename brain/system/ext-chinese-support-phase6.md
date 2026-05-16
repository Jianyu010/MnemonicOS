# Chinese Support Phase 6: End-to-End Tests and Rollout

## Goal

Prove Chinese ingestion and retrieval work across wiki notes, archive chunks, and
mixed-language queries.

## Implementation

- Add unit tests for CJK n-grams and query terms.
- Add integration tests for Chinese wiki note BM25 retrieval.
- Add archive ingestion tests for unspaced Chinese content.
- Add regression tests ensuring generated search text is not emitted.

## Acceptance

- Targeted query parser, recall, phase2, and full unittest suites pass.
- Roadmap evidence is updated after test completion.
