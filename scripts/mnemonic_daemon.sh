#!/usr/bin/env bash
# mnemonic_daemon.sh — thin wrapper around the MnemonicOS daemon CLI

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONFIG="$REPO_ROOT/config/memory.toml"

if [[ ! -f "$CONFIG" ]]; then
  echo "Error: config not found at $CONFIG" >&2
  exit 1
fi

if command -v mnemonicos >/dev/null 2>&1; then
  exec mnemonicos --config "$CONFIG" daemon "$@"
else
  exec env PYTHONPATH="$REPO_ROOT/src" python3 -m second_brain --config "$CONFIG" daemon "$@"
fi
