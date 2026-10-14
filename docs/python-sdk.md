# MnemonicOS Python SDK

The `second_brain` package exposes a programmatic API for embedding MnemonicOS into Python code or build tooling. All functions accept an `AppConfig` as first argument.

```python
from second_brain import load_config, AppConfig
config = load_config("config/memory.toml")   # or pass None for auto-discovery
```

---

## Configuration

```python
from second_brain import load_config, AppConfig
from second_brain.config import default_config_path

config: AppConfig = load_config(path: str | None = None)
```

`load_config(None)` searches `config/memory.toml` then `config/memory.example.toml` relative to cwd.

**Key config fields:**

| Field | Type | Description |
|-------|------|-------------|
| `config.paths.db_path` | `Path` | SQLite database path |
| `config.paths.vault_root` | `Path` | Vault markdown root |
| `config.paths.workspace_root` | `Path` | Repo root |
| `config.extraction.llm_timeout_seconds` | `int \| None` | LLM timeout; `None` = infinite |
| `config.extraction.vlm_timeout_seconds` | `int \| None` | VLM timeout |
| `config.extraction.max_vlm_artifacts_per_item` | `int` | VLM calls per queue item; extra figures get skipped snippets (default 24) |
| `config.daemon.socket_path` | `Path` | Unix socket path |
| `config.daemon.schedules` | `dict[str, str]` | Cron overrides per job name |

---

## Database

```python
from second_brain import connect_db, run_migrations

conn = connect_db(config.paths.db_path)
run_migrations(conn, config.paths.workspace_root / "migrations")
```

Returns a `sqlite3.Connection` with `row_factory = sqlite3.Row`.

---

## Retrieval

```python
from second_brain import retrieve
from second_brain.retrieval import result_to_json

result = retrieve(
    config,
    query: str,
    *,
    top_k: int | None = None,
    query_type_hint: str | None = None,        # "how_to" | "decision" | "who_where_relation"
    include_archive_fallback: bool | None = None,
    temporality_override: str | None = None,
    bridge_id: str | None = None,
    caller_type_hint: str | None = None,
) -> RetrieveResult
```

**`RetrieveResult` fields:**

| Field | Type | Description |
|-------|------|-------------|
| `query_id` | `int` | ID logged in `retrieval_queries` table |
| `hits` | `list[RetrieveHit]` | Wiki hits |
| `archive_hits` | `list[RetrieveHit]` | Archive fallback hits |
| `pinned_paths` | `list[str]` | Always-included paths |
| `suppressed_by_gate_count` | `int` | Hits filtered by trust gate |
| `passed_gate_count` | `int` | Hits that passed gate |

`result_to_json(result, include_body=False, body_chars=None)` → JSON string
suitable for CLI output.

By default, retrieve JSON remains compact and exposes `summary`, `excerpt`, and
`body_path`. Pass `include_body=True` to hydrate returned wiki hits with their
note body text for inspection; `body_chars` caps each hydrated body.

```python
payload = result_to_json(result, include_body=True, body_chars=4000)
```

---

## Ingest

```python
from second_brain import ingest_session
from second_brain.ingest import ingest_artifact, reprocess_artifact, reprocess_failed_artifacts

result = ingest_session(
    config,
    bridge_id: str,
    session_id: str,
    content: str,
    tags: list[str] = [],
    source_refs: list[str] = [],
) -> IngestSessionResult

result = ingest_artifact(
    config,
    path: str,                              # absolute path to file
    tags: list[str] = [],
    attachments: list[str] = [],            # companion files or dirs
    connector_config: ConnectorConfig = None,  # override per-call; see ConnectorConfig below
) -> IngestArtifactResult

result = reprocess_artifact(config, archive_id: str) -> IngestArtifactResult

results = reprocess_failed_artifacts(config) -> list[IngestArtifactResult]
```

**`IngestArtifactResult` fields:**

| Field | Type |
|-------|------|
| `archive_id` | `str` |
| `raw_path` | `str` |
| `manifest_path` | `str` |
| `ingest_status` | `str` (`"accepted"` / `"accepted_with_warnings"` / `"failed_extraction"` / `"manual_fix_needed"`) |
| `source_format` | `str` |
| `figure_count` | `int` |
| `warnings` | `list[str]` |
| `failure_reason` | `str \| None` |
| `needs_visual_pass` | `bool` |
| `image_only_pdf` | `bool` |
| `duplicate_state` | `str` |

**`ConnectorConfig` fields** (from `second_brain.connectors.base_connector`):

| Field | Default | Description |
|-------|---------|-------------|
| `figures_enabled` | `True` | Extract and return figures from document |
| `skip_text_extraction` | `False` | Skip text extraction; return `accepted` immediately. Used by lazy PDF ingest. The worker always runs with `skip_text_extraction=False`. |
| `max_in_memory_figure_bytes` | `None` | Stop rendering page images when a single page exceeds this byte threshold |
| `persist_extracted_figures` | `False` | Write figure payloads to disk rather than keeping in memory |

To force full (non-lazy) extraction for a single call, pass an explicit config:

```python
from second_brain.connectors.base_connector import ConnectorConfig

result = ingest_artifact(
    config,
    path="/path/to/scan.pdf",
    connector_config=ConnectorConfig(skip_text_extraction=False),
)
```

---

## Raw Capture

```python
from second_brain.session_capture import begin_episode, append_event, end_episode
from second_brain.models import (
    BeginEpisodeRequest, BeginEpisodeResult,
    AppendEventRequest, AppendEventResult,
    EndEpisodeRequest, EndEpisodeResult,
)

result: BeginEpisodeResult = begin_episode(config, BeginEpisodeRequest(
    bridge_id="my-bridge",
    conversation_id=None,      # auto-generated if absent
    episode_id=None,
    parent_conversation_id=None,
    related_conversation_ids=[],
    task_id=None,
    cwd=None, repo=None, branch=None,
    model=None, agent_runtime=None,
    platform_thread_id=None,
    metadata={},
))

result: AppendEventResult = append_event(config, AppendEventRequest(
    bridge_id="my-bridge",
    conversation_id="...",
    episode_id="...",
    event_type="user_message",
    payload={"content": "hello"},
    task_id=None,
    branch_id="main",
    timestamp=None,
))

result: EndEpisodeResult = end_episode(config, EndEpisodeRequest(
    bridge_id="my-bridge",
    conversation_id="...",
    episode_id="...",
    status="success",
    metadata={},
))
```

---

## Extraction Queue

```python
from second_brain.extraction import (
    enqueue_extraction,
    extraction_status,
    process_extraction_queue,
)

item = enqueue_extraction(
    config,
    source_kind="episode",          # "artifact" | "episode" | "session"
    archive_id=None,                # required for artifact/session
    conversation_id="...",          # required for episode
    episode_id="...",
    bridge_id=None,
    session_id=None,
    task_id=None,
    content_hash=None,
) -> ExtractionQueueItem

status = extraction_status(
    config,
    queue_id: int | None = None,
    errors_only: bool = False,
    limit: int = 20,
) -> dict

result = process_extraction_queue(
    config,
    dry_run: bool = False,
    limit: int | None = None,
) -> ExtractionProcessResult
```

---

## Orchestrator

High-level facade. Preferred integration pattern for agents.

```python
from second_brain.orchestrator import (
    begin_task, mark_failure, finish_task,
    OrchestratorTaskRequest,
    OrchestratorFailureRequest,
    OrchestratorOutcomeRequest,
    OrchestratorContextResult,
    OrchestratorWritebackResult,
)

ctx: OrchestratorContextResult = begin_task(config, OrchestratorTaskRequest(
    bridge_id="my-bridge",
    task_text="Fix the auth bug",
    session_id=None,            # auto-generated
    task_id=None,               # auto-generated
    task_kind="coding",
    temporality="current",
    token_budget=4000,
    workspace=None, project=None, repo=None,
    tags=[],
    auto_retry=False,
    max_focused_retries=1,
))
# ctx.task_id, ctx.session_id — use for subsequent calls
# ctx.memory_packet — inject into LLM system prompt

ctx2: OrchestratorContextResult = mark_failure(config, OrchestratorFailureRequest(
    bridge_id="my-bridge",
    session_id=ctx.session_id,
    task_id=ctx.task_id,
    failure_text="ImportError: module not found",
    task_kind="coding",
    query_id=ctx.query_id,
    retrieval_state=ctx.retrieval_state,
))

wb: OrchestratorWritebackResult = finish_task(config, OrchestratorOutcomeRequest(
    bridge_id="my-bridge",
    session_id=ctx.session_id,
    task_id=ctx.task_id,
    status="success",
    outcome_type="coding",
    summary="Fixed ImportError by adding missing __init__.py",
    query_id=ctx.query_id,
    retrieval_state=ctx.retrieval_state,
    useful_ids=[],
    misleading_ids=[],
    fresh_sources_checked=[],
    note_type=None,
    title=None,
    writeback_intent=None,
))
# wb.decision: "new" | "update" | "confirm" | "skip"
```

---

## Runtime Operations (Lower Level)

```python
from second_brain import resolve_task_context, record_outcome, decide_writeback_runtime
from second_brain.models import (
    ResolveTaskContextRequest,
    RecordOutcomeRequest,
    DecideWritebackRequest,
)

ctx = resolve_task_context(config, ResolveTaskContextRequest(
    bridge_id="...", session_id="...", task_id="...",
    task_text="...", task_kind="...",
))

outcome = record_outcome(config, RecordOutcomeRequest(
    bridge_id="...", session_id="...", task_id="...",
    outcome_type="...", status="success", summary="...",
))

decision = decide_writeback_runtime(config, DecideWritebackRequest(
    bridge_id="...", session_id="...", task_id="...",
    note_type="...", title="...", summary_text="...",
))
```

---

## Jobs

```python
from second_brain import run_job
from second_brain.models import JobRunResult

result: JobRunResult = run_job(
    config,
    job_name: str,
    dry_run: bool = False,
)
# result.job_name, result.status, result.stats (dict), result.review_items_created
```

Valid job names: see [CLI reference](cli-reference.md#run-job).

---

## Vault Sync

```python
from second_brain import sync_vault
from second_brain.sync import SyncResult

result: SyncResult = sync_vault(
    config,
    mode: str = "incremental",     # "incremental" | "full"
    selected_paths: list[str] | None = None,
)
# result.scanned_paths, result.synced_notes, result.deleted_notes, result.parse_errors
```

---

## Top-Level Exports (`second_brain.__init__`)

```python
from second_brain import (
    AppConfig,
    connect_db,
    run_migrations,
    ingest_session,
    load_config,
    retrieve,
    record_outcome,
    resolve_task_context,
    decide_writeback_runtime,
    run_job,
    sync_vault,
)
```
