from __future__ import annotations
from pathlib import Path

def safe_join(base: Path, rel: str) -> Path:
    p = (base / rel).resolve()
    b = base.resolve()
    if p != b and b not in p.parents:
        raise ValueError("Unsafe path")
    return p
