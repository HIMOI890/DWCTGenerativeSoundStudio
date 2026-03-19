from __future__ import annotations

from typing import Any


def lightweight_audio_features(path: str) -> dict[str, Any]:
    """Optional audio features.

    Imports are lazy so core installs stay lightweight.

    Install:
      pip install -e ".[audio]"
    """
    try:
        import librosa  # type: ignore
        import numpy as np  # type: ignore
    except Exception as e:
        raise RuntimeError("Audio features require optional deps: pip install -e '.[audio]'") from e

    y, sr = librosa.load(path, sr=None, mono=True)
    tempo, _ = librosa.beat.beat_track(y=y, sr=sr)
    rms = float(np.mean(librosa.feature.rms(y=y)))
    centroid = float(np.mean(librosa.feature.spectral_centroid(y=y, sr=sr)))
    duration = float(librosa.get_duration(y=y, sr=sr))

    return {
        "duration_s": duration,
        "bpm": float(tempo),
        "rms": rms,
        "spectral_centroid": centroid,
    }
