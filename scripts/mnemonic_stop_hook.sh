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
# Test/agent isolation: explicit config path overrides repo default.
CONFIG="${MNEMO_CONFIG:-$CONFIG}"
PENDING="/tmp/mnemo_pending_summary.md"
STATE_FILE="${STATE_FILE:-/tmp/mnemo_hook_state}"

# --- MnemonicOS enable/disable switch ---
MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0  # no-op — MnemonicOS disabled (pending file left intact)
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

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

TASK_ID=""
CONVERSATION_ID=""
EPISODE_ID=""
if [[ -f "$STATE_FILE" ]]; then
  TASK_ID=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null || true
import json
import sys
raw = open(sys.argv[1], encoding="utf-8").read().strip()
try:
    data = json.loads(raw)
    print(data.get("task_id", ""))
except Exception:
    print(raw)
PY
)
  CONVERSATION_ID=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null || true
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("conversation_id", ""))
except Exception:
    print("")
PY
)
  EPISODE_ID=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null || true
import json
import sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("episode_id", ""))
except Exception:
    print("")
PY
)
fi
TASK_ID=$(echo "$TASK_ID" | tr -d '\n')
CONVERSATION_ID=$(echo "$CONVERSATION_ID" | tr -d '\n')
EPISODE_ID=$(echo "$EPISODE_ID" | tr -d '\n')

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
          --bridge-id claude \
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
    --bridge-id claude \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --status success \
    --json >/dev/null 2>&1 || true

fi

# No summary prepared — raw capture has already been finalized if available.
if [[ ! -f "$PENDING" || ! -s "$PENDING" ]]; then
  rm -f "$STATE_FILE"
  exit 0
fi

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
elif [[ -z "$CONVERSATION_ID" ]]; then
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
