from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional


@dataclass(frozen=True)
class PreviewFrame:
    frame: int
    time: float
    energy: float
    beat: bool
    prompt: str
    zoom: float
    angle: float


class PreviewGenerator:
    QUALITY_FPS = {
        'draft': 8,
        'low': 10,
        'medium': 12,
        'high': 15,
        'ultra': 24,
    }

    def __init__(self, quality: str = 'medium', max_duration: float = 30.0, fps: Optional[int] = None):
        self.quality = quality if quality in self.QUALITY_FPS else 'medium'
        self.max_duration = float(max(1.0, max_duration))
        self.fps = int(fps or self.QUALITY_FPS[self.quality])

    def generate_preview(
        self,
        analysis: Any,
        prompts: Optional[Mapping[Any, str]] = None,
        fps: Optional[int] = None,
        duration: Optional[float] = None,
    ) -> Dict[str, Any]:
        fps = int(fps or self.fps)
        duration_s = min(self.max_duration, self._analysis_duration(analysis))
        if duration is not None:
            duration_s = min(duration_s, max(0.1, float(duration)))
        total_frames = max(1, int(math.ceil(duration_s * fps)))
        energy = self._resample(self._analysis_energy(analysis), total_frames)
        beats = list(self._analysis_beats(analysis))
        prompt_map = self._normalize_prompts(prompts)
        frames: List[PreviewFrame] = []
        beat_frames: List[int] = []

        for frame_idx in range(total_frames):
            time_s = frame_idx / fps
            beat = any(abs(bt - time_s) <= (0.5 / fps) for bt in beats)
            if beat:
                beat_frames.append(frame_idx)
            prompt = self._prompt_for_frame(prompt_map, frame_idx)
            e = float(energy[frame_idx]) if frame_idx < len(energy) else 0.0
            frames.append(
                PreviewFrame(
                    frame=frame_idx,
                    time=round(time_s, 4),
                    energy=round(e, 4),
                    beat=beat,
                    prompt=prompt,
                    zoom=round(1.0 + (0.025 * e) + (0.01 if beat else 0.0), 4),
                    angle=round(math.sin(time_s * 1.7) * (2.5 + 5.0 * e), 4),
                )
            )

        return {
            'ok': True,
            'quality': self.quality,
            'fps': fps,
            'duration': round(duration_s, 4),
            'total_frames': total_frames,
            'beat_frames': beat_frames,
            'frames': [asdict(f) for f in frames],
        }

    def export_manifest(self, preview: Mapping[str, Any], output_path: str | Path) -> str:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(dict(preview), indent=2), encoding='utf-8')
        return str(out)

    def render_svg(self, preview: Mapping[str, Any], output_path: str | Path, width: int = 1200, height: int = 260) -> str:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        frames = list(preview.get('frames') or [])
        total = max(len(frames), 1)
        pad = 28
        usable_w = max(width - pad * 2, 1)
        usable_h = max(height - pad * 2, 1)
        bar_w = usable_w / total
        bars = []
        beat_lines = []
        labels = []
        for idx, frame in enumerate(frames):
            energy = float(frame.get('energy') or 0.0)
            x = pad + idx * bar_w
            h = max(4.0, energy * (usable_h - 24))
            y = pad + usable_h - h
            bars.append(f"<rect x='{x:.2f}' y='{y:.2f}' width='{max(bar_w - 1.0, 1.0):.2f}' height='{h:.2f}' rx='1' fill='#4fd1c5' opacity='0.9' />")
            if frame.get('beat'):
                beat_lines.append(f"<line x1='{x + (bar_w / 2):.2f}' y1='{pad:.2f}' x2='{x + (bar_w / 2):.2f}' y2='{pad + usable_h:.2f}' stroke='#f56565' stroke-width='1.5' opacity='0.8' />")
        last_prompt = None
        for frame in frames:
            prompt = str(frame.get('prompt') or '').strip()
            if not prompt or prompt == last_prompt:
                continue
            x = pad + float(frame.get('frame', 0)) * bar_w
            labels.append(f"<text x='{x:.2f}' y='{height - 10}' font-size='10' fill='#e2e8f0'>{self._escape(prompt[:42])}</text>")
            last_prompt = prompt
        svg = f"""<svg xmlns='http://www.w3.org/2000/svg' width='{width}' height='{height}' viewBox='0 0 {width} {height}'>
  <rect width='100%' height='100%' fill='#111827' />
  <text x='{pad}' y='18' font-size='14' fill='#f8fafc'>EDMG Preview Timeline</text>
  <text x='{width - 180}' y='18' font-size='11' fill='#94a3b8'>fps={preview.get('fps')} duration={preview.get('duration')}</text>
  {''.join(beat_lines)}
  {''.join(bars)}
  {''.join(labels)}
</svg>
"""
        out.write_text(svg, encoding='utf-8')
        return str(out)

    def _analysis_duration(self, analysis: Any) -> float:
        try:
            value = getattr(analysis, 'duration', None)
            if value is None and isinstance(analysis, Mapping):
                value = analysis.get('duration')
            return float(value or 0.0) or 1.0
        except Exception:
            return 1.0

    def _analysis_beats(self, analysis: Any) -> Iterable[float]:
        if isinstance(analysis, Mapping):
            beats = analysis.get('beats') or analysis.get('beat_frames') or []
        else:
            beats = getattr(analysis, 'beats', None) or getattr(analysis, 'beat_frames', None) or []
        for beat in beats:
            try:
                yield float(beat)
            except Exception:
                continue

    def _analysis_energy(self, analysis: Any) -> List[float]:
        if isinstance(analysis, Mapping):
            energy = analysis.get('energy') or analysis.get('energy_segments') or []
        else:
            energy = getattr(analysis, 'energy', None) or getattr(analysis, 'energy_segments', None) or []
        out: List[float] = []
        for value in energy:
            try:
                out.append(float(value))
            except Exception:
                pass
        if not out:
            out = [0.0]
        return out

    def _normalize_prompts(self, prompts: Optional[Mapping[Any, str]]) -> Dict[int, str]:
        if not prompts:
            return {0: 'cinematic, detailed, photorealistic'}
        out: Dict[int, str] = {}
        for key, value in prompts.items():
            try:
                frame = int(key)
            except Exception:
                frame = 0
            text = str(value or '').strip()
            if text:
                out[frame] = text
        return dict(sorted(out.items())) or {0: 'cinematic, detailed, photorealistic'}

    def _prompt_for_frame(self, prompts: Mapping[int, str], frame: int) -> str:
        current = ''
        for key, value in prompts.items():
            if key <= frame:
                current = value
            else:
                break
        return current or next(iter(prompts.values()))

    def _resample(self, values: List[float], length: int) -> List[float]:
        if not values:
            return [0.0] * length
        if len(values) == length:
            return list(values)
        if len(values) == 1:
            return [float(values[0])] * length
        out: List[float] = []
        max_src = len(values) - 1
        for idx in range(length):
            pos = (idx / max(length - 1, 1)) * max_src
            left = int(math.floor(pos))
            right = min(left + 1, max_src)
            frac = pos - left
            v = float(values[left]) * (1.0 - frac) + float(values[right]) * frac
            out.append(v)
        return out

    def _escape(self, text: str) -> str:
        return (
            text.replace('&', '&amp;')
            .replace('<', '&lt;')
            .replace('>', '&gt;')
            .replace('"', '&quot;')
            .replace("'", '&apos;')
        )


__all__ = ['PreviewGenerator', 'PreviewFrame']
