import numpy as np
import soundfile as sf

from enhanced_deforum_music_generator.multitrack import MultiTrackGenerator, MultitrackProcessor


def _tone(path, freq, duration=1.5, sr=22050, amp=0.5):
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    y = amp * np.sin(2 * np.pi * freq * t)
    sf.write(str(path), y, sr)


def test_multitrack_processor_and_generator(tmp_path):
    drums = tmp_path / 'drums.wav'
    bass = tmp_path / 'bass.wav'
    _tone(drums, 120.0, amp=0.6)
    _tone(bass, 55.0, amp=0.4)

    processor = MultitrackProcessor(track_weights={'drums': 0.6, 'bass': 0.4})
    result = processor.analyze({'drums': str(drums), 'bass': str(bass)})

    assert result['ok'] is True
    assert set(result['tracks']) == {'drums', 'bass'}
    assert result['combined']['duration'] > 0
    assert result['combined']['dominant_tracks'][0] in {'drums', 'bass'}

    settings = MultiTrackGenerator(processor=processor).build_deforum_settings(
        {'drums': str(drums), 'bass': str(bass)},
        user_settings={'base_prompt': 'cinematic neon city', 'style_prompt': 'volumetric light'},
    )
    assert 'prompts' in settings
    assert '_edmg_multitrack' in settings
    assert settings['_edmg_multitrack']['tracks'] == ['bass', 'drums'] or settings['_edmg_multitrack']['tracks'] == ['drums', 'bass']
