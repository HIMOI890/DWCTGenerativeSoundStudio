from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from edmg_studio_backend.api.director_workflow import create_workflow_router
from edmg_studio_backend.domain.director_workflow import (
    apply_workflow,
    prepare_workflow,
    reviewed_draft,
    workflow_state,
)
from edmg_studio_backend.domain.editor_commands import execute
from edmg_studio_backend.store.projects import ProjectStore


def local_plan(project):
    return {"variants": [{"scenes": [{"id": "arrival", "start_s": 0, "end_s": 4,
                                      "prompt": "A traveler crosses a forest", "extension": {"kept": True}}]}]}


@pytest.fixture
def state(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Forest music")
    project.meta["analysis"] = {"revision": 1, "duration_s": 4, "tags": ["cinematic"],
                                "features": {"bpm": 120, "rms_energy": [.2, .8, .3]},
                                "transcript": {"text": "We travel home"}}
    project.meta["timeline"] = {"timebase": {"sample_rate": 44100, "frame_rate": {"numerator": 30000, "denominator": 1001}},
                                "tracks": [{"id": "user-track", "type": "audio", "locked": True, "clips": []}],
                                "markers": [{"id": "user-marker", "t": 1, "label": "keep"}]}
    store.save(project)
    return store, project


def test_prepare_is_automatic_draft_only_and_uses_project_clock(state):
    store, project = state
    before = deepcopy(project.meta)
    draft = prepare_workflow(project, local_plan, resulting_revision=project.revision + 1)
    assert {k: project.meta[k] for k in before} == before
    assert "last_plan" not in project.meta and "director_document" not in project.meta
    assert draft.document.scenes[0].end_sample == "176400"
    assert draft.schedule["transport"]["fps"] == 30000 / 1001
    assert draft.schedule["motion_keys"] and draft.schedule["camera_keys"]
    assert not draft.provenance["inference"]
    store.save(project)
    assert workflow_state(store.get(project.id))["status"] == "draft"


def test_review_apply_preserves_user_content_alternates_and_undo(state):
    store, project = state
    project.meta["last_plan"] = {"variants": [local_plan(project)["variants"][0], {"name": "alternate", "scenes": []}]}
    before = deepcopy(project.meta["timeline"])
    draft = prepare_workflow(project, local_plan, resulting_revision=project.revision + 1)
    document = draft.document.model_copy(deep=True)
    document.scenes[0].actions = ["walks toward the river"]
    draft = reviewed_draft(project, draft.draft_id, document)
    apply_workflow(project, draft)
    store.save(project)
    loaded = store.get(project.id)
    assert loaded.meta["timeline"]["tracks"][0]["locked"]
    assert loaded.meta["timeline"]["markers"][0]["id"] == "planner:0:arrival:scene:0"
    assert any(marker["id"] == "user-marker" for marker in loaded.meta["timeline"]["markers"])
    assert loaded.meta["last_plan"]["variants"][1]["name"] == "alternate"
    assert loaded.meta["last_plan"]["variants"][0]["scenes"][0]["extension"] == {"kept": True}
    assert "walks toward the river" in loaded.meta["last_plan"]["variants"][0]["scenes"][0]["prompt"]
    execute(loaded.meta, {"operation_id": "undo-apply", "action": "undo"})
    assert loaded.meta["timeline"] == before


def test_reanalysis_preserves_approved_document_and_rejects_timing_or_lock_changes(state):
    _, project = state
    project.meta["last_plan"] = local_plan(project)
    project.meta["last_plan"]["variants"][0]["scenes"][0]["character_lock"] = "red coat"
    draft = prepare_workflow(project, local_plan, resulting_revision=project.revision + 1)
    apply_workflow(project, draft)
    saved = deepcopy(project.meta["director_document"])
    project.meta["analysis"]["revision"] = 2
    project.meta["analysis"]["features"]["bpm"] = 130
    new = prepare_workflow(project, lambda _: pytest.fail("Approved scenes must be preserved"), resulting_revision=project.revision + 1)
    assert project.meta["director_document"] == saved
    assert new.document.scenes[0].model_dump() == saved["scenes"][0]
    assert new.document.analysis_revision == 2
    timing = new.document.model_copy(deep=True)
    timing.scenes[0].end_sample = "999999"
    with pytest.raises(ValueError, match="timing"):
        reviewed_draft(project, new.draft_id, timing)
    identity = new.document.model_copy(deep=True)
    identity.scenes[0].subjects[0].appearance_notes = ["blue coat"]
    with pytest.raises(ValueError, match="appearances"):
        reviewed_draft(project, new.draft_id, identity)


def test_workflow_api_revisions_review_reload_and_apply_replay(state):
    store, project = state
    app = FastAPI()
    app.include_router(create_workflow_router(lambda: store, local_plan))
    with TestClient(app) as client:
        path = f"/v1/projects/{project.id}/director/workflow"
        prepared = client.post(path + "/prepare", json={"expected_revision": project.revision})
        assert prepared.status_code == 200, prepared.text
        current = prepared.json()
        document = current["draft"]["document"]
        document["scenes"][0]["intent"] = "The traveler walks into the dawn"
        reviewed = client.post(path + "/review", json={"expected_revision": current["revision"], "draft_id": current["draft"]["draft_id"], "document": document})
        assert reviewed.status_code == 200, reviewed.text
        assert "timeline" not in reviewed.json()["draft"]["document"]
        assert client.get(path).json()["draft"]["document"]["scenes"][0]["intent"] == document["scenes"][0]["intent"]
        body = {"expected_revision": reviewed.json()["revision"], "draft_id": reviewed.json()["draft"]["draft_id"]}
        applied = client.post(path + "/apply", json=body)
        assert applied.status_code == 200, applied.text
        assert applied.json()["status"] == "applied"
        replay = client.post(path + "/apply", json=body)
        assert replay.status_code == 200, replay.text
        assert replay.json()["replayed"]
        assert replay.json()["revision"] == applied.json()["revision"]


def test_source_changes_and_stale_client_cannot_apply(state):
    store, project = state
    app = FastAPI()
    app.include_router(create_workflow_router(lambda: store, local_plan))
    with TestClient(app) as client:
        path = f"/v1/projects/{project.id}/director/workflow"
        prepared = client.post(path + "/prepare", json={"expected_revision": project.revision}).json()
        store.mutate(project.id, lambda value: value.meta["analysis"].update(revision=2))
        assert client.get(path).json()["status"] == "stale"
        body = {"expected_revision": prepared["revision"], "draft_id": prepared["draft"]["draft_id"]}
        assert client.post(path + "/apply", json=body).status_code == 409
        body["expected_revision"] = store.get(project.id).revision
        assert client.post(path + "/apply", json=body).status_code == 409
        assert store.get(project.id).meta["timeline"]["tracks"][0]["id"] == "user-track"
