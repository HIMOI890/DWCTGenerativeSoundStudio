from __future__ import annotations

from typing import Any, Dict

from fastapi import APIRouter

from ..public_api import AudioAnalysis, DeforumMusicGenerator
from ..deforum_defaults import make_deforum_settings_template

router = APIRouter()


def _coerce_analysis(obj: Dict[str, Any] | None) -> AudioAnalysis:
    d = obj or {}
    return AudioAnalysis(
        filepath=str(d.get("filepath") or d.get("path") or ""),
        duration=float(d.get("duration") or 0.0),
        tempo_bpm=float(d.get("tempo_bpm") or d.get("tempo") or 0.0),
        beats=list(d.get("beats") or d.get("beat_times") or d.get("beat_frames") or []),
        energy=list(d.get("energy") or d.get("energy_segments") or []),
    )


@router.post("/generate-deforum")
async def generate_deforum(payload: Dict[str, Any]):
    """Generate Deforum-ready settings JSON based on audio analysis + user input.

    Backward compatible:
    - If payload contains {"analysis": {...}, "settings": {...}} it uses both.
    - Otherwise, payload itself is treated as "settings" with an empty analysis.
    """
    generator = DeforumMusicGenerator()

    analysis_data = (payload or {}).get("analysis")
    settings = (payload or {}).get("settings")

    if isinstance(analysis_data, dict) and isinstance(settings, dict):
        analysis = _coerce_analysis(analysis_data)
        return generator.build_deforum_settings(analysis, settings)

    # Treat entire payload as settings (no analysis supplied)
    analysis = AudioAnalysis()
    return generator.build_deforum_settings(analysis, payload or {})


@router.get("/template")
async def deforum_template() -> Dict[str, Any]:
    """Return the full Deforum settings template (JSON-first editing surface)."""
    return make_deforum_settings_template()
