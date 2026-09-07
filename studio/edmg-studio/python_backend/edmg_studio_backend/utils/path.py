from __future__ import annotations

import os
from pathlib import Path, PureWindowsPath
from urllib.parse import unquote
import re


def safe_join(base: Path, rel: str) -> Path:
    """Join an untrusted relative path beneath *base* without allowing escape."""
    probe = str(rel)
    for _ in range(4):
        if re.search(r"%(?![0-9a-fA-F]{2})", probe):
            raise ValueError("Malformed path encoding")
        if (not probe or "\x00" in probe or PureWindowsPath(probe).drive
                or probe.startswith(("/", "\\")) or ".." in probe.replace("\\", "/").split("/")):
            raise ValueError("Unsafe path")
        decoded = unquote(probe, errors="strict")
        if decoded == probe:
            break
        probe = decoded
    base_path = os.path.realpath(os.fspath(base))
    candidate = os.path.realpath(os.path.join(base_path, rel))
    if candidate != base_path and not candidate.startswith(base_path + os.sep):
        raise ValueError("Unsafe path")
    return Path(candidate)
