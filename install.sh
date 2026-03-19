#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-full}"
BACKEND="${2:-cpu}"

# Back-compat: allow "cuda" to mean cu121
if [[ "${BACKEND}" == "cuda" ]]; then
  BACKEND="cu121"
fi

python3 scripts/edmg_installer.py install --mode "$MODE" --backend "$BACKEND" --venv venv
