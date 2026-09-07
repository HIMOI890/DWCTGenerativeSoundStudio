from __future__ import annotations

from pathlib import Path

import pytest
from edmg_studio_backend.tests.revision_client import TestClient

from edmg_studio_backend import app as backend_app
from edmg_studio_backend.domain.performer_workflow import build_performer_workflow_plan
from edmg_studio_backend.render_conductor.planner import build_advisory_render_plan
from edmg_studio_backend.schemas import ProjectSnapshot, RenderIntent
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


def test_performer_workflow_plan_routes_performance_scenes() -> None:
    plan = build_performer_workflow_plan(
        project_id="proj-1",
        variant_index=0,
        scenes=[
            {"id": "scene-1", "start_s": 0.0, "end_s": 4.0, "prompt": "lead performer under stage lights"},
            {"id": "scene-2", "start_s": 4.0, "end_s": 8.0, "prompt": "abstract color wash"},
        ],
        music_graph={"schemaVersion": "1.0", "sections": [{"start": 0.0, "end": 4.0, "energy": 0.8}]},
        director_mode="performance",
        environment={"engines": {"hosted_video": {"available": True}}},
    )
    assert plan["tasks"]
    assert plan["tasks"][0]["engine"] == "hosted_video"
    assert plan["tasks"][0]["model"]["repo_id"] == "Wan-AI/Wan2.2-S2V-14B"
    assert plan["tasks"][0]["provenance"]["lane"] == "experimental_high_end"
    assert plan["advisory_only"] is False
    assert plan["execution"]["supports_cancel"] is True


def test_advisory_plan_records_music_graph_diagnostics() -> None:
    snapshot = ProjectSnapshot(
        project_id="proj-123",
        analysis={
            "sections": [{"start": 0.0, "end": 5.0, "label": "intro", "energy": 0.4}],
            "beats": [0.0, 1.0, 2.0],
            "tags": ["pulse"],
        },
        plan={
            "variants": [
                {
                    "scenes": [
                        {"id": "scene-1", "start_s": 0.0, "end_s": 5.0, "prompt": "performer close-up on stage"},
                    ]
                }
            ]
        },
    )
    intent = RenderIntent(project_id="proj-123", variant_index=0)
    environment = {
        "director_mode": "performance",
        "engines": {
            "internal": {"available": True},
            "hosted_video": {"available": True, "quality_score": 0.8, "speed_score": 0.82},
        },
    }
    plan = build_advisory_render_plan(intent, snapshot, environment=environment)
    joined = " ".join(plan.diagnostics)
    assert "music_graph_schema=1.0" in joined
    assert "music_graph_sections=1" in joined
    assert plan.sections[0].engine == "hosted_video"


def test_performer_run_rejects_without_real_adapter(tmp_path: Path, monkeypatch) -> None:
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    project = store.create("Performer queue")
    project.meta["last_performer_plan"] = {
        "plan_id": "performer-test",
        "variant_index": 0,
        "model": {"id": "wan_s2v_14b", "repo_id": "Wan-AI/Wan2.2-S2V-14B"},
        "tasks": [{"scene_id": "scene-1", "audio_window": {"start_s": 0, "end_s": 4}}],
    }
    store.save(project)
    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(backend_app.worker, "start", lambda *args, **kwargs: None)
    monkeypatch.delenv("EDMG_PERFORMER_PROVIDER_URL", raising=False)

    with TestClient(backend_app.app) as client:
        response = client.post(
            f"/v1/projects/{project.id}/render/performer/run",
            json={"variant_index": 0, "plan_id": "performer-test", "provider": "auto"},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PERFORMER_ADAPTER_UNAVAILABLE"
    assert jobs.list_for_project(project.id) == []
    saved = store.get(project.id)
    assert saved is not None
    assert "last_performer_run" not in saved.meta


@pytest.mark.parametrize("provider", ["high_end", "mock"])
def test_performer_run_rejects_explicit_non_real_provider(
    tmp_path: Path,
    monkeypatch,
    provider: str,
) -> None:
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    project = store.create("Performer strict")
    project.meta["last_performer_plan"] = {
        "plan_id": "performer-strict",
        "variant_index": 0,
        "tasks": [{"scene_id": "scene-1"}],
    }
    store.save(project)
    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(backend_app.worker, "start", lambda *args, **kwargs: None)
    monkeypatch.delenv("EDMG_PERFORMER_PROVIDER_URL", raising=False)

    with TestClient(backend_app.app) as client:
        response = client.post(
            f"/v1/projects/{project.id}/render/performer/run",
            json={"provider": provider},
        )

    assert response.status_code == 409
    assert response.json()["error"]["code"] == "PERFORMER_ADAPTER_UNAVAILABLE"
    assert jobs.list_for_project(project.id) == []
    saved = store.get(project.id)
    assert saved is not None
    assert "last_performer_run" not in saved.meta
