from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from edmg_studio_backend.domain.planner_schedule import compile_schedule, apply_schedule, attach_schedule_drafts
from edmg_studio_backend.api.planner_schedule import create_schedule_router
from edmg_studio_backend.store.projects import ProjectStore


def variant():
    return {"duration_s": 4, "scenes": [
        {"id": "a", "start_s": 0, "end_s": 2, "character_lock": "a person in a red coat", "action": "walks across a bridge", "start_state": {"position": "left"}, "end_state": {"position": "center", "hand": "raised"}},
        {"id": "b", "start_s": 2, "end_s": 4, "character_lock": "a person in a red coat", "action": "waves", "start_state": {"position": "wrong"}, "end_state": {"position": "right"}},
    ]}


def draft(**overrides):
    return compile_schedule(**({"project_id": "p", "project_revision": 3, "variant_index": 0,
                               "variant": variant(), "analysis": {}, "fps": 30, "duration_s": 4} | overrides))


def test_draft_is_deterministic_transport_consistent_and_preserves_exact_handoff():
    first = draft()
    assert first == draft()
    assert first["image_anchors"][1]["state"] == first["image_anchors"][2]["state"]
    assert first["prompt_anchors"][1]["frame"] == 60
    assert first["summary"]["scenes"] == 2
    assert len(first["warnings"]) == 3
    for kind in ("prompt_anchors", "image_anchors", "camera_keys", "motion_keys", "markers"):
        assert all(0 <= point["t"] <= 4 and point["frame"] == round(point["t"] * 30) for point in first[kind])


def test_application_preserves_manual_tracks_and_locked_generated_keys():
    source = {"tracks": [{"id": "manual", "type": "prompt", "clips": [{"id": "mine", "data": {"prompt": "keep"}}]}],
              "camera": {"keyframes": [{"id": "user-camera", "t": 1, "zoom": 1.2}]}}
    original = deepcopy(source)
    applied = apply_schedule(source, draft())
    assert source == original
    assert applied["tracks"][0] == original["tracks"][0]
    generated = applied["tracks"][1]["clips"][0]
    generated["locked"] = True
    generated["data"]["prompt"] = "manual refinement"
    again = apply_schedule(applied, draft(project_revision=4))
    assert again["tracks"][1]["clips"][0]["data"]["prompt"] == "manual refinement"
    assert len(again["tracks"]) == len(applied["tracks"])
    assert any(point["id"] == "user-camera" for point in again["camera"]["keyframes"])


def test_schedule_regeneration_is_draft_only_and_stale_approval_is_rejected(tmp_path):
    store = ProjectStore(tmp_path)
    project = store.create("Draft")
    project.meta = {"last_plan": {"variants": [variant()]}, "timeline": {"tracks": [{"id": "user", "clips": []}]}}
    store.save(project)
    app = FastAPI()
    app.include_router(create_schedule_router(lambda: store))
    with TestClient(app) as client:
        url = f"/v1/projects/{project.id}/schedule"
        assert client.post(url + "/regenerate", json={}).status_code == 428
        response = client.post(url + "/regenerate", json={"expected_revision": project.revision}).json()
        schedule = response["schedule_draft"]
        current = store.get(project.id)
        assert current.meta["timeline"] == project.meta["timeline"]
        store.mutate(project.id, lambda p: p.meta.update({"notes": "new"}))
        response = client.post(url + "/apply", json={"expected_revision": current.revision + 1, "schedule_revision": schedule["schedule_revision"]})
        assert response.status_code == 409
        regenerated = client.post(url + "/regenerate", json={"expected_revision": current.revision + 1}).json()
        applied = client.post(url + "/apply", json={"expected_revision": regenerated["revision"], "schedule_revision": regenerated["schedule_draft"]["schedule_revision"]})
        assert applied.status_code == 200, applied.text
        assert applied.json()["timeline"]["tracks"][0]["id"] == "user"


@pytest.mark.parametrize("fps,duration", [(0, 4), (float("inf"), 4), (24, float("nan"))])
def test_invalid_transport_rejected(fps, duration):
    with pytest.raises(ValueError):
        draft(fps=fps, duration_s=duration)
