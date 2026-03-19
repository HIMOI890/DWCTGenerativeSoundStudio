from __future__ import annotations

import dataclasses
import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np


@dataclass(frozen=True)
class TempoInfo:
    bpm: Optional[float]
    beat_times: List[float]
    onset_strength_mean: float
    onset_strength_peak: float


@dataclass(frozen=True)
class SpectralInfo:
    centroid_mean: float
    centroid_std: float
    bandwidth_mean: float
    bandwidth_std: float
    rolloff_mean: float
    rolloff_std: float
    flatness_mean: float
    flatness_std: float


@dataclass(frozen=True)
class HarmPercInfo:
    harmonic_rms: float
    percussive_rms: float
    harmonic_ratio: float


@dataclass(frozen=True)
class MfccInfo:
    mfcc_mean: List[float]
    mfcc_std: List[float]


@dataclass(frozen=True)
class ChromaInfo:
    chroma_mean: List[float]
    chroma_std: List[float]
    key: Optional[str]
    key_confidence: Optional[float]


@dataclass(frozen=True)
class LoudnessInfo:
    rms: float
    peak: float
    rms_dbfs: float
    peak_dbfs: float
    dynamic_range_db: float


@dataclass(frozen=True)
class AudioAnalysis:
    path: str
    sample_rate: int
    channels: int
    duration_sec: float
    tempo: TempoInfo
    spectral: SpectralInfo
    harmperc: HarmPercInfo
    mfcc: MfccInfo
    chroma: ChromaInfo
    loudness: LoudnessInfo
    energy_score: float
    brightness_score: float
    percussiveness_score: float


_MAJOR_PROFILE = np.array([6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88], dtype=np.float32)
_MINOR_PROFILE = np.array([6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17], dtype=np.float32)
_PITCH_CLASSES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def _default_cache_dir() -> Path:
    env = os.environ.get("DEFORUM_MUSIC_CACHE")
    if env:
        return Path(env).expanduser().resolve()
    return (Path.home() / ".deforum_music_cache").resolve()


def _file_fingerprint(path: Path) -> str:
    st = path.stat()
    payload = f"{path.resolve()}|{st.st_size}|{int(st.st_mtime)}".encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _cache_path_for(audio_path: Path, cache_dir: Path) -> Path:
    return cache_dir / f"{_file_fingerprint(audio_path)}.analysis.json"


def _to_jsonable(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {k: _to_jsonable(v) for k, v in dataclasses.asdict(obj).items()}
    if isinstance(obj, (list, tuple)):
        return [_to_jsonable(x) for x in obj]
    if isinstance(obj, dict):
        return {str(k): _to_jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (np.floating, np.integer)):
        return obj.item()
    return obj


def _estimate_key_from_chroma(chroma_mean: np.ndarray) -> Tuple[Optional[str], Optional[float]]:
    if chroma_mean.shape != (12,) or not np.isfinite(chroma_mean).all():
        return None, None

    v = chroma_mean.astype(np.float32)
    if v.sum() <= 0:
        return None, None
    v = v / (v.sum() + 1e-8)

    best_key: Optional[str] = None
    best_score = -1e9
    scores: List[float] = []

    for i in range(12):
        maj = np.roll(_MAJOR_PROFILE, i)
        minr = np.roll(_MINOR_PROFILE, i)
        maj = maj / maj.sum()
        minr = minr / minr.sum()

        s_maj = float(np.corrcoef(v, maj)[0, 1])
        s_min = float(np.corrcoef(v, minr)[0, 1])

        scores.append(max(s_maj, s_min))

        if s_maj > best_score:
            best_score = s_maj
            best_key = f"{_PITCH_CLASSES[i]} major"
        if s_min > best_score:
            best_score = s_min
            best_key = f"{_PITCH_CLASSES[i]} minor"

    scores_np = np.array(scores, dtype=np.float32)
    conf = float((best_score - scores_np.mean()) / (scores_np.std() + 1e-8))
    conf01 = float(1.0 / (1.0 + np.exp(-conf)))
    return best_key, conf01


def analyze_audio(
    path: str,
    *,
    use_cache: bool = True,
    cache_dir: Optional[str] = None,
    mono: bool = True,
    target_sr: Optional[int] = None,
    n_mfcc: int = 20,
) -> Dict[str, Any]:
    audio_path = Path(path).expanduser()
    if not audio_path.exists():
        raise FileNotFoundError(str(audio_path))

    cdir = Path(cache_dir).expanduser().resolve() if cache_dir else _default_cache_dir()
    cdir.mkdir(parents=True, exist_ok=True)
    cpath = _cache_path_for(audio_path, cdir)

    if use_cache and cpath.exists():
        try:
            with cpath.open("r", encoding="utf-8") as f:
                cached = json.load(f)
            if isinstance(cached, dict) and "sample_rate" in cached and "tempo" in cached:
                return cached
        except Exception:
            pass

    import librosa

    y, sr = librosa.load(str(audio_path), sr=target_sr, mono=mono)
    if y is None or len(y) == 0 or sr is None:
        raise RuntimeError(f"Failed to load audio: {audio_path}")

    channels = 1
    if not mono and getattr(y, "ndim", 1) == 2:
        channels = int(y.shape[0])

    duration = float(len(y) / sr)

    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    rms = float(np.sqrt(np.mean(np.square(y)))) if len(y) else 0.0
    rms_dbfs = float(20.0 * np.log10(max(rms, 1e-12)))
    peak_dbfs = float(20.0 * np.log10(max(peak, 1e-12)))

    frame_rms = librosa.feature.rms(y=y, frame_length=2048, hop_length=512)[0]
    if frame_rms.size:
        fr_db = 20.0 * np.log10(np.maximum(frame_rms, 1e-12))
        dynamic_range_db = float(np.percentile(fr_db, 95) - np.percentile(fr_db, 10))
    else:
        dynamic_range_db = 0.0

    loudness = LoudnessInfo(
        rms=rms,
        peak=peak,
        rms_dbfs=rms_dbfs,
        peak_dbfs=peak_dbfs,
        dynamic_range_db=dynamic_range_db,
    )

    onset_env = librosa.onset.onset_strength(y=y, sr=sr, hop_length=512)
    onset_strength_mean = float(np.mean(onset_env)) if onset_env.size else 0.0
    onset_strength_peak = float(np.max(onset_env)) if onset_env.size else 0.0

    try:
        tempo_val, beat_frames = librosa.beat.beat_track(onset_envelope=onset_env, sr=sr, hop_length=512)
        bpm = float(tempo_val) if tempo_val and np.isfinite(tempo_val) else None
        beat_times = librosa.frames_to_time(beat_frames, sr=sr, hop_length=512).astype(float).tolist()
    except Exception:
        bpm = None
        beat_times = []

    tempo = TempoInfo(bpm=bpm, beat_times=beat_times, onset_strength_mean=onset_strength_mean, onset_strength_peak=onset_strength_peak)

    centroid = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
    bandwidth = librosa.feature.spectral_bandwidth(y=y, sr=sr)[0]
    rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr, roll_percent=0.85)[0]
    flatness = librosa.feature.spectral_flatness(y=y)[0]

    spectral = SpectralInfo(
        centroid_mean=float(np.mean(centroid)) if centroid.size else 0.0,
        centroid_std=float(np.std(centroid)) if centroid.size else 0.0,
        bandwidth_mean=float(np.mean(bandwidth)) if bandwidth.size else 0.0,
        bandwidth_std=float(np.std(bandwidth)) if bandwidth.size else 0.0,
        rolloff_mean=float(np.mean(rolloff)) if rolloff.size else 0.0,
        rolloff_std=float(np.std(rolloff)) if rolloff.size else 0.0,
        flatness_mean=float(np.mean(flatness)) if flatness.size else 0.0,
        flatness_std=float(np.std(flatness)) if flatness.size else 0.0,
    )

    y_harm, y_perc = librosa.effects.hpss(y)
    harm_rms = float(np.sqrt(np.mean(np.square(y_harm)))) if len(y_harm) else 0.0
    perc_rms = float(np.sqrt(np.mean(np.square(y_perc)))) if len(y_perc) else 0.0
    harm_ratio = float(harm_rms / (harm_rms + perc_rms + 1e-12))

    harmperc = HarmPercInfo(harmonic_rms=harm_rms, percussive_rms=perc_rms, harmonic_ratio=harm_ratio)

    mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=n_mfcc)
    mfcc_mean = np.mean(mfcc, axis=1) if mfcc.size else np.zeros((n_mfcc,), dtype=np.float32)
    mfcc_std = np.std(mfcc, axis=1) if mfcc.size else np.zeros((n_mfcc,), dtype=np.float32)

    mfcc_info = MfccInfo(mfcc_mean=mfcc_mean.astype(float).tolist(), mfcc_std=mfcc_std.astype(float).tolist())

    chroma = librosa.feature.chroma_cqt(y=y, sr=sr)
    chroma_mean = np.mean(chroma, axis=1) if chroma.size else np.zeros((12,), dtype=np.float32)
    chroma_std = np.std(chroma, axis=1) if chroma.size else np.zeros((12,), dtype=np.float32)

    key, key_conf = _estimate_key_from_chroma(chroma_mean)

    chroma_info = ChromaInfo(
        chroma_mean=chroma_mean.astype(float).tolist(),
        chroma_std=chroma_std.astype(float).tolist(),
        key=key,
        key_confidence=key_conf,
    )

    energy_raw = (rms * 2.5) + (onset_strength_mean * 0.15)
    energy_score = float(np.clip(energy_raw, 0.0, 1.0))

    centroid_norm = float(spectral.centroid_mean / max(sr / 2.0, 1e-6))
    brightness_score = float(np.clip(centroid_norm * 1.8, 0.0, 1.0))

    percussiveness_score = float(np.clip(1.0 - harm_ratio, 0.0, 1.0))

    analysis = AudioAnalysis(
        path=str(audio_path),
        sample_rate=int(sr),
        channels=int(channels),
        duration_sec=float(duration),
        tempo=tempo,
        spectral=spectral,
        harmperc=harmperc,
        mfcc=mfcc_info,
        chroma=chroma_info,
        loudness=loudness,
        energy_score=energy_score,
        brightness_score=brightness_score,
        percussiveness_score=percussiveness_score,
    )

    out = _to_jsonable(analysis)

    if use_cache:
        try:
            with cpath.open("w", encoding="utf-8") as f:
                json.dump(out, f, indent=2)
        except Exception:
            pass

    return out
