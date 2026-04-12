#!/usr/bin/env bash
# mnemonic_postflight.sh — ingest a session summary and run daily consolidation

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS="$REPO_ROOT/scripts"
AGENT=""
SLUG=""
SUMMARY_FILE=""
MODE=""
TAGS=()
SOURCE_REFS=()
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)        AGENT="$2"; shift 2 ;;
    --slug)         SLUG="$2"; shift 2 ;;
    --summary-file) SUMMARY_FILE="$2"; shift 2 ;;
    --mode)         MODE="$2"; shift 2 ;;
    --tag)          TAGS+=("$2"); shift 2 ;;
    --source-ref)   SOURCE_REFS+=("$2"); shift 2 ;;
    --dry-run)      DRY_RUN=1; shift ;;
    *)              echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$AGENT" || -z "$SLUG" || -z "$SUMMARY_FILE" ]]; then
  echo "Usage: mnemonic_postflight.sh --agent <name> --slug <slug> --summary-file <path> [--mode failure|success|knowledge]" >&2
  exit 1
fi

if [[ ! -f "$SUMMARY_FILE" ]]; then
  echo "Error: summary file not found: $SUMMARY_FILE" >&2
  exit 1
fi

CONFIG="$REPO_ROOT/config/memory.toml"
if [[ ! -f "$CONFIG" ]]; then
  echo "Error: config not found at $CONFIG" >&2
  exit 1
fi

if command -v mnemonicos >/dev/null 2>&1; then
  CLI=(mnemonicos --config "$CONFIG")
else
  CLI=(env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG")
fi

UPSERT_CMD=("$SCRIPTS/mnemonic_should_upsert.sh" --summary-file "$SUMMARY_FILE")
[[ -n "$MODE" ]] && UPSERT_CMD+=(--mode "$MODE")
UPSERT_CMD+=(--json)
UPSERT_JSON="$("${UPSERT_CMD[@]}")"

TMP_SUMMARY="$(mktemp /tmp/mnemo-postflight-XXXXXX.md)"
trap 'rm -f "$TMP_SUMMARY"' EXIT

env UPSERT_JSON="$UPSERT_JSON" PYTHONPATH="$REPO_ROOT/src" python3 - "$SUMMARY_FILE" "$TMP_SUMMARY" <<'PYEOF'
import json
import os
import sys
from pathlib import Path

from second_brain.bridge import parse_summary

source = Path(sys.argv[1]).read_text(encoding="utf-8")
target = Path(sys.argv[2])
payload = json.loads(os.environ["UPSERT_JSON"])
summary = parse_summary(source)
metadata = dict(summary.metadata)
metadata.setdefault("writeback_intent", {
    "new_note": "new",
    "update_existing": "update",
    "confirm_existing": "confirm",
    "skip_duplicate": "skip",
    "needs_review": "update",
}.get(payload["decision"], "new"))
metadata.setdefault("existing_note_candidates", ", ".join(payload.get("candidates", [])))
metadata.setdefault("update_target_ids", ", ".join(payload.get("targets", [])))

header_lines = [f"{key}: {value}" for key, value in metadata.items() if str(value).strip()]

body_lines = source.splitlines()
index = 0
while index < len(body_lines):
    line = body_lines[index]
    if not line.strip():
        index += 1
        break
    if line.startswith("#"):
        break
    if ":" not in line:
        break
    index += 1

body = "\n".join(body_lines[index:]).lstrip("\n")
rendered = "\n".join(header_lines).rstrip() + "\n\n" + body
target.write_text(rendered.rstrip() + "\n", encoding="utf-8")
PYEOF

cd "$REPO_ROOT"
INGEST_CMD=("${CLI[@]}" ingest-session --agent "$AGENT" --slug "$SLUG" --file "$TMP_SUMMARY")
for tag in "${TAGS[@]+"${TAGS[@]}"}"; do
  INGEST_CMD+=(--tag "$tag")
done
for ref in "${SOURCE_REFS[@]+"${SOURCE_REFS[@]}"}"; do
  INGEST_CMD+=(--source-ref "$ref")
done

if [[ $DRY_RUN -eq 1 ]]; then
  echo "[DRY RUN] Upsert decision:"
  echo "$UPSERT_JSON"
  echo "[DRY RUN] Would run: ${INGEST_CMD[*]}"
  echo "[DRY RUN] Then: ${CLI[*]} run-job daily_consolidation --dry-run"
  exit 0
fi

echo "=== MnemonicOS: Upsert decision ==="
echo "$UPSERT_JSON"
echo ""
echo "=== MnemonicOS: Ingesting session summary ==="
"${INGEST_CMD[@]}"

echo ""
echo "=== MnemonicOS: Running daily consolidation ==="
"${CLI[@]}" run-job daily_consolidation
