from __future__ import annotations

import os
import platform
import mimetypes
import time
import zipfile
import json
import hashlib
import shutil
from copy import deepcopy
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
    import multipart as _multipart  # type: ignore
    HAS_MULTIPART = True
except Exception:
    _multipart = None
    HAS_MULTIPART = False

from .config import Settings
from .schemas import (
    HealthResponse, ProjectCreateRequest, PlanRequest, ApplyPlanRequest,
    RenderScenesRequest, RenderMotionRequest, AssembleVideoRequest, InternalVideoRenderRequest, TimelineUpdateRequest, ExportDeforumRequest,
    CloudAwsTestRequest, CloudAwsBundleRequest, CloudLightningBundleRequest,
)
from .store.projects import ProjectStore
from .store.jobs import JobStore
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
from .services.ffmpeg import assemble_slideshow, assemble_image_sequence, concat_videos, interpolate_video_fps, mux_audio
from .services.internal_video import (
    InternalVideoSettings,
    describe_internal_render_cache,
    describe_proxy_render_cache,
    render_internal_video_variant,
    render_internal_proxy_video_variant,
    render_internal_diffusion_preview_segment,
)
from .services.compositor import apply_timeline_layers
from .integrations import aws as aws_integration
from .integrations import lightning as lightning_integration
from .utils.path import safe_join
from .errors import UserFacingError, hint_from_exception
from .services.model_manager import ModelManager
from .services.secrets import SecretStore
from .services.setup_wizard import (
    SetupTaskManager,
    check_backend_bundle,
    check_ollama,
    download_and_run_ollama_installer,
    pull_ollama_model,
    download_and_extract_portable,
    ComfyPortableProcess,
    check_ffmpeg,
    comfy_portable_installed,
    download_and_install_7zip,
    install_backend_bundle,
    _find_7z_exe,
)

settings = Settings()


class JobCanceled(Exception):
    """Raised when a running job is canceled and should stop promptly."""


settings.data_dir.mkdir(parents=True, exist_ok=True)

store = ProjectStore(settings.data_dir)
jobs = JobStore(store.projects_dir)

# Multi-node ComfyUI pool (supports EDMG_COMFYUI_URLS)
comfy_pool = ComfyUINodePool(settings.load_comfyui_nodes(), default_max_inflight=settings.comfyui_node_concurrency)

# Always-on worker manager
worker = None  # set after _execute_job is defined
ai = build_ai_client(settings.ai_mode, settings.ai_base_url, settings.ai_timeout_s)

setup_tasks = SetupTaskManager()
secrets = SecretStore(settings.data_dir)
models = ModelManager(
    settings.data_dir,
    settings.comfyui_url,
    os.getenv('EDMG_AI_OLLAMA_URL','http://127.0.0.1:11434'),
    secrets=secrets,
)

comfy_portable = ComfyPortableProcess()

@asynccontextmanager
async def _app_lifespan(_app: FastAPI):
    if settings.worker_autostart:
        worker.start()
    try:
        yield
    finally:
        try:
            worker.stop()
        except Exception:
            pass


app = FastAPI(title="EDMG Studio Backend", version="1.1.0", lifespan=_app_lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(UserFacingError)
async def _user_facing_error(_req: Request, exc: UserFacingError):
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": exc.to_dict()})


@app.exception_handler(HTTPException)
async def _http_exception(_req: Request, exc: HTTPException):
    msg = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
    hint = hint_from_exception(Exception(msg))
    return JSONResponse(status_code=exc.status_code, content={"ok": False, "error": {"message": msg, "hint": hint, "code": "HTTP_ERROR"}})


@app.exception_handler(Exception)
async def _unhandled_exception(_req: Request, exc: Exception):
    msg = str(exc) or "Internal error"
    hint = hint_from_exception(exc) or "Open Render Queue → Log for details, then retry."
    return JSONResponse(status_code=500, content={"ok": False, "error": {"message": msg, "hint": hint, "code": "INTERNAL"}})


def _require_multipart() -> None:
    if not HAS_MULTIPART:
        raise UserFacingError(
            "File upload support is unavailable because python-multipart is not installed.",
            hint="Install backend dependencies with `pip install -e .` or add `python-multipart`, then restart EDMG Studio.",
            code="MISSING_MULTIPART",
            status_code=503,
        )


def _stable_seed(project_id: str, variant_index: int, scene_index: int) -> int:
    h = hashlib.md5(f"{project_id}:{variant_index}:{scene_index}".encode("utf-8")).hexdigest()[:8]
    return int(h, 16)

@app.get("/health", response_model=HealthResponse)
def health():
    return HealthResponse(ok=True)


def _request_payload(model: Any) -> dict[str, Any]:
    dump = getattr(model, "model_dump", None)
    if callable(dump):
        return dump()
    legacy = getattr(model, "dict", None)
    if callable(legacy):
        return legacy()
    raise TypeError(f"Object {type(model)!r} is not a supported request model")


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


def _enqueue_internal_job_from_source(project_id: str, source_job: Any, *, resume_existing_frames: bool, queue_action: str) -> dict[str, Any]:
    payload = deepcopy(getattr(source_job, "payload", None) or {})
    payload["resume_existing_frames"] = bool(resume_existing_frames)
    payload["queue_action"] = str(queue_action)
    payload["source_job_id"] = str(source_job.id)
    if not resume_existing_frames:
        payload["queue_clean_restart"] = True

    preflight = _internal_render_preflight_data(project_id, payload)
    mode = str(preflight.get("mode") or payload.get("render_mode") or "auto")
    model_id = str(preflight.get("model_id") or payload.get("model_id") or ("proxy_draft" if mode == "proxy" else "auto"))
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
            "proxy" if mode == "proxy" else "internal",
            model_id,
            checkpoint,
            queue_action=queue_action,
            source_job_id=str(source_job.id),
            resume_existing_frames=bool(resume_existing_frames),
        ),
    }
    jobs.save(job)
    jobs.append_log(project_id, job.id, f"Queued {queue_action} from job {source_job.id}")
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
    return {"ok": True, "job": job.__dict__, "preflight": preflight, "source_job": source_job.__dict__}


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
            "interpolation_engine": "fps" if backend == "cpu" else "auto",
            "temporal_mode": "keyframes",
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
            "interpolation_engine": "fps",
            "temporal_mode": "off",
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


def _build_internal_render_plan(hw: dict[str, Any] | None = None, *, requested_tier: str = "auto", duration_s: float | None = None) -> dict[str, Any]:
    hw = dict(hw or {})
    backend = str(hw.get("backend") or "cpu").lower()
    backend_family = str(hw.get("backend_family") or ("discrete_gpu" if backend == "cuda" else ("integrated_gpu" if backend == "mps" else "cpu_only"))).lower()
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

    preferred_internal_model = "hf_sdxl_internal" if backend == "cuda" and _tier_rank(applied) >= _tier_rank("quality") else "hf_sd15_internal"
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
        "supports_proxy_render": True,
    }


def _hardware_profile() -> dict[str, Any]:
    """Best-effort local hardware detection used for auto tiering."""
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
            except Exception:
                pass
    except Exception:
        pass

    out["backend_family"] = "discrete_gpu" if out["backend"] == "cuda" else ("integrated_gpu" if out["backend"] == "mps" else "cpu_only")
    plan = _build_internal_render_plan(out, requested_tier="auto")
    out["recommended_tier"] = plan["recommended_tier"]
    out["max_supported_tier"] = plan["max_supported_tier"]
    out["preferred_internal_model"] = plan["preferred_internal_model"]
    out["device_preference"] = plan["device_preference"]
    out["supports_internal_diffusion"] = True
    out["supports_proxy_render"] = True
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

@app.get("/v1/config")
def get_config():
    return {
        "data_dir": str(settings.data_dir),
        "ai_mode": settings.ai_mode,
        "ai_base_url": settings.ai_base_url,
        "ai_timeout_s": settings.ai_timeout_s,
        "ai_provider": os.getenv("EDMG_AI_PROVIDER", "ollama").strip().lower() or "ollama",
        "ai_ollama_url": os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434").strip(),
        "ai_ollama_model": os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen2.5:3b-instruct").strip(),
        "ai_openai_compat_base_url": os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", "http://127.0.0.1:8000").strip(),
        "ai_openai_compat_model": os.getenv("EDMG_AI_OPENAI_COMPAT_MODEL", "qwen2.5-7b-instruct").strip(),
        "ai_openai_compat_api_key_configured": bool(
            secrets.get("openai_compat_api_key") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
        ),
        "comfyui_url": settings.comfyui_url,
        "comfyui_urls": list(settings.resolved_comfyui_urls()),
        "comfyui_node_concurrency": settings.comfyui_node_concurrency,
        "comfyui_checkpoint": settings.comfyui_checkpoint,
        "ffmpeg_path": settings.ffmpeg_path,
        "worker_autostart": settings.worker_autostart,
        "worker_concurrency": settings.worker_concurrency,
        "worker_poll_interval_s": settings.worker_poll_interval_s,
        "secrets_store": secrets.status().store,
    }


@app.get("/v1/settings/secrets/status")
def secrets_status():
    """Return whether optional tokens are configured (never returns the values)."""
    st = secrets.status()
    return {
        "ok": True,
        "store": st.store,
        "available": st.available,
        "has_hf_token": st.has_hf_token,
        "has_civitai_api_key": st.has_civitai_api_key,
        "has_openai_compat_api_key": st.has_openai_compat_api_key,
        "note": st.note,
    }


@app.post("/v1/settings/secrets/set")
def secrets_set(payload: dict[str, Any]):
    name = str((payload or {}).get("name") or "").strip().lower()
    value = str((payload or {}).get("value") or "")
    if name not in ("hf_token", "civitai_api_key", "openai_compat_api_key"):
        raise UserFacingError(
            "Unknown secret",
            hint="Supported: hf_token, civitai_api_key, openai_compat_api_key",
        )
    if not value:
        raise UserFacingError("Missing value", hint="Paste the token/key value, then click Save.")
    secrets.set(name, value)
    return {"ok": True}


@app.post("/v1/settings/secrets/clear")
def secrets_clear(payload: dict[str, Any]):
    name = str((payload or {}).get("name") or "").strip().lower()
    if name not in ("hf_token", "civitai_api_key", "openai_compat_api_key"):
        raise UserFacingError(
            "Unknown secret",
            hint="Supported: hf_token, civitai_api_key, openai_compat_api_key",
        )
    secrets.delete(name)
    return {"ok": True}


def _setup_ai_config() -> dict[str, Any]:
    ai_mode = (settings.ai_mode or "local").strip().lower() or "local"
    ai_provider = (os.getenv("EDMG_AI_PROVIDER", "ollama").strip().lower() or "ollama")
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model = os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen2.5:3b-instruct")
    openai_compat_base_url = os.getenv("EDMG_AI_OPENAI_COMPAT_BASE_URL", "http://127.0.0.1:8000")
    openai_compat_model = os.getenv("EDMG_AI_OPENAI_COMPAT_MODEL", "qwen2.5-7b-instruct")
    openai_compat_api_key_configured = bool(
        secrets.get("openai_compat_api_key") or os.getenv("EDMG_AI_OPENAI_COMPAT_API_KEY")
    )

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

    if ai_provider in ("openai_compat", "openai-compatible", "openai"):
        return {
            "mode": "local",
            "provider": "openai_compat",
            "label": "Local OpenAI-compatible provider",
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


@app.get("/v1/setup/status")
def setup_status():
    """Installer GUI status for required components."""
    ai_config = _setup_ai_config()
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ollama_model = os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen2.5:3b-instruct")
    ollama = check_ollama(ollama_url, ollama_model)

    # ComfyUI availability
    try:
        diag = comfy_pool.diagnose({"checkpoint": settings.comfyui_checkpoint})
        comfy_ok = bool(diag.get("compatible") or diag.get("busy_compatible"))
        comfy_hint = None if comfy_ok else "Install and start ComfyUI (Portable) or ComfyUI Desktop, then ensure it is reachable at the configured URL(s)."
        comfy_status = {
            "ok": comfy_ok,
            "url": settings.resolved_comfyui_urls()[0] if settings.resolved_comfyui_urls() else settings.comfyui_url,
            "checkpoint": settings.comfyui_checkpoint,
            "diagnose": diag,
            "portable_installed": comfy_portable_installed(settings.data_dir),
            "hint": comfy_hint,
        }
    except Exception as e:
        comfy_status = {
            "ok": False,
            "url": settings.comfyui_url,
            "checkpoint": settings.comfyui_checkpoint,
            "portable_installed": comfy_portable_installed(settings.data_dir),
            "error": str(e),
            "hint": "Configure EDMG_COMFYUI_URL to a running ComfyUI instance, or install ComfyUI Portable via this wizard.",
        }

    ff = check_ffmpeg(settings.ffmpeg_path)
    backend_bundle = check_backend_bundle()
    edmg = core_status()
    if not edmg.get("available"):
        edmg.setdefault(
            "hint",
            "Studio backend installs should include EDMG Core by default. Use this wizard to repair the backend environment if Core is missing.",
        )

    
    # 7-Zip CLI (needed to extract some .7z archives, e.g., ComfyUI Portable BCJ2)
    try:
        seven_path = _find_7z_exe(settings.data_dir)
        seven = {"ok": True, "path": seven_path, "hint": None}
    except Exception as e:
        seven = {"ok": False, "path": None, "hint": "Install 7-Zip (recommended) or set EDMG_7Z_PATH / bundle 7z.exe in data/third_party/bin."}

    return {
            "ok": True,
            "ai_config": ai_config,
            "backend_bundle": backend_bundle,
            "ollama": ollama,
            "comfyui": comfy_status,
            "ffmpeg": ff,
            "edmg": edmg,
            "sevenzip": seven,
            "tasks": [t.__dict__ for t in setup_tasks.list()[:10]],
        }


@app.post("/v1/setup/ollama/download_and_run")
def setup_ollama_download_and_run():
    dest = settings.data_dir / "third_party" / "_installers"
    task = setup_tasks.start("download_ollama_installer", download_and_run_ollama_installer, dest)
    return {"ok": True, "task": task.__dict__}


@app.post("/v1/setup/ollama/pull")
def setup_ollama_pull(payload: dict[str, Any]):
    import os

    model = (payload or {}).get("model") or os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen2.5:3b-instruct")
    url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    task = setup_tasks.start(f"pull_model:{model}", pull_ollama_model, url, model)
    return {"ok": True, "task": task.__dict__}

@app.post("/v1/setup/7zip/install")
def setup_7zip_install():
    """Install 7-Zip on Windows (required for extracting some .7z archives)."""
    task = setup_tasks.start("install_7zip", download_and_install_7zip, settings.data_dir)
    return {"ok": True, "task": task.__dict__}

@app.post("/v1/setup/backend/install")
def setup_backend_install(payload: dict[str, Any]):
    bundle = str((payload or {}).get("bundle") or "studio_bundle").strip() or "studio_bundle"
    task = setup_tasks.start(f"install_backend_bundle:{bundle}", install_backend_bundle, bundle)
    return {"ok": True, "task": task.__dict__}

@app.post("/v1/setup/full/install")
def setup_full_install(payload: dict[str, Any]):
    """Run a full one-click setup: backend bundle, 7-Zip, Ollama/model, ComfyUI Portable install + start."""
    import os

    flavor = (payload or {}).get("flavor") or "cpu"
    port = int((payload or {}).get("comfy_port") or 8188)
    bundle = str((payload or {}).get("bundle") or "studio_bundle").strip() or "studio_bundle"
    model = (payload or {}).get("model") or os.getenv("EDMG_AI_OLLAMA_MODEL", "qwen2.5:3b-instruct")
    ollama_url = os.getenv("EDMG_AI_OLLAMA_URL", "http://127.0.0.1:11434")
    ai_config = _setup_ai_config()

    def _run(task):
        # 1) Ensure backend runtime bundle is present for audio/ASR/internal render paths.
        if not check_backend_bundle(bundle).get("ok"):
            install_backend_bundle(task, bundle)
        else:
            SetupTaskManager.log(task, f"Backend runtime bundle `{bundle}` already installed.")

        # 2) Ensure 7-Zip for .7z extraction
        try:
            _find_7z_exe(settings.data_dir)
        except Exception:
            download_and_install_7zip(task, settings.data_dir)

        # 3) Ollama installer/model only when the active AI path actually uses Ollama.
        if ai_config.get("ollama_required"):
            ollama_status = check_ollama(ollama_url, model)
            if not ollama_status.get("ok"):
                dest = settings.data_dir / "third_party" / "_installers"
                download_and_run_ollama_installer(task, dest)
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
        if not comfy_portable_installed(settings.data_dir):
            download_and_extract_portable(task, settings.data_dir, flavor)
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
            comfy_portable.start(task, settings.data_dir, flavor, "127.0.0.1", port)

    task = setup_tasks.start(f"full_setup:{flavor}:{ai_config.get('provider')}", _run)
    return {"ok": True, "task": task.__dict__}


@app.post("/v1/setup/comfyui/portable/install")
def setup_comfyui_portable_install(payload: dict[str, Any]):
    flavor = (payload or {}).get("flavor") or "cpu"
    task = setup_tasks.start(f"install_comfyui_portable:{flavor}", download_and_extract_portable, settings.data_dir, flavor)
    return {"ok": True, "task": task.__dict__}


@app.post("/v1/setup/comfyui/portable/start")
def setup_comfyui_portable_start(payload: dict[str, Any]):
    flavor = (payload or {}).get("flavor") or "cpu"
    port = int((payload or {}).get("port") or 8188)
    task = setup_tasks.start(f"start_comfyui_portable:{flavor}", comfy_portable.start, settings.data_dir, flavor, "127.0.0.1", port)
    return {"ok": True, "task": task.__dict__}


@app.post("/v1/setup/comfyui/portable/stop")
def setup_comfyui_portable_stop():
    comfy_portable.stop()
    return {"ok": True}


@app.post("/v1/setup/edmg/install")
def setup_edmg_install(payload: dict[str, Any]):
    mode = str((payload or {}).get("mode") or "standard").strip().lower() or "standard"
    backend = str((payload or {}).get("backend") or "cpu").strip().lower() or "cpu"
    task = setup_tasks.start(f"install_edmg_core:{mode}:{backend}", edmg_install_core, settings.data_dir, mode=mode, backend=backend)
    return {"ok": True, "task": task.__dict__}


@app.get("/v1/ai/status")
def ai_status():
    return {"ok": True, "ai": ai.status()}

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
    except Exception as e:
        raise HTTPException(502, f"ComfyUI error: {e}")

@app.get("/v1/comfyui/capabilities")
def comfyui_capabilities():
    try:
        primary = settings.resolved_comfyui_urls()[0]
        obj = comfy.get_object_info(primary)
    except Exception as e:
        raise HTTPException(502, f"ComfyUI error: {e}")

    ad_ok, ad_missing = comfy.has_nodes(obj, ["ADE_AnimateDiffLoaderGen1", "ADE_StandardStaticContextOptions"])
    svd_ok, svd_missing = comfy.has_nodes(obj, ["SVDSimpleImg2Vid"])
    return {
        "comfyui_url": settings.comfyui_url,
        "comfyui_urls": list(settings.resolved_comfyui_urls()),
        "comfyui_node_concurrency": settings.comfyui_node_concurrency,
        "animatediff": {"available": ad_ok, "missing_nodes": ad_missing},
        "svd": {"available": svd_ok, "missing_nodes": svd_missing},
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

@app.get("/v1/projects")
def list_projects():
    return {"projects": [p.__dict__ for p in store.list()]}

@app.post("/v1/projects")
def create_project(req: ProjectCreateRequest):
    proj = store.create(req.name)
    return {"project": proj.__dict__}

@app.get("/v1/projects/{project_id}")
def get_project(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    return {"project": proj.__dict__}

@app.get("/v1/projects/{project_id}/timeline")
def get_timeline(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    return {"ok": True, "timeline": proj.meta.get("timeline") or {"layers": []}}

@app.post("/v1/projects/{project_id}/timeline")
def set_timeline(project_id: str, req: TimelineUpdateRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    proj.meta["timeline"] = req.timeline or {"layers": []}
    store.save(proj)
    return {"ok": True, "timeline": proj.meta["timeline"]}
@app.get("/v1/projects/{project_id}/preview/frame")
def preview_frame(project_id: str, t: float = 0.0, w: int = 768, h: int = 432, force: int = 0):
    """Render a low-res cached preview frame for timeline scrubbing (no diffusion)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}

    try:
        from PIL import Image  # type: ignore
    except Exception as e:
        raise HTTPException(500, f"Pillow not installed: {e}")

    cache_dir = (pdir / "outputs" / "previews" / f"{int(w)}x{int(h)}").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"t{int(float(t) * 1000):010d}.png"
    out = cache_dir / key

    if out.exists() and not force:
        return FileResponse(str(out), media_type="image/png")

    base = Image.new("RGB", (int(w), int(h)), color=(18, 18, 22))
    try:
        img = apply_timeline_layers(base, project_dir=pdir, timeline=timeline, t=float(t))
    except Exception:
        img = base
    img.save(out)
    

    return FileResponse(str(out), media_type="image/png")
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
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}

    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
    except Exception as e:
        raise HTTPException(500, f"Pillow not installed: {e}")

    start = max(0.0, float(start_s))
    end = max(start + 0.05, float(end_s))
    # protect the server: cap clip length
    end = min(end, start + 30.0)
    fps_i = max(1, min(24, int(fps)))

    tl_hash = hashlib.sha1(json.dumps(timeline, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    cache_dir = (pdir / "outputs" / "previews" / f"seg_{int(w)}x{int(h)}").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"seg_{int(start*1000):010d}_{int(end*1000):010d}_{fps_i}fps_{tl_hash}.mp4"
    out_mp4 = cache_dir / key

    if out_mp4.exists() and not force:
        return FileResponse(str(out_mp4), media_type="video/mp4")

    frames_dir = cache_dir / f"_tmp_{out_mp4.stem}"
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
      - uses the internal Diffusers engine (SD1.5/SDXL) if installed
    """
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)
    timeline = proj.meta.get("timeline") or {}

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
    w_i = max(256, min(1536, int(w)))
    h_i = max(256, min(1536, int(h)))

    # Resolve internal model
    mid = str(model_id or "auto")
    if mid == "auto":
        preferred = _hardware_profile().get("preferred_internal_model") or "hf_sd15_internal"
        mid = preferred
        if models.installed_path(mid) is None:
            # fallback
            mid = "hf_sd15_internal" if preferred != "hf_sd15_internal" else "hf_sdxl_internal"
    model_dir = models.installed_path(mid)
    if not model_dir or not model_dir.exists():
        raise UserFacingError(
            "Internal model is not installed.",
            hint="Go to Models and install an internal model (SD 1.5 or SDXL), then retry.",
            code="MODEL_MISSING",
            status_code=400,
        )

    # Cache
    tl_hash = hashlib.sha1(json.dumps(timeline, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:10]
    p_hash = hashlib.sha1((prompt or "").encode("utf-8")).hexdigest()[:8]
    cache_dir = (pdir / "outputs" / "previews" / f"diff_{w_i}x{h_i}").resolve()
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"diff_{int(start*1000):010d}_{int(end*1000):010d}_{fps_i}fps_{steps_i}s_{int(cfg*10):03d}c_{int(strength*100):03d}st_{mid}_{tl_hash}_{p_hash}.mp4"
    out_mp4 = cache_dir / key

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
        name = (file.filename or "audio.wav").replace("\\", "_").replace("/", "_")
        out = audio_dir / name
        data = await file.read()
        out.write_bytes(data)
        store.set_audio(project_id, name, len(data))
        return {"ok": True, "path": str(out)}
else:
    @app.post("/v1/projects/{project_id}/assets/audio")
    async def upload_audio(project_id: str):
        _require_multipart()


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

    audio_path = store.project_dir(project_id) / "assets" / "audio" / fn
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
        name = (file.filename or "overlay.png").replace("\\", "_").replace("/", "_")
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
        name = (file.filename or "mask.png").replace("\\", "_").replace("/", "_")
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


@app.post("/v1/projects/{project_id}/analyze_audio")
def analyze_audio(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    audio_meta = proj.meta.get("audio")
    if not audio_meta:
        raise HTTPException(400, "No audio uploaded")
    audio_path = store.project_dir(project_id) / "assets" / "audio" / audio_meta["filename"]

    feats = {}
    trans = {}
    # Prefer EDMG-core AudioAnalyzer (richer beats/energy) when deps are available.
    try:
        from enhanced_deforum_music_generator.core.audio_analyzer import AudioAnalyzer  # type: ignore
        from enhanced_deforum_music_generator.config.config_system import AudioConfig  # type: ignore

        analyzer = AudioAnalyzer(AudioConfig())
        af = analyzer.analyze_features(str(audio_path))
        # Keep only JSON-friendly fields
        energy = list(getattr(af, "energy", []) or [])
        if energy:
            mn = min(energy)
            mx = max(energy)
            if mx > mn:
                energy = [(float(e) - mn) / (mx - mn) for e in energy]
            energy = [max(0.0, min(1.0, float(e))) for e in energy]
        feats = {
            "duration_s": float(getattr(af, "duration", 0.0) or 0.0),
            "bpm": float(getattr(af, "tempo", 0.0) or 0.0),
            "tempo_bpm": float(getattr(af, "tempo", 0.0) or 0.0),
            "beats": [float(x) for x in (getattr(af, "beats", []) or [])],
            "energy": energy,
        }
    except Exception:
        # Fallback to local feature extractors so audio analysis does not depend on the AI client.
        try:
            from edmg_ai_service.audio import lightweight_audio_features  # type: ignore

            feats = lightweight_audio_features(str(audio_path))
        except Exception as e:
            feats = {"error": f"audio_features failed: {e}"}

    try:
        from edmg_ai_service.asr import transcribe as local_transcribe  # type: ignore

        transcript_result = local_transcribe(str(audio_path), model_size="small")
        if isinstance(transcript_result, dict):
            trans = transcript_result
        else:
            trans = {"text": str(transcript_result or "")}
    except Exception as e:
        trans = {"error": f"transcribe failed: {e}"}

    analysis = {"features": feats, "transcript": trans, "timestamp": time.time()}
    proj.meta["analysis"] = analysis
    store.save(proj)
    return {"ok": True, "analysis": analysis}


def _analysis_transcript_text(analysis: dict[str, Any]) -> str:
    raw = (analysis or {}).get("transcript")
    if isinstance(raw, dict):
        return str(raw.get("text") or "")
    if isinstance(raw, str):
        return raw
    return ""

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

    duration = float(feats.get("duration_s") or feats.get("duration") or 0.0)
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
    fps = 24

    from enhanced_deforum_music_generator.core.prompt_orchestrator import PromptOrchestrator, OrchestrationConfig  # type: ignore
    from enhanced_deforum_music_generator.core.motion_orchestrator import MotionConfig, motion_schedules  # type: ignore

    orch = PromptOrchestrator(provider=None, cfg=OrchestrationConfig(fps=fps, max_scenes=max_scenes))
    motion = motion_schedules(analysis_obj, cfg=MotionConfig(fps=fps))

    # add steps + denoise schedules
    steps_sched, denoise_sched = _derive_steps_and_denoise_schedules(analysis_obj, fps=fps, base_steps=15)
    motion.setdefault("steps_schedule", steps_sched)
    motion.setdefault("denoise_schedule", denoise_sched)

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
            p = str(prompts.get(str(int(a))) or prompts.get(str(int(frames[max(0, i - 1)]))) or base_prompt).strip() or base_prompt
            scenes.append({"start_s": start_s, "end_s": end_s, "prompt": p, "negative_prompt": "blurry, low quality, watermark, text, logo"})

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


@app.post("/v1/projects/{project_id}/plan")
def generate_plan(project_id: str, req: PlanRequest, mode: str = "auto"):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    analysis = proj.meta.get("analysis") or {}
    feats = (analysis.get("features") or {})
    transcript = _analysis_transcript_text(analysis)

    payload = {
        "title": req.title or proj.name,
        "user_notes": req.user_notes,
        "duration_s": feats.get("duration_s") or feats.get("duration"),
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
                    message="AI Director is not available.",
                    hint=(
                        "Fix: If you're using Ollama, make sure it is installed and running (Ollama app or `ollama serve`), "
                        "and that the model is pulled (e.g., `ollama pull qwen2.5:3b-instruct`). "
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

    proj.meta["last_plan"] = plan
    store.save(proj)
    return plan


@app.post("/v1/projects/{project_id}/timeline/apply_plan")
def apply_plan_to_timeline(project_id: str, req: ApplyPlanRequest):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not isinstance(plan, dict):
        raise HTTPException(400, "No plan. Generate a plan first.")
    variants = plan.get("variants") if isinstance(plan.get("variants"), list) else []
    vi = int(req.variant_index or 0)
    if not variants or vi < 0 or vi >= len(variants):
        raise HTTPException(400, "Invalid variant_index")
    variant = variants[vi] if isinstance(variants[vi], dict) else {}
    scenes = variant.get("scenes") if isinstance(variant.get("scenes"), list) else []
    duration_s = float(variant.get("duration_s") or plan.get("duration_s") or 60.0)

    timeline = proj.meta.get("timeline") if isinstance(proj.meta.get("timeline"), dict) else {}
    timeline = {**timeline}

    # tracks
    tracks = timeline.get("tracks") if isinstance(timeline.get("tracks"), list) else []
    tracks = [t for t in tracks if isinstance(t, dict)]
    overwrite = bool(req.overwrite)

    def upsert_track(tid: str, name: str, ttype: str, clips: list[dict[str, Any]]) -> None:
        nonlocal tracks
        idx = next((i for i, t in enumerate(tracks) if str(t.get("id") or "") == tid or str(t.get("type") or "").lower() == ttype.lower()), -1)
        if idx >= 0:
            if overwrite or not tracks[idx].get("clips"):
                tracks[idx] = {**tracks[idx], "id": tid, "name": name, "type": ttype, "clips": clips}
        else:
            tracks.append({"id": tid, "name": name, "type": ttype, "clips": clips})

    # prompt track
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
                    "prompt": str(s.get("prompt") or "").strip(),
                    "negative_prompt": str(s.get("negative_prompt") or "").strip(),
                },
            }
        )
    upsert_track("edmg_prompt", "EDMG Prompts", "prompt", prompt_clips)

    # motion track: store schedules directly on clip.data
    ms = variant.get("motion_schedules") if isinstance(variant.get("motion_schedules"), dict) else {}
    if not ms:
        # best-effort: derive from analysis
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

    # camera keyframes (from zoom + angle schedules) if empty or overwrite
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
                a, av = pairs[i]
                b, bv = pairs[i + 1]
                if a <= frame <= b:
                    w = (frame - a) / max(1e-9, (b - a))
                    return float(av) * (1.0 - w) + float(bv) * w
            return float(pairs[-1][1])

        zoom_pairs = _parse_sched(zoom_s)
        ang_pairs = _parse_sched(ang_s)
        frames = sorted({0, *[f for f, _ in zoom_pairs], *[f for f, _ in ang_pairs]})
        if len(frames) > 64:
            step = max(1, len(frames) // 64)
            frames = frames[::step]
        out = []
        for f in frames:
            out.append(
                {
                    "t": float(f) / float(fps),
                    "zoom": _sample(zoom_pairs, f) if zoom_pairs else 1.0,
                    "pan_x": 0.0,
                    "pan_y": 0.0,
                    "rotation_deg": _sample(ang_pairs, f) if ang_pairs else 0.0,
                }
            )
        cam["keyframes"] = out
        timeline["camera"] = cam

    proj.meta["timeline"] = timeline
    store.save(proj)
    return {"ok": True, "timeline": timeline}


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
    return {"ok": True, "job": job.__dict__}

@app.post("/v1/projects/{project_id}/jobs/{job_id}/retry")
def retry_job(project_id: str, job_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    job = jobs.retry(project_id, job_id)
    if not job:
        raise HTTPException(404, "Job not found")
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
    if source_job.status in ("queued", "running"):
        raise HTTPException(409, "Job is still active. Cancel it before resuming from checkpoint.")
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
    if source_job.status in ("queued", "running"):
        raise HTTPException(409, "Job is still active. Cancel it before starting a clean restart.")
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

@app.post("/v1/jobs/tick")
def tick_worker():
    """Manual single-step worker tick (useful for debugging)."""
    job = jobs.claim_next_queued()
    if not job:
        return {"ok": True, "note": "no queued jobs"}
    _execute_job(job)
    latest = jobs.get(job.project_id, job.id) or job
    return {"ok": True, "job": latest.__dict__}

def _run_assemble_variant(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    req = AssembleVideoRequest(**(payload or {}))
    return assemble_video(project_id, req)

def _execute_job(job):
    jobs.append_log(job.project_id, job.id, f"Started job type={job.type}")

    try:
        if job.type == "comfyui_scene":
            res = _run_comfyui_scene(job.project_id, job.id, job.payload)
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
        else:
            job.status = "failed"
            job.error = f"Unknown job type: {job.type}"
    except JobCanceled as e:
        job.status = "canceled"
        job.error = None
        latest = jobs.get(job.project_id, job.id)
        if latest and latest.result:
            job.result = latest.result
        jobs.append_log(job.project_id, job.id, str(e) or "Job canceled during execution")
    except Exception as e:
        latest = jobs.get(job.project_id, job.id)
        if latest and latest.status == "canceled":
            job.status = "canceled"
            job.error = None
        else:
            job.status = "failed"
            hint = hint_from_exception(e)
            job.error = f"{e}" + (f"\nFix: {hint}" if hint else "")

    jobs.append_log(job.project_id, job.id, f"Finished status={job.status}")
    if job.error:
        jobs.append_log(job.project_id, job.id, f"Error: {job.error}")

    latest = jobs.get(job.project_id, job.id)
    if latest and isinstance(latest.progress, dict):
        job.progress = latest.progress
    jobs.save(job)


# Initialize always-on worker manager now that _execute_job exists
worker = WorkerManager(
    jobs=jobs,
    run_job=_execute_job,
    concurrency=settings.worker_concurrency,
    poll_interval_s=settings.worker_poll_interval_s,
)

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
        return {"cached": True, "saved": str(out_path)}

    checkpoint = payload.get("checkpoint") or settings.comfyui_checkpoint

    wf = comfy.default_workflow(
        checkpoint=checkpoint,
        prompt=prompt,
        negative_prompt=negative_prompt,
        seed=seed,
        width=width,
        height=height,
        steps=steps,
        cfg=cfg,
        sampler=sampler
    )

    req = {"checkpoint": checkpoint, "est_steps": steps, "est_frames": 1}
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
                return {"prompt_id": prompt_id, "saved": str(out_path), "comfyui_image": im}

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

    frames_dir = Path(payload.get("frames_dir") or "")
    out_clip = Path(payload.get("out_clip") or "")
    if out_clip and out_clip.exists():
        return {"cached": True, "saved": str(out_clip)}
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
                    return {"prompt_id": prompt_id, "saved": str(out_clip), "frames_dir": str(frames_dir)}
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


def _run_internal_video(project_id: str, job_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    preflight = _internal_render_preflight_data(project_id, payload)
    if preflight.get("mode") == "proxy":
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

        settings_obj = InternalVideoSettings(
            fps_render=int(payload.get("fps_render", 2)),
            fps_output=int(payload.get("fps_output", 24)),
            width=int(payload.get("width", 768)),
            height=int(payload.get("height", 432)),
            steps=int(payload.get("steps", 15)),
            cfg=float(payload.get("cfg", 7.0)),
            keyframe_interval_s=float(payload.get("keyframe_interval_s", 5.0)),
            interpolation_engine=str(payload.get("interpolation_engine", "auto")),
            negative_prompt=str(payload.get("negative_prompt", "blurry, low quality, watermark, text, logo")),
            model_id="proxy_draft",
            temporal_mode="off",
            temporal_strength=float(payload.get("temporal_strength", 0.35)),
            temporal_steps=(int(payload["temporal_steps"]) if payload.get("temporal_steps") is not None else None),
            refine_every_n_frames=int(payload.get("refine_every_n_frames", 1)),
            anchor_strength=float(payload.get("anchor_strength", 0.20)),
            prompt_blend=bool(payload.get("prompt_blend", True)),
            resume_existing_frames=bool(payload.get("resume_existing_frames", True)),
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
                extra=_job_checkpoint_extra("proxy", "proxy_draft", runtime_checkpoint),
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
                    extra=_job_checkpoint_extra("proxy", "proxy_draft", runtime_checkpoint),
                )
                raise JobCanceled("Proxy render canceled")

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
                extra=_job_checkpoint_extra("proxy", "proxy_draft", runtime_checkpoint),
            )

        _progress("starting", 0, estimated_total, "Starting proxy draft render")
        variant2 = dict(variant)
        variant2["index"] = variant_index
        variant2["duration_s"] = float(
            proj.meta.get("analysis", {}).get("duration_s")
            or variant.get("duration_s")
            or scenes[-1].get("end_s")
            or 60.0
        )

        out = render_internal_proxy_video_variant(
            ffmpeg_path=settings.ffmpeg_path,
            project_dir=pdir,
            variant=variant2,
            scenes=scenes,
            audio_path=audio_path,
            settings=settings_obj,
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
            extra=_job_checkpoint_extra("proxy", "proxy_draft", checkpoint_summary, video=str(out)),
        )

        rel_video = str(out.relative_to(pdir))
        videos = proj.meta.setdefault("outputs", {}).setdefault("videos", [])
        if rel_video not in videos:
            videos.append(rel_video)
        render_entry = {
            "video": rel_video,
            "model_id": "proxy_draft",
            "mode": "proxy",
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "temporal_mode": "off",
            "resume_existing_frames": settings_obj.resume_existing_frames,
            "variant_index": variant_index,
            "completed_at": time.time(),
            "preflight": preflight,
            "runtime_checkpoint": checkpoint_summary,
        }
        proj.meta["last_internal_render"] = render_entry
        hist = proj.meta.setdefault("internal_render_history", [])
        hist.append(render_entry)
        if isinstance(hist, list) and len(hist) > 20:
            proj.meta["internal_render_history"] = hist[-20:]
        store.save(proj)
        return {"ok": True, "video": rel_video, "video_abs": str(out), "mode": "proxy", "preflight": preflight, "runtime_checkpoint": checkpoint_summary}

    proj, variant, model_id, model_path, settings_obj = _resolve_internal_render_request(project_id, payload)
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
        f"keyframe_interval_s={settings_obj.keyframe_interval_s} temporal_mode={settings_obj.temporal_mode}"
    )
    _log(f"Hardware: backend={hw.get('backend')} vram_gb={hw.get('vram_gb')}")
    _log(f"Using model_id={model_id} path={model_path}")
    if preflight.get("warnings"):
        for warning in preflight["warnings"]:
            _log(f"Warning: {warning}")

    _progress("starting", 0, estimated_total, "Starting internal render")

    variant2 = dict(variant)
    variant2["index"] = int(payload.get("variant_index", 0))
    variant2["duration_s"] = float(
        proj.meta.get("analysis", {}).get("duration_s")
        or variant.get("duration_s")
        or scenes[-1].get("end_s")
        or 60.0
    )

    out = render_internal_video_variant(
        ffmpeg_path=settings.ffmpeg_path,
        project_dir=pdir,
        variant=variant2,
        scenes=scenes,
        audio_path=audio_path,
        model_dir=model_path,
        settings=settings_obj,
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
        "resume_existing_frames": settings_obj.resume_existing_frames,
        "variant_index": int(payload.get("variant_index", 0)),
        "completed_at": time.time(),
        "preflight": preflight,
        "runtime_checkpoint": checkpoint_summary,
    }
    proj.meta["last_internal_render"] = render_entry
    hist = proj.meta.setdefault("internal_render_history", [])
    hist.append(render_entry)
    if isinstance(hist, list) and len(hist) > 20:
        proj.meta["internal_render_history"] = hist[-20:]
    store.save(proj)
    return {"ok": True, "video": rel_video, "video_abs": str(out), "mode": "diffusion", "preflight": preflight, "runtime_checkpoint": checkpoint_summary}


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
    for idx, sc in enumerate(scenes):
        # Deterministic output path for caching
        out_dir = store.project_dir(project_id) / "outputs" / "images"
        out_dir.mkdir(parents=True, exist_ok=True)
        seed = _stable_seed(project_id, req.variant_index, idx)
        out_path = out_dir / f"v{req.variant_index:02d}_scene{idx:03d}_seed{seed}.png"
        p = {
            "variant_index": req.variant_index,
            "scene_index": idx,
            "prompt": sc.get("prompt") or "",
            "negative_prompt": req.negative_prompt,
            "seed": seed,
            "width": req.width,
            "height": req.height,
            "steps": req.steps,
            "cfg": req.cfg,
            "sampler": req.sampler,
            "out_path": str(out_path),
        }
        job = jobs.create(project_id, "comfyui_scene", p)
        created.append(job.__dict__)

    proj.meta.setdefault("jobs", []).extend(created)
    store.save(proj)

    return {"ok": True, "enqueued": len(created), "jobs": created}



@app.post("/v1/projects/{project_id}/render/internal/video")
def render_internal_video(project_id: str, req: InternalVideoRenderRequest):
    """Enqueue a full internal render job (CPU-safe baseline)."""
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    plan = proj.meta.get("last_plan")
    if not plan or not (plan.get("variants") or []):
        raise HTTPException(400, "No plan generated")

    payload = _request_payload(req)
    preflight = _internal_render_preflight_data(project_id, payload)
    payload["render_mode"] = str(preflight.get("mode") or payload.get("render_mode") or "auto")
    job = jobs.create(project_id, "internal_video", payload)
    job.progress = {
        "stage": "queued",
        "current": 0,
        "total": max(1, int(preflight.get("estimated_frames", 1)) + 3),
        "percent": 0.0,
        "message": f"Queued internal render for model {preflight.get('model_id')}",
    }
    jobs.save(job)
    proj.meta.setdefault("jobs", []).append(job.__dict__)
    store.save(proj)
    return {"ok": True, "job": job.__dict__, "preflight": preflight}




def _resolve_internal_render_request(project_id: str, payload: dict[str, Any]) -> tuple[Any, dict[str, Any], str, Path, InternalVideoSettings]:
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

    req_model_id = str(payload.get("model_id") or "hf_sd15_internal")
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or "auto"))

    def _pick_auto_model() -> str | None:
        preferred = str(tier_plan.get("preferred_internal_model") or hw.get("preferred_internal_model") or "hf_sd15_internal")
        fallbacks = [preferred, "hf_sd15_internal", "hf_sdxl_internal"]
        for mid in fallbacks:
            if models.installed_path(mid):
                return mid
        return None

    model_id = req_model_id
    if req_model_id.lower() in ("auto", "auto_internal"):
        picked = _pick_auto_model()
        if not picked:
            raise UserFacingError(
                "No internal diffusion model installed",
                hint="Open Models → install either 'Stable Diffusion v1.5 (Internal / Diffusers)' or 'Stable Diffusion XL Base 1.0 (Internal / Diffusers)' then retry.",
                code="MODEL_NOT_INSTALLED",
                status_code=400,
            )
        model_id = picked

    model_path = models.installed_path(model_id)
    if not model_path:
        raise UserFacingError(
            "Internal model not installed",
            hint="Open Models → install the requested internal model (SD1.5 or SDXL) then retry.",
            code="MODEL_NOT_INSTALLED",
            status_code=400,
        )

    settings_obj = InternalVideoSettings(
        fps_render=int(payload.get("fps_render", 2)),
        fps_output=int(payload.get("fps_output", 24)),
        width=int(payload.get("width", 768)),
        height=int(payload.get("height", 432)),
        steps=int(payload.get("steps", 15)),
        cfg=float(payload.get("cfg", 7.0)),
        keyframe_interval_s=float(payload.get("keyframe_interval_s", 5.0)),
        interpolation_engine=str(payload.get("interpolation_engine", "auto")),
        negative_prompt=str(payload.get("negative_prompt", "blurry, low quality, watermark, text, logo")),
        model_id=model_id,
        render_tier=str(tier_plan.get("applied_tier") or payload.get("render_tier") or "auto"),
        device_preference=str(payload.get("device_preference") or tier_plan.get("device_preference") or "auto"),
        temporal_mode=str(payload.get("temporal_mode", "frame_img2img")),
        temporal_strength=float(payload.get("temporal_strength", 0.35)),
        temporal_steps=(int(payload["temporal_steps"]) if payload.get("temporal_steps") is not None else None),
        refine_every_n_frames=int(payload.get("refine_every_n_frames", 1)),
        anchor_strength=float(payload.get("anchor_strength", 0.20)),
        prompt_blend=bool(payload.get("prompt_blend", True)),
        resume_existing_frames=bool(payload.get("resume_existing_frames", True)),
    )
    return proj, variant, model_id, model_path, settings_obj


def _proxy_render_preflight_data(
    project_id: str,
    payload: dict[str, Any],
    *,
    reason: str | None = None,
    requested_model_id: str | None = None,
) -> dict[str, Any]:
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

    settings_obj = InternalVideoSettings(
        fps_render=int(payload.get("fps_render", 2)),
        fps_output=int(payload.get("fps_output", 24)),
        width=int(payload.get("width", 768)),
        height=int(payload.get("height", 432)),
        steps=int(payload.get("steps", 15)),
        cfg=float(payload.get("cfg", 7.0)),
        keyframe_interval_s=float(payload.get("keyframe_interval_s", 5.0)),
        interpolation_engine=str(payload.get("interpolation_engine", "auto")),
        negative_prompt=str(payload.get("negative_prompt", "blurry, low quality, watermark, text, logo")),
        model_id="proxy_draft",
        render_tier=str(payload.get("render_tier") or "auto"),
        device_preference="cpu",
        temporal_mode="off",
        temporal_strength=float(payload.get("temporal_strength", 0.35)),
        temporal_steps=(int(payload["temporal_steps"]) if payload.get("temporal_steps") is not None else None),
        refine_every_n_frames=int(payload.get("refine_every_n_frames", 1)),
        anchor_strength=float(payload.get("anchor_strength", 0.20)),
        prompt_blend=bool(payload.get("prompt_blend", True)),
        resume_existing_frames=bool(payload.get("resume_existing_frames", True)),
    )

    duration_s = float(
        proj.meta.get("analysis", {}).get("duration_s")
        or variant.get("duration_s")
        or scenes[-1].get("end_s")
        or 60.0
    )
    total_frames = int(math.ceil(duration_s * max(1, int(settings_obj.fps_render))))
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or "auto"), duration_s=duration_s)
    tier_plan["chunk_plan"] = _build_render_chunk_plan(hw, applied_tier=str(tier_plan.get("applied_tier") or "draft"), duration_s=duration_s, total_frames=total_frames, fps_render=int(settings_obj.fps_render), render_mode="proxy")
    cache = describe_proxy_render_cache(
        project_dir=store.project_dir(project_id),
        variant_index=variant_index,
        scenes=scenes,
        timeline=(proj.meta.get("timeline") or None),
        settings=settings_obj,
        total_frames=total_frames,
    )
    warnings = [
        "Using proxy draft render because no internal diffusion model is installed.",
        "Proxy mode renders pacing, prompts, and timeline overlays locally without ComfyUI or Diffusers.",
    ]
    if reason:
        warnings.insert(0, reason)
    return {
        "ok": True,
        "mode": "proxy",
        "variant_index": variant_index,
        "model_id": "proxy_draft",
        "requested_model_id": str(requested_model_id or payload.get("model_id") or "auto"),
        "model_path": None,
        "duration_s": duration_s,
        "estimated_frames": total_frames,
        "estimated_keyframes": max(1, len(scenes)),
        "device": str(tier_plan.get("device_preference") or "cpu"),
        "hardware": hw,
        "tier_plan": tier_plan,
        "resume_existing_frames": bool(settings_obj.resume_existing_frames),
        "warnings": warnings,
        "cache": cache,
        "installed_internal_models": {
            "hf_sd15_internal": bool(models.installed_path("hf_sd15_internal")),
            "hf_sdxl_internal": bool(models.installed_path("hf_sdxl_internal")),
        },
        "settings": {
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "width": settings_obj.width,
            "height": settings_obj.height,
            "interpolation_engine": settings_obj.interpolation_engine,
            "resume_existing_frames": settings_obj.resume_existing_frames,
            "render_mode": "proxy",
        },
    }


def _internal_render_preflight_data(project_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    requested_mode = str(payload.get("render_mode") or "auto").strip().lower()
    if requested_mode == "proxy":
        return _proxy_render_preflight_data(project_id, payload, reason="Proxy mode requested explicitly.")

    try:
        proj, variant, model_id, model_path, settings_obj = _resolve_internal_render_request(project_id, payload)
    except UserFacingError as e:
        allow_proxy = bool(payload.get("allow_proxy_fallback", True))
        if allow_proxy and e.code == "MODEL_NOT_INSTALLED":
            return _proxy_render_preflight_data(project_id, payload, reason=e.message, requested_model_id=str(payload.get("model_id") or "auto"))
        raise

    scenes = variant.get("scenes") or []
    duration_s = float(
        proj.meta.get("analysis", {}).get("duration_s")
        or variant.get("duration_s")
        or scenes[-1].get("end_s")
        or 60.0
    )
    fps_render = max(1, int(settings_obj.fps_render))
    total_frames = int(math.ceil(duration_s * fps_render))
    keyframes = max(1, len(scenes))
    hw = _hardware_profile()
    tier_plan = _build_internal_render_plan(hw, requested_tier=str(payload.get("render_tier") or settings_obj.render_tier or "auto"), duration_s=duration_s)
    tier_plan["chunk_plan"] = _build_render_chunk_plan(hw, applied_tier=str(tier_plan.get("applied_tier") or "draft"), duration_s=duration_s, total_frames=total_frames, fps_render=fps_render, render_mode="diffusion")
    warnings: list[str] = []
    if str(hw.get("backend") or "").lower() == "cpu":
        warnings.append("No GPU acceleration detected; internal diffusion will run on CPU and may be slow on longer renders.")
    elif str(hw.get("backend") or "").lower() == "mps":
        warnings.append("Apple Silicon acceleration detected; balanced settings are recommended for sustained laptop rendering.")
    if total_frames > 900:
        warnings.append("This render is long for the current FPS render setting; consider lowering FPS render or increasing keyframe interval.")
    if settings_obj.temporal_mode == "frame_img2img" and total_frames > 600:
        warnings.append("Frame img2img temporal mode is the most expensive mode for long clips.")
    if settings_obj.fps_render > settings_obj.fps_output:
        warnings.append("FPS render is higher than FPS output; you may be spending extra time on frames that will be blended down.")
    for note in list(tier_plan.get("notes") or []):
        if note not in warnings:
            warnings.append(str(note))
    timeline = proj.meta.get("timeline") or None
    cache = describe_internal_render_cache(
        project_dir=store.project_dir(project_id),
        variant_index=int(payload.get("variant_index", 0)),
        scenes=scenes,
        timeline=timeline if isinstance(timeline, dict) else None,
        model_dir=model_path,
        settings=settings_obj,
        total_frames=total_frames,
    )
    installed_internal = {
        "hf_sd15_internal": bool(models.installed_path("hf_sd15_internal")),
        "hf_sdxl_internal": bool(models.installed_path("hf_sdxl_internal")),
    }
    return {
        "ok": True,
        "mode": "diffusion",
        "variant_index": int(payload.get("variant_index", 0)),
        "model_id": model_id,
        "model_path": str(model_path),
        "duration_s": duration_s,
        "estimated_frames": total_frames,
        "estimated_keyframes": keyframes,
        "device": str(tier_plan.get("device_preference") or hw.get("backend") or "cpu"),
        "hardware": hw,
        "tier_plan": tier_plan,
        "resume_existing_frames": bool(settings_obj.resume_existing_frames),
        "warnings": warnings,
        "cache": cache,
        "installed_internal_models": installed_internal,
        "settings": {
            "fps_render": settings_obj.fps_render,
            "fps_output": settings_obj.fps_output,
            "width": settings_obj.width,
            "height": settings_obj.height,
            "temporal_mode": settings_obj.temporal_mode,
            "interpolation_engine": settings_obj.interpolation_engine,
            "render_mode": "diffusion",
            "render_tier": settings_obj.render_tier,
            "device_preference": settings_obj.device_preference,
        },
    }


@app.post("/v1/projects/{project_id}/render/internal/preflight")
def render_internal_preflight(project_id: str, req: InternalVideoRenderRequest):
    return _internal_render_preflight_data(project_id, _request_payload(req))

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
    for idx, sc in enumerate(scenes):
        start = float(sc.get("start_s", idx * 5))
        end = float(sc.get("end_s", start + 5))
        duration_s = max(0.5, end - start)
        frames = max(1, int(round(duration_s * req.fps)))
        frames = min(frames, int(req.max_frames_per_scene))

        # Practical caps for SVD (most setups use 14 or 25 frames)
        if req.engine == "svd":
            frames = min(frames, 25)

        seed = _stable_seed(project_id, req.variant_index, idx)
        pdir = store.project_dir(project_id)
        frames_dir = pdir / "outputs" / "frames" / f"v{req.variant_index:02d}" / f"scene{idx:03d}" / f"{req.engine}_seed{seed}"
        out_clip = pdir / "outputs" / "clips" / f"v{req.variant_index:02d}_scene{idx:03d}_{req.engine}_seed{seed}.mp4"
        p = {
            "variant_index": req.variant_index,
            "scene_index": idx,
            "prompt": sc.get("prompt") or "",
            "negative_prompt": req.negative_prompt,
            "seed": seed,
            "width": req.width,
            "height": req.height,
            "steps": req.steps,
            "cfg": req.cfg,
            "sampler": req.sampler,
            "checkpoint": req.checkpoint,
            "fps": req.fps,
            "frames": frames,
            "engine": req.engine,
            "frames_dir": str(frames_dir),
            "out_clip": str(out_clip),
            "motion_model_name": req.motion_model_name,
            "context_length": req.context_length,
            "context_overlap": req.context_overlap,
            "beta_schedule": req.beta_schedule,
            "svd_checkpoint": req.svd_checkpoint,
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


@lru_cache(maxsize=1)
def _internal_diffusion_runtime_status() -> dict[str, Any]:
    try:
        import diffusers  # type: ignore  # noqa: F401
        import torch  # type: ignore  # noqa: F401
        return {"ok": True, "diagnostics": ["internal_runtime=ready"]}
    except Exception as e:
        return {"ok": False, "error": str(e), "diagnostics": ["internal_runtime=missing"]}


def _recommend_local_fallback(project_id: str, preset: str, *, reason: str) -> dict[str, Any]:
    hw = _hardware_profile()
    preset_l = str(preset or "balanced").lower().strip()
    requested_tier = "draft" if preset_l == "fast" else ("quality" if preset_l in ("quality", "ultra") else "auto")
    tier_plan = _build_internal_render_plan(hw, requested_tier=requested_tier)
    preferred = str(tier_plan.get("preferred_internal_model") or hw.get("preferred_internal_model") or "hf_sd15_internal")
    fallbacks = [preferred, "hf_sd15_internal", "hf_sdxl_internal"]
    runtime = _internal_diffusion_runtime_status()
    picked = next((mid for mid in fallbacks if models.installed_path(mid)), None)
    if picked and runtime.get("ok"):
        return {
            "mode": "internal",
            "engine": "diffusion",
            "model_id": picked,
            "reason": f"{reason} Falling back to local internal render.",
            "diagnostics": ["comfyui=unavailable", f"internal_model={picked}", *list(runtime.get("diagnostics") or [])],
            "tier_plan": tier_plan,
        }
    diagnostics = ["comfyui=unavailable"]
    if picked:
        diagnostics.append(f"internal_model={picked}")
    else:
        diagnostics.append("internal_models=missing")
    diagnostics.extend(list(runtime.get("diagnostics") or []))
    proxy_reason = reason
    if picked and not runtime.get("ok"):
        proxy_reason = f"{reason} Internal diffusion runtime is not installed."
    return {
        "mode": "proxy",
        "engine": "proxy",
        "model_id": "proxy_draft",
        "reason": f"{proxy_reason} Falling back to proxy draft render.",
        "diagnostics": diagnostics + [f"project={project_id}"],
        "tier_plan": tier_plan,
    }


def _recommend_pipeline(project_id: str, preset: str, mode: str = "auto", engine: str = "auto") -> dict[str, Any]:
    ckpt = settings.comfyui_checkpoint
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
        tier_plan = _build_internal_render_plan(_hardware_profile(), requested_tier=requested_tier)
        tier_defaults = dict(tier_plan.get("defaults") or {})
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
            device_preference=str(tier_plan.get("device_preference") or "auto"),
            temporal_mode=str(tier_defaults.get("temporal_mode", "frame_img2img")),
            temporal_steps=int(tier_defaults.get("temporal_steps", 12)),
            refine_every_n_frames=int(tier_defaults.get("refine_every_n_frames", 1)),
            anchor_strength=float(tier_defaults.get("anchor_strength", 0.20)),
            prompt_blend=bool(tier_defaults.get("prompt_blend", True)),
            allow_proxy_fallback=True,
        )
        res = render_internal_video(project_id, internal_req)
        return {"ok": True, "mode": str(res.get("preflight", {}).get("mode") or "internal"), "job": res.get("job"), "preflight": res.get("preflight")}

    defaults = _preset_defaults(preset)
    rec = _recommend_pipeline(project_id, preset=preset, mode=mode, engine=engine)

    if rec["mode"] in ("internal", "proxy"):
        tier_plan = dict(rec.get("tier_plan") or _build_internal_render_plan(_hardware_profile(), requested_tier=("draft" if preset == "fast" else ("quality" if preset in ("quality", "ultra") else "auto"))))
        tier_defaults = dict(tier_plan.get("defaults") or {})
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
            render_mode=("proxy" if rec["mode"] == "proxy" else "auto"),
            render_tier=str(tier_plan.get("applied_tier") or "auto"),
            device_preference=str(tier_plan.get("device_preference") or "auto"),
            temporal_mode=str(tier_defaults.get("temporal_mode", "frame_img2img")),
            temporal_steps=int(tier_defaults.get("temporal_steps", 12)),
            refine_every_n_frames=int(tier_defaults.get("refine_every_n_frames", 1)),
            anchor_strength=float(tier_defaults.get("anchor_strength", 0.20)),
            prompt_blend=bool(tier_defaults.get("prompt_blend", True)),
            allow_proxy_fallback=True,
        )
        res = render_internal_video(project_id, internal_req)
        effective_mode = str(res.get("preflight", {}).get("mode") or rec["mode"])
        return {
            "ok": True,
            "preset": preset,
            "selected": rec,
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
        name = (file.filename or "ref.png").replace("\\", "_").replace("/", "_")
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
def export_comfyui_workflows(project_id: str, variant_index: int = 0):
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
    for idx, sc in enumerate(scenes):
        checkpoint = str(sc.get("checkpoint") or settings.comfyui_checkpoint)
        wf = comfy.default_workflow(
            checkpoint=checkpoint,
            prompt=str(sc.get("prompt") or ""),
            negative_prompt=str(sc.get("negative_prompt") or "(low quality, worst quality)"),
            seed=int(sc.get("seed") or (idx + 12345)),
            width=int(sc.get("width") or 768),
            height=int(sc.get("height") or 432),
            steps=int(sc.get("steps") or 20),
            cfg=float(sc.get("cfg") or 6.5),
            sampler=str(sc.get("sampler") or "euler"),
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

    pdir = store.project_dir(project_id)
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
    prompts = _scene_schedule_to_prompts(variant, fps=req.fps)

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

    out_dir = store.project_dir(project_id) / "outputs" / "deforum"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"variant_{req.variant_index:02d}.deforum.json"
    out_path.write_text(json.dumps(settings_dict, ensure_ascii=False, indent=2), encoding="utf-8")

    rel = str(out_path.relative_to(store.project_dir(project_id)))
    proj.meta.setdefault("exports", {}).setdefault("deforum", []).append(rel)
    store.save(proj)

    return {"ok": True, "path": rel}

@app.get("/v1/projects/{project_id}/outputs")
def list_outputs(project_id: str):
    proj = store.get(project_id)
    if not proj:
        raise HTTPException(404, "Project not found")
    pdir = store.project_dir(project_id)

    def _file_entry(fp: Path) -> dict[str, Any]:
        try:
            st = fp.stat()
            return {
                "path": str(fp.relative_to(pdir)),
                "name": fp.name,
                "size_bytes": int(st.st_size),
                "modified_at": float(st.st_mtime),
            }
        except Exception:
            return {"path": str(fp.relative_to(pdir)), "name": fp.name}

    imgs = []
    vids = []
    defs = []
    for p in sorted((pdir / "outputs" / "images").glob("*"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        if p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp"):
            imgs.append(_file_entry(p))
    for p in sorted((pdir / "outputs" / "videos").glob("*.mp4"), key=lambda x: x.stat().st_mtime if x.exists() else 0, reverse=True):
        entry = _file_entry(p)
        name = p.name
        if name.endswith("_raw.mp4"):
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

    latest_internal = proj.meta.get("last_internal_render") or None
    history = proj.meta.get("internal_render_history") or []
    project_jobs = jobs.list_for_project(project_id)
    active_internal_jobs = [
        j.__dict__
        for j in project_jobs
        if j.type == "internal_video" and j.status in ("queued", "running", "canceled", "failed")
    ][:8]
    return {
        "images": imgs,
        "videos": vids,
        "deforum_exports": defs,
        "project_id": project_id,
        "latest_internal_render": latest_internal,
        "internal_render_history": history[-20:] if isinstance(history, list) else [],
        "active_internal_jobs": active_internal_jobs,
    }

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
        res = aws_integration.test_credentials(bucket=req.bucket)
        return {"ok": res.ok, "account": res.account, "region": res.region}
    except Exception as e:
        raise HTTPException(status_code=501, detail=str(e))

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
        except Exception as e:
            result["upload_error"] = str(e)
    return result

@app.post("/v1/cloud/lightning/bundle")
def cloud_lightning_bundle(req: CloudLightningBundleRequest):
    try:
        return lightning_integration.generate_lightning_bundle(req.output_dir)
    except Exception as e:
        raise HTTPException(500, str(e))

# ------------------------------
# Model Manager (GUI)
# ------------------------------

@app.get("/v1/models/catalog")
def models_catalog():
    return models.catalog()

@app.get("/v1/models/tasks")
def models_tasks():
    return {"tasks": [t.__dict__ for t in models.tasks.list()]}

@app.post("/v1/models/accept")
def models_accept(req: dict[str, Any]):
    model_id = str(req.get("model_id") or "")
    license_id = str(req.get("license_id") or "")
    models.accept_license(model_id, license_id)
    return {"ok": True}

@app.post("/v1/models/install")
def models_install(req: dict[str, Any]):
    model_id = str(req.get("model_id") or "")
    task = models.install(model_id)
    return {"task": task.__dict__}

@app.post("/v1/models/install_pack")
def models_install_pack(req: dict[str, Any]):
    pack_id = str(req.get("pack_id") or "")
    tasks = models.install_pack(pack_id)
    return {"tasks": [t.__dict__ for t in tasks]}

@app.post("/v1/models/import/civitai")
def models_import_civitai(req: dict[str, Any]):
    url_or_id = str(req.get("url") or req.get("id") or "")
    entry = models.civitai_import(url_or_id)
    return {"entry": entry}

@app.post("/v1/models/import/local")
def models_import_local(req: dict[str, Any]):
    path = str(req.get("file_path") or "")
    name = req.get("name")
    folder = str(req.get("folder") or "checkpoints")
    entry = models.import_local(path, name=name, folder=folder)
    return {"entry": entry}

@app.post("/v1/models/remove_user")
def models_remove_user(req: dict[str, Any]):
    model_id = str(req.get("model_id") or "")
    models.remove_user_model(model_id)
    return {"ok": True}
