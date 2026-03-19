from __future__ import annotations
from pydantic import BaseModel, Field
from typing import Any, Literal

class HealthResponse(BaseModel):
    ok: bool = True
    version: str = "1.1.0"

class ProjectCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)

class PlanRequest(BaseModel):
    title: str | None = None
    user_notes: str | None = None
    style_prefs: str | None = None
    num_variants: int = Field(default=3, ge=1, le=10)
    max_scenes: int = Field(default=12, ge=1, le=64)

class ApplyPlanRequest(BaseModel):
    variant_index: int = 0
    overwrite: bool = False

class RenderScenesRequest(BaseModel):
    """Render one still image per scene."""
    variant_index: int = 0
    checkpoint: str | None = None  # optional checkpoint filename for ComfyUI
    width: int = 1024
    height: int = 576
    steps: int = 28
    cfg: float = 7.0
    sampler: str = "euler"
    negative_prompt: str = "blurry, low quality, watermark, text, logo"

MotionEngine = Literal["animatediff","svd"]

class RenderMotionRequest(BaseModel):
    """Render motion clips per scene via ComfyUI (AnimateDiff or SVD)."""
    variant_index: int = 0
    checkpoint: str | None = None  # optional base checkpoint filename for ComfyUI
    engine: MotionEngine = "animatediff"

    # Output / timeline
    fps: int = Field(default=12, ge=1, le=60)
    max_frames_per_scene: int = Field(default=240, ge=1, le=4000)  # cap long scenes by default

    # Base SD render settings (for prompts / keyframes)
    width: int = 768
    height: int = 432
    steps: int = 24
    cfg: float = 6.5
    sampler: str = "euler"
    negative_prompt: str = "blurry, low quality, watermark, text, logo"

    # AnimateDiff Evolved settings
    motion_model_name: str = "mm_sd_v15_v2.ckpt"
    context_length: int = 16
    context_overlap: int = 4
    beta_schedule: str = "autoselect"

    # SVD settings (only used if engine == 'svd')
    svd_checkpoint: str = "svd_xt.safetensors"
    svd_num_steps: int = 25
    svd_motion_bucket_id: int = 127
    svd_fps_id: int = 6
    svd_cond_aug: float = 0.02
    svd_decoding_t: int = 14
    device: Literal["cuda","cpu"] = "cuda"

class AssembleVideoRequest(BaseModel):
    variant_index: int = 0
    fps: int = 30

class InternalVideoRenderRequest(BaseModel):
    """Render a full video using the internal renderer.

    Modes:
      - auto: prefer diffusion if an internal model is installed, otherwise fall back to proxy
      - diffusion: require an internal diffusion model
      - proxy: render a local draft video using timeline compositing only
    """
    variant_index: int = 0

    fps_output: int = Field(default=24, ge=1, le=60)
    fps_render: int = Field(default=2, ge=1, le=30)
    width: int = Field(default=768, ge=256, le=1920)
    height: int = Field(default=432, ge=256, le=1080)

    steps: int = Field(default=15, ge=1, le=80)
    cfg: float = Field(default=7.0, ge=1.0, le=20.0)
    keyframe_interval_s: float = Field(default=5.0, ge=0.5, le=60.0)

    interpolation_engine: Literal["auto","minterpolate","fps","rife"] = "auto"
    model_id: str = "auto"
    render_mode: Literal["auto","diffusion","proxy"] = "auto"
    render_tier: Literal["auto","draft","balanced","quality"] = "auto"
    device_preference: Literal["auto","cpu","cuda","mps"] = "auto"
    allow_proxy_fallback: bool = True
    negative_prompt: str = "blurry, low quality, watermark, text, logo"

    temporal_mode: Literal["off","keyframes","frame_img2img"] = "frame_img2img"
    temporal_strength: float = Field(default=0.35, ge=0.01, le=0.99)
    temporal_steps: int | None = Field(default=None, ge=1, le=80)
    refine_every_n_frames: int = Field(default=1, ge=1, le=30)
    anchor_strength: float = Field(default=0.20, ge=0.0, le=1.0)
    prompt_blend: bool = True
    resume_existing_frames: bool = True

class TimelineUpdateRequest(BaseModel):
    timeline: dict[str, Any] = Field(default_factory=dict)

class ExportDeforumRequest(BaseModel):
    variant_index: int = 0
    fps: int = 30
    width: int = 1024
    height: int = 576

class CloudAwsTestRequest(BaseModel):
    bucket: str | None = None

class CloudAwsBundleRequest(BaseModel):
    bucket: str | None = None
    key: str | None = None

class CloudLightningBundleRequest(BaseModel):
    output_dir: str = "lightning_bundle"
