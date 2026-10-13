#!/usr/bin/env bash
# mnemonic_pending.sh — shared helpers for pending-summary files and hook state.
#
# Source this file; do not execute it.
#
# Pending summaries live in a directory, one NEW file per writeback, so a
# Claude session and a Codex session (or two sessions of the same agent) can
# never overwrite each other's not-yet-ingested summary (issue #37):
#
#   ${MNEMO_PENDING_DIR:-/tmp/mnemo_pending}/<agent>-<YYYYmmdd-HHMMSS>-<rand>.md
#
# The legacy single path (/tmp/mnemo_pending_summary.md) is still honoured by
# every Stop hook, but only when its `agent:` header matches the bridge running
# the hook (or it has no header at all).
#
# Hook state is per session: /tmp/mnemo_hook_state_<sha256(session_id)[:16]>.

mnemo_pending_dir() {
  printf '%s' "${MNEMO_PENDING_DIR:-/tmp/mnemo_pending}"
}

mnemo_pending_legacy_file() {
  printf '%s' "${MNEMO_PENDING_LEGACY_FILE:-/tmp/mnemo_pending_summary.md}"
}

# Print the lower-cased value of the first `agent:` header line, or nothing.
mnemo_summary_agent() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  grep -m1 -E '^agent:' "$file" 2>/dev/null \
    | sed -E 's/^agent:[[:space:]]*//' | tr -d '\r' | tr '[:upper:]' '[:lower:]' | xargs 2>/dev/null || true
}

# Print the `session_date:` header value, or nothing.
mnemo_summary_session_date() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  grep -m1 -E '^session_date:' "$file" 2>/dev/null \
    | sed -E 's/^session_date:[[:space:]]*//' | tr -d '\r' | xargs 2>/dev/null || true
}

# Does this pending file belong to <bridge>?
#   - files in the pending dir: filename prefix "<bridge>-" or header agent == bridge
#   - legacy file: header agent == bridge, or no header (historical behaviour)
mnemo_pending_file_matches() {
  local bridge="$1" file="$2"
  local base header
  base="$(basename "$file")"
  header="$(mnemo_summary_agent "$file")"
  if [[ "$file" == "$(mnemo_pending_legacy_file)" ]]; then
    [[ -z "$header" || "$header" == "$bridge" ]]
    return
  fi
  if [[ -n "$header" ]]; then
    [[ "$header" == "$bridge" ]]
    return
  fi
  [[ "$base" == "$bridge-"* ]]
}

# List (one per line) every non-empty pending file owned by <bridge>, oldest
# first, legacy path last.
mnemo_pending_files_for_bridge() {
  local bridge="$1" dir file
  dir="$(mnemo_pending_dir)"
  if [[ -d "$dir" ]]; then
    # shellcheck disable=SC2045
    for file in $(ls -1tr "$dir" 2>/dev/null); do
      file="$dir/$file"
      [[ -f "$file" && -s "$file" && "$file" == *.md ]] || continue
      [[ "$file" == *.partial ]] && continue
      mnemo_pending_file_matches "$bridge" "$file" && printf '%s\n' "$file"
    done
  fi
  local legacy
  legacy="$(mnemo_pending_legacy_file)"
  if [[ -f "$legacy" && -s "$legacy" ]] && mnemo_pending_file_matches "$bridge" "$legacy"; then
    printf '%s\n' "$legacy"
  fi
}

# Create a NEW pending file for <agent> from stdin (never overwrites). Prints
# the path. A second call in the same second gets a different random suffix.
mnemo_pending_create() {
  local agent="${1:-unknown}" dir path rand attempt
  agent="$(printf '%s' "$agent" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_\n' '-')"
  [[ -z "$agent" ]] && agent="unknown"
  dir="$(mnemo_pending_dir)"
  mkdir -p "$dir"
  for attempt in 1 2 3 4 5; do
    rand="$(python3 -c 'import secrets; print(secrets.token_hex(3))' 2>/dev/null || printf '%s' "$RANDOM$RANDOM")"
    path="$dir/${agent}-$(date +%Y%m%d-%H%M%S)-${rand}.md"
    # noclobber: fail instead of truncating if the name already exists.
    if ( set -o noclobber; cat > "$path" ) 2>/dev/null; then
      printf '%s\n' "$path"
      return 0
    fi
  done
  echo "mnemonic_pending: could not create a unique pending file in $dir" >&2
  return 1
}

# Per-session hook state path: <prefix>_<sha256(session_id)[:16]>, or <prefix>
# when no session id is known.
mnemo_state_file_for_session() {
  local prefix="$1" session_id="${2:-}"
  if [[ -z "$session_id" ]]; then
    printf '%s' "$prefix"
    return 0
  fi
  local digest
  digest="$(SESSION_ID="$session_id" python3 -c 'import hashlib,os; print(hashlib.sha256(os.environ["SESSION_ID"].encode()).hexdigest()[:16])' 2>/dev/null || true)"
  if [[ -z "$digest" ]]; then
    printf '%s' "$prefix"
  else
    printf '%s_%s' "$prefix" "$digest"
  fi
}

# Read one JSON string field from a hook state file; prints nothing on error.
mnemo_state_field() {
  local file="$1" field="$2"
  [[ -f "$file" ]] || return 0
  python3 - "$file" "$field" <<'PY' 2>/dev/null || true
import json, sys
try:
    with open(sys.argv[1], encoding="utf-8") as handle:
        raw = handle.read().strip()
    try:
        data = json.loads(raw)
    except Exception:
        # Legacy state file: bare task_id on one line.
        print(raw if sys.argv[2] == "task_id" else "")
        raise SystemExit(0)
    value = data.get(sys.argv[2], "") if isinstance(data, dict) else ""
    print(value if isinstance(value, str) else "")
except Exception:
    print("")
PY
}
