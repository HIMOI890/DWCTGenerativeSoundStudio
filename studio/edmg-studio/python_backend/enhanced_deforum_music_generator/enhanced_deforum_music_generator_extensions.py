# file: enhanced_deforum_music_generator_extensions.py
"""
Extensions for enhanced_deforum_music_generator.py
- Adds configurable prompt-mapping support (load from dict or JSON file)
- Provides a wrapper to produce prompts using custom emotion/energy->modifiers

Usage:
from enhanced_deforum_music_generator_extensions import load_prompt_mapping, generate_prompts_with_mapping

pm = load_prompt_mapping('my_mapping.json')  # or pass dict
prompts = generate_prompts_with_mapping(generator, analysis, user_settings, prompt_mapping=pm)

"""
import json
from typing import Any, Dict, List, Optional

# import classes from the uploaded module (assumes it's in PYTHONPATH / same folder)
try:
    from enhanced_deforum_music_generator import DeforumMusicGenerator, AudioAnalysis
except Exception as e:
    raise ImportError("Please ensure enhanced_deforum_music_generator.py is in the same directory or PYTHONPATH")


def load_prompt_mapping(mapping: Optional[object]) -> Dict[str, Any]:
    """Load prompt mapping from a dict or a path to a JSON file.
{% load 
    Mapping format (example):
    {
      "emotions": {"joy": ["uplifting", "radiant"]},
      "energy_ranges": [
        {"min": 0.0, "max": 0.4, "modifier": "calm, tranquil"},
        {"min": 0.4, "max": 0.7, "modifier": "medium energy, cinematic"},
        {"min": 0.7, "max": 1.1, "modifier": "high energy, epic"}
      ]
    }_tags %}
    """
    if mapping is None:
        return {}
    if isinstance(mapping, dict):
        return mapping
    if isinstance(mapping, str):
        with open(mapping, 'r', encoding='utf-8') as f:
            return json.load(f)
    raise TypeError("mapping must be a dict or a path to a JSON file")


def _energy_modifier_for_value(mapping: Dict[str, Any], value: float) -> Optional[str]:
    for r in mapping.get('energy_ranges', []):
        try:
            if value >= float(r.get('min', 0.0)) and value < float(r.get('max', 1.0)):
                return r.get('modifier')
        except Exception:
            continue
    return None


def generate_prompts_with_mapping(generator: DeforumMusicGenerator, analysis: AudioAnalysis,
                                  user_settings: Dict[str, Any], prompt_mapping: Optional[object] = None) -> Dict[str, str]:
    """Generate prompts using the built-in generator, then augment them with a custom mapping.

    - prompt_mapping may be a dict or path to a JSON file.
    - mapping keys supported: 'emotions' (dict emotion->list/modifier), 'energy_ranges' (list)

    The generator's existing logic is preserved; we only append modifiers where matches occur.
    """
    mapping = load_prompt_mapping(prompt_mapping)
    base_prompts = generator.generate_prompts(analysis, user_settings)

    # quick-helpers
    emotion_map: Dict[str, List[str]] = mapping.get('emotions', {})

    # For each generated prompt, decide which extra modifiers to append
    augmented: Dict[str, str] = {}
    # Determine a representative energy value per prompt index (if energy segments exist)
    nseg = max(1, len(analysis.energy_segments))

    for k, prompt_text in base_prompts.items():
        # try to infer which segment this prompt corresponds to (frame index --> segment)
        try:
            frame_idx = int(k)
            seg_idx = min(nseg - 1, int((frame_idx / max(1, int(analysis.duration * user_settings.get('fps', 24)))) * nseg))
        except Exception:
            seg_idx = 0

        energy_val = 0.5
        if analysis.energy_segments and seg_idx < len(analysis.energy_segments):
            energy_val = analysis.energy_segments[seg_idx]

        extras: List[str] = []

        # energy based modifier
        en_mod = _energy_modifier_for_value(mapping, energy_val)
        if en_mod:
            extras.append(en_mod)

        # emotions
        for emo in analysis.lyric_emotions or []:
            emo_lower = emo.lower()
            if emo_lower in emotion_map:
                em_val = emotion_map[emo_lower]
                if isinstance(em_val, list):
                    extras.extend(em_val)
                else:
                    extras.append(str(em_val))

        # visual keywords - simple direct mapping if provided
        visual_map = mapping.get('visuals', {})
        for vis in (analysis.visual_elements or [])[:3]:
            if vis in visual_map:
                extras.append(visual_map[vis])

        # spectral mapping
        spectral_map = mapping.get('spectral', {})
        # allow keys like 'brightness>0.6' -> modifier
        for cond, mod in spectral_map.items():
            try:
                if '>' in cond:
                    kleft, kv = cond.split('>')
                    if analysis.spectral_features.get(kleft.strip(), 0.0) > float(kv):
                        extras.append(mod)
                elif '<' in cond:
                    kleft, kv = cond.split('<')
                    if analysis.spectral_features.get(kleft.strip(), 0.0) < float(kv):
                        extras.append(mod)
            except Exception:
                continue

        # deduplicate and append
        if extras:
            # keep order, dedupe
            seen = set()
            deduped = [x for x in extras if not (x in seen or seen.add(x))]
            augmented[k] = prompt_text + ", " + ", ".join(deduped)
        else:
            augmented[k] = prompt_text

    return augmented


# Sample helper to write a sample mapping JSON
def write_sample_mapping(path: str) -> None:
    sample = {
        "emotions": {
            "joy": ["uplifting", "radiant"],
            "melancholy": ["wistful", "moody"]
        },
        "energy_ranges": [
            {"min": 0.0, "max": 0.4, "modifier": "calm, tranquil"},
            {"min": 0.4, "max": 0.7, "modifier": "medium energy, cinematic"},
            {"min": 0.7, "max": 2.0, "modifier": "high energy, epic"}
        ],
        "visuals": {"ocean": "featuring oceanic vistas", "city": "neon cityscapes"},
        "spectral": {"brightness>0.6": "bright lighting, high key"}
    }
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(sample, f, indent=2)
