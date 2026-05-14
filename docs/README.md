# MnemonicOS Documentation

## Reference Docs

| Document | Audience | Contents |
|----------|----------|----------|
| [cli-reference.md](cli-reference.md) | Humans | All `mnemonicos` subcommands — human ops and agentic/bridge commands |
| [http-api.md](http-api.md) | Agents / integrations | HTTP REST API (`/v1/*`) — request/response shapes for all endpoints |
| [python-sdk.md](python-sdk.md) | Python code / tooling | `second_brain` package API — function signatures, types, usage examples |
| [daemon-and-socket.md](daemon-and-socket.md) | Operators / power users | Daemon lifecycle, scheduled job table, Unix socket protocol, job_runs table |
| [scripts-and-hooks.md](scripts-and-hooks.md) | Bridge / hook integrators | Shell scripts, Claude Code hook wiring, Codex integration, artifact connectors |

## Integration Guides

| Document | Contents |
|----------|----------|
| [claude-code-bridge.md](claude-code-bridge.md) | Detailed Claude Code ↔ MnemonicOS hook setup |
| [codex-bridge-from-scratch.md](codex-bridge-from-scratch.md) | Codex bridge setup from scratch |

## Quick Start

**Human use (inspect and operate):**
```bash
mnemonicos extraction-status --errors-only
mnemonicos daemon status
mnemonicos review list
mnemonicos run-job retry_failed_extractions --dry-run
```

**Agent integration (orchestrator pattern):**
```bash
mnemonicos orchestrator begin-task "fix the auth bug" --bridge-id my-bridge --json
mnemonicos orchestrator finish-task --bridge-id my-bridge --session-id ... --task-id ... \
  --status success --outcome-type coding --summary "..." --json
```

**HTTP API:**
```bash
curl -s -X POST http://localhost:8000/v1/orchestrator/begin-task \
  -H 'Content-Type: application/json' \
  -d '{"bridge_id":"my-bridge","task_text":"fix the auth bug"}'
```

**Python SDK:**
```python
from second_brain import load_config, retrieve
config = load_config()
result = retrieve(config, "how to handle Ollama timeout")
```
