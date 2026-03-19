"""NLP and lyrics transcription.

Notes:
- Whisper is treated as an optional dependency.
- Model loading is lazy so importing this module is fast and tests can monkeypatch
  `whisper.load_model` without downloading weights.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from ..config.config_system import LyricsConfig
import importlib


@dataclass
class LyricsSegment:
    start: float
    end: float
    text: str


class LyricsProcessor:
    def __init__(self, config: LyricsConfig):
        self.config = config
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return
        if self.config.provider != "whisper":
            raise ValueError(f"Unsupported transcription provider: {self.config.provider}")
        whisper = importlib.import_module("whisper")
        self._model = whisper.load_model(self.config.model)

    def transcribe(self, audio_path: str) -> List[Dict[str, Any]]:
        # For tests we do not require audio_path to exist; the underlying provider
        # will handle errors if it's real.
        self._ensure_model()
        result = self._model.transcribe(str(audio_path))
        segments = result.get("segments", [])
        out: List[Dict[str, Any]] = []
        for s in segments:
            out.append({
                "start": float(s.get("start", 0.0)),
                "end": float(s.get("end", 0.0)),
                "text": str(s.get("text", "")).strip(),
            })
        return out


class NLPProcessor:
    """Lightweight keyword/emotion inference used by some UI flows."""

    def __init__(self, cfg: Optional[dict] = None):
        self.cfg = cfg or {}

    def infer_emotions(self, text: str) -> List[str]:
        text = (text or "").lower()
        emos: List[str] = []
        if any(w in text for w in ["happy", "joy", "love", "smile", "sun"]):
            emos.append("joy")
        if any(w in text for w in ["sad", "lonely", "tears", "pain", "rain"]):
            emos.append("melancholy")
        if any(w in text for w in ["fear", "dark", "nightmare", "panic"]):
            emos.append("fear")
        if any(w in text for w in ["anger", "rage", "fire", "burn"]):
            emos.append("anger")
        return emos

    def extract_themes(self, text: str) -> List[str]:
        words = re_split(r"[^a-zA-Z0-9]+", (text or "").lower())
        words = [w for w in words if len(w) >= 4]
        # de-dup while preserving order
        seen = set()
        themes = []
        for w in words:
            if w not in seen:
                seen.add(w)
                themes.append(w)
        return themes[: int(self.cfg.get("max_themes", 10))]


def re_split(pattern: str, text: str):
    import re
    return re.split(pattern, text)
