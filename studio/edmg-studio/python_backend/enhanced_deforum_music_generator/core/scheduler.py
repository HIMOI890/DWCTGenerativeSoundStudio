"""
Enhanced Deforum Scheduler
Generates sophisticated Deforum animation schedules from audio and lyrics analysis.
"""

import numpy as np
import json
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from dataclasses import dataclass
import logging

from ..config.config_system import AnimationConfig
from ..utils.logging_utils import get_logger
from .audio_analyzer import AudioFeatures
from .nlp_processor import LyricsSegment

logger = get_logger(__name__)


@dataclass
class ScheduleKeyframe:
    """Individual keyframe in animation schedule."""
    frame: int
    time: float
    prompt: Optional[str] = None
    negative_prompt: Optional[str] = None
    cfg_scale: Optional[float] = None
    strength: Optional[float] = None
    zoom: Optional[float] = None
    rotation: Optional[float] = None
    translation_x: Optional[float] = None
    translation_y: Optional[float] = None
    perspective_flip_theta: Optional[float] = None
    perspective_flip_phi: Optional[float] = None
    perspective_flip_gamma: Optional[float] = None
    perspective_flip_fv: Optional[float] = None


class DeforumScheduler:
    """
    Enhanced scheduler that creates complex Deforum animations synchronized to music.
    """

    def __init__(self, config: AnimationConfig):
        self.config = config
        self.keyframes: List[ScheduleKeyframe] = []

    def build(self, beats, energy, lyrics):
        """Compatibility wrapper used by unit tests.

        This produces a simple list of schedule steps aligned to beat times.
        Each step includes a `prompt` that matches any lyric segment overlapping
        the beat time.
        """
        schedule = []
        for i, bt in enumerate(beats):
            prompt = ""
            for seg in (lyrics or []):
                if float(seg.get("start", 0.0)) <= float(bt) <= float(seg.get("end", 0.0)):
                    prompt = str(seg.get("text", "")).strip()
                    break
            e = float(energy[i]) if energy and i < len(energy) else 0.0
            schedule.append({"time": float(bt), "beat": int(i), "energy": e, "prompt": prompt})
        return schedule

    def build_schedule(
        self,
        audio_features: AudioFeatures,
        lyrics_segments: List[LyricsSegment],
        base_prompt: str = "beautiful abstract art",
        negative_prompt: str = "ugly, blurry, low quality"
    ) -> Dict[str, Any]:
        """
        Build complete Deforum schedule from audio and lyrics.
        
        Args:
            audio_features: Extracted audio features
            lyrics_segments: Transcribed lyrics segments
            base_prompt: Base prompt for generation
            negative_prompt: Negative prompt for generation
            
        Returns:
            Complete Deforum schedule dictionary
        """
        logger.info("Building Deforum animation schedule")
        
        # Calculate total frames
        total_frames = int(audio_features.duration * self.config.fps)
        
        # Clear existing keyframes
        self.keyframes = []
        
        # Generate different types of keyframes
        self._generate_beat_keyframes(audio_features, total_frames)
        self._generate_energy_keyframes(audio_features, total_frames)
        self._generate_lyrics_keyframes(lyrics_segments, base_prompt, negative_prompt)
        self._generate_smooth_transitions(total_frames)
        
        # Build final schedule
        schedule = self._build_deforum_json(total_frames, base_prompt, negative_prompt)
        
        logger.info(f"Schedule generated: {total_frames} frames, {len(self.keyframes)} keyframes")
        return schedule

    def _generate_beat_keyframes(self, features: AudioFeatures, total_frames: int):
        """Generate keyframes synchronized to beats."""
        for beat_time in features.beats:
            frame = int(beat_time * self.config.fps)
            if frame >= total_frames:
                continue
                
            # Beat-synchronized zoom pulse
            zoom_intensity = self.config.zoom_range * self._get_energy_at_time(features, beat_time)
            zoom_value = self.config.zoom_base + zoom_intensity
            
            # Beat-synchronized rotation
            rotation = np.random.uniform(-self.config.rotation_range, self.config.rotation_range)
            
            # Beat-synchronized perspective changes
            perspective_intensity = 0.5 * self._get_energy_at_time(features, beat_time)
            
            keyframe = ScheduleKeyframe(
                frame=frame,
                time=beat_time,
                zoom=zoom_value,
                rotation=rotation,
                perspective_flip_theta=perspective_intensity * np.random.uniform(-1, 1),
                perspective_flip_phi=perspective_intensity * np.random.uniform(-1, 1)
            )
            
            self.keyframes.append(keyframe)

    def _generate_energy_keyframes(self, features: AudioFeatures, total_frames: int):
        """Generate keyframes based on energy levels."""
        # Sample energy at regular intervals
        energy_sample_rate = 4  # 4 times per second
        for i in range(0, int(features.duration * energy_sample_rate)):
            time = i / energy_sample_rate
            frame = int(time * self.config.fps)
            
            if frame >= total_frames:
                continue
                
            energy = self._get_energy_at_time(features, time)
            
            # Energy-based CFG scale modulation
            cfg_scale = self.config.cfg_scale_base + (self.config.cfg_scale_range * energy)
            
            # Energy-based strength modulation
            strength = self.config.strength_base + (self.config.strength_range * (energy - 0.5))
            strength = np.clip(strength, 0.1, 1.0)
            
            # Energy-based translation
            translation_intensity = self.config.translation_range * energy
            translation_x = translation_intensity * np.sin(time * 0.5)  # Smooth wave
            translation_y = translation_intensity * np.cos(time * 0.3)
            
            keyframe = ScheduleKeyframe(
                frame=frame,
                time=time,
                cfg_scale=cfg_scale,
                strength=strength,
                translation_x=translation_x,
                translation_y=translation_y
            )
            
            self.keyframes.append(keyframe)

    def _generate_lyrics_keyframes(
        self, 
        segments: List[LyricsSegment], 
        base_prompt: str, 
        negative_prompt: str
    ):
        """Generate prompt keyframes from lyrics."""
        for segment in segments:
            frame = int(segment.start * self.config.fps)
            
            # Build enhanced prompt
            enhanced_prompt = self._enhance_prompt_with_lyrics(base_prompt, segment)
            
            keyframe = ScheduleKeyframe(
                frame=frame,
                time=segment.start,
                prompt=enhanced_prompt,
                negative_prompt=negative_prompt
            )
            
            self.keyframes.append(keyframe)

    def _enhance_prompt_with_lyrics(self, base_prompt: str, segment: LyricsSegment) -> str:
        """Enhance base prompt with lyrics-based elements."""
        # Use prompt suggestions if available
        if segment.prompt_suggestions:
            suggestions = ", ".join(segment.prompt_suggestions)
            return f"{base_prompt}, {suggestions}"
        
        # Fallback: extract mood and add appropriate style
        text_lower = segment.text.lower()
        
        style_additions = []
        
        # Mood-based enhancements
        if any(word in text_lower for word in ['love', 'heart', 'romance']):
            style_additions.append("romantic atmosphere, warm colors, soft lighting")
        elif any(word in text_lower for word in ['dark', 'shadow', 'night']):
            style_additions.append("dark atmosphere, moody lighting, shadows")
        elif any(word in text_lower for word in ['bright', 'light', 'sun']):
            style_additions.append("bright, luminous, golden hour lighting")
        elif any(word in text_lower for word in ['dance', 'move', 'rhythm']):
            style_additions.append("dynamic motion, flowing movement, energetic")
        elif any(word in text_lower for word in ['dream', 'fantasy', 'magic']):
            style_additions.append("dreamlike, surreal, magical atmosphere")
        
        if style_additions:
            return f"{base_prompt}, {', '.join(style_additions)}"
        
        return base_prompt

    def _generate_smooth_transitions(self, total_frames: int):
        """Generate smooth transition keyframes between major changes."""
        # Sort keyframes by frame
        self.keyframes.sort(key=lambda k: k.frame)
        
        # Add transition keyframes for smooth parameter changes
        transition_keyframes = []
        
        for i in range(len(self.keyframes) - 1):
            current = self.keyframes[i]
            next_kf = self.keyframes[i + 1]
            
            frame_diff = next_kf.frame - current.frame
            
            # Add intermediate keyframes for smooth transitions
            if frame_diff > self.config.fps:  # More than 1 second apart
                num_transitions = min(3, frame_diff // (self.config.fps // 2))
                
                for j in range(1, num_transitions):
                    transition_frame = current.frame + (frame_diff * j // num_transitions)
                    transition_time = transition_frame / self.config.fps
                    
                    # Interpolate parameters
                    transition_keyframe = ScheduleKeyframe(
                        frame=transition_frame,
                        time=transition_time,
                        zoom=self._interpolate_value(current.zoom, next_kf.zoom, j / num_transitions),
                        rotation=self._interpolate_value(current.rotation, next_kf.rotation, j / num_transitions),
                        cfg_scale=self._interpolate_value(current.cfg_scale, next_kf.cfg_scale, j / num_transitions),
                        strength=self._interpolate_value(current.strength, next_kf.strength, j / num_transitions)
                    )
                    
                    transition_keyframes.append(transition_keyframe)
        
        self.keyframes.extend(transition_keyframes)
        self.keyframes.sort(key=lambda k: k.frame)

    def _interpolate_value(self, start: Optional[float], end: Optional[float], ratio: float) -> Optional[float]:
        """Interpolate between two values."""
        if start is None or end is None:
            return start or end
        return start + (end - start) * ratio

    def _get_energy_at_time(self, features: AudioFeatures, time: float) -> float:
        """Get normalized energy at specific time."""
        if not features.energy_times or not features.energy:
            return 0.5
            
        # Find closest time index
        times = np.array(features.energy_times)
        idx = np.argmin(np.abs(times - time))
        return features.energy[idx]

    def _build_deforum_json(
        self, 
        total_frames: int, 
        base_prompt: str, 
        negative_prompt: str
    ) -> Dict[str, Any]:
        """Build complete Deforum-compatible JSON schedule."""
        
        # Initialize parameter dictionaries
        prompts = {0: base_prompt}
        negative_prompts = {0: negative_prompt}
        cfg_scales = {0: self.config.cfg_scale_base}
        strengths = {0: self.config.strength_base}
        zooms = {0: self.config.zoom_base}
        rotations = {0: 0.0}
        translation_x = {0: 0.0}
        translation_y = {0: 0.0}
        perspective_flip_theta = {0: 0.0}
        perspective_flip_phi = {0: 0.0}
        perspective_flip_gamma = {0: 0.0}
        perspective_flip_fv = {0: 53.0}
        
        # Fill in keyframes
        for keyframe in self.keyframes:
            if keyframe.prompt is not None:
                prompts[keyframe.frame] = keyframe.prompt
            if keyframe.negative_prompt is not None:
                negative_prompts[keyframe.frame] = keyframe.negative_prompt
            if keyframe.cfg_scale is not None:
                cfg_scales[keyframe.frame] = keyframe.cfg_scale
            if keyframe.strength is not None:
                strengths[keyframe.frame] = keyframe.strength
            if keyframe.zoom is not None:
                zooms[keyframe.frame] = keyframe.zoom
            if keyframe.rotation is not None:
                rotations[keyframe.frame] = keyframe.rotation
            if keyframe.translation_x is not None:
                translation_x[keyframe.frame] = keyframe.translation_x
            if keyframe.translation_y is not None:
                translation_y[keyframe.frame] = keyframe.translation_y
            if keyframe.perspective_flip_theta is not None:
                perspective_flip_theta[keyframe.frame] = keyframe.perspective_flip_theta
            if keyframe.perspective_flip_phi is not None:
                perspective_flip_phi[keyframe.frame] = keyframe.perspective_flip_phi
        
        # Build complete Deforum schedule
        schedule = {
            "W": int(self.config.resolution.split('x')[0]),
            "H": int(self.config.resolution.split('x')[1]),
            "max_frames": total_frames,
            "fps": self.config.fps,
            "sampler": self.config.sampler,
            "steps": self.config.steps,
            "seed": -1,
            
            # Animation parameters
            "prompts": prompts,
            "negative_prompts": negative_prompts,
            "cfg_scale_schedule": cfg_scales,
            "strength_schedule": strengths,
            "zoom": zooms,
            "angle": rotations,
            "transform_center_x": "0:(512)",
            "transform_center_y": "0:(512)",
            "translation_x": translation_x,
            "translation_y": translation_y,
            "translation_z": "0:(0)",
            "perspective_flip_theta": perspective_flip_theta,
            "perspective_flip_phi": perspective_flip_phi,
            "perspective_flip_gamma": perspective_flip_gamma,
            "perspective_flip_fv": perspective_flip_fv,
            
            # Motion blur
            "motion_blur": True,
            "motion_blur_kernel": 1,
            "motion_blur_sigma": 0.5,
            
            # Noise settings
            "noise_schedule": "0:(0.065)",
            "noise_type": "perlin",
            
            # Color coherence
            "color_coherence": "LAB",
            "color_force": "0:(0.5)",
            
            # Anti blur
            "anti_blur": True,
            "anti_blur_sigma": 0.5,
            
            # Video output
            "video_init_path": "",
            "extract_from_video": 0,
            "video_mask_path": "",
            
            # Resume settings
            "resume_from_timestring": False,
            "resume_timestring": ""
        }
        
        return schedule

    def save_schedule(self, schedule: Dict[str, Any], output_path: str):
        """Save schedule to JSON file."""
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        with open(output_path, 'w') as f:
            json.dump(schedule, f, indent=2, ensure_ascii=False)
        
        logger.info(f"Schedule saved to {output_path}")

    def create_preview_schedule(
        self, 
        full_schedule: Dict[str, Any], 
        preview_duration: int = 10
    ) -> Dict[str, Any]:
        """Create a shortened preview version of the schedule."""
        preview_frames = preview_duration * self.config.fps
        
        # Copy the full schedule
        preview_schedule = full_schedule.copy()
        preview_schedule["max_frames"] = preview_frames
        
        # Filter keyframes to preview duration
        for param_name in ["prompts", "negative_prompts", "cfg_scale_schedule", 
                          "strength_schedule", "zoom", "angle", "translation_x", 
                          "translation_y", "perspective_flip_theta", "perspective_flip_phi"]:
            if param_name in preview_schedule:
                filtered_params = {}
                for frame, value in preview_schedule[param_name].items():
                    if int(frame) <= preview_frames:
                        filtered_params[frame] = value
                preview_schedule[param_name] = filtered_params
        
        return preview_schedule

    def analyze_schedule_complexity(self, schedule: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze the complexity and characteristics of the generated schedule."""
        total_frames = schedule.get("max_frames", 0)
        
        # Count keyframes per parameter
        keyframe_counts = {}
        for param in ["prompts", "cfg_scale_schedule", "zoom", "angle"]:
            if param in schedule:
                keyframe_counts[param] = len(schedule[param])
        
        # Calculate change frequency
        change_frequency = sum(keyframe_counts.values()) / max(total_frames, 1)
        
        # Analyze prompt changes
        prompt_changes = len(schedule.get("prompts", {}))
        avg_prompt_duration = total_frames / max(prompt_changes, 1) / schedule.get("fps", 30)
        
        return {
            "total_frames": total_frames,
            "duration_seconds": total_frames / schedule.get("fps", 30),
            "keyframe_counts": keyframe_counts,
            "change_frequency": change_frequency,
            "prompt_changes": prompt_changes,
            "avg_prompt_duration": avg_prompt_duration,
            "complexity_score": change_frequency * 10  # Arbitrary scoring
        }