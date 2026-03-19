from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import APIRouter

from ..integrations.hf_model_manager import (
    load_catalog,
    ensure_downloaded_from_catalog,
    central_model_dir,
)

router = APIRouter()


def _models_root() -> Path:
    """Central models root.

    You can override via EDMG_MODELS_ROOT.
    """
    env = os.environ.get("EDMG_MODELS_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    # Default: repo_root/models_store (works for dev checkouts)
    return Path(__file__).resolve().parents[3] / "models_store"


@router.get("/catalog")
async def catalog() -> Dict[str, Any]:
    """Return the bundled HF video model catalog."""
    models = load_catalog()
    out = []
    root = _models_root()
    for name, m in sorted(models.items(), key=lambda kv: kv[0]):
        local_dir = central_model_dir(root, name)
        out.append(
            {
                "name": m.name,
                "display_name": m.display_name,
                "repo_id": m.repo_id,
                "family": m.family,
                "task": m.task,
                "pipeline": m.pipeline,
                "recommended": m.recommended,
                "notes": m.notes,
                "installed": local_dir.exists(),
                "local_path": str(local_dir),
            }
        )
    return {"models_root": str(root), "models": out}


@router.post("/download")
async def download(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Download a model from the bundled catalog into central storage.

    JSON body:
      {"model_name": "svd_xt", "token": "..." (optional), "revision": "..." (optional)}
    """
    model_name = str(payload.get("model_name") or payload.get("name") or "").strip()
    if not model_name:
        return {"ok": False, "error": "model_name is required"}

    token: Optional[str] = payload.get("token") or None
    revision: Optional[str] = payload.get("revision") or None
    root = _models_root()

    try:
        local_dir = ensure_downloaded_from_catalog(
            model_name,
            models_root=root,
            token=token,
            revision=revision,
        )
    except Exception as e:
        return {"ok": False, "error": str(e)}

    return {"ok": True, "model_name": model_name, "local_path": str(local_dir)}
