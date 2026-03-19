"""
sitecustomize.py

Python automatically imports `sitecustomize` on interpreter startup (if found on
`sys.path`). This repo uses it for two reasons:

1) Make `src/` importable without requiring an editable install, so commands like:
     python -m enhanced_deforum_music_generator ui
   work immediately after extracting/cloning.

2) Provide small compatibility shims for third-party libraries used in tests and
   lightweight CPU-only environments.

   - If `librosa` fails to import (common when an old librosa is paired with a
     new NumPy), we inject a minimal stub that supports the functions EDMG tests
     require (`load`, `beat.beat_track`, `frames_to_time`, `output.write_wav`).
   - If `librosa` imports successfully but is missing `output.write_wav`, we add it.
   - `whisper` (openai-whisper) is optional; tests monkeypatch `whisper.load_model`.

These shims do *not* override real installations. They only fill missing symbols
or provide a fallback when the import fails.
"""

from __future__ import annotations

import sys
from pathlib import Path
import types


def _ensure_src_on_path() -> None:
    repo_root = Path(__file__).resolve().parent
    src_dir = repo_root / "src"
    if src_dir.exists():
        src_str = str(src_dir)
        if src_str not in sys.path:
            sys.path.insert(0, src_str)




def _ensure_studio_backend_on_path() -> None:
    repo_root = Path(__file__).resolve().parent
    backend_dir = repo_root / "studio" / "edmg-studio" / "python_backend"
    if backend_dir.exists():
        backend_str = str(backend_dir)
        if backend_str not in sys.path:
            sys.path.insert(0, backend_str)

def _ensure_librosa() -> None:
    """
    Ensure `import librosa` works.

    - If librosa imports: patch `librosa.output.write_wav` if missing.
    - If librosa import fails: inject a minimal stub module.
    """
    try:
        import librosa  # type: ignore
    except Exception:
        _inject_librosa_stub()
        return

    # librosa imported: patch output.write_wav for legacy tests
    try:
        import soundfile as sf  # type: ignore
    except Exception:
        sf = None  # type: ignore

    def write_wav(path: str, y, sr: int, norm: bool = False) -> None:
        if sf is None:
            raise RuntimeError("soundfile is required to write wav files.")
        sf.write(path, y, sr)

    if not hasattr(librosa, "output"):
        librosa.output = types.SimpleNamespace(write_wav=write_wav)  # type: ignore[attr-defined]
        return

    out = getattr(librosa, "output")
    if not hasattr(out, "write_wav"):
        try:
            setattr(out, "write_wav", write_wav)
        except Exception:
            librosa.output = types.SimpleNamespace(write_wav=write_wav)  # type: ignore[attr-defined]


def _inject_librosa_stub() -> None:
    import numpy as np

    try:
        import soundfile as sf  # type: ignore
    except Exception:
        sf = None  # type: ignore

    librosa = types.ModuleType("librosa")

    def load(path: str, sr=None, duration=None, mono: bool = True):
        if sf is None:
            raise RuntimeError("soundfile is required for librosa stub (install soundfile).")
        y, native_sr = sf.read(path, always_2d=False)
        if y is None:
            y = np.zeros(int((sr or 22050) * 1.0), dtype=np.float32)
            native_sr = int(sr or 22050)

        y = np.asarray(y, dtype=np.float32)
        if y.ndim > 1 and mono:
            y = np.mean(y, axis=1)

        if duration is not None:
            max_len = int(float(duration) * float(native_sr))
            y = y[:max_len]

        target_sr = native_sr if sr is None else int(sr)
        if target_sr != int(native_sr) and len(y) > 1:
            # Minimal linear resample (good enough for tests).
            x_old = np.linspace(0.0, 1.0, num=len(y), endpoint=False, dtype=np.float32)
            new_len = int(len(y) * (float(target_sr) / float(native_sr)))
            x_new = np.linspace(0.0, 1.0, num=max(1, new_len), endpoint=False, dtype=np.float32)
            y = np.interp(x_new, x_old, y).astype(np.float32)

        return y, int(target_sr)

    beat_mod = types.ModuleType("librosa.beat")

    def beat_track(y=None, sr: int = 22050):
        # Minimal: return a plausible tempo and no beats.
        return 120.0, np.asarray([], dtype=np.int32)

    beat_mod.beat_track = beat_track  # type: ignore[attr-defined]

    def frames_to_time(frames, sr: int = 22050, hop_length: int = 512):
        frames = np.asarray(frames, dtype=np.float32)
        return frames * (float(hop_length) / float(sr))

    output_mod = types.SimpleNamespace()

    def write_wav(path: str, y, sr: int, norm: bool = False) -> None:
        if sf is None:
            raise RuntimeError("soundfile is required to write wav files.")
        sf.write(path, y, sr)

    output_mod.write_wav = write_wav

    librosa.load = load  # type: ignore[attr-defined]
    librosa.frames_to_time = frames_to_time  # type: ignore[attr-defined]
    librosa.beat = beat_mod  # type: ignore[attr-defined]
    librosa.output = output_mod  # type: ignore[attr-defined]
    librosa.__version__ = "0.0-stub"

    sys.modules["librosa"] = librosa
    sys.modules["librosa.beat"] = beat_mod


def _ensure_whisper_stub() -> None:
    if "whisper" in sys.modules:
        return
    try:
        import whisper  # noqa: F401
        return
    except Exception:
        pass

    mod = types.ModuleType("whisper")

    class _MissingWhisperModel:
        def transcribe(self, *args, **kwargs):
            raise RuntimeError("Whisper is not installed. Install `openai-whisper` to use transcription.")

    def load_model(model_name: str = "base"):
        return _MissingWhisperModel()

    mod.load_model = load_model  # type: ignore[attr-defined]
    sys.modules["whisper"] = mod


_ensure_src_on_path()
_ensure_studio_backend_on_path()
_ensure_librosa()
_ensure_whisper_stub()
