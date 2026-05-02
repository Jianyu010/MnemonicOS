#!/usr/bin/env bash
# mnemonic_codex_stop_hook.sh — Codex Stop hook
#
# Completes the raw capture episode started by mnemonic_codex_hook_preflight.sh.
# Uses Codex's Stop hook payload directly; it does not scrape Claude logs.
#
# Exit: always 0 — never block turn completion.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_ROOT/config/memory.toml" ]]; then
  CONFIG="$REPO_ROOT/config/memory.toml"
else
  CONFIG="$REPO_ROOT/config/memory.example.toml"
fi
STATE_FILE="${STATE_FILE:-}"
PENDING="/tmp/mnemo_pending_summary.md"

MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0
fi

HOOK_JSON=$(cat 2>/dev/null) || HOOK_JSON="{}"
SESSION_ID=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null) \
  || SESSION_ID=""
if [[ -z "$STATE_FILE" ]]; then
  STATE_FILE="/tmp/mnemo_codex_hook_state_$(SESSION_ID="$SESSION_ID" python3 -c 'import hashlib,os; print(hashlib.sha256(os.environ.get("SESSION_ID","").encode()).hexdigest()[:16])' 2>/dev/null || printf 'default')"
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

TASK_ID=""
CONVERSATION_ID=""
EPISODE_ID=""
if [[ -f "$STATE_FILE" ]]; then
  TASK_ID=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("task_id", ""))
except Exception:
    print("")
PY
)
  CONVERSATION_ID=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null || true
import json, sys
try:
    print(json.load(open(sys.argv[1], encoding="utf-8")).get("conversation_id", ""))
except Exception:
    print("")
PY
)
  EPISODE_ID=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null || true
import json, sys
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
        --bridge-id codex \
        --conversation-id "$CONVERSATION_ID" \
        --episode-id "$EPISODE_ID" \
        --type assistant_message \
        --payload-file "$PAYLOAD_FILE" \
        --json >/dev/null 2>&1 || true
    fi
    rm -f "$PAYLOAD_FILE"
  fi

  "${CLI[@]}" capture end-episode \
    --bridge-id codex \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --status success \
    --metadata-json "$(TURN_ID="$TURN_ID" python3 -c 'import json,os; print(json.dumps({"turn_id": os.environ.get("TURN_ID") or None, "hook_event": "Stop"}))' 2>/dev/null || printf '{}')" \
    --json >/dev/null 2>&1 || true

  if [[ -f "$PENDING" && -s "$PENDING" ]]; then
    "${CLI[@]}" capture project-episode \
      --conversation-id "$CONVERSATION_ID" \
      --episode-id "$EPISODE_ID" \
      --json >/dev/null 2>&1 || true
  fi
fi

rm -f "$STATE_FILE"
exit 0
