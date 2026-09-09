from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from edmg_studio_backend.errors import UserFacingError
from edmg_studio_backend.services import internal_video_models as ivm
from edmg_studio_backend.tests.safetensors_test_utils import (
    write_minimal_safetensors,
)


def _write_lfs_pointer(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "version https://git-lfs.github.com/spec/v1\n"
        "oid sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
        "size 123456\n",
        encoding="utf-8",
    )


def test_video_model_load_kwargs_uses_real_fp16_bin_when_safetensors_is_lfs_pointer(tmp_path: Path) -> None:
    _write_lfs_pointer(tmp_path / "unet" / "diffusion_pytorch_model.fp16.safetensors")
    (tmp_path / "unet" / "diffusion_pytorch_model.fp16.bin").write_bytes(b"real fp16 bin weights")
    (tmp_path / "text_encoder").mkdir(parents=True)
    write_minimal_safetensors(
        tmp_path / "text_encoder" / "model.fp16.safetensors"
    )

    kwargs = ivm._video_model_base_load_kwargs(tmp_path, "cuda")

    assert kwargs["variant"] == "fp16"
    assert kwargs["use_safetensors"] is False


def test_video_model_load_kwargs_keeps_real_fp16_safetensors_preferred(tmp_path: Path) -> None:
    (tmp_path / "unet").mkdir(parents=True)
    write_minimal_safetensors(
        tmp_path / "unet" / "diffusion_pytorch_model.fp16.safetensors"
    )

    kwargs = ivm._video_model_base_load_kwargs(tmp_path, "cuda")

    assert kwargs["variant"] == "fp16"
    assert "use_safetensors" not in kwargs


def test_video_model_load_error_wraps_git_lfs_message(tmp_path: Path) -> None:
    with pytest.raises(UserFacingError) as exc:
        ivm._reraise_video_model_load_error(
            RuntimeError("You seem to have cloned a repository without having git-lfs installed."),
            tmp_path,
        )
    assert exc.value.code == "INTERNAL_VIDEO_MODEL_LFS_POINTER"


def test_motion_adapter_layout_is_rejected_for_svd(tmp_path: Path) -> None:
    (tmp_path / "config.json").write_text(
        '{"_class_name": "MotionAdapter"}',
        encoding="utf-8",
    )
    (tmp_path / "diffusion_pytorch_model.safetensors").write_bytes(b"weights")

    with pytest.raises(UserFacingError) as exc:
        ivm.validate_video_model_layout("svd", tmp_path)

    assert exc.value.code == "INTERNAL_VIDEO_MODEL_LAYOUT_INVALID"
    assert "model_index.json" in (exc.value.hint or "")
    assert str(tmp_path) not in exc.value.message
    assert str(tmp_path) not in (exc.value.hint or "")


def test_svd_layout_is_rejected_for_animatediff(tmp_path: Path) -> None:
    (tmp_path / "model_index.json").write_text(
        '{"_class_name": "StableVideoDiffusionPipeline"}',
        encoding="utf-8",
    )

    with pytest.raises(UserFacingError) as exc:
        ivm.validate_video_model_layout("animatediff", tmp_path)

    assert exc.value.code == "INTERNAL_VIDEO_MODEL_LAYOUT_INVALID"
    assert "config.json" in (exc.value.hint or "")
    assert str(tmp_path) not in exc.value.message
    assert str(tmp_path) not in (exc.value.hint or "")


def test_video_model_layout_accepts_canonical_assets(tmp_path: Path) -> None:
    svd_dir = tmp_path / "svd"
    svd_dir.mkdir()
    (svd_dir / "model_index.json").write_text(
        '{"_class_name": "StableVideoDiffusionPipeline"}',
        encoding="utf-8",
    )
    animatediff_dir = tmp_path / "animatediff"
    animatediff_dir.mkdir()
    (animatediff_dir / "config.json").write_text(
        '{"_class_name": "MotionAdapter"}',
        encoding="utf-8",
    )
    (animatediff_dir / "diffusion_pytorch_model.safetensors").write_bytes(b"weights")

    ivm.validate_video_model_layout("svd", svd_dir)
    ivm.validate_video_model_layout("animatediff", animatediff_dir)


def test_hunyuan_layout_accepts_diffusers_and_upstream_config_names(tmp_path: Path) -> None:
    diffusers_dir = tmp_path / "hunyuan-diffusers"
    diffusers_dir.mkdir()
    (diffusers_dir / "model_index.json").write_text(
        '{"_class_name": "HunyuanVideo15Pipeline"}',
        encoding="utf-8",
    )
    upstream_dir = tmp_path / "hunyuan-upstream"
    upstream_dir.mkdir()
    (upstream_dir / "config.json").write_text(
        '{"_class_name": "HunyuanVideo_1_5_Pipeline"}',
        encoding="utf-8",
    )

    ivm.validate_video_model_layout("hunyuan_video15", diffusers_dir)
    ivm.validate_video_model_layout("hunyuan_video15", upstream_dir)


def test_hunyuan_t2v_and_i2v_use_distinct_pipeline_contracts(tmp_path: Path, monkeypatch) -> None:
    model_dir = tmp_path / "hunyuan"
    model_dir.mkdir()
    (model_dir / "model_index.json").write_text(
        '{"_class_name": "HunyuanVideo15Pipeline"}',
        encoding="utf-8",
    )
    calls: list[tuple[str, dict[str, object]]] = []
    guider_calls: list[float] = []
    tiling_calls: list[str] = []

    class FakeGuider:
        def new(self, **kwargs):
            guider_calls.append(float(kwargs["guidance_scale"]))
            return self

    class FakeVae:
        def enable_tiling(self):
            tiling_calls.append("vae")

    class FakePipe:
        def __init__(self) -> None:
            self.guider = FakeGuider()
            self.vae = FakeVae()

        @classmethod
        def from_pretrained(cls, _path, **kwargs):
            calls.append((cls.__name__, dict(kwargs)))
            return cls()

        def to(self, _device):
            return self

        def __call__(self, **kwargs):
            frame = Image.new("RGB", (32, 20), color=(len(calls), 0, 0))
            return SimpleNamespace(frames=[[frame.copy(), frame.copy()]])

    class FakeT2VPipeline(FakePipe):
        pass

    class FakeI2VPipeline(FakePipe):
        pass

    monkeypatch.setitem(
        __import__("sys").modules,
        "diffusers",
        type(
            "FakeDiffusers",
            (),
            {
                "HunyuanVideo15Pipeline": FakeT2VPipeline,
                "HunyuanVideo15ImageToVideoPipeline": FakeI2VPipeline,
            },
        ),
    )
    monkeypatch.setattr(ivm, "_parse_torch_dtype", lambda _dtype, _device: "float32")
    monkeypatch.setattr(ivm, "_seeded_generator", lambda seed, _device: (object(), int(seed or 0)))
    ivm.clear_video_pipeline_cache()

    t2v = ivm.generate_video_model_frames(
        engine="hunyuan_video15",
        video_model_dir=model_dir,
        base_model_dir=tmp_path / "base",
        init_image=None,
        prompt="a dancer in a red room",
        negative_prompt="frozen frame",
        width=32,
        height=20,
        num_frames=2,
        fps=24,
        steps=5,
        cfg=4.5,
        seed=11,
        device="cpu",
    )
    i2v = ivm.generate_video_model_frames(
        engine="hunyuan_video15",
        video_model_dir=model_dir,
        base_model_dir=tmp_path / "base",
        init_image=Image.new("RGB", (32, 20), color="white"),
        prompt="the dancer turns",
        negative_prompt="frozen frame",
        width=32,
        height=20,
        num_frames=2,
        fps=24,
        steps=5,
        cfg=5.5,
        seed=12,
        device="cpu",
    )

    assert len(t2v) == len(i2v) == 2
    assert [name for name, _kwargs in calls] == ["FakeT2VPipeline", "FakeI2VPipeline"]
    assert guider_calls == [4.5, 5.5]
    assert tiling_calls == ["vae", "vae"]


def test_video_model_cache_key_separates_cpu_offload(tmp_path: Path, monkeypatch) -> None:
    calls: list[dict[str, object]] = []
    scheduler_calls: list[dict[str, object]] = []
    attention_slicing_calls = 0

    class FakeAdapter:
        @classmethod
        def from_pretrained(cls, *_args, **_kwargs):
            return cls()

    class FakePipe:
        def __init__(self) -> None:
            self.offload = False
            self.scheduler = SimpleNamespace(config={"beta_schedule": "scaled_linear"})

        @classmethod
        def from_pretrained(cls, *_args, **kwargs):
            calls.append(dict(kwargs))
            return cls()

        def enable_attention_slicing(self):
            nonlocal attention_slicing_calls
            attention_slicing_calls += 1
            return None

        def enable_model_cpu_offload(self):
            self.offload = True
            return None

        def to(self, _device):
            return self

    class FakeScheduler:
        @classmethod
        def from_config(cls, config, **kwargs):
            scheduler_calls.append({"config": dict(config), **kwargs})
            return SimpleNamespace(config={**dict(config), **kwargs})

    monkeypatch.setitem(
        __import__("sys").modules,
        "diffusers",
        type(
            "FakeDiffusers",
            (),
            {
                "AnimateDiffPipeline": FakePipe,
                "DDIMScheduler": FakeScheduler,
                "MotionAdapter": FakeAdapter,
            },
        ),
    )
    monkeypatch.setattr(ivm, "_parse_torch_dtype", lambda _dtype, _device: "float16")
    ivm.clear_video_pipeline_cache()

    first = ivm._load_animatediff_pipeline(
        adapter_dir=tmp_path / "adapter",
        base_model_dir=tmp_path / "base",
        device="cuda",
        dtype="float16",
        cpu_offload=False,
    )
    second = ivm._load_animatediff_pipeline(
        adapter_dir=tmp_path / "adapter",
        base_model_dir=tmp_path / "base",
        device="cuda",
        dtype="float16",
        cpu_offload=True,
    )

    assert first is not second
    assert len(calls) == 2
    assert scheduler_calls == [
        {
            "config": {"beta_schedule": "scaled_linear"},
            "beta_schedule": "linear",
            "timestep_spacing": "linspace",
            "steps_offset": 1,
            "clip_sample": False,
        },
        {
            "config": {"beta_schedule": "scaled_linear"},
            "beta_schedule": "linear",
            "timestep_spacing": "linspace",
            "steps_offset": 1,
            "clip_sample": False,
        },
    ]
    assert second.offload is True
    assert attention_slicing_calls == 0


def test_svd_uses_native_conditioning_and_preserves_whole_pil_frames(tmp_path: Path, monkeypatch) -> None:
    model_dir = tmp_path / "svd"
    model_dir.mkdir()
    (model_dir / "model_index.json").write_text(
        '{"_class_name": "StableVideoDiffusionPipeline"}',
        encoding="utf-8",
    )
    captured: dict[str, object] = {}
    source_frames = [
        Image.new("RGB", (64, 40), color=(255, 0, 0)),
        Image.new("RGB", (64, 40), color=(0, 255, 0)),
    ]

    class FakePipe:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(frames=[source_frames])

    monkeypatch.setattr(ivm, "_load_svd_pipeline", lambda *_args, **_kwargs: FakePipe())
    monkeypatch.setattr(ivm, "_seeded_generator", lambda seed, _device: (object(), int(seed or 0)))

    frames = ivm.generate_video_model_frames(
        engine="svd",
        video_model_dir=model_dir,
        base_model_dir=tmp_path / "base",
        init_image=Image.new("RGB", (64, 40), color="white"),
        prompt="single subject",
        negative_prompt="collage",
        width=64,
        height=40,
        num_frames=2,
        fps=2,
        steps=20,
        cfg=9.8,
        seed=123,
        device="cuda",
        decode_chunk_size=1,
        cpu_offload=True,
    )

    assert captured["fps"] == ivm.SVD_CONDITIONING_FPS
    assert captured["min_guidance_scale"] == ivm.SVD_MIN_GUIDANCE_SCALE
    assert captured["max_guidance_scale"] == ivm.SVD_MAX_GUIDANCE_SCALE
    assert captured["output_type"] == "pil"
    assert [frame.size for frame in frames] == [(64, 40), (64, 40)]
    assert frames[0].getpixel((0, 0)) == (255, 0, 0)
    assert frames[1].getpixel((0, 0)) == (0, 255, 0)


def test_video_model_rejects_incomplete_frame_sequences(tmp_path: Path, monkeypatch) -> None:
    model_dir = tmp_path / "svd"
    model_dir.mkdir()
    (model_dir / "model_index.json").write_text(
        '{"_class_name": "StableVideoDiffusionPipeline"}',
        encoding="utf-8",
    )

    class FakePipe:
        def __call__(self, **_kwargs):
            return SimpleNamespace(frames=[[Image.new("RGB", (64, 40), color="white")]])

    monkeypatch.setattr(ivm, "_load_svd_pipeline", lambda *_args, **_kwargs: FakePipe())
    monkeypatch.setattr(ivm, "_seeded_generator", lambda seed, _device: (object(), int(seed or 0)))

    with pytest.raises(RuntimeError, match="returned 1 frames; expected 2"):
        ivm.generate_video_model_frames(
            engine="svd",
            video_model_dir=model_dir,
            base_model_dir=tmp_path / "base",
            init_image=Image.new("RGB", (64, 40), color="white"),
            prompt="single subject",
            negative_prompt="collage",
            width=64,
            height=40,
            num_frames=2,
            fps=2,
            steps=20,
            cfg=7.0,
            seed=123,
            device="cuda",
        )


def test_animatediff_requests_whole_pil_frames(tmp_path: Path, monkeypatch) -> None:
    adapter_dir = tmp_path / "animatediff"
    adapter_dir.mkdir()
    (adapter_dir / "config.json").write_text(
        '{"_class_name": "MotionAdapter"}',
        encoding="utf-8",
    )
    (adapter_dir / "diffusion_pytorch_model.safetensors").write_bytes(b"weights")
    captured: dict[str, object] = {}

    class FakePipe:
        def __call__(self, **kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                frames=[
                    [
                        Image.new("RGB", (64, 40), color=(0, 0, 255)),
                        Image.new("RGB", (64, 40), color=(255, 255, 0)),
                    ]
                ]
            )

    monkeypatch.setattr(ivm, "_load_animatediff_pipeline", lambda **_kwargs: FakePipe())
    monkeypatch.setattr(ivm, "_seeded_generator", lambda seed, _device: (object(), int(seed or 0)))

    frames = ivm.generate_video_model_frames(
        engine="animatediff",
        video_model_dir=adapter_dir,
        base_model_dir=tmp_path / "base",
        init_image=None,
        prompt="single subject walking",
        negative_prompt="collage",
        width=64,
        height=40,
        num_frames=2,
        fps=2,
        steps=20,
        cfg=9.8,
        seed=456,
        device="cuda",
    )

    assert captured["output_type"] == "pil"
    assert captured["guidance_scale"] == ivm.ANIMATEDIFF_MAX_GUIDANCE_SCALE
    assert [frame.size for frame in frames] == [(64, 40), (64, 40)]
    assert frames[0].getpixel((0, 0)) == (0, 0, 255)
    assert frames[1].getpixel((0, 0)) == (255, 255, 0)


def test_cuda_oom_message_becomes_user_facing_error() -> None:
    assert ivm._is_cuda_out_of_memory(RuntimeError("CUDA out of memory. Tried to allocate 2 GiB."))
    with pytest.raises(UserFacingError) as exc:
        ivm._raise_cuda_oom("AnimateDiff", RuntimeError("CUDA out of memory"))
    assert exc.value.code == "INTERNAL_VIDEO_MODEL_CUDA_OOM"
