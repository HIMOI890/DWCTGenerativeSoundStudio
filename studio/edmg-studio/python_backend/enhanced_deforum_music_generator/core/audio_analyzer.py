"""
Enhanced Audio Analyzer
Performs comprehensive beat detection, energy analysis, and audio feature extraction.
"""

import librosa
import numpy as np
import soundfile as sf
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass
import logging

from ..config.config_system import AudioConfig
from ..utils.logging_utils import get_logger

logger = get_logger(__name__)


@dataclass
class AudioFeatures:
    """Container for extracted audio features."""
    duration: float
    sample_rate: int
    tempo: float
    beats: List[float]
    beat_frames: List[int]
    energy: List[float]
    energy_times: List[float]
    spectral_centroid: List[float]
    spectral_rolloff: List[float]
    mfcc: np.ndarray
    chroma: np.ndarray
    onset_strength: List[float]
    onset_times: List[float]
    rms_energy: List[float]
    zero_crossing_rate: List[float]


class AudioAnalyzer:
    """
    Enhanced audio analyzer with comprehensive feature extraction.
    """

    def __init__(self, config: AudioConfig):
        self.config = config
        self.cache = {}

    def analyze_features(self, audio_path: str, enable_cache: bool = True) -> AudioFeatures:
        """
        Perform comprehensive audio analysis.
        
        Args:
            audio_path: Path to audio file
            enable_cache: Whether to use cached results
            
        Returns:
            AudioFeatures object with all extracted features
        """
        audio_path = Path(audio_path)
        
        if not audio_path.exists():
            raise FileNotFoundError(f"Audio file not found: {audio_path}")

        # Check cache
        cache_key = f"{audio_path}_{audio_path.stat().st_mtime}"
        if enable_cache and cache_key in self.cache:
            logger.info(f"Using cached analysis for {audio_path.name}")
            return self.cache[cache_key]

        logger.info(f"Analyzing audio: {audio_path.name}")
        
        # Load and preprocess audio
        y, sr = self._load_audio(audio_path)
        
        # Extract all features
        features = self._extract_features(y, sr)
        
        # Cache results
        if enable_cache:
            self.cache[cache_key] = features
            
        logger.info(f"Analysis complete: {features.duration:.2f}s, {features.tempo:.1f} BPM, {len(features.beats)} beats")
        return features

    def analyze(self, audio_path: str, enable_cache: bool = True) -> Dict[str, Any]:
        """
        Analyze audio and return a JSON-serializable dictionary.

        This wrapper keeps unit-tests (and the UI) simple while preserving the
        full-featured `analyze_features()` API.
        """
        features = self.analyze_features(audio_path, enable_cache=enable_cache)
        return {
            "duration": float(features.duration),
            "sample_rate": int(features.sample_rate),
            "tempo": float(features.tempo),
            "beats": list(features.beats),
            "energy": list(features.energy),
            "onset_strength": list(features.onset_strength),
            "onset_times": list(features.onset_times),
            "spectral_centroid": list(features.spectral_centroid),
            "spectral_rolloff": list(features.spectral_rolloff),
            "rms_energy": list(features.rms_energy),
        }

    def _load_audio(self, audio_path: Path) -> Tuple[np.ndarray, int]:
        """Load and preprocess audio file."""
        try:
            # Load audio with librosa
            y, sr = librosa.load(
                str(audio_path), 
                sr=self.config.sample_rate,
                duration=self.config.max_duration
            )
            
            # Apply noise reduction if enabled
            if getattr(self.config, "enable_noise_reduction", False):
                y = self._reduce_noise(y)
                
            # Normalize audio if enabled
            if getattr(self.config, "normalize_audio", False):
                y = librosa.util.normalize(y)
                
            logger.debug(f"Loaded audio: {len(y)} samples, {sr} Hz, {len(y)/sr:.2f}s")
            return y, sr
            
        except Exception as e:
            logger.error(f"Failed to load audio: {e}")
            raise

    def _reduce_noise(self, y: np.ndarray) -> np.ndarray:
        """Simple noise reduction using spectral gating."""
        try:
            # Compute magnitude spectrogram
            D = librosa.stft(y)
            magnitude = np.abs(D)
            
            # Estimate noise floor from first 10% of signal
            noise_frame_count = int(magnitude.shape[1] * 0.1)
            noise_spectrum = np.mean(magnitude[:, :noise_frame_count], axis=1, keepdims=True)
            
            # Apply spectral gating
            gate_threshold = noise_spectrum * 3.0  # 3x noise floor
            mask = magnitude > gate_threshold
            
            # Apply mask and reconstruct
            D_filtered = D * mask
            y_filtered = librosa.istft(D_filtered)
            
            return y_filtered
            
        except Exception as e:
            logger.warning(f"Noise reduction failed: {e}")
            return y

    def _extract_features(self, y: np.ndarray, sr: int) -> AudioFeatures:
        """Extract comprehensive audio features."""
        duration = len(y) / sr
        
        # Beat tracking
        tempo, beat_frames = librosa.beat.beat_track(
            y=y, sr=sr, units=getattr(self.config, "beat_track_units", "time")
        )
        beat_times = librosa.frames_to_time(beat_frames, sr=sr)
        
        # Energy analysis (frame-wise RMS)
        frame_length = int(0.05 * sr)  # 50ms frames
        hop_length = int(0.025 * sr)   # 25ms hop
        
        rms = librosa.feature.rms(
            y=y, frame_length=frame_length, hop_length=hop_length
        )[0]
        rms_times = librosa.frames_to_time(range(len(rms)), sr=sr, hop_length=hop_length)
        
        # Normalize energy
        energy = rms / (np.max(rms) + 1e-6)
        
        # Spectral features
        spectral_centroids = librosa.feature.spectral_centroid(y=y, sr=sr)[0]
        spectral_rolloff = librosa.feature.spectral_rolloff(y=y, sr=sr)[0]
        
        # MFCC features
        mfcc = librosa.feature.mfcc(y=y, sr=sr, n_mfcc=13)
        
        # Chroma features
        chroma = librosa.feature.chroma_stft(y=y, sr=sr)
        
        # Onset detection
        onset_frames = librosa.onset.onset_detect(y=y, sr=sr)
        onset_times = librosa.frames_to_time(onset_frames, sr=sr)
        onset_strength = librosa.onset.onset_strength(y=y, sr=sr)
        
        # Zero crossing rate
        zcr = librosa.feature.zero_crossing_rate(y)[0]
        
        return AudioFeatures(
            duration=duration,
            sample_rate=sr,
            tempo=float(tempo),
            beats=beat_times.tolist(),
            beat_frames=beat_frames.tolist(),
            energy=energy.tolist(),
            energy_times=rms_times.tolist(),
            spectral_centroid=spectral_centroids.tolist(),
            spectral_rolloff=spectral_rolloff.tolist(),
            mfcc=mfcc,
            chroma=chroma,
            onset_strength=onset_strength.tolist(),
            onset_times=onset_times.tolist(),
            rms_energy=rms.tolist(),
            zero_crossing_rate=zcr.tolist()
        )

    def get_energy_at_time(self, features: AudioFeatures, time: float) -> float:
        """Get normalized energy level at specific time."""
        if not features.energy_times or not features.energy:
            return 0.5
            
        # Find closest time index
        times = np.array(features.energy_times)
        idx = np.argmin(np.abs(times - time))
        return features.energy[idx]

    def get_beat_intervals(self, features: AudioFeatures) -> List[float]:
        """Calculate intervals between beats."""
        if len(features.beats) < 2:
            return []
        return [features.beats[i] - features.beats[i-1] for i in range(1, len(features.beats))]

    def detect_sections(self, features: AudioFeatures, num_sections: int = 4) -> List[Dict[str, Any]]:
        """Detect musical sections based on audio features."""
        duration = features.duration
        section_duration = duration / num_sections
        
        sections = []
        for i in range(num_sections):
            start_time = i * section_duration
            end_time = (i + 1) * section_duration
            
            # Calculate average energy for this section
            start_idx = int(start_time * len(features.energy) / duration)
            end_idx = int(end_time * len(features.energy) / duration)
            avg_energy = np.mean(features.energy[start_idx:end_idx])
            
            # Count beats in section
            beats_in_section = [b for b in features.beats if start_time <= b < end_time]
            
            sections.append({
                "start": start_time,
                "end": end_time,
                "duration": section_duration,
                "avg_energy": avg_energy,
                "beat_count": len(beats_in_section),
                "beats": beats_in_section
            })
        
        return sections

    def export_analysis(self, features: AudioFeatures, output_path: str):
        """Export analysis results to JSON."""
        import json
        
        data = {
            "duration": features.duration,
            "sample_rate": features.sample_rate,
            "tempo": features.tempo,
            "beats": features.beats,
            "energy": features.energy,
            "energy_times": features.energy_times,
            "spectral_centroid": features.spectral_centroid,
            "spectral_rolloff": features.spectral_rolloff,
            "onset_times": features.onset_times,
            "rms_energy": features.rms_energy,
            "zero_crossing_rate": features.zero_crossing_rate,
            "sections": self.detect_sections(features)
        }
        
        with open(output_path, 'w') as f:
            json.dump(data, f, indent=2)
            
        logger.info(f"Analysis exported to {output_path}")
