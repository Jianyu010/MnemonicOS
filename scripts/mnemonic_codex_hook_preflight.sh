#!/usr/bin/env bash
# mnemonic_codex_hook_preflight.sh — Codex UserPromptSubmit hook
#
# Starts a raw capture episode for this Codex turn, appends the submitted user
# prompt, and optionally injects MnemonicOS retrieval context back into Codex.
#
# Exit: always 0 — never block the prompt on retrieval/capture errors.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_ROOT/config/memory.toml" ]]; then
  CONFIG="$REPO_ROOT/config/memory.toml"
else
  CONFIG="$REPO_ROOT/config/memory.example.toml"
fi
STATE_FILE="${STATE_FILE:-}"

MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0
fi

HOOK_JSON=$(cat)

PROMPT=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('prompt',''))" 2>/dev/null) \
  || PROMPT=""
[[ -z "$PROMPT" ]] && exit 0

SESSION_ID=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json,uuid; d=json.load(sys.stdin); s=d.get('session_id',''); print(s or 'codex-'+uuid.uuid4().hex[:12])" \
  2>/dev/null) \
  || SESSION_ID="codex-$(python3 -c 'import uuid;print(uuid.uuid4().hex[:12])')"
if [[ -z "$STATE_FILE" ]]; then
  STATE_FILE="/tmp/mnemo_codex_hook_state_$(SESSION_ID="$SESSION_ID" python3 -c 'import hashlib,os; print(hashlib.sha256(os.environ["SESSION_ID"].encode()).hexdigest()[:16])' 2>/dev/null || printf 'default')"
fi

TURN_ID=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('turn_id',''))" 2>/dev/null) \
  || TURN_ID=""
CWD_FROM_HOOK=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('cwd',''))" 2>/dev/null) \
  || CWD_FROM_HOOK=""
MODEL=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('model',''))" 2>/dev/null) \
  || MODEL=""

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

CAPTURE=$("${CLI[@]}" capture begin-episode \
  --bridge-id codex \
  --conversation-id "$SESSION_ID" \
  --cwd "${CWD_FROM_HOOK:-$PWD}" \
  --model "$MODEL" \
  --agent-runtime codex \
  --platform-thread-id "$SESSION_ID" \
  --metadata-json "$(HOOK_JSON="$HOOK_JSON" python3 -c 'import json,os; d=json.loads(os.environ.get("HOOK_JSON","{}")); print(json.dumps({"turn_id": d.get("turn_id"), "hook_event": "UserPromptSubmit"}))' 2>/dev/null || printf '{}')" \
  --json 2>/dev/null) || CAPTURE=""

CONVERSATION_ID=$(echo "$CAPTURE" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('conversation_id',''))" 2>/dev/null) \
  || CONVERSATION_ID=""
EPISODE_ID=$(echo "$CAPTURE" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('episode_id',''))" 2>/dev/null) \
  || EPISODE_ID=""

if [[ -n "$CONVERSATION_ID" && -n "$EPISODE_ID" ]]; then
  PROMPT_PAYLOAD=$(PROMPT="$PROMPT" TURN_ID="$TURN_ID" HOOK_JSON="$HOOK_JSON" python3 -c \
    "import json,os; print(json.dumps({'content': os.environ.get('PROMPT',''), 'turn_id': os.environ.get('TURN_ID') or None, 'source': 'codex_user_prompt_submit'}))" 2>/dev/null) \
    || PROMPT_PAYLOAD="{}"
  "${CLI[@]}" capture append-event \
    --bridge-id codex \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --type user_message \
    --payload-json "$PROMPT_PAYLOAD" \
    --json >/dev/null 2>&1 || true
fi

RESULT=$("${CLI[@]}" orchestrator begin-task "$PROMPT" \
  --bridge-id codex --session-id "$SESSION_ID" --json 2>/dev/null) || RESULT=""

TASK_ID=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('task_id',''))" 2>/dev/null) \
  || TASK_ID=""
CONTEXT=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('context_markdown',''))" 2>/dev/null) \
  || CONTEXT=""
STATE=$(echo "$RESULT" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('retrieval_state',''))" 2>/dev/null) \
  || STATE=""

if [[ -n "$TASK_ID" || -n "$CONVERSATION_ID" ]]; then
  python3 - "$STATE_FILE" "$TASK_ID" "$SESSION_ID" "$CONVERSATION_ID" "$EPISODE_ID" "$TURN_ID" <<'PY' 2>/dev/null || true
import json
import sys

path, task_id, session_id, conversation_id, episode_id, turn_id = sys.argv[1:7]
with open(path, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "task_id": task_id,
            "session_id": session_id,
            "conversation_id": conversation_id,
            "episode_id": episode_id,
            "turn_id": turn_id,
        },
        handle,
        sort_keys=True,
    )
    handle.write("\n")
PY
fi

if [[ -n "$CONVERSATION_ID" && -n "$EPISODE_ID" && -n "$STATE" ]]; then
  RETRIEVAL_PAYLOAD=$(CONTEXT="$CONTEXT" STATE="$STATE" TURN_ID="$TURN_ID" python3 -c \
    "import json,os; print(json.dumps({'state': os.environ.get('STATE',''), 'context_markdown': os.environ.get('CONTEXT',''), 'turn_id': os.environ.get('TURN_ID') or None, 'source': 'codex_user_prompt_submit'}))" \
    2>/dev/null) || RETRIEVAL_PAYLOAD="{}"
  "${CLI[@]}" capture append-event \
    --bridge-id codex \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --type retrieval_packet \
    --payload-json "$RETRIEVAL_PAYLOAD" \
    --json >/dev/null 2>&1 || true
fi

[[ -z "$RESULT" || -z "$CONTEXT" || "$STATE" == "insufficient_no_hit" ]] && exit 0

python3 -c 'import json,sys; print(json.dumps({"hookSpecificOutput":{"hookEventName":"UserPromptSubmit","additionalContext":sys.argv[1]}}))' "$CONTEXT" 2>/dev/null || true
exit 0
