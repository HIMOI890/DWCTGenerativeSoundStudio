from __future__ import annotations

import math
import time
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..preview_limits import PreviewBudgetLimits
from ..security import configured_media_signer, media_url_ttl_s
from ..utils.path import safe_join
from ..project_metadata import validate_metadata_patch


def validate_timeline_media(project_dir, timeline):
    """Reject unsafe stored asset references before any preview decoding or cache hit."""
    validate_metadata_patch({"timeline": timeline})
    def visit(value):
        if isinstance(value, dict):
            for key, item in value.items():
                if key in {"asset", "mask_asset", "source_path", "path", "filename"} and isinstance(item, str) and item:
                    safe_join(project_dir, item)
                    if key in {"asset", "mask_asset"}:
                        directory = "masks" if key == "mask_asset" else "overlays"
                        safe_join(project_dir, f"assets/{directory}/{item}")
                visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(timeline)


class MediaRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["file", "audio", "preview"]
    path: str | None = None
    preview_kind: Literal["frame", "segment", "diffusion_segment"] = "frame"
    query: dict[str, Any] = Field(default_factory=dict)


class MediaBatchRequest(BaseModel):
    requests: list[MediaRequest] = Field(min_length=1, max_length=100)


def validate_preview(kind: str, query: dict[str, Any]) -> None:
    diffusion = kind == "diffusion_segment"
    if diffusion:
        cfg, strength = float(query.get("cfg", 7)), float(query.get("strength", 0.45))
        if not math.isfinite(cfg) or cfg <= 0 or not math.isfinite(strength) or not 0 < strength <= 1:
            raise ValueError("Preview cfg must be finite and positive; strength must be within (0, 1]")
    limits = PreviewBudgetLimits.from_env(diffusion=diffusion)
    width, height = int(query.get("w", 512 if diffusion else 768)), int(query.get("h", 512 if diffusion else 432))
    if kind == "frame":
        stamp = float(query.get("t", 0))
        if not math.isfinite(stamp) or stamp < 0:
            raise ValueError("Preview time must be finite and nonnegative")
        limits.validate_frame(width=width, height=height)
    else:
        limits.validate_segment(start_s=float(query.get("start_s", 0)),
            end_s=float(query.get("end_s", 2 if diffusion else 5)), width=width, height=height,
            fps=int(query.get("fps", 2 if diffusion else 6)),
            diffusion_steps=int(query.get("steps", 6)) if diffusion else None)


def create_media_router(get_store, security):
    router = APIRouter(tags=["media"])
    signer = configured_media_signer(security.auth_token)

    @router.post("/v1/projects/{project_id}/media-urls")
    def issue(project_id: str, req: MediaBatchRequest):
        store = get_store()
        try:
            project = store.get(project_id)
        except ValueError as exc:
            raise HTTPException(400, "Invalid project path") from exc
        if project is None:
            raise HTTPException(404, "Project not found")
        urls = []
        now, ttl = int(time.time()), media_url_ttl_s()
        for item in req.requests:
            query = dict(item.query)
            route, purpose = item.purpose, item.purpose
            try:
                if item.purpose == "preview":
                    route = "preview/" + item.preview_kind
                    purpose = "preview_" + item.preview_kind
                    allowed = {"t", "w", "h", "force"} if item.preview_kind == "frame" else {
                        "start_s", "end_s", "w", "h", "fps", "force"}
                    if item.preview_kind == "diffusion_segment":
                        allowed |= {"steps", "cfg", "strength", "model_id", "variant_index", "seed", "prompt"}
                    if query.keys() - allowed:
                        raise ValueError("Unsupported preview parameters")
                    validate_preview(item.preview_kind, query)
                    validate_timeline_media(store.project_dir(project_id), project.meta.get("timeline") or {})
                    safe_join(store.project_dir(project_id), "outputs/previews")
                else:
                    if query:
                        raise ValueError("File and audio requests do not accept extra query parameters")
                    if item.purpose == "audio":
                        filename = str((project.meta.get("audio") or {}).get("filename") or "")
                        # Validate the filename independently before adding its server-owned directory.
                        safe_join(store.project_dir(project_id), filename)
                        relative = "assets/audio/" + filename
                    else:
                        relative = item.path or ""
                        query["path"] = relative
                    target = safe_join(store.project_dir(project_id), relative)
                    if not target.is_file():
                        raise HTTPException(404, "Media file not found")
                url, expires = signer.issue_signed_path(path=f"/v1/projects/{project_id}/{route}",
                    query=query, project_id=project_id, purpose=purpose, ttl_s=ttl, now=now)
            except (ValueError, TypeError, OverflowError) as exc:
                raise HTTPException(400, str(exc)) from exc
            urls.append({"purpose": item.purpose, "url": url})
        return {"urls": urls, "expires_at": expires}

    return router
