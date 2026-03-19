#!/usr/bin/env bash
set -euo pipefail

echo "🎵 Enhanced Deforum Music Generator 🎥"
echo "=================================="

if [ -d "venv" ]; then
  echo "📦 Activating virtual environment..."
  # shellcheck disable=SC1091
  source "venv/bin/activate"
fi

mkdir -p data/models data/cache data/logs output/packages output/analysis output/previews

echo "🚀 Starting UI..."
python -m enhanced_deforum_music_generator ui --port 7860
