from __future__ import annotations

from pathlib import Path

from edmg_studio_backend import app as studio_app
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


def _make_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "data")
    jobs = JobStore(store.projects_dir)
    proj = store.create("Tier Planner Test")
    proj.meta = {
        "analysis": {"duration_s": 12.0},
        "last_plan": {
            "variants": [
                {
                    "name": "v1",
                    "duration_s": 12.0,
                    "scenes": [
                        {"start_s": 0.0, "end_s": 6.0, "prompt": "misty cyber forest"},
                        {"start_s": 6.0, "end_s": 12.0, "prompt": "glowing geometric skyline"},
                    ],
                }
            ]
        },
        "timeline": {"layers": [], "camera": {"keyframes": []}},
    }
    store.save(proj)
    return store, jobs, proj


def test_internal_render_plan_prefers_quality_on_strong_cuda():
    hw = {"backend": "cuda", "vram_gb": 12.0, "ram_gb": 32.0, "cpu_threads": 16}
    plan = studio_app._build_internal_render_plan(hw, requested_tier="auto")

    assert plan["recommended_tier"] == "quality"
    assert plan["applied_tier"] == "quality"
    assert plan["device_preference"] == "cuda"
    assert plan["preferred_internal_model"] == "hf_sdxl_internal"
    assert plan["defaults"]["width"] == 1024


def test_internal_preflight_includes_tier_plan_for_cpu_proxy(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)
    monkeypatch.setattr(studio_app, "_hardware_profile", lambda: {
        "backend": "cpu",
        "device": "cpu",
        "device_name": "CPU",
        "available_backends": ["cpu"],
        "vram_gb": 0.0,
        "ram_gb": 8.0,
        "cpu_threads": 4,
        "backend_family": "cpu_only",
        "preferred_internal_model": "hf_sd15_internal",
        "recommended_tier": "draft",
        "max_supported_tier": "draft",
    })
    monkeypatch.setattr(studio_app.models, "installed_path", lambda _mid: None)

    preflight = studio_app._internal_render_preflight_data(
        proj.id,
        {"variant_index": 0, "fps_render": 2, "fps_output": 24, "model_id": "auto", "allow_proxy_fallback": True, "render_tier": "quality"},
    )

    assert preflight["mode"] == "proxy"
    assert preflight["tier_plan"]["applied_tier"] == "draft"
    assert preflight["tier_plan"]["defaults"]["width"] == 640
    assert any("draft" in w.lower() or "cpu" in w.lower() for w in preflight["warnings"])


def test_run_pipeline_local_fallback_uses_tier_defaults_on_cpu(tmp_path, monkeypatch):
    store, jobs, proj = _make_project(tmp_path)
    monkeypatch.setattr(studio_app, "store", store)
    monkeypatch.setattr(studio_app, "jobs", jobs)
    monkeypatch.setattr(studio_app, "_hardware_profile", lambda: {
        "backend": "cpu",
        "device": "cpu",
        "device_name": "CPU",
        "available_backends": ["cpu"],
        "vram_gb": 0.0,
        "ram_gb": 8.0,
        "cpu_threads": 4,
        "backend_family": "cpu_only",
        "preferred_internal_model": "hf_sd15_internal",
        "recommended_tier": "draft",
        "max_supported_tier": "draft",
    })
    monkeypatch.setattr(studio_app.comfy_pool, "diagnose", lambda _req: {"compatible": [], "busy_compatible": []})
    monkeypatch.setattr(studio_app.models, "installed_path", lambda mid: Path("/tmp/fake-model") if mid == "hf_sd15_internal" else None)

    captured = {}

    def fake_render_internal_video(project_id, req):
        captured["req"] = req
        return {"ok": True, "job": {"id": "job-internal"}, "preflight": {"mode": "diffusion", "tier_plan": {"applied_tier": req.render_tier}}}

    monkeypatch.setattr(studio_app, "render_internal_video", fake_render_internal_video)

    result = studio_app.run_pipeline(proj.id, variant_index=0, preset="quality", mode="auto", engine="auto")

    assert result["ok"] is True
    assert result["selected"]["mode"] == "internal"
    assert captured["req"].render_tier == "draft"
    assert captured["req"].width == 640
    assert captured["req"].device_preference == "cpu"


def test_cpu_chunk_plan_enabled_for_long_render():
    hw = {"backend": "cpu", "backend_family": "cpu_only", "vram_gb": 0.0, "ram_gb": 8.0, "cpu_threads": 4}
    plan = studio_app._build_internal_render_plan(hw, requested_tier="auto", duration_s=180.0)

    assert plan["applied_tier"] == "draft"
    assert plan["chunk_plan"]["enabled"] is True
    assert plan["chunk_plan"]["resume_recommended"] is True
    assert plan["defaults"]["resume_existing_frames"] is True
    assert plan["defaults"]["interpolation_engine"] == "fps"


def test_render_profiles_recommend_laptop_safe_for_cpu(monkeypatch):
    monkeypatch.setattr(studio_app, "_hardware_profile", lambda: {
        "backend": "cpu",
        "device": "cpu",
        "device_name": "CPU",
        "available_backends": ["cpu"],
        "vram_gb": 0.0,
        "ram_gb": 8.0,
        "cpu_threads": 4,
        "backend_family": "cpu_only",
        "preferred_internal_model": "hf_sd15_internal",
        "recommended_tier": "draft",
        "max_supported_tier": "draft",
    })

    out = studio_app.render_profiles()
    assert out["ok"] is True
    assert out["recommended_profile"] == "laptop_safe"
    assert out["profiles"]["laptop_safe"]["internal_render_tier"] == "draft"
