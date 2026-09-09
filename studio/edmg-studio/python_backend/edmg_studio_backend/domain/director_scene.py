"""Persistent creative intent and deterministic renderer-specific compilation.

Compilation is preparation, not inference or proof that a renderer is installed.
Unknown extension fields survive project round trips.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .project_time import int64


class ExtensibleModel(BaseModel):
    model_config = ConfigDict(extra="allow", allow_inf_nan=False)


class StoryBible(ExtensibleModel):
    revision: int = Field(default=1, ge=1)
    project_theme: str = ""
    narrative_summary: str = ""
    visual_style: str = ""
    characters: dict[str, str] = Field(default_factory=dict)
    locations: dict[str, str] = Field(default_factory=dict)
    continuity_rules: list[str] = Field(default_factory=list)
    forbidden_changes: list[str] = Field(default_factory=list)


class Subject(ExtensibleModel):
    id: str = Field(min_length=1)
    role: str = "primary"
    appearance_lock: bool = True
    appearance_notes: list[str] = Field(default_factory=list)
    expression: str = ""


class Camera(ExtensibleModel):
    shot_type: str = ""
    movement: str = ""
    stability: str = ""
    motion_strength: float = Field(default=0.4, ge=0, le=1)


class Environment(ExtensibleModel):
    location_id: str = ""
    location: str = ""
    weather: str = ""
    secondary_motion: list[str] = Field(default_factory=list)


class SceneSpec(ExtensibleModel):
    scene_id: str = Field(min_length=1, max_length=128)
    start_sample: str = "0"
    end_sample: str
    intent: str = Field(min_length=1, max_length=16000)
    continuity_mode: Literal["continuous", "cut", "independent"] = "continuous"
    subjects: list[Subject] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    camera: Camera = Field(default_factory=Camera)
    environment: Environment = Field(default_factory=Environment)
    lighting: dict = Field(default_factory=dict)
    renderer_hints: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_range(self):
        if int64(self.end_sample) <= int64(self.start_sample):
            raise ValueError("Scene end must be after its start")
        if len({subject.id for subject in self.subjects}) != len(self.subjects):
            raise ValueError("Subject IDs must be unique within a scene")
        return self


class DirectorDocument(ExtensibleModel):
    version: Literal[1] = 1
    story_bible: StoryBible = Field(default_factory=StoryBible)
    scenes: list[SceneSpec] = Field(default_factory=list, max_length=10000)
    analysis_revision: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def unique_scenes(self):
        if len({scene.scene_id for scene in self.scenes}) != len(self.scenes):
            raise ValueError("Scene IDs must be unique")
        return self


def compile_scene(scene: SceneSpec, bible: StoryBible, engine: str) -> dict:
    if engine not in {"hunyuan_video15", "ltx_25", "external"}:
        raise ValueError("Unsupported prompt compiler")
    subjects = []
    for subject in scene.subjects:
        identity = bible.characters.get(subject.id, subject.id)
        notes = ", ".join(subject.appearance_notes)
        subjects.append("; ".join(filter(None, [identity, notes, subject.expression])))
    environment = scene.environment
    location = bible.locations.get(environment.location_id, environment.location)
    camera = ", ".join(
        filter(None, [scene.camera.shot_type, scene.camera.movement, scene.camera.stability])
    )
    context = ". ".join(
        filter(
            None,
            [bible.visual_style, scene.intent, "; ".join(subjects), location, environment.weather],
        )
    )
    if engine == "ltx_25":
        action = " Then, ".join(scene.actions)
        prompt = f"{context}. The shot unfolds: {action}. Camera: {camera}."
    elif engine == "hunyuan_video15":
        prompt = (
            f"{context}. Subject action: {'; '.join(scene.actions)}. Camera and framing: {camera}."
        )
    else:
        prompt = f"{context}. Actions: {'; '.join(scene.actions)}. Camera: {camera}."
    if environment.secondary_motion:
        prompt += " Environmental motion: " + "; ".join(environment.secondary_motion) + "."
    if scene.lighting:
        prompt += (
            " Lighting: " + json.dumps(scene.lighting, ensure_ascii=False, sort_keys=True) + "."
        )
    locks = list(bible.continuity_rules)
    locks.extend(
        f"Preserve appearance of {subject.id}"
        for subject in scene.subjects
        if subject.appearance_lock
    )
    if locks:
        prompt += " Continuity: " + "; ".join(locks) + "."
    if bible.forbidden_changes:
        # Constraints remain in positive text too; not every pipeline accepts negatives.
        prompt += " Do not change: " + "; ".join(bible.forbidden_changes) + "."
    source = {"scene": scene.model_dump(mode="json"), "story_bible": bible.model_dump(mode="json")}
    return {
        "version": 1,
        "compiler_version": "1",
        "engine": engine,
        "scene_id": scene.scene_id,
        "story_bible_revision": bible.revision,
        "source_hash": hashlib.sha256(
            json.dumps(source, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest(),
        "prompt": prompt,
        "constraints": locks + list(bible.forbidden_changes),
        "start_sample": scene.start_sample,
        "end_sample": scene.end_sample,
        "status": "prepared",
    }
