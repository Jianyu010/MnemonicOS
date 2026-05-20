# LLM/VLM Layer Phase 12: Dual-Lane Concurrent Extraction

## Goal

Prevent heavy documents from blocking agent memory capture by running one
quick-lane subprocess (episode, session, lightweight text) and one slow-lane
subprocess (PDF, DOCX, PPTX, unknown artifacts) concurrently inside a single
`process_extraction_queue` job invocation.

## Context

Phase 11 introduced priority-ordered `pending_items()`, sorting pending rows by
coarse expected processing cost before FIFO. This ensures that when a batch is
selected, light work appears first. However, the worker still processes items
strictly one at a time: a single slow PDF extraction (30–90 min) blocks every
subsequent item in the same batch until it finishes.

With `OLLAMA_NUM_PARALLEL=2` set in the Ollama environment, the inference
server can serve two concurrent requests. This makes true parallel extraction
worthwhile: while the slow lane's LLM call is in-flight, the quick lane's LLM
call can also run, and all non-inference work (projection load, windowing, DB
writes) in both items overlaps freely regardless of Ollama parallelism.

Phase 12 is the deliberate extension of Phase 11's "Out of Scope: Changing
daemon job concurrency." Phase 11 isolated the scheduling concern; Phase 12
adds the concurrency mechanism.

## Architecture

Two lane-specific DB queries split the pending set by expected cost:

- **Quick lane** (`pending_quick_items`): `source_kind IN ('episode', 'session')`
  plus `artifact` rows whose `archive.source_format` is lightweight text
  (`md`, `markdown`, `txt`, `text`, `plain_text`, `log`). These map to
  lanes 0–1 from Phase 11.
- **Slow lane** (`pending_slow_items`): all other artifact rows — unknown
  formats and known-heavy formats (`pdf`, `docx`, `pptx`). These map to
  lanes 2–3 from Phase 11.

Each job run fetches up to `quick_lane_batch` quick items and up to
`slow_lane_batch` slow items independently. The worker loops over pairs:
one quick item + one slow item per iteration. Both are marked `processing`
before either subprocess is spawned. Each pair runs in its own thread and
is joined before the next pair is started.

When one lane is exhausted, the surviving item runs solo with no wasted slot.
The existing single-lane path is preserved, selected by `dual_lane_enabled`.

## Invariants

- `mark_processing` is called for both items **before** either subprocess
  starts. This prevents the next daemon tick from picking the same items even
  if the job takes longer than the cron interval.
- Failures in one lane do not cancel the other. Both lanes are joined
  independently and their results accumulated separately.
- Dry-run mode always uses the single-lane path. Dual-lane runs live
  subprocesses only.
- The extraction queue schema is unchanged. No new columns are added.
- Lane queries use the same `archive.source_format` join as Phase 11. No new
  DB metadata is required.
- Quick and slow lane queries are disjoint: an item cannot appear in both.
- When `dual_lane_enabled = false` (the default), the worker behaves exactly
  as before Phase 12.

## Configuration

```toml
[extraction]
dual_lane_enabled = true   # off by default
quick_lane_batch = 5       # max items fetched from quick lane per job run
slow_lane_batch = 5        # max items fetched from slow lane per job run
```

`OLLAMA_NUM_PARALLEL=2` must be set in the environment of the Ollama process
(launchd plist, systemd unit, or shell) to enable true parallel inference.
Without it, the second request queues inside Ollama. Extraction is still
correct; only throughput is affected.

## Progress Reporting

The live `job_progress` dict gains a `"mode": "dual_lane"` key when dual-lane
is active. Sub-dicts `"quick"` and `"slow"` carry per-lane state:

```python
{
    "mode": "dual_lane",
    "pair_index": 0,
    "quick_total": 5,
    "slow_total": 5,
    "quick": {
        "item_id": N, "item_name": "episode/2026-05-20T…",
        "lane_index": 0, "lane_total": 5,
        "window": 7, "total_windows": 10, "phase": "extracting",
    },
    "slow": {
        "item_id": M, "item_name": "artifact/large.pdf",
        "lane_index": 0, "lane_total": 5,
        "window": 2, "total_windows": 50, "phase": "extracting",
    },
}
```

`daemon status` and `daemon watch` render both lanes as a "dual lane" block.
Child subprocess progress must flush on both mapping assignment and bulk
updates. `_process_item()` mutates `progress["phase"]`, `progress["window"]`,
and `progress["total_windows"]` directly, so the file-backed progress dict used
by `_subprocess_entry.py` must write through `__setitem__`; otherwise the parent
keeps showing the spawn-time `preparing` state while Ollama is actually running.

Timeout cleanup must also reap the whole subprocess process group before the
lane is considered complete. A timed-out child that remains alive can keep DB
handles and Ollama connections open after its queue row has already been marked
`error`.

## Implementation Scope

Files modified:

| File | Change |
|---|---|
| `src/second_brain/config.py` | `dual_lane_enabled`, `quick_lane_batch`, `slow_lane_batch` in `ExtractionConfig` + TOML loading |
| `src/second_brain/extraction/queue.py` | Add `pending_quick_items()` and `pending_slow_items()` |
| `src/second_brain/extraction/worker.py` | Add `_LaneHandle`, `_make_extraction_proc()`, `_spawn_item_subprocess()`, `_join_lane_pair()`, `_process_single_lane()`, `_process_dual_lane()`; refactor `process_extraction_queue` |
| `src/second_brain/daemon_status.py` | Render dual-lane progress in `daemon_status_summary()` and `_render_watch()` |
| `tests/test_extraction_layer.py` | Lane-split query tests, `_LaneHandle` test |
| `tests/test_daemon_jobs.py` | Dual-lane progress display test |
| `config/memory.example.toml` | Document new keys |

This file is the governing implementation plan. Roadmap summary:
[ingestion-extraction-roadmap.md](ingestion-extraction-roadmap.md).

## Verification

```bash
# Config tests
python -m pytest tests/test_daemon_jobs.py -k "dual_lane" -v

# Lane query tests
python -m pytest tests/test_extraction_layer.py -k "quick_items or lane_handle" -v

# Full suite
python -m pytest tests/ -q
```

After enabling in config and restarting daemon:
```bash
mnemonicos daemon status   # shows "Dual-lane extraction [quick · slow]" when job runs
```

## Out of Scope

- Adaptive or learned lane assignment based on observed extraction times.
- More than two concurrent subprocesses. Two is the natural limit given
  `OLLAMA_NUM_PARALLEL=2` on a single host.
- Intra-lane parallelism (multiple quick items simultaneously). One quick
  + one slow is the target.
- Changing the extraction queue schema to persist lane assignments.
- Continuous drain (while-pending loop without cron). That is a separate
  architectural concern not addressed here.
