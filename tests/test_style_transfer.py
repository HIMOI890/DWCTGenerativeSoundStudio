from PIL import Image

from enhanced_deforum_music_generator.style_transfer import StyleTransfer, StyleTransferEngine


def test_style_transfer_prompts_and_images(tmp_path):
    engine = StyleTransferEngine()
    assert 'cinematic' in engine.available_styles()

    prompts = engine.apply_style_to_prompts({0: 'city skyline'}, 'cinematic', strength=0.75)
    assert 'cinematic lighting' in prompts[0]

    img_path = tmp_path / 'input.png'
    out_path = tmp_path / 'output.png'
    Image.new('RGB', (32, 32), color=(120, 90, 60)).save(img_path)

    transfer = StyleTransfer(engine=engine)
    transfer.transfer(img_path, out_path, 'vintage', strength=0.8)

    assert out_path.exists()
    out = Image.open(out_path)
    assert out.size == (32, 32)
