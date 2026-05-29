# Ingestion Debug Record

Last updated: 2026-05-29

---

## B1 — Socket thread deadlocks on DB write contention

**Symptom**
Daemon process alive and processing extractions, but IPC socket returns `ECONNREFUSED` or times out. `python3 -m second_brain.cli status` hangs or fails silently.

**Cause**
`SocketServer._handle_request("status")` calls `collect_status_snapshot()` synchronously in the socket thread (single-threaded, no per-client goroutine). That function calls `connect_db()` which issues `PRAGMA journal_mode = WAL` (a DDL write), followed by `run_migrations()` which acquires SQLite's WAL write lock to check schema. If any extraction subprocess holds an open write transaction on `archive.db` (e.g., mid-inference), both PRAGMA and migration check block for up to `timeout=30s`. Socket thread cannot `accept()` new connections during this window. Client timeout (typically 5s) fires → daemon appears unreachable.

Cascade: `collect_status_snapshot` → `promotion_status` → `extraction_status` each independently call `run_migrations`. Three write-lock attempts, 30s each, all from the socket thread.

**Root callsite**
`daemon.py:SocketServer._handle_request` → `jobs.py:collect_status_snapshot`

**Patch**
- Run migrations **once at daemon startup** (before threads start) via `Daemon._startup_migrate()`.
- Remove `run_migrations` from `collect_status_snapshot`, `promotion_status`, `extraction_status`.
- Status path becomes read-only; no write-lock contention possible.

---

## B2 — FD leak: DB connections not closed on exception

**Symptom**
Daemon accumulates open file handles to `archive.db` over days (observed: 7+ FDs in a 10h run). `lsof -p <pid> | grep archive.db` shows growing count. WAL checkpoint `PRAGMA wal_checkpoint` returns `(0, -1, -1)` (blocked).

**Cause**
Pattern throughout `jobs.py` and `extraction/queue.py`:
```python
connection = connect_db(config.paths.db_path)
run_migrations(connection, ...)   # may throw database is locked
last_runs = connection.execute(...)  # may throw
connection.close()                # never reached if above throws
```
When `database is locked` (30s timeout exhausted), exception propagates before `close()`. Connection leaks into the process's FD table.

**Patch**
Wrap all `connect_db` usage in `try/finally: connection.close()`. Already done correctly in `promotion_status` — extend pattern to `collect_status_snapshot` and `extraction_status`.

---

## B3 — Double-spawn stall bypass via shared progress file

**Symptom**
Two `second_brain.extraction._subprocess_entry` processes with identical `--item-id` and `--progress-file` args. Both stuck in `recv()` for hours. Neither killed by stall detector despite `stall_timeout_seconds=900`.

**Cause**
`_spawn_item_subprocess` uses a fixed progress file path: `mnemo_progress_{item_id}.json`. When two spawns occur for the same item (root cause of double-spawn itself is a separate race condition TBD):

1. Second spawn calls `progress_file.unlink()` — deletes first spawn's file.
2. Both subprocesses write to the same path.
3. Monitoring threads for **both** `_LaneHandle` instances read the same path.
4. Any progress write by either subprocess resets the stall timer for **both** handles → stall never triggers on either.
5. Absolute timeout `max_extraction_item_seconds=7200` (2h) is the only remaining kill gate.

**Patch**
Use a unique progress file per spawn:
```python
progress_file = Path(tempfile.gettempdir()) / f"mnemo_progress_{item_id}_{uuid.uuid4().hex[:8]}.json"
```
Each handle now monitors its own file. Stall detection is independent. Double-spawn root cause still TBD (likely TOCTOU between `pending_quick_items` read and `mark_processing` write in dual-lane, or lock release while daemon lane threads still alive).

---

## B5 — Double-spawn: `retry_failed_extractions` races against active subprocess

**Symptom**
Two `second_brain.extraction._subprocess_entry` processes with identical `--item-id` running simultaneously. Item `attempt_count` higher than expected. `started_at` in DB is stale (not updated for the second spawn).

**Cause**
`_STALE_PROCESSING_TIMEOUT_MINUTES = 60` (1h) was shorter than `max_extraction_item_seconds = 7200` (2h). `retry_failed_extractions` runs at `:55` every hour (`55 * * * *`). An item legitimately mid-extraction for 61+ minutes is classified as "stale" and reset to `status='pending'`. The next `process_extraction_queue` job picks it up → `mark_processing` called → **second subprocess spawned while first is still running**.

The second `mark_processing` updates `started_at` and increments `attempt_count`. DB state from that moment forward reflects the second spawn only; the first subprocess runs orphaned (no DB entry updates). Progress file conflict (B3) then prevents stall detection on both.

**Location**
`jobs.py:_retry_failed_extractions_job` → `_STALE_PROCESSING_TIMEOUT_MINUTES = 60`

**Patch**
- Raise `_STALE_PROCESSING_TIMEOUT_MINUTES = 150` (floor constant).
- In `_retry_failed_extractions_job`, compute effective threshold from config:
  ```python
  effective_stale_minutes = max(
      _STALE_PROCESSING_TIMEOUT_MINUTES,
      config.extraction.max_extraction_item_seconds // 60 + 30,
  )
  ```
  Ensures threshold auto-adapts if `max_extraction_item_seconds` is changed in config.

---

## B4 — Stuck `processing` items after unclean daemon exit

**Symptom**
After daemon SIGKILL or OOM kill, items stay in `status='processing'` indefinitely. `pending_quick_items` / `pending_slow_items` query `WHERE status='pending'` — stuck items are invisible to scheduler. Queue appears drained but extraction stalls.

**Cause**
`_mark_processing_items_as_error()` only runs at **graceful shutdown**. Hard kills skip it. No startup cleanup exists.

**Patch**
Add `Daemon._reset_stale_processing_items()` called in `Daemon.run()` before threads start:
```sql
UPDATE extraction_queue SET status='pending', finished_at=NULL, last_error=NULL
WHERE status='processing'
```
Preserves `attempt_count` so tombstone logic still works on repeated failures.

---

## B6 — Double-spawn ROOT CAUSE: unreaped subprocess + non-atomic claim

**Status**: Resolves the "root cause TBD" flagged in B3 and B5.

**Symptom**
Two `second_brain.extraction._subprocess_entry --item-id N` procs (both PPID=daemon,
one old + one fresh) for the same item, row `status='processing' attempt_count=2`,
single daemon. Wasted Ollama calls → VRAM contention.

**Cause (two compounding defects)**
1. `_LaneHandle.join()`'s `finally` cleaned up registry/progress/poll-thread but
   **never killed the subprocess**. Any exception in the watch loop → finally runs,
   proc left alive, lane thread dies, `_process_dual_lane`'s `t.join()` returns,
   `process_extraction_queue` returns, **job lock released with a live orphan**.
2. `_terminate_process_group` called `proc.communicate()` while `join()`'s
   `drain_thread` concurrently read the same `proc.stderr` pipe. Two threads on one
   pipe → `communicate()` can raise a non-`TimeoutExpired` error → **SIGKILL
   escalation skipped** → kill silently fails → feeds defect 1.
3. `mark_processing` was an unconditional `UPDATE ... WHERE id=?` (no `status`
   guard, no rowcount check). Once the orphan row was requeued→pending (by
   `requeue_orphaned_processing` after `max_extraction_item_seconds`, or
   `retry_failed_extractions` stale-reset), the next tick re-marked it
   (attempt_count→2) and **spawned subprocess #2 while #1 still ran**.

Within one daemon the per-job `threading.Lock` prevents *concurrent*
`process_extraction_queue` runs — so the double-spawn required run #1 to return
while its subprocess stayed alive (defects 1+2), then a later run #3 to re-claim
the requeued row (defect 3). Not a concurrency-of-runs bug; a reaping bug.

**Patch** (`extraction/queue.py`, `extraction/worker.py`)
- `mark_processing` → atomic claim: `UPDATE ... WHERE id=? AND status='pending'`,
  returns `cursor.rowcount == 1`. Both callsites skip the item (no spawn) on False.
- `_terminate_process_group` reaps with `proc.wait()`, never `communicate()`
  (wait() doesn't touch the pipes, so it can't race the drain thread); logs if a
  proc survives SIGKILL.
- `_LaneHandle.join()` `finally` is now a **guaranteed-reap safety net**: if
  `proc.poll() is None` it calls `_terminate_process_group`, then joins the drain
  thread and closes the pipes (FD hygiene, see B2). drain loop swallows
  ValueError/OSError from pipes closed underneath it.

**Regression tests** (`tests/test_extraction_layer.py`)
- `test_mark_processing_is_atomic_claim` — 2nd claim returns False, no attempt bump.
- `test_lane_handle_join_reaps_when_watch_loop_raises` — exception in watch loop
  still reaps the subprocess.
- `test_lane_handle_timeout_reaps_despite_stderr_flood` — child ignores SIGTERM
  and floods stderr (forces SIGKILL path under concurrent drain); still reaped.
