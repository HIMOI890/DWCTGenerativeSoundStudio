"""Review/apply boundary for the combined Workspace overview and Director."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..domain.director_scene import DirectorDocument
from ..domain.director_workflow import (
    apply_workflow,
    prepare_workflow,
    reviewed_draft,
    workflow_state,
)
from ..domain.editor_commands import digest
from ..domain.project_time import ProjectClock
from ..domain.workspace_reactive import apply_overrides, review_reactive
from ..revisions import RevisionRoute, record_revision, revision_context
from ..store.projects import StaleProjectRevisionError


class PrepareDirectionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)


class ReviewDirectionRequest(PrepareDirectionRequest):
    draft_id: str = Field(min_length=1, max_length=128)
    document: DirectorDocument | None = None


class ReviewReactiveRequest(PrepareDirectionRequest):
    draft_id: str = Field(min_length=1, max_length=128)
    payload: dict


def create_workflow_router(get_store, plan_builder):
    router = APIRouter(route_class=RevisionRoute, tags=["director workflow"])

    def project(project_id):
        value = get_store().get(project_id)
        if value is None:
            raise HTTPException(404, "Project not found")
        return value

    def mutate(project_id, expected_revision, action):
        try:
            return get_store().mutate(project_id, action, expected_revision=expected_revision)
        except KeyError as exc:
            raise HTTPException(404, "Project not found") from exc
        except ValueError as exc:
            # The store's revision conflict is translated by RevisionRoute.
            if isinstance(exc, StaleProjectRevisionError):
                raise
            raise HTTPException(409, str(exc)) from exc

    @router.get("/v1/projects/{project_id}/director/workflow")
    def read(project_id: str):
        current = project(project_id)
        return {**workflow_state(current), "preparation_error": current.meta.get("director_workflow_error")}

    @router.post("/v1/projects/{project_id}/director/workflow/prepare")
    def prepare(project_id: str, request: PrepareDirectionRequest):
        def action(current):
            prepare_workflow(current, plan_builder, resulting_revision=current.revision + 1)
            current.meta.pop("director_workflow_error", None)
        current = mutate(project_id, request.expected_revision, action)
        return workflow_state(current)

    @router.post("/v1/projects/{project_id}/director/workflow/review")
    def review(project_id: str, request: ReviewDirectionRequest):
        def action(current):
            draft = reviewed_draft(current, request.draft_id, request.document)
            draft.source_revision = current.revision + 1
            draft.draft_id = digest({"source": draft.source_fingerprint,
                                     "document": draft.document.model_dump(mode="json"), "schedule": draft.schedule})
            current.meta["director_workflow"] = draft.model_dump(mode="json")
        current = mutate(project_id, request.expected_revision, action)
        return workflow_state(current)

    @router.post("/v1/projects/{project_id}/director/workflow/apply")
    def apply(project_id: str, request: ReviewDirectionRequest):
        fingerprint = digest(request.model_dump(mode="json", exclude={"expected_revision"}))

        def latest():
            token = revision_context.set(None)
            try:
                return project(project_id)
            finally:
                revision_context.reset(token)

        def replay(current):
            receipts = current.meta.get("director_workflow_receipts") or {}
            receipt = receipts.get(request.draft_id)
            prior = current.meta.get("director_workflow") or {}
            if receipt is not None:
                if receipt != fingerprint:
                    raise HTTPException(409, "This draft was already applied with different content")
            elif prior.get("draft_id") == request.draft_id and prior.get("status") == "applied":
                if request.document is not None and request.document.model_dump(mode="json") != prior["document"]:
                    raise HTTPException(409, "This draft was already applied with different content")
            else:
                return None
            record_revision(project_id, current.revision)
            return {**workflow_state(current), "replayed": True}

        repeated = replay(latest())
        if repeated is not None:
            return repeated

        def action(current):
            draft = reviewed_draft(current, request.draft_id, request.document)
            apply_workflow(current, draft)
            receipts = current.meta.setdefault("director_workflow_receipts", {})
            receipts[request.draft_id] = fingerprint
            while len(receipts) > 200:
                receipts.pop(next(iter(receipts)))
        try:
            current = mutate(project_id, request.expected_revision, action)
        except StaleProjectRevisionError:
            repeated = replay(latest())
            if repeated is not None:
                return repeated
            raise
        return {**workflow_state(current), "replayed": False}

    @router.post("/v1/projects/{project_id}/director/workflow/reactive/review")
    def reactive_review(project_id: str, request: ReviewReactiveRequest):
        def action(current):
            draft = reviewed_draft(current, request.draft_id, None)
            draft.reactive_overrides = review_reactive(draft, request.payload, ProjectClock.from_timeline(current.meta.get("timeline") or {}))
            draft.schedule = apply_overrides(draft.schedule, draft.reactive_overrides)
            draft.source_revision = current.revision + 1
            draft.draft_id = digest({"source": draft.source_fingerprint,
                                     "document": draft.document.model_dump(mode="json"), "schedule": draft.schedule})
            current.meta["director_workflow"] = draft.model_dump(mode="json")
        current = mutate(project_id, request.expected_revision, action)
        return workflow_state(current)

    return router
