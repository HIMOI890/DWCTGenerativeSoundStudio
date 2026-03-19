"""Sync calibration helpers.

This module estimates a small time offset (seconds) that improves alignment
between beat times (seconds) and video frames at a given FPS.

The intent is practical: if an audio file has a leading silence, or your beat
detector's first beat is slightly late/early, a small global shift can make
beat-reactive schedules "hit" more precisely.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List


@dataclass(frozen=True)
class SyncCalibrationResult:
    fps: int
    offset_seconds: float
    score: float


def _wrap01(x: float) -> float:
    # Map to [0,1)
    x = x % 1.0
    return x if x >= 0.0 else x + 1.0


def _dist_to_int(frac: float) -> float:
    # Distance to nearest integer on a unit circle
    frac = _wrap01(frac)
    return min(frac, 1.0 - frac)


def estimate_global_offset_seconds(
    beats_seconds: Iterable[float],
    *,
    fps: int,
    search_frames: float = 0.5,
    step_frames: float = 0.01,
) -> SyncCalibrationResult:
    """Estimate a global time offset for beat-to-frame alignment.

    Args:
        beats_seconds: Beat timestamps in seconds.
        fps: Target frames per second.
        search_frames: Search window in *frames* on either side of 0.
        step_frames: Step size in *frames*.

    Returns:
        SyncCalibrationResult
    """
    fps_i = max(1, int(fps))
    beats: List[float] = [float(b) for b in beats_seconds if b is not None]
    beats = [b for b in beats if b == b and b >= 0.0]  # drop NaNs
    if not beats:
        return SyncCalibrationResult(fps=fps_i, offset_seconds=0.0, score=0.0)

    # Precompute fractional frame positions
    fracs = [_wrap01(b * fps_i) for b in beats]

    # Brute-force small delta (in frames)
    best_delta_frames = 0.0
    best_score = float("inf")

    sf = float(search_frames)
    step = max(float(step_frames), 1e-6)
    n = int((2.0 * sf) / step) + 1
    start = -sf

    for i in range(n):
        delta = start + i * step
        # Equivalent fractional shift
        err = 0.0
        for f in fracs:
            err += _dist_to_int(f + delta)
        if err < best_score:
            best_score = err
            best_delta_frames = delta

    # Convert frames delta to seconds
    offset_seconds = best_delta_frames / float(fps_i)
    return SyncCalibrationResult(fps=fps_i, offset_seconds=float(offset_seconds), score=float(best_score))
