# GUI Installer (Automatic) — EDMG

This repo includes a **CPU-first GUI installer** that can:

- Create `.venv`
- Install Python dependencies (Full or Minimal)
- Clone and configure external backends (optional):
  - Automatic1111 + Deforum extension (CPU mode)
  - ComfyUI (CPU mode)
- Start/stop services and run verification checks

## Run

```bash
python installer_gui.py
```

## What it can install automatically

- EDMG Python environment + deps ✅
- Clone/configure A1111 + Deforum ✅ (requires `git` and internet)
- Clone/configure ComfyUI ✅ (requires `git` and internet)

## Custom install locations

The installer supports moving the heavy parts off `C:\`:

- `EDMG venv folder` for the standalone Python environment
- `External folder`, `A1111 folder`, and `ComfyUI folder` for external repos
- `Shared cache folder` for pip, Hugging Face, Torch, Whisper, NLTK, and temp files

Point those fields at `D:\...` before clicking **Install Selected**.

## What it cannot reliably install

- GPU drivers / CUDA / ROCm / Apple Metal
- Model weights (SD checkpoints, video weights) — you provide/download them separately

## Build a standalone app (optional)

You can package the GUI with PyInstaller:

```bash
./.venv/bin/python -m pip install pyinstaller
./.venv/bin/pyinstaller --onefile --windowed installer_gui.py
```

On Windows:

```powershell
.\.venv\Scripts\python.exe -m pip install pyinstaller
.\.venv\Scripts\pyinstaller.exe --onefile --windowed installer_gui.py
```

The built executable will appear under `dist/`.
