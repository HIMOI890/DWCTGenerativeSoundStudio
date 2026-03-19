# EDMG Studio – quick test (no CLI typing)

1. Unzip this folder somewhere short (e.g. `C:\EDMG\`).
2. Double-click **LAUNCH_EDMG_STUDIO_GUI.bat**
3. In the Launcher:
   - Click **Install/Update Backend**
   - Click **Install/Update Studio UI**
   - Click **Start Backend**
   - Click **Run Health Test**
   - Click **Start Studio (Electron dev)**

## Prereqs (installed once)
- **Python 3.10+** (Windows: install from python.org)
- **Node.js LTS** (for the Electron UI)
- Optional but recommended:
  - **Ollama** running at `http://127.0.0.1:11434`
  - **ComfyUI** running at `http://127.0.0.1:8188`
  - **FFmpeg** on PATH (for MP4 assembly)

If Ollama/ComfyUI/FFmpeg aren’t installed yet, the app will still boot and show clear “Fix:” instructions in the Setup / logs.
