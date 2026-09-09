
from __future__ import annotations

import gc
import hashlib
import importlib
import importlib.metadata
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import UserFacingError
from .deforum_motion import DeforumMotionScheduleBundle, evaluate_motion_state
from .deforum_normalize import (
    CLIP_SAFE_RENDER_PROMPT_MAX_WORDS,
    DEFAULT_RENDER_PROMPT,
    UnifiedDeforumRenderContext,
    build_deforum_render_context,
    limit_prompt_words,
    operational_render_prompt_from_scene,
    prompt_excerpt,
    render_prompt_from_scene,
)
from .deforum_prompt_timeline import resolve_prompt_frame
from .deforum_schedule import coerce_schedule_pairs, evaluate_schedule
from .model_weights import diffusers_weight_load_kwargs

try:
    from PIL import Image, ImageOps
except Exception:  # pragma: no cover
    Image = None  # type: ignore
    ImageOps = None  # type: ignore

from .compositor import apply_timeline_layers
from .ffmpeg import (
    assemble_image_sequence,
    has_video_stream,
    interpolate_video_fps,
    mux_audio,
)
from .internal_video_models import generate_video_model_frames, validate_video_model_layout
from .video_motion_quality import (
    MIN_VIDEO_MODEL_NATIVE_FRAMES,
    MIN_VIDEO_MODEL_OUTPUT_FRAMES,
    analyze_motion_images,
    analyze_motion_paths,
    describe_video_model_frame_budget,
    temporal_blend_frame,
)

logger = logging.getLogger(__name__)

INTERNAL_VIDEO_RENDERER_ALGORITHM_VERSION = "storyboard-continuity-v3"


@dataclass(frozen=True)
class InternalVideoSettings:
    fps_render: int = 2
    fps_output: int = 24
    width: int = 768
    height: int = 432

    steps: int = 15
    cfg: float = 7.0
    sampler: str = "euler"
    seed: int | None = None
    keyframe_interval_s: float = 5.0
    keyframe_continuity_mode: str = "scene"  # scene|project

    interpolation_engine: str = "auto"  # auto|minterpolate|fps|rife
    negative_prompt: str = (
        "blurry, low quality, watermark, text, logo, collage, contact sheet, "
        "split screen, multi-panel composition, comic panels, tiled image, storyboard sheet, mosaic, "
        "duplicate subject, multiple people, extra person, cloned subject"
    )
    model_id: str = "hf_sd15_internal"
    loras: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    vae: str | None = None
    hires_fix: dict[str, Any] | None = None
    refiner: dict[str, Any] | None = None
    upscaler: str | None = None
    render_tier: str = "auto"
    device_preference: str = "auto"

    # Temporal consistency
    temporal_mode: str = "frame_img2img"  # off|keyframes|frame_img2img|video_model
    temporal_strength: float = 0.35
    temporal_steps: int | None = None
    refine_every_n_frames: int = 1
    anchor_strength: float = 0.20
    prompt_blend: bool = True
    resume_existing_frames: bool = True
    motion_strategy: str = "manual"  # manual|storyboard_full_motion
    storyboard_shot_max_s: float = 4.0
    deforum_overrides: dict[str, Any] | None = None
    # Internal video-model adapter. SVD is image-to-video from generated
    # keyframes; AnimateDiff is text-to-video through a Diffusers motion adapter.
    video_model_engine: str = "auto"  # auto|svd|animatediff|hunyuan_video15
    video_model_id: str | None = None
    video_model_path: str | None = None
    video_model_max_frames_per_scene: int = 25
    video_model_motion_bucket_id: int = 127
    video_model_noise_aug_strength: float = 0.02
    video_model_decode_chunk_size: int = 8
    video_model_dtype: str = "auto"
    video_model_cpu_offload: bool = False
    video_model_motion_score_mode: str = "auto"  # auto|manual|off
    video_model_manual_motion_score: int = 4
    video_model_anchor_mode: str = "start"  # start|end|both|loop
    video_model_prompt_refine: bool = True
    video_model_scene_motion: str = "subject"  # camera|subject|scene
    video_model_apply_timeline_camera: bool = True
    video_model_keyframe_renderer: str = "internal"  # internal|tensorrt_sd15
    video_model_keyframe_model_id: str | None = None
    video_model_motion_score_schedule: Any = None
    video_model_noise_aug_schedule: Any = None
    anchor_strength_schedule: Any = None
    # Image animation: an uploaded still used to seed the first keyframe (img2img).
    source_asset: str | None = None
    source_strength: float = 0.55

def normalize_internal_motion_strategy(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"storyboard", "storyboard_full_motion", "full_motion_storyboard", "auto_storyboard"}:
        return "storyboard_full_motion"
    return "manual"


def normalize_keyframe_continuity_mode(value: Any) -> str:
    """Normalize chained keyframe continuity scope with a safe legacy default."""

    return "project" if str(value or "").strip().lower() == "project" else "scene"


def normalize_video_model_keyframe_renderer(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"trt", "tensorrt", "tensorrt_sd15", "sd15_tensorrt", "trt_sd15"}:
        return "tensorrt_sd15"
    return "internal"


def normalize_video_model_scene_motion(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if raw in {"camera", "camera_only", "atmosphere", "ambient"}:
        return "camera"
    if raw in {"scene", "whole_scene", "full_scene", "objects", "object_motion", "living_scene"}:
        return "scene"
    return "subject"


class _PipelineCache:
    _cache: dict[tuple[str, str, str], Any] = {}

    @classmethod
    def get(cls, key: tuple[str, str, str]) -> Any | None:
        return cls._cache.get(key)

    @classmethod
    def set(cls, key: tuple[str, str, str], value: Any) -> None:
        cls._cache[key] = value

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()

    @classmethod
    def drain(cls) -> list[Any]:
        cached = list(cls._cache.values())
        cls._cache.clear()
        return cached


class _EmbedCache:
    _cache: dict[tuple[str, str], Any] = {}

    @classmethod
    def get(cls, key: tuple[str, str]) -> Any | None:
        return cls._cache.get(key)

    @classmethod
    def set(cls, key: tuple[str, str], value: Any) -> None:
        cls._cache[key] = value

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()


class _ControlNetCache:
    _cache: dict[tuple[str, str, str], Any] = {}

    @classmethod
    def get(cls, key: tuple[str, str, str]) -> Any | None:
        return cls._cache.get(key)

    @classmethod
    def set(cls, key: tuple[str, str, str], value: Any) -> None:
        cls._cache[key] = value

    @classmethod
    def clear(cls) -> None:
        cls._cache.clear()


_STILL_PIPELINE_LOCK = threading.Lock()


def _cuda_total_vram_gb(device: str) -> float:
    if str(device or "").lower() != "cuda":
        return 0.0
    try:
        import torch  # type: ignore

        if not (getattr(torch, "cuda", None) and torch.cuda.is_available()):
            return 0.0
        props = torch.cuda.get_device_properties(0)
        return round(float(getattr(props, "total_memory", 0.0)) / float(1024 ** 3), 2)
    except Exception:
        return 0.0


def _cleanup_torch_cuda(device: str) -> None:
    gc.collect()
    if str(device or "").lower() != "cuda":
        return
    try:
        import torch  # type: ignore

        if getattr(torch, "cuda", None) and torch.cuda.is_available():
            torch.cuda.empty_cache()
            if hasattr(torch.cuda, "ipc_collect"):
                torch.cuda.ipc_collect()
    except Exception:
        pass


def _release_still_pipeline_memory(pipes: _Pipes | None, device: str, *, log_fn=None) -> None:
    for pipe in (
        getattr(pipes, "txt2img", None),
        getattr(pipes, "img2img", None),
        getattr(pipes, "inpaint", None),
    ):
        if hasattr(pipe, "to"):
            try:
                pipe.to("cpu")
            except Exception:
                pass
    _PipelineCache.clear()
    _EmbedCache.clear()
    _ControlNetCache.clear()
    _cleanup_torch_cuda(device)
    if log_fn:
        log_fn("Released still-image diffusion pipelines before loading the internal video model.")


def release_cached_internal_pipelines() -> int:
    """Release API-process CUDA preview pipelines before spawning a render worker.

    Diffusion previews run in the API process and intentionally cache their still-image
    pipelines.  A later isolated render process cannot reclaim that VRAM itself, so move
    cached CUDA pipelines back to CPU and discard CUDA-resident embeds/controlnets before
    the child starts.  The return value is the number of CUDA pipeline bundles released.
    """
    cached = _PipelineCache.drain()
    released = 0
    moved: set[int] = set()
    for pipes in cached:
        if str(getattr(pipes, "device", "")).strip().lower() != "cuda":
            continue
        released += 1
        for pipe in (
            getattr(pipes, "txt2img", None),
            getattr(pipes, "img2img", None),
            getattr(pipes, "inpaint", None),
        ):
            if pipe is None or id(pipe) in moved or not hasattr(pipe, "to"):
                continue
            moved.add(id(pipe))
            try:
                pipe.to("cpu")
            except Exception:
                pass
    _EmbedCache.clear()
    _ControlNetCache.clear()
    # Drop this function's final strong references before collecting CUDA/CPU
    # allocations. This matters on 16 GB systems where the isolated worker will
    # immediately load another offloaded video pipeline.
    pipes = None
    pipe = None
    cached.clear()
    moved.clear()
    gc.collect()
    _cleanup_torch_cuda("cuda")
    return released


def _fit_multiple_of_8(width: int, height: int, *, max_width: int, max_height: int) -> tuple[int, int]:
    width_i = max(64, int(width))
    height_i = max(64, int(height))
    scale = min(1.0, float(max_width) / float(width_i), float(max_height) / float(height_i))
    out_w = max(64, int(math.floor((width_i * scale) / 8.0) * 8))
    out_h = max(64, int(math.floor((height_i * scale) / 8.0) * 8))
    return out_w, out_h


def _video_model_adapter_canvas(
    *,
    engine: str,
    width: int,
    height: int,
    device: str,
    cpu_offload: bool,
) -> tuple[int, int, str | None]:
    engine_l = str(engine or "").lower()
    if engine_l not in {"animatediff", "svd", "hunyuan_video15"} or str(device or "").lower() != "cuda":
        return int(width), int(height), None
    vram_gb = _cuda_total_vram_gb(device)
    if vram_gb <= 0.0:
        return int(width), int(height), None
    if vram_gb <= 6.5:
        max_w, max_h = {
            "svd": (576, 320),
            "animatediff": (640, 384),
            # Hunyuan is much larger than the legacy adapters. These bounds
            # are conservative execution targets, not a release-support claim.
            "hunyuan_video15": (512, 288),
        }[engine_l]
        adapter_w, adapter_h = _fit_multiple_of_8(int(width), int(height), max_width=max_w, max_height=max_h)
        if (adapter_w, adapter_h) != (int(width), int(height)):
            label = {
                "svd": "SVD",
                "animatediff": "AnimateDiff",
                "hunyuan_video15": "HunyuanVideo-1.5",
            }[engine_l]
            return adapter_w, adapter_h, f"6 GB CUDA {label} canvas capped to {adapter_w}x{adapter_h}"
    elif vram_gb <= 8.5 and not bool(cpu_offload):
        max_w, max_h = {
            "svd": (640, 360),
            "animatediff": (704, 448),
            "hunyuan_video15": (576, 320),
        }[engine_l]
        adapter_w, adapter_h = _fit_multiple_of_8(int(width), int(height), max_width=max_w, max_height=max_h)
        if (adapter_w, adapter_h) != (int(width), int(height)):
            label = {
                "svd": "SVD",
                "animatediff": "AnimateDiff",
                "hunyuan_video15": "HunyuanVideo-1.5",
            }[engine_l]
            return adapter_w, adapter_h, f"8 GB CUDA {label} canvas capped to {adapter_w}x{adapter_h}"
    return int(width), int(height), None


def _video_model_temporal_step_cap(*, engine: str, device: str) -> int | None:
    """Do not trade denoising quality for VRAM; steps affect time, not peak model allocation."""

    del engine, device
    return None


def _apply_video_model_temporal_step_cap(scheduled_steps: int, step_cap: int | None) -> int:
    steps = max(1, int(scheduled_steps))
    return min(steps, max(1, int(step_cap))) if step_cap is not None else steps


def _stable_seed_int(*parts: Any, fallback: int = 0) -> int:
    raw = "|".join(str(part) for part in parts if part is not None)
    if not raw:
        return int(fallback)
    digest = hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:8]
    return int(digest, 16)



def _json_digest(value: Any) -> str:
    try:
        raw = json.dumps(value, sort_keys=True, ensure_ascii=False, default=str)
    except Exception:
        raw = repr(value)
    return hashlib.sha1(raw.encode("utf-8", errors="ignore")).hexdigest()[:12]


def _timeline_render_fingerprint(timeline: dict[str, Any] | None) -> Any:
    if not isinstance(timeline, dict):
        return None
    cleaned: dict[str, Any] = {}
    for k, v in timeline.items():
        if k in {"trash_layers", "trash_clips", "history", "future", "selection"}:
            continue
        cleaned[k] = v
    return cleaned


def _build_work_tag(
    *,
    variant_index: int,
    variant: dict[str, Any] | None,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    model_dir: Path,
    settings: "InternalVideoSettings",
) -> str:
    render_sig = _render_signature(
        variant_index=variant_index,
        model_dir=model_dir,
        settings=settings,
        variant=variant,
        scenes=scenes,
        timeline=timeline,
    )
    return (
        f"internal_v{int(variant_index):02d}_"
        f"{int(settings.width)}x{int(settings.height)}_{int(settings.fps_render)}rf_{int(settings.fps_output)}of_{render_sig}"
    )


def describe_internal_render_cache(
    *,
    project_dir: Path,
    variant_index: int,
    variant: dict[str, Any] | None,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    model_dir: Path,
    settings: "InternalVideoSettings",
    total_frames: int,
) -> dict[str, Any]:
    work_tag = _build_work_tag(
        variant_index=variant_index,
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=model_dir,
        settings=settings,
    )
    out_frames = project_dir / "outputs" / "frames_internal" / work_tag
    raw_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_raw.mp4"
    interp_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_interp.mp4"
    final_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}.mp4"
    meta_json = project_dir / "outputs" / "videos" / f"{work_tag}.render.json"
    frame_count = 0
    if out_frames.exists():
        try:
            frame_count = len(list(out_frames.glob("frame_*.png")))
        except Exception:
            frame_count = 0
    motion_validation_passed = (
        _cached_motion_validation_passed(meta_json)
        if str(settings.temporal_mode or "").lower() == "video_model"
        else None
    )
    return {
        "work_tag": work_tag,
        "frames_dir": str(out_frames),
        "render_meta_path": str(meta_json),
        "raw_mp4": str(raw_mp4),
        "interp_mp4": str(interp_mp4),
        "final_mp4": str(final_mp4),
        "frames_present": frame_count,
        "frames_expected": int(total_frames),
        "frames_complete": bool(frame_count >= int(total_frames)),
        "raw_exists": raw_mp4.exists(),
        "interp_exists": interp_mp4.exists(),
        "final_exists": final_mp4.exists(),
        "final_reusable": bool(
            final_mp4.exists()
            and (motion_validation_passed is None or motion_validation_passed)
        ),
        "motion_validation_passed": motion_validation_passed,
        "render_meta_exists": meta_json.exists(),
    }


def _render_signature(
    *,
    variant_index: int,
    model_dir: Path,
    settings: "InternalVideoSettings",
    variant: dict[str, Any] | None = None,
    scenes: list[dict[str, Any]] | None = None,
    timeline: dict[str, Any] | None = None,
) -> str:
    payload = {
        "renderer_algorithm_version": INTERNAL_VIDEO_RENDERER_ALGORITHM_VERSION,
        "variant_index": int(variant_index),
        "model_dir": str(model_dir),
        "fps_render": int(settings.fps_render),
        "fps_output": int(settings.fps_output),
        "width": int(settings.width),
        "height": int(settings.height),
        "steps": int(settings.steps),
        "cfg": float(settings.cfg),
        "sampler": str(settings.sampler),
        "seed": settings.seed,
        "keyframe_interval_s": float(settings.keyframe_interval_s),
        "keyframe_continuity_mode": normalize_keyframe_continuity_mode(
            settings.keyframe_continuity_mode
        ),
        "interpolation_engine": str(settings.interpolation_engine),
        "model_id": str(settings.model_id),
        "loras_digest": _json_digest(list(settings.loras)),
        "vae": str(settings.vae or ""),
        "hires_fix": settings.hires_fix or None,
        "refiner": settings.refiner or None,
        "upscaler": str(settings.upscaler or ""),
        "render_tier": str(settings.render_tier),
        "device_preference": str(settings.device_preference),
        "temporal_mode": str(settings.temporal_mode),
        "temporal_strength": float(settings.temporal_strength),
        "temporal_steps": int(settings.temporal_steps or 0),
        "refine_every_n_frames": int(settings.refine_every_n_frames),
        "anchor_strength": float(settings.anchor_strength),
        "prompt_blend": bool(settings.prompt_blend),
        "motion_strategy": normalize_internal_motion_strategy(settings.motion_strategy),
        "storyboard_shot_max_s": float(_storyboard_shot_max_s(settings)),
        "video_model_engine": str(settings.video_model_engine),
        "video_model_id": str(settings.video_model_id or ""),
        "video_model_path": str(settings.video_model_path or ""),
        "video_model_max_frames_per_scene": int(settings.video_model_max_frames_per_scene),
        "video_model_motion_bucket_id": int(settings.video_model_motion_bucket_id),
        "video_model_noise_aug_strength": float(settings.video_model_noise_aug_strength),
        "video_model_decode_chunk_size": int(settings.video_model_decode_chunk_size),
        "video_model_dtype": str(settings.video_model_dtype),
        "video_model_cpu_offload": bool(settings.video_model_cpu_offload),
        "video_model_motion_score_mode": str(settings.video_model_motion_score_mode),
        "video_model_manual_motion_score": int(settings.video_model_manual_motion_score),
        "video_model_anchor_mode": str(settings.video_model_anchor_mode),
        "video_model_prompt_refine": bool(settings.video_model_prompt_refine),
        "video_model_scene_motion": normalize_video_model_scene_motion(settings.video_model_scene_motion),
        "video_model_apply_timeline_camera": bool(settings.video_model_apply_timeline_camera),
        "video_model_keyframe_renderer": normalize_video_model_keyframe_renderer(settings.video_model_keyframe_renderer),
        "video_model_keyframe_model_id": str(settings.video_model_keyframe_model_id or ""),
        "video_model_motion_score_schedule": settings.video_model_motion_score_schedule,
        "video_model_noise_aug_schedule": settings.video_model_noise_aug_schedule,
        "anchor_strength_schedule": settings.anchor_strength_schedule,
        "source_asset": str(settings.source_asset or ""),
        "source_strength": float(settings.source_strength),
        "deforum_overrides": settings.deforum_overrides or None,
        "variant_motion_digest": _json_digest((variant or {}).get("motion_schedules") if isinstance(variant, dict) else None),
        "variant_prompt_digest": _json_digest((variant or {}).get("prompts") if isinstance(variant, dict) else None),
        "scenes_digest": _json_digest(scenes or []),
        "timeline_digest": _json_digest(_timeline_render_fingerprint(timeline)),
    }
    raw = repr(sorted(payload.items())).encode("utf-8", errors="ignore")
    return hashlib.sha1(raw).hexdigest()[:10]


def _frame_path(frames_dir: Path, fi: int) -> Path:
    return frames_dir / f"frame_{fi:06d}.png"


def _require_pillow() -> None:
    if Image is None:
        raise UserFacingError(
            "Pillow is not installed",
            hint="Install backend deps including Pillow, then retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        )


def _media_output_is_reusable(ffmpeg_path: str, media_path: Path) -> bool:
    if not media_path.exists():
        return False
    try:
        if media_path.stat().st_size <= 0:
            return False
    except OSError:
        return False
    stream_status = has_video_stream(ffmpeg_path, media_path)
    return stream_status is not False


def _cached_motion_validation_passed(meta_json: Path) -> bool:
    try:
        payload = json.loads(meta_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return False
    validation = payload.get("motion_validation") if isinstance(payload, dict) else None
    if not isinstance(validation, dict) or validation.get("status") != "pass":
        return False
    output_sequence = validation.get("output_sequence")
    native_scenes = validation.get("native_scenes")
    try:
        expected_native_scene_count = int(
            validation.get("expected_native_scene_count")
        )
    except (TypeError, ValueError):
        return False
    native_scene_keys = {
        (item.get("scene_index"), item.get("shot_index"))
        for item in native_scenes
        if isinstance(item, dict)
    } if isinstance(native_scenes, list) else set()
    return (
        isinstance(output_sequence, dict)
        and output_sequence.get("status") == "pass"
        and isinstance(native_scenes, list)
        and expected_native_scene_count > 0
        and len(native_scenes) == expected_native_scene_count
        and len(native_scene_keys) == expected_native_scene_count
        and all(isinstance(item, dict) and item.get("status") == "pass" for item in native_scenes)
    )


def _cached_native_motion_report(
    meta_json: Path,
    *,
    scene_index: int,
    shot_index: int,
) -> dict[str, Any] | None:
    """Return passing evidence only for the exact cached storyboard shot."""

    try:
        payload = json.loads(meta_json.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return None
    validation = payload.get("motion_validation") if isinstance(payload, dict) else None
    if not isinstance(validation, dict) or validation.get("status") != "pass":
        return None
    output_sequence = validation.get("output_sequence")
    if not isinstance(output_sequence, dict) or output_sequence.get("status") != "pass":
        return None
    native_scenes = validation.get("native_scenes")
    if not isinstance(native_scenes, list):
        return None
    for item in native_scenes:
        if not isinstance(item, dict) or item.get("status") != "pass":
            continue
        try:
            cached_scene_index = int(item.get("scene_index"))
            cached_shot_index = int(item.get("shot_index"))
        except (TypeError, ValueError):
            continue
        if cached_scene_index == int(scene_index) and cached_shot_index == int(shot_index):
            return dict(item)
    return None


@dataclass
class _Pipes:
    txt2img: Any
    img2img: Any
    device: str
    inpaint: Any | None = None
    family: str = "sd15"
    backend: str = "diffusers"


def _model_family_from_dir(model_dir: Path) -> str:
    family = "unknown"
    mi = model_dir / "model_index.json"
    if mi.exists():
        try:
            j = json.loads(mi.read_text(encoding="utf-8"))
            cls = str(j.get("_class_name") or "")
            if "Flux" in cls:
                family = "flux"
            elif "StableDiffusion3" in cls:
                family = "sd3"
            elif ("XL" in cls) or ("XLPipeline" in cls):
                family = "sdxl"
            elif "StableDiffusion" in cls:
                family = "sd15"
        except Exception:
            family = "unknown"
    if family == "unknown":
        path_hint = str(model_dir.name or "").strip().lower()
        if "flux" in path_hint:
            return "flux"
        if "sd35" in path_hint or "stable-diffusion-3" in path_hint:
            return "sd3"
        if "sdxl" in path_hint:
            return "sdxl"
        if "sd15" in path_hint or "stable-diffusion-v1" in path_hint:
            return "sd15"
    return family


def _diffusers_from_pretrained_kwargs(*, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Prefer safetensors weights; bucket/local snapshots rarely ship legacy .bin files."""
    kwargs: dict[str, Any] = {"use_safetensors": True}
    if extra:
        kwargs.update(extra)
    return kwargs


def _diffusers_model_load_kwargs(model_dir: Path, device: str, *, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    kwargs = _diffusers_from_pretrained_kwargs(extra=extra)
    kwargs.update(diffusers_weight_load_kwargs(model_dir, device))
    return kwargs


def _reraise_snapshot_load_error(exc: Exception, model_dir: Path) -> None:
    message = str(exc).lower()
    if "git-lfs" in message or "git lfs" in message:
        raise UserFacingError(
            "Internal diffusion model snapshot contains Git LFS pointer files",
            hint=(
                f"The Diffusers snapshot at {model_dir} has placeholder weight files instead of full model weights. "
                "Reinstall the model in Models or run git lfs pull/re-sync for that snapshot, then retry."
            ),
            code="MODEL_SNAPSHOT_LFS_POINTER",
            status_code=400,
        ) from exc
    if any(
        token in message
        for token in ("no file named", "does not appear to have", "safetensors", "not found in directory")
    ):
        raise UserFacingError(
            "Internal diffusion model failed to load",
            hint=(
                f"The Diffusers snapshot at {model_dir} is incomplete or missing weight files. "
                "Reinstall the model in Models or re-sync from the Hugging Face bucket, then retry."
            ),
            code="MODEL_SNAPSHOT_LOAD_FAILED",
            status_code=400,
        ) from exc
    raise exc


_DIFFUSERS_PIPELINE_CLASS_NAMES: dict[str, tuple[str, ...]] = {
    "sd15": (
        "StableDiffusionPipeline",
        "StableDiffusionImg2ImgPipeline",
        "StableDiffusionInpaintPipeline",
    ),
    "sdxl": (
        "StableDiffusionXLPipeline",
        "StableDiffusionXLImg2ImgPipeline",
        "StableDiffusionXLInpaintPipeline",
    ),
    "sd3": (
        "StableDiffusion3Pipeline",
        "StableDiffusion3Img2ImgPipeline",
        "StableDiffusion3InpaintPipeline",
    ),
    "flux": ("FluxPipeline",),
}


def _installed_distribution_version(distribution: str) -> str:
    try:
        return str(importlib.metadata.version(distribution))
    except Exception:
        return "unknown"


def _import_failure_detail(exc: BaseException, *, limit: int = 360) -> str:
    message = " ".join(str(exc).split())
    detail = f"{type(exc).__name__}: {message}" if message else type(exc).__name__
    if len(detail) > limit:
        return f"{detail[: limit - 3]}..."
    return detail


def _load_diffusers_runtime(family: str) -> tuple[Any, Any, Any | None, Any | None]:
    """Load only the Diffusers classes required by the selected model family.

    Diffusers exposes pipeline classes through lazy module attributes. Importing
    every supported family in one statement makes an SD 1.5 render depend on
    optional SDXL and SD3 imports too. Keep the runtime boundary family-local so
    an unrelated optional pipeline cannot disable an otherwise valid model.
    """
    normalized_family = str(family or "").strip().lower()
    if normalized_family not in _DIFFUSERS_PIPELINE_CLASS_NAMES:
        raise UserFacingError(
            "Internal diffusion model family is unsupported",
            hint=(
                f"Studio could not identify the Diffusers pipeline family '{normalized_family or 'unknown'}'. "
                "Reinstall a supported SD 1.5, SDXL, SD3.5, or FLUX snapshot instead of falling back to an unrelated model."
            ),
            code="INTERNAL_MODEL_FAMILY_UNSUPPORTED",
            status_code=400,
        )
    family_label = {"sd15": "SD 1.5", "sdxl": "SDXL", "sd3": "SD3", "flux": "FLUX"}[normalized_family]

    try:
        torch = importlib.import_module("torch")
    except Exception as exc:
        logger.exception("Internal diffusion dependency import failed: torch")
        raise UserFacingError(
            "Internal diffusion runtime could not load Torch",
            hint=(
                f"Torch {_installed_distribution_version('torch')} failed to import: "
                f"{_import_failure_detail(exc)}. Reinstall the selected Studio backend runtime, then retry."
            ),
            code="INTERNAL_DEPS",
            status_code=500,
        ) from exc

    try:
        diffusers = importlib.import_module("diffusers")
    except Exception as exc:
        logger.exception("Internal diffusion dependency import failed: diffusers")
        raise UserFacingError(
            "Internal diffusion runtime could not load Diffusers",
            hint=(
                f"Diffusers {_installed_distribution_version('diffusers')} failed to import: "
                f"{_import_failure_detail(exc)}. Reinstall the selected Studio backend runtime, then retry."
            ),
            code="INTERNAL_DEPS",
            status_code=500,
        ) from exc

    pipeline_classes: list[Any] = []
    for class_name in _DIFFUSERS_PIPELINE_CLASS_NAMES[normalized_family]:
        try:
            pipeline_classes.append(getattr(diffusers, class_name))
        except Exception as exc:
            logger.exception(
                "Internal diffusion pipeline import failed for %s (%s)",
                family_label,
                class_name,
            )
            raise UserFacingError(
                f"Internal {family_label} pipeline is unavailable",
                hint=(
                    f"Diffusers {_installed_distribution_version('diffusers')} could not load "
                    f"{class_name}: {_import_failure_detail(exc)}. Reinstall or repair the selected "
                    "Studio backend runtime, then retry this model."
                ),
                code="INTERNAL_DEPS",
                status_code=500,
            ) from exc

    padded = [*pipeline_classes, None, None]
    return torch, padded[0], padded[1], padded[2]


def _try_load_diffusers(model_dir: Path, device: str, *, role: str = "video") -> _Pipes:
    cache_key = (str(model_dir), device, str(role or "video"))
    cached = _PipelineCache.get(cache_key)
    if cached is not None:
        return cached

    # TF32 / cuDNN benchmark flags are set at app startup by _apply_cuda_startup_flags()
    # in app.py, so we don't need to re-apply them here on every pipeline load.
    family = _model_family_from_dir(model_dir)
    torch, txt_pipeline_class, img_pipeline_class, inpaint_pipeline_class = _load_diffusers_runtime(family)

    torch_dtype = torch.float16 if device in ("cuda", "rocm") else torch.float32

    if family == "flux":
        if device == "directml":
            raise UserFacingError(
                "FLUX is not available through DirectML",
                hint="Use CUDA with offload, use CPU, or select SDXL/SD 1.5 for DirectML.",
                code="DIRECTML_MODEL_UNSUPPORTED",
                status_code=400,
            )
        flux_dtype = torch.float16
        if hasattr(torch, "bfloat16") and device in {"cuda", "cpu"}:
            flux_dtype = torch.bfloat16
        txt = txt_pipeline_class.from_pretrained(
            str(model_dir),
            **_diffusers_model_load_kwargs(
                model_dir,
                device,
                extra={"torch_dtype": flux_dtype, "low_cpu_mem_usage": True},
            ),
        )
        backend = "diffusers"
        if device == "cuda":
            try:
                vram_gb = float(torch.cuda.get_device_properties(0).total_memory) / (1024 ** 3)
            except Exception:
                vram_gb = 0.0
            if vram_gb < 16.0:
                if not hasattr(txt, "enable_sequential_cpu_offload"):
                    raise UserFacingError(
                        "FLUX cannot fit this GPU without sequential CPU offload",
                        hint="Repair the Studio internal-video environment so Diffusers and Accelerate provide sequential CPU offload.",
                        code="FLUX_OFFLOAD_UNAVAILABLE",
                        status_code=400,
                    )
                txt.enable_sequential_cpu_offload()
                backend = "diffusers_sequential_offload"
            else:
                txt = txt.to(device)
        elif device == "mps":
            txt = txt.to(device)
        pipes = _Pipes(txt2img=txt, img2img=None, inpaint=None, device=device, family="flux", backend=backend)
    elif family == "sd3":
        txt = txt_pipeline_class.from_pretrained(
            str(model_dir),
            **_diffusers_model_load_kwargs(model_dir, device, extra={"torch_dtype": torch_dtype}),
        )
        if hasattr(txt, "enable_attention_slicing"):
            txt.enable_attention_slicing()
        if device == "cuda" and hasattr(txt, "enable_xformers_memory_efficient_attention"):
            try:
                txt.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        txt = txt.to(device)

        img = img_pipeline_class(**txt.components)
        if hasattr(img, "enable_attention_slicing"):
            img.enable_attention_slicing()
        if device == "cuda" and hasattr(img, "enable_xformers_memory_efficient_attention"):
            try:
                img.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        img = img.to(device)

        inpaint = inpaint_pipeline_class(**txt.components)
        if hasattr(inpaint, "enable_attention_slicing"):
            inpaint.enable_attention_slicing()
        if device == "cuda" and hasattr(inpaint, "enable_xformers_memory_efficient_attention"):
            try:
                inpaint.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        inpaint = inpaint.to(device)

        pipes = _Pipes(txt2img=txt, img2img=img, inpaint=inpaint, device=device, family="sd3", backend="diffusers")
    elif family == "sdxl":
        txt = txt_pipeline_class.from_pretrained(
            str(model_dir),
            **_diffusers_model_load_kwargs(
                model_dir,
                device,
                extra={
                    "torch_dtype": torch_dtype,
                    "safety_checker": None,
                    "requires_safety_checker": False,
                }
            ),
        )
        if hasattr(txt, "enable_attention_slicing"):
            txt.enable_attention_slicing()
        if device == "cuda" and hasattr(txt, "enable_xformers_memory_efficient_attention"):
            try:
                txt.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        txt = txt.to(device)

        img = img_pipeline_class(**txt.components)
        if hasattr(img, "enable_attention_slicing"):
            img.enable_attention_slicing()
        if device == "cuda" and hasattr(img, "enable_xformers_memory_efficient_attention"):
            try:
                img.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        img = img.to(device)

        inpaint = inpaint_pipeline_class(**txt.components)
        if hasattr(inpaint, "enable_attention_slicing"):
            inpaint.enable_attention_slicing()
        if device == "cuda" and hasattr(inpaint, "enable_xformers_memory_efficient_attention"):
            try:
                inpaint.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        inpaint = inpaint.to(device)

        pipes = _Pipes(txt2img=txt, img2img=img, inpaint=inpaint, device=device, family="sdxl", backend="diffusers")
    else:
        txt = txt_pipeline_class.from_pretrained(
            str(model_dir),
            **_diffusers_model_load_kwargs(
                model_dir,
                device,
                extra={
                    "torch_dtype": torch_dtype,
                    "safety_checker": None,
                    "requires_safety_checker": False,
                }
            ),
        )
        if hasattr(txt, "enable_attention_slicing"):
            txt.enable_attention_slicing()
        if device == "cuda" and hasattr(txt, "enable_xformers_memory_efficient_attention"):
            try:
                txt.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        txt = txt.to(device)

        img = img_pipeline_class(**txt.components)
        if hasattr(img, "enable_attention_slicing"):
            img.enable_attention_slicing()
        if device == "cuda" and hasattr(img, "enable_xformers_memory_efficient_attention"):
            try:
                img.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        img = img.to(device)

        inpaint = inpaint_pipeline_class(**txt.components)
        if hasattr(inpaint, "enable_attention_slicing"):
            inpaint.enable_attention_slicing()
        if device == "cuda" and hasattr(inpaint, "enable_xformers_memory_efficient_attention"):
            try:
                inpaint.enable_xformers_memory_efficient_attention()
            except Exception:
                pass
        inpaint = inpaint.to(device)

        pipes = _Pipes(txt2img=txt, img2img=img, inpaint=inpaint, device=device, family="sd15", backend="diffusers")

    _PipelineCache.set(cache_key, pipes)
    return pipes


def _try_load_directml(model_dir: Path, *, role: str = "video") -> _Pipes:
    family = _model_family_from_dir(model_dir)
    if family not in {"sd15", "sdxl"}:
        raise UserFacingError(
            "DirectML acceleration currently supports SD 1.5 and SDXL only.",
            hint="Use SDXL or SD 1.5 for AMD / DirectML renders, or switch device preference to CPU for SD3.5.",
            code="DIRECTML_MODEL_UNSUPPORTED",
            status_code=400,
        )

    try:
        import onnxruntime as ort  # type: ignore
        from optimum.onnxruntime import (  # type: ignore
            ORTStableDiffusionImg2ImgPipeline,
            ORTStableDiffusionPipeline,
            ORTStableDiffusionXLImg2ImgPipeline,
            ORTStableDiffusionXLPipeline,
        )
    except Exception as e:
        raise UserFacingError(
            "DirectML runtime is not installed.",
            hint="Open Setup and install the AMD / DirectML backend runtime, then retry.",
            code="DIRECTML_DEPS",
            status_code=500,
        ) from e

    providers = list(ort.get_available_providers() or [])
    if "DmlExecutionProvider" not in providers:
        raise UserFacingError(
            "DirectML execution provider is unavailable in this backend environment.",
            hint="Reinstall the AMD / DirectML backend runtime from Setup, then retry.",
            code="DIRECTML_UNAVAILABLE",
            status_code=500,
        )

    cache_key = (str(model_dir), "directml", str(role or "video"))
    cached = _PipelineCache.get(cache_key)
    if cached is not None:
        return cached

    common_kwargs = {
        "export": True,
        "provider": "DmlExecutionProvider",
    }
    if family == "sdxl":
        txt = ORTStableDiffusionXLPipeline.from_pretrained(str(model_dir), **common_kwargs)
        img = ORTStableDiffusionXLImg2ImgPipeline.from_pretrained(str(model_dir), **common_kwargs)
        pipes = _Pipes(txt2img=txt, img2img=img, inpaint=None, device="directml", family="sdxl", backend="directml")
    else:
        txt = ORTStableDiffusionPipeline.from_pretrained(str(model_dir), **common_kwargs)
        img = ORTStableDiffusionImg2ImgPipeline.from_pretrained(str(model_dir), **common_kwargs)
        pipes = _Pipes(txt2img=txt, img2img=img, inpaint=None, device="directml", family="sd15", backend="directml")

    _PipelineCache.set(cache_key, pipes)
    return pipes


def _try_load_pipelines(model_dir: Path, device: str, *, role: str = "video") -> _Pipes:
    try:
        if device == "directml":
            return _try_load_directml(model_dir, role=role)
        return _try_load_diffusers(model_dir, device=device, role=role)
    except UserFacingError:
        raise
    except Exception as exc:
        _reraise_snapshot_load_error(exc, model_dir)


def _device_auto(preference: str = "auto") -> str:
    pref = str(preference or "auto").strip().lower()
    try:
        import torch  # type: ignore
    except Exception:
        torch = None  # type: ignore

    def _cuda_ok() -> bool:
        try:
            return bool(torch is not None and getattr(torch, "cuda", None) is not None and torch.cuda.is_available())
        except Exception:
            return False

    def _mps_ok() -> bool:
        try:
            backends = getattr(torch, "backends", None)
            mps = getattr(backends, "mps", None)
            return bool(mps is not None and mps.is_available())
        except Exception:
            return False

    def _directml_ok() -> bool:
        if pref != "directml" and pref != "auto" and pref not in {"cuda", "mps", "cpu"}:
            return False
        try:
            import onnxruntime as ort  # type: ignore

            return "DmlExecutionProvider" in list(ort.get_available_providers() or [])
        except Exception:
            return False

    if pref == "cuda" and _cuda_ok():
        return "cuda"
    if pref == "mps" and _mps_ok():
        return "mps"
    if pref == "directml" and _directml_ok():
        return "directml"
    if pref == "cpu":
        return "cpu"
    if _cuda_ok():
        return "cuda"
    if _mps_ok():
        return "mps"
    if _directml_ok():
        return "directml"
    return "cpu"


def _encode_prompt(pipes: _Pipes, prompt: str) -> Any:
    """Return an encoded prompt representation.

    SD1.5 path: returns text-encoder embeddings (fast + blendable).
    SDXL / SD3 path: returns the prompt string (we rely on pipeline internal encoding).
    """
    prompt = str(prompt or "").strip() or "cinematic"
    if pipes.family != "sd15" or pipes.backend == "directml":
        # Keep it simple & robust for SDXL: use native pipeline encoding.
        return prompt

    import torch  # type: ignore

    key = (pipes.device, prompt)
    cached = _EmbedCache.get(key)
    if cached is not None:
        return cached

    tokenizer = pipes.txt2img.tokenizer
    text_encoder = pipes.txt2img.text_encoder

    inputs = tokenizer(
        prompt,
        padding="max_length",
        max_length=tokenizer.model_max_length,
        truncation=True,
        return_tensors="pt",
    )
    ids = inputs.input_ids.to(pipes.device)
    with torch.no_grad():
        embeds = text_encoder(ids)[0]
    _EmbedCache.set(key, embeds)
    return embeds


def _blend_embeds(a: Any, b: Any, w: float) -> Any:
    import torch  # type: ignore

    w = float(max(0.0, min(1.0, w)))
    if isinstance(a, str) or isinstance(b, str):
        # SDXL path: we can't blend embeddings safely here; pick a side deterministically.
        return str(b) if w >= 0.5 else str(a)
    return a * (1.0 - w) + b * w


def _ken_burns_frame(
    img: "Image.Image",
    out_w: int,
    out_h: int,
    zoom: float,
    pan_x: float,
    pan_y: float,
    rotation_deg: float = 0.0,
) -> "Image.Image":
    w, h = img.size

    if abs(rotation_deg) > 0.01:
        _fill = (0, 0, 0, 0) if img.mode == "RGBA" else None
        img = img.rotate(float(rotation_deg), resample=Image.BICUBIC, expand=True, fillcolor=_fill)
        w, h = img.size

    # A centered 1.0x crop has no pixels available for lateral travel. Add only
    # the overscan needed by the requested pan so custom pan remains visible
    # without forcing every caller to author a matching zoom schedule.
    effective_zoom = float(zoom)
    if abs(float(pan_x)) > 1e-4:
        min_zoom_x = (float(out_w) + 2.0 * abs(float(pan_x))) / max(1.0, float(w))
        effective_zoom = max(effective_zoom, min_zoom_x)
    if abs(float(pan_y)) > 1e-4:
        min_zoom_y = (float(out_h) + 2.0 * abs(float(pan_y))) / max(1.0, float(h))
        effective_zoom = max(effective_zoom, min_zoom_y)

    zw, zh = int(round(w * effective_zoom)), int(round(h * effective_zoom))
    imz = img.resize((max(1, zw), max(1, zh)), resample=Image.BICUBIC)

    cx, cy = imz.width // 2, imz.height // 2
    x0 = int(round(cx - out_w / 2 + pan_x))
    y0 = int(round(cy - out_h / 2 + pan_y))
    x0 = max(0, min(x0, imz.width - out_w))
    y0 = max(0, min(y0, imz.height - out_h))
    return imz.crop((x0, y0, x0 + out_w, y0 + out_h))


def _perspective_coeffs(
    dst_pts: list[tuple[float, float]],
    src_pts: list[tuple[float, float]],
) -> tuple[float, ...]:
    """Solve the 8 perspective coefficients for ``Image.transform(PERSPECTIVE)``.

    ``dst_pts`` are output-image corner positions, ``src_pts`` the matching
    source-image corners. PIL maps each output point back into the source using
    the returned coefficients.
    """
    import numpy as np  # type: ignore

    matrix = []
    for (dx, dy), (sx, sy) in zip(dst_pts, src_pts, strict=False):
        matrix.append([dx, dy, 1.0, 0.0, 0.0, 0.0, -sx * dx, -sx * dy])
        matrix.append([0.0, 0.0, 0.0, dx, dy, 1.0, -sy * dx, -sy * dy])
    a = np.array(matrix, dtype=np.float64)
    b = np.array([coord for point in src_pts for coord in point], dtype=np.float64)
    solution = np.linalg.solve(a, b)
    return tuple(float(v) for v in solution)


def _project_image_corners(
    w: int,
    h: int,
    *,
    rot_x_deg: float,
    rot_y_deg: float,
    rot_z_deg: float,
    translation_x: float,
    translation_y: float,
    translation_z: float,
    fov_deg: float,
) -> list[tuple[float, float]]:
    """Project the image-plane corners through a simple pinhole camera.

    Returns the four destination corner positions (top-left, top-right,
    bottom-right, bottom-left) after applying 3D rotations (pitch/yaw/roll),
    translation, and a dolly along Z. With neutral parameters the corners map
    back to the original rectangle, so the transform reduces to identity.
    """
    fov = max(10.0, min(179.0, float(fov_deg or 70.0)))
    focal = (0.5 * float(w)) / math.tan(math.radians(fov) / 2.0)
    half_w, half_h = float(w) / 2.0, float(h) / 2.0
    corners = [
        (-half_w, -half_h, 0.0),
        (half_w, -half_h, 0.0),
        (half_w, half_h, 0.0),
        (-half_w, half_h, 0.0),
    ]

    rx, ry, rz = (math.radians(rot_x_deg), math.radians(rot_y_deg), math.radians(rot_z_deg))
    cos_x, sin_x = math.cos(rx), math.sin(rx)
    cos_y, sin_y = math.cos(ry), math.sin(ry)
    cos_z, sin_z = math.cos(rz), math.sin(rz)

    def _rotate(x: float, y: float, z: float) -> tuple[float, float, float]:
        # pitch (X axis)
        y1 = y * cos_x - z * sin_x
        z1 = y * sin_x + z * cos_x
        x1 = x
        # yaw (Y axis)
        x2 = x1 * cos_y + z1 * sin_y
        z2 = -x1 * sin_y + z1 * cos_y
        y2 = y1
        # roll (Z axis)
        x3 = x2 * cos_z - y2 * sin_z
        y3 = x2 * sin_z + y2 * cos_z
        return x3, y3, z2

    distance = focal
    min_depth = 0.1 * focal
    out: list[tuple[float, float]] = []
    for cx, cy, cz in corners:
        rxp, ryp, rzp = _rotate(cx, cy, cz)
        xc = rxp + float(translation_x)
        yc = ryp + float(translation_y)
        zc = distance - float(translation_z) + rzp
        if zc < min_depth:
            zc = min_depth
        u = focal * xc / zc + half_w
        v = focal * yc / zc + half_h
        out.append((u, v))
    return out


def _apply_camera_3d(
    img: "Image.Image",
    out_w: int,
    out_h: int,
    *,
    zoom: float = 1.0,
    pan_x: float = 0.0,
    pan_y: float = 0.0,
    rotation_deg: float = 0.0,
    translation_z: float = 0.0,
    rotation_3d_x: float = 0.0,
    rotation_3d_y: float = 0.0,
    fov_deg: float = 70.0,
) -> "Image.Image":
    """Apply full (2D + 3D) camera motion to a frame.

    3D pitch/yaw/dolly are applied first as a perspective warp, then the
    existing 2D zoom/pan/roll crop runs on top. When no 3D component is active
    this is bit-identical to :func:`_ken_burns_frame`, preserving the legacy
    2D-only behavior.
    """
    has_3d = (
        abs(float(translation_z)) > 1e-4
        or abs(float(rotation_3d_x)) > 1e-4
        or abs(float(rotation_3d_y)) > 1e-4
    )
    if not has_3d:
        return _ken_burns_frame(
            img, out_w, out_h, zoom=zoom, pan_x=pan_x, pan_y=pan_y, rotation_deg=rotation_deg
        )

    w, h = img.size
    dst = _project_image_corners(
        w,
        h,
        rot_x_deg=float(rotation_3d_x),
        rot_y_deg=float(rotation_3d_y),
        rot_z_deg=0.0,
        translation_x=0.0,
        translation_y=0.0,
        translation_z=float(translation_z),
        fov_deg=float(fov_deg),
    )
    src = [(0.0, 0.0), (float(w), 0.0), (float(w), float(h)), (0.0, float(h))]
    try:
        coeffs = _perspective_coeffs(dst, src)
        _fill = (0, 0, 0, 0) if img.mode == "RGBA" else None
        warped = img.transform(
            (w, h), Image.PERSPECTIVE, coeffs, resample=Image.BICUBIC, fillcolor=_fill
        )
    except Exception:
        warped = img
    return _ken_burns_frame(
        warped, out_w, out_h, zoom=zoom, pan_x=pan_x, pan_y=pan_y, rotation_deg=rotation_deg
    )



def _generate_txt2img(
    pipes: _Pipes,
    prompt_embeds: Any,
    negative_embeds: Any,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
) -> "Image.Image":
    g = None
    if pipes.device != "directml":
        import torch  # type: ignore

        g = torch.Generator(device=pipes.device if pipes.device != "mps" else "cpu")
        g.manual_seed(int(seed))

    if pipes.family == "flux":
        prompt = str(prompt_embeds or "").strip() or "cinematic"
        flux_steps = max(1, min(4, int(steps)))
        kwargs = {
            "prompt": prompt,
            "width": int(width),
            "height": int(height),
            "num_inference_steps": flux_steps,
            "guidance_scale": 0.0,
            "max_sequence_length": 256,
        }
        if g is not None:
            kwargs["generator"] = g
        out = pipes.txt2img(**kwargs)
        return out.images[0]

    if pipes.family != "sd15" or pipes.backend == "directml" or isinstance(prompt_embeds, str):
        prompt = str(prompt_embeds or "").strip() or "cinematic"
        negative = str(negative_embeds or "").strip()
        kwargs = dict(
            prompt=prompt,
            negative_prompt=negative,
            width=int(width),
            height=int(height),
            num_inference_steps=int(steps),
            guidance_scale=float(cfg),
        )
        if g is not None:
            kwargs["generator"] = g
        out = pipes.txt2img(**kwargs)
        return out.images[0]

    kwargs = dict(
        prompt=None,
        width=int(width),
        height=int(height),
        num_inference_steps=int(steps),
        guidance_scale=float(cfg),
        prompt_embeds=prompt_embeds,
        negative_prompt_embeds=negative_embeds,
    )
    if g is not None:
        kwargs["generator"] = g
    out = pipes.txt2img(**kwargs)
    return out.images[0]


def _generate_img2img(
    pipes: _Pipes,
    init_image: "Image.Image",
    prompt_embeds: Any,
    negative_embeds: Any,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    strength: float,
) -> "Image.Image":
    g = None
    if pipes.device != "directml":
        import torch  # type: ignore

        g = torch.Generator(device=pipes.device if pipes.device != "mps" else "cpu")
        g.manual_seed(int(seed))

    if pipes.family != "sd15" or pipes.backend == "directml" or isinstance(prompt_embeds, str):
        prompt = str(prompt_embeds or "").strip() or "cinematic"
        negative = str(negative_embeds or "").strip()
        kwargs = dict(
            prompt=prompt,
            negative_prompt=negative,
            image=init_image,
            strength=float(max(0.0, min(1.0, strength))),
            width=int(width),
            height=int(height),
            num_inference_steps=int(steps),
            guidance_scale=float(cfg),
        )
        if g is not None:
            kwargs["generator"] = g
        out = pipes.img2img(**kwargs)
        return out.images[0]

    kwargs = dict(
        prompt=None,
        image=init_image,
        strength=float(max(0.0, min(1.0, strength))),
        width=int(width),
        height=int(height),
        num_inference_steps=int(steps),
        guidance_scale=float(cfg),
        prompt_embeds=prompt_embeds,
        negative_prompt_embeds=negative_embeds,
    )
    if g is not None:
        kwargs["generator"] = g
    out = pipes.img2img(**kwargs)
    return out.images[0]


def _generate_tensorrt_sd15_keyframe(
    *,
    project_id: str | None,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler: str,
    seed: int,
    model_id: str | None,
    model_path: Path,
) -> "Image.Image":
    if not project_id:
        raise UserFacingError(
            "TensorRT keyframe anchors need a project id",
            hint="Use the Studio render endpoint so TensorRT anchors can write into the project runtime folder.",
            code="TRT_ANCHOR_PROJECT_MISSING",
            status_code=400,
        )
    if ImageOps is None:
        raise UserFacingError(
            "Pillow image operations are unavailable",
            hint="Install Pillow in the backend environment and retry.",
            code="PILLOW_UNAVAILABLE",
            status_code=500,
        )

    from . import tensorrt_standalone

    resolved_model_path = Path(model_path).expanduser().resolve()
    if not resolved_model_path.is_dir():
        raise UserFacingError(
            "The TensorRT storyboard-anchor bundle is unavailable",
            hint="Open Models and verify the canonical SD 1.5 TensorRT bundle, then retry.",
            code="TRT_ANCHOR_BUNDLE_NOT_INSTALLED",
            status_code=400,
        )

    result = tensorrt_standalone.run_job(
        project_id,
        None,
        {
            "model_id": str(model_id or "local_sd15_tensorrt_bundle"),
            "model_path": str(resolved_model_path),
            "workflow_family": "sd15",
            "prompt": str(prompt or "cinematic music video keyframe"),
            "negative_prompt": str(negative_prompt or "blurry, low quality, watermark, text, logo"),
            "steps": max(1, min(80, int(steps))),
            "cfg": float(cfg),
            "sampler": str(sampler or "pndm"),
            "seed": int(seed) & 0xFFFFFFFF,
            "batch_size": 1,
        },
    )
    src = Path(str(result.get("output_path") or ""))
    if not src.exists():
        raise RuntimeError("TensorRT SD1.5 keyframe render did not produce an image")
    image = Image.open(src).convert("RGB")
    if image.size != (int(width), int(height)):
        image = ImageOps.fit(image, (int(width), int(height)), method=Image.LANCZOS)
    return image


def _generate_inpaint(
    pipes: _Pipes,
    init_image: "Image.Image",
    mask_image: "Image.Image",
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    seed: int,
    strength: float,
) -> "Image.Image":
    if pipes.inpaint is None:
        raise UserFacingError(
            "Internal inpaint pipeline is unavailable for this model/backend.",
            hint="Use a supported internal diffusers model, or switch the still model to a Comfy checkpoint.",
            code="INTERNAL_INPAINT_UNAVAILABLE",
            status_code=400,
        )

    g = None
    if pipes.device != "directml":
        import torch  # type: ignore

        g = torch.Generator(device=pipes.device if pipes.device != "mps" else "cpu")
        g.manual_seed(int(seed))

    kwargs = dict(
        prompt=str(prompt or "").strip() or "cinematic",
        negative_prompt=str(negative_prompt or "").strip(),
        image=init_image,
        mask_image=mask_image,
        strength=float(max(0.0, min(1.0, strength))),
        width=int(width),
        height=int(height),
        num_inference_steps=int(steps),
        guidance_scale=float(cfg),
    )
    if g is not None:
        kwargs["generator"] = g
    out = pipes.inpaint(**kwargs)
    return out.images[0]


def _load_controlnet_model(model_dir: Path, family: str, device: str) -> Any:
    if family not in {"sd15", "sdxl"}:
        raise UserFacingError(
            "Internal ControlNet is only available for SD 1.5 and SDXL in this phase.",
            hint="Use an SD 1.5 or SDXL internal still model for ControlNet, or switch to a Comfy checkpoint.",
            code="INTERNAL_CONTROLNET_UNSUPPORTED",
            status_code=400,
        )

    cache_key = (str(model_dir), family, device)
    cached = _ControlNetCache.get(cache_key)
    if cached is not None:
        return cached

    try:
        import torch  # type: ignore
        from diffusers import ControlNetModel  # type: ignore
    except Exception as e:
        raise UserFacingError(
            "Internal ControlNet runtime is not installed.",
            hint="Install the internal diffusers runtime and retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        ) from e

    torch_dtype = torch.float16 if device in ("cuda", "rocm") else torch.float32
    controlnet = ControlNetModel.from_pretrained(
        str(model_dir),
        **_diffusers_model_load_kwargs(model_dir, device, extra={"torch_dtype": torch_dtype}),
    )
    if device != "directml":
        controlnet = controlnet.to(device)
    _ControlNetCache.set(cache_key, controlnet)
    return controlnet


def _build_controlnet_pipeline(
    pipes: _Pipes,
    *,
    controlnet_dirs: list[Path],
) -> Any:
    if pipes.family not in {"sd15", "sdxl"}:
        raise UserFacingError(
            "Internal ControlNet is only available for SD 1.5 and SDXL in this phase.",
            hint="Use an SD 1.5 or SDXL internal still model for ControlNet, or switch to a Comfy checkpoint.",
            code="INTERNAL_CONTROLNET_UNSUPPORTED",
            status_code=400,
        )

    try:
        from diffusers import (  # type: ignore
            MultiControlNetModel,
            StableDiffusionControlNetPipeline,
            StableDiffusionXLControlNetPipeline,
        )
    except Exception as e:
        raise UserFacingError(
            "Internal ControlNet runtime is not installed.",
            hint="Install the internal diffusers runtime and retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        ) from e

    models = [_load_controlnet_model(model_dir, pipes.family, pipes.device) for model_dir in controlnet_dirs]
    if not models:
        raise UserFacingError(
            "No internal ControlNet models were provided.",
            hint="Choose one or more compatible internal ControlNet units before retrying.",
            code="INTERNAL_CONTROLNET_MISSING",
            status_code=400,
        )
    controlnet = models[0] if len(models) == 1 else MultiControlNetModel(models)
    base_components = dict(getattr(pipes.txt2img, "components", {}) or {})
    base_components.pop("controlnet", None)
    if pipes.family == "sdxl":
        pipeline = StableDiffusionXLControlNetPipeline(controlnet=controlnet, **base_components)
    else:
        pipeline = StableDiffusionControlNetPipeline(controlnet=controlnet, **base_components)
    if hasattr(pipeline, "enable_attention_slicing"):
        pipeline.enable_attention_slicing()
    if pipes.device == "cuda" and hasattr(pipeline, "enable_xformers_memory_efficient_attention"):
        try:
            pipeline.enable_xformers_memory_efficient_attention()
        except Exception:
            pass
    if pipes.device != "directml":
        pipeline = pipeline.to(pipes.device)
    return pipeline


def _apply_loras(pipeline: Any, loras: tuple[dict[str, Any], ...]) -> list[str]:
    loaded: list[str] = []
    weights: list[float] = []
    if not loras or not hasattr(pipeline, "load_lora_weights"):
        return loaded
    for idx, item in enumerate(loras):
        lora_path = str(item.get("path") or item.get("filename") or item.get("name") or "").strip()
        if not lora_path:
            continue
        adapter_name = f"edmg_lora_{idx}"
        pipeline.load_lora_weights(lora_path, adapter_name=adapter_name)
        loaded.append(adapter_name)
        weights.append(float(item.get("weight", 1.0)))
    if loaded and hasattr(pipeline, "set_adapters"):
        try:
            pipeline.set_adapters(loaded, adapter_weights=weights)
        except TypeError:
            pipeline.set_adapters(loaded, weights)
    return loaded


def _clear_loras(pipeline: Any, adapter_names: list[str]) -> None:
    if not adapter_names:
        return
    try:
        if hasattr(pipeline, "delete_adapters"):
            pipeline.delete_adapters(adapter_names)
        elif hasattr(pipeline, "unload_lora_weights"):
            pipeline.unload_lora_weights()
    except Exception:
        pass


def _load_render_image(path: Path, *, mode: str, size: tuple[int, int] | None = None) -> "Image.Image":
    _require_pillow()
    with Image.open(path) as image:
        result = image.convert(mode)
        if size is not None and result.size != size:
            resample = Image.BICUBIC if mode != "L" else Image.BILINEAR
            result = result.resize(size, resample=resample)
        return result


def _fit_render_image(image: "Image.Image", *, size: tuple[int, int], mode: str) -> "Image.Image":
    if image.size == size:
        return image.copy()
    target_w, target_h = size
    resample = Image.BICUBIC if mode != "L" else Image.BILINEAR
    scale = max(target_w / max(1, image.width), target_h / max(1, image.height))
    resized = image.resize(
        (max(1, int(round(image.width * scale))), max(1, int(round(image.height * scale)))),
        resample=resample,
    )
    left = max(0, int(round((resized.width - target_w) / 2)))
    top = max(0, int(round((resized.height - target_h) / 2)))
    return resized.crop((left, top, left + target_w, top + target_h))


def _load_render_source_image(path: Path, *, size: tuple[int, int]) -> "Image.Image":
    _require_pillow()
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return _fit_render_image(rgb, size=size, mode="RGB")


def _pil_upscale_resample(upscaler: str | None) -> int:
    raw = str(upscaler or "").strip().lower()
    if raw.startswith("latent_"):
        raw = raw[len("latent_") :]
    elif raw.startswith("pixel_"):
        raw = raw[len("pixel_") :]
    mapping = {
        "nearest": Image.NEAREST,
        "nearest-exact": Image.NEAREST,
        "nearest_exact": Image.NEAREST,
        "bilinear": Image.BILINEAR,
        "area": Image.BOX,
        "bicubic": Image.BICUBIC,
        "bislerp": Image.BICUBIC,
        "lanczos": Image.LANCZOS,
    }
    return mapping.get(raw, Image.LANCZOS)


def _upscale_render_image(image: "Image.Image", *, scale: float, upscaler: str | None) -> "Image.Image":
    normalized_scale = float(max(1.0, scale))
    target_size = (
        max(1, int(round(image.width * normalized_scale))),
        max(1, int(round(image.height * normalized_scale))),
    )
    if target_size == image.size:
        return image.copy()
    return image.resize(target_size, resample=_pil_upscale_resample(upscaler))


def _apply_hires_fix(
    pipes: _Pipes,
    image: "Image.Image",
    *,
    prompt_embeds: Any,
    negative_embeds: Any,
    settings: InternalVideoSettings,
    seed: int,
) -> "Image.Image":
    hires_cfg = settings.hires_fix if isinstance(settings.hires_fix, dict) and settings.hires_fix.get("enabled", True) else None
    if not hires_cfg:
        return image
    scale = float(hires_cfg.get("scale") or 1.0)
    if scale <= 1.0:
        return image
    upscaled = _upscale_render_image(
        image,
        scale=scale,
        upscaler=str(hires_cfg.get("upscaler") or settings.upscaler or ""),
    )
    return _generate_img2img(
        pipes,
        upscaled,
        prompt_embeds,
        negative_embeds,
        upscaled.width,
        upscaled.height,
        int(hires_cfg.get("steps") or settings.steps),
        float(settings.cfg),
        int(seed) + 1,
        float(max(0.0, min(1.0, hires_cfg.get("denoise", 0.35)))),
    )


def _apply_refiner(
    base_pipes: _Pipes,
    image: "Image.Image",
    *,
    prompt: str,
    negative_prompt: str,
    settings: InternalVideoSettings,
    seed: int,
    device: str,
    log_fn=None,
) -> "Image.Image":
    refiner_cfg = settings.refiner if isinstance(settings.refiner, dict) else None
    if not refiner_cfg:
        return image

    refiner_pipes = base_pipes
    refiner_model = str(refiner_cfg.get("model") or "").strip()
    refiner_path_raw = str(refiner_cfg.get("path") or "").strip()
    if refiner_model and not refiner_path_raw:
        raise UserFacingError(
            "Internal refiner model is not installed",
            hint="Install or select a compatible internal refiner model before enabling the refiner pass.",
            code="INTERNAL_REFINER_MISSING",
            status_code=400,
        )

    if refiner_path_raw:
        refiner_dir = Path(refiner_path_raw)
        if not refiner_dir.exists():
            raise UserFacingError(
                "Internal refiner model path does not exist",
                hint="Reinstall the selected internal refiner model, then retry.",
                code="INTERNAL_REFINER_MISSING",
                status_code=400,
            )
        base_path_raw = str(refiner_cfg.get("base_path") or "").strip()
        should_load_dedicated_refiner = True
        if base_path_raw:
            should_load_dedicated_refiner = refiner_dir.resolve() != Path(base_path_raw).resolve()
        if should_load_dedicated_refiner:
            refiner_pipes = _try_load_pipelines(refiner_dir, device=device, role="still")
            if callable(log_fn):
                log_fn(f"Using dedicated refiner model: {refiner_model or refiner_dir.name}")

    prompt_embeds = _encode_prompt(refiner_pipes, prompt)
    negative_embeds = _encode_prompt(refiner_pipes, negative_prompt) if negative_prompt else ""
    switch_at = float(refiner_cfg.get("switch_at", 0.8))
    switch_at = max(0.0, min(1.0, switch_at))
    refiner_steps = int(refiner_cfg.get("steps") or max(6, round(int(settings.steps) * max(0.2, 1.0 - switch_at))))
    return _generate_img2img(
        refiner_pipes,
        image,
        prompt_embeds,
        negative_embeds,
        image.width,
        image.height,
        refiner_steps,
        float(settings.cfg),
        int(seed) + 2,
        float(max(0.05, min(1.0, 1.0 - switch_at))),
    )


def render_internal_still_image(
    *,
    model_dir: Path,
    settings: InternalVideoSettings,
    workflow_family: str,
    prompt: str,
    source_image_path: Path | None = None,
    mask_image_path: Path | None = None,
    controlnet_units: list[dict[str, Any]] | None = None,
    denoise_strength: float = 0.75,
    log_fn=None,
) -> dict[str, Any]:
    family = _model_family_from_dir(model_dir)
    if family == "unknown":
        raise UserFacingError(
            "Internal diffusion model family is unsupported",
            hint="The snapshot is not a recognized SD 1.5, SDXL, SD3.5, or FLUX Diffusers model.",
            code="INTERNAL_MODEL_FAMILY_UNSUPPORTED",
            status_code=400,
        )
    if family == "flux":
        if workflow_family != "txt2img":
            raise UserFacingError(
                "FLUX.1 Schnell currently supports text-to-image only",
                hint="Switch the Studio still workflow to text-to-image. FLUX img2img, inpaint, and ControlNet require separate native adapters.",
                code="WORKFLOW_UNSUPPORTED",
                status_code=400,
            )
        if settings.loras or settings.hires_fix or settings.refiner:
            raise UserFacingError(
                "This FLUX render includes unsupported refinement options",
                hint="Disable LoRAs, hires fix, and the refiner for the phase-one native FLUX.1 Schnell path.",
                code="FLUX_REFINEMENT_UNSUPPORTED",
                status_code=400,
            )
    requested_device = _device_auto(settings.device_preference)
    device = requested_device
    if requested_device == "directml" and (
        workflow_family in {"inpaint", "outpaint", "controlnet"} or bool(settings.loras) or family == "sd3"
    ):
        device = "cpu"
    pipes = _try_load_pipelines(model_dir, device=device, role="still")
    width = int(settings.width)
    height = int(settings.height)
    negative_prompt = str(settings.negative_prompt or "").strip()
    seed = int(settings.seed if settings.seed is not None else _stable_seed_int(prompt, width, height, workflow_family, fallback=1337))
    prompt_embeds = _encode_prompt(pipes, prompt)
    negative_embeds = _encode_prompt(pipes, negative_prompt) if negative_prompt else ""

    def _log(message: str) -> None:
        if callable(log_fn):
            log_fn(message)

    with _STILL_PIPELINE_LOCK:
        pipeline = None
        adapter_targets: list[tuple[Any, list[str]]] = []

        def _apply_pipeline_loras(target: Any) -> None:
            if target is None:
                return
            if any(existing is target for existing, _ in adapter_targets):
                return
            adapter_targets.append((target, _apply_loras(target, settings.loras)))

        try:
            if workflow_family == "controlnet":
                units = list(controlnet_units or [])
                controlnet_dirs = [Path(str(unit.get("path") or "")) for unit in units if str(unit.get("path") or "").strip()]
                pipeline = _build_controlnet_pipeline(pipes, controlnet_dirs=controlnet_dirs)
            elif workflow_family in {"inpaint", "outpaint"}:
                pipeline = pipes.inpaint
            elif workflow_family == "img2img":
                pipeline = pipes.img2img
            else:
                pipeline = pipes.txt2img
            for candidate in (pipes.txt2img, pipes.img2img, pipes.inpaint, pipeline):
                _apply_pipeline_loras(candidate)

            if workflow_family == "img2img":
                if source_image_path is None:
                    raise UserFacingError(
                        "No source image selected for img2img",
                        hint="Choose a project source image before running img2img.",
                        code="IMG2IMG_SOURCE_MISSING",
                        status_code=400,
                    )
                init_image = _load_render_source_image(source_image_path, size=(width, height))
                image = _generate_img2img(
                    pipes,
                    init_image,
                    prompt_embeds,
                    negative_embeds,
                    width,
                    height,
                    int(settings.steps),
                    float(settings.cfg),
                    seed,
                    float(max(0.0, min(1.0, denoise_strength))),
                )
            elif workflow_family in {"inpaint", "outpaint"}:
                if source_image_path is None or mask_image_path is None:
                    raise UserFacingError(
                        "Source image or mask is missing for inpaint/outpaint",
                        hint="Choose both a source image and a mask before running the render.",
                        code="INPAINT_ASSETS_MISSING",
                        status_code=400,
                    )
                init_image = _load_render_source_image(source_image_path, size=(width, height))
                mask_image = _load_render_image(mask_image_path, mode="L", size=(width, height))
                image = _generate_inpaint(
                    pipes,
                    init_image,
                    mask_image,
                    prompt=str(prompt or ""),
                    negative_prompt=negative_prompt,
                    width=width,
                    height=height,
                    steps=int(settings.steps),
                    cfg=float(settings.cfg),
                    seed=seed,
                    strength=float(max(0.0, min(1.0, denoise_strength))),
                )
            elif workflow_family == "controlnet":
                units = list(controlnet_units or [])
                if not units:
                    raise UserFacingError(
                        "No compatible ControlNet units were provided.",
                        hint="Attach one or more ControlNet units before running the render.",
                        code="CONTROLNET_MISSING",
                        status_code=400,
                    )
                control_images = [
                    _load_render_image(Path(str(unit.get("reference_path") or unit.get("path_reference") or unit.get("reference_image_path") or "")), mode="RGB", size=(width, height))
                    for unit in units
                ]
                scales = [float(unit.get("strength", 0.8)) for unit in units]
                starts = [float(unit.get("start_percent", 0.0)) for unit in units]
                ends = [float(unit.get("end_percent", 1.0)) for unit in units]
                g = None
                if device != "directml":
                    import torch  # type: ignore

                    g = torch.Generator(device=device if device != "mps" else "cpu")
                    g.manual_seed(seed)
                kwargs = {
                    "prompt": str(prompt or "").strip() or "cinematic",
                    "negative_prompt": negative_prompt,
                    "image": control_images[0] if len(control_images) == 1 else control_images,
                    "width": width,
                    "height": height,
                    "num_inference_steps": int(settings.steps),
                    "guidance_scale": float(settings.cfg),
                    "controlnet_conditioning_scale": scales[0] if len(scales) == 1 else scales,
                    "control_guidance_start": starts[0] if len(starts) == 1 else starts,
                    "control_guidance_end": ends[0] if len(ends) == 1 else ends,
                }
                if g is not None:
                    kwargs["generator"] = g
                image = pipeline(**kwargs).images[0]
            else:
                image = _generate_txt2img(
                    pipes,
                    prompt_embeds,
                    negative_embeds,
                    width,
                    height,
                    int(settings.steps),
                    float(settings.cfg),
                    seed,
                )
            image = _apply_hires_fix(
                pipes,
                image,
                prompt_embeds=prompt_embeds,
                negative_embeds=negative_embeds,
                settings=settings,
                seed=seed,
            )
            image = _apply_refiner(
                pipes,
                image,
                prompt=str(prompt or ""),
                negative_prompt=negative_prompt,
                settings=settings,
                seed=seed,
                device=device,
                log_fn=log_fn,
            )
            if device != requested_device:
                _log(f"Internal still render fell back from {requested_device} to {device} for {workflow_family}.")
            return {
                "image": image,
                "device": device,
                "requested_device": requested_device,
                "family": pipes.family,
                "backend": pipes.backend,
                "seed": seed,
                "effective_steps": max(1, min(4, int(settings.steps))) if pipes.family == "flux" else int(settings.steps),
                "effective_cfg": 0.0 if pipes.family == "flux" else float(settings.cfg),
            }
        finally:
            for target, adapters in adapter_targets:
                _clear_loras(target, adapters)


def _scene_keyframe_times(scenes: list[dict[str, Any]], interval_s: float) -> list[float]:
    times: list[float] = []
    for sc in scenes:
        start = float(sc.get("start_s", 0.0))
        end = float(sc.get("end_s", start + 5.0))
        t = start
        while t < end - 1e-6:
            times.append(t)
            t += max(0.5, float(interval_s))
    if not times:
        times = [0.0]
    times = sorted(set([round(x, 3) for x in times]))
    return times


def _infer_duration(scenes: list[dict[str, Any]]) -> float:
    if not scenes:
        return 60.0
    return float(scenes[-1].get("end_s", 60.0))


def _prompt_at_time(scenes: list[dict[str, Any]], t: float, timeline: Any | None = None) -> str:
    """Return prompt text at time t.

    Priority:
      1) DAW timeline prompt track (if present): timeline.tracks[*].type=="prompt"
      2) legacy timeline.prompt_regions (if present)
      3) plan scenes
    """
    if timeline:
        # New DAW tracks schema
        tracks = timeline.get("tracks") if isinstance(timeline, dict) else None
        if isinstance(tracks, list):
            for tr in tracks:
                if not isinstance(tr, dict):
                    continue
                if str(tr.get("type") or "").lower() != "prompt":
                    continue
                clips = tr.get("clips")
                if not isinstance(clips, list):
                    continue
                for cl in clips:
                    if not isinstance(cl, dict):
                        continue
                    s = float(cl.get("start_s", 0.0))
                    e = float(cl.get("end_s", s + 5.0))
                    if s <= t < e:
                        data = cl.get("data") or {}
                        p = str((data.get("prompt") if isinstance(data, dict) else "") or "").strip()
                        if p:
                            return p

        # Back-compat: prompt_regions
        regs = timeline.get("prompt_regions") if isinstance(timeline, dict) else None
        if isinstance(regs, list):
            for r in regs:
                if not isinstance(r, dict):
                    continue
                s = float(r.get("start_s", 0.0))
                e = float(r.get("end_s", s + 5.0))
                if s <= t < e:
                    p = str(r.get("prompt") or "").strip()
                    if p:
                        return p

    for sc in scenes:
        s = float(sc.get("start_s", 0.0))
        e = float(sc.get("end_s", s + 5.0))
        if s <= t < e:
            return render_prompt_from_scene(sc, fallback="")
    return render_prompt_from_scene(scenes[0], fallback="") if scenes else DEFAULT_RENDER_PROMPT



def _key_times_bracket(key_times: list[float], t: float) -> tuple[float, float, float]:
    if not key_times:
        return 0.0, 0.0, 0.0
    if t <= key_times[0]:
        return key_times[0], key_times[0], 0.0
    if t >= key_times[-1]:
        return key_times[-1], key_times[-1], 0.0
    a = key_times[0]
    b = key_times[-1]
    for i in range(len(key_times) - 1):
        if key_times[i] <= t <= key_times[i + 1]:
            a, b = key_times[i], key_times[i + 1]
            break
    u = (t - a) / max(1e-9, (b - a))
    w = _ease01(u)
    return a, b, w


def _ease01(u: float) -> float:
    u = max(0.0, min(1.0, u))
    return u * u * (3.0 - 2.0 * u)




def _parse_deforum_schedule(s: str) -> list[tuple[int, float]]:
    """Back-compat wrapper around the shared schedule parser."""
    return coerce_schedule_pairs(s)


def _eval_schedule(pairs: list[tuple[int, float]], frame: int) -> float | None:
    return evaluate_schedule(pairs, frame, default=None)


def _scheduled_numeric(
    schedule: Any,
    frame: int,
    *,
    default: float,
    lo: float | None = None,
    hi: float | None = None,
) -> float:
    value = evaluate_schedule(schedule, frame, default=float(default))
    try:
        out = float(value if value is not None else default)
    except Exception:
        out = float(default)
    if lo is not None:
        out = max(float(lo), out)
    if hi is not None:
        out = min(float(hi), out)
    return out


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, float(v)))


def _build_unified_deforum_context(
    *,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    variant: dict[str, Any] | None,
    settings: InternalVideoSettings,
    fps: int,
) -> UnifiedDeforumRenderContext:
    return build_deforum_render_context(
        scenes=scenes,
        timeline=timeline,
        variant=variant,
        fps=max(1, int(fps)),
        default_negative_prompt=str(settings.negative_prompt or ""),
        overrides=settings.deforum_overrides,
    )


def _prompt_text_for_frame(
    *,
    frame_idx: int,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    deforum_context: UnifiedDeforumRenderContext,
    fps: int,
) -> str:
    prompt = resolve_prompt_frame(deforum_context.prompts, frame_idx, default="")
    if str(prompt or "").strip():
        return str(prompt).strip()
    return _prompt_at_time(scenes, float(frame_idx) / float(max(1, fps)), timeline=timeline) or DEFAULT_RENDER_PROMPT


def _negative_prompt_for_frame(
    *,
    frame_idx: int,
    settings: InternalVideoSettings,
    deforum_context: UnifiedDeforumRenderContext,
) -> str:
    prompt = resolve_prompt_frame(deforum_context.negative_prompts, frame_idx, default=str(settings.negative_prompt or ""))
    resolved = str(prompt or settings.negative_prompt or "").strip()
    layout_terms = (
        "collage",
        "contact sheet",
        "split screen",
        "multi-panel composition",
        "comic panels",
        "tiled image",
        "storyboard sheet",
        "mosaic",
        "duplicate subject",
        "multiple people",
        "extra person",
        "cloned subject",
    )
    lowered = resolved.lower()
    missing = [term for term in layout_terms if term not in lowered]
    if missing:
        resolved = ", ".join(part for part in (resolved, *missing) if part)
    return resolved


def _keyframe_continuity_source(
    previous_image: Any | None,
    *,
    previous_scene_index: int | None,
    scene_index: int,
    keyframe_continuity_mode: str = "scene",
) -> Any | None:
    """Select the previous image according to the requested continuity scope."""

    if (
        normalize_keyframe_continuity_mode(keyframe_continuity_mode) == "scene"
        and previous_scene_index is not None
        and int(scene_index) != int(previous_scene_index)
    ):
        return None
    return previous_image


def _use_direct_video_model_source_anchor(
    *,
    keyframe_index: int,
    source_image_path: Path | None,
    temporal_mode: str,
) -> bool:
    """Keep the selected source exact for the first video-model anchor."""

    return (
        int(keyframe_index) == 0
        and source_image_path is not None
        and str(temporal_mode or "").strip().lower() == "video_model"
    )


def _motion_params_at_time(
    t: float,
    timeline: dict[str, Any] | None,
    *,
    deforum_motion: DeforumMotionScheduleBundle | None = None,
    fps: int = 24,
) -> dict[str, float] | None:
    frame = int(round(float(t) * float(max(1, fps))))
    motion = deforum_motion
    if motion is None:
        motion = build_deforum_render_context(
            scenes=[],
            timeline=timeline,
            variant=None,
            fps=max(1, int(fps)),
            default_negative_prompt="",
        ).motion
    if not motion.has_camera_motion() and not motion.has_diffusion_controls():
        return None

    state = evaluate_motion_state(frame, motion)
    out = state.to_renderer_params()
    if "steps" not in out and "strength" in out:
        out["steps"] = _clamp(15.0 * (0.70 + 0.90 * float(out["strength"])), 6.0, 40.0)
    if "denoise" not in out and "strength" in out:
        out["denoise"] = _clamp(float(out["strength"]), 0.01, 0.99)
    return out


_CAMERA_KEYFRAME_FIELDS: tuple[tuple[str, float], ...] = (
    ("zoom", 1.0),
    ("pan_x", 0.0),
    ("pan_y", 0.0),
    ("rotation_deg", 0.0),
    ("translation_z", 0.0),
    ("rotation_3d_x", 0.0),
    ("rotation_3d_y", 0.0),
    ("rotation_3d_z", 0.0),
    ("fov", 70.0),
)


@dataclass(frozen=True)
class _CameraComponents:
    """Full camera pose at a point in time (2D Ken-Burns + 3D Deforum motion)."""

    zoom: float = 1.0
    pan_x: float = 0.0
    pan_y: float = 0.0
    rotation_deg: float = 0.0
    translation_z: float = 0.0
    rotation_3d_x: float = 0.0
    rotation_3d_y: float = 0.0
    rotation_3d_z: float = 0.0
    fov: float = 70.0

    @property
    def roll_deg(self) -> float:
        return float(self.rotation_deg) + float(self.rotation_3d_z)


def _camera_keyframes_are_actionable(points: list[dict[str, Any]]) -> bool:
    if len(points) >= 2:
        return True
    if len(points) != 1:
        return False
    point = points[0]
    return any(
        abs(float(point.get(key, default)) - float(default)) > 1e-4
        for key, default in _CAMERA_KEYFRAME_FIELDS
    )


def _camera_keyframe_components(point: dict[str, Any]) -> _CameraComponents:
    values = {key: float(point.get(key, default)) for key, default in _CAMERA_KEYFRAME_FIELDS}
    return _CameraComponents(**values)


def _normalize_camera_keyframes(
    points: list[dict[str, Any]],
    *,
    coalesce_within_s: float = 0.25,
) -> list[dict[str, Any]]:
    """Sort camera points and remove accidental sub-frame pose collisions.

    Generated camera lanes sometimes put an old scene endpoint and a new scene
    start only a few milliseconds apart. At the 2 FPS diffusion cadence those
    points occupy the same frame and become a visible warp. Prefer the later
    point unless either key explicitly marks an editorial cut/hold.
    """

    ordered = sorted(
        (dict(point) for point in points if isinstance(point, dict) and "t" in point),
        key=lambda point: float(point.get("t", 0.0)),
    )
    normalized: list[dict[str, Any]] = []
    protected_easing = {"cut", "hold"}
    for point in ordered:
        if not normalized:
            normalized.append(point)
            continue
        previous = normalized[-1]
        gap_s = float(point.get("t", 0.0)) - float(previous.get("t", 0.0))
        previous_easing = str(previous.get("easing") or "").strip().lower()
        point_easing = str(point.get("easing") or "").strip().lower()
        if (
            gap_s >= 0.0
            and gap_s < max(0.0, float(coalesce_within_s))
            and previous_easing not in protected_easing
            and point_easing not in protected_easing
        ):
            # Keep the earlier timestamp so the new authored pose is reached
            # before the next render sample rather than one sample afterward.
            point["t"] = float(previous.get("t", 0.0))
            normalized[-1] = point
        else:
            normalized.append(point)
    return normalized


def _lerp_camera_components(a: _CameraComponents, b: _CameraComponents, w: float) -> _CameraComponents:
    iw = 1.0 - w
    return _CameraComponents(
        zoom=a.zoom * iw + b.zoom * w,
        pan_x=a.pan_x * iw + b.pan_x * w,
        pan_y=a.pan_y * iw + b.pan_y * w,
        rotation_deg=a.rotation_deg * iw + b.rotation_deg * w,
        translation_z=a.translation_z * iw + b.translation_z * w,
        rotation_3d_x=a.rotation_3d_x * iw + b.rotation_3d_x * w,
        rotation_3d_y=a.rotation_3d_y * iw + b.rotation_3d_y * w,
        rotation_3d_z=a.rotation_3d_z * iw + b.rotation_3d_z * w,
        fov=a.fov * iw + b.fov * w,
    )


def _camera_components_at_time(
    t: float,
    *,
    timeline: dict[str, Any] | None,
    fallback_interval_s: float,
    deforum_motion: DeforumMotionScheduleBundle | None = None,
    fps: int = 24,
) -> _CameraComponents:
    """Full camera evaluator (2D + 3D).

    Timeline format (optional):
      timeline["camera"]["keyframes"] = [{"t":0,"zoom":1.0,"pan_x":0,"pan_y":0,
        "rotation_deg":0,"translation_z":0,"rotation_3d_x":0,"rotation_3d_y":0,
        "rotation_3d_z":0,"fov":70}, ...]

    Resolution order: timeline camera keyframes -> Deforum motion schedules
    (variant/timeline/overrides) -> deterministic 2D fallback.
    """
    if timeline and isinstance(timeline, dict):
        cam = timeline.get("camera")
        if isinstance(cam, dict):
            kfs = cam.get("keyframes")
            if isinstance(kfs, list):
                pts = _normalize_camera_keyframes(
                    [x for x in kfs if isinstance(x, dict) and "t" in x]
                )
                if _camera_keyframes_are_actionable(pts):
                    if t <= float(pts[0]["t"]):
                        return _camera_keyframe_components(pts[0])
                    if t >= float(pts[-1]["t"]):
                        return _camera_keyframe_components(pts[-1])

                    a, b = pts[0], pts[-1]
                    for i in range(len(pts) - 1):
                        ta, tb = float(pts[i]["t"]), float(pts[i + 1]["t"])
                        if ta <= t <= tb:
                            a, b = pts[i], pts[i + 1]
                            break
                    ta, tb = float(a["t"]), float(b["t"])
                    u = max(0.0, min(1.0, (t - ta) / max(1e-9, (tb - ta))))
                    easing = str(a.get("easing") or "through").strip().lower()
                    if easing == "cut":
                        w = 1.0 if u >= 1.0 - 1e-9 else 0.0
                    elif easing == "hold":
                        w = 0.0
                    elif easing in {"ease", "smooth", "smoothstep", "settle"}:
                        w = _ease01(u)
                    else:
                        # Interior/generated samples are control points on one
                        # camera move, not places where the dolly should stop.
                        w = u
                    return _lerp_camera_components(
                        _camera_keyframe_components(a), _camera_keyframe_components(b), w
                    )

    # If camera keyframes are missing, fall back to motion track clips (DAW).
    mp = _motion_params_at_time(t, timeline, deforum_motion=deforum_motion, fps=fps)
    if mp:
        return _CameraComponents(
            zoom=float(mp.get("zoom", 1.0)),
            pan_x=float(mp.get("pan_x", 0.0)),
            pan_y=float(mp.get("pan_y", 0.0)),
            rotation_deg=float(mp.get("rotation_deg", 0.0)),
            translation_z=float(mp.get("translation_z", 0.0)),
            rotation_3d_x=float(mp.get("rotation_3d_x", 0.0)),
            rotation_3d_y=float(mp.get("rotation_3d_y", 0.0)),
            rotation_3d_z=float(mp.get("rotation_3d_z", 0.0)),
            fov=float(mp.get("fov", 70.0)),
        )

    # fallback deterministic motion (2D Ken-Burns drift)
    phase = (t / max(0.001, fallback_interval_s))
    zoom = 1.0 + 0.06 * _ease01((t % fallback_interval_s) / max(0.001, fallback_interval_s))
    pan_x = 8.0 * math.sin(2.0 * math.pi * phase)
    pan_y = 5.0 * math.sin(2.0 * math.pi * phase + 1.2)
    return _CameraComponents(zoom=zoom, pan_x=pan_x, pan_y=pan_y)


def _camera_at_time(
    t: float,
    *,
    timeline: dict[str, Any] | None,
    fallback_interval_s: float,
    deforum_motion: DeforumMotionScheduleBundle | None = None,
    fps: int = 24,
) -> tuple[float, float, float, float]:
    """Backward-compatible 2D camera evaluator (zoom, pan_x, pan_y, rotation_deg)."""
    c = _camera_components_at_time(
        t,
        timeline=timeline,
        fallback_interval_s=fallback_interval_s,
        deforum_motion=deforum_motion,
        fps=fps,
    )
    return c.zoom, c.pan_x, c.pan_y, c.rotation_deg


def _apply_camera_components_absolute(
    img: "Image.Image", out_w: int, out_h: int, comp: _CameraComponents
) -> "Image.Image":
    """Apply an absolute camera pose to a (static) source frame."""
    return _apply_camera_3d(
        img,
        out_w,
        out_h,
        zoom=comp.zoom,
        pan_x=comp.pan_x,
        pan_y=comp.pan_y,
        rotation_deg=comp.roll_deg,
        translation_z=comp.translation_z,
        rotation_3d_x=comp.rotation_3d_x,
        rotation_3d_y=comp.rotation_3d_y,
        fov_deg=comp.fov,
    )


def _apply_camera_components_delta(
    prev_frame: "Image.Image",
    out_w: int,
    out_h: int,
    comp: _CameraComponents,
    prev: _CameraComponents,
) -> "Image.Image":
    """Warp the previous frame by the per-frame camera delta (img2img path)."""
    rz = comp.zoom / max(1e-6, prev.zoom)
    return _apply_camera_3d(
        prev_frame,
        out_w,
        out_h,
        zoom=rz,
        pan_x=comp.pan_x - prev.pan_x,
        pan_y=comp.pan_y - prev.pan_y,
        rotation_deg=comp.roll_deg - prev.roll_deg,
        translation_z=comp.translation_z - prev.translation_z,
        rotation_3d_x=comp.rotation_3d_x - prev.rotation_3d_x,
        rotation_3d_y=comp.rotation_3d_y - prev.rotation_3d_y,
        fov_deg=comp.fov,
    )


def _write_runtime_checkpoint(checkpoint_json: Path, state: dict[str, Any]) -> None:
    checkpoint_json.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_json.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _build_checkpoint_emitter(
    *,
    checkpoint_json: Path,
    project_dir: Path,
    work_tag: str,
    render_mode: str,
    variant_index: int,
    total_frames: int,
    fps_render: int,
    chunk_plan: dict[str, Any] | None,
    checkpoint_fn=None,
):
    plan = dict(chunk_plan or {})
    frames_per_chunk = max(1, int(plan.get("frames_per_chunk") or total_frames or 1))
    checkpoint_interval_frames = max(1, int(plan.get("checkpoint_interval_frames") or max(1, fps_render * 15)))
    estimated_chunks = max(1, int(plan.get("estimated_chunks") or math.ceil(max(1, total_frames) / max(1, frames_per_chunk))))
    strategy = str(plan.get("strategy") or ("resume_friendly_chunks" if total_frames > frames_per_chunk else "single_pass"))
    enabled = bool(plan.get("enabled", total_frames > frames_per_chunk))
    notes = list(plan.get("notes") or [])
    state: dict[str, Any] = {
        "status": "pending",
        "render_mode": str(render_mode),
        "work_tag": str(work_tag),
        "variant_index": int(variant_index),
        "total_frames": int(total_frames),
        "fps_render": int(fps_render),
        "frames_rendered": 0,
        "frames_reused": 0,
        "completed_frames": 0,
        "last_completed_frame": -1,
        "next_frame_index": 0,
        "frames_per_chunk": int(frames_per_chunk),
        "estimated_chunks": int(estimated_chunks),
        "completed_chunks": 0,
        "current_chunk_index": 1 if total_frames > 0 else 0,
        "current_chunk_progress_frames": 0,
        "checkpoint_interval_frames": int(checkpoint_interval_frames),
        "resume_recommended": bool(plan.get("resume_recommended", enabled)),
        "chunking_enabled": enabled,
        "chunk_strategy": strategy,
        "notes": notes,
        "can_resume": True,
        "outputs": {
            "checkpoint_json": str(checkpoint_json.relative_to(project_dir)),
            "raw_exists": False,
            "interp_exists": False,
            "final_exists": False,
        },
    }
    last_emitted = {"stage": None, "completed_frames": -1, "ts": 0.0}

    def _emit(
        *,
        stage: str,
        status: str = "running",
        force: bool = False,
        final: bool = False,
        message: str | None = None,
        frame_event: str | None = None,
        rendered_delta: int = 0,
        reused_delta: int = 0,
        extra_outputs: dict[str, bool] | None = None,
    ) -> dict[str, Any]:
        if rendered_delta:
            state["frames_rendered"] = min(int(total_frames), int(state.get("frames_rendered", 0)) + int(rendered_delta))
        if reused_delta:
            state["frames_reused"] = min(int(total_frames), int(state.get("frames_reused", 0)) + int(reused_delta))
        completed_frames = min(int(total_frames), int(state.get("frames_rendered", 0)) + int(state.get("frames_reused", 0)))
        state["status"] = str(status or ("complete" if final else "running"))
        state["stage"] = str(stage or "running")
        state["completed_frames"] = completed_frames
        state["last_completed_frame"] = completed_frames - 1 if completed_frames > 0 else -1
        state["next_frame_index"] = min(int(total_frames), completed_frames)
        state["current_chunk_index"] = min(int(estimated_chunks), max(1, (completed_frames // max(1, frames_per_chunk)) + 1)) if total_frames > 0 else 0
        state["completed_chunks"] = min(int(estimated_chunks), completed_frames // max(1, frames_per_chunk))
        if completed_frames >= int(total_frames) and total_frames > 0:
            state["completed_chunks"] = int(estimated_chunks)
        state["current_chunk_progress_frames"] = completed_frames % max(1, frames_per_chunk)
        if completed_frames >= int(total_frames) and total_frames > 0:
            state["current_chunk_progress_frames"] = 0
        percent = 100.0 if total_frames <= 0 else round((completed_frames / float(max(1, total_frames))) * 100.0, 1)
        state["resume_percent"] = percent
        state["updated_at"] = time.time()
        if frame_event:
            state["frame_event"] = str(frame_event)
        if message:
            state["message"] = str(message)
        outputs = dict(state.get("outputs") or {})
        if extra_outputs:
            outputs.update({k: bool(v) for k, v in extra_outputs.items()})
        state["outputs"] = outputs

        should_emit = force or final
        now = time.time()
        if not should_emit:
            if last_emitted["stage"] != stage:
                should_emit = True
            elif completed_frames in (0, int(total_frames)):
                should_emit = True
            elif completed_frames - int(last_emitted["completed_frames"]) >= checkpoint_interval_frames:
                should_emit = True
            elif completed_frames > 0 and (completed_frames % max(1, frames_per_chunk) == 0):
                should_emit = True
            elif frame_event and completed_frames != int(last_emitted["completed_frames"]):
                if completed_frames <= max(12, fps_render * 8):
                    should_emit = True
                elif (now - float(last_emitted["ts"] or 0.0)) >= 1.0:
                    should_emit = True
        if should_emit:
            _write_runtime_checkpoint(checkpoint_json, state)
            if checkpoint_fn:
                checkpoint_fn(dict(state))
            last_emitted["stage"] = str(stage)
            last_emitted["completed_frames"] = int(completed_frames)
            last_emitted["ts"] = float(now)
        return dict(state)

    return _emit





def render_internal_video_variant(
    *,
    ffmpeg_path: str,
    project_dir: Path,
    project_id: str | None = None,
    variant: dict[str, Any],
    scenes: list[dict[str, Any]],
    audio_path: Path | None,
    model_dir: Path,
    settings: InternalVideoSettings,
    tensorrt_bundle_path: Path | None = None,
    timeline: dict[str, Any] | None = None,
    log_fn=None,
    progress_fn=None,
    cancel_check_fn=None,
    chunk_plan: dict[str, Any] | None = None,
    checkpoint_fn=None,
    source_image_path: Path | None = None,
) -> Path:
    """Render an internal baseline music video.

    Modes:
      - off/keyframes: SD keyframes + Ken Burns + optional overlays
      - frame_img2img: sequential img2img refinement per frame for temporal consistency

    Image animation:
      - when ``source_image_path`` is provided (or ``settings.source_asset`` resolves),
        video-model renders fit it directly into the first SVD anchor. Other temporal
        modes seed the first keyframe through img2img so a painting or photo can be
        brought to life with motion and prompt evolution.
    """
    _require_pillow()

    device = _device_auto(settings.device_preference)
    video_model_path: Path | None = None
    video_model_engine: str | None = None
    video_model_temporal_step_cap: int | None = None
    if settings.temporal_mode == "video_model":
        raw_video_model_path = str(settings.video_model_path or "").strip()
        if not settings.video_model_id or not raw_video_model_path:
            raise UserFacingError(
                "Internal video motion model is not installed",
                hint=(
                    "Open Models and install Internal SVD or Internal AnimateDiff, then retry "
                    "with Temporal mode set to Internal video model."
                ),
                code="INTERNAL_VIDEO_MODEL_NOT_INSTALLED",
                status_code=400,
            )
        video_model_path = Path(raw_video_model_path)
        video_model_engine = str(settings.video_model_engine or "svd").strip().lower()
        if video_model_engine == "auto":
            video_model_id_hint = str(settings.video_model_id or "").lower()
            video_model_engine = (
                "hunyuan_video15"
                if "hunyuan" in video_model_id_hint
                else ("animatediff" if "animatediff" in video_model_id_hint else "svd")
            )
        validate_video_model_layout(video_model_engine, video_model_path)
        if video_model_engine == "animatediff" and _model_family_from_dir(model_dir) != "sd15":
            raise UserFacingError(
                "AnimateDiff internal motion needs an SD 1.5 internal base model",
                hint=(
                    "Switch Internal model to Stable Diffusion v1.5, or use the SVD internal "
                    "video model with SDXL/SD3 keyframes."
                ),
                code="INTERNAL_VIDEO_MODEL_BASE_UNSUPPORTED",
                status_code=400,
            )
        video_model_temporal_step_cap = _video_model_temporal_step_cap(
            engine=video_model_engine,
            device=device,
        )
    keyframe_renderer = normalize_video_model_keyframe_renderer(settings.video_model_keyframe_renderer)
    use_tensorrt_keyframes = settings.temporal_mode == "video_model" and keyframe_renderer == "tensorrt_sd15"
    resolved_tensorrt_bundle_path: Path | None = None
    if use_tensorrt_keyframes:
        if tensorrt_bundle_path is None:
            raise UserFacingError(
                "The TensorRT storyboard-anchor bundle is not installed",
                hint="Open Models and verify the canonical SD 1.5 TensorRT bundle, then retry.",
                code="TRT_ANCHOR_BUNDLE_NOT_INSTALLED",
                status_code=400,
            )
        resolved_tensorrt_bundle_path = Path(tensorrt_bundle_path).expanduser().resolve()
        if not resolved_tensorrt_bundle_path.is_dir():
            raise UserFacingError(
                "The TensorRT storyboard-anchor bundle is unavailable",
                hint="Open Models and verify the canonical SD 1.5 TensorRT bundle, then retry.",
                code="TRT_ANCHOR_BUNDLE_NOT_INSTALLED",
                status_code=400,
            )
    if use_tensorrt_keyframes and device != "cuda":
        raise UserFacingError(
            "TensorRT SD1.5 storyboard anchors require CUDA",
            hint="Switch Device to CUDA or use Internal diffusion keyframes for SVD/AnimateDiff anchors.",
            code="TRT_ANCHOR_CUDA_REQUIRED",
            status_code=400,
        )
    # Load the still-image pipeline lazily. Exact-work-tag video resumes may
    # already have every persisted storyboard anchor and should not spend RAM
    # or several minutes reloading SD1.5 only to discard it before I2V starts.
    pipes = None

    out_w, out_h = settings.width, settings.height
    fps_r = max(1, int(settings.fps_render))
    fps_schedule = max(1, int(settings.fps_output))
    duration_s = float(variant.get("duration_s") or _infer_duration(scenes))
    total_frames = int(math.ceil(duration_s * fps_r))
    deforum_context = _build_unified_deforum_context(
        scenes=scenes,
        timeline=timeline,
        variant=variant,
        settings=settings,
        fps=fps_schedule,
    )

    work_tag = _build_work_tag(
        variant_index=int(variant.get("index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=model_dir,
        settings=settings,
    )
    out_frames = project_dir / "outputs" / "frames_internal" / work_tag
    out_frames.mkdir(parents=True, exist_ok=True)

    key_times = _scene_keyframe_times(scenes, settings.keyframe_interval_s)
    total_units = max(1, len(key_times) + total_frames + 3)
    cache_info = describe_internal_render_cache(
        project_dir=project_dir,
        variant_index=int(variant.get("index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=model_dir,
        settings=settings,
        total_frames=total_frames,
    )
    raw_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_raw.mp4"
    interp_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_interp.mp4"
    final_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}.mp4"
    meta_json = project_dir / "outputs" / "videos" / f"{work_tag}.render.json"
    checkpoint_json = project_dir / "outputs" / "videos" / f"{work_tag}.checkpoint.json"
    emit_checkpoint = _build_checkpoint_emitter(
        checkpoint_json=checkpoint_json,
        project_dir=project_dir,
        work_tag=work_tag,
        render_mode="diffusion",
        variant_index=int(variant.get("index", 0)),
        total_frames=total_frames,
        fps_render=fps_r,
        chunk_plan=chunk_plan,
        checkpoint_fn=checkpoint_fn,
    )
    if progress_fn:
        progress_fn("preparing", 0, total_units, f"Preparing internal render on {device}")
    emit_checkpoint(stage="preparing", status="running", force=True, message=f"Preparing internal render on {device}")

    default_negative_embeds = None
    if log_fn:
        log_fn(
            f"Render cache tag={work_tag} resume_existing_frames={'yes' if settings.resume_existing_frames else 'no'}"
        )
        if use_tensorrt_keyframes:
            log_fn(
                "Video-model storyboard anchors: TensorRT SD1.5 keyframes enabled. "
                "SVD will use these images directly; AnimateDiff still loads its SD1.5 Diffusers base and uses anchors for shot blending."
            )
        log_fn(
            f"Cache status frames={cache_info['frames_present']}/{cache_info['frames_expected']} "
            f"raw={'yes' if cache_info['raw_exists'] else 'no'} "
            f"interp={'yes' if cache_info['interp_exists'] else 'no'} "
            f"final={'yes' if cache_info['final_exists'] else 'no'}"
        )

    if settings.resume_existing_frames and _media_output_is_reusable(ffmpeg_path, final_mp4):
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        motion_cache_ok = (
            settings.temporal_mode != "video_model"
            or _cached_motion_validation_passed(meta_json)
        )
        if audio_ok and motion_cache_ok:
            emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Reusing completed render {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
            if progress_fn:
                progress_fn("complete", total_units, total_units, f"Reusing completed render {final_mp4.name}")
            if log_fn:
                log_fn(f"Reusing completed render {final_mp4.name}")
            return final_mp4
        if not motion_cache_ok and log_fn:
            log_fn(
                f"Ignoring cached video-model output {final_mp4.name}: "
                "its render metadata has no passing native/output motion validation."
            )


    # Generate temporally consistent keyframes
    key_imgs: dict[float, Image.Image] = {}
    prev_key_img: Image.Image | None = None
    prev_key_scene_index: int | None = None
    anchor_dir = project_dir / "outputs" / "anchors_internal" / work_tag
    anchor_dir.mkdir(parents=True, exist_ok=True)
    for i, t in enumerate(key_times):
        if cancel_check_fn:
            cancel_check_fn()
        key_scene_index = next(
            (
                scene_index
                for scene_index, scene in enumerate(scenes)
                if isinstance(scene, dict)
                and float(scene.get("start_s", 0.0) or 0.0) <= float(t)
                < float(scene.get("end_s", duration_s) or duration_s)
            ),
            max(0, len(scenes) - 1),
        )
        anchor_path = anchor_dir / f"anchor_{i:03d}_t{int(round(float(t) * 1000.0)):010d}.png"
        if settings.temporal_mode == "video_model" and settings.resume_existing_frames and anchor_path.is_file():
            try:
                with Image.open(anchor_path) as persisted:
                    img = persisted.convert("RGB").copy()
                if img.size != (int(out_w), int(out_h)):
                    raise ValueError(
                        f"persisted anchor has size {img.size}; expected {(int(out_w), int(out_h))}"
                    )
                key_imgs[t] = img
                prev_key_img = img
                prev_key_scene_index = key_scene_index
                if log_fn:
                    log_fn(f"Reusing persisted storyboard anchor {i+1}/{len(key_times)}: {anchor_path.name}")
                if progress_fn:
                    progress_fn("keyframes", i + 1, total_units, f"Reused keyframe {i+1}/{len(key_times)}")
                emit_checkpoint(
                    stage="keyframes",
                    status="running",
                    message=f"Reused keyframe {i+1}/{len(key_times)}",
                )
                continue
            except Exception as exc:
                if log_fn:
                    log_fn(f"Persisted storyboard anchor {anchor_path.name} is not reusable ({exc}); regenerating it")

        schedule_frame = int(round(float(t) * float(fps_schedule)))
        p = _prompt_text_for_frame(
            frame_idx=schedule_frame,
            scenes=scenes,
            timeline=timeline,
            deforum_context=deforum_context,
            fps=fps_schedule,
        ) or "cinematic"
        negative_prompt = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)
        seed_from_source = i == 0 and source_image_path is not None
        direct_video_source = _use_direct_video_model_source_anchor(
            keyframe_index=i,
            source_image_path=source_image_path,
            temporal_mode=settings.temporal_mode,
        )
        negative_embeds = None
        if not use_tensorrt_keyframes and not direct_video_source:
            if pipes is None:
                pipes = _try_load_pipelines(model_dir, device=device)
                default_negative_embeds = _encode_prompt(pipes, settings.negative_prompt)
            negative_embeds = (
                default_negative_embeds
                if negative_prompt == settings.negative_prompt
                else _encode_prompt(pipes, negative_prompt)  # type: ignore[arg-type]
            )
        seed = _stable_seed_int("key", settings.seed, t, p, work_tag)
        if log_fn:
            prompt_preview = " ".join(str(p or "").split())[:220]
            log_fn(f"Keyframe {i+1}/{len(key_times)} t={t:.2f}s seed={seed} device={device} prompt={prompt_preview!r}")
        if progress_fn:
            progress_fn("keyframes", i, total_units, f"Generating keyframe {i+1}/{len(key_times)}")
        emit_checkpoint(stage="keyframes", status="running", message=f"Generating keyframe {i+1}/{len(key_times)}")
        mpk = _motion_params_at_time(t, timeline, deforum_motion=deforum_context.motion, fps=fps_schedule)
        cfgk = float((mpk or {}).get('cfg', settings.cfg))
        stepsk = int(float((mpk or {}).get('steps', settings.steps)))
        denk = float((mpk or {}).get('denoise', (mpk or {}).get('strength', settings.temporal_strength)))
        prev_key_img = _keyframe_continuity_source(
            prev_key_img,
            previous_scene_index=prev_key_scene_index,
            scene_index=key_scene_index,
            keyframe_continuity_mode=settings.keyframe_continuity_mode,
        )
        if direct_video_source:
            img = _load_render_source_image(source_image_path, size=(out_w, out_h))
            if log_fn:
                log_fn(
                    f"Using source image {Path(source_image_path).name} directly as the first video-model anchor"
                )
        elif use_tensorrt_keyframes:
            img = _generate_tensorrt_sd15_keyframe(
                project_id=project_id,
                prompt=p,
                negative_prompt=negative_prompt,
                width=out_w,
                height=out_h,
                steps=stepsk,
                cfg=cfgk,
                sampler=settings.sampler,
                seed=seed,
                model_id=settings.video_model_keyframe_model_id or "local_sd15_tensorrt_bundle",
                model_path=resolved_tensorrt_bundle_path,
            )
        else:
            pe = _encode_prompt(pipes, p)  # type: ignore[arg-type]
            if seed_from_source:
                # Image animation: bring the uploaded still to life as the first keyframe.
                try:
                    base_src = _load_render_source_image(source_image_path, size=(out_w, out_h))
                    img = _generate_img2img(
                        pipes,  # type: ignore[arg-type]
                        init_image=base_src,
                        prompt_embeds=pe,
                        negative_embeds=negative_embeds,
                        width=out_w,
                        height=out_h,
                        steps=stepsk,
                        cfg=cfgk,
                        seed=seed,
                        strength=max(0.05, min(0.95, float(settings.source_strength))),
                    )
                    if log_fn:
                        log_fn(f"Seeded first keyframe from source image {Path(source_image_path).name}")
                except Exception as e:  # pragma: no cover - depends on runtime model
                    if log_fn:
                        log_fn(f"Source image seed failed ({e}); falling back to txt2img")
                    img = _generate_txt2img(pipes, pe, negative_embeds, out_w, out_h, stepsk, cfgk, seed)  # type: ignore[arg-type]
            elif prev_key_img is None or settings.temporal_mode in ("off",):
                img = _generate_txt2img(pipes, pe, negative_embeds, out_w, out_h, stepsk, cfgk, seed)  # type: ignore[arg-type]
            else:
                # Keyframe continuity: anchor to previous keyframe to keep style stable.
                img = _generate_img2img(
                    pipes,  # type: ignore[arg-type]
                    init_image=prev_key_img,
                    prompt_embeds=pe,
                    negative_embeds=negative_embeds,
                    width=out_w,
                    height=out_h,
                    steps=max(6, int(settings.temporal_steps or max(8, settings.steps - 3))),
                    cfg=cfgk,
                    seed=seed,
                    strength=max(0.05, min(0.95, denk)),
                )
        key_imgs[t] = img
        img.convert("RGB").save(anchor_path)
        prev_key_img = img
        prev_key_scene_index = key_scene_index
        if progress_fn:
            progress_fn("keyframes", i + 1, total_units, f"Ready keyframe {i+1}/{len(key_times)}")
        emit_checkpoint(stage="keyframes", status="running", message=f"Ready keyframe {i+1}/{len(key_times)}")

    def _save_frame(img: Image.Image, fi: int, t: float) -> Path:
        if timeline:
            img = apply_timeline_layers(img, project_dir=project_dir, timeline=timeline, t=t)
        p = _frame_path(out_frames, fi)
        img.save(p)
        return p

    frame_paths: list[Path] = []
    video_model_native_motion_reports: list[dict[str, Any]] = []
    video_model_output_motion_report: dict[str, Any] | None = None
    video_model_expected_native_scene_count = 0

    if settings.temporal_mode == "video_model":
        pe = None
        negative_embeds = None
        default_negative_embeds = None
        _release_still_pipeline_memory(pipes, device, log_fn=log_fn)
        pipes = None  # type: ignore[assignment]

        if video_model_path is None or video_model_engine is None:
            raise RuntimeError("Internal video model preflight state was not initialized.")
        engine = video_model_engine

        if log_fn:
            log_fn(
                f"Internal video model adapter: engine={engine} model_id={settings.video_model_id} "
                f"path={video_model_path}"
            )
            log_fn(
                "Timeline camera overlay on video-model frames: "
                + ("enabled" if settings.video_model_apply_timeline_camera else "disabled")
            )

        def _finish_video_model_frame(frame: "Image.Image", t: float) -> "Image.Image":
            return _apply_video_model_timeline_camera(
                frame,
                out_w,
                out_h,
                t=t,
                timeline=timeline,
                fallback_interval_s=settings.keyframe_interval_s,
                deforum_motion=deforum_context.motion,
                fps=fps_schedule,
                enabled=bool(settings.video_model_apply_timeline_camera),
            )

        source_scenes = [sc for sc in scenes if isinstance(sc, dict)] or [{"start_s": 0.0, "end_s": duration_s, "prompt": DEFAULT_RENDER_PROMPT}]
        sorted_scenes = _storyboard_scene_windows(scenes=source_scenes, duration_s=duration_s, settings=settings)
        video_model_expected_native_scene_count = len(sorted_scenes)
        max_scene_frames = max(2, int(settings.video_model_max_frames_per_scene or 25))
        fi_cursor = 0
        previous_storyboard_source_scene: int | None = None
        previous_video_model_frame: Image.Image | None = None
        if log_fn and normalize_internal_motion_strategy(settings.motion_strategy) == "storyboard_full_motion":
            log_fn(
                f"Storyboard full motion: generated anchors with {len(sorted_scenes)} short motion shots "
                f"(max { _storyboard_shot_max_s(settings):.1f}s each)."
            )
        for scene_index, scene in enumerate(sorted_scenes):
            if cancel_check_fn:
                cancel_check_fn()
            try:
                start_s = max(0.0, float(scene.get("start_s", 0.0) or 0.0))
            except Exception:
                start_s = 0.0
            try:
                end_s = float(scene.get("end_s", 0.0) or 0.0)
            except Exception:
                end_s = 0.0
            if end_s <= start_s:
                next_start = (
                    float(sorted_scenes[scene_index + 1].get("start_s", duration_s) or duration_s)
                    if scene_index + 1 < len(sorted_scenes)
                    else duration_s
                )
                end_s = max(start_s + (1.0 / fps_r), next_start)

            start_f = max(fi_cursor, int(round(start_s * fps_r)))
            end_f = min(total_frames, max(start_f + 1, int(round(end_s * fps_r))))
            if scene_index == len(sorted_scenes) - 1:
                end_f = total_frames
            if start_f >= total_frames or end_f <= start_f:
                continue

            source_scene_index = int(scene.get("_storyboard_source_scene_index", scene_index) or 0)
            authored_scene_boundary = (
                previous_storyboard_source_scene is not None
                and source_scene_index != previous_storyboard_source_scene
            )
            transition_kind = str(
                scene.get("_storyboard_transition")
                or ("dissolve" if authored_scene_boundary else "technical_continue")
            )

            while fi_cursor < start_f and fi_cursor < total_frames:
                t = fi_cursor / fps_r
                a_t, b_t, w = _key_times_bracket(key_times, t)
                filler = key_imgs[a_t].convert("RGB")
                if a_t != b_t:
                    filler = Image.blend(filler, key_imgs[b_t].convert("RGB"), float(w))
                frame_paths.append(_save_frame(_finish_video_model_frame(filler, t), fi_cursor, t))
                fi_cursor += 1

            scene_frame_count = max(1, end_f - start_f)
            shot_index = int(scene.get("_storyboard_shot_index", 0) or 0)
            cached_native_report = _cached_native_motion_report(
                meta_json,
                scene_index=source_scene_index,
                shot_index=shot_index,
            )
            cached_scene_frames_complete = all(
                _frame_path(out_frames, fi).exists() for fi in range(start_f, end_f)
            )
            if (
                settings.resume_existing_frames
                and cached_scene_frames_complete
                and cached_native_report is not None
            ):
                video_model_native_motion_reports.append(
                    {**cached_native_report, "cache_reused": True}
                )
                for fi in range(start_f, end_f):
                    existing = _frame_path(out_frames, fi)
                    frame_paths.append(existing)
                    fi_cursor = fi + 1
                    emit_checkpoint(stage="frames", status="running", message=f"Reusing video-model frame {fi+1}/{total_frames}", frame_event="reused", reused_delta=1)
                try:
                    with Image.open(_frame_path(out_frames, end_f - 1)) as cached_last_frame:
                        previous_video_model_frame = cached_last_frame.convert("RGB").copy()
                except Exception:
                    previous_video_model_frame = None
                previous_storyboard_source_scene = source_scene_index
                continue
            if (
                settings.resume_existing_frames
                and cached_scene_frames_complete
                and cached_native_report is None
                and log_fn
            ):
                log_fn(
                    f"Regenerating video-model scene {scene_index + 1}: existing frames have no "
                    "matching passing native-motion report."
                )

            adapter_frames = min(
                max_scene_frames,
                max(MIN_VIDEO_MODEL_NATIVE_FRAMES, scene_frame_count),
            )
            if engine == "svd":
                adapter_frames = min(adapter_frames, 25)
                cuda_vram = _cuda_total_vram_gb(device)
                if cuda_vram and cuda_vram <= 6.5:
                    adapter_frames = min(adapter_frames, 8)
                elif cuda_vram and cuda_vram <= 8.5 and not bool(settings.video_model_cpu_offload):
                    adapter_frames = min(adapter_frames, 12)
            elif engine == "animatediff":
                cuda_vram = _cuda_total_vram_gb(device)
                if cuda_vram and cuda_vram <= 6.5:
                    adapter_frames = min(adapter_frames, 12)
                elif cuda_vram and cuda_vram <= 8.5 and not bool(settings.video_model_cpu_offload):
                    adapter_frames = min(adapter_frames, 16)

            frame_budget = describe_video_model_frame_budget(
                native_frame_count=adapter_frames,
                output_frame_count=scene_frame_count,
                fps=fps_r,
            )
            if frame_budget["status"] != "pass":
                ratio = frame_budget.get("stretch_ratio")
                ratio_label = f"{float(ratio):.1f}x" if ratio is not None else "unbounded"
                raise UserFacingError(
                    "This video-model shot does not have enough native frames for continuous motion.",
                    hint=(
                        f"The shot would stretch {adapter_frames} native {engine.upper()} frames across "
                        f"{scene_frame_count} raw output frames ({ratio_label}). Use at least 8 Frames per "
                        "scene, keep raw motion shots within a 2x stretch, and select Storyboard full motion "
                        "for long scenes. On this 6 GB CUDA system, use 4-second shots at 2 raw FPS, then "
                        "interpolate the finished output to 24 FPS."
                    ),
                    code="INSUFFICIENT_TEMPORAL_FRAME_DENSITY",
                    status_code=400,
                )

            schedule_frame = int(round(start_s * float(fps_schedule)))
            prompt = _prompt_text_for_frame(
                frame_idx=schedule_frame,
                scenes=scenes,
                timeline=timeline,
                deforum_context=deforum_context,
                fps=fps_schedule,
            ) or render_prompt_from_scene(scene, fallback=DEFAULT_RENDER_PROMPT)
            negative_prompt = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)
            start_anchor_img, end_anchor_img = _video_anchor_images(
                key_imgs=key_imgs,
                key_times=key_times,
                start_s=start_s,
                end_s=end_s,
                duration_s=duration_s,
                fps_render=fps_r,
                width=out_w,
                height=out_h,
            )
            continuity_scope = normalize_keyframe_continuity_mode(
                settings.keyframe_continuity_mode
            )
            continuity_anchor_source = "generated_keyframe"
            if previous_video_model_frame is not None and (
                transition_kind == "technical_continue" or continuity_scope == "project"
            ):
                start_anchor_img = previous_video_model_frame.convert("RGB").resize(
                    start_anchor_img.size,
                    resample=Image.LANCZOS,
                )
                continuity_anchor_source = "previous_native_motion_frame"
                if log_fn:
                    log_fn(
                        "Reusing the preceding native motion frame as this shot's continuity anchor "
                        f"(transition={transition_kind}, scope={continuity_scope})."
                    )
            anchor_mode = _normalize_video_anchor_mode(settings.video_model_anchor_mode)
            init_img = end_anchor_img if anchor_mode == "end" else start_anchor_img
            score_info = video_model_scene_motion_score(
                scene=scene,
                timeline=timeline,
                start_s=start_s,
                end_s=end_s,
                duration_s=duration_s,
                settings=settings,
            )
            if settings.video_model_motion_score_schedule is not None and _normalize_video_motion_score_mode(settings.video_model_motion_score_mode) != "off":
                scheduled_score = int(round(_scheduled_numeric(
                    settings.video_model_motion_score_schedule,
                    schedule_frame,
                    default=float(score_info.get("motion_score") or settings.video_model_manual_motion_score or 4),
                    lo=1.0,
                    hi=7.0,
                )))
                local_score = _clamp_video_motion_score(
                    score_info.get("motion_score") or settings.video_model_manual_motion_score or 4
                )
                # An authored score of 1-2 requests restrained motion and keeps
                # that intent even under an energetic passage. It must still
                # pass the same temporal-motion quality gate as every shot.
                effective_score = (
                    scheduled_score
                    if scheduled_score <= 2
                    else _clamp_video_motion_score((scheduled_score * 0.45) + (local_score * 0.55))
                )
                score_info = {
                    **score_info,
                    "motion_score": effective_score,
                    "scheduled_motion_score": scheduled_score,
                    "local_motion_score": local_score,
                    "source": f"parseq+{score_info.get('source') or 'local'}",
                }
            prompt_for_model = _refine_video_model_prompt(
                prompt,
                score_info=score_info,
                settings=settings,
                scene=scene,
            )
            motion_bucket_id = _video_model_motion_bucket_for_score(settings, score_info)
            seed = _stable_seed_int("video-model", settings.seed, scene_index, prompt_for_model, motion_bucket_id, anchor_mode, work_tag)
            mp_scene = evaluate_motion_state(
                schedule_frame,
                deforum_context.motion,
                defaults={"cfg": settings.cfg, "steps": float(settings.temporal_steps or settings.steps)},
            )
            cfg_for_scene = float(mp_scene.cfg if mp_scene.cfg is not None else settings.cfg)
            scheduled_steps = max(
                1,
                int(float(mp_scene.steps if mp_scene.steps is not None else (settings.temporal_steps or settings.steps))),
            )
            steps_for_scene = _apply_video_model_temporal_step_cap(
                scheduled_steps,
                video_model_temporal_step_cap,
            )
            if video_model_temporal_step_cap is not None and scheduled_steps > steps_for_scene and log_fn:
                log_fn(
                    f"Low-VRAM CUDA safety capped scheduled {engine} temporal steps "
                    f"from {scheduled_steps} to {steps_for_scene}."
                )
            noise_aug_strength = _scheduled_numeric(
                settings.video_model_noise_aug_schedule,
                schedule_frame,
                default=float(settings.video_model_noise_aug_strength),
                lo=0.0,
                hi=1.0,
            )
            shot_anchor_strength = _scheduled_numeric(
                settings.anchor_strength_schedule,
                schedule_frame,
                default=float(settings.anchor_strength),
                lo=0.0,
                hi=1.0,
            )

            if progress_fn:
                progress_fn("video_model", len(key_times) + fi_cursor, total_units, f"Generating {engine} scene {scene_index+1}/{len(sorted_scenes)}")
            emit_checkpoint(stage="video_model", status="running", message=f"Generating {engine} scene {scene_index+1}/{len(sorted_scenes)}")
            if log_fn:
                prompt_preview = " ".join(prompt_for_model.split())[:220]
                score_label = score_info.get("motion_score")
                source_scene = scene.get("_storyboard_source_scene_index")
                shot_index = scene.get("_storyboard_shot_index")
                shot_count = scene.get("_storyboard_shot_count")
                storyboard_label = (
                    f" scene={int(source_scene) + 1} shot={int(shot_index) + 1}/{int(shot_count)}"
                    if source_scene is not None and shot_index is not None and shot_count
                    else ""
                )
                log_fn(
                    f"Generating {engine} scene {scene_index+1}/{len(sorted_scenes)} "
                    f"frames={adapter_frames} seed={seed} anchor={anchor_mode}{storyboard_label} "
                    f"motion_score={score_label} motion_bucket={motion_bucket_id} "
                    f"cfg={cfg_for_scene:.2f} steps={steps_for_scene} noise_aug={noise_aug_strength:.3f} "
                    f"anchor_strength={shot_anchor_strength:.2f} prompt={prompt_preview!r}"
                )

            adapter_w, adapter_h, adapter_note = _video_model_adapter_canvas(
                engine=engine,
                width=out_w,
                height=out_h,
                device=device,
                cpu_offload=bool(settings.video_model_cpu_offload),
            )
            if adapter_note and log_fn:
                log_fn(f"{adapter_note}; final frames will be resized to {out_w}x{out_h}.")

            generated = generate_video_model_frames(
                engine=engine,
                video_model_dir=video_model_path,
                base_model_dir=model_dir,
                init_image=init_img,
                prompt=prompt_for_model,
                negative_prompt=negative_prompt,
                width=adapter_w,
                height=adapter_h,
                num_frames=adapter_frames,
                fps=fps_r,
                steps=steps_for_scene,
                cfg=cfg_for_scene,
                seed=seed,
                device=device,
                dtype=str(settings.video_model_dtype or "auto"),
                motion_bucket_id=int(motion_bucket_id),
                noise_aug_strength=float(noise_aug_strength),
                decode_chunk_size=int(settings.video_model_decode_chunk_size),
                cpu_offload=bool(settings.video_model_cpu_offload),
            )
            if not generated:
                raise RuntimeError(f"Internal {engine} adapter returned no frames.")
            if anchor_mode == "end":
                generated = list(reversed(generated))
            generated = _apply_video_anchor_frames(
                [frame.convert("RGB") for frame in generated],
                anchor_mode=anchor_mode,
                start_img=start_anchor_img,
                end_img=end_anchor_img,
                anchor_strength=float(shot_anchor_strength),
            )
            previous_video_model_frame = generated[-1].convert("RGB").copy()
            native_motion_report = analyze_motion_images(generated, fps=fps_r)
            native_motion_report = {
                **native_motion_report,
                "scene_index": int(source_scene_index),
                "shot_index": shot_index,
                "start_s": round(float(start_s), 4),
                "end_s": round(float(end_s), 4),
                "engine": engine,
                "motion_score": score_info.get("motion_score"),
                "prompt": prompt_for_model,
                "continuity_anchor_source": continuity_anchor_source,
            }
            video_model_native_motion_reports.append(native_motion_report)
            if native_motion_report["status"] != "pass":
                raise UserFacingError(
                    "The internal video model completed, but its native frames did not contain distributed visible motion.",
                    hint=(
                        f"Motion validation found {native_motion_report['perceptually_unique_frames']} perceptually "
                        f"unique frames and {native_motion_report['meaningful_transition_count']} meaningful "
                        "transitions. Use motion score 4 or higher, keep Prompt refine enabled, choose Animate "
                        "subjects or Animate whole scene, and retry with Resume existing cached frames off."
                    ),
                    code="INSUFFICIENT_TEMPORAL_MOTION",
                    status_code=422,
                )
            if log_fn:
                log_fn(
                    "Native motion validation passed: "
                    f"unique={native_motion_report['perceptually_unique_frames']}/{native_motion_report['frame_count']} "
                    f"meaningful_transitions={native_motion_report['meaningful_transition_count']} "
                    f"frozen_ratio={native_motion_report['frozen_pair_ratio']:.3f}"
                )

            for local_i, fi in enumerate(range(start_f, end_f)):
                if cancel_check_fn:
                    cancel_check_fn()
                t = fi / fps_r
                fr = _finish_video_model_frame(
                    temporal_blend_frame(
                        generated,
                        output_index=local_i,
                        output_frame_count=scene_frame_count,
                    ),
                    t,
                )
                if local_i == 0 and authored_scene_boundary and frame_paths:
                    try:
                        with Image.open(frame_paths[-1]) as previous_frame:
                            fr = _blend_storyboard_scene_boundary(
                                previous_frame.convert("RGB"),
                                fr,
                                transition=transition_kind,
                            )
                        if log_fn:
                            log_fn(
                                f"Authored scene boundary {source_scene_index + 1}: "
                                f"transition={transition_kind}"
                            )
                    except Exception as exc:
                        if log_fn:
                            log_fn(
                                f"Authored scene boundary blend was skipped ({exc}); "
                                "continuing with the generated frame"
                            )
                frame_paths.append(_save_frame(fr, fi, t))
                fi_cursor = fi + 1
                if progress_fn:
                    progress_fn("frames", len(key_times) + fi + 1, total_units, f"Rendered video-model frame {fi+1}/{total_frames}")
                emit_checkpoint(stage="frames", status="running", message=f"Rendered video-model frame {fi+1}/{total_frames}", frame_event="rendered", rendered_delta=1)
            previous_storyboard_source_scene = source_scene_index

        while fi_cursor < total_frames:
            t = fi_cursor / fps_r
            a_t, b_t, w = _key_times_bracket(key_times, t)
            filler = key_imgs[a_t].convert("RGB")
            if a_t != b_t:
                filler = Image.blend(filler, key_imgs[b_t].convert("RGB"), float(w))
            frame_paths.append(_save_frame(_finish_video_model_frame(filler, t), fi_cursor, t))
            fi_cursor += 1

    elif settings.temporal_mode != "frame_img2img":
        for fi in range(total_frames):
            if cancel_check_fn:
                cancel_check_fn()
            t = fi / fps_r
            existing = _frame_path(out_frames, fi)
            if settings.resume_existing_frames and existing.exists():
                frame_paths.append(existing)
                if progress_fn:
                    progress_fn("frames", len(key_times) + fi + 1, total_units, f"Reusing frame {fi+1}/{total_frames}")
                emit_checkpoint(stage="frames", status="running", message=f"Reusing frame {fi+1}/{total_frames}", frame_event="reused", reused_delta=1)
                if log_fn and fi % max(1, fps_r * 10) == 0:
                    log_fn(f"Reused cached frame {fi+1}/{total_frames}")
                continue

            a, b, w = _key_times_bracket(key_times, t)
            src = key_imgs[a].convert("RGB")
            if a != b:
                src = Image.blend(src, key_imgs[b].convert("RGB"), float(w))
            comp = _camera_components_at_time(
                t,
                timeline=timeline,
                fallback_interval_s=settings.keyframe_interval_s,
                deforum_motion=deforum_context.motion,
                fps=fps_schedule,
            )
            fr = _apply_camera_components_absolute(src, out_w, out_h, comp)
            frame_paths.append(_save_frame(fr, fi, t))
            if progress_fn:
                progress_fn("frames", len(key_times) + fi + 1, total_units, f"Rendered frame {fi+1}/{total_frames}")
            emit_checkpoint(stage="frames", status="running", message=f"Rendered frame {fi+1}/{total_frames}", frame_event="rendered", rendered_delta=1)
            if log_fn and fi % max(1, fps_r * 3) == 0:
                log_fn(f"Rendered frame {fi+1}/{total_frames}")
    else:
        prev_frame = key_imgs[key_times[0]].resize((out_w, out_h), resample=Image.LANCZOS)
        prev_comp = _camera_components_at_time(
            0.0,
            timeline=timeline,
            fallback_interval_s=settings.keyframe_interval_s,
            deforum_motion=deforum_context.motion,
            fps=fps_schedule,
        )

        refine_every = max(1, int(settings.refine_every_n_frames))
        steps_refine = int(settings.temporal_steps or max(8, settings.steps - 3))

        for fi in range(total_frames):
            if cancel_check_fn:
                cancel_check_fn()
            t = fi / fps_r
            existing = _frame_path(out_frames, fi)
            schedule_frame = int(round(float(t) * float(fps_schedule)))

            a_t, b_t, w = _key_times_bracket(key_times, t)
            comp = _camera_components_at_time(
                t,
                timeline=timeline,
                fallback_interval_s=settings.keyframe_interval_s,
                deforum_motion=deforum_context.motion,
                fps=fps_schedule,
            )

            if settings.resume_existing_frames and existing.exists():
                try:
                    prev_frame = Image.open(existing).convert("RGB").resize((out_w, out_h), resample=Image.LANCZOS)
                    prev_comp = comp
                    frame_paths.append(existing)
                    if progress_fn:
                        progress_fn("frames", len(key_times) + fi + 1, total_units, f"Reusing frame {fi+1}/{total_frames}")
                    emit_checkpoint(stage="frames", status="running", message=f"Reusing frame {fi+1}/{total_frames}", frame_event="reused", reused_delta=1)
                    if log_fn and fi % max(1, fps_r * 10) == 0:
                        log_fn(f"Reused cached frame {fi+1}/{total_frames}")
                    continue
                except Exception:
                    pass

            mp = _motion_params_at_time(t, timeline, deforum_motion=deforum_context.motion, fps=fps_schedule)

            a_frame = int(round(float(a_t) * float(fps_schedule)))
            b_frame = int(round(float(b_t) * float(fps_schedule)))
            a_prompt = _prompt_text_for_frame(
                frame_idx=a_frame,
                scenes=scenes,
                timeline=timeline,
                deforum_context=deforum_context,
                fps=fps_schedule,
            ) or "cinematic"
            b_prompt = _prompt_text_for_frame(
                frame_idx=b_frame,
                scenes=scenes,
                timeline=timeline,
                deforum_context=deforum_context,
                fps=fps_schedule,
            ) or a_prompt
            a_e = _encode_prompt(pipes, a_prompt)
            b_e = _encode_prompt(pipes, b_prompt)
            pe = _blend_embeds(a_e, b_e, w) if settings.prompt_blend else a_e
            negative_prompt = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)
            negative_embeds = (
                default_negative_embeds
                if negative_prompt == settings.negative_prompt
                else _encode_prompt(pipes, negative_prompt)
            )

            init = _apply_camera_components_delta(prev_frame, out_w, out_h, comp, prev_comp)

            # Blend in keyframe anchors to prevent drift.
            anchor = key_imgs[a_t]
            if a_t != b_t:
                anchor = Image.blend(key_imgs[a_t].convert("RGB"), key_imgs[b_t].convert("RGB"), float(w))
            if settings.anchor_strength > 0:
                init = Image.blend(init.convert("RGB"), anchor.convert("RGB"), float(settings.anchor_strength))

            seed = _stable_seed_int("frame", settings.seed, fi, f"{t:.3f}", work_tag)
            if fi % refine_every == 0:
                if log_fn and fi % max(1, fps_r * 3) == 0:
                    log_fn(f"Refining frame {fi+1}/{total_frames} strength={settings.temporal_strength:.2f} steps={steps_refine}")
                out = _generate_img2img(
                    pipes,
                    init_image=init,
                    prompt_embeds=pe,
                    negative_embeds=negative_embeds,
                    width=out_w,
                    height=out_h,
                    steps=int(float((mp or {}).get('steps', steps_refine))),
                    cfg=float((mp or {}).get('cfg', settings.cfg)),
                    seed=seed,
                    strength=float((mp or {}).get('denoise', (mp or {}).get('strength', settings.temporal_strength))),
                )
                prev_frame = out.resize((out_w, out_h), resample=Image.LANCZOS)
            else:
                prev_frame = init.resize((out_w, out_h), resample=Image.LANCZOS)

            prev_comp = comp
            frame_paths.append(_save_frame(prev_frame, fi, t))
            if progress_fn:
                progress_fn("frames", len(key_times) + fi + 1, total_units, f"Rendered frame {fi+1}/{total_frames}")
            emit_checkpoint(stage="frames", status="running", message=f"Rendered frame {fi+1}/{total_frames}", frame_event="rendered", rendered_delta=1)

    if settings.temporal_mode == "video_model":
        video_model_output_motion_report = analyze_motion_paths(
            frame_paths,
            fps=fps_r,
            minimum_frames=MIN_VIDEO_MODEL_OUTPUT_FRAMES,
        )
        if video_model_output_motion_report["status"] != "pass":
            raise UserFacingError(
                "The rendered video sequence contains prolonged still-frame holds.",
                hint=(
                    f"Motion validation measured a {float(video_model_output_motion_report['frozen_pair_ratio']) * 100.0:.1f}% "
                    f"frozen transition ratio and a {float(video_model_output_motion_report['longest_static_hold_s']):.2f}-second "
                    "longest hold. Disable Resume existing cached frames, use Storyboard full motion for long scenes, "
                    "and retry with at least 8 native frames per short shot."
                ),
                code="INSUFFICIENT_TEMPORAL_MOTION",
                status_code=422,
            )
        if log_fn:
            log_fn(
                "Output motion validation passed: "
                f"unique={video_model_output_motion_report['perceptually_unique_frames']}/{video_model_output_motion_report['frame_count']} "
                f"meaningful_transitions={video_model_output_motion_report['meaningful_transition_count']} "
                f"longest_hold_s={video_model_output_motion_report['longest_static_hold_s']:.3f}"
            )

    if cancel_check_fn:
        cancel_check_fn()

    raw_mp4.parent.mkdir(parents=True, exist_ok=True)
    if settings.resume_existing_frames and cache_info["frames_complete"] and raw_mp4.exists():
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, f"Reusing raw MP4 {raw_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing raw MP4 {raw_mp4.name}", extra_outputs={"raw_exists": True})
        if log_fn:
            log_fn(f"Reusing raw MP4 {raw_mp4.name}")
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, "Assembling raw MP4")
        emit_checkpoint(stage="assembling", status="running", force=True, message="Assembling raw MP4")
        if log_fn:
            log_fn("Assembling raw MP4 from rendered frames")
        assemble_image_sequence(
            ffmpeg_path=ffmpeg_path,
            frames_dir=out_frames,
            out_mp4=raw_mp4,
            fps=fps_r,
            glob_pattern="frame_*.png",
            audio_path=None,
        )

    if cancel_check_fn:
        cancel_check_fn()

    if int(settings.fps_output) == int(fps_r):
        if not _media_output_is_reusable(ffmpeg_path, interp_mp4) or interp_mp4.stat().st_mtime < raw_mp4.stat().st_mtime:
            interp_mp4.write_bytes(raw_mp4.read_bytes())
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Keeping FPS at {int(settings.fps_output)}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Keeping FPS at {int(settings.fps_output)}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
        if log_fn:
            log_fn(f"Skipping interpolation because fps_output matches fps_render ({int(settings.fps_output)})")
    elif (
        settings.resume_existing_frames
        and _media_output_is_reusable(ffmpeg_path, interp_mp4)
        and raw_mp4.exists()
        and interp_mp4.stat().st_mtime >= raw_mp4.stat().st_mtime
    ):
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Reusing interpolated MP4 {interp_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing interpolated MP4 {interp_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
        if log_fn:
            log_fn(f"Reusing interpolated MP4 {interp_mp4.name}")
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Interpolating to {int(settings.fps_output)} fps")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Interpolating to {int(settings.fps_output)} fps", extra_outputs={"raw_exists": raw_mp4.exists()})
        if log_fn:
            log_fn(f"Interpolating to {int(settings.fps_output)} fps via {settings.interpolation_engine}")
        interpolate_video_fps(
            ffmpeg_path=ffmpeg_path,
            in_mp4=raw_mp4,
            out_mp4=interp_mp4,
            fps_out=int(settings.fps_output),
            engine=settings.interpolation_engine,
        )

    if cancel_check_fn:
        cancel_check_fn()

    if settings.resume_existing_frames and _media_output_is_reusable(ffmpeg_path, final_mp4):
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        interp_ok = _media_output_is_reusable(ffmpeg_path, interp_mp4) and final_mtime >= interp_mp4.stat().st_mtime
    else:
        audio_ok = False
        interp_ok = False

    if audio_ok and interp_ok:
        if progress_fn:
            progress_fn("muxing", total_units, total_units, f"Reusing final video {final_mp4.name}")
        emit_checkpoint(stage="muxing", status="running", force=True, message=f"Reusing final video {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
        if log_fn:
            log_fn(f"Reusing final video {final_mp4.name}")
    else:
        if progress_fn:
            progress_fn("muxing", total_units, total_units, "Muxing audio and finalizing video")
        emit_checkpoint(stage="muxing", status="running", force=True, message="Muxing audio and finalizing video", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists()})
        if audio_path and audio_path.exists():
            mux_audio(ffmpeg_path=ffmpeg_path, video_mp4=interp_mp4, audio_path=audio_path, out_mp4=final_mp4)
        else:
            final_mp4.write_bytes(interp_mp4.read_bytes())
    meta = {
        "renderer_algorithm_version": INTERNAL_VIDEO_RENDERER_ALGORITHM_VERSION,
        "work_tag": work_tag,
        "completed_at": __import__("time").time(),
        "variant_index": int(variant.get("index", 0)),
        "settings": {
            "fps_render": int(settings.fps_render),
            "fps_output": int(settings.fps_output),
            "width": int(settings.width),
            "height": int(settings.height),
            "steps": int(settings.steps),
            "cfg": float(settings.cfg),
            "sampler": str(settings.sampler),
            "seed": settings.seed,
            "keyframe_interval_s": float(settings.keyframe_interval_s),
            "keyframe_continuity_mode": normalize_keyframe_continuity_mode(
                settings.keyframe_continuity_mode
            ),
            "interpolation_engine": str(settings.interpolation_engine),
            "temporal_mode": str(settings.temporal_mode),
            "temporal_strength": float(settings.temporal_strength),
            "temporal_steps": int(settings.temporal_steps or 0),
            "refine_every_n_frames": int(settings.refine_every_n_frames),
            "anchor_strength": float(settings.anchor_strength),
            "prompt_blend": bool(settings.prompt_blend),
            "motion_strategy": normalize_internal_motion_strategy(settings.motion_strategy),
            "storyboard_shot_max_s": float(_storyboard_shot_max_s(settings)),
            "video_model_engine": str(settings.video_model_engine),
            "video_model_id": str(settings.video_model_id or ""),
            "video_model_path": str(settings.video_model_path or ""),
            "video_model_max_frames_per_scene": int(settings.video_model_max_frames_per_scene),
            "video_model_motion_bucket_id": int(settings.video_model_motion_bucket_id),
            "video_model_noise_aug_strength": float(settings.video_model_noise_aug_strength),
            "video_model_decode_chunk_size": int(settings.video_model_decode_chunk_size),
            "video_model_dtype": str(settings.video_model_dtype),
            "video_model_cpu_offload": bool(settings.video_model_cpu_offload),
            "video_model_motion_score_mode": str(settings.video_model_motion_score_mode),
            "video_model_manual_motion_score": int(settings.video_model_manual_motion_score),
            "video_model_anchor_mode": str(settings.video_model_anchor_mode),
            "video_model_prompt_refine": bool(settings.video_model_prompt_refine),
            "video_model_scene_motion": normalize_video_model_scene_motion(settings.video_model_scene_motion),
            "video_model_apply_timeline_camera": bool(settings.video_model_apply_timeline_camera),
            "video_model_keyframe_renderer": normalize_video_model_keyframe_renderer(settings.video_model_keyframe_renderer),
            "video_model_keyframe_model_id": str(settings.video_model_keyframe_model_id or ""),
            "video_model_motion_score_schedule": settings.video_model_motion_score_schedule,
            "video_model_noise_aug_schedule": settings.video_model_noise_aug_schedule,
            "anchor_strength_schedule": settings.anchor_strength_schedule,
            "source_asset": str(settings.source_asset or ""),
            "resume_existing_frames": bool(settings.resume_existing_frames),
            "model_id": str(settings.model_id),
            "negative_prompt": str(settings.negative_prompt),
            "loras": list(settings.loras),
            "vae": settings.vae,
            "refiner": settings.refiner,
        },
        "frames": {
            "expected": int(total_frames),
            "present": len(list(out_frames.glob("frame_*.png"))),
            "dir": str(out_frames),
        },
        "motion_validation": {
            "status": (
                str(video_model_output_motion_report.get("status"))
                if video_model_output_motion_report is not None
                else "not_applicable"
            ),
            "expected_native_scene_count": video_model_expected_native_scene_count,
            "native_scenes": video_model_native_motion_reports,
            "output_sequence": video_model_output_motion_report,
        },
        "source_anchor": {
            "mode": (
                "direct_video_model"
                if source_image_path is not None and settings.temporal_mode == "video_model"
                else "keyframe_img2img"
                if source_image_path is not None
                else "none"
            ),
            "requested_asset": str(settings.source_asset or ""),
            "resolved_path": str(source_image_path or ""),
        },
        "outputs": {
            "raw_mp4": str(raw_mp4),
            "interp_mp4": str(interp_mp4),
            "final_mp4": str(final_mp4),
            "checkpoint_json": str(checkpoint_json),
        },
        "timeline_digest": _json_digest(_timeline_render_fingerprint(timeline)),
        "scene_digest": _json_digest(scenes or []),
    }
    try:
        meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        from ..store.artifacts import write_artifact_manifest

        source_assets: list[dict[str, Any]] = []
        if audio_path and Path(audio_path).exists():
            source_assets.append(
                {
                    "role": "audio",
                    "path": str(Path(audio_path)),
                    "sha256": None,
                }
            )
        if source_image_path and Path(source_image_path).exists():
            source_assets.append(
                {
                    "role": "source_image",
                    "path": str(Path(source_image_path)),
                    "sha256": None,
                }
            )
        write_artifact_manifest(
            final_mp4,
            project_dir=project_dir,
            project_id=project_id,
            kind="video",
            engine="internal_video",
            model_id=str(settings.model_id or ""),
            model_revision=None,
            seed=int(settings.seed) if settings.seed is not None else None,
            params={
                "width": int(settings.width),
                "height": int(settings.height),
                "fps_render": int(settings.fps_render),
                "fps_output": int(settings.fps_output),
                "temporal_mode": str(settings.temporal_mode),
                "keyframe_continuity_mode": normalize_keyframe_continuity_mode(
                    settings.keyframe_continuity_mode
                ),
                "work_tag": work_tag,
            },
            source_assets=source_assets,
            parents=[str(meta_json.name)] if meta_json.exists() else [],
            extra={"render_meta": str(meta_json.name), "variant_index": int(variant.get("index", 0))},
        )
    except Exception:
        pass
    emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Internal render complete: {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": final_mp4.exists()})
    if log_fn:
        log_fn(f"Internal render complete: {final_mp4.name}")

    return final_mp4


def render_stability_hosted_video_variant(
    *,
    ffmpeg_path: str,
    project_dir: Path,
    variant: dict[str, Any],
    scenes: list[dict[str, Any]],
    audio_path: Path | None,
    settings: InternalVideoSettings,
    stability_api_key: str,
    hosted_settings: dict[str, Any],
    timeline: dict[str, Any] | None = None,
    log_fn=None,
    progress_fn=None,
    cancel_check_fn=None,
    chunk_plan: dict[str, Any] | None = None,
    checkpoint_fn=None,
) -> Path:
    _require_pillow()

    from .stability_platform import StabilityPlatformClient

    service = str(hosted_settings.get("service") or "sd3").strip().lower()
    model = str(hosted_settings.get("model") or "sd3.5-large-turbo").strip().lower()
    style_preset = str(hosted_settings.get("style_preset") or "none").strip().lower()
    output_format = str(hosted_settings.get("output_format") or "png").strip().lower()
    hosted_strength = float(hosted_settings.get("strength") or settings.temporal_strength or 0.55)
    hosted_cfg_scale = float(hosted_settings.get("cfg_scale") or settings.cfg or 6.5)
    client = StabilityPlatformClient(stability_api_key)

    out_w, out_h = settings.width, settings.height
    fps_r = max(1, int(settings.fps_render))
    fps_schedule = max(1, int(settings.fps_output))
    duration_s = float(variant.get("duration_s") or _infer_duration(scenes))
    total_frames = int(math.ceil(duration_s * fps_r))
    deforum_context = _build_unified_deforum_context(
        scenes=scenes,
        timeline=timeline,
        variant=variant,
        settings=settings,
        fps=fps_schedule,
    )

    provider_marker = Path(f"stability_platform/{service}/{model or 'default'}")
    work_tag = _build_work_tag(
        variant_index=int(variant.get("index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=provider_marker,
        settings=settings,
    )
    out_frames = project_dir / "outputs" / "frames_internal" / work_tag
    out_frames.mkdir(parents=True, exist_ok=True)

    key_times = _scene_keyframe_times(scenes, settings.keyframe_interval_s)
    total_units = max(1, len(key_times) + total_frames + 3)
    cache_info = describe_internal_render_cache(
        project_dir=project_dir,
        variant_index=int(variant.get("index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline,
        model_dir=provider_marker,
        settings=settings,
        total_frames=total_frames,
    )
    raw_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_raw.mp4"
    interp_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}_interp.mp4"
    final_mp4 = project_dir / "outputs" / "videos" / f"{work_tag}.mp4"
    meta_json = project_dir / "outputs" / "videos" / f"{work_tag}.render.json"
    checkpoint_json = project_dir / "outputs" / "videos" / f"{work_tag}.checkpoint.json"
    emit_checkpoint = _build_checkpoint_emitter(
        checkpoint_json=checkpoint_json,
        project_dir=project_dir,
        work_tag=work_tag,
        render_mode="hosted",
        variant_index=int(variant.get("index", 0)),
        total_frames=total_frames,
        fps_render=fps_r,
        chunk_plan=chunk_plan,
        checkpoint_fn=checkpoint_fn,
    )
    if progress_fn:
        progress_fn("preparing", 0, total_units, f"Preparing hosted Stability render via {service}")
    emit_checkpoint(stage="preparing", status="running", force=True, message=f"Preparing hosted Stability render via {service}")

    if log_fn:
        log_fn(
            f"Hosted Stability render cache tag={work_tag} service={service} model={model or 'default'} "
            f"resume_existing_frames={'yes' if settings.resume_existing_frames else 'no'}"
        )
        log_fn(
            f"Cache status frames={cache_info['frames_present']}/{cache_info['frames_expected']} "
            f"raw={'yes' if cache_info['raw_exists'] else 'no'} "
            f"interp={'yes' if cache_info['interp_exists'] else 'no'} "
            f"final={'yes' if cache_info['final_exists'] else 'no'}"
        )

    if settings.resume_existing_frames and _media_output_is_reusable(ffmpeg_path, final_mp4):
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        if audio_ok:
            emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Reusing completed hosted render {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
            if progress_fn:
                progress_fn("complete", total_units, total_units, f"Reusing completed hosted render {final_mp4.name}")
            if log_fn:
                log_fn(f"Reusing completed hosted render {final_mp4.name}")
            return final_mp4

    key_imgs: dict[float, Image.Image] = {}
    prev_key_img: Image.Image | None = None
    supports_init = service in {"sd3", "ultra"}
    for i, t in enumerate(key_times):
        if cancel_check_fn:
            cancel_check_fn()
        schedule_frame = int(round(float(t) * float(fps_schedule)))
        prompt = _prompt_text_for_frame(
            frame_idx=schedule_frame,
            scenes=scenes,
            timeline=timeline,
            deforum_context=deforum_context,
            fps=fps_schedule,
        ) or "cinematic"
        negative_prompt = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)
        seed = int(hash(f"hosted-key:{t}:{prompt}") & 0x7FFFFFFF)
        if progress_fn:
            progress_fn("keyframes", i, total_units, f"Generating hosted keyframe {i+1}/{len(key_times)}")
        emit_checkpoint(stage="keyframes", status="running", message=f"Generating hosted keyframe {i+1}/{len(key_times)}")
        if log_fn:
            log_fn(f"Hosted keyframe {i+1}/{len(key_times)} t={t:.2f}s seed={seed} service={service} model={model or 'default'}")

        key_result = client.generate_image(
            prompt=prompt,
            width=out_w,
            height=out_h,
            service=service,
            model=model,
            style_preset=style_preset,
            negative_prompt=negative_prompt,
            seed=seed,
            init_image=(prev_key_img if supports_init and prev_key_img is not None and settings.temporal_mode != "off" else None),
            strength=hosted_strength,
            cfg_scale=hosted_cfg_scale,
            output_format=output_format,
        )
        img = key_result.image
        key_imgs[t] = img
        prev_key_img = img
        if progress_fn:
            progress_fn("keyframes", i + 1, total_units, f"Ready hosted keyframe {i+1}/{len(key_times)}")
        emit_checkpoint(stage="keyframes", status="running", message=f"Ready hosted keyframe {i+1}/{len(key_times)}")

    def _save_frame(img: Image.Image, fi: int, t: float) -> Path:
        if timeline:
            img = apply_timeline_layers(img, project_dir=project_dir, timeline=timeline, t=t)
        p = _frame_path(out_frames, fi)
        img.save(p)
        return p

    for fi in range(total_frames):
        if cancel_check_fn:
            cancel_check_fn()
        t = fi / fps_r
        existing = _frame_path(out_frames, fi)
        if settings.resume_existing_frames and existing.exists():
            if progress_fn:
                progress_fn("frames", len(key_times) + fi + 1, total_units, f"Reusing hosted frame {fi+1}/{total_frames}")
            emit_checkpoint(stage="frames", status="running", message=f"Reusing hosted frame {fi+1}/{total_frames}", frame_event="reused", reused_delta=1)
            continue

        a, _b, _w = _key_times_bracket(key_times, t)
        src = key_imgs[a]
        comp = _camera_components_at_time(
            t,
            timeline=timeline,
            fallback_interval_s=settings.keyframe_interval_s,
            deforum_motion=deforum_context.motion,
            fps=fps_schedule,
        )
        fr = _apply_camera_components_absolute(src, out_w, out_h, comp)
        _save_frame(fr, fi, t)
        if progress_fn:
            progress_fn("frames", len(key_times) + fi + 1, total_units, f"Rendered hosted frame {fi+1}/{total_frames}")
        emit_checkpoint(stage="frames", status="running", message=f"Rendered hosted frame {fi+1}/{total_frames}", frame_event="rendered", rendered_delta=1)
        if log_fn and fi % max(1, fps_r * 3) == 0:
            log_fn(f"Rendered hosted frame {fi+1}/{total_frames}")

    if cancel_check_fn:
        cancel_check_fn()

    raw_mp4.parent.mkdir(parents=True, exist_ok=True)
    if settings.resume_existing_frames and cache_info["frames_complete"] and raw_mp4.exists():
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, f"Reusing hosted raw MP4 {raw_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing hosted raw MP4 {raw_mp4.name}", extra_outputs={"raw_exists": True})
        if log_fn:
            log_fn(f"Reusing hosted raw MP4 {raw_mp4.name}")
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 2, total_units, "Assembling hosted raw MP4")
        emit_checkpoint(stage="assembling", status="running", force=True, message="Assembling hosted raw MP4")
        if log_fn:
            log_fn("Assembling hosted raw MP4 from rendered frames")
        assemble_image_sequence(
            ffmpeg_path=ffmpeg_path,
            frames_dir=out_frames,
            out_mp4=raw_mp4,
            fps=fps_r,
            glob_pattern="frame_*.png",
            audio_path=None,
        )

    if cancel_check_fn:
        cancel_check_fn()

    if int(settings.fps_output) == int(fps_r):
        if not _media_output_is_reusable(ffmpeg_path, interp_mp4) or interp_mp4.stat().st_mtime < raw_mp4.stat().st_mtime:
            interp_mp4.write_bytes(raw_mp4.read_bytes())
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Keeping FPS at {int(settings.fps_output)}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Keeping FPS at {int(settings.fps_output)}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
    elif (
        settings.resume_existing_frames
        and _media_output_is_reusable(ffmpeg_path, interp_mp4)
        and raw_mp4.exists()
        and interp_mp4.stat().st_mtime >= raw_mp4.stat().st_mtime
    ):
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Reusing hosted interpolated MP4 {interp_mp4.name}")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Reusing hosted interpolated MP4 {interp_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": True})
    else:
        if progress_fn:
            progress_fn("assembling", total_units - 1, total_units, f"Interpolating to {int(settings.fps_output)} fps")
        emit_checkpoint(stage="assembling", status="running", force=True, message=f"Interpolating to {int(settings.fps_output)} fps", extra_outputs={"raw_exists": raw_mp4.exists()})
        interpolate_video_fps(
            ffmpeg_path=ffmpeg_path,
            in_mp4=raw_mp4,
            out_mp4=interp_mp4,
            fps_out=int(settings.fps_output),
            engine=settings.interpolation_engine,
        )

    if cancel_check_fn:
        cancel_check_fn()

    if settings.resume_existing_frames and _media_output_is_reusable(ffmpeg_path, final_mp4):
        final_mtime = final_mp4.stat().st_mtime
        audio_ok = (audio_path is None) or (not audio_path.exists()) or (final_mtime >= audio_path.stat().st_mtime)
        interp_ok = _media_output_is_reusable(ffmpeg_path, interp_mp4) and final_mtime >= interp_mp4.stat().st_mtime
    else:
        audio_ok = False
        interp_ok = False

    if audio_ok and interp_ok:
        if progress_fn:
            progress_fn("muxing", total_units, total_units, f"Reusing hosted final video {final_mp4.name}")
        emit_checkpoint(stage="muxing", status="running", force=True, message=f"Reusing hosted final video {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": True})
    else:
        if progress_fn:
            progress_fn("muxing", total_units, total_units, "Muxing audio and finalizing hosted video")
        emit_checkpoint(stage="muxing", status="running", force=True, message="Muxing audio and finalizing hosted video", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists()})
        if audio_path and audio_path.exists():
            mux_audio(ffmpeg_path=ffmpeg_path, video_mp4=interp_mp4, audio_path=audio_path, out_mp4=final_mp4)
        else:
            final_mp4.write_bytes(interp_mp4.read_bytes())

    meta = {
        "work_tag": work_tag,
        "completed_at": __import__("time").time(),
        "variant_index": int(variant.get("index", 0)),
        "render_mode": "hosted",
        "hosted_provider": {
            "service": service,
            "model": model,
            "style_preset": style_preset,
            "output_format": output_format,
            "strength": hosted_strength,
            "cfg_scale": hosted_cfg_scale,
        },
        "settings": {
            "fps_render": int(settings.fps_render),
            "fps_output": int(settings.fps_output),
            "width": int(settings.width),
            "height": int(settings.height),
            "keyframe_interval_s": float(settings.keyframe_interval_s),
            "interpolation_engine": str(settings.interpolation_engine),
            "temporal_mode": "keyframes",
            "resume_existing_frames": bool(settings.resume_existing_frames),
            "model_id": str(settings.model_id),
        },
        "frames": {
            "expected": int(total_frames),
            "present": len(list(out_frames.glob("frame_*.png"))),
            "dir": str(out_frames),
        },
        "outputs": {
            "raw_mp4": str(raw_mp4),
            "interp_mp4": str(interp_mp4),
            "final_mp4": str(final_mp4),
            "checkpoint_json": str(checkpoint_json),
        },
        "timeline_digest": _json_digest(_timeline_render_fingerprint(timeline)),
        "scene_digest": _json_digest(scenes or []),
    }
    try:
        meta_json.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    emit_checkpoint(stage="complete", status="complete", force=True, final=True, message=f"Hosted render complete: {final_mp4.name}", extra_outputs={"raw_exists": raw_mp4.exists(), "interp_exists": interp_mp4.exists(), "final_exists": final_mp4.exists()})
    if log_fn:
        log_fn(f"Hosted render complete: {final_mp4.name}")
    return final_mp4


def _scene_energy_at_time(scene: dict[str, Any] | None, t: float, duration_s: float) -> float:
    """Return the best available 0..1 energy value for motion scoring."""
    scene = scene or {}
    for key in ("energy", "avg_energy", "peak_energy"):
        val = scene.get(key) if isinstance(scene, dict) else None
        if val is None:
            continue
        try:
            return max(0.0, min(1.0, float(val)))
        except Exception:
            continue
    if duration_s <= 0:
        return 0.5
    # Gentle breathing curve so the draft is never perfectly static.
    return max(0.0, min(1.0, 0.5 + 0.18 * math.sin((t / max(1e-6, duration_s)) * 2.0 * math.pi)))


def _normalize_video_motion_score_mode(mode: Any) -> str:
    mode_l = str(mode or "auto").strip().lower()
    return mode_l if mode_l in {"auto", "manual", "off"} else "auto"


def _normalize_video_anchor_mode(mode: Any) -> str:
    mode_l = str(mode or "start").strip().lower()
    return mode_l if mode_l in {"start", "end", "both", "loop"} else "start"


def _clamp_video_motion_score(value: Any, *, default: int = 4) -> int:
    try:
        raw = int(round(float(value)))
    except Exception:
        raw = int(default)
    return max(1, min(7, raw))


def _coerce_unit_float(value: Any) -> float | None:
    try:
        out = float(value)
    except Exception:
        return None
    if not math.isfinite(out):
        return None
    return max(0.0, min(1.0, out))


def _scene_energy_values(scene: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not isinstance(scene, dict):
        return None, None
    energy = None
    peak = None
    for key in ("energy", "avg_energy", "avgEnergy", "intensity", "scene_intensity"):
        energy = _coerce_unit_float(scene.get(key))
        if energy is not None:
            break
    for key in ("peak_energy", "peakEnergy", "onset_energy", "transient_energy"):
        peak = _coerce_unit_float(scene.get(key))
        if peak is not None:
            break
    return energy, peak


def _iter_timeline_sections(timeline: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not isinstance(timeline, dict):
        return []
    candidates: list[Any] = []
    for key in ("sections", "scene_sections"):
        value = timeline.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    reactive = timeline.get("reactive_lab")
    if isinstance(reactive, dict):
        for key in ("sections", "phrases", "segments"):
            value = reactive.get(key)
            if isinstance(value, list):
                candidates.extend(value)
    return [item for item in candidates if isinstance(item, dict)]


def _timeline_energy_for_range(timeline: dict[str, Any] | None, start_s: float, end_s: float) -> tuple[float | None, float | None]:
    sections = _iter_timeline_sections(timeline)
    if not sections:
        return None, None
    weighted = 0.0
    peak = 0.0
    total = 0.0
    for section in sections:
        try:
            sec_start = float(
                section.get("start_s", section.get("start", section.get("startTime", 0.0))) or 0.0
            )
            sec_end = float(
                section.get("end_s", section.get("end", section.get("endTime", sec_start))) or sec_start
            )
        except Exception:
            continue
        overlap = max(0.0, min(end_s, sec_end) - max(start_s, sec_start))
        if overlap <= 0:
            continue
        energy, section_peak = _scene_energy_values(section)
        if energy is None:
            continue
        weighted += energy * overlap
        peak = max(peak, section_peak if section_peak is not None else energy)
        total += overlap
    if total <= 0:
        return None, None
    return max(0.0, min(1.0, weighted / total)), max(0.0, min(1.0, peak))


def _timeline_event_density(timeline: dict[str, Any] | None, start_s: float, end_s: float) -> float:
    if not isinstance(timeline, dict):
        return 0.0
    reactive = timeline.get("reactive_lab")
    sources: list[Any] = []
    if isinstance(reactive, dict):
        for key in ("cue_events", "onset_events", "beat_markers", "beats"):
            value = reactive.get(key)
            if isinstance(value, list):
                sources.extend(value)
    for key in ("cue_events", "onset_events", "beat_markers", "beats"):
        value = timeline.get(key)
        if isinstance(value, list):
            sources.extend(value)
    if not sources:
        return 0.0
    count = 0
    for event in sources:
        if isinstance(event, dict):
            raw_t = event.get("time_s", event.get("time", event.get("t", event.get("start_s"))))
        else:
            raw_t = event
        try:
            t = float(raw_t)
        except Exception:
            continue
        if start_s <= t < end_s:
            count += 1
    duration = max(0.25, end_s - start_s)
    return max(0.0, min(1.0, count / max(1.0, duration * 2.0)))


def video_model_scene_motion_score(
    *,
    scene: dict[str, Any] | None,
    timeline: dict[str, Any] | None,
    start_s: float,
    end_s: float,
    duration_s: float,
    settings: InternalVideoSettings,
) -> dict[str, Any]:
    mode = _normalize_video_motion_score_mode(settings.video_model_motion_score_mode)
    manual_score = _clamp_video_motion_score(settings.video_model_manual_motion_score)
    scene_energy, scene_peak = _scene_energy_values(scene)
    timeline_energy, timeline_peak = _timeline_energy_for_range(timeline, start_s, end_s)
    event_density = _timeline_event_density(timeline, start_s, end_s)

    source = "fallback"
    energy = 0.5
    peak = 0.5
    if scene_energy is not None and timeline_energy is not None:
        energy = (scene_energy * 0.35) + (timeline_energy * 0.65)
        peak = max(
            scene_peak if scene_peak is not None else scene_energy,
            timeline_peak if timeline_peak is not None else timeline_energy,
        )
        source = "scene+timeline"
    elif scene_energy is not None:
        energy = scene_energy
        peak = scene_peak if scene_peak is not None else scene_energy
        source = "scene"
    elif timeline_energy is not None:
        energy = timeline_energy
        peak = timeline_peak if timeline_peak is not None else timeline_energy
        source = "timeline"
    elif duration_s > 0:
        mid_t = (float(start_s) + float(end_s)) * 0.5
        energy = _scene_energy_at_time(scene, mid_t, duration_s)
        peak = energy

    if event_density > 0:
        energy = max(energy, min(1.0, energy + event_density * 0.18))
        peak = max(peak, min(1.0, event_density))
        source = f"{source}+events" if source != "fallback" else "events"

    if mode == "off":
        score: int | None = None
    elif mode == "manual":
        score = manual_score
        source = "manual"
    else:
        blended = max(0.0, min(1.0, (float(energy) * 0.75) + (float(peak) * 0.25)))
        score = _clamp_video_motion_score(1.0 + blended * 6.0)

    return {
        "start_s": round(float(start_s), 3),
        "end_s": round(float(end_s), 3),
        "energy": round(float(energy), 3),
        "peak_energy": round(float(peak), 3),
        "event_density": round(float(event_density), 3),
        "motion_score": score,
        "source": source,
    }


def video_model_scene_motion_scores(
    *,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    settings: InternalVideoSettings,
    duration_s: float,
) -> list[dict[str, Any]]:
    valid_scenes = [scene for scene in scenes if isinstance(scene, dict)] or [{"start_s": 0.0, "end_s": duration_s}]
    out: list[dict[str, Any]] = []
    for index, scene in enumerate(valid_scenes):
        try:
            start_s = max(0.0, float(scene.get("start_s", 0.0) or 0.0))
        except Exception:
            start_s = 0.0
        try:
            end_s = float(scene.get("end_s", 0.0) or 0.0)
        except Exception:
            end_s = 0.0
        if end_s <= start_s:
            next_start = (
                float(valid_scenes[index + 1].get("start_s", duration_s) or duration_s)
                if index + 1 < len(valid_scenes)
                else duration_s
            )
            end_s = max(start_s + 0.5, next_start)
        item = video_model_scene_motion_score(
            scene=scene,
            timeline=timeline,
            start_s=start_s,
            end_s=end_s,
            duration_s=duration_s,
            settings=settings,
        )
        item["scene_index"] = index
        out.append(item)
    return out


def _storyboard_shot_max_s(settings: InternalVideoSettings) -> float:
    try:
        value = float(settings.storyboard_shot_max_s or 4.0)
    except Exception:
        value = 4.0
    return max(1.0, min(12.0, value))


def _normalize_storyboard_transition(scene: dict[str, Any] | None) -> str:
    if not isinstance(scene, dict):
        return "dissolve"
    raw = str(
        scene.get("transition")
        or scene.get("transition_cue")
        or scene.get("transitionCue")
        or "dissolve"
    ).strip().lower()
    if any(token in raw for token in ("cut", "impact", "smash")):
        return "cut"
    if any(token in raw for token in ("dissolve", "fade", "blend", "match", "flow")):
        return "dissolve"
    return "dissolve"


def _blend_storyboard_scene_boundary(
    previous_frame: "Image.Image",
    current_frame: "Image.Image",
    *,
    transition: str,
) -> "Image.Image":
    current = current_frame.convert("RGB")
    if str(transition or "").strip().lower() != "dissolve":
        return current
    previous = previous_frame.convert("RGB").resize(current.size, resample=Image.LANCZOS)
    # One raw 2-FPS bridge frame becomes a restrained half-second dissolve
    # after final interpolation without changing the movie's duration.
    return Image.blend(previous, current, 0.5)


def _storyboard_scene_windows(
    *,
    scenes: list[dict[str, Any]],
    duration_s: float,
    settings: InternalVideoSettings,
) -> list[dict[str, Any]]:
    valid_scenes = [scene for scene in scenes if isinstance(scene, dict)] or [
        {"start_s": 0.0, "end_s": duration_s, "prompt": DEFAULT_RENDER_PROMPT}
    ]
    strategy = normalize_internal_motion_strategy(settings.motion_strategy)
    max_shot_s = _storyboard_shot_max_s(settings)
    windows: list[dict[str, Any]] = []

    for scene_index, scene in enumerate(valid_scenes):
        try:
            start_s = max(0.0, float(scene.get("start_s", 0.0) or 0.0))
        except Exception:
            start_s = 0.0
        try:
            end_s = float(scene.get("end_s", 0.0) or 0.0)
        except Exception:
            end_s = 0.0
        if end_s <= start_s:
            next_start = (
                float(valid_scenes[scene_index + 1].get("start_s", duration_s) or duration_s)
                if scene_index + 1 < len(valid_scenes)
                else duration_s
            )
            end_s = max(start_s + 0.5, next_start)

        duration = max(0.5, end_s - start_s)
        shot_count = 1
        if strategy == "storyboard_full_motion":
            shot_count = max(1, int(math.ceil(duration / max_shot_s)))
        for shot_index in range(shot_count):
            shot_start = start_s + (duration * (shot_index / shot_count))
            shot_end = start_s + (duration * ((shot_index + 1) / shot_count))
            if shot_index == shot_count - 1:
                shot_end = end_s
            shot = dict(scene)
            shot["start_s"] = round(float(shot_start), 3)
            shot["end_s"] = round(float(shot_end), 3)
            shot["_storyboard_source_scene_index"] = scene_index
            shot["_storyboard_shot_index"] = shot_index
            shot["_storyboard_shot_count"] = shot_count
            shot["_storyboard_original_start_s"] = round(float(start_s), 3)
            shot["_storyboard_original_end_s"] = round(float(end_s), 3)
            shot["_storyboard_motion_strategy"] = strategy
            shot["_storyboard_transition"] = (
                "technical_continue"
                if shot_index > 0
                else ("opening" if scene_index == 0 else _normalize_storyboard_transition(scene))
            )
            windows.append(shot)
    return windows


def _motion_intent_for_score(score: Any) -> dict[str, str]:
    if score is None:
        return {
            "subject_motion": "prompt-led subject motion",
            "camera_motion": "steady cinematic camera",
            "environment_motion": "subtle atmosphere",
        }
    score_i = _clamp_video_motion_score(score)
    if score_i <= 2:
        return {
            "subject_motion": "restrained breathing motion",
            "camera_motion": "slow locked-off push",
            "environment_motion": "soft ambient drift",
        }
    if score_i >= 6:
        return {
            "subject_motion": "energetic beat-reactive movement",
            "camera_motion": "assertive dolly or orbit",
            "environment_motion": "visible particles, light, or fabric motion",
        }
    return {
        "subject_motion": "controlled music-reactive movement",
        "camera_motion": "smooth forward glide",
        "environment_motion": "moderate atmospheric motion",
    }


def _storyboard_scene_text(scene: dict[str, Any] | None, *keys: str) -> str:
    if not isinstance(scene, dict):
        return ""
    storyboard = scene.get("storyboard") if isinstance(scene.get("storyboard"), dict) else {}
    for source in (scene, storyboard):
        for key in keys:
            value = source.get(key)
            if isinstance(value, (dict, list, tuple, set)):
                continue
            text = " ".join(str(value or "").split())
            if text:
                return text
    return ""


def _storyboard_shot_phase(scene: dict[str, Any] | None) -> str:
    if not isinstance(scene, dict):
        return "single"
    shot_index = max(0, int(scene.get("_storyboard_shot_index", 0) or 0))
    shot_count = max(1, int(scene.get("_storyboard_shot_count", 1) or 1))
    if shot_count <= 1:
        return "single"
    if shot_index <= 0:
        return "establish"
    if shot_index >= shot_count - 1:
        return "resolve"
    return "develop"


def _apply_video_model_timeline_camera(
    frame: "Image.Image",
    out_w: int,
    out_h: int,
    *,
    t: float,
    timeline: dict[str, Any] | None,
    fallback_interval_s: float,
    deforum_motion: DeforumMotionScheduleBundle | None,
    fps: int,
    enabled: bool,
) -> "Image.Image":
    """Optionally layer authored Timeline/Deforum camera motion over I2V output."""
    resized = frame.convert("RGB").resize((out_w, out_h), resample=Image.LANCZOS)
    if not enabled:
        return resized

    points: list[dict[str, Any]] = []
    if isinstance(timeline, dict):
        camera = timeline.get("camera")
        keyframes = camera.get("keyframes") if isinstance(camera, dict) else None
        if isinstance(keyframes, list):
            points = [point for point in keyframes if isinstance(point, dict) and "t" in point]

    has_authored_camera = _camera_keyframes_are_actionable(points)
    has_scheduled_camera = bool(deforum_motion and deforum_motion.has_camera_motion())
    if not has_authored_camera and not has_scheduled_camera:
        return resized

    comp = _camera_components_at_time(
        t,
        timeline=timeline,
        fallback_interval_s=fallback_interval_s,
        deforum_motion=deforum_motion,
        fps=fps,
    )
    return _apply_camera_components_absolute(resized, out_w, out_h, comp)


def describe_storyboard_motion_plan(
    *,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    settings: InternalVideoSettings,
    duration_s: float,
) -> dict[str, Any] | None:
    strategy = normalize_internal_motion_strategy(settings.motion_strategy)
    if strategy != "storyboard_full_motion":
        return None
    keyframe_renderer = normalize_video_model_keyframe_renderer(settings.video_model_keyframe_renderer)
    anchor_source = (
        "source_image"
        if settings.source_asset
        else ("tensorrt_sd15_keyframe" if keyframe_renderer == "tensorrt_sd15" else "generated_scene_keyframe")
    )

    windows = _storyboard_scene_windows(scenes=scenes, duration_s=duration_s, settings=settings)
    shots: list[dict[str, Any]] = []
    for shot in windows:
        start_s = float(shot.get("start_s") or 0.0)
        end_s = float(shot.get("end_s") or max(start_s + 0.5, duration_s))
        score_info = video_model_scene_motion_score(
            scene=shot,
            timeline=timeline,
            start_s=start_s,
            end_s=end_s,
            duration_s=duration_s,
            settings=settings,
        )
        prompt = render_prompt_from_scene(shot, fallback=DEFAULT_RENDER_PROMPT)
        intent = _motion_intent_for_score(score_info.get("motion_score"))
        refined_prompt = _refine_video_model_prompt(
            prompt,
            score_info=score_info,
            settings=settings,
            scene=shot,
        )
        source_scene_index = int(shot.get("_storyboard_source_scene_index", 0) or 0)
        shot_index = int(shot.get("_storyboard_shot_index", 0) or 0)
        shot_count = int(shot.get("_storyboard_shot_count", 1) or 1)
        shots.append(
            {
                "scene_index": source_scene_index,
                "shot_index": shot_index,
                "shot_count": shot_count,
                "start_s": round(start_s, 3),
                "end_s": round(end_s, 3),
                "prompt": " ".join(refined_prompt.split())[:1200],
                "shot_phase": _storyboard_shot_phase(shot),
                "setting": _storyboard_scene_text(
                    shot,
                    "setting",
                    "location",
                    "location_hint",
                    "locationHint",
                ),
                "shot_type": _storyboard_scene_text(shot, "shot_type", "shotType", "composition"),
                "character_lock": _storyboard_scene_text(
                    shot,
                    "character_lock",
                    "characterLock",
                    "subject",
                    "subject_anchor",
                ),
                "style_lock": _storyboard_scene_text(
                    shot,
                    "style_lock",
                    "styleLock",
                    "visual_lock",
                    "visualLock",
                ),
                "start_state": _storyboard_scene_text(
                    shot,
                    "start_state",
                    "startState",
                    "opening_state",
                ),
                "end_state": _storyboard_scene_text(
                    shot,
                    "end_state",
                    "endState",
                    "closing_state",
                ),
                "subject_anchor": _storyboard_scene_text(
                    shot,
                    "character_lock",
                    "characterLock",
                    "subject",
                    "subject_anchor",
                ),
                "shot_action": _storyboard_scene_text(shot, "action", "shot_action"),
                "authored_camera": _storyboard_scene_text(shot, "camera", "camera_move", "camera_hint"),
                "authored_subject_motion": _storyboard_scene_text(shot, "motion", "subject_motion", "motion_hint"),
                "authored_environment_motion": _storyboard_scene_text(
                    shot,
                    "environment_motion",
                    "environmentMotion",
                ),
                "continuity": _storyboard_scene_text(
                    shot,
                    "continuity",
                    "continuity_note",
                    "continuityNote",
                ),
                "anchor_source": anchor_source,
                "keyframe_renderer": keyframe_renderer,
                "scene_motion": normalize_video_model_scene_motion(settings.video_model_scene_motion),
                "transition": (
                    "start from generated visual anchor"
                    if str(shot.get("_storyboard_transition") or "") == "opening"
                    and anchor_source == "generated_scene_keyframe"
                    else str(shot.get("_storyboard_transition") or "technical_continue")
                ),
                **intent,
                "motion_score": score_info.get("motion_score"),
                "motion_source": score_info.get("source"),
            }
        )

    return {
        "strategy": strategy,
        "anchor_source": anchor_source,
        "keyframe_renderer": keyframe_renderer,
        "keyframe_model_id": (
            settings.video_model_keyframe_model_id or "local_sd15_tensorrt_bundle"
            if keyframe_renderer == "tensorrt_sd15"
            else None
        ),
        "shot_max_s": _storyboard_shot_max_s(settings),
        "scene_count": len([scene for scene in scenes if isinstance(scene, dict)]),
        "shot_count": len(shots),
        "shots": shots,
    }


def _video_model_motion_bucket_for_score(settings: InternalVideoSettings, score_info: dict[str, Any]) -> int:
    base_bucket = max(1, min(255, int(settings.video_model_motion_bucket_id or 127)))
    if _normalize_video_motion_score_mode(settings.video_model_motion_score_mode) == "off":
        return base_bucket
    score = score_info.get("motion_score")
    if score is None:
        return base_bucket
    score_i = _clamp_video_motion_score(score)
    mapped = int(round(72 + ((score_i - 1) / 6.0) * 120))
    return max(1, min(255, int(round((mapped * 0.75) + (base_bucket * 0.25)))))


def _refine_video_model_prompt(
    prompt: str,
    *,
    score_info: dict[str, Any],
    settings: InternalVideoSettings,
    scene: dict[str, Any] | None = None,
) -> str:
    fallback = prompt or DEFAULT_RENDER_PROMPT
    if not bool(settings.video_model_prompt_refine):
        return limit_prompt_words(
            fallback,
            max_words=CLIP_SAFE_RENDER_PROMPT_MAX_WORDS,
        )

    # SVD conditions on the generated image, while AnimateDiff uses SD1.5 CLIP text.
    # Keep one truthful prompt contract for both: concrete subject/action/motion first,
    # within the 77-token CLIP window. Exact start/end states and transitions remain in
    # the structured storyboard motion plan and are enforced through anchor reuse.
    refined_scene = dict(scene) if isinstance(scene, dict) else {"character_lock": fallback}
    score = score_info.get("motion_score")
    score_i = _clamp_video_motion_score(score or settings.video_model_manual_motion_score or 4)
    scene_motion = normalize_video_model_scene_motion(settings.video_model_scene_motion)
    if not isinstance(scene, dict) and not _storyboard_scene_text(refined_scene, "action", "shot_action"):
        refined_scene["action"] = (
            "slow restrained movement"
            if score_i <= 2
            else (
                "energetic music reactive movement"
                if score_i >= 6
                else "controlled music reactive movement"
            )
        )
    if not _storyboard_scene_text(refined_scene, "motion", "subject_motion", "motion_hint"):
        refined_scene["motion"] = (
            "whole scene changes with motion"
            if scene_motion == "scene"
            else (
                "subtle breathing and pose changes"
                if score_i <= 2
                else (
                    "energetic pose and object changes"
                    if score_i >= 6
                    else "visible controlled pose changes"
                )
            )
        )
    if not _storyboard_scene_text(refined_scene, "environment_motion", "environmentMotion"):
        refined_scene["environment_motion"] = (
            "gentle atmosphere movement"
            if scene_motion == "camera"
            else (
                "visible objects themselves move naturally"
                if scene_motion == "scene"
                else "foreground objects and atmosphere move visibly"
            )
        )
    if scene_motion == "camera" and not _storyboard_scene_text(refined_scene, "camera", "camera_move", "camera_hint"):
        refined_scene["camera"] = "camera motion is primary"
    return operational_render_prompt_from_scene(
        refined_scene,
        fallback=fallback,
        max_words=CLIP_SAFE_RENDER_PROMPT_MAX_WORDS,
        include_states=False,
    )


def describe_internal_video_model_preflight(
    *,
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    settings: InternalVideoSettings,
    duration_s: float,
    total_frames: int,
    hardware: dict[str, Any] | None = None,
) -> dict[str, Any]:
    hw = hardware or {}
    engine = str(settings.video_model_engine or "svd").strip().lower()
    if engine == "auto":
        model_id_hint = str(settings.video_model_id or "").lower()
        engine = (
            "hunyuan_video15"
            if "hunyuan" in model_id_hint
            else ("animatediff" if "animatediff" in model_id_hint else "svd")
        )
    mode = _normalize_video_motion_score_mode(settings.video_model_motion_score_mode)
    anchor_mode = _normalize_video_anchor_mode(settings.video_model_anchor_mode)
    keyframe_renderer = normalize_video_model_keyframe_renderer(settings.video_model_keyframe_renderer)
    checks: list[dict[str, Any]] = []
    warnings: list[str] = []

    backend = str(settings.device_preference or "").strip().lower()
    if backend in {"", "auto"}:
        backend = str(hw.get("backend") or hw.get("device") or "cpu").lower()
    vram_gb = float(hw.get("vram_gb") or hw.get("cuda_vram_gb") or 0.0)

    effective_native_cap = max(2, int(settings.video_model_max_frames_per_scene or 25))
    if engine == "svd":
        effective_native_cap = min(effective_native_cap, 25)
        if backend == "cuda" and vram_gb and vram_gb <= 6.5:
            effective_native_cap = min(effective_native_cap, 8)
        elif backend == "cuda" and vram_gb and vram_gb <= 8.5 and not bool(settings.video_model_cpu_offload):
            effective_native_cap = min(effective_native_cap, 12)
    elif engine == "animatediff":
        if backend == "cuda" and vram_gb and vram_gb <= 6.5:
            effective_native_cap = min(effective_native_cap, 12)
        elif backend == "cuda" and vram_gb and vram_gb <= 8.5 and not bool(settings.video_model_cpu_offload):
            effective_native_cap = min(effective_native_cap, 16)
    elif engine == "hunyuan_video15":
        # Hunyuan's native context is intentionally conservative until the
        # project qualifies a concrete local checkpoint/profile. These caps
        # describe the adapter contract and do not certify hardware support.
        effective_native_cap = min(effective_native_cap, 61)
        if backend == "cuda" and vram_gb and vram_gb <= 6.5:
            effective_native_cap = min(effective_native_cap, 8)
        elif backend == "cuda" and vram_gb and vram_gb <= 8.5:
            effective_native_cap = min(effective_native_cap, 12)
        warnings.append(
            "HunyuanVideo-1.5 local execution remains discovery-only until its adapter, low-VRAM profile, and fresh temporal output evidence are qualified."
        )
        checks.append({"name": "adapter_qualification", "status": "blocked"})

    motion_frame_budgets: list[dict[str, Any]] = []
    planned_windows = _storyboard_scene_windows(
        scenes=scenes,
        duration_s=duration_s,
        settings=settings,
    )
    for shot_index, shot in enumerate(planned_windows):
        start_s = max(0.0, float(shot.get("start_s", 0.0) or 0.0))
        end_s = max(start_s, float(shot.get("end_s", duration_s) or duration_s))
        output_frames = max(1, int(round((end_s - start_s) * max(1, int(settings.fps_render)))))
        native_frames = min(
            effective_native_cap,
            max(MIN_VIDEO_MODEL_NATIVE_FRAMES, output_frames),
        )
        motion_frame_budgets.append(
            {
                **describe_video_model_frame_budget(
                    native_frame_count=native_frames,
                    output_frame_count=output_frames,
                    fps=max(1, int(settings.fps_render)),
                ),
                "shot_index": shot_index,
                "start_s": round(start_s, 4),
                "end_s": round(end_s, 4),
            }
        )

    failed_motion_budgets = [
        budget for budget in motion_frame_budgets if budget.get("status") != "pass"
    ]
    if failed_motion_budgets:
        worst = max(
            failed_motion_budgets,
            key=lambda item: float(item.get("stretch_ratio") or float("inf")),
        )
        ratio = worst.get("stretch_ratio")
        ratio_label = f"{float(ratio):.1f}x" if ratio is not None else "unbounded"
        warnings.append(
            "Motion-frame density is too low: at least one shot would use "
            f"{worst['native_frame_count']} native frame(s) for {worst['output_frame_count']} raw output "
            f"frame(s) ({ratio_label}). Use Storyboard full motion for long scenes, at least 8 native "
            "frames per shot, and no more than 2x temporal stretching."
        )
        checks.append(
            {
                "name": "motion_density",
                "status": "error",
                "failed_shots": len(failed_motion_budgets),
            }
        )
    else:
        checks.append({"name": "motion_density", "status": "ok"})

    if int(settings.fps_render) < 2:
        warnings.append(
            "Raw video-model rendering below 2 FPS will look stepped and is not suitable for a full-motion acceptance render."
        )
        checks.append({"name": "raw_fps", "status": "warn", "minimum_recommended": 2})
    else:
        checks.append({"name": "raw_fps", "status": "ok"})

    interpolation_engine = str(settings.interpolation_engine or "auto").strip().lower()
    if interpolation_engine == "fps" and int(settings.fps_output) > int(settings.fps_render):
        warnings.append(
            "The FPS interpolation option duplicates frames; it raises the container frame rate but does not create motion. Use Auto, minterpolate, or configured RIFE for motion interpolation."
        )
        checks.append({"name": "interpolation", "status": "warn", "creates_motion": False})
    else:
        checks.append({"name": "interpolation", "status": "ok"})

    if int(settings.width) % 8 != 0 or int(settings.height) % 8 != 0:
        warnings.append(
            f"Internal video models expect dimensions divisible by 8; requested {int(settings.width)}x{int(settings.height)} may fail or force a resize."
        )
        checks.append({"name": "dimensions", "status": "warn"})
    else:
        checks.append({"name": "dimensions", "status": "ok"})

    if engine == "svd" and int(settings.video_model_max_frames_per_scene or 25) > 25:
        warnings.append("SVD supports short image-to-video windows; Studio will cap generated adapter frames to 25 per scene.")
        checks.append({"name": "frame_count", "status": "warn", "cap": 25})
    elif engine == "animatediff" and int(settings.video_model_max_frames_per_scene or 25) > 32:
        warnings.append("AnimateDiff works best with shorter context windows; consider 16-32 generated frames per scene before scaling up.")
        checks.append({"name": "frame_count", "status": "warn", "recommended_max": 32})
    elif engine == "hunyuan_video15" and int(settings.video_model_max_frames_per_scene or 25) > 61:
        warnings.append("HunyuanVideo-1.5 uses a bounded temporal window; Studio will cap the adapter request to 61 frames.")
        checks.append({"name": "frame_count", "status": "warn", "cap": 61})
    else:
        checks.append({"name": "frame_count", "status": "ok"})

    dtype = str(settings.video_model_dtype or "auto").strip().lower()
    if backend == "cpu" and dtype in {"float16", "bfloat16"}:
        warnings.append("float16/bfloat16 video-model precision is a GPU setting; CPU runs should use auto or float32.")
        checks.append({"name": "dtype", "status": "warn"})
    else:
        checks.append({"name": "dtype", "status": "ok"})

    if (
        backend == "cuda"
        and engine in {"animatediff", "hunyuan_video15"}
        and vram_gb
        and vram_gb <= 8.5
        and not bool(settings.video_model_cpu_offload)
    ):
        label = "HunyuanVideo-1.5" if engine == "hunyuan_video15" else "AnimateDiff"
        warnings.append(f"{label} on low-VRAM CUDA should use CPU offload before rendering.")
        checks.append({"name": "offload", "status": "warn"})
    else:
        checks.append({"name": "offload", "status": "ok"})

    if keyframe_renderer == "tensorrt_sd15":
        checks.append({"name": "keyframe_renderer", "status": "ok", "renderer": "tensorrt_sd15"})
        if engine == "animatediff":
            warnings.append("TensorRT SD1.5 anchors can guide AnimateDiff shot blending, but AnimateDiff is still text-to-video and still loads its SD1.5 Diffusers base.")
    else:
        checks.append({"name": "keyframe_renderer", "status": "ok", "renderer": "internal"})

    scene_scores = video_model_scene_motion_scores(
        scenes=scenes,
        timeline=timeline,
        settings=settings,
        duration_s=duration_s,
    )
    storyboard_motion_plan = describe_storyboard_motion_plan(
        scenes=scenes,
        timeline=timeline,
        settings=settings,
        duration_s=duration_s,
    )

    return {
        "engine": engine,
        "motion_score_mode": mode,
        "manual_motion_score": _clamp_video_motion_score(settings.video_model_manual_motion_score),
        "anchor_mode": anchor_mode,
        "prompt_refine": bool(settings.video_model_prompt_refine),
        "scene_motion": normalize_video_model_scene_motion(settings.video_model_scene_motion),
        "keyframe_renderer": keyframe_renderer,
        "keyframe_model_id": (
            settings.video_model_keyframe_model_id or "local_sd15_tensorrt_bundle"
            if keyframe_renderer == "tensorrt_sd15"
            else None
        ),
        "motion_strategy": normalize_internal_motion_strategy(settings.motion_strategy),
        "storyboard_motion_plan": storyboard_motion_plan,
        "total_frames": int(total_frames),
        "max_frames_per_scene": int(settings.video_model_max_frames_per_scene or 25),
        "effective_native_frame_cap": int(effective_native_cap),
        "motion_validation_required": True,
        "motion_frame_budgets": motion_frame_budgets,
        "effective_interpolation_engine": interpolation_engine,
        "scene_scores": scene_scores,
        "checks": checks,
        "warnings": warnings,
    }


def _key_image_at_time(
    key_imgs: dict[float, Image.Image],
    key_times: list[float],
    t: float,
    *,
    width: int,
    height: int,
) -> Image.Image:
    a_t, b_t, w = _key_times_bracket(key_times, t)
    img = key_imgs[a_t].convert("RGB")
    if a_t != b_t:
        img = Image.blend(img, key_imgs[b_t].convert("RGB"), float(w))
    return img.resize((int(width), int(height)), resample=Image.LANCZOS)


def _video_anchor_images(
    *,
    key_imgs: dict[float, Image.Image],
    key_times: list[float],
    start_s: float,
    end_s: float,
    duration_s: float,
    fps_render: int,
    width: int,
    height: int,
) -> tuple[Image.Image, Image.Image]:
    start_img = _key_image_at_time(key_imgs, key_times, float(start_s), width=width, height=height)
    end_probe = min(float(duration_s), max(float(start_s), float(end_s) - (1.0 / max(1, int(fps_render)))))
    end_img = _key_image_at_time(key_imgs, key_times, end_probe, width=width, height=height)
    return start_img, end_img


def _apply_video_anchor_frames(
    frames: list[Image.Image],
    *,
    anchor_mode: str,
    start_img: Image.Image,
    end_img: Image.Image,
    anchor_strength: float,
) -> list[Image.Image]:
    if not frames:
        return frames
    mode = _normalize_video_anchor_mode(anchor_mode)
    out = [frame.convert("RGB") for frame in frames]
    edge = max(1, min(8, max(1, len(out) // 4)))
    blend_max = max(0.20, min(0.85, 0.35 + float(anchor_strength) * 0.5))
    start = start_img.convert("RGB").resize(out[0].size, resample=Image.LANCZOS)
    tail_target = start if mode == "loop" else end_img.convert("RGB").resize(out[-1].size, resample=Image.LANCZOS)

    if mode in {"start", "both", "loop"}:
        for i in range(edge):
            alpha = blend_max * (1.0 - (i / max(1, edge)))
            out[i] = Image.blend(out[i], start, alpha)
    if mode in {"end", "both", "loop"}:
        for i in range(edge):
            idx = len(out) - 1 - i
            alpha = blend_max * (1.0 - (i / max(1, edge)))
            out[idx] = Image.blend(out[idx], tail_target, alpha)
    return out


def render_internal_diffusion_preview_segment(
    *,
    ffmpeg_path: str,
    project_dir: Path,
    scenes: list[dict[str, Any]],
    model_dir: Path,
    settings: InternalVideoSettings,
    timeline: dict[str, Any] | None,
    start_s: float,
    end_s: float,
    fps: int,
    out_mp4: Path,
    prompt_override: str | None = None,
    seed: int | None = None,
    force: bool = False,
    log_fn=None,
) -> Path:
    """Render a short cached diffusion preview clip (low-res, low steps).

    Intended for quick "look" checks inside the Timeline page. This is NOT a full render:
      - capped duration
      - no audio mux
      - low FPS and low steps by default (caller should set settings.steps/settings.cfg)

    Cache keys and directories are managed by the caller (backend endpoint).
    """
    _require_pillow()

    start = max(0.0, float(start_s))
    end = max(start + 0.05, float(end_s))
    # protect the machine: keep previews short
    end = min(end, start + 10.0)
    fps_i = max(1, min(12, int(fps)))

    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    if out_mp4.exists() and not force:
        return out_mp4

    # tmp frames directory
    frames_dir = out_mp4.parent / f"_tmp_{out_mp4.stem}"
    if frames_dir.exists():
        try:
            for f in frames_dir.glob("*.png"):
                f.unlink(missing_ok=True)
        except Exception:
            pass
    frames_dir.mkdir(parents=True, exist_ok=True)

    device = _device_auto(settings.device_preference)
    pipes = _try_load_pipelines(model_dir, device=device)

    try:
        import torch  # type: ignore
    except Exception:
        torch = None  # type: ignore

    # Stable seed for repeatable previews
    base_seed = int(seed) if seed is not None else int(settings.seed or 1337)
    gen = None
    try:
        if torch is not None:
            gen = torch.Generator(device=device).manual_seed(base_seed)
    except Exception:
        gen = None

    # Render frames
    n = int(math.ceil((end - start) * fps_i))
    prev_img = None
    fps_schedule = max(1, int(settings.fps_output))
    deforum_context = _build_unified_deforum_context(
        scenes=scenes,
        timeline=timeline,
        variant=None,
        settings=settings,
        fps=fps_schedule,
    )

    # Limit preview cost even if user set aggressive settings
    steps = max(1, min(int(settings.steps), 30))
    cfg = float(settings.cfg)

    for i in range(n):
        t = start + (i / fps_i)
        schedule_frame = int(round(float(t) * float(fps_schedule)))

        prompt = (prompt_override or "").strip()
        if not prompt:
            prompt = _prompt_text_for_frame(
                frame_idx=schedule_frame,
                scenes=scenes,
                timeline=timeline,
                deforum_context=deforum_context,
                fps=fps_schedule,
            )
        neg = _negative_prompt_for_frame(frame_idx=schedule_frame, settings=settings, deforum_context=deforum_context)

        # camera motion (camera keyframes -> motion track -> fallback)
        comp = _camera_components_at_time(
            t,
            timeline=timeline,
            fallback_interval_s=settings.keyframe_interval_s,
            deforum_motion=deforum_context.motion,
            fps=fps_schedule,
        )

        # low-cost temporal continuity
        use_img2img = (settings.temporal_mode or "").lower() == "frame_img2img" and prev_img is not None
        strength = float(settings.temporal_strength if use_img2img else 1.0)

        try:
            if use_img2img:
                # img2img path
                img = pipes.img2img(
                    prompt=prompt,
                    negative_prompt=neg,
                    image=prev_img,
                    strength=strength,
                    guidance_scale=cfg,
                    num_inference_steps=steps,
                    generator=gen,
                ).images[0]
            else:
                img = pipes.txt2img(
                    prompt=prompt,
                    negative_prompt=neg,
                    width=int(settings.width),
                    height=int(settings.height),
                    guidance_scale=cfg,
                    num_inference_steps=steps,
                    generator=gen,
                ).images[0]
        except Exception as e:
            raise UserFacingError(
                "Diffusion preview failed",
                hint=f"Try lower resolution/steps, or switch internal model. Error: {e}",
                code="DIFF_PREVIEW",
                status_code=500,
            ) from e

        # Apply camera transform and overlays at absolute time t
        try:
            fr = _apply_camera_components_absolute(img, int(settings.width), int(settings.height), comp)
        except Exception:
            fr = img

        try:
            fr = apply_timeline_layers(fr, project_dir=project_dir, timeline=(timeline or {}), t=float(t))
        except Exception:
            pass

        fr.save(frames_dir / f"frame_{i:06d}.png")
        prev_img = img

        if log_fn and i % max(1, fps_i * 2) == 0:
            log_fn(f"Diffusion preview frame {i+1}/{n}")

    assemble_image_sequence(
        ffmpeg_path=ffmpeg_path,
        frames_dir=frames_dir,
        out_mp4=out_mp4,
        fps=fps_i,
        glob_pattern="frame_*.png",
        audio_path=None,
    )

    # cleanup
    try:
        for f in frames_dir.glob("*.png"):
            f.unlink(missing_ok=True)
        frames_dir.rmdir()
    except Exception:
        pass

    return out_mp4
