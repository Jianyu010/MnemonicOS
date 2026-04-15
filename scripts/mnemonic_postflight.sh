#!/usr/bin/env bash
# mnemonic_postflight.sh — archive-first postflight intake

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRIDGE_ID=""
SESSION_ID=""
SUMMARY_FILE=""
PROMOTE_NOW=0
TAGS=()
SOURCE_REFS=()
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --bridge-id)    BRIDGE_ID="$2"; shift 2 ;;
    --session-id)   SESSION_ID="$2"; shift 2 ;;
    --agent)        BRIDGE_ID="$2"; shift 2 ;;
    --slug)         SESSION_ID="$2"; shift 2 ;;
    --summary-file) SUMMARY_FILE="$2"; shift 2 ;;
    --tag)          TAGS+=("$2"); shift 2 ;;
    --source-ref)   SOURCE_REFS+=("$2"); shift 2 ;;
    --promote-now)  PROMOTE_NOW=1; shift ;;
    --dry-run)      DRY_RUN=1; shift ;;
    *)              echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$BRIDGE_ID" || -z "$SESSION_ID" || -z "$SUMMARY_FILE" ]]; then
  echo "Usage: mnemonic_postflight.sh --bridge-id <id> --session-id <id> --summary-file <path> [--promote-now]" >&2
  exit 1
fi

if [[ ! -f "$SUMMARY_FILE" ]]; then
  echo "Error: summary file not found: $SUMMARY_FILE" >&2
  exit 1
fi

if [[ -f "$REPO_ROOT/config/memory.toml" ]]; then
  CONFIG="$REPO_ROOT/config/memory.toml"
else
  CONFIG="$REPO_ROOT/config/memory.example.toml"
fi

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

cd "$REPO_ROOT"
INGEST_CMD=("${CLI[@]}" ingest-session --bridge-id "$BRIDGE_ID" --session-id "$SESSION_ID" --file "$SUMMARY_FILE")
for tag in "${TAGS[@]+"${TAGS[@]}"}"; do
  INGEST_CMD+=(--tag "$tag")
done
for ref in "${SOURCE_REFS[@]+"${SOURCE_REFS[@]}"}"; do
  INGEST_CMD+=(--source-ref "$ref")
done
if [[ $PROMOTE_NOW -eq 1 ]]; then
  INGEST_CMD+=(--promote-now)
fi

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[DRY RUN] Would run: ${INGEST_CMD[*]}"
  if [[ $PROMOTE_NOW -eq 1 ]]; then
    echo "[DRY RUN] Deferred processing bypass enabled."
  else
    echo "[DRY RUN] Session will be archived and queued for daemon/background promotion."
  fi
  exit 0
fi

echo "=== MnemonicOS: Ingesting session summary ==="
"${INGEST_CMD[@]}"

if [[ $PROMOTE_NOW -eq 1 ]]; then
  echo ""
  echo "=== MnemonicOS: Immediate promotion requested ==="
fi
