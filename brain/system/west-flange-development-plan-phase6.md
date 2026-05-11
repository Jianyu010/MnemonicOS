# West-Flange Phase 6: Capture-to-Promotion Push Pipeline

## Architectural Principle

Every intake path must immediately trigger the full extraction + promotion
pipeline. No raw material should silently sit in the true-raw archive waiting
for a scheduled daemon tick to notice it.

This is a **push model**, not a poll model:

```
intake (capture end / ingest-artifact / ingest-session / reprocess-artifact)
    → material lands in true-raw archive
    → extraction enqueued AND processing triggered immediately
    → LLM/VLM extraction → candidates
    → promotion → wiki notes
    → returns
```

The daemon's scheduled `process_extraction_queue` and `process_promotion_queue`
jobs are **maintenance / cleanup**, not the primary driver:

- They retry failed items and clear stale `processing` states.
- They do not scan true-raw for unprocessed material — the daemon has no
  mechanism to discover which true-raw items are missing queue entries. If an
  item was not enqueued at intake time, the daemon cannot recover it.
- Therefore the push enqueue at intake is the only injection point into the
  pipeline. Missing it means the item is permanently invisible to the daemon.

## Daemon Job Role Clarification

| Job | Role under push model |
|---|---|
| `process_extraction_queue` | Cleanup: retry `error` items, unstick stale `processing` rows |
| `process_promotion_queue` | Cleanup: retry `error` items, unstick stale `processing` rows |
| `expire_stale_episodes` | Gap closure: auto-close orphaned `active` episodes, triggering full pipeline for each |
| `embed_backfill` | Maintenance: fill missing vectors for existing archive chunks |
| `daily_consolidation` | Wiki maintenance: dedup, stale review, note hygiene |
| `weekly_hygiene` | Wiki maintenance: rerank model, deeper hygiene |
| `status_report` | Health report only |

None of these scan true-raw. None of them are the primary path for new material.

## Intake Paths and Status

| Intake path | Function | Auto-enqueue | Immediate trigger |
|---|---|---|---|
| `ingest-artifact` | `ingest_artifact()` in `ingest.py` | **done** | **done** |
| `reprocess-artifact` | `reprocess_artifact()` in `ingest.py` | **done** | **done** |
| `ingest-session` | `ingest_session()` in `ingest.py` | **done** | **done** |
| capture begin/append/**end** | `end_episode()` in `session_capture.py` | **done** | **done** |

"Auto-enqueue" = `enqueue_extraction()` called inline.
"Immediate trigger" = extraction + promotion runs immediately, not deferred to
daemon tick.

## Implementation

### `ingest.py` — `ingest_artifact()` and `ingest_session()`

**`ingest_artifact()` — unique/near-duplicate path** (just before final return):

```python
from .extraction.queue import enqueue_extraction as _enqueue_extraction
_enqueue_extraction(
    config, source_kind="artifact", archive_id=archive_id,
    content_hash=raw_hash,  # raw_hash already computed as SHA-256
)
if config.ingest.auto_trigger_pipeline:
    from .pipeline import trigger_pipeline as _trigger_pipeline
    _trigger_pipeline(config)
```

Exact-duplicate branch intentionally excluded — canonical owns the entry.

**`reprocess_artifact()` — repaired or regenerated artifact path** (after
manifest/identity updates, before final return):

```python
from .extraction.queue import enqueue_extraction as _enqueue_extraction
_enqueue_extraction(
    config, source_kind="artifact", archive_id=archive_id,
    content_hash=raw_hash,
)
if config.ingest.auto_trigger_pipeline:
    from .pipeline import trigger_pipeline as _trigger_pipeline
    _trigger_pipeline(config)
```

Canonical duplicate shortcut remains excluded because the canonical archive owns
the extraction queue row.

**`ingest_session()`** (just before final return):

```python
_session_hash = sha256((content.rstrip() + "\n").encode()).hexdigest()
from .extraction.queue import enqueue_extraction as _enqueue_extraction
_enqueue_extraction(
    config, source_kind="session", archive_id=archive_id,
    bridge_id=resolved_bridge_id, session_id=resolved_session_id,
    content_hash=_session_hash,
)
if config.ingest.auto_trigger_pipeline:
    from .pipeline import trigger_pipeline as _trigger_pipeline
    _trigger_pipeline(config)
```

### `session_capture.py` — `end_episode()`

Just before `return EndEpisodeResult(...)`:

```python
from .extraction.queue import enqueue_extraction as _enqueue_extraction
_enqueue_extraction(
    config, source_kind="episode",
    conversation_id=conversation_id, episode_id=episode_id,
    bridge_id=request.bridge_id, session_id=request.episode_id,
    content_hash=content_hash,  # already computed via _sha256_path(events_path)
)
if config.ingest.auto_trigger_pipeline:
    from .pipeline import trigger_pipeline as _trigger_pipeline
    _trigger_pipeline(config)
```

### `pipeline.py` (new module)

`trigger_pipeline(config)`: runs `process_extraction_queue` and then
`process_promotion_queue` through the daemon socket. Falls back to running both
jobs inline if the daemon is unreachable or cannot complete the request.

```python
def trigger_pipeline(config: "AppConfig") -> None:
    if config.daemon is not None:
        extracted = _run_daemon_job(config.daemon.socket_path, "process_extraction_queue")
        promoted = extracted and _run_daemon_job(config.daemon.socket_path, "process_promotion_queue")
        if promoted:
            return
    from .extraction.worker import process_extraction_queue
    from .promotion.worker import process_promotion_queue
    process_extraction_queue(config)
    process_promotion_queue(config)
```

### `config.py` — `auto_trigger_pipeline` flag

Added `auto_trigger_pipeline: bool = True` to `IngestConfig`. Test configs set
`auto_trigger_pipeline = false` to prevent auto-processing from interfering with
explicit test control.

### Gap Closures

**Gap 1 + 3 — Stale episode expiry** (`jobs.py` + `daemon_jobs.py`):

`_expire_stale_episodes_job()` queries `raw_episodes WHERE status='active'` older
than `stale_episode_threshold_hours` (default: 2h from `DaemonConfig`), calls
`end_episode(status='partial')` for each orphan. This triggers the full pipeline
for each recovered episode. Registered as `expire_stale_episodes` on `"0 * * * *"`
(hourly) in `_DEFAULT_SCHEDULES`.

**Gap 2 — Content-hash re-extraction** (`extraction/queue.py` + migration `017`):

`enqueue_extraction()` now accepts `content_hash: str | None`. ON CONFLICT logic
resets status to `'pending'` when the incoming hash differs from the stored hash,
enabling re-processing of updated episodes. Added `content_hash TEXT` column via
`migrations/017_extraction_queue_content_hash.sql`.

## Idempotency

`enqueue_extraction()` uses `ON CONFLICT DO UPDATE` with a content-hash-aware
status guard. Same content → preserves `processed` / `processing` row unchanged.
Different content hash → resets to `pending` for re-extraction. Calling
`end_episode()` twice for the same episode with unchanged content does not reset
an already-processed item.

## Invariants Respected

- West flange remains pull-based. `enqueue_extraction()` writes a queue row
  only; VLM/LLM calls happen in the extraction worker, not inside `end_episode`.
- New modules: `pipeline.py` (daemon-run extraction and promotion + inline fallback).
- New config: `IngestConfig.auto_trigger_pipeline` guards trigger calls (default `True`).
- New config: `DaemonConfig.stale_episode_threshold_hours` controls expiry window.
- Daemon jobs retain their existing implementations; `expire_stale_episodes` added as new maintenance job.

## Verification

1. **End-to-end capture push**: run `capture-begin/append/end`. Immediately
   after `capture-end` returns, queue entry shows `processed` and extraction
   candidates exist — no manual job invocation required.

2. **Ingest push**: `ingest-artifact` and `ingest-session` both produce a
   `processed` queue entry immediately on return.

2b. **Reprocess push**: repairing raw artifact bytes and running
    `reprocess-artifact` re-enqueues the artifact with the new `content_hash`.
    If the old queue row was `processed`, it resets to `pending` or is processed
    immediately when `auto_trigger_pipeline` is enabled.

3. **Content-hash re-extraction**: ingest session, confirm `content_hash` set.
   Call `enqueue_extraction` with a different hash → status resets to `pending`.
   Same hash → stays `processed`.

4. **Stale episode expiry**: insert a `raw_episodes` row with `status='active'`
   and `updated_at` 3h ago. Run `expire_stale_episodes` job. Confirm episode
   ends as `partial` and an extraction queue entry appears.

5. **Daemon cleanup**: scheduled tick does not re-process `processed` items;
   only retries `error` rows.

6. `pytest tests/ -x -q` — all pass.
