#!/usr/bin/env bash
# mnemonic_hook_preflight.sh — Claude Code UserPromptSubmit hook (per-turn begin_task)
#
# Resolves MnemonicOS context before an LLM call via the shared orchestrator.
#
# Claude Code hook protocol:
#   Input (stdin): JSON with at minimum { "prompt": "...", "session_id": "..." }
#   Output (stdout): JSON { "context": "..." } to prepend context, or empty to no-op
#   Exit: always 0 — never block the prompt on retrieval errors

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_ROOT/config/memory.toml" ]]; then
  CONFIG="$REPO_ROOT/config/memory.toml"
else
  CONFIG="$REPO_ROOT/config/memory.example.toml"
fi
# Test/agent isolation: explicit config path overrides repo default.
CONFIG="${MNEMO_CONFIG:-$CONFIG}"
STATE_FILE="${STATE_FILE:-/tmp/mnemo_hook_state}"

# --- MnemonicOS enable/disable switch ---
MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
MNEMO_AUTO_RETRY="${MNEMO_AUTO_RETRY:-1}"
MNEMO_MAX_FOCUSED_RETRIES="${MNEMO_MAX_FOCUSED_RETRIES:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0  # no-op — MnemonicOS disabled
fi
# ----------------------------------------

HOOK_JSON=$(cat)

PROMPT=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('prompt',''))" 2>/dev/null) \
  || PROMPT=""
[[ -z "$PROMPT" ]] && exit 0

SESSION_ID=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json,uuid; d=json.load(sys.stdin); \
   s=d.get('session_id',''); print(s or 'session-'+uuid.uuid4().hex[:12])" \
  2>/dev/null) \
  || SESSION_ID="session-$(python3 -c 'import uuid;print(uuid.uuid4().hex[:12])')"

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

CAPTURE=$("${CLI[@]}" capture begin-episode \
  --bridge-id claude \
  --conversation-id "$SESSION_ID" \
  --cwd "$PWD" \
  --agent-runtime claude-code \
  --json 2>/dev/null) || CAPTURE=""

CONVERSATION_ID=$(echo "$CAPTURE" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('conversation_id',''))" 2>/dev/null) \
  || CONVERSATION_ID=""
EPISODE_ID=$(echo "$CAPTURE" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('episode_id',''))" 2>/dev/null) \
  || EPISODE_ID=""

if [[ -n "$CONVERSATION_ID" && -n "$EPISODE_ID" ]]; then
  PROMPT_PAYLOAD=$(PROMPT="$PROMPT" python3 -c \
    "import json,os; print(json.dumps({'content': os.environ.get('PROMPT','')}))" 2>/dev/null) \
    || PROMPT_PAYLOAD="{}"
  "${CLI[@]}" capture append-event \
    --bridge-id claude \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --type user_message \
    --payload-json "$PROMPT_PAYLOAD" \
    --json >/dev/null 2>&1 || true
fi

BEGIN_ARGS=(orchestrator begin-task "$PROMPT" \
  --bridge-id claude --session-id "$SESSION_ID" \
  --max-focused-retries "$MNEMO_MAX_FOCUSED_RETRIES" \
  --json)
if [[ "$MNEMO_AUTO_RETRY" != "0" ]]; then
  BEGIN_ARGS+=(--auto-retry)
fi
RESULT=$("${CLI[@]}" "${BEGIN_ARGS[@]}" 2>/dev/null) || RESULT=""

TASK_ID=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('task_id',''))" 2>/dev/null) \
  || TASK_ID=""
CONTEXT=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('context_markdown',''))" 2>/dev/null) \
  || CONTEXT=""
STATE=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('retrieval_state',''))" 2>/dev/null) \
  || STATE=""

# Persist capture coordinates and task_id for this turn's Stop hook.
if [[ -n "$TASK_ID" || -n "$CONVERSATION_ID" ]]; then
  python3 - "$STATE_FILE" "$TASK_ID" "$SESSION_ID" "$CONVERSATION_ID" "$EPISODE_ID" <<'PY' 2>/dev/null || true
import json
import sys
path, task_id, session_id, conversation_id, episode_id = sys.argv[1:6]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "task_id": task_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "episode_id": episode_id,
        },
        handle,
        sort_keys=True,
    )
    handle.write("\n")
PY
fi

[[ -z "$RESULT" ]] && exit 0

# Emit retrieval_packet event when a retrieval result exists (even if empty/no-hit).
if [[ -n "$CONVERSATION_ID" && -n "$EPISODE_ID" && -n "$STATE" ]]; then
  RETRIEVAL_PAYLOAD=$(CONTEXT="$CONTEXT" STATE="$STATE" RESULT="$RESULT" python3 -c \
    "import json,os; d=json.loads(os.environ.get('RESULT') or '{}'); print(json.dumps({'state': os.environ.get('STATE',''), 'context_markdown': os.environ.get('CONTEXT',''), 'final_pass_index': d.get('final_pass_index'), 'auto_retry_summary': d.get('auto_retry_summary'), 'retrieval_pass_count': len(d.get('retrieval_passes') or []), 'followup_query': (d.get('emission') or {}).get('followup_query')}))" \
    2>/dev/null) || RETRIEVAL_PAYLOAD="{}"
  "${CLI[@]}" capture append-event \
    --bridge-id claude \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --type retrieval_packet \
    --payload-json "$RETRIEVAL_PAYLOAD" \
    --json >/dev/null 2>&1 || true
fi

[[ -z "$CONTEXT" || "$STATE" == "insufficient_no_hit" ]] && exit 0

python3 -c "import json,sys; print(json.dumps({'context':sys.argv[1]}))" "$CONTEXT"
exit 0
