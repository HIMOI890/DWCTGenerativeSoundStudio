from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from edmg_studio_backend.tests.revision_client import TestClient
from PIL import Image

from edmg_studio_backend import app as app_module
from edmg_studio_backend.errors import UserFacingError
from edmg_studio_backend.schemas import InternalVideoRenderRequest, TensorRTStandaloneRenderRequest
from edmg_studio_backend.services import internal_video, tensorrt_standalone, tensorrt_video
from edmg_studio_backend.services.internal_video import InternalVideoSettings
from edmg_studio_backend.store.jobs import JobStore
from edmg_studio_backend.store.projects import ProjectStore


def _make_render_project(tmp_path: Path):
    store = ProjectStore(tmp_path / "data")
    project = store.create("TensorRT Path Handoff")
    project.meta = {
        "last_plan": {
            "variants": [
                {
                    "index": 0,
                    "duration_s": 1.0,
                    "scenes": [{"start_s": 0.0, "end_s": 1.0, "prompt": "neon skyline"}],
                }
            ]
        }
    }
    store.save(project)
    return store, project


class _RunningJobs:
    def get(self, _project_id: str, _job_id: str):
        return SimpleNamespace(status="running", progress={})

    def append_log(self, *_args, **_kwargs) -> None:
        return None

    def update_progress(self, *_args, **_kwargs) -> None:
        return None


def test_internal_settings_parse_video_model_timeline_camera_toggle() -> None:
    default_settings = app_module._internal_settings_from_payload(
        {}, model_id="hf_sd15_internal", render_tier="balanced", device_preference="cuda"
    )
    disabled_settings = app_module._internal_settings_from_payload(
        {"video_model_apply_timeline_camera": "false"},
        model_id="hf_sd15_internal",
        render_tier="balanced",
        device_preference="cuda",
    )

    assert default_settings.video_model_apply_timeline_camera is True
    assert disabled_settings.video_model_apply_timeline_camera is False


def test_storyboard_full_motion_preserves_disabled_prompt_refinement() -> None:
    settings = app_module._internal_settings_from_payload(
        {
            "motion_strategy": "storyboard_full_motion",
            "storyboard_shot_max_s": 4.8,
            "keyframe_interval_s": 6.0,
            "video_model_prompt_refine": False,
        },
        model_id="hf_sd15_internal",
        render_tier="balanced",
        device_preference="cuda",
    )

    resolved = app_module._apply_storyboard_full_motion_settings(
        settings,
        {"video_model_scene_motion": "scene"},
    )

    assert resolved.video_model_prompt_refine is False
    assert resolved.video_model_motion_score_mode == "auto"
    assert resolved.video_model_scene_motion == "scene"
    assert resolved.keyframe_continuity_mode == "project"
    assert resolved.video_model_max_frames_per_scene >= 8
    assert resolved.keyframe_interval_s == 4.8


def test_storyboard_full_motion_preserves_explicit_motion_score_mode() -> None:
    settings = app_module._internal_settings_from_payload(
        {
            "motion_strategy": "storyboard_full_motion",
            "video_model_motion_score_mode": "manual",
            "video_model_manual_motion_score": 5,
        },
        model_id="hf_sd15_internal",
        render_tier="draft",
        device_preference="cuda",
    )

    resolved = app_module._apply_storyboard_full_motion_settings(settings, {})

    assert resolved.video_model_motion_score_mode == "manual"
    assert resolved.video_model_manual_motion_score == 5


def test_studio_resource_policy_reports_native_sdpa_on_6gb_cuda() -> None:
    policy = app_module._studio_native_resource_policy(
        settings_obj=InternalVideoSettings(
            temporal_mode="video_model",
            device_preference="cuda",
        ),
        hw={"backend": "cuda", "vram_gb": 6.0},
        model_family="sd15",
    )

    assert policy["attention_policy"] == "native_sdpa_with_vae_slicing_and_small_decode_chunks"


def test_internal_video_request_accepts_cinematic_both_anchor_mode() -> None:
    request = InternalVideoRenderRequest(video_model_anchor_mode="both")

    assert request.video_model_anchor_mode == "both"


def test_internal_video_request_validates_keyframe_continuity_mode() -> None:
    assert InternalVideoRenderRequest().keyframe_continuity_mode == "scene"
    assert (
        InternalVideoRenderRequest(keyframe_continuity_mode="project").keyframe_continuity_mode
        == "project"
    )

    with pytest.raises(ValueError):
        InternalVideoRenderRequest(keyframe_continuity_mode="sequence")  # type: ignore[arg-type]


def test_internal_settings_parse_keyframe_continuity_mode() -> None:
    default_settings = app_module._internal_settings_from_payload(
        {}, model_id="hf_sd15_internal", render_tier="balanced", device_preference="cuda"
    )
    project_settings = app_module._internal_settings_from_payload(
        {"keyframe_continuity_mode": "project"},
        model_id="hf_sd15_internal",
        render_tier="balanced",
        device_preference="cuda",
    )
    invalid_settings = app_module._internal_settings_from_payload(
        {"keyframe_continuity_mode": "sequence"},
        model_id="hf_sd15_internal",
        render_tier="balanced",
        device_preference="cuda",
    )

    assert default_settings.keyframe_continuity_mode == "scene"
    assert project_settings.keyframe_continuity_mode == "project"
    assert invalid_settings.keyframe_continuity_mode == "scene"

    resolved_payload = app_module._persist_resolved_internal_video_payload(
        {"keyframe_continuity_mode": "scene"},
        {"mode": "diffusion", "settings": {"keyframe_continuity_mode": "project"}},
    )
    assert resolved_payload["keyframe_continuity_mode"] == "project"


def test_stale_tensorrt_runtime_bundle_selection_maps_to_supported_video_bundle() -> None:
    payload = {"model_id": "hf_svd_xt_1_1_tensorrt_bundle"}

    assert app_module._payload_requests_tensorrt_video(payload) is True
    assert app_module._tensorrt_model_id_from_payload(payload) == "local_sd15_tensorrt_bundle"
    warning = app_module._tensorrt_requested_model_warning(payload) or ""
    assert "hf_svd_xt_1_1_tensorrt_bundle" in warning
    assert "discovery-only" in warning
    assert "SD1.5 keyframes" in warning


def test_render_tensorrt_video_variant_uses_keyframes_and_assembles_video(tmp_path, monkeypatch) -> None:
    project_dir = tmp_path / "project"
    bundle_path = tmp_path / "TensorRT Bundle With Spaces"
    bundle_path.mkdir()
    generated_dir = tmp_path / "generated"
    calls: list[dict] = []
    progress_events: list[tuple[str, int, int, str | None]] = []

    def fake_run_job(project_id: str, job_id: str | None, payload: dict) -> dict:
        calls.append({"project_id": project_id, "job_id": job_id, "payload": dict(payload)})
        generated_dir.mkdir(parents=True, exist_ok=True)
        out = generated_dir / f"key_{len(calls):04d}.png"
        Image.new("RGB", (512, 512), (40 * len(calls), 24, 96)).save(out)
        return {"output_path": str(out)}

    def fake_assemble(*, ffmpeg_path: str, frames_dir: Path, out_mp4: Path, fps: int) -> None:
        assert ffmpeg_path == "ffmpeg"
        assert fps == 2
        assert len(list(frames_dir.glob("frame_*.png"))) == 4
        out_mp4.write_bytes(b"raw")

    def fake_interpolate(*, ffmpeg_path: str, in_mp4: Path, out_mp4: Path, fps_out: int, engine: str) -> None:
        assert fps_out == 4
        assert engine == "fps"
        out_mp4.write_bytes(in_mp4.read_bytes() + b"-interp")

    def fake_mux(*, ffmpeg_path: str, video_mp4: Path, audio_path: Path, out_mp4: Path) -> None:
        assert audio_path.name == "song.wav"
        out_mp4.write_bytes(video_mp4.read_bytes() + b"-audio")

    monkeypatch.setattr(tensorrt_video.tensorrt_standalone, "run_job", fake_run_job)
    monkeypatch.setattr(tensorrt_video, "assemble_image_sequence", fake_assemble)
    monkeypatch.setattr(tensorrt_video, "interpolate_video_fps", fake_interpolate)
    monkeypatch.setattr(tensorrt_video, "mux_audio", fake_mux)

    audio_path = tmp_path / "song.wav"
    audio_path.write_bytes(b"wav")

    out = tensorrt_video.render_tensorrt_video_variant(
        ffmpeg_path="ffmpeg",
        project_id="p1",
        project_dir=project_dir,
        variant={"index": 2, "duration_s": 2.0},
        scenes=[{"start_s": 0, "end_s": 2, "prompt": "neon skyline"}],
        audio_path=audio_path,
        settings=InternalVideoSettings(
            fps_render=2,
            fps_output=4,
            steps=3,
            cfg=6.5,
            sampler="pndm",
            seed=123,
            keyframe_interval_s=1.0,
            interpolation_engine="fps",
        ),
        bundle_path=bundle_path,
        model_id="local_sd15_tensorrt_bundle",
        progress_fn=lambda stage, current, total, message=None: progress_events.append((stage, current, total, message)),
    )

    assert out.exists()
    assert out.read_bytes().endswith(b"-interp-audio")
    assert out.name.startswith("internal_trt_v02_")
    assert len(calls) == 2
    for call in calls:
        payload = call["payload"]
        assert call["project_id"] == "p1"
        assert call["job_id"] is None
        assert payload["model_id"] == "local_sd15_tensorrt_bundle"
        assert payload["model_path"] == str(bundle_path.resolve())
        assert payload["width"] == 512
        assert payload["height"] == 512
        assert payload["batch_size"] == 1
    assert len(list((project_dir / "outputs" / "tensorrt_video").glob("*/frames/frame_*.png"))) == 4
    assert progress_events[-1][0] == "complete"


def test_app_worker_passes_exact_preflight_bundle_path_to_tensorrt_video(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    bundle_path = tmp_path / "Canonical TensorRT Bundle With Spaces"
    bundle_path.mkdir()
    captured: dict = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        output = store.project_dir(project.id) / "outputs" / "videos" / "tensorrt.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")
        return output

    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "jobs", _RunningJobs())
    monkeypatch.setattr(
        app_module,
        "_internal_render_preflight_data",
        lambda _project_id, _payload: {
            "mode": "tensorrt",
            "model_id": "local_sd15_tensorrt_bundle",
            "model_path": str(bundle_path.resolve()),
            "estimated_frames": 2,
            "estimated_keyframes": 1,
            "warnings": [],
        },
    )
    monkeypatch.setattr(app_module, "render_tensorrt_video_variant", fake_render)

    result = app_module._run_internal_video(project.id, "job-1", {"render_mode": "tensorrt"})

    assert result["mode"] == "tensorrt"
    assert captured["bundle_path"] == bundle_path.resolve()
    assert captured["model_id"] == "local_sd15_tensorrt_bundle"


def test_standalone_worker_resolves_private_bundle_path_only_at_execution(tmp_path, monkeypatch) -> None:
    bundle_path = tmp_path / "Private TensorRT Bundle"
    bundle_path.mkdir()
    calls: list[tuple[str, str, dict]] = []

    def fake_run_job(project_id: str, job_id: str, payload: dict) -> dict:
        calls.append((project_id, job_id, dict(payload)))
        return {"ok": True, "output_path": "outputs/stills/keyframe.png"}

    monkeypatch.setattr(
        app_module,
        "_resolve_installed_model_path",
        lambda model_id, *, materialize_remote: bundle_path
        if model_id == "local_sd15_tensorrt_bundle" and materialize_remote
        else None,
    )
    monkeypatch.setattr(tensorrt_standalone, "run_job", fake_run_job)
    persisted_payload = {"model_id": "local_sd15_tensorrt_bundle", "prompt": "neon skyline"}

    result = app_module._run_tensorrt_standalone("project-1", "job-1", persisted_payload)

    assert result["ok"] is True
    assert "model_path" not in persisted_payload
    assert calls == [
        (
            "project-1",
            "job-1",
            {
                "model_id": "local_sd15_tensorrt_bundle",
                "prompt": "neon skyline",
                "model_path": str(bundle_path.resolve()),
            },
        )
    ]


def test_app_worker_keeps_tensorrt_anchor_path_separate_from_video_model_path(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    base_path = tmp_path / "SD15 Base"
    svd_path = tmp_path / "SVD Motion Model"
    bundle_path = tmp_path / "TensorRT Anchor Bundle"
    for path in (base_path, svd_path, bundle_path):
        path.mkdir()
    settings = InternalVideoSettings(
        temporal_mode="video_model",
        device_preference="cuda",
        video_model_engine="svd",
        video_model_id="hf_svd_xt_1_1_internal",
        video_model_path=str(svd_path),
        video_model_keyframe_renderer="tensorrt_sd15",
        video_model_keyframe_model_id="local_sd15_tensorrt_bundle",
    )
    variant = project.meta["last_plan"]["variants"][0]
    captured: dict = {}

    def fake_render(**kwargs):
        captured.update(kwargs)
        output = store.project_dir(project.id) / "outputs" / "videos" / "anchor.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")
        return output

    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "jobs", _RunningJobs())
    monkeypatch.setattr(app_module, "_hardware_profile", lambda: {"backend": "cuda", "vram_gb": 12.0})
    monkeypatch.setattr(
        app_module,
        "_internal_render_preflight_data",
        lambda _project_id, _payload: {
            "mode": "diffusion",
            "estimated_frames": 2,
            "tier_plan": {"chunk_plan": {}},
            "warnings": [],
        },
    )
    monkeypatch.setattr(
        app_module,
        "_resolve_internal_render_request",
        lambda _project_id, _payload: (
            project,
            variant,
            "hf_sd15_internal",
            base_path,
            bundle_path,
            settings,
        ),
    )
    monkeypatch.setattr(app_module, "render_internal_video_variant", fake_render)
    monkeypatch.setattr(app_module, "_load_render_checkpoint", lambda _path: None)

    result = app_module._run_internal_video(project.id, "job-2", {"render_mode": "diffusion"})

    assert result["mode"] == "diffusion"
    assert captured["model_dir"] == base_path
    assert captured["settings"].video_model_path == str(svd_path)
    assert captured["tensorrt_bundle_path"] == bundle_path


def test_tensorrt_sd15_keyframe_anchor_resizes_and_uses_bundle(tmp_path, monkeypatch) -> None:
    generated = tmp_path / "trt.png"
    bundle_path = tmp_path / "TensorRT Anchor Bundle With Spaces"
    bundle_path.mkdir()
    Image.new("RGB", (512, 512), (12, 34, 56)).save(generated)
    calls: list[dict] = []

    def fake_run_job(project_id: str, job_id: str | None, payload: dict) -> dict:
        calls.append({"project_id": project_id, "job_id": job_id, "payload": dict(payload)})
        return {"output_path": str(generated)}

    monkeypatch.setattr(tensorrt_standalone, "run_job", fake_run_job)

    image = internal_video._generate_tensorrt_sd15_keyframe(
        project_id="p1",
        prompt="neon skyline",
        negative_prompt="blur",
        width=320,
        height=180,
        steps=4,
        cfg=6.5,
        sampler="pndm",
        seed=123,
        model_id="local_sd15_tensorrt_bundle",
        model_path=bundle_path,
    )

    assert image.size == (320, 180)
    assert calls[0]["project_id"] == "p1"
    assert calls[0]["job_id"] is None
    assert calls[0]["payload"]["model_id"] == "local_sd15_tensorrt_bundle"
    assert calls[0]["payload"]["model_path"] == str(bundle_path.resolve())
    assert calls[0]["payload"]["workflow_family"] == "sd15"
    assert "width" not in calls[0]["payload"]
    assert "height" not in calls[0]["payload"]


def test_internal_request_resolves_distinct_server_side_tensorrt_anchor_path(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    base_path = tmp_path / "Internal SD15"
    svd_path = tmp_path / "Internal SVD"
    bundle_path = tmp_path / "Canonical TensorRT"
    for path in (base_path, svd_path, bundle_path):
        path.mkdir()
    (svd_path / "model_index.json").write_text(
        '{"_class_name": "StableVideoDiffusionPipeline"}',
        encoding="utf-8",
    )

    class FakeModels:
        paths = {
            "hf_sd15_internal": base_path,
            "hf_svd_xt_1_1_internal": svd_path,
            "local_sd15_tensorrt_bundle": bundle_path,
        }

        def installed_path(self, model_id: str):
            return self.paths.get(model_id)

    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "models", FakeModels())
    monkeypatch.setattr(app_module, "_hardware_profile", lambda: {"backend": "cuda", "vram_gb": 12.0})
    monkeypatch.setattr(
        app_module,
        "_build_internal_render_plan",
        lambda *_args, **_kwargs: {
            "preferred_internal_model": "hf_sd15_internal",
            "device_preference": "cuda",
            "defaults": {"temporal_mode": "video_model"},
            "applied_tier": "balanced",
        },
    )
    monkeypatch.setattr(app_module, "_render_provider_status", lambda _hw: {"settings": {"directml": {}}})
    monkeypatch.setattr(app_module, "_internal_model_family_for_request", lambda *_args: "sd15")
    monkeypatch.setattr(app_module, "_internal_model_hardware_issue", lambda *_args: None)

    resolved = app_module._resolve_internal_render_request(
        project.id,
        {
            "model_id": "hf_sd15_internal",
            "device_preference": "cuda",
            "temporal_mode": "video_model",
            "video_model_engine": "svd",
            "video_model_id": "hf_svd_xt_1_1_internal",
            "video_model_keyframe_renderer": "tensorrt_sd15",
            "video_model_keyframe_model_id": "hf_svd_xt_1_1_tensorrt_bundle",
        },
    )

    _proj, _variant, model_id, resolved_base, resolved_bundle, settings = resolved
    assert model_id == "hf_sd15_internal"
    assert resolved_base == base_path
    assert settings.video_model_path == str(svd_path)
    assert settings.video_model_keyframe_model_id == "local_sd15_tensorrt_bundle"
    assert resolved_bundle == bundle_path.resolve()
    assert resolved_bundle not in {resolved_base, Path(settings.video_model_path)}


def test_internal_request_fails_preflight_when_tensorrt_anchor_bundle_is_missing(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    base_path = tmp_path / "Internal SD15"
    svd_path = tmp_path / "Internal SVD"
    base_path.mkdir()
    svd_path.mkdir()
    (svd_path / "model_index.json").write_text(
        '{"_class_name": "StableVideoDiffusionPipeline"}',
        encoding="utf-8",
    )

    class FakeModels:
        paths = {
            "hf_sd15_internal": base_path,
            "hf_svd_xt_1_1_internal": svd_path,
        }

        def installed_path(self, model_id: str):
            return self.paths.get(model_id)

    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "models", FakeModels())
    monkeypatch.setattr(app_module, "_hardware_profile", lambda: {"backend": "cuda", "vram_gb": 12.0})
    monkeypatch.setattr(
        app_module,
        "_build_internal_render_plan",
        lambda *_args, **_kwargs: {
            "preferred_internal_model": "hf_sd15_internal",
            "device_preference": "cuda",
            "defaults": {"temporal_mode": "video_model"},
            "applied_tier": "balanced",
        },
    )
    monkeypatch.setattr(app_module, "_render_provider_status", lambda _hw: {"settings": {"directml": {}}})
    monkeypatch.setattr(app_module, "_internal_model_family_for_request", lambda *_args: "sd15")
    monkeypatch.setattr(app_module, "_internal_model_hardware_issue", lambda *_args: None)

    with pytest.raises(UserFacingError) as exc:
        app_module._resolve_internal_render_request(
            project.id,
            {
                "model_id": "hf_sd15_internal",
                "device_preference": "cuda",
                "temporal_mode": "video_model",
                "video_model_engine": "svd",
                "video_model_id": "hf_svd_xt_1_1_internal",
                "video_model_keyframe_renderer": "tensorrt_sd15",
            },
        )

    assert exc.value.code == "TRT_ANCHOR_BUNDLE_NOT_INSTALLED"


def test_internal_video_request_rejects_flux_still_model(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    flux_path = tmp_path / "hf_flux1_schnell_internal"
    flux_path.mkdir()
    (flux_path / "model_index.json").write_text(
        '{"_class_name":"FluxPipeline"}',
        encoding="utf-8",
    )

    class FakeModels:
        def installed_path(self, model_id: str):
            return flux_path if model_id == "hf_flux1_schnell_internal" else None

    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "models", FakeModels())
    monkeypatch.setattr(app_module, "_hardware_profile", lambda: {"backend": "cuda", "vram_gb": 6.0})
    monkeypatch.setattr(
        app_module,
        "_build_internal_render_plan",
        lambda *_args, **_kwargs: {
            "preferred_internal_model": "hf_sdxl_internal",
            "device_preference": "cuda",
            "defaults": {},
            "applied_tier": "draft",
        },
    )
    monkeypatch.setattr(app_module, "_render_provider_status", lambda _hw: {"settings": {"directml": {}}})

    with pytest.raises(UserFacingError) as exc:
        app_module._resolve_internal_render_request(
            project.id,
            {"model_id": "hf_flux1_schnell_internal", "device_preference": "cuda"},
        )

    assert exc.value.code == "FLUX_VIDEO_BASE_UNSUPPORTED"


def test_public_tensorrt_routes_reject_model_id_that_is_a_filesystem_path(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    jobs = JobStore(store.projects_dir)
    local_directory = tmp_path / "client supplied local directory"
    local_directory.mkdir()
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "jobs", jobs)
    monkeypatch.setattr(app_module.worker, "start", lambda *_args, **_kwargs: None)

    with TestClient(app_module.app) as client:
        for suffix in (
            "tensorrt-standalone",
            "tensorrt-standalone/preview",
            "tensorrt-deforum",
        ):
            response = client.post(
                f"/v1/projects/{project.id}/render/{suffix}",
                json={"model_id": str(local_directory)},
            )
            assert response.status_code == 400
            assert response.json()["error"]["code"] == "TRT_MODEL_UNSUPPORTED"
            assert "hint" in response.json()["error"]

    assert jobs.list_for_project(project.id) == []
    jobs.close()


def test_public_tensorrt_request_bounds_reject_unsafe_workloads(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    jobs = JobStore(store.projects_dir)
    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "jobs", jobs)
    monkeypatch.setattr(app_module.worker, "start", lambda *_args, **_kwargs: None)

    invalid_payloads = [
        {"model_id": "local_sd15_tensorrt_bundle", "width": 16384},
        {"model_id": "local_sd15_tensorrt_bundle", "height": 1},
        {"model_id": "local_sd15_tensorrt_bundle", "steps": 100_000},
        {"model_id": "local_sd15_tensorrt_bundle", "batch_size": 100_000},
        {"model_id": "local_sd15_tensorrt_bundle", "prompt": "x" * 10_001},
    ]
    with TestClient(app_module.app) as client:
        for payload in invalid_payloads:
            response = client.post(
                f"/v1/projects/{project.id}/render/tensorrt-standalone",
                json=payload,
            )
            assert response.status_code == 422

    assert jobs.list_for_project(project.id) == []
    jobs.close()


def test_tensorrt_deforum_compatibility_route_queues_canonical_video(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    jobs = JobStore(store.projects_dir)
    bundle_path = tmp_path / "Canonical TensorRT Bundle"
    bundle_path.mkdir()
    preflight_calls: list[tuple[str, dict]] = []

    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "jobs", jobs)
    monkeypatch.setattr(
        app_module,
        "_server_resolved_tensorrt_payload",
        lambda req: {
            **req.model_dump(),
            "model_id": "local_sd15_tensorrt_bundle",
            "workflow_family": "sd15",
        },
    )
    monkeypatch.setattr(
        app_module,
        "_apply_active_parseq_motion",
        lambda _proj, payload: ({**payload, "parseq_applied": True}, {"summary": "applied"}),
    )
    monkeypatch.setattr(
        app_module,
        "_internal_render_preflight_data",
        lambda project_id, payload: preflight_calls.append((project_id, dict(payload)))
        or {
            "ok": True,
            "mode": "tensorrt",
            "model_id": "local_sd15_tensorrt_bundle",
            "model_path": str(bundle_path.resolve()),
            "estimated_frames": 12,
            "estimated_keyframes": 3,
            "settings": {"profile_width": 512, "profile_height": 512, "max_batch": 1},
        },
    )

    response = app_module.render_tensorrt_deforum(
        project.id,
        TensorRTStandaloneRenderRequest(
            variant_index=0,
            model_id="local_sd15_tensorrt_bundle",
            width=512,
            height=512,
        ),
    )

    queued = jobs.get(project.id, response["job"]["id"])
    assert queued is not None
    assert queued.type == "tensorrt_deforum"
    assert queued.payload["render_mode"] == "tensorrt"
    assert queued.payload["compatibility_source"] == "tensorrt-deforum"
    assert queued.payload["parseq_applied"] is True
    assert "model_path" not in queued.payload
    assert queued.progress["total"] == 18
    assert preflight_calls == [(project.id, queued.payload)]
    assert response["preflight"]["settings"] == {
        "profile_width": 512,
        "profile_height": 512,
        "max_batch": 1,
    }
    assert "model_path" not in response["preflight"]
    assert response["compatibility"] == {
        "route": "tensorrt-deforum",
        "execution_mode": "canonical_tensorrt_keyframe_video",
        "legacy_deforum_schedule_applied": False,
    }
    jobs.close()


def test_public_render_preflight_recursively_removes_private_and_absolute_paths() -> None:
    public = app_module._public_render_preflight(
        {
            "ok": True,
            "model_id": "local_sd15_tensorrt_bundle",
            "model_path": r"C:\private\models\bundle",
            "cache": {
                "frames_dir": "/srv/edmg/private/frames",
                "render_meta_path": "file:///srv/edmg/private/render.json",
                "nested": [
                    "outputs/videos/public.mp4",
                    "/srv/edmg/private/final.mp4",
                    r"D:\private\final.mp4",
                    {"logical_id": "asset-123", "source_path": r"\\server\share\secret.bin"},
                ],
                "logical_video": "outputs/videos/public.mp4",
            },
            "settings": {"profile_width": 512, "profile_height": 512},
        }
    )

    assert public == {
        "ok": True,
        "model_id": "local_sd15_tensorrt_bundle",
        "cache": {
            "nested": ["outputs/videos/public.mp4", {"logical_id": "asset-123"}],
            "logical_video": "outputs/videos/public.mp4",
        },
        "settings": {"profile_width": 512, "profile_height": 512},
    }


def test_legacy_tensorrt_deforum_job_executes_canonical_video_path(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    jobs = JobStore(store.projects_dir)
    job = jobs.create(
        project.id,
        "tensorrt_deforum",
        {
            "model_id": "local_sd15_tensorrt_bundle",
            "deforum_settings": {"zoom": "0:(1.0)"},
        },
    )
    calls: list[tuple[str, str, dict]] = []
    absolute_video = str((tmp_path / "outputs" / "videos" / "canonical.mp4").resolve())

    def fake_run_internal_video(project_id: str, job_id: str, payload: dict) -> dict:
        calls.append((project_id, job_id, dict(payload)))
        return {
            "ok": True,
            "mode": "tensorrt",
            "video": "outputs/videos/canonical.mp4",
            "video_abs": absolute_video,
        }

    monkeypatch.setattr(app_module, "jobs", jobs)
    monkeypatch.setattr(app_module, "_run_internal_video", fake_run_internal_video)

    app_module._execute_job(job)

    completed = jobs.get(project.id, job.id)
    assert completed is not None
    assert completed.status == "succeeded"
    assert calls[0][0:2] == (project.id, job.id)
    assert calls[0][2]["render_mode"] == "tensorrt"
    assert completed.result == {
        "ok": True,
        "mode": "tensorrt",
        "video": "outputs/videos/canonical.mp4",
        "compatibility_route": "tensorrt-deforum",
        "execution_mode": "canonical_tensorrt_keyframe_video",
        "legacy_deforum_schedule_applied": False,
        "output_path": "outputs/videos/canonical.mp4",
    }
    jobs.close()


def test_legacy_tensorrt_deforum_job_discards_untrusted_persisted_paths(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    jobs = JobStore(store.projects_dir)
    untrusted_path = tmp_path / "Old client supplied path"
    untrusted_path.mkdir()
    job = jobs.create(
        project.id,
        "tensorrt_deforum",
        {
            "model_id": str(untrusted_path),
            "model_path": str(untrusted_path),
            "bundle_path": str(untrusted_path),
        },
    )
    calls: list[dict] = []

    def fake_run_internal_video(_project_id: str, _job_id: str, payload: dict) -> dict:
        calls.append(dict(payload))
        return {"ok": True, "video": "outputs/videos/canonical.mp4", "mode": "tensorrt"}

    monkeypatch.setattr(app_module, "jobs", jobs)
    monkeypatch.setattr(app_module, "_run_internal_video", fake_run_internal_video)

    app_module._execute_job(job)

    completed = jobs.get(project.id, job.id)
    assert completed is not None
    assert completed.status == "succeeded"
    assert completed.payload["model_id"] == "local_sd15_tensorrt_bundle"
    assert completed.payload["render_mode"] == "tensorrt"
    assert "model_path" not in completed.payload
    assert "bundle_path" not in completed.payload
    assert calls == [completed.payload]
    jobs.close()


def test_legacy_tensorrt_video_uses_creative_fallback_when_project_has_no_saved_plan(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    project.meta.pop("last_plan", None)
    store.save(project)
    jobs = JobStore(store.projects_dir)
    job = jobs.create(project.id, "tensorrt_deforum", {"model_id": "local_sd15_tensorrt_bundle"})
    bundle_path = tmp_path / "verified-tensorrt-bundle"
    bundle_path.mkdir()
    captured: dict[str, object] = {}

    monkeypatch.setattr(app_module, "store", store)
    monkeypatch.setattr(app_module, "jobs", jobs)
    monkeypatch.setattr(
        app_module,
        "_internal_render_preflight_data",
        lambda _project_id, _payload: {
            "ok": True,
            "mode": "tensorrt",
            "model_id": "local_sd15_tensorrt_bundle",
            "model_path": str(bundle_path),
            "estimated_frames": 4,
            "estimated_keyframes": 1,
        },
    )
    monkeypatch.setattr(
        app_module,
        "_creative_direction_fallback_variant",
        lambda _proj, _variant_index: {
            "index": 0,
            "duration_s": 2.0,
            "_fallback_plan_source": "creative_direction_fallback",
            "scenes": [{"start_s": 0.0, "end_s": 2.0, "prompt": "fallback neon skyline"}],
        },
    )

    def fake_render_tensorrt_video_variant(**kwargs):
        captured.update(kwargs)
        output = store.project_dir(project.id) / "outputs" / "videos" / "legacy-fallback.mp4"
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"video")
        return output

    monkeypatch.setattr(app_module, "render_tensorrt_video_variant", fake_render_tensorrt_video_variant)

    result = app_module._run_internal_video(
        project.id,
        job.id,
        {"model_id": "local_sd15_tensorrt_bundle", "render_mode": "tensorrt", "variant_index": 0},
    )

    assert Path(result["video"]) == Path("outputs/videos/legacy-fallback.mp4")
    assert captured["variant"]["_fallback_plan_source"] == "creative_direction_fallback"
    assert captured["scenes"][0]["prompt"] == "fallback neon skyline"
    jobs.close()


def test_legacy_tensorrt_deforum_job_preserves_canonical_cancellation(tmp_path, monkeypatch) -> None:
    store, project = _make_render_project(tmp_path)
    jobs = JobStore(store.projects_dir)
    job = jobs.create(
        project.id,
        "tensorrt_deforum",
        {"model_id": "local_sd15_tensorrt_bundle"},
    )

    def fake_canceled_render(project_id: str, job_id: str, _payload: dict) -> dict:
        current = jobs.get(project_id, job_id)
        assert current is not None
        current.status = "canceled"
        current.result = {"partial": "outputs/videos/partial.mp4"}
        jobs.save(current)
        raise app_module.JobCanceled("TensorRT render canceled")

    monkeypatch.setattr(app_module, "jobs", jobs)
    monkeypatch.setattr(app_module, "_run_internal_video", fake_canceled_render)

    app_module._execute_job(job)

    completed = jobs.get(project.id, job.id)
    assert completed is not None
    assert completed.status == "canceled"
    assert completed.result == {
        "partial": "outputs/videos/partial.mp4",
    }
    jobs.close()


def test_tensorrt_deforum_route_is_deprecated_in_openapi() -> None:
    operation = app_module.app.openapi()["paths"][
        "/v1/projects/{project_id}/render/tensorrt-deforum"
    ]["post"]

    assert operation["deprecated"] is True


def test_video_model_preflight_reports_tensorrt_anchor_renderer() -> None:
    preflight = internal_video.describe_internal_video_model_preflight(
        scenes=[{"start_s": 0, "end_s": 2, "prompt": "neon skyline"}],
        timeline=None,
        settings=InternalVideoSettings(
            temporal_mode="video_model",
            motion_strategy="storyboard_full_motion",
            video_model_engine="svd",
            video_model_keyframe_renderer="tensorrt_sd15",
            video_model_keyframe_model_id="local_sd15_tensorrt_bundle",
        ),
        duration_s=2.0,
        total_frames=4,
        hardware={"backend": "cuda", "vram_gb": 6.0},
    )

    assert preflight["keyframe_renderer"] == "tensorrt_sd15"
    assert preflight["keyframe_model_id"] == "local_sd15_tensorrt_bundle"
    assert preflight["storyboard_motion_plan"]["anchor_source"] == "tensorrt_sd15_keyframe"


def test_video_model_scene_motion_refines_prompt_and_preflight() -> None:
    settings = InternalVideoSettings(
        temporal_mode="video_model",
        motion_strategy="storyboard_full_motion",
        video_model_prompt_refine=True,
        video_model_scene_motion="scene",
        video_model_motion_score_mode="manual",
        video_model_manual_motion_score=6,
    )
    refined = internal_video._refine_video_model_prompt(  # noqa: SLF001 - pure prompt helper
        "cinematic figure in an old town",
        score_info={"motion_score": 6},
        settings=settings,
    )
    assert "whole scene" in refined
    assert "visible objects themselves move" in refined
    assert len(refined.split()) <= internal_video.CLIP_SAFE_RENDER_PROMPT_MAX_WORDS

    preflight = internal_video.describe_internal_video_model_preflight(
        scenes=[{"start_s": 0, "end_s": 2, "prompt": "cinematic figure in an old town"}],
        timeline=None,
        settings=settings,
        duration_s=2.0,
        total_frames=4,
        hardware={"backend": "cuda", "vram_gb": 12.0},
    )
    assert preflight["scene_motion"] == "scene"
    assert preflight["storyboard_motion_plan"]["shots"][0]["scene_motion"] == "scene"


def test_svd_low_vram_memory_safety_preserves_steps_and_warns() -> None:
    settings = InternalVideoSettings(
        temporal_mode="video_model",
        video_model_engine="svd",
        video_model_id="hf_svd_xt_1_1_internal",
        video_model_max_frames_per_scene=25,
        video_model_decode_chunk_size=8,
        temporal_steps=20,
    )

    safe = app_module._apply_internal_video_model_memory_safety(
        settings,
        {"backend": "cuda", "vram_gb": 6.0},
    )
    warnings = app_module._internal_video_model_memory_warnings(
        safe,
        {"backend": "cuda", "vram_gb": 6.0},
    )

    assert safe.video_model_cpu_offload is True
    assert safe.video_model_max_frames_per_scene == 8
    assert safe.video_model_decode_chunk_size == 1
    assert safe.temporal_steps == 20
    assert any("6 GB CUDA SVD safety" in warning for warning in warnings)
    assert any("Inference steps are preserved" in warning for warning in warnings)


def test_svd_low_vram_canvas_is_capped(monkeypatch) -> None:
    monkeypatch.setattr(internal_video, "_cuda_total_vram_gb", lambda _device: 6.0)

    width, height, note = internal_video._video_model_adapter_canvas(  # noqa: SLF001 - pure sizing helper
        engine="svd",
        width=768,
        height=432,
        device="cuda",
        cpu_offload=True,
    )

    assert (width, height) == (568, 320)
    assert note == "6 GB CUDA SVD canvas capped to 568x320"


@pytest.mark.parametrize(
    ("engine", "device", "vram_gb"),
    [
        ("svd", "cuda", 6.0),
        ("animatediff", "cuda", 6.0),
        ("svd", "cuda", 8.0),
        ("animatediff", "cuda", 8.0),
        ("svd", "cuda", 12.0),
        ("svd", "cpu", 0.0),
    ],
)
def test_parseq_steps_are_not_reduced_by_low_vram_policy(
    monkeypatch,
    engine: str,
    device: str,
    vram_gb: float,
) -> None:
    monkeypatch.setattr(internal_video, "_cuda_total_vram_gb", lambda _device: vram_gb)

    cap = internal_video._video_model_temporal_step_cap(  # noqa: SLF001 - pure policy helper
        engine=engine,
        device=device,
    )
    effective = internal_video._apply_video_model_temporal_step_cap(  # noqa: SLF001
        15,
        cap,
    )

    assert cap is None
    assert effective == 15


def test_internal_negative_prompt_rejects_spatial_storyboard_layouts() -> None:
    settings = InternalVideoSettings(negative_prompt="blurry, watermark")
    context = internal_video._build_unified_deforum_context(  # noqa: SLF001 - pure prompt helper
        scenes=[],
        timeline=None,
        variant=None,
        settings=settings,
        fps=24,
    )

    negative = internal_video._negative_prompt_for_frame(  # noqa: SLF001 - pure prompt helper
        frame_idx=0,
        settings=settings,
        deforum_context=context,
    )

    assert "collage" in negative
    assert "contact sheet" in negative
    assert "split screen" in negative
    assert "multi-panel composition" in negative
    assert "storyboard sheet" in negative
    assert "duplicate subject" in negative
    assert "multiple people" in negative
    assert "extra person" in negative
    assert "cloned subject" in negative


def test_keyframe_continuity_scope_controls_authored_scene_boundary_reset() -> None:
    sentinel = object()

    assert (
        internal_video._keyframe_continuity_source(  # noqa: SLF001 - pure continuity helper
            sentinel,
            previous_scene_index=3,
            scene_index=3,
        )
        is sentinel
    )
    assert (
        internal_video._keyframe_continuity_source(  # noqa: SLF001 - pure continuity helper
            sentinel,
            previous_scene_index=3,
            scene_index=4,
        )
        is None
    )
    assert (
        internal_video._keyframe_continuity_source(  # noqa: SLF001 - pure continuity helper
            sentinel,
            previous_scene_index=3,
            scene_index=4,
            keyframe_continuity_mode="project",
        )
        is sentinel
    )


def test_keyframe_continuity_mode_changes_render_cache_signature(tmp_path: Path) -> None:
    common = {
        "variant_index": 0,
        "model_dir": tmp_path / "model",
        "variant": {"prompts": {"0": "cinematic guitarist"}},
        "scenes": [{"start_s": 0.0, "end_s": 5.0, "prompt": "cinematic guitarist"}],
        "timeline": None,
    }

    scene_tag = internal_video._build_work_tag(  # noqa: SLF001 - deterministic cache contract
        **common,
        settings=InternalVideoSettings(keyframe_continuity_mode="scene"),
    )
    project_tag = internal_video._build_work_tag(  # noqa: SLF001 - deterministic cache contract
        **common,
        settings=InternalVideoSettings(keyframe_continuity_mode="project"),
    )

    assert scene_tag != project_tag


def test_cinematic_both_anchor_mode_blends_opening_and_ending_frames() -> None:
    frames = [Image.new("RGB", (8, 8), (0, 0, 0)) for _ in range(8)]
    start = Image.new("RGB", (8, 8), (255, 0, 0))
    end = Image.new("RGB", (8, 8), (0, 0, 255))

    anchored = internal_video._apply_video_anchor_frames(  # noqa: SLF001 - continuity contract
        frames,
        anchor_mode="both",
        start_img=start,
        end_img=end,
        anchor_strength=0.2,
    )

    assert internal_video._normalize_video_anchor_mode("both") == "both"  # noqa: SLF001
    assert anchored[0].getpixel((0, 0))[0] > 0
    assert anchored[0].getpixel((0, 0))[2] == 0
    assert anchored[-1].getpixel((0, 0))[2] > 0
    assert anchored[-1].getpixel((0, 0))[0] == 0
    assert anchored[len(anchored) // 2].getpixel((0, 0)) == (0, 0, 0)


def test_storyboard_windows_distinguish_technical_continuity_from_authored_dissolve() -> None:
    windows = internal_video._storyboard_scene_windows(  # noqa: SLF001 - motion-plan contract
        scenes=[
            {"start_s": 0.0, "end_s": 5.0, "prompt": "arrival"},
            {
                "start_s": 5.0,
                "end_s": 10.0,
                "prompt": "departure",
                "transition_cue": "match dissolve",
            },
        ],
        duration_s=10.0,
        settings=InternalVideoSettings(
            motion_strategy="storyboard_full_motion",
            storyboard_shot_max_s=2.5,
        ),
    )

    assert [window["_storyboard_transition"] for window in windows] == [
        "opening",
        "technical_continue",
        "dissolve",
        "technical_continue",
    ]


def test_authored_scene_dissolve_blends_without_changing_frame_size() -> None:
    previous = Image.new("RGB", (8, 8), (255, 0, 0))
    current = Image.new("RGB", (8, 8), (0, 0, 255))

    dissolved = internal_video._blend_storyboard_scene_boundary(  # noqa: SLF001
        previous,
        current,
        transition="dissolve",
    )
    cut = internal_video._blend_storyboard_scene_boundary(  # noqa: SLF001
        previous,
        current,
        transition="cut",
    )

    assert dissolved.size == current.size
    assert dissolved.getpixel((0, 0)) == (127, 0, 127)
    assert cut.getpixel((0, 0)) == (0, 0, 255)


def test_video_motion_score_reads_reactive_camel_case_sections_and_time_events() -> None:
    score = internal_video.video_model_scene_motion_score(
        scene={"energy": 0.2},
        timeline={
            "reactive_lab": {
                "sections": [
                    {"startTime": 0.0, "endTime": 4.0, "avgEnergy": 0.8},
                ],
                "beat_markers": [
                    {"time": 0.5},
                    {"time": 1.5},
                    {"time": 2.5},
                ],
            }
        },
        start_s=0.0,
        end_s=4.0,
        duration_s=4.0,
        settings=InternalVideoSettings(video_model_motion_score_mode="auto"),
    )

    assert score["source"] == "scene+timeline+events"
    assert score["energy"] > 0.59
    assert score["event_density"] > 0
    assert score["motion_score"] >= 5


def test_release_cached_internal_pipelines_moves_cuda_previews_to_cpu(monkeypatch) -> None:
    class FakePipeline:
        def __init__(self) -> None:
            self.devices: list[str] = []

        def to(self, device: str):
            self.devices.append(device)
            return self

    shared = FakePipeline()
    inpaint = FakePipeline()
    pipes = SimpleNamespace(
        txt2img=shared,
        img2img=shared,
        inpaint=inpaint,
        device="cuda",
    )
    cleanup_devices: list[str] = []
    monkeypatch.setattr(internal_video, "_cleanup_torch_cuda", cleanup_devices.append)

    internal_video._PipelineCache.clear()  # noqa: SLF001 - cache-release contract
    internal_video._PipelineCache.set(("preview", "cuda", "video"), pipes)  # noqa: SLF001
    try:
        released = internal_video.release_cached_internal_pipelines()
    finally:
        internal_video._PipelineCache.clear()  # noqa: SLF001

    assert released == 1
    assert shared.devices == ["cpu"]
    assert inpaint.devices == ["cpu"]
    assert cleanup_devices == ["cuda"]
    assert internal_video._PipelineCache.get(("preview", "cuda", "video")) is None  # noqa: SLF001
