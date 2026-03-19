"""enhanced_deforum_music_generator.cli.hf_models

Manage HF video model catalog downloads and wiring.

Examples:
    python -m enhanced_deforum_music_generator.cli.hf_models list
    python -m enhanced_deforum_music_generator.cli.hf_models download wan2.2-ti2v-5b --models-root models_store
    python -m enhanced_deforum_music_generator.cli.hf_models wire wan2.2-ti2v-5b --models-root models_store --comfyui-root /path/to/ComfyUI
"""

from __future__ import annotations

import argparse
from pathlib import Path

from enhanced_deforum_music_generator.integrations.hf_model_manager import (
    load_catalog,
    ensure_downloaded_from_catalog,
    wire_hf_video_model_to_comfyui,
    wire_hf_video_model_to_a1111,
)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="List catalog models.")

    dl = sub.add_parser("download", help="Download a catalog model to the central store.")
    dl.add_argument("name")
    dl.add_argument("--models-root", default="models_store")
    dl.add_argument("--token", default=None)

    w = sub.add_parser("wire", help="Wire a downloaded model into ComfyUI and/or A1111.")
    w.add_argument("name")
    w.add_argument("--models-root", default="models_store")
    w.add_argument("--comfyui-root", default=None)
    w.add_argument("--a1111-root", default=None)
    w.add_argument("--prefer-link", action="store_true")

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    catalog = load_catalog()

    if args.cmd == "list":
        for k in sorted(catalog.keys()):
            m = catalog[k]
            print(f"{k:22s}  {m.repo_id}  ({m.task})")
        return 0

    if args.cmd == "download":
        path = ensure_downloaded_from_catalog(
            args.name,
            models_root=Path(args.models_root).expanduser().resolve(),
            token=args.token,
        )
        print(path)
        return 0

    if args.cmd == "wire":
        models_root = Path(args.models_root).expanduser().resolve()
        local_dir = ensure_downloaded_from_catalog(args.name, models_root=models_root)
        if args.comfyui_root:
            dst, method = wire_hf_video_model_to_comfyui(
                local_dir,
                comfyui_root=Path(args.comfyui_root).expanduser().resolve(),
                model_name=args.name,
                prefer_link=args.prefer_link,
            )
            print(f"ComfyUI: {dst} ({method})")
        if args.a1111_root:
            dst, method = wire_hf_video_model_to_a1111(
                local_dir,
                a1111_root=Path(args.a1111_root).expanduser().resolve(),
                model_name=args.name,
                prefer_link=args.prefer_link,
            )
            print(f"A1111: {dst} ({method})")
        return 0

    raise AssertionError("unreachable")


if __name__ == "__main__":
    raise SystemExit(main())
