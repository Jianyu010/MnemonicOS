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
# Each file carries an `agent:` header (which bridge owns it) and, when the
# session is known at write time, a `mnemo_session:` header (which session of
# that agent owns it). A Stop hook ingests:
#   - files whose mnemo_session matches its own session, always;
#   - files with no mnemo_session only when no OTHER session of the same agent
#     is active (so it cannot swallow a concurrent session's summary).
#
# The legacy single path (/tmp/mnemo_pending_summary.md) is still honoured by
# every Stop hook under the same rules (agent header matches or is absent).
#
# Hook state is per session: <prefix>_<sha256(session_id)[:16]> where prefix is
# /tmp/mnemo_hook_state (claude) or /tmp/mnemo_codex_hook_state (codex).

mnemo_pending_dir() {
  printf '%s' "${MNEMO_PENDING_DIR:-/tmp/mnemo_pending}"
}

mnemo_pending_legacy_file() {
  printf '%s' "${MNEMO_PENDING_LEGACY_FILE:-/tmp/mnemo_pending_summary.md}"
}

# State-file prefix for a bridge (where its preflight hook writes per-session state).
mnemo_state_prefix_for_bridge() {
  case "$1" in
    claude) printf '%s' "${MNEMO_CLAUDE_STATE_PREFIX:-/tmp/mnemo_hook_state}" ;;
    codex)  printf '%s' "${MNEMO_CODEX_STATE_PREFIX:-/tmp/mnemo_codex_hook_state}" ;;
    *)      printf '%s' "/tmp/mnemo_${1}_hook_state" ;;
  esac
}

# A state file untouched for longer than this is treated as a dead session
# (crashed mid-turn) rather than an active one.
MNEMO_STATE_ACTIVE_MAX_AGE_MINUTES="${MNEMO_STATE_ACTIVE_MAX_AGE_MINUTES:-360}"

_mnemo_header_value() {
  local file="$1" key="$2"
  [[ -f "$file" ]] || return 0
  grep -m1 -E "^${key}:" "$file" 2>/dev/null \
    | sed -E "s/^${key}:[[:space:]]*//" | tr -d '\r' | xargs 2>/dev/null || true
}

# Print the lower-cased value of the first `agent:` header line, or nothing.
mnemo_summary_agent() {
  _mnemo_header_value "$1" agent | tr '[:upper:]' '[:lower:]'
}

# Print the `session_date:` header value, or nothing.
mnemo_summary_session_date() {
  _mnemo_header_value "$1" session_date
}

# Print the `mnemo_session:` header value (owning session id), or nothing.
mnemo_summary_session() {
  _mnemo_header_value "$1" mnemo_session
}

# Does this pending file belong to <bridge> (agent-level ownership)?
#   - files in the pending dir: header agent == bridge, or (no header) filename prefix "<bridge>-"
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

# Print per-session state files of <bridge> that look active (recently touched),
# excluding <own_state_file>. One path per line.
mnemo_other_active_sessions() {
  local bridge="$1" own="${2:-}" prefix file
  prefix="$(mnemo_state_prefix_for_bridge "$bridge")"
  for file in "$prefix"_*; do
    [[ -f "$file" ]] || continue
    [[ -n "$own" && "$file" == "$own" ]] && continue
    # Skip stale files left by a crashed session.
    if [[ -n "$(find "$file" -mmin +"$MNEMO_STATE_ACTIVE_MAX_AGE_MINUTES" 2>/dev/null)" ]]; then
      continue
    fi
    printf '%s\n' "$file"
  done
}

# Session-level ownership. Args: bridge file session_id own_state_file.
# Returns 0 if the Stop hook of (bridge, session_id) may ingest <file>.
mnemo_pending_file_for_session() {
  local bridge="$1" file="$2" session_id="${3:-}" own_state="${4:-}"
  local owner
  owner="$(mnemo_summary_session "$file")"
  if [[ -n "$owner" ]]; then
    [[ -n "$session_id" && "$owner" == "$session_id" ]]
    return
  fi
  # Unbound file: safe to take only when no other session of this agent is
  # mid-turn; otherwise leave it for the session that wrote it.
  [[ -z "$(mnemo_other_active_sessions "$bridge" "$own_state")" ]]
}

# List (one per line) every non-empty pending file that the Stop hook of
# (bridge, session_id) should ingest, oldest first, legacy path last.
# Args: bridge [session_id] [own_state_file]
mnemo_pending_files_for_bridge() {
  local bridge="$1" session_id="${2:-}" own_state="${3:-}" dir file
  dir="$(mnemo_pending_dir)"
  if [[ -d "$dir" ]]; then
    # shellcheck disable=SC2045
    for file in $(ls -1tr "$dir" 2>/dev/null); do
      file="$dir/$file"
      [[ -f "$file" && -s "$file" && "$file" == *.md ]] || continue
      mnemo_pending_file_matches "$bridge" "$file" || continue
      mnemo_pending_file_for_session "$bridge" "$file" "$session_id" "$own_state" || continue
      printf '%s\n' "$file"
    done
  fi
  local legacy
  legacy="$(mnemo_pending_legacy_file)"
  if [[ -f "$legacy" && -s "$legacy" ]] \
     && mnemo_pending_file_matches "$bridge" "$legacy" \
     && mnemo_pending_file_for_session "$bridge" "$legacy" "$session_id" "$own_state"; then
    printf '%s\n' "$legacy"
  fi
}

# Best-effort detection of the current session id for <agent> when the caller
# did not pass one: env overrides first, then the single active state file.
mnemo_detect_session_id() {
  local agent="$1" candidates
  if [[ -n "${MNEMO_SESSION_ID:-}" ]]; then
    printf '%s' "$MNEMO_SESSION_ID"; return 0
  fi
  case "$agent" in
    claude) [[ -n "${CLAUDE_SESSION_ID:-}" ]] && { printf '%s' "$CLAUDE_SESSION_ID"; return 0; } ;;
    codex)  [[ -n "${CODEX_SESSION_ID:-${CODEX_THREAD_ID:-}}" ]] && { printf '%s' "${CODEX_SESSION_ID:-$CODEX_THREAD_ID}"; return 0; } ;;
  esac
  candidates="$(mnemo_other_active_sessions "$agent" "")"
  if [[ -n "$candidates" && "$(printf '%s\n' "$candidates" | wc -l | tr -d ' ')" == "1" ]]; then
    mnemo_state_field "$candidates" session_id
    return 0
  fi
  return 0
}

# Create a NEW pending file for <agent> from stdin (never overwrites). Prints
# the path. When <session_id> is non-empty a `mnemo_session:` header is
# prepended so only that session's Stop hook ingests the file.
# Args: agent [session_id]
mnemo_pending_create() {
  local agent="${1:-unknown}" session_id="${2:-}" dir path rand attempt body
  agent="$(printf '%s' "$agent" | tr '[:upper:]' '[:lower:]' | tr -c 'a-z0-9_\n' '-')"
  [[ -z "$agent" ]] && agent="unknown"
  dir="$(mnemo_pending_dir)"
  mkdir -p "$dir"
  body="$(mktemp "${TMPDIR:-/tmp}/mnemo_pending_body.XXXXXX")" || return 1
  if [[ -n "$session_id" ]]; then
    printf 'mnemo_session: %s\n' "$session_id" > "$body"
    cat >> "$body"
  else
    cat > "$body"
  fi
  for attempt in 1 2 3 4 5; do
    rand="$(python3 -c 'import secrets; print(secrets.token_hex(3))' 2>/dev/null || printf '%s' "$RANDOM$RANDOM")"
    path="$dir/${agent}-$(date +%Y%m%d-%H%M%S)-${rand}.md"
    # noclobber: fail instead of truncating if the name already exists.
    if ( set -o noclobber; cat "$body" > "$path" ) 2>/dev/null; then
      rm -f "$body"
      printf '%s\n' "$path"
      return 0
    fi
  done
  rm -f "$body"
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
