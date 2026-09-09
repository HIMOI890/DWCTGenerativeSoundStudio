"""Project-owned Director state; preparing prompts never submits generation."""

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from ..domain.director_readiness import (
    HIGH_TIER_DIRECTOR_MODEL_ID,
    HUNYUAN_MODEL_ID,
    LTX_MODEL_ID,
    STANDARD_DIRECTOR_MODEL_ID,
    resolve_director_readiness,
)
from ..domain.director_scene import DirectorDocument, compile_scene
from ..domain.director_workflow import prepare_workflow
from ..revisions import RevisionRoute
from ..services.engine_packages import HIGH_GGUF_ID, STANDARD_GGUF_ID
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
    mode: Literal["automatic", "fast", "quality", "maximum"] = "automatic"
    renderer_engine: str = Field(default="automatic", min_length=1, max_length=80)
    allow_external: bool = False


class DirectorApplyRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    expected_revision: int = Field(ge=1, strict=True)


def create_director_router(get_store, get_jobs=None, get_models=None, get_hardware=None):
    router = APIRouter(route_class=RevisionRoute, tags=["director"])

    def installed_models() -> dict[str, bool]:
        """Return install/cache availability without loading any weights."""

        model_ids = (
            STANDARD_DIRECTOR_MODEL_ID,
            HIGH_TIER_DIRECTOR_MODEL_ID,
            HUNYUAN_MODEL_ID,
            LTX_MODEL_ID,
            STANDARD_GGUF_ID,
            HIGH_GGUF_ID,
        )
        service = get_models() if get_models is not None else None
        if service is None:
            return {model_id: False for model_id in model_ids}
        available: dict[str, bool] = {}
        try:
            catalog = service.catalog()
            if isinstance(catalog, dict) and isinstance(catalog.get("installed"), dict):
                available.update(
                    {
                        str(model_id): bool(value)
                        for model_id, value in catalog["installed"].items()
                        if str(model_id) in model_ids
                    }
                )
        except Exception:
            # Readiness is diagnostic; an unavailable catalog must not take the
            # project document or editor offline.
            pass
        for model_id in model_ids:
            if model_id in available and available[model_id]:
                continue
            try:
                available[model_id] = bool(service.installed_path(model_id))
            except Exception:
                available[model_id] = False
        return available

    def hardware_profile() -> dict:
        if get_hardware is None:
            return {}
        try:
            value = get_hardware()
            return dict(value or {})
        except Exception:
            # A readiness card should show a blocked/unknown result rather than
            # make the Workspace unusable when a platform probe fails.
            return {}

    @router.get("/v1/projects/{project_id}/director/readiness")
    def readiness(
        project_id: str,
        mode: str = "automatic",
        engine: str = "automatic",
        allow_external: bool = False,
    ):
        project = get_store().get(project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        try:
            result = resolve_director_readiness(
                hardware_profile(),
                mode=mode,
                engine=engine,
                installed_models=installed_models(),
                allow_external=allow_external,
            )
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        payload = result.model_dump(mode="json")
        payload["project_id"] = project_id
        payload["project_revision"] = project.revision
        return {"ok": True, **payload}

    @router.post("/v1/projects/{project_id}/director/generate")
    def generate(project_id: str, request: DirectorGenerationRequest):
        if get_jobs is None or get_models is None:
            raise HTTPException(503, "Director job services are unavailable")
        project = get_store().get(project_id)
        if project is None:
            raise HTTPException(404, "Project not found")
        if project.revision != request.expected_revision:
            raise HTTPException(409, "Project changed; refresh direction before generating")
        model_id = STANDARD_DIRECTOR_MODEL_ID
        readiness_snapshot = None
        if get_hardware is not None:
            try:
                readiness = resolve_director_readiness(
                    hardware_profile(),
                    mode=request.mode,
                    engine=request.renderer_engine,
                    installed_models=installed_models(),
                    allow_external=request.allow_external,
                )
            except ValueError as exc:
                raise HTTPException(422, str(exc)) from exc
            if not readiness.director.ready:
                raise HTTPException(
                    422,
                    {
                        "message": readiness.director.reason,
                        "hint": " ".join(readiness.blockers or readiness.actions),
                        "code": "DIRECTOR_NOT_READY",
                    },
                )
            model_id = readiness.director.model_id
            readiness_snapshot = {
                "mode": readiness.requested_mode,
                "renderer_engine": readiness.requested_engine,
                "director": readiness.director.model_dump(mode="json"),
                "renderer": readiness.renderer.model_dump(mode="json"),
                "hardware_tier": readiness.hardware_tier,
            }
        if get_models().installed_path(model_id) is None:
            raise HTTPException(
                422, f"Install the resolved Director model ({model_id}) in Models before generating direction"
            )
        document = DirectorDocument.model_validate(project.meta.get("director_document") or {})
        if not document.scenes:
            raise HTTPException(422, "Save at least one scene range before generating direction")
        payload = {
            "document": document.model_dump(mode="json"),
            "instruction": request.instruction,
            "source_revision": project.revision,
            "model_id": model_id,
            "mode": request.mode,
            "renderer_engine": request.renderer_engine,
            "allow_external": request.allow_external,
        }
        if readiness_snapshot is not None:
            payload["readiness"] = readiness_snapshot
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
            "progress": job.progress,
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
            prepare_workflow(project, lambda _: project.meta.get("last_plan") or {},
                             resulting_revision=project.revision + 1, source="director")

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
            if document.scenes:
                prepare_workflow(project, lambda _: project.meta.get("last_plan") or {},
                                 resulting_revision=project.revision + 1, source="director")

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
