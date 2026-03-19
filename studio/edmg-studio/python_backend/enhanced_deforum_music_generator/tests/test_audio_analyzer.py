import pytest
import numpy as np
from core.audio_analyzer import AudioAnalyzer
from config.config_system import AudioConfig
import librosa


def test_audio_analyzer_runs_on_sample(tmp_path):
    # Generate a synthetic sine wave as test audio
    sr = 22050
    duration = 2.0
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    y = 0.5 * np.sin(2 * np.pi * 440.0 * t)  # 440 Hz tone

    audio_path = tmp_path / "test.wav"
    librosa.output.write_wav(str(audio_path), y, sr)

    analyzer = AudioAnalyzer(AudioConfig(max_duration=5))
    results = analyzer.analyze(str(audio_path))

    assert "beats" in results
    assert "energy" in results
    assert isinstance(results["energy"], list)
