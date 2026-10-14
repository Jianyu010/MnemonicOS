#!/usr/bin/env bash
# mnemonic_codex_stop_hook.sh — Codex Stop hook
#
# Completes the raw capture episode started by mnemonic_codex_hook_preflight.sh.
# Uses Codex's Stop hook payload directly; it does not scrape Claude logs.
#
# Then ingests every pending summary owned by the codex bridge: new-style files
# under $MNEMO_PENDING_DIR (default /tmp/mnemo_pending) plus the legacy
# /tmp/mnemo_pending_summary.md when its `agent:` header is "codex" (or absent).
# Claude-owned summaries are left for the Claude Stop hook (issue #37). A file
# is deleted only after the CLI accepted it; without a task_id it goes in via
# the legacy postflight path instead of being left behind or dropped.
#
# Exit: always 0 — never block turn completion.

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
BRIDGE_ID="codex"

MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0
fi

# Stop hooks must finish quickly. They still enqueue capture for daemon pickup,
# but they do not run extraction/promotion inline inside the hook process.
export MNEMOS_AUTO_TRIGGER_PIPELINE="${MNEMOS_AUTO_TRIGGER_PIPELINE:-0}"

HOOK_JSON=$(cat 2>/dev/null) || HOOK_JSON="{}"
SESSION_ID=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null) \
  || SESSION_ID=""
if [[ -z "$STATE_FILE" ]]; then
  STATE_FILE="$(mnemo_state_file_for_session "$(mnemo_state_prefix_for_bridge codex)" "$SESSION_ID")"
  [[ -z "$SESSION_ID" ]] && STATE_FILE="$(mnemo_state_prefix_for_bridge codex)_default"
fi
TURN_ID=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('turn_id',''))" 2>/dev/null) \
  || TURN_ID=""
LAST_ASSISTANT=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('last_assistant_message') or '')" 2>/dev/null) \
  || LAST_ASSISTANT=""

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

TASK_ID="$(mnemo_state_field "$STATE_FILE" task_id | tr -d '\n')"
CONVERSATION_ID="$(mnemo_state_field "$STATE_FILE" conversation_id | tr -d '\n')"
EPISODE_ID="$(mnemo_state_field "$STATE_FILE" episode_id | tr -d '\n')"
STATE_SESSION_ID="$(mnemo_state_field "$STATE_FILE" session_id | tr -d '\n')"
[[ -z "$SESSION_ID" ]] && SESSION_ID="$STATE_SESSION_ID"

if [[ -n "$CONVERSATION_ID" && -n "$EPISODE_ID" ]]; then
  if [[ -n "$LAST_ASSISTANT" ]]; then
    PAYLOAD_FILE=$(mktemp /tmp/mnemo_codex_assistant_payload.XXXXXX.json)
    LAST_ASSISTANT="$LAST_ASSISTANT" TURN_ID="$TURN_ID" HOOK_JSON="$HOOK_JSON" python3 - "$PAYLOAD_FILE" <<'PY' 2>/dev/null || true
import json
import os
import sys

target = sys.argv[1]
with open(target, "w", encoding="utf-8") as handle:
    json.dump(
        {
            "content": os.environ.get("LAST_ASSISTANT", ""),
            "turn_id": os.environ.get("TURN_ID") or None,
            "source": "codex_stop_hook",
        },
        handle,
    )
PY
    if [[ -s "$PAYLOAD_FILE" ]]; then
      "${CLI[@]}" capture append-event \
        --bridge-id "$BRIDGE_ID" \
        --conversation-id "$CONVERSATION_ID" \
        --episode-id "$EPISODE_ID" \
        --type assistant_message \
        --payload-file "$PAYLOAD_FILE" \
        --json >/dev/null 2>&1 || true
    fi
    rm -f "$PAYLOAD_FILE"
  fi

  "${CLI[@]}" capture end-episode \
    --bridge-id "$BRIDGE_ID" \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --status success \
    --metadata-json "$(TURN_ID="$TURN_ID" python3 -c 'import json,os; print(json.dumps({"turn_id": os.environ.get("TURN_ID") or None, "hook_event": "Stop"}))' 2>/dev/null || printf '{}')" \
    --json >/dev/null 2>&1 || true

fi

# --- Ingest every pending summary owned by this bridge ---------------------
ingest_pending_file() {
  local pending="$1" log rc agent session_date slug
  log=$(mktemp /tmp/mnemo_codex_stop_ingest.XXXXXX.log)
  if [[ -n "$TASK_ID" && -n "$SESSION_ID" ]]; then
    "${CLI[@]}" orchestrator finish-task \
      --bridge-id "$BRIDGE_ID" \
      --session-id "$SESSION_ID" \
      --task-id "$TASK_ID" \
      --status success \
      --outcome-type general \
      --summary-file "$pending" \
      >"$log" 2>&1
    rc=$?
  else
    agent="$(mnemo_summary_agent "$pending")"
    [[ -z "$agent" ]] && agent="$BRIDGE_ID"
    session_date="$(mnemo_summary_session_date "$pending")"
    slug="${BRIDGE_ID}-${SESSION_ID:-nosession}-$(date +%Y-%m-%d-%H%M%S)"
    [[ -n "$session_date" ]] && slug="${BRIDGE_ID}-${session_date}-$(date +%H%M%S)"
    echo "MnemonicOS: legacy postflight $agent/$slug file=$(basename "$pending") (no task_id)" >&2
    MNEMO_CONFIG="$CONFIG" "$REPO_ROOT/scripts/mnemonic_postflight.sh" \
      --agent "$agent" \
      --slug "$slug" \
      --summary-file "$pending" \
      --tag "auto-stop-hook" \
      >"$log" 2>&1
    rc=$?
  fi
  if [[ "$rc" -ne 0 ]]; then
    sed 's/^/[mnemo] /' "$log" >&2
  fi
  rm -f "$log"
  return "$rc"
}

while IFS= read -r pending; do
  [[ -z "$pending" ]] && continue
  if ingest_pending_file "$pending"; then
    rm -f "$pending"
  else
    echo "MnemonicOS: ingestion failed for $pending — left in place for the next turn" >&2
  fi
done < <(mnemo_pending_files_for_bridge "$BRIDGE_ID" "$SESSION_ID" "$STATE_FILE")

rm -f "$STATE_FILE"
exit 0
