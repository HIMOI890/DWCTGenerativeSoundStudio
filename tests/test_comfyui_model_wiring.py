from __future__ import annotations

import json
from pathlib import Path

from enhanced_deforum_music_generator.integrations.comfyui_model_wiring import wire_from_workflow


def test_wire_from_workflow_copies_into_expected_folders(tmp_path: Path) -> None:
    comfyui_root = tmp_path / "ComfyUI"
    models_dir = comfyui_root / "models"
    models_dir.mkdir(parents=True)

    central = tmp_path / "central_models"
    central.mkdir()

    ckpt = central / "my.ckpt.safetensors"
    vae = central / "my.vae.safetensors"
    lora = central / "my.lora.safetensors"
    for p in (ckpt, vae, lora):
        p.write_bytes(b"dummy")

    workflow = {
        "nodes": {
            "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt.name}},
            "2": {"class_type": "VAELoader", "inputs": {"vae_name": vae.name}},
            "3": {"class_type": "LoraLoader", "inputs": {"lora_name": lora.name}},
        }
    }
    wf_path = tmp_path / "wf.json"
    wf_path.write_text(json.dumps(workflow), encoding="utf-8")

    ok = wire_from_workflow(
        workflow_path=wf_path,
        comfyui_root=comfyui_root,
        models_root=central,
        mode="copy",
        dry_run=False,
    )
    assert ok

    assert (comfyui_root / "models" / "checkpoints" / ckpt.name).exists()
    assert (comfyui_root / "models" / "vae" / vae.name).exists()
    assert (comfyui_root / "models" / "loras" / lora.name).exists()
