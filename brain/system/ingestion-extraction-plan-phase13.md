# LLM/VLM Layer Phase 13: Decoupled Lanes + Progress-Aware Timeout

## Goal

Two targeted reliability improvements to dual-lane extraction:

1. **Decoupled lane execution** — quick and slow lane items are processed by
   independent threads. A slow-lane item timing out or stalling does not block
   the quick lane from advancing to its next item.

2. **Progress-aware stall timeout** — the hard per-item wall-clock limit is
   replaced by a stall detector. A subprocess is killed only when its progress
   file stops changing for `stall_timeout_seconds` (default 5 min). An
   absolute fallback (`max_extraction_item_seconds`, default 2 h) is kept as a
   safety net for runaway processes that somehow keep touching the file.

## Context

Phase 12 introduced dual-lane extraction: one quick subprocess (episodes, text)
runs alongside one slow subprocess (PDFs, heavy artifacts) per pair. Both are
joined before the next pair starts. This preserved throughput when the slow item
completed normally.

In production, a set of large academic PDF textbooks (100+ embedded images each,
~2 min per VLM call = ~3 h total) always exceeded the 30-minute hard timeout.
`retry_failed_extractions` requeued them hourly. Each job run paired one of
these PDFs with a quick item, waited 30 min for the PDF to timeout, then
advanced to the next pair. Effective quick-lane throughput: ~1 item per 30 min.
Overnight: ~10 items processed.

Two failure modes coexist:

- **Stalled VLM call**: Ollama hangs on a single artifact. The progress file
  stops updating. The subprocess uses 0 % CPU but never exits. Under the old
  hard timeout this item is killed after 30 min, leaving the quick lane blocked
  for 30 min before the next pair starts.

- **Legitimately slow item**: A 100-artifact PDF processes fine at 2 min/artifact
  = 200 min. Old 30-min timeout kills it every attempt. Progress-aware timeout
  lets it run as long as each artifact call completes within `stall_timeout_seconds`.

## Architecture

### Decoupled Lane Threads

`_process_dual_lane` no longer uses `zip_longest` pairs. Instead:

```python
quick_thread = Thread(target=_run_lane_items, args=(quick_candidates, "quick", ...))
slow_thread  = Thread(target=_run_lane_items, args=(slow_candidates,  "slow",  ...))
quick_thread.start(); slow_thread.start()
quick_thread.join();  slow_thread.join()
```

`_run_lane_items` opens its own SQLite connection (thread safety: SQLite
connection objects must not be shared across threads), iterates its candidate
list serially, calls `mark_processing` + `_spawn_item_subprocess` + `join()`
for each item, and accumulates stats into a per-lane `ExtractionProcessResult`.
After both threads join, the caller merges the two result objects.

Each lane's subprocess still uses the `progress_key` routing ("quick" / "slow")
so `daemon watch` continues to show separate per-lane progress.

The `_join_lane_pair` function is removed.

### Progress-Aware Stall Timeout

`_LaneHandle` gains a `stall_timeout_seconds: int` field. `join()` is rewritten
from a single `proc.communicate(timeout=...)` call into a 1-second poll loop:

```
loop every 1 s:
  if proc exited → drain stderr, log non-zero exit, return False (no timeout)
  if now > absolute_deadline → kill, return True
  read progress_file bytes
    if changed since last read → update last_progress_time
  if now - last_progress_time > stall_timeout_seconds → kill, return True
```

Stderr is drained by a background daemon thread started before the loop.
This prevents the subprocess's stderr pipe buffer from filling and blocking
the subprocess while the parent is busy in the poll loop.

Progress is detected by comparing `Path.read_bytes()` snapshots. Any byte
change (phase transition, artifact counter increment, window counter) resets
the stall timer. A `FileNotFoundError` on the progress file is ignored (the
subprocess may not have written it yet).

`last_progress_time` is initialised to the moment `join()` is called, giving
the subprocess `stall_timeout_seconds` to write its first progress byte.

### Configuration

```toml
[extraction]
stall_timeout_seconds       = 300   # kill if no progress for 5 min (default)
max_extraction_item_seconds = 7200  # absolute fallback (2 h)
```

`stall_timeout_seconds` governs the per-call stall detector.
`max_extraction_item_seconds` remains as a safety net against processes that
somehow keep tickling the progress file without actually finishing.

### Backward Compatibility

- `dual_lane_enabled = false` → single-lane path unchanged.
- `_process_single_lane` passes `stall_timeout_seconds` to `_spawn_item_subprocess`
  the same way.
- The stall-kill error string begins with `"timeout:"` so `retry_failed_extractions`
  continues to requeue these items (not tombstoned).

## Invariants

- Both lane threads share no mutable state other than the top-level `progress`
  dict, which is structured with disjoint sub-keys ("quick", "slow").
- `mark_processing` is called inside the lane thread (per item, before spawning),
  not upfront. The job lock prevents another `process_extraction_queue` run from
  picking the same items.
- Failure in one lane (including stall kill) does not cancel the other lane
  thread. Both threads run to completion.

## Implementation Scope

| File | Change |
|------|--------|
| `src/second_brain/config.py` | `stall_timeout_seconds` in `ExtractionConfig` |
| `src/second_brain/extraction/worker.py` | `_LaneHandle.join()` rewrite; `_process_dual_lane` thread split; remove `_join_lane_pair` |
| `config/memory.example.toml` | Document new key |
| `config/memory.toml` | Set production values |
| `tests/test_extraction_layer.py` | Stall-timeout and decoupled-lane tests |

## Verification

```bash
python -m pytest tests/test_extraction_layer.py -k "stall or decouple or lane" -v
python -m pytest tests/ -q
```
