# Ingestion Debug Record

Last updated: 2026-05-23

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
