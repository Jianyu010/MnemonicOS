#!/usr/bin/env bash
# mnemonic_precompact_dump.sh — Claude Code PreCompact hook
#
# Dumps every claude-owned pending summary to brain/raw/sessions/ before
# compaction discards conversation context: the new-style files under
# $MNEMO_PENDING_DIR (default /tmp/mnemo_pending) and the legacy
# /tmp/mnemo_pending_summary.md. Other agents' summaries are not touched.
# If nothing is pending, exits quietly without writing a placeholder entry.
#
# Exit: always 0 — never block compaction

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
# shellcheck source=lib/mnemonic_pending.sh
source "$REPO_ROOT/scripts/lib/mnemonic_pending.sh"
BRIDGE_ID="claude"

# Honour MNEMO_CONFIG: the raw directory lives beside the config's workspace.
if [[ -n "${MNEMO_CONFIG:-}" ]]; then
  WORKSPACE_ROOT="$(cd "$(dirname "$MNEMO_CONFIG")/.." 2>/dev/null && pwd || printf '%s' "$REPO_ROOT")"
else
  WORKSPACE_ROOT="$REPO_ROOT"
fi
RAW_DIR="$WORKSPACE_ROOT/brain/raw/sessions"

# --- MnemonicOS enable/disable switch ---
MNEMO_ENABLED="${MNEMO_ENABLED:-1}"
DISABLE_FLAG="$REPO_ROOT/config/mnemo_disabled"
if [[ "$MNEMO_ENABLED" == "0" || -f "$DISABLE_FLAG" ]]; then
  exit 0
fi
# ----------------------------------------

HOOK_JSON=$(cat 2>/dev/null) || HOOK_JSON="{}"
TRIGGER=$(echo "$HOOK_JSON" | python3 -c \
  "import sys,json; d=json.load(sys.stdin); print(d.get('trigger','auto'))" 2>/dev/null) \
  || TRIGGER="auto"

TIMESTAMP=$(date +%Y-%m-%d-%H%M%S)
COUNT=0
while IFS= read -r pending; do
  [[ -z "$pending" ]] && continue
  mkdir -p "$RAW_DIR"
  COUNT=$((COUNT + 1))
  OUTFILE="$RAW_DIR/${TIMESTAMP}-claude-precompact-${TRIGGER}-${COUNT}.md"
  cp "$pending" "$OUTFILE"
  echo "MnemonicOS: precompact dump saved → $OUTFILE (trigger=$TRIGGER, from $(basename "$pending"))" >&2
done < <(mnemo_pending_files_for_bridge "$BRIDGE_ID")

if [[ "$COUNT" -eq 0 ]]; then
  echo "MnemonicOS: precompact dump skipped (trigger=$TRIGGER, no pending summary)" >&2
fi

exit 0
