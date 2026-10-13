#!/usr/bin/env bash
# mnemonic_hook_post_tool_failure.sh — Claude Code PostToolUseFailure hook
#
# Fires when a tool call fails (CC 2.1.143+). Appends tool_result (is_error=true)
# and failure events to the active MnemonicOS episode.
#
# Input (stdin): CC PostToolUseFailure JSON
#   { "session_id": "...", "tool_name": "...", "tool_use_id": "...",
#     "tool_input": {...}, "error": "...", "error_type": "...",
#     "is_interrupt": bool, "is_timeout": bool }
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
  STATE_FILE="$(mnemo_state_file_for_session /tmp/mnemo_hook_state "$_SESSION_ID")"
  [[ ! -f "$STATE_FILE" && -f /tmp/mnemo_hook_state ]] && STATE_FILE="/tmp/mnemo_hook_state"
fi
[[ -f "$STATE_FILE" ]] || exit 0

PAYLOADS_FILE=$(mktemp /tmp/mnemo_tool_failure_payloads.XXXXXX.json)

HOOK_JSON="$HOOK_JSON" python3 - "$PAYLOADS_FILE" <<'PY' 2>/dev/null || { rm -f "$PAYLOADS_FILE"; exit 0; }
import json, os, sys

out_path = sys.argv[1]
raw = os.environ.get("HOOK_JSON", "{}")
try:
    hook = json.loads(raw)
except Exception:
    sys.exit(0)

tool_name   = hook.get("tool_name", "")
tool_input  = hook.get("tool_input", {})
error_msg   = str(hook.get("error") or "")[:512]
error_type  = hook.get("error_type", "")
is_interrupt = bool(hook.get("is_interrupt", False))
is_timeout   = bool(hook.get("is_timeout", False))

MAX_BYTES = 256 * 1024

def maybe_truncate(obj):
    encoded = json.dumps(obj, ensure_ascii=False)
    if len(encoded.encode("utf-8")) <= MAX_BYTES:
        return obj, False
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if isinstance(v, str) and len(v.encode("utf-8")) > MAX_BYTES // 2:
                out[k] = v[:MAX_BYTES // 2] + "\n…[truncated]"
            else:
                out[k] = v
        return out, True
    if isinstance(obj, str):
        return obj[:MAX_BYTES] + "\n…[truncated]", True
    return obj, False

tool_input_out, _ = maybe_truncate(tool_input)

call_payload    = {"tool_name": tool_name, "tool_input": tool_input_out}
result_payload  = {"tool_name": tool_name, "is_error": True,
                   "error": error_msg, "error_type": error_type,
                   "is_interrupt": is_interrupt, "is_timeout": is_timeout}
failure_payload = {"tool_name": tool_name, "error_message": error_msg,
                   "error_type": error_type, "source": "tool_error"}

with open(out_path, "w", encoding="utf-8") as fh:
    json.dump({"call": call_payload, "result": result_payload,
               "failure": failure_payload}, fh)
PY

[[ -s "$PAYLOADS_FILE" ]] || { rm -f "$PAYLOADS_FILE"; exit 0; }

CALL_PAYLOAD=$(python3 -c \
  "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d['call']))" \
  "$PAYLOADS_FILE" 2>/dev/null) || { rm -f "$PAYLOADS_FILE"; exit 0; }
RESULT_PAYLOAD=$(python3 -c \
  "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d['result']))" \
  "$PAYLOADS_FILE" 2>/dev/null) || { rm -f "$PAYLOADS_FILE"; exit 0; }
FAILURE_PAYLOAD=$(python3 -c \
  "import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps(d['failure']))" \
  "$PAYLOADS_FILE" 2>/dev/null) || FAILURE_PAYLOAD=""
rm -f "$PAYLOADS_FILE"

CONVERSATION_ID=$(python3 -c \
  "import json,sys; print(json.load(open(sys.argv[1])).get('conversation_id',''))" \
  "$STATE_FILE" 2>/dev/null) || CONVERSATION_ID=""
EPISODE_ID=$(python3 -c \
  "import json,sys; print(json.load(open(sys.argv[1])).get('episode_id',''))" \
  "$STATE_FILE" 2>/dev/null) || EPISODE_ID=""

[[ -z "$CONVERSATION_ID" || -z "$EPISODE_ID" ]] && exit 0

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

"${CLI[@]}" capture append-event \
  --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" \
  --episode-id "$EPISODE_ID" \
  --type tool_call \
  --payload-json "$CALL_PAYLOAD" \
  --json >/dev/null 2>&1 || true

"${CLI[@]}" capture append-event \
  --bridge-id claude \
  --conversation-id "$CONVERSATION_ID" \
  --episode-id "$EPISODE_ID" \
  --type tool_result \
  --payload-json "$RESULT_PAYLOAD" \
  --json >/dev/null 2>&1 || true

if [[ -n "$FAILURE_PAYLOAD" ]]; then
  "${CLI[@]}" capture append-event \
    --bridge-id claude \
    --conversation-id "$CONVERSATION_ID" \
    --episode-id "$EPISODE_ID" \
    --type failure \
    --payload-json "$FAILURE_PAYLOAD" \
    --json >/dev/null 2>&1 || true
fi

exit 0
