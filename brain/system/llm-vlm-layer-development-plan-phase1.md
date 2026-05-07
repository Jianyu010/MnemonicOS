# LLM/VLM Layer Phase 1: Legacy Promotion Removal

## Goal

Remove the old west-to-wiki compiler so the new LLM/VLM layer starts from a
clean breakpoint.

## Context

The old promotion runtime predates the west-flange projection layer. It mixes raw
session handling, explicit-memory-block extraction, queue state, duplicate/update
decisions, wiki mutation, review creation, sync, and metrics. The new layer must
replace this with a source-agnostic extraction queue that terminates before wiki
writes.

## Scope

Remove or obsolete:

- `promotion_queue`
- `process_promotion_queue`
- `backfill_promotion_queue`
- `--promote-now`
- `ingest_session_promote()`
- `_promote_session_content()`
- `_enqueue_promotion_candidate()`
- explicit-memory-block promotion behavior
- `project_episode_for_promotion()` as a wiki-writing path

Keep only reusable deterministic helpers if they still serve later phases:

- note normalization
- note path resolution
- note body/render helpers
- Obsidian link projection
- parser validation
- duplicate/update decision logic
- `sync_vault`

## Milestones

1. Legacy promotion daemon jobs are removed from schedules, CLI choices, API job
   handling, and status summaries.
2. `ingest-session` archives raw material without promoting or enqueueing old
   promotion work.
3. Captured episode projection no longer writes wiki.
4. Old promotion tests are removed, marked obsolete, or rewritten as extraction
   queue tests.
5. Stale docs are marked clearly:
   - `brain/system/promotion-detach-plan.md`
   - old promotion sections in `docs/RUNTIME_PROTOCOL.md`
   - old promotion sections in `docs/IMPLEMENTATION_PLAN.md`

## Verification

- No daemon schedule contains `process_promotion_queue`.
- CLI no longer exposes or accepts `--promote-now`.
- No public API route claims to project and promote an episode.
- `ingest-session` creates raw archive state but no wiki note.
- Captured episode round trip ends with raw episode state only.
- Existing retrieval tests over manually seeded canonical wiki notes still pass.
- Repository search for `promotion_queue` returns only migration history or
  explicitly obsolete documentation until migrations are reset or retired.

## Out Of Scope

- Creating the new extraction queue.
- Calling VLMs or LLMs.
- Writing wiki through the replacement path.

