# MnemonicOS Daemon and Unix Socket Protocol

## Overview

The daemon is a long-running background process that executes scheduled maintenance jobs. It exposes a Unix domain socket for IPC — any process on the same host can query status or trigger jobs immediately.

```
mnemonicos-daemon      # direct entry point
mnemonicos daemon start   # equivalent via CLI
```

---

## Lifecycle

### Start

```
mnemonicos daemon start
```

- Reads config from the standard location (or `--config`)
- Forks to background, writes PID to `config.daemon.pid_path`
- Opens Unix socket at `config.daemon.socket_path`
- Runs the scheduler loop (30-second tick)
- Logs to `config.daemon.log_path`

### Stop

```
mnemonicos daemon stop
```

Requests cooperative shutdown, then escalates until the daemon and all its child processes are gone:

1. Read PID from `config.daemon.pid_path`.
2. Send `{"cmd": "shutdown"}` over the daemon socket (cooperative drain — daemon sets `shutdown_event`, joins job threads, runs registry cleanup).
3. If socket is unreachable, send `SIGTERM` to the daemon PID directly.
4. Wait until the PID is gone and the socket file is removed.
5. If still alive, send `SIGTERM` to the daemon process group.
6. If still alive, send `SIGKILL` to the daemon process group.
7. Print `daemon_stopped` only when PID, socket, and process group are all gone. Print `daemon_stop_timeout` otherwise.

`daemon_stopped` is a guarantee: no MnemonicOS daemon process, extraction subprocess, or daemon-managed provider process remains. External providers MnemonicOS did not start (e.g. the macOS `Ollama.app`) are not killed unless `ollama_managed = true` is set.

### Status

```
mnemonicos daemon status [--json]
```

Queries the socket and prints:
- Whether the daemon is alive
- Currently running job (if any)
- All scheduled jobs with cron expression and next-run UTC time
- Last job result

---

## Scheduled Jobs

Jobs run on cron schedules defined in `_DEFAULT_SCHEDULES` (`daemon_jobs.py`). Schedules can be overridden per-job in `config.daemon.schedules`.

Default schedule (all times UTC, cron format `minute hour day month weekday`):

| Job | Cron | Runs |
|-----|------|------|
| `process_extraction_queue` | `*/5 * * * *` | every 5 min |
| `process_promotion_queue` | `*/5 * * * *` | every 5 min |
| `wiki_auto_resolve_review` | `*/10 * * * *` | every 10 min |
| `wiki_derive_passive_feedback` | `*/30 * * * *` | every 30 min |
| `expire_stale_episodes` | `0 * * * *` | hourly :00 |
| `status_report` | `0 * * * *` | hourly :00 |
| `embed_backfill` | `15 * * * *` | hourly :15 |
| `wiki_embedding_neighbor_backfill` | `20 * * * *` | hourly :20 |
| `wiki_cluster_validation` | `25 * * * *` | hourly :25 |
| `wiki_pattern_discovery` | `30 * * * *` | hourly :30 |
| `wiki_graph_rebuild` | `35 * * * *` | hourly :35 |
| `wiki_lifecycle_audit` | `40 * * * *` | hourly :40 |
| `wiki_importance_scoring` | `45 * * * *` | hourly :45 |
| `rerank_training` | `47 * * * *` | hourly :47 |
| `wiki_health_report` | `50 * * * *` | hourly :50 |
| `retry_failed_extractions` | `55 * * * *` | hourly :55 |
| `wiki_raw_revisitation` | `0 2 * * *` | daily 02:00 |

`process_extraction_queue` uses expected-time queue ordering rather than strict
FIFO: sessions and episodes run before lightweight text artifacts, then unknown
artifact formats, then known-heavy PDFs/DOCX/PPTX. The extraction worker spawns
one subprocess per queue row and passes the active config path into that child;
the child must operate on the same database as the parent daemon.

When dual-lane extraction is enabled, the worker processes one quick-lane item
and one slow-lane item at the same time. `mnemonicos daemon watch` treats the
socket `job_progress` dict as the primary source of lane progress, but it also
falls back to live `processing` rows and `/tmp/mnemo_progress_<queue_id>.json`
child progress files when a lane is empty in socket progress. This prevents a
live slow PDF subprocess from being displayed as `idle` after overlapping or
stale extraction-job bookkeeping.

Override a schedule in `config/memory.toml`:

```toml
[daemon.schedules]
process_extraction_queue = "*/2 * * * *"   # run every 2 min instead
retry_failed_extractions = "0 */4 * * *"   # run every 4 hours instead
```

---

## Unix Socket Protocol

**Socket path:** `config.daemon.socket_path` (default: `~/.local/share/mnemonicos/daemon.sock`)

**Protocol:** newline-delimited JSON over a Unix stream socket.

Send a JSON object followed by `\n`. Read the response JSON line.

### Python example

```python
import socket, json

def socket_send(sock_path: str, payload: dict) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(sock_path)
        s.sendall((json.dumps(payload) + "\n").encode())
        data = b""
        while True:
            chunk = s.recv(4096)
            if not chunk:
                break
            data += chunk
            if b"\n" in data:
                break
    return json.loads(data.decode().strip())
```

### Shell example

```bash
echo '{"cmd":"status"}' | nc -U ~/.local/share/mnemonicos/daemon.sock
```

---

### Command: `status`

**Request:**
```json
{ "cmd": "status" }
```

**Response:**
```json
{
  "status": "ok",
  "service": {
    "running_job": null,
    "scheduled_jobs": [
      {
        "name": "process_extraction_queue",
        "schedule": "*/5 * * * *",
        "next_run_utc": "2026-05-14T12:05:00Z"
      }
    ],
    "last_result": {
      "job_name": "status_report",
      "status": "ok",
      "stats": { "pending_count": 0 }
    }
  },
  "health": { ... }
}
```

---

### Command: `shutdown`

Ask the daemon to initiate cooperative teardown. Equivalent to `daemon stop` sending the stop signal; use when you need the daemon to drain cleanly from inside another process.

**Request:**
```json
{ "cmd": "shutdown" }
```

**Response:**
```json
{ "status": "ok", "message": "shutdown_started" }
```

The daemon sets `shutdown_event` immediately upon receiving this command. It then joins all running job threads (up to 30 s each), calls `process_registry.stop_all()` to terminate registered extraction subprocesses and daemon-managed provider processes, closes the socket, and removes the PID file.

---

### Command: `run-job`

Ask the daemon to run a job immediately (outside its normal schedule).

**Request:**
```json
{ "cmd": "run-job", "job": "embed_backfill" }
```

Add `"wait": false` to start the job on a scheduler thread and get an immediate
acknowledgement instead of waiting for completion. This is what the intake
push trigger (`pipeline.trigger_pipeline`) sends, so an `ingest-artifact` call
never blocks on a multi-hour extraction and never falls back to running
extraction inline while the daemon is busy:

```json
{ "cmd": "run-job", "job": "process_extraction_queue", "wait": false }
```

**Response (started, `wait: false` only):**
```json
{ "status": "started", "job": "process_extraction_queue" }
```

Each socket connection is served on its own thread, so `status` and `shutdown`
keep working while a synchronous `run-job` is still executing.

**Response (success):**
```json
{
  "status": "ok",
  "job": "embed_backfill",
  "result": {
    "job_name": "embed_backfill",
    "status": "ok",
    "stats": { "archive_chunks_embedded": 3 },
    "review_items_created": []
  }
}
```

**Response (busy — another job is running):**
```json
{
  "status": "busy",
  "running_job": "process_extraction_queue"
}
```

**Response (error):**
```json
{
  "status": "error",
  "error": "unsupported job: bad_name"
}
```

---

## Daemon-Owned Providers

By default, MnemonicOS calls an already-running Ollama HTTP service at `127.0.0.1:11434` and does not start or stop it. This is correct for the macOS `Ollama.app` workflow.

To make Ollama part of the daemon lifecycle, opt in via config:

```toml
[daemon]
# If true, the daemon starts this command at startup and owns its lifetime.
# Leave false when using the macOS Ollama.app or any shared Ollama server.
ollama_managed = true
ollama_command = "/usr/local/bin/ollama serve"
```

When `ollama_managed = true`:
- The daemon starts the command with `start_new_session=True` at the top of `Daemon.run()`.
- The process is registered in `DaemonProcessRegistry` with label `"ollama"` as a process-group owner.
- `daemon stop` terminates the Ollama process group as part of its normal teardown; `daemon_stopped` will not print until Ollama has exited.

When `ollama_managed = false` (default), `daemon stop` does not touch any Ollama process, and leaving Ollama running is correct behavior.

---

## Auto-Tombstone Behavior

The extraction worker (`extraction/worker.py`) automatically tombstones items that fail on their second or later attempt:

- First failure (`attempt_count = 0` before the run): marked `error`, retryable.
- Any subsequent failure (`attempt_count >= 1` before the run): `last_error` prefixed with `tombstoned: failed after retry (attempt N): <original error>`. Tombstoned items are excluded from `retry-extraction --all-errors` and `retry_failed_extractions` daemon job.

The `retry_failed_extractions` daemon job resets non-tombstoned errors to `pending` without resetting `attempt_count`, so the next failure will tombstone them.

To manually inspect:

```
mnemonicos extraction-status --errors-only
```

---

## `job_runs` Table

Every job execution writes a row to `job_runs` with:

| Column | Description |
|--------|-------------|
| `job_name` | Name of the job |
| `mode` | `"live"` or `"dry-run"` |
| `status` | `"started"` → `"ok"` or `"error"` |
| `stats_json` | JSON dict of job-specific counters |
| `error_text` | Exception message if status = error |
| `finished_at` | UTC timestamp |

Query recent runs:

```sql
SELECT job_name, status, stats_json, finished_at
FROM job_runs
ORDER BY id DESC
LIMIT 20;
```
