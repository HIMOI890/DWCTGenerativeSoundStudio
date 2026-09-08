"""Project-owned Director state; preparing prompts never submits generation."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..domain.director_scene import DirectorDocument, compile_scene
from ..revisions import RevisionRoute
from ..services.qwen_director import validate_proposal


class DirectorUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    document: DirectorDocument


class DirectorGenerationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)
    operation_id: str = Field(min_length=1, max_length=128)
    instruction: str = Field(min_length=1, max_length=16000)


class DirectorApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)


def create_director_router(get_store, get_jobs=None, get_models=None):
    router = APIRouter(route_class=RevisionRoute, tags=["director"])

    @router.post("/v1/projects/{project_id}/director/generate")
    def generate(project_id: str, request: DirectorGenerationRequest):
        if get_jobs is None or get_models is None:
            raise HTTPException(503, "Director job services are unavailable")
        project = get_store().get(project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if project.revision != request.expected_revision:
            raise HTTPException(409, "Project changed; refresh direction before generating")
        model_id = "hf_qwen3_vl_8b_director"
        if get_models().installed_path(model_id) is None:
            raise HTTPException(
                422, "Install Qwen3-VL-8B Director in Models before generating direction"
            )
        document = DirectorDocument.model_validate(project.meta.get("director_document") or {})
        if not document.scenes:
            raise HTTPException(422, "Save at least one scene range before generating direction")
        payload = {
            "document": document.model_dump(mode="json"),
            "instruction": request.instruction,
            "source_revision": project.revision,
            "model_id": model_id,
        }
        job = get_jobs().create(
            project_id, "qwen_director", payload, idempotency_key="director:" + request.operation_id
        )
        if job.type != "qwen_director" or job.payload != payload:
            raise HTTPException(409, "Operation ID already used for different direction")
        return {"ok": True, "job_id": job.id, "status": job.status, "output_policy": "draft"}

    def response(project):
        document = DirectorDocument.model_validate(project.meta.get("director_document") or {})
        return {
            "ok": True,
            "revision": project.revision,
            "document": document.model_dump(mode="json"),
        }

    def director_job(project_id, job_id):
        if get_jobs is None:
            raise HTTPException(503, "Director job services are unavailable")
        job = get_jobs().get(project_id, job_id)
        if job is None or job.type != "qwen_director":
            raise HTTPException(404, "Director job not found")
        return job

    @router.get("/v1/projects/{project_id}/director/drafts/{job_id}")
    def draft(project_id: str, job_id: str):
        job = director_job(project_id, job_id)
        return {
            "ok": True,
            "job_id": job.id,
            "status": job.status,
            "error": job.error,
            "result": job.result if job.status == "succeeded" else None,
        }

    @router.post("/v1/projects/{project_id}/director/drafts/{job_id}/apply")
    def apply_draft(project_id: str, job_id: str, request: DirectorApplyRequest):
        job = director_job(project_id, job_id)
        if job.status != "succeeded" or not job.result or job.result.get("status") != "draft":
            raise HTTPException(409, "Director draft is not ready for review and application")
        baseline = DirectorDocument.model_validate(job.payload["document"])
        import json

        try:
            proposal = validate_proposal(json.dumps(job.result["document"]), baseline)
        except (ValueError, KeyError) as exc:
            raise HTTPException(
                422, "Director draft violates approved project constraints"
            ) from exc

        def apply(project):
            current = DirectorDocument.model_validate(project.meta.get("director_document") or {})
            if current != baseline:
                raise HTTPException(
                    409,
                    "Direction changed during generation; retain this draft for placement review",
                )
            project.meta["director_document"] = proposal.model_dump(mode="json")
            project.meta["director_applied_job"] = {
                "job_id": job.id,
                "source_revision": job.payload["source_revision"],
                "provenance": job.result.get("provenance", {}),
            }

        try:
            return response(
                get_store().mutate(project_id, apply, expected_revision=request.expected_revision)
            )
        except KeyError as exc:
            raise HTTPException(404, "Project not found") from exc

    @router.get("/v1/projects/{project_id}/director/document")
    def read(project_id: str):
        project = get_store().get(project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        return response(project)

    @router.post("/v1/projects/{project_id}/director/document")
    def update(project_id: str, request: DirectorUpdate):
        def apply(project):
            previous = DirectorDocument.model_validate(project.meta.get("director_document") or {})
            document = request.document.model_copy(deep=True)
            # Revision belongs to the project service, not the client.
            old_bible = previous.story_bible.model_dump(exclude={"revision"})
            new_bible = document.story_bible.model_dump(exclude={"revision"})
            document.story_bible.revision = previous.story_bible.revision + (old_bible != new_bible)
            project.meta["director_document"] = document.model_dump(mode="json")

        try:
            return response(
                get_store().mutate(project_id, apply, expected_revision=request.expected_revision)
            )
        except KeyError as exc:
            raise HTTPException(404, "Project not found") from exc

    @router.get("/v1/projects/{project_id}/director/prompts")
    def prompts(project_id: str, engine: str = "hunyuan_video15"):
        project = get_store().get(project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if engine not in {"hunyuan_video15", "ltx_25", "external"}:
            raise HTTPException(422, "Unsupported prompt compiler")
        document = DirectorDocument.model_validate(project.meta.get("director_document") or {})
        return {
            "ok": True,
            "revision": project.revision,
            "packages": [
                compile_scene(scene, document.story_bible, engine) for scene in document.scenes
            ],
        }

    return router
