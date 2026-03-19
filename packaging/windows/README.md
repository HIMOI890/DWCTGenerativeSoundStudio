# Windows Release Build (Installer-grade)

This folder contains a **Windows-first** packaging pipeline that produces a DAW/game-like installer.

## Prereqs

- Windows 10/11 x64
- Python 3.10+ (or 3.11) on PATH
- Node.js 18+ on PATH
- Git (optional, for fetching ComfyUI)

Recommended (for AI):

- **Ollama** installed and running.

## One-command build

Open PowerShell in repo root and run:

```powershell
./packaging/windows/build_all.ps1
```

Outputs:

- `studio/edmg-studio/release/` (electron-builder output)

## What gets bundled

- Electron UI
- Python backend compiled into `edmg-studio-backend.exe`
- A place to drop runtime deps:
  - `studio/edmg-studio/electron-resources/bin/ffmpeg.exe`
  - `studio/edmg-studio/electron-resources/backend/edmg-studio-backend.exe`

## Runtime defaults

- AI defaults to **local Ollama** (no separate AI server required)
  - `EDMG_AI_MODE=local`
  - `EDMG_AI_PROVIDER=ollama`

If you prefer a remote AI service:

```powershell
$env:EDMG_AI_MODE = "http"
$env:EDMG_AI_BASE_URL = "http://127.0.0.1:7862"
```


The build script auto-detects both backend layouts used in this repo:

- `studio/edmg-studio/python_backend/edmg_studio_backend`
- `studio/edmg-studio/python_backend/src/edmg_studio_backend`
