from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from edmg_studio_backend.api.director import create_director_router
from edmg_studio_backend.domain.director_readiness import (
    HIGH_TIER_DIRECTOR_MODEL_ID,
    HUNYUAN_MODEL_ID,
    LTX_MODEL_ID,
    STANDARD_DIRECTOR_MODEL_ID,
    resolve_director_readiness,
)
from edmg_studio_backend.store.projects import ProjectStore


def test_low_hardware_automatic_resolves_standard_director_and_hunyuan_admission():
    result = resolve_director_readiness(
        {
            "backend": "cuda",
            "device_name": "RTX 4050 Laptop GPU",
            "vram_gb": 6.0,
            "ram_gb": 16.0,
        },
        installed_models={STANDARD_DIRECTOR_MODEL_ID: True},
    )

    assert result.hardware_tier == "low"
    assert result.director.model_id == STANDARD_DIRECTOR_MODEL_ID
    assert result.renderer.model_id == HUNYUAN_MODEL_ID
    assert result.renderer.profile == "low_vram_chunked"
    assert result.director.ready is True
    assert result.ready is False
    assert any("HunyuanVideo-1.5" in blocker for blocker in result.blockers)


def test_quality_on_high_hardware_prefers_ltx_and_high_tier_director_when_available():
    result = resolve_director_readiness(
        {
            "backend": "cuda",
            "device_name": "RTX A6000",
            "vram_gb": 48.0,
            "ram_gb": 64.0,
        },
        mode="quality",
        installed_models={
            STANDARD_DIRECTOR_MODEL_ID: True,
            HIGH_TIER_DIRECTOR_MODEL_ID: True,
            LTX_MODEL_ID: True,
        },
    )

    assert result.hardware_tier == "ultra"
    assert result.director.model_id == HIGH_TIER_DIRECTOR_MODEL_ID
    assert result.renderer.model_id == LTX_MODEL_ID
    assert result.renderer.profile == "maximum_quality"
    assert result.renderer.installed is True
    assert result.director.ready is False
    assert result.ready is False
    assert any("30B-A3B" in blocker for blocker in result.blockers)
    assert any("not release-qualified" in blocker for blocker in result.blockers)


def test_explicit_external_policy_can_be_ready_after_director_installation():
    result = resolve_director_readiness(
        {"backend": "cpu", "ram_gb": 32.0, "cpu_threads": 16},
        mode="fast",
        engine="external",
        installed_models={STANDARD_DIRECTOR_MODEL_ID: True},
        allow_external=True,
    )

    assert result.renderer.engine == "external"
    assert result.renderer.adapter_ready is True
    assert result.ready is True
    assert result.blockers == []


def test_workspace_readiness_route_returns_project_revision_and_actionable_blockers(tmp_path):
    store = ProjectStore(tmp_path / "projects")
    project = store.create("Readiness")

    class Models:
        def installed_path(self, model_id):
            return tmp_path if model_id == STANDARD_DIRECTOR_MODEL_ID else None

    app = FastAPI()
    app.include_router(
        create_director_router(
            lambda: store,
            get_models=lambda: Models(),
            get_hardware=lambda: {
                "backend": "cuda",
                "device_name": "RTX 4050",
                "vram_gb": 6.0,
                "ram_gb": 16.0,
            },
        )
    )
    with TestClient(app) as client:
        response = client.get(f"/v1/projects/{project.id}/director/readiness?mode=automatic")

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["project_id"] == project.id
    assert payload["project_revision"] == project.revision
    assert payload["director"]["model_id"] == STANDARD_DIRECTOR_MODEL_ID
    assert payload["renderer"]["model_id"] == HUNYUAN_MODEL_ID
    assert payload["ready"] is False
    assert payload["actions"]
