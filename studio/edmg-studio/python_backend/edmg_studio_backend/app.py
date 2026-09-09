from __future__ import annotations

from .cuda_dll_path import prepare_cuda_dll_path

prepare_cuda_dll_path()

import os
import asyncio
import platform
import mimetypes
import time
import zipfile
import json
import hashlib
import logging
import shutil
import subprocess
import sys
import threading
import wave
from copy import deepcopy
from dataclasses import replace
import math
import re
from contextlib import asynccontextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi import Request

try:
    import python_multipart as _multipart  # type: ignore
    HAS_MULTIPART = True
except Exception:
    try:
        import multipart as _multipart  # type: ignore
        HAS_MULTIPART = True
    except Exception:
        _multipart = None
        HAS_MULTIPART = False

try:
    from PIL import Image, ImageFilter, ImageOps  # type: ignore
except Exception:
    Image = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageOps = None  # type: ignore

from .config import Settings
from .schemas import (
    HealthResponse, ProjectCreateRequest, PlanRequest, ApplyPlanRequest,
    RenderScenesRequest, RenderMotionRequest, AssembleVideoRequest, InternalVideoRenderRequest,
    CreativeDirectionApplyRequest, PlannerLabImportRequest, ReactiveLabApplyRequest, ExportDeforumRequest, ExportUnrealBridgeRequest,
    ImportUnrealBridgeReturnRequest,
    BuildUnrealImportPlanRequest,
    StoryboardVariantUpdateRequest,
    CloudAwsTestRequest, CloudAwsBundleRequest, CloudAzureTestRequest, CloudHfBucketTestRequest, CloudHfBucketSettingsRequest, CloudLightningBundleRequest,
    ProjectSnapshot, RenderConductorPlanRequest, RenderConductorPromoteRequest, PerformerWorkflowPlanRequest, PerformerWorkflowRunRequest, RenderIntent, VisualDNAFeedbackRequest,
    VisualDNAUpdateRequest,
    UnrealBridgePreviewResponse,
    AutoAnimateRequest,
    ParseqMotionApplyRequest,
    LayeredAnimateRequest,
    TensorRTStandaloneRenderRequest,
    TimelineRenderRequest,
)
from .services import animation_autoconfig as autoconfig
from .services import layer_animation as layeranim
from .services import parseq_adapter
from .store.projects import ProjectStore
from .version import STUDIO_VERSION
from .store.jobs import JobStore
from .store.artifacts import write_artifact_manifest
from .api import create_models_router, create_project_router, create_system_router
from .domain.director_modes import (
    director_mode_profile,
    flavor_prompt,
    list_director_modes,
    normalize_director_mode,
    reactive_preset_for_mode,
)
from .domain.director_readiness import (
    HIGH_TIER_DIRECTOR_MODEL_ID,
    HUNYUAN_MODEL_ID,
    LTX_MODEL_ID,
    STANDARD_DIRECTOR_MODEL_ID,
    resolve_director_readiness,
)
from .services.ai_client import build_ai_client
from .services.edmg_core import (
    core_status,
    deforum_template as edmg_deforum_template,
    install_core as edmg_install_core,
    selfcheck as edmg_selfcheck,
)
from .integrations import comfyui as comfy
from .integrations.comfyui_pool import ComfyUINodePool
from .services.worker_manager import WorkerManager
from .services.hf_auth import describe_hf_auth
from .services.ffmpeg import (
    TimelineRenderCanceled,
    _probe_duration_seconds,
    assemble_image_sequence,
    assemble_slideshow,
    build_timeline_render_command,
    concat_videos,
    interpolate_video_fps,
    mux_audio,
    prepare_timeline_render_plan,
    render_timeline_edited_master,
)
from .services.safe_audio_analysis import SafeAudioAnalysisError, analyze_audio_ffmpeg_numpy
from .services.internal_video import (
    InternalVideoSettings,
    _scene_keyframe_times,
    describe_internal_render_cache,
    describe_internal_video_model_preflight,
    normalize_internal_motion_strategy,
    normalize_keyframe_continuity_mode,
    normalize_video_model_keyframe_renderer,
    normalize_video_model_scene_motion,
    release_cached_internal_pipelines,
    render_internal_still_image,
    render_internal_video_variant,
    render_stability_hosted_video_variant,
    render_internal_diffusion_preview_segment,
)
from .services.deforum_normalize import (
    CLIP_SAFE_RENDER_PROMPT_MAX_WORDS,
    DEFAULT_NEGATIVE_PROMPT,
    DEFAULT_RENDER_PROMPT,
    build_deforum_render_context,
    negative_prompt_from_scene,
    operational_render_prompt_from_scene,
    render_prompt_from_scene,
)
from .services.deforum_prompt_timeline import resolve_prompt_frame
from .services import internal_video_models
from .services.codex_sdk_bridge import codex_sdk_status, run_render_review as run_codex_render_review_task
from .services.tensorrt_video import render_tensorrt_video_variant
from .services.compositor import apply_timeline_layers
from .integrations import aws as aws_integration
from .integrations import azure as azure_integration
from .integrations import hf_bucket as hf_bucket_integration
from .integrations import lightning as lightning_integration
from .utils.path import safe_join
from .errors import UserFacingError, hint_from_exception
from .security import BackendSecurityMiddleware, BackendSecuritySettings
from .services.model_manager import ModelManager
from .services.secrets import SecretStore
from .services.model_cache_settings import ModelCacheSettingsStore
from .services.render_settings import (
    RenderSettingsStore,
    FIREFLY_CONTENT_CLASSES,
    FIREFLY_STYLES,
    STABILITY_SD3_MODELS,
    STABILITY_SERVICES,
    STABILITY_STYLE_PRESETS,
    VIDEO_GENERATION_PREFERENCES,
)
from .services.firefly_platform import FireflyClient, FireflyImageResult, FireflyVideoResult
from .services.imagineart_platform import (
    IMAGINEART_ASPECT_RATIOS,
    IMAGINEART_IMAGE_STYLES,
    IMAGINEART_VIDEO_STYLES,
    ImagineArtClient,
)
from .services.cosmos_platform import CosmosClient, COSMOS_MODELS, _COSMOS3_SHAPES
from .services.azure_foundry_platform import AzureFoundryClient
from .services.transcription_settings import (
    PARAKEET_MODELS,
    PARAKEET_NIM_MODELS,
    TRANSCRIPTION_COMPUTE_TYPES,
    TRANSCRIPTION_DEVICES,
    TRANSCRIPTION_PROVIDERS,
    WHISPER_MODELS,
    TranscriptionSettingsStore,
    transcription_dependency_status,
)
from .services.workbench_bridge import (
    build_unreal_bridge_export_payloads,
    build_unreal_bridge_preview,
    merge_reactive_lab_into_timeline,
    planner_lab_to_canonical_plan,
    planner_lab_to_project_analysis,
)
from .services.core_capabilities import (
    apply_core_style_direction,
    development_timing,
    enrich_with_multitrack_defaults,
)
from .services.unreal_bridge_consumer import (
    build_unreal_sequence_import_plan,
    write_unreal_sequence_import_plan,
)
from .services.visual_dna import (
    build_prompt_hints as build_visual_dna_prompt_hints,
    ingest_planner_payload as ingest_visual_dna_planner_payload,
    ingest_reactive_payload as ingest_visual_dna_reactive_payload,
    load_visual_dna,
    record_render_feedback as record_visual_dna_feedback,
    save_visual_dna,
    trait_id as visual_dna_trait_id,
    update_visual_dna,
)
from .render_conductor.planner import (
    NoRealRenderRouteError,
    build_advisory_render_plan,
    promote_proxy_sections,
)
from .domain.music_graph import music_graph_from_analysis
from .domain.performer_workflow import build_performer_workflow_plan
from .services.setup_wizard import (
    SetupTaskManager,
    check_backend_bundle,
    check_ollama,
    download_and_install_ollama,
    pull_ollama_model,
    download_and_extract_portable,
    ComfyPortableProcess,
    OllamaManagedProcess,
    check_ffmpeg,
    comfy_portable_installed,
    comfy_portable_root,
    download_and_install_7zip,
    install_backend_bundle,
    _find_ollama_exe,
    _find_7z_exe,
    managed_ollama_launch_script_path,
    resolve_setup_accelerator_profile,
)
from .services.system_readiness import assess_system_readiness
from .services.baseline_metrics import collect_baseline_metrics
from .services.project_health import assess_project_health, collect_project_bundle, suggest_relinks
from .uv_toolchain import ToolchainError

logger = logging.getLogger(__name__)
settings = Settings()


class JobCanceled(Exception):
    """Raised when a running job is canceled and should stop promptly."""


settings.data_dir.mkdir(parents=True, exist_ok=True)
settings.models_dir.mkdir(parents=True, exist_ok=True)
settings.cache_dir.mkdir(parents=True, exist_ok=True)
settings.logs_dir.mkdir(parents=True, exist_ok=True)
settings.external_dir.mkdir(parents=True, exist_ok=True)
settings.ollama_models_dir.mkdir(parents=True, exist_ok=True)

store = ProjectStore(settings.data_dir)
jobs = JobStore(store.projects_dir)

# Multi-node ComfyUI pool (supports EDMG_COMFYUI_URLS)
comfy_pool = ComfyUINodePool(settings.load_comfyui_nodes(), default_max_inflight=settings.comfyui_node_concurrency)

# Always-on worker manager
worker = None  # set after _execute_job is defined
ai = build_ai_client(settings.ai_mode, settings.ai_base_url, settings.ai_timeout_s)

setup_tasks = SetupTaskManager()
secrets = SecretStore(settings.data_dir)
render_settings = RenderSettingsStore(settings.data_dir)
transcription_settings = TranscriptionSettingsStore(settings.data_dir)
# Project the persisted model-cache choice onto the environment before the
# ModelManager resolves its cache, so the UI-selected Hugging Face bucket (the
# preferred provider over S3/Azure) activates on startup. force=False lets an
# explicit launcher env var still win.
model_cache_settings = ModelCacheSettingsStore(settings.data_dir)
model_cache_settings.apply_to_env(force=False)
models = ModelManager(
    settings.data_dir,
    settings.models_dir,
    settings.external_dir,
    settings.comfyui_url,
    os.getenv('EDMG_AI_OLLAMA_URL','http://127.0.0.1:11434'),
    secrets=secrets,
)

comfy_portable = ComfyPortableProcess()
ollama_managed = OllamaManagedProcess()

# Apply CUDA performance flags at process startup so they take effect for
# every pipeline load without needing to re-read settings on each call.
def _apply_cuda_startup_flags() -> None:
    try:
        os.environ.setdefault("PYTORCH_CUDA_ALLOC_CONF", "expandable_segments:True")
        import torch  # type: ignore
        if not (getattr(torch, "cuda", None) and torch.cuda.is_available()):
            return
        cuda_cfg = dict((render_settings.get().get("cuda") or {}))
        if bool(cuda_cfg.get("enable_tf32", True)):
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True
        torch.backends.cudnn.benchmark = True
    except Exception:
        pass

_apply_cuda_startup_flags()

_ALLOWED_SECRETS: frozenset[str] = frozenset({
    "hf_token", "civitai_api_key", "openai_compat_api_key",
    "stability_api_key", "nvidia_api_key", "nvidia_service_key", "lightning_api_key",
    "adobe_client_id", "adobe_client_secret", "imagineart_api_key", "azure_foundry_api_key",
})

def _install_benign_connection_error_filter(loop: asyncio.AbstractEventLoop) -> None:
    """Silence benign client-disconnect tracebacks on the event loop.

    On Windows (ProactorEventLoop), a browser aborting an HTTP response
    mid-stream — very common with ``<video>`` range requests when the user
    seeks, pauses, or navigates away — surfaces as a noisy
    ``ConnectionResetError: [WinError 10054]`` traceback. It is harmless, so we
    swallow only connection-reset style errors and defer everything else to the
    default handler.
    """
    previous = loop.get_exception_handler()

    def _handler(loop_: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if isinstance(exc, (ConnectionResetError, ConnectionAbortedError, BrokenPipeError)):
            return
        if previous is not None:
            previous(loop_, context)
        else:
            loop_.default_exception_handler(context)

    loop.set_exception_handler(_handler)


@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    try:
        _install_benign_connection_error_filter(asyncio.get_running_loop())
    except Exception:
        pass
    if settings.worker_autostart:
        worker.start()
    try:
        yield
    finally:
        try:
            worker.stop()
        except Exception:
            pass


backend_security = BackendSecuritySettings.from_env()

app = FastAPI(title="EDMG Studio Backend", version=STUDIO_VERSION, lifespan=_app_lifespan)
from .revisions import RevisionRoute
app.router.route_class = RevisionRoute
from .api.media import create_media_router, validate_preview, validate_timeline_media
app.include_router(create_media_router(lambda: store, backend_security))
from .api.planner_schedule import create_schedule_router
from .domain.planner_schedule import attach_schedule_drafts
app.include_router(create_schedule_router(lambda: store))
from .api.editor import create_editor_router
app.include_router(create_editor_router(lambda: store))
from .api.director import create_director_router
from .api.director_workflow import create_workflow_router

app.include_router(create_workflow_router(lambda: store, lambda project: _workspace_audio_plan(project)))
app.include_router(
    create_director_router(
        lambda: store,
        lambda: jobs,
        lambda: models,
        # The callback resolves after module initialization, so the router can
        # be registered beside the other project services without duplicating
        # hardware detection logic.
        lambda: _hardware_profile(),
    )
)

app.add_middleware(BackendSecurityMiddleware, settings=backend_security)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(backend_security.cors_origins),
    allow_origin_regex=backend_security.cors_origin_regex,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["Accept-Ranges", "Content-Length", "Content-Range"],
)


@app.exception_handler(UserFacingError)
async def _user_facing_error(_req: Request, exc: UserFacingError):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.to_dict()})


@app.exception_handler(HTTPException)
async def _http_exception(_req: Request, exc: HTTPException):
    if isinstance(exc.detail, dict):
        return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.detail}, headers=exc.headers)
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    hint = hint_from_exception(Exception(msg))
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": {"message": msg, "hint": hint, "code": "HTTP_ERROR"}})


@app.exception_handler(Exception)
async def _unhandled_exception(_req: Request, exc: Exception):
    logger.error(
        "Unhandled backend request error",
        exc_info=(type(exc), exc, exc.__traceback__),
    )
    return JSONResponse(
        status_code=500,
        content={
            "ok": False,
            "error": {
                "message": "Internal server error",
                "hint": "Open Render Queue → Log for details, then retry.",
                "code": "INTERNAL",
            },
        },
    )


def _require_multipart() -> None:
    if not HAS_MULTIPART:
        raise UserFacingError(
            "File upload support is unavailable because python-multipart is not installed.",
            hint=(
                "Run the source launcher (or a frozen uv sync for one accelerator profile) "
                "to restore `python-multipart`, then restart EDMG Studio."
            ),
            code="MISSING_MULTIPART",
            status_code=503,
        )


_WINDOWS_RESERVED_FILENAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{index}" for index in range(1, 10)),
    *(f"LPT{index}" for index in range(1, 10)),
}


def _safe_upload_filename(filename: str | None, fallback: str) -> str:
    """Reduce an untrusted upload name to one portable basename."""
    basename = str(filename or "").replace("\\", "/").rsplit("/", 1)[-1]
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", basename).strip(" ._")
    if not cleaned:
        cleaned = fallback
    suffix = Path(cleaned).suffix[:16]
    stem = Path(cleaned).stem[:96].rstrip(" ._") or Path(fallback).stem
    # Windows treats the portion before the first dot as the DOS device name,
    # so names such as ``CON.preview.png`` are reserved too.
    device_stem = stem.split(".", 1)[0].upper()
    if device_stem in _WINDOWS_RESERVED_FILENAMES:
        stem = f"_{stem}"
    return f"{stem}{suffix}"[:112]


def _cache_key_token(value: str) -> str:
    """Map untrusted labels to a fixed-width, filename-safe cache token."""
    return hashlib.sha256(str(value).encode("utf-8", errors="ignore")).hexdigest()[:16]


def _stable_seed(project_id: str, variant_index: int, scene_index: int) -> int:
    h = hashlib.md5(f"{project_id}:{variant_index}:{scene_index}".encode("utf-8")).hexdigest()[:8]
    return int(h, 16)


def _positive_float(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number) or number <= 0:
        return None
    return number


def _analysis_duration_s(analysis: Any) -> float | None:
    if not isinstance(analysis, dict):
        return None
    features = analysis.get("features") if isinstance(analysis.get("features"), dict) else {}
    candidates = (
        analysis.get("duration_s"),
        analysis.get("duration"),
        features.get("duration_s"),
        features.get("duration"),
    )
    for candidate in candidates:
        value = _positive_float(candidate)
        if value is not None:
            return value
    return None


def _duration_source(source: str, value: Any) -> dict[str, Any] | None:
    duration_s = _positive_float(value)
    if duration_s is None:
        return None
    return {"source": source, "duration_s": float(duration_s)}


def _append_duration_source(sources: list[dict[str, Any]], source: str, value: Any) -> None:
    item = _duration_source(source, value)
    if item:
        sources.append(item)


def _scenes_duration_s(scenes: Any) -> float | None:
    if not isinstance(scenes, list):
        return None
    values: list[float] = []
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        value = _positive_float(scene.get("end_s") or scene.get("end"))
        if value is not None:
            values.append(value)
    return max(values) if values else None


def _reactive_payload_duration_s(payload: Any) -> float | None:
    if not isinstance(payload, dict):
        return None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    sections = payload.get("sections") if isinstance(payload.get("sections"), list) else []
    keyframes = payload.get("keyframes") if isinstance(payload.get("keyframes"), list) else []
    cue_events = payload.get("cue_events") if isinstance(payload.get("cue_events"), list) else []

    candidates: list[float] = []
    for key in ("duration_s", "duration", "durationSeconds", "duration_seconds"):
        value = _positive_float(metadata.get(key))
        if value is not None:
            candidates.append(value)

    total_frames = _positive_float(
        metadata.get("totalFrames")
        or metadata.get("total_frames")
        or metadata.get("frameCount")
        or metadata.get("frame_count")
    )
    fps = _positive_float(metadata.get("fps") or metadata.get("fps_output") or metadata.get("frameRate"))
    if total_frames is not None and fps is not None:
        candidates.append(total_frames / fps)

    for section in sections:
        if not isinstance(section, dict):
            continue
        value = _positive_float(section.get("endTime") or section.get("end_s") or section.get("end"))
        if value is not None:
            candidates.append(value)

    for frame in keyframes:
        if not isinstance(frame, dict):
            continue
        value = _positive_float(frame.get("time") or frame.get("t"))
        if value is not None:
            candidates.append(value)

    for cue in cue_events:
        if not isinstance(cue, dict):
            continue
        value = _positive_float(cue.get("time") or cue.get("t"))
        if value is not None:
            candidates.append(value)

    return max(candidates) if candidates else None


def _timeline_duration_s(timeline: Any) -> float | None:
    if not isinstance(timeline, dict):
        return None
    candidates: list[float] = []
    for key in ("duration_s", "duration"):
        value = _positive_float(timeline.get(key))
        if value is not None:
            candidates.append(value)

    render = timeline.get("render") if isinstance(timeline.get("render"), dict) else {}
    for key in ("duration_s", "duration"):
        value = _positive_float(render.get(key))
        if value is not None:
            candidates.append(value)

    tracks = timeline.get("tracks") if isinstance(timeline.get("tracks"), list) else []
    for track in tracks:
        if not isinstance(track, dict):
            continue
        clips = track.get("clips") if isinstance(track.get("clips"), list) else []
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            value = _positive_float(clip.get("end_s") or clip.get("end"))
            if value is not None:
                candidates.append(value)

    camera = timeline.get("camera") if isinstance(timeline.get("camera"), dict) else {}
    keyframes = camera.get("keyframes") if isinstance(camera.get("keyframes"), list) else []
    for keyframe in keyframes:
        if not isinstance(keyframe, dict):
            continue
        value = _positive_float(keyframe.get("t") or keyframe.get("time"))
        if value is not None:
            candidates.append(value)

    reactive_duration = _reactive_payload_duration_s(timeline.get("reactive_lab"))
    if reactive_duration is not None:
        candidates.append(reactive_duration)

    return max(candidates) if candidates else None


def _project_audio_path(proj: Any) -> Path | None:
    meta = getattr(proj, "meta", {}) if proj is not None else {}
    audio_meta = meta.get("audio") if isinstance(meta, dict) and isinstance(meta.get("audio"), dict) else {}
    filename = str(audio_meta.get("filename") or "").strip()
    if not filename:
        return None
    try:
        path = safe_join(store.project_dir(str(proj.id)) / "assets" / "audio", filename)
    except Exception:
        return None
    return path if path.exists() else None


def _audio_file_duration_s(path: Path | None) -> float | None:
    if not path or not path.exists():
        return None
    if path.suffix.lower() == ".wav":
        try:
            with wave.open(str(path), "rb") as handle:
                frames = handle.getnframes()
                rate = handle.getframerate()
            if frames > 0 and rate > 0:
                return float(frames) / float(rate)
        except Exception:
            pass
    try:
        return _probe_duration_seconds(settings.ffmpeg_path, path)
    except Exception:
        return None


def _project_duration_sources(
    proj: Any,
    variant: dict[str, Any] | None = None,
    scenes: list[dict[str, Any]] | None = None,
    *,
    analysis: Any | None = None,
) -> list[dict[str, Any]]:
    meta = getattr(proj, "meta", {}) if proj is not None else {}
    meta = meta if isinstance(meta, dict) else {}
    variant = variant if isinstance(variant, dict) else {}
    scenes = scenes if isinstance(scenes, list) else []
    sources: list[dict[str, Any]] = []

    _append_duration_source(sources, "analysis", _analysis_duration_s(analysis if analysis is not None else meta.get("analysis")))
    _append_duration_source(sources, "plan", variant.get("duration_s") or variant.get("duration"))
    _append_duration_source(sources, "scenes", _scenes_duration_s(scenes))
    _append_duration_source(sources, "timeline", _timeline_duration_s(meta.get("timeline")))
    _append_duration_source(sources, "reactive_lab", _reactive_payload_duration_s(meta.get("last_reactive_lab")))
    _append_duration_source(sources, "audio", _audio_file_duration_s(_project_audio_path(proj)))

    best_by_source: dict[str, dict[str, Any]] = {}
    for item in sources:
        source = str(item.get("source") or "")
        if not source:
            continue
        existing = best_by_source.get(source)
        if existing is None or float(item["duration_s"]) > float(existing["duration_s"]):
            best_by_source[source] = item
    return sorted(best_by_source.values(), key=lambda item: float(item["duration_s"]), reverse=True)


def _project_duration_hint_s(
    proj: Any,
    variant: dict[str, Any] | None = None,
    scenes: list[dict[str, Any]] | None = None,
    *,
    analysis: Any | None = None,
) -> float | None:
    sources = _project_duration_sources(proj, variant, scenes, analysis=analysis)
    if not sources:
        return None
    return float(sources[0]["duration_s"])


def _duration_mismatch_warning(duration_sources: list[dict[str, Any]]) -> str | None:
    if not duration_sources:
        return None
    best = duration_sources[0]
    best_duration = float(best.get("duration_s") or 0.0)
    if best_duration <= 0:
        return None
    planned = [
        float(item.get("duration_s") or 0.0)
        for item in duration_sources
        if str(item.get("source") or "") in {"analysis", "plan", "scenes"}
    ]
    planned_duration = max(planned) if planned else 0.0
    if planned_duration <= 0 or planned_duration >= best_duration - 1.0:
        return None
    best_source = str(best.get("source") or "project")
    return (
        f"Project duration resolved from {best_source} ({best_duration:.1f}s), but the current plan/scenes reach "
        f"only {planned_duration:.1f}s. Regenerate and apply the plan to spread prompts across the full track."
    )


def _resolved_project_duration_s(proj: Any, variant: dict[str, Any], scenes: list[dict[str, Any]]) -> float:
    duration = _project_duration_hint_s(proj, variant, scenes)
    return float(duration or 60.0)

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(ok=True)


@app.get("/v1/security/status")
def backend_security_status(request: Request):
    return backend_security.public_status(
        request_scheme=request.url.scheme,
        request_server_host=(request.scope.get("server") or (None,))[0],
    )


def _request_payload(model: Any) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump()
    legacy = getattr(model, "dict", None)
    if callable(legacy):
        return legacy()
    raise TypeError(f"Object {type(model)!r} is not a supported request model")


_PRIVATE_RENDER_PATH_KEYS = frozenset(
    {
        "base_model_path",
        "bundle_path",
        "model_path",
        "source_path",
        "tensorrt_keyframe_bundle_path",
        "video_model_path",
    }
)
_PRIVATE_RENDER_PATH_SUFFIXES = ("_path", "_paths", "_dir", "_abspath")
_OMIT_PRIVATE_RENDER_VALUE = object()


def _is_private_render_path_key(value: Any) -> bool:
    key = str(value or "").strip().lower()
    return key in _PRIVATE_RENDER_PATH_KEYS or key.endswith(_PRIVATE_RENDER_PATH_SUFFIXES)


def _is_absolute_filesystem_location(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    lowered = candidate.lower()
    return (
        lowered.startswith("file:")
        or candidate.startswith(("/", "\\"))
        or bool(re.match(r"^[A-Za-z]:[\\/]", candidate))
    )


def _without_private_render_paths(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized: dict[Any, Any] = {}
        for key, item in value.items():
            if _is_private_render_path_key(key):
                continue
            public_item = _without_private_render_paths(item)
            if public_item is not _OMIT_PRIVATE_RENDER_VALUE:
                sanitized[key] = public_item
        return sanitized
    if isinstance(value, (list, tuple)):
        sanitized_items = []
        for item in value:
            public_item = _without_private_render_paths(item)
            if public_item is not _OMIT_PRIVATE_RENDER_VALUE:
                sanitized_items.append(public_item)
        return sanitized_items
    if _is_absolute_filesystem_location(value):
        return _OMIT_PRIVATE_RENDER_VALUE
    return deepcopy(value)


def _public_render_preflight(preflight: dict[str, Any]) -> dict[str, Any]:
    """Return preflight evidence without backend-only filesystem locations."""

    sanitized = _without_private_render_paths(preflight)
    return sanitized if isinstance(sanitized, dict) else {}


def _project_variant_for_render(proj: Any, variant_index: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    plan = proj.meta.get("last_plan") if isinstance(getattr(proj, "meta", None), dict) else {}
    variants = plan.get("variants") if isinstance(plan, dict) and isinstance(plan.get("variants"), list) else []
    vi = int(variant_index or 0)
    if variants and 0 <= vi < len(variants) and isinstance(variants[vi], dict):
        variant = variants[vi]
    else:
        fallback = _creative_direction_fallback_variant(proj, vi)
        variant = fallback if isinstance(fallback, dict) else {"index": vi, "scenes": []}
    scenes = [scene for scene in list(variant.get("scenes") or []) if isinstance(scene, dict)]
    return variant, scenes


def _active_parseq_manifest(proj: Any) -> dict[str, Any] | None:
    meta = proj.meta if isinstance(getattr(proj, "meta", None), dict) else {}
    manifest = meta.get("active_parseq_manifest")
    return manifest if isinstance(manifest, dict) else None


def _parseq_payload_enabled(payload: dict[str, Any]) -> bool:
    raw = payload.get("parseq_enabled", True)
    if isinstance(raw, str):
        return raw.strip().lower() not in {"0", "false", "no", "off"}
    return bool(raw)


def _apply_active_parseq_motion(proj: Any, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None]:
    if not _parseq_payload_enabled(payload):
        return payload, None
    manifest = payload.get("parseq_manifest") if isinstance(payload.get("parseq_manifest"), dict) else _active_parseq_manifest(proj)
    if not isinstance(manifest, dict):
        return payload, None
    parsed = parseq_adapter.parseq_manifest_to_internal_overrides(manifest)
    overrides = parsed.get("overrides") if isinstance(parsed.get("overrides"), dict) else {}
    if not overrides:
        return payload, parsed
    merged = dict(payload)
    for key, value in overrides.items():
        merged[key] = value
    merged["_parseq_motion"] = parsed.get("summary") or {}
    merged["_render_recipe_graph"] = parseq_adapter.build_render_recipe_graph(
        manifest=manifest,
        internal_request=merged,
    )
    return merged, parsed


def _catalog_entry(model_id: str | None) -> dict[str, Any] | None:
    if not model_id:
        return None
    catalog_payload = models.catalog()
    all_entries = list(catalog_payload.get("catalog") or []) + list(catalog_payload.get("user") or [])
    return next((e for e in all_entries if isinstance(e, dict) and e.get("id") == model_id), None)


def _catalog_render_metadata(entry: dict[str, Any] | None) -> dict[str, Any]:
    render = (entry or {}).get("render") or {}
    return render if isinstance(render, dict) else {}


def _catalog_entry_engine(entry: dict[str, Any] | None) -> str:
    render = _catalog_render_metadata(entry)
    target = (entry or {}).get("target") or {}
    if not isinstance(target, dict):
        target = {}
    engine = str((entry or {}).get("engine") or render.get("engine") or target.get("engine") or "comfyui").strip().lower()
    return engine or "comfyui"


def _catalog_entry_family(entry: dict[str, Any] | None) -> str | None:
    render = _catalog_render_metadata(entry)
    family = str((entry or {}).get("family") or render.get("family") or "").strip().lower()
    return family or None


def _catalog_supports_workflow(entry: dict[str, Any] | None, workflow_family: str) -> bool:
    family = str(workflow_family or "txt2img").strip().lower()
    if family == "auto":
        family = "txt2img"
    if family == "txt2img":
        return bool((entry or {}).get("supports_txt2img", True))
    if family == "img2img":
        return bool((entry or {}).get("supports_img2img", False))
    if family == "inpaint":
        return bool((entry or {}).get("supports_inpaint", False))
    if family == "outpaint":
        return bool((entry or {}).get("supports_outpaint", False))
    if family == "controlnet":
        return bool((entry or {}).get("supports_controlnet", False))
    return False


def _safe_name_tag(value: str | None, fallback: str = "default") -> str:
    raw = str(value or "").strip()
    if not raw:
        raw = fallback
    tag = re.sub(r"[^a-zA-Z0-9]+", "_", raw).strip("_").lower()
    return tag[:32] or fallback


def _extract_comfy_checkpoint_names(object_info: dict[str, Any] | None) -> list[str]:
    info = object_info or {}
    loader = info.get("CheckpointLoaderSimple")
    if not isinstance(loader, dict):
        return []
    input_info = loader.get("input")
    if not isinstance(input_info, dict):
        return []
    required = input_info.get("required")
    if not isinstance(required, dict):
        return []
    ckpt_field = required.get("ckpt_name")
    if not isinstance(ckpt_field, list) or not ckpt_field:
        return []
    options = ckpt_field[0]
    if not isinstance(options, list):
        return []
    return [str(item).strip() for item in options if str(item).strip()]


def _resolve_comfy_checkpoint_name(
    preferred: str | None,
    *,
    allow_auto_fallback: bool,
) -> tuple[str, str | None]:
    requested = str(preferred or settings.comfyui_checkpoint or "").strip()
    available: list[str] = []
    for url in settings.resolved_comfyui_urls():
        try:
            names = _extract_comfy_checkpoint_names(comfy.get_object_info(url, timeout=2.0))
        except Exception:
            continue
        for name in names:
            if name not in available:
                available.append(name)
    if requested and requested in available:
        return requested, None
    if allow_auto_fallback and available:
        fallback = available[0]
        if fallback != requested:
            return fallback, requested or None
        return fallback, None
    return requested, None


def _catalog_comfy_asset_filename(
    entry: dict[str, Any] | None,
    *,
    folder: str,
    allowed_kinds: set[str] | None = None,
) -> str:
    if not isinstance(entry, dict) or not entry.get("id"):
        return ""
    target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
    engine = str(target.get("engine") or entry.get("engine") or "comfyui").strip().lower()
    target_folder = str(target.get("folder") or "checkpoints").strip().lower()
    if engine != "comfyui" or target_folder != str(folder or "").strip().lower():
        return ""
    asset = models.resolve_comfy_asset(str(entry.get("id") or ""), folder=folder, allowed_kinds=allowed_kinds)
    return str(asset.get("filename") or entry.get("filename") or "").strip()


def _resolve_comfy_still_selection(
    *,
    model_id: str | None,
    checkpoint: str | None,
    workflow_family: str | None,
    controlnet_model: str | None,
    reference_asset: str | None,
    conditioning_mode: str | None,
    controlnet_units: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entry = _catalog_entry(model_id)
    entry_data = entry if isinstance(entry, dict) else {}
    render = _catalog_render_metadata(entry)

    explicit_checkpoint = str(checkpoint or "").strip()
    catalog_checkpoint = str(render.get("checkpoint_name") or entry_data.get("filename") or "").strip()
    if not explicit_checkpoint:
        materialized_checkpoint = _catalog_comfy_asset_filename(
            entry_data,
            folder="checkpoints",
            allowed_kinds={"checkpoint"},
        )
        if materialized_checkpoint:
            catalog_checkpoint = materialized_checkpoint
    chosen_checkpoint, fallback_checkpoint = _resolve_comfy_checkpoint_name(
        explicit_checkpoint or catalog_checkpoint or settings.comfyui_checkpoint,
        allow_auto_fallback=not explicit_checkpoint and not catalog_checkpoint,
    )
    family = str(workflow_family or "auto").strip().lower()
    supported_families = {"auto", "txt2img", "img2img", "inpaint", "outpaint", "controlnet"}
    if family not in supported_families:
        family = "auto"

    control_entry = _catalog_entry(controlnet_model)
    control_entry_data = control_entry if isinstance(control_entry, dict) else {}
    control_render = _catalog_render_metadata(control_entry)
    controlnet_name = str(
        control_render.get("controlnet_name")
        or control_entry_data.get("filename")
        or render.get("controlnet_name")
        or ""
    ).strip()
    if control_entry_data:
        materialized_controlnet = _catalog_comfy_asset_filename(
            control_entry_data,
            folder="controlnet",
            allowed_kinds={"controlnet"},
        )
        if materialized_controlnet:
            controlnet_name = materialized_controlnet
    has_controlnet_units = any(
        isinstance(unit, dict) and str(unit.get("model") or unit.get("controlnet_name") or "").strip()
        for unit in (controlnet_units or [])
    )
    if family == "auto":
        if controlnet_name or reference_asset or has_controlnet_units or str(entry_data.get("kind") or "") == "controlnet":
            family = "controlnet"
        else:
            family = str(render.get("workflow_family") or "txt2img").strip().lower()
    if family not in supported_families - {"auto"}:
        family = "txt2img"

    if family == "controlnet" and not controlnet_name and not has_controlnet_units and str(entry_data.get("kind") or "") == "controlnet":
        controlnet_name = str(entry_data.get("filename") or "")
    if family == "controlnet" and not controlnet_name and not has_controlnet_units:
        raise UserFacingError(
            "No ControlNet model selected",
            hint="Install a Studio ControlNet model in Models, then choose it on the Render page.",
            code="CONTROLNET_MISSING",
            status_code=400,
        )
    if family == "controlnet" and not reference_asset and not has_controlnet_units:
        raise UserFacingError(
            "No reference image selected",
            hint="Upload or pick a project reference image before running a ControlNet still render.",
            code="REFERENCE_IMAGE_MISSING",
            status_code=400,
        )

    return {
        "entry": entry,
        "checkpoint": chosen_checkpoint,
        "checkpoint_fallback_from": fallback_checkpoint,
        "workflow_family": family,
        "controlnet_name": controlnet_name or None,
        "conditioning_mode": str(
            conditioning_mode
            or control_render.get("conditioning_mode")
            or render.get("conditioning_mode")
            or "raw"
        ).strip().lower(),
    }


def _resolve_comfy_motion_selection(
    *,
    model_id: str | None,
    checkpoint: str | None,
    svd_model_id: str | None,
    svd_checkpoint: str | None = None,
) -> dict[str, Any]:
    entry = _catalog_entry(model_id)
    entry_data = entry if isinstance(entry, dict) else {}
    render = _catalog_render_metadata(entry)
    explicit_checkpoint = str(checkpoint or "").strip()
    catalog_checkpoint = str(render.get("checkpoint_name") or entry_data.get("filename") or "").strip()
    if not explicit_checkpoint:
        materialized_checkpoint = _catalog_comfy_asset_filename(
            entry_data,
            folder="checkpoints",
            allowed_kinds={"checkpoint"},
        )
        if materialized_checkpoint:
            catalog_checkpoint = materialized_checkpoint
    base_checkpoint, fallback_checkpoint = _resolve_comfy_checkpoint_name(
        explicit_checkpoint or catalog_checkpoint or settings.comfyui_checkpoint,
        allow_auto_fallback=not explicit_checkpoint and not catalog_checkpoint,
    )

    svd_entry = _catalog_entry(svd_model_id)
    svd_entry_data = svd_entry if isinstance(svd_entry, dict) else {}
    svd_render = _catalog_render_metadata(svd_entry)
    explicit_svd_checkpoint = str(svd_checkpoint or "").strip()
    resolved_svd = str(
        explicit_svd_checkpoint
        or svd_render.get("svd_checkpoint")
        or svd_entry_data.get("filename")
        or "svd_xt.safetensors"
    ).strip()
    if not explicit_svd_checkpoint:
        materialized_svd = _catalog_comfy_asset_filename(
            svd_entry_data,
            folder="checkpoints",
            allowed_kinds={"checkpoint", "motion_module"},
        )
        if materialized_svd:
            resolved_svd = materialized_svd
    return {
        "entry": entry,
        "svd_entry": svd_entry,
        "checkpoint": base_checkpoint,
        "checkpoint_fallback_from": fallback_checkpoint,
        "svd_checkpoint": resolved_svd,
    }


def _resolve_installed_model_path(model_id: str, *, materialize_remote: bool = True) -> Path | None:
    """Resolve an installed model path with backward-compatible fallbacks."""
    installed_path = getattr(models, "installed_path", None)
    if callable(installed_path):
        fallback = installed_path(model_id)
        if fallback:
            return Path(fallback)
        if not materialize_remote:
            return None

    resolver = getattr(models, "resolve_installed_path", None)
    if callable(resolver):
        try:
            resolved = resolver(model_id, materialize_remote=materialize_remote)
        except TypeError:
            resolved = resolver(model_id)
        if resolved:
            return Path(resolved)
    return None


def _installed_internal_models_status() -> dict[str, bool]:
    return {
        "hf_sd15_internal": bool(_resolve_installed_model_path("hf_sd15_internal", materialize_remote=False)),
        "hf_sdxl_internal": bool(_resolve_installed_model_path("hf_sdxl_internal", materialize_remote=False)),
        "hf_sd35_medium_internal": bool(_resolve_installed_model_path("hf_sd35_medium_internal", materialize_remote=False)),
        "hf_flux1_schnell_internal": bool(_resolve_installed_model_path("hf_flux1_schnell_internal", materialize_remote=False)),
    }


def _internal_model_is_available(model_id: str, *, probe_remote: bool = False) -> bool:
    if _resolve_installed_model_path(model_id, materialize_remote=False):
        return True
    probe = getattr(models, "is_model_available", None)
    if callable(probe):
        return bool(probe(model_id, probe_remote=probe_remote))
    return bool(models.installed_path(model_id))


def _resolve_still_scene_selection(
    *,
    model_id: str | None,
    checkpoint: str | None,
    workflow_family: str | None,
    controlnet_model: str | None,
    reference_asset: str | None,
    conditioning_mode: str | None,
    controlnet_units: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    entry = _catalog_entry(model_id)
    if entry is None:
        return {
            **_resolve_comfy_still_selection(
                model_id=model_id,
                checkpoint=checkpoint,
                workflow_family=workflow_family,
                controlnet_model=controlnet_model,
                reference_asset=reference_asset,
                conditioning_mode=conditioning_mode,
                controlnet_units=controlnet_units,
            ),
            "engine": "comfyui",
            "family": None,
            "model_path": None,
        }

    engine = _catalog_entry_engine(entry)
    family = _catalog_entry_family(entry)
    render = _catalog_render_metadata(entry)
    requested_family = str(workflow_family or "auto").strip().lower()
    if requested_family not in {"auto", "txt2img", "img2img", "inpaint", "outpaint", "controlnet"}:
        requested_family = "auto"
    if requested_family == "auto":
        has_controlnet_units = any(
            isinstance(unit, dict) and str(unit.get("model") or unit.get("controlnet_name") or "").strip()
            for unit in (controlnet_units or [])
        )
        if controlnet_model or reference_asset or has_controlnet_units:
            requested_family = "controlnet"
        else:
            requested_family = str(render.get("workflow_family") or "txt2img").strip().lower()
            if requested_family == "diffusers":
                requested_family = "txt2img"
    if requested_family not in {"txt2img", "img2img", "inpaint", "outpaint", "controlnet"}:
        requested_family = "txt2img"

    if not _catalog_supports_workflow(entry, requested_family):
        raise UserFacingError(
            "The selected still model does not support this workflow.",
            hint="Choose a compatible still model or switch to a supported workflow family.",
            code="WORKFLOW_UNSUPPORTED",
            status_code=400,
        )

    if engine == "internal":
        model_path = _resolve_installed_model_path(str(entry.get("id") or ""), materialize_remote=True)
        if model_path is None:
            raise UserFacingError(
                "Internal still model is not installed",
                hint="Install the selected internal diffusers model in Models, then retry.",
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        return {
            "entry": entry,
            "engine": "internal",
            "family": family,
            "workflow_family": requested_family,
            "model_path": model_path,
            "checkpoint": None,
            "conditioning_mode": str(conditioning_mode or "raw").strip().lower() or "raw",
            "controlnet_name": None,
        }

    comfy_selection = _resolve_comfy_still_selection(
        model_id=model_id,
        checkpoint=checkpoint,
        workflow_family=requested_family,
        controlnet_model=controlnet_model,
        reference_asset=reference_asset,
        conditioning_mode=conditioning_mode,
        controlnet_units=controlnet_units,
    )
    return {
        **comfy_selection,
        "engine": "comfyui",
        "family": family,
        "model_path": None,
    }


def _resolve_project_reference_path(project_id: str, reference_asset: str | None) -> Path | None:
    raw = str(reference_asset or "").strip()
    if not raw:
        return None
    project_dir = store.project_dir(project_id)
    direct = _safe_project_path(project_dir, raw)
    if direct is not None and direct.exists() and direct.is_file():
        return direct
    refs_dir = project_dir / "assets" / "refs"
    fallback = refs_dir / Path(raw).name
    if fallback.exists() and fallback.is_file():
        return fallback
    return None


def _resolve_project_mask_path(project_id: str, mask_asset: str | None) -> Path | None:
    raw = str(mask_asset or "").strip()
    if not raw:
        return None
    project_dir = store.project_dir(project_id)
    direct = _safe_project_path(project_dir, raw)
    if direct is not None and direct.exists() and direct.is_file():
        return direct
    masks_dir = project_dir / "assets" / "masks"
    fallback = masks_dir / Path(raw).name
    if fallback.exists() and fallback.is_file():
        return fallback
    return None


def _normalize_outpaint(raw: Any) -> dict[str, int] | None:
    if not isinstance(raw, dict):
        return None
    out = {
        "top_px": max(0, int(raw.get("top_px", 0) or 0)),
        "right_px": max(0, int(raw.get("right_px", 0) or 0)),
        "bottom_px": max(0, int(raw.get("bottom_px", 0) or 0)),
        "left_px": max(0, int(raw.get("left_px", 0) or 0)),
    }
    if any(value > 0 for value in out.values()):
        return out
    return None


def _prepare_outpaint_assets(
    project_id: str,
    *,
    source_asset: str,
    outpaint: dict[str, int] | None = None,
    mask_asset: str | None = None,
) -> dict[str, Any]:
    if Image is None:
        raise UserFacingError(
            "Pillow is not installed",
            hint="Install backend deps including Pillow, then retry.",
            code="INTERNAL_DEPS",
            status_code=500,
        )

    source_path = _resolve_project_reference_path(project_id, source_asset)
    if source_path is None:
        raise UserFacingError(
            "Source image not found",
            hint="Upload or choose a valid project source image before running the render.",
            code="SOURCE_IMAGE_NOT_FOUND",
            status_code=400,
        )

    explicit_mask_path = _resolve_project_mask_path(project_id, mask_asset)
    margins = _normalize_outpaint(outpaint) or {"top_px": 0, "right_px": 0, "bottom_px": 0, "left_px": 0}

    cache_dir = store.project_dir(project_id) / "cache" / "outpaint_inputs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha1(
        json.dumps(
            {
                "source": str(source_path),
                "source_mtime": source_path.stat().st_mtime if source_path.exists() else 0,
                "mask": str(explicit_mask_path) if explicit_mask_path else None,
                "mask_mtime": explicit_mask_path.stat().st_mtime if explicit_mask_path and explicit_mask_path.exists() else 0,
                "margins": margins,
            },
            sort_keys=True,
        ).encode("utf-8", errors="ignore")
    ).hexdigest()[:12]
    prepared_source = cache_dir / f"{source_path.stem}_{digest}_source.png"
    prepared_mask = cache_dir / f"{source_path.stem}_{digest}_mask.png"

    if prepared_source.exists() and prepared_mask.exists():
        mask_source = "explicit_mask" if explicit_mask_path else "generated_outpaint"
        if explicit_mask_path and any(value > 0 for value in margins.values()):
            mask_source = "explicit_mask_with_margins"
        return {
            "source_path": prepared_source,
            "mask_path": prepared_mask,
            "mask_source": mask_source,
            "outpaint": margins if any(value > 0 for value in margins.values()) else None,
        }

    with Image.open(source_path) as source_image:
        source = source_image.convert("RGB")
        source_w, source_h = source.size
        if explicit_mask_path:
            with Image.open(explicit_mask_path) as mask_image:
                mask = mask_image.convert("L")
                if any(value > 0 for value in margins.values()):
                    canvas_w = source_w + int(margins["left_px"]) + int(margins["right_px"])
                    canvas_h = source_h + int(margins["top_px"]) + int(margins["bottom_px"])
                    canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
                    canvas.paste(source, (int(margins["left_px"]), int(margins["top_px"])))
                    if mask.size != canvas.size:
                        mask = mask.resize(canvas.size, resample=Image.BILINEAR)
                    source = canvas
                elif mask.size != source.size:
                    canvas = Image.new("RGB", mask.size, (0, 0, 0))
                    x = max(0, int((mask.size[0] - source_w) / 2))
                    y = max(0, int((mask.size[1] - source_h) / 2))
                    canvas.paste(source, (x, y))
                    source = canvas
                prepared = mask
                mask_source = "explicit_mask" if not any(value > 0 for value in margins.values()) else "explicit_mask_with_margins"
        else:
            if not any(value > 0 for value in margins.values()):
                raise UserFacingError(
                    "Outpaint margins are missing",
                    hint="Set at least one outpaint edge expansion or choose an explicit outpaint mask.",
                    code="OUTPAINT_MARGINS_MISSING",
                    status_code=400,
                )
            canvas_w = source_w + int(margins["left_px"]) + int(margins["right_px"])
            canvas_h = source_h + int(margins["top_px"]) + int(margins["bottom_px"])
            canvas = Image.new("RGB", (canvas_w, canvas_h), (0, 0, 0))
            canvas.paste(source, (int(margins["left_px"]), int(margins["top_px"])))
            prepared = Image.new("L", (canvas_w, canvas_h), 255)
            prepared.paste(0, (int(margins["left_px"]), int(margins["top_px"]), int(margins["left_px"]) + source_w, int(margins["top_px"]) + source_h))
            source = canvas
            mask_source = "generated_outpaint"

        source.save(prepared_source)
        prepared.save(prepared_mask)

    return {
        "source_path": prepared_source,
        "mask_path": prepared_mask,
        "mask_source": mask_source,
        "outpaint": margins if any(value > 0 for value in margins.values()) else None,
    }


def _prepare_condition_image(project_id: str, source_path: Path, mode: str) -> Path:
    mode_l = str(mode or "raw").strip().lower()
    if mode_l in {"raw", "external"}:
        return source_path
    if Image is None or ImageFilter is None or ImageOps is None:
        return source_path

    cache_dir = store.project_dir(project_id) / "cache" / "control_inputs"
    cache_dir.mkdir(parents=True, exist_ok=True)
    suffix = source_path.suffix if source_path.suffix.lower() in {".png", ".jpg", ".jpeg", ".webp"} else ".png"
    out_path = cache_dir / f"{source_path.stem}_{mode_l}{suffix}"
    if out_path.exists():
        return out_path

    with Image.open(source_path) as image:
        base = image.convert("RGB")
        if mode_l == "blur":
            prepared = base.filter(ImageFilter.GaussianBlur(radius=8))
        elif mode_l == "edge":
            edge = base.convert("L").filter(ImageFilter.FIND_EDGES)
            edge = ImageOps.autocontrast(edge)
            prepared = edge.point(lambda v: 255 if v >= 48 else 0).convert("RGB")
        else:
            prepared = base
        prepared.save(out_path)
    return out_path


def _fallback_comfy_input_image(image_path: Path, project_id: str) -> str:
    if comfy_portable_installed(settings.external_dir, settings.data_dir):
        input_dir = comfy_portable_root(settings.external_dir, settings.data_dir) / "ComfyUI" / "input" / "edmg" / project_id
        input_dir.mkdir(parents=True, exist_ok=True)
        dest = input_dir / image_path.name
        if not dest.exists() or dest.stat().st_mtime < image_path.stat().st_mtime:
            shutil.copy2(image_path, dest)
        return f"edmg/{project_id}/{dest.name}".replace("\\", "/")
    return str(image_path)


def _prepare_comfy_reference_image(project_id: str, node_url: str, reference_asset: str, conditioning_mode: str) -> str:
    source_path = _resolve_project_reference_path(project_id, reference_asset)
    if source_path is None:
        raise UserFacingError(
            "Reference image not found",
            hint="Upload the reference into the project first, then pick it again on the Render page.",
            code="REFERENCE_IMAGE_NOT_FOUND",
            status_code=400,
        )

    prepared = _prepare_condition_image(project_id, source_path, conditioning_mode)
    try:
        uploaded = comfy.upload_input_image(node_url, str(prepared), subfolder=f"edmg/{project_id}", overwrite=True)
        name = str(uploaded.get("name") or uploaded.get("filename") or prepared.name).strip()
        subfolder = str(uploaded.get("subfolder") or f"edmg/{project_id}").strip().strip("/")
        return f"{subfolder}/{name}".replace("\\", "/") if subfolder else name
    except Exception:
        return _fallback_comfy_input_image(prepared, project_id)


def _resolve_optional_comfy_asset_name(
    ref: str | None,
    *,
    folder: str,
    allowed_kinds: set[str] | None = None,
) -> str | None:
    raw = str(ref or "").strip()
    if not raw:
        return None
    asset = models.resolve_comfy_asset(raw, folder=folder, allowed_kinds=allowed_kinds)
    return str(asset.get("filename") or raw).strip() or None


def _normalize_render_loras(raw_loras: Any) -> list[dict[str, Any]]:
    if not isinstance(raw_loras, list):
        return []
    items = [item for item in raw_loras if isinstance(item, dict)]
    return models.resolve_loras(items)


def _normalize_controlnet_units(
    raw_units: Any,
    *,
    engine: str = "comfyui",
    family: str | None = None,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(raw_units, list):
        return normalized
    for unit in raw_units:
        if not isinstance(unit, dict):
            continue
        model_ref = str(unit.get("model") or unit.get("controlnet_name") or "").strip()
        reference_asset = str(unit.get("reference_asset") or "").strip()
        if not model_ref or not reference_asset:
            continue
        if engine == "internal":
            asset = models.resolve_internal_asset(model_ref, folder="controlnet", allowed_kinds={"controlnet"})
            asset_family = str(asset.get("family") or "").strip().lower()
            if family and asset_family and asset_family != str(family).strip().lower():
                raise UserFacingError(
                    "ControlNet family is incompatible with the selected internal still model.",
                    hint=f"Pick an internal {family.upper()} ControlNet for this still model.",
                    code="CONTROLNET_FAMILY_MISMATCH",
                    status_code=400,
                )
            normalized.append(
                {
                    "model": model_ref,
                    "id": asset.get("id"),
                    "name": asset.get("name") or model_ref,
                    "path": str(asset.get("path") or ""),
                    "family": asset_family or family,
                    "engine": "internal",
                    "reference_asset": reference_asset,
                    "conditioning_mode": str(unit.get("conditioning_mode") or "raw").strip().lower() or "raw",
                    "strength": float(unit.get("strength", 0.8)),
                    "start_percent": float(unit.get("start_percent", 0.0)),
                    "end_percent": float(unit.get("end_percent", 1.0)),
                }
            )
        else:
            asset = models.resolve_comfy_asset(model_ref, folder="controlnet", allowed_kinds={"controlnet"})
            asset_entry = _catalog_entry(str(asset.get("id") or "")) if asset.get("id") else None
            asset_family = _catalog_entry_family(asset_entry)
            if family and asset_family and asset_family != str(family).strip().lower():
                raise UserFacingError(
                    "ControlNet family is incompatible with the selected still model.",
                    hint=f"Pick a {family.upper()} ControlNet model for this still render.",
                    code="CONTROLNET_FAMILY_MISMATCH",
                    status_code=400,
                )
            normalized.append(
                {
                    "model": model_ref,
                    "name": asset.get("name") or Path(str(asset.get("filename") or model_ref)).stem,
                    "controlnet_name": str(asset.get("filename") or model_ref),
                    "family": asset_family or family,
                    "engine": "comfyui",
                    "reference_asset": reference_asset,
                    "conditioning_mode": str(unit.get("conditioning_mode") or "raw").strip().lower() or "raw",
                    "strength": float(unit.get("strength", 0.8)),
                    "start_percent": float(unit.get("start_percent", 0.0)),
                    "end_percent": float(unit.get("end_percent", 1.0)),
                }
            )
    return normalized


def _output_metadata_path(output_path: Path) -> Path:
    return output_path.with_name(f"{output_path.name}.json")


def _project_relative_path(project_id: str, path: Path | str | None) -> str | None:
    if path is None:
        return None
    try:
        return str(Path(path).resolve().relative_to(store.project_dir(project_id).resolve()))
    except Exception:
        try:
            return str(Path(path).relative_to(store.project_dir(project_id)))
        except Exception:
            return str(path)


def _build_generation_metadata(
    *,
    project_id: str,
    job_id: str,
    output_path: Path,
    payload: dict[str, Any],
    workflow_family: str,
    checkpoint: str,
    loras: list[dict[str, Any]] | None = None,
    controlnet_units: list[dict[str, Any]] | None = None,
    vae_name: str | None = None,
    prompt_id: str | None = None,
    comfyui_image: dict[str, Any] | None = None,
    node_url: str | None = None,
    backend: str = "comfyui",
    engine: str | None = None,
    model_family: str | None = None,
    resolved_model_asset: str | None = None,
    mask_source: str | None = None,
    outpaint: dict[str, Any] | None = None,
    device: str | None = None,
    cached: bool = False,
    artifact_key: str = "image",
) -> dict[str, Any]:
    metadata_path = _output_metadata_path(output_path)
    rel_output = _project_relative_path(project_id, output_path)
    rel_metadata = _project_relative_path(project_id, metadata_path)
    return {
        "kind": "studio_diffusion_output",
        "project_id": project_id,
        "job_id": job_id,
        "variant_index": int(payload.get("variant_index", 0)),
        "scene_index": int(payload.get("scene_index", 0)),
        "workflow_family": str(workflow_family or "txt2img"),
        "prompt": str(payload.get("prompt") or ""),
        "source_prompt": str(payload.get("source_prompt") or payload.get("prompt") or ""),
        "storyboard_contract": (
            dict(payload.get("storyboard"))
            if isinstance(payload.get("storyboard"), dict)
            else None
        ),
        "negative_prompt": str(payload.get("negative_prompt") or ""),
        "seed": int(payload.get("seed") or 0),
        "steps": int(payload.get("steps") or 0),
        "cfg_scale": float(payload.get("cfg") or 0.0),
        "sampler": str(payload.get("sampler") or "euler"),
        "width": int(payload.get("width") or 0),
        "height": int(payload.get("height") or 0),
        "denoise_strength": float(payload.get("denoise_strength") or 0.0),
        "base_model": {
            "model_id": payload.get("model_id"),
            "engine": str(engine or backend or "comfyui"),
            "family": model_family,
            "checkpoint": checkpoint,
            "resolved_model_asset": resolved_model_asset or checkpoint,
            "vae": vae_name,
        },
        "loras": list(loras or []),
        "controlnet_units": list(controlnet_units or []),
        "source_asset": payload.get("source_asset"),
        "reference_asset": payload.get("reference_asset"),
        "inpaint_mask": payload.get("inpaint_mask"),
        "mask_source": mask_source,
        "outpaint": outpaint,
        "hires_fix": payload.get("hires_fix"),
        "refiner": payload.get("refiner"),
        "upscaler": payload.get("upscaler"),
        "output": {
            str(artifact_key or "image"): rel_output,
            "metadata": rel_metadata,
            "cached": bool(cached),
            "comfyui_prompt_id": prompt_id,
            "comfyui_image": comfyui_image or None,
        },
        "provenance": {
            "app": "DWCT Generative Sound Studio",
            "backend": backend,
            "device": device,
            "node_url": node_url,
            "captured_at": time.time(),
        },
    }


def _write_generation_metadata(output_path: Path, metadata: dict[str, Any]) -> Path:
    metadata_path = _output_metadata_path(output_path)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata_path


def _render_checkpoint_path(video_path: Path) -> Path:
    return video_path.with_suffix(".checkpoint.json")


def _load_render_checkpoint(video_path: Path) -> dict[str, Any] | None:
    cp = _render_checkpoint_path(video_path)
    if not cp.exists():
        return None
    try:
        data = json.loads(cp.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _job_checkpoint_extra(mode: str, model_id: str, runtime_checkpoint: dict[str, Any] | None = None, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {"mode": mode, "model_id": model_id}
    if runtime_checkpoint:
        payload["runtime_checkpoint"] = runtime_checkpoint
    payload.update(extra)
    return payload




def _runtime_checkpoint_from_job(project_id: str, job: Any | None) -> dict[str, Any] | None:
    if not job:
        return None
    progress = job.progress if isinstance(getattr(job, "progress", None), dict) else {}
    runtime = progress.get("runtime_checkpoint")
    if isinstance(runtime, dict) and runtime:
        return dict(runtime)
    result = job.result if isinstance(getattr(job, "result", None), dict) else {}
    runtime = result.get("runtime_checkpoint")
    if isinstance(runtime, dict) and runtime:
        return dict(runtime)
    rel_video = result.get("video") if isinstance(result, dict) else None
    if isinstance(rel_video, str) and rel_video:
        try:
            video_path = safe_join(store.project_dir(project_id), rel_video)
        except Exception:
            video_path = None
        if video_path is not None and video_path.exists():
            cp = _load_render_checkpoint(video_path)
            if cp:
                return cp
    return None


def _read_log_tail(project_id: str, job_id: str, *, tail_lines: int = 80) -> dict[str, Any]:
    lp = jobs.log_path(project_id, job_id)
    if not lp.exists():
        return {"log": "", "log_tail": "", "log_path": str(lp), "log_exists": False, "log_line_count": 0}
    raw = lp.read_text(encoding="utf-8", errors="ignore")
    lines = raw.splitlines()
    tail = max(1, int(tail_lines or 80))
    tail_text = "\n".join(lines[-tail:])
    return {
        "log": raw,
        "log_tail": tail_text,
        "log_path": str(lp),
        "log_exists": True,
        "log_line_count": len(lines),
    }


def _job_output_metadata(project_id: str, job: Any | None, runtime_checkpoint: dict[str, Any] | None = None) -> dict[str, Any]:
    result = job.result if job and isinstance(getattr(job, "result", None), dict) else {}
    progress = job.progress if job and isinstance(getattr(job, "progress", None), dict) else {}
    project_dir = store.project_dir(project_id)

    rel_video = result.get("video") or progress.get("video")
    video_abs = result.get("video_abs")
    checkpoint_outputs = runtime_checkpoint.get("outputs") if isinstance(runtime_checkpoint, dict) else {}
    checkpoint_json = checkpoint_outputs.get("checkpoint_json") if isinstance(checkpoint_outputs, dict) else None

    video_path = None
    if isinstance(rel_video, str) and rel_video:
        try:
            video_path = safe_join(project_dir, rel_video)
        except Exception:
            video_path = None
    elif isinstance(video_abs, str) and video_abs:
        video_path = Path(video_abs)
        try:
            rel_video = str(video_path.relative_to(project_dir))
        except Exception:
            pass

    if not checkpoint_json and video_path is not None:
        try:
            checkpoint_json = str(_render_checkpoint_path(video_path).relative_to(project_dir))
        except Exception:
            checkpoint_json = str(_render_checkpoint_path(video_path))

    checkpoint_path = None
    if isinstance(checkpoint_json, str) and checkpoint_json:
        try:
            checkpoint_path = safe_join(project_dir, checkpoint_json)
        except Exception:
            checkpoint_path = Path(checkpoint_json)

    render_meta = None
    render_meta_path = None
    if video_path is not None:
        render_meta_path = video_path.with_suffix('.render.json')
        if render_meta_path.exists():
            try:
                render_meta = json.loads(render_meta_path.read_text(encoding='utf-8'))
            except Exception:
                render_meta = None

    cache_paths = {}
    if isinstance(render_meta, dict):
        outputs = render_meta.get('outputs') if isinstance(render_meta.get('outputs'), dict) else {}
        frames = render_meta.get('frames') if isinstance(render_meta.get('frames'), dict) else {}
        cache_paths = {
            'frames_dir': frames.get('dir'),
            'raw_mp4': outputs.get('raw_mp4'),
            'interp_mp4': outputs.get('interp_mp4'),
            'final_mp4': outputs.get('final_mp4'),
            'checkpoint_json': outputs.get('checkpoint_json') or checkpoint_json,
        }
    elif checkpoint_path is not None:
        base = checkpoint_path.with_suffix('')
        cache_paths = {
            'checkpoint_json': str(checkpoint_path),
            'final_mp4': str(base.with_suffix('.mp4')),
        }

    return {
        'video_relpath': rel_video,
        'video_abspath': str(video_path) if video_path is not None else video_abs,
        'checkpoint_json_relpath': checkpoint_json,
        'checkpoint_json_abspath': str(checkpoint_path) if checkpoint_path is not None else None,
        'checkpoint_exists': bool(checkpoint_path and checkpoint_path.exists()),
        'render_meta_path': str(render_meta_path) if render_meta_path is not None else None,
        'render_meta_exists': bool(render_meta_path and render_meta_path.exists()),
        'render_meta': render_meta,
        'cache_paths': cache_paths,
    }


def _job_detail_payload(project_id: str, job: Any, *, tail_lines: int = 80) -> dict[str, Any]:
    runtime_checkpoint = _runtime_checkpoint_from_job(project_id, job)
    log = _read_log_tail(project_id, job.id, tail_lines=tail_lines)
    outputs = _job_output_metadata(project_id, job, runtime_checkpoint)
    return {
        'ok': True,
        'job': job.__dict__,
        'runtime_checkpoint': runtime_checkpoint,
        'log': log['log'],
        'log_tail': log['log_tail'],
        'log_path': log['log_path'],
        'log_exists': log['log_exists'],
        'log_line_count': log['log_line_count'],
        'outputs': outputs,
        'resume_ready': bool((runtime_checkpoint or {}).get('can_resume')),
    }






def _job_runtime_checkpoint_paths(project_id: str, job: Any | None) -> dict[str, Any]:
    runtime_checkpoint = _runtime_checkpoint_from_job(project_id, job)
    outputs = _job_output_metadata(project_id, job, runtime_checkpoint)
    cache_paths = dict(outputs.get("cache_paths") or {})
    return {
        "project_dir": store.project_dir(project_id),
        "runtime_checkpoint": runtime_checkpoint,
        "outputs": outputs,
        "frames_dir": cache_paths.get("frames_dir"),
        "raw_mp4": cache_paths.get("raw_mp4"),
        "interp_mp4": cache_paths.get("interp_mp4"),
        "final_mp4": cache_paths.get("final_mp4") or outputs.get("video_abspath"),
        "checkpoint_json": outputs.get("checkpoint_json_abspath"),
        "render_meta_path": outputs.get("render_meta_path"),
    }


def _safe_project_path(project_dir: Path, value: str | None) -> Path | None:
    if not value:
        return None
    try:
        return safe_join(project_dir, value)
    except Exception:
        p = Path(value)
        try:
            p.resolve().relative_to(project_dir.resolve())
            return p
        except Exception:
            return None


def _apply_runtime_checkpoint_state(project_id: str, job: Any, runtime_checkpoint: dict[str, Any] | None) -> Any:
    if isinstance(job.progress, dict):
        progress = dict(job.progress)
        if runtime_checkpoint is None:
            progress.pop("runtime_checkpoint", None)
        else:
            progress["runtime_checkpoint"] = dict(runtime_checkpoint)
        job.progress = progress
    if isinstance(job.result, dict):
        result = dict(job.result)
        if runtime_checkpoint is None:
            result.pop("runtime_checkpoint", None)
        else:
            result["runtime_checkpoint"] = dict(runtime_checkpoint)
        job.result = result
    jobs.save(job)

    proj = store.get(project_id)
    if proj is not None:
        targets = []
        latest = proj.meta.get("last_internal_render")
        if isinstance(latest, dict):
            targets.append(latest)
        hist = proj.meta.get("internal_render_history")
        if isinstance(hist, list):
            targets.extend([entry for entry in hist if isinstance(entry, dict)])
        video_rel = None
        if isinstance(getattr(job, "result", None), dict):
            video_rel = job.result.get("video")
        for entry in targets:
            same_video = bool(video_rel and entry.get("video") == video_rel)
            same_source = bool(entry.get("source_job_id") and str(entry.get("source_job_id")) == str(job.id))
            if same_video or same_source:
                if runtime_checkpoint is None:
                    entry.pop("runtime_checkpoint", None)
                else:
                    entry["runtime_checkpoint"] = dict(runtime_checkpoint)
        store.save(proj)
    return jobs.get(project_id, job.id) or job


def _terminalize_failed_runtime_checkpoint(project_id: str, job: Any, *, message: str) -> None:
    """Make a persisted render checkpoint agree with a terminally failed job."""

    progress = dict(job.progress) if isinstance(getattr(job, "progress", None), dict) else {}
    progress["stage"] = "failed"
    progress["message"] = str(message or "Render failed")
    job.progress = progress

    runtime_checkpoint = _runtime_checkpoint_from_job(project_id, job)
    if not runtime_checkpoint:
        jobs.save(job)
        return
    completed_frames = max(0, int(runtime_checkpoint.get("completed_frames") or 0))
    outputs = dict(runtime_checkpoint.get("outputs") or {})
    can_resume = bool(
        completed_frames > 0
        and not bool(outputs.get("final_exists"))
    )
    runtime_checkpoint.update(
        {
            "status": "failed",
            "stage": "failed",
            "message": str(message or "Render failed"),
            "can_resume": can_resume,
            "resume_recommended": can_resume,
            "updated_at": time.time(),
        }
    )
    checkpoint_value = outputs.get("checkpoint_json")
    checkpoint_path = _safe_project_path(
        store.project_dir(project_id),
        str(checkpoint_value) if checkpoint_value else None,
    )
    if checkpoint_path is not None:
        try:
            checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
            checkpoint_path.write_text(
                json.dumps(runtime_checkpoint, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            logger.warning(
                "Unable to terminalize failed render checkpoint: project=%s job=%s",
                project_id,
                getattr(job, "id", "unknown"),
                exc_info=True,
            )
    progress["runtime_checkpoint"] = dict(runtime_checkpoint)
    job.progress = progress
    _apply_runtime_checkpoint_state(project_id, job, runtime_checkpoint)


def _remove_path(path: Path | None) -> bool:
    if path is None or not path.exists():
        return False
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=False)
        return True
    path.unlink(missing_ok=True)
    return True


def _mutate_internal_job_artifacts(project_id: str, job: Any, *, clear_cached_frames: bool = False, drop_checkpoint: bool = False) -> dict[str, Any]:
    if getattr(job, "type", None) != "internal_video":
        raise HTTPException(400, "Artifact maintenance is only available for internal render jobs")
    if getattr(job, "status", None) in ("queued", "running"):
        raise HTTPException(409, "Stop the active job before modifying cached frames or checkpoints")

    info = _job_runtime_checkpoint_paths(project_id, job)
    project_dir = info["project_dir"]
    runtime_checkpoint = dict(info.get("runtime_checkpoint") or {}) if info.get("runtime_checkpoint") else None
    removed: list[str] = []

    frames_dir = _safe_project_path(project_dir, info.get("frames_dir"))
    raw_mp4 = _safe_project_path(project_dir, info.get("raw_mp4"))
    interp_mp4 = _safe_project_path(project_dir, info.get("interp_mp4"))
    render_meta_path = _safe_project_path(project_dir, info.get("render_meta_path"))
    checkpoint_json = _safe_project_path(project_dir, info.get("checkpoint_json"))
    final_mp4 = _safe_project_path(project_dir, info.get("final_mp4"))

    if clear_cached_frames:
        for label, target in (("frames_dir", frames_dir), ("raw_mp4", raw_mp4), ("interp_mp4", interp_mp4), ("render_meta_path", render_meta_path)):
            if _remove_path(target):
                removed.append(label)

    if drop_checkpoint and _remove_path(checkpoint_json):
        removed.append("checkpoint_json")

    if runtime_checkpoint is not None:
        outputs = dict(runtime_checkpoint.get("outputs") or {})
        if clear_cached_frames:
            outputs["raw_exists"] = bool(raw_mp4 and raw_mp4.exists())
            outputs["interp_exists"] = bool(interp_mp4 and interp_mp4.exists())
            outputs["final_exists"] = bool(final_mp4 and final_mp4.exists())
            runtime_checkpoint["can_resume"] = False
            runtime_checkpoint["resume_recommended"] = False
            runtime_checkpoint["message"] = "Cached frames and intermediates cleared"
            runtime_checkpoint["maintenance_action"] = "clear_cached_frames"
        if drop_checkpoint:
            outputs["checkpoint_json"] = None
            runtime_checkpoint["can_resume"] = False
            runtime_checkpoint["resume_recommended"] = False
            runtime_checkpoint["message"] = "Checkpoint file removed" if not clear_cached_frames else "Cached frames cleared and checkpoint removed"
            runtime_checkpoint["maintenance_action"] = "drop_checkpoint" if not clear_cached_frames else "clear_cached_frames+drop_checkpoint"
        runtime_checkpoint["outputs"] = outputs
        runtime_checkpoint["updated_at"] = time.time()
        runtime_checkpoint["checkpoint_present"] = bool(checkpoint_json and checkpoint_json.exists())
        if checkpoint_json and checkpoint_json.exists():
            try:
                checkpoint_json.write_text(json.dumps(runtime_checkpoint, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
        job = _apply_runtime_checkpoint_state(project_id, job, runtime_checkpoint)
    else:
        job = jobs.get(project_id, job.id) or job

    detail = _job_detail_payload(project_id, job, tail_lines=80)
    return {
        "ok": True,
        "job": job.__dict__,
        "removed": removed,
        "detail": detail,
    }


_RESOLVED_INTERNAL_VIDEO_PAYLOAD_KEYS = (
    "temporal_mode",
    "temporal_steps",
    "keyframe_continuity_mode",
    "video_model_engine",
    "video_model_id",
    "video_model_max_frames_per_scene",
    "video_model_decode_chunk_size",
    "video_model_dtype",
    "video_model_cpu_offload",
    "video_model_motion_score_mode",
    "video_model_manual_motion_score",
    "video_model_anchor_mode",
    "video_model_prompt_refine",
    "video_model_scene_motion",
    "video_model_apply_timeline_camera",
    "video_model_keyframe_renderer",
    "video_model_keyframe_model_id",
)


def _persist_resolved_internal_video_payload(
    payload: dict[str, Any],
    preflight: dict[str, Any],
) -> dict[str, Any]:
    """Persist the executable model pair and memory policy returned by preflight."""

    resolved = dict(payload)
    mode = str(preflight.get("mode") or resolved.get("render_mode") or "auto").strip().lower()
    resolved["render_mode"] = mode
    if mode != "diffusion":
        return resolved

    settings_data = preflight.get("settings")
    if not isinstance(settings_data, dict):
        return resolved
    if preflight.get("model_id"):
        resolved["model_id"] = str(preflight["model_id"])
    for key in _RESOLVED_INTERNAL_VIDEO_PAYLOAD_KEYS:
        if key in settings_data and settings_data[key] is not None:
            resolved[key] = settings_data[key]
    resolved.pop("video_model_path", None)
    resolved["_render_recipe_graph"] = parseq_adapter.build_render_recipe_graph(
        manifest=resolved.get("parseq_manifest") if isinstance(resolved.get("parseq_manifest"), dict) else None,
        internal_request=resolved,
    )
    return resolved


def _repair_legacy_internal_video_selection(payload: dict[str, Any]) -> tuple[dict[str, Any], str | None]:
    """Repair only the two known mismatched pairs written by older Studio builds."""

    repaired = dict(payload)
    engine = str(repaired.get("video_model_engine") or "auto").strip().lower()
    model_id = str(repaired.get("video_model_id") or "").strip()
    declared_engine = INTERNAL_VIDEO_MODEL_ENGINES.get(model_id)
    if engine not in {"svd", "animatediff"} or not declared_engine or engine == declared_engine:
        return repaired, None
    canonical_model_id = (
        INTERNAL_SVD_VIDEO_MODEL_ID if engine == "svd" else INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID
    )
    repaired["video_model_id"] = canonical_model_id
    return (
        repaired,
        f"Normalized legacy {engine}/{model_id} selection to {engine}/{canonical_model_id} before retry.",
    )


def _enqueue_internal_job_from_source(project_id: str, source_job: Any, *, resume_existing_frames: bool, queue_action: str) -> dict[str, Any]:
    payload = deepcopy(getattr(source_job, "payload", None) or {})
    payload["resume_existing_frames"] = bool(resume_existing_frames)
    payload["queue_action"] = str(queue_action)
    payload["source_job_id"] = str(source_job.id)
    if not resume_existing_frames:
        payload["queue_clean_restart"] = True

    payload, legacy_selection_note = _repair_legacy_internal_video_selection(payload)
    preflight = _internal_render_preflight_data(project_id, payload)
    payload = _persist_resolved_internal_video_payload(payload, preflight)
    mode = str(preflight.get("mode") or payload.get("render_mode") or "auto")
    model_id = str(preflight.get("model_id") or payload.get("model_id") or "auto")
    checkpoint = _runtime_checkpoint_from_job(project_id, source_job)
    total = max(1, int(preflight.get("estimated_frames", 1)) + 3)
    job = jobs.create(project_id, "internal_video", payload)
    message = (
        f"Queued resume from checkpoint for model {model_id}"
        if resume_existing_frames
        else f"Queued clean restart for model {model_id}"
    )
    job.progress = {
        "stage": "queued",
        "current": 0,
        "total": total,
        "percent": 0.0,
        "message": message,
        **_job_checkpoint_extra(
            "internal",
            model_id,
            checkpoint,
            queue_action=queue_action,
            source_job_id=str(source_job.id),
            resume_existing_frames=bool(resume_existing_frames),
        ),
    }
    jobs.save(job)
    jobs.append_log(project_id, job.id, f"Queued {queue_action} from job {source_job.id}")
    if legacy_selection_note:
        jobs.append_log(project_id, job.id, legacy_selection_note)
    if checkpoint:
        jobs.append_log(
            project_id,
            job.id,
            f"Checkpoint summary: status={checkpoint.get('status')} resume_percent={checkpoint.get('resume_percent')} chunks={checkpoint.get('completed_chunks')}/{checkpoint.get('estimated_chunks')}",
        )
    proj = store.get(project_id)
    if proj:
        proj.meta.setdefault("jobs", []).append(job.__dict__)
        store.save(proj)
    return {
        "ok": True,
        "job": job.__dict__,
        "preflight": _public_render_preflight(preflight),
        "source_job": source_job.__dict__,
    }


def _tier_rank(name: str) -> int:
    return {"draft": 0, "balanced": 1, "quality": 2}.get(str(name or "draft").lower(), 0)


def _internal_render_defaults_for_tier(tier: str, hw: dict[str, Any], *, duration_s: float | None = None) -> dict[str, Any]:
    tier_l = str(tier or "draft").lower()
    backend = str(hw.get("backend") or "cpu").lower()
    if tier_l == "quality":
        defaults: dict[str, Any] = {
            "fps_output": 24,
            "fps_render": 4,
            "width": 1024,
            "height": 576,
            "steps": 24,
            "cfg": 7.2,
            "keyframe_interval_s": 4.0,
            "interpolation_engine": "auto",
            "temporal_mode": "frame_img2img" if backend == "cuda" else "keyframes",
            "temporal_steps": 18,
            "refine_every_n_frames": 1,
            "anchor_strength": 0.20,
            "prompt_blend": True,
        }
    elif tier_l == "balanced":
        defaults = {
            "fps_output": 24,
            "fps_render": 2,
            "width": 768,
            "height": 432,
            "steps": 15 if backend == "cpu" else 16,
            "cfg": 6.8,
            "keyframe_interval_s": 5.0,
            "interpolation_engine": "auto",
            "temporal_mode": "frame_img2img" if backend == "cuda" else "keyframes",
            "temporal_steps": 12,
            "refine_every_n_frames": 2,
            "anchor_strength": 0.18,
            "prompt_blend": True,
        }
    else:
        defaults = {
            "fps_output": 24,
            "fps_render": 1,
            "width": 640,
            "height": 360,
            "steps": 10,
            "cfg": 6.0,
            "keyframe_interval_s": 6.0,
            "interpolation_engine": "auto",
            "temporal_mode": "keyframes",
            "temporal_steps": 8,
            "refine_every_n_frames": 3,
            "anchor_strength": 0.12,
            "prompt_blend": True,
        }
    if duration_s and duration_s > 120.0:
        defaults["fps_render"] = min(int(defaults["fps_render"]), 2)
        defaults["keyframe_interval_s"] = max(float(defaults["keyframe_interval_s"]), 6.0)
    return defaults


def _build_render_chunk_plan(
    hw: dict[str, Any] | None = None,
    *,
    applied_tier: str = "draft",
    duration_s: float | None = None,
    total_frames: int | None = None,
    fps_render: int | None = None,
    render_mode: str = "diffusion",
) -> dict[str, Any]:
    hw = dict(hw or {})
    backend_family = str(hw.get("backend_family") or "cpu_only").lower()
    applied = str(applied_tier or "draft").lower()
    fps_r = max(1, int(fps_render or 1))
    total_frames_i = max(0, int(total_frames or 0))
    duration = float(duration_s or 0.0)
    if duration <= 0.0 and total_frames_i > 0:
        duration = float(total_frames_i) / float(fps_r)
    mode_l = str(render_mode or "diffusion").lower()
    notes: list[str] = []

    enabled = False
    strategy = "single_pass"
    checkpoint_interval_frames = max(1, min(60, fps_r * 15))
    if backend_family == "cpu_only":
        threshold_s = 45.0 if mode_l == "diffusion" else 90.0
        frames_per_chunk = 90 if applied == "balanced" else 120
        if total_frames_i >= frames_per_chunk * 2 or duration >= threshold_s:
            enabled = True
            strategy = "resume_friendly_chunks"
            notes.append("CPU-only system detected; using resume-friendly chunk guidance for long renders.")
    elif backend_family == "integrated_gpu":
        threshold_s = 75.0 if mode_l == "diffusion" else 120.0
        frames_per_chunk = 120 if applied == "balanced" else 180
        if total_frames_i >= frames_per_chunk * 2 or duration >= threshold_s:
            enabled = True
            strategy = "integrated_gpu_chunks"
            notes.append("Integrated-graphics system detected; chunk guidance is enabled to keep long renders recoverable.")
    else:
        frames_per_chunk = 240 if mode_l == "diffusion" else 360
        if total_frames_i >= frames_per_chunk * 3 and applied != "quality":
            enabled = True
            strategy = "throughput_chunks"
            notes.append("Long render on discrete GPU; chunk checkpoints will improve retryability.")

    if enabled:
        estimated_chunks = max(1, math.ceil(total_frames_i / max(1, frames_per_chunk)))
    else:
        estimated_chunks = 1
        frames_per_chunk = max(total_frames_i, 1)

    seconds_per_chunk = round(float(frames_per_chunk) / float(fps_r), 2)
    return {
        "enabled": enabled,
        "strategy": strategy,
        "resume_recommended": bool(enabled or backend_family != "discrete_gpu"),
        "frames_per_chunk": int(frames_per_chunk),
        "seconds_per_chunk": seconds_per_chunk,
        "estimated_chunks": int(estimated_chunks),
        "checkpoint_interval_frames": int(checkpoint_interval_frames),
        "notes": notes,
    }


@lru_cache(maxsize=1)
def _windows_video_controllers() -> list[dict[str, Any]]:
    if platform.system().lower() != "windows":
        return []
    cmd = [
        "powershell",
        "-NoProfile",
        "-Command",
        "Get-CimInstance Win32_VideoController | Select-Object Name,AdapterRAM | ConvertTo-Json -Compress",
    ]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=6, check=False)
        if result.returncode != 0:
            return []
        raw = str(result.stdout or "").strip()
        if not raw:
            return []
        data = json.loads(raw)
        items = data if isinstance(data, list) else [data]
        out: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            name = str(item.get("Name") or item.get("name") or "").strip()
            if not name:
                continue
            adapter_ram = item.get("AdapterRAM") or item.get("adapterRam") or 0
            try:
                vram_gb = round(float(adapter_ram) / float(1024 ** 3), 2) if adapter_ram else 0.0
            except Exception:
                vram_gb = 0.0
            vendor = "unknown"
            name_l = name.lower()
            if "nvidia" in name_l:
                vendor = "nvidia"
            elif "amd" in name_l or "radeon" in name_l:
                vendor = "amd"
            elif "intel" in name_l:
                vendor = "intel"
            out.append({"name": name, "vendor": vendor, "vram_gb": vram_gb})
        return out
    except Exception:
        return []


def _pick_windows_accel_gpu() -> dict[str, Any] | None:
    gpus = _windows_video_controllers()
    if not gpus:
        return None
    preferred = [g for g in gpus if g.get("vendor") in {"amd", "nvidia", "intel"}]
    ordered = preferred or gpus
    ordered = sorted(ordered, key=lambda item: (0 if item.get("vendor") == "amd" else 1, -float(item.get("vram_gb") or 0.0)))
    return ordered[0] if ordered else None


def _directml_runtime_status() -> dict[str, Any]:
    out: dict[str, Any] = {
        "available": False,
        "runtime_ready": False,
        "provider": "DmlExecutionProvider",
        "providers": [],
        "device_name": None,
        "vendor": None,
        "vram_gb": 0.0,
        "integrated": False,
        "error": None,
    }
    if platform.system().lower() != "windows":
        out["error"] = "DirectML is only available on Windows."
        return out
    try:
        import onnxruntime as ort  # type: ignore

        providers = list(ort.get_available_providers() or [])
        out["providers"] = providers
        out["runtime_ready"] = "DmlExecutionProvider" in providers
    except Exception:
        logger.exception("DirectML runtime discovery failed")
        out["error"] = "DirectML runtime discovery failed"
        return out

    gpu = _pick_windows_accel_gpu()
    if gpu:
        out["device_name"] = gpu.get("name")
        out["vendor"] = gpu.get("vendor")
        out["vram_gb"] = float(gpu.get("vram_gb") or 0.0)
        out["integrated"] = bool(gpu.get("vendor") == "intel")
    out["available"] = bool(out["runtime_ready"])
    return out


def _backend_family_for(backend: str, *, integrated: bool = False) -> str:
    backend_l = str(backend or "cpu").lower()
    if backend_l in {"cuda"}:
        return "discrete_gpu"
    if backend_l in {"mps"}:
        return "integrated_gpu"
    if backend_l == "directml":
        return "integrated_gpu" if integrated else "discrete_gpu"
    return "cpu_only"


def _build_internal_render_plan(hw: dict[str, Any] | None = None, *, requested_tier: str = "auto", duration_s: float | None = None) -> dict[str, Any]:
    hw = dict(hw or {})
    backend = str(hw.get("backend") or "cpu").lower()
    backend_family = str(hw.get("backend_family") or _backend_family_for(backend, integrated=bool(hw.get("integrated_acceleration")))).lower()
    vram_gb = float(hw.get("vram_gb") or 0.0)
    ram_gb = float(hw.get("ram_gb") or 0.0)
    cpu_threads = int(hw.get("cpu_threads") or 1)
    notes: list[str] = []

    if backend == "cuda":
        if vram_gb >= 10.0:
            recommended = "quality"
            max_supported = "quality"
        elif vram_gb >= 6.0:
            recommended = "balanced"
            max_supported = "balanced"
            notes.append("Mid-range CUDA GPU detected; balanced tier is the safest default.")
        else:
            recommended = "draft"
            max_supported = "draft"
            notes.append("Low-VRAM CUDA GPU detected; use draft settings for reliable renders.")
        device_preference = "cuda"
    elif backend == "mps":
        recommended = "balanced" if ram_gb >= 16.0 else "draft"
        max_supported = "balanced"
        device_preference = "mps"
        notes.append("Apple Silicon acceleration detected; balanced tier is recommended for sustained laptop rendering.")
    elif backend == "directml":
        if backend_family == "discrete_gpu":
            recommended = "balanced" if (vram_gb >= 6.0 or ram_gb >= 16.0) else "draft"
            max_supported = "balanced"
            notes.append("DirectML acceleration detected; SDXL is the preferred AMD / Windows internal path.")
        else:
            recommended = "draft"
            max_supported = "balanced"
            notes.append("Integrated DirectML acceleration detected; draft or balanced tiers are the safest choice.")
        device_preference = "directml"
    else:
        if ram_gb >= 24.0 and cpu_threads >= 12:
            recommended = "balanced"
            max_supported = "balanced"
            notes.append("High-core CPU system detected; balanced tier is viable but slower than GPU rendering.")
        else:
            recommended = "draft"
            max_supported = "draft"
            notes.append("CPU-only or low-power system detected; draft tier is recommended for responsiveness.")
        device_preference = "cpu"

    requested = str(requested_tier or "auto").strip().lower()
    if requested not in {"auto", "draft", "balanced", "quality"}:
        requested = "auto"
    applied = recommended if requested == "auto" else requested
    if _tier_rank(applied) > _tier_rank(max_supported):
        notes.append(f"Requested tier '{applied}' exceeds current hardware ceiling; capping to {max_supported}.")
        applied = max_supported

    defaults = _internal_render_defaults_for_tier(applied, hw, duration_s=duration_s)
    chunk_plan = _build_render_chunk_plan(
        hw,
        applied_tier=applied,
        duration_s=duration_s,
        fps_render=int(defaults.get("fps_render", 1)),
        render_mode="diffusion",
    )
    if chunk_plan["resume_recommended"]:
        defaults["resume_existing_frames"] = True
    if chunk_plan["enabled"] and backend_family == "cpu_only":
        defaults["interpolation_engine"] = "fps"
        if float(duration_s or 0.0) >= 90.0 and _tier_rank(applied) <= _tier_rank("balanced"):
            defaults["temporal_mode"] = "off"
            defaults["refine_every_n_frames"] = max(int(defaults.get("refine_every_n_frames", 1)), 3)
            notes.append("Long CPU render detected; using chunk-friendly temporal defaults to make resumes cheaper.")
    elif chunk_plan["enabled"] and backend_family == "integrated_gpu":
        if _tier_rank(applied) <= _tier_rank("balanced"):
            defaults["temporal_mode"] = "keyframes"
            defaults["refine_every_n_frames"] = max(int(defaults.get("refine_every_n_frames", 1)), 2)
            notes.append("Integrated GPU path favors keyframe continuity over denser temporal refinement on long renders.")

    if backend == "cuda" and vram_gb >= 14.0 and _tier_rank(applied) >= _tier_rank("quality"):
        preferred_internal_model = "hf_sd35_medium_internal"
    elif backend == "cuda" and _tier_rank(applied) >= _tier_rank("balanced"):
        preferred_internal_model = "hf_sdxl_internal"
    elif backend == "directml":
        preferred_internal_model = "hf_sdxl_internal" if _tier_rank(applied) >= _tier_rank("balanced") else "hf_sd15_internal"
    else:
        preferred_internal_model = "hf_sd15_internal"
    return {
        "requested_tier": requested,
        "recommended_tier": recommended,
        "max_supported_tier": max_supported,
        "applied_tier": applied,
        "device_preference": device_preference,
        "preferred_internal_model": preferred_internal_model,
        "defaults": defaults,
        "chunk_plan": chunk_plan,
        "notes": notes + list(chunk_plan.get("notes") or []),
        "hardware_backend": backend,
        "supports_proxy_render": False,
    }


_HW_CACHE: dict[str, Any] = {}
_HW_CACHE_TTL_S: float = 30.0


def _hardware_profile_invalidate() -> None:
    """Force next call to _hardware_profile() to re-probe hardware."""
    _HW_CACHE.clear()


def _hardware_profile() -> dict[str, Any]:
    """Best-effort local hardware detection used for auto tiering.

    Results are cached for _HW_CACHE_TTL_S seconds so rapid successive calls
    (e.g. preflight + render plan + conductor) don't re-probe torch.cuda and
    the DirectML runtime on every request.
    """
    import time as _time
    now = _time.monotonic()
    if _HW_CACHE.get("_ts", 0.0) + _HW_CACHE_TTL_S > now:
        return dict(_HW_CACHE.get("_data") or {})
    result = _compute_hardware_profile()
    _HW_CACHE["_ts"] = now
    _HW_CACHE["_data"] = result
    return dict(result)


def _compute_hardware_profile() -> dict[str, Any]:
    """Unconditionally probe local hardware — call _hardware_profile() instead."""
    cpu_threads = max(1, int(os.cpu_count() or 1))
    out: dict[str, Any] = {
        "backend": "cpu",
        "device": "cpu",
        "device_name": "CPU",
        "available_backends": ["cpu"],
        "vram_gb": 0.0,
        "ram_gb": 0.0,
        "cpu_threads": cpu_threads,
        "platform": platform.system().lower(),
        "machine": platform.machine().lower(),
        "integrated_acceleration": False,
        "gpu_vendor": None,
        "supports_directml": False,
        "directml_runtime_ready": False,
        "directml_device_name": None,
    }
    try:
        page_size = int(os.sysconf("SC_PAGE_SIZE"))
        phys_pages = int(os.sysconf("SC_PHYS_PAGES"))
        out["ram_gb"] = round((page_size * phys_pages) / float(1024 ** 3), 2)
    except Exception:
        try:
            import psutil  # type: ignore
            out["ram_gb"] = round(float(psutil.virtual_memory().total) / float(1024 ** 3), 2)
        except Exception:
            out["ram_gb"] = 0.0
    try:
        import torch  # type: ignore
        if getattr(torch, "cuda", None) is not None and torch.cuda.is_available():
            out["backend"] = "cuda"
            out["device"] = "cuda"
            out["available_backends"].append("cuda")
            try:
                props = torch.cuda.get_device_properties(0)
                out["device_name"] = getattr(props, "name", "cuda")
                out["vram_gb"] = round(float(getattr(props, "total_memory", 0.0)) / float(1024 ** 3), 2)
            except Exception:
                pass
        else:
            try:
                mps = getattr(getattr(torch, "backends", None), "mps", None)
                if mps is not None and mps.is_available():
                    out["backend"] = "mps"
                    out["device"] = "mps"
                    out["device_name"] = "Apple Silicon GPU"
                    out["available_backends"].append("mps")
                    out["integrated_acceleration"] = True
                    out["gpu_vendor"] = "apple"
            except Exception:
                pass
    except Exception:
        pass

    directml = _directml_runtime_status()
    out["supports_directml"] = bool(directml.get("available"))
    out["directml_runtime_ready"] = bool(directml.get("runtime_ready"))
    out["directml_device_name"] = directml.get("device_name")
    if directml.get("available"):
        if "directml" not in out["available_backends"]:
            out["available_backends"].append("directml")
        if out["backend"] == "cpu":
            out["backend"] = "directml"
            out["device"] = "directml"
            out["device_name"] = str(directml.get("device_name") or "DirectML GPU")
            out["vram_gb"] = max(float(out.get("vram_gb") or 0.0), float(directml.get("vram_gb") or 0.0))
            out["integrated_acceleration"] = bool(directml.get("integrated"))
            out["gpu_vendor"] = directml.get("vendor")
        elif not out.get("gpu_vendor") and directml.get("vendor"):
            out["gpu_vendor"] = directml.get("vendor")

    cuda_cfg: dict[str, Any] = {}
    try:
        cuda_cfg = dict((render_settings.get().get("cuda") or {}))
    except Exception:
        cuda_cfg = {}
    cuda_available = bool("cuda" in list(out.get("available_backends") or []) or str(out.get("backend") or "").lower() == "cuda")
    cuda_enabled = bool(cuda_cfg.get("enabled", True))
    cuda_auto = bool(cuda_cfg.get("allow_auto_selection", True))
    out["cuda_runtime_ready"] = cuda_available
    out["cuda_enabled"] = cuda_enabled
    out["cuda_allow_auto_selection"] = cuda_auto
    out["cuda_preferred_model"] = str(cuda_cfg.get("preferred_model") or "auto")
    out["cuda_tf32_enabled"] = bool(cuda_cfg.get("enable_tf32", True))
    if cuda_available:
        out["cuda_device_name"] = out.get("device_name") if str(out.get("backend") or "").lower() == "cuda" else None
        out["cuda_vram_gb"] = float(out.get("vram_gb") or 0.0) if str(out.get("backend") or "").lower() == "cuda" else 0.0
    if str(out.get("backend") or "").lower() == "cuda" and (not cuda_enabled or not cuda_auto):
        out["backend"] = "cpu"
        out["device"] = "cpu"
        out["device_name"] = "CPU"
        out["vram_gb"] = 0.0
        out["gpu_vendor"] = out.get("gpu_vendor") or "nvidia"
        out["cuda_disabled_by_settings"] = not cuda_enabled
        out["cuda_auto_selection_disabled"] = cuda_enabled and not cuda_auto

    out["backend_family"] = _backend_family_for(
        str(out.get("backend") or "cpu"),
        integrated=bool(out.get("integrated_acceleration")),
    )
    plan = _build_internal_render_plan(out, requested_tier="auto")
    out["recommended_tier"] = plan["recommended_tier"]
    out["max_supported_tier"] = plan["max_supported_tier"]
    out["preferred_internal_model"] = plan["preferred_internal_model"]
    if str(out.get("backend") or "").lower() == "cuda":
        preferred_cuda_model = str(cuda_cfg.get("preferred_model") or "auto").strip().lower()
        if preferred_cuda_model != "auto":
            out["preferred_internal_model"] = preferred_cuda_model
    out["device_preference"] = plan["device_preference"]
    out["supports_internal_diffusion"] = True
    out["supports_proxy_render"] = False
    return out


def _render_profiles_for_hardware(hw: dict[str, Any] | None = None) -> dict[str, Any]:
    hw = dict(hw or _hardware_profile())
    recommended_tier = str(hw.get("recommended_tier") or "draft")
    backend_family = str(hw.get("backend_family") or "cpu_only")
    profiles = {
        "laptop_safe": {
            "label": "Laptop-safe",
            "description": "Fastest and safest defaults for CPU-only and integrated-GPU systems.",
            "render_preset": "fast",
            "internal_render_tier": "draft",
            "resume_existing_frames": True,
        },
        "balanced_auto": {
            "label": "Balanced auto",
            "description": "Recommended general-purpose defaults that follow current hardware planning.",
            "render_preset": "balanced",
            "internal_render_tier": "auto",
            "resume_existing_frames": True,
        },
        "high_quality": {
            "label": "High quality",
            "description": "Higher output quality for stronger GPUs and patient renders.",
            "render_preset": "quality",
            "internal_render_tier": "quality",
            "resume_existing_frames": True,
        },
    }
    recommended_profile = "balanced_auto"
    if backend_family in {"cpu_only", "integrated_gpu"} or recommended_tier == "draft":
        recommended_profile = "laptop_safe"
    elif recommended_tier == "quality":
        recommended_profile = "high_quality"
    return {"ok": True, "recommended_profile": recommended_profile, "profiles": profiles, "hardware": hw}


@app.get("/v1/settings/render_profiles")
def render_profiles():
    return _render_profiles_for_hardware()


@app.get("/v1/hardware")
def hardware():
    hw = _hardware_profile()
    return {"ok": True, "hardware": hw, "render_tier_plan": _build_internal_render_plan(hw, requested_tier="auto")}


def _system_readiness_report() -> dict[str, Any]:
    """Shared Studio readiness report used by Settings and Setup."""
    return assess_system_readiness(
        ffmpeg_path=settings.ffmpeg_path,
        data_dir=settings.data_dir,
        models_dir=settings.models_dir,
        cache_dir=settings.cache_dir,
        logs_dir=settings.logs_dir,
        external_dir=settings.external_dir,
        check_ffmpeg=check_ffmpeg,
        check_runtime=check_backend_bundle,
        hardware_profile=_hardware_profile,
    )


def _baseline_metrics_report() -> dict[str, Any]:
    """Read-only baseline timing counters for Settings (P0-06 stub)."""
    return collect_baseline_metrics(hardware_probe=_hardware_profile)


def _render_provider_status(hw: dict[str, Any] | None = None) -> dict[str, Any]:
    hw = dict(hw or _hardware_profile())
    cfg = render_settings.get()
    video_cfg = dict(cfg.get("video") or {})
    cosmos_cfg = dict(cfg.get("cosmos") or {})
    azure_foundry_cfg = dict(cfg.get("azure_foundry") or {})
    firefly_cfg = dict(cfg.get("firefly") or {})
    stability_cfg = dict(cfg.get("stability") or {})
    imagineart_cfg = dict(cfg.get("imagineart") or {})
    directml_cfg = dict(cfg.get("directml") or {})
    cuda_cfg = dict(cfg.get("cuda") or {})
    has_nvidia_key = bool(
        secrets.get("nvidia_api_key") or os.getenv("EDMG_AI_NVIDIA_API_KEY") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
    )
    cosmos_enabled = bool(cosmos_cfg.get("enabled", True))
    cosmos_base_url_set = bool(str(cosmos_cfg.get("base_url") or "").strip())
    azure_foundry_enabled = bool(azure_foundry_cfg.get("enabled", True))
    azure_foundry_configured = bool(
        str(azure_foundry_cfg.get("endpoint_url") or "").strip()
        and str(azure_foundry_cfg.get("deployment_name") or "").strip()
    )
    has_azure_foundry_key = bool(secrets.get("azure_foundry_api_key"))
    has_stability_key = bool(secrets.get("stability_api_key"))
    has_imagineart_key = bool(secrets.get("imagineart_api_key") or os.getenv("IMAGINEART_API_KEY"))
    has_adobe_client_id = bool(secrets.get("adobe_client_id") or os.getenv("ADOBE_CLIENT_ID"))
    has_adobe_client_secret = bool(secrets.get("adobe_client_secret") or os.getenv("ADOBE_CLIENT_SECRET"))
    firefly_credentials_ok = has_adobe_client_id and has_adobe_client_secret
    firefly_enabled = bool(firefly_cfg.get("enabled"))
    stability_enabled = bool(stability_cfg.get("enabled"))
    imagineart_enabled = bool(imagineart_cfg.get("enabled"))
    stability_service = str(stability_cfg.get("service") or "sd3")
    stability_model = str(stability_cfg.get("model") or "sd3.5-large-turbo")
    directml_available = bool(hw.get("supports_directml"))
    directml_enabled = bool(directml_cfg.get("enabled"))
    cuda_available = bool(hw.get("cuda_runtime_ready"))
    cuda_enabled = bool(cuda_cfg.get("enabled", True))
    return {
        "ok": True,
        "settings": cfg,
        "firefly": {
            "provider": "adobe-firefly",
            "configured": firefly_credentials_ok,
            "enabled": firefly_enabled,
            "visible": bool(firefly_credentials_ok and firefly_enabled),
            "has_client_id": has_adobe_client_id,
            "has_client_secret": has_adobe_client_secret,
            "allow_auto_fallback": bool(firefly_cfg.get("allow_auto_fallback", True)),
            "custom_model_id": str(firefly_cfg.get("custom_model_id") or ""),
            "style": str(firefly_cfg.get("style") or "none"),
            "content_class": str(firefly_cfg.get("content_class") or "photo"),
            "strength": float(firefly_cfg.get("strength") or 0.6),
            "video_enabled": bool(firefly_cfg.get("video_enabled", False)),
            "video_duration_s": int(firefly_cfg.get("video_duration_s") or 5),
            "note": (
                "No Adobe credentials saved — add Client ID and Client Secret in Settings → Adobe Firefly."
                if not firefly_credentials_ok else
                "Adobe Firefly is configured. Set a custom_model_id to use your fine-tuned model."
                if not firefly_cfg.get("custom_model_id") else
                f"Adobe Firefly configured with custom model: {firefly_cfg['custom_model_id']}"
            ),
        },
        "cuda": {
            "provider": "pytorch-cuda",
            "available": cuda_available,
            "enabled": cuda_enabled,
            "active": bool(cuda_available and cuda_enabled and str(hw.get("backend") or "cpu").lower() == "cuda"),
            "device_name": hw.get("cuda_device_name") or hw.get("device_name"),
            "vram_gb": float(hw.get("cuda_vram_gb") or hw.get("vram_gb") or 0.0),
            "allow_auto_selection": bool(cuda_cfg.get("allow_auto_selection", True)),
            "preferred_model": str(cuda_cfg.get("preferred_model") or "auto"),
            "enable_tf32": bool(cuda_cfg.get("enable_tf32", True)),
            "optimize_comfyui": bool(cuda_cfg.get("optimize_comfyui", True)),
        },
        "stability": {
            "provider": "stability",
            "configured": bool(has_stability_key),
            "enabled": stability_enabled,
            "visible": bool(has_stability_key and stability_enabled),
            "has_api_key": has_stability_key,
            "allow_auto_fallback": bool(stability_cfg.get("allow_auto_fallback", True)),
            "service": stability_service,
            "model": stability_model,
            "style_preset": str(stability_cfg.get("style_preset") or "none"),
            "output_format": str(stability_cfg.get("output_format") or "png"),
            "supports_video_api": False,
            "note": "Studio uses the current public Stability image API for hosted keyframes, then assembles video locally. A public hosted video route was not found in the current API spec.",
        },
        "imagineart": {
            "provider": "imagineart",
            "configured": bool(has_imagineart_key),
            "enabled": imagineart_enabled,
            "visible": bool(has_imagineart_key and imagineart_enabled),
            "has_api_key": has_imagineart_key,
            "allow_auto_fallback": bool(imagineart_cfg.get("allow_auto_fallback", True)),
            "image_style": str(imagineart_cfg.get("image_style") or "imagine-turbo"),
            "video_style": str(imagineart_cfg.get("video_style") or "kling-1.0-pro"),
            "video_enabled": bool(imagineart_cfg.get("video_enabled", False)),
            "timeout_s": int(imagineart_cfg.get("timeout_s") or 600),
            "supports_video_api": True,
            "note": (
                "No ImagineArt API key saved — add one in Settings → Tokens → ImagineArt API key."
                if not has_imagineart_key else
                "ImagineArt is configured for hosted stills and optional native video clips."
            ),
        },
        "directml": {
            "provider": "onnxruntime-directml",
            "enabled": directml_enabled,
            "available": directml_available,
            "active": bool(directml_enabled and str(hw.get("backend") or "cpu").lower() == "directml"),
            "runtime_ready": bool(hw.get("directml_runtime_ready")),
            "device_name": hw.get("directml_device_name") or hw.get("device_name"),
            "preferred_model": str(directml_cfg.get("preferred_model") or "auto"),
            "allow_auto_selection": bool(directml_cfg.get("allow_auto_selection", True)),
        },
        "cosmos": {
            "provider": "nvidia-cosmos",
            # Cosmos video generation runs on a self-hosted NIM — it is only usable once a NIM
            # Base URL is set. (There is no hosted Cosmos video route on the NVIDIA API Catalog.)
            "configured": cosmos_base_url_set,
            "enabled": cosmos_enabled,
            "active": bool(cosmos_base_url_set and cosmos_enabled),
            "requires_nim": True,
            "has_nvidia_key": has_nvidia_key,
            "model": str(cosmos_cfg.get("model") or "cosmos3"),
            "model_size": str(cosmos_cfg.get("model_size") or "nano"),
            "models": list(COSMOS_MODELS.keys()),
            "steps": int(cosmos_cfg.get("steps") or 50),
            "guidance_scale": float(cosmos_cfg.get("guidance_scale") or 7.5),
            "num_frames": int(cosmos_cfg.get("num_frames") or 121),
            "fps": float(cosmos_cfg.get("fps") or 24.0),
            "prompt_upsampling": bool(cosmos_cfg.get("prompt_upsampling", True)),
            "base_url": str(cosmos_cfg.get("base_url") or ""),
            "timeout_s": int(cosmos_cfg.get("timeout_s") or 600),
            "note": (
                "Cosmos video runs on a self-hosted NVIDIA NIM. Start a Cosmos NIM (e.g. "
                "cosmos3-generator) on a CUDA GPU and set its URL in Settings → GPU / Render "
                "Runtime → Cosmos (Base URL), e.g. http://127.0.0.1:8000."
                if not cosmos_base_url_set else
                "Cosmos NIM configured. The Studio sends requests to {base}/v1/infer.".format(
                    base=str(cosmos_cfg.get("base_url") or "").rstrip("/")
                )
            ),
        },
        "azure_foundry": {
            "provider": "azure-foundry-cosmos",
            # Cosmos3 running as a managed-compute deployment on Azure AI Foundry —
            # a hosted alternative to the self-hosted Cosmos NIM above. Needs an
            # endpoint URL, a deployment name, and an API key.
            "configured": azure_foundry_configured,
            "enabled": azure_foundry_enabled,
            "active": bool(azure_foundry_configured and azure_foundry_enabled and has_azure_foundry_key),
            "has_api_key": has_azure_foundry_key,
            "allow_auto_fallback": bool(azure_foundry_cfg.get("allow_auto_fallback", False)),
            "endpoint_url": str(azure_foundry_cfg.get("endpoint_url") or ""),
            "deployment_name": str(azure_foundry_cfg.get("deployment_name") or ""),
            "resolution": str(azure_foundry_cfg.get("resolution") or "720_16_9"),
            "num_frames": int(azure_foundry_cfg.get("num_frames") or 121),
            "fps": float(azure_foundry_cfg.get("fps") or 24.0),
            "guidance_scale": float(azure_foundry_cfg.get("guidance_scale") or 7.0),
            "steps": int(azure_foundry_cfg.get("steps") or 50),
            "timeout_s": int(azure_foundry_cfg.get("timeout_s") or 600),
            "note": (
                "Not configured — set an Endpoint URL and Deployment name in Settings → "
                "Azure AI Foundry, then add an API key."
                if not azure_foundry_configured else
                "No API key saved — add one in Settings → Tokens → Azure Foundry API key."
                if not has_azure_foundry_key else
                "Azure AI Foundry configured. Requests go to {base}/managed-deployments/{name}/v1/messages.".format(
                    base=str(azure_foundry_cfg.get("endpoint_url") or "").rstrip("/"),
                    name=str(azure_foundry_cfg.get("deployment_name") or ""),
                )
            ),
        },
        "firefly_styles": list(FIREFLY_STYLES),
        "firefly_content_classes": list(FIREFLY_CONTENT_CLASSES),
        "stability_services": list(STABILITY_SERVICES),
        "stability_models": list(STABILITY_SD3_MODELS),
        "imagineart_image_styles": list(IMAGINEART_IMAGE_STYLES),
        "imagineart_video_styles": list(IMAGINEART_VIDEO_STYLES),
        "imagineart_aspect_ratios": list(IMAGINEART_ASPECT_RATIOS),
        "style_presets": list(STABILITY_STYLE_PRESETS),
        "hardware": hw,
    }


def _hosted_stability_ready(payload: dict[str, Any] | None = None) -> bool:
    payload = payload or {}
    provider = _render_provider_status().get("stability") or {}
    if not provider.get("configured") or not provider.get("enabled"):
        return False
    requested_mode = str(payload.get("render_mode") or "auto").strip().lower()
    if requested_mode == "hosted":
        return True
    return bool(provider.get("allow_auto_fallback")) and bool(payload.get("allow_hosted_fallback", True))


def _cosmos_client() -> CosmosClient:
    """Return a configured CosmosClient — uses the same NVIDIA key as Nemotron."""
    api_key = (
        secrets.get("nvidia_api_key")
        or os.getenv("EDMG_AI_NVIDIA_API_KEY")
        or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
        or ""
    )
    cosmos_cfg = dict((render_settings.get().get("cosmos") or {}))
    base_url = str(cosmos_cfg.get("base_url") or "").strip()
    timeout_s = float(cosmos_cfg.get("timeout_s") or 600)
    return CosmosClient(api_key=api_key, base_url=base_url, timeout_s=timeout_s)


def _azure_foundry_client() -> AzureFoundryClient:
    """Return a configured AzureFoundryClient for the hosted Cosmos3 managed-compute deployment."""
    api_key = secrets.get("azure_foundry_api_key") or os.getenv("EDMG_AI_AZURE_FOUNDRY_API_KEY") or ""
    azure_foundry_cfg = dict((render_settings.get().get("azure_foundry") or {}))
    endpoint_url = str(azure_foundry_cfg.get("endpoint_url") or "").strip()
    deployment_name = str(azure_foundry_cfg.get("deployment_name") or "").strip()
    timeout_s = float(azure_foundry_cfg.get("timeout_s") or 600)
    return AzureFoundryClient(
        api_key=api_key,
        endpoint_url=endpoint_url,
        deployment_name=deployment_name,
        timeout_s=timeout_s,
    )


def _firefly_client() -> FireflyClient:
    """Return a configured FireflyClient, reading credentials from secrets or env."""
    client_id = secrets.get("adobe_client_id") or os.getenv("ADOBE_CLIENT_ID") or ""
    client_secret = secrets.get("adobe_client_secret") or os.getenv("ADOBE_CLIENT_SECRET") or ""
    return FireflyClient(client_id=client_id, client_secret=client_secret)


def _imagineart_client() -> ImagineArtClient:
    api_key = secrets.get("imagineart_api_key") or os.getenv("IMAGINEART_API_KEY") or ""
    return ImagineArtClient(api_key=api_key)


def _hosted_firefly_ready(payload: dict[str, Any] | None = None) -> bool:
    payload = payload or {}
    provider = _render_provider_status().get("firefly") or {}
    if not provider.get("configured") or not provider.get("enabled"):
        return False
    requested_mode = str(payload.get("render_mode") or "auto").strip().lower()
    if requested_mode == "firefly":
        return True
    return bool(provider.get("allow_auto_fallback")) and bool(payload.get("allow_firefly_fallback", True))


@app.get("/v1/settings/render_providers")
def get_render_providers():
    return _render_provider_status()


@app.post("/v1/settings/render_providers")
def set_render_providers(payload: dict[str, Any]):
    saved = render_settings.update(payload)
    _hardware_profile_invalidate()  # CUDA enabled/disabled affects hardware profile
    return {
        "ok": True,
        "settings": saved,
        "status": _render_provider_status(),
    }


def _transcription_status() -> dict[str, Any]:
    cfg = transcription_settings.get()
    deps = transcription_dependency_status()
    nvidia_key_configured = bool(
        secrets.get("nvidia_api_key")
        or os.getenv("EDMG_AI_NVIDIA_API_KEY")
        or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
    )
    return {
        "ok": True,
        "settings": cfg,
        "active": {
            "provider": cfg.get("provider"),
            "model": cfg.get("model"),
            "device": cfg.get("device"),
            "compute_type": cfg.get("compute_type"),
            "fallback_to_whisper": bool(cfg.get("fallback_to_whisper", True)),
            "separate_vocals": bool(cfg.get("separate_vocals", False)),
            "separation_model": cfg.get("separation_model"),
        },
        "providers": list(TRANSCRIPTION_PROVIDERS),
        "whisper_models": list(WHISPER_MODELS),
        "parakeet_models": list(PARAKEET_MODELS),
        "parakeet_nim_models": list(PARAKEET_NIM_MODELS),
        "parakeet_nim_ready": bool(nvidia_key_configured and deps.get("parakeet_nim_available")),
        "parakeet_nim_api_key_configured": nvidia_key_configured,
        "devices": list(TRANSCRIPTION_DEVICES),
        "compute_types": list(TRANSCRIPTION_COMPUTE_TYPES),
        "dependencies": deps,
        "hardware": _hardware_profile(),
        "acceleration": {
            "asr_runtime": "faster-whisper uses CTranslate2; Parakeet local uses NeMo/PyTorch; Parakeet NIM is remote.",
            "tensorrt_image_bundle_applicable": False,
            "tensorrt_note": (
                "Studio's TensorRT bundle is for Stable Diffusion image/keyframe rendering only. "
                "It does not accelerate faster-whisper transcription. For local ASR speed, use device=cuda "
                "with compute=float16 or int8_float16, or use Parakeet/Parakeet NIM."
            ),
        },
    }


@app.get("/v1/settings/transcription")
def get_transcription_settings():
    return _transcription_status()


@app.post("/v1/settings/transcription")
def set_transcription_settings(payload: dict[str, Any]):
    saved = transcription_settings.update(payload)
    return {
        "ok": True,
        "settings": saved,
        "status": _transcription_status(),
    }


@app.get("/v1/codex/status")
def get_codex_status():
    return codex_sdk_status()


@app.post("/v1/projects/{project_id}/codex/render-review")
def codex_render_review(project_id: str, payload: dict[str, Any]):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    variant_index = int((payload or {}).get("variant_index") or 0)
    latest_render = proj.meta.get("last_internal_render") if isinstance(proj.meta, dict) else None
    if not isinstance(latest_render, dict):
        latest_render = {}
    return run_codex_render_review_task(
        project_dir=store.project_dir(project_id),
        project_id=project_id,
        variant_index=variant_index,
        latest_render=latest_render,
        prompt_extra=str((payload or {}).get("note") or ""),
    )


def _nvidia_prompt_model_family(model: str | None) -> str:
    raw = str(model or "").strip().lower()
    if "diffusiongemma" in raw or "diffusion-gemma" in raw:
        return "diffusiongemma"
    if "nemotron" in raw:
        return "nemotron"
    return "custom"


@app.get("/v1/config")
def get_config():
    provider_status = _render_provider_status()
    transcription_status = _transcription_status()
    try:
        from edmg_ai_service.config import (
            _OPENAI_COMPAT_DEFAULT_BASE_URL,
            _OPENAI_COMPAT_DEFAULT_MODEL,
            _NEMOTRON_ULTRA_MODEL,
            _NVIDIA_NIM_BASE_URL,
            _NVIDIA_PROMPT_MODEL_PRESETS,
            normalize_openai_compat_defaults,
        )
        nvidia_model_presets = [dict(item) for item in _NVIDIA_PROMPT_MODEL_PRESETS]
    except Exception:
        _OPENAI_COMPAT_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"
        _OPENAI_COMPAT_DEFAULT_MODEL = "nvidia/llama-3.1-nemotron-ultra-253b-v1"
        _NEMOTRON_ULTRA_MODEL = _OPENAI_COMPAT_DEFAULT_MODEL
        _NVIDIA_NIM_BASE_URL = _OPENAI_COMPAT_DEFAULT_BASE_URL
        def normalize_openai_compat_defaults(base_url: str | None, model: str | None) -> tuple[str, str]:
            base = (base_url or "").strip()
            selected_model = (model or "").strip()
            if (
                base == "http://127.0.0.1:8000"
                and selected_model in {"", "qwen3-8b"}
            ) or (not base and selected_model == "qwen3-8b"):
                return _OPENAI_COMPAT_DEFAULT_BASE_URL, _OPENAI_COMPAT_DEFAULT_MODEL
            return base or _OPENAI_COMPAT_DEFAULT_BASE_URL, selected_model or _OPENAI_COMPAT_DEFAULT_MODEL
        nvidia_model_presets = []
    nvidia_model = os.getenv("EDMG_AI_NVIDIA_MODEL", _NEMOTRON_ULTRA_MODEL).strip()
    openai_compat_base_url, openai_compat_model = normalize_openai_compat_defaults(
        os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL"),
        os.getenv("EDMG_AI_OPENAI_COMPAT_MODEL"),
    )
    return {
        "studio_home": str(settings.studio_home),
        "data_dir": str(settings.data_dir),
        "models_dir": str(settings.models_dir),
        "ollama_models_dir": str(settings.ollama_models_dir),
        "cache_dir": str(settings.cache_dir),
        "logs_dir": str(settings.logs_dir),
        "external_dir": str(settings.external_dir),
        "ai_mode": settings.ai_mode,
        "ai_base_url": settings.ai_base_url,
        "ai_timeout_s": settings.ai_timeout_s,
        "ai_provider": os.getenv("EDMG_AI_PROVIDER", "nemotron_cloud").strip().lower() or "nemotron_cloud",
        "ai_ollama_url": os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434").strip(),
        "ai_ollama_model": os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b").strip(),
        "ai_openai_compat_base_url": openai_compat_base_url,
        "ai_openai_compat_model": openai_compat_model,
        "ai_openai_compat_api_key_configured": bool(
            secrets.get("openai_compat_api_key") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
        ),
        "ai_nvidia_base_url": os.getenv("EDMG_AI_NVIDIA_BASE_URL", _NVIDIA_NIM_BASE_URL).strip(),
        "ai_nvidia_model": nvidia_model,
        "ai_nvidia_model_family": _nvidia_prompt_model_family(nvidia_model),
        "ai_nvidia_model_presets": nvidia_model_presets,
        "ai_nvidia_api_key_configured": bool(
            secrets.get("nvidia_api_key") or secrets.get("openai_compat_api_key")
            or os.getenv("EDMG_AI_NVIDIA_API_KEY") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
        ),
        "stability_api_key_configured": bool(secrets.get("stability_api_key")),
        "comfyui_url": settings.comfyui_url,
        "comfyui_urls": list(settings.resolved_comfyui_urls()),
        "comfyui_node_concurrency": settings.comfyui_node_concurrency,
        "comfyui_checkpoint": settings.comfyui_checkpoint,
        "ffmpeg_path": settings.ffmpeg_path,
        "worker_autostart": settings.worker_autostart,
        "worker_concurrency": settings.worker_concurrency,
        "worker_poll_interval_s": settings.worker_poll_interval_s,
        "secrets_store": secrets.status().store,
        "render_provider_settings": provider_status.get("settings"),
        "render_provider_status": provider_status,
        "transcription_settings": transcription_status.get("settings"),
        "transcription_status": transcription_status,
    }


@app.get("/v1/settings/secrets/status")
def secrets_status():
    """Return whether optional tokens are configured (never returns the values)."""
    st = secrets.status()
    hf_auth = describe_hf_auth(secrets_store=secrets)
    return {
        "ok": True,
        "store": st.store,
        "available": st.available,
        "has_hf_token": st.has_hf_token,
        "hf_auth_available": hf_auth["available"],
        "hf_auth_token_source": hf_auth["token_source"],
        "hf_modern_cli": hf_auth["modern_cli"],
        "hf_cli_available": hf_auth["cli_available"],
        "hf_login_command": hf_auth["login_command"],
        "hf_whoami_command": hf_auth["whoami_command"],
        "hf_token_command": hf_auth["token_command"],
        "has_civitai_api_key": st.has_civitai_api_key,
        "has_openai_compat_api_key": st.has_openai_compat_api_key,
        "has_stability_api_key": st.has_stability_api_key,
        "has_nvidia_api_key": st.has_nvidia_api_key,
        "note": st.note,
    }


@app.post("/v1/settings/secrets/set")
def secrets_set(payload: dict[str, Any]):
    name = str((payload or {}).get("name") or "").strip().lower()
    value = str((payload or {}).get("value") or "")
    if name not in _ALLOWED_SECRETS:
        raise UserFacingError(
            "Unknown secret",
            hint=f"Supported: {', '.join(sorted(_ALLOWED_SECRETS))}",
        )
    if not value:
        raise UserFacingError("Missing value", hint="Paste the token/key value, then click Save.")
    secrets.set(name, value)
    return {"ok": True}


@app.post("/v1/settings/secrets/clear")
def secrets_clear(payload: dict[str, Any]):
    name = str((payload or {}).get("name") or "").strip().lower()
    if name not in _ALLOWED_SECRETS:
        raise UserFacingError(
            "Unknown secret",
            hint=f"Supported: {', '.join(sorted(_ALLOWED_SECRETS))}",
        )
    secrets.delete(name)
    return {"ok": True}


def _setup_ai_config() -> dict[str, Any]:
    from edmg_ai_service.config import (
        _OPENAI_COMPAT_DEFAULT_BASE_URL,
        _OPENAI_COMPAT_DEFAULT_MODEL,
        _NEMOTRON_ULTRA_MODEL,
        _NVIDIA_NIM_BASE_URL,
        _NVIDIA_PROMPT_MODEL_PRESETS,
        normalize_openai_compat_defaults,
    )

    ai_mode = (settings.ai_mode or "local").strip().lower() or "local"
    ai_provider = (os.getenv("EDMG_AI_PROVIDER", "nemotron_cloud").strip().lower() or "nemotron_cloud")
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model = os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b")
    openai_compat_base_url, openai_compat_model = normalize_openai_compat_defaults(
        os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL"),
        os.getenv("EDMG_AI_OPENAI_COMPAT_MODEL"),
    )
    openai_compat_api_key_configured = bool(
        secrets.get("openai_compat_api_key") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
    )
    nvidia_api_key_configured = bool(
        secrets.get("nvidia_api_key") or secrets.get("openai_compat_api_key")
        or os.getenv("EDMG_AI_NVIDIA_API_KEY") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
    )
    nemotron_base_url = os.getenv("EDMG_AI_NVIDIA_BASE_URL", _NVIDIA_NIM_BASE_URL)
    nemotron_model = os.getenv("EDMG_AI_NVIDIA_MODEL", _NEMOTRON_ULTRA_MODEL)
    nvidia_model_family = _nvidia_prompt_model_family(nemotron_model)
    nvidia_presets = [dict(item) for item in _NVIDIA_PROMPT_MODEL_PRESETS]

    if ai_mode in ("http", "remote"):
        return {
            "mode": "http",
            "provider": "remote_ai_service",
            "label": "Remote AI service",
            "ollama_required": False,
            "model_required": False,
            "base_url": settings.ai_base_url,
            "hint": "Studio planning is configured to call a separate EDMG AI service over HTTP.",
        }

    if ai_provider in ("nemotron_cloud", "nvidia_nim", "nemotron"):
        missing_key_warning = (
            None if nvidia_api_key_configured else
            "No NVIDIA API key saved. Planning will fall back to rule-based mode. "
            "Add your key in Settings → AI Provider → NVIDIA API key."
        )
        if nvidia_model_family == "diffusiongemma":
            label = "DiffusionGemma (NVIDIA / OpenAI-compatible)"
            hint = (
                "DiffusionGemma is used for planning and prompt text through an OpenAI-compatible "
                "NVIDIA NIM or vLLM endpoint. It does not replace SVD, AnimateDiff, or Cosmos video generation."
            )
        else:
            label = "Nemotron Ultra (NVIDIA Cloud)"
            hint = "Studio planning uses NVIDIA Nemotron Ultra via the NVIDIA NIM cloud API."
        return {
            "mode": "local",
            "provider": "nvidia_nim",
            "label": label,
            "ollama_required": False,
            "model_required": False,
            "base_url": nemotron_base_url,
            "model": nemotron_model,
            "model_family": nvidia_model_family,
            "model_presets": nvidia_presets,
            "nvidia_api_key_configured": nvidia_api_key_configured,
            "warning": missing_key_warning,
            "hint": hint,
            "nvidia_studio_driver_note": (
                "NVIDIA Studio Driver updates can improve local CUDA/NIM/vLLM runtime behavior, "
                "but Studio still calls the configured OpenAI-compatible endpoint for this planner model."
            ),
        }

    if ai_provider in ("openai_compat", "openai-compatible", "openai"):
        return {
            "mode": "local",
            "provider": "openai_compat",
            "label": "OpenAI-compatible endpoint",
            "ollama_required": False,
            "model_required": False,
            "base_url": openai_compat_base_url,
            "model": openai_compat_model,
            "openai_compat_api_key_configured": openai_compat_api_key_configured,
            "hint": "Studio planning is configured for an OpenAI-compatible endpoint instead of Ollama.",
        }

    if ai_provider == "rule_based":
        return {
            "mode": "local",
            "provider": "rule_based",
            "label": "Rule-based fallback",
            "ollama_required": False,
            "model_required": False,
            "hint": "Studio planning is configured for the built-in rule-based fallback. Ollama is optional.",
        }

    return {
        "mode": "local",
        "provider": "ollama",
        "label": "Local Ollama",
        "ollama_required": True,
        "model_required": True,
        "base_url": ollama_url,
        "model": ollama_model,
        "hint": "Studio planning is configured for local Ollama.",
    }


def _compute_setup_status(*, include_optional: bool = False) -> dict[str, Any]:
    """Installer GUI status for required components."""
    is_windows = platform.system() == "Windows"
    ai_config = _setup_ai_config()
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model = os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b")
    should_probe_ollama = bool(ai_config.get("ollama_required") or include_optional)
    if should_probe_ollama:
        ollama = check_ollama(ollama_url, ollama_model)
    else:
        ollama = {
            "ok": False,
            "model_present": False,
            "url": ollama_url,
            "model": ollama_model,
            "optional": True,
            "skipped": True,
            "hint": (
                f"Ollama health probing is paused because Studio AI is configured for "
                f"{ai_config.get('label') or ai_config.get('provider') or 'another provider'}."
            ),
        }
    ollama_exe = None
    ollama_exe_error = None
    try:
        ollama_exe = _find_ollama_exe(settings.external_dir)
    except Exception:
        logger.exception("Ollama executable discovery failed")
        ollama_exe_error = "Ollama executable discovery failed"
    ollama["managed_models_dir"] = str(settings.ollama_models_dir)
    ollama["managed_launch_script"] = str(managed_ollama_launch_script_path(settings.external_dir))
    ollama["launch_available"] = bool(ollama_exe)
    ollama["ollama_exe"] = ollama_exe
    ollama["managed_running"] = bool(ollama_managed.running())
    if ollama_exe_error and not ollama.get("ok"):
        ollama["launch_hint"] = ollama_exe_error
    elif ollama_exe and not ollama.get("ok"):
        ollama["hint"] = (
            f"Studio can start Ollama with models stored under {settings.ollama_models_dir}. "
            "Use Start Studio-managed Ollama, or run the helper script after installing Ollama."
        )
    elif not ollama.get("ok"):
        ollama["hint"] = (
            (
                f"Studio can install Ollama into {settings.external_dir / 'ollama'} and keep models under "
                f"{settings.ollama_models_dir}."
            )
            if is_windows
            else (
                "Run the Linux sidecar setup from this wizard, install Ollama system-wide, or set EDMG_OLLAMA_PATH "
                "to your ollama binary, then point Studio at the running Ollama service."
                if platform.system().lower() == "linux"
                else "Install Ollama system-wide, or set EDMG_OLLAMA_PATH to your ollama binary, then point Studio at the running Ollama service."
            )
        )

    # ComfyUI availability
    try:
        resolved_checkpoint, fallback_from = _resolve_comfy_checkpoint_name(
            settings.comfyui_checkpoint,
            allow_auto_fallback=True,
        )
        diag = comfy_pool.diagnose({"checkpoint": resolved_checkpoint})
        comfy_ok = bool(diag.get("compatible") or diag.get("busy_compatible"))
        if comfy_ok and fallback_from:
            comfy_hint = (
                f"Configured checkpoint `{fallback_from}` is unavailable; Studio will use `{resolved_checkpoint}` until the configured checkpoint is installed."
            )
        else:
            comfy_hint = None if comfy_ok else (
                "Install and start ComfyUI (Portable) or ComfyUI Desktop, then ensure it is reachable at the configured URL(s)."
                if is_windows
                else (
                    "Install and start the Linux ComfyUI sidecar from this wizard, or point Studio at another reachable ComfyUI URL."
                    if platform.system().lower() == "linux"
                    else "Install and start ComfyUI, then ensure it is reachable at the configured URL(s)."
                )
            )
        comfy_status = {
            "ok": comfy_ok,
            "url": settings.resolved_comfyui_urls()[0] if settings.resolved_comfyui_urls() else settings.comfyui_url,
            "checkpoint": resolved_checkpoint,
            "requested_checkpoint": settings.comfyui_checkpoint,
            "checkpoint_fallback_from": fallback_from,
            "diagnose": diag,
            "portable_installed": comfy_portable_installed(settings.external_dir, settings.data_dir),
            "hint": comfy_hint,
        }
    except Exception:
        logger.exception("ComfyUI setup status check failed")
        comfy_status = {
            "ok": False,
            "url": settings.comfyui_url,
            "checkpoint": settings.comfyui_checkpoint,
            "portable_installed": comfy_portable_installed(settings.external_dir, settings.data_dir),
            "error": "ComfyUI setup status check failed",
            "hint": (
                "Configure EDMG_COMFYUI_URL to a running ComfyUI instance, or install ComfyUI Portable via this wizard."
                if is_windows
                else (
                    "Configure EDMG_COMFYUI_URL to a running ComfyUI instance, or install the Linux ComfyUI sidecar via this wizard."
                    if platform.system().lower() == "linux"
                    else "Configure EDMG_COMFYUI_URL to a running ComfyUI instance."
                )
            ),
        }

    readiness = _system_readiness_report()
    ff = dict((readiness.get("checks") or {}).get("ffmpeg") or check_ffmpeg(settings.ffmpeg_path))
    toolchain = check_backend_bundle()
    edmg = core_status()
    if not edmg.get("available"):
        edmg.setdefault(
            "hint",
            "Studio backend installs should include EDMG Core by default. Use this wizard to repair the backend environment if Core is missing.",
        )

    
    # 7-Zip CLI (needed to extract some .7z archives, e.g., ComfyUI Portable BCJ2)
    if not is_windows:
        seven = {
            "ok": True,
            "path": shutil.which("7z") or shutil.which("7zz"),
            "hint": "Portable 7-Zip install is only needed for the Windows ComfyUI Portable workflow.",
        }
    else:
        try:
            seven_path = _find_7z_exe(settings.external_dir, settings.data_dir)
            seven = {"ok": True, "path": seven_path, "hint": None}
        except Exception:
            seven = {"ok": False, "path": None, "hint": "Download the portable 7-Zip CLI into the Studio external tools folder, or set EDMG_7Z_PATH."}

    hw = _hardware_profile()
    return {
            "ok": True,
            "ai_config": ai_config,
            "toolchain": toolchain,
            # Temporary response alias for desktop clients predating UV-01.
            "backend_bundle": toolchain,
            "ollama": ollama,
            "comfyui": comfy_status,
            "ffmpeg": ff,
            "edmg": edmg,
            "sevenzip": seven,
            "hardware": hw,
            "system_readiness": readiness,
        }


_SETUP_STATUS_CACHE_TTL_S = 30.0
_setup_status_cache_lock = threading.Lock()
_setup_status_cache: dict[bool, tuple[float, dict[str, Any]]] = {}


def _clear_setup_status_cache() -> None:
    with _setup_status_cache_lock:
        _setup_status_cache.clear()


@app.get("/v1/setup/status")
def setup_status(refresh: bool = False, include_optional: bool = False):
    """Return cached setup diagnostics plus the current lightweight task list."""
    now = time.monotonic()
    cache_key = bool(include_optional)
    cached = False
    with _setup_status_cache_lock:
        entry = _setup_status_cache.get(cache_key)
        if not refresh and entry and now - entry[0] < _SETUP_STATUS_CACHE_TTL_S:
            checked_at, payload = entry
            result = deepcopy(payload)
            cached = True
        else:
            result = _compute_setup_status(include_optional=include_optional)
            checked_at = time.monotonic()
            _setup_status_cache[cache_key] = (checked_at, deepcopy(result))

    result["tasks"] = [task.to_dict() for task in setup_tasks.list()[:10]]
    result["status_cache"] = {
        "cached": cached,
        "age_seconds": round(max(0.0, time.monotonic() - checked_at), 3),
        "ttl_seconds": _SETUP_STATUS_CACHE_TTL_S,
    }
    return result


@app.get("/v1/setup/tasks")
def setup_task_list():
    """Lightweight progress endpoint; never runs external dependency probes."""
    tasks = [task.to_dict() for task in setup_tasks.list()[:10]]
    return {
        "ok": True,
        "active": any(task["status"] in ("queued", "running") for task in tasks),
        "tasks": tasks,
    }


@app.post("/v1/setup/tasks/{task_id}/cancel")
def setup_task_cancel(task_id: str):
    task = setup_tasks.cancel(task_id)
    if task is None:
        raise HTTPException(404, f"Setup task not found: {task_id}")
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/ollama/install_managed")
def setup_ollama_install_managed():
    dest = settings.external_dir / "_installers"
    url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    task = setup_tasks.start(
        "install_managed_ollama",
        download_and_install_ollama,
        dest,
        settings.external_dir,
        settings.models_dir,
        url,
    )
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/ollama/download_and_run")
def setup_ollama_download_and_run():
    return setup_ollama_install_managed()


@app.post("/v1/setup/ollama/start_managed")
def setup_ollama_start_managed():
    url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    task = setup_tasks.start(
        "start_managed_ollama",
        ollama_managed.start,
        settings.external_dir,
        settings.models_dir,
        url,
    )
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/ollama/pull")
def setup_ollama_pull(payload: dict[str, Any]):
    import os

    model = (payload or {}).get("model") or os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b")
    url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    task = setup_tasks.start(f"pull_model:{model}", pull_ollama_model, url, model)
    return {"ok": True, "task": task.to_dict()}

@app.post("/v1/setup/7zip/install")
def setup_7zip_install():
    """Download the portable 7-Zip CLI (required for extracting some .7z archives)."""
    task = setup_tasks.start("install_7zip", download_and_install_7zip, settings.external_dir, settings.data_dir)
    return {"ok": True, "task": task.to_dict()}

@app.post("/v1/setup/backend/install")
def setup_backend_install(payload: dict[str, Any]):
    try:
        profile = resolve_setup_accelerator_profile(payload)
    except ToolchainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    status = check_backend_bundle(accelerator_profile=profile, check_sync=False)
    if status.get("immutable"):
        detail = str(
            status.get("hint")
            or "This packaged backend is self-contained; install another application build to change profiles."
        )
        raise HTTPException(status_code=409, detail=detail)

    task = setup_tasks.start(
        f"sync_backend_profile:{profile}",
        install_backend_bundle,
        accelerator_profile=profile,
    )
    return {"ok": True, "task": task.to_dict()}

@app.post("/v1/setup/full/install")
def setup_full_install(payload: dict[str, Any]):
    """Run one-click setup around one locked backend accelerator profile."""
    import os

    try:
        profile = resolve_setup_accelerator_profile(payload)
    except ToolchainError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    toolchain = check_backend_bundle(accelerator_profile=profile, check_sync=False)
    if toolchain.get("immutable") and not toolchain.get("ok"):
        raise HTTPException(
            status_code=409,
            detail=str(toolchain.get("hint") or "The packaged backend profile does not match this setup request."),
        )

    comfy_flavor = {"cpu": "cpu", "directml": "amd", "cuda": "nvidia"}[profile]
    port = int((payload or {}).get("comfy_port") or 8188)
    model = (payload or {}).get("model") or os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen3:8b")
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ai_config = _setup_ai_config()

    def _run(task):
        # 1) Source checkouts sync from uv.lock; packaged backends are immutable.
        SetupTaskManager.check_canceled(task, "Full setup canceled.")
        install_backend_bundle(task, accelerator_profile=profile)

        # 2) Ensure 7-Zip for .7z extraction
        SetupTaskManager.check_canceled(task, "Full setup canceled.")
        try:
            _find_7z_exe(settings.external_dir, settings.data_dir)
        except Exception:
            download_and_install_7zip(task, settings.external_dir, settings.data_dir)

        # 3) Ollama install/model only when the active AI path actually uses Ollama.
        SetupTaskManager.check_canceled(task, "Full setup canceled.")
        if ai_config.get("ollama_required"):
            ollama_status = check_ollama(ollama_url, model)
            if not ollama_status.get("ok"):
                try:
                    ollama_managed.start(task, settings.external_dir, settings.models_dir, ollama_url)
                except Exception:
                    dest = settings.external_dir / "_installers"
                    download_and_install_ollama(task, dest, settings.external_dir, settings.models_dir, ollama_url)
                    ollama_managed.start(task, settings.external_dir, settings.models_dir, ollama_url)
            else:
                SetupTaskManager.log(task, "Ollama is already reachable.")

            ollama_status = check_ollama(ollama_url, model)
            if not ollama_status.get("model_present"):
                pull_ollama_model(task, ollama_url, model)
            else:
                SetupTaskManager.log(task, f"Ollama model {model} is already present.")
        else:
            SetupTaskManager.log(
                task,
                f"Skipping Ollama install because Studio AI is configured for {ai_config.get('label')}.",
            )

        # 4) ComfyUI Portable install + start
        SetupTaskManager.check_canceled(task, "Full setup canceled.")
        if not comfy_portable_installed(settings.external_dir, settings.data_dir):
            download_and_extract_portable(
                task,
                settings.external_dir,
                comfy_flavor,
                settings.data_dir,
                settings.models_dir,
            )
        else:
            SetupTaskManager.log(task, "ComfyUI Portable is already installed.")

        comfy_ready = False
        try:
            diag = comfy_pool.diagnose({})
            comfy_ready = bool(diag.get("compatible") or diag.get("busy_compatible"))
        except Exception:
            comfy_ready = False

        if comfy_ready:
            SetupTaskManager.log(task, "ComfyUI is already reachable.")
        else:
            comfy_portable.start(
                task,
                settings.external_dir,
                comfy_flavor,
                "127.0.0.1",
                port,
                settings.data_dir,
                settings.models_dir,
            )

    task = setup_tasks.start(f"full_setup:{profile}:{ai_config.get('provider')}", _run)
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/comfyui/portable/install")
def setup_comfyui_portable_install(payload: dict[str, Any]):
    flavor = (payload or {}).get("flavor") or "cpu"
    task = setup_tasks.start(
        f"install_comfyui_portable:{flavor}",
        download_and_extract_portable,
        settings.external_dir,
        flavor,
        settings.data_dir,
        settings.models_dir,
    )
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/comfyui/portable/start")
def setup_comfyui_portable_start(payload: dict[str, Any]):
    raw_flavor = str((payload or {}).get("flavor") or "auto").strip().lower()
    if raw_flavor == "auto":
        hw = _hardware_profile()
        cuda_cfg = dict((render_settings.get().get("cuda") or {}))
        if str(hw.get("backend") or "cpu").lower() == "cuda" and bool(cuda_cfg.get("enabled", True)):
            raw_flavor = "nvidia"
        else:
            raw_flavor = "cpu"
    flavor = raw_flavor
    port = int((payload or {}).get("port") or 8188)
    task = setup_tasks.start(
        f"start_comfyui_portable:{flavor}",
        comfy_portable.start,
        settings.external_dir,
        flavor,
        "127.0.0.1",
        port,
        settings.data_dir,
        settings.models_dir,
    )
    return {"ok": True, "task": task.to_dict()}


@app.post("/v1/setup/comfyui/portable/stop")
def setup_comfyui_portable_stop():
    comfy_portable.stop()
    return {"ok": True}


@app.post("/v1/setup/edmg/install")
def setup_edmg_install(payload: dict[str, Any]):
    mode = str((payload or {}).get("mode") or "standard").strip().lower() or "standard"
    backend = str((payload or {}).get("backend") or "cpu").strip().lower() or "cpu"
    task = setup_tasks.start(f"install_edmg_core:{mode}:{backend}", edmg_install_core, settings.data_dir, mode=mode, backend=backend)
    return {"ok": True, "task": task.to_dict()}


@app.get("/v1/ai/status")
def ai_status():
    return {"ok": True, "ai": ai.status(), "ai_config": _setup_ai_config()}

@app.get("/v1/worker/status")
def worker_status():
    if worker is None:
        return {"ok": True, "running": False}
    st = worker.status()
    return {"ok": True, **st.__dict__}

@app.get("/v1/comfyui/nodes")
def comfyui_nodes():
    return {"ok": True, "nodes": comfy_pool.snapshot()}


@app.get("/v1/comfyui/object_info")
def comfyui_object_info():
    try:
        primary = settings.resolved_comfyui_urls()[0]
        return comfy.get_object_info(primary)
    except Exception as exc:
        logger.exception("ComfyUI node discovery failed")
        raise HTTPException(502, "ComfyUI node discovery failed") from exc

@app.get("/v1/comfyui/capabilities")
def comfyui_capabilities():
    try:
        primary = settings.resolved_comfyui_urls()[0]
        obj = comfy.get_object_info(primary)
    except Exception as exc:
        logger.exception("ComfyUI queue discovery failed")
        raise HTTPException(502, "ComfyUI queue discovery failed") from exc

    ad_ok, ad_missing = comfy.has_nodes(obj, ["ADE_AnimateDiffLoaderGen1", "ADE_StandardStaticContextOptions"])
    svd_ok, svd_missing = comfy.has_nodes(obj, ["SVDSimpleImg2Vid"])
    controlnet_ok, controlnet_missing = comfy.has_nodes(obj, ["LoadImage", "ControlNetLoader", "ControlNetApplyAdvanced"])
    detected_checkpoints = sorted(
        list(set(comfy_pool._extract_checkpoint_names(obj)[0]))  # type: ignore[attr-defined]
    )
    return {
        "comfyui_url": settings.comfyui_url,
        "comfyui_urls": list(settings.resolved_comfyui_urls()),
        "comfyui_node_concurrency": settings.comfyui_node_concurrency,
        "animatediff": {"available": ad_ok, "missing_nodes": ad_missing},
        "svd": {"available": svd_ok, "missing_nodes": svd_missing},
        "controlnet": {"available": controlnet_ok, "missing_nodes": controlnet_missing},
        "detected_checkpoints": detected_checkpoints,
    }

@app.get("/v1/edmg/status")
def edmg_status():
    return core_status()

@app.post("/v1/edmg/verify")
def edmg_verify():
    return edmg_selfcheck()

@app.get("/v1/edmg/deforum_template")
def edmg_template():
    try:
        return edmg_deforum_template()
    except Exception:
        # Not fatal; return minimal template so UI doesn't crash
        return {"note": "EDMG Core not installed or template unavailable."}

# Core project list/create/get/health/timeline/autosave/recovery routes are registered
# via create_project_router() after _project_response_payload is defined.


@app.get("/v1/projects/{project_id}/visual_dna")
def get_project_visual_dna(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    dna = _load_project_visual_dna(proj)
    traits = [
        {
            "id": visual_dna_trait_id(str(trait.scope), trait.value),
            **trait.model_dump(mode="json"),
        }
        for trait in dna.trait_memory
    ]
    return {
        "ok": True,
        "visual_dna": dna.model_dump(mode="json"),
        "traits": traits,
        "prompt_hints": build_visual_dna_prompt_hints(dna),
    }


@app.get("/v1/projects/{project_id}/health/relink")
def get_project_relink_suggestions(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    return suggest_relinks(store.project_dir(project_id), proj.meta)


@app.post("/v1/projects/{project_id}/health/collect")
def post_collect_project(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    dest = pdir.parent / f"{project_id}_collect_{time.strftime('%Y%m%d-%H%M%S')}"
    return collect_project_bundle(pdir, dest)


@app.post("/v1/projects/{project_id}/visual_dna/feedback")
def post_project_visual_dna_feedback(project_id: str, req: VisualDNAFeedbackRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    dna = _load_project_visual_dna(proj)
    updated = record_visual_dna_feedback(dna, feedback=req.feedback)
    saved = _save_project_visual_dna(proj, updated)
    return {
        "ok": True,
        "visual_dna": saved.model_dump(mode="json"),
        "prompt_hints": build_visual_dna_prompt_hints(saved),
    }


@app.post("/v1/projects/{project_id}/visual_dna/update")
def post_project_visual_dna_update(project_id: str, req: VisualDNAUpdateRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    dna = _load_project_visual_dna(proj)
    updated = update_visual_dna(
        dna,
        identity=req.identity,
        continuity=req.continuity,
        approve_trait_ids=list(req.approve_trait_ids or []),
        deprecate_trait_ids=list(req.deprecate_trait_ids or []),
        notes=req.notes,
    )
    saved = _save_project_visual_dna(proj, updated)
    traits = [
        {
            "id": visual_dna_trait_id(str(trait.scope), trait.value),
            **trait.model_dump(mode="json"),
        }
        for trait in saved.trait_memory
    ]
    return {
        "ok": True,
        "visual_dna": saved.model_dump(mode="json"),
        "traits": traits,
        "prompt_hints": build_visual_dna_prompt_hints(saved),
    }


def _validate_preview_request(kind: str, query: dict[str, Any]) -> None:
    try:
        validate_preview(kind, query)
    except (ValueError, TypeError, OverflowError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _preview_path(project_dir: Path, relative: str) -> Path:
    try:
        return safe_join(project_dir, relative)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _validate_preview_timeline(project_dir: Path, timeline: dict) -> None:
    try:
        validate_timeline_media(project_dir, timeline)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.head("/v1/projects/{project_id}/preview/frame", include_in_schema=False)
@app.get("/v1/projects/{project_id}/preview/frame")
def preview_frame(project_id: str, t: float = 0.0, w: int = 768, h: int = 432, force: int = 0):
    """Render a low-res cached preview frame for timeline scrubbing (no diffusion)."""
    _validate_preview_request("frame", {"t": t, "w": w, "h": h})
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}
    _validate_preview_timeline(pdir, timeline)

    try:
        from PIL import Image  # type: ignore
    except Exception as exc:
        logger.exception("Preview image dependency is unavailable")
        raise HTTPException(500, "Preview image dependency is unavailable") from exc

    cache_dir = _preview_path(pdir, f"outputs/previews/{int(w)}x{int(h)}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"t{int(float(t) * 1000):010d}.png"
    out = _preview_path(cache_dir, key)

    if out.exists() and not force:
        return FileResponse(str(out), media_type="image/png")

    base = Image.new("RGB", (int(w), int(h)), color=(18, 18, 22))
    try:
        img = apply_timeline_layers(base, project_dir=pdir, timeline=timeline, t=float(t))
    except Exception:
        img = base
    img.save(out)
    

    return FileResponse(str(out), media_type="image/png")
@app.head("/v1/projects/{project_id}/preview/segment", include_in_schema=False)
@app.get("/v1/projects/{project_id}/preview/segment")
def preview_segment(
    project_id: str,
    start_s: float = 0.0,
    end_s: float = 5.0,
    w: int = 768,
    h: int = 432,
    fps: int = 6,
    force: int = 0,
):
    """Render a low-res cached proxy preview clip for timeline scrubbing (no diffusion).

    This is intended for fast iteration:
      - overlays/text/masks are applied (same compositor as internal render)
      - audio is not muxed (UI plays audio separately)

    Cache key includes a hash of the current timeline.
    """
    _validate_preview_request("segment", {"start_s": start_s, "end_s": end_s, "w": w, "h": h, "fps": fps})
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}
    _validate_preview_timeline(pdir, timeline)

    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception as exc:
        logger.exception("Preview image dependency is unavailable")
        raise HTTPException(500, "Preview image dependency is unavailable") from exc

    start = max(0.0, float(start_s))
    end = max(start + 0.05, float(end_s))
    # protect the server: cap clip length
    end = min(end, start + 30.0)
    fps_i = max(1, min(24, int(fps)))

    tl_hash = hashlib.sha1(json.dumps(timeline, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    cache_dir = _preview_path(pdir, f"outputs/previews/seg_{int(w)}x{int(h)}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"seg_{int(start*1000):010d}_{int(end*1000):010d}_{fps_i}fps_{tl_hash}.mp4"
    out_mp4 = _preview_path(cache_dir, key)

    if out_mp4.exists() and not force:
        return FileResponse(str(out_mp4), media_type="video/mp4")

    frames_dir = _preview_path(cache_dir, f"_tmp_{out_mp4.stem}")
    if frames_dir.exists():
        try:
            for f in frames_dir.glob("*.png"):
                f.unlink(missing_ok=True)
        except Exception:
            pass
    frames_dir.mkdir(parents=True, exist_ok=True)

    n = int(math.ceil((end - start) * fps_i))
    font = None
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    for i in range(n):
        t = start + (i / fps_i)
        base = Image.new("RGB", (int(w), int(h)), color=(18, 18, 22))
        # small time stamp (helps debugging scrubs)
        try:
            d = ImageDraw.Draw(base)
            d.text((10, 10), f"t={t:.2f}s", fill=(240, 240, 240), font=font)
        except Exception:
            pass
        try:
            img = apply_timeline_layers(base, project_dir=pdir, timeline=timeline, t=float(t))
        except Exception:
            img = base
        img.save(frames_dir / f"frame_{i:06d}.png")

    assemble_image_sequence(
        ffmpeg_path=settings.ffmpeg_path,
        frames_dir=frames_dir,
        out_mp4=out_mp4,
        fps=fps_i,
        glob_pattern="frame_*.png",
        audio_path=None,
    )

    # cleanup tmp frames (keep only mp4)
    try:
        for f in frames_dir.glob("*.png"):
            f.unlink(missing_ok=True)
        frames_dir.rmdir()
    except Exception:
        pass

    return FileResponse(str(out_mp4), media_type="video/mp4")






@app.head("/v1/projects/{project_id}/preview/diffusion_segment", include_in_schema=False)
@app.get("/v1/projects/{project_id}/preview/diffusion_segment")
def preview_diffusion_segment(
    project_id: str,
    start_s: float = 0.0,
    end_s: float = 2.0,
    w: int = 512,
    h: int = 512,
    fps: int = 2,
    steps: int = 6,
    cfg: float = 7.0,
    strength: float = 0.45,
    model_id: str = "auto",
    variant_index: int = 0,
    seed: int = 1337,
    prompt: str | None = None,
    force: int = 0,
):
    """Render a short cached diffusion preview clip (low-cost 'look' preview).

    Notes:
      - capped length to protect slow machines
      - no audio mux (Timeline page plays audio separately)
      - uses the internal Diffusers engine (SD1.5 / SDXL / SD3.5) if installed
    """
    _validate_preview_request("diffusion_segment", {"start_s": start_s, "end_s": end_s, "w": w, "h": h, "fps": fps, "steps": steps, "cfg": cfg, "strength": strength})
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}
    _validate_preview_timeline(pdir, timeline)

    # Scenes from last plan are optional; timeline prompt track takes precedence anyway.
    scenes: list[dict[str, Any]] = []
    try:
        plan = proj.meta.get("last_plan") or {}
        vars_ = plan.get("variants") if isinstance(plan, dict) else None
        if isinstance(vars_, list) and vars_:
            vi = max(0, min(int(variant_index), len(vars_) - 1))
            scenes = (vars_[vi] or {}).get("scenes") or []
            if not isinstance(scenes, list):
                scenes = []
    except Exception:
        scenes = []

    start = max(0.0, float(start_s))
    end = max(start + 0.05, float(end_s))
    end = min(end, start + 10.0)

    fps_i = max(1, min(12, int(fps)))
    steps_i = max(1, min(30, int(steps)))
    w_i = int(w)
    h_i = int(h)

    # Resolve internal model
    mid = str(model_id or "auto")
    if mid == "auto":
        preferred = _hardware_profile().get("preferred_internal_model") or "hf_sd15_internal"
        mid = preferred
        if _resolve_installed_model_path(mid, materialize_remote=True) is None:
            # fallback
            mid = "hf_sd15_internal" if preferred != "hf_sd15_internal" else "hf_sdxl_internal"
    model_dir = _resolve_installed_model_path(mid, materialize_remote=True)
    if not model_dir or not model_dir.exists():
        raise UserFacingError(
            "Internal model is not installed.",
            hint="Go to Models and install an internal model such as SD 1.5, SDXL, or SD3.5 Medium, then retry.",
            code="MODEL_MISSING",
            status_code=400,
        )

    # Cache
    tl_hash = hashlib.sha1(json.dumps(timeline, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    p_hash = hashlib.sha1((prompt or "").encode("utf-8")).hexdigest()[:8]
    cache_dir = _preview_path(pdir, f"outputs/previews/diff_{w_i}x{h_i}")
    cache_dir.mkdir(parents=True, exist_ok=True)
    model_cache_token = _cache_key_token(mid)
    key = f"diff_{int(start*1000):010d}_{int(end*1000):010d}_{fps_i}fps_{steps_i}s_{int(cfg*10):03d}c_{int(strength*100):03d}st_{model_cache_token}_{tl_hash}_{p_hash}.mp4"
    out_mp4 = _preview_path(cache_dir, key)

    if out_mp4.exists() and not force:
        return FileResponse(str(out_mp4), media_type="video/mp4")

    s = InternalVideoSettings(
        fps_render=fps_i,
        fps_output=fps_i,
        width=w_i,
        height=h_i,
        steps=steps_i,
        cfg=float(cfg),
        interpolation_engine="fps",
        model_id=mid,
        temporal_mode="frame_img2img",
        temporal_strength=float(strength),
    )

    render_internal_diffusion_preview_segment(
        ffmpeg_path=settings.ffmpeg_path,
        project_dir=pdir,
        scenes=scenes,
        model_dir=Path(model_dir),
        settings=s,
        timeline=timeline,
        start_s=start,
        end_s=end,
        fps=fps_i,
        out_mp4=out_mp4,
        prompt_override=prompt,
        seed=int(seed),
        force=bool(force),
    )
    return FileResponse(str(out_mp4), media_type="video/mp4")



if HAS_MULTIPART:
    @app.post("/v1/projects/{project_id}/assets/audio")
    async def upload_audio(project_id: str, file: UploadFile = File(...)):
        proj = store.get(project_id)
        if not proj:
            raise HTTPException(404, "Project not found")
        pdir = store.project_dir(project_id)
        audio_dir = pdir / "assets" / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_upload_filename(file.filename, "audio.wav")
        out = audio_dir / name
        size = 0
        with out.open("wb") as handle:
            while True:
                chunk = await file.read(1024 * 1024)
                if not chunk:
                    break
                handle.write(chunk)
                size += len(chunk)
        try:
            await file.close()
        except Exception:
            pass
        store.set_audio(project_id, name, size)
        return {"ok": True, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/audio")
    async def upload_audio(project_id: str):
        _require_multipart()


@app.head("/v1/projects/{project_id}/audio", include_in_schema=False)
@app.get("/v1/projects/{project_id}/audio")
def get_project_audio(project_id: str):
    """Serve the project's primary uploaded audio file (Timeline playback)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    audio_meta = proj.meta.get("audio") or {}
    fn = str(audio_meta.get("filename") or "").strip()
    if not fn:
        raise HTTPException(404, "No audio uploaded")

    try:
        safe_join(store.project_dir(project_id), fn)
        audio_path = safe_join(store.project_dir(project_id), "assets/audio/" + fn)
    except ValueError as exc:
        raise HTTPException(400, "Unsafe audio path") from exc
    if not audio_path.exists() or not audio_path.is_file():
        raise HTTPException(404, "Audio file missing on disk")

    mt, _ = mimetypes.guess_type(str(audio_path))
    return FileResponse(str(audio_path), media_type=mt or "application/octet-stream")

if HAS_MULTIPART:
    @app.post("/v1/projects/{project_id}/assets/overlay")
    async def upload_overlay_asset(project_id: str, file: UploadFile = File(...)):
        proj = store.get(project_id)
        if not proj:
            raise HTTPException(404, "Project not found")
        pdir = store.project_dir(project_id)
        overlays_dir = pdir / "assets" / "overlays"
        overlays_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_upload_filename(file.filename, "overlay.png")
        out = overlays_dir / name
        data = await file.read()
        out.write_bytes(data)
        proj.meta.setdefault("assets", {}).setdefault("overlays", []).append(name)
        store.save(proj)
        return {"ok": True, "asset": name, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/overlay")
    async def upload_overlay_asset(project_id: str):
        _require_multipart()


if HAS_MULTIPART:
    @app.post("/v1/projects/{project_id}/assets/mask")
    async def upload_mask_asset(project_id: str, file: UploadFile = File(...)):
        proj = store.get(project_id)
        if not proj:
            raise HTTPException(404, "Project not found")
        pdir = store.project_dir(project_id)
        masks_dir = pdir / "assets" / "masks"
        masks_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_upload_filename(file.filename, "mask.png")
        out = masks_dir / name
        data = await file.read()
        out.write_bytes(data)
        proj.meta.setdefault("assets", {}).setdefault("masks", []).append(name)
        store.save(proj)
        return {"ok": True, "asset": name, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/mask")
    async def upload_mask_asset(project_id: str):
        _require_multipart()


def _prepare_transcription_audio(audio_path: Path, project_dir: Path, asr_cfg: dict[str, Any]) -> tuple[Path, dict[str, Any]]:
    if not bool(asr_cfg.get("separate_vocals", False)):
        return audio_path, {"enabled": False, "source": "original_mix", "audio_path": str(audio_path)}

    model = str(asr_cfg.get("separation_model") or "htdemucs").strip() or "htdemucs"
    stems_root = project_dir / "analysis" / "stems"
    vocals_path = stems_root / model / audio_path.stem / "vocals.wav"
    metadata = {
        "enabled": True,
        "source": "demucs",
        "model": model,
        "requested_audio_path": str(audio_path),
        "audio_path": str(vocals_path),
    }

    if vocals_path.exists() and vocals_path.stat().st_mtime >= audio_path.stat().st_mtime:
        metadata["cached"] = True
        return vocals_path, metadata

    deps = transcription_dependency_status()
    if not deps.get("demucs_available"):
        metadata["available"] = False
        metadata["error"] = deps.get("demucs_install_hint") or "Demucs is not installed."
        metadata["fallback_audio_path"] = str(audio_path)
        return audio_path, metadata

    stems_root.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "demucs",
        "-n",
        model,
        "--two-stems",
        "vocals",
        "-o",
        str(stems_root),
        str(audio_path),
    ]
    try:
        subprocess.run(
            cmd,
            cwd=str(project_dir),
            check=True,
            capture_output=True,
            text=True,
            timeout=1800,
        )
    except Exception:
        logger.exception("Demucs vocal separation failed")
        metadata["available"] = True
        metadata["error"] = "Vocal separation failed; using the original mix."
        metadata["fallback_audio_path"] = str(audio_path)
        return audio_path, metadata

    if not vocals_path.exists():
        metadata["available"] = True
        metadata["error"] = f"Demucs completed but did not create {vocals_path}."
        metadata["fallback_audio_path"] = str(audio_path)
        return audio_path, metadata

    metadata["available"] = True
    metadata["cached"] = False
    return vocals_path, metadata


@app.post("/v1/projects/{project_id}/analyze_audio")
def analyze_audio(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    audio_meta = proj.meta.get("audio")
    if not audio_meta:
        raise HTTPException(400, "No audio uploaded")
    audio_path = store.project_dir(project_id) / "assets" / "audio" / audio_meta["filename"]

    development_timings_ms: dict[str, float] = {}
    with development_timing("audio_analysis", development_timings_ms):
        feats = _collect_audio_analysis_features(audio_path)
    try:
        asr_cfg = transcription_settings.get()
        transcription_audio_path, separation_meta = _prepare_transcription_audio(
            audio_path,
            store.project_dir(project_id),
            asr_cfg,
        )
        asr_provider = str(asr_cfg.get("provider") or "faster_whisper")
        # Resolve NVIDIA API key for Parakeet NIM cloud path
        _nvidia_key = ""
        if asr_provider == "parakeet_nim":
            _nvidia_key = (
                secrets.get("nvidia_api_key")
                or os.getenv("EDMG_AI_NVIDIA_API_KEY")
                or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
                or ""
            )
        transcript_result = ai.transcribe(
            str(transcription_audio_path),
            model_size=str(asr_cfg.get("model") or "turbo"),
            provider=asr_provider,
            device=str(asr_cfg.get("device") or "auto"),
            compute_type=str(asr_cfg.get("compute_type") or "auto"),
            fallback_to_whisper=bool(asr_cfg.get("fallback_to_whisper", True)),
            nvidia_api_key=_nvidia_key,
            nim_base_url=str(asr_cfg.get("nim_base_url") or ""),
        )
        trans = transcript_result if isinstance(transcript_result, dict) else {"text": str(transcript_result or "")}
        trans["source_audio_path"] = str(transcription_audio_path)
        trans["vocal_separation"] = separation_meta
    except Exception:
        logger.exception("Project transcription failed")
        trans = {"error": "Transcription failed"}

    with development_timing("analysis_enrichment", development_timings_ms):
        analysis = _enrich_project_audio_analysis(
            getattr(proj, "name", "Untitled project"),
            {"features": feats, "transcript": trans, "timestamp": time.time()},
        )
        analysis = enrich_with_multitrack_defaults(analysis)
    if development_timings_ms:
        analysis["development_diagnostics"] = {"stage_timings_ms": development_timings_ms}
    duration_s = _analysis_duration_s(analysis)
    if duration_s:
        analysis["duration_s"] = float(duration_s)
    previous_analysis = proj.meta.get("analysis") or next(iter(reversed(proj.meta.get("analysis_history") or [])), {})
    analysis["revision"] = int(previous_analysis.get("revision") or 0) + 1
    analysis_path = _write_project_analysis_snapshot(project_id, analysis)
    if analysis_path:
        analysis["analysis_path"] = analysis_path
    proj.meta["analysis"] = analysis
    # Analysis prepares reviewable direction; approved plans and the timeline
    # remain active until the user applies the combined Workspace draft.
    try:
        from .domain.director_workflow import prepare_workflow
        prepare_workflow(proj, _workspace_audio_plan, resulting_revision=proj.revision + 1)
        proj.meta.pop("director_workflow_error", None)
    except Exception:
        logger.exception("Could not prepare Workspace direction after audio analysis")
        proj.meta["director_workflow_error"] = "Audio analysis is saved. Prepare direction again to retry the scene draft."
    store.save(proj)
    return {"ok": True, "analysis": analysis, "direction_prepared": not proj.meta.get("director_workflow_error")}


@app.get("/v1/director_modes")
def get_director_modes():
    return {"ok": True, "modes": list_director_modes()}


@app.get("/v1/projects/{project_id}/creative_direction")
def get_creative_direction(
    project_id: str,
    variant_index: int = 0,
    preset: str = "cinematic",
    director_mode: str | None = None,
    sensitivity: float = 1.0,
):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    payload = _build_creative_direction_payload(
        proj,
        variant_index=variant_index,
        preset=preset,
        sensitivity=sensitivity,
        director_mode=director_mode,
    )
    return {"ok": True, "creative_direction": payload}


@app.post("/v1/projects/{project_id}/creative_direction/apply_timeline_patch")
def apply_creative_direction_timeline_patch(project_id: str, req: CreativeDirectionApplyRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    payload = _build_creative_direction_payload(
        proj,
        variant_index=int(req.variant_index or 0),
        preset=str(req.preset or "cinematic"),
        sensitivity=float(req.sensitivity or 1.0),
        director_mode=req.director_mode,
    )
    patch_timeline = (
        payload.get("timeline_patch", {}).get("timeline")
        if isinstance(payload.get("timeline_patch"), dict)
        else {}
    )
    if not isinstance(patch_timeline, dict) or not patch_timeline:
        raise HTTPException(400, "Creative direction timeline patch is unavailable")

    base_timeline = proj.meta.get("timeline") if isinstance(proj.meta.get("timeline"), dict) else {}
    merged = _merge_creative_timeline_patch(
        base_timeline,
        patch_timeline,
        overwrite_tracks=bool(req.overwrite_tracks),
        overwrite_camera=bool(req.overwrite_camera),
    )
    proj.meta["timeline"] = merged
    proj.meta["last_creative_direction"] = {
        "variant_index": int(req.variant_index or 0),
        "preset": str(payload.get("preset") or req.preset or "cinematic"),
        "director_mode": str(payload.get("director_mode") or req.director_mode or "narrative"),
        "sensitivity": float(req.sensitivity or 1.0),
        "applied_at": time.time(),
    }
    store.save(proj)
    return {"ok": True, "timeline": merged, "creative_direction": payload}


def _analysis_transcript_text(analysis: dict[str, Any]) -> str:
    raw = (analysis or {}).get("transcript")
    if isinstance(raw, dict):
        text = str(raw.get("text") or "").strip()
        if text:
            return text
        segments = raw.get("segments") if isinstance(raw.get("segments"), list) else []
        return " ".join(
            [str(seg.get("text") or "").strip() for seg in segments if isinstance(seg, dict) and str(seg.get("text") or "").strip()]
        ).strip()
    if isinstance(raw, str):
        return raw
    return ""


def _analysis_transcript_segments(analysis: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (analysis or {}).get("transcript")
    if isinstance(raw, dict) and isinstance(raw.get("segments"), list):
        out: list[dict[str, Any]] = []
        for item in raw.get("segments") or []:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text") or "").strip()
            if not text:
                continue
            try:
                start = float(item.get("start") or 0.0)
            except Exception:
                start = 0.0
            try:
                end = float(item.get("end") or start)
            except Exception:
                end = start
            out.append({"start": max(0.0, start), "end": max(start, end), "text": text})
        return out
    return []


def _normalize_curve(values: Any) -> list[float]:
    out = _coerce_float_list(values)
    if not out:
        return []
    mn = min(out)
    mx = max(out)
    if mx > mn:
        out = [(float(v) - mn) / (mx - mn) for v in out]
    return [max(0.0, min(1.0, float(v))) for v in out]


def _collect_audio_analysis_features(audio_path: Path) -> dict[str, Any]:
    # Do not import the enhanced analyzer on Windows. A native access violation
    # in librosa's numba/llvmlite stack terminates the backend process and
    # cannot be handled by a Python try/except block.
    if platform.system().strip().lower() == "windows":
        try:
            return analyze_audio_ffmpeg_numpy(
                audio_path,
                ffmpeg_path=settings.ffmpeg_path,
                source="windows_safe_path",
            )
        except Exception as exc:
            logger.exception("Windows-safe FFmpeg audio feature analysis failed")
            return {
                "error": "Audio feature analysis failed",
                "analysis_backend": "ffmpeg_numpy",
                "analysis_source": "windows_safe_path",
                "analysis_diagnostics": {
                    "backend": "ffmpeg_numpy",
                    "source": "windows_safe_path",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                },
            }

    try:
        from enhanced_deforum_music_generator.core.audio_analyzer import AudioAnalyzer  # type: ignore
        from enhanced_deforum_music_generator.config.config_system import AudioConfig  # type: ignore

        analyzer = AudioAnalyzer(AudioConfig())
        af = analyzer.analyze_features(str(audio_path))
        return {
            "duration_s": float(getattr(af, "duration", 0.0) or 0.0),
            "bpm": float(getattr(af, "tempo", 0.0) or 0.0),
            "tempo_bpm": float(getattr(af, "tempo", 0.0) or 0.0),
            "beats": [float(x) for x in (getattr(af, "beats", []) or [])],
            "energy": _normalize_curve(getattr(af, "energy", []) or []),
            "onset_strength": _normalize_curve(getattr(af, "onset_strength", []) or []),
            "onset_times": [float(x) for x in (getattr(af, "onset_times", []) or [])],
            "spectral_centroid": [float(x) for x in (getattr(af, "spectral_centroid", []) or [])],
            "spectral_rolloff": [float(x) for x in (getattr(af, "spectral_rolloff", []) or [])],
            "rms_energy": _normalize_curve(getattr(af, "rms_energy", []) or []),
            "analysis_backend": "enhanced_audio_analyzer",
            "analysis_source": "primary",
            "analysis_diagnostics": {
                "backend": "enhanced_audio_analyzer",
                "source": "primary",
            },
        }
    except Exception as enhanced_exc:
        logger.warning(
            "Enhanced audio feature analysis failed; using the FFmpeg/NumPy safe fallback",
            exc_info=True,
        )
        try:
            return analyze_audio_ffmpeg_numpy(
                audio_path,
                ffmpeg_path=settings.ffmpeg_path,
                source="enhanced_analyzer_fallback",
            )
        except SafeAudioAnalysisError:
            logger.warning("FFmpeg/NumPy audio fallback failed; trying the legacy lightweight analyzer", exc_info=True)
        except Exception:
            logger.warning("Unexpected FFmpeg/NumPy audio fallback failure; trying the legacy lightweight analyzer", exc_info=True)

        try:
            from edmg_ai_service.audio import lightweight_audio_features  # type: ignore

            result = dict(lightweight_audio_features(str(audio_path)) or {})
            result["analysis_backend"] = "lightweight_audio_features"
            result["analysis_source"] = "legacy_fallback"
            result["analysis_diagnostics"] = {
                "backend": "lightweight_audio_features",
                "source": "legacy_fallback",
                "enhanced_error_type": type(enhanced_exc).__name__,
            }
            return result
        except Exception:
            logger.exception("Audio feature analysis failed")
            return {"error": "Audio feature analysis failed"}


def _normalize_transcript_payload(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        raw = {"text": raw}
    elif not isinstance(raw, dict):
        raw = {}

    text = str(raw.get("text") or "").strip()
    segments: list[dict[str, Any]] = []
    if isinstance(raw.get("segments"), list):
        for item in raw.get("segments") or []:
            if not isinstance(item, dict):
                continue
            seg_text = str(item.get("text") or "").strip()
            if not seg_text:
                continue
            try:
                start = float(item.get("start") or 0.0)
            except Exception:
                start = 0.0
            try:
                end = float(item.get("end") or start)
            except Exception:
                end = start
            segments.append({"start": max(0.0, start), "end": max(start, end), "text": seg_text})

    if not text and segments:
        text = "\n".join(seg["text"] for seg in segments).strip()

    duration_s = _pick_raw_number(raw, ["duration_s", "duration"])
    duration_after_vad_s = _pick_raw_number(raw, ["duration_after_vad_s"])
    word_count = int(raw.get("word_count") or len(text.split()))
    return {
        "text": text,
        "segments": segments,
        "language": str(raw.get("language") or ""),
        "duration_s": float(duration_s or 0.0),
        "duration_after_vad_s": float(duration_after_vad_s or 0.0),
        "segment_count": int(raw.get("segment_count") or len(segments)),
        "word_count": word_count,
        "model_size": str(raw.get("model_size") or "small"),
        "source": str(raw.get("source") or "transcribe"),
        **({"error": str(raw.get("error"))} if raw.get("error") else {}),
        **({"note": str(raw.get("note"))} if raw.get("note") else {}),
    }


def _analysis_top_keywords(text: str, limit: int = 12) -> list[str]:
    counts: dict[str, int] = {}
    for token in _creative_tokenize(text):
        counts[token] = counts.get(token, 0) + 1
    return [token for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _analysis_theme_terms(text: str, limit: int = 8) -> list[str]:
    try:
        from enhanced_deforum_music_generator.core.nlp_processor import NLPProcessor  # type: ignore

        terms = NLPProcessor({"max_themes": limit}).extract_themes(text)
    except Exception:
        terms = []
    merged: list[str] = []
    for token in list(terms or []) + _analysis_top_keywords(text, limit=limit):
        clean = str(token or "").strip().lower()
        if clean and clean not in merged:
            merged.append(clean)
        if len(merged) >= limit:
            break
    return merged


_AUDIO_ONLY_SUMMARY_SUFFIX = (
    "Studio is still able to build audio-reactive sections and a first creative direction from rhythm, energy, and spectral movement."
)


def _analysis_audio_only_status(summary_prefix: str) -> str:
    clean = str(summary_prefix or "").strip()
    if not clean:
        clean = "No transcript is available for this track yet."
    elif clean[-1] not in ".!?":
        clean = f"{clean}."
    return f"{clean} {_AUDIO_ONLY_SUMMARY_SUFFIX}"


def _analysis_summary_text(transcript: dict[str, Any], text: str, segments: list[dict[str, Any]]) -> str:
    candidates: list[str] = []
    if segments:
        picks = [segments[0], segments[len(segments) // 2], segments[-1]]
        for seg in picks:
            cue = str(seg.get("text") or "").strip()
            if cue and cue not in candidates:
                candidates.append(cue)
    if not candidates:
        for sentence in _analysis_transcript_sentences({"transcript": {"text": text}}):
            if sentence not in candidates:
                candidates.append(sentence)
            if len(candidates) >= 3:
                break
    if not candidates:
        note = str((transcript or {}).get("note") or "").strip()
        error = str((transcript or {}).get("error") or "").strip()
        if note:
            return _analysis_audio_only_status(note)
        if error:
            return _analysis_audio_only_status("Transcription failed.")
        return _analysis_audio_only_status("No transcript is available for this track yet.")
    return " ".join(candidates[:3]).strip()


def _derive_longform_analysis_sections(
    title: str,
    analysis: dict[str, Any],
    tags: list[str],
    *,
    preset: str = "cinematic",
    sensitivity: float = 1.0,
    max_sections: int = 12,
) -> list[dict[str, Any]]:
    segments = _analysis_transcript_segments(analysis)
    duration_s = _analysis_duration_s(analysis) or 0.0
    if duration_s <= 0.0 and segments:
        duration_s = max(float(seg.get("end") or 0.0) for seg in segments)
    overall = _infer_reactivity_metrics(analysis)
    energy_curve = list(overall.get("energy_curve") or [])

    if not segments:
        return _derive_reactive_sections(
            overall,
            duration_s,
            _analysis_transcript_sentences(analysis),
            tags[:8],
            title,
            preset,
            sensitivity,
            max_sections=min(8, max(3, int(max_sections))),
        )

    desired = max(3, min(int(max_sections), int(math.ceil(max(duration_s, 1.0) / 60.0))))
    window_s = max(20.0, duration_s / max(1, desired))
    sections: list[dict[str, Any]] = []
    for index in range(desired):
        start_s = float(index) * window_s
        end_s = duration_s if index == desired - 1 else min(duration_s, float(index + 1) * window_s)
        bucket = [
            seg for seg in segments
            if float(seg.get("end") or 0.0) > start_s and float(seg.get("start") or 0.0) < end_s
        ]
        if not bucket:
            midpoint = (start_s + end_s) / 2.0
            nearest = min(segments, key=lambda seg: abs((((float(seg.get("start") or 0.0) + float(seg.get("end") or 0.0)) / 2.0) - midpoint)))
            bucket = [nearest]
        cue_text = " ".join(str(seg.get("text") or "").strip() for seg in bucket[:3]).strip()
        bucket_tags = _analysis_top_keywords(cue_text, limit=4) or list(tags[:4])
        metrics = _scene_metrics_from_curve(
            index,
            desired,
            {"start_s": start_s, "end_s": end_s},
            overall,
            duration_s,
            energy_curve,
        )
        band_scores = {
            "bass": float(metrics.get("bass") or 0.0),
            "mid": float(metrics.get("mid") or 0.0),
            "treble": float(metrics.get("treble") or 0.0),
        }
        band = max(band_scores.items(), key=lambda item: item[1])[0]
        label = _creative_section_label(index, desired, float(metrics.get("energy") or 0.0), band)
        camera_hint, motion_hint_base = _creative_section_hints(label, band)
        params = _compute_reactive_params(metrics, preset, sensitivity)
        motion_hint = f"{motion_hint_base} {_creative_motion_hint(params)}".strip()
        focus = ", ".join(bucket_tags[:3]) if bucket_tags else ", ".join(tags[:3]) or "cinematic continuity"
        prompt = (
            f"{title or 'Untitled project'}, {label.lower()}, {band}-led motion language, "
            f"{preset} music-film framing, themes: {focus}"
        )
        sections.append(
            {
                "index": index,
                "name": label,
                "start_s": start_s,
                "end_s": max(start_s + 0.2, end_s),
                "duration_s": max(0.2, end_s - start_s),
                "energy": float(metrics.get("energy") or 0.0),
                "energy_label": _creative_energy_label(float(metrics.get("energy") or 0.0)),
                "prompt": prompt,
                "transcript_cue": cue_text or "No transcript cue available; drive the section from the energy arc.",
                "camera_hint": camera_hint,
                "motion_hint": motion_hint,
                "band": band,
                "keywords": bucket_tags,
                "avg_energy": float(metrics.get("energy") or 0.0),
                "peak_energy": float(metrics.get("energy") or 0.0),
                "reactive_params": params,
                "scene_source": "analysis_fallback",
            }
        )
    return sections


def _enrich_project_audio_analysis(title: str, analysis: dict[str, Any]) -> dict[str, Any]:
    normalized: dict[str, Any] = {
        "features": dict((analysis or {}).get("features") or {}),
        "transcript": _normalize_transcript_payload((analysis or {}).get("transcript")),
        "timestamp": float((analysis or {}).get("timestamp") or time.time()),
    }
    transcript_text = _analysis_transcript_text(normalized)
    transcript_segments = _analysis_transcript_segments(normalized)
    tags = _analysis_top_keywords(transcript_text, limit=12)
    themes = _analysis_theme_terms(transcript_text, limit=8)
    emotion_scores = _creative_emotion_scores(_creative_tokenize(transcript_text), limit=4)
    normalized["summary"] = _analysis_summary_text(normalized.get("transcript") or {}, transcript_text, transcript_segments)
    normalized["tags"] = list(dict.fromkeys([*themes, *tags]))[:12]
    normalized["themes"] = themes
    normalized["emotions"] = emotion_scores
    normalized["sections"] = _derive_longform_analysis_sections(title, normalized, normalized["tags"])
    normalized["transcript"]["segment_count"] = len(transcript_segments)
    normalized["transcript"]["word_count"] = int(normalized["transcript"].get("word_count") or len(transcript_text.split()))
    return normalized


def _write_project_analysis_snapshot(project_id: str, analysis: dict[str, Any]) -> str | None:
    try:
        pdir = store.project_dir(project_id)
        rel = Path("analysis") / "audio_analysis.json"
        target = pdir / rel
        tmp = target.with_suffix(".json.tmp")
        payload = json.dumps(analysis, ensure_ascii=False, indent=2)
        with tmp.open("w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, target)
        return str(rel).replace("\\", "/")
    except Exception:
        return None

def _coerce_float_list(v: Any) -> list[float]:
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        out = []
        for x in v:
            try:
                out.append(float(x))
            except Exception:
                continue
        return out
    return []


def _build_public_audio_analysis(proj: Any) -> Any:
    """Build enhanced_deforum_music_generator.public_api.AudioAnalysis from project meta."""
    analysis = (proj.meta.get("analysis") or {}) if hasattr(proj, "meta") else {}
    feats = (analysis.get("features") or {}) if isinstance(analysis, dict) else {}

    duration = float(_project_duration_hint_s(proj, analysis=analysis) or feats.get("duration_s") or feats.get("duration") or 0.0)
    bpm = float(feats.get("bpm") or feats.get("tempo_bpm") or feats.get("tempo") or 0.0)

    beats = _coerce_float_list(feats.get("beats") or feats.get("beat_times") or feats.get("beat_timestamps"))
    energy = _coerce_float_list(feats.get("energy") or feats.get("energy_curve") or feats.get("energy_envelope") or feats.get("onset_strength"))

    # normalize energy to 0..1
    if energy:
        mn = min(energy)
        mx = max(energy)
        if mx > mn:
            energy = [(e - mn) / (mx - mn) for e in energy]
        energy = [max(0.0, min(1.0, float(e))) for e in energy]

    transcript = _analysis_transcript_text(analysis)

    try:
        from enhanced_deforum_music_generator.public_api import AudioAnalysis  # type: ignore
        aa = AudioAnalysis(filepath="", duration=duration, tempo_bpm=bpm, beats=beats, energy=energy)
        # soft-attach lyrics if present; orchestrator may use lyric_segments
        setattr(aa, "lyrics", transcript)
        return aa
    except Exception:
        return {"duration": duration, "tempo_bpm": bpm, "beats": beats, "energy": energy, "lyrics": transcript}


_CREATIVE_DIRECTION_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "into", "is", "it", "its",
    "of", "on", "or", "that", "the", "their", "this", "to", "with", "your", "you", "about", "after",
    "before", "during", "through", "scene", "shot", "visual", "video", "music", "audio", "render",
    "track", "variant", "project", "style", "look", "high", "detail", "coherent", "consistent",
}

_CREATIVE_EMOTION_WORDS: dict[str, set[str]] = {
    "euphoria": {"light", "higher", "rise", "alive", "open", "glow", "gold", "electric", "dance", "rush"},
    "longing": {"echo", "late", "ghost", "after", "distance", "remember", "missing", "fade", "lost", "again"},
    "tension": {"edge", "fall", "smoke", "storm", "shadow", "break", "pressure", "night", "wire", "warning"},
    "intimacy": {"skin", "breath", "close", "touch", "hand", "heart", "whisper", "inside"},
    "defiance": {"burn", "riot", "wild", "fight", "loud", "rough", "fire", "run"},
    "wonder": {"sky", "stars", "ocean", "dream", "horizon", "infinite", "blue", "sun", "neon", "glass"},
}


def _creative_tokenize(text: str) -> list[str]:
    return [
        token
        for token in re.sub(r"[^a-z0-9\s'-]", " ", str(text or "").lower()).split()
        if len(token) > 2 and token not in _CREATIVE_DIRECTION_STOPWORDS
    ]


def _creative_emotion_scores(tokens: list[str], limit: int = 4) -> list[dict[str, Any]]:
    if not tokens:
        return []

    raw_scores = [
        (emotion, sum(1 for token in tokens if token in words))
        for emotion, words in _CREATIVE_EMOTION_WORDS.items()
    ]
    peak = max([score for _emotion, score in raw_scores] or [0])
    if peak <= 0:
        return []

    return [
        {"emotion": emotion, "score": round(float(score) / float(peak), 3)}
        for emotion, score in sorted(raw_scores, key=lambda item: (-item[1], item[0]))
        if score > 0
    ][:limit]


def _creative_hooks(sentences: list[str], limit: int = 3) -> list[str]:
    picks: list[str] = []
    for sentence in list(sentences[:2]) + list(sentences[-1:]):
        clean = str(sentence or "").strip()
        if clean and clean not in picks:
            picks.append(clean)
        if len(picks) >= limit:
            break
    return picks


def _creative_average(values: list[float]) -> float:
    return float(sum(values) / len(values)) if values else 0.0


def _creative_provider_mode(plan: dict[str, Any]) -> str:
    source = str((plan or {}).get("source") or "").strip().lower()
    provider = os.getenv("EDMG_AI_PROVIDER", "nemotron_cloud").strip().lower()
    if source == "ai":
        if provider == "ollama":
            return "ollama-contract"
        if provider in {"openai_compat", "openai-compatible", "openai"}:
            return "openai-contract"
        if provider in {"nemotron_cloud", "nvidia_nim", "nemotron"}:
            return "nemotron-contract"
        return f"{provider}-contract" if provider else "provider-contract"
    return "local-heuristic"


def _normalize_unit(value: Any, mode: str = "unit") -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if mode == "tempo":
        return max(0.0, min(1.0, (number - 60.0) / 120.0))
    if mode == "centroid":
        return max(0.0, min(1.0, number / 5000.0))
    if abs(number) <= 1.0:
        return max(0.0, min(1.0, number))
    return max(0.0, min(1.0, number / 100.0))


def _pick_feature_number(source: dict[str, Any], keys: list[str], mode: str = "unit") -> float | None:
    for key in keys:
        if key not in source:
            continue
        normalized = _normalize_unit(source.get(key), mode)
        if normalized is not None:
            return normalized
    return None


def _pick_raw_number(source: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        try:
            return float(source.get(key))
        except Exception:
            continue
    return None


def _feature_series(source: dict[str, Any], keys: list[str]) -> list[float]:
    for key in keys:
        values = source.get(key)
        if isinstance(values, (list, tuple)):
            out: list[float] = []
            for item in values:
                try:
                    out.append(float(item))
                except Exception:
                    continue
            if out:
                return out
    return []


def _bucket_curve(values: list[float], buckets: int = 96) -> list[float]:
    if not values:
        return []
    target = max(16, int(buckets))
    step = max(1, int(math.ceil(len(values) / target)))
    out: list[float] = []
    for start in range(0, len(values), step):
        chunk = values[start:start + step]
        if not chunk:
            continue
        peak = max(abs(float(v)) for v in chunk)
        out.append(max(0.0, min(1.0, peak)))
    return out[:target]


def _analysis_transcript_sentences(analysis: dict[str, Any]) -> list[str]:
    text = _analysis_transcript_text(analysis).strip()
    if not text:
        return []
    return [part.strip() for part in re.split(r"(?<=[.!?])\s+", text) if part.strip()]


def _has_usable_transcript(analysis: dict[str, Any]) -> bool:
    text = _analysis_transcript_text(analysis).strip()
    if text:
        return True
    raw = (analysis or {}).get("transcript")
    if isinstance(raw, dict) and isinstance(raw.get("segments"), list):
        return any(str(seg.get("text") or "").strip() for seg in raw.get("segments") if isinstance(seg, dict))
    return False


def _usable_transcript_overlay_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    lowered = text.lower()
    fallback_prefixes = (
        "no transcript cue available",
        "transcription unavailable",
        "audio-only analysis",
        "drive the section from the energy arc",
        "drive the scene from the prompt and energy arc",
    )
    if any(lowered.startswith(prefix) for prefix in fallback_prefixes):
        return ""
    return text[:180]


def _analysis_motifs(variant: dict[str, Any], transcript_text: str, limit: int = 8) -> list[str]:
    feed: list[str] = [transcript_text]
    for scene in list(variant.get("scenes") or []):
        feed.append(str(scene.get("name") or ""))
        feed.append(str(scene.get("prompt") or ""))

    counts: dict[str, int] = {}
    for value in feed:
        tokens = re.sub(r"[^a-z0-9\s-]", " ", str(value).lower()).split()
        for token in tokens:
            if len(token) <= 2 or token in _CREATIVE_DIRECTION_STOPWORDS:
                continue
            counts[token] = counts.get(token, 0) + 1

    return [token for token, _count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:limit]]


def _infer_reactivity_metrics(analysis: dict[str, Any]) -> dict[str, Any]:
    feats = (analysis.get("features") or {}) if isinstance(analysis, dict) else {}
    duration_s = (
        _pick_raw_number(feats, ["duration_s", "duration", "audio_duration_s"])
        or _pick_raw_number(analysis, ["duration_s", "duration"])
        or 0.0
    )
    energy_curve = _feature_series(feats, ["energy", "energy_curve", "energy_envelope", "onset_strength"])
    scalar_energy = _pick_feature_number(feats, ["energy", "rms_energy", "loudness_norm", "dynamic_energy"])
    if scalar_energy is None and energy_curve:
        scalar_energy = max(0.0, min(1.0, sum(energy_curve) / max(1, len(energy_curve))))
    energy = scalar_energy if scalar_energy is not None else 0.45
    bass = _pick_feature_number(feats, ["bass", "bass_energy", "low_frequency_energy", "kick_energy"])
    if bass is None:
        bass = max(0.0, min(1.0, 0.32 + energy * 0.45))
    mid = _pick_feature_number(feats, ["mid", "mid_energy", "spectral_flatness", "harmonic_energy"])
    if mid is None:
        mid = max(0.0, min(1.0, 0.38 + energy * 0.34))
    treble = _pick_feature_number(feats, ["treble", "brightness", "high_frequency_energy"])
    if treble is None:
        treble = _pick_feature_number(feats, ["spectral_centroid"], mode="centroid")
    if treble is None:
        tempo = _pick_feature_number(feats, ["tempo_bpm", "bpm", "tempo"], mode="tempo") or 0.2
        treble = max(0.0, min(1.0, 0.25 + energy * 0.18 + tempo * 0.3))

    return {
        "energy": float(energy),
        "bass": float(bass),
        "mid": float(mid),
        "treble": float(treble),
        "duration_s": float(duration_s),
        "source": "analysis",
        "waveform": _bucket_curve(energy_curve, 96),
        "energy_curve": [max(0.0, min(1.0, float(v))) for v in energy_curve],
    }


def _compute_reactive_params(metrics: dict[str, Any], preset: str, sensitivity: float) -> dict[str, float]:
    sens = max(0.1, min(3.0, float(sensitivity or 1.0)))
    energy = max(0.0, min(1.0, float(metrics.get("energy") or 0.0)))
    bass = max(0.0, min(1.0, float(metrics.get("bass") or 0.0)))
    mid = max(0.0, min(1.0, float(metrics.get("mid") or 0.0)))
    treble = max(0.0, min(1.0, float(metrics.get("treble") or 0.0)))
    progress = max(0.0, min(1.0, float(metrics.get("progress") or 0.0)))
    lateral_phase = math.sin(progress * math.pi * 2.0)
    vertical_phase = math.cos(progress * math.pi * 1.5)
    orbit_phase = math.sin(progress * math.pi)

    if preset == "psychedelic":
        return {
            "zoom": 1.0 + (0.08 + energy * 0.16 + orbit_phase * 0.04) * sens,
            "rotation_x": (energy * 64.0 + vertical_phase * 16.0) * sens,
            "rotation_y": (bass * 92.0 + lateral_phase * 26.0) * sens,
            "rotation_z": (treble * 28.0 + lateral_phase * 8.0) * sens,
            "translation_x": (lateral_phase * (10.0 + bass * 12.0) + math.sin(mid * 6.0) * 6.0) * sens,
            "translation_y": (vertical_phase * (6.0 + treble * 8.0) + orbit_phase * 4.0) * sens,
            "translation_z": -(18.0 + energy * 20.0 + bass * 5.0) * sens,
            "cfg_scale": 6.8 + mid * sens * 2.5,
            "strength": 0.56 + treble * sens * 0.22,
            "brightness": 0.48 + mid * sens * 0.36,
            "contrast": 1.0 + energy * sens * 0.72,
        }
    if preset == "ambient":
        return {
            "zoom": 1.0 + (0.03 + energy * 0.08) * sens,
            "rotation_x": (bass * 6.0 + vertical_phase * 3.0) * sens,
            "rotation_y": (mid * 10.0 + lateral_phase * 4.0) * sens,
            "rotation_z": (treble * 5.0 + orbit_phase * 2.0) * sens,
            "translation_x": lateral_phase * (4.0 + mid * 5.0) * sens,
            "translation_y": vertical_phase * (3.0 + treble * 4.0) * sens,
            "translation_z": -(6.0 + energy * 8.0) * sens,
            "cfg_scale": 6.0 + treble * sens * 1.8,
            "strength": 0.5 + mid * sens * 0.16,
            "brightness": 0.42 + mid * sens * 0.22,
            "contrast": 0.95 + energy * sens * 0.24,
        }
    return {
        "zoom": 1.0 + (0.05 + energy * 0.14 + bass * 0.02) * sens,
        "rotation_x": (mid * 8.0 + vertical_phase * 4.0) * sens,
        "rotation_y": (math.sin(bass * 4.0) * 16.0 + lateral_phase * 9.0) * sens,
        "rotation_z": (treble * 7.0 + orbit_phase * 3.5) * sens,
        "translation_x": (lateral_phase * (6.0 + mid * 8.0 + bass * 5.0)) * sens,
        "translation_y": (vertical_phase * (3.0 + treble * 5.0)) * sens,
        "translation_z": -(12.0 + energy * 18.0 + bass * 4.0) * sens,
        "cfg_scale": 7.0 + mid * sens * 2.4,
        "strength": 0.62 + treble * sens * 0.21,
        "brightness": 0.45 + energy * sens * 0.16,
        "contrast": 1.02 + energy * sens * 0.4,
    }


def _creative_energy_label(value: float) -> str:
    if value >= 0.82:
        return "surge"
    if value >= 0.64:
        return "lift"
    if value >= 0.42:
        return "steady"
    return "breath"


def _creative_camera_hint(value: float) -> str:
    if value >= 0.82:
        return "Aggressive push with a lateral sweep, stronger parallax, sharper light contrast, and a quicker axis reset on the cut."
    if value >= 0.64:
        return "Tracking medium shot with progressive push, controlled side travel, and bolder edge lighting around the subject."
    if value >= 0.42:
        return "Measured dolly, orbit, or lateral pan with restrained motion blur and stable framing for continuity."
    return "Wide or medium-wide hold with soft side drift, longer lens settle, and more negative space."


def _creative_motion_hint(params: dict[str, float]) -> str:
    return (
        f"Zoom {params['zoom']:.2f}, pan X {params['translation_x']:.1f}, pan Y {params['translation_y']:.1f}, "
        f"roll {params['rotation_z']:.1f}, Z travel {params['translation_z']:.1f}, cfg {params['cfg_scale']:.1f}, strength {params['strength']:.2f}."
    )


def _creative_section_label(index: int, total: int, energy: float, band: str) -> str:
    if index == 0:
        return "Arrival" if energy < 0.42 else "Cold Open"
    if index == max(0, total - 1):
        return "Resolve" if energy > 0.68 else "Afterglow"
    if energy > 0.82 and band == "bass":
        return "Drop"
    if energy > 0.68 and band == "mid":
        return "Lift"
    if energy < 0.34:
        return "Breath"
    if band == "treble":
        return "Spark"
    if band == "bass":
        return "Drive"
    return "Build"


def _creative_section_hints(label: str, band: str) -> tuple[str, str]:
    if label == "Drop":
        return (
            "Fast push with a lateral sweep, foreground occlusion, and sharper light separation.",
            "Push zoom selectively, extend side travel, and use transient shake accents around impact.",
        )
    if label == "Breath":
        return (
            "Locked or gently drifting frame with longer lens settle and a soft side drift.",
            "Small XY drift, softer contrast, and more negative space.",
        )
    if band == "treble":
        return (
            "Lateral glide with highlight streaks, cleaner silhouette edges, and subject or light passes across frame.",
            "Particle flicker, quicker spin accents, and brighter edge energy without losing the camera axis.",
        )
    if band == "bass":
        return (
            "Low-angle arc with grounded perspective, denser foreground depth, and weighty side-to-side travel.",
            "Scale pulses, front-to-back travel, and weighty motion ramps with occasional lateral shove.",
        )
    return (
        "Steadicam reveal with measured parallax depth, a controlled lateral pan, and subtle height changes.",
        "Blend orbit, rise, and moderate contrast ramps while preserving continuity.",
    )


def _fallback_scene_metrics(index: int, total: int, overall: dict[str, Any]) -> dict[str, Any]:
    ratio = float(index) / max(1.0, float(total - 1)) if total > 1 else 0.0
    curve = math.sin(ratio * math.pi)
    energy = max(0.0, min(1.0, float(overall["energy"]) * 0.72 + curve * 0.26 + ratio * 0.06))
    return {
        "energy": energy,
        "bass": max(0.0, min(1.0, float(overall["bass"]) * 0.7 + curve * 0.22)),
        "mid": max(0.0, min(1.0, float(overall["mid"]) * 0.8 + (1.0 - abs(0.5 - ratio) * 2.0) * 0.14)),
        "treble": max(0.0, min(1.0, float(overall["treble"]) * 0.72 + ratio * 0.18)),
        "duration_s": float(overall.get("duration_s") or 0.0),
        "source": "analysis",
        "progress": ratio,
    }


def _derive_reactive_sections(
    overall: dict[str, Any],
    duration_s: float,
    transcript_sentences: list[str],
    motifs: list[str],
    title: str,
    preset: str,
    sensitivity: float,
    max_sections: int = 6,
) -> list[dict[str, Any]]:
    if duration_s <= 0:
        duration_s = max(12.0, float(len(transcript_sentences) or 3) * 6.0)
    desired = max(3, min(8, int(max_sections)))
    curve = [max(0.0, min(1.0, float(v))) for v in list(overall.get("energy_curve") or [])]

    ordered: list[int] = [0]
    if len(curve) >= 4:
        min_gap = max(2, len(curve) // max(3, desired + 1))
        candidates = sorted(
            [
                (index, abs(curve[index] - curve[index - 1]))
                for index in range(1, len(curve) - 1)
            ],
            key=lambda item: item[1],
            reverse=True,
        )
        for index, _score in candidates:
            if len(ordered) >= desired:
                break
            if all(abs(index - existing) >= min_gap for existing in ordered):
                ordered.append(index)
        if len(ordered) < desired:
            step = max(1, len(curve) // desired)
            for index in range(step, len(curve) - 1, step):
                if len(ordered) >= desired:
                    break
                if all(abs(index - existing) >= min_gap for existing in ordered):
                    ordered.append(index)
        ordered.append(len(curve) - 1)
    else:
        total_points = max(desired * 4, 16)
        ordered.extend([int(round((index / float(desired)) * (total_points - 1))) for index in range(1, desired)])
        ordered.append(total_points - 1)

    ordered = sorted(set(max(0, int(value)) for value in ordered))
    if len(ordered) < 2:
        ordered = [0, max(1, len(curve) - 1 if curve else desired * 3)]

    total_points = max(ordered[-1], len(curve) - 1, 1)
    sections: list[dict[str, Any]] = []
    for index, start_idx in enumerate(ordered[:-1]):
        end_idx = max(start_idx + 1, ordered[index + 1])
        if curve:
            chunk = curve[start_idx : min(len(curve), end_idx + 1)]
        else:
            span = max(1, end_idx - start_idx)
            chunk = [
                max(0.0, min(1.0, float(overall.get("energy") or 0.45) * 0.75 + math.sin((start_idx + offset) / max(1.0, total_points) * math.pi) * 0.22))
                for offset in range(span)
            ]
        avg_energy = _creative_average(chunk)
        peak_energy = max(chunk) if chunk else float(overall.get("energy") or 0.45)
        ratio = float(index) / max(1.0, float(len(ordered) - 2)) if len(ordered) > 2 else 0.0
        band = (
            "bass"
            if float(overall.get("bass") or 0.0) + peak_energy * 0.12 >= max(float(overall.get("mid") or 0.0) + avg_energy * 0.08, float(overall.get("treble") or 0.0) + ratio * 0.1)
            else "mid"
            if float(overall.get("mid") or 0.0) + avg_energy * 0.08 >= float(overall.get("treble") or 0.0) + ratio * 0.1
            else "treble"
        )
        label = _creative_section_label(index, len(ordered) - 1, avg_energy, band)
        metrics = {
            "energy": avg_energy,
            "bass": max(0.0, min(1.0, float(overall.get("bass") or 0.0) * 0.85 + peak_energy * 0.12)),
            "mid": max(0.0, min(1.0, float(overall.get("mid") or 0.0) * 0.85 + avg_energy * 0.12)),
            "treble": max(0.0, min(1.0, float(overall.get("treble") or 0.0) * 0.82 + ratio * 0.08 + peak_energy * 0.1)),
            "duration_s": max(0.2, (end_idx - start_idx) / max(1.0, total_points) * duration_s),
            "source": "analysis",
            "progress": ratio,
        }
        params = _compute_reactive_params(metrics, preset, sensitivity)
        camera_hint, motion_hint = _creative_section_hints(label, band)
        start_s = float(start_idx) / float(total_points) * duration_s
        end_s = min(duration_s, max(start_s + 0.2, float(end_idx) / float(total_points) * duration_s))
        cue_index = min(len(transcript_sentences) - 1, int(round(ratio * max(0, len(transcript_sentences) - 1)))) if transcript_sentences else -1
        transcript_cue = transcript_sentences[cue_index] if cue_index >= 0 else "No transcript cue available; drive the section from the energy arc."
        prompt = (
            f"{title or 'Untitled project'}, {label.lower()} section, {band}-led motion language, "
            f"{preset} music-film framing, motifs: {', '.join(motifs[:3]) or 'cinematic continuity'}"
        )
        sections.append(
            {
                "index": index,
                "name": label,
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": max(0.2, end_s - start_s),
                "energy": float(avg_energy),
                "energy_label": _creative_energy_label(float(avg_energy)),
                "prompt": prompt,
                "transcript_cue": transcript_cue,
                "camera_hint": camera_hint,
                "motion_hint": f"{motion_hint} {_creative_motion_hint(params)}",
                "band": band,
                "avg_energy": float(avg_energy),
                "peak_energy": float(peak_energy),
                "reactive_params": params,
                "scene_source": "analysis_fallback",
            }
        )
    return sections


def _scene_metrics_from_curve(
    index: int,
    total: int,
    scene: dict[str, Any],
    overall: dict[str, Any],
    duration_s: float,
    energy_curve: list[float],
) -> dict[str, Any]:
    if duration_s <= 0 or not energy_curve:
        return _fallback_scene_metrics(index, total, overall)

    start_s = float(scene.get("start_s") or 0.0)
    end_s = float(scene.get("end_s") or (start_s + 5.0))
    start_idx = max(0, min(len(energy_curve) - 1, int((start_s / max(duration_s, 0.001)) * len(energy_curve))))
    end_idx = max(start_idx + 1, min(len(energy_curve), int(math.ceil((end_s / max(duration_s, 0.001)) * len(energy_curve)))))
    chunk = energy_curve[start_idx:end_idx]
    if not chunk:
        return _fallback_scene_metrics(index, total, overall)

    energy = max(0.0, min(1.0, sum(chunk) / max(1, len(chunk))))
    peak = max(chunk)
    ratio = float(index) / max(1.0, float(total - 1)) if total > 1 else 0.0
    return {
        "energy": energy,
        "bass": max(0.0, min(1.0, float(overall["bass"]) * 0.82 + peak * 0.14)),
        "mid": max(0.0, min(1.0, float(overall["mid"]) * 0.82 + energy * 0.18)),
        "treble": max(0.0, min(1.0, float(overall["treble"]) * 0.78 + ratio * 0.08 + peak * 0.1)),
        "duration_s": max(0.2, end_s - start_s),
        "source": "analysis",
        "progress": ratio,
    }


def _dedupe_camera_keyframes(keyframes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    dedup: dict[float, dict[str, Any]] = {}
    for keyframe in keyframes:
        try:
            t = round(float(keyframe.get("t") or 0.0), 3)
        except Exception:
            continue
        dedup[t] = {**keyframe, "t": t}
    return [dedup[t] for t in sorted(dedup.keys())]


def _build_creative_timeline_patch(
    packed_scenes: list[dict[str, Any]],
    duration_s: float,
    negative_prompt: str,
) -> dict[str, Any]:
    prompt_track = {
        "id": "track_prompt",
        "name": "Prompts",
        "type": "prompt",
        "clips": [],
    }
    motion_track = {
        "id": "track_motion",
        "name": "Motion",
        "type": "motion",
        "clips": [],
    }
    layers: list[dict[str, Any]] = []
    camera_keyframes: list[dict[str, Any]] = []
    prev_zoom = 1.0
    prev_pan_x = 0.0
    prev_pan_y = 0.0
    prev_rotation = 0.0

    for index, scene in enumerate(packed_scenes):
        start_s = float(scene.get("start_s") or 0.0)
        end_s = max(start_s + 0.2, float(scene.get("end_s") or (start_s + 5.0)))
        params = scene.get("reactive_params") if isinstance(scene.get("reactive_params"), dict) else {}
        zoom = float(params.get("zoom") or prev_zoom or 1.0)
        zoom_start = prev_zoom
        zoom_end = max(zoom, zoom_start + max(0.01, float(scene.get("energy") or 0.0) * 0.025))
        pan_x_start = prev_pan_x
        pan_y_start = prev_pan_y
        pan_x_end = float(params.get("translation_x") or 0.0)
        pan_y_end = float(params.get("translation_y") or 0.0)
        rotation_target = float(params.get("rotation_z") or 0.0) + float(params.get("rotation_y") or 0.0) * 0.18
        rotation_start = prev_rotation
        rotation_end = rotation_target

        prompt_track["clips"].append(
            {
                "id": f"creative_prompt_{index}",
                "start_s": start_s,
                "end_s": end_s,
                "data": {
                    "prompt": str(scene.get("prompt_pack") or scene.get("prompt") or "").strip(),
                    "negative_prompt": negative_prompt,
                },
            }
        )
        motion_track["clips"].append(
            {
                "id": f"creative_motion_{index}",
                "start_s": start_s,
                "end_s": end_s,
                "data": {
                    "zoom_start": zoom_start,
                    "zoom_end": zoom_end,
                    "pan_x_start": pan_x_start,
                    "pan_x_end": pan_x_end,
                    "pan_y_start": pan_y_start,
                    "pan_y_end": pan_y_end,
                    "rotation_start": rotation_start,
                    "rotation_end": rotation_end,
                    "strength": float(params.get("strength") or 0.35),
                    "cfg": float(params.get("cfg_scale") or 7.0),
                    "steps": 12,
                },
            }
        )

        cue_text = _usable_transcript_overlay_text(scene.get("transcript_cue"))
        if cue_text:
            layers.append(
                {
                    "id": f"creative_overlay_{index}",
                    "type": "text",
                    "text": cue_text[:180],
                    "start_s": start_s,
                    "end_s": end_s,
                    "x": 24,
                    "y": 24 + (index % 3) * 92,
                    "w": 420,
                    "h": 84,
                    "size": 32,
                    "color": "#ffffff",
                    "stroke_color": "#000000",
                    "stroke_width": 2,
                    "opacity": 0.94 if float(scene.get("energy") or 0.0) >= 0.5 else 0.82,
                    "blend_mode": "normal",
                    "z": 20 + index,
                }
            )

        camera_keyframes.extend(
            [
                {
                    "t": start_s,
                    "zoom": zoom_start,
                    "pan_x": pan_x_start,
                    "pan_y": pan_y_start,
                    "rotation_deg": rotation_start,
                },
                {
                    "t": end_s,
                    "zoom": zoom_end,
                    "pan_x": pan_x_end,
                    "pan_y": pan_y_end,
                    "rotation_deg": rotation_end,
                },
            ]
        )
        prev_zoom = zoom_end
        prev_pan_x = pan_x_end
        prev_pan_y = pan_y_end
        prev_rotation = rotation_end

    return {
        "ok": bool(packed_scenes),
        "timeline": {
            "tracks": [prompt_track, motion_track],
            "layers": layers,
            "camera": {"keyframes": _dedupe_camera_keyframes(camera_keyframes)},
            "render": {"fps_output": 24},
            "duration_s": duration_s,
        },
        "notes": [
            "Prompt and motion tracks match the canonical Studio timeline schema.",
            "Lyric and transcript cues are converted into compositor text layers instead of a parallel overlay-track format.",
        ],
    }


def _build_creative_deforum_preview(
    packed_scenes: list[dict[str, Any]],
    duration_s: float,
    negative_prompt: str,
    fps: int = 30,
) -> dict[str, Any]:
    total_frames = max(1, int(round(max(duration_s, 1.0) * max(1, fps))))
    prompts: dict[str, str] = {}
    zoom_pairs: list[tuple[int, float]] = []
    angle_pairs: list[tuple[int, float]] = []
    translation_pairs: list[tuple[int, float]] = []
    translation_x_pairs: list[tuple[int, float]] = []
    translation_y_pairs: list[tuple[int, float]] = []
    rotation_x_pairs: list[tuple[int, float]] = []
    rotation_y_pairs: list[tuple[int, float]] = []
    cfg_pairs: list[tuple[int, float]] = []
    strength_pairs: list[tuple[int, float]] = []
    contrast_pairs: list[tuple[int, float]] = []

    for index, scene in enumerate(packed_scenes):
        start_frame = max(0, int(round(float(scene.get("start_s") or 0.0) * fps)))
        end_frame = max(start_frame + 1, int(round(float(scene.get("end_s") or 0.0) * fps)))
        params = scene.get("reactive_params") if isinstance(scene.get("reactive_params"), dict) else {}
        prompts[str(start_frame)] = str(scene.get("prompt") or "cinematic").strip() or "cinematic"
        zoom = float(params.get("zoom") or 1.0)
        angle = float(params.get("rotation_y") or params.get("rotation_z") or 0.0)
        rotation_x = float(params.get("rotation_x") or 0.0)
        rotation_y = float(params.get("rotation_y") or 0.0)
        translation = float(params.get("translation_z") or 0.0)
        translation_x = float(params.get("translation_x") or 0.0)
        translation_y = float(params.get("translation_y") or 0.0)
        cfg = float(params.get("cfg_scale") or 7.0)
        strength = float(params.get("strength") or 0.35)
        contrast = float(params.get("contrast") or 1.0)
        zoom_pairs.extend([(start_frame, zoom), (end_frame, zoom + max(0.01, float(scene.get("energy") or 0.0) * 0.02))])
        angle_pairs.extend([(start_frame, angle), (end_frame, angle + float(scene.get("energy") or 0.0) * 2.0)])
        rotation_x_pairs.extend([(start_frame, rotation_x), (end_frame, rotation_x)])
        rotation_y_pairs.extend([(start_frame, rotation_y), (end_frame, rotation_y)])
        translation_pairs.extend([(start_frame, translation), (end_frame, translation - float(scene.get("energy") or 0.0) * 2.0)])
        translation_x_pairs.extend([(start_frame, translation_x), (end_frame, translation_x)])
        translation_y_pairs.extend([(start_frame, translation_y), (end_frame, translation_y)])
        cfg_pairs.extend([(start_frame, cfg), (end_frame, cfg)])
        strength_pairs.extend([(start_frame, strength), (end_frame, strength)])
        contrast_pairs.extend([(start_frame, contrast), (end_frame, contrast)])

    schedules = {
        "zoom": _format_schedule_pairs(zoom_pairs) if zoom_pairs else "",
        "angle": _format_schedule_pairs(angle_pairs) if angle_pairs else "",
        "rotation_3d_x": _format_schedule_pairs(rotation_x_pairs) if rotation_x_pairs else "",
        "rotation_3d_y": _format_schedule_pairs(rotation_y_pairs) if rotation_y_pairs else "",
        "translation_x": _format_schedule_pairs(translation_x_pairs) if translation_x_pairs else "",
        "translation_y": _format_schedule_pairs(translation_y_pairs) if translation_y_pairs else "",
        "translation_z": _format_schedule_pairs(translation_pairs) if translation_pairs else "",
        "cfg_scale_schedule": _format_schedule_pairs(cfg_pairs) if cfg_pairs else "",
        "strength_schedule": _format_schedule_pairs(strength_pairs) if strength_pairs else "",
        "contrast_schedule": _format_schedule_pairs(contrast_pairs) if contrast_pairs else "",
    }

    return {
        "ok": bool(packed_scenes),
        "settings": {
            "animation_mode": "3D",
            "fps": fps,
            "max_frames": total_frames,
            "prompts": prompts or {"0": "cinematic"},
            "negative_prompts": {"0": negative_prompt},
            **{key: value for key, value in schedules.items() if value},
            "schedules": schedules,
        },
    }


def _build_creative_contract(
    proj: Any,
    plan: dict[str, Any],
    transcript_text: str,
    packed_scenes: list[dict[str, Any]],
    motifs: list[str],
    hooks: list[str],
    duration_s: float,
    bpm: float,
    provider_mode: str,
) -> dict[str, Any]:
    mode = "lyric-film" if transcript_text else "music-video"
    visual_tone = str(
        (plan.get("variants") or [{}])[0].get("mood")
        if isinstance((plan.get("variants") or [{}])[0], dict)
        else ""
    ).strip() or "cinematic reactive framing"

    return {
        "ok": True,
        "endpoint": "/v1/projects/:project_id/narrative_direction",
        "provider_mode": provider_mode,
        "request": {
            "title": str(getattr(proj, "name", "") or "Untitled project"),
            "transcript": transcript_text,
            "duration_s": duration_s,
            "bpm": bpm,
            "scene_count": len(packed_scenes),
            "mode": mode,
            "visual_tone": visual_tone,
            "anchors": motifs[:5],
            "hooks": hooks,
        },
        "expected_response_shape": {
            "ok": True,
            "creative_direction": {
                "scenes": [
                    {
                        "name": "string",
                        "start_s": 0,
                        "end_s": 0,
                        "prompt": "string",
                        "camera_hint": "string",
                        "motion_hint": "string",
                        "transcript_cue": "string",
                    }
                ]
            },
            "timeline_patch": {
                "timeline": {
                    "tracks": [{"type": "prompt"}, {"type": "motion"}],
                    "layers": [{"type": "text"}],
                }
            },
        },
    }


def _merge_creative_timeline_patch(
    base_timeline: dict[str, Any],
    patch_timeline: dict[str, Any],
    *,
    overwrite_tracks: bool,
    overwrite_camera: bool,
) -> dict[str, Any]:
    merged = {**(base_timeline or {})}
    base_tracks = [track for track in list(merged.get("tracks") or []) if isinstance(track, dict)]
    patch_tracks = [track for track in list(patch_timeline.get("tracks") or []) if isinstance(track, dict)]

    for patch_track in patch_tracks:
        track_type = str(patch_track.get("type") or "").lower()
        idx = next(
            (index for index, track in enumerate(base_tracks) if str(track.get("type") or "").lower() == track_type),
            -1,
        )
        if idx >= 0:
            if overwrite_tracks:
                base_tracks[idx] = patch_track
            else:
                existing_clips = [clip for clip in list(base_tracks[idx].get("clips") or []) if isinstance(clip, dict)]
                merged_clips = {str(clip.get("id") or f"clip_{index}"): clip for index, clip in enumerate(existing_clips)}
                for clip_index, clip in enumerate(list(patch_track.get("clips") or [])):
                    if not isinstance(clip, dict):
                        continue
                    merged_clips[str(clip.get("id") or f"patch_{clip_index}")] = clip
                base_tracks[idx] = {**base_tracks[idx], **patch_track, "clips": list(merged_clips.values())}
        else:
            base_tracks.append(patch_track)

    merged["tracks"] = base_tracks

    base_layers = [layer for layer in list(merged.get("layers") or []) if isinstance(layer, dict)]
    patch_layers = [layer for layer in list(patch_timeline.get("layers") or []) if isinstance(layer, dict)]
    merged_layers = {str(layer.get("id") or f"layer_{index}"): layer for index, layer in enumerate(base_layers)}
    for index, layer in enumerate(patch_layers):
        merged_layers[str(layer.get("id") or f"patch_layer_{index}")] = layer
    merged["layers"] = list(merged_layers.values())

    patch_camera = patch_timeline.get("camera") if isinstance(patch_timeline.get("camera"), dict) else {}
    base_camera = merged.get("camera") if isinstance(merged.get("camera"), dict) else {}
    if overwrite_camera or not list(base_camera.get("keyframes") or []):
        merged["camera"] = patch_camera or base_camera
    else:
        merged_keyframes = _dedupe_camera_keyframes(
            [keyframe for keyframe in list(base_camera.get("keyframes") or []) if isinstance(keyframe, dict)]
            + [keyframe for keyframe in list(patch_camera.get("keyframes") or []) if isinstance(keyframe, dict)]
        )
        merged["camera"] = {**base_camera, **patch_camera, "keyframes": merged_keyframes}

    patch_render = patch_timeline.get("render") if isinstance(patch_timeline.get("render"), dict) else {}
    if patch_render:
        merged["render"] = {**(merged.get("render") if isinstance(merged.get("render"), dict) else {}), **patch_render}

    if isinstance(patch_timeline.get("duration_s"), (int, float)):
        merged["duration_s"] = float(patch_timeline.get("duration_s"))
    return merged


def _build_creative_direction_payload(
    proj: Any,
    variant_index: int,
    preset: str,
    sensitivity: float,
    director_mode: str | None = None,
) -> dict[str, Any]:
    mode = normalize_director_mode(director_mode or preset)
    mode_profile = director_mode_profile(mode)
    reactive_preset = reactive_preset_for_mode(mode)
    analysis_raw = (proj.meta.get("analysis") or {}) if hasattr(proj, "meta") else {}
    analysis = analysis_raw if isinstance(analysis_raw, dict) else {}
    plan_raw = (proj.meta.get("last_plan") or {}) if hasattr(proj, "meta") else {}
    plan = plan_raw if isinstance(plan_raw, dict) else {}
    variants = list(plan.get("variants") or [])
    variant = variants[variant_index] if 0 <= variant_index < len(variants) else {}
    scenes = list(variant.get("scenes") or []) if isinstance(variant, dict) else []
    transcript_text = _analysis_transcript_text(analysis).strip()
    transcript_sentences = _analysis_transcript_sentences(analysis)
    hooks = _creative_hooks(transcript_sentences)
    saved_tags = list(analysis.get("tags") or []) if isinstance(analysis, dict) else []
    motifs = list(dict.fromkeys([*saved_tags, *_analysis_motifs(variant if isinstance(variant, dict) else {}, transcript_text)]))[:8]
    has_transcript = _has_usable_transcript(analysis)
    emotion_tokens = _creative_tokenize(" ".join([transcript_text, *[str(scene.get("prompt") or "") for scene in scenes if isinstance(scene, dict)]]))
    emotions = _creative_emotion_scores(emotion_tokens)
    overall = _infer_reactivity_metrics(analysis if isinstance(analysis, dict) else {})
    energy_curve = list(overall.get("energy_curve") or [])
    waveform = list(overall.get("waveform") or [])
    duration_s = float(overall.get("duration_s") or 0.0)
    saved_sections = list(analysis.get("sections") or []) if isinstance(analysis, dict) and isinstance(analysis.get("sections"), list) else []
    audio_meta = (proj.meta.get("audio") or {}) if hasattr(proj, "meta") and isinstance(proj.meta, dict) else {}
    music_graph = music_graph_from_analysis(
        analysis if isinstance(analysis, dict) else {},
        audio_filename=str(audio_meta.get("filename") or "") or None,
        duration_s=float(audio_meta.get("duration_s") or analysis.get("duration_s") or 0) or None,
    )
    if not saved_sections:
        graph_sections = list(music_graph.get("sections") or [])
        if graph_sections:
            saved_sections = [
                {
                    "start_s": float(item.get("start") or 0.0),
                    "end_s": float(item.get("end") or 0.0),
                    "label": str(item.get("label") or "section"),
                    "energy": item.get("energy"),
                    "confidence": item.get("confidence"),
                    "source": "music_graph",
                }
                for item in graph_sections
                if isinstance(item, dict)
            ]
    fallback_sections = saved_sections or _derive_reactive_sections(
        overall,
        duration_s,
        transcript_sentences,
        motifs,
        str(getattr(proj, "name", "") or "Untitled project"),
        reactive_preset,
        sensitivity,
        max_sections=min(8, max(3, len(scenes) or 6)),
    )
    source_scenes: list[dict[str, Any]] = scenes if scenes else fallback_sections
    scene_source = "plan" if scenes else "analysis_fallback" if fallback_sections else "none"
    provider_mode = _creative_provider_mode(plan)
    negative_prompt = next(
        (
            str(scene.get("negative_prompt") or "").strip()
            for scene in source_scenes
            if isinstance(scene, dict) and str(scene.get("negative_prompt") or "").strip()
        ),
        "blurry, low quality, watermark, text, logo",
    )

    packed_scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(source_scenes):
        name = str(scene.get("name") or f"Scene {index + 1}")
        start_s = float(scene.get("start_s") or index * 5.0)
        end_s = float(scene.get("end_s") or (start_s + 5.0))
        if scene_source == "analysis_fallback" and isinstance(scene.get("reactive_params"), dict):
            metrics = {
                "energy": float(scene.get("energy") or 0.0),
                "bass": max(0.0, min(1.0, float(overall.get("bass") or 0.0) * 0.85 + float(scene.get("peak_energy") or scene.get("energy") or 0.0) * 0.12)),
                "mid": max(0.0, min(1.0, float(overall.get("mid") or 0.0) * 0.85 + float(scene.get("avg_energy") or scene.get("energy") or 0.0) * 0.12)),
                "treble": max(0.0, min(1.0, float(overall.get("treble") or 0.0) * 0.85 + float(scene.get("peak_energy") or scene.get("energy") or 0.0) * 0.1)),
                "duration_s": max(0.2, end_s - start_s),
                "source": "analysis",
            }
            params = {key: float(value) for key, value in dict(scene.get("reactive_params") or {}).items() if isinstance(value, (int, float))}
            transcript_cue = str(scene.get("transcript_cue") or "").strip() if has_transcript else ""
            energy_label = str(scene.get("energy_label") or _creative_energy_label(float(metrics["energy"])))
            camera_hint = str(scene.get("camera_hint") or _creative_camera_hint(float(metrics["energy"])))
            motion_hint = str(scene.get("motion_hint") or _creative_motion_hint(params))
        else:
            metrics = _scene_metrics_from_curve(index, len(source_scenes) or 1, scene, overall, duration_s, energy_curve)
            params = _compute_reactive_params(metrics, reactive_preset, sensitivity)
            cue_index = (
                min(len(transcript_sentences) - 1, int((index / max(1, len(source_scenes) - 1)) * len(transcript_sentences)))
                if transcript_sentences and has_transcript else -1
            )
            transcript_cue = transcript_sentences[cue_index] if cue_index >= 0 else ""
            energy_label = _creative_energy_label(float(metrics["energy"]))
            camera_hint = _creative_camera_hint(float(metrics["energy"]))
            motion_hint = _creative_motion_hint(params)
        prompt = flavor_prompt(render_prompt_from_scene(scene, fallback=DEFAULT_RENDER_PROMPT), mode)
        camera_bias = str(mode_profile.get("camera_bias") or "").strip()
        if camera_bias and camera_bias.casefold() not in camera_hint.casefold():
            camera_hint = f"{camera_hint} {camera_bias}".strip()
        motion_bias = str(mode_profile.get("motion_bias") or "").strip()
        if motion_bias and motion_bias.casefold() not in motion_hint.casefold():
            motion_hint = f"{motion_hint} ({motion_bias})"
        scene_tokens = _analysis_top_keywords(" ".join([name, prompt, transcript_cue]), limit=5)
        scene_motifs = list(
            dict.fromkeys(
                [
                    *scene_tokens[:3],
                    *motifs[(index * 2): (index * 2) + 2],
                    *motifs[: max(0, 2 - len(scene_tokens[:3]))],
                ]
            )
        )[:4]
        phase_hint = (
            "Open the visual world clearly before adding pressure."
            if index == 0 else
            "Resolve the sequence with a release image and clean afterglow."
            if index == max(0, len(source_scenes) - 1) else
            "Push into a distinct section change instead of repeating the previous beat."
            if float(metrics["energy"]) >= 0.68 else
            "Use this section to vary texture, framing, or environment while holding continuity."
        )
        continuity_hint = (
            "Continuity: establish subject, palette, and world."
            if index == 0 else
            f"Continuity: retain the strongest subject and palette cues from scene {index}."
        )
        audio_anchor = (
            f"Audio anchor: follow the {energy_label.lower()} section arc with {scene_motifs[0]}."
            if not transcript_cue and scene_motifs
            else "Audio anchor: let motion and framing follow the section energy arc."
            if not transcript_cue
            else ""
        )
        prompt_pack = " ".join(
            [
                prompt,
                f"Director mode: {mode_profile.get('label') or mode}.",
                f"Energy profile: {energy_label}.",
                camera_hint,
                f"Motion recipe: {motion_hint}",
                f"Scene motifs: {', '.join(scene_motifs)}." if scene_motifs else "",
                phase_hint,
                continuity_hint,
                f"Narrative cue: {transcript_cue}" if transcript_cue else "",
                audio_anchor,
            ]
        ).strip()
        packed_scenes.append(
            {
                "index": index,
                "name": name,
                "start_s": start_s,
                "end_s": end_s,
                "duration_s": max(0.2, end_s - start_s),
                "energy": float(metrics["energy"]),
                "energy_label": energy_label,
                "prompt": prompt,
                "transcript_cue": transcript_cue,
                "camera_hint": camera_hint,
                "motion_hint": motion_hint,
                "prompt_pack": prompt_pack,
                "reactive_params": params,
                "scene_source": scene_source,
                "director_mode": mode,
            }
        )

    export_text = "\n\n".join(
        [
            (
                f"{scene['index'] + 1}. {scene['name']} ({scene['start_s']:.2f}s - {scene['end_s']:.2f}s)\n"
                f"{scene['prompt_pack']}"
            )
            for scene in packed_scenes
        ]
    )
    timeline_patch = _build_creative_timeline_patch(packed_scenes, duration_s or max([float(scene.get("end_s") or 0.0) for scene in packed_scenes] or [0.0]), negative_prompt)
    deforum_preview = _build_creative_deforum_preview(
        packed_scenes,
        duration_s or max([float(scene.get("end_s") or 0.0) for scene in packed_scenes] or [0.0]),
        negative_prompt,
        fps=30,
    )
    bpm = float(
        _pick_raw_number((analysis.get("features") or {}) if isinstance(analysis, dict) else {}, ["bpm", "tempo_bpm", "tempo"])
        or 0.0
    )
    narrative_analysis = {
        "ok": bool(transcript_text or motifs or packed_scenes),
        "title": str(getattr(proj, "name", "") or "Untitled project"),
        "provider_mode": provider_mode,
        "scene_source": scene_source,
        "emotions": emotions,
        "hooks": hooks,
        "motifs": motifs,
        "transcript_line_count": len(transcript_sentences),
        "segment_count": len(_analysis_transcript_segments(analysis)),
        "section_count": len(fallback_sections),
        "themes": list(analysis.get("themes") or []) if isinstance(analysis, dict) else [],
        "director_mode": mode,
    }
    llm_contract = _build_creative_contract(
        proj,
        plan,
        transcript_text,
        packed_scenes,
        motifs,
        hooks,
        duration_s,
        bpm,
        provider_mode,
    )

    missing: list[str] = []
    if not analysis:
        missing.append("analysis")
    if not variants:
        missing.append("plan")
    ready = bool(packed_scenes or fallback_sections or transcript_text)
    if analysis and scenes:
        status = "Creative direction is being derived on the backend from the saved Overview analysis and plan. Planner extends that base, and Reactive Lab can add motion scheduling on top."
    elif analysis and fallback_sections:
        status = "Plan not found. Using audio-reactive fallback sections derived from saved Overview analysis."
    elif scenes:
        status = "Audio analysis not found. Using saved plan scenes with narrative fallbacks."
    else:
        status = "Run audio analysis and generate a plan variant to unlock creative direction guidance."

    return {
        "ready": ready,
        "missing": missing,
        "preset": reactive_preset,
        "director_mode": mode,
        "director_profile": mode_profile,
        "sensitivity": float(sensitivity),
        "provider_mode": provider_mode,
        "scene_source": scene_source,
        "metrics": {
            "energy": float(overall["energy"]),
            "bass": float(overall["bass"]),
            "mid": float(overall["mid"]),
            "treble": float(overall["treble"]),
            "duration_s": duration_s,
            "source": "analysis",
        },
        "waveform": waveform,
        "motifs": motifs,
        "transcript_text": transcript_text,
        "transcript_summary": str(analysis.get("summary") or "").strip() or " ".join(transcript_sentences[:3]),
        "narrative_analysis": narrative_analysis,
        "music_graph": music_graph,
        "sections": fallback_sections,
        "scenes": packed_scenes,
        "export_text": export_text,
        "timeline_patch": timeline_patch,
        "deforum_preview": deforum_preview,
        "llm_contract": llm_contract,
        "notes": [
            "Creative direction now carries audio-reactive sections, timeline patch data, and a Deforum-aligned preview in one Studio-native payload.",
            "Prompt and motion tracks stay in the canonical timeline schema, while lyric cues are translated into compositor text layers.",
            "Overview analysis remains the canonical source. Planner enriches the storyboard, and Reactive Lab adds motion schedules without replacing the saved story pass.",
            f"Director mode `{mode}` maps reactive motion through the `{reactive_preset}` profile.",
        ],
        "status": status,
    }


def _format_schedule_pairs(pairs: list[tuple[int, float]]) -> str:
    try:
        from enhanced_deforum_music_generator.core.deforum_schedule_format import format_schedule  # type: ignore
        return format_schedule(pairs)
    except Exception:
        # fallback: "f:(v), ..."
        return ", ".join([f"{int(f)}:({float(v):.4f})" for f, v in pairs])


def _derive_steps_and_denoise_schedules(analysis_obj: Any, *, fps: int, base_steps: int = 15) -> tuple[str, str]:
    """Heuristic schedules from energy: higher energy -> more steps + higher denoise."""
    dur = float(getattr(analysis_obj, "duration", 0.0) or 0.0)
    energy = list(getattr(analysis_obj, "energy", []) or [])
    if not dur or not energy:
        # safe defaults
        steps = _format_schedule_pairs([(0, float(base_steps))])
        denoise = _format_schedule_pairs([(0, 0.35)])
        return steps, denoise

    n = min(64, max(8, len(energy)))
    pairs_steps: list[tuple[int, float]] = []
    pairs_d: list[tuple[int, float]] = []

    for i in range(n):
        u = i / max(1, n - 1)
        idx = int(round(u * (len(energy) - 1)))
        e = float(energy[idx])
        frame = int(round((u * dur) * fps))

        # steps: 10..28 around base_steps
        steps_v = max(8.0, min(36.0, float(base_steps) * (0.70 + 0.90 * e)))
        # denoise/strength: 0.20..0.85
        den_v = max(0.15, min(0.90, 0.20 + 0.65 * e))

        pairs_steps.append((frame, steps_v))
        pairs_d.append((frame, den_v))

    return _format_schedule_pairs(pairs_steps), _format_schedule_pairs(pairs_d)


def _local_plan_from_project(proj: Any, *, title: str, style_prefs: str, num_variants: int, max_scenes: int) -> dict[str, Any]:
    """Deterministic (no-LLM) plan builder using EDMG-core orchestrators."""
    analysis_obj = _build_public_audio_analysis(proj)
    analysis_meta = (proj.meta.get("analysis") or {}) if hasattr(proj, "meta") else {}
    fps = 24

    from enhanced_deforum_music_generator.core.prompt_orchestrator import PromptOrchestrator, OrchestrationConfig  # type: ignore
    from enhanced_deforum_music_generator.core.motion_orchestrator import MotionConfig, motion_schedules  # type: ignore

    orch = PromptOrchestrator(provider=None, cfg=OrchestrationConfig(fps=fps, max_scenes=max_scenes))
    motion = motion_schedules(analysis_obj, cfg=MotionConfig(fps=fps))

    # add steps + denoise schedules
    steps_sched, denoise_sched = _derive_steps_and_denoise_schedules(analysis_obj, fps=fps, base_steps=15)
    motion.setdefault("steps_schedule", steps_sched)
    motion.setdefault("denoise_schedule", denoise_sched)
    transcript_sentences = _analysis_transcript_sentences(analysis_meta)
    tags = list(analysis_meta.get("tags") or []) if isinstance(analysis_meta, dict) else []
    scene_roles = [
        "opening tableau",
        "first lift",
        "world expansion",
        "pressure turn",
        "release peak",
        "afterglow resolve",
    ]

    variants: list[dict[str, Any]] = []
    for vi in range(int(num_variants)):
        base_prompt = "cinematic, coherent subject, high detail, consistent style"
        style_prompt = style_prefs or ""
        out = orch.orchestrate(
            analysis_obj,
            base_prompt=base_prompt,
            style_prompt=style_prompt,
            negative_prompt="blurry, low quality, watermark, text, logo",
            use_ai=False,
        )
        fps_out = int(out.get("fps") or fps) or fps
        frames = [int(s.get("frame", 0)) for s in (out.get("scene_plan") or [])]
        frames = sorted({0, *frames})
        dur_s = float(getattr(analysis_obj, "duration", 0.0) or 0.0) or 60.0
        end_frame = int(round(dur_s * fps_out))
        if frames and frames[-1] < end_frame:
            frames.append(end_frame)

        prompts = out.get("prompts") or {}
        scenes: list[dict[str, Any]] = []
        for i in range(len(frames) - 1):
            a = frames[i]
            b = frames[i + 1]
            start_s = float(a) / float(fps_out)
            end_s = float(b) / float(fps_out)
            prompt_base = str(prompts.get(str(int(a))) or prompts.get(str(int(frames[max(0, i - 1)]))) or base_prompt).strip() or base_prompt
            role = scene_roles[min(len(scene_roles) - 1, int(round((i / max(1, max(1, len(frames) - 2))) * (len(scene_roles) - 1))))]
            cue_index = min(len(transcript_sentences) - 1, i) if transcript_sentences else -1
            narrative_cue = transcript_sentences[cue_index] if cue_index >= 0 else ""
            motif_window = list(dict.fromkeys([*tags[i:i + 3], *tags[: max(0, 3 - len(tags[i:i + 3]))]]))[:3]
            prompt_variant = " ".join(
                [
                    prompt_base,
                    f"section role {role}.",
                    f"scene motifs {', '.join(motif_window)}." if motif_window else "",
                    f"narrative cue {narrative_cue}" if narrative_cue else "",
                ]
            ).strip()
            scenes.append(
                {
                    "name": role.title(),
                    "start_s": start_s,
                    "end_s": end_s,
                    "prompt": prompt_variant,
                    "negative_prompt": "blurry, low quality, watermark, text, logo",
                }
            )

        variants.append(
            {
                "index": vi,
                "fps": fps_out,
                "duration_s": dur_s,
                "scenes": scenes,
                "motion_schedules": motion,
                "source": "local",
            }
        )

    return {"title": title, "duration_s": float(getattr(analysis_obj, "duration", 0.0) or 0.0) or 60.0, "variants": variants, "source": "local"}


def _workspace_audio_plan(project) -> dict:
    """Reuse the installed local planner for automatic, non-chargeable drafts."""
    plan = _local_plan_from_project(
        project, title=project.name, style_prefs="", num_variants=1, max_scenes=12,
    )
    return _enrich_normalized_plan(plan, project.meta.get("analysis") or {})


def _scene_energy_from_analysis(index: int, total: int, analysis: dict[str, Any]) -> float:
    overall = _infer_reactivity_metrics(analysis if isinstance(analysis, dict) else {})
    curve = list(overall.get("energy_curve") or [])
    if curve:
        pointer = min(len(curve) - 1, max(0, int(round((index / max(1, total - 1)) * (len(curve) - 1)))))
        try:
            return max(0.0, min(1.0, float(curve[pointer])))
        except Exception:
            pass
    return max(0.0, min(1.0, 0.3 + (index / max(1, total - 1)) * 0.45 if total > 1 else 0.5))


def _first_scene_text(scene: dict[str, Any], *keys: str) -> str:
    storyboard = scene.get("storyboard") if isinstance(scene.get("storyboard"), dict) else {}
    for source in (scene, storyboard):
        for key in keys:
            raw_value = source.get(key)
            if isinstance(raw_value, (dict, list, tuple, set)):
                continue
            value = str(raw_value or "").strip()
            if value:
                return " ".join(value.split())
    return ""


def _first_variant_scene_text(scenes: list[Any], *keys: str) -> str:
    for scene in scenes:
        if not isinstance(scene, dict):
            continue
        value = _first_scene_text(scene, *keys)
        if value:
            return value
    return ""


def _enrich_normalized_plan(plan: dict[str, Any], analysis: dict[str, Any]) -> dict[str, Any]:
    plan_out = deepcopy(plan if isinstance(plan, dict) else {})
    variants = list(plan_out.get("variants") or [])
    transcript_sentences = _analysis_transcript_sentences(analysis if isinstance(analysis, dict) else {})
    tags = [str(tag or "").strip() for tag in list((analysis or {}).get("tags") or []) if str(tag or "").strip()]
    scene_roles = [
        "opening tableau",
        "first lift",
        "world expansion",
        "pressure turn",
        "release peak",
        "afterglow resolve",
    ]
    high_energy_moves = [
        "cross-frame tracking push with the subject moving left-to-right",
        "decisive lateral sweep through foreground depth",
        "parallax-heavy drive that preserves the camera axis through the impact",
    ]
    mid_energy_moves = [
        "measured dolly with lateral travel",
        "steady side-to-side drift through foreground texture",
        "motivated pan that follows the subject through the frame",
    ]
    low_energy_moves = [
        "wide hold with a slow pan reveal",
        "quiet reframing around the subject with soft side drift",
        "negative-space composition with a restrained lateral glide",
    ]
    high_energy_actions = [
        "the lead crosses the frame and completes a decisive gesture on the beat",
        "the lead turns, advances, and interacts with a foreground prop through distinct poses",
        "the lead drives forward while wardrobe and nearby objects react to the movement",
    ]
    mid_energy_actions = [
        "the lead walks or turns through the set with readable pose progression",
        "the lead changes gaze and hand position while continuing in one screen direction",
        "the lead interacts with the environment instead of holding a static pose",
    ]
    low_energy_actions = [
        "the lead breathes, shifts weight, and slowly changes gaze",
        "the lead makes one restrained gesture while maintaining a natural living pose",
        "the lead moves gently through foreground depth without freezing",
    ]
    environment_cues = [
        "fabric, hair, haze, and practical light continue moving at different depths",
        "foreground particles and background atmosphere travel naturally through the shot",
        "props, reflections, and edge light react visibly to the subject and music",
    ]
    staging_cues = [
        "use foreground depth and moving light to keep the frame alive",
        "let the environment change the camera lane so the section does not repeat the last one",
        "keep the subject silhouette clear while varying lens distance and frame pressure",
    ]
    palette_defaults = [
        "silver fog and petrol green",
        "desaturated indigo and moonlit white",
        "crimson pulse and black chrome",
        "dusty gold and weathered teal",
    ]
    transition_cues = [
        "bridge into the next beat through motion continuity",
        "shift the camera lane before the next section lands",
        "let atmosphere and edge light carry the cut forward",
        "reset composition pressure on the next downbeat",
    ]
    shot_type_defaults = [
        "wide establishing composition",
        "moving medium shot with foreground depth",
        "profile close-up with readable action",
        "low-angle movement frame",
        "overhead geography reveal",
        "hero resolution composition",
    ]

    for variant_index, raw_variant in enumerate(variants):
        if not isinstance(raw_variant, dict):
            continue
        variant = dict(raw_variant)
        scenes = list(variant.get("scenes") or [])
        total = max(1, len(scenes))
        variant_motifs = [
            str(item or "").strip()
            for item in list(variant.get("visual_motifs") or [])
            if str(item or "").strip()
        ]
        default_subject = (
            f"one recurring lead subject associated with {variant_motifs[0]}, with the same recognizable "
            "face, silhouette, wardrobe, and signature prop"
            if variant_motifs
            else "one recurring lead subject with the same recognizable face, silhouette, wardrobe, and signature prop"
        )
        character_lock = (
            _first_variant_scene_text(
                scenes,
                "character_lock",
                "characterLock",
                "subject",
                "subject_anchor",
            )
            or default_subject
        )
        raw_palette = [
            str(item or "").strip()
            for item in list(variant.get("color_palette") or [])
            if str(item or "").strip()
        ]
        style_parts = [str(variant.get("mood") or "").strip()]
        if raw_palette:
            style_parts.append(f"{', '.join(raw_palette[:4])} palette")
        style_prefix = "; ".join(part for part in style_parts if part)
        style_lock = (
            _first_variant_scene_text(scenes, "style_lock", "styleLock", "visual_lock", "visualLock")
            or " ".join(
                part
                for part in (
                    f"{style_prefix};" if style_prefix else "",
                    "consistent medium, texture, lighting logic, lens family, contrast, and aspect ratio",
                )
                if part
            )
        )
        setting_anchor = (
            _first_variant_scene_text(scenes, "setting", "location", "location_hint", "locationHint")
            or (
                f"one geographically continuous cinematic world organized around {variant_motifs[0]}, "
                "with stable landmark placement and screen axis"
                if variant_motifs
                else "one geographically continuous cinematic world with stable landmark placement and screen axis"
            )
        )
        previous_end_state = ""
        next_scenes: list[dict[str, Any]] = []
        for scene_index, raw_scene in enumerate(scenes):
            if not isinstance(raw_scene, dict):
                continue
            scene = dict(raw_scene)
            role = scene_roles[min(len(scene_roles) - 1, int(round((scene_index / max(1, total - 1)) * (len(scene_roles) - 1))))]
            energy = _scene_energy_from_analysis(scene_index, total, analysis if isinstance(analysis, dict) else {})
            camera_fallback = (
                high_energy_moves[(scene_index + variant_index) % len(high_energy_moves)]
                if energy >= 0.72
                else mid_energy_moves[(scene_index + variant_index) % len(mid_energy_moves)]
                if energy >= 0.44
                else low_energy_moves[(scene_index + variant_index) % len(low_energy_moves)]
            )
            action_fallback = (
                high_energy_actions[(scene_index + variant_index) % len(high_energy_actions)]
                if energy >= 0.72
                else mid_energy_actions[(scene_index + variant_index) % len(mid_energy_actions)]
                if energy >= 0.44
                else low_energy_actions[(scene_index + variant_index) % len(low_energy_actions)]
            )
            staging = staging_cues[(scene_index + variant_index) % len(staging_cues)]
            cue_index = min(len(transcript_sentences) - 1, scene_index) if transcript_sentences else -1
            narrative_cue = transcript_sentences[cue_index] if cue_index >= 0 else ""
            motif_window = list(dict.fromkeys([*tags[scene_index:scene_index + 2], *tags[: max(0, 2 - len(tags[scene_index:scene_index + 2]))]]))[:2]
            palette_note = motif_window[0] if motif_window else palette_defaults[(scene_index + variant_index) % len(palette_defaults)]
            subject = _first_scene_text(scene, "subject", "subject_anchor") or character_lock
            setting = (
                _first_scene_text(scene, "setting", "location", "location_hint", "locationHint")
                or setting_anchor
            )
            shot_type = (
                _first_scene_text(scene, "shot_type", "shotType", "composition")
                or shot_type_defaults[(scene_index + variant_index) % len(shot_type_defaults)]
            )
            camera = _first_scene_text(scene, "camera", "camera_hint") or camera_fallback
            action = _first_scene_text(scene, "action", "shot_action") or action_fallback
            subject_motion = _first_scene_text(scene, "motion", "motion_hint") or action
            environment_motion = (
                _first_scene_text(scene, "environment_motion", "environmentMotion")
                or environment_cues[(scene_index + variant_index) % len(environment_cues)]
            )
            continuity = _first_scene_text(scene, "continuity_note", "continuityNote", "continuity") or (
                f"establish {character_lock}; keep exactly one lead subject and a consistent screen direction"
                if scene_index == 0
                else (
                    f"continue {character_lock}; preserve identity, wardrobe, {palette_note} palette, world, and "
                    "screen direction from the preceding scene while the action advances"
                )
            )
            transition = (
                _first_scene_text(scene, "transition", "transition_cue", "transitionCue")
                or transition_cues[(scene_index + variant_index) % len(transition_cues)]
            )
            authored_start_state = _first_scene_text(scene, "start_state", "startState", "opening_state")
            start_state = previous_end_state or authored_start_state or (
                f"{character_lock} begins in a readable pose inside {setting}, oriented left-to-right; "
                f"the {shot_type} camera is settled before the action begins"
            )
            end_state = _first_scene_text(scene, "end_state", "endState", "closing_state") or (
                f"{character_lock} completes {action} inside {setting} in a readable handoff pose; "
                f"identity, wardrobe, landmark placement, left-to-right screen direction, and the {camera} camera axis remain stable"
            )
            previous_end_state = end_state
            prompt = str(
                scene.get("prompt")
                or scene.get("prompt_pack")
                or render_prompt_from_scene(scene, fallback=DEFAULT_RENDER_PROMPT)
            ).strip()
            prompt_lower = prompt.lower()
            additions: list[str] = []
            structured_additions = (
                (("setting:",), f"setting: {setting}."),
                (("shot composition:", "shot type:"), f"shot composition: {shot_type}."),
                (("visible action:", "continuous action:"), f"visible action: {action}."),
                (("subject motion:",), f"subject motion: {subject_motion}."),
                (("environment motion:",), f"environment motion: {environment_motion}."),
                (("camera path:", "camera move:"), f"camera path: {camera}."),
                (("character lock:",), f"character lock: {character_lock}."),
                (("style lock:",), f"style lock: {style_lock}."),
                (("start state:",), f"start state: {start_state}."),
                (("end state:",), f"end state: {end_state}."),
                (("continuity:",), f"continuity: {continuity}."),
                (("transition:",), f"transition: {transition}."),
            )
            # Structured fields are authoritative after editing or reordering.
            # Replace complete labeled clauses, including values containing periods.
            labels = [marker for markers, _ in structured_additions for marker in markers]
            boundary = "|".join(re.escape(label) for label in labels)
            for markers, addition in structured_additions:
                if markers[0] not in {"setting:", "shot composition:", "character lock:",
                                      "style lock:", "start state:", "end state:"}:
                    continue
                pattern = (
                    r"(?:" + "|".join(re.escape(marker) for marker in markers) + r")\s*.*?"
                    r"(?=\s+(?:" + boundary + r"|section role |staging |palette emphasis |scene motifs |narrative cue )|$)"
                )
                prompt = re.sub(pattern, lambda _, replacement=addition: replacement,
                                prompt, flags=re.IGNORECASE | re.DOTALL)
            prompt_lower = prompt.lower()
            for markers, addition in structured_additions:
                if not any(marker in prompt_lower for marker in markers):
                    additions.append(addition)
            for marker, addition in (
                ("section role ", f"section role {role}."),
                ("staging ", f"staging {staging}."),
                ("palette emphasis ", f"palette emphasis {palette_note}."),
            ):
                if marker not in prompt_lower:
                    additions.append(addition)
            if motif_window and "scene motifs " not in prompt_lower:
                additions.append(f"scene motifs {', '.join(motif_window)}.")
            if (
                narrative_cue
                and "narrative cue " not in prompt_lower
                and narrative_cue.lower() not in prompt_lower
            ):
                additions.append(f"narrative cue {narrative_cue}.")

            enriched_prompt = " ".join([prompt, *additions]).strip()
            scene["prompt"] = enriched_prompt
            scene["prompt_pack"] = enriched_prompt
            scene["subject"] = subject
            scene["setting"] = setting
            scene["shot_type"] = shot_type
            scene["character_lock"] = character_lock
            scene["style_lock"] = style_lock
            scene["start_state"] = start_state
            scene["end_state"] = end_state
            scene["action"] = action
            scene["camera"] = camera
            scene["motion"] = subject_motion
            scene["environment_motion"] = environment_motion
            if not isinstance(scene.get("continuity"), (dict, list)):
                scene["continuity"] = continuity
            scene["continuity_note"] = continuity
            scene["camera_hint"] = camera
            scene["motion_hint"] = subject_motion
            scene["storyboard"] = {
                "subject_anchor": subject,
                "setting": setting,
                "shot_type": shot_type,
                "character_lock": character_lock,
                "style_lock": style_lock,
                "start_state": start_state,
                "end_state": end_state,
                "shot_action": action,
                "camera_move": camera,
                "subject_motion": subject_motion,
                "environment_motion": environment_motion,
                "continuity": continuity,
                "transition": transition,
            }
            scene["render_prompt"] = operational_render_prompt_from_scene(
                scene,
                fallback=enriched_prompt,
                max_words=CLIP_SAFE_RENDER_PROMPT_MAX_WORDS,
                include_states=False,
            )
            if authored_start_state and authored_start_state != start_state:
                scene["storyboard"]["authored_start_state"] = authored_start_state
            negative_prompt = str(scene.get("negative_prompt") or DEFAULT_NEGATIVE_PROMPT).strip()
            continuity_negatives = (
                "still frame",
                "frozen pose",
                "slideshow",
                "collage",
                "storyboard sheet",
                "duplicate subject",
                "identity drift",
                "wardrobe change",
                "style drift",
                "location jump",
                "landmark drift",
                "camera teleport",
                "discontinuous action",
                "conflicting camera moves",
            )
            negative_lower = negative_prompt.lower()
            missing_negatives = [term for term in continuity_negatives if term not in negative_lower]
            scene["negative_prompt"] = ", ".join(
                part for part in (negative_prompt, *missing_negatives) if part
            )
            if not str(scene.get("name") or "").strip() or re.fullmatch(r"scene\s*\d+", str(scene.get("name") or "").strip(), re.IGNORECASE):
                scene["name"] = role.title()
            scene["transition"] = transition
            next_scenes.append(scene)
        variant["scenes"] = next_scenes
        variants[variant_index] = variant

    plan_out["variants"] = variants
    return plan_out


def _coerce_scene_time(raw: Any, fallback: float) -> float:
    try:
        return max(0.0, float(raw))
    except Exception:
        return max(0.0, float(fallback))


def _normalize_plan_scene_list(
    scenes: Any,
    *,
    duration_s: float | None,
    max_scenes: int,
) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    source = scenes if isinstance(scenes, list) else []
    for index, raw_scene in enumerate(source):
        if not isinstance(raw_scene, dict):
            continue
        start_s = _coerce_scene_time(raw_scene.get("start_s"), float(index))
        end_s = _coerce_scene_time(raw_scene.get("end_s"), start_s + 1.0)
        if end_s <= start_s:
            end_s = start_s + 0.5
        scene = dict(raw_scene)
        scene["start_s"] = start_s
        scene["end_s"] = end_s
        authored_prompt = str(
            raw_scene.get("prompt")
            or raw_scene.get("prompt_pack")
            or render_prompt_from_scene(raw_scene, fallback=DEFAULT_RENDER_PROMPT)
        ).strip()
        scene["prompt"] = authored_prompt or DEFAULT_RENDER_PROMPT
        scene["negative_prompt"] = negative_prompt_from_scene(raw_scene, fallback=DEFAULT_NEGATIVE_PROMPT)
        normalized.append(scene)

    normalized.sort(key=lambda scene: (_coerce_scene_time(scene.get("start_s"), 0.0), _coerce_scene_time(scene.get("end_s"), 0.0)))

    if not normalized:
        if duration_s and duration_s > 0:
            return [
                {
                    "start_s": 0.0,
                    "end_s": float(duration_s),
                    "prompt": DEFAULT_RENDER_PROMPT,
                    "negative_prompt": DEFAULT_NEGATIVE_PROMPT,
                }
            ]
        return []

    limit = max(1, int(max_scenes or len(normalized)))
    if len(normalized) > limit:
        normalized = normalized[:limit]

    final_duration = float(duration_s or 0.0)
    if final_duration <= 0:
        final_duration = max(_coerce_scene_time(scene.get("end_s"), 0.0) for scene in normalized)
    if final_duration <= 0:
        final_duration = max(0.5, float(len(normalized)))

    source_duration = max(_coerce_scene_time(scene.get("end_s"), 0.0) for scene in normalized)
    if source_duration > 0 and final_duration > source_duration + 1e-6:
        scale = final_duration / source_duration
        for scene in normalized:
            scene["start_s"] = _coerce_scene_time(scene.get("start_s"), 0.0) * scale
            scene["end_s"] = _coerce_scene_time(scene.get("end_s"), 0.0) * scale

    if len(normalized) >= 3 and final_duration > 0:
        last_start = _coerce_scene_time(normalized[-1].get("start_s"), 0.0)
        last_end = _coerce_scene_time(normalized[-1].get("end_s"), 0.0)
        prior_lengths = [
            max(0.0, _coerce_scene_time(scene.get("end_s"), 0.0) - _coerce_scene_time(scene.get("start_s"), 0.0))
            for scene in normalized[:-1]
        ]
        typical_prior = sorted(prior_lengths)[len(prior_lengths) // 2] if prior_lengths else 0.0
        final_scene_len = max(0.0, last_end - last_start)
        if (
            last_end >= final_duration - 1e-6
            and last_start < final_duration * 0.5
            and final_scene_len > max(final_duration * 0.25, typical_prior * 4.0)
        ):
            slice_s = final_duration / float(len(normalized))
            for index, scene in enumerate(normalized):
                scene["start_s"] = float(index) * slice_s
                scene["end_s"] = final_duration if index == len(normalized) - 1 else float(index + 1) * slice_s

    carry_start = 0.0
    for index, scene in enumerate(normalized):
        scene["start_s"] = carry_start
        if index == len(normalized) - 1:
            scene["end_s"] = max(carry_start + 0.05, final_duration)
        else:
            proposed_end = _coerce_scene_time(scene.get("end_s"), carry_start + 0.5)
            scene["end_s"] = max(carry_start + 0.05, min(proposed_end, final_duration))
        carry_start = float(scene["end_s"])

    return normalized


def _normalize_plan_payload(
    plan: dict[str, Any],
    *,
    requested_variants: int,
    requested_max_scenes: int,
    duration_s_hint: float | None,
) -> dict[str, Any]:
    normalized = dict(plan or {})
    variants_raw = normalized.get("variants") if isinstance(normalized.get("variants"), list) else []
    variants: list[dict[str, Any]] = []
    limit = max(1, int(requested_variants or 1))

    for raw_variant in list(variants_raw)[:limit]:
        if not isinstance(raw_variant, dict):
            continue
        variant = dict(raw_variant)
        raw_duration = _coerce_scene_time(
            raw_variant.get("duration_s") or normalized.get("duration_s") or duration_s_hint,
            duration_s_hint or 0.0,
        )
        variant_duration = max(float(raw_duration or 0.0), float(duration_s_hint or 0.0))
        variant["duration_s"] = variant_duration
        variant["scenes"] = _normalize_plan_scene_list(
            raw_variant.get("scenes"),
            duration_s=variant_duration or duration_s_hint,
            max_scenes=requested_max_scenes,
        )
        variants.append(variant)

    normalized["variants"] = variants
    if duration_s_hint and duration_s_hint > 0:
        normalized["duration_s"] = float(duration_s_hint)
    elif variants:
        normalized["duration_s"] = max(_coerce_scene_time((variant or {}).get("duration_s"), 0.0) for variant in variants)
    normalized["source"] = str(normalized.get("source") or "local")
    return normalized


def _merge_imported_analysis(base: Any, imported: dict[str, Any]) -> dict[str, Any]:
    current = deepcopy(base) if isinstance(base, dict) else {}
    incoming = imported if isinstance(imported, dict) else {}
    base_features = current.get("features") if isinstance(current.get("features"), dict) else {}
    next_features = incoming.get("features") if isinstance(incoming.get("features"), dict) else {}
    current["features"] = {**base_features, **next_features}

    transcript = incoming.get("transcript")
    if isinstance(transcript, dict) and str(transcript.get("text") or "").strip():
        current["transcript"] = transcript

    tags = []
    for raw in [*(current.get("tags") or []), *(incoming.get("tags") or [])]:
        text = str(raw or "").strip()
        if text and text not in tags:
            tags.append(text)
    if tags:
        current["tags"] = tags

    current["source"] = str(incoming.get("source") or current.get("source") or "imported")
    return current


def _load_project_visual_dna(proj: Any):
    return load_visual_dna(
        store.project_dir(proj.id),
        project_id=str(proj.id),
        project_name=str(getattr(proj, "name", "") or "") or None,
    )


def _save_project_visual_dna(proj: Any, dna):
    return save_visual_dna(store.project_dir(proj.id), dna)


def _project_response_payload(proj: Any) -> dict[str, Any]:
    dna = _load_project_visual_dna(proj)
    return {
        "project": proj.__dict__,
        "visual_dna": dna.model_dump(mode="json"),
        "visual_dna_hints": build_visual_dna_prompt_hints(dna),
    }


def _enqueue_timeline_render(project_id: str, req: TimelineRenderRequest) -> dict[str, Any]:
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    timeline = proj.meta.get("timeline")
    if not isinstance(timeline, dict):
        raise HTTPException(400, "Project timeline is missing or invalid")
    try:
        render_plan = prepare_timeline_render_plan(
            ffmpeg_path=settings.ffmpeg_path,
            project_dir=store.project_dir(project_id),
            timeline=timeline,
        )
    except (RuntimeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    settings_payload = req.model_dump(mode="json")
    job = jobs.create(
        project_id,
        "timeline_render",
        {
            "settings": settings_payload,
            "timeline": render_plan,
        },
    )
    jobs.update_progress(
        project_id,
        job.id,
        stage="queued",
        current=0,
        total=1000,
        message="Timeline render queued",
    )
    proj.meta["last_timeline_render_request"] = {
        "job_id": job.id,
        "status": "queued",
        **settings_payload,
    }
    store.save(proj)
    persisted = jobs.get(project_id, job.id) or job
    return {"ok": True, "job": persisted.__dict__}


app.include_router(create_system_router(
    readiness_report=_system_readiness_report,
    baseline_metrics=_baseline_metrics_report,
))
app.include_router(
    create_project_router(
        get_store=lambda: store,
        project_response=_project_response_payload,
        assess_health=assess_project_health,
        enqueue_timeline_render=_enqueue_timeline_render,
    )
)
app.include_router(create_models_router(get_models=lambda: models))


def _render_quality_tier_from_preset(preset: str | None) -> str:
    preset_l = str(preset or "balanced").strip().lower()
    if preset_l == "fast":
        return "draft"
    if preset_l == "quality":
        return "quality"
    if preset_l == "ultra":
        return "ultra"
    return "balanced"


def _default_speed_priority(quality_tier: str) -> float:
    return {
        "draft": 0.85,
        "balanced": 0.55,
        "quality": 0.35,
        "ultra": 0.25,
    }.get(str(quality_tier or "balanced"), 0.55)


def _build_render_conductor_intent(project_id: str, proj: Any, req: RenderConductorPlanRequest) -> RenderIntent:
    quality_tier = str(req.quality_tier or _render_quality_tier_from_preset(req.preset))
    dna = _load_project_visual_dna(proj)
    continuity_default = 0.8 if list(dna.continuity.subject_anchors or []) else 0.72
    return RenderIntent.model_validate(
        {
            "project_id": project_id,
            "variant_index": int(req.variant_index or 0),
            "aspect_ratio": req.aspect_ratio,
            "output_mode": req.output_mode,
            "quality_tier": quality_tier,
            "continuity_priority": continuity_default if req.continuity_priority is None else req.continuity_priority,
            "speed_priority": _default_speed_priority(quality_tier) if req.speed_priority is None else req.speed_priority,
            "style_lock_strength": 0.8 if req.style_lock_strength is None else req.style_lock_strength,
            "allowed_engines": list(req.allowed_engines or []),
            "fallback_policy": req.fallback_policy,
            "sections": [section.model_dump(mode="json") for section in list(req.sections or [])],
        }
    )


def _build_render_conductor_environment() -> dict[str, Any]:
    hw = _hardware_profile()
    provider_status = _render_provider_status(hw)
    runtime = _internal_diffusion_runtime_status()
    installed_internal = any(
        _internal_model_is_available(model_id)
        for model_id in ("hf_sd15_internal", "hf_sdxl_internal", "hf_sd35_medium_internal")
    )
    backend_family = str(hw.get("backend_family") or "cpu_only").lower()
    if backend_family == "discrete_gpu":
        internal_quality = 0.92
        internal_speed = 0.74
    elif backend_family == "integrated_gpu":
        internal_quality = 0.8
        internal_speed = 0.56
    else:
        internal_quality = 0.66
        internal_speed = 0.32

    ckpt, _fallback = _resolve_comfy_checkpoint_name(settings.comfyui_checkpoint, allow_auto_fallback=True)
    try:
        base_diag = comfy_pool.diagnose({"checkpoint": ckpt})
    except Exception:
        base_diag = {"compatible": [], "busy_compatible": []}
    base_ok = bool(base_diag.get("compatible") or base_diag.get("busy_compatible"))
    ad_ok = False
    svd_ok = False
    if base_ok:
        try:
            ad_diag = comfy_pool.diagnose(
                {
                    "checkpoint": ckpt,
                    "node_classes": ["ADE_StandardStaticContextOptions", "ADE_AnimateDiffLoaderGen1"],
                    "est_steps": 20,
                    "est_frames": 24,
                }
            )
            ad_ok = bool(ad_diag.get("compatible") or ad_diag.get("busy_compatible"))
        except Exception:
            ad_ok = False
        try:
            svd_diag = comfy_pool.diagnose(
                {
                    "checkpoint": ckpt,
                    "node_classes": ["SVDSimpleImg2Vid"],
                    "est_steps": 20,
                    "est_frames": 14,
                }
            )
            svd_ok = bool(svd_diag.get("compatible") or svd_diag.get("busy_compatible"))
        except Exception:
            svd_ok = False
    try:
        deforum_ok = bool(core_status().get("ok"))
    except Exception:
        deforum_ok = False
    tensorrt_ok = _tensorrt_sd15_bundle_available()

    diagnostics = [
        f"internal_runtime={'ready' if runtime.get('ok') else 'missing'}",
        f"internal_models={'installed' if installed_internal else 'missing'}",
        f"comfyui_still={'ready' if base_ok else 'unavailable'}",
        f"comfyui_motion={'ready' if (ad_ok or svd_ok) else 'unavailable'}",
        f"hosted_stability={'ready' if _hosted_stability_ready({'allow_hosted_fallback': True}) else 'unavailable'}",
        f"deforum_export={'ready' if deforum_ok else 'unavailable'}",
        f"tensorrt_standalone={'ready' if tensorrt_ok else 'unavailable'}",
    ]
    return {
        "hardware": hw,
        "providers": provider_status,
        "diagnostics": diagnostics,
        "engines": {
            "internal": {
                "available": bool(runtime.get("ok") and installed_internal),
                "quality_score": internal_quality,
                "speed_score": internal_speed,
            },
            "comfyui_still": {
                "available": base_ok,
                "quality_score": 0.84,
                "speed_score": 0.58,
            },
            "comfyui_motion": {
                "available": bool(ad_ok or svd_ok),
                "quality_score": 0.8 if ad_ok else 0.74,
                "speed_score": 0.62 if ad_ok else 0.57,
            },
            "hosted_video": {
                "available": _hosted_stability_ready({"allow_hosted_fallback": True}),
                "quality_score": 0.78,
                "speed_score": 0.82,
            },
            "deforum_export": {
                "available": deforum_ok,
                "quality_score": 0.7,
                "speed_score": 0.45,
            },
            "tensorrt_standalone": {
                "available": tensorrt_ok,
                "quality_score": 0.82,
                "speed_score": 0.88,
            },
        },
    }


def _build_project_snapshot(proj: Any, *, dna: Any | None = None) -> ProjectSnapshot:
    visual_dna = dna or _load_project_visual_dna(proj)
    return ProjectSnapshot(
        project_id=str(proj.id),
        project_name=str(getattr(proj, "name", "") or "") or None,
        analysis=(proj.meta.get("analysis") or {}) if isinstance(proj.meta, dict) else {},
        plan=(proj.meta.get("last_plan") or {}) if isinstance(proj.meta, dict) else {},
        timeline=(proj.meta.get("timeline") or {}) if isinstance(proj.meta, dict) else {},
        visual_dna=visual_dna,
    )


def _apply_plan_to_project_timeline(proj: Any, *, variant_index: int, overwrite: bool) -> dict[str, Any]:
    plan = proj.meta.get("last_plan")
    if not isinstance(plan, dict):
        from .domain.director_workflow import workflow_state
        current_workflow = workflow_state(proj)
        plan = current_workflow.get("plan") if current_workflow["status"] == "draft" else None
        if not isinstance(plan, dict):
            raise HTTPException(400, "No plan. Analyze audio or generate a plan first.")
    variants = plan.get("variants") if isinstance(plan.get("variants"), list) else []
    vi = int(variant_index or 0)
    if not variants or vi < 0 or vi >= len(variants):
        raise HTTPException(400, "Invalid variant_index")
    variant = variants[vi] if isinstance(variants[vi], dict) else {}
    scenes = variant.get("scenes") if isinstance(variant.get("scenes"), list) else []
    duration_s = float(_project_duration_hint_s(proj, variant, scenes) or variant.get("duration_s") or plan.get("duration_s") or 60.0)

    timeline = proj.meta.get("timeline") if isinstance(proj.meta.get("timeline"), dict) else {}
    timeline = {**timeline}
    timeline["duration_s"] = duration_s

    tracks = timeline.get("tracks") if isinstance(timeline.get("tracks"), list) else []
    tracks = [t for t in tracks if isinstance(t, dict)]

    def upsert_track(tid: str, name: str, ttype: str, clips: list[dict[str, Any]]) -> None:
        nonlocal tracks
        idx = next((i for i, t in enumerate(tracks) if str(t.get("id") or "") == tid or str(t.get("type") or "").lower() == ttype.lower()), -1)
        if idx >= 0:
            if overwrite or not tracks[idx].get("clips"):
                tracks[idx] = {**tracks[idx], "id": tid, "name": name, "type": ttype, "clips": clips}
        else:
            tracks.append({"id": tid, "name": name, "type": ttype, "clips": clips})

    prompt_clips: list[dict[str, Any]] = []
    for i, s in enumerate(scenes):
        try:
            ss = float(s.get("start_s", 0.0))
            ee = float(s.get("end_s", ss + 1.0))
        except Exception:
            ss, ee = 0.0, 1.0
        prompt_clips.append(
            {
                "id": f"edmg_prompt_{i}",
                "start_s": ss,
                "end_s": ee,
                "data": {
                    "prompt": render_prompt_from_scene(s, fallback=""),
                    "negative_prompt": negative_prompt_from_scene(s, fallback=""),
                },
            }
        )
    upsert_track("edmg_prompt", "EDMG Prompts", "prompt", prompt_clips)

    ms = variant.get("motion_schedules") if isinstance(variant.get("motion_schedules"), dict) else {}
    if not ms:
        try:
            aa = _build_public_audio_analysis(proj)
            from enhanced_deforum_music_generator.core.motion_orchestrator import MotionConfig, motion_schedules  # type: ignore
            ms = motion_schedules(aa, cfg=MotionConfig(fps=24))
            steps_sched, denoise_sched = _derive_steps_and_denoise_schedules(aa, fps=24, base_steps=15)
            ms.setdefault("steps_schedule", steps_sched)
            ms.setdefault("denoise_schedule", denoise_sched)
        except Exception:
            ms = {}
    motion_clip = {
        "id": "edmg_motion_0",
        "start_s": 0.0,
        "end_s": duration_s,
        "data": {**ms},
    }
    upsert_track("edmg_motion", "EDMG Motion", "motion", [motion_clip])

    timeline["tracks"] = tracks

    cam = timeline.get("camera") if isinstance(timeline.get("camera"), dict) else {}
    cam = {**cam}
    kfs = cam.get("keyframes") if isinstance(cam.get("keyframes"), list) else []
    if overwrite or not kfs:
        fps = 24
        zoom_s = str(ms.get("zoom") or "")
        ang_s = str(ms.get("angle") or "")

        def _parse_sched(s: str) -> list[tuple[int, float]]:
            pairs = []
            for part in str(s or "").split(","):
                part = part.strip()
                if not part:
                    continue
                m = re.match(r"^(\d+)\s*:\s*\(?\s*([-+]?\d*\.?\d+)\s*\)?$", part)
                if not m:
                    continue
                pairs.append((int(m.group(1)), float(m.group(2))))
            return sorted(pairs, key=lambda x: x[0])

        def _sample(pairs: list[tuple[int, float]], frame: int) -> float:
            if not pairs:
                return 0.0
            if frame <= pairs[0][0]:
                return float(pairs[0][1])
            if frame >= pairs[-1][0]:
                return float(pairs[-1][1])
            for i in range(len(pairs) - 1):
                fa, va = pairs[i]
                fb, vb = pairs[i + 1]
                if fa <= frame <= fb:
                    if fb <= fa:
                        return float(vb)
                    w = (frame - fa) / max(1.0, (fb - fa))
                    return float(va) * (1.0 - w) + float(vb) * w
            return float(pairs[-1][1])

        zp = _parse_sched(zoom_s)
        ap = _parse_sched(ang_s)
        frames = sorted({f for f, _ in zp} | {f for f, _ in ap})
        kfs = []
        if frames:
            for f in frames:
                kfs.append({"t": f / fps, "zoom": _sample(zp, f) or 1.0, "pan_x": 0.0, "pan_y": 0.0, "rotation_deg": _sample(ap, f)})
        elif duration_s > 0:
            kfs = [{"t": 0.0, "zoom": 1.0, "pan_x": 0.0, "pan_y": 0.0, "rotation_deg": 0.0}]
        cam["keyframes"] = kfs
        timeline["camera"] = cam

    proj.meta["timeline"] = timeline
    return timeline


@app.post("/v1/projects/{project_id}/plan")
def generate_plan(project_id: str, req: PlanRequest, mode: str = "auto"):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    analysis = proj.meta.get("analysis") or {}
    feats = (analysis.get("features") or {})
    transcript = _analysis_transcript_text(analysis)
    duration_hint = _project_duration_hint_s(proj, analysis=analysis)

    payload = {
        "title": req.title or proj.name,
        "user_notes": req.user_notes,
        "duration_s": duration_hint or feats.get("duration_s") or feats.get("duration"),
        "bpm": feats.get("bpm") or feats.get("tempo_bpm") or feats.get("tempo"),
        "lyrics": transcript,
        "tags": (analysis.get("tags") or []),
        "style_prefs": req.style_prefs,
        "num_variants": req.num_variants,
        "max_scenes": req.max_scenes,
    }

    mode_norm = str(mode or "auto").lower().strip()
    if mode_norm not in ("auto", "ai", "local", "edmg_core"):
        mode_norm = "auto"

    plan = None
    if mode_norm in ("ai", "auto"):
        try:
            plan = ai.plan(payload)
            if isinstance(plan, dict):
                plan.setdefault("source", "ai")
        except Exception as e:
            if mode_norm == "ai":
                # strict AI mode
                raise UserFacingError(
                    message="The configured planning/transcription provider is not available.",
                    hint=(
                        "Fix: If you're using Ollama, make sure it is installed and running (Ollama app or `ollama serve`), "
                        "and that the model is pulled (e.g., `ollama pull qwen3:8b`). "
                        "If you want a remote AI, set EDMG_AI_MODE=http and EDMG_AI_BASE_URL to the running AI service."
                    ),
                    code="AI_UNAVAILABLE",
                    status_code=502,
                )
            plan = None

    if plan is None:
        # deterministic local fallback (no LLM)
        plan = _local_plan_from_project(
            proj,
            title=req.title or proj.name,
            style_prefs=req.style_prefs or "",
            num_variants=req.num_variants,
            max_scenes=req.max_scenes,
        )

    if isinstance(plan, dict):
        plan = _normalize_plan_payload(
            plan,
            requested_variants=req.num_variants,
            requested_max_scenes=req.max_scenes,
            duration_s_hint=duration_hint,
        )
        plan = apply_core_style_direction(plan, req.style_prefs)
        plan = _enrich_normalized_plan(plan, analysis if isinstance(analysis, dict) else {})

    proj.meta["last_plan"] = plan
    attach_schedule_drafts(proj, resulting_revision=proj.revision + 1)
    store.save(proj)
    return plan


@app.post("/v1/projects/{project_id}/analyze_and_plan")
def analyze_and_build_plan(project_id: str, req: PlanRequest, mode: str = "auto"):
    analyze_audio(project_id)
    return generate_plan(project_id, req, mode)


@app.post("/v1/projects/{project_id}/timeline/apply_plan")
def apply_plan_to_timeline(project_id: str, req: ApplyPlanRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    timeline = _apply_plan_to_project_timeline(
        proj,
        variant_index=int(req.variant_index or 0),
        overwrite=bool(req.overwrite),
    )
    store.save(proj)
    return {"ok": True, "timeline": timeline, "variant_index": int(req.variant_index or 0)}


@app.post("/v1/projects/{project_id}/plan/variant")
def update_plan_variant(project_id: str, req: StoryboardVariantUpdateRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    plan = proj.meta.get("last_plan")
    if not isinstance(plan, dict):
        from .domain.director_workflow import workflow_state
        current_workflow = workflow_state(proj)
        plan = current_workflow.get("plan") if current_workflow["status"] == "draft" else None
        if not isinstance(plan, dict):
            raise HTTPException(400, "No plan. Analyze audio or generate a plan first.")

    variants = plan.get("variants") if isinstance(plan.get("variants"), list) else []
    variant_index = int(req.variant_index or 0)
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "Invalid variant_index")

    variant = variants[variant_index] if isinstance(variants[variant_index], dict) else {}
    duration_hint = _coerce_scene_time(
        _project_duration_hint_s(proj, variant, variant.get("scenes") if isinstance(variant, dict) else []),
        _analysis_duration_s(proj.meta.get("analysis") or {}),
    )

    updated_variant = dict(variant)
    updated_variant["scenes"] = _normalize_plan_scene_list(
        req.scenes,
        duration_s=duration_hint,
        max_scenes=max(1, len(req.scenes or [])),
    )
    updated_variant["duration_s"] = max(
        duration_hint,
        max(
            (_coerce_scene_time(scene.get("end_s"), 0.0) for scene in updated_variant["scenes"]),
            default=duration_hint or 0.0,
        ),
    )
    variants[variant_index] = updated_variant

    normalized_plan = _normalize_plan_payload(
        {**plan, "variants": variants},
        requested_variants=max(1, len(variants)),
        requested_max_scenes=max([len((item or {}).get("scenes") or []) for item in variants] or [1]),
        duration_s_hint=_project_duration_hint_s(proj, updated_variant, updated_variant["scenes"]) or plan.get("duration_s"),
    )
    normalized_plan = _enrich_normalized_plan(
        normalized_plan,
        proj.meta.get("analysis") if isinstance(proj.meta.get("analysis"), dict) else {},
    )
    proj.meta["last_plan"] = normalized_plan
    attach_schedule_drafts(proj, resulting_revision=proj.revision + 1, variant_indices={variant_index})

    planner_lab = proj.meta.get("last_planner_lab")
    if isinstance(planner_lab, dict):
        planner_plan = planner_lab.get("plan")
        if isinstance(planner_plan, dict):
            planner_variants = planner_plan.get("variants") if isinstance(planner_plan.get("variants"), list) else []
            if 0 <= variant_index < len(planner_variants) and isinstance(planner_variants[variant_index], dict):
                planner_variant = dict(planner_variants[variant_index])
                normalized_variants = (
                    normalized_plan.get("variants")
                    if isinstance(normalized_plan.get("variants"), list)
                    else []
                )
                normalized_variant = (
                    normalized_variants[variant_index]
                    if 0 <= variant_index < len(normalized_variants)
                    and isinstance(normalized_variants[variant_index], dict)
                    else updated_variant
                )
                planner_variant["scenes"] = deepcopy(normalized_variant.get("scenes") or [])
                planner_variants[variant_index] = planner_variant
                planner_lab["plan"] = {**planner_plan, "variants": planner_variants}

    store.save(proj)
    return {"ok": True, "plan": normalized_plan, "variant_index": variant_index}


@app.post("/v1/projects/{project_id}/planner_lab/import")
def import_planner_lab(project_id: str, req: PlannerLabImportRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    imported_analysis = planner_lab_to_project_analysis(req.analysis)
    if imported_analysis:
        proj.meta["analysis"] = enrich_with_multitrack_defaults(
            _merge_imported_analysis(proj.meta.get("analysis"), imported_analysis)
        )

    imported_plan = planner_lab_to_canonical_plan(req.analysis, req.plan, req.settings)
    scene_counts = [
        len(variant.get("scenes") or [])
        for variant in list(imported_plan.get("variants") or [])
        if isinstance(variant, dict)
    ]
    normalized_plan = _normalize_plan_payload(
        imported_plan,
        requested_variants=max(1, len(imported_plan.get("variants") or [])),
        requested_max_scenes=max(scene_counts or [1]),
        duration_s_hint=_project_duration_hint_s(proj, analysis=proj.meta.get("analysis") or imported_analysis),
    )
    normalized_plan = apply_core_style_direction(
        normalized_plan,
        str((req.settings or {}).get("promptStyle") or ""),
    )
    normalized_plan = _enrich_normalized_plan(
        normalized_plan,
        proj.meta.get("analysis") if isinstance(proj.meta.get("analysis"), dict) else {},
    )
    proj.meta["last_plan"] = normalized_plan
    attach_schedule_drafts(proj, resulting_revision=proj.revision + 1)
    proj.meta["last_planner_lab"] = {
        "analysis": deepcopy(req.analysis),
        "plan": deepcopy(req.plan),
        "settings": deepcopy(req.settings),
        "imported_at": time.time(),
    }
    visual_dna = _load_project_visual_dna(proj)
    visual_dna = ingest_visual_dna_planner_payload(
        visual_dna,
        analysis=deepcopy(req.analysis) if isinstance(req.analysis, dict) else {},
        plan=deepcopy(req.plan) if isinstance(req.plan, dict) else {},
        settings=deepcopy(req.settings) if isinstance(req.settings, dict) else {},
    )
    saved_dna = _save_project_visual_dna(proj, visual_dna)

    timeline = None
    if req.apply_timeline:
        timeline = _apply_plan_to_project_timeline(
            proj,
            variant_index=0,
            overwrite=bool(req.overwrite_timeline),
        )

    store.save(proj)
    return {
        "ok": True,
        "plan": normalized_plan,
        "timeline": timeline,
        "visual_dna": saved_dna.model_dump(mode="json"),
        "visual_dna_hints": build_visual_dna_prompt_hints(saved_dna),
    }


@app.post("/v1/projects/{project_id}/reactive_lab/apply")
def apply_reactive_lab(project_id: str, req: ReactiveLabApplyRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    payload = {
        "metadata": deepcopy(req.metadata),
        "keyframes": deepcopy(req.keyframes),
        "beat_markers": deepcopy(req.beat_markers),
        "cue_events": deepcopy(req.cue_events),
        "sections": deepcopy(req.sections),
        "repair_suggestions": deepcopy(req.repair_suggestions),
        "schedules": deepcopy(req.schedules),
        "handoff_manifest": deepcopy(req.handoff_manifest),
    }
    timeline = merge_reactive_lab_into_timeline(
        proj.meta.get("timeline"),
        payload,
        overwrite_motion_track=bool(req.overwrite_motion_track),
        overwrite_camera=bool(req.overwrite_camera),
    )
    proj.meta["timeline"] = timeline
    proj.meta["last_reactive_lab"] = {**payload, "applied_at": time.time()}
    visual_dna = _load_project_visual_dna(proj)
    visual_dna = ingest_visual_dna_reactive_payload(
        visual_dna,
        payload=payload,
    )
    saved_dna = _save_project_visual_dna(proj, visual_dna)
    store.save(proj)
    return {
        "ok": True,
        "timeline": timeline,
        "visual_dna": saved_dna.model_dump(mode="json"),
        "visual_dna_hints": build_visual_dna_prompt_hints(saved_dna),
    }


@app.get("/v1/jobs")
def list_jobs():
    return {"jobs": [j.__dict__ for j in jobs.list_all()]}

@app.get("/v1/projects/{project_id}/jobs")
def list_project_jobs(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    return {"jobs": [j.__dict__ for j in jobs.list_for_project(project_id)]}


@app.get("/v1/projects/{project_id}/jobs/{job_id}")
def get_project_job(project_id: str, job_id: str, tail_lines: int = 80):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.get(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _job_detail_payload(project_id, job, tail_lines=tail_lines)

@app.post("/v1/projects/{project_id}/jobs/{job_id}/cancel")
def cancel_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.cancel(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    if job.status != "canceled":
        raise HTTPException(409, {"code": "JOB_ALREADY_TERMINAL", "message": "The completed job could not be canceled.", "status": job.status})
    return {"ok": True, "job": job.__dict__}


@app.post("/v1/projects/{project_id}/jobs/{job_id}/pause")
def pause_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    current = jobs.get(project_id, job_id)
    if not current:
        raise HTTPException(404, "Job not found")
    if current.status != "queued":
        raise HTTPException(409, "Only queued jobs can be paused")
    job = jobs.pause(project_id, job_id)
    if not job or job.status != "paused":
        raise HTTPException(409, "Job could not be paused because its state changed")
    return {"ok": True, "job": job.__dict__}


@app.post("/v1/projects/{project_id}/jobs/{job_id}/resume")
def resume_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    current = jobs.get(project_id, job_id)
    if not current:
        raise HTTPException(404, "Job not found")
    if current.status != "paused":
        raise HTTPException(409, "Only paused jobs can be resumed")
    job = jobs.resume(project_id, job_id)
    if not job or job.status != "queued":
        raise HTTPException(409, "Job could not be resumed because its state changed")
    return {"ok": True, "job": job.__dict__}


@app.post("/v1/projects/{project_id}/jobs/{job_id}/retry")
def retry_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    source_job = jobs.get(project_id, job_id)
    if not source_job:
        raise HTTPException(404, "Job not found")
    if source_job.status not in ("succeeded", "failed", "canceled"):
        raise HTTPException(409, "Only completed, failed, or canceled jobs can be retried")
    legacy_selection_note: str | None = None
    retry_payload: dict[str, Any] | None = None
    if source_job.type == "internal_video":
        retry_payload, legacy_selection_note = _repair_legacy_internal_video_selection(
            deepcopy(source_job.payload or {})
        )
        preflight = _internal_render_preflight_data(project_id, retry_payload)
        retry_payload = _persist_resolved_internal_video_payload(retry_payload, preflight)
    job = jobs.retry(project_id, job_id, payload=retry_payload)
    if not job:
        raise HTTPException(409, "Job could not be retried because its state changed")
    if legacy_selection_note:
        jobs.append_log(project_id, job.id, legacy_selection_note)
    return {"ok": True, "job": job.__dict__}


@app.post("/v1/projects/{project_id}/jobs/{job_id}/resume_from_checkpoint")
def resume_internal_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    source_job = jobs.get(project_id, job_id)
    if not source_job:
        raise HTTPException(404, "Job not found")
    if source_job.type != "internal_video":
        raise HTTPException(400, "Resume from checkpoint is only available for internal render jobs")
    if source_job.status in ("queued", "paused", "running"):
        raise HTTPException(409, "Job is still active. Resume or cancel it before resuming from checkpoint.")
    return _enqueue_internal_job_from_source(project_id, source_job, resume_existing_frames=True, queue_action="resume_from_checkpoint")


@app.post("/v1/projects/{project_id}/jobs/{job_id}/restart_clean")
def restart_internal_job_clean(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    source_job = jobs.get(project_id, job_id)
    if not source_job:
        raise HTTPException(404, "Job not found")
    if source_job.type != "internal_video":
        raise HTTPException(400, "Clean restart is only available for internal render jobs")
    if source_job.status in ("queued", "paused", "running"):
        raise HTTPException(409, "Job is still active. Resume or cancel it before starting a clean restart.")
    return _enqueue_internal_job_from_source(project_id, source_job, resume_existing_frames=False, queue_action="restart_clean")


@app.post("/v1/projects/{project_id}/jobs/{job_id}/clear_cached_frames")
def clear_project_job_cached_frames(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.get(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _mutate_internal_job_artifacts(project_id, job, clear_cached_frames=True, drop_checkpoint=False)


@app.post("/v1/projects/{project_id}/jobs/{job_id}/drop_checkpoint")
def drop_project_job_checkpoint(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.get(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return _mutate_internal_job_artifacts(project_id, job, clear_cached_frames=False, drop_checkpoint=True)


@app.get("/v1/projects/{project_id}/jobs/{job_id}/log")
def get_job_log(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    lp = jobs.log_path(project_id, job_id)
    if not lp.exists():
        return {"ok": True, "log": ""}
    return {"ok": True, "log": lp.read_text(encoding="utf-8", errors="ignore")}


@app.get("/v1/projects/{project_id}/jobs/{job_id}/events")
def get_job_events(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.get(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {"ok": True, "events": jobs.list_events(project_id, job_id)}


@app.post("/v1/jobs/tick")
def tick_worker():
    """Manual single-step worker tick (useful for debugging)."""
    job = jobs.claim_next_queued()
    if not job:
        return {"ok": True, "note": "no queued jobs"}
    _dispatch_job(job)
    latest = jobs.get(job.project_id, job.id) or job
    return {"ok": True, "job": latest.__dict__}

def _run_assemble_variant(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = AssembleVideoRequest(**(payload or {}))
    return assemble_video(project_id, req)


def _tensorrt_deforum_compatibility_result(result: dict[str, Any] | None) -> dict[str, Any]:
    sanitized = _without_private_render_paths(result or {})
    compatible = sanitized if isinstance(sanitized, dict) else {}
    compatible.pop("video_abs", None)
    relative_output = compatible.get("video") or compatible.get("output_path")
    compatible.pop("output_path", None)
    if isinstance(relative_output, str) and not _is_absolute_filesystem_location(relative_output):
        compatible["output_path"] = relative_output
    compatible["compatibility_route"] = "tensorrt-deforum"
    compatible["execution_mode"] = "canonical_tensorrt_keyframe_video"
    compatible["legacy_deforum_schedule_applied"] = False
    return compatible


def _public_render_job_error(exc: Exception) -> str:
    """Return only curated failure details suitable for the Studio job UI."""

    if isinstance(exc, UserFacingError):
        parts = [str(exc.message or "Render job failed.").strip()]
        if exc.hint:
            parts.append(f"Fix: {str(exc.hint).strip()}")
        if exc.code:
            parts.append(f"Code: {str(exc.code).strip()}")
        return "\n".join(part for part in parts if part)
    hint = hint_from_exception(exc)
    return "Render job failed." + (f"\nFix: {hint}" if hint else "")


def _execute_job(job):
    from .revisions import background_context, revision_context, merge_owned_fields
    state = {"baselines": {}, "pending": []}
    token = background_context.set(state)
    revision_token = revision_context.set(None)
    try:
        _execute_job_body(job)
    finally:
        background_context.reset(token)
        revision_context.reset(revision_token)
    try:
        with jobs.publication_guard(job.project_id, job.id, attempt=job.attempt) as active:
            if active and job.status == "succeeded":
                def publish(current):
                    for project_id, baseline, edited in state["pending"]:
                        if project_id != current.id:
                            raise ValueError("Job attempted to publish another project")
                        current.meta = merge_owned_fields(current.meta, baseline, edited)
                if state["pending"]:
                    store.mutate(job.project_id, publish)
                from .store.artifacts import _write_atomic as publish_artifact
                for artifact_path, artifact_payload in state.get("artifacts", []):
                    publish_artifact(artifact_path, artifact_payload)
            elif not active:
                return
            jobs.save(job)
    except Exception as exc:
        job.status = "failed"
        job.error = _public_render_job_error(exc)
        jobs.save(job)


def _execute_job_body(job):
    jobs.append_log(job.project_id, job.id, f"Started job type={job.type}")

    try:
        if job.type == "qwen_director":
            from .services.qwen_director import run_director_job
            job.result = run_director_job(
                job.payload,
                models,
                cancel_check=lambda: not _job_attempt_active(job),
                progress_fn=lambda stage, message: jobs.update_progress(
                    job.project_id, job.id, stage=stage, current=0, total=1, message=message,
                    expected_attempt=job.attempt,
                ),
            )
            if _job_attempt_active(job):
                jobs.update_progress(
                    job.project_id, job.id, stage="draft_ready", current=1, total=1,
                    message="Director draft ready for review",
                    expected_attempt=job.attempt,
                )
            job.status = "succeeded"
        elif job.type == "comfyui_scene":
            res = _run_comfyui_scene(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "internal_still_scene":
            res = _run_internal_still_scene(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "comfyui_motion_scene":
            res = _run_comfyui_motion_scene(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "assemble_variant":
            res = _run_assemble_variant(job.project_id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "internal_video":
            res = _run_internal_video(job.project_id, job.id, job.payload)
            latest = jobs.get(job.project_id, job.id)
            if latest and latest.status == "canceled":
                job.status = "canceled"
                job.result = latest.result
            else:
                job.result = res
                job.status = "succeeded"
        elif job.type == "timeline_render":
            res = _run_timeline_render(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "performer_video":
            res = _run_performer_video(job.project_id, job.id, job.payload)
            latest = jobs.get(job.project_id, job.id)
            if latest and latest.status == "canceled":
                job.status = "canceled"
                job.result = latest.result
            else:
                job.result = res
                job.status = "succeeded"
        elif job.type == "tensorrt_standalone":
            res = _run_tensorrt_standalone(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        elif job.type == "tensorrt_deforum":
            compatibility_payload = dict(job.payload or {})
            for private_key in _PRIVATE_RENDER_PATH_KEYS:
                compatibility_payload.pop(private_key, None)
            compatibility_payload["model_id"] = TENSORRT_VIDEO_MODEL_ID
            compatibility_payload["render_mode"] = "tensorrt"
            job.payload = compatibility_payload
            res = _run_internal_video(job.project_id, job.id, compatibility_payload)
            latest = jobs.get(job.project_id, job.id)
            if latest and latest.status == "canceled":
                job.status = "canceled"
                job.result = _tensorrt_deforum_compatibility_result(latest.result)
            else:
                job.result = _tensorrt_deforum_compatibility_result(res)
                job.status = "succeeded"
        elif job.type == "layered_animation":
            res = _run_layered_animation(job.project_id, job.id, job.payload)
            job.result = res
            job.status = "succeeded"
        else:
            job.status = "failed"
            job.error = f"Unknown job type: {job.type}"
    except JobCanceled as e:
        job.status = "canceled"
        job.error = None
        latest = jobs.get(job.project_id, job.id)
        if latest and latest.result:
            job.result = latest.result
        if job.type == "tensorrt_deforum":
            job.result = _tensorrt_deforum_compatibility_result(job.result)
        jobs.append_log(job.project_id, job.id, str(e) or "Job canceled during execution")
    except Exception as exc:
        logger.error(
            "Render job failed: project=%s job=%s type=%s",
            job.project_id,
            job.id,
            job.type,
            exc_info=(type(exc), exc, exc.__traceback__),
        )
        latest = jobs.get(job.project_id, job.id)
        if latest and latest.status == "canceled":
            job.status = "canceled"
            job.error = None
        else:
            job.status = "failed"
            job.error = _public_render_job_error(exc)
            checkpoint_job = latest or job
            _terminalize_failed_runtime_checkpoint(
                job.project_id,
                checkpoint_job,
                message=job.error.splitlines()[0],
            )

    jobs.append_log(job.project_id, job.id, f"Finished status={job.status}")
    if job.error:
        jobs.append_log(job.project_id, job.id, f"Error: {job.error}")

    latest = jobs.get(job.project_id, job.id)
    if latest and isinstance(latest.progress, dict):
        job.progress = latest.progress


def _job_in_subprocess_enabled() -> bool:
    """Whether the claimed job should run in an isolated child process.

    Disabled under pytest so the test suite keeps running jobs in-process
    (TestClient starts the worker via lifespan, and tests assert on in-process
    state / temporary data dirs).
    """
    if not settings.render_subprocess:
        return False
    if "PYTEST_CURRENT_TEST" in os.environ or "pytest" in sys.modules:
        return False
    return True


def _render_worker_command(project_id: str, job_id: str, *, attempt: int | None = None) -> list[str]:
    """Build the CLI command for an isolated render worker.

    Source launches need Python's module selector. A PyInstaller-frozen backend
    executable already enters ``edmg_studio_backend.cli`` directly, so passing
    ``-m edmg_studio_backend`` would be parsed as invalid CLI arguments.
    """
    cmd = [sys.executable]
    if not getattr(sys, "frozen", False):
        cmd.extend(["-m", "edmg_studio_backend"])
    cmd.extend(["run-job", "--project", project_id, "--job", job_id])
    if attempt is not None:
        cmd.extend(["--attempt", str(attempt)])
    return cmd


class _WorkerProcessStartedError(RuntimeError):
    """Execution already started; retrying in the API could duplicate work."""


def _stop_render_process(proc) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
    except OSError:
        logger.exception("Could not terminate the render process; waiting before forced shutdown")
    try:
        proc.wait(timeout=5.0)
    except subprocess.TimeoutExpired:
        try:
            proc.kill()
        except OSError:
            logger.exception("Could not kill the render process; retaining admission until it exits")
        # Keep the model admission slot until the process has actually exited.
        proc.wait()


def _run_job_in_subprocess(job) -> None:
    """Execute a claimed job in a separate Python process.

    The child writes progress, logs and results to the shared SQLite job store. This keeps
    CPU/GIL-bound rendering out of the API process so the UI stays responsive
    and cancellation still works.
    """
    if not _job_attempt_active(job):
        return
    cmd = _render_worker_command(job.project_id, job.id, attempt=job.attempt)
    jobs.append_log(job.project_id, job.id, "Dispatching to isolated render process")
    popen_kwargs: dict[str, Any] = {"env": os.environ.copy()}
    if os.name == "nt":
        # Keep the foreground API/UI snappy while the render grinds.
        popen_kwargs["creationflags"] = (
            getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    proc = subprocess.Popen(cmd, **popen_kwargs)

    cancel_deadline: float | None = None
    try:
        while True:
            try:
                proc.wait(timeout=1.0)
                break
            except subprocess.TimeoutExpired:
                latest = jobs.get(job.project_id, job.id)
                obsolete = latest is None or latest.attempt != job.attempt
                if obsolete or latest.status == "canceled":
                    if obsolete:
                        _stop_render_process(proc)
                        break
                    if cancel_deadline is None:
                        cancel_deadline = time.monotonic() + max(1.0, settings.render_subprocess_cancel_grace_s)
                    elif time.monotonic() >= cancel_deadline:
                        jobs.append_log(
                            job.project_id, job.id,
                            "Cancel grace period elapsed; terminating render process",
                        )
                        _stop_render_process(proc)
                        break
        rc = proc.returncode
        latest = jobs.get(job.project_id, job.id)
        if latest is None or latest.attempt != job.attempt:
            return
        if latest.status in ("succeeded", "failed", "canceled"):
            return
        # Child exited without finalizing the job (crash / OOM / killed).
        latest.status = "failed"
        latest.error = f"Render worker process exited unexpectedly (exit code {rc})."
        _terminalize_failed_runtime_checkpoint(job.project_id, latest, message=latest.error)
        latest = jobs.get(job.project_id, job.id)
        if latest is None or latest.attempt != job.attempt or latest.status in ("succeeded", "failed", "canceled"):
            return
        latest.status = "failed"
        latest.error = f"Render worker process exited unexpectedly (exit code {rc})."
        jobs.save(latest)
        jobs.append_log(job.project_id, job.id, latest.error)
    except BaseException as exc:
        try:
            _stop_render_process(proc)
        finally:
            # Every failure after Popen, including finalization or cleanup, is
            # ineligible for the legacy launch-failure fallback.
            if not isinstance(exc, Exception):
                raise
            raise _WorkerProcessStartedError("Could not monitor or finalize the isolated worker") from exc


_LOCAL_MODEL_JOB_TYPES = frozenset({
    "qwen_director", "internal_still_scene", "internal_video", "performer_video",
    "tensorrt_standalone", "tensorrt_deforum", "comfyui_scene", "comfyui_motion_scene",
})


def _job_attempt_active(job) -> bool:
    latest = jobs.get(job.project_id, job.id)
    return bool(
        latest and latest.attempt == job.attempt and latest.status in ("queued", "running")
    )


def _dispatch_job(job) -> None:
    """Serialize local model jobs through their complete child-process lifetime."""
    from .services.model_load_coordinator import ModelLoadCanceled, model_load_lock

    with jobs.maintain_lease(job):
        if not _job_attempt_active(job):
            return
        if job.type not in _LOCAL_MODEL_JOB_TYPES:
            _dispatch_admitted_job(job)
            return

        def waiting() -> None:
            jobs.update_progress(
                job.project_id, job.id, stage="waiting_for_model", current=0, total=1,
                message="Waiting for the current local model job to finish. You can cancel this job.",
                expected_attempt=job.attempt,
            )

        try:
            with model_load_lock(
                settings.models_dir / ".runtime",
                cancel_check=lambda: not _job_attempt_active(job), on_wait=waiting,
            ):
                if not _job_attempt_active(job):
                    return
                release_cached_internal_pipelines()
                jobs.update_progress(
                    job.project_id, job.id, stage="starting_worker", current=0, total=1,
                    message="Starting the local model worker",
                    expected_attempt=job.attempt,
                )
                try:
                    _dispatch_admitted_job(job)
                finally:
                    # In-process legacy/test execution can retain pipelines. Drop
                    # them before the next admitted model checks live memory.
                    from .services.internal_video_models import clear_video_pipeline_cache
                    clear_video_pipeline_cache()
                    release_cached_internal_pipelines()
        except ModelLoadCanceled:
            # Cancellation/retry is already authoritative in the job store.
            return
        except Exception as exc:
            logger.exception("Local model worker admission failed: %s", job.id)
            job.status = "failed"
            job.error = _public_render_job_error(exc)
            jobs.save(job)


def _dispatch_admitted_job(job) -> None:
    """Worker entry point: run the job in a child process when enabled."""
    if job.type == "qwen_director":
        # Director inference must never fall back into the API process.
        try:
            _run_job_in_subprocess(job)
        except Exception:
            job.status = "failed"
            logger.exception("Director worker failed: %s", job.id)
            job.error = "Director worker failed. Check the backend log, then retry the draft."
            jobs.save(job)
        return
    if _job_in_subprocess_enabled():
        try:
            _run_job_in_subprocess(job)
            return
        except _WorkerProcessStartedError:
            job.status = "failed"
            job.error = "The isolated worker stopped after a monitoring error. Review its outputs before retrying."
            jobs.save(job)
            return
        except Exception as exc:  # pragma: no cover - launch failure fallback
            logger.warning(
                "Isolated render process could not start; running in-process",
                exc_info=(type(exc), exc, exc.__traceback__),
            )
            jobs.append_log(
                job.project_id,
                job.id,
                "Isolated render process could not start; running in-process.",
            )
    _execute_job(job)


# Initialize always-on worker manager now that _execute_job exists
worker = WorkerManager(
    jobs=jobs,
    run_job=_dispatch_job,
    concurrency=settings.worker_concurrency,
    poll_interval_s=settings.worker_poll_interval_s,
)


def _prepare_still_scene_assets(project_id: str, payload: dict[str, Any], workflow_family: str) -> dict[str, Any]:
    source_asset = str(payload.get("source_asset") or payload.get("reference_asset") or "").strip()
    mask_asset = str(payload.get("inpaint_mask") or "").strip()
    outpaint = _normalize_outpaint(payload.get("outpaint"))

    source_path: Path | None = None
    mask_path: Path | None = None
    mask_source: str | None = None

    if workflow_family == "img2img":
        source_path = _resolve_project_reference_path(project_id, source_asset)
        if source_path is None:
            raise UserFacingError(
                "No source image selected for img2img",
                hint="Upload or choose a project source image before running img2img.",
                code="IMG2IMG_SOURCE_MISSING",
                status_code=400,
            )
    elif workflow_family == "inpaint":
        source_path = _resolve_project_reference_path(project_id, source_asset)
        mask_path = _resolve_project_mask_path(project_id, mask_asset)
        if source_path is None or mask_path is None:
            raise UserFacingError(
                "Source image or mask is missing",
                hint="Choose both a source image and a mask before running an inpaint render.",
                code="INPAINT_ASSETS_MISSING",
                status_code=400,
            )
        mask_source = "explicit_mask"
    elif workflow_family == "outpaint":
        prepared = _prepare_outpaint_assets(
            project_id,
            source_asset=source_asset,
            outpaint=outpaint,
            mask_asset=mask_asset or None,
        )
        source_path = prepared["source_path"]
        mask_path = prepared["mask_path"]
        mask_source = prepared.get("mask_source")
        outpaint = prepared.get("outpaint")

    width = int(payload.get("width") or 0)
    height = int(payload.get("height") or 0)
    if workflow_family == "outpaint" and source_path is not None and Image is not None:
        with Image.open(source_path) as generated_source:
            width, height = generated_source.size

    return {
        "source_path": source_path,
        "mask_path": mask_path,
        "mask_source": mask_source,
        "outpaint": outpaint,
        "width": width,
        "height": height,
    }


def _prepare_internal_controlnet_units(project_id: str, units: list[dict[str, Any]]) -> list[dict[str, Any]]:
    prepared: list[dict[str, Any]] = []
    for unit in units:
        model_ref = str(unit.get("model") or unit.get("controlnet_name") or "").strip()
        if not model_ref:
            raise UserFacingError(
                "ControlNet model is missing",
                hint="Pick an internal ControlNet model before running the render.",
                code="CONTROLNET_MODEL_MISSING",
                status_code=400,
            )
        asset = models.resolve_internal_asset(model_ref, folder="controlnet", allowed_kinds={"controlnet"})
        ref_path = _resolve_project_reference_path(project_id, str(unit.get("reference_asset") or ""))
        if ref_path is None:
            raise UserFacingError(
                "Reference image not found",
                hint="Upload or choose a valid project reference image before running the ControlNet render.",
                code="REFERENCE_IMAGE_NOT_FOUND",
                status_code=400,
            )
        conditioned = _prepare_condition_image(project_id, ref_path, str(unit.get("conditioning_mode") or "raw"))
        prepared.append(
            {
                **unit,
                "path": str(asset.get("path") or ""),
                "family": asset.get("family"),
                "reference_path": str(conditioned),
            }
        )
    return prepared


def _run_internal_still_scene(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = str(payload.get("prompt") or "")
    workflow_family = str(payload.get("workflow_family") or "txt2img")
    model_id = str(payload.get("model_id") or "")
    raw_model_path = str(payload.get("model_path") or "").strip()
    model_path = Path(raw_model_path) if raw_model_path else None
    if model_path is None or not model_path.exists():
        installed = _resolve_installed_model_path(model_id, materialize_remote=True)
        if installed is None:
            raise UserFacingError(
                "Internal still model is not installed",
                hint="Install the selected internal diffusers model in Models, then retry.",
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        model_path = installed

    out_path = Path(str(payload.get("out_path") or ""))
    if out_path and out_path.exists():
        metadata_path = _output_metadata_path(out_path)
        metadata = None
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = None
        return {
            "cached": True,
            "saved": str(out_path),
            "metadata_path": str(metadata_path),
            "metadata": metadata,
        }

    prepared_assets = _prepare_still_scene_assets(project_id, payload, workflow_family)
    actual_width = int(prepared_assets.get("width") or payload.get("width") or 1024)
    actual_height = int(prepared_assets.get("height") or payload.get("height") or 576)
    controlnet_units = _prepare_internal_controlnet_units(project_id, list(payload.get("controlnet_units") or []))
    resolved_refiner = dict(payload.get("refiner")) if isinstance(payload.get("refiner"), dict) else None
    if resolved_refiner is not None:
        resolved_refiner["base_path"] = str(model_path)
        refiner_model = str(resolved_refiner.get("model") or "").strip()
        if refiner_model:
            refiner_asset = models.resolve_internal_asset(refiner_model, folder="diffusers", allowed_kinds={"diffusers"})
            resolved_refiner["path"] = str(refiner_asset.get("path") or "")
            resolved_refiner["family"] = refiner_asset.get("family")
    settings_obj = InternalVideoSettings(
        width=actual_width,
        height=actual_height,
        steps=int(payload.get("steps") or 28),
        cfg=float(payload.get("cfg") or 7.0),
        sampler=str(payload.get("sampler") or "euler"),
        seed=(int(payload["seed"]) if payload.get("seed") is not None else None),
        negative_prompt=str(payload.get("negative_prompt") or ""),
        model_id=model_id or str(payload.get("family") or "internal_still"),
        loras=tuple(_normalize_render_loras(payload.get("loras"))),
        vae=str(payload.get("vae") or "").strip() or None,
        hires_fix=dict(payload.get("hires_fix")) if isinstance(payload.get("hires_fix"), dict) else None,
        refiner=resolved_refiner,
        upscaler=str(payload.get("upscaler") or "").strip() or None,
        device_preference="auto",
    )

    jobs.update_progress(project_id, job_id, stage="rendering", current=0, total=1, message=f"Running internal {workflow_family} render")
    if settings_obj.loras:
        lora_log = ", ".join(
            f"{str(item.get('filename') or item.get('name') or 'lora')}@{float(item.get('weight', 1.0)):.2f}"
            for item in settings_obj.loras
        )
        jobs.append_log(project_id, job_id, f"LoRAs: {lora_log}")
    if settings_obj.hires_fix and settings_obj.hires_fix.get("enabled", True):
        jobs.append_log(
            project_id,
            job_id,
            f"Hires fix: scale {float(settings_obj.hires_fix.get('scale', 1.5)):.2f} • denoise {float(settings_obj.hires_fix.get('denoise', 0.35)):.2f}",
        )
    if resolved_refiner:
        jobs.append_log(
            project_id,
            job_id,
            f"Refiner pass: {str(resolved_refiner.get('model') or 'base-model')} • switch {float(resolved_refiner.get('switch_at', 0.8)):.2f}",
        )

    result = render_internal_still_image(
        model_dir=model_path,
        settings=settings_obj,
        workflow_family=workflow_family,
        prompt=prompt,
        source_image_path=prepared_assets.get("source_path"),
        mask_image_path=prepared_assets.get("mask_path"),
        controlnet_units=controlnet_units,
        denoise_strength=float(payload.get("denoise_strength") or 0.75),
        log_fn=lambda message: jobs.append_log(project_id, job_id, str(message)),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    result["image"].save(out_path)
    final_width, final_height = result["image"].size

    metadata = _build_generation_metadata(
        project_id=project_id,
        job_id=job_id,
        output_path=out_path,
        payload={
            **payload,
            "width": final_width,
            "height": final_height,
            "steps": int(result.get("effective_steps") or settings_obj.steps),
            "cfg": float(result.get("effective_cfg") if result.get("effective_cfg") is not None else settings_obj.cfg),
            "refiner": resolved_refiner,
        },
        workflow_family=workflow_family,
        checkpoint=str(model_path.name),
        loras=list(settings_obj.loras),
        controlnet_units=controlnet_units,
        vae_name=settings_obj.vae,
        backend=str(result.get("backend") or "internal_diffusers"),
        engine="internal",
        model_family=str(payload.get("family") or ""),
        resolved_model_asset=str(model_path),
        mask_source=str(prepared_assets.get("mask_source") or ""),
        outpaint=prepared_assets.get("outpaint"),
        device=str(result.get("device") or "cpu"),
    )
    metadata_path = _write_generation_metadata(out_path, metadata)
    jobs.update_progress(project_id, job_id, stage="complete", current=1, total=1, message=f"Saved {out_path.name}")
    return {
        "saved": str(out_path),
        "metadata_path": str(metadata_path),
        "metadata": metadata,
        "device": result.get("device"),
        "requested_device": result.get("requested_device"),
    }

def _run_comfyui_scene(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    prompt = payload["prompt"]
    negative_prompt = payload["negative_prompt"]
    seed = int(payload["seed"])
    width = int(payload["width"])
    height = int(payload["height"])
    steps = int(payload["steps"])
    cfg = float(payload["cfg"])
    sampler = str(payload["sampler"])
    scene_index = int(payload["scene_index"])
    variant_index = int(payload["variant_index"])

    out_path = Path(payload.get("out_path") or "")
    if out_path and out_path.exists():
        metadata_path = _output_metadata_path(out_path)
        metadata = None
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = None
        return {
            "cached": True,
            "saved": str(out_path),
            "metadata_path": str(metadata_path) if metadata_path else None,
            "metadata": metadata,
        }

    raw_controlnet_units = payload.get("controlnet_units") if isinstance(payload.get("controlnet_units"), list) else []
    selection = _resolve_comfy_still_selection(
        model_id=str(payload.get("model_id") or "") or None,
        checkpoint=str(payload.get("checkpoint") or "") or None,
        workflow_family=str(payload.get("workflow_family") or "auto") or None,
        controlnet_model=str(payload.get("controlnet_model") or "") or None,
        reference_asset=str(payload.get("reference_asset") or "") or None,
        conditioning_mode=str(payload.get("conditioning_mode") or "raw") or None,
        controlnet_units=raw_controlnet_units,
    )
    checkpoint = selection["checkpoint"]
    workflow_family = str(selection.get("workflow_family") or "txt2img")
    controlnet_name = str(payload.get("controlnet_name") or selection.get("controlnet_name") or "").strip()
    conditioning_mode = str(selection.get("conditioning_mode") or "raw")
    resolved_loras = _normalize_render_loras(payload.get("loras"))
    vae_name = _resolve_optional_comfy_asset_name(payload.get("vae"), folder="vae", allowed_kinds={"vae"})
    hires_fix = dict(payload.get("hires_fix")) if isinstance(payload.get("hires_fix"), dict) else None
    upscaler = str(payload.get("upscaler") or "").strip() or None
    resolved_refiner = dict(payload.get("refiner")) if isinstance(payload.get("refiner"), dict) else None
    if resolved_refiner is not None:
        refiner_model = str(resolved_refiner.get("model") or "").strip()
        if refiner_model:
            resolved_refiner["checkpoint"] = _resolve_optional_comfy_asset_name(
                refiner_model,
                folder="checkpoints",
                allowed_kinds={"checkpoint"},
            )
    metadata_controlnet_units: list[dict[str, Any]] = []
    prepared_assets = _prepare_still_scene_assets(project_id, payload, workflow_family)
    actual_width = int(prepared_assets.get("width") or width)
    actual_height = int(prepared_assets.get("height") or height)

    req = {"checkpoint": checkpoint, "est_steps": steps, "est_frames": 1}
    if workflow_family == "controlnet":
        req["node_classes"] = ["LoadImage", "ControlNetLoader", "ControlNetApplyAdvanced"]
    elif workflow_family == "img2img":
        req["node_classes"] = ["LoadImage", "VAEEncode"]
    elif workflow_family in {"inpaint", "outpaint"}:
        req["node_classes"] = ["LoadImage", "LoadImageMask", "VAEEncodeForInpaint"]
    try:
        node_url = comfy_pool.acquire(req)
    except Exception as e:
        raise UserFacingError(
            message="No available ComfyUI node could run this job.",
            hint=hint_from_exception(e) or "Check ComfyUI is running and not saturated, then retry.",
            code="COMFYUI_NO_NODE",
            status_code=502,
        )
    jobs.append_log(project_id, job_id, f"Using ComfyUI node: {node_url}".rstrip())
    try:
        if workflow_family == "controlnet":
            controlnet_units = _normalize_controlnet_units(raw_controlnet_units)
            if not controlnet_units and controlnet_name and payload.get("reference_asset"):
                controlnet_units = _normalize_controlnet_units(
                    [
                        {
                            "model": str(payload.get("controlnet_model") or controlnet_name),
                            "reference_asset": str(payload.get("reference_asset") or ""),
                            "conditioning_mode": conditioning_mode,
                            "strength": float(payload.get("controlnet_strength") or 0.8),
                        }
                    ]
                )
            prepared_units = []
            for unit in controlnet_units:
                prepared_units.append(
                    {
                        **unit,
                        "reference_image": _prepare_comfy_reference_image(
                            project_id,
                            node_url,
                            str(unit.get("reference_asset") or ""),
                            str(unit.get("conditioning_mode") or "raw"),
                        ),
                    }
                )
            metadata_controlnet_units = [dict(unit) for unit in prepared_units]
            wf = comfy.controlnet_workflow(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=actual_width,
                height=actual_height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                controlnet_name=controlnet_name,
                reference_image=str(prepared_units[0].get("reference_image") or "reference.png") if prepared_units else "reference.png",
                controlnet_strength=float(payload.get("controlnet_strength") or 0.8),
                filename_prefix=f"edmg_cn_v{variant_index:02d}_scene{scene_index:03d}_{job_id[:6]}",
                loras=resolved_loras,
                vae_name=vae_name,
                controlnet_units=prepared_units,
                hires_fix=hires_fix,
                refiner=resolved_refiner,
                upscaler=upscaler,
            )
            jobs.append_log(
                project_id,
                job_id,
                f"ControlNet still render using {checkpoint} with {len(prepared_units) or 1} unit(s)",
            )
        elif workflow_family == "img2img":
            source_path = prepared_assets.get("source_path")
            if not isinstance(source_path, Path):
                raise UserFacingError(
                    "No source image selected for img2img",
                    hint="Upload or choose a project source image before running img2img.",
                    code="IMG2IMG_SOURCE_MISSING",
                    status_code=400,
                )
            source_image = _prepare_comfy_reference_image(project_id, node_url, str(source_path), "raw")
            wf = comfy.img2img_workflow(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=actual_width,
                height=actual_height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                source_image=source_image,
                denoise_strength=float(payload.get("denoise_strength") or 0.75),
                filename_prefix=f"edmg_img2img_v{variant_index:02d}_scene{scene_index:03d}_{job_id[:6]}",
                loras=resolved_loras,
                vae_name=vae_name,
                hires_fix=hires_fix,
                refiner=resolved_refiner,
                upscaler=upscaler,
            )
        elif workflow_family in {"inpaint", "outpaint"}:
            source_path = prepared_assets.get("source_path")
            mask_path = prepared_assets.get("mask_path")
            if not isinstance(source_path, Path) or not isinstance(mask_path, Path):
                raise UserFacingError(
                    "Source image or mask is missing",
                    hint="Choose both a source image and a mask before running an inpaint or outpaint render.",
                    code="INPAINT_ASSETS_MISSING",
                    status_code=400,
                )
            source_image = _prepare_comfy_reference_image(project_id, node_url, str(source_path), "raw")
            mask_image = _prepare_comfy_reference_image(project_id, node_url, str(mask_path), "raw")
            builder = comfy.outpaint_workflow if workflow_family == "outpaint" else comfy.inpaint_workflow
            wf = builder(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=actual_width,
                height=actual_height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                source_image=source_image,
                mask_image=mask_image,
                denoise_strength=float(payload.get("denoise_strength") or 0.8),
                filename_prefix=f"edmg_{workflow_family}_v{variant_index:02d}_scene{scene_index:03d}_{job_id[:6]}",
                loras=resolved_loras,
                vae_name=vae_name,
                hires_fix=hires_fix,
                refiner=resolved_refiner,
                upscaler=upscaler,
            )
        else:
            wf = comfy.default_workflow(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                filename_prefix=f"edmg_still_v{variant_index:02d}_scene{scene_index:03d}_{job_id[:6]}",
                loras=resolved_loras,
                vae_name=vae_name,
                hires_fix=hires_fix,
                refiner=resolved_refiner,
                upscaler=upscaler,
            )
        if resolved_loras:
            lora_log = ", ".join(
                f"{str(item.get('filename') or item.get('name') or 'lora')}@{float(item.get('weight', 1.0)):.2f}"
                for item in resolved_loras
            )
            jobs.append_log(
                project_id,
                job_id,
                f"LoRAs: {lora_log}",
            )
        if vae_name:
            jobs.append_log(project_id, job_id, f"VAE override: {vae_name}")
        if hires_fix and hires_fix.get("enabled", True):
            jobs.append_log(
                project_id,
                job_id,
                f"Hires fix: scale {float(hires_fix.get('scale', 1.5)):.2f} • denoise {float(hires_fix.get('denoise', 0.35)):.2f}",
            )
        if resolved_refiner:
            jobs.append_log(
                project_id,
                job_id,
                f"Refiner pass: {str(resolved_refiner.get('model') or resolved_refiner.get('checkpoint') or 'base-checkpoint')} • switch {float(resolved_refiner.get('switch_at', 0.8)):.2f}",
            )

        submit = comfy.submit_prompt(node_url, wf)
        prompt_id = submit.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI submit missing prompt_id: {submit}")

        for _ in range(180):  # up to ~3 min
            hist = comfy.get_history(node_url, prompt_id)
            ims = comfy.extract_output_images(hist)
            err = comfy.extract_execution_error(hist)
            if err:
                raise UserFacingError(
                    message=f"ComfyUI scene render failed: {err}",
                    hint=hint_from_exception(Exception(err)) or "Check ComfyUI History/console, fix the model or nodes, then retry.",
                    code="COMFYUI_EXECUTION_ERROR",
                    status_code=502,
                )
            if ims:
                im = ims[0]
                img_bytes = comfy.download_image_bytes(
                    node_url,
                    filename=im["filename"],
                    subfolder=im.get("subfolder",""),
                    folder_type=im.get("type","output")
                )
                if not out_path:
                    out_dir = store.project_dir(project_id) / "outputs" / "images"
                    out_dir.mkdir(parents=True, exist_ok=True)
                    ext = Path(im["filename"]).suffix or ".png"
                    out_name = f"v{variant_index:02d}_scene{scene_index:03d}_seed{seed}{ext}"
                    out_path = out_dir / out_name
                out_path.parent.mkdir(parents=True, exist_ok=True)
                out_path.write_bytes(img_bytes)
                final_width = actual_width
                final_height = actual_height
                if Image is not None:
                    try:
                        with Image.open(out_path) as generated_image:
                            final_width, final_height = generated_image.size
                    except Exception:
                        final_width = actual_width
                        final_height = actual_height
                metadata = _build_generation_metadata(
                    project_id=project_id,
                    job_id=job_id,
                    output_path=out_path,
                    payload={**payload, "width": final_width, "height": final_height, "refiner": resolved_refiner},
                    workflow_family=workflow_family,
                    checkpoint=checkpoint,
                    loras=resolved_loras,
                    controlnet_units=metadata_controlnet_units,
                    vae_name=vae_name,
                    prompt_id=str(prompt_id),
                    comfyui_image=im,
                    node_url=node_url,
                    backend="comfyui",
                    engine="comfyui",
                    model_family=payload.get("family"),
                    resolved_model_asset=checkpoint,
                    mask_source=prepared_assets.get("mask_source"),
                    outpaint=prepared_assets.get("outpaint"),
                )
                metadata_path = _write_generation_metadata(out_path, metadata)
                return {
                    "prompt_id": prompt_id,
                    "saved": str(out_path),
                    "metadata_path": str(metadata_path),
                    "metadata": metadata,
                    "comfyui_image": im,
                }

            time.sleep(1.0)

        raise UserFacingError(
            message="Timed out waiting for ComfyUI output.",
            hint="ComfyUI may be busy or stuck. Check ComfyUI console, then retry the job.",
            code="COMFYUI_TIMEOUT",
            status_code=504,
        )
    finally:
        comfy_pool.release(node_url)

def _run_comfyui_motion_scene(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Render a short motion clip via ComfyUI and assemble an MP4.

    This intentionally keeps the *runtime UX* simple:
      - jobs always write frames into frames_dir
      - then FFmpeg assembles out_clip
      - if motion capabilities aren't available, it can fall back to a still-based clip
    """

    prompt = payload["prompt"]
    negative_prompt = payload["negative_prompt"]
    seed = int(payload["seed"])
    width = int(payload["width"])
    height = int(payload["height"])
    steps = int(payload["steps"])
    cfg = float(payload["cfg"])
    sampler = str(payload["sampler"])
    scene_index = int(payload["scene_index"])
    variant_index = int(payload["variant_index"])

    engine = str(payload.get("engine") or "animatediff")
    frames = int(payload.get("frames", 24))
    fps = int(payload.get("fps", 12))
    motion_model_name = str(payload.get("motion_model_name") or "mm_sd_v15_v2.ckpt")
    required_tags = payload.get("required_tags") or []
    resolved_loras = _normalize_render_loras(payload.get("loras"))
    vae_name = _resolve_optional_comfy_asset_name(payload.get("vae"), folder="vae", allowed_kinds={"vae"})

    frames_dir = Path(payload.get("frames_dir") or "")
    out_clip = Path(payload.get("out_clip") or "")
    if out_clip and out_clip.exists():
        metadata_path = _output_metadata_path(out_clip)
        metadata = None
        if metadata_path.exists():
            try:
                metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            except Exception:
                metadata = None
        return {"cached": True, "saved": str(out_clip), "metadata_path": str(metadata_path), "metadata": metadata}
    if frames_dir and frames_dir.exists() and out_clip:
        # If frames already exist (resume), try assembling.
        try:
            assemble_image_sequence(settings.ffmpeg_path, frames_dir, out_clip, fps=fps)
            return {"cached": True, "saved": str(out_clip)}
        except Exception:
            pass

    checkpoint = payload.get("checkpoint") or settings.comfyui_checkpoint
    filename_prefix = f"edmg_v{variant_index:02d}_scene{scene_index:03d}_{engine}_seed{seed}_{job_id[:6]}"

    # Build workflow and routing requirements.
    if engine == "svd" and hasattr(comfy, "svd_workflow"):
        wf = comfy.svd_workflow(
            checkpoint=checkpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            svd_checkpoint=str(payload.get("svd_checkpoint") or "svd_xt.safetensors"),
            svd_num_frames=frames,
            svd_num_steps=int(payload.get("svd_num_steps") or 25),
            svd_motion_bucket_id=int(payload.get("svd_motion_bucket_id") or 127),
            svd_fps_id=int(payload.get("svd_fps_id") or 6),
            svd_cond_aug=float(payload.get("svd_cond_aug") or 0.02),
            svd_decoding_t=int(payload.get("svd_decoding_t") or 14),
            device=str(payload.get("device") or "cuda"),
            filename_prefix=filename_prefix,
            loras=resolved_loras,
            vae_name=vae_name,
        )
        req = {
            "checkpoint": checkpoint,
            "est_steps": steps,
            "est_frames": frames,
            "node_classes": ["SVDSimpleImg2Vid"],
            "tags": required_tags,
        }
        expected_frames = frames
    elif engine == "animatediff" and hasattr(comfy, "animatediff_workflow"):
        wf = comfy.animatediff_workflow(
            checkpoint=checkpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            frames=frames,
            motion_model_name=motion_model_name,
            context_length=int(payload.get("context_length") or 16),
            context_overlap=int(payload.get("context_overlap") or 4),
            beta_schedule=str(payload.get("beta_schedule") or "autoselect"),
            filename_prefix=filename_prefix,
            loras=resolved_loras,
            vae_name=vae_name,
        )
        req = {
            "checkpoint": checkpoint,
            "est_steps": steps,
            "est_frames": frames,
            "node_classes": ["ADE_StandardStaticContextOptions", "ADE_AnimateDiffLoaderGen1"],
            "tags": required_tags,
        }
        expected_frames = frames
    else:
        # Fallback: still workflow (produces 1 image, then we assemble a 1-frame clip)
        wf = comfy.default_workflow(
            checkpoint=checkpoint,
            prompt=prompt,
            negative_prompt=negative_prompt,
            seed=seed,
            width=width,
            height=height,
            steps=steps,
            cfg=cfg,
            sampler=sampler,
            filename_prefix=filename_prefix,
            loras=resolved_loras,
            vae_name=vae_name,
        )
        req = {"checkpoint": checkpoint, "est_steps": steps, "est_frames": 1, "tags": required_tags}
        expected_frames = 1

    try:
        node_url = comfy_pool.acquire(req)
    except Exception as e:
        # If motion can't run, fall back to stills and produce a slideshow-like clip.
        if req.get("node_classes"):
            jobs.append_log(project_id, job_id, f"No compatible motion node for {engine}; falling back to stills.")
            wf = comfy.default_workflow(
                checkpoint=checkpoint,
                prompt=prompt,
                negative_prompt=negative_prompt,
                seed=seed,
                width=width,
                height=height,
                steps=steps,
                cfg=cfg,
                sampler=sampler,
                filename_prefix=filename_prefix,
                loras=resolved_loras,
                vae_name=vae_name,
            )
            req = {"checkpoint": checkpoint, "est_steps": steps, "est_frames": 1, "tags": required_tags}
            expected_frames = 1
            try:
                node_url = comfy_pool.acquire(req)
            except Exception as e2:
                raise UserFacingError(
                    message="No available ComfyUI node could run this job.",
                    hint=hint_from_exception(e2) or "Start ComfyUI and retry.",
                    code="COMFYUI_NO_NODE",
                    status_code=502,
                )
        else:
            raise UserFacingError(
                message="No available ComfyUI node could run this job.",
                hint=hint_from_exception(e) or "Start ComfyUI and retry.",
                code="COMFYUI_NO_NODE",
                status_code=502,
            )

    jobs.append_log(project_id, job_id, f"Using ComfyUI node: {node_url}".rstrip())
    if resolved_loras:
        lora_log = ", ".join(
            f"{str(item.get('filename') or item.get('name') or 'lora')}@{float(item.get('weight', 1.0)):.2f}"
            for item in resolved_loras
        )
        jobs.append_log(project_id, job_id, f"LoRAs: {lora_log}")
    if vae_name:
        jobs.append_log(project_id, job_id, f"VAE override: {vae_name}")
    try:
        submit = comfy.submit_prompt(node_url, wf)
        prompt_id = submit.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI submit missing prompt_id: {submit}")

        frames_dir.mkdir(parents=True, exist_ok=True)

        for _ in range(420):  # up to ~7 min
            hist = comfy.get_history(node_url, prompt_id)
            ims_all = comfy.extract_output_images(hist)
            err = comfy.extract_execution_error(hist)
            if err:
                raise UserFacingError(
                    message=f"ComfyUI motion render failed: {err}",
                    hint=hint_from_exception(Exception(err)) or "Check ComfyUI History/console, fix the model or nodes, then retry.",
                    code="COMFYUI_EXECUTION_ERROR",
                    status_code=502,
                )
            ims = [im for im in ims_all if filename_prefix in str(im.get("filename", ""))] or ims_all

            if ims and len(ims) >= expected_frames:
                # Download all frames we have (cap at expected_frames)
                ims = ims[:expected_frames]
                for i, im in enumerate(ims, start=1):
                    ext = Path(im.get("filename", "")).suffix or ".png"
                    frame_path = frames_dir / f"frame_{i:06d}{ext}"
                    if frame_path.exists():
                        continue
                    img_bytes = comfy.download_image_bytes(
                        node_url,
                        filename=im["filename"],
                        subfolder=im.get("subfolder", ""),
                        folder_type=im.get("type", "output"),
                    )
                    frame_path.write_bytes(img_bytes)

                # Assemble clip
                if out_clip:
                    assemble_image_sequence(settings.ffmpeg_path, frames_dir, out_clip, fps=fps)
                    metadata = _build_generation_metadata(
                        project_id=project_id,
                        job_id=job_id,
                        output_path=out_clip,
                        payload=payload,
                        workflow_family=f"motion_{engine}",
                        checkpoint=str(checkpoint),
                        loras=resolved_loras,
                        controlnet_units=[],
                        vae_name=vae_name,
                        prompt_id=str(prompt_id),
                        comfyui_image=ims[0] if ims else None,
                        node_url=node_url,
                        artifact_key="video",
                    )
                    metadata["frames_dir"] = _project_relative_path(project_id, frames_dir)
                    metadata_path = _write_generation_metadata(out_clip, metadata)
                    return {
                        "prompt_id": prompt_id,
                        "saved": str(out_clip),
                        "frames_dir": str(frames_dir),
                        "metadata_path": str(metadata_path),
                        "metadata": metadata,
                    }
                # Fallback: no clip target provided
                return {"prompt_id": prompt_id, "frames_dir": str(frames_dir)}

            time.sleep(1.0)

        raise UserFacingError(
            message="Timed out waiting for ComfyUI frames.",
            hint="ComfyUI may be busy or stuck. Check ComfyUI console, then retry the job.",
            code="COMFYUI_TIMEOUT",
            status_code=504,
        )
    finally:
        comfy_pool.release(node_url)

def _run_tensorrt_standalone(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    from .services import tensorrt_standalone as trt_service

    execution_payload = _resolved_tensorrt_execution_payload(payload)
    return trt_service.run_job(project_id, job_id, execution_payload)

def _run_internal_video(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    preflight = _internal_render_preflight_data(project_id, payload)
    if preflight.get("mode") == "hosted":
        provider_cfg = dict((render_settings.get().get("stability") or {}))
        proj = store.get(project_id)
        if not proj:
            raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
        plan = proj.meta.get("last_plan")
        if not plan or not (plan.get("variants") or []):
            raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")

        variant_index = int(payload.get("variant_index", 0))
        variants = plan["variants"]
        if variant_index < 0 or variant_index >= len(variants):
            raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")

        variant = variants[variant_index]
        scenes = variant.get("scenes") or []
        pdir = store.project_dir(project_id)
        audio_meta = proj.meta.get("audio")
        audio_path: Path | None = None
        if audio_meta and audio_meta.get("filename"):
            audio_path = pdir / "assets" / "audio" / str(audio_meta["filename"])
            if not audio_path.exists():
                audio_path = None

        hosted_payload = dict(payload)
        hosted_payload.setdefault("cfg", provider_cfg.get("cfg_scale", 6.5))
        hosted_payload.setdefault("temporal_strength", provider_cfg.get("strength", 0.55))
        settings_obj = _internal_settings_from_payload(
            hosted_payload,
            model_id=str(preflight.get("model_id") or "stability:sd3:sd3.5-large-turbo"),
            render_tier=str(payload.get("render_tier") or "auto"),
            device_preference="cpu",
            temporal_mode="keyframes" if str(payload.get("temporal_mode") or "frame_img2img") == "frame_img2img" else str(payload.get("temporal_mode") or "keyframes"),
        )

        runtime_checkpoint: dict[str, Any] | None = None
        chunk_plan = dict(((preflight.get("tier_plan") or {}).get("chunk_plan") or {}))
        estimated_total = max(1, int(preflight.get("estimated_frames", 1)) + 3)

        def _checkpoint(state: dict[str, Any]) -> None:
            nonlocal runtime_checkpoint
            runtime_checkpoint = dict(state or {})
            latest = jobs.get(project_id, job_id)
            latest_progress = latest.progress if latest and isinstance(latest.progress, dict) else {}
            jobs.update_progress(
                project_id,
                job_id,
                stage=str(latest_progress.get("stage") or runtime_checkpoint.get("stage") or "running"),
                current=int(latest_progress.get("current", 0) or 0),
                total=max(1, int(latest_progress.get("total", estimated_total) or estimated_total)),
                message=str(latest_progress.get("message") or runtime_checkpoint.get("message") or ""),
                extra=_job_checkpoint_extra("hosted", settings_obj.model_id, runtime_checkpoint),
            )

        def _check_canceled() -> None:
            latest = jobs.get(project_id, job_id)
            if latest and latest.status == "canceled":
                jobs.update_progress(
                    project_id,
                    job_id,
                    stage="canceled",
                    current=int((latest.progress or {}).get("current", 0)),
                    total=max(1, int((latest.progress or {}).get("total", estimated_total) or estimated_total)),
                    message="Cancel requested — stopping after current step",
                    extra=_job_checkpoint_extra("hosted", settings_obj.model_id, runtime_checkpoint),
                )
                raise JobCanceled("Hosted render canceled")

        def _log(line: str) -> None:
            _check_canceled()
            jobs.append_log(project_id, job_id, line)

        def _progress(stage: str, current: int, total: int, message: str | None = None) -> None:
            _check_canceled()
            jobs.update_progress(
                project_id,
                job_id,
                stage=stage,
                current=current,
                total=total,
                message=message,
                extra=_job_checkpoint_extra("hosted", settings_obj.model_id, runtime_checkpoint),
            )

        _log(
            f"Hosted render: fps_render={settings_obj.fps_render} fps_output={settings_obj.fps_output} "
            f"keyframe_interval_s={settings_obj.keyframe_interval_s} service={preflight.get('hosted_provider', {}).get('service')}"
        )
        if preflight.get("warnings"):
            for warning in preflight["warnings"]:
                _log(f"Warning: {warning}")

        _progress("starting", 0, estimated_total, "Starting hosted Stability render")
        variant2 = dict(variant)
        variant2["index"] = variant_index
        variant2["duration_s"] = _resolved_project_duration_s(proj, variant, scenes)

        out = render_stability_hosted_video_variant(
            ffmpeg_path=settings.ffmpeg_path,
            project_dir=pdir,
            variant=variant2,
            scenes=scenes,
            audio_path=audio_path,
            settings=settings_obj,
            stability_api_key=str(secrets.get("stability_api_key") or ""),
            hosted_settings={
                "service": str(preflight.get("hosted_provider", {}).get("service") or provider_cfg.get("service") or "sd3"),
                "model": str(preflight.get("hosted_provider", {}).get("model") or provider_cfg.get("model") or "sd3.5-large-turbo"),
                "style_preset": str(preflight.get("hosted_provider", {}).get("style_preset") or provider_cfg.get("style_preset") or "none"),
                "output_format": str(preflight.get("hosted_provider", {}).get("output_format") or provider_cfg.get("output_format") or "png"),
                "strength": float(provider_cfg.get("strength", 0.55)),
                "cfg_scale": float(provider_cfg.get("cfg_scale", 6.5)),
            },
            timeline=(proj.meta.get("timeline") or None),
            log_fn=_log,
            progress_fn=_progress,
            cancel_check_fn=_check_canceled,
            chunk_plan=chunk_plan,
            checkpoint_fn=_checkpoint,
        )
        checkpoint_summary = runtime_checkpoint or _load_render_checkpoint(out)

        jobs.update_progress(
            project_id,
            job_id,
            stage="complete",
            current=estimated_total,
            total=estimated_total,
            message=f"Saved {out.name}",
            extra=_job_checkpoint_extra("hosted", settings_obj.model_id, checkpoint_summary, video=str(out)),
        )

        rel_video = str(out.relative_to(pdir))
        videos = proj.meta.setdefault("outputs", {}).setdefault("videos", [])
        if rel_video not in videos:
            videos.append(rel_video)
        render_entry = {
            "video": rel_video,
            "model_id": settings_obj.model_id,
            "mode": "hosted",
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "temporal_mode": settings_obj.temporal_mode,
            "resume_existing_frames": settings_obj.resume_existing_frames,
            "variant_index": variant_index,
            "completed_at": time.time(),
            "preflight": _public_render_preflight(preflight),
            "runtime_checkpoint": checkpoint_summary,
            "hosted_provider": preflight.get("hosted_provider"),
        }
        proj.meta["last_internal_render"] = render_entry
        hist = proj.meta.setdefault("internal_render_history", [])
        hist.append(render_entry)
        if isinstance(hist, list) and len(hist) > 20:
            proj.meta["internal_render_history"] = hist[-20:]
        store.save(proj)
        return {
            "ok": True,
            "video": rel_video,
            "video_abs": str(out),
            "mode": "hosted",
            "preflight": _public_render_preflight(preflight),
            "runtime_checkpoint": checkpoint_summary,
        }

    if preflight.get("mode") == "tensorrt":
        proj = store.get(project_id)
        if not proj:
            raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
        variant_index = int(payload.get("variant_index", 0))
        variant, _used_fallback = _internal_render_variant_or_fallback(proj, variant_index)
        scenes = variant.get("scenes") or []
        pdir = store.project_dir(project_id)
        audio_meta = proj.meta.get("audio")
        audio_path: Path | None = None
        if audio_meta and audio_meta.get("filename"):
            audio_path = pdir / "assets" / "audio" / str(audio_meta["filename"])
            if not audio_path.exists():
                audio_path = None

        model_id = str(preflight.get("model_id") or _tensorrt_model_id_from_payload(payload))
        bundle_path_raw = str(preflight.get("model_path") or "").strip()
        if not bundle_path_raw:
            raise UserFacingError(
                "TensorRT preflight did not resolve an installed bundle path",
                hint="Open Models and verify the canonical TensorRT bundle, then retry.",
                code="TRT_MODEL_NOT_FOUND",
                status_code=400,
            )
        bundle_path = Path(bundle_path_raw).expanduser().resolve()
        trt_payload = dict(payload)
        trt_payload.update({"width": 512, "height": 512})
        settings_obj = _internal_settings_from_payload(
            trt_payload,
            model_id=model_id,
            render_tier=str(payload.get("render_tier") or "auto"),
            device_preference="cuda",
            temporal_mode="keyframes",
        )

        runtime_checkpoint: dict[str, Any] | None = None
        estimated_total = max(1, int(preflight.get("estimated_frames", 1)) + int(preflight.get("estimated_keyframes", 1)) + 3)

        def _check_canceled() -> None:
            latest = jobs.get(project_id, job_id)
            if latest and latest.status == "canceled":
                jobs.update_progress(
                    project_id,
                    job_id,
                    stage="canceled",
                    current=int((latest.progress or {}).get("current", 0)),
                    total=max(1, int((latest.progress or {}).get("total", estimated_total) or estimated_total)),
                    message="Cancel requested - stopping after current TensorRT step",
                    extra=_job_checkpoint_extra("tensorrt", model_id, runtime_checkpoint),
                )
                raise JobCanceled("TensorRT render canceled")

        def _log(line: str) -> None:
            _check_canceled()
            jobs.append_log(project_id, job_id, line)

        def _progress(stage: str, current: int, total: int, message: str | None = None) -> None:
            _check_canceled()
            jobs.update_progress(
                project_id,
                job_id,
                stage=stage,
                current=current,
                total=max(total, estimated_total),
                message=message,
                extra=_job_checkpoint_extra("tensorrt", model_id, runtime_checkpoint),
            )

        _log(
            f"TensorRT internal video: fps_render={settings_obj.fps_render} "
            f"fps_output={settings_obj.fps_output} keyframe_interval_s={settings_obj.keyframe_interval_s}"
        )
        _log(f"Using TensorRT model_id={model_id} path={preflight.get('model_path')}")
        if preflight.get("warnings"):
            for warning in preflight["warnings"]:
                _log(f"Warning: {warning}")

        _progress("starting", 0, estimated_total, "Starting TensorRT internal video render")
        variant2 = dict(variant)
        variant2["index"] = variant_index
        variant2["duration_s"] = _resolved_project_duration_s(proj, variant, scenes)

        out = render_tensorrt_video_variant(
            ffmpeg_path=settings.ffmpeg_path,
            project_id=project_id,
            project_dir=pdir,
            variant=variant2,
            scenes=scenes,
            audio_path=audio_path,
            settings=settings_obj,
            bundle_path=bundle_path,
            model_id=model_id,
            log_fn=_log,
            progress_fn=_progress,
            cancel_check_fn=_check_canceled,
        )

        jobs.update_progress(
            project_id,
            job_id,
            stage="complete",
            current=estimated_total,
            total=estimated_total,
            message=f"Saved {out.name}",
            extra=_job_checkpoint_extra("tensorrt", model_id, runtime_checkpoint, video=str(out)),
        )

        rel_video = str(out.relative_to(pdir))
        videos = proj.meta.setdefault("outputs", {}).setdefault("videos", [])
        if rel_video not in videos:
            videos.append(rel_video)
        render_entry = {
            "video": rel_video,
            "model_id": model_id,
            "mode": "tensorrt",
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "temporal_mode": "keyframes",
            "resume_existing_frames": False,
            "variant_index": variant_index,
            "completed_at": time.time(),
            "preflight": _public_render_preflight(preflight),
            "runtime_checkpoint": runtime_checkpoint,
        }
        proj.meta["last_internal_render"] = render_entry
        hist = proj.meta.setdefault("internal_render_history", [])
        hist.append(render_entry)
        if isinstance(hist, list) and len(hist) > 20:
            proj.meta["internal_render_history"] = hist[-20:]
        store.save(proj)
        return {
            "ok": True,
            "video": rel_video,
            "video_abs": str(out),
            "mode": "tensorrt",
            "preflight": _public_render_preflight(preflight),
            "runtime_checkpoint": runtime_checkpoint,
        }

    (
        proj,
        variant,
        model_id,
        model_path,
        tensorrt_keyframe_bundle_path,
        settings_obj,
    ) = _resolve_internal_render_request(project_id, payload)
    scenes = variant.get("scenes") or []
    pdir = store.project_dir(project_id)
    audio_meta = proj.meta.get("audio")
    audio_path: Path | None = None
    if audio_meta and audio_meta.get("filename"):
        audio_path = pdir / "assets" / "audio" / str(audio_meta["filename"])
        if not audio_path.exists():
            audio_path = None

    hw = _hardware_profile()
    runtime_checkpoint: dict[str, Any] | None = None
    chunk_plan = dict(((preflight.get("tier_plan") or {}).get("chunk_plan") or {}))
    estimated_total = max(1, int(preflight.get("estimated_frames", 1)) + 3)

    def _checkpoint(state: dict[str, Any]) -> None:
        nonlocal runtime_checkpoint
        runtime_checkpoint = dict(state or {})
        latest = jobs.get(project_id, job_id)
        latest_progress = latest.progress if latest and isinstance(latest.progress, dict) else {}
        jobs.update_progress(
            project_id,
            job_id,
            stage=str(latest_progress.get("stage") or runtime_checkpoint.get("stage") or "running"),
            current=int(latest_progress.get("current", 0) or 0),
            total=max(1, int(latest_progress.get("total", estimated_total) or estimated_total)),
            message=str(latest_progress.get("message") or runtime_checkpoint.get("message") or ""),
            extra=_job_checkpoint_extra("internal", model_id, runtime_checkpoint),
        )

    def _check_canceled() -> None:
        latest = jobs.get(project_id, job_id)
        if latest and latest.status == "canceled":
            jobs.update_progress(
                project_id,
                job_id,
                stage="canceled",
                current=int((latest.progress or {}).get("current", 0)),
                total=max(1, int((latest.progress or {}).get("total", estimated_total) or estimated_total)),
                message="Cancel requested — stopping after current step",
                extra=_job_checkpoint_extra("internal", model_id, runtime_checkpoint),
            )
            raise JobCanceled("Internal render canceled")

    def _log(line: str) -> None:
        _check_canceled()
        jobs.append_log(project_id, job_id, line)

    def _progress(stage: str, current: int, total: int, message: str | None = None) -> None:
        _check_canceled()
        jobs.update_progress(
            project_id,
            job_id,
            stage=stage,
            current=current,
            total=total,
            message=message,
            extra=_job_checkpoint_extra("internal", model_id, runtime_checkpoint),
        )

    _log(
        f"Internal render: fps_render={settings_obj.fps_render} fps_output={settings_obj.fps_output} "
        f"keyframe_interval_s={settings_obj.keyframe_interval_s} temporal_mode={settings_obj.temporal_mode} "
        f"keyframe_continuity_mode={normalize_keyframe_continuity_mode(settings_obj.keyframe_continuity_mode)}"
    )
    if settings_obj.temporal_mode == "video_model":
        _log(
            f"Internal video model: engine={settings_obj.video_model_engine} "
            f"model_id={settings_obj.video_model_id} path={settings_obj.video_model_path}"
        )
    _log(f"Hardware: backend={hw.get('backend')} vram_gb={hw.get('vram_gb')}")
    _log(f"Using model_id={model_id} path={model_path}")
    if preflight.get("warnings"):
        for warning in preflight["warnings"]:
            _log(f"Warning: {warning}")

    _progress("starting", 0, estimated_total, "Starting internal render")

    variant2 = dict(variant)
    variant2["index"] = int(payload.get("variant_index", 0))
    variant2["duration_s"] = _resolved_project_duration_s(proj, variant, scenes)

    source_image_path = _resolve_project_reference_path(project_id, getattr(settings_obj, "source_asset", None))
    out = render_internal_video_variant(
        ffmpeg_path=settings.ffmpeg_path,
        project_dir=pdir,
        project_id=project_id,
        variant=variant2,
        scenes=scenes,
        audio_path=audio_path,
        model_dir=model_path,
        settings=settings_obj,
        tensorrt_bundle_path=tensorrt_keyframe_bundle_path,
        timeline=(proj.meta.get("timeline") or None),
        log_fn=_log,
        progress_fn=_progress,
        cancel_check_fn=_check_canceled,
        chunk_plan=chunk_plan,
        checkpoint_fn=_checkpoint,
        source_image_path=source_image_path,
    )
    checkpoint_summary = runtime_checkpoint or _load_render_checkpoint(out)

    jobs.update_progress(
        project_id,
        job_id,
        stage="complete",
        current=estimated_total,
        total=estimated_total,
        message=f"Saved {out.name}",
        extra=_job_checkpoint_extra("internal", model_id, checkpoint_summary, video=str(out)),
    )

    rel_video = str(out.relative_to(pdir))
    videos = proj.meta.setdefault("outputs", {}).setdefault("videos", [])
    if rel_video not in videos:
        videos.append(rel_video)
    render_entry = {
        "video": rel_video,
        "model_id": model_id,
        "mode": "diffusion",
        "fps_render": settings_obj.fps_render,
        "fps_output": settings_obj.fps_output,
        "temporal_mode": settings_obj.temporal_mode,
        "keyframe_continuity_mode": normalize_keyframe_continuity_mode(
            settings_obj.keyframe_continuity_mode
        ),
        "video_model_engine": settings_obj.video_model_engine,
        "video_model_id": settings_obj.video_model_id,
        "video_model_scene_motion": normalize_video_model_scene_motion(settings_obj.video_model_scene_motion),
        "video_model_apply_timeline_camera": settings_obj.video_model_apply_timeline_camera,
        "video_model_keyframe_renderer": normalize_video_model_keyframe_renderer(settings_obj.video_model_keyframe_renderer),
        "video_model_keyframe_model_id": settings_obj.video_model_keyframe_model_id,
        "resume_existing_frames": settings_obj.resume_existing_frames,
        "variant_index": int(payload.get("variant_index", 0)),
        "completed_at": time.time(),
        "preflight": _public_render_preflight(preflight),
        "runtime_checkpoint": checkpoint_summary,
    }
    proj.meta["last_internal_render"] = render_entry
    hist = proj.meta.setdefault("internal_render_history", [])
    hist.append(render_entry)
    if isinstance(hist, list) and len(hist) > 20:
        proj.meta["internal_render_history"] = hist[-20:]
    store.save(proj)
    return {
        "ok": True,
        "video": rel_video,
        "video_abs": str(out),
        "mode": "diffusion",
        "preflight": _public_render_preflight(preflight),
        "runtime_checkpoint": checkpoint_summary,
    }


@app.post("/v1/projects/{project_id}/render/cosmos/scene")
def render_cosmos_scene(project_id: str, payload: dict[str, Any]):
    """Generate a single video clip for one scene using NVIDIA Cosmos.

    Uses your existing NVIDIA API key (same as Nemotron Ultra).
    Returns a base path-relative video path once the clip is saved.

    payload fields (all optional):
      scene_index   : int   (default 0)
      variant_index : int   (default 0)
      model         : str   "text2world" | "video2world" | "cosmos3"
      seed          : int
      steps         : int
      guidance_scale: float
      num_frames    : int
      fps           : float
      use_keyframe  : bool  if true and variant has a rendered keyframe,
                            passes it as the init image for video2world
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    provider_status = _render_provider_status()
    cosmos_status = provider_status.get("cosmos") or {}
    if not cosmos_status.get("configured"):
        raise UserFacingError(
            "Cosmos NIM is not configured.",
            hint=(
                "Cosmos video generation runs on a self-hosted NVIDIA NIM (there is no hosted Cosmos "
                "video endpoint). Start a Cosmos NIM on a CUDA GPU and set its URL in "
                "Settings → GPU / Render Runtime → Cosmos (Base URL), e.g. http://127.0.0.1:8000."
            ),
            code="COSMOS_NOT_CONFIGURED",
            status_code=400,
        )

    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    variant_index = int((payload or {}).get("variant_index") or 0)
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    scene_index = int((payload or {}).get("scene_index") or 0)
    if scene_index < 0 or scene_index >= len(scenes):
        raise HTTPException(400, f"scene_index {scene_index} out of range (0–{len(scenes)-1})")

    scene = scenes[scene_index]
    project_dir = store.project_dir(project_id)
    cosmos_cfg = dict((render_settings.get().get("cosmos") or {}))
    client = _cosmos_client()

    prompt = str(scene.get("prompt") or "cinematic music video").strip()
    negative = str(scene.get("negative_prompt") or "blurry, low quality, text, watermark, logo").strip()
    model = str((payload or {}).get("model") or cosmos_cfg.get("model") or "cosmos3")
    steps = int((payload or {}).get("steps") or cosmos_cfg.get("steps") or 50)
    guidance_scale = float((payload or {}).get("guidance_scale") or cosmos_cfg.get("guidance_scale") or 7.5)
    num_frames = int((payload or {}).get("num_frames") or cosmos_cfg.get("num_frames") or 121)
    fps = float((payload or {}).get("fps") or cosmos_cfg.get("fps") or 24.0)
    seed = (payload or {}).get("seed")
    prompt_upsampling = bool(cosmos_cfg.get("prompt_upsampling", True))

    out_dir = project_dir / "cosmos" / f"variant_{variant_index}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"scene_{scene_index:04d}.mp4"

    use_keyframe = bool((payload or {}).get("use_keyframe", False))
    init_image = None
    if use_keyframe or model in ("video2world",):
        kf_path = project_dir / "stills" / f"variant_{variant_index}" / f"scene_{scene_index:04d}.png"
        if kf_path.exists():
            try:
                from PIL import Image as PILImage
                init_image = PILImage.open(str(kf_path)).convert("RGB")
            except Exception:
                init_image = None

    hw = _hardware_profile()
    width = int(hw.get("preferred_width") or 1280)
    height = int(hw.get("preferred_height") or 704)

    if init_image is not None and model in ("video2world", "cosmos3"):
        result = client.image_to_video(
            image=init_image,
            out_path=out_path,
            prompt=prompt,
            negative_prompt=negative,
            width=width,
            height=height,
            fps=fps,
            num_frames=num_frames,
            steps=steps,
            guidance_scale=guidance_scale,
            seed=int(seed) if seed is not None else None,
            model=model,
        )
    else:
        result = client.text_to_video(
            prompt=prompt,
            out_path=out_path,
            negative_prompt=negative,
            width=width,
            height=height,
            fps=fps,
            num_frames=num_frames,
            steps=steps,
            guidance_scale=guidance_scale,
            seed=int(seed) if seed is not None else None,
            prompt_upsampling=prompt_upsampling,
            model=model,
        )

    rel = result.video_path.relative_to(project_dir).as_posix()
    return {
        "ok": True,
        "provider": "nvidia-cosmos",
        "video": rel,
        "video_abs": str(result.video_path),
        "scene_index": scene_index,
        "model": result.model,
        "duration_s": result.duration_s,
        "frames": result.frames,
        "fps": result.fps,
        "seed": result.seed,
    }


@app.post("/v1/projects/{project_id}/render/cosmos/all_scenes")
def render_cosmos_all_scenes(project_id: str, payload: dict[str, Any]):
    """Generate a Cosmos clip for every scene in a variant sequentially.

    Same as calling /render/cosmos/scene for each scene index in order.
    Returns a list of results. Failed scenes include an error key but do not
    stop processing of remaining scenes.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    variant_index = int((payload or {}).get("variant_index") or 0)
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    scenes = (variants[variant_index].get("scenes") or [])
    results = []
    for idx in range(len(scenes)):
        per_scene_payload = {**(payload or {}), "scene_index": idx, "variant_index": variant_index}
        try:
            r = render_cosmos_scene(project_id, per_scene_payload)
            results.append(r)
        except UserFacingError as e:
            results.append({"ok": False, "scene_index": idx, "error": e.message, "hint": e.hint})
        except Exception:
            logger.exception("Cosmos scene render failed for scene %s", idx)
            results.append({"ok": False, "scene_index": idx, "error": "Cosmos scene render failed"})

    return {"ok": True, "provider": "nvidia-cosmos", "results": results, "total": len(scenes)}


@app.post("/v1/projects/{project_id}/render/azure_foundry/scene")
def render_azure_foundry_scene(project_id: str, payload: dict[str, Any]):
    """Generate a single video clip for one scene using the hosted Azure AI Foundry
    Cosmos3-Super managed-compute deployment.

    Unlike /render/cosmos/scene (a self-hosted NIM), this calls a hosted Foundry
    ``GlobalManagedCompute`` deployment with an API key — no local GPU required.

    payload fields (all optional):
      scene_index   : int   (default 0)
      variant_index : int   (default 0)
      seed          : int
      steps         : int
      guidance_scale: float
      num_frames    : int
      fps           : float
      resolution    : str   e.g. "720_16_9" (see Settings → Azure Foundry Cosmos3)
      use_keyframe  : bool  if true and variant has a rendered keyframe,
                            passes it as the init image
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    provider_status = _render_provider_status()
    azure_foundry_status = provider_status.get("azure_foundry") or {}
    if not azure_foundry_status.get("configured"):
        raise UserFacingError(
            "Azure AI Foundry Cosmos3 is not configured.",
            hint=(
                "Set the Endpoint URL and Deployment name in Settings → GPU / Render Runtime → "
                "Azure Foundry Cosmos3, then add an API key in Settings → Secrets."
            ),
            code="AZURE_FOUNDRY_NOT_CONFIGURED",
            status_code=400,
        )
    if not azure_foundry_status.get("has_api_key"):
        raise UserFacingError(
            "Azure Foundry API key is not set.",
            hint="Add the Azure Foundry API key in Settings → Secrets, then retry.",
            code="AZURE_FOUNDRY_NO_API_KEY",
            status_code=400,
        )

    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    variant_index = int((payload or {}).get("variant_index") or 0)
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    scene_index = int((payload or {}).get("scene_index") or 0)
    if scene_index < 0 or scene_index >= len(scenes):
        raise HTTPException(400, f"scene_index {scene_index} out of range (0–{len(scenes)-1})")

    scene = scenes[scene_index]
    project_dir = store.project_dir(project_id)
    azure_foundry_cfg = dict((render_settings.get().get("azure_foundry") or {}))
    client = _azure_foundry_client()

    prompt = str(scene.get("prompt") or "cinematic music video").strip()
    negative = str(scene.get("negative_prompt") or "blurry, low quality, text, watermark, logo").strip()
    steps = int((payload or {}).get("steps") or azure_foundry_cfg.get("steps") or 50)
    guidance_scale = float((payload or {}).get("guidance_scale") or azure_foundry_cfg.get("guidance_scale") or 7.0)
    num_frames = int((payload or {}).get("num_frames") or azure_foundry_cfg.get("num_frames") or 121)
    fps = float((payload or {}).get("fps") or azure_foundry_cfg.get("fps") or 24.0)
    seed = (payload or {}).get("seed")
    resolution = str((payload or {}).get("resolution") or azure_foundry_cfg.get("resolution") or "720_16_9")
    width, height = _COSMOS3_SHAPES.get(resolution, (1280, 720))

    out_dir = project_dir / "azure_foundry" / f"variant_{variant_index}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"scene_{scene_index:04d}.mp4"

    use_keyframe = bool((payload or {}).get("use_keyframe", False))
    init_image = None
    if use_keyframe:
        kf_path = project_dir / "stills" / f"variant_{variant_index}" / f"scene_{scene_index:04d}.png"
        if kf_path.exists():
            try:
                from PIL import Image as PILImage
                init_image = PILImage.open(str(kf_path)).convert("RGB")
            except Exception:
                init_image = None

    if init_image is not None:
        result = client.image_to_video(
            image=init_image,
            out_path=out_path,
            prompt=prompt,
            negative_prompt=negative,
            width=width,
            height=height,
            fps=fps,
            num_frames=num_frames,
            steps=steps,
            guidance_scale=guidance_scale,
            seed=int(seed) if seed is not None else None,
        )
    else:
        result = client.text_to_video(
            prompt=prompt,
            out_path=out_path,
            negative_prompt=negative,
            width=width,
            height=height,
            fps=fps,
            num_frames=num_frames,
            steps=steps,
            guidance_scale=guidance_scale,
            seed=int(seed) if seed is not None else None,
        )

    rel = result.video_path.relative_to(project_dir).as_posix()
    return {
        "ok": True,
        "provider": "azure-foundry-cosmos",
        "video": rel,
        "video_abs": str(result.video_path),
        "scene_index": scene_index,
        "model": result.model,
        "duration_s": result.duration_s,
        "frames": result.frames,
        "fps": result.fps,
        "seed": result.seed,
    }


@app.post("/v1/projects/{project_id}/render/azure_foundry/all_scenes")
def render_azure_foundry_all_scenes(project_id: str, payload: dict[str, Any]):
    """Generate an Azure Foundry Cosmos3 clip for every scene in a variant sequentially.

    Same as calling /render/azure_foundry/scene for each scene index in order.
    Returns a list of results. Failed scenes include an error key but do not
    stop processing of remaining scenes.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    variant_index = int((payload or {}).get("variant_index") or 0)
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    scenes = (variants[variant_index].get("scenes") or [])
    results = []
    for idx in range(len(scenes)):
        per_scene_payload = {**(payload or {}), "scene_index": idx, "variant_index": variant_index}
        try:
            r = render_azure_foundry_scene(project_id, per_scene_payload)
            results.append(r)
        except UserFacingError as e:
            results.append({"ok": False, "scene_index": idx, "error": e.message, "hint": e.hint})
        except Exception:
            logger.exception("Azure Foundry scene render failed for scene %s", idx)
            results.append({"ok": False, "scene_index": idx, "error": "Azure Foundry scene render failed"})

    return {"ok": True, "provider": "azure-foundry-cosmos", "results": results, "total": len(scenes)}


@app.post("/v1/projects/{project_id}/render/firefly/scenes")
def render_firefly_scenes(project_id: str, req: RenderScenesRequest):
    """Generate one keyframe per scene using Adobe Firefly (standard or custom model)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    provider_status = _render_provider_status()
    firefly_status = provider_status.get("firefly") or {}
    if not firefly_status.get("configured"):
        raise UserFacingError(
            "Adobe Firefly credentials not configured.",
            hint="Open Settings → Adobe Firefly, save your Client ID and Client Secret, then retry.",
            code="FIREFLY_NOT_CONFIGURED",
            status_code=400,
        )

    firefly_cfg = dict((render_settings.get().get("firefly") or {}))
    client = _firefly_client()
    variant = variants[req.variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes.")

    width = int(req.width or proj.meta.get("width") or 768)
    height = int(req.height or proj.meta.get("height") or 432)
    results = []
    project_dir = store.project_dir(project_id)
    stills_dir = project_dir / "stills" / f"variant_{req.variant_index}"
    stills_dir.mkdir(parents=True, exist_ok=True)

    for idx, scene in enumerate(scenes):
        prompt = str(scene.get("prompt") or "cinematic music video still").strip()
        negative = str(scene.get("negative_prompt") or req.negative_prompt or "").strip()
        seed = req.seed if req.seed is not None else None
        custom_model_id = str(req.model_id or firefly_cfg.get("custom_model_id") or "").strip() or None

        try:
            result = client.generate_image(
                prompt=prompt,
                width=width,
                height=height,
                negative_prompt=negative,
                seed=seed,
                style=str(firefly_cfg.get("style") or "none"),
                content_class=str(firefly_cfg.get("content_class") or "photo"),
                custom_model_id=custom_model_id,
                timeout_s=180.0,
            )
            out_path = stills_dir / f"scene_{idx:04d}.png"
            result.image.save(str(out_path), format="PNG")
            rel = out_path.relative_to(project_dir).as_posix()
            results.append({
                "scene_index": idx,
                "path": rel,
                "seed": result.seed,
                "generation_id": result.generation_id,
                "custom_model_id": result.custom_model_id,
                "ok": True,
            })
        except UserFacingError:
            raise
        except Exception:
            logger.exception("Firefly scene render failed for scene %s", idx)
            results.append({"scene_index": idx, "ok": False, "error": "Firefly scene render failed"})

    return {"ok": True, "provider": "adobe-firefly", "results": results, "width": width, "height": height}


@app.post("/v1/projects/{project_id}/render/firefly/video")
def render_firefly_video(project_id: str, payload: dict[str, Any]):
    """Generate native Firefly video clips (text-to-video) for a plan variant.

    For each scene in the selected variant, submits a Firefly Video job and
    saves the returned MP4 under clips/variant_N/. Pass ``scene_index`` to
    render a single scene instead of the whole variant.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    payload = payload or {}
    variants = plan["variants"]
    variant_index = int(payload.get("variant_index") or 0)
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    provider_status = _render_provider_status()
    firefly_status = provider_status.get("firefly") or {}
    if not firefly_status.get("configured"):
        raise UserFacingError(
            "Adobe Firefly credentials not configured.",
            hint="Open Settings → Adobe Firefly, save your Client ID and Client Secret, then retry.",
            code="FIREFLY_NOT_CONFIGURED",
            status_code=400,
        )

    firefly_cfg = dict((render_settings.get().get("firefly") or {}))
    client = _firefly_client()
    scenes = variants[variant_index].get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes.")

    width = int(payload.get("width") or proj.meta.get("width") or 1280)
    height = int(payload.get("height") or proj.meta.get("height") or 720)
    duration_s = float(payload.get("duration_s") or firefly_cfg.get("video_duration_s") or 5)
    custom_model_id = str(payload.get("model_id") or firefly_cfg.get("custom_model_id") or "").strip() or None
    seed = payload.get("seed")
    seed = int(seed) if seed is not None else None

    requested_scene = payload.get("scene_index")
    scene_indices = (
        [int(requested_scene)]
        if requested_scene is not None
        else list(range(len(scenes)))
    )

    project_dir = store.project_dir(project_id)
    clips_dir = project_dir / "clips" / f"variant_{variant_index}"
    clips_dir.mkdir(parents=True, exist_ok=True)

    results = []
    for idx in scene_indices:
        if idx < 0 or idx >= len(scenes):
            results.append({"scene_index": idx, "ok": False, "error": "scene_index out of range"})
            continue
        scene = scenes[idx]
        prompt = str(scene.get("prompt") or "cinematic music video clip").strip()
        negative = str(scene.get("negative_prompt") or payload.get("negative_prompt") or "").strip()
        try:
            result = client.generate_video(
                prompt=prompt,
                width=width,
                height=height,
                duration_s=duration_s,
                negative_prompt=negative,
                seed=seed,
                custom_model_id=custom_model_id,
            )
            out_path = clips_dir / f"scene_{idx:04d}.mp4"
            out_path.write_bytes(result.video_bytes)
            rel = out_path.relative_to(project_dir).as_posix()
            results.append({
                "scene_index": idx,
                "path": rel,
                "seed": result.seed,
                "generation_id": result.generation_id,
                "duration_s": result.duration_s,
                "ok": True,
            })
        except UserFacingError:
            raise
        except Exception:
            logger.exception("Firefly video render failed for scene %s", idx)
            results.append({"scene_index": idx, "ok": False, "error": "Firefly video render failed"})

    return {
        "ok": True,
        "provider": "adobe-firefly",
        "kind": "video",
        "results": results,
        "width": width,
        "height": height,
    }


@app.post("/v1/projects/{project_id}/render/firefly/assemble")
def render_firefly_assemble(project_id: str, payload: dict[str, Any]):
    """Assemble Firefly-generated scene stills into a final MP4 video.

    Reads the PNGs from stills/variant_N/ that were produced by
    /render/firefly/scenes, assigns each scene's duration from the plan,
    and calls FFmpeg slideshow assembly + optional audio mux.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    variant_index = int((payload or {}).get("variant_index") or 0)
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "No scenes in selected variant.")

    project_dir = store.project_dir(project_id)
    stills_dir = project_dir / "stills" / f"variant_{variant_index}"
    imgs: list[Path] = []
    durations: list[float] = []
    for idx, scene in enumerate(scenes):
        img_path = stills_dir / f"scene_{idx:04d}.png"
        if img_path.exists():
            imgs.append(img_path)
            start = float(scene.get("start_s") or 0.0)
            end = float(scene.get("end_s") or (start + 4.0))
            durations.append(max(0.5, end - start))
        else:
            raise UserFacingError(
                f"Scene {idx} still not found at {img_path.name}.",
                hint="Run 'Render with Firefly' first to generate keyframes for all scenes.",
                code="FIREFLY_STILL_MISSING",
                status_code=400,
            )

    if not imgs:
        raise HTTPException(400, "No Firefly stills found for this variant.")

    out_path = project_dir / "output" / f"firefly_v{variant_index}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    assemble_slideshow(
        ffmpeg_path=settings.ffmpeg_path,
        image_paths=imgs,
        durations_s=durations,
        out_mp4=out_path,
        fps=int((payload or {}).get("fps") or 24),
    )

    audio_path = _project_audio_path(proj)
    fallback_audio = project_dir / "audio.wav"
    if audio_path is not None or fallback_audio.exists():
        resolved_audio = audio_path or fallback_audio
        muxed = out_path.with_name(out_path.stem + "_muxed.mp4")
        try:
            mux_audio(settings.ffmpeg_path, video_mp4=out_path, audio_path=resolved_audio, out_mp4=muxed)
            out_path = muxed
        except Exception:
            logger.warning("Firefly audio mux failed", exc_info=True)

    rel = out_path.relative_to(project_dir).as_posix()
    return {"ok": True, "provider": "adobe-firefly", "video": rel, "video_abs": str(out_path)}


@app.post("/v1/projects/{project_id}/render/imagineart/scenes")
def render_imagineart_scenes(project_id: str, req: RenderScenesRequest):
    """Generate one keyframe per scene using ImagineArt hosted image generation."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    provider_status = _render_provider_status()
    imagineart_status = provider_status.get("imagineart") or {}
    if not imagineart_status.get("configured"):
        raise UserFacingError(
            "ImagineArt API key not configured.",
            hint="Open Settings → Tokens, save your ImagineArt API key, then retry.",
            code="IMAGINEART_NOT_CONFIGURED",
            status_code=400,
        )
    if not imagineart_status.get("enabled"):
        raise UserFacingError(
            "ImagineArt provider is disabled.",
            hint="Open Settings → GPU / Render Runtime → ImagineArt and enable the provider.",
            code="IMAGINEART_DISABLED",
            status_code=400,
        )

    imagineart_cfg = dict((render_settings.get().get("imagineart") or {}))
    client = _imagineart_client()
    variant = variants[req.variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes.")

    width = int(req.width or proj.meta.get("width") or 768)
    height = int(req.height or proj.meta.get("height") or 432)
    results = []
    project_dir = store.project_dir(project_id)
    stills_dir = project_dir / "stills" / f"variant_{req.variant_index}"
    stills_dir.mkdir(parents=True, exist_ok=True)

    for idx, scene in enumerate(scenes):
        prompt = str(scene.get("prompt") or "cinematic music video still").strip()
        seed = req.seed if req.seed is not None else None
        try:
            result = client.generate_image(
                prompt=prompt,
                width=width,
                height=height,
                style=str(imagineart_cfg.get("image_style") or "imagine-turbo"),
                seed=seed,
                timeout_s=float(imagineart_cfg.get("timeout_s") or 180),
            )
            out_path = stills_dir / f"scene_{idx:04d}.png"
            result.image.save(str(out_path), format="PNG")
            rel = out_path.relative_to(project_dir).as_posix()
            results.append({
                "scene_index": idx,
                "path": rel,
                "seed": result.seed,
                "model": result.model,
                "ok": True,
            })
        except UserFacingError:
            raise
        except Exception:
            logger.exception("ImagineArt scene render failed for scene %s", idx)
            results.append({"scene_index": idx, "ok": False, "error": "ImagineArt scene render failed"})

    return {"ok": True, "provider": "imagineart", "results": results, "width": width, "height": height}


@app.post("/v1/projects/{project_id}/render/imagineart/video")
def render_imagineart_video(project_id: str, payload: dict[str, Any]):
    """Generate native ImagineArt video clips for plan scenes (text-to-video or image-to-video)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    payload = payload or {}
    variants = plan["variants"]
    variant_index = int(payload.get("variant_index") or 0)
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    provider_status = _render_provider_status()
    imagineart_status = provider_status.get("imagineart") or {}
    if not imagineart_status.get("configured"):
        raise UserFacingError(
            "ImagineArt API key not configured.",
            hint="Open Settings → Tokens, save your ImagineArt API key, then retry.",
            code="IMAGINEART_NOT_CONFIGURED",
            status_code=400,
        )

    imagineart_cfg = dict((render_settings.get().get("imagineart") or {}))
    client = _imagineart_client()
    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "No scenes in selected variant.")

    scene_index = payload.get("scene_index")
    scene_indices = [int(scene_index)] if scene_index is not None else list(range(len(scenes)))
    use_keyframe = bool(payload.get("use_keyframe", False))
    video_style = str(payload.get("video_style") or imagineart_cfg.get("video_style") or "kling-1.0-pro")
    timeout_s = float(payload.get("timeout_s") or imagineart_cfg.get("timeout_s") or 600)

    project_dir = store.project_dir(project_id)
    clips_dir = project_dir / "clips" / f"variant_{variant_index}"
    clips_dir.mkdir(parents=True, exist_ok=True)
    stills_dir = project_dir / "stills" / f"variant_{variant_index}"
    results = []

    for idx in scene_indices:
        if idx < 0 or idx >= len(scenes):
            continue
        scene = scenes[idx]
        prompt = str(scene.get("prompt") or "cinematic music video clip").strip()
        init_image = None
        if use_keyframe:
            still_path = stills_dir / f"scene_{idx:04d}.png"
            if still_path.exists():
                from PIL import Image

                init_image = Image.open(still_path).convert("RGB")

        try:
            result = client.generate_video(
                prompt=prompt,
                style=video_style,
                init_image=init_image,
                timeout_s=timeout_s,
                poll_interval_s=5.0,
            )
            out_path = clips_dir / f"scene_{idx:04d}.mp4"
            out_path.write_bytes(result.video_bytes)
            rel = out_path.relative_to(project_dir).as_posix()
            results.append({
                "scene_index": idx,
                "path": rel,
                "generation_id": result.generation_id,
                "model": result.model,
                "ok": True,
            })
        except UserFacingError:
            raise
        except Exception:
            logger.exception("ImagineArt video render failed for scene %s", idx)
            results.append({"scene_index": idx, "ok": False, "error": "ImagineArt video render failed"})

    return {
        "ok": True,
        "provider": "imagineart",
        "results": results,
        "variant_index": variant_index,
    }


@app.post("/v1/projects/{project_id}/render/imagineart/assemble")
def render_imagineart_assemble(project_id: str, payload: dict[str, Any]):
    """Assemble ImagineArt-generated scene stills into a final MP4 video."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated — run Plan first.")

    variant_index = int((payload or {}).get("variant_index") or 0)
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "No scenes in selected variant.")

    project_dir = store.project_dir(project_id)
    stills_dir = project_dir / "stills" / f"variant_{variant_index}"
    imgs: list[Path] = []
    durations: list[float] = []
    for idx, scene in enumerate(scenes):
        img_path = stills_dir / f"scene_{idx:04d}.png"
        if img_path.exists():
            imgs.append(img_path)
            start = float(scene.get("start_s") or 0.0)
            end = float(scene.get("end_s") or (start + 4.0))
            durations.append(max(0.5, end - start))
        else:
            raise UserFacingError(
                f"Scene {idx} still not found at {img_path.name}.",
                hint="Run 'Render with ImagineArt' first to generate keyframes for all scenes.",
                code="IMAGINEART_STILL_MISSING",
                status_code=400,
            )

    if not imgs:
        raise HTTPException(400, "No ImagineArt stills found for this variant.")

    out_path = project_dir / "output" / f"imagineart_v{variant_index}.mp4"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    assemble_slideshow(
        ffmpeg_path=settings.ffmpeg_path,
        image_paths=imgs,
        durations_s=durations,
        out_mp4=out_path,
        fps=int((payload or {}).get("fps") or 24),
    )

    audio_path = _project_audio_path(proj)
    fallback_audio = project_dir / "audio.wav"
    if audio_path is not None or fallback_audio.exists():
        resolved_audio = audio_path or fallback_audio
        muxed = out_path.with_name(out_path.stem + "_muxed.mp4")
        try:
            mux_audio(settings.ffmpeg_path, video_mp4=out_path, audio_path=resolved_audio, out_mp4=muxed)
            out_path = muxed
        except Exception:
            logger.warning("ImagineArt audio mux failed", exc_info=True)

    rel = out_path.relative_to(project_dir).as_posix()
    return {"ok": True, "provider": "imagineart", "video": rel, "video_abs": str(out_path)}


@app.post("/v1/projects/{project_id}/render/stills/scenes")
@app.post("/v1/projects/{project_id}/render/comfyui/scenes")
def render_scenes(project_id: str, req: RenderScenesRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[req.variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes")

    created = []
    resolved_loras = _normalize_render_loras(getattr(req, "loras", []))
    raw_controlnet_units = _request_payload(req).get("controlnet_units") if isinstance(_request_payload(req).get("controlnet_units"), list) else list(getattr(req, "controlnet_units", []))
    if req.workflow_family == "controlnet" and not raw_controlnet_units and req.controlnet_model and req.reference_asset:
        raw_controlnet_units = [
            {
                "model": req.controlnet_model,
                "reference_asset": req.reference_asset,
                "conditioning_mode": req.conditioning_mode,
                "strength": req.controlnet_strength,
            }
        ]

    selection = _resolve_still_scene_selection(
        model_id=req.model_id,
        checkpoint=req.checkpoint,
        workflow_family=req.workflow_family,
        controlnet_model=req.controlnet_model,
        reference_asset=req.reference_asset,
        conditioning_mode=req.conditioning_mode,
        controlnet_units=raw_controlnet_units,
    )
    controlnet_units = _normalize_controlnet_units(
        raw_controlnet_units,
        engine=str(selection.get("engine") or "comfyui"),
        family=selection.get("family"),
    )
    if str(selection.get("workflow_family") or "") == "controlnet" and not controlnet_units:
        raise UserFacingError(
            "No compatible ControlNet units were selected",
            hint="Attach one or more compatible ControlNet units before running the still render.",
            code="CONTROLNET_MISSING",
            status_code=400,
        )
    vae_name = (
        _resolve_optional_comfy_asset_name(req.vae, folder="vae", allowed_kinds={"vae"})
        if str(selection.get("engine") or "comfyui") == "comfyui"
        else (str(req.vae or "").strip() or None)
    )
    model_tag = _safe_name_tag(req.model_id or selection.get("checkpoint") or "default")
    workflow_tag = _safe_name_tag(selection.get("workflow_family") or "txt2img")
    ref_tag = _safe_name_tag(req.source_asset or req.reference_asset or "noref")
    for idx, sc in enumerate(scenes):
        # Deterministic output path for caching
        out_dir = store.project_dir(project_id) / "outputs" / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        seed = int(req.seed) + idx if req.seed is not None else _stable_seed(project_id, req.variant_index, idx)
        out_path = out_dir / f"v{req.variant_index:02d}_scene{idx:03d}_{workflow_tag}_{model_tag}_{ref_tag}_seed{seed}.png"
        p = {
            "variant_index": req.variant_index,
            "scene_index": idx,
            "model_id": req.model_id,
            "prompt": render_prompt_from_scene(sc, fallback=""),
            "source_prompt": sc.get("prompt") or sc.get("prompt_pack") or "",
            "storyboard": (
                dict(sc.get("storyboard"))
                if isinstance(sc.get("storyboard"), dict)
                else None
            ),
            "negative_prompt": req.negative_prompt,
            "seed": seed,
            "width": req.width,
            "height": req.height,
            "steps": req.steps,
            "cfg": req.cfg,
            "sampler": req.sampler,
            "checkpoint": selection.get("checkpoint"),
            "workflow_family": selection.get("workflow_family"),
            "source_asset": req.source_asset,
            "reference_asset": req.reference_asset,
            "inpaint_mask": req.inpaint_mask,
            "outpaint": _request_payload(req.outpaint) if req.outpaint else None,
            "conditioning_mode": selection.get("conditioning_mode"),
            "controlnet_model": req.controlnet_model,
            "controlnet_name": selection.get("controlnet_name"),
            "controlnet_strength": req.controlnet_strength,
            "controlnet_units": controlnet_units,
            "engine": selection.get("engine"),
            "family": selection.get("family"),
            "model_path": str(selection.get("model_path")) if selection.get("model_path") else None,
            "loras": resolved_loras,
            "vae": vae_name,
            "denoise_strength": req.denoise_strength,
            "hires_fix": _request_payload(req.hires_fix) if req.hires_fix else None,
            "refiner": _request_payload(req.refiner) if req.refiner else None,
            "upscaler": req.upscaler,
            "out_path": str(out_path),
        }
        job_type = "internal_still_scene" if str(selection.get("engine") or "comfyui") == "internal" else "comfyui_scene"
        job = jobs.create(project_id, job_type, p)
        created.append(job.__dict__)

    proj.meta.setdefault("jobs", []).extend(created)
    store.save(proj)

    return {"ok": True, "enqueued": len(created), "jobs": created}



def _server_resolved_tensorrt_payload(req: TensorRTStandaloneRenderRequest) -> dict[str, Any]:
    """Validate a public model ID without persisting its trusted local path.

    Public TensorRT requests never accept or reinterpret filesystem paths.
    Workers resolve the private installation path immediately before execution.
    """

    supported_model_id = TENSORRT_VIDEO_MODEL_ID
    requested_model_id = str(req.model_id or supported_model_id).strip()
    if requested_model_id != supported_model_id:
        raise UserFacingError(
            "This TensorRT model is not executable by Studio's standalone renderer",
            hint=f"Select {supported_model_id} in Models. Other TensorRT catalog entries are discovery-only.",
            code="TRT_MODEL_UNSUPPORTED",
            status_code=400,
        )
    if _resolve_installed_model_path(supported_model_id, materialize_remote=True) is None:
        raise UserFacingError(
            "Local TensorRT SD 1.5 bundle is not installed",
            hint="Open Models and verify the canonical TensorRT bundle, then retry.",
            code="TRT_MODEL_NOT_FOUND",
            status_code=400,
        )

    payload = _request_payload(req)
    payload["model_id"] = supported_model_id
    entry = _catalog_entry(supported_model_id)
    if entry is not None:
        render_meta = entry.get("render") if isinstance(entry.get("render"), dict) else {}
        payload["workflow_family"] = str(render_meta.get("workflow_family") or entry.get("family") or "")
        if render_meta.get("base_model_id"):
            payload["base_model_id"] = str(render_meta.get("base_model_id"))
    return payload


def _resolved_tensorrt_execution_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Inject the trusted bundle path only into an in-memory execution payload."""

    model_id = str(payload.get("model_id") or TENSORRT_VIDEO_MODEL_ID).strip()
    if model_id != TENSORRT_VIDEO_MODEL_ID:
        raise UserFacingError(
            "This TensorRT model is not executable by Studio's standalone renderer",
            hint=f"Select {TENSORRT_VIDEO_MODEL_ID} in Models.",
            code="TRT_MODEL_UNSUPPORTED",
            status_code=400,
        )
    model_path = _resolve_installed_model_path(model_id, materialize_remote=True)
    if model_path is None:
        raise UserFacingError(
            "Local TensorRT SD 1.5 bundle is not installed",
            hint="Open Models and verify the canonical TensorRT bundle, then retry.",
            code="TRT_MODEL_NOT_FOUND",
            status_code=400,
        )
    execution_payload = dict(payload)
    execution_payload["model_id"] = TENSORRT_VIDEO_MODEL_ID
    execution_payload["model_path"] = str(Path(model_path).expanduser().resolve())
    return execution_payload


@app.post("/v1/projects/{project_id}/render/tensorrt-standalone")
def render_tensorrt_standalone(project_id: str, req: TensorRTStandaloneRenderRequest):
    """Enqueue a standalone TensorRT image render job."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    payload = _server_resolved_tensorrt_payload(req)
    job = jobs.create(project_id, "tensorrt_standalone", payload)
    job.progress = {
        "stage": "queued",
        "current": 0,
        "total": 1,
        "percent": 0.0,
        "message": f"Queued TensorRT standalone render for model {payload['model_id']}",
    }
    jobs.save(job)
    proj.meta.setdefault("jobs", []).append(job.__dict__)
    store.save(proj)
    return {"ok": True, "job": job.__dict__}


def _enqueue_internal_video_job(
    project_id: str,
    proj: Any,
    payload: dict[str, Any],
    *,
    job_type: str = "internal_video",
    queued_message: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    """Apply canonical motion/preflight rules and persist one video-render job."""

    resolved_payload, _parseq = _apply_active_parseq_motion(proj, payload)
    preflight = _internal_render_preflight_data(project_id, resolved_payload)
    resolved_payload = _persist_resolved_internal_video_payload(resolved_payload, preflight)
    estimated_total = max(1, int(preflight.get("estimated_frames", 1)) + 3)
    if str(preflight.get("mode") or "").strip().lower() == "tensorrt":
        estimated_total += max(0, int(preflight.get("estimated_keyframes", 0)))
    job = jobs.create(project_id, job_type, resolved_payload)
    job.progress = {
        "stage": "queued",
        "current": 0,
        "total": estimated_total,
        "percent": 0.0,
        "message": queued_message
        or f"Queued internal render for model {preflight.get('model_id')}",
    }
    jobs.save(job)
    proj.meta.setdefault("jobs", []).append(job.__dict__)
    store.save(proj)
    return job, preflight


@app.post("/v1/projects/{project_id}/render/tensorrt-deforum", deprecated=True)
def render_tensorrt_deforum(project_id: str, req: TensorRTStandaloneRenderRequest):
    """Compatibility route for the canonical TensorRT keyframe-video renderer.

    The former implementation generated simulated noise frames after merely
    deserializing an engine.  Release builds must never present that as model
    inference, so this route now performs the same server-side preflight and
    queues the canonical internal TensorRT video path.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    payload = _server_resolved_tensorrt_payload(req)
    payload["render_mode"] = "tensorrt"
    payload["compatibility_source"] = "tensorrt-deforum"
    job, preflight = _enqueue_internal_video_job(
        project_id,
        proj,
        payload,
        job_type="tensorrt_deforum",
        queued_message=f"Queued canonical TensorRT compatibility render for model {payload['model_id']}",
    )
    return {
        "ok": True,
        "job": job.__dict__,
        "preflight": _public_render_preflight(preflight),
        "compatibility": {
            "route": "tensorrt-deforum",
            "execution_mode": "canonical_tensorrt_keyframe_video",
            "legacy_deforum_schedule_applied": False,
        },
    }



@app.post("/v1/projects/{project_id}/render/tensorrt-standalone/preview")
def render_tensorrt_standalone_preview(project_id: str, req: TensorRTStandaloneRenderRequest):
    """Synchronously run a low-latency preview render."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    from .services import tensorrt_standalone
    # Run the generation synchronously in the request thread
    try:
        payload = _resolved_tensorrt_execution_payload(_server_resolved_tensorrt_payload(req))
        # Override steps for fast preview
        payload["steps"] = min(payload.get("steps", 8), 8)
        
        # We need a custom run_preview in tensorrt_standalone
        result = tensorrt_standalone.run_preview(project_id, payload)
        return {"ok": True, "image": result["image"], "engine_used": result["engine_used"]}
    except UserFacingError:
        raise
    except Exception as exc:
        logger.exception("TensorRT preview render failed")
        raise HTTPException(500, "TensorRT preview render failed") from exc



@app.post("/v1/projects/{project_id}/render/internal/video")
def render_internal_video(project_id: str, req: InternalVideoRenderRequest):
    """Enqueue a full internal render job (CPU-safe baseline)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    job, preflight = _enqueue_internal_video_job(
        project_id,
        proj,
        _request_payload(req),
    )
    return {
        "ok": True,
        "job": job.__dict__,
        "preflight": _public_render_preflight(preflight),
    }


@app.get("/v1/projects/{project_id}/render/motion_sequencer")
def render_motion_sequencer(project_id: str, variant_index: int = 0, fps: int = 24):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    variant, scenes = _project_variant_for_render(proj, int(variant_index or 0))
    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    analysis = proj.meta.get("analysis") if isinstance(proj.meta.get("analysis"), dict) else {}
    generated = parseq_adapter.build_parseq_manifest(
        variant=variant,
        analysis=analysis,
        fps=max(1, min(60, int(fps or variant.get("fps") or 24))),
        duration_s=duration_s,
    )
    active = _active_parseq_manifest(proj)
    manifest = active or generated
    parsed = parseq_adapter.parseq_manifest_to_internal_overrides(manifest)
    recipe_graph = parseq_adapter.build_render_recipe_graph(
        manifest=manifest,
        internal_request=(proj.meta.get("last_internal_render") if isinstance(proj.meta.get("last_internal_render"), dict) else {}),
    )
    return {
        "ok": True,
        "variant_index": int(variant_index or 0),
        "active": active,
        "generated": generated,
        "summary": parsed.get("summary"),
        "overrides": parsed.get("overrides"),
        "recipe_graph": recipe_graph,
    }


@app.post("/v1/projects/{project_id}/render/motion_sequencer/apply")
def render_motion_sequencer_apply(project_id: str, req: ParseqMotionApplyRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    variant, scenes = _project_variant_for_render(proj, int(req.variant_index or 0))
    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    analysis = proj.meta.get("analysis") if isinstance(proj.meta.get("analysis"), dict) else {}
    manifest = req.manifest if isinstance(req.manifest, dict) else parseq_adapter.build_parseq_manifest(
        variant=variant,
        analysis=analysis,
        fps=int(req.fps or variant.get("fps") or 24),
        duration_s=duration_s,
    )
    parsed = parseq_adapter.parseq_manifest_to_internal_overrides(manifest)
    recipe_graph = parseq_adapter.build_render_recipe_graph(manifest=manifest, internal_request={})
    if req.activate:
        proj.meta["active_parseq_manifest"] = manifest
        proj.meta["render_recipe_graph"] = recipe_graph
        store.save(proj)
    return {
        "ok": True,
        "active": bool(req.activate),
        "manifest": manifest,
        "summary": parsed.get("summary"),
        "overrides": parsed.get("overrides"),
        "recipe_graph": recipe_graph,
    }




def _internal_model_family(model_path: Path) -> str:
    path_hint = str(model_path.name or "").lower()
    if "flux" in path_hint:
        return "flux"
    if "sd35" in path_hint or "sd3" in path_hint or "stable-diffusion-3" in path_hint:
        return "sd3"
    if "sdxl" in path_hint:
        return "sdxl"
    if "sd15" in path_hint or "stable-diffusion-v1" in path_hint:
        return "sd15"
    mi = model_path / "model_index.json"
    if mi.exists():
        try:
            data = json.loads(mi.read_text(encoding="utf-8"))
            cls = str(data.get("_class_name") or "")
            if "Flux" in cls:
                return "flux"
            if "StableDiffusion3" in cls:
                return "sd3"
            if "XL" in cls or "XLPipeline" in cls:
                return "sdxl"
            if "StableDiffusion" in cls:
                return "sd15"
        except Exception:
            pass
    return "unknown"


SD35_INTERNAL_MIN_CUDA_VRAM_GB = 14.0


def _internal_model_family_for_request(model_id: str, model_path: Path) -> str:
    model_id_l = str(model_id or "").lower()
    if "flux" in model_id_l:
        return "flux"
    if "sd35" in model_id_l or "stable-diffusion-3" in model_id_l:
        return "sd3"
    if "sdxl" in model_id_l:
        return "sdxl"
    if "sd15" in model_id_l or "stable-diffusion-v1" in model_id_l:
        return "sd15"
    return _internal_model_family(model_path)


def _internal_model_hardware_issue(
    model_id: str,
    model_family: str,
    hw: dict[str, Any],
    requested_device: str,
) -> dict[str, str] | None:
    family = str(model_family or "").lower()
    model_id_l = str(model_id or "").lower()
    if family not in {"sd3", "sd35"} and "sd35" not in model_id_l:
        return None

    requested = str(requested_device or "auto").strip().lower()
    backend = requested if requested in {"cuda", "cpu", "mps", "directml"} else str(hw.get("backend") or "cpu").lower()
    if backend != "cuda":
        return None

    vram_gb = float(hw.get("vram_gb") or 0.0)
    if vram_gb >= SD35_INTERNAL_MIN_CUDA_VRAM_GB:
        return None

    return {
        "code": "MODEL_UNSUPPORTED_FOR_HARDWARE",
        "message": (
            f"Stable Diffusion 3.5 internal rendering needs at least "
            f"{SD35_INTERNAL_MIN_CUDA_VRAM_GB:.0f} GB of CUDA VRAM for this Studio video path; "
            f"this GPU reports {vram_gb:.1f} GB."
        ),
        "hint": "Use SDXL or SD 1.5 for local CUDA rendering on this GPU, or use hosted Stability for SD3.5 keyframes.",
    }


def _internal_settings_from_payload(
    payload: dict[str, Any],
    *,
    model_id: str,
    render_tier: str,
    device_preference: str,
    temporal_mode: str | None = None,
) -> InternalVideoSettings:
    refiner = payload.get("refiner") if isinstance(payload.get("refiner"), dict) else None
    video_motion_score_mode = str(payload.get("video_model_motion_score_mode") or "auto").strip().lower()
    if video_motion_score_mode not in {"auto", "manual", "off"}:
        video_motion_score_mode = "auto"
    video_anchor_mode = str(payload.get("video_model_anchor_mode") or "start").strip().lower()
    if video_anchor_mode not in {"start", "end", "both", "loop"}:
        video_anchor_mode = "start"
    try:
        video_manual_motion_score = int(payload.get("video_model_manual_motion_score", 4))
    except Exception:
        video_manual_motion_score = 4
    prompt_refine_raw = payload.get("video_model_prompt_refine", True)
    video_prompt_refine = (
        str(prompt_refine_raw).strip().lower() not in {"0", "false", "no", "off"}
        if isinstance(prompt_refine_raw, str)
        else bool(prompt_refine_raw)
    )
    timeline_camera_raw = payload.get("video_model_apply_timeline_camera", True)
    video_apply_timeline_camera = (
        str(timeline_camera_raw).strip().lower() not in {"0", "false", "no", "off"}
        if isinstance(timeline_camera_raw, str)
        else bool(timeline_camera_raw)
    )
    deforum_override_keys = (
        "deforum_prompts",
        "deforum_negative_prompts",
        "deforum_zoom",
        "deforum_angle",
        "deforum_translation_x",
        "deforum_translation_y",
        "deforum_translation_z",
        "deforum_rotation_3d_x",
        "deforum_rotation_3d_y",
        "deforum_rotation_3d_z",
        "deforum_fov",
        "deforum_strength_schedule",
        "deforum_cfg_scale_schedule",
        "deforum_steps_schedule",
        "deforum_denoise_schedule",
    )
    deforum_overrides = {
        key: payload.get(key)
        for key in deforum_override_keys
        if payload.get(key) is not None
    }
    motion_strategy = normalize_internal_motion_strategy(payload.get("motion_strategy") or payload.get("internal_motion_strategy"))
    video_keyframe_renderer = normalize_video_model_keyframe_renderer(payload.get("video_model_keyframe_renderer"))
    video_scene_motion = normalize_video_model_scene_motion(payload.get("video_model_scene_motion"))
    try:
        storyboard_shot_max_s = float(payload.get("storyboard_shot_max_s", 4.0))
    except Exception:
        storyboard_shot_max_s = 4.0
    return InternalVideoSettings(
        fps_render=int(payload.get("fps_render", 2)),
        fps_output=int(payload.get("fps_output", 24)),
        width=int(payload.get("width", 768)),
        height=int(payload.get("height", 432)),
        steps=int(payload.get("steps", 15)),
        cfg=float(payload.get("cfg", 7.0)),
        sampler=str(payload.get("sampler", "euler")),
        seed=(int(payload["seed"]) if payload.get("seed") is not None else None),
        keyframe_interval_s=float(payload.get("keyframe_interval_s", 5.0)),
        keyframe_continuity_mode=normalize_keyframe_continuity_mode(
            payload.get("keyframe_continuity_mode")
        ),
        interpolation_engine=str(payload.get("interpolation_engine", "auto")),
        negative_prompt=str(payload.get("negative_prompt", "blurry, low quality, watermark, text, logo")),
        model_id=model_id,
        loras=tuple(_normalize_render_loras(payload.get("loras"))),
        vae=str(payload.get("vae") or "").strip() or None,
        refiner=refiner,
        render_tier=render_tier,
        device_preference=device_preference,
        temporal_mode=temporal_mode if temporal_mode is not None else str(payload.get("temporal_mode", "frame_img2img")),
        temporal_strength=float(payload.get("temporal_strength", 0.35)),
        temporal_steps=(int(payload["temporal_steps"]) if payload.get("temporal_steps") is not None else None),
        refine_every_n_frames=int(payload.get("refine_every_n_frames", 1)),
        anchor_strength=float(payload.get("anchor_strength", 0.20)),
        prompt_blend=bool(payload.get("prompt_blend", True)),
        resume_existing_frames=bool(payload.get("resume_existing_frames", True)),
        motion_strategy=motion_strategy,
        storyboard_shot_max_s=max(1.0, min(12.0, storyboard_shot_max_s)),
        video_model_engine=str(payload.get("video_model_engine") or "auto"),
        video_model_id=(str(payload.get("video_model_id")).strip() or None) if payload.get("video_model_id") is not None else None,
        video_model_path=(str(payload.get("video_model_path")).strip() or None) if payload.get("video_model_path") is not None else None,
        video_model_max_frames_per_scene=int(payload.get("video_model_max_frames_per_scene", 25)),
        video_model_motion_bucket_id=int(payload.get("video_model_motion_bucket_id", 127)),
        video_model_noise_aug_strength=float(payload.get("video_model_noise_aug_strength", 0.02)),
        video_model_decode_chunk_size=int(payload.get("video_model_decode_chunk_size", 8)),
        video_model_dtype=str(payload.get("video_model_dtype") or "auto"),
        video_model_cpu_offload=bool(payload.get("video_model_cpu_offload", False)),
        video_model_motion_score_mode=video_motion_score_mode,
        video_model_manual_motion_score=max(1, min(7, video_manual_motion_score)),
        video_model_anchor_mode=video_anchor_mode,
        video_model_prompt_refine=video_prompt_refine,
        video_model_scene_motion=video_scene_motion,
        video_model_apply_timeline_camera=video_apply_timeline_camera,
        video_model_keyframe_renderer=video_keyframe_renderer,
        video_model_keyframe_model_id=(str(payload.get("video_model_keyframe_model_id")).strip() or None) if payload.get("video_model_keyframe_model_id") is not None else None,
        video_model_motion_score_schedule=payload.get("video_model_motion_score_schedule"),
        video_model_noise_aug_schedule=payload.get("video_model_noise_aug_schedule"),
        anchor_strength_schedule=payload.get("anchor_strength_schedule"),
        source_asset=(str(payload.get("source_asset")).strip() or None) if payload.get("source_asset") is not None else None,
        source_strength=float(payload.get("source_strength", 0.55)),
        deforum_overrides=deforum_overrides or None,
    )


def _apply_storyboard_full_motion_settings(
    settings_obj: InternalVideoSettings,
    payload: dict[str, Any],
) -> InternalVideoSettings:
    """Apply full-motion defaults without overriding explicit quality controls."""
    return replace(
        settings_obj,
        video_model_max_frames_per_scene=max(
            8,
            int(settings_obj.video_model_max_frames_per_scene or 8),
        ),
        video_model_scene_motion=normalize_video_model_scene_motion(
            payload.get("video_model_scene_motion") or "scene"
        ),
        keyframe_continuity_mode=normalize_keyframe_continuity_mode(
            payload.get("keyframe_continuity_mode")
            if "keyframe_continuity_mode" in payload
            else "project"
        ),
        keyframe_interval_s=min(
            float(settings_obj.keyframe_interval_s),
            float(settings_obj.storyboard_shot_max_s),
        ),
    )


def _creative_direction_fallback_variant(proj: Any, variant_index: int) -> dict[str, Any] | None:
    if variant_index != 0:
        return None
    payload = _build_creative_direction_payload(
        proj,
        variant_index=0,
        preset="cinematic",
        sensitivity=1.0,
    )
    scenes_raw = list(payload.get("scenes") or [])
    if not scenes_raw:
        return None

    scenes: list[dict[str, Any]] = []
    for index, scene in enumerate(scenes_raw):
        if not isinstance(scene, dict):
            continue
        try:
            start_s = float(scene.get("start_s") or index * 5.0)
        except Exception:
            start_s = float(index * 5.0)
        try:
            end_s = float(scene.get("end_s") or (start_s + float(scene.get("duration_s") or 5.0)))
        except Exception:
            end_s = start_s + 5.0
        duration_s = max(0.2, end_s - start_s)
        prompt = str(scene.get("prompt_pack") or scene.get("prompt") or DEFAULT_RENDER_PROMPT).strip()
        scenes.append(
            {
                "index": index,
                "name": str(scene.get("name") or f"Scene {index + 1}"),
                "start_s": start_s,
                "end_s": start_s + duration_s,
                "duration_s": duration_s,
                "energy": scene.get("energy"),
                "energy_label": scene.get("energy_label"),
                "prompt": prompt or DEFAULT_RENDER_PROMPT,
                "negative_prompt": str(payload.get("negative_prompt") or "blurry, low quality, watermark, text, logo"),
                "transcript_cue": str(scene.get("transcript_cue") or ""),
                "camera_hint": str(scene.get("camera_hint") or ""),
                "motion_hint": str(scene.get("motion_hint") or ""),
                "reactive_params": scene.get("reactive_params") if isinstance(scene.get("reactive_params"), dict) else {},
                "scene_source": "creative_direction_fallback",
            }
        )
    if not scenes:
        return None

    duration_s = max(float(scene.get("end_s") or 0.0) for scene in scenes)
    return {
        "index": 0,
        "name": "Creative direction fallback",
        "duration_s": duration_s,
        "provider_mode": "local-heuristic",
        "scenes": scenes,
        "_fallback_plan_source": "creative_direction_fallback",
    }


def _internal_render_variant_or_fallback(proj: Any, variant_index: int) -> tuple[dict[str, Any], bool]:
    plan = proj.meta.get("last_plan") if isinstance(getattr(proj, "meta", None), dict) else None
    variants = list(plan.get("variants") or []) if isinstance(plan, dict) else []
    if variants:
        if variant_index < 0 or variant_index >= len(variants):
            raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")
        variant = variants[variant_index]
        if not isinstance(variant, dict):
            raise UserFacingError("Selected variant is invalid", hint="Re-run Plan, then retry.")
        return variant, False

    fallback = _creative_direction_fallback_variant(proj, variant_index)
    if fallback:
        return fallback, True

    raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")


def _resolve_internal_render_request(
    project_id: str,
    payload: dict[str, Any],
) -> tuple[Any, dict[str, Any], str, Path, Path | None, InternalVideoSettings]:
    proj = store.get(project_id)
    if not proj:
        raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")

    variant_index = int(payload.get("variant_index", 0))
    variant, _used_fallback = _internal_render_variant_or_fallback(proj, variant_index)
    scenes = variant.get("scenes") or []
    if not scenes:
        raise UserFacingError("Selected variant has no scenes", hint="Re-run Plan with at least 1 scene.")

    req_model_id = str(payload.get("model_id") or "hf_sd15_internal")
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or "auto"))
    provider_cfg = _render_provider_status(hw).get("settings") or {}
    directml_cfg = dict(provider_cfg.get("directml") or {})
    directml_enabled = bool(directml_cfg.get("enabled", True))
    requested_device = str(payload.get("device_preference") or tier_plan.get("device_preference") or "auto").strip().lower()
    if requested_device == "directml" and not directml_enabled:
        raise UserFacingError(
            "DirectML is disabled in Settings.",
            hint="Enable AMD / DirectML internal runtime in Settings, or switch the device preference to CPU/CUDA/MPS.",
            code="DIRECTML_DISABLED",
            status_code=400,
        )
    if requested_device == "auto" and str(hw.get("backend") or "").lower() == "directml" and not directml_enabled:
        requested_device = "cpu"
    if requested_device == "auto" and str(hw.get("backend") or "").lower() == "directml" and not bool(directml_cfg.get("allow_auto_selection", True)):
        requested_device = "cpu"

    auto_model_hardware_issue: dict[str, str] | None = None

    def _pick_auto_model() -> str | None:
        nonlocal auto_model_hardware_issue
        preferred = str(tier_plan.get("preferred_internal_model") or hw.get("preferred_internal_model") or "hf_sd15_internal")
        if requested_device == "directml":
            preferred = str(directml_cfg.get("preferred_model") or preferred or "auto").strip().lower()
            if preferred == "auto":
                preferred = "hf_sdxl_internal"
            fallbacks = [preferred, "hf_sdxl_internal", "hf_sd15_internal"]
        else:
            fallbacks = [preferred, "hf_sd35_medium_internal", "hf_sdxl_internal", "hf_sd15_internal"]
        seen: set[str] = set()
        for mid in fallbacks:
            if mid in seen:
                continue
            seen.add(mid)
            installed = _resolve_installed_model_path(mid, materialize_remote=False)
            if not installed:
                continue
            family = _internal_model_family_for_request(mid, installed)
            hardware_issue = _internal_model_hardware_issue(mid, family, hw, requested_device)
            if hardware_issue:
                auto_model_hardware_issue = hardware_issue
                continue
            return mid
        return None

    model_id = req_model_id
    if req_model_id.lower() in ("auto", "auto_internal"):
        picked = _pick_auto_model()
        if not picked:
            if auto_model_hardware_issue:
                raise UserFacingError(
                    str(auto_model_hardware_issue["message"]),
                    hint=str(auto_model_hardware_issue["hint"]),
                    code=str(auto_model_hardware_issue["code"]),
                    status_code=400,
                )
            raise UserFacingError(
                "No internal diffusion model installed",
                hint="Open Models and install an internal Diffusers model such as SD 1.5, SDXL, or SD3.5 Medium, then retry.",
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        model_id = picked

    model_path = _resolve_installed_model_path(model_id, materialize_remote=False)
    if not model_path:
        issue = getattr(models, "internal_asset_issue", lambda _model_id: None)(model_id)
        if issue == "incomplete":
            missing = getattr(models, "missing_diffusers_components", lambda _model_id: [])(model_id)
            missing_hint = (
                f" Missing components: {', '.join(missing)}."
                if missing
                else ""
            )
            raise UserFacingError(
                "Internal model install is incomplete",
                hint=(
                    "Open Models and reinstall the requested internal model, or re-sync it from the "
                    f"Hugging Face bucket.{missing_hint}"
                ),
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        raise UserFacingError(
            "Internal model not installed",
            hint="Open Models and install the requested internal model, then retry.",
            code="MODEL_NOT_INSTALLED",
            status_code=400,
        )

    model_family = _internal_model_family_for_request(model_id, model_path)
    if model_family == "flux":
        raise UserFacingError(
            "FLUX is a still and storyboard-keyframe model, not an internal video base model.",
            hint=(
                "Use FLUX.1 Schnell from Studio still rendering, then animate the saved keyframe with "
                "SVD, Wan, or the layered animation tools. Select SDXL or SD 1.5 for frame-to-frame internal video."
            ),
            code="FLUX_VIDEO_BASE_UNSUPPORTED",
            status_code=400,
        )
    if model_family == "unknown":
        raise UserFacingError(
            "Internal diffusion model family is unsupported",
            hint="Reinstall a supported SD 1.5, SDXL, or SD3.5 internal video model.",
            code="INTERNAL_MODEL_FAMILY_UNSUPPORTED",
            status_code=400,
        )
    effective_device_preference = requested_device
    if requested_device == "directml" and model_family not in {"sd15", "sdxl"}:
        raise UserFacingError(
            "DirectML currently supports SD 1.5 and SDXL only.",
            hint="Use SDXL or SD 1.5 for AMD / DirectML, or switch device preference to CPU for SD3.5.",
            code="DIRECTML_MODEL_UNSUPPORTED",
            status_code=400,
        )
    if requested_device == "auto" and str(hw.get("backend") or "").lower() == "directml" and model_family not in {"sd15", "sdxl"}:
        effective_device_preference = "cpu"
    hardware_issue = _internal_model_hardware_issue(model_id, model_family, hw, requested_device)
    if hardware_issue:
        raise UserFacingError(
            str(hardware_issue["message"]),
            hint=str(hardware_issue["hint"]),
            code=str(hardware_issue["code"]),
            status_code=400,
        )

    tier_defaults = dict(tier_plan.get("defaults") or {})
    motion_strategy = normalize_internal_motion_strategy(payload.get("motion_strategy") or payload.get("internal_motion_strategy"))
    effective_temporal_mode = (
        str(payload.get("temporal_mode"))
        if payload.get("temporal_mode") is not None
        else str(tier_defaults.get("temporal_mode", "frame_img2img"))
    )
    if motion_strategy == "storyboard_full_motion":
        effective_temporal_mode = "video_model"
    settings_obj = _internal_settings_from_payload(
        payload,
        model_id=model_id,
        render_tier=str(tier_plan.get("applied_tier") or payload.get("render_tier") or "auto"),
        device_preference=effective_device_preference,
        temporal_mode=effective_temporal_mode,
    )
    if motion_strategy == "storyboard_full_motion":
        settings_obj = _apply_storyboard_full_motion_settings(settings_obj, payload)
    tensorrt_keyframe_bundle_path: Path | None = None
    if settings_obj.temporal_mode == "video_model":
        engine, video_model_id, video_model_path = _resolve_internal_video_model_selection(
            payload,
            base_model_family=model_family,
        )
        settings_obj = replace(
            settings_obj,
            video_model_engine=engine,
            video_model_id=video_model_id,
            video_model_path=str(video_model_path),
        )
        settings_obj = _apply_internal_video_model_memory_safety(settings_obj, hw)
        if normalize_video_model_keyframe_renderer(settings_obj.video_model_keyframe_renderer) == "tensorrt_sd15":
            settings_obj = replace(
                settings_obj,
                video_model_keyframe_model_id=TENSORRT_VIDEO_MODEL_ID,
            )
            resolved_bundle = _resolve_installed_model_path(
                TENSORRT_VIDEO_MODEL_ID,
                materialize_remote=False,
            )
            if not resolved_bundle:
                raise UserFacingError(
                    "The TensorRT SD 1.5 storyboard-anchor bundle is not installed",
                    hint=(
                        "Open Models and verify the canonical TensorRT bundle. Studio requires its "
                        "engine, ONNX, base-model, and compiled-profile metadata before rendering."
                    ),
                    code="TRT_ANCHOR_BUNDLE_NOT_INSTALLED",
                    status_code=400,
                )
            tensorrt_keyframe_bundle_path = Path(resolved_bundle).expanduser().resolve()
    return (
        proj,
        variant,
        model_id,
        model_path,
        tensorrt_keyframe_bundle_path,
        settings_obj,
    )


def _hosted_render_preflight_data(
    project_id: str,
    payload: dict[str, Any],
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    provider_status = _render_provider_status()
    stability = dict(provider_status.get("stability") or {})
    provider_cfg = dict((provider_status.get("settings") or {}).get("stability") or {})
    if not stability.get("configured"):
        raise UserFacingError(
            "Stability API key is not configured.",
            hint="Open Settings and save a Stability API key, then retry the hosted render.",
            code="STABILITY_API_KEY_MISSING",
            status_code=400,
        )
    if not stability.get("enabled"):
        raise UserFacingError(
            "Hosted Stability fallback is disabled.",
            hint="Open Settings and enable the Stability hosted fallback, then retry.",
            code="STABILITY_HOSTED_DISABLED",
            status_code=400,
        )

    proj = store.get(project_id)
    if not proj:
        raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")

    variant_index = int(payload.get("variant_index", 0))
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise UserFacingError("Selected variant has no scenes", hint="Re-run Plan with at least 1 scene.")

    hosted_service = str(payload.get("hosted_service") or "default").strip().lower()
    if hosted_service in {"", "default"}:
        hosted_service = str(provider_cfg.get("service") or "sd3")
    hosted_model = str(payload.get("hosted_model") or provider_cfg.get("model") or "sd3.5-large-turbo").strip().lower()
    hosted_style = str(payload.get("hosted_style_preset") or provider_cfg.get("style_preset") or "none").strip().lower()
    hosted_model_id = f"stability:{hosted_service}:{hosted_model if hosted_service == 'sd3' else 'default'}"

    hosted_payload = dict(payload)
    hosted_payload.setdefault("cfg", provider_cfg.get("cfg_scale", 6.5))
    hosted_payload.setdefault("temporal_strength", provider_cfg.get("strength", 0.55))
    settings_obj = _internal_settings_from_payload(
        hosted_payload,
        model_id=hosted_model_id,
        render_tier=str(payload.get("render_tier") or "auto"),
        device_preference="cpu",
        temporal_mode="keyframes" if str(payload.get("temporal_mode") or "frame_img2img") == "frame_img2img" else str(payload.get("temporal_mode") or "keyframes"),
    )

    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    total_frames = int(math.ceil(duration_s * max(1, int(settings_obj.fps_render))))
    keyframes = max(1, len(_scene_keyframe_times(scenes, settings_obj.keyframe_interval_s)))
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or "auto"), duration_s=duration_s)
    tier_plan["chunk_plan"] = _build_render_chunk_plan(hw, applied_tier=str(tier_plan.get("applied_tier") or "draft"), duration_s=duration_s, total_frames=total_frames, fps_render=int(settings_obj.fps_render), render_mode="hosted")
    cache = describe_internal_render_cache(
        project_dir=store.project_dir(project_id),
        variant_index=variant_index,
        variant=variant,
        scenes=scenes,
        timeline=(proj.meta.get("timeline") or None),
        model_dir=Path(f"stability_platform/{hosted_service}/{hosted_model}"),
        settings=settings_obj,
        total_frames=total_frames,
    )
    warnings = [
        "Hosted Stability mode generates keyframes through the public image API, then assembles and muxes the video locally.",
        "Hosted mode does not call a public Stability video endpoint because one was not found in the current public API spec.",
    ]
    if reason:
        warnings.insert(0, reason)
    if str(payload.get("temporal_mode") or "frame_img2img") == "frame_img2img":
        warnings.append("Frame img2img temporal mode is reduced to keyframe continuity in hosted mode to avoid per-frame API calls.")
    return {
        "ok": True,
        "mode": "hosted",
        "variant_index": variant_index,
        "model_id": hosted_model_id,
        "model_path": None,
        "duration_s": duration_s,
        "estimated_frames": total_frames,
        "estimated_keyframes": keyframes,
        "device": "hosted+local_ffmpeg",
        "hardware": hw,
        "tier_plan": tier_plan,
        "resume_existing_frames": bool(settings_obj.resume_existing_frames),
        "warnings": warnings,
        "cache": cache,
        "installed_internal_models": _installed_internal_models_status(),
        "hosted_provider": {
            "provider": "stability",
            "service": hosted_service,
            "model": hosted_model,
            "style_preset": hosted_style,
            "output_format": str(provider_cfg.get("output_format") or "png"),
            "allow_auto_fallback": bool(provider_cfg.get("allow_auto_fallback", True)),
        },
        "settings": {
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "width": settings_obj.width,
            "height": settings_obj.height,
            "interpolation_engine": settings_obj.interpolation_engine,
            "resume_existing_frames": settings_obj.resume_existing_frames,
            "render_mode": "hosted",
            "render_tier": settings_obj.render_tier,
        },
    }


TENSORRT_VIDEO_MODEL_ID = "local_sd15_tensorrt_bundle"
INTERNAL_SVD_VIDEO_MODEL_ID = "hf_svd_xt_1_1_internal"
INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID = "hf_animatediff_motion_adapter_v15_2_internal"
INTERNAL_VIDEO_MODEL_IDS = (
    INTERNAL_SVD_VIDEO_MODEL_ID,
    INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID,
    HUNYUAN_MODEL_ID,
)
INTERNAL_VIDEO_MODEL_ENGINES = {
    INTERNAL_SVD_VIDEO_MODEL_ID: "svd",
    INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID: "animatediff",
    HUNYUAN_MODEL_ID: "hunyuan_video15",
}


def _tensorrt_sd15_bundle_available() -> bool:
    return bool(_resolve_installed_model_path(TENSORRT_VIDEO_MODEL_ID, materialize_remote=False))


def _installed_internal_video_models_status() -> dict[str, bool]:
    return {model_id: bool(models.installed_path(model_id)) for model_id in INTERNAL_VIDEO_MODEL_IDS}


def _video_model_engine_from_id(model_id: str | None) -> str:
    return INTERNAL_VIDEO_MODEL_ENGINES.get(str(model_id or "").strip(), "svd")


def _resolve_internal_video_model_selection(
    payload: dict[str, Any],
    *,
    base_model_family: str,
) -> tuple[str, str, Path]:
    requested_engine = str(payload.get("video_model_engine") or "auto").strip().lower()
    requested_model_id = str(payload.get("video_model_id") or "").strip()
    installed_svd = models.installed_path(INTERNAL_SVD_VIDEO_MODEL_ID)
    installed_ad = models.installed_path(INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID)
    installed_hunyuan = models.installed_path(HUNYUAN_MODEL_ID)

    if requested_engine not in {"auto", "svd", "animatediff", "hunyuan_video15"}:
        raise UserFacingError(
            "Selected internal video adapter engine is not supported",
            hint="Choose Auto installed, SVD image-to-video, AnimateDiff SD1.5, or HunyuanVideo-1.5.",
            code="INTERNAL_VIDEO_MODEL_ENGINE_UNKNOWN",
            status_code=400,
        )

    if requested_model_id:
        expected_engine = INTERNAL_VIDEO_MODEL_ENGINES.get(requested_model_id)
        if not expected_engine:
            raise UserFacingError(
                "Selected model is not a supported internal video model",
                hint="Open Models and select Stable Video Diffusion XT 1.1, AnimateDiff Motion Adapter, or HunyuanVideo-1.5.",
                code="INTERNAL_VIDEO_MODEL_UNSUPPORTED",
                status_code=400,
            )
        if requested_engine != "auto" and requested_engine != expected_engine:
            expected_label = {
                "svd": "Stable Video Diffusion XT 1.1",
                "animatediff": "AnimateDiff Motion Adapter",
                "hunyuan_video15": "HunyuanVideo-1.5",
            }[requested_engine]
            raise UserFacingError(
                "Selected internal video model does not match the adapter engine",
                hint=(
                    f"{requested_engine.upper()} requires {expected_label}. Choose the matching model, "
                    "or switch the adapter engine to match the selected model."
                ),
                code="INTERNAL_VIDEO_MODEL_ENGINE_MODEL_MISMATCH",
                status_code=400,
            )
        path = models.installed_path(requested_model_id)
        if not path:
            raise UserFacingError(
                "Selected internal video model is not installed",
                hint="Open Models and install the selected internal video model, then retry.",
                code="INTERNAL_VIDEO_MODEL_NOT_INSTALLED",
                status_code=400,
            )
        engine = expected_engine
    elif requested_engine == "animatediff":
        path = installed_ad
        engine = "animatediff"
        requested_model_id = INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID
    elif requested_engine == "svd":
        path = installed_svd
        engine = "svd"
        requested_model_id = INTERNAL_SVD_VIDEO_MODEL_ID
    elif requested_engine == "hunyuan_video15":
        path = installed_hunyuan
        engine = "hunyuan_video15"
        requested_model_id = HUNYUAN_MODEL_ID
    elif installed_svd:
        path = installed_svd
        engine = "svd"
        requested_model_id = INTERNAL_SVD_VIDEO_MODEL_ID
    elif installed_ad:
        path = installed_ad
        engine = "animatediff"
        requested_model_id = INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID
    else:
        path = None
        engine = "svd"
        requested_model_id = INTERNAL_SVD_VIDEO_MODEL_ID

    if not path:
        raise UserFacingError(
            "The selected internal video model is not installed",
            hint="Open Models and install Stable Video Diffusion XT 1.1, AnimateDiff Motion Adapter, or the qualified HunyuanVideo-1.5 snapshot.",
            code="INTERNAL_VIDEO_MODEL_NOT_INSTALLED",
            status_code=400,
        )

    resolved_path = Path(path)
    internal_video_models.validate_video_model_layout(engine, resolved_path)

    if engine == "hunyuan_video15":
        # The Workspace readiness card is diagnostic, but this second
        # resolution is authoritative at queue time. It prevents a manually
        # crafted render request from loading a discovery-only Hunyuan snapshot
        # before the adapter and hardware profile have passed qualification.
        installed_for_readiness = {
            HUNYUAN_MODEL_ID: bool(installed_hunyuan),
            STANDARD_DIRECTOR_MODEL_ID: bool(models.installed_path(STANDARD_DIRECTOR_MODEL_ID)),
            HIGH_TIER_DIRECTOR_MODEL_ID: bool(models.installed_path(HIGH_TIER_DIRECTOR_MODEL_ID)),
            LTX_MODEL_ID: bool(models.installed_path(LTX_MODEL_ID)),
        }
        try:
            readiness = resolve_director_readiness(
                _hardware_profile(),
                mode="automatic",
                engine=engine,
                installed_models=installed_for_readiness,
                allow_external=False,
            )
        except ValueError as exc:
            raise UserFacingError(
                "HunyuanVideo-1.5 renderer admission could not be resolved",
                hint="Refresh Workspace readiness and retry after the hardware probe completes.",
                code="DIRECTOR_RENDERER_READINESS_INVALID",
                status_code=422,
            ) from exc
        if not readiness.renderer.ready:
            blockers = list(readiness.blockers) or [readiness.renderer.reason]
            actions = list(readiness.actions)
            raise UserFacingError(
                "HunyuanVideo-1.5 is not admitted for local rendering",
                hint=" ".join([*blockers, *actions]),
                code="DIRECTOR_RENDERER_NOT_READY",
                status_code=422,
            )

    if engine == "animatediff" and str(base_model_family or "").lower() != "sd15":
        raise UserFacingError(
            "AnimateDiff internal motion needs an SD 1.5 internal base model",
            hint="Switch Internal model to Stable Diffusion v1.5, or use SVD internal video model with SDXL/SD3 keyframes.",
            code="INTERNAL_VIDEO_MODEL_BASE_UNSUPPORTED",
            status_code=400,
        )

    return engine, requested_model_id, resolved_path


def _apply_internal_video_model_memory_safety(settings_obj: InternalVideoSettings, hw: dict[str, Any]) -> InternalVideoSettings:
    if str(settings_obj.temporal_mode or "").lower() != "video_model":
        return settings_obj
    engine = str(settings_obj.video_model_engine or "").lower()
    if engine == "auto":
        engine = _video_model_engine_from_id(settings_obj.video_model_id)
    backend = str(settings_obj.device_preference or "").strip().lower()
    if backend in {"", "auto"}:
        backend = str(hw.get("backend") or hw.get("device") or "cpu").lower()
    if engine not in {"animatediff", "svd", "hunyuan_video15"} or backend != "cuda":
        return settings_obj
    vram_gb = float(hw.get("vram_gb") or hw.get("cuda_vram_gb") or 0.0)
    updates: dict[str, Any] = {}
    if vram_gb and vram_gb <= 6.5:
        updates["video_model_cpu_offload"] = True
        if engine == "svd":
            updates["video_model_max_frames_per_scene"] = min(int(settings_obj.video_model_max_frames_per_scene or 25), 8)
            updates["video_model_decode_chunk_size"] = min(int(settings_obj.video_model_decode_chunk_size or 8), 1)
        elif engine == "animatediff":
            updates["video_model_max_frames_per_scene"] = min(int(settings_obj.video_model_max_frames_per_scene or 25), 12)
            updates["video_model_decode_chunk_size"] = min(int(settings_obj.video_model_decode_chunk_size or 8), 2)
            updates["temporal_steps"] = min(int(settings_obj.temporal_steps or 18), 8)
        else:
            updates["video_model_max_frames_per_scene"] = min(int(settings_obj.video_model_max_frames_per_scene or 25), 8)
            updates["video_model_decode_chunk_size"] = 1
    elif vram_gb and vram_gb <= 8.5:
        updates["video_model_cpu_offload"] = True
        if engine == "svd":
            updates["video_model_max_frames_per_scene"] = min(int(settings_obj.video_model_max_frames_per_scene or 25), 12)
            updates["video_model_decode_chunk_size"] = min(int(settings_obj.video_model_decode_chunk_size or 8), 2)
        elif engine == "animatediff":
            updates["video_model_max_frames_per_scene"] = min(int(settings_obj.video_model_max_frames_per_scene or 25), 16)
            updates["video_model_decode_chunk_size"] = min(int(settings_obj.video_model_decode_chunk_size or 8), 4)
            if settings_obj.temporal_steps is None or int(settings_obj.temporal_steps) > 10:
                updates["temporal_steps"] = 10
        else:
            updates["video_model_max_frames_per_scene"] = min(int(settings_obj.video_model_max_frames_per_scene or 25), 12)
            updates["video_model_decode_chunk_size"] = min(int(settings_obj.video_model_decode_chunk_size or 8), 2)
    return replace(settings_obj, **updates) if updates else settings_obj


def _internal_video_model_memory_warnings(settings_obj: InternalVideoSettings, hw: dict[str, Any]) -> list[str]:
    if str(settings_obj.temporal_mode or "").lower() != "video_model":
        return []
    engine = str(settings_obj.video_model_engine or "").lower()
    if engine == "auto":
        engine = _video_model_engine_from_id(settings_obj.video_model_id)
    if engine not in {"animatediff", "svd", "hunyuan_video15"}:
        return []
    backend = str(settings_obj.device_preference or "").strip().lower()
    if backend in {"", "auto"}:
        backend = str(hw.get("backend") or hw.get("device") or "cpu").lower()
    if backend != "cuda":
        return []
    vram_gb = float(hw.get("vram_gb") or hw.get("cuda_vram_gb") or 0.0)
    if vram_gb and vram_gb <= 6.5:
        if engine == "svd":
            return [
                "6 GB CUDA SVD safety is active: Studio releases still-image pipelines before motion, enables CPU offload, caps SVD adapter frames to 8, uses decode chunks of 1, and renders the SVD adapter at a lower working canvas before resizing to the final video size. Inference steps are preserved because they affect render time rather than peak model allocation."
            ]
        if engine == "animatediff":
            return [
            "6 GB CUDA AnimateDiff safety is active: Studio releases still-image pipelines before motion, enables CPU offload, caps adapter frames to 12, uses small decode chunks, and renders the adapter at a lower working canvas before resizing to the final video size. Inference steps are preserved because they affect render time rather than peak model allocation."
            ]
        return [
            "6 GB CUDA HunyuanVideo-1.5 safety targets are active: Studio would use CPU offload, cap each temporal shot to 8 frames, decode in a single-frame chunk, and render a conservative adapter canvas. This profile remains unverified until fresh temporal output evidence passes."
        ]
    if vram_gb and vram_gb <= 8.5:
        if engine == "svd":
            return [
                "8 GB CUDA SVD safety is active: Studio enables CPU offload, caps SVD adapter frames to 12, preserves inference steps, and uses smaller decode chunks."
            ]
        if engine == "animatediff":
            return [
                "8 GB CUDA AnimateDiff safety is active: Studio enables CPU offload, caps adapter frames to 16, preserves inference steps, and uses smaller decode chunks."
            ]
        return [
            "8 GB CUDA HunyuanVideo-1.5 safety targets are active: Studio would use CPU offload, cap each temporal shot to 12 frames, and use small decode chunks. This profile remains unverified until fresh temporal output evidence passes."
        ]
    return []


def _studio_native_resource_policy(
    *,
    settings_obj: InternalVideoSettings,
    hw: dict[str, Any],
    model_family: str,
) -> dict[str, Any]:
    """Forge-inspired resource policy implemented inside Studio, with no Forge API dependency."""
    requested = str(settings_obj.device_preference or "auto").strip().lower()
    backend = requested if requested in {"cuda", "cpu", "mps", "directml"} else str(hw.get("backend") or "cpu").lower()
    vram_gb = float(hw.get("vram_gb") or hw.get("cuda_vram_gb") or 0.0)
    family = str(model_family or "").lower()
    video_model = str(settings_obj.temporal_mode or "").lower() == "video_model"
    notes: list[str] = []

    if backend == "cuda":
        dtype = "float16"
        if vram_gb and vram_gb <= 6.5:
            offload = "aggressive_cpu_offload"
            attention = "native_sdpa_with_vae_slicing_and_small_decode_chunks"
            notes.append("6 GB CUDA policy: prefer SD1.5/SDXL anchors, CPU offload, sliced decode, and short video-model shots.")
        elif vram_gb and vram_gb <= 8.5:
            offload = "balanced_cpu_offload"
            attention = "native_sdpa_with_vae_slicing"
            notes.append("8 GB CUDA policy: keep video shots short, use CPU offload for SVD/AnimateDiff, and avoid stacking many adapters.")
        else:
            offload = "gpu_preferred"
            attention = "native_sdpa"
    elif backend == "directml":
        dtype = "float16"
        offload = "directml_safe"
        attention = "single_adapter"
        notes.append("DirectML policy: prefer SDXL/SD1.5 still or frame motion paths; keep video-model adapters on CUDA when available.")
    else:
        dtype = "float32" if backend == "cpu" else "auto"
        offload = "cpu_or_unified_memory"
        attention = "low_parallelism"
        notes.append("Non-CUDA policy: use lower FPS render, smaller frames, and real keyframe paths for long clips.")

    lora_count = len(tuple(settings_obj.loras or ()))
    if lora_count > 2 and vram_gb and vram_gb <= 8.5:
        notes.append("Multiple LoRAs on low VRAM can destabilize long renders; Studio keeps them as prompt/keyframe adapters only.")
    if family in {"sd3", "sd35"} and backend == "cuda" and vram_gb and vram_gb < SD35_INTERNAL_MIN_CUDA_VRAM_GB:
        notes.append("SD3.5 is too large for this CUDA video path; Studio will prefer SDXL/SD1.5 or hosted fallback.")
    if video_model and backend == "cuda" and vram_gb and vram_gb <= 8.5:
        notes.append("Video-model memory safety is active before motion generation so still-image pipelines can be released first.")

    return {
        "source": "studio_native_forge_equivalent",
        "backend": backend,
        "model_family": family or "unknown",
        "vram_gb": vram_gb,
        "precision_policy": dtype,
        "offload_policy": offload,
        "attention_policy": attention,
        "adapter_policy": {
            "loras": {"count": lora_count, "policy": "keyframe_safe_merge" if lora_count else "none"},
            "controlnet": {"policy": "still_image_workflow_only"},
            "ip_adapter": {"policy": "not_enabled_for_internal_video"},
        },
        "notes": notes,
    }


def _internal_render_prompt_preview(
    *,
    variant: dict[str, Any],
    scenes: list[dict[str, Any]],
    timeline: dict[str, Any] | None,
    settings_obj: InternalVideoSettings,
    limit: int = 6,
) -> list[dict[str, Any]]:
    fps = max(1, int(settings_obj.fps_output or 24))
    ctx = build_deforum_render_context(
        scenes=scenes,
        timeline=timeline,
        variant=variant,
        fps=fps,
        default_negative_prompt=str(settings_obj.negative_prompt or ""),
        overrides=settings_obj.deforum_overrides,
    )
    valid_scenes = [scene for scene in scenes if isinstance(scene, dict)]
    sample_times: list[float] = []
    for scene in valid_scenes:
        try:
            sample_times.append(max(0.0, float(scene.get("start_s", 0.0) or 0.0)))
        except Exception:
            continue
    if not sample_times:
        sample_times = [0.0]
    sample_times = sorted(dict.fromkeys(round(t, 3) for t in sample_times))[: max(1, int(limit))]

    preview: list[dict[str, Any]] = []
    last_prompt = ""
    for time_s in sample_times:
        frame = int(round(float(time_s) * float(fps)))
        prompt = resolve_prompt_frame(ctx.prompts, frame, default="")
        if not str(prompt or "").strip():
            scene = next(
                (
                    candidate
                    for candidate in valid_scenes
                    if float(candidate.get("start_s", 0.0) or 0.0) <= time_s < float(candidate.get("end_s", time_s + 1.0) or time_s + 1.0)
                ),
                valid_scenes[0] if valid_scenes else {},
            )
            prompt = render_prompt_from_scene(scene, fallback=DEFAULT_RENDER_PROMPT)
        prompt = " ".join(str(prompt or DEFAULT_RENDER_PROMPT).split())
        if prompt == last_prompt:
            continue
        last_prompt = prompt
        preview.append(
            {
                "time_s": float(time_s),
                "frame": frame,
                "prompt": prompt[:420],
            }
        )
    return preview


def _payload_requests_tensorrt_video(payload: dict[str, Any]) -> bool:
    requested = str(payload.get("model_id") or "").strip()
    return bool(requested and requested not in {"auto", "auto_internal"} and "tensorrt" in requested.lower())


def _tensorrt_requested_model_warning(payload: dict[str, Any]) -> str | None:
    requested = str(payload.get("model_id") or "").strip()
    if _payload_requests_tensorrt_video(payload) and requested != TENSORRT_VIDEO_MODEL_ID:
        return (
            f"Requested TensorRT bundle {requested} is discovery-only for Studio video. "
            f"Internal TensorRT video currently supports only {TENSORRT_VIDEO_MODEL_ID}, "
            "which renders SD1.5 keyframes and assembles/interpolates them locally."
        )
    return None


def _tensorrt_model_id_from_payload(payload: dict[str, Any]) -> str:
    requested = str(payload.get("model_id") or "").strip()
    if requested == TENSORRT_VIDEO_MODEL_ID:
        return requested
    return TENSORRT_VIDEO_MODEL_ID


def _tensorrt_render_preflight_data(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    proj = store.get(project_id)
    if not proj:
        raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise UserFacingError("No plan generated", hint="Run Analyze + Plan first, then retry.")

    variant_index = int(payload.get("variant_index", 0))
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise UserFacingError("variant_index out of range", hint="Pick a valid variant index.")

    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise UserFacingError("Selected variant has no scenes", hint="Re-run Plan with at least 1 scene.")

    hw = _hardware_profile()
    if str(hw.get("backend") or "").lower() != "cuda":
        raise UserFacingError(
            "TensorRT video rendering requires CUDA.",
            hint="Use an NVIDIA CUDA backend, or switch Internal renderer mode to an installed local diffusion model.",
            code="TRT_CUDA_UNAVAILABLE",
            status_code=400,
        )

    model_id = _tensorrt_model_id_from_payload(payload)
    model_path = models.installed_path(model_id)
    if not model_path:
        raise UserFacingError(
            "Local TensorRT SD1.5 bundle is not installed.",
            hint=(
                "Open Models and verify the canonical TensorRT bundle. Advanced compatibility "
                "setups may explicitly set EDMG_TENSORRT_SD15_BUNDLE to a complete bundle with "
                "verified engine, ONNX, base-model, and compiled-profile metadata."
            ),
            code="TRT_MODEL_NOT_FOUND",
            status_code=400,
        )

    trt_payload = dict(payload)
    trt_payload.update({"width": 512, "height": 512})
    settings_obj = _internal_settings_from_payload(
        trt_payload,
        model_id=model_id,
        render_tier=str(payload.get("render_tier") or "auto"),
        device_preference="cuda",
        temporal_mode="keyframes",
    )
    duration_sources = _project_duration_sources(proj, variant, scenes)
    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    fps_render = max(1, int(settings_obj.fps_render))
    total_frames = int(math.ceil(duration_s * fps_render))
    keyframes = max(1, len(_scene_keyframe_times(scenes, settings_obj.keyframe_interval_s)))
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or settings_obj.render_tier or "auto"), duration_s=duration_s)
    tier_plan["chunk_plan"] = _build_render_chunk_plan(hw, applied_tier=str(tier_plan.get("applied_tier") or "draft"), duration_s=duration_s, total_frames=total_frames, fps_render=fps_render, render_mode="tensorrt")
    timeline = proj.meta.get("timeline") or None
    cache = describe_internal_render_cache(
        project_dir=store.project_dir(project_id),
        variant_index=variant_index,
        variant=variant,
        scenes=scenes,
        timeline=timeline if isinstance(timeline, dict) else None,
        model_dir=Path(model_path),
        settings=settings_obj,
        total_frames=total_frames,
    )
    warnings = [
        "TensorRT video mode is SD1.5 keyframe generation plus local assembly/interpolation, not an SVD/AnimateDiff motion model.",
        "This path uses the local SD1.5 TensorRT bundle only and is constrained to the compiled 512x512 batch-1 profile.",
    ]
    requested_warning = _tensorrt_requested_model_warning(payload)
    if requested_warning:
        warnings.append(requested_warning)
    duration_warning = _duration_mismatch_warning(duration_sources)
    if duration_warning:
        warnings.append(duration_warning)
    if settings_obj.fps_render > 4:
        warnings.append("High FPS render values will require many TensorRT keyframes; use 1-2 FPS render for first passes.")
    for note in list(tier_plan.get("notes") or []):
        if note not in warnings:
            warnings.append(str(note))
    return {
        "ok": True,
        "mode": "tensorrt",
        "variant_index": variant_index,
        "model_id": model_id,
        "model_path": str(model_path),
        "duration_s": duration_s,
        "duration_sources": duration_sources,
        "estimated_frames": total_frames,
        "estimated_keyframes": keyframes,
        "device": "cuda+tensorrt",
        "hardware": hw,
        "tier_plan": tier_plan,
        "resume_existing_frames": False,
        "warnings": warnings,
        "cache": cache,
        "installed_internal_models": _installed_internal_models_status(),
        "installed_tensorrt_models": {model_id: True},
        "settings": {
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "width": 512,
            "height": 512,
            "temporal_mode": "keyframes",
            "interpolation_engine": settings_obj.interpolation_engine,
            "render_mode": "tensorrt",
            "render_tier": settings_obj.render_tier,
            "device_preference": "cuda",
            "profile_width": 512,
            "profile_height": 512,
            "max_batch": 1,
        },
    }


def _internal_render_preflight_data(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    requested_mode = str(payload.get("render_mode") or "auto").strip().lower()
    if requested_mode == "proxy":
        raise UserFacingError(
            "Proxy rendering is no longer available for new renders.",
            hint="Install a supported local model or configure a hosted provider, then retry.",
            code="PROXY_RENDER_DISABLED",
            status_code=400,
        )
    if requested_mode == "hosted":
        return _hosted_render_preflight_data(project_id, payload)
    if requested_mode == "tensorrt" or (requested_mode in {"auto", "diffusion"} and _payload_requests_tensorrt_video(payload)):
        return _tensorrt_render_preflight_data(project_id, payload)

    try:
        (
            proj,
            variant,
            model_id,
            model_path,
            tensorrt_keyframe_bundle_path,
            settings_obj,
        ) = _resolve_internal_render_request(project_id, payload)
    except UserFacingError as e:
        if e.code in {"MODEL_NOT_INSTALLED", "DIRECTML_MODEL_UNSUPPORTED", "MODEL_UNSUPPORTED_FOR_HARDWARE"} and _hosted_stability_ready(payload):
            return _hosted_render_preflight_data(project_id, payload, reason=e.message)
        raise
    model_family = _internal_model_family_for_request(model_id, model_path)

    scenes = variant.get("scenes") or []
    used_fallback_plan = str(variant.get("_fallback_plan_source") or "") == "creative_direction_fallback"
    duration_sources = _project_duration_sources(proj, variant, scenes)
    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    fps_render = max(1, int(settings_obj.fps_render))
    total_frames = int(math.ceil(duration_s * fps_render))
    keyframes = max(1, len(_scene_keyframe_times(scenes, settings_obj.keyframe_interval_s)))
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or settings_obj.render_tier or "auto"), duration_s=duration_s)
    tier_plan["chunk_plan"] = _build_render_chunk_plan(hw, applied_tier=str(tier_plan.get("applied_tier") or "draft"), duration_s=duration_s, total_frames=total_frames, fps_render=fps_render, render_mode="diffusion")
    warnings: list[str] = []
    parseq_motion = payload.get("_parseq_motion") if isinstance(payload.get("_parseq_motion"), dict) else None
    if parseq_motion:
        warnings.append(
            f"Parseq-style motion sequencer is active: {int(parseq_motion.get('schedules') or 0)} schedule(s), "
            f"{int(parseq_motion.get('keyframes') or 0)} keyframe row(s), {int(parseq_motion.get('prompts') or 0)} prompt override(s)."
        )
    resource_policy = _studio_native_resource_policy(settings_obj=settings_obj, hw=hw, model_family=model_family)
    for note in list(resource_policy.get("notes") or []):
        warnings.append(str(note))
    if used_fallback_plan:
        warnings.append(
            "No saved plan found; using the generated creative-direction fallback scene pack. "
            "Run Analyze + Plan for transcript/audio-accurate scenes."
        )
    if str(hw.get("backend") or "").lower() == "cpu":
        warnings.append("No GPU acceleration detected; internal diffusion will run on CPU and may be slow on longer renders.")
    elif str(hw.get("backend") or "").lower() == "mps":
        warnings.append("Apple Silicon acceleration detected; balanced settings are recommended for sustained laptop rendering.")
    elif str(hw.get("backend") or "").lower() == "directml":
        warnings.append("DirectML acceleration detected; SDXL and SD 1.5 are the supported AMD / Windows GPU paths.")
    if str(hw.get("backend") or "").lower() == "directml" and str(settings_obj.device_preference or "auto") == "cpu":
        warnings.append("The selected internal model is not DirectML-compatible, so this render will fall back to CPU.")
    if total_frames > 900:
        warnings.append("This render is long for the current FPS render setting; consider lowering FPS render or increasing keyframe interval.")
    if settings_obj.temporal_mode == "frame_img2img" and total_frames > 600:
        warnings.append("Frame img2img temporal mode is the most expensive mode for long clips.")
    if settings_obj.temporal_mode == "video_model":
        warnings.append(
            f"Internal video-model motion is enabled via {settings_obj.video_model_engine}; "
            "this is the path for subject/object motion and is heavier than keyframe assembly."
        )
        video_model_preflight = describe_internal_video_model_preflight(
            scenes=scenes,
            timeline=(proj.meta.get("timeline") if isinstance(proj.meta.get("timeline"), dict) else None),
            settings=settings_obj,
            duration_s=duration_s,
            total_frames=total_frames,
            hardware=hw,
        )
        if settings_obj.video_model_engine == "svd":
            warnings.append("SVD animates from each generated keyframe; it is best for short subject, fabric, camera, and transition motion.")
        if settings_obj.video_model_engine == "animatediff":
            warnings.append("AnimateDiff uses an SD1.5 motion adapter; keep the internal base model on SD1.5 for this mode.")
        if normalize_video_model_keyframe_renderer(settings_obj.video_model_keyframe_renderer) == "tensorrt_sd15":
            warnings.append(
                "TensorRT SD1.5 storyboard anchors are enabled: Studio generates fast SD1.5 keyframes first, then SVD can animate those anchors directly. AnimateDiff still uses its SD1.5 Diffusers base and only uses these anchors for start/end/loop blending."
            )
            requested_anchor_model = str(payload.get("video_model_keyframe_model_id") or "").strip()
            if requested_anchor_model and requested_anchor_model != TENSORRT_VIDEO_MODEL_ID:
                warnings.append(
                    f"Requested TensorRT anchor bundle {requested_anchor_model} is discovery-only. "
                    f"Studio mapped the request to the executable {TENSORRT_VIDEO_MODEL_ID} bundle."
                )
        for warning in list(video_model_preflight.get("warnings") or []):
            warnings.append(str(warning))
        for warning in _internal_video_model_memory_warnings(settings_obj, hw):
            warnings.append(warning)
        if normalize_internal_motion_strategy(settings_obj.motion_strategy) == "storyboard_full_motion":
            storyboard_plan = video_model_preflight.get("storyboard_motion_plan") if isinstance(video_model_preflight, dict) else None
            shot_count = int((storyboard_plan or {}).get("shot_count") or 0)
            warnings.append(
                "Storyboard full motion is active: Studio generates scene keyframe anchors from the plan/transcript prompts, "
                "then renders short video-model motion shots without requiring a source image."
            )
            if shot_count:
                warnings.append(f"Storyboard full motion will render {shot_count} short motion shot(s) before stitching.")
    else:
        video_model_preflight = None
    if settings_obj.fps_render > settings_obj.fps_output:
        warnings.append("FPS render is higher than FPS output; you may be spending extra time on frames that will be blended down.")
    duration_warning = _duration_mismatch_warning(duration_sources)
    if duration_warning:
        warnings.append(duration_warning)
    for note in list(tier_plan.get("notes") or []):
        if note not in warnings:
            warnings.append(str(note))
    timeline = proj.meta.get("timeline") or None
    cache = describe_internal_render_cache(
        project_dir=store.project_dir(project_id),
        variant_index=int(payload.get("variant_index", 0)),
        variant=variant,
        scenes=scenes,
        timeline=timeline if isinstance(timeline, dict) else None,
        model_dir=model_path,
        settings=settings_obj,
        total_frames=total_frames,
    )
    prompt_preview = _internal_render_prompt_preview(
        variant=variant,
        scenes=scenes,
        timeline=timeline if isinstance(timeline, dict) else None,
        settings_obj=settings_obj,
    )
    installed_internal = _installed_internal_models_status()
    return {
        "ok": True,
        "mode": "diffusion",
        "plan_source": "creative_direction_fallback" if used_fallback_plan else "last_plan",
        "variant_index": int(payload.get("variant_index", 0)),
        "model_id": model_id,
        "model_path": str(model_path),
        "tensorrt_keyframe_bundle_path": (
            str(tensorrt_keyframe_bundle_path)
            if tensorrt_keyframe_bundle_path is not None
            else None
        ),
        "duration_s": duration_s,
        "duration_sources": duration_sources,
        "estimated_frames": total_frames,
        "estimated_keyframes": keyframes,
        "device": str(tier_plan.get("device_preference") or hw.get("backend") or "cpu"),
        "hardware": hw,
        "tier_plan": tier_plan,
        "resume_existing_frames": bool(settings_obj.resume_existing_frames),
        "prompt_preview": prompt_preview,
        "warnings": warnings,
        "parseq_motion": parseq_motion,
        "render_recipe_graph": payload.get("_render_recipe_graph") or parseq_adapter.build_render_recipe_graph(
            manifest=payload.get("parseq_manifest") if isinstance(payload.get("parseq_manifest"), dict) else None,
            internal_request=payload,
        ),
        "resource_policy": resource_policy,
        "cache": cache,
        "installed_internal_models": installed_internal,
        "installed_internal_video_models": _installed_internal_video_models_status(),
        "internal_video_model_dependencies": internal_video_models.dependency_status(),
        "internal_video_model_preflight": video_model_preflight,
        "settings": {
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "width": settings_obj.width,
            "height": settings_obj.height,
            "keyframe_interval_s": settings_obj.keyframe_interval_s,
            "keyframe_continuity_mode": normalize_keyframe_continuity_mode(
                settings_obj.keyframe_continuity_mode
            ),
            "temporal_mode": settings_obj.temporal_mode,
            "video_model_engine": settings_obj.video_model_engine,
            "video_model_id": settings_obj.video_model_id,
            "video_model_path": settings_obj.video_model_path,
            "video_model_max_frames_per_scene": settings_obj.video_model_max_frames_per_scene,
            "video_model_decode_chunk_size": settings_obj.video_model_decode_chunk_size,
            "video_model_dtype": settings_obj.video_model_dtype,
            "video_model_cpu_offload": settings_obj.video_model_cpu_offload,
            "video_model_motion_score_mode": settings_obj.video_model_motion_score_mode,
            "video_model_manual_motion_score": settings_obj.video_model_manual_motion_score,
            "video_model_anchor_mode": settings_obj.video_model_anchor_mode,
            "video_model_prompt_refine": settings_obj.video_model_prompt_refine,
            "video_model_scene_motion": normalize_video_model_scene_motion(settings_obj.video_model_scene_motion),
            "video_model_apply_timeline_camera": settings_obj.video_model_apply_timeline_camera,
            "video_model_keyframe_renderer": normalize_video_model_keyframe_renderer(settings_obj.video_model_keyframe_renderer),
            "video_model_keyframe_model_id": settings_obj.video_model_keyframe_model_id,
            "video_model_motion_score_schedule": settings_obj.video_model_motion_score_schedule,
            "video_model_noise_aug_schedule": settings_obj.video_model_noise_aug_schedule,
            "anchor_strength_schedule": settings_obj.anchor_strength_schedule,
            "motion_strategy": normalize_internal_motion_strategy(settings_obj.motion_strategy),
            "storyboard_shot_max_s": settings_obj.storyboard_shot_max_s,
            "temporal_steps": settings_obj.temporal_steps,
            "interpolation_engine": settings_obj.interpolation_engine,
            "render_mode": "diffusion",
            "render_tier": settings_obj.render_tier,
            "device_preference": settings_obj.device_preference,
        },
    }


@app.post("/v1/projects/{project_id}/render/internal/preflight")
def render_internal_preflight(project_id: str, req: InternalVideoRenderRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    payload, _parseq = _apply_active_parseq_motion(proj, _request_payload(req))
    return _public_render_preflight(_internal_render_preflight_data(project_id, payload))

@app.post("/v1/projects/{project_id}/render/comfyui/motion_scenes")
def render_motion_scenes(project_id: str, req: RenderMotionRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[req.variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes")

    created = []
    resolved_loras = _normalize_render_loras(getattr(req, "loras", []))
    vae_name = _resolve_optional_comfy_asset_name(req.vae, folder="vae", allowed_kinds={"vae"})
    motion_selection = _resolve_comfy_motion_selection(
        model_id=req.model_id,
        checkpoint=req.checkpoint,
        svd_model_id=req.svd_model_id,
        svd_checkpoint=req.svd_checkpoint,
    )
    checkpoint = str(motion_selection.get("checkpoint") or settings.comfyui_checkpoint)
    svd_checkpoint = str(motion_selection.get("svd_checkpoint") or req.svd_checkpoint or "svd_xt.safetensors")
    model_tag = _safe_name_tag(req.model_id or checkpoint)
    svd_tag = _safe_name_tag(req.svd_model_id or svd_checkpoint or "svd")
    for idx, sc in enumerate(scenes):
        start = float(sc.get("start_s", idx * 5))
        end = float(sc.get("end_s", start + 5))
        duration_s = max(0.5, end - start)
        frames = max(1, int(round(duration_s * req.fps)))
        frames = min(frames, int(req.max_frames_per_scene))

        # Practical caps for SVD (most setups use 14 or 25 frames)
        if req.engine == "svd":
            frames = min(frames, 25)

        seed = int(req.seed) + idx if req.seed is not None else _stable_seed(project_id, req.variant_index, idx)
        pdir = store.project_dir(project_id)
        frames_dir = pdir / "outputs" / "frames" / f"v{req.variant_index:02d}" / f"scene{idx:03d}" / f"{req.engine}_{model_tag}_{svd_tag}_seed{seed}"
        out_clip = pdir / "outputs" / "clips" / f"v{req.variant_index:02d}_scene{idx:03d}_{req.engine}_{model_tag}_{svd_tag}_seed{seed}.mp4"
        p = {
            "variant_index": req.variant_index,
            "scene_index": idx,
            "model_id": req.model_id,
            "svd_model_id": req.svd_model_id,
            "prompt": sc.get("prompt") or "",
            "negative_prompt": req.negative_prompt,
            "seed": seed,
            "width": req.width,
            "height": req.height,
            "steps": req.steps,
            "cfg": req.cfg,
            "sampler": req.sampler,
            "checkpoint": checkpoint,
            "fps": req.fps,
            "frames": frames,
            "engine": req.engine,
            "frames_dir": str(frames_dir),
            "out_clip": str(out_clip),
            "loras": resolved_loras,
            "vae": vae_name,
            "motion_model_name": req.motion_model_name,
            "context_length": req.context_length,
            "context_overlap": req.context_overlap,
            "beta_schedule": req.beta_schedule,
            "svd_checkpoint": svd_checkpoint,
            "svd_num_steps": req.svd_num_steps,
            "svd_motion_bucket_id": req.svd_motion_bucket_id,
            "svd_fps_id": req.svd_fps_id,
            "svd_cond_aug": req.svd_cond_aug,
            "svd_decoding_t": req.svd_decoding_t,
            "device": req.device,
        }
        job = jobs.create(project_id, "comfyui_motion_scene", p)
        created.append(job.__dict__)

    proj.meta.setdefault("jobs", []).extend(created)
    store.save(proj)

    return {"ok": True, "enqueued": len(created), "jobs": created}


def _preset_defaults(preset: str) -> dict[str, Any]:
    p = (preset or "balanced").lower().strip()
    if p not in ("fast", "balanced", "quality", "ultra"):
        p = "balanced"

    if p == "fast":
        return {"stills": {"width": 640, "height": 360, "steps": 12, "cfg": 6.0, "sampler": "euler"}, "motion": {"fps": 10, "max_frames": 36}}
    if p == "quality":
        return {"stills": {"width": 896, "height": 504, "steps": 26, "cfg": 7.0, "sampler": "euler"}, "motion": {"fps": 12, "max_frames": 60}}
    if p == "ultra":
        return {"stills": {"width": 1024, "height": 576, "steps": 30, "cfg": 7.5, "sampler": "euler"}, "motion": {"fps": 12, "max_frames": 72}}
    # balanced
    return {"stills": {"width": 768, "height": 432, "steps": 20, "cfg": 6.5, "sampler": "euler"}, "motion": {"fps": 12, "max_frames": 48}}


def _internal_diffusion_runtime_status() -> dict[str, Any]:
    try:
        import diffusers  # type: ignore  # noqa: F401
        import torch  # type: ignore  # noqa: F401
        diagnostics = ["internal_runtime=ready"]
        directml = _directml_runtime_status()
        if directml.get("runtime_ready"):
            diagnostics.append("directml_runtime=ready")
        return {"ok": True, "diagnostics": diagnostics}
    except Exception:
        logger.exception("Internal diffusion runtime check failed")
        return {
            "ok": False,
            "error": "Internal diffusion runtime is unavailable",
            "diagnostics": ["internal_runtime=missing"],
        }


def _recommend_local_fallback(project_id: str, preset: str, *, reason: str) -> dict[str, Any]:
    hw = _hardware_profile()
    provider_status = _render_provider_status(hw)
    directml_status = dict(provider_status.get("directml") or {})
    if str(hw.get("backend") or "").lower() == "directml" and not bool(directml_status.get("enabled", True)):
        hw = dict(hw)
        hw["backend"] = "cpu"
        hw["device"] = "cpu"
        hw["backend_family"] = "cpu_only"
        hw["device_preference"] = "cpu"
        hw["available_backends"] = [b for b in list(hw.get("available_backends") or []) if str(b).lower() != "directml"]
    preset_l = str(preset or "balanced").lower().strip()
    requested_tier = "draft" if preset_l == "fast" else ("quality" if preset_l in ("quality", "ultra") else "auto")
    tier_plan = _build_internal_render_plan(hw, requested_tier=requested_tier)
    preferred = str(tier_plan.get("preferred_internal_model") or hw.get("preferred_internal_model") or "hf_sd15_internal")
    if str(tier_plan.get("device_preference") or "auto") == "directml":
        fallbacks = [preferred, "hf_sdxl_internal", "hf_sd15_internal"]
    else:
        fallbacks = [preferred, "hf_sd35_medium_internal", "hf_sdxl_internal", "hf_sd15_internal"]
    runtime = _internal_diffusion_runtime_status()
    picked = None
    seen: set[str] = set()
    hardware_issues: list[dict[str, str]] = []
    for mid in fallbacks:
        if mid in seen:
            continue
        seen.add(mid)
        installed = _resolve_installed_model_path(mid, materialize_remote=False)
        if not installed:
            continue
        family = _internal_model_family_for_request(mid, installed)
        hardware_issue = _internal_model_hardware_issue(mid, family, hw, str(tier_plan.get("device_preference") or "auto"))
        if hardware_issue:
            hardware_issues.append(hardware_issue)
            continue
        picked = mid
        break
    if picked and runtime.get("ok"):
        return {
            "mode": "internal",
            "engine": "diffusion",
            "model_id": picked,
            "reason": f"{reason} Falling back to local internal render.",
            "diagnostics": ["comfyui=unavailable", f"internal_model={picked}", *list(runtime.get("diagnostics") or [])],
            "tier_plan": tier_plan,
        }
    if _hosted_stability_ready({"allow_hosted_fallback": True}):
        stability = provider_status.get("stability") or {}
        diagnostics = ["comfyui=unavailable", "hosted_stability=ready", *list(runtime.get("diagnostics") or [])]
        return {
            "mode": "hosted",
            "engine": "stability",
            "model_id": f"stability:{stability.get('service')}:{stability.get('model')}",
            "reason": f"{reason} Falling back to hosted Stability keyframes.",
            "diagnostics": diagnostics,
            "tier_plan": tier_plan,
            "hosted_provider": stability,
        }
    diagnostics = ["comfyui=unavailable"]
    if picked:
        diagnostics.append(f"internal_model={picked}")
    else:
        diagnostics.append("internal_models=unsupported" if hardware_issues else "internal_models=missing")
        diagnostics.extend(str(issue["message"]) for issue in hardware_issues)
    diagnostics.extend(list(runtime.get("diagnostics") or []))
    return {
        "mode": "none",
        "engine": None,
        "model_id": None,
        "reason": f"{reason} No installed local model or configured hosted provider is available.",
        "diagnostics": diagnostics + [f"project={project_id}"],
        "tier_plan": tier_plan,
    }


def _recommend_video_route(project_id: str | None = None) -> dict[str, Any]:
    """Decide whether to use the local GPU or NVIDIA Cosmos cloud for video generation.

    Returns a dict with:
      route       : "local_gpu" | "cosmos_cloud" | "none"
      reason      : human-readable explanation
      preference  : the saved preference setting
      local_ready : bool - local GPU path is available
      cosmos_ready: bool - Cosmos cloud path is configured
      local_detail: dict  - hardware/model info for local path
      cosmos_detail: dict - cosmos provider info
    """
    cfg = render_settings.get()
    video_cfg = dict(cfg.get("video") or {})
    preference = str(video_cfg.get("preference") or "auto").strip().lower()
    auto_prefer_gpu = bool(video_cfg.get("auto_prefer_gpu", True))
    cosmos_fallback = bool(video_cfg.get("cosmos_fallback", True))

    hw = _hardware_profile()
    provider_status = _render_provider_status(hw)
    cosmos_status = dict(provider_status.get("cosmos") or {})
    azure_foundry_status = dict(provider_status.get("azure_foundry") or {})
    cuda_status = dict(provider_status.get("cuda") or {})

    # Local GPU: CUDA available + enabled
    local_ready = bool(cuda_status.get("active") or (
        bool(hw.get("cuda_runtime_ready")) and bool(cuda_status.get("enabled", True))
    ))
    # Also consider: does an internal model exist?
    local_model = str(hw.get("preferred_internal_model") or "hf_sdxl_internal")
    local_model_installed = bool(_resolve_installed_model_path(local_model, materialize_remote=False))

    # Cosmos cloud: configured API key + enabled
    cosmos_ready = bool(cosmos_status.get("active") or (
        cosmos_status.get("configured") and cosmos_status.get("enabled")
    ))
    # Azure Foundry Cosmos3: hosted managed-compute deployment — configured + enabled + API key.
    # Never auto-selected (see the "auto" branch below); only used via explicit preference/route.
    azure_foundry_ready = bool(azure_foundry_status.get("active") or (
        azure_foundry_status.get("configured")
        and azure_foundry_status.get("enabled")
        and azure_foundry_status.get("has_api_key")
    ))

    local_detail = {
        "cuda_available": bool(hw.get("cuda_runtime_ready")),
        "device": hw.get("device_name") or hw.get("device") or "cpu",
        "vram_gb": float(hw.get("vram_gb") or 0.0),
        "model": local_model,
        "model_installed": local_model_installed,
    }
    cosmos_detail = {
        "model": cosmos_status.get("model"),
        "configured": cosmos_status.get("configured"),
        "num_frames": cosmos_status.get("num_frames"),
        "fps": cosmos_status.get("fps"),
    }
    azure_foundry_detail = {
        "configured": azure_foundry_status.get("configured"),
        "has_api_key": azure_foundry_status.get("has_api_key"),
        "endpoint_url": azure_foundry_status.get("endpoint_url"),
        "deployment_name": azure_foundry_status.get("deployment_name"),
        "num_frames": azure_foundry_status.get("num_frames"),
        "fps": azure_foundry_status.get("fps"),
    }

    # Explicit preferences override auto logic
    if preference == "local_gpu":
        if local_ready:
            return {"route": "local_gpu", "reason": "Local GPU selected by preference.",
                    "preference": preference, "local_ready": local_ready,
                    "cosmos_ready": cosmos_ready, "local_detail": local_detail,
                    "cosmos_detail": cosmos_detail,
                    "azure_foundry_ready": azure_foundry_ready, "azure_foundry_detail": azure_foundry_detail}
        return {"route": "none", "reason": "Local GPU selected but CUDA is not available or disabled.",
                "preference": preference, "local_ready": False,
                "cosmos_ready": cosmos_ready, "local_detail": local_detail,
                "cosmos_detail": cosmos_detail,
                "azure_foundry_ready": azure_foundry_ready, "azure_foundry_detail": azure_foundry_detail}

    if preference == "cosmos_cloud":
        if cosmos_ready:
            return {"route": "cosmos_cloud", "reason": "NVIDIA Cosmos selected by preference.",
                    "preference": preference, "local_ready": local_ready,
                    "cosmos_ready": cosmos_ready, "local_detail": local_detail,
                    "cosmos_detail": cosmos_detail,
                    "azure_foundry_ready": azure_foundry_ready, "azure_foundry_detail": azure_foundry_detail}
        return {"route": "none", "reason": "Cosmos selected but NVIDIA API key is not configured.",
                "preference": preference, "local_ready": local_ready,
                "cosmos_ready": False, "local_detail": local_detail,
                "cosmos_detail": cosmos_detail,
                "azure_foundry_ready": azure_foundry_ready, "azure_foundry_detail": azure_foundry_detail}

    if preference == "azure_foundry_cloud":
        if azure_foundry_ready:
            return {"route": "azure_foundry_cloud", "reason": "Azure AI Foundry Cosmos3 selected by preference.",
                    "preference": preference, "local_ready": local_ready,
                    "cosmos_ready": cosmos_ready, "local_detail": local_detail,
                    "cosmos_detail": cosmos_detail,
                    "azure_foundry_ready": azure_foundry_ready, "azure_foundry_detail": azure_foundry_detail}
        return {"route": "none",
                "reason": "Azure Foundry selected but it is not fully configured (endpoint, deployment name, and API key are all required).",
                "preference": preference, "local_ready": local_ready,
                "cosmos_ready": cosmos_ready, "local_detail": local_detail,
                "cosmos_detail": cosmos_detail,
                "azure_foundry_ready": False, "azure_foundry_detail": azure_foundry_detail}

    # auto: pick intelligently
    gpu_score = 0
    if local_ready:
        gpu_score += 3
        vram = float(hw.get("vram_gb") or 0.0)
        if vram >= 8.0:
            gpu_score += 2  # strong GPU → prefer local
        elif vram >= 6.0:
            gpu_score += 1  # RTX 4050 class — local stills ok, Cosmos better for full video
        if local_model_installed:
            gpu_score += 1

    cosmos_score = 0
    if cosmos_ready:
        cosmos_score += 4  # cloud video quality generally higher than local for full clips
        if not local_ready:
            cosmos_score += 3  # only option

    if auto_prefer_gpu and local_ready and gpu_score >= cosmos_score:
        route = "local_gpu"
        reason = f"Auto: local GPU preferred ({hw.get('device_name', 'GPU')}, {float(hw.get('vram_gb',0)):.1f} GB VRAM)."
    elif cosmos_ready:
        route = "cosmos_cloud"
        reason = "Auto: NVIDIA Cosmos cloud selected (better full-video quality or GPU not preferred)."
        if not local_ready:
            reason = "Auto: local CUDA not available — using NVIDIA Cosmos cloud."
    elif local_ready:
        route = "local_gpu"
        reason = "Auto: Cosmos not configured — using local GPU."
    else:
        route = "none"
        reason = "Auto: neither local GPU nor Cosmos cloud is available. Enable CUDA or add NVIDIA API key."

    # Cosmos fallback: if preferred local but cosmos available, note it
    fallback_available = cosmos_fallback and cosmos_ready and route == "local_gpu"

    return {
        "route": route,
        "reason": reason,
        "preference": preference,
        "local_ready": local_ready,
        "cosmos_ready": cosmos_ready,
        "fallback_available": fallback_available,
        "fallback_route": "cosmos_cloud" if fallback_available else None,
        "local_detail": local_detail,
        "cosmos_detail": cosmos_detail,
        "azure_foundry_ready": azure_foundry_ready,
        "azure_foundry_detail": azure_foundry_detail,
    }


@app.get("/v1/render/route")
def get_video_route():
    """Return the current recommended video generation route (GPU vs Cloud)."""
    return {"ok": True, **_recommend_video_route()}


@app.post("/v1/render/route/preferences")
def set_video_route_preferences(payload: dict[str, Any]):
    """Save video generation preference (auto / local_gpu / cosmos_cloud / comfyui)."""
    preference = str((payload or {}).get("preference") or "auto").strip().lower()
    if preference not in VIDEO_GENERATION_PREFERENCES:
        raise UserFacingError(
            f"Unknown preference '{preference}'.",
            hint=f"Choose one of: {', '.join(VIDEO_GENERATION_PREFERENCES)}",
            code="INVALID_VIDEO_PREFERENCE",
            status_code=400,
        )
    auto_prefer_gpu = bool((payload or {}).get("auto_prefer_gpu", True))
    cosmos_fallback = bool((payload or {}).get("cosmos_fallback", True))
    saved = render_settings.update({"video": {
        "preference": preference,
        "auto_prefer_gpu": auto_prefer_gpu,
        "cosmos_fallback": cosmos_fallback,
    }})
    _hardware_profile_invalidate()
    return {"ok": True, "video": saved.get("video"), "route": _recommend_video_route()}


@app.post("/v1/projects/{project_id}/render/video/smart")
def render_video_smart(project_id: str, payload: dict[str, Any]):
    """Route a video render to local GPU, NVIDIA Cosmos, or Azure AI Foundry Cosmos3
    based on preference.

    Accepts same fields as /render/cosmos/all_scenes and the internal video
    conductor. The router decides which backend to use; the caller can override
    with explicit route='local_gpu'|'cosmos_cloud'|'azure_foundry_cloud'.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    explicit_route = str((payload or {}).get("route") or "").strip().lower()
    recommendation = _recommend_video_route(project_id)
    route = explicit_route if explicit_route in ("local_gpu", "cosmos_cloud", "azure_foundry_cloud") else recommendation["route"]

    if route == "cosmos_cloud":
        return render_cosmos_all_scenes(project_id, payload)

    if route == "azure_foundry_cloud":
        return render_azure_foundry_all_scenes(project_id, payload)

    if route == "local_gpu":
        # Kick off internal video render via the existing conductor flow
        variant_index = int((payload or {}).get("variant_index") or 0)
        preset = str((payload or {}).get("preset") or "balanced")
        return run_pipeline(project_id, variant_index=variant_index, preset=preset, mode="auto", engine="auto")

    raise UserFacingError(
        "No video generation route is available.",
        hint=(
            "Enable CUDA in Settings → GPU / Render Runtime, add your NVIDIA API key "
            "(same key as Nemotron) for Cosmos cloud, or configure Azure AI Foundry Cosmos3 "
            "(endpoint, deployment name, and API key) in Settings."
        ),
        code="NO_VIDEO_ROUTE",
        status_code=400,
    )


def _recommend_pipeline(project_id: str, preset: str, mode: str = "auto", engine: str = "auto") -> dict[str, Any]:
    ckpt, _fallback_from = _resolve_comfy_checkpoint_name(settings.comfyui_checkpoint, allow_auto_fallback=True)
    mode_l = (mode or "auto").lower().strip()
    engine_l = (engine or "auto").lower().strip()

    if mode_l == "internal":
        return _recommend_local_fallback(project_id, preset, reason="Internal mode requested.")

    # Basic availability (any healthy node)
    base_diag = comfy_pool.diagnose({"checkpoint": ckpt})
    base_ok = bool(base_diag["compatible"] or base_diag["busy_compatible"])
    if not base_ok:
        if mode_l == "auto":
            return _recommend_local_fallback(project_id, preset, reason="ComfyUI is not reachable.")
        raise UserFacingError(
            message="ComfyUI is not reachable (no healthy nodes).",
            hint="Start ComfyUI, then confirm EDMG_COMFYUI_URL points to it (default http://127.0.0.1:8188).",
            code="COMFYUI_UNREACHABLE",
            status_code=502,
        )

    # Motion capabilities
    ad_req = {"checkpoint": ckpt, "node_classes": ["ADE_StandardStaticContextOptions", "ADE_AnimateDiffLoaderGen1"], "est_steps": 20, "est_frames": 24}
    svd_req = {"checkpoint": ckpt, "node_classes": ["SVDSimpleImg2Vid"], "est_steps": 20, "est_frames": 14}
    ad_diag = comfy_pool.diagnose(ad_req)
    svd_diag = comfy_pool.diagnose(svd_req)
    ad_ok = bool(ad_diag["compatible"] or ad_diag["busy_compatible"])
    svd_ok = bool(svd_diag["compatible"] or svd_diag["busy_compatible"])

    diagnostics = [
        f"healthy_nodes={len(base_diag['compatible']) + len(base_diag['busy_compatible'])}",
        f"animatediff_nodes={len(ad_diag['compatible']) + len(ad_diag['busy_compatible'])}",
        f"svd_nodes={len(svd_diag['compatible']) + len(svd_diag['busy_compatible'])}",
    ]

    preset_l = (preset or "balanced").lower().strip()

    # Fast preset intentionally forces stills unless user overrides in Advanced.
    if preset_l == "fast" and mode_l == "auto":
        return {"mode": "stills", "engine": None, "reason": "Fast preset uses stills for speed.", "diagnostics": diagnostics}

    if mode_l == "stills":
        return {"mode": "stills", "engine": None, "reason": "Forced stills mode.", "diagnostics": diagnostics}

    # motion desired (auto or forced)
    chosen = None
    if engine_l in ("auto", "animatediff") and ad_ok:
        chosen = "animatediff"
    elif engine_l in ("auto", "svd") and svd_ok:
        chosen = "svd"
    elif ad_ok:
        chosen = "animatediff"
    elif svd_ok:
        chosen = "svd"

    if chosen:
        return {"mode": "motion", "engine": chosen, "reason": "Motion-capable node detected.", "diagnostics": diagnostics}

    # fallback
    return {"mode": "stills", "engine": None, "reason": "No motion-capable nodes detected; falling back to stills.", "diagnostics": diagnostics}


@app.get("/v1/projects/{project_id}/pipeline/validate")
def validate_pipeline(project_id: str, variant_index: int = 0, preset: str = "balanced", mode: str = "auto", engine: str = "auto"):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    rec = _recommend_pipeline(project_id, preset=preset, mode=mode, engine=engine)
    return {"ok": True, "recommended": rec, "hardware": _hardware_profile()}


@app.post("/v1/projects/{project_id}/render/conductor/plan")
def render_conductor_plan(project_id: str, req: RenderConductorPlanRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    visual_dna = _load_project_visual_dna(proj)
    intent = _build_render_conductor_intent(project_id, proj, req)
    snapshot = _build_project_snapshot(proj, dna=visual_dna)
    environment = _build_render_conductor_environment()
    meta = proj.meta if isinstance(proj.meta, dict) else {}
    audio_meta = meta.get("audio") if isinstance(meta.get("audio"), dict) else {}
    analysis = meta.get("analysis") if isinstance(meta.get("analysis"), dict) else {}
    environment["director_mode"] = normalize_director_mode(meta.get("director_mode") or meta.get("creative_direction_mode"))
    environment["music_graph"] = music_graph_from_analysis(
        analysis,
        audio_filename=str(audio_meta.get("filename") or "") or None,
        duration_s=float(audio_meta.get("duration_s") or analysis.get("duration_s") or 0) or None,
    )
    try:
        advisory_plan = build_advisory_render_plan(intent, snapshot, environment=environment)
    except NoRealRenderRouteError as exc:
        diagnostics = "; ".join(exc.diagnostics)
        raise UserFacingError(
            "No requested real render route is currently available.",
            hint=diagnostics or "Install a supported local model or configure a hosted provider, then retry.",
            code="NO_RENDER_ROUTE",
            status_code=409,
        ) from exc
    plan_payload = advisory_plan.model_dump(mode="json")
    proj.meta["last_conductor_plan"] = plan_payload
    proj.meta["last_conductor_intent"] = intent.model_dump(mode="json")
    store.save(proj)
    return {
        "ok": True,
        "intent": intent.model_dump(mode="json"),
        "plan": plan_payload,
        "environment": environment,
        "visual_dna_hints": build_visual_dna_prompt_hints(visual_dna),
    }


@app.post("/v1/projects/{project_id}/render/conductor/promote")
def render_conductor_promote(project_id: str, req: RenderConductorPromoteRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    stored = proj.meta.get("last_conductor_plan") if isinstance(proj.meta.get("last_conductor_plan"), dict) else None
    if not stored:
        raise HTTPException(400, "No conductor plan available. Generate an advisory plan first.")
    if req.plan_id and str(stored.get("plan_id") or "") != str(req.plan_id):
        raise HTTPException(400, "Conductor plan_id does not match the saved plan")

    updated_plan, promoted = promote_proxy_sections(
        stored,
        scene_ids=list(req.scene_ids or []),
        target_engine=req.target_engine,
        quality_tier=str(req.quality_tier or "quality"),
        reason=req.reason,
    )
    plan_payload = updated_plan.model_dump(mode="json")
    promotions = list(proj.meta.get("conductor_promotions") or []) if isinstance(proj.meta.get("conductor_promotions"), list) else []
    promotions.append(
        {
            "at": time.time(),
            "plan_id": plan_payload.get("plan_id"),
            "scene_ids": promoted,
            "target_engine": req.target_engine,
            "quality_tier": req.quality_tier,
            "reason": req.reason,
        }
    )
    proj.meta["last_conductor_plan"] = plan_payload
    proj.meta["conductor_promotions"] = promotions[-20:]
    store.save(proj)
    return {
        "ok": True,
        "plan": plan_payload,
        "promoted_scene_ids": promoted,
        "promotions": promotions[-5:],
    }


@app.get("/v1/projects/{project_id}/render/performer/plan")
def get_render_performer_plan(project_id: str, variant_index: int = 0) -> dict[str, Any]:
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    stored = proj.meta.get("last_performer_plan") if isinstance(proj.meta.get("last_performer_plan"), dict) else None
    if not stored:
        return {"ok": True, "performer_plan": None, "stored": False}
    if int(stored.get("variant_index") or 0) != int(variant_index):
        return {"ok": True, "performer_plan": None, "stored": False, "variant_index": variant_index}
    return {"ok": True, "performer_plan": stored, "stored": True}


@app.post("/v1/projects/{project_id}/render/performer/plan")
def render_performer_plan(project_id: str, req: PerformerWorkflowPlanRequest) -> dict[str, Any]:
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan.get("variants") if isinstance(plan.get("variants"), list) else []
    vi = int(req.variant_index or 0)
    if vi < 0 or vi >= len(variants):
        raise HTTPException(400, "Invalid variant_index")
    variant = variants[vi] if isinstance(variants[vi], dict) else {}
    scenes = [scene for scene in list(variant.get("scenes") or []) if isinstance(scene, dict)]
    meta = proj.meta if isinstance(proj.meta, dict) else {}
    audio_meta = meta.get("audio") if isinstance(meta.get("audio"), dict) else {}
    analysis = meta.get("analysis") if isinstance(meta.get("analysis"), dict) else {}
    music_graph = music_graph_from_analysis(
        analysis,
        audio_filename=str(audio_meta.get("filename") or "") or None,
        duration_s=float(audio_meta.get("duration_s") or analysis.get("duration_s") or 0) or None,
    )
    environment = _build_render_conductor_environment()
    performer_engines = environment.setdefault("engines", {})
    performer_hosted = dict(performer_engines.get("hosted_video") or {})
    performer_hosted["available"] = _performer_high_end_available()
    performer_hosted["capability"] = "audio_driven_performance_video"
    performer_engines["hosted_video"] = performer_hosted
    performer_plan = build_performer_workflow_plan(
        project_id=project_id,
        variant_index=vi,
        scenes=scenes,
        music_graph=music_graph,
        director_mode=normalize_director_mode(meta.get("director_mode") or meta.get("creative_direction_mode")),
        environment=environment,
        scene_ids=list(req.scene_ids or []),
        model_id=str(req.model_id or "wan_s2v_14b"),
    )
    proj.meta["last_performer_plan"] = performer_plan
    store.save(proj)
    return {
        "ok": True,
        "performer_plan": performer_plan,
        "music_graph": music_graph,
        "environment": environment,
    }


def _performer_high_end_available() -> bool:
    """High-end stays unavailable until a supported Wan S2V adapter ships.

    Generic hosted still/video credentials are not audio-driven performer
    capability and must never be reported as Wan S2V execution.
    """
    return False


def _run_performer_video(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    raise UserFacingError(
        "No real Wan S2V performer adapter is available in this build.",
        hint="Install and configure a supported Wan S2V adapter before starting a performer render.",
        code="PERFORMER_ADAPTER_UNAVAILABLE",
        status_code=409,
    )


@app.post("/v1/projects/{project_id}/render/performer/run")
def render_performer_run(project_id: str, req: PerformerWorkflowRunRequest) -> dict[str, Any]:
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    stored = proj.meta.get("last_performer_plan") if isinstance(proj.meta.get("last_performer_plan"), dict) else None
    if not stored:
        raise HTTPException(400, "No performer plan available. Plan the performer lane first.")
    if int(stored.get("variant_index") or 0) != int(req.variant_index):
        raise HTTPException(400, "Performer plan does not match the selected variant")
    if req.plan_id and str(stored.get("plan_id") or "") != str(req.plan_id):
        raise HTTPException(400, "Performer plan_id does not match the saved plan")
    if not list(stored.get("tasks") or []):
        raise HTTPException(400, "Performer plan has no render tasks")

    raise UserFacingError(
        "No real Wan S2V performer adapter is available in this build.",
        hint="Install and configure a supported Wan S2V adapter before starting a performer render.",
        code="PERFORMER_ADAPTER_UNAVAILABLE",
        status_code=409,
    )


@app.get("/v1/projects/{project_id}/unreal/preview")
def get_unreal_bridge_preview(project_id: str, variant_index: int = 0):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    variants = plan.get("variants") if isinstance(plan, dict) and isinstance(plan.get("variants"), list) else []
    if not variants:
        raise HTTPException(400, "No plan generated")
    vi = int(variant_index or 0)
    if vi < 0 or vi >= len(variants):
        raise HTTPException(400, "Invalid variant_index")

    preview = UnrealBridgePreviewResponse.model_validate(
        build_unreal_bridge_preview(
            project_id=str(proj.id),
            project_name=str(getattr(proj, "name", "") or "") or None,
            analysis=(proj.meta.get("analysis") or {}) if isinstance(proj.meta, dict) else {},
            plan=plan if isinstance(plan, dict) else {},
            timeline=(proj.meta.get("timeline") or {}) if isinstance(proj.meta, dict) else {},
            variant_index=vi,
        )
    )
    return {"ok": True, "preview": preview.model_dump(mode="json")}


@app.post("/v1/projects/{project_id}/pipeline/run")
def run_pipeline(project_id: str, variant_index: int = 0, preset: str = "balanced", mode: str = "auto", engine: str = "auto"):
    """Enqueue an end-to-end pipeline: render (auto stills/motion) -> assemble final MP4.

    This endpoint is designed for one-click UX. It keeps full functionality internally.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    mode_l = (mode or "auto").lower().strip()
    if mode_l == "internal":
        preset_l = str(preset or "balanced").lower().strip()
        requested_tier = "draft" if preset_l == "fast" else ("quality" if preset_l in ("quality", "ultra") else "auto")
        hw = _hardware_profile()
        provider_status = _render_provider_status(hw)
        tier_plan = _build_internal_render_plan(hw, requested_tier=requested_tier)
        tier_defaults = dict(tier_plan.get("defaults") or {})
        device_preference = str(tier_plan.get("device_preference") or "auto")
        if device_preference == "directml" and not bool((provider_status.get("directml") or {}).get("enabled", True)):
            device_preference = "cpu"
        internal_req = InternalVideoRenderRequest(
            variant_index=variant_index,
            fps_output=int(tier_defaults.get("fps_output", 24)),
            fps_render=int(tier_defaults.get("fps_render", 2)),
            width=int(tier_defaults.get("width", 768)),
            height=int(tier_defaults.get("height", 432)),
            steps=int(tier_defaults.get("steps", 15)),
            cfg=float(tier_defaults.get("cfg", 7.0)),
            keyframe_interval_s=float(tier_defaults.get("keyframe_interval_s", 5.0)),
            interpolation_engine=str(tier_defaults.get("interpolation_engine", os.getenv("EDMG_INTERPOLATION_ENGINE", "auto"))),
            model_id=os.getenv("EDMG_INTERNAL_MODEL_ID", "auto"),
            render_mode="auto",
            render_tier=str(tier_plan.get("applied_tier") or requested_tier),
            device_preference=device_preference,
            temporal_mode=str(tier_defaults.get("temporal_mode", "frame_img2img")),
            temporal_steps=int(tier_defaults.get("temporal_steps", 12)),
            refine_every_n_frames=int(tier_defaults.get("refine_every_n_frames", 1)),
            anchor_strength=float(tier_defaults.get("anchor_strength", 0.20)),
            prompt_blend=bool(tier_defaults.get("prompt_blend", True)),
        )
        res = render_internal_video(project_id, internal_req)
        return {"ok": True, "mode": str(res.get("preflight", {}).get("mode") or "internal"), "job": res.get("job"), "preflight": res.get("preflight")}

    defaults = _preset_defaults(preset)
    rec = _recommend_pipeline(project_id, preset=preset, mode=mode, engine=engine)
    if rec.get("mode") == "none":
        raise UserFacingError(
            "No render route is available.",
            hint=str(rec.get("reason") or "Install a supported local model or configure a hosted provider."),
            code="NO_RENDER_ROUTE",
            status_code=400,
        )

    if rec["mode"] in ("internal", "hosted"):
        hw = _hardware_profile()
        provider_status = _render_provider_status(hw)
        tier_plan = dict(rec.get("tier_plan") or _build_internal_render_plan(hw, requested_tier=("draft" if preset == "fast" else ("quality" if preset in ("quality", "ultra") else "auto"))))
        tier_defaults = dict(tier_plan.get("defaults") or {})
        device_preference = str(tier_plan.get("device_preference") or "auto")
        if device_preference == "directml" and not bool((provider_status.get("directml") or {}).get("enabled", True)):
            device_preference = "cpu"
        internal_req = InternalVideoRenderRequest(
            variant_index=variant_index,
            fps_output=int(tier_defaults.get("fps_output", 24)),
            fps_render=int(tier_defaults.get("fps_render", 2)),
            width=int(tier_defaults.get("width", defaults["stills"]["width"])),
            height=int(tier_defaults.get("height", defaults["stills"]["height"])),
            steps=int(tier_defaults.get("steps", defaults["stills"]["steps"])),
            cfg=float(tier_defaults.get("cfg", defaults["stills"]["cfg"])),
            keyframe_interval_s=float(tier_defaults.get("keyframe_interval_s", os.getenv("EDMG_INTERNAL_KEYFRAME_INTERVAL_S", "5.0"))),
            interpolation_engine=str(tier_defaults.get("interpolation_engine", os.getenv("EDMG_INTERPOLATION_ENGINE", "auto"))),
            model_id=str(rec.get("model_id") or os.getenv("EDMG_INTERNAL_MODEL_ID", "auto")),
            render_mode=("hosted" if rec["mode"] == "hosted" else "auto"),
            render_tier=str(tier_plan.get("applied_tier") or "auto"),
            device_preference=device_preference,
            temporal_mode=str(tier_defaults.get("temporal_mode", "frame_img2img")),
            temporal_steps=int(tier_defaults.get("temporal_steps", 12)),
            refine_every_n_frames=int(tier_defaults.get("refine_every_n_frames", 1)),
            anchor_strength=float(tier_defaults.get("anchor_strength", 0.20)),
            prompt_blend=bool(tier_defaults.get("prompt_blend", True)),
            allow_hosted_fallback=True,
        )
        res = render_internal_video(project_id, internal_req)
        effective_mode = str(res.get("preflight", {}).get("mode") or rec["mode"])
        selected = dict(rec)
        if effective_mode == "diffusion":
            selected["mode"] = "internal"
            selected["engine"] = "diffusion"
            selected["model_id"] = str(res.get("preflight", {}).get("model_id") or selected.get("model_id") or "auto")
        elif effective_mode == "hosted":
            selected["mode"] = effective_mode
        return {
            "ok": True,
            "preset": preset,
            "selected": selected,
            "render_mode": effective_mode,
            "job": res.get("job"),
            "preflight": res.get("preflight"),
        }

    if rec["mode"] == "stills":
        req = RenderScenesRequest(
            variant_index=variant_index,
            negative_prompt="(low quality, worst quality)",
            width=int(defaults["stills"]["width"]),
            height=int(defaults["stills"]["height"]),
            steps=int(defaults["stills"]["steps"]),
            cfg=float(defaults["stills"]["cfg"]),
            sampler=str(defaults["stills"]["sampler"]),
        )
        enq = render_scenes(project_id, req)
        assemble_fps = 24
    else:
        eng = rec["engine"] or "animatediff"
        req = RenderMotionRequest(
            variant_index=variant_index,
            negative_prompt="(low quality, worst quality)",
            width=int(defaults["stills"]["width"]),
            height=int(defaults["stills"]["height"]),
            steps=int(defaults["stills"]["steps"]),
            cfg=float(defaults["stills"]["cfg"]),
            sampler=str(defaults["stills"]["sampler"]),
            fps=int(defaults["motion"]["fps"]),
            max_frames_per_scene=int(defaults["motion"]["max_frames"]),
            engine=eng,
            motion_model_name="mm_sd_v15_v2.ckpt",
            context_length=16,
            context_overlap=4,
            beta_schedule="autoselect",
            svd_checkpoint="svd_xt.safetensors",
            svd_num_steps=25,
            svd_motion_bucket_id=127,
            svd_fps_id=6,
            svd_cond_aug=0.02,
            svd_decoding_t=14,
            device="cuda",
        )
        enq = render_motion_scenes(project_id, req)
        assemble_fps = int(defaults["motion"]["fps"])

    assemble_job = jobs.create(project_id, "assemble_variant", {"variant_index": variant_index, "fps": assemble_fps})
    return {
        "ok": True,
        "preset": preset,
        "selected": rec,
        "render_enqueued": enq.get("enqueued"),
        "assemble_job": assemble_job.__dict__,
    }


def _comfyui_available_quick() -> bool:
    """Best-effort check for a reachable, render-capable ComfyUI node."""
    try:
        ckpt, _ = _resolve_comfy_checkpoint_name(settings.comfyui_checkpoint, allow_auto_fallback=True)
        diag = comfy_pool.diagnose({"checkpoint": ckpt})
        return bool(diag.get("compatible") or diag.get("busy_compatible"))
    except Exception:
        return False


@app.get("/v1/render/animation_presets")
def animation_presets():
    """List the one-click animation presets (quality + motion intensity buttons)."""
    return {"ok": True, "presets": autoconfig.list_presets()}


@app.post("/v1/projects/{project_id}/render/auto")
def render_auto(project_id: str, req: AutoAnimateRequest):
    """AI auto-configure render settings for a chosen animation preset, then
    optionally launch the full workflow on the internal renderer or ComfyUI.

    Manual configuration endpoints (``/render/internal/video``,
    ``/render/comfyui/motion_scenes``, etc.) remain available unchanged; this is
    an additive "push a button and the AI sets everything, then renders" layer.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    vi = int(req.variant_index)
    if vi < 0 or vi >= len(variants):
        raise HTTPException(400, "Invalid variant_index")
    variant = variants[vi]
    scenes = variant.get("scenes") or []

    preset = autoconfig.resolve_preset(req.preset)
    if preset is None:
        raise UserFacingError(
            f"Unknown animation preset '{req.preset}'",
            hint="Call GET /v1/render/animation_presets for the available preset ids.",
            code="UNKNOWN_PRESET",
            status_code=400,
        )

    duration_s = _resolved_project_duration_s(proj, variant, scenes)
    fps = int(req.fps or variant.get("fps") or 24)

    hw = _hardware_profile()
    provider_status = _render_provider_status(hw)
    requested_tier = preset.quality if preset.quality in ("draft", "balanced", "quality") else "auto"
    tier_plan = _build_internal_render_plan(hw, requested_tier=requested_tier, duration_s=duration_s)
    tier_defaults = dict(tier_plan.get("defaults") or {})
    device_preference = str(tier_plan.get("device_preference") or "auto")
    if device_preference == "directml" and not bool((provider_status.get("directml") or {}).get("enabled", True)):
        device_preference = "cpu"

    requested_engine = str(req.engine or "auto").lower().strip()
    comfy_probe_performed = requested_engine == "comfyui" or (
        requested_engine == "auto" and str(preset.engine_hint or "auto").lower().strip() == "comfyui"
    )
    comfy_ok = _comfyui_available_quick() if comfy_probe_performed else False
    cfg = autoconfig.build_autoconfig(
        preset,
        engine=req.engine,
        tier_defaults=tier_defaults,
        applied_tier=str(tier_plan.get("applied_tier") or "auto"),
        preferred_model=str(tier_plan.get("preferred_internal_model") or "auto"),
        device_preference=device_preference,
        duration_s=duration_s,
        fps=fps,
        variant_index=vi,
        source_asset=req.source_asset,
        comfyui_available=comfy_ok,
        tensorrt_sd15_available=_tensorrt_sd15_bundle_available(),
    )

    result: dict[str, Any] = {
        "ok": True,
        "config": cfg.to_public(),
        "engine": cfg.engine,
        "hardware": hw,
        "tier_plan": tier_plan,
        "comfyui_available": comfy_ok,
        "comfyui_probe_performed": comfy_probe_performed,
        "launched": False,
    }
    if not req.run:
        return result

    # Object/layer animation presets (parallax / segment / background) run on the
    # model-free layered renderer. Masked / ComfyUI-regional presets need masks,
    # so they return the config and point at /render/animate_layers.
    if preset.is_layered:
        if preset.requires_masks or cfg.engine == "comfyui":
            result["notes"] = list(result["config"].get("notes") or []) + [
                "This preset animates masked objects; call POST /render/animate_layers with masks."
            ]
            return result
        if not req.source_asset:
            result["notes"] = list(result["config"].get("notes") or []) + [
                "Object animation needs a source image; pass source_asset."
            ]
            return result
        lr = cfg.layered_request or {}
        layered_req = LayeredAnimateRequest(
            source_asset=req.source_asset,
            mode=cfg.animation_mode,
            motion=cfg.motion_profile,
            fps=int(lr.get("fps", req.fps or 24)),
            duration_s=float(lr.get("duration_s", 5.0)),
            width=int(lr.get("width", 768)),
            height=int(lr.get("height", 432)),
        )
        res = render_animate_layers(project_id, layered_req)
        result.update(
            {
                "launched": True,
                "engine": "internal",
                "animation_mode": cfg.animation_mode,
                "job": res.get("job"),
            }
        )
        return result

    if cfg.engine == "comfyui" and cfg.comfyui_request is not None:
        motion_payload = {
            k: v for k, v in cfg.comfyui_request.items() if k in RenderMotionRequest.model_fields
        }
        enq = render_motion_scenes(project_id, RenderMotionRequest(**motion_payload))
        assemble_job = jobs.create(
            project_id, "assemble_variant", {"variant_index": vi, "fps": int(cfg.comfyui_request.get("fps", 24))}
        )
        result.update(
            {
                "launched": True,
                "render_enqueued": enq.get("enqueued"),
                "jobs": enq.get("jobs"),
                "assemble_job": assemble_job.__dict__,
            }
        )
        return result

    internal_payload = {
        k: v for k, v in cfg.internal_request.items() if k in InternalVideoRenderRequest.model_fields
    }
    res = render_internal_video(project_id, InternalVideoRenderRequest(**internal_payload))
    result.update(
        {
            "launched": True,
            "engine": "internal",
            "job": res.get("job"),
            "preflight": res.get("preflight"),
        }
    )
    return result


def _run_timeline_render(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    proj = store.get(project_id)
    if not proj:
        raise RuntimeError("Project not found")
    pdir = store.project_dir(project_id)
    request = TimelineRenderRequest.model_validate(payload.get("settings") or {})
    timeline = payload.get("timeline")
    if not isinstance(timeline, dict):
        timeline = proj.meta.get("timeline")
    if not isinstance(timeline, dict):
        raise ValueError("Project timeline is missing or invalid")

    extension = ".mov" if request.video_codec == "prores" else ".mp4"
    safe_stem = Path(request.name).stem.strip(" .") or "edited-master"
    output_path = pdir / "outputs" / "videos" / f"{safe_stem}_{job_id[:8]}{extension}"

    def _is_canceled() -> bool:
        latest = jobs.get(project_id, job_id)
        return bool(latest and latest.status == "canceled")

    def _set_request_status(status: str, **extra: Any) -> None:
        current = store.get(project_id)
        if not current:
            return
        current.meta["last_timeline_render_request"] = {
            "job_id": job_id,
            "status": status,
            **request.model_dump(mode="json"),
            **extra,
        }
        store.save(current)

    jobs.update_progress(
        project_id,
        job_id,
        stage="validating",
        current=25,
        total=1000,
        message="Validating timeline sources",
    )
    try:
        command, duration_s = build_timeline_render_command(
            ffmpeg_path=settings.ffmpeg_path,
            project_dir=pdir,
            timeline=timeline,
            output_path=output_path,
            width=request.width,
            height=request.height,
            fps=request.fps,
            video_codec=request.video_codec,
            audio_codec=request.audio_codec,
            quality=request.quality,
        )
        if _is_canceled():
            raise TimelineRenderCanceled("Timeline render canceled")

        jobs.update_progress(
            project_id,
            job_id,
            stage="rendering",
            current=100,
            total=1000,
            message="Rendering edited master",
        )

        def _on_progress(fraction: float) -> None:
            jobs.update_progress(
                project_id,
                job_id,
                stage="rendering",
                current=100 + round(max(0.0, min(1.0, fraction)) * 850),
                total=1000,
                message="Rendering edited master",
            )

        render_timeline_edited_master(
            command=command,
            output_path=output_path,
            duration_s=duration_s,
            is_canceled=_is_canceled,
            on_progress=_on_progress,
        )
    except TimelineRenderCanceled as exc:
        _set_request_status("canceled")
        raise JobCanceled(str(exc)) from exc
    except Exception as exc:
        _set_request_status("failed", error=str(exc))
        raise

    video_rel = output_path.relative_to(pdir).as_posix()
    manifest_path = write_artifact_manifest(
        output_path,
        project_dir=pdir,
        project_id=project_id,
        kind="video",
        engine="timeline_render",
        params={
            **request.model_dump(mode="json"),
            "duration_s": duration_s,
        },
        extra={"job_id": job_id},
    )
    manifest_rel = manifest_path.relative_to(pdir).as_posix()

    proj = store.get(project_id)
    if not proj:
        raise RuntimeError("Project not found")
    outputs = proj.meta.setdefault("outputs", {})
    videos = outputs.setdefault("videos", [])
    videos.append(
        {
            "kind": "timeline_render",
            "path": video_rel,
            "video_codec": request.video_codec,
            "audio_codec": request.audio_codec,
        }
    )
    completed = {
        "job_id": job_id,
        "status": "succeeded",
        "video": video_rel,
        "artifact_manifest": manifest_rel,
        "duration_s": duration_s,
        **request.model_dump(mode="json"),
    }
    proj.meta["last_timeline_render_request"] = completed
    proj.meta["last_timeline_render"] = completed
    store.save(proj)
    jobs.update_progress(
        project_id,
        job_id,
        stage="complete",
        current=1000,
        total=1000,
        message="Timeline render complete",
    )
    return {
        "video": video_rel,
        "artifact_manifest": manifest_rel,
        "duration_s": duration_s,
    }


def _resolve_layered_refinement(
    payload: dict[str, Any],
) -> tuple[Path, str, str]:
    """Resolve the required downloaded model/device for a refined layer render."""
    hw = _hardware_profile()
    device_pref = str(payload.get("device_preference") or "auto")
    refine_device = device_pref if device_pref != "auto" else str(hw.get("device_preference") or "auto")
    model_id = str(payload.get("model_id") or "auto")
    if model_id in ("auto", "auto_internal"):
        tier_plan = _build_internal_render_plan(hw)
        model_id = str(tier_plan.get("preferred_internal_model") or "hf_sd15_internal")

    try:
        model_dir = _resolve_installed_model_path(model_id, materialize_remote=False)
    except Exception as exc:
        raise UserFacingError(
            "Diffusion refinement model could not be resolved",
            hint="Open Models, verify the selected internal image model, then retry the layered animation.",
            code="REFINEMENT_MODEL_RESOLUTION_FAILED",
            status_code=500,
        ) from exc
    if model_dir is None or not model_dir.exists():
        raise UserFacingError(
            f"Diffusion refinement model '{model_id}' is not installed",
            hint="Install the selected internal image model in Models, or turn refinement off for compositor-only output.",
            code="REFINEMENT_MODEL_NOT_INSTALLED",
            status_code=400,
        )
    return model_dir, refine_device, model_id


def _run_layered_animation(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """Worker: render an object/layer animation (parallax / masked / segment)."""
    proj = store.get(project_id)
    if not proj:
        raise UserFacingError("Project not found", hint="Open Projects and select a valid project.")
    pdir = store.project_dir(project_id)

    source_path = _resolve_project_reference_path(project_id, payload.get("source_asset"))
    if source_path is None:
        raise UserFacingError(
            "Source image not found",
            hint="Upload an image under Render → References, then pass its path as source_asset.",
            code="ASSET_MISSING",
            status_code=400,
        )

    mode = str(payload.get("mode") or "parallax")
    mask_specs: list[dict[str, Any]] = []
    for entry in payload.get("masks") or []:
        name = str(entry.get("mask_asset") or "").strip()
        if not name:
            continue
        mask_path = pdir / "assets" / "masks" / Path(name).name
        if not mask_path.exists():
            raise UserFacingError(
                f"Mask not found: {name}",
                hint="Upload the mask under Render → Masks, then retry.",
                code="ASSET_MISSING",
                status_code=400,
            )
        from PIL import Image as _PILImage

        mask_specs.append(
            {
                "mask": _PILImage.open(mask_path).convert("L"),
                "depth": float(entry.get("depth", 1.0)),
                "motion_scale": float(entry.get("motion_scale", 1.0)),
                "name": Path(name).stem,
            }
        )

    if mode == "masked" and not mask_specs:
        raise UserFacingError(
            "Masked mode requires at least one mask",
            hint="Add a mask asset, or use the parallax/segment modes which need no masks.",
            code="MASK_REQUIRED",
            status_code=400,
        )

    audio_path: Path | None = None
    if bool(payload.get("include_audio")):
        audio_meta = (proj.meta.get("audio") or {}) if isinstance(proj.meta, dict) else {}
        fname = str(audio_meta.get("filename") or "").strip()
        if fname:
            cand = pdir / "assets" / "audio" / Path(fname).name
            if cand.exists():
                audio_path = cand

    def _log(message: str) -> None:
        jobs.append_log(project_id, job_id, message)

    def _check_canceled() -> None:
        latest = jobs.get(project_id, job_id)
        if latest and latest.status == "canceled":
            raise JobCanceled("Job canceled")

    def _progress(stage: str, current: int, total: int, message: str | None = None) -> None:
        _check_canceled()
        jobs.update_progress(project_id, job_id, stage=stage, current=current, total=total, message=message)

    # Optional diffusion-refinement: resolve an installed internal model (auto-pick
    # by hardware tier) and run img2img over every composited frame. Refinement is
    # an explicit contract: missing models/runtimes fail instead of downgrading.
    refine = bool(payload.get("diffusion_refine"))
    refine_model_dir: Path | None = None
    refine_device = "auto"
    if refine:
        refine_model_dir, refine_device, model_id = _resolve_layered_refinement(payload)
        _log(f"Diffusion refinement requires model '{model_id}' on device '{refine_device}'.")

    out_dir = pdir / "outputs" / "videos" / f"layered_{job_id}"
    res = layeranim.render_layered_animation(
        ffmpeg_path=settings.ffmpeg_path,
        source_image_path=source_path,
        out_dir=out_dir,
        mode=mode,
        motion_schedule=payload.get("motion_schedule") or {},
        fps=int(payload.get("fps", 24)),
        duration_s=float(payload.get("duration_s", 5.0)),
        width=int(payload.get("width", 768)),
        height=int(payload.get("height", 432)),
        bands=int(payload.get("bands", 3)),
        mask_specs=mask_specs,
        subject_motion=float(payload.get("subject_motion", 1.0)),
        background_motion=float(payload.get("background_motion", 0.12)),
        audio_path=audio_path,
        log_fn=_log,
        progress_fn=_progress,
        cancel_check_fn=_check_canceled,
        diffusion_refine=refine,
        refine_model_dir=refine_model_dir,
        refine_device=refine_device,
        refine_prompt=str(payload.get("refine_prompt") or ""),
        refine_negative=str(payload.get("refine_negative") or "blurry, low quality, watermark, text, logo"),
        refine_denoise=float(payload.get("refine_denoise", 0.3)),
        refine_steps=int(payload.get("refine_steps", 20)),
        refine_cfg=float(payload.get("refine_cfg", 7.0)),
        refine_seed=int(payload.get("seed") or 0),
    )

    video_abs = Path(str(res.get("video") or ""))
    video_rel = None
    try:
        video_rel = str(video_abs.relative_to(pdir))
    except Exception:
        video_rel = str(res.get("video"))

    render_meta_path = video_abs.with_suffix(".render.json")
    render_meta = {
        "completed_at": time.time(),
        "render_mode": "layered_animation",
        "engine": "internal_layered_animation",
        "motion_profile": str(payload.get("motion_profile") or ""),
        "mode": str(res.get("mode") or payload.get("mode") or "parallax"),
        "segmentation": str(res.get("segmentation") or ""),
        "diffusion_refined": bool(res.get("diffusion_refined")),
        "refined_frames": int(res.get("refined_frames") or 0),
        "layers": list(res.get("layers") or []),
        "frames": {
            "expected": int(res.get("frames") or 0),
            "present": len(list((video_abs.parent / "frames").glob("frame_*.png"))),
            "dir": str(video_abs.parent / "frames"),
        },
        "outputs": {
            "final_mp4": str(video_abs),
            "checkpoint_json": None,
        },
        "settings": {
            "fps": int(payload.get("fps", 24)),
            "duration_s": float(payload.get("duration_s", 5.0)),
            "width": int(payload.get("width", 768)),
            "height": int(payload.get("height", 432)),
            "bands": int(payload.get("bands", 3)),
            "subject_motion": float(payload.get("subject_motion", 1.0)),
            "background_motion": float(payload.get("background_motion", 0.12)),
            "include_audio": bool(payload.get("include_audio")),
            "source_asset": str(payload.get("source_asset") or ""),
        },
    }
    try:
        render_meta_path.write_text(json.dumps(render_meta, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass
    try:
        from .store.artifacts import write_artifact_manifest

        write_artifact_manifest(
            video_abs,
            project_dir=pdir,
            project_id=project_id,
            kind="video",
            engine="internal_layered_animation",
            model_id=None,
            model_revision=None,
            seed=int(payload.get("seed")) if payload.get("seed") is not None else None,
            params={
                "mode": str(res.get("mode") or payload.get("mode") or "parallax"),
                "segmentation": str(res.get("segmentation") or ""),
                "fps": int(payload.get("fps", 24)),
                "duration_s": float(payload.get("duration_s", 5.0)),
                "width": int(payload.get("width", 768)),
                "height": int(payload.get("height", 432)),
                "bands": int(payload.get("bands", 3)),
                "diffusion_refined": bool(res.get("diffusion_refined")),
            },
            source_assets=[{"role": "source_image", "path": str(payload.get("source_asset") or ""), "sha256": None}],
            parents=[render_meta_path.name] if render_meta_path.exists() else [],
            extra={"render_meta": render_meta_path.name, "job_id": job_id},
        )
    except Exception:
        pass

    if isinstance(proj.meta, dict):
        outputs = proj.meta.setdefault("outputs", {})
        videos = outputs.setdefault("videos", [])
        videos.append({"kind": "layered_animation", "path": video_rel, "mode": mode})
        proj.meta["last_layered_animation"] = {**res, "video": video_rel}
        store.save(proj)

    jobs.update_progress(
        project_id, job_id, stage="complete", current=1, total=1,
        message=f"Layered animation complete ({res.get('segmentation')})",
    )
    return {**res, "video": video_rel}


@app.post("/v1/projects/{project_id}/render/animate_layers")
def render_animate_layers(project_id: str, req: LayeredAnimateRequest):
    """Animate individual objects/regions within an image (parallax / masked / segment).

    Model-free compositing path; runs without a diffusion model or GPU.
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    source_path = _resolve_project_reference_path(project_id, req.source_asset)
    if source_path is None:
        raise UserFacingError(
            "Source image not found",
            hint="Upload an image under Render → References, then pass its path as source_asset.",
            code="ASSET_MISSING",
            status_code=400,
        )
    if req.mode == "masked" and not req.masks:
        raise UserFacingError(
            "Masked mode requires at least one mask",
            hint="Add a mask asset, or use parallax/segment modes.",
            code="MASK_REQUIRED",
            status_code=400,
        )

    if req.diffusion_refine:
        _resolve_layered_refinement(
            {
                "model_id": req.model_id,
                "device_preference": req.device_preference,
            }
        )

    profile = str(req.motion or "full_3d")
    schedule = autoconfig.build_motion_schedule(profile, duration_s=req.duration_s, fps=req.fps)
    payload = {
        "source_asset": req.source_asset,
        "mode": req.mode,
        "motion_profile": profile,
        "motion_schedule": schedule,
        "bands": int(req.bands),
        "masks": [m.model_dump() for m in req.masks],
        "subject_motion": float(req.subject_motion),
        "background_motion": float(req.background_motion),
        "fps": int(req.fps),
        "duration_s": float(req.duration_s),
        "width": int(req.width),
        "height": int(req.height),
        "include_audio": bool(req.include_audio),
        "diffusion_refine": bool(req.diffusion_refine),
        "model_id": str(req.model_id or "auto"),
        "device_preference": str(req.device_preference or "auto"),
        "refine_prompt": req.refine_prompt,
        "refine_negative": req.refine_negative,
        "refine_denoise": float(req.refine_denoise),
        "refine_steps": int(req.refine_steps),
        "refine_cfg": float(req.refine_cfg),
        "seed": req.seed,
    }
    job = jobs.create(project_id, "layered_animation", payload)
    job.progress = {
        "stage": "queued",
        "current": 0,
        "total": max(1, int(req.duration_s * req.fps) + 1),
        "percent": 0.0,
        "message": f"Queued {req.mode} object animation",
    }
    jobs.save(job)
    if isinstance(proj.meta, dict):
        proj.meta.setdefault("jobs", []).append(job.__dict__)
        store.save(proj)
    return {"ok": True, "job": job.__dict__, "animation_mode": req.mode, "motion_schedule": schedule}


@app.get("/v1/projects/{project_id}/assets")
def list_assets(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    assets = {"audio": [], "refs": []}
    audio_dir = pdir / "assets" / "audio"
    if audio_dir.exists():
        for p in sorted(audio_dir.glob("*") ):
            if p.is_file():
                assets["audio"].append({"path": str(p.relative_to(pdir))})
    refs_dir = pdir / "assets" / "refs"
    if refs_dir.exists():
        for p in sorted(refs_dir.glob("*") ):
            if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
                assets["refs"].append({"path": str(p.relative_to(pdir))})
    return {"project_id": project_id, "assets": assets}


if HAS_MULTIPART:
    @app.post("/v1/projects/{project_id}/assets/refs")
    async def upload_ref(project_id: str, file: UploadFile = File(...)):
        proj = store.get(project_id)
        if not proj:
            raise HTTPException(404, "Project not found")
        pdir = store.project_dir(project_id)
        refs_dir = pdir / "assets" / "refs"
        refs_dir.mkdir(parents=True, exist_ok=True)
        name = _safe_upload_filename(file.filename, "ref.png")
        out = refs_dir / name
        data = await file.read()
        out.write_bytes(data)
        proj.meta.setdefault("assets", {}).setdefault("refs", []).append(str(out.relative_to(pdir)))
        store.save(proj)
        return {"ok": True, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/refs")
    async def upload_ref(project_id: str):
        _require_multipart()


@app.get("/v1/projects/{project_id}/export/comfyui_workflows")
def export_comfyui_workflows(
    project_id: str,
    variant_index: int = 0,
    model_id: str | None = None,
    workflow_family: str = "auto",
    source_asset: str | None = None,
    reference_asset: str | None = None,
    inpaint_mask: str | None = None,
    controlnet_model: str | None = None,
    conditioning_mode: str = "raw",
    width: int | None = None,
    height: int | None = None,
    steps: int | None = None,
    cfg: float | None = None,
    sampler: str | None = None,
    negative_prompt: str | None = None,
    seed: int | None = None,
    denoise_strength: float | None = None,
    loras_json: str | None = None,
    outpaint_json: str | None = None,
    controlnet_units_json: str | None = None,
    hires_fix_json: str | None = None,
    refiner_json: str | None = None,
    upscaler: str | None = None,
):
    """Compile plan scenes into per-scene ComfyUI workflow JSON files."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    if variant_index < 0 or variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")
    variant = variants[variant_index]
    scenes = variant.get("scenes") or []
    if not scenes:
        raise HTTPException(400, "Selected variant has no scenes")

    out_dir = store.project_dir(project_id) / "outputs" / "comfyui_workflows" / f"variant_{variant_index:02d}"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_files = []
    loras = []
    if loras_json:
        try:
            parsed_loras = json.loads(loras_json)
        except Exception:
            parsed_loras = []
        loras = _normalize_render_loras(parsed_loras)

    raw_controlnet_units: list[dict[str, Any]] = []
    if controlnet_units_json:
        try:
            parsed_units = json.loads(controlnet_units_json)
        except Exception:
            parsed_units = []
        if isinstance(parsed_units, list):
            raw_controlnet_units = [dict(item) for item in parsed_units if isinstance(item, dict)]
    parsed_outpaint = None
    if outpaint_json:
        try:
            parsed_outpaint = json.loads(outpaint_json)
        except Exception:
            raise UserFacingError(
                "Invalid outpaint settings",
                hint="Retry the export after re-entering the outpaint margins.",
                code="OUTPAINT_INVALID",
                status_code=400,
            )
    parsed_hires_fix = None
    if hires_fix_json:
        try:
            parsed_hires_fix = json.loads(hires_fix_json)
        except Exception:
            raise UserFacingError(
                "Invalid hires-fix settings",
                hint="Retry the export after re-entering the hires-fix controls.",
                code="HIRES_FIX_INVALID",
                status_code=400,
            )
    parsed_refiner = None
    if refiner_json:
        try:
            parsed_refiner = json.loads(refiner_json)
        except Exception:
            raise UserFacingError(
                "Invalid refiner settings",
                hint="Retry the export after re-entering the refiner controls.",
                code="REFINER_INVALID",
                status_code=400,
            )
    if isinstance(parsed_refiner, dict):
        refiner_model = str(parsed_refiner.get("model") or "").strip()
        if refiner_model:
            parsed_refiner["checkpoint"] = _resolve_optional_comfy_asset_name(
                refiner_model,
                folder="checkpoints",
                allowed_kinds={"checkpoint"},
            )
    if workflow_family == "controlnet" and not raw_controlnet_units and controlnet_model and reference_asset:
        raw_controlnet_units = [
            {
                "model": controlnet_model,
                "reference_asset": reference_asset,
                "conditioning_mode": conditioning_mode,
                "strength": 0.8,
            }
        ]

    selection = _resolve_still_scene_selection(
        model_id=model_id,
        checkpoint=None,
        workflow_family=workflow_family,
        controlnet_model=controlnet_model,
        reference_asset=reference_asset,
        conditioning_mode=conditioning_mode,
        controlnet_units=raw_controlnet_units,
    )

    if str(selection.get("engine") or "comfyui") != "comfyui":
        raise UserFacingError(
            "ComfyUI workflow export only supports ComfyUI still models.",
            hint="Pick a checkpoint-based still model before exporting ComfyUI workflows.",
            code="EXPORT_ENGINE_UNSUPPORTED",
            status_code=400,
        )

    workflow_kind = str(selection.get("workflow_family") or "txt2img")
    controlnet_units = _normalize_controlnet_units(
        raw_controlnet_units,
        engine="comfyui",
        family=selection.get("family"),
    )
    if workflow_kind == "controlnet" and not controlnet_units:
        raise UserFacingError(
            "No compatible ControlNet units were selected",
            hint="Attach one or more compatible ControlNet units before exporting the workflow.",
            code="CONTROLNET_MISSING",
            status_code=400,
        )

    def _copy_export_asset(src: Path, folder: str) -> str:
        target_dir = out_dir / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / src.name
        if src.resolve() != target.resolve():
            shutil.copy2(src, target)
        return str(Path(folder) / target.name).replace("\\", "/")

    prepared_assets = {
        "source_path": None,
        "mask_path": None,
        "mask_source": None,
        "outpaint": None,
        "width": int(width or 0),
        "height": int(height or 0),
    }
    if workflow_kind in {"img2img", "inpaint", "outpaint"}:
        prepared_assets = _prepare_still_scene_assets(
            project_id,
            {
                "source_asset": source_asset,
                "reference_asset": reference_asset,
                "inpaint_mask": inpaint_mask,
                "outpaint": parsed_outpaint,
                "width": width,
                "height": height,
            },
            workflow_kind,
        )

    exported_source_image = (
        _copy_export_asset(Path(str(prepared_assets["source_path"])), "inputs")
        if prepared_assets.get("source_path")
        else None
    )
    exported_mask_image = (
        _copy_export_asset(Path(str(prepared_assets["mask_path"])), "masks")
        if prepared_assets.get("mask_path")
        else None
    )

    exported_controlnet_units: list[dict[str, Any]] = []
    if workflow_kind == "controlnet":
        for unit in controlnet_units:
            ref_path = _resolve_project_reference_path(project_id, str(unit.get("reference_asset") or ""))
            if ref_path is None:
                raise UserFacingError(
                    "Reference image not found",
                    hint="Upload or choose a valid project reference image before exporting the ControlNet workflow.",
                    code="REFERENCE_IMAGE_NOT_FOUND",
                    status_code=400,
                )
            conditioned = _prepare_condition_image(project_id, ref_path, str(unit.get("conditioning_mode") or "raw"))
            exported_controlnet_units.append(
                {
                    **unit,
                    "reference_image": _copy_export_asset(conditioned, "refs"),
                }
            )

    for idx, sc in enumerate(scenes):
        checkpoint = str(selection.get("checkpoint") or sc.get("checkpoint") or settings.comfyui_checkpoint)
        resolved_seed = int(seed if seed is not None else (sc.get("seed") or (idx + 12345)))
        resolved_width = int(prepared_assets.get("width") or width or sc.get("width") or 768)
        resolved_height = int(prepared_assets.get("height") or height or sc.get("height") or 432)
        resolved_steps = int(steps or sc.get("steps") or 20)
        resolved_cfg = float(cfg if cfg is not None else (sc.get("cfg") or 6.5))
        resolved_sampler = str(sampler or sc.get("sampler") or "euler")
        resolved_negative = str(negative_prompt or sc.get("negative_prompt") or "(low quality, worst quality)")
        resolved_denoise = float(denoise_strength if denoise_strength is not None else 0.75)

        if workflow_kind == "controlnet":
            wf = comfy.controlnet_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                controlnet_name=str(exported_controlnet_units[0].get("controlnet_name") or selection.get("controlnet_name") or ""),
                reference_image=str(exported_controlnet_units[0].get("reference_image") or "reference.png"),
                controlnet_strength=0.8,
                start_percent=float(exported_controlnet_units[0].get("start_percent", 0.0) if exported_controlnet_units else 0.0),
                end_percent=float(exported_controlnet_units[0].get("end_percent", 1.0) if exported_controlnet_units else 1.0),
                filename_prefix=f"scene_{idx:03d}",
                loras=loras,
                controlnet_units=exported_controlnet_units,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        elif workflow_kind == "img2img":
            wf = comfy.img2img_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                source_image=str(exported_source_image or "source.png"),
                denoise_strength=resolved_denoise,
                filename_prefix=f"scene_{idx:03d}",
                loras=loras,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        elif workflow_kind == "inpaint":
            wf = comfy.inpaint_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                source_image=str(exported_source_image or "source.png"),
                mask_image=str(exported_mask_image or "mask.png"),
                denoise_strength=float(denoise_strength if denoise_strength is not None else 0.8),
                filename_prefix=f"scene_{idx:03d}",
                loras=loras,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        elif workflow_kind == "outpaint":
            wf = comfy.outpaint_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                source_image=str(exported_source_image or "source.png"),
                mask_image=str(exported_mask_image or "mask.png"),
                denoise_strength=float(denoise_strength if denoise_strength is not None else 0.8),
                filename_prefix=f"scene_{idx:03d}",
                loras=loras,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        else:
            wf = comfy.default_workflow(
                checkpoint=checkpoint,
                prompt=str(sc.get("prompt") or ""),
                negative_prompt=resolved_negative,
                seed=resolved_seed,
                width=resolved_width,
                height=resolved_height,
                steps=resolved_steps,
                cfg=resolved_cfg,
                sampler=resolved_sampler,
                loras=loras,
                hires_fix=parsed_hires_fix,
                refiner=parsed_refiner,
                upscaler=upscaler,
            )
        p = out_dir / f"scene_{idx:03d}.json"
        p.write_text(json.dumps(wf, ensure_ascii=False, indent=2), encoding="utf-8")
        out_files.append(str(p.relative_to(store.project_dir(project_id))))

    proj.meta.setdefault("exports", {}).setdefault("comfyui", []).extend(out_files)
    store.save(proj)
    return {"ok": True, "files": out_files}

@app.post("/v1/projects/{project_id}/assemble_video")
def assemble_video(project_id: str, req: AssembleVideoRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[req.variant_index]
    scenes = variant.get("scenes") or []

    # Revalidate the identifier loaded from the persisted project before path
    # construction. Keeping the request value out of this downstream path also
    # makes the ProjectStore trust boundary explicit to static analysis.
    pdir = store.project_dir(proj.id)
    audio_meta = proj.meta.get("audio")
    audio_path = None
    if audio_meta:
        audio_path = pdir / "assets" / "audio" / audio_meta["filename"]

    # Prefer motion clips if available
    clips_dir = pdir / "outputs" / "clips"
    clips = []
    if clips_dir.exists():
        clips = sorted([p for p in clips_dir.glob(f"v{req.variant_index:02d}_scene*.mp4") if p.is_file()])

    out_vid = pdir / "outputs" / "videos" / f"variant_{req.variant_index:02d}.mp4"
    out_vid.parent.mkdir(parents=True, exist_ok=True)

    if clips:
        # Assemble motion clips, then optionally interpolate FPS, then mux audio.
        raw_vid = out_vid.parent / f"{out_vid.stem}_raw.mp4"
        concat_videos(
            ffmpeg_path=settings.ffmpeg_path,
            video_paths=clips,
            out_mp4=raw_vid,
            audio_path=None
        )
        # Interpolate to requested FPS (best effort: RIFE -> minterpolate -> fps dup).
        interp_vid = out_vid.parent / f"{out_vid.stem}_interp_{req.fps}fps.mp4"
        interpolate_video_fps(
            ffmpeg_path=settings.ffmpeg_path,
            in_mp4=raw_vid,
            out_mp4=interp_vid,
            fps_out=int(req.fps),
            engine=os.getenv("EDMG_INTERPOLATION_ENGINE", "auto"),
        )
        if audio_path and audio_path.exists():
            mux_audio(ffmpeg_path=settings.ffmpeg_path, video_mp4=interp_vid, audio_path=audio_path, out_mp4=out_vid)
        else:
            out_vid.write_bytes(interp_vid.read_bytes())
        mode = "motion"
    else:
        out_images_dir = pdir / "outputs" / "images"
        imgs = sorted([p for p in out_images_dir.glob(f"v{req.variant_index:02d}_scene*") if p.suffix.lower() in (".png",".jpg",".jpeg",".webp")])
        if not imgs:
            raise HTTPException(400, "No rendered scene images found. Render scenes or motion scenes first.")

        durations = []
        for i in range(len(imgs)):
            if i < len(scenes):
                start = float(scenes[i].get("start_s", i*5))
                end = float(scenes[i].get("end_s", start+5))
                durations.append(max(0.5, end-start))
            else:
                durations.append(5.0)

        assemble_slideshow(
            ffmpeg_path=settings.ffmpeg_path,
            image_paths=imgs,
            durations_s=durations,
            out_mp4=out_vid,
            audio_path=audio_path,
            fps=req.fps
        )
        mode = "slideshow"

    proj.meta.setdefault("outputs", {}).setdefault("videos", []).append(str(out_vid.relative_to(pdir)))
    store.save(proj)

    return {"ok": True, "mode": mode, "video": str(out_vid)}



def _scene_schedule_to_prompts(variant: dict[str, Any], fps: int) -> dict[str, str]:
    scenes = variant.get("scenes") or []
    prompts: dict[str, str] = {}
    for i, sc in enumerate(scenes):
        start_s = float(sc.get("start_s", i * 5))
        frame = max(0, int(round(start_s * fps)))
        prompts[str(frame)] = str(sc.get("prompt") or "").strip() or "cinematic"
    if not prompts:
        prompts["0"] = "cinematic"
    return prompts


def _safe_export_bundle_stem(value: str, fallback: str) -> str:
    stem = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return stem[:80] or fallback


UNREAL_RETURN_VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm"}
UNREAL_RETURN_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".webp"}


def _read_json_dict(path: Path) -> dict[str, Any]:
    if not path.exists() or not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _unique_output_artifact_path(target_dir: Path, base_stem: str, suffix: str) -> Path:
    candidate = target_dir / f"{base_stem}{suffix}"
    if not candidate.exists():
        return candidate
    counter = 2
    while True:
        candidate = target_dir / f"{base_stem}_{counter}{suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def _build_unreal_return_metadata(
    *,
    project_id: str,
    output_path: Path,
    bundle_dir: str,
    source_path: str,
    media_kind: str,
    variant_index: int,
    sequence_name: str,
    manifest_path: str | None,
    source_manifest: dict[str, Any] | None,
    return_contract: dict[str, Any] | None,
    render_handoff: dict[str, Any] | None,
) -> dict[str, Any]:
    rel_output = _project_relative_path(project_id, output_path)
    source_bundle = source_manifest if isinstance(source_manifest, dict) else {}
    contract = return_contract if isinstance(return_contract, dict) else {}
    handoff = render_handoff if isinstance(render_handoff, dict) else {}
    return {
        "kind": "unreal_bridge_return",
        "project_id": project_id,
        "variant_index": int(variant_index or 0),
        "sequence_name": sequence_name,
        "bundle_dir": bundle_dir,
        "source_path": source_path,
        "manifest_path": manifest_path,
        "media_kind": media_kind,
        "output": {media_kind: rel_output},
        "source_bundle": {
            "export_family": str(source_bundle.get("export_family") or "unreal_bridge_bundle"),
            "created_at": source_bundle.get("created_at"),
        },
        "return_contract": {
            "return_owner": str(contract.get("return_owner") or "studio"),
            "assembly_mode": str(contract.get("assembly_mode") or "ffmpeg_back_in_studio"),
            "expected_outputs": list(contract.get("expected_outputs") or []),
        },
        "render_handoff": {
            "render_mode": str(handoff.get("render_mode") or ""),
            "return_owner": str(handoff.get("return_owner") or "studio"),
            "approved_section_ids": list(handoff.get("approved_section_ids") or []),
        },
        "captured_at": time.time(),
    }

@app.post("/v1/projects/{project_id}/export/deforum")
def export_deforum(project_id: str, req: ExportDeforumRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    variant = variants[req.variant_index]
    safe_preset = str(req.preset or "cinematic")
    if safe_preset not in {"cinematic", "psychedelic", "ambient"}:
        safe_preset = "cinematic"
    creative_payload = _build_creative_direction_payload(
        proj,
        variant_index=req.variant_index,
        preset=safe_preset,
        sensitivity=float(req.sensitivity or 1.0),
    )
    preview_settings = (
        creative_payload.get("deforum_preview", {}).get("settings")
        if isinstance(creative_payload.get("deforum_preview"), dict)
        else {}
    )
    prompts = (
        dict(preview_settings.get("prompts") or {})
        if isinstance(preview_settings, dict) and isinstance(preview_settings.get("prompts"), dict)
        else _scene_schedule_to_prompts(variant, fps=req.fps)
    )

    # Use EDMG Core template if available; otherwise minimal
    try:
        from enhanced_deforum_music_generator.public_api import DeforumMusicGenerator, AudioAnalysis  # type: ignore
        gen = DeforumMusicGenerator()
        analysis = AudioAnalysis()
        settings_dict = gen.build_deforum_settings(analysis, {
            "W": req.width,
            "H": req.height,
            "fps": req.fps,
            "base_prompt": prompts.get("0", "cinematic"),
            "style_prompt": "",
        })
        settings_dict["prompts"] = prompts
    except Exception:
        settings_dict = {
            "W": req.width,
            "H": req.height,
            "fps": req.fps,
            "prompts": prompts,
            "note": "Install EDMG Core for full Deforum template output."
        }

    if isinstance(preview_settings, dict):
        for key in (
            "negative_prompts",
            "zoom",
            "angle",
            "translation_z",
            "cfg_scale_schedule",
            "strength_schedule",
            "contrast_schedule",
            "schedules",
        ):
            value = preview_settings.get(key)
            if value:
                settings_dict[key] = value
    settings_dict["W"] = req.width
    settings_dict["H"] = req.height
    settings_dict["fps"] = req.fps

    out_dir = store.project_dir(project_id) / "outputs" / "deforum"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"variant_{req.variant_index:02d}.deforum.json"
    out_path.write_text(json.dumps(settings_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    rel = str(out_path.relative_to(store.project_dir(project_id)))
    proj.meta.setdefault("exports", {}).setdefault("deforum", []).append(rel)
    store.save(proj)

    return {"ok": True, "path": rel}


@app.post("/v1/projects/{project_id}/export/unreal")
def export_unreal_bridge_bundle(project_id: str, req: ExportUnrealBridgeRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")
    variants = plan["variants"]
    if req.variant_index < 0 or req.variant_index >= len(variants):
        raise HTTPException(400, "variant_index out of range")

    preview = UnrealBridgePreviewResponse.model_validate(
        build_unreal_bridge_preview(
            project_id=str(proj.id),
            project_name=str(getattr(proj, "name", "") or "") or None,
            analysis=(proj.meta.get("analysis") or {}) if isinstance(proj.meta, dict) else {},
            plan=plan if isinstance(plan, dict) else {},
            timeline=(proj.meta.get("timeline") or {}) if isinstance(proj.meta, dict) else {},
            variant_index=int(req.variant_index or 0),
        )
    )
    visual_dna = _load_project_visual_dna(proj)
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")
    stamp = time.strftime("%Y%m%d_%H%M%S")
    base_stem = _safe_export_bundle_stem(
        str(req.bundle_name or f"variant_{int(req.variant_index or 0):02d}_unreal_{stamp}"),
        f"unreal_bundle_{stamp}",
    )

    pdir = store.project_dir(project_id)
    out_root = pdir / "outputs" / "unreal"
    out_root.mkdir(parents=True, exist_ok=True)
    bundle_dir = out_root / base_stem
    suffix = 2
    while bundle_dir.exists():
        bundle_dir = out_root / f"{base_stem}_{suffix}"
        suffix += 1
    bundle_dir.mkdir(parents=True, exist_ok=True)

    payloads = build_unreal_bridge_export_payloads(
        project_id=str(proj.id),
        project_name=str(getattr(proj, "name", "") or "") or None,
        variant_index=int(req.variant_index or 0),
        preview=preview.model_dump(mode="json"),
        analysis=(proj.meta.get("analysis") or {}) if isinstance(proj.meta, dict) else {},
        visual_dna=visual_dna.model_dump(mode="json"),
        created_at=created_at,
    )
    for relative_name, payload in payloads.items():
        target = bundle_dir / relative_name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    zip_rel: str | None = None
    zip_path: Path | None = None
    if req.include_zip:
        zip_path = out_root / f"{bundle_dir.name}.zip"
        with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for file_path in sorted(bundle_dir.rglob("*")):
                if file_path.is_file():
                    archive.write(file_path, arcname=str(file_path.relative_to(bundle_dir)))
        zip_rel = zip_path.relative_to(pdir).as_posix()

    manifest_rel = (bundle_dir / "bundle_manifest.json").relative_to(pdir).as_posix()
    bundle_rel = bundle_dir.relative_to(pdir).as_posix()
    export_entry = {
        "bundle_dir": bundle_rel,
        "manifest_path": manifest_rel,
        "zip_path": zip_rel,
        "created_at": created_at,
        "variant_index": int(req.variant_index or 0),
        "sequence_name": str(preview.shot_metadata_export.sequence_name),
        "files": sorted(payloads.keys()),
    }
    proj.meta.setdefault("exports", {}).setdefault("unreal", []).append(export_entry)
    store.save(proj)

    return {
        "ok": True,
        "bundle": export_entry,
    }


@app.post("/v1/projects/{project_id}/import/unreal")
def import_unreal_bridge_return(project_id: str, req: ImportUnrealBridgeReturnRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    pdir = store.project_dir(project_id)
    try:
        bundle_dir = safe_join(pdir, req.bundle_dir)
    except Exception:
        raise HTTPException(400, "Invalid bundle_dir")
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise HTTPException(404, "Unreal bundle not found")

    bundle_rel = bundle_dir.relative_to(pdir).as_posix()
    source_rel = str(req.source_dir or f"{bundle_rel}/returned").replace("\\", "/").strip()
    try:
        source_dir = safe_join(pdir, source_rel)
    except Exception:
        raise HTTPException(400, "Invalid source_dir")
    if not source_dir.exists() or not source_dir.is_dir():
        raise HTTPException(400, "Returned media folder not found")

    bundle_manifest_path = bundle_dir / "bundle_manifest.json"
    return_contract_path = bundle_dir / "return_contract.json"
    render_handoff_path = bundle_dir / "render_handoff.json"
    source_manifest = _read_json_dict(bundle_manifest_path)
    return_contract = _read_json_dict(return_contract_path)
    render_handoff = _read_json_dict(render_handoff_path)
    try:
        variant_index = int(source_manifest.get("variant_index") or 0)
    except Exception:
        variant_index = 0
    sequence_name = str(source_manifest.get("sequence_name") or bundle_dir.name)
    created_at = time.strftime("%Y-%m-%d %H:%M:%S")

    imported_media: list[dict[str, Any]] = []
    for source_path in sorted(source_dir.rglob("*")):
        if not source_path.is_file():
            continue
        suffix = source_path.suffix.lower()
        media_kind = ""
        if suffix in UNREAL_RETURN_VIDEO_EXTENSIONS:
            media_kind = "video"
        elif suffix in UNREAL_RETURN_IMAGE_EXTENSIONS:
            media_kind = "image"
        if not media_kind:
            continue

        target_root = pdir / "outputs" / ("videos" if media_kind == "video" else "images")
        target_root.mkdir(parents=True, exist_ok=True)
        base_stem = _safe_export_bundle_stem(
            f"{bundle_dir.name}_{source_path.stem}",
            f"unreal_return_{media_kind}",
        )
        target_path = _unique_output_artifact_path(target_root, base_stem, suffix)
        shutil.copy2(source_path, target_path)

        source_rel_path = source_path.relative_to(pdir).as_posix()
        manifest_rel = bundle_manifest_path.relative_to(pdir).as_posix() if bundle_manifest_path.exists() else None
        metadata = _build_unreal_return_metadata(
            project_id=project_id,
            output_path=target_path,
            bundle_dir=bundle_rel,
            source_path=source_rel_path,
            media_kind=media_kind,
            variant_index=variant_index,
            sequence_name=sequence_name,
            manifest_path=manifest_rel,
            source_manifest=source_manifest,
            return_contract=return_contract,
            render_handoff=render_handoff,
        )
        metadata_path = _write_generation_metadata(target_path, metadata)
        imported_media.append(
            {
                "kind": media_kind,
                "path": target_path.relative_to(pdir).as_posix(),
                "source_path": source_rel_path,
                "metadata_path": metadata_path.relative_to(pdir).as_posix(),
            }
        )

    if not imported_media:
        raise HTTPException(400, "No importable media found in returned folder")

    return_entry = {
        "bundle_dir": bundle_rel,
        "source_dir": source_rel,
        "manifest_path": bundle_manifest_path.relative_to(pdir).as_posix() if bundle_manifest_path.exists() else None,
        "return_contract_path": return_contract_path.relative_to(pdir).as_posix() if return_contract_path.exists() else None,
        "created_at": created_at,
        "variant_index": variant_index,
        "sequence_name": sequence_name,
        "media": imported_media,
    }
    proj.meta.setdefault("exports", {}).setdefault("unreal_returns", []).append(return_entry)
    store.save(proj)

    return {
        "ok": True,
        "imported": return_entry,
    }


@app.post("/v1/projects/{project_id}/unreal/import-plan")
def build_unreal_bridge_import_plan(project_id: str, req: BuildUnrealImportPlanRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")

    pdir = store.project_dir(project_id)
    try:
        bundle_dir = safe_join(pdir, req.bundle_dir)
    except Exception:
        raise HTTPException(400, "Invalid bundle_dir")
    if not bundle_dir.exists() or not bundle_dir.is_dir():
        raise HTTPException(404, "Unreal bundle not found")

    try:
        plan = build_unreal_sequence_import_plan(
            bundle_dir,
            content_path=req.content_path,
            asset_name=req.asset_name,
        )
    except ValueError as exc:
        logger.warning("Unreal import bundle validation failed", exc_info=True)
        raise HTTPException(400, "Unreal bundle validation failed") from exc

    plan_path = bundle_dir / "unreal_import_plan.json"
    write_unreal_sequence_import_plan(plan, plan_path)
    plan_rel = plan_path.relative_to(pdir).as_posix()

    return {
        "ok": True,
        "plan_path": plan_rel,
        "plan": plan.to_dict(),
    }

@app.get("/v1/projects/{project_id}/outputs")
def list_outputs(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)

    def _file_entry(fp: Path) -> dict[str, Any]:
        try:
            st = fp.stat()
            entry = {
                "path": str(fp.relative_to(pdir)),
                "name": fp.name,
                "size_bytes": int(st.st_size),
                "modified_at": float(st.st_mtime),
            }
            metadata_path = _output_metadata_path(fp)
            if metadata_path.exists():
                entry["metadata_path"] = str(metadata_path.relative_to(pdir))
                try:
                    entry["metadata"] = json.loads(metadata_path.read_text(encoding="utf-8"))
                except Exception:
                    entry["metadata_error"] = "invalid_json"
            return entry
        except Exception:
            return {"path": str(fp.relative_to(pdir)), "name": fp.name}

    imgs = []
    vids = []
    defs = []
    unreal_exports = []
    for p in sorted((pdir / "outputs" / "images").glob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            imgs.append(_file_entry(p))
    # Render workers may group supporting frames and the final clip under a
    # job-specific directory (for example layered_<job-id>/parallax_animation.mp4).
    # Return those completed videos too so Timeline's media library can edit
    # every genuine Studio output, not only files placed at the directory root.
    video_root = pdir / "outputs" / "videos"
    video_files = [
        path
        for path in video_root.rglob("*")
        if path.is_file() and path.suffix.lower() in UNREAL_RETURN_VIDEO_EXTENSIONS
    ]
    for p in sorted(video_files, key=lambda x: x.stat().st_mtime, reverse=True):
        entry = _file_entry(p)
        name = p.name
        metadata_kind = str((entry.get("metadata") or {}).get("kind") or "")
        if metadata_kind == "unreal_bridge_return":
            entry["kind"] = "unreal_bridge_return"
        elif name.endswith("_raw.mp4"):
            entry["kind"] = "internal_raw"
        elif name.endswith("_interp.mp4"):
            entry["kind"] = "internal_interp"
        elif name.startswith("internal_v"):
            entry["kind"] = "internal_final"
        else:
            entry["kind"] = "video"
        vids.append(entry)
    for p in sorted((pdir / "outputs" / "deforum").glob("*.json"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        defs.append(_file_entry(p))
    raw_unreal_exports = []
    raw_unreal_returns = []
    if isinstance(proj.meta, dict):
        raw_exports = proj.meta.get("exports") if isinstance(proj.meta.get("exports"), dict) else {}
        raw_unreal_exports = raw_exports.get("unreal") if isinstance(raw_exports.get("unreal"), list) else []
        raw_unreal_returns = raw_exports.get("unreal_returns") if isinstance(raw_exports.get("unreal_returns"), list) else []
    for raw in reversed(raw_unreal_exports):
        if not isinstance(raw, dict):
            continue
        entry = {
            "bundle_dir": str(raw.get("bundle_dir") or ""),
            "manifest_path": str(raw.get("manifest_path") or ""),
            "zip_path": str(raw.get("zip_path") or "") or None,
            "created_at": str(raw.get("created_at") or ""),
            "variant_index": int(raw.get("variant_index") or 0),
            "sequence_name": str(raw.get("sequence_name") or ""),
            "files": list(raw.get("files") or []),
        }
        manifest_rel = entry["manifest_path"]
        if manifest_rel:
            try:
                manifest_path = safe_join(pdir, manifest_rel)
                if manifest_path.exists() and manifest_path.is_file():
                    entry["manifest_file"] = _file_entry(manifest_path)
                    entry["manifest"] = json.loads(manifest_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        zip_rel = entry["zip_path"]
        if zip_rel:
            try:
                zip_file = safe_join(pdir, zip_rel)
                if zip_file.exists() and zip_file.is_file():
                    entry["zip_file"] = _file_entry(zip_file)
            except Exception:
                pass
        bundle_rel = entry["bundle_dir"]
        if bundle_rel:
            try:
                bundle_path = safe_join(pdir, bundle_rel)
                import_plan_path = bundle_path / "unreal_import_plan.json"
                if import_plan_path.exists() and import_plan_path.is_file():
                    entry["import_plan_path"] = import_plan_path.relative_to(pdir).as_posix()
                    entry["import_plan_file"] = _file_entry(import_plan_path)
                    entry["import_plan"] = json.loads(import_plan_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        unreal_exports.append(entry)
    unreal_returns = []
    for raw in reversed(raw_unreal_returns):
        if not isinstance(raw, dict):
            continue
        entry = {
            "bundle_dir": str(raw.get("bundle_dir") or ""),
            "source_dir": str(raw.get("source_dir") or ""),
            "manifest_path": str(raw.get("manifest_path") or "") or None,
            "return_contract_path": str(raw.get("return_contract_path") or "") or None,
            "created_at": str(raw.get("created_at") or ""),
            "variant_index": int(raw.get("variant_index") or 0),
            "sequence_name": str(raw.get("sequence_name") or ""),
            "media": [],
        }
        for raw_media in list(raw.get("media") or []):
            if not isinstance(raw_media, dict):
                continue
            media_path = str(raw_media.get("path") or "")
            if not media_path:
                continue
            media_entry: dict[str, Any]
            try:
                media_entry = _file_entry(safe_join(pdir, media_path))
            except Exception:
                media_entry = {"path": media_path, "name": Path(media_path).name}
            media_entry["kind"] = str(raw_media.get("kind") or media_entry.get("kind") or "")
            source_path = str(raw_media.get("source_path") or "")
            if source_path:
                media_entry["source_path"] = source_path
            metadata_path = str(raw_media.get("metadata_path") or "")
            if metadata_path:
                media_entry["metadata_path"] = metadata_path
            entry["media"].append(media_entry)
        unreal_returns.append(entry)

    latest_internal = proj.meta.get("last_internal_render") or None
    history = proj.meta.get("internal_render_history") or []
    project_jobs = jobs.list_for_project(project_id)
    active_internal_jobs = [
        j.__dict__
        for j in project_jobs
        if j.type == "internal_video" and j.status in ("queued", "paused", "running", "canceled", "failed")
    ][:8]
    return {
        "images": imgs,
        "videos": vids,
        "deforum_exports": defs,
        "unreal_exports": unreal_exports,
        "unreal_returns": unreal_returns,
        "project_id": project_id,
        "latest_internal_render": latest_internal,
        "internal_render_history": history[-20:] if isinstance(history, list) else [],
        "active_internal_jobs": active_internal_jobs,
    }

@app.head("/v1/projects/{project_id}/file", include_in_schema=False)
@app.get("/v1/projects/{project_id}/file")
def get_file(project_id: str, path: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    try:
        fp = safe_join(pdir, path)
    except Exception:
        raise HTTPException(400, "Invalid path")
    if not fp.exists() or not fp.is_file():
        raise HTTPException(404, "File not found")
    return FileResponse(str(fp))

@app.post("/v1/cloud/aws/test")
def cloud_aws_test(req: CloudAwsTestRequest):
    try:
        res = aws_integration.test_credentials(bucket=req.bucket, prefix=req.prefix)
        return {"ok": res.ok, "account": res.account, "region": res.region}
    except Exception as exc:
        logger.exception("AWS credential test failed")
        raise HTTPException(status_code=501, detail="AWS credential test failed") from exc

@app.post("/v1/cloud/aws/bundle")
def cloud_aws_bundle(req: CloudAwsBundleRequest):
    data_dir = settings.data_dir
    out_zip = data_dir / "edmg_studio_bundle.zip"
    with zipfile.ZipFile(out_zip, "w", compression=zipfile.ZIP_DEFLATED) as z:
        for p in data_dir.rglob("*"):
            if p.is_file():
                z.write(p, arcname=str(p.relative_to(data_dir)))

    result = {"ok": True, "bundle_path": str(out_zip)}
    if req.bucket and req.key:
        try:
            up = aws_integration.upload_file_s3(req.bucket, req.key, str(out_zip))
            result["uploaded"] = up
        except Exception:
            logger.exception("AWS bundle upload failed")
            result["upload_error"] = "AWS bundle upload failed"
    return result

@app.post("/v1/cloud/azure/test")
def cloud_azure_test(req: CloudAzureTestRequest):
    try:
        return azure_integration.test_credentials(container=req.container, prefix=req.prefix)
    except Exception as exc:
        logger.exception("Azure credential test failed")
        raise HTTPException(status_code=501, detail="Azure credential test failed") from exc


@app.get("/v1/cloud/hf/status")
def cloud_hf_status():
    try:
        return hf_bucket_integration.describe_status(
            models_dir=settings.models_dir,
            secrets_store=secrets,
        )
    except Exception as exc:
        logger.exception("Hugging Face bucket status check failed")
        raise HTTPException(status_code=501, detail="Hugging Face bucket status check failed") from exc


@app.post("/v1/cloud/hf/test")
def cloud_hf_test(req: CloudHfBucketTestRequest):
    try:
        return hf_bucket_integration.test_credentials(
            bucket=req.bucket,
            prefix=req.prefix,
            models_dir=settings.models_dir,
            secrets_store=secrets,
        )
    except Exception as exc:
        logger.exception("Hugging Face bucket credential test failed")
        raise HTTPException(status_code=501, detail="Hugging Face bucket credential test failed") from exc


def _hf_settings_payload() -> dict[str, Any]:
    cfg = model_cache_settings.get()
    status = hf_bucket_integration.describe_status(
        models_dir=settings.models_dir,
        secrets_store=secrets,
    )
    return {
        "ok": True,
        "settings": {**cfg["hf_bucket"], "storage_mode": cfg.get("storage_mode", "local_cache")},
        "status": status,
        "active_provider": models._model_cache_label() if models.model_cache is not None else None,
        "priority": ["huggingface_bucket", "aws_s3", "azure_blob"],
    }


@app.get("/v1/cloud/hf/settings")
def cloud_hf_settings_get():
    try:
        return _hf_settings_payload()
    except Exception as exc:
        logger.exception("Hugging Face bucket settings read failed")
        raise HTTPException(status_code=501, detail="Hugging Face bucket settings are unavailable") from exc


@app.post("/v1/cloud/hf/settings")
def cloud_hf_settings_set(req: CloudHfBucketSettingsRequest):
    try:
        model_cache_settings.update(req.model_dump(exclude_none=True))
        # UI choice is authoritative for this process, then rebuild the cache so
        # the Hugging Face bucket takes effect (and priority) immediately.
        model_cache_settings.apply_to_env(force=True)
        models.refresh_model_cache()
        return _hf_settings_payload()
    except Exception as exc:
        logger.exception("Hugging Face bucket settings update failed")
        raise HTTPException(status_code=501, detail="Hugging Face bucket settings update failed") from exc


def _resolve_lightning_bundle_output_dir(output_dir: str | None) -> Path:
    raw = str(output_dir or "lightning/lightning_bundle").strip() or "lightning/lightning_bundle"
    requested = Path(raw).expanduser()
    cloud_root = (settings.data_dir / "cloud").resolve()
    resolved = requested.resolve() if requested.is_absolute() else (cloud_root / requested).resolve()
    if not (resolved == cloud_root or resolved.is_relative_to(cloud_root)):
        raise HTTPException(400, "Lightning bundle output must stay under Studio data/cloud.")
    return resolved


@app.post("/v1/cloud/lightning/bundle")
def cloud_lightning_bundle(req: CloudLightningBundleRequest):
    try:
        output_dir = _resolve_lightning_bundle_output_dir(req.output_dir)
        return lightning_integration.generate_lightning_bundle(str(output_dir))
    except Exception as exc:
        if isinstance(exc, HTTPException):
            raise exc
        logger.exception("Lightning bundle generation failed")
        raise HTTPException(500, "Lightning bundle generation failed") from exc

# ------------------------------
# Model Manager (GUI) — routes live in api/routers.create_models_router
# ------------------------------
