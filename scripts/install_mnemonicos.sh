#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

cd "${ROOT}"

python3 -m pip install -e ".[game]"

printf '\nMnemonicOS and the Library RPG are installed.\n'
printf 'Available commands:\n'
printf '  - mnemonicos\n'
printf '  - mnemonicos-daemon\n'
printf '  - mnemonicos-game\n'
printf '\nQuick check:\n'
printf '  mnemonicos-game --check --root %s\n' "${ROOT}"
