import json
from pathlib import Path

from enhanced_deforum_music_generator.preview_generator import PreviewGenerator
from enhanced_deforum_music_generator.public_api import AudioAnalysis


def test_preview_generator_builds_manifest_and_svg(tmp_path):
    analysis = AudioAnalysis(duration=4.0, beats=[0.5, 1.5, 2.5], energy=[0.1, 0.4, 0.8, 0.2])
    gen = PreviewGenerator(quality='draft', max_duration=10)
    preview = gen.generate_preview(analysis, prompts={0: 'wide establishing shot', 12: 'close portrait'})

    assert preview['ok'] is True
    assert preview['total_frames'] == 32
    assert any(frame['beat'] for frame in preview['frames'])

    manifest = tmp_path / 'preview.json'
    svg = tmp_path / 'preview.svg'
    gen.export_manifest(preview, manifest)
    gen.render_svg(preview, svg)

    data = json.loads(manifest.read_text(encoding='utf-8'))
    assert data['quality'] == 'draft'
    assert svg.exists()
    assert 'EDMG Preview Timeline' in svg.read_text(encoding='utf-8')
