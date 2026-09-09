from __future__ import annotations

from pathlib import Path

import pytest

from edmg_studio_backend import app as app_module
from edmg_studio_backend.errors import UserFacingError


def test_ltx_selection_is_registered_but_never_bypasses_runtime_admission(tmp_path, monkeypatch):
    installed = _install_lookup(tmp_path, monkeypatch)
    installed[app_module.LTX_MODEL_ID] = tmp_path / "ltx"
    monkeypatch.setattr(app_module, "_hardware_profile", lambda: {"backend": "cuda", "vram_gb": 80, "ram_gb": 128})
    for engine in ("auto", "ltx_25"):
        with pytest.raises(UserFacingError) as exc:
            app_module._resolve_internal_video_model_selection(
                {"video_model_engine": engine, "video_model_id": app_module.LTX_MODEL_ID}, base_model_family="sd15")
        assert exc.value.code == "DIRECTOR_RENDERER_NOT_READY"
        assert "LTX-2.5" in exc.value.hint


def _write_svd_layout(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "model_index.json").write_text(
        '{"_class_name": "StableVideoDiffusionPipeline"}',
        encoding="utf-8",
    )
    return path


def _write_animatediff_layout(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "config.json").write_text(
        '{"_class_name": "MotionAdapter"}',
        encoding="utf-8",
    )
    (path / "diffusion_pytorch_model.safetensors").write_bytes(b"weights")
    return path


def _write_hunyuan_layout(path: Path) -> Path:
    path.mkdir(parents=True)
    (path / "model_index.json").write_text(
        '{"_class_name": "HunyuanVideo15Pipeline"}',
        encoding="utf-8",
    )
    return path


def _install_lookup(tmp_path: Path, monkeypatch) -> dict[str, Path]:
    installed = {
        app_module.INTERNAL_SVD_VIDEO_MODEL_ID: _write_svd_layout(tmp_path / "svd"),
        app_module.INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID: _write_animatediff_layout(
            tmp_path / "animatediff"
        ),
    }
    monkeypatch.setattr(app_module.models, "installed_path", installed.get)
    return installed


@pytest.mark.parametrize(
    ("engine", "model_id"),
    [
        ("svd", app_module.INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID),
        ("animatediff", app_module.INTERNAL_SVD_VIDEO_MODEL_ID),
    ],
)
def test_explicit_engine_model_mismatch_is_rejected_before_render(
    tmp_path: Path,
    monkeypatch,
    engine: str,
    model_id: str,
) -> None:
    _install_lookup(tmp_path, monkeypatch)

    with pytest.raises(UserFacingError) as exc:
        app_module._resolve_internal_video_model_selection(
            {"video_model_engine": engine, "video_model_id": model_id},
            base_model_family="sd15",
        )

    assert exc.value.code == "INTERNAL_VIDEO_MODEL_ENGINE_MODEL_MISMATCH"
    assert "does not match" in exc.value.message
    assert str(tmp_path) not in exc.value.message
    assert str(tmp_path) not in (exc.value.hint or "")


def test_auto_engine_uses_selected_models_declared_engine(tmp_path: Path, monkeypatch) -> None:
    installed = _install_lookup(tmp_path, monkeypatch)

    engine, model_id, model_path = app_module._resolve_internal_video_model_selection(
        {
            "video_model_engine": "auto",
            "video_model_id": app_module.INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID,
        },
        base_model_family="sd15",
    )

    assert engine == "animatediff"
    assert model_id == app_module.INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID
    assert model_path == installed[model_id]


def test_arbitrary_installed_model_cannot_enter_video_model_lane(tmp_path: Path, monkeypatch) -> None:
    _install_lookup(tmp_path, monkeypatch)

    with pytest.raises(UserFacingError) as exc:
        app_module._resolve_internal_video_model_selection(
            {"video_model_engine": "auto", "video_model_id": "hf_sd15_internal"},
            base_model_family="sd15",
        )

    assert exc.value.code == "INTERNAL_VIDEO_MODEL_UNSUPPORTED"


def test_invalid_selected_model_layout_fails_preflight(tmp_path: Path, monkeypatch) -> None:
    invalid_svd = tmp_path / "svd"
    invalid_svd.mkdir()
    installed = {
        app_module.INTERNAL_SVD_VIDEO_MODEL_ID: invalid_svd,
        app_module.INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID: None,
    }
    monkeypatch.setattr(app_module.models, "installed_path", installed.get)

    with pytest.raises(UserFacingError) as exc:
        app_module._resolve_internal_video_model_selection(
            {"video_model_engine": "svd"},
            base_model_family="sd15",
        )

    assert exc.value.code == "INTERNAL_VIDEO_MODEL_LAYOUT_INVALID"


def test_hunyuan_selection_is_blocked_until_renderer_admission_is_qualified(
    tmp_path: Path, monkeypatch
) -> None:
    hunyuan = _write_hunyuan_layout(tmp_path / "hunyuan")
    installed = {
        app_module.INTERNAL_SVD_VIDEO_MODEL_ID: None,
        app_module.INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID: None,
        app_module.HUNYUAN_MODEL_ID: hunyuan,
    }
    monkeypatch.setattr(app_module.models, "installed_path", installed.get)

    with pytest.raises(UserFacingError) as exc:
        app_module._resolve_internal_video_model_selection(
            {
                "video_model_engine": "hunyuan_video15",
                "video_model_id": app_module.HUNYUAN_MODEL_ID,
            },
            base_model_family="sd15",
        )

    assert exc.value.code == "DIRECTOR_RENDERER_NOT_READY"
    assert "not release-qualified" in (exc.value.hint or "")


def test_public_render_job_error_preserves_only_curated_details() -> None:
    curated = app_module._public_render_job_error(
        UserFacingError(
            "Selected model does not match its engine",
            hint="Choose the matching SVD or AnimateDiff model.",
            code="INTERNAL_VIDEO_MODEL_ENGINE_MODEL_MISMATCH",
        )
    )
    assert "Selected model does not match its engine" in curated
    assert "Fix: Choose the matching SVD or AnimateDiff model." in curated
    assert "Code: INTERNAL_VIDEO_MODEL_ENGINE_MODEL_MISMATCH" in curated

    secret = "token=do-not-leak path=C:\\private\\model"
    generic = app_module._public_render_job_error(RuntimeError(secret))
    assert generic == "Render job failed."
    assert "do-not-leak" not in generic
    assert "C:\\private" not in generic


def test_resolved_video_model_pair_and_memory_policy_are_persisted() -> None:
    resolved = app_module._persist_resolved_internal_video_payload(
        {
            "model_id": "auto",
            "render_mode": "auto",
            "temporal_mode": "video_model",
            "video_model_engine": "auto",
            "video_model_id": "",
        },
        {
            "mode": "diffusion",
            "model_id": "hf_sd15_internal",
            "settings": {
                "temporal_mode": "video_model",
                "temporal_steps": 6,
                "video_model_engine": "svd",
                "video_model_id": app_module.INTERNAL_SVD_VIDEO_MODEL_ID,
                "video_model_max_frames_per_scene": 8,
                "video_model_decode_chunk_size": 1,
                "video_model_cpu_offload": True,
            },
        },
    )

    assert resolved["render_mode"] == "diffusion"
    assert resolved["model_id"] == "hf_sd15_internal"
    assert resolved["video_model_engine"] == "svd"
    assert resolved["video_model_id"] == app_module.INTERNAL_SVD_VIDEO_MODEL_ID
    assert resolved["video_model_max_frames_per_scene"] == 8
    assert resolved["video_model_decode_chunk_size"] == 1
    assert resolved["video_model_cpu_offload"] is True
    assert resolved["temporal_steps"] == 6
    motion_node = next(
        node for node in resolved["_render_recipe_graph"]["nodes"] if node["id"] == "motion"
    )
    assert motion_node["engine"] == "svd"


def test_legacy_retry_repairs_only_known_mismatched_pair() -> None:
    repaired, note = app_module._repair_legacy_internal_video_selection(
        {
            "video_model_engine": "svd",
            "video_model_id": app_module.INTERNAL_ANIMATEDIFF_VIDEO_MODEL_ID,
        }
    )

    assert repaired["video_model_engine"] == "svd"
    assert repaired["video_model_id"] == app_module.INTERNAL_SVD_VIDEO_MODEL_ID
    assert note is not None
    assert "Normalized legacy" in note

    unknown, unknown_note = app_module._repair_legacy_internal_video_selection(
        {"video_model_engine": "svd", "video_model_id": "unknown-installed-model"}
    )
    assert unknown["video_model_id"] == "unknown-installed-model"
    assert unknown_note is None
