# Claude Code Bridge — Implementation Guide

How to wire Claude Code into MnemonicOS so every session is captured, indexed,
and available for future retrieval. Covers the bridge from scratch. A new user
who has never touched the codebase can follow this and end up with a working
integration.

---

## Boundary: What You Own vs What MnemonicOS Owns

```
┌─────────────────────────────────────────────────────────────────────┐
│  Claude Code (your IDE / CLI)                                       │
│                                                                     │
│  UserPromptSubmit hook ────┐                                        │
│  PostToolUse hook          │                                        │
│  PostToolUseFailure hook   ├──► YOUR BRIDGE SCRIPTS (scripts/)      │
│  PreCompact hook           │         │         │                    │
│  Stop hook ────────────────┘         │         │                    │
│                                    │         │                      │
│ ════════════════════════ BOUNDARY ═╪═════════╪════════════════════ │
│                                    ▼         ▼                      │
│  MnemonicOS CLI  ◄──────  capture append-event                      │
│  (mnemonicos / python3 -m second_brain)                             │
│      │                                                              │
│      ├── orchestrator begin-task   (retrieval + context inject)     │
│      ├── capture begin-episode     (episode lifecycle)              │
│      ├── capture append-event      (raw event stream)              │
│      ├── capture end-episode       (close episode)                  │
│      └── orchestrator finish-task  (writeback decision)             │
└─────────────────────────────────────────────────────────────────────┘
```

**Your bridge owns:** hook scripts, settings.json wiring, payload construction,
state file management, transcript scraping.

**MnemonicOS owns:** episode storage, event schema validation, payload
externalization, retrieval, extraction queue, promotion pipeline, writeback
decisions. You call its CLI — you do not touch its internals.

The bridge API surface is exactly five CLI subcommands:

| Command | When to call |
|---|---|
| `capture begin-episode` | Turn starts (UserPromptSubmit) |
| `capture append-event` | Any event during the turn |
| `capture end-episode` | Turn ends (Stop) |
| `orchestrator begin-task` | UserPromptSubmit — get retrieval context |
| `orchestrator finish-task` | Stop — record outcome and trigger writeback |

---

## Prerequisites

- MnemonicOS installed and `config/memory.toml` present (or `config/memory.example.toml`)
- Claude Code CLI (`claude`) installed and `settings.json` accessible at
  `~/.claude/settings.json`
- Python 3.9+

Verify the CLI works:

```bash
cd /path/to/mnemonicos
env PYTHONPATH=src python3 -m second_brain capture --help
# or if installed as a package:
mnemonicos capture --help
```

---

## Concepts

### Episode

One user turn = one episode. The lifecycle is:

```
UserPromptSubmit  →  begin-episode  →  [events...]  →  end-episode  ←  Stop
```

Each episode belongs to a **conversation** (the Claude Code session ID). Multiple
episodes within one conversation form a thread.

### State File

The bridge keeps a small JSON file per Claude Code session at
`/tmp/mnemo_hook_state_<sha256(session_id)[:16]>` to pass `conversation_id` and
`episode_id` from the preflight hook to every downstream hook in the same turn.
Every hook derives the same path from the `session_id` in its own stdin JSON, so
two concurrent sessions never end each other's episodes. The Stop hook reads it
and then deletes it. (A legacy shared `/tmp/mnemo_hook_state` is still read as a
fallback. Set `STATE_FILE` explicitly to override, e.g. in tests.)

```json
{
  "conversation_id": "a0b5a353-...",
  "episode_id": "2026-05-13T163809+0800",
  "session_id": "a0b5a353-...",
  "task_id": "task-43fc1c095370"
}
```

### Bridge ID

Every capture call carries `--bridge-id claude`. This namespaces raw captures
so multiple agent runtimes (Claude Code, Codex, Cursor, etc.) can share one
MnemonicOS instance without collision.

---

## Hook Architecture

Five hooks cover the full turn lifecycle:

| Hook event | Trigger | Script | What it captures |
|---|---|---|---|
| `UserPromptSubmit` | User sends a message | `mnemonic_hook_preflight.sh` | `user_message`, `retrieval_packet`, episode begin |
| `PostToolUse` (all) | Tool call **succeeds** (CC 2.1.143+) | `mnemonic_hook_post_tool.sh` | `tool_call`, `tool_result` |
| `PostToolUseFailure` (all) | Tool call **fails** (CC 2.1.143+) | `mnemonic_hook_post_tool_failure.sh` | `tool_call`, `tool_result` (is_error=true), `failure` |
| `PostToolUse` (Write/Edit) | Write or Edit tool completes | `mnemonic_hook_file_artifact.sh` | `file_changed`, `artifact_created` |
| `Stop` | Claude finishes responding | `mnemonic_stop_hook.sh` | `assistant_message` (scraped from transcript), episode end, writeback |
| `PreCompact` | Context window near limit | `mnemonic_precompact_dump.sh` | Copies pending summary to `brain/raw/sessions/` before compaction discards conversation context |

---

## Skill Layer

Hooks inject context **passively** — the agent receives the `## Memory Context`
block in its prompt but nothing enforces that it reads and acts on it correctly.
The skill layer closes this gap.

### How it works

Claude Code auto-discovers skills from `.claude/skills/*/SKILL.md` in both the
project directory and `~/.claude/skills/` globally. This is the `skills-dir`
mechanism (CC 2.1.143+). No `settings.json` registration is needed.

File: `.claude/skills/mnemonicos-bridge/SKILL.md` (in this repo)

When the `superpowers` plugin's `using-superpowers` skill fires (before every
response), it checks the available-skills list. Because `mnemonicos-bridge` is
registered there, it is auto-triggered on any non-trivial task — without the
user typing a `/skill` command.

### What the skill enforces

| Step | Action |
|---|---|
| 1 | Read hook-injected `## Memory Context` and act on `retrieval_state` |
| 2 | Run `mnemonic_context.sh` manually if hook missed or state insufficient |
| 3 | Mandatory re-query after first meaningful failure |
| 4 | Dedup-aware writeback: `confirm > update > new` |

### Installation

The project-level skill at `.claude/skills/mnemonicos-bridge/SKILL.md` activates
when working inside this repo. For activation from any project (since
`~/.claude/CLAUDE.md` references MnemonicOS globally), also copy it to the
global location:

```bash
mkdir -p ~/.claude/skills/mnemonicos-bridge
cp .claude/skills/mnemonicos-bridge/SKILL.md ~/.claude/skills/mnemonicos-bridge/
```

---

## Step 1 — settings.json Wiring

Add to `~/.claude/settings.json` (global) or `.claude/settings.json` (project):

```json
{
  "hooks": {
    "UserPromptSubmit": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "cd /path/to/mnemonicos && scripts/mnemonic_hook_preflight.sh"
          }
        ]
      }
    ],
    "PostToolUse": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "cd /path/to/mnemonicos && scripts/mnemonic_hook_post_tool.sh"
          }
        ]
      },
      {
        "matcher": "Write|Edit",
        "hooks": [
          {
            "type": "command",
            "command": "cd /path/to/mnemonicos && scripts/mnemonic_hook_file_artifact.sh"
          }
        ]
      }
    ],
    "PostToolUseFailure": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "cd /path/to/mnemonicos && scripts/mnemonic_hook_post_tool_failure.sh"
          }
        ]
      }
    ],
    "PreCompact": [
      {
        "matcher": "",
        "hooks": [
          {
            "type": "command",
            "command": "cd /path/to/mnemonicos && scripts/mnemonic_precompact_dump.sh"
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
            "command": "cd /path/to/mnemonicos && scripts/mnemonic_stop_hook.sh"
          }
        ]
      }
    ]
  }
}
```

`matcher: ""` means the hook fires for all tool names. `matcher: "Write|Edit"`
is a substring match — only fires when tool name contains "Write" or "Edit".

All hooks must **exit 0** even on failure. Claude Code blocks the user turn
if any hook exits non-zero.

---

## Step 2 — Hook Input Schema

Claude Code passes JSON on **stdin** to each hook. The fields vary by hook type:

### UserPromptSubmit stdin

```json
{
  "prompt": "the user's message text",
  "session_id": "uuid-of-the-claude-session"
}
```

### PostToolUse stdin (CC 2.1.143+: success only)

```json
{
  "session_id": "uuid",
  "tool_name": "Bash",
  "tool_input": { "command": "ls" },
  "tool_response": { "success": true }
}
```

`tool_response` shape varies by tool. `is_error` is **not** present in
`PostToolUse` — failed tool calls fire `PostToolUseFailure` instead.

### PostToolUseFailure stdin (CC 2.1.143+)

```json
{
  "session_id": "uuid",
  "tool_name": "Bash",
  "tool_use_id": "toolu_...",
  "tool_input": { "command": "ls /nonexistent" },
  "error": "No such file or directory",
  "error_type": "bash_error",
  "is_interrupt": false,
  "is_timeout": false
}
```

No `tool_response` field — error info is in top-level `error`, `error_type`,
`is_interrupt`, and `is_timeout`.

### Stop stdin

```json
{
  "session_id": "uuid",
  "stop_hook_active": false
}
```

`stop_hook_active` is `true` when the hook fires inside a loop guard (e.g.
the turn is being held open by another Stop hook). **Exit 0 immediately** when
this is `true` — doing real work while `stop_hook_active=true` risks hitting the
`CLAUDE_CODE_STOP_HOOK_BLOCK_CAP` limit and causing `stop_hook_prevented`.

---

## Step 3 — Preflight Hook (UserPromptSubmit)

`scripts/mnemonic_hook_preflight.sh` does four things per turn:

1. **Begin episode** — calls `capture begin-episode` to open a raw episode for
   this turn. Returns `conversation_id` and `episode_id`.
2. **Emit `user_message`** — appends the prompt text as the first event.
3. **Run orchestrator** — calls `orchestrator begin-task` with the prompt text.
   Agent hooks pass `--auto-retry --max-focused-retries 1` by default, so Stage
   7 may emit a focused follow-up query and the hook-side orchestrator may run
   one bounded second retrieval pass. Returns `context_markdown` (retrieved
   memory), `retrieval_state`, and retry trace fields when a focused pass ran.
4. **Emit `retrieval_packet`** — records which retrieval state and context were
   injected, so future analysis can correlate retrieval quality with outcomes.
5. **Return context to Claude** — if context is non-empty and state is not
   `insufficient_no_hit`, prints `{"context": "..."}` to stdout so Claude Code
   prepends it to the prompt.

Skeleton (error handling omitted for clarity):

```bash
HOOK_JSON=$(cat)
PROMPT=$(echo "$HOOK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('prompt',''))")
SESSION_ID=$(echo "$HOOK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))")

# 1. Begin episode
CAPTURE=$(mnemonicos capture begin-episode \
  --bridge-id claude \
  --conversation-id "$SESSION_ID" \
  --cwd "$PWD" \
  --agent-runtime claude-code \
  --json)
CONVERSATION_ID=$(echo "$CAPTURE" | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
EPISODE_ID=$(echo "$CAPTURE" | python3 -c "import sys,json; print(json.load(sys.stdin)['episode_id'])")

# 2. Emit user_message
mnemonicos capture append-event \
  --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" \
  --episode-id "$EPISODE_ID" \
  --type user_message \
  --payload-json "{\"content\": \"$PROMPT\"}" \
  --json >/dev/null

# 3. Run orchestrator (retrieval)
RESULT=$(mnemonicos orchestrator begin-task "$PROMPT" \
  --bridge-id claude --session-id "$SESSION_ID" \
  --auto-retry --max-focused-retries 1 --json)
TASK_ID=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('task_id',''))")
CONTEXT=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('context_markdown',''))")
STATE=$(echo "$RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin).get('retrieval_state',''))")

# 4. Emit retrieval_packet
mnemonicos capture append-event \
  --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" \
  --episode-id "$EPISODE_ID" \
  --type retrieval_packet \
  --payload-json "{\"state\": \"$STATE\", \"context_markdown\": \"$CONTEXT\"}" \
  --json >/dev/null

# Persist state for downstream hooks
echo "{\"conversation_id\":\"$CONVERSATION_ID\",\"episode_id\":\"$EPISODE_ID\",\"task_id\":\"$TASK_ID\",\"session_id\":\"$SESSION_ID\"}" \
  > /tmp/mnemo_hook_state

# 5. Return context (or exit silently)
[[ -z "$CONTEXT" || "$STATE" == "insufficient_no_hit" ]] && exit 0
python3 -c "import json,sys; print(json.dumps({'context':sys.argv[1]}))" "$CONTEXT"
exit 0
```

The real script (`scripts/mnemonic_hook_preflight.sh`) adds error guards, a
disable flag, and proper JSON escaping via `python3 -c` env-var injection.

---

## Step 4 — PostToolUse / PostToolUseFailure: Tool Capture Hooks

**CC 2.1.143+** split tool capture into two hooks:

| Hook | Script | Fires when |
|---|---|---|
| `PostToolUse` | `mnemonic_hook_post_tool.sh` | tool call **succeeds** |
| `PostToolUseFailure` | `mnemonic_hook_post_tool_failure.sh` | tool call **fails** |

`mnemonic_hook_post_tool.sh` reads the state file and emits two events:

- `tool_call` — tool name + input
- `tool_result` — tool name + response (`is_error: false`)

`mnemonic_hook_post_tool_failure.sh` emits three events:

- `tool_call` — tool name + input
- `tool_result` — tool name + error info (`is_error: true`)
- `failure` — tool name + error message + error_type

Key design decisions:

**Payload truncation.** Tool results (especially Bash) can be arbitrarily large.
The hook truncates string values above 256 KB before sending (dict values above
128 KB are truncated individually). MnemonicOS then externalizes payloads above
its own 32 KB threshold to artifact files. The inline event record contains only
a hash and path reference for large payloads. Truncated results carry
`"_truncated": true` in the result payload.

**No stdin re-read after cat.** The hook reads stdin into `HOOK_JSON` once at
the top, then passes it via environment variable to the Python subprocess.
This avoids the "stdin already consumed" problem.

**Temp file for Python output.** Rather than command substitution around a
heredoc (which has tricky quoting semantics), the Python subprocess writes its
result to a temp file. The shell then reads named fields from it.

Success hook skeleton (`mnemonic_hook_post_tool.sh`):

```bash
HOOK_JSON=$(cat)
[[ -f /tmp/mnemo_hook_state ]] || exit 0  # no active episode — skip

PAYLOADS_FILE=$(mktemp /tmp/mnemo_tool_payloads.XXXXXX.json)
HOOK_JSON="$HOOK_JSON" python3 - "$PAYLOADS_FILE" <<'PY'
import json, os, sys
hook = json.loads(os.environ["HOOK_JSON"])
tool_name = hook.get("tool_name", "")
tool_input = hook.get("tool_input", {})
tool_response = hook.get("tool_response", {})
# PostToolUse fires on success only — is_error never true here

# ... truncate large values ...

json.dump({"call": {"tool_name": tool_name, "tool_input": tool_input},
           "result": {"tool_name": tool_name, "is_error": False,
                      "tool_response": tool_response}},
          open(sys.argv[1], "w"))
PY

CONVERSATION_ID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('conversation_id',''))" /tmp/mnemo_hook_state)
EPISODE_ID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('episode_id',''))" /tmp/mnemo_hook_state)
CALL_PAYLOAD=$(python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))['call']))" "$PAYLOADS_FILE")
RESULT_PAYLOAD=$(python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))['result']))" "$PAYLOADS_FILE")
rm -f "$PAYLOADS_FILE"

mnemonicos capture append-event --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" --episode-id "$EPISODE_ID" \
  --type tool_call --payload-json "$CALL_PAYLOAD" --json >/dev/null
mnemonicos capture append-event --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" --episode-id "$EPISODE_ID" \
  --type tool_result --payload-json "$RESULT_PAYLOAD" --json >/dev/null
exit 0
```

Failure hook skeleton (`mnemonic_hook_post_tool_failure.sh`):

```bash
HOOK_JSON=$(cat)
[[ -f /tmp/mnemo_hook_state ]] || exit 0

PAYLOADS_FILE=$(mktemp /tmp/mnemo_tool_failure_payloads.XXXXXX.json)
HOOK_JSON="$HOOK_JSON" python3 - "$PAYLOADS_FILE" <<'PY'
import json, os, sys
hook = json.loads(os.environ["HOOK_JSON"])
tool_name  = hook.get("tool_name", "")
tool_input = hook.get("tool_input", {})
error_msg  = str(hook.get("error") or "")[:512]
error_type = hook.get("error_type", "")
is_interrupt = bool(hook.get("is_interrupt", False))
is_timeout   = bool(hook.get("is_timeout", False))

# ... truncate large values ...

json.dump({
    "call":    {"tool_name": tool_name, "tool_input": tool_input},
    "result":  {"tool_name": tool_name, "is_error": True,
                "error": error_msg, "error_type": error_type,
                "is_interrupt": is_interrupt, "is_timeout": is_timeout},
    "failure": {"tool_name": tool_name, "error_message": error_msg,
                "error_type": error_type, "source": "tool_error"},
}, open(sys.argv[1], "w"))
PY

CONVERSATION_ID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('conversation_id',''))" /tmp/mnemo_hook_state)
EPISODE_ID=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('episode_id',''))" /tmp/mnemo_hook_state)
CALL_PAYLOAD=$(python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))['call']))" "$PAYLOADS_FILE")
RESULT_PAYLOAD=$(python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))['result']))" "$PAYLOADS_FILE")
FAILURE_PAYLOAD=$(python3 -c "import json,sys; print(json.dumps(json.load(open(sys.argv[1]))['failure']))" "$PAYLOADS_FILE")
rm -f "$PAYLOADS_FILE"

mnemonicos capture append-event --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" --episode-id "$EPISODE_ID" \
  --type tool_call --payload-json "$CALL_PAYLOAD" --json >/dev/null
mnemonicos capture append-event --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" --episode-id "$EPISODE_ID" \
  --type tool_result --payload-json "$RESULT_PAYLOAD" --json >/dev/null
mnemonicos capture append-event --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" --episode-id "$EPISODE_ID" \
  --type failure --payload-json "$FAILURE_PAYLOAD" --json >/dev/null
exit 0
```

---

## Step 5 — PostToolUse: File Artifact Hook

`scripts/mnemonic_hook_file_artifact.sh` fires **only for Write and Edit tools**.
It emits:

- `artifact_created` — when tool is `Write` (new file)
- `file_changed` — when tool is `Edit` (modification)

Both events include `file_path`, `tool`, `size_bytes`, and inline `content` for
recognized text extensions (`.md`, `.txt`, `.py`, `.js`, `.ts`, `.json`, `.yaml`,
`.yml`, `.toml`, `.sh`, `.sql`, `.csv`, `.html`, `.css`). Binary files and
unrecognized extensions omit `content`. MnemonicOS externalizes payloads above
32 KB to artifact files.

This hook fires **in addition to** the tool capture hook for Write/Edit, so the
episode record contains both the artifact content and the tool invocation
semantics.

Skeleton:

```bash
HOOK_JSON=$(cat)
TOOL_NAME=$(echo "$HOOK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_name',''))")
[[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]] || exit 0

FILE_PATH=$(echo "$HOOK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tool_input',{}).get('file_path',''))")

EVENT_TYPE="file_changed"
[[ "$TOOL_NAME" == "Write" ]] && EVENT_TYPE="artifact_created"

PAYLOAD=$(python3 -c "
import json, os, sys
p = {'file_path': sys.argv[1], 'tool': sys.argv[2]}
try:
    p['size_bytes'] = os.stat(sys.argv[1]).st_size
    p['content'] = open(sys.argv[1], errors='replace').read()
except Exception:
    pass
print(json.dumps(p))
" "$FILE_PATH" "$TOOL_NAME")

mnemonicos capture append-event --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" --episode-id "$EPISODE_ID" \
  --type "$EVENT_TYPE" --payload-json "$PAYLOAD" --json >/dev/null
exit 0
```

---

## Step 6 — Stop Hook

`scripts/mnemonic_stop_hook.sh` fires after Claude finishes each response. It:

1. **Scrapes transcript** — finds the Claude Code `.jsonl` transcript file for
   the current session and extracts all `assistant` entries timestamped after the
   episode started. Emits each as an `assistant_message` event.
2. **Ends episode** — calls `capture end-episode --status success`.
3. **Ingests pending summaries** — every file under `/tmp/mnemo_pending/`
   (written by `scripts/mnemonic_pending_summary.sh` during the turn) whose
   `agent:` header is `claude`, plus the legacy `/tmp/mnemo_pending_summary.md`
   when its header is `claude` or missing. With orchestrator state it calls
   `orchestrator finish-task`; without a `task_id` (begin-task failed, hook
   timed out) it falls back to `mnemonic_postflight.sh` so the summary is still
   archived. Files owned by other agents (e.g. `agent: codex`) are left alone.
4. **Cleans up** — deletes each summary only after the CLI accepted it, then
   removes the state file. A summary whose ingestion failed stays in place and
   is retried on the next turn.

Why scrape the transcript rather than capturing assistant text in a PostToolUse
hook? Claude Code does not expose an `AssistantResponse` hook. The transcript
file is the only reliable source of assistant content.

Transcript file location:

```
~/.claude/projects/<project-slug>/<session_id>.jsonl
```

Each JSONL line with `"type": "assistant"` contains:

```json
{
  "type": "assistant",
  "sessionId": "uuid",
  "uuid": "msg-uuid",
  "requestId": "req-uuid",
  "timestamp": "2026-05-13T08:38:09.000Z",
  "message": {
    "content": [
      {"type": "text", "text": "..."},
      {"type": "tool_use", "id": "...", "name": "Bash", "input": {...}},
      {"type": "thinking", "thinking": "..."}
    ]
  }
}
```

The Stop hook reads only entries where:
- `sessionId` matches the current session
- `timestamp` >= episode `started_at` (filters prior turns in same session)

Skeleton:

```bash
HOOK_JSON=$(cat)
SESSION_ID=$(echo "$HOOK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('session_id',''))")

# Load state
STATE=$(python3 -c "import json; print(json.dumps(json.load(open('/tmp/mnemo_hook_state'))))")
CONVERSATION_ID=$(echo "$STATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['conversation_id'])")
EPISODE_ID=$(echo "$STATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['episode_id'])")
TASK_ID=$(echo "$STATE" | python3 -c "import sys,json; print(json.load(sys.stdin)['task_id'])")

# Scrape transcript
TRANSCRIPT=$(find ~/.claude/projects -name "${SESSION_ID}.jsonl" | head -1)
MANIFEST="brain/raw/sessions/$CONVERSATION_ID/episodes/$EPISODE_ID/manifest.json"

python3 - "$SESSION_ID" "$TRANSCRIPT" "$MANIFEST" /tmp/mnemo_payloads.jsonl <<'PY'
import json, sys
from datetime import datetime
session_id, transcript, manifest_path, out = sys.argv[1:5]
started_at = datetime.fromisoformat(json.load(open(manifest_path))["started_at"].replace("Z","+00:00"))
with open(out, "w") as fh:
    for raw in open(transcript):
        e = json.loads(raw.strip())
        if e.get("type") != "assistant": continue
        if e.get("sessionId") != session_id: continue
        ts = datetime.fromisoformat(e.get("timestamp","").replace("Z","+00:00"))
        if ts < started_at: continue
        content = e.get("message",{}).get("content",[])
        if not content: continue
        fh.write(json.dumps({"content": content, "timestamp": e["timestamp"]}) + "\n")
PY

while IFS= read -r payload; do
  mnemonicos capture append-event --bridge-id claude \
    --conversation-id "$CONVERSATION_ID" --episode-id "$EPISODE_ID" \
    --type assistant_message --payload-json "$payload" --json >/dev/null
done < /tmp/mnemo_payloads.jsonl

# End episode
mnemonicos capture end-episode --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" --episode-id "$EPISODE_ID" \
  --status success --json >/dev/null

# Ingest every claude-owned pending summary (see scripts/lib/mnemonic_pending.sh)
source scripts/lib/mnemonic_pending.sh
while IFS= read -r pending; do
  if [[ -n "$TASK_ID" ]]; then
    mnemonicos orchestrator finish-task \
      --bridge-id claude --session-id "$SESSION_ID" --task-id "$TASK_ID" \
      --status success --outcome-type general --summary-file "$pending" \
      && rm -f "$pending"
  else
    scripts/mnemonic_postflight.sh --agent claude --slug "claude-$(date +%Y-%m-%d-%H%M%S)" \
      --summary-file "$pending" && rm -f "$pending"
  fi
done < <(mnemo_pending_files_for_bridge claude)

rm -f "$STATE_FILE" /tmp/mnemo_payloads.jsonl
exit 0
```

---

## Step 7 — PreCompact Hook

`scripts/mnemonic_precompact_dump.sh` fires when Claude Code is about to compact
the conversation context (context window near limit). It does **not** open or
close episodes — it only safeguards the pending summary.

What it does:
- Copies every claude-owned pending summary (files under `/tmp/mnemo_pending/`
  with `agent: claude`, plus the legacy `/tmp/mnemo_pending_summary.md`) to
  `brain/raw/sessions/<timestamp>-claude-precompact-<trigger>-<n>.md`
- If no pending summary exists, exits silently

The `trigger` field comes from the PreCompact hook's stdin JSON (`"trigger":
"auto"` when Claude Code initiates compaction automatically). This lets you
distinguish manual vs automatic compaction in the archive.

The copy is a safety net: if compaction discards context that Claude used to
decide what to write, the summary is preserved for manual review and reingestion
via `mnemonic_postflight.sh`.

Skeleton:

```bash
HOOK_JSON=$(cat)
TRIGGER=$(echo "$HOOK_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('trigger','auto'))")
TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
OUTFILE="brain/raw/sessions/${TIMESTAMP}-claude-precompact-${TRIGGER}.md"
mkdir -p brain/raw/sessions
source scripts/lib/mnemonic_pending.sh
n=0
while IFS= read -r pending; do
  n=$((n + 1)); cp "$pending" "${OUTFILE%.md}-$n.md"
done < <(mnemo_pending_files_for_bridge claude)
exit 0
```

---

## Event Types Reference

All events land in:

```
brain/raw/sessions/<conversation_id>/episodes/<episode_id>/events.jsonl
```

Each line:

```json
{
  "seq": 5,
  "type": "tool_call",
  "ts": "2026-05-13T16:38:41+08:00",
  "bridge_id": "claude",
  "conversation_id": "...",
  "episode_id": "...",
  "task_id": null,
  "branch_id": "main",
  "payload": { ... }
}
```

| Event type | Emitted by | Payload fields |
|---|---|---|
| `episode_started` | `begin-episode` (automatic) | `cwd`, `repo`, `branch`, `model`, `agent_runtime` |
| `user_message` | preflight hook | `content` (string) |
| `retrieval_packet` | preflight hook | `state` (retrieval_state string), `context_markdown`, optional Stage 7 retry trace (`final_pass_index`, `auto_retry_summary`, `retrieval_pass_count`, `followup_query`) |
| `tool_call` | post-tool hook (success or failure) | `tool_name`, `tool_input` |
| `tool_result` | post-tool hook (success or failure) | `tool_name`, `is_error`, `tool_response` (success) or `error`/`error_type`/`is_interrupt`/`is_timeout` (failure) |
| `failure` | post-tool **failure** hook only | `tool_name`, `error_message`, `error_type`, `source` |
| `file_changed` | file artifact hook | `file_path`, `tool`, `size_bytes`, `content` |
| `artifact_created` | file artifact hook | `file_path`, `tool`, `size_bytes`, `content` |
| `assistant_message` | stop hook (transcript scrape) | `content` (array of content blocks), `timestamp`, `source_uuid` |
| `episode_ended` | `end-episode` (automatic) | `status` |

Payloads above 32 KB are externalized to
`brain/raw/sessions/.../episodes/.../artifacts/event-NNNNNN-payload.json`
and the event record contains only `{"artifact": "artifacts/...", "byte_size": N, "sha256": "..."}`.

---

## Turn Flow Summary

```
User sends prompt
        │
        ▼
UserPromptSubmit hook fires
  ├─ capture begin-episode          → opens episode, gets conversation_id + episode_id
  ├─ capture append-event user_message
  ├─ orchestrator begin-task        → retrieval, returns context_markdown + task_id
  ├─ capture append-event retrieval_packet
  ├─ write /tmp/mnemo_hook_state_<session hash> → persists IDs for downstream hooks
  └─ print {"context": "..."}       → prepended to Claude's prompt
        │
        ▼
Claude processes prompt (tool calls interleaved)
        │
   For each tool call (CC 2.1.143+):
        ├─ PostToolUse hook (success):
        │    ├─ capture append-event tool_call
        │    └─ capture append-event tool_result   (is_error=false)
        │
        ├─ PostToolUseFailure hook (failure):
        │    ├─ capture append-event tool_call
        │    ├─ capture append-event tool_result   (is_error=true)
        │    └─ capture append-event failure
        │
        └─ PostToolUse hook (Write|Edit only, success):
             ├─ capture append-event file_changed    (Edit)
             └─ capture append-event artifact_created (Write)
        │
        ▼
Claude finishes responding
        │
Stop hook fires
  ├─ scrape ~/.claude/projects/.../SESSION_ID.jsonl for assistant entries
  ├─ capture append-event assistant_message  (for each entry)
  ├─ capture end-episode --status success
  ├─ for each claude-owned file in /tmp/mnemo_pending/ (+ legacy path):
  │    orchestrator finish-task --summary-file <file>   (or postflight when no task_id)
  │    rm <file> only if accepted
  └─ rm /tmp/mnemo_hook_state_<session hash>
```

---

## Disable Switch

To disable MnemonicOS for a session without touching settings.json:

```bash
# Disable
touch /path/to/mnemonicos/config/mnemo_disabled

# Re-enable
rm /path/to/mnemonicos/config/mnemo_disabled
```

All hooks check for this flag before doing anything and exit 0 silently.

---

## Verifying the Bridge Works

After your first session with hooks wired up, check the episode was captured:

```bash
# Find your session ID from the Claude Code transcript filename
ls ~/.claude/projects/<project-slug>/

# Inspect the episode
ls brain/raw/sessions/<session_id>/episodes/

# Count events
python3 -c "
import json
path = 'brain/raw/sessions/<session_id>/episodes/<episode_id>/events.jsonl'
counts = {}
for line in open(path):
    t = json.loads(line.strip()).get('type','?')
    counts[t] = counts.get(t, 0) + 1
print(counts)
"
```

Expected output for a typical turn:

```
{'episode_started': 1, 'user_message': 1, 'retrieval_packet': 1,
 'tool_call': N, 'tool_result': N, 'assistant_message': M, 'episode_ended': 1}
```

---

## Common Pitfalls

**Hook exits non-zero.** Claude Code blocks the turn. Always end hooks with
`exit 0` and use `|| true` on CLI calls that may fail.

**State file missing in PostToolUse.** Means preflight didn't run (or failed).
All downstream hooks check for the state file and bail early if absent.

**Heredoc inside `$()`.** Bash parses the closing `)` before the heredoc
terminator if you put `)` on the same line as `<<'PY'`. Always place `)` on
its own line after the heredoc terminator:

```bash
# Wrong — `)` closes before heredoc body is read
OUT=$(python3 - <<'PY') || exit 0
...
PY

# Correct
OUT=$(python3 - <<'PY'
...
PY
) || exit 0
```

**Large tool results.** Bash output can be megabytes. The tool hook truncates
at 256 KB total (128 KB per dict value). MnemonicOS externalizes above 32 KB.
If you see `externalized_payload: true` in an event, the artifact file holds
the real content. Truncated events carry `"_truncated": true`.

**Transcript timestamp filtering.** Without filtering by `started_at`, the Stop
hook would re-emit all prior turns as assistant messages. Always compare
`timestamp >= episode.started_at`.

**Stop hook with `stop_hook_active=true`.** CC 2.1.143+ passes `stop_hook_active: true`
in Stop hook stdin when the hook fires inside a loop guard. The stop hook must
check this field and exit 0 immediately; doing real work (episode end, writeback)
when it is `true` will double-emit events and risk hitting `CLAUDE_CODE_STOP_HOOK_BLOCK_CAP`.

**Legacy postflight fallback.** When `TASK_ID` and `SESSION_ID` are both set,
the stop hook uses `orchestrator finish-task` (preferred — runs full writeback
logic). When `CONVERSATION_ID` is missing (preflight failed entirely), it falls
back to `scripts/mnemonic_postflight.sh` directly. `capture project-episode`
is not used by the stop hook.
