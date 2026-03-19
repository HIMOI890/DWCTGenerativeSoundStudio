# ComfyUI integration: workflows (video)

This project can generate video via **Diffusers** directly (see `scripts/run_video_diffusers.py`),
and it can also **co-exist** with ComfyUI by wiring the same model files into `ComfyUI/models`.

Because ComfyUI workflow distribution varies by model family (templates, images-with-metadata, JSONs),
EDMG provides a reproducible fetch+patch tool:

- `scripts/fetch_comfyui_workflows.py`
- `comfyui_workflows/manifest.json`

## Fetch workflows

```bash
python scripts/fetch_comfyui_workflows.py --out comfyui_workflows/downloaded
```

## Patch workflows to your local model filenames

If you already have models installed under `ComfyUI/models/**`, patch the downloaded workflows to
match *your* filenames:

```bash
python scripts/fetch_comfyui_workflows.py \
  --out comfyui_workflows/downloaded \
  --patch \
  --comfyui-root /path/to/ComfyUI
```

## Open in ComfyUI

ComfyUI UI:
- `Workflows -> Open` (select the JSON)
- or drag and drop the JSON onto the canvas.

## Notes per family

- **Wan2.2**: ComfyUI docs provide official JSON workflow downloads and manual model placement guidance.
- **Hunyuan Video**: official docs primarily ship workflows as images-with-metadata; a reproducible JSON is included via a community workflow source.
- **LTX**: ComfyUI core includes templates; Lightricks also ships example workflows in their custom-node repo.
- **CogVideoX**: workflows come from `ComfyUI-CogVideoXWrapper` examples; install the custom nodes first.

See: `comfyui_workflows/manifest.json` for sources and expected model filenames.


## Wiring required models into ComfyUI automatically

If a workflow references model filenames you already have in a central store, you can link/copy them into the right `ComfyUI/models/*` folders:

```bash
python scripts/wire_comfyui_models.py --comfyui-root /path/to/ComfyUI --models-root external/models --workflow path/to/workflow.json
```

Use `--mode copy` if symlinks are not allowed on your system.
