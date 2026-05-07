# OBSOLETE: MnemonicOS Refactor: Detach Old Promotion Runtime

This transition plan is superseded by the clean-slate LLM/VLM layer plan. The legacy promotion queue, workers, synchronous promotion helpers, and `--promote-now` path have been removed rather than kept as a compatibility bridge.

## Context

MnemonicOS has three solid components ready for the new extraction-based architecture:

1. **Ingestion** — raw material dump + registration (`_archive_session`, `promotion_queue`, `archive` table, `raw_episodes` table)
2. **Wiki structure** — schemas, note types, frontmatter rules, directory layout in `brain/wiki/`
3. **Bridges** — `session_capture.py` (episode capture), orchestrator (context retrieval), hooks

The old **promotion runtime** is the daemon-driven pipeline that dequeues `promotion_queue` rows and calls `_promote_session_content()` to write wiki files. It was designed around LLM-discretionary memory blocks (` ```memory ` sections in session summaries). This runtime must be safely detached before the new <=8B extraction-based pipeline replaces it.

**Goal:** Preserve ingestion + wiki structure + bridges fully intact. Sever automatic promotion and the stop hook's summary-as-event anti-pattern. Leave a clean queue seam for the future extraction daemon.

Important correction: `promotion_queue.archive_id` currently has a foreign key to `archive(id)`. Captured raw episodes are indexed in `raw_episodes`, not `archive`, and should not get synthetic `archive` rows. Therefore episode work items need either a queue schema extension or a sibling extraction queue. This plan uses a schema extension so legacy session rows and captured episode rows can share one queue.

---

## Architecture After Detach

```
UserPromptSubmit hook
    → begin_episode (or reopen) in raw_episodes
    → append user_message event to events.jsonl

Agent turn runs
    → tool calls / file edits appended to events.jsonl by hook
    → agent writes /tmp/mnemo_pending_summary.md (sidecar only)

Stop hook
    → append agent-summary.md to episodes/{id}/artifacts/ as artifact_created event
    → end_episode (finalizes events.jsonl + manifest)
    [NO project-episode call]
    [NO _promote_session_content call]

Ingestion (legacy sessions)
    → _archive_session() writes raw to brain/raw/sessions/
    → _enqueue_promotion_candidate() inserts into promotion_queue (status=pending)
    [NO automatic dequeue]

promotion_queue table
    → accumulates pending rows
      source_kind=archive_session for legacy session archives
      source_kind=episode for raw_episodes rows
    → future extraction daemon will dequeue

brain/wiki/ structure
    → unchanged
    → only written to by future extraction daemon (or manual operations)
```

---

## What to Preserve (Untouched)

| Component | Files | Why Keep |
|-----------|-------|----------|
| Raw archival | `ingest.py: _archive_session()` | Writes true raw to `brain/raw/sessions/`, populates `archive` + `archive_chunks` |
| Queue insert | `ingest.py: _enqueue_promotion_candidate()` | Sessions continue to queue; new daemon will dequeue |
| `promotion_queue` table | `migrations/009_phase10_promotion_queue.sql` + new migration | Bridge to future extraction path |
| `raw_episodes` table | `migrations/011_raw_episodes.sql` | Episode index for future extraction daemon |
| Wiki directory layout | `brain/wiki/{concepts,decisions,...}/` | Canonical note storage untouched |
| Note schemas + rendering | `ingest.py: _render_note(), _path_for_item(), _render_note_body()` | Pure functions, no side effects |
| Wiki sync | `sync.py: sync_vault()` | Still needed for retrieval freshness |
| `ingest-session` CLI | `cli.py: ingest-session` | Still archives raw + queues; just won't auto-promote |
| Orchestrator | `orchestrator.py` | `begin-task`/`finish-task` remain independent; already return decision only, do not call `ingest_session_promote()` |
| Session capture | `session_capture.py` | Episode capture untouched |
| Preflight hook | `scripts/mnemonic_hook_preflight.sh` | Retrieval context injection untouched |
| Active daemon jobs | `embed_backfill`, `sync_vault`, `hygiene` | Infrastructure, not promotion |

---

## Changes Required

### Change 0: Extend queue/index schema for detached episode work

**File:** new migration after `011_raw_episodes.sql`

Do not create synthetic `archive` rows for episodes. Add an explicit source discriminator to `promotion_queue`:

```sql
ALTER TABLE promotion_queue ADD COLUMN source_kind TEXT NOT NULL DEFAULT 'archive_session'
  CHECK (source_kind IN ('archive_session', 'episode'));
ALTER TABLE promotion_queue ADD COLUMN conversation_id TEXT;
ALTER TABLE promotion_queue ADD COLUMN episode_id TEXT;
ALTER TABLE promotion_queue ADD COLUMN content_path TEXT;
ALTER TABLE promotion_queue ADD COLUMN artifact_path TEXT;
CREATE INDEX IF NOT EXISTS idx_promotion_queue_source_kind_status
  ON promotion_queue(source_kind, status, queued_at);
CREATE INDEX IF NOT EXISTS idx_promotion_queue_episode
  ON promotion_queue(conversation_id, episode_id);
```

Implementation note:

- Keep `archive_id` for legacy archive-session rows.
- For episode rows, either make `archive_id` nullable in a replacement-table migration or store a stable non-FK `episode_ref` only if the existing FK remains unavoidable. Preferred: replacement-table migration that allows `archive_id NULL` when `source_kind='episode'`.
- Add `queued` to `raw_episodes.projection_status`: `not_projected`, `queued`, `projected`, `skipped`, `error`.
- Episode rows should use `(source_kind='episode', conversation_id, episode_id, content_path/events_path)` as their durable identity.

This is required before `project_episode_for_promotion()` can enqueue episodes without violating the `archive(id)` foreign key.

### Change 1: Remove `process_promotion_queue` daemon job

**File:** `src/second_brain/daemon_jobs.py`

Remove the `process_promotion_queue` cron entry from `_DEFAULT_SCHEDULES`:

```python
# REMOVE:
"process_promotion_queue": "*/5 * * * *",
```

`_process_promotion_queue()` in `jobs.py` remains in the file but unreachable from the scheduler. No deletion required — the function is the future extraction daemon's reference implementation.

**Impact:** Pending `promotion_queue` rows accumulate indefinitely. Wiki writes stop. New daemon will dequeue.

---

### Change 2: Remove or guard all normal immediate-promotion entrypoints

**Files:** `src/second_brain/cli.py`, `scripts/mnemonic_postflight.sh`

`--promote-now` on `ingest-session` calls `ingest_session_promote()` which calls `_promote_session_content()` synchronously. Remove the flag:

```python
# REMOVE from ingest-session subparser:
ingest_parser.add_argument("--promote-now", action="store_true", ...)
```

`ingest_session_promote()` function in `ingest.py` stays (reference for future extraction path) but should not be reachable from normal CLI.

Also update `scripts/mnemonic_postflight.sh`:

- Remove `--promote-now` from accepted options, usage, and forwarded args; or make it error with a clear deprecation message.
- Dry-run messaging should say "archived and queued for future extraction", not "daemon/background promotion".

---

### Change 3: Detach `project_episode_for_promotion` from old promotion runtime and rename intent

**File:** `src/second_brain/ingest.py` (lines 1286+)

`project_episode_for_promotion()` correctly projects `events.jsonl` → in-memory markdown via `_project_episode_markdown()`. The problem is it then calls `_promote_session_content()` immediately.

**Fix — split into two phases and make the name honest:**

```python
def project_episode_for_promotion(config, *, conversation_id, episode_id):
    """Project captured episode events to in-memory markdown and enqueue for
    future extraction. Does NOT call _promote_session_content().
    """
    # Phase 1: project events to markdown (keep as-is)
    ...
    content = _project_episode_markdown(
        conversation_id=conversation_id,
        episode_id=episode_id,
        ...
        events=events,
    )

    # Phase 2: DETACHED — enqueue for future extraction daemon
    # OLD: promoted_notes, review_items = _promote_session_content(...)
    # NEW: enqueue to promotion_queue as source_kind='episode',
    #      update raw_episodes.projection_status = 'queued'
    _enqueue_promotion_candidate(
        connection,
        source_kind="episode",
        archive_id=None,
        conversation_id=conversation_id,
        episode_id=episode_id,
        bridge_id=bridge_id,
        session_id=session_id,
        content=content,
        summary_payload=json.dumps({}),
        ...
    )
    connection.execute(
        """UPDATE raw_episodes SET projection_status = 'queued',
           updated_at = datetime('now')
           WHERE conversation_id = ? AND episode_id = ?""",
        (conversation_id, episode_id),
    )
    connection.commit()
    return IngestSessionResult(archive_id=archive_id, ...)
```

Better API name:

- keep `project_episode_for_promotion()` as a temporary compatibility wrapper if needed
- add `enqueue_episode_projection()` or `queue_episode_for_extraction()` as the new explicit function name
- CLI/API should prefer `capture queue-episode` rather than `capture project-episode`

Also add deprecation docstrings to `_promote_session_content()` and `ingest_session_promote()` marking them as legacy — do not call from new code.

---

### Change 4: Stop hook — summary as sidecar artifact

**File:** `scripts/mnemonic_stop_hook.sh`

**Problem A: Summary embedded in events.jsonl as `assistant_message`**

The stop hook appends `mnemo_pending_summary.md` content as an `assistant_message` event. This contaminates the comprehensive raw event log with agent-synthesized content. The summary is a sidecar document; it should not appear as a primary event.

**Fix:**

First add attachment support to `capture append-event`:

- `--attachment-file PATH`
- `--attachment-name NAME`
- session_capture copies the attachment into `episodes/{episode_id}/artifacts/{NAME}`
- event payload records only non-content metadata: artifact path, mime type, byte size, hash, source
- HTTP equivalent accepts either a local `attachment_path` or, if needed later, multipart upload

Then change the stop hook:

```bash
# BEFORE (embeds synthesized content in raw log):
"${CLI[@]}" capture append-event \
  --type assistant_message \
  --payload-file "$PAYLOAD_FILE" ...

# AFTER (stores summary as named sidecar artifact):
"${CLI[@]}" capture append-event \
  --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" \
  --episode-id "$EPISODE_ID" \
  --type artifact_created \
  --payload-json "{\"artifact\": \"artifacts/agent-summary.md\", \"mime_type\": \"text/markdown\", \"source\": \"agent_summary\"}" \
  --attachment-file "$PENDING" \
  --attachment-name "agent-summary.md" \
  --json >/dev/null 2>&1 || true
```

The summary content goes to `episodes/{episode_id}/artifacts/agent-summary.md`. An `artifact_created` event in `events.jsonl` records that a summary was written and where to find it. The comprehensive log contains no synthesized text.

**Problem B: `capture project-episode` triggers old promotion**

```bash
# REMOVE these lines entirely:
if [[ -f "$PENDING" && -s "$PENDING" ]]; then
  "${CLI[@]}" capture project-episode \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --json >/dev/null 2>&1 || true
fi
```

`project-episode` calls `project_episode_for_promotion()` which (before Change 3) calls `_promote_session_content()`. After Change 3 it only enqueues. But regardless: do not call projection at every turn end — let the daemon batch-process episodes on its own schedule.

**Net result after Change 4:**

- `events.jsonl` = raw observable events only (user messages, tool calls, tool results, file changes, artifact pointers)
- `artifacts/agent-summary.md` = standalone sidecar, generated by agent, stored beside episode data
- No promotion fires at turn end
- `raw_episodes.projection_status` stays `not_projected` until daemon processes it

---

### Change 5: Detach orchestrator `finish-task` hidden ingest

**Files:** `src/second_brain/cli.py`, `src/second_brain/server.py`

Current CLI/server `orchestrator finish-task` wrappers call `ingest_session()` when the writeback decision is not `skip`. That creates a legacy raw session archive and queues old summary content. During detach, this should be disabled by default.

Options:

1. Remove the hidden `ingest_session()` call entirely.
2. Gate it behind an explicit `--archive-summary` / `archive_summary: true` flag for backward compatibility.

Preferred for refactor: explicit flag only. Orchestrator should return runtime/writeback decisions; raw capture belongs to `session_capture`.

---

### Change 6: Update PreCompact summary handling

**File:** `scripts/mnemonic_precompact_dump.sh`

Current Claude `PreCompact` copies `/tmp/mnemo_pending_summary.md` directly into `brain/raw/sessions/*.md`. After detach, that creates an extra legacy raw session shape outside the episode directory.

Change it to:

- if capture state exists, copy pending summary to the current episode's `artifacts/agent-summary-precompact-{trigger}.md` and append an `artifact_created` event
- set/merge `context_compressed=true` in the episode manifest, if available
- if no capture state exists, keep the old raw dump fallback only as a compatibility path and tag/filename it clearly as `legacy-precompact`

Note: PreCompact cannot preserve original tokens after platform compression. It only preserves the agent-provided pending summary, which is synthetic sidecar evidence, not primary raw conversation.

## Seam for Future Extraction Daemon

`promotion_queue` table is the plug-in point after Change 0. Future daemon:

1. `SELECT * FROM promotion_queue WHERE status = 'pending' ORDER BY queued_at`
2. For `source_kind='episode'`: use `conversation_id` + `episode_id`, read `raw_episodes.events_path`, load sidecar artifacts as needed, run VLM/<=8B extraction
3. For `source_kind='archive_session'`: read `content` or `archive.path` directly
4. Write structured candidates to wiki via `_render_note()` + `_path_for_item()` + `sync_vault()`
5. `UPDATE promotion_queue SET status = 'processed'`
6. `UPDATE raw_episodes SET projection_status = 'projected'`

---

## Daemon Jobs That Remain Active

| Job | Schedule | Purpose |
|-----|----------|---------|
| `embed_backfill` | `*/15 * * * *` | Vectorize un-embedded archive chunks |
| `sync_vault` | `*/10 * * * *` | Sync wiki disk → SQLite |
| `hygiene` | `0 * * * *` | Prune stale task_outcomes, cleanup |

Do NOT remove these — they are infrastructure supporting retrieval freshness.

---

## Critical Files

| File | Change |
|------|--------|
| `src/second_brain/daemon_jobs.py` | Remove `process_promotion_queue` from `_DEFAULT_SCHEDULES` |
| `src/second_brain/cli.py` | Remove `--promote-now`; add attachment args; prefer `capture queue-episode`; guard hidden orchestrator ingest |
| `src/second_brain/server.py` | Add matching queue/attachment behavior; guard hidden orchestrator ingest |
| `src/second_brain/ingest.py` | Split/rename episode projection — project-only + enqueue phases; deprecation comments on `_promote_session_content()` and `ingest_session_promote()` |
| `src/second_brain/session_capture.py` | Add attachment sidecar support for `artifact_created` |
| `scripts/mnemonic_stop_hook.sh` | (1) Change summary append: `assistant_message` → `artifact_created` sidecar; (2) Remove `capture project-episode` call |
| `scripts/mnemonic_precompact_dump.sh` | Store precompact summaries as episode sidecar artifacts when capture state exists |
| `scripts/mnemonic_postflight.sh` | Remove or error on `--promote-now` |
| new migration | Extend `promotion_queue` for `source_kind='episode'`; add `queued` projection state |

---

## Deprecation Criteria for Old Promotion Runtime

Keep `_promote_session_content()` and `ingest_session_promote()` in the file (commented as legacy) until:

1. New extraction daemon ships and has processed at least one `promotion_queue` batch end-to-end
2. `brain/wiki/` shows promoted notes from the new extraction path
3. All active hooks have migrated to sidecar summary pattern
4. One release cycle passes with `--promote-now` flagged as deprecated

After those criteria: remove `_promote_session_content()`, `_process_promotion_queue()`, and `ingest_session_promote()`.

---

## Verification

1. `mnemonicos ingest-session --file test.md --bridge-id test` → archives raw, queues row with `status=pending`, no wiki writes
2. Inspect `promotion_queue` — row exists; `brain/wiki/` unchanged
3. Simulate stop hook turn:
   - Write test content to `/tmp/mnemo_pending_summary.md`
   - Run stop hook with fake STATE_FILE containing `conversation_id` + `episode_id`
   - Verify `events.jsonl` has `artifact_created` event (no `assistant_message` with summary content)
   - Verify `episodes/{id}/artifacts/agent-summary.md` exists as standalone file
   - Verify `raw_episodes.projection_status` = `not_projected` (no promotion triggered)
4. Run daemon for 10 minutes → confirm no wiki writes occur from `process_promotion_queue`
5. `mnemonicos retrieve "test content"` → raw chunks still retrievable from `archive_chunks`
6. Wiki structure intact: `brain/wiki/concepts/`, `decisions/`, etc. unchanged
7. `mnemonicos ingest-session --file test.md --promote-now` → error or deprecation warning (flag removed)
8. `orchestrator finish-task` without explicit archive flag → no `archive` row and no `promotion_queue` row
9. PreCompact with capture state → sidecar artifact under episode `artifacts/`, not top-level legacy raw session
