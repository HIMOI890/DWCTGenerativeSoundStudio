import pathlib
import sys

_BACKEND = pathlib.Path(__file__).resolve().parents[2]
_ROOT = pathlib.Path(__file__).resolve().parents[5]

for _p in (_BACKEND, _ROOT / 'src', _ROOT):
    _p_str = str(_p)
    if _p.exists() and _p_str not in sys.path:
        sys.path.insert(0, _p_str)

try:
    import librosa  # type: ignore
    import soundfile as sf  # type: ignore

    def _write_wav(path: str, y, sr: int, norm: bool = False) -> None:
        sf.write(path, y, sr)

    if not hasattr(librosa, 'output'):
        class _Output:
            pass
        librosa.output = _Output()  # type: ignore[attr-defined]
    if not hasattr(librosa.output, 'write_wav'):
        librosa.output.write_wav = _write_wav  # type: ignore[attr-defined]
except Exception:
    pass

import pytest

@pytest.fixture(scope='session')
def test_audio_file(tmp_path_factory):
    """Provide a small dummy WAV file for tests."""
    import wave
    import numpy as np

    path = tmp_path_factory.mktemp('data') / 'test.wav'
    framerate = 44100
    duration = 1
    amplitude = 16000
    freq = 440.0
    t = np.linspace(0, duration, int(framerate * duration))
    signal = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16)

    with wave.open(str(path), 'w') as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(signal.tobytes())

    return str(path)
