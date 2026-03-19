import sys
import pathlib
_ROOT = pathlib.Path(__file__).resolve().parents[1]
_BACKEND = _ROOT / 'studio' / 'edmg-studio' / 'python_backend'
if _BACKEND.exists():
    sys.path.insert(0, str(_BACKEND))
sys.path.insert(0, str(_ROOT / 'src'))
sys.path.insert(0, str(_ROOT))

import pytest

@pytest.fixture(scope="session")
def test_audio_file(tmp_path_factory):
    """Provide a small dummy WAV file for tests."""
    import wave
    import numpy as np

    path = tmp_path_factory.mktemp("data") / "test.wav"
    framerate = 44100
    duration = 1  # 1 second
    amplitude = 16000
    freq = 440.0  # A4 tone
    t = np.linspace(0, duration, int(framerate * duration))
    signal = (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.int16)

    with wave.open(str(path), "w") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(framerate)
        wav_file.writeframes(signal.tobytes())

    return str(path)
