from __future__ import annotations

import importlib.util
import json
import logging
import random
from pathlib import Path
from typing import Any

from ..errors import UserFacingError
from .model_weights import diffusers_weight_load_kwargs

logger = logging.getLogger(__name__)

_VIDEO_PIPELINE_CACHE: dict[tuple[str, str, str, str], Any] = {}

SVD_CONDITIONING_FPS = 7
SVD_MIN_GUIDANCE_SCALE = 1.0
SVD_MAX_GUIDANCE_SCALE = 3.0
ANIMATEDIFF_MAX_GUIDANCE_SCALE = 7.5
HUNYUAN_DEFAULT_FPS = 24


def validate_video_model_layout(engine: str, model_dir: Path) -> None:
    """Reject mismatched or incomplete internal video assets before loading Diffusers."""

    engine_l = str(engine or "").strip().lower()
    model_dir = Path(model_dir)
    if engine_l not in {"svd", "animatediff", "hunyuan_video15"}:
        raise UserFacingError(
            f"Unknown internal video model engine: {engine}",
            hint="Choose auto, SVD image-to-video, AnimateDiff SD1.5, or HunyuanVideo-1.5.",
            code="INTERNAL_VIDEO_MODEL_ENGINE_UNKNOWN",
            status_code=400,
        )

    if not model_dir.is_dir():
        raise UserFacingError(
            "Internal video model is not installed",
            hint="Open Models and install the selected internal video model, then retry.",
            code="INTERNAL_VIDEO_MODEL_NOT_INSTALLED",
            status_code=400,
        )

    if engine_l == "svd":
        config_names = ("model_index.json",)
    elif engine_l == "animatediff":
        config_names = ("config.json",)
    else:
        # Diffusers-compatible Hunyuan snapshots use model_index.json.  The
        # upstream Tencent snapshot uses config.json, so accept both layouts
        # and let the loader report missing runtime components precisely.
        config_names = ("model_index.json", "config.json")
    config_name = next((name for name in config_names if (model_dir / name).is_file()), config_names[0])
    config_path = model_dir / config_name
    if not config_path.is_file():
        model_label = {
            "svd": "SVD pipeline",
            "animatediff": "AnimateDiff motion adapter",
            "hunyuan_video15": "HunyuanVideo-1.5 pipeline",
        }[engine_l]
        raise UserFacingError(
            f"Selected internal video model is not a complete {model_label}",
            hint=(
                f"Open Models and reinstall the {model_label}. The selected folder is missing "
                f"{', '.join(config_names)}, so Studio will not start the render."
            ),
            code="INTERNAL_VIDEO_MODEL_LAYOUT_INVALID",
            status_code=400,
        )

    try:
        config = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise UserFacingError(
            "Selected internal video model has an unreadable configuration",
            hint=f"Open Models and reinstall the selected video model; {config_name} is invalid.",
            code="INTERNAL_VIDEO_MODEL_LAYOUT_INVALID",
            status_code=400,
        ) from exc

    class_name = str(config.get("_class_name") or "") if isinstance(config, dict) else ""
    expected_class = {
        "svd": "StableVideoDiffusionPipeline",
        "animatediff": "MotionAdapter",
        "hunyuan_video15": "HunyuanVideo15Pipeline",
    }[engine_l]
    class_matches = (
        expected_class.lower() in class_name.lower()
        if engine_l != "hunyuan_video15"
        else any(
            token in class_name.lower()
            for token in ("hunyuanvideo15", "hunyuan_video_1_5", "hunyuanvideo_1_5")
        )
    )
    if not class_matches:
        raise UserFacingError(
            "Selected internal video model does not match the adapter engine",
            hint=(
                f"The {engine_l} engine expected {expected_class}, but the selected model declares "
                f"{class_name}. Choose the matching video model and retry."
            ),
            code="INTERNAL_VIDEO_MODEL_ENGINE_MODEL_MISMATCH",
            status_code=400,
        )

    if engine_l == "animatediff":
        weight_files = [
            path
            for path in model_dir.glob("diffusion_pytorch_model*")
            if path.is_file() and path.name != config_name
        ]
        if not weight_files:
            raise UserFacingError(
                "Selected AnimateDiff motion adapter is incomplete",
                hint=(
                    "Open Models and reinstall AnimateDiff Motion Adapter. The selected folder "
                    "does not contain its diffusion_pytorch_model weights."
                ),
                code="INTERNAL_VIDEO_MODEL_LAYOUT_INVALID",
                status_code=400,
            )


def dependency_status() -> dict[str, Any]:
    return {
        "diffusers_available": importlib.util.find_spec("diffusers") is not None,
        "torch_available": importlib.util.find_spec("torch") is not None,
        "pil_available": importlib.util.find_spec("PIL") is not None,
    }


def _parse_torch_dtype(dtype: str, device: str):
    import torch  # type: ignore

    raw = str(dtype or "").strip().lower()
    if raw in {"float32", "fp32"}:
        return torch.float32
    if raw in {"bfloat16", "bf16"}:
        return torch.bfloat16
    if device in {"cuda", "mps"}:
        return torch.float16
    return torch.float32


def _seeded_generator(seed: int | None, device: str):
    import torch  # type: ignore

    used_seed = int(seed) if seed is not None else random.randint(0, 2**31 - 1)
    generator_device = device if device == "cuda" else "cpu"
    generator = torch.Generator(device=generator_device)
    generator.manual_seed(used_seed)
    return generator, used_seed


def clear_video_pipeline_cache() -> None:
    _VIDEO_PIPELINE_CACHE.clear()


def _cleanup_cuda(device: str) -> None:
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


def _is_cuda_out_of_memory(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "cuda out of memory" in message
        or "torch.cuda.outofmemoryerror" in message
        or ("out of memory" in message and "cuda" in message)
    )


def _raise_cuda_oom(engine: str, exc: Exception) -> None:
    clear_video_pipeline_cache()
    raise UserFacingError(
        f"Internal {engine} video model ran out of CUDA memory",
        hint=(
            "Studio will fit 6 GB CUDA best with CPU offload, 8-12 frames per scene, "
            "20-25 temporal steps, and a conservative adapter canvas near 640x360. "
            "Reduce canvas size or frames per scene before reducing denoising quality."
        ),
        code="INTERNAL_VIDEO_MODEL_CUDA_OOM",
        status_code=400,
    ) from exc


def _normalize_frames(raw_frames: Any) -> list[Any]:
    frames = raw_frames
    if hasattr(frames, "frames"):
        frames = frames.frames
    if frames is None:
        return []
    if isinstance(frames, (list, tuple)) and frames and isinstance(frames[0], (list, tuple)):
        frames = frames[0]
    # Diffusers normally returns PIL output as [batch][time]. Explicit PIL
    # output below keeps that contract, but accepting an array/tensor batch here
    # makes the adapter fail predictably if a future pipeline changes defaults.
    ndim = getattr(frames, "ndim", None)
    if isinstance(ndim, int) and ndim == 5:
        frames = frames[0]
    try:
        return list(frames)
    except TypeError as exc:
        raise RuntimeError("Internal video model returned a non-iterable frame payload.") from exc


def _to_rgb_frames(frames: list[Any], *, width: int, height: int) -> list[Any]:
    from PIL import Image  # type: ignore

    out: list[Image.Image] = []
    for frame in frames:
        if frame is None:
            continue
        img = frame.convert("RGB") if hasattr(frame, "convert") else Image.fromarray(frame).convert("RGB")
        if img.size != (int(width), int(height)):
            img = img.resize((int(width), int(height)), resample=Image.LANCZOS)
        out.append(img)
    return out


def _optimize_pipeline(pipe: Any, device: str, *, cpu_offload: bool) -> Any:
    # Diffusers on PyTorch 2.x uses AttnProcessor2_0/SDPA by default. Calling
    # enable_attention_slicing() replaces that processor with a sliced
    # implementation and can make video denoising dramatically slower. Keep
    # native SDPA unless a separately installed xFormers backend is selected.
    if hasattr(pipe, "enable_vae_slicing"):
        try:
            pipe.enable_vae_slicing()
        except Exception as exc:
            logger.debug("Unable to enable VAE slicing for internal video pipeline: %s", exc)
    if hasattr(pipe, "enable_vae_tiling"):
        try:
            pipe.enable_vae_tiling()
        except Exception as exc:
            logger.debug("Unable to enable VAE tiling for internal video pipeline: %s", exc)
    # HunyuanVideo-1.5 exposes tiling on the VAE rather than the pipeline in
    # current Diffusers releases. Keep this capability optional so older SVD
    # and AnimateDiff fakes/runtimes remain compatible.
    vae = getattr(pipe, "vae", None)
    if vae is not None and hasattr(vae, "enable_tiling"):
        try:
            vae.enable_tiling()
        except Exception as exc:
            logger.debug("Unable to enable VAE tiling on internal video VAE: %s", exc)
    if (
        device == "cuda"
        and importlib.util.find_spec("xformers") is not None
        and hasattr(pipe, "enable_xformers_memory_efficient_attention")
    ):
        try:
            pipe.enable_xformers_memory_efficient_attention()
            logger.info("Internal video attention backend: xFormers")
        except Exception as exc:
            logger.warning("xFormers activation failed; retaining PyTorch SDPA: %s", exc)
    elif device == "cuda":
        logger.info("Internal video attention backend: PyTorch SDPA")
    if cpu_offload and hasattr(pipe, "enable_model_cpu_offload"):
        try:
            pipe.enable_model_cpu_offload()
            logger.info("Internal video memory strategy: model CPU offload")
            return pipe
        except Exception as exc:
            logger.warning("Model CPU offload failed; trying sequential CPU offload: %s", exc)
    if cpu_offload and hasattr(pipe, "enable_sequential_cpu_offload"):
        try:
            pipe.enable_sequential_cpu_offload()
            logger.warning(
                "Internal video memory strategy: sequential CPU offload; this fallback can be extremely slow"
            )
            return pipe
        except Exception as exc:
            logger.warning("Sequential CPU offload failed; moving the full pipeline to %s: %s", device, exc)
    if hasattr(pipe, "to"):
        pipe = pipe.to(device)
        logger.info("Internal video memory strategy: full pipeline on %s", device)
    return pipe


def _video_model_base_load_kwargs(model_dir: Path, device: str) -> dict[str, object]:
    return diffusers_weight_load_kwargs(model_dir, device)


def _reraise_video_model_load_error(exc: Exception, model_dir: Path) -> None:
    message = str(exc).lower()
    if "git-lfs" in message or "git lfs" in message:
        raise UserFacingError(
            "Internal video model snapshot contains Git LFS pointer files",
            hint=(
                f"The Diffusers snapshot at {model_dir} has placeholder weight files instead of full model weights. "
                "Reinstall the internal base model in Models or run git lfs pull/re-sync for that snapshot, then retry."
            ),
            code="INTERNAL_VIDEO_MODEL_LFS_POINTER",
            status_code=400,
        ) from exc
    raise exc


def _load_svd_pipeline(model_dir: Path, *, device: str, dtype: str, cpu_offload: bool):
    try:
        from diffusers import StableVideoDiffusionPipeline  # type: ignore
    except Exception as exc:
        raise UserFacingError(
            "Internal SVD video support is not installed",
            hint="Install the Studio backend internal dependencies, then install the internal SVD video model from Models.",
            code="INTERNAL_VIDEO_MODEL_DEPS",
            status_code=500,
        ) from exc

    key = ("svd", str(model_dir), device, f"{dtype}|offload={int(bool(cpu_offload))}")
    cached = _VIDEO_PIPELINE_CACHE.get(key)
    if cached is not None:
        return cached

    load_kwargs: dict[str, Any] = {"torch_dtype": _parse_torch_dtype(dtype, device)}
    load_kwargs.update(_video_model_base_load_kwargs(model_dir, device))
    try:
        pipe = StableVideoDiffusionPipeline.from_pretrained(str(model_dir), **load_kwargs)
    except Exception as exc:
        _reraise_video_model_load_error(exc, model_dir)
    pipe = _optimize_pipeline(pipe, device, cpu_offload=cpu_offload)
    _VIDEO_PIPELINE_CACHE[key] = pipe
    return pipe


def _load_animatediff_pipeline(
    *,
    adapter_dir: Path,
    base_model_dir: Path,
    device: str,
    dtype: str,
    cpu_offload: bool,
):
    try:
        from diffusers import AnimateDiffPipeline, DDIMScheduler, MotionAdapter  # type: ignore
    except Exception as exc:
        raise UserFacingError(
            "Internal AnimateDiff support is not installed",
            hint="Upgrade/install diffusers with AnimateDiff support, then install the internal AnimateDiff motion adapter from Models.",
            code="INTERNAL_VIDEO_MODEL_DEPS",
            status_code=500,
        ) from exc

    key = ("animatediff", f"{base_model_dir}|{adapter_dir}", device, f"{dtype}|offload={int(bool(cpu_offload))}")
    cached = _VIDEO_PIPELINE_CACHE.get(key)
    if cached is not None:
        return cached

    torch_dtype = _parse_torch_dtype(dtype, device)
    try:
        adapter = MotionAdapter.from_pretrained(str(adapter_dir), torch_dtype=torch_dtype)
    except Exception as exc:
        _reraise_video_model_load_error(exc, adapter_dir)
    load_kwargs: dict[str, Any] = {
        "motion_adapter": adapter,
        "torch_dtype": torch_dtype,
        "safety_checker": None,
        "requires_safety_checker": False,
    }
    load_kwargs.update(_video_model_base_load_kwargs(base_model_dir, device))
    try:
        pipe = AnimateDiffPipeline.from_pretrained(str(base_model_dir), **load_kwargs)
    except Exception as exc:
        _reraise_video_model_load_error(exc, base_model_dir)
    scheduler = getattr(pipe, "scheduler", None)
    scheduler_config = getattr(scheduler, "config", None)
    if scheduler_config is not None:
        pipe.scheduler = DDIMScheduler.from_config(
            scheduler_config,
            beta_schedule="linear",
            timestep_spacing="linspace",
            steps_offset=1,
            clip_sample=False,
        )
    pipe = _optimize_pipeline(pipe, device, cpu_offload=cpu_offload)
    _VIDEO_PIPELINE_CACHE[key] = pipe
    return pipe


def _load_hunyuan_pipeline(
    model_dir: Path,
    *,
    device: str,
    dtype: str,
    cpu_offload: bool,
    image_to_video: bool,
):
    """Load a Diffusers-compatible HunyuanVideo-1.5 pipeline lazily.

    Hunyuan has separate T2V and I2V pipeline classes. Keeping them in the
    cache under distinct keys prevents a text-to-video request from reusing an
    image-to-video pipeline with an incompatible call contract.
    """

    try:
        from diffusers import (  # type: ignore
            HunyuanVideo15ImageToVideoPipeline,
            HunyuanVideo15Pipeline,
        )
    except Exception as exc:
        raise UserFacingError(
            "Internal HunyuanVideo-1.5 support is not installed",
            hint=(
                "Install the reviewed internal-video runtime with HunyuanVideo-1.5 support, "
                "then qualify the local model snapshot before enabling this renderer."
            ),
            code="INTERNAL_VIDEO_MODEL_DEPS",
            status_code=500,
        ) from exc

    pipeline_kind = "hunyuan_video15_i2v" if image_to_video else "hunyuan_video15_t2v"
    key = (pipeline_kind, str(model_dir), device, f"{dtype}|offload={int(bool(cpu_offload))}")
    cached = _VIDEO_PIPELINE_CACHE.get(key)
    if cached is not None:
        return cached

    pipeline_cls = HunyuanVideo15ImageToVideoPipeline if image_to_video else HunyuanVideo15Pipeline
    load_kwargs: dict[str, Any] = {"torch_dtype": _parse_torch_dtype(dtype, device)}
    load_kwargs.update(_video_model_base_load_kwargs(model_dir, device))
    try:
        pipe = pipeline_cls.from_pretrained(str(model_dir), **load_kwargs)
    except Exception as exc:
        _reraise_video_model_load_error(exc, model_dir)
    pipe = _optimize_pipeline(pipe, device, cpu_offload=cpu_offload)
    _VIDEO_PIPELINE_CACHE[key] = pipe
    return pipe


def _configure_hunyuan_guidance(pipe: Any, cfg: float) -> None:
    """Apply CFG through the Hunyuan guider without passing unsupported kwargs."""

    guider = getattr(pipe, "guider", None)
    guider_new = getattr(guider, "new", None)
    if callable(guider_new):
        try:
            pipe.guider = guider_new(guidance_scale=float(cfg))
            return
        except Exception as exc:
            logger.debug("Hunyuan guider rejected guidance scale: %s", exc)
    # Older/fake pipelines may expose a mutable guidance_scale instead of a
    # guider factory. This fallback is deliberately best-effort.
    if hasattr(pipe, "guidance_scale"):
        try:
            pipe.guidance_scale = float(cfg)
        except Exception:
            pass


def generate_video_model_frames(
    *,
    engine: str,
    video_model_dir: Path,
    base_model_dir: Path,
    init_image: Any | None,
    prompt: str,
    negative_prompt: str,
    width: int,
    height: int,
    num_frames: int,
    fps: int,
    steps: int,
    cfg: float,
    seed: int | None,
    device: str,
    dtype: str = "auto",
    motion_bucket_id: int = 127,
    noise_aug_strength: float = 0.02,
    decode_chunk_size: int = 8,
    cpu_offload: bool = False,
) -> list[Any]:
    """Generate PIL frames with an internal Diffusers video model.

    SVD is image-to-video and uses ``init_image``. AnimateDiff is text-to-video
    through a motion adapter and uses the internal SD1.5 base model. Hunyuan
    selects its Diffusers T2V or I2V pipeline from the presence of ``init_image``.
    """
    if num_frames <= 0:
        return []
    if not video_model_dir.exists():
        raise UserFacingError(
            "Internal video model is not installed",
            hint="Open Models and install a qualified internal video model, then retry.",
            code="INTERNAL_VIDEO_MODEL_NOT_INSTALLED",
            status_code=400,
        )

    engine_l = str(engine or "svd").strip().lower()
    validate_video_model_layout(engine_l, video_model_dir)
    dtype_l = "float16" if str(dtype or "auto").strip().lower() == "auto" and device == "cuda" else str(dtype or "float32")
    generator, used_seed = _seeded_generator(seed, device)

    if engine_l == "hunyuan_video15":
        pipe = _load_hunyuan_pipeline(
            video_model_dir,
            device=device,
            dtype=dtype_l,
            cpu_offload=cpu_offload,
            image_to_video=init_image is not None,
        )
        _configure_hunyuan_guidance(pipe, cfg)
        kwargs: dict[str, Any] = {
            "prompt": str(prompt or "cinematic subject motion"),
            "negative_prompt": str(negative_prompt or ""),
            "height": int(height),
            "width": int(width),
            "num_frames": int(num_frames),
            "num_inference_steps": int(steps),
            "generator": generator,
            "output_type": "pil",
        }
        if init_image is not None:
            kwargs["image"] = init_image.convert("RGB").resize((int(width), int(height)))
        try:
            # HunyuanVideo-1.5 configures classifier-free guidance through its
            # guider; passing guidance_scale here is rejected by current
            # Diffusers releases and would make a valid runtime fail early.
            result = pipe(**kwargs)
        except Exception as exc:
            _cleanup_cuda(device)
            if _is_cuda_out_of_memory(exc):
                _raise_cuda_oom("HunyuanVideo-1.5", exc)
            raise
        frames = _normalize_frames(result)
        if not frames:
            raise RuntimeError(f"HunyuanVideo-1.5 returned no frames (seed={used_seed}).")
        rgb_frames = _to_rgb_frames(frames, width=width, height=height)
        if len(rgb_frames) != int(num_frames):
            raise RuntimeError(
                f"HunyuanVideo-1.5 returned {len(rgb_frames)} frames; expected {int(num_frames)} (seed={used_seed})."
            )
        return rgb_frames

    if engine_l == "svd":
        if init_image is None:
            raise UserFacingError(
                "SVD needs an input keyframe",
                hint="Run internal video with generated keyframes enabled, or provide a source image.",
                code="INTERNAL_VIDEO_MODEL_INPUT_MISSING",
                status_code=400,
            )
        pipe = _load_svd_pipeline(video_model_dir, device=device, dtype=dtype_l, cpu_offload=cpu_offload)
        image = init_image.convert("RGB").resize((int(width), int(height)))
        kwargs = {
            "image": image,
            "num_frames": int(num_frames),
            "num_inference_steps": int(steps),
            "generator": generator,
            "motion_bucket_id": int(motion_bucket_id),
            "noise_aug_strength": float(noise_aug_strength),
            "decode_chunk_size": int(decode_chunk_size),
            "output_type": "pil",
        }
        try:
            # SVD's fps value is model micro-conditioning, not the render or
            # export rate. Its guidance schedule is likewise distinct from the
            # still-image CFG control used for storyboard anchors.
            kwargs["fps"] = SVD_CONDITIONING_FPS
            kwargs["min_guidance_scale"] = SVD_MIN_GUIDANCE_SCALE
            kwargs["max_guidance_scale"] = SVD_MAX_GUIDANCE_SCALE
            result = pipe(**kwargs)
        except TypeError:
            kwargs.pop("fps", None)
            kwargs.pop("min_guidance_scale", None)
            kwargs.pop("max_guidance_scale", None)
            kwargs["guidance_scale"] = SVD_MAX_GUIDANCE_SCALE
            try:
                result = pipe(**kwargs)
            except Exception as exc:
                _cleanup_cuda(device)
                if _is_cuda_out_of_memory(exc):
                    _raise_cuda_oom("SVD", exc)
                raise
        except Exception as exc:
            _cleanup_cuda(device)
            if _is_cuda_out_of_memory(exc):
                _raise_cuda_oom("SVD", exc)
            raise
        frames = _normalize_frames(result)
        if not frames:
            raise RuntimeError(f"SVD returned no frames (seed={used_seed}).")
        rgb_frames = _to_rgb_frames(frames, width=width, height=height)
        if len(rgb_frames) != int(num_frames):
            raise RuntimeError(
                f"SVD returned {len(rgb_frames)} frames; expected {int(num_frames)} (seed={used_seed})."
            )
        return rgb_frames

    if engine_l == "animatediff":
        pipe = _load_animatediff_pipeline(
            adapter_dir=video_model_dir,
            base_model_dir=base_model_dir,
            device=device,
            dtype=dtype_l,
            cpu_offload=cpu_offload,
        )
        try:
            result = pipe(
                prompt=str(prompt or "cinematic subject motion"),
                negative_prompt=str(negative_prompt or ""),
                num_frames=int(num_frames),
                num_inference_steps=int(steps),
                # Storyboard CFG schedules can legitimately run hotter for
                # still anchors, but the v1.5-2 motion adapter degrades into
                # oversaturated structure at those values. Preserve lower
                # authored values while enforcing the adapter's quality ceiling.
                guidance_scale=max(1.0, min(float(cfg), ANIMATEDIFF_MAX_GUIDANCE_SCALE)),
                generator=generator,
                width=int(width),
                height=int(height),
                output_type="pil",
            )
        except Exception as exc:
            _cleanup_cuda(device)
            if _is_cuda_out_of_memory(exc):
                _raise_cuda_oom("AnimateDiff", exc)
            raise
        frames = _normalize_frames(result)
        if not frames:
            raise RuntimeError(f"AnimateDiff returned no frames (seed={used_seed}).")
        rgb_frames = _to_rgb_frames(frames, width=width, height=height)
        if len(rgb_frames) != int(num_frames):
            raise RuntimeError(
                f"AnimateDiff returned {len(rgb_frames)} frames; expected {int(num_frames)} (seed={used_seed})."
            )
        return rgb_frames

    raise UserFacingError(
        f"Unknown internal video model engine: {engine}",
        hint="Choose auto, svd, animatediff, or hunyuan_video15.",
        code="INTERNAL_VIDEO_MODEL_ENGINE_UNKNOWN",
        status_code=400,
    )
