# ComfyUI Workflows (EDMG)

This folder contains **workflow manifests + tools** to fetch and patch ComfyUI workflow JSONs for
top video model families (Wan2.2, HunyuanVideo, LTX, CogVideoX).

Why this exists:
- Official ComfyUI tutorials often ship workflows as **Templates** or images-with-metadata.
- We keep a reproducible, scriptable way to **download** the workflow JSON and **patch model file names**
  based on what you have installed/wired under your `ComfyUI/models/` tree.

## Quickstart

1) Fetch workflows:
```bash
python scripts/fetch_comfyui_workflows.py --out comfyui_workflows/downloaded
```

2) Patch workflows based on your local ComfyUI install:
```bash
python scripts/fetch_comfyui_workflows.py \
  --out comfyui_workflows/downloaded \
  --patch \
  --comfyui-root /path/to/ComfyUI
```

3) Open the patched workflow in ComfyUI:
- ComfyUI UI: `Workflows -> Open`
- Or drag-and-drop the JSON file into the canvas.

## Notes

- Wan2.2 official template JSONs come from ComfyUI docs and ComfyUI_examples.
- Hunyuan official docs provide embedded workflow images; JSON here uses a reproducible community workflow + model naming conventions.
- LTX official repo ships example workflows under `example_workflows/`.
- CogVideoX workflows come from the ComfyUI-CogVideoXWrapper example workflows.
