from __future__ import annotations

import errno
import json
import logging
import os
import re
import shutil
import threading
import time
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

import requests

from ..domain.model_lanes import (
    annotate_entry,
    can_promote,
    infer_lane,
    normalize_lane,
    promotion_blockers,
)
from ..errors import UserFacingError
from .engine_packages import (
    checked_files,
    package_manifest,
    runtime_status,
    safe_file,
    validate_package,
)
from .hf_auth import HfTokenCandidate, hf_token_candidates
from .model_catalog import built_in_catalog, built_in_packs
from .model_weights import is_real_weight_file
from .secrets import SecretStore
from .setup_wizard import (
    _ollama_base,  # reuse
    comfy_portable_installed,
    comfy_portable_root,
)
from .tensorrt_bundle_migration import (
    MODEL_ID as LOCAL_SD15_TENSORRT_MODEL_ID,
)
from .tensorrt_bundle_migration import (
    TensorRTBundleContract,
    TensorRTBundleMigration,
    TensorRTMigrationCancelled,
)

try:
    from huggingface_hub import constants as hf_hub_constants  # type: ignore
    from huggingface_hub import snapshot_download  # type: ignore
except Exception:  # pragma: no cover
    hf_hub_constants = None  # type: ignore
    snapshot_download = None  # type: ignore

try:
    from ..integrations.azure import AzureModelCache
except Exception:  # pragma: no cover - optional integration
    AzureModelCache = None  # type: ignore

try:
    from ..integrations.aws import S3ModelCache
except Exception:  # pragma: no cover - optional integration
    S3ModelCache = None  # type: ignore

try:
    from ..integrations.hf_bucket import HFBucketModelCache
    from ..integrations.hf_bucket import download_bucket_file as _hf_bucket_download_file
    from ..integrations.hf_bucket import download_bucket_snapshot as _hf_bucket_download_snapshot
except Exception:  # pragma: no cover - optional integration
    HFBucketModelCache = None  # type: ignore
    _hf_bucket_download_snapshot = None  # type: ignore
    _hf_bucket_download_file = None  # type: ignore


logger = logging.getLogger(__name__)
_HF_SNAPSHOT_DOWNLOAD_LOCK = threading.Lock()


# ------------------------------ persistence ------------------------------

def _config_dir(data_dir: Path) -> Path:
    return _ensure_managed_dir(data_dir / "config", label="config")

def _read_json(path: Path, default: Any) -> Any:
    candidates = [path, path.with_suffix(path.suffix + ".tmp")]
    try:
        candidates.extend(
            sorted(
                path.parent.glob(f".{path.name}.*.tmp"),
                key=lambda candidate: candidate.stat().st_mtime_ns,
                reverse=True,
            )
        )
    except OSError:
        pass
    for candidate in dict.fromkeys(candidates):
        try:
            if not candidate.is_file():
                continue
            value = json.loads(candidate.read_text(encoding="utf-8"))
            if candidate != path:
                try:
                    _replace_with_retry(candidate, path)
                except OSError:
                    pass
            return value
        except Exception:
            continue
    return default


def _replace_with_retry(source: Path, target: Path, *, attempts: int = 5) -> None:
    for attempt in range(max(1, attempts)):
        try:
            os.replace(source, target)
            return
        except OSError as exc:
            retryable = (
                getattr(exc, "winerror", None) in {5, 32, 33}
                or exc.errno in {errno.EACCES, errno.EBUSY}
            )
            if not retryable or attempt + 1 >= attempts:
                raise
            time.sleep(0.04 * (attempt + 1))


def _write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.{uuid.uuid4().hex}.tmp")
    try:
        with tmp.open("w", encoding="utf-8", newline="\n") as handle:
            json.dump(obj, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(tmp, path)
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass


def _is_hf_auth_failure(error: Exception) -> bool:
    response = getattr(error, "response", None)
    if getattr(response, "status_code", None) in (401, 403):
        return True
    message = str(error).lower()
    return any(
        marker in message
        for marker in (
            "401",
            "403",
            "unauthorized",
            "forbidden",
            "invalid user token",
            "oauth token signature verification failed",
        )
    )


def _normalize_path(path: Path | str) -> str:
    raw = os.fspath(path)
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    return os.path.normcase(os.path.normpath(os.path.abspath(raw)))


def _same_path(left: Path | str, right: Path | str) -> bool:
    return _normalize_path(left) == _normalize_path(right)


# Renamed aside when a model tree is unreadable (e.g. WinError 1392 on USB).
_CORRUPTED_QUARANTINE_SUFFIX = ".__corrupted_quarantine"


def _path_exists_safe(path: Path) -> bool:
    """Return whether *path* exists; treat unreadable volumes as missing.

    On Windows, corrupt/unreadable paths (e.g. WinError 1392 on USB/external
    mounts) can raise OSError from ``Path.exists()`` / ``stat()`` instead of
    returning False. Catalog and install probes must never crash on that.
    """
    try:
        return path.exists()
    except OSError as exc:
        logger.warning("Treating unreadable path as missing: %s (%s)", path, exc)
        return False


def _path_is_dir_safe(path: Path) -> bool:
    try:
        return path.is_dir()
    except OSError as exc:
        logger.warning("Treating unreadable path as non-directory: %s (%s)", path, exc)
        return False


def _path_is_file_safe(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError as exc:
        logger.warning("Treating unreadable path as non-file: %s (%s)", path, exc)
        return False


def _quarantine_unreadable_model_dir(model_dir: Path) -> Path | None:
    """Best-effort rename of a corrupted model folder so later scans skip it."""
    name = model_dir.name
    if not name or name.endswith(_CORRUPTED_QUARANTINE_SUFFIX):
        return None
    target = model_dir.with_name(f"{name}{_CORRUPTED_QUARANTINE_SUFFIX}")
    try:
        if target.exists():
            target = model_dir.with_name(f"{name}.{int(time.time())}{_CORRUPTED_QUARANTINE_SUFFIX}")
    except OSError:
        target = model_dir.with_name(f"{name}.{int(time.time())}{_CORRUPTED_QUARANTINE_SUFFIX}")
    try:
        model_dir.rename(target)
        logger.warning("Quarantined unreadable model directory: %s -> %s", model_dir, target)
        return target
    except OSError as exc:
        logger.warning("Could not quarantine unreadable model dir %s: %s", model_dir, exc)
        return None


def _read_reparse_target(path: Path) -> Path | None:
    try:
        raw = os.readlink(path)
    except OSError:
        return None
    if raw.startswith("\\\\?\\"):
        raw = raw[4:]
    if not os.path.isabs(raw):
        raw = os.path.join(os.fspath(path.parent), raw)
    return Path(os.path.abspath(raw))


def _repair_mutual_junction(path: Path) -> bool:
    if os.name != "nt":
        return False
    target = _read_reparse_target(path)
    if target is None:
        return False
    reverse = _read_reparse_target(target)
    if reverse is None or not _same_path(reverse, path):
        return False
    try:
        os.rmdir(path)
        path.mkdir(parents=True, exist_ok=True)
        return True
    except OSError:
        return False


def _repair_mutual_junction_chain(path: Path) -> bool:
    current = path
    while True:
        if _repair_mutual_junction(current):
            return True
        if current.parent == current:
            return False
        current = current.parent


def _ensure_managed_dir(path: Path, *, label: str) -> Path:
    candidate = Path(os.path.abspath(os.fspath(path.expanduser())))
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        return candidate
    except (OSError, RuntimeError) as exc:
        if _repair_mutual_junction_chain(candidate):
            candidate.mkdir(parents=True, exist_ok=True)
            return candidate
        raise UserFacingError(
            f"Studio {label} path is invalid: {candidate}",
            hint="Restart EDMG Studio so it can repair the storage junctions, then retry.",
            code="INVALID_STORAGE_PATH",
        ) from exc


def _entry_render(entry: dict[str, Any]) -> dict[str, Any]:
    render = entry.get("render")
    return dict(render) if isinstance(render, dict) else {}


def _entry_target(entry: dict[str, Any]) -> dict[str, Any]:
    target = entry.get("target")
    return dict(target) if isinstance(target, dict) else {}


def _entry_engine(entry: dict[str, Any]) -> str:
    render = _entry_render(entry)
    target = _entry_target(entry)
    engine = str(render.get("engine") or target.get("engine") or "").strip().lower()
    if engine:
        return engine
    kind = str(entry.get("kind") or "").strip().lower()
    if kind == "diffusers":
        return "internal"
    return "comfyui"


def _entry_family(entry: dict[str, Any]) -> str | None:
    family = str(entry.get("family") or _entry_render(entry).get("family") or "").strip().lower()
    return family or None


def _entry_support_flags(entry: dict[str, Any]) -> dict[str, bool]:
    kind = str(entry.get("kind") or "").strip().lower()
    engine = _entry_engine(entry)
    family = _entry_family(entry)
    if kind in {"checkpoint", "diffusers"}:
        if engine == "internal" and family == "flux":
            return {
                "supports_txt2img": True,
                "supports_img2img": False,
                "supports_inpaint": False,
                "supports_outpaint": False,
                "supports_controlnet": False,
            }
        return {
            "supports_txt2img": True,
            "supports_img2img": True,
            "supports_inpaint": True,
            "supports_outpaint": True,
            "supports_controlnet": not (engine == "internal" and family == "sd35"),
        }
    if kind == "runtime_bundle" and engine == "tensorrt_standalone":
        return {
            "supports_txt2img": True,
            "supports_img2img": False,
            "supports_inpaint": False,
            "supports_outpaint": False,
            "supports_controlnet": False,
        }
    return {
        "supports_txt2img": False,
        "supports_img2img": False,
        "supports_inpaint": False,
        "supports_outpaint": False,
        "supports_controlnet": False,
    }


def _normalize_catalog_entry(entry: dict[str, Any]) -> dict[str, Any]:
    item = dict(entry)
    render = _entry_render(item)
    engine = _entry_engine(item)
    family = _entry_family(item)
    kind = str(item.get("kind") or "").strip().lower()
    render_modes = [str(mode).strip() for mode in (render.get("render_modes") or []) if str(mode).strip()]
    if kind in {"checkpoint", "diffusers"} and "stills" not in render_modes:
        render_modes.append("stills")
    if (
        kind == "diffusers"
        and item.get("supports_internal_video", True) is not False
        and "internal_video" not in render_modes
    ):
        render_modes.append("internal_video")
    render["engine"] = engine
    render["family"] = family
    render["render_modes"] = render_modes
    item["render"] = render
    item["engine"] = engine
    item["family"] = family
    item.update(_entry_support_flags(item))
    return annotate_entry(item)


# Hugging Face snapshots often contain complete checkpoint exports, PyTorch
# Diffusers trees, ONNX/OpenVINO exports, and several precision variants in the
# same repository. Studio's internal renderer only needs one runnable PyTorch
# Diffusers layout. These profiles make that selection explicit and testable.
_HF_METADATA_PATTERNS = (
    "*.json",
    "**/*.json",
    "*.txt",
    "**/*.txt",
    "*.model",
    "**/*.model",
    "*.tiktoken",
    "**/*.tiktoken",
    "*.yaml",
    "**/*.yaml",
    "*.yml",
    "**/*.yml",
)
_HF_NON_PYTORCH_IGNORE_PATTERNS = (
    "*.ckpt",
    "**/*.ckpt",
    "*.onnx",
    "**/*.onnx",
    "*.onnx_data",
    "**/*.onnx_data",
    "*.xml",
    "**/*.xml",
    "*.msgpack",
    "**/*.msgpack",
    "*.h5",
    "**/*.h5",
    "*.pb",
    "**/*.pb",
    "*.tflite",
    "**/*.tflite",
    "*.gguf",
    "**/*.gguf",
    "onnx/**",
    "**/onnx/**",
    "openvino/**",
    "**/openvino/**",
    "flax/**",
    "**/flax/**",
)
_HF_DUPLICATE_VARIANT_IGNORE_PATTERNS = (
    "*.fp16.safetensors",
    "**/*.fp16.safetensors",
    "*.bf16.safetensors",
    "**/*.bf16.safetensors",
    "*.fp32.safetensors",
    "**/*.fp32.safetensors",
    "*.non_ema.safetensors",
    "**/*.non_ema.safetensors",
    "*.fp16.bin",
    "**/*.fp16.bin",
    "*.bf16.bin",
    "**/*.bf16.bin",
    "*.fp32.bin",
    "**/*.fp32.bin",
    "*.non_ema.bin",
    "**/*.non_ema.bin",
    "*.fp16.safetensors.index.json",
    "**/*.fp16.safetensors.index.json",
    "*.bf16.safetensors.index.json",
    "**/*.bf16.safetensors.index.json",
    "*.fp32.safetensors.index.json",
    "**/*.fp32.safetensors.index.json",
    "*.non_ema.safetensors.index.json",
    "**/*.non_ema.safetensors.index.json",
    "*.fp16.bin.index.json",
    "**/*.fp16.bin.index.json",
    "*.bf16.bin.index.json",
    "**/*.bf16.bin.index.json",
    "*.fp32.bin.index.json",
    "**/*.fp32.bin.index.json",
    "*.non_ema.bin.index.json",
    "**/*.non_ema.bin.index.json",
)
_HF_COMMON_DIFFUSERS_COMPONENTS = (
    "unet",
    "vae",
    "text_encoder",
    "text_encoder_2",
    "text_encoder_3",
    "transformer",
    "image_encoder",
    "controlnet",
    "prior",
    "decoder",
    "vqvae",
    "movq",
)


@dataclass(frozen=True)
class HFSnapshotDownloadProfile:
    name: str
    allow_patterns: tuple[str, ...]
    ignore_patterns: tuple[str, ...]


def _hf_snapshot_download_profile(
    entry: dict[str, Any],
    *,
    weight_format: str,
    components: list[str] | tuple[str, ...] | None = None,
) -> HFSnapshotDownloadProfile:
    """Return the bounded artifact profile used for a Hub snapshot operation.

    ``metadata`` fetches the Diffusers layout/config/tokenizer/scheduler plan.
    ``safetensors`` fetches only default-precision safetensors. ``bin`` is a
    compatibility fallback and should only be requested for components still
    missing after the safetensors pass.
    """

    normalized_format = str(weight_format or "").strip().lower()
    if normalized_format not in {"metadata", "safetensors", "bin"}:
        raise ValueError(f"Unsupported Hugging Face snapshot profile: {weight_format}")

    kind = str(entry.get("kind") or "").strip().lower()
    pipeline_layout = kind in {"diffusers", "video_diffusers"}
    allow: list[str] = list(_HF_METADATA_PATTERNS)
    if normalized_format != "metadata":
        extension = "safetensors" if normalized_format == "safetensors" else "bin"
        if pipeline_layout:
            selected_components = tuple(
                dict.fromkeys(
                    str(component).strip().strip("/")
                    for component in (components or _HF_COMMON_DIFFUSERS_COMPONENTS)
                    if str(component).strip().strip("/")
                )
            )
            for component in selected_components:
                allow.extend(
                    (
                        f"{component}/*.{extension}",
                        f"{component}/**/*.{extension}",
                    )
                )
        else:
            # ControlNet, motion-adapter, and similar component repositories
            # conventionally keep their inference weights at the repository root.
            for stem in (
                "diffusion_pytorch_model",
                "pytorch_model",
                "model",
            ):
                allow.append(f"{stem}*.{extension}")

    return HFSnapshotDownloadProfile(
        name=(
            "metadata"
            if normalized_format == "metadata"
            else f"{normalized_format}-default-precision"
        ),
        allow_patterns=tuple(dict.fromkeys(allow)),
        ignore_patterns=tuple(
            dict.fromkeys(
                _HF_NON_PYTORCH_IGNORE_PATTERNS
                + _HF_DUPLICATE_VARIANT_IGNORE_PATTERNS
            )
        ),
    )


def _hf_profile_matches_path(path: str, profile: HFSnapshotDownloadProfile) -> bool:
    normalized = str(path or "").replace("\\", "/").lstrip("/")
    if not normalized:
        return False
    if not any(fnmatch(normalized, pattern) for pattern in profile.allow_patterns):
        return False
    return not any(fnmatch(normalized, pattern) for pattern in profile.ignore_patterns)


# ------------------------------ tasks ------------------------------


class ModelTaskCancelled(Exception):
    """Cooperative cancellation signal for a managed model task."""

@dataclass
class ModelTask:
    id: str
    name: str
    status: str = "queued"  # queued|running|done|failed|interrupted|cancelled
    progress: float | None = None
    last_log: str = ""
    error: str | None = None
    error_hint: str | None = None
    error_code: str | None = None
    started_at: float | None = None
    ended_at: float | None = None
    model_id: str | None = None
    stage: str = "queued"
    bytes_completed: int = 0
    bytes_total: int | None = None
    files_completed: int = 0
    files_total: int | None = None
    cancel_requested: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "progress": self.progress,
            "last_log": self.last_log,
            "error": self.error,
            "error_hint": self.error_hint,
            "error_code": self.error_code,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "model_id": self.model_id,
            "stage": self.stage,
            "bytes_completed": self.bytes_completed,
            "bytes_total": self.bytes_total,
            "files_completed": self.files_completed,
            "files_total": self.files_total,
            "cancel_requested": self.cancel_requested,
        }


class ModelTaskManager:
    _ACTIVE_STATUSES = {"queued", "running"}

    def __init__(self, persistence_path: Path | None = None):
        self._lock = threading.Lock()
        self._tasks: dict[str, ModelTask] = {}
        self._persistence_path = persistence_path
        self._last_progress_persist_at = 0.0
        self._load()

    def _load(self) -> None:
        if self._persistence_path is None:
            return
        raw = _read_json(self._persistence_path, default={})
        rows = raw.get("tasks") if isinstance(raw, dict) else raw
        if not isinstance(rows, list):
            return
        interrupted = False
        allowed = set(ModelTask.__dataclass_fields__)
        for row in rows:
            if not isinstance(row, dict):
                continue
            values = {key: value for key, value in row.items() if key in allowed}
            try:
                task = ModelTask(**values)
            except (TypeError, ValueError):
                continue
            if task.status in self._ACTIVE_STATUSES:
                interrupted = True
                task.status = "interrupted"
                task.stage = "interrupted"
                task.ended_at = time.time()
                task.error = "Studio stopped before this model operation completed."
                task.error_hint = "Retry the model operation to resume any partial download."
                task.error_code = "MODEL_TASK_INTERRUPTED"
                suffix = "Interrupted by a Studio restart. Retry to resume the partial download."
                task.last_log = f"{task.last_log.rstrip()}\n{suffix}".strip()
            self._tasks[task.id] = task
        if interrupted:
            with self._lock:
                self._persist_locked()

    def _persist_locked(self) -> None:
        if self._persistence_path is None:
            return
        try:
            tasks = sorted(
                self._tasks.values(),
                key=lambda task: (task.started_at or 0.0, task.id),
                reverse=True,
            )[:100]
            _write_json(
                self._persistence_path,
                {"version": 1, "tasks": [task.to_dict() for task in tasks]},
            )
        except Exception as exc:
            logger.warning("Could not persist model tasks to %s: %s", self._persistence_path, exc)

    def list(self) -> list[ModelTask]:
        with self._lock:
            return sorted(self._tasks.values(), key=lambda t: (t.started_at or 0), reverse=True)

    def start(
        self,
        name: str,
        fn,
        *args,
        model_id: str | None = None,
        **kwargs,
    ) -> ModelTask:
        if model_id is None and args and isinstance(args[0], dict):
            model_id = str(args[0].get("id") or "").strip() or None
        with self._lock:
            if model_id:
                existing = next(
                    (
                        candidate
                        for candidate in self._tasks.values()
                        if candidate.model_id == model_id
                        and candidate.status in self._ACTIVE_STATUSES
                    ),
                    None,
                )
                if existing is not None:
                    return existing
            task = ModelTask(
                id=str(uuid.uuid4())[:8],
                name=name,
                status="queued",
                model_id=model_id,
            )
            self._tasks[task.id] = task
            self._persist_locked()

        def runner():
            with self._lock:
                task.status = "running"
                task.stage = "starting"
                task.started_at = time.time()
                self._persist_locked()
            try:
                if task.cancel_requested:
                    raise ModelTaskCancelled("Cancelled before the model operation started")
                fn(task, *args, **kwargs)
                with self._lock:
                    task.status = "done"
                    task.stage = "complete"
                    task.progress = 1.0
            except ModelTaskCancelled:
                with self._lock:
                    task.status = "cancelled"
                    task.stage = "cancelled"
                    task.error = None
                    task.error_hint = None
                    task.error_code = None
                    suffix = "Cancelled safely. No incomplete model bundle was published."
                    task.last_log = f"{task.last_log.rstrip()}\n{suffix}".strip()
            except Exception as e:
                with self._lock:
                    task.status = "failed"
                    task.stage = "failed"
                    task.error = str(e)
                    if isinstance(e, UserFacingError):
                        task.error_hint = e.hint
                        task.error_code = e.code
                    else:
                        task.error_hint = None
                        task.error_code = None
                    task.last_log = (task.last_log + "\n" if task.last_log else "") + f"ERROR: {e}"
            finally:
                with self._lock:
                    task.ended_at = time.time()
                    self._persist_locked()

        threading.Thread(target=runner, daemon=True).start()
        return task

    def request_cancel(self, task_id: str) -> ModelTask | None:
        with self._lock:
            task = self._tasks.get(str(task_id or "").strip())
            if task is None:
                return None
            if task.status in self._ACTIVE_STATUSES:
                task.cancel_requested = True
                task.stage = "cancelling"
                task.last_log = "Cancellation requested; Studio is stopping at the next safe copy boundary."
                self._persist_locked()
            return task

    def is_cancel_requested(self, task: ModelTask) -> bool:
        with self._lock:
            return bool(task.cancel_requested)

    @staticmethod
    def log(task: ModelTask, msg: str) -> None:
        task.last_log = msg

    @staticmethod
    def set_progress(task: ModelTask, v: float | None) -> None:
        task.progress = v

    def set_stage(
        self,
        task: ModelTask,
        stage: str,
        *,
        progress: float | None = None,
    ) -> None:
        with self._lock:
            task.stage = str(stage or "").strip() or task.stage
            if progress is not None:
                task.progress = max(0.0, min(1.0, float(progress)))
            self._persist_locked()

    def set_transfer(
        self,
        task: ModelTask,
        *,
        bytes_completed: int,
        bytes_total: int | None = None,
        files_completed: int | None = None,
        files_total: int | None = None,
        progress_floor: float = 0.1,
        progress_ceiling: float = 0.8,
        force_persist: bool = False,
    ) -> None:
        with self._lock:
            task.bytes_completed = max(task.bytes_completed, 0, int(bytes_completed))
            task.bytes_total = (
                max(task.bytes_completed, int(bytes_total))
                if bytes_total is not None and int(bytes_total) > 0
                else None
            )
            if files_completed is not None:
                task.files_completed = max(task.files_completed, 0, int(files_completed))
            if files_total is not None:
                task.files_total = max(task.files_completed, int(files_total))
            if task.bytes_total:
                fraction = min(1.0, task.bytes_completed / task.bytes_total)
                task.progress = progress_floor + ((progress_ceiling - progress_floor) * fraction)
            now = time.monotonic()
            if force_persist or now - self._last_progress_persist_at >= 1.0:
                self._last_progress_persist_at = now
                self._persist_locked()

    def persist(self, task: ModelTask | None = None) -> None:
        del task
        with self._lock:
            self._persist_locked()


# ------------------------------ manager ------------------------------

class _CompositeModelCache:
    """Try multiple remote caches in priority order.

    HF bucket is normally first; S3/Azure remain secondary mirrors and restore
    fallbacks. Uploads are best-effort fan-out so a configured secondary cache
    can keep a copy without blocking the primary local install path.
    """

    def __init__(self, caches: list[Any]):
        self.caches = [cache for cache in caches if cache is not None]
        self._last_cache = self.caches[0] if self.caches else None

    @property
    def label(self) -> str:
        labels = [str(getattr(cache, "label", cache.__class__.__name__)) for cache in self.caches]
        if not labels:
            return "No model cache"
        if len(labels) == 1:
            return labels[0]
        return f"{labels[0]} primary + " + " + ".join(f"{label} secondary" for label in labels[1:])

    @property
    def settings(self) -> Any:
        return getattr(self._last_cache, "settings", None)

    def _call_exists(self, method_name: str, entry: dict[str, Any], path: Path) -> str | None:
        for cache in self.caches:
            method = getattr(cache, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(entry, path)
            except Exception:
                continue
            if result:
                self._last_cache = cache
                return str(result)
        return None

    def _call_download(self, method_name: str, entry: dict[str, Any], dest: Path) -> bool:
        for cache in self.caches:
            method = getattr(cache, method_name, None)
            if not callable(method):
                continue
            try:
                if method(entry, dest):
                    self._last_cache = cache
                    return True
            except Exception:
                continue
        return False

    def _call_upload(self, method_name: str, entry: dict[str, Any], path: Path) -> str:
        first_object: str | None = None
        first_cache: Any | None = None
        for cache in self.caches:
            method = getattr(cache, method_name, None)
            if not callable(method):
                continue
            try:
                result = method(entry, path)
            except Exception:
                continue
            if not result:
                continue
            object_name = str(result)
            if object_name and first_object is None:
                first_object = object_name
                first_cache = cache
        if first_object is None:
            raise RuntimeError("No configured model cache accepted the upload")
        self._last_cache = first_cache
        return first_object

    def model_exists(self, entry: dict[str, Any], path: Path) -> str | None:
        return self._call_exists("model_exists", entry, path)

    def model_directory_exists(self, entry: dict[str, Any], path: Path) -> str | None:
        return self._call_exists("model_directory_exists", entry, path)

    def model_directory_complete(
        self,
        remote_prefix: str,
        *,
        model_entry: dict[str, Any],
    ) -> bool:
        complete = getattr(self._last_cache, "model_directory_complete", None)
        if not callable(complete):
            return False
        return bool(complete(remote_prefix, model_entry=model_entry))

    def download_model(self, entry: dict[str, Any], dest: Path) -> bool:
        return self._call_download("download_model", entry, dest)

    def download_model_directory(self, entry: dict[str, Any], dest: Path) -> bool:
        return self._call_download("download_model_directory", entry, dest)

    def upload_model(self, entry: dict[str, Any], path: Path) -> str:
        return self._call_upload("upload_model", entry, path)

    def upload_model_directory(self, entry: dict[str, Any], path: Path) -> str:
        return self._call_upload("upload_model_directory", entry, path)


class ModelManager:
    def __init__(
        self,
        data_dir: Path,
        models_dir: Path,
        external_dir: Path,
        comfyui_url: str,
        ollama_url: str,
        secrets: SecretStore | None = None,
    ):
        self.data_dir = data_dir
        self.models_dir = models_dir
        self.external_dir = external_dir
        self.comfyui_url = comfyui_url.rstrip("/")
        self.ollama_url = _ollama_base(ollama_url)
        self.secrets = secrets

        cfg = _config_dir(self.data_dir)
        task_dir = _ensure_managed_dir(self.data_dir / "tasks", label="task history")
        self.tasks = ModelTaskManager(task_dir / "model_tasks.json")
        self.model_cache = self._build_model_cache()
        self._user_models_path = cfg / "models_user.json"
        self._accept_path = cfg / "licenses_accepted.json"
        self._cloud_models_path = cfg / "models_cloud.json"
        self._lane_overrides_path = cfg / "model_lane_overrides.json"
        self._benchmarks_path = cfg / "model_benchmarks.json"
        self._tensorrt_bundle_migration = TensorRTBundleMigration(self.models_dir)

        self._lock = threading.Lock()
        self._ollama_probe_lock = threading.Lock()
        self._ollama_models_cache: set[str] = set()
        self._ollama_models_cache_at = 0.0

    def refresh_model_cache(self):
        """Rebuild the active model cache after settings/env changes."""
        self.model_cache = self._build_model_cache()
        return self.model_cache

    def _build_model_cache(self):
        # Priority: Hugging Face bucket first, then AWS S3, then Azure. When
        # more than one cache is configured, keep all of them so HF can be the
        # primary model mirror while S3/Azure remain secondary storage.
        caches: list[Any] = []
        if HFBucketModelCache is not None:
            try:
                cache = HFBucketModelCache.from_runtime(
                    models_dir=self.models_dir,
                    secrets_store=self.secrets,
                )
                if cache is not None:
                    caches.append(cache)
            except Exception:
                pass
        for cache_type in (S3ModelCache, AzureModelCache):
            if cache_type is None:
                continue
            try:
                cache = cache_type.from_env()
            except Exception:
                continue
            if cache is not None:
                caches.append(cache)
        if len(caches) > 1:
            return _CompositeModelCache(caches)
        return caches[0] if caches else None

    def _model_cache_label(self) -> str:
        cache = getattr(self, "model_cache", None)
        label = getattr(cache, "label", "")
        if label:
            return str(label)
        cache_name = cache.__class__.__name__.lower() if cache is not None else ""
        if "s3" in cache_name:
            return "S3 model cache"
        return "Azure model cache"

    def _model_storage_mode(self) -> str:
        raw = (
            os.getenv("EDMG_MODEL_STORAGE_MODE", "").strip().lower()
            or os.getenv("EDMG_AWS_MODEL_CACHE_MODE", "").strip().lower()
            or os.getenv("EDMG_MODEL_CACHE_MODE", "").strip().lower()
        )
        if raw in {"cloud_only", "s3_only", "remote_only"}:
            return "cloud_only"
        return "local_cache"

    def _cloud_models(self) -> dict[str, Any]:
        data = _read_json(self._cloud_models_path, default={})
        return data if isinstance(data, dict) else {}

    def _write_cloud_models(self, data: dict[str, Any]) -> None:
        _write_json(self._cloud_models_path, data)

    def _record_cloud_model(self, entry: dict[str, Any], object_name: str, *, mode: str) -> None:
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            return
        cache = getattr(self, "model_cache", None)
        settings = getattr(cache, "settings", None)
        record: dict[str, Any] = {
            "provider": self._model_cache_label(),
            "object": object_name,
            "mode": mode,
            "stored_at": time.time(),
        }
        for attr in ("bucket", "container", "prefix", "region", "endpoint_url"):
            value = getattr(settings, attr, None)
            if value:
                record[attr] = value
        data = self._cloud_models()
        data[model_id] = record
        self._write_cloud_models(data)

    def _cloud_model_record(self, model_id: str) -> dict[str, Any] | None:
        record = self._cloud_models().get(str(model_id or ""))
        return record if isinstance(record, dict) else None

    def _cache_entry_from_cloud_record(self, entry: dict[str, Any], record: dict[str, Any] | None) -> dict[str, Any]:
        if not isinstance(record, dict):
            return entry
        object_name = str(record.get("object") or record.get("key") or "").strip()
        if not object_name:
            return entry

        cache_entry = dict(entry)
        cache_entry["s3_key"] = object_name
        bucket = str(record.get("bucket") or "").strip()
        if bucket:
            cache_entry["s3_bucket"] = bucket
        return cache_entry

    def _cache_model_exists(self, entry: dict[str, Any], dest: Path) -> str | None:
        cache = getattr(self, "model_cache", None)
        exists = getattr(cache, "model_exists", None)
        if cache is None or not callable(exists):
            return None
        return exists(entry, dest)

    def _cache_snapshot_exists(
        self,
        entry: dict[str, Any],
        dest: Path,
        *,
        require_complete: bool = False,
    ) -> str | None:
        cache = getattr(self, "model_cache", None)
        exists = getattr(cache, "model_directory_exists", None)
        if cache is None or not callable(exists):
            return None
        remote_prefix = exists(entry, dest)
        if not remote_prefix:
            return None
        if not require_complete:
            return str(remote_prefix)
        complete = getattr(cache, "model_directory_complete", None)
        if not callable(complete):
            logger.warning(
                "Ignoring unvalidated remote model directory %s; cache does not expose completeness validation.",
                remote_prefix,
            )
            return None
        try:
            return (
                str(remote_prefix)
                if complete(str(remote_prefix), model_entry=entry)
                else None
            )
        except Exception as exc:
            logger.warning(
                "Could not validate remote model directory %s: %s",
                remote_prefix,
                exc,
            )
            return None

    def _cloud_temp_path(self, dest: Path) -> Path:
        root = _ensure_managed_dir(self.data_dir / "cache" / "model_transfers", label="model transfer cache")
        return root / uuid.uuid4().hex / dest.name

    def _all_entries(self) -> list[dict[str, Any]]:
        built = [_normalize_catalog_entry(entry) for entry in built_in_catalog()]
        user = _read_json(self._user_models_path, default=[])
        if not isinstance(user, list):
            user = []
        user = [_normalize_catalog_entry(entry) for entry in user if isinstance(entry, dict)]
        return built + user

    def _find_entry(self, model_id: str) -> dict[str, Any] | None:
        return next(
            (e for e in self._all_entries() if isinstance(e, dict) and e.get("id") == model_id),
            None,
        )

    # ---- catalog ----
    def catalog(self, hardware: dict[str, Any] | None = None) -> dict[str, Any]:
        built = [_normalize_catalog_entry(entry) for entry in built_in_catalog()]
        user = _read_json(self._user_models_path, default=[])
        if not isinstance(user, list):
            user = []
        user = [_normalize_catalog_entry(entry) for entry in user if isinstance(entry, dict)]
        accepted = _read_json(self._accept_path, default={})
        if not isinstance(accepted, dict):
            accepted = {}
        overrides = _read_json(self._lane_overrides_path, default={})
        if not isinstance(overrides, dict):
            overrides = {}
        benchmarks = _read_json(self._benchmarks_path, default={})
        if not isinstance(benchmarks, dict):
            benchmarks = {}

        def _apply_lane(entry: dict[str, Any]) -> dict[str, Any]:
            model_id = str(entry.get("id") or "")
            override = overrides.get(model_id) if isinstance(overrides.get(model_id), dict) else None
            lane_value = str((override or {}).get("lane") or "") or None
            annotated = annotate_entry(entry, lane_override=lane_value)
            bench = benchmarks.get(model_id) if isinstance(benchmarks.get(model_id), dict) else None
            annotated["benchmark"] = {
                "present": bool(bench),
                "summary": (bench or {}).get("summary"),
                "updated_at": (bench or {}).get("updated_at"),
            }
            return annotated

        built = [_apply_lane(entry) for entry in built]
        user = [_apply_lane(entry) for entry in user]

        installed = self._installed_map(built + user)
        cloud = {key: value for key, value in self._cloud_models().items() if package_manifest(key) is None}
        for entry in built:
            if package_manifest(entry["id"]):
                entry["package_status"] = self.engine_package_status(entry["id"], hardware)

        return {
            "catalog": built,
            "user": user,
            "packs": built_in_packs(),
            "accepted": accepted,
            "installed": installed,
            "cloud": cloud,
            "lanes": {
                "overrides": overrides,
                "order": ["stable", "recommended", "experimental", "research", "legacy"],
            },
            "storage_mode": self._model_storage_mode(),
            "model_cache": self._model_cache_label() if self.model_cache is not None else None,
            "tensorrt_migration": self.legacy_tensorrt_status(),
        }

    def promote_model_lane(self, model_id: str, target_lane: str, *, reason: str | None = None, force: bool = False) -> dict[str, Any]:
        entry = self._find_entry(model_id)
        if not entry:
            raise UserFacingError(f"Unknown model id: {model_id}", hint="Refresh the model catalog and try again.")
        target = normalize_lane(target_lane)
        current = infer_lane(entry)
        overrides = _read_json(self._lane_overrides_path, default={})
        if not isinstance(overrides, dict):
            overrides = {}
        existing = overrides.get(model_id) if isinstance(overrides.get(model_id), dict) else {}
        if existing.get("lane"):
            current = normalize_lane(str(existing.get("lane")))
        benchmarks = _read_json(self._benchmarks_path, default={})
        has_benchmark = isinstance(benchmarks, dict) and isinstance(benchmarks.get(model_id), dict)
        license_accepted = self._is_accepted(model_id)
        blockers = promotion_blockers(
            {**entry, "lane": current},
            target_lane=target,
            has_benchmark=has_benchmark,
            license_accepted=license_accepted,
        )
        if blockers and not force:
            raise UserFacingError(
                f"Promotion blocked for {model_id}",
                hint="; ".join(blockers),
                code="MODEL_PROMOTION_BLOCKED",
            )
        if not can_promote(current, target) and not force:
            raise UserFacingError(
                f"Cannot promote from {current} to {target}",
                hint="Choose a allowed target lane for this model.",
                code="MODEL_LANE_GATE",
            )
        with self._lock:
            overrides[model_id] = {
                "lane": target,
                "from_lane": current,
                "reason": str(reason or "").strip() or None,
                "updated_at": time.time(),
                "force": bool(force),
                "blockers_ignored": blockers if force else [],
            }
            _write_json(self._lane_overrides_path, overrides)
        return {
            "ok": True,
            "model_id": model_id,
            "lane": target,
            "from_lane": current,
            "blockers": blockers,
            "override": overrides[model_id],
        }

    def record_model_benchmark(self, model_id: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        entry = self._find_entry(model_id)
        if not entry:
            raise UserFacingError(f"Unknown model id: {model_id}", hint="Refresh the model catalog and try again.")
        body = dict(payload or {})
        record = {
            "model_id": model_id,
            "lane": infer_lane(entry),
            "summary": str(body.get("summary") or "manual_benchmark_recorded"),
            "metrics": body.get("metrics") if isinstance(body.get("metrics"), dict) else {},
            "passed": bool(body.get("passed", True)),
            "updated_at": time.time(),
        }
        with self._lock:
            data = _read_json(self._benchmarks_path, default={})
            if not isinstance(data, dict):
                data = {}
            data[model_id] = record
            _write_json(self._benchmarks_path, data)
        return {"ok": True, "benchmark": record}

    # ---- acceptance ----
    def accept_license(self, model_id: str, license_id: str) -> None:
        if not model_id or not license_id:
            raise UserFacingError("Missing model_id or license_id", hint="Select a model and accept its license terms.")
        data = _read_json(self._accept_path, default={})
        if not isinstance(data, dict):
            data = {}
        data[model_id] = {
            "license_id": license_id,
            "accepted_at": time.time(),
        }
        _write_json(self._accept_path, data)

    def _is_accepted(self, model_id: str) -> bool:
        data = _read_json(self._accept_path, default={})
        return isinstance(data, dict) and model_id in data

    # ---- add/remove user models ----
    def add_user_model(self, entry: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(entry, dict) or not entry.get("id"):
            raise UserFacingError("Invalid model entry", hint="Provide a valid model entry.")
        with self._lock:
            user = _read_json(self._user_models_path, default=[])
            if not isinstance(user, list):
                user = []
            # replace if exists
            user = [u for u in user if isinstance(u, dict) and u.get("id") != entry["id"]]
            user.append(entry)
            _write_json(self._user_models_path, user)
        return entry

    def remove_user_model(self, model_id: str) -> None:
        with self._lock:
            user = _read_json(self._user_models_path, default=[])
            if not isinstance(user, list):
                return
            user2 = [u for u in user if isinstance(u, dict) and u.get("id") != model_id]
            _write_json(self._user_models_path, user2)

    # ---- install ----
    def install(self, model_id: str) -> ModelTask:
        entry = self._find_entry(model_id)
        if not entry:
            raise UserFacingError(f"Unknown model id: {model_id}", hint="Refresh the model catalog and try again.")
        if entry.get("installable", True) is False:
            raise UserFacingError(
                "This model is discovery-only in Studio right now",
                hint="Open the model card to review the external runtime bundle, or install a Studio-supported checkpoint/diffusers model instead.",
                code="MODEL_BROWSER_ONLY",
            )

        # Enforce license acceptance for any external weights/download.
        if entry.get("kind") != "llm" and not self._is_accepted(model_id):
            raise UserFacingError(
                "License not accepted",
                hint="Open Model Manager, click the model, review license, then click Accept & Install."
            )

        if package_manifest(model_id):
            return self.tasks.start(f"Install: {entry.get('name')}", self._install_engine_package, entry)

        source = (entry.get("source") or "").lower()
        if source == "ollama":
            name = f"Install (Ollama): {entry.get('name')}"
            return self.tasks.start(name, self._install_ollama, entry)
        if source in ("hf", "civitai", "local", "s3", "hf_bucket"):
            name = f"Install: {entry.get('name')}"
            return self.tasks.start(name, self._install_file_model, entry)

        raise UserFacingError("Unsupported model source", hint=f"Source '{source}' is not supported yet.")

    def engine_package_status(self, model_id: str, hardware: dict[str, Any] | None = None) -> dict[str, Any]:
        manifest = package_manifest(model_id)
        entry = self._find_entry(model_id)
        if manifest is None or entry is None:
            raise UserFacingError("Unknown managed engine package", code="MODEL_PACKAGE_UNKNOWN")
        _, dest = self._models_dest(entry)
        validation = validate_package(dest, manifest)
        installed = bool(validation["valid"]) and not self._snapshot_has_incomplete_markers(dest)
        status = runtime_status(model_id, hardware)
        status.update(installed=installed, state="installed" if installed else "installable",
                      files_present=dest.exists(), validation_issues=validation["issues"],
                      download_size_bytes=sum(item["size_bytes"] for item in manifest["files"]))
        if not installed:
            status["blockers"].insert(0, "Install or revalidate the required package files in Models.")
        return status

    def _validate_engine_package(self, task: ModelTask, entry: dict[str, Any]) -> None:
        manifest = package_manifest(entry["id"])
        _, dest = self._models_dest(entry)
        self.tasks.set_stage(task, "validating", progress=0.90)
        receipt = safe_file(dest, "model.json")
        receipt.unlink(missing_ok=True)
        result = validate_package(dest, manifest, verify_hashes=True,
            cancel_check=lambda: self._raise_if_task_cancelled(task, boundary="package validation"))
        if not result["valid"] or self._snapshot_has_incomplete_markers(dest):
            raise UserFacingError("Engine package validation failed",
                hint="; ".join(result["issues"]) or "Incomplete download markers remain; retry installation.",
                code="MODEL_PACKAGE_INVALID")
        self._raise_if_task_cancelled(task, boundary="package publication")
        _write_json(receipt, result)
        self.tasks.set_stage(task, "complete", progress=1.0)

    def validate_engine_package(self, model_id: str) -> ModelTask:
        entry = self._find_entry(model_id)
        if entry is None or package_manifest(model_id) is None:
            raise UserFacingError("Unknown managed engine package", code="MODEL_PACKAGE_UNKNOWN")
        return self.tasks.start(f"Validate: {entry['name']}", self._validate_engine_package, entry)

    def _install_engine_package(self, task: ModelTask, entry: dict[str, Any]) -> None:
        manifest = package_manifest(entry["id"])
        files = checked_files(manifest)
        if self._model_storage_mode() == "cloud_only":
            raise UserFacingError("Engine packages require local storage",
                hint="Select local + cache storage in Settings, then install the package.", code="MODEL_PACKAGE_LOCAL_REQUIRED")
        _, dest = self._models_dest(entry)
        dest.mkdir(parents=True, exist_ok=True)
        for existing in dest.rglob("*"):
            safe_file(dest, existing.relative_to(dest).as_posix())
        for item in files:
            safe_file(dest, item["path"])
        if self._internal_asset_installed(entry, dest):
            self.tasks.set_stage(task, "complete", progress=1.0)
            return
        safe_file(dest, "model.json").unlink(missing_ok=True)
        # Hub may trust its own local metadata for an existing file. Remove only
        # corrupt managed artifacts so retry actually repairs them; retain valid
        # siblings and the resumable transport cache.
        for item in files:
            candidate = safe_file(dest, item["path"])
            if candidate.is_file():
                check = validate_package(dest, {**manifest, "files": [item]}, verify_hashes=True,
                    cancel_check=lambda: self._raise_if_task_cancelled(task, boundary="package repair"))
                if not check["valid"]:
                    candidate.unlink()
        remaining = sum(item["size_bytes"] for item in files
                        if not (dest / item["path"]).is_file() or (dest / item["path"]).stat().st_size != item["size_bytes"])
        if shutil.disk_usage(dest).free < remaining + 1024**3:
            raise UserFacingError("Insufficient disk space for engine package",
                hint=f"Allow {remaining / 1024**3:.1f} GiB plus 1 GiB working space.", code="MODEL_PACKAGE_DISK_SPACE")
        candidates = hf_token_candidates(secrets_store=self.secrets)
        self.tasks.set_stage(task, "downloading", progress=0.05)
        self._append_task_log(task, f"Downloading exactly {len(files)} pinned package files from {manifest['repo_id']}.")
        # Bypass broad metadata profiles and cloud archives: neither can enforce
        # this package's exact allowlist. Hub local_dir preserves subdirectories.
        self._run_hf_download_with_auth_fallback(task, resource=manifest["repo_id"], candidates=candidates,
            download=lambda token: self._download_hf_snapshot(task,
                repo_id=manifest["repo_id"], revision=manifest["revision"], token=token,
                local_dir=str(dest), allow_patterns=[item["path"] for item in files]))
        self._validate_engine_package(task, entry)

    def uninstall(self, model_id: str) -> ModelTask:
        entry = self._find_entry(model_id)
        if entry is None or package_manifest(model_id) is None:
            raise UserFacingError("Uninstall is supported for managed engine packages only", code="MODEL_PACKAGE_UNKNOWN")
        # Task manager serializes operations for the same model. An active
        # install/validation must finish or be cancelled before uninstalling.
        if any(t.model_id == model_id and t.status in {"queued", "running"} for t in self.tasks.list()):
            raise UserFacingError("Package operation is still active", hint="Cancel or wait for the current task, then uninstall.", code="MODEL_PACKAGE_BUSY")
        return self.tasks.start(f"Uninstall: {entry['name']}", self._uninstall_engine_package, entry)

    def _uninstall_engine_package(self, task: ModelTask, entry: dict[str, Any]) -> None:
        _, dest = self._models_dest(entry)
        expected = (self.models_dir / "internal" / entry["target"]["folder"] / entry["id"]).absolute()
        if dest.absolute() != expected or not dest.resolve().is_relative_to(self.models_dir.resolve()):
            raise UserFacingError("Unsafe package directory", code="MODEL_PACKAGE_PATH_INVALID")
        safe_file(dest, "model.json")
        # Refuse links anywhere in the owned tree, including Hub cache paths.
        if dest.exists():
            for item in dest.rglob("*"):
                safe_file(dest, item.relative_to(dest).as_posix())
            self._raise_if_task_cancelled(task, boundary="uninstall")
            shutil.rmtree(dest)
        cloud = self._cloud_models()
        cloud.pop(entry["id"], None)
        self._write_cloud_models(cloud)
        self._append_task_log(task, "Removed local package and partial downloads. Remote cache objects and other models are preserved.")

    def install_pack(self, pack_id: str) -> list[ModelTask]:
        packs = built_in_packs()
        pack = next((p for p in packs if p.get("id") == pack_id), None)
        if not pack:
            raise UserFacingError("Unknown pack", hint="Choose a valid pack.")
        tasks: list[ModelTask] = []
        for mid in (pack.get("models") or []):
            tasks.append(self.install(mid))
        return tasks

    def restore_local(self, model_id: str) -> ModelTask:
        entry = self._find_entry(model_id)
        if not entry:
            raise UserFacingError(f"Unknown model id: {model_id}", hint="Refresh the model catalog and try again.")
        if package_manifest(model_id):
            return self.install(model_id)
        name = f"Restore local: {entry.get('name')}"
        return self.tasks.start(name, self._restore_cloud_model, entry)

    def cancel_task(self, task_id: str) -> ModelTask:
        normalized_task_id = str(task_id or "").strip()
        task = next((item for item in self.tasks.list() if item.id == normalized_task_id), None)
        if task is None:
            raise UserFacingError(
                "The model task was not found",
                hint="Refresh Models and retry with an active model task.",
                code="MODEL_TASK_NOT_FOUND",
                status_code=404,
            )
        return self.tasks.request_cancel(task.id) or task

    # ---- legacy TensorRT bundle migration ----
    def legacy_tensorrt_status(self, *, include_hashes: bool = False) -> dict[str, Any]:
        """Describe the legacy and canonical SD 1.5 TensorRT layouts.

        Catalog calls intentionally use the fast metadata-only form.  The
        explicit import task streams and verifies SHA-256 for every engine and
        records those hashes in the canonical manifest.
        """

        return self._tensorrt_bundle_migration.inspect(include_hashes=include_hashes)

    def resolve_tensorrt_bundle(
        self,
        *,
        additional_paths: Iterable[Path | str] = (),
        verify_engine_hashes: bool = True,
    ) -> TensorRTBundleContract | None:
        """Resolve canonical first, then only fully verified explicit overrides."""

        return self._tensorrt_bundle_migration.resolve_preferred_bundle(
            external_paths=additional_paths,
            verify_engine_hashes=verify_engine_hashes,
        )

    def import_legacy_tensorrt(self) -> ModelTask:
        self._tensorrt_bundle_migration.ensure_migration_available()
        return self.tasks.start(
            "Verify and copy legacy TensorRT engines",
            self._import_legacy_tensorrt_task,
            model_id=LOCAL_SD15_TENSORRT_MODEL_ID,
        )

    def _import_legacy_tensorrt_task(self, task: ModelTask) -> None:
        self.tasks.set_stage(task, "Checking legacy TensorRT engines", progress=0.01)

        def cancelled() -> bool:
            return self.tasks.is_cancel_requested(task)

        def progress(
            bytes_completed: int,
            bytes_total: int,
            files_completed: int,
            files_total: int,
            stage: str,
        ) -> None:
            self.tasks.set_stage(task, stage)
            self.tasks.set_transfer(
                task,
                bytes_completed=bytes_completed,
                bytes_total=bytes_total,
                files_completed=files_completed,
                files_total=files_total,
                progress_floor=0.02,
                progress_ceiling=0.95,
            )
            ModelTaskManager.log(task, stage)

        try:
            result = self._tensorrt_bundle_migration.migrate(
                cancel_check=cancelled,
                progress=progress,
            )
        except TensorRTMigrationCancelled as exc:
            raise ModelTaskCancelled(str(exc)) from exc

        task.bytes_completed = int(result.get("copied_bytes") or task.bytes_completed)
        task.bytes_total = task.bytes_completed
        task.files_completed = int(result.get("copied_file_count") or task.files_completed)
        task.files_total = task.files_completed
        ModelTaskManager.log(
            task,
            (
                "Copied and SHA-256 verified the legacy engines. The source files remain in place. "
                "The canonical bundle is not renderer-ready until its ONNX assets, base-model metadata, "
                "and compiled profile metadata are verified."
            ),
        )

    def cancel_legacy_tensorrt_import(self, task_id: str) -> ModelTask:
        task = next((item for item in self.tasks.list() if item.id == str(task_id or "").strip()), None)
        if task is None:
            raise UserFacingError(
                "The TensorRT import task was not found",
                hint="Refresh Models and retry with the active TensorRT import task.",
                code="MODEL_TASK_NOT_FOUND",
                status_code=404,
            )
        if task.model_id != LOCAL_SD15_TENSORRT_MODEL_ID:
            raise UserFacingError(
                "That task is not a legacy TensorRT import",
                hint="Only the source-preserving TensorRT copy task can be cancelled from this action.",
                code="MODEL_TASK_TYPE_MISMATCH",
                status_code=409,
            )
        return self.tasks.request_cancel(task.id) or task


    # ---- resolution ----
    def _internal_models_dir(self, folder: str) -> Path:
        return _ensure_managed_dir(self.models_dir / "internal" / folder, label="internal models")

    def _models_dest(self, entry: dict[str, Any]) -> tuple[str, Path]:
        """Return (mode, dest_path).

        mode:
          - "file": download/copy a single file into dest_path
          - "snapshot": download a HF repo snapshot into dest_path (directory)
        """
        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        fname = str(entry.get("filename") or "")

        if engine == "internal":
            # Diffusers expects a directory repo snapshot.
            model_dir = self._internal_models_dir(folder) / str(entry.get("id") or "model")
            return "snapshot", model_dir
        if engine == "runtime_bundle":
            bundle_dir = self._internal_models_dir(folder) / str(entry.get("id") or "model")
            return "snapshot", bundle_dir

        # default: comfyui file model
        if not fname:
            fname = "model.safetensors"
        return "file", self._comfy_models_dir(folder) / fname

    # ---- resolution ----
    def _comfy_models_dir(self, folder: str) -> Path:
        return _ensure_managed_dir(self.models_dir / folder, label="models")

    def _legacy_comfy_models_dir(self, folder: str) -> Path | None:
        if comfy_portable_installed(self.external_dir, self.data_dir):
            root = Path(os.path.abspath(os.fspath(comfy_portable_root(self.external_dir, self.data_dir) / "ComfyUI" / "models" / folder)))
            try:
                if _path_exists_safe(root):
                    return root
            except (OSError, RuntimeError):
                if _repair_mutual_junction_chain(root) and _path_exists_safe(root):
                    return root
        return None

    def _cached_ollama_models(self, *, has_ollama_entries: bool) -> set[str]:
        if not has_ollama_entries:
            return set()
        provider = (
            os.getenv("EDMG_AI_PROVIDER", "nemotron_cloud").strip().lower()
            or "nemotron_cloud"
        )
        explicit_probe = (
            os.getenv("EDMG_MODEL_CATALOG_PROBE_OLLAMA", "").strip().lower()
            in {"1", "true", "yes", "on"}
        )
        if provider != "ollama" and not explicit_probe:
            # Model catalog polling must not wait on an unused local service.
            return set()
        try:
            ttl_seconds = max(
                2.0,
                min(
                    300.0,
                    float(os.getenv("EDMG_OLLAMA_CATALOG_CACHE_SECONDS", "30")),
                ),
            )
        except ValueError:
            ttl_seconds = 30.0
        now = time.monotonic()
        with self._ollama_probe_lock:
            if now - self._ollama_models_cache_at < ttl_seconds:
                return set(self._ollama_models_cache)
            models: set[str] = set()
            try:
                response = requests.get(
                    f"{self.ollama_url}/api/tags",
                    timeout=(0.25, 0.75),
                )
                if response.ok:
                    data = response.json() or {}
                    for item in data.get("models") or []:
                        if isinstance(item, dict) and item.get("name"):
                            models.add(str(item["name"]))
            except Exception:
                pass
            self._ollama_models_cache = models
            self._ollama_models_cache_at = time.monotonic()
            return set(models)

    def _installed_map(self, entries: list[dict[str, Any]]) -> dict[str, bool]:
        out: dict[str, bool] = {}
        ollama_models = self._cached_ollama_models(
            has_ollama_entries=any(
                str(entry.get("source") or "").strip().lower() == "ollama"
                for entry in entries
            )
        )

        for e in entries:
            mid = str(e.get("id") or "")
            if not mid:
                continue
            try:
                src = (e.get("source") or "").lower()
                if src == "ollama":
                    out[mid] = str(e.get("ollama_model") or "") in ollama_models
                    continue

                target = e.get("target") or {}
                engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
                folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
                fname = str(e.get("filename") or "")

                if engine == "internal":
                    out[mid] = self._local_installed_path(e) is not None or (
                        package_manifest(mid) is None and self._cloud_model_record(mid) is not None
                    )
                    continue
                if engine == "runtime_bundle":
                    out[mid] = self._local_installed_path(e) is not None or self._cloud_model_record(mid) is not None
                    continue

                if fname:
                    primary = self._comfy_models_dir(folder) / fname
                    legacy_root = self._legacy_comfy_models_dir(folder)
                    out[mid] = _path_exists_safe(primary) or bool(
                        legacy_root and _path_exists_safe(legacy_root / fname)
                    )
                else:
                    out[mid] = False
            except OSError as exc:
                # Belt-and-suspenders: corrupt volumes can raise from exists/stat/listdir
                # deep in install probes (WinError 1392). Never fail the whole catalog.
                logger.warning(
                    "Skipping unreadable model install probe for %s: %s",
                    mid,
                    exc,
                )
                out[mid] = False
        return out

    def _iter_comfy_model_dirs(self, folder: str) -> list[Path]:
        dirs = [self._comfy_models_dir(folder)]
        legacy_root = self._legacy_comfy_models_dir(folder)
        if legacy_root is not None:
            dirs.append(legacy_root)
        return dirs

    def _is_model_weight_file(self, candidate: Path) -> bool:
        return is_real_weight_file(candidate)

    def _internal_component_has_weights(
        self,
        component_dir: Path,
        *,
        weight_format: str | None = None,
    ) -> bool:
        if not _path_exists_safe(component_dir) or not _path_is_dir_safe(component_dir):
            return False
        formats = (
            (str(weight_format).strip().lower(),)
            if weight_format is not None
            else ("safetensors", "bin")
        )
        if any(extension not in {"safetensors", "bin"} for extension in formats):
            return False
        stems = ("diffusion_pytorch_model", "pytorch_model", "model")
        for extension in formats:
            for stem in stems:
                if self._is_model_weight_file(component_dir / f"{stem}.{extension}"):
                    return True
                index_file = component_dir / f"{stem}.{extension}.index.json"
                try:
                    payload = json.loads(index_file.read_text(encoding="utf-8"))
                    weight_map = (
                        payload.get("weight_map") if isinstance(payload, dict) else None
                    )
                    filenames = {
                        str(filename).replace("\\", "/").strip()
                        for filename in (weight_map or {}).values()
                        if str(filename).strip()
                    }
                except (OSError, ValueError, TypeError, json.JSONDecodeError):
                    continue
                shard_pattern = re.compile(
                    rf"^{re.escape(stem)}-\d{{5}}-of-\d{{5}}\."
                    rf"{re.escape(extension)}$",
                    re.IGNORECASE,
                )
                if filenames and all(
                    "/" not in filename
                    and shard_pattern.fullmatch(filename) is not None
                    and self._is_model_weight_file(component_dir / filename)
                    for filename in filenames
                ):
                    return True
        return False

    def _snapshot_has_incomplete_markers(self, path: Path) -> bool:
        if not _path_exists_safe(path) or not _path_is_dir_safe(path):
            return False
        marker_suffixes = (".incomplete", ".partial", ".part", ".tmp")
        walk_error: OSError | None = None

        def capture_error(exc: OSError) -> None:
            nonlocal walk_error
            walk_error = exc

        try:
            for current, directories, filenames in os.walk(path, onerror=capture_error):
                # Hub local-dir cache markers are resumable transport state,
                # not runtime artifacts. Pruning the private cache also avoids
                # repeatedly scanning hundreds of old metadata/partial files
                # during the catalog's installed-state check.
                if _same_path(current, path):
                    directories[:] = [
                        directory
                        for directory in directories
                        if directory.lower() != ".cache"
                    ]
                if any(filename.lower().endswith(marker_suffixes) for filename in filenames):
                    return True
        except OSError as exc:
            walk_error = exc
        if walk_error is not None:
            logger.warning(
                "Treating unreadable snapshot as incomplete: %s (%s)",
                path,
                walk_error,
            )
            return True
        return False

    def _required_diffusers_components(self, path: Path) -> list[str]:
        model_index = path / "model_index.json"
        if not _path_exists_safe(model_index):
            return []
        try:
            data = json.loads(model_index.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(data, dict):
            return []

        weightless_markers = (
            "Tokenizer",
            "TokenizerFast",
            "Scheduler",
            "ImageProcessor",
            "FeatureExtractor",
            "SafetyChecker",
        )
        components: list[str] = []
        for name, spec in data.items():
            if not isinstance(name, str) or not isinstance(spec, list) or len(spec) < 2:
                continue
            class_name = str(spec[1] or "")
            if not class_name:
                # Diffusers uses [null, null] for optional components that are
                # intentionally absent from this snapshot.
                continue
            if any(marker in class_name for marker in weightless_markers):
                continue
            components.append(name)
        return components

    def _diffusers_snapshot_complete(self, path: Path) -> bool:
        if self._snapshot_has_incomplete_markers(path):
            return False
        model_index = path / "model_index.json"
        try:
            index_exists = model_index.exists()
        except OSError as exc:
            # WinError 1392 etc.: treat as not installed and quarantine when possible.
            logger.warning("Treating unreadable path as missing: %s (%s)", model_index, exc)
            _quarantine_unreadable_model_dir(path)
            return False
        if not index_exists:
            return False
        try:
            data = json.loads(model_index.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("Treating unreadable model_index as missing: %s (%s)", model_index, exc)
            _quarantine_unreadable_model_dir(path)
            return False
        except Exception:
            return False
        if not isinstance(data, dict):
            return False

        required_components = self._required_diffusers_components(path)

        if not required_components:
            return False

        try:
            return any(
                all(
                    self._internal_component_has_weights(
                        path / component,
                        weight_format=weight_format,
                    )
                    for component in required_components
                )
                for weight_format in ("safetensors", "bin")
            )
        except OSError as exc:
            logger.warning(
                "Treating unreadable diffusers snapshot as incomplete: %s (%s)",
                path,
                exc,
            )
            _quarantine_unreadable_model_dir(path)
            return False

    def missing_diffusers_components(self, model_id: str) -> list[str]:
        entry = self._find_entry(model_id)
        if not entry:
            return []
        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        if engine != "internal" or str(entry.get("kind") or "").strip().lower() != "diffusers":
            return []
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        path = self._internal_models_dir(folder) / str(model_id or "")
        if not _path_exists_safe(path):
            return ["snapshot"]
        model_index = path / "model_index.json"
        if not _path_exists_safe(model_index):
            return ["model_index.json"]
        try:
            data = json.loads(model_index.read_text(encoding="utf-8"))
        except Exception:
            return ["model_index.json"]
        if not isinstance(data, dict):
            return ["model_index.json"]

        missing: list[str] = []
        for name in self._required_diffusers_components(path):
            if not self._internal_component_has_weights(path / name):
                missing.append(name)
        return missing

    def _clear_incomplete_snapshot(self, dest: Path) -> None:
        if not _path_exists_safe(dest):
            return
        import shutil

        try:
            shutil.rmtree(dest)
        except OSError:
            pass

    def _internal_asset_installed(self, entry: dict[str, Any], path: Path) -> bool:
        manifest = package_manifest(str(entry.get("id") or ""))
        if manifest is not None:
            return validate_package(path, manifest)["valid"] and not self._snapshot_has_incomplete_markers(path)
        if not _path_exists_safe(path):
            return False

        kind = str(entry.get("kind") or "").strip().lower()
        if kind in {"diffusers", "video_diffusers"}:
            return self._diffusers_snapshot_complete(path)
        if self._snapshot_has_incomplete_markers(path):
            return False
        if kind == "transformers":
            try:
                for filename in entry.get("required_files") or ["config.json", "tokenizer_config.json"]:
                    if not isinstance(filename, str) or Path(filename).name != filename:
                        return False
                    metadata = json.loads((path / filename).read_text(encoding="utf-8"))
                    if not isinstance(metadata, dict) or not metadata:
                        return False
                config = json.loads((path / "config.json").read_text(encoding="utf-8"))
                if entry.get("family") and config.get("model_type") != entry["family"]:
                    return False
                return self._internal_component_has_weights(path, weight_format="safetensors")
            except (OSError, ValueError, TypeError):
                return False
        if kind == "motion_adapter":
            try:
                has_config = _path_exists_safe(path / "config.json") or _path_exists_safe(
                    path / "adapter_config.json"
                )
                has_weights = self._internal_component_has_weights(path)
            except OSError as exc:
                logger.warning(
                    "Treating unreadable motion adapter as not installed: %s (%s)",
                    path,
                    exc,
                )
                return False
            return bool(has_config and has_weights)
        if kind == "controlnet":
            if not _path_exists_safe(path / "config.json"):
                return False
            try:
                return self._internal_component_has_weights(path)
            except OSError as exc:
                logger.warning(
                    "Treating unreadable controlnet as not installed: %s (%s)",
                    path,
                    exc,
                )
                return False
        return True

    def _local_installed_path(self, entry: dict[str, Any]) -> Path | None:
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            return None

        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        if engine == "internal":
            path = self._internal_models_dir(folder) / model_id
            return path if self._internal_asset_installed(entry, path) else None
        if engine == "runtime_bundle":
            source_path = str(entry.get("source_path") or "").strip()
            if model_id == LOCAL_SD15_TENSORRT_MODEL_ID:
                contract = self.resolve_tensorrt_bundle(
                    additional_paths=(source_path,) if source_path else (),
                    verify_engine_hashes=True,
                )
                return contract.root if contract is not None else None
            if source_path:
                try:
                    path = Path(source_path).expanduser()
                    if _path_exists_safe(path):
                        return path
                except (OSError, RuntimeError):
                    pass
            path = self._internal_models_dir(folder) / model_id
            return path if _path_exists_safe(path) else None

        filename = str(entry.get("filename") or "")
        if not filename:
            return None

        primary = self._comfy_models_dir(folder) / filename
        if _path_exists_safe(primary):
            return primary
        legacy_root = self._legacy_comfy_models_dir(folder)
        if legacy_root is not None:
            legacy = legacy_root / filename
            if _path_exists_safe(legacy):
                return legacy
        return None

    def _materialize_file_from_model_cache(self, entry: dict[str, Any], dest: Path) -> Path | None:
        cache = getattr(self, "model_cache", None)
        if cache is None:
            return None

        model_id = str(entry.get("id") or "").strip()
        record = self._cloud_model_record(model_id)
        candidates: list[dict[str, Any]] = []
        if record is not None:
            candidates.append(self._cache_entry_from_cloud_record(entry, record))
        candidates.append(entry)

        seen_objects: set[str] = set()
        for candidate in candidates:
            try:
                object_name = self._cache_model_exists(candidate, dest)
            except Exception as exc:
                if record is not None:
                    raise UserFacingError(
                        "Cloud model cache is unavailable",
                        hint=f"Check the {self._model_cache_label()} credentials and bucket/prefix settings, then retry.",
                        code="MODEL_CACHE_UNAVAILABLE",
                    ) from exc
                continue

            if not object_name or object_name in seen_objects:
                continue
            seen_objects.add(str(object_name))

            try:
                if not cache.download_model(candidate, dest):
                    continue
            except Exception as exc:
                raise UserFacingError(
                    "Could not restore model from cloud cache",
                    hint=f"Check that the model object exists in {self._model_cache_label()} and that Studio has read access.",
                    code="MODEL_CACHE_RESTORE_FAILED",
                ) from exc

            mode = str(record.get("mode") or "remote_cache") if record is not None else "remote_cache"
            self._record_cloud_model(entry, str(object_name), mode=mode)
            return dest if dest.exists() else None

        return None

    def _materialize_snapshot_from_model_cache(self, entry: dict[str, Any], dest: Path) -> Path | None:
        cache = getattr(self, "model_cache", None)
        download = getattr(cache, "download_model_directory", None)
        if cache is None or not callable(download):
            return None

        model_id = str(entry.get("id") or "").strip()
        record = self._cloud_model_record(model_id)
        candidates: list[dict[str, Any]] = []
        if record is not None:
            candidates.append(self._cache_entry_from_cloud_record(entry, record))
        candidates.append(entry)

        seen_objects: set[str] = set()
        for candidate in candidates:
            try:
                object_name = self._cache_snapshot_exists(candidate, dest)
            except Exception as exc:
                if record is not None:
                    raise UserFacingError(
                        "Cloud model cache is unavailable",
                        hint=f"Check the {self._model_cache_label()} credentials and bucket/prefix settings, then retry.",
                        code="MODEL_CACHE_UNAVAILABLE",
                    ) from exc
                continue

            if not object_name or object_name in seen_objects:
                continue
            seen_objects.add(str(object_name))

            try:
                if not download(candidate, dest):
                    continue
            except Exception as exc:
                raise UserFacingError(
                    "Could not restore internal model from cloud cache",
                    hint=f"Check that the internal model archive exists in {self._model_cache_label()} and that Studio has read access.",
                    code="MODEL_CACHE_RESTORE_FAILED",
                ) from exc

            if not self._internal_asset_installed(entry, dest):
                raise UserFacingError(
                    "Cloud internal model archive is incomplete",
                    hint="The restored archive did not contain a valid Diffusers snapshot. Rebuild and upload the internal model archive.",
                    code="MODEL_CACHE_RESTORE_INVALID",
                )

            mode = str(record.get("mode") or "remote_cache") if record is not None else "remote_cache"
            self._record_cloud_model(entry, str(object_name), mode=mode)
            return dest

        return None

    def internal_asset_issue(self, model_id: str) -> str | None:
        entry = self._find_entry(model_id)
        if not entry:
            return None
        target = entry.get("target") or {}
        engine = (target.get("engine") if isinstance(target, dict) else "") or "comfyui"
        if engine != "internal":
            return None
        folder = (target.get("folder") if isinstance(target, dict) else None) or "checkpoints"
        path = self._internal_models_dir(folder) / model_id
        if self._local_installed_path(entry) is not None:
            return None
        if _path_exists_safe(path):
            if self._internal_asset_installed(entry, path):
                return None
            return "incomplete"
        if self.is_model_available(model_id, probe_remote=True):
            return None
        return "missing"

    def _find_existing_comfy_file(self, folder: str, ref: str) -> Path | None:
        raw = str(ref or "").strip()
        if not raw:
            return None

        candidates = {raw, Path(raw).name}
        stem = Path(raw).stem
        for model_dir in self._iter_comfy_model_dirs(folder):
            try:
                for candidate in candidates:
                    match = model_dir / candidate
                    if _path_exists_safe(match) and _path_is_file_safe(match):
                        return match
                if stem:
                    for match in model_dir.glob("*"):
                        try:
                            if _path_is_file_safe(match) and match.stem == stem:
                                return match
                        except OSError as exc:
                            logger.warning(
                                "Skipping unreadable Comfy file candidate %s: %s",
                                match,
                                exc,
                            )
            except OSError as exc:
                logger.warning(
                    "Skipping unreadable Comfy model dir %s: %s",
                    model_dir,
                    exc,
                )
        return None

    def resolve_comfy_asset(
        self,
        ref: str,
        *,
        folder: str,
        allowed_kinds: set[str] | None = None,
    ) -> dict[str, Any]:
        raw = str(ref or "").strip()
        if not raw:
            raise UserFacingError("Missing model selection", hint=f"Pick a Studio {folder.rstrip('s')} first.")

        entry = self._find_entry(raw)
        if entry is None:
            normalized_folder = str(folder or "checkpoints").strip().lower()
            for candidate in self._all_entries():
                if not isinstance(candidate, dict):
                    continue
                target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
                candidate_folder = str(target.get("folder") or "checkpoints").strip().lower()
                filename = str(candidate.get("filename") or "").strip()
                if candidate_folder != normalized_folder:
                    continue
                if filename == raw or Path(filename).stem == Path(raw).stem:
                    entry = candidate
                    break

        if entry is not None:
            kind = str(entry.get("kind") or "").strip().lower()
            target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
            engine = str(target.get("engine") or entry.get("engine") or "comfyui").strip().lower()
            if engine != "comfyui":
                raise UserFacingError(
                    f"{entry.get('name') or raw} is not a valid {folder.rstrip('s')} selection",
                    hint="Pick a Studio ComfyUI asset for this workflow.",
                )
            if allowed_kinds and kind not in allowed_kinds:
                expected = ", ".join(sorted(allowed_kinds))
                raise UserFacingError(
                    f"{entry.get('name') or raw} is not a valid {folder.rstrip('s')} selection",
                    hint=f"Pick a Studio asset of type: {expected}.",
                )

            filename = str(
                entry.get("filename")
                or Path(str(entry.get("source_path") or "")).name
                or raw
            ).strip()
            installed = self.resolve_installed_path(str(entry.get("id") or ""), materialize_remote=True)
            resolved_path = installed or self._find_existing_comfy_file(folder, filename)
            if resolved_path is None:
                cloud_record = self._cloud_model_record(str(entry.get("id") or ""))
                hint = "Install the asset in Model Manager, or import it as a local Studio model first."
                if cloud_record is not None:
                    hint = (
                        "This asset is stored in the cloud cache only. Local ComfyUI needs a filesystem model path; "
                        "restore it locally or use a remote worker that mounts/downloads the S3 cache."
                    )
                raise UserFacingError(
                    f"{entry.get('name') or filename} is not installed",
                    hint=hint,
                )

            return {
                "id": entry.get("id"),
                "name": entry.get("name") or Path(filename).stem,
                "kind": entry.get("kind") or folder.rstrip("s"),
                "filename": resolved_path.name,
                "path": str(resolved_path),
                "source": entry.get("source") or "local",
                "folder": folder,
            }

        resolved_path = self._find_existing_comfy_file(folder, raw)
        if resolved_path is None:
            raise UserFacingError(
                f"Unknown Studio asset: {raw}",
                hint=f"Import the file into Models as a {folder.rstrip('s')} first, then retry.",
            )

        return {
            "id": None,
            "name": resolved_path.stem,
            "kind": folder.rstrip("s"),
            "filename": resolved_path.name,
            "path": str(resolved_path),
            "source": "local",
            "folder": folder,
        }

    def resolve_internal_asset(
        self,
        ref: str,
        *,
        folder: str,
        allowed_kinds: set[str] | None = None,
    ) -> dict[str, Any]:
        raw = str(ref or "").strip()
        if not raw:
            raise UserFacingError("Missing model selection", hint=f"Pick a Studio {folder.rstrip('s')} first.")

        entry = self._find_entry(raw)
        if entry is None:
            for candidate in self._all_entries():
                if not isinstance(candidate, dict):
                    continue
                target = candidate.get("target") if isinstance(candidate.get("target"), dict) else {}
                candidate_folder = str(target.get("folder") or "").strip().lower()
                if str(target.get("engine") or "").strip().lower() != "internal":
                    continue
                if candidate_folder != str(folder or "").strip().lower():
                    continue
                if str(candidate.get("id") or "").strip() == raw:
                    entry = candidate
                    break

        if entry is None:
            raise UserFacingError(
                f"Unknown internal Studio asset: {raw}",
                hint=f"Install an internal {folder.rstrip('s')} asset in Models first, then retry.",
            )

        kind = str(entry.get("kind") or "").strip().lower()
        if allowed_kinds and kind not in allowed_kinds:
            expected = ", ".join(sorted(allowed_kinds))
            raise UserFacingError(
                f"{entry.get('name') or raw} is not a valid {folder.rstrip('s')} selection",
                hint=f"Pick a Studio internal asset of type: {expected}.",
            )

        target = entry.get("target") if isinstance(entry.get("target"), dict) else {}
        engine = str(target.get("engine") or entry.get("engine") or "").strip().lower()
        if engine != "internal":
            raise UserFacingError(
                f"{entry.get('name') or raw} is not an internal Studio asset",
                hint="Pick an internal Studio asset for the internal diffusers path.",
            )

        model_id = str(entry.get("id") or "")
        issue = self.internal_asset_issue(model_id)
        if issue == "incomplete":
            raise UserFacingError(
                f"{entry.get('name') or raw} is not installed",
                hint="Reinstall the asset in Model Manager. The current local snapshot is missing required weight files.",
            )

        resolved_path = self.resolve_installed_path(model_id, materialize_remote=True)
        if resolved_path is None:
            hint = "Install the asset in Model Manager, then retry."
            if issue == "incomplete":
                hint = "Reinstall the asset in Model Manager. The current local snapshot is missing required weight files."
            elif self._cloud_model_record(model_id) is not None:
                hint = (
                    "This asset is stored in the cloud cache only. Studio tried to restore it locally for the internal "
                    "renderer but could not materialize a valid Diffusers snapshot."
                )
            raise UserFacingError(
                f"{entry.get('name') or raw} is not installed",
                hint=hint,
            )

        return {
            "id": entry.get("id"),
            "name": entry.get("name") or raw,
            "kind": entry.get("kind") or folder.rstrip("s"),
            "path": str(resolved_path),
            "source": entry.get("source") or "local",
            "folder": folder,
            "engine": "internal",
            "family": entry.get("family"),
        }

    def resolve_loras(self, requested: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        resolved: list[dict[str, Any]] = []
        for item in requested or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name") or "").strip()
            if not name:
                continue
            asset = self.resolve_comfy_asset(name, folder="loras", allowed_kinds={"lora"})
            weight = float(item.get("weight", 1.0))
            clip_weight = item.get("clip_weight")
            resolved.append(
                {
                    "id": asset.get("id"),
                    "name": asset.get("name") or Path(asset["filename"]).stem,
                    "filename": asset["filename"],
                    "path": asset["path"],
                    "weight": weight,
                    "clip_weight": float(clip_weight) if clip_weight is not None else weight,
                }
            )
        return resolved

    # ---- installers ----
    def _raise_if_task_cancelled(self, task: ModelTask, *, boundary: str) -> None:
        if self.tasks.is_cancel_requested(task):
            raise ModelTaskCancelled(
                f"Cancelled at the {str(boundary or 'next safe').strip()} boundary"
            )

    def _install_ollama(self, task: ModelTask, entry: dict[str, Any]) -> None:
        model = str(entry.get("ollama_model") or "")
        if not model:
            raise RuntimeError("Missing ollama_model")
        self.tasks.set_stage(task, "downloading", progress=0.05)
        ModelTaskManager.log(task, f"Pulling {model} via Ollama…")
        with requests.post(
            f"{self.ollama_url}/api/pull",
            json={"model": model, "stream": True},
            stream=True,
            timeout=60 * 60,
        ) as r:
            r.raise_for_status()
            last = ""
            for line in r.iter_lines(decode_unicode=True):
                self._raise_if_task_cancelled(task, boundary="Ollama stream")
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                status = obj.get("status") or ""
                total = obj.get("total")
                completed = obj.get("completed")
                if total and completed:
                    try:
                        p = float(completed) / float(total)
                        ModelTaskManager.set_progress(task, max(0.0, min(0.99, p)))
                    except Exception:
                        pass
                if status and status != last:
                    ModelTaskManager.log(task, status)
                    last = status
        ModelTaskManager.set_progress(task, 1.0)
        ModelTaskManager.log(task, "Done.")
        with self._ollama_probe_lock:
            self._ollama_models_cache.add(model)
            self._ollama_models_cache_at = time.monotonic()

    def _download_stream(
        self,
        task: ModelTask,
        url: str,
        dest: Path,
        headers: dict[str, str] | None = None,
    ) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        headers = dict(headers or {})
        self.tasks.set_stage(task, "downloading", progress=0.1)
        ModelTaskManager.log(task, f"Downloading…\n{url}")
        tmp = dest.with_suffix(dest.suffix + ".tmp")
        self._raise_if_task_cancelled(task, boundary="download start")
        resumed_bytes = 0
        try:
            if tmp.exists():
                resumed_bytes = max(0, int(tmp.stat().st_size))
        except OSError:
            resumed_bytes = 0
        if resumed_bytes:
            headers["Range"] = f"bytes={resumed_bytes}-"
            self._append_task_log(
                task,
                f"Resuming partial file at {resumed_bytes:,} bytes.",
            )
        with requests.get(url, stream=True, timeout=60 * 60, headers=headers) as r:
            if r.status_code in (401, 403):
                raise UserFacingError(
                    "Download unauthorized",
                    hint="Run `hf auth login` on the backend, or set an API token in Settings → Tokens (Hugging Face token for HF downloads, Civitai API key for Civitai downloads), then retry."
                )
            if r.status_code == 416 and resumed_bytes:
                content_range = str(r.headers.get("content-range") or "")
                match = re.search(r"/(\d+)$", content_range)
                expected = int(match.group(1)) if match else 0
                if expected and expected == resumed_bytes:
                    os.replace(tmp, dest)
                    self.tasks.set_transfer(
                        task,
                        bytes_completed=expected,
                        bytes_total=expected,
                        files_completed=1,
                        files_total=1,
                        force_persist=True,
                    )
                    ModelTaskManager.set_progress(task, 1.0)
                    ModelTaskManager.log(task, f"Saved: {dest.name}")
                    return
            r.raise_for_status()
            append = resumed_bytes > 0 and r.status_code == 206
            if not append:
                resumed_bytes = 0
            remaining = int(r.headers.get("content-length") or 0)
            total = resumed_bytes + remaining if remaining else 0
            got = resumed_bytes
            self.tasks.set_transfer(
                task,
                bytes_completed=got,
                bytes_total=total or None,
                files_completed=0,
                files_total=1,
                force_persist=True,
            )
            with open(tmp, "ab" if append else "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    self._raise_if_task_cancelled(task, boundary="download chunk")
                    if not chunk:
                        continue
                    f.write(chunk)
                    got += len(chunk)
                    self.tasks.set_transfer(
                        task,
                        bytes_completed=got,
                        bytes_total=total or None,
                        files_completed=0,
                        files_total=1,
                    )
                f.flush()
                os.fsync(f.fileno())
        os.replace(tmp, dest)
        self.tasks.set_transfer(
            task,
            bytes_completed=got,
            bytes_total=total or got,
            files_completed=1,
            files_total=1,
            force_persist=True,
        )
        ModelTaskManager.set_progress(task, 1.0)
        ModelTaskManager.log(task, f"Saved: {dest.name}")

    def _run_hf_download_with_auth_fallback(
        self,
        task: ModelTask,
        *,
        resource: str,
        candidates: list[HfTokenCandidate],
        download: Callable[[str | bool], None],
    ) -> None:
        rejected_sources: list[str] = []
        for candidate in candidates:
            self._append_task_log(task, f"Using Hugging Face auth from {candidate.source}")
            try:
                download(candidate.token)
                return
            except Exception as exc:
                if not _is_hf_auth_failure(exc):
                    raise
                rejected_sources.append(candidate.source)
                self._append_task_log(
                    task,
                    f"Hugging Face auth from {candidate.source} was rejected; trying another credential.",
                )

        try:
            # False prevents huggingface_hub from silently reusing a rejected cached token.
            download(False)
        except Exception as exc:
            if not _is_hf_auth_failure(exc):
                raise
            if rejected_sources:
                hint = (
                    "Run `hf auth logout` followed by `hf auth login`, or replace the Hugging Face "
                    "token in Settings → Tokens. If this model is gated, accept its license on "
                    "Hugging Face before retrying."
                )
            else:
                hint = (
                    "Set a valid Hugging Face token in Settings → Tokens, or run `hf auth login`. "
                    "If this model is gated, accept its license on Hugging Face before retrying."
                )
            raise UserFacingError(
                f"Hugging Face denied access to {resource}",
                hint=hint,
                code="HF_AUTH_REQUIRED",
            ) from exc

        if rejected_sources:
            self._append_task_log(
                task,
                "Downloaded without Hugging Face authentication after stored credentials were rejected.",
            )

    def _download_hf_snapshot(self, task: ModelTask, **kwargs: Any) -> None:
        if snapshot_download is None:
            raise RuntimeError("huggingface_hub is not installed (required for snapshot downloads)")
        self._raise_if_task_cancelled(task, boundary="snapshot phase")
        local_dir = Path(str(kwargs.get("local_dir") or "")).expanduser()
        stop_watcher = threading.Event()
        active_profile = HFSnapshotDownloadProfile(
            name="active",
            allow_patterns=tuple(str(item) for item in (kwargs.get("allow_patterns") or ("**",))),
            ignore_patterns=tuple(str(item) for item in (kwargs.get("ignore_patterns") or ())),
        )
        hub_download_cache = local_dir / ".cache" / "huggingface" / "download"

        def incomplete_cache_bytes() -> int:
            total = 0
            try:
                if hub_download_cache.is_dir():
                    for candidate in hub_download_cache.rglob("*.incomplete"):
                        try:
                            total += max(0, int(candidate.stat().st_size))
                        except OSError:
                            continue
            except OSError:
                return total
            return total

        def has_incomplete_cache_files() -> bool:
            try:
                if not hub_download_cache.is_dir():
                    return False
                return any(
                    _path_is_file_safe(candidate)
                    for candidate in hub_download_cache.rglob("*.incomplete")
                )
            except OSError:
                # An unreadable cache must never prevent restoration of the
                # process-wide transfer flag or crash catalog/install state.
                return False

        baseline_incomplete_bytes = incomplete_cache_bytes()

        def snapshot_stats() -> tuple[int, int]:
            completed_bytes = 0
            completed_files = 0
            try:
                if local_dir.is_dir():
                    for candidate in local_dir.rglob("*"):
                        try:
                            if not candidate.is_file():
                                continue
                            relative = candidate.relative_to(local_dir)
                            if tuple(part.lower() for part in relative.parts[:2]) == (
                                ".cache",
                                "huggingface",
                            ):
                                continue
                            relative_name = relative.as_posix()
                            if not _hf_profile_matches_path(relative_name, active_profile):
                                continue
                            name = candidate.name.lower()
                            if name.endswith((".incomplete", ".partial", ".part", ".tmp")):
                                continue
                            completed_bytes += max(0, int(candidate.stat().st_size))
                            completed_files += 1
                        except (OSError, ValueError):
                            continue
            except OSError:
                pass
            transferred_partial_bytes = max(
                0,
                incomplete_cache_bytes() - baseline_incomplete_bytes,
            )
            return completed_bytes + transferred_partial_bytes, completed_files

        def watch_snapshot() -> None:
            while not stop_watcher.wait(0.5):
                try:
                    completed_bytes, completed_files = snapshot_stats()
                    self.tasks.set_transfer(
                        task,
                        bytes_completed=completed_bytes,
                        files_completed=completed_files,
                    )
                except Exception:
                    continue

        with _HF_SNAPSHOT_DOWNLOAD_LOCK:
            self._raise_if_task_cancelled(task, boundary="snapshot queue")
            transfer_was_enabled = (
                hf_hub_constants is not None
                and bool(getattr(hf_hub_constants, "HF_HUB_ENABLE_HF_TRANSFER", False))
            )
            resume_override = transfer_was_enabled and has_incomplete_cache_files()
            watcher: threading.Thread | None = None
            if resume_override and hf_hub_constants is not None:
                # huggingface_hub 0.x deletes resumable local-dir partials when
                # hf_transfer is enabled. Temporarily use Hub's standard/Xet
                # path for this retry, then restore hf_transfer for fresh files.
                hf_hub_constants.HF_HUB_ENABLE_HF_TRANSFER = False
            try:
                if resume_override:
                    self._append_task_log(
                        task,
                        (
                            "Resume compatibility fallback: continuing existing Hugging Face "
                            "partials with the Hub/Xet downloader; Xet remains enabled and "
                            "hf_transfer will be restored for fresh snapshot operations."
                        ),
                    )
                elif transfer_was_enabled:
                    try:
                        requested_concurrency = int(
                            os.getenv("EDMG_HF_TRANSFER_CONCURRENCY", "4")
                        )
                    except ValueError:
                        requested_concurrency = 4
                    concurrency = max(1, min(16, requested_concurrency))
                    hf_hub_constants.HF_TRANSFER_CONCURRENCY = concurrency
                    self._append_task_log(
                        task,
                        (
                            f"hf_transfer is available with concurrency {concurrency}; "
                            "Xet-backed repositories remain eligible for hf_xet."
                        ),
                    )
                elif (
                    hf_hub_constants is not None
                    and not bool(getattr(hf_hub_constants, "HF_HUB_DISABLE_XET", False))
                ):
                    self._append_task_log(
                        task,
                        "Hugging Face Hub may use hf_xet for Xet-backed files.",
                    )
                watcher = threading.Thread(target=watch_snapshot, daemon=True)
                watcher.start()
                try:
                    snapshot_download(**kwargs)
                    self._raise_if_task_cancelled(task, boundary="snapshot phase")
                except ValueError as exc:
                    missing_transfer = (
                        "HF_HUB_ENABLE_HF_TRANSFER=1" in str(exc)
                        and "'hf_transfer' package is not available" in str(exc)
                    )
                    if not missing_transfer or hf_hub_constants is None:
                        raise

                    # huggingface_hub reads this flag once when its constants module is imported.
                    # Disable only its in-process cached value so an optional accelerator cannot
                    # prevent the standard resumable downloader from doing the requested install.
                    hf_hub_constants.HF_HUB_ENABLE_HF_TRANSFER = False
                    self._append_task_log(
                        task,
                        "hf_transfer is unavailable; continuing with the standard Hugging Face downloader.",
                    )
                    snapshot_download(**kwargs)
                    self._raise_if_task_cancelled(task, boundary="snapshot phase")
            finally:
                stop_watcher.set()
                if watcher is not None:
                    watcher.join(timeout=1.0)
                completed_bytes, completed_files = snapshot_stats()
                self.tasks.set_transfer(
                    task,
                    bytes_completed=completed_bytes,
                    files_completed=completed_files,
                    force_persist=True,
                )
                if resume_override and hf_hub_constants is not None:
                    hf_hub_constants.HF_HUB_ENABLE_HF_TRANSFER = transfer_was_enabled

    def _download_hf_inference_snapshot(
        self,
        task: ModelTask,
        *,
        entry: dict[str, Any],
        repo_id: str,
        local_dir: Path,
        revision: str | None,
        token: str | bool,
    ) -> None:
        common_kwargs = {
            "repo_id": repo_id,
            "local_dir": str(local_dir),
            "local_dir_use_symlinks": False,
            "revision": revision,
            "token": token,
            "resume_download": True,
        }

        metadata_profile = _hf_snapshot_download_profile(entry, weight_format="metadata")
        self.tasks.set_stage(task, "planning", progress=0.04)
        self._append_task_log(
            task,
            "Selecting runnable Diffusers metadata, configs, tokenizers, and schedulers.",
        )
        self._download_hf_snapshot(
            task,
            **common_kwargs,
            allow_patterns=list(metadata_profile.allow_patterns),
            ignore_patterns=list(metadata_profile.ignore_patterns),
        )

        kind = str(entry.get("kind") or "").strip().lower()
        components = (
            self._required_diffusers_components(local_dir)
            if kind in {"diffusers", "video_diffusers"}
            else []
        )
        if kind in {"diffusers", "video_diffusers"} and not components:
            components = list(_HF_COMMON_DIFFUSERS_COMPONENTS)

        safe_profile = _hf_snapshot_download_profile(
            entry,
            weight_format="safetensors",
            components=components,
        )
        self.tasks.set_stage(task, "downloading", progress=0.1)
        if components:
            selected = ", ".join(components)
            self._append_task_log(
                task,
                (
                    f"Selected inference plan: default-precision safetensors for "
                    f"{len(components)} component(s): {selected}."
                ),
            )
        else:
            self._append_task_log(
                task,
                "Selected inference plan: root component config plus default-precision safetensors.",
            )
        self._download_hf_snapshot(
            task,
            **common_kwargs,
            allow_patterns=list(safe_profile.allow_patterns),
            ignore_patterns=list(safe_profile.ignore_patterns),
        )

        self.tasks.set_stage(task, "validating", progress=0.82)
        if self._internal_asset_installed(entry, local_dir):
            self._append_task_log(
                task,
                (
                    f"Validated {task.files_completed} selected file(s), "
                    f"{task.bytes_completed:,} bytes on disk."
                ),
            )
            return
        if self._snapshot_has_incomplete_markers(local_dir):
            raise UserFacingError(
                f"{entry.get('name') or entry.get('id') or 'Model'} download is incomplete",
                hint="Retry the install. Studio kept the partial Hugging Face files so the download can resume.",
                code="MODEL_SNAPSHOT_INCOMPLETE",
            )

        missing_components = (
            [
                component
                for component in self._required_diffusers_components(local_dir)
                if not self._internal_component_has_weights(
                    local_dir / component,
                    weight_format="safetensors",
                )
            ]
            if kind in {"diffusers", "video_diffusers"}
            else []
        )
        repair_profile = _hf_snapshot_download_profile(
            entry,
            weight_format="safetensors",
            components=missing_components or components,
        )
        self.tasks.set_stage(task, "repairing_download", progress=0.35)
        self._append_task_log(
            task,
            (
                "Default safetensors are missing or structurally invalid"
                + (
                    f" for: {', '.join(missing_components)}"
                    if missing_components
                    else ""
                )
                + "; forcing one clean redownload before trying legacy weights."
            ),
        )
        self._download_hf_snapshot(
            task,
            **common_kwargs,
            allow_patterns=list(repair_profile.allow_patterns),
            ignore_patterns=list(repair_profile.ignore_patterns),
            force_download=True,
        )
        self.tasks.set_stage(task, "validating", progress=0.82)
        if self._internal_asset_installed(entry, local_dir):
            self._append_task_log(
                task,
                "Validated repaired default-safetensors inference layout.",
            )
            return

        legacy_profile = _hf_snapshot_download_profile(
            entry,
            weight_format="bin",
            # Diffusers selects weight format for the whole pipeline. Fetch a
            # coherent default-bin layout, not a safetensors/bin component mix.
            components=components,
        )
        self.tasks.set_stage(task, "downloading_fallback", progress=0.45)
        self._append_task_log(
            task,
            (
                "No runnable safetensors set was available"
                + (
                    f" for: {', '.join(missing_components)}"
                    if missing_components
                    else ""
                )
                + "; downloading a coherent legacy PyTorch .bin layout."
            ),
        )
        self._download_hf_snapshot(
            task,
            **common_kwargs,
            allow_patterns=list(legacy_profile.allow_patterns),
            ignore_patterns=list(legacy_profile.ignore_patterns),
        )
        self.tasks.set_stage(task, "validating", progress=0.82)
        if not self._internal_asset_installed(entry, local_dir):
            missing = (
                ", ".join(missing_components)
                if missing_components
                else "required weights or config"
            )
            raise UserFacingError(
                f"{entry.get('name') or entry.get('id') or 'Model'} snapshot is incomplete",
                hint=(
                    f"Missing {missing}. Retry to resume; Studio will not mark or mirror "
                    "this partial snapshot as installed."
                ),
                code="MODEL_SNAPSHOT_INCOMPLETE",
            )
        self._append_task_log(
            task,
            (
                f"Validated legacy fallback: {task.files_completed} selected file(s), "
                f"{task.bytes_completed:,} bytes on disk."
            ),
        )

    def _append_task_log(self, task: ModelTask, msg: str) -> None:
        current = str(task.last_log or "").strip()
        ModelTaskManager.log(task, f"{current}\n{msg}" if current else msg)
        self.tasks.persist(task)

    def _restore_from_model_cache(self, task: ModelTask, entry: dict[str, Any], dest: Path) -> bool:
        cache = getattr(self, "model_cache", None)
        if cache is None:
            return False
        self.tasks.set_stage(task, "restoring", progress=0.05)
        self._append_task_log(task, f"Checking {self._model_cache_label()} for a local restore.")
        cache_entry = self._cache_entry_from_cloud_record(
            entry,
            self._cloud_model_record(str(entry.get("id") or "").strip()),
        )
        try:
            if not cache.download_model(cache_entry, dest):
                return False
        except Exception as exc:
            self._append_task_log(task, f"{self._model_cache_label()} restore skipped: {exc}")
            return False
        ModelTaskManager.set_progress(task, 1.0)
        ModelTaskManager.log(task, f"Restored from {self._model_cache_label()}: {dest.name}")
        self.tasks.persist(task)
        return True

    def _restore_snapshot_from_model_cache(self, task: ModelTask, entry: dict[str, Any], dest: Path) -> bool:
        cache = getattr(self, "model_cache", None)
        download = getattr(cache, "download_model_directory", None)
        if cache is None or not callable(download):
            return False
        self.tasks.set_stage(task, "restoring", progress=0.05)
        self._append_task_log(task, f"Checking {self._model_cache_label()} for an internal snapshot restore.")
        cache_entry = self._cache_entry_from_cloud_record(
            entry,
            self._cloud_model_record(str(entry.get("id") or "").strip()),
        )
        try:
            if not download(cache_entry, dest):
                return False
        except Exception as exc:
            self._append_task_log(task, f"{self._model_cache_label()} restore skipped: {exc}")
            return False
        if not self._internal_asset_installed(entry, dest):
            raise UserFacingError(
                "Restored internal model archive is incomplete",
                hint="Rebuild and upload the internal model archive. The restored Diffusers snapshot is missing required files.",
                code="MODEL_CACHE_RESTORE_INVALID",
            )
        ModelTaskManager.set_progress(task, 1.0)
        ModelTaskManager.log(task, f"Restored internal snapshot from {self._model_cache_label()}: {dest.name}")
        self.tasks.persist(task)
        return True

    def _upload_to_model_cache(self, task: ModelTask, entry: dict[str, Any], path: Path, *, mode: str = "local_cache") -> str | None:
        cache = getattr(self, "model_cache", None)
        if cache is None:
            return None
        self.tasks.set_stage(task, "mirroring", progress=0.9)
        self._append_task_log(task, f"Mirroring model to {self._model_cache_label()}.")
        try:
            object_name = cache.upload_model(entry, path)
        except Exception as exc:
            self._append_task_log(task, f"{self._model_cache_label()} upload skipped: {exc}")
            return None
        self._record_cloud_model(entry, object_name, mode=mode)
        self._append_task_log(task, f"{self._model_cache_label()}: {object_name}")
        return str(object_name)

    def _upload_snapshot_to_model_cache(self, task: ModelTask, entry: dict[str, Any], path: Path, *, mode: str = "local_cache") -> str | None:
        cache = getattr(self, "model_cache", None)
        upload = getattr(cache, "upload_model_directory", None)
        if cache is None or not callable(upload):
            return None
        self.tasks.set_stage(task, "mirroring", progress=0.9)
        self._append_task_log(task, f"Mirroring validated snapshot to {self._model_cache_label()}.")
        try:
            object_name = upload(entry, path)
        except Exception as exc:
            self._append_task_log(task, f"{self._model_cache_label()} snapshot upload skipped: {exc}")
            return None
        self._record_cloud_model(entry, object_name, mode=mode)
        self._append_task_log(task, f"{self._model_cache_label()} snapshot: {object_name}")
        return str(object_name)

    def _restore_cloud_model(self, task: ModelTask, entry: dict[str, Any]) -> None:
        self.tasks.set_stage(task, "restoring", progress=0.03)
        mode, dest = self._models_dest(entry)
        if self.model_cache is None:
            raise UserFacingError(
                "No model cache is enabled",
                hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                code="MODEL_CACHE_REQUIRED",
            )
        if mode == "file":
            if not self._restore_from_model_cache(task, entry, dest):
                raise UserFacingError(
                    f"{entry.get('name') or entry.get('id') or 'Model'} is not present in the model cache",
                    hint="Install it in S3-only mode first, or install it locally from the original source.",
                    code="MODEL_CACHE_MISS",
                )
            return
        if mode == "snapshot":
            if not self._restore_snapshot_from_model_cache(task, entry, dest):
                raise UserFacingError(
                    f"{entry.get('name') or entry.get('id') or 'Internal model'} is not present in the model cache",
                    hint="Install it in S3-only mode first, or point the model entry at a valid S3 snapshot archive.",
                    code="MODEL_CACHE_MISS",
                )
            return
        raise UserFacingError(
            "This model type cannot be restored from the model cache",
            hint="Only single-file assets and internal Diffusers snapshot archives are supported.",
            code="CACHE_RESTORE_UNSUPPORTED_MODEL",
        )

    def _install_file_model(self, task: ModelTask, entry: dict[str, Any]) -> None:
        self.tasks.set_stage(task, "preparing", progress=0.01)
        src = (entry.get("source") or "").lower()
        target = entry.get("target") or {}
        fname = str(entry.get("filename") or "")
        if not fname:
            # for civitai user entries we may set filename later
            fname = "model.safetensors"

        mode, dest = self._models_dest(entry)
        storage_mode = self._model_storage_mode()
        cloud_only = storage_mode == "cloud_only"

        if mode == "file":
            if cloud_only:
                if self.model_cache is None:
                    raise UserFacingError(
                        "Cloud-only model storage requires an enabled model cache",
                        hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                        code="MODEL_CACHE_REQUIRED",
                    )
                object_name = self._cache_model_exists(entry, dest)
                if object_name:
                    self._record_cloud_model(entry, object_name, mode="cloud_only")
                    ModelTaskManager.set_progress(task, 1.0)
                    ModelTaskManager.log(task, f"Already stored in {self._model_cache_label()}: {object_name}")
                    return
            elif src != "s3" and self._restore_from_model_cache(task, entry, dest):
                return

        if mode == "snapshot":
            if cloud_only:
                if self.model_cache is None:
                    raise UserFacingError(
                        "Cloud-only internal model storage requires an enabled model cache",
                        hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                        code="MODEL_CACHE_REQUIRED",
                    )
                object_name = self._cache_snapshot_exists(
                    entry,
                    dest,
                    require_complete=True,
                )
                if object_name:
                    self._record_cloud_model(entry, object_name, mode="cloud_only")
                    ModelTaskManager.set_progress(task, 1.0)
                    ModelTaskManager.log(task, f"Already stored in {self._model_cache_label()}: {object_name}")
                    return
            elif src != "s3" and self._restore_snapshot_from_model_cache(task, entry, dest):
                return

        # Optional HF token support. Prefer explicit env vars, then the modern
        # `hf auth login` / Hub token cache, then Studio Settings.
        hf_candidates = hf_token_candidates(secrets_store=self.secrets)
        hf_token = hf_candidates[0].token if hf_candidates else ""

        civitai_key = ""
        if self.secrets is not None:
            civitai_key = self.secrets.get("civitai_api_key") or ""
        if not civitai_key:
            civitai_key = os.getenv("CIVITAI_API_KEY") or ""

        if src == "hf":
            repo_id = str(entry.get("hf_repo_id") or entry.get("hf_repo") or "")
            url = str(entry.get("hf_url") or "")
            if mode == "snapshot":
                if not repo_id:
                    raise RuntimeError("Missing hf_repo_id for snapshot install")
                if snapshot_download is None:
                    raise RuntimeError("huggingface_hub is not installed (required for snapshot downloads)")
                target_path = self._cloud_temp_path(dest) if cloud_only else dest
                target_path.mkdir(parents=True, exist_ok=True)
                ModelTaskManager.log(task, f"Downloading HF snapshot: {repo_id}")
                try:
                    self._run_hf_download_with_auth_fallback(
                        task,
                        resource=repo_id,
                        candidates=hf_candidates,
                        download=lambda token: self._download_hf_inference_snapshot(
                            task,
                            entry=entry,
                            repo_id=repo_id,
                            local_dir=target_path,
                            revision=str(entry.get("hf_revision") or "") or None,
                            token=token,
                        ),
                    )
                    if cloud_only:
                        object_name = self._upload_snapshot_to_model_cache(task, entry, target_path, mode="cloud_only")
                        if not object_name:
                            raise RuntimeError("Cloud-only internal snapshot upload failed")
                        ModelTaskManager.set_progress(task, 1.0)
                        self._append_task_log(task, f"Cloud-only internal install complete; no local snapshot kept: {object_name}")
                    else:
                        self._upload_snapshot_to_model_cache(task, entry, dest)
                        ModelTaskManager.set_progress(task, 1.0)
                    self.tasks.set_stage(task, "complete", progress=1.0)
                finally:
                    if cloud_only:
                        shutil.rmtree(target_path.parent, ignore_errors=True)
                return
            # file mode
            if not url:
                raise RuntimeError("Missing hf_url")
            target_path = self._cloud_temp_path(dest) if cloud_only else dest
            try:
                self._run_hf_download_with_auth_fallback(
                    task,
                    resource=url,
                    candidates=hf_candidates,
                    download=lambda token: self._download_stream(
                        task,
                        url,
                        target_path,
                        headers={"Authorization": f"Bearer {token}"} if token else {},
                    ),
                )
                if cloud_only:
                    object_name = self._upload_to_model_cache(task, entry, target_path, mode="cloud_only")
                    if not object_name:
                        raise RuntimeError("Cloud-only upload failed")
                    ModelTaskManager.set_progress(task, 1.0)
                    self._append_task_log(task, f"Cloud-only install complete; no local model file kept: {object_name}")
                else:
                    self._upload_to_model_cache(task, entry, dest)
            finally:
                if cloud_only:
                    try:
                        target_path.unlink(missing_ok=True)
                        target_path.parent.rmdir()
                    except Exception:
                        pass
            return

        if src == "hf_bucket":
            bucket_id = str(
                entry.get("hf_bucket_id")
                or entry.get("hf_bucket")
                or (target.get("hf_bucket_id") if isinstance(target, dict) else "")
                or ""
            ).strip()
            if not bucket_id:
                raise RuntimeError("Missing hf_bucket_id for Hugging Face bucket install")
            remote_path = str(
                entry.get("hf_bucket_path")
                or entry.get("bucket_path")
                or (target.get("hf_bucket_path") if isinstance(target, dict) else "")
                or ""
            ).strip()

            if mode == "snapshot":
                if _hf_bucket_download_snapshot is None:
                    raise RuntimeError(
                        "huggingface_hub bucket support is not installed (required for hf_bucket snapshot installs)"
                    )
                target_path = self._cloud_temp_path(dest) if cloud_only else dest
                target_path.mkdir(parents=True, exist_ok=True)
                self.tasks.set_stage(task, "downloading", progress=0.1)
                ModelTaskManager.log(task, f"Syncing HF bucket snapshot: {bucket_id}")
                try:
                    ok = _hf_bucket_download_snapshot(
                        bucket=bucket_id,
                        dest=target_path,
                        remote_path=remote_path,
                        token=(hf_token or None),
                    )
                    if not ok:
                        raise UserFacingError(
                            f"{entry.get('name') or entry.get('id') or 'Model'} was not found in the Hugging Face bucket",
                            hint="Check hf_bucket_id / hf_bucket_path and that your HF token can read the bucket.",
                            code="HF_BUCKET_MISS",
                        )
                    self.tasks.set_stage(task, "validating", progress=0.82)
                    if not self._internal_asset_installed(entry, target_path):
                        raise UserFacingError(
                            f"{entry.get('name') or entry.get('id') or 'Model'} bucket snapshot is incomplete",
                            hint=(
                                "Retry the sync. Studio kept the partial files and will not "
                                "mark or mirror the snapshot until all runnable weights are present."
                            ),
                            code="MODEL_SNAPSHOT_INCOMPLETE",
                        )
                    if cloud_only:
                        object_name = self._upload_snapshot_to_model_cache(task, entry, target_path, mode="cloud_only")
                        if not object_name:
                            raise RuntimeError("Cloud-only internal snapshot upload failed")
                        ModelTaskManager.set_progress(task, 1.0)
                        self._append_task_log(task, f"Cloud-only internal install complete; no local snapshot kept: {object_name}")
                    else:
                        self._upload_snapshot_to_model_cache(task, entry, dest)
                        ModelTaskManager.set_progress(task, 1.0)
                        ModelTaskManager.log(task, f"Synced from HF bucket: {bucket_id}")
                    self.tasks.set_stage(task, "complete", progress=1.0)
                finally:
                    if cloud_only:
                        shutil.rmtree(target_path.parent, ignore_errors=True)
                return

            # file mode
            if _hf_bucket_download_file is None:
                raise RuntimeError(
                    "huggingface_hub bucket support is not installed (required for hf_bucket file installs)"
                )
            file_remote = remote_path or fname
            target_path = self._cloud_temp_path(dest) if cloud_only else dest
            ModelTaskManager.log(task, f"Downloading HF bucket file: {bucket_id}/{file_remote}")
            try:
                ok = _hf_bucket_download_file(
                    bucket=bucket_id,
                    remote_path=file_remote,
                    dest=target_path,
                    token=(hf_token or None),
                )
                if not ok:
                    raise UserFacingError(
                        f"{entry.get('name') or entry.get('id') or 'Model'} was not found in the Hugging Face bucket",
                        hint="Check hf_bucket_id / hf_bucket_path and that your HF token can read the bucket.",
                        code="HF_BUCKET_MISS",
                    )
                if cloud_only:
                    object_name = self._upload_to_model_cache(task, entry, target_path, mode="cloud_only")
                    if not object_name:
                        raise RuntimeError("Cloud-only upload failed")
                    ModelTaskManager.set_progress(task, 1.0)
                    self._append_task_log(task, f"Cloud-only install complete; no local model file kept: {object_name}")
                else:
                    self._upload_to_model_cache(task, entry, dest)
                    ModelTaskManager.set_progress(task, 1.0)
                    ModelTaskManager.log(task, f"Saved from HF bucket: {dest.name}")
            finally:
                if cloud_only:
                    try:
                        target_path.unlink(missing_ok=True)
                        target_path.parent.rmdir()
                    except Exception:
                        pass
            return

        if src == "s3":
            if self.model_cache is None:
                raise UserFacingError(
                    "S3 model source requires an enabled model cache",
                    hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                    code="MODEL_CACHE_REQUIRED",
                )
            if mode == "file":
                object_name = self._cache_model_exists(entry, dest)
                if not object_name:
                    raise UserFacingError(
                        f"{entry.get('name') or entry.get('id') or 'Model'} was not found in S3",
                        hint="Check the model entry's s3_uri/s3_key, bucket, prefix, and Studio AWS credentials.",
                        code="MODEL_CACHE_MISS",
                    )
                self._record_cloud_model(entry, object_name, mode="cloud_only" if cloud_only else "remote_cache")
                if cloud_only:
                    ModelTaskManager.set_progress(task, 1.0)
                    ModelTaskManager.log(task, f"Stored in {self._model_cache_label()}: {object_name}")
                    return
                if not self._restore_from_model_cache(task, entry, dest):
                    raise UserFacingError(
                        "Could not download S3 model source",
                        hint="Check that Studio has read access to the configured S3 object.",
                        code="MODEL_CACHE_RESTORE_FAILED",
                    )
                return
            if mode == "snapshot":
                object_name = self._cache_snapshot_exists(entry, dest)
                if not object_name:
                    raise UserFacingError(
                        f"{entry.get('name') or entry.get('id') or 'Internal model'} was not found in S3",
                        hint="Check the model entry's s3_uri/s3_key points at a .zip/.tar/.tar.gz Diffusers snapshot archive.",
                        code="MODEL_CACHE_MISS",
                    )
                self._record_cloud_model(entry, object_name, mode="cloud_only" if cloud_only else "remote_cache")
                if cloud_only:
                    ModelTaskManager.set_progress(task, 1.0)
                    ModelTaskManager.log(task, f"Stored in {self._model_cache_label()}: {object_name}")
                    return
                if not self._restore_snapshot_from_model_cache(task, entry, dest):
                    raise UserFacingError(
                        "Could not download S3 internal model source",
                        hint="Check that Studio has read access to the configured S3 snapshot archive.",
                        code="MODEL_CACHE_RESTORE_FAILED",
                    )
                return
            raise UserFacingError(
                "S3 model source is not supported for this model type",
                hint="Use S3-hosted single-file assets or internal Diffusers snapshot archives.",
                code="S3_SOURCE_UNSUPPORTED_MODEL",
            )

        if src == "civitai":
            dl = str(entry.get("civitai_download_url") or "")
            if not dl:
                raise RuntimeError("Missing civitai_download_url")
            headers: dict[str, str] = {}
            if civitai_key:
                headers["Authorization"] = f"Bearer {civitai_key}"
            target_path = self._cloud_temp_path(dest) if cloud_only else dest
            try:
                self._download_stream(task, dl, target_path, headers=headers)
                if cloud_only:
                    object_name = self._upload_to_model_cache(task, entry, target_path, mode="cloud_only")
                    if not object_name:
                        raise RuntimeError("Cloud-only upload failed")
                    ModelTaskManager.set_progress(task, 1.0)
                    self._append_task_log(task, f"Cloud-only install complete; no local model file kept: {object_name}")
                else:
                    self._upload_to_model_cache(task, entry, dest)
            finally:
                if cloud_only:
                    try:
                        target_path.unlink(missing_ok=True)
                        target_path.parent.rmdir()
                    except Exception:
                        pass
            return

        if src == "local":
            # local models are assumed already placed. Copy if source_path provided.
            sp = str(entry.get("source_path") or "")
            if not sp:
                raise RuntimeError("Missing source_path")
            srcp = Path(sp).expanduser()
            if not srcp.exists():
                raise RuntimeError(f"File not found: {srcp}")
            if cloud_only:
                object_name = self._upload_to_model_cache(task, entry, srcp, mode="cloud_only")
                if not object_name:
                    raise RuntimeError("Cloud-only upload failed")
                ModelTaskManager.set_progress(task, 1.0)
                ModelTaskManager.log(task, f"Stored in {self._model_cache_label()} only: {object_name}")
                return
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(srcp.read_bytes())
            ModelTaskManager.log(task, f"Copied: {srcp.name}")
            ModelTaskManager.set_progress(task, 1.0)
            self._upload_to_model_cache(task, entry, dest)
            return

        raise RuntimeError(f"Unsupported source: {src}")

    def installed_path(self, model_id: str) -> Path | None:
        """Return local path for an installed model (file or directory), else None."""
        entry = self._find_entry(model_id)
        if not entry:
            return None
        return self._local_installed_path(entry)

    def _entry_is_available(self, entry: dict[str, Any], *, probe_remote: bool = True) -> bool:
        model_id = str(entry.get("id") or "").strip()
        if not model_id:
            return False
        if self._local_installed_path(entry) is not None:
            return True
        if not probe_remote or package_manifest(model_id) is not None:
            return False
        if self._cloud_model_record(model_id) is not None:
            return True
        cache = getattr(self, "model_cache", None)
        if cache is None:
            return False
        mode, dest = self._models_dest(entry)
        try:
            if mode == "snapshot":
                return bool(self._cache_snapshot_exists(entry, dest))
            if mode == "file":
                return bool(self._cache_model_exists(entry, dest))
        except Exception:
            return False
        return False

    def is_model_available(self, model_id: str, *, probe_remote: bool = True) -> bool:
        """Return True when a model is installed locally or present in the model cache."""
        entry = self._find_entry(model_id)
        if not entry:
            return False
        return self._entry_is_available(entry, probe_remote=probe_remote)

    def installed_internal_models(self) -> dict[str, bool]:
        """Bucket-aware availability for built-in internal diffusion models."""
        ids = ("hf_sd15_internal", "hf_sdxl_internal", "hf_sd35_medium_internal")
        return {model_id: self.is_model_available(model_id, probe_remote=True) for model_id in ids}

    def resolve_installed_path(self, model_id: str, *, materialize_remote: bool = True) -> Path | None:
        """Return a local runtime path, restoring a cached remote model when requested."""
        entry = self._find_entry(model_id)
        if not entry:
            return None

        local = self._local_installed_path(entry)
        if local is not None or not materialize_remote or package_manifest(model_id) is not None:
            return local

        mode, dest = self._models_dest(entry)
        if mode == "snapshot" and dest.exists() and not self._internal_asset_installed(entry, dest):
            # Keep Hub ``.incomplete`` payloads in place so a user retry can
            # resume them. Runtime resolution must not erase a large partial
            # download or mistake it for a loadable snapshot.
            return None
        if mode == "file":
            return self._materialize_file_from_model_cache(entry, dest)
        if mode == "snapshot":
            return self._materialize_snapshot_from_model_cache(entry, dest)
        return None


    def import_local(self, file_path: str, name: str | None = None, folder: str = "checkpoints") -> dict[str, Any]:
        """Register a local model file and copy it into the configured ComfyUI models folder.

        This is the BYO path for checkpoints/loras/etc.
        """
        srcp = Path(file_path).expanduser()
        if not srcp.exists() or not srcp.is_file():
            raise UserFacingError("File not found", hint="Pick a valid local model file.")
        folder = (folder or "checkpoints").strip().lower()
        safe_folder = folder if folder in ("checkpoints","loras","embeddings","vae","controlnet","upscale_models") else "checkpoints"
        cloud_only = self._model_storage_mode() == "cloud_only"
        if not cloud_only:
            dest_dir = self._comfy_models_dir(safe_folder)
            dest = dest_dir / srcp.name
            dest.write_bytes(srcp.read_bytes())

        entry = {
            "id": f"local_{uuid.uuid4().hex[:8]}",
            "name": name or srcp.stem,
            "kind": safe_folder.rstrip("s") if safe_folder.endswith("s") else safe_folder,
            "source": "local",
            "source_path": str(srcp if cloud_only else dest),
            "filename": srcp.name,
            "target": {"engine": "comfyui", "folder": safe_folder},
            "license_id": "user-provided",
            "license_url": "",
            "redistributable_in_installer": False,
            "recommended": "advanced",
            "notes": "User-provided local file. Ensure you have rights to use/distribute outputs as applicable.",
        }
        if cloud_only:
            if self.model_cache is None:
                raise UserFacingError(
                    "Cloud-only model storage requires an enabled model cache",
                    hint="Set EDMG_AWS_MODEL_CACHE=1 and EDMG_AWS_MODEL_CACHE_BUCKET, then restart Studio.",
                    code="MODEL_CACHE_REQUIRED",
                )
            object_name = self._upload_to_model_cache(ModelTask(id="import", name="Import local"), entry, srcp, mode="cloud_only")
            if not object_name:
                raise RuntimeError("Cloud-only upload failed")
        self.add_user_model(entry)
        return entry

    # ---- civitai helper ----
    def civitai_import(self, url_or_id: str) -> dict[str, Any]:
        """Import a model from Civitai by URL or numeric modelId.

        We add an entry to the user model registry but DO NOT download until user clicks Install.
        """
        model_id, version_id = _parse_civitai_url(url_or_id)
        if not model_id:
            raise UserFacingError("Couldn't parse Civitai model URL/ID", hint="Paste a Civitai model URL like https://civitai.com/models/12345 or a numeric ID.")
        api_key = ""
        if self.secrets is not None:
            api_key = self.secrets.get("civitai_api_key") or ""
        if not api_key:
            api_key = os.getenv("CIVITAI_API_KEY") or ""
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"

        # Fetch model metadata
        r = requests.get(f"https://civitai.com/api/v1/models/{model_id}", headers=headers, timeout=30)
        if r.status_code in (401, 403):
            raise UserFacingError(
                "Civitai API unauthorized",
                hint="Set CIVITAI_API_KEY in Settings → Tokens (some downloads require auth), then retry."
            )
        r.raise_for_status()
        m = r.json() or {}
        name = m.get("name") or f"Civitai Model {model_id}"
        mtype = (m.get("type") or "").lower()  # Checkpoint, LORA, TextualInversion, etc.

        # Pick a version (latest by createdAt)
        versions = m.get("modelVersions") or []
        if version_id:
            v = next((vv for vv in versions if str(vv.get("id")) == str(version_id)), None)
        else:
            v = None
            if versions and isinstance(versions, list):
                versions_sorted = sorted(
                    [vv for vv in versions if isinstance(vv, dict)],
                    key=lambda vv: vv.get("createdAt") or "",
                    reverse=True,
                )
                v = versions_sorted[0] if versions_sorted else None
        if not v:
            raise UserFacingError("No model version found", hint="Try a different model or specify a version.")

        # Determine download URL + filename from primary file
        files = v.get("files") or []
        primary = None
        for f in files:
            if isinstance(f, dict) and f.get("primary"):
                primary = f
                break
        if not primary and files:
            primary = files[0]
        if not primary:
            # Some versions include top-level downloadUrl
            dl = v.get("downloadUrl")
            if not dl:
                raise UserFacingError("No downloadable file found", hint="This model may require login/API key to download.")
            fname = f"civitai_{model_id}_{v.get('id')}.safetensors"
        else:
            dl = primary.get("downloadUrl") or v.get("downloadUrl")
            # Safety: avoid pickle tensors by default.
            meta = primary.get("metadata") or {}
            fmt = str(meta.get("format") or "").lower()
            if fmt and "safetensor" not in fmt:
                raise UserFacingError(
                    "Unsafe model format blocked",
                    hint="This Civitai file is not a SafeTensor. Choose a SafeTensor variant or export/download manually."
                )

            fname = primary.get("name") or f"civitai_{model_id}_{v.get('id')}.safetensors"

        # Map to comfy folder
        folder = "checkpoints"
        if "lora" in mtype:
            folder = "loras"
        elif "textualinversion" in mtype or "embedding" in mtype:
            folder = "embeddings"
        elif "vae" in mtype:
            folder = "vae"
        elif "controlnet" in mtype:
            folder = "controlnet"

        entry = {
            "id": f"civitai_{model_id}_{v.get('id')}",
            "name": f"{name} (Civitai)",
            "kind": "checkpoint" if folder == "checkpoints" else folder.rstrip("s"),
            "source": "civitai",
            "civitai_model_id": model_id,
            "civitai_version_id": v.get("id"),
            "civitai_page_url": f"https://civitai.com/models/{model_id}",
            "civitai_download_url": dl,
            "filename": fname,
            "target": {"engine": "comfyui", "folder": folder},
            # Civitai license varies per model; we surface the page and mark as unknown unless the API returns license data.
            "license_id": str(m.get("license") or m.get("licenseId") or "unknown"),
            "license_url": f"https://civitai.com/models/{model_id}",
            "redistributable_in_installer": False,
            "recommended": "advanced",
            "notes": "Community model from Civitai. Review license/terms on the model page before using commercially.",
        }
        self.add_user_model(entry)
        return entry


def _parse_civitai_url(s: str) -> tuple[str | None, str | None]:
    s = (s or "").strip()
    if not s:
        return None, None
    if s.isdigit():
        return s, None

    # URLs like:
    #  - https://civitai.com/models/12345
    #  - https://civitai.com/models/12345/name?modelVersionId=67890
    m = re.search(r"civitai\.com/(?:en/)?models/(\d+)", s)
    model_id = m.group(1) if m else None
    mv = re.search(r"modelVersionId=(\d+)", s)
    version_id = mv.group(1) if mv else None
    return model_id, version_id
