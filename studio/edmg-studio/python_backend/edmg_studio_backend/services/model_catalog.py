from __future__ import annotations

# Built-in curated model catalog for EDMG Studio.
# NOTE: We intentionally do NOT bundle large weights in the installer.
# The catalog drives the GUI "Model Manager" which downloads models on-demand.

from typing import Any

def built_in_catalog() -> list[dict[str, Any]]:
    # Fields:
    # - id: stable ID
    # - name: display name
    # - kind: llm | checkpoint | lora | embedding | vae | controlnet | motion_module
    # - source: ollama | hf | civitai | local
    # - license_id: SPDX-ish or platform label (may be "other" / "openrail++", etc.)
    # - license_url: link to full text
    # - redistributable_in_installer: bool (hard rule: we ship none unless explicitly true)
    # - recommended: "default" | "advanced"
    # - install: installation instructions (where it goes)
    return [
        {
            "id": "ollama_qwen2p5_3b_instruct",
            "name": "Qwen2.5 3B Instruct (Ollama)",
            "kind": "llm",
            "source": "ollama",
            "ollama_model": "qwen2.5:3b-instruct",
            "license_id": "Apache-2.0",
            "license_url": "https://huggingface.co/Qwen/Qwen2.5-3B-Instruct",
            "redistributable_in_installer": False,
            "recommended": "default",
            "notes": "Fast local director model (CPU-friendly)."
        },
        {
            "id": "ollama_qwen2p5_7b_instruct",
            "name": "Qwen2.5 7B Instruct (Ollama)",
            "kind": "llm",
            "source": "ollama",
            "ollama_model": "qwen2.5:7b-instruct",
            "license_id": "Apache-2.0",
            "license_url": "https://huggingface.co/Qwen/Qwen2.5-7B-Instruct",
            "redistributable_in_installer": False,
            "recommended": "advanced",
            "notes": "Higher quality; slower on CPU."
        },

        {
            "id": "hf_sd15_internal",
            "name": "Stable Diffusion v1.5 (Internal / Diffusers)",
            "kind": "diffusers",
            "source": "hf",
            "hf_repo_id": "runwayml/stable-diffusion-v1-5",
            "target": {"engine": "internal", "folder": "diffusers"},
            "license_id": "openrail-m",
            "license_url": "https://huggingface.co/runwayml/stable-diffusion-v1-5",
            "redistributable_in_installer": False,
            "recommended": "default",
            "notes": "CPU-friendly baseline for internal rendering. Downloaded on-demand."
        },
{
    "id": "hf_sdxl_internal",
    "name": "Stable Diffusion XL Base 1.0 (Internal / Diffusers)",
    "kind": "diffusers",
    "source": "hf",
    "hf_repo_id": "stabilityai/stable-diffusion-xl-base-1.0",
    "target": {"engine": "internal", "folder": "diffusers"},
    "license_id": "openrail++",
    "license_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md",
    "redistributable_in_installer": False,
    "recommended": "advanced",
    "notes": "Higher quality internal rendering (GPU recommended). Downloaded on-demand."
},
        {
            "id": "hf_sdxl_base_1_0",
            "name": "Stable Diffusion XL Base 1.0 (Checkpoint)",
            "kind": "checkpoint",
            "source": "hf",
            "hf_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/resolve/main/sd_xl_base_1.0.safetensors",
            "filename": "sd_xl_base_1.0.safetensors",
            "target": {"engine": "comfyui", "folder": "checkpoints"},
            "license_id": "openrail++",
            "license_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-base-1.0/blob/main/LICENSE.md",
            "redistributable_in_installer": False,
            "recommended": "default",
            "notes": "Good default image generation model."
        },
        {
            "id": "hf_sdxl_refiner_1_0",
            "name": "Stable Diffusion XL Refiner 1.0 (Checkpoint)",
            "kind": "checkpoint",
            "source": "hf",
            "hf_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-refiner-1.0/resolve/main/sd_xl_refiner_1.0.safetensors",
            "filename": "sd_xl_refiner_1.0.safetensors",
            "target": {"engine": "comfyui", "folder": "checkpoints"},
            "license_id": "openrail++",
            "license_url": "https://huggingface.co/stabilityai/stable-diffusion-xl-refiner-1.0",
            "redistributable_in_installer": False,
            "recommended": "advanced",
            "notes": "Optional second-pass enhancement; heavy VRAM."
        },
    ]

def built_in_packs() -> list[dict[str, Any]]:
    # Packs used by first-run wizard.
    return [
        {
            "id": "basic",
            "name": "Basic (Planning + Preflight)",
            "description": "Installs the default Ollama director model. Use if you only want planning/analyzing right now.",
            "models": ["ollama_qwen2p5_3b_instruct"],
        },
        {
            "id": "creator",
            "name": "Creator (Recommended)",
            "description": "Installs the default Ollama director model plus SDXL Base checkpoint for ComfyUI rendering.",
            "models": ["ollama_qwen2p5_3b_instruct", "hf_sdxl_base_1_0"],
        },
        {
            "id": "pro",
            "name": "Pro (Advanced)",
            "description": "Adds SDXL Refiner (higher quality, heavier GPU/VRAM).",
            "models": ["ollama_qwen2p5_3b_instruct", "hf_sdxl_base_1_0", "hf_sdxl_refiner_1_0"],
        },
    ]
