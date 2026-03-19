"""Source-tree compatibility shim for the vendored EDMG engine.

The canonical implementation lives under
``studio/edmg-studio/python_backend/enhanced_deforum_music_generator``.
This shim makes ``python -m enhanced_deforum_music_generator`` and direct
imports work from the repo root without requiring an editable install.
"""

from __future__ import annotations

import sys as _sys
from pathlib import Path as _Path

_PKG_DIR = _Path(__file__).resolve().parent
_REPO_ROOT = _PKG_DIR.parents[1]
_BACKEND_ROOT = _REPO_ROOT / 'studio' / 'edmg-studio' / 'python_backend'
_REAL_PKG = _BACKEND_ROOT / 'enhanced_deforum_music_generator'

if not _REAL_PKG.exists():
    raise ImportError(
        'Vendored EDMG engine was not found at '
        f'{_REAL_PKG}. Make sure the full repository is present.'
    )

_backend_str = str(_BACKEND_ROOT)
if _backend_str not in _sys.path:
    _sys.path.insert(0, _backend_str)

__path__ = [str(_REAL_PKG), str(_PKG_DIR)]  # type: ignore[name-defined]
try:
    __spec__.submodule_search_locations[:] = __path__  # type: ignore[attr-defined]
except Exception:
    pass

from .public_api import AudioAnalysis, DeforumMusicGenerator

__all__ = ['AudioAnalysis', 'DeforumMusicGenerator']
