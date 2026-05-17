# MnemonicOS HTTP API Reference

Start the server:

```
mnemonicos server [--host 127.0.0.1] [--port 8000]
```

Or in-process:

```python
from second_brain.server import build_server
server = build_server(config)
server.serve_forever()
```

All request and response bodies are JSON. All routes return HTTP 200 on success, 400 on bad input, 500 on internal error.

---

## Health

### `GET /v1/health`

**Response:**
```json
{
  "status": "ok",
  "db_ready": true
}
```

---

## Retrieval

### `POST /v1/retrieve`

Run hybrid retrieval (BM25 + semantic + graph + archive channels).

**Request:**
```json
{
  "query": "string (required)",
  "top_k": 10,
  "query_type_hint": "how_to | decision | who_where_relation",
  "include_archive_fallback": true,
  "include_body": false,
  "body_chars": 4000
}
```

**Response:** `RetrieveResult`
```json
{
  "query_id": 42,
  "hits": [
    {
      "id": "concept/foo",
      "type": "concept",
      "title": "...",
      "body_path": "brain/wiki/...",
      "summary": "...",
      "body": "... only present for CLI/API callers that opt into body hydration ...",
      "body_truncated": false,
      "excerpt": "...",
      "channels": ["bm25", "semantic"],
      "raw_score": 0.87,
      "final_score": 0.91,
      "relevance_score": 0.85,
      "quality_score": 0.72
    }
  ],
  "archive_hits": [...],
  "pinned_paths": [],
  "suppressed_by_gate_count": 0,
  "passed_gate_count": 5
}
```

`body` and `body_truncated` are omitted unless `include_body` is true. Body
hydration is an inspection/debug feature for selected wiki hits; normal retrieval
responses remain compact and archive chunks are not hydrated by this flag.

---

## Ingest

### `POST /v1/ingest/session`

Archive a raw session for later extraction.

**Request:**
```json
{
  "bridge_id": "string (required)",
  "session_id": "string (required)",
  "content": "string (required)",
  "tags": ["optional"],
  "source_refs": ["optional"]
}
```

**Response:**
```json
{
  "archive_id": "...",
  "raw_path": "...",
  "chunk_count": 3,
  "promoted_notes": [],
  "review_items": []
}
```

---

### `POST /v1/ingest/artifact`

Ingest a raw artifact file (PDF, DOCX, PPTX, HTML, or passthrough).

**Request:**
```json
{
  "path": "/absolute/path/to/file (required)",
  "tags": ["optional"],
  "attachments": ["/path/to/companion"]
}
```

**Response:**
```json
{
  "archive_id": "...",
  "raw_path": "...",
  "manifest_path": "...",
  "ingest_status": "ok | failed | needs_manual_projection",
  "source_format": "pdf",
  "figure_count": 2,
  "warnings": [],
  "failure_reason": null,
  "needs_visual_pass": false,
  "needs_manual_projection": false,
  "image_only_pdf": false
}
```

---

### `POST /v1/reprocess/artifact`

Re-run extraction against an archived artifact.

**Request** (one of):
```json
{ "archive_id": "string" }
{ "failed": true }
```

`failed: true` reprocesses all artifacts whose last ingest failed. Returns a list of results in that case.

---

## Raw Capture (Episode Bridge)

### `POST /v1/capture/begin-episode`

Begin a raw capture episode.

**Request:**
```json
{
  "bridge_id": "string (required)",
  "conversation_id": null,
  "episode_id": null,
  "parent_conversation_id": null,
  "related_conversation_ids": [],
  "task_id": null,
  "cwd": null,
  "repo": null,
  "branch": null,
  "model": null,
  "agent_runtime": null,
  "platform_thread_id": null,
  "metadata": {}
}
```

**Response:** `BeginEpisodeResult`
```json
{
  "conversation_id": "...",
  "episode_id": "...",
  "created_at": "2026-05-14T12:00:00Z",
  "parent_conversation_id": null,
  "related_conversation_ids": [],
  "bridge_state": "active"
}
```

---

### `POST /v1/capture/append-event`

Append one event to an active episode.

**Request:**
```json
{
  "bridge_id": "string (required)",
  "conversation_id": "string (required)",
  "episode_id": "string (required)",
  "type": "string (required)",
  "payload": {},
  "task_id": null,
  "branch_id": "main",
  "timestamp": null
}
```

**Response:** `AppendEventResult`
```json
{
  "event_seq": 5,
  "event_count": 5,
  "artifact_count": 0
}
```

---

### `POST /v1/capture/end-episode`

Close an active episode.

**Request:**
```json
{
  "bridge_id": "string (required)",
  "conversation_id": "string (required)",
  "episode_id": "string (required)",
  "status": "success | failure | partial | abandoned",
  "metadata": {}
}
```

**Response:** `EndEpisodeResult`
```json
{
  "status": "success",
  "closed_at": "2026-05-14T12:01:00Z"
}
```

---

## Extraction Queue

### `POST /v1/extraction/queue`

Enqueue a source for LLM/VLM extraction.

**Request:**
```json
{
  "source_kind": "artifact | episode | session (required)",
  "archive_id": null,
  "conversation_id": null,
  "episode_id": null,
  "bridge_id": null,
  "session_id": null,
  "task_id": null
}
```

`source_kind=artifact|session` → provide `archive_id`.  
`source_kind=episode` → provide `conversation_id` + `episode_id`.

**Response:** `ExtractionQueueItem` (the queued row)

---

### `POST /v1/extraction/process`

Process pending extraction queue items (runs the LLM/VLM extraction step).

**Request:**
```json
{ "dry_run": false }
```

**Response:** extraction stats
```json
{
  "processed": 3,
  "skipped": 1,
  "errors": 0,
  "windows": 12,
  "artifacts": 2,
  "visual_snippets": 2,
  "candidates": 9,
  "promotion_ready": 7
}
```

---

### `GET /v1/extraction/status`

Query extraction queue status.

**Query params:** `queue_id=N` (optional) — if provided, returns detail for one row.

**Response (summary):**
```json
{
  "queue_counts": { "pending": 2, "processed": 140, "error": 3 },
  "pending_count": 2,
  "error_count": 3,
  "oldest_pending_queued_at": "2026-05-14T11:00:00"
}
```

---

## Promotion Queue

### `POST /v1/promotion/process`

Process the promotion queue (move extraction candidates into the wiki).

**Request:**
```json
{ "dry_run": false }
```

**Response:** promotion stats

---

### `GET /v1/promotion/status`

Show promotion queue health.

**Response:**
```json
{
  "promotion_ready": 4,
  "promoted": 312,
  "review_routed": 7,
  "rejected": 12,
  "last_run_at": "2026-05-14T11:55:00"
}
```

---

## Sync

### `POST /v1/sync/vault`

Synchronize vault notes into SQLite.

**Request:**
```json
{
  "mode": "incremental | full (required)",
  "paths": []
}
```

**Response:**
```json
{
  "scanned_paths": 250,
  "synced_notes": 3,
  "deleted_notes": 0,
  "parse_errors": 0
}
```

---

## Jobs

### `POST /v1/jobs/run`

Run a maintenance job.

**Request:**
```json
{
  "job_name": "string (required)",
  "dry_run": false
}
```

See [CLI reference](cli-reference.md#run-job) for valid `job_name` values.

**Response:** `JobRunResult`
```json
{
  "job_name": "embed_backfill",
  "status": "ok",
  "stats": { "archive_chunks_embedded": 5 },
  "review_items_created": []
}
```

---

## Orchestrator

High-level facade for agents. Combines retrieval + outcome recording + writeback decision into three coordinated calls.

### `POST /v1/orchestrator/begin-task`

Resolve memory context before an LLM call.

**Request:**
```json
{
  "bridge_id": "string (required)",
  "task_text": "string (required)",
  "session_id": null,
  "task_id": null,
  "task_kind": "",
  "temporality": null,
  "token_budget": null,
  "workspace": null,
  "project": null,
  "repo": null,
  "tags": [],
  "auto_retry": false,
  "max_focused_retries": 1
}
```

`session_id` and `task_id` are auto-generated if absent.

**Response:** `OrchestratorContextResult`
```json
{
  "task_id": "task-abc123",
  "session_id": "session-def456",
  "task_kind": "coding",
  "temporality": "current",
  "retrieval_state": "sufficient_current",
  "recommendation": "reuse",
  "verification_action": null,
  "memory_packet": "...",
  "auto_retry_summary": {},
  "final_pass_index": 0
}
```

---

### `POST /v1/orchestrator/mark-failure`

Record a task failure and re-resolve context with the same task_id.

**Request:**
```json
{
  "bridge_id": "string (required)",
  "session_id": "string (required)",
  "task_id": "string (required)",
  "failure_text": "string (required)",
  "task_kind": "general",
  "query_id": null,
  "retrieval_state": null
}
```

**Response:** `OrchestratorContextResult` (same shape as begin-task)

---

### `POST /v1/orchestrator/finish-task`

Record final outcome and compute writeback decision.

**Request:**
```json
{
  "bridge_id": "string (required)",
  "session_id": "string (required)",
  "task_id": "string (required)",
  "status": "success | failure | partial (required)",
  "outcome_type": "string (required)",
  "summary": "string (required)",
  "query_id": null,
  "retrieval_state": null,
  "recommendation": null,
  "useful_ids": [],
  "misleading_ids": [],
  "fresh_sources_checked": [],
  "note_type": null,
  "title": null,
  "writeback_intent": null
}
```

**Response:** `OrchestratorWritebackResult`
```json
{
  "task_id": "task-abc123",
  "session_id": "session-def456",
  "outcome_status": "success",
  "decision": "new | update | confirm | skip",
  "targets": ["concept/foo"],
  "candidates": [],
  "reason": "..."
}
```
