# EDMG Studio (v1.1.0)

A desktop-style "studio" application:

- **Electron** shell + **React** UI
- Local **FastAPI** backend for projects, assets, planning, rendering, and outputs
- Integrates with:
  - **ComfyUI** for image generation (local or remote)
  - **AI Director** service (optional) for planning/transcription/features
  - **EDMG Core** (enhanced-deforum-music-generator) for Deforum template/export (optional but recommended)
  - **AWS** + **Lightning.ai** bundle scaffolding

## Quick start

### Prereqs
- Node.js LTS
- Python 3.10+
- FFmpeg on PATH for dev checkouts, or the bundled Studio FFmpeg for packaged builds (used for MP4 assembly)
- ComfyUI running (default `http://127.0.0.1:8188`)
- AI Director runs **in-process** by default (talks to Ollama directly); no separate AI server needed.
- EDMG Core is included by the default Studio backend bundle/install target

### Backend
```bash
cd python_backend
python -m venv venv
venv\Scripts\activate
pip install -U pip
pip install -e ".[studio_bundle]"
edmg-studio-backend serve --host 127.0.0.1 --port 7863
```

### UI
```bash
npm install
npm run dev
```

## Setup Wizard (no command line)

When you install the packaged app, EDMG Studio includes an in-app **Setup Wizard** (Sidebar → **Setup**) that:

- Uses an assisted Windows installer, so you can choose the **app install directory** instead of being forced into the default `C:\` path
- Lets you choose a **Studio Home** folder before large downloads, so project data, Electron data, ComfyUI Portable, and caches can live on `D:\...`
- Checks **Ollama** availability (local AI)
- Lets you **pull the default model** via a button
- Checks **ComfyUI** availability and can **download + extract ComfyUI Portable** on Windows
- Verifies **FFmpeg** for MP4 assembly, preferring the Studio-bundled binary when present

This keeps the runtime UX like a DAW/game installer: click buttons, no terminal required.

Install/storage split:
- **Install directory**: where the packaged app itself is installed
- **Studio Home**: where projects, caches, Electron session data, portable tools, and large runtime payloads live

## Ports
- Studio backend: **7863**
- AI director service: **7862**
- ComfyUI: **8188**

## Environment variables (Backend)
- `EDMG_STUDIO_HOME` (optional; preferred root for Studio storage)
- `EDMG_STUDIO_DATA_DIR` (default: `./data`)
- `EDMG_AI_MODE` (default: `local`)
- `EDMG_AI_PROVIDER` (default: `ollama`)
- `EDMG_AI_OLLAMA_URL` (default: `http://127.0.0.1:11434`)
- `EDMG_AI_OLLAMA_MODEL` (default: `qwen2.5:3b-instruct`)
- `EDMG_COMFYUI_URL` (default: `http://127.0.0.1:8188`)
- `EDMG_COMFYUI_CHECKPOINT` (default: `sdxl_base_1.0.safetensors`)
- `EDMG_FFMPEG_PATH` (optional override; packaged Studio prefers its bundled FFmpeg, dev falls back to `ffmpeg` on PATH)

If `EDMG_STUDIO_HOME` is set, Studio uses it as the root for:
- backend project data (`<studio-home>/data`)
- Electron user/session data (`<studio-home>/electron`)
- caches and temporary files (`<studio-home>/cache`)

EDMG Core integration:
- If EDMG Core is installed in the same environment, Studio can:
  - Verify the core install
  - Export Deforum settings JSON per variant
  - Fetch the Deforum template

## Workflow
1. Create a project
2. Upload audio
3. Analyze + transcribe (optional via AI service)
4. Generate plan variants
5. Render scene stills via ComfyUI
6. Assemble MP4 (FFmpeg slideshow + audio)
7. Export Deforum settings (optional)


## Motion video rendering (ComfyUI)

Studio supports **motion clips per scene** via two free, local-friendly ComfyUI paths:

- **AnimateDiff (recommended for longer sequences)**  
  Requires `ComfyUI-AnimateDiff-Evolved` nodes. AnimateDiff supports *unlimited* animation length when you pass Context Options (sliding context windows). 

- **Stable Video Diffusion (SVD) img2vid (best for short clips / transitions)**  
  Requires `ComfyUI-Stable-Video-Diffusion` nodes (e.g. `SVDSimpleImg2Vid`). 

### Verify ComfyUI capabilities

From the Studio UI (Workspace), you’ll see availability checks (✓/×).  
Backend endpoint: `GET /v1/comfyui/capabilities` (uses ComfyUI’s `/object_info`). 

### Rendering motion

- Workspace → Render → Mode → **Motion (AnimateDiff)** or **Motion (SVD)**  
- Click **Enqueue motion scenes**, then use **Tick worker** repeatedly (or run a simple loop).

Outputs:
- Frames: `data/<project>/outputs/frames/...`
- Per-scene clips: `data/<project>/outputs/clips/...`
- Final concatenated video: `data/<project>/outputs/videos/variant_XX.mp4`
