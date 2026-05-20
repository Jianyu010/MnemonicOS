# LLM/VLM Layer Phase 11: Expected-Time Queue Scheduling

## Goal

Keep daemon extraction throughput responsive when large documents are queued by
letting lightweight session and text work run before likely-slow document work.

## Context

The daemon can run jobs concurrently, and extraction items run in subprocesses,
but each `process_extraction_queue` job currently selects pending rows in strict
FIFO order. A backlog of huge PDFs can therefore fill every extraction batch and
delay newer agent session episodes for a long time. That defeats MnemonicOS's
working-memory role even though the raw capture path itself is healthy.

## Scheduling Policy

Pending rows are selected by a coarse expected processing lane, then FIFO within
the lane:

1. `episode` rows and `session` rows.
2. `artifact` rows whose archive format is lightweight text:
   `md`, `markdown`, `txt`, `text`, `plain_text`, or `log`.
3. Other artifact rows except known heavy office/document formats.
4. Heavy artifact rows: `pdf`, `docx`, and `pptx`.

This is intentionally a heuristic, not a learned scheduler. It uses existing
`archive.source_format` metadata and does not open a projection, count windows,
or inspect file contents before choosing work.

## Invariants

- The selector only changes pending-row order; it does not change queue identity,
  status transitions, retries, or subprocess extraction behavior.
- FIFO order is preserved among rows with the same expected-time lane.
- If no lightweight pending work exists, heavy artifacts are selected normally.
- Missing archive metadata must not hide work. Unknown artifact formats remain
  processable and sort before known-heavy `pdf`, `docx`, and `pptx` files.
- The policy is source-agnostic from the worker's perspective: selected rows are
  still ordinary `ExtractionQueueItem` objects.

## Implementation Plan

1. Add focused tests around `pending_items()` ordering:
   - sessions/episodes sort ahead of known-heavy artifacts even when newer;
   - markdown/text artifacts sort near sessions and before PDFs/DOCX/PPTX;
   - FIFO is preserved inside the heavy lane;
   - when only heavy rows exist, they are still returned.
2. Update `src/second_brain/extraction/queue.py` so `pending_items()` left-joins
   `archive` for artifact metadata and orders by an expected-time CASE
   expression before `queued_at ASC, id ASC`.
3. Keep the public function signature unchanged.

## Verification

- Focused test command:
  `python -m unittest tests.test_extraction_layer.ExtractionLayerTest.test_pending_items_prefers_lightweight_sources_over_heavy_artifacts`
- Regression command:
  `python -m unittest tests.test_extraction_layer`

## Out Of Scope

- Persisting a new priority column.
- Precomputing token/window counts before projection.
- Learned scheduling or adaptive runtime estimates.
- Changing daemon job concurrency.
