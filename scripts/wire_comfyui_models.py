#!/usr/bin/env python3
"""scripts/wire_comfyui_models.py

Thin wrapper around the package CLI for wiring models into ComfyUI.
"""
from __future__ import annotations

import sys
from enhanced_deforum_music_generator.cli.comfyui_wire import run_cli

if __name__ == "__main__":
    raise SystemExit(run_cli(sys.argv[1:]))
