from enhanced_deforum_music_generator import DeforumMusicGenerator, AudioAnalysis

def test_deforum_generator_prompts():
    generator = DeforumMusicGenerator()
    analysis = AudioAnalysis()
    user_settings = {"base_prompt": "cinematic", "style_prompt": "film grain"}
    
    settings = generator.build_deforum_settings(analysis, user_settings)
    assert "prompts" in settings
