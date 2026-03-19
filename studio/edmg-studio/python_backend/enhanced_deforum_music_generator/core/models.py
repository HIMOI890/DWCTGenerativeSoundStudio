from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any


@dataclass
class AudioAnalysis:
    duration: float
    tempo_bpm: float
    beats: List[float] = field(default_factory=list)
    raw_text: Optional[str] = None


@dataclass
class PromptSet:
    base_prompt: str
    variations: List[str] = field(default_factory=list)


@dataclass
class DeforumSettings:
    frames: int
    fps: int
    schedule: Dict[str, Any] = field(default_factory=dict)
    prompt_set: Optional[PromptSet] = None
