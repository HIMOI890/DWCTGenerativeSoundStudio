# Automatic installation: what is automated vs what is external

This repository can automatically set up its own Python environment and dependencies.

## What is automatic (repo-local)
- Creates a Python `venv` (virtual environment)
- Installs Python dependencies for the selected mode (`minimal`, `standard`, `full`, `dev`)
- Creates config/templates and startup scripts
- Runs basic verification checks
- Can redirect the EDMG venv and shared installer caches to another drive

Use:
```bash
python setup.py --mode full
```

Or (Linux/macOS):
```bash
./install.sh
```

Or (Windows PowerShell):
```powershell
.\install.ps1

# Example with custom locations on D:
.\install.ps1 -Mode full -Venv D:\EDMG\venv -CacheRoot D:\EDMG\cache
```

## What is not fully automatic (external systems)
These depend on your OS, GPU driver stack, and/or large downloads:

- CUDA/ROCm/Metal GPU drivers
- Automatic1111 + Deforum extension installation
- ComfyUI installation
- Large model weights (checkpoints, LoRAs, video models)

The repo *does* provide helpers to make these easier once installed:
- `scripts/fetch_comfyui_workflows.py` to download workflow JSONs
- `scripts/wire_comfyui_models.py` to link/copy models into the correct `ComfyUI/models/*` folders
- `scripts/run_video_diffusers.py` to run Hugging Face video models via Diffusers

## Conda / mamba
Conda itself is not installed by this repo. If you already have conda/mamba, use the included `environment*.yml` files:
```bash
conda env create -f environment-full-cuda.yml
conda activate enhanced-deforum-music-generator
```
