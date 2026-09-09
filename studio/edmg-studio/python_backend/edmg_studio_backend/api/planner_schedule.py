from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..domain.director_workflow import apply_workflow, reviewed_draft, workflow_state
from ..domain.planner_schedule import apply_schedule, attach_schedule_drafts
from ..revisions import RevisionRoute


class ScheduleRequest(BaseModel):
    variant_index: int = Field(default=0, ge=0)
    expected_revision: int | None = Field(default=None, ge=1)
    schedule_revision: str | None = None


def create_schedule_router(get_store):
    router = APIRouter(route_class=RevisionRoute, tags=["planner schedule"])

    def selected(project_id, variant_index):
        project = get_store().get(project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        variants = (project.meta.get("last_plan") or {}).get("variants") or []
        if variant_index < 0 or variant_index >= len(variants):
            raise HTTPException(400, "Select a valid plan variant first")
        return project, variants[variant_index]

    @router.get("/v1/projects/{project_id}/schedule")
    def read(project_id: str, variant_index: int = 0):
        project, variant = selected(project_id, variant_index)
        workflow = workflow_state(project)
        if workflow.get("draft") and workflow["draft"]["variant_index"] == variant_index:
            return {"ok": True, "revision": project.revision, "schedule_draft": workflow["draft"]["schedule"],
                    "workflow_status": workflow["status"],
                    "approved_schedule": (project.meta.get("timeline") or {}).get("approved_schedule")}
        if not variant.get("schedule_draft"):
            # Legacy adaptation is read-only until regeneration or approval.
            try:
                attach_schedule_drafts(project, resulting_revision=project.revision, variant_indices={variant_index})
            except ValueError as exc:
                raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "revision": project.revision, "schedule_draft": variant["schedule_draft"],
                "approved_schedule": (project.meta.get("timeline") or {}).get("approved_schedule")}

    @router.post("/v1/projects/{project_id}/schedule/regenerate")
    def regenerate(project_id: str, req: ScheduleRequest):
        project, variant = selected(project_id, req.variant_index)
        try:
            attach_schedule_drafts(project, resulting_revision=project.revision + 1, variant_indices={req.variant_index})
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        get_store().save(project)
        return {"ok": True, "revision": project.revision, "schedule_draft": variant["schedule_draft"]}

    @router.post("/v1/projects/{project_id}/schedule/apply")
    def approve(project_id: str, req: ScheduleRequest):
        project, variant = selected(project_id, req.variant_index)
        if not variant.get("schedule_draft"):
            attach_schedule_drafts(project, resulting_revision=project.revision, variant_indices={req.variant_index})
        draft = variant["schedule_draft"]
        if not req.schedule_revision or req.schedule_revision != draft["schedule_revision"]:
            raise HTTPException(409, {"code": "SCHEDULE_REVISION_CONFLICT", "message": "Reload and review the current schedule draft before applying."})
        if draft["source_project_revision"] != project.revision:
            raise HTTPException(409, {"code": "SCHEDULE_SOURCE_STALE", "message": "The project changed after this draft. Regenerate and review the draft before applying.",
                                      "expected_revision": draft["source_project_revision"], "current_revision": project.revision})
        try:
            workflow = workflow_state(project)
            shared = workflow.get("draft") or {}
            if shared.get("variant_index") == req.variant_index and shared.get("schedule", {}).get("schedule_revision") == req.schedule_revision:
                apply_workflow(project, reviewed_draft(project, shared["draft_id"], None))
            else:
                project.meta["timeline"] = apply_schedule(project.meta.get("timeline") or {}, draft)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        get_store().save(project)
        return {"ok": True, "revision": project.revision, "timeline": project.meta["timeline"]}

    return router
