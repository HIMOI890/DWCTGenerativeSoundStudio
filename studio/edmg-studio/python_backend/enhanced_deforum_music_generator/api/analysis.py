from __future__ import annotations

import os
import tempfile
from pathlib import Path

from fastapi import APIRouter, File, UploadFile

from ..core.audio_analyzer import AudioAnalyzer
from ..config.config_system import AudioConfig, LyricsConfig

try:
    from ..core.nlp_processor import LyricsProcessor, NLPProcessor
except Exception:  # pragma: no cover
    LyricsProcessor = None  # type: ignore
    NLPProcessor = None  # type: ignore

router = APIRouter()


@router.post("/analyze-audio")
async def analyze_audio(file: UploadFile = File(...), enable_lyrics: bool = False, max_duration: int = 1800):
    """Analyze an uploaded audio file.

    Returns a JSON-serializable dict containing tempo, beats, energy, onset info,
    and (optionally) a lightweight lyrics transcript + inferred themes.

    Notes:
    - `enable_lyrics` is off by default because Whisper weights are large.
    - `max_duration` defaults to 30 minutes for full-song music video workflows.
    """
    analyzer = AudioAnalyzer(AudioConfig(max_duration=int(max_duration)))
    contents = await file.read()
    suffix = Path(file.filename or "").suffix

    tmp_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(contents)
            tmp_path = tmp.name

        result = analyzer.analyze(tmp_path, enable_cache=True)

        lyrics_segments = []
        inferred = {"emotions": [], "themes": []}
        if enable_lyrics and LyricsProcessor is not None:
            try:
                lp = LyricsProcessor(LyricsConfig(provider="whisper", model="tiny"))
                lyrics_segments = lp.transcribe(tmp_path)
                if NLPProcessor is not None and lyrics_segments:
                    text = " ".join([s.get("text", "") for s in lyrics_segments])
                    nlp = NLPProcessor()
                    inferred = {
                        "emotions": nlp.infer_emotions(text),
                        "themes": nlp.extract_themes(text),
                    }
            except Exception:
                # Keep analysis robust even when Whisper isn't installed.
                lyrics_segments = []
    finally:
        if tmp_path:
            try:
                os.remove(tmp_path)
            except OSError:
                pass

    # Backward-compatible keys
    return {
        "tempo_bpm": float(result.get("tempo", 0.0)),
        "duration": float(result.get("duration", 0.0)),
        "sample_rate": int(result.get("sample_rate", 0)),
        "beats": list(result.get("beats", [])),
        "energy": list(result.get("energy", [])),
        "onset_strength": list(result.get("onset_strength", [])),
        "onset_times": list(result.get("onset_times", [])),
        "spectral_centroid": list(result.get("spectral_centroid", [])),
        "spectral_rolloff": list(result.get("spectral_rolloff", [])),
        "rms_energy": list(result.get("rms_energy", [])),
        "lyrics_segments": lyrics_segments,
        "lyrics_inferred": inferred,
    }


@router.post("/calibrate-sync")
async def calibrate_sync(payload: dict):
    """Estimate a global audio->video sync offset.

    Expects JSON:
      {"beats": [..seconds..], "fps": 30}
    Returns:
      {"offset_seconds": ..., "score": ...}
    """
    from ..core.sync_calibration import estimate_global_offset_seconds

    beats = payload.get("beats") or payload.get("beat_times") or []
    fps = payload.get("fps") or 30
    res = estimate_global_offset_seconds(beats, fps=int(fps))
    return {
        "fps": res.fps,
        "offset_seconds": res.offset_seconds,
        "score": res.score,
    }
