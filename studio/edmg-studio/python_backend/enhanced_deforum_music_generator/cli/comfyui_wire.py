"""ComfyUI model wiring CLI.

Purpose
-------
Copy or link model files from a central store into the correct ComfyUI model folders,
optionally inferred from a workflow JSON (by node input keys/class types).

This keeps ComfyUI workflows portable while letting you store models once.

Usage
-----
# Wire a single file to a target kind:
python -m enhanced_deforum_music_generator comfyui-wire --comfyui-root /path/to/ComfyUI \
    --source /path/to/model.safetensors --kind checkpoint

# Wire all models referenced by a workflow (auto-detect kinds by nodes):
python -m enhanced_deforum_music_generator comfyui-wire --comfyui-root /path/to/ComfyUI \
    --models-root external/models --workflow comfyui_workflows/downloaded/some.json
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional

from enhanced_deforum_music_generator.integrations.comfyui_model_wiring import wire_from_workflow, wire_single


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="comfyui-wire", add_help=True)
    p.add_argument("--comfyui-root", required=True, help="Path to ComfyUI repo root.")
    p.add_argument("--models-root", default="external/models", help="Central model store to search.")
    p.add_argument("--mode", default="auto", choices=["auto", "symlink", "copy"], help="Link strategy.")
    p.add_argument("--dry-run", action="store_true", help="Print actions without writing.")
    p.add_argument("--workflow", help="Workflow JSON to infer required model files.")
    p.add_argument("--source", help="Single model file path to wire.")
    p.add_argument("--kind", help="Kind for --source: checkpoint|vae|lora|clip|controlnet|embedding|unet|video|other")
    return p


def run_cli(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    comfyui_root = Path(args.comfyui_root).expanduser().resolve()
    models_root = Path(args.models_root).expanduser().resolve()

    if args.workflow:
        return 0 if wire_from_workflow(
            workflow_path=Path(args.workflow).expanduser().resolve(),
            comfyui_root=comfyui_root,
            models_root=models_root,
            mode=args.mode,
            dry_run=args.dry_run,
        ) else 2

    if args.source and args.kind:
        return 0 if wire_single(
            source=Path(args.source).expanduser().resolve(),
            kind=args.kind,
            comfyui_root=comfyui_root,
            mode=args.mode,
            dry_run=args.dry_run,
        ) else 2

    raise SystemExit("Provide either --workflow or (--source and --kind).")


if __name__ == "__main__":
    raise SystemExit(run_cli())
