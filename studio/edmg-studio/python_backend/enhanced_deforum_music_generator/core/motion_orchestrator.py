from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Tuple

from .deforum_schedule_format import format_schedule
from ..public_api import AudioAnalysis


@dataclass(frozen=True)
class MotionConfig:
    fps: int = 24
    zoom_base: float = 1.0
    zoom_pulse: float = 0.10
    angle_pulse: float = 1.5
    strength_base: float = 0.65
    strength_pulse: float = 0.10
    cfg_base: float = 7.0
    cfg_pulse: float = 1.0
    max_keyframes: int = 64


def _frame(t: float, fps: int) -> int:
    return max(0, int(round(float(t) * float(fps))))


def _sample_energy(energy: List[float], idx: int) -> float:
    if not energy:
        return 0.0
    idx = max(0, min(int(idx), len(energy) - 1))
    v = float(energy[idx])
    if v != v:  # NaN
        return 0.0
    return max(0.0, min(1.0, v))


def motion_schedules(analysis: AudioAnalysis, *, cfg: MotionConfig) -> Dict[str, str]:
    """Generate conservative audio-reactive Deforum schedules from beats + energy.

    Output keys:
      - zoom
      - angle
      - strength_schedule
      - cfg_scale_schedule
    """
    fps = max(1, int(cfg.fps))
    beats = list(getattr(analysis, "beats", []) or [])
    energy = list(getattr(analysis, "energy", []) or [])

    # Reduce beats to avoid extremely long schedules
    if beats and len(beats) > cfg.max_keyframes:
        step = max(1, len(beats) // cfg.max_keyframes)
        beats = beats[::step]

    zoom_pairs: List[Tuple[int, float]] = [(0, float(cfg.zoom_base))]
    angle_pairs: List[Tuple[int, float]] = [(0, 0.0)]
    strength_pairs: List[Tuple[int, float]] = [(0, float(cfg.strength_base))]
    cfg_pairs: List[Tuple[int, float]] = [(0, float(cfg.cfg_base))]

    for i, bt in enumerate(beats, start=1):
        f = _frame(bt, fps)
        e = _sample_energy(energy, min(len(energy) - 1, int((bt / max(1e-6, getattr(analysis, "duration", 1.0))) * (len(energy) - 1))) if energy else 0)
        pulse = (e * 2.0) - 1.0  # [-1, 1]

        zoom_pairs.append((f, cfg.zoom_base + cfg.zoom_pulse * pulse))
        angle_pairs.append((f, cfg.angle_pulse * pulse))
        strength_pairs.append((f, max(0.0, min(1.0, cfg.strength_base + cfg.strength_pulse * e))))
        cfg_pairs.append((f, max(1.0, cfg.cfg_base + cfg.cfg_pulse * e)))

    return {
        "zoom": format_schedule(zoom_pairs),
        "angle": format_schedule(angle_pairs),
        "strength_schedule": format_schedule(strength_pairs),
        "cfg_scale_schedule": format_schedule(cfg_pairs),
    }
