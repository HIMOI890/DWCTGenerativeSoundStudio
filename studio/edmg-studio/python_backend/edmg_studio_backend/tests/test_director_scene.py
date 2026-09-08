import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError

from edmg_studio_backend.api.director import create_director_router
from edmg_studio_backend.domain.director_scene import (
    DirectorDocument,
    SceneSpec,
    StoryBible,
    compile_scene,
)
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


def scene(**changes):
    return SceneSpec.model_validate(
        {
            "scene_id": "arrival",
            "start_sample": "9007199254740993",
            "end_sample": "9007199255028993",
            "intent": "A traveler enters town",
            "subjects": [{"id": "traveler", "appearance_lock": True}],
            "actions": ["walks across the road", "looks toward the window"],
            "environment": {"secondary_motion": ["fog drifts across the road"]},
            **changes,
        }
    )


def test_compilers_preserve_exact_range_identity_constraints_and_provenance():
    bible = StoryBible(
        characters={"traveler": "A traveler in a charcoal coat"}, forbidden_changes=["coat color"]
    )
    hunyuan = compile_scene(scene(), bible, "hunyuan_video15")
    ltx = compile_scene(scene(), bible, "ltx_25")
    assert hunyuan["prompt"] != ltx["prompt"]
    assert hunyuan["source_hash"] == ltx["source_hash"]
    assert hunyuan["start_sample"] == "9007199254740993"
    assert hunyuan["status"] == "prepared"
    for package in [hunyuan, ltx]:
        for expected in ["charcoal coat", "fog drifts", "coat color", "Preserve appearance"]:
            assert expected in package["prompt"]
    changed = compile_scene(scene(intent="Different scene"), bible, "hunyuan_video15")
    assert changed["source_hash"] != hunyuan["source_hash"]


@pytest.mark.parametrize(
    "changes",
    [
        {"end_sample": "0"},
        {"start_sample": "9223372036854775808"},
        {"start_sample": 9007199254740993},
        {"camera": {"motion_strength": 2}},
        {"subjects": [{"id": "x"}, {"id": "x"}]},
    ],
)
def test_scene_rejects_invalid_timing_and_structure(changes):
    with pytest.raises((ValidationError, ValueError)):
        scene(**changes)


def test_duplicate_scene_ids_rejected():
    with pytest.raises(ValidationError):
        DirectorDocument(scenes=[scene(), scene()])


def test_project_direction_roundtrip_compilation_and_revision_conflict(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("Director")
    app = FastAPI()
    app.include_router(create_director_router(lambda: store))
    document = DirectorDocument(scenes=[scene()]).model_dump(mode="json")
    document["extension"] = {"future": True}
    document["story_bible"]["project_theme"] = "Arrival"
    with TestClient(app) as client:
        path = f"/v1/projects/{project.id}/director"
        initial = client.get(path + "/document").json()
        result = client.post(
            path + "/document",
            json={"expected_revision": initial["revision"], "document": document},
        )
        assert result.status_code == 200, result.text
        saved = result.json()
        assert saved["document"]["story_bible"]["revision"] == 2
        assert saved["document"]["extension"] == {"future": True}
        assert (
            client.post(
                path + "/document",
                json={"expected_revision": initial["revision"], "document": document},
            ).status_code
            == 409
        )
        assert client.get(path + "/document").json() == saved
        compiled = client.get(path + "/prompts?engine=ltx_25")
        assert compiled.status_code == 200
        assert compiled.json()["packages"][0]["scene_id"] == "arrival"
        assert store.get(project.id).revision == saved["revision"]
        assert client.get(path + "/prompts?engine=invalid").status_code == 422
    loaded = ProjectStore(tmp_path).get(project.id)
    assert loaded.meta["director_document"]["scenes"][0]["start_sample"] == "9007199254740993"


def test_director_queue_is_idempotent_and_never_applies_draft(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Queued Director")
    project.meta["director_document"] = DirectorDocument(scenes=[scene()]).model_dump(mode="json")
    store.save(project)
    jobs = JobStore(tmp_path / "projects")

    class Models:
        available = True

        def installed_path(self, model_id):
            return tmp_path if self.available else None

    models = Models()
    app = FastAPI()
    app.include_router(create_director_router(lambda: store, lambda: jobs, lambda: models))
    with TestClient(app) as client:
        path = f"/v1/projects/{project.id}/director/generate"
        body = {
            "expected_revision": store.get(project.id).revision,
            "operation_id": "direction-1",
            "instruction": "Add more motion",
        }
        first = client.post(path, json=body)
        assert first.status_code == 200, first.text
        assert client.post(path, json=body).json()["job_id"] == first.json()["job_id"]
        assert (
            client.post(path, json={**body, "instruction": "Different direction"}).status_code
            == 409
        )
        assert store.get(project.id).revision == body["expected_revision"]
        job = jobs.get(project.id, first.json()["job_id"])
        assert job.type == "qwen_director"
        assert job.payload["document"] == project.meta["director_document"]
        models.available = False
        assert client.post(path, json={**body, "operation_id": "direction-2"}).status_code == 422


def test_reviewed_draft_apply_checks_baseline_and_preserves_job(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Draft review")
    baseline = DirectorDocument(scenes=[scene()])
    project.meta["director_document"] = baseline.model_dump(mode="json")
    store.save(project)
    jobs = JobStore(tmp_path / "projects")
    job = jobs.create(
        project.id,
        "qwen_director",
        {
            "document": baseline.model_dump(mode="json"),
            "source_revision": store.get(project.id).revision,
        },
    )
    proposal = baseline.model_copy(deep=True)
    proposal.scenes[0].actions = ["walks slowly toward the window"]
    job.result = {
        "status": "draft",
        "document": proposal.model_dump(mode="json"),
        "provenance": {"test_fixture": True},
    }
    job.status = "succeeded"
    jobs.save(job)
    app = FastAPI()
    app.include_router(create_director_router(lambda: store, lambda: jobs))
    with TestClient(app) as client:
        path = f"/v1/projects/{project.id}/director/drafts/{job.id}"
        assert client.get(path).json()["result"]["status"] == "draft"
        revision = store.get(project.id).revision
        result = client.post(path + "/apply", json={"expected_revision": revision})
        assert result.status_code == 200, result.text
        assert result.json()["document"]["scenes"][0]["actions"] == proposal.scenes[0].actions
        # Retrying against a freshly loaded revision must not overwrite newer direction.
        assert (
            client.post(
                path + "/apply", json={"expected_revision": result.json()["revision"]}
            ).status_code
            == 409
        )
        assert jobs.get(project.id, job.id).result == job.result
        assert store.get(project.id).meta["director_applied_job"]["job_id"] == job.id
