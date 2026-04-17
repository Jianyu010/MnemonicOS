#!/usr/bin/env bash
# mnemonic_precompact_dump.sh — Claude Code PreCompact hook
#
# Dumps the pending summary to brain/raw/sessions/ before compaction discards
# conversation context. If no pending summary exists, exits quietly without
# writing an empty placeholder entry.
#
# Exit: always 0 — never block compaction

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PENDING="/tmp/mnemo_pending_summary.md"
RAW_DIR="$REPO_ROOT/brain/raw/sessions"

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
OUTFILE="$RAW_DIR/${TIMESTAMP}-claude-precompact-${TRIGGER}.md"

mkdir -p "$RAW_DIR"

if [[ -f "$PENDING" && -s "$PENDING" ]]; then
  cp "$PENDING" "$OUTFILE"
  echo "MnemonicOS: precompact dump saved → $OUTFILE (trigger=$TRIGGER)" >&2
else
  echo "MnemonicOS: precompact dump skipped (trigger=$TRIGGER, no pending summary)" >&2
fi

exit 0
