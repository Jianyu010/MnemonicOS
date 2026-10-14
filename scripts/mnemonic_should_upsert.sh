#!/usr/bin/env bash
# mnemonic_should_upsert.sh — decide whether a summary should create, update, confirm, or skip notes

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SUMMARY_FILE=""
MODE=""
EMIT_JSON=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --summary-file) SUMMARY_FILE="$2"; shift 2 ;;
    --mode)         MODE="$2"; shift 2 ;;
    --json)         EMIT_JSON=1; shift ;;
    *)              echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

if [[ -z "$SUMMARY_FILE" ]]; then
  echo "Usage: mnemonic_should_upsert.sh --summary-file <path> [--mode failure|success|knowledge] [--json]" >&2
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
# Test/agent isolation: explicit config path overrides repo default.
CONFIG="${MNEMO_CONFIG:-$CONFIG}"

env PYTHONPATH="$REPO_ROOT/src" python3 - "$CONFIG" "$SUMMARY_FILE" "$MODE" "$EMIT_JSON" <<'PYEOF'
import json
import sys
from pathlib import Path

from second_brain.bridge import (
    candidate_queries_from_summary,
    decide_upsert,
    decision_to_json,
    infer_summary_mode,
    parse_summary,
)
from second_brain.config import load_config
from second_brain.db import connect_db, run_migrations

config_path = Path(sys.argv[1])
summary_path = Path(sys.argv[2])
mode_arg = sys.argv[3].strip().casefold()
emit_json = sys.argv[4] == "1"

config = load_config(config_path, config_path.parent.parent)
connection = connect_db(config.paths.db_path)
run_migrations(connection, config.paths.workspace_root / "migrations")

text = summary_path.read_text(encoding="utf-8")
summary = parse_summary(text)
mode = mode_arg or infer_summary_mode(summary)
queries = candidate_queries_from_summary(summary)
targets = []
title = ""
note_type = "concept" if mode == "knowledge" else ""
body = ""
if summary.memory_items:
    first_item = summary.memory_items[0]
    title = str(first_item.get("title", "")).strip()
    note_type = str(first_item.get("type", note_type) or note_type).strip()
    body = str(first_item.get("body", "") or first_item.get("summary", "")).strip()
if not title:
    title = (queries[0] if queries else summary.sections.get("Task", "").strip()) or "session-summary"
if not body:
    body = summary.sections.get("Reusable Takeaways", "") or summary.sections.get("Final Approach", "") or title

decision = decide_upsert(
    connection,
    config,
    summary=summary,
    note_type=note_type,
    title=title,
    summary_text=body,
)
connection.close()

payload = {
    "mode": mode,
    "decision": decision.decision,
    "targets": decision.targets,
    "candidates": decision.candidates,
    "reason": decision.reason,
    "queries": queries,
    "title": title,
    "note_type": note_type,
}
if emit_json:
    print(json.dumps(payload, indent=2))
else:
    print(f"decision={decision.decision}")
    print(f"mode={mode}")
    print(f"title={title}")
    print(f"note_type={note_type}")
    if decision.targets:
      print(f"targets={','.join(decision.targets)}")
    if decision.candidates:
      print(f"candidates={','.join(decision.candidates)}")
    print(f"reason={decision.reason}")
PYEOF
