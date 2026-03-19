from __future__ import annotations

import requests
from typing import Any

# ---------------------------
# Core ComfyUI REST helpers
# ---------------------------

def submit_prompt(comfyui_url: str, workflow: dict[str, Any], client_id: str = "edmg-studio") -> dict[str, Any]:
    r = requests.post(f"{comfyui_url}/prompt", json={"prompt": workflow, "client_id": client_id}, timeout=60)
    r.raise_for_status()
    return r.json()

def get_history(comfyui_url: str, prompt_id: str) -> dict[str, Any]:
    r = requests.get(f"{comfyui_url}/history/{prompt_id}", timeout=60)
    r.raise_for_status()
    return r.json()

def get_object_info(comfyui_url: str) -> dict[str, Any]:
    """Returns ComfyUI node catalog (keys are node class names).

    This is the most reliable way to detect which custom nodes are installed.
    """
    r = requests.get(f"{comfyui_url}/object_info", timeout=60)
    r.raise_for_status()
    return r.json()

def download_image_bytes(comfyui_url: str, filename: str, subfolder: str = "", folder_type: str = "output") -> bytes:
    params = {"filename": filename, "subfolder": subfolder, "type": folder_type}
    r = requests.get(f"{comfyui_url}/view", params=params, timeout=60)
    r.raise_for_status()
    return r.content

def extract_output_images(history_payload: dict[str, Any]) -> list[dict[str, Any]]:
    images: list[dict[str, Any]] = []
    for _pid, data in (history_payload or {}).items():
        outputs = data.get("outputs", {}) or {}
        for _node, out in outputs.items():
            for im in (out.get("images") or []):
                images.append(im)
    return images

def extract_execution_error(history_payload: dict[str, Any]) -> str | None:
    for _pid, data in (history_payload or {}).items():
        status = data.get("status") or {}
        for item in (status.get("messages") or []):
            if not isinstance(item, (list, tuple)) or len(item) < 2:
                continue
            kind, payload = item[0], item[1]
            if kind != "execution_error" or not isinstance(payload, dict):
                continue
            msg = str(payload.get("exception_message") or "").strip()
            node_type = str(payload.get("node_type") or "").strip()
            if msg and node_type:
                return f"{node_type}: {msg}"
            if msg:
                return msg
    return None

def has_nodes(object_info: dict[str, Any], required: list[str]) -> tuple[bool, list[str]]:
    missing = [n for n in required if n not in (object_info or {})]
    return (len(missing) == 0), missing

# ---------------------------
# Workflow builders
# ---------------------------

def default_workflow(
    checkpoint: str,
    prompt: str,
    negative_prompt: str,
    seed: int,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler: str,
    filename_prefix: str = "edmg_studio"
) -> dict[str, Any]:
    """Basic SD txt2img workflow (single image)."""
    return {
        "3": {"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
        "5": {"class_type":"EmptyLatentImage","inputs":{"width":width,"height":height,"batch_size":1}},
        "6": {"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["3",1]}},
        "7": {"class_type":"CLIPTextEncode","inputs":{"text":negative_prompt,"clip":["3",1]}},
        "8": {"class_type":"KSampler","inputs":{
            "seed":seed,
            "steps":steps,
            "cfg":cfg,
            "sampler_name":sampler,
            "scheduler":"normal",
            "denoise":1,
            "model":["3",0],
            "positive":["6",0],
            "negative":["7",0],
            "latent_image":["5",0]
        }},
        "9": {"class_type":"VAEDecode","inputs":{"samples":["8",0],"vae":["3",2]}},
        "10": {"class_type":"SaveImage","inputs":{"filename_prefix":filename_prefix,"images":["9",0]}}
    }

def animatediff_workflow(
    checkpoint: str,
    prompt: str,
    negative_prompt: str,
    seed: int,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler: str,
    frames: int,
    motion_model_name: str,
    context_length: int = 16,
    context_overlap: int = 4,
    beta_schedule: str = "autoselect",
    filename_prefix: str = "edmg_studio_ad"
) -> dict[str, Any]:
    """AnimateDiff Evolved txt2video workflow.

    Requires ComfyUI-AnimateDiff-Evolved custom nodes:
      - ADE_StandardStaticContextOptions
      - ADE_AnimateDiffLoaderGen1
    """
    frames = max(1, int(frames))
    context_length = max(1, int(context_length))
    context_overlap = max(0, int(context_overlap))

    return {
        "3": {"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
        "4": {"class_type":"ADE_StandardStaticContextOptions","inputs":{
            "context_length": context_length,
            "context_overlap": context_overlap,
            "fuse_method": "pyramid",
            "use_on_equal_length": True,
            "start_percent": 0.0,
            "guarantee_steps": 0
        }},
        "5": {"class_type":"ADE_AnimateDiffLoaderGen1","inputs":{
            "model":["3",0],
            "model_name": motion_model_name,
            "beta_schedule": beta_schedule,
            "context_options":["4",0]
        }},
        "6": {"class_type":"EmptyLatentImage","inputs":{"width":width,"height":height,"batch_size":frames}},
        "7": {"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["3",1]}},
        "8": {"class_type":"CLIPTextEncode","inputs":{"text":negative_prompt,"clip":["3",1]}},
        "9": {"class_type":"KSampler","inputs":{
            "seed":seed,
            "steps":steps,
            "cfg":cfg,
            "sampler_name":sampler,
            "scheduler":"normal",
            "denoise":1,
            "model":["5",0],
            "positive":["7",0],
            "negative":["8",0],
            "latent_image":["6",0]
        }},
        "10": {"class_type":"VAEDecode","inputs":{"samples":["9",0],"vae":["3",2]}},
        "11": {"class_type":"SaveImage","inputs":{"filename_prefix":filename_prefix,"images":["10",0]}}
    }

def svd_workflow(
    checkpoint: str,
    prompt: str,
    negative_prompt: str,
    seed: int,
    width: int,
    height: int,
    steps: int,
    cfg: float,
    sampler: str,
    svd_checkpoint: str = "svd_xt.safetensors",
    svd_num_frames: int = 14,
    svd_num_steps: int = 25,
    svd_motion_bucket_id: int = 127,
    svd_fps_id: int = 6,
    svd_cond_aug: float = 0.02,
    svd_decoding_t: int = 14,
    device: str = "cuda",
    filename_prefix: str = "edmg_studio_svd"
) -> dict[str, Any]:
    """txt2img -> Stable Video Diffusion (img2vid).

    Requires ComfyUI-Stable-Video-Diffusion custom nodes:
      - SVDSimpleImg2Vid
    """
    svd_num_frames = max(1, int(svd_num_frames))
    return {
        "3": {"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
        "5": {"class_type":"EmptyLatentImage","inputs":{"width":width,"height":height,"batch_size":1}},
        "6": {"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["3",1]}},
        "7": {"class_type":"CLIPTextEncode","inputs":{"text":negative_prompt,"clip":["3",1]}},
        "8": {"class_type":"KSampler","inputs":{
            "seed":seed,
            "steps":steps,
            "cfg":cfg,
            "sampler_name":sampler,
            "scheduler":"normal",
            "denoise":1,
            "model":["3",0],
            "positive":["6",0],
            "negative":["7",0],
            "latent_image":["5",0]
        }},
        "9": {"class_type":"VAEDecode","inputs":{"samples":["8",0],"vae":["3",2]}},
        "10": {"class_type":"SVDSimpleImg2Vid","inputs":{
            "image":["9",0],
            "checkpoint": svd_checkpoint,
            "num_frames": svd_num_frames,
            "num_steps": int(svd_num_steps),
            "motion_bucket_id": int(svd_motion_bucket_id),
            "fps_id": int(svd_fps_id),
            "cond_aug": float(svd_cond_aug),
            "seed": int(seed),
            "decoding_t": int(svd_decoding_t),
            "device": str(device)
        }},
        "11": {"class_type":"SaveImage","inputs":{"filename_prefix":filename_prefix,"images":["10",0]}}
    }
