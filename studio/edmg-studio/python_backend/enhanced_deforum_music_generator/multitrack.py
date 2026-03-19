from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from .config.config_system import AudioConfig
from .core.audio_analyzer import AudioAnalyzer
from .public_api import AudioAnalysis, DeforumMusicGenerator


AUDIO_EXTS = {'.wav', '.mp3', '.flac', '.ogg', '.m4a', '.aac'}
DEFAULT_WEIGHTS = {
    'drums': 0.32,
    'percussion': 0.32,
    'bass': 0.24,
    'vocals': 0.18,
    'lead': 0.16,
    'synth': 0.14,
    'pads': 0.10,
    'fx': 0.08,
    'mixed': 1.0,
    'master': 1.0,
}


@dataclass(frozen=True)
class TrackSpec:
    name: str
    path: str
    weight: float


class MultitrackProcessor:
    def __init__(self, track_weights: Optional[Mapping[str, float]] = None, audio_config: Optional[AudioConfig] = None):
        self.track_weights = {str(k).lower(): float(v) for k, v in dict(track_weights or {}).items()}
        self.audio_config = audio_config or AudioConfig(max_duration=1800)
        self.analyzer = AudioAnalyzer(self.audio_config)

    def discover_tracks(self, source: str | Path | Mapping[str, str]) -> List[TrackSpec]:
        if isinstance(source, Mapping):
            specs = [
                TrackSpec(name=str(name), path=str(path), weight=self._default_weight(str(name)))
                for name, path in source.items()
            ]
            return sorted(specs, key=lambda s: s.name)

        source_path = Path(source)
        if source_path.is_file():
            return [TrackSpec(name=source_path.stem.lower(), path=str(source_path), weight=1.0)]
        if not source_path.is_dir():
            raise FileNotFoundError(f'No such multitrack source: {source_path}')

        specs: List[TrackSpec] = []
        for candidate in sorted(source_path.iterdir()):
            if candidate.is_file() and candidate.suffix.lower() in AUDIO_EXTS:
                specs.append(
                    TrackSpec(
                        name=candidate.stem.lower(),
                        path=str(candidate),
                        weight=self._default_weight(candidate.stem.lower()),
                    )
                )
        if not specs:
            raise FileNotFoundError(f'No audio stems were found in {source_path}')
        return specs

    def analyze(self, source: str | Path | Mapping[str, str]) -> Dict[str, Any]:
        tracks = self.discover_tracks(source)
        analyses: Dict[str, Dict[str, Any]] = {}
        duration = 0.0
        combined_tempos: List[tuple[float, float]] = []
        energy_series: List[tuple[List[float], float]] = []
        beat_pool: List[float] = []

        for spec in tracks:
            analysis = self.analyzer.analyze(spec.path)
            analyses[spec.name] = {
                'path': spec.path,
                'weight': spec.weight,
                'analysis': analysis,
                'avg_energy': round(self._avg(analysis.get('energy') or []), 4),
            }
            duration = max(duration, float(analysis.get('duration') or 0.0))
            combined_tempos.append((float(analysis.get('tempo') or 0.0), spec.weight))
            energy_series.append((list(analysis.get('energy') or []), spec.weight))
            beat_pool.extend(float(b) for b in (analysis.get('beats') or []))

        combined_energy = self._combine_energy(energy_series)
        dominant = sorted(
            analyses.items(),
            key=lambda item: (float(item[1].get('avg_energy') or 0.0), float(item[1].get('weight') or 0.0)),
            reverse=True,
        )

        combined = {
            'duration': round(duration, 4),
            'tempo': round(self._weighted_average(combined_tempos) or 0.0, 4),
            'beats': self._merge_beats(beat_pool),
            'energy': combined_energy,
            'dominant_tracks': [name for name, _ in dominant[:3]],
        }
        return {'ok': True, 'tracks': analyses, 'combined': combined}

    def build_audio_analysis(self, source: str | Path | Mapping[str, str]) -> AudioAnalysis:
        result = self.analyze(source)
        combined = result['combined']
        return AudioAnalysis(
            duration=float(combined['duration']),
            tempo_bpm=float(combined['tempo']),
            beats=list(combined['beats']),
            energy=list(combined['energy']),
        )

    def export_manifest(self, result: Mapping[str, Any], output_path: str | Path) -> str:
        out = Path(output_path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(dict(result), indent=2), encoding='utf-8')
        return str(out)

    def _default_weight(self, track_name: str) -> float:
        name = str(track_name).lower()
        for key, value in self.track_weights.items():
            if key in name:
                return float(value)
        for key, value in DEFAULT_WEIGHTS.items():
            if key in name:
                return float(value)
        return 0.12

    def _weighted_average(self, pairs: Iterable[tuple[float, float]]) -> float:
        num = 0.0
        den = 0.0
        for value, weight in pairs:
            if value <= 0.0 or weight <= 0.0:
                continue
            num += value * weight
            den += weight
        return (num / den) if den > 0 else 0.0

    def _avg(self, values: Iterable[float]) -> float:
        vals = [float(v) for v in values]
        return sum(vals) / len(vals) if vals else 0.0

    def _combine_energy(self, series: Iterable[tuple[List[float], float]]) -> List[float]:
        items = list(series)
        if not items:
            return [0.0]
        target = max((len(values) for values, _ in items), default=1)
        if target <= 0:
            target = 1
        accum = [0.0] * target
        weight_sum = [0.0] * target
        for values, weight in items:
            resampled = self._resample(values or [0.0], target)
            for idx, value in enumerate(resampled):
                accum[idx] += float(value) * float(weight)
                weight_sum[idx] += float(weight)
        out = []
        for idx in range(target):
            out.append(round(accum[idx] / weight_sum[idx], 4) if weight_sum[idx] > 0 else 0.0)
        return out

    def _resample(self, values: List[float], length: int) -> List[float]:
        if not values:
            return [0.0] * length
        if len(values) == length:
            return [float(v) for v in values]
        if len(values) == 1:
            return [float(values[0])] * length
        max_src = len(values) - 1
        out: List[float] = []
        for idx in range(length):
            pos = (idx / max(length - 1, 1)) * max_src
            left = int(math.floor(pos))
            right = min(left + 1, max_src)
            frac = pos - left
            val = float(values[left]) * (1.0 - frac) + float(values[right]) * frac
            out.append(val)
        return out

    def _merge_beats(self, beats: Iterable[float]) -> List[float]:
        merged: List[float] = []
        for beat in sorted(float(b) for b in beats if b >= 0.0):
            if not merged or abs(merged[-1] - beat) > 0.05:
                merged.append(round(beat, 4))
        return merged


class MultiTrackGenerator:
    def __init__(self, processor: Optional[MultitrackProcessor] = None):
        self.processor = processor or MultitrackProcessor()
        self.generator = DeforumMusicGenerator()

    def build_deforum_settings(self, source: str | Path | Mapping[str, str], user_settings: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        result = self.processor.analyze(source)
        analysis = self.processor.build_audio_analysis(source)
        settings = self.generator.build_deforum_settings(analysis, dict(user_settings or {}))
        settings['_edmg_multitrack'] = {
            'tracks': list(result['tracks'].keys()),
            'dominant_tracks': list(result['combined']['dominant_tracks']),
            'tempo': result['combined']['tempo'],
        }
        return settings


__all__ = ['TrackSpec', 'MultitrackProcessor', 'MultiTrackGenerator']
