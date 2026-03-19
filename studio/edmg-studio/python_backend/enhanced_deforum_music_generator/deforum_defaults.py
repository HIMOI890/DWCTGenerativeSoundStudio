"""
Deforum defaults

This module provides a *complete* Deforum settings template (101 keys),
based on the A1111 Deforum extension schema.

Primary entrypoints:
- make_deforum_settings_template(overrides=None) -> dict
- deep_merge_dicts(base, overlay) -> dict
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, MutableMapping, Optional
import time


def make_deforum_settings_template(overrides: Optional[Mapping[str, Any]] = None) -> Dict[str, Any]:
    """
    Return a full Deforum settings dict (all known keys), optionally overridden.

    Notes:
    - Width/height synonyms are supported via "width"/"height" overriding "W"/"H".
    - "cfg_scale" synonym is supported via "cfg_scale" overriding "scale".
    """
    settings = dict(overrides or {})
    return {
            # Run settings
            # Defaults tuned for music-video workflows.
            # Primary default: 720p @ 30fps (easy to preview/edit, scales upward).
            "W": settings.get("W", settings.get("width", 1280)),
            "H": settings.get("H", settings.get("height", 720)),
            "seed": settings.get("seed", -1),
            "sampler": settings.get("sampler", "Euler a"),
            "steps": settings.get("steps", 30),
            "scale": settings.get("scale", settings.get("cfg_scale", 7.0)),  # Note: Deforum uses 'scale' not 'cfg_scale'
            "ddim_eta": 0.0,
            "dynamic_threshold": None,
            "static_threshold": None,
            
            # Animation settings
            "animation_mode": "3D",
            "max_frames": settings.get("max_frames", 1440),
            "border": "replicate",
            
            # Motion parameters - using our optimized schedules
            "zoom": settings.get("zoom", "0:(1.0)"),
            "angle": settings.get("angle", "0:(0)"),
            "transform_center_x": settings.get("transform_center_x", "0:(0.5)"),
            "transform_center_y": settings.get("transform_center_y", "0:(0.5)"),
            "translation_x": settings.get("translation_x", "0:(0)"),
            "translation_y": settings.get("translation_y", "0:(0)"),
            "translation_z": settings.get("translation_z", "0:(2.0)"),
            "rotation_3d_x": settings.get("rotation_3d_x", "0:(0)"),
            "rotation_3d_y": settings.get("rotation_3d_y", "0:(0)"),
            "rotation_3d_z": settings.get("rotation_3d_z", "0:(0)"),
            
            # Depth/3D settings
            "use_depth_warping": settings.get("use_depth_warping", True),
            "midas_weight": 0.3,
            "near_plane": settings.get("near_schedule", "0:(200)").split(":(")[1].rstrip(")"),
            "far_plane": settings.get("far_schedule", "0:(10000)").split(":(")[1].rstrip(")"),
            "fov": settings.get("fov_schedule", "0:(70)").split(":(")[1].rstrip(")"),
            "padding_mode": "border",
            "sampling_mode": "bicubic",
            
            # Coherence settings
            "color_coherence": settings.get("color_coherence", "LAB"),
            "diffusion_cadence": settings.get("diffusion_cadence", 2),
            
            # Strength and noise
            "strength_schedule": settings.get("strength_schedule", "0:(0.65)"),
            "noise_schedule": settings.get("noise_schedule", "0:(0.04)"),
            "contrast_schedule": settings.get("contrast_schedule", "0:(1.0)"),
            
            # Prompts
            "prompts": settings.get("prompts", {}),
            "negative_prompts": {
                "0": settings.get("animation_prompts_negative", 
                                "blurry, low quality, distorted, watermark, text, worst quality")
            },
            
            # Video output settings
            "fps": settings.get("fps", 30),
            "output_format": "mp4",
            "ffmpeg_location": "ffmpeg",
            "add_soundtrack": "File",
            "soundtrack_path": settings.get("soundtrack_path", ""),
            
            # Init settings
            "use_init": False,
            "strength_0_no_init": True,
            "init_image": None,
            
            # Video input settings (for music sync)
            "video_init_path": "",
            "extract_nth_frame": 1,
            "overwrite_extracted_frames": True,
            "use_mask_video": False,
            
            # Advanced settings
            "resume_from_timestring": False,
            "resume_timestring": "",
            
            # Model and VAE
            "override_settings_with_file": False,
            "custom_settings_file": "",
            "save_settings": True,
            "save_samples": True,
            "display_samples": True,
            "save_sample_per_step": False,
            "show_sample_per_step": False,
            
            # Batch settings
            "batch_name": settings.get("batch_name", f"DefMusic_{int(time.time())}"),
            "filename_format": "{timestring}_{index:05d}_{seed}",
            "seed_behavior": "iter",
            "make_grid": False,
            "grid_rows": 2,
            
            # Anti-blur and sharpening
            "kernel_schedule": "0:(5)",
            "sigma_schedule": "0:(1.0)",
            "amount_schedule": "0:(0.1)",
            "threshold_schedule": "0:(0.0)",
            
            # Color matching
            "color_match_frame_str": "1",
            "color_match_input": "",
            
            # Optical flow settings  
            "optical_flow_redo_generation": "None",
            "optical_flow_cadence": "None",
            
            # Generation settings
            "enable_checkpoint_schedules": False,
            "enable_clipskip_schedules": False,
            "enable_steps_schedules": settings.get("enable_steps_scheduling", False),
            "steps_schedule": settings.get("steps_schedule", f"0:({settings.get('steps', 30)})"),
            
            # CFG schedule
            "enable_cfg_schedules": True,
            "cfg_scale_schedule": settings.get("cfg_scale_schedule", f"0:({settings.get('cfg_scale', 7.0)})"),
            
            # Seed schedules
            "enable_seed_schedules": False,
            "seed_schedule": f"0:({settings.get('seed', -1)})",
            
            # Subseed schedules  
            "enable_subseed_schedules": False,
            "subseed_schedule": "0:(-1)",
            "subseed_strength_schedule": "0:(0.0)",
            
            # Anti-blur
            "enable_kernel_schedules": False,
            "enable_sigma_schedules": False,
            "enable_amount_schedules": False,
            "enable_threshold_schedules": False,
            
            # parseq integration
            "parseq_manifest": "",
            "parseq_use_deltas": True,
            
            # Turbo settings
            "turbo_mode": False,
            "turbo_steps": "3",
            "turbo_preroll": "10",
            
            # Hybrid video settings
            "hybrid_generate_inputframes": False,
            "hybrid_use_first_frame_as_init_image": True,
            "hybrid_motion": "None",
            "hybrid_motion_use_prev_img": False,
            "hybrid_flow_method": "Farneback",
            "hybrid_composite": "None",
            "hybrid_comp_mask_type": "None",
            "hybrid_comp_mask_inverse": False,
            "hybrid_comp_mask_equalize": "None",
            "hybrid_comp_mask_auto_contrast": False,
            "hybrid_comp_save_extra_frames": False
        }


def deep_merge_dicts(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> Dict[str, Any]:
    """Deep-merge overlay into base (dicts merged recursively)."""
    out: Dict[str, Any] = deepcopy(dict(base))
    _deep_merge_into(out, overlay)
    return out


def _deep_merge_into(dst: MutableMapping[str, Any], overlay: Mapping[str, Any]) -> None:
    for k, v in overlay.items():
        if isinstance(v, Mapping) and isinstance(dst.get(k), Mapping):
            _deep_merge_into(dst[k], v)  # type: ignore[arg-type]
        else:
            dst[k] = deepcopy(v)
