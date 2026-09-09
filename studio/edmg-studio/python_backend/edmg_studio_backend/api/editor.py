"""Revision-checked persistent editor commands, independent of either desktop UI."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..domain.editor_commands import (
    EditorConflict,
    digest,
    execute,
    history_state,
    normalize_timeline,
)
from ..revisions import RevisionRoute, record_revision, revision_context
from ..store.projects import StaleProjectRevisionError
from .media import validate_timeline_media


class EditorCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(min_length=1, max_length=128)
    expected_revision: int = Field(ge=1, strict=True)
    action: Literal["edit", "replace", "undo", "redo"]
    label: str = Field(default="Timeline edit", max_length=200)
    operations: list[dict[str, Any]] = Field(default_factory=list, max_length=200)
    timeline: dict[str, Any] | None = None


def create_editor_router(get_store):
    router = APIRouter(route_class=RevisionRoute, tags=["editor"])

    def state(project):
        return {
            "ok": True,
            "revision": project.revision,
            "timeline": normalize_timeline(project.meta.get("timeline") or {}),
            "history": history_state(project.meta),
        }

    @router.get("/v1/projects/{project_id}/editor")
    def read(project_id: str):
        project = get_store().get(project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        try:
            return state(project)
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.post("/v1/projects/{project_id}/editor/commands")
    def command(project_id: str, request: EditorCommandRequest):
        store = get_store()
        payload = request.model_dump()
        fingerprint = digest({k: v for k, v in payload.items() if k != "expected_revision"})

        def current_project():
            # Inspect committed receipts before enforcing the original revision.
            # The actual mutation still uses the request's compare-and-set revision.
            token = revision_context.set(None)
            try:
                return store.get(project_id)
            finally:
                revision_context.reset(token)

        def replay(project):
            receipt = (
                (project.meta.get("editor_history") or {})
                .get("receipts", {})
                .get(request.operation_id)
            )
            if receipt is None:
                return None
            if receipt != fingerprint:
                raise HTTPException(
                    409,
                    {
                        "code": "EDITOR_OPERATION_CONFLICT",
                        "message": "Operation ID already used for different content",
                    },
                )
            record_revision(project_id, project.revision)
            return {**state(project), "replayed": True}

        project = current_project()
        if project is None:
            raise HTTPException(404, "Project not found")
        repeated = replay(project)
        if repeated is not None:
            return repeated

        def apply(project):
            execute(project.meta, payload)
            validate_timeline_media(store.project_dir(project_id), project.meta["timeline"])

        try:
            project = store.mutate(project_id, apply, expected_revision=request.expected_revision)
            return {**state(project), "replayed": False}
        except (StaleProjectRevisionError, EditorConflict) as exc:
            # Recheck under contention: an identical concurrent submission may have won.
            current = current_project()
            if current is not None:
                repeated = replay(current)
                if repeated is not None:
                    return repeated
            if isinstance(exc, StaleProjectRevisionError):
                raise
            raise HTTPException(
                409, {"code": "EDITOR_HISTORY_CONFLICT", "message": str(exc)}
            ) from exc
        except KeyError as exc:
            raise HTTPException(404, "Project not found") from exc
        except (ValueError, TypeError, ZeroDivisionError) as exc:
            raise HTTPException(422, str(exc)) from exc

    return router
