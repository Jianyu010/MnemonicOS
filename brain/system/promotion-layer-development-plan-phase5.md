# Promotion Layer Phase 5: Daemon and Admin Surfaces

## Goal

Wire the promotion pipeline into the daemon scheduler, CLI, and HTTP admin
surface so the layer operates automatically and is inspectable by operators.

## Context

The daemon already runs `process_extraction_queue` every five minutes. The
promotion layer adds `process_promotion_queue` at the same cadence. The two
jobs are independent: extraction produces `promotion_ready` rows; promotion
consumes them. Neither job calls the other.

After a successful wiki write, the promotion job triggers the existing
`embed_backfill` job (or marks notes dirty for the next scheduled backfill run)
so new and updated notes appear in semantic search. FTS sync is triggered via
the existing `sync_vault` path.

## Scope

Modified: `src/second_brain/daemon_jobs.py`
Modified: `src/second_brain/jobs.py`
Modified: `config/memory.toml` + `config/memory.example.toml`
Extended: existing CLI surfaces (mnemonicos CLI)
Extended: existing HTTP server (`src/second_brain/server.py`)

New dataclass `PromotionRunResult` in `src/second_brain/promotion/models.py`:
- `promoted: int`
- `review_routed: int`
- `rejected: int`
- `skipped: int`
- `errors: list[str]`

## Daemon Job

Job name: `process_promotion_queue`

Default schedule: `*/5 * * * *` (same cadence as `process_extraction_queue`)

Batch size: `config.promotion.max_candidates_per_run` (default 20)

Job flow per run:
1. Query `extraction_candidates` for `promotion_state = 'promotion_ready'`
   rows not yet in `promotion_decisions`, ordered by `id ASC`, limit batch size.
2. For each candidate:
   a. Deserialize `payload_json`.
   b. Run promotion gate (Phase 1). On gate failure: write rejected ledger row,
      continue to next candidate.
   c. Run decision engine (Phase 2). On error: log, continue.
   d. Run note renderer (Phase 3). On error: log, continue.
   e. Run wiki writer (Phase 4). On rollback: mark rejected, continue.
   f. Increment appropriate counter in `PromotionRunResult`.
3. After batch: trigger `sync_vault` for any note directory that received writes.
4. Mark `embed_backfill` as needed (set a dirty flag or enqueue directly).
5. Return `PromotionRunResult`.

Stuck-row recovery: if a candidate has `promotion_state = 'promotion_ready'`
and a `promotion_decisions` row with `promotion_state = 'rejected'` older than
`config.promotion.retry_after_hours` (default 24), clear the rejected row and
re-queue. This handles transient failures without manual intervention.

## Config Section

```toml
[promotion]
enabled = true
max_components_per_candidate = 50
max_candidates_per_run = 20
min_confidence_for_direct_write = "medium"
auto_review_on_conflict = true
retry_after_hours = 24
```

## CLI Surfaces

Added to the existing `mnemonicos` CLI:

```
mnemonicos promote-status
    Show: pending count, last run time, promoted/review_routed/rejected totals.

mnemonicos run-job process_promotion_queue
    Trigger immediate promotion run. Already supported by the job runner
    framework; only the job registration is new.

mnemonicos run-job process_promotion_queue --dry-run
    Run gate + decision engine for each pending candidate. Print decisions.
    No wiki writes. No ledger writes.
```

## HTTP Admin Endpoint

Added to `src/second_brain/server.py`:

```
POST /v1/promotion/process
    Body: {"dry_run": false}
    Response: PromotionRunResult as JSON.

GET /v1/promotion/status
    Response: {"pending": N, "last_run_at": "...", "totals": {...}}
```

These mirror the extraction layer's `/v1/extraction/process` and
`/v1/extraction/status` endpoints.

## Daemon Status

The daemon status summary (returned by socket `status` command and by
`mnemonicos daemon-status`) includes a `promotion` section:

```
promotion: pending=N promoted=M review_routed=K rejected=J last_run=...
```

## Milestones

1. `process_promotion_queue` job is registered in `daemon_jobs.py`.
2. Daemon schedule includes `process_promotion_queue` at `*/5 * * * *`.
3. `config/memory.toml` includes `[promotion]` section.
4. `mnemonicos promote-status` reports pending count and last run.
5. `mnemonicos run-job process_promotion_queue` runs the full pipeline.
6. `--dry-run` flag prints decisions without writing wiki or ledger.
7. `POST /v1/promotion/process` runs the pipeline via HTTP.
8. `GET /v1/promotion/status` returns operational counts.
9. Daemon status output includes the `promotion` section.
10. `sync_vault` and embedding backfill are triggered after writes.
11. Stuck-row recovery re-queues candidates with stale `rejected` state.

## Verification

- Full end-to-end: seed one `promotion_ready` candidate, run
  `process_promotion_queue`, verify note in `brain/wiki/`, ledger row present,
  `log.md` updated, `embed_backfill` scheduled.
- Idempotency: second run of same candidate returns `skipped=1`.
- Dry-run: decisions printed, no note written, no ledger row.
- `confidence="low"` candidate routes to `brain/wiki/review/` without a
  direct wiki note.
- Gate failure: candidate marked `rejected`, no note written.
- `mnemonicos promote-status` shows correct pending count before and after run.
- Daemon status includes promotion section.
- Full test suite passes with no regressions: `python -m pytest tests/`.

## Out Of Scope

- A UI for human review of items in `brain/wiki/review/`.
- Promotion of notes that are not sourced from extraction candidates
  (manual or legacy promotion paths remain separate).
- HTTP transport for direct candidate upload; promotion reads from SQLite only.
