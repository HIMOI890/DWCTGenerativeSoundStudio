from __future__ import annotations

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..domain.director_workflow import apply_workflow, prepare_workflow, reviewed_draft, workflow_state
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
        workflow = workflow_state(project)
        shared = workflow.get("draft")
        if shared and shared["variant_index"] != variant_index:
            shared = None
        # Analysis can prepare a shared draft before any plan is approved. The
        # projection also contains reactive refinements absent from last_plan.
        plan = workflow["plan"] if shared else project.meta.get("last_plan") or {}
        variants = plan.get("variants") or []
        if variant_index < 0 or variant_index >= len(variants):
            raise HTTPException(400, "Select a valid plan variant first")
        return project, variants[variant_index], shared

    def current_schedule(project, variant, shared, variant_index):
        if shared:
            return shared["schedule"], shared
        if not variant.get("schedule_draft"):
            attach_schedule_drafts(project, resulting_revision=project.revision, variant_indices={variant_index})
            shared = workflow_state(project).get("draft")
            if shared and shared["variant_index"] == variant_index:
                return shared["schedule"], shared
        return variant["schedule_draft"], None

    @router.get("/v1/projects/{project_id}/schedule")
    def read(project_id: str, variant_index: int = 0):
        project, variant, shared = selected(project_id, variant_index)
        try:
            # Legacy adaptation is read-only until regeneration or approval.
            draft, shared = current_schedule(project, variant, shared, variant_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        return {"ok": True, "revision": project.revision, "schedule_draft": draft,
                **({"workflow_status": workflow_state(project)["status"]} if shared else {}),
                "approved_schedule": (project.meta.get("timeline") or {}).get("approved_schedule")}

    @router.post("/v1/projects/{project_id}/schedule/regenerate")
    def regenerate(project_id: str, req: ScheduleRequest):
        project, variant, shared = selected(project_id, req.variant_index)
        try:
            if shared:
                # Recompile music timing while retaining the reviewed direction
                # and value overrides from the shared draft.
                prepare_workflow(project, lambda current: workflow_state(current)["plan"],
                                 resulting_revision=project.revision + 1, variant_index=req.variant_index)
            else:
                attach_schedule_drafts(project, resulting_revision=project.revision + 1, variant_indices={req.variant_index})
            draft = workflow_state(project)["draft"]["schedule"]
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        get_store().save(project)
        return {"ok": True, "revision": project.revision, "schedule_draft": draft}

    @router.post("/v1/projects/{project_id}/schedule/apply")
    def approve(project_id: str, req: ScheduleRequest):
        project, variant, shared = selected(project_id, req.variant_index)
        try:
            draft, shared = current_schedule(project, variant, shared, req.variant_index)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        if not req.schedule_revision or req.schedule_revision != draft["schedule_revision"]:
            raise HTTPException(409, {"code": "SCHEDULE_REVISION_CONFLICT", "message": "Reload and review the current schedule draft before applying."})
        # Reviews advance the shared draft's source revision without changing
        # the original schedule compilation revision. Legacy variants still use
        # their schedule's source revision.
        source_revision = shared["source_revision"] if shared else draft["source_project_revision"]
        if source_revision != project.revision or (shared and workflow_state(project)["status"] == "stale"):
            raise HTTPException(409, {"code": "SCHEDULE_SOURCE_STALE", "message": "The project changed after this draft. Regenerate and review the draft before applying.",
                                      "expected_revision": source_revision, "current_revision": project.revision})
        try:
            if shared:
                apply_workflow(project, reviewed_draft(project, shared["draft_id"], None))
            else:
                project.meta["timeline"] = apply_schedule(project.meta.get("timeline") or {}, draft)
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        get_store().save(project)
        return {"ok": True, "revision": project.revision, "timeline": project.meta["timeline"]}

    return router
