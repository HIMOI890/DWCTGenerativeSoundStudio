#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  python3 tools/launcher_gui.py
elif command -v python >/dev/null 2>&1; then
  python tools/launcher_gui.py
else
  echo "Python not found. Install Python 3.10+."
  exit 1
fi
