"""enhanced_deforum_music_generator.integrations.hf_model_manager

Hugging Face model catalog + downloader + wiring helpers.

Design goals:
- Keep a *central* models store under `models_root`.
- Optionally wire (symlink/junction/copy) into:
    - Automatic1111 WebUI (checkpoints, loras, vae, embeddings, controlnet)
    - ComfyUI (checkpoints, loras, vae, embeddings, controlnet, video)
- Support auth via:
    - HF_TOKEN / HUGGINGFACE_TOKEN env vars
    - explicit `token=...`
    - additional HTTP headers (for enterprise mirrors)
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Optional, Tuple

import json
import os
import shutil
import sys

try:
    from huggingface_hub import snapshot_download
except Exception as e:  # pragma: no cover
    snapshot_download = None  # type: ignore

CATALOG_RELATIVE_PATH = Path(__file__).resolve().parents[1] / "presets" / "hf_video_model_catalog.json"


@dataclass(frozen=True)
class CatalogModel:
    name: str
    display_name: str
    repo_id: str
    family: str
    task: str
    pipeline: str
    recommended: Dict[str, Any]
    notes: str = ""


def load_catalog(catalog_path: Optional[Path] = None) -> Dict[str, CatalogModel]:
    """Load the bundled HF video model catalog."""
    path = catalog_path or CATALOG_RELATIVE_PATH
    data = json.loads(path.read_text(encoding="utf-8"))
    models: Dict[str, CatalogModel] = {}
    for m in data.get("models", []):
        models[m["name"]] = CatalogModel(
            name=m["name"],
            display_name=m.get("display_name", m["name"]),
            repo_id=m["repo_id"],
            family=m.get("family", ""),
            task=m.get("task", ""),
            pipeline=m.get("pipeline", ""),
            recommended=m.get("recommended", {}),
            notes=m.get("notes", ""),
        )
    return models


def get_hf_token(explicit_token: Optional[str] = None) -> Optional[str]:
    return explicit_token or os.environ.get("HF_TOKEN") or os.environ.get("HUGGINGFACE_TOKEN")


def download_model_snapshot(
    repo_id: str,
    *,
    dest_dir: Path,
    token: Optional[str] = None,
    allow_patterns: Optional[Iterable[str]] = None,
    ignore_patterns: Optional[Iterable[str]] = None,
    revision: Optional[str] = None,
) -> Path:
    """Download a HF repo snapshot to a local directory.

    Returns the local directory containing the snapshot.
    """
    if snapshot_download is None:
        raise RuntimeError(
            "huggingface_hub is not installed. Install it (and diffusers full deps) via: "
            "python setup.py --mode full"
        )

    dest_dir.mkdir(parents=True, exist_ok=True)

    local_dir = snapshot_download(
        repo_id=repo_id,
        local_dir=str(dest_dir),
        local_dir_use_symlinks=False,
        revision=revision,
        token=get_hf_token(token),
        allow_patterns=list(allow_patterns) if allow_patterns else None,
        ignore_patterns=list(ignore_patterns) if ignore_patterns else None,
        resume_download=True,
    )
    return Path(local_dir)


def central_model_dir(models_root: Path, model_name: str) -> Path:
    return models_root / "hf_video" / model_name


def ensure_downloaded_from_catalog(
    model_name: str,
    *,
    models_root: Path,
    token: Optional[str] = None,
    revision: Optional[str] = None,
    allow_patterns: Optional[Iterable[str]] = None,
    ignore_patterns: Optional[Iterable[str]] = None,
) -> Path:
    """Download a catalog entry into central storage and return its local path."""
    catalog = load_catalog()
    if model_name not in catalog:
        raise KeyError(f"Unknown model '{model_name}'. Known: {', '.join(sorted(catalog))}")

    entry = catalog[model_name]
    dest = central_model_dir(models_root, model_name)
    return download_model_snapshot(
        entry.repo_id,
        dest_dir=dest,
        token=token,
        revision=revision,
        allow_patterns=allow_patterns,
        ignore_patterns=ignore_patterns,
    )


def _try_symlink(src: Path, dst: Path) -> bool:
    try:
        if dst.exists() or dst.is_symlink():
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(src, dst, target_is_directory=src.is_dir())
        return True
    except Exception:
        return False


def _try_junction_dir(src: Path, dst: Path) -> bool:
    """Windows-only directory junction fallback."""
    if os.name != "nt":
        return False
    try:
        if dst.exists():
            return True
        dst.parent.mkdir(parents=True, exist_ok=True)
        import subprocess

        subprocess.check_call(["cmd", "/c", "mklink", "/J", str(dst), str(src)])
        return True
    except Exception:
        return False


def _copytree(src: Path, dst: Path) -> None:
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(src, dst)


def wire_directory(
    src_dir: Path,
    dst_dir: Path,
    *,
    prefer_link: bool = True,
) -> Tuple[Path, str]:
    """Wire src_dir into dst_dir via symlink/junction/copy.

    Returns: (dst_dir, method)
    """
    if dst_dir.exists():
        return dst_dir, "exists"

    if prefer_link and _try_symlink(src_dir, dst_dir):
        return dst_dir, "symlink"

    if prefer_link and _try_junction_dir(src_dir, dst_dir):
        return dst_dir, "junction"

    _copytree(src_dir, dst_dir)
    return dst_dir, "copy"


def wire_hf_video_model_to_comfyui(
    model_local_dir: Path,
    *,
    comfyui_root: Path,
    model_name: str,
    prefer_link: bool = True,
) -> Tuple[Path, str]:
    """Wire an HF *Diffusers-format* video model snapshot into ComfyUI.

    Since ComfyUI model folder conventions can vary by plugin, we place under:
        ComfyUI/models/video/<model_name>

    Your ComfyUI video nodes/plugins should be configured to point there.
    """
    dst = comfyui_root / "models" / "video" / model_name
    return wire_directory(model_local_dir, dst, prefer_link=prefer_link)


def wire_hf_video_model_to_a1111(
    model_local_dir: Path,
    *,
    a1111_root: Path,
    model_name: str,
    prefer_link: bool = True,
) -> Tuple[Path, str]:
    """Wire an HF *Diffusers-format* video model snapshot into A1111.

    A1111 doesn't natively load Diffusers video repos. We place these under:
        stable-diffusion-webui/models/video/<model_name>
    as a convenience store (for extensions/scripts).

    Returns destination + method.
    """
    dst = a1111_root / "models" / "video" / model_name
    return wire_directory(model_local_dir, dst, prefer_link=prefer_link)
