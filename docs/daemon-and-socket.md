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

Sends SIGTERM to the PID. The daemon finishes any running job before exiting.

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

### Command: `run-job`

Ask the daemon to run a job immediately (outside its normal schedule).

**Request:**
```json
{ "cmd": "run-job", "job": "embed_backfill" }
```

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
