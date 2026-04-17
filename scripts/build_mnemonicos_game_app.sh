#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="${ROOT}/dist"
BUILD_DIR="${ROOT}/build"

cd "${ROOT}"

python3 -m pip install -e ".[game]"
python3 -m pip install pyinstaller

pyinstaller \
  --noconfirm \
  --windowed \
  --name MnemonicOSLibraryRPG \
  --paths "${ROOT}/src" \
  --collect-submodules mnemonicos_game \
  --collect-submodules second_brain \
  "${ROOT}/src/mnemonicos_game/main.py"

printf '\nBuilt app bundle under %s\n' "${DIST_DIR}"
printf 'Temporary build files are under %s\n' "${BUILD_DIR}"
