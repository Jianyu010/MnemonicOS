#!/usr/bin/env bash
# install_codex_skill.sh — copy the repo-local MnemonicOS skill into the user's Codex skill directory

set -euo pipefail

SCRIPT_PATH="${BASH_SOURCE[0]:-$0}"
if [[ "$SCRIPT_PATH" != /* ]]; then
  SCRIPT_PATH="$(pwd)/$SCRIPT_PATH"
fi
REPO_ROOT="$(cd "$(dirname "$SCRIPT_PATH")/.." && pwd)"
SOURCE_DIR="$REPO_ROOT/codex_skills/mnemonicos-bridge"
CODEX_HOME_DIR="${CODEX_HOME:-$HOME/.codex}"
TARGET_DIR="$CODEX_HOME_DIR/skills/mnemonicos-bridge"

if [[ ! -d "$SOURCE_DIR" ]]; then
  echo "Error: skill source not found at $SOURCE_DIR" >&2
  exit 1
fi

mkdir -p "$CODEX_HOME_DIR/skills"
rm -rf "$TARGET_DIR"
cp -R "$SOURCE_DIR" "$TARGET_DIR"

echo "Installed MnemonicOS Codex skill to $TARGET_DIR"
