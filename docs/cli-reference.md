# MnemonicOS CLI Reference

Command: `mnemonicos [--config PATH] <subcommand>`

`--config PATH` — path to TOML config file. Defaults to `config/memory.toml`, falls back to `config/memory.example.toml`.

---

## Human Operations

Commands intended for direct human use to operate, inspect, and maintain the system.

### `init-db`

Apply SQLite migrations. Run once after install or after pulling schema changes.

```
mnemonicos init-db
```

---

### `sync`

Synchronize vault notes (markdown files) into SQLite.

```
mnemonicos sync [--mode incremental|full] [paths ...]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--mode` | `incremental` | `incremental` syncs changed files only; `full` rescans everything |
| `paths` | all | Optional subset of file paths to sync |

---

### `daemon`

Manage the background daemon process.

```
mnemonicos daemon {start|stop|status|run-job}
```

**`daemon start`** — Start the daemon (runs scheduled jobs).

**`daemon stop`** — Send SIGTERM to the daemon.

**`daemon status [--json]`** — Show daemon liveness, scheduled jobs, next-run times, and last job result. `--json` emits raw JSON.

**`daemon run-job JOB_NAME`** — Ask a running daemon to execute a job immediately (via Unix socket). Falls back to in-process execution if daemon is unreachable. See [run-job](#run-job) for valid names.

---

### `extraction-status`

Inspect the extraction queue.

```
mnemonicos extraction-status [--queue-id N] [--errors-only] [--limit N] [--json]
```

| Arg | Default | Description |
|-----|---------|-------------|
| `--queue-id N` | — | Show detail for a single queue row |
| `--errors-only` | false | Show only error items, grouped into active errors and tombstoned |
| `--limit N` | 20 | Max items returned |
| `--json` | false | Emit raw JSON |

Without flags: shows queue counts by status and the N most recent items.

---

### `retry-extraction`

Reset errored extraction queue items back to `pending` so they will be retried.

```
mnemonicos retry-extraction {--queue-id N | --all-errors} [--include-tombstoned] [--dry-run]
```

| Arg | Description |
|-----|-------------|
| `--queue-id N` | Retry a single item by queue id |
| `--all-errors` | Retry all non-tombstoned error items |
| `--include-tombstoned` | Also reset tombstoned items (`last_error LIKE 'tombstoned:%'`) when using `--all-errors` |
| `--dry-run` | Print what would change without modifying the DB |

**Note:** `attempt_count` is preserved on retry. If an item fails again after retry, the extraction worker will automatically tombstone it (prefixes `last_error` with `tombstoned:`). Tombstoned items are excluded from `--all-errors` by default; pass `--include-tombstoned` to override.

**Tip for large artifacts (textbooks, long PDFs):** If extraction fails with `HTTP Error 500`, the LLM context window was exceeded. The worker sends one text window (~10 000 tokens) per LLM call by default (`extraction.max_windows_per_batch = 1`). Increasing this value on capable hardware reduces round-trips but risks context overflow on smaller models.

---

### `promote-status`

Show promotion queue health.

```
mnemonicos promote-status [--json]
```

Displays counts of `promotion_ready`, `promoted`, `review_routed`, `rejected`, and the timestamp of the last promotion run.

---

### `review`

Inspect and process notes routed to human review.

```
mnemonicos review [list|show NAME|approve NAME|reject NAME]
```

**No subcommand** — Interactive terminal UI for reviewing items one by one.

**`review list [--json]`** — List all pending review items.

**`review show NAME`** — Display a review item in full.

**`review approve NAME [--lifecycle-state STATE] [--dry-run]`** — Approve and promote to wiki. `STATE` ∈ `{draft, verified, canonical, contested, archived}`.

**`review reject NAME [--dry-run]`** — Reject and delete the review item.

---

### `run-job`

Run a maintenance job directly (bypasses the daemon scheduler).

```
mnemonicos run-job JOB_NAME [--dry-run]
```

| Job name | Schedule | Description |
|----------|----------|-------------|
| `process_extraction_queue` | every 5 min | Run LLM/VLM extraction on pending items |
| `process_promotion_queue` | every 5 min | Promote extraction candidates to wiki |
| `expire_stale_episodes` | hourly :00 | Close raw capture episodes idle > threshold |
| `embed_backfill` | hourly :15 | Compute missing embedding vectors |
| `status_report` | hourly :00 | Write queue/storage health snapshot to job_runs |
| `wiki_embedding_neighbor_backfill` | hourly :20 | Assert embedding-neighbor edges in wiki graph |
| `wiki_cluster_validation` | hourly :25 | Validate and prune wiki clusters |
| `wiki_pattern_discovery` | hourly :30 | Discover new cross-note patterns |
| `wiki_graph_rebuild` | hourly :35 | Rebuild wiki graph edges from scratch |
| `wiki_lifecycle_audit` | hourly :40 | Advance note lifecycle states |
| `wiki_importance_scoring` | hourly :45 | Score notes by retrieval signal |
| `rerank_training` | hourly :47 | Train reranker model from feedback |
| `wiki_health_report` | hourly :50 | Compute wiki health stats and write maintenance overview |
| `retry_failed_extractions` | hourly :55 | Reset non-tombstoned extraction errors back to pending |
| `wiki_derive_passive_feedback` | every 30 min | Derive implicit feedback from session traces |
| `wiki_raw_revisitation` | daily 02:00 | Requeue mature sources for re-extraction with updated context |
| `sync_vault_incremental` | on-demand | Incremental vault sync |

`--dry-run` runs the job logic without writing changes; stats are still reported.

---

### Ingestion path overview

MnemonicOS has two distinct manual ingestion paths. Choosing the wrong one means content is processed by the wrong extraction pipeline.

| | `ingest-session` | `ingest-artifact` |
|---|---|---|
| **Input** | Text / markdown | Binary or text file copied to vault |
| **Identity** | `bridge-id` + `session-id` (who wrote it) | None — file hash is the identity |
| **Dedup** | None | SHA-256 exact-duplicate detection |
| **Extraction** | Session transcript → memory items | Typed extractor (PDF parser, HTML reader, etc.) |
| **Manifest** | No | Yes — page/figure/slide counts, source format |
| **Use when** | Importing notes, summaries, freeform text | Importing PDFs, DOCX, PPTX, HTML, images |

The agentic ingestion path (conversations via the bridge API) uses **sessions** for dialogue turns and **artifacts** for file attachments (images, HTML exports, etc.). Manual ingestion should follow the same logic: text → session, file → artifact.

---

### `ingest-session`

Manually archive text content as a session for later extraction.

```
mnemonicos ingest-session [--bridge-id ID] [--session-id ID] \
  {--file PATH | --content TEXT} [--tag TAG ...] [--source-ref REF ...]
```

`--bridge-id` defaults to `cli-call`; `--session-id` defaults to current timestamp. `--agent` and `--slug` are deprecated aliases.

---

### `ingest-artifact`

Ingest a single artifact file (PDF, DOCX, PPTX, HTML, or passthrough).

```
mnemonicos ingest-artifact --file PATH [--tag TAG ...] \
  [--attachment PATH_OR_DIR ...] [--attachment-recursive] [--json]
```

`--attachment` archives companion files alongside the primary artifact (e.g. images with an HTML export). It is **not** for bulk ingestion of independent files — use `ingest-dir` for that.

**PDF ingestion** has two stages:

1. **Ingest** (fast): the raw file is copied to the vault, page/image counts are recorded, and the artifact is queued for extraction. Returns `status=accepted` immediately — text extraction does not block the CLI. Controlled by `ingest.lazy_pdf_ingest` (default: `true`).
2. **Extraction** (async, via daemon or `trigger_pipeline`): the worker extracts text using [PyMuPDF](https://pymupdf.readthedocs.io) for text-layer PDFs. For scanned / image-only PDFs (`image_only_pdf=true`), OCR is attempted via [Tesseract](https://github.com/tesseract-ocr/tesseract) + [pytesseract](https://github.com/madmaze/pytesseract), limited to the first 50 pages by default. Page images are also rendered and queued for VLM description.

To disable lazy ingest and extract text synchronously at ingest time, set:

```toml
[ingest]
lazy_pdf_ingest = false
```

**Extraction windowing** — the projection layer splits each artifact's text into windows of `~10 000` tokens (paragraphs, with 512-token overlap). The extraction worker sends one window per LLM call by default. Relevant `[extraction]` config keys:

| Key | Default | Description |
|-----|---------|-------------|
| `max_windows_per_batch` | `1` | Windows sent per LLM call. Keep at `1` for models with ≤ 32 K context. Increase only if your model handles larger inputs. |
| `max_queue_batch` | `10` | Artifacts processed per daemon tick (every 5 min). |

Large textbooks produce many windows (a 566-page PDF ≈ 17 windows). At `max_windows_per_batch = 1` the daemon processes one window per LLM call, serialised within each artifact, emitting N candidates per window (one candidate per discrete knowledge piece: theorem, definition, proof, procedure). All candidates across windows are merged before promotion.

---

### `ingest-dir`

Bulk-ingest files under a directory. Routes each file to `ingest-artifact` or `ingest-session` depending on `--mode`. Artifact dedup (SHA-256) prevents re-ingesting identical files.

```
mnemonicos ingest-dir DIR [--mode {artifact,session,auto}] [--recursive] \
  [--ext EXT ...] [--bridge-id ID] [--tag TAG ...] [--dry-run] [--json]
```

| Flag | Description |
|---|---|
| `DIR` | Root directory to scan |
| `--mode` | `auto` (default): `.md/.txt/.rst` → session, everything else → artifact. `artifact`: all files via artifact path. `session`: all files via session path |
| `--recursive` | Descend into subdirectories |
| `--ext EXT` | Only ingest files with this extension (repeatable). Default: all files |
| `--bridge-id ID` | bridge-id for session-routed files (default: `cli-call`) |
| `--dry-run` | Print files and their route without ingesting |
| `--json` | Emit one JSON object per file |

Examples:

```bash
# Ingest all PDFs under a textbook library (auto-routes to artifact)
mnemonicos ingest-dir local/University/Textbooks --recursive --ext pdf --tag textbook

# Mixed directory: markdown → session, PDF/DOCX → artifact
mnemonicos ingest-dir docs/ --recursive --tag imported

# Preview routing without ingesting
mnemonicos ingest-dir local/dump --dry-run

# Force all files as sessions (e.g. exported notes)
mnemonicos ingest-dir exports/ --mode session --bridge-id notion-export
```

---

### `migrate-raws`

Register a MnemonicOS raw directory from another instance into this instance's DB, then queue all found sources for extraction. Files are registered at their current path — no copying. Use this when you have `rsync`'d or copied a `brain/raw/` tree from another machine.

```
mnemonicos migrate-raws SOURCE [--dry-run] [--json]
```

`SOURCE` can be:
- A full `brain/raw/` directory — scans both `sessions/` and `artifacts/` subtrees
- Just `brain/raw/sessions/` — registers episodes only
- Just `brain/raw/artifacts/` — registers artifacts only

For each episode found, reads `manifest.json`, registers the episode in `raw_episodes`, and enqueues it as `source_kind=episode`. For each artifact dir with a `manifest.json`, re-registers it in `archive` (skips if archive_id already exists) and enqueues for extraction.

```bash
# Dry-run to preview
mnemonicos migrate-raws /Volumes/OldMachine/ai_memory/brain/raw --dry-run

# Full migration
mnemonicos migrate-raws /Volumes/OldMachine/ai_memory/brain/raw
```

---

### `reprocess-artifact`

Re-run extraction against an already-archived artifact.

```
mnemonicos reprocess-artifact {--archive-id ID | --failed} [--json]
```

`--failed` reprocesses all artifacts whose last ingest failed.

---

### `retrieve`

Run hybrid retrieval against the wiki and archive.

```
mnemonicos retrieve QUERY [--top-k N] [--query-type-hint HINT] [--include-body] [--body-chars N] [--json]
```

`--query-type-hint` ∈ `{how_to, decision, who_where_relation}`. Adjusts channel weights.

By default, retrieve output stays compact: hits expose `summary`, `excerpt`, and
`body_path`, but not full note text. `--include-body` hydrates returned wiki hits
from `body_path` for inspection. Combine it with `--body-chars N` to cap each
body. Archive chunks are not hydrated by this flag.

---

## Agentic / Bridge Operations

Commands called by agents, hooks, or integration scripts — not intended for routine human use.

### `capture begin-episode`

Begin a raw capture episode for a bridge session.

```
mnemonicos capture begin-episode --bridge-id ID \
  [--conversation-id ID] [--episode-id ID] \
  [--parent-conversation-id ID] [--related-conversation-id ID ...] \
  [--task-id ID] [--cwd PATH] [--repo PATH] [--branch NAME] \
  [--model NAME] [--agent-runtime NAME] [--platform-thread-id ID] \
  [--metadata-json JSON] [--json]
```

### `capture append-event`

Append one event to an active episode.

```
mnemonicos capture append-event \
  --bridge-id ID --conversation-id ID --episode-id ID \
  --type TYPE \
  [--payload-json JSON | --payload-file PATH | --content TEXT] \
  [--task-id ID] [--branch-id ID] [--timestamp ISO8601] [--json]
```

### `capture end-episode`

Close an active episode.

```
mnemonicos capture end-episode \
  --bridge-id ID --conversation-id ID --episode-id ID \
  [--status success|failure|partial|abandoned] \
  [--metadata-json JSON] [--json]
```

---

### `queue-extraction`

Enqueue a source for LLM/VLM extraction.

```
mnemonicos queue-extraction --source-kind {artifact|episode|session} \
  [--archive-id ID] [--conversation-id ID] [--episode-id ID] \
  [--bridge-id ID] [--session-id ID] [--task-id ID] [--json]
```

`source_kind=artifact|session` requires `--archive-id`.  
`source_kind=episode` requires `--conversation-id` and `--episode-id`.

---

### `orchestrator begin-task`

Resolve memory context before an LLM call. Primary entry point for agents.

```
mnemonicos orchestrator begin-task TASK_TEXT \
  --bridge-id ID \
  [--session-id ID] [--task-id ID] [--task-kind KIND] \
  [--temporality current|historical|mixed|unknown] \
  [--token-budget N] [--workspace WS] [--project PROJ] [--repo REPO] \
  [--tag TAG ...] [--auto-retry] [--max-focused-retries N] [--json]
```

Auto-generates `session-id` and `task-id` if absent. Returns a memory packet with retrieval hits, recommendation, and writeback guidance.

### `orchestrator mark-failure`

Record a task failure and re-resolve context with the same `task_id`.

```
mnemonicos orchestrator mark-failure FAILURE_TEXT \
  --bridge-id ID --session-id ID --task-id ID \
  [--task-kind KIND] [--query-id N] [--retrieval-state STATE] [--json]
```

### `orchestrator finish-task`

Record final outcome and compute writeback decision.

```
mnemonicos orchestrator finish-task \
  --bridge-id ID --session-id ID --task-id ID \
  --status success|failure|partial \
  --outcome-type TYPE \
  [--summary TEXT | --summary-file PATH] \
  [--query-id N] [--retrieval-state STATE] [--recommendation TEXT] \
  [--useful-id ID ...] [--misleading-id ID ...] [--fresh-source SRC ...] \
  [--note-type TYPE] [--title TITLE] [--writeback-intent INTENT] [--json]
```

---

### `resolve-task-context`

Lower-level alternative to `orchestrator begin-task`. Returns the same memory packet but does not auto-manage session/task IDs.

```
mnemonicos resolve-task-context TASK_TEXT \
  --bridge-id ID --session-id ID --task-id ID \
  [--task-kind KIND] [--temporality ...] [--token-budget N] \
  [--workspace WS] [--project PROJ] [--repo REPO] \
  [--tag TAG ...] [--top-k N] [--query-type-hint HINT] \
  [--no-archive-fallback] [--json]
```

### `record-outcome`

Record outcome without writeback decision computation.

```
mnemonicos record-outcome \
  --bridge-id ID --session-id ID --task-id ID \
  --outcome-type TYPE --status STATUS --summary TEXT \
  [--task-kind KIND] [--query-id N] [--retrieval-state STATE] \
  [--recommendation TEXT] [--fresh-research-needed] \
  [--fresh-source SRC ...] [--useful-id ID ...] [--misleading-id ID ...] \
  [--json]
```

### `decide-writeback`

Compute writeback decision without recording an outcome.

```
mnemonicos decide-writeback \
  --bridge-id ID --session-id ID --task-id ID \
  --note-type TYPE --title TITLE --summary-text TEXT \
  [--task-kind KIND] [--retrieval-state STATE] [--writeback-intent INTENT] \
  [--existing-note-candidate ID ...] [--update-target-id ID ...] \
  [--fresh-research-needed] [--fresh-source SRC ...] [--json]
```

---

## Entry Points

| Command | Module |
|---------|--------|
| `mnemonicos` | `second_brain.cli:main` |
| `mnemonicos-daemon` | `second_brain.daemon:main` |
