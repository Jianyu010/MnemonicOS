#!/usr/bin/env bash
# mnemonic_pending_summary.sh — queue a writeback summary for the Stop hook.
#
# Writes the summary to a NEW file under the pending directory so it can never
# overwrite another agent's (or another session's) not-yet-ingested summary.
#
# Usage:
#   mnemonic_pending_summary.sh [--agent claude|codex|...] [--file <path>] [--print-dir]
#
#   --agent   Owner of the summary. Defaults to the `agent:` header of the
#             content, then to $MNEMO_AGENT, then to "unknown".
#   --file    Read the summary from <path> instead of stdin.
#   --print-dir  Print the pending directory and exit.
#
# Prints the created path on stdout. Exit 0 on success, 1 on error.

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib/mnemonic_pending.sh
source "$SCRIPT_DIR/lib/mnemonic_pending.sh"

AGENT=""
SOURCE_FILE=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --agent)     AGENT="$2"; shift 2 ;;
    --file)      SOURCE_FILE="$2"; shift 2 ;;
    --print-dir) mnemo_pending_dir; echo; exit 0 ;;
    -h|--help)   sed -n '2,16p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *)           echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

TMP="$(mktemp "${TMPDIR:-/tmp}/mnemo_pending_input.XXXXXX")" || exit 1
trap 'rm -f "$TMP"' EXIT
if [[ -n "$SOURCE_FILE" ]]; then
  if [[ ! -f "$SOURCE_FILE" ]]; then
    echo "Error: summary file not found: $SOURCE_FILE" >&2
    exit 1
  fi
  cat "$SOURCE_FILE" > "$TMP"
else
  cat > "$TMP"
fi

if [[ ! -s "$TMP" ]]; then
  echo "Error: empty summary; nothing queued" >&2
  exit 1
fi

if [[ -z "$AGENT" ]]; then
  AGENT="$(mnemo_summary_agent "$TMP")"
fi
AGENT="${AGENT:-${MNEMO_AGENT:-unknown}}"

mnemo_pending_create "$AGENT" < "$TMP"
