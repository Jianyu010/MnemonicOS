#!/usr/bin/env bash
# codex_notify_bridge.sh — Codex Desktop notify wrapper.
#
# Imports the latest Codex transcript delta into MnemonicOS raw capture.
#
# Earlier versions of this wrapper also launched the bundled Computer Use
# SkyComputerUseClient to preserve Codex's default turn-ended notification.
# That helper has parent launch constraints and can be killed by macOS if it is
# started from this shell wrapper, producing a noisy code-signing crash dialog.

set -u

LOG_FILE="${MNEMO_CODEX_NOTIFY_WRAPPER_LOG:-/tmp/mnemo_codex_notify_wrapper.log}"

{
  printf '%s args=' "$(date '+%Y-%m-%dT%H:%M:%S%z' 2>/dev/null || printf unknown-time)"
  printf '%q ' "$@"
  printf '\n'
} >>"$LOG_FILE" 2>/dev/null || true

if [[ "${MNEMO_CODEX_NOTIFY_RUN_ORIGINAL:-0}" == "1" && -n "${MNEMO_CODEX_NOTIFY_ORIGINAL:-}" && -x "${MNEMO_CODEX_NOTIFY_ORIGINAL:-}" ]]; then
  ORIGINAL_NOTIFY="$MNEMO_CODEX_NOTIFY_ORIGINAL" /Users/jianyulong/.pyenv/versions/3.11.2/bin/python3 - "$@" <<'PY' >/dev/null 2>&1 || true
import os
import subprocess
import sys

subprocess.Popen(
    [os.environ["ORIGINAL_NOTIFY"], *sys.argv[1:]],
    stdin=subprocess.DEVNULL,
    stdout=subprocess.DEVNULL,
    stderr=subprocess.DEVNULL,
    start_new_session=True,
)
PY
fi

if [[ "${1:-}" == "turn-ended" ]]; then
  env PYTHONPATH="/Users/jianyulong/ai_memory/src" \
    /Users/jianyulong/.pyenv/versions/3.11.2/bin/python3 /Users/jianyulong/ai_memory/scripts/mnemonic_codex_notify_bridge.py "$@" \
    >/dev/null 2>&1 || true
fi

exit 0
