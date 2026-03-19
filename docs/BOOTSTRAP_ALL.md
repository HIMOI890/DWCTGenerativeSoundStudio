# bootstrap_all.py

This repo includes a best-effort orchestration script `bootstrap_all.py` that can:

- Install **EDMG** (this repo) via `setup.py --mode ...` and `deploy.py --setup-project`
- Clone **Automatic1111** and enable `--api`
- Clone **Deforum** extension into the A1111 extensions folder
- Clone **ComfyUI**
- Create a **JUCE** example skeleton (CMake FetchContent)

It cannot reliably install:
- GPU drivers (CUDA/ROCm/Metal)
- Large model weights (license + size)
- GPU-specific torch wheels (varies by OS/GPU)

## Examples

```bash
python bootstrap_all.py install --all
python bootstrap_all.py run edmg-api --host 127.0.0.1 --port 8000
python bootstrap_all.py verify --edmg http://127.0.0.1:8000 --a1111 http://127.0.0.1:7860 --comfyui http://127.0.0.1:8188
```
