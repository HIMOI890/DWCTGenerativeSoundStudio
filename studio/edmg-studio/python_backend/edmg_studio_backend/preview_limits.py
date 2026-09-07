from __future__ import annotations

import math
import os
from dataclasses import dataclass


class PreviewBudgetViolation(ValueError):
    pass


def _env_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default)).strip()))
    except Exception:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, str(default)).strip())
        return value if math.isfinite(value) and value > 0 else default
    except Exception:
        return default


@dataclass(frozen=True)
class PreviewBudgetLimits:
    max_width: int
    max_height: int
    max_pixels: int
    max_duration_s: float
    max_fps: int
    max_frames: int
    max_diffusion_steps: int
    max_work_units: int

    @classmethod
    def from_env(cls, *, diffusion: bool = False) -> "PreviewBudgetLimits":
        prefix = "EDMG_DIFFUSION_PREVIEW" if diffusion else "EDMG_PREVIEW"
        return cls(
            max_width=_env_int(prefix + "_MAX_WIDTH", 1024 if diffusion else 2048),
            max_height=_env_int(prefix + "_MAX_HEIGHT", 1024 if diffusion else 2048),
            max_pixels=_env_int(prefix + "_MAX_PIXELS", 4_194_304),
            max_duration_s=_env_float(prefix + "_MAX_DURATION_S", 5.0 if diffusion else 10.0),
            max_fps=_env_int(prefix + "_MAX_FPS", 8 if diffusion else 12),
            max_frames=_env_int(prefix + "_MAX_FRAMES", 40 if diffusion else 120),
            max_diffusion_steps=_env_int("EDMG_PREVIEW_MAX_DIFFUSION_STEPS", 30),
            max_work_units=_env_int(prefix + "_MAX_WORK_UNITS", 41_943_040 if diffusion else 125_829_120),
        )

    def _validate_dimensions(self, *, width: int, height: int, prefix: str) -> int:
        width_i = int(width)
        height_i = int(height)
        if width_i <= 0 or height_i <= 0:
            raise PreviewBudgetViolation(f"{prefix} width and height must be positive.")
        if width_i > self.max_width:
            raise PreviewBudgetViolation(f"{prefix} width {width_i} exceeds limit {self.max_width}.")
        if height_i > self.max_height:
            raise PreviewBudgetViolation(f"{prefix} height {height_i} exceeds limit {self.max_height}.")
        pixels = width_i * height_i
        if pixels > self.max_pixels:
            raise PreviewBudgetViolation(
                f"{prefix} pixel count {pixels} exceeds limit {self.max_pixels}."
            )
        return pixels

    def validate_frame(self, *, width: int, height: int) -> None:
        pixels = self._validate_dimensions(width=width, height=height, prefix="Preview")
        if pixels > self.max_work_units:
            raise PreviewBudgetViolation(
                f"Preview work budget {pixels} exceeds limit {self.max_work_units}."
            )

    def validate_segment(
        self,
        *,
        start_s: float,
        end_s: float,
        width: int,
        height: int,
        fps: int,
        diffusion_steps: int | None = None,
        prefix: str = "Preview",
    ) -> tuple[float, int, int]:
        start = float(start_s)
        end = float(end_s)
        if not math.isfinite(start) or not math.isfinite(end) or start < 0:
            raise PreviewBudgetViolation(f"{prefix} times must be finite and nonnegative.")
        if end <= start:
            raise PreviewBudgetViolation(f"{prefix} end_s must be greater than start_s.")
        duration_s = end - start
        if duration_s > self.max_duration_s:
            raise PreviewBudgetViolation(
                f"{prefix} duration {duration_s:.2f}s exceeds limit {self.max_duration_s:.2f}s."
            )
        fps_i = int(fps)
        if fps_i <= 0:
            raise PreviewBudgetViolation(f"{prefix} fps must be positive.")
        if fps_i > self.max_fps:
            raise PreviewBudgetViolation(f"{prefix} fps {fps_i} exceeds limit {self.max_fps}.")
        pixels = self._validate_dimensions(width=width, height=height, prefix=prefix)
        frame_count = int(math.ceil(duration_s * fps_i))
        if frame_count <= 0:
            raise PreviewBudgetViolation(f"{prefix} frame count must be positive.")
        if frame_count > self.max_frames:
            raise PreviewBudgetViolation(
                f"{prefix} frame count {frame_count} exceeds limit {self.max_frames}."
            )
        multiplier = 1
        if diffusion_steps is not None:
            steps_i = int(diffusion_steps)
            if steps_i <= 0:
                raise PreviewBudgetViolation(f"{prefix} diffusion steps must be positive.")
            if steps_i > self.max_diffusion_steps:
                raise PreviewBudgetViolation(
                    f"{prefix} diffusion steps {steps_i} exceed limit {self.max_diffusion_steps}."
                )
            # Pixel-frames and diffusion steps have independent ceilings.
        work_units = pixels * frame_count * multiplier
        if work_units > self.max_work_units:
            raise PreviewBudgetViolation(
                f"{prefix} work budget {work_units} exceeds limit {self.max_work_units}."
            )
        return duration_s, fps_i, frame_count
