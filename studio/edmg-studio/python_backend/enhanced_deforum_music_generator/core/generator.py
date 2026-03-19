# file: src/enhanced_deforum_music_generator/core/generator.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional, Tuple
from ..visual.visual_mapper import VisualMapper

@dataclass
class AudioAnalysis:
    duration: float
    beats: List[float]
    energy: List[float]
    lyric_segments: List[Dict[str, Any]] = field(default_factory=list)
    lyric_emotions: List[str] = field(default_factory=list)
    visual_elements: List[str] = field(default_factory=list)
    spectral_features: Dict[str, float] = field(default_factory=dict)

class DeforumMusicGenerator:
    '''
    Generates prompts either from user inputs or fully automatically from analysis.
    '''
    def __init__(self, mapper: Optional[VisualMapper] = None):
        self.mapper = mapper or VisualMapper()

    # --------- public entrypoints ----------
    def auto_prompts(self, analysis: AudioAnalysis, base_style: str = "") -> Dict[int, str]:
        '''
        Fully automatic prompt creation when user gives nothing.
        '''
        # derive keywords from lyric_segments
        keywords = self._keywords_from_lyrics(analysis.lyric_segments)
        emos = analysis.lyric_emotions or self._infer_emotions_from_lyrics(analysis.lyric_segments)
        visuals = self.mapper.for_emotions(emos) + self.mapper.for_keywords(keywords)
        prompts_by_frame = {}
        fps =  max(24, int(round(len(analysis.energy) / max(1, analysis.duration))))
        # choose cut points at beats; fall back to N segments
        cut_frames = [int(b * fps) for b in analysis.beats] or [0, int(analysis.duration * fps * 0.33), int(analysis.duration * fps * 0.66)]
        cut_frames = sorted(set([max(0,int(c)) for c in cut_frames if c >= 0]))

        # pick an energy-based flavor per region
        last = 0
        for cf in cut_frames + [int(analysis.duration * fps)]:
            if cf <= last: 
                continue
            # representative energy in this span
            ei = min(len(analysis.energy)-1, max(0, int((last + cf)/2)))
            energy_mods = self.mapper.for_energy(analysis.energy[ei] if analysis.energy else 0.5)
            prompt = ", ".join(
                [w for w in [
                    base_style or "cinematic, detailed, photorealistic",
                    *visuals,
                    *energy_mods
                ] if w]
            )
            prompts_by_frame[last] = prompt
            last = cf

        if not prompts_by_frame:
            prompts_by_frame[0] = base_style or "cinematic, detailed, photorealistic"
        return prompts_by_frame

    def generate_prompts(self, analysis: AudioAnalysis, user_settings: Dict[str, Any]) -> Dict[int, str]:
        '''
        If user provided prompts in settings, merge/enhance them; else call auto_prompts.
        Expected user_settings optional keys:
          - prompts_by_frame: Dict[int, str]
          - base_style: str
        '''
        user_prompts = user_settings.get("prompts_by_frame")
        base_style = user_settings.get("base_style", "")
        if not user_prompts:
            return self.auto_prompts(analysis, base_style=base_style)

        # Optionally enhance user prompts with emotion/energy
        enhanced = {}
        for frame, text in user_prompts.items():
            energy_mods = []
            if analysis.energy:
                ei = min(len(analysis.energy)-1, int(frame))
                energy_val = analysis.energy[ei]
                energy_mods = self.mapper.for_energy(energy_val)
            enhanced[frame] = ", ".join([text, *energy_mods]) if energy_mods else text
        return enhanced

    # --------- helpers ----------
    def _keywords_from_lyrics(self, segs: List[Dict[str, Any]]) -> List[str]:
        kws = []
        for s in segs:
            for t in (s.get("text") or "").split():
                t = "".join([c for c in t.lower() if c.isalnum()])
                if t and len(t) > 3:
                    kws.append(t)
        # keep some uniqueness
        uniq = []
        seen = set()
        for k in kws:
            if k not in seen:
                uniq.append(k); seen.add(k)
        return uniq[:10]

    def _infer_emotions_from_lyrics(self, segs: List[Dict[str, Any]]) -> List[str]:
        text = " ".join([s.get("text","") for s in segs]).lower()
        emos = []
        rules = {
            "joy": ["love","shine","sun","happy","smile","bright"],
            "melancholy": ["alone","cold","tears","blue","lost","empty"],
            "rage": ["fire","burn","rage","fight","scream"],
            "longing": ["want","miss","wish","dream","far"],
            "calm": ["breathe","slow","calm","quiet","peace"]
        }
        for emo, words in rules.items():
            if any(w in text for w in words):
                emos.append(emo)
        return emos or ["cinematic"]
