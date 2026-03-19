"""Enhanced Deforum Music Generator (EDMG) — embedded engine.

This package is vendored into EDMG Studio and used as a backend library.
Standalone UI entrypoints (Gradio/A1111 extension) are intentionally not shipped.
"""

from __future__ import annotations

from importlib import import_module

__all__ = ["DeforumMusicGenerator", "AudioAnalysis"]

_public = import_module(".public_api", __name__)

DeforumMusicGenerator = _public.DeforumMusicGenerator
AudioAnalysis = _public.AudioAnalysis
