from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from .audio_analysis import analyze_audio


@dataclass(frozen=True)
class AudioAnalysis:
    """
    Convenience wrapper used by packaging/build tooling.

    The canonical analyzer output is returned by `deforum_music.audio_analysis.analyze_audio`.
    """
    raw: Dict[str, Any]

    @property
    def bpm(self) -> Optional[float]:
        return self.raw.get("tempo", {}).get("bpm")

    @property
    def duration_sec(self) -> float:
        return float(self.raw.get("duration_sec", 0.0))

    @property
    def energy_score(self) -> float:
        return float(self.raw.get("energy_score", 0.0))

    @property
    def brightness_score(self) -> float:
        return float(self.raw.get("brightness_score", 0.0))

    @property
    def percussiveness_score(self) -> float:
        return float(self.raw.get("percussiveness_score", 0.0))

    @property
    def beat_times(self) -> List[float]:
        return list(self.raw.get("tempo", {}).get("beat_times", []))


class AudioAnalyzer:
    """High-level analyzer that returns an `AudioAnalysis` wrapper."""
    def __init__(self, *, cache: bool = True, cache_dir: Optional[str] = None):
        self.cache = cache
        self.cache_dir = cache_dir

    def analyze(self, audio_path: str) -> AudioAnalysis:
        raw = analyze_audio(audio_path, use_cache=self.cache, cache_dir=self.cache_dir)
        return AudioAnalysis(raw=raw)


class DeforumMusicGenerator:
    """
    Maps analysis signals to Deforum-friendly schedules.

    Output is intentionally conservative and deterministic: it produces schedules that
    Deforum can paste into `strength_schedule`, `zoom`, `angle`, `noise_schedule`,
    plus prompt cadence suggestions.
    """
    def __init__(self, analysis: AudioAnalysis):
        self.analysis = analysis

    def build_deforum_settings(
        self,
        *,
        fps: int = 24,
        base_prompt: str = "cinematic masterpiece, highly detailed",
        style_prompt: str = "film grain, dynamic lighting",
        motion_strength: float = 1.0,
    ) -> Dict[str, Any]:
        duration = max(self.analysis.duration_sec, 0.001)
        total_frames = int(round(duration * fps))
        total_frames = max(total_frames, 1)

        beats = self.analysis.beat_times
        if not beats:
            # fallback: use evenly spaced markers
            beats = [i * 1.0 for i in range(int(duration) + 1)]

        beat_frames = sorted({int(round(t * fps)) for t in beats if 0 <= t <= duration})
        if not beat_frames:
            beat_frames = [0, total_frames - 1]

        # Core scores
        energy = float(np.clip(self.analysis.energy_score, 0.0, 1.0))
        bright = float(np.clip(self.analysis.brightness_score, 0.0, 1.0))
        perc = float(np.clip(self.analysis.percussiveness_score, 0.0, 1.0))

        # Strength: higher energy => lower strength (more motion), bounded
        strength_hi = 0.75 - (0.25 * energy)
        strength_lo = 0.55 - (0.30 * energy)
        strength_hi = float(np.clip(strength_hi, 0.35, 0.85))
        strength_lo = float(np.clip(strength_lo, 0.20, strength_hi))

        # Zoom: brighter/energy => slightly higher zoom on beats
        zoom_base = 1.0 + 0.003 * (0.3 + energy + 0.5 * bright) * motion_strength
        zoom_beat = zoom_base + 0.010 * (0.5 + energy) * motion_strength

        # Angle: percussive => stronger shake
        angle_amp = float(0.6 + 2.5 * perc) * motion_strength

        # Noise: percussive => more noise on beats
        noise_base = float(0.02 + 0.06 * perc)
        noise_beat = float(noise_base + 0.10 * perc)

        def as_schedule(pairs: List[Tuple[int, float]]) -> str:
            return ", ".join([f"{f}:({v:.4f})" for f, v in pairs])

        strength_pairs = []
        zoom_pairs = []
        angle_pairs = []
        noise_pairs = []

        for f in range(total_frames):
            if f in beat_frames:
                strength_pairs.append((f, strength_lo))
                zoom_pairs.append((f, zoom_beat))
                angle_pairs.append((f, angle_amp if (f // max(1, fps//2)) % 2 == 0 else -angle_amp))
                noise_pairs.append((f, noise_beat))
            else:
                # sparse schedules: only add every second for smoothness
                if f % 2 == 0:
                    strength_pairs.append((f, strength_hi))
                    zoom_pairs.append((f, zoom_base))
                    angle_pairs.append((f, 0.0))
                    noise_pairs.append((f, noise_base))

        # Prompts: insert "beat accents" by repeating style prompt on beat segments
        prompt_cadence = []
        for f in beat_frames:
            prompt_cadence.append({"frame": f, "prompt": f"{base_prompt}, {style_prompt}, beat accent"})

        return {
            "fps": fps,
            "total_frames": total_frames,
            "analysis": self.analysis.raw,
            "schedules": {
                "strength_schedule": as_schedule(strength_pairs),
                "zoom": as_schedule(zoom_pairs),
                "angle": as_schedule(angle_pairs),
                "noise_schedule": as_schedule(noise_pairs),
            },
            "prompts": {
                "base_prompt": base_prompt,
                "style_prompt": style_prompt,
                "beat_prompts": prompt_cadence,
            },
        }


def create_gradio_interface() -> Any:
    """
    Lightweight Gradio app for quickly analyzing an audio file and exporting schedules.

    This is used by packaging scripts and can run standalone:
        python -m deforum_music gradio
    """
    import gradio as gr

    def run(audio_path: str, fps: int, base_prompt: str, style_prompt: str, motion_strength: float) -> Tuple[str, str]:
        if not audio_path:
            raise gr.Error("Provide an audio file path.")
        analysis = AudioAnalyzer().analyze(audio_path)
        settings = DeforumMusicGenerator(analysis).build_deforum_settings(
            fps=fps, base_prompt=base_prompt, style_prompt=style_prompt, motion_strength=motion_strength
        )
        return json.dumps(settings["analysis"], indent=2), json.dumps(settings["schedules"], indent=2)

    with gr.Blocks(title="Deforum Music Analyzer") as demo:
        gr.Markdown("# Deforum Music Analyzer\nAnalyze audio and export Deforum schedules.")
        with gr.Row():
            audio = gr.Textbox(label="Audio file path")
            fps = gr.Number(label="FPS", value=24, precision=0)
        base_prompt = gr.Textbox(label="Base prompt", value="cinematic masterpiece, highly detailed")
        style_prompt = gr.Textbox(label="Style prompt", value="film grain, dynamic lighting")
        motion = gr.Slider(label="Motion strength", minimum=0.2, maximum=2.0, value=1.0, step=0.1)
        run_btn = gr.Button("Analyze")
        analysis_out = gr.Code(label="Analysis (json)", language="json")
        schedules_out = gr.Code(label="Schedules (json)", language="json")
        run_btn.click(run, inputs=[audio, fps, base_prompt, style_prompt, motion], outputs=[analysis_out, schedules_out])
    return demo
