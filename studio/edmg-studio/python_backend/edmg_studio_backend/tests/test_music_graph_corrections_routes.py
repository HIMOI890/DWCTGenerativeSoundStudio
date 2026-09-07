from __future__ import annotations

from fastapi import FastAPI
from edmg_studio_backend.tests.revision_client import TestClient

from edmg_studio_backend.api.routers import create_project_router
from edmg_studio_backend.store.projects import ProjectStore


def _client_for_store(tmp_path) -> tuple[TestClient, ProjectStore]:
    store = ProjectStore(tmp_path / "data")
    app = FastAPI()
    app.include_router(
        create_project_router(
            get_store=lambda: store,
            project_response=lambda proj: {"project": proj.__dict__},
            assess_health=lambda _pdir, _meta: {"ok": True, "status": "ok"},
        )
    )
    return TestClient(app), store


def test_patch_music_graph_corrections_updates_graph_and_invalidates_plans(tmp_path) -> None:
    test_client, store = _client_for_store(tmp_path)
    proj = store.create("Understand Project")
    proj.meta["analysis"] = {
        "sections": [{"start": 0.0, "end": 4.0, "label": "intro", "energy": 0.4}],
        "features": {"bpm": 120.0, "duration_s": 8.0},
        "beats": [0.0, 0.5],
        "tags": ["pulse"],
        "transcript": {"text": "hello", "segments": [{"start": 0.0, "end": 1.0, "text": "hello"}]},
    }
    proj.meta["last_conductor_plan"] = {"plan_id": "stale"}
    proj.meta["last_conductor_intent"] = {"quality_tier": "balanced"}
    store.save(proj)

    resp = test_client.patch(
        f"/v1/projects/{proj.id}/music_graph/corrections",
        json={
            "sections": [{"start": 0.0, "end": 8.0, "label": "verse", "energy": 0.8}],
            "tempo_bpm": 128.0,
            "semantic_tags": [{"tag": "neon", "confidence": 0.9}],
            "lyrics_lines": [{"start": 0.0, "end": 2.0, "text": "edited line"}],
            "reason": "test_edit",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["music_graph"]["sections"][0]["label"] == "verse"
    assert body["music_graph"]["tempo"]["bpm"] == 128.0
    assert body["invalidation"]["changed"]
    assert "last_conductor_plan" in body["invalidation"]["invalidated"]

    saved = store.get(proj.id)
    assert saved is not None
    assert "last_conductor_plan" not in saved.meta
    assert saved.meta["analysis"]["sections"][0]["label"] == "verse"
