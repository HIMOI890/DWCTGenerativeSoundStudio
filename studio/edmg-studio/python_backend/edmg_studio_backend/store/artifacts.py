from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

ARTIFACT_SCHEMA_VERSION = 1


def _sha256_file(path: Path, *, chunk_size: int = 1024 * 1024) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            chunk = fh.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _write_atomic(path: Path, payload: dict[str, Any]) -> None:
    from ..revisions import background_context
    from copy import deepcopy
    background = background_context.get()
    if background is not None:
        background.setdefault("artifacts", []).append((path, deepcopy(payload)))
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    with tmp.open("w", encoding="utf-8") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def build_artifact_manifest(
    *,
    artifact_path: Path,
    project_dir: Path,
    project_id: str | None = None,
    kind: str = "video",
    engine: str = "internal_video",
    model_id: str | None = None,
    model_revision: str | None = None,
    seed: int | None = None,
    params: dict[str, Any] | None = None,
    source_assets: list[dict[str, Any]] | None = None,
    parents: list[str] | None = None,
    review_state: str = "unreviewed",
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    rel = None
    try:
        rel = str(artifact_path.resolve().relative_to(project_dir.resolve())).replace("\\", "/")
    except Exception:
        rel = artifact_path.name
    content_hash = _sha256_file(artifact_path)
    manifest: dict[str, Any] = {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "project_id": project_id,
        "kind": kind,
        "path": rel,
        "content_hash": content_hash,
        "content_hash_alg": "sha256",
        "bytes": artifact_path.stat().st_size if artifact_path.exists() else None,
        "engine": engine,
        "model": {
            "id": model_id,
            "revision": model_revision,
        },
        "seed": seed,
        "params": dict(params or {}),
        "source_assets": list(source_assets or []),
        "lineage": {
            "parents": list(parents or []),
        },
        "review": {
            "state": review_state,
        },
        "safety": {
            "license_notes": None,
        },
    }
    if extra:
        manifest["extra"] = dict(extra)
    return manifest


def write_artifact_manifest(
    artifact_path: Path,
    *,
    project_dir: Path,
    project_id: str | None = None,
    kind: str = "video",
    engine: str = "internal_video",
    model_id: str | None = None,
    model_revision: str | None = None,
    seed: int | None = None,
    params: dict[str, Any] | None = None,
    source_assets: list[dict[str, Any]] | None = None,
    parents: list[str] | None = None,
    review_state: str = "unreviewed",
    extra: dict[str, Any] | None = None,
    manifest_path: Path | None = None,
) -> Path:
    """Write `<artifact>.artifact.json` next to the output file."""
    out = manifest_path or artifact_path.with_suffix(artifact_path.suffix + ".artifact.json")
    payload = build_artifact_manifest(
        artifact_path=artifact_path,
        project_dir=project_dir,
        project_id=project_id,
        kind=kind,
        engine=engine,
        model_id=model_id,
        model_revision=model_revision,
        seed=seed,
        params=params,
        source_assets=source_assets,
        parents=parents,
        review_state=review_state,
        extra=extra,
    )
    _write_atomic(out, payload)
    return out
