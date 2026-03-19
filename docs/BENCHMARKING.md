# Video benchmarking

EDMG includes a multi-model benchmarking harness:

- `scripts/video_model_bench.py` (spawns one process per model)

It runs the same prompt across multiple Diffusers pipelines and writes:
- per-model MP4 outputs
- `bench_report.json` with timings and errors
- optional `bench_grid.png` (first-frame montage)

## Example

```bash
python scripts/video_model_bench.py \
  --prompt "A macro shot of raindrops on neon glass, cinematic lighting" \
  --bench-name smoke \
  --quick
```

## Notes

- `bench_grid.png` requires `imageio` + ffmpeg support (`pip install imageio imageio-ffmpeg`).
- Large models (Wan 14B, LTX-2 19B) can require very large VRAM; use `--cpu-offload` to trade speed for memory.
