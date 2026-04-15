#!/usr/bin/env bash
# mnemonic_stop_hook.sh — Claude Code Stop hook (per-turn finish_task)
#
# Fires once per user turn. Ingests pending summary via the shared orchestrator.
# Falls back to mnemonic_postflight.sh when no orchestrator state is available.
#
# Exit: always 0 — never block session close

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_ROOT/config/memory.toml" ]]; then
  CONFIG="$REPO_ROOT/config/memory.toml"
else
  CONFIG="$REPO_ROOT/config/memory.example.toml"
fi
PENDING="/tmp/mnemo_pending_summary.md"
STATE_FILE="${STATE_FILE:-/tmp/mnemo_hook_state}"

# --- MnemonicOS enable/disable switch ---
MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0  # no-op — MnemonicOS disabled (pending file left intact)
fi
# ----------------------------------------

# Read session_id from Stop hook's stdin JSON (same session as preflight)
HOOK_JSON=$(cat 2>/dev/null) || HOOK_JSON="{}"
SESSION_ID=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null) \
  || SESSION_ID=""

# No summary prepared — nothing to do
if [[ ! -f "$PENDING" || ! -s "$PENDING" ]]; then
  rm -f "$STATE_FILE"
  exit 0
fi

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

TASK_ID=""
[[ -f "$STATE_FILE" ]] && TASK_ID=$(cat "$STATE_FILE" 2>/dev/null | tr -d '\n') || TASK_ID=""

if [[ -n "$TASK_ID" && -n "$SESSION_ID" ]]; then
  # Orchestrator path: per-turn finish_task
  echo "MnemonicOS: finish-task task_id=$TASK_ID session=$SESSION_ID" >&2
  "${CLI[@]}" orchestrator finish-task \
    --bridge-id claude \
    --session-id "$SESSION_ID" \
    --task-id "$TASK_ID" \
    --status success \
    --outcome-type general \
    --summary-file "$PENDING" \
    2>&1 | sed 's/^/[mnemo] /' >&2 || true
else
  # Fallback: legacy postflight (no orchestrator state — backward compat)
  SLUG="claude-$(date +%Y-%m-%d-%H%M%S)"
  AGENT=$(grep -m1 '^agent:' "$PENDING" 2>/dev/null \
    | sed 's/^agent:[[:space:]]*//' | tr -d '\r') || AGENT="claude"
  [[ -z "$AGENT" ]] && AGENT="claude"
  SESSION_DATE=$(grep -m1 '^session_date:' "$PENDING" 2>/dev/null \
    | sed 's/^session_date:[[:space:]]*//' | tr -d '\r') || SESSION_DATE=""
  [[ -n "$SESSION_DATE" ]] && SLUG="claude-${SESSION_DATE}-$(date +%H%M%S)"
  echo "MnemonicOS: legacy postflight $AGENT/$SLUG" >&2
  "$REPO_ROOT/scripts/mnemonic_postflight.sh" \
    --agent "$AGENT" \
    --slug "$SLUG" \
    --summary-file "$PENDING" \
    --tag "auto-stop-hook" \
    2>&1 | sed 's/^/[mnemo] /' >&2 || true
fi

rm -f "$PENDING" "$STATE_FILE"
exit 0
