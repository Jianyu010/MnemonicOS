#!/usr/bin/env bash
# codex_hook_canary.sh — minimal Codex hook dispatch probe.
#
# Appends the raw hook payload to /tmp/codex-hook-canary.log and exits 0.
# This intentionally avoids MnemonicOS so hook dispatch can be tested in
# isolation.

set -u

LOG_FILE="${CODEX_HOOK_CANARY_LOG:-/tmp/codex-hook-canary.log}"
PAYLOAD=$(cat 2>/dev/null) || PAYLOAD=""
TIMESTAMP=$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || printf 'unknown-time')
EVENT_NAME="${CODEX_HOOK_EVENT:-unknown}"

{
  printf '%s event=%s cwd=%s\n' "$TIMESTAMP" "$EVENT_NAME" "$PWD"
  printf '%s\n' "$PAYLOAD"
  printf '%s\n' '---'
} >>"$LOG_FILE" 2>/dev/null || true

exit 0
