"""Route tests for the AI auto-configure + animate endpoints."""

from __future__ import annotations

import json
from pathlib import Path

from edmg_studio_backend.tests.revision_client import TestClient

from edmg_studio_backend import app as backend_app
from edmg_studio_backend.services.render_settings import RenderSettingsStore
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore
from edmg_studio_backend.tests.safetensors_test_utils import write_minimal_safetensors


def _make_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    proj = store.create("AutoAnimate Test")
    proj.meta = {
        "timeline": {"layers": [], "camera": {"keyframes": []}},
        "last_plan": {
            "variants": [
                {
                    "index": 0,
                    "fps": 24,
                    "duration_s": 6.0,
                    "scenes": [
                        {"start_s": 0.0, "end_s": 3.0, "prompt": "neon city"},
                        {"start_s": 3.0, "end_s": 6.0, "prompt": "sunrise skyline"},
                    ],
                }
            ]
        },
    }
    store.save(proj)
    return store, jobs, proj


def _patch(monkeypatch, store, jobs, *, comfy_available=False, tensorrt_available=False):
    monkeypatch.setattr(backend_app, "store", store)
    monkeypatch.setattr(backend_app, "jobs", jobs)
    monkeypatch.setattr(backend_app, "render_settings", RenderSettingsStore(store.base_dir))
    # Keep tests deterministic: never let the background worker execute jobs.
    monkeypatch.setattr(backend_app.worker, "start", lambda *a, **k: None)
    monkeypatch.setattr(backend_app, "_comfyui_available_quick", lambda: comfy_available)
    monkeypatch.setattr(backend_app, "_tensorrt_sd15_bundle_available", lambda: tensorrt_available)


def _install_minimal_internal_model(monkeypatch, tmp_path: Path) -> Path:
    models_dir = tmp_path / "models"
    monkeypatch.setattr(backend_app.models, "models_dir", models_dir)
    model_dir = models_dir / "internal" / "diffusers" / "hf_sd15_internal"
    model_dir.mkdir(parents=True)
    (model_dir / "model_index.json").write_text(
        json.dumps(
            {
                "_class_name": "StableDiffusionPipeline",
                "unet": ["diffusers", "UNet2DConditionModel"],
            }
        ),
        encoding="utf-8",
    )
    write_minimal_safetensors(
        model_dir / "unet" / "diffusion_pytorch_model.safetensors"
    )
    return model_dir


def test_list_animation_presets():
    with TestClient(backend_app.app) as client:
        resp = client.get("/v1/render/animation_presets")
        resp.raise_for_status()
        data = resp.json()
        assert data["ok"] is True
        ids = [p["id"] for p in data["presets"]]
        assert "cinematic_3d" in ids
        assert "image_animation" in ids


def test_auto_dry_run_cinematic_3d(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "cinematic_3d", "engine": "internal", "run": False},
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["ok"] is True
        assert data["launched"] is False
        assert data["engine"] == "internal"
        req = data["config"]["internal_request"]
        assert "deforum_translation_z" in req
        assert "deforum_rotation_3d_y" in req


def test_auto_dry_run_full_motion_uses_storyboard_video_model(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs, tensorrt_available=True)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "full_motion", "engine": "internal", "run": False},
        )
        resp.raise_for_status()
        data = resp.json()
        req = data["config"]["internal_request"]
        assert req["temporal_mode"] == "video_model"
        assert req["motion_strategy"] == "storyboard_full_motion"
        assert req["video_model_engine"] == "auto"
        assert req["video_model_anchor_mode"] == "both"
        assert req["video_model_scene_motion"] == "scene"
        assert req["video_model_keyframe_renderer"] == "tensorrt_sd15"
        assert req["video_model_keyframe_model_id"] == "local_sd15_tensorrt_bundle"


def test_motion_sequencer_apply_generates_active_parseq_manifest(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/motion_sequencer/apply",
            json={"variant_index": 0, "fps": 24, "activate": True},
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["ok"] is True
        assert data["active"] is True
        assert data["summary"]["schedules"] >= 1
        assert data["overrides"]["video_model_motion_score_schedule"]
        saved = store.get(proj.id)
        assert saved.meta["active_parseq_manifest"]["format"] == "edmg_parseq_motion_manifest"
        assert saved.meta["render_recipe_graph"]["source"] == "studio_native"


def test_motion_sequencer_preview_returns_generated_manifest(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    with TestClient(backend_app.app) as client:
        resp = client.get(f"/v1/projects/{proj.id}/render/motion_sequencer?variant_index=0&fps=24")
        resp.raise_for_status()
        data = resp.json()
        assert data["ok"] is True
        assert data["active"] is None
        assert data["generated"]["format"] == "edmg_parseq_motion_manifest"
        assert data["recipe_graph"]["source"] == "studio_native"


def test_auto_dry_run_image_animation_with_source(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={
                "preset": "image_animation",
                "engine": "internal",
                "run": False,
                "source_asset": "assets/refs/painting.png",
            },
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["config"]["uses_source_image"] is True
        assert data["config"]["internal_request"]["source_asset"] == "assets/refs/painting.png"


def test_auto_run_layered_internal_skips_comfy_probe(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    ref = _upload_ref_image(store, proj)
    probe_calls: list[str] = []

    def _unexpected_probe() -> bool:
        probe_calls.append("called")
        raise AssertionError("ComfyUI availability probe should not run for internal layered auto renders")

    monkeypatch.setattr(backend_app, "_comfyui_available_quick", _unexpected_probe)

    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "parallax_animation", "engine": "internal", "run": True, "source_asset": ref},
        )
        resp.raise_for_status()
        data = resp.json()

    assert probe_calls == []
    assert data["ok"] is True
    assert data["launched"] is True
    assert data["engine"] == "internal"
    assert data["animation_mode"] == "parallax"
    assert data["comfyui_available"] is False
    assert data["comfyui_probe_performed"] is False
    assert data["job"]["type"] == "layered_animation"


def test_auto_dry_run_comfyui_engine(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs, comfy_available=True)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "comfyui_animatediff", "engine": "comfyui", "run": False},
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["engine"] == "comfyui"
        assert data["config"]["comfyui_request"]["engine"] == "animatediff"
        assert data["comfyui_available"] is True
        assert data["comfyui_probe_performed"] is True


def test_auto_dry_run_comfyui_preset_probes_availability(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs, comfy_available=False)
    probe_calls: list[str] = []

    def _probe() -> bool:
        probe_calls.append("called")
        return False

    monkeypatch.setattr(backend_app, "_comfyui_available_quick", _probe)

    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "comfyui_animatediff", "engine": "auto", "run": False},
        )
        resp.raise_for_status()
        data = resp.json()

    assert probe_calls == ["called"]
    assert data["engine"] == "internal"
    assert data["comfyui_available"] is False
    assert data["comfyui_probe_performed"] is True


def test_auto_run_internal_launches_job(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    _install_minimal_internal_model(monkeypatch, tmp_path)
    monkeypatch.setattr(
        backend_app,
        "_internal_diffusion_runtime_status",
        lambda: {"ok": True, "message": "ok"},
    )
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "draft_fast", "engine": "internal", "run": True},
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["launched"] is True
        assert data["engine"] == "internal"
        assert data["job"]["type"] == "internal_video"
        assert data["job"]["status"] == "queued"
        # The launched job payload carries the AI-chosen render settings.
        assert data["job"]["payload"]["render_tier"] in ("draft", "balanced", "quality", "auto")


def test_auto_unknown_preset_is_400(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "nope", "run": False},
        )
        assert resp.status_code == 400


def _upload_ref_image(store, proj, name="painting.png", size=(256, 144)):
    from PIL import Image

    refs = store.project_dir(proj.id) / "assets" / "refs"
    refs.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, (40, 60, 90)).save(refs / name)
    return f"assets/refs/{name}"


def test_animate_layers_parallax_launches_job(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    ref = _upload_ref_image(store, proj)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/animate_layers",
            json={"source_asset": ref, "mode": "parallax", "fps": 12, "duration_s": 1.0, "width": 256, "height": 256},
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["ok"] is True
        assert data["job"]["type"] == "layered_animation"
        assert data["job"]["payload"]["mode"] == "parallax"
        assert data["job"]["payload"]["motion_schedule"]  # AI-built motion schedule


def test_animate_layers_rejects_odd_dimensions_before_queue(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    ref = _upload_ref_image(store, proj)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/animate_layers",
            json={"source_asset": ref, "width": 769, "height": 432},
        )

    assert resp.status_code == 422
    assert jobs.list_for_project(proj.id) == []


def test_animate_layers_requires_refinement_model_before_queue(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    ref = _upload_ref_image(store, proj)
    monkeypatch.setattr(backend_app, "_hardware_profile", lambda: {"device_preference": "cpu"})
    monkeypatch.setattr(
        backend_app,
        "_resolve_installed_model_path",
        lambda _model_id, *, materialize_remote: None,
    )
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/animate_layers",
            json={
                "source_asset": ref,
                "diffusion_refine": True,
                "model_id": "missing_internal_model",
            },
        )

    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "REFINEMENT_MODEL_NOT_INSTALLED"
    assert jobs.list_for_project(proj.id) == []


def test_animate_layers_masked_requires_mask(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    ref = _upload_ref_image(store, proj)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/animate_layers",
            json={"source_asset": ref, "mode": "masked", "masks": []},
        )
        assert resp.status_code == 400


def test_animate_layers_missing_source_is_400(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/animate_layers",
            json={"source_asset": "assets/refs/missing.png", "mode": "parallax"},
        )
        assert resp.status_code == 400


def test_auto_routes_parallax_preset_to_layered_job(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    ref = _upload_ref_image(store, proj)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "parallax_animation", "run": True, "source_asset": ref},
        )
        resp.raise_for_status()
        data = resp.json()
        assert data["launched"] is True
        assert data["animation_mode"] == "parallax"
        assert data["job"]["type"] == "layered_animation"


def test_auto_masked_preset_defers_to_animate_layers(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    ref = _upload_ref_image(store, proj)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "masked_object_motion", "run": True, "source_asset": ref},
        )
        resp.raise_for_status()
        data = resp.json()
        # masked needs explicit masks -> not auto-launched
        assert data["launched"] is False
        assert any("mask" in n.lower() for n in data.get("notes", []))


def test_run_layered_animation_writes_render_metadata_and_artifact_manifest(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    _patch(monkeypatch, store, jobs)
    ref = _upload_ref_image(store, proj, size=(320, 256))

    job = jobs.create(
        proj.id,
        "layered_animation",
        {
            "source_asset": ref,
            "mode": "parallax",
            "motion_profile": "full_3d",
            "motion_schedule": {
                "translation_x": "0:(0), 24:(24)",
                "translation_z": "0:(0), 24:(80)",
            },
            "bands": 3,
            "masks": [],
            "subject_motion": 1.0,
            "background_motion": 0.12,
            "fps": 12,
            "duration_s": 1.0,
            "width": 320,
            "height": 256,
            "include_audio": False,
            "diffusion_refine": False,
            "model_id": "auto",
            "device_preference": "auto",
            "refine_prompt": None,
            "refine_negative": "blurry, low quality, watermark, text, logo",
            "refine_denoise": 0.3,
            "refine_steps": 20,
            "refine_cfg": 7.0,
            "seed": 123,
        },
    )

    res = backend_app._run_layered_animation(proj.id, job.id, job.payload)

    project_dir = store.project_dir(proj.id)
    video_path = project_dir / res["video"]
    render_meta_path = video_path.with_suffix(".render.json")
    artifact_path = video_path.with_suffix(video_path.suffix + ".artifact.json")

    assert video_path.exists()
    assert render_meta_path.exists()
    assert artifact_path.exists()

    render_meta = json.loads(render_meta_path.read_text(encoding="utf-8"))
    artifact = json.loads(artifact_path.read_text(encoding="utf-8"))

    assert render_meta["render_mode"] == "layered_animation"
    assert render_meta["engine"] == "internal_layered_animation"
    assert render_meta["mode"] == "parallax"
    assert render_meta["outputs"]["final_mp4"] == str(video_path)
    assert render_meta["frames"]["present"] == 12
    assert artifact["engine"] == "internal_layered_animation"
    assert artifact["kind"] == "video"
    assert artifact["path"] == res["video"].replace("\\", "/")
    assert artifact["extra"]["render_meta"] == render_meta_path.name


def test_auto_requires_plan(tmp_path, monkeypatch):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    proj = store.create("No Plan")
    proj.meta = {"timeline": {}}
    store.save(proj)
    _patch(monkeypatch, store, jobs)
    with TestClient(backend_app.app) as client:
        resp = client.post(
            f"/v1/projects/{proj.id}/render/auto",
            json={"preset": "balanced_motion", "run": False},
        )
        assert resp.status_code == 400
