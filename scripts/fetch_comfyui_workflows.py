#!/usr/bin/env python3
"""scripts/fetch_comfyui_workflows.py

Fetch and optionally patch ComfyUI workflow JSONs.

This script is intentionally dependency-free (stdlib only) so it can run in fresh setups.

Usage:
    python scripts/fetch_comfyui_workflows.py --out comfyui_workflows/downloaded
    python scripts/fetch_comfyui_workflows.py --out comfyui_workflows/downloaded --patch --comfyui-root /path/to/ComfyUI
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class ModelHint:
    want: str
    alts: List[str]
    type: str = ""


@dataclass(frozen=True)
class WorkflowSpec:
    workflow_id: str
    title: str
    family: str
    source_url: str
    file_name: str
    model_hints: List[ModelHint]


def _load_manifest(path: Path) -> List[WorkflowSpec]:
    """
    Load workflow manifest JSON.

    Supported manifest schemas:
      1) EDMG schema:
         {"workflows":[{"id":"x","source_url":"https://...","file_name":"x.json", ...}, ...]}
      2) Minimal schema (used by unit tests):
         {"workflows":[{"id":"x","url":"file:///.../x.json","output":"x.json"}]}
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    items = data.get("workflows") or data.get("items") or []
    if not isinstance(items, list):
        raise ValueError("Manifest must contain a list under `workflows` (or `items`).")

    out: List[WorkflowSpec] = []
    for it in items:
        if not isinstance(it, dict):
            continue

        workflow_id = str(it.get("id") or it.get("workflow_id") or "").strip()
        if not workflow_id:
            raise ValueError(f"Manifest entry missing `id`: {it}")

        source_url = it.get("source_url") or it.get("url")
        if not source_url:
            raise ValueError(f"Manifest entry {workflow_id} missing `source_url`/`url`")

        file_name = it.get("file_name") or it.get("output") or f"{workflow_id}.json"
        title = it.get("title") or workflow_id
        family = it.get("family") or "general"
        required_models = it.get("required_models") or it.get("model_hints") or []
        model_hints: List[ModelHint] = []
        if isinstance(required_models, list):
            for m in required_models:
                if not isinstance(m, dict):
                    continue
                model_hints.append(
                    ModelHint(
                        want=str(m.get("want", "")),
                        alts=[str(x) for x in (m.get("alts") or []) if x],
                        type=str(m.get("type", "")),
                    )
                )

        out.append(
            WorkflowSpec(
                workflow_id=workflow_id,
                title=title,
                family=family,
                source_url=str(source_url),
                file_name=str(file_name),
                model_hints=model_hints,
            )
        )
    return out

def _download(url: str, dest: Path, *, retries: int = 3, timeout: int = 60) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    # Support local file sources for offline/CI use.
    if url.startswith('file://'):
        from urllib.parse import urlparse
        import shutil
        src_path = Path(urlparse(url).path)
        if not src_path.exists():
            raise FileNotFoundError(f'Workflow source not found: {src_path}')
        shutil.copy2(src_path, dest)
        return
    headers = {
        "User-Agent": "EDMG-WorkflowFetcher/1.0 (+https://github.com/)",
        "Accept": "application/json,text/plain,*/*",
    }
    req = urllib.request.Request(url, headers=headers)
    last_err: Optional[BaseException] = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                content = resp.read()
            dest.write_bytes(content)
            return
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = e
            if attempt < retries:
                time.sleep(1.0 * attempt)
            continue
    raise RuntimeError(f"Failed to download {url} after {retries} tries: {last_err}") from last_err


def _iter_strings(obj: Any) -> Iterable[Tuple[List[Any], str]]:
    """Yield (path, string_value) for every string in nested JSON-like object."""
    stack: List[Tuple[List[Any], Any]] = [([], obj)]
    while stack:
        path, cur = stack.pop()
        if isinstance(cur, dict):
            for k, v in cur.items():
                stack.append((path + [k], v))
        elif isinstance(cur, list):
            for i, v in enumerate(cur):
                stack.append((path + [i], v))
        elif isinstance(cur, str):
            yield path, cur


def _set_at_path(obj: Any, path: List[Any], value: Any) -> None:
    cur = obj
    for p in path[:-1]:
        cur = cur[p]
    cur[path[-1]] = value


def _scan_comfyui_model_basenames(comfyui_root: Path) -> Dict[str, Path]:
    models_root = comfyui_root / "models"
    found: Dict[str, Path] = {}
    if not models_root.exists():
        return found
    for p in models_root.rglob("*"):
        if p.is_file():
            found[p.name] = p
    return found


def _choose_best_match(hint: ModelHint, available: Dict[str, Path]) -> Optional[str]:
    if hint.want in available:
        return hint.want

    # Prefer exact alts, then substring matches.
    for alt in hint.alts:
        # Exact filename
        if alt in available:
            return alt

    # Substring / regex-ish match on basenames
    alt_patterns = [hint.want] + hint.alts
    candidates: List[str] = list(available.keys())
    for pat in alt_patterns:
        if not pat:
            continue
        # treat pat as substring (safe)
        for c in candidates:
            if pat.lower() in c.lower():
                return c
    return None


def _patch_workflow_models(workflow: Dict[str, Any], hints: List[ModelHint], available: Dict[str, Path]) -> Tuple[Dict[str, Any], List[str]]:
    changed: List[str] = []
    # Build map: want -> chosen
    mapping: Dict[str, str] = {}
    for h in hints:
        chosen = _choose_best_match(h, available)
        if chosen and chosen != h.want and h.want:
            mapping[h.want] = chosen

    if not mapping:
        return workflow, changed

    for path, s in _iter_strings(workflow):
        if s in mapping:
            _set_at_path(workflow, path, mapping[s])
            changed.append(f"{s} -> {mapping[s]} @ {path}")
    return workflow, changed


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Fetch and patch ComfyUI workflow JSONs (EDMG).")
    p.add_argument("--manifest", default="comfyui_workflows/manifest.json", help="Manifest JSON path.")
    p.add_argument("--out", required=True, help="Output directory for downloaded workflows.")
    p.add_argument("--only", action="append", default=[], help="Only fetch specific workflow id(s). Repeatable.")
    p.add_argument("--patch", action="store_true", help="Patch workflow model filenames based on your ComfyUI/models/*.")
    p.add_argument("--comfyui-root", default="", help="Path to ComfyUI root (required with --patch).")
    p.add_argument("--overwrite", action="store_true", help="Overwrite existing files.")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    manifest_path = Path(args.manifest)
    out_dir = Path(args.out)
    only = set(args.only or [])
    specs = _load_manifest(manifest_path)

    if args.patch and not args.comfyui_root:
        print("ERROR: --patch requires --comfyui-root", file=sys.stderr)
        return 2

    available = _scan_comfyui_model_basenames(Path(args.comfyui_root)) if args.patch else {}

    out_dir.mkdir(parents=True, exist_ok=True)

    fetched = 0
    patched = 0
    for spec in specs:
        if only and spec.workflow_id not in only:
            continue

        dest = out_dir / spec.file_name
        if dest.exists() and not args.overwrite:
            print(f"[skip] {spec.workflow_id}: {dest} exists (use --overwrite)")
            continue

        print(f"[fetch] {spec.workflow_id}: {spec.title}")
        _download(spec.source_url, dest)
        fetched += 1

        if args.patch:
            try:
                wf = json.loads(dest.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                # Some repos ship JSON-with-comments; keep as-is.
                print(f"[warn] {spec.workflow_id}: not valid JSON, skipping patch")
                continue

            wf2, changes = _patch_workflow_models(wf, spec.model_hints, available)
            if changes:
                dest.write_text(json.dumps(wf2, indent=2), encoding="utf-8")
                patched += 1
                print(f"[patch] {spec.workflow_id}:")
                for c in changes[:25]:
                    print(f"  - {c}")
                if len(changes) > 25:
                    print(f"  ... +{len(changes)-25} more")
            else:
                print(f"[patch] {spec.workflow_id}: no changes needed")

    print(f"Done. fetched={fetched} patched={patched} out={out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
