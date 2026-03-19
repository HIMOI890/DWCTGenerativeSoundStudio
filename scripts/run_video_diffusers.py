#!/usr/bin/env python3
"""Thin wrapper around `python -m enhanced_deforum_music_generator.cli.video_diffusers`.

This is kept for convenience when running from repo root without installation.
"""
from enhanced_deforum_music_generator.cli.video_diffusers import main

if __name__ == "__main__":
    raise SystemExit(main())
