"""
EDMG whisper compatibility shim.

- If `openai-whisper` is installed and importable, load it in-place.
- Otherwise provide a minimal stub with `load_model()` returning an object that
  raises on `.transcribe()`.

This keeps unit tests patchable and avoids hard failures in CPU-only setups.
"""

from __future__ import annotations

import sys
from pathlib import Path
import importlib.machinery
import types


_THIS_FILE = Path(__file__).resolve()
_REPO_ROOT = _THIS_FILE.parent


def _find_real_spec() -> importlib.machinery.ModuleSpec | None:
    paths = []
    for p in sys.path:
        try:
            if Path(p).resolve() == _REPO_ROOT.resolve():
                continue
        except Exception:
            pass
        paths.append(p)
    return importlib.machinery.PathFinder.find_spec("whisper", paths)


def _try_load_real() -> bool:
    spec = _find_real_spec()
    if not spec or not spec.loader:
        return False
    try:
        if spec.origin and Path(spec.origin).resolve() == _THIS_FILE.resolve():
            return False
    except Exception:
        pass

    try:
        this_mod = sys.modules.get(__name__)
        if this_mod is None:
            return False
        this_mod.__spec__ = spec  # type: ignore[attr-defined]
        this_mod.__file__ = spec.origin  # type: ignore[attr-defined]
        if spec.submodule_search_locations:
            this_mod.__path__ = list(spec.submodule_search_locations)  # type: ignore[attr-defined]
        spec.loader.exec_module(this_mod)  # type: ignore[arg-type]
        return True
    except Exception:
        return False


if not _try_load_real():
    class _MissingWhisperModel:
        def transcribe(self, *args, **kwargs):
            raise RuntimeError("Whisper is not installed. Install `openai-whisper` to use transcription.")

    def load_model(model_name: str = "base"):
        return _MissingWhisperModel()

    __all__ = ["load_model"]
