#!/usr/bin/env bash
# mnemonic_hook_file_artifact.sh — Claude Code PostToolUse hook
#
# Fires after Write or Edit tool calls. Appends a file_changed or
# artifact_created event to the active MnemonicOS episode so file
# writes/edits are captured as raw artifacts.
#
# Input (stdin): Claude Code PostToolUse JSON
#   { "session_id": "...", "tool_name": "Write|Edit",
#     "tool_input": { "file_path": "...", ... } }
# Exit: always 0 — never block the tool call

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "$REPO_ROOT/config/memory.toml" ]]; then
  CONFIG="$REPO_ROOT/config/memory.toml"
else
  CONFIG="$REPO_ROOT/config/memory.example.toml"
fi
# shellcheck source=lib/mnemonic_pending.sh
source "$REPO_ROOT/scripts/lib/mnemonic_pending.sh"
CONFIG="${MNEMO_CONFIG:-$CONFIG}"

# Abort if MnemonicOS disabled
MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0
fi

HOOK_JSON=$(cat)

# Per-session hook state (issue #37); explicit STATE_FILE wins, legacy shared
# path is the fallback for a preflight hook that predates per-session state.
if [[ -z "${STATE_FILE:-}" ]]; then
  _SESSION_ID=$(echo "$HOOK_JSON" | python3 -c \
    "import sys,json; d=json.load(sys.stdin); print(d.get('session_id',''))" 2>/dev/null) || _SESSION_ID=""
  STATE_FILE="$(mnemo_state_file_for_session "$(mnemo_state_prefix_for_bridge claude)" "$_SESSION_ID")"
  [[ ! -f "$STATE_FILE" && -f /tmp/mnemo_hook_state ]] && STATE_FILE="/tmp/mnemo_hook_state"
fi
# Abort if no active episode state
[[ -f "$STATE_FILE" ]] || exit 0

TOOL_NAME=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('tool_name',''))" 2>/dev/null) \
  || exit 0

# Only handle Write and Edit
[[ "$TOOL_NAME" == "Write" || "$TOOL_NAME" == "Edit" ]] || exit 0

FILE_PATH=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('tool_input',{}).get('file_path',''))" 2>/dev/null) \
  || FILE_PATH=""
[[ -z "$FILE_PATH" ]] && exit 0

# Read capture coordinates from state file
CONVERSATION_ID=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("conversation_id", ""))
except Exception:
    print("")
PY
)
EPISODE_ID=$(python3 - "$STATE_FILE" <<'PY' 2>/dev/null
import json, sys
try:
    print(json.load(open(sys.argv[1])).get("episode_id", ""))
except Exception:
    print("")
PY
)

[[ -z "$CONVERSATION_ID" || -z "$EPISODE_ID" ]] && exit 0

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

# artifact_created for Write (new file), file_changed for Edit
if [[ "$TOOL_NAME" == "Write" ]]; then
  EVENT_TYPE="artifact_created"
else
  EVENT_TYPE="file_changed"
fi

# Build payload: path, tool, size, inline content for small text files
PAYLOAD=$(python3 - "$FILE_PATH" "$TOOL_NAME" 2>/dev/null <<'PY'
import json, os, sys
file_path, tool_name = sys.argv[1], sys.argv[2]
payload = {"file_path": file_path, "tool": tool_name}
try:
    stat = os.stat(file_path)
    payload["size_bytes"] = stat.st_size
    ext = os.path.splitext(file_path)[1].lower()
    text_exts = {".md", ".txt", ".html", ".css", ".js", ".ts", ".py",
                 ".json", ".yaml", ".yml", ".toml", ".sh", ".sql", ".csv"}
    if ext in text_exts:
        payload["content"] = open(file_path, encoding="utf-8", errors="replace").read()
except Exception:
    pass
print(json.dumps(payload))
PY
)
[[ -z "$PAYLOAD" ]] && PAYLOAD="{}"

"${CLI[@]}" capture append-event \
  --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" \
  --episode-id "$EPISODE_ID" \
  --type "$EVENT_TYPE" \
  --payload-json "$PAYLOAD" \
  --json >/dev/null 2>&1 || true

exit 0
