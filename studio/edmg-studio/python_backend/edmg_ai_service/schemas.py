from __future__ import annotations

from pydantic import BaseModel, Field
from typing import Any


class PlanRequest(BaseModel):
    title: str | None = None
    user_notes: str | None = None

    # optional metadata (EDMG can fill these from its own analysis)
    duration_s: float | None = None
    bpm: float | None = None

    # optional text-derived content
    lyrics: str | None = None
    tags: list[str] = Field(default_factory=list)

    # knobs
    style_prefs: str | None = None
    num_variants: int = Field(default=3, ge=1, le=10)
    max_scenes: int = Field(default=12, ge=1, le=64)


class Scene(BaseModel):
    start_s: float
    end_s: float
    prompt: str
    negative_prompt: str | None = None
    camera: str | None = None
    motion: str | None = None
    notes: str | None = None


class PlanVariant(BaseModel):
    name: str
    logline: str
    mood: str | None = None
    visual_motifs: list[str] = Field(default_factory=list)
    color_palette: list[str] = Field(default_factory=list)
    scenes: list[Scene] = Field(default_factory=list)


class PlanResponse(BaseModel):
    provider: str
    model: str | None = None
    variants: list[PlanVariant]


class HealthResponse(BaseModel):
    ok: bool = True
    provider: str
    model: str | None = None
    version: str = "0.1.0"


class ErrorResponse(BaseModel):
    error: str
    detail: Any | None = None
