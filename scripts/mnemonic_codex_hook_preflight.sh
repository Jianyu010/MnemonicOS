#!/usr/bin/env bash
# mnemonic_codex_hook_preflight.sh — Codex UserPromptSubmit hook
#
# Starts a raw capture episode for this Codex turn, appends the submitted user
# prompt, and optionally injects MnemonicOS retrieval context back into Codex.
#
# Exit: always 0 — never block the prompt on retrieval/capture errors.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/mnemonic_pending.sh
source "$REPO_ROOT/scripts/lib/mnemonic_pending.sh"
if [[ -f "$REPO_ROOT/config/memory.toml" ]]; then
  CONFIG="$REPO_ROOT/config/memory.toml"
else
  CONFIG="$REPO_ROOT/config/memory.example.toml"
fi
# Test/agent isolation: explicit config path overrides repo default.
CONFIG="${MNEMO_CONFIG:-$CONFIG}"
STATE_FILE="${STATE_FILE:-}"

MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
MNEMO_AUTO_RETRY="${MNEMO_AUTO_RETRY:-1}"
MNEMO_MAX_FOCUSED_RETRIES="${MNEMO_MAX_FOCUSED_RETRIES:-1}"
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
  STATE_FILE="$(mnemo_state_file_for_session "$(mnemo_state_prefix_for_bridge codex)" "$SESSION_ID")"
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

# Run a CLI call, capturing stdout; on failure report ONE line to stderr so a
# broken config or dead daemon is visible instead of silently disabling memory
# (issue #32). Never changes the hook's exit status.
cli_or_warn() {
  local label="$1"; shift
  local err rc out
  err=$(mktemp /tmp/mnemo_codex_preflight_err.XXXXXX)
  out=$("$@" 2>"$err")
  rc=$?
  if [[ $rc -ne 0 ]]; then
    echo "MnemonicOS: $label failed (exit $rc): $(tail -n 1 "$err" 2>/dev/null | cut -c1-300)" >&2
    out=""
  fi
  rm -f "$err"
  printf '%s' "$out"
  return 0
}

CAPTURE=$(cli_or_warn "capture begin-episode" "${CLI[@]}" capture begin-episode \
  --bridge-id codex \
  --conversation-id "$SESSION_ID" \
  --cwd "${CWD_FROM_HOOK:-$PWD}" \
  --model "$MODEL" \
  --agent-runtime codex \
  --platform-thread-id "$SESSION_ID" \
  --metadata-json "$(HOOK_JSON="$HOOK_JSON" python3 -c 'import json,os; d=json.loads(os.environ.get("HOOK_JSON","{}")); print(json.dumps({"turn_id": d.get("turn_id"), "hook_event": "UserPromptSubmit"}))' 2>/dev/null || printf '{}')" \
  --json)

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

BEGIN_ARGS=(orchestrator begin-task "$PROMPT" \
  --bridge-id codex --session-id "$SESSION_ID" \
  --max-focused-retries "$MNEMO_MAX_FOCUSED_RETRIES" \
  --json)
if [[ "$MNEMO_AUTO_RETRY" != "0" ]]; then
  BEGIN_ARGS+=(--auto-retry)
fi
RESULT=$(cli_or_warn "orchestrator begin-task" "${CLI[@]}" "${BEGIN_ARGS[@]}")

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
  RETRIEVAL_PAYLOAD=$(CONTEXT="$CONTEXT" STATE="$STATE" TURN_ID="$TURN_ID" RESULT="$RESULT" python3 -c \
    "import json,os; d=json.loads(os.environ.get('RESULT') or '{}'); print(json.dumps({'state': os.environ.get('STATE',''), 'context_markdown': os.environ.get('CONTEXT',''), 'turn_id': os.environ.get('TURN_ID') or None, 'source': 'codex_user_prompt_submit', 'final_pass_index': d.get('final_pass_index'), 'auto_retry_summary': d.get('auto_retry_summary'), 'retrieval_pass_count': len(d.get('retrieval_passes') or []), 'followup_query': (d.get('emission') or {}).get('followup_query')}))" \
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
