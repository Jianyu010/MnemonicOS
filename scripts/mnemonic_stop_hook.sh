#!/usr/bin/env bash
# mnemonic_stop_hook.sh — Claude Code Stop hook (per-turn finish_task)
#
# Fires once per user turn. Finalizes the raw episode, then ingests every
# pending summary that belongs to the claude bridge:
#   - new-style files under $MNEMO_PENDING_DIR (default /tmp/mnemo_pending),
#     written by scripts/mnemonic_pending_summary.sh, one file per writeback;
#   - the legacy /tmp/mnemo_pending_summary.md when its `agent:` header is
#     "claude" (or absent).
# Summaries owned by other agents (e.g. `agent: codex`) are left untouched.
#
# A summary is deleted only after the CLI accepted it. If orchestrator state is
# missing (begin-task failed, hook timed out) the summary still goes in through
# the legacy postflight path instead of being dropped (issue #34).
#
# Exit: always 0 — never block session close

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
BRIDGE_ID="claude"

# --- MnemonicOS enable/disable switch ---
MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0  # no-op — MnemonicOS disabled (pending files left intact)
fi
# ----------------------------------------

# Stop hooks must finish quickly. They still enqueue capture for daemon pickup,
# but they do not run extraction/promotion inline inside the hook process.
export MNEMOS_AUTO_TRIGGER_PIPELINE="${MNEMOS_AUTO_TRIGGER_PIPELINE:-0}"

# Read session_id from Stop hook's stdin JSON (same session as preflight)
HOOK_JSON=$(cat 2>/dev/null) || HOOK_JSON="{}"

# CC 2.1.143+: stop_hook_active=true means the hook is running inside a loop
# guard; must return success immediately or trigger "stop_hook_prevented" limit.
STOP_HOOK_ACTIVE=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print('1' if d.get('stop_hook_active') else '0')" 2>/dev/null) \
  || STOP_HOOK_ACTIVE="0"
[[ "$STOP_HOOK_ACTIVE" == "1" ]] && exit 0

SESSION_ID=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null) \
  || SESSION_ID=""

# Per-session hook state (issue #37). An explicit STATE_FILE wins (tests);
# otherwise use the per-session path, falling back to the legacy shared path
# written by an older preflight hook.
if [[ -z "${STATE_FILE:-}" ]]; then
  STATE_FILE="$(mnemo_state_file_for_session /tmp/mnemo_hook_state "$SESSION_ID")"
  if [[ ! -f "$STATE_FILE" && -f /tmp/mnemo_hook_state ]]; then
    STATE_FILE="/tmp/mnemo_hook_state"
  fi
fi

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
  # Scrape Claude Code transcript for raw assistant message entries this episode.
  # Each entry's full content array (text, thinking, tool_use blocks) is appended
  # as its own assistant_message event — no merging, no filtering.
  TRANSCRIPT=$(find "$HOME/.claude/projects" -name "${SESSION_ID}.jsonl" 2>/dev/null | head -1)
  EPISODE_MANIFEST="$REPO_ROOT/brain/raw/sessions/$CONVERSATION_ID/episodes/$EPISODE_ID/manifest.json"
  if [[ -n "$TRANSCRIPT" && -f "$TRANSCRIPT" && -f "$EPISODE_MANIFEST" ]]; then
    PAYLOADS_FILE=$(mktemp /tmp/mnemo_transcript_payloads.XXXXXX.jsonl)
    python3 - "$SESSION_ID" "$TRANSCRIPT" "$EPISODE_MANIFEST" "$PAYLOADS_FILE" <<'PY' 2>/dev/null || true
import json, sys
from datetime import datetime

session_id, transcript_path, manifest_path, out_path = sys.argv[1:5]

def parse_ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))

try:
    started_at = parse_ts(json.load(open(manifest_path)).get("started_at", ""))
except Exception:
    started_at = None

with open(out_path, "w", encoding="utf-8") as out:
    for raw in open(transcript_path, encoding="utf-8"):
        raw = raw.strip()
        if not raw:
            continue
        try:
            entry = json.loads(raw)
        except Exception:
            continue
        if entry.get("type") != "assistant":
            continue
        if entry.get("sessionId") != session_id:
            continue
        if started_at:
            try:
                if parse_ts(entry.get("timestamp", "")) < started_at:
                    continue
            except Exception:
                pass
        content = entry.get("message", {}).get("content", [])
        if not content:
            continue
        payload = {
            "content": content,
            "timestamp": entry.get("timestamp"),
            "source_uuid": entry.get("uuid"),
            "request_id": entry.get("requestId"),
        }
        out.write(json.dumps(payload) + "\n")
PY
    if [[ -s "$PAYLOADS_FILE" ]]; then
      while IFS= read -r payload_json; do
        [[ -z "$payload_json" ]] && continue
        "${CLI[@]}" capture append-event \
          --bridge-id "$BRIDGE_ID" \
          --conversation-id "$CONVERSATION_ID" \
          --episode-id "$EPISODE_ID" \
          --type assistant_message \
          --payload-json "$payload_json" \
          --json >/dev/null 2>&1 || true
      done < "$PAYLOADS_FILE"
    fi
    rm -f "$PAYLOADS_FILE"
  fi

  "${CLI[@]}" capture end-episode \
    --bridge-id "$BRIDGE_ID" \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --status success \
    --json >/dev/null 2>&1 || true

fi

# --- Ingest every pending summary owned by this bridge ---------------------
# Returns 0 when the CLI accepted the summary (so the file may be deleted).
ingest_pending_file() {
  local pending="$1" log rc agent session_date slug
  log=$(mktemp /tmp/mnemo_stop_ingest.XXXXXX.log)
  if [[ -n "$TASK_ID" && -n "$SESSION_ID" ]]; then
    # Orchestrator path: per-turn finish_task
    echo "MnemonicOS: finish-task task_id=$TASK_ID session=$SESSION_ID file=$(basename "$pending")" >&2
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
    # No task_id (begin-task failed, hook timed out, or no preflight state):
    # archive through the legacy postflight path rather than dropping it.
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
  sed 's/^/[mnemo] /' "$log" >&2
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
done < <(mnemo_pending_files_for_bridge "$BRIDGE_ID")

rm -f "$STATE_FILE"
exit 0
