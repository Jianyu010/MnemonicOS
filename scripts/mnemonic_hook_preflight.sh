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
CONFIG="$REPO_ROOT/config/memory.toml"
STATE_FILE="${STATE_FILE:-/tmp/mnemo_hook_state}"

# --- MnemonicOS enable/disable switch ---
MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
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

RESULT=$("${CLI[@]}" orchestrator begin-task "$PROMPT" \
  --bridge-id claude --session-id "$SESSION_ID" --json 2>/dev/null) || RESULT=""
[[ -z "$RESULT" ]] && exit 0

TASK_ID=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('task_id',''))" 2>/dev/null) \
  || TASK_ID=""
CONTEXT=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('context_markdown',''))" 2>/dev/null) \
  || CONTEXT=""
STATE=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('retrieval_state',''))" 2>/dev/null) \
  || STATE=""

# Persist task_id for this turn's Stop hook (plain text, one line)
[[ -n "$TASK_ID" ]] && echo "$TASK_ID" > "$STATE_FILE" 2>/dev/null || true

[[ -z "$CONTEXT" || "$STATE" == "insufficient_no_hit" ]] && exit 0

python3 -c "import json,sys; print(json.dumps({'context':sys.argv[1]}))" "$CONTEXT"
exit 0
