# Promotion Layer Phase 5: Gap Closure

## Scope

Two open items from Phase 5:

1. **Stuck-row recovery** — candidates that were rejected (transiently: gate
   failure, write error, LLM transient) are permanently invisible to the
   promotion queue. Config field `retry_after_hours` exists but the scan-and-
   requeue logic was never implemented.

2. **Post-promotion sync coverage** — `sync_vault` (which writes `notes` table
   and `note_vectors` inline) is only called from the daemon-job wrapper in
   `jobs.py`. When `process_promotion_queue` is invoked inline via
   `trigger_pipeline`, no sync runs. Promoted notes are on disk but not in the
   search index until the next daemon tick.

---

## Gap 1: Stuck-Row Recovery

### Root Cause

`write_rejected` and gate failures both set:
- `extraction_candidates.promotion_state = 'rejected'`
- `promotion_decisions` row with `promotion_state = 'rejected'`

The main worker fetch:
```sql
WHERE ec.promotion_state = 'promotion_ready'
  AND NOT EXISTS (SELECT 1 FROM promotion_decisions pd WHERE pd.candidate_id = ec.id)
```
A rejected candidate fails BOTH conditions. It can never be picked up again without
intervention.

### Fix

At the start of `process_promotion_queue` in `worker.py`, before the main fetch,
run a requeue sweep:

```python
retry_hours = getattr(promo_cfg, "retry_after_hours", 24) if promo_cfg is not None else 24
with conn:
    # Step 1: identify stale rejected candidates and reset promotion_state
    conn.execute(
        """
        UPDATE extraction_candidates
        SET promotion_state = 'promotion_ready'
        WHERE id IN (
            SELECT candidate_id FROM promotion_decisions
            WHERE promotion_state = 'rejected'
              AND created_at < datetime('now', '-' || ? || ' hours')
        )
        """,
        (retry_hours,),
    )
    # Step 2: delete the stale decision rows so the NOT EXISTS check passes
    cur = conn.execute(
        """
        DELETE FROM promotion_decisions
        WHERE promotion_state = 'rejected'
          AND created_at < datetime('now', '-' || ? || ' hours')
        """,
        (retry_hours,),
    )
    result.requeued = cur.rowcount
```

Order matters: UPDATE first, DELETE second (DELETE is the limiting step; UPDATE
references rows that DELETE will remove).

This does NOT apply to `review_routed` or `promoted` rows — only `rejected`.

### `PromotionRunResult` model change

Add `requeued: int = 0` field and include in `to_stats()`:

```python
@dataclass
class PromotionRunResult:
    promoted: int = 0
    review_routed: int = 0
    rejected: int = 0
    requeued: int = 0        # ← new
    skipped: int = 0
    errors: list[str] = field(default_factory=list)

    def to_stats(self) -> dict[str, int]:
        return {
            "promoted": self.promoted,
            "review_routed": self.review_routed,
            "rejected": self.rejected,
            "requeued": self.requeued,   # ← new
            "skipped": self.skipped,
            "errors": len(self.errors),
        }
```

### Dry-run behaviour

In dry-run mode, run the COUNT of candidates that WOULD be requeued but do not
execute the DELETE/UPDATE. Report in `result.requeued`.

```python
if dry_run:
    count_row = conn.execute(
        """
        SELECT COUNT(*) FROM promotion_decisions
        WHERE promotion_state = 'rejected'
          AND created_at < datetime('now', '-' || ? || ' hours')
        """,
        (retry_hours,),
    ).fetchone()
    result.requeued = int(count_row[0])
else:
    # ... UPDATE + DELETE as above
```

---

## Gap 2: Post-Promotion Sync Coverage

### Root Cause

`sync_vault` is called in `_process_promotion_queue_job` (the `jobs.py` wrapper):

```python
if not dry_run and result.promoted > 0:
    sync_vault(config, mode="incremental")
```

When `process_promotion_queue` is called directly (via `trigger_pipeline` inline
fallback or direct API call), this wrapper never runs → promoted notes live on
disk but are invisible to retrieval until the next scheduled daemon tick.

Additionally, the `review_routed > 0` case is not covered at all: review files
land in `wiki/review/` but never trigger sync.

### Fix A: Move sync_vault into the worker

In `process_promotion_queue` in `worker.py`, at the end before `return result`:

```python
if not dry_run and (result.promoted > 0 or result.review_routed > 0):
    from ..sync import sync_vault
    sync_vault(config, mode="incremental")
```

Remove the redundant `sync_vault` call from `_process_promotion_queue_job` in
`jobs.py` (avoid double sync when run as daemon job).

### Fix B: Trigger embed_backfill for archive chunk vectors

After sync_vault, archive chunk vectors for source material may be missing if
`embed_backfill` hasn't run since extraction (scheduled at `15 * * * *`, up to
15 min wait). Run it promptly via the daemon when reachable, falling back inline:

Add to `pipeline.py`:

```python
def trigger_embed_backfill(config: "AppConfig") -> None:
    """Run embed_backfill promptly, using the daemon when available."""
    if config.daemon is not None and _run_daemon_job(config.daemon.socket_path, "embed_backfill"):
        return
    from .jobs import run_job
    run_job(config, job_name="embed_backfill", dry_run=False)
```

Call it in `process_promotion_queue` after `sync_vault`:

```python
if not dry_run and result.promoted > 0:
    from ..sync import sync_vault
    sync_vault(config, mode="incremental")
    from ..pipeline import trigger_embed_backfill
    trigger_embed_backfill(config)
```

Note: `sync_vault` already calls `upsert_note_vector` inline — note vectors are
already written. `trigger_embed_backfill` only closes the gap for archive chunk
vectors (material referenced in promoted notes).

---

## Files Changed

| File | Change |
|---|---|
| `src/second_brain/promotion/models.py` | Add `requeued: int = 0` to `PromotionRunResult` |
| `src/second_brain/promotion/worker.py` | Add requeue sweep before main fetch; move `sync_vault` call in; call `trigger_embed_backfill` |
| `src/second_brain/jobs.py` | Remove redundant `sync_vault` call from `_process_promotion_queue_job` |
| `src/second_brain/pipeline.py` | Add `trigger_embed_backfill(config)` |

---

## Verification

1. **Stuck-row recovery**: seed a `rejected` candidate with `created_at` older
   than `retry_after_hours`. Run `process_promotion_queue`. Confirm
   `extraction_candidates.promotion_state` reset to `promotion_ready` and the
   candidate is picked up in the same batch or next run.

2. **Dry-run requeue count**: same seed, run with `--dry-run`. Confirm
   `result.requeued = 1`, no DB writes.

3. **Sync on inline call**: call `process_promotion_queue(config)` directly
   (bypassing daemon job wrapper). Confirm promoted note appears in `notes` table
   and `note_vectors` immediately after return.

4. **review_routed sync**: route a candidate to review. Confirm `wiki/review/`
   file and `notes` table entry both present after `process_promotion_queue`.

5. **No double sync from daemon**: run via `run-job process_promotion_queue`.
   Confirm `sync_vault` called exactly once (check log or mock).

6. `pytest tests/test_promotion_layer.py -x -q` — all pass.
7. `pytest tests/ -x -q` — full suite green.
