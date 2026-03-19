"""ComfyUI model wiring helpers.

Notes
-----
ComfyUI uses conventional model directories under `<COMFYUI_ROOT>/models/`.
Workflows refer to model filenames; this module maps workflow node inputs to folders.

This module never overwrites existing target files.
"""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

# Mapping from "kind" to ComfyUI subfolder.
KIND_TO_FOLDER: Dict[str, str] = {
    "checkpoint": "checkpoints",
    "vae": "vae",
    "lora": "loras",
    "clip": "clip",
    "controlnet": "controlnet",
    "embedding": "embeddings",
    "unet": "unet",
    "diffusion_model": "diffusion_models",
    "video": "video",
    "other": "other",
}

# Workflow input keys commonly used by ComfyUI nodes.
INPUT_KEY_TO_KIND: Dict[str, str] = {
    "ckpt_name": "checkpoint",
    "checkpoint": "checkpoint",
    "model_name": "checkpoint",
    "unet_name": "unet",
    "vae_name": "vae",
    "lora_name": "lora",
    "loras": "lora",
    "control_net_name": "controlnet",
    "controlnet_name": "controlnet",
    "embedding_name": "embedding",
    "clip_name": "clip",
    "text_encoder": "clip",
    "diffusion_model": "diffusion_model",
    "motion_model": "video",
    "video_model": "video",
}


@dataclass(frozen=True)
class NeededModel:
    filename: str
    kind: str


def _load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def _safe_symlink(src: Path, dst: Path) -> bool:
    try:
        dst.symlink_to(src)
        return True
    except Exception:
        return False


def _ensure_dir(p: Path) -> None:
    p.mkdir(parents=True, exist_ok=True)


def _link_or_copy(src: Path, dst: Path, mode: str) -> bool:
    if dst.exists():
        return True
    _ensure_dir(dst.parent)

    if mode in ("auto", "symlink"):
        if _safe_symlink(src, dst):
            return True
        if mode == "symlink":
            return False

    try:
        shutil.copy2(src, dst)
        return True
    except Exception:
        return False


def _find_by_filename(models_root: Path) -> Dict[str, Path]:
    found: Dict[str, Path] = {}
    if not models_root.exists():
        return found
    for p in models_root.rglob("*"):
        if p.is_file():
            found.setdefault(p.name, p)
    return found


def _extract_needed_models(workflow: dict) -> List[NeededModel]:
    needed: List[NeededModel] = []
    nodes = workflow.get("nodes") or workflow  # some exports are node-map directly
    if isinstance(nodes, dict):
        # {node_id: {class_type, inputs,...}}
        iterable = nodes.values()
    elif isinstance(nodes, list):
        iterable = nodes
    else:
        return needed

    for node in iterable:
        if not isinstance(node, dict):
            continue
        inputs = node.get("inputs") or {}
        if not isinstance(inputs, dict):
            continue
        for k, v in inputs.items():
            if not isinstance(v, str):
                continue
            kind = INPUT_KEY_TO_KIND.get(k)
            if not kind:
                continue
            needed.append(NeededModel(filename=v, kind=kind))

    # de-dup preserving order
    seen = set()
    out: List[NeededModel] = []
    for n in needed:
        key = (n.filename, n.kind)
        if key in seen:
            continue
        seen.add(key)
        out.append(n)
    return out


def wire_single(
    source: Path,
    kind: str,
    comfyui_root: Path,
    mode: str = "auto",
    dry_run: bool = False,
) -> bool:
    kind_norm = kind.strip().lower()
    folder = KIND_TO_FOLDER.get(kind_norm, KIND_TO_FOLDER["other"])
    target = comfyui_root / "models" / folder / source.name
    if dry_run:
        print(f"[DRY] {mode}: {source} -> {target}")
        return True
    return _link_or_copy(source, target, mode)


def wire_from_workflow(
    workflow_path: Path,
    comfyui_root: Path,
    models_root: Path,
    mode: str = "auto",
    dry_run: bool = False,
) -> bool:
    wf = _load_json(workflow_path)
    needed = _extract_needed_models(wf)
    available = _find_by_filename(models_root)

    ok = True
    for item in needed:
        src = available.get(item.filename)
        if not src:
            print(f"[WARN] Missing in models-root: {item.filename} (kind={item.kind})")
            ok = False
            continue
        folder = KIND_TO_FOLDER.get(item.kind, KIND_TO_FOLDER["other"])
        dst = comfyui_root / "models" / folder / item.filename
        if dry_run:
            print(f"[DRY] {mode}: {src} -> {dst}")
            continue
        if not _link_or_copy(src, dst, mode):
            print(f"[ERROR] Failed to wire: {src} -> {dst}")
            ok = False
    return ok
