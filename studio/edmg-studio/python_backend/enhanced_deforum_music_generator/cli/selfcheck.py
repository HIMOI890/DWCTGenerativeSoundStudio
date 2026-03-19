"""Self-check command for EDMG.

Usage:
    python -m enhanced_deforum_music_generator selfcheck

Exit codes:
    0 - required components available
    1 - missing required components
"""

from __future__ import annotations

import importlib.util
import json
import platform
import shutil
import sys
from dataclasses import dataclass, asdict
from typing import Dict, Optional


@dataclass
class Check:
    ok: bool
    detail: str = ""


def _has_module(name: str) -> Check:
    spec = importlib.util.find_spec(name)
    if spec is None:
        return Check(False, "not installed")
    return Check(True, "installed")


def run() -> int:
    checks: Dict[str, Check] = {
        "python": Check(True, sys.version.split()[0]),
        "platform": Check(True, f"{platform.system()} {platform.release()}"),
        "fastapi": _has_module("fastapi"),
        "numpy": _has_module("numpy"),
        "librosa": _has_module("librosa"),
        "whisper": _has_module("whisper"),
        "torch": _has_module("torch"),
        "gradio": _has_module("gradio"),
        "ffmpeg": Check(shutil.which("ffmpeg") is not None, "found" if shutil.which("ffmpeg") else "missing"),
    }

    required = ["fastapi", "numpy"]
    ok = all(checks[k].ok for k in required)

    payload = {
        "ok": ok,
        "checks": {k: asdict(v) for k, v in checks.items()},
        "required": required,
    }
    print(json.dumps(payload, indent=2))
    return 0 if ok else 1
