# file: src/enhanced_deforum_music_generator/visual/visual_mapper.py
from dataclasses import dataclass, field
from typing import Dict, List

@dataclass
class VisualMapper:
    '''
    Maps emotions/keywords/energy -> visual modifiers.
    '''
    emotion_map: Dict[str, List[str]] = field(default_factory=lambda:{
        "joy": ["warm lighting", "vibrant colors", "sunlit"],
        "melancholy": ["cool tones", "mist", "soft focus"],
        "rage": ["high contrast", "storm clouds", "embers"],
        "longing": ["haze", "sunset glow", "film grain"],
        "calm": ["pastel palette", "wide shots", "ambient fog"]
    })
    keyword_map: Dict[str, List[str]] = field(default_factory=lambda:{
        "ocean": ["oceanic vistas", "sea spray", "distant horizon"],
        "city": ["neon cityscapes", "wet streets", "bokeh lights"],
        "forest": ["dappled light", "lush foliage", "mist trails"],
        "desert": ["windswept dunes", "golden hour", "heat shimmer"]
    })
    def for_emotions(self, emos: List[str]) -> List[str]:
        out = []
        for e in emos:
            out += self.emotion_map.get(e.lower(), [])
        return out
    def for_keywords(self, kws: List[str]) -> List[str]:
        out=[]
        for k in kws:
            out += self.keyword_map.get(k.lower(), [])
        return out
    def for_energy(self, value: float) -> List[str]:
        if value < 0.35: return ["calm", "tranquil", "wide composition"]
        if value < 0.7:  return ["cinematic", "medium energy", "balanced lighting"]
        return ["high energy", "dynamic motion", "epic scale"]
