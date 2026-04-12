#!/usr/bin/env bash
# mnemonic_stop_hook.sh — Claude Code Stop hook
#
# When Claude ends a session and has prepared a summary, this hook ingests it.
# Claude writes a summary to /tmp/mnemo_pending_summary.md when writeback is warranted.
# This hook picks it up, ingests it, and cleans up.
#
# Exit: always 0 — never block session close

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS="$REPO_ROOT/scripts"
PENDING="/tmp/mnemo_pending_summary.md"

# No summary prepared — nothing to do
if [[ ! -f "$PENDING" || ! -s "$PENDING" ]]; then
  exit 0
fi

# Derive slug from current timestamp
SLUG="claude-$(date +%Y-%m-%d-%H%M%S)"

# Extract agent field from summary header if present (optional, falls back to 'claude')
AGENT=$(grep -m1 '^agent:' "$PENDING" 2>/dev/null | sed 's/^agent:[[:space:]]*//' | tr -d '\r') || AGENT=""
[[ -z "$AGENT" ]] && AGENT="claude"

# Extract session_date if present (optional)
SESSION_DATE=$(grep -m1 '^session_date:' "$PENDING" 2>/dev/null | sed 's/^session_date:[[:space:]]*//' | tr -d '\r') || SESSION_DATE=""
if [[ -n "$SESSION_DATE" ]]; then
  SLUG="claude-${SESSION_DATE}-$(date +%H%M%S)"
fi

echo "MnemonicOS Stop hook: ingesting session summary as $AGENT/$SLUG" >&2

"$SCRIPTS/mnemonic_postflight.sh" \
  --agent "$AGENT" \
  --slug "$SLUG" \
  --summary-file "$PENDING" \
  --tag "auto-stop-hook" \
  2>&1 | sed 's/^/[mnemo] /' >&2

# Clean up pending file whether or not ingest succeeded
rm -f "$PENDING"

exit 0
