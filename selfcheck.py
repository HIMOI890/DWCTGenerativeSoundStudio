#!/usr/bin/env python3
import sys, os, importlib
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "src")))

MODULES = [
    "enhanced_deforum_music_generator",
    "enhanced_deforum_music_generator.config.config_system",
    "enhanced_deforum_music_generator.core.generator",
    "enhanced_deforum_music_generator.core.audio_analyzer",
    "enhanced_deforum_music_generator.core.nlp_processor",
    "enhanced_deforum_music_generator.interface.gradio_interface",
    "enhanced_deforum_music_generator.integrations.a1111_connector",
    "enhanced_deforum_music_generator.utils.logging_utils",
]

def check(name):
    try:
        importlib.import_module(name)
        print(f"[OK]     {name}")
    except Exception as e:
        print(f"[ERROR]  {name} -> {e}")

if __name__ == "__main__":
    print("--- Self-Check: Import Verification ---")
    for m in MODULES:
        check(m)
    print("--------------------------------------")
