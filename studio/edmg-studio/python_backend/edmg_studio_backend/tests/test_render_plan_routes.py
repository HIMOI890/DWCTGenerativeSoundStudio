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


def test_get_render_conductor_plan_returns_stored_plan(tmp_path):
    test_client, store = _client_for_store(tmp_path)
    proj = store.create("Plan Project")
    proj.meta["last_plan"] = {
        "variants": [{"scenes": [{"id": "scene-1", "start_s": 0.0, "end_s": 2.0, "prompt": "test"}]}]
    }
    proj.meta["last_conductor_plan"] = {
        "plan_id": "plan-abc",
        "project_id": proj.id,
        "variant_index": 0,
        "created_at": "2026-01-01T00:00:00Z",
        "advisory_only": True,
        "summary": "Stored plan",
        "sections": [
            {
                "scene_id": "scene-1",
                "engine": "proxy",
                "rationale": "test",
                "estimated_cost": 0.1,
                "estimated_seconds": 4.0,
                "continuity_risk": 0.2,
                "steps": [
                    {
                        "id": "scene-1-motion",
                        "kind": "render_motion",
                        "adapter": "proxy",
                        "inputs": {"scene_id": "scene-1"},
                        "outputs": {"clip": "scene:scene-1:clip"},
                        "notes": [],
                    }
                ],
                "notes": [],
            }
        ],
        "assembly": {"mode": "audio_mux", "expected_output_path": "outputs/videos/out.mp4"},
        "fallback_branches": [],
        "diagnostics": ["advisory_only=true"],
    }
    proj.meta["last_conductor_intent"] = {
        "project_id": proj.id,
        "variant_index": 0,
        "quality_tier": "balanced",
        "continuity_priority": 0.5,
        "speed_priority": 0.5,
        "style_lock_strength": 0.8,
        "allowed_engines": ["internal", "proxy"],
        "fallback_policy": "auto",
        "sections": [],
    }
    store.save(proj)

    get_resp = test_client.get(f"/v1/projects/{proj.id}/render/conductor/plan?variant_index=0")
    assert get_resp.status_code == 200
    body = get_resp.json()
    assert body["stored"] is True
    assert body["plan"]["plan_id"] == "plan-abc"
    assert body["plan"]["tasks"][0]["cache_key"]


def test_template_package_export_and_import(tmp_path):
    test_client, store = _client_for_store(tmp_path)
    proj = store.create("Template Project")
    proj.meta["visual_dna"] = {"version": 1, "project_id": proj.id, "identity": {"motifs": ["city"]}}
    proj.meta["director_mode"] = "narrative"
    store.save(proj)

    export_resp = test_client.get(f"/v1/projects/{proj.id}/template_package/export")
    assert export_resp.status_code == 200
    package = export_resp.json()["package"]
    assert package["schema_version"] == 1

    import_resp = test_client.post(
        f"/v1/projects/{proj.id}/template_package/import",
        json={"package": package, "merge": True},
    )
    assert import_resp.status_code == 200
    applied = import_resp.json()["applied"]
    assert "visual_dna" in applied["fields"]
