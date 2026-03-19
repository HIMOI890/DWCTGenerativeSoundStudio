# HF Video Models (Diffusers) — EDMG helper

EDMG ships a small Hugging Face *catalog* of strong video-generation models in Diffusers format and provides
a download + wiring helper for ComfyUI/A1111.

## Catalog file

- `src/enhanced_deforum_music_generator/presets/hf_video_model_catalog.json`

## Download + wire from the Gradio UI

In the main UI, open:

- **HF Video Models (Download + Wire)**

Provide:
- a model from the dropdown
- optional HF token (or set `HF_TOKEN`)
- `models_root` (central store)
- optional ComfyUI root and/or A1111 root

EDMG downloads into:

- `<models_root>/hf_video/<model_name>/`

and wires into:

- ComfyUI: `<comfyui_root>/models/video/<model_name>/`
- A1111: `<a1111_root>/models/video/<model_name>/`

## Generate a clip with Diffusers (CLI)

Use the unified script:

```bash
python scripts/run_video_diffusers.py --model-id Wan-AI/Wan2.2-TI2V-5B-Diffusers \
  --prompt "Two cats boxing on a stage" --output outputs/wan.mp4 --device cuda --dtype bfloat16
```

For image-to-video models, add `--image /path/to/image.png`.

## Notes

- Video generation needs a modern GPU and a recent `diffusers` release.
- For Wan pipelines, the upstream docs suggest torch >= 2.4.
