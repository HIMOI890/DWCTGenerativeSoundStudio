"""Compatibility adapters from current Studio JSON documents to v1 contracts."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from .v1 import (
    AssetRef,
    CapabilityRequirement,
    CueContract,
    JobContract,
    PlanWarning,
    ProjectContract,
    RenderAllocation,
    RenderDependency,
    RenderEstimates,
    RenderPlanContract,
    RenderTaskContract,
    utc_now,
)


def _mapping(value: object) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return {str(key): item for key, item in value.items()}
    return {}


def _timestamp(value: object, *, fallback: datetime | None = None) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value.strip():
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    else:
        return fallback or utc_now()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def adapt_legacy_project(payload: Mapping[str, Any]) -> ProjectContract:
    """Adapt the current ``project.json`` shape without dropping extension data."""

    project_id = str(payload.get("id") or payload.get("project_id") or "").strip()
    name = str(payload.get("name") or payload.get("project_name") or "").strip()
    if not project_id or not name:
        raise ValueError("legacy project requires a stable id and name")

    created_at = _timestamp(payload.get("created_at"))
    updated_at = _timestamp(payload.get("updated_at"), fallback=created_at)
    metadata = _mapping(payload.get("meta") or payload.get("metadata"))
    audio_data = _mapping(metadata.get("audio"))
    audio: AssetRef | None = None
    filename = str(audio_data.get("filename") or "").strip()
    if filename:
        audio = AssetRef(
            id=f"{project_id}-audio",
            relative_path=f"assets/audio/{filename}",
            size_bytes=int(audio_data["size_bytes"]) if audio_data.get("size_bytes") is not None else None,
        )

    known = {"id", "project_id", "name", "project_name", "created_at", "updated_at", "meta", "metadata"}
    extensions = {str(key): value for key, value in payload.items() if key not in known}
    revision_raw = payload.get("revision")
    if revision_raw is None:
        revision_raw = metadata.get("revision", 1)
    try:
        revision = max(1, int(revision_raw))
    except (TypeError, ValueError):
        revision = 1

    return ProjectContract(
        id=project_id,
        name=name,
        revision=revision,
        created_at=created_at,
        updated_at=max(created_at, updated_at),
        audio=audio,
        timeline=_mapping(metadata.get("timeline")),
        metadata=metadata,
        extensions={"legacy_top_level": extensions} if extensions else {},
    )


def adapt_legacy_job(payload: Mapping[str, Any]) -> JobContract:
    """Adapt the process-local JSON job record to the frozen durable-job shape."""

    created_at = _timestamp(payload.get("created_at"))
    updated_at = _timestamp(payload.get("updated_at"), fallback=created_at)
    return JobContract(
        id=str(payload.get("id") or ""),
        project_id=str(payload.get("project_id") or ""),
        job_type=str(payload.get("type") or payload.get("job_type") or ""),
        status=str(payload.get("status") or "queued"),
        created_at=created_at,
        updated_at=max(created_at, updated_at),
        payload=_mapping(payload.get("payload")),
        result=_mapping(payload.get("result")) if payload.get("result") is not None else None,
        error=str(payload["error"]) if payload.get("error") is not None else None,
        progress=_mapping(payload.get("progress")) if payload.get("progress") is not None else None,
    )


def adapt_legacy_render_plan(payload: Mapping[str, Any]) -> RenderPlanContract:
    """Represent the current section/step plan as a v1 task DAG.

    The original payload is retained in ``extensions`` so a round trip never
    silently loses current Render Conductor fields.
    """

    plan_id = str(payload.get("plan_id") or payload.get("id") or "").strip()
    project_id = str(payload.get("project_id") or "").strip()
    if not plan_id or not project_id:
        raise ValueError("legacy render plan requires plan_id and project_id")

    tasks: list[RenderTaskContract] = []
    dependencies: list[RenderDependency] = []
    allocations: list[RenderAllocation] = []
    estimated_seconds = 0.0
    previous_by_section: dict[str, str] = {}

    for section_index, section_value in enumerate(payload.get("sections") or []):
        section = _mapping(section_value)
        scene_id = str(section.get("scene_id") or f"scene-{section_index}")
        estimated_seconds += float(section.get("estimated_seconds") or 0.0)
        for step_index, step_value in enumerate(section.get("steps") or []):
            step = _mapping(step_value)
            task_id = str(step.get("id") or f"{scene_id}-step-{step_index}")
            tasks.append(
                RenderTaskContract(
                    id=task_id,
                    kind=str(step.get("kind") or "render"),
                    inputs={"scene_id": scene_id, **_mapping(step.get("inputs"))},
                    outputs=_mapping(step.get("outputs")),
                )
            )
            previous = previous_by_section.get(scene_id)
            if previous:
                dependencies.append(RenderDependency(from_task=previous, to_task=task_id))
            previous_by_section[scene_id] = task_id
            allocations.append(
                RenderAllocation(
                    task_id=task_id,
                    capability=CapabilityRequirement(
                        media="video",
                        operation="assemble" if str(step.get("kind")) == "assemble" else "generate",
                        controls=["text"],
                    ),
                    preferred_provider=str(step.get("adapter") or section.get("engine") or "internal"),
                    fallbacks=[],
                )
            )

    created_at = _timestamp(payload.get("created_at"))
    diagnostics = [str(item) for item in payload.get("diagnostics") or []]
    variant_index = int(payload.get("variant_index") or 0)
    return RenderPlanContract(
        id=plan_id,
        project_id=project_id,
        revision=max(1, variant_index + 1),
        intent_revision=f"legacy-intent-{variant_index}",
        project_revision="legacy-project-1",
        created_at=created_at,
        updated_at=created_at,
        tasks=tasks,
        dependencies=dependencies,
        allocations=allocations,
        estimates=RenderEstimates(seconds=max(0.0, estimated_seconds), disk_gb=0.0),
        warnings=[PlanWarning(code="legacy-diagnostic", message=item) for item in diagnostics],
        extensions={"legacy_render_plan": dict(payload)},
    )


def adapt_legacy_cue(payload: Mapping[str, Any], *, project_id: str) -> CueContract:
    """Adapt existing Unreal/workbench cue events to the common cue contract."""

    cue_id = str(payload.get("cue_id") or payload.get("id") or "").strip()
    if not cue_id:
        raise ValueError("legacy cue requires cue_id or id")
    instruction = payload.get("instruction")
    cue_payload: dict[str, Any] = {}
    if instruction is not None:
        cue_payload["instruction"] = str(instruction)
    return CueContract(
        id=cue_id,
        project_id=project_id,
        cue_type=str(payload.get("cue_type") or payload.get("cueType") or "cue"),
        time_seconds=float(payload.get("time_seconds") or payload.get("time") or 0.0),
        frame=int(payload["frame"]) if payload.get("frame") is not None else None,
        transport="unreal" if payload.get("transport") == "unreal" else "internal",
        target=str(payload["target"]) if payload.get("target") is not None else None,
        payload=cue_payload,
    )
