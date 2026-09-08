from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest

from edmg_studio_backend.services import model_manager as model_manager_module
from edmg_studio_backend.services.model_manager import (
    ModelManager,
    ModelTask,
    ModelTaskCancelled,
    ModelTaskManager,
    _hf_profile_matches_path,
    _hf_snapshot_download_profile,
)
from edmg_studio_backend.tests.safetensors_test_utils import (
    write_minimal_safetensors,
)


def _manager(tmp_path, monkeypatch) -> ModelManager:
    monkeypatch.setattr(model_manager_module, "HFBucketModelCache", None)
    monkeypatch.setattr(model_manager_module, "S3ModelCache", None)
    monkeypatch.setattr(model_manager_module, "AzureModelCache", None)
    monkeypatch.setattr(model_manager_module, "hf_token_candidates", lambda **_kwargs: [])
    for key in (
        "EDMG_HF_BUCKET_MODEL_CACHE",
        "EDMG_AWS_MODEL_CACHE",
        "EDMG_S3_MODEL_CACHE",
        "EDMG_MODEL_STORAGE_MODE",
        "EDMG_AWS_MODEL_CACHE_MODE",
        "EDMG_MODEL_CACHE_MODE",
    ):
        monkeypatch.delenv(key, raising=False)
    return ModelManager(
        tmp_path / "data",
        tmp_path / "models",
        tmp_path / "external",
        "http://127.0.0.1:8188",
        "http://127.0.0.1:11434",
    )


def _diffusers_entry(model_id: str = "test_diffusers") -> dict:
    return {
        "id": model_id,
        "name": "Test Diffusers",
        "kind": "diffusers",
        "source": "hf",
        "hf_repo_id": "example/test-diffusers",
        "target": {"engine": "internal", "folder": "diffusers"},
    }


def test_director_catalog_is_pinned_and_requires_complete_snapshot(tmp_path, monkeypatch):
    from edmg_studio_backend.services.model_catalog import built_in_catalog

    entry = next(item for item in built_in_catalog() if item["id"] == "hf_qwen3_vl_8b_director")
    assert entry["hf_revision"] == "0c351dd01ed87e9c1b53cbc748cba10e6187ff3b"
    manager = _manager(tmp_path, monkeypatch)
    mode, destination = manager._models_dest(entry)
    assert mode == "snapshot"
    destination.mkdir(parents=True, exist_ok=True)
    for filename in entry["required_files"]:
        (destination / filename).write_text(json.dumps({"fixture": True}), encoding="utf-8")
    (destination / "config.json").write_text(json.dumps({"model_type": "qwen3_vl"}), encoding="utf-8")
    (destination / "model.safetensors.index.json").write_text(json.dumps({
        "weight_map": {"weight": "model-00001-of-00001.safetensors"}
    }), encoding="utf-8")
    assert not manager._internal_asset_installed(entry, destination)
    write_minimal_safetensors(destination / "model-00001-of-00001.safetensors")
    assert manager._internal_asset_installed(entry, destination)
    (destination / "chat_template.json").write_text("{}", encoding="utf-8")
    assert not manager._internal_asset_installed(entry, destination)
    profile = _hf_snapshot_download_profile(entry, weight_format="safetensors")
    assert _hf_profile_matches_path("model-00001-of-00004.safetensors", profile)


def _write_valid_unet_snapshot(path: Path, *, extension: str = "safetensors") -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "model_index.json").write_text(
        json.dumps(
            {
                "_class_name": "StableDiffusionPipeline",
                "feature_extractor": [None, None],
                "scheduler": ["diffusers", "DDIMScheduler"],
                "tokenizer": ["transformers", "CLIPTokenizer"],
                "unet": ["diffusers", "UNet2DConditionModel"],
            }
        ),
        encoding="utf-8",
    )
    (path / "scheduler").mkdir(exist_ok=True)
    (path / "scheduler" / "scheduler_config.json").write_text("{}", encoding="utf-8")
    (path / "tokenizer").mkdir(exist_ok=True)
    (path / "tokenizer" / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (path / "unet").mkdir(exist_ok=True)
    weight_path = path / "unet" / f"diffusion_pytorch_model.{extension}"
    if extension == "safetensors":
        write_minimal_safetensors(weight_path)
    else:
        weight_path.write_bytes(b"weights")


def test_hf_snapshot_profile_keeps_runnable_layout_and_excludes_export_dump() -> None:
    entry = _diffusers_entry()
    metadata = _hf_snapshot_download_profile(entry, weight_format="metadata")
    preferred = _hf_snapshot_download_profile(
        entry,
        weight_format="safetensors",
        components=["unet", "vae", "text_encoder"],
    )
    legacy = _hf_snapshot_download_profile(
        entry,
        weight_format="bin",
        components=["unet"],
    )

    assert _hf_profile_matches_path("model_index.json", metadata)
    assert _hf_profile_matches_path("tokenizer/vocab.json", metadata)
    assert _hf_profile_matches_path("scheduler/scheduler_config.json", metadata)
    assert _hf_profile_matches_path("unet/diffusion_pytorch_model.safetensors", preferred)
    assert not _hf_profile_matches_path("v1-5-pruned-emaonly.safetensors", preferred)
    assert not _hf_profile_matches_path(
        "unet/diffusion_pytorch_model.fp16.safetensors",
        preferred,
    )
    assert not _hf_profile_matches_path(
        "unet/diffusion_pytorch_model.non_ema.safetensors",
        preferred,
    )
    assert not _hf_profile_matches_path("onnx/unet/model.onnx", preferred)
    assert not _hf_profile_matches_path("openvino/openvino_model.bin", legacy)
    assert _hf_profile_matches_path("unet/diffusion_pytorch_model.bin", legacy)


def test_catalog_skips_unused_ollama_and_ttl_caches_active_probe(
    tmp_path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry = {
        "id": "ollama_test",
        "source": "ollama",
        "ollama_model": "qwen:test",
    }
    calls = 0

    class FakeResponse:
        ok = True

        @staticmethod
        def json():
            return {"models": [{"name": "qwen:test"}]}

    def fake_get(_url, **_kwargs):
        nonlocal calls
        calls += 1
        return FakeResponse()

    monkeypatch.setattr(model_manager_module.requests, "get", fake_get)
    monkeypatch.setenv("EDMG_AI_PROVIDER", "nemotron_cloud")

    assert manager._installed_map([entry])["ollama_test"] is False
    assert calls == 0

    monkeypatch.setenv("EDMG_AI_PROVIDER", "ollama")
    assert manager._installed_map([entry])["ollama_test"] is True
    assert manager._installed_map([entry])["ollama_test"] is True
    assert calls == 1


def test_cloud_snapshot_fast_path_requires_remote_completeness_api(
    tmp_path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry = _diffusers_entry("cloud_snapshot")
    _mode, dest = manager._models_dest(entry)

    class ExistsOnlyCache:
        @staticmethod
        def model_directory_exists(_entry, _path):
            return "internal/diffusers/cloud_snapshot"

    manager.model_cache = ExistsOnlyCache()
    assert (
        manager._cache_snapshot_exists(entry, dest, require_complete=True)
        is None
    )

    class ValidatingCache(ExistsOnlyCache):
        def __init__(self, complete: bool):
            self.complete = complete
            self.calls: list[tuple[str, dict]] = []

        def model_directory_complete(self, remote_prefix, *, model_entry):
            self.calls.append((remote_prefix, model_entry))
            return self.complete

    invalid = ValidatingCache(False)
    manager.model_cache = invalid
    assert (
        manager._cache_snapshot_exists(entry, dest, require_complete=True)
        is None
    )
    assert invalid.calls[0][0] == "internal/diffusers/cloud_snapshot"

    valid = ValidatingCache(True)
    manager.model_cache = valid
    assert (
        manager._cache_snapshot_exists(entry, dest, require_complete=True)
        == "internal/diffusers/cloud_snapshot"
    )


def test_hf_snapshot_uses_bin_only_as_missing_safetensors_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    calls: list[tuple[list[str], list[str]]] = []

    def fake_snapshot_download(*, local_dir, allow_patterns, ignore_patterns, **_kwargs):
        calls.append((list(allow_patterns), list(ignore_patterns)))
        target = Path(local_dir)
        target.mkdir(parents=True, exist_ok=True)
        if any(pattern.endswith(".json") for pattern in allow_patterns):
            (target / "model_index.json").write_text(
                json.dumps({"unet": ["diffusers", "UNet2DConditionModel"]}),
                encoding="utf-8",
            )
            (target / "unet").mkdir(exist_ok=True)
            (target / "unet" / "config.json").write_text("{}", encoding="utf-8")
        if any(pattern.endswith("*.bin") for pattern in allow_patterns):
            (target / "unet" / "diffusion_pytorch_model.bin").write_bytes(b"legacy")

    monkeypatch.setattr(model_manager_module, "snapshot_download", fake_snapshot_download)
    entry = _diffusers_entry()
    task = ModelTask(id="download", name="Download")

    manager._install_file_model(task, entry)

    snapshot = tmp_path / "models" / "internal" / "diffusers" / entry["id"]
    assert (snapshot / "unet" / "diffusion_pytorch_model.bin").read_bytes() == b"legacy"
    assert len(calls) == 4
    assert any(pattern.endswith("*.safetensors") for pattern in calls[1][0])
    assert any(pattern.endswith("*.safetensors") for pattern in calls[2][0])
    assert any(pattern.endswith("*.bin") for pattern in calls[3][0])
    assert all("*.ckpt" in ignores for _allows, ignores in calls)
    assert task.stage == "complete"
    assert task.bytes_completed > 0
    assert task.files_completed >= 3
    assert "Selected inference plan" in task.last_log
    assert "legacy PyTorch .bin" in task.last_log


def test_sharded_component_requires_every_weight_map_file(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    component = tmp_path / "unet"
    component.mkdir()
    index = component / "diffusion_pytorch_model.safetensors.index.json"
    index.write_text(
        json.dumps(
            {
                "weight_map": {
                    "a": "diffusion_pytorch_model-00001-of-00002.safetensors",
                    "b": "diffusion_pytorch_model-00002-of-00002.safetensors",
                }
            }
        ),
        encoding="utf-8",
    )
    first = component / "diffusion_pytorch_model-00001-of-00002.safetensors"
    second = component / "diffusion_pytorch_model-00002-of-00002.safetensors"
    write_minimal_safetensors(first)

    assert manager._internal_component_has_weights(component) is False

    second.write_text(
        "version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 12\n",
        encoding="utf-8",
    )
    assert manager._internal_component_has_weights(component) is False

    write_minimal_safetensors(second)
    assert manager._internal_component_has_weights(component) is True


def test_component_rejects_variant_only_and_corrupt_default_safetensors(
    tmp_path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    component = tmp_path / "unet"
    component.mkdir()
    write_minimal_safetensors(
        component / "diffusion_pytorch_model.fp16.safetensors"
    )
    write_minimal_safetensors(
        component / "diffusion_pytorch_model.non_ema.safetensors"
    )

    assert manager._internal_component_has_weights(component) is False

    default = component / "diffusion_pytorch_model.safetensors"
    header = json.dumps(
        {
            "weight": {
                "dtype": "F32",
                "shape": [100],
                "data_offsets": [0, 400],
            }
        }
    ).encode("utf-8")
    default.write_bytes(len(header).to_bytes(8, "little") + header + b"\0" * 4)
    assert manager._internal_component_has_weights(component) is False

    write_minimal_safetensors(default)
    assert manager._internal_component_has_weights(component) is True


def test_corrupt_cached_safetensors_gets_one_forced_repair(
    tmp_path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    calls: list[dict] = []

    def fake_snapshot_download(*, local_dir, allow_patterns, **kwargs):
        calls.append(
            {
                "allow_patterns": list(allow_patterns),
                "force_download": bool(kwargs.get("force_download")),
            }
        )
        target = Path(local_dir)
        target.mkdir(parents=True, exist_ok=True)
        (target / "model_index.json").write_text(
            json.dumps({"unet": ["diffusers", "UNet2DConditionModel"]}),
            encoding="utf-8",
        )
        (target / "unet").mkdir(exist_ok=True)
        (target / "unet" / "config.json").write_text("{}", encoding="utf-8")
        selects_safetensors = any(
            pattern.endswith("*.safetensors") for pattern in allow_patterns
        )
        if selects_safetensors and kwargs.get("force_download"):
            write_minimal_safetensors(
                target / "unet" / "diffusion_pytorch_model.safetensors"
            )
        elif selects_safetensors:
            corrupt = target / "unet" / "diffusion_pytorch_model.safetensors"
            corrupt.write_bytes((64).to_bytes(8, "little") + b"{}")

    monkeypatch.setattr(model_manager_module, "snapshot_download", fake_snapshot_download)
    task = ModelTask(id="repair", name="Repair")

    manager._install_file_model(task, _diffusers_entry("repair_model"))

    assert [call["force_download"] for call in calls] == [False, False, True]
    assert not any(
        any(pattern.endswith("*.bin") for pattern in call["allow_patterns"])
        for call in calls
    )
    assert "forcing one clean redownload" in task.last_log
    assert "Validated repaired default-safetensors" in task.last_log


def test_direct_file_download_resumes_partial_bytes(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    dest = tmp_path / "models" / "checkpoints" / "model.safetensors"
    partial = dest.with_suffix(dest.suffix + ".tmp")
    partial.parent.mkdir(parents=True)
    partial.write_bytes(b"abc")
    captured_headers: dict[str, str] = {}

    class FakeResponse:
        status_code = 206
        headers = {"content-length": "3", "content-range": "bytes 3-5/6"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def raise_for_status(self):
            return None

        def iter_content(self, chunk_size):
            assert chunk_size == 1024 * 1024
            yield b"def"

    def fake_get(_url, **kwargs):
        captured_headers.update(kwargs["headers"])
        return FakeResponse()

    monkeypatch.setattr(model_manager_module.requests, "get", fake_get)
    task = ModelTask(id="file", name="File")

    manager._download_stream(task, "https://example.invalid/model", dest)

    assert captured_headers["Range"] == "bytes=3-"
    assert dest.read_bytes() == b"abcdef"
    assert task.bytes_completed == 6
    assert task.bytes_total == 6
    assert task.files_completed == 1
    assert task.progress == 1.0


def test_hf_transfer_yields_to_resumable_hub_path_for_existing_partial(
    tmp_path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    local_dir = tmp_path / "snapshot"
    cache = local_dir / ".cache" / "huggingface" / "download"
    cache.mkdir(parents=True)
    partial = cache / "hashed.etag.incomplete"
    partial.write_bytes(b"resume-me")
    enabled_during_call: list[bool] = []

    monkeypatch.setattr(
        model_manager_module.hf_hub_constants,
        "HF_HUB_ENABLE_HF_TRANSFER",
        True,
    )
    monkeypatch.setattr(
        model_manager_module.hf_hub_constants,
        "HF_HUB_DISABLE_XET",
        False,
    )

    def fake_snapshot_download(**_kwargs):
        enabled_during_call.append(
            model_manager_module.hf_hub_constants.HF_HUB_ENABLE_HF_TRANSFER
        )
        assert partial.read_bytes() == b"resume-me"

    monkeypatch.setattr(model_manager_module, "snapshot_download", fake_snapshot_download)
    task = ModelTask(id="resume", name="Resume")

    manager._download_hf_snapshot(
        task,
        repo_id="example/repo",
        local_dir=str(local_dir),
        allow_patterns=["**/*.safetensors"],
        ignore_patterns=[],
    )

    assert enabled_during_call == [False]
    assert partial.read_bytes() == b"resume-me"
    assert model_manager_module.hf_hub_constants.HF_HUB_ENABLE_HF_TRANSFER is True
    assert model_manager_module.hf_hub_constants.HF_HUB_DISABLE_XET is False
    assert "Resume compatibility fallback" in task.last_log


def test_model_tasks_dedupe_and_persist_interrupted_restart(tmp_path) -> None:
    task_path = tmp_path / "data" / "tasks" / "model_tasks.json"
    manager = ModelTaskManager(task_path)
    started = threading.Event()
    release = threading.Event()

    def blocked(_task):
        started.set()
        release.wait(timeout=5)

    first = manager.start("Install model", blocked, model_id="same-model")
    assert started.wait(timeout=2)
    duplicate = manager.start("Install model again", blocked, model_id="same-model")

    assert duplicate.id == first.id
    persisted = json.loads(task_path.read_text(encoding="utf-8"))
    persisted_task = next(row for row in persisted["tasks"] if row["id"] == first.id)
    assert persisted_task["status"] == "running"
    assert persisted_task["model_id"] == "same-model"

    restarted = ModelTaskManager(task_path)
    recovered = next(task for task in restarted.list() if task.id == first.id)
    assert recovered.status == "interrupted"
    assert recovered.stage == "interrupted"
    assert "Retry to resume" in recovered.last_log

    release.set()
    deadline = time.time() + 2
    while first.status != "done" and time.time() < deadline:
        time.sleep(0.01)
    assert first.status == "done"


def test_model_task_cancel_is_keyed_and_finishes_cancelled(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    started = threading.Event()

    def cancellable(task: ModelTask) -> None:
        started.set()
        while not manager.tasks.is_cancel_requested(task):
            time.sleep(0.01)
        raise ModelTaskCancelled("cancelled in test")

    task = manager.tasks.start(
        "Install FLUX.1 Schnell",
        cancellable,
        model_id="hf_flux1_schnell_internal",
    )
    assert started.wait(timeout=2)

    cancelled = manager.cancel_task(task.id)
    assert cancelled.id == task.id
    assert cancelled.model_id == "hf_flux1_schnell_internal"
    assert cancelled.cancel_requested is True
    assert cancelled.stage == "cancelling"

    deadline = time.time() + 2
    while task.status != "cancelled" and time.time() < deadline:
        time.sleep(0.01)
    assert task.status == "cancelled"
    assert task.stage == "cancelled"


def test_hf_snapshot_checks_cancellation_before_start(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    task = ModelTask(
        id="cancelled-flux",
        name="Install FLUX.1 Schnell",
        model_id="hf_flux1_schnell_internal",
        cancel_requested=True,
    )
    called = False

    def fake_snapshot_download(**_kwargs):
        nonlocal called
        called = True

    monkeypatch.setattr(model_manager_module, "snapshot_download", fake_snapshot_download)

    with pytest.raises(ModelTaskCancelled):
        manager._download_hf_snapshot(
            task,
            repo_id="black-forest-labs/FLUX.1-schnell",
            local_dir=str(tmp_path / "flux"),
            allow_patterns=["**/*.safetensors"],
            ignore_patterns=[],
        )
    assert called is False


def test_model_task_preserves_user_facing_error_details(tmp_path) -> None:
    tasks = ModelTaskManager(tmp_path / "tasks.json")

    def fail_with_actionable_error(_task: ModelTask) -> None:
        raise model_manager_module.UserFacingError(
            "Hugging Face denied access",
            hint="Accept the gated model conditions in your Hugging Face account, then retry.",
            code="HF_AUTH_REQUIRED",
        )

    task = tasks.start(
        "Install FLUX.1 Schnell",
        fail_with_actionable_error,
        model_id="hf_flux1_schnell_internal",
    )
    deadline = time.time() + 2
    while task.status not in {"failed", "done"} and time.time() < deadline:
        time.sleep(0.01)

    assert task.status == "failed"
    assert task.error == "Hugging Face denied access"
    assert task.error_hint == "Accept the gated model conditions in your Hugging Face account, then retry."
    assert task.error_code == "HF_AUTH_REQUIRED"


def test_atomic_json_write_retries_windows_sharing_violation_and_recovers_tmp(
    tmp_path,
    monkeypatch,
) -> None:
    path = tmp_path / "data" / "tasks" / "model_tasks.json"
    real_replace = model_manager_module.os.replace
    calls = 0

    def flaky_replace(source, target):
        nonlocal calls
        calls += 1
        if calls < 3:
            error = PermissionError(13, "sharing violation")
            error.winerror = 32
            raise error
        return real_replace(source, target)

    monkeypatch.setattr(model_manager_module.os, "replace", flaky_replace)
    model_manager_module._write_json(path, {"tasks": [{"id": "durable"}]})

    assert calls == 3
    assert json.loads(path.read_text(encoding="utf-8"))["tasks"][0]["id"] == "durable"
    assert list(path.parent.glob(f".{path.name}.*.tmp")) == []

    path.unlink()
    legacy_tmp = path.with_suffix(path.suffix + ".tmp")
    legacy_tmp.write_text('{"tasks":[{"id":"recovered"}]}', encoding="utf-8")
    assert model_manager_module._read_json(path, {})["tasks"][0]["id"] == "recovered"
    assert path.exists()


def test_incomplete_marker_prevents_false_installed_snapshot(tmp_path, monkeypatch) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry = _diffusers_entry()
    snapshot = tmp_path / "models" / "internal" / "diffusers" / entry["id"]
    _write_valid_unet_snapshot(snapshot)
    (snapshot / "unet" / "weights.safetensors.incomplete").write_bytes(b"partial")

    assert manager._diffusers_snapshot_complete(snapshot) is False
    assert manager._internal_asset_installed(entry, snapshot) is False


def test_excluded_old_hub_cache_partial_does_not_block_complete_snapshot(
    tmp_path,
    monkeypatch,
) -> None:
    manager = _manager(tmp_path, monkeypatch)
    entry = _diffusers_entry()
    snapshot = tmp_path / "models" / "internal" / "diffusers" / entry["id"]
    _write_valid_unet_snapshot(snapshot)
    hub_cache = snapshot / ".cache" / "huggingface" / "download"
    hub_cache.mkdir(parents=True)
    # Local-dir Hub partials are content-hash names. This represents an old
    # whole-repo checkpoint that the bounded inference profile no longer selects.
    (hub_cache / "abc123.old-etag.incomplete").write_bytes(b"old checkpoint partial")

    assert manager._internal_asset_installed(entry, snapshot) is True
