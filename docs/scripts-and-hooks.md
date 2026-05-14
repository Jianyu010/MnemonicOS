# MnemonicOS Scripts and Hook Integration

All scripts live in `scripts/`. Most are thin wrappers around the `mnemonicos` CLI or HTTP API, designed to be wired as editor/agent hooks.

---

## Hook Integration Scripts

These scripts are designed to be called by Claude Code hooks or Codex hooks at specific lifecycle points.

### `mnemonic_hook_preflight.sh`

**When:** Claude Code `UserPromptSubmit` hook — fires before each user prompt is processed.

**What it does:** Calls `mnemonicos orchestrator begin-task` (or `scripts/mnemonic_context.sh`) to inject memory context into the prompt. The resulting `## Memory Context` block is prepended to the prompt so Claude sees relevant notes before answering.

**Usage:**
```bash
# In Claude Code hooks config (settings.json):
# "hooks": { "UserPromptSubmit": ["scripts/mnemonic_hook_preflight.sh"] }
```

---

### `mnemonic_context.sh`

**When:** Called by preflight hook, or directly when you want retrieval context.

**Usage:**
```bash
scripts/mnemonic_context.sh "query text" \
  [--query-type-hint how_to|decision|who_where_relation] \
  [--top-k 10]
```

Outputs a formatted `## Memory Context` block with `current_hits`, `stale_hits`, `contested_hits` and the full memory packet. Used by CLAUDE.md instructions to inject context.

---

### `mnemonic_postflight.sh`

**When:** Claude Code `Stop` hook — fires when a Claude session ends.

**What it does:** Reads `/tmp/mnemo_pending_summary.md` (if present) and ingests it into MnemonicOS as a session writeback. Cleans up the temp file.

**Usage:**
```bash
scripts/mnemonic_postflight.sh --agent claude \
  --slug claude-YYYY-MM-DD-HHMMSS \
  --summary-file /tmp/mnemo_pending_summary.md
```

Also callable directly if the Stop hook is unavailable.

---

### `mnemonic_stop_hook.sh`

**When:** Claude Code `Stop` hook (alternative to `mnemonic_postflight.sh`).

Lightweight wrapper; delegates to `mnemonic_postflight.sh`.

---

### `mnemonic_hook_post_tool.sh`

**When:** Claude Code `PostToolUse` hook — fires after each tool call.

**What it does:** Light signal capture for passive feedback (e.g., recording which notes were accessed).

---

### `mnemonic_precompact_dump.sh`

**When:** Before Claude Code compacts the context window.

**What it does:** Dumps current session state / open episodeinfo so the compacted context retains key identifiers.

---

## Codex Integration Scripts

### `mnemonic_codex_hook_preflight.sh`

Codex equivalent of `mnemonic_hook_preflight.sh`. Wired as Codex pre-task hook.

---

### `mnemonic_codex_notify_bridge.py`

**Bridge script** for Codex → MnemonicOS event streaming.

Reads JSON event objects from stdin (Codex message stream) and POSTs them to the MnemonicOS HTTP API (`/v1/capture/append-event`). Creates/manages episodes automatically.

**Usage:**
```bash
# Typically invoked by codex_notify_bridge.sh wrapper
cat codex_events.jsonl | python scripts/mnemonic_codex_notify_bridge.py \
  --bridge-id codex-unit-hook \
  --config config/memory.toml
```

---

### `mnemonic_codex_stop_hook.sh`

Codex session stop hook — ends the active capture episode and triggers writeback.

---

### `codex_hook_canary.sh` *(deprecated)*

Old Codex hook health check. Replaced by `mnemonic_codex_hook_preflight.sh`.

### `codex_notify_bridge.sh` *(deprecated)*

Old wrapper for `mnemonic_codex_notify_bridge.py`. Superseded by direct invocation pattern.

---

## Heuristic Gate Scripts

These scripts return exit code 0 (proceed) or non-zero (skip) based on lightweight heuristics. Used to avoid expensive operations when not warranted.

### `mnemonic_should_retrieve.sh`

Gate: should we run retrieval for this prompt?

```bash
scripts/mnemonic_should_retrieve.sh "prompt text"
# exit 0 = yes, run retrieval
# exit 1 = no, skip
```

---

### `mnemonic_should_writeback.sh`

Gate: should we ingest this session summary?

```bash
scripts/mnemonic_should_writeback.sh \
  --mode failure|success \
  --summary-file /tmp/mnemo_pending_summary.md
# exit 0 = yes, ingest
# exit 1 = skip
```

---

### `mnemonic_should_upsert.sh`

Gate: should this summary create a new note, update an existing one, or skip?

```bash
scripts/mnemonic_should_upsert.sh \
  --summary-file /tmp/mnemo_pending_summary.md \
  --json
# Outputs JSON: { "intent": "new"|"update"|"confirm"|"skip", "target_ids": [...] }
```

---

## Utility Scripts

### `mnemonic_consolidate_raw.py`

Consolidate fragmented raw session data (e.g., merge multiple partial episode files).

```bash
python scripts/mnemonic_consolidate_raw.py [--config PATH]
```

---

### `mnemonic_daemon.sh`

Shell wrapper for daemon lifecycle management. Equivalent to `mnemonicos daemon {start|stop|status}`.

```bash
scripts/mnemonic_daemon.sh start|stop|status
```

---

### `install_mnemonicos.sh`

Install the `second_brain` Python package (runs `pip install -e .`).

```bash
bash scripts/install_mnemonicos.sh
```

---

### `install_codex_skill.sh`

Install the Codex skill integration (copies hook scripts to the Codex hooks directory).

---

### `run_evals.py`

Run the MnemonicOS evaluation suite.

```bash
python scripts/run_evals.py [--config PATH]
```

---

## Wiring Claude Code Hooks

In `~/.claude/settings.json`:

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/ai_memory/scripts/mnemonic_hook_preflight.sh"
          }
        ]
      }
    ],
    "Stop": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "bash /path/to/ai_memory/scripts/mnemonic_stop_hook.sh"
          }
        ]
      }
    ]
  }
}
```

See `docs/claude-code-bridge.md` for detailed Claude Code integration setup.

---

## Artifact Connectors

Source format connectors live in `src/second_brain/connectors/`:

| Connector | Formats |
|-----------|---------|
| `pdf_connector.py` | `.pdf` |
| `docx_connector.py` | `.docx` |
| `pptx_connector.py` | `.pptx` |
| `html_connector.py` | `.html`, `.htm` |
| `passthrough_connector.py` | any other format (identity) |

Custom connectors can be registered via `connector_registry.py`. Connectors implement `BaseConnector` from `connectors/base_connector.py`.
