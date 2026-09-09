"""Exercise the connected Workspace through the real application routes.

Only analysis/transcription are substituted. Planning, scheduling, projections,
revision checks, persistence, and application use the production services.
"""

import json
from copy import deepcopy
from fractions import Fraction

import pytest
from fastapi.testclient import TestClient

from edmg_studio_backend import app as backend_app
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    project = store.create("Connected music workspace")
    project.meta["audio"] = {"filename": "music.wav", "duration_s": 8}
    project.meta["timeline"] = {
        "timebase": {"sample_rate": 44100, "frame_rate": {"numerator": 30000, "denominator": 1001}},
        "tracks": [{"id": "manual-audio", "type": "audio", "locked": True, "clips": []}],
        "markers": [{"id": "manual-cue", "t": 1.25, "label": "Keep this cue"}],
        "camera": {"keyframes": [{"id": "manual-camera", "t": 1.25, "zoom": 1.37}]},
    }
    store.save(project)
    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    features = {
        "duration_s": 8, "sample_rate": 44100, "bpm": 120,
        "beat_times": [index / 2 for index in range(16)],
        "rms_energy": [.2, .4, .9, .3, .5, .8, .4, .2],
        "energy_curve": [.2, .4, .9, .3, .5, .8, .4, .2],
    }
    monkeypatch.setattr(backend_app, "_collect_audio_analysis_features", lambda _: deepcopy(features))
    monkeypatch.setattr(backend_app, "_prepare_transcription_audio", lambda path, *_: (path, {}))
    monkeypatch.setattr(backend_app.ai, "transcribe", lambda *_args, **_kwargs: {"text": "We travel home into the dawn"})
    monkeypatch.setattr(backend_app.ai, "plan", lambda *_: pytest.fail("Automatic preparation must not submit a provider job"))
    # No lifespan means no worker or model startup; the full app/router remains in use.
    client = TestClient(backend_app.app)
    try:
        yield client, store, jobs, project, features
    finally:
        client.close()
        jobs.close()


def _post(workspace, suffix, body=None, *, expected_revision=None):
    client, store, _, project, _ = workspace
    revision = expected_revision if expected_revision is not None else store.get(project.id).revision
    response = client.post(f"/v1/projects/{project.id}{suffix}", headers={"If-Match": str(revision)}, json=body)
    assert response.status_code == 200, response.text
    return response.json()


def _workflow(workspace):
    client, _, _, project, _ = workspace
    response = client.get(f"/v1/projects/{project.id}/director/workflow")
    assert response.status_code == 200, response.text
    return response.json()


def _analyze(workspace):
    response = _post(workspace, "/analyze_audio")
    assert response["direction_prepared"], response
    result = _workflow(workspace)
    assert result["status"] == "draft", result
    return result


def _review_reactive(workspace, state, payload):
    return _post(workspace, "/director/workflow/reactive/review", {
        "expected_revision": state["revision"], "draft_id": state["draft"]["draft_id"], "payload": payload,
    })


def _apply(workspace, state):
    return _post(workspace, "/director/workflow/apply", {
        "expected_revision": state["revision"], "draft_id": state["draft"]["draft_id"],
    })


def test_audio_analysis_automatically_prepares_all_workspace_views_without_applying(workspace):
    _, store, jobs, project, _ = workspace
    timeline = deepcopy(project.meta["timeline"])
    state = _analyze(workspace)
    draft, reactive = state["draft"], state["reactive"]

    assert draft["document"]["scenes"]
    assert state["plan"]["variants"][0]["scenes"]
    assert reactive["keyframes"] and reactive["beat_markers"] and reactive["schedules"]["strength"]
    assert reactive["metadata"]["workflow_draft_id"] == draft["draft_id"]
    assert reactive["metadata"]["schedule_revision"] == draft["schedule"]["schedule_revision"]
    assert reactive["metadata"]["analysis_revision"] == draft["document"]["analysis_revision"] == 1
    assert reactive["metadata"]["sample_rate"] == 44100
    assert reactive["metadata"]["frame_rate"] == {"numerator": 30000, "denominator": 1001}
    assert max(int(scene["end_sample"]) for scene in draft["document"]["scenes"]) == 352800
    for point in reactive["keyframes"]:
        samples = Fraction(str(point["time"])) * 44100
        assert isinstance(point["sample"], str)
        assert int(point["sample"]) == int(samples + Fraction(1, 2))

    loaded = ProjectStore(store.base_dir).get(project.id)
    assert loaded.meta["timeline"] == timeline
    assert not loaded.meta.get("director_document")
    assert not loaded.meta.get("last_plan")
    assert not draft["provenance"]["inference"]
    assert jobs.list_for_project(project.id) == []
    snapshot = store.project_dir(project.id) / loaded.meta["analysis"]["analysis_path"]
    assert json.loads(snapshot.read_text(encoding="utf-8"))["revision"] == 1
    assert _workflow(workspace) == state


def test_planner_generation_and_scene_edit_refresh_reactive_without_extra_sync(workspace):
    _, store, _, project, _ = workspace
    initial = _analyze(workspace)
    before = deepcopy(store.get(project.id).meta["timeline"])
    _post(workspace, "/plan?mode=local", {"num_variants": 2, "max_scenes": 2})
    planned = _workflow(workspace)
    assert planned["status"] == "draft"
    assert planned["draft"]["draft_id"] != initial["draft"]["draft_id"]
    scenes = deepcopy(store.get(project.id).meta["last_plan"]["variants"][1]["scenes"])
    scenes[0]["prompt"] = "A red fox climbs a silver tree"
    scenes[0]["action"] = "climbs a silver tree"
    alternate = deepcopy(store.get(project.id).meta["last_plan"]["variants"][0])
    _post(workspace, "/plan/variant", {"variant_index": 1, "scenes": scenes})
    edited = _workflow(workspace)
    assert edited["status"] == "draft"
    assert edited["draft"]["draft_id"] != planned["draft"]["draft_id"]
    assert edited["reactive"]["metadata"]["selected_variant_index"] == 1
    assert "red fox" in edited["draft"]["document"]["scenes"][0]["intent"]
    assert "silver tree" in " ".join(edited["draft"]["document"]["scenes"][0]["actions"])
    assert edited["reactive"]["metadata"]["workflow_draft_id"] == edited["draft"]["draft_id"]
    assert store.get(project.id).meta["timeline"] == before
    _apply(workspace, edited)
    assert store.get(project.id).meta["last_plan"]["variants"][0] == alternate


def test_ai_planner_import_automatically_populates_direction_and_reactive(workspace):
    _, store, _, project, _ = workspace
    previous = _analyze(workspace)
    before = deepcopy(store.get(project.id).meta["timeline"])
    _post(workspace, "/planner_lab/import", {
        "analysis": {"basicInfo": {"durationSeconds": 8, "tempo": 135},
                     "themes": [{"theme": "Copper forest"}], "energyCurve": [.1, .8, .2]},
        "plan": {"scenes": [{"id": 1, "text": "A traveler enters the copper forest",
                              "action": "walks beside the river", "camera": "slow tracking shot"}],
                 "scenePlan": [{"id": 1, "startTime": "00:00", "endTime": "00:08"}]},
        "settings": {"promptStyle": "cinematic"}, "apply_timeline": False,
    })
    state = _workflow(workspace)
    assert state["status"] == "draft"
    assert state["draft"]["draft_id"] != previous["draft"]["draft_id"]
    assert "copper forest" in state["draft"]["document"]["scenes"][0]["intent"]
    assert state["reactive"]["keyframes"] and state["reactive"]["cue_events"]
    assert state["reactive"]["sections"][0]["end_sample"] == "352800"
    assert store.get(project.id).meta["timeline"] == before


def test_reactive_value_refinements_survive_reanalysis_and_common_apply(workspace):
    _, store, _, project, features = workspace
    state = _analyze(workspace)
    before = deepcopy(store.get(project.id).meta["timeline"])
    payload = deepcopy(state["reactive"])
    point = payload["keyframes"][0]
    point["strength"], point["zoom"] = .271, 1.731
    point["extension"] = {"keep": "keyframe"}
    payload["extension"] = {"keep": "document"}
    payload["metadata"]["extension"] = {"keep": "metadata"}
    saved = _review_reactive(workspace, state, payload)
    assert store.get(project.id).meta["timeline"] == before
    assert _workflow(workspace)["reactive"]["keyframes"][0]["strength"] == .271
    features["bpm"] = 130
    refreshed = _analyze(workspace)
    assert refreshed["draft"]["draft_id"] != saved["draft"]["draft_id"]
    assert refreshed["reactive"]["metadata"]["analysis_revision"] == 2
    kept = next(item for item in refreshed["reactive"]["keyframes"] if item["id"] == point["id"])
    assert (kept["strength"], kept["zoom"]) == (.271, 1.731)
    assert kept["extension"] == {"keep": "keyframe"}
    assert refreshed["reactive"]["extension"] == {"keep": "document"}
    assert refreshed["reactive"]["metadata"]["extension"] == {"keep": "metadata"}
    assert store.get(project.id).meta["timeline"] == before
    applied = _apply(workspace, refreshed)
    assert applied["status"] == "applied"
    timeline = store.get(project.id).meta["timeline"]
    assert timeline["tracks"][0] == before["tracks"][0]
    assert before["markers"][0] in timeline["markers"]
    assert before["camera"]["keyframes"][0] in timeline["camera"]["keyframes"]
    camera = next(item for item in timeline["camera"]["keyframes"] if item["id"] == kept["camera_id"])
    assert camera["zoom"] == 1.731
    motion = next(point for track in timeline["tracks"] for clip in track["clips"]
                  for point in clip.get("data", {}).get("keyframes", []) if point["id"] == kept["id"])
    assert motion["strength"] == .271
    loaded = ProjectStore(store.base_dir).get(project.id)
    assert loaded.meta["timeline"] == timeline


@pytest.mark.parametrize("field,value", [("time", 1.5), ("sample", "9000"), ("source_id", "other-scene"), ("steps", 2.5), ("strength", 5)])
def test_reactive_review_rejects_detached_timing_and_invalid_values_atomically(workspace, field, value):
    client, store, _, project, _ = workspace
    state = _analyze(workspace)
    before = deepcopy(store.get(project.id).meta)
    payload = deepcopy(state["reactive"])
    payload["keyframes"][0][field] = value
    response = client.post(f"/v1/projects/{project.id}/director/workflow/reactive/review", json={
        "expected_revision": state["revision"], "draft_id": state["draft"]["draft_id"], "payload": payload,
    })
    assert response.status_code in (409, 422), response.text
    current = store.get(project.id)
    assert current.revision == state["revision"]
    assert current.meta == before


def test_replaced_draft_cannot_apply_even_with_latest_project_revision(workspace):
    client, store, _, project, _ = workspace
    old = _analyze(workspace)
    _post(workspace, "/plan?mode=local", {"num_variants": 1, "max_scenes": 2})
    current = store.get(project.id)
    before = deepcopy(current.meta)
    response = client.post(f"/v1/projects/{project.id}/director/workflow/apply", json={
        "expected_revision": current.revision, "draft_id": old["draft"]["draft_id"],
    })
    assert response.status_code == 409, response.text
    assert store.get(project.id).meta == before


def test_saved_director_document_automatically_refreshes_workspace_schedule(workspace):
    _, store, _, project, _ = workspace
    state = _analyze(workspace)
    before = deepcopy(store.get(project.id).meta["timeline"])
    document = deepcopy(state["draft"]["document"])
    document["scenes"][0]["actions"] = ["walks up the copper staircase"]
    document["story_bible"]["project_theme"] = "Homecoming"
    _post(workspace, "/director/document", {"expected_revision": state["revision"], "document": document})
    refreshed = _workflow(workspace)
    assert refreshed["status"] == "draft"
    assert refreshed["draft"]["draft_id"] != state["draft"]["draft_id"]
    assert refreshed["draft"]["document"]["scenes"][0]["actions"] == document["scenes"][0]["actions"]
    assert refreshed["reactive"]["metadata"]["workflow_draft_id"] == refreshed["draft"]["draft_id"]
    assert store.get(project.id).meta["timeline"] == before


def test_accepting_qwen_job_prepares_reactive_draft_without_applying_timeline(workspace):
    _, store, jobs, project, _ = workspace
    state = _analyze(workspace)
    _post(workspace, "/director/document", {
        "expected_revision": state["revision"], "document": state["draft"]["document"],
    })
    current = store.get(project.id)
    baseline = deepcopy(current.meta["director_document"])
    before = deepcopy(current.meta["timeline"])
    job = jobs.create(project.id, "qwen_director", {"document": baseline, "source_revision": current.revision})
    proposal = deepcopy(baseline)
    proposal["scenes"][0]["actions"] = ["reaches toward the lantern"]
    job.status = "succeeded"
    job.result = {"status": "draft", "document": proposal, "provenance": {"test_fixture": True}}
    jobs.save(job)
    prior = _workflow(workspace)
    _post(workspace, f"/director/drafts/{job.id}/apply", {"expected_revision": current.revision})
    refreshed = _workflow(workspace)
    assert refreshed["status"] == "draft"
    assert refreshed["draft"]["draft_id"] != prior["draft"]["draft_id"]
    assert refreshed["draft"]["document"]["scenes"][0]["actions"] == proposal["scenes"][0]["actions"]
    assert refreshed["reactive"]["keyframes"]
    assert store.get(project.id).meta["timeline"] == before
    assert jobs.get(project.id, job.id).result["provenance"] == {"test_fixture": True}
